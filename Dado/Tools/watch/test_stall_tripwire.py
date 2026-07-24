"""Tests for the stall tripwire.

The dangerous failure mode for a tripwire is not missing a stall - it is firing
on healthy turns until Rachad learns to ignore it. Most of these tests are
therefore about STAYING SILENT.
"""
from __future__ import annotations

import datetime as dt
import unittest

import stall_tripwire as tw


def line(stamp: str, session: str, body: str) -> str:
    return f"{stamp},000 INFO [{session}] {body}\n"


def turn_start(stamp: str, session: str, platform: str = "telegram") -> str:
    return line(stamp, session,
                f"agent.turn_context: conversation turn: session={session} "
                f"model=gpt-5.6-sol platform={platform} history=9 msg='do a thing'")


def turn_end(stamp: str, session: str) -> str:
    return line(stamp, session,
                f"agent.conversation_loop: Turn ended: reason=text_response "
                f"api_calls=4/60 session={session}")


def tool(stamp: str, session: str, seconds: float, name: str = "process") -> str:
    return line(stamp, session,
                f"agent.tool_executor: tool {name} completed ({seconds:.2f}s, 1357 chars)")


def api(stamp: str, session: str, n: int) -> str:
    return line(stamp, session, f"agent.conversation_loop: API call #{n}: model=gpt-5.6-sol")


class OpenTurnTests(unittest.TestCase):
    def test_finished_turn_is_not_open(self) -> None:
        lines = [turn_start("2026-07-24 10:00:00", "S1"),
                 api("2026-07-24 10:00:10", "S1", 1),
                 turn_end("2026-07-24 10:01:00", "S1")]
        self.assertEqual(tw.open_turns(lines), {})

    def test_open_turn_is_reported_with_metrics(self) -> None:
        lines = [turn_start("2026-07-24 16:02:42", "S1"),
                 tool("2026-07-24 16:21:28", "S1", 600.05),
                 tool("2026-07-24 16:31:34", "S1", 600.06),
                 api("2026-07-24 16:31:40", "S1", 25)]
        turns = tw.open_turns(lines)
        self.assertIn("S1", turns)
        self.assertEqual(turns["S1"]["long_tools"], 2)
        self.assertEqual(turns["S1"]["api_calls"], 25)
        self.assertEqual(turns["S1"]["platform"], "telegram")

    def test_cron_turn_finishing_does_not_clear_a_stuck_telegram_turn(self) -> None:
        """The real log interleaves cron sweeps into the same file."""
        lines = [turn_start("2026-07-24 16:02:42", "S1"),
                 tool("2026-07-24 16:21:28", "S1", 600.05),
                 turn_start("2026-07-24 17:01:00", "CRON", platform="local"),
                 api("2026-07-24 17:01:30", "CRON", 3),
                 turn_end("2026-07-24 17:02:39", "CRON"),
                 tool("2026-07-24 17:12:05", "S1", 600.06)]
        turns = tw.open_turns(lines)
        self.assertIn("S1", turns)
        self.assertNotIn("CRON", turns)
        self.assertEqual(turns["S1"]["long_tools"], 2)

    def test_a_later_turn_in_the_same_session_resets_the_clock(self) -> None:
        """Session ids repeat across turns; only the newest turn counts."""
        lines = [turn_start("2026-07-24 10:00:00", "S1"),
                 tool("2026-07-24 10:05:00", "S1", 600.0),
                 turn_end("2026-07-24 10:06:00", "S1"),
                 turn_start("2026-07-24 12:00:00", "S1"),
                 api("2026-07-24 12:00:30", "S1", 2)]
        turns = tw.open_turns(lines)
        self.assertEqual(turns["S1"]["started"], dt.datetime(2026, 7, 24, 12, 0, 0))
        self.assertEqual(turns["S1"]["long_tools"], 0, "old turn's blocking call must not carry over")

    def test_short_tool_calls_are_not_counted_as_blocking(self) -> None:
        lines = [turn_start("2026-07-24 10:00:00", "S1"),
                 tool("2026-07-24 10:00:05", "S1", 1.59, "search_files"),
                 tool("2026-07-24 10:00:20", "S1", 47.11, "terminal")]
        self.assertEqual(tw.open_turns(lines)["S1"]["long_tools"], 0)

    def test_real_log_line_shape_parses(self) -> None:
        """Guard against the live format drifting away from these regexes."""
        real = ("2026-07-24 19:03:16,769 INFO [20260724_153746_f881f5ca] "
                "agent.tool_executor: tool process completed (600.05s, 1357 chars)\n")
        lines = [turn_start("2026-07-24 16:02:42", "20260724_153746_f881f5ca"), real]
        self.assertEqual(tw.open_turns(lines)["20260724_153746_f881f5ca"]["long_tools"], 1)


if __name__ == "__main__":
    unittest.main()
