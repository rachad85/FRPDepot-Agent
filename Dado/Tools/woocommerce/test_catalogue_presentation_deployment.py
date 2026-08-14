"""Offline behavioural tests for the fixed catalogue-presentation deployer.

No test in this module opens Playwright, CDP, a browser, a credential store, or a
network connection. Small stateful admin/public doubles exercise plan, ordering,
one-attempt, rollback, result, and receipt behaviour around the production command
functions. PublicPage's exact anonymous catalogue contract is separately exercised
through a read-only DOM double.
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
import re
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import catalogue_presentation_deployment_tool as deploy  # noqa: E402


class FakeAdmin:
    """The one fixed plugin row; no generic row or delete operation exists."""

    def __init__(self):
        self.present = True
        self.active = False
        self.version = deploy.ARTIFACT_VERSION
        self.update_marker = False
        self.clicks: list[str] = []
        self.uploads: list[str] = []
        self.route = "replace"
        self.fail_upload: Exception | None = None
        self.fail_activate: Exception | None = None
        self.activate_then_fail = False
        self.fail_deactivate: Exception | None = None
        self.events: list[str] = []
        self.write_probe = lambda: None

    def goto_plugins(self):
        self.events.append("goto_plugins")

    def read_row(self, allow_absent=False):
        if not self.present:
            if allow_absent:
                return deploy.project_row(False, None, "", False)
            raise deploy.DeploymentError("fixed plugin is absent")
        return deploy.project_row(True, self.active, self.version, self.update_marker)

    def upload_install_or_replace(self, artifact, existed_before):
        self.write_probe()
        self.events.append("upload")
        self.uploads.append(str(artifact))
        if self.fail_upload is not None:
            raise self.fail_upload
        self.present = True
        self.active = False
        self.version = deploy.ARTIFACT_VERSION
        route = "replace" if existed_before else "install"
        if route != self.route:
            raise AssertionError(f"unexpected route {route!r}, expected {self.route!r}")
        return {"route": route, "after": self.read_row()}

    def activate(self):
        self.write_probe()
        self.events.append("activate")
        self.clicks.append("activate")
        if self.activate_then_fail:
            self.active = True
        if self.fail_activate is not None:
            raise self.fail_activate
        self.active = True
        return self.read_row()

    def deactivate(self):
        self.write_probe()
        self.events.append("deactivate")
        self.clicks.append("deactivate")
        if self.fail_deactivate is not None:
            raise self.fail_deactivate
        self.active = False
        return self.read_row()


class FakePublic:
    def __init__(self):
        self.health = {"blank": False, "fatal": False}
        self.findings = {"passed": True, "projections": {}, "guide": {"passed": True}}
        self.validation_error: Exception | None = None
        self.health_error: Exception | None = None
        self.calls: list[tuple[str, str | None]] = []
        prerequisite_core = {"passed": True, "fixed_live_contract": "satisfied"}
        self.prerequisites = {
            **prerequisite_core,
            "fingerprint": deploy.digest_for(prerequisite_core),
        }

    def require_healthy(self, url):
        self.calls.append(("healthy", url))
        if self.health_error is not None:
            raise self.health_error
        return dict(self.health)

    def catalogue_findings(self):
        self.calls.append(("catalogue", None))
        if self.validation_error is not None:
            raise self.validation_error
        return json.loads(json.dumps(self.findings))

    def activation_prerequisite_findings(self):
        self.calls.append(("prerequisites", None))
        return json.loads(json.dumps(self.prerequisites))


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="catalogue-deploy-tests-"))
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

        @contextlib.contextmanager
        def fake_mutex(lane, *, purpose, wait_seconds=90.0):
            self.assertEqual(lane, "wordpress")
            self.assertFalse(self.mutex_held, "the command-level mutex must not overlap itself")
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
            self.events.append("anonymous_open")
            yield self.public

        patches = (
            mock.patch.object(deploy, "PLAN_DIR", self.plan_dir),
            mock.patch.object(deploy, "RECEIPTS", self.receipts),
            mock.patch.object(deploy, "ui_browser_lock", fake_mutex),
            mock.patch.object(deploy, "admin_session", fake_admin_session),
            mock.patch.object(deploy, "anonymous_session", fake_anonymous_session),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        def write_probe():
            self.events.append("write_side_effect")
            self.assertTrue(self.mutex_held, "browser mutex must cover every write attempt")
            active_plan = getattr(self, "active_plan", None)
            if active_plan is not None:
                self.assertTrue(deploy.plan_lock_path(active_plan).exists(),
                                "attempt lock must precede every write attempt")

        self.admin.write_probe = write_probe

    def stage(self, action):
        handlers = {
            "plugin_install_or_replace": deploy.command_stage_install_or_replace,
            "plugin_activate": deploy.command_stage_activate,
            "plugin_deactivate": deploy.command_stage_deactivate,
        }
        before = set(self.plan_dir.glob("*.json"))
        with contextlib.redirect_stdout(io.StringIO()):
            handlers[action](argparse.Namespace())
        created = sorted(set(self.plan_dir.glob("*.json")) - before)
        self.assertEqual(len(created), 1)
        return created[0]

    def commit(self, action, plan, approval=deploy.APPROVAL_WORD):
        handlers = {
            "plugin_install_or_replace": deploy.command_commit_install_or_replace,
            "plugin_activate": deploy.command_commit_activate,
            "plugin_deactivate": deploy.command_commit_deactivate,
        }
        self.active_plan = Path(plan)
        with contextlib.redirect_stdout(io.StringIO()):
            handlers[action](argparse.Namespace(plan=str(plan), approval=approval))

    @staticmethod
    def read_json(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def lock(self, plan):
        return self.read_json(deploy.plan_lock_path(Path(plan)))

    def result(self, plan):
        return self.read_json(deploy.result_path(Path(plan)))

    def receipt_rows(self):
        if not self.receipts.exists():
            return []
        return [json.loads(line) for line in self.receipts.read_text(encoding="utf-8").splitlines()]

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


class TestFixedCapability(unittest.TestCase):
    def test_identity_origin_artifact_and_plugin_row_are_exact(self):
        self.assertEqual(deploy.EXACT_ORIGIN, "https://frpdepots.com")
        self.assertEqual(deploy.ALLOWED_HOST, "frpdepots.com")
        self.assertEqual(deploy.CDP_ENDPOINT, "http://127.0.0.1:9229")
        self.assertEqual(deploy.PLUGIN_NAME, "FRP Depot Automatic Catalogue Presentation")
        self.assertEqual(deploy.PLUGIN_SLUG, "frpdepot-automatic-catalogue-presentation")
        self.assertEqual(
            deploy.PLUGIN_FILE,
            "frpdepot-automatic-catalogue-presentation/"
            "frpdepot-automatic-catalogue-presentation.php",
        )
        self.assertEqual(
            deploy.ROW_SELECTOR,
            f'tr[data-plugin="{deploy.PLUGIN_FILE}"]:not(.plugin-update-tr)',
        )
        self.assertEqual(deploy.ACTIVATE_SELECTOR, ".row-actions .activate a")
        self.assertEqual(deploy.DEACTIVATE_SELECTOR, ".row-actions .deactivate a")

    def test_post_protection_shared_guide_source_state_is_exact_activation_prerequisite(self):
        class PageDouble:
            @staticmethod
            def content():
                return "Hetron " + deploy.DERAKANE_OLD_URL

            @staticmethod
            def query_selector_all(selector):
                expected = {
                    ".et_pb_blurb_1_tb_body h4.et_pb_module_header": 1,
                    ".et_pb_blurb_0_tb_body h4.et_pb_module_header": 1,
                    f'.et_pb_text_1_tb_body a[href="{deploy.INLINE_CTA_PATH}"]': 1,
                }
                return [object()] * expected.get(selector, 0)

        public = deploy.PublicPage.__new__(deploy.PublicPage)
        public._page = PageDouble()
        public.require_healthy = lambda _url: None
        finding = public._guide_findings(1368, transformed=False)
        self.assertTrue(finding["passed"])
        self.assertEqual(finding["hetron_text_count"], 1)
        self.assertEqual(finding["hetron_url_count"], 0)
        self.assertEqual(finding["hetron_card_count"], 1)
        self.assertEqual(finding["derakane_old_url_count"], 1)

    def test_only_the_six_fixed_commands_exist(self):
        self.assertEqual(deploy.COMMANDS, (
            "stage-install-or-replace", "commit-install-or-replace",
            "stage-activate", "commit-activate",
            "stage-deactivate", "commit-deactivate",
        ))
        parser = deploy.build_parser()
        subcommands = next(action for action in parser._actions
                           if isinstance(action, argparse._SubParsersAction)).choices
        self.assertEqual(set(subcommands), set(deploy.COMMANDS))
        for banned in ("delete", "remove", "upload", "install", "replace", "commit", "stage"):
            self.assertNotIn(banned, subcommands)

    def test_actions_and_navigation_are_closed_sets(self):
        self.assertEqual(deploy.ACTIONS, (
            "plugin_install_or_replace", "plugin_activate", "plugin_deactivate"))
        self.assertEqual(deploy.ALLOWED_ADMIN_PATHS, frozenset({
            "/wp-admin/plugins.php", "/wp-admin/plugin-install.php", "/wp-admin/update.php"}))
        self.assertEqual(deploy.ALLOWED_PUBLIC_PATHS, frozenset({
            "/", "/products/", *(deploy.urlsplit(url).path for url in deploy.PRODUCT_URLS.values()),
            "/wp-json/wc/store/v1/products/2061", "/derakane-resin-resistance-search/",
            "/wp-json/frpdepot-derakane/v1/search", "/hetron-cr-guide-2007_ineos/",
            "/wp-content/uploads/2026/03/HETRON-CR-Guide-2007_Ineos.pdf",
        }))

    def test_origin_and_paths_refuse_near_misses(self):
        for url in (
            "http://frpdepots.com/wp-admin/plugins.php",
            "https://www.frpdepots.com/wp-admin/plugins.php",
            "https://frpdepots.com.evil.test/wp-admin/plugins.php",
            "https://frpdepots.com:8443/wp-admin/plugins.php",
            "https://user:pw@frpdepots.com/wp-admin/plugins.php",
        ):
            with self.subTest(url=url), self.assertRaises(deploy.DeploymentError):
                deploy.assert_origin(url)
        for url in (
            f"{deploy.EXACT_ORIGIN}/wp-admin/plugin-editor.php",
            f"{deploy.EXACT_ORIGIN}/wp-admin/options-general.php",
            f"{deploy.EXACT_ORIGIN}/wp-login.php",
        ):
            with self.subTest(url=url), self.assertRaises(deploy.DeploymentError):
                deploy.assert_admin_url(url)
        deploy.assert_admin_url(deploy.PLUGINS_URL)
        deploy.assert_public_url(deploy.PRODUCT_URL)

    def test_no_delete_generic_network_shell_or_credential_route_in_executable_code(self):
        source = Path(deploy.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        doc_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and ast.get_docstring(node, clean=False) is not None:
                item = node.body[0]
                doc_lines.update(range(item.lineno, (item.end_lineno or item.lineno) + 1))
        code = "\n".join("" if n in doc_lines or line.lstrip().startswith("#") else line
                         for n, line in enumerate(source.splitlines(), 1))
        for needle in (
            ".delete", "delete-selected", "action=delete", "unlink(", "rmtree",
            "import requests", "urllib.request", "urlopen", "socket.", "subprocess",
            "os.system", "woocommerce_common", "/wc/v3",
            "storage_state", "localStorage", "sessionStorage", "getpass",
            "Authorization", "Bearer", "access_token", "credentials",
            "launch_persistent_context", "user_data_dir", ".fill(", ".type(",
        ):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, code)
        self.assertNotIn("password", code.replace("parsed.password", ""))

    def test_anonymous_session_is_throwaway_and_nonpersistent(self):
        source = Path(deploy.__file__).read_text(encoding="utf-8")
        block = source.split("def anonymous_session", 1)[1].split("\ndef plan_lock_path", 1)[0]
        self.assertIn('launch(channel="msedge", headless=True)', block)
        self.assertIn("new_context()", block)
        self.assertIn("context.close()", block)
        self.assertNotIn("storage_state", block)
        self.assertNotIn("launch_persistent_context", block)


class TestArtifact(unittest.TestCase):
    def test_current_fixed_artifact_matches_every_pinned_constant(self):
        record = deploy.verify_artifact()
        self.assertEqual(Path(record["path"]).resolve(), deploy.ARTIFACT_PATH.resolve())
        self.assertEqual(record["sha256"],
                         "472d8fa0bd647b255093301386c2ce94779b8005daa76eadcf18169f372f468c")
        self.assertEqual(record["sha256"], deploy.ARTIFACT_SHA256)
        self.assertEqual(record["bytes"], 6964)
        self.assertEqual(record["bytes"], deploy.ARTIFACT_BYTES)
        self.assertEqual(record["version"], "1.0.3")
        self.assertEqual(record["members"], sorted(deploy.ARTIFACT_MEMBERS))
        self.assertEqual(deploy.ARTIFACT_MEMBER_SHA256, {
            f"{deploy.PLUGIN_SLUG}/frpdepot-automatic-catalogue-presentation.php":
                "90f35e3de454bc28966884932ae06d0b4ac9548f37f3e01efd4b64f52f50d9c9",
            f"{deploy.PLUGIN_SLUG}/readme.txt":
                "4431cee3cf114564cc99d59cc36145fb4df89557f63a4379965e31a0e6bf84b2",
        })

    def test_other_path_hash_size_member_or_version_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            other = Path(folder) / deploy.ARTIFACT_PATH.name
            other.write_bytes(deploy.ARTIFACT_PATH.read_bytes())
            with self.assertRaises(deploy.DeploymentError):
                deploy.verify_artifact(other)
        for name, value in (("ARTIFACT_SHA256", "0" * 64), ("ARTIFACT_BYTES", 1),
                            ("ARTIFACT_VERSION", "9.9.9"), ("ARTIFACT_MEMBERS", ())):
            with self.subTest(name=name), mock.patch.object(deploy, name, value):
                with self.assertRaises(deploy.DeploymentError):
                    deploy.verify_artifact()


class TestPlans(Harness):
    def setUp(self):
        super().setUp()
        self.admin.active = False
        self.plan = self.stage("plugin_activate")
        self.original = self.plan.read_text(encoding="utf-8")

    def restore(self):
        os.chmod(self.plan, stat.S_IREAD | stat.S_IWRITE)
        self.plan.write_text(self.original, encoding="utf-8")

    def test_plan_is_exactly_24_hours_hashed_closed_and_read_only(self):
        value = self.read_json(self.plan)
        created = deploy.datetime.fromisoformat(value["created_utc"])
        expires = deploy.datetime.fromisoformat(value["expires_utc"])
        self.assertEqual(expires - created, timedelta(hours=24))
        self.assertEqual(created.utcoffset(), timedelta(0))
        saved = value.pop("sha256")
        self.assertEqual(saved, deploy.digest_for(value))
        self.assertEqual(set(value), deploy.PLAN_KEYS)
        self.assertFalse(os.stat(self.plan).st_mode & stat.S_IWUSR)
        self.assertRegex(value["nonce"], r"^[0-9a-f]{32}$")
        self.assertEqual(value["origin"], deploy.EXACT_ORIGIN)
        self.assertEqual(value["plugin_file"], deploy.PLUGIN_FILE)
        self.assertEqual(value["validation"], deploy.VALIDATION_CONTRACT)

    def test_unhashed_tampering_is_refused(self):
        self.rewrite_plan(self.plan, rehash=False, action="plugin_deactivate")
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan))
        self.assertIn("hash failed", str(caught.exception))

    def test_rehashed_identity_schema_and_contract_forgeries_are_refused(self):
        for field, value in (
            ("origin", "https://example.test"), ("tool", "other"),
            ("tool_version", "1.0.0"), ("schema_version", 1),
            ("plugin_slug", "other"), ("plugin_file", "other/other.php"),
            ("action", "plugin_delete"), ("validation", {"anonymous": True}),
        ):
            with self.subTest(field=field):
                self.restore()
                self.rewrite_plan(self.plan, **{field: value})
                with self.assertRaises(deploy.DeploymentError):
                    deploy.load_plan(str(self.plan))

    def test_expired_future_non_utc_and_non_24_hour_plans_are_refused(self):
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

    def test_plan_outside_fixed_directory_is_refused(self):
        outside = self.tmp / "outside.json"
        outside.write_text(self.original, encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError):
            deploy.resolve_plan_path(str(outside))

    def test_install_plan_pins_path_hash_size_version_and_members(self):
        self.admin.version = "0.9.0"
        plan = self.stage("plugin_install_or_replace")
        artifact = self.read_json(plan)["artifact"]
        self.assertEqual(artifact, deploy.verify_artifact())
        for field, value in (("path", str(self.tmp / "other.zip")), ("sha256", "0" * 64),
                             ("bytes", 1), ("version", "9.9.9"), ("members", [])):
            original = plan.read_text(encoding="utf-8")
            with self.subTest(field=field):
                os.chmod(plan, stat.S_IREAD | stat.S_IWRITE)
                plan.write_text(original, encoding="utf-8")
                changed = self.read_json(plan)["artifact"]
                changed[field] = value
                self.rewrite_plan(plan, artifact=changed)
                with self.assertRaises(deploy.DeploymentError):
                    deploy.load_plan(str(plan))


class TestApprovalAndOrdering(Harness):
    def test_approval_is_exact(self):
        deploy.require_approval("APPROVED")
        for wrong in ("", "approved", " Approved", "APPROVED ", "APPROVED!",
                      "APPROVED APPROVED", "yes", None):
            with self.subTest(wrong=wrong), self.assertRaises(deploy.DeploymentError):
                deploy.require_approval(wrong)

    def test_every_bad_approval_refuses_before_browser_credentials_lock_or_result(self):
        plans = {}
        self.admin.version = "0.9.0"
        plans["plugin_install_or_replace"] = self.stage("plugin_install_or_replace")
        self.admin.version = deploy.ARTIFACT_VERSION
        plans["plugin_activate"] = self.stage("plugin_activate")
        self.admin.active = True
        plans["plugin_deactivate"] = self.stage("plugin_deactivate")
        opens = self.admin_opens
        for action, plan in plans.items():
            with self.subTest(action=action), self.assertRaises(deploy.DeploymentError):
                self.commit(action, plan, approval="approved")
            self.assertEqual(self.admin_opens, opens)
            self.assertFalse(deploy.plan_lock_path(plan).exists())
            self.assertFalse(deploy.result_path(plan).exists())
        # Staging activation runs one anonymous, read-only prerequisite inspection;
        # bad approval still opens no additional public/admin browser and creates no lock.
        self.assertEqual(self.public_opens, 1)

    def test_mutex_is_entered_before_attempt_lock_and_covers_side_effect(self):
        self.admin.version = "0.9.0"
        plan = self.stage("plugin_install_or_replace")
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

    def test_busy_mutex_cannot_burn_a_plan_or_open_a_browser(self):
        self.admin.active = False
        plan = self.stage("plugin_activate")
        opens = self.admin_opens

        @contextlib.contextmanager
        def busy(*_args, **_kwargs):
            raise deploy.UiLaneBusy("busy")
            yield  # pragma: no cover

        with mock.patch.object(deploy, "ui_browser_lock", busy):
            with self.assertRaises(deploy.UiLaneBusy):
                self.commit("plugin_activate", plan)
        self.assertEqual(self.admin_opens, opens)
        self.assertFalse(deploy.plan_lock_path(plan).exists())
        self.assertFalse(deploy.result_path(plan).exists())


class TestInstallOrReplace(Harness):
    def test_replace_uploads_exact_artifact_once_and_ends_inactive(self):
        self.admin.version = "0.9.0"
        self.admin.active = False
        self.admin.route = "replace"
        plan = self.stage("plugin_install_or_replace")
        self.commit("plugin_install_or_replace", plan)
        self.assertEqual(self.admin.uploads, [str(deploy.ARTIFACT_PATH)])
        self.assertFalse(self.admin.active)
        self.assertEqual(self.admin.version, deploy.ARTIFACT_VERSION)
        result = self.result(plan)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["route"], "replace")
        self.assertFalse(result["activated"])
        self.assertFalse(result["after"]["active"])
        self.assertEqual(self.lock(plan)["status"], "committed_verified")

    def test_fresh_install_uses_only_install_route_and_ends_inactive(self):
        self.admin.present = False
        self.admin.route = "install"
        plan = self.stage("plugin_install_or_replace")
        self.commit("plugin_install_or_replace", plan)
        self.assertTrue(self.admin.present)
        self.assertFalse(self.admin.active)
        self.assertEqual(self.result(plan)["route"], "install")

    def test_upload_uncertainty_is_permanently_indeterminate_and_never_retried(self):
        self.admin.version = "0.9.0"
        self.admin.fail_upload = TimeoutError("unknown final state")
        plan = self.stage("plugin_install_or_replace")
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_install_or_replace", plan)
        self.assertEqual(len(self.admin.uploads), 1)
        self.assertEqual(self.lock(plan)["status"], "indeterminate")
        self.assertEqual(self.result(plan)["status"], "INDETERMINATE")
        self.assertFalse(self.result(plan)["retry"])
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_install_or_replace", plan)
        self.assertEqual(len(self.admin.uploads), 1)


class TestActivation(Harness):
    def test_success_activates_then_runs_exact_anonymous_validation(self):
        plan = self.stage("plugin_activate")
        self.commit("plugin_activate", plan)
        self.assertTrue(self.admin.active)
        self.assertEqual(self.admin.clicks, ["activate"])
        self.assertEqual(self.public.calls, [
            ("prerequisites", None), ("prerequisites", None), ("catalogue", None)])
        result = self.result(plan)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertTrue(result["validation"]["passed"])
        self.assertFalse(result["emergency_deactivated"])
        self.assertFalse(result["retry"])
        calls = list(self.public.calls)
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_activate", plan)
        self.assertEqual(self.admin.clicks, ["activate"])
        self.assertEqual(self.public.calls, calls)

    def test_known_validation_failure_auto_deactivates_exactly_once(self):
        self.public.findings = {
            "passed": False,
            "projections": {"mobile": {"matches_expected": False}},
            "guide": {"passed": True},
        }
        plan = self.stage("plugin_activate")
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.commit("plugin_activate", plan)
        self.assertNotIsInstance(caught.exception, deploy.IndeterminateError)
        self.assertFalse(self.admin.active)
        self.assertEqual(self.admin.clicks, ["activate", "deactivate"])
        result = self.result(plan)
        self.assertEqual(result["status"], "FAILED_CLOSED")
        self.assertEqual(result["validation"], self.public.findings)
        self.assertTrue(result["emergency_deactivated"])
        self.assertTrue(result["rollback"]["inactive_confirmed"])
        self.assertEqual(self.lock(plan)["status"], "failed_closed")
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_activate", plan)
        self.assertEqual(self.admin.clicks.count("deactivate"), 1)

    def test_unknown_validation_failure_rolls_back_but_remains_indeterminate(self):
        self.public.validation_error = TimeoutError("public response unknown")
        plan = self.stage("plugin_activate")
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_activate", plan)
        self.assertFalse(self.admin.active)
        self.assertEqual(self.admin.clicks, ["activate", "deactivate"])
        self.assertEqual(self.lock(plan)["status"], "indeterminate")
        self.assertEqual(self.result(plan)["status"], "INDETERMINATE")
        self.assertTrue(self.result(plan)["rollback"]["inactive_confirmed"])

    def test_activation_click_exception_still_attempts_one_emergency_deactivation(self):
        self.admin.activate_then_fail = True
        self.admin.fail_activate = TimeoutError("click outcome unknown")
        plan = self.stage("plugin_activate")
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_activate", plan)
        self.assertFalse(self.admin.active)
        self.assertEqual(self.admin.clicks, ["activate", "deactivate"])
        self.assertEqual(self.lock(plan)["status"], "indeterminate")
        self.assertEqual(self.result(plan)["status"], "INDETERMINATE")

    def test_unconfirmed_emergency_deactivation_is_never_reported_as_successful(self):
        self.public.findings["passed"] = False
        self.admin.fail_deactivate = TimeoutError("deactivation unknown")
        plan = self.stage("plugin_activate")
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_activate", plan)
        result = self.result(plan)
        self.assertEqual(result["status"], "INDETERMINATE")
        self.assertEqual(self.lock(plan)["status"], "indeterminate")
        self.assertFalse(result["emergency_deactivated"])
        self.assertFalse(result["rollback"]["inactive_confirmed"])
        self.assertTrue(self.admin.active)


class TestDeactivation(Harness):
    def test_deactivate_keeps_exact_plugin_installed_and_records_no_delete(self):
        self.admin.active = True
        plan = self.stage("plugin_deactivate")
        self.commit("plugin_deactivate", plan)
        self.assertFalse(self.admin.active)
        self.assertTrue(self.admin.present)
        self.assertEqual(self.admin.clicks, ["deactivate"])
        result = self.result(plan)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertFalse(result["deleted"])
        self.assertTrue(result["after"]["present"])

    def test_deactivation_uncertainty_is_permanently_indeterminate(self):
        self.admin.active = True
        self.admin.fail_deactivate = TimeoutError("unknown")
        plan = self.stage("plugin_deactivate")
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_deactivate", plan)
        self.assertEqual(self.admin.clicks.count("deactivate"), 1)
        self.assertEqual(self.lock(plan)["status"], "indeterminate")
        self.assertEqual(self.result(plan)["status"], "INDETERMINATE")
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_deactivate", plan)
        self.assertEqual(self.admin.clicks.count("deactivate"), 1)


class MenuElement:
    def __init__(self, class_text):
        self.class_text = class_text

    def get_attribute(self, name):
        return self.class_text if name == "class" else None


class MenuLink:
    def __init__(self, title, href):
        self.title = title
        self.href = href

    def get_attribute(self, name):
        return self.href if name == "href" else None

    def inner_text(self):
        return self.title.upper()

    def text_content(self):
        return self.title


class MenuRoot:
    def __init__(self, *, duplicate=False):
        categories = sorted(deploy.EXPECTED_CATEGORY_IDS)
        products = sorted(deploy.EXPECTED_PRODUCT_IDS)
        self.categories = [MenuElement(f"frpdepot-acp-category-{value}")
                           for value in categories]
        self.products = [MenuElement(
            f"frpdepot-acp-product-{value} "
            f"frpdepot-acp-category-parent-{categories[min(index, len(categories) - 1)]}")
            for index, value in enumerate(products)]
        if duplicate:
            self.products.append(self.products[0])

    def query_selector_all(self, selector):
        if selector == "li > a":
            return [MenuLink(deploy.SHOP_TITLE, deploy.EXACT_ORIGIN + deploy.SHOP_PATH)]
        if selector == ".frpdepot-acp-category-item":
            return self.categories
        if selector == ".frpdepot-acp-product-item":
            return self.products
        raise AssertionError(f"unexpected menu selector {selector!r}")


class ContractPage:
    def __init__(self, *, missing_selector=None, duplicate=False, bad_guide=False):
        self.url = deploy.HOME_URL
        self.missing_selector = missing_selector
        self.duplicate = duplicate
        self.bad_guide = bad_guide
        self.navigations: list[str] = []

    def goto(self, url, wait_until=None, timeout=None):
        if timeout is None:
            raise AssertionError("navigation must be bounded")
        self.url = url
        self.navigations.append(url)

    def inner_text(self, selector, timeout=None):
        if selector != "body" or timeout is None:
            raise AssertionError("unexpected or unbounded body read")
        return "Healthy anonymous FRP Depot storefront content long enough to pass the blank gate."

    def content(self):
        if self.url in deploy.PRODUCT_URLS.values():
            if self.bad_guide:
                return deploy.HETRON_URL + deploy.DERAKANE_NEW_URL
            return deploy.DERAKANE_NEW_URL
        return "Healthy anonymous FRP Depot storefront document."

    def query_selector_all(self, selector):
        if self.url == deploy.HOME_URL and selector in (
                deploy.MAIN_MENU_SELECTOR, deploy.FOOTER_MENU_SELECTOR,
                deploy.HEADER_MOBILE_SELECTOR, deploy.FOOTER_MOBILE_SELECTOR):
            if selector == self.missing_selector:
                return []
            return [MenuRoot(duplicate=self.duplicate)]
        if self.url in deploy.PRODUCT_URLS.values():
            counts = {
                ".et_pb_blurb_1_tb_body h4.et_pb_module_header": 0,
                ".et_pb_blurb_0_tb_body h4.et_pb_module_header": 1,
                f'.et_pb_text_1_tb_body a[href="{deploy.INLINE_CTA_PATH}"]': 1,
            }
            return [object() for _ in range(counts.get(selector, 0))]
        if self.url == deploy.DERAKANE_PAGE_URL:
            counts = {
                "section[data-derakane-search]": 1,
                "section[data-derakane-search] h1": 1,
            }
            return [object() for _ in range(counts.get(selector, 0))]
        return []


class ContractResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status = status
        self.headers = headers or {}
        self._payload = payload

    def body(self):
        if self._payload is None:
            return b""
        return json.dumps(self._payload).encode("utf-8")

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


class ContractRequest:
    def get(self, url, **_options):
        if url == deploy.FNPT_STORE_API_URL:
            return ContractResponse(200, {
                "id": deploy.FNPT_PRODUCT_ID,
                "categories": [{"id": deploy.FNPT_TARGET_CATEGORY_ID}],
                "attributes": [{"name": "RESIN TYPE", "terms": [{"name": "Derakane 411"}]}],
            })
        if url.startswith(deploy.DERAKANE_REST_URL + "?"):
            return ContractResponse(200, {"total": 1, "groups": [{"name": "Derakane"}]})
        if url in {deploy.HETRON_ATTACHMENT_URL, deploy.HETRON_ATTACHMENT_QUERY_URL,
                   deploy.HETRON_DIRECT_PDF_URL}:
            return ContractResponse(404)
        raise AssertionError(f"unexpected public request {url!r}")


class TestAnonymousContract(unittest.TestCase):
    def test_contract_pins_all_four_surfaces_and_exact_guide_counts(self):
        self.assertEqual(deploy.VALIDATION_CONTRACT["product_pages"],
                         [deploy.PRODUCT_URLS[product_id]
                          for product_id in sorted(deploy.PRODUCT_URLS)])
        self.assertEqual(deploy.VALIDATION_CONTRACT["menus"],
                         ["desktop", "mobile", "footer", "footer_mobile"])
        self.assertEqual(deploy.VALIDATION_CONTRACT["required_category_ids"],
                         sorted(deploy.EXPECTED_CATEGORY_IDS))
        self.assertEqual(deploy.VALIDATION_CONTRACT["required_product_ids"],
                         sorted(deploy.EXPECTED_PRODUCT_IDS))
        self.assertTrue(deploy.VALIDATION_CONTRACT[
            "additional_nonempty_approved_descendant_categories_allowed"])
        self.assertFalse(deploy.VALIDATION_CONTRACT["duplicates_allowed"])
        self.assertEqual(deploy.VALIDATION_CONTRACT["shop_root"],
                         {"title": "Shop All", "path": "/products/"})
        self.assertTrue(deploy.VALIDATION_CONTRACT[
            "menu_category_product_bytes_identical"])
        self.assertTrue(deploy.VALIDATION_CONTRACT[
            "derakane_v2_page_and_rest_required"])
        self.assertTrue(deploy.VALIDATION_CONTRACT[
            "obsolete_hetron_attachment_and_direct_pdf_unavailable"])
        self.assertFalse(deploy.VALIDATION_CONTRACT["retry"])

    def test_public_page_passes_only_the_exact_anonymous_projection(self):
        raw = ContractPage()
        findings = deploy.PublicPage(raw, ContractRequest()).catalogue_findings()
        self.assertTrue(findings["passed"])
        self.assertEqual(raw.navigations,
                         [deploy.HOME_URL]
                         + [deploy.PRODUCT_URLS[product_id]
                            for product_id in sorted(deploy.PRODUCT_URLS)]
                         + [deploy.DERAKANE_PAGE_URL])
        self.assertEqual(set(findings["projections"]),
                         {"desktop", "footer", "mobile", "footer_mobile"})
        self.assertTrue(all(item["matches_expected"]
                            for item in findings["projections"].values()))
        self.assertEqual(set(findings["guides"]),
                         {str(product_id) for product_id in deploy.PRODUCT_URLS})
        self.assertTrue(all(item["passed"] for item in findings["guides"].values()))
        self.assertTrue(findings["fnpt"]["passed"])
        self.assertTrue(findings["derakane_v2"]["passed"])
        self.assertTrue(findings["hetron_unavailable"]["passed"])

    def test_missing_surface_duplicate_or_bad_guide_is_a_known_negative_result(self):
        pages = (
            ContractPage(missing_selector=deploy.HEADER_MOBILE_SELECTOR),
            ContractPage(duplicate=True),
            ContractPage(bad_guide=True),
        )
        for raw in pages:
            with self.subTest(raw=vars(raw)):
                self.assertFalse(deploy.PublicPage(
                    raw, ContractRequest()).catalogue_findings()["passed"])


class TestReceipts(Harness):
    def test_stage_and_success_receipts_name_exact_action_plan_and_hash(self):
        self.admin.active = True
        plan = self.stage("plugin_deactivate")
        staged = self.receipt_rows()
        self.assertEqual(len(staged), 1)
        self.assertEqual(set(staged[0]), {"ts", "action", "evidence"})
        self.assertEqual(staged[0]["action"], "catalogue_presentation_plugin_plan_staged")
        self.assertEqual(staged[0]["evidence"], str(plan))
        deploy.datetime.fromisoformat(staged[0]["ts"])

        self.commit("plugin_deactivate", plan)
        rows = self.receipt_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["action"],
                         "catalogue_presentation_plugin_committed_and_verified")
        plan_sha = self.read_json(plan)["sha256"]
        self.assertEqual(rows[1]["evidence"],
                         f"action=plugin_deactivate; sha256={plan_sha}")
        result = self.result(plan)
        self.assertEqual(Path(result["plan"]).resolve(), plan.resolve())
        self.assertEqual(result["plan_sha256"], plan_sha)
        self.assertEqual(result["plugin_file"], deploy.PLUGIN_FILE)
        self.assertFalse(result["retry"])

    def test_indeterminate_receipt_matches_permanent_result(self):
        self.admin.active = True
        self.admin.fail_deactivate = TimeoutError("unknown")
        plan = self.stage("plugin_deactivate")
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_deactivate", plan)
        row = self.receipt_rows()[-1]
        self.assertEqual(row["action"], "catalogue_presentation_plugin_indeterminate")
        result = self.result(plan)
        self.assertEqual(result["status"], "INDETERMINATE")
        self.assertEqual(result["action"], "plugin_deactivate")
        self.assertFalse(result["retry"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
