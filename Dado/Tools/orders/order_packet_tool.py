#!/usr/bin/env python
"""Validate one FRP Depot future-order intake packet without network or business writes.

The tool consolidates evidence once, rejects incomplete/unsafe packets early, routes
requested actions to existing named tools/skills, and emits stage INPUTS only for the
reusable Quote and Draft-Invoice paths. It never stages, commits, emails or calls a
network endpoint.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
TOOL_NAME = "FRP Depot Future Order Packet Validator"
INTERNAL_REFERENCE_RE = re.compile(r"^(?:QT|SO|INV)-\d+$", re.IGNORECASE)
PACKET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PERCENT_RE = re.compile(r"^(?:\d+(?:\.\d+)?|\.\d+)%$")
ALLOWED_ATTACHMENT_ROLES = {"client_po", "pricing", "specifications", "correspondence", "other"}
ALLOWED_PO_STATES = {"issued", "none", "ambiguous"}
ALLOWED_PO_EVIDENCE = {"client_po_attachment", "customer_email", "linked_customer_record", "none"}
ALLOWED_CUSTOMER_STATES = {"existing", "create_required"}
ALLOWED_ACTIONS = {
    ("quote", "create_draft"),
    ("sales_order", "create_draft"),
    ("invoice", "create_draft"),
    ("invoice", "revise_existing"),
    ("email_draft", "reply_all"),
    ("email_draft", "forward_or_new_draft"),
    ("follow_up", "review_only"),
}
ALLOWED_RECIPIENT_VERIFICATION = {"outlook_thread", "live_zoho_contact", "rachad_instruction"}
ALLOWED_DISCOUNT_KINDS = {"none", "percentage", "amount"}
ALLOWED_TAX_STATES = {"taxable", "exempt", "out_of_scope"}
ALLOWED_AVAILABILITY = {
    "sufficient_physical_stock",
    "backorder_accepted",
    "lead_time_accepted",
    "not_applicable",
}
POSTCOMMIT_GATES = [
    "fresh_live_api_reference_number_equals_exact_client_po_or_explicit_no_po_exception",
    "fresh_rendered_pdf_or_preview_visibly_displays_the_same_reference",
    "linked_sales_order_and_invoice_references_are_checked_independently",
    "all_protected_business_fields_and_line_identity_order_are_unchanged",
    "every_requested_action_is_accounted_for_separately",
    "email_remains_a_verified_draft_until_rachad_sends_it",
    "receipt_records_live_ids_plan_hashes_and_rendered_evidence",
]
TOP_KEYS = {
    "schema_version", "packet_id", "customer", "source_review", "client_po",
    "requested_actions", "commercial_terms", "lines",
}
CUSTOMER_KEYS = {"name", "state", "zoho_customer_id", "source"}
SOURCE_REVIEW_KEYS = {
    "full_thread_read", "outlook_conversation_id", "latest_external_message_id",
    "latest_external_received_utc", "thread_source", "internal_document_numbers_seen",
    "attachments",
}
ATTACHMENT_KEYS = {"path", "role", "inspected", "sha256", "bytes", "source"}
PO_KEYS = {
    "state", "value", "source_value_exact", "evidence_kind", "evidence_locator",
    "no_po_exception_authorized_by", "no_po_exception_source",
}
ACTION_KEYS = {
    "kind", "operation", "required", "reference_number", "target_record_id",
    "target_record_number", "recipients", "attachments", "source",
}
RECIPIENT_KEYS = {"email", "source", "verification"}
COMMERCIAL_KEYS = {
    "document_date", "quote_expiry_date", "invoice_due_date", "currency",
    "billing_address", "billing_address_id", "shipping_address", "shipping_address_id",
    "shipping_instructions", "payment_terms", "required_date", "document_notes",
}
SOURCED_KEYS = {"value", "source"}
LINE_KEYS = {
    "item_id", "item_name", "sku", "item_source", "description", "unit",
    "quantity", "rate", "discount", "tax", "availability",
}
DISCOUNT_KEYS = {"kind", "value", "source"}
TAX_KEYS = {"state", "tax_id", "percentage", "source"}
AVAILABILITY_KEYS = {"state", "physical_available_for_sale", "checked_utc", "source"}


class OrderPacketError(ValueError):
    pass


def closed(obj: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise OrderPacketError(f"{label} must be an object.")
    actual = set(obj)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise OrderPacketError(f"{label} has a non-closed schema; missing={missing}, extra={extra}.")
    return obj


def text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise OrderPacketError(f"{label} must be text.")
    if "\n" in value or "\r" in value:
        raise OrderPacketError(f"{label} must be one line.")
    if value != value.strip():
        raise OrderPacketError(f"{label} has leading or trailing whitespace.")
    if not value and not allow_empty:
        raise OrderPacketError(f"{label} must not be blank.")
    return value


def positive_id(value: Any, label: str, *, allow_empty: bool = False) -> str:
    raw = text(value, label, allow_empty=allow_empty)
    if not raw and allow_empty:
        return raw
    if not raw.isdigit() or int(raw) <= 0:
        raise OrderPacketError(f"{label} must be canonical positive-ID text.")
    return raw


def iso_datetime(value: Any, label: str) -> str:
    raw = text(value, label)
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OrderPacketError(f"{label} must be ISO-8601 date-time text.") from exc
    return raw


def iso_date(value: Any, label: str, *, allow_empty: bool = False) -> str:
    raw = text(value, label, allow_empty=allow_empty)
    if not raw and allow_empty:
        return raw
    try:
        date.fromisoformat(raw)
    except ValueError as exc:
        raise OrderPacketError(f"{label} must be YYYY-MM-DD.") from exc
    return raw


def decimal_value(value: Any, label: str, *, positive: bool = False, allow_zero: bool = True) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise OrderPacketError(f"{label} must be decimal text or number.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise OrderPacketError(f"{label} is not a finite decimal.") from exc
    if not amount.is_finite():
        raise OrderPacketError(f"{label} is not finite.")
    if positive and amount <= 0:
        raise OrderPacketError(f"{label} must be greater than zero.")
    if not allow_zero and amount == 0:
        raise OrderPacketError(f"{label} must not be zero.")
    if amount < 0:
        raise OrderPacketError(f"{label} must not be negative.")
    return amount


def sourced(raw: Any, label: str, *, allow_empty_value: bool = True) -> dict[str, str]:
    obj = closed(raw, SOURCED_KEYS, label)
    return {
        "value": text(obj["value"], f"{label}.value", allow_empty=allow_empty_value),
        "source": text(obj["source"], f"{label}.source"),
    }


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_attachments(rows: Any, base_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    if not isinstance(rows, list):
        raise OrderPacketError("source_review.attachments must be a list.")
    clean: list[dict[str, Any]] = []
    resolved_by_declared: dict[str, Path] = {}
    for index, raw in enumerate(rows):
        label = f"source_review.attachments[{index}]"
        row = closed(raw, ATTACHMENT_KEYS, label)
        declared = text(row["path"], f"{label}.path")
        candidate = Path(declared)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise OrderPacketError(f"{label}.path does not resolve to a live local file.") from exc
        if not resolved.is_file():
            raise OrderPacketError(f"{label}.path is not a file.")
        lowered = str(resolved).replace("/", "\\").casefold()
        if lowered == "c:\\agentteam" or lowered.startswith("c:\\agentteam\\"):
            raise OrderPacketError(f"{label}.path crosses the closed TDI agent-tree boundary.")
        role = text(row["role"], f"{label}.role")
        if role not in ALLOWED_ATTACHMENT_ROLES:
            raise OrderPacketError(f"{label}.role is not allowed.")
        if row["inspected"] is not True:
            raise OrderPacketError(f"{label}.inspected must be true after content review.")
        expected_hash = text(row["sha256"], f"{label}.sha256").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise OrderPacketError(f"{label}.sha256 must be full lowercase SHA-256.")
        expected_bytes = row["bytes"]
        if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes <= 0:
            raise OrderPacketError(f"{label}.bytes must be a positive integer.")
        actual_bytes = resolved.stat().st_size
        actual_hash = file_sha256(resolved)
        if actual_bytes != expected_bytes or actual_hash != expected_hash:
            raise OrderPacketError(
                f"{label} no longer matches its evidence: bytes {actual_bytes}/{expected_bytes}, "
                f"sha256 {actual_hash}/{expected_hash}."
            )
        resolved_by_declared[declared] = resolved
        clean.append({
            "path": str(resolved), "role": role, "inspected": True,
            "sha256": actual_hash, "bytes": actual_bytes,
            "source": text(row["source"], f"{label}.source"),
        })
    return clean, resolved_by_declared


def validate_packet(raw: Any, base_dir: Path) -> dict[str, Any]:
    packet = closed(raw, TOP_KEYS, "order packet")
    if packet["schema_version"] != SCHEMA_VERSION:
        raise OrderPacketError(f"schema_version must be exactly {SCHEMA_VERSION}.")
    packet_id = text(packet["packet_id"], "packet_id")
    if not PACKET_ID_RE.fullmatch(packet_id):
        raise OrderPacketError("packet_id must be 3-80 lowercase slug characters.")

    customer_raw = closed(packet["customer"], CUSTOMER_KEYS, "customer")
    customer_state = text(customer_raw["state"], "customer.state")
    if customer_state not in ALLOWED_CUSTOMER_STATES:
        raise OrderPacketError("customer.state must be existing or create_required.")
    customer_id = positive_id(
        customer_raw["zoho_customer_id"], "customer.zoho_customer_id", allow_empty=True
    )
    if customer_state == "existing" and not customer_id:
        raise OrderPacketError("An existing customer requires zoho_customer_id.")
    if customer_state == "create_required" and customer_id:
        raise OrderPacketError("A create_required customer must not claim a Zoho customer ID.")
    customer = {
        "name": text(customer_raw["name"], "customer.name"),
        "state": customer_state,
        "zoho_customer_id": customer_id,
        "source": text(customer_raw["source"], "customer.source"),
    }

    review_raw = closed(packet["source_review"], SOURCE_REVIEW_KEYS, "source_review")
    if review_raw["full_thread_read"] is not True:
        raise OrderPacketError("source_review.full_thread_read must be true before any order staging.")
    internal_seen = review_raw["internal_document_numbers_seen"]
    if not isinstance(internal_seen, list):
        raise OrderPacketError("source_review.internal_document_numbers_seen must be a list.")
    internal_numbers = [text(v, f"internal_document_numbers_seen[{i}]") for i, v in enumerate(internal_seen)]
    attachments, attachment_lookup = validate_attachments(review_raw["attachments"], base_dir)
    source_review = {
        "full_thread_read": True,
        "outlook_conversation_id": text(review_raw["outlook_conversation_id"], "source_review.outlook_conversation_id"),
        "latest_external_message_id": text(review_raw["latest_external_message_id"], "source_review.latest_external_message_id"),
        "latest_external_received_utc": iso_datetime(review_raw["latest_external_received_utc"], "source_review.latest_external_received_utc"),
        "thread_source": text(review_raw["thread_source"], "source_review.thread_source"),
        "internal_document_numbers_seen": internal_numbers,
        "attachments": attachments,
    }

    po_raw = closed(packet["client_po"], PO_KEYS, "client_po")
    po_state = text(po_raw["state"], "client_po.state")
    if po_state not in ALLOWED_PO_STATES:
        raise OrderPacketError("client_po.state is not allowed.")
    if po_state == "ambiguous":
        raise OrderPacketError("client_po.state ambiguous is fail-closed; ask Rachad one question.")
    po_value = text(po_raw["value"], "client_po.value", allow_empty=True)
    po_exact = text(po_raw["source_value_exact"], "client_po.source_value_exact", allow_empty=True)
    evidence_kind = text(po_raw["evidence_kind"], "client_po.evidence_kind")
    if evidence_kind not in ALLOWED_PO_EVIDENCE:
        raise OrderPacketError("client_po.evidence_kind is not allowed.")
    evidence_locator = text(po_raw["evidence_locator"], "client_po.evidence_locator", allow_empty=True)
    exception_by = text(
        po_raw["no_po_exception_authorized_by"],
        "client_po.no_po_exception_authorized_by", allow_empty=True,
    )
    exception_source = text(
        po_raw["no_po_exception_source"], "client_po.no_po_exception_source", allow_empty=True
    )
    if po_state == "issued":
        if not po_value or po_value != po_exact:
            raise OrderPacketError("Issued client PO value must byte-equal source_value_exact; do not normalize it.")
        if INTERNAL_REFERENCE_RE.fullmatch(po_value) or any(
            po_value.casefold() == value.casefold() for value in internal_numbers
        ):
            raise OrderPacketError("The proposed client PO is an internal FRP Depot QT/SO/INV number.")
        if evidence_kind == "none" or not evidence_locator:
            raise OrderPacketError("Issued client PO requires direct customer evidence.")
        if evidence_kind == "client_po_attachment":
            resolved = attachment_lookup.get(evidence_locator)
            if resolved is None:
                try:
                    target = Path(evidence_locator).resolve(strict=True)
                except OSError as exc:
                    raise OrderPacketError("client_po.evidence_locator is not a declared attachment.") from exc
                if not any(Path(row["path"]) == target and row["role"] == "client_po" for row in attachments):
                    raise OrderPacketError("client_po attachment evidence must be declared with role client_po.")
            elif not any(Path(row["path"]) == resolved and row["role"] == "client_po" for row in attachments):
                raise OrderPacketError("client_po attachment evidence must have role client_po.")
        if exception_by or exception_source:
            raise OrderPacketError("Issued client PO must not carry a no-PO exception.")
    else:
        if po_value or po_exact or evidence_locator or evidence_kind != "none":
            raise OrderPacketError("No-PO packet must keep PO values/evidence empty and evidence_kind none.")
        if exception_by != "Rachad Homsi" or not exception_source:
            raise OrderPacketError("No-PO exception requires Rachad Homsi and a direct decision source.")
    client_po = {
        "state": po_state, "value": po_value, "source_value_exact": po_exact,
        "evidence_kind": evidence_kind, "evidence_locator": evidence_locator,
        "no_po_exception_authorized_by": exception_by,
        "no_po_exception_source": exception_source,
    }

    terms_raw = closed(packet["commercial_terms"], COMMERCIAL_KEYS, "commercial_terms")
    commercial = {key: sourced(terms_raw[key], f"commercial_terms.{key}") for key in COMMERCIAL_KEYS}
    commercial["document_date"]["value"] = iso_date(commercial["document_date"]["value"], "commercial_terms.document_date.value")
    commercial["quote_expiry_date"]["value"] = iso_date(
        commercial["quote_expiry_date"]["value"], "commercial_terms.quote_expiry_date.value", allow_empty=True
    )
    commercial["invoice_due_date"]["value"] = iso_date(
        commercial["invoice_due_date"]["value"], "commercial_terms.invoice_due_date.value", allow_empty=True
    )
    currency = commercial["currency"]["value"]
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise OrderPacketError("commercial_terms.currency.value must be a three-letter uppercase code.")
    for key in ("billing_address_id", "shipping_address_id"):
        value = commercial[key]["value"]
        if value:
            positive_id(value, f"commercial_terms.{key}.value")

    lines_raw = packet["lines"]
    if not isinstance(lines_raw, list) or not lines_raw:
        raise OrderPacketError("lines must contain at least one existing Zoho item.")
    lines: list[dict[str, Any]] = []
    for index, raw_line in enumerate(lines_raw):
        label = f"lines[{index}]"
        row = closed(raw_line, LINE_KEYS, label)
        item_id = positive_id(row["item_id"], f"{label}.item_id")
        description = sourced(row["description"], f"{label}.description")
        quantity = sourced(row["quantity"], f"{label}.quantity", allow_empty_value=False)
        rate = sourced(row["rate"], f"{label}.rate", allow_empty_value=False)
        quantity_decimal = decimal_value(quantity["value"], f"{label}.quantity.value", positive=True)
        decimal_value(rate["value"], f"{label}.rate.value")
        discount_raw = closed(row["discount"], DISCOUNT_KEYS, f"{label}.discount")
        discount_kind = text(discount_raw["kind"], f"{label}.discount.kind")
        if discount_kind not in ALLOWED_DISCOUNT_KINDS:
            raise OrderPacketError(f"{label}.discount.kind is not allowed.")
        discount_value = text(discount_raw["value"], f"{label}.discount.value")
        discount_source = text(discount_raw["source"], f"{label}.discount.source")
        if discount_kind == "none":
            if decimal_value(discount_value, f"{label}.discount.value") != 0:
                raise OrderPacketError(f"{label}.discount none must carry zero.")
        elif discount_kind == "percentage":
            if not PERCENT_RE.fullmatch(discount_value):
                raise OrderPacketError(
                    f"{label}.discount percentage must be an exact string ending in %; a bare number is flat currency in Zoho."
                )
            percent = Decimal(discount_value[:-1])
            if percent <= 0 or percent > 100:
                raise OrderPacketError(f"{label}.discount percentage is outside 0-100.")
        else:
            decimal_value(discount_value, f"{label}.discount.value")
        tax_raw = closed(row["tax"], TAX_KEYS, f"{label}.tax")
        tax_state = text(tax_raw["state"], f"{label}.tax.state")
        if tax_state not in ALLOWED_TAX_STATES:
            raise OrderPacketError(f"{label}.tax.state is not allowed.")
        tax_id = text(tax_raw["tax_id"], f"{label}.tax.tax_id", allow_empty=True)
        tax_percent = text(tax_raw["percentage"], f"{label}.tax.percentage", allow_empty=True)
        tax_source = text(tax_raw["source"], f"{label}.tax.source")
        if tax_state == "taxable":
            positive_id(tax_id, f"{label}.tax.tax_id")
            decimal_value(tax_percent, f"{label}.tax.percentage", positive=True)
        elif tax_id or tax_percent:
            raise OrderPacketError(f"{label}.tax {tax_state} must not claim tax ID or percentage.")
        availability_raw = closed(row["availability"], AVAILABILITY_KEYS, f"{label}.availability")
        availability_state = text(availability_raw["state"], f"{label}.availability.state")
        if availability_state not in ALLOWED_AVAILABILITY:
            raise OrderPacketError(f"{label}.availability.state is not allowed.")
        physical_raw = text(
            availability_raw["physical_available_for_sale"],
            f"{label}.availability.physical_available_for_sale", allow_empty=True,
        )
        availability_source = text(availability_raw["source"], f"{label}.availability.source")
        checked_utc = iso_datetime(availability_raw["checked_utc"], f"{label}.availability.checked_utc")
        if availability_state != "not_applicable":
            if not physical_raw:
                raise OrderPacketError(f"{label}.availability requires live physical quantity.")
            physical = decimal_value(physical_raw, f"{label}.availability.physical_available_for_sale")
            lowered_source = availability_source.casefold()
            if "physical available for sale" not in lowered_source and "actual_available_stock" not in lowered_source:
                raise OrderPacketError(
                    f"{label}.availability source must explicitly identify Physical Available for Sale or actual_available_stock."
                )
            if availability_state == "sufficient_physical_stock" and physical < quantity_decimal:
                raise OrderPacketError(f"{label} claims sufficient physical stock but has less than ordered quantity.")
        lines.append({
            "item_id": item_id,
            "item_name": text(row["item_name"], f"{label}.item_name"),
            "sku": text(row["sku"], f"{label}.sku"),
            "item_source": text(row["item_source"], f"{label}.item_source"),
            "description": description,
            "unit": text(row["unit"], f"{label}.unit"),
            "quantity": quantity,
            "rate": rate,
            "discount": {"kind": discount_kind, "value": discount_value, "source": discount_source},
            "tax": {"state": tax_state, "tax_id": tax_id, "percentage": tax_percent, "source": tax_source},
            "availability": {
                "state": availability_state,
                "physical_available_for_sale": physical_raw,
                "checked_utc": checked_utc,
                "source": availability_source,
            },
        })

    actions_raw = packet["requested_actions"]
    if not isinstance(actions_raw, list) or not actions_raw:
        raise OrderPacketError("requested_actions must list every requested action.")
    actions: list[dict[str, Any]] = []
    seen_actions: set[tuple[str, str, str]] = set()
    document_action_seen = False
    for index, raw_action in enumerate(actions_raw):
        label = f"requested_actions[{index}]"
        action = closed(raw_action, ACTION_KEYS, label)
        kind = text(action["kind"], f"{label}.kind")
        operation = text(action["operation"], f"{label}.operation")
        if (kind, operation) not in ALLOWED_ACTIONS:
            raise OrderPacketError(f"{label} names an unapproved action/operation; sending is never accepted.")
        if not isinstance(action["required"], bool):
            raise OrderPacketError(f"{label}.required must be boolean.")
        reference = text(action["reference_number"], f"{label}.reference_number", allow_empty=True)
        target_id = positive_id(action["target_record_id"], f"{label}.target_record_id", allow_empty=True)
        target_number = text(action["target_record_number"], f"{label}.target_record_number", allow_empty=True)
        if operation == "revise_existing" and (not target_id or not target_number):
            raise OrderPacketError(f"{label} revision needs exact existing record ID and number.")
        if operation != "revise_existing" and (target_id or target_number):
            raise OrderPacketError(f"{label} create/non-record action must not claim an existing target.")
        if kind in {"quote", "sales_order", "invoice"}:
            document_action_seen = True
            if po_state == "issued" and reference != po_value:
                raise OrderPacketError(f"{label}.reference_number must byte-equal the customer PO.")
            if po_state == "none" and reference:
                raise OrderPacketError(f"{label} must keep Reference# blank under the approved no-PO exception.")
        recipients_raw = action["recipients"]
        if not isinstance(recipients_raw, list):
            raise OrderPacketError(f"{label}.recipients must be a list.")
        recipients: list[dict[str, str]] = []
        for r_index, raw_recipient in enumerate(recipients_raw):
            r_label = f"{label}.recipients[{r_index}]"
            recipient = closed(raw_recipient, RECIPIENT_KEYS, r_label)
            email = text(recipient["email"], f"{r_label}.email").casefold()
            if not EMAIL_RE.fullmatch(email):
                raise OrderPacketError(f"{r_label}.email is invalid.")
            verification = text(recipient["verification"], f"{r_label}.verification")
            if verification not in ALLOWED_RECIPIENT_VERIFICATION:
                raise OrderPacketError(f"{r_label}.verification is not allowed; body-only addresses are unsafe.")
            recipients.append({
                "email": email,
                "source": text(recipient["source"], f"{r_label}.source"),
                "verification": verification,
            })
        if kind == "email_draft" and not recipients:
            raise OrderPacketError(f"{label} email draft requires verified recipients.")
        if kind != "email_draft" and recipients:
            raise OrderPacketError(f"{label} non-email action must not carry recipients.")
        attachments_raw = action["attachments"]
        if not isinstance(attachments_raw, list):
            raise OrderPacketError(f"{label}.attachments must be a list.")
        action_attachments = [text(v, f"{label}.attachments[{i}]") for i, v in enumerate(attachments_raw)]
        key = (kind, operation, target_id)
        if key in seen_actions:
            raise OrderPacketError(f"{label} duplicates an earlier requested action.")
        seen_actions.add(key)
        actions.append({
            "kind": kind, "operation": operation, "required": action["required"],
            "reference_number": reference, "target_record_id": target_id,
            "target_record_number": target_number, "recipients": recipients,
            "attachments": action_attachments,
            "source": text(action["source"], f"{label}.source"),
        })
    if not document_action_seen:
        raise OrderPacketError("A future-order packet must contain a Quote, Sales Order or Invoice action.")

    action_pairs = {(row["kind"], row["operation"]) for row in actions}
    if ("quote", "create_draft") in action_pairs:
        expiry = commercial["quote_expiry_date"]["value"]
        if expiry and date.fromisoformat(expiry) < date.fromisoformat(commercial["document_date"]["value"]):
            raise OrderPacketError("quote_expiry_date is before document_date.")
    if ("invoice", "create_draft") in action_pairs:
        due = commercial["invoice_due_date"]["value"]
        if not due:
            raise OrderPacketError("Draft Invoice action requires invoice_due_date.")
        if date.fromisoformat(due) < date.fromisoformat(commercial["document_date"]["value"]):
            raise OrderPacketError("invoice_due_date is before document_date.")

    return {
        "schema_version": SCHEMA_VERSION,
        "packet_id": packet_id,
        "customer": customer,
        "source_review": source_review,
        "client_po": client_po,
        "requested_actions": actions,
        "commercial_terms": commercial,
        "lines": lines,
    }


def route_actions(packet: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    routes: list[dict[str, Any]] = []
    blockers: list[str] = []
    if packet["customer"]["state"] == "create_required":
        reason = (
            "Customer must first be created and verified through zoho_customer_quote_tool.py "
            "stage-customer / commit-customer under its own immutable plan and APPROVED."
        )
        routes.append({"kind": "customer", "route": "zoho_customer_quote_tool.py", "state": "PREREQUISITE", "detail": reason})
        blockers.append(reason)
    for action in packet["requested_actions"]:
        kind = action["kind"]
        operation = action["operation"]
        if kind == "quote":
            route = "zoho_customer_quote_tool.py stage-quote"
            state = "READY_INPUT_GENERATED" if packet["customer"]["state"] == "existing" else "BLOCKED_PREREQUISITE"
            detail = "Stages one Draft Estimate; commit still requires that plan's exact APPROVED."
        elif kind == "sales_order":
            route = "NO_REUSABLE_COMMISSIONED_FUTURE_ORDER_TOOL"
            state = "BLOCKED_SEPARATE_COMMISSION_REQUIRED"
            detail = (
                "Current zoho_sales_order_tool.py is fixed to SCT PO26330 and cannot be reused. "
                "Do not stage or write this Sales Order until Rachad separately commissions a suitable named tool."
            )
        elif kind == "invoice" and operation == "create_draft":
            route = "zoho_invoice_revision_tool.py stage-create"
            state = "READY_INPUT_GENERATED" if packet["customer"]["state"] == "existing" else "BLOCKED_PREREQUISITE"
            detail = "Stages one new Draft Invoice; cannot send it; commit requires that plan's exact APPROVED."
        elif kind == "invoice":
            route = "zoho_invoice_revision_tool.py stage"
            state = "READY_SPECIALIZED_INPUT_REQUIRED"
            detail = "Existing-invoice revision requires a fresh protected live read and exact per-change input."
        elif kind == "email_draft":
            route = "outlook-threaded-reply-drafts skill"
            state = "READY_DRAFT_ONLY"
            detail = "Prepare and verify a draft only; Rachad sends it himself."
        else:
            route = "manual read-only review"
            state = "READY_READ_ONLY"
            detail = "Account for this follow-up separately before completion."
        if action["required"] and state.startswith("BLOCKED"):
            blockers.append(detail)
        routes.append({
            "kind": kind, "operation": operation, "required": action["required"],
            "route": route, "state": state, "detail": detail,
        })
    return routes, blockers


def build_quote_input(packet: dict[str, Any]) -> dict[str, Any]:
    terms = packet["commercial_terms"]
    result: dict[str, Any] = {
        "customer_id": packet["customer"]["zoho_customer_id"],
        "date": terms["document_date"]["value"],
        "line_items": [],
    }
    if packet["client_po"]["state"] == "issued":
        result["reference_number"] = packet["client_po"]["value"]
    if terms["quote_expiry_date"]["value"]:
        result["expiry_date"] = terms["quote_expiry_date"]["value"]
    if terms["document_notes"]["value"]:
        result["notes"] = terms["document_notes"]["value"]
    if terms["payment_terms"]["value"]:
        result["terms"] = terms["payment_terms"]["value"]
    for line in packet["lines"]:
        row: dict[str, Any] = {
            "item_id": line["item_id"], "name": line["item_name"],
            "description": line["description"]["value"],
            "quantity": exact_json_number(line["quantity"]["value"], "quote quantity"),
            "rate": exact_json_number(line["rate"]["value"], "quote rate"),
            "unit": line["unit"],
            "quantity_source": line["quantity"]["source"], "rate_source": line["rate"]["source"],
        }
        if line["discount"]["kind"] != "none":
            row["discount"] = line["discount"]["value"]
            row["discount_source"] = line["discount"]["source"]
        if line["tax"]["state"] == "taxable":
            row["tax_id"] = line["tax"]["tax_id"]
            row["tax_source"] = line["tax"]["source"]
        result["line_items"].append(row)
    return result


def build_invoice_input(packet: dict[str, Any]) -> dict[str, Any]:
    terms = packet["commercial_terms"]
    fields: dict[str, Any] = {
        "date": terms["document_date"],
        "due_date": terms["invoice_due_date"],
    }
    if packet["client_po"]["state"] == "issued":
        fields["reference_number"] = {
            "value": packet["client_po"]["value"],
            "source": f"{packet['client_po']['evidence_kind']}: {packet['client_po']['evidence_locator']}",
        }
    for source_key, target_key in (
        ("document_notes", "notes"), ("payment_terms", "terms"),
        ("billing_address_id", "billing_address_id"), ("shipping_address_id", "shipping_address_id"),
    ):
        if terms[source_key]["value"]:
            fields[target_key] = terms[source_key]
    lines: list[dict[str, Any]] = []
    for line in packet["lines"]:
        row: dict[str, Any] = {
            "item_id": line["item_id"], "item_name": line["item_name"],
            "quantity": line["quantity"], "rate": line["rate"],
            "discount": {"value": line["discount"]["value"], "source": line["discount"]["source"]},
            "description": line["description"],
        }
        if line["tax"]["state"] == "taxable":
            row["tax_id"] = {
                "value": line["tax"]["tax_id"], "source": line["tax"]["source"],
                "tax_percentage": line["tax"]["percentage"],
            }
        lines.append(row)
    return {
        "customer_id": packet["customer"]["zoho_customer_id"],
        "customer_name": packet["customer"]["name"],
        "fields": fields,
        "lines": lines,
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def exact_json_number(value: str, label: str) -> int | float:
    """Adapt exact packet decimal text to the quote tool's JSON-number contract."""
    amount = decimal_value(value, label)
    if amount == amount.to_integral_value():
        return int(amount)
    return float(amount)


def validate_command(args: argparse.Namespace) -> int:
    input_path = Path(args.input).resolve(strict=True)
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    packet = validate_packet(raw, input_path.parent)
    packet_sha = canonical_hash(packet)
    routes, blockers = route_actions(packet)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[dict[str, str]] = []
    actions = {(row["kind"], row["operation"]) for row in packet["requested_actions"]}
    if packet["customer"]["state"] == "existing" and ("quote", "create_draft") in actions:
        quote_path = output_dir / f"{packet['packet_id']}.quote_input.json"
        quote_input = build_quote_input(packet)
        write_json(quote_path, quote_input)
        generated.append({"kind": "quote_stage_input", "path": str(quote_path), "sha256": canonical_hash(quote_input)})
    if packet["customer"]["state"] == "existing" and ("invoice", "create_draft") in actions:
        invoice_path = output_dir / f"{packet['packet_id']}.invoice_input.json"
        invoice_input = build_invoice_input(packet)
        write_json(invoice_path, invoice_input)
        generated.append({"kind": "draft_invoice_stage_input", "path": str(invoice_path), "sha256": canonical_hash(invoice_input)})
    result = {
        "tool": TOOL_NAME,
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "READY_FOR_STAGING" if not blockers else "BLOCKED_BEFORE_STAGING",
        "packet_id": packet["packet_id"],
        "packet_sha256": packet_sha,
        "client_po_state": packet["client_po"]["state"],
        "client_po_exact": packet["client_po"]["value"],
        "full_thread_read": True,
        "attachments_verified": len(packet["source_review"]["attachments"]),
        "requested_action_count": len(packet["requested_actions"]),
        "line_count": len(packet["lines"]),
        "routes": routes,
        "blockers": blockers,
        "generated_stage_inputs": generated,
        "postcommit_gates": POSTCOMMIT_GATES,
        "business_writes": 0,
        "emails_sent": 0,
    }
    result_path = output_dir / f"{packet['packet_id']}.validated.json"
    write_json(result_path, result)
    print(json.dumps({"result": str(result_path), **result}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--input", required=True)
    validate.add_argument("--output-dir", required=True)
    validate.set_defaults(func=validate_command)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        return int(args.func(args))
    except (OrderPacketError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
