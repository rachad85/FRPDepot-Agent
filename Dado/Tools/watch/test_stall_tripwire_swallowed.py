"""A message can die BEFORE it ever becomes a turn — the class orphans miss.

Orphan detection starts from `inbound message:`. A message that was never picked
up produces no such line: it only ever reaches the adapter's own arrival line
(`Cached user voice at ...` / `Flushing text batch ...`), so the orphan check
cannot see it at all.

Measured on Sary's live log on 2026-08-13, which is why this exists: a voice note
at 12:58 asking a real business question, and a text at 13:33, both arrived while
his session was wedged on a 90-minute work unit, were never picked up, and were
destroyed by the 14:05 restart. He was told nothing. Dado's log carries the same
two line types (16 voice arrivals, 326 text flushes) and had the same hole.
"""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stall_tripwire as tw  # noqa: E402


def line(stamp, body):
    return f"{stamp},000 INFO {body}\n"


def voice(stamp, platform="Telegram"):
    return line(stamp, f"hermes_plugins.telegram_platform.adapter: "
                       f"[{platform}] Cached user voice at C:\\cache\\a.ogg")


def flush(stamp, chat="891365639", platform="telegram", chars=42):
    return line(stamp, f"hermes_plugins.{platform}_platform.adapter: "
                       f"[{platform.title()}] Flushing text batch "
                       f"agent:main:{platform}:dm:{chat} ({chars} chars)")


def inbound(stamp, chat="891365639", msg="hello", platform="telegram"):
    return line(stamp, f"gateway.run: inbound message: platform={platform} "
                       f"user=RH chat={chat} msg='{msg}' reply_to_id=None")


def ready(stamp, chat="891365639", platform="telegram"):
    return line(stamp, f"gateway.run: response ready: platform={platform} chat={chat} ok")


def boot(stamp):
    return line(stamp, "gateway.run: Starting Hermes Gateway")


BOUNDARY = dt.datetime(2026, 8, 12, 10, 0, 0)


class SwallowedDetectionTests(unittest.TestCase):
    def test_a_voice_note_never_picked_up_is_found(self):
        lines = [voice("2026-08-12 09:50:00"), boot("2026-08-12 10:00:00")]
        found = tw.swallowed_messages(lines, BOUNDARY)
        self.assertEqual(1, len(found))
        self.assertEqual("a voice note", found[0]["what"])
        self.assertIn("NEVER PICKED UP", tw.swallowed_message(found[0]))

    def test_a_text_never_picked_up_is_found(self):
        lines = [flush("2026-08-12 09:50:00"), boot("2026-08-12 10:00:00")]
        found = tw.swallowed_messages(lines, BOUNDARY)
        self.assertEqual(1, len(found))
        self.assertIn("text message (42 chars)", found[0]["what"])

    def test_an_arrival_that_became_a_turn_is_not_swallowed(self):
        lines = [flush("2026-08-12 09:50:00"), inbound("2026-08-12 09:50:01"),
                 boot("2026-08-12 10:00:00")]
        self.assertEqual([], tw.swallowed_messages(lines, BOUNDARY))

    def test_an_arrival_picked_up_much_later_in_the_same_life_is_not_swallowed(self):
        # Queued behind a long turn and answered an hour later. NORMAL — both
        # agents legitimately run turns over an hour, and paging him for this
        # would make the tripwire noise.
        lines = [voice("2026-08-12 08:30:00"), inbound("2026-08-12 09:45:00"),
                 boot("2026-08-12 10:00:00")]
        self.assertEqual([], tw.swallowed_messages(lines, BOUNDARY))

    def test_swallowed_and_orphan_never_report_the_same_message(self):
        # Arrival -> inbound -> no response -> restart. That is an ORPHAN and
        # must be reported exactly once, by the orphan check only.
        lines = [flush("2026-08-12 09:50:00"), inbound("2026-08-12 09:50:01"),
                 boot("2026-08-12 10:00:00")]
        self.assertEqual([], tw.swallowed_messages(lines, BOUNDARY))
        self.assertEqual(1, len(tw.orphaned_messages(lines, BOUNDARY)))

    def test_a_pickup_in_a_later_life_does_not_clear_an_earlier_arrival(self):
        # The re-asked-question trap: he gets ignored, re-sends, and the new
        # message's pickup must not erase the evidence that the first was lost.
        lines = [voice("2026-08-12 09:50:00"), boot("2026-08-12 10:00:00"),
                 inbound("2026-08-12 10:05:00", msg="did you get my voice note?")]
        self.assertEqual(1, len(tw.swallowed_messages(lines, BOUNDARY)))

    def test_an_arrival_in_the_current_life_is_not_reported(self):
        # Still live: she may yet pick it up, so calling it lost would be false.
        lines = [boot("2026-08-12 10:00:00"), voice("2026-08-12 10:05:00")]
        self.assertEqual([], tw.swallowed_messages(lines, BOUNDARY))

    def test_a_discord_pickup_does_not_clear_a_telegram_arrival(self):
        lines = [voice("2026-08-12 09:50:00"),
                 inbound("2026-08-12 09:51:00", platform="discord", chat="99"),
                 boot("2026-08-12 10:00:00")]
        self.assertEqual(1, len(tw.swallowed_messages(lines, BOUNDARY)))

    def test_later_messages_that_went_the_same_way_are_counted_not_listed(self):
        lines = [voice("2026-08-12 09:50:00"), voice("2026-08-12 09:52:00"),
                 boot("2026-08-12 10:00:00")]
        found = tw.swallowed_messages(lines, BOUNDARY)
        self.assertEqual(2, found[0]["count"])
        self.assertIn("1 message(s) you sent after it", tw.swallowed_message(found[0]))

    def test_inbound_logged_just_before_its_own_arrival_is_not_swallowed(self):
        # THE FALSE POSITIVE THE REAL LOG PRODUCED. Measured on 2026-08-11:
        #   23:35:04,731  inbound message: ... msg=''
        #   23:35:04,841  Flushing text batch ... (65 chars)
        # Two loggers, same message, written 110 ms inverted. Matching in
        # streaming order reported one ordinary message as BOTH an orphan and
        # swallowed.
        lines = [inbound("2026-08-11 23:35:04", msg=""),
                 flush("2026-08-11 23:35:04", chars=65),
                 boot("2026-08-12 00:21:39")]
        self.assertEqual([], tw.swallowed_messages(lines, BOUNDARY))

    def test_no_boundary_means_the_check_is_inert(self):
        # Same refusal-to-guess rule the orphan check uses: with no established
        # gateway start, nothing can be classified as lost.
        lines = [voice("2026-08-12 09:50:00"), boot("2026-08-12 10:00:00")]
        self.assertEqual([], tw.swallowed_messages(lines, None))

    def test_lane_from_session(self):
        self.assertEqual(("telegram", "891365639"),
                         tw.lane_from_session("agent:main:telegram:dm:891365639"))
        self.assertIsNone(tw.lane_from_session("nonsense"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
