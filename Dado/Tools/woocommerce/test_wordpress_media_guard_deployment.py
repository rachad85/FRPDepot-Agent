from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).with_name("wordpress_media_guard_deployment_tool.py")
spec = importlib.util.spec_from_file_location("media_guard_deploy", MODULE_PATH)
tool = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(tool)


class FakeElement:
    def __init__(self, *, text="", classes="", children=None, attrs=None):
        self.text = text
        self.classes = classes
        self.children = children or {}
        self.attrs = attrs or {}
        self.clicked = 0
        self.files = []

    def get_attribute(self, name):
        return self.classes if name == "class" else self.attrs.get(name)

    def query_selector(self, selector):
        value = self.children.get(selector)
        return value[0] if isinstance(value, list) and value else value

    def query_selector_all(self, selector):
        value = self.children.get(selector)
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def inner_text(self):
        return self.text

    def click(self, **kwargs):
        self.clicked += 1

    def set_input_files(self, path, **kwargs):
        self.files.append(path)


class FakeLocator:
    def __init__(self, text):
        self.text = text

    def inner_text(self, **kwargs):
        return self.text


class FakePage:
    def __init__(self, row=None, update_rows=None):
        self.url = tool.PLUGINS_URL
        self.row = row
        self.update_rows = list(update_rows or [])
        self.handlers = {}
        self.guard_version = f"Version {tool.PLUGIN_VERSION}"
        self.guard_status = "Guard inactive"
        self.waits = []

    def goto(self, url, **kwargs):
        self.url = url

    def query_selector_all(self, selector):
        if selector == tool.ROW_SELECTOR:
            return [] if self.row is None else [self.row]
        if selector.startswith("tr.plugin-update-tr"):
            return self.update_rows
        return []

    def query_selector(self, selector):
        return None

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, value):
        self.waits.append(value)

    def on(self, event, callback):
        self.handlers[event] = callback

    def locator(self, selector):
        if selector == "#frpd-mg-version":
            return FakeLocator(self.guard_version)
        if selector == "#frpd-mg-status":
            return FakeLocator(self.guard_status)
        raise AssertionError(selector)


def fixed_row(active: bool, version: str = tool.PLUGIN_VERSION):
    def action_link(action):
        return FakeElement(attrs={"href": (
            f"{tool.PLUGINS_URL}?action={action}&plugin=frpdepot-media-mutation-guard%2F"
            "frpdepot-media-mutation-guard.php&plugin_status=all&paged=1&s=&_wpnonce=abcDEF1234"
        )})
    activate = action_link("activate")
    deactivate = action_link("deactivate")
    children = {
        tool.DEACTIVATE_SELECTOR if active else tool.ACTIVATE_SELECTOR:
            deactivate if active else activate,
    }
    return FakeElement(
        text=f"{tool.PLUGIN_NAME} Version {version}",
        classes="active" if active else "inactive",
        children=children,
    )


class ArtifactTests(unittest.TestCase):
    def test_pinned_artifact_is_exact_and_reproducible(self):
        value = tool.validate_artifact()
        self.assertEqual(value["sha256"], tool.ARTIFACT_SHA256)
        self.assertEqual(value["bytes"], tool.ARTIFACT_BYTES)
        self.assertEqual(tuple(value["members"]), tuple(sorted(tool.ARTIFACT_MEMBERS)))
        self.assertEqual(value["member_sha256"], tool.ARTIFACT_MEMBER_SHA256)

    def test_only_three_fixed_actions_exist(self):
        self.assertEqual(tool.ACTIONS, frozenset({"install_inactive", "activate", "deactivate"}))
        with self.assertRaises(SystemExit):
            tool.parser().parse_args(["stage", "--action", "replace"])

    def test_exact_approval_only(self):
        tool.require_approval("APPROVED")
        for value in ("approved", " APPROVED", "APPROVED ", "YES", ""):
            with self.assertRaises(tool.DeploymentError):
                tool.require_approval(value)


class ProjectionTests(unittest.TestCase):
    def test_absent_projection(self):
        admin = tool.AdminPage(FakePage())
        self.assertEqual(admin.read_row(allow_absent=True), tool.project_row(False, None, "", False))

    def test_active_and_inactive_rows(self):
        self.assertEqual(
            tool.AdminPage(FakePage(fixed_row(True))).read_row(),
            tool.project_row(True, True, tool.PLUGIN_VERSION, False),
        )
        self.assertEqual(
            tool.AdminPage(FakePage(fixed_row(False))).read_row(),
            tool.project_row(True, False, tool.PLUGIN_VERSION, False),
        )

    def test_ambiguous_state_refused(self):
        row = fixed_row(False)
        row.classes = "active inactive"
        with self.assertRaises(tool.DeploymentError):
            tool.AdminPage(FakePage(row)).read_row()

    def test_update_marker_refused_by_staging_contract(self):
        before = tool.project_row(True, False, tool.PLUGIN_VERSION, True)
        with self.assertRaises(tool.DeploymentError):
            tool.validate_before("activate", before)

    def test_action_state_contracts_are_exact(self):
        tool.validate_before("install_inactive", tool.project_row(False, None, "", False))
        tool.validate_before("activate", tool.project_row(True, False, tool.PLUGIN_VERSION, False))
        tool.validate_before("deactivate", tool.project_row(True, True, tool.PLUGIN_VERSION, False))
        with self.assertRaises(tool.DeploymentError):
            tool.validate_before("install_inactive", tool.project_row(True, False, tool.PLUGIN_VERSION, False))
        with self.assertRaises(tool.DeploymentError):
            tool.validate_before("activate", tool.project_row(True, True, tool.PLUGIN_VERSION, False))
        with self.assertRaises(tool.DeploymentError):
            tool.validate_before("deactivate", tool.project_row(True, False, tool.PLUGIN_VERSION, False))

    def test_guard_health_is_exact_and_js_clean(self):
        page = FakePage(fixed_row(True))
        proof = tool.AdminPage(page).verify_guard_health()
        self.assertEqual(proof["guard_active"], False)
        self.assertEqual(proof["javascript_errors"], 0)
        page.guard_status = "Guard active"
        with self.assertRaises(tool.IndeterminateError):
            tool.AdminPage(page).verify_guard_health()

    def test_guard_health_rejects_console_error(self):
        page = FakePage(fixed_row(True))
        original_goto = page.goto
        def goto_and_error(url, **kwargs):
            original_goto(url, **kwargs)
            if "console" in page.handlers:
                message = type("Message", (), {"type": "error"})()
                page.handlers["console"](message)
        page.goto = goto_and_error
        with self.assertRaises(tool.IndeterminateError):
            tool.AdminPage(page).verify_guard_health()

    def test_network_admin_and_other_admin_routes_are_refused(self):
        tool.assert_admin_url(tool.PLUGINS_URL, mode="plugins")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_admin_url("https://frpdepots.com/wp-admin/network/plugins.php", mode="state_result")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_admin_url("https://frpdepots.com/wp-admin/users.php", mode="state_result")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_admin_url(tool.PLUGINS_URL + "?action=activate", mode="plugins")


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patchers = [
            mock.patch.object(tool, "PLAN_DIR", root / "plans"),
            mock.patch.object(tool, "REGISTRY_KEY", root / "state" / "registry.key"),
            mock.patch.object(tool, "REGISTRY_DIR", root / "state" / "stages"),
            mock.patch.object(tool, "ATTEMPT_DIR", root / "state" / "attempts"),
            mock.patch.object(tool, "RESULT_DIR", root / "state" / "results"),
            mock.patch.object(tool, "RECEIPTS", root / "receipts.jsonl"),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.artifact = tool.validate_artifact()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_authenticated_immutable_plan_round_trip(self):
        before = tool.project_row(False, None, "", False)
        path, staged = tool.stage("install_inactive", before, self.artifact)
        loaded_path, loaded = tool.load_plan(str(path))
        self.assertEqual(loaded_path, path)
        self.assertEqual(loaded, staged)
        self.assertEqual(loaded["writes_if_committed"], ["one fixed plugin ZIP upload; plugin remains inactive"])
        self.assertTrue(tool.REGISTRY_KEY.is_file())
        self.assertEqual(len(tool.REGISTRY_KEY.read_bytes()), 32)

    def test_commit_requires_two_minutes_of_authorization_after_browser_preparation(self):
        now = tool.utc_now()
        with mock.patch.object(tool, "utc_now", return_value=now):
            with self.assertRaises(tool.DeploymentError):
                tool.assert_commit_execution_window({
                    "expires_utc": (now + tool.COMMIT_EXPIRY_MARGIN).isoformat()
                })
            tool.assert_commit_execution_window({
                "expires_utc": (now + tool.COMMIT_EXPIRY_MARGIN + tool.timedelta(seconds=1)).isoformat()
            })
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertLess(source.index("assert_commit_execution_window(plan)"),
                        source.index("exclusive_json(attempt_path(operation)"))

    def test_plan_tamper_refused(self):
        path, _ = tool.stage("install_inactive", tool.project_row(False, None, "", False), self.artifact)
        value = json.loads(path.read_text(encoding="ascii"))
        value["risk"] = "changed"
        path.write_text(json.dumps(value), encoding="ascii")
        with self.assertRaises(tool.DeploymentError):
            tool.load_plan(str(path))

    def test_registry_tamper_refused(self):
        path, plan = tool.stage("install_inactive", tool.project_row(False, None, "", False), self.artifact)
        registry = tool.stage_registry_path(plan["sha256"])
        value = json.loads(registry.read_text(encoding="ascii"))
        value["hmac_sha256"] = "0" * 64
        registry.write_text(json.dumps(value), encoding="ascii")
        with self.assertRaises(tool.DeploymentError):
            tool.load_plan(str(path))

    def test_stable_operation_ignores_plan_nonce(self):
        before = tool.project_row(False, None, "", False)
        first_path, first = tool.stage("install_inactive", before, self.artifact)
        second_path, second = tool.stage("install_inactive", before, self.artifact)
        self.assertNotEqual(first_path, second_path)
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["operation_sha256"], second["operation_sha256"])

    def test_each_action_discloses_one_write(self):
        cases = {
            "install_inactive": tool.project_row(False, None, "", False),
            "activate": tool.project_row(True, False, tool.PLUGIN_VERSION, False),
            "deactivate": tool.project_row(True, True, tool.PLUGIN_VERSION, False),
        }
        for action, before in cases.items():
            with self.subTest(action=action):
                _, plan = tool.stage(action, before, self.artifact)
                self.assertEqual(len(plan["writes_if_committed"]), 1)

    def test_bad_approval_refuses_before_plan_key_or_browser(self):
        args = argparse.Namespace(approval="approved", plan="missing")
        with mock.patch.object(tool, "load_plan") as load, mock.patch.object(tool, "admin_session") as session:
            with self.assertRaises(tool.DeploymentError):
                tool.command_commit(args)
        load.assert_not_called()
        session.assert_not_called()

    def test_pre_attempt_drift_refuses_without_burning_operation(self):
        before = tool.project_row(False, None, "", False)
        path, plan = tool.stage("install_inactive", before, self.artifact)

        class DriftAdmin:
            def goto_plugins(self): pass
            def read_row(self, allow_absent=True):
                return tool.project_row(True, False, tool.PLUGIN_VERSION, False)

        @contextlib.contextmanager
        def session(_purpose):
            yield DriftAdmin()

        with mock.patch.object(tool, "admin_session", session):
            with self.assertRaises(tool.DeploymentError):
                tool.command_commit(argparse.Namespace(approval="APPROVED", plan=str(path)))
        self.assertFalse(tool.attempt_path(plan["operation_sha256"]).exists())
        self.assertFalse(tool.result_path(plan["operation_sha256"]).exists())

    def test_verified_install_locks_before_one_write_and_replay_refuses(self):
        before = tool.project_row(False, None, "", False)
        path, plan = tool.stage("install_inactive", before, self.artifact)
        events = []

        class InstallAdmin:
            def goto_plugins(self): events.append("goto")
            def read_row(self, allow_absent=True): return before
            def prepare_install(self): events.append("prepare"); return object(), object()
            def execute_install(self, chooser, submit, artifact_raw):
                self_outer.assertEqual(hashlib.sha256(artifact_raw).hexdigest(), tool.ARTIFACT_SHA256)
                self_outer.assertTrue(tool.attempt_path(plan["operation_sha256"]).exists())
                events.append("write")
                return tool.expected_after("install_inactive")

        self_outer = self
        @contextlib.contextmanager
        def session(_purpose):
            yield InstallAdmin()

        with mock.patch.object(tool, "admin_session", session), mock.patch.object(tool, "print_json"):
            tool.command_commit(argparse.Namespace(approval="APPROVED", plan=str(path)))
        self.assertEqual(events, ["goto", "prepare", "write"])
        result = json.loads(tool.result_path(plan["operation_sha256"]).read_text(encoding="ascii"))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["writes"], 1)
        with mock.patch.object(tool, "admin_session", session), mock.patch.object(tool, "print_json"):
            with self.assertRaises(tool.DeploymentError):
                tool.command_commit(argparse.Namespace(approval="APPROVED", plan=str(path)))
        self.assertEqual(events, ["goto", "prepare", "write"])

    def test_exception_after_attempt_is_indeterminate_and_no_retry(self):
        before = tool.project_row(False, None, "", False)
        path, plan = tool.stage("install_inactive", before, self.artifact)

        class FailingAdmin:
            def goto_plugins(self): pass
            def read_row(self, allow_absent=True): return before
            def prepare_install(self): return object(), object()
            def execute_install(self, chooser, submit, artifact_raw): raise RuntimeError("submission uncertain")

        @contextlib.contextmanager
        def session(_purpose):
            yield FailingAdmin()

        with mock.patch.object(tool, "admin_session", session):
            with self.assertRaises(RuntimeError):
                tool.command_commit(argparse.Namespace(approval="APPROVED", plan=str(path)))
        result = json.loads(tool.result_path(plan["operation_sha256"]).read_text(encoding="ascii"))
        self.assertEqual(result["status"], "indeterminate_no_retry")
        self.assertTrue(tool.attempt_path(plan["operation_sha256"]).exists())


class SourceSurfaceTests(unittest.TestCase):
    def test_no_replace_delete_or_mail_write_call(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("update-from-upload-overwrite).click", source)
        self.assertNotIn("delete_plugin", source)
        self.assertNotIn("wp-json", source)
        self.assertNotIn("/products/", source)
        self.assertNotIn("send_mail", source)
        self.assertNotIn("requests.post", source)
        self.assertNotIn("requests.put", source)
        self.assertNotIn("requests.delete", source)

    def test_install_preparation_has_no_caller_selected_path(self):
        class Chooser:
            def __init__(self): self.value = None
            def set_input_files(self, value, **kwargs): self.value = value
        class Submit:
            def click(self, **kwargs): pass
        chooser = Chooser(); submit = Submit()
        nonce = FakeElement(attrs={"value": "abcDEF1234"})
        form = FakeElement(
            attrs={"action": "update.php?action=upload-plugin"},
            children={
                'input[type="file"][name="pluginzip"]': [chooser],
                "#install-plugin-submit": [submit],
                'input[type="hidden"][name="_wpnonce"]': [nonce],
            },
        )
        fake = FakePage()
        fake.url = tool.UPLOAD_URL
        fake.query_selector_all = lambda selector: [form] if selector == "form#plugin-upload-form" else []
        admin = tool.AdminPage(fake)
        got_chooser, got_submit = admin.prepare_install()
        self.assertIs(got_chooser, chooser)
        self.assertIs(got_submit, submit)

    def test_action_href_and_result_routes_are_closed(self):
        valid = (f"{tool.PLUGINS_URL}?action=deactivate&plugin=frpdepot-media-mutation-guard%2F"
                 "frpdepot-media-mutation-guard.php&plugin_status=all&paged=1&s=&_wpnonce=abcDEF1234")
        tool.assert_state_action_url(valid, "deactivate")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_state_action_url(valid.replace("action=deactivate", "action=delete-selected"), "deactivate")
        tool.assert_admin_url(f"{tool.PLUGINS_URL}?deactivate=true&plugin_status=all&paged=1&s=", mode="state_result")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_admin_url(f"{tool.PLUGINS_URL}?action=delete-selected&checked[]=akismet.php", mode="state_result")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_admin_url(f"{tool.ORIGIN}/wp-admin/update.php?action=upload-plugin&unrelated=1", mode="install_result")

    def test_artifact_payload_is_exact_bytes_and_source_never_uploads_a_path(self):
        artifact, raw = tool.validate_artifact_payload()
        self.assertEqual(artifact, tool.validate_artifact())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), tool.ARTIFACT_SHA256)
        self.assertEqual(len(raw), tool.ARTIFACT_BYTES)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("set_input_files(str(", source)
        self.assertIn('"buffer": artifact_raw', source)

    @staticmethod
    def temp_path():
        return tempfile.gettempdir()


if __name__ == "__main__":
    unittest.main(verbosity=2)
