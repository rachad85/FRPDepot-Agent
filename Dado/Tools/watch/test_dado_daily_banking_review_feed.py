"""The daily banking review had never produced a single review.

Commissioned 2026-08-07. Five runs by 2026-08-11: one creation test, one silent,
three errors, ZERO reviews. The cause was structural, not flaky.

`fetch_account_lines` asked Zoho for one account at a time and RAISED the moment
Zoho answered with a different one. Zoho's server-side account_id filter on this
UI route is not honoured - measured 2026-08-09 and 08-10, a request carrying
96274000001409019 came back with a row belonging to 96274000001409012, and the
unfiltered read on 08-10 proved that row was the only line in the whole feed. So
an empty feed was silent and ANY row was a hard failure: there was no input that
produced a report.

The filter is no longer sent or trusted. Every row is attributed from its OWN
account_id, and anything that cannot be attributed is disclosed rather than
dropped or mis-filed.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dado_daily_banking_review as review  # noqa: E402

ORG = {"books_organization_id": "110002157575"}

CHECKS = [
    {"logical_account": "Desjardins CAD", "account_id": "96274000001409019",
     "account_name": "Chequing account (C)", "currency": "CAD",
     "feeds_last_refresh_date": "", "refresh_status": ""},
    {"logical_account": "USD build-up", "account_id": "96274000001409012",
     "account_name": "USD Desjardins corporate build-up account", "currency": "USD",
     "feeds_last_refresh_date": "", "refresh_status": ""},
]


def feed_row(txn_id, account_id, account_name, amount="100.00", currency="CAD"):
    return {
        "transaction_id": txn_id, "account_id": account_id,
        "account_name": account_name, "date": "2026-08-08", "amount": amount,
        "currency_code": currency, "status": "uncategorized",
        "description": "a payment", "payee": "ACME", "reference": "REF1",
    }


class FeedReviewTests(unittest.TestCase):
    def _build(self, rows, candidates=None, candidate_error=None):
        pages = [{"transactions": rows, "page_context": {"has_more_page": False}}]

        def fake_get(path, org):
            if path.startswith(review.MATCH_PATH if hasattr(review, "MATCH_PATH") else "/api/v3/banktransactions/uncategorized/match"):
                if candidate_error:
                    raise review.DailyReviewError(candidate_error)
                return {"matching_transactions": candidates or []}
            return pages[0]

        with mock.patch.object(review, "validate_accounts", return_value=CHECKS), \
             mock.patch.object(review, "account_rows", return_value={}), \
             mock.patch.object(review.banking, "books_ui_get", side_effect=fake_get):
            return review.build_report("token", ORG)

    # ---- the regression -----------------------------------------------------
    def test_a_configured_row_now_PRODUCES_a_review(self):
        """There was previously no input that produced one."""
        report = self._build([feed_row("100", "96274000001409019", "Chequing account (C)")])

        self.assertEqual(report["open_count"], 1)
        self.assertEqual(report["open_lines"][0]["logical_account"], "Desjardins CAD")
        self.assertTrue(review.render(report), "a review with a line must not be silent")

    def test_the_account_zoho_used_to_return_wrongly_is_now_just_another_row(self):
        """96274000001409012 arriving in the feed is normal, not a failure."""
        report = self._build([
            feed_row("100", "96274000001409012",
                     "USD Desjardins corporate build-up account", currency="USD"),
        ])

        self.assertEqual(report["open_count"], 1)
        self.assertEqual(report["open_lines"][0]["logical_account"], "USD build-up")
        self.assertEqual(report["not_reviewed_count"], 0)

    def test_an_empty_feed_is_still_silent(self):
        report = self._build([])
        self.assertEqual(report["open_count"], 0)
        self.assertEqual(review.render(report), "", "nothing to say means say nothing")

    # ---- disclosure ---------------------------------------------------------
    def test_an_unconfigured_account_is_disclosed_not_dropped(self):
        report = self._build([
            feed_row("100", "96274000001409019", "Chequing account (C)"),
            feed_row("101", "96274000000000001", "Some other account"),
        ])

        self.assertEqual(report["open_count"], 1)
        self.assertEqual(report["not_reviewed_count"], 1)
        text = review.render(report)
        self.assertIn("NOT REVIEWED", text)
        self.assertIn("96274000000000001", text)

    def test_a_report_of_only_skipped_lines_still_speaks(self):
        report = self._build([feed_row("101", "96274000000000001", "Some other account")])

        self.assertEqual(report["open_count"], 0)
        text = review.render(report)
        self.assertIn("NOT REVIEWED", text)
        self.assertIn("No line was attributable", text)
        self.assertNotIn("Reply with the line number", text,
                         "there are no line numbers to reply about")

    # ---- trust boundaries ---------------------------------------------------
    def test_unreadable_candidates_on_SOME_lines_degrade_and_disclose(self):
        """One unreadable line must not cost the others their review."""
        calls = {"n": 0}

        def fake_get(path, org):
            if "match" in path:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise review.DailyReviewError("timeout")
                return {"matching_transactions": []}
            return {"transactions": [
                feed_row("100", "96274000001409019", "Chequing account (C)"),
                feed_row("101", "96274000001409019", "Chequing account (C)"),
            ], "page_context": {"has_more_page": False}}

        with mock.patch.object(review, "validate_accounts", return_value=CHECKS), \
             mock.patch.object(review, "account_rows", return_value={}), \
             mock.patch.object(review.banking, "books_ui_get", side_effect=fake_get):
            report = review.build_report("token", ORG)

        self.assertEqual(report["open_count"], 2)
        self.assertEqual(report["unread_candidate_count"], 1)
        self.assertIn("classify this line by hand", review.render(report))

    def test_unreadable_candidates_on_EVERY_line_hard_fails(self):
        """A read we cannot trust must not become a report - applied consistently."""
        with self.assertRaisesRegex(review.DailyReviewError, "not trustworthy"):
            self._build(
                [feed_row("100", "96274000001409019", "Chequing account (C)")],
                candidate_error="timeout",
            )

    def test_a_whole_read_integrity_failure_still_hard_fails(self):
        """Per-row problems degrade; a broken READ does not."""
        with mock.patch.object(review, "validate_accounts", return_value=CHECKS), \
             mock.patch.object(review, "account_rows", return_value={}), \
             mock.patch.object(review.banking, "books_ui_get",
                               return_value={"transactions": "not a list"}):
            with self.assertRaises(review.DailyReviewError):
                review.build_report("token", ORG)

    def test_duplicate_transaction_ids_hard_fail(self):
        with self.assertRaises(review.DailyReviewError):
            self._build([
                feed_row("100", "96274000001409019", "Chequing account (C)"),
                feed_row("100", "96274000001409019", "Chequing account (C)"),
            ])

    def test_the_filter_is_never_sent(self):
        """Trusting it is what produced a wrong-account row twice."""
        seen = []

        def fake_get(path, org):
            seen.append(path)
            if "match" in path:
                return {"matching_transactions": []}
            return {"transactions": [], "page_context": {"has_more_page": False}}

        with mock.patch.object(review, "validate_accounts", return_value=CHECKS), \
             mock.patch.object(review, "account_rows", return_value={}), \
             mock.patch.object(review.banking, "books_ui_get", side_effect=fake_get):
            review.build_report("token", ORG)

        self.assertTrue(seen)
        self.assertFalse(any("account_id=" in path for path in seen),
                         "the account_id filter must never be sent")


if __name__ == "__main__":
    unittest.main()
