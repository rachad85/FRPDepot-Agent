from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dado_followup_digest as digest
import dado_inbox_reasoner as inbox


class ClosedDraftSuppressionTests(unittest.TestCase):
    def test_closed_thread_is_suppressed_but_later_sent_activity_reopens_it(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            closed_path = Path(folder) / "closed.jsonl"
            closed_path.write_text(
                json.dumps({
                    "closed_at": "2026-07-27T12:14:42-04:00",
                    "conversation_id": "closed-thread",
                    "last_sent_at_close": "2026-06-05 00:50",
                }) + "\n",
                encoding="utf-8",
            )
            data = {
                "overdue_count": 3,
                "overdue": [
                    {"conversation_id": "closed-thread", "last_sent": "2026-06-05 00:50"},
                    {"conversation_id": "closed-thread", "last_sent": "2026-07-28 10:00"},
                    {"conversation_id": "open-thread", "last_sent": "2026-06-01 09:00"},
                ],
                "not_yet_due": [
                    {"conversation_id": "closed-thread", "last_sent": "2026-06-05 00:50"},
                ],
                "chase_draft_waiting": [],
            }
            with patch.object(digest, "CLOSED_TASKS", closed_path):
                filtered, suppressed = digest.suppress_closed_task_threads(data)

            self.assertEqual(suppressed, 1)
            self.assertEqual(filtered["overdue_count"], 2)
            self.assertEqual(
                [row["last_sent"] for row in filtered["overdue"]],
                ["2026-07-28 10:00", "2026-06-01 09:00"],
            )
            self.assertEqual(filtered["not_yet_due"], [])
            self.assertEqual(filtered["closed_task_suppressed_count"], 1)

    def test_inbox_watch_uses_the_same_closed_task_suppression(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            closed_path = Path(folder) / "closed.jsonl"
            closed_path.write_text(
                json.dumps({
                    "closed_at": "2026-07-27T12:14:42-04:00",
                    "conversation_id": "closed-thread",
                    "last_sent_at_close": "2026-06-05 00:50",
                }) + "\n",
                encoding="utf-8",
            )
            raw = json.dumps({
                "overdue_count": 2,
                "overdue": [
                    {"conversation_id": "closed-thread", "last_sent": "2026-06-05 00:50"},
                    {"conversation_id": "open-thread", "last_sent": "2026-06-01 09:00"},
                ],
                "not_yet_due": [],
                "chase_draft_waiting": [],
            })
            with patch.object(inbox, "CLOSED_TASKS", closed_path):
                filtered = json.loads(inbox.suppress_closed_waiting_on_them(raw))

            self.assertEqual(filtered["overdue_count"], 1)
            self.assertEqual(filtered["overdue"][0]["conversation_id"], "open-thread")
            self.assertEqual(filtered["closed_task_suppressed_count"], 1)


if __name__ == "__main__":
    unittest.main()
