"""Guards on the two ways Dado's monitoring used to fail silently.

B-02  A failed brain run returned "" and is_silent("") is True, so a quota
      exhaustion, a gateway outage or a 45-minute timeout logged "silent" and
      sent nothing - indistinguishable from a clean sweep. Rachad reads that
      quiet as "nothing needs me". Observed live 2026-07-24: the 21:47 digest
      run died around 22:32 with 15 overdue threads on file and said nothing.

B-03  _try_send ran `hermes send` with timeout=60 and caught nothing, so a hung
      Telegram call (TimeoutExpired) or a missing binary (FileNotFoundError)
      escaped send_clean entirely - queue_undelivered never ran and the alert
      was destroyed rather than queued for the next sweep.

Both are absence-of-alert bugs, so every test here asserts that something WAS
said or queued. A test that only checks the happy path cannot see either.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dado_inbox_reasoner as reasoner  # noqa: E402
import dado_followup_digest as digest  # noqa: E402


class TrySendSurvivesAnyFailure(unittest.TestCase):
    """B-03: _try_send must report a failure, never raise one."""

    def _assert_reported_not_raised(self, exc):
        with patch.object(reasoner.subprocess, "run", side_effect=exc):
            rc, err = reasoner._try_send("an alert Rachad needs")
        self.assertEqual(rc, -1)
        self.assertIn(type(exc).__name__, err)

    def test_hung_telegram_call_is_reported(self):
        self._assert_reported_not_raised(subprocess.TimeoutExpired("hermes", 60))

    def test_missing_hermes_binary_is_reported(self):
        self._assert_reported_not_raised(FileNotFoundError("hermes not on PATH"))


class SendCleanAlwaysQueues(unittest.TestCase):
    """B-03: whatever goes wrong, the alert reaches the undelivered queue."""

    def test_alert_is_queued_when_every_attempt_hangs(self):
        with tempfile.TemporaryDirectory() as folder:
            queue = Path(folder) / "undelivered_alerts.txt"
            with (
                patch.object(reasoner, "UNDELIVERED", queue),
                patch.object(reasoner, "log"),
                patch.object(reasoner.time, "sleep"),
                patch.object(reasoner.subprocess, "run",
                             side_effect=subprocess.TimeoutExpired("hermes", 60)),
            ):
                delivered = reasoner.send_clean("CAD 4,101.30 outstanding on INV-000040")
            self.assertFalse(delivered)
            self.assertIn("CAD 4,101.30 outstanding", queue.read_text(encoding="utf-8"))

    def test_alert_is_queued_even_if_the_retry_loop_itself_raises(self):
        with tempfile.TemporaryDirectory() as folder:
            queue = Path(folder) / "undelivered_alerts.txt"
            with (
                patch.object(reasoner, "UNDELIVERED", queue),
                patch.object(reasoner, "log"),
                patch.object(reasoner.time, "sleep"),
                patch.object(reasoner, "_try_send", side_effect=RuntimeError("boom")),
            ):
                delivered = reasoner.send_clean("a money-landing alert")
            self.assertFalse(delivered)
            self.assertIn("a money-landing alert", queue.read_text(encoding="utf-8"))


class BrainFailureIsNotSilence(unittest.TestCase):
    """B-02: None (the run never produced text) must not read as [SILENT]."""

    def test_run_dado_returns_none_on_nonzero_exit(self):
        done = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="quota")
        with (patch.object(reasoner.subprocess, "run", return_value=done),
              patch.object(reasoner, "log")):
            self.assertIsNone(reasoner.run_dado())

    def test_run_dado_returns_none_on_timeout(self):
        with (patch.object(reasoner.subprocess, "run",
                           side_effect=subprocess.TimeoutExpired("hermes", 2700)),
              patch.object(reasoner, "log")):
            self.assertIsNone(reasoner.run_dado())

    def test_is_silent_still_swallows_none_which_is_why_order_matters(self):
        # Documents the trap: run_once MUST test `msg is None` before is_silent.
        self.assertTrue(reasoner.is_silent(None))
        self.assertTrue(reasoner.is_silent(""))
        self.assertTrue(reasoner.is_silent("[SILENT]"))
        self.assertFalse(reasoner.is_silent("Brian needs pricing today."))

    def test_sweep_alerts_instead_of_going_quiet_when_the_brain_fails(self):
        sent = []
        with (
            patch.object(reasoner, "flush_undelivered"),
            patch.object(reasoner, "prefetch_triage"),
            patch.object(reasoner, "run_dado", return_value=None),
            patch.object(reasoner, "send_clean", side_effect=lambda m: sent.append(m)),
            patch.object(reasoner, "log"),
        ):
            reasoner.run_once()
        self.assertEqual(len(sent), 1, "a failed sweep must say so, not go silent")
        self.assertIn("NOT an all-clear", sent[0])

    def test_a_genuine_silent_reply_stays_silent(self):
        sent = []
        with (
            patch.object(reasoner, "flush_undelivered"),
            patch.object(reasoner, "prefetch_triage"),
            patch.object(reasoner, "run_dado", return_value="[SILENT]"),
            patch.object(reasoner, "send_clean", side_effect=lambda m: sent.append(m)),
            patch.object(reasoner, "log"),
        ):
            reasoner.run_once()
        self.assertEqual(sent, [], "a quiet morning must still be quiet")


class ScrubKeepsBusinessContent(unittest.TestCase):
    """B-09: the scrub is a backstop, not a censor."""

    def test_a_line_mentioning_a_job_id_survives(self):
        text = "Their job ID 88-A needs the FRP grating quote priced by Friday."
        self.assertEqual(reasoner.scrub_noise(text).strip(), text)

    def test_a_line_mentioning_cleaned_up_survives(self):
        text = "Forte cleaned up the site and wants the revised manway price."
        self.assertEqual(reasoner.scrub_noise(text).strip(), text)

    def test_pending_is_not_peeled_as_a_ticker(self):
        text = "Pending... payment from SCT Composites, CAD 4,101.30."
        self.assertTrue(reasoner.scrub_noise(text).strip().startswith("Pending"),
                        "peeling the first word inverts what Rachad reads")

    def test_real_machinery_lines_are_still_dropped(self):
        text = "suite green\nBrian needs pricing today.\nad-hoc verification done"
        self.assertEqual(reasoner.scrub_noise(text).strip(), "Brian needs pricing today.")

    def test_real_tickers_are_still_peeled(self):
        self.assertEqual(
            reasoner.scrub_noise("processing... Brian needs pricing.").strip(),
            "Brian needs pricing.")


class FollowupCollectionIsBestEffort(unittest.TestCase):
    """B-18: the newest, slowest source must not take down the whole sweep."""

    def test_a_failed_followup_collection_leaves_the_sweep_running(self):
        written = {}

        def fake_collect(name, args, timeout=300):
            if name == "waiting_on_them":
                raise RuntimeError("collection failed rc=1: Graph timed out")
            return f"{name}-data"

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(reasoner, "collect", side_effect=fake_collect),
                patch.object(reasoner, "SWEEP_DIR", Path(folder)),
                patch.object(reasoner, "log", side_effect=lambda m: written.setdefault("log", m)),
            ):
                reasoner.prefetch_triage()  # must NOT raise
            payload = json.loads((Path(folder) / "waiting_on_them.json").read_text(encoding="utf-8"))
        self.assertTrue(payload["unavailable"])
        self.assertIn("Do NOT infer", payload["note"])
        # The other three sources were still written.
        self.assertTrue(written)

    def test_a_failure_in_a_core_source_still_raises(self):
        def fake_collect(name, args, timeout=300):
            if name == "inbox":
                raise RuntimeError("inbox collection failed")
            return f"{name}-data"

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(reasoner, "collect", side_effect=fake_collect),
                patch.object(reasoner, "SWEEP_DIR", Path(folder)),
                patch.object(reasoner, "log"),
            ):
                with self.assertRaises(RuntimeError):
                    reasoner.prefetch_triage()


class DigestFailureNamesTheBacklog(unittest.TestCase):
    """B-02 in dado_followup_digest, where prefetch has PROVEN there is work."""

    def _run_with(self, model_result):
        sent = []
        with (
            patch.object(digest, "flush_undelivered"),
            patch.object(digest, "prefetch", return_value=15),
            patch.object(digest, "run_dado", return_value=model_result),
            patch.object(digest, "send_clean", side_effect=lambda m: sent.append(m)),
            patch.object(digest, "log"),
        ):
            digest.run_once()
        return sent

    def test_failed_run_alerts_and_states_the_overdue_count(self):
        sent = self._run_with(None)
        self.assertEqual(len(sent), 1)
        self.assertIn("15", sent[0])
        self.assertIn("NOT an all-clear", sent[0])

    def test_empty_output_is_not_an_all_clear_when_work_exists(self):
        sent = self._run_with("")
        self.assertEqual(len(sent), 1, "prefetch proved there are 15 overdue threads")
        self.assertIn("15", sent[0])

    def test_output_that_scrubs_to_nothing_alerts_rather_than_going_dark(self):
        sent = self._run_with("thinking...\nworking...")
        self.assertEqual(len(sent), 1)
        self.assertIn("15", sent[0])

    def test_an_explicit_silent_reply_is_still_honoured(self):
        self.assertEqual(self._run_with("[SILENT]"), [])

    def test_a_real_digest_is_delivered_unchanged(self):
        sent = self._run_with("3 threads have gone quiet on you. Drafts are ready.")
        self.assertEqual(len(sent), 1)
        self.assertIn("3 threads have gone quiet", sent[0])


if __name__ == "__main__":
    unittest.main()
