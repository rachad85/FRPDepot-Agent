#!/usr/bin/env python
"""FRP Depot historical Zoho Books Sales Order reference audit. READ-ONLY.

For every Books Sales Order this reports the client PO/reference number Zoho
holds, the originating Quote/Estimate where a defensible link exists, and any
downstream invoices. It is an audit only: it changes nothing in Zoho and it
produces no update payload.

Every Zoho access goes through ``zoho_tool.api_get``, which is GET-only. This
module has no write verb, no UI session, no message transport and no retry of a
write, because it never attempts one.

Deliberate omission: no amount, unit price, tax, discount, total, balance or
payment value is ever projected, saved, printed or tested. This is a
reference-link audit, not a financial report. ``assert_projection_is_clean``
enforces that on every projected record against its own live source record.

No company filtering is applied. Troy Dualam is an ordinary FRP Depot customer.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(r"C:\FRPDepot")
ZOHO_TOOLS = ROOT / "Dado" / "Tools" / "zoho"
if str(ZOHO_TOOLS) not in sys.path:
    sys.path.insert(0, str(ZOHO_TOOLS))
import zoho_tool  # noqa: E402

TOOL_NAME = "zoho_order_reference_audit"
TOOL_VERSION = "1.0.0"
OUTPUT_ROOT = ROOT / "Dado" / "20_Working" / "zoho_order_reference_audit"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"

MAX_PAGES = 100
PER_PAGE = 200
BATCH_SIZE = 20

JSON_REPORT_NAME = "order_reference_audit.json"
CSV_REPORT_NAME = "order_reference_audit.csv"
MARKDOWN_REPORT_NAME = "order_reference_audit.md"

# Any projected key holding one of these tokens is a financial field and is
# refused outright, even if some future edit adds it to an allowlist.
FINANCIAL_KEY_TOKENS = (
    "amount",
    "total",
    "rate",
    "tax",
    "discount",
    "balance",
    "payment",
    "paid",
    "price",
    "cost",
    "adjustment",
    "charge",
    "credit",
    "write_off",
    "writeoff",
    "currency",
    "exchange",
    "margin",
    "subtotal",
    "sub_total",
)

SALESORDER_FIELDS = (
    "salesorder_id",
    "salesorder_number",
    "date",
    "status",
    "order_status",
    "invoiced_status",
    "shipped_status",
    "customer_id",
    "customer_name",
    "reference_number",
    "created_time",
    "last_modified_time",
)
ESTIMATE_FIELDS = (
    "estimate_id",
    "estimate_number",
    "date",
    "status",
    "customer_id",
    "customer_name",
    "reference_number",
    "created_time",
    "last_modified_time",
)
INVOICE_FIELDS = (
    "invoice_id",
    "invoice_number",
    "date",
    "status",
    "customer_id",
    "customer_name",
    "reference_number",
    "created_time",
    "last_modified_time",
)
# Sub-allowlists for the nested link collections Zoho returns inside a detail
# record. Zoho puts financial values in those entries too; only these four
# identity keys are ever copied out.
LINKED_ESTIMATE_ENTRY_FIELDS = ("estimate_id", "estimate_number", "date", "status")
LINKED_INVOICE_ENTRY_FIELDS = ("invoice_id", "invoice_number", "date", "status")
LINKED_SALESORDER_ENTRY_FIELDS = ("salesorder_id", "salesorder_number", "date", "status")

PROJECTION_EXTRA_KEYS = (
    "linked_estimate_ids",
    "linked_estimate_numbers",
    "linked_invoice_ids",
    "linked_invoice_numbers",
    "linked_salesorder_ids",
    "linked_salesorder_numbers",
    "linked_estimate_entries",
    "linked_invoice_entries",
    "linked_salesorder_entries",
    "malformed_link_ids",
    "link_source_fields",
    "detail_fetched",
    "po_original",
    "po_normalized",
    "po_state",
)

REPORT_COLUMNS = (
    "salesorder_id",
    "salesorder_number",
    "date",
    "status",
    "customer_id",
    "customer_name",
    "client_po_original",
    "client_po_normalized",
    "client_po_state",
    "quote_id",
    "quote_number",
    "quote_match_source",
    "quote_confidence",
    "quote_candidates",
    "invoice_ids",
    "invoice_numbers",
    "invoice_match_source",
    "invoice_confidence",
    "review_state",
    "recommended_correction",
    "evidence_fields",
)

PO_STATE_PRESENT = "present"
PO_STATE_MISSING = "missing"
PO_STATE_AMBIGUOUS = "ambiguous_format"

CONFIDENCE_CERTAIN = "certain"
CONFIDENCE_STRONG = "strong"
CONFIDENCE_AMBIGUOUS = "ambiguous"
CONFIDENCE_NONE = "none"

SOURCE_NONE = "NONE"
SOURCE_DIRECT_ID = "DIRECT_ID"
SOURCE_DIRECT_NUMBER = "DIRECT_NUMBER"
SOURCE_REVERSE_DIRECT_ID = "REVERSE_DIRECT_ID"
SOURCE_REVERSE_DIRECT_NUMBER = "REVERSE_DIRECT_NUMBER"
SOURCE_CUSTOMER_AND_EXACT_PO = "CUSTOMER_AND_EXACT_PO"
SOURCE_CUSTOMER_AND_UNIQUE_QUOTE_NUMBER_TEXT = "CUSTOMER_AND_UNIQUE_QUOTE_NUMBER_TEXT"

DIRECT_QUOTE_SOURCES = (
    SOURCE_DIRECT_ID,
    SOURCE_DIRECT_NUMBER,
    SOURCE_REVERSE_DIRECT_ID,
    SOURCE_REVERSE_DIRECT_NUMBER,
)

REVIEW_STATE_COMPLETE = "complete"
REVIEW_STATE_ORDER = (
    "missing_po",
    "ambiguous_po",
    "missing_quote",
    "ambiguous_quote",
    "missing_invoice",
    "ambiguous_invoice",
)

# Only a leading standalone label is stripped. Nothing inside the reference is
# touched, so PO 104750 / J6276 keeps both halves and can never be conflated
# with a different reference that merely shares one of them.
_PO_LABEL = re.compile(r"^(?:CLIENT\s+PO|PURCHASE\s+ORDER|P\.\s*O\.|PO)(?![A-Z0-9])\s*[#:\-]*\s*")
_PO_SEPARATORS = re.compile(r"[,;]| AND | & ")
_NUMERIC_ID = re.compile(r"^[0-9]+$")


class AuditError(RuntimeError):
    pass


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def is_positive_numeric_id(value: object) -> bool:
    text = _text(value)
    return bool(_NUMERIC_ID.fullmatch(text)) and int(text) > 0


def _is_number(text: str) -> bool:
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def require_id(value: object, label: str) -> str:
    text = _text(value)
    if not is_positive_numeric_id(text):
        raise AuditError(f"REFUSED: {label} is not a positive numeric Zoho id: {text!r}")
    return text


def normalize_po(value: object) -> str:
    """Conservative client-PO normalization.

    NFKC, trim, uppercase, collapse whitespace, then remove only a leading
    standalone PO / P.O. / PURCHASE ORDER / CLIENT PO label and the separators
    immediately around it. Internal letters, digits, slashes and dashes are
    preserved exactly.
    """
    text = _text(value)
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = " ".join(text.split()).upper()
    text = _PO_LABEL.sub("", text, count=1)
    text = " ".join(text.split()).strip(" #:-")
    return text


def classify_po(original: object, normalized: str) -> str:
    """present / missing / ambiguous_format.

    ambiguous_format means the reference exists but cannot be used as a single
    matching key: it is a bare label with no reference behind it, or it clearly
    carries more than one reference. Such a value is never used to infer a link.
    """
    raw = _text(original)
    if not raw:
        return PO_STATE_MISSING
    if not normalized:
        return PO_STATE_AMBIGUOUS
    if _PO_SEPARATORS.search(normalized):
        return PO_STATE_AMBIGUOUS
    return PO_STATE_PRESENT


def assert_projection_is_clean(projection: dict, source: dict, label: str) -> None:
    """No financial key and no financial value may survive into a projection."""
    forbidden_values: set[str] = set()
    innocent_values: set[str] = set()
    for key, value in (source or {}).items():
        text = _text(value)
        if not text or text in {"0", "0.0", "0.00", "false", "true"}:
            continue
        lowered = str(key).casefold()
        if any(token in lowered for token in FINANCIAL_KEY_TOKENS) and _is_number(text):
            # Only a FIGURE can be a leaked financial value. Zoho puts words in
            # financial-sounding keys too (paid_status is "paid"), and a linked
            # invoice's lifecycle status legitimately carries that same word.
            forbidden_values.add(text)
        else:
            innocent_values.add(text)
    # A value that also occurs under a non-financial key is that field's own
    # value, not a leaked figure. Without this a reference number that happened
    # to read like a total would abort an otherwise clean historical audit.
    forbidden_values -= innocent_values

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).casefold()
                if any(token in lowered for token in FINANCIAL_KEY_TOKENS):
                    raise AuditError(
                        f"REFUSED: {label} projection holds financial key {path}{key}"
                    )
                walk(value, f"{path}{key}.")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}].")
        else:
            text = _text(node)
            if text and text in forbidden_values:
                raise AuditError(
                    f"REFUSED: {label} projection leaked a financial value at {path.rstrip('.')}"
                )

    walk(projection, "")


def _collect_links(
    raw: dict,
    scalar_id_keys: tuple[str, ...],
    scalar_number_keys: tuple[str, ...],
    list_specs: tuple[tuple[str, str, str, tuple[str, ...]], ...],
) -> dict:
    ids: list[str] = []
    numbers: list[str] = []
    entries: list[dict] = []
    fields: list[str] = []
    malformed: list[str] = []

    def add_id(value: object, field: str) -> None:
        text = _text(value)
        if not text or text == "0":
            return
        if not is_positive_numeric_id(text):
            record = f"{field}={text}"
            if record not in malformed:
                malformed.append(record)
            return
        if text not in ids:
            ids.append(text)
        if field not in fields:
            fields.append(field)

    def add_number(value: object, field: str) -> None:
        text = _text(value)
        if not text:
            return
        if text not in numbers:
            numbers.append(text)
        if field not in fields:
            fields.append(field)

    for key in scalar_id_keys:
        add_id(raw.get(key), key)
    for key in scalar_number_keys:
        add_number(raw.get(key), key)

    for list_key, id_key, number_key, entry_fields in list_specs:
        rows = raw.get(list_key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            add_id(row.get(id_key), f"{list_key}[].{id_key}")
            add_number(row.get(number_key), f"{list_key}[].{number_key}")
            entry = {field: _text(row.get(field)) for field in entry_fields}
            if any(entry.values()) and entry not in entries:
                entries.append(entry)

    return {"ids": ids, "numbers": numbers, "entries": entries, "fields": fields, "malformed": malformed}


def _base_projection(raw: dict, fields: tuple[str, ...], id_field: str, label: str) -> dict:
    projection = {field: _text(raw.get(field)) for field in fields}
    projection[id_field] = require_id(raw.get(id_field), f"{label} {id_field}")
    customer = _text(raw.get("customer_id"))
    if customer:
        projection["customer_id"] = require_id(customer, f"{label} customer_id")
    original = raw.get("reference_number")
    normalized = normalize_po(original)
    projection["po_original"] = _text(original)
    projection["po_normalized"] = normalized
    projection["po_state"] = classify_po(original, normalized)
    return projection


def project_salesorder(raw: dict, detail_fetched: bool = True) -> dict:
    projection = _base_projection(raw, SALESORDER_FIELDS, "salesorder_id", "sales order")
    estimate_links = _collect_links(
        raw,
        ("estimate_id",),
        ("estimate_number",),
        (("estimates", "estimate_id", "estimate_number", LINKED_ESTIMATE_ENTRY_FIELDS),),
    )
    invoice_links = _collect_links(
        raw,
        ("invoice_id",),
        ("invoice_number",),
        (("invoices", "invoice_id", "invoice_number", LINKED_INVOICE_ENTRY_FIELDS),),
    )
    projection["linked_estimate_ids"] = estimate_links["ids"]
    projection["linked_estimate_numbers"] = estimate_links["numbers"]
    projection["linked_estimate_entries"] = estimate_links["entries"]
    projection["linked_invoice_ids"] = invoice_links["ids"]
    projection["linked_invoice_numbers"] = invoice_links["numbers"]
    projection["linked_invoice_entries"] = invoice_links["entries"]
    projection["link_source_fields"] = sorted(set(estimate_links["fields"]) | set(invoice_links["fields"]))
    projection["malformed_link_ids"] = sorted(set(estimate_links["malformed"]) | set(invoice_links["malformed"]))
    projection["detail_fetched"] = bool(detail_fetched)
    assert_projection_is_clean(projection, raw, "sales order")
    return projection


def project_estimate(raw: dict, detail_fetched: bool = False) -> dict:
    projection = _base_projection(raw, ESTIMATE_FIELDS, "estimate_id", "estimate")
    links = _collect_links(
        raw,
        ("salesorder_id",),
        ("salesorder_number",),
        (("salesorders", "salesorder_id", "salesorder_number", LINKED_SALESORDER_ENTRY_FIELDS),),
    )
    projection["linked_salesorder_ids"] = links["ids"]
    projection["linked_salesorder_numbers"] = links["numbers"]
    projection["linked_salesorder_entries"] = links["entries"]
    projection["link_source_fields"] = sorted(set(links["fields"]))
    projection["malformed_link_ids"] = sorted(set(links["malformed"]))
    projection["detail_fetched"] = bool(detail_fetched)
    assert_projection_is_clean(projection, raw, "estimate")
    return projection


def project_invoice(raw: dict, detail_fetched: bool = False) -> dict:
    projection = _base_projection(raw, INVOICE_FIELDS, "invoice_id", "invoice")
    salesorder_links = _collect_links(
        raw,
        ("salesorder_id",),
        ("salesorder_number",),
        (("salesorders", "salesorder_id", "salesorder_number", LINKED_SALESORDER_ENTRY_FIELDS),),
    )
    estimate_links = _collect_links(
        raw,
        ("estimate_id",),
        ("estimate_number",),
        (("estimates", "estimate_id", "estimate_number", LINKED_ESTIMATE_ENTRY_FIELDS),),
    )
    projection["linked_salesorder_ids"] = salesorder_links["ids"]
    projection["linked_salesorder_numbers"] = salesorder_links["numbers"]
    projection["linked_salesorder_entries"] = salesorder_links["entries"]
    projection["linked_estimate_ids"] = estimate_links["ids"]
    projection["linked_estimate_numbers"] = estimate_links["numbers"]
    projection["link_source_fields"] = sorted(set(salesorder_links["fields"]) | set(estimate_links["fields"]))
    projection["malformed_link_ids"] = sorted(
        set(salesorder_links["malformed"]) | set(estimate_links["malformed"])
    )
    projection["detail_fetched"] = bool(detail_fetched)
    assert_projection_is_clean(projection, raw, "invoice")
    return projection


def fetch_all(fetch, path: str, key: str, extra: dict | None = None) -> list[dict]:
    """Bounded pagination. Terminates on Zoho's own has_more_page or refuses."""
    rows: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        params = {"page": page, "per_page": PER_PAGE}
        params.update(extra or {})
        payload = fetch(path, params)
        if not isinstance(payload, dict):
            raise AuditError(f"REFUSED: Zoho returned a non-object listing for {path}")
        batch = payload.get(key)
        if batch is None:
            batch = []
        if not isinstance(batch, list):
            raise AuditError(f"REFUSED: Zoho returned a non-list {key} collection for {path}")
        rows.extend(row for row in batch if isinstance(row, dict))
        if not (payload.get("page_context") or {}).get("has_more_page"):
            return rows
    raise AuditError(
        f"REFUSED: pagination guard stopped {path} after {MAX_PAGES} pages of {PER_PAGE}"
    )


def require_unique(records: list[dict], id_field: str, label: str) -> None:
    seen: set[str] = set()
    for record in records:
        value = _text(record.get(id_field))
        if value in seen:
            raise AuditError(f"REFUSED: duplicate {label} {id_field} {value} in the live collection")
        seen.add(value)


def require_identity(detail: dict, id_field: str, expected: str, label: str) -> None:
    actual = _text(detail.get(id_field))
    if actual != expected:
        raise AuditError(
            f"REFUSED: {label} detail identity mismatch, asked {expected} and Zoho returned {actual!r}"
        )


def _quote_number_pattern(number: str) -> re.Pattern:
    return re.compile(r"(?<![A-Z0-9])" + re.escape(number.upper()) + r"(?![A-Z0-9])")


class Inventory:
    """Indexes over the projected collections. Nothing here holds a raw record."""

    def __init__(self, orders: list[dict], estimates: list[dict], invoices: list[dict]) -> None:
        self.orders = orders
        self.estimates = estimates
        self.invoices = invoices
        self.estimates_by_id = {row["estimate_id"]: row for row in estimates}
        self.invoices_by_id = {row["invoice_id"]: row for row in invoices}
        self.estimates_by_number: dict[str, list[dict]] = {}
        self.invoices_by_number: dict[str, list[dict]] = {}
        self.estimates_by_customer_po: dict[tuple[str, str], list[dict]] = {}
        self.invoices_by_customer_po: dict[tuple[str, str], list[dict]] = {}
        self.estimates_by_linked_so_id: dict[str, list[dict]] = {}
        self.estimates_by_linked_so_number: dict[str, list[dict]] = {}
        self.invoices_by_linked_so_id: dict[str, list[dict]] = {}
        self.invoices_by_linked_so_number: dict[str, list[dict]] = {}
        self.estimates_by_customer: dict[str, list[dict]] = {}

        for row in estimates:
            number = row.get("estimate_number", "")
            if number:
                self.estimates_by_number.setdefault(number.upper(), []).append(row)
            if row.get("po_state") == PO_STATE_PRESENT:
                key = (row.get("customer_id", ""), row.get("po_normalized", ""))
                self.estimates_by_customer_po.setdefault(key, []).append(row)
            for linked in row.get("linked_salesorder_ids", []):
                self.estimates_by_linked_so_id.setdefault(linked, []).append(row)
            for linked in row.get("linked_salesorder_numbers", []):
                self.estimates_by_linked_so_number.setdefault(linked.upper(), []).append(row)
            customer = row.get("customer_id", "")
            if customer:
                self.estimates_by_customer.setdefault(customer, []).append(row)

        for row in invoices:
            number = row.get("invoice_number", "")
            if number:
                self.invoices_by_number.setdefault(number.upper(), []).append(row)
            if row.get("po_state") == PO_STATE_PRESENT:
                key = (row.get("customer_id", ""), row.get("po_normalized", ""))
                self.invoices_by_customer_po.setdefault(key, []).append(row)
            for linked in row.get("linked_salesorder_ids", []):
                self.invoices_by_linked_so_id.setdefault(linked, []).append(row)
            for linked in row.get("linked_salesorder_numbers", []):
                self.invoices_by_linked_so_number.setdefault(linked.upper(), []).append(row)


def _candidate_view(row: dict, number_field: str, id_field: str) -> dict:
    return {
        id_field: row.get(id_field, ""),
        number_field: row.get(number_field, ""),
        "date": row.get("date", ""),
        "status": row.get("status", ""),
        "reference_original": row.get("po_original", ""),
        "reference_normalized": row.get("po_normalized", ""),
    }


def _resolve(rows: list[dict]) -> list[dict]:
    seen: set[int] = set()
    unique: list[dict] = []
    for row in rows:
        if id(row) in seen:
            continue
        seen.add(id(row))
        unique.append(row)
    return unique


def match_quote(order: dict, inventory: Inventory) -> dict:
    """Precedence: direct, reverse direct, customer+exact PO, quote number text.

    Amount is never consulted. Customer plus date alone never matches. There is
    no fuzzy name comparison anywhere in this function.
    """
    evidence: list[str] = []
    unresolved: list[str] = []

    def result(source: str, matches: list[dict], confidence: str) -> dict:
        candidates = [_candidate_view(row, "estimate_number", "estimate_id") for row in matches]
        chosen = matches[0] if (len(matches) == 1 and confidence != CONFIDENCE_AMBIGUOUS) else None
        return {
            "quote_id": chosen.get("estimate_id", "") if chosen else "",
            "quote_number": chosen.get("estimate_number", "") if chosen else "",
            "quote_match_source": source,
            "quote_confidence": confidence,
            "quote_candidates": candidates,
            "quote_evidence_fields": sorted(set(evidence)),
            "unresolved_quote_links": sorted(set(unresolved)),
        }

    # 1. Explicit live link on the Sales Order.
    direct: list[dict] = []
    for estimate_id in order.get("linked_estimate_ids", []):
        row = inventory.estimates_by_id.get(estimate_id)
        if row is not None:
            direct.append(row)
        else:
            unresolved.append(f"salesorder.estimate_id={estimate_id}")
    if direct:
        evidence.append("salesorder.estimate_id")
        direct = _resolve(direct)
        confidence = CONFIDENCE_CERTAIN if len(direct) == 1 else CONFIDENCE_AMBIGUOUS
        return result(SOURCE_DIRECT_ID, direct, confidence)

    by_number: list[dict] = []
    for number in order.get("linked_estimate_numbers", []):
        by_number.extend(inventory.estimates_by_number.get(number.upper(), []))
        if not inventory.estimates_by_number.get(number.upper()):
            unresolved.append(f"salesorder.estimate_number={number}")
    if by_number:
        evidence.append("salesorder.estimate_number")
        by_number = _resolve(by_number)
        confidence = CONFIDENCE_CERTAIN if len(by_number) == 1 else CONFIDENCE_AMBIGUOUS
        return result(SOURCE_DIRECT_NUMBER, by_number, confidence)

    # 2. Explicit live link on the Estimate pointing back at this Sales Order.
    reverse = _resolve(inventory.estimates_by_linked_so_id.get(order["salesorder_id"], []))
    if reverse:
        evidence.append("estimate.salesorder_id")
        confidence = CONFIDENCE_CERTAIN if len(reverse) == 1 else CONFIDENCE_AMBIGUOUS
        return result(SOURCE_REVERSE_DIRECT_ID, reverse, confidence)

    order_number = order.get("salesorder_number", "")
    if order_number:
        reverse_number = _resolve(inventory.estimates_by_linked_so_number.get(order_number.upper(), []))
        if reverse_number:
            evidence.append("estimate.salesorder_number")
            confidence = CONFIDENCE_CERTAIN if len(reverse_number) == 1 else CONFIDENCE_AMBIGUOUS
            return result(SOURCE_REVERSE_DIRECT_NUMBER, reverse_number, confidence)

    customer_id = order.get("customer_id", "")

    # 3. Same customer and exact normalized client PO.
    if customer_id and order.get("po_state") == PO_STATE_PRESENT:
        key = (customer_id, order.get("po_normalized", ""))
        candidates = _resolve(inventory.estimates_by_customer_po.get(key, []))
        if candidates:
            evidence.extend(["salesorder.reference_number", "estimate.reference_number"])
            confidence = CONFIDENCE_STRONG if len(candidates) == 1 else CONFIDENCE_AMBIGUOUS
            return result(SOURCE_CUSTOMER_AND_EXACT_PO, candidates, confidence)

    # 4. A verified live quote number written into this order's own reference.
    if customer_id:
        haystack = " ".join(
            part
            for part in (order.get("po_original", ""), order.get("po_normalized", ""))
            if part
        ).upper()
        if haystack:
            hits: list[dict] = []
            for row in inventory.estimates_by_customer.get(customer_id, []):
                number = row.get("estimate_number", "")
                if number and _quote_number_pattern(number).search(haystack):
                    hits.append(row)
            hits = _resolve(hits)
            if hits:
                evidence.extend(["salesorder.reference_number", "estimate.estimate_number"])
                confidence = CONFIDENCE_STRONG if len(hits) == 1 else CONFIDENCE_AMBIGUOUS
                return result(SOURCE_CUSTOMER_AND_UNIQUE_QUOTE_NUMBER_TEXT, hits, confidence)

    return result(SOURCE_NONE, [], CONFIDENCE_NONE)


def match_invoices(order: dict, inventory: Inventory) -> dict:
    evidence: list[str] = []
    unresolved: list[str] = []

    def result(source: str, matches: list[dict], confidence: str) -> dict:
        matches = sorted(matches, key=lambda row: (row.get("date", ""), row.get("invoice_number", "")))
        return {
            "invoice_ids": [row.get("invoice_id", "") for row in matches],
            "invoice_numbers": [row.get("invoice_number", "") for row in matches],
            "invoice_match_source": source,
            "invoice_confidence": confidence,
            "invoice_candidates": [
                _candidate_view(row, "invoice_number", "invoice_id") for row in matches
            ],
            "invoice_evidence_fields": sorted(set(evidence)),
            "unresolved_invoice_links": sorted(set(unresolved)),
        }

    direct: list[dict] = []
    for invoice_id in order.get("linked_invoice_ids", []):
        row = inventory.invoices_by_id.get(invoice_id)
        if row is not None:
            direct.append(row)
        else:
            unresolved.append(f"salesorder.invoices[].invoice_id={invoice_id}")
    if direct:
        evidence.append("salesorder.invoices[].invoice_id")
        return result(SOURCE_DIRECT_ID, _resolve(direct), CONFIDENCE_CERTAIN)

    by_number: list[dict] = []
    for number in order.get("linked_invoice_numbers", []):
        rows = inventory.invoices_by_number.get(number.upper(), [])
        by_number.extend(rows)
        if not rows:
            unresolved.append(f"salesorder.invoices[].invoice_number={number}")
    if by_number:
        evidence.append("salesorder.invoices[].invoice_number")
        return result(SOURCE_DIRECT_NUMBER, _resolve(by_number), CONFIDENCE_CERTAIN)

    reverse = _resolve(inventory.invoices_by_linked_so_id.get(order["salesorder_id"], []))
    if reverse:
        evidence.append("invoice.salesorder_id")
        return result(SOURCE_REVERSE_DIRECT_ID, reverse, CONFIDENCE_CERTAIN)

    order_number = order.get("salesorder_number", "")
    if order_number:
        reverse_number = _resolve(inventory.invoices_by_linked_so_number.get(order_number.upper(), []))
        if reverse_number:
            evidence.append("invoice.salesorder_number")
            return result(SOURCE_REVERSE_DIRECT_NUMBER, reverse_number, CONFIDENCE_CERTAIN)

    customer_id = order.get("customer_id", "")
    if customer_id and order.get("po_state") == PO_STATE_PRESENT:
        key = (customer_id, order.get("po_normalized", ""))
        candidates = _resolve(inventory.invoices_by_customer_po.get(key, []))
        if candidates:
            evidence.extend(["salesorder.reference_number", "invoice.reference_number"])
            confidence = CONFIDENCE_STRONG if len(candidates) == 1 else CONFIDENCE_AMBIGUOUS
            return result(SOURCE_CUSTOMER_AND_EXACT_PO, candidates, confidence)

    return result(SOURCE_NONE, [], CONFIDENCE_NONE)


def review_state(order: dict, quote: dict, invoice: dict) -> str:
    states: list[str] = []
    if order.get("po_state") == PO_STATE_MISSING:
        states.append("missing_po")
    elif order.get("po_state") == PO_STATE_AMBIGUOUS:
        states.append("ambiguous_po")
    if quote["quote_confidence"] == CONFIDENCE_AMBIGUOUS:
        states.append("ambiguous_quote")
    elif not quote["quote_id"]:
        states.append("missing_quote")
    if invoice["invoice_confidence"] == CONFIDENCE_AMBIGUOUS:
        states.append("ambiguous_invoice")
    elif not invoice["invoice_ids"]:
        states.append("missing_invoice")
    if not states:
        return REVIEW_STATE_COMPLETE
    return "+".join(state for state in REVIEW_STATE_ORDER if state in states)


def recommend(order: dict, quote: dict, invoice: dict) -> str:
    """Words for Rachad. Never an update payload and never a Zoho action."""
    parts: list[str] = []
    if order.get("po_state") == PO_STATE_MISSING:
        parts.append(
            "No client PO is recorded on this Sales Order; find the customer PO document "
            "and have the reference number entered by hand in Zoho."
        )
    elif order.get("po_state") == PO_STATE_AMBIGUOUS:
        parts.append(
            f"The recorded reference {order.get('po_original', '')!r} does not read as one "
            "single client PO; confirm the intended reference before relying on it."
        )
    if quote["quote_confidence"] == CONFIDENCE_AMBIGUOUS:
        numbers = ", ".join(row.get("estimate_number", "") for row in quote["quote_candidates"])
        parts.append(f"More than one quote fits this order ({numbers}); pick the right one by hand.")
    elif not quote["quote_id"]:
        parts.append(
            "No defensible quote link exists; if this order came from a quote, open it in "
            "Zoho and confirm which estimate it was."
        )
    elif quote["quote_match_source"] not in DIRECT_QUOTE_SOURCES:
        parts.append(
            f"Quote {quote['quote_number']} is inferred from {quote['quote_match_source']}, "
            "not from a live Zoho link; confirm before treating it as the source quote."
        )
    if invoice["invoice_confidence"] == CONFIDENCE_AMBIGUOUS:
        numbers = ", ".join(invoice["invoice_numbers"])
        parts.append(f"More than one invoice shares this reference ({numbers}); confirm by hand.")
    elif not invoice["invoice_ids"]:
        parts.append("No invoice is linked to this order yet.")
    if not parts:
        parts.append("Client PO, quote and invoice all resolve; no correction needed.")
    return " ".join(parts)


def build_rows(inventory: Inventory) -> list[dict]:
    rows: list[dict] = []
    for order in inventory.orders:
        quote = match_quote(order, inventory)
        invoice = match_invoices(order, inventory)
        evidence_fields = sorted(
            set(quote["quote_evidence_fields"])
            | set(invoice["invoice_evidence_fields"])
            | {"salesorder.reference_number", "salesorder.customer_id", "salesorder.status"}
        )
        row = {
            "salesorder_id": order["salesorder_id"],
            "salesorder_number": order.get("salesorder_number", ""),
            "date": order.get("date", ""),
            "status": order.get("status", ""),
            "customer_id": order.get("customer_id", ""),
            "customer_name": order.get("customer_name", ""),
            "client_po_original": order.get("po_original", ""),
            "client_po_normalized": order.get("po_normalized", ""),
            "client_po_state": order.get("po_state", PO_STATE_MISSING),
            "quote_id": quote["quote_id"],
            "quote_number": quote["quote_number"],
            "quote_match_source": quote["quote_match_source"],
            "quote_confidence": quote["quote_confidence"],
            "quote_candidates": [c.get("estimate_number", "") for c in quote["quote_candidates"]],
            "quote_candidate_details": quote["quote_candidates"],
            "unresolved_quote_links": quote["unresolved_quote_links"],
            "invoice_ids": invoice["invoice_ids"],
            "invoice_numbers": invoice["invoice_numbers"],
            "invoice_match_source": invoice["invoice_match_source"],
            "invoice_confidence": invoice["invoice_confidence"],
            "invoice_candidate_details": invoice["invoice_candidates"],
            "unresolved_invoice_links": invoice["unresolved_invoice_links"],
            "malformed_link_ids": order.get("malformed_link_ids", []),
            "order_status_detail": {
                "order_status": order.get("order_status", ""),
                "invoiced_status": order.get("invoiced_status", ""),
                "shipped_status": order.get("shipped_status", ""),
            },
            "created_time": order.get("created_time", ""),
            "last_modified_time": order.get("last_modified_time", ""),
            "evidence_fields": evidence_fields,
        }
        row["review_state"] = review_state(order, quote, invoice)
        row["recommended_correction"] = recommend(order, quote, invoice)
        assert_projection_is_clean(row, {}, "report row")
        rows.append(row)
    rows.sort(key=lambda row: (row.get("date", ""), row.get("salesorder_number", "")))
    return rows


def summarize(rows: list[dict], counts: dict) -> dict:
    dates = sorted(row["date"] for row in rows if row.get("date"))
    quote_sources: dict[str, int] = {}
    review_states: dict[str, int] = {}
    for row in rows:
        quote_sources[row["quote_match_source"]] = quote_sources.get(row["quote_match_source"], 0) + 1
        review_states[row["review_state"]] = review_states.get(row["review_state"], 0) + 1
    direct_quote_links = sum(
        1 for row in rows
        if row["quote_match_source"] in DIRECT_QUOTE_SOURCES and row["quote_confidence"] == CONFIDENCE_CERTAIN
    )
    inferred_exact_po = sum(
        1 for row in rows
        if row["quote_match_source"] == SOURCE_CUSTOMER_AND_EXACT_PO
        and row["quote_confidence"] == CONFIDENCE_STRONG
    )
    inferred_quote_text = sum(
        1 for row in rows
        if row["quote_match_source"] == SOURCE_CUSTOMER_AND_UNIQUE_QUOTE_NUMBER_TEXT
        and row["quote_confidence"] == CONFIDENCE_STRONG
    )
    return {
        "salesorders_total": len(rows),
        "complete_po_and_quote": sum(
            1 for row in rows
            if row["client_po_state"] == PO_STATE_PRESENT
            and row["quote_id"]
            and row["quote_confidence"] in (CONFIDENCE_CERTAIN, CONFIDENCE_STRONG)
        ),
        "missing_po": sum(1 for row in rows if row["client_po_state"] == PO_STATE_MISSING),
        "ambiguous_po_format": sum(1 for row in rows if row["client_po_state"] == PO_STATE_AMBIGUOUS),
        "missing_quote": sum(
            1 for row in rows if not row["quote_id"] and row["quote_confidence"] != CONFIDENCE_AMBIGUOUS
        ),
        "ambiguous_quote": sum(1 for row in rows if row["quote_confidence"] == CONFIDENCE_AMBIGUOUS),
        "direct_quote_links": direct_quote_links,
        "inferred_exact_po_quote_links": inferred_exact_po,
        "inferred_quote_number_text_links": inferred_quote_text,
        "orders_with_linked_invoices": sum(1 for row in rows if row["invoice_ids"]),
        "missing_invoice": sum(
            1 for row in rows
            if not row["invoice_ids"] and row["invoice_confidence"] != CONFIDENCE_AMBIGUOUS
        ),
        "ambiguous_invoice": sum(1 for row in rows if row["invoice_confidence"] == CONFIDENCE_AMBIGUOUS),
        "needs_manual_review": sum(1 for row in rows if row["review_state"] != REVIEW_STATE_COMPLETE),
        "date_range": {"earliest": dates[0] if dates else "", "latest": dates[-1] if dates else ""},
        "quote_match_sources": dict(sorted(quote_sources.items())),
        "review_states": dict(sorted(review_states.items())),
        "estimates_enumerated": counts.get("estimates", 0),
        "invoices_enumerated": counts.get("invoices", 0),
        "salesorder_details_fetched": counts.get("salesorder_details", 0),
        "estimate_details_fetched": counts.get("estimate_details", 0),
        "invoice_details_fetched": counts.get("invoice_details", 0),
        "zoho_writes": 0,
    }


def write_batches(directory: Path, name: str, records: list[dict], batch_size: int = BATCH_SIZE) -> list[str]:
    """Intermediate normalized inventories, so a large corpus never has to be
    carried in one blob through the agent turn."""
    if batch_size > BATCH_SIZE:
        raise AuditError(f"REFUSED: batch size {batch_size} exceeds the {BATCH_SIZE} record ceiling")
    directory.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    total = max(1, (len(records) + batch_size - 1) // batch_size)
    for index in range(0, max(len(records), 1), batch_size):
        chunk = records[index:index + batch_size]
        if not chunk and records:
            break
        path = directory / f"{name}_batch_{index // batch_size + 1:03d}_of_{total:03d}.json"
        path.write_text(
            json.dumps(
                {"collection": name, "batch_records": len(chunk), "records": chunk},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        written.append(str(path))
        if not records:
            break
    return written


def _csv_value(value: object) -> str:
    if isinstance(value, list):
        return ";".join(_text(item) for item in value)
    return _text(value)


def write_reports(directory: Path, rows: list[dict], summary: dict, generated_utc: str) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / JSON_REPORT_NAME
    csv_path = directory / CSV_REPORT_NAME
    markdown_path = directory / MARKDOWN_REPORT_NAME

    report = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "generated_utc": generated_utc,
        "read_only": True,
        "zoho_modified": False,
        "financial_data_included": False,
        "summary": summary,
        "salesorders": rows,
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(REPORT_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column, "")) for column in REPORT_COLUMNS})

    lines = [
        "# FRP Depot Sales Order reference audit",
        "",
        f"Generated {generated_utc} UTC by {TOOL_NAME} {TOOL_VERSION}. READ-ONLY: "
        f"{summary['zoho_writes']} Zoho writes.",
        "",
        "No price, tax, discount, total, balance or payment value appears in this audit.",
        "",
        "| Count | Value |",
        "| --- | --- |",
        f"| Sales Orders | {summary['salesorders_total']} |",
        f"| Complete PO + Quote | {summary['complete_po_and_quote']} |",
        f"| Missing client PO | {summary['missing_po']} |",
        f"| Client PO not a single reference | {summary['ambiguous_po_format']} |",
        f"| Missing quote | {summary['missing_quote']} |",
        f"| Ambiguous quote | {summary['ambiguous_quote']} |",
        f"| Quote from a live Zoho link | {summary['direct_quote_links']} |",
        f"| Quote inferred from exact client PO | {summary['inferred_exact_po_quote_links']} |",
        f"| Quote inferred from a quote number in the reference | {summary['inferred_quote_number_text_links']} |",
        f"| Orders with linked invoices | {summary['orders_with_linked_invoices']} |",
        f"| Orders with no linked invoice | {summary['missing_invoice']} |",
        f"| Ambiguous invoice | {summary['ambiguous_invoice']} |",
        f"| Needs manual review | {summary['needs_manual_review']} |",
        f"| Date range | {summary['date_range']['earliest']} to {summary['date_range']['latest']} |",
        f"| Estimates enumerated | {summary['estimates_enumerated']} |",
        f"| Invoices enumerated | {summary['invoices_enumerated']} |",
        "",
        "## Orders needing manual review",
        "",
    ]
    review_rows = [row for row in rows if row["review_state"] != REVIEW_STATE_COMPLETE]
    if review_rows:
        lines.extend([
            "| Sales Order | Date | Customer | Client PO | Quote | Review state |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for row in review_rows:
            lines.append(
                "| {number} | {date} | {customer} | {po} | {quote} | {state} |".format(
                    number=row["salesorder_number"] or row["salesorder_id"],
                    date=row["date"],
                    customer=row["customer_name"].replace("|", "/"),
                    po=row["client_po_original"].replace("|", "/") or "(none)",
                    quote=row["quote_number"] or "(none)",
                    state=row["review_state"],
                )
            )
    else:
        lines.append("Every Sales Order resolved. Nothing needs manual review.")
    lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")

    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(markdown_path)}


def append_receipt(paths: dict, summary: dict) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": "zoho_sales_order_reference_audit_read_only",
        "evidence": [paths["json"], paths["csv"], paths["markdown"]],
        "zoho_writes": 0,
        "salesorders_total": summary["salesorders_total"],
    }
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _needs_detail(projection: dict, id_keys: tuple[str, ...]) -> bool:
    return not any(projection.get(key) for key in id_keys)


def run_audit(fetch, output_directory: Path, generated_utc: str | None = None) -> dict:
    """The whole audit against an injected read function. No credential, no
    network and no filesystem outside output_directory is reached from here."""
    generated_utc = generated_utc or datetime.now(timezone.utc).isoformat()
    counts = {
        "salesorder_details": 0,
        "estimate_details": 0,
        "invoice_details": 0,
    }

    order_rows = fetch_all(fetch, "/books/v3/salesorders", "salesorders")
    estimate_rows = fetch_all(fetch, "/books/v3/estimates", "estimates")
    invoice_rows = fetch_all(fetch, "/books/v3/invoices", "invoices")
    counts["estimates"] = len(estimate_rows)
    counts["invoices"] = len(invoice_rows)

    orders: list[dict] = []
    for row in order_rows:
        order_id = require_id(row.get("salesorder_id"), "sales order salesorder_id")
        detail = (fetch(f"/books/v3/salesorders/{order_id}", {}) or {}).get("salesorder") or {}
        require_identity(detail, "salesorder_id", order_id, "sales order")
        counts["salesorder_details"] += 1
        orders.append(project_salesorder(detail, detail_fetched=True))
    require_unique(orders, "salesorder_id", "sales order")

    order_customers = {order.get("customer_id", "") for order in orders if order.get("customer_id")}

    estimates: list[dict] = []
    for row in estimate_rows:
        projection = project_estimate(row, detail_fetched=False)
        if _needs_detail(projection, ("linked_salesorder_ids", "linked_salesorder_numbers")) and (
            projection.get("customer_id") in order_customers
        ):
            estimate_id = projection["estimate_id"]
            detail = (fetch(f"/books/v3/estimates/{estimate_id}", {}) or {}).get("estimate") or {}
            require_identity(detail, "estimate_id", estimate_id, "estimate")
            counts["estimate_details"] += 1
            projection = project_estimate(detail, detail_fetched=True)
        estimates.append(projection)
    require_unique(estimates, "estimate_id", "estimate")

    invoices: list[dict] = []
    for row in invoice_rows:
        projection = project_invoice(row, detail_fetched=False)
        if _needs_detail(projection, ("linked_salesorder_ids", "linked_salesorder_numbers")) and (
            projection.get("customer_id") in order_customers
        ):
            invoice_id = projection["invoice_id"]
            detail = (fetch(f"/books/v3/invoices/{invoice_id}", {}) or {}).get("invoice") or {}
            require_identity(detail, "invoice_id", invoice_id, "invoice")
            counts["invoice_details"] += 1
            projection = project_invoice(detail, detail_fetched=True)
        invoices.append(projection)
    require_unique(invoices, "invoice_id", "invoice")

    batch_directory = output_directory / "batches"
    batch_paths = {
        "salesorders": write_batches(batch_directory, "salesorders", orders),
        "estimates": write_batches(batch_directory, "estimates", estimates),
        "invoices": write_batches(batch_directory, "invoices", invoices),
    }

    inventory = Inventory(orders, estimates, invoices)
    rows = build_rows(inventory)
    summary = summarize(rows, counts)
    paths = write_reports(output_directory, rows, summary, generated_utc)
    return {"paths": paths, "batch_paths": batch_paths, "summary": summary, "rows": rows}


def live_fetch(token: str, domain: str, organization_id: str):
    def fetch(path: str, params: dict | None = None) -> dict:
        query = dict(params or {})
        query["organization_id"] = organization_id
        return zoho_tool.api_get(token, domain, f"{path}?{urlencode(query)}")

    return fetch


def command_run(args: argparse.Namespace) -> int:
    vault = zoho_tool.load_vault()
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = str(vault["api_domain"])
    organization_id = str(vault["books_organization_id"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_directory = Path(args.output_root or OUTPUT_ROOT) / stamp
    result = run_audit(live_fetch(token, domain, organization_id), output_directory)
    # The vault is deliberately NOT written back. Nothing here changes it, and a
    # second lane may legitimately be holding it open at the same moment.
    append_receipt(result["paths"], result["summary"])
    print(
        json.dumps(
            {
                "report_paths": result["paths"],
                "batch_files": {name: len(paths) for name, paths in result["batch_paths"].items()},
                "summary": result["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FRP Depot READ-ONLY Zoho Books Sales Order reference audit"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Audit every Sales Order and write the three reports")
    run.add_argument("--output-root", default=None)
    run.set_defaults(func=command_run)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except (AuditError, zoho_tool.ZohoError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
