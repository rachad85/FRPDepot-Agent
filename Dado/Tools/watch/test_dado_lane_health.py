"""Tests for the chat-lane health check.

The whole value of this module is that it does NOT believe gateway_state.json
at face value. These tests pin the four judgements that make it trustworthy:

  * a lane that is not configured is not expected to be up (no false alarm),
  * a status line written before the running gateway started is treated as
    ABSENT, never as truth -- in both directions,
  * a freshly started gateway is given time before anything is called broken,
  * a genuinely down lane is reported while the surviving lane is named.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

import dado_lane_health as health  # noqa: E402


class LaneHealthTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self._original_profile_dir = health.profile_dir
        self._original_pid_alive = health.pid_is_alive
        health.profile_dir = lambda: self.home  # type: ignore[assignment]
        health.pid_is_alive = lambda pid: True  # type: ignore[assignment]
        self.addCleanup(self._restore)

        # The disable flag must not exist for these tests to be meaningful.
        self._original_flag = health.DISABLE_FLAG
        health.DISABLE_FLAG = self.home / "never-created.flag"

    def _restore(self) -> None:
        health.profile_dir = self._original_profile_dir  # type: ignore[assignment]
        health.pid_is_alive = self._original_pid_alive  # type: ignore[assignment]
        health.DISABLE_FLAG = self._original_flag

    # -- helpers ----------------------------------------------------------

    def write_env(self, keys: dict[str, str], *, age_seconds: float = 7 * 86400) -> None:
        """Write the profile .env, dated BEFORE the gateway started by default.

        The mtime matters: a credential newer than the running gateway means
        "configured, awaiting restart", not "down". Tests that want the normal
        steady state must therefore look like the token has been there a while.
        """
        lines = [f"{key}={value}" for key, value in keys.items()]
        path = self.home / ".env"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if age_seconds:
            past = time.time() - age_seconds
            os.utime(path, (past, past))

    def write_state(
        self,
        platforms: dict[str, dict],
        *,
        gateway_age_seconds: float = 3600,
    ) -> None:
        started = datetime.now(timezone.utc) - timedelta(seconds=gateway_age_seconds)
        (self.home / "gateway_state.json").write_text(
            json.dumps({
                "pid": 4242,
                "gateway_state": "running",
                "start_time": int(started.timestamp() * 100),
                "platforms": platforms,
            }),
            encoding="utf-8",
        )

    @staticmethod
    def entry(state: str, *, minutes_ago: float = 1.0, error: str | None = None) -> dict:
        moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        return {
            "state": state,
            "error_message": error,
            "error_code": None,
            "updated_at": moment.isoformat(),
        }

    # -- not configured is not expected -----------------------------------

    def test_a_lane_with_no_token_is_not_expected_to_be_up(self):
        self.write_env({"TELEGRAM_BOT_TOKEN": "x"})
        self.write_state({"telegram": self.entry("connected")})
        verdict = health.assess()
        self.assertEqual(verdict["verdict"], "healthy")
        self.assertEqual(verdict["healthy"], ["telegram"])

    def test_an_empty_token_value_does_not_count_as_configured(self):
        self.write_env({"TELEGRAM_BOT_TOKEN": "x", "DISCORD_BOT_TOKEN": ""})
        self.write_state({"telegram": self.entry("connected")})
        self.assertEqual(health.assess()["verdict"], "healthy")

    # -- stale lines are not truth, in either direction --------------------

    def test_a_stale_connected_line_does_not_prove_a_lane_is_up(self):
        """The live false positive this module was written for.

        dado's real file claimed discord 'connected' from 2026-08-04 on a
        gateway started 2026-08-10.
        """
        self.write_env({"TELEGRAM_BOT_TOKEN": "x", "DISCORD_BOT_TOKEN": "y"})
        self.write_state(
            {
                "telegram": self.entry("connected"),
                # Written 2 hours before this gateway started.
                "discord": self.entry("connected", minutes_ago=180),
            },
            gateway_age_seconds=3600,
        )
        verdict = health.assess()
        self.assertEqual(verdict["verdict"], "lane_down")
        self.assertEqual([item["lane"] for item in verdict["down"]], ["discord"])
        self.assertIn("stale", verdict["down"][0]["detail"])

    def test_a_stale_fatal_line_does_not_raise_a_false_alarm(self):
        """The mirror image: a dead configuration must not alert forever."""
        self.write_env({"TELEGRAM_BOT_TOKEN": "x"})
        self.write_state(
            {
                "telegram": self.entry("connected"),
                "whatsapp": self.entry("fatal", minutes_ago=180, error="not paired"),
            },
            gateway_age_seconds=3600,
        )
        # whatsapp is not a configured chat lane here, and its line is stale.
        self.assertEqual(health.assess()["verdict"], "healthy")

    # -- startup grace -----------------------------------------------------

    def test_a_just_started_gateway_is_not_called_broken(self):
        self.write_env({"TELEGRAM_BOT_TOKEN": "x", "DISCORD_BOT_TOKEN": "y"})
        self.write_state({"telegram": self.entry("connected")}, gateway_age_seconds=5)
        self.assertEqual(health.assess()["verdict"], "starting")

    def test_after_the_grace_a_missing_lane_is_reported(self):
        self.write_env({"TELEGRAM_BOT_TOKEN": "x", "DISCORD_BOT_TOKEN": "y"})
        self.write_state({"telegram": self.entry("connected")}, gateway_age_seconds=3600)
        verdict = health.assess()
        self.assertEqual(verdict["verdict"], "lane_down")
        self.assertEqual(verdict["down"][0]["lane"], "discord")
        self.assertEqual(verdict["down"][0]["state"], "missing")

    # -- genuine failure ---------------------------------------------------

    def test_a_failed_lane_is_reported_with_its_reason_and_the_survivor(self):
        self.write_env({"TELEGRAM_BOT_TOKEN": "x", "DISCORD_BOT_TOKEN": "y"})
        self.write_state({
            "telegram": self.entry("connected"),
            "discord": self.entry("fatal", error="Discord bot token already in use (PID 17152)"),
        })
        verdict = health.assess()
        self.assertEqual(verdict["verdict"], "lane_down")
        self.assertEqual(verdict["healthy"], ["telegram"])
        message = health.alert_message(verdict["down"], verdict["healthy"])
        self.assertIn("DISCORD", message)
        self.assertIn("already in use", message)
        self.assertIn("TELEGRAM", message)
        self.assertIn("8647 is the API server", message)

    def test_a_dead_gateway_is_left_to_the_port_watchdog(self):
        health.pid_is_alive = lambda pid: False  # type: ignore[assignment]
        self.write_env({"TELEGRAM_BOT_TOKEN": "x"})
        self.write_state({"telegram": self.entry("connected")})
        verdict = health.assess()
        self.assertEqual(verdict["verdict"], "gateway_down")
        self.assertEqual(verdict["down"], [])

    def test_missing_state_file_is_unknown_not_an_alert(self):
        self.write_env({"TELEGRAM_BOT_TOKEN": "x"})
        self.assertEqual(health.assess()["verdict"], "unknown")

    # -- the setup window: token written, gateway not yet restarted ---------

    def test_a_token_added_since_the_gateway_started_is_awaiting_restart(self):
        """The false alarm the documented setup order would otherwise guarantee.

        SET_DADO_DISCORD_TOKEN.bat is step 5 and the restart is step 7. Between
        them the 5-minute watchdog fires. Paging Rachad "DISCORD LANE IS DOWN"
        while he is midway through the instructions would be a broken promise.
        """
        self.write_state({"telegram": self.entry("connected")}, gateway_age_seconds=3600)
        # Token written NOW, i.e. after the gateway started.
        self.write_env({"TELEGRAM_BOT_TOKEN": "x", "DISCORD_BOT_TOKEN": "y"}, age_seconds=0)
        verdict = health.assess()
        self.assertEqual(verdict["verdict"], "awaiting_restart")
        self.assertEqual(verdict["pending"], ["discord"])
        self.assertEqual(verdict["down"], [])

    def test_a_lane_that_actually_failed_is_still_reported_during_that_window(self):
        """Awaiting-restart must not become a blanket excuse.

        If the gateway DID report the lane and reported it broken, that is real
        news regardless of when the .env was last touched.
        """
        self.write_state({
            "telegram": self.entry("connected"),
            "discord": self.entry("fatal", error="Discord bot token already in use"),
        }, gateway_age_seconds=3600)
        self.write_env({"TELEGRAM_BOT_TOKEN": "x", "DISCORD_BOT_TOKEN": "y"}, age_seconds=0)
        verdict = health.assess()
        self.assertEqual(verdict["verdict"], "lane_down")
        self.assertEqual(verdict["down"][0]["lane"], "discord")


class LaneAlertPolicyTestCase(LaneHealthTestCase):
    """main()'s paging policy: two samples, and never clear an alert we never sent."""

    def setUp(self) -> None:
        super().setUp()
        self.sent: list[tuple[str, str]] = []
        self.cleared: list[str] = []
        self._orig_send = health.send_alert
        self._orig_clear = health.clear_alert
        self._orig_state_file = health.STATE_FILE
        health.send_alert = lambda message, reason: self.sent.append((reason, message))
        health.clear_alert = lambda reason: self.cleared.append(reason)
        health.STATE_FILE = self.home / "lane_health_state.json"
        self.addCleanup(self._restore_policy)

    def _restore_policy(self) -> None:
        health.send_alert = self._orig_send
        health.clear_alert = self._orig_clear
        health.STATE_FILE = self._orig_state_file

    def down_state(self) -> None:
        self.write_state({
            "telegram": self.entry("connected"),
            "discord": self.entry("disconnected"),
        }, gateway_age_seconds=3600)
        self.write_env({"TELEGRAM_BOT_TOKEN": "x", "DISCORD_BOT_TOKEN": "y"})

    def test_one_bad_sample_is_not_paged(self):
        self.down_state()
        self.assertEqual(health.main([]), 0)
        self.assertEqual(self.sent, [], "a single sample is usually a reconnect")

    def test_two_consecutive_bad_samples_are_paged_once(self):
        self.down_state()
        health.main([])
        self.assertEqual(health.main([]), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("DISCORD", self.sent[0][1])

    def test_a_blip_between_two_checks_never_pages(self):
        self.down_state()
        health.main([])
        # Recovered before the second sample.
        self.write_state({
            "telegram": self.entry("connected"),
            "discord": self.entry("connected"),
        }, gateway_age_seconds=3600)
        health.main([])
        self.down_state()
        health.main([])
        self.assertEqual(self.sent, [], "the streak must reset on recovery")

    def test_a_healthy_run_does_not_clear_an_alert_it_never_raised(self):
        """--clear also deletes the SHARED last-resort Desktop marker.

        Calling it on every healthy 5-minute run would quietly erase the marker
        left by a real gateway-down alert Rachad had not read yet.
        """
        self.write_state({"telegram": self.entry("connected")}, gateway_age_seconds=3600)
        self.write_env({"TELEGRAM_BOT_TOKEN": "x"})
        health.main([])
        health.main([])
        self.assertEqual(self.cleared, [])

    def test_recovery_after_a_real_alert_does_clear_it(self):
        self.down_state()
        health.main([])
        health.main([])
        self.assertEqual(len(self.sent), 1)
        self.write_state({
            "telegram": self.entry("connected"),
            "discord": self.entry("connected"),
        }, gateway_age_seconds=3600)
        self.assertEqual(health.main([]), 0)
        self.assertEqual(self.cleared, ["chat_lane_down"])

    def test_a_configuration_failure_is_not_told_to_just_restart(self):
        message = health.alert_message(
            [{"lane": "discord", "state": "fatal", "detail": "token already in use"}],
            ["telegram"],
        )
        self.assertIn("Do NOT just restart", message)
        self.assertIn("CHECK_DADO_DISCORD.bat", message)

    def test_a_plain_disconnect_is_told_to_restart_with_the_caveat(self):
        message = health.alert_message(
            [{"lane": "discord", "state": "disconnected", "detail": "no detail given"}],
            ["telegram"],
        )
        self.assertIn("START_DADO.bat", message)
        self.assertIn("ends anything she is working on", message)


class LaneHealthSourceTestCase(unittest.TestCase):
    """It must never take the action that would make things worse.

    These read CODE, not prose. The module's docstring necessarily discusses
    the gateway running and restarting -- explaining why it must not do those
    things -- so a raw substring scan would fail on its own reasoning.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = Path(health.__file__).read_text(encoding="utf-8")
        cls.code = cls._strip_comments_and_strings(cls.source)

    @staticmethod
    def _strip_comments_and_strings(source: str) -> str:
        import io
        import tokenize

        kept: list[str] = []
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
        return " ".join(kept)

    def test_it_never_restarts_or_kills_the_gateway(self):
        # Checked as ACTIONS, not as the word "restart" -- the module legitimately
        # reasons about restarts (awaiting_restart) without ever performing one.
        for forbidden in ("taskkill", "Stop_Process", "Popen", "startfile", "TerminateProcess"):
            self.assertNotIn(forbidden, self.code,
                             f"{forbidden}: restarting kills in-flight turns; alert instead")

    def test_its_only_subprocess_is_the_out_of_band_alerter(self):
        # subprocess.run appears exactly twice: send_alert and clear_alert.
        self.assertEqual(self.code.count("subprocess . run"), 2)

    def test_it_respects_a_deliberate_stop(self):
        self.assertIn("DISABLE_FLAG", self.source)

    def test_it_covers_both_of_her_chat_lanes(self):
        self.assertEqual(
            health.CHAT_LANES,
            {"telegram": "TELEGRAM_BOT_TOKEN", "discord": "DISCORD_BOT_TOKEN"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
