"""Offline safety tests for the fixed Hetron attachment lifecycle tool.

No test opens Playwright, CDP, a browser, WordPress, or a network connection.
Stateful doubles exercise the production commands, plan integrity, mutex/attempt
ordering, one-write/no-retry behavior, receipts, public-route verification and
historical-byte preservation.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wordpress_hetron_guide_lifecycle_tool as tool

PUBLIC_BEFORE = {
    "guide_status": 301,
    "guide_location": tool.PDF_URL,
    "query_status": 301,
    "query_location": tool.PDF_URL,
    "redirect_by": "Yoast SEO",
}


def live_object(status=tool.BEFORE_STATUS):
    """Complete edit-context fixture with the production top-level schema."""
    obj = {
        "id": tool.ATTACHMENT_ID,
        "date": "2026-03-17T15:20:38",
        "date_gmt": tool.ATTACHMENT_DATE_GMT,
        "guid": {"rendered": tool.PDF_URL, "raw": tool.PDF_URL},
        "modified": "2026-03-17T15:20:38" if status == tool.BEFORE_STATUS
                    else "2026-08-13T21:00:00",
        "modified_gmt": "2026-03-17T15:20:38" if status == tool.BEFORE_STATUS
                        else "2026-08-13T21:00:00",
        "slug": tool.ATTACHMENT_SLUG,
        "status": status,
        "type": tool.ATTACHMENT_TYPE,
        "link": tool.PUBLIC_GUIDE_URL,
        "title": {"raw": tool.ATTACHMENT_TITLE, "rendered": tool.ATTACHMENT_TITLE},
        "author": 1,
        "featured_media": 0,
        "comment_status": "open",
        "ping_status": "closed",
        "template": tool.ATTACHMENT_TEMPLATE,
        "meta": {"_et_pb_use_builder": "", "_et_pb_old_content": "",
                 "_et_gb_content_width": ""},
        "permalink_template": tool.PUBLIC_QUERY_URL,
        "generated_slug": tool.ATTACHMENT_SLUG,
        "class_list": [f"post-{tool.ATTACHMENT_ID}", "attachment", "type-attachment",
                       f"status-{status}", "hentry"],
        "description": {"raw": "", "rendered": "fixed rendered preview"},
        "caption": {"raw": "", "rendered": ""},
        "alt_text": "",
        "media_type": "file",
        "mime_type": tool.ATTACHMENT_MIME,
        "media_details": {"filesize": tool.ATTACHMENT_FILESIZE,
                          "sizes": {"full": {"file": "preview.jpg"}}},
        "post": tool.ATTACHMENT_PARENT,
        "source_url": tool.PDF_URL,
        "missing_image_sizes": [],
        "filename": tool.ATTACHMENT_FILENAME,
        "filesize": tool.ATTACHMENT_FILESIZE,
        "_links": {"self": [{"href": tool.REST_URL}]},
    }
    if set(obj) != tool.OBJECT_KEYS:
        raise AssertionError("fixture drifted from production closed object schema")
    return obj


class FakeAdmin:
    def __init__(self, harness):
        self.harness = harness
        self.obj = live_object()
        self.gotos = 0
        self.reads = 0
        self.writes = 0
        self.fail_write: Exception | None = None

    def goto_fixed_attachment(self):
        self.gotos += 1
        self.harness.events.append("admin_goto")

    def read_full(self, *, expected_status):
        self.reads += 1
        self.harness.events.append("authenticated_read")
        return tool.assert_live_object(json.loads(json.dumps(self.obj)),
                                       expected_status=expected_status)

    def make_private_once(self):
        self.harness.events.append("write_side_effect")
        self.harness.assertTrue(self.harness.mutex_held,
                                "shared WordPress mutex must cover the write")
        self.harness.assertIsNotNone(self.harness.active_plan)
        self.harness.assertTrue(tool.plan_lock_path(self.harness.active_plan).exists(),
                                "attempt lock must exist before the write")
        self.writes += 1
        if self.fail_write is not None:
            raise self.fail_write
        self.obj = live_object(tool.AFTER_STATUS)
        return json.loads(json.dumps(self.obj))


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hetron-lifecycle-tests-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.plan_dir = self.tmp / "plans"
        self.plan_dir.mkdir()
        self.receipts = self.tmp / "receipts.jsonl"
        self.admin = FakeAdmin(self)
        self.events: list[str] = []
        self.mutex_held = False
        self.active_plan: Path | None = None
        self.admin_opens = 0
        self.public_before_calls = 0
        self.asset_calls = 0
        self.public_after_calls = 0

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
            self.admin_opens += 1
            self.events.append("admin_open")
            yield self.admin

        def fake_public_before():
            self.public_before_calls += 1
            self.events.append("public_before")
            return dict(PUBLIC_BEFORE)

        def fake_assets():
            self.asset_calls += 1
            self.events.append("assets")
            return json.loads(json.dumps(tool.FIXED_ASSETS))

        def fake_public_after():
            self.public_after_calls += 1
            self.events.append("public_after")
            return {"guide_status": 404, "query_status": 410, "redirect": False}

        patches = (
            mock.patch.object(tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(tool, "RECEIPTS", self.receipts),
            mock.patch.object(tool, "ui_browser_lock", fake_mutex),
            mock.patch.object(tool, "admin_session", fake_admin_session),
            mock.patch.object(tool, "read_public_before", fake_public_before),
            mock.patch.object(tool, "verify_historical_assets", fake_assets),
            mock.patch.object(tool, "require_public_after_unavailable", fake_public_after),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def stage(self):
        before = set(self.plan_dir.glob("*.json"))
        with contextlib.redirect_stdout(io.StringIO()):
            tool.command_stage(argparse.Namespace())
        created = sorted(set(self.plan_dir.glob("*.json")) - before)
        self.assertEqual(len(created), 1)
        return created[0]

    def commit(self, plan, approval=tool.APPROVAL_WORD):
        self.active_plan = Path(plan)
        with contextlib.redirect_stdout(io.StringIO()):
            tool.command_commit(argparse.Namespace(plan=str(plan), approval=approval))

    @staticmethod
    def read_json(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def receipts_rows(self):
        if not self.receipts.exists():
            return []
        return [json.loads(line) for line in self.receipts.read_text(encoding="utf-8").splitlines()]

    def rewrite_plan(self, path, *, rehash=True, **changes):
        path = Path(path)
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        value = self.read_json(path)
        value.pop("sha256", None)
        value.update(changes)
        if rehash:
            value["sha256"] = tool.digest_for(value)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return path


class TestFixedCapability(unittest.TestCase):
    def test_exact_live_identity_is_pinned(self):
        self.assertEqual(tool.ATTACHMENT_ID, 1832)
        self.assertEqual(tool.ATTACHMENT_TYPE, "attachment")
        self.assertEqual(tool.ATTACHMENT_SLUG, "hetron-cr-guide-2007_ineos")
        self.assertEqual(tool.BEFORE_STATUS, "inherit")
        self.assertEqual(tool.AFTER_STATUS, "private")
        self.assertIsNone(tool.ATTACHMENT_PARENT)
        self.assertEqual(tool.ATTACHMENT_TEMPLATE, "")
        self.assertEqual(tool.ATTACHMENT_MIME, "application/pdf")
        self.assertEqual(tool.ATTACHMENT_FILENAME, "HETRON-CR-Guide-2007_Ineos.pdf")

    def test_only_two_commands_and_no_arbitrary_identity_arguments(self):
        self.assertEqual(tool.COMMANDS, ("stage", "commit"))
        parser = tool.build_parser()
        subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        self.assertEqual(set(subparsers.choices), {"stage", "commit"})
        stage_options = {a.dest for a in subparsers.choices["stage"]._actions}
        commit_options = {a.dest for a in subparsers.choices["commit"]._actions}
        self.assertEqual(stage_options, {"help"})
        self.assertEqual(commit_options, {"help", "plan", "approval"})
        for forbidden in ("id", "slug", "url", "status", "payload", "field", "action"):
            self.assertNotIn(forbidden, commit_options)

    def test_one_exact_rest_resource_and_literal_payload(self):
        self.assertEqual(tool.REST_URL, "https://frpdepots.com/wp-json/wp/v2/media/1832")
        self.assertEqual(tool.WRITE_PAYLOAD, {"status": "private"})
        self.assertEqual(tool.ALLOWED_ADMIN_URLS,
                         frozenset({"https://frpdepots.com/wp-admin/post.php?post=1832&action=edit"}))

    def test_public_routes_and_yost_redirect_target_are_exact(self):
        self.assertEqual(tool.PUBLIC_GUIDE_URL,
                         "https://frpdepots.com/hetron-cr-guide-2007_ineos/")
        self.assertEqual(tool.PUBLIC_QUERY_URL, "https://frpdepots.com/?attachment_id=1832")
        self.assertEqual(tool.PDF_URL,
                         "https://frpdepots.com/wp-content/uploads/2026/03/"
                         "HETRON-CR-Guide-2007_Ineos.pdf")

    def test_historical_assets_are_five_fixed_hashes_and_preserved(self):
        self.assertEqual(len(tool.FIXED_ASSETS), 5)
        self.assertEqual(tool.FIXED_ASSETS[0]["bytes"], 5_740_139)
        self.assertEqual(tool.FIXED_ASSETS[0]["sha256"],
                         "b9993ac63eeeb4994c17dd34a79d6db8e154d3ae65de1d6a98b188fb766986c5")
        for asset in tool.FIXED_ASSETS:
            self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
            self.assertLessEqual(asset["bytes"], tool.MAX_ASSET_BYTES)
        self.assertFalse(tool.AFTER_EXPECTED["object_deleted"])
        self.assertFalse(tool.AFTER_EXPECTED["files_deleted"])
        self.assertTrue(tool.AFTER_EXPECTED["historical_assets_preserved"])

    def test_source_has_no_delete_trash_shell_email_or_generic_route_call(self):
        source = Path(tool.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                   for alias in node.names}
        for forbidden in ("subprocess", "requests", "smtplib", "woocommerce_common"):
            self.assertNotIn(forbidden, imports)
        called = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    called.append(node.func.attr)
                elif isinstance(node.func, ast.Name):
                    called.append(node.func.id)
        for forbidden in ("unlink", "remove", "rmtree", "urlopen", "system", "popen"):
            self.assertNotIn(forbidden, called)
        self.assertNotIn("DELETE", source)
        self.assertNotIn("action=trash", source)
        self.assertNotIn("action=delete", source)
        self.assertNotIn("/wp-json/wp/v2/pages", source)
        self.assertNotIn("/wp-json/wp/v2/posts", source)
        self.assertNotIn("/wp-json/wc/", source)

    def test_origin_and_url_guards_refuse_near_misses(self):
        for url in (
            "http://frpdepots.com/wp-admin/post.php?post=1832&action=edit",
            "https://www.frpdepots.com/wp-admin/post.php?post=1832&action=edit",
            "https://frpdepots.com.evil.test/wp-admin/post.php?post=1832&action=edit",
            "https://frpdepots.com:8443/wp-admin/post.php?post=1832&action=edit",
            "https://user:pw@frpdepots.com/wp-admin/post.php?post=1832&action=edit",
        ):
            with self.subTest(url=url), self.assertRaises(tool.LifecycleError):
                tool.assert_exact_origin(url)
        with self.assertRaises(tool.LifecycleError):
            tool.assert_admin_url("https://frpdepots.com/wp-admin/post.php?post=1833&action=edit")
        with self.assertRaises(tool.LifecycleError):
            tool.assert_public_url("https://frpdepots.com/wp-content/uploads/other.pdf")

    def test_complete_object_schema_and_identity_are_required(self):
        obj = live_object()
        self.assertEqual(tool.assert_live_object(obj, expected_status="inherit"), obj)
        for mutate in (
            lambda x: x.update(id=1833),
            lambda x: x.update(slug="other"),
            lambda x: x.update(type="page"),
            lambda x: x.update(post=99),
            lambda x: x.update(template="custom.php"),
            lambda x: x.update(filename="other.pdf"),
            lambda x: x.update(source_url="https://frpdepots.com/other.pdf"),
        ):
            changed = live_object()
            mutate(changed)
            with self.assertRaises(tool.LifecycleError):
                tool.assert_live_object(changed, expected_status="inherit")
        extra = live_object()
        extra["unexpected"] = True
        with self.assertRaises(tool.LifecycleError):
            tool.assert_live_object(extra, expected_status="inherit")

    def test_protected_projection_is_every_field_except_closed_lifecycle_set(self):
        obj = live_object()
        projected = tool.protected_projection(obj)
        self.assertEqual(set(projected), tool.OBJECT_KEYS - tool.LIFECYCLE_FIELDS)
        self.assertNotIn("status", projected)
        self.assertIn("guid", projected)
        self.assertIn("media_details", projected)
        self.assertIn("source_url", projected)

    def test_approval_is_byte_exact(self):
        tool.require_approval("APPROVED")
        for wrong in ("approved", "Approved", " APPROVED", "APPROVED ", "APPROVED.",
                      "'APPROVED'", "yes", ""):
            with self.subTest(wrong=wrong), self.assertRaises(tool.LifecycleError):
                tool.require_approval(wrong)


class TestPublicVerification(unittest.TestCase):
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
            observed = tool._public_request(tool.PUBLIC_GUIDE_URL, max_bytes=1)
        self.assertEqual(observed["status"], 404)
        self.assertEqual(observed["body"], b"")
        self.assertFalse(response.read_called)

    def test_read_before_requires_both_yost_301s_to_fixed_pdf(self):
        good = {"status": 301, "location": tool.PDF_URL, "redirect_by": "Yoast SEO",
                "content_type": "text/html", "body": b""}
        with mock.patch.object(tool, "_public_request", return_value=good) as request:
            self.assertEqual(tool.read_public_before(), PUBLIC_BEFORE)
            self.assertEqual([call.args[0] for call in request.call_args_list],
                             [tool.PUBLIC_GUIDE_URL, tool.PUBLIC_QUERY_URL])
        bad = dict(good, location="https://frpdepots.com/other.pdf")
        with mock.patch.object(tool, "_public_request", return_value=bad), \
                self.assertRaises(tool.LifecycleError):
            tool.read_public_before()

    def test_after_requires_unavailable_and_never_redirect(self):
        responses = [
            {"status": 404, "location": None, "redirect_by": None,
             "content_type": "text/html", "body": b"not found"},
            {"status": 410, "location": None, "redirect_by": None,
             "content_type": "text/html", "body": b"gone"},
        ]
        with mock.patch.object(tool, "_public_request", side_effect=responses):
            self.assertEqual(tool.require_public_after_unavailable(),
                             {"guide_status": 404, "query_status": 410, "redirect": False})
        available = dict(responses[0], status=200)
        with mock.patch.object(tool, "_public_request", return_value=available), \
                self.assertRaises(tool.IndeterminateError):
            tool.require_public_after_unavailable()
        redirect = dict(responses[0], status=404, location=tool.PDF_URL)
        with mock.patch.object(tool, "_public_request", return_value=redirect), \
                self.assertRaises(tool.IndeterminateError):
            tool.require_public_after_unavailable()

    def test_asset_verification_is_full_body_sha256_not_header_trust(self):
        def fake_request(url, *, max_bytes):
            expected = next(a for a in tool.FIXED_ASSETS if a["url"] == url)
            # Only model one byte sequence by replacing expected hashes during this unit.
            return {"status": 200, "location": None, "redirect_by": None,
                    "content_type": expected["content_type"], "body": b"asset"}

        fake_assets = tuple({**a, "bytes": 5,
                             "sha256": hashlib.sha256(b"asset").hexdigest()}
                            for a in tool.FIXED_ASSETS)
        with mock.patch.object(tool, "FIXED_ASSETS", fake_assets), \
                mock.patch.object(tool, "_public_request", side_effect=fake_request):
            self.assertEqual(tool.verify_historical_assets(), list(fake_assets))
        with mock.patch.object(tool, "FIXED_ASSETS", fake_assets), \
                mock.patch.object(tool, "_public_request", side_effect=fake_request):
            wrong = list(fake_assets)
            wrong[0] = {**wrong[0], "sha256": "0" * 64}
            with mock.patch.object(tool, "FIXED_ASSETS", tuple(wrong)), \
                    self.assertRaises(tool.LifecycleError):
                tool.verify_historical_assets()


class TestAdminActor(unittest.TestCase):
    class Page:
        def __init__(self, result):
            self.url = tool.ADMIN_EDIT_URL
            self.result = result
            self.calls = []

        def evaluate(self, expression, arg=None):
            self.calls.append((expression, arg))
            if arg is None:
                return {"id": str(tool.ATTACHMENT_ID), "type": tool.ATTACHMENT_TYPE,
                        "status": tool.BEFORE_STATUS}
            return self.result

        def goto(self, url, wait_until=None, timeout=None):
            self.url = url

        def query_selector(self, selector):
            return None

    def test_rest_actor_uses_fixed_urls_and_boolean_write_switch_only(self):
        result = {"nonce_status": 200, "status": 200, "data": live_object()}
        page = self.Page(result)
        admin = tool.AdminPage(page)
        self.assertEqual(admin.read_full(expected_status="inherit"), live_object())
        _, arg = page.calls[-1]
        self.assertEqual(arg, {"write": False, "nonceUrl": tool.REST_NONCE_URL,
                               "readUrl": tool.REST_READ_URL, "writeUrl": tool.REST_URL})
        page.result = {"nonce_status": 200, "status": 200,
                       "data": live_object(tool.AFTER_STATUS)}
        admin.make_private_once()
        _, arg = page.calls[-1]
        self.assertTrue(arg["write"])
        self.assertEqual(set(arg), {"write", "nonceUrl", "readUrl", "writeUrl"})

    def test_write_transport_or_json_failure_is_indeterminate(self):
        for result in (
            {"nonce_status": 403, "status": 0, "data": None},
            {"nonce_status": 200, "status": 400, "data": {}},
            {"nonce_status": 200, "status": 200, "data": None},
        ):
            with self.subTest(result=result), self.assertRaises(tool.IndeterminateError):
                tool.AdminPage(self.Page(result)).make_private_once()


class TestStageAndPlan(Harness):
    def test_stage_is_read_only_and_records_complete_fingerprints(self):
        plan_path = self.stage()
        plan = self.read_json(plan_path)
        self.assertEqual(self.admin.writes, 0)
        self.assertFalse(tool.plan_lock_path(plan_path).exists())
        self.assertFalse(tool.result_path(plan_path).exists())
        self.assertEqual(plan["before"]["object"], live_object())
        self.assertEqual(plan["before"]["full_sha256"], tool.digest_for(live_object()))
        self.assertEqual(plan["before"]["protected_sha256"],
                         tool.digest_for(tool.protected_projection(live_object())))
        self.assertEqual(plan["write_payload"], {"status": "private"})
        self.assertEqual(plan["historical_assets"], list(tool.FIXED_ASSETS))
        self.assertEqual(self.events[0], "mutex_enter")

    def test_plan_is_closed_hash_bearing_nonce_and_exact_24_hours(self):
        path = self.stage()
        plan = self.read_json(path)
        saved = plan.pop("sha256")
        self.assertEqual(saved, tool.digest_for(plan))
        self.assertRegex(plan["nonce"], r"^[0-9a-f]{32}$")
        created = tool.datetime.fromisoformat(plan["created_utc"])
        expires = tool.datetime.fromisoformat(plan["expires_utc"])
        self.assertEqual(expires - created, timedelta(hours=24))
        self.assertEqual(set(plan), tool.PLAN_KEYS)
        self.assertEqual(set(plan["before"]), tool.BEFORE_KEYS)

    def test_stage_receipt_says_write_false(self):
        self.stage()
        rows = self.receipts_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "wordpress_hetron_guide_lifecycle_plan_staged")
        self.assertIn("write=false", rows[0]["evidence"])

    def test_tamper_without_rehash_is_refused(self):
        path = self.stage()
        self.rewrite_plan(path, rehash=False, action="other")
        with self.assertRaises(tool.LifecycleError):
            tool.load_plan(str(path))

    def test_rehashed_identity_payload_route_or_asset_change_is_refused(self):
        mutations = [
            {"object_id": 1833},
            {"rest_route": "/wp-json/wp/v2/media/1833"},
            {"write_payload": {"status": "draft"}},
            {"action": "arbitrary"},
            {"historical_assets": []},
        ]
        for changes in mutations:
            with self.subTest(changes=changes):
                path = self.stage()
                self.rewrite_plan(path, **changes)
                with self.assertRaises(tool.LifecycleError):
                    tool.load_plan(str(path))
                shutil.rmtree(self.plan_dir)
                self.plan_dir.mkdir()

    def test_rehashed_unknown_plan_key_is_refused(self):
        path = self.stage()
        self.rewrite_plan(path, arbitrary=True)
        with self.assertRaises(tool.LifecycleError):
            tool.load_plan(str(path))

    def test_expired_or_non_24_hour_plan_is_refused(self):
        path = self.stage()
        value = self.read_json(path)
        old = tool.utc_now() - timedelta(hours=25)
        self.rewrite_plan(path, created_utc=old.isoformat(),
                          expires_utc=(old + timedelta(hours=24)).isoformat())
        with self.assertRaisesRegex(tool.LifecycleError, "expired"):
            tool.load_plan(str(path))
        # Fresh path, wrong duration.
        shutil.rmtree(self.plan_dir)
        self.plan_dir.mkdir()
        path = self.stage()
        value = self.read_json(path)
        created = tool.datetime.fromisoformat(value["created_utc"])
        self.rewrite_plan(path, expires_utc=(created + timedelta(hours=23)).isoformat())
        with self.assertRaisesRegex(tool.LifecycleError, "24-hour"):
            tool.load_plan(str(path))

    def test_plan_outside_fixed_directory_is_refused(self):
        outside = self.tmp / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaises(tool.LifecycleError):
            tool.load_plan(str(outside))


class TestCommit(Harness):
    def test_success_is_one_write_private_readback_public_unavailable_assets_preserved(self):
        plan = self.stage()
        self.events.clear()
        self.commit(plan)
        self.assertEqual(self.admin.writes, 1)
        self.assertEqual(self.admin.obj["status"], "private")
        self.assertEqual(self.public_after_calls, 1)
        # Assets: stage, commit preflight, commit post-write.
        self.assertEqual(self.asset_calls, 3)
        lock = self.read_json(tool.plan_lock_path(plan))
        result = self.read_json(tool.result_path(plan))
        self.assertEqual(lock["status"], "committed_verified")
        self.assertFalse(lock["retry"])
        self.assertEqual(lock["attempts_allowed"], 1)
        self.assertEqual(lock["attempts_started"], 1)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["write_count"], 1)
        self.assertTrue(result["object_preserved"])
        self.assertTrue(result["files_preserved"])
        self.assertTrue(result["historical_assets_preserved"])
        self.assertEqual(result["public_after"],
                         {"guide_status": 404, "query_status": 410, "redirect": False})
        self.assertLess(self.events.index("mutex_enter"), self.events.index("write_side_effect"))
        self.assertIn("authenticated_read", self.events[self.events.index("write_side_effect") + 1:])

    def test_wrong_approval_refuses_before_admin_and_before_attempt(self):
        plan = self.stage()
        opens = self.admin_opens
        writes = self.admin.writes
        with self.assertRaises(tool.LifecycleError):
            self.commit(plan, "Approved")
        self.assertEqual(self.admin_opens, opens)
        self.assertEqual(self.admin.writes, writes)
        self.assertFalse(tool.plan_lock_path(plan).exists())
        self.assertFalse(tool.result_path(plan).exists())

    def test_mutex_busy_is_free_refusal_before_attempt(self):
        plan = self.stage()

        @contextlib.contextmanager
        def busy(*args, **kwargs):
            raise tool.UiLaneBusy("busy")
            yield  # pragma: no cover

        with mock.patch.object(tool, "ui_browser_lock", busy), \
                self.assertRaises(tool.UiLaneBusy):
            self.commit(plan)
        self.assertEqual(self.admin.writes, 0)
        self.assertFalse(tool.plan_lock_path(plan).exists())
        self.assertFalse(tool.result_path(plan).exists())

    def test_complete_before_drift_refuses_without_burning_plan(self):
        plan = self.stage()
        self.admin.obj["comment_status"] = "closed"
        with self.assertRaises(tool.LifecycleError):
            self.commit(plan)
        self.assertEqual(self.admin.writes, 0)
        self.assertFalse(tool.plan_lock_path(plan).exists())
        self.assertFalse(tool.result_path(plan).exists())

    def test_public_redirect_drift_refuses_without_burning_plan(self):
        plan = self.stage()
        with mock.patch.object(tool, "read_public_before",
                               side_effect=tool.LifecycleError("drift")), \
                self.assertRaises(tool.LifecycleError):
            self.commit(plan)
        self.assertEqual(self.admin.writes, 0)
        self.assertFalse(tool.plan_lock_path(plan).exists())

    def test_historical_asset_drift_refuses_without_burning_plan(self):
        plan = self.stage()
        changed = list(json.loads(json.dumps(tool.FIXED_ASSETS)))
        changed[0]["sha256"] = "0" * 64
        with mock.patch.object(tool, "verify_historical_assets", return_value=changed), \
                self.assertRaises(tool.LifecycleError):
            self.commit(plan)
        self.assertEqual(self.admin.writes, 0)
        self.assertFalse(tool.plan_lock_path(plan).exists())

    def test_write_failure_is_permanent_indeterminate_and_never_retried(self):
        plan = self.stage()
        self.admin.fail_write = TimeoutError("fake")
        with self.assertRaises(tool.IndeterminateError):
            self.commit(plan)
        self.assertEqual(self.admin.writes, 1)
        lock = self.read_json(tool.plan_lock_path(plan))
        result = self.read_json(tool.result_path(plan))
        self.assertEqual(lock["status"], "indeterminate")
        self.assertEqual(result["status"], "INDETERMINATE")
        self.assertFalse(lock["retry"])
        with self.assertRaises(tool.LifecycleError):
            self.commit(plan)
        self.assertEqual(self.admin.writes, 1)

    def test_post_write_public_verification_failure_is_permanent_indeterminate(self):
        plan = self.stage()
        with mock.patch.object(tool, "require_public_after_unavailable",
                               side_effect=tool.IndeterminateError("still public")), \
                self.assertRaises(tool.IndeterminateError):
            self.commit(plan)
        self.assertEqual(self.admin.writes, 1)
        self.assertEqual(self.admin.obj["status"], "private")
        self.assertEqual(self.read_json(tool.plan_lock_path(plan))["status"], "indeterminate")
        self.assertEqual(self.read_json(tool.result_path(plan))["status"], "INDETERMINATE")

    def test_authenticated_readback_protected_drift_is_permanent_indeterminate(self):
        plan = self.stage()
        original = self.admin.make_private_once

        def write_with_drift():
            value = original()
            self.admin.obj["title"]["raw"] = "changed"
            self.admin.obj["title"]["rendered"] = "changed"
            return value

        self.admin.make_private_once = write_with_drift
        with self.assertRaises(tool.IndeterminateError):
            self.commit(plan)
        self.assertEqual(self.admin.writes, 1)
        self.assertEqual(self.read_json(tool.plan_lock_path(plan))["status"], "indeterminate")

    def test_success_receipt_and_replay_refusal(self):
        plan = self.stage()
        self.commit(plan)
        rows = self.receipts_rows()
        self.assertEqual([row["action"] for row in rows], [
            "wordpress_hetron_guide_lifecycle_plan_staged",
            "wordpress_hetron_guide_lifecycle_committed_and_verified",
        ])
        with self.assertRaises(tool.LifecycleError):
            self.commit(plan)
        self.assertEqual(self.admin.writes, 1)


if __name__ == "__main__":
    unittest.main()
