"""The send fuse must fire for EVERY test runner - and for no cron run.

Backstory, because the shape of this file is a direct response to it. On
2026-08-12 wiring delivery into job_runner/stall_tripwire made a plain `pytest`
run of the existing suite put 14 real messages on Rachad's phone. The fix added
`if os.environ.get("PYTEST_CURRENT_TEST")` at four call sites - but that
variable is set by PYTEST ONLY, and 12 of the 14 test files in this directory
are `unittest` entrypoints. `python -m unittest discover` still drove the live
sender. So the guard was half a guard for the rest of that day.

The canonical fuse now lives at the chokepoint (`dado_inbox_reasoner.is_test_run`,
consumed by `_try_send` and `send_clean`), and both watchers delegate to it.

THE TEST THAT MATTERS MOST IS THE NEGATIVE ONE. A false positive is far worse
than the bug being fixed: it would silently mute Dado's live alerter forever
while every suite stayed green. That is why the fuse keys on how the PROCESS was
launched and never on `"unittest" in sys.modules` - unittest.mock is imported
transitively by ordinary libraries.

This file is deliberately runnable BOTH ways, and must pass identically:
    python -m pytest test_send_fuse.py
    python -m unittest test_send_fuse -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WATCH_DIR = str(Path(__file__).resolve().parent)
sys.path.insert(0, WATCH_DIR)

import dado_inbox_reasoner as reasoner  # noqa: E402
import job_runner  # noqa: E402
import stall_tripwire  # noqa: E402

CRON_ARGV0 = r"C:\Users\TDI-service\AppData\Local\hermes\profiles\dado\scripts\stall_tripwire.py"


class FuseFiresForThisProcess(unittest.TestCase):
    """Whichever runner is executing this file, the fuse must already be live."""

    def test_the_fuse_is_live_in_this_very_process(self):
        self.assertTrue(
            reasoner.is_test_run(),
            "the fuse did not detect the runner executing this file - under "
            "pytest that means PYTEST_CURRENT_TEST regressed, under unittest it "
            "means the launch-path detection regressed, and either way the live "
            "sender is reachable from a test run again",
        )

    def test_both_watchers_agree_with_the_canonical_fuse(self):
        self.assertTrue(job_runner._test_run_suppressed())
        self.assertTrue(stall_tripwire._test_run_suppressed())

    def test_the_watchers_delegate_rather_than_keep_their_own_copy(self):
        """A re-copied guard is the original defect; pin the delegation."""
        with mock.patch.object(reasoner, "is_test_run", return_value=False):
            self.assertFalse(job_runner._test_run_suppressed())
            self.assertFalse(stall_tripwire._test_run_suppressed())


class NothingReachesTheWire(unittest.TestCase):
    def test_send_clean_never_shells_out(self):
        with mock.patch.object(reasoner.subprocess, "run") as ran:
            self.assertTrue(reasoner.send_clean("regression probe - must not send"))
        ran.assert_not_called()

    def test_try_send_never_shells_out(self):
        with mock.patch.object(reasoner.subprocess, "run") as ran:
            rc, err = reasoner._try_send("regression probe - must not send")
        ran.assert_not_called()
        self.assertEqual(rc, 0, "suppressed sends report ACCEPTED so callers' "
                                "persist-on-confirmed-send paths stay covered")

    def test_both_watcher_deliver_paths_are_inert(self):
        with mock.patch.object(reasoner.subprocess, "run") as ran:
            self.assertTrue(job_runner._deliver("regression probe - must not send"))
            self.assertTrue(stall_tripwire._deliver("regression probe - must not send"))
        ran.assert_not_called()


class TheFuseMustNotMuteProduction(unittest.TestCase):
    """The dangerous direction, proven in REAL separate processes.

    These cannot be done in-process. The fuse's load-bearing check is "are there
    unittest/pytest frames on the stack right now", and inside a test there
    always are - so an in-process probe that patches argv can only ever report
    True and would either fail forever or have to disable the very check it is
    meant to exercise. An earlier version of this class did exactly that and
    produced six confident failures that said nothing about production.

    Spawning the interpreter is slower and is the only honest way to ask "what
    would this return when cron runs it?".
    """

    PROBE = (
        "import sys\n"
        "sys.path.insert(0, r'{watch}')\n"
        "import dado_inbox_reasoner as r\n"
        "print('FUSE:', r.is_test_run())\n"
    )

    def _run_probe_named(self, filename):
        """Run the probe as a real process whose script is called `filename`."""
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / filename
            script.write_text(self.PROBE.format(watch=WATCH_DIR), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
            out = subprocess.run([sys.executable, str(script)], capture_output=True,
                                 text=True, timeout=120, env=env)
        self.assertIn("FUSE:", out.stdout, f"probe did not run: {out.stderr[-400:]}")
        return out.stdout.strip().rsplit("FUSE:", 1)[1].strip() == "True"

    def test_a_cron_invocation_is_NOT_mistaken_for_a_test(self):
        self.assertFalse(
            self._run_probe_named("stall_tripwire.py"),
            "`python stall_tripwire.py` was classified as a test run - that "
            "would silently mute Dado's live alerter while every suite stayed "
            "green, which is strictly worse than the bug this fuse fixes",
        )

    def test_the_other_watchers_are_not_muted_either(self):
        for name in ("job_runner.py", "dado_inbox_reasoner.py", "dado_lane_health.py",
                     "dado_delivery_watch.py", "dado_urgent_alert.py"):
            with self.subTest(script=name):
                self.assertFalse(self._run_probe_named(name))

    def test_running_a_test_file_directly_IS_detected(self):
        self.assertTrue(self._run_probe_named("test_watch_delivery.py"))

    def test_a_PROGRAMMATIC_runner_is_detected_even_from_stdin(self):
        """THE 2026-08-12 REGRESSION, pinned.

        A verification harness piped to `python` runs with argv[0] == "-" and
        __main__.__file__ == "<stdin>". Every launch-style signal says
        "production", the suite ran for real, and 14 live Telegram messages
        reached Rachad. Only a stack check catches this.
        """
        script = (
            "import sys, unittest\n"
            f"sys.path.insert(0, r'{WATCH_DIR}')\n"
            "import dado_inbox_reasoner as r\n"
            "seen = []\n"
            "class T(unittest.TestCase):\n"
            "    def test_x(self):\n"
            "        seen.append(r.is_test_run())\n"
            "import io\n"
            "unittest.TextTestRunner(verbosity=0, stream=io.StringIO()).run(\n"
            "    unittest.TestLoader().loadTestsFromTestCase(T))\n"
            "print('FUSE:', seen[0])\n"
        )
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        out = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                             text=True, timeout=120, env=env)
        self.assertIn("FUSE: True", out.stdout,
                      f"the stdin-harness hole is open again: {out.stdout}{out.stderr[-400:]}")

    def test_the_env_variable_alone_still_wins(self):
        """pytest sets it per-test even when argv looks like production."""
        with mock.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "x"}), \
                mock.patch.object(sys, "argv", [CRON_ARGV0]):
            self.assertTrue(reasoner.is_test_run())


if __name__ == "__main__":
    unittest.main(verbosity=2)
