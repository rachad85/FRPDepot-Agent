"""The keep-alive's decision logic, exercised without touching a real browser.

The expensive mistakes here are asymmetric, so each one gets a test:
  - restarting a session that is merely SIGNED OUT churns and fixes nothing
  - restarting after a DELIBERATE stop fights the operator
  - paging Rachad for one stray login tab beside working tabs is noise
  - staying silent when the session is genuinely gone is the whole failure
    this script was written to prevent
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "zoho_keepalive", Path(__file__).with_name("zoho_session_keepalive.py"))
ka = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ka)


class SignedOutDetection(unittest.TestCase):
    def test_all_pages_on_signin_is_signed_out(self):
        self.assertTrue(ka.signed_out([
            "https://accounts.zohocloud.ca/signin?service=zohobooks",
            "https://accounts.zohocloud.ca/signin",
        ]))

    def test_a_stray_login_tab_beside_working_tabs_is_not_an_outage(self):
        self.assertFalse(ka.signed_out([
            "https://books.zohocloud.ca/app#/banking/uncategorizedtransactions",
            "https://accounts.zohocloud.ca/signin",
        ]), "one login tab next to a live app tab must not page Rachad")

    def test_no_pages_at_all_is_not_signed_out(self):
        # "no pages" is a browser that is up with nothing open, not a logout.
        self.assertFalse(ka.signed_out([]))

    def test_healthy_app_pages_are_not_signed_out(self):
        self.assertFalse(ka.signed_out([
            "https://inventory.zohocloud.ca/app#/settings/preferences/items",
        ]))


class MainDecisions(unittest.TestCase):
    def setUp(self):
        self.state = {}
        p_state = patch.object(ka, "write_state", side_effect=self.state.update)
        p_read = patch.object(ka, "read_state", side_effect=lambda: dict(self.state))
        p_log = patch.object(ka, "log")
        for p in (p_state, p_read, p_log):
            p.start(); self.addCleanup(p.stop)

    def test_a_deliberate_stop_is_left_alone(self):
        with (patch.object(Path, "exists", return_value=True),
              patch.object(ka, "restart") as restart,
              patch.object(ka, "alert") as alert):
            self.assertEqual(0, ka.main())
        restart.assert_not_called()
        alert.assert_not_called()

    def test_signed_out_session_is_reported_but_never_restarted(self):
        with (patch.object(Path, "exists", return_value=False),
              patch.object(ka, "alive", return_value=True),
              patch.object(ka, "session_pages",
                           return_value=["https://accounts.zohocloud.ca/signin"]),
              patch.object(ka, "restart") as restart,
              patch.object(ka, "alert") as alert):
            ka.main()
        restart.assert_not_called()
        self.assertEqual("zoho_session_signed_out", alert.call_args[0][0])

    def test_a_dead_session_is_restarted_and_not_alerted_when_it_comes_back(self):
        with (patch.object(Path, "exists", return_value=False),
              patch.object(ka, "alive", return_value=False),
              patch.object(ka, "restart", return_value=True) as restart,
              patch.object(ka, "alert") as alert):
            self.assertEqual(0, ka.main())
        restart.assert_called_once()
        alert.assert_not_called()

    def test_a_failed_restart_pages_rachad(self):
        with (patch.object(Path, "exists", return_value=False),
              patch.object(ka, "alive", return_value=False),
              patch.object(ka, "restart", return_value=False),
              patch.object(ka, "alert") as alert):
            self.assertEqual(1, ka.main())
        self.assertEqual("zoho_session_down", alert.call_args[0][0])

    def test_the_alert_does_not_repeat_every_tick(self):
        self.state.update({"down": True, "signed_out": False})
        with (patch.object(Path, "exists", return_value=False),
              patch.object(ka, "alive", return_value=False),
              patch.object(ka, "restart", return_value=False),
              patch.object(ka, "alert") as alert):
            ka.main()
        alert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
