from __future__ import annotations

import unittest
from unittest import mock

import dado_daily_banking_review as review


class DailyBankingReviewTests(unittest.TestCase):
    def line(self, **changes):
        value = {
            "transaction_id": "100",
            "account_id": "96274000001409019",
            "date": "2026-08-08",
            "amount": "100.00",
            "amount_raw": "100.00",
            "currency": "CAD",
            "description": "Ordinary deposit",
            "payee": "",
            "reference": "",
            "debit_or_credit": "credit",
            "status": "uncategorized",
        }
        value.update(changes)
        return value

    def test_best_invoice_candidate_is_customer_receipt(self):
        category, recommendation = review.classify(self.line(), [{
            "transaction_type": "invoice", "is_best_match": True,
        }])
        self.assertEqual(category, "customer receipt")
        self.assertIn("invoice match", recommendation)

    def test_payroll_marker_without_target_is_not_auto_categorized(self):
        category, recommendation = review.classify(
            self.line(description="ADP payroll withdrawal", amount_raw="-500.00"), []
        )
        self.assertEqual(category, "possible payroll")
        self.assertIn("do not categorize automatically", recommendation)

    def test_negative_without_target_is_possible_expense(self):
        category, recommendation = review.classify(
            self.line(description="Office supplier", amount_raw="-25.00", debit_or_credit="debit"), []
        )
        self.assertEqual(category, "possible expense")
        self.assertIn("tax treatment", recommendation)

    def test_multiple_best_candidates_are_unknown(self):
        category, recommendation = review.classify(self.line(), [
            {"transaction_type": "invoice", "is_best_match": True},
            {"transaction_type": "invoice", "is_best_match": True},
        ])
        self.assertEqual(category, "unknown")
        self.assertIn("manual review", recommendation)

    def test_clean_report_is_silent(self):
        self.assertEqual(review.render({"open_lines": [], "open_count": 0}), "")

    def test_a_foreign_account_row_is_disclosed_not_fatal(self):
        """The defect that meant this job could never produce a review.

        The old code asked per account and RAISED the moment Zoho answered with
        a different one, so an empty feed was silent and any row was a hard
        failure - there was no input that produced a report. Measured across
        five runs since 2026-08-07: one creation test, one silent, three errors,
        zero reviews.

        Zoho's filter is no longer sent or trusted. A row belonging to an
        account we do not configure is DISCLOSED, so it can neither be lost nor
        mis-attributed.
        """
        rows = [{
            "transaction_id": "100",
            "account_id": "96274000009999999",
            "account_name": "Someone else's account",
            "date": "2026-08-08",
            "amount": 100,
            "currency_code": "CAD",
            "status": "uncategorized",
        }]
        reviewed, skipped = review.partition_feed(rows, {})

        self.assertEqual(reviewed, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("not one of the configured", skipped[0]["reason"])
        self.assertEqual(skipped[0]["account_id"], "96274000009999999")

    def test_attribution_comes_from_the_row_never_from_what_was_asked(self):
        """The constraint that outranks availability.

        The old code attributed every returned row to the account it had ASKED
        for. Softening its filter check without restructuring attribution would
        have reported USD build-up money under "Desjardins CAD" - an
        availability bug turned into a money-attribution bug.
        """
        check = {
            "96274000001409012": {
                "logical_account": "USD build-up",
                "account_id": "96274000001409012",
                "account_name": "USD Desjardins corporate build-up account",
                "currency": "USD",
            }
        }
        rows = [{
            "transaction_id": "100",
            "account_id": "96274000001409012",
            "account_name": "USD Desjardins corporate build-up account",
            "date": "2026-08-08",
            "amount": 100,
            "currency_code": "USD",
            "status": "uncategorized",
        }]
        reviewed, skipped = review.partition_feed(rows, check)

        self.assertEqual(skipped, [])
        self.assertEqual(reviewed[0]["account_id"], "96274000001409012")

    def test_a_name_that_disagrees_with_zoho_is_not_reviewed(self):
        """Two sources must agree before money is attributed to an account."""
        check = {
            "96274000001409019": {
                "logical_account": "Desjardins CAD",
                "account_id": "96274000001409019",
                "account_name": "Chequing account (C)",
                "currency": "CAD",
            }
        }
        rows = [{
            "transaction_id": "100",
            "account_id": "96274000001409019",
            "account_name": "Something Else Entirely",
            "date": "2026-08-08",
            "amount": 100,
            "currency_code": "CAD",
            "status": "uncategorized",
        }]
        reviewed, skipped = review.partition_feed(rows, check)

        self.assertEqual(reviewed, [])
        self.assertIn("feed calls this account", skipped[0]["reason"])

    def test_nonempty_render_discloses_zero_writes_and_requires_reply(self):
        line = self.line()
        line.update({
            "logical_account": "Desjardins CAD",
            "account_name": "Chequing account (C)",
            "category": "customer receipt",
            "recommendation": "Recommend staging an invoice match after live verification.",
            "candidates": [{
                "transaction_id": "200",
                "transaction_type": "invoice",
                "transaction_number": "INV-000001",
                "reference": "",
                "date": "2026-08-01",
                "amount": "100.00",
                "contact": "Example Customer",
                "is_best_match": True,
                "is_exact_match": False,
            }],
        })
        text = review.render({"open_lines": [line], "open_count": 1})
        self.assertIn("Zoho writes: **0**", text)
        self.assertIn("Reply with the line number", text)
        self.assertNotIn("APPROVED", text)


if __name__ == "__main__":
    unittest.main()
