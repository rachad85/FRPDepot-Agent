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

    def test_account_filter_must_be_honored(self):
        response = {
            "transactions": [{
                "transaction_id": "100",
                "account_id": "96274000009999999",
                "date": "2026-08-08",
                "amount": 100,
                "currency_code": "CAD",
                "status": "uncategorized",
            }],
            "page_context": {"has_more_page": False},
        }
        with mock.patch.object(review.banking, "books_ui_get", return_value=response):
            with self.assertRaisesRegex(review.DailyReviewError, "ignored account filter"):
                review.fetch_account_lines(
                    {"books_organization_id": "110002157575"},
                    "96274000001409019",
                )

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
