"""Guard tests for the nightly conduct review (autonomy programme B6, 2026-08-21).

The reviewer may fix the fit profile and tool bugs; it may NOT touch the SOUL
mirror. Any SOUL change made during the review is reverted byte for byte --
whatever section it landed in -- and reported as a guard trip. The report path
and the Telegram delivery rules are unchanged.

Nothing here runs claude, git, the collector, or touches C:\\FRPDepot: every
path and every subprocess is redirected into a temporary folder.
"""
from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conduct_review  # noqa: E402

SOUL_BEFORE = (
    "# SOUL\n\n## WHO YOU SERVE\n\n- Rachad.\n\n"
    "## HARD RULES\n\n1. DRAFTS ONLY.\n\n"
    "## WORKING STATE\n\n- Folder: C:\\FRPDepot.\n"
)
CLEAN = f"{conduct_review.CLEAN_MARKER}\nQuiet day, 12 turns."
FINDING = ("FINDING 1: something | evidence: log line | fix: proposed\n"
           "NEEDS-RACHAD: nothing\n"
           "SUMMARY: one finding, no edits.")
NEEDS_HIM = ("FINDING 1: something | evidence: log line | fix: proposed\n"
             "NEEDS-RACHAD: decide the freight class\n"
             "SUMMARY: one decision is his.")


def completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class ConductReviewGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.mirror = root / "DadoProfile" / "SOUL.md"
        self.mirror.parent.mkdir(parents=True)
        self.mirror.write_bytes(SOUL_BEFORE.encode("utf-8"))
        self.live = root / "live" / "SOUL.md"
        self.live.parent.mkdir(parents=True)
        self.live.write_bytes(SOUL_BEFORE.encode("utf-8"))
        self.review_dir = root / "conduct_reviews"
        self.failure_dir = root / "conduct_failures"
        self.bundle = root / "bundle.md"
        self.bundle.write_text("# bundle\n", encoding="utf-8")
        self.git_calls: list[tuple[str, ...]] = []

        def fake_git(*args):
            self.git_calls.append(args)
            return completed(stdout="")

        for name, value in {
            "MIRROR_SOUL": self.mirror,
            "LIVE_SOUL": self.live,
            "REVIEW_DIR": self.review_dir,
            "FAILURE_DIR": self.failure_dir,
        }.items():
            patcher = mock.patch.object(conduct_review, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, kwargs in (
            ("git", {"side_effect": fake_git}),
            ("collect_bundle", {"return_value": completed(stdout=f"{self.bundle}\n")}),
        ):
            patcher = mock.patch.object(conduct_review, target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(conduct_review.time, "sleep", lambda _s: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(sys, "argv", ["conduct_review.py", "2026-08-20"])
        patcher.start()
        self.addCleanup(patcher.stop)

    # ------------------------------------------------------------ helpers
    def reviewer(self, output: str, rewrite_mirror_to: str | None = None):
        """A fake headless reviewer: optionally edits the mirror, then answers."""
        def _run(_prompt: str):
            if rewrite_mirror_to is not None:
                self.mirror.write_bytes(rewrite_mirror_to.encode("utf-8"))
            return completed(stdout=output)
        return _run

    def run_review(self, reviewer) -> str:
        out = io.StringIO()
        with mock.patch.object(conduct_review, "run_reviewer", side_effect=reviewer):
            with redirect_stdout(out):
                rc = conduct_review.main()
        self.assertEqual(rc, 0)
        return out.getvalue()

    def report(self) -> str:
        return (self.review_dir / "2026-08-20.md").read_text(encoding="utf-8")

    def checkouts(self) -> list[tuple[str, ...]]:
        return [c for c in self.git_calls if c and c[0] == "checkout"]

    # ------------------------------------------------------------ the guard
    def test_an_edit_outside_hard_rules_is_reverted_and_reported(self):
        edited = SOUL_BEFORE.replace("- Folder: C:\\FRPDepot.", "- Folder: C:\\FRPDepot.\n- New rule the reviewer invented.")
        ping = self.run_review(self.reviewer(FINDING, rewrite_mirror_to=edited))
        self.assertEqual(self.mirror.read_bytes(), SOUL_BEFORE.encode("utf-8"),
                         "the WORKING STATE edit must be undone byte for byte")
        self.assertIn("GUARD TRIPPED", self.report())
        self.assertIn("GUARD TRIPPED", ping, "a guard trip is one of the three things Rachad hears about")
        self.assertEqual(self.live.read_bytes(), SOUL_BEFORE.encode("utf-8"),
                         "a reverted mirror is never synced to the live profile")

    def test_an_edit_inside_hard_rules_is_reverted_too(self):
        edited = SOUL_BEFORE.replace("1. DRAFTS ONLY.", "1. DRAFTS ONLY, mostly.")
        self.run_review(self.reviewer(FINDING, rewrite_mirror_to=edited))
        self.assertEqual(self.mirror.read_bytes(), SOUL_BEFORE.encode("utf-8"))
        self.assertIn("GUARD TRIPPED", self.report())

    def test_an_untouched_mirror_is_not_a_guard_trip_and_a_clean_day_is_silent(self):
        ping = self.run_review(self.reviewer(CLEAN))
        self.assertEqual(self.mirror.read_bytes(), SOUL_BEFORE.encode("utf-8"))
        self.assertNotIn("GUARD", self.report())
        self.assertEqual(ping.strip(), "", "clean nights stay silent on Telegram")
        self.assertEqual(self.checkouts(), [])

    def test_the_revert_restores_pre_review_bytes_not_git_head(self):
        """A backend edit made earlier that day and not yet committed must survive
        the night: the guard writes back what was there BEFORE the reviewer, and
        never reaches for `git checkout` while that write succeeds."""
        uncommitted = SOUL_BEFORE + "- Backend edit from this afternoon, not yet committed.\n"
        self.mirror.write_bytes(uncommitted.encode("utf-8"))
        self.run_review(self.reviewer(FINDING, rewrite_mirror_to=uncommitted + "- Reviewer addition.\n"))
        self.assertEqual(self.mirror.read_bytes(), uncommitted.encode("utf-8"))
        self.assertEqual(self.checkouts(), [], "git checkout would have destroyed the backend edit")
        self.assertIn("GUARD TRIPPED", self.report())

    def test_git_checkout_is_only_the_fallback_when_the_byte_restore_fails(self):
        edited = SOUL_BEFORE + "- Reviewer addition.\n"
        with mock.patch.object(conduct_review, "write_mirror", side_effect=OSError("disk")):
            self.run_review(self.reviewer(FINDING, rewrite_mirror_to=edited))
        self.assertEqual(self.checkouts(), [("checkout", "--", "DadoProfile/SOUL.md")])
        self.assertIn("reverted from git instead", self.report())

    def test_the_guard_is_wider_than_the_old_hard_rules_check(self):
        """Direct unit test: a change that leaves HARD RULES identical still trips."""
        before = SOUL_BEFORE.encode("utf-8")
        after = SOUL_BEFORE.replace("- Rachad.", "- Rachad, mostly.")
        self.mirror.write_bytes(after.encode("utf-8"))
        self.assertEqual(conduct_review.hard_rules_section(SOUL_BEFORE),
                         conduct_review.hard_rules_section(after),
                         "the old guard would have let this through")
        note = conduct_review.revert_reviewer_soul_changes(before)
        self.assertEqual(note, conduct_review.GUARD_NOTE)
        self.assertEqual(self.mirror.read_bytes(), before)
        self.assertEqual(conduct_review.revert_reviewer_soul_changes(before), "",
                         "an untouched mirror returns no note")

    # --------------------------------------------- the guard on the exit paths
    def test_the_guard_runs_when_the_reviewer_times_out(self):
        """B6 on the TimeoutExpired path: a reviewer killed mid-run has still
        edited the mirror, so the pre-review bytes go back before main()
        returns and the timeout ping carries the guard note."""
        edited = SOUL_BEFORE + "- Reviewer addition before the timeout.\n"

        def _run(_prompt: str):
            self.mirror.write_bytes(edited.encode("utf-8"))
            raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

        ping = self.run_review(_run)
        self.assertEqual(self.mirror.read_bytes(), SOUL_BEFORE.encode("utf-8"),
                         "the edit made before the timeout must be undone byte for byte")
        self.assertIn("timed out", ping)
        self.assertIn("GUARD TRIPPED", ping)
        self.assertEqual(self.live.read_bytes(), SOUL_BEFORE.encode("utf-8"),
                         "a reverted mirror is never synced to the live profile")
        self.assertEqual(self.checkouts(), [])

    def test_the_guard_runs_when_the_reviewer_fails_or_returns_nothing(self):
        """B6 on the failed-run path, across the one fast retry: attempt 1
        exits non-zero and attempt 2 exits 0 with EMPTY output (also a failed
        run); both edit the mirror, and it is still restored before main()
        returns, with no review file and no live sync."""
        edited = SOUL_BEFORE + "- Reviewer addition before the crash.\n"
        calls: list[int] = []

        def _run(_prompt: str):
            calls.append(1)
            self.mirror.write_bytes(edited.encode("utf-8"))
            if len(calls) == 1:
                return completed(stdout="", returncode=1, stderr="boom")
            return completed(stdout="", returncode=0)

        ping = self.run_review(_run)
        self.assertEqual(len(calls), 2, "a fast failure gets exactly one retry")
        self.assertEqual(self.mirror.read_bytes(), SOUL_BEFORE.encode("utf-8"))
        self.assertIn("could not run", ping)
        self.assertIn("GUARD TRIPPED", ping)
        self.assertEqual(self.live.read_bytes(), SOUL_BEFORE.encode("utf-8"))
        self.assertFalse((self.review_dir / "2026-08-20.md").exists())
        self.assertEqual(self.checkouts(), [])

    def test_a_timeout_or_failure_with_an_untouched_mirror_is_not_a_guard_trip(self):
        def _timeout(_prompt: str):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

        ping = self.run_review(_timeout)
        self.assertIn("timed out", ping)
        self.assertNotIn("GUARD", ping)

        ping = self.run_review(lambda _prompt: completed(stdout="", returncode=2))
        self.assertIn("could not run", ping)
        self.assertNotIn("GUARD", ping)
        self.assertEqual(self.mirror.read_bytes(), SOUL_BEFORE.encode("utf-8"))

    # ------------------------------------------------------------ the prompt
    def test_the_prompt_no_longer_grants_soul_edits(self):
        prompt = conduct_review.PROMPT
        self.assertNotIn("SOUL mirror wording", prompt)
        self.assertNotIn('sections OTHER than "## HARD RULES"', prompt)
        never = prompt[prompt.index("NEVER:"):]
        self.assertIn("DadoProfile\\SOUL.md", never)
        self.assertIn("ANY section", never)
        allowed = prompt[prompt.index("AUTO-FIX AUTHORITY"):prompt.index("NEVER:")]
        self.assertNotIn("SOUL", allowed, "the SOUL is not on the auto-fix list at all")
        self.assertIn("fit_profile.md", allowed)
        self.assertIn("tool scripts under Dado\\Tools", allowed)

    # ------------------------------------------------ unchanged: path + ping
    def test_report_path_and_needs_rachad_delivery_are_unchanged(self):
        ping = self.run_review(self.reviewer(NEEDS_HIM))
        self.assertTrue((self.review_dir / "2026-08-20.md").exists())
        self.assertIn("needs you", ping)
        self.assertIn("decide the freight class", ping)

    def test_a_legitimate_mirror_change_still_reaches_the_live_profile(self):
        """The sync branch is untouched: a mirror that differs from live for a
        reason other than the reviewer (the backend edited it) is still copied
        over after a review that did not touch it."""
        edited = SOUL_BEFORE + "- Backend edit.\n"
        self.mirror.write_bytes(edited.encode("utf-8"))
        self.run_review(self.reviewer(CLEAN))
        self.assertEqual(self.live.read_bytes(), edited.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
