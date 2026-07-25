"""Tests for the stall tripwire.

The dangerous failure mode for a tripwire is not missing a stall - it is firing
on healthy turns until Rachad learns to ignore it. Most of these tests are
therefore about STAYING SILENT.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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


class AlertBudgetTests(unittest.TestCase):
    """B-07: the per-turn alert budget must only be spent on what is PRINTED.

    record["alerts"] += 1 used to run for every stalled session found while only
    problems[:3] were printed. With four or more concurrent sessions -- a
    Telegram turn plus several cron-spawned ones interleaved in the same log --
    the 4th session's counter climbed 1, 2, 3 across three passes, hit
    MAX_ALERTS_PER_TURN and went permanently quiet having never once reached
    Rachad. The genuinely stuck turn silently exhausted its own budget.
    """

    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.state = Path(self._folder.name) / "state.json"
        self.log = Path(self._folder.name) / "agent.log"
        for attr, value in (("STATE_PATH", self.state),):
            patcher = patch.object(tw, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(tw, "log_path", lambda: self.log)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _stalled_log(self, sessions: int) -> None:
        old = (dt.datetime.now() - dt.timedelta(minutes=90)).strftime("%Y-%m-%d %H:%M:%S")
        self.log.write_text(
            "".join(turn_start(old, f"s{i}") for i in range(sessions)), encoding="utf-8"
        )

    def _run(self) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            tw.main()
        return out.getvalue()

    def test_unprinted_stalls_do_not_burn_their_budget(self):
        self._stalled_log(5)
        first = self._run()
        self.assertIn("more stalled turn(s) not listed", first)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        spent = [k for k, v in state.items() if v["alerts"] > 0]
        self.assertEqual(len(spent), tw.MAX_PROBLEMS_PER_TICK,
                         "only the printed problems may spend an alert")

    def test_a_held_stall_still_reaches_rachad_eventually(self):
        self._stalled_log(5)
        seen = ""
        for _ in range(4):
            seen += self._run()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertTrue(all(v["alerts"] > 0 for v in state.values()),
                        "every stalled turn must be announced at least once")


class TailWindowTests(unittest.TestCase):
    """B-10: a turn whose start line scrolled out used to become invisible."""

    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.log = Path(self._folder.name) / "agent.log"

    def test_read_tail_returns_the_end_of_a_large_file(self):
        filler = "".join(f"{i} filler line\n" for i in range(50_000))
        self.log.write_text(filler + "LAST LINE\n", encoding="utf-8")
        tail = tw.read_tail(self.log)
        self.assertTrue(tail[-1].startswith("LAST LINE"))
        self.assertLessEqual(len(tail), tw.TAIL_LINES)

    def test_a_rotated_sibling_is_consulted_when_no_turn_start_is_in_view(self):
        old = (dt.datetime.now() - dt.timedelta(minutes=90)).strftime("%Y-%m-%d %H:%M:%S")
        rotated = self.log.with_name("agent.log.1")
        rotated.write_text(turn_start(old, "s1"), encoding="utf-8")
        self.log.write_text(api(old, "s1", 3), encoding="utf-8")  # no start line here
        state = Path(self._folder.name) / "state.json"
        with (patch.object(tw, "STATE_PATH", state),
              patch.object(tw, "log_path", lambda: self.log)):
            out = io.StringIO()
            with redirect_stdout(out):
                tw.main()
        self.assertIn("has sent you nothing", out.getvalue(),
                      "a rotation must not hide an open turn")

    def test_no_siblings_is_not_an_error(self):
        self.log.write_text("nothing interesting\n", encoding="utf-8")
        self.assertEqual(tw.rotated_siblings(self.log), [])


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
