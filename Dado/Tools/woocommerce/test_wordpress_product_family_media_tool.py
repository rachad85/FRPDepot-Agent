"""Offline safety tests for the fixed FRP product-family media tool."""
from __future__ import annotations

import argparse
import ast
import contextlib
from copy import deepcopy
from datetime import timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wordpress_product_family_media_tool as tool  # noqa: E402

SOURCE_PATH = Path(tool.__file__)
SOURCE_TEXT = SOURCE_PATH.read_text(encoding="utf-8")
SOURCE_TREE = ast.parse(SOURCE_TEXT)


class FakeAdmin:
    def __init__(self, key: str, *, fail_upload_at: int | None = None,
                 fail_public: bool = False, fail_after_submit: bool = False,
                 fail_guard_acquire: bool = False, fail_guard_complete: bool = False):
        self.key = key
        self.fail_upload_at = fail_upload_at
        self.fail_public = fail_public
        self.fail_after_submit = fail_after_submit
        self.fail_guard_acquire = fail_guard_acquire
        self.fail_guard_complete = fail_guard_complete
        self.upload_count = 0
        self.guard_acquire_count = 0
        self.guard_complete_count = 0
        self.guard_snapshot_count = 0
        self.guard_expires_utc = (tool.utc_now() + timedelta(minutes=30)).isoformat()
        self.left = False
        self.id_to_name: dict[int, str] = {}

    def enumerate_library(self):
        return {"rows": [{
                    "id": tool.GUARD_PRIVATE_EXCEPTION["attachment_id"],
                    "filename": Path(tool.GUARD_PRIVATE_EXCEPTION["attached_file"]).name,
                    "stem": "hetron-cr-guide-2007_ineos",
                }], "total": 1, "pages": 1, "complete": True,
                "unidentified": 0}

    def _snapshot(self, mode, active, total, reserved=0, family=None):
        matches = ([{"attachment_id": 9000 + position, "fixed_position": position}
                    for position in range(1, 5)]
                   if mode == "guarded_snapshot" and total == 4 else [])
        value = {
            "schema": tool.GUARD_PROOF_SCHEMA, "plugin_version": tool.GUARD_PLUGIN_VERSION,
            "mode": mode, "family": family or self.key,
            "generated_utc": "2026-08-16T12:00:00+00:00",
            "attachment_total": total + 1, "hashed_total": total, "total_bytes": total * 10,
            "snapshot_sha256": "a" * 64, "complete": True, "failures": [],
            "private_exceptions": [deepcopy(tool.GUARD_PRIVATE_EXCEPTION)],
            "name_conflicts": deepcopy(matches), "hash_conflicts": deepcopy(matches),
            "fixed_matches": deepcopy(matches), "guard_active": active,
        }
        if active:
            value["guard_expires_utc"] = self.guard_expires_utc
            value["reserved_uploads"] = reserved
        return value

    def atomic_snapshot(self, key):
        return self._snapshot("atomic_snapshot", False, 0, family=key)

    def prepare_guard_acquire(self, key):
        if key != self.key:
            raise RuntimeError("wrong modelled family")
        return object()

    def acquire_prepared_guard(self, key, button, on_submit_attempt=None):
        self.guard_acquire_count += 1
        if on_submit_attempt is not None:
            on_submit_attempt()
        if self.fail_guard_acquire:
            raise RuntimeError("modelled guard acquisition failure")
        return self._snapshot("guard_acquired", True, 0, 0)

    def guarded_snapshot(self, key):
        if key != self.key:
            raise RuntimeError("wrong modelled family")
        self.guard_snapshot_count += 1
        if self.upload_count == 0:
            return self._snapshot("guarded_snapshot", True, 0, 0)
        return self._snapshot("guarded_snapshot", True, 4, 4)

    def complete_guard(self, key, attachment_ids, on_submit_attempt=None):
        self.guard_complete_count += 1
        if on_submit_attempt is not None:
            on_submit_attempt()
        if self.fail_guard_complete:
            raise RuntimeError("modelled guard completion failure")
        return {
            "proof": {
                "schema": tool.GUARD_PROOF_SCHEMA, "plugin_version": tool.GUARD_PLUGIN_VERSION,
                "mode": "guard_completed", "family": key,
                "product_id": tool.FAMILY_SPECS[key]["product_id"],
                "attachment_ids": list(attachment_ids), "attachment_total": 5,
                "snapshot_sha256": "a" * 64,
            },
            "post_completion_health": {
                "plugin_version": tool.GUARD_PLUGIN_VERSION,
                "status": "Guard inactive", "families": sorted(tool.FAMILY_KEYS),
            },
        }

    def upload_one(self, expected, known_ids, on_submit_attempt=None):
        self.upload_count += 1
        if on_submit_attempt is not None:
            on_submit_attempt()
        if self.fail_upload_at == self.upload_count:
            raise RuntimeError("modelled upload failure")
        attachment_id = 9000 + self.upload_count
        self.id_to_name[attachment_id] = expected["filename"]
        return attachment_id

    def read_attachment(self, attachment_id, expected_basename=None, allowed_extensions=None):
        name = expected_basename or self.id_to_name[int(attachment_id)]
        return {"id": int(attachment_id), "filename": name,
                "source_url": f"https://frpdepots.com/wp-content/uploads/2026/08/{name}"}

    def verify_public_product(self, key, verified_source_urls):
        if self.fail_public:
            raise RuntimeError("modelled public verification failure")
        spec = tool.FAMILY_SPECS[key]
        if len(verified_source_urls) != 4:
            raise RuntimeError("modelled public source URL mismatch")
        return {"url": spec["permalink"], "http_status": 200,
                "product_id": spec["product_id"], "fixed_images_in_order": 4,
                "rendered_gallery_items": 4, "broken_images": 0,
                "javascript_errors": 0}

    def leave_on_media_list(self):
        self.left = True


class ProductFamilyMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.plan_dir = Path(self.tmp.name) / "plans"
        self.receipts = Path(self.tmp.name) / "receipts.jsonl"
        self.patchers = [
            mock.patch.object(tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(tool, "STAGE_REGISTRY_DIR", self.plan_dir / "stage-registry"),
            mock.patch.object(tool, "ATTEMPT_LEDGER_DIR", self.plan_dir / "attempt-ledger"),
            mock.patch.object(tool, "RESERVATION_DIR", self.plan_dir / "result-reservations"),
            mock.patch.object(tool, "RESULT_DIR", self.plan_dir / "results"),
            mock.patch.object(tool, "JOURNAL_DIR", self.plan_dir / "event-journal"),
            mock.patch.object(tool, "REGISTRY_KEY_PATH", Path(self.tmp.name) / "registry.key"),
            mock.patch.object(tool, "RECEIPTS", self.receipts),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmp.cleanup()

    def test_common_transport_forwards_only_the_exact_conditional_gallery_etag(self):
        captured = {}

        class Response:
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return b"{}"

        class Opener:
            def open(self, request, timeout):
                captured["headers"] = dict(request.header_items())
                captured["method"] = request.get_method()
                return Response()

        vault = {
            "site_url": tool.EXACT_ORIGIN,
            "consumer_key": "ck_local_test",
            "consumer_secret": "cs_local_test",
            "declared_permissions": "read_write",
        }
        etag = '"' + "a" * 64 + '"'
        with mock.patch.object(tool.wc, "build_opener", return_value=Opener()):
            tool.wc.api_request(
                "PUT", "/products/1368", payload={"images": [{"id": 1}]},
                vault=vault, if_match=etag,
            )
        self.assertEqual(captured["method"], "PUT")
        self.assertEqual(captured["headers"].get("If-match"), etag)
        with self.assertRaises(tool.wc.WooError):
            tool.wc.api_request(
                "PUT", "/products/1368", payload={"images": [{"id": 1}]},
                vault=vault, if_match="a" * 64,
            )

    def test_terminal_evidence_is_published_only_after_full_flush(self):
        final = Path(self.tmp.name) / "terminal.json"
        with mock.patch.object(tool.os, "fsync", side_effect=OSError("modelled fsync failure")):
            with self.assertRaises(OSError):
                tool.write_json(final, {"status": "verified"}, exclusive=True)
        self.assertFalse(final.exists())
        self.assertEqual(list(Path(self.tmp.name).glob("*.pending")), [])
        tool.write_json(final, {"status": "indeterminate_no_retry"}, exclusive=True)
        self.assertEqual(
            json.loads(final.read_text(encoding="utf-8"))["status"],
            "indeterminate_no_retry",
        )
        with self.assertRaises(tool.FamilyMediaError):
            tool.write_json(final, {"status": "second_write_forbidden"}, exclusive=True)

    @staticmethod
    def product_record(key="stub_flange", gallery=None):
        spec = tool.FAMILY_SPECS[key]
        record = {
            "id": spec["product_id"],
            "name": spec["label"],
            "slug": key.replace("_", "-"),
            "type": spec["type"],
            "status": spec["status"],
            "catalog_visibility": "visible",
            "sku": spec["sku"],
            "permalink": spec["permalink"],
            "price": "100.00",
            "regular_price": "100.00",
            "stock_status": "instock",
            "categories": [{"id": 9, "name": "Fittings", "slug": "fittings"}],
            "tags": [], "attributes": [], "variations": [],
            "related_ids": [1455, 2061, 1423],
            "meta_data": [{"id": 55, "key": "catalog_class", "value": "Website Catalog"}],
            "date_created": "2026-01-01T10:00:00",
            "date_created_gmt": "2026-01-01T15:00:00",
            "yoast_head": "derived from gallery",
            "yoast_head_json": {"og_image": [{"url": "derived"}]},
            "date_modified_gmt": "2026-08-15T10:00:00",
            "images": deepcopy(gallery if gallery is not None else [
                {"id": 100, "alt": "old hero", "src": "https://frpdepots.com/old.png"}
            ]),
        }
        return record

    @classmethod
    def product_evidence_for(cls, key="stub_flange"):
        record = cls.product_record(key)
        spec = tool.FAMILY_SPECS[key]
        projection = tool.protected_product_projection(record)
        return {
            "product_id": spec["product_id"],
            "identity": {field: record[field] for field in
                         ("id", "name", "sku", "type", "status", "permalink")},
            "date_modified_gmt": record["date_modified_gmt"],
            "before_gallery": tool.safe_gallery(record),
            "protected_projection": projection,
            "protected_fingerprint": hashlib.sha256(
                tool.canonical(projection).encode("utf-8")
            ).hexdigest(),
        }

    @staticmethod
    def duplicate_ok(families=None):
        return {
            "checked_utc": "2026-08-15T10:00:00+00:00", "library_total": 1,
            "enumerated": 1, "pages_read": 1, "enumeration_complete": True,
            "image_rows": 0, "image_hashes_completed": 0, "hash_failures": 0,
            "hash_bytes_read": 0, "hash_complete": True,
            "recheck_total": 1, "recheck_enumerated": 1, "recheck_pages": 1,
            "recheck_complete": True, "snapshot_stable": True,
            "recheck_image_hashes_completed": 0, "recheck_hash_failures": 0,
            "recheck_hash_bytes_read": 0, "recheck_hash_complete": True,
            "content_stable": True, "final_total": 1, "final_enumerated": 1,
            "final_pages": 1, "final_complete": True, "final_snapshot_stable": True,
            "private_exception": {
                "attachment_id": tool.GUARD_PRIVATE_EXCEPTION["attachment_id"],
                "filename": Path(tool.GUARD_PRIVATE_EXCEPTION["attached_file"]).name,
            },
            "private_exception_proven": True,
            "name_conflicts": [],
            "hash_conflicts": [], "target_families": families or ["stub_flange"],
            "complete": True,
        }

    @staticmethod
    def private_library_row():
        return {
            "id": tool.GUARD_PRIVATE_EXCEPTION["attachment_id"],
            "filename": Path(tool.GUARD_PRIVATE_EXCEPTION["attached_file"]).name,
            "stem": "hetron-cr-guide-2007_ineos",
        }

    def make_plan(self, key="stub_flange"):
        local = tool.validate_local_images(key)
        return tool.stage_one(
            key, local, self.product_evidence_for(key), self.duplicate_ok([key]),
            self.guard_snapshot_ok(key),
        )

    @staticmethod
    def guard_snapshot_ok(key="stub_flange", total=0):
        return {
            "schema": tool.GUARD_PROOF_SCHEMA, "plugin_version": tool.GUARD_PLUGIN_VERSION,
            "mode": "atomic_snapshot", "family": key,
            "generated_utc": "2026-08-16T12:00:00+00:00",
            "attachment_total": total + 1, "hashed_total": total, "total_bytes": total * 10,
            "snapshot_sha256": "c" * 64, "complete": True, "failures": [],
            "private_exceptions": [deepcopy(tool.GUARD_PRIVATE_EXCEPTION)],
            "name_conflicts": [], "hash_conflicts": [], "fixed_matches": [],
            "guard_active": False,
        }

    @staticmethod
    def plan_sha(path: Path) -> str:
        return str(json.loads(path.read_text(encoding="utf-8"))["sha256"])

    @staticmethod
    def operation_sha(path: Path) -> str:
        return str(json.loads(path.read_text(encoding="utf-8"))["operation_sha256"])

    @staticmethod
    def downloader_for(key):
        by_name = {row["filename"]: Path(row["path"]).read_bytes()
                   for row in tool.FAMILY_SPECS[key]["images"]}

        def download(url, expected_basename=None, allowed_extensions=None):
            name = expected_basename or Path(str(url)).name
            return by_name[name]
        return download

    def commit_patches(self, key, admin, *, request_failure=False,
                       protected_drift=False, wrong_response_id=False,
                       pre_put_drift=False, metadata_drift=False,
                       post_completion_drift=False, gallery_race=False):
        before_record = self.product_record(key)
        state = {"put": False, "payload": None, "request_calls": [], "get_calls": 0}

        def api_get(endpoint, params=None, vault=None):
            state["get_calls"] += 1
            record = deepcopy(before_record)
            if pre_put_drift and not state["put"] and state["get_calls"] == 2:
                record["date_modified_gmt"] = "2026-08-15T10:30:00"
            if state["put"]:
                record["images"] = deepcopy(state["payload"]["images"])
                record["date_modified_gmt"] = "2026-08-15T11:00:00"
                if protected_drift:
                    record["price"] = "999.00"
                if metadata_drift:
                    record["meta_data"] = [{"id": 55, "key": "catalog_class", "value": "changed"}]
                if post_completion_drift and state["get_calls"] >= 5:
                    record["price"] = "777.00"
            return record, {}

        def api_request(method, endpoint, *, params=None, payload=None, vault=None, timeout=60,
                        if_match=None):
            state["request_calls"].append((method, endpoint, deepcopy(payload)))
            state["if_match"] = if_match
            if request_failure:
                raise RuntimeError("modelled PUT failure")
            if gallery_race:
                raise RuntimeError("modelled server-side gallery precondition failure")
            state["put"] = True
            state["payload"] = deepcopy(payload)
            response = deepcopy(before_record)
            response["id"] = 999999 if wrong_response_id else before_record["id"]
            response["images"] = deepcopy(payload["images"])
            return response, {}

        patches = [
            mock.patch.object(tool.wc, "load_vault", return_value={
                "declared_permissions": "read_write", "site_url": tool.EXACT_ORIGIN,
                "consumer_key": "hidden", "consumer_secret": "hidden",
            }),
            mock.patch.object(tool.wc, "api_get", side_effect=api_get),
            mock.patch.object(tool.wc, "api_request", side_effect=api_request),
            mock.patch.object(tool, "admin_session",
                              side_effect=lambda allowed: contextlib.nullcontext(admin)),
            mock.patch.object(tool, "ui_browser_lock",
                              side_effect=lambda *a, **k: contextlib.nullcontext()),
            mock.patch.object(tool.media_base, "download_public_bytes",
                              side_effect=self.downloader_for(key)),
            mock.patch.object(tool, "emit"),
        ]
        return patches, state

    def test_exact_five_families_and_product_ids(self):
        self.assertEqual(tuple(tool.FAMILY_SPECS),
                         ("stub_flange", "open_manway", "manway_cover", "elbow_90", "pipe"))
        self.assertEqual(tool.FIXED_PRODUCT_IDS, frozenset({1368, 1397, 1411, 1423, 1455}))

    def test_fnpt_is_unreachable(self):
        self.assertNotIn("fnpt", tool.FAMILY_SPECS)
        self.assertNotIn(2061, tool.FIXED_PRODUCT_IDS)
        self.assertIn("FNPT", tool.RISK_DISCLOSURE + SOURCE_TEXT)

    def test_each_family_has_exactly_four_positions(self):
        for spec in tool.FAMILY_SPECS.values():
            self.assertEqual([row["position"] for row in spec["images"]], [1, 2, 3, 4])

    def test_all_fixed_paths_hashes_and_names_are_unique(self):
        rows = [row for spec in tool.FAMILY_SPECS.values() for row in spec["images"]]
        self.assertEqual(len({row["path"] for row in rows}), len(rows))
        self.assertEqual(len({row["sha256"] for row in rows}), len(rows))
        self.assertEqual(len({row["filename"] for row in rows}), len(rows))

    def test_every_pinned_local_file_verifies(self):
        for key in tool.FAMILY_KEYS:
            self.assertEqual(len(tool.validate_local_images(key)), 4)

    def test_alt_text_is_not_in_fixed_specs_or_gallery_payload_template(self):
        for spec in tool.FAMILY_SPECS.values():
            for row in spec["images"]:
                self.assertNotIn("alt", row)
        path, _ = self.make_plan()
        plan = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(all(set(row) == {"position", "filename", "sha256"}
                            for row in plan["gallery_payload_template"]))

    def test_approved_collection_manifest_is_pinned_and_exact(self):
        evidence = tool.validate_approved_manifest()
        self.assertEqual(evidence["sha256"], tool.APPROVED_MANIFEST_SHA256)
        self.assertEqual(evidence["status"], "APPROVED_WORKING_COLLECTION_NOT_PUBLISHED")

    def test_approved_collection_manifest_hash_drift_refuses(self):
        with mock.patch.object(tool, "APPROVED_MANIFEST_SHA256", "0" * 64):
            with self.assertRaises(tool.FamilyMediaError):
                tool.validate_approved_manifest()

    def test_approved_elbow_hero_preserves_1254_square(self):
        hero = tool.FAMILY_SPECS["elbow_90"]["images"][0]
        self.assertEqual((hero["width"], hero["height"]), (1254, 1254))

    def test_approval_is_byte_exact(self):
        tool.require_approval("APPROVED")
        for bad in ("approved", " Approved", "APPROVED ", "APPROVED\n", "", None):
            with self.subTest(bad=bad), self.assertRaises(tool.FamilyMediaError):
                tool.require_approval(bad)

    def test_parser_closes_family_choices(self):
        parser = tool.build_parser()
        parsed = parser.parse_args(["stage", "--family", "pipe"])
        self.assertEqual(parsed.family, "pipe")
        with self.assertRaises(SystemExit):
            parser.parse_args(["stage", "--family", "fnpt"])

    def test_parser_has_only_stage_stage_all_and_commit(self):
        parser = tool.build_parser()
        choices = next(action for action in parser._actions
                       if isinstance(action, argparse._SubParsersAction)).choices
        self.assertEqual(set(choices), {"stage", "stage-all", "commit"})

    def test_only_one_woocommerce_write_call_exists_and_is_put(self):
        calls = []
        for node in ast.walk(SOURCE_TREE):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "api_request":
                    calls.append(node)
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0].args[0], ast.Constant)
        self.assertEqual(calls[0].args[0].value, "PUT")

    def test_no_delete_http_or_mail_transport(self):
        upper = SOURCE_TEXT.upper()
        self.assertNotIn('API_REQUEST("DELETE"', upper)
        self.assertNotIn("SMTPLIB", upper)
        self.assertNotIn("MAIL.SEND", upper)
        self.assertNotIn("REQUESTS.DELETE", upper)
        self.assertNotIn(".cookies(", SOURCE_TEXT)
        self.assertNotIn("document.cookie", SOURCE_TEXT)
        self.assertNotIn("storage_state", SOURCE_TEXT)

    def test_guard_completion_precedes_nonessential_public_page_verification(self):
        self.assertLess(
            SOURCE_TEXT.index("guard_completion = admin.complete_guard"),
            SOURCE_TEXT.index("public_verification = admin.verify_public_product"),
        )

    def test_safe_gallery_is_closed_projection(self):
        self.assertEqual(tool.safe_gallery({"images": [{"id": 7, "alt": "a", "src": "x"}]}),
                         [{"id": 7, "alt": "a"}])
        for images in (None, [{"id": 0}], [{"id": "7"}], ["bad"]):
            with self.subTest(images=images), self.assertRaises(tool.FamilyMediaError):
                tool.safe_gallery({"images": images})

    def test_protected_projection_excludes_only_mutable_gallery_and_dates(self):
        record = self.product_record()
        projection = tool.protected_product_projection(record)
        self.assertNotIn("images", projection)
        self.assertNotIn("date_modified_gmt", projection)
        self.assertNotIn("yoast_head", projection)
        self.assertNotIn("yoast_head_json", projection)
        for field in ("status", "price", "regular_price", "stock_status", "sku", "name",
                      "permalink", "meta_data", "date_created", "date_created_gmt"):
            self.assertIn(field, projection)
        self.assertEqual(projection["related_ids"], [1423, 1455, 2061])

    def test_related_ids_order_is_canonical_but_membership_remains_protected(self):
        first = self.product_record()
        second = self.product_record()
        second["related_ids"] = [1423, 2061, 1455]
        self.assertEqual(tool.protected_product_projection(first),
                         tool.protected_product_projection(second))
        second["related_ids"] = [1423, 2061, 9999]
        self.assertNotEqual(tool.protected_product_projection(first),
                            tool.protected_product_projection(second))
        for invalid in (None, [1423, 1423], [0], ["1423"]):
            record = self.product_record()
            record["related_ids"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(tool.FamilyMediaError):
                tool.protected_product_projection(record)

    def test_stage_plan_round_trips_hash_and_fixed_contract(self):
        path, staged = self.make_plan()
        loaded = tool.load_plan(path)
        self.assertEqual(loaded["sha256"], staged["sha256"])
        self.assertEqual(loaded["endpoint"], "/products/1368")
        self.assertEqual(len(loaded["files"]), 4)
        self.assertEqual(loaded["approval_manifest"]["sha256"], tool.APPROVED_MANIFEST_SHA256)
        self.assertEqual(loaded["guard_contract"], tool.guard_contract("stub_flange"))
        self.assertEqual(loaded["guard_stage_snapshot"]["mode"], "atomic_snapshot")

    def test_guard_admin_url_surface_is_exact(self):
        tool.assert_guard_admin_url(tool.GUARD_ADMIN_URL)
        tool.assert_guard_admin_url(tool.GUARD_POST_URL)
        refused = [
            tool.GUARD_ADMIN_URL + "&page=frpd-media-mutation-guard",
            tool.GUARD_ADMIN_URL + "&x=1",
            tool.GUARD_POST_URL + "?action=frpd_media_guard_acquire",
            "https://example.com/wp-admin/tools.php?page=frpd-media-mutation-guard",
            "https://frpdepots.com/wp-admin/options.php",
        ]
        for url in refused:
            with self.subTest(url=url), self.assertRaises(
                    (tool.FamilyMediaError, tool.media_base.MediaUploadError)):
                tool.assert_guard_admin_url(url)

    def test_guard_snapshot_proof_is_closed_and_requires_no_conflicts(self):
        good = self.guard_snapshot_ok()
        self.assertEqual(
            tool.require_empty_guard_snapshot(
                good, "stub_flange", "atomic_snapshot", self.duplicate_ok(["stub_flange"])
            )["snapshot_sha256"],
            "c" * 64,
        )
        bad_values = []
        extra = deepcopy(good)
        extra["unexpected"] = True
        bad_values.append(extra)
        conflict = deepcopy(good)
        conflict["name_conflicts"] = [{"attachment_id": 7, "fixed_position": 1}]
        bad_values.append(conflict)
        active = deepcopy(good)
        active["guard_active"] = True
        bad_values.append(active)
        incomplete = deepcopy(good)
        incomplete["complete"] = False
        bad_values.append(incomplete)
        private_path_drift = deepcopy(good)
        private_path_drift["private_exceptions"][0]["attached_file"] = "2026/03/other.pdf"
        bad_values.append(private_path_drift)
        private_protector_drift = deepcopy(good)
        private_protector_drift["private_exceptions"][0]["protector_plugin_sha256"] = "0" * 64
        bad_values.append(private_protector_drift)
        for proof in bad_values:
            with self.subTest(proof=proof), self.assertRaises(tool.FamilyMediaError):
                tool.require_empty_guard_snapshot(proof, "stub_flange", "atomic_snapshot")

    def test_guard_runtime_manifest_matches_all_five_families_and_excludes_fnpt(self):
        contract = tool.validate_guard_manifest_contract()
        self.assertEqual(contract["zip_sha256"], tool.GUARD_ZIP_SHA256)
        self.assertEqual(contract["plugin_php_sha256"], tool.GUARD_PLUGIN_PHP_SHA256)
        self.assertEqual(contract["sha256"], tool.GUARD_RUNTIME_MANIFEST_SHA256)
        self.assertEqual(set(contract["families"]), set(tool.FAMILY_KEYS))
        self.assertFalse(contract["fnpt_present"])

    def test_guard_runtime_manifest_semantic_drift_refuses_even_with_matching_new_hash(self):
        data = json.loads(tool.GUARD_RUNTIME_MANIFEST_PATH.read_text(encoding="utf-8"))
        data["families"]["stub_flange"]["product_id"] = 9999
        raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        tampered = Path(self.tmp.name) / "tampered-approved-media.json"
        tampered.write_bytes(raw)
        with mock.patch.object(tool, "GUARD_RUNTIME_MANIFEST_PATH", tampered), \
                mock.patch.object(tool, "GUARD_RUNTIME_MANIFEST_SHA256",
                                  hashlib.sha256(raw).hexdigest()):
            with self.assertRaises(tool.FamilyMediaError):
                tool.validate_guard_manifest_contract()

    def test_guarded_snapshot_binds_each_uploaded_id_to_its_fixed_position(self):
        admin = FakeAdmin("stub_flange")
        proof = admin._snapshot("guarded_snapshot", True, 4, 4)
        tool.require_guarded_upload_snapshot(
            proof, "stub_flange", 1, [9001, 9002, 9003, 9004]
        )
        bad = deepcopy(proof)
        bad["hash_conflicts"][2]["attachment_id"] = 9999
        with self.assertRaises(tool.FamilyMediaError):
            tool.require_guarded_upload_snapshot(
                bad, "stub_flange", 1, [9001, 9002, 9003, 9004]
            )

    def test_authorization_margin_refuses_plan_too_close_to_expiry(self):
        with self.assertRaises(tool.FamilyMediaError):
            tool.require_authorization_margin({
                "expires_utc": (tool.utc_now() + tool.MIN_AUTHORIZATION_MARGIN / 2).isoformat()
            })
        tool.require_authorization_margin({
            "expires_utc": (tool.utc_now() + tool.MIN_AUTHORIZATION_MARGIN * 2).isoformat()
        })

    def test_rehashed_plan_with_unknown_field_is_refused(self):
        path, _ = self.make_plan()
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("sha256")
        raw["unexpected"] = True
        raw["sha256"] = tool.digest_for(raw)
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(tool.FamilyMediaError):
            tool.load_plan(path)

    def test_rehashed_plan_with_nested_identity_field_is_refused(self):
        path, _ = self.make_plan()
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("sha256")
        raw["product_before"]["identity"]["unexpected"] = True
        raw["sha256"] = tool.digest_for(raw)
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(tool.FamilyMediaError):
            tool.load_plan(path)

    def test_tampered_plan_fails_hash(self):
        path, _ = self.make_plan()
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["product_id"] = 999
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(tool.FamilyMediaError):
            tool.load_plan(path)

    def test_expired_plan_fails(self):
        path, _ = self.make_plan()
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("sha256")
        raw["expires_utc"] = (tool.utc_now() - timedelta(seconds=1)).isoformat()
        raw["sha256"] = tool.digest_for(raw)
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(tool.FamilyMediaError):
            tool.load_plan(path)

    def test_foreign_plan_path_refused(self):
        foreign = Path(self.tmp.name) / "foreign.json"
        foreign.write_text("{}", encoding="utf-8")
        with self.assertRaises(tool.FamilyMediaError):
            tool.fixed_plan_path(str(foreign))

    def test_copied_or_renamed_plan_cannot_reach_vault_or_commit(self):
        path, _ = self.make_plan()
        copied = self.plan_dir / "copied-approved-plan.json"
        shutil.copy2(path, copied)
        with mock.patch.object(tool.wc, "load_vault") as vault:
            with self.assertRaises(tool.FamilyMediaError):
                tool.command_commit(argparse.Namespace(plan=str(copied), approval="APPROVED"))
        vault.assert_not_called()

    def test_hard_linked_plan_alias_is_refused(self):
        path, _ = self.make_plan()
        alias = self.plan_dir / "hard-linked-plan.json"
        try:
            os.link(path, alias)
        except OSError as exc:
            self.skipTest(f"hard links unavailable: {exc}")
        with self.assertRaises(tool.FamilyMediaError):
            tool.fixed_plan_path(str(alias))
        with self.assertRaises(tool.FamilyMediaError):
            tool.fixed_plan_path(str(path))

    def test_rehashed_and_canonically_renamed_plan_without_registry_is_refused(self):
        path, _ = self.make_plan()
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("sha256")
        future = tool.utc_now() + timedelta(minutes=1)
        raw["created_utc"] = future.isoformat()
        raw["expires_utc"] = (future + timedelta(hours=24)).isoformat()
        forged_sha = tool.digest_for(raw)
        raw["sha256"] = forged_sha
        created = tool.datetime.fromisoformat(raw["created_utc"])
        forged = self.plan_dir / tool.canonical_plan_filename(created, raw["family"], forged_sha)
        forged.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(tool.FamilyMediaError):
            tool.load_plan(forged)

    def test_two_legitimately_signed_plans_share_one_stable_no_retry_operation(self):
        first, _ = self.make_plan()
        second, _ = self.make_plan()
        self.assertNotEqual(self.plan_sha(first), self.plan_sha(second))
        self.assertEqual(self.operation_sha(first), self.operation_sha(second))
        tool.write_json(tool.lock_path(self.operation_sha(first)), {"status": "acting"}, exclusive=True)
        with mock.patch.object(tool.wc, "load_vault") as vault:
            with self.assertRaises(tool.FamilyMediaError):
                tool.command_commit(argparse.Namespace(plan=str(second), approval="APPROVED"))
        vault.assert_not_called()

    def test_fabricated_matching_registry_fails_authentication_before_vault(self):
        original, _ = self.make_plan()
        raw = json.loads(original.read_text(encoding="utf-8"))
        raw.pop("sha256")
        raw["nonce"] = "f" * 32
        forged_sha = tool.digest_for(raw)
        raw["sha256"] = forged_sha
        created = tool.datetime.fromisoformat(raw["created_utc"])
        forged = self.plan_dir / tool.canonical_plan_filename(created, raw["family"], forged_sha)
        forged.write_text(json.dumps(raw), encoding="utf-8")
        registry_core = {
            "schema_version": tool.SCHEMA_VERSION,
            "tool_version": tool.TOOL_VERSION,
            "plan_sha256": forged_sha,
            "operation_sha256": raw["operation_sha256"],
            "plan_file_sha256": hashlib.sha256(forged.read_bytes()).hexdigest(),
            "plan_path": str(forged.resolve()),
            "plan_filename": forged.name,
            "family": raw["family"], "product_id": raw["product_id"],
            "nonce": raw["nonce"], "created_utc": raw["created_utc"],
            "expires_utc": raw["expires_utc"],
        }
        tool.write_json(tool.stage_registry_path(forged_sha),
                        {**registry_core, "hmac_sha256": "0" * 64}, exclusive=True)
        self.assertEqual(tool.load_plan(forged, authenticate_registry=False)["operation_sha256"],
                         self.operation_sha(original))
        with mock.patch.object(tool.wc, "load_vault") as vault, \
                mock.patch.object(tool.wc, "api_request") as request:
            with self.assertRaises(tool.FamilyMediaError):
                tool.command_commit(argparse.Namespace(plan=str(forged), approval="APPROVED"))
        vault.assert_not_called()
        request.assert_not_called()

    def test_verified_upload_payload_uses_exact_opened_bytes_and_refuses_mutation(self):
        path = Path(self.tmp.name) / "fixed.png"
        original = b"approved-fixed-bytes"
        path.write_bytes(original)
        expected = {"path": str(path), "filename": path.name, "bytes": len(original),
                    "sha256": hashlib.sha256(original).hexdigest()}
        with mock.patch.object(tool, "ALL_FIXED_PATHS", frozenset({path.resolve()})):
            payload = tool.verified_upload_payload(expected)
            self.assertEqual(payload["buffer"], original)
            self.assertEqual(payload["name"], "fixed.png")
            path.write_bytes(b"changed-after-preflight")
            with self.assertRaises(tool.FamilyMediaError):
                tool.verified_upload_payload(expected)

    def test_duplicate_complete_clean_passes(self):
        tool.require_no_duplicates(self.duplicate_ok())

    def test_duplicate_private_attachment_evidence_is_exact(self):
        for field, value in (("attachment_id", 9999), ("filename", "other.pdf")):
            evidence = self.duplicate_ok()
            evidence["private_exception"][field] = value
            with self.subTest(field=field), self.assertRaises(tool.FamilyMediaError):
                tool.validate_duplicate_evidence(evidence, "stub_flange")

    def test_duplicate_scan_refuses_private_filename_or_id_substitution(self):
        expected_name = Path(tool.GUARD_PRIVATE_EXCEPTION["attached_file"]).name
        substitutions = [
            {"id": 9999, "filename": expected_name, "stem": "hetron-cr-guide-2007_ineos"},
            {"id": tool.GUARD_PRIVATE_EXCEPTION["attachment_id"],
             "filename": "other.pdf", "stem": "other"},
        ]
        targets = {"stub_flange": {"images": [{
            "filename": "fixed.png", "sha256": "0" * 64,
        }]}}
        for row in substitutions:
            walked = {"rows": [row], "total": 1, "pages": 1,
                      "complete": True, "unidentified": 0}
            evidence = tool.duplicate_scan(mock.Mock(), targets, walked)
            with self.subTest(row=row):
                self.assertFalse(evidence["private_exception_proven"])
                self.assertFalse(evidence["complete"])
                with self.assertRaises(tool.FamilyMediaError):
                    tool.require_no_duplicates(evidence)

    def test_duplicate_incomplete_refuses(self):
        evidence = self.duplicate_ok()
        evidence["hash_complete"] = False
        evidence["complete"] = False
        with self.assertRaises(tool.FamilyMediaError):
            tool.require_no_duplicates(evidence)

    def test_duplicate_name_conflict_refuses(self):
        evidence = self.duplicate_ok()
        evidence["name_conflicts"] = [{"attachment_id": 1, "filename": "fixed.png"}]
        with self.assertRaises(tool.FamilyMediaError):
            tool.require_no_duplicates(evidence)

    def test_duplicate_hash_conflict_refuses(self):
        evidence = self.duplicate_ok()
        evidence["hash_conflicts"] = [{"attachment_id": 1, "matches_fixed_image": "x"}]
        with self.assertRaises(tool.FamilyMediaError):
            tool.require_no_duplicates(evidence)

    def test_duplicate_scan_rejects_list_and_detail_filename_mismatch(self):
        walked = {
            "rows": [self.private_library_row(),
                     {"id": 99, "filename": "reported.jpg", "stem": "reported"}],
            "total": 2, "pages": 2, "complete": True, "unidentified": 0,
        }
        admin = mock.Mock()
        admin.read_attachment.return_value = {
            "attachment_id": 99,
            "filename": "actual.png",
            "source_url": "https://frpdepots.com/wp-content/uploads/actual.png",
            "extension": ".png",
            "filetype_matches_name": True,
        }
        targets = {"stub_flange": {"images": [{
            "filename": "fixed.png", "sha256": "0" * 64,
        }]}}
        evidence = tool.duplicate_scan(admin, targets, walked)
        self.assertFalse(evidence["complete"])
        self.assertFalse(evidence["hash_complete"])
        self.assertEqual(evidence["hash_failures"], 1)
        self.assertEqual(evidence["image_hashes_completed"], 0)
        with self.assertRaises(tool.FamilyMediaError):
            tool.require_no_duplicates(evidence)

    def test_duplicate_scan_rejects_attachment_outside_closed_image_types(self):
        walked = {
            "rows": [self.private_library_row(),
                     {"id": 100, "filename": "benign.png", "stem": "benign"}],
            "total": 2, "pages": 2, "complete": True, "unidentified": 0,
        }
        admin = mock.Mock()
        admin.read_attachment.side_effect = tool.media_base.MediaUploadError("not image")
        targets = {"stub_flange": {"images": [{
            "filename": "fixed.png", "sha256": "0" * 64,
        }]}}
        evidence = tool.duplicate_scan(admin, targets, walked)
        self.assertFalse(evidence["complete"])
        self.assertEqual(evidence["hash_failures"], 1)
        self.assertEqual(evidence["image_rows"], 1)

    def test_duplicate_scan_requires_exact_stable_second_snapshot(self):
        first = {
            "rows": [self.private_library_row(),
                     {"id": 99, "filename": "benign.png", "stem": "benign"}],
            "total": 2, "pages": 2, "complete": True, "unidentified": 0,
        }
        changed = {
            "rows": [
                self.private_library_row(),
                {"id": 99, "filename": "benign.png", "stem": "benign"},
                {"id": 100, "filename": "inserted.png", "stem": "inserted"},
            ],
            "total": 3, "pages": 2, "complete": True, "unidentified": 0,
        }
        admin = mock.Mock()
        admin.read_attachment.return_value = {
            "attachment_id": 99, "filename": "benign.png",
            "source_url": "https://frpdepots.com/wp-content/uploads/benign.png",
            "extension": ".png", "filetype_matches_name": True,
        }
        admin.enumerate_library.return_value = changed
        targets = {"stub_flange": {"images": [{
            "filename": "fixed.png", "sha256": hashlib.sha256(b"fixed").hexdigest(),
        }]}}
        with mock.patch.object(tool.media_base, "download_public_bytes", return_value=b"benign"):
            evidence = tool.duplicate_scan(admin, targets, first)
        self.assertTrue(evidence["hash_complete"])
        self.assertTrue(evidence["recheck_complete"])
        self.assertFalse(evidence["snapshot_stable"])
        self.assertFalse(evidence["complete"])
        self.assertEqual(evidence["recheck_total"], 3)
        with self.assertRaises(tool.FamilyMediaError):
            tool.require_no_duplicates(evidence)

    def test_duplicate_scan_accepts_same_id_filename_set_in_different_order(self):
        first = {
            "rows": [
                self.private_library_row(),
                {"id": 99, "filename": "a.png", "stem": "a"},
                {"id": 100, "filename": "b.png", "stem": "b"},
            ],
            "total": 3, "pages": 2, "complete": True, "unidentified": 0,
        }
        second = deepcopy(first)
        second["rows"].reverse()
        details = {
            99: {"attachment_id": 99, "filename": "a.png",
                 "source_url": "https://frpdepots.com/wp-content/uploads/a.png",
                 "extension": ".png", "filetype_matches_name": True},
            100: {"attachment_id": 100, "filename": "b.png",
                  "source_url": "https://frpdepots.com/wp-content/uploads/b.png",
                  "extension": ".png", "filetype_matches_name": True},
        }
        admin = mock.Mock()
        admin.read_attachment.side_effect = lambda attachment_id, **kwargs: details[attachment_id]
        admin.enumerate_library.return_value = second
        targets = {"stub_flange": {"images": [{
            "filename": "fixed.png", "sha256": hashlib.sha256(b"fixed").hexdigest(),
        }]}}
        with mock.patch.object(tool.media_base, "download_public_bytes", return_value=b"benign"):
            evidence = tool.duplicate_scan(admin, targets, first)
        self.assertTrue(evidence["snapshot_stable"])
        self.assertTrue(evidence["complete"])
        tool.require_no_duplicates(evidence)

    def test_duplicate_scan_rejects_same_id_filename_when_bytes_change_between_passes(self):
        snapshot = {
            "rows": [self.private_library_row(),
                     {"id": 99, "filename": "benign.png", "stem": "benign"}],
            "total": 2, "pages": 2, "complete": True, "unidentified": 0,
        }
        detail = {
            "attachment_id": 99, "filename": "benign.png",
            "source_url": "https://frpdepots.com/wp-content/uploads/benign.png",
            "extension": ".png", "filetype_matches_name": True,
        }
        fixed_bytes = b"fixed-target-bytes"
        admin = mock.Mock()
        admin.read_attachment.return_value = detail
        admin.enumerate_library.return_value = deepcopy(snapshot)
        targets = {"stub_flange": {"images": [{
            "filename": "fixed.png", "sha256": hashlib.sha256(fixed_bytes).hexdigest(),
        }]}}
        with mock.patch.object(
                tool.media_base, "download_public_bytes",
                side_effect=[b"benign-first-pass", fixed_bytes]):
            evidence = tool.duplicate_scan(admin, targets, deepcopy(snapshot))
        self.assertTrue(evidence["snapshot_stable"])
        self.assertTrue(evidence["recheck_hash_complete"])
        self.assertFalse(evidence["content_stable"])
        self.assertTrue(evidence["final_snapshot_stable"])
        self.assertFalse(evidence["complete"])
        self.assertEqual(len(evidence["hash_conflicts"]), 1)
        with self.assertRaises(tool.FamilyMediaError):
            tool.require_no_duplicates(evidence)

    def test_duplicate_scan_rejects_source_url_change_between_hash_passes(self):
        snapshot = {
            "rows": [self.private_library_row(),
                     {"id": 99, "filename": "benign.png", "stem": "benign"}],
            "total": 2, "pages": 2, "complete": True, "unidentified": 0,
        }
        first_detail = {
            "attachment_id": 99, "filename": "benign.png",
            "source_url": "https://frpdepots.com/wp-content/uploads/a/benign.png",
            "extension": ".png", "filetype_matches_name": True,
        }
        second_detail = deepcopy(first_detail)
        second_detail["source_url"] = "https://frpdepots.com/wp-content/uploads/b/benign.png"
        admin = mock.Mock()
        admin.read_attachment.side_effect = [first_detail, second_detail]
        admin.enumerate_library.return_value = deepcopy(snapshot)
        targets = {"stub_flange": {"images": [{
            "filename": "fixed.png", "sha256": hashlib.sha256(b"fixed").hexdigest(),
        }]}}
        with mock.patch.object(tool.media_base, "download_public_bytes", return_value=b"same"):
            evidence = tool.duplicate_scan(admin, targets, deepcopy(snapshot))
        self.assertFalse(evidence["content_stable"])
        self.assertFalse(evidence["complete"])

    def test_product_admin_rejects_non_allowlisted_path_before_page_use(self):
        admin = object.__new__(tool.ProductFamilyAdmin)
        admin._allowed_paths = frozenset()
        admin._page = mock.Mock()
        with self.assertRaises(tool.FamilyMediaError):
            admin.upload_one({"path": str(Path(self.tmp.name) / "foreign.png"),
                              "filename": "foreign.png"}, set())
        admin._page.assert_not_called()

    def test_product_admin_preserves_unique_id_when_list_filename_is_blank(self):
        page = mock.Mock()
        row = mock.Mock()
        link = mock.Mock()
        link.get_attribute.return_value = tool.media_base.attachment_edit_url(1767)

        def row_links(selector):
            if selector in ("a[href]", tool.media_base.ROW_LINK_SELECTOR):
                return [link] * 5
            if selector == tool.media_base.EMPTY_TABLE_CELL_SELECTOR:
                return []
            return []

        row.query_selector_all.side_effect = row_links
        row.query_selector.return_value = None
        page.query_selector_all.return_value = [row]
        admin = tool.ProductFamilyAdmin(page, frozenset())
        self.assertEqual(admin._row_records(), [
            {"id": 1767, "filename": "", "stem": ""}
        ])

    def test_exact_attachment_parser_rejects_repeated_or_extra_query_keys(self):
        origin = tool.media_base.EXACT_ORIGIN
        path = tool.media_base.POST_EDIT_PATH
        self.assertEqual(
            tool.parse_exact_attachment_edit_url(f"{origin}{path}?post=1767&action=edit"),
            1767,
        )
        refused = [
            f"{origin}{path}?post=1767&post=1768&action=edit",
            f"{origin}{path}?post=1767&action=edit&action=edit",
            f"{origin}{path}?post=1767&action=edit&extra=1",
        ]
        for url in refused:
            with self.subTest(url=url):
                self.assertIsNone(tool.parse_exact_attachment_edit_url(url))

    def test_row_with_repeated_post_keys_remains_unidentified(self):
        page = mock.Mock()
        row = mock.Mock()
        link = mock.Mock()
        link.get_attribute.return_value = (
            f"{tool.media_base.EXACT_ORIGIN}{tool.media_base.POST_EDIT_PATH}"
            "?post=1767&post=1768&action=edit"
        )

        def row_links(selector):
            if selector in ("a[href]", tool.media_base.ROW_LINK_SELECTOR):
                return [link]
            if selector == tool.media_base.EMPTY_TABLE_CELL_SELECTOR:
                return []
            return []

        row.query_selector_all.side_effect = row_links
        name = mock.Mock()
        name.inner_text.return_value = "File name: ambiguous.jpg"
        row.query_selector.return_value = name
        page.query_selector_all.return_value = [row]
        admin = tool.ProductFamilyAdmin(page, frozenset())
        self.assertEqual(admin._row_records(), [
            {"id": None, "filename": "", "stem": ""}
        ])

    def test_mixed_exact_and_malformed_edit_links_make_the_entire_row_unidentified(self):
        origin = tool.media_base.EXACT_ORIGIN
        path = tool.media_base.POST_EDIT_PATH
        exact = f"{origin}{path}?post=1767&action=edit"
        malformed = [
            f"{origin}{path}?post=1767&post=1768&action=edit",
            f"{origin}{path}?post=1767&action=edit&action=edit",
            f"{origin}{path}?post=1767&action=edit&extra=1",
            f"{origin}/wp-admin/%70ost.php?post=1768&action=edit",
            f"{origin}/wp-admin/x/../post.php?post=1768&action=edit",
            "?post=1768&action=edit",
            "post.php?post=1768&action=edit",
            "/wp-admin/post.php?post=1768&action=edit",
            "https://user@frpdepots.com/wp-admin/post.php?post=1768&action=edit",
            "//user@frpdepots.com/wp-admin/post.php?post=1768&action=edit",
            "https://frpdepots。com/wp-admin/post.php?post=1768&action=edit",
            "https://foreign.example/wp-admin/post.php?post=1768&action=edit",
        ]
        for bad_url in malformed:
            with self.subTest(bad_url=bad_url):
                page = mock.Mock()
                row = mock.Mock()
                links = []
                for href in (exact, bad_url):
                    link = mock.Mock()
                    link.get_attribute.return_value = href
                    links.append(link)

                def row_links(selector):
                    if selector in ("a[href]", tool.media_base.ROW_LINK_SELECTOR):
                        return links
                    if selector == tool.media_base.EMPTY_TABLE_CELL_SELECTOR:
                        return []
                    return []

                row.query_selector_all.side_effect = row_links
                name = mock.Mock()
                name.inner_text.return_value = "File name: ambiguous.jpg"
                row.query_selector.return_value = name
                page.query_selector_all.return_value = [row]
                admin = tool.ProductFamilyAdmin(page, frozenset())
                self.assertEqual(admin._row_records(), [
                    {"id": None, "filename": "", "stem": ""}
                ])

    def test_noncanonical_edit_candidate_makes_full_enumeration_incomplete(self):
        origin = tool.media_base.EXACT_ORIGIN
        path = tool.media_base.POST_EDIT_PATH
        page = mock.Mock()
        row = mock.Mock()
        links = []
        for href in (
            f"{origin}{path}?post=1767&action=edit",
            f"{origin}/wp-admin/%70ost.php?post=1768&action=edit",
        ):
            link = mock.Mock()
            link.get_attribute.return_value = href
            links.append(link)

        def row_links(selector):
            if selector in ("a[href]", tool.media_base.ROW_LINK_SELECTOR):
                return links
            if selector == tool.media_base.EMPTY_TABLE_CELL_SELECTOR:
                return []
            return []

        row.query_selector_all.side_effect = row_links
        name = mock.Mock()
        name.inner_text.return_value = "File name: ambiguous.jpg"
        row.query_selector.return_value = name
        page.query_selector_all.side_effect = [[row], []]
        admin = tool.ProductFamilyAdmin(page, frozenset())
        with mock.patch.object(admin, "_goto"), \
             mock.patch.object(admin, "_library_total", return_value=1):
            evidence = admin.enumerate_library()
        self.assertFalse(evidence["complete"])
        self.assertEqual(evidence["unidentified"], 1)
        self.assertEqual(evidence["rows"], [])

    def test_exact_edit_plus_ordinary_delete_link_keeps_one_exact_identity(self):
        origin = tool.media_base.EXACT_ORIGIN
        path = tool.media_base.POST_EDIT_PATH
        hrefs = [
            f"{origin}{path}?post=1767&action=edit",
            f"{origin}{path}?post=1767&action=delete",
        ]
        page = mock.Mock()
        row = mock.Mock()
        links = []
        for href in hrefs:
            link = mock.Mock()
            link.get_attribute.return_value = href
            links.append(link)

        def row_links(selector):
            if selector in ("a[href]", tool.media_base.ROW_LINK_SELECTOR):
                return links
            if selector == tool.media_base.EMPTY_TABLE_CELL_SELECTOR:
                return []
            return []

        row.query_selector_all.side_effect = row_links
        name = mock.Mock()
        name.inner_text.return_value = "File name: valid.jpg"
        row.query_selector.return_value = name
        page.query_selector_all.return_value = [row]
        admin = tool.ProductFamilyAdmin(page, frozenset())
        self.assertEqual(admin._row_records(), [{
            "id": 1767, "filename": "valid.jpg", "stem": "valid"
        }])

    def test_product_admin_resolves_blank_list_filename_from_fixed_attachment_screen(self):
        admin = tool.ProductFamilyAdmin(mock.Mock(), frozenset())
        blank = {"id": 1767, "filename": "", "stem": ""}
        detail = {
            "attachment_id": 1767,
            "filename": "frp-lots-of-pipes.jpg",
            "source_url": "https://frpdepots.com/wp-content/uploads/2026/03/frp-lots-of-pipes.jpg",
            "extension": ".jpg", "filetype_matches_name": True,
        }
        with mock.patch.object(admin, "_goto"), \
                mock.patch.object(admin, "_row_records", side_effect=[[blank], []]), \
                mock.patch.object(admin, "_library_total", return_value=1), \
                mock.patch.object(admin, "read_attachment", return_value=detail) as read:
            evidence = admin.enumerate_library()
        self.assertTrue(evidence["complete"])
        self.assertEqual(evidence["rows"], [{
            "id": 1767, "filename": "frp-lots-of-pipes.jpg",
            "stem": "frp-lots-of-pipes",
        }])
        read.assert_called_once_with(
            1767, allowed_extensions=tool.media_base.SCANNED_EXTENSIONS
        )

    def test_product_admin_blank_filename_resolution_failure_remains_closed(self):
        admin = tool.ProductFamilyAdmin(mock.Mock(), frozenset())
        blank = {"id": 1767, "filename": "", "stem": ""}
        with mock.patch.object(admin, "_goto"), \
                mock.patch.object(admin, "_row_records", side_effect=[[blank], []]), \
                mock.patch.object(admin, "_library_total", return_value=1), \
                mock.patch.object(admin, "read_attachment",
                                  side_effect=tool.media_base.MediaUploadError("unreadable")):
            with self.assertRaises(tool.media_base.MediaUploadError):
                admin.enumerate_library()

    def test_enumeration_reads_past_underreported_total_to_terminal_empty_page(self):
        admin = tool.ProductFamilyAdmin(mock.Mock(), frozenset())
        pages = [
            [
                {"id": 1, "filename": "one.jpg", "stem": "one"},
                {"id": 2, "filename": "two.jpg", "stem": "two"},
            ],
            [{"id": 3, "filename": "three.jpg", "stem": "three"}],
            [],
        ]
        with mock.patch.object(admin, "_goto"), \
                mock.patch.object(admin, "_row_records", side_effect=pages), \
                mock.patch.object(admin, "_library_total", return_value=2):
            evidence = admin.enumerate_library()
        self.assertFalse(evidence["complete"])
        self.assertEqual([row["id"] for row in evidence["rows"]], [1, 2, 3])
        self.assertEqual(evidence["pages"], 3)

    def test_duplicate_attachment_id_across_pages_makes_enumeration_incomplete(self):
        admin = tool.ProductFamilyAdmin(mock.Mock(), frozenset())
        pages = [
            [{"id": 1, "filename": "one.jpg", "stem": "one"}],
            [{"id": 1, "filename": "alias.jpg", "stem": "alias"}],
            [],
        ]
        with mock.patch.object(admin, "_goto"), \
                mock.patch.object(admin, "_row_records", side_effect=pages), \
                mock.patch.object(admin, "_library_total", return_value=2):
            evidence = admin.enumerate_library()
        self.assertFalse(evidence["complete"])
        self.assertEqual(evidence["unidentified"], 1)
        self.assertEqual([row["id"] for row in evidence["rows"]], [1])

    def test_attachment_detail_redirect_to_another_valid_id_refuses_before_identity_read(self):
        requested = tool.media_base.attachment_edit_url(1767)
        redirected = tool.media_base.attachment_edit_url(1768)
        page = mock.Mock()
        page.url = requested

        def redirect_on_goto(_url, **_kwargs):
            page.url = redirected

        page.goto.side_effect = redirect_on_goto
        page.query_selector.return_value = None
        admin = tool.ProductFamilyAdmin(page, frozenset())
        with self.assertRaisesRegex(tool.FamilyMediaError, "redirected"):
            admin.read_attachment(
                1767, allowed_extensions=tool.media_base.SCANNED_EXTENSIONS
            )
        page.query_selector_all.assert_not_called()

    def test_attachment_detail_redirect_with_repeated_post_keys_refuses(self):
        requested = tool.media_base.attachment_edit_url(1767)
        page = mock.Mock()
        page.url = requested

        def redirect_on_goto(_url, **_kwargs):
            page.url = (
                f"{tool.media_base.EXACT_ORIGIN}{tool.media_base.POST_EDIT_PATH}"
                "?post=1767&post=1768&action=edit"
            )

        page.goto.side_effect = redirect_on_goto
        page.query_selector.return_value = None
        admin = tool.ProductFamilyAdmin(page, frozenset())
        with self.assertRaisesRegex(tool.media_base.MediaUploadError, "repeats a query key"):
            admin.read_attachment(
                1767, allowed_extensions=tool.media_base.SCANNED_EXTENSIONS
            )
        page.query_selector_all.assert_not_called()

    def test_attachment_detail_exact_landed_id_is_accepted_by_navigation_guard(self):
        requested = tool.media_base.attachment_edit_url(1767)
        page = mock.Mock()
        page.url = requested

        def land_exactly(url, **_kwargs):
            page.url = url

        page.goto.side_effect = land_exactly
        page.query_selector.return_value = None
        admin = tool.ProductFamilyAdmin(page, frozenset())
        admin._goto(requested)
        self.assertEqual(page.url, requested)

    def public_page(self, key="stub_flange", *, console_error=False, reverse=False,
                    metadata_only=False, broken=False, wrong_path=False,
                    php_current=False, prefix_collision=False,
                    inconsistent_dimensions=False):
        spec = tool.FAMILY_SPECS[key]

        class Response:
            status = 200

        class Locator:
            def evaluate_all(self, script):
                if metadata_only:
                    return []
                images = list(spec["images"])
                if reverse:
                    images.reverse()
                rows = []
                for index, image in enumerate(images):
                    name = image["filename"]
                    directory = "/not-uploads" if wrong_path else "/wp-content/uploads/2026/08"
                    current_name = name
                    if php_current and index == 1:
                        current_name = Path(name).stem + ".php"
                    if prefix_collision and index == 1:
                        current_name = Path(name).stem + "-evil.png"
                    width = int(image["width"])
                    height = int(image["height"])
                    if inconsistent_dimensions and index == 1:
                        width += 1
                    rows.append({
                        "link_count": 1, "image_count": 1,
                        "href": f"https://frpdepots.com{directory}/{name}",
                        "complete": True, "natural_width": 0 if broken and index == 1 else width,
                        "natural_height": height,
                        "current_src": f"https://frpdepots.com{directory}/{current_name}",
                    })
                return rows

        class Page:
            def __init__(self):
                self.url = spec["permalink"]
                self.listeners = {}

            def on(self, name, callback):
                self.listeners[name] = callback

            def remove_listener(self, name, callback):
                if self.listeners.get(name) is callback:
                    self.listeners.pop(name)

            def goto(self, url, wait_until=None, timeout=None):
                self.url = url
                if console_error:
                    self.listeners["console"](type("Message", (), {"type": "error"})())
                return Response()

            def get_attribute(self, selector, name):
                return f"product-template-default postid-{spec['product_id']}"

            def locator(self, selector):
                return Locator()

            def content(self):
                return "<script type='application/json'>" + " ".join(
                    Path(row["filename"]).stem for row in spec["images"]
                ) + "</script><p>No gallery images rendered.</p>"

        return Page()

    def test_public_product_verifier_requires_fixed_route_identity_order_and_no_js_errors(self):
        admin = object.__new__(tool.ProductFamilyAdmin)
        source_urls = [
            f"https://frpdepots.com/wp-content/uploads/2026/08/{row['filename']}"
            for row in tool.FAMILY_SPECS["stub_flange"]["images"]
        ]
        admin._page = self.public_page()
        evidence = admin.verify_public_product("stub_flange", source_urls)
        self.assertEqual(evidence["fixed_images_in_order"], 4)
        self.assertEqual(evidence["javascript_errors"], 0)
        self.assertEqual(admin._page.listeners, {})

        for page in (
            self.public_page(console_error=True), self.public_page(reverse=True),
            self.public_page(metadata_only=True), self.public_page(broken=True),
            self.public_page(wrong_path=True), self.public_page(php_current=True),
            self.public_page(prefix_collision=True),
            self.public_page(inconsistent_dimensions=True),
        ):
            admin._page = page
            with self.assertRaises((tool.FamilyMediaError, tool.media_base.MediaUploadError)):
                admin.verify_public_product("stub_flange", source_urls)
            self.assertEqual(page.listeners, {})

    def test_gallery_adapter_rejects_any_nonexact_id_set_before_write(self):
        vault = {"declared_permissions": "read_write"}
        expected = self.product_evidence_for()
        with mock.patch.object(tool.wc, "api_request") as request:
            for invalid in ([1, 2, 3], [1, 2, 3, 3], [1, 2, 3, 0], [1, 2, 3, "4"]):
                with self.subTest(invalid=invalid), self.assertRaises(tool.FamilyMediaError):
                    tool.assign_fixed_gallery("stub_flange", invalid, vault, expected)
        request.assert_not_called()

    def test_gallery_adapter_performs_its_own_fresh_get_and_refuses_drift_before_put(self):
        expected = self.product_evidence_for()
        drifted = self.product_record()
        drifted["date_modified_gmt"] = "2026-08-15T10:30:00"
        with mock.patch.object(tool.wc, "api_get", return_value=(drifted, {})) as api_get, \
                mock.patch.object(tool.wc, "api_request") as request:
            with self.assertRaises(tool.FamilyMediaError):
                tool.assign_fixed_gallery("stub_flange", [1, 2, 3, 4], {}, expected)
        api_get.assert_called_once()
        request.assert_not_called()

    def test_wrong_approval_refuses_before_vault_browser_or_network(self):
        path, _ = self.make_plan()
        with mock.patch.object(tool.wc, "load_vault") as vault, \
                mock.patch.object(tool, "ui_browser_lock") as lane, \
                mock.patch.object(tool.wc, "api_get") as api_get, \
                mock.patch.object(tool, "_registry_key") as registry_key:
            with self.assertRaises(tool.FamilyMediaError):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="approved"))
        vault.assert_not_called()
        lane.assert_not_called()
        api_get.assert_not_called()
        registry_key.assert_not_called()
        self.assertFalse(tool.lock_path(self.operation_sha(path)).exists())

    def test_wrong_origin_refuses_before_browser_or_network(self):
        path, _ = self.make_plan()
        with mock.patch.object(tool.wc, "load_vault", return_value={
                "declared_permissions": "read_write", "site_url": "https://example.com"}), \
                mock.patch.object(tool, "ui_browser_lock") as lane, \
                mock.patch.object(tool.wc, "api_get") as api_get:
            with self.assertRaises((tool.FamilyMediaError, tool.wc.WooError)):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        lane.assert_not_called()
        api_get.assert_not_called()
        self.assertFalse(tool.lock_path(self.operation_sha(path)).exists())

    def test_product_drift_refuses_before_upload_and_lock(self):
        path, _ = self.make_plan()
        drift = deepcopy(json.loads(path.read_text(encoding="utf-8"))["product_before"])
        drift["date_modified_gmt"] = "changed"
        with mock.patch.object(tool.wc, "load_vault", return_value={
                "declared_permissions": "read_write", "site_url": tool.EXACT_ORIGIN}), \
                mock.patch.object(tool, "ui_browser_lock",
                                  side_effect=lambda *a, **k: contextlib.nullcontext()), \
                mock.patch.object(tool, "product_evidence", return_value=drift), \
                mock.patch.object(tool, "admin_session") as admin:
            with self.assertRaises(tool.FamilyMediaError):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        admin.assert_not_called()
        self.assertFalse(tool.lock_path(self.operation_sha(path)).exists())

    def test_success_uploads_four_then_one_images_only_put(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        patches, state = self.commit_patches(key, admin)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(admin.guard_acquire_count, 1)
        self.assertEqual(admin.upload_count, 4)
        self.assertEqual(admin.guard_complete_count, 1)
        self.assertEqual(admin.guard_snapshot_count, 2)
        self.assertEqual(state["get_calls"], 5)
        self.assertEqual(len(state["request_calls"]), 1)
        method, endpoint, payload = state["request_calls"][0]
        self.assertEqual((method, endpoint), ("PUT", "/products/1368"))
        self.assertEqual(set(payload), {"images"})
        self.assertEqual([row["id"] for row in payload["images"]], [9001, 9002, 9003, 9004])
        self.assertTrue(all(set(row) == {"id"} for row in payload["images"]))
        self.assertEqual(
            state["if_match"], tool.gallery_precondition(tool.safe_gallery(self.product_record(key)))
        )
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["public_verification"]["javascript_errors"], 0)
        self.assertTrue(result["protected_product_fields_unchanged"])
        self.assertFalse(result["guard_active_after_verification"])
        self.assertEqual(result["guard_acquisition"]["mode"], "guard_acquired")
        self.assertEqual(result["guard_owner_snapshot"]["reserved_uploads"], 0)
        self.assertEqual(result["guarded_snapshot"]["reserved_uploads"], 4)
        self.assertEqual(result["guard_completion"]["proof"]["mode"], "guard_completed")
        self.assertTrue(result["post_completion_product_verified"])
        self.assertEqual(result["emails"], 0)
        self.assertFalse(result["delete_performed"])
        attempt = json.loads(tool.lock_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(attempt["status"], "ATTEMPT_STARTED")
        self.assertFalse(tool.reservation_path(self.operation_sha(path)).exists())
        self.assertTrue(tool.journal_path(self.operation_sha(path), "900_committed_verified").is_file())

    def test_server_side_gallery_precondition_failure_cannot_be_silently_overwritten(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        patches, state = self.commit_patches(key, admin, gallery_race=True)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(len(state["request_calls"]), 1)
        self.assertRegex(state["if_match"], r'^"[0-9a-f]{64}"$')
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "gallery_put_or_readback")
        self.assertTrue(result["product_may_have_changed"])
        self.assertTrue(result["no_retry"])

    def test_guard_acquisition_failure_locks_before_any_upload_or_gallery_put(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key, fail_guard_acquire=True)
        patches, state = self.commit_patches(key, admin)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(admin.guard_acquire_count, 1)
        self.assertEqual(admin.upload_count, 0)
        self.assertEqual(state["request_calls"], [])
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "guard_acquisition")
        self.assertTrue(result["guard_may_be_active"])
        self.assertTrue(result["no_retry"])

    def test_missing_post_acquire_owner_proof_locks_before_any_upload(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        admin.guarded_snapshot = mock.Mock(side_effect=RuntimeError("owner cookie unavailable"))
        patches, state = self.commit_patches(key, admin)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(admin.guard_acquire_count, 1)
        self.assertEqual(admin.upload_count, 0)
        self.assertEqual(state["request_calls"], [])
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "guard_owner_snapshot")
        self.assertTrue(result["guard_may_be_active"])
        self.assertIsNone(result["guard_owner_snapshot"])

    def test_guarded_snapshot_mismatch_locks_before_gallery_put(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        bad = admin._snapshot("guarded_snapshot", True, 4, 4)
        bad["fixed_matches"][0]["attachment_id"] = 9999
        original_guarded_snapshot = admin.guarded_snapshot
        admin.guarded_snapshot = lambda _key: (
            original_guarded_snapshot(_key) if admin.upload_count == 0 else bad
        )
        patches, state = self.commit_patches(key, admin)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(admin.upload_count, 4)
        self.assertEqual(state["request_calls"], [])
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "guarded_snapshot")
        self.assertTrue(result["guard_may_be_active"])

    def test_near_expiry_guard_refuses_after_upload_proofs_and_before_gallery_put(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        patches, state = self.commit_patches(key, admin)
        checks = {"count": 0}

        def margin(proof):
            checks["count"] += 1
            if checks["count"] == 3:
                raise tool.FamilyMediaError("modelled guard too close to expiry")

        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            stack.enter_context(mock.patch.object(
                tool, "require_guard_completion_margin", side_effect=margin
            ))
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(admin.upload_count, 4)
        self.assertEqual(state["request_calls"], [])
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "guard_completion_margin")
        self.assertTrue(result["guard_may_be_active"])

    def test_guard_completion_failure_after_gallery_put_is_indeterminate(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key, fail_guard_complete=True)
        patches, state = self.commit_patches(key, admin)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(admin.upload_count, 4)
        self.assertEqual(admin.guard_complete_count, 1)
        self.assertEqual(len(state["request_calls"]), 1)
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "guard_completion")
        self.assertTrue(result["product_may_have_changed"])
        self.assertTrue(result["guard_may_be_active"])

    def test_authorization_margin_is_checked_before_attempt_lock_or_guard_acquisition(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        patches, state = self.commit_patches(key, admin)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            stack.enter_context(mock.patch.object(
                tool, "require_authorization_margin",
                side_effect=tool.FamilyMediaError("too close"),
            ))
            with self.assertRaises(tool.FamilyMediaError):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(admin.guard_acquire_count, 0)
        self.assertEqual(admin.upload_count, 0)
        self.assertEqual(state["request_calls"], [])
        self.assertFalse(tool.lock_path(self.operation_sha(path)).exists())

    def test_replay_is_refused_without_new_write(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        tool.write_json(tool.lock_path(self.operation_sha(path)), {"status": "acting"}, exclusive=True)
        with mock.patch.object(tool.wc, "load_vault") as vault:
            with self.assertRaises(tool.FamilyMediaError):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        vault.assert_not_called()

    def test_upload_two_failure_leaves_one_and_never_puts_gallery(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key, fail_upload_at=2)
        patches, state = self.commit_patches(key, admin)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(admin.upload_count, 2)
        self.assertEqual(state["request_calls"], [])
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "INDETERMINATE_NO_RETRY")
        self.assertEqual(len(result["uploaded_verified"]), 1)
        self.assertFalse(result["product_may_have_changed"])
        self.assertTrue(result["media_may_have_changed"])
        self.assertTrue(result["current_upload_may_have_landed"])
        self.assertEqual(result["current_upload"]["position"], 2)
        self.assertTrue(result["no_retry"])

    def test_gallery_put_failure_locks_after_four_verified_uploads(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        patches, state = self.commit_patches(key, admin, request_failure=True)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(admin.upload_count, 4)
        self.assertEqual(len(state["request_calls"]), 1)
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertTrue(result["product_may_have_changed"])
        self.assertEqual(len(result["uploaded_verified"]), 4)

    def test_product_drift_after_uploads_refuses_before_gallery_put(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        patches, state = self.commit_patches(key, admin, pre_put_drift=True)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(admin.upload_count, 4)
        self.assertEqual(state["request_calls"], [])
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "pre_gallery_revalidation")
        self.assertFalse(result["product_may_have_changed"])

    def test_public_verification_failure_after_put_is_indeterminate(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key, fail_public=True)
        patches, state = self.commit_patches(key, admin)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(len(state["request_calls"]), 1)
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "post_completion_final_verification")
        self.assertTrue(result["product_may_have_changed"])
        self.assertFalse(result["guard_may_be_active"])
        self.assertEqual(result["guard_completion"]["proof"]["mode"], "guard_completed")

    def test_post_completion_product_race_is_detected_with_guard_inactive(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        patches, state = self.commit_patches(
            key, admin, post_completion_drift=True
        )
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(len(state["request_calls"]), 1)
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "post_completion_final_verification")
        self.assertTrue(result["product_may_have_changed"])
        self.assertFalse(result["guard_may_be_active"])
        self.assertEqual(result["guard_completion"]["proof"]["mode"], "guard_completed")

    def test_wrong_put_response_id_is_indeterminate(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        patches, _ = self.commit_patches(key, admin, wrong_response_id=True)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertTrue(result["product_may_have_changed"])

    def test_protected_field_drift_after_put_is_indeterminate(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        patches, _ = self.commit_patches(key, admin, protected_drift=True)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "gallery_put_or_readback")
        self.assertTrue(result["guard_may_be_active"])

    def test_plugin_metadata_drift_after_put_is_indeterminate(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        patches, _ = self.commit_patches(key, admin, metadata_drift=True)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertTrue(result["product_may_have_changed"])

    def test_preexisting_result_refuses_before_vault_browser_or_write(self):
        path, _ = self.make_plan()
        operation_sha = self.operation_sha(path)
        tool.write_json(tool.result_path(operation_sha), {"planted": True}, exclusive=True)
        with mock.patch.object(tool.wc, "load_vault") as vault, \
                mock.patch.object(tool, "ui_browser_lock") as lane, \
                mock.patch.object(tool.wc, "api_request") as request:
            with self.assertRaises(tool.FamilyMediaError):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        vault.assert_not_called()
        lane.assert_not_called()
        request.assert_not_called()

    def test_post_lock_journal_failure_is_indeterminate_and_no_put(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        patches, state = self.commit_patches(key, admin)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            stack.enter_context(mock.patch.object(tool, "record_event", side_effect=OSError("disk")))
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(admin.guard_acquire_count, 1)
        self.assertEqual(admin.upload_count, 0)
        self.assertEqual(state["request_calls"], [])
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "INDETERMINATE_NO_RETRY")
        self.assertTrue(result["guard_may_be_active"])
        self.assertIn("journal:OSError", result["evidence_write_failures"])
        self.assertTrue(tool.lock_path(self.operation_sha(path)).is_file())

    def test_attempt_marker_write_then_raise_is_permanent_indeterminate_before_upload(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        patches, state = self.commit_patches(key, admin)
        real_write_json = tool.write_json
        attempt_path = tool.lock_path(self.operation_sha(path))

        def write_then_raise(target, value, *, exclusive=False):
            result = real_write_json(target, value, exclusive=exclusive)
            if Path(target) == attempt_path:
                raise OSError("modelled post-create fsync/close failure")
            return result

        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            stack.enter_context(mock.patch.object(tool, "write_json", side_effect=write_then_raise))
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertTrue(attempt_path.is_file())
        self.assertEqual(admin.upload_count, 0)
        self.assertEqual(state["request_calls"], [])
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "INDETERMINATE_NO_RETRY")
        self.assertEqual(result["stage"], "attempt_marker")

    def test_success_receipt_failure_becomes_indeterminate_no_retry(self):
        key = "stub_flange"
        path, _ = self.make_plan(key)
        admin = FakeAdmin(key)
        patches, state = self.commit_patches(key, admin)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            stack.enter_context(mock.patch.object(tool, "append_receipt", side_effect=OSError("receipt")))
            with self.assertRaises(tool.FamilyMediaIndeterminate):
                tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(admin.upload_count, 4)
        self.assertEqual(len(state["request_calls"]), 1)
        result = json.loads(tool.result_path(self.operation_sha(path)).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "INDETERMINATE_NO_RETRY")
        self.assertIn("receipt:OSError", result["evidence_write_failures"])

    def test_stage_all_writes_five_independent_plans_and_no_api_write(self):
        products = {key: self.product_evidence_for(key) for key in tool.FAMILY_KEYS}
        admin = FakeAdmin("stub_flange")
        duplicate = self.duplicate_ok(sorted(tool.FAMILY_KEYS))
        with mock.patch.object(tool.wc, "load_vault", return_value={}), \
                mock.patch.object(tool, "product_evidence",
                                  side_effect=lambda key, vault=None: products[key]), \
                mock.patch.object(tool, "admin_session",
                                  side_effect=lambda allowed: contextlib.nullcontext(admin)), \
                mock.patch.object(tool, "duplicate_scan", return_value=duplicate), \
                mock.patch.object(tool, "ui_browser_lock",
                                  side_effect=lambda *a, **k: contextlib.nullcontext()), \
                mock.patch.object(tool.wc, "api_request") as api_request, \
                mock.patch.object(tool, "emit"):
            tool.command_stage_all(argparse.Namespace())
        api_request.assert_not_called()
        plans = list(self.plan_dir.glob("*.json"))
        self.assertEqual(len(plans), 5)
        self.assertEqual({tool.load_plan(path)["family"] for path in plans}, set(tool.FAMILY_KEYS))

    def test_stage_one_has_zero_website_write_call(self):
        key = "stub_flange"
        product = json.loads(self.make_plan(key)[0].read_text(encoding="utf-8"))["product_before"]
        admin = FakeAdmin(key)
        with mock.patch.object(tool.wc, "load_vault", return_value={}), \
                mock.patch.object(tool, "product_evidence", return_value=product), \
                mock.patch.object(tool, "admin_session",
                                  side_effect=lambda allowed: contextlib.nullcontext(admin)), \
                mock.patch.object(tool, "duplicate_scan", return_value=self.duplicate_ok([key])), \
                mock.patch.object(tool, "ui_browser_lock",
                                  side_effect=lambda *a, **k: contextlib.nullcontext()), \
                mock.patch.object(tool.wc, "api_request") as api_request, \
                mock.patch.object(tool, "emit"):
            tool.command_stage(argparse.Namespace(family=key))
        api_request.assert_not_called()

    def test_plan_records_non_atomic_no_rollback_risk(self):
        path, _ = self.make_plan()
        plan = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("NOT ATOMIC", plan["risk"])
        self.assertIn("NO ROLLBACK", plan["risk"])
        self.assertIn("delete", plan["forbidden"])
        self.assertIn("FNPT", plan["forbidden"])
        self.assertIn("Replaced gallery attachments remain in the Media Library", plan["risk"])

    def test_source_has_no_arbitrary_input_image_or_product_argument(self):
        parser = tool.build_parser()
        choices = next(action for action in parser._actions
                       if isinstance(action, argparse._SubParsersAction)).choices
        stage_options = {option for action in choices["stage"]._actions
                         for option in action.option_strings}
        commit_options = {option for action in choices["commit"]._actions
                          for option in action.option_strings}
        self.assertEqual(stage_options, {"-h", "--help", "--family"})
        self.assertEqual(commit_options, {"-h", "--help", "--plan", "--approval"})

    def test_main_scrubs_refusal_and_returns_one(self):
        with mock.patch.object(sys, "argv", [SOURCE_PATH.name, "stage", "--family", "pipe"]), \
                mock.patch.object(tool, "command_stage", side_effect=tool.FamilyMediaError("refused")), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            # Parser stored the original callable during construction, so patch family validation instead.
            with mock.patch.object(tool, "validate_local_images",
                                   side_effect=tool.FamilyMediaError("refused")):
                result = tool.main()
        self.assertEqual(result, 1)
        self.assertIn("ERROR: refused", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
