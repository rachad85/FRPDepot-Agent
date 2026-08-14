"""Offline safety, plan, dependency, ordering, failure, and restoration tests.

No test opens CDP, a browser, a credential store, or the network.
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
import derakane_search_v2_migration_tool as migration  # noqa: E402

ORIGINAL_VERIFY_DEPENDENCY = migration.verify_dependency


class TestPinnedInactiveInstallDependency(unittest.TestCase):
    def test_batch_one_verified_install_is_the_only_allowed_predecessor(self):
        expected_sha = "502af8cdcde53ccce9403dbdd265b6997f9eabab638d7b282063aa4c4b635326"
        self.assertEqual(migration.INSTALL_PLAN_SHA256, expected_sha)
        self.assertEqual(
            migration.INSTALL_PLAN_PATH.name,
            "20260813T220805Z_plugin_install_or_replace_" + expected_sha + ".json",
        )


def page_projection(*, new=False, modified=None, **changes):
    modified = modified or ("2026-08-13T22:00:00" if new
                            else migration.PAGE_INITIAL_MODIFIED_GMT)
    page = {
        "id": migration.PAGE_ID, "slug": migration.PAGE_SLUG,
        "status": migration.PAGE_STATUS, "link": migration.PAGE_LINK,
        "date_gmt": migration.PAGE_DATE_GMT, "modified_gmt": modified,
        "title_raw": migration.PAGE_TITLE_RAW,
        "content_bytes": migration.PAGE_AFTER_BYTES if new else migration.PAGE_BEFORE_BYTES,
        "content_sha256": migration.PAGE_AFTER_SHA256 if new else migration.PAGE_BEFORE_SHA256,
        "old_shortcode_count": 0 if new else 1,
        "new_shortcode_count": 1 if new else 0,
    }
    page.update(changes)
    page["fingerprint"] = migration.page_fingerprint(page)
    return page


def downtime(**changes):
    core = {
        "mode": "inactive_gap", "page_status": 200, "blank": False, "fatal": False,
        "root_count": 0, "legacy_asset_count": 0, "v2_js_count": 0, "v2_css_count": 0,
        "v2_rest_status": 404, "v2_rest_code": "rest_no_route",
    }
    core.update(changes)
    return {**core, "fingerprint": migration.digest_for(core)}


def legacy(**changes):
    core = {
        "mode": "legacy", "page_status": 200, "blank": False, "fatal": False,
        "root_count": 1, "legacy_asset_count": 2, "v2_js_count": 0, "v2_css_count": 0,
        "v2_rest_status": 404, "v2_rest_code": "rest_no_route",
        "known_search_status_text": 'Showing results for "hydrochloric acid".',
        "known_search_result_rows": 40, "known_search_ajax_posts_200": 1,
    }
    core.update(changes)
    value = {**core, "fingerprint": migration.digest_for(core)}
    value["passed"] = migration.legacy_passed(value)
    return value


def dependency(action):
    return {
        "action": action, "plan": f"C:/fixed/{action}.json", "plan_sha256": "a" * 64,
        "result": f"C:/fixed/{action}.result.json", "result_file_sha256": "b" * 64,
        "required_status": "COMMITTED_AND_VERIFIED",
    }


class FakeAdmin:
    def __init__(self):
        self.v2_active = False
        self.v2_version = migration.V2_VERSION
        self.legacy_active = True
        self.legacy_version = migration.LEGACY_VERSION
        self.page_new = False
        self.page_modified = migration.PAGE_INITIAL_MODIFIED_GMT
        self.page_override = None
        self.events = []
        self.write_probe = lambda: None
        self.fail_deactivate_legacy = None
        self.deactivate_legacy_then_fail = False
        self.fail_reactivate_legacy = None
        self.fail_activate_v2 = None
        self.activate_v2_then_fail = False
        self.fail_deactivate_v2 = None
        self.fail_write_page = None
        self.write_page_then_fail = False

    def read_rows(self):
        self.events.append("read_rows")
        return {
            "v2": migration.project_row(migration.V2_PLUGIN_FILE, True, self.v2_active,
                                        self.v2_version, False),
            "legacy": migration.project_row(migration.LEGACY_PLUGIN_FILE, True,
                                            self.legacy_active, self.legacy_version, False),
        }

    def read_page(self):
        self.events.append("read_page")
        if self.page_override is not None:
            return json.loads(json.dumps(self.page_override))
        return page_projection(new=self.page_new, modified=self.page_modified)

    def goto_plugins(self):
        self.events.append("goto_plugins")

    def deactivate_legacy(self):
        self.write_probe()
        self.events.append("deactivate_legacy")
        if self.deactivate_legacy_then_fail:
            self.legacy_active = False
        if self.fail_deactivate_legacy:
            raise self.fail_deactivate_legacy
        self.legacy_active = False
        return self.read_rows()["legacy"]

    def reactivate_legacy(self):
        self.write_probe()
        self.events.append("reactivate_legacy")
        if self.fail_reactivate_legacy:
            raise self.fail_reactivate_legacy
        self.legacy_active = True
        return self.read_rows()["legacy"]

    def activate_v2(self):
        self.write_probe()
        self.events.append("activate_v2")
        if self.activate_v2_then_fail:
            self.v2_active = True
        if self.fail_activate_v2:
            raise self.fail_activate_v2
        self.v2_active = True
        return self.read_rows()["v2"]

    def deactivate_v2(self):
        self.write_probe()
        self.events.append("deactivate_v2")
        if self.fail_deactivate_v2:
            raise self.fail_deactivate_v2
        self.v2_active = False
        return self.read_rows()["v2"]

    def write_page_content(self, content):
        self.write_probe()
        self.events.append("write_page")
        old, new = migration.verify_page_fixture()
        if content == new and self.write_page_then_fail:
            self.page_new = True
            self.page_modified = "2026-08-13T22:00:00"
        if self.fail_write_page and content == new:
            failure = self.fail_write_page
            self.fail_write_page = None
            raise failure
        if content == new:
            self.page_new = True
            self.page_modified = "2026-08-13T22:00:00"
        elif content == old:
            self.page_new = False
            self.page_modified = "2026-08-13T22:01:00"
        else:
            raise AssertionError("arbitrary content reached fake admin")
        return self.read_page()


class FakePublic:
    def __init__(self):
        self.legacy = legacy()
        self.downtime = downtime()
        self.v2 = {
            "passed": True, "page": {"status": 200, "root_count": 1},
            "api": {"known_status": 200, "known_total_positive": True},
            "browser": {"status_ready": True, "result_count_positive": True},
        }
        self.events = []

    def legacy_findings(self):
        self.events.append("legacy_qa")
        return json.loads(json.dumps(self.legacy))

    def downtime_projection(self):
        self.events.append("downtime_qa")
        return json.loads(json.dumps(self.downtime))

    def v2_findings(self):
        self.events.append("v2_qa")
        return json.loads(json.dumps(self.v2))


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="derakane-migration-tests-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.plan_dir = self.tmp / "plans"
        self.plan_dir.mkdir()
        self.receipts = self.tmp / "receipts.jsonl"
        self.admin = FakeAdmin()
        self.public = FakePublic()
        self.events = []
        self.mutex_held = False
        self.active_plan = None
        self.admin_opens = 0
        self.public_opens = 0

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
        def fake_public_session():
            self.public_opens += 1
            self.events.append("public_open")
            yield self.public

        def fake_install_dependency():
            return dependency("inactive_v2_install")

        def fake_migration_dependency(_raw, expected_action):
            return dependency(expected_action)

        for patcher in (
            mock.patch.object(migration, "PLAN_DIR", self.plan_dir),
            mock.patch.object(migration, "RECEIPTS", self.receipts),
            mock.patch.object(migration, "ui_browser_lock", fake_mutex),
            mock.patch.object(migration, "admin_session", fake_admin_session),
            mock.patch.object(migration, "anonymous_session", fake_public_session),
            mock.patch.object(migration, "install_dependency", fake_install_dependency),
            mock.patch.object(migration, "migration_dependency", fake_migration_dependency),
            mock.patch.object(migration, "verify_dependency", lambda _value: None),
            mock.patch.object(migration, "dependency_after_state", lambda _value: None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        def write_probe():
            self.events.append("write_side_effect")
            self.assertTrue(self.mutex_held, "shared WordPress mutex must span every side effect")
            if self.active_plan is not None:
                self.assertTrue(migration.attempt_lock_path(self.active_plan).exists(),
                                "attempt lock must precede first side effect")

        self.admin.write_probe = write_probe

    def action_dependency(self, action):
        return dependency(migration.ACTION_PREDECESSOR[action])

    def stage(self, action):
        handlers = {
            "legacy_deactivate": (migration.command_stage_deactivate, argparse.Namespace()),
            "page_shortcode_replace": (
                migration.command_stage_update_page,
                argparse.Namespace(predecessor_plan="fixed-predecessor")),
            "v2_activate": (
                migration.command_stage_activate_v2,
                argparse.Namespace(predecessor_plan="fixed-predecessor")),
        }
        before = set(self.plan_dir.glob("*.json"))
        handler, args = handlers[action]
        with contextlib.redirect_stdout(io.StringIO()):
            handler(args)
        created = list(set(self.plan_dir.glob("*.json")) - before)
        self.assertEqual(len(created), 1)
        return created[0]

    def direct_plan(self, action, public=None):
        live = migration.read_admin_state()
        public = public or (legacy() if action == "legacy_deactivate" else downtime())
        return migration.write_plan(action, self.action_dependency(action), live, public)

    def commit(self, action, plan, approval="APPROVED"):
        handlers = {
            "legacy_deactivate": migration.command_commit_deactivate,
            "page_shortcode_replace": migration.command_commit_update_page,
            "v2_activate": migration.command_commit_activate_v2,
        }
        self.active_plan = Path(plan)
        with contextlib.redirect_stdout(io.StringIO()):
            handlers[action](argparse.Namespace(plan=str(plan), approval=approval))

    @staticmethod
    def read_json(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def result(self, plan):
        return self.read_json(migration.result_path(Path(plan)))

    def rewrite_plan(self, plan, rehash=True, **changes):
        path = Path(plan)
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        value = self.read_json(path)
        value.pop("sha256", None)
        value.update(changes)
        if rehash:
            value["sha256"] = migration.digest_for(value)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return path

    def prepare_page_step(self):
        self.admin.legacy_active = False

    def prepare_activation_step(self):
        self.admin.legacy_active = False
        self.admin.page_new = True
        self.admin.page_modified = "2026-08-13T22:00:00"


class TestFixedCapability(unittest.TestCase):
    def test_exact_plugin_and_page_identities_are_hardcoded(self):
        self.assertEqual(migration.LEGACY_PLUGIN_FILE,
                         "derakane-chemical-search-v1.1.0/derakane-chemical-search.php")
        self.assertEqual(migration.LEGACY_VERSION, "1.1.0")
        self.assertEqual(migration.V2_PLUGIN_FILE,
                         "frpdepot-derakane-chemical-search/"
                         "frpdepot-derakane-chemical-search.php")
        self.assertEqual(migration.V2_VERSION, "2.0.0")
        self.assertEqual(migration.PAGE_ID, 1840)
        self.assertEqual(migration.PAGE_SLUG, "derakane-resin-resistance-search")
        self.assertEqual(migration.PAGE_BEFORE_SHA256,
                         "13e439db9e81a60894f70781b7117cb10420c7f207ea334e886f78cee92f48b6")
        self.assertEqual(migration.PAGE_AFTER_SHA256,
                         "4824bbe758c10decc743e3738e1becf2853ce1c7bdaabc025fb325e4b026158f")

    def test_fixture_is_exact_and_replacement_is_one_short_code_only(self):
        before, after = migration.verify_page_fixture()
        self.assertEqual(len(before.encode()), 2393)
        self.assertEqual(len(after.encode()), 2393)
        self.assertEqual(before.count(migration.OLD_SHORTCODE), 1)
        self.assertEqual(before.count(migration.NEW_SHORTCODE), 0)
        self.assertEqual(after.count(migration.OLD_SHORTCODE), 0)
        self.assertEqual(after.count(migration.NEW_SHORTCODE), 1)
        self.assertEqual(after, before.replace(migration.OLD_SHORTCODE,
                                               migration.NEW_SHORTCODE))

    def test_only_seven_fixed_commands_and_three_actions(self):
        parser = migration.build_parser()
        choices = next(x for x in parser._actions
                       if isinstance(x, argparse._SubParsersAction)).choices
        self.assertEqual(set(choices), set(migration.COMMANDS))
        self.assertEqual(migration.ACTIONS,
                         ("legacy_deactivate", "page_shortcode_replace", "v2_activate"))
        self.assertNotIn("delete", " ".join(choices))
        self.assertNotIn("settings", " ".join(choices))

    def test_admin_and_rest_urls_refuse_wrong_page_or_route(self):
        migration.assert_admin_url(migration.PLUGINS_URL)
        migration.assert_admin_url(migration.PAGE_EDIT_URL)
        migration.assert_page_rest_url(migration.PAGE_REST_URL)
        for url in (
            "https://frpdepots.com/wp-admin/post.php?post=1841&action=edit",
            "https://frpdepots.com/wp-admin/post.php?post=1840&action=trash",
            "https://frpdepots.com/wp-admin/users.php",
            "https://frpdepots.com/wp-admin/options-general.php",
        ):
            with self.subTest(url=url), self.assertRaises(migration.MigrationError):
                migration.assert_admin_url(url)
        for url in (
            "https://frpdepots.com/wp-json/wp/v2/pages/1841",
            "https://frpdepots.com/wp-json/wc/v3/products/1840",
            "https://example.test/wp-json/wp/v2/pages/1840",
        ):
            with self.subTest(url=url), self.assertRaises(migration.MigrationError):
                migration.assert_page_rest_url(url)

    def test_wrong_page_slug_status_title_link_date_or_content_refuses(self):
        fields = {
            "id": 1841, "slug": "wrong", "status": "draft",
            "link": "https://frpdepots.com/wrong/", "date_gmt": "2026-01-01T00:00:00",
            "title_raw": "Wrong", "content_sha256": "0" * 64,
        }
        for field, value in fields.items():
            with self.subTest(field=field):
                page = page_projection(**{field: value})
                if field == "content_sha256":
                    self.assertFalse(migration.page_has_old(page, initial_modified=True))
                else:
                    with self.assertRaises(migration.MigrationError):
                        migration.assert_page_identity(page)

    def test_no_delete_settings_user_commerce_media_email_shell_or_generic_route(self):
        source = Path(migration.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        code = "\n".join(line for line in source.splitlines()
                         if not line.lstrip().startswith("#"))
        for needle in (
            "action=trash", "action=delete", ".delete(", "wp-json/wc/", "/wc/v3/",
            "users.php", "options-general.php", "media-new.php", "upload.php",
            "import requests", "urllib.request", "subprocess", "os.system", "smtplib",
            "send_mail", "wp_mail", "plugin-editor.php", "post-new.php",
        ):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, code)
        function_names = {node.name for node in ast.walk(tree)
                          if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertNotIn("delete", function_names)

    def test_anonymous_context_is_fresh_nonpersistent(self):
        source = Path(migration.__file__).read_text(encoding="utf-8")
        block = source.split("def anonymous_session", 1)[1].split(
            "\ndef legacy_passed", 1)[0]
        self.assertIn('launch(channel="msedge", headless=True)', block)
        self.assertIn("new_context()", block)
        self.assertIn("context.close()", block)
        self.assertNotIn("storage_state", block)
        self.assertNotIn("launch_persistent_context", block)


class TestPlans(Harness):
    def setUp(self):
        super().setUp()
        self.plan = self.stage("legacy_deactivate")
        self.original = self.plan.read_text(encoding="utf-8")

    def restore(self):
        os.chmod(self.plan, stat.S_IREAD | stat.S_IWRITE)
        self.plan.write_text(self.original, encoding="utf-8")

    def test_plan_is_immutable_hash_named_exact_24h_closed_and_nonatomic(self):
        value = self.read_json(self.plan)
        created = migration.datetime.fromisoformat(value["created_utc"])
        expires = migration.datetime.fromisoformat(value["expires_utc"])
        self.assertEqual(expires - created, timedelta(hours=24))
        self.assertEqual(created.utcoffset(), timedelta(0))
        saved = value.pop("sha256")
        self.assertEqual(saved, migration.digest_for(value))
        self.assertIn(saved, self.plan.name)
        self.assertEqual(set(value), migration.PLAN_KEYS)
        self.assertFalse(os.stat(self.plan).st_mode & stat.S_IWUSR)
        self.assertFalse(value["rollback"]["atomic"])
        self.assertTrue(value["rollback"]["compensating_only"])
        self.assertEqual(value["validation"]["attempts_allowed"], 1)
        self.assertFalse(value["validation"]["retry"])

    def test_unhashed_tamper_refuses(self):
        self.rewrite_plan(self.plan, rehash=False, action="v2_activate")
        with self.assertRaises(migration.MigrationError) as caught:
            migration.load_plan(str(self.plan))
        self.assertIn("hash failed", str(caught.exception))

    def test_rehashed_identity_action_dependency_contract_forgeries_refuse(self):
        original = self.read_json(self.plan)
        cases = [
            {"origin": "https://example.test"}, {"tool": "wrong"},
            {"tool_version": "0"}, {"schema_version": 9},
            {"action": "delete"}, {"identities": {"page_id": 1841}},
            {"validation": {}}, {"rollback": {"atomic": True}},
        ]
        dep = dict(original["dependency"])
        dep["action"] = "wrong"
        cases.append({"dependency": dep})
        for changes in cases:
            with self.subTest(changes=changes):
                self.restore()
                self.rewrite_plan(self.plan, **changes)
                with self.assertRaises(migration.MigrationError):
                    migration.load_plan(str(self.plan))

    def test_rehashed_wrong_plugin_slug_version_page_or_content_refuses(self):
        original = self.read_json(self.plan)
        cases = []
        for row_name, field, value in (
            ("legacy", "plugin_file", "wrong/wrong.php"),
            ("legacy", "version", "1.0.0"),
            ("v2", "plugin_file", "wrong-v2/wrong.php"),
            ("v2", "version", "2.0.1"),
        ):
            before = json.loads(json.dumps(original["before"]))
            before["rows"][row_name][field] = value
            cases.append({"before": before})
        for field, value in (("id", 1841), ("slug", "wrong"),
                             ("content_sha256", "0" * 64),
                             ("modified_gmt", "2026-08-13T00:00:00")):
            before = json.loads(json.dumps(original["before"]))
            before["page"][field] = value
            before["page"]["fingerprint"] = migration.page_fingerprint(before["page"])
            cases.append({"before": before})
        for changes in cases:
            with self.subTest(changes=changes):
                self.restore()
                self.rewrite_plan(self.plan, **changes)
                with self.assertRaises(migration.MigrationError):
                    migration.load_plan(str(self.plan))

    def test_expired_future_nonutc_and_non24hour_refuse(self):
        now = migration.utc_now()
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
                with self.assertRaises(migration.MigrationError):
                    migration.load_plan(str(self.plan))

    def test_wrong_directory_refuses(self):
        outside = self.tmp / "outside.json"
        outside.write_text(self.original, encoding="utf-8")
        with self.assertRaises(migration.MigrationError):
            migration.resolve_plan_path(str(outside))


class TestStagingDependenciesAndDrift(Harness):
    def test_first_stage_requires_exact_installed_v2_inactive_legacy_active_original_page(self):
        plan = self.stage("legacy_deactivate")
        value = self.read_json(plan)
        self.assertEqual(value["dependency"]["action"], "inactive_v2_install")
        self.assertFalse(value["before"]["rows"]["v2"]["active"])
        self.assertTrue(value["before"]["rows"]["legacy"]["active"])
        self.assertEqual(value["before"]["page"]["content_sha256"],
                         migration.PAGE_BEFORE_SHA256)

    def test_wrong_slug_page_content_plugin_version_or_row_state_refuses_before_plan(self):
        scenarios = (
            ("v2 version", lambda: setattr(self.admin, "v2_version", "2.0.1")),
            ("legacy version", lambda: setattr(self.admin, "legacy_version", "1.0.0")),
            ("legacy inactive", lambda: setattr(self.admin, "legacy_active", False)),
            ("page new", lambda: setattr(self.admin, "page_new", True)),
            ("page slug", lambda: setattr(self.admin, "page_override",
                                           page_projection(slug="wrong"))),
            ("page content", lambda: setattr(self.admin, "page_override",
                                              page_projection(content_sha256="0" * 64))),
        )
        for label, change in scenarios:
            with self.subTest(label=label):
                self.setUp()
                change()
                with self.assertRaises(migration.MigrationError):
                    self.stage("legacy_deactivate")
                self.assertEqual(list(self.plan_dir.glob("*.json")), [])

    def test_second_and_third_steps_enforce_exact_predecessor_types(self):
        self.prepare_page_step()
        page_plan = self.stage("page_shortcode_replace")
        self.assertEqual(self.read_json(page_plan)["dependency"]["action"],
                         "legacy_deactivate")
        self.prepare_activation_step()
        activation_plan = self.stage("v2_activate")
        self.assertEqual(self.read_json(activation_plan)["dependency"]["action"],
                         "page_shortcode_replace")

    def test_dependency_result_byte_drift_refuses(self):
        dep_result = self.tmp / "dependency.result.json"
        dep_result.write_text("{}", encoding="utf-8")
        dep = dependency("legacy_deactivate")
        dep["result"] = str(dep_result)
        dep["result_file_sha256"] = migration.file_sha256(dep_result)
        dep_result.write_text('{"tampered":true}', encoding="utf-8")
        with self.assertRaises(migration.MigrationError) as caught:
            ORIGINAL_VERIFY_DEPENDENCY(dep)
        self.assertIn("drifted", str(caught.exception))

    def test_public_precondition_drift_refuses_staging(self):
        self.public.legacy = legacy(known_search_result_rows=0)
        with self.assertRaises(migration.MigrationError):
            self.stage("legacy_deactivate")
        self.assertEqual(list(self.plan_dir.glob("*.json")), [])


class TestApprovalLockingAndCommitDrift(Harness):
    def test_approval_is_exact_unpadded_uppercase(self):
        migration.require_approval("APPROVED")
        for wrong in ("", "approved", " Approved", "APPROVED ", "APPROVED!",
                      "APPROVED APPROVED", None):
            with self.subTest(wrong=wrong), self.assertRaises(migration.MigrationError):
                migration.require_approval(wrong)

    def test_bad_approval_precedes_dependency_fixture_browser_network_and_lock(self):
        plan = self.stage("legacy_deactivate")
        opens = (self.admin_opens, self.public_opens)
        with mock.patch.object(migration, "verify_dependency",
                               side_effect=AssertionError("dependency read before approval")), \
             mock.patch.object(migration, "verify_page_fixture",
                               side_effect=AssertionError("fixture read before approval")):
            with self.assertRaises(migration.MigrationError):
                self.commit("legacy_deactivate", plan, approval="approved")
        self.assertEqual((self.admin_opens, self.public_opens), opens)
        self.assertFalse(migration.attempt_lock_path(plan).exists())

    def test_mutex_before_attempt_lock_and_lock_before_first_side_effect(self):
        plan = self.stage("legacy_deactivate")
        self.events.clear()
        original = migration.write_attempt_lock
        def watched(path, value):
            self.events.append("attempt_lock")
            self.assertTrue(self.mutex_held)
            original(path, value)
        with mock.patch.object(migration, "write_attempt_lock", watched):
            self.commit("legacy_deactivate", plan)
        self.assertLess(self.events.index("mutex_enter"), self.events.index("attempt_lock"))
        self.assertLess(self.events.index("attempt_lock"), self.events.index("write_side_effect"))
        self.assertLess(self.events.index("write_side_effect"), self.events.index("mutex_exit"))

    def test_busy_mutex_does_not_burn_plan_or_open_browser(self):
        plan = self.stage("legacy_deactivate")
        opens = (self.admin_opens, self.public_opens)
        @contextlib.contextmanager
        def busy(*_args, **_kwargs):
            raise migration.UiLaneBusy("busy")
            yield
        with mock.patch.object(migration, "ui_browser_lock", busy):
            with self.assertRaises(migration.UiLaneBusy):
                self.commit("legacy_deactivate", plan)
        self.assertEqual((self.admin_opens, self.public_opens), opens)
        self.assertFalse(migration.attempt_lock_path(plan).exists())

    def test_plugin_page_or_public_drift_refuses_without_burning_plan(self):
        changes = (
            lambda: setattr(self.admin, "legacy_version", "1.0.0"),
            lambda: setattr(self.admin, "page_override", page_projection(slug="wrong")),
            lambda: setattr(self.public, "legacy", legacy(known_search_result_rows=0)),
        )
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                plan = self.stage("legacy_deactivate")
                change()
                with self.assertRaises(migration.MigrationError):
                    self.commit("legacy_deactivate", plan)
                self.assertFalse(migration.attempt_lock_path(plan).exists())
                self.assertNotIn("deactivate_legacy", self.admin.events)


class TestSuccessfulSequence(Harness):
    def test_deactivate_success_complete_admin_and_downtime_readback_one_attempt(self):
        plan = self.stage("legacy_deactivate")
        self.commit("legacy_deactivate", plan)
        self.assertFalse(self.admin.legacy_active)
        self.assertFalse(self.admin.v2_active)
        result = self.result(plan)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertFalse(result["after"]["rows"]["legacy"]["active"])
        self.assertEqual(result["after"]["page"]["content_sha256"],
                         migration.PAGE_BEFORE_SHA256)
        self.assertTrue(migration.downtime_passed(result["public_after"]))
        with self.assertRaises(migration.MigrationError):
            self.commit("legacy_deactivate", plan)
        self.assertEqual(self.admin.events.count("deactivate_legacy"), 1)

    def test_page_update_success_changes_only_exact_shortcode_projection(self):
        self.prepare_page_step()
        plan = self.stage("page_shortcode_replace")
        self.commit("page_shortcode_replace", plan)
        self.assertTrue(self.admin.page_new)
        self.assertFalse(self.admin.legacy_active)
        self.assertFalse(self.admin.v2_active)
        result = self.result(plan)
        self.assertEqual(result["after"]["page"]["content_sha256"],
                         migration.PAGE_AFTER_SHA256)
        self.assertEqual(result["after"]["page"]["old_shortcode_count"], 0)
        self.assertEqual(result["after"]["page"]["new_shortcode_count"], 1)

    def test_v2_activation_runs_page_rest_known_search_browser_ui_and_admin_readback(self):
        self.prepare_activation_step()
        plan = self.stage("v2_activate")
        self.commit("v2_activate", plan)
        self.assertTrue(self.admin.v2_active)
        self.assertFalse(self.admin.legacy_active)
        result = self.result(plan)
        self.assertTrue(result["public_after"]["passed"])
        self.assertEqual(result["public_after"]["api"]["known_status"], 200)
        self.assertTrue(result["public_after"]["browser"]["status_ready"])
        self.assertEqual(result["after"]["page"]["content_sha256"],
                         migration.PAGE_AFTER_SHA256)
        self.assertIn("v2_qa", self.public.events)


class TestFailuresAndRestoration(Harness):
    def test_deactivation_click_partial_failure_restores_legacy_once(self):
        self.admin.deactivate_legacy_then_fail = True
        self.admin.fail_deactivate_legacy = TimeoutError("unknown click outcome")
        plan = self.stage("legacy_deactivate")
        with self.assertRaises(migration.MigrationError) as caught:
            self.commit("legacy_deactivate", plan)
        self.assertNotIsInstance(caught.exception, migration.IndeterminateError)
        self.assertTrue(self.admin.legacy_active)
        self.assertFalse(self.admin.v2_active)
        result = self.result(plan)
        self.assertEqual(result["status"], "FAILED_CLOSED_RESTORED")
        self.assertTrue(result["restoration"]["fully_restored"])
        self.assertEqual(self.admin.events.count("reactivate_legacy"), 1)

    def test_post_deactivation_qa_failure_restores_legacy_service(self):
        plan = self.stage("legacy_deactivate")
        # preflight uses call 2 (stage was 1); post-write call 3 is bad; restoration call 4 good.
        calls = {"n": 0}
        good = legacy()
        def changing_legacy():
            calls["n"] += 1
            if calls["n"] == 3:
                return legacy(known_search_result_rows=0)
            return good
        # Downtime validation, not legacy QA, is post-write. Make its second call fail.
        down_calls = {"n": 0}
        def changing_down():
            down_calls["n"] += 1
            return downtime(root_count=1) if down_calls["n"] == 1 else downtime()
        self.public.downtime_projection = changing_down
        with self.assertRaises(migration.MigrationError):
            self.commit("legacy_deactivate", plan)
        self.assertTrue(self.admin.legacy_active)
        self.assertEqual(self.result(plan)["status"], "FAILED_CLOSED_RESTORED")

    def test_page_write_then_timeout_restores_page_and_legacy_once(self):
        self.prepare_page_step()
        self.admin.write_page_then_fail = True
        self.admin.fail_write_page = TimeoutError("unknown page update")
        plan = self.stage("page_shortcode_replace")
        with self.assertRaises(migration.MigrationError):
            self.commit("page_shortcode_replace", plan)
        self.assertFalse(self.admin.page_new)
        self.assertTrue(self.admin.legacy_active)
        result = self.result(plan)
        self.assertEqual(result["status"], "FAILED_CLOSED_RESTORED")
        self.assertTrue(result["restoration"]["page_restore_attempted"])
        self.assertEqual(self.admin.events.count("write_page"), 2)
        self.assertEqual(self.admin.events.count("reactivate_legacy"), 1)

    def test_v2_known_qa_failure_deactivates_v2_restores_page_legacy_and_proves_search(self):
        self.prepare_activation_step()
        self.public.v2["passed"] = False
        plan = self.stage("v2_activate")
        with self.assertRaises(migration.MigrationError) as caught:
            self.commit("v2_activate", plan)
        self.assertNotIsInstance(caught.exception, migration.IndeterminateError)
        self.assertFalse(self.admin.v2_active)
        self.assertTrue(self.admin.legacy_active)
        self.assertFalse(self.admin.page_new)
        result = self.result(plan)
        self.assertEqual(result["status"], "FAILED_CLOSED_RESTORED")
        self.assertTrue(result["restoration"]["fully_restored"])
        self.assertEqual(self.admin.events.count("deactivate_v2"), 1)
        self.assertEqual(self.admin.events.count("write_page"), 1)
        self.assertEqual(self.admin.events.count("reactivate_legacy"), 1)

    def test_activation_click_uncertainty_still_compensates_once(self):
        self.prepare_activation_step()
        self.admin.activate_v2_then_fail = True
        self.admin.fail_activate_v2 = TimeoutError("unknown activation")
        plan = self.stage("v2_activate")
        with self.assertRaises(migration.MigrationError):
            self.commit("v2_activate", plan)
        self.assertFalse(self.admin.v2_active)
        self.assertTrue(self.admin.legacy_active)
        self.assertFalse(self.admin.page_new)
        self.assertEqual(self.admin.events.count("deactivate_v2"), 1)

    def test_restoration_partial_failure_is_indeterminate_never_claimed_restored(self):
        self.prepare_activation_step()
        self.public.v2["passed"] = False
        self.admin.fail_reactivate_legacy = TimeoutError("rollback unavailable")
        plan = self.stage("v2_activate")
        with self.assertRaises(migration.IndeterminateError):
            self.commit("v2_activate", plan)
        result = self.result(plan)
        self.assertEqual(result["status"], "INDETERMINATE")
        self.assertFalse(result["restoration"]["fully_restored"])
        self.assertFalse(self.admin.legacy_active)

    def test_unknown_page_content_is_never_overwritten_during_restoration(self):
        self.prepare_activation_step()
        self.public.v2["passed"] = False
        unknown = page_projection(new=True, content_sha256="c" * 64,
                                  old_shortcode_count=0, new_shortcode_count=0)
        # Drift happens after v2 QA begins so preflight still sees the reviewed page.
        original_v2 = self.public.v2_findings
        def drift_during_qa():
            value = original_v2()
            self.admin.page_override = unknown
            return value
        self.public.v2_findings = drift_during_qa
        plan = self.stage("v2_activate")
        with self.assertRaises(migration.IndeterminateError):
            self.commit("v2_activate", plan)
        result = self.result(plan)
        self.assertIn("page_restore:unknown_content_refused",
                      result["restoration"]["errors"])
        self.assertFalse(result["restoration"]["page_restore_attempted"])

    def test_failed_plan_cannot_retry(self):
        self.admin.deactivate_legacy_then_fail = True
        self.admin.fail_deactivate_legacy = TimeoutError("unknown")
        plan = self.stage("legacy_deactivate")
        with self.assertRaises(migration.MigrationError):
            self.commit("legacy_deactivate", plan)
        with self.assertRaises(migration.MigrationError):
            self.commit("legacy_deactivate", plan)
        self.assertEqual(self.admin.events.count("deactivate_legacy"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
