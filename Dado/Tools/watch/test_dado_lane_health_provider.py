"""Lane 3: the model provider.

A connected adapter proves the pipe, not what is at the far end. On 2026-08-10
telegram and discord were both "connected" while every turn died at the model.
assess() returned "healthy" the whole way through - correctly, and uselessly.

The real evidence, from profiles\\dado\\logs\\errors.log: five
"Non-retryable client error" lines between 13:36:58 and 13:50:18 that day, and
none at any other point in the 21 days either side. These tests replay that
window.

Kept in its own file so the 33 tests in test_dado_lane_health.py stay exactly as
they were - the provider lane must not disturb the chat-lane contract.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dado_lane_health as health  # noqa: E402

# The real outage, verbatim shape.
OUTAGE = [
    "2026-08-10 13:36:58 ERROR agent.conversation_loop: Non-retryable client error: 401",
    "2026-08-10 13:37:31 ERROR agent.conversation_loop: Non-retryable client error: 401",
    "2026-08-10 13:37:53 ERROR agent.conversation_loop: Non-retryable client error: 401",
    "2026-08-10 13:39:49 ERROR agent.conversation_loop: Non-retryable client error: 401",
    "2026-08-10 13:50:18 ERROR agent.conversation_loop: Non-retryable client error: 401",
]


class ProviderLaneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / ".test_tmp_provider"
        (self.tmp / "logs").mkdir(parents=True, exist_ok=True)
        self.log = self.tmp / "logs" / "errors.log"
        self.patch = mock.patch.object(health, "profile_dir", lambda: self.tmp)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, lines):
        self.log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _at(self, when):
        return health.provider_failures(datetime.fromisoformat(when))

    # ---- the outage it exists for -----------------------------------------
    def test_one_failure_does_not_page(self):
        """A single error is a blip; hermes retries on its own."""
        self._write(OUTAGE[:1])
        self.assertLess(len(self._at("2026-08-10T13:37:00")),
                        health.PROVIDER_FAILURES_BEFORE_DOWN)

    def test_the_second_failure_pages(self):
        self._write(OUTAGE[:2])
        self.assertGreaterEqual(len(self._at("2026-08-10T13:37:35")),
                                health.PROVIDER_FAILURES_BEFORE_DOWN)

    def test_it_stays_down_for_the_whole_outage(self):
        self._write(OUTAGE)
        self.assertGreaterEqual(len(self._at("2026-08-10T13:45:00")), 2)

    def test_it_recovers_once_the_window_passes(self):
        """It must not page forever about an outage that ended."""
        self._write(OUTAGE)
        self.assertEqual(self._at("2026-08-10T14:15:00"), [])

    def test_old_failures_are_not_counted(self):
        self._write(OUTAGE)
        self.assertEqual(self._at("2026-08-11T09:00:00"), [])

    # ---- honesty ----------------------------------------------------------
    def test_a_missing_log_is_silent_not_an_alarm(self):
        self.assertEqual(health.provider_failures(), [])

    def test_undated_and_unparseable_lines_are_ignored(self):
        """A false provider alarm sends Rachad chasing a working model."""
        self._write([
            "no timestamp here Non-retryable client error",
            "9999-99-99 99:99:99 Non-retryable client error",
            OUTAGE[0],
        ])
        self.assertEqual(len(self._at("2026-08-10T13:37:00")), 1)

    def test_unrelated_errors_do_not_count(self):
        self._write([
            "2026-08-10 13:36:58 ERROR something else entirely",
            "2026-08-10 13:37:00 WARNING marking nous unhealthy for 600s",
        ])
        self.assertEqual(self._at("2026-08-10T13:38:00"), [])

    def test_a_future_dated_line_is_ignored(self):
        """Clock skew must not manufacture an outage."""
        self._write(["2099-01-01 00:00:00 Non-retryable client error: x"])
        self.assertEqual(self._at("2026-08-10T13:38:00"), [])

    # ---- the chat-lane contract is untouched -------------------------------
    def test_a_healthy_provider_is_not_added_to_the_healthy_list(self):
        """`healthy` is the "Still working" line and is about CHAT lanes.

        Four tests in test_dado_lane_health.py assert the chat-only list; a
        healthy provider there would break them and tell Rachad nothing he can
        act on.
        """
        self._write([])
        with mock.patch.object(health, "load_state", return_value=None):
            verdict = health.assess()
        self.assertNotIn("model provider", verdict.get("healthy", []))

    def test_the_advice_matches_a_provider_failure(self):
        """A Discord token check cannot fix a dead model."""
        message = health.alert_message(
            [{"lane": "model provider", "state": "failing", "detail": "2 errors"}], ["telegram"]
        )
        self.assertIn("model provider is the problem", message)
        self.assertNotIn("CHECK_DADO_DISCORD.bat", message)
        self.assertIn("will NOT fix", message)

    def test_chat_lane_advice_is_unchanged(self):
        message = health.alert_message(
            [{"lane": "discord", "state": "fatal", "detail": "token in use"}], ["telegram"]
        )
        self.assertIn("CHECK_DADO_DISCORD.bat", message)
        self.assertNotIn("model provider is the problem", message)


if __name__ == "__main__":
    unittest.main()
