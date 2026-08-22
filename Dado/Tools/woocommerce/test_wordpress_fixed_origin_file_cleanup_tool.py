from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from Dado.Tools.woocommerce import wordpress_fixed_origin_file_cleanup_tool as tool


class FakeControl:
    def __init__(self) -> None:
        self.files = []
        self.clicks = 0

    def set_input_files(self, value, timeout=None):
        self.files.append((value, timeout))

    def click(self, timeout=None):
        self.clicks += 1


class FakePage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.waits = []

    def wait_for_load_state(self, state, timeout=None):
        self.waits.append((state, timeout))

    def query_selector_all(self, selector):
        return []


class LocalStateMixin:
    def local_paths(self):
        temporary = tempfile.TemporaryDirectory(prefix="ffoc-python-test-")
        root = Path(temporary.name)
        values = {
            "PLAN_DIR": root / "plans",
            "LOCAL_STATE": root / "state",
            "REGISTRY_KEY": root / "state" / "registry.key",
            "REGISTRY_DIR": root / "state" / "stages",
            "ATTEMPT_DIR": root / "state" / "attempts",
            "RESULT_DIR": root / "state" / "results",
            "EVENT_DIR": root / "state" / "events",
            "RECEIPTS": root / "receipts.jsonl",
        }
        return temporary, patch.multiple(tool, **values), values


class FixedContractTests(unittest.TestCase):
    def valid_state(self, phase="preinstall"):
        present = phase in {"preinstall", "preactivate"}
        public = []
        records = []
        for target in tool.TARGETS:
            public.append({
                "position": target["position"],
                "upload_path": target["upload_path"],
                "url": target["url"],
                "bytes": target["bytes"],
                "sha256": target["sha256"],
                "status": 200 if present else 404,
                "redirected": False,
                "body_bytes": target["bytes"] if present else None,
                "body_sha256": target["sha256"] if present else None,
            })
            records.append({"attachment_id": target["attachment_id"],
                            "status": 404, "redirected": False})
        return {"plugin": tool.expected_plugin_for_phase(phase),
                "public_files": public, "attachment_records": records,
                "media_catalog": {
                    "total": 83,
                    "enumerated": 83,
                    "pages": 2,
                    "rows_sha256": "a" * 64,
                    "target_conflicts": [],
                }}

    def test_exact_four_target_contract(self):
        self.assertEqual((5521, 5523, 5525, 5527), tool.TARGET_IDS)
        self.assertEqual([
            "wp-content/uploads/2026/08/01_manway_real_hero.png",
            "wp-content/uploads/2026/08/02_manway_real_alternate.png",
            "wp-content/uploads/2026/08/03_manway_real_laminate_detail.png",
            "wp-content/uploads/2026/08/04_manway_real_bore_flange_detail.png",
        ], [row["upload_path"] for row in tool.TARGETS])
        self.assertEqual([261492, 366491, 301011, 416461],
                         [row["bytes"] for row in tool.TARGETS])
        self.assertEqual([
            "db886ee83d211d755ffc5e095b3546351f9b01478be73d1a71c5b299a1643be6",
            "07d1678e976152a5fdc8ccdc0396a43a92e0055125fffc587508b354c747484b",
            "572741ffd433acbc8b2bd36dbd9cb2afe02dbd8b6346978c38a7c0d4f8a352d9",
            "c5742b9ee84370d2ed6034d891955ff1a7774e89c1f1ad1ffd5b2b5d14bfd753",
        ], [row["sha256"] for row in tool.TARGETS])

    def test_single_predicate_accepts_each_fixed_phase(self):
        for phase in sorted(tool.PHASES):
            state = self.valid_state(phase)
            self.assertIs(state, tool.assert_cleanup_eligible(state, phase))

    def test_predicate_rejects_every_target_mutation(self):
        for index in range(4):
            state = self.valid_state()
            state["public_files"][index]["sha256"] = "0" * 64
            with self.assertRaises(tool.CleanupError):
                tool.assert_cleanup_eligible(state, "preinstall")
            state = self.valid_state()
            state["public_files"][index]["body_bytes"] += 1
            with self.assertRaises(tool.CleanupError):
                tool.assert_cleanup_eligible(state, "preinstall")
            state = self.valid_state()
            state["attachment_records"][index]["status"] = 200
            with self.assertRaises(tool.CleanupError):
                tool.assert_cleanup_eligible(state, "preinstall")

    def test_after_phases_require_404_or_410_and_no_body(self):
        state = self.valid_state("predelete")
        state["public_files"][0]["status"] = 410
        tool.assert_cleanup_eligible(state, "predelete")
        state["public_files"][1]["status"] = 200
        with self.assertRaises(tool.CleanupError):
            tool.assert_cleanup_eligible(state, "predelete")

    def test_phase_specific_plugin_contract(self):
        for phase in tool.PHASES:
            wrong = self.valid_state(phase)
            wrong["plugin"] = tool.project_row(True, True, tool.PLUGIN_VERSION)
            with self.assertRaises(tool.CleanupError):
                tool.assert_cleanup_eligible(wrong, phase)

    def test_media_catalog_must_be_complete_and_target_free(self):
        mutations = (
            lambda catalog: catalog.update(enumerated=82),
            lambda catalog: catalog.update(pages=0),
            lambda catalog: catalog.update(rows_sha256="0" * 63),
            lambda catalog: catalog.update(target_conflicts=[{"id": 5521, "filename": "01_manway_real_hero.png"}]),
        )
        for mutate in mutations:
            state = self.valid_state()
            mutate(state["media_catalog"])
            with self.assertRaises(tool.CleanupError):
                tool.assert_cleanup_eligible(state, "preinstall")

    def test_authenticated_media_reader_requires_complete_snapshot_and_detects_targets(self):
        page = FakePage("https://frpdepots.com/wp-admin/upload.php?mode=list")
        admin = tool.AdminPage(page)
        rows = [
            {"id": 100, "filename": "ordinary.png", "stem": "ordinary"},
            {"id": 101, "filename": "another.jpg", "stem": "another"},
        ]
        admin._media_reader = Mock(return_value=None)
        admin._media_reader.enumerate_library.return_value = {
            "rows": rows, "total": 2, "pages": 1, "complete": True, "unidentified": 0,
        }
        observed = admin.read_media_catalog()
        self.assertEqual(2, observed["enumerated"])
        self.assertEqual([], observed["target_conflicts"])
        self.assertRegex(observed["rows_sha256"], r"^[0-9a-f]{64}$")

        admin._media_reader.enumerate_library.return_value = {
            "rows": rows, "total": 3, "pages": 1, "complete": False, "unidentified": 1,
        }
        with self.assertRaises(tool.CleanupError):
            admin.read_media_catalog()

        conflicting = list(rows) + [
            {"id": 5521, "filename": "different.png", "stem": "different"},
            {"id": 102, "filename": "01_manway_real_hero.png", "stem": "01_manway_real_hero"},
        ]
        admin._media_reader.enumerate_library.return_value = {
            "rows": conflicting, "total": 4, "pages": 1, "complete": True, "unidentified": 0,
        }
        observed = admin.read_media_catalog()
        self.assertEqual([102, 5521], [row["id"] for row in observed["target_conflicts"]])
        state = self.valid_state()
        state["media_catalog"] = observed
        with self.assertRaises(tool.CleanupError):
            tool.assert_cleanup_eligible(state, "preinstall")

    def test_sanitized_diagnostic_link_shape_retains_no_query_values(self):
        url = ("https://frpdepots.com/wp-admin/post.php?post=5521&action=edit"
               "&_wpnonce=TOP_SECRET_VALUE")
        shape = tool.sanitized_row_link_shape(url)
        encoded = json.dumps(shape, sort_keys=True)
        self.assertNotIn("TOP_SECRET_VALUE", encoded)
        self.assertEqual("/wp-admin/post.php", shape["path"])
        self.assertEqual(["_wpnonce", "action", "post"], shape["query_keys"])
        self.assertIsNone(shape["parsed_attachment_id"])
        self.assertEqual(5521, shape["post_id_hint"])
        self.assertTrue(shape["action_edit"])
        self.assertTrue(shape["same_origin"])

    def test_diagnostic_reader_records_bounded_issue_structure_without_values(self):
        class Link:
            def get_attribute(self, _name):
                return ("post.php?post=5521&_wpnonce=NEVER_PERSIST_THIS"
                        "&custom=customer-private-value")

        class Name:
            def inner_text(self):
                return "File name: ordinary.png"

        class Row:
            def get_attribute(self, name):
                return "post-5521" if name == "id" else None

            def query_selector_all(self, selector):
                if selector == tool.media_base.EMPTY_TABLE_CELL_SELECTOR:
                    return []
                if selector == tool.media_base.ROW_LINK_SELECTOR:
                    return [Link()]
                if selector == "a[href]":
                    return [Link()]
                return []

            def query_selector(self, selector):
                return Name() if selector == tool.media_base.ROW_FILENAME_SELECTOR else None

        class Page:
            url = "https://frpdepots.com/wp-admin/upload.php?mode=list&paged=3"

            def query_selector_all(self, selector):
                return [Row()] if selector == tool.media_base.LIST_ROW_SELECTOR else []

        reader = tool.DiagnosticMediaReader(Page())
        with patch.object(tool.media_base.AdminPage, "_row_records", return_value=[
                {"id": None, "filename": "ordinary.png", "stem": "ordinary"}]):
            reader._row_records()
        self.assertEqual(1, reader.issue_count)
        issue = reader.issue_rows[0]
        self.assertEqual(3, issue["page"])
        self.assertEqual("canonical_row_identity_unavailable_or_unmatched", issue["reason"])
        self.assertIsNone(issue["canonical_row_attachment_id"])
        self.assertEqual([], issue["exact_attachment_ids"])
        encoded = json.dumps(issue, sort_keys=True)
        self.assertNotIn("NEVER_PERSIST_THIS", encoded)
        self.assertNotIn("customer-private-value", encoded)
        self.assertNotIn("ordinary.png", encoded)
        self.assertIn(hashlib.sha256(b"ordinary.png").hexdigest(), encoded)

    def test_strict_cleanup_reader_uses_canonical_row_id_not_live_empty_primary_selector(self):
        class Link:
            def __init__(self, href):
                self.href = href

            def get_attribute(self, _name):
                return self.href

        media_edit = Link("https://frpdepots.com/wp-admin/post.php?post=6998&action=edit")
        parent_edit = Link("https://frpdepots.com/wp-admin/post.php?post=5000&action=edit")
        nonce_action = Link("post.php?post=6998&action=delete&_wpnonce=SECRET")

        class Name:
            def inner_text(self):
                return "File name: fixed.webp"

        class Row:
            def __init__(self, identity):
                self.identity = identity

            def get_attribute(self, name):
                return self.identity if name == "id" else None

            def query_selector_all(self, selector):
                if selector == "a[href]":
                    return [media_edit, parent_edit, nonce_action]
                if selector in (tool.media_base.ROW_LINK_SELECTOR,
                                tool.media_base.EMPTY_TABLE_CELL_SELECTOR):
                    return []
                return []

            def query_selector(self, selector):
                return Name() if selector == tool.media_base.ROW_FILENAME_SELECTOR else None

        class Page:
            def __init__(self, identity):
                self.identity = identity

            def query_selector_all(self, selector):
                return [Row(self.identity)] if selector == tool.media_base.LIST_ROW_SELECTOR else []

        accepted = tool.StrictCleanupMediaReader(Page("post-6998"))._row_records()
        self.assertEqual([{"id": 6998, "filename": "fixed.webp", "stem": "fixed"}], accepted)
        refused = tool.StrictCleanupMediaReader(Page("post-6999"))._row_records()
        self.assertEqual([{"id": None, "filename": "", "stem": ""}], refused)

    def test_strict_cleanup_reader_uses_one_exact_upload_link_when_filename_node_is_absent(self):
        class Link:
            def __init__(self, href):
                self.href = href

            def get_attribute(self, _name):
                return self.href

        edit = Link("https://frpdepots.com/wp-admin/post.php?post=7609&action=edit")
        upload = Link(
            "https://frpdepots.com/wp-content/uploads/2026/08/01_manway_premium_hero.png"
        )

        class Row:
            def __init__(self, extra_links=()):
                self.links = [edit, upload, *extra_links]

            def get_attribute(self, name):
                return "post-7609" if name == "id" else None

            def query_selector_all(self, selector):
                if selector == "a[href]":
                    return self.links
                if selector in (tool.media_base.ROW_LINK_SELECTOR,
                                tool.media_base.EMPTY_TABLE_CELL_SELECTOR):
                    return []
                return []

            def query_selector(self, _selector):
                return None

        class Page:
            def __init__(self, row):
                self.row = row

            def query_selector_all(self, selector):
                return [self.row] if selector == tool.media_base.LIST_ROW_SELECTOR else []

        accepted = tool.StrictCleanupMediaReader(Page(Row()))._row_records()
        self.assertEqual([{
            "id": 7609,
            "filename": "01_manway_premium_hero.png",
            "stem": "01_manway_premium_hero",
        }], accepted)

        for extra in (
                Link("https://frpdepots.com/wp-content/uploads/2026/08/other.png"),
                Link("https://other.example/wp-content/uploads/2026/08/other.png"),
                Link("https://frpdepots.com/wp-content/uploads/2026/08/other.png?token=secret"),
                Link("https://user:password@frpdepots.com/wp-content/uploads/2026/08/other.png")):
            refused = tool.StrictCleanupMediaReader(Page(Row([extra])))._row_records()
            if "other.example" in extra.href or "?token=" in extra.href or "user:password" in extra.href:
                self.assertEqual(accepted, refused)
            else:
                self.assertEqual([{"id": None, "filename": "", "stem": ""}], refused)

    def test_wordpress_703_row_identity_fixture_is_pinned_to_official_source(self):
        fixture_path = (Path(tool.__file__).parent / "testdata"
                        / "wordpress_7_0_3_media_list_row_identity_contract.json")
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        source_path = tool.ROOT / fixture["source"]["file"]
        source = source_path.read_bytes()
        self.assertEqual(fixture["source"]["sha256"], hashlib.sha256(source).hexdigest())
        lines = source.decode("utf-8").splitlines()
        self.assertIn('<tr id="post-<?php echo $post->ID; ?>"',
                      lines[fixture["source"]["official_row_line"] - 1])
        contract = fixture["reader_contract"]
        self.assertEqual(tool.media_base.LIST_ROW_SELECTOR, contract["list_row_selector"])
        self.assertEqual(tool.media_base.ROW_FILENAME_SELECTOR, contract["filename_selector"])
        self.assertEqual(tool.media_base.EMPTY_TABLE_CELL_SELECTOR,
                         contract["empty_placeholder_selector"])
        self.assertTrue(fixture["safety"]["canonical_row_id_must_equal_one_exact_edit_link_id"])

    def test_approval_is_exact_unpadded_uppercase(self):
        tool.require_approval("APPROVED")
        for value in ("approved", " APPROVED", "APPROVED ", "APPROVED\n", "APPROVE", ""):
            with self.assertRaises(tool.CleanupError):
                tool.require_approval(value)


class ArtifactTests(unittest.TestCase):
    def test_artifact_hash_bytes_members_and_manifest_are_pinned(self):
        artifact, raw = tool.validate_artifact_payload()
        self.assertEqual(tool.ARTIFACT_SHA256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(tool.ARTIFACT_BYTES, len(raw))
        self.assertEqual(tool.ARTIFACT_MEMBERS, tuple(artifact["members"]))
        with zipfile.ZipFile(tool.ARTIFACT_PATH) as archive:
            self.assertEqual(list(tool.ARTIFACT_MEMBERS), archive.namelist())
            for name in tool.ARTIFACT_MEMBERS:
                data = archive.read(name)
                self.assertEqual(tool.ARTIFACT_MEMBER_BYTES[name], len(data))
                self.assertEqual(tool.ARTIFACT_MEMBER_SHA256[name],
                                 hashlib.sha256(data).hexdigest())
                info = archive.getinfo(name)
                self.assertEqual((1980, 1, 1, 0, 0, 0), info.date_time)
                self.assertEqual(0o100644, info.external_attr >> 16)

    def test_builder_is_byte_reproducible(self):
        builder = tool.PACKAGE_DIR / "build_plugin_zip.py"
        first = subprocess.run([sys.executable, str(builder)], cwd=tool.ROOT,
                               check=True, capture_output=True, text=True)
        bytes_one = tool.ARTIFACT_PATH.read_bytes()
        manifest_one = tool.ARTIFACT_MANIFEST_PATH.read_bytes()
        second = subprocess.run([sys.executable, str(builder)], cwd=tool.ROOT,
                                check=True, capture_output=True, text=True)
        self.assertEqual(bytes_one, tool.ARTIFACT_PATH.read_bytes())
        self.assertEqual(manifest_one, tool.ARTIFACT_MANIFEST_PATH.read_bytes())
        self.assertEqual(tool.ARTIFACT_SHA256, hashlib.sha256(bytes_one).hexdigest())
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))

    def test_artifact_validator_rejects_byte_change(self):
        raw = tool.ARTIFACT_PATH.read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            changed = Path(folder) / tool.ARTIFACT_PATH.name
            changed.write_bytes(raw + b"x")
            with patch.object(tool, "ARTIFACT_PATH", changed):
                with self.assertRaises(tool.CleanupError):
                    tool.validate_artifact_payload()

    def test_php_syntax_and_offline_activation_harness(self):
        php = shutil.which("php")
        if php is None:
            self.skipTest("php CLI unavailable")
        source = tool.PACKAGE_DIR / tool.PLUGIN_SLUG / f"{tool.PLUGIN_SLUG}.php"
        lint = subprocess.run([php, "-l", str(source)], cwd=tool.ROOT,
                              check=True, capture_output=True, text=True)
        self.assertIn("No syntax errors detected", lint.stdout)
        harness = subprocess.run([php, str(tool.PACKAGE_DIR / "test_fixed_origin_file_cleanup.php")],
                                 cwd=tool.ROOT, check=True, capture_output=True, text=True)
        self.assertRegex(harness.stdout.strip(), r"^PASS [0-9]+$")

    def test_plugin_has_no_public_admin_or_generic_mutation_route(self):
        source = (tool.PACKAGE_DIR / tool.PLUGIN_SLUG / f"{tool.PLUGIN_SLUG}.php").read_text(
            encoding="utf-8")
        forbidden = (
            "admin_post_", "wp_ajax_", "rest_api_init", "register_rest_route", "wp_remote_post",
            "wp_mail(", "update_option(", "add_option(", "delete_option(", "wp_insert_post(",
            "wp_delete_post(", "update_post_meta(", "delete_post_meta(", "woocommerce_", "/wc/v3",
            "$_GET", "$_POST", "$_REQUEST", "glob(", "eval(", "exec(", "shell_exec",
        )
        for token in forbidden:
            self.assertNotIn(token.lower(), source.lower(), token)
        self.assertEqual(1, source.count("register_activation_hook("))
        self.assertEqual(1, source.count("add_action("))
        self.assertIn("add_action( 'shutdown'", source)
        self.assertIn("return unlink( $path );", source)
        self.assertNotIn("unlink( $_", source)


class PlanTests(LocalStateMixin, FixedContractTests):
    def test_stage_and_load_create_authenticated_immutable_24h_plan(self):
        temporary, patched, values = self.local_paths()
        with temporary, patched:
            artifact = tool.validate_artifact()
            before = self.valid_state()
            path, plan = tool.stage_plan(artifact, before)
            self.assertTrue(path.is_file())
            self.assertEqual(timedelta(hours=24),
                tool.datetime.fromisoformat(plan["expires_utc"])
                - tool.datetime.fromisoformat(plan["created_utc"]))
            loaded_path, loaded = tool.load_plan(str(path))
            self.assertEqual(path, loaded_path)
            self.assertEqual(plan, loaded)
            self.assertTrue((values["REGISTRY_DIR"] / f'{plan["sha256"]}.json').is_file())
            self.assertIn("NOT ATOMIC", plan["risk"])
            self.assertIn("ONE ATTEMPT, NO RETRY, NO ROLLBACK", plan["risk"])
            self.assertIn("partial file deletion is possible", plan["risk"])
            self.assertIn("plugin may remain installed/inactive", plan["risk"])
            self.assertIn("There is no cleanup after failure", plan["risk"])

    def test_tampered_plan_and_unregistered_plan_refuse(self):
        temporary, patched, values = self.local_paths()
        with temporary, patched:
            path, plan = tool.stage_plan(tool.validate_artifact(), self.valid_state())
            raw = json.loads(path.read_text(encoding="ascii"))
            raw["risk"] = "atomic"
            path.write_text(json.dumps(raw), encoding="ascii")
            with self.assertRaises(tool.CleanupError):
                tool.load_plan(str(path))

            path2, plan2 = tool.stage_plan(tool.validate_artifact(), self.valid_state())
            (values["REGISTRY_DIR"] / f'{plan2["sha256"]}.json').unlink()
            with self.assertRaises((tool.CleanupError, FileNotFoundError)):
                tool.load_plan(str(path2))

    def test_expiry_and_stale_tool_version_refuse_closed(self):
        temporary, patched, _values = self.local_paths()
        with temporary, patched:
            path, plan = tool.stage_plan(tool.validate_artifact(), self.valid_state())
            expired_at = tool.datetime.fromisoformat(plan["expires_utc"]) + timedelta(seconds=1)
            with patch.object(tool, "utc_now", return_value=expired_at):
                with self.assertRaises(tool.CleanupError):
                    tool.load_plan(str(path))
            with patch.object(tool, "TOOL_VERSION", "9.9.9"):
                with self.assertRaises(tool.CleanupError):
                    tool.load_plan(str(path))

    def test_command_stage_is_read_only_on_website_and_creates_no_attempt(self):
        temporary, patched, values = self.local_paths()
        with temporary, patched:
            state = self.valid_state("preinstall")

            @contextmanager
            def session(_purpose):
                yield object()

            output = io.StringIO()
            with patch.object(tool, "admin_session", session), \
                 patch.object(tool, "collect_state", return_value=state), \
                 patch("sys.stdout", new=output):
                tool.command_stage(SimpleNamespace())
            report = json.loads(output.getvalue())
            self.assertEqual("STAGED_READ_ONLY", report["status"])
            self.assertEqual(0, report["website_writes"])
            self.assertEqual(0, report["emails"])
            self.assertEqual([], list(values["ATTEMPT_DIR"].glob("*.json")))
            self.assertEqual([], list(values["RESULT_DIR"].glob("*.json")))
            self.assertEqual([], list(values["EVENT_DIR"].glob("**/*.json")))
            self.assertEqual(1, len(list(values["PLAN_DIR"].glob("*.json"))))

    def test_diagnostic_command_is_read_only_and_creates_no_local_lifecycle_artifact(self):
        temporary, patched, values = self.local_paths()
        with temporary, patched:
            diagnostic = {
                "snapshot": {"complete": False, "total": 83, "pages": 2,
                             "identified_rows": 82, "unidentified_rows": 1,
                             "identified_rows_sha256": "a" * 64},
                "total_reads": [{"page": 1, "total": 83}],
                "page_metrics": [], "issue_count": 1, "issue_rows": [],
                "issues_truncated": True,
            }

            class FakeAdmin:
                def read_media_catalog_diagnostic(self):
                    return diagnostic

            @contextmanager
            def session(_purpose):
                yield FakeAdmin()

            output = io.StringIO()
            with patch.object(tool, "admin_session", session), patch("sys.stdout", new=output):
                tool.command_diagnose_library(SimpleNamespace())
            report = json.loads(output.getvalue())
            self.assertEqual("READ_ONLY_MEDIA_LIBRARY_DIAGNOSTIC", report["status"])
            self.assertEqual(diagnostic, report["diagnostic"])
            self.assertFalse(report["plan_created"])
            self.assertFalse(report["attempt_lock_created"])
            self.assertEqual(0, report["website_writes"])
            self.assertEqual(0, report["origin_files_deleted"])
            self.assertFalse(report["plugin_installed"])
            for key in ("PLAN_DIR", "ATTEMPT_DIR", "RESULT_DIR", "EVENT_DIR"):
                self.assertFalse(values[key].exists())

    def test_operation_identity_is_fixed_and_state_independent(self):
        first = tool.operation_sha()
        second = tool.operation_sha()
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_stage_uses_same_predicate_as_first_write_adapter(self):
        temporary, patched, _values = self.local_paths()
        with temporary, patched:
            good = self.valid_state("preinstall")
            artifact, raw = tool.validate_artifact_payload()
            path, _plan = tool.stage_plan(artifact, good)
            self.assertTrue(path.is_file())

            chooser, submit = FakeControl(), FakeControl()
            page = FakePage("https://frpdepots.com/wp-admin/update.php?action=upload-plugin")
            admin = tool.AdminPage(page)
            admin.read_bounded = Mock(return_value=tool.expected_plugin_for_phase("preactivate"))
            observed = admin.execute_install(chooser, submit, raw, good)
            self.assertEqual(tool.expected_plugin_for_phase("preactivate"), observed)
            self.assertEqual(1, len(chooser.files))
            self.assertEqual(1, submit.clicks)

            bad = self.valid_state("preinstall")
            bad["public_files"][3]["body_sha256"] = "0" * 64
            with self.assertRaises(tool.CleanupError):
                tool.stage_plan(artifact, bad)
            chooser2, submit2 = FakeControl(), FakeControl()
            with self.assertRaises(tool.CleanupError):
                admin.execute_install(chooser2, submit2, raw, bad)
            self.assertEqual([], chooser2.files)
            self.assertEqual(0, submit2.clicks)

    def test_activation_and_delete_adapters_call_same_phase_predicate_before_click(self):
        activation_state = self.valid_state("preactivate")
        link = FakeControl()
        activation_page = FakePage(
            "https://frpdepots.com/wp-admin/plugins.php?activate=true&plugin_status=all&paged=1&s="
            "&frpd_ffoc_result=deleted-4-self-deactivation-scheduled&frpd_ffoc_count=4")
        admin = tool.AdminPage(activation_page)
        admin.read_bounded = Mock(return_value=tool.expected_plugin_for_phase("predelete"))
        admin.execute_activation(link, activation_state)
        self.assertEqual(1, link.clicks)

        bad_activation = self.valid_state("preactivate")
        bad_activation["attachment_records"][0]["status"] = 200
        link2 = FakeControl()
        with self.assertRaises(tool.CleanupError):
            admin.execute_activation(link2, bad_activation)
        self.assertEqual(0, link2.clicks)

        delete_state = self.valid_state("predelete")
        submit = FakeControl()
        delete_page = FakePage(
            "https://frpdepots.com/wp-admin/plugins.php?deleted=true&plugin_status=all&paged=1&s=")
        delete_admin = tool.AdminPage(delete_page)
        delete_admin.goto_plugins = Mock()
        delete_admin.read_row = Mock(return_value=tool.expected_plugin_for_phase("final"))
        deleted = delete_admin.execute_delete(submit, delete_state)
        self.assertEqual(tool.expected_plugin_for_phase("final"), deleted)
        self.assertEqual(1, submit.clicks)

        bad_delete = self.valid_state("predelete")
        bad_delete["public_files"][0]["status"] = 200
        submit2 = FakeControl()
        with self.assertRaises(tool.CleanupError):
            delete_admin.execute_delete(submit2, bad_delete)
        self.assertEqual(0, submit2.clicks)


class RouteAndCapabilityTests(unittest.TestCase):
    def test_only_exact_admin_routes_are_accepted(self):
        tool.assert_admin_url(tool.PLUGINS_URL, "plugins")
        tool.assert_admin_url(tool.UPLOAD_URL, "upload")
        tool.assert_admin_url("https://frpdepots.com/wp-admin/update.php?action=upload-plugin",
                              "install_result")
        tool.assert_admin_url(
            "https://frpdepots.com/wp-admin/plugins.php?activate=true&plugin_status=all&paged=1&s="
            "&frpd_ffoc_result=deleted-4-self-deactivation-scheduled&frpd_ffoc_count=4",
            "activation_result")
        tool.assert_admin_url(
            "https://frpdepots.com/wp-admin/plugins.php?deleted=true&plugin_status=all&paged=1&s=",
            "delete_result")
        for url, mode in (
            ("http://frpdepots.com/wp-admin/plugins.php", "plugins"),
            ("https://evil.example/wp-admin/plugins.php", "plugins"),
            ("https://frpdepots.com/wp-admin/plugins.php?x=1", "plugins"),
            ("https://frpdepots.com/wp-admin/plugin-install.php?tab=search", "upload"),
            ("https://frpdepots.com/wp-admin/update.php?action=upload-theme", "install_result"),
        ):
            with self.assertRaises(tool.CleanupError):
                tool.assert_admin_url(url, mode)

    def test_activation_and_delete_urls_are_exactly_fixed_plugin(self):
        nonce = "abcDEF_123456"
        activation = ("plugins.php?action=activate&plugin=" + tool.PLUGIN_FILE
                      + "&plugin_status=all&paged=1&s=&_wpnonce=" + nonce)
        deletion = ("plugins.php?action=delete-selected&checked%5B%5D=" + tool.PLUGIN_FILE
                    + "&plugin_status=all&paged=1&s=&_wpnonce=" + nonce)
        tool.assert_state_action_url(activation, "activate")
        tool.assert_delete_action_url(deletion)
        tool.assert_delete_confirm_url(deletion)
        for changed in (activation.replace(tool.PLUGIN_FILE, "akismet/akismet.php"),
                        deletion.replace(tool.PLUGIN_FILE, "akismet/akismet.php"),
                        deletion + "&checked%5B%5D=akismet%2Fakismet.php"):
            with self.assertRaises(tool.CleanupError):
                if "action=activate" in changed:
                    tool.assert_state_action_url(changed, "activate")
                else:
                    tool.assert_delete_action_url(changed)

    def test_cli_exposes_no_generic_plugin_or_path_input(self):
        parser = tool.parser()
        with patch.object(sys, "stderr"):
            with self.assertRaises(SystemExit):
                parser.parse_args(["stage", "--zip", "anything.zip"])
            with self.assertRaises(SystemExit):
                parser.parse_args(["diagnose-library", "--path", "wp-content/uploads/other.png"])
            with self.assertRaises(SystemExit):
                parser.parse_args(["commit", "--plan", "x", "--approval", "APPROVED",
                                   "--path", "wp-content/uploads/other.png"])
        diagnostic = parser.parse_args(["diagnose-library"])
        self.assertEqual({"command", "func"}, set(vars(diagnostic)))
        parsed = parser.parse_args(["commit", "--plan", "x", "--approval", "APPROVED"])
        self.assertEqual({"command", "plan", "approval", "func"}, set(vars(parsed)))

    def test_python_tool_has_no_banned_business_capabilities(self):
        source = Path(tool.__file__).read_text(encoding="utf-8")
        forbidden = (
            "smtplib", "send_message", "create_draft", "wp-json/wc/", "/wc/v3/",
            "zoho_api", "zoho_client", "salesorder", "invoice_tool", "customer_tool",
            "payment_tool", "wp_delete_post", "wp_insert_post", "requests.post",
            "page.request.post", "context.request.post",
            "wordpress_plugin_deployment_tool", "wordpress_orphan_media_correction_tool",
        )
        lower = source.lower()
        for token in forbidden:
            self.assertNotIn(token.lower(), lower, token)
        self.assertNotIn("--zip", source)
        self.assertNotIn("--path", source)
        self.assertNotIn("--slug", source)
        self.assertIn("ui_browser_lock(\"wordpress\"", source)
        self.assertIn("credentials: 'omit'", source)
        self.assertIn("cache: 'no-store'", source)
        self.assertIn("'Cache-Control': 'no-cache'", source)


class CommitOrderingTests(LocalStateMixin, FixedContractTests):
    def _plan(self, artifact, before):
        now = tool.utc_now()
        return {
            "sha256": "a" * 64,
            "operation_sha256": tool.operation_sha(),
            "artifact": artifact,
            "before": before,
            "expires_utc": (now + timedelta(hours=1)).isoformat(),
        }

    def test_mutex_fresh_reads_attempt_lock_writes_and_delete_order(self):
        temporary, patched, values = self.local_paths()
        with temporary, patched:
            artifact, artifact_raw = tool.validate_artifact_payload()
            states = {
                phase: self.valid_state(phase)
                for phase in ("preinstall", "preactivate", "predelete", "final")
            }
            plan = self._plan(artifact, states["preinstall"])
            events = []
            active = {"mutex": False}

            class FakeAdmin:
                def prepare_install(self):
                    events.append("prepare_install")
                    return object(), object()
                def execute_install(self, chooser, submit, raw, state):
                    self_test.assertTrue(active["mutex"])
                    self_test.assertTrue((values["ATTEMPT_DIR"] / f'{plan["operation_sha256"]}.json').is_file())
                    events.append("execute_install")
                    return tool.expected_plugin_for_phase("preactivate")
                def prepare_activation(self, state):
                    events.append("prepare_activation")
                    return object()
                def execute_activation(self, link, state):
                    self_test.assertTrue((values["EVENT_DIR"] / plan["operation_sha256"] /
                                          "02-activation-attempted.json").is_file())
                    events.append("execute_activation")
                    return {"bounded_plugin_result": {"result": "ok"},
                            "after": tool.expected_plugin_for_phase("predelete")}
                def prepare_delete(self, state):
                    events.append("prepare_delete_confirmation")
                    return object()
                def execute_delete(self, submit, state):
                    self_test.assertTrue((values["EVENT_DIR"] / plan["operation_sha256"] /
                                          "03-plugin-delete-attempted.json").is_file())
                    events.append("execute_delete")
                    return tool.expected_plugin_for_phase("final")

            self_test = self
            admin = FakeAdmin()

            @contextmanager
            def session(_purpose):
                active["mutex"] = True
                events.append("mutex_enter")
                try:
                    yield admin
                finally:
                    events.append("mutex_exit")
                    active["mutex"] = False

            def collect(_admin, phase):
                self.assertTrue(active["mutex"])
                events.append("collect_" + phase)
                return states[phase]

            real_exclusive = tool.exclusive_json
            def recording_exclusive(path, value):
                if Path(path).parent == values["ATTEMPT_DIR"]:
                    self.assertTrue(active["mutex"])
                    events.append("attempt_lock")
                return real_exclusive(path, value)

            args = SimpleNamespace(plan="fixed.json", approval="APPROVED")
            with patch.object(tool, "load_plan", return_value=(Path("fixed.json"), plan)), \
                 patch.object(tool, "validate_artifact_payload", return_value=(artifact, artifact_raw)), \
                 patch.object(tool, "admin_session", session), \
                 patch.object(tool, "collect_state", side_effect=collect), \
                 patch.object(tool, "exclusive_json", side_effect=recording_exclusive), \
                 patch("sys.stdout", new=io.StringIO()):
                tool.command_commit(args)

            self.assertEqual([
                "mutex_enter", "collect_preinstall", "prepare_install", "attempt_lock",
                "execute_install", "collect_preactivate", "prepare_activation",
                "execute_activation", "collect_predelete", "prepare_delete_confirmation",
                "execute_delete", "collect_final", "mutex_exit",
            ], events)
            result_file = values["RESULT_DIR"] / f'{plan["operation_sha256"]}.json'
            result = json.loads(result_file.read_text(encoding="ascii"))
            self.assertEqual("COMMITTED_AND_VERIFIED", result["status"])
            self.assertEqual(3, result["browser_write_attempts"])
            self.assertEqual(4, result["origin_files_deleted"])
            self.assertTrue(result["plugin_deleted"])

    def test_failure_after_activation_is_indeterminate_no_retry_and_no_delete(self):
        temporary, patched, values = self.local_paths()
        with temporary, patched:
            artifact, raw = tool.validate_artifact_payload()
            preinstall = self.valid_state("preinstall")
            preactivate = self.valid_state("preactivate")
            plan = self._plan(artifact, preinstall)
            events = []

            class FakeAdmin:
                def prepare_install(self): return object(), object()
                def execute_install(self, *args):
                    events.append("install")
                    return tool.expected_plugin_for_phase("preactivate")
                def prepare_activation(self, state): return object()
                def execute_activation(self, *args):
                    events.append("activate")
                    return {"bounded_plugin_result": {}, "after": tool.expected_plugin_for_phase("predelete")}
                def prepare_delete(self, state):
                    events.append("prepare_delete")
                    return object()
                def execute_delete(self, *args):
                    events.append("delete")

            @contextmanager
            def session(_purpose):
                yield FakeAdmin()

            reads = iter([preinstall, preactivate])
            def collect(_admin, phase):
                if phase == "predelete":
                    raise tool.CleanupError("fixed post-activation read failed")
                return next(reads)

            args = SimpleNamespace(plan="fixed.json", approval="APPROVED")
            with patch.object(tool, "load_plan", return_value=(Path("fixed.json"), plan)), \
                 patch.object(tool, "validate_artifact_payload", return_value=(artifact, raw)), \
                 patch.object(tool, "admin_session", session), \
                 patch.object(tool, "collect_state", side_effect=collect):
                with self.assertRaises(tool.CleanupError):
                    tool.command_commit(args)

            self.assertEqual(["install", "activate"], events)
            failure = json.loads((values["RESULT_DIR"] / f'{plan["operation_sha256"]}.json').read_text())
            self.assertEqual("INDETERMINATE_NO_RETRY", failure["status"])
            self.assertTrue(failure["earlier_effects_remain"])
            self.assertTrue(failure["partial_origin_file_deletion_possible"])
            self.assertTrue(failure["plugin_may_remain_installed_inactive"])
            self.assertTrue((values["ATTEMPT_DIR"] / f'{plan["operation_sha256"]}.json').exists())

    def test_bad_approval_precedes_plan_load_and_any_session(self):
        args = SimpleNamespace(plan="fixed.json", approval="approved")
        with patch.object(tool, "load_plan") as load, patch.object(tool, "admin_session") as session:
            with self.assertRaises(tool.CleanupError):
                tool.command_commit(args)
        load.assert_not_called()
        session.assert_not_called()

    def test_browser_busy_refuses_before_attempt_lock(self):
        temporary, patched, values = self.local_paths()
        with temporary, patched:
            artifact, raw = tool.validate_artifact_payload()
            before = self.valid_state("preinstall")
            plan = self._plan(artifact, before)

            @contextmanager
            def busy_session(_purpose):
                raise tool.UiLaneBusy("WordPress browser lane is busy")
                yield  # pragma: no cover

            args = SimpleNamespace(plan="fixed.json", approval="APPROVED")
            with patch.object(tool, "load_plan", return_value=(Path("fixed.json"), plan)), \
                 patch.object(tool, "validate_artifact_payload", return_value=(artifact, raw)), \
                 patch.object(tool, "admin_session", busy_session):
                with self.assertRaises(tool.UiLaneBusy):
                    tool.command_commit(args)
            self.assertFalse((values["ATTEMPT_DIR"] / f'{plan["operation_sha256"]}.json').exists())
            self.assertFalse((values["RESULT_DIR"] / f'{plan["operation_sha256"]}.json').exists())

    def test_fresh_drift_refuses_before_attempt_lock_or_write(self):
        temporary, patched, values = self.local_paths()
        with temporary, patched:
            artifact, raw = tool.validate_artifact_payload()
            staged = self.valid_state("preinstall")
            fresh = self.valid_state("preinstall")
            fresh["public_files"][0]["body_sha256"] = "0" * 64
            plan = self._plan(artifact, staged)

            @contextmanager
            def session(_purpose):
                yield object()

            args = SimpleNamespace(plan="fixed.json", approval="APPROVED")
            with patch.object(tool, "load_plan", return_value=(Path("fixed.json"), plan)), \
                 patch.object(tool, "validate_artifact_payload", return_value=(artifact, raw)), \
                 patch.object(tool, "admin_session", session), \
                 patch.object(tool, "collect_state", return_value=fresh):
                with self.assertRaises(tool.CleanupError):
                    tool.command_commit(args)
            self.assertFalse((values["ATTEMPT_DIR"] / f'{plan["operation_sha256"]}.json').exists())
            self.assertFalse((values["RESULT_DIR"] / f'{plan["operation_sha256"]}.json').exists())


if __name__ == "__main__":
    unittest.main()
