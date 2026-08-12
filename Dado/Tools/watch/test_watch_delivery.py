"""Backlog B-08: "announced" must mean Telegram took it, not that we printed it.

job_runner's watch tick and stall_tripwire both persisted their "already told
him" state on the strength of a print(). Delivery happened later, out of
process, through cron `deliver: telegram`, which has no retry and no undelivered
queue - and nothing reads last_delivery_error. So a dropped message left state
saying "announced" and it was never re-emitted. Filed 2026-07-25, still open
until now.

Both alerts are RE-DERIVED from durable state on a short cadence (10 and 15
minutes), so the fix is not a queue: it is simply refusing to spend the state.
An unset reported flag and an unspent alert budget ARE the queue.

A note for whoever runs this suite: these tests never touch the real sender.
Wiring delivery into these paths made a plain pytest run put 14 real messages on
Rachad's phone, so both scripts now refuse to send when PYTEST_CURRENT_TEST is
set, and the tests below patch send_clean explicitly on top of that.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_runner  # noqa: E402
import stall_tripwire  # noqa: E402


class JobWatchDeliveryTests(unittest.TestCase):
    """A finished job stays owed until Telegram confirms."""

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / ".test_tmp_b08"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.patch = mock.patch.object(job_runner, "JOBS_DIR", self.tmp)
        self.patch.start()
        self.job = {
            "id": "j1", "name": "drive-backfill", "status": "done",
            "pid": 1, "started": "2026-08-12T10:00:00",
            "finished": "2026-08-12T10:05:00", "exit_code": 0,
            "reported": False, "kind": "job",
        }
        (self.tmp / "j1.json").write_text(json.dumps(self.job), encoding="utf-8")
        (self.tmp / "j1.log").write_text("done\n", encoding="utf-8")

    def tearDown(self):
        self.patch.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _record(self):
        return json.loads((self.tmp / "j1.json").read_text(encoding="utf-8"))

    def test_a_confirmed_send_marks_it_reported(self):
        with mock.patch.object(job_runner, "_deliver", return_value=True) as send:
            job_runner.cmd_watch(mock.Mock())
        send.assert_called_once()
        self.assertTrue(self._record()["reported"], "a delivered update is reported")

    def test_an_unconfirmed_send_leaves_it_OWED(self):
        """The B-08 defect itself."""
        with mock.patch.object(job_runner, "_deliver", return_value=False):
            job_runner.cmd_watch(mock.Mock())
        self.assertFalse(
            self._record()["reported"],
            "a dropped update must stay owed, or it is never re-emitted",
        )

    def test_the_next_tick_re_derives_and_re_announces_it(self):
        with mock.patch.object(job_runner, "_deliver", return_value=False):
            job_runner.cmd_watch(mock.Mock())
        with mock.patch.object(job_runner, "_deliver", return_value=True) as send:
            job_runner.cmd_watch(mock.Mock())
        send.assert_called_once()
        self.assertIn("drive-backfill", send.call_args[0][0])
        self.assertTrue(self._record()["reported"])

    def test_a_broken_sender_does_not_mark_it_reported(self):
        with mock.patch.object(job_runner, "_deliver", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                job_runner.cmd_watch(mock.Mock())
        self.assertFalse(self._record()["reported"])

    def test_nothing_to_say_sends_nothing(self):
        self.job["reported"] = True
        (self.tmp / "j1.json").write_text(json.dumps(self.job), encoding="utf-8")
        with mock.patch.object(job_runner, "_deliver") as send:
            job_runner.cmd_watch(mock.Mock())
        send.assert_not_called()


class TripwireDeliveryTests(unittest.TestCase):
    """The alert budget and the re-alert clock are spent only on a real send."""

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / ".test_tmp_b08_tw"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.state = self.tmp / "state.json"
        self.patch = mock.patch.object(stall_tripwire, "STATE_PATH", self.state)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, delivered):
        """One tick against a synthetic stalled turn."""
        import datetime as dt

        started = dt.datetime.now() - dt.timedelta(minutes=45)
        turn = {"started": started, "platform": "telegram", "long_tools": 4,
                "last_activity": dt.datetime.now() - dt.timedelta(minutes=1),
                "api_calls": 9}
        with mock.patch.object(stall_tripwire, "read_tail", return_value=["x"]), \
             mock.patch.object(stall_tripwire, "open_turns", return_value={"s1": turn}), \
             mock.patch.object(stall_tripwire, "_deliver", return_value=delivered) as send:
            stall_tripwire.main()
        return send

    def _alerts(self):
        if not self.state.exists():
            return 0
        state = json.loads(self.state.read_text(encoding="utf-8"))
        return sum(r.get("alerts", 0) for r in state.values() if isinstance(r, dict))

    def test_a_confirmed_send_spends_one_alert(self):
        self._run(delivered=True)
        self.assertEqual(self._alerts(), 1)

    def test_an_unconfirmed_send_spends_NOTHING(self):
        """A dropped alert must not consume the budget that would have retried it."""
        self._run(delivered=False)
        self.assertEqual(
            self._alerts(), 0,
            "spending the budget on a dropped alert is the B-08 defect: the "
            "60-minute re-alert clock then suppresses the retry too",
        )

    def test_the_next_tick_alerts_again_after_a_failure(self):
        self._run(delivered=False)
        send = self._run(delivered=True)
        send.assert_called_once()
        self.assertEqual(self._alerts(), 1)

    def test_silence_when_clean_is_unchanged(self):
        with mock.patch.object(stall_tripwire, "read_tail", return_value=["x"]), \
             mock.patch.object(stall_tripwire, "open_turns", return_value={}), \
             mock.patch.object(stall_tripwire, "_deliver") as send:
            stall_tripwire.main()
        send.assert_not_called()


class SenderContractTests(unittest.TestCase):
    def test_send_clean_can_opt_out_of_the_shared_queue(self):
        """These two callers re-derive, so queueing would double-deliver.

        It also keeps them off undelivered_alerts.txt, a read-all/rewrite-all
        over one unlocked file that job-watch and the tripwire would otherwise
        touch on the same minute every 30 minutes.
        """
        import dado_inbox_reasoner as reasoner

        with mock.patch.object(reasoner, "_try_send", return_value=(1, "nope")), \
             mock.patch.object(reasoner, "queue_undelivered") as queue, \
             mock.patch.object(reasoner.time, "sleep"):
            self.assertFalse(reasoner.send_clean("m", queue_on_failure=False))
        queue.assert_not_called()

    def test_the_default_still_queues(self):
        import dado_inbox_reasoner as reasoner

        with mock.patch.object(reasoner, "_try_send", return_value=(1, "nope")), \
             mock.patch.object(reasoner, "queue_undelivered") as queue, \
             mock.patch.object(reasoner.time, "sleep"):
            self.assertFalse(reasoner.send_clean("m"))
        queue.assert_called_once()


if __name__ == "__main__":
    unittest.main()
