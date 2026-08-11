"""Independent tests for the authenticated-browser lane lock.

The lock exists because Dado's Discord lane made her turns genuinely
concurrent, and both lanes share ONE authenticated Edge window per service.
These tests hold it to the properties that make it worth having:

  1. It is exclusive ACROSS THREADS and ACROSS PROCESSES -- the two shapes the
     two lanes actually take (the gateway runs turns as threads; the tools run
     as subprocesses).
  2. It refuses BEFORE doing anything, so a refusal never burns a plan.
  3. It cannot ORPHAN. A holder killed outright must not wedge the browser
     forever -- this is the failure the first, file-based implementation had.
  4. Nesting on one thread does not release the browser mid-command.

Note what is deliberately NOT tested any more: writing a descriptor JSON file
by hand no longer holds the lane. The descriptor is informational; the kernel
mutex is the exclusion. A test that faked a holder with a file would be
testing a mechanism that no longer grants anything.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

import ui_lane_lock  # noqa: E402

MODULE_PATH = str(Path(ui_lane_lock.__file__).resolve())


def child_source(lock_dir: str, lane: str, body: str) -> str:
    """A standalone child process that shares this test's LOCK_DIR.

    The header is dedented on its own and the body appended afterwards: a body
    written at column 0 would otherwise make textwrap.dedent see a common prefix
    of "" and leave the header indented, which is an IndentationError in the
    child and shows up here only as "child never acquired the lane".
    """
    header = textwrap.dedent(f"""
        import importlib.util, sys, time
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("ui_lane_lock", r"{MODULE_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.LOCK_DIR = Path(r"{lock_dir}")
        lane = "{lane}"
    """)
    return header + textwrap.dedent(body)


class LaneLockTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.lock_dir = Path(self._tmp.name)
        self._original_dir = ui_lane_lock.LOCK_DIR
        ui_lane_lock.LOCK_DIR = self.lock_dir
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        ui_lane_lock.LOCK_DIR = self._original_dir
        ui_lane_lock._REENTRY.clear()
        self._tmp.cleanup()

    def descriptor(self, lane: str = "zoho") -> Path:
        return self.lock_dir / f"{lane}.lock.json"

    # -- 1. exclusivity ---------------------------------------------------

    def test_descriptor_is_written_while_held_and_removed_after(self):
        path = self.descriptor()
        self.assertFalse(path.exists())
        with ui_lane_lock.ui_browser_lock("zoho", purpose="unit test"):
            self.assertTrue(path.exists())
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["pid"], os.getpid())
            self.assertEqual(record["cdp_port"], 9228)
            self.assertFalse(record["took_over_from_dead_holder"])
        self.assertFalse(path.exists())

    def test_another_thread_is_refused_while_held(self):
        """The shape the gateway actually produces: two lanes, two threads."""
        outcome: dict[str, str] = {}
        entered = threading.Event()
        release = threading.Event()

        def holder() -> None:
            with ui_lane_lock.ui_browser_lock("zoho", purpose="lane one"):
                entered.set()
                release.wait(timeout=15)

        def other_lane() -> None:
            entered.wait(timeout=15)
            try:
                with ui_lane_lock.ui_browser_lock("zoho", purpose="lane two", wait_seconds=0):
                    outcome["result"] = "GOT IN - the lock did not hold"
            except ui_lane_lock.UiLaneBusy as exc:
                outcome["result"] = "refused"
                outcome["message"] = str(exc)

        first = threading.Thread(target=holder)
        second = threading.Thread(target=other_lane)
        first.start()
        second.start()
        second.join(timeout=30)
        release.set()
        first.join(timeout=30)

        self.assertEqual(outcome.get("result"), "refused")
        self.assertIn("lane one", outcome.get("message", ""))
        self.assertIn("nothing was changed", outcome.get("message", "").lower())

    def test_another_process_is_refused_while_held(self):
        """The shape the tools actually take: each run is its own subprocess."""
        ready = self.lock_dir / "child-ready"
        child = subprocess.Popen(
            [sys.executable, "-c", child_source(str(self.lock_dir), "zoho", f"""
with mod.ui_browser_lock(lane, purpose="the other lane"):
    Path(r"{ready}").write_text("held")
    time.sleep(12)
""")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(child.kill)
        for _ in range(200):
            if ready.exists():
                break
            time.sleep(0.05)
        self.assertTrue(ready.exists(), "child never acquired the lane")

        with self.assertRaises(ui_lane_lock.UiLaneBusy) as caught:
            with ui_lane_lock.ui_browser_lock("zoho", purpose="us", wait_seconds=0):
                self.fail("a second process must not get in")
        self.assertIn("the other lane", str(caught.exception))

    def test_the_two_browsers_are_independent_lanes(self):
        with ui_lane_lock.ui_browser_lock("zoho", purpose="zoho work"):
            # A busy Zoho browser must not block a WordPress write; they are
            # separate Edge windows on separate CDP ports.
            def other() -> None:
                with ui_lane_lock.ui_browser_lock("wordpress", purpose="wp work", wait_seconds=2):
                    pass
            thread = threading.Thread(target=other)
            thread.start()
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive())

    def test_unknown_lane_is_rejected(self):
        with self.assertRaises(ui_lane_lock.UiLaneLockError):
            with ui_lane_lock.ui_browser_lock("outlook", purpose="not a browser lane"):
                self.fail("unreachable")

    # -- 2. refusal is free, and bounded ----------------------------------

    def test_wait_is_bounded_and_does_not_hang(self):
        entered = threading.Event()
        release = threading.Event()

        def holder() -> None:
            with ui_lane_lock.ui_browser_lock("zoho", purpose="slow write"):
                entered.set()
                release.wait(timeout=20)

        thread = threading.Thread(target=holder)
        thread.start()
        entered.wait(timeout=15)
        started = time.monotonic()
        with self.assertRaises(ui_lane_lock.UiLaneBusy):
            with ui_lane_lock.ui_browser_lock("zoho", purpose="waiter", wait_seconds=2):
                self.fail("unreachable")
        elapsed = time.monotonic() - started
        release.set()
        thread.join(timeout=20)
        self.assertGreaterEqual(elapsed, 1.5)
        self.assertLess(elapsed, 20)

    # -- 3. it cannot orphan ----------------------------------------------

    def test_a_killed_holder_does_not_wedge_the_lane_forever(self):
        """The defect the file-based implementation had.

        A process killed between claim and release left a lock whose PID could
        be reused, making the lane permanently unclaimable. The kernel releases
        an abandoned mutex, so this must now just work.
        """
        ready = self.lock_dir / "child-held"
        child = subprocess.Popen(
            [sys.executable, "-c", child_source(str(self.lock_dir), "zoho", f"""
with mod.ui_browser_lock(lane, purpose="about to be killed"):
    Path(r"{ready}").write_text("held")
    time.sleep(120)
""")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(300):
            if ready.exists():
                break
            time.sleep(0.05)
        self.assertTrue(ready.exists(), "child never acquired the lane")

        child.kill()
        child.wait(timeout=30)

        # The descriptor file is still on disk -- it is informational and the
        # killed process never got to remove it. That must not deny the lane.
        self.assertTrue(self.descriptor().exists())

        with ui_lane_lock.ui_browser_lock("zoho", purpose="after the kill", wait_seconds=10):
            record = json.loads(self.descriptor().read_text(encoding="utf-8"))
            self.assertEqual(record["pid"], os.getpid())

        # Deliberately NOT asserted: that took_over_from_dead_holder is True.
        # WAIT_ABANDONED is reported only when the kernel object OUTLIVES its
        # dead owner, i.e. when some other process still holds a handle. Here
        # the killed child held the only handle, so the object was destroyed
        # with it and we simply created a fresh one. Both routes give the
        # property that matters and is asserted above: a hard-killed holder
        # cannot wedge the lane. The flag records which route happened.

    def test_a_stale_descriptor_alone_does_not_hold_the_lane(self):
        """The descriptor is informational; only the kernel mutex excludes.

        This is the direct inverse of the old file-based design, where writing
        this file WAS the lock and a leftover one could deny the browser.
        """
        path = self.descriptor()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "pid": os.getpid(), "nonce": "not-ours", "lane": "zoho",
            "cdp_port": 9228, "purpose": "a leftover from a killed run",
            "acquired_utc": "2026-08-10T00:00:00+00:00",
        }), encoding="utf-8")

        with ui_lane_lock.ui_browser_lock("zoho", purpose="unimpeded", wait_seconds=0):
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["purpose"], "unimpeded")

    def test_lock_is_released_even_when_the_body_raises(self):
        with self.assertRaises(ZeroDivisionError):
            with ui_lane_lock.ui_browser_lock("zoho", purpose="failing write"):
                raise ZeroDivisionError
        self.assertFalse(self.descriptor().exists())
        # And the lane is immediately available again.
        with ui_lane_lock.ui_browser_lock("zoho", purpose="next writer", wait_seconds=2):
            pass

    # -- 4. re-entrancy ---------------------------------------------------

    def test_the_same_thread_may_nest_without_deadlocking(self):
        # WordPress activation wraps the whole command and its emergency
        # rollback opens a second admin session inside it.
        with ui_lane_lock.ui_browser_lock("wordpress", purpose="outer command"):
            with ui_lane_lock.ui_browser_lock("wordpress", purpose="inner rollback", wait_seconds=0):
                self.assertTrue(self.descriptor("wordpress").exists())
            # The inner exit must NOT release the browser mid-command.
            self.assertTrue(self.descriptor("wordpress").exists())
        self.assertFalse(self.descriptor("wordpress").exists())

    def test_nesting_keeps_the_outer_purpose_on_record(self):
        with ui_lane_lock.ui_browser_lock("zoho", purpose="outer command"):
            with ui_lane_lock.ui_browser_lock("zoho", purpose="inner helper", wait_seconds=0):
                record = json.loads(self.descriptor().read_text(encoding="utf-8"))
                self.assertEqual(record["purpose"], "outer command")

    def test_a_nested_hold_still_excludes_another_thread(self):
        """Re-entrancy must not become a hole in the exclusion."""
        outcome: dict[str, str] = {}
        entered = threading.Event()
        release = threading.Event()

        def holder() -> None:
            with ui_lane_lock.ui_browser_lock("zoho", purpose="outer"):
                with ui_lane_lock.ui_browser_lock("zoho", purpose="inner", wait_seconds=0):
                    entered.set()
                    release.wait(timeout=15)

        def other_lane() -> None:
            entered.wait(timeout=15)
            try:
                with ui_lane_lock.ui_browser_lock("zoho", purpose="other", wait_seconds=0):
                    outcome["result"] = "GOT IN"
            except ui_lane_lock.UiLaneBusy:
                outcome["result"] = "refused"

        first = threading.Thread(target=holder)
        second = threading.Thread(target=other_lane)
        first.start()
        second.start()
        second.join(timeout=30)
        release.set()
        first.join(timeout=30)
        self.assertEqual(outcome.get("result"), "refused")

    # -- status -----------------------------------------------------------

    def test_lane_status_reports_free_and_held(self):
        self.assertEqual(ui_lane_lock.lane_status("zoho")["held"], False)
        with ui_lane_lock.ui_browser_lock("zoho", purpose="status check"):
            status = ui_lane_lock.lane_status("zoho")
            self.assertTrue(status["held"])
            self.assertTrue(status["alive"])
            self.assertFalse(status["stale"])
            self.assertEqual(status["purpose"], "status check")


class LaneLockSourceTestCase(unittest.TestCase):
    """Properties of the module itself that must not regress.

    These read CODE, not prose: the module's docstring necessarily names the
    very things the code must not do, so a raw substring scan would fail on its
    own explanation. Comments and string literals are stripped first.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = Path(ui_lane_lock.__file__).read_text(encoding="utf-8")
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

    def test_exclusion_is_the_kernel_mutex(self):
        for required in ("CreateMutexW", "WaitForSingleObject", "ReleaseMutex"):
            self.assertIn(required, self.code)

    def test_there_is_no_reclaim_path_left_to_race(self):
        """The file-based reclaim was the source of two reproduced defects."""
        for gone in ("O_EXCL", "_reclaim_if_dead", "HARD_STALE_SECONDS"):
            self.assertNotIn(gone, self.code,
                             f"{gone} belonged to the racy file lock and must stay gone")

    def test_the_mutex_name_is_scoped_to_the_lock_dir(self):
        # Otherwise a test run would contend with the live browser lane.
        self.assertIn("_mutex_name", self.code)
        first = ui_lane_lock._mutex_name("zoho")
        original = ui_lane_lock.LOCK_DIR
        try:
            ui_lane_lock.LOCK_DIR = Path(tempfile.gettempdir()) / "somewhere-else"
            self.assertNotEqual(first, ui_lane_lock._mutex_name("zoho"))
        finally:
            ui_lane_lock.LOCK_DIR = original

    def test_it_has_no_network_and_no_browser_control_of_its_own(self):
        for forbidden in ("urlopen", "connect_over_cdp", "requests", "subprocess", "socket"):
            self.assertNotIn(forbidden, self.code, f"{forbidden} has no business here")

    def test_it_never_kills_the_holding_process(self):
        for forbidden in ("taskkill", "TerminateProcess", "kill"):
            self.assertNotIn(forbidden, self.code)

    def test_both_authenticated_browsers_are_covered(self):
        self.assertEqual(ui_lane_lock.LANES, {"zoho": 9228, "wordpress": 9229})


if __name__ == "__main__":
    unittest.main(verbosity=2)
