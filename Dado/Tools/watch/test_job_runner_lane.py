"""The watch tick reports a job on the lane the request came from (spec B1, 2026-08-21).

Before this, every announcement went to Telegram: a job Rachad asked for on
Discord answered on his other phone app, and the dashboard got nothing. Now
`start` records the originating lane (argument, else the session platform
Hermes bridges into the child environment, else Telegram) and `watch` sends
one message per lane, persisting each lane's flags on its own confirmation.
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


class OriginatingLaneTests(unittest.TestCase):
    def test_default_is_telegram_when_nothing_is_known(self) -> None:
        self.assertEqual(job_runner.originating_lane(None, {}),
                         {"lane": "telegram", "lane_target": "", "lane_source": "default"})

    def test_the_session_platform_is_honoured(self) -> None:
        env = {"HERMES_SESSION_PLATFORM": "telegram", "HERMES_SESSION_CHAT_ID": "123"}
        self.assertEqual(job_runner.originating_lane(None, env)["lane"], "telegram")
        self.assertEqual(job_runner.originating_lane(None, env)["lane_target"], "")
        env = {"HERMES_SESSION_PLATFORM": "discord", "HERMES_SESSION_CHAT_ID": "987"}
        lane = job_runner.originating_lane(None, env)
        self.assertEqual(lane["lane"], "discord")
        self.assertEqual(lane["lane_target"], "discord:987")
        self.assertEqual(lane["lane_source"], "session")

    def test_the_dashboard_is_recognised_under_its_hermes_name(self) -> None:
        lane = job_runner.originating_lane(None, {"HERMES_SESSION_PLATFORM": "api_server"})
        self.assertEqual(lane["lane"], "dashboard")
        self.assertEqual(lane["lane_target"], "")

    def test_an_explicit_argument_wins_over_the_session(self) -> None:
        env = {"HERMES_SESSION_PLATFORM": "telegram", "HERMES_SESSION_CHAT_ID": "123"}
        lane = job_runner.originating_lane("discord", env)
        self.assertEqual(lane["lane"], "discord")
        # The Telegram chat id is NOT a Discord target: fall back to the home channel.
        self.assertEqual(lane["lane_target"], "discord")
        self.assertEqual(lane["lane_source"], "argument")

    def test_an_unknown_name_falls_back_to_telegram_rather_than_refusing(self) -> None:
        self.assertEqual(job_runner.originating_lane("pigeon", {})["lane"], "telegram")
        self.assertEqual(job_runner.originating_lane(None, {"HERMES_SESSION_PLATFORM": "cron"})["lane"],
                         "telegram")


class StartRecordsTheLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.jobs_dir = Path(self._folder.name)
        patcher = patch.object(job_runner, "JOBS_DIR", self.jobs_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _start(self, lane=None, env=None):
        args = argparse.Namespace(name="x", note="", timeout_hours=1.0, service=False,
                                  command=["python", "-c", "pass"], lane=lane)
        out = io.StringIO()
        with (patch.object(job_runner.subprocess, "Popen") as popen,
              patch.object(job_runner, "receipt"),
              patch.dict(job_runner.os.environ, env or {}, clear=False),
              redirect_stdout(out)):
            for key in ("HERMES_SESSION_PLATFORM", "HERMES_SESSION_CHAT_ID"):
                if not (env or {}).get(key):
                    job_runner.os.environ.pop(key, None)
            code = job_runner.cmd_start(args)
        self.assertEqual(code, 0)
        popen.assert_called_once()
        printed = json.loads(out.getvalue())
        record = json.loads(next(self.jobs_dir.glob("*.json")).read_text(encoding="utf-8"))
        return printed, record

    def test_start_records_the_lane_and_says_where_the_result_goes(self) -> None:
        printed, record = self._start(env={"HERMES_SESSION_PLATFORM": "discord",
                                           "HERMES_SESSION_CHAT_ID": "555"})
        self.assertEqual(record["lane"], "discord")
        self.assertEqual(record["lane_target"], "discord:555")
        self.assertEqual(printed["lane"], "discord")
        self.assertIn("(discord)", printed["next"])
        self.assertNotIn("555", json.dumps(printed), "the chat id never reaches the chat")

    def test_start_defaults_to_telegram(self) -> None:
        printed, record = self._start()
        self.assertEqual(record["lane"], "telegram")
        self.assertEqual(printed["lane"], "telegram")
        self.assertNotIn("END YOUR TURN", printed["next"])


class WatchDeliversPerLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.jobs_dir = Path(self._folder.name)
        patcher = patch.object(job_runner, "JOBS_DIR", self.jobs_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_job(self, job_id, **fields):
        job = {"id": job_id, "name": fields.pop("name", job_id), "status": "done",
               "pid": 1, "exit_code": 0, "reported": False}
        job.update(fields)
        (self.jobs_dir / f"{job_id}.json").write_text(json.dumps(job), encoding="utf-8")

    def read_job(self, job_id):
        return json.loads((self.jobs_dir / f"{job_id}.json").read_text(encoding="utf-8"))

    def watch(self, deliver):
        out = io.StringIO()
        with (patch.object(job_runner, "_deliver", side_effect=deliver) as send,
              patch.object(job_runner, "summarize_output", return_value=""),
              redirect_stdout(out)):
            job_runner.cmd_watch(argparse.Namespace())
        return send, out.getvalue()

    def test_each_lane_gets_its_own_message(self) -> None:
        self.write_job("t", name="tele-job")
        self.write_job("d", name="disc-job", lane="discord", lane_target="discord:42")
        self.write_job("w", name="dash-job", lane="dashboard")
        send, _ = self.watch(lambda message, target="": True)
        calls = {call.args[1] if len(call.args) > 1 else call.kwargs.get("target", ""): call.args[0]
                 for call in send.call_args_list}
        self.assertEqual(set(calls), {"", "discord:42"})
        self.assertIn("tele-job", calls[""])
        self.assertIn("dash-job", calls[""])
        self.assertIn("dashboard has no push lane", calls[""])
        self.assertIn("disc-job", calls["discord:42"])
        self.assertNotIn("tele-job", calls["discord:42"])
        for job_id in ("t", "d", "w"):
            self.assertTrue(self.read_job(job_id)["reported"])

    def test_a_failed_lane_leaves_only_its_own_jobs_owed(self) -> None:
        self.write_job("t", name="tele-job")
        self.write_job("d", name="disc-job", lane="discord", lane_target="discord:42")
        send, output = self.watch(lambda message, target="": target == "")
        self.assertTrue(self.read_job("t")["reported"])
        self.assertFalse(self.read_job("d")["reported"], "the Discord job stays owed")
        self.assertIn("not confirmed on discord:42", output)

    def test_a_job_file_without_a_lane_still_goes_to_telegram(self) -> None:
        self.write_job("old", name="legacy")
        send, _ = self.watch(lambda message, target="": True)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[1], "")
        self.assertTrue(self.read_job("old")["reported"])

    def test_the_held_note_reaches_every_lane(self) -> None:
        for i in range(4):
            self.write_job(f"t{i}", name=f"tele{i}")
        for i in range(3):
            self.write_job(f"d{i}", name=f"disc{i}", lane="discord", lane_target="discord")
        send, _ = self.watch(lambda message, target="": True)
        for call in send.call_args_list:
            self.assertIn("held for the next tick", call.args[0])


class SenderTargetTests(unittest.TestCase):
    def test_send_clean_passes_the_target_to_the_wire(self) -> None:
        import dado_inbox_reasoner as reasoner
        seen = []

        def fake_try_send(message, target=None):
            seen.append(target)
            return 0, ""

        with (patch.object(reasoner, "_try_send", side_effect=fake_try_send),
              patch.object(reasoner, "log")):
            self.assertTrue(reasoner.send_clean("m", queue_on_failure=False, target="discord:1"))
            self.assertTrue(reasoner.send_clean("m", queue_on_failure=False))
        self.assertEqual(seen, ["discord:1", None])

    def test_deliver_omits_the_target_kwarg_on_the_default_lane(self) -> None:
        """The profile's scripts copy of dado_inbox_reasoner.py may predate the
        `target` parameter: a Telegram announcement must reach a legacy
        send_clean(message, queue_on_failure) unchanged, and only a real
        target is passed as a kwarg."""
        import dado_inbox_reasoner as reasoner
        calls = []

        def legacy_send_clean(message, queue_on_failure=True):  # no `target` at all
            calls.append((message, queue_on_failure))
            return True

        with (patch.object(job_runner, "_test_run_suppressed", return_value=False),
              patch.object(reasoner, "send_clean", legacy_send_clean)):
            self.assertTrue(job_runner._deliver("hello", ""))
            self.assertTrue(job_runner._deliver("hello"))
            # A legacy sender cannot route a Discord target: owed, never crashed.
            with redirect_stdout(io.StringIO()) as out:
                self.assertFalse(job_runner._deliver("hello", "discord:7"))
            self.assertIn("send raised", out.getvalue())
        self.assertEqual(calls, [("hello", False), ("hello", False)])

        seen = []

        def modern_send_clean(message, queue_on_failure=True, target=None):
            seen.append(target)
            return True

        with (patch.object(job_runner, "_test_run_suppressed", return_value=False),
              patch.object(reasoner, "send_clean", modern_send_clean)):
            self.assertTrue(job_runner._deliver("hello", "discord:7"))
            self.assertTrue(job_runner._deliver("hello", ""))
        self.assertEqual(seen, ["discord:7", None])


if __name__ == "__main__":
    unittest.main()
