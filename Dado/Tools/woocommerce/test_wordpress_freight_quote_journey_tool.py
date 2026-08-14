"""Offline fake-UI tests for the fixed freight-quote journey deployer.

No test opens Playwright, CDP, a browser, a socket, or a live WordPress system.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
from datetime import timedelta
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
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wordpress_freight_quote_journey_tool as deploy  # noqa: E402


class Element:
    def __init__(self, *, text="", attrs=None, query=None, query_all=None, click=None, files=None):
        self.text = text
        self.attrs = attrs or {}
        self._query = query or {}
        self._query_all = query_all or {}
        self._click = click
        self._files = files

    def get_attribute(self, name):
        return self.attrs.get(name)

    def inner_text(self):
        return self.text

    def query_selector(self, selector):
        if selector in self._query:
            return self._query[selector]
        values = self._query_all.get(selector, [])
        return values[0] if values else None

    def query_selector_all(self, selector):
        return list(self._query_all.get(selector, []))

    def click(self, timeout=None):
        if timeout is None:
            raise AssertionError("click lacks bounded timeout")
        if self._click:
            self._click()

    def set_input_files(self, path, timeout=None):
        if timeout is None:
            raise AssertionError("file selection lacks bounded timeout")
        if self._files:
            self._files(path)


class FakeRawPage:
    """WordPress DOM double containing another plugin and reachable Delete links."""

    def __init__(self, version=deploy.FROM_VERSION, active=True):
        self.version = version
        self.active = active
        self.url = deploy.PLUGINS_URL
        self.navigations = []
        self.clicks = []
        self.uploads = []
        self.selectors = []
        self.comparison_name = deploy.PLUGIN_NAME
        self.comparison_version = deploy.TO_VERSION
        self.comparison_tables = 1
        self.overwrite_links = 1
        self.duplicate_row = False
        self.status = recovery_status()
        self.other_plugin_present = True

    def goto(self, url, wait_until=None, timeout=None):
        if timeout is None:
            raise AssertionError("navigation lacks bounded timeout")
        self.navigations.append(url)
        self.url = url

    def wait_for_load_state(self, state, timeout=None):
        if timeout is None:
            raise AssertionError("load wait lacks bounded timeout")

    def query_selector(self, selector):
        values = self.query_selector_all(selector)
        return values[0] if values else None

    def query_selector_all(self, selector):
        self.selectors.append(selector)
        path = urlsplit(self.url).path
        if path == "/wp-admin/plugins.php":
            if selector == deploy.ROW_SELECTOR:
                rows = [self._plugin_row()]
                return rows * (2 if self.duplicate_row else 1)
            if selector == 'tr[data-plugin="akismet/akismet.php"]':
                return [Element(
                    attrs={"class": "active", "data-plugin": "akismet/akismet.php"},
                    query={".row-actions .delete a": Element(click=self._other_plugin)},
                )]
            if selector == deploy.UPDATE_ROW_SELECTOR:
                return []
            return []
        if path == "/wp-admin/plugin-install.php":
            if selector == deploy.UPLOAD_INPUT_SELECTOR:
                return [Element(files=self._set_files)]
            if selector == deploy.UPLOAD_SUBMIT_SELECTOR:
                return [Element(click=self._submit)]
            return []
        if path == "/wp-admin/update.php":
            if selector == deploy.COMPARISON_SELECTOR:
                return [self._comparison_table() for _ in range(self.comparison_tables)]
            if selector == deploy.OVERWRITE_SELECTOR:
                return [Element(click=self._overwrite) for _ in range(self.overwrite_links)]
            return []
        if path == "/wp-admin/tools.php":
            if selector == deploy.STATUS_SELECTOR:
                return [Element(attrs={"data-projection": json.dumps(self.status)})]
            if selector == deploy.APPLY_FORM_SELECTOR:
                return [Element()] if self.status.get("status") == "not_applied" else []
            if selector == deploy.APPLY_BUTTON_SELECTOR:
                return [Element(click=self._apply)] if self.status.get("status") == "not_applied" else []
            if selector == deploy.ROLLBACK_FORM_SELECTOR:
                return [Element()]
            if selector == deploy.ROLLBACK_BUTTON_SELECTOR:
                return [Element(click=self._rollback)]
            return []
        return []

    def _plugin_row(self):
        def activate():
            self.clicks.append("fixed:activate")
            self.active = True

        def deactivate():
            self.clicks.append("fixed:deactivate")
            self.active = False

        def forbidden_delete():
            self.clicks.append("fixed:delete")
            raise AssertionError("Delete was reached")

        action_selector = deploy.DEACTIVATE_SELECTOR if self.active else deploy.ACTIVATE_SELECTOR
        action = Element(click=deactivate if self.active else activate)
        # The fake has a Delete link, but the row exposes it only to its exact selector.
        query = {
            action_selector: action,
            deploy.VERSION_SELECTOR: Element(text=f"Version {self.version} | By FRP Depot"),
            ".row-actions .delete a": Element(click=forbidden_delete),
        }
        return Element(attrs={
            "class": "active" if self.active else "inactive",
            "data-plugin": deploy.PLUGIN_FILE,
        }, query=query)

    def _comparison_table(self):
        def row(label, current, uploaded):
            cells = [Element(text=label), Element(text=current), Element(text=uploaded)]
            return Element(query_all={"td": cells})
        rows = [
            row("Plugin name", deploy.PLUGIN_NAME, self.comparison_name),
            row("Version", self.version, self.comparison_version),
        ]
        return Element(query_all={"tr": rows})

    def _set_files(self, path):
        self.uploads.append(path)

    def _submit(self):
        self.clicks.append("upload:submit")
        self.url = f"{deploy.EXACT_ORIGIN}/wp-admin/update.php?action=upload-plugin"

    def _overwrite(self):
        self.clicks.append("upload:overwrite")
        self.version = self.comparison_version

    def _apply(self):
        self.clicks.append("status:apply")
        self.status = applied_status()

    def _rollback(self):
        self.clicks.append("status:rollback")
        self.status = rolled_back_status(self.status)

    def _other_plugin(self):
        self.clicks.append("other-plugin")
        raise AssertionError("unrelated plugin was reached")


class FakeAdmin:
    """High-level command double; each side effect checks lane and attempt lock order."""

    def __init__(self, harness):
        self.harness = harness
        self.version = deploy.FROM_VERSION
        self.active = True
        self.events = []
        self.status = recovery_status()
        self.next_deployment_id = "a" * 32
        self.rollback_drift = False
        self.activate_mode = "normal"
        self.status_error = False

    def _side_effect(self, name):
        self.events.append(name)
        self.harness.assertTrue(self.harness.lane_held)
        plan = self.harness.active_plan
        self.harness.assertIsNotNone(plan)
        operation = self.harness.operation
        lock = (deploy.plan_rollback_lock(plan) if operation == "rollback"
                else deploy.plan_apply_lock(plan))
        self.harness.assertTrue(lock.exists(), f"{name} happened before one-attempt lock")

    def goto_plugins(self):
        self.events.append("goto_plugins")

    def read_row(self):
        return deploy.project_row(True, self.active, self.version, False)

    def deactivate(self, version):
        self._side_effect(f"deactivate:{version}")
        self.harness.assertEqual(version, self.version)
        self.active = False
        return self.read_row()

    def activate(self, version):
        self._side_effect(f"activate:{version}")
        self.harness.assertEqual(version, self.version)
        if version == deploy.TO_VERSION and self.activate_mode == "inactive_error":
            raise deploy.DeploymentError("forced activation failure while inactive")
        self.active = True
        if version == deploy.TO_VERSION:
            self.status = applied_status(self.next_deployment_id)
        if version == deploy.TO_VERSION and self.activate_mode == "active_error":
            raise deploy.DeploymentError("forced activation response failure while active")
        return self.read_row()

    def replace(self, artifact, version, *, preserve_active=False):
        self._side_effect(f"replace:{version}")
        self.harness.assertEqual(self.active, bool(preserve_active))
        expected = deploy.ARTIFACT_PATH if version == deploy.TO_VERSION else deploy.ROLLBACK_ARTIFACT_PATH
        self.harness.assertEqual(Path(artifact).resolve(), expected.resolve())
        self.version = version
        return self.read_row()

    def read_status(self):
        self.events.append("read_status")
        if self.status_error:
            raise deploy.DeploymentError("forced unavailable status projection")
        return json.loads(json.dumps(self.status))

    def click_fixed_apply(self, before):
        self._side_effect("fixed_apply")
        deploy.require_recovery_source_status(before)
        self.status = applied_status(self.next_deployment_id)
        deploy.require_applied_status(self.status)
        return json.loads(json.dumps(self.status))

    def click_internal_rollback(self, before):
        self._side_effect("internal_rollback")
        if self.rollback_drift:
            raise deploy.RollbackDriftError("ROLLBACK_BLOCKED_DRIFT")
        self.status = rolled_back_status(before)
        return json.loads(json.dumps(self.status))


def applied_status(deployment_id="a" * 32):
    return {
        "spec_sha256": deploy.SPECIFICATION_SHA256,
        "status": "applied",
        "deployment_id": deployment_id,
        "source_form_id": deploy.SOURCE_FORM_ID,
        "source_notification_name_match": True,
        "route_sha256": "1" * 64,
        "form_id": 12,
        "form_owned": True,
        "form_sha256": "2" * 64,
        "page_id": 34,
        "page_owned": True,
        "page_sha256": "3" * 64,
        "contact_id": deploy.CONTACT_ID,
        "contact_new_count": 1,
        "contact_old_count": 0,
        "contact_sha256": "4" * 64,
        "form_backup_present": True,
        "quote_page_backup_present": True,
        "contact_backup_present": True,
        "route_backup_present": True,
        "receipt_count": deploy.MIN_APPLY_RECEIPTS,
        "receipt_schema_valid": True,
        "receipt_chain_valid": True,
        "receipt_append_only": True,
        "receipt_head_sha256": "5" * 64,
        "apply_receipt_head_sha256": "5" * 64,
        "rollback_drift_free": True,
        "rollback_blocked_artifact": "",
        "form_before_sha256": "6" * 64,
        "quote_page_before_sha256": "7" * 64,
        "contact_before_sha256": "8" * 64,
        "privacy": dict(deploy.PRIVACY_STATUS),
    }


def recovery_status():
    empty = deploy.digest_for(None)
    return {
        "spec_sha256": deploy.SPECIFICATION_SHA256,
        "status": "not_applied",
        "deployment_id": "0" * 32,
        "source_form_id": deploy.SOURCE_FORM_ID,
        "source_notification_name_match": False,
        "route_sha256": empty,
        "form_id": 0,
        "form_owned": False,
        "form_sha256": empty,
        "page_id": 0,
        "page_owned": False,
        "page_sha256": empty,
        "contact_id": deploy.CONTACT_ID,
        "contact_new_count": 0,
        "contact_old_count": 1,
        "contact_sha256": "4" * 64,
        "form_backup_present": False,
        "quote_page_backup_present": False,
        "contact_backup_present": False,
        "route_backup_present": False,
        "receipt_count": 0,
        "receipt_schema_valid": False,
        "receipt_chain_valid": False,
        "receipt_append_only": False,
        "receipt_head_sha256": empty,
        "apply_receipt_head_sha256": empty,
        "rollback_drift_free": False,
        "rollback_blocked_artifact": "",
        "form_before_sha256": empty,
        "quote_page_before_sha256": empty,
        "contact_before_sha256": empty,
        "privacy": dict(deploy.PRIVACY_STATUS),
    }


def rolled_back_status(before):
    after = json.loads(json.dumps(before))
    after.update({
        "status": "rolled_back",
        "form_owned": False,
        "form_sha256": before["form_before_sha256"],
        "page_owned": False,
        "page_sha256": before["quote_page_before_sha256"],
        "contact_new_count": 0,
        "contact_old_count": 1,
        "contact_sha256": before["contact_before_sha256"],
        "receipt_count": before["receipt_count"] + 7,
        "apply_receipt_head_sha256": before["receipt_head_sha256"],
        "receipt_head_sha256": "9" * 64,
    })
    return after


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="freight-journey-deployer-tests-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.plan_dir = self.tmp / "plans"
        self.plan_dir.mkdir()
        self.receipts = self.tmp / "receipts.jsonl"
        self.lane_held = False
        self.lane_entries = 0
        self.admin_opens = 0
        self.events = []
        self.active_plan = None
        self.operation = "apply"
        self.admin = FakeAdmin(self)

        @contextlib.contextmanager
        def non_reentrant_lane(lane, *, purpose, **_kwargs):
            self.assertEqual(lane, "wordpress")
            self.assertFalse(self.lane_held, "command tried to re-acquire non-reentrant lane")
            self.lane_entries += 1
            self.lane_held = True
            self.events.append("lane_enter")
            try:
                yield
            finally:
                self.events.append("lane_exit")
                self.lane_held = False

        @contextlib.contextmanager
        def fake_admin_session():
            self.admin_opens += 1
            self.assertTrue(self.lane_held)
            yield self.admin

        for patcher in (
            mock.patch.object(deploy, "PLAN_DIR", self.plan_dir),
            mock.patch.object(deploy, "RECEIPTS", self.receipts),
            mock.patch.object(deploy, "ui_browser_lock", non_reentrant_lane),
            mock.patch.object(deploy, "admin_session", fake_admin_session),
            mock.patch.object(deploy, "ENFORCE_PLUGIN_CONTRACT", False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def stage(self):
        before = set(self.plan_dir.glob("*.json"))
        with contextlib.redirect_stdout(io.StringIO()):
            deploy.command_stage(argparse.Namespace())
        created = set(self.plan_dir.glob("*.json")) - before
        self.assertEqual(len(created), 1)
        plan = created.pop()
        self.active_plan = plan
        return plan

    def apply(self, plan):
        self.active_plan = Path(plan)
        self.operation = "apply"
        self.admin.next_deployment_id = self.read_json(plan)["nonce"]
        with contextlib.redirect_stdout(io.StringIO()):
            deploy.command_apply(argparse.Namespace(plan=str(plan)))

    def rollback(self, plan):
        self.active_plan = Path(plan)
        self.operation = "rollback"
        with contextlib.redirect_stdout(io.StringIO()):
            deploy.command_rollback(argparse.Namespace(plan=str(plan)))

    @staticmethod
    def read_json(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))


class FixedCapabilityTests(unittest.TestCase):
    def test_exact_three_commands_and_no_approval_parameter(self):
        self.assertEqual(deploy.COMMANDS, ("stage", "apply", "rollback"))
        parser = deploy.build_parser()
        choices = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices
        self.assertEqual(set(choices), set(deploy.COMMANDS))
        self.assertEqual(vars(parser.parse_args(["stage"]))["command"], "stage")
        parsed = parser.parse_args(["apply", "--plan", "x"])
        self.assertEqual((parsed.command, parsed.plan), ("apply", "x"))
        with self.assertRaises(SystemExit):
            parser.parse_args(["apply", "--plan", "x", "--approval", "APPROVED"])

    def test_fixed_identity_cdp_versions_paths_and_selectors(self):
        self.assertEqual(deploy.CDP_ENDPOINT, "http://127.0.0.1:9229")
        self.assertEqual(deploy.EXACT_ORIGIN, "https://frpdepots.com")
        self.assertEqual((deploy.TO_VERSION, deploy.FROM_VERSION, deploy.ROLLBACK_VERSION),
                         ("2.0.3", "1.0.1", "1.0.1"))
        self.assertEqual(deploy.ROW_SELECTOR,
                         f'tr[data-plugin="{deploy.PLUGIN_FILE}"]:not(.plugin-update-tr)')
        self.assertEqual(deploy.UPLOAD_INPUT_SELECTOR, 'input[type="file"][name="pluginzip"]')
        self.assertEqual(deploy.UPLOAD_SUBMIT_SELECTOR, "#install-plugin-submit")
        self.assertEqual(deploy.OVERWRITE_SELECTOR, "a.update-from-upload-overwrite")

    def test_only_fixed_admin_paths_are_allowed(self):
        self.assertEqual(deploy.ALLOWED_ADMIN_PATHS, frozenset({
            "/wp-admin/plugins.php", "/wp-admin/plugin-install.php", "/wp-admin/update.php",
            "/wp-admin/tools.php", "/wp-admin/admin-post.php",
        }))
        for path in ("/wp-admin/plugin-editor.php", "/wp-admin/post.php",
                     "/wp-admin/edit.php?post_type=shop_order", "/checkout/", "/wp-json/"):
            with self.subTest(path=path), self.assertRaises(deploy.DeploymentError):
                deploy.assert_admin_url(deploy.EXACT_ORIGIN + path)

    def test_source_has_no_generic_browser_delete_editor_order_payment_or_send_path(self):
        source = Path(deploy.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        doc_lines = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and ast.get_docstring(node, clean=False):
                item = node.body[0]
                doc_lines.update(range(item.lineno, (item.end_lineno or item.lineno) + 1))
        code = "\n".join("" if n in doc_lines or line.lstrip().startswith("#") else line
                         for n, line in enumerate(source.splitlines(), 1))
        for needle in ("browser.close", ".launch(", "launch_persistent_context", ".fill(",
                       ".type(", "delete a", "plugin-editor", "/wp-admin/post.php", "shop_order",
                       "orders.php", "payment", "send_keys", "gform_submit", "wp-json"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, code)


class ArtifactAndPlanTests(Harness):
    def test_fixed_artifact_hashes_sizes_members_and_reviewed_contract(self):
        forward = deploy.verify_artifact()
        rollback = deploy.verify_artifact(rollback=True)
        self.assertEqual((forward["sha256"], forward["bytes"]),
                         (deploy.ARTIFACT_SHA256, deploy.ARTIFACT_BYTES))
        self.assertEqual((rollback["sha256"], rollback["bytes"]),
                         ("fe6fa440ea3a08169bf568ae0fbb06f666ad71c1110e58f9b2b6bb0acc8be6cb", 31216))
        self.assertEqual(len(forward["members"]), 5)
        self.assertEqual(len(rollback["members"]), 4)
        self.assertEqual(deploy.ARTIFACT_MEMBER_SHA256[
            f"{deploy.PLUGIN_SLUG}/ups-allowlist.json"], deploy.BASELINE_ALLOWLIST_SHA256)

    def test_stage_plan_is_24h_immutable_hash_bound_and_closed(self):
        plan_path = self.stage()
        plan = self.read_json(plan_path)
        created = deploy.datetime.fromisoformat(plan["created_utc"])
        expires = deploy.datetime.fromisoformat(plan["expires_utc"])
        self.assertEqual(expires - created, timedelta(hours=24))
        saved = plan.pop("sha256")
        self.assertEqual(saved, deploy.digest_for(plan))
        self.assertEqual(set(plan), deploy.PLAN_KEYS)
        self.assertEqual(plan["action"], deploy.ACTION)
        self.assertEqual(set(plan["plugin_before"]), deploy.ROW_KEYS)
        self.assertFalse(os.stat(plan_path).st_mode & stat.S_IWUSR)
        self.assertNotIn("approval", json.dumps(plan).lower())

    def test_plan_pins_exact_artifacts_and_validation_privacy(self):
        plan = self.read_json(self.stage())
        self.assertEqual(plan["new_artifact"], deploy.verify_artifact())
        self.assertEqual(plan["rollback_artifact"], deploy.verify_artifact(rollback=True))
        validation = plan["validation_contract"]
        self.assertEqual(validation["privacy"], deploy.PRIVACY_STATUS)
        self.assertEqual(validation["separate_backup_keys"], list(deploy.BACKUP_STATUS_KEYS))
        self.assertTrue(validation["receipt_hash"].startswith("sha256"))
        self.assertTrue(validation["rollback_drift_free_required"])

    def test_tampered_or_expired_plan_refuses(self):
        plan_path = self.stage()
        value = self.read_json(plan_path)
        os.chmod(plan_path, stat.S_IREAD | stat.S_IWRITE)
        value["origin"] = "https://example.test"
        plan_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError):
            deploy.load_plan(str(plan_path))

    def test_schema_four_2_0_2_plan_is_permanently_refused(self):
        plan_path = self.stage()
        value = self.read_json(plan_path)
        os.chmod(plan_path, stat.S_IREAD | stat.S_IWRITE)
        value["schema_version"] = 4
        value["tool_version"] = "2.0.3"
        value["new_artifact"]["version"] = "2.0.2"
        core = {key: value[key] for key in value if key != "sha256"}
        value["sha256"] = deploy.digest_for(core)
        plan_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(deploy.DeploymentError, "identity or contract changed"):
            deploy.load_plan(str(plan_path))

    def test_newer_valid_plan_permanently_supersedes_older_plan(self):
        first = self.stage()
        second = self.stage()
        with self.assertRaisesRegex(deploy.DeploymentError, "newer immutable"):
            deploy.load_plan(str(first))
        loaded_path, loaded = deploy.load_plan(str(second))
        self.assertEqual(loaded_path, second.resolve())
        self.assertEqual(loaded["sha256"], self.read_json(second)["sha256"])

        # Starting the newer attempt never makes an older plan eligible again.
        deploy.write_attempt_lock(deploy.plan_apply_lock(second), loaded, "apply")
        with self.assertRaisesRegex(deploy.DeploymentError, "newer immutable"):
            deploy.load_plan(str(first))

        # Supersession closes apply only. A plan that entered apply remains
        # loadable for the separately locked rollback path.
        first_plan = self.read_json(first)
        deploy.write_attempt_lock(deploy.plan_apply_lock(first), first_plan, "apply")
        rollback_path, rollback_plan = deploy.load_plan(
            str(first), allow_expired_for_rollback=True
        )
        self.assertEqual(rollback_path, first.resolve())
        self.assertEqual(rollback_plan["sha256"], first_plan["sha256"])


class StatusAndReceiptTests(Harness):
    def test_status_requires_privacy_separate_backups_and_hash_chain(self):
        status = applied_status()
        deploy.require_applied_status(status)
        for key in (*deploy.BACKUP_STATUS_KEYS, "receipt_chain_valid", "receipt_append_only"):
            broken = applied_status()
            broken[key] = False
            with self.subTest(key=key), self.assertRaises(deploy.DeploymentError):
                deploy.require_applied_status(broken)
        broken = applied_status()
        broken["privacy"] = {**deploy.PRIVACY_STATUS, "recipient_values_projected": True}
        with self.assertRaises(deploy.DeploymentError):
            deploy._validate_status_types(broken)

    def test_local_receipts_are_append_only_hash_chained_and_tamper_evident(self):
        plan_path = self.stage()
        plan = self.read_json(plan_path)
        first_count, first_head = deploy.validate_receipt_chain(plan["nonce"])
        self.assertEqual(first_count, 1)
        row = deploy.append_receipt(plan, "verify", "journey", plan["nonce"],
                                    "a" * 64, "b" * 64, "OK")
        count, head = deploy.validate_receipt_chain(plan["nonce"])
        self.assertEqual(count, 2)
        self.assertEqual(row["previous_receipt_sha256"], first_head)
        self.assertEqual(head, row["receipt_sha256"])
        rows = self.receipts.read_text(encoding="utf-8").splitlines()
        changed = json.loads(rows[0]); changed["after_sha256"] = "c" * 64
        rows[0] = json.dumps(changed)
        self.receipts.write_text("\n".join(rows) + "\n", encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError):
            deploy.validate_receipt_chain(plan["nonce"])

    def test_rollback_verification_requires_exact_before_hashes_and_chain_continuity(self):
        before = applied_status()
        after = rolled_back_status(before)
        deploy.require_rolled_back_status(before, after)
        for key, value in (("contact_sha256", "f" * 64), ("form_sha256", "f" * 64),
                           ("page_sha256", "f" * 64),
                           ("apply_receipt_head_sha256", "f" * 64)):
            drifted = dict(after); drifted[key] = value
            with self.subTest(key=key), self.assertRaises(deploy.DeploymentError):
                deploy.require_rolled_back_status(before, drifted)


class FakeUiRouteTests(unittest.TestCase):
    def test_exact_replace_route_clicks_only_fixed_controls(self):
        raw = FakeRawPage(active=True)
        admin = deploy.AdminPage(raw)
        admin.goto_plugins()
        admin.replace(deploy.ARTIFACT_PATH, deploy.TO_VERSION, preserve_active=True)
        self.assertEqual(raw.navigations, [
            deploy.PLUGINS_URL, deploy.UPLOAD_URL, deploy.PLUGINS_URL,
        ])
        self.assertEqual(raw.uploads, [str(deploy.ARTIFACT_PATH)])
        self.assertEqual(raw.clicks, ["upload:submit", "upload:overwrite"])
        self.assertTrue(raw.other_plugin_present)
        self.assertNotIn("fixed:delete", raw.clicks)
        self.assertNotIn("other-plugin", raw.clicks)
        self.assertFalse(any("editor" in url or "shop_order" in url for url in raw.navigations))
        self.assertTrue(raw.active)

    def test_exact_plugin_deactivate_activate_and_no_other_plugin_or_delete(self):
        raw = FakeRawPage(active=True)
        admin = deploy.AdminPage(raw)
        admin.deactivate(deploy.FROM_VERSION)
        admin.activate(deploy.FROM_VERSION)
        self.assertEqual(raw.clicks, ["fixed:deactivate", "fixed:activate"])
        self.assertNotIn("fixed:delete", raw.clicks)

    def test_update_comparison_must_be_one_exact_name_and_version(self):
        for attribute, value in (("comparison_name", "Other"),
                                 ("comparison_version", "9.9.9"),
                                 ("comparison_tables", 2), ("overwrite_links", 2)):
            raw = FakeRawPage(active=True)
            setattr(raw, attribute, value)
            admin = deploy.AdminPage(raw)
            with self.subTest(attribute=attribute), self.assertRaises(deploy.DeploymentError):
                admin.replace(deploy.ARTIFACT_PATH, deploy.TO_VERSION, preserve_active=True)
            self.assertNotIn("upload:overwrite", raw.clicks)
            self.assertNotIn("fixed:delete", raw.clicks)
            self.assertTrue(raw.active)

    def test_ambiguous_plugin_row_refuses_without_click(self):
        raw = FakeRawPage(active=True); raw.duplicate_row = True
        admin = deploy.AdminPage(raw)
        with self.assertRaises(deploy.DeploymentError):
            admin.read_row()
        self.assertEqual(raw.clicks, [])

    def test_status_rollback_uses_exact_control_and_verifies_drift_projection(self):
        raw = FakeRawPage(active=True)
        raw.status = applied_status()
        admin = deploy.AdminPage(raw)
        before = admin.read_status()
        after = admin.click_internal_rollback(before)
        self.assertEqual(raw.clicks, ["status:rollback"])
        self.assertEqual(after["status"], "rolled_back")


class BrowserAttachmentTests(unittest.TestCase):
    class Page:
        def __init__(self):
            self.closed = False

        def is_closed(self):
            return self.closed

        def close(self):
            self.closed = True

    class Context:
        def __init__(self, page):
            self.page = page
            self.new_page_calls = 0

        def new_page(self):
            self.new_page_calls += 1
            return self.page

    def test_one_owned_page_is_created_and_closed(self):
        page = self.Page()
        context = self.Context(page)
        with deploy.owned_admin_page(context) as selected:
            self.assertIs(page, selected)
            self.assertFalse(page.closed)
        self.assertEqual(1, context.new_page_calls)
        self.assertTrue(page.closed)

    def test_owned_page_closes_after_failure_without_touching_other_tabs(self):
        page = self.Page()
        context = self.Context(page)
        with self.assertRaisesRegex(RuntimeError, "fixture"):
            with deploy.owned_admin_page(context):
                raise RuntimeError("fixture")
        self.assertTrue(page.closed)


class CommandOrderingAndRollbackTests(Harness):
    def test_apply_holds_lane_once_before_attempt_and_exact_write_order(self):
        plan = self.stage()
        # Stage held it once; reset observations for apply.
        self.lane_entries = 0; self.events.clear(); self.admin.events.clear()
        original = deploy.write_attempt_lock

        def watched(path, value, operation):
            self.events.append("attempt_lock")
            self.assertTrue(self.lane_held)
            original(path, value, operation)

        with mock.patch.object(deploy, "write_attempt_lock", watched):
            self.apply(plan)
        self.assertEqual(self.lane_entries, 1)
        self.assertLess(self.events.index("lane_enter"), self.events.index("attempt_lock"))
        self.assertEqual(self.admin.events, [
            "goto_plugins", f"replace:{deploy.TO_VERSION}", "read_status", "fixed_apply",
        ])
        self.assertEqual((self.admin.version, self.admin.active), (deploy.TO_VERSION, True))
        self.assertTrue(deploy.plan_apply_lock(plan).exists())


    def test_replay_lock_allows_one_apply_attempt_only(self):
        plan = self.stage()
        self.apply(plan)
        events = list(self.admin.events)
        with self.assertRaises(deploy.DeploymentError):
            self.apply(plan)
        self.assertEqual(self.admin.events, events)

    def test_busy_lane_is_free_refusal_no_browser_attempt_or_plan_burn(self):
        plan = self.stage()
        opens = self.admin_opens

        @contextlib.contextmanager
        def busy(*_args, **_kwargs):
            raise deploy.UiLaneBusy("busy")
            yield

        with mock.patch.object(deploy, "ui_browser_lock", busy):
            with self.assertRaises(deploy.UiLaneBusy):
                self.apply(plan)
        self.assertEqual(self.admin_opens, opens)
        self.assertFalse(deploy.plan_apply_lock(plan).exists())
        self.assertFalse(deploy.plan_result(plan).exists())

    def test_clean_rollback_restores_original_projection_in_exact_order(self):
        plan = self.stage()
        self.apply(plan)
        self.admin.events.clear(); self.lane_entries = 0
        self.rollback(plan)
        self.assertEqual(self.lane_entries, 1)
        self.assertEqual(self.admin.events, [
            "goto_plugins", "read_status", "internal_rollback", "goto_plugins",
            "replace:1.0.1",
        ])
        self.assertEqual((self.admin.version, self.admin.active), ("1.0.1", True))
        self.assertTrue(deploy.plan_rollback_lock(plan).exists())

    def test_failed_precheck_binary_only_rollback_restores_source_without_content_route(self):
        plan = self.stage()
        self.admin.version = deploy.TO_VERSION
        self.admin.active = True
        self.admin.status = recovery_status()
        self.admin.status["contact_old_count"] = 0
        self.admin.status["contact_new_count"] = 0
        self.admin.status["contact_sha256"] = "4" * 64
        deploy.write_attempt_lock(deploy.plan_apply_lock(plan), self.read_json(plan), "apply")
        self.admin.events.clear(); self.lane_entries = 0
        self.rollback(plan)
        self.assertEqual(self.lane_entries, 1)
        self.assertEqual(self.admin.events, ["goto_plugins", "read_status", "goto_plugins", "replace:1.0.1"])
        self.assertEqual((self.admin.version, self.admin.active), ("1.0.1", True))
        result = self.read_json(deploy.plan_rollback_result(plan))
        self.assertEqual(result["status"], "ROLLED_BACK_AND_VERIFIED")
        self.assertEqual(result["content_status"], "not_applied")
        self.assertFalse(result["content_write_occurred"])
        self.assertFalse(result["immutable_backups_retained"])

    def test_rollback_drift_stops_before_plugin_deactivate_replace_activate(self):
        plan = self.stage()
        self.apply(plan)
        self.admin.events.clear()
        self.admin.rollback_drift = True
        with self.assertRaises(deploy.RollbackDriftError):
            self.rollback(plan)
        self.assertEqual(self.admin.events, ["goto_plugins", "read_status", "internal_rollback"])
        self.assertEqual((self.admin.version, self.admin.active), (deploy.TO_VERSION, True))
        result = self.read_json(deploy.plan_rollback_result(plan))
        self.assertEqual(result["status"], "ROLLBACK_BLOCKED_DRIFT")
        self.assertFalse(result["plugin_rollback_attempted"])

    def test_projected_preflight_drift_is_recorded_and_clicks_nothing(self):
        plan = self.stage()
        self.apply(plan)
        self.admin.events.clear()
        self.admin.status["rollback_drift_free"] = False
        self.admin.status["rollback_blocked_artifact"] = "contact_faq"
        with self.assertRaises(deploy.RollbackDriftError):
            self.rollback(plan)
        self.assertEqual(self.admin.events, ["goto_plugins", "read_status"])
        self.assertEqual((self.admin.version, self.admin.active), (deploy.TO_VERSION, True))
        result = self.read_json(deploy.plan_rollback_result(plan))
        self.assertEqual(result["status"], "ROLLBACK_BLOCKED_DRIFT")
        self.assertFalse(result["plugin_rollback_attempted"])

    def test_post_activation_validation_failure_runs_one_emergency_rollback(self):
        plan = self.stage()
        original = deploy.require_applied_status
        calls = 0

        def fail_once(status, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise deploy.DeploymentError("forced post-activation validation failure")
            return original(status, **kwargs)

        with mock.patch.object(deploy, "require_applied_status", fail_once):
            with self.assertRaises(deploy.DeploymentError) as caught:
                self.apply(plan)
        self.assertIn("one fixed emergency rollback", str(caught.exception))
        self.assertEqual(self.admin.events.count("internal_rollback"), 1)
        self.assertEqual(self.admin.events.count("replace:1.0.1"), 1)
        self.assertEqual((self.admin.version, self.admin.active), ("1.0.1", True))
        result = self.read_json(deploy.plan_result(plan))
        self.assertEqual(result["emergency_rollbacks"], 1)
        self.assertEqual(result["status"], "APPLY_FAILED_CLOSED_AND_ROLLED_BACK")

    def test_forward_replace_failure_never_deactivates_source_guard(self):
        plan = self.stage()
        original = self.admin.replace

        def fail_forward(artifact, version, *, preserve_active=False):
            if version == deploy.TO_VERSION:
                self.admin._side_effect(f"replace_failed:{deploy.TO_VERSION}")
                self.assertTrue(preserve_active)
                raise deploy.DeploymentError("forced active overwrite failure")
            return original(artifact, version, preserve_active=preserve_active)

        with mock.patch.object(self.admin, "replace", fail_forward):
            with self.assertRaises(deploy.IndeterminateError):
                self.apply(plan)
        self.assertEqual((self.admin.version, self.admin.active), (deploy.FROM_VERSION, True))
        self.assertNotIn(f"deactivate:{deploy.FROM_VERSION}", self.admin.events)
        result = self.read_json(deploy.plan_result(plan))
        self.assertEqual(result["status"], "INDETERMINATE_NO_RETRY")

    def test_active_target_with_unreadable_status_is_left_inspectable_and_locked(self):
        plan = self.stage()
        self.admin.status_error = True
        with self.assertRaises(deploy.IndeterminateError):
            self.apply(plan)
        self.assertEqual((self.admin.version, self.admin.active), (deploy.TO_VERSION, True))
        self.assertEqual(self.admin.events.count("replace:1.0.1"), 0)
        result = self.read_json(deploy.plan_result(plan))
        self.assertEqual(result["status"], "INDETERMINATE_NO_RETRY")

    def test_inactive_source_refuses_stage_without_write(self):
        self.admin.active = False
        with self.assertRaises(deploy.DeploymentError):
            self.stage()
        self.assertEqual((self.admin.version, self.admin.active), (deploy.FROM_VERSION, False))
        self.assertEqual(self.admin.events, ["goto_plugins"])


if __name__ == "__main__":
    unittest.main()
