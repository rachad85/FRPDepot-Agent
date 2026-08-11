"""The derived-totals defect class, pinned in both totals-bearing write tools.

A post-write verifier that treats a figure Zoho DERIVES FROM THE APPROVED CHANGE
as protected turns a correct write into `indeterminate` and permanently locks the
plan. It happened live on 2026-08-11: QT-000029's Item 9 quantity correction was
written correctly, the gross subtotal moved 13,220.64 -> 10,790.64 exactly as
intended, and the byte-exact fingerprint called that corruption.

The rule these tests enforce is EXEMPT IMPLIES ASSERTED: a key may leave the
fingerprint only if something else then checks it against an independently
recomputed figure. Dropping a key without asserting it would trade a false
failure for a blind spot, which is not the fix.

Kept in its own module deliberately: the per-tool suites were being edited
concurrently when this was written.
"""
from __future__ import annotations

import inspect
import unittest
from decimal import Decimal

import zoho_customer_quote_tool as quote
import zoho_invoice_revision_tool as invoice


class QuoteGrossSubtotalExemptionTests(unittest.TestCase):
    """The estimate tool: the exact field that locked the live item9 plan."""

    def test_the_field_that_locked_the_live_plan_is_named(self) -> None:
        self.assertIn("sub_total_exclusive_of_discount", quote.ITEM9_GROSS_SUBTOTAL_KEYS)

    def test_discount_only_keys_do_not_silently_cover_a_quantity_change(self) -> None:
        # ESTIMATE_DERIVED_KEYS is written for the DISCOUNT correction and is
        # correct there: a discount change does not move the gross subtotal. The
        # bug was reusing it unchanged for a QUANTITY change, which does.
        for key in quote.ITEM9_GROSS_SUBTOTAL_KEYS:
            self.assertNotIn(key, quote.ESTIMATE_DERIVED_KEYS)

    def test_quantity_fingerprint_exempts_the_gross_and_discount_fingerprint_does_not(self) -> None:
        estimate = {
            "estimate_id": "1",
            "sub_total": 100,
            "sub_total_exclusive_of_discount": 120,
            "bcy_sub_total_exclusive_of_discount": 120,
            "customer_name": "Troy Dualam Services",
            "line_items": [],
            "taxes": [],
        }
        discount_protected = quote.correction_protected_state(estimate)
        quantity_protected = quote.item9_protected_state(estimate)
        for key in quote.ITEM9_GROSS_SUBTOTAL_KEYS:
            self.assertIn(key, discount_protected, f"{key} must stay protected on a discount change")
            self.assertNotIn(key, quantity_protected, f"{key} must be exempt on a quantity change")
        # Exempting the gross must not quietly widen anything else.
        self.assertEqual(
            set(discount_protected) - set(quantity_protected),
            set(quote.ITEM9_GROSS_SUBTOTAL_KEYS),
        )
        self.assertEqual(quantity_protected["customer_name"], "Troy Dualam Services")

    @staticmethod
    def _case(gross: str = "810.00"):
        """One live-shaped estimate whose gross subtotal is `gross`.

        Built as a single record so `after` and the staged before-state agree by
        construction; only the figure under test is varied.
        """
        net = Decimal("729.00")
        combined, gst, qst = quote.tax_on(net)
        line = {
            "line_item_id": "9001", "item_id": "7001", "name": "FRP ELBOW",
            "description": "", "unit": "ea", "tax_id": "5001", "item_order": 1,
            "quantity": 1, "rate": "810.00", "discount": quote.TDS_LINE_DISCOUNT,
            "discount_amount": "81.00", "item_total": "729.00", "line_item_taxes": [],
        }
        after = {
            "estimate_id": "96274000001559037", "estimate_number": "QT-000029",
            "reference_number": "104750", "customer_id": "C1", "status": "sent",
            "discount_type": "item_level", "is_discount_before_tax": True,
            "line_items": [dict(line)],
            "sub_total": "729.00", "tax_total": str(combined), "total": str(net + combined),
            "sub_total_exclusive_of_discount": gross,
            "taxes": [
                {"tax_name": "GST", "tax_amount": str(gst)},
                {"tax_name": "QST", "tax_amount": str(qst)},
            ],
        }
        before_state = dict(after)
        before_state["sub_total_exclusive_of_discount"] = "810.00"
        expected = {
            "lines": [{
                "line_item_id": "9001", "quantity": "1",
                "discount_amount": "81.00", "item_total": "729.00", "changed": False,
            }],
            "sub_total": "729.00", "tax_total": str(combined), "total": str(net + combined),
            "tax_gst": str(gst), "tax_qst": str(qst),
            "list_subtotal": "810.00",
        }
        protected = quote.item9_protected_state(after)
        evidence = {
            "estimate": {
                "estimate_id": "96274000001559037", "estimate_number": "QT-000029",
                "reference_number": "104750", "customer_id": "C1", "status": "sent",
                "line_count": 1, "before_state": before_state,
                "protected_state": protected,
                "protected_state_sha256": quote.digest_for(protected),
            },
            "expected": expected,
        }
        return after, evidence

    def test_the_verifier_actually_checks_the_gross_against_the_recomputed_one(self) -> None:
        """EXEMPT IMPLIES ASSERTED - the property whose absence caused the bug.

        Exercises the real verification path: dropping the gross from the
        fingerprint is only safe because this assertion replaces it.
        """
        good_after, good_evidence = self._case()
        quote.verify_item9_result(good_after, good_evidence, "read-back")

        # The pre-write gross, i.e. the write silently failed to take effect.
        stale_after, stale_evidence = self._case(gross="13220.64")
        with self.assertRaisesRegex(quote.DraftToolError, "sub_total_exclusive_of_discount"):
            quote.verify_item9_result(stale_after, stale_evidence, "read-back")

        # Off by one cent: still refused, never rounded away.
        cent_after, cent_evidence = self._case(gross="810.01")
        with self.assertRaisesRegex(quote.DraftToolError, "sub_total_exclusive_of_discount"):
            quote.verify_item9_result(cent_after, cent_evidence, "read-back")

    def test_producer_and_consumer_agree_on_the_gross_key_name(self) -> None:
        """A NAME-CONTRACT check only, and deliberately labelled as one.

        item9_expected derives the gross from the live quantities and rates;
        verify_item9_result reads it back out under the same key. The functional
        test above builds its own expected dict, so it cannot catch a rename on
        the producing side - this can. It proves the two names match and nothing
        more; substring presence is not evidence that a check runs.
        """
        for function in (quote.item9_expected, quote.verify_item9_result):
            self.assertIn(
                "list_subtotal", inspect.getsource(function),
                f"{function.__name__} no longer uses the recomputed gross key",
            )


class InvoiceDerivedTotalsExemptionTests(unittest.TestCase):
    """The invoice tool: the same gap, latent, on a tool never yet run live."""

    def test_the_missing_families_are_named(self) -> None:
        self.assertIn("sub_total_exclusive_of_discount", invoice.GROSS_SUBTOTAL_FIELDS)
        for key in ("bcy_sub_total", "bcy_tax_total", "bcy_total"):
            self.assertIn(key, invoice.BCY_TOTAL_FIELDS)

    def test_exempt_on_a_line_change_and_protected_without_one(self) -> None:
        derived = set(invoice.GROSS_SUBTOTAL_FIELDS) | set(invoice.BCY_TOTAL_FIELDS)
        with_lines = set(invoice.unprotected_keys(
            {"changes": {}, "line_changes": [{"line_item_id": "1"}]}
        ))
        without_lines = set(invoice.unprotected_keys(
            {"changes": {"reference_number": "PO-1"}, "line_changes": []}
        ))
        self.assertTrue(derived <= with_lines, "a line-value change must exempt them")
        self.assertFalse(
            derived & without_lines,
            "a header-only revision moves no total, so they must stay protected",
        )

    def test_gross_is_withheld_rather_than_guessed_when_rounding_bites(self) -> None:
        # Exact at 2dp: predicted.
        self.assertEqual(
            invoice.deterministic_line_gross(Decimal("4"), Decimal("810.00"), 2),
            Decimal("3240.00"),
        )
        # Not exact at 2dp: summing rounded lines and rounding a summed total can
        # disagree by a cent, so no prediction is made.
        self.assertIsNone(
            invoice.deterministic_line_gross(Decimal("3"), Decimal("0.005"), 2)
        )

    def test_build_totals_predicts_the_gross_and_the_proven_bcy_mirrors(self) -> None:
        live = {
            "sub_total": 3240.00,
            "tax_total": 0,
            "total": 3240.00,
            "balance": 3240.00,
            "sub_total_exclusive_of_discount": 3240.00,
            "bcy_sub_total": 3240.00,
            "bcy_tax_total": 0,
            "bcy_total": 3240.00,
            "shipping_charge": 0,
            "adjustment": 0,
        }
        totals = invoice.build_totals(
            live,
            {"line_changes": [{"line_item_id": "1"}]},
            [Decimal("810.00")],
            [Decimal("810.00")],
            [{"quantity": 1, "rate": "810.00"}],
            [{"tax_percentage": 0}],
            False,
        )
        after = totals["after"]
        self.assertEqual(after["sub_total"], "810.00")
        self.assertEqual(after["sub_total_exclusive_of_discount"], "810.00")
        # Proven mirrors only: the live record shows bcy == plain before the write.
        self.assertEqual(after["bcy_sub_total"], "810.00")
        self.assertEqual(after["bcy_total"], "810.00")

    def test_a_foreign_currency_invoice_never_false_fails_on_bcy(self) -> None:
        live = {
            "sub_total": 3240.00, "tax_total": 0, "total": 3240.00, "balance": 3240.00,
            "sub_total_exclusive_of_discount": 3240.00,
            "bcy_sub_total": 4471.20,  # a real exchange rate: not predictable here
            "bcy_total": 4471.20,
            "shipping_charge": 0, "adjustment": 0,
        }
        totals = invoice.build_totals(
            live,
            {"line_changes": [{"line_item_id": "1"}]},
            [Decimal("810.00")],
            [Decimal("810.00")],
            [{"quantity": 1, "rate": "810.00"}],
            [{"tax_percentage": 0}],
            False,
        )
        self.assertNotIn("bcy_sub_total", totals["after"])
        self.assertNotIn("bcy_total", totals["after"])

    def test_an_unpredicted_gross_is_disclosed_not_silently_dropped(self) -> None:
        live = {
            "sub_total": 10.00, "tax_total": 0, "total": 10.00, "balance": 10.00,
            "sub_total_exclusive_of_discount": 10.00,
            "shipping_charge": 0, "adjustment": 0,
        }
        totals = invoice.build_totals(
            live,
            {"line_changes": [{"line_item_id": "1"}]},
            [Decimal("10.00")],
            [None],
            [{"quantity": 3, "rate": "0.005"}],
            [{"tax_percentage": 0}],
            False,
        )
        self.assertNotIn("sub_total_exclusive_of_discount", totals["after"])
        self.assertIn("gross subtotal is not predicted", totals["basis"])

    def test_verify_totals_catches_a_wrong_gross(self) -> None:
        evidence = {"totals": {"after": {"sub_total_exclusive_of_discount": "810.00"}}}
        invoice.verify_totals({"sub_total_exclusive_of_discount": 810.00}, evidence)
        with self.assertRaisesRegex(invoice.InvoiceRevisionError, "sub_total_exclusive_of_discount"):
            invoice.verify_totals({"sub_total_exclusive_of_discount": 3240.00}, evidence)


if __name__ == "__main__":
    unittest.main()
