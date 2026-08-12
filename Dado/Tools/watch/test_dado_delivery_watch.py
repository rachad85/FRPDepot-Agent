"""Tests for the cron delivery watch.

The defect it exists for: cron computes a delivery outcome separately from the
run outcome, so a dropped message leaves last_status "ok" and nothing speaks.
Measured on the other profile of this same shared hermes install 2026-08-10:
a job whose message reached nobody, found only by an audit weeks later.

The rule these tests protect hardest is the one dado_inbox_reasoner.send_clean
already applies to its own sends: a finding is marked reported ONLY after a
CONFIRMED send. The alerter suppresses on a 60-minute per-reason cooldown and
still exits 0, so persisting on anything other than {"status": "SENT"} would
silently destroy a second job's failure inside that hour - which is exactly backlog
item B-08, filed 2026-07-25 against job_runner and stall_tripwire.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dado_delivery_watch as watch  # noqa: E402


def _make_ledger(path: Path, rows, with_columns=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    extra = ", delivery_outcome TEXT, delivery_error TEXT" if with_columns else ""
    conn.execute(
        f"""CREATE TABLE executions (
              id TEXT PRIMARY KEY, job_id TEXT, claimed_at TEXT,
              finished_at TEXT{extra})"""
    )
    for row in rows:
        if with_columns:
            conn.execute("INSERT INTO executions VALUES (?,?,?,?,?,?)", row)
        else:
            conn.execute("INSERT INTO executions VALUES (?,?,?,?)", row[:4])
    conn.commit()
    conn.close()


class DeliveryWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / ".test_tmp_delivery"
        if self.tmp.exists():
            import shutil
            shutil.rmtree(self.tmp, ignore_errors=True)
        self.cron = self.tmp / "cron"
        self.cron.mkdir(parents=True, exist_ok=True)
        self.state = self.tmp / "state.json"
        self.flag = self.tmp / "stopped.flag"
        self._patches = [
            mock.patch.object(watch, "cron_dir", lambda: self.cron),
            mock.patch.object(watch, "STATE_FILE", self.state),
            mock.patch.object(watch, "STATE_DIR", self.tmp),
            mock.patch.object(watch, "DISABLE_FLAG", self.flag),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _jobs(self, jobs):
        (self.cron / "jobs.json").write_text(json.dumps({"jobs": jobs}), encoding="utf-8")

    def _run(self, status="SENT", argv=None):
        payload = json.dumps({"status": status, "reason": "cron_delivery"})
        with mock.patch.object(watch.subprocess, "run") as runner:
            runner.return_value = mock.Mock(stdout=payload, returncode=0)
            code = watch.main(argv or [])
            return code, runner

    # ---- silence ----------------------------------------------------------
    def test_silent_when_every_delivery_succeeded(self):
        self._jobs([{"id": "a", "name": "ok job", "last_delivery_error": None}])
        _make_ledger(self.cron / "executions.db",
                     [("e1", "a", "t0", "t1", "delivered", None)])
        code, runner = self._run()
        self.assertEqual(code, 0)
        runner.assert_not_called()

    def test_deliver_local_is_not_a_finding(self):
        """'suppressed' is deliver=local working as designed."""
        self._jobs([{"id": "a", "name": "local job"}])
        _make_ledger(self.cron / "executions.db",
                     [("e1", "a", "t0", "t1", "suppressed", None)])
        code, runner = self._run()
        self.assertEqual(code, 0)
        runner.assert_not_called()

    def test_a_deliberate_stop_never_pages(self):
        self.flag.write_text("stopped", encoding="utf-8")
        self._jobs([{"id": "a", "name": "j", "last_delivery_error": "boom",
                     "last_run_at": "t"}])
        code, runner = self._run()
        self.assertEqual(code, 0)
        runner.assert_not_called()

    # ---- detection --------------------------------------------------------
    def test_a_failed_delivery_in_the_ledger_is_reported(self):
        self._jobs([{"id": "a", "name": "job watch"}])
        _make_ledger(self.cron / "executions.db",
                     [("e1", "a", "t0", "t1", "failed", "adapter refused")])
        code, runner = self._run()
        self.assertEqual(code, 1)
        message = runner.call_args[0][0][-1]
        self.assertIn("job watch", message)
        self.assertIn("adapter refused", message)

    def test_not_configured_counts_as_a_drop(self):
        """The job had something to say and nowhere to send it."""
        self._jobs([{"id": "a", "name": "orphan"}])
        _make_ledger(self.cron / "executions.db",
                     [("e1", "a", "t0", "t1", "not_configured", None)])
        code, _ = self._run()
        self.assertEqual(code, 1)

    def test_jobs_json_is_used_when_the_ledger_has_no_column(self):
        """Pre-patch runtime, or the gateway has not restarted yet."""
        self._jobs([{"id": "a", "name": "legacy", "last_delivery_error": "old drop",
                     "last_run_at": "t9"}])
        _make_ledger(self.cron / "executions.db",
                     [("e1", "a", "t0", "t1")], with_columns=False)
        code, runner = self._run()
        self.assertEqual(code, 1)
        self.assertIn("legacy", runner.call_args[0][0][-1])

    def test_a_missing_ledger_still_reports_from_jobs_json(self):
        self._jobs([{"id": "a", "name": "n", "last_delivery_error": "d",
                     "last_run_at": "t"}])
        code, _ = self._run()
        self.assertEqual(code, 1)

    # ---- report-once, only after a confirmed send -------------------------
    def test_the_same_failure_is_not_reported_twice(self):
        self._jobs([{"id": "a", "name": "n", "last_delivery_error": "d",
                     "last_run_at": "t"}])
        self.assertEqual(self._run()[0], 1)
        code, runner = self._run()
        self.assertEqual(code, 0)
        runner.assert_not_called()

    def test_a_suppressed_alert_does_NOT_mark_it_reported(self):
        """The B-08 defect, guarded. He was not told; keep it pending."""
        self._jobs([{"id": "a", "name": "n", "last_delivery_error": "d",
                     "last_run_at": "t"}])
        code, _ = self._run(status="SUPPRESSED_COOLDOWN")
        self.assertEqual(code, 0)
        self.assertFalse(self.state.exists(), "nothing may be persisted on a suppressed alert")
        # the next tick must try again
        code, runner = self._run(status="SENT")
        self.assertEqual(code, 1)
        runner.assert_called_once()

    def test_a_failed_send_does_NOT_mark_it_reported(self):
        self._jobs([{"id": "a", "name": "n", "last_delivery_error": "d",
                     "last_run_at": "t"}])
        self.assertEqual(self._run(status="SEND_FAILED")[0], 0)
        self.assertEqual(self._run(status="SENT")[0], 1)

    def test_a_new_failure_on_the_same_job_is_a_new_finding(self):
        self._jobs([{"id": "a", "name": "n", "last_delivery_error": "d",
                     "last_run_at": "t1"}])
        self.assertEqual(self._run()[0], 1)
        self._jobs([{"id": "a", "name": "n", "last_delivery_error": "d2",
                     "last_run_at": "t2"}])
        code, runner = self._run()
        self.assertEqual(code, 1)
        self.assertIn("d2", runner.call_args[0][0][-1])

    # ---- robustness -------------------------------------------------------
    def test_dry_run_sends_nothing(self):
        self._jobs([{"id": "a", "name": "n", "last_delivery_error": "d",
                     "last_run_at": "t"}])
        code, runner = self._run(argv=["--dry-run"])
        self.assertEqual(code, 0)
        runner.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_a_broken_alerter_does_not_wedge_the_watchdog(self):
        self._jobs([{"id": "a", "name": "n", "last_delivery_error": "d",
                     "last_run_at": "t"}])
        with mock.patch.object(watch.subprocess, "run", side_effect=OSError("gone")):
            self.assertEqual(watch.main([]), 0)

    def test_a_long_blind_spell_reports_as_a_summary(self):
        self._jobs([{"id": f"j{i}", "name": f"job{i}", "last_delivery_error": "d",
                     "last_run_at": "t"} for i in range(25)])
        code, runner = self._run()
        self.assertEqual(code, 1)
        message = runner.call_args[0][0][-1]
        self.assertIn("and 15 more", message)
        # MAX_LINES findings plus the one "...and N more" summary line, which
        # is also bulleted.
        self.assertEqual(message.count("\n- "), watch.MAX_LINES + 1)
        self.assertIn("job0", message)
        self.assertNotIn("job24", message)


class CatchUpReadingTests(DeliveryWatchTests):
    """The other half of "the counter is write-only".

    catch_up_occurrences was a bare tally: it wrote "1" for a job 333s late on a
    600s period (which loses nothing, being inside the next slot) and "1" for a
    60s job 103 minutes late (which loses about a hundred runs). Nothing could
    act on that, which is why nothing read it. The runtime now records the
    magnitude per event; this reads it.
    """

    def _events(self, *events):
        (self.cron / "catch_up_events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    def test_a_skip_that_lost_nothing_is_not_reported(self):
        """333s late on a 600s period. Alerting on this is how a watch gets muted."""
        self._jobs([{"id": "a", "name": "tick"}])
        self._events({"at": "2026-08-12T10:00:00", "job_id": "a", "job": "tick",
                      "skipped_occurrences": 0, "late_seconds": 333})
        code, runner = self._run()
        self.assertEqual(code, 0)
        runner.assert_not_called()

    def test_real_lost_occurrences_are_reported_with_their_magnitude(self):
        self._jobs([{"id": "a", "name": "dado-job-watch"}])
        self._events({"at": "2026-08-12T10:00:00", "job_id": "a",
                      "job": "dado-job-watch", "skipped_occurrences": 103,
                      "late_seconds": 6180})
        code, runner = self._run()
        self.assertEqual(code, 1)
        message = runner.call_args[0][0][-1]
        self.assertIn("103 occurrence(s) skipped", message)
        self.assertIn("does not backfill", message)

    def test_an_uncountable_schedule_is_reported_as_unknown_not_zero(self):
        self._jobs([{"id": "a", "name": "odd"}])
        self._events({"at": "2026-08-12T10:00:00", "job_id": "a", "job": "odd",
                      "skipped_occurrences": None})
        code, runner = self._run()
        self.assertEqual(code, 1)
        self.assertIn("unknown number", runner.call_args[0][0][-1])

    def test_a_capped_count_says_at_least(self):
        self._jobs([{"id": "a", "name": "minutely"}])
        self._events({"at": "2026-08-12T10:00:00", "job_id": "a", "job": "minutely",
                      "skipped_occurrences": 5000, "capped": True})
        self._run()
        # re-run to inspect the composed text via a fresh state
        (self.state).unlink(missing_ok=True)
        _, runner = self._run()
        self.assertIn("at least 5000", runner.call_args[0][0][-1])

    def test_the_same_skip_is_reported_once(self):
        self._jobs([{"id": "a", "name": "tick"}])
        self._events({"at": "2026-08-12T10:00:00", "job_id": "a", "job": "tick",
                      "skipped_occurrences": 7})
        self.assertEqual(self._run()[0], 1)
        code, runner = self._run()
        self.assertEqual(code, 0)
        runner.assert_not_called()

    def test_a_malformed_line_does_not_break_the_watch(self):
        self._jobs([{"id": "a", "name": "tick"}])
        (self.cron / "catch_up_events.jsonl").write_text(
            "not json\n" + json.dumps({"at": "t", "job_id": "a", "job": "tick",
                                       "skipped_occurrences": 4}) + "\n",
            encoding="utf-8")
        self.assertEqual(self._run()[0], 1)

    def test_both_kinds_report_together(self):
        self._jobs([{"id": "a", "name": "tick", "last_delivery_error": "dropped",
                     "last_run_at": "t"}])
        self._events({"at": "2026-08-12T10:00:00", "job_id": "a", "job": "tick",
                      "skipped_occurrences": 3})
        code, runner = self._run()
        self.assertEqual(code, 1)
        message = runner.call_args[0][0][-1]
        self.assertIn("never reached you", message)
        self.assertIn("skipped entirely", message)


if __name__ == "__main__":
    unittest.main()
