"""Guards on cmd_watch's announcements (backlog B-04, B-05, B-06).

All three are the same shape: Rachad is told the wrong thing, or nothing, about
a background job, and the job file on disk says it was announced.

B-04 The stall warning and the completion notice shared one `reported` flag, so
     a long OCR job that went quiet, got flagged "may be stuck", then finished
     cleanly with exit 0 was NEVER reported as finished.
B-05 cmd_watch mutated a job dict loaded up to 20s earlier (pid_alive shells out
     to tasklist). If the supervisor wrote status=done in that window, watch
     overwrote it with status=died and announced a success as a death.
B-06 Every job with something to say was flagged reported, but only the first 5
     messages printed -- jobs 6+ lost their announcement permanently. Broken-file
     notices were appended FIRST, so five unreadable files starved all real
     results on every tick.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_runner  # noqa: E402


class WatchAnnouncementTests(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.jobs_dir = Path(self._folder.name)
        patcher = patch.object(job_runner, "JOBS_DIR", self.jobs_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_job(self, job_id, **fields):
        job = {"id": job_id, "name": fields.pop("name", job_id), "status": "running",
               "pid": 4242, "exit_code": None, "reported": False}
        job.update(fields)
        (self.jobs_dir / f"{job_id}.json").write_text(json.dumps(job), encoding="utf-8")
        return job

    def watch(self):
        out = io.StringIO()
        with redirect_stdout(out):
            job_runner.cmd_watch(argparse.Namespace())
        return out.getvalue()

    def read_job(self, job_id):
        return json.loads((self.jobs_dir / f"{job_id}.json").read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- B-04
    def test_a_stall_warning_does_not_swallow_the_later_completion(self):
        self.write_job("j1", name="drive-ocr-backfill")
        with (patch.object(job_runner, "pid_alive", return_value=True),
              patch.object(job_runner, "heartbeat_age_minutes", return_value=31)):
            first = self.watch()
        self.assertIn("no output for 31 min", first)
        self.assertTrue(self.read_job("j1")["stall_reported"])
        self.assertFalse(self.read_job("j1")["reported"],
                         "the completion notice must still be owed")

        # The job then finishes cleanly.
        job = self.read_job("j1")
        job.update(status="done", exit_code=0)
        (self.jobs_dir / "j1.json").write_text(json.dumps(job), encoding="utf-8")
        with patch.object(job_runner, "summarize_output", return_value=""):
            second = self.watch()
        self.assertIn("finished", second, "a stalled-then-finished job must still report")

    def test_the_stall_warning_is_said_only_once(self):
        self.write_job("j1", name="slow")
        with (patch.object(job_runner, "pid_alive", return_value=True),
              patch.object(job_runner, "heartbeat_age_minutes", return_value=45)):
            self.assertIn("no output", self.watch())
            self.assertEqual(self.watch().strip(), "", "must not repeat every tick")

    # ---------------------------------------------------------------- B-05
    def test_a_job_that_finished_during_the_pid_check_is_not_called_dead(self):
        self.write_job("j1", name="drive-ocr-backfill")

        def finish_then_report_gone(pid):
            # Exactly the race: the supervisor completes while tasklist runs.
            job = self.read_job("j1")
            job.update(status="done", exit_code=0, finished="2026-07-25T01:00:00")
            (self.jobs_dir / "j1.json").write_text(json.dumps(job), encoding="utf-8")
            return False

        with (patch.object(job_runner, "pid_alive", side_effect=finish_then_report_gone),
              patch.object(job_runner, "summarize_output", return_value="")):
            output = self.watch()
        self.assertIn("finished", output)
        self.assertNotIn("died", output)
        saved = self.read_job("j1")
        self.assertEqual(saved["status"], "done", "watch must not clobber the outcome")
        self.assertEqual(saved["exit_code"], 0)

    def test_a_genuinely_dead_job_is_still_announced(self):
        self.write_job("j1", name="ghost")
        with patch.object(job_runner, "pid_alive", return_value=False):
            output = self.watch()
        self.assertIn("died without finishing", output)
        self.assertEqual(self.read_job("j1")["status"], "died")

    # ---------------------------------------------------------------- B-06
    def test_jobs_beyond_the_print_cap_are_held_not_lost(self):
        for i in range(8):
            self.write_job(f"j{i}", name=f"job{i}", status="done", exit_code=0)
        with patch.object(job_runner, "summarize_output", return_value=""):
            first = self.watch()
        self.assertIn("held for the next tick", first)
        reported = sum(1 for i in range(8) if self.read_job(f"j{i}")["reported"])
        self.assertEqual(reported, job_runner.MAX_WATCH_MESSAGES,
                         "only the jobs actually printed may be marked reported")

        with patch.object(job_runner, "summarize_output", return_value=""):
            second = self.watch()
        for i in range(8):
            self.assertTrue(self.read_job(f"j{i}")["reported"],
                            "the held jobs must be announced on the next tick")
        self.assertTrue(second.strip())

    def test_a_service_job_is_never_reported_as_stalled(self):
        """zoho-ui-live holds an authenticated Edge/CDP session that eight
        scripts attach to. It produces no log output for days BY DESIGN. On
        2026-08-07 it was tracked as an ordinary job, so it looked stuck after
        30 minutes and sent Rachad a false stall alert."""
        self.write_job("svc", name="zoho-ui-live", kind=job_runner.SERVICE_JOB)
        with (patch.object(job_runner, "pid_alive", return_value=True),
              patch.object(job_runner, "heartbeat_age_minutes", return_value=1200)):
            output = self.watch()
        self.assertNotIn("zoho-ui-live", output,
                         "a service job that is quiet is healthy, not stuck")
        self.assertFalse(self.read_job("svc").get("stall_reported"))

    def test_a_service_job_that_DIES_is_still_reported(self):
        """The exemption is from the stall alert, not from supervision. A
        session other tools depend on going away is exactly what must be said."""
        self.write_job("svc", name="zoho-ui-live", kind=job_runner.SERVICE_JOB)
        with patch.object(job_runner, "pid_alive", return_value=False):
            output = self.watch()
        self.assertIn("zoho-ui-live", output)
        self.assertIn("died", output)

    def test_a_timed_out_job_is_announced_as_killed_not_as_failed(self):
        """`timeout` and `failed` need different responses: one ran and
        returned non-zero, the other never finished at all."""
        self.write_job("t1", name="stuck-scrape", status="timeout", exit_code=1)
        with patch.object(job_runner, "summarize_output", return_value=""):
            output = self.watch()
        self.assertIn("stuck-scrape", output)
        self.assertIn("KILLED", output)
        self.assertTrue(self.read_job("t1")["reported"])

    def test_unreadable_files_do_not_starve_real_job_results(self):
        for i in range(5):
            (self.jobs_dir / f"broken{i}.json").write_text("{not json", encoding="utf-8")
        self.write_job("real", name="drive-ocr-backfill", status="done", exit_code=0)
        with patch.object(job_runner, "summarize_output", return_value=""):
            output = self.watch()
        self.assertIn("drive-ocr-backfill", output,
                      "a real completion must outrank five unreadable files")
        self.assertTrue(self.read_job("real")["reported"])


if __name__ == "__main__":
    unittest.main()
