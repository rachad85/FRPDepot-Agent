"""Offline safety tests for the fixed Hetron private-history deployment tool."""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wordpress_hetron_private_history_deployment_tool as tool  # noqa: E402


def remove_test_tree(path):
    """Remove immutable-plan fixtures on Windows without weakening production files."""
    def clear_readonly(function, blocked_path, _exc_info):
        os.chmod(blocked_path, stat.S_IWRITE)
        function(blocked_path)
    shutil.rmtree(path, onerror=clear_readonly)


def probe_projection(state="public_uploads_exact_unprepared"):
    location = "private_history" if state == "private_history_exact" else "public_upload"
    return {
        "state": state,
        "attachment_id": tool.ATTACHMENT_ID,
        "attachment_status": "private",
        "private_root_hidden": True,
        "same_filesystem": True,
        "destination_safe": True,
        "destination_writable": True,
        "canary_exact": state != "public_uploads_exact_unprepared",
        "assets": [
            {"role": item["role"], "location": location, "bytes": item["bytes"],
             "sha256": item["sha256"]}
            for item in tool.FIXED_ASSETS
        ],
    }


class FakeAdmin:
    def __init__(self, harness, row):
        self.harness = harness
        self.row = json.loads(json.dumps(row))
        self.writes = 0
        self.fail_write = None
        self.downloads = [{key: item[key] for key in
                           ("role", "content_type", "bytes", "sha256")}
                          for item in tool.FIXED_ASSETS]

    def read_attachment(self):
        self.harness.events.append("attachment_read")
        return {
            "id": tool.ATTACHMENT_ID, "slug": tool.ATTACHMENT_SLUG, "status": "private",
            "mime_type": tool.ATTACHMENT_MIME, "filename": tool.ATTACHMENT_FILENAME,
            "filesize": tool.ATTACHMENT_FILESIZE, "source_url": tool.PDF_URL,
            "date_gmt": tool.ATTACHMENT_DATE_GMT,
        }

    def read_row(self, *, allow_absent=False):
        self.harness.events.append("row_read")
        if not self.row["present"] and not allow_absent:
            raise tool.DeploymentError("absent")
        return json.loads(json.dumps(self.row))

    def probe(self, *, expected_state):
        self.harness.events.append("probe_" + expected_state)
        projection = probe_projection(expected_state)
        return projection, "a" * 10

    def _write(self, event):
        self.harness.events.append(event)
        self.harness.assertTrue(self.harness.mutex_held)
        self.harness.assertIsNotNone(self.harness.active_plan)
        self.harness.assertTrue(tool.plan_lock_path(self.harness.active_plan).exists())
        self.writes += 1
        if self.fail_write:
            raise self.fail_write

    def replace_active_once(self):
        self._write("replace_write")
        self.row = tool.row_projection(True, True, tool.PLUGIN_VERSION, False)
        after = self.read_row()
        return {"comparison_name": tool.PLUGIN_NAME,
                "comparison_version": tool.PLUGIN_VERSION, "after": after}

    def prepare_once(self, nonce):
        self.harness.assertEqual(nonce, "a" * 10)
        self._write("prepare_write")
        return probe_projection("public_uploads_exact_prepared")

    def protect_once(self, nonce):
        self.harness.assertEqual(nonce, "a" * 10)
        self._write("protect_write")
        return probe_projection("private_history_exact")

    def verify_authenticated_downloads(self):
        self.harness.events.append("authenticated_downloads")
        return json.loads(json.dumps(self.downloads))


class Harness(unittest.TestCase):
    ACTION_ROW = {
        "plugin_replace_active": tool.row_projection(
            True, True, tool.WITHDRAWN_PLUGIN_VERSION, False),
        "prepare_private_root": tool.row_projection(True, True, tool.PLUGIN_VERSION, False),
        "protect_five_assets": tool.row_projection(True, True, tool.PLUGIN_VERSION, False),
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hetron-private-history-tests-"))
        self.addCleanup(lambda: remove_test_tree(self.tmp) if self.tmp.exists() else None)
        self.plan_dir = self.tmp / "plans"
        self.plan_dir.mkdir()
        self.receipts = self.tmp / "receipts.jsonl"
        self.events = []
        self.mutex_held = False
        self.active_plan = None
        self.admin = None
        self.public_mode = "available"

        @contextlib.contextmanager
        def fake_mutex(lane, *, purpose, wait_seconds=90.0):
            self.assertEqual(lane, "wordpress")
            self.assertFalse(self.mutex_held)
            self.events.append("mutex_enter")
            self.mutex_held = True
            try:
                yield
            finally:
                self.mutex_held = False
                self.events.append("mutex_exit")

        @contextlib.contextmanager
        def fake_admin_session():
            self.events.append("admin_open")
            yield self.admin

        def fake_routes():
            self.events.append("attachment_routes")
            return {tool.ATTACHMENT_URL: 404, tool.ATTACHMENT_QUERY_URL: 410}

        def fake_assets():
            self.events.append("public_assets")
            return json.loads(json.dumps(tool.ASSET_PLAN_RECORDS))

        def fake_canary():
            self.events.append("canary_denied")
            return {"url": tool.CANARY_URL, "status": 403,
                    "fresh_url": tool.CANARY_FRESH_URL, "fresh_status": 403,
                    "sha256": tool.CANARY_SHA256}

        def fake_all_unavailable():
            self.events.append("all_public_unavailable")
            return {url: 404 for url in (
                *tool.ATTACHMENT_ROUTE_URLS,
                *(item["url"] for item in tool.FIXED_ASSETS),
                *tool.PUBLIC_ASSET_FRESH_URLS,
                tool.CANARY_URL, tool.CANARY_FRESH_URL,
                *tool.PRIVATE_ASSET_URLS, *tool.PRIVATE_ASSET_FRESH_URLS,
            )}

        for patcher in (
            mock.patch.object(tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(tool, "RECEIPTS", self.receipts),
            mock.patch.object(tool, "ui_browser_lock", fake_mutex),
            mock.patch.object(tool, "admin_session", fake_admin_session),
            mock.patch.object(tool, "require_attachment_routes_unavailable", fake_routes),
            mock.patch.object(tool, "verify_public_assets_available", fake_assets),
            mock.patch.object(tool, "require_canary_denied", fake_canary),
            mock.patch.object(tool, "require_all_public_unavailable", fake_all_unavailable),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def set_action(self, action):
        self.admin = FakeAdmin(self, self.ACTION_ROW[action])

    def stage(self, action):
        self.set_action(action)
        before = set(self.plan_dir.glob("*.json"))
        functions = {
            "plugin_replace_active": tool.command_stage_replace,
            "prepare_private_root": tool.command_stage_prepare,
            "protect_five_assets": tool.command_stage_protect,
        }
        with contextlib.redirect_stdout(io.StringIO()):
            functions[action](argparse.Namespace())
        created = list(set(self.plan_dir.glob("*.json")) - before)
        self.assertEqual(len(created), 1)
        return created[0]

    def commit(self, action, plan, approval="APPROVED"):
        self.active_plan = Path(plan)
        functions = {
            "plugin_replace_active": tool.command_commit_replace,
            "prepare_private_root": tool.command_commit_prepare,
            "protect_five_assets": tool.command_commit_protect,
        }
        with contextlib.redirect_stdout(io.StringIO()):
            functions[action](argparse.Namespace(plan=str(plan), approval=approval))

    @staticmethod
    def read_json(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def rewrite(self, path, **changes):
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        value = self.read_json(path)
        value.pop("sha256", None)
        value.update(changes)
        value["sha256"] = tool.digest_for(value)
        Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class TestFixedCapability(unittest.TestCase):
    def _fixture_plugin(self, root):
        source = (tool.BASE / "plugin" / tool.PLUGIN_SLUG /
                  "frpdepot-hetron-private-history.php").read_text(encoding="utf-8")
        fixtures = {}
        for index, item in enumerate(tool.FIXED_ASSETS):
            payload = (f"fixed-{index}-{item['role']}-bytes").encode("ascii")
            fixtures[item["role"]] = payload
            source = source.replace(
                f"'bytes' => {item['bytes']}", f"'bytes' => {len(payload)}", 1
            ).replace(
                f"'sha256' => '{item['sha256']}'",
                f"'sha256' => '{__import__('hashlib').sha256(payload).hexdigest()}'", 1
            )
        path = root / "fixture-plugin.php"
        path.write_text(source, encoding="utf-8")
        return path, fixtures

    def test_exact_commands_actions_and_no_arbitrary_arguments(self):
        self.assertEqual(tool.COMMANDS, (
            "inspect", "stage-replace", "commit-replace", "stage-prepare",
            "commit-prepare", "stage-protect", "commit-protect"))
        self.assertEqual(tool.ACTIONS, ("plugin_replace_active", "prepare_private_root",
                                        "protect_five_assets"))
        parser = tool.build_parser()
        subs = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        self.assertEqual(set(subs.choices), set(tool.COMMANDS))
        for name, child in subs.choices.items():
            options = {action.dest for action in child._actions}
            if name.startswith("commit-"):
                self.assertEqual(options, {"help", "plan", "approval"})
            else:
                self.assertEqual(options, {"help"})

    def test_exact_identity_and_five_asset_hashes(self):
        self.assertEqual(tool.PLUGIN_FILE,
                         "frpdepot-hetron-private-history/frpdepot-hetron-private-history.php")
        self.assertEqual(tool.ATTACHMENT_ID, 1832)
        self.assertEqual(len(tool.FIXED_ASSETS), 5)
        self.assertEqual(tool.FIXED_ASSETS[0]["bytes"], 5_740_139)
        self.assertEqual(tool.FIXED_ASSETS[0]["sha256"],
                         "b9993ac63eeeb4994c17dd34a79d6db8e154d3ae65de1d6a98b188fb766986c5")
        self.assertEqual(len({item["url"] for item in tool.FIXED_ASSETS}), 5)
        self.assertEqual(len({item["download_action"] for item in tool.FIXED_ASSETS}), 5)

    def test_artifact_bytes_members_hashes_and_manifest_are_pinned(self):
        observed = tool.verify_artifact()
        self.assertEqual(observed["sha256"], tool.ARTIFACT_SHA256)
        self.assertEqual(observed["bytes"], tool.ARTIFACT_BYTES)
        self.assertEqual(tuple(observed["members"]), tool.ARTIFACT_MEMBERS)

    def test_artifact_builder_is_reproducible(self):
        original = tool.ARTIFACT_PATH.read_bytes()
        import importlib.util
        path = tool.BASE / "build_plugin_zip.py"
        spec = importlib.util.spec_from_file_location("hetron_build_plugin_zip", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        manifest = module.build()
        self.assertEqual(tool.ARTIFACT_PATH.read_bytes(), original)
        self.assertEqual(manifest["artifact_sha256"], tool.ARTIFACT_SHA256)

    def test_php_source_has_fixed_prepare_move_restore_downloads_but_no_delete_or_rewrite(self):
        source = (tool.BASE / "plugin" / tool.PLUGIN_SLUG /
                  "frpdepot-hetron-private-history.php").read_text(encoding="utf-8")
        self.assertIn("rename( $source, $destination )", source)
        self.assertIn("private_root_hidden", source)
        self.assertIn("CANARY_NAME", source)
        self.assertIn("chmod( $private_root, 0700 )", source)
        self.assertIn("frpdepot_hetron_private_history_prepare", source)
        self.assertIn("hash_file( 'sha256'", source)
        self.assertIn("current_user_can( 'read_post', self::ATTACHMENT_ID )", source)
        self.assertNotIn(".htaccess", source)
        self.assertNotIn("unlink(", source)
        self.assertNotIn("wp_delete", source)
        self.assertNotIn("header( 'Location:", source)
        self.assertNotIn("add_rewrite", source)
        self.assertNotIn("restore_manifest", source)
        self.assertNotIn("frpdepot_hetron_private_history_restore", source)
        self.assertNotIn("register_activation_hook", source)
        self.assertNotIn("register_deactivation_hook", source)

    def test_actual_php_prepare_creates_only_exact_canary_and_leaves_assets_public(self):
        with tempfile.TemporaryDirectory(prefix="hetron-php-prepare-") as raw:
            root = Path(raw)
            document = root / "public"
            uploads = document / "wp-content" / "uploads"
            uploads.mkdir(parents=True)
            plugin, fixtures = self._fixture_plugin(root)
            source_paths = []
            for item, payload in zip(tool.FIXED_ASSETS, fixtures.values(), strict=True):
                relative = urlsplit(item["url"]).path.split("/uploads/", 1)[1]
                target = uploads / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                source_paths.append(target)
            private = uploads / tool.PRIVATE_DIRECTORY
            harness = root / "prepare.php"
            harness.write_text("""<?php
define('ABSPATH', %s);
$_SERVER['DOCUMENT_ROOT'] = %s;
define('FIXTURE_UPLOAD_ROOT', %s);
function add_action(...$args) {}
function wp_upload_dir(...$args) { return array('basedir'=>FIXTURE_UPLOAD_ROOT, 'error'=>false); }
function is_user_logged_in() { return true; }
function current_user_can(...$args) { return true; }
function get_post($id) { return (object)array('post_type'=>'attachment'); }
function get_post_status(...$args) { return 'private'; }
function get_post_mime_type(...$args) { return 'application/pdf'; }
function get_post_meta(...$args) { return '2026/03/HETRON-CR-Guide-2007_Ineos.pdf'; }
function check_admin_referer(...$args) { return true; }
function wp_send_json_success($data, $status=200) { echo json_encode(array('success'=>true,'status'=>$status,'data'=>$data)); exit(0); }
function wp_send_json_error($data, $status=409) { echo json_encode(array('success'=>false,'status'=>$status,'data'=>$data)); exit(2); }
require %s;
FRPDepot_Hetron_Private_History::prepare();
""" % tuple(json.dumps(str(value).replace("\\", "/")) for value in (
                str(document).replace("\\", "/") + "/", document, uploads, plugin
            )), encoding="utf-8")
            result = subprocess.run(["php", str(harness)], capture_output=True, text=True,
                                    timeout=30, check=True)
            evidence = json.loads(result.stdout)
            self.assertTrue(evidence["success"])
            self.assertEqual(evidence["data"]["state"], "public_uploads_exact_prepared")
            self.assertTrue(evidence["data"]["canary_exact"])
            self.assertEqual(sorted(path.name for path in private.iterdir()), [tool.CANARY_NAME])
            self.assertEqual((private / tool.CANARY_NAME).read_bytes(), tool.CANARY_BYTES)
            self.assertTrue(all(path.is_file() for path in source_paths))

    def test_actual_php_one_way_move_preserves_bytes_and_unrelated_file(self):
        with tempfile.TemporaryDirectory(prefix="hetron-php-transition-") as raw:
            root = Path(raw)
            document = root / "public"
            uploads = document / "wp-content" / "uploads"
            uploads.mkdir(parents=True)
            plugin, fixtures = self._fixture_plugin(root)
            relative = {item["role"]: urlsplit(item["url"]).path.split("/uploads/", 1)[1]
                        for item in tool.FIXED_ASSETS}
            for role, payload in fixtures.items():
                target = uploads / relative[role]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            unrelated = uploads / "2026" / "03" / "unrelated.txt"
            unrelated.write_bytes(b"untouched")
            private = uploads / tool.PRIVATE_DIRECTORY
            private.mkdir()
            (private / tool.CANARY_NAME).write_bytes(tool.CANARY_BYTES)
            os.chmod(private, 0o700)
            harness = root / "transition.php"
            harness.write_text("""<?php
define('ABSPATH', %s);
$_SERVER['DOCUMENT_ROOT'] = %s;
define('FIXTURE_UPLOAD_ROOT', %s);
function add_action(...$args) {}
function wp_upload_dir(...$args) { return array('basedir'=>FIXTURE_UPLOAD_ROOT, 'error'=>false); }
require %s;
$class = new ReflectionClass('FRPDepot_Hetron_Private_History');
$assets_method = $class->getMethod('assets'); $assets_method->setAccessible(true);
$transition = $class->getMethod('transition_manifest'); $transition->setAccessible(true);
$state_method = $class->getMethod('transition_state'); $state_method->setAccessible(true);
$assets = $assets_method->invoke(null);
$transition->invoke(null, $assets, %s, %s);
$private_state = $state_method->invoke(null);
$transitioned = array();
foreach ($assets as $role => $asset) {
  $source = %s . '/' . $asset['relative'];
  $destination = %s . '/' . $asset['private_name'];
  $transitioned[$role] = array(!file_exists($source), filesize($destination), hash_file('sha256', $destination));
}
echo json_encode(array('transitioned'=>$transitioned,'private_state'=>$private_state,'unrelated'=>file_get_contents(%s)));
""" % tuple(json.dumps(str(value).replace("\\", "/")) for value in (
                str(document).replace("\\", "/") + "/", document, uploads, plugin,
                uploads, private, uploads, private, unrelated
            )), encoding="utf-8")
            result = subprocess.run(["php", str(harness)], capture_output=True, text=True,
                                    timeout=30, check=True)
            evidence = json.loads(result.stdout)
            self.assertEqual(evidence["unrelated"], "untouched")
            self.assertEqual(evidence["private_state"]["state"], "private_history_exact")
            self.assertTrue(evidence["private_state"]["private_root_hidden"])
            self.assertTrue(evidence["private_state"]["same_filesystem"])
            self.assertTrue(evidence["private_state"]["destination_safe"])
            self.assertTrue(evidence["private_state"]["destination_writable"])
            self.assertTrue(evidence["private_state"]["canary_exact"])
            for role, payload in fixtures.items():
                digest = __import__("hashlib").sha256(payload).hexdigest()
                self.assertEqual(evidence["transitioned"][role], [True, len(payload), digest])

    def test_actual_php_complete_preflight_prevents_partial_move_on_hash_mismatch(self):
        with tempfile.TemporaryDirectory(prefix="hetron-php-preflight-") as raw:
            root = Path(raw)
            document = root / "public"
            uploads = document / "wp-content" / "uploads"
            uploads.mkdir(parents=True)
            plugin, fixtures = self._fixture_plugin(root)
            paths = []
            for index, (item, payload) in enumerate(zip(tool.FIXED_ASSETS,
                                                        fixtures.values(), strict=True)):
                relative = urlsplit(item["url"]).path.split("/uploads/", 1)[1]
                target = uploads / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload + (b"corrupt" if index == 2 else b""))
                paths.append(target)
            private = uploads / tool.PRIVATE_DIRECTORY
            private.mkdir()
            (private / tool.CANARY_NAME).write_bytes(tool.CANARY_BYTES)
            os.chmod(private, 0o700)
            harness = root / "preflight.php"
            php_paths = "json_decode(" + json.dumps(json.dumps(
                [str(path).replace("\\", "/") for path in paths]
            )) + ", true)"
            harness.write_text("""<?php
define('ABSPATH', %s); $_SERVER['DOCUMENT_ROOT'] = %s;
function add_action(...$args) {} require %s;
$class = new ReflectionClass('FRPDepot_Hetron_Private_History');
$a=$class->getMethod('assets'); $a->setAccessible(true); $assets=$a->invoke(null);
$m=$class->getMethod('transition_manifest'); $m->setAccessible(true);
$error=''; try {$m->invoke(null,$assets,%s,%s);} catch(Throwable $e) {$error=$e->getPrevious() ? $e->getPrevious()->getMessage() : $e->getMessage();}
echo json_encode(array('error'=>$error,'sources'=>array_map('file_exists',%s),'private_exists'=>file_exists(%s)));
""" % (
                json.dumps(str(document).replace("\\", "/") + "/"),
                json.dumps(str(document).replace("\\", "/")),
                json.dumps(str(plugin).replace("\\", "/")),
                json.dumps(str(uploads).replace("\\", "/")),
                json.dumps(str(private).replace("\\", "/")),
                php_paths,
                json.dumps(str(private).replace("\\", "/")),
            ), encoding="utf-8")
            result = subprocess.run(["php", str(harness)], capture_output=True, text=True,
                                    timeout=30, check=True)
            evidence = json.loads(result.stdout)
            self.assertEqual(evidence["error"], "transition_preflight_mismatch")
            self.assertEqual(evidence["sources"], [True] * 5)
            self.assertTrue(evidence["private_exists"])

    def test_deployer_has_no_restore_delete_shell_email_or_generic_route_argument(self):
        source = Path(tool.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {alias.name for node in ast.walk(tree)
                   if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
        for forbidden in ("subprocess", "smtplib", "requests"):
            self.assertNotIn(forbidden, imports)
        calls = [node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
                 for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, (ast.Attribute, ast.Name))]
        for forbidden in ("unlink", "remove", "rmtree", "system", "popen"):
            self.assertNotIn(forbidden, calls)
        self.assertNotIn("frpdepot_hetron_private_history_restore", source)
        self.assertNotIn("DELETE", source)

    def test_exact_approval(self):
        tool.require_approval("APPROVED")
        for wrong in ("approved", "Approved", " APPROVED", "APPROVED ", "APPROVED.", ""):
            with self.subTest(wrong=wrong), self.assertRaises(tool.DeploymentError):
                tool.require_approval(wrong)

    def test_url_guards_reject_near_misses(self):
        for url in (
            "http://frpdepots.com/wp-admin/plugins.php",
            "https://www.frpdepots.com/wp-admin/plugins.php",
            "https://frpdepots.com.evil.test/wp-admin/plugins.php",
            "https://frpdepots.com:8443/wp-admin/plugins.php",
            "https://u:p@frpdepots.com/wp-admin/plugins.php",
        ):
            with self.subTest(url=url), self.assertRaises(tool.DeploymentError):
                tool.assert_origin(url)
        with self.assertRaises(tool.DeploymentError):
            tool.assert_admin_url(tool.EXACT_ORIGIN + "/wp-admin/plugin-editor.php")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_public_url(tool.EXACT_ORIGIN + "/other.pdf")

    def test_probe_requires_all_five_exact_and_safe_hidden_destination(self):
        value = {**probe_projection(), "nonce": "a" * 10}
        self.assertEqual(tool.normalize_probe(
            value, expected_state="public_uploads_exact_unprepared"), probe_projection())
        for key in ("private_root_hidden", "same_filesystem",
                    "destination_safe", "destination_writable"):
            changed = json.loads(json.dumps(value))
            changed[key] = False
            with self.subTest(key=key), self.assertRaises(tool.DeploymentError):
                tool.normalize_probe(changed, expected_state="public_uploads_exact_unprepared")
        changed = json.loads(json.dumps(value))
        changed["canary_exact"] = True
        with self.assertRaises(tool.DeploymentError):
            tool.normalize_probe(changed, expected_state="public_uploads_exact_unprepared")
        changed = json.loads(json.dumps(value))
        changed["assets"][0]["sha256"] = "0" * 64
        with self.assertRaises(tool.DeploymentError):
            tool.normalize_probe(changed, expected_state="public_uploads_exact_unprepared")

    def test_timed_out_plugin_write_reconciliation_never_repeats_the_write(self):
        expected = tool.row_projection(True, True, tool.PLUGIN_VERSION, False)
        admin = object.__new__(tool.AdminPage)
        admin.goto_plugins = mock.Mock()
        admin.read_row = mock.Mock(return_value=expected)
        self.assertEqual(admin._reconcile_row_after_timeout(expected, "Plugin activation"), expected)
        admin.goto_plugins.assert_called_once_with()
        admin.read_row.assert_called_once_with(allow_absent=True)

    def test_timed_out_plugin_write_reconciliation_fails_closed_on_wrong_state(self):
        expected = tool.row_projection(True, True, tool.PLUGIN_VERSION, False)
        admin = object.__new__(tool.AdminPage)
        admin.goto_plugins = mock.Mock()
        admin.read_row = mock.Mock(
            return_value=tool.row_projection(True, False, tool.PLUGIN_VERSION, False)
        )
        with self.assertRaises(tool.IndeterminateError):
            admin._reconcile_row_after_timeout(expected, "Plugin activation")


class TestAnonymousVerification(unittest.TestCase):
    def test_large_404_body_is_not_read_or_treated_as_verification_failure(self):
        class Response:
            status = 404
            headers = {"Content-Type": "text/html"}
            read_called = False

            def getcode(self):
                return self.status

            def read(self, _limit):
                self.read_called = True
                return b"x" * 100

            def close(self):
                pass

        response = Response()
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(tool, "build_opener", return_value=opener):
            observed = tool.public_request(tool.ATTACHMENT_URL, max_bytes=1)
        self.assertEqual(observed["status"], 404)
        self.assertEqual(observed["body"], b"")
        self.assertFalse(response.read_called)

    def test_attachment_routes_require_404_or_410_and_no_redirect(self):
        responses = [
            {"status": 404, "location": None, "content_type": "text/html", "body": b""},
            {"status": 410, "location": None, "content_type": "text/html", "body": b""},
        ]
        with mock.patch.object(tool, "public_request", side_effect=responses):
            self.assertEqual(tool.require_attachment_routes_unavailable(),
                             {tool.ATTACHMENT_URL: 404, tool.ATTACHMENT_QUERY_URL: 410})
        bad = dict(responses[0], status=301, location=tool.PDF_URL)
        with mock.patch.object(tool, "public_request", return_value=bad), \
                self.assertRaises(tool.DeploymentError):
            tool.require_attachment_routes_unavailable()

    def test_all_twenty_four_routes_are_checked_after_protect(self):
        response = {"status": 404, "location": None, "content_type": "text/html", "body": b""}
        with mock.patch.object(tool, "public_request", return_value=response) as request:
            result = tool.require_all_public_unavailable()
        expected = [*tool.ATTACHMENT_ROUTE_URLS, *(item["url"] for item in tool.FIXED_ASSETS),
                    *tool.PUBLIC_ASSET_FRESH_URLS,
                    tool.CANARY_URL, tool.CANARY_FRESH_URL,
                    *tool.PRIVATE_ASSET_URLS, *tool.PRIVATE_ASSET_FRESH_URLS]
        self.assertEqual([call.args[0] for call in request.call_args_list], expected)
        self.assertEqual(len(expected), 24)
        self.assertEqual(set(result), set(expected))

    def test_canary_literal_and_cache_bust_must_be_denied_without_redirect(self):
        denied = {"status": 403, "location": None, "content_type": "text/html", "body": b""}
        with mock.patch.object(tool, "public_request", return_value=denied) as request:
            self.assertEqual(tool.require_canary_denied(), {
                "url": tool.CANARY_URL, "status": 403,
                "fresh_url": tool.CANARY_FRESH_URL, "fresh_status": 403,
                "sha256": tool.CANARY_SHA256})
        self.assertEqual([call.args[0] for call in request.call_args_list],
                         [tool.CANARY_URL, tool.CANARY_FRESH_URL])
        for bad in (
            {**denied, "status": 200, "body": tool.CANARY_BYTES},
            {**denied, "status": 302, "location": tool.EXACT_ORIGIN + "/"},
        ):
            with self.subTest(bad=bad), mock.patch.object(
                    tool, "public_request", return_value=bad), self.assertRaises(tool.DeploymentError):
                tool.require_canary_denied()

    def test_asset_before_check_hashes_full_body_not_headers(self):
        payload = b"fixed"
        records = tuple({**item, "bytes": len(payload),
                         "sha256": __import__("hashlib").sha256(payload).hexdigest()}
                        for item in tool.ASSET_PLAN_RECORDS)
        fixed = tuple({**item, "bytes": len(payload),
                       "sha256": __import__("hashlib").sha256(payload).hexdigest()}
                      for item in tool.FIXED_ASSETS)
        response = {"status": 200, "location": None, "content_type": "application/pdf",
                    "body": payload}
        def request(url, *, max_bytes):
            wanted = next(item for item in fixed if item["url"] == url)
            return {**response, "content_type": wanted["content_type"]}
        with mock.patch.object(tool, "FIXED_ASSETS", fixed), \
                mock.patch.object(tool, "ASSET_PLAN_RECORDS", records), \
                mock.patch.object(tool, "public_request", side_effect=request):
            self.assertEqual(tool.verify_public_assets_available(), list(records))


class TestPlans(Harness):
    def test_each_stage_is_read_only_exact_24h_and_immutable(self):
        for action in tool.ACTIONS:
            with self.subTest(action=action):
                path = self.stage(action)
                plan = self.read_json(path)
                self.assertEqual(self.admin.writes, 0)
                self.assertFalse(tool.plan_lock_path(path).exists())
                saved = plan.pop("sha256")
                self.assertEqual(saved, tool.digest_for(plan))
                created = tool.datetime.fromisoformat(plan["created_utc"])
                expires = tool.datetime.fromisoformat(plan["expires_utc"])
                self.assertEqual(expires - created, timedelta(hours=24))
                self.assertEqual(plan["action"], action)
                self.assertEqual(plan["after_expected"], tool.after_expected(action))
                remove_test_tree(self.plan_dir)
                self.plan_dir.mkdir()

    def test_stage_requires_private_attachment_and_attachment_routes_unavailable(self):
        self.set_action("plugin_replace_active")
        self.admin.read_attachment = mock.Mock(side_effect=tool.DeploymentError("not private"))
        with self.assertRaises(tool.DeploymentError):
            tool.command_stage_replace(argparse.Namespace())
        self.assertFalse(list(self.plan_dir.glob("*.json")))

    def test_rehashed_action_artifact_or_before_change_is_refused(self):
        path = self.stage("plugin_replace_active")
        for field, value in (("action", "prepare_private_root"), ("artifact", {}), ("before", {})):
            copy = self.tmp / f"{field}.json"
            copy.write_bytes(path.read_bytes())
            # Must reside in plan directory.
            copy2 = self.plan_dir / copy.name
            copy2.write_bytes(copy.read_bytes())
            self.rewrite(copy2, **{field: value})
            with self.subTest(field=field), self.assertRaises(tool.DeploymentError):
                tool.load_plan(str(copy2), expected_action="plugin_replace_active")

    def test_tamper_without_rehash_is_refused(self):
        path = self.stage("plugin_replace_active")
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        value = self.read_json(path)
        value["action"] = "other"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(tool.DeploymentError):
            tool.load_plan(str(path), expected_action="plugin_replace_active")

    def test_expired_and_non_24h_plans_refused(self):
        path = self.stage("plugin_replace_active")
        old = tool.utc_now() - timedelta(hours=25)
        self.rewrite(path, created_utc=old.isoformat(),
                     expires_utc=(old + timedelta(hours=24)).isoformat())
        with self.assertRaises(tool.DeploymentError):
            tool.load_plan(str(path), expected_action="plugin_replace_active")
        remove_test_tree(self.plan_dir); self.plan_dir.mkdir()
        path = self.stage("plugin_replace_active")
        value = self.read_json(path)
        created = tool.datetime.fromisoformat(value["created_utc"])
        self.rewrite(path, expires_utc=(created + timedelta(hours=23)).isoformat())
        with self.assertRaises(tool.DeploymentError):
            tool.load_plan(str(path), expected_action="plugin_replace_active")

    def test_outside_plan_path_refused(self):
        outside = self.tmp / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaises(tool.DeploymentError):
            tool.load_plan(str(outside), expected_action="plugin_replace_active")


class TestCommits(Harness):
    def _successful(self, action):
        path = self.stage(action)
        self.events.clear()
        self.commit(action, path)
        self.assertEqual(self.admin.writes, 1)
        lock = self.read_json(tool.plan_lock_path(path))
        result = self.read_json(tool.result_path(path))
        self.assertEqual(lock["status"], "committed_verified")
        self.assertEqual(lock["attempts_allowed"], 1)
        self.assertEqual(lock["attempts_started"], 1)
        self.assertFalse(lock["retry"])
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["write_count"], 1)
        write_event = {"plugin_replace_active": "replace_write",
                       "prepare_private_root": "prepare_write",
                       "protect_five_assets": "protect_write"}[action]
        self.assertLess(self.events.index("mutex_enter"), self.events.index(write_event))
        return path, result

    def test_replace_success_one_write_active_no_move(self):
        _path, result = self._successful("plugin_replace_active")
        self.assertEqual(result["plugin_after"],
                         tool.row_projection(True, True, tool.PLUGIN_VERSION, False))
        self.assertEqual(result["probe_after"]["state"], "public_uploads_exact_unprepared")
        self.assertFalse(result["historical_files_moved"])

    def test_prepare_success_one_write_canary_denied_no_move(self):
        _path, result = self._successful("prepare_private_root")
        self.assertEqual(result["plugin_after"],
                         tool.row_projection(True, True, tool.PLUGIN_VERSION, False))
        self.assertEqual(result["probe_after"]["state"], "public_uploads_exact_prepared")
        self.assertEqual(result["canary_denial"]["status"], 403)
        self.assertEqual(result["canary_denial"]["fresh_status"], 403)
        self.assertFalse(result["historical_files_moved"])

    def test_protect_success_all_twenty_four_anonymous_and_five_authenticated_hashes(self):
        _path, result = self._successful("protect_five_assets")
        self.assertEqual(len(result["anonymous_after"]), 24)
        self.assertEqual(set(result["anonymous_after"].values()), {404})
        self.assertEqual(result["authenticated_downloads"], self.admin.downloads)
        self.assertTrue(result["historical_files_moved"])
        self.assertFalse(result["historical_files_deleted"])
        self.assertTrue(result["attachment_preserved_private"])
        self.assertIn("authenticated_downloads", self.events)

    def test_wrong_approval_refuses_before_admin_and_attempt(self):
        path = self.stage("plugin_replace_active")
        events = list(self.events)
        with self.assertRaises(tool.DeploymentError):
            self.commit("plugin_replace_active", path, "Approved")
        self.assertEqual(self.events, events + ["mutex_enter", "mutex_exit"])
        self.assertEqual(self.admin.writes, 0)
        self.assertFalse(tool.plan_lock_path(path).exists())

    def test_mutex_busy_refuses_before_attempt(self):
        path = self.stage("plugin_replace_active")
        @contextlib.contextmanager
        def busy(*args, **kwargs):
            raise tool.UiLaneBusy("busy")
            yield
        with mock.patch.object(tool, "ui_browser_lock", busy), \
                self.assertRaises(tool.UiLaneBusy):
            self.commit("plugin_replace_active", path)
        self.assertFalse(tool.plan_lock_path(path).exists())
        self.assertEqual(self.admin.writes, 0)

    def test_before_drift_refuses_without_burning_plan(self):
        path = self.stage("plugin_replace_active")
        self.admin.row = tool.row_projection(True, False, tool.PLUGIN_VERSION, False)
        with self.assertRaises(tool.DeploymentError):
            self.commit("plugin_replace_active", path)
        self.assertFalse(tool.plan_lock_path(path).exists())
        self.assertEqual(self.admin.writes, 0)

    def test_write_failure_closes_indeterminate_and_never_retries(self):
        path = self.stage("plugin_replace_active")
        self.admin.fail_write = TimeoutError("fake")
        with self.assertRaises(tool.IndeterminateError):
            self.commit("plugin_replace_active", path)
        self.assertEqual(self.admin.writes, 1)
        self.assertEqual(self.read_json(tool.plan_lock_path(path))["status"], "indeterminate")
        self.assertEqual(self.read_json(tool.result_path(path))["status"], "INDETERMINATE")
        with self.assertRaises(tool.DeploymentError):
            self.commit("plugin_replace_active", path)
        self.assertEqual(self.admin.writes, 1)

    def test_post_protect_public_failure_is_permanent_indeterminate(self):
        path = self.stage("protect_five_assets")
        with mock.patch.object(tool, "require_all_public_unavailable",
                               side_effect=tool.IndeterminateError("public")), \
                self.assertRaises(tool.IndeterminateError):
            self.commit("protect_five_assets", path)
        self.assertEqual(self.admin.writes, 1)
        self.assertEqual(self.read_json(tool.plan_lock_path(path))["status"], "indeterminate")

    def test_authenticated_download_hash_failure_is_permanent_indeterminate(self):
        path = self.stage("protect_five_assets")
        self.admin.verify_authenticated_downloads = mock.Mock(
            side_effect=tool.IndeterminateError("hash"))
        with self.assertRaises(tool.IndeterminateError):
            self.commit("protect_five_assets", path)
        self.assertEqual(self.admin.writes, 1)
        self.assertEqual(self.read_json(tool.result_path(path))["status"], "INDETERMINATE")

    def test_success_receipts_and_replay_refusal(self):
        path, _ = self._successful("plugin_replace_active")
        rows = [json.loads(line) for line in self.receipts.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["action"] for row in rows], [
            "wordpress_hetron_private_history_plan_staged",
            "wordpress_hetron_private_history_committed_and_verified",
        ])
        with self.assertRaises(tool.DeploymentError):
            self.commit("plugin_replace_active", path)
        self.assertEqual(self.admin.writes, 1)


if __name__ == "__main__":
    unittest.main()
