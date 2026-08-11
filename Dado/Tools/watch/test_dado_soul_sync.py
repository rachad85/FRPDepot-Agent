"""The SOUL mirror->live sync, including the cases where it must REFUSE.

The failure this guards against is silent: a commissioned rule sitting in the
committed mirror while Dado runs without it. It happened twice on 2026-08-11.
The opposite failure would be worse - clobbering a live SOUL.md that holds
something the mirror does not - so the refusal cases carry as much weight here
as the happy path.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import dado_soul_sync as sync


BASE = "# SOUL\n\nRule 1: drafts only.\nRule 2: no secrets in chat.\n"


class SoulSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.mirror = root / "mirror_SOUL.md"
        self.live = root / "live_SOUL.md"
        self.receipts = root / "receipts.jsonl"
        self._real_receipts = sync.RECEIPTS
        sync.RECEIPTS = self.receipts
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        sync.RECEIPTS = self._real_receipts
        self._tmp.cleanup()

    def receipt_actions(self) -> list[str]:
        if not self.receipts.exists():
            return []
        return [
            json.loads(line)["action"]
            for line in self.receipts.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # -- nothing to do -------------------------------------------------
    def test_identical_files_are_silent_and_write_nothing(self) -> None:
        self.mirror.write_text(BASE, encoding="utf-8", newline="\n")
        self.live.write_text(BASE, encoding="utf-8", newline="\n")
        before = self.live.stat().st_mtime_ns
        self.assertEqual(sync.sync(self.mirror, self.live, dry_run=False), 0)
        self.assertEqual(self.live.stat().st_mtime_ns, before)
        self.assertEqual(self.receipt_actions(), [])

    # -- the drift this exists for -------------------------------------
    def test_a_rule_added_to_the_mirror_reaches_the_live_profile(self) -> None:
        self.live.write_text(BASE, encoding="utf-8", newline="\n")
        updated = BASE + "Rule 3: one commissioned tool per write.\n"
        self.mirror.write_text(updated, encoding="utf-8", newline="\n")
        self.assertEqual(sync.sync(self.mirror, self.live, dry_run=False), 0)
        self.assertEqual(self.live.read_bytes(), self.mirror.read_bytes())
        self.assertIn("Rule 3", self.live.read_text(encoding="utf-8"))
        self.assertIn("dado_soul_sync_applied", self.receipt_actions())

    def test_the_replaced_file_is_kept_as_a_backup(self) -> None:
        self.live.write_text(BASE, encoding="utf-8", newline="\n")
        self.mirror.write_text(BASE + "Rule 3: added.\n", encoding="utf-8", newline="\n")
        sync.sync(self.mirror, self.live, dry_run=False)
        backup = self.live.with_name(sync.BACKUP_NAME)
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding="utf-8"), BASE)

    def test_dry_run_reports_and_changes_nothing(self) -> None:
        self.live.write_text(BASE, encoding="utf-8", newline="\n")
        self.mirror.write_text(BASE + "Rule 3: added.\n", encoding="utf-8", newline="\n")
        self.assertEqual(sync.sync(self.mirror, self.live, dry_run=True), 0)
        self.assertEqual(self.live.read_text(encoding="utf-8"), BASE)
        self.assertFalse(self.live.with_name(sync.BACKUP_NAME).exists())
        self.assertEqual(self.receipt_actions(), [])

    # -- the case where copying would destroy work ---------------------
    def test_it_refuses_when_the_live_file_holds_something_the_mirror_lacks(self) -> None:
        self.mirror.write_text(BASE, encoding="utf-8", newline="\n")
        live_only = BASE + "Rule 3: written straight into the live profile.\n"
        self.live.write_text(live_only, encoding="utf-8", newline="\n")
        self.assertEqual(sync.sync(self.mirror, self.live, dry_run=False), 1)
        self.assertEqual(self.live.read_text(encoding="utf-8"), live_only)
        self.assertIn(
            "dado_soul_sync_refused_live_has_unmirrored_content", self.receipt_actions()
        )

    def test_it_refuses_a_changed_rule_rather_than_overwriting_it(self) -> None:
        # A modification is a deletion plus an insertion, so it must refuse too:
        # the tool cannot tell which side is the intended one.
        self.mirror.write_text(BASE, encoding="utf-8", newline="\n")
        self.live.write_text(
            BASE.replace("Rule 1: drafts only.", "Rule 1: drafts only, amended live."),
            encoding="utf-8", newline="\n",
        )
        self.assertEqual(sync.sync(self.mirror, self.live, dry_run=False), 1)
        self.assertIn("amended live", self.live.read_text(encoding="utf-8"))

    # -- it must never corrupt a HARD RULES file -----------------------
    def test_line_endings_of_the_live_file_are_preserved(self) -> None:
        self.live.write_bytes(BASE.replace("\n", "\r\n").encode("utf-8"))
        self.mirror.write_text(BASE + "Rule 3: added.\n", encoding="utf-8", newline="\n")
        # Must report SUCCESS: a CRLF live file is byte-different from the LF
        # mirror by design, so verifying raw bytes would fail every correct sync.
        self.assertEqual(sync.sync(self.mirror, self.live, dry_run=False), 0)
        self.assertIn("dado_soul_sync_applied", self.receipt_actions())
        written = self.live.read_bytes()
        self.assertIn(b"\r\n", written)
        self.assertNotIn(b"\n\n\r", written)
        self.assertIn("Rule 3", written.decode("utf-8"))

    def test_no_temporary_file_is_left_behind(self) -> None:
        self.live.write_text(BASE, encoding="utf-8", newline="\n")
        self.mirror.write_text(BASE + "Rule 3: added.\n", encoding="utf-8", newline="\n")
        sync.sync(self.mirror, self.live, dry_run=False)
        leftovers = [p.name for p in self.live.parent.glob("*.tmp")]
        self.assertEqual(leftovers, [])

    # -- it must never take the keep-alive down ------------------------
    def test_a_missing_file_on_either_side_is_a_quiet_skip(self) -> None:
        self.mirror.write_text(BASE, encoding="utf-8", newline="\n")
        self.assertEqual(sync.sync(self.mirror, self.live, dry_run=False), 0)
        self.live.write_text(BASE, encoding="utf-8", newline="\n")
        self.assertEqual(
            sync.sync(self.mirror.with_name("absent.md"), self.live, dry_run=False), 0
        )

    def test_an_unreadable_receipts_log_does_not_break_the_sync(self) -> None:
        self.live.write_text(BASE, encoding="utf-8", newline="\n")
        self.mirror.write_text(BASE + "Rule 3: added.\n", encoding="utf-8", newline="\n")
        sync.RECEIPTS = Path("Z:/definitely/not/a/path/receipts.jsonl")
        self.assertEqual(sync.sync(self.mirror, self.live, dry_run=False), 0)
        self.assertEqual(self.live.read_bytes(), self.mirror.read_bytes())


if __name__ == "__main__":
    unittest.main()
