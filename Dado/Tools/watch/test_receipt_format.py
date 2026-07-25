"""Every receipts.jsonl writer must agree on one timestamp format (B-30).

receipts.jsonl is written by nine different modules across the tree. On
2026-07-25 it held three formats: 4,807 UTC-aware lines, 8 with a local offset,
and 13 with no timezone at all. Comparing a naive datetime to an aware one
raises TypeError in Python, so any freshness or windowing query over the log
either crashes or silently skips those rows -- it broke a freshness query during
the sweep that found this.

The naive writer was job_runner. This test is an AST/source check across every
writer rather than a runtime one, because the failure is in how the timestamp is
CONSTRUCTED: a runtime test would need each module's real dependencies, and
several need live credentials to import.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest

TOOLS = Path(__file__).resolve().parent.parent

# Every module that appends to receipts.jsonl.
WRITERS = [
    "watch/job_runner.py",
    "outlook/outlook_tool.py",
    "google/google_backfill.py",
    "google/google_indexer.py",
    "google/google_rescreen.py",
    "google/google_extended_auth.py",
    "google/frp_search_console_report.py",
    "woocommerce/woocommerce_common.py",
    "zoho/zoho_tool.py",
]

# The timestamp handed to a receipt must be built from an AWARE utc call.
AWARE_UTC = re.compile(
    r"datetime\.now\(\s*(?:dt\.)?timezone\.utc\s*\)"      # datetime.now(timezone.utc)
    r"|dt\.datetime\.now\(\s*dt\.timezone\.utc\s*\)"      # dt.datetime.now(dt.timezone.utc)
    r"|now\(\s*\)\.astimezone\(",                          # explicitly zoned
)
NAIVE_NOW = re.compile(r"datetime\.now\(\s*\)")


class ReceiptTimestampTests(unittest.TestCase):
    def test_every_writer_exists(self):
        for rel in WRITERS:
            with self.subTest(module=rel):
                self.assertTrue((TOOLS / rel).exists(), f"{rel} moved or was renamed")

    def test_no_writer_builds_a_receipt_timestamp_from_a_naive_now(self):
        for rel in WRITERS:
            with self.subTest(module=rel):
                text = (TOOLS / rel).read_text(encoding="utf-8")
                # Find the line that actually writes the "ts" field.
                ts_lines = [ln for ln in text.splitlines() if '"ts"' in ln]
                self.assertTrue(ts_lines, f"{rel} no longer writes a ts field")
                for line in ts_lines:
                    self.assertNotRegex(
                        line, NAIVE_NOW,
                        f"{rel}: receipt timestamp must carry a timezone. A naive "
                        f"value cannot be compared with the aware ones already in "
                        f"the log without raising TypeError.")

    def test_job_runner_receipt_stamp_is_aware_utc(self):
        """The one writer that was actually naive.

        Compares the function's CODE, not its source text: the docstring quotes
        the old naive expression on purpose, and a prose mention must not read as
        a defect.
        """
        tree = ast.parse((TOOLS / "watch/job_runner.py").read_text(encoding="utf-8"))
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "receipt_stamp"), None)
        self.assertIsNotNone(fn, "receipt_stamp() is the named entry point")
        body = [n for n in fn.body if not (isinstance(n, ast.Expr)
                                           and isinstance(n.value, ast.Constant)
                                           and isinstance(n.value.value, str))]
        code = "\n".join(ast.unparse(n) for n in body)
        self.assertRegex(code, AWARE_UTC)
        self.assertNotRegex(code, NAIVE_NOW)

    def test_job_records_may_still_use_local_wall_clock(self):
        """Deliberate: job started/finished are read by a human, in their own file."""
        text = (TOOLS / "watch/job_runner.py").read_text(encoding="utf-8")
        self.assertIn("def stamp()", text,
                      "the local wall-clock helper is still used for job records")


if __name__ == "__main__":
    unittest.main()
