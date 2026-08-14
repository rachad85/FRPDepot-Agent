"""Offline safety and behaviour tests for the exact Derakane v2 deployer.

No test opens CDP, a browser, a credential store, or the network. Stateful fixed-row
and anonymous-page doubles exercise staging, commit ordering, one-attempt closure,
complete readback, validation, and rollback behaviour.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
from datetime import timedelta, timezone
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import derakane_search_v2_deployment_tool as deploy  # noqa: E402


def baseline(**changes):
    core = {
        "page_status": 200, "blank": False, "fatal": False,
        "search_root_count": 1, "legacy_asset_count": 2,
        "v2_js_count": 0, "v2_css_count": 0,
        "dataset_sha256_count": 0, "source_document_sha256_count": 0,
        "unavailable_count": 0, "api_short_status": 404,
        "api_short_code": "rest_no_route",
    }
    core.update(changes)
    return {**core, "fingerprint": deploy.digest_for(core)}


class FakeAdmin:
    """Only the exact target row and fixed read-only legacy row exist."""

    def __init__(self):
        self.present = False
        self.active = None
        self.version = ""
        self.update_marker = False
        self.legacy_present = True
        self.legacy_active = True
        self.legacy_version = "1.1.0"
        self.legacy_update_marker = False
        self.uploads: list[str] = []
        self.clicks: list[str] = []
        self.fail_upload: Exception | None = None
        self.fail_activate: Exception | None = None
        self.activate_then_fail = False
        self.fail_deactivate: Exception | None = None
        self.write_probe = lambda: None
        self.events: list[str] = []

    def goto_plugins(self):
        self.events.append("goto_plugins")

    def read_target(self, allow_absent=False):
        if not self.present:
            if allow_absent:
                return deploy.project_row(deploy.PLUGIN_FILE, False, None, "", False)
            raise deploy.DeploymentError("target absent")
        return deploy.project_row(deploy.PLUGIN_FILE, True, self.active, self.version,
                                  self.update_marker)

    def read_legacy_conflict(self):
        if not self.legacy_present:
            return deploy.project_row(deploy.LEGACY_PLUGIN_FILE, False, None, "", False)
        return deploy.project_row(deploy.LEGACY_PLUGIN_FILE, True, self.legacy_active,
                                  self.legacy_version, self.legacy_update_marker)

    def upload_install_or_replace(self, artifact, existed_before):
        self.write_probe()
        self.events.append("upload")
        self.uploads.append(str(artifact))
        if self.fail_upload is not None:
            raise self.fail_upload
        route = "replace" if existed_before else "install"
        self.present = True
        self.active = False
        self.version = deploy.ARTIFACT_VERSION
        self.update_marker = False
        comparison = ({"uploaded_name": deploy.PLUGIN_NAME,
                       "uploaded_version": deploy.ARTIFACT_VERSION}
                      if existed_before else None)
        return {"route": route, "comparison": comparison, "after": self.read_target()}

    def activate(self):
        self.write_probe()
        self.events.append("activate")
        self.clicks.append("activate")
        if self.activate_then_fail:
            self.active = True
        if self.fail_activate is not None:
            raise self.fail_activate
        self.active = True
        return self.read_target()

    def deactivate_for_rollback(self):
        if self.version != deploy.ARTIFACT_VERSION:
            raise deploy.IndeterminateError("rollback target is not exact v2")
        self.write_probe()
        self.events.append("deactivate")
        self.clicks.append("deactivate")
        if self.fail_deactivate is not None:
            raise self.fail_deactivate
        self.active = False
        return self.read_target()


class FakePublic:
    def __init__(self):
        self.baseline = baseline()
        self.baseline_calls = 0
        self.drift_on_call: int | None = None
        self.findings = {
            "passed": True,
            "page": {"status": 200},
            "api": {"known_status": 200, "known_total_positive": True},
            "browser": {"status_ready": True, "result_count_positive": True},
        }
        self.validation_error: Exception | None = None
        self.after_validation = lambda: None

    def baseline_projection(self):
        self.baseline_calls += 1
        if self.drift_on_call == self.baseline_calls:
            return baseline(search_root_count=9)
        return json.loads(json.dumps(self.baseline))

    def activation_findings(self):
        if self.validation_error is not None:
            raise self.validation_error
        self.after_validation()
        return json.loads(json.dumps(self.findings))


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="derakane-v2-deploy-tests-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.plan_dir = self.tmp / "plans"
        self.plan_dir.mkdir()
        self.receipts = self.tmp / "receipts.jsonl"
        self.admin = FakeAdmin()
        self.public = FakePublic()
        self.events: list[str] = []
        self.mutex_held = False
        self.admin_opens = 0
        self.public_opens = 0
        self.active_plan: Path | None = None

        @contextlib.contextmanager
        def fake_mutex(lane, *, purpose, wait_seconds=90.0):
            self.assertEqual(lane, "wordpress")
            self.assertFalse(self.mutex_held)
            self.mutex_held = True
            self.events.append("mutex_enter")
            try:
                yield
            finally:
                self.events.append("mutex_exit")
                self.mutex_held = False

        @contextlib.contextmanager
        def fake_admin_session():
            self.admin_opens += 1
            self.events.append("admin_open")
            yield self.admin

        @contextlib.contextmanager
        def fake_anonymous_session():
            self.public_opens += 1
            self.events.append("public_open")
            yield self.public

        for patcher in (
            mock.patch.object(deploy, "PLAN_DIR", self.plan_dir),
            mock.patch.object(deploy, "RECEIPTS", self.receipts),
            mock.patch.object(deploy, "ui_browser_lock", fake_mutex),
            mock.patch.object(deploy, "admin_session", fake_admin_session),
            mock.patch.object(deploy, "anonymous_session", fake_anonymous_session),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        def write_probe():
            self.events.append("write_side_effect")
            self.assertTrue(self.mutex_held, "mutex must cover each side effect")
            if self.active_plan is not None:
                self.assertTrue(deploy.attempt_lock_path(self.active_plan).exists(),
                                "attempt lock must precede first side effect")

        self.admin.write_probe = write_probe

    def stage(self, action):
        handler = {
            "plugin_install_or_replace": deploy.command_stage_install_or_replace,
            "plugin_activate": deploy.command_stage_activate,
        }[action]
        before = set(self.plan_dir.glob("*.json"))
        with contextlib.redirect_stdout(io.StringIO()):
            handler(argparse.Namespace())
        created = sorted(set(self.plan_dir.glob("*.json")) - before)
        self.assertEqual(len(created), 1)
        return created[0]

    def commit(self, action, plan, approval=deploy.APPROVAL_WORD):
        handler = {
            "plugin_install_or_replace": deploy.command_commit_install_or_replace,
            "plugin_activate": deploy.command_commit_activate,
        }[action]
        self.active_plan = Path(plan)
        with contextlib.redirect_stdout(io.StringIO()):
            handler(argparse.Namespace(plan=str(plan), approval=approval))

    @staticmethod
    def read_json(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def lock(self, plan):
        return self.read_json(deploy.attempt_lock_path(Path(plan)))

    def result(self, plan):
        return self.read_json(deploy.result_path(Path(plan)))

    def receipts_rows(self):
        if not self.receipts.exists():
            return []
        return [json.loads(row) for row in self.receipts.read_text(
            encoding="utf-8").splitlines()]

    def rewrite_plan(self, plan, *, rehash=True, **changes):
        path = Path(plan)
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        value = self.read_json(path)
        value.pop("sha256", None)
        value.update(changes)
        if rehash:
            value["sha256"] = deploy.digest_for(value)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return path

    def prepare_activation(self):
        self.admin.present = True
        self.admin.active = False
        self.admin.version = deploy.ARTIFACT_VERSION
        self.admin.legacy_active = False
        self.public.baseline = baseline(search_root_count=0, legacy_asset_count=0)


class TestFixedCapability(unittest.TestCase):
    def test_exact_artifact_slug_version_identity(self):
        self.assertEqual(deploy.PLUGIN_SLUG, "frpdepot-derakane-chemical-search")
        self.assertEqual(deploy.PLUGIN_FILE,
                         "frpdepot-derakane-chemical-search/"
                         "frpdepot-derakane-chemical-search.php")
        self.assertEqual(deploy.PLUGIN_NAME,
                         "Derakane™ Resin Chemical Resistance Guide Search")
        self.assertEqual(deploy.ARTIFACT_VERSION, "2.0.0")
        self.assertEqual(deploy.ARTIFACT_BYTES, 791167)
        self.assertEqual(deploy.ARTIFACT_SHA256,
                         "fb0c73d63bfda241910231446d93d9260b8dad75562385aabf3d08e4560e85fa")
        self.assertEqual(deploy.DATASET_SHA256,
                         "6a650b48400d4ddfa6e7fa8d75ba1424b17568ffedc2375b2647bb3b94810bc8")
        self.assertEqual(deploy.MANIFEST_SHA256,
                         "effee39814215f58d207f10845911ddbf405b5d68e9ab57f471d4a6a283ea589")
        self.assertEqual(len(deploy.ARTIFACT_MEMBERS), 6)

    def test_artifact_and_all_six_member_hashes_verify(self):
        found = deploy.verify_artifact()
        self.assertEqual(found["sha256"], deploy.ARTIFACT_SHA256)
        self.assertEqual(found["bytes"], deploy.ARTIFACT_BYTES)
        self.assertEqual(found["version"], deploy.ARTIFACT_VERSION)
        self.assertEqual(found["member_sha256"], {
            key: deploy.ARTIFACT_MEMBER_SHA256[key]
            for key in sorted(deploy.ARTIFACT_MEMBER_SHA256)})

    def test_other_path_hash_size_members_member_hash_and_version_refuse(self):
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / deploy.ARTIFACT_PATH.name
            copy.write_bytes(deploy.ARTIFACT_PATH.read_bytes())
            with self.assertRaises(deploy.DeploymentError):
                deploy.verify_artifact(copy)
        cases = (
            ("ARTIFACT_SHA256", "0" * 64), ("ARTIFACT_BYTES", 1),
            ("ARTIFACT_VERSION", "9.9.9"), ("ARTIFACT_MEMBERS", ()),
            ("ARTIFACT_MEMBER_SHA256", {}), ("DATASET_SHA256", "0" * 64),
            ("MANIFEST_SHA256", "0" * 64),
        )
        for name, value in cases:
            with self.subTest(name=name), mock.patch.object(deploy, name, value):
                with self.assertRaises(deploy.DeploymentError):
                    deploy.verify_artifact()

    def test_only_five_fixed_commands_and_two_actions_exist(self):
        self.assertEqual(deploy.ACTIONS,
                         ("plugin_install_or_replace", "plugin_activate"))
        parser = deploy.build_parser()
        choices = next(item for item in parser._actions
                       if isinstance(item, argparse._SubParsersAction)).choices
        self.assertEqual(set(choices), set(deploy.COMMANDS))
        self.assertEqual(deploy.COMMANDS, (
            "inspect", "stage-install-or-replace", "commit-install-or-replace",
            "stage-activate", "commit-activate"))
        for banned in ("delete", "remove", "deactivate", "settings", "page", "product",
                       "order", "user", "content", "generic"):
            self.assertNotIn(banned, choices)

    def test_navigation_and_selectors_are_exact_closed_sets(self):
        self.assertEqual(deploy.ALLOWED_ADMIN_PATHS, frozenset({
            "/wp-admin/plugins.php", "/wp-admin/plugin-install.php",
            "/wp-admin/update.php"}))
        self.assertEqual(deploy.ALLOWED_PUBLIC_PATHS, frozenset({
            "/derakane-resin-resistance-search/",
            "/wp-json/frpdepot-derakane/v1/search"}))
        self.assertEqual(deploy.ROW_SELECTOR,
                         f'tr[data-plugin="{deploy.PLUGIN_FILE}"]:not(.plugin-update-tr)')
        self.assertEqual(deploy.LEGACY_ROW_SELECTOR,
                         f'tr[data-plugin="{deploy.LEGACY_PLUGIN_FILE}"]:'
                         "not(.plugin-update-tr)")
        for url in (
            "http://frpdepots.com/wp-admin/plugins.php",
            "https://www.frpdepots.com/wp-admin/plugins.php",
            "https://frpdepots.com.evil.test/wp-admin/plugins.php",
            "https://frpdepots.com:8443/wp-admin/plugins.php",
            "https://u:p@frpdepots.com/wp-admin/plugins.php",
        ):
            with self.subTest(url=url), self.assertRaises(deploy.DeploymentError):
                deploy.assert_origin(url)
        for url in (
            f"{deploy.EXACT_ORIGIN}/wp-admin/plugin-editor.php",
            f"{deploy.EXACT_ORIGIN}/wp-admin/options-general.php",
            f"{deploy.EXACT_ORIGIN}/wp-json/wp/v2/pages/1840",
            f"{deploy.EXACT_ORIGIN}/wp-json/wc/v3/products",
        ):
            with self.subTest(url=url), self.assertRaises(deploy.DeploymentError):
                if "/wp-admin/" in url:
                    deploy.assert_admin_url(url)
                else:
                    deploy.assert_public_url(url)

    def test_no_generic_delete_settings_content_commerce_shell_or_credentials_route(self):
        source = Path(deploy.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        doc_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)) and ast.get_docstring(
                                     node, clean=False) is not None:
                item = node.body[0]
                doc_lines.update(range(item.lineno, (item.end_lineno or item.lineno) + 1))
        code = "\n".join("" if n in doc_lines or line.lstrip().startswith("#") else line
                         for n, line in enumerate(source.splitlines(), 1))
        for needle in (
            ".delete", "delete-selected", "action=delete", "unlink(", "rmtree",
            "import requests", "urllib.request", "urlopen", "socket.", "subprocess",
            "os.system", "wp-json/wp/v2", "wp-json/wc/v3", "/wc/v3", ".fill(",
            ".type(", "storage_state", "localStorage", "sessionStorage", "getpass",
            "Authorization", "Bearer", "access_token", "launch_persistent_context",
            "user_data_dir", "plugin-editor.php", "options-general.php",
        ):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, code)
        self.assertNotIn("password", code.replace("parsed.password", ""))

    def test_anonymous_context_is_fresh_nonpersistent_and_get_only(self):
        source = Path(deploy.__file__).read_text(encoding="utf-8")
        block = source.split("def anonymous_session", 1)[1].split(
            "\ndef attempt_lock_path", 1)[0]
        self.assertIn('launch(channel="msedge", headless=True)', block)
        self.assertIn("new_context()", block)
        self.assertIn("context.close()", block)
        self.assertNotIn("storage_state", block)
        request_block = source.split("def _api_get", 1)[1].split(
            "\n    def baseline_projection", 1)[0]
        self.assertIn("self._request.get", request_block)
        self.assertNotIn(".post", request_block)


class TestPlans(Harness):
    def setUp(self):
        super().setUp()
        self.plan = self.stage("plugin_install_or_replace")
        self.original = self.plan.read_text(encoding="utf-8")

    def restore(self):
        os.chmod(self.plan, stat.S_IREAD | stat.S_IWRITE)
        self.plan.write_text(self.original, encoding="utf-8")

    def test_plan_is_full_hash_named_exact_24_hour_immutable_closed(self):
        value = self.read_json(self.plan)
        created = deploy.datetime.fromisoformat(value["created_utc"])
        expires = deploy.datetime.fromisoformat(value["expires_utc"])
        self.assertEqual(expires - created, timedelta(hours=24))
        self.assertEqual(created.utcoffset(), timedelta(0))
        saved = value.pop("sha256")
        self.assertEqual(saved, deploy.digest_for(value))
        self.assertIn(saved, self.plan.name)
        self.assertRegex(self.plan.name, r"_[0-9a-f]{64}\.json$")
        self.assertEqual(set(value), deploy.PLAN_KEYS)
        self.assertFalse(os.stat(self.plan).st_mode & stat.S_IWUSR)
        self.assertRegex(value["nonce"], r"^[0-9a-f]{32}$")
        self.assertEqual(value["artifact"], deploy.verify_artifact())
        self.assertEqual(value["rollback_limits"], {
            "automatic_v2_deactivation": False,
            "file_restoration": False,
            "prior_same_slug_artifact_available": False,
            "fresh_install_delete_rollback": False,
            "inactive_replace_can_restore_prior_files": False,
            "route": "install", "retry": False})

    def test_unhashed_tampering_refuses(self):
        self.rewrite_plan(self.plan, rehash=False, action="plugin_activate")
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan))
        self.assertIn("hash failed", str(caught.exception))

    def test_rehashed_identity_schema_contract_and_rollback_forgeries_refuse(self):
        for field, value in (
            ("origin", "https://example.test"), ("tool", "other"),
            ("tool_version", "0"), ("schema_version", 9),
            ("plugin_slug", "other"), ("plugin_file", "other/other.php"),
            ("action", "plugin_delete"), ("validation", {}),
            ("rollback_limits", {"file_restoration": True}),
        ):
            with self.subTest(field=field):
                self.restore()
                self.rewrite_plan(self.plan, **{field: value})
                with self.assertRaises(deploy.DeploymentError):
                    deploy.load_plan(str(self.plan))

    def test_expired_future_nonutc_and_non24hour_plans_refuse(self):
        now = deploy.utc_now()
        cases = (
            {"created_utc": (now - timedelta(hours=25)).isoformat(),
             "expires_utc": (now - timedelta(hours=1)).isoformat()},
            {"created_utc": (now + timedelta(minutes=6)).isoformat(),
             "expires_utc": (now + timedelta(hours=24, minutes=6)).isoformat()},
            {"created_utc": now.astimezone(timezone(timedelta(hours=1))).isoformat(),
             "expires_utc": (now + timedelta(hours=24)).astimezone(
                 timezone(timedelta(hours=1))).isoformat()},
            {"created_utc": now.isoformat(),
             "expires_utc": (now + timedelta(hours=23)).isoformat()},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.restore()
                self.rewrite_plan(self.plan, **changes)
                with self.assertRaises(deploy.DeploymentError):
                    deploy.load_plan(str(self.plan))

    def test_plan_outside_exact_directory_refuses(self):
        outside = self.tmp / "outside.json"
        outside.write_text(self.original, encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError):
            deploy.resolve_plan_path(str(outside))

    def test_artifact_public_and_row_records_are_fully_pinned(self):
        original = self.read_json(self.plan)
        changes = []
        artifact = dict(original["artifact"])
        artifact["sha256"] = "0" * 64
        changes.append({"artifact": artifact})
        before = dict(original["before"])
        before["version"] = "9.9.9"
        changes.append({"before": before})
        public = dict(original["public_before"])
        public["api_short_code"] = "forged"
        changes.append({"public_before": public})
        for change in changes:
            with self.subTest(change=next(iter(change))):
                self.restore()
                self.rewrite_plan(self.plan, **change)
                with self.assertRaises(deploy.DeploymentError):
                    deploy.load_plan(str(self.plan))


class TestStagingPreconditions(Harness):
    def test_current_shape_absent_same_slug_active_different_slug_stages_inactive_install(self):
        plan = self.stage("plugin_install_or_replace")
        value = self.read_json(plan)
        self.assertFalse(value["before"]["present"])
        self.assertTrue(value["legacy_conflict"]["present"])
        self.assertTrue(value["legacy_conflict"]["active"])
        self.assertEqual(value["legacy_conflict"]["version"], "1.1.0")
        self.assertEqual(value["after_expected"],
                         deploy.project_row(deploy.PLUGIN_FILE, True, False, "2.0.0", False))

    def test_active_same_slug_v1_replacement_is_refused_not_pretended_inactive(self):
        self.admin.present = True
        self.admin.active = True
        self.admin.version = "1.1.0"
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.stage("plugin_install_or_replace")
        self.assertIn("active same-slug replacement", str(caught.exception))
        self.assertEqual(list(self.plan_dir.glob("*.json")), [])

    def test_inactive_same_slug_v1_replace_is_disclosed_without_file_rollback(self):
        self.admin.present = True
        self.admin.active = False
        self.admin.version = "1.1.0"
        plan = self.stage("plugin_install_or_replace")
        value = self.read_json(plan)
        self.assertEqual(value["rollback_limits"]["route"], "replace")
        self.assertFalse(value["rollback_limits"]["file_restoration"])
        self.assertFalse(value["rollback_limits"]["prior_same_slug_artifact_available"])

    def test_unknown_same_slug_version_or_exact_v2_noop_refuses(self):
        for version in ("0.9.0", "2.0.0"):
            self.admin.present = True
            self.admin.active = False
            self.admin.version = version
            with self.subTest(version=version), self.assertRaises(deploy.DeploymentError):
                self.stage("plugin_install_or_replace")

    def test_activation_is_separate_and_requires_exact_v2_inactive(self):
        with self.assertRaises(deploy.DeploymentError):
            self.stage("plugin_activate")
        self.prepare_activation()
        plan = self.stage("plugin_activate")
        value = self.read_json(plan)
        self.assertIsNone(value["artifact"])
        self.assertEqual(value["action"], "plugin_activate")
        self.assertTrue(value["rollback_limits"]["automatic_v2_deactivation"])
        self.assertFalse(value["rollback_limits"]["file_restoration"])

    def test_activation_refuses_while_fixed_different_slug_legacy_is_active(self):
        self.prepare_activation()
        self.admin.legacy_active = True
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.stage("plugin_activate")
        self.assertIn("legacy Derakane v1.1 plugin is active", str(caught.exception))

    def test_unhealthy_or_wrong_page_api_precondition_refuses(self):
        self.prepare_activation()
        for changed in (
            baseline(blank=True, search_root_count=0, legacy_asset_count=0),
            baseline(search_root_count=0, legacy_asset_count=0, v2_js_count=1),
            baseline(search_root_count=0, legacy_asset_count=0,
                     api_short_status=200, api_short_code="derakane_short_query"),
        ):
            self.public.baseline = changed
            with self.subTest(changed=changed), self.assertRaises(deploy.DeploymentError):
                self.stage("plugin_activate")


class TestApprovalOrderingDriftAndLocking(Harness):
    def test_approval_is_exact_unpadded_uppercase(self):
        deploy.require_approval("APPROVED")
        for wrong in ("", "approved", " Approved", "APPROVED ", "APPROVED!",
                      "APPROVED APPROVED", "yes", None):
            with self.subTest(wrong=wrong), self.assertRaises(deploy.DeploymentError):
                deploy.require_approval(wrong)

    def test_bad_approval_precedes_artifact_browser_public_network_and_attempt_lock(self):
        plan = self.stage("plugin_install_or_replace")
        admin_opens = self.admin_opens
        public_opens = self.public_opens
        with mock.patch.object(deploy, "verify_artifact",
                               side_effect=AssertionError("artifact opened before approval")):
            with self.assertRaises(deploy.DeploymentError):
                self.commit("plugin_install_or_replace", plan, approval="approved")
        self.assertEqual(self.admin_opens, admin_opens)
        self.assertEqual(self.public_opens, public_opens)
        self.assertFalse(deploy.attempt_lock_path(plan).exists())
        self.assertFalse(deploy.result_path(plan).exists())

    def test_mutex_is_acquired_before_attempt_lock_and_lock_before_side_effect(self):
        plan = self.stage("plugin_install_or_replace")
        self.events.clear()
        original = deploy.write_attempt_lock

        def watched(path, value, stage):
            self.events.append("attempt_lock")
            self.assertTrue(self.mutex_held)
            original(path, value, stage)
            lock = self.read_json(path)
            self.assertEqual(lock["attempts_allowed"], 1)
            self.assertFalse(lock["retry"])

        with mock.patch.object(deploy, "write_attempt_lock", watched):
            self.commit("plugin_install_or_replace", plan)
        self.assertLess(self.events.index("mutex_enter"), self.events.index("attempt_lock"))
        self.assertLess(self.events.index("attempt_lock"), self.events.index("write_side_effect"))
        self.assertLess(self.events.index("write_side_effect"), self.events.index("mutex_exit"))

    def test_busy_mutex_does_not_burn_plan_or_open_browser(self):
        plan = self.stage("plugin_install_or_replace")
        opens = (self.admin_opens, self.public_opens)

        @contextlib.contextmanager
        def busy(*_args, **_kwargs):
            raise deploy.UiLaneBusy("busy")
            yield  # pragma: no cover

        with mock.patch.object(deploy, "ui_browser_lock", busy):
            with self.assertRaises(deploy.UiLaneBusy):
                self.commit("plugin_install_or_replace", plan)
        self.assertEqual((self.admin_opens, self.public_opens), opens)
        self.assertFalse(deploy.attempt_lock_path(plan).exists())

    def test_target_legacy_or_public_drift_refuses_before_attempt(self):
        scenarios = ("target", "legacy", "public")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                self.setUp()
                plan = self.stage("plugin_install_or_replace")
                if scenario == "target":
                    self.admin.present = True
                    self.admin.active = False
                    self.admin.version = "1.1.0"
                elif scenario == "legacy":
                    self.admin.legacy_active = False
                else:
                    self.public.baseline = baseline(search_root_count=8)
                with self.assertRaises(deploy.DeploymentError):
                    self.commit("plugin_install_or_replace", plan)
                self.assertEqual(self.admin.uploads, [])
                self.assertFalse(deploy.attempt_lock_path(plan).exists())


class TestInstallOrReplace(Harness):
    def test_fresh_install_exact_artifact_once_ends_inactive_complete_live_readback(self):
        plan = self.stage("plugin_install_or_replace")
        self.commit("plugin_install_or_replace", plan)
        self.assertEqual(self.admin.uploads, [str(deploy.ARTIFACT_PATH)])
        self.assertTrue(self.admin.present)
        self.assertFalse(self.admin.active)
        self.assertEqual(self.admin.version, "2.0.0")
        result = self.result(plan)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["route"], "install")
        self.assertFalse(result["activated"])
        self.assertFalse(result["file_restoration_available"])
        self.assertEqual(result["after"], deploy.expected_after(
            "plugin_install_or_replace", self.read_json(plan)["before"]))
        self.assertEqual(result["legacy_conflict"], self.read_json(plan)["legacy_conflict"])
        self.assertEqual(result["public_after"], self.read_json(plan)["public_before"])
        self.assertEqual(self.lock(plan)["status"], "committed_verified")

    def test_inactive_same_slug_v1_replaces_once_and_stays_inactive(self):
        self.admin.present = True
        self.admin.active = False
        self.admin.version = "1.1.0"
        plan = self.stage("plugin_install_or_replace")
        self.commit("plugin_install_or_replace", plan)
        result = self.result(plan)
        self.assertEqual(result["route"], "replace")
        self.assertEqual(result["comparison"], {
            "uploaded_name": deploy.PLUGIN_NAME, "uploaded_version": "2.0.0"})
        self.assertFalse(result["after"]["active"])

    def test_upload_uncertainty_one_attempt_no_retry_and_rollback_limits_recorded(self):
        self.admin.fail_upload = TimeoutError("unknown final state")
        plan = self.stage("plugin_install_or_replace")
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_install_or_replace", plan)
        self.assertEqual(len(self.admin.uploads), 1)
        result = self.result(plan)
        self.assertEqual(result["status"], "INDETERMINATE")
        self.assertFalse(result["retry"])
        self.assertFalse(result["file_restoration_attempted"])
        self.assertFalse(result["file_restoration_available"])
        self.assertFalse(result["delete_attempted"])
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_install_or_replace", plan)
        self.assertEqual(len(self.admin.uploads), 1)

    def test_postwrite_public_readback_failure_is_indeterminate_no_fake_rollback(self):
        plan = self.stage("plugin_install_or_replace")
        # stage baseline call=1, commit prewrite baseline=2, postwrite baseline=3.
        self.public.drift_on_call = 3
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_install_or_replace", plan)
        result = self.result(plan)
        self.assertEqual(result["status"], "INDETERMINATE")
        self.assertEqual(result["stage"], "complete_live_readback")
        self.assertFalse(result["file_restoration_available"])
        self.assertFalse(result["delete_attempted"])
        self.assertFalse(self.admin.active)


class TestActivation(Harness):
    def setUp(self):
        super().setUp()
        self.prepare_activation()

    def test_success_activates_separately_then_complete_anonymous_and_admin_readback(self):
        plan = self.stage("plugin_activate")
        self.commit("plugin_activate", plan)
        self.assertTrue(self.admin.active)
        self.assertEqual(self.admin.clicks, ["activate"])
        result = self.result(plan)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertTrue(result["anonymous_validation"]["passed"])
        self.assertEqual(result["after"], self.read_json(plan)["after_expected"])
        self.assertEqual(result["legacy_conflict"], self.read_json(plan)["legacy_conflict"])
        self.assertFalse(result["emergency_deactivated"])
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_activate", plan)
        self.assertEqual(self.admin.clicks, ["activate"])

    def test_known_page_api_failure_auto_deactivates_once_and_proves_baseline_restored(self):
        self.public.findings["passed"] = False
        plan = self.stage("plugin_activate")
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.commit("plugin_activate", plan)
        self.assertNotIsInstance(caught.exception, deploy.IndeterminateError)
        self.assertFalse(self.admin.active)
        self.assertEqual(self.admin.clicks, ["activate", "deactivate"])
        result = self.result(plan)
        self.assertEqual(result["status"], "FAILED_CLOSED")
        self.assertTrue(result["rollback"]["inactive_confirmed"])
        self.assertTrue(result["rollback"]["baseline_restored"])
        self.assertEqual(self.lock(plan)["status"], "failed_closed")
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_activate", plan)
        self.assertEqual(self.admin.clicks.count("deactivate"), 1)

    def test_timeout_validation_rolls_back_once_but_remains_indeterminate(self):
        self.public.validation_error = TimeoutError("unknown public result")
        plan = self.stage("plugin_activate")
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_activate", plan)
        self.assertFalse(self.admin.active)
        self.assertEqual(self.admin.clicks, ["activate", "deactivate"])
        result = self.result(plan)
        self.assertEqual(result["status"], "INDETERMINATE")
        self.assertTrue(result["rollback"]["inactive_confirmed"])
        self.assertTrue(result["rollback"]["baseline_restored"])

    def test_activation_click_uncertainty_attempts_exact_v2_deactivation_once(self):
        self.admin.activate_then_fail = True
        self.admin.fail_activate = TimeoutError("click outcome unknown")
        plan = self.stage("plugin_activate")
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_activate", plan)
        self.assertFalse(self.admin.active)
        self.assertEqual(self.admin.clicks, ["activate", "deactivate"])
        self.assertEqual(self.result(plan)["status"], "INDETERMINATE")

    def test_unconfirmed_rollback_is_never_reported_restored(self):
        self.public.findings["passed"] = False
        self.admin.fail_deactivate = TimeoutError("rollback unknown")
        plan = self.stage("plugin_activate")
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_activate", plan)
        result = self.result(plan)
        self.assertEqual(result["status"], "INDETERMINATE")
        self.assertFalse(result["rollback"]["inactive_confirmed"])
        self.assertFalse(result["rollback"]["baseline_restored"])
        self.assertTrue(self.admin.active)

    def test_final_admin_readback_drift_triggers_rollback_and_indeterminate(self):
        def drift():
            self.admin.version = "9.9.9"
        self.public.after_validation = drift
        plan = self.stage("plugin_activate")
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_activate", plan)
        result = self.result(plan)
        self.assertEqual(result["status"], "INDETERMINATE")
        self.assertFalse(result["rollback"]["inactive_confirmed"])
        self.assertTrue(self.admin.active)


class TestReceipts(Harness):
    def test_stage_and_success_receipts_bind_action_plan_full_hash_and_result(self):
        plan = self.stage("plugin_install_or_replace")
        staged = self.receipts_rows()[0]
        digest = self.read_json(plan)["sha256"]
        self.assertEqual(staged["action"], "derakane_search_v2_plan_staged")
        self.assertIn(str(plan), staged["evidence"])
        self.assertIn(digest, staged["evidence"])
        self.commit("plugin_install_or_replace", plan)
        row = self.receipts_rows()[-1]
        self.assertEqual(row["action"],
                         "derakane_search_v2_committed_and_verified")
        self.assertIn(digest, row["evidence"])
        result = self.result(plan)
        self.assertEqual(result["plan_sha256"], digest)
        self.assertEqual(Path(result["plan"]).resolve(), plan.resolve())
        self.assertFalse(result["retry"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
