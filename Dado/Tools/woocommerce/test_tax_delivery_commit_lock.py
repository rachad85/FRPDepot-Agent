"""Thread/process/dead-holder tests for the tax-delivery global named mutex."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest

COMMON = Path(__file__).resolve().parents[1] / "common"
sys.path.insert(0, str(COMMON))
import tax_delivery_commit_lock as lock  # noqa: E402

MODULE_PATH = str(Path(lock.__file__).resolve())


def child_source(lock_dir: str, body: str) -> str:
    header = textwrap.dedent(f"""
        import importlib.util, time
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("tax_delivery_commit_lock", {MODULE_PATH!r})
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.LOCK_DIR = Path({lock_dir!r})
    """)
    return header + textwrap.dedent(body)


class GlobalMutexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original = lock.LOCK_DIR
        lock.LOCK_DIR = Path(self.tmp.name)
        self.addCleanup(self.restore)

    def restore(self):
        lock.LOCK_DIR = self.original
        lock._REENTRY.clear()

    def test_descriptor_and_distinct_mutex_identity(self):
        path = lock._descriptor_path()
        self.assertIn("Woo-Tax-Delivery-Commit", lock._mutex_name())
        self.assertNotIn("UI-Lane", lock._mutex_name())
        with lock.tax_delivery_commit_lock(purpose="unit commit"):
            self.assertTrue(path.exists())
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["pid"], os.getpid())
            self.assertEqual(record["purpose"], "unit commit")
        self.assertFalse(path.exists())

    def test_second_thread_is_refused(self):
        entered = threading.Event(); release = threading.Event(); outcome = {}
        def holder():
            with lock.tax_delivery_commit_lock(purpose="thread holder"):
                entered.set(); release.wait(15)
        def contender():
            entered.wait(15)
            try:
                with lock.tax_delivery_commit_lock(purpose="thread contender", wait_seconds=0):
                    outcome["result"] = "entered"
            except lock.TaxDeliveryCommitBusy as exc:
                outcome["result"] = "refused"; outcome["message"] = str(exc)
        first = threading.Thread(target=holder); second = threading.Thread(target=contender)
        first.start(); second.start(); second.join(30); release.set(); first.join(30)
        self.assertEqual(outcome.get("result"), "refused")
        self.assertIn("no plan was permanently locked", outcome.get("message", ""))

    def test_second_process_is_refused(self):
        ready = Path(self.tmp.name) / "ready"
        child = subprocess.Popen([
            sys.executable, "-c", child_source(self.tmp.name, f"""
with mod.tax_delivery_commit_lock(purpose="child holder"):
    Path({str(ready)!r}).write_text("held")
    time.sleep(30)
""")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop_child():
            if child.poll() is None:
                child.kill()
            child.wait(timeout=30)
        self.addCleanup(stop_child)
        for _ in range(300):
            if ready.exists(): break
            time.sleep(0.05)
        self.assertTrue(ready.exists(), "child did not acquire mutex")
        with self.assertRaises(lock.TaxDeliveryCommitBusy):
            with lock.tax_delivery_commit_lock(purpose="parent contender", wait_seconds=0):
                self.fail("second process entered")

    def test_killed_holder_cannot_orphan_mutex(self):
        ready = Path(self.tmp.name) / "dead-ready"
        child = subprocess.Popen([
            sys.executable, "-c", child_source(self.tmp.name, f"""
with mod.tax_delivery_commit_lock(purpose="holder to kill"):
    Path({str(ready)!r}).write_text("held")
    time.sleep(120)
""")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(300):
            if ready.exists(): break
            time.sleep(0.05)
        self.assertTrue(ready.exists(), "child did not acquire mutex")
        child.kill(); child.wait(timeout=30)
        self.assertTrue(lock._descriptor_path().exists())  # informational stale file
        with lock.tax_delivery_commit_lock(purpose="after kill", wait_seconds=10):
            record = json.loads(lock._descriptor_path().read_text(encoding="utf-8"))
            self.assertEqual(record["pid"], os.getpid())

    def test_same_thread_is_reentrant_and_exception_releases(self):
        with self.assertRaises(ZeroDivisionError):
            with lock.tax_delivery_commit_lock(purpose="outer"):
                with lock.tax_delivery_commit_lock(purpose="inner", wait_seconds=0):
                    self.assertTrue(lock._descriptor_path().exists())
                raise ZeroDivisionError
        self.assertFalse(lock._descriptor_path().exists())
        with lock.tax_delivery_commit_lock(purpose="next", wait_seconds=0):
            pass

    def test_source_uses_kernel_mutex_without_file_reclaim(self):
        source = Path(lock.__file__).read_text(encoding="utf-8")
        for required in ("CreateMutexW", "WaitForSingleObject", "ReleaseMutex"):
            self.assertIn(required, source)
        for forbidden in ("O_EXCL", "_reclaim_if_dead", "HARD_STALE_SECONDS"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
