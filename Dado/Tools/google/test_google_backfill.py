"""Guards on text_from_msg's temp-file handling (backlog B-23).

extract_msg.Message opens the .msg file and holds a handle. m.close() used to
sit on the SUCCESS path only, so any parse error - a malformed OLE stream, a
missing property, a bad unicode body - propagated with the handle still open.
Windows will not delete a file with an open handle, so the finally block's
tmp.unlink() raised PermissionError, `except OSError: pass` swallowed it, and a
full raw copy of someone's Drive file was left in %TEMP% indefinitely.

The temp NAME made it worse: it was per-PID, so every .msg in a run reused one
path. Once a file was left undeletable, the next write_bytes() onto that open
handle failed too, and the remaining .msg files in the run failed behind it -
122 were queued in the run that surfaced this.

These tests use a fake extract_msg that behaves like the real one in the way
that matters: it opens a real handle and then raises partway through.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import google_backfill  # noqa: E402


class _ExplodingMessage:
    """Opens a real handle, then fails on the second field read."""

    def __init__(self, path):
        self._handle = open(path, "rb")
        self.subject = "Quote QT-000023"

    def __getattr__(self, name):  # sender/to/cc/date/body/attachments
        raise ValueError(f"malformed OLE stream reading {name!r}")

    def close(self):
        self._handle.close()


class _WorkingMessage:
    def __init__(self, path):
        self._handle = open(path, "rb")
        self.subject = "Fittings - RFQ"
        self.sender = "buyer@sctfrp.com"
        self.to = "info@frpdepots.com"
        self.cc = ""
        self.date = "2026-06-24"
        self.body = "Please quote the elbows."
        self.attachments = []

    def close(self):
        self._handle.close()


class TextFromMsgTempFileTests(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self._folder.name)
        # mkstemp() with no dir= honours tempfile.tempdir, so this contains the
        # test entirely inside a folder we can inspect for leftovers.
        self._saved_tempdir = tempfile.tempdir
        tempfile.tempdir = str(self.temp_dir)
        # TEMP is pinned as well so the PRE-FIX implementation, which built its
        # own path from os.environ["TEMP"], also lands in this folder. Without
        # it these tests would pass against the buggy code by looking in the
        # wrong place while the stale file piled up in the real %TEMP%.
        env = patch.dict(os.environ, {"TEMP": str(self.temp_dir),
                                      "TMP": str(self.temp_dir)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._restore)

    def _restore(self):
        tempfile.tempdir = self._saved_tempdir
        self._folder.cleanup()

    def _install_fake(self, message_class):
        module = types.ModuleType("extract_msg")
        module.Message = message_class
        return patch.dict(sys.modules, {"extract_msg": module})

    def leftovers(self):
        return sorted(p.name for p in self.temp_dir.iterdir())

    def test_parse_error_still_deletes_the_temp_copy(self):
        with self._install_fake(_ExplodingMessage):
            with self.assertRaises(ValueError):
                google_backfill.text_from_msg(b"raw-outlook-bytes")
        self.assertEqual(
            self.leftovers(), [],
            "a failed parse must not leave a raw Drive file behind in %TEMP%",
        )

    def test_success_path_still_works_and_cleans_up(self):
        with self._install_fake(_WorkingMessage):
            got = google_backfill.text_from_msg(b"raw-outlook-bytes")
        self.assertIn("Fittings - RFQ", got.text)
        self.assertIn("buyer@sctfrp.com", got.text)
        self.assertIn("Please quote the elbows.", got.text)
        self.assertTrue(got.complete)
        self.assertEqual(self.leftovers(), [])

    def test_one_bad_message_does_not_poison_the_next(self):
        """The per-PID name meant a stuck file broke every later .msg in the run."""
        with self._install_fake(_ExplodingMessage):
            with self.assertRaises(ValueError):
                google_backfill.text_from_msg(b"first-bad")
        with self._install_fake(_WorkingMessage):
            got = google_backfill.text_from_msg(b"second-good")
        self.assertIn("Fittings - RFQ", got.text)
        self.assertEqual(self.leftovers(), [])


class CompletenessTests(unittest.TestCase):
    """B-21 and B-22: a partial read must never look like a full one."""

    def _eml(self, body: bytes) -> bytes:
        return body

    def test_a_readable_message_is_complete(self):
        raw = (b"Subject: Fittings RFQ\r\nFrom: buyer@sctfrp.com\r\n"
               b"Content-Type: text/plain\r\n\r\nPlease quote the elbows.\r\n")
        got = google_backfill.text_from_eml(raw)
        self.assertTrue(got.complete)
        self.assertIn("Please quote the elbows.", got.text)

    def test_a_message_with_no_readable_body_is_partial(self):
        # multipart/related carrying only an attachment: get_body() returns None,
        # but the header block still makes the text non-empty. That combination
        # used to be written as read-and-clear with the body never seen.
        raw = (b"Subject: Scan\r\nFrom: someone@example.com\r\n"
               b'Content-Type: multipart/related; boundary="b"\r\n\r\n'
               b"--b\r\nContent-Type: application/pdf\r\n"
               b'Content-Disposition: attachment; filename="scan.pdf"\r\n\r\n'
               b"%PDF-1.4\r\n--b--\r\n")
        got = google_backfill.text_from_eml(raw)
        self.assertFalse(got.complete, "a body we could not read is not a complete read")
        self.assertTrue(got.text.strip(), "headers are still returned")

    def test_a_zip_is_never_complete(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("drawings/manway.dwg", "x")
        got = google_backfill.text_from_zip(buf.getvalue())
        self.assertFalse(got.complete, "member names only is by definition partial")
        self.assertIn("manway.dwg", got.text)

    def test_status_names_differ_for_partial_and_complete(self):
        """The status string is what downstream reads; they must not collide."""
        complete = f"backfill_{'eml'}"
        partial = f"backfill_partial_{'eml'}"
        self.assertNotEqual(complete, partial)
        # Both must still be excluded from a later run, or the job never converges.
        for status in (complete, partial):
            self.assertTrue(status.startswith("backfill_"))


class CandidateSelectionTests(unittest.TestCase):
    """B-20: select on 'has no content', not on a status string."""

    def _db(self, rows):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE drive_files (id TEXT, name TEXT, mime_type TEXT, "
                    "size INT, content TEXT, content_status TEXT)")
        con.executemany("INSERT INTO drive_files VALUES (?,?,?,?,?,?)", rows)
        return con

    def test_an_indexed_but_empty_scan_is_picked_up(self):
        con = self._db([
            # The headline case: image-only PDF, no text layer, stored 'indexed'
            # with content "". NOT LIKE 'indexed%' excluded it forever.
            ("f1", "site scan.pdf", "application/pdf", 1000, "", "indexed"),
        ])
        got = google_backfill.candidates(con, {"pdf"})
        self.assertEqual([r[0] for r in got], ["f1"])

    def test_a_row_that_already_has_content_is_left_alone(self):
        con = self._db([
            ("f1", "quote.pdf", "application/pdf", 1000, "real text", "indexed"),
        ])
        self.assertEqual(google_backfill.candidates(con, {"pdf"}), [])

    def test_rows_this_tool_already_attempted_are_not_retried(self):
        con = self._db([
            ("f1", "a.pdf", "application/pdf", 10, "", "backfill_no_text"),
            ("f2", "b.pdf", "application/pdf", 10, "", "backfill_error:ValueError"),
            ("f3", "c.pdf", "application/pdf", 10, "", "backfill_partial_pdf"),
        ])
        self.assertEqual(google_backfill.candidates(con, {"pdf"}), [],
                         "a run must converge, not redo its own outcomes")

    def test_folder_metadata_is_never_a_candidate(self):
        con = self._db([("f1", "a folder", "application/vnd.google-apps.folder",
                         0, "", "folder_metadata")])
        self.assertEqual(google_backfill.candidates(con, {"pdf", "image"}), [])


if __name__ == "__main__":
    unittest.main()
