#!/usr/bin/env python
"""FRP Depot Zoho Draft Purchase Order Tool.

Commissioned by Rachad Homsi on 2026-08-13: Dado must be able to prepare a
Purchase Order in Zoho Books that is ready for HIM to review and send. This tool
therefore creates ONE NEW purchase order in exactly Draft status and stops.

The complete write surface is one route and one verb:
    POST /books/v3/purchaseorders        (create, Draft only)
There is no PUT, PATCH or DELETE anywhere in this module, no route that could
submit, approve, mark-issued, receive, bill, pay, void, cancel, restatus,
convert, attach to, template or MAIL a purchase order, no browser path, and no
mail transport of any kind. Zoho's own auto-numbering assigns the PO number; a
caller cannot supply or override it.

*** THE WRITABLE FIELD SET IS PROVEN FROM LIVE FRP DEPOT RECORDS, NOT GUESSED.
On 2026-08-13 every purchase order in the organization was read read-only (six
records, complete pagination). `delivery_date` carries a real value on
PO-00001-R2 (2026-02-28) and PO-00002-R1 (2026-01-15) while
`expected_delivery_date` is empty on all six, so `delivery_date` is the delivery
key this tool sets and `expected_delivery_date` is never written. The same read
proved `reference_number`, `ship_via`, `notes`, `contact_persons` and the line
keys `item_id`, `name`, `description`, `quantity`, `rate`, `unit`, `tax_id`.
DELIBERATELY ABSENT, and stated in every plan rather than guessed:
`terms` and line `description` are returned keys that no live FRP Depot purchase
order populates, so their write acceptance is UNPROVEN -- they are still
accepted here because Rachad commissioned them, and the read-back verification
is strict, so a value Zoho silently ignores locks the plan indeterminate instead
of being reported as landed. There is no vendor-owned ADDRESS id in the proven
contract (`delivery_org_address_id` is an organization address, not a vendor
one), so no address field is writable here at all.

Every business value needs a nonblank explicit source. The vendor must already
exist, be active and be a VENDOR -- a customer record is never treated as one by
inference. Every line must name an existing active Zoho item; there are no
free-text lines and nothing here can create a vendor, an item or a tax.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
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

# Shared owner authority (autonomy programme 2026-08-21, spec A3/A4/A5). A
# purchase order is MONEY work: it keeps the two-step -- stage, then Rachad's
# own unambiguous go to THAT plan, sent AFTER the plan was written. Exact
# APPROVED is no longer REQUIRED ("yes go ahead" to the shown plan counts); the
# plan-before-approval timestamp check runs whenever the message time is
# stated; a failed commit is reported and re-staged (no silent retry, no
# permanent lock). Appending keeps the stdlib ahead of the common folder.
sys.path.append(str(Path(__file__).resolve().parent.parent / "common"))
import owner_authority  # noqa: E402

TOOL_NAME = "FRP Depot Zoho Draft Purchase Order Tool"
TOOL_VERSION = "1.0.0"
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_purchase_order_plans"
LOCK_DIRNAME = ".commit-locks"
PLAN_KIND = "draft_purchase_order_create"
SCHEMA_VERSION = 1
PLAN_LIFETIME_HOURS = 24
# Rachad's ruling: his approval is ONE PLAIN WORD, never a checksum, and it must
# come FROM HIS OWN MESSAGE answering the staged plan. Dado relays it and never
# supplies, types first or infers it.
APPROVAL_WORD = "APPROVED"

PURCHASE_ORDER_CREATE_SCOPE = "ZohoBooks.purchaseorders.CREATE"
# Every scope that would widen this beyond one Draft creation. The tool refuses
# to run at all while the saved connection holds one of them.
# *** ZohoBooks.purchaseorders.UPDATE WAS REMOVED FROM THIS LIST 2026-08-21, AND
# THAT WAS FORCED, NOT PREFERRED. *** Rachad commissioned
# zoho_j26_403_revision_tool.py that day to append two fixed lines to the
# already-emailed PO-00010, which needs the UPDATE scope on the ONE shared saved
# connection. Leaving UPDATE listed here would have made this create-only tool
# refuse to run at all the moment that grant is made -- a silent, total loss of
# the draft-PO capability, discovered at the worst possible time. The choice was
# never "keep both guardrails"; it was "which tool stops working".
# NOTHING ABOUT THIS TOOL'S OWN CONTAINMENT CHANGED, and the scope list was
# always the weaker of the two defences: ALLOWED_METHODS holds only GET and
# POST, CREATE_PATH_RE pins the one create route, require_create_allowed refuses
# any verb that is not POST by name, and there is no PUT, PATCH or DELETE
# transport anywhere in this module. This tool still cannot update, delete,
# void, cancel, submit, approve, receive, bill, pay or mail a purchase order,
# with or without the scope. Every widening scope below is still refused.
FORBIDDEN_PURCHASE_ORDER_SCOPES = (
    "ZohoBooks.purchaseorders.DELETE",
    "ZohoBooks.purchaseorders.ALL",
    "ZohoInventory.purchaseorders.CREATE",
    "ZohoInventory.purchaseorders.UPDATE",
    "ZohoInventory.purchaseorders.DELETE",
    "ZohoInventory.purchaseorders.ALL",
    "ZohoBooks.fullaccess.all",
    "ZohoInventory.fullaccess.all",
)
REAUTHORIZE_STEPS = (
    "Run PREPARE_DADO_ZOHO_ACCESS.bat, create the one-time grant in the Zoho API "
    "Console with the printed scope list, then run REAUTHORIZE_DADO_ZOHO.bat and "
    "CHECK_DADO_ZOHO.bat."
)

CREATE_PATH = "/books/v3/purchaseorders"
CREATE_PATH_RE = re.compile(r"^/books/v3/purchaseorders$")
# The complete bounded read surface. Anything else is refused before it is sent.
READ_PATH_PATTERNS = (
    re.compile(r"^/books/v3/purchaseorders$"),
    re.compile(r"^/books/v3/purchaseorders/[1-9][0-9]*$"),
    re.compile(r"^/books/v3/contacts/[1-9][0-9]*$"),
    re.compile(r"^/books/v3/items/[1-9][0-9]*$"),
    re.compile(r"^/books/v3/settings/taxes$"),
)
ALLOWED_METHODS = ("GET", "POST")

DRAFT_STATUS = "draft"
# States that would mean the created order is already beyond Draft. Verification
# refuses every one of them explicitly rather than only checking for "draft".
NON_DRAFT_STATUSES = (
    "open", "issued", "sent", "approved", "pending_approval", "billed",
    "partially_billed", "closed", "cancelled", "void", "deleted",
)

HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
ID_RE = re.compile(r"^[1-9][0-9]*$")
DATE_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")
CENT = Decimal("0.01")
MAX_TEXT = 2000
MAX_LINES = 200
MAX_QUANTITY = Decimal("1000000")
MAX_RATE = Decimal("10000000")

# Bounded COMPLETE pagination for the duplicate preflight. Every ceiling is a
# refusal when exceeded, never a partial scan reported as clean.
PO_PER_PAGE = 200
PO_MAX_PAGES = 200
PO_MAX_ROWS = 40000
# Zoho's own word for a voided purchase order. A duplicate check ignores these
# and nothing else.
VOID_STATUSES = ("cancelled", "void", "deleted")

# The closed input schema.
INPUT_KEYS = {
    "purpose", "vendor", "date", "delivery_date", "reference_number",
    "ship_via", "notes", "terms", "contact_persons", "line_items",
}
REQUIRED_INPUT_KEYS = {"purpose", "vendor", "date", "line_items"}
VENDOR_KEYS = {"vendor_id", "vendor_name", "source"}
VALUE_KEYS = {"value", "source"}
LINE_KEYS = {"item_id", "quantity", "rate", "description", "tax_id"}
REQUIRED_LINE_KEYS = {"item_id", "quantity", "rate"}
# Header keys this tool may put in the POST body, all proven above. There is no
# purchaseorder_number, no currency_id, no exchange_rate, no status, no email
# key and no attachment key -- they are unreachable, not merely unused.
ALLOWED_POST_HEADER_KEYS = (
    "vendor_id", "date", "delivery_date", "reference_number", "ship_via",
    "notes", "terms", "contact_persons",
)
REQUIRED_POST_KEYS = {"vendor_id", "date", "line_items"}
ALLOWED_POST_LINE_KEYS = ("item_id", "name", "description", "quantity", "rate", "unit", "tax_id")
REQUIRED_POST_LINE_KEYS = {"item_id", "name", "quantity", "rate"}
ALLOWED_POST_KEYS = set(ALLOWED_POST_HEADER_KEYS) | {"line_items"}
# Keys a caller might reach for that this tool refuses by name, so the refusal
# message says why instead of only "unknown field".
NAMED_REFUSALS = {
    "purchaseorder_number": "Zoho's own auto-numbering assigns the purchase order number.",
    "status": "This tool creates a Draft and cannot set or change a status.",
    "currency_id": "The vendor's own live currency is preserved and never set.",
    "currency_code": "The vendor's own live currency is preserved and never set.",
    "exchange_rate": "An exchange rate is never set by this tool.",
    "expected_delivery_date": (
        "No live FRP Depot purchase order populates expected_delivery_date; "
        "delivery_date is the proven key."
    ),
    "delivery_org_address_id": (
        "That is an organization address, not a vendor-owned one, and it is outside "
        "this commission."
    ),
    "billing_address_id": "No address field is writable by this tool.",
    "attachment": "This tool cannot attach anything to a purchase order.",
    "documents": "This tool cannot attach anything to a purchase order.",
    "custom_fields": "Custom fields are outside this commission.",
    "discount": "A purchase-order discount is outside this commission.",
    "template_id": "The template is left to Zoho's own default.",
    "salesorder_id": "A purchase order cannot be linked to a sales order here.",
    "is_drop_shipment": "Drop shipment is outside this commission.",
}

RISK_NOTE = (
    "One POST creates ONE NEW Zoho Books Purchase Order in exactly Draft status for the existing "
    "vendor named in this plan. Zoho's own numbering assigns the PO number. The order is NOT "
    "issued, NOT approved, NOT emailed and NOT sent to the vendor -- Rachad reviews and sends it "
    "himself. THIS IS NOT REVERSIBLE FROM HERE: there is no update, delete, void, cancel, "
    "rollback, cleanup or retry route in this tool by design, so a created draft REMAINS even if "
    "it later turns out to be wrong. The plan is locked before the POST, attempted once, and "
    "stays locked on any failure, timeout or indeterminate result."
)
UNPROVEN_FIELD_NOTE = (
    "terms and line description are returned keys that no live FRP Depot purchase order "
    "populates, so Zoho's acceptance of them on create is UNPROVEN. They are sent only when this "
    "plan states them, and the read-back is strict: if Zoho ignores one, this plan locks "
    "indeterminate rather than reporting a value that did not land."
)


class PurchaseOrderToolError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise PurchaseOrderToolError("Zoho returned evidence that is not JSON serializable.") from exc


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PurchaseOrderToolError(f"Input JSON is unreadable: {path}") from exc


def file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PurchaseOrderToolError(f"Input file is unreadable: {path}") from exc


def money_text(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def live_decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise PurchaseOrderToolError(f"{label} is missing or not a number.")
    text = str(value).strip()
    if not text:
        raise PurchaseOrderToolError(f"{label} is blank.")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise PurchaseOrderToolError(f"{label} is not a valid number: {value!r}") from exc
    if not result.is_finite():
        raise PurchaseOrderToolError(f"{label} must be a finite number.")
    return result


def number_json(value: Decimal) -> Any:
    integral = value.to_integral_value()
    return int(integral) if value == integral else float(value)


def parse_plan_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise PurchaseOrderToolError(f"Plan {label} is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PurchaseOrderToolError(f"Plan {label} must include a timezone.")
    return parsed


def require_exact_approval(approval: Any, plan: dict[str, Any], *, lane: Any = None,
                           sent_utc: Any = None) -> owner_authority.OwnerGo:
    """Rachad's own unambiguous go to THIS plan, sent after it was written (A3).

    Until 2026-08-21 this compared the exact string APPROVED. It must still come
    from his own message (Hard Rule 3); staging is not approval and Dado cannot
    supply it. The time of his message (--approval-message-utc) is required and
    must fall after ``created_utc`` and before ``expires_utc``.
    """
    try:
        return owner_authority.require_owner_go_after_plan(
            approval, plan_created_utc=plan.get("created_utc"), plan_expires_utc=plan.get("expires_utc"),
            sent_utc=sent_utc, lane=lane, what="this draft purchase-order plan",
        )
    except owner_authority.OwnerAuthorityRefused as exc:
        raise PurchaseOrderToolError(str(exc)) from exc


def books_organization_id(vault: dict[str, Any]) -> str:
    value = str(vault.get("books_organization_id") or "")
    if not ID_RE.fullmatch(value):
        raise PurchaseOrderToolError(
            "The saved Zoho connection has no FRP Depot Books organization ID."
        )
    return value


def require_purchase_order_scopes(scopes: list[str]) -> None:
    """CREATE present, and every widening scope absent. Refused before any write."""
    zoho_tool.validate_scopes(scopes)
    held = set(scopes)
    widened = sorted(held & set(FORBIDDEN_PURCHASE_ORDER_SCOPES))
    if widened:
        raise PurchaseOrderToolError(
            "REFUSED: the saved Zoho connection holds purchase-order scope(s) this tool was "
            "never commissioned to have: " + ", ".join(widened) + ". No Zoho call was made."
        )
    if PURCHASE_ORDER_CREATE_SCOPE not in held:
        raise PurchaseOrderToolError(
            f"REFUSED: the saved Zoho connection lacks {PURCHASE_ORDER_CREATE_SCOPE}, so no "
            "purchase order can be created. " + REAUTHORIZE_STEPS + " No POST was issued."
        )


# ---------------------------------------------------------------------------
# The closed input schema
# ---------------------------------------------------------------------------


def clean_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise PurchaseOrderToolError(f"REFUSED: {label} must be text.")
    if value != value.strip():
        raise PurchaseOrderToolError(f"REFUSED: {label} must not be padded with whitespace.")
    if not value:
        raise PurchaseOrderToolError(f"REFUSED: {label} must be nonblank.")
    if len(value) > MAX_TEXT:
        raise PurchaseOrderToolError(f"REFUSED: {label} is longer than {MAX_TEXT} characters.")
    return value


def value_entry(raw: Any, label: str) -> dict[str, Any]:
    """Every business value is {value, source} with a nonblank explicit source."""
    if not isinstance(raw, dict) or set(raw) != VALUE_KEYS:
        raise PurchaseOrderToolError(
            f"REFUSED: {label} must be exactly {{\"value\": ..., \"source\": \"...\"}}."
        )
    source = raw.get("source")
    if not isinstance(source, str) or not source.strip():
        raise PurchaseOrderToolError(f"REFUSED: {label} needs a nonblank explicit source.")
    if len(source) > MAX_TEXT:
        raise PurchaseOrderToolError(f"REFUSED: {label} source is longer than {MAX_TEXT} characters.")
    return {"value": raw["value"], "source": source.strip()}


def refuse_named_fields(keys: Any, label: str) -> None:
    for key in sorted(set(keys) & set(NAMED_REFUSALS)):
        raise PurchaseOrderToolError(
            f"REFUSED: {label} names {key}. {NAMED_REFUSALS[key]}"
        )


def purchase_order_intent(raw: Any) -> dict[str, Any]:
    """The closed input schema, normalized once so stage and commit agree."""
    if not isinstance(raw, dict):
        raise PurchaseOrderToolError("REFUSED: the purchase-order input must be one JSON object.")
    refuse_named_fields(raw, "the purchase-order input")
    unknown = sorted(set(raw) - INPUT_KEYS)
    if unknown:
        raise PurchaseOrderToolError(
            "REFUSED: the purchase-order input names uncommissioned field(s): "
            + ", ".join(unknown) + ". The exact schema is: " + ", ".join(sorted(INPUT_KEYS)) + "."
        )
    missing = sorted(REQUIRED_INPUT_KEYS - set(raw))
    if missing:
        raise PurchaseOrderToolError(
            "REFUSED: the purchase-order input is missing " + ", ".join(missing) + "."
        )
    purpose = clean_text(raw.get("purpose"), "purpose")
    vendor_raw = raw.get("vendor")
    if not isinstance(vendor_raw, dict) or set(vendor_raw) != VENDOR_KEYS:
        raise PurchaseOrderToolError(
            "REFUSED: vendor must be exactly {\"vendor_id\": \"...\", \"vendor_name\": \"...\", "
            "\"source\": \"...\"}."
        )
    vendor_id = clean_text(vendor_raw.get("vendor_id"), "vendor.vendor_id")
    if not ID_RE.fullmatch(vendor_id):
        raise PurchaseOrderToolError(
            "REFUSED: vendor.vendor_id must be a positive numeric Zoho contact ID of an EXISTING "
            "vendor. This tool never creates a vendor."
        )
    vendor = {
        "vendor_id": vendor_id,
        "vendor_name": clean_text(vendor_raw.get("vendor_name"), "vendor.vendor_name"),
        "source": clean_text(vendor_raw.get("source"), "vendor.source"),
    }
    header: dict[str, Any] = {}
    for field in ("date", "delivery_date", "reference_number", "ship_via", "notes", "terms"):
        if field not in raw:
            continue
        entry = value_entry(raw[field], field)
        text = clean_text(entry["value"], f"{field}.value")
        if field in ("date", "delivery_date") and not DATE_RE.fullmatch(text):
            raise PurchaseOrderToolError(
                f"REFUSED: {field} must be an exact YYYY-MM-DD calendar date."
            )
        header[field] = {"value": text, "source": entry["source"]}
    if "date" not in header:
        raise PurchaseOrderToolError("REFUSED: date is required.")
    if "delivery_date" in header and header["delivery_date"]["value"] < header["date"]["value"]:
        raise PurchaseOrderToolError(
            "REFUSED: delivery_date is earlier than the purchase order date."
        )
    contact_persons: dict[str, Any] | None = None
    if "contact_persons" in raw:
        entry = value_entry(raw["contact_persons"], "contact_persons")
        ids = entry["value"]
        if not isinstance(ids, list) or not ids or len(ids) > 20:
            raise PurchaseOrderToolError(
                "REFUSED: contact_persons.value must be a list of 1 to 20 vendor-owned contact "
                "person IDs."
            )
        cleaned: list[str] = []
        for index, value in enumerate(ids):
            text = clean_text(value, f"contact_persons.value[{index}]")
            if not ID_RE.fullmatch(text):
                raise PurchaseOrderToolError(
                    f"REFUSED: contact_persons.value[{index}] must be a positive numeric ID."
                )
            if text in cleaned:
                raise PurchaseOrderToolError(
                    f"REFUSED: contact_persons names {text} twice."
                )
            cleaned.append(text)
        contact_persons = {"value": cleaned, "source": entry["source"]}
    lines_raw = raw.get("line_items")
    if not isinstance(lines_raw, list) or not lines_raw:
        raise PurchaseOrderToolError("REFUSED: line_items must name at least one existing item.")
    if len(lines_raw) > MAX_LINES:
        raise PurchaseOrderToolError(f"REFUSED: at most {MAX_LINES} lines are accepted.")
    lines: list[dict[str, Any]] = []
    for index, entry in enumerate(lines_raw):
        label = f"line_items[{index}]"
        if not isinstance(entry, dict):
            raise PurchaseOrderToolError(f"REFUSED: {label} must be an object.")
        refuse_named_fields(entry, label)
        unknown = sorted(set(entry) - LINE_KEYS)
        if unknown:
            raise PurchaseOrderToolError(
                f"REFUSED: {label} names uncommissioned field(s): " + ", ".join(unknown)
                + ". A line may carry only: " + ", ".join(sorted(LINE_KEYS)) + "."
            )
        missing = sorted(REQUIRED_LINE_KEYS - set(entry))
        if missing:
            raise PurchaseOrderToolError(f"REFUSED: {label} is missing " + ", ".join(missing) + ".")
        item_entry = value_entry(entry["item_id"], f"{label}.item_id")
        item_id = clean_text(item_entry["value"], f"{label}.item_id.value")
        if not ID_RE.fullmatch(item_id):
            raise PurchaseOrderToolError(
                f"REFUSED: {label}.item_id must be a positive numeric Zoho item ID. There is no "
                "free-text or unlinked purchase-order line in this tool."
            )
        quantity_entry = value_entry(entry["quantity"], f"{label}.quantity")
        quantity = live_decimal(quantity_entry["value"], f"{label}.quantity.value")
        if quantity <= 0 or quantity > MAX_QUANTITY:
            raise PurchaseOrderToolError(
                f"REFUSED: {label}.quantity must be greater than 0 and at most {MAX_QUANTITY}."
            )
        rate_entry = value_entry(entry["rate"], f"{label}.rate")
        rate = live_decimal(rate_entry["value"], f"{label}.rate.value")
        if rate < 0 or rate > MAX_RATE:
            raise PurchaseOrderToolError(
                f"REFUSED: {label}.rate must be 0 or more and at most {MAX_RATE}."
            )
        if -rate.as_tuple().exponent > 6:
            raise PurchaseOrderToolError(f"REFUSED: {label}.rate carries more than six decimals.")
        line: dict[str, Any] = {
            "item_id": {"value": item_id, "source": item_entry["source"]},
            "quantity": {"value": format(quantity, "f"), "source": quantity_entry["source"]},
            "rate": {"value": format(rate, "f"), "source": rate_entry["source"]},
        }
        if "description" in entry:
            description_entry = value_entry(entry["description"], f"{label}.description")
            line["description"] = {
                "value": clean_text(description_entry["value"], f"{label}.description.value"),
                "source": description_entry["source"],
            }
        if "tax_id" in entry:
            tax_entry = value_entry(entry["tax_id"], f"{label}.tax_id")
            tax_id = clean_text(tax_entry["value"], f"{label}.tax_id.value")
            if not ID_RE.fullmatch(tax_id):
                raise PurchaseOrderToolError(
                    f"REFUSED: {label}.tax_id must be a positive numeric Zoho tax ID."
                )
            line["tax_id"] = {"value": tax_id, "source": tax_entry["source"]}
        lines.append(line)
    intent = {
        "purpose": purpose,
        "vendor": vendor,
        "header": header,
        "line_items": lines,
    }
    if contact_persons is not None:
        intent["contact_persons"] = contact_persons
    return intent


# ---------------------------------------------------------------------------
# Read-only Zoho access, all through zoho_tool's GET-only helper
# ---------------------------------------------------------------------------


def require_read_path(path: str) -> str:
    if not any(pattern.fullmatch(path.split("?", 1)[0]) for pattern in READ_PATH_PATTERNS):
        raise PurchaseOrderToolError(
            f"REFUSED: {path.split('?', 1)[0]} is not one of this tool's bounded read routes."
        )
    return path


def api_get(access_token: str, vault: dict[str, Any], path: str, query: dict[str, Any]) -> dict[str, Any]:
    """The ONE read path in this module: GET only, route-allowlisted."""
    require_read_path(path)
    parameters = dict(query)
    parameters["organization_id"] = books_organization_id(vault)
    return zoho_tool.api_get(
        access_token, str(vault["api_domain"]), f"{path}?{urlencode(parameters)}"
    )


def get_vendor(access_token: str, vault: dict[str, Any], vendor_id: str) -> dict[str, Any]:
    result = api_get(access_token, vault, f"/books/v3/contacts/{vendor_id}", {})
    contact = result.get("contact")
    if not isinstance(contact, dict) or str(contact.get("contact_id") or "") != vendor_id:
        raise PurchaseOrderToolError(f"Zoho returned no contact record for {vendor_id}.")
    return json_copy(contact)


def get_item(access_token: str, vault: dict[str, Any], item_id: str) -> dict[str, Any]:
    result = api_get(access_token, vault, f"/books/v3/items/{item_id}", {})
    item = result.get("item")
    if not isinstance(item, dict) or str(item.get("item_id") or "") != item_id:
        raise PurchaseOrderToolError(f"Zoho returned no item record for {item_id}.")
    return json_copy(item)


def get_purchase_order(
    access_token: str, vault: dict[str, Any], purchaseorder_id: str
) -> dict[str, Any]:
    result = api_get(access_token, vault, f"/books/v3/purchaseorders/{purchaseorder_id}", {})
    order = result.get("purchaseorder")
    if not isinstance(order, dict) or str(order.get("purchaseorder_id") or "") != purchaseorder_id:
        raise PurchaseOrderToolError(
            f"Zoho returned no purchase order record for {purchaseorder_id}."
        )
    return json_copy(order)


def get_active_taxes(access_token: str, vault: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = api_get(access_token, vault, "/books/v3/settings/taxes", {})
    rows = result.get("taxes")
    if not isinstance(rows, list):
        raise PurchaseOrderToolError("Zoho returned no readable tax list. Nothing staged.")
    taxes: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise PurchaseOrderToolError("Zoho returned an unreadable tax row. Nothing staged.")
        tax_id = str(row.get("tax_id") or "")
        if not ID_RE.fullmatch(tax_id):
            continue
        taxes[tax_id] = {
            "tax_id": tax_id,
            "tax_name": str(row.get("tax_name") or ""),
            "tax_percentage": format(
                live_decimal(row.get("tax_percentage"), f"tax {tax_id} percentage"), "f"
            ),
            "tax_type": str(row.get("tax_type") or ""),
            "status": str(row.get("status") or ""),
            "is_inactive": bool(row.get("is_inactive")),
        }
    return taxes


def enumerate_all_purchase_orders(
    access_token: str, vault: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """EVERY purchase order in the organization, or a refusal. No sampling path.

    Zoho's own page_context.has_more_page is the termination proof; if it is
    absent, not a boolean, or answers a page other than the one asked for, the
    walk cannot be shown to be complete, so this refuses rather than returning
    what it happened to see.
    """
    rows: list[dict[str, Any]] = []
    page = 1
    pages = 0
    while True:
        if pages >= PO_MAX_PAGES:
            raise PurchaseOrderToolError(
                f"REFUSED: the purchase-order list exceeded the {PO_MAX_PAGES}-page ceiling, so a "
                "complete duplicate check could not be proven. Nothing staged."
            )
        result = api_get(
            access_token, vault, "/books/v3/purchaseorders",
            {"page": page, "per_page": PO_PER_PAGE},
        )
        batch = result.get("purchaseorders")
        if not isinstance(batch, list):
            raise PurchaseOrderToolError(
                f"REFUSED: Zoho returned no readable purchase-order list on page {page}. A "
                "complete duplicate check is not possible. Nothing staged."
            )
        for row in batch:
            if not isinstance(row, dict):
                raise PurchaseOrderToolError(
                    f"REFUSED: purchase-order page {page} carries an unreadable row. Nothing staged."
                )
            rows.append(json_copy(row))
            if len(rows) > PO_MAX_ROWS:
                raise PurchaseOrderToolError(
                    f"REFUSED: the purchase-order list exceeded the {PO_MAX_ROWS}-row ceiling, so "
                    "a complete duplicate check could not be proven. Nothing staged."
                )
        pages += 1
        context = result.get("page_context")
        if not isinstance(context, dict):
            raise PurchaseOrderToolError(
                f"REFUSED: Zoho returned no page context on purchase-order page {page}, so the "
                "walk cannot be proven complete. Nothing staged."
            )
        has_more = context.get("has_more_page")
        if not isinstance(has_more, bool):
            raise PurchaseOrderToolError(
                f"REFUSED: Zoho did not state has_more_page on purchase-order page {page}, so the "
                "walk cannot be proven complete. Nothing staged."
            )
        try:
            reported_page = int(context.get("page"))
        except (TypeError, ValueError) as exc:
            raise PurchaseOrderToolError(
                f"REFUSED: Zoho returned an unreadable page number on purchase-order page {page}."
            ) from exc
        if reported_page != page:
            raise PurchaseOrderToolError(
                f"REFUSED: Zoho answered purchase-order page {reported_page} when page {page} was "
                "asked for. The walk cannot be proven complete. Nothing staged."
            )
        if not has_more:
            break
        page += 1
    return rows, {
        "pages": pages,
        "per_page": PO_PER_PAGE,
        "enumerated": len(rows),
        "filtered": False,
        "complete": True,
    }


# ---------------------------------------------------------------------------
# Vendor, item, tax and duplicate preflight
# ---------------------------------------------------------------------------


def normalized_reference(value: Any) -> str:
    """Conservative normalization: case and whitespace only.

    Punctuation is deliberately NOT stripped -- "PO 1-2" and "PO 12" are
    different references and must not collide.
    """
    return " ".join(str(value or "").split()).casefold()


def vendor_evidence(contact: dict[str, Any], vendor: dict[str, Any]) -> dict[str, Any]:
    """The vendor must already exist, be active, and BE a vendor."""
    contact_type = str(contact.get("contact_type") or "")
    if contact_type != "vendor":
        raise PurchaseOrderToolError(
            f"REFUSED: Zoho contact {vendor['vendor_id']} is a {contact_type or 'unknown'} record, "
            "not a vendor. A customer is never treated as a vendor by inference, and this tool "
            "cannot create or convert one. Nothing staged."
        )
    status = str(contact.get("status") or "")
    if status != "active":
        raise PurchaseOrderToolError(
            f"REFUSED: vendor {vendor['vendor_id']} is {status or 'unknown'}, not active. Nothing staged."
        )
    names = {
        str(contact.get("contact_name") or "").strip(),
        str(contact.get("company_name") or "").strip(),
    }
    if vendor["vendor_name"] not in names:
        raise PurchaseOrderToolError(
            f"REFUSED: Zoho vendor {vendor['vendor_id']} is named "
            f"{str(contact.get('contact_name') or '')!r}, not the stated "
            f"{vendor['vendor_name']!r}. Nothing staged."
        )
    currency = str(contact.get("currency_code") or "")
    if not currency:
        raise PurchaseOrderToolError(
            f"REFUSED: vendor {vendor['vendor_id']} has no live currency. Nothing staged."
        )
    persons = []
    for person in contact.get("contact_persons") or []:
        if isinstance(person, dict) and ID_RE.fullmatch(str(person.get("contact_person_id") or "")):
            persons.append(str(person["contact_person_id"]))
    return {
        "vendor_id": vendor["vendor_id"],
        "contact_name": str(contact.get("contact_name") or ""),
        "company_name": str(contact.get("company_name") or ""),
        "contact_type": contact_type,
        "status": status,
        "currency_code": currency,
        "currency_id": str(contact.get("currency_id") or ""),
        "payment_terms_label": str(contact.get("payment_terms_label") or ""),
        "contact_person_ids": sorted(persons),
    }


def require_vendor_evidence(vendor: Any, stated: dict[str, Any]) -> None:
    """The recorded vendor projection, re-checked wherever it comes from."""
    if not isinstance(vendor, dict):
        raise PurchaseOrderToolError("Plan vendor evidence is invalid.")
    expected = {
        "vendor_id", "contact_name", "company_name", "contact_type", "status",
        "currency_code", "currency_id", "payment_terms_label", "contact_person_ids",
    }
    if set(vendor) != expected:
        raise PurchaseOrderToolError("Plan vendor evidence is not the exact closed schema.")
    if str(vendor.get("vendor_id") or "") != stated["vendor_id"]:
        raise PurchaseOrderToolError("Plan vendor evidence names a different vendor.")
    if vendor.get("contact_type") != "vendor":
        raise PurchaseOrderToolError(
            f"REFUSED: the recorded contact is a {vendor.get('contact_type') or 'unknown'} record, "
            "not a vendor. A customer is never treated as a vendor by inference."
        )
    if vendor.get("status") != "active":
        raise PurchaseOrderToolError("REFUSED: the recorded vendor is not active.")
    if stated["vendor_name"] not in {
        str(vendor.get("contact_name") or "").strip(),
        str(vendor.get("company_name") or "").strip(),
    }:
        raise PurchaseOrderToolError("REFUSED: the recorded vendor name is not the stated one.")
    if not str(vendor.get("currency_code") or "").strip():
        raise PurchaseOrderToolError("REFUSED: the recorded vendor has no currency.")


def require_item_evidence(item: Any, item_id: str) -> None:
    if not isinstance(item, dict) or str(item.get("item_id") or "") != item_id:
        raise PurchaseOrderToolError(f"Plan item evidence for {item_id} is invalid.")
    if item.get("status") != "active":
        raise PurchaseOrderToolError(f"REFUSED: the recorded item {item_id} is not active.")
    if not str(item.get("name") or "").strip():
        raise PurchaseOrderToolError(f"REFUSED: the recorded item {item_id} has no name.")


def item_evidence(item: dict[str, Any], item_id: str) -> dict[str, Any]:
    status = str(item.get("status") or "")
    if status != "active":
        raise PurchaseOrderToolError(
            f"REFUSED: Zoho item {item_id} is {status or 'unknown'}, not active. Nothing staged."
        )
    name = str(item.get("name") or "").strip()
    if not name:
        raise PurchaseOrderToolError(f"REFUSED: Zoho item {item_id} has no name. Nothing staged.")
    return {
        "item_id": item_id,
        "name": name,
        "sku": str(item.get("sku") or ""),
        "status": status,
        "unit": str(item.get("unit") or ""),
        "purchase_rate": format(
            live_decimal(item.get("purchase_rate", 0), f"item {item_id} purchase_rate"), "f"
        ),
        "item_type": str(item.get("item_type") or ""),
    }


def tax_evidence(taxes: dict[str, dict[str, Any]], tax_ids: list[str]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for tax_id in sorted(set(tax_ids)):
        row = taxes.get(tax_id)
        if row is None:
            raise PurchaseOrderToolError(
                f"REFUSED: tax {tax_id} is not an active tax in this Zoho organization. This tool "
                "cannot create a tax. Nothing staged."
            )
        if row["is_inactive"] or row["status"].casefold() != "active":
            raise PurchaseOrderToolError(f"REFUSED: tax {tax_id} is not active. Nothing staged.")
        evidence[tax_id] = json_copy(row)
    return evidence


def scan_for_duplicate(
    rows: list[dict[str, Any]], totals: dict[str, Any], intent: dict[str, Any]
) -> dict[str, Any]:
    """A duplicate is the same vendor plus the same reference, and nothing else.

    A matching TOTAL is never treated as a duplicate: FRP Depot legitimately
    reorders the same basket, and inferring from money alone would block real work.
    """
    reference = normalized_reference(
        intent["header"].get("reference_number", {}).get("value", "")
    )
    vendor_id = intent["vendor"]["vendor_id"]
    matches: list[dict[str, Any]] = []
    same_vendor = 0
    for row in rows:
        if str(row.get("vendor_id") or "") != vendor_id:
            continue
        same_vendor += 1
        status = str(row.get("status") or "").casefold()
        if status in VOID_STATUSES:
            continue
        if not reference:
            continue
        if normalized_reference(row.get("reference_number")) != reference:
            continue
        matches.append({
            "purchaseorder_id": str(row.get("purchaseorder_id") or ""),
            "purchaseorder_number": str(row.get("purchaseorder_number") or ""),
            "status": str(row.get("status") or ""),
            "reference_number": str(row.get("reference_number") or ""),
            "date": str(row.get("date") or ""),
        })
    scan = dict(totals)
    scan.update({
        "vendor_id": vendor_id,
        "vendor_purchase_orders": same_vendor,
        "reference_number": intent["header"].get("reference_number", {}).get("value", ""),
        "normalized_reference": reference,
        "reference_supplied": bool(reference),
        "duplicate_matches": sorted(matches, key=lambda item: item["purchaseorder_id"]),
        "duplicate_match_count": len(matches),
        "totals_are_not_used_for_duplicate_detection": True,
    })
    return scan


def require_no_duplicate(scan: dict[str, Any]) -> None:
    if scan.get("complete") is not True:
        raise PurchaseOrderToolError(
            "REFUSED: the duplicate check is not complete, so a duplicate cannot be ruled out."
        )
    matches = scan.get("duplicate_matches") or []
    if matches:
        listed = ", ".join(
            f"{item['purchaseorder_number']} ({item['purchaseorder_id']}, {item['status']})"
            for item in matches
        )
        raise PurchaseOrderToolError(
            f"REFUSED: vendor {scan['vendor_id']} already has a live purchase order carrying "
            f"reference {scan['reference_number']!r}: {listed}. No duplicate was created."
        )


# ---------------------------------------------------------------------------
# Independent Decimal arithmetic
# ---------------------------------------------------------------------------


def purchase_order_lines(
    intent: dict[str, Any], items: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(intent["line_items"]):
        item_id = line["item_id"]["value"]
        item = items[item_id]
        quantity = Decimal(line["quantity"]["value"])
        rate = Decimal(line["rate"]["value"])
        total = (quantity * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        rows.append({
            "index": index,
            "item_id": item_id,
            "name": item["name"],
            "sku": item["sku"],
            "unit": item["unit"],
            "description": line.get("description", {}).get("value", ""),
            "quantity": format(quantity, "f"),
            "rate": format(rate, "f"),
            "tax_id": line.get("tax_id", {}).get("value", ""),
            "item_total": money_text(total),
        })
    return rows


def purchase_order_totals(
    rows: list[dict[str, Any]], taxes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Line totals, tax per bucket and grand total, half-up, with uncertainty disclosed."""
    zero = Decimal("0")
    sub_total = zero
    buckets: dict[str, Decimal] = {}
    per_line_tax = zero
    uncertain: list[str] = []
    untaxed = 0
    for row in rows:
        net = Decimal(row["item_total"])
        sub_total += net
        tax_id = row["tax_id"]
        if not tax_id:
            untaxed += 1
            continue
        tax_row = taxes.get(tax_id)
        if tax_row is None:
            uncertain.append(f"tax {tax_id} is not in the pinned active tax list")
            continue
        if tax_row["tax_type"] != "tax":
            uncertain.append(
                f"tax {tax_row['tax_name']} ({tax_id}) is a {tax_row['tax_type']}, so its "
                "component rounding is not predictable here"
            )
        percentage = Decimal(tax_row["tax_percentage"])
        buckets[tax_id] = buckets.get(tax_id, zero) + net
        per_line_tax += (net * percentage / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
    tax_rows = []
    bucket_tax = zero
    for tax_id in sorted(buckets):
        tax_row = taxes[tax_id]
        percentage = Decimal(tax_row["tax_percentage"])
        amount = (buckets[tax_id] * percentage / Decimal("100")).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        bucket_tax += amount
        tax_rows.append({
            "tax_id": tax_id,
            "tax_name": tax_row["tax_name"],
            "tax_percentage": tax_row["tax_percentage"],
            "taxable_net": money_text(buckets[tax_id]),
            "tax_amount": money_text(amount),
        })
    if bucket_tax != per_line_tax:
        uncertain.append(
            f"per-bucket tax {money_text(bucket_tax)} and per-line tax {money_text(per_line_tax)} "
            "disagree on rounding"
        )
    exact = not uncertain
    return {
        "sub_total": money_text(sub_total),
        "tax_rows": tax_rows,
        "tax_total": money_text(bucket_tax),
        "total": money_text(sub_total + bucket_tax),
        "untaxed_line_count": untaxed,
        "discount_supported": False,
        "tax_certainty": "exact" if exact else "disclosed_uncertain",
        "tax_uncertainty_reasons": sorted(set(uncertain)),
        "tax_total_asserted": exact,
    }


def purchase_order_payload(
    intent: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """The complete POST body. No number, no currency, no status, no mail key."""
    payload: dict[str, Any] = {"vendor_id": intent["vendor"]["vendor_id"]}
    for field in ("date", "delivery_date", "reference_number", "ship_via", "notes", "terms"):
        if field in intent["header"]:
            payload[field] = intent["header"][field]["value"]
    if "contact_persons" in intent:
        payload["contact_persons"] = list(intent["contact_persons"]["value"])
    post_lines = []
    for row in rows:
        line: dict[str, Any] = {
            "item_id": row["item_id"],
            "name": row["name"],
            "quantity": number_json(Decimal(row["quantity"])),
            "rate": number_json(Decimal(row["rate"])),
        }
        if row["unit"]:
            line["unit"] = row["unit"]
        if row["description"]:
            line["description"] = row["description"]
        if row["tax_id"]:
            line["tax_id"] = row["tax_id"]
        unknown = sorted(set(line) - set(ALLOWED_POST_LINE_KEYS))
        if unknown or not REQUIRED_POST_LINE_KEYS.issubset(line):
            raise PurchaseOrderToolError("The purchase-order line payload is not the exact shape.")
        post_lines.append(line)
    payload["line_items"] = post_lines
    extra = sorted(set(payload) - ALLOWED_POST_KEYS)
    if extra or not REQUIRED_POST_KEYS.issubset(payload):
        raise PurchaseOrderToolError("The purchase-order payload is not the exact commissioned shape.")
    return payload


def build_purchase_order(
    intent: dict[str, Any],
    vendor: dict[str, Any],
    items: dict[str, dict[str, Any]],
    taxes: dict[str, dict[str, Any]],
    duplicate_scan: dict[str, Any],
) -> dict[str, Any]:
    """The whole projection, derived from immutable inputs alone.

    Commit re-runs this over the staged intent and FRESH live evidence and
    refuses unless the result is byte-identical to the reviewed plan.
    """
    # The stored vendor and item evidence are re-checked here, not only where
    # they were read, so a re-signed plan carrying a customer record, an
    # inactive vendor or a retired item is refused by the projection itself.
    require_vendor_evidence(vendor, intent["vendor"])
    for line in intent["line_items"]:
        item_id = line["item_id"]["value"]
        if item_id not in items:
            raise PurchaseOrderToolError(f"Line item {item_id} has no live evidence. Nothing staged.")
        require_item_evidence(items[item_id], item_id)
    if "contact_persons" in intent:
        unknown = sorted(
            set(intent["contact_persons"]["value"]) - set(vendor["contact_person_ids"])
        )
        if unknown:
            raise PurchaseOrderToolError(
                "REFUSED: contact person(s) " + ", ".join(unknown) + " are not owned by vendor "
                f"{vendor['vendor_id']}. Nothing staged."
            )
    rows = purchase_order_lines(intent, items)
    totals = purchase_order_totals(rows, taxes)
    require_no_duplicate(duplicate_scan)
    sources = {
        "purpose": intent["purpose"],
        "vendor": intent["vendor"]["source"],
        **{field: intent["header"][field]["source"] for field in sorted(intent["header"])},
        **(
            {"contact_persons": intent["contact_persons"]["source"]}
            if "contact_persons" in intent else {}
        ),
        "line_items": [
            {
                field: line[field]["source"]
                for field in sorted(line)
            }
            for line in intent["line_items"]
        ],
    }
    return {
        "tool_version": TOOL_VERSION,
        "purpose": intent["purpose"],
        "vendor": vendor,
        "vendor_currency_preserved": vendor["currency_code"],
        "items": {item_id: items[item_id] for item_id in sorted(items)},
        "tax_rows_used": {tax_id: taxes[tax_id] for tax_id in sorted(taxes)},
        "duplicate_preflight": duplicate_scan,
        "lines": rows,
        "totals": totals,
        "sources": sources,
        "unproven_field_note": UNPROVEN_FIELD_NOTE,
        "post_endpoint": f"POST {CREATE_PATH}",
        "post_payload": purchase_order_payload(intent, rows),
        "created_status_required": DRAFT_STATUS,
        "auto_numbered_by_zoho": True,
        "email_sent": False,
    }


# ---------------------------------------------------------------------------
# Plan staging
# ---------------------------------------------------------------------------


def contained_plan(raw_path: Any) -> Path:
    candidate = Path(str(raw_path if raw_path is not None else ""))
    if not candidate.is_absolute():
        raise PurchaseOrderToolError("Plan must be an absolute path inside the exact plan folder.")
    lexical_root = PLAN_DIR.absolute()
    try:
        candidate.absolute().relative_to(lexical_root)
    except ValueError as exc:
        raise PurchaseOrderToolError("Plan is outside the exact allowlisted plan folder.") from exc
    cursor = candidate.absolute()
    while True:
        if cursor.is_symlink():
            raise PurchaseOrderToolError("Plan paths and parents must not be symlinks.")
        if cursor == lexical_root:
            break
        parent = cursor.parent
        if parent == cursor:
            raise PurchaseOrderToolError("Plan is outside the exact allowlisted plan folder.")
        cursor = parent
    try:
        root = PLAN_DIR.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise PurchaseOrderToolError("Plan does not resolve inside the exact plan folder.") from exc
    if root not in resolved.parents or not resolved.is_file() or resolved.suffix.casefold() != ".json":
        raise PurchaseOrderToolError("Plan is outside the exact allowlisted plan folder.")
    return resolved


def lock_path(plan_sha256: str) -> Path:
    if not HEX_64_RE.fullmatch(str(plan_sha256)):
        raise PurchaseOrderToolError("Plan digest is invalid for replay locking.")
    return PLAN_DIR / LOCK_DIRNAME / f"{plan_sha256}.json"


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    """Durable single-use lock. Created BEFORE the POST, never removed after."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError:
        # A4: what the existing record says decides -- spent, or needs re-stage.
        owner_authority.refuse_replay(PurchaseOrderToolError, owner_authority.read_json_if_exists(path),
                                      what="draft purchase-order plan")
        raise  # unreachable
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def stage_plan(
    intent: dict[str, Any], evidence: dict[str, Any], organization_id: str, input_path: Path
) -> Path:
    created = utc_now()
    core = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "kind": PLAN_KIND,
        "schema_version": SCHEMA_VERSION,
        "nonce": secrets.token_hex(16),
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "approval_required": APPROVAL_WORD,
        "books_organization_id": organization_id,
        "risk": {
            "atomic": True,
            "single_post": True,
            "reversible": False,
            "email_sent": False,
            "write_attempted": False,
            "created_status_required": DRAFT_STATUS,
            "note": RISK_NOTE,
        },
        "input": {"path": str(input_path), "sha256": file_digest(input_path)},
        "intent": intent,
        "live_evidence": evidence,
    }
    plan = dict(core)
    plan["sha256"] = digest_for(core)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{PLAN_KIND}_{plan['sha256'][:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt(
        "zoho_draft_purchase_order_plan_staged_read_only",
        f"vendor={evidence['vendor']['contact_name']} ({evidence['vendor']['vendor_id']}); "
        f"lines={len(evidence['lines'])}; total={evidence['totals']['total']} "
        f"{evidence['vendor']['currency_code']}; plan={path}; sha256={plan['sha256']}; "
        "writes=0; method=GET_ONLY",
    )
    return path


def print_summary(plan: dict[str, Any], path: Path) -> None:
    evidence = plan["live_evidence"]
    vendor = evidence["vendor"]
    totals = evidence["totals"]
    payload = evidence["post_payload"]
    currency = vendor["currency_code"]
    print("=" * 78)
    print("STAGED PLAN -- NEW DRAFT PURCHASE ORDER (NOT YET APPROVED, NOT CREATED)")
    print("=" * 78)
    print(f"Purpose       : {evidence['purpose']}")
    print(f"Vendor        : {vendor['contact_name']} ({vendor['vendor_id']}) "
          f"-- {vendor['contact_type']}, {vendor['status']}")
    print(f"Currency      : {currency} (the vendor's own; never set by this tool)")
    print(f"Endpoint      : {evidence['post_endpoint']} (one POST, no retry)")
    print(f"PO number     : assigned by Zoho's own auto-numbering")
    print(f"Created state : {evidence['created_status_required']} "
          f"-- not issued, not approved, not emailed")
    print("-" * 78)
    for field in ("date", "delivery_date", "reference_number", "ship_via", "notes", "terms"):
        if field in payload:
            print(f"{field:16}: {payload[field]}")
    if "contact_persons" in payload:
        print(f"{'contact_persons':16}: {', '.join(payload['contact_persons'])}")
    print("-" * 78)
    print(f"{'#':>2}  {'Item':34} {'Qty':>8} {'Rate':>12} {'Line total':>12}")
    for index, line in enumerate(evidence["lines"], start=1):
        print(
            f"{index:>2}  {line['name'][:33]:33} {line['quantity']:>8} "
            f"{line['rate']:>12} {line['item_total']:>12}"
        )
    print("-" * 78)
    print(f"Sub total                : {currency} {totals['sub_total']}")
    for row in totals["tax_rows"]:
        print(
            f"  tax {row['tax_name']} {row['tax_percentage']}% on {row['taxable_net']}"
            f" = {row['tax_amount']}"
        )
    print(f"Tax total                : {currency} {totals['tax_total']}")
    print(f"Grand total              : {currency} {totals['total']}")
    print(f"Tax prediction           : {totals['tax_certainty']}")
    for reason in totals["tax_uncertainty_reasons"]:
        print(f"  NOT EXACT because {reason}")
    if totals["untaxed_line_count"]:
        print(f"  {totals['untaxed_line_count']} line(s) carry no tax id.")
    scan = evidence["duplicate_preflight"]
    print("-" * 78)
    print(
        f"Duplicate preflight      : {scan['enumerated']} purchase order(s) over "
        f"{scan['pages']} page(s), complete={scan['complete']}; "
        f"{scan['vendor_purchase_orders']} for this vendor; "
        f"{scan['duplicate_match_count']} reference match(es)"
    )
    if not scan["reference_supplied"]:
        print("  No reference number supplied, so a reference duplicate cannot be ruled out.")
        print("  A matching TOTAL is deliberately never treated as a duplicate.")
    print("-" * 78)
    print(f"UNPROVEN FIELDS          : {evidence['unproven_field_note']}")
    print(f"NOT REVERSIBLE           : {plan['risk']['note']}")
    print(f"Email sent               : NO -- this tool has no mail transport")
    print(f"Plan                     : {path}")
    print(f"Plan sha256              : {plan['sha256']}")
    print(f"Expires                  : {plan['expires_utc']}")
    print("-" * 78)
    print(
        f"NO WRITE HAS BEEN MADE. Committing this plan needs Rachad's own one-word\n"
        f"reply {APPROVAL_WORD} to THIS plan (exact uppercase). Dado never supplies it."
    )
    print("=" * 78)


def collect_live_evidence(
    access_token: str, vault: dict[str, Any], intent: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """Every read the projection needs, GET-only and route-allowlisted."""
    vendor = vendor_evidence(
        get_vendor(access_token, vault, intent["vendor"]["vendor_id"]), intent["vendor"]
    )
    items: dict[str, dict[str, Any]] = {}
    for line in intent["line_items"]:
        item_id = line["item_id"]["value"]
        if item_id in items:
            continue
        items[item_id] = item_evidence(get_item(access_token, vault, item_id), item_id)
    tax_ids = [
        line["tax_id"]["value"] for line in intent["line_items"] if "tax_id" in line
    ]
    taxes = tax_evidence(get_active_taxes(access_token, vault), tax_ids) if tax_ids else {}
    rows, totals = enumerate_all_purchase_orders(access_token, vault)
    scan = scan_for_duplicate(rows, totals, intent)
    return vendor, items, taxes, scan


def command_stage_create(args: argparse.Namespace) -> None:
    """GET-only. Refuses before any read if the CREATE scope is not live."""
    input_path = Path(str(args.input))
    intent = purchase_order_intent(read_json(str(input_path)))
    vault = zoho_tool.load_vault()
    require_purchase_order_scopes([str(scope) for scope in vault.get("scopes") or []])
    organization_id = books_organization_id(vault)
    access_token, vault = zoho_tool.refresh_access_token(vault)
    vendor, items, taxes, scan = collect_live_evidence(access_token, vault, intent)
    zoho_tool.save_vault(vault)
    evidence = build_purchase_order(intent, vendor, items, taxes, scan)
    path = stage_plan(intent, evidence, organization_id, input_path)
    print_summary(read_json(str(path)), path)


# ---------------------------------------------------------------------------
# Plan validation
# ---------------------------------------------------------------------------


def validate_plan(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        plan.get("tool") != TOOL_NAME
        or plan.get("tool_version") != TOOL_VERSION
        or plan.get("kind") != PLAN_KIND
        or plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise PurchaseOrderToolError(
            "The plan belongs to a different tool, action, build or schema version."
        )
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise PurchaseOrderToolError("Plan nonce is invalid.")
    created = parse_plan_time(plan.get("created_utc"), "creation time")
    expires = parse_plan_time(plan.get("expires_utc"), "expiry")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise PurchaseOrderToolError("Plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise PurchaseOrderToolError("Plan creation time is in the future.")
    if now >= expires:
        raise PurchaseOrderToolError("Plan expired. Stage a new plan for review.")
    risk = plan.get("risk")
    if not isinstance(risk, dict) or (
        risk.get("atomic") is not True
        or risk.get("single_post") is not True
        or risk.get("reversible") is not False
        or risk.get("email_sent") is not False
        or risk.get("write_attempted") is not False
        or risk.get("created_status_required") != DRAFT_STATUS
        or risk.get("note") != RISK_NOTE
    ):
        raise PurchaseOrderToolError(
            "Plan must disclose the exact single-atomic-POST, not-reversible risk."
        )
    if not ID_RE.fullmatch(str(plan.get("books_organization_id") or "")):
        raise PurchaseOrderToolError("Plan organization ID is invalid.")
    # Re-normalizing through the same closed schema means a hand-edited intent
    # cannot smuggle in a purchase order number, a status, a currency or a
    # free-text line.
    intent = purchase_order_intent(plan_intent_input(plan.get("intent")))
    if intent != plan.get("intent"):
        raise PurchaseOrderToolError("Plan intent is not the canonical normalized form of its own input.")
    evidence = plan.get("live_evidence")
    if not isinstance(evidence, dict):
        raise PurchaseOrderToolError("Plan evidence is invalid.")
    if evidence.get("tool_version") != TOOL_VERSION:
        raise PurchaseOrderToolError("Plan evidence was produced by a different build.")
    rebuilt = build_purchase_order(
        intent,
        evidence.get("vendor") or {},
        evidence.get("items") or {},
        evidence.get("tax_rows_used") or {},
        evidence.get("duplicate_preflight") or {},
    )
    if rebuilt != evidence:
        raise PurchaseOrderToolError(
            "Plan evidence is not the canonical projection of the staged inputs and live evidence."
        )
    if evidence["post_endpoint"] != f"POST {CREATE_PATH}":
        raise PurchaseOrderToolError("Plan endpoint is not the one commissioned create route.")
    return intent, evidence


def plan_intent_input(intent: Any) -> Any:
    """Turn a stored normalized intent back into raw input shape for re-validation."""
    if not isinstance(intent, dict):
        raise PurchaseOrderToolError("Plan intent is invalid.")
    unknown = sorted(set(intent) - {"purpose", "vendor", "header", "line_items", "contact_persons"})
    if unknown:
        raise PurchaseOrderToolError(
            "Plan intent names uncommissioned section(s): " + ", ".join(unknown)
        )
    header = intent.get("header")
    if not isinstance(header, dict):
        raise PurchaseOrderToolError("Plan intent header is invalid.")
    raw: dict[str, Any] = {
        "purpose": intent.get("purpose"),
        "vendor": intent.get("vendor"),
        "line_items": intent.get("line_items"),
    }
    raw.update(header)
    if "contact_persons" in intent:
        raw["contact_persons"] = intent["contact_persons"]
    return raw


def load_plan(path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan = read_json(str(path))
    if not isinstance(plan, dict):
        raise PurchaseOrderToolError("Plan must contain one object.")
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise PurchaseOrderToolError("Plan hash check failed. The plan changed after review.")
    intent, evidence = validate_plan(plan)
    return plan, intent, evidence


# ---------------------------------------------------------------------------
# The one write path
# ---------------------------------------------------------------------------


def require_create_allowed(
    method: str, path: str, organization_id: str, payload: dict[str, Any],
    expected_payload: dict[str, Any],
) -> None:
    """The complete write allowlist. Pure validation -- it touches nothing.

    Commit runs it once BEFORE the replay lock (so a bad payload is a free
    refusal, not a burned plan) and the write function runs it again as the
    transport's own gate.
    """
    if method != "POST":
        raise PurchaseOrderToolError(
            "REFUSED: creating a draft purchase order is a POST and nothing else. There is no "
            "PUT, PATCH or DELETE verb in this tool."
        )
    if not CREATE_PATH_RE.fullmatch(str(path)):
        raise PurchaseOrderToolError(
            f"REFUSED: the only write route in this tool is {CREATE_PATH}. Update, deletion, "
            "void, cancel, submit, approval, mark-issued, receive, bill, payment, attachment, "
            "template, mail and every bulk route are unreachable."
        )
    if not isinstance(payload, dict) or not isinstance(expected_payload, dict):
        raise PurchaseOrderToolError("REFUSED: the purchase-order POST payload must be an object.")
    extra = sorted(set(payload) - ALLOWED_POST_KEYS)
    if extra:
        raise PurchaseOrderToolError(
            "REFUSED: the purchase-order POST payload names uncommissioned field(s): "
            + ", ".join(extra)
        )
    if not REQUIRED_POST_KEYS.issubset(payload):
        raise PurchaseOrderToolError(
            "REFUSED: the purchase-order POST payload must carry the existing vendor, the date "
            "and at least one item line."
        )
    if not ID_RE.fullmatch(str(payload.get("vendor_id") or "")):
        raise PurchaseOrderToolError("REFUSED: the POST payload names no existing vendor.")
    lines = payload.get("line_items")
    expected_lines = expected_payload.get("line_items")
    if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES:
        raise PurchaseOrderToolError("REFUSED: the POST payload line list is not the reviewed shape.")
    if not isinstance(expected_lines, list) or len(expected_lines) != len(lines):
        raise PurchaseOrderToolError("REFUSED: the reviewed plan payload is not the commissioned shape.")
    for index, (line, reviewed) in enumerate(zip(lines, expected_lines)):
        if not isinstance(line, dict) or not isinstance(reviewed, dict):
            raise PurchaseOrderToolError("REFUSED: every POST line must be an object.")
        unknown = sorted(set(line) - set(ALLOWED_POST_LINE_KEYS))
        if unknown:
            raise PurchaseOrderToolError(
                "REFUSED: a POST line names uncommissioned field(s): " + ", ".join(unknown)
            )
        if not REQUIRED_POST_LINE_KEYS.issubset(line):
            raise PurchaseOrderToolError(
                "REFUSED: every POST line must name an existing Zoho item, its name, a quantity "
                "and a rate."
            )
        if not ID_RE.fullmatch(str(line.get("item_id") or "")):
            raise PurchaseOrderToolError(
                f"REFUSED: POST line {index + 1} is not linked to an existing Zoho item."
            )
        if set(line) != set(reviewed):
            raise PurchaseOrderToolError(
                f"REFUSED: POST line {index + 1} does not carry the reviewed field set."
            )
        for key in line:
            if line[key] != reviewed[key]:
                raise PurchaseOrderToolError(
                    f"REFUSED: POST line {index + 1} {key} does not match the reviewed plan."
                )
    for key in sorted(ALLOWED_POST_KEYS):
        if key == "line_items":
            continue
        if payload.get(key) != expected_payload.get(key):
            raise PurchaseOrderToolError(
                f"REFUSED: the purchase-order POST {key} does not match the reviewed plan."
            )
    if set(payload) != set(expected_payload):
        raise PurchaseOrderToolError("REFUSED: the purchase-order POST is not the reviewed payload.")
    if not ID_RE.fullmatch(str(organization_id)):
        raise PurchaseOrderToolError("REFUSED: the organization ID is invalid.")


def send_create(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
    expected_payload: dict[str, Any],
) -> dict[str, Any]:
    """The ONE write transport in this module. One attempt, no retry.

    The query string is exactly the organization id: there is no send, email,
    status, approve or ignore-auto-number parameter here or anywhere else.
    """
    require_create_allowed(method, path, organization_id, payload, expected_payload)
    request = Request(
        api_domain.rstrip("/") + path + "?" + urlencode({"organization_id": organization_id}),
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PurchaseOrderToolError(
            f"Zoho purchase-order creation failed with HTTP {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise PurchaseOrderToolError(
            f"Zoho purchase-order creation outcome is indeterminate: {exc.reason}"
        ) from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PurchaseOrderToolError(
            "Zoho purchase-order creation returned invalid JSON; the outcome is indeterminate."
        ) from exc
    if not isinstance(result, dict) or result.get("code") != 0:
        message = result.get("message") if isinstance(result, dict) else "invalid response"
        raise PurchaseOrderToolError(
            "Zoho purchase-order creation returned an invalid or unknown result: " + str(message)
        )
    return result


def verify_created(after: Any, evidence: dict[str, Any], label: str, *, full: bool = True) -> str:
    """Prove the live result is exactly a Draft, with the approved content."""
    if not isinstance(after, dict):
        raise PurchaseOrderToolError(f"{label} returned no purchase order record.")
    purchaseorder_id = str(after.get("purchaseorder_id") or "")
    if not ID_RE.fullmatch(purchaseorder_id):
        raise PurchaseOrderToolError(f"{label} carries no usable purchase order ID.")
    status = str(after.get("status") or "")
    if status.casefold() in NON_DRAFT_STATUSES:
        raise PurchaseOrderToolError(
            f"{label} status is {status!r}, which is beyond Draft. Stop and reconcile: this tool "
            "cannot issue, approve, restatus, void or delete a purchase order."
        )
    if status != DRAFT_STATUS:
        raise PurchaseOrderToolError(
            f"{label} status is {status!r}, not exactly {DRAFT_STATUS!r}. Stop and reconcile."
        )
    if "is_emailed" in after and after.get("is_emailed") is not False:
        raise PurchaseOrderToolError(
            f"{label} reports is_emailed {after.get('is_emailed')!r}. Stop and reconcile: this "
            "tool has no mail transport."
        )
    vendor = evidence["vendor"]
    if str(after.get("vendor_id") or "") != vendor["vendor_id"]:
        raise PurchaseOrderToolError(
            f"{label} names vendor {after.get('vendor_id')!r}, not the approved "
            f"{vendor['vendor_id']!r}. Stop and reconcile."
        )
    if "currency_code" in after and str(after.get("currency_code") or "") != vendor["currency_code"]:
        raise PurchaseOrderToolError(
            f"{label} bills in {after.get('currency_code')!r}, not the vendor's own "
            f"{vendor['currency_code']!r}. Stop and reconcile."
        )
    payload = evidence["post_payload"]
    for field in ("date", "delivery_date", "reference_number", "ship_via", "notes", "terms"):
        if field not in payload:
            continue
        actual = after.get(field)
        if actual != payload[field]:
            raise PurchaseOrderToolError(
                f"{label} {field} is {actual!r}, not the approved {payload[field]!r}. Stop and "
                "reconcile."
            )
    lines = after.get("line_items")
    want_lines = evidence["lines"]
    if not isinstance(lines, list) or len(lines) != len(want_lines):
        raise PurchaseOrderToolError(
            f"{label} carries {len(lines) if isinstance(lines, list) else 'no'} lines, not the "
            f"approved {len(want_lines)}. Stop and reconcile."
        )
    for index, (line, want) in enumerate(zip(lines, want_lines)):
        if not isinstance(line, dict):
            raise PurchaseOrderToolError(f"{label} line {index + 1} is not an object.")
        if str(line.get("item_id") or "") != want["item_id"]:
            raise PurchaseOrderToolError(
                f"{label} line {index + 1} is item {line.get('item_id')!r}, not the approved "
                f"{want['item_id']!r}. Stop and reconcile."
            )
        for key in ("quantity", "rate"):
            actual_value = live_decimal(line.get(key), f"{label} line {index + 1} {key}")
            if actual_value != Decimal(want[key]):
                raise PurchaseOrderToolError(
                    f"{label} line {index + 1} {key} is {actual_value}, not the approved "
                    f"{want[key]}. Stop and reconcile."
                )
        if want["tax_id"] and str(line.get("tax_id") or "") != want["tax_id"]:
            raise PurchaseOrderToolError(
                f"{label} line {index + 1} tax_id is {line.get('tax_id')!r}, not the approved "
                f"{want['tax_id']!r}. Stop and reconcile."
            )
        if want["description"] and str(line.get("description") or "") != want["description"]:
            raise PurchaseOrderToolError(
                f"{label} line {index + 1} description is not the approved text. Zoho may have "
                "ignored it. Stop and reconcile."
            )
        if "item_total" in line:
            actual_total = live_decimal(line.get("item_total"), f"{label} line {index + 1} total")
            if actual_total != Decimal(want["item_total"]):
                raise PurchaseOrderToolError(
                    f"{label} line {index + 1} total is {actual_total}, not the approved "
                    f"{want['item_total']}. Stop and reconcile."
                )
    if not full:
        return purchaseorder_id
    totals = evidence["totals"]
    sub_total = live_decimal(after.get("sub_total"), f"{label} sub_total")
    if sub_total != Decimal(totals["sub_total"]):
        raise PurchaseOrderToolError(
            f"{label} sub_total is {sub_total}, not the approved {totals['sub_total']}. Stop and "
            "reconcile."
        )
    tax_total = live_decimal(after.get("tax_total"), f"{label} tax_total")
    total = live_decimal(after.get("total"), f"{label} total")
    if totals["tax_total_asserted"]:
        if tax_total != Decimal(totals["tax_total"]):
            raise PurchaseOrderToolError(
                f"{label} tax_total is {tax_total}, not the approved {totals['tax_total']}. Stop "
                "and reconcile."
            )
        if total != Decimal(totals["total"]):
            raise PurchaseOrderToolError(
                f"{label} total is {total}, not the approved {totals['total']}. Stop and reconcile."
            )
    if total != sub_total + tax_total:
        raise PurchaseOrderToolError(
            f"{label} total {total} is not its own sub_total plus tax_total. Stop and reconcile."
        )
    for key, allowed in (
        ("billed_status", ("unbilled", "")),
        ("received_status", ("pending", "to_be_received", "")),
    ):
        if key in after and str(after.get(key) or "").casefold() not in allowed:
            raise PurchaseOrderToolError(
                f"{label} {key} is {after.get(key)!r}, which is beyond a fresh Draft. Stop and "
                "reconcile."
            )
    for key in ("bills", "approvers_list"):
        value = after.get(key)
        if isinstance(value, list) and value:
            raise PurchaseOrderToolError(
                f"{label} already carries {key}. Stop and reconcile."
            )
    if str(after.get("submitted_by") or "").strip():
        raise PurchaseOrderToolError(f"{label} is already submitted. Stop and reconcile.")
    return purchaseorder_id


def command_commit(args: argparse.Namespace) -> None:
    plan_path = contained_plan(args.plan)
    plan, intent, evidence = load_plan(plan_path)
    # His go is checked before the lock, the vault, the token and the network.
    go = require_exact_approval(
        args.approval, plan, lane=getattr(args, "approval_lane", None),
        sent_utc=getattr(args, "approval_message_utc", None),
    )
    vendor_label = f"{evidence['vendor']['contact_name']} ({evidence['vendor']['vendor_id']})"
    lock = lock_path(plan["sha256"])
    if lock.exists():
        owner_authority.refuse_replay(PurchaseOrderToolError, owner_authority.read_json_if_exists(lock),
                                      what="draft purchase-order plan")
    try:
        vault = zoho_tool.load_vault()
        require_purchase_order_scopes([str(scope) for scope in vault.get("scopes") or []])
        organization_id = books_organization_id(vault)
        if organization_id != str(plan["books_organization_id"]):
            raise PurchaseOrderToolError(
                "REFUSED: the live FRP Depot Books organization does not match the plan."
            )
        access_token, vault = zoho_tool.refresh_access_token(vault)
        # A FRESH complete preflight, not the staged one: a vendor deactivated,
        # an item retired, a tax repriced or a duplicate PO created between
        # staging and approval must still stop this for free.
        fresh_vendor, fresh_items, fresh_taxes, fresh_scan = collect_live_evidence(
            access_token, vault, intent
        )
        fresh_evidence = build_purchase_order(
            intent, fresh_vendor, fresh_items, fresh_taxes, fresh_scan
        )
        for section in ("vendor", "items", "tax_rows_used", "lines", "totals", "post_payload"):
            if fresh_evidence[section] != evidence[section]:
                raise PurchaseOrderToolError(
                    f"The live {section} no longer matches the reviewed plan. No POST was issued "
                    "and this plan is not locked; stage a new plan."
                )
        # The write allowlist runs here too, so a payload it would reject is a
        # free refusal rather than a permanently burned plan.
        require_create_allowed(
            "POST", CREATE_PATH, organization_id,
            evidence["post_payload"], fresh_evidence["post_payload"],
        )
    except Exception as exc:
        zoho_tool.append_receipt(
            "zoho_draft_purchase_order_refused_before_lock",
            f"vendor={vendor_label}; plan={plan_path}; sha256={plan['sha256']}; "
            "write_attempted=false; locked=false; email_sent=false",
        )
        raise PurchaseOrderToolError(
            "The draft purchase order was refused BEFORE any write and BEFORE the replay lock. "
            f"Vendor: {vendor_label}. No POST was issued and no email was sent. Reason: {exc}"
        ) from exc
    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_IN_FLIGHT, plan_sha256=plan["sha256"], action=PLAN_KIND, go=go,
        kind=PLAN_KIND, vendor_id=evidence["vendor"]["vendor_id"], started_utc=utc_now().isoformat(),
    ), exclusive=True)
    write_attempted = False
    purchaseorder_id = ""
    try:
        write_attempted = True
        result = send_create(
            access_token,
            str(vault["api_domain"]),
            "POST",
            CREATE_PATH,
            organization_id,
            evidence["post_payload"],
            fresh_evidence["post_payload"],
        )
        created = result.get("purchaseorder")
        purchaseorder_id = verify_created(created, evidence, "POST response", full=False)
        verified = get_purchase_order(access_token, vault, purchaseorder_id)
        if verify_created(verified, evidence, "Fresh read-back", full=True) != purchaseorder_id:
            raise PurchaseOrderToolError("The fresh read-back returned a different purchase order.")
        purchaseorder_number = str(verified.get("purchaseorder_number") or "")
        if not purchaseorder_number:
            raise PurchaseOrderToolError("Zoho returned no purchase order number.")
        zoho_tool.save_vault(vault)
    except Exception as exc:
        write_lock(lock, owner_authority.attempt_record(
            owner_authority.STATUS_INDETERMINATE, plan_sha256=plan["sha256"], action=PLAN_KIND, go=go,
            reason=str(exc), kind=PLAN_KIND, purchaseorder_id=purchaseorder_id,
            write_attempted=write_attempted,
        ))
        zoho_tool.append_receipt(
            "zoho_draft_purchase_order_indeterminate_needs_restage",
            f"vendor={vendor_label}; purchaseorder_id={purchaseorder_id or 'unknown'}; "
            f"write_attempted={str(write_attempted).lower()}; plan={plan_path}; "
            f"sha256={plan['sha256']}; email_sent=false",
        )
        raise PurchaseOrderToolError(
            owner_authority.explain_outcome(
                "The draft purchase order", owner_authority.STATUS_INDETERMINATE,
                f"Vendor: {vendor_label}. A POST was ISSUED -- the live purchase order list is "
                "unconfirmed. No email was sent; this tool has no mail transport. Nothing was "
                f"deleted, voided, cancelled, cleaned up or attempted a second time. Reason: {exc}",
                money=True,
            )
            + " The re-stage's duplicate walk shows whether the draft landed."
        ) from exc
    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_COMMITTED, plan_sha256=plan["sha256"], action=PLAN_KIND, go=go,
        kind=PLAN_KIND, purchaseorder_id=purchaseorder_id, purchaseorder_number=purchaseorder_number,
    ))
    zoho_tool.append_receipt(
        "zoho_draft_purchase_order_committed_verified",
        f"vendor={vendor_label}; purchaseorder_id={purchaseorder_id}; "
        f"purchaseorder_number={purchaseorder_number}; status={DRAFT_STATUS}; "
        f"total={evidence['totals']['total']} {evidence['vendor']['currency_code']}; "
        f"plan={plan_path}; sha256={plan['sha256']}; email_sent=false",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "kind": PLAN_KIND,
        "purchaseorder_id": purchaseorder_id,
        "purchaseorder_number": purchaseorder_number,
        "purchase_order_status": DRAFT_STATUS,
        "vendor_id": evidence["vendor"]["vendor_id"],
        "vendor_name": evidence["vendor"]["contact_name"],
        "currency_code": evidence["vendor"]["currency_code"],
        "lines": len(evidence["lines"]),
        "sub_total": evidence["totals"]["sub_total"],
        "tax_total": evidence["totals"]["tax_total"],
        "tax_certainty": evidence["totals"]["tax_certainty"],
        "total": evidence["totals"]["total"],
        "auto_numbered_by_zoho": True,
        "issued": False,
        "approved": False,
        "email_sent": False,
        "atomic": True,
        "replay_locked": True,
        "plan_spent": True,
        "approval_message_utc": go.sent_utc or "not stated",
        "plan": str(plan_path),
        "plan_sha256": plan["sha256"],
    }, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage-create")
    stage.add_argument("--input", required=True)
    stage.set_defaults(func=command_stage_create)
    commit = commands.add_parser("commit")
    commit.add_argument("--plan", required=True)
    owner_authority.add_owner_go_arguments(commit, money=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (PurchaseOrderToolError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
