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

    def test_only_an_explicit_chase_is_logged(self):
        """Dado's review, 2026-07-25.

        record_chase originally fired on EVERY successful reply-all draft, so an
        ordinary customer reply would suppress that thread from follow-up
        monitoring for CHASE_QUIET_DAYS -- reintroducing, in narrower form, the
        exact bug B-11 removed. Only a draft the caller declares a chase counts.
        """
        source = (Path(__file__).resolve().parent / "outlook_tool.py").read_text(
            encoding="utf-8")
        self.assertIn('if bool(draft_input.get("is_chase")):', source)
        # The guard must sit immediately before the call, not somewhere else.
        guard = source.index('if bool(draft_input.get("is_chase")):')
        call = source.index("record_chase(conversation_id", guard)
        between = source[guard:call]
        self.assertNotIn("\n    ", between.rstrip(),
                         "record_chase must be inside the is_chase guard")
        self.assertIn('"--chase", action="store_true"', source,
                      "the flag must exist for the digest to pass")


class WaitingOnThemSuppressionTests(unittest.TestCase):
    """The regression itself, through show_waiting_on_them."""

    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.log = Path(self._folder.name) / "chase_log.jsonl"
        self.watch = Path(self._folder.name) / "followup_watch.json"
        # BOTH must be redirected: show_waiting_on_them reads the chase log and
        # WRITES the follow-up watch. A test run must never leave real threads
        # registered in operational state.
        for target, attr, value in ((tool, "CHASE_LOG", self.log),
                                    (check, "FOLLOWUP_WATCH", self.watch)):
            patcher = patch.object(target, attr, value)
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
        self.assertEqual(result["chase_draft_waiting"], [])
        item = result["overdue"][0]
        self.assertFalse(item["chase_draft_pending"])
        self.assertIsNone(item["chase_drafted_on"])
        self.assertEqual(item["drafts_in_thread"], 1,
                         "the draft is still reported, just not load-bearing")

    def test_a_chase_we_created_does_suppress_it(self):
        tool.record_chase("conv-1", "draft-1", "RE: New submission from Contact")
        result = self._run(self._thread())
        self.assertEqual(result["overdue_count"], 0)
        self.assertEqual(len(result["chase_draft_waiting"]), 1)
        self.assertTrue(result["chase_draft_waiting"][0]["chase_draft_pending"])
        self.assertIsNotNone(result["chase_draft_waiting"][0]["chase_drafted_on"])

    def test_the_thread_returns_once_the_chase_goes_stale(self):
        self.log.write_text(json.dumps({
            "ts": iso(check.CHASE_QUIET_DAYS + 1),
            "conversation_id": "conv-1", "draft_id": "d",
        }) + "\n", encoding="utf-8")
        result = self._run(self._thread())
        self.assertEqual(result["overdue_count"], 1,
                         "an unsent chase older than the quiet period must resurface")


class BusinessDayClockTests(unittest.TestCase):
    """B-14: the wait clock must be measured in Rachad's working days."""

    def test_two_mails_sent_the_same_eastern_monday_agree(self):
        # 15:30 and 21:30 ET on 2026-07-20. The second is 2026-07-21 in UTC, so
        # the old UTC-date version reported a whole working day less for a mail
        # sent SIX HOURS LATER.
        afternoon = check.business_days_since("2026-07-20T19:30:00Z")
        evening = check.business_days_since("2026-07-21T01:30:00Z")
        self.assertEqual(afternoon, evening)

    def test_weekends_are_not_counted_as_silence(self):
        friday = check.business_days_since("2026-07-17T14:00:00Z")
        monday = check.business_days_since("2026-07-20T14:00:00Z")
        self.assertEqual(friday - monday, 1, "Fri->Mon is one working day, not three")

    def test_a_naive_timestamp_does_not_crash_the_clock(self):
        self.assertIsInstance(check.business_days_since("2026-07-20T19:30:00"), int)

    def test_unparseable_input_is_zero_not_an_exception(self):
        self.assertEqual(check.business_days_since("not a date"), 0)


class ClassificationTests(unittest.TestCase):
    """B-16: the category sets the threshold, so it must read the right text."""

    def test_a_quote_numbered_subject_outranks_payment_words(self):
        self.assertEqual(
            check.classify_thread("Quote QT-000099 for FRP pipe", "Deposit invoice attached"),
            "rfq_quote", "a quote must wait 5 working days, not payment's 7")

    def test_quoted_history_does_not_set_the_category(self):
        self.assertEqual(
            check.classify_thread(
                "Re: Manway covers",
                "Any update?\n-----Original Message-----\nFrom: x@y.com\n"
                "Invoice payment overdue"),
            "general")

    def test_ordinary_classification_is_unchanged(self):
        self.assertEqual(check.classify_thread("Invoice INV-000040 outstanding", ""), "payment")
        self.assertEqual(check.classify_thread("RFQ for elbows", ""), "rfq_quote")
        self.assertEqual(check.classify_thread("Shipping address", ""), "general")

    def test_strip_quoted_keeps_only_his_own_text(self):
        self.assertEqual(
            check.strip_quoted("Please advise.\nFrom: someone@x.com\nold stuff").strip(),
            "Please advise.")


class TrackerResilienceTests(unittest.TestCase):
    """B-13 and B-15, plus the automated-root half of B-16."""

    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        for target, attr, value in (
            (tool, "CHASE_LOG", Path(self._folder.name) / "chase_log.jsonl"),
            (check, "FOLLOWUP_WATCH", Path(self._folder.name) / "followup_watch.json"),
        ):
            patcher = patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_an_unresolvable_mailbox_exits_nonzero_instead_of_reporting_all_clear(self):
        with patch.object(check, "my_address", return_value=""):
            out = io.StringIO()
            with self.assertRaises(SystemExit) as caught, redirect_stdout(out):
                check.show_waiting_on_them("token", 60)
        self.assertNotEqual(caught.exception.code, 0)
        self.assertIn("error", json.loads(out.getvalue()))

    def test_sent_paging_follows_next_link_and_reports_a_capped_scan(self):
        page = {"value": [{"conversationId": "c1"}], "@odata.nextLink": "https://g/v1.0/next"}
        with patch.object(check, "get", return_value=page):
            messages, truncated = check._all_sent_since("token", "2026-01-01T00:00:00Z")
        self.assertTrue(truncated, "an endlessly paging scan must admit it was capped")
        self.assertEqual(len(messages), check.SENT_PAGE_LIMIT)

    def test_sent_paging_stops_cleanly_without_a_next_link(self):
        page = {"value": [{"conversationId": "c1"}, {"conversationId": "c2"}]}
        with patch.object(check, "get", return_value=page):
            messages, truncated = check._all_sent_since("token", "2026-01-01T00:00:00Z")
        self.assertFalse(truncated)
        self.assertEqual(len(messages), 2)

    def _bank_notice_run(self, thread, subject):
        ours = thread[-1]["sentDateTime"]
        sent_page = {"value": [{
            "conversationId": "conv-bank", "subject": subject,
            "bodyPreview": "You received a deposit", "sentDateTime": ours,
            "toRecipients": [{"emailAddress": {"address": "someone@example.com"}}],
        }]}
        with (
            patch.object(check, "my_address", return_value="info@frpdepots.com"),
            patch.object(check, "get", return_value=sent_page),
            patch.object(check, "_conversation", return_value=thread),
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                check.show_waiting_on_them("token", 60)
        return json.loads(out.getvalue())

    def test_a_thread_rooted_in_an_automated_sender_is_excluded(self):
        """The narrow case the root check DOES cover: the notice is in-thread."""
        now = datetime.datetime.now(datetime.timezone.utc)
        old = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ours = (now - datetime.timedelta(days=21)).strftime("%Y-%m-%dT%H:%M:%SZ")
        thread = [
            {"id": "m1", "isDraft": False,  # the ROOT is automated
             "from": {"emailAddress": {"address": "no-reply@plooto.com"}},
             "toRecipients": [{"emailAddress": {"address": "info@frpdepots.com"}}],
             "sentDateTime": old},
            {"id": "m2", "isDraft": False,
             "from": {"emailAddress": {"address": "info@frpdepots.com"}},
             "toRecipients": [{"emailAddress": {"address": "someone@example.com"}}],
             "sentDateTime": ours},
        ]
        result = self._bank_notice_run(thread, "Re: deposit notice")
        self.assertEqual(result["overdue_count"], 0)

    def test_website_contact_submission_with_reply_to_is_awaits_you(self):
        """Web contact form from sales@frpdepots.com with customer Reply-To must be [awaits YOU]."""
        msgs = [
            {
                "id": "m1",
                "from": {"emailAddress": {"address": "sales@frpdepots.com"}},
                "toRecipients": [{"emailAddress": {"address": "info@frpdepots.com"}}],
                "replyTo": [{"emailAddress": {"address": "customer@example.com"}}],
                "receivedDateTime": "2026-08-17T18:36:58Z",
                "isDraft": False,
            }
        ]
        with patch.object(check, "_conversation", return_value=msgs):
            st = check.thread_state("t", "c1", "info@frpdepots.com")
            self.assertEqual(st["tag"], "[awaits YOU]")
            self.assertEqual(st["last_from"], "customer@example.com")
            ws = check._waiting_since(msgs, "info@frpdepots.com")
            self.assertEqual(ws, "2026-08-17T18:36:58Z")

    @unittest.expectedFailure
    def test_a_FORWARDED_bank_notice_is_still_wrongly_urgent(self):
        """B-16, the part that is NOT fixed. Documented, not pretended away.

        FORWARDING starts a NEW conversation whose root is RACHAD'S OWN message -
        the bank's address is not in this thread at all - so the automated-root
        check cannot see it. Confirmed against the live mailbox 2026-07-25: "Fw:
        You received a deposit of 49,100.00 USD" is still payment + urgent at 27
        working days.

        Left as an expected failure on purpose. It fails loudly the day someone
        fixes it, which is the prompt to delete this test and mark B-16 done.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        ours = (now - datetime.timedelta(days=21)).strftime("%Y-%m-%dT%H:%M:%SZ")
        thread = [
            {"id": "m1", "isDraft": False,  # the root IS Rachad - it is a forward
             "from": {"emailAddress": {"address": "info@frpdepots.com"}},
             "toRecipients": [{"emailAddress": {"address": "someone@example.com"}}],
             "sentDateTime": ours},
        ]
        result = self._bank_notice_run(
            thread, "Fw: You received a deposit of 49,100.00 USD")
        self.assertEqual(result["overdue_count"], 0,
                         "a forwarded no-reply bank notice should not be an urgent chase")


class CarryForwardTests(unittest.TestCase):
    """B-12: ageing past the window must not retire a thread unchased."""

    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.watch = Path(self._folder.name) / "followup_watch.json"
        for target, attr, value in (
            (tool, "CHASE_LOG", Path(self._folder.name) / "chase_log.jsonl"),
            (check, "FOLLOWUP_WATCH", self.watch),
        ):
            patcher = patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _old_thread(self):
        long_ago = (datetime.datetime.now(datetime.timezone.utc)
                    - datetime.timedelta(days=70)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return [
            {"id": "m1", "isDraft": False,
             "from": {"emailAddress": {"address": "cadelacruz@sctfrp.com"}},
             "toRecipients": [{"emailAddress": {"address": "info@frpdepots.com"}}],
             "sentDateTime": long_ago},
            {"id": "m2", "isDraft": False, "subject": "Re: Quote - QT-000023",
             "from": {"emailAddress": {"address": "info@frpdepots.com"}},
             "toRecipients": [{"emailAddress": {"address": "cadelacruz@sctfrp.com"}}],
             "sentDateTime": long_ago},
        ]

    def test_a_thread_outside_the_window_is_still_tracked(self):
        # It was registered on an earlier run; Sent Items no longer returns it.
        self.watch.write_text(json.dumps({
            "conv-qt23": {"subject": "Re: Quote - QT-000023", "last_sent": "2026-05-27 19:09"}
        }), encoding="utf-8")
        with (
            patch.object(check, "my_address", return_value="info@frpdepots.com"),
            patch.object(check, "get", return_value={"value": []}),
            patch.object(check, "_conversation", return_value=self._old_thread()),
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                check.show_waiting_on_them("token", 60)
        result = json.loads(out.getvalue())
        self.assertEqual(result["overdue_count"], 1,
                         "QT-000023 must not vanish just for ageing out of the window")
        self.assertTrue(result["overdue"][0]["carried_forward"])
        self.assertEqual(result["carried_forward_count"], 1)

    def test_an_answered_carried_thread_drops_off_the_watch(self):
        self.watch.write_text(json.dumps({"conv-x": {"subject": "s", "last_sent": ""}}),
                              encoding="utf-8")
        answered = self._old_thread() + [{
            "id": "m3", "isDraft": False,  # they finally replied
            "from": {"emailAddress": {"address": "cadelacruz@sctfrp.com"}},
            "toRecipients": [{"emailAddress": {"address": "info@frpdepots.com"}}],
            "sentDateTime": datetime.datetime.now(datetime.timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        }]
        with (
            patch.object(check, "my_address", return_value="info@frpdepots.com"),
            patch.object(check, "get", return_value={"value": []}),
            patch.object(check, "_conversation", return_value=answered),
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                check.show_waiting_on_them("token", 60)
        result = json.loads(out.getvalue())
        self.assertEqual(result["overdue_count"], 0)
        self.assertEqual(json.loads(self.watch.read_text(encoding="utf-8")), {},
                         "an answered thread must stop being carried forward")


if __name__ == "__main__":
    unittest.main()
