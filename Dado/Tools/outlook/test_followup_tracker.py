"""Guards on "did we already chase this thread?" (backlog B-11).

chase_draft_pending used to be `bool([x for x in msgs if x.get("isDraft")])` --
"does this conversation contain ANY draft". That is a much wider question than
the one being asked. It also matched an ordinary reply draft, one of Rachad's
own half-typed messages, and a chase he read and rejected (a deleted draft still
comes back from /me/messages, which spans every folder).

A matching thread was dropped from `overdue` and from overdue_count, and the
digest prompt is told to ignore already_chased -- so the thread left the watch
list permanently and could go quiet forever. Measured 2026-07-24: a CAD 9,936
budgetary quote to Nashtec, one working day old and never chased, was already
excluded on exactly this basis.

It is now driven by chase_log.jsonl, which only records reply-all drafts this
tree created, and it expires after CHASE_QUIET_DAYS.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import datetime
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import outlook_check as check  # noqa: E402
import outlook_tool as tool  # noqa: E402


def iso(days_ago: float) -> str:
    when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return when.isoformat()


class RecentChasesTests(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.log = Path(self._folder.name) / "chase_log.jsonl"
        patcher = patch.object(tool, "CHASE_LOG", self.log)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, rows):
        self.log.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )

    def test_no_log_file_means_nothing_has_been_chased(self):
        self.assertEqual(check.recent_chases(), {})

    def test_a_fresh_chase_suppresses_and_a_stale_one_does_not(self):
        self.write([
            {"ts": iso(2), "conversation_id": "fresh", "draft_id": "d1"},
            {"ts": iso(check.CHASE_QUIET_DAYS + 1), "conversation_id": "stale", "draft_id": "d2"},
        ])
        found = check.recent_chases()
        self.assertIn("fresh", found)
        self.assertNotIn("stale", found,
                         "a chase older than the quiet period must let the thread return")

    def test_latest_chase_wins_when_a_thread_was_chased_twice(self):
        self.write([
            {"ts": iso(6), "conversation_id": "c", "draft_id": "old"},
            {"ts": iso(1), "conversation_id": "c", "draft_id": "new"},
        ])
        self.assertEqual(check.recent_chases()["c"][:10], iso(1)[:10])

    def test_a_corrupt_line_does_not_blind_the_tracker(self):
        self.log.write_text(
            "not json at all\n"
            + json.dumps({"ts": "nonsense", "conversation_id": "bad"}) + "\n"
            + json.dumps({"ts": iso(1), "conversation_id": "good", "draft_id": "d"}) + "\n",
            encoding="utf-8",
        )
        found = check.recent_chases()
        self.assertEqual(list(found), ["good"])

    def test_a_naive_timestamp_is_read_as_utc_rather_than_crashing(self):
        naive = (datetime.datetime.now(datetime.timezone.utc)
                 - datetime.timedelta(days=1)).replace(tzinfo=None).isoformat()
        self.write([{"ts": naive, "conversation_id": "c", "draft_id": "d"}])
        self.assertIn("c", check.recent_chases())

    def test_record_chase_round_trips_into_recent_chases(self):
        """The two modules must agree on the format; this is the seam."""
        tool.record_chase("conv-abc", "draft-1", "RE: Fittings - RFQ")
        self.assertIn("conv-abc", check.recent_chases())


class WaitingOnThemSuppressionTests(unittest.TestCase):
    """The regression itself, through show_waiting_on_them."""

    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.log = Path(self._folder.name) / "chase_log.jsonl"
        patcher = patch.object(tool, "CHASE_LOG", self.log)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, thread_messages):
        """Runs the tracker over one conversation and returns the parsed JSON."""
        sent_page = {"value": [{
            "conversationId": "conv-1",
            "subject": "RE: New submission from Contact",
            "bodyPreview": "Please see our budgetary pricing below. CAD 9,936.00",
            "sentDateTime": thread_messages[-1]["sentDateTime"],
            "toRecipients": [{"emailAddress": {"address": "brianb@nashtecllc.com"}}],
        }]}
        with (
            patch.object(check, "my_address", return_value="info@frpdepots.com"),
            patch.object(check, "get", return_value=sent_page),
            patch.object(check, "_conversation", return_value=thread_messages),
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                check.show_waiting_on_them("token", 60)
        return json.loads(out.getvalue())

    def _thread(self, extra=None):
        """Their old mail, then ours -- and optionally an unrelated draft."""
        old = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ours = (datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(days=21)).strftime("%Y-%m-%dT%H:%M:%SZ")
        msgs = [
            {"id": "m1", "isDraft": False,
             "from": {"emailAddress": {"address": "brianb@nashtecllc.com"}},
             "toRecipients": [{"emailAddress": {"address": "info@frpdepots.com"}}],
             "sentDateTime": old},
            {"id": "m2", "isDraft": False,
             "from": {"emailAddress": {"address": "info@frpdepots.com"}},
             "toRecipients": [{"emailAddress": {"address": "brianb@nashtecllc.com"}}],
             "sentDateTime": ours},
        ]
        if extra:
            msgs.append(extra)
        return msgs

    def test_an_unrelated_draft_no_longer_hides_a_live_money_thread(self):
        unrelated_draft = {
            "id": "m3", "isDraft": True,
            "from": {"emailAddress": {"address": "info@frpdepots.com"}},
            "toRecipients": [{"emailAddress": {"address": "brianb@nashtecllc.com"}}],
            "sentDateTime": (datetime.datetime.now(datetime.timezone.utc)
                             ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        result = self._run(self._thread(unrelated_draft))
        self.assertEqual(result["overdue_count"], 1,
                         "a draft we did not create must not remove the thread")
        self.assertEqual(result["already_chased"], [])
        item = result["overdue"][0]
        self.assertFalse(item["chase_draft_pending"])
        self.assertIsNone(item["chased_on"])
        self.assertEqual(item["drafts_in_thread"], 1,
                         "the draft is still reported, just not load-bearing")

    def test_a_chase_we_created_does_suppress_it(self):
        tool.record_chase("conv-1", "draft-1", "RE: New submission from Contact")
        result = self._run(self._thread())
        self.assertEqual(result["overdue_count"], 0)
        self.assertEqual(len(result["already_chased"]), 1)
        self.assertTrue(result["already_chased"][0]["chase_draft_pending"])
        self.assertIsNotNone(result["already_chased"][0]["chased_on"])

    def test_the_thread_returns_once_the_chase_goes_stale(self):
        self.log.write_text(json.dumps({
            "ts": iso(check.CHASE_QUIET_DAYS + 1),
            "conversation_id": "conv-1", "draft_id": "d",
        }) + "\n", encoding="utf-8")
        result = self._run(self._thread())
        self.assertEqual(result["overdue_count"], 1,
                         "an unsent chase older than the quiet period must resurface")


if __name__ == "__main__":
    unittest.main()
