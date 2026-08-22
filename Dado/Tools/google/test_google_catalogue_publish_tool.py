from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import fitz

import google_catalogue_publish_tool as tool

REAL_ARTIFACT_BYTES = tool.ARTIFACT_PATH.read_bytes()


class PreconditionFailed(RuntimeError):
    def __init__(self):
        super().__init__("HTTP 412 precondition failed")
        self.resp = type("Response", (), {"status": 412})()


class FakeExecutable:
    def __init__(self, payload):
        self.payload = payload
        self.headers: dict[str, str] = {}

    def execute(self, num_retries=None, **_kwargs):
        value = self.payload(num_retries, self.headers) if callable(self.payload) else self.payload
        if isinstance(value, Exception):
            raise value
        return value


class FakeFiles:
    def __init__(self, harness: "FakeDrive"):
        self.harness = harness

    def update(self, **kwargs):
        self.harness.update_calls.append(kwargs)

        def perform(num_retries, headers):
            self.harness.events.append("update")
            self.harness.execute_retries.append(num_retries)
            self.harness.if_match.append(dict(headers))
            if self.harness.require_lock and not list(self.harness.lock_dir.glob("*.json")):
                raise AssertionError("update occurred before attempt lock")
            if self.harness.update_error:
                raise self.harness.update_error
            return {
                "id": tool.EXPECTED_FILE_ID,
                "title": tool.EXPECTED_FILE_NAME,
                "mimeType": tool.EXPECTED_MIME_TYPE,
            }

        return FakeExecutable(perform)

    def get(self, fileId=None, supportsAllDrives=None, fields=None):
        if str(fileId) == tool.EXPECTED_FILE_ID:
            return FakeExecutable(self.harness.file_item)
        if str(fileId) not in self.harness.parents:
            raise AssertionError(f"unexpected parent lookup {fileId}")
        return FakeExecutable(self.harness.parents[str(fileId)])


class FakeService:
    def __init__(self, harness: "FakeDrive"):
        self.harness = harness

    def files(self):
        return FakeFiles(self.harness)


class FakeDrive:
    def __init__(self, lock_dir: Path):
        self.lock_dir = lock_dir
        self.require_lock = False
        self.events: list[str] = []
        self.update_calls: list[dict] = []
        self.execute_retries: list[int | None] = []
        self.if_match: list[dict] = []
        self.update_error: Exception | None = None
        self.file_item = {
            "id": tool.EXPECTED_FILE_ID,
            "title": tool.EXPECTED_FILE_NAME,
            "mimeType": tool.EXPECTED_MIME_TYPE,
            "parents": [{"id": tool.EXPECTED_PARENT_IDS[-1]}],
            "editable": True,
            "labels": {"trashed": False},
            "etag": '"etag-before"',
            "md5Checksum": "a" * 32,
            "modifiedDate": "2026-08-15T17:47:49.200Z",
            "version": "27",
            "downloadUrl": "https://example.invalid/download",
            "fileSize": "100",
            "alternateLink": "https://drive.google.com/file/d/fixed/view",
            "webContentLink": "https://drive.google.com/uc?id=fixed&export=download",
        }
        self.parents = {}
        for index, identifier in enumerate(tool.EXPECTED_PARENT_IDS):
            parent = [{"id": tool.EXPECTED_PARENT_IDS[index - 1]}] if index else []
            self.parents[identifier] = {
                "id": identifier,
                "title": tool.EXPECTED_PARENT_PATH[index],
                "parents": parent,
            }

    def service(self):
        return FakeService(self)


class CatalogueToolTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.plan_dir = base / "plans"
        self.result_dir = base / "results"
        self.backup_dir = base / "backups"
        self.receipts = base / "receipts.jsonl"
        for name, value in (
            ("PLAN_DIR", self.plan_dir),
            ("RESULT_DIR", self.result_dir),
            ("BACKUP_DIR", self.backup_dir),
            ("RECEIPTS", self.receipts),
        ):
            patcher = mock.patch.object(tool, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fake = FakeDrive(self.plan_dir / ".commit-locks")
        self.artifact = tool.expected_artifact_projection()
        self.artifact_bytes = REAL_ARTIFACT_BYTES
        self.before = {
            "id": tool.EXPECTED_FILE_ID,
            "name": tool.EXPECTED_FILE_NAME,
            "mime_type": tool.EXPECTED_MIME_TYPE,
            "parent_ids": list(tool.EXPECTED_PARENT_IDS),
            "parent_path": list(tool.EXPECTED_PARENT_PATH),
            "editable": True,
            "trashed": False,
            "etag": '"etag-before"',
            "md5": "a" * 32,
            "sha256": "1" * 64,
            "bytes": 100,
            "modified_time": "2026-08-15T17:47:49.200Z",
            "version": "27",
            "alternate_link": "https://drive.google.com/file/d/fixed/view",
            "web_content_link": "https://drive.google.com/uc?id=fixed&export=download",
            "pages": tool.EXPECTED_PAGES,
            "page_headings": list(tool.EXPECTED_PAGE_HEADINGS),
        }
        self.after = copy.deepcopy(self.before)
        self.after.update({
            "etag": '"etag-after"',
            "md5": self.artifact["md5"],
            "sha256": self.artifact["sha256"],
            "bytes": self.artifact["bytes"],
            "modified_time": "2026-08-16T03:00:00.000Z",
            "version": "28",
        })

    def valid_plan(self) -> dict:
        return tool.build_plan(copy.deepcopy(self.before), copy.deepcopy(self.artifact))

    def write_plan(self, plan: dict | None = None) -> Path:
        self.plan_dir.mkdir(parents=True, exist_ok=True)
        path = self.plan_dir / "plan.json"
        path.write_text(json.dumps(plan or self.valid_plan()), encoding="utf-8")
        return path

    def reseal(self, plan: dict) -> dict:
        core = copy.deepcopy(plan)
        core.pop("sha256", None)
        return {**core, "sha256": tool.digest_for(core)}

    def patch_fixed_runtime(self, live_side_effect=None):
        if live_side_effect is None:
            live_side_effect = [
                (copy.deepcopy(self.before), b"old-live-pdf"),
                (copy.deepcopy(self.after), self.artifact_bytes),
            ]
        patches = [
            mock.patch.object(tool, "artifact_projection", return_value=(copy.deepcopy(self.artifact), self.artifact_bytes)),
            mock.patch.object(tool, "live_projection", side_effect=live_side_effect),
            mock.patch.object(tool.auth, "drive_service", return_value=self.fake.service()),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)


class ConstantAndArtifactTests(CatalogueToolTestCase):
    def test_exact_file_parent_and_artifact_are_pinned(self):
        self.assertEqual(tool.EXPECTED_FILE_ID, "1PqcjZf-SSCbBVp7quMri_ernaOPZDPz1")
        self.assertEqual(tool.EXPECTED_FILE_NAME, "FRP Depots Catalogue 2026.pdf")
        self.assertEqual(tool.EXPECTED_MIME_TYPE, "application/pdf")
        self.assertEqual(len(tool.EXPECTED_PARENT_IDS), 6)
        self.assertEqual(tool.EXPECTED_PARENT_PATH[-2:], ("FRPDEPOT INC.", "Specs & Catalog"))
        self.assertEqual(tool.EXPECTED_ARTIFACT_SHA256, "00cbed31d9a15fb466950ed785be881cd994c83346404c9a65858954450f837b")
        self.assertEqual(tool.EXPECTED_ARTIFACT_BYTES, 23443803)
        self.assertEqual(tool.APPROVAL_WORD, "APPROVED")
        self.assertEqual(tool.PLAN_LIFETIME_HOURS, 24)

    def test_real_fixed_artifact_is_still_exact_and_readable(self):
        projection, data = tool.artifact_projection()
        self.assertEqual(projection, tool.expected_artifact_projection())
        self.assertEqual(tool.sha256_bytes(data), tool.EXPECTED_ARTIFACT_SHA256)
        self.assertEqual(projection["pages"], 11)

    def test_same_bytes_at_any_other_resolved_path_are_refused(self):
        other = Path(self.tmp.name) / "alias.pdf"
        other.write_bytes(REAL_ARTIFACT_BYTES)
        with mock.patch.object(tool, "ARTIFACT_PATH", other):
            with self.assertRaisesRegex(tool.CataloguePublishError, "resolves outside"):
                tool.artifact_projection()

    def test_parser_exposes_only_stage_and_commit(self):
        parser = tool.build_parser()
        stage = parser.parse_args(["stage"])
        self.assertIs(stage.func, tool.command_stage)
        commit = parser.parse_args(["commit", "--plan", "x", "--approval", "APPROVED"])
        self.assertIs(commit.func, tool.command_commit)
        source = Path(tool.__file__).read_text(encoding="utf-8")
        for forbidden in ("add_parser(\"delete\")", "add_parser(\"create\")", "add_parser(\"rename\")", "add_parser(\"share\")"):
            self.assertNotIn(forbidden, source)

    def test_parent_identity_is_the_exact_live_chain(self):
        ids, names = tool._parent_identity(self.fake.service(), self.fake.file_item)
        self.assertEqual(ids, tool.EXPECTED_PARENT_IDS)
        self.assertEqual(names, tool.EXPECTED_PARENT_PATH)
        wrong = copy.deepcopy(self.fake.file_item)
        wrong["parents"] = []
        with self.assertRaisesRegex(tool.CataloguePublishError, "exactly one parent"):
            tool._parent_identity(self.fake.service(), wrong)


class PdfProjectionTests(unittest.TestCase):
    def make_pdf(self, headings=None) -> bytes:
        headings = list(headings or tool.EXPECTED_PAGE_HEADINGS)
        document = fitz.open()
        for heading in headings:
            page = document.new_page()
            page.insert_text((72, 72), heading)
        data = document.tobytes()
        document.close()
        return data

    def test_valid_eleven_page_heading_projection(self):
        result = tool.pdf_projection(self.make_pdf())
        self.assertEqual(result["pages"], 11)
        self.assertEqual(result["page_headings"], list(tool.EXPECTED_PAGE_HEADINGS))

    def test_wrong_page_count_is_refused(self):
        with self.assertRaisesRegex(tool.CataloguePublishError, "exactly 11 pages"):
            tool.pdf_projection(self.make_pdf(tool.EXPECTED_PAGE_HEADINGS[:-1]))

    def test_missing_heading_is_refused(self):
        headings = list(tool.EXPECTED_PAGE_HEADINGS)
        headings[6] = "Wrong pipe page"
        with self.assertRaisesRegex(tool.CataloguePublishError, "page 7 is missing"):
            tool.pdf_projection(self.make_pdf(headings))

    def test_non_pdf_is_refused(self):
        with self.assertRaisesRegex(tool.CataloguePublishError, "readable PDF"):
            tool.pdf_projection(b"not a pdf")


class EligibilityTests(CatalogueToolTestCase):
    def test_stage_eligible_state_passes(self):
        tool.eligibility(self.before, self.artifact)
        tool.eligibility(self.before, self.artifact, copy.deepcopy(self.before))

    def test_already_live_digest_is_refused(self):
        live = copy.deepcopy(self.before)
        live["sha256"] = self.artifact["sha256"]
        with self.assertRaisesRegex(tool.CataloguePublishError, "already live"):
            tool.eligibility(live, self.artifact)

    def test_every_fixed_identity_field_is_guarded(self):
        mutations = {
            "id": "other",
            "name": "copy.pdf",
            "mime_type": "text/plain",
            "parent_ids": ["other"],
            "parent_path": ["Elsewhere"],
            "editable": False,
            "trashed": True,
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                live = copy.deepcopy(self.before)
                live[key] = value
                with self.assertRaises(tool.CataloguePublishError):
                    tool.eligibility(live, self.artifact)

    def test_every_fingerprint_field_is_required(self):
        for key in ("etag", "md5", "sha256", "bytes", "modified_time", "version", "alternate_link", "web_content_link"):
            with self.subTest(key=key):
                live = copy.deepcopy(self.before)
                live[key] = ""
                with self.assertRaisesRegex(tool.CataloguePublishError, key):
                    tool.eligibility(live, self.artifact)

    def test_artifact_or_live_drift_is_refused(self):
        artifact = copy.deepcopy(self.artifact)
        artifact["sha256"] = "f" * 64
        with self.assertRaisesRegex(tool.CataloguePublishError, "pinned reviewed"):
            tool.eligibility(self.before, artifact)
        drift = copy.deepcopy(self.before)
        drift["version"] = "28"
        with self.assertRaisesRegex(tool.CataloguePublishError, "changed after staging"):
            tool.eligibility(drift, self.artifact, self.before)


class PlanTests(CatalogueToolTestCase):
    def test_plan_round_trip_and_tamper_detection(self):
        plan = self.valid_plan()
        path = self.write_plan(plan)
        self.assertEqual(tool.load_plan(path)["sha256"], plan["sha256"])
        plan["artifact"]["sha256"] = "f" * 64
        path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(tool.CataloguePublishError, "hash check failed"):
            tool.load_plan(path)

    def test_closed_schema_and_target_artifact_contract_are_fixed(self):
        cases = []
        plan = self.valid_plan(); plan["extra"] = True; cases.append((plan, "closed"))
        plan = self.valid_plan(); plan["target"]["id"] = "other"; cases.append((plan, "exact commissioned"))
        plan = self.valid_plan(); plan["artifact"]["path"] = "other"; cases.append((plan, "exact approved"))
        plan = self.valid_plan(); plan["source"] = "changed"; cases.append((plan, "source"))
        plan = self.valid_plan(); plan["write_contract"]["retry"] = True; cases.append((plan, "contract"))
        for plan, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(tool.CataloguePublishError, message):
                    tool.load_plan(self.write_plan(self.reseal(plan)))

    def test_expired_future_and_wrong_lifetime_are_refused(self):
        expired = self.valid_plan()
        created = datetime.now(timezone.utc) - timedelta(hours=25)
        expired["created_utc"] = created.isoformat()
        expired["expires_utc"] = (created + timedelta(hours=24)).isoformat()
        with self.assertRaisesRegex(tool.CataloguePublishError, "expired"):
            tool.load_plan(self.write_plan(self.reseal(expired)))
        future = self.valid_plan()
        created = datetime.now(timezone.utc) + timedelta(hours=2)
        future["created_utc"] = created.isoformat()
        future["expires_utc"] = (created + timedelta(hours=24)).isoformat()
        with self.assertRaisesRegex(tool.CataloguePublishError, "future"):
            tool.load_plan(self.write_plan(self.reseal(future)))
        lifetime = self.valid_plan()
        created = datetime.now(timezone.utc)
        lifetime["created_utc"] = created.isoformat()
        lifetime["expires_utc"] = (created + timedelta(hours=48)).isoformat()
        with self.assertRaisesRegex(tool.CataloguePublishError, "exactly 24 hours"):
            tool.load_plan(self.write_plan(self.reseal(lifetime)))

    def test_nonce_and_before_schema_are_closed(self):
        plan = self.valid_plan(); plan["nonce"] = "short"
        with self.assertRaisesRegex(tool.CataloguePublishError, "nonce"):
            tool.load_plan(self.write_plan(self.reseal(plan)))
        plan = self.valid_plan(); plan["before"]["surprise"] = True
        with self.assertRaisesRegex(tool.CataloguePublishError, "fingerprint fields"):
            tool.load_plan(self.write_plan(self.reseal(plan)))

    def test_named_unapproved_plan_is_permanently_superseded(self):
        old_digest = next(iter(tool.SUPERSEDED_PLAN_SHA256))
        self.plan_dir.mkdir(parents=True, exist_ok=True)
        path = self.plan_dir / "old.json"
        path.write_text(json.dumps({"sha256": old_digest}), encoding="utf-8")
        with self.assertRaisesRegex(tool.CataloguePublishError, "superseded"):
            tool.load_plan(path)

    def test_digest_keyed_exclusive_replay_lock(self):
        digest = "a" * 64
        path = tool.lock_path(digest)
        tool.write_json_exclusive(path, {"status": "in_flight"})
        with self.assertRaisesRegex(tool.CataloguePublishError, "already entered commit"):
            tool.write_json_exclusive(tool.lock_path(digest), {"status": "in_flight"})
        tool.overwrite_json(path, {"status": "committed_verified"})
        with self.assertRaisesRegex(tool.CataloguePublishError, "cannot be replayed"):
            tool.write_json_exclusive(tool.lock_path(digest), {"status": "in_flight"})
        tool.overwrite_json(path, {"status": "needs_restage"})
        with self.assertRaisesRegex(tool.CataloguePublishError, "Re-stage"):
            tool.write_json_exclusive(tool.lock_path(digest), {"status": "in_flight"})
        for bad in ("", "A" * 64, "a" * 63, "zz"):
            with self.subTest(bad=bad), self.assertRaises(tool.CataloguePublishError):
                tool.lock_path(bad)


class FlowTests(CatalogueToolTestCase):
    def test_stage_is_read_only_and_shows_complete_plan(self):
        self.patch_fixed_runtime(live_side_effect=[(copy.deepcopy(self.before), b"old")])
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            tool.command_stage(argparse.Namespace())
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "STAGED_NOT_COMMITTED")
        self.assertFalse(result["remote_write_performed"])
        self.assertEqual(result["approval"], "APPROVED")
        self.assertRegex(result["plan_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(self.fake.update_calls), 0)
        path = Path(result["plan"])
        self.assertTrue(path.is_file())
        self.assertEqual(tool.load_plan(path)["artifact"], self.artifact)
        self.assertIn("staged_read_only", self.receipts.read_text(encoding="utf-8"))

    def test_commit_makes_one_conditional_media_update_after_lock_and_verifies(self):
        self.patch_fixed_runtime()
        plan_path = self.write_plan()
        self.fake.require_lock = True
        output = io.StringIO()
        original_lock = tool.write_json_exclusive

        def ordered_lock(path, value):
            self.fake.events.append("lock")
            return original_lock(path, value)

        with mock.patch("sys.stdout", output), \
             mock.patch.object(tool, "eligibility", wraps=tool.eligibility) as guard, \
             mock.patch.object(tool, "write_json_exclusive", side_effect=ordered_lock):
            tool.command_commit(argparse.Namespace(plan=str(plan_path), approval="APPROVED"))
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["downloaded_live_sha256"], self.artifact["sha256"])
        self.assertEqual(len(self.fake.update_calls), 1)
        self.assertEqual(self.fake.update_calls[0]["fileId"], tool.EXPECTED_FILE_ID)
        self.assertNotIn("body", self.fake.update_calls[0])
        self.assertEqual(self.fake.execute_retries, [0])
        self.assertEqual(self.fake.if_match, [{"If-Match": self.before["etag"]}])
        self.assertEqual(self.fake.events, ["lock", "update"])
        self.assertGreaterEqual(guard.call_count, 3)
        locks = list((self.plan_dir / ".commit-locks").glob("*.json"))
        self.assertEqual(len(locks), 1)
        lock = json.loads(locks[0].read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")
        self.assertIn("restore --plan", lock["rollback_route"])
        self.assertFalse(lock["permanent_lock"])
        self.assertEqual(lock["owner_go"], "APPROVED")
        self.assertTrue(Path(lock["backup"]).is_file())
        self.assertTrue(Path(lock["result"]).is_file())
        self.assertTrue(result["plan_spent"])
        self.assertTrue(tool.owner_authority.live_state_path(plan_path).is_file())

    def test_a_conditional_word_is_refused_before_auth_and_a_plain_yes_is_not(self):
        """2026-08-21 (A1): exact APPROVED still works, any clear go counts, a
        condition, a question, a blank or a non-string still refuses before
        Google is touched."""
        plan_path = self.write_plan()
        for bad in ("yes but wait for the new cover", "hold on", "APPROVED?", "", object(), "APPROVED\nnow"):
            with self.subTest(bad=repr(bad)):
                with mock.patch.object(tool.auth, "drive_service") as auth_call:
                    with self.assertRaises(tool.CataloguePublishError):
                        tool.command_commit(argparse.Namespace(plan=str(plan_path), approval=bad))
                    auth_call.assert_not_called()
                self.assertEqual(len(self.fake.update_calls), 0)
                self.assertFalse((self.plan_dir / ".commit-locks").exists())
        self.patch_fixed_runtime()
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            tool.command_commit(argparse.Namespace(plan=str(plan_path), approval="yes go ahead and publish"))
        self.assertEqual(json.loads(output.getvalue())["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(len(self.fake.update_calls), 1)

    def restore_after_commit(self, live_after_commit=None, live_after_restore=None, approval="yes put it back"):
        """Commit, then restore, with the live file reading `after` (or the given state)."""
        self.patch_fixed_runtime()
        plan_path = self.write_plan()
        with mock.patch("sys.stdout", io.StringIO()):
            tool.command_commit(argparse.Namespace(plan=str(plan_path), approval="APPROVED"))
        backup = Path(json.loads(next((self.plan_dir / ".commit-locks").glob(f"{'[0-9a-f]' * 64}.json")).read_text(encoding="utf-8"))["backup"])
        restored_state = copy.deepcopy(self.before)
        restored_state.update({"etag": '"etag-restored"', "modified_time": "2026-08-17T00:00:00.000Z", "version": "29",
                               "sha256": tool.sha256_bytes(b"old-live-pdf"), "md5": tool.md5_bytes(b"old-live-pdf"),
                               "bytes": len(b"old-live-pdf"), "pages": 1, "page_headings": ["x"]})
        after = copy.deepcopy(live_after_commit if live_after_commit is not None else self.after)
        side_effect = [(after, self.artifact_bytes)]
        if live_after_restore is not None:
            side_effect.append((live_after_restore, b"old-live-pdf"))
        else:
            side_effect.append((restored_state, b"old-live-pdf"))
        with mock.patch.object(tool, "live_projection", side_effect=side_effect), \
             mock.patch.object(tool, "pdf_projection", return_value={"pages": 1, "page_headings": ["x"]}):
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                tool.command_restore(argparse.Namespace(plan=str(plan_path), approval=approval))
        return plan_path, backup, json.loads(output.getvalue())

    def test_restore_reuploads_the_backed_up_previous_pdf_once(self):
        # The plan's `before` must describe the backup bytes for the restore to match.
        self.before.update({"sha256": tool.sha256_bytes(b"old-live-pdf"), "md5": tool.md5_bytes(b"old-live-pdf"),
                            "bytes": len(b"old-live-pdf")})
        self.after.update({"sha256": self.artifact["sha256"]})
        plan_path, backup, result = self.restore_after_commit()
        self.assertEqual(result["status"], "RESTORED")
        self.assertEqual(backup.read_bytes(), b"old-live-pdf")
        self.assertEqual(len(self.fake.update_calls), 2, "commit once, restore once")
        self.assertEqual(self.fake.if_match[-1], {"If-Match": self.after["etag"]})
        record = json.loads(tool.owner_authority.restore_record_path(plan_path).read_text(encoding="utf-8"))
        self.assertEqual(record["owner_go"], "yes put it back")
        with self.assertRaisesRegex(tool.CataloguePublishError, "cannot be replayed"):
            with mock.patch.object(tool, "live_projection", return_value=(copy.deepcopy(self.after), self.artifact_bytes)):
                tool.command_restore(argparse.Namespace(plan=str(plan_path), approval="yes"))
        self.assertEqual(len(self.fake.update_calls), 2)

    def test_restore_leaves_a_file_that_moved_since_alone_and_needs_his_go(self):
        self.before.update({"sha256": tool.sha256_bytes(b"old-live-pdf"), "md5": tool.md5_bytes(b"old-live-pdf"),
                            "bytes": len(b"old-live-pdf")})
        moved = copy.deepcopy(self.after); moved["sha256"] = "9" * 64
        plan_path, _, result = self.restore_after_commit(live_after_commit=moved)
        self.assertEqual(result["status"], "NOT_RESTORED")
        self.assertIn("moved since", result["reason"])
        self.assertEqual(len(self.fake.update_calls), 1)
        with self.assertRaises(tool.CataloguePublishError):
            tool.command_restore(argparse.Namespace(plan=str(plan_path), approval="wait"))

    def test_live_drift_refuses_before_lock_and_update(self):
        drift = copy.deepcopy(self.before); drift["version"] = "28"
        self.patch_fixed_runtime(live_side_effect=[(drift, b"changed")])
        with self.assertRaisesRegex(tool.CataloguePublishError, "changed after staging"):
            tool.command_commit(argparse.Namespace(plan=str(self.write_plan()), approval="APPROVED"))
        self.assertEqual(len(self.fake.update_calls), 0)
        self.assertFalse((self.plan_dir / ".commit-locks").exists())

    def test_local_artifact_drift_refuses_before_auth_lock_and_update(self):
        plan_path = self.write_plan()
        bad = copy.deepcopy(self.artifact); bad["sha256"] = "f" * 64
        with mock.patch.object(tool, "artifact_projection", return_value=(bad, b"bad")), \
             mock.patch.object(tool.auth, "drive_service") as auth_call:
            with self.assertRaisesRegex(tool.CataloguePublishError, "changed after staging"):
                tool.command_commit(argparse.Namespace(plan=str(plan_path), approval="APPROVED"))
            auth_call.assert_not_called()
        self.assertFalse((self.plan_dir / ".commit-locks").exists())

    def test_update_failure_needs_restage_and_the_same_plan_is_not_retried(self):
        """A4 (2026-08-21): no silent retry, no permanent lock. The SAME plan is
        bound to stale live state and is refused; a re-stage reads Drive again."""
        self.patch_fixed_runtime(live_side_effect=[(copy.deepcopy(self.before), b"old")])
        self.fake.update_error = RuntimeError("simulated timeout")
        with self.assertRaisesRegex(tool.CataloguePublishError, "re-stage") as caught:
            tool.command_commit(argparse.Namespace(plan=str(self.write_plan()), approval="APPROVED"))
        self.assertNotIn("permanent", str(caught.exception).casefold())
        self.assertEqual(len(self.fake.update_calls), 1)
        lock_path = next((self.plan_dir / ".commit-locks").glob("*.json"))
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertFalse(lock["permanent_lock"])
        with self.assertRaisesRegex(tool.CataloguePublishError, "Re-stage"):
            tool.command_commit(argparse.Namespace(plan=str(self.plan_dir / "plan.json"), approval="APPROVED"))
        self.assertEqual(len(self.fake.update_calls), 1)

    def test_http_412_is_authoritative_no_write_and_needs_restage(self):
        self.patch_fixed_runtime(live_side_effect=[(copy.deepcopy(self.before), b"old")])
        self.fake.update_error = PreconditionFailed()
        with self.assertRaisesRegex(tool.CataloguePublishError, "authoritatively rejected"):
            tool.command_commit(argparse.Namespace(plan=str(self.write_plan()), approval="APPROVED"))
        self.assertEqual(len(self.fake.update_calls), 1)
        lock_path = next((self.plan_dir / ".commit-locks").glob("*.json"))
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "needs_restage")
        self.assertTrue(lock["authoritative_no_write"])
        with self.assertRaisesRegex(tool.CataloguePublishError, "Re-stage"):
            tool.command_commit(argparse.Namespace(plan=str(self.plan_dir / "plan.json"), approval="APPROVED"))
        self.assertEqual(len(self.fake.update_calls), 1)

    def test_byte_mismatch_after_update_is_indeterminate_and_needs_restage(self):
        self.patch_fixed_runtime(live_side_effect=[
            (copy.deepcopy(self.before), b"old"),
            (copy.deepcopy(self.after), b"wrong-readback"),
        ])
        with self.assertRaisesRegex(tool.CataloguePublishError, "re-stage"):
            tool.command_commit(argparse.Namespace(plan=str(self.write_plan()), approval="APPROVED"))
        lock = json.loads(next((self.plan_dir / ".commit-locks").glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertEqual(len(self.fake.update_calls), 1)

    def test_protected_name_parent_or_share_link_change_locks_indeterminate(self):
        for key, value in (("name", "Changed.pdf"), ("parent_ids", ["other"]), ("alternate_link", "other")):
            with self.subTest(key=key):
                # Each case needs a clean isolated replay namespace.
                with tempfile.TemporaryDirectory() as tmp:
                    with mock.patch.object(tool, "PLAN_DIR", Path(tmp) / "plans"), \
                         mock.patch.object(tool, "RESULT_DIR", Path(tmp) / "results"), \
                         mock.patch.object(tool, "BACKUP_DIR", Path(tmp) / "backups"), \
                         mock.patch.object(tool, "RECEIPTS", Path(tmp) / "receipts.jsonl"):
                        plan_dir = tool.PLAN_DIR; plan_dir.mkdir(parents=True)
                        plan = self.valid_plan(); plan_path = plan_dir / "plan.json"
                        plan_path.write_text(json.dumps(plan), encoding="utf-8")
                        damaged = copy.deepcopy(self.after); damaged[key] = value
                        fake = FakeDrive(plan_dir / ".commit-locks")
                        with mock.patch.object(tool, "artifact_projection", return_value=(copy.deepcopy(self.artifact), self.artifact_bytes)), \
                             mock.patch.object(tool, "live_projection", side_effect=[(copy.deepcopy(self.before), b"old"), (damaged, self.artifact_bytes)]), \
                             mock.patch.object(tool.auth, "drive_service", return_value=fake.service()):
                            with self.assertRaisesRegex(tool.CataloguePublishError, "re-stage"):
                                tool.command_commit(argparse.Namespace(plan=str(plan_path), approval="APPROVED"))
                        lock = json.loads(next((plan_dir / ".commit-locks").glob("*.json")).read_text(encoding="utf-8"))
                        self.assertEqual(lock["status"], "indeterminate_needs_restage")
                        self.assertEqual(len(fake.update_calls), 1)


class StaticSurfaceTests(unittest.TestCase):
    def test_remote_write_surface_is_one_exact_media_update(self):
        source = Path(tool.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count("service.files().update("), 1)
        for forbidden in (
            "service.files().insert(", "service.files().delete(", "service.files().copy(",
            "service.files().patch(",
            "service.permissions()", "service.revisions()", "newRevision=False",
            "removeParents=", "addParents=", "setModifiedDate=", "sendNotificationEmails",
            "gmail", "smtp", "playwright", "connect_over_cdp",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('request.headers["If-Match"]', source)
        self.assertIn("num_retries=0", source)
        self.assertIn("resumable=False", source)

    def test_auth_reuses_exact_drive_only_vault_and_no_new_grant_code(self):
        source = Path(tool.__file__).read_text(encoding="utf-8")
        self.assertIn("import google_investments_auth as auth", source)
        for forbidden in ("InstalledAppFlow", "flow.run_local_server", "token.json", "grant.json", "SCOPES ="):
            self.assertNotIn(forbidden, source)

    def test_write_contract_discloses_one_attempt_the_restore_route_and_no_mail(self):
        self.assertEqual(tool.WRITE_CONTRACT["remote_requests"], 1)
        self.assertEqual(tool.WRITE_CONTRACT["attempts"], 1)
        self.assertFalse(tool.WRITE_CONTRACT["retry"])
        self.assertIn("restore --plan", tool.WRITE_CONTRACT["rollback_route"])
        self.assertFalse(tool.WRITE_CONTRACT["email_or_notification"])

    def test_parser_exposes_restore_with_the_shared_go_arguments(self):
        parser = tool.build_parser()
        args = parser.parse_args(["restore", "--plan", "x", "--approval", "yes", "--approval-lane", "discord"])
        self.assertIs(args.func, tool.command_restore)
        self.assertEqual(args.approval_lane, "discord")


if __name__ == "__main__":
    unittest.main()
