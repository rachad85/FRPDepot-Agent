from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

HERE = Path(__file__).resolve().parents[1]
FIXTURE = HERE / "tests" / "fixtures" / "verified"
SOURCE_DATA = HERE / "plugin" / "frpdepot-derakane-chemical-search" / "data"
sys.path.insert(0, str(HERE))

import build_plugin  # noqa: E402
from import_contract import ImportContractError, RESIN_IDS, verify_import  # noqa: E402


class FixtureCopy:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="derakane-import-test-")
        self.path = Path(self.temp.name)
        shutil.copyfile(FIXTURE / "derakane-dataset.json", self.path / "derakane-dataset.json")
        shutil.copyfile(FIXTURE / "import-manifest.json", self.path / "import-manifest.json")

    def close(self):
        self.temp.cleanup()

    def manifest(self):
        return json.loads((self.path / "import-manifest.json").read_text(encoding="utf-8"))

    def dataset(self):
        return json.loads((self.path / "derakane-dataset.json").read_text(encoding="utf-8"))

    def write_dataset_and_repin(self, dataset, manifest=None):
        payload = (json.dumps(dataset, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        (self.path / "derakane-dataset.json").write_bytes(payload)
        manifest = copy.deepcopy(manifest or self.manifest())
        manifest["dataset"]["bytes"] = len(payload)
        manifest["dataset"]["sha256"] = hashlib.sha256(payload).hexdigest()
        (self.path / "import-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def write_manifest(self, manifest):
        (self.path / "import-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


class ImportContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureCopy()

    def tearDown(self):
        self.fixture.close()

    def assertBlocked(self, pattern):
        with self.assertRaisesRegex(ImportContractError, pattern):
            verify_import(self.fixture.path)

    def test_verified_fixture_satisfies_closed_cross_file_contract(self):
        verified = verify_import(self.fixture.path)
        self.assertEqual(verified.sha256, verified.manifest["dataset"]["sha256"])
        self.assertEqual(verified.byte_count, verified.manifest["dataset"]["bytes"])
        self.assertEqual(tuple(verified.manifest["summary"]["resin_columns"]), RESIN_IDS)
        self.assertEqual(verified.manifest["contract_version"], 2)
        self.assertEqual(len(verified.dataset["footnotes"]), 25)
        self.assertEqual(verified.dataset["source"], verified.manifest["source"])
        self.assertEqual(verified.dataset["rebuild_provenance"], verified.manifest["rebuild_provenance"])

    def test_fixture_also_satisfies_both_published_json_schemas(self):
        dataset_schema = json.loads((HERE / "contracts" / "dataset.schema.json").read_text(encoding="utf-8"))
        manifest_schema = json.loads((HERE / "contracts" / "import-manifest.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(dataset_schema)
        Draft202012Validator.check_schema(manifest_schema)
        registry = Registry().with_resource(
            dataset_schema["$id"], Resource.from_contents(dataset_schema)
        )
        Draft202012Validator(dataset_schema, registry=registry).validate(self.fixture.dataset())
        Draft202012Validator(manifest_schema, registry=registry).validate(self.fixture.manifest())

    def test_missing_permission_flag_does_not_matter(self):
        manifest = self.fixture.manifest()
        self.assertNotIn("permission_documented", manifest)
        verify_import(self.fixture.path)

    def test_false_and_true_permission_metadata_do_not_matter(self):
        for value in (False, True):
            with self.subTest(value=value):
                manifest = self.fixture.manifest()
                manifest["permission_documented"] = value
                self.fixture.write_manifest(manifest)
                verify_import(self.fixture.path)

    def test_unknown_permission_gate_is_rejected_by_closed_contract(self):
        manifest = self.fixture.manifest()
        manifest["permission_required"] = True
        self.fixture.write_manifest(manifest)
        self.assertBlocked("unknown field.*permission_required")

    def test_only_the_rebuild_pipeline_contract_can_produce_an_import(self):
        manifest = self.fixture.manifest()
        manifest["producer"]["pipeline"] = "hand-authored"
        self.fixture.write_manifest(manifest)
        self.assertBlocked("supported rebuild pipeline")

    def test_not_verified_manifest_is_release_blocking(self):
        manifest = self.fixture.manifest()
        manifest["verification"]["status"] = "NOT_VERIFIED"
        manifest["verification"]["verified_utc"] = None
        manifest["verification"]["verifier"] = None
        self.fixture.write_manifest(manifest)
        self.assertBlocked("NOT_VERIFIED")

    def test_unresolved_manifest_is_release_blocking(self):
        manifest = self.fixture.manifest()
        manifest["verification"]["unresolved_count"] = 1
        self.fixture.write_manifest(manifest)
        self.assertBlocked("unresolved")

    def test_dataset_hash_mismatch_is_release_blocking(self):
        path = self.fixture.path / "derakane-dataset.json"
        path.write_bytes(path.read_bytes() + b" ")
        self.assertBlocked("byte count mismatch")

    def test_dataset_sha_mismatch_is_release_blocking_even_when_length_matches(self):
        manifest = self.fixture.manifest()
        manifest["dataset"]["sha256"] = "0" * 64
        self.fixture.write_manifest(manifest)
        self.assertBlocked("SHA-256 mismatch")

    def test_unresolved_cell_state_is_never_treated_as_blank(self):
        dataset = self.fixture.dataset()
        dataset["rows"][0]["cells"][0]["ratings"][0]["state"] = "unresolved"
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("unsupported or unresolved")

    def test_not_verified_row_is_release_blocking(self):
        dataset = self.fixture.dataset()
        dataset["rows"][0]["qa_status"] = "NOT_VERIFIED"
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("qa_status must be VERIFIED")

    def test_nine_resin_cells_and_exact_source_column_order_are_required(self):
        dataset = self.fixture.dataset()
        dataset["rows"][0]["cells"][0], dataset["rows"][0]["cells"][1] = (
            dataset["rows"][0]["cells"][1], dataset["rows"][0]["cells"][0]
        )
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("breaks source column order")

    def test_source_row_order_must_be_strictly_increasing_not_lexical(self):
        dataset = self.fixture.dataset()
        dataset["rows"][2]["source_sequence"] = 9
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("strictly increasing")

    def test_every_footnote_reference_must_resolve(self):
        dataset = self.fixture.dataset()
        dataset["rows"][0]["row_footnote_ids_raw"] = [28]
        dataset["rows"][0]["row_footnote_ids"] = [28]
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("undefined footnote 28")

    def test_duplicate_row_refs_are_preserved_raw_and_deduplicated_for_display(self):
        verified = verify_import(self.fixture.path)
        row = next(item for item in verified.dataset["rows"] if item["row_id"] == "fixture-p14-s10")
        self.assertEqual(row["row_footnote_ids_raw"], [2, 2, 15, 16])
        self.assertEqual(row["row_footnote_ids"], [2, 15, 16])
        self.assertEqual(row["chemical_source"]["footnote_refs_raw"], [2, 2])
        self.assertEqual(row["chemical_source"]["footnote_refs"], [2])

    def test_duplicate_cell_and_rating_refs_are_preserved_raw_and_deduplicated_for_display(self):
        verified = verify_import(self.fixture.path)
        cell = next(
            cell for row in verified.dataset["rows"] for cell in row["cells"]
            if cell["footnote_ids_raw"] == [3, 4, 3]
        )
        rating = cell["ratings"][0]
        self.assertEqual(cell["footnote_ids"], [3, 4])
        self.assertEqual(rating["footnote_refs_raw"], [3, 4, 3])
        self.assertEqual(rating["footnote_refs"], [3, 4])

    def test_display_refs_must_match_first_occurrence_dedup_of_raw_refs(self):
        dataset = self.fixture.dataset()
        row = next(item for item in dataset["rows"] if item["row_id"] == "fixture-p14-s10")
        row["row_footnote_ids"] = [2, 1]
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("first-occurrence deduplicated projection")

    def test_raw_ref_sequence_cannot_be_changed_without_matching_display_projection(self):
        dataset = self.fixture.dataset()
        cell = next(
            cell for row in dataset["rows"] for cell in row["cells"]
            if cell["footnote_ids_raw"] == [3, 4, 3]
        )
        cell["footnote_ids_raw"] = [3, 5, 4, 3]
        cell["ratings"][0]["footnote_refs_raw"] = [3, 5, 4, 3]
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("first-occurrence deduplicated projection")

    def test_raw_refs_cannot_be_tampered_even_when_display_projection_is_recomputed(self):
        dataset = self.fixture.dataset()
        cell = next(
            cell for row in dataset["rows"] for cell in row["cells"]
            if cell["footnote_ids_raw"] == [3, 4, 3]
        )
        cell["footnote_ids_raw"] = [3, 4]
        cell["ratings"][0]["footnote_refs_raw"] = [3, 4]
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("exactly match references in source_raw")

    def test_row_raw_refs_must_exactly_match_all_source_component_refs(self):
        dataset = self.fixture.dataset()
        row = next(item for item in dataset["rows"] if item["row_id"] == "fixture-p14-s10")
        row["row_footnote_ids_raw"] = [2, 2]
        row["row_footnote_ids"] = [2]
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("exactly preserve all source component references")

    def test_raw_and_display_ref_fields_are_required_by_closed_contract(self):
        for field in ("row_footnote_ids_raw", "row_footnote_ids"):
            with self.subTest(field=field):
                fixture = FixtureCopy()
                try:
                    dataset = fixture.dataset()
                    del dataset["rows"][0][field]
                    fixture.write_dataset_and_repin(dataset)
                    with self.assertRaisesRegex(ImportContractError, f"missing required field.*{field}"):
                        verify_import(fixture.path)
                finally:
                    fixture.close()

    def test_source_edition_and_original_hash_are_cross_file_pinned(self):
        manifest = self.fixture.manifest()
        manifest["source"]["edition"] = "different edition"
        self.fixture.write_manifest(manifest)
        self.assertBlocked("source/rebuild provenance does not exactly match")

    def test_counts_must_match_content(self):
        manifest = self.fixture.manifest()
        manifest["summary"]["row_count"] += 1
        self.fixture.write_manifest(manifest)
        self.assertBlocked("summary row count")

    def test_cas_numbers_must_pass_checksum(self):
        dataset = self.fixture.dataset()
        dataset["cas_catalog"][0]["normalized_cas"] = "64-19-8"
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("checksum-valid.*public search")

    def test_rebuild_provenance_is_required_and_tamper_evident(self):
        manifest = self.fixture.manifest()
        del manifest["rebuild_provenance"]
        self.fixture.write_manifest(manifest)
        self.assertBlocked("missing required field.*rebuild_provenance")

    def test_rebuild_provenance_must_match_dataset_exactly(self):
        manifest = self.fixture.manifest()
        manifest["rebuild_provenance"]["output_manifest_sha256"] = "0" * 64
        self.fixture.write_manifest(manifest)
        self.assertBlocked("source/rebuild provenance does not exactly match")

    def test_every_required_rebuild_artifact_pin_is_required(self):
        manifest = self.fixture.manifest()
        manifest["rebuild_provenance"]["artifacts"] = manifest["rebuild_provenance"]["artifacts"][1:]
        self.fixture.write_manifest(manifest)
        self.assertBlocked("does not contain every required rebuild pin")

    def test_excluded_raw_cas_cannot_leak_into_public_search_metadata(self):
        dataset = self.fixture.dataset()
        entity = next(item for item in dataset["search_entities"] if "fixture-cas-excluded" in item["cas_entry_ids"])
        entity["aliases"].append({"display": "1330-96-4", "search_key": "1330 96 4", "basis": "tamper"})
        row = next(item for item in dataset["rows"] if item["chemical_id"] == entity["entity_id"])
        row["aliases"] = entity["aliases"]
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("leaks an excluded raw CAS")

    def test_raw_pdf_cas_internal_provenance_cannot_be_lost(self):
        dataset = self.fixture.dataset()
        dataset["cas_catalog"][0]["source"]["raw_lines"] = ["Synthetic line with raw token removed"]
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("raw_pdf_cas is missing from retained source lines")

    def test_source_special_cannot_be_flattened_to_an_ordinary_value(self):
        dataset = self.fixture.dataset()
        target = next(
            rating for row in dataset["rows"] for cell in row["cells"] for rating in cell["ratings"]
            if rating.get("source_special_form") == "printed_single_temperature_without_unit"
        )
        target.update({
            "state": "value", "resolution_status": "RESOLVED", "syntax": "temperature_pair",
            "temperature_c": 75, "temperature_f": 167, "temperature_pair_order": "C/F",
            "alternatives": [{
                "kind": "temperature_pair", "source_raw": "75", "temperature_pair_order": "C/F",
                "components": [
                    {"unit": "C", "value": 75, "limited_service": False, "limited_service_prefix_raw": None},
                    {"unit": "F", "value": 167, "limited_service": False, "limited_service_prefix_raw": None},
                ],
            }],
        })
        del target["source_special_form"]
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("source-special form")

    def test_pdf_proven_footnotes_cannot_be_removed_or_replaced(self):
        dataset = self.fixture.dataset()
        dataset["footnotes"] = dataset["footnotes"][:-1]
        self.fixture.write_dataset_and_repin(dataset)
        self.assertBlocked("exactly PDF-proven definitions 1 through 25")


class BuildGateTests(unittest.TestCase):
    def test_production_data_directory_contains_the_verified_import(self):
        verified = verify_import(SOURCE_DATA)
        self.assertEqual(verified.sha256, verified.manifest["dataset"]["sha256"])
        self.assertTrue((SOURCE_DATA / "README.md").is_file())

    def test_verify_build_and_package_are_all_blocked_without_verified_import(self):
        with tempfile.TemporaryDirectory(prefix="derakane-build-gate-") as temp:
            empty_data = Path(temp) / "empty-data"
            empty_data.mkdir()
            output_dir = Path(temp) / "plugin-output"
            zip_output = Path(temp) / "plugin.zip"
            for operation, artifact in (
                (lambda: build_plugin.verification_report(empty_data), None),
                (lambda: build_plugin.build(output_dir, empty_data), output_dir),
                (lambda: build_plugin.package(zip_output, empty_data), zip_output),
            ):
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(ImportContractError, "manifest is missing"):
                        operation()
                    if artifact is not None:
                        self.assertFalse(artifact.exists(), "gate must run before creating an artifact")

    def test_build_and_package_each_refuse_not_verified_unresolved_and_hash_mismatch(self):
        def not_verified(fixture):
            manifest = fixture.manifest()
            manifest["verification"].update({"status": "NOT_VERIFIED", "verified_utc": None, "verifier": None})
            fixture.write_manifest(manifest)

        def unresolved(fixture):
            manifest = fixture.manifest()
            manifest["verification"]["unresolved_count"] = 1
            fixture.write_manifest(manifest)

        def hash_mismatch(fixture):
            manifest = fixture.manifest()
            manifest["dataset"]["sha256"] = "0" * 64
            fixture.write_manifest(manifest)

        for defect_name, mutate, message in (
            ("NOT_VERIFIED", not_verified, "NOT_VERIFIED"),
            ("unresolved", unresolved, "unresolved"),
            ("hash-mismatch", hash_mismatch, "SHA-256 mismatch"),
        ):
            for operation_name in ("build", "package"):
                with self.subTest(defect=defect_name, operation=operation_name):
                    fixture = FixtureCopy()
                    try:
                        mutate(fixture)
                        output = fixture.path / ("output-plugin" if operation_name == "build" else "output.zip")
                        if operation_name == "build":
                            operation = lambda: build_plugin.build(output, fixture.path)
                        else:
                            operation = lambda: build_plugin.package(output, fixture.path)
                        with self.assertRaisesRegex(ImportContractError, message):
                            operation()
                        self.assertFalse(output.exists(), "gate must run before creating any artifact")
                    finally:
                        fixture.close()

    def test_build_consumes_verified_fixture_without_any_permission_flag(self):
        fixture = FixtureCopy()
        try:
            self.assertNotIn("permission_documented", fixture.manifest())
            output = fixture.path / "built-plugin"
            report = build_plugin.build(output, fixture.path)
            self.assertEqual(report["status"], "BUILT")
            self.assertTrue((output / "data" / "import-manifest.json").is_file())
            self.assertTrue((output / "data" / "derakane-dataset.json").is_file())
            self.assertFalse(any(output.rglob("*.zip")))
        finally:
            fixture.close()

    def test_cli_build_refuses_and_creates_no_artifact(self):
        with tempfile.TemporaryDirectory(prefix="derakane-build-cli-") as temp:
            empty_data = Path(temp) / "empty-data"
            empty_data.mkdir()
            output = Path(temp) / "blocked-output"
            result = subprocess.run(
                [
                    sys.executable, str(HERE / "build_plugin.py"), "build",
                    "--data-dir", str(empty_data), "--output-dir", str(output),
                ],
                cwd=HERE,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("BLOCKED:", result.stderr)
            self.assertFalse(output.exists())

    def test_cli_package_refuses_and_creates_no_zip(self):
        with tempfile.TemporaryDirectory(prefix="derakane-package-cli-") as temp:
            empty_data = Path(temp) / "empty-data"
            empty_data.mkdir()
            output = Path(temp) / "blocked.zip"
            result = subprocess.run(
                [
                    sys.executable, str(HERE / "build_plugin.py"), "package",
                    "--data-dir", str(empty_data), "--output", str(output),
                ],
                cwd=HERE,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("BLOCKED:", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
