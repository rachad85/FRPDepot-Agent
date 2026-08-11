#!/usr/bin/env python
"""FRP Depot Zoho Books invoice revision and draft-creation tool.

Commissioned by Rachad Homsi on 2026-08-10. Building, testing, or staging with
this module is NOT approval of a business write.

This one named tool supports exactly TWO plan actions, and no third:

``invoice_revision``
    Revise ONE existing invoice with exactly one OAuth
    ``PUT /books/v3/invoices/{positive invoice_id}``, preserving its status.

``create_draft_invoice``
    Create ONE new invoice with exactly one OAuth ``POST /books/v3/invoices``,
    verified live afterwards to be in exactly ``draft`` status.

Neither action can ever mail, forward or otherwise transmit an invoice: this
module contains no mail transport of any kind, and the created invoice is
verified to carry no record of having been mailed.

REVISION -- only these fields may be intentionally changed:

* ``customer_id`` -- to an EXISTING live Zoho Books customer only;
* ``reference_number`` -- the customer PO / reference;
* ``date`` and ``due_date``;
* ``billing_address_id`` and ``shipping_address_id`` -- only addresses owned by
  the selected live customer;
* per existing line: ``quantity``, ``rate``, ``discount``, ``description`` and
  ``tax_id``;
* ``notes`` and ``terms``.

Every existing line item is always resent, exactly once, in its live order,
carrying its own ``line_item_id`` and ``item_id``, so no line can be deleted by
omission. Adding a line, removing a line, substituting an item, or changing a
line's item/name/unit/account is refused by the input schema, the plan
validator, the write allowlist and the read-back.

DRAFT CREATION -- one plan creates exactly one invoice:

* Zoho's own auto-numbering assigns the invoice number. No caller-supplied
  number and no auto-number override parameter exists anywhere in this module.
* the customer must already exist and be an active live customer; any address
  named must be owned by that customer.
* every line names an EXISTING active Zoho item by ID; there are no free-text
  or unlinked lines, and no item, customer or tax is ever created.
* quantity, rate, discount, description and tax ID are accepted only with an
  explicit source string recorded in the plan for each value.
* the customer's own currency is preserved; currency and exchange rate are not
  in the payload allowlist and can never be overridden.
* an independent Decimal calculation of the expected line totals, discount,
  tax and grand total is shown to Rachad before he approves, and is verified
  against the live invoice afterwards wherever Zoho's result is deterministic.

Permanently unreachable from BOTH actions: invoice deletion, voiding,
mark-as-draft, mark-as-sent, submission, approval, rejection, mailing an
invoice, reminders, payments, credit notes, attachments, templates, every bulk
route and every lifecycle route; converting an estimate into an invoice;
customer, item, tax and settings writes; the invoice number; the invoice
status; the currency and exchange rate; balance, payment, write-off and
adjustment figures; shipping charges; and custom fields. Revision additionally
can never add, remove or substitute a line. This module contains no mail
transport of any kind and exposes no generic write helper.

Each write is issued exactly once: any failure, timeout or indeterminate
result permanently locks the plan against retry, and no cleanup, deletion,
status change or second attempt is ever made.
"""
from __future__ import annotations

import argparse
from datetime import date as date_type, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import zoho_tool

TOOL_NAME = "FRP Depot Zoho Books Invoice Revision and Draft Creation Tool"
SCHEMA_VERSION = 1
ACTION = "invoice_revision"
CREATE_ACTION = "create_draft_invoice"
ACTIONS = (ACTION, CREATE_ACTION)
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_invoice_revision_plans"
PLAN_LIFETIME_HOURS = 24
# Rachad's ruling: his approval is ONE PLAIN WORD answering the displayed plan.
# Compared byte-exact -- no strip(), no case folding.
APPROVAL_WORD = "APPROVED"
UPDATE_SCOPE = "ZohoBooks.invoices.UPDATE"
# Permitted for exactly one draft-invoice POST and nothing else. There is
# deliberately no DELETE, no ALL and no fullaccess invoice scope anywhere.
CREATE_SCOPE = "ZohoBooks.invoices.CREATE"
DRAFT_STATUS = "draft"

INVOICE_PATH_RE = re.compile(r"^/books/v3/invoices/([1-9][0-9]*)$")
INVOICE_COLLECTION_PATH = "/books/v3/invoices"
INVOICE_COLLECTION_RE = re.compile(r"^/books/v3/invoices$")
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
ID_RE = re.compile(r"[1-9][0-9]*")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

CENT = Decimal("0.01")
MAX_LINES = 200
MAX_MONEY = Decimal("10000000.00")
MAX_QUANTITY = Decimal("1000000")

QUANTITY_RE = re.compile(r"^(0|[1-9][0-9]{0,6})(\.[0-9]{1,4})?$")
RATE_RE = re.compile(r"^(0|[1-9][0-9]{0,7})(\.[0-9]{1,6})?$")
DISCOUNT_AMOUNT_RE = re.compile(r"^(0|[1-9][0-9]{0,7})(\.[0-9]{1,2})?$")
DISCOUNT_PERCENT_RE = re.compile(r"^(0|[1-9][0-9]?|100)(\.[0-9]{1,2})?%$")
PERCENT_RE = re.compile(r"^(0|[1-9][0-9]?|100)(\.[0-9]{1,4})?$")

# ---------------------------------------------------------------------------
# The exact commissioned change surface
# ---------------------------------------------------------------------------

HEADER_CHANGE_FIELDS = (
    "customer_id",
    "reference_number",
    "date",
    "due_date",
    "billing_address_id",
    "shipping_address_id",
    "notes",
    "terms",
)
LINE_CHANGE_FIELDS = ("quantity", "rate", "discount", "description", "tax_id")

INPUT_FIELDS = {"invoice_id", "invoice_number", "changes", "line_changes"}
CHANGE_FIELDS = {"value", "source"}
TAX_CHANGE_FIELDS = {"value", "source", "old_tax_percentage", "new_tax_percentage"}
LINE_CHANGE_ENVELOPE_FIELDS = {"line_item_id", "item_id", "changes"}

PLAN_FIELDS = {
    "schema_version", "tool", "action", "created_utc", "expires_utc", "nonce",
    "approval_required", "origin", "organization", "payload", "risk",
    "live_evidence", "sha256",
}
ORIGIN_FIELDS = {"tool_path", "repo_root", "plan_dir"}
ORGANIZATION_FIELDS = {"organization_id", "name", "currency_code"}
RISK_FIELDS = {"atomic", "single_put", "email_sent", "note"}
EVIDENCE_FIELDS = {
    "invoice", "customer", "addresses", "taxes", "header_changes",
    "line_changes", "lines", "totals", "dependencies", "put_endpoint",
    "put_payload", "unprotected_keys", "email_sent",
}
INVOICE_EVIDENCE_FIELDS = {
    "invoice_id", "invoice_number", "status", "currency_id", "currency_code",
    "exchange_rate", "customer_id", "customer_name", "is_emailed",
    "before_state", "before_state_sha256",
    "protected_state", "protected_state_sha256",
}

# Fields Zoho recomputes, or that this tool intentionally changes. They leave
# the byte-exact protected fingerprint ONLY when the plan actually changes
# something that moves them, and every one of them is then verified by an
# explicit rule below (verify_header_changes / verify_lines / verify_totals).
VOLATILE_FIELDS = (
    "last_modified_time",
    "last_modified_by_id",
    "updated_time",
    "invoice_url",
)
CUSTOMER_LINKED_FIELDS = (
    "customer_id", "customer_name", "contact_persons", "contact_persons_details",
    "billing_address", "shipping_address", "billing_address_id",
    "shipping_address_id", "customer_custom_fields", "customer_custom_field_hash",
    "contact_category", "email", "phone", "mobile",
)
DERIVED_TOTAL_FIELDS = (
    "sub_total", "sub_total_inclusive_of_tax", "tax_total", "taxes", "total",
    "balance", "discount_total", "discount_amount", "roundoff_value",
)
DUE_DERIVED_FIELDS = ("due_days", "is_overdue", "days_to_due")
# The gross subtotal before line discounts, and the base-currency mirrors of the
# header totals. Zoho recomputes all of them from quantity x rate, so a CORRECT
# line-value revision must move them -- yet DERIVED_TOTAL_FIELDS never listed
# them. That is the defect that locked the 2026-08-11 QT-000029 quantity
# correction `indeterminate` in the sibling estimate tool AFTER its PUT had
# landed correctly. They leave the fingerprint only on a line-value change, and
# build_totals then predicts each one wherever Zoho's result is deterministic so
# verify_totals asserts it against a recomputed figure.
GROSS_SUBTOTAL_FIELDS = ("sub_total_exclusive_of_discount",)
BCY_TOTAL_FIELDS = ("bcy_sub_total", "bcy_discount_total", "bcy_tax_total", "bcy_total")

# Line keys resent verbatim from the live line so the PUT cannot drop identity
# or linkage. Anything not listed is left to Zoho, which keeps it because the
# line is addressed by its existing line_item_id.
LINE_PUT_KEYS = (
    "line_item_id", "item_id", "name", "description", "item_order", "unit",
    "quantity", "rate", "discount", "tax_id", "account_id", "warehouse_id",
    "salesorder_item_id", "product_type",
)
# Line fields Zoho recomputes when a commissioned line field changes.
LINE_DERIVED_FIELDS = (
    "item_total", "item_total_inclusive_of_tax", "discount_amount",
    "discount_amounts", "tax_percentage", "tax_name", "tax_type",
    "tax_specific_type", "line_item_taxes", "bcy_rate", "bcy_item_total",
    "pricebook_rate",
)

ALLOWED_STATUSES = ("draft", "sent")
ALLOWED_PUT_KEYS = {
    "customer_id", "invoice_number", "reference_number", "date", "due_date",
    "billing_address_id", "shipping_address_id", "notes", "terms", "line_items",
}
REQUIRED_PUT_KEYS = {"customer_id", "invoice_number", "date", "due_date", "line_items"}

RISK_NOTE = (
    "ONE atomic PUT against one existing invoice. It is attempted exactly once: "
    "any failure, timeout or indeterminate result permanently locks this plan "
    "against retry and requires a fresh live reconciliation before anything new "
    "is staged. This tool never mails an invoice, never changes its number or "
    "status, and never adds or removes a line."
)

# ---------------------------------------------------------------------------
# The exact commissioned draft-creation surface
# ---------------------------------------------------------------------------

CREATE_INPUT_FIELDS = {"customer_id", "customer_name", "fields", "lines"}
CREATE_HEADER_FIELDS = (
    "date",
    "due_date",
    "reference_number",
    "notes",
    "terms",
    "billing_address_id",
    "shipping_address_id",
)
# Both dates are always stated explicitly, so nothing is left for Zoho to infer
# and the read-back can be exact.
REQUIRED_CREATE_HEADER_FIELDS = ("date", "due_date")
CREATE_LINE_FIELDS = {
    "item_id", "item_name", "quantity", "rate", "discount", "description", "tax_id",
}
REQUIRED_CREATE_LINE_FIELDS = {"item_id", "item_name", "quantity", "rate"}
CREATE_VALUE_FIELDS = ("quantity", "rate", "discount", "description", "tax_id")
TAX_CREATE_FIELDS = {"value", "source", "tax_percentage"}

# The complete POST body allowlist. invoice_number is ABSENT on purpose: Zoho's
# own auto-numbering assigns it. So are status, currency, exchange rate,
# shipping, adjustment, custom fields and every mail or lifecycle parameter.
ALLOWED_POST_KEYS = {
    "customer_id", "date", "due_date", "reference_number", "notes", "terms",
    "billing_address_id", "shipping_address_id", "line_items",
}
REQUIRED_POST_KEYS = {"customer_id", "date", "due_date", "line_items"}
LINE_POST_KEYS = ("item_id", "quantity", "rate", "discount", "description", "tax_id")
REQUIRED_LINE_POST_KEYS = {"item_id", "quantity", "rate"}

CREATE_RISK_FIELDS = {"atomic", "single_post", "email_sent", "note"}
CREATE_EVIDENCE_FIELDS = {
    "customer", "addresses", "items", "taxes", "settings", "header", "lines",
    "totals", "post_endpoint", "post_payload", "email_sent",
}
CREATE_CUSTOMER_FIELDS = {
    "customer_id", "customer_name", "company_name", "currency_code", "currency_id",
    "status", "contact_type",
}
CREATE_ITEM_FIELDS = {
    "item_id", "name", "sku", "status", "unit", "product_type", "item_type",
    "live_rate", "description",
}
CREATE_TAX_FIELDS = {"tax_id", "tax_name", "tax_percentage", "kind", "components"}
CREATE_SETTINGS_FIELDS = {
    "price_precision", "price_precision_source", "organization_currency_code",
    "invoice_numbering",
}
AUTO_NUMBERING_NOTE = (
    "Zoho Books assigns the invoice number from its own series. This tool never "
    "supplies, requests or overrides an invoice number."
)

CREATE_RISK_NOTE = (
    "ONE atomic POST that creates ONE new invoice in Draft status. It is "
    "attempted exactly once: any failure, timeout or indeterminate result "
    "permanently locks this plan against retry, and no cleanup, deletion, "
    "status change or second attempt is ever made -- reconcile in Zoho by hand. "
    "This tool never mails an invoice and has no mail transport at all; the new "
    "invoice is verified live to be exactly Draft and never transmitted."
)


class InvoiceRevisionError(RuntimeError):
    """A fail-closed validation, precondition, transport, or read-back error."""


# ---------------------------------------------------------------------------
# Small shared primitives
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise InvoiceRevisionError("Zoho returned evidence that is not JSON serializable.") from exc


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvoiceRevisionError(f"{label} JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise InvoiceRevisionError(f"{label} JSON must contain exactly one object.")
    return value


def closed_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) == expected:
        return
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    details: list[str] = []
    if missing:
        details.append("missing: " + ", ".join(missing))
    if extra:
        details.append("unsupported: " + ", ".join(extra))
    raise InvoiceRevisionError(f"{label} must use the exact closed schema ({'; '.join(details)}).")


def clean_text(value: Any, label: str, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise InvoiceRevisionError(f"{label} must be text.")
    if value != value.strip():
        raise InvoiceRevisionError(f"{label} must not have surrounding whitespace.")
    if not value:
        raise InvoiceRevisionError(f"{label} cannot be blank.")
    if len(value) > maximum:
        raise InvoiceRevisionError(f"{label} exceeds the {maximum}-character safety limit.")
    if any(ord(character) < 32 for character in value):
        raise InvoiceRevisionError(f"{label} contains control characters.")
    return value


def body_text(value: Any, label: str, maximum: int) -> str:
    """Free text that MAY legitimately be cleared to empty (notes, terms, PO)."""
    if not isinstance(value, str):
        raise InvoiceRevisionError(f"{label} must be text.")
    if value != value.strip():
        raise InvoiceRevisionError(f"{label} must not have surrounding whitespace.")
    if len(value) > maximum:
        raise InvoiceRevisionError(f"{label} exceeds the {maximum}-character safety limit.")
    if any(ord(character) < 32 and character != "\n" for character in value):
        raise InvoiceRevisionError(f"{label} contains control characters.")
    return value


def nonblank_body(value: Any, label: str, maximum: int) -> str:
    """Free text on a NEW invoice: multi-line is fine, blank is not.

    A new invoice simply omits a field it does not want, so an empty string is
    always a mistake rather than a deliberate clearing.
    """
    text = body_text(value, label, maximum)
    if not text:
        raise InvoiceRevisionError(
            f"{label} cannot be blank on a new invoice; omit the field instead."
        )
    return text


def positive_id(value: Any, label: str) -> str:
    if isinstance(value, bool):
        raise InvoiceRevisionError(f"{label} must be a positive Zoho ID.")
    text = str(value if value is not None else "").strip()
    if not ID_RE.fullmatch(text):
        raise InvoiceRevisionError(f"{label} must be a positive Zoho ID.")
    return text


def money_text(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def decimal_text(value: Any, label: str, pattern: re.Pattern[str]) -> Decimal:
    """Money and quantities are carried as canonical decimal TEXT.

    Floats never enter the arithmetic; they are produced only at the very last
    step, for the JSON body, and only after an exact round-trip check.
    """
    if isinstance(value, bool) or not isinstance(value, str):
        raise InvoiceRevisionError(
            f"{label} must be canonical decimal text, not a number or other type."
        )
    if not pattern.fullmatch(value):
        raise InvoiceRevisionError(f"{label} is not canonical decimal text: {value!r}")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise InvoiceRevisionError(f"{label} is not a valid decimal.") from exc
    return result


def live_number(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvoiceRevisionError(f"{label} is missing or not a number.")
    text = str(value).strip()
    if not text:
        raise InvoiceRevisionError(f"{label} is blank.")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise InvoiceRevisionError(f"{label} is not a valid number: {value!r}") from exc
    if not result.is_finite():
        raise InvoiceRevisionError(f"{label} must be a finite number.")
    return result


def exact_float(value: Decimal, label: str) -> float:
    result = float(value)
    if Decimal(str(result)) != value:
        raise InvoiceRevisionError(f"{label} is not exactly representable for the Zoho payload.")
    return result


def parse_date(value: str, label: str) -> date_type:
    if not DATE_RE.fullmatch(value):
        raise InvoiceRevisionError(f"{label} must be an exact YYYY-MM-DD date (got {value!r}).")
    try:
        return date_type.fromisoformat(value)
    except ValueError as exc:
        raise InvoiceRevisionError(f"{label} is not a real calendar date: {value!r}") from exc


def parse_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise InvoiceRevisionError(f"Plan {label} is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvoiceRevisionError(f"Plan {label} must include a timezone.")
    return parsed


def require_rachad_approval(approval: Any) -> None:
    """Exact, unpadded, uppercase APPROVED. No strip(), no case folding."""
    if not isinstance(approval, str) or approval != APPROVAL_WORD:
        raise InvoiceRevisionError(
            "Rachad must answer this exact staged plan with the one-word approval: "
            f"{APPROVAL_WORD} (exact uppercase, no extra words or spaces). Building "
            "or staging is not approval, and Dado cannot supply it."
        )


def origin_record() -> dict[str, str]:
    return {
        "tool_path": str(Path(__file__).resolve()),
        "repo_root": str(ROOT),
        "plan_dir": str(PLAN_DIR),
    }


def require_origin(origin: Any) -> None:
    if not isinstance(origin, dict):
        raise InvoiceRevisionError("Plan origin is invalid.")
    closed_fields(origin, ORIGIN_FIELDS, "Plan origin")
    if origin != origin_record():
        raise InvoiceRevisionError(
            "REFUSED: the plan was staged by a different tool file, repository root, "
            "or plan folder than the one running now."
        )


def contained_plan(raw_path: Any) -> Path:
    candidate = Path(str(raw_path if raw_path is not None else ""))
    if not candidate.is_absolute():
        raise InvoiceRevisionError(
            "Plan must be an absolute path inside the exact invoice-revision plan folder."
        )
    try:
        lexical_root = PLAN_DIR.absolute()
        candidate.absolute().relative_to(lexical_root)
    except (OSError, ValueError) as exc:
        raise InvoiceRevisionError("Plan is outside the exact allowlisted plan folder.") from exc
    cursor = candidate.absolute()
    while True:
        if cursor.is_symlink():
            raise InvoiceRevisionError("Plan paths and parents must not be symlinks.")
        if cursor == lexical_root:
            break
        parent = cursor.parent
        if parent == cursor:
            raise InvoiceRevisionError("Plan is outside the exact allowlisted plan folder.")
        cursor = parent
    try:
        root = PLAN_DIR.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise InvoiceRevisionError("Plan does not resolve inside the exact plan folder.") from exc
    if (
        root not in resolved.parents
        or not resolved.is_file()
        or resolved.suffix.casefold() != ".json"
    ):
        raise InvoiceRevisionError("Plan is outside the exact allowlisted plan folder.")
    return resolved


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


def validate_change_envelope(
    raw: Any, label: str, *, tax: bool = False, create: bool = False
) -> dict[str, str]:
    """Every stated value carries its own explicit source string. No exceptions."""
    if not isinstance(raw, dict):
        raise InvoiceRevisionError(f"{label} must be an object with a value and its source.")
    if tax:
        expected = TAX_CREATE_FIELDS if create else TAX_CHANGE_FIELDS
    else:
        expected = CHANGE_FIELDS
    closed_fields(raw, expected, label)
    source = clean_text(raw["source"], f"{label}.source", 600)
    result = {"value": raw["value"], "source": source}
    if tax and create:
        result["tax_percentage"] = raw["tax_percentage"]
    elif tax:
        result["old_tax_percentage"] = raw["old_tax_percentage"]
        result["new_tax_percentage"] = raw["new_tax_percentage"]
    return result


def validate_header_change(field: str, envelope: dict[str, Any]) -> dict[str, Any]:
    label = f"changes.{field}"
    value = envelope["value"]
    if field in ("customer_id", "billing_address_id", "shipping_address_id"):
        clean = positive_id(value, f"{label}.value")
        if value != clean:
            raise InvoiceRevisionError(f"{label}.value must be canonical positive-ID text.")
    elif field in ("date", "due_date"):
        clean = clean_text(value, f"{label}.value", 10)
        parse_date(clean, f"{label}.value")
    elif field == "reference_number":
        clean = body_text(value, f"{label}.value", 100)
        if "\n" in clean:
            raise InvoiceRevisionError(f"{label}.value must be a single line.")
    elif field == "notes":
        clean = body_text(value, f"{label}.value", 2000)
    elif field == "terms":
        clean = body_text(value, f"{label}.value", 2000)
    else:  # pragma: no cover - guarded by the caller's allowlist
        raise InvoiceRevisionError(f"REFUSED: {field} is not a commissioned invoice field.")
    return {"value": clean, "source": envelope["source"]}


def validate_line_change(field: str, envelope: dict[str, Any], label: str) -> dict[str, Any]:
    value = envelope["value"]
    if field == "quantity":
        quantity = decimal_text(value, f"{label}.value", QUANTITY_RE)
        if quantity <= 0:
            raise InvoiceRevisionError(f"{label}.value must be greater than zero.")
        if quantity > MAX_QUANTITY:
            raise InvoiceRevisionError(f"{label}.value exceeds the {MAX_QUANTITY} safety ceiling.")
    elif field == "rate":
        rate = decimal_text(value, f"{label}.value", RATE_RE)
        if rate < 0:
            raise InvoiceRevisionError(f"{label}.value must not be negative.")
        if rate > MAX_MONEY:
            raise InvoiceRevisionError(f"{label}.value exceeds the {MAX_MONEY} safety ceiling.")
    elif field == "discount":
        if not isinstance(value, str):
            raise InvoiceRevisionError(
                f"{label}.value must be decimal text, either an amount or a percentage ending in %."
            )
        if value.endswith("%"):
            if not DISCOUNT_PERCENT_RE.fullmatch(value):
                raise InvoiceRevisionError(
                    f"{label}.value is not a canonical 0-100 percentage: {value!r}"
                )
        else:
            amount = decimal_text(value, f"{label}.value", DISCOUNT_AMOUNT_RE)
            if amount > MAX_MONEY:
                raise InvoiceRevisionError(f"{label}.value exceeds the {MAX_MONEY} safety ceiling.")
    elif field == "description":
        value = body_text(value, f"{label}.value", 2000)
    elif field == "tax_id":
        value = positive_id(value, f"{label}.value")
        if envelope["value"] != value:
            raise InvoiceRevisionError(f"{label}.value must be canonical positive-ID text.")
        for key in ("old_tax_percentage", "new_tax_percentage"):
            percentage = envelope[key]
            if not isinstance(percentage, str) or not PERCENT_RE.fullmatch(percentage):
                raise InvoiceRevisionError(
                    f"{label}.{key} must be canonical percentage text such as \"13\" or \"5.00\". "
                    "A tax change must state the exact old and new rates."
                )
    else:  # pragma: no cover - guarded by the caller's allowlist
        raise InvoiceRevisionError(f"REFUSED: {field} is not a commissioned line field.")
    result = {"value": value, "source": envelope["source"]}
    if field == "tax_id":
        result["old_tax_percentage"] = envelope["old_tax_percentage"]
        result["new_tax_percentage"] = envelope["new_tax_percentage"]
    return result


def validate_line_changes(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise InvoiceRevisionError("line_changes must be a list (use [] for header-only changes).")
    if len(raw) > MAX_LINES:
        raise InvoiceRevisionError(f"line_changes exceeds the {MAX_LINES}-line safety ceiling.")
    result: list[dict[str, Any]] = []
    for index, row in enumerate(raw):
        label = f"line_changes[{index}]"
        if not isinstance(row, dict):
            raise InvoiceRevisionError(f"{label} must be an object.")
        closed_fields(row, LINE_CHANGE_ENVELOPE_FIELDS, label)
        line_item_id = positive_id(row["line_item_id"], f"{label}.line_item_id")
        item_id = positive_id(row["item_id"], f"{label}.item_id")
        if row["line_item_id"] != line_item_id or row["item_id"] != item_id:
            raise InvoiceRevisionError(f"{label} IDs must be canonical positive-ID text.")
        changes = row["changes"]
        if not isinstance(changes, dict) or not changes:
            raise InvoiceRevisionError(f"{label}.changes must name at least one changed field.")
        unsupported = sorted(set(changes) - set(LINE_CHANGE_FIELDS))
        if unsupported:
            raise InvoiceRevisionError(
                f"REFUSED: {label}.changes names uncommissioned line field(s): "
                + ", ".join(unsupported)
                + ". Only " + ", ".join(LINE_CHANGE_FIELDS) + " may change, and no line may be "
                "added, removed or substituted."
            )
        clean: dict[str, Any] = {}
        for field in LINE_CHANGE_FIELDS:
            if field not in changes:
                continue
            envelope = validate_change_envelope(
                changes[field], f"{label}.changes.{field}", tax=(field == "tax_id")
            )
            clean[field] = validate_line_change(field, envelope, f"{label}.changes.{field}")
        result.append({"line_item_id": line_item_id, "item_id": item_id, "changes": clean})
    ids = [row["line_item_id"] for row in result]
    if len(set(ids)) != len(ids):
        raise InvoiceRevisionError("REFUSED: the same line_item_id appears twice in line_changes.")
    return result


def validate_input(raw: dict[str, Any]) -> dict[str, Any]:
    closed_fields(raw, INPUT_FIELDS, "Stage input")
    invoice_id = positive_id(raw["invoice_id"], "invoice_id")
    if raw["invoice_id"] != invoice_id:
        raise InvoiceRevisionError("invoice_id must be canonical positive-ID text.")
    invoice_number = clean_text(raw["invoice_number"], "invoice_number", 100)
    changes = raw["changes"]
    if not isinstance(changes, dict):
        raise InvoiceRevisionError("changes must be an object (use {} for line-only changes).")
    unsupported = sorted(set(changes) - set(HEADER_CHANGE_FIELDS))
    if unsupported:
        raise InvoiceRevisionError(
            "REFUSED: changes names uncommissioned invoice field(s): "
            + ", ".join(unsupported)
            + ". Only " + ", ".join(HEADER_CHANGE_FIELDS) + " may change. The invoice number, "
            "status, currency, exchange rate, balance, payments, adjustments, shipping charges "
            "and custom fields are unreachable."
        )
    clean_changes: dict[str, Any] = {}
    for field in HEADER_CHANGE_FIELDS:
        if field not in changes:
            continue
        envelope = validate_change_envelope(changes[field], f"changes.{field}")
        clean_changes[field] = validate_header_change(field, envelope)
    line_changes = validate_line_changes(raw["line_changes"])
    if not clean_changes and not line_changes:
        raise InvoiceRevisionError("REFUSED: the request changes nothing; nothing was staged.")
    if "customer_id" in clean_changes and not (
        "billing_address_id" in clean_changes and "shipping_address_id" in clean_changes
    ):
        raise InvoiceRevisionError(
            "REFUSED: changing customer_id requires both billing_address_id and "
            "shipping_address_id in the same request, because the existing addresses belong "
            "to the previous customer."
        )
    if "date" in clean_changes and "due_date" not in clean_changes:
        raise InvoiceRevisionError(
            "REFUSED: changing date requires due_date in the same request, so the due date is "
            "stated explicitly instead of being recalculated by Zoho."
        )
    return {
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "changes": clean_changes,
        "line_changes": line_changes,
    }


# ---------------------------------------------------------------------------
# Draft-creation input schema
# ---------------------------------------------------------------------------


def validate_create_header(field: str, envelope: dict[str, Any]) -> dict[str, Any]:
    label = f"fields.{field}"
    value = envelope["value"]
    if field in ("billing_address_id", "shipping_address_id"):
        clean = positive_id(value, f"{label}.value")
        if value != clean:
            raise InvoiceRevisionError(f"{label}.value must be canonical positive-ID text.")
    elif field in ("date", "due_date"):
        clean = clean_text(value, f"{label}.value", 10)
        parse_date(clean, f"{label}.value")
    elif field == "reference_number":
        clean = clean_text(value, f"{label}.value", 100)
        if "\n" in clean:
            raise InvoiceRevisionError(f"{label}.value must be a single line.")
    elif field in ("notes", "terms"):
        clean = nonblank_body(value, f"{label}.value", 2000)
    else:  # pragma: no cover - guarded by the caller's allowlist
        raise InvoiceRevisionError(f"REFUSED: {field} is not a commissioned invoice field.")
    return {"value": clean, "source": envelope["source"]}


def validate_create_line_value(field: str, envelope: dict[str, Any], label: str) -> dict[str, Any]:
    value = envelope["value"]
    if field == "quantity":
        quantity = decimal_text(value, f"{label}.value", QUANTITY_RE)
        if quantity <= 0:
            raise InvoiceRevisionError(f"{label}.value must be greater than zero.")
        if quantity > MAX_QUANTITY:
            raise InvoiceRevisionError(f"{label}.value exceeds the {MAX_QUANTITY} safety ceiling.")
    elif field == "rate":
        rate = decimal_text(value, f"{label}.value", RATE_RE)
        if rate < 0:
            raise InvoiceRevisionError(f"{label}.value must not be negative.")
        if rate > MAX_MONEY:
            raise InvoiceRevisionError(f"{label}.value exceeds the {MAX_MONEY} safety ceiling.")
    elif field == "discount":
        if not isinstance(value, str):
            raise InvoiceRevisionError(
                f"{label}.value must be decimal text, either an amount or a percentage ending in %."
            )
        if value.endswith("%"):
            if not DISCOUNT_PERCENT_RE.fullmatch(value):
                raise InvoiceRevisionError(
                    f"{label}.value is not a canonical 0-100 percentage: {value!r}"
                )
        else:
            amount = decimal_text(value, f"{label}.value", DISCOUNT_AMOUNT_RE)
            if amount > MAX_MONEY:
                raise InvoiceRevisionError(f"{label}.value exceeds the {MAX_MONEY} safety ceiling.")
    elif field == "description":
        value = nonblank_body(value, f"{label}.value", 2000)
    elif field == "tax_id":
        value = positive_id(value, f"{label}.value")
        if envelope["value"] != value:
            raise InvoiceRevisionError(f"{label}.value must be canonical positive-ID text.")
        percentage = envelope["tax_percentage"]
        if not isinstance(percentage, str) or not PERCENT_RE.fullmatch(percentage):
            raise InvoiceRevisionError(
                f"{label}.tax_percentage must be canonical percentage text such as \"5\" or "
                "\"14.975\". A taxed line must state the exact live rate it expects."
            )
    else:  # pragma: no cover - guarded by the caller's allowlist
        raise InvoiceRevisionError(f"REFUSED: {field} is not a commissioned line field.")
    result = {"value": value, "source": envelope["source"]}
    if field == "tax_id":
        result["tax_percentage"] = envelope["tax_percentage"]
    return result


def validate_create_lines(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise InvoiceRevisionError(
            "REFUSED: a new invoice needs at least one line; nothing was staged."
        )
    if len(raw) > MAX_LINES:
        raise InvoiceRevisionError(f"lines exceeds the {MAX_LINES}-line safety ceiling.")
    result: list[dict[str, Any]] = []
    for index, row in enumerate(raw):
        label = f"lines[{index}]"
        if not isinstance(row, dict):
            raise InvoiceRevisionError(f"{label} must be an object.")
        unsupported = sorted(set(row) - CREATE_LINE_FIELDS)
        if unsupported:
            raise InvoiceRevisionError(
                f"REFUSED: {label} names uncommissioned line field(s): "
                + ", ".join(unsupported)
                + ". Only " + ", ".join(sorted(CREATE_LINE_FIELDS)) + " may be stated, and a line "
                "must name an EXISTING Zoho item -- free-text and unlinked lines are refused."
            )
        missing = sorted(REQUIRED_CREATE_LINE_FIELDS - set(row))
        if missing:
            raise InvoiceRevisionError(
                f"REFUSED: {label} is missing required field(s): " + ", ".join(missing)
            )
        item_id = positive_id(row["item_id"], f"{label}.item_id")
        if row["item_id"] != item_id:
            raise InvoiceRevisionError(f"{label}.item_id must be canonical positive-ID text.")
        item_name = clean_text(row["item_name"], f"{label}.item_name", 300)
        clean: dict[str, Any] = {"item_id": item_id, "item_name": item_name}
        for field in CREATE_VALUE_FIELDS:
            if field not in row:
                continue
            envelope = validate_change_envelope(
                row[field], f"{label}.{field}", tax=(field == "tax_id"), create=True
            )
            clean[field] = validate_create_line_value(field, envelope, f"{label}.{field}")
        result.append(clean)
    _require_distinguishable_lines(result)
    return result


def _require_distinguishable_lines(lines: list[dict[str, Any]]) -> None:
    """Two lines for the SAME item must be told apart explicitly.

    Zoho happily accepts two identical lines, and the read-back could then match
    the wrong one. A repeat of an item is therefore allowed only when every line
    carrying that item states its own distinct, non-blank description.
    """
    by_item: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        by_item.setdefault(line["item_id"], []).append(index)
    for item_id, indexes in sorted(by_item.items()):
        if len(indexes) < 2:
            continue
        descriptions = []
        for index in indexes:
            change = lines[index].get("description")
            if change is None:
                raise InvoiceRevisionError(
                    f"REFUSED: item {item_id} appears on {len(indexes)} lines "
                    f"({', '.join('lines[%d]' % i for i in indexes)}) without a distinct "
                    "description on each. Repeat an item only when every one of its lines states "
                    "its own description, so the lines can be told apart on read-back."
                )
            descriptions.append(change["value"])
        if len(set(descriptions)) != len(descriptions):
            raise InvoiceRevisionError(
                f"REFUSED: item {item_id} appears on {len(indexes)} lines with a repeated "
                "description. Each repeated line needs its own distinct description."
            )


def validate_create_input(raw: dict[str, Any]) -> dict[str, Any]:
    closed_fields(raw, CREATE_INPUT_FIELDS, "Draft-invoice stage input")
    customer_id = positive_id(raw["customer_id"], "customer_id")
    if raw["customer_id"] != customer_id:
        raise InvoiceRevisionError("customer_id must be canonical positive-ID text.")
    customer_name = clean_text(raw["customer_name"], "customer_name", 300)
    fields = raw["fields"]
    if not isinstance(fields, dict):
        raise InvoiceRevisionError("fields must be an object.")
    unsupported = sorted(set(fields) - set(CREATE_HEADER_FIELDS))
    if unsupported:
        raise InvoiceRevisionError(
            "REFUSED: fields names uncommissioned invoice field(s): "
            + ", ".join(unsupported)
            + ". Only " + ", ".join(CREATE_HEADER_FIELDS) + " may be stated. The invoice number, "
            "status, currency, exchange rate, balance, payments, adjustments, shipping charges "
            "and custom fields are unreachable, and the number is assigned by Zoho."
        )
    missing = [field for field in REQUIRED_CREATE_HEADER_FIELDS if field not in fields]
    if missing:
        raise InvoiceRevisionError(
            "REFUSED: fields must state " + " and ".join(missing) + " explicitly, so nothing is "
            "left for Zoho to infer and the read-back can be exact."
        )
    clean_fields: dict[str, Any] = {}
    for field in CREATE_HEADER_FIELDS:
        if field not in fields:
            continue
        envelope = validate_change_envelope(fields[field], f"fields.{field}")
        clean_fields[field] = validate_create_header(field, envelope)
    invoice_date = parse_date(clean_fields["date"]["value"], "fields.date.value")
    due_date = parse_date(clean_fields["due_date"]["value"], "fields.due_date.value")
    if due_date < invoice_date:
        raise InvoiceRevisionError(
            f"REFUSED: due_date {clean_fields['due_date']['value']} is before the invoice date "
            f"{clean_fields['date']['value']}."
        )
    return {
        "customer_id": customer_id,
        "customer_name": customer_name,
        "fields": clean_fields,
        "lines": validate_create_lines(raw["lines"]),
    }


# ---------------------------------------------------------------------------
# Live read-only Zoho reads
# ---------------------------------------------------------------------------


def books_organization_id(vault: dict[str, Any]) -> str:
    return positive_id(vault.get("books_organization_id"), "books_organization_id")


def organization_record(
    access_token: str, vault: dict[str, Any]
) -> tuple[dict[str, str], dict[str, Any]]:
    """The verified FRP Depot organization, plus its complete live record."""
    org_id = books_organization_id(vault)
    result = zoho_tool.api_get(access_token, str(vault["api_domain"]), "/books/v3/organizations")
    organizations = result.get("organizations")
    if not isinstance(organizations, list) or not organizations:
        raise InvoiceRevisionError("Zoho returned no Books organizations.")
    frp = zoho_tool.frp_organization(organizations)
    if positive_id(frp.get("organization_id"), "live organization_id") != org_id:
        raise InvoiceRevisionError(
            "REFUSED: the live FRP Depot Books organization does not match the vault."
        )
    return (
        {
            "organization_id": org_id,
            "name": clean_text(
                frp.get("name") or frp.get("organization_name"), "organization name", 200
            ),
            "currency_code": clean_text(frp.get("currency_code"), "organization currency_code", 8),
        },
        json_copy(frp),
    )


def verified_organization(access_token: str, vault: dict[str, Any]) -> dict[str, str]:
    return organization_record(access_token, vault)[0]


def _org_query(vault: dict[str, Any]) -> str:
    return urlencode({"organization_id": books_organization_id(vault)})


def get_invoice(access_token: str, vault: dict[str, Any], invoice_id: str) -> dict[str, Any]:
    invoice_id = positive_id(invoice_id, "invoice_id")
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/invoices/{invoice_id}?{_org_query(vault)}",
    )
    invoice = result.get("invoice")
    if not isinstance(invoice, dict) or str(invoice.get("invoice_id") or "") != invoice_id:
        raise InvoiceRevisionError(f"Zoho invoice {invoice_id} was not found.")
    return json_copy(invoice)


def get_customer(access_token: str, vault: dict[str, Any], customer_id: str) -> dict[str, Any]:
    customer_id = positive_id(customer_id, "customer_id")
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/contacts/{customer_id}?{_org_query(vault)}",
    )
    contact = result.get("contact")
    if not isinstance(contact, dict) or str(contact.get("contact_id") or "") != customer_id:
        raise InvoiceRevisionError(
            f"REFUSED: Zoho customer {customer_id} was not found in this organization. This tool "
            "can only point an invoice at a customer that already exists; it never creates one."
        )
    return json_copy(contact)


def get_customer_addresses(
    access_token: str, vault: dict[str, Any], customer_id: str
) -> list[dict[str, Any]]:
    customer_id = positive_id(customer_id, "customer_id")
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/contacts/{customer_id}/address?{_org_query(vault)}",
    )
    addresses = result.get("addresses")
    if not isinstance(addresses, list):
        raise InvoiceRevisionError(f"Zoho returned no address list for customer {customer_id}.")
    return json_copy(addresses)


def get_taxes(access_token: str, vault: dict[str, Any]) -> list[dict[str, Any]]:
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/settings/taxes?{_org_query(vault)}",
    )
    taxes = result.get("taxes")
    if not isinstance(taxes, list):
        raise InvoiceRevisionError("Zoho returned no tax list for this organization.")
    return json_copy(taxes)


def get_tax_groups(access_token: str, vault: dict[str, Any]) -> list[dict[str, Any]]:
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/settings/taxgroups?{_org_query(vault)}",
    )
    groups = result.get("tax_groups")
    if not isinstance(groups, list):
        raise InvoiceRevisionError("Zoho returned no tax-group list for this organization.")
    return json_copy(groups)


def get_item(access_token: str, vault: dict[str, Any], item_id: str) -> dict[str, Any]:
    item_id = positive_id(item_id, "item_id")
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/items/{item_id}?{_org_query(vault)}",
    )
    item = result.get("item")
    if not isinstance(item, dict) or str(item.get("item_id") or "") != item_id:
        raise InvoiceRevisionError(
            f"REFUSED: Zoho item {item_id} was not found in this organization. Every line must "
            "name an item that already exists; this tool never creates one."
        )
    return json_copy(item)


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------


def _zero(value: Any, label: str) -> Decimal:
    amount = live_number(value if value not in (None, "") else 0, label)
    return amount


def dependency_state(invoice: dict[str, Any]) -> dict[str, Any]:
    """Everything that makes revising an existing invoice unsafe. Fail closed."""
    status = clean_text(invoice.get("status"), "invoice status", 32)
    if status not in ALLOWED_STATUSES:
        raise InvoiceRevisionError(
            f"REFUSED: invoice status is {status!r}. This tool revises only an invoice that is "
            f"exactly {' or '.join(ALLOWED_STATUSES)}, and it preserves that status unchanged. "
            "It cannot void an invoice, mark it draft, or mark it sent."
        )
    total = live_number(invoice.get("total"), "invoice total")
    balance = live_number(invoice.get("balance"), "invoice balance")
    payment_made = _zero(invoice.get("payment_made"), "invoice payment_made")
    credits_applied = _zero(invoice.get("credits_applied"), "invoice credits_applied")
    write_off = _zero(invoice.get("write_off_amount"), "invoice write_off_amount")
    if payment_made != 0:
        raise InvoiceRevisionError(
            f"REFUSED: invoice has {payment_made} recorded against it in payments. A paid or "
            "partially paid invoice is not revised by this tool."
        )
    if credits_applied != 0:
        raise InvoiceRevisionError(
            f"REFUSED: invoice has {credits_applied} in applied credits. Revision is unsafe."
        )
    if write_off != 0:
        raise InvoiceRevisionError(
            f"REFUSED: invoice carries a write-off of {write_off}. Revision is unsafe."
        )
    if balance != total:
        raise InvoiceRevisionError(
            f"REFUSED: invoice balance {balance} does not equal its total {total}, so something "
            "is already applied against it. Revision is unsafe."
        )
    dependent_lists = {}
    for key in ("payments", "credits", "creditnotes", "applied_credits", "packages", "shipments"):
        value = invoice.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            raise InvoiceRevisionError(f"REFUSED: invoice field {key} has an unexpected shape.")
        if value:
            raise InvoiceRevisionError(
                f"REFUSED: invoice has {len(value)} linked {key} record(s). A dependent invoice "
                "is not revised by this tool."
            )
        dependent_lists[key] = 0
    recurring = str(invoice.get("recurring_invoice_id") or "").strip()
    if recurring:
        raise InvoiceRevisionError(
            "REFUSED: this invoice belongs to a recurring profile and is not revised by this tool."
        )
    shipped = str(invoice.get("shipping_status") or invoice.get("shipped_status") or "").strip()
    if shipped and shipped.casefold() not in ("", "not_shipped", "pending"):
        raise InvoiceRevisionError(
            f"REFUSED: invoice shipping status is {shipped!r}; packages or shipments exist. "
            "Revision is unsafe."
        )
    return {
        "status": status,
        "total": money_text(total),
        "balance": money_text(balance),
        "payment_made": money_text(payment_made),
        "credits_applied": money_text(credits_applied),
        "write_off_amount": money_text(write_off),
        "empty_dependent_lists": dict(sorted(dependent_lists.items())),
        "recurring_invoice_id": "",
        "shipping_status": shipped,
    }


def live_lines(invoice: dict[str, Any]) -> list[dict[str, Any]]:
    lines = invoice.get("line_items")
    if not isinstance(lines, list) or not lines:
        raise InvoiceRevisionError("REFUSED: the live invoice has no readable line items.")
    if len(lines) > MAX_LINES:
        raise InvoiceRevisionError(f"REFUSED: the invoice exceeds the {MAX_LINES}-line ceiling.")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise InvoiceRevisionError(f"Live line {index} is not an object.")
        line_item_id = positive_id(line.get("line_item_id"), f"live line {index} line_item_id")
        if line_item_id in seen:
            raise InvoiceRevisionError(
                f"REFUSED: the live invoice repeats line_item_id {line_item_id}."
            )
        seen.add(line_item_id)
        result.append(json_copy(line))
    return result


# ---------------------------------------------------------------------------
# Protected state
# ---------------------------------------------------------------------------


def unprotected_keys(payload: dict[str, Any]) -> list[str]:
    """Exactly which invoice keys leave the byte-exact fingerprint, and why.

    Nothing is excluded speculatively: a key is exempt only when this plan
    genuinely changes something that moves it, and every exempt key is then
    verified by an explicit rule. If the plan changes only a reference number,
    the customer, the addresses, the dates, the notes, the terms, every line
    and every total stay inside the fingerprint and must not move at all.
    """
    changes = payload["changes"]
    keys = set(VOLATILE_FIELDS)
    if "customer_id" in changes:
        keys.update(CUSTOMER_LINKED_FIELDS)
    for field in ("reference_number", "notes", "terms"):
        if field in changes:
            keys.add(field)
    if "billing_address_id" in changes:
        keys.update(("billing_address_id", "billing_address"))
    if "shipping_address_id" in changes:
        keys.update(("shipping_address_id", "shipping_address"))
    if "date" in changes or "due_date" in changes:
        keys.update(("date", "due_date"))
        keys.update(DUE_DERIVED_FIELDS)
    if payload["line_changes"]:
        keys.add("line_items")
        keys.update(DERIVED_TOTAL_FIELDS)
        keys.update(GROSS_SUBTOTAL_FIELDS)
        keys.update(BCY_TOTAL_FIELDS)
    return sorted(keys)


def protected_state(invoice: dict[str, Any], exempt: list[str]) -> dict[str, Any]:
    if not isinstance(invoice, dict):
        raise InvoiceRevisionError("Invoice state must be an object.")
    exempt_set = set(exempt)
    return {
        key: json_copy(value) for key, value in invoice.items() if key not in exempt_set
    }


def verify_protected_unchanged(invoice: dict[str, Any], evidence: dict[str, Any], exempt: list[str]) -> None:
    current = protected_state(invoice, exempt)
    if current != evidence["protected_state"] or not secrets.compare_digest(
        digest_for(current), str(evidence["protected_state_sha256"])
    ):
        expected = evidence["protected_state"]
        moved = sorted(
            key for key in set(current) | set(expected)
            if current.get(key, "\0missing") != expected.get(key, "\0missing")
        )
        raise InvoiceRevisionError(
            "Invoice changed outside the approved fields. Moved or unexpected key(s): "
            + (", ".join(moved) or "structure")
            + ". Stop and reconcile."
        )


# ---------------------------------------------------------------------------
# Deterministic revision builder
# ---------------------------------------------------------------------------


def customer_evidence(contact: dict[str, Any], invoice: dict[str, Any]) -> dict[str, Any]:
    customer_id = positive_id(contact.get("contact_id"), "customer contact_id")
    contact_type = str(contact.get("contact_type") or "").strip()
    if contact_type != "customer":
        raise InvoiceRevisionError(
            f"REFUSED: contact {customer_id} is a {contact_type or 'unknown'} record, not a customer."
        )
    status = str(contact.get("status") or "").strip()
    if status != "active":
        raise InvoiceRevisionError(f"REFUSED: customer {customer_id} is {status or 'unknown'}, not active.")
    currency = str(contact.get("currency_code") or "").strip()
    invoice_currency = str(invoice.get("currency_code") or "").strip()
    if currency and invoice_currency and currency != invoice_currency:
        raise InvoiceRevisionError(
            f"REFUSED: customer {customer_id} bills in {currency} but the invoice is "
            f"{invoice_currency}. This tool never changes an invoice's currency or exchange rate."
        )
    return {
        "customer_id": customer_id,
        "customer_name": clean_text(
            contact.get("contact_name") or contact.get("company_name"), "customer name", 300
        ),
        "company_name": str(contact.get("company_name") or ""),
        "currency_code": currency,
        "currency_id": str(contact.get("currency_id") or ""),
        "status": status,
        "contact_type": contact_type,
        "billing_address": json_copy(contact.get("billing_address") or {}),
        "shipping_address": json_copy(contact.get("shipping_address") or {}),
    }


def address_index(addresses: list[dict[str, Any]], contact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for entry in addresses:
        if not isinstance(entry, dict):
            continue
        address_id = str(entry.get("address_id") or "").strip()
        if ID_RE.fullmatch(address_id):
            index[address_id] = json_copy(entry)
    for key in ("billing_address", "shipping_address"):
        entry = contact.get(key)
        if isinstance(entry, dict):
            address_id = str(entry.get("address_id") or "").strip()
            if ID_RE.fullmatch(address_id) and address_id not in index:
                index[address_id] = json_copy(entry)
    return index


def owned_address(
    address_id: str, index: dict[str, dict[str, Any]], customer_id: str, label: str
) -> dict[str, Any]:
    if address_id not in index:
        raise InvoiceRevisionError(
            f"REFUSED: {label} {address_id} is not one of the addresses owned by live customer "
            f"{customer_id}. Known address IDs: {', '.join(sorted(index)) or 'none'}."
        )
    return index[address_id]


def tax_index(taxes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for entry in taxes:
        if not isinstance(entry, dict):
            continue
        tax_id = str(entry.get("tax_id") or "").strip()
        if ID_RE.fullmatch(tax_id):
            index[tax_id] = json_copy(entry)
    return index


def line_discount_kind(value: Any) -> str:
    if isinstance(value, str) and value.strip().endswith("%"):
        return "percent"
    return "amount"


def deterministic_line_total(
    quantity: Decimal, rate: Decimal, discount: Any, precision: int
) -> Decimal | None:
    gross = (quantity * rate).quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)
    if discount in (None, "", 0):
        return gross
    text = str(discount).strip()
    if text.endswith("%"):
        body = text[:-1].strip()
        try:
            percentage = Decimal(body)
        except InvalidOperation:
            return None
        reduced = gross * (Decimal(100) - percentage) / Decimal(100)
        return reduced.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return (gross - amount).quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)


def deterministic_line_gross(
    quantity: Decimal, rate: Decimal, precision: int
) -> Decimal | None:
    """quantity x rate before any discount, or None if rounding makes it unsafe.

    Zoho's gross subtotal is a sum of per-line grosses, but summing rounded lines
    and rounding a summed total can differ by a cent. When any line needs
    rounding at all, the prediction is withheld rather than guessed - the same
    rule build_totals already applies to the taxed and inclusive-tax cases.
    """
    exact = quantity * rate
    rounded = exact.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)
    return rounded if rounded == exact else None


def build_revision(
    invoice: dict[str, Any],
    payload: dict[str, Any],
    customer: dict[str, Any] | None,
    addresses: dict[str, dict[str, Any]],
    taxes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """The single deterministic projection of live state + request -> plan.

    Stage builds it from live reads; the plan validator rebuilds it from the
    plan's own stored live state and requires an identical result, so tampering
    with any recorded figure, payload key or fingerprint cannot survive review.
    """
    invoice_id = positive_id(invoice.get("invoice_id"), "live invoice_id")
    if invoice_id != payload["invoice_id"]:
        raise InvoiceRevisionError(
            f"Zoho returned invoice {invoice_id} for requested invoice {payload['invoice_id']}."
        )
    invoice_number = clean_text(invoice.get("invoice_number"), "live invoice_number", 100)
    if invoice_number != payload["invoice_number"]:
        raise InvoiceRevisionError(
            f"REFUSED: live invoice {invoice_id} is numbered {invoice_number!r}, not the stated "
            f"{payload['invoice_number']!r}. The invoice number is never changed and must be "
            "confirmed before anything is staged."
        )
    dependencies = dependency_state(invoice)
    changes = payload["changes"]
    lines = live_lines(invoice)
    exempt = unprotected_keys(payload)

    # -- header --------------------------------------------------------
    header_changes: dict[str, Any] = {}
    put: dict[str, Any] = {}
    live_customer_id = positive_id(invoice.get("customer_id"), "live customer_id")
    put["customer_id"] = live_customer_id
    put["invoice_number"] = invoice_number
    live_date = clean_text(invoice.get("date"), "live invoice date", 10)
    live_due_date = clean_text(invoice.get("due_date"), "live invoice due_date", 10)
    put["date"] = live_date
    put["due_date"] = live_due_date

    customer_evidence_row: dict[str, Any] | None = None
    if "customer_id" in changes:
        new_customer_id = changes["customer_id"]["value"]
        if new_customer_id == live_customer_id:
            raise InvoiceRevisionError(
                f"REFUSED: the invoice already bills customer {new_customer_id}; nothing was staged."
            )
        if customer is None:
            raise InvoiceRevisionError("The selected customer was not read live.")
        customer_evidence_row = customer_evidence(customer, invoice)
        if customer_evidence_row["customer_id"] != new_customer_id:
            raise InvoiceRevisionError("The customer read live is not the requested customer.")
        put["customer_id"] = new_customer_id
        header_changes["customer_id"] = {
            "old": live_customer_id,
            "new": new_customer_id,
            "source": changes["customer_id"]["source"],
            "new_customer_name": customer_evidence_row["customer_name"],
            "new_customer_currency": customer_evidence_row["currency_code"],
        }

    address_owner = (
        header_changes["customer_id"]["new"] if "customer_id" in header_changes else live_customer_id
    )
    address_evidence: dict[str, Any] = {}
    for field, live_key in (
        ("billing_address_id", "billing_address"),
        ("shipping_address_id", "shipping_address"),
    ):
        if field not in changes:
            continue
        address_id = changes[field]["value"]
        record = owned_address(address_id, addresses, address_owner, field)
        put[field] = address_id
        live_address = invoice.get(live_key)
        old_id = ""
        if isinstance(live_address, dict):
            old_id = str(live_address.get("address_id") or "")
        old_id = old_id or str(invoice.get(field) or "")
        header_changes[field] = {
            "old": old_id,
            "new": address_id,
            "source": changes[field]["source"],
            "owner_customer_id": address_owner,
        }
        address_evidence[field] = record

    for field, maximum in (("reference_number", 100), ("notes", 2000), ("terms", 2000)):
        if field not in changes:
            continue
        new_value = changes[field]["value"]
        old_value = invoice.get(field)
        old_text = "" if old_value is None else str(old_value)
        if new_value == old_text:
            raise InvoiceRevisionError(
                f"REFUSED: {field} is already {new_value!r}; nothing was staged."
            )
        _ = maximum
        put[field] = new_value
        header_changes[field] = {
            "old": old_text,
            "new": new_value,
            "source": changes[field]["source"],
        }

    for field in ("date", "due_date"):
        if field not in changes:
            continue
        new_value = changes[field]["value"]
        old_text = live_date if field == "date" else live_due_date
        put[field] = new_value
        header_changes[field] = {
            "old": old_text,
            "new": new_value,
            "source": changes[field]["source"],
        }
    final_date = parse_date(put["date"], "invoice date")
    final_due = parse_date(put["due_date"], "invoice due_date")
    if final_due < final_date:
        raise InvoiceRevisionError(
            f"REFUSED: due_date {put['due_date']} is before the invoice date {put['date']}."
        )
    if ("date" in changes or "due_date" in changes) and dependencies["status"] == "sent":
        if final_due < utc_now().date():
            raise InvoiceRevisionError(
                f"REFUSED: due_date {put['due_date']} is already past, which would move this sent "
                "invoice to overdue. This tool preserves the invoice status exactly."
            )

    # -- lines ---------------------------------------------------------
    requested = {row["line_item_id"]: row for row in payload["line_changes"]}
    live_ids = [str(line.get("line_item_id")) for line in lines]
    unknown = sorted(set(requested) - set(live_ids))
    if unknown:
        raise InvoiceRevisionError(
            "REFUSED: line_changes names line_item_id(s) that are not on this invoice: "
            + ", ".join(unknown)
            + ". A line cannot be added, and only existing lines can be revised."
        )
    salesorder_id = str(invoice.get("salesorder_id") or "").strip()
    if requested and salesorder_id:
        raise InvoiceRevisionError(
            f"REFUSED: this invoice is linked to sales order {salesorder_id}. Changing line "
            "quantities, rates, discounts or taxes would desync the linked order, so only header "
            "fields may be revised here."
        )

    precision = 2
    raw_precision = invoice.get("price_precision")
    if isinstance(raw_precision, int) and not isinstance(raw_precision, bool) and 0 <= raw_precision <= 6:
        precision = raw_precision
    is_inclusive_tax = bool(invoice.get("is_inclusive_tax"))
    discount_type = str(invoice.get("discount_type") or "").strip()

    put_lines: list[dict[str, Any]] = []
    line_evidence: list[dict[str, Any]] = []
    line_change_evidence: list[dict[str, Any]] = []
    sub_total_parts: list[Decimal | None] = []
    gross_parts: list[Decimal | None] = []
    for index, line in enumerate(lines):
        line_item_id = str(line["line_item_id"])
        item_id = str(line.get("item_id") or "")
        row = requested.get(line_item_id)
        if row is not None and row["item_id"] != item_id:
            raise InvoiceRevisionError(
                f"REFUSED: line {line_item_id} carries item {item_id or 'none'}, not the stated "
                f"{row['item_id']}. Item substitution is not reachable through this tool."
            )
        put_line: dict[str, Any] = {}
        for key in LINE_PUT_KEYS:
            if key in line:
                put_line[key] = json_copy(line[key])
        put_line["line_item_id"] = line_item_id
        if item_id:
            put_line["item_id"] = item_id
        applied: dict[str, Any] = {}
        if row is not None:
            for field, envelope in row["changes"].items():
                old_value = line.get(field)
                old_text = "" if old_value is None else str(old_value)
                if field == "quantity":
                    new_decimal = Decimal(envelope["value"])
                    if live_number(old_value, f"line {line_item_id} quantity") == new_decimal:
                        raise InvoiceRevisionError(
                            f"REFUSED: line {line_item_id} quantity is already {envelope['value']}."
                        )
                    put_line["quantity"] = exact_float(new_decimal, f"line {line_item_id} quantity")
                elif field == "rate":
                    new_decimal = Decimal(envelope["value"])
                    if live_number(old_value, f"line {line_item_id} rate") == new_decimal:
                        raise InvoiceRevisionError(
                            f"REFUSED: line {line_item_id} rate is already {envelope['value']}."
                        )
                    put_line["rate"] = exact_float(new_decimal, f"line {line_item_id} rate")
                elif field == "discount":
                    if envelope["value"].endswith("%"):
                        put_line["discount"] = envelope["value"]
                    else:
                        put_line["discount"] = exact_float(
                            Decimal(envelope["value"]), f"line {line_item_id} discount"
                        )
                    if discount_type and discount_type != "item_level":
                        raise InvoiceRevisionError(
                            f"REFUSED: this invoice applies discounts at {discount_type!r}, so a "
                            "line-level discount is not the field in force. Nothing was staged."
                        )
                elif field == "description":
                    put_line["description"] = envelope["value"]
                elif field == "tax_id":
                    new_tax_id = envelope["value"]
                    tax = taxes.get(new_tax_id)
                    if tax is None:
                        raise InvoiceRevisionError(
                            f"REFUSED: tax {new_tax_id} is not a live tax in this organization. A "
                            "tax change must name an exact existing tax ID."
                        )
                    live_percentage = live_number(
                        tax.get("tax_percentage"), f"tax {new_tax_id} percentage"
                    )
                    if live_percentage != Decimal(envelope["new_tax_percentage"]):
                        raise InvoiceRevisionError(
                            f"REFUSED: tax {new_tax_id} is live at {live_percentage}%, not the "
                            f"stated {envelope['new_tax_percentage']}%."
                        )
                    old_percentage = _zero(
                        line.get("tax_percentage"), f"line {line_item_id} tax_percentage"
                    )
                    if old_percentage != Decimal(envelope["old_tax_percentage"]):
                        raise InvoiceRevisionError(
                            f"REFUSED: line {line_item_id} is live at {old_percentage}% tax, not "
                            f"the stated {envelope['old_tax_percentage']}%."
                        )
                    if str(line.get("tax_id") or "") == new_tax_id:
                        raise InvoiceRevisionError(
                            f"REFUSED: line {line_item_id} already carries tax {new_tax_id}."
                        )
                    put_line["tax_id"] = new_tax_id
                applied[field] = {
                    "old": old_text,
                    "new": envelope["value"],
                    "source": envelope["source"],
                }
                if field == "tax_id":
                    applied[field]["old_tax_percentage"] = envelope["old_tax_percentage"]
                    applied[field]["new_tax_percentage"] = envelope["new_tax_percentage"]
            line_change_evidence.append(
                {"line_item_id": line_item_id, "item_id": item_id, "applied": applied}
            )
        put_lines.append(put_line)
        quantity = live_number(
            put_line.get("quantity", line.get("quantity")), f"line {line_item_id} quantity"
        )
        rate = live_number(put_line.get("rate", line.get("rate")), f"line {line_item_id} rate")
        discount = put_line.get("discount", line.get("discount"))
        computed = None if is_inclusive_tax else deterministic_line_total(
            quantity, rate, discount, precision
        )
        sub_total_parts.append(computed)
        gross_parts.append(
            None if is_inclusive_tax else deterministic_line_gross(quantity, rate, precision)
        )
        line_evidence.append({
            "index": index,
            "line_item_id": line_item_id,
            "item_id": item_id,
            "name": str(line.get("name") or ""),
            "changed": sorted(applied),
            "before": json_copy(line),
            "before_sha256": digest_for(json_copy(line)),
            "expected_item_total": None if computed is None else money_text(computed),
        })
    put["line_items"] = put_lines
    if not line_change_evidence and all(
        change["old"] == change["new"] for change in header_changes.values()
    ):
        raise InvoiceRevisionError(
            "REFUSED: every requested value already matches the live invoice; nothing was staged."
        )

    # -- totals --------------------------------------------------------
    totals = build_totals(
        invoice, payload, sub_total_parts, gross_parts, put_lines, lines, is_inclusive_tax
    )

    # -- assembled payload --------------------------------------------
    if set(put) - ALLOWED_PUT_KEYS:
        raise InvoiceRevisionError(
            "REFUSED: the assembled payload names a field outside the commissioned surface: "
            + ", ".join(sorted(set(put) - ALLOWED_PUT_KEYS))
        )
    if not REQUIRED_PUT_KEYS.issubset(put):
        raise InvoiceRevisionError("The assembled payload is missing a required preserved field.")

    state = json_copy(invoice)
    protected = protected_state(state, exempt)
    invoice_row = {
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "status": dependencies["status"],
        "currency_id": str(invoice.get("currency_id") or ""),
        "currency_code": str(invoice.get("currency_code") or ""),
        "exchange_rate": str(invoice.get("exchange_rate") if invoice.get("exchange_rate") is not None else ""),
        "customer_id": live_customer_id,
        "customer_name": str(invoice.get("customer_name") or ""),
        "is_emailed": bool(invoice.get("is_emailed")),
        "before_state": state,
        "before_state_sha256": digest_for(state),
        "protected_state": protected,
        "protected_state_sha256": digest_for(protected),
    }
    tax_rows = sorted(
        {row["applied"]["tax_id"]["new"] for row in line_change_evidence if "tax_id" in row["applied"]}
    )
    return {
        "invoice": invoice_row,
        "customer": customer_evidence_row,
        "addresses": address_evidence,
        "taxes": {tax_id: taxes[tax_id] for tax_id in tax_rows},
        "header_changes": header_changes,
        "line_changes": line_change_evidence,
        "lines": line_evidence,
        "totals": totals,
        "dependencies": dependencies,
        "put_endpoint": f"PUT /books/v3/invoices/{invoice_id}",
        "put_payload": put,
        "unprotected_keys": exempt,
        # This tool contains no mail transport. Nothing here can send anything.
        "email_sent": False,
    }


def build_totals(
    invoice: dict[str, Any],
    payload: dict[str, Any],
    sub_total_parts: list[Decimal | None],
    gross_parts: list[Decimal | None],
    put_lines: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    is_inclusive_tax: bool,
) -> dict[str, Any]:
    """Totals before, and after ONLY where Zoho's result is deterministic."""
    before = {
        key: money_text(_zero(invoice.get(key), f"invoice {key}"))
        for key in ("sub_total", "tax_total", "total", "balance")
    }
    if not payload["line_changes"]:
        # No line value moves, so Zoho recalculates to exactly the same figures.
        return {
            "before": before,
            "after": dict(before),
            "sub_total_deterministic": True,
            "total_deterministic": True,
            "tax_total_deterministic": True,
            "basis": "no line value changes: every total must stay byte-identical",
        }
    sub_total_deterministic = all(part is not None for part in sub_total_parts)
    expected_sub_total = (
        sum((part for part in sub_total_parts if part is not None), Decimal(0))
        if sub_total_deterministic else None
    )
    untaxed = all(not str(line.get("tax_id") or "").strip() for line in put_lines) and all(
        _zero(line.get("tax_percentage"), "line tax_percentage") == 0
        for line in lines
    )
    shipping = _zero(invoice.get("shipping_charge"), "invoice shipping_charge")
    adjustment = _zero(invoice.get("adjustment"), "invoice adjustment")
    flat = shipping == 0 and adjustment == 0
    total_deterministic = bool(sub_total_deterministic and untaxed and flat and not is_inclusive_tax)
    after: dict[str, str] = {}
    if sub_total_deterministic and expected_sub_total is not None:
        after["sub_total"] = money_text(expected_sub_total)
    if total_deterministic and expected_sub_total is not None:
        after["tax_total"] = money_text(Decimal(0))
        after["total"] = money_text(expected_sub_total)
        after["balance"] = money_text(expected_sub_total)
    # The gross subtotal leaves the byte-exact fingerprint on any line-value
    # change, so it is predicted here wherever every line's quantity x rate is
    # exact at the invoice precision, and asserted by verify_totals.
    gross_deterministic = bool(gross_parts) and all(part is not None for part in gross_parts)
    if gross_deterministic and "sub_total_exclusive_of_discount" in invoice:
        after["sub_total_exclusive_of_discount"] = money_text(
            sum((part for part in gross_parts if part is not None), Decimal(0))
        )
    # Base-currency mirrors, asserted only where the LIVE invoice proves the base
    # currency equals the document currency, so a foreign-currency invoice can
    # never false-fail on a figure this tool has no exchange rate to predict.
    for bcy_key, plain_key in (
        ("bcy_sub_total", "sub_total"),
        ("bcy_tax_total", "tax_total"),
        ("bcy_total", "total"),
    ):
        if bcy_key not in invoice or plain_key not in invoice or plain_key not in after:
            continue
        if _zero(invoice.get(bcy_key), f"invoice {bcy_key}") == _zero(
            invoice.get(plain_key), f"invoice {plain_key}"
        ):
            after[bcy_key] = after[plain_key]
    basis_parts = []
    if not gross_deterministic:
        basis_parts.append(
            "a line total needs rounding: the gross subtotal is not predicted here"
        )
    if is_inclusive_tax:
        basis_parts.append("invoice prices are tax-inclusive, so line totals are not predicted")
    if not untaxed:
        basis_parts.append("taxed lines: Zoho recalculates tax and total; not predicted here")
    if not flat:
        basis_parts.append(
            f"shipping {shipping} / adjustment {adjustment} present: total not predicted here"
        )
    if not basis_parts:
        basis_parts.append("untaxed, no shipping or adjustment: totals computed exactly")
    return {
        "before": before,
        "after": after,
        "sub_total_deterministic": sub_total_deterministic,
        "total_deterministic": total_deterministic,
        "tax_total_deterministic": total_deterministic,
        "basis": "; ".join(basis_parts),
    }


# ---------------------------------------------------------------------------
# Deterministic draft-invoice builder
# ---------------------------------------------------------------------------


def quantum(precision: int) -> Decimal:
    return Decimal(1).scaleb(-precision)


def create_customer_evidence(
    contact: dict[str, Any], organization: dict[str, str], stated_name: str
) -> dict[str, Any]:
    customer_id = positive_id(contact.get("contact_id"), "customer contact_id")
    contact_type = str(contact.get("contact_type") or "").strip()
    if contact_type != "customer":
        raise InvoiceRevisionError(
            f"REFUSED: contact {customer_id} is a {contact_type or 'unknown'} record, not a "
            "customer. A new invoice is only ever raised against an existing live customer."
        )
    status = str(contact.get("status") or "").strip()
    if status != "active":
        raise InvoiceRevisionError(
            f"REFUSED: customer {customer_id} is {status or 'unknown'}, not active."
        )
    name = clean_text(
        contact.get("contact_name") or contact.get("company_name"), "customer name", 300
    )
    if name != stated_name:
        raise InvoiceRevisionError(
            f"REFUSED: live customer {customer_id} is named {name!r}, not the stated "
            f"{stated_name!r}. Confirm the customer before anything is staged."
        )
    # The customer's own currency is preserved exactly as Zoho holds it. Neither
    # currency nor exchange rate is in the payload allowlist, so this tool cannot
    # override either; it only records what the invoice will inherit.
    currency = str(contact.get("currency_code") or "").strip() or organization["currency_code"]
    return {
        "customer_id": customer_id,
        "customer_name": name,
        "company_name": str(contact.get("company_name") or ""),
        "currency_code": currency,
        "currency_id": str(contact.get("currency_id") or ""),
        "status": status,
        "contact_type": contact_type,
    }


def item_evidence(item: dict[str, Any], stated_name: str) -> dict[str, Any]:
    item_id = positive_id(item.get("item_id"), "item_id")
    status = str(item.get("status") or "").strip()
    if status != "active":
        raise InvoiceRevisionError(
            f"REFUSED: Zoho item {item_id} is {status or 'unknown'}, not active. A new invoice "
            "line may only name an existing ACTIVE item."
        )
    name = clean_text(item.get("name"), f"item {item_id} name", 300)
    if name != stated_name:
        raise InvoiceRevisionError(
            f"REFUSED: live item {item_id} is named {name!r}, not the stated {stated_name!r}. "
            "Confirm the item before anything is staged."
        )
    return {
        "item_id": item_id,
        "name": name,
        "sku": str(item.get("sku") or ""),
        "status": status,
        "unit": str(item.get("unit") or ""),
        "product_type": str(item.get("product_type") or ""),
        "item_type": str(item.get("item_type") or ""),
        "live_rate": money_text(_zero(item.get("rate"), f"item {item_id} rate")),
        "description": str(item.get("description") or ""),
    }


def normalized_tax(record: dict[str, Any], kind: str) -> dict[str, Any]:
    """One shape for a simple tax and for a tax group, so both can be verified."""
    if kind == "tax_group":
        tax_id = positive_id(record.get("tax_group_id"), "tax_group_id")
        name = clean_text(record.get("tax_group_name"), f"tax group {tax_id} name", 200)
        percentage = live_number(
            record.get("tax_group_percentage"), f"tax group {tax_id} percentage"
        )
        raw_components = record.get("taxes")
        if not isinstance(raw_components, list) or not raw_components:
            raise InvoiceRevisionError(f"Zoho tax group {tax_id} lists no component taxes.")
        components = []
        for component in raw_components:
            if not isinstance(component, dict):
                raise InvoiceRevisionError(f"Zoho tax group {tax_id} has an unreadable component.")
            components.append({
                "tax_id": positive_id(component.get("tax_id"), "component tax_id"),
                "tax_name": str(component.get("tax_name") or ""),
                "tax_percentage": format(
                    live_number(component.get("tax_percentage"), "component tax_percentage"), "f"
                ),
            })
        return {
            "tax_id": tax_id,
            "tax_name": name,
            "tax_percentage": format(percentage, "f"),
            "kind": "tax_group",
            "components": components,
        }
    tax_id = positive_id(record.get("tax_id"), "tax_id")
    name = clean_text(record.get("tax_name"), f"tax {tax_id} name", 200)
    percentage = live_number(record.get("tax_percentage"), f"tax {tax_id} percentage")
    tax_type = str(record.get("tax_type") or "tax").strip() or "tax"
    return {
        "tax_id": tax_id,
        "tax_name": name,
        "tax_percentage": format(percentage, "f"),
        "kind": tax_type,
        "components": [{
            "tax_id": tax_id, "tax_name": name, "tax_percentage": format(percentage, "f"),
        }],
    }


def resolve_create_taxes(
    access_token: str, vault: dict[str, Any], wanted: list[str]
) -> dict[str, dict[str, Any]]:
    """Resolve exactly the tax IDs a plan names, as simple taxes or tax groups."""
    resolved: dict[str, dict[str, Any]] = {}
    if not wanted:
        return resolved
    for record in get_taxes(access_token, vault):
        if not isinstance(record, dict):
            continue
        tax_id = str(record.get("tax_id") or "").strip()
        if tax_id in wanted and tax_id not in resolved:
            resolved[tax_id] = normalized_tax(record, "tax")
    outstanding = [tax_id for tax_id in wanted if tax_id not in resolved]
    if outstanding:
        for record in get_tax_groups(access_token, vault):
            if not isinstance(record, dict):
                continue
            group_id = str(record.get("tax_group_id") or "").strip()
            if group_id in outstanding and group_id not in resolved:
                resolved[group_id] = normalized_tax(record, "tax_group")
    missing = sorted(set(wanted) - set(resolved))
    if missing:
        raise InvoiceRevisionError(
            "REFUSED: tax ID(s) " + ", ".join(missing) + " are not live taxes or tax groups in "
            "this organization. A taxed line must name an exact existing live tax."
        )
    return resolved


def settings_evidence(
    frp_record: dict[str, Any], organization: dict[str, str]
) -> dict[str, Any]:
    raw = frp_record.get("price_precision")
    if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= 6:
        precision, source = raw, "live FRP Depot organization record"
    else:
        precision, source = 2, "Zoho default: the organization record did not state it"
    return {
        "price_precision": precision,
        "price_precision_source": source,
        "organization_currency_code": organization["currency_code"],
        "invoice_numbering": AUTO_NUMBERING_NOTE,
    }


def build_draft_invoice(
    payload: dict[str, Any],
    settings: dict[str, Any],
    customer: dict[str, Any],
    addresses: dict[str, dict[str, Any]],
    items: dict[str, dict[str, Any]],
    taxes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """The single deterministic projection of live state + request -> plan.

    Stage builds it from live reads; the plan validator rebuilds it from the
    plan's own stored live evidence and requires an identical result, so a
    tampered figure, payload key, endpoint or total cannot survive review.
    """
    customer_row = dict(customer)
    if customer_row["customer_id"] != payload["customer_id"]:
        raise InvoiceRevisionError("The customer read live is not the requested customer.")
    fields = payload["fields"]
    precision = settings["price_precision"]
    precision_is_live = settings["price_precision_source"] == "live FRP Depot organization record"

    post: dict[str, Any] = {
        "customer_id": customer_row["customer_id"],
        "date": fields["date"]["value"],
        "due_date": fields["due_date"]["value"],
    }
    header: dict[str, Any] = {}
    address_evidence: dict[str, Any] = {}
    for field in CREATE_HEADER_FIELDS:
        if field not in fields:
            continue
        value = fields[field]["value"]
        if field in ("billing_address_id", "shipping_address_id"):
            address_evidence[field] = owned_address(
                value, addresses, customer_row["customer_id"], field
            )
        post[field] = value
        header[field] = {"value": value, "source": fields[field]["source"]}

    line_rows: list[dict[str, Any]] = []
    post_lines: list[dict[str, Any]] = []
    sub_total = Decimal(0)
    discount_total = Decimal(0)
    tax_total = Decimal(0)
    rounding_free = True
    tax_deterministic = True
    used_taxes: dict[str, dict[str, Any]] = {}
    for index, line in enumerate(payload["lines"]):
        label = f"lines[{index}]"
        item_id = line["item_id"]
        item = items.get(item_id)
        if item is None:
            raise InvoiceRevisionError(f"{label} item {item_id} was not read live.")
        if item["name"] != line["item_name"]:
            raise InvoiceRevisionError(
                f"REFUSED: {label} names item {item_id} as {line['item_name']!r} but the live item "
                f"is {item['name']!r}."
            )
        quantity = Decimal(line["quantity"]["value"])
        rate = Decimal(line["rate"]["value"])
        gross_exact = quantity * rate
        gross = gross_exact.quantize(quantum(precision), rounding=ROUND_HALF_UP)
        if gross != gross_exact:
            rounding_free = False

        discount_text = None
        discount_amount = Decimal(0)
        if "discount" in line:
            discount_text = line["discount"]["value"]
            if discount_text.endswith("%"):
                discount_exact = gross * Decimal(discount_text[:-1]) / Decimal(100)
            else:
                discount_exact = Decimal(discount_text)
            discount_amount = discount_exact.quantize(
                quantum(precision), rounding=ROUND_HALF_UP
            )
            if discount_amount != discount_exact:
                rounding_free = False
            if discount_amount > gross:
                raise InvoiceRevisionError(
                    f"REFUSED: {label} discount {discount_text} exceeds the line value "
                    f"{money_text(gross)}."
                )
        item_total = gross - discount_amount
        sub_total += item_total
        discount_total += discount_amount

        tax_id = ""
        tax_name = ""
        tax_percentage = ""
        line_tax = Decimal(0)
        if "tax_id" in line:
            tax_id = line["tax_id"]["value"]
            tax = taxes.get(tax_id)
            if tax is None:
                raise InvoiceRevisionError(
                    f"REFUSED: {label} tax {tax_id} is not a live tax in this organization."
                )
            stated = Decimal(line["tax_id"]["tax_percentage"])
            if Decimal(tax["tax_percentage"]) != stated:
                raise InvoiceRevisionError(
                    f"REFUSED: tax {tax_id} is live at {tax['tax_percentage']}%, not the stated "
                    f"{line['tax_id']['tax_percentage']}%."
                )
            tax_name = tax["tax_name"]
            tax_percentage = tax["tax_percentage"]
            used_taxes[tax_id] = tax
            # Zoho charges each component of a tax group separately and rounds
            # each one, so a group's cent total is not something this tool will
            # claim to know exactly.
            if tax["kind"] != "tax" or len(tax["components"]) != 1:
                tax_deterministic = False
            for component in tax["components"]:
                line_tax += (
                    item_total * Decimal(component["tax_percentage"]) / Decimal(100)
                ).quantize(CENT, rounding=ROUND_HALF_UP)
        tax_total += line_tax

        post_line: dict[str, Any] = {
            "item_id": item_id,
            "quantity": exact_float(quantity, f"{label} quantity"),
            "rate": exact_float(rate, f"{label} rate"),
        }
        if discount_text is not None:
            post_line["discount"] = (
                discount_text if discount_text.endswith("%")
                else exact_float(Decimal(discount_text), f"{label} discount")
            )
        if "description" in line:
            post_line["description"] = line["description"]["value"]
        if tax_id:
            post_line["tax_id"] = tax_id
        unknown = sorted(set(post_line) - set(LINE_POST_KEYS))
        if unknown:  # pragma: no cover - guarded by the input schema
            raise InvoiceRevisionError(
                "REFUSED: an assembled line names a field outside the commissioned surface: "
                + ", ".join(unknown)
            )
        post_lines.append(post_line)

        line_rows.append({
            "index": index,
            "item_id": item_id,
            "item_name": item["name"],
            "sku": item["sku"],
            "quantity": line["quantity"]["value"],
            "rate": line["rate"]["value"],
            "discount": "" if discount_text is None else discount_text,
            "description": line["description"]["value"] if "description" in line else None,
            "tax_id": tax_id,
            "tax_name": tax_name,
            "tax_percentage": tax_percentage,
            "expected_gross": money_text(gross),
            "expected_discount_amount": money_text(discount_amount),
            "expected_item_total": money_text(item_total),
            "expected_tax_amount": money_text(line_tax),
            "live_item_rate": item["live_rate"],
            "sources": {
                field: line[field]["source"] for field in CREATE_VALUE_FIELDS if field in line
            },
        })

    post["line_items"] = post_lines
    extra = sorted(set(post) - ALLOWED_POST_KEYS)
    if extra:  # pragma: no cover - guarded by the input schema
        raise InvoiceRevisionError(
            "REFUSED: the assembled payload names a field outside the commissioned surface: "
            + ", ".join(extra)
        )
    if not REQUIRED_POST_KEYS.issubset(post):  # pragma: no cover - always assembled above
        raise InvoiceRevisionError("The assembled payload is missing a required field.")
    if "invoice_number" in post:  # pragma: no cover - impossible by allowlist
        raise InvoiceRevisionError("REFUSED: the invoice number is assigned by Zoho, never here.")

    sub_total_deterministic = bool(precision_is_live or rounding_free)
    total_deterministic = bool(sub_total_deterministic and tax_deterministic)
    total = sub_total + tax_total
    expected = {
        "sub_total": money_text(sub_total),
        "discount_total": money_text(discount_total),
        "tax_total": money_text(tax_total),
        "total": money_text(total),
        "balance": money_text(total),
    }
    verified_totals: dict[str, str] = {}
    if sub_total_deterministic:
        verified_totals["sub_total"] = expected["sub_total"]
        verified_totals["discount_total"] = expected["discount_total"]
    if total_deterministic:
        verified_totals["tax_total"] = expected["tax_total"]
        verified_totals["total"] = expected["total"]
        verified_totals["balance"] = expected["balance"]
    basis: list[str] = []
    if not precision_is_live:
        basis.append(
            "price precision was not stated by the organization record, so two decimals were "
            "assumed"
            + ("; no line needed rounding, so the assumption cannot matter" if rounding_free
               else "; at least one line needs rounding, so the sub-total is not claimed exact")
        )
    if not tax_deterministic:
        basis.append(
            "a tax group or compound tax is used: Zoho rounds each component separately, so the "
            "tax and grand total are shown as estimates and are not asserted on read-back"
        )
    if not basis:
        basis.append(
            "live price precision, simple taxes only, no shipping or adjustment: every total is "
            "computed exactly and asserted on read-back"
        )

    return {
        "customer": customer_row,
        "addresses": address_evidence,
        "items": {item_id: items[item_id] for item_id in sorted({row["item_id"] for row in line_rows})},
        "taxes": dict(sorted(used_taxes.items())),
        "settings": settings,
        "header": header,
        "lines": line_rows,
        "totals": {
            "expected": expected,
            "verified": verified_totals,
            "sub_total_deterministic": sub_total_deterministic,
            "tax_total_deterministic": tax_deterministic,
            "total_deterministic": total_deterministic,
            "basis": "; ".join(basis),
        },
        "post_endpoint": f"POST {INVOICE_COLLECTION_PATH}",
        "post_payload": post,
        # This tool contains no mail transport. Nothing here can transmit anything.
        "email_sent": False,
    }


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


def write_plan(
    action: str,
    payload: dict[str, Any],
    organization: dict[str, str],
    evidence: dict[str, Any],
    risk: dict[str, Any],
) -> Path:
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "action": action,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "approval_required": APPROVAL_WORD,
        "origin": origin_record(),
        "organization": organization,
        "payload": payload,
        "risk": risk,
        "live_evidence": evidence,
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = (PLAN_DIR / f"{stamp}_{action}_{digest[:16]}.json").resolve()
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError as exc:
        raise InvoiceRevisionError("Refused to overwrite an existing invoice plan.") from exc
    return path


def stage_plan(payload: dict[str, Any], organization: dict[str, str], evidence: dict[str, Any]) -> Path:
    path = write_plan(ACTION, payload, organization, evidence, {
        "atomic": True,
        "single_put": True,
        "email_sent": False,
        "note": RISK_NOTE,
    })
    digest = json.loads(path.read_text(encoding="utf-8"))["sha256"]
    zoho_tool.append_receipt(
        f"zoho_books_{ACTION}_plan_staged_not_committed",
        f"plan={path}; sha256={digest}; invoice={payload['invoice_number']} "
        f"({payload['invoice_id']}); header_changes={len(payload['changes'])}; "
        f"line_changes={len(payload['line_changes'])}; zoho_writes=0; email_sent=false",
    )
    return path


def stage_create_plan(
    payload: dict[str, Any], organization: dict[str, str], evidence: dict[str, Any]
) -> Path:
    path = write_plan(CREATE_ACTION, payload, organization, evidence, {
        "atomic": True,
        "single_post": True,
        "email_sent": False,
        "note": CREATE_RISK_NOTE,
    })
    digest = json.loads(path.read_text(encoding="utf-8"))["sha256"]
    zoho_tool.append_receipt(
        f"zoho_books_{CREATE_ACTION}_plan_staged_not_committed",
        f"plan={path}; sha256={digest}; customer={payload['customer_name']} "
        f"({payload['customer_id']}); lines={len(payload['lines'])}; "
        f"total={evidence['totals']['expected']['total']} "
        f"{evidence['customer']['currency_code']}; zoho_writes=0; invoices_created=0; "
        "email_sent=false",
    )
    return path


def command_stage(args: argparse.Namespace) -> None:
    payload = validate_input(read_json_object(Path(args.input), "Stage input"))
    vault = zoho_tool.load_vault()
    access_token, vault = zoho_tool.refresh_access_token(vault)
    organization = verified_organization(access_token, vault)
    invoice = get_invoice(access_token, vault, payload["invoice_id"])

    customer: dict[str, Any] | None = None
    addresses: dict[str, dict[str, Any]] = {}
    if "customer_id" in payload["changes"]:
        customer_id = payload["changes"]["customer_id"]["value"]
        customer = get_customer(access_token, vault, customer_id)
        addresses = address_index(get_customer_addresses(access_token, vault, customer_id), customer)
    elif "billing_address_id" in payload["changes"] or "shipping_address_id" in payload["changes"]:
        customer_id = positive_id(invoice.get("customer_id"), "live customer_id")
        existing = get_customer(access_token, vault, customer_id)
        addresses = address_index(get_customer_addresses(access_token, vault, customer_id), existing)

    taxes: dict[str, dict[str, Any]] = {}
    if any("tax_id" in row["changes"] for row in payload["line_changes"]):
        taxes = tax_index(get_taxes(access_token, vault))

    evidence = build_revision(invoice, payload, customer, addresses, taxes)
    zoho_tool.save_vault(vault)
    path = stage_plan(payload, organization, evidence)
    plan = read_json_object(path, "Plan")
    print_stage_summary(plan, path)


def print_stage_summary(plan: dict[str, Any], path: Path) -> None:
    evidence = plan["live_evidence"]
    invoice = evidence["invoice"]
    print("STAGED_NOT_COMMITTED")
    print(f"Tool: {TOOL_NAME}")
    print(
        f"Invoice: {invoice['invoice_number']} ({invoice['invoice_id']}), status "
        f"{invoice['status']} -- PRESERVED UNCHANGED"
    )
    print(
        f"Organization: {plan['organization']['name']} "
        f"({plan['organization']['organization_id']}), {plan['organization']['currency_code']}"
    )
    print(
        f"Currency {invoice['currency_code']} and exchange rate {invoice['exchange_rate'] or 'n/a'}: "
        "UNCHANGED"
    )
    print("")
    if evidence["header_changes"]:
        print("Header changes:")
        for field, change in evidence["header_changes"].items():
            print(f"  {field}: {change['old']!r} -> {change['new']!r}")
            if field == "customer_id":
                print(
                    f"    live customer: {change['new_customer_name']} "
                    f"({change['new_customer_currency'] or 'org currency'})"
                )
            print(f"    source: {change['source']}")
    else:
        print("Header changes: none")
    print("")
    if evidence["line_changes"]:
        print("Line changes:")
        for row in evidence["line_changes"]:
            print(f"  line {row['line_item_id']} (item {row['item_id']}):")
            for field, change in row["applied"].items():
                print(f"    {field}: {change['old']!r} -> {change['new']!r}  [{change['source']}]")
    else:
        print("Line changes: none")
    print("")
    lines = evidence["lines"]
    print(
        f"Lines resent complete and in order: {len(lines)} "
        f"(every line_item_id and item_id preserved; no line added, removed or substituted)"
    )
    totals = evidence["totals"]
    print(
        "Totals before: sub_total {sub_total}, tax {tax_total}, total {total}, balance {balance}".format(
            **totals["before"]
        )
    )
    if totals["after"]:
        after = totals["after"]
        print(
            "Totals after (Zoho-deterministic): "
            + ", ".join(f"{key} {value}" for key, value in sorted(after.items()))
        )
    else:
        print("Totals after: NOT PREDICTED -- Zoho recalculates")
    print(f"Totals basis: {totals['basis']}")
    print("")
    print("Zoho writes performed by this stage: 0 (read-only GETs only)")
    print("Emails sent or queued by this tool, ever: 0 (it has no mail transport)")
    print(f"Plan: {path}")
    print(f"Plan sha256: {plan['sha256']}")
    print(f"Expires: {plan['expires_utc']} (24-hour maximum)")
    print(f"RISK: {RISK_NOTE}")
    print(
        f"To commit, Rachad must answer THIS plan with the exact one word {APPROVAL_WORD}. "
        "Commissioning and staging are not approval."
    )


def command_stage_create(args: argparse.Namespace) -> None:
    payload = validate_create_input(read_json_object(Path(args.input), "Draft-invoice stage input"))
    vault = zoho_tool.load_vault()
    access_token, vault = zoho_tool.refresh_access_token(vault)
    organization, frp_record = organization_record(access_token, vault)
    settings = settings_evidence(frp_record, organization)

    contact = get_customer(access_token, vault, payload["customer_id"])
    customer = create_customer_evidence(contact, organization, payload["customer_name"])
    addresses: dict[str, dict[str, Any]] = {}
    if any(field in payload["fields"] for field in ("billing_address_id", "shipping_address_id")):
        addresses = address_index(
            get_customer_addresses(access_token, vault, payload["customer_id"]), contact
        )

    items: dict[str, dict[str, Any]] = {}
    for line in payload["lines"]:
        item_id = line["item_id"]
        if item_id in items:
            continue
        items[item_id] = item_evidence(
            get_item(access_token, vault, item_id), line["item_name"]
        )
    taxes = resolve_create_taxes(
        access_token,
        vault,
        sorted({line["tax_id"]["value"] for line in payload["lines"] if "tax_id" in line}),
    )

    evidence = build_draft_invoice(payload, settings, customer, addresses, items, taxes)
    zoho_tool.save_vault(vault)
    path = stage_create_plan(payload, organization, evidence)
    print_create_summary(read_json_object(path, "Plan"), path)


def print_create_summary(plan: dict[str, Any], path: Path) -> None:
    evidence = plan["live_evidence"]
    customer = evidence["customer"]
    totals = evidence["totals"]
    print("STAGED_NOT_COMMITTED")
    print(f"Tool: {TOOL_NAME}")
    print("Action: create ONE NEW invoice in DRAFT status -- never transmitted to anyone")
    print(
        f"Organization: {plan['organization']['name']} "
        f"({plan['organization']['organization_id']}), {plan['organization']['currency_code']}"
    )
    print(
        f"Customer: {customer['customer_name']} ({customer['customer_id']}), "
        f"{customer['status']} {customer['contact_type']}"
    )
    print(f"Currency: {customer['currency_code']} -- the customer's own, never overridden")
    print(f"Invoice number: {AUTO_NUMBERING_NOTE}")
    print("")
    print("Header:")
    for field, entry in evidence["header"].items():
        print(f"  {field}: {entry['value']!r}")
        print(f"    source: {entry['source']}")
    if evidence["addresses"]:
        for field, record in sorted(evidence["addresses"].items()):
            print(
                f"  {field} {record.get('address_id')}: owned by customer "
                f"{customer['customer_id']}"
            )
    print("")
    print(f"Lines ({len(evidence['lines'])}), in this exact order:")
    for row in evidence["lines"]:
        print(
            f"  {row['index'] + 1}. {row['item_name']} (item {row['item_id']}"
            + (f", SKU {row['sku']}" if row["sku"] else "")
            + ")"
        )
        print(
            f"     qty {row['quantity']} x rate {row['rate']}"
            + (f" less discount {row['discount']}" if row["discount"] else "")
            + f" = {row['expected_item_total']}"
            + (f"  (live item rate {row['live_item_rate']})" if row["live_item_rate"] else "")
        )
        if row["description"] is not None:
            print(f"     description: {row['description']!r}")
        if row["tax_id"]:
            print(
                f"     tax: {row['tax_name']} ({row['tax_id']}) at {row['tax_percentage']}% "
                f"= {row['expected_tax_amount']}"
            )
        for field, source in sorted(row["sources"].items()):
            print(f"     {field} source: {source}")
    print("")
    expected = totals["expected"]
    print("Independently calculated totals (exact Decimal, half-up):")
    print(f"  sub_total      {expected['sub_total']} {customer['currency_code']}")
    print(f"  discount_total {expected['discount_total']}")
    print(f"  tax_total      {expected['tax_total']}"
          + ("" if totals["tax_total_deterministic"] else "   [ESTIMATE -- not asserted]"))
    print(f"  total          {expected['total']}"
          + ("" if totals["total_deterministic"] else "   [ESTIMATE -- not asserted]"))
    print(f"  balance        {expected['balance']}"
          + ("" if totals["total_deterministic"] else "   [ESTIMATE -- not asserted]"))
    print(f"Totals basis: {totals['basis']}")
    print(
        "Verified against the live invoice after creation: "
        + (", ".join(sorted(totals["verified"])) or "structural identities only")
    )
    print("")
    print(f"Price precision: {evidence['settings']['price_precision']} "
          f"({evidence['settings']['price_precision_source']})")
    print(f"Endpoint: {evidence['post_endpoint']} (exactly one, with only the organization id)")
    print("Zoho writes performed by this stage: 0 (read-only GETs only)")
    print("Emails sent or queued by this tool, ever: 0 (it has no mail transport)")
    print(f"Plan: {path}")
    print(f"Plan sha256: {plan['sha256']}")
    print(f"Expires: {plan['expires_utc']} (24-hour maximum)")
    print(f"RISK: {CREATE_RISK_NOTE}")
    print(
        f"To commit, Rachad must answer THIS plan with the exact one word {APPROVAL_WORD}. "
        "Commissioning and staging are not approval."
    )


# ---------------------------------------------------------------------------
# Plan validation
# ---------------------------------------------------------------------------


def validate_plan_common(plan: dict[str, Any]) -> str:
    closed_fields(plan, PLAN_FIELDS, "Plan")
    action = plan.get("action")
    if (
        plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("tool") != TOOL_NAME
        or action not in ACTIONS
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise InvoiceRevisionError(
            "Plan schema version, tool, action, or approval requirement is invalid."
        )
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise InvoiceRevisionError("Plan nonce is invalid.")
    require_origin(plan.get("origin"))
    organization = plan.get("organization")
    if not isinstance(organization, dict):
        raise InvoiceRevisionError("Plan organization is invalid.")
    closed_fields(organization, ORGANIZATION_FIELDS, "Plan organization")
    positive_id(organization["organization_id"], "plan organization_id")
    clean_text(organization["name"], "plan organization name", 200)
    clean_text(organization["currency_code"], "plan organization currency", 8)
    created = parse_time(plan["created_utc"], "creation time")
    expires = parse_time(plan["expires_utc"], "expiry")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise InvoiceRevisionError("Plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise InvoiceRevisionError("Plan creation time is in the future.")
    if now >= expires:
        raise InvoiceRevisionError("Plan expired. Stage a new plan for review.")
    return str(action)


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if validate_plan_common(plan) == CREATE_ACTION:
        return validate_create_plan(plan)
    risk = plan.get("risk")
    if not isinstance(risk, dict):
        raise InvoiceRevisionError("Plan risk disclosure is invalid.")
    closed_fields(risk, RISK_FIELDS, "Plan risk")
    if (
        risk["atomic"] is not True
        or risk["single_put"] is not True
        or risk["email_sent"] is not False
        or risk["note"] != RISK_NOTE
    ):
        raise InvoiceRevisionError("Plan must disclose the exact single-atomic-PUT risk.")
    payload = plan.get("payload")
    if not isinstance(payload, dict):
        raise InvoiceRevisionError("Plan payload is invalid.")
    validated = validate_input(payload)
    if payload != validated:
        raise InvoiceRevisionError("Plan payload is not canonical.")
    evidence = plan.get("live_evidence")
    if not isinstance(evidence, dict):
        raise InvoiceRevisionError("Plan live evidence is invalid.")
    closed_fields(evidence, EVIDENCE_FIELDS, "Plan live evidence")
    invoice_row = evidence["invoice"]
    if not isinstance(invoice_row, dict):
        raise InvoiceRevisionError("Plan invoice evidence is invalid.")
    closed_fields(invoice_row, INVOICE_EVIDENCE_FIELDS, "Plan invoice evidence")
    before = invoice_row["before_state"]
    if not isinstance(before, dict):
        raise InvoiceRevisionError("Plan before-state evidence must be an object.")
    if not secrets.compare_digest(str(invoice_row["before_state_sha256"]), digest_for(before)):
        raise InvoiceRevisionError("Plan before-state evidence hash is invalid.")
    if not secrets.compare_digest(
        str(invoice_row["protected_state_sha256"]), digest_for(invoice_row["protected_state"])
    ):
        raise InvoiceRevisionError("Plan protected-state evidence hash is invalid.")
    if evidence["email_sent"] is not False:
        raise InvoiceRevisionError("Plan must record that no email is sent.")
    taxes = evidence["taxes"]
    if not isinstance(taxes, dict):
        raise InvoiceRevisionError("Plan tax evidence is invalid.")
    addresses = evidence["addresses"]
    if not isinstance(addresses, dict):
        raise InvoiceRevisionError("Plan address evidence is invalid.")
    address_lookup = {
        str(record.get("address_id")): record
        for record in addresses.values()
        if isinstance(record, dict)
    }
    customer = evidence["customer"]
    if customer is not None and not isinstance(customer, dict):
        raise InvoiceRevisionError("Plan customer evidence is invalid.")
    # Re-derive the ENTIRE projection from the immutable staged live state. A
    # tampered figure, payload key, endpoint or fingerprint cannot survive.
    rebuilt = build_revision(before, payload, _customer_state(customer, before), address_lookup, taxes)
    if rebuilt != evidence:
        raise InvoiceRevisionError(
            "Plan evidence is not the canonical projection of the staged live invoice state."
        )
    put = evidence["put_payload"]
    if not isinstance(put, dict) or set(put) - ALLOWED_PUT_KEYS or not REQUIRED_PUT_KEYS.issubset(put):
        raise InvoiceRevisionError("Plan payload names a field outside the commissioned surface.")
    if evidence["put_endpoint"] != f"PUT /books/v3/invoices/{payload['invoice_id']}":
        raise InvoiceRevisionError("Plan endpoint is not the one commissioned invoice route.")
    return evidence


def validate_create_plan(plan: dict[str, Any]) -> dict[str, Any]:
    risk = plan.get("risk")
    if not isinstance(risk, dict):
        raise InvoiceRevisionError("Plan risk disclosure is invalid.")
    closed_fields(risk, CREATE_RISK_FIELDS, "Plan risk")
    if (
        risk["atomic"] is not True
        or risk["single_post"] is not True
        or risk["email_sent"] is not False
        or risk["note"] != CREATE_RISK_NOTE
    ):
        raise InvoiceRevisionError("Plan must disclose the exact single-atomic-POST risk.")
    payload = plan.get("payload")
    if not isinstance(payload, dict):
        raise InvoiceRevisionError("Plan payload is invalid.")
    validated = validate_create_input(payload)
    if payload != validated:
        raise InvoiceRevisionError("Plan payload is not canonical.")
    evidence = plan.get("live_evidence")
    if not isinstance(evidence, dict):
        raise InvoiceRevisionError("Plan live evidence is invalid.")
    closed_fields(evidence, CREATE_EVIDENCE_FIELDS, "Plan live evidence")
    if evidence["email_sent"] is not False:
        raise InvoiceRevisionError("Plan must record that no email is sent.")
    customer = evidence["customer"]
    if not isinstance(customer, dict):
        raise InvoiceRevisionError("Plan customer evidence is invalid.")
    closed_fields(customer, CREATE_CUSTOMER_FIELDS, "Plan customer evidence")
    settings = evidence["settings"]
    if not isinstance(settings, dict):
        raise InvoiceRevisionError("Plan settings evidence is invalid.")
    closed_fields(settings, CREATE_SETTINGS_FIELDS, "Plan settings evidence")
    if settings["invoice_numbering"] != AUTO_NUMBERING_NOTE:
        raise InvoiceRevisionError("Plan must record that Zoho assigns the invoice number.")
    items = evidence["items"]
    if not isinstance(items, dict) or not items:
        raise InvoiceRevisionError("Plan item evidence is invalid.")
    for item_id, record in items.items():
        if not isinstance(record, dict):
            raise InvoiceRevisionError("Plan item evidence is invalid.")
        closed_fields(record, CREATE_ITEM_FIELDS, f"Plan item evidence {item_id}")
        if record["item_id"] != item_id:
            raise InvoiceRevisionError("Plan item evidence is keyed inconsistently.")
    taxes = evidence["taxes"]
    if not isinstance(taxes, dict):
        raise InvoiceRevisionError("Plan tax evidence is invalid.")
    for tax_id, record in taxes.items():
        if not isinstance(record, dict):
            raise InvoiceRevisionError("Plan tax evidence is invalid.")
        closed_fields(record, CREATE_TAX_FIELDS, f"Plan tax evidence {tax_id}")
        if record["tax_id"] != tax_id:
            raise InvoiceRevisionError("Plan tax evidence is keyed inconsistently.")
    addresses = evidence["addresses"]
    if not isinstance(addresses, dict):
        raise InvoiceRevisionError("Plan address evidence is invalid.")
    address_lookup = {
        str(record.get("address_id")): record
        for record in addresses.values()
        if isinstance(record, dict)
    }
    # Re-derive the ENTIRE projection from the immutable staged live evidence. A
    # tampered figure, payload key, endpoint or total cannot survive.
    rebuilt = build_draft_invoice(payload, settings, customer, address_lookup, items, taxes)
    if rebuilt != evidence:
        raise InvoiceRevisionError(
            "Plan evidence is not the canonical projection of the staged live state."
        )
    post = evidence["post_payload"]
    if (
        not isinstance(post, dict)
        or set(post) - ALLOWED_POST_KEYS
        or not REQUIRED_POST_KEYS.issubset(post)
    ):
        raise InvoiceRevisionError("Plan payload names a field outside the commissioned surface.")
    if evidence["post_endpoint"] != f"POST {INVOICE_COLLECTION_PATH}":
        raise InvoiceRevisionError(
            "Plan endpoint is not the one commissioned invoice-creation route."
        )
    return evidence


def _customer_state(customer: dict[str, Any] | None, invoice: dict[str, Any]) -> dict[str, Any] | None:
    """Rebuild the contact shape build_revision needs from stored evidence."""
    if customer is None:
        return None
    _ = invoice
    return {
        "contact_id": customer.get("customer_id"),
        "contact_name": customer.get("customer_name"),
        "company_name": customer.get("company_name"),
        "currency_code": customer.get("currency_code"),
        "currency_id": customer.get("currency_id"),
        "status": customer.get("status"),
        "contact_type": customer.get("contact_type"),
        "billing_address": customer.get("billing_address"),
        "shipping_address": customer.get("shipping_address"),
    }


def load_plan(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = read_json_object(path, "Plan")
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise InvoiceRevisionError("Plan hash check failed. The plan changed after review.")
    evidence = validate_plan(plan)
    return plan, evidence


def lock_path(plan_sha256: str) -> Path:
    if not HEX_64_RE.fullmatch(str(plan_sha256)):
        raise InvoiceRevisionError("Plan digest is invalid for replay locking.")
    return PLAN_DIR / ".commit-locks" / f"{plan_sha256}.json"


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise InvoiceRevisionError(
            "REFUSED: this plan has already entered commit and cannot be replayed."
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


# ---------------------------------------------------------------------------
# The write transports -- two allowlists, ONE network call site
# ---------------------------------------------------------------------------


def _perform(request: Request, label: str) -> dict[str, Any]:
    """The single network call site in this module.

    Both allowlists below funnel through here, so no other code path in this
    tool can reach Zoho at all, and there is exactly one place to audit.
    """
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise InvoiceRevisionError(
            f"Zoho invoice {label} failed with HTTP {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise InvoiceRevisionError(
            f"Zoho invoice {label} outcome is indeterminate: {exc.reason}"
        ) from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvoiceRevisionError(
            f"Zoho invoice {label} returned invalid JSON; the outcome is indeterminate."
        ) from exc
    if not isinstance(result, dict) or result.get("code") != 0:
        message = result.get("message") if isinstance(result, dict) else "invalid response"
        raise InvoiceRevisionError(
            f"Zoho invoice {label} returned an invalid or unknown result: " + str(message)
        )
    return result


def oauth_invoice_create_write_allowed(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """The complete OAuth draft-creation transport and its exact allowlist.

    Only POST. Only the one invoice collection route -- no ID-suffixed route,
    no lifecycle route, no bulk route, no mail parameter of any kind exists here
    or anywhere else in this module. Only the commissioned fields, every line
    linked to an existing item. The invoice number is deliberately absent, and
    the query string is exactly the organization id, so Zoho's own numbering
    runs and the result is a Draft.
    """
    if method != "POST":
        raise InvoiceRevisionError("REFUSED: the draft-invoice creation is a POST and nothing else.")
    if not INVOICE_COLLECTION_RE.fullmatch(str(path)):
        raise InvoiceRevisionError(
            "REFUSED: draft creation targets the one exact invoice collection route. Deletion, "
            "voiding, status, mail, reminder, payment, credit-note, attachment, template, "
            "estimate conversion and every bulk route are unreachable."
        )
    if not isinstance(payload, dict):
        raise InvoiceRevisionError("REFUSED: the invoice POST payload must be an object.")
    extra = sorted(set(payload) - ALLOWED_POST_KEYS)
    if extra:
        raise InvoiceRevisionError(
            "REFUSED: the invoice POST payload names uncommissioned field(s): " + ", ".join(extra)
        )
    if not REQUIRED_POST_KEYS.issubset(payload):
        raise InvoiceRevisionError(
            "REFUSED: the invoice POST payload must always carry the customer, both dates and at "
            "least one item-linked line."
        )
    positive_id(payload["customer_id"], "payload customer_id")
    for field in ("date", "due_date"):
        parse_date(clean_text(payload[field], f"payload {field}", 10), f"payload {field}")
    lines = payload["line_items"]
    if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES:
        raise InvoiceRevisionError(
            "REFUSED: the invoice POST must carry between one and "
            f"{MAX_LINES} item-linked lines."
        )
    for line in lines:
        if not isinstance(line, dict):
            raise InvoiceRevisionError("REFUSED: every POST line must be an object.")
        unknown = sorted(set(line) - set(LINE_POST_KEYS))
        if unknown:
            raise InvoiceRevisionError(
                "REFUSED: a POST line names uncommissioned field(s): " + ", ".join(unknown)
            )
        if not REQUIRED_LINE_POST_KEYS.issubset(line):
            raise InvoiceRevisionError(
                "REFUSED: every POST line must name an existing item with its quantity and rate. "
                "Free-text and unlinked lines are not reachable through this tool."
            )
        positive_id(line["item_id"], "payload line item_id")
    org_id = positive_id(organization_id, "organization_id")
    query = urlencode({"organization_id": org_id})
    request = Request(
        api_domain.rstrip("/") + path + "?" + query,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return _perform(request, "creation")


def oauth_invoice_revision_write_allowed(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """The complete OAuth write transport and its exact allowlist.

    Only PUT. Only one positive-ID invoice endpoint. Only the commissioned
    fields, with the invoice number, customer and complete line list always
    present so nothing can be renumbered or dropped by omission. The query
    string is exactly the organization id -- no action, no status, no mail
    parameter of any kind exists here or anywhere else in this module.
    """
    if method != "PUT":
        raise InvoiceRevisionError("REFUSED: the invoice revision is a PUT and nothing else.")
    match = INVOICE_PATH_RE.fullmatch(str(path))
    if not match:
        raise InvoiceRevisionError(
            "REFUSED: the invoice revision targets one exact positive-ID invoice endpoint. "
            "Creation, deletion, voiding, status, mail, reminder, payment, credit-note, "
            "attachment, template and every bulk route are unreachable."
        )
    positive_id(match.group(1), "invoice endpoint ID")
    if not isinstance(payload, dict):
        raise InvoiceRevisionError("REFUSED: the invoice PUT payload must be an object.")
    extra = sorted(set(payload) - ALLOWED_PUT_KEYS)
    if extra:
        raise InvoiceRevisionError(
            "REFUSED: the invoice PUT payload names uncommissioned field(s): " + ", ".join(extra)
        )
    if not REQUIRED_PUT_KEYS.issubset(payload):
        raise InvoiceRevisionError(
            "REFUSED: the invoice PUT payload must always carry the preserved invoice number, "
            "customer, dates and the complete line list."
        )
    clean_text(payload["invoice_number"], "preserved invoice_number", 100)
    positive_id(payload["customer_id"], "payload customer_id")
    lines = payload["line_items"]
    if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES:
        raise InvoiceRevisionError("REFUSED: the invoice PUT must carry the complete line list.")
    seen: set[str] = set()
    for line in lines:
        if not isinstance(line, dict):
            raise InvoiceRevisionError("REFUSED: every PUT line must be an object.")
        line_item_id = positive_id(line.get("line_item_id"), "payload line_item_id")
        if line_item_id in seen:
            raise InvoiceRevisionError("REFUSED: the invoice PUT repeats a line_item_id.")
        seen.add(line_item_id)
        unknown = sorted(set(line) - set(LINE_PUT_KEYS))
        if unknown:
            raise InvoiceRevisionError(
                "REFUSED: a PUT line names uncommissioned field(s): " + ", ".join(unknown)
            )
    org_id = positive_id(organization_id, "organization_id")
    query = urlencode({"organization_id": org_id})
    request = Request(
        api_domain.rstrip("/") + path + "?" + query,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    return _perform(request, "PUT")


# ---------------------------------------------------------------------------
# Read-back verification
# ---------------------------------------------------------------------------


def verify_identity_and_status(after: dict[str, Any], invoice_row: dict[str, Any]) -> None:
    checks = (
        ("invoice_id", invoice_row["invoice_id"]),
        ("invoice_number", invoice_row["invoice_number"]),
        ("status", invoice_row["status"]),
        ("currency_id", invoice_row["currency_id"]),
        ("currency_code", invoice_row["currency_code"]),
    )
    for key, expected in checks:
        actual = str(after.get(key) if after.get(key) is not None else "")
        if actual != expected:
            raise InvoiceRevisionError(
                f"Read-back {key} is {actual!r}, not the preserved {expected!r}. Stop and reconcile."
            )
    expected_rate = invoice_row["exchange_rate"]
    actual_rate = str(after.get("exchange_rate") if after.get("exchange_rate") is not None else "")
    if expected_rate and actual_rate and live_number(actual_rate, "read-back exchange_rate") != live_number(
        expected_rate, "staged exchange_rate"
    ):
        raise InvoiceRevisionError(
            f"Read-back exchange rate is {actual_rate}, not the preserved {expected_rate}."
        )


def verify_dependencies_unchanged(after: dict[str, Any], staged: dict[str, Any]) -> None:
    fresh = dependency_state(after)
    for key in ("status", "payment_made", "credits_applied", "write_off_amount"):
        if fresh[key] != staged[key]:
            raise InvoiceRevisionError(
                f"Read-back {key} is {fresh[key]!r}, not the approved {staged[key]!r}. "
                "Stop and reconcile."
            )
    if live_number(after.get("balance"), "read-back balance") != live_number(
        after.get("total"), "read-back total"
    ):
        raise InvoiceRevisionError(
            "Read-back balance no longer equals the invoice total. Stop and reconcile."
        )


def verify_header_changes(after: dict[str, Any], evidence: dict[str, Any]) -> None:
    for field, change in evidence["header_changes"].items():
        if field in ("billing_address_id", "shipping_address_id"):
            key = field.replace("_id", "")
            block = after.get(key)
            actual = ""
            if isinstance(block, dict):
                actual = str(block.get("address_id") or "")
            actual = actual or str(after.get(field) or "")
            if actual != change["new"]:
                raise InvoiceRevisionError(
                    f"Read-back {field} is {actual!r}, not the approved {change['new']!r}."
                )
            continue
        actual = after.get(field)
        actual_text = "" if actual is None else str(actual)
        if actual_text != change["new"]:
            raise InvoiceRevisionError(
                f"Read-back {field} is {actual_text!r}, not the approved {change['new']!r}."
            )
    if "customer_id" in evidence["header_changes"] and evidence["customer"] is not None:
        expected_name = evidence["customer"]["customer_name"]
        actual_name = str(after.get("customer_name") or "")
        if actual_name != expected_name:
            raise InvoiceRevisionError(
                f"Read-back customer_name is {actual_name!r}, not the live customer "
                f"{expected_name!r}."
            )
    for field in ("date", "due_date"):
        expected = evidence["put_payload"][field]
        actual = str(after.get(field) or "")
        if actual != expected:
            raise InvoiceRevisionError(
                f"Read-back {field} is {actual!r}, not the approved {expected!r}."
            )


def verify_lines(after: dict[str, Any], evidence: dict[str, Any]) -> None:
    lines = after.get("line_items")
    expected_rows = evidence["lines"]
    if not isinstance(lines, list) or len(lines) != len(expected_rows):
        raise InvoiceRevisionError(
            f"Read-back has {len(lines) if isinstance(lines, list) else 'no'} line(s), not the "
            f"{len(expected_rows)} approved lines. Stop and reconcile."
        )
    changed_by_id = {row["line_item_id"]: row["applied"] for row in evidence["line_changes"]}
    for index, (line, expected) in enumerate(zip(lines, expected_rows)):
        if not isinstance(line, dict):
            raise InvoiceRevisionError(f"Read-back line {index} is not an object.")
        line_item_id = str(line.get("line_item_id") or "")
        item_id = str(line.get("item_id") or "")
        if line_item_id != expected["line_item_id"]:
            raise InvoiceRevisionError(
                f"Read-back line {index} is {line_item_id!r}, not the approved "
                f"{expected['line_item_id']!r}. Line identity or order changed. Stop and reconcile."
            )
        if item_id != expected["item_id"]:
            raise InvoiceRevisionError(
                f"Read-back line {line_item_id} carries item {item_id!r}, not the preserved "
                f"{expected['item_id']!r}. Stop and reconcile."
            )
        applied = changed_by_id.get(line_item_id)
        before = expected["before"]
        if applied is None:
            if json_copy(line) != before:
                moved = sorted(
                    key for key in set(line) | set(before)
                    if json_copy(line).get(key, "\0missing") != before.get(key, "\0missing")
                )
                raise InvoiceRevisionError(
                    f"Read-back line {line_item_id} was not approved for change but moved: "
                    + ", ".join(moved)
                    + ". Stop and reconcile."
                )
            continue
        for field, change in applied.items():
            actual = line.get(field)
            if field in ("quantity", "rate"):
                if live_number(actual, f"read-back line {line_item_id} {field}") != Decimal(
                    change["new"]
                ):
                    raise InvoiceRevisionError(
                        f"Read-back line {line_item_id} {field} is {actual!r}, not the approved "
                        f"{change['new']!r}."
                    )
            elif field == "discount":
                approved_percent = line_discount_kind(change["new"]) == "percent"
                actual_text = str(actual if actual is not None else "").strip()
                actual_percent = actual_text.endswith("%")
                if approved_percent != actual_percent and actual_text not in ("", "0"):
                    raise InvoiceRevisionError(
                        f"Read-back line {line_item_id} discount is {actual!r}, which is not the "
                        f"same kind of discount as the approved {change['new']!r}."
                    )
                approved_value = Decimal(change["new"].rstrip("%"))
                actual_value = _zero(
                    actual_text.rstrip("%"), f"read-back line {line_item_id} discount"
                )
                if actual_value != approved_value:
                    raise InvoiceRevisionError(
                        f"Read-back line {line_item_id} discount is {actual!r}, not the approved "
                        f"{change['new']!r}."
                    )
            elif field == "tax_id":
                if str(actual or "") != change["new"]:
                    raise InvoiceRevisionError(
                        f"Read-back line {line_item_id} tax_id is {actual!r}, not the approved "
                        f"{change['new']!r}."
                    )
                percentage = _zero(line.get("tax_percentage"), "read-back tax_percentage")
                if percentage != Decimal(change["new_tax_percentage"]):
                    raise InvoiceRevisionError(
                        f"Read-back line {line_item_id} tax rate is {percentage}%, not the approved "
                        f"{change['new_tax_percentage']}%."
                    )
            elif str(actual if actual is not None else "") != change["new"]:
                raise InvoiceRevisionError(
                    f"Read-back line {line_item_id} {field} is {actual!r}, not the approved "
                    f"{change['new']!r}."
                )
        # Everything on a changed line that was NOT approved to move, and that
        # Zoho does not recompute from the approved fields, must be identical.
        exempt = set(applied) | set(LINE_DERIVED_FIELDS)
        for key, value in before.items():
            if key in exempt:
                continue
            if json_copy(line.get(key)) != value:
                raise InvoiceRevisionError(
                    f"Read-back line {line_item_id} field {key} changed outside the approved "
                    "fields. Stop and reconcile."
                )
        if expected["expected_item_total"] is not None and "tax_id" not in applied:
            actual_total = live_number(line.get("item_total"), f"line {line_item_id} item_total")
            if money_text(actual_total) != expected["expected_item_total"]:
                raise InvoiceRevisionError(
                    f"Read-back line {line_item_id} item_total is {money_text(actual_total)}, not "
                    f"the calculated {expected['expected_item_total']}."
                )


def verify_totals(after: dict[str, Any], evidence: dict[str, Any]) -> None:
    totals = evidence["totals"]
    for key, expected in totals["after"].items():
        actual = live_number(after.get(key), f"read-back {key}")
        if money_text(actual) != expected:
            raise InvoiceRevisionError(
                f"Read-back {key} is {money_text(actual)}, not the calculated {expected}. "
                "Stop and reconcile."
            )


def verify_created_invoice(after: dict[str, Any], evidence: dict[str, Any]) -> str:
    """Everything the approved plan promised, checked against the LIVE invoice."""
    invoice_id = positive_id(after.get("invoice_id"), "read-back invoice_id")
    status = str(after.get("status") or "")
    if status != DRAFT_STATUS:
        raise InvoiceRevisionError(
            f"Read-back status is {status!r}, not exactly {DRAFT_STATUS!r}. The new invoice "
            f"{invoice_id} exists but is NOT the Draft that was approved. Stop and reconcile by "
            "hand; nothing will be retried, deleted or changed."
        )
    invoice_number = clean_text(after.get("invoice_number"), "read-back invoice_number", 100)
    if "invoice_number" in evidence["post_payload"]:  # pragma: no cover - impossible by allowlist
        raise InvoiceRevisionError("The invoice number must be assigned by Zoho, never supplied.")
    if bool(after.get("is_emailed")):
        raise InvoiceRevisionError(
            f"Read-back says invoice {invoice_number} has already been mailed. This tool has no "
            "mail transport; something else acted. Stop and reconcile."
        )
    customer = evidence["customer"]
    if str(after.get("customer_id") or "") != customer["customer_id"]:
        raise InvoiceRevisionError(
            f"Read-back customer_id is {after.get('customer_id')!r}, not the approved "
            f"{customer['customer_id']!r}."
        )
    if str(after.get("customer_name") or "") != customer["customer_name"]:
        raise InvoiceRevisionError(
            f"Read-back customer_name is {after.get('customer_name')!r}, not the approved "
            f"{customer['customer_name']!r}."
        )
    if str(after.get("currency_code") or "") != customer["currency_code"]:
        raise InvoiceRevisionError(
            f"Read-back currency is {after.get('currency_code')!r}, not the customer's own "
            f"{customer['currency_code']!r}. Stop and reconcile."
        )
    if customer["currency_id"] and str(after.get("currency_id") or "") != customer["currency_id"]:
        raise InvoiceRevisionError(
            f"Read-back currency_id is {after.get('currency_id')!r}, not the customer's "
            f"{customer['currency_id']!r}."
        )

    for field, entry in evidence["header"].items():
        if field in ("billing_address_id", "shipping_address_id"):
            block = after.get(field.replace("_id", ""))
            actual = str(block.get("address_id") or "") if isinstance(block, dict) else ""
            actual = actual or str(after.get(field) or "")
        else:
            value = after.get(field)
            actual = "" if value is None else str(value)
        if actual != entry["value"]:
            raise InvoiceRevisionError(
                f"Read-back {field} is {actual!r}, not the approved {entry['value']!r}."
            )

    rows = evidence["lines"]
    lines = after.get("line_items")
    if not isinstance(lines, list) or len(lines) != len(rows):
        raise InvoiceRevisionError(
            f"Read-back has {len(lines) if isinstance(lines, list) else 'no'} line(s), not the "
            f"{len(rows)} approved lines. Stop and reconcile."
        )
    exact_lines = evidence["totals"]["sub_total_deterministic"]
    any_discount = False
    for row, line in zip(rows, lines):
        position = row["index"] + 1
        if not isinstance(line, dict):
            raise InvoiceRevisionError(f"Read-back line {position} is not an object.")
        if str(line.get("item_id") or "") != row["item_id"]:
            raise InvoiceRevisionError(
                f"Read-back line {position} carries item {line.get('item_id')!r}, not the approved "
                f"{row['item_id']!r}. Line identity or order is wrong. Stop and reconcile."
            )
        for field in ("quantity", "rate"):
            if live_number(line.get(field), f"read-back line {position} {field}") != Decimal(
                row[field]
            ):
                raise InvoiceRevisionError(
                    f"Read-back line {position} {field} is {line.get(field)!r}, not the approved "
                    f"{row[field]!r}."
                )
        actual_discount = str(line.get("discount") if line.get("discount") is not None else "").strip()
        if row["discount"]:
            any_discount = True
            if row["discount"].endswith("%") != actual_discount.endswith("%") and actual_discount not in ("", "0"):
                raise InvoiceRevisionError(
                    f"Read-back line {position} discount is {line.get('discount')!r}, which is not "
                    f"the same kind of discount as the approved {row['discount']!r}."
                )
            if _zero(actual_discount.rstrip("%"), f"read-back line {position} discount") != Decimal(
                row["discount"].rstrip("%")
            ):
                raise InvoiceRevisionError(
                    f"Read-back line {position} discount is {line.get('discount')!r}, not the "
                    f"approved {row['discount']!r}."
                )
        elif _zero(actual_discount.rstrip("%"), f"read-back line {position} discount") != 0:
            raise InvoiceRevisionError(
                f"Read-back line {position} carries discount {line.get('discount')!r}, but none "
                "was approved."
            )
        if row["description"] is not None:
            actual_description = str(line.get("description") or "")
            if actual_description != row["description"]:
                raise InvoiceRevisionError(
                    f"Read-back line {position} description is {actual_description!r}, not the "
                    f"approved {row['description']!r}."
                )
        if str(line.get("tax_id") or "") != row["tax_id"]:
            raise InvoiceRevisionError(
                f"Read-back line {position} tax_id is {line.get('tax_id')!r}, not the approved "
                f"{row['tax_id'] or 'no tax'!r}."
            )
        actual_percentage = _zero(line.get("tax_percentage"), f"read-back line {position} tax rate")
        expected_percentage = Decimal(row["tax_percentage"]) if row["tax_id"] else Decimal(0)
        if actual_percentage != expected_percentage:
            raise InvoiceRevisionError(
                f"Read-back line {position} tax rate is {actual_percentage}%, not the approved "
                f"{expected_percentage}%."
            )
        if exact_lines:
            actual_total = live_number(line.get("item_total"), f"read-back line {position} total")
            if money_text(actual_total) != row["expected_item_total"]:
                raise InvoiceRevisionError(
                    f"Read-back line {position} item_total is {money_text(actual_total)}, not the "
                    f"calculated {row['expected_item_total']}."
                )

    # Nothing this tool cannot set may appear on the new invoice.
    shipping = _zero(after.get("shipping_charge"), "read-back shipping_charge")
    adjustment = _zero(after.get("adjustment"), "read-back adjustment")
    if shipping != 0 or adjustment != 0:
        raise InvoiceRevisionError(
            f"Read-back carries shipping {shipping} and adjustment {adjustment}; this tool sets "
            "neither and approved neither. Stop and reconcile."
        )
    if bool(after.get("is_inclusive_tax")):
        raise InvoiceRevisionError(
            "Read-back prices are tax-inclusive, which the approved calculation did not assume. "
            "Stop and reconcile."
        )
    if any_discount:
        discount_type = str(after.get("discount_type") or "").strip()
        if discount_type and discount_type != "item_level":
            raise InvoiceRevisionError(
                f"Read-back applies discounts at {discount_type!r}, not the line level the "
                "approved calculation assumed. Stop and reconcile."
            )
    for key, expected in sorted(evidence["totals"]["verified"].items()):
        if key not in after and expected == money_text(Decimal(0)):
            continue
        actual = live_number(after.get(key), f"read-back {key}")
        if money_text(actual) != expected:
            raise InvoiceRevisionError(
                f"Read-back {key} is {money_text(actual)}, not the calculated {expected}. "
                "Stop and reconcile."
            )
    sub_total = live_number(after.get("sub_total"), "read-back sub_total")
    tax_total = _zero(after.get("tax_total"), "read-back tax_total")
    total = live_number(after.get("total"), "read-back total")
    if total != sub_total + tax_total + shipping + adjustment:
        raise InvoiceRevisionError(
            f"Read-back total {total} is not its own sub-total {sub_total} plus tax {tax_total}. "
            "Stop and reconcile."
        )
    # Refuses anything already applied against the brand-new invoice, and
    # re-checks that its status is one this tool is allowed to have produced.
    dependency_state(after)
    return invoice_number


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def commit_create(plan: dict[str, Any], evidence: dict[str, Any], plan_path: Path) -> None:
    payload = plan["payload"]
    lock = lock_path(plan["sha256"])
    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": CREATE_ACTION,
        "status": "in_flight",
        "customer_id": payload["customer_id"],
        "started_utc": utc_now().isoformat(),
    }, exclusive=True)
    write_attempted = False
    invoice_id = ""
    invoice_number = ""
    try:
        vault = zoho_tool.load_vault()
        if CREATE_SCOPE not in (vault.get("scopes") or []):
            raise InvoiceRevisionError(
                f"Saved Zoho connection lacks {CREATE_SCOPE}. Run PREPARE_DADO_ZOHO_ACCESS.bat, "
                "create the grant in the Zoho API Console, then REAUTHORIZE_DADO_ZOHO.bat."
            )
        access_token, vault = zoho_tool.refresh_access_token(vault)
        organization, frp_record = organization_record(access_token, vault)
        if organization != plan["organization"]:
            raise InvoiceRevisionError(
                "REFUSED: the live FRP Depot Books organization does not match the organization "
                "recorded in the plan."
            )
        if settings_evidence(frp_record, organization) != evidence["settings"]:
            raise InvoiceRevisionError(
                "Organization invoice settings changed after review. No invoice was created."
            )
        org_id = organization["organization_id"]

        # Fresh reads of every dependency, again, immediately before the write.
        contact = get_customer(access_token, vault, payload["customer_id"])
        if create_customer_evidence(
            contact, organization, payload["customer_name"]
        ) != evidence["customer"]:
            raise InvoiceRevisionError(
                f"Customer {payload['customer_id']} changed after review. No invoice was created."
            )
        if evidence["addresses"]:
            index = address_index(
                get_customer_addresses(access_token, vault, payload["customer_id"]), contact
            )
            for field, record in sorted(evidence["addresses"].items()):
                address_id = str(record.get("address_id") or "")
                if index.get(address_id) != record:
                    raise InvoiceRevisionError(
                        f"The approved {field} {address_id} is no longer the same address owned by "
                        f"customer {payload['customer_id']}. No invoice was created."
                    )
        stated_names = {line["item_id"]: line["item_name"] for line in payload["lines"]}
        for item_id, record in sorted(evidence["items"].items()):
            if item_evidence(
                get_item(access_token, vault, item_id), stated_names[item_id]
            ) != record:
                raise InvoiceRevisionError(
                    f"Item {item_id} changed after review. No invoice was created."
                )
        if evidence["taxes"]:
            live_taxes = resolve_create_taxes(
                access_token, vault, sorted(evidence["taxes"])
            )
            for tax_id, record in sorted(evidence["taxes"].items()):
                if live_taxes.get(tax_id) != record:
                    raise InvoiceRevisionError(
                        f"Tax {tax_id} changed after review. No invoice was created."
                    )

        write_attempted = True
        result = oauth_invoice_create_write_allowed(
            access_token,
            str(vault["api_domain"]),
            "POST",
            INVOICE_COLLECTION_PATH,
            org_id,
            evidence["post_payload"],
        )
        created = result.get("invoice")
        if not isinstance(created, dict):
            raise InvoiceRevisionError(
                "Zoho accepted the creation but returned no invoice record, so the new invoice ID "
                "is UNKNOWN."
            )
        invoice_id = positive_id(created.get("invoice_id"), "created invoice_id")
        verified = get_invoice(access_token, vault, invoice_id)
        invoice_number = verify_created_invoice(verified, evidence)
        zoho_tool.save_vault(vault)
    except Exception as exc:
        status = "indeterminate" if write_attempted else "aborted_before_write"
        write_lock(lock, {
            "plan_sha256": plan["sha256"],
            "action": CREATE_ACTION,
            "status": status,
            "invoice_id": invoice_id,
            "plan_locked_indeterminate": write_attempted,
            "write_attempted": write_attempted,
            "updated_utc": utc_now().isoformat(),
            "reason": str(exc)[:2000],
            "no_retry": True,
        })
        zoho_tool.append_receipt(
            f"zoho_books_{CREATE_ACTION}_{status}_no_retry",
            f"status={status}; customer={payload['customer_name']} ({payload['customer_id']}); "
            f"invoice_id={invoice_id or 'unknown'}; write_attempted={str(write_attempted).lower()}; "
            f"plan={plan_path}; sha256={plan['sha256']}; email_sent=false",
        )
        raise InvoiceRevisionError(
            f"Draft-invoice creation is {status} and this plan is permanently locked against "
            f"retry. Customer: {payload['customer_name']} ({payload['customer_id']}). "
            + (
                "A POST was ISSUED. New invoice ID: "
                + (invoice_id or "UNKNOWN -- Zoho did not return one")
                + ". Its live state is unconfirmed; NOTHING was cleaned up, deleted, voided or "
                "changed, and nothing will be retried."
                if write_attempted else "No POST was issued and no invoice exists."
            )
            + f" No email was sent; this tool has no mail transport. Reason: {exc} "
            "Read the live invoice list in Zoho and reconcile before staging anything new."
        ) from exc
    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": CREATE_ACTION,
        "status": "committed_verified",
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "updated_utc": utc_now().isoformat(),
        "no_retry": True,
    })
    zoho_tool.append_receipt(
        f"zoho_books_{CREATE_ACTION}_committed_verified",
        f"invoice={invoice_number} ({invoice_id}); status=draft; "
        f"customer={payload['customer_name']} ({payload['customer_id']}); "
        f"lines={len(evidence['lines'])}; plan={plan_path}; sha256={plan['sha256']}; "
        "email_sent=false",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "action": CREATE_ACTION,
        "plan_sha256": plan["sha256"],
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "invoice_number_assigned_by": "Zoho auto-numbering",
        "invoice_status_verified": DRAFT_STATUS,
        "customer_id": payload["customer_id"],
        "customer_name": payload["customer_name"],
        "currency_code": evidence["customer"]["currency_code"],
        "lines_created": len(evidence["lines"]),
        "totals_verified": evidence["totals"]["verified"],
        "email_sent": False,
        "atomic": True,
        "replay_locked": True,
    }, ensure_ascii=False, indent=2))


def command_commit(args: argparse.Namespace) -> None:
    plan_path = contained_plan(args.plan)
    plan, evidence = load_plan(plan_path)
    # Approval is checked before the lock, the vault, the token, and the network.
    require_rachad_approval(args.approval)
    if plan["action"] == CREATE_ACTION:
        commit_create(plan, evidence, plan_path)
        return
    invoice_row = evidence["invoice"]
    invoice_id = invoice_row["invoice_id"]
    lock = lock_path(plan["sha256"])
    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": ACTION,
        "status": "in_flight",
        "invoice_id": invoice_id,
        "started_utc": utc_now().isoformat(),
    }, exclusive=True)
    write_attempted = False
    try:
        vault = zoho_tool.load_vault()
        if UPDATE_SCOPE not in (vault.get("scopes") or []):
            raise InvoiceRevisionError(
                f"Saved Zoho connection lacks {UPDATE_SCOPE}. Run PREPARE_DADO_ZOHO_ACCESS.bat, "
                "create the grant in the Zoho API Console, then REAUTHORIZE_DADO_ZOHO.bat."
            )
        access_token, vault = zoho_tool.refresh_access_token(vault)
        organization = verified_organization(access_token, vault)
        if organization != plan["organization"]:
            raise InvoiceRevisionError(
                "REFUSED: the live FRP Depot Books organization does not match the organization "
                "recorded in the plan."
            )
        org_id = organization["organization_id"]

        current = get_invoice(access_token, vault, invoice_id)
        if current != invoice_row["before_state"] or not secrets.compare_digest(
            digest_for(current), str(invoice_row["before_state_sha256"])
        ):
            raise InvoiceRevisionError(
                f"Invoice {invoice_row['invoice_number']} changed after review. No PUT was issued."
            )
        dependency_state(current)

        if evidence["customer"] is not None:
            customer_id = evidence["customer"]["customer_id"]
            live_contact = get_customer(access_token, vault, customer_id)
            if customer_evidence(live_contact, current) != evidence["customer"]:
                raise InvoiceRevisionError(
                    f"Customer {customer_id} changed after review. No PUT was issued."
                )
        if evidence["addresses"]:
            owner = (
                evidence["header_changes"]["customer_id"]["new"]
                if "customer_id" in evidence["header_changes"]
                else invoice_row["customer_id"]
            )
            contact = get_customer(access_token, vault, owner)
            index = address_index(get_customer_addresses(access_token, vault, owner), contact)
            for field, record in evidence["addresses"].items():
                address_id = str(record.get("address_id") or "")
                if index.get(address_id) != record:
                    raise InvoiceRevisionError(
                        f"The approved {field} {address_id} is no longer the same address owned by "
                        f"customer {owner}. No PUT was issued."
                    )
        if evidence["taxes"]:
            live_taxes = tax_index(get_taxes(access_token, vault))
            for tax_id, record in evidence["taxes"].items():
                if live_taxes.get(tax_id) != record:
                    raise InvoiceRevisionError(
                        f"Tax {tax_id} changed after review. No PUT was issued."
                    )

        write_attempted = True
        oauth_invoice_revision_write_allowed(
            access_token,
            str(vault["api_domain"]),
            "PUT",
            f"/books/v3/invoices/{invoice_id}",
            org_id,
            evidence["put_payload"],
        )
        verified = get_invoice(access_token, vault, invoice_id)
        verify_identity_and_status(verified, invoice_row)
        verify_dependencies_unchanged(verified, evidence["dependencies"])
        verify_header_changes(verified, evidence)
        verify_lines(verified, evidence)
        verify_totals(verified, evidence)
        verify_protected_unchanged(verified, invoice_row, evidence["unprotected_keys"])
        zoho_tool.save_vault(vault)
    except Exception as exc:
        status = "indeterminate" if write_attempted else "aborted_before_write"
        write_lock(lock, {
            "plan_sha256": plan["sha256"],
            "action": ACTION,
            "status": status,
            "invoice_id": invoice_id,
            "plan_locked_indeterminate": write_attempted,
            "write_attempted": write_attempted,
            "updated_utc": utc_now().isoformat(),
            "reason": str(exc)[:2000],
            "no_retry": True,
        })
        zoho_tool.append_receipt(
            f"zoho_books_{ACTION}_{status}_no_retry",
            f"status={status}; invoice={invoice_row['invoice_number']} ({invoice_id}); "
            f"write_attempted={str(write_attempted).lower()}; plan={plan_path}; "
            f"sha256={plan['sha256']}; email_sent=false",
        )
        raise InvoiceRevisionError(
            f"Invoice revision is {status} and this plan is permanently locked against retry. "
            f"Invoice: {invoice_row['invoice_number']} ({invoice_id}). "
            f"A PUT was {'ISSUED -- the live invoice state is unconfirmed' if write_attempted else 'NOT issued'}. "
            f"No email was sent; this tool has no mail transport. Reason: {exc} "
            "Read the live invoice in Zoho and reconcile before staging anything new."
        ) from exc
    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": ACTION,
        "status": "committed_verified",
        "invoice_id": invoice_id,
        "updated_utc": utc_now().isoformat(),
        "no_retry": True,
    })
    zoho_tool.append_receipt(
        f"zoho_books_{ACTION}_committed_verified",
        f"invoice={invoice_row['invoice_number']} ({invoice_id}); plan={plan_path}; "
        f"sha256={plan['sha256']}; header_changes={len(evidence['header_changes'])}; "
        f"line_changes={len(evidence['line_changes'])}; email_sent=false",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "action": ACTION,
        "plan_sha256": plan["sha256"],
        "invoice_id": invoice_id,
        "invoice_number": invoice_row["invoice_number"],
        "invoice_status_preserved": invoice_row["status"],
        "header_changes": {
            field: change["new"] for field, change in evidence["header_changes"].items()
        },
        "lines_preserved": len(evidence["lines"]),
        "lines_changed": len(evidence["line_changes"]),
        "email_sent": False,
        "atomic": True,
        "replay_locked": True,
    }, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--input", required=True)
    stage.set_defaults(func=command_stage)
    stage_create = commands.add_parser("stage-create")
    stage_create.add_argument("--input", required=True)
    stage_create.set_defaults(func=command_stage_create)
    commit = commands.add_parser("commit")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (InvoiceRevisionError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
