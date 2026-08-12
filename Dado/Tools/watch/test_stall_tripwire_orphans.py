"""A turn killed by a gateway restart is a corpse, not a stall.

open_turns() pops a session on TURN_END. A gateway death writes no such line, so
the dead turn looks OPEN and the tripwire described it as "still active, ask her
what she is doing" - an instruction to interrogate an agent with no memory of the
request. Then the moment Rachad re-asks (exactly what a user does when ignored)
the newer turn overwrites the dead one and the evidence is gone forever.

Measured on the live log while building this: TWO real orphans, including a
business instruction on Discord - "Please proceed the PO we just received from
SCT..." - lost when the gateway restarted on 2026-08-11. Nobody was ever told.

Proposed 2026-08-04, built 2026-08-12.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stall_tripwire as tw  # noqa: E402


def line(stamp, body):
    return f"{stamp},000 INFO {body}\n"


def inbound(stamp, chat="891365639", msg="hello", platform="telegram"):
    return line(stamp, f"gateway.run: inbound message: platform={platform} "
                       f"user=RH chat={chat} msg='{msg}' reply_to_id=None")


def ready(stamp, chat="891365639", platform="telegram"):
    return line(stamp, f"gateway.run: response ready: platform={platform} chat={chat} ok")


def boot(stamp):
    return line(stamp, "gateway.run: Starting Hermes Gateway")


BOUNDARY = dt.datetime(2026, 8, 12, 10, 0, 0)


class OrphanDetectionTests(unittest.TestCase):
    def test_a_message_that_died_with_its_gateway_is_found(self):
        lines = [inbound("2026-08-12 09:50:00", msg="proceed the PO from SCT"),
                 boot("2026-08-12 10:00:00")]
        found = tw.orphaned_messages(lines, BOUNDARY)
        self.assertEqual(len(found), 1)
        self.assertIn("SCT", found[0]["text"])

    def test_an_answered_message_is_not_an_orphan(self):
        lines = [inbound("2026-08-12 09:50:00"), ready("2026-08-12 09:51:00"),
                 boot("2026-08-12 10:00:00")]
        self.assertEqual(tw.orphaned_messages(lines, BOUNDARY), [])

    def test_a_message_in_the_CURRENT_life_is_not_an_orphan(self):
        """It may still be being worked on. Only the previous life is dead."""
        lines = [boot("2026-08-12 10:00:00"), inbound("2026-08-12 10:05:00")]
        self.assertEqual(tw.orphaned_messages(lines, BOUNDARY), [])

    def test_a_reply_clears_only_its_OWN_gateway_life(self):
        """The design decision that must not be 'simplified'.

        Rachad re-asks after being ignored and IS answered. A lane-wide reset
        would let that later reply erase the evidence of the lost message before
        the next 15-minute tick, making it luck whether he was ever told.
        """
        lines = [inbound("2026-08-12 09:50:00", msg="the lost one"),
                 boot("2026-08-12 10:00:00"),
                 inbound("2026-08-12 10:05:00", msg="re-asking"),
                 ready("2026-08-12 10:06:00")]
        found = tw.orphaned_messages(lines, BOUNDARY)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["text"], "the lost one")

    def test_no_boundary_means_the_check_is_inert(self):
        """None is a legitimate outcome; it never guesses."""
        self.assertEqual(tw.orphaned_messages([inbound("2026-08-12 09:50:00")], None), [])

    def test_several_lost_messages_are_counted_but_reported_once(self):
        lines = [inbound("2026-08-12 09:50:00", msg="first"),
                 inbound("2026-08-12 09:55:00", msg="second"),
                 boot("2026-08-12 10:00:00")]
        found = tw.orphaned_messages(lines, BOUNDARY)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["count"], 2)
        self.assertIn("1 message(s) you sent after it", tw.orphan_message(found[0]))

    def test_lanes_are_independent(self):
        lines = [inbound("2026-08-12 09:50:00", chat="A", msg="a"),
                 inbound("2026-08-12 09:51:00", chat="B", msg="b"),
                 ready("2026-08-12 09:52:00", chat="B"),
                 boot("2026-08-12 10:00:00")]
        found = tw.orphaned_messages(lines, BOUNDARY)
        self.assertEqual([o["chat"] for o in found], ["A"])


class OrphanMessageTests(unittest.TestCase):
    def test_a_textless_message_is_described_not_quoted_empty(self):
        """The live log holds msg='' for an attachment or voice note.

        Quoting "" back at him is worse than useless. Found by running against
        the real log, not in review.
        """
        orphan = {"platform": "telegram", "chat": "1", "received": BOUNDARY,
                  "text": "", "count": 1, "died": BOUNDARY}
        message = tw.orphan_message(orphan)
        self.assertIn("attachment or voice note", message)
        self.assertNotIn('- "" -', message)

    def test_a_long_message_is_truncated(self):
        orphan = {"platform": "telegram", "chat": "1", "received": BOUNDARY,
                  "text": "x" * 200, "count": 1, "died": BOUNDARY}
        self.assertIn("...", tw.orphan_message(orphan))


class DeliberateStopTests(unittest.TestCase):
    """He stopped her himself: reporting it is crying wolf."""

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / ".test_tmp_stops"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.ledger = self.tmp / "gateway_stops.log"
        self.p = mock.patch.object(tw, "STOP_LEDGER_PATH", self.ledger)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stamps_are_read(self):
        self.ledger.write_text("2026-08-12T09:55:00 stop requested via STOP_DADO.bat\n",
                               encoding="utf-8")
        self.assertEqual(tw.deliberate_stops(), [dt.datetime(2026, 8, 12, 9, 55, 0)])

    def test_a_missing_ledger_is_not_an_error(self):
        self.assertEqual(tw.deliberate_stops(), [])

    def test_unparseable_lines_are_skipped_not_fatal(self):
        self.ledger.write_text("# a comment\nnot a date at all\n"
                               "2026-08-12T09:55:00 stop\n", encoding="utf-8")
        self.assertEqual(len(tw.deliberate_stops()), 1)


class GatewayBoundaryTests(unittest.TestCase):
    def test_a_future_start_time_is_refused(self):
        """Believing it would silence the open-turn check for every turn."""
        future = (dt.datetime.now() + dt.timedelta(days=1)).timestamp() * 100
        with mock.patch.object(tw, "gateway_state_path") as path:
            path.return_value = mock.Mock()
            path.return_value.read_text.return_value = json.dumps({"start_time": future})
            self.assertIsNone(tw.current_gateway_start([]))

    def test_it_falls_back_to_the_log_when_state_is_unreadable(self):
        with mock.patch.object(tw, "gateway_state_path") as path:
            path.return_value.read_text.side_effect = OSError("gone")
            got = tw.current_gateway_start([boot("2026-08-12 10:00:00")])
        self.assertEqual(got, dt.datetime(2026, 8, 12, 10, 0, 0))

    def test_centiseconds_are_divided_by_one_hundred(self):
        when = dt.datetime(2026, 8, 12, 8, 0, 0)
        with mock.patch.object(tw, "gateway_state_path") as path:
            path.return_value.read_text.return_value = json.dumps(
                {"start_time": when.timestamp() * 100})
            self.assertEqual(tw.current_gateway_start([]), when)


if __name__ == "__main__":
    unittest.main()
