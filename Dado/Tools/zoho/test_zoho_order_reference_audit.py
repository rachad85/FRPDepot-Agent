"""Tests for the READ-ONLY Zoho Sales Order reference audit.

Every Zoho read is mocked. Nothing here touches the network, the credential
vault, the live receipts log or any real report directory.
"""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import zoho_order_reference_audit as audit


LIST_SPECS = {
    "/books/v3/salesorders": ("salesorders", "salesorder", "salesorder_id"),
    "/books/v3/estimates": ("estimates", "estimate", "estimate_id"),
    "/books/v3/invoices": ("invoices", "invoice", "invoice_id"),
}


class FakeZoho:
    """Serves paginated listings and detail reads from in-memory records."""

    def __init__(
        self,
        salesorders=(),
        estimates=(),
        invoices=(),
        details=None,
        force_page_size=None,
        always_more=False,
    ) -> None:
        self.collections = {
            "/books/v3/salesorders": list(salesorders),
            "/books/v3/estimates": list(estimates),
            "/books/v3/invoices": list(invoices),
        }
        self.details = dict(details or {})
        self.force_page_size = force_page_size
        self.always_more = always_more
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path: str, params=None) -> dict:
        self.calls.append((path, dict(params or {})))
        if path in self.collections:
            key = LIST_SPECS[path][0]
            rows = self.collections[path]
            page = int((params or {}).get("page", 1))
            size = self.force_page_size or int((params or {}).get("per_page", 200))
            start = (page - 1) * size
            chunk = rows[start:start + size]
            has_more = self.always_more or (start + size) < len(rows)
            return {key: chunk, "page_context": {"has_more_page": has_more}}
        base, _, record_id = path.rpartition("/")
        if base in LIST_SPECS:
            _, singular, id_field = LIST_SPECS[base]
            record = self.details.get(record_id)
            if record is None:
                for row in self.collections[base]:
                    if str(row.get(id_field)) == record_id:
                        record = row
                        break
            if record is None:
                raise AssertionError(f"unexpected detail read: {path}")
            return {singular: record}
        raise AssertionError(f"unexpected path: {path}")

    def list_calls(self, path: str) -> list[dict]:
        return [params for called, params in self.calls if called == path]

    def detail_calls(self) -> list[str]:
        return [called for called, _ in self.calls if called.rpartition("/")[0] in LIST_SPECS]


def salesorder(
    salesorder_id="1001",
    number="SO-00001",
    customer_id="9001",
    customer_name="Structural Composites Technologies Ltd",
    reference="PO26330",
    date="2026-08-01",
    status="open",
    **extra,
):
    row = {
        "salesorder_id": salesorder_id,
        "salesorder_number": number,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "reference_number": reference,
        "date": date,
        "status": status,
        "order_status": status,
        "invoiced_status": "not_invoiced",
        "shipped_status": "not_shipped",
        "created_time": "2026-08-01T09:00:00-0400",
        "last_modified_time": "2026-08-01T09:05:00-0400",
        # Financial noise Zoho really returns. None of it may survive.
        "total": 105.42,
        "sub_total": 100.40,
        "tax_total": 5.02,
        "discount": 0,
        "balance": 105.42,
        "currency_code": "CAD",
        "exchange_rate": 1.0,
    }
    row.update(extra)
    return row


def estimate(
    estimate_id="2001",
    number="QT-000029",
    customer_id="9001",
    customer_name="Structural Composites Technologies Ltd",
    reference="PO26330",
    date="2026-07-20",
    status="sent",
    **extra,
):
    row = {
        "estimate_id": estimate_id,
        "estimate_number": number,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "reference_number": reference,
        "date": date,
        "status": status,
        "created_time": "2026-07-20T09:00:00-0400",
        "last_modified_time": "2026-07-20T09:05:00-0400",
        "total": 11165.88,
        "tax_total": 1454.31,
        "discount": "10%",
    }
    row.update(extra)
    return row


def invoice(
    invoice_id="3001",
    number="INV-000051",
    customer_id="9001",
    customer_name="Structural Composites Technologies Ltd",
    reference="PO26330",
    date="2026-08-05",
    status="sent",
    **extra,
):
    row = {
        "invoice_id": invoice_id,
        "invoice_number": number,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "reference_number": reference,
        "date": date,
        "status": status,
        "created_time": "2026-08-05T09:00:00-0400",
        "last_modified_time": "2026-08-05T09:05:00-0400",
        "total": 105.42,
        "balance": 0.0,
        "payment_made": 105.42,
    }
    row.update(extra)
    return row


def run(fake, directory=None):
    if directory is not None:
        return audit.run_audit(fake, Path(directory), generated_utc="2026-08-11T00:00:00+00:00")
    with tempfile.TemporaryDirectory() as temporary:
        return audit.run_audit(fake, Path(temporary), generated_utc="2026-08-11T00:00:00+00:00")


def row_for(result, number):
    for row in result["rows"]:
        if row["salesorder_number"] == number:
            return row
    raise AssertionError(f"sales order {number} is not in the report")


class PaginationTests(unittest.TestCase):
    def test_bounded_pagination_uses_200_per_page_and_terminates(self) -> None:
        orders = [salesorder(str(1000 + index), f"SO-{index:05d}") for index in range(45)]
        fake = FakeZoho(salesorders=orders, force_page_size=20)
        result = run(fake)
        self.assertEqual(len(result["rows"]), 45)
        pages = fake.list_calls("/books/v3/salesorders")
        self.assertEqual([params["page"] for params in pages], [1, 2, 3])
        self.assertTrue(all(params["per_page"] == audit.PER_PAGE for params in pages))
        self.assertEqual(audit.PER_PAGE, 200)
        self.assertEqual(audit.MAX_PAGES, 100)

    def test_pagination_guard_refuses_after_the_page_ceiling(self) -> None:
        fake = FakeZoho(salesorders=[salesorder()], force_page_size=1, always_more=True)
        with self.assertRaises(audit.AuditError) as caught:
            run(fake)
        self.assertIn("pagination guard", str(caught.exception))
        self.assertEqual(len(fake.list_calls("/books/v3/salesorders")), audit.MAX_PAGES)

    def test_non_list_collection_is_refused(self) -> None:
        def fetch(path, params=None):
            return {"salesorders": {"salesorder_id": "1"}, "page_context": {"has_more_page": False}}

        with self.assertRaises(audit.AuditError):
            run(fetch)

    def test_absent_collection_key_reads_as_empty_not_as_an_error(self) -> None:
        fake = FakeZoho()
        result = run(fake)
        self.assertEqual(result["summary"]["salesorders_total"], 0)


class IdentityAndUniquenessTests(unittest.TestCase):
    def test_detail_identity_mismatch_is_refused(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder("1001")],
            details={"1001": salesorder("9999")},
        )
        with self.assertRaises(audit.AuditError) as caught:
            run(fake)
        self.assertIn("identity mismatch", str(caught.exception))

    def test_duplicate_salesorder_id_is_refused(self) -> None:
        fake = FakeZoho(salesorders=[salesorder("1001"), salesorder("1001", "SO-00002")])
        with self.assertRaises(audit.AuditError) as caught:
            run(fake)
        self.assertIn("duplicate", str(caught.exception))

    def test_duplicate_estimate_id_is_refused(self) -> None:
        fake = FakeZoho(estimates=[estimate("2001"), estimate("2001", "QT-000030")])
        with self.assertRaises(audit.AuditError):
            run(fake)

    def test_duplicate_invoice_id_is_refused(self) -> None:
        fake = FakeZoho(invoices=[invoice("3001"), invoice("3001", "INV-000052")])
        with self.assertRaises(audit.AuditError):
            run(fake)

    def test_ids_must_be_positive_numeric_strings(self) -> None:
        for bad in ("", "0", "-5", "10a", "1 001", "abc"):
            self.assertFalse(audit.is_positive_numeric_id(bad), bad)
        for good in ("1", "96274000001559037"):
            self.assertTrue(audit.is_positive_numeric_id(good), good)
        with self.assertRaises(audit.AuditError):
            run(FakeZoho(salesorders=[salesorder("not-an-id")]))

    def test_every_salesorder_detail_is_fetched_once(self) -> None:
        orders = [salesorder(str(1000 + index), f"SO-{index:05d}") for index in range(5)]
        fake = FakeZoho(salesorders=orders)
        result = run(fake)
        details = [path for path in fake.detail_calls() if "/salesorders/" in path]
        self.assertEqual(len(details), 5)
        self.assertEqual(len(set(details)), 5)
        self.assertEqual(result["summary"]["salesorder_details_fetched"], 5)


class NormalizationTests(unittest.TestCase):
    def test_labels_and_separators_are_stripped_only_when_leading(self) -> None:
        self.assertEqual(audit.normalize_po("PO 26330"), "26330")
        self.assertEqual(audit.normalize_po("po#26330"), "26330")
        self.assertEqual(audit.normalize_po("P.O. : 26330"), "26330")
        self.assertEqual(audit.normalize_po("Purchase Order - 26330"), "26330")
        self.assertEqual(audit.normalize_po("Client PO 26330"), "26330")
        self.assertEqual(audit.normalize_po("  po26330  "), "PO26330")
        self.assertEqual(audit.normalize_po("4567-PO-2"), "4567-PO-2")

    def test_normalization_does_not_over_collapse_distinct_references(self) -> None:
        distinct = [
            "PO 104750 / J6276",
            "PO 104750",
            "104750-A",
            "104750/2",
            "J6276",
            "104751",
        ]
        normalized = [audit.normalize_po(value) for value in distinct]
        self.assertEqual(len(set(normalized)), len(distinct))
        self.assertEqual(audit.normalize_po("PO 104750 / J6276"), "104750 / J6276")

    def test_unicode_case_and_whitespace_only(self) -> None:
        self.assertEqual(audit.normalize_po("ｐｏ　26330"), "26330")
        self.assertEqual(audit.normalize_po("po   26330\t"), "26330")
        self.assertEqual(audit.normalize_po("po-26330"), "26330")

    def test_po_state_classification(self) -> None:
        self.assertEqual(audit.classify_po("", ""), audit.PO_STATE_MISSING)
        self.assertEqual(audit.classify_po(None, ""), audit.PO_STATE_MISSING)
        self.assertEqual(audit.classify_po("PO26330", "PO26330"), audit.PO_STATE_PRESENT)
        self.assertEqual(audit.classify_po("PO #", audit.normalize_po("PO #")), audit.PO_STATE_AMBIGUOUS)
        self.assertEqual(
            audit.classify_po("PO 111, PO 222", audit.normalize_po("PO 111, PO 222")),
            audit.PO_STATE_AMBIGUOUS,
        )
        self.assertEqual(
            audit.classify_po("111 and 222", audit.normalize_po("111 and 222")),
            audit.PO_STATE_AMBIGUOUS,
        )


class ProjectionTests(unittest.TestCase):
    def test_projections_hold_no_financial_field(self) -> None:
        projections = [
            audit.project_salesorder(salesorder()),
            audit.project_estimate(estimate()),
            audit.project_invoice(invoice()),
        ]
        for projection in projections:
            for key in projection:
                lowered = key.casefold()
                for token in audit.FINANCIAL_KEY_TOKENS:
                    self.assertNotIn(token, lowered, f"{key} carries {token}")
            self.assertNotIn("105.42", json.dumps(projection))
            self.assertNotIn("11165.88", json.dumps(projection))

    def test_projection_keys_are_a_closed_allowlist(self) -> None:
        allowed = set(audit.SALESORDER_FIELDS) | set(audit.PROJECTION_EXTRA_KEYS)
        self.assertLessEqual(set(audit.project_salesorder(salesorder())), allowed)
        allowed = set(audit.ESTIMATE_FIELDS) | set(audit.PROJECTION_EXTRA_KEYS)
        self.assertLessEqual(set(audit.project_estimate(estimate())), allowed)
        allowed = set(audit.INVOICE_FIELDS) | set(audit.PROJECTION_EXTRA_KEYS)
        self.assertLessEqual(set(audit.project_invoice(invoice())), allowed)

    def test_nested_link_entries_drop_financial_keys(self) -> None:
        raw = salesorder(
            invoices=[
                {
                    "invoice_id": "3001",
                    "invoice_number": "INV-000051",
                    "date": "2026-08-05",
                    "status": "sent",
                    "total": 105.42,
                    "balance": 0.0,
                    "payment_made": 105.42,
                }
            ]
        )
        projection = audit.project_salesorder(raw)
        self.assertEqual(
            projection["linked_invoice_entries"],
            [{"invoice_id": "3001", "invoice_number": "INV-000051", "date": "2026-08-05", "status": "sent"}],
        )
        self.assertNotIn("105.42", json.dumps(projection))

    def test_a_leaked_financial_value_is_refused(self) -> None:
        with self.assertRaises(audit.AuditError):
            audit.assert_projection_is_clean(
                {"reference_number": "4242.42"}, {"total": "4242.42"}, "test"
            )

    def test_a_value_that_also_exists_under_a_clean_key_is_not_a_leak(self) -> None:
        audit.assert_projection_is_clean(
            {"reference_number": "1000"}, {"total": "1000", "reference_number": "1000"}, "test"
        )

    def test_a_lifecycle_status_word_is_not_a_financial_leak(self) -> None:
        # Live Zoho really does return paid_status="paid" on a Sales Order while
        # its linked invoice entry's own status is also "paid". A word is never
        # a leaked figure, and refusing it would abort a clean audit.
        audit.assert_projection_is_clean(
            {"linked_invoice_entries": [{"status": "paid"}]},
            {"paid_status": "paid", "total": 105.42},
            "test",
        )
        projection = audit.project_salesorder(
            salesorder(
                paid_status="paid",
                invoices=[{"invoice_id": "3001", "invoice_number": "INV-1", "status": "paid"}],
            )
        )
        self.assertEqual(projection["linked_invoice_entries"][0]["status"], "paid")

    def test_a_numeric_financial_value_is_still_refused(self) -> None:
        with self.assertRaises(audit.AuditError):
            audit.assert_projection_is_clean(
                {"reference_number": "105.42"}, {"total": "105.42"}, "test"
            )

    def test_a_financial_key_in_a_projection_is_refused(self) -> None:
        with self.assertRaises(audit.AuditError):
            audit.assert_projection_is_clean({"total": "1"}, {}, "test")
        with self.assertRaises(audit.AuditError):
            audit.assert_projection_is_clean({"nested": [{"balance": "1"}]}, {}, "test")

    def test_historical_statuses_are_preserved_not_filtered(self) -> None:
        orders = [
            salesorder("1001", "SO-00001", status="draft"),
            salesorder("1002", "SO-00002", status="void"),
            salesorder("1003", "SO-00003", status="closed"),
            salesorder("1004", "SO-00004", status="open"),
        ]
        result = run(FakeZoho(salesorders=orders))
        self.assertEqual(result["summary"]["salesorders_total"], 4)
        self.assertEqual(
            sorted(row["status"] for row in result["rows"]),
            ["closed", "draft", "open", "void"],
        )

    def test_a_malformed_link_id_is_recorded_not_silently_dropped(self) -> None:
        projection = audit.project_salesorder(salesorder(estimate_id="not-numeric"))
        self.assertEqual(projection["linked_estimate_ids"], [])
        self.assertEqual(projection["malformed_link_ids"], ["estimate_id=not-numeric"])


class QuoteMatchingTests(unittest.TestCase):
    def test_direct_id_link_wins_over_an_inferred_exact_po(self) -> None:
        # The PO-matching estimate is a decoy; the live link points elsewhere.
        fake = FakeZoho(
            salesorders=[salesorder(estimate_id="2002")],
            estimates=[
                estimate("2001", "QT-000029", reference="PO26330"),
                estimate("2002", "QT-000030", reference="SOMETHING ELSE"),
            ],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_id"], "2002")
        self.assertEqual(row["quote_number"], "QT-000030")
        self.assertEqual(row["quote_match_source"], audit.SOURCE_DIRECT_ID)
        self.assertEqual(row["quote_confidence"], audit.CONFIDENCE_CERTAIN)

    def test_direct_number_link_is_certain(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(reference="", estimate_number="QT-000029")],
            estimates=[estimate("2001", "QT-000029", reference="")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_id"], "2001")
        self.assertEqual(row["quote_match_source"], audit.SOURCE_DIRECT_NUMBER)
        self.assertEqual(row["quote_confidence"], audit.CONFIDENCE_CERTAIN)

    def test_reverse_direct_id_link_works(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder("1001", reference="")],
            estimates=[estimate("2001", reference="", salesorder_id="1001")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_id"], "2001")
        self.assertEqual(row["quote_match_source"], audit.SOURCE_REVERSE_DIRECT_ID)
        self.assertEqual(row["quote_confidence"], audit.CONFIDENCE_CERTAIN)

    def test_reverse_direct_number_link_works(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder("1001", "SO-00001", reference="")],
            estimates=[estimate("2001", reference="", salesorder_number="SO-00001")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_match_source"], audit.SOURCE_REVERSE_DIRECT_NUMBER)
        self.assertEqual(row["quote_confidence"], audit.CONFIDENCE_CERTAIN)

    def test_exact_normalized_po_matches_only_within_the_same_customer(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(customer_id="9001", reference="PO 26330")],
            estimates=[estimate("2002", "QT-000031", customer_id="9002", reference="26330")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_id"], "")
        self.assertEqual(row["quote_match_source"], audit.SOURCE_NONE)
        self.assertIn("missing_quote", row["review_state"])

    def test_exact_normalized_po_matches_inside_one_customer(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(customer_id="9001", reference="PO 26330")],
            estimates=[estimate("2002", "QT-000031", customer_id="9001", reference="26330")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_id"], "2002")
        self.assertEqual(row["quote_match_source"], audit.SOURCE_CUSTOMER_AND_EXACT_PO)
        self.assertEqual(row["quote_confidence"], audit.CONFIDENCE_STRONG)

    def test_duplicate_po_candidates_are_ambiguous_and_nothing_is_chosen(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(reference="PO 26330")],
            estimates=[
                estimate("2001", "QT-000029", reference="PO 26330"),
                estimate("2002", "QT-000030", reference="26330"),
            ],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_id"], "")
        self.assertEqual(row["quote_confidence"], audit.CONFIDENCE_AMBIGUOUS)
        self.assertEqual(sorted(row["quote_candidates"]), ["QT-000029", "QT-000030"])
        self.assertIn("ambiguous_quote", row["review_state"])
        # All originals are shown, so two different references can never be
        # silently reported as one.
        originals = sorted(c["reference_original"] for c in row["quote_candidate_details"])
        self.assertEqual(originals, ["26330", "PO 26330"])

    def test_a_glued_po_prefix_is_a_different_reference_from_the_bare_number(self) -> None:
        # PO26330 is not the label "PO" plus 26330; it is its own reference, so
        # the two must never be conflated into one candidate group.
        fake = FakeZoho(
            salesorders=[salesorder(reference="PO26330")],
            estimates=[
                estimate("2001", "QT-000029", reference="PO26330"),
                estimate("2002", "QT-000030", reference="26330"),
            ],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_number"], "QT-000029")
        self.assertEqual(row["quote_confidence"], audit.CONFIDENCE_STRONG)

    def test_a_quote_number_written_into_the_reference_matches(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(reference="Against quote QT-000029")],
            estimates=[estimate("2001", "QT-000029", reference="")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_id"], "2001")
        self.assertEqual(row["quote_match_source"], audit.SOURCE_CUSTOMER_AND_UNIQUE_QUOTE_NUMBER_TEXT)
        self.assertEqual(row["quote_confidence"], audit.CONFIDENCE_STRONG)

    def test_a_quote_number_text_hit_needs_the_same_customer(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(customer_id="9001", reference="Against quote QT-000029")],
            estimates=[estimate("2001", "QT-000029", customer_id="9002", reference="")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_match_source"], audit.SOURCE_NONE)

    def test_a_partial_quote_number_does_not_match(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(reference="QT-0000291")],
            estimates=[estimate("2001", "QT-000029", reference="")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_match_source"], audit.SOURCE_NONE)

    def test_customer_and_date_alone_never_match(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(customer_id="9001", reference="", date="2026-07-20")],
            estimates=[estimate("2001", customer_id="9001", reference="", date="2026-07-20")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_id"], "")
        self.assertEqual(row["quote_match_source"], audit.SOURCE_NONE)
        self.assertEqual(row["quote_confidence"], audit.CONFIDENCE_NONE)

    def test_a_matching_amount_alone_never_matches(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(reference="", total=11165.88)],
            estimates=[estimate("2001", reference="", total=11165.88)],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_match_source"], audit.SOURCE_NONE)

    def test_a_matching_amount_never_outranks_the_live_link(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(reference="", estimate_id="2002", total=999.0)],
            estimates=[
                estimate("2001", "QT-000029", reference="", total=999.0),
                estimate("2002", "QT-000030", reference="", total=1.0),
            ],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_number"], "QT-000030")

    def test_a_name_lookalike_never_matches(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(customer_id="9001", customer_name="Ralmax Group Inc", reference="")],
            estimates=[
                estimate("2001", customer_id="9002", customer_name="Ralmax Group Inc.", reference="")
            ],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_match_source"], audit.SOURCE_NONE)

    def test_an_ambiguous_format_po_is_never_used_to_infer_a_quote(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(reference="PO 111, PO 222")],
            estimates=[estimate("2001", "QT-000029", reference="PO 111, PO 222")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["client_po_state"], audit.PO_STATE_AMBIGUOUS)
        self.assertEqual(row["quote_match_source"], audit.SOURCE_NONE)
        self.assertIn("ambiguous_po", row["review_state"])

    def test_an_unresolvable_direct_id_is_reported(self) -> None:
        fake = FakeZoho(salesorders=[salesorder(reference="", estimate_id="8888")])
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["quote_id"], "")
        self.assertEqual(row["unresolved_quote_links"], ["salesorder.estimate_id=8888"])


class InvoiceMatchingTests(unittest.TestCase):
    def test_direct_invoice_links_are_certain(self) -> None:
        fake = FakeZoho(
            salesorders=[
                salesorder(
                    invoices=[{"invoice_id": "3001", "invoice_number": "INV-000051", "total": 1.0}]
                )
            ],
            invoices=[invoice("3001", "INV-000051")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["invoice_ids"], ["3001"])
        self.assertEqual(row["invoice_numbers"], ["INV-000051"])
        self.assertEqual(row["invoice_match_source"], audit.SOURCE_DIRECT_ID)
        self.assertEqual(row["invoice_confidence"], audit.CONFIDENCE_CERTAIN)

    def test_reverse_invoice_link_works(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder("1001", reference="")],
            invoices=[invoice("3001", reference="", salesorder_id="1001")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["invoice_ids"], ["3001"])
        self.assertEqual(row["invoice_match_source"], audit.SOURCE_REVERSE_DIRECT_ID)

    def test_multiple_direct_invoices_are_all_reported(self) -> None:
        fake = FakeZoho(
            salesorders=[
                salesorder(
                    invoices=[
                        {"invoice_id": "3001", "invoice_number": "INV-000051"},
                        {"invoice_id": "3002", "invoice_number": "INV-000052"},
                    ]
                )
            ],
            invoices=[invoice("3001", "INV-000051"), invoice("3002", "INV-000052", date="2026-08-06")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["invoice_ids"], ["3001", "3002"])
        self.assertEqual(row["invoice_confidence"], audit.CONFIDENCE_CERTAIN)

    def test_unique_customer_and_po_infers_an_invoice(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(reference="PO 26330")],
            invoices=[invoice("3001", reference="26330")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["invoice_ids"], ["3001"])
        self.assertEqual(row["invoice_match_source"], audit.SOURCE_CUSTOMER_AND_EXACT_PO)
        self.assertEqual(row["invoice_confidence"], audit.CONFIDENCE_STRONG)

    def test_duplicate_invoice_po_candidates_are_ambiguous(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(reference="PO 26330")],
            invoices=[invoice("3001", "INV-000051", reference="PO 26330"),
                      invoice("3002", "INV-000052", reference="26330")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["invoice_confidence"], audit.CONFIDENCE_AMBIGUOUS)
        self.assertIn("ambiguous_invoice", row["review_state"])

    def test_a_different_customer_with_the_same_po_is_not_an_invoice_match(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(customer_id="9001", reference="PO26330")],
            invoices=[invoice("3001", customer_id="9002", reference="PO26330")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["invoice_ids"], [])
        self.assertIn("missing_invoice", row["review_state"])


class ReviewStateTests(unittest.TestCase):
    def test_complete_when_po_quote_and_invoice_all_resolve(self) -> None:
        fake = FakeZoho(
            salesorders=[
                salesorder(
                    estimate_id="2001",
                    invoices=[{"invoice_id": "3001", "invoice_number": "INV-000051"}],
                )
            ],
            estimates=[estimate("2001")],
            invoices=[invoice("3001")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["review_state"], audit.REVIEW_STATE_COMPLETE)
        self.assertIn("no correction needed", row["recommended_correction"])

    def test_missing_everything_produces_a_combined_state(self) -> None:
        fake = FakeZoho(salesorders=[salesorder(reference="")])
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["review_state"], "missing_po+missing_quote+missing_invoice")
        self.assertEqual(row["client_po_state"], audit.PO_STATE_MISSING)

    def test_review_state_components_come_from_the_closed_list(self) -> None:
        fake = FakeZoho(
            salesorders=[
                salesorder("1001", "SO-00001", reference=""),
                salesorder("1002", "SO-00002", reference="PO 111, PO 222"),
                salesorder("1003", "SO-00003", reference="PO26330"),
            ],
            estimates=[
                estimate("2001", "QT-000029", reference="PO26330"),
                estimate("2002", "QT-000030", reference="26330"),
            ],
        )
        for row in run(fake)["rows"]:
            for component in row["review_state"].split("+"):
                self.assertIn(
                    component,
                    set(audit.REVIEW_STATE_ORDER) | {audit.REVIEW_STATE_COMPLETE},
                )

    def test_recommendation_is_words_and_never_an_update_payload(self) -> None:
        fake = FakeZoho(salesorders=[salesorder(reference="")])
        row = row_for(run(fake), "SO-00001")
        text = row["recommended_correction"]
        self.assertIsInstance(text, str)
        for forbidden in ("{", "}", "JSONString", "reference_number=", "payload"):
            self.assertNotIn(forbidden, text)


class SummaryTests(unittest.TestCase):
    def test_summary_counts_agree_with_the_rows(self) -> None:
        fake = FakeZoho(
            salesorders=[
                salesorder("1001", "SO-00001", estimate_id="2001", date="2026-05-01"),
                salesorder("1002", "SO-00002", reference="", date="2026-06-01"),
                salesorder("1003", "SO-00003", reference="PO 26330", date="2026-07-01"),
                salesorder("1004", "SO-00004", reference="PO-777", date="2026-08-01"),
            ],
            estimates=[
                estimate("2001", "QT-000029", reference="ANY"),
                estimate("2002", "QT-000030", reference="PO 26330"),
                estimate("2003", "QT-000031", reference="26330"),
                estimate("2004", "QT-000032", reference="777"),
            ],
        )
        result = run(fake)
        summary = result["summary"]
        self.assertEqual(summary["salesorders_total"], 4)
        self.assertEqual(summary["missing_po"], 1)
        self.assertEqual(summary["direct_quote_links"], 1)
        self.assertEqual(summary["ambiguous_quote"], 1)
        self.assertEqual(summary["inferred_exact_po_quote_links"], 1)
        self.assertEqual(summary["date_range"], {"earliest": "2026-05-01", "latest": "2026-08-01"})
        self.assertEqual(summary["zoho_writes"], 0)
        self.assertEqual(
            summary["needs_manual_review"],
            sum(1 for row in result["rows"] if row["review_state"] != audit.REVIEW_STATE_COMPLETE),
        )
        self.assertEqual(sum(summary["quote_match_sources"].values()), 4)
        self.assertEqual(sum(summary["review_states"].values()), 4)

    def test_orders_with_linked_invoices_is_counted(self) -> None:
        fake = FakeZoho(
            salesorders=[
                salesorder("1001", "SO-00001", invoices=[{"invoice_id": "3001", "invoice_number": "INV-000051"}]),
                salesorder("1002", "SO-00002", reference=""),
            ],
            invoices=[invoice("3001")],
        )
        summary = run(fake)["summary"]
        self.assertEqual(summary["orders_with_linked_invoices"], 1)
        self.assertEqual(summary["missing_invoice"], 1)


class BatchTests(unittest.TestCase):
    def test_no_batch_file_holds_more_than_twenty_records(self) -> None:
        orders = [salesorder(str(1000 + index), f"SO-{index:05d}") for index in range(45)]
        with tempfile.TemporaryDirectory() as temporary:
            result = run(FakeZoho(salesorders=orders), temporary)
            paths = result["batch_paths"]["salesorders"]
            self.assertEqual(len(paths), 3)
            sizes = []
            for path in paths:
                payload = json.loads(Path(path).read_text(encoding="utf-8"))
                self.assertLessEqual(len(payload["records"]), audit.BATCH_SIZE)
                self.assertEqual(payload["batch_records"], len(payload["records"]))
                sizes.append(len(payload["records"]))
            self.assertEqual(sizes, [20, 20, 5])
            self.assertEqual(sum(sizes), 45)

    def test_a_larger_batch_size_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(audit.AuditError):
                audit.write_batches(Path(temporary), "salesorders", [], batch_size=21)

    def test_every_collection_is_batched(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder()], estimates=[estimate()], invoices=[invoice()]
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = run(fake, temporary)
            for name in ("salesorders", "estimates", "invoices"):
                self.assertTrue(result["batch_paths"][name])
                for path in result["batch_paths"][name]:
                    self.assertTrue(Path(path).exists())


class OutputTests(unittest.TestCase):
    def build(self, temporary):
        fake = FakeZoho(
            salesorders=[
                salesorder("1001", "SO-00001", estimate_id="2001",
                           invoices=[{"invoice_id": "3001", "invoice_number": "INV-000051"}]),
                salesorder("1002", "SO-00002", reference="", date="2026-08-02"),
            ],
            estimates=[estimate("2001")],
            invoices=[invoice("3001")],
        )
        return run(fake, temporary)

    def test_three_reports_are_written_and_agree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.build(temporary)
            paths = result["paths"]
            report = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["salesorders_total"], 2)
            self.assertEqual(len(report["salesorders"]), 2)
            self.assertFalse(report["zoho_modified"])
            self.assertTrue(report["read_only"])

            with Path(paths["csv"]).open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(list(rows[0]), list(audit.REPORT_COLUMNS))

            markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
            self.assertIn("| Sales Orders | 2 |", markdown)
            self.assertIn("0 Zoho writes", markdown)

    def test_every_salesorder_appears_exactly_once_in_every_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.build(temporary)
            ids = [row["salesorder_id"] for row in result["rows"]]
            self.assertEqual(sorted(ids), ["1001", "1002"])
            self.assertEqual(len(ids), len(set(ids)))
            with Path(result["paths"]["csv"]).open(encoding="utf-8-sig", newline="") as handle:
                csv_ids = [row["salesorder_id"] for row in csv.DictReader(handle)]
            self.assertEqual(sorted(csv_ids), sorted(ids))

    def test_report_columns_carry_no_financial_field(self) -> None:
        for column in audit.REPORT_COLUMNS:
            lowered = column.casefold()
            for token in audit.FINANCIAL_KEY_TOKENS:
                self.assertNotIn(token, lowered, f"{column} carries {token}")

    def test_no_financial_key_or_value_reaches_any_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.build(temporary)
            for path in result["paths"].values():
                text = Path(path).read_text(encoding="utf-8-sig")
                for leaked in ("105.42", "100.40", "11165.88", "1454.31", "5.02"):
                    self.assertNotIn(leaked, text, f"{leaked} leaked into {path}")
            report = json.loads(Path(result["paths"]["json"]).read_text(encoding="utf-8"))

            def walk_keys(node):
                if isinstance(node, dict):
                    for key, value in node.items():
                        yield key
                        yield from walk_keys(value)
                elif isinstance(node, list):
                    for value in node:
                        yield from walk_keys(value)

            for key in walk_keys(report["salesorders"]):
                lowered = str(key).casefold()
                for token in audit.FINANCIAL_KEY_TOKENS:
                    self.assertNotIn(token, lowered, f"{key} carries {token}")

    def test_csv_joins_lists_with_semicolons(self) -> None:
        fake = FakeZoho(
            salesorders=[salesorder(reference="PO 26330")],
            estimates=[estimate("2001", "QT-000029", reference="PO 26330"),
                       estimate("2002", "QT-000030", reference="26330")],
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = run(fake, temporary)
            with Path(result["paths"]["csv"]).open(encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(sorted(row["quote_candidates"].split(";")), ["QT-000029", "QT-000030"])
            self.assertEqual(row["quote_confidence"], audit.CONFIDENCE_AMBIGUOUS)


class ReceiptTests(unittest.TestCase):
    def test_the_receipt_identifies_all_three_report_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipts = Path(temporary) / "receipts.jsonl"
            paths = {
                "json": str(Path(temporary) / audit.JSON_REPORT_NAME),
                "csv": str(Path(temporary) / audit.CSV_REPORT_NAME),
                "markdown": str(Path(temporary) / audit.MARKDOWN_REPORT_NAME),
            }
            with patch.object(audit, "RECEIPTS", receipts):
                audit.append_receipt(paths, {"salesorders_total": 7})
            record = json.loads(receipts.read_text(encoding="utf-8").strip())
            self.assertEqual(record["action"], "zoho_sales_order_reference_audit_read_only")
            self.assertEqual(record["evidence"], [paths["json"], paths["csv"], paths["markdown"]])
            self.assertEqual(record["zoho_writes"], 0)
            self.assertEqual(record["salesorders_total"], 7)


class ReadOnlySurfaceTests(unittest.TestCase):
    SOURCE = Path(audit.__file__).read_text(encoding="utf-8")

    def test_the_source_holds_no_write_verb_or_transport(self) -> None:
        for token in (
            '"POST"',
            '"PUT"',
            '"PATCH"',
            '"DELETE"',
            "method=",
            "urlopen",
            "Request(",
            "requests.post",
            "requests.put",
            "http.client",
            "smtplib",
            "sendmail",
            "send_message",
            "EmailMessage",
            "playwright",
            "connect_over_cdp",
            "selenium",
            "webbrowser",
            "subprocess",
        ):
            self.assertNotIn(token, self.SOURCE, f"{token} must not appear in a read-only audit")

    def test_the_only_zoho_entry_point_is_the_get_only_helper(self) -> None:
        self.assertEqual(self.SOURCE.count("zoho_tool.api_get("), 1)
        for token in ("api_post", "api_put", "api_delete", "commit", "approval", "APPROVED"):
            self.assertNotIn(token, self.SOURCE)

    def test_no_company_filtering_construct_exists(self) -> None:
        lowered = self.SOURCE.casefold()
        for token in ("def forbidden", "tdi_filter", "withheld", "quarantine", "company_wall"):
            self.assertNotIn(token, lowered)

    def test_a_customer_named_like_the_sibling_company_is_audited_normally(self) -> None:
        fake = FakeZoho(
            salesorders=[
                salesorder("1001", "SO-00001", customer_id="96274000000060019",
                           customer_name="Troy Dualam Services Inc.", estimate_id="2001")
            ],
            estimates=[estimate("2001", customer_id="96274000000060019",
                                customer_name="Troy Dualam Services Inc.")],
        )
        row = row_for(run(fake), "SO-00001")
        self.assertEqual(row["customer_name"], "Troy Dualam Services Inc.")
        self.assertEqual(row["quote_number"], "QT-000029")

    def test_the_credential_vault_is_never_written(self) -> None:
        self.assertNotIn("save_vault", self.SOURCE)

    def test_run_audit_never_reads_a_credential(self) -> None:
        with patch.object(audit.zoho_tool, "load_vault", side_effect=AssertionError("vault read")):
            with patch.object(
                audit.zoho_tool, "refresh_access_token", side_effect=AssertionError("token read")
            ):
                result = run(FakeZoho(salesorders=[salesorder()]))
        self.assertEqual(result["summary"]["salesorders_total"], 1)


if __name__ == "__main__":
    unittest.main()
