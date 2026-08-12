#!/usr/bin/env python
"""Tests for the READ-ONLY historical client PO evidence recovery.

Every Microsoft Graph, Zoho and Google Drive access is mocked. No test reaches
a network, a live vault, the real mailbox or the real reference cache.
"""
from __future__ import annotations

import ast
import base64
import io
import json
import sys
import tokenize
import unittest
import urllib.parse
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
OUTLOOK_TOOLS = TOOLS.parent / "outlook"
if str(OUTLOOK_TOOLS) not in sys.path:
    sys.path.insert(0, str(OUTLOOK_TOOLS))

import zoho_client_po_recovery as recovery  # noqa: E402

MODULE_PATH = Path(recovery.__file__)
GRAPH_BASE = recovery.GRAPH_BASE
CUSTOMER_DOMAIN = "sctfrp.com"
CUSTOMER_ADDRESS = f"buyer@{CUSTOMER_DOMAIN}"
INTERNAL_ADDRESS = "info@frpdepots.com"


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def make_order(
    number: str,
    order_id: str,
    reference: str = "",
    quote_number: str = "",
    quote_id: str = "",
    order_date: str = "2026-01-15",
    customer_id: str = "CUST1",
    customer_name: str = "Structural Composites Technologies Ltd",
) -> dict:
    return {
        "salesorder_id": order_id,
        "salesorder_number": number,
        "date": order_date,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "client_po_original": reference,
        "quote_id": quote_id,
        "quote_number": quote_number,
    }


def make_message(
    message_id: str,
    subject: str = "RE: Flange",
    body: str = "",
    sender: str = CUSTOMER_ADDRESS,
    received: str = "2026-01-14T10:00:00Z",
    has_attachments: bool = False,
    recipients: tuple[str, ...] = (INTERNAL_ADDRESS,),
    preview: str | None = None,
) -> dict:
    return {
        "id": message_id,
        "conversationId": f"conv-{message_id}",
        "subject": subject,
        "bodyPreview": preview if preview is not None else body[:255],
        "body": {"contentType": "text", "content": body},
        "receivedDateTime": received,
        "hasAttachments": has_attachments,
        "from": {"emailAddress": {"address": sender}},
        "toRecipients": [{"emailAddress": {"address": address}} for address in recipients],
        "ccRecipients": [],
    }


def write_audit(directory: Path, orders: list[dict], **overrides) -> Path:
    payload = {
        "tool": "zoho_order_reference_audit",
        "read_only": True,
        "zoho_modified": False,
        "salesorders": orders,
    }
    payload.update(overrides)
    path = directory / "order_reference_audit.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class FakeGraph:
    """A stand-in for the GET-only Graph reader."""

    def __init__(
        self,
        results_by_term: dict[str, list[dict]] | None = None,
        attachments: dict[str, list[dict]] | None = None,
        attachment_payloads: dict[str, dict] | None = None,
        page_size: int | None = None,
        endless: bool = False,
    ) -> None:
        self.results_by_term = results_by_term or {}
        self.attachments = attachments or {}
        self.attachment_payloads = attachment_payloads or {}
        self.page_size = page_size
        self.endless = endless
        self.paths: list[str] = []
        self.body_reads: list[str] = []

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _query(path: str) -> dict:
        return urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)

    def _messages_by_id(self) -> dict[str, dict]:
        index: dict[str, dict] = {}
        for messages in self.results_by_term.values():
            for message in messages:
                index[message["id"]] = message
        return index

    @staticmethod
    def _search_projection(message: dict) -> dict:
        keep = (
            "id",
            "conversationId",
            "subject",
            "bodyPreview",
            "receivedDateTime",
            "hasAttachments",
            "from",
            "toRecipients",
            "ccRecipients",
        )
        return {key: message[key] for key in keep if key in message}

    # -- transport -----------------------------------------------------
    def __call__(self, path: str) -> dict:
        self.paths.append(path)
        if path.startswith("/me/messages?$search=") or path.startswith("/me/messages?$skiptoken="):
            return self._search(path)
        if "/attachments/" in path:
            attachment_id = path.rsplit("/", 1)[-1].split("?")[0]
            return self.attachment_payloads.get(urllib.parse.unquote(attachment_id), {})
        if "/attachments" in path:
            message_id = urllib.parse.unquote(path.split("/me/messages/")[1].split("/attachments")[0])
            return {"value": self.attachments.get(message_id, [])}
        message_id = urllib.parse.unquote(path.split("/me/messages/")[1].split("?")[0])
        self.body_reads.append(message_id)
        return self._messages_by_id().get(message_id, {})

    def _search(self, path: str) -> dict:
        query = self._query(path)
        if "$skiptoken" in query:
            term, offset = query["$skiptoken"][0].split("|")
            offset = int(offset)
        else:
            term = query["$search"][0].strip('"')
            offset = 0
        messages = [self._search_projection(message) for message in self.results_by_term.get(term, [])]
        if self.endless:
            return {
                "value": messages,
                "@odata.nextLink": f"{GRAPH_BASE}/me/messages?$skiptoken={urllib.parse.quote(term)}|{offset + 1}",
            }
        if self.page_size is None:
            return {"value": messages}
        page = messages[offset:offset + self.page_size]
        result: dict = {"value": page}
        if offset + self.page_size < len(messages):
            result["@odata.nextLink"] = (
                f"{GRAPH_BASE}/me/messages?$skiptoken={urllib.parse.quote(term)}|{offset + self.page_size}"
            )
        return result


def zoho_reader(domains: dict[str, list[str]] | None = None):
    domains = domains if domains is not None else {"CUST1": [CUSTOMER_ADDRESS]}
    calls: list[str] = []

    def read(path: str) -> dict:
        calls.append(path)
        customer_id = path.rsplit("/", 1)[-1].split("?")[0]
        return {
            "contact": {
                "contact_id": customer_id,
                "email": (domains.get(customer_id) or [""])[0],
                "contact_persons": [
                    {"email": address} for address in (domains.get(customer_id) or [])[1:]
                ],
            }
        }

    read.calls = calls  # type: ignore[attr-defined]
    return read


def run(
    orders: list[dict],
    graph: FakeGraph,
    directory: Path,
    zoho_get=None,
    drive_connection=None,
    audit_overrides: dict | None = None,
) -> dict:
    audit_path = write_audit(directory, orders, **(audit_overrides or {}))
    return recovery.run_recovery(
        graph,
        zoho_get or zoho_reader(),
        audit_path,
        directory / "out",
        directory / "transient",
        drive_connection=drive_connection,
        generated_utc="2026-08-11T00:00:00+00:00",
    )


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------
class TestAffectedSelection(unittest.TestCase):
    def test_quote_reference_is_affected(self):
        self.assertTrue(recovery.is_affected(make_order("SO-00002", "1", reference="QT-000003")))

    def test_blank_reference_is_affected(self):
        self.assertTrue(recovery.is_affected(make_order("SO-00006", "1", reference="")))
        self.assertTrue(recovery.is_affected(make_order("SO-00006", "1", reference="   ")))

    def test_real_customer_po_is_not_affected(self):
        for reference in ("PO5117", "PO 104689", "234", "PO5171/ 26-368", "MTI"):
            self.assertFalse(recovery.is_affected(make_order("SO-1", "1", reference=reference)), reference)

    def test_quote_reference_variants_are_recognised(self):
        for reference in ("QT-000003", "qt-000003", "QT 000003", "QT000003", " QT-000003 "):
            self.assertTrue(recovery.is_affected(make_order("SO-1", "1", reference=reference)), reference)

    def test_selection_is_sorted_by_date_then_number(self):
        orders = [
            make_order("SO-00051", "b", order_date="2026-08-11"),
            make_order("SO-00006", "c", order_date="2026-01-05"),
            make_order("SO-00052", "a", order_date="2026-08-11"),
        ]
        selected = recovery.select_affected(orders)
        self.assertEqual([row["salesorder_number"] for row in selected], ["SO-00006", "SO-00051", "SO-00052"])

    def test_duplicate_sales_order_id_is_refused(self):
        orders = [make_order("SO-1", "same"), make_order("SO-2", "same")]
        with self.assertRaises(recovery.RecoveryError):
            recovery.select_affected(orders)

    def test_missing_sales_order_id_is_refused(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.select_affected([make_order("SO-1", "")])

    def test_exactly_twenty_seven_orders_when_the_fixture_matches_the_real_shape(self):
        orders = [
            make_order(f"SO-{index:05d}", f"id{index}", reference=f"QT-{index:06d}", quote_number=f"QT-{index:06d}")
            for index in range(1, 21)
        ]
        orders += [make_order(f"SO-{index:05d}", f"blank{index}", reference="") for index in range(21, 28)]
        orders += [make_order(f"SO-{index:05d}", f"ok{index}", reference=f"PO{5000 + index}") for index in range(28, 45)]
        selected = recovery.select_affected(orders)
        self.assertEqual(len(selected), 27)
        self.assertEqual(sum(1 for row in selected if recovery.is_frp_quote_number(row["client_po_original"])), 20)
        self.assertEqual(sum(1 for row in selected if not row["client_po_original"]), 7)

    def test_audit_that_is_not_read_only_is_refused(self):
        with TemporaryDirectory() as temporary:
            path = write_audit(Path(temporary), [make_order("SO-1", "1")], read_only=False)
            with self.assertRaises(recovery.RecoveryError):
                recovery.load_audit(path)

    def test_audit_that_reports_a_zoho_change_is_refused(self):
        with TemporaryDirectory() as temporary:
            path = write_audit(Path(temporary), [make_order("SO-1", "1")], zoho_modified=True)
            with self.assertRaises(recovery.RecoveryError):
                recovery.load_audit(path)

    def test_missing_audit_is_refused(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.load_audit(Path("C:/FRPDepot/does/not/exist.json"))


# --------------------------------------------------------------------------
# PO extraction and rejection
# --------------------------------------------------------------------------
class TestPoExtraction(unittest.TestCase):
    def values(self, text: str) -> list[str]:
        return [candidate["value"] for candidate in recovery.extract_po_candidates(text)]

    def test_labelled_forms_are_extracted(self):
        self.assertIn("104689", self.values("Our PO number 104689 is attached"))
        self.assertIn("26330", self.values("Purchase Order: 26330"))
        self.assertIn("25592", self.values("PO # 25592 for the flange"))
        self.assertIn("5117", self.values("P.O. 5117"))

    def test_glued_form_is_extracted_with_its_prefix(self):
        self.assertIn("PO5117", self.values("Please ship against PO5117 today"))
        self.assertIn("PO104689", self.values("see PO104689"))

    def test_subject_line_form_is_extracted(self):
        self.assertIn("PO25592", self.values("RE: Flange - PO25592"))

    def test_an_unlabelled_number_is_never_a_candidate(self):
        self.assertEqual(self.values("Please ship 26330 pieces tomorrow"), [])
        self.assertEqual(self.values("Job 5117 is ready"), [])

    def test_frp_document_numbers_are_rejected(self):
        for text in ("PO QT-000029", "purchase order SO-00020", "PO: INV-000051"):
            self.assertEqual(self.values(text), [], text)

    def test_reject_reason_names_frp_documents(self):
        self.assertIn("quote", recovery.reject_reason("QT-000029"))
        self.assertIn("sales order", recovery.reject_reason("SO-00020"))
        self.assertIn("invoice", recovery.reject_reason("INV-000051"))

    def test_dates_are_rejected(self):
        for value in ("2026-08-11", "08/11/2026", "2026", "11-08-26"):
            self.assertTrue(recovery.reject_reason(value), value)
        self.assertEqual(self.values("PO 2026-08-11"), [])

    def test_amounts_are_rejected(self):
        for value in ("105.42", "$100", "1,234.56"):
            self.assertTrue(recovery.reject_reason(value), value)
        self.assertEqual(self.values("purchase order 105.42"), [])

    def test_english_words_are_rejected(self):
        for text in ("purchase order number will follow", "PO attached", "purchase order to be confirmed"):
            self.assertEqual(self.values(text), [], text)

    def test_a_value_without_a_digit_is_rejected(self):
        self.assertTrue(recovery.reject_reason("ABCDEF"))
        self.assertEqual(self.values("purchase order ASAP"), [])

    def test_a_value_too_short_is_rejected(self):
        self.assertTrue(recovery.reject_reason("A1"))

    def test_candidates_are_deduplicated_and_ordered(self):
        values = self.values("PO 5117 and again PO 5117 then PO 5118")
        self.assertEqual(values, ["5117", "5118"])

    def test_excerpt_is_capped_and_redacted(self):
        text = "x" * 400 + " Our purchase order 26330 total CAD 1,234.56 thanks " + "y" * 400
        candidates = recovery.extract_po_candidates(text)
        self.assertEqual(len(candidates), 1)
        excerpt = candidates[0]["excerpt"]
        self.assertLessEqual(len(excerpt), recovery.MAX_EXCERPT_CHARS)
        self.assertIn("26330", excerpt)
        self.assertNotIn("1,234.56", excerpt)
        self.assertIn(recovery.REDACTION, excerpt)

    def test_redaction_covers_the_common_money_forms(self):
        redacted = recovery.redact_amounts("total CAD 100.40 or $50.20 or 13.05")
        self.assertNotIn("100.40", redacted)
        self.assertNotIn("50.20", redacted)
        self.assertNotIn("13.05", redacted)

    def test_frp_generated_document_is_recognised(self):
        self.assertTrue(recovery.looks_frp_generated("FRP Depot\nQuote # QT-000029\nItem"))
        self.assertFalse(recovery.looks_frp_generated("Structural Composites\nPurchase Order 26330"))
        self.assertFalse(recovery.looks_frp_generated("FRP Depot is our supplier, our PO 26330"))


# --------------------------------------------------------------------------
# customer identity
# --------------------------------------------------------------------------
class TestCustomerIdentity(unittest.TestCase):
    def test_corporate_domains_are_kept_and_generic_ones_dropped(self):
        contact = {
            "email": "buyer@sctfrp.com",
            "contact_persons": [{"email": "someone@gmail.com"}, {"email": "ap@sctfrp.com"}, {"email": "x@frpdepots.com"}],
        }
        self.assertEqual(recovery.contact_email_domains(contact), ["sctfrp.com"])

    def test_customer_name_phrase_drops_legal_suffixes(self):
        self.assertEqual(recovery.customer_name_phrase("Structural Composites Technologies Ltd"), "Structural Composites Technologies")
        self.assertEqual(recovery.customer_name_phrase("Troy Dualam Services Inc."), "Troy Dualam")
        self.assertEqual(recovery.customer_name_phrase("Ralmax Group of Companies"), "Ralmax")

    def test_internal_address_is_recognised(self):
        self.assertTrue(recovery.is_internal_address(INTERNAL_ADDRESS))
        self.assertFalse(recovery.is_internal_address(CUSTOMER_ADDRESS))

    def test_customer_involvement_looks_at_every_participant(self):
        message = make_message("m1", sender=INTERNAL_ADDRESS, recipients=(CUSTOMER_ADDRESS,))
        self.assertTrue(recovery.message_involves_customer(message, {CUSTOMER_DOMAIN}))
        self.assertFalse(recovery.message_involves_customer(message, {"other.com"}))
        self.assertFalse(recovery.message_involves_customer(message, set()))

    def test_window_bounds_are_inclusive(self):
        order_day = date(2026, 1, 15)
        self.assertTrue(recovery.within_window(date(2026, 1, 1), order_day, 14, 14))
        self.assertTrue(recovery.within_window(date(2026, 1, 29), order_day, 14, 14))
        self.assertFalse(recovery.within_window(date(2025, 12, 31), order_day, 14, 14))
        self.assertFalse(recovery.within_window(None, order_day, 14, 14))


# --------------------------------------------------------------------------
# pagination and dedupe
# --------------------------------------------------------------------------
class TestPaginationAndDedupe(unittest.TestCase):
    def test_every_page_is_followed(self):
        messages = [make_message(f"m{index}") for index in range(5)]
        graph = FakeGraph({"QT-000009": messages}, page_size=2)
        collected = recovery.search_messages(graph, "QT-000009")
        self.assertEqual([message["id"] for message in collected], [f"m{index}" for index in range(5)])

    def test_the_paging_ceiling_fails_closed(self):
        graph = FakeGraph({"QT-000009": [make_message("m1")]}, endless=True)
        with self.assertRaises(recovery.RecoveryError) as caught:
            recovery.search_messages(graph, "QT-000009")
        self.assertIn("paging ceiling", str(caught.exception))

    def test_a_next_link_outside_graph_is_refused(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.relative_graph_path("https://evil.example.com/me/messages?$skiptoken=1")

    def test_a_next_link_inside_graph_is_made_relative(self):
        self.assertEqual(recovery.relative_graph_path(f"{GRAPH_BASE}/me/messages?x=1"), "/me/messages?x=1")

    def test_a_message_found_by_two_queries_is_read_once(self):
        message = make_message("m1", subject="RE: Flange - PO25592")
        graph = FakeGraph({"QT-000009": [message], "SO-00010": [message]})
        with TemporaryDirectory() as temporary:
            result = run(
                [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                graph,
                Path(temporary),
            )
        self.assertEqual(result["summary"]["messages_returned"], 2)
        self.assertEqual(result["summary"]["messages_unique"], 1)
        self.assertEqual(result["summary"]["message_bodies_read"], 1)
        self.assertEqual(result["rows"][0]["recovered_client_po"], "PO25592")

    def test_a_repeated_query_term_is_searched_once(self):
        orders = [
            make_order("SO-1", "a", reference="QT-000009", quote_number="QT-000009"),
            make_order("SO-2", "b", reference="QT-000009", quote_number="QT-000009"),
        ]
        queries = recovery.build_queries(orders, {"CUST1": [CUSTOMER_DOMAIN]})
        terms = [query["term"] for query in queries]
        self.assertEqual(len(terms), len(set(terms)))
        quote_query = next(query for query in queries if query["term"] == "QT-000009")
        self.assertEqual(quote_query["salesorder_ids"], ["a", "b"])

    def test_query_plan_is_deterministic(self):
        orders = [make_order("SO-1", "a", reference="QT-000009", quote_number="QT-000009")]
        first = recovery.build_queries(orders, {"CUST1": [CUSTOMER_DOMAIN]})
        second = recovery.build_queries(orders, {"CUST1": [CUSTOMER_DOMAIN]})
        self.assertEqual(first, second)
        self.assertEqual(
            [query["term"] for query in first],
            sorted((query["term"] for query in first), key=str.casefold),
        )


# --------------------------------------------------------------------------
# evidence rules
# --------------------------------------------------------------------------
class TestEvidenceRules(unittest.TestCase):
    def test_customer_message_yields_a_certain_recovery(self):
        graph = FakeGraph({"QT-000009": [make_message("m1", body="Please proceed against our purchase order 25592.")]})
        with TemporaryDirectory() as temporary:
            result = run(
                [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009", quote_id="q1")],
                graph,
                Path(temporary),
            )
        row = result["rows"][0]
        self.assertEqual(row["recovered_client_po"], "25592")
        self.assertEqual(row["confidence"], recovery.CONFIDENCE_CERTAIN)
        self.assertEqual(row["evidence_source_type"], recovery.SOURCE_MESSAGE_LINKED)
        self.assertEqual(row["evidence_sender"], CUSTOMER_ADDRESS)
        self.assertEqual(row["recommended_action"], recovery.ACTION_REPLACE_QUOTE)

    def test_frp_authored_message_alone_is_not_evidence(self):
        graph = FakeGraph(
            {"QT-000009": [make_message("m1", sender=INTERNAL_ADDRESS, recipients=(CUSTOMER_ADDRESS,), body="Booked against PO 99999.")]}
        )
        with TemporaryDirectory() as temporary:
            result = run(
                [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                graph,
                Path(temporary),
            )
        row = result["rows"][0]
        self.assertEqual(row["confidence"], recovery.CONFIDENCE_NONE)
        self.assertEqual(row["recovered_client_po"], "")
        self.assertEqual(row["evidence_source_type"], recovery.SOURCE_NONE)
        self.assertEqual(row["recommended_action"], recovery.ACTION_LEAVE_UNCHANGED)

    def test_a_message_from_a_stranger_is_not_customer_evidence(self):
        graph = FakeGraph({"QT-000009": [make_message("m1", sender="someone@unrelated.com", body="our PO 77777")]})
        with TemporaryDirectory() as temporary:
            result = run(
                [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                graph,
                Path(temporary),
            )
        self.assertEqual(result["rows"][0]["confidence"], recovery.CONFIDENCE_NONE)

    def test_conflicting_purchase_orders_are_never_chosen_between(self):
        graph = FakeGraph(
            {
                "QT-000009": [
                    make_message("m1", body="our purchase order 25592"),
                    make_message("m2", body="correction, use purchase order 25593", received="2026-01-15T09:00:00Z"),
                ]
            }
        )
        with TemporaryDirectory() as temporary:
            result = run(
                [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                graph,
                Path(temporary),
            )
        row = result["rows"][0]
        self.assertEqual(row["confidence"], recovery.CONFIDENCE_AMBIGUOUS)
        self.assertEqual(row["recovered_client_po"], "")
        self.assertEqual(row["conflict_candidates"], ["25592", "25593"])
        self.assertEqual(row["recommended_action"], recovery.ACTION_MANUAL_REVIEW)

    def test_no_match_leaves_the_order_unchanged(self):
        graph = FakeGraph({})
        with TemporaryDirectory() as temporary:
            result = run([make_order("SO-00006", "o1", reference="")], graph, Path(temporary))
        row = result["rows"][0]
        self.assertEqual(row["confidence"], recovery.CONFIDENCE_NONE)
        self.assertEqual(row["evidence_source_type"], recovery.SOURCE_NONE)
        self.assertEqual(row["recommended_action"], recovery.ACTION_LEAVE_UNCHANGED)

    def test_a_blank_reference_recovery_is_a_fill_not_a_replace(self):
        graph = FakeGraph({"SO-00006": [make_message("m1", body="our purchase order 31005")]})
        with TemporaryDirectory() as temporary:
            result = run([make_order("SO-00006", "o1", reference="")], graph, Path(temporary))
        row = result["rows"][0]
        self.assertEqual(row["recovered_client_po"], "31005")
        self.assertEqual(row["recommended_action"], recovery.ACTION_FILL_BLANK)

    def test_linked_evidence_outranks_window_evidence(self):
        linked = make_message("m1", body="our purchase order 25592")
        window = make_message("m2", body="our purchase order 99999", received="2026-01-10T10:00:00Z")
        graph = FakeGraph({"QT-000009": [linked], "Structural Composites Technologies": [window]})
        with TemporaryDirectory() as temporary:
            result = run(
                [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                graph,
                Path(temporary),
            )
        row = result["rows"][0]
        self.assertEqual(row["recovered_client_po"], "25592")
        self.assertEqual(row["evidence_source_type"], recovery.SOURCE_MESSAGE_LINKED)

    def test_window_evidence_is_used_when_nothing_is_linked(self):
        window = make_message("m2", body="our purchase order 31007", received="2026-01-10T10:00:00Z")
        graph = FakeGraph({"Structural Composites Technologies": [window]})
        with TemporaryDirectory() as temporary:
            result = run([make_order("SO-00006", "o1", reference="")], graph, Path(temporary))
        row = result["rows"][0]
        self.assertEqual(row["recovered_client_po"], "31007")
        self.assertEqual(row["evidence_source_type"], recovery.SOURCE_MESSAGE_WINDOW)

    def test_window_evidence_outside_the_tight_window_is_ignored(self):
        stale = make_message("m2", body="our purchase order 31007", received="2025-09-01T10:00:00Z")
        graph = FakeGraph({"Structural Composites Technologies": [stale]})
        with TemporaryDirectory() as temporary:
            result = run([make_order("SO-00006", "o1", reference="")], graph, Path(temporary))
        self.assertEqual(result["rows"][0]["confidence"], recovery.CONFIDENCE_NONE)

    def test_a_busy_customer_window_lands_on_ambiguous_rather_than_guessing(self):
        graph = FakeGraph(
            {
                "Structural Composites Technologies": [
                    make_message("m1", body="our purchase order 5117", received="2026-01-10T10:00:00Z"),
                    make_message("m2", body="our purchase order 5118", received="2026-01-12T10:00:00Z"),
                ]
            }
        )
        with TemporaryDirectory() as temporary:
            result = run([make_order("SO-00006", "o1", reference="")], graph, Path(temporary))
        row = result["rows"][0]
        self.assertEqual(row["confidence"], recovery.CONFIDENCE_AMBIGUOUS)
        self.assertEqual(row["conflict_candidates"], ["5117", "5118"])

    def test_evidence_ordering_is_stable_across_runs(self):
        messages = [
            make_message("m2", body="our purchase order 25592", received="2026-01-14T10:00:00Z"),
            make_message("m1", body="our purchase order 25592", received="2026-01-13T10:00:00Z"),
        ]
        outputs = []
        for _attempt in range(2):
            graph = FakeGraph({"QT-000009": list(messages)})
            with TemporaryDirectory() as temporary:
                result = run(
                    [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                    graph,
                    Path(temporary),
                )
            outputs.append(result["rows"])
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0][0]["evidence_message_id"], "m1")


# --------------------------------------------------------------------------
# attachments
# --------------------------------------------------------------------------
def file_attachment(attachment_id: str, name: str, size: int = 1024, inline: bool = False, kind: str = "#microsoft.graph.fileAttachment") -> dict:
    return {
        "@odata.type": kind,
        "id": attachment_id,
        "name": name,
        "contentType": "application/pdf",
        "size": size,
        "isInline": inline,
    }


class TestAttachments(unittest.TestCase):
    def test_supported_non_inline_file_attachment_is_a_candidate(self):
        self.assertTrue(recovery.attachment_is_evidence_candidate(file_attachment("a1", "po.pdf")))

    def test_inline_image_is_not_a_candidate(self):
        self.assertFalse(recovery.attachment_is_evidence_candidate(file_attachment("a1", "image002.png", inline=True)))

    def test_item_attachment_is_not_downloaded(self):
        self.assertFalse(
            recovery.attachment_is_evidence_candidate(file_attachment("a1", "forward.msg", kind="#microsoft.graph.itemAttachment"))
        )

    def test_unsupported_type_is_not_a_candidate(self):
        self.assertFalse(recovery.attachment_is_evidence_candidate(file_attachment("a1", "model.dwg")))
        self.assertFalse(recovery.attachment_is_evidence_candidate(file_attachment("a1", "archive.zip")))

    def test_oversize_attachment_is_not_a_candidate(self):
        self.assertFalse(
            recovery.attachment_is_evidence_candidate(file_attachment("a1", "huge.pdf", size=recovery.MAX_ATTACHMENT_BYTES + 1))
        )
        self.assertTrue(
            recovery.attachment_is_evidence_candidate(file_attachment("a1", "big.pdf", size=recovery.MAX_ATTACHMENT_BYTES))
        )

    def test_path_separators_are_refused(self):
        for name in ("../escape.pdf", "..\\escape.pdf", "sub/dir.pdf", "sub\\dir.pdf"):
            with self.assertRaises(recovery.RecoveryError, msg=name):
                recovery.safe_attachment_filename(name, 1)

    def test_drive_and_stream_separators_are_refused(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.safe_attachment_filename("C:evil.pdf", 1)

    def test_control_characters_and_nulls_are_refused(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.safe_attachment_filename("bad\x00.pdf", 1)
        with self.assertRaises(recovery.RecoveryError):
            recovery.safe_attachment_filename("bad\x07name.pdf", 1)

    def test_empty_and_unsupported_names_are_refused(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.safe_attachment_filename("", 1)
        with self.assertRaises(recovery.RecoveryError):
            recovery.safe_attachment_filename("payload.exe", 1)

    def test_reserved_windows_name_is_defused(self):
        self.assertEqual(recovery.safe_attachment_filename("con.pdf", 3), "0003__con.pdf")

    def test_safe_name_keeps_the_suffix_and_prefixes_the_index(self):
        self.assertEqual(recovery.safe_attachment_filename("SCT PO 26330.pdf", 7), "0007_SCT_PO_26330.pdf")

    def test_attachment_bytes_are_extracted_then_deleted(self):
        payload = base64.b64encode(b"%PDF-1.4 purchase order 26330").decode("ascii")
        graph = FakeGraph(attachment_payloads={"a1": {"contentBytes": payload}})
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / "transient"
            with mock.patch.object(recovery, "attachment_extract_one", return_value=("Purchase Order 26330", "pdf", {})) as extractor:
                text = recovery.attachment_text(graph, "m1", file_attachment("a1", "po.pdf"), directory, 1)
            self.assertEqual(text, "Purchase Order 26330")
            written = extractor.call_args[0][0]
            self.assertTrue(str(written).startswith(str(directory)))
            self.assertFalse(written.exists())
            self.assertEqual(list(directory.iterdir()), [])

    def test_attachment_bytes_are_never_written_inside_the_repository(self):
        payload = base64.b64encode(b"data").decode("ascii")
        graph = FakeGraph(attachment_payloads={"a1": {"contentBytes": payload}})
        inside = recovery.ROOT / "Dado" / "20_Working" / "should_never_exist"
        # Clean first AND after: without the guard this call really does write
        # bytes here, and a leftover from a previous run must not mask the fix.
        self.addCleanup(lambda: __import__("shutil").rmtree(inside, ignore_errors=True))
        __import__("shutil").rmtree(inside, ignore_errors=True)
        with self.assertRaises(recovery.RecoveryError):
            recovery.attachment_text(graph, "m1", file_attachment("a1", "po.pdf"), inside, 1)
        self.assertFalse(inside.exists())

    def test_undecodable_attachment_is_refused(self):
        graph = FakeGraph(attachment_payloads={"a1": {"contentBytes": "not base64!!"}})
        with TemporaryDirectory() as temporary:
            with self.assertRaises(recovery.RecoveryError):
                recovery.attachment_text(graph, "m1", file_attachment("a1", "po.pdf"), Path(temporary), 1)

    def test_customer_attachment_produces_labelled_evidence(self):
        message = make_message("m1", subject="Flange order", body="See attached.", has_attachments=True)
        graph = FakeGraph(
            {"QT-000009": [message]},
            attachments={"m1": [file_attachment("a1", "SCT PO 26330.pdf")]},
            attachment_payloads={"a1": {"contentBytes": base64.b64encode(b"pdf").decode("ascii")}},
        )
        with TemporaryDirectory() as temporary:
            with mock.patch.object(
                recovery, "attachment_extract_one", return_value=("Structural Composites\nPurchase Order 26330\n", "pdf", {})
            ):
                result = run(
                    [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                    graph,
                    Path(temporary),
                )
        row = result["rows"][0]
        self.assertEqual(row["recovered_client_po"], "26330")
        self.assertEqual(row["evidence_source_type"], recovery.SOURCE_ATTACHMENT_LINKED)
        self.assertEqual(row["evidence_attachment_name"], "SCT PO 26330.pdf")

    def test_frp_generated_attachment_is_not_customer_evidence(self):
        message = make_message("m1", sender=INTERNAL_ADDRESS, recipients=(CUSTOMER_ADDRESS,), has_attachments=True)
        graph = FakeGraph(
            {"QT-000009": [message]},
            attachments={"m1": [file_attachment("a1", "QT-000009.pdf")]},
            attachment_payloads={"a1": {"contentBytes": base64.b64encode(b"pdf").decode("ascii")}},
        )
        with TemporaryDirectory() as temporary:
            with mock.patch.object(
                recovery, "attachment_extract_one", return_value=("FRP Depot\nQuote # QT-000009\nPO 12345", "pdf", {})
            ):
                result = run(
                    [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                    graph,
                    Path(temporary),
                )
        self.assertEqual(result["rows"][0]["confidence"], recovery.CONFIDENCE_NONE)

    def test_the_same_attachment_is_extracted_once(self):
        message = make_message("m1", has_attachments=True)
        graph = FakeGraph(
            {"QT-000009": [message], "SO-00010": [message]},
            attachments={"m1": [file_attachment("a1", "po.pdf")]},
            attachment_payloads={"a1": {"contentBytes": base64.b64encode(b"pdf").decode("ascii")}},
        )
        with TemporaryDirectory() as temporary:
            with mock.patch.object(recovery, "attachment_extract_one", return_value=("Purchase Order 26330", "pdf", {})) as extractor:
                result = run(
                    [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                    graph,
                    Path(temporary),
                )
        self.assertEqual(extractor.call_count, 1)
        self.assertEqual(result["summary"]["attachments_extracted"], 1)


# --------------------------------------------------------------------------
# reports
# --------------------------------------------------------------------------
class TestReports(unittest.TestCase):
    def build(self, temporary: Path) -> dict:
        orders = [
            make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009", quote_id="q1"),
            make_order("SO-00006", "o2", reference="", order_date="2026-01-05"),
        ]
        graph = FakeGraph({"QT-000009": [make_message("m1", body="our purchase order 25592")]})
        return run(orders, graph, temporary)

    def test_all_three_reports_agree_on_counts(self):
        with TemporaryDirectory() as temporary:
            result = self.build(Path(temporary))
            report = json.loads(Path(result["paths"]["json"]).read_text(encoding="utf-8"))
            with open(result["paths"]["csv"], newline="", encoding="utf-8-sig") as handle:
                csv_rows = list(__import__("csv").DictReader(handle))
            markdown = Path(result["paths"]["markdown"]).read_text(encoding="utf-8")
        self.assertEqual(len(report["salesorders"]), 2)
        self.assertEqual(len(csv_rows), 2)
        self.assertEqual(report["summary"]["affected_salesorders"], 2)
        self.assertEqual(markdown.count("| SO-000"), 2)
        self.assertIn("| Affected Sales Orders checked | 2 |", markdown)

    def test_csv_header_is_exactly_the_closed_column_set(self):
        with TemporaryDirectory() as temporary:
            result = self.build(Path(temporary))
            with open(result["paths"]["csv"], newline="", encoding="utf-8-sig") as handle:
                header = next(__import__("csv").reader(handle))
        self.assertEqual(header, list(recovery.REPORT_COLUMNS))

    def test_json_rows_carry_exactly_the_closed_column_set(self):
        with TemporaryDirectory() as temporary:
            result = self.build(Path(temporary))
        for row in result["rows"]:
            self.assertEqual(set(row.keys()), set(recovery.REPORT_COLUMNS))

    def test_reports_declare_zero_writes(self):
        with TemporaryDirectory() as temporary:
            result = self.build(Path(temporary))
            report = json.loads(Path(result["paths"]["json"]).read_text(encoding="utf-8"))
        self.assertIs(report["read_only"], True)
        self.assertIs(report["zoho_modified"], False)
        self.assertIs(report["outlook_modified"], False)
        self.assertEqual(report["summary"]["zoho_writes"], 0)
        self.assertEqual(report["summary"]["outlook_drafts_created"], 0)
        self.assertEqual(report["summary"]["emails_sent"], 0)

    def test_batches_hold_at_most_ten_orders_each(self):
        orders = [make_order(f"SO-{index:05d}", f"o{index}", reference="") for index in range(1, 28)]
        graph = FakeGraph({})
        with TemporaryDirectory() as temporary:
            result = run(orders, graph, Path(temporary))
            batch_files = sorted(Path(result["batch_paths"][0]).parent.iterdir())
            self.assertEqual(len(batch_files), 3)
            seen = 0
            for path in batch_files:
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertLessEqual(payload["batch_orders"], recovery.BATCH_SIZE)
                seen += payload["batch_orders"]
            self.assertEqual(seen, 27)

    def test_batches_hold_queries_and_no_message_content(self):
        with TemporaryDirectory() as temporary:
            result = self.build(Path(temporary))
            payload = json.loads(Path(result["batch_paths"][0]).read_text(encoding="utf-8"))
        entry = payload["orders"][0]
        self.assertIn("queries", entry)
        self.assertTrue(entry["queries"])
        self.assertNotIn("body", json.dumps(payload))
        self.assertNotIn("bodyPreview", json.dumps(payload))

    def test_output_folder_holds_only_reports_and_batches(self):
        with TemporaryDirectory() as temporary:
            result = self.build(Path(temporary))
            directory = Path(result["paths"]["json"]).parent
            names = sorted(child.name for child in directory.iterdir())
        self.assertEqual(
            names,
            sorted([recovery.CSV_REPORT_NAME, recovery.JSON_REPORT_NAME, recovery.MARKDOWN_REPORT_NAME, "batches"]),
        )

    def test_a_stray_file_in_the_output_folder_is_refused(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / "out"
            directory.mkdir()
            (directory / recovery.JSON_REPORT_NAME).write_text("{}", encoding="utf-8")
            (directory / recovery.CSV_REPORT_NAME).write_text("", encoding="utf-8")
            (directory / recovery.MARKDOWN_REPORT_NAME).write_text("", encoding="utf-8")
            (directory / "raw_body.txt").write_text("secret body", encoding="utf-8")
            with self.assertRaises(recovery.RecoveryError):
                recovery.assert_no_raw_material(directory)

    def test_every_affected_order_appears_exactly_once_and_no_other(self):
        orders = [
            make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009"),
            make_order("SO-00006", "o2", reference=""),
            make_order("SO-00029", "o3", reference="PO 5117"),
        ]
        graph = FakeGraph({})
        with TemporaryDirectory() as temporary:
            result = run(orders, graph, Path(temporary))
        identifiers = [row["salesorder_id"] for row in result["rows"]]
        self.assertEqual(identifiers, ["o2", "o1"])
        self.assertNotIn("o3", identifiers)

    def test_summary_splits_replacements_from_fills(self):
        orders = [
            make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009"),
            make_order("SO-00006", "o2", reference="", order_date="2026-01-05"),
        ]
        graph = FakeGraph(
            {
                "QT-000009": [make_message("m1", body="our purchase order 25592")],
                "SO-00006": [make_message("m2", body="our purchase order 31005", received="2026-01-04T10:00:00Z")],
            }
        )
        with TemporaryDirectory() as temporary:
            result = run(orders, graph, Path(temporary))
        summary = result["summary"]
        self.assertEqual(summary["certain_recoveries"], 2)
        self.assertEqual(summary["certain_quote_reference_replacements"], 1)
        self.assertEqual(summary["certain_blank_reference_fills"], 1)
        self.assertEqual(summary["quote_number_references"], 1)
        self.assertEqual(summary["blank_references"], 1)


class TestClosedRowValidation(unittest.TestCase):
    def base_row(self, **overrides) -> dict:
        row = {column: "" for column in recovery.REPORT_COLUMNS}
        row.update(
            {
                "salesorder_id": "o1",
                "salesorder_number": "SO-1",
                "confidence": recovery.CONFIDENCE_NONE,
                "evidence_source_type": recovery.SOURCE_NONE,
                "conflict_candidates": [],
                "recommended_action": recovery.ACTION_LEAVE_UNCHANGED,
            }
        )
        row.update(overrides)
        return row

    def orders(self) -> list[dict]:
        return [make_order("SO-1", "o1")]

    def test_a_clean_row_passes(self):
        recovery.assert_rows_are_closed([self.base_row()], self.orders())

    def test_an_extra_column_is_refused(self):
        row = self.base_row()
        row["evidence_body"] = "raw"
        with self.assertRaises(recovery.RecoveryError):
            recovery.assert_rows_are_closed([row], self.orders())

    def test_a_missing_order_is_refused(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.assert_rows_are_closed([], self.orders())

    def test_an_unexpected_order_is_refused(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.assert_rows_are_closed([self.base_row(salesorder_id="other")], self.orders())

    def test_certainty_without_a_value_is_refused(self):
        row = self.base_row(confidence=recovery.CONFIDENCE_CERTAIN)
        with self.assertRaises(recovery.RecoveryError):
            recovery.assert_rows_are_closed([row], self.orders())

    def test_a_value_without_certainty_is_refused(self):
        row = self.base_row(recovered_client_po="26330")
        with self.assertRaises(recovery.RecoveryError):
            recovery.assert_rows_are_closed([row], self.orders())

    def test_a_recovered_quote_number_is_refused(self):
        row = self.base_row(
            recovered_client_po="QT-000029",
            confidence=recovery.CONFIDENCE_CERTAIN,
            recommended_action=recovery.ACTION_FILL_BLANK,
        )
        with self.assertRaises(recovery.RecoveryError):
            recovery.assert_rows_are_closed([row], self.orders())

    def test_an_unknown_action_is_refused(self):
        row = self.base_row(recommended_action="update_zoho_now")
        with self.assertRaises(recovery.RecoveryError):
            recovery.assert_rows_are_closed([row], self.orders())

    def test_an_unknown_evidence_source_is_refused(self):
        row = self.base_row(evidence_source_type="tdi_gmail")
        with self.assertRaises(recovery.RecoveryError):
            recovery.assert_rows_are_closed([row], self.orders())

    def test_an_overlong_excerpt_is_refused(self):
        row = self.base_row(evidence_excerpt="x" * (recovery.MAX_EXCERPT_CHARS + 1))
        with self.assertRaises(recovery.RecoveryError):
            recovery.assert_rows_are_closed([row], self.orders())


# --------------------------------------------------------------------------
# leak scan
# --------------------------------------------------------------------------
class TestLeakScan(unittest.TestCase):
    def scan(self, text: str) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            path.write_text(text, encoding="utf-8")
            recovery.scan_for_leaks({"json": str(path)})

    def test_clean_text_passes(self):
        self.scan('{"recovered_client_po": "PO26330", "date": "2026-08-11"}')

    def test_a_total_is_caught(self):
        with self.assertRaises(recovery.RecoveryError):
            self.scan('{"note": "total 105.42"}')

    def test_a_currency_amount_is_caught(self):
        with self.assertRaises(recovery.RecoveryError):
            self.scan('{"note": "CAD 100"}')

    def test_a_currency_symbol_is_caught(self):
        with self.assertRaises(recovery.RecoveryError):
            self.scan('{"note": "$50"}')

    def test_a_credential_is_caught(self):
        for text in ('{"a": "access_token"}', '{"a": "client_secret"}', '{"a": "password"}', '{"a": "cookie"}'):
            with self.assertRaises(recovery.RecoveryError, msg=text):
                self.scan(text)

    def test_bank_wording_is_caught(self):
        with self.assertRaises(recovery.RecoveryError):
            self.scan('{"a": "IBAN"}')

    def test_the_leak_scan_is_actually_wired_into_the_run(self):
        """Redaction normally leaves nothing to catch, so the scan is proved by
        defeating the redaction: the run must still abort."""
        graph = FakeGraph({"QT-000009": [make_message("m1", body="our purchase order 25592, total CAD 1,234.56")]})
        with TemporaryDirectory() as temporary:
            with mock.patch.object(recovery, "redact_amounts", side_effect=lambda text: text):
                with self.assertRaises(recovery.RecoveryError) as caught:
                    run(
                        [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                        graph,
                        Path(temporary),
                    )
        self.assertIn("leaked", str(caught.exception))

    def test_a_live_run_that_would_leak_a_total_aborts(self):
        graph = FakeGraph({"QT-000009": [make_message("m1", body="our purchase order 25592, total CAD 1,234.56")]})
        with TemporaryDirectory() as temporary:
            result = run(
                [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                graph,
                Path(temporary),
            )
            text = Path(result["paths"]["json"]).read_text(encoding="utf-8")
        self.assertNotIn("1,234.56", text)
        self.assertIn(recovery.REDACTION, text)


# --------------------------------------------------------------------------
# Drive fallback
# --------------------------------------------------------------------------
class TestDriveFallback(unittest.TestCase):
    def connection(self, rows: list[tuple[str, str, str]]):
        import sqlite3

        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE VIRTUAL TABLE drive_fts USING fts5(id UNINDEXED, name, content)")
        connection.executemany("INSERT INTO drive_fts (id, name, content) VALUES (?, ?, ?)", rows)
        return connection

    def test_drive_is_only_consulted_when_outlook_is_silent(self):
        connection = self.connection([("f1", "SCT PO.pdf", "QT-000009 purchase order 26330")])
        graph = FakeGraph({"QT-000009": [make_message("m1", body="our purchase order 25592")]})
        with TemporaryDirectory() as temporary:
            result = run(
                [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                graph,
                Path(temporary),
                drive_connection=connection,
            )
        self.assertEqual(result["rows"][0]["recovered_client_po"], "25592")
        self.assertEqual(result["rows"][0]["evidence_source_type"], recovery.SOURCE_MESSAGE_LINKED)

    def test_drive_evidence_is_labelled_as_cache_only(self):
        connection = self.connection([("f1", "SCT PO.pdf", "QT-000009 purchase order 26330")])
        graph = FakeGraph({})
        with TemporaryDirectory() as temporary:
            result = run(
                [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                graph,
                Path(temporary),
                drive_connection=connection,
            )
        row = result["rows"][0]
        self.assertEqual(row["recovered_client_po"], "26330")
        self.assertEqual(row["evidence_source_type"], recovery.SOURCE_DRIVE_CACHE)
        self.assertIn("cache", row["evidence_source_type"])
        self.assertEqual(row["evidence_message_id"], "")

    def test_an_frp_generated_drive_document_is_not_evidence(self):
        connection = self.connection([("f1", "QT-000009.pdf", "FRP Depot Quote # QT-000009 PO 99999")])
        graph = FakeGraph({})
        with TemporaryDirectory() as temporary:
            result = run(
                [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")],
                graph,
                Path(temporary),
                drive_connection=connection,
            )
        self.assertEqual(result["rows"][0]["confidence"], recovery.CONFIDENCE_NONE)


# --------------------------------------------------------------------------
# containment
# --------------------------------------------------------------------------
def stripped_source() -> str:
    """The module with comments AND docstrings removed. A raw-text scan only
    proves the prose mentions what it refuses."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    without_comments = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        without_comments.append(token)
    text = tokenize.untokenize(without_comments)
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body[0].value.value = ""
    return ast.unparse(tree)


class TestContainment(unittest.TestCase):
    def setUp(self):
        self.source = stripped_source()
        self.tree = ast.parse(self.source)

    def test_no_write_verb_string_exists(self):
        for verb in ('"POST"', "'POST'", '"PUT"', "'PUT'", '"DELETE"', "'DELETE'", '"PATCH"', "'PATCH'"):
            self.assertNotIn(verb, self.source, verb)

    def test_the_only_graph_verb_is_get(self):
        verbs = [
            node.args[1].value
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "graph_request"
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
        ]
        self.assertEqual(verbs, ["GET"])

    def test_no_direct_network_call_site_exists(self):
        for needle in ("urlopen", "urlretrieve", "requests.", "http.client", "socket."):
            self.assertNotIn(needle, self.source, needle)

    def test_no_mail_transport_exists(self):
        for needle in ("sendMail", "send_mail", "createReply", "createForward", "/send", "smtplib", "Mail.Send"):
            self.assertNotIn(needle, self.source, needle)

    def test_no_draft_route_exists(self):
        for needle in ("command_draft", "command_reply_all", "create_draft", "add_official_inline_attachments"):
            self.assertNotIn(needle, self.source, needle)

    def test_no_zoho_write_helper_is_reachable(self):
        for needle in ("api_post", "api_put", "api_delete", "save_vault", "ignore_auto_number_generation"):
            self.assertNotIn(needle, self.source, needle)

    def test_the_only_zoho_transport_is_the_get_helper(self):
        attributes = sorted(
            {
                node.func.attr
                for node in ast.walk(self.tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "zoho_tool"
            }
        )
        self.assertEqual(attributes, ["api_get", "load_vault", "refresh_access_token"])

    def test_no_gmail_table_is_read(self):
        for needle in ("gmail_fts", "gmail_messages", "withheld_hashes"):
            self.assertNotIn(needle, self.source, needle)

    def test_the_tdi_tree_is_never_referenced(self):
        for needle in ("AgentTeam", "aze", "troy_history"):
            self.assertNotIn(needle, self.source, needle)

    def test_the_drive_cache_is_opened_read_only(self):
        self.assertIn("mode=ro", self.source)
        connects = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
        ]
        self.assertEqual(len(connects), 1)

    def test_no_browser_or_ui_session_exists(self):
        for needle in ("playwright", "connect_over_cdp", "9228", "9229", "zoho_ui_session"):
            self.assertNotIn(needle, self.source, needle)

    def test_no_company_filter_is_applied_to_the_frp_mailbox(self):
        for needle in ("FORBIDDEN_NEEDLES", "forbidden_text", "tdi_filter", "quarantine"):
            self.assertNotIn(needle, self.source, needle)

    def test_transient_attachments_live_outside_the_repository(self):
        self.assertIn("LOCALAPPDATA", self.source)
        self.assertNotIn('TRANSIENT_ROOT = ROOT', self.source)


class TestReadOnlyEndToEnd(unittest.TestCase):
    def test_a_full_run_issues_only_get_paths_and_touches_no_vault(self):
        orders = [make_order("SO-00010", "o1", reference="QT-000009", quote_number="QT-000009")]
        graph = FakeGraph({"QT-000009": [make_message("m1", body="our purchase order 25592")]})
        zoho_get = zoho_reader()
        with TemporaryDirectory() as temporary:
            with mock.patch.object(recovery.zoho_tool, "save_vault", side_effect=AssertionError("vault write")):
                with mock.patch.object(recovery.outlook_tool, "graph_request", side_effect=AssertionError("live graph")):
                    result = run(orders, graph, Path(temporary), zoho_get=zoho_get)
        self.assertTrue(all(path.startswith("/me/") for path in graph.paths))
        self.assertTrue(all(path.startswith("/books/v3/contacts/") for path in zoho_get.calls))
        self.assertEqual(result["summary"]["zoho_writes"], 0)

    def test_transient_directory_is_removed_and_never_inside_the_repository(self):
        with TemporaryDirectory() as temporary:
            transient = Path(temporary) / "transient"
            transient.mkdir()
            (transient / "leftover.pdf").write_bytes(b"x")
            recovery.cleanup_transient(transient)
            self.assertFalse(transient.exists())

    def test_cleanup_refuses_a_transient_directory_inside_the_repository(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.cleanup_transient(recovery.ROOT / "Dado" / "20_Working")


if __name__ == "__main__":
    unittest.main(verbosity=2)
