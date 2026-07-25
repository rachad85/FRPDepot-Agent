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

import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
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
            text = google_backfill.text_from_msg(b"raw-outlook-bytes")
        self.assertIn("Fittings - RFQ", text)
        self.assertIn("buyer@sctfrp.com", text)
        self.assertIn("Please quote the elbows.", text)
        self.assertEqual(self.leftovers(), [])

    def test_one_bad_message_does_not_poison_the_next(self):
        """The per-PID name meant a stuck file broke every later .msg in the run."""
        with self._install_fake(_ExplodingMessage):
            with self.assertRaises(ValueError):
                google_backfill.text_from_msg(b"first-bad")
        with self._install_fake(_WorkingMessage):
            text = google_backfill.text_from_msg(b"second-good")
        self.assertIn("Fittings - RFQ", text)
        self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()
