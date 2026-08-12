"""Tests for dado_heartbeat_check -- the reciprocal cross-scheduler check.

Mirror of C:\\AgentTeam\\Sync\\test_aze_heartbeat_check.py.

Run:
  "%LOCALAPPDATA%\\hermes\\hermes-agent\\venv\\Scripts\\python.exe" -m unittest \\
      discover -s C:\\FRPDepot\\Dado\\Tools\\watch -p "test_dado_heartbeat_check.py" -v
"""

from __future__ import annotations

import json

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dado_heartbeat_check as hb  # noqa: E402


class Assess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        hb.HEARTBEAT = root / "heartbeat.txt"
        hb.WATCHDOG_SCRIPT = root / "dado_gateway_watchdog.ps1"
        hb.DISABLE_FLAG = root / "gateway_disabled.flag"
        hb.WATCHDOG_SCRIPT.write_text("# watchdog", encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def _stamp(self, minutes_ago):
        hb.HEARTBEAT.write_text("ran at ...", encoding="utf-8")
        when = time.time() - minutes_ago * 60
        os.utime(hb.HEARTBEAT, (when, when))

    def test_fresh_heartbeat_is_healthy(self):
        self._stamp(2)
        self.assertEqual(hb.assess()["verdict"], "healthy")

    def test_just_inside_the_threshold_is_still_healthy(self):
        self._stamp(hb.STALE_MINUTES - 1)
        self.assertEqual(hb.assess()["verdict"], "healthy")

    def test_past_the_threshold_is_a_stopped_watchdog(self):
        self._stamp(hb.STALE_MINUTES + 5)
        verdict = hb.assess()
        self.assertEqual(verdict["verdict"], "watchdog_not_running")
        self.assertGreater(verdict["age_minutes"], hb.STALE_MINUTES)

    def test_missing_heartbeat_is_a_stopped_watchdog(self):
        self.assertEqual(hb.assess()["verdict"], "watchdog_not_running")

    def test_silent_when_frp_is_not_installed_on_this_pc(self):
        """Otherwise a TDI-only machine pages Rachad every 10 minutes forever."""
        hb.WATCHDOG_SCRIPT.unlink()
        self.assertEqual(hb.assess()["verdict"], "not_installed")

    def test_not_installed_wins_over_missing_heartbeat(self):
        hb.WATCHDOG_SCRIPT.unlink()
        self.assertFalse(hb.HEARTBEAT.exists())
        self.assertEqual(hb.assess()["verdict"], "not_installed")


class PagingDiscipline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        hb.STATE_FILE = root / "state.json"
        hb.DISABLE_FLAG = root / "gateway_disabled.flag"
        self.calls: list[list[str]] = []
        hb.run_alerter = lambda args: (self.calls.append(args), "{}")[1]
        self.addCleanup(self.tmp.cleanup)

    BAD = {"verdict": "watchdog_not_running", "age_minutes": 45, "detail": "stale"}
    OK = {"verdict": "healthy", "age_minutes": 2}

    def _run(self, verdict):
        hb.assess = lambda: verdict
        return hb.main([])

    def test_first_stale_sample_is_silent(self):
        self.assertEqual(self._run(self.BAD), 0)
        self.assertEqual(self.calls, [])

    def test_second_consecutive_stale_sample_pages(self):
        self._run(self.BAD)
        self.assertEqual(self._run(self.BAD), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("watchdog_not_running", self.calls[0])

    def test_recovery_clears_the_alert(self):
        self._run(self.BAD)
        self._run(self.BAD)
        self.calls.clear()
        self._run(self.OK)
        self.assertEqual(self.calls, [["--clear", "--reason", "watchdog_not_running"]])

    def test_healthy_without_a_prior_alert_clears_nothing(self):
        """--clear deletes the SHARED DADO_NEEDS_ATTENTION marker, which is
        written for ANY reason. Clearing on every healthy 5-minute run would
        erase the marker left by a real gateway-down alert."""
        self._run(self.OK)
        self.assertEqual(self.calls, [])

    def test_not_installed_never_pages_and_never_clears(self):
        self._run({"verdict": "not_installed", "detail": "d"})
        self._run({"verdict": "not_installed", "detail": "d"})
        self.assertEqual(self.calls, [])


class AlertMessage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        hb.DISABLE_FLAG = Path(self.tmp.name) / "gateway_disabled.flag"
        self.addCleanup(self.tmp.cleanup)

    def test_message_separates_lost_recovery_from_being_down(self):
        msg = hb.alert_message(dict(verdict="watchdog_not_running",
                                    age_minutes=45, detail="stale"))
        self.assertIn("STOPPED RUNNING", msg)
        self.assertIn("probably still up", msg)

    def test_message_says_aze_is_now_unwatched_too(self):
        """The blast radius is the point: Dado's keep-alive is what watches Aze."""
        msg = hb.alert_message(dict(verdict="watchdog_not_running",
                                    age_minutes=45, detail="stale"))
        self.assertIn("AZE IS NO LONGER BEING WATCHED", msg)

    def test_deliberate_stop_is_explicitly_not_an_explanation(self):
        """STOP_DADO stops the GATEWAY, not this task -- it still heartbeats.

        Without this note the obvious guess ('oh, I stopped her on purpose')
        would wave away a genuinely dead watchdog.
        """
        hb.DISABLE_FLAG.write_text("stopped", encoding="utf-8")
        msg = hb.alert_message(dict(verdict="watchdog_not_running",
                                    age_minutes=45, detail="stale"))
        self.assertIn("does NOT explain this", msg)


class MarkerIsolation(unittest.TestCase):
    def test_dado_alerter_marker_is_not_azes(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import dado_urgent_alert as ua
        self.assertIn("DADO_NEEDS_ATTENTION", str(ua.DESKTOP_MARKER))
        self.assertNotIn("AZE_NEEDS", str(ua.DESKTOP_MARKER).upper())



class CronStalledRuns(unittest.TestCase):
    """The transport half of stale-running-rows.

    The runtime writes stalled_runs.json ONLY while a run is wedged, so
    file-present is the whole signal. These pin the three behaviours that
    matter: silence when clean, exactly one alert per episode, and a second
    look at the SAME episode staying quiet.

    run_alerter is stubbed throughout. Nothing here may put a real message on
    Rachad's phone - a test run in this tree has done that twice.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = (hb.STALLED_RUNS_FILE, hb.STALLED_STATE_FILE,
                      hb.run_alerter)
        hb.STALLED_RUNS_FILE = root / "stalled_runs.json"
        hb.STALLED_STATE_FILE = root / "stalled_state.json"
        self.sent = []

        def fake_alerter(args):
            self.sent.append(args)
            return '{"status": "SENT"}'

        hb.run_alerter = fake_alerter

    def tearDown(self):
        (hb.STALLED_RUNS_FILE, hb.STALLED_STATE_FILE,
         hb.run_alerter) = self._orig
        self.tmp.cleanup()

    def _write_marker(self, execution_id="exec-1", job_name="wedged-job"):
        hb.STALLED_RUNS_FILE.write_text(json.dumps({
            "recorded_at": 1.0,
            "stalled": [{
                "job_id": "j1", "job_name": job_name,
                "execution_id": execution_id, "status": "running",
                "age_seconds": 1800, "skipped_occurrences": 3,
                "still_in_flight": True,
            }],
        }), encoding="utf-8")

    def test_absent_marker_sends_nothing(self):
        result = hb.check_cron_stalled_runs()
        self.assertEqual(result["status"], "clean")
        self.assertEqual(self.sent, [])

    def test_a_wedged_run_alerts_once(self):
        self._write_marker()
        result = hb.check_cron_stalled_runs()
        self.assertEqual(result["alerted"], 1)
        self.assertEqual(len(self.sent), 1)
        body = " ".join(self.sent[0])
        self.assertIn("wedged-job", body)
        self.assertIn("30 min", body)
        self.assertIn("3 scheduled run(s) skipped", body)

    def test_the_same_episode_is_not_re_alerted(self):
        self._write_marker()
        hb.check_cron_stalled_runs()
        hb.check_cron_stalled_runs()
        self.assertEqual(len(self.sent), 1, "a wedge lasting hours must alert once")

    def test_a_new_episode_alerts_again(self):
        self._write_marker(execution_id="exec-1")
        hb.check_cron_stalled_runs()
        self._write_marker(execution_id="exec-2", job_name="other-job")
        hb.check_cron_stalled_runs()
        self.assertEqual(len(self.sent), 2)

    def test_an_unconfirmed_send_is_retried_not_marked_reported(self):
        """B-08: a dropped alert must not consume the budget that would retry it."""
        self._write_marker()
        hb.run_alerter = lambda args: '{"status": "SEND_FAILED"}'
        first = hb.check_cron_stalled_runs()
        self.assertEqual(first["alerted"], 0)

        sent = []
        hb.run_alerter = lambda args: sent.append(args) or '{"status": "SENT"}'
        second = hb.check_cron_stalled_runs()
        self.assertEqual(second["alerted"], 1, "the retry must still happen")
        self.assertEqual(len(sent), 1)

    def test_the_alerter_is_never_asked_to_clear(self):
        """--clear also deletes the SHARED desktop marker for other reasons."""
        self._write_marker()
        hb.check_cron_stalled_runs()
        for args in self.sent:
            self.assertNotIn("--clear", args)

    def test_an_unreadable_marker_is_reported_not_alerted(self):
        hb.STALLED_RUNS_FILE.write_text("{ not json", encoding="utf-8")
        result = hb.check_cron_stalled_runs()
        self.assertEqual(result["status"], "unreadable")
        self.assertEqual(self.sent, [])

    def test_dry_run_sends_nothing(self):
        self._write_marker()
        result = hb.check_cron_stalled_runs(dry_run=True)
        self.assertEqual(result["status"], "wedged")
        self.assertEqual(self.sent, [])

if __name__ == "__main__":
    unittest.main(verbosity=2)
