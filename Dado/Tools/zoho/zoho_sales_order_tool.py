#!/usr/bin/env python
"""FRP Depot Zoho Books SCT PO26330 draft Sales Order tool.

Commissioned by Rachad Homsi on 2026-08-11 for ONE fixed transaction and
nothing else. Building, testing, or staging with this module is NOT approval
of a business write.

THE ONE TRANSACTION
-------------------
Create ONE new Zoho Books Sales Order, in exactly ``draft`` status, for the
EXISTING customer Structural Composites Technologies Ltd against their client
purchase order PO26330, and attach the original client PO PDF to that newly
created order.

Everything about it is fixed in this module as a constant: the customer, both
address IDs, the contact person, the single item, its quantity and rate, the
tax, both dates, the reference number, the payment terms, the notes, the
expected totals, and the exact source PDF with its byte size and SHA-256.
Nothing is parameterised and nothing is inferred, so there is no input by
which this tool could be pointed at a different customer, item or order.

TWO DELIBERATE DEPARTURES FROM THE CLIENT PO'S OWN FIGURES
----------------------------------------------------------
1. The RATE is CAD 50.20, not the live item's CAD 45.72 -- the client PO and
   Rachad's own accepted offer of 2026-08-07 both say 50.20.
2. The TAX is Ontario HST at 13%, not the client PO's own "GST (ITC)@5.0%".
   Rachad corrected this directly on 2026-08-11: "we want to charge sale of
   Ontario". The PO's own CAD 5.02 tax figure is therefore NOT used, and the
   sub-total is the only figure this tool still checks against the PO.
Both departures are stated with their source in every staged plan, so Rachad
approves the values themselves and not merely the shape of the order.

THE TWO WRITES, DELIBERATELY NOT ATOMIC
---------------------------------------
1. ONE JSON ``POST /books/v3/salesorders`` creating the order.
2. ONE multipart ``POST /books/v3/salesorders/{new_id}/attachment`` uploading
   the exact source PDF, where ``{new_id}`` comes ONLY from the response to
   the create in step 1. No pre-existing sales order ID is reachable by the
   attachment route, ever.

Zoho assigns the Sales Order number from its own series. ``salesorder_number``
is absent from the payload allowlist and the string
``ignore_auto_number_generation`` does not appear anywhere in this module.

Because the two writes are separate, the order can exist without its
attachment. That is disclosed in the plan and shown to Rachad before he
approves. On ANY failure -- create, attachment, response, read-back, timeout or
indeterminate result -- the plan is permanently locked, the Sales Order ID is
reported when known, and nothing is ever retried, deleted, voided, restatused,
rolled back, cleaned up or attached a second time.

PERMANENTLY UNREACHABLE
-----------------------
There is no PUT, PATCH or DELETE verb anywhere in this module. It cannot
update or delete a sales order, cannot touch any pre-existing order, and
cannot create or modify a customer, contact, address, item, tax or setting.
It has no stock adjustment, allocation, package, shipment, invoice,
confirm/open/mark-sent/approve/void/convert, template or PDF-generation route.
It has no email, SMTP, Graph, Outlook, recipient, send or reminder path of any
kind -- there is no mail transport in this file. It never drives a browser and
never touches a CDP session.
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
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import zoho_tool

TOOL_NAME = "FRP Depot Zoho Books SCT PO26330 Draft Sales Order Tool"
# 2.0.0 / schema 2 applied Rachad's 2026-08-11 Ontario-HST correction.
# Version and schema are bumped whenever a protected commercial term or the
# exact client attachment changes. This rejects both the superseded GST-5%
# plan and any plan that does not fingerprint the revised Rev.1 client PO.
TOOL_VERSION = "2.2.0"
SCHEMA_VERSION = 4
ACTION = "create_sct_po26330_draft_sales_order_with_attachment"

# The ONE plan staged under the superseded GST-5% build. Rachad never approved
# it, it was never committed, and its file is deliberately left on disk as a
# record. The version and schema bump above already refuse it; it is named here
# so a mistaken retry is told WHY instead of only that the plan is invalid.
SUPERSEDED_PLAN_SHA256 = "2950664b01e2366a6e6a2aa3f20cb0e46b0f6b2ba5c2c1d4befcfd3743c3ec56"

ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_sales_order_plans"
PLAN_LIFETIME_HOURS = 24

# Rachad's ruling: his approval is ONE PLAIN WORD answering the displayed plan.
# Compared byte-exact -- no strip(), no case folding, deliberately stricter
# than a tolerant comparison would be.
APPROVAL_WORD = "APPROVED"

# The ONE commissioned write scope. There is deliberately no salesorders
# UPDATE, DELETE, ALL or fullaccess scope, and no Inventory sales-order write
# scope, anywhere in this tree.
CREATE_SCOPE = "ZohoBooks.salesorders.CREATE"
FORBIDDEN_SALESORDER_SCOPES = (
    "ZohoBooks.salesorders.UPDATE",
    "ZohoBooks.salesorders.DELETE",
    "ZohoBooks.salesorders.ALL",
    "ZohoInventory.salesorders.CREATE",
    "ZohoInventory.salesorders.UPDATE",
    "ZohoInventory.salesorders.DELETE",
    "ZohoInventory.salesorders.ALL",
)
# Staging is GET-only and must never require the write scope.
REQUIRED_READ_SCOPES = (
    "ZohoBooks.salesorders.READ",
    "ZohoBooks.contacts.READ",
    "ZohoBooks.settings.READ",
    "ZohoInventory.items.READ",
)

DRAFT_STATUS = "draft"
CURRENCY_CODE = "CAD"

# ---------------------------------------------------------------------------
# The exact fixed transaction. Every value below is evidence-backed; the source
# of each one is carried into the plan so Rachad approves values, not a shape.
# ---------------------------------------------------------------------------

PO_REFERENCE = "PO26330"
PO_NUMBER_RAW = "26330"
ORDER_DATE = "2026-08-11"
REQUIRED_DATE = "2026-08-12"
PAYMENT_TERMS = 30
PAYMENT_TERMS_LABEL = "Net 30"

CUSTOMER_ID = "96274000000186533"
CUSTOMER_NAME = "Structural Composites Technologies Ltd"
BILLING_ADDRESS_ID = "96274000000186536"
SHIPPING_ADDRESS_ID = "96274000000186538"
CONTACT_PERSON_ID = "96274000000509126"
CONTACT_PERSON_NAME = "Bon Bacani"

ITEM_ID = "96274000000523055"
ITEM_SKU = "FNPTCOUPLING-DERAKANE470-3/4\"6\""
ITEM_NAME = "FNPT Coupling, Threaded On both ends-Derakane 470-300/3/4\"/6\""
ITEM_UNIT = "pcs"
QUANTITY = "2"
# The live item sells at CAD 45.72. THIS order is deliberately CAD 50.20: the
# client PO and Rachad's own accepted offer of 2026-08-07 both say 50.20. The
# live item rate is read and displayed but never substituted, and the item
# itself is never altered.
RATE = "50.20"
LINE_DESCRIPTION = (
    "SCT PO26330 item 8-150-470-3/4CPL-M -- Coupling, 3/4\" Diameter, D470, 150 psi, "
    "6\" long"
)

# Explicitly refused look-alikes. The item ID above is a constant, so these can
# never be selected; they are named and refused so that a future edit that
# changed the constant would fail loudly instead of quietly shipping the wrong
# coupling.
REFUSED_ITEM_IDS = {
    "96274000000523057": (
        "the 3/4\" x 8\" variation: it happens to sell at the same CAD 50.20 but has zero "
        "physical stock"
    ),
    "96274000000508303": (
        "the record named LEGACY - DO NOT USE, which is the item the historical SO-00020 used"
    ),
}

# Rachad's own correction, 2026-08-11: "we want to charge sale of Ontario".
# This is the live existing active Ontario HST tax; nothing here creates or
# alters a tax. The revised client PO now matches it exactly.
TAX_ID = "96274000000035516"
TAX_NAME = "ON HST"
TAX_PERCENTAGE = "13"
# What the revised client PO itself prints, pinned so staging fails closed if
# the order tax and the attached source document ever diverge.
CLIENT_PO_TAX_LABEL = "HST - ON@13.0%"
CLIENT_PO_TAX_TOTAL = "13.05"

REQUIRED_PHYSICAL_STOCK = Decimal("2")

NOTES = (
    "Tag: SO38211-Nutrien Vanscoy\n"
    "Required: 2026-08-12\n"
    "Ship via Purolator Express collect on SCT account 3763800\n"
    "Please send tracking number once shipped."
)

# Independently recalculated below with Decimal ROUND_HALF_UP and required to
# equal these figures before anything is staged. The sub-total is the client
# PO's own; the tax and total are Ontario HST at 13% per Rachad's correction.
EXPECTED_SUB_TOTAL = "100.40"
EXPECTED_TAX_TOTAL = "13.05"
EXPECTED_TOTAL = "113.45"

SOURCE_PDF_PATH = ROOT / "Dado" / "20_Working" / "sct_po26330" / "revised" / "PO26330 Rev.1-FRP Depot - Couplings.pdf"
SOURCE_PDF_NAME = "PO26330 Rev.1-FRP Depot - Couplings.pdf"
SOURCE_PDF_SIZE = 149315
SOURCE_PDF_SHA256 = "5703f691a7cf278b9fc93930b1ffacebb290f392fbb1741ed22b82b561f4f2ef"
SOURCE_PDF_CONTENT_TYPE = "application/pdf"

VALUE_SOURCES = {
    "customer": "Live Zoho Books contact 96274000000186533, read-only cross-check 2026-08-11 16:26 UTC",
    "billing_address_id": "Live Zoho address owned by contact 96274000000186533",
    "shipping_address_id": "Live Zoho address owned by contact 96274000000186533",
    "contact_person": "Live Zoho contact person Bon Bacani, who sent the PO",
    "reference_number": "Client PO26330 field 'P.O. No.' 26330, in the user-facing PO26330 form",
    "date": "Client PO26330 field 'Date' 8/11/2026",
    "required_date": "Client PO26330 field 'Required' 8/12/2026",
    "payment_terms": (
        "Client PO26330 field 'Terms' Net 30. The customer default is 'Due end of next month'; "
        "THIS PO overrides it"
    ),
    "item": "Live Zoho item 96274000000523055, the existing active non-legacy 3/4\" x 6\" D470 coupling",
    "quantity": "Client PO26330 line quantity 2 ea",
    "rate": (
        "Client PO26330 unit cost CAD 50.20, matching Rachad's own emailed offer to Bon Bacani "
        "on 2026-08-07"
    ),
    "tax": (
        "Client PO26330 Rev.1 prints HST - ON@13.0% CAD 13.05, matching Rachad's 2026-08-11 "
        "Ontario-sale correction and the live Zoho ON HST tax 96274000000035516 at 13%"
    ),
    "description": "Client PO26330 item code and specification, preserved verbatim",
    "notes": (
        "Client PO26330 tag, required date and shipping instruction, plus Bon Bacani's emailed "
        "request of 2026-08-11 16:06 UTC to send the tracking number once shipped"
    ),
    "attachment": "The original client PO PDF exactly as Bon Bacani sent it, byte-for-byte",
}

# ---------------------------------------------------------------------------
# Routes. Exactly three, all constructed from these patterns and nothing else.
# ---------------------------------------------------------------------------

SALESORDER_COLLECTION_PATH = "/books/v3/salesorders"
SALESORDER_COLLECTION_RE = re.compile(r"^/books/v3/salesorders$")
ATTACHMENT_PATH_RE = re.compile(r"^/books/v3/salesorders/([1-9][0-9]*)/attachment$")
ATTACHMENT_FORM_FIELD = "attachment"
ALLOWED_METHODS = ("GET", "POST")

# One EXISTING sales order, read GET-only, used to prove which optional fields
# this organization's live sales-order contract actually exposes. Nothing is
# ever written to it and its own values are never copied into the new order.
CONTRACT_PROBE_SALESORDER_ID = "96274000001071007"
CONTRACT_PROBE_SALESORDER_NUMBER = "SO-00041"
# Absence of an optional key on the probe order is NOT proof the field does not
# exist -- Zoho omits keys it has no value for. So a missing key means "not
# proven", and this tool then omits the field rather than guessing. Presence IS
# positive proof and enables the field.
OPTIONAL_CONTRACT_KEYS = ("shipment_date", "contact_persons", "documents")
# payment_terms is not optional here: the PO states Net 30 explicitly, so if
# the live contract does not expose it, nothing is staged.
REQUIRED_CONTRACT_KEYS = ("payment_terms", "payment_terms_label")

ATTACHMENT_CONTRACT_NOTE = (
    "Upload is the documented Zoho Books v3 multipart POST to "
    "/books/v3/salesorders/{salesorder_id}/attachment with the single form field 'attachment', "
    "and read-back is the documented GET on that same path, which returns the stored file "
    "itself. NEITHER ROUTE WAS EXERCISED LIVE WHEN THIS PLAN WAS STAGED, because no FRP Depot "
    "sales order carried an attachment to read. If the read-back route does not answer exactly "
    "as documented, the commit reports INDETERMINATE, permanently locks this plan and reports "
    "the Sales Order ID: the order and its attachment will already exist and NOTHING will be "
    "undone, retried or cleaned up."
)

# Safety ceilings for the duplicate sweep. A sweep that hits either ceiling
# fails closed rather than silently reporting "no duplicate found" on a partial
# read of the order book.
MAX_SALESORDER_PAGES = 200
SALESORDER_PAGE_SIZE = 200
MAX_DUPLICATE_DETAIL_READS = 60
# Order states that could still be a live duplicate of this PO. A closed, void
# or fully invoiced historical order is not a duplicate.
OPEN_SALESORDER_STATUSES = (
    "draft", "open", "pending_approval", "approved", "confirmed",
    "partially_shipped", "overdue",
)

STABLE_READ_ATTEMPTS = 2

RISK_NOTE = (
    "TWO writes that are deliberately NOT atomic: one POST creating the Sales Order, then one "
    "multipart POST attaching the client PO to the ID that create returned. Each is attempted "
    "exactly once. If the create succeeds and the attachment does not, the Sales Order EXISTS "
    "WITHOUT ITS ATTACHMENT, this plan is permanently locked, and nothing is retried, deleted, "
    "voided, restatused or attached again -- reconcile in Zoho by hand. This tool never emails "
    "anything and has no mail transport at all."
)

PLAN_FIELDS = {
    "schema_version", "tool", "tool_version", "action", "created_utc", "expires_utc", "nonce",
    "approval_required", "origin", "organization", "payload", "risk", "live_evidence", "sha256",
}
ORIGIN_FIELDS = {"tool_path", "repo_root", "plan_dir"}
ORGANIZATION_FIELDS = {"organization_id", "name", "currency_code"}
RISK_FIELDS = {"atomic", "writes", "email_sent", "note", "attachment_contract"}
EVIDENCE_FIELDS = {
    "customer", "addresses", "contact_person", "item", "tax", "contract", "duplicates",
    "attachment", "totals", "post_endpoint", "post_payload", "attachment_endpoint_template",
    "email_sent",
}
CUSTOMER_EVIDENCE_FIELDS = {
    "customer_id", "customer_name", "company_name", "status", "contact_type", "currency_code",
    "currency_id", "payment_terms", "payment_terms_label", "fingerprint_sha256",
}
ADDRESS_EVIDENCE_FIELDS = {"address_id", "address", "city", "zip", "country", "fingerprint_sha256"}
CONTACT_PERSON_EVIDENCE_FIELDS = {
    "contact_person_id", "first_name", "last_name", "email", "fingerprint_sha256",
}
ITEM_EVIDENCE_FIELDS = {
    "item_id", "name", "sku", "status", "unit", "live_rate", "stock_on_hand",
    "available_stock", "actual_available_stock", "fingerprint_sha256",
}
TAX_EVIDENCE_FIELDS = {"tax_id", "tax_name", "tax_percentage", "kind", "fingerprint_sha256"}
CONTRACT_EVIDENCE_FIELDS = {
    "probe_salesorder_id", "probe_salesorder_number", "exposed_keys", "supported",
    "shipment_date_supported", "contact_persons_supported", "documents_supported",
    "required_date_placement", "fingerprint_sha256",
}
DUPLICATE_EVIDENCE_FIELDS = {
    "normalized_references_refused", "salesorders_scanned", "pages_read",
    "same_customer_open_orders_inspected", "matches", "fingerprint_sha256",
}
ATTACHMENT_EVIDENCE_FIELDS = {
    "file_name", "size_bytes", "sha256", "content_type", "form_field", "source_path", "contract",
}
TOTALS_EVIDENCE_FIELDS = {
    "quantity", "rate", "sub_total", "tax_percentage", "tax_total", "total", "currency_code",
    "basis", "deterministic",
}

CENT = Decimal("0.01")
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
ID_RE = re.compile(r"[1-9][0-9]*")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")


class SalesOrderError(RuntimeError):
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
        raise SalesOrderError("Zoho returned evidence that is not JSON serializable.") from exc


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SalesOrderError(f"{label} JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise SalesOrderError(f"{label} JSON must contain exactly one object.")
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
    raise SalesOrderError(f"{label} must use the exact closed schema ({'; '.join(details)}).")


def clean_text(value: Any, label: str, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise SalesOrderError(f"{label} must be text.")
    if value != value.strip():
        raise SalesOrderError(f"{label} must not have surrounding whitespace.")
    if not value:
        raise SalesOrderError(f"{label} cannot be blank.")
    if len(value) > maximum:
        raise SalesOrderError(f"{label} exceeds the {maximum}-character safety limit.")
    if any(ord(character) < 32 for character in value):
        raise SalesOrderError(f"{label} contains control characters.")
    return value


def positive_id(value: Any, label: str) -> str:
    if isinstance(value, bool):
        raise SalesOrderError(f"{label} must be a positive Zoho ID.")
    text = str(value if value is not None else "").strip()
    if not ID_RE.fullmatch(text):
        raise SalesOrderError(f"{label} must be a positive Zoho ID.")
    return text


def money_text(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def live_number(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SalesOrderError(f"{label} is missing or not a number.")
    text = str(value).strip()
    if not text:
        raise SalesOrderError(f"{label} is blank.")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise SalesOrderError(f"{label} is not a valid number: {value!r}") from exc
    if not result.is_finite():
        raise SalesOrderError(f"{label} must be a finite number.")
    return result


def optional_number(value: Any, label: str) -> Decimal:
    if value in (None, ""):
        return Decimal(0)
    return live_number(value, label)


def exact_float(value: Decimal, label: str) -> float:
    result = float(value)
    if Decimal(str(result)) != value:
        raise SalesOrderError(f"{label} is not exactly representable for the Zoho payload.")
    return result


def parse_date(value: str, label: str) -> date_type:
    if not DATE_RE.fullmatch(value):
        raise SalesOrderError(f"{label} must be an exact YYYY-MM-DD date (got {value!r}).")
    try:
        return date_type.fromisoformat(value)
    except ValueError as exc:
        raise SalesOrderError(f"{label} is not a real calendar date: {value!r}") from exc


def parse_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise SalesOrderError(f"Plan {label} is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SalesOrderError(f"Plan {label} must include a timezone.")
    return parsed


def normalized_reference(value: Any) -> str:
    """Fold a reference to comparable form: lowercase, alphanumerics only."""
    return NON_ALNUM_RE.sub("", str(value if value is not None else "").casefold())


def require_rachad_approval(approval: Any) -> None:
    """Exact, unpadded, uppercase APPROVED. No strip(), no case folding."""
    if not isinstance(approval, str) or approval != APPROVAL_WORD:
        raise SalesOrderError(
            "Rachad must answer this exact staged plan with the one-word approval: "
            f"{APPROVAL_WORD} (exact uppercase, no extra words or spaces). Commissioning, "
            "building and staging are not approval, and Dado cannot supply it."
        )


def origin_record() -> dict[str, str]:
    return {
        "tool_path": str(Path(__file__).resolve()),
        "repo_root": str(ROOT),
        "plan_dir": str(PLAN_DIR),
    }


def require_origin(origin: Any) -> None:
    if not isinstance(origin, dict):
        raise SalesOrderError("Plan origin is invalid.")
    closed_fields(origin, ORIGIN_FIELDS, "Plan origin")
    if origin != origin_record():
        raise SalesOrderError(
            "REFUSED: the plan was staged by a different tool file, repository root, or plan "
            "folder than the one running now."
        )


def contained_plan(raw_path: Any) -> Path:
    """The plan must be a real .json file inside the exact plan folder.

    Symlinks are refused at every level, and the path is checked both
    lexically and after resolution, so neither a traversal nor a link can point
    the commit at a plan this tool did not write.
    """
    candidate = Path(str(raw_path if raw_path is not None else ""))
    if not candidate.is_absolute():
        raise SalesOrderError(
            "Plan must be an absolute path inside the exact sales-order plan folder."
        )
    try:
        lexical_root = PLAN_DIR.absolute()
        candidate.absolute().relative_to(lexical_root)
    except (OSError, ValueError) as exc:
        raise SalesOrderError("Plan is outside the exact allowlisted plan folder.") from exc
    cursor = candidate.absolute()
    while True:
        if cursor.is_symlink():
            raise SalesOrderError("Plan paths and parents must not be symlinks.")
        if cursor == lexical_root:
            break
        parent = cursor.parent
        if parent == cursor:
            raise SalesOrderError("Plan is outside the exact allowlisted plan folder.")
        cursor = parent
    try:
        root = PLAN_DIR.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise SalesOrderError("Plan does not resolve inside the exact plan folder.") from exc
    if (
        root not in resolved.parents
        or not resolved.is_file()
        or resolved.suffix.casefold() != ".json"
    ):
        raise SalesOrderError("Plan is outside the exact allowlisted plan folder.")
    return resolved


# ---------------------------------------------------------------------------
# The fixed source PDF
# ---------------------------------------------------------------------------


def read_source_pdf() -> bytes:
    """The exact client PO, proven by path, size and SHA-256 before every use.

    The path is a module constant, not an argument, so there is no traversal
    surface; it is still checked for symlinks and re-hashed on every read, so a
    file swapped between staging and commit is caught.
    """
    path = SOURCE_PDF_PATH
    cursor = path
    while True:
        if cursor.is_symlink():
            raise SalesOrderError(
                f"REFUSED: the client PO path contains a symlink at {cursor}. The attachment must "
                "be the real file on disk."
            )
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    if not path.is_file():
        raise SalesOrderError(f"REFUSED: the client PO PDF is missing: {path}")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise SalesOrderError(f"The client PO PDF could not be read: {path}") from exc
    if len(content) != SOURCE_PDF_SIZE:
        raise SalesOrderError(
            f"REFUSED: the client PO PDF is {len(content)} bytes, not the exact "
            f"{SOURCE_PDF_SIZE} bytes recorded from Bon Bacani's original attachment."
        )
    digest = hashlib.sha256(content).hexdigest()
    if not secrets.compare_digest(digest, SOURCE_PDF_SHA256):
        raise SalesOrderError(
            f"REFUSED: the client PO PDF hashes to {digest}, not the exact recorded "
            f"{SOURCE_PDF_SHA256}. The file on disk is not the original attachment."
        )
    if not content.startswith(b"%PDF-"):
        raise SalesOrderError("REFUSED: the client PO file is not a PDF.")
    return content


def attachment_evidence() -> dict[str, Any]:
    read_source_pdf()
    return {
        "file_name": SOURCE_PDF_NAME,
        "size_bytes": SOURCE_PDF_SIZE,
        "sha256": SOURCE_PDF_SHA256,
        "content_type": SOURCE_PDF_CONTENT_TYPE,
        "form_field": ATTACHMENT_FORM_FIELD,
        "source_path": str(SOURCE_PDF_PATH),
        "contract": ATTACHMENT_CONTRACT_NOTE,
    }


# ---------------------------------------------------------------------------
# Live read-only Zoho reads
# ---------------------------------------------------------------------------


def books_organization_id(vault: dict[str, Any]) -> str:
    return positive_id(vault.get("books_organization_id"), "books_organization_id")


def inventory_organization_id(vault: dict[str, Any]) -> str:
    return positive_id(vault.get("inventory_organization_id"), "inventory_organization_id")


def require_read_scopes(vault: dict[str, Any]) -> None:
    """Staging needs reads and nothing else; it must never demand the write scope."""
    scopes = [str(scope) for scope in vault.get("scopes") or []]
    missing = [scope for scope in REQUIRED_READ_SCOPES if scope not in scopes]
    if missing:
        raise SalesOrderError(
            "The saved Zoho connection lacks required READ scope(s): " + ", ".join(missing)
        )


def require_no_forbidden_scopes(vault: dict[str, Any]) -> None:
    scopes = {str(scope) for scope in vault.get("scopes") or []}
    present = sorted(scopes & set(FORBIDDEN_SALESORDER_SCOPES))
    if present:
        raise SalesOrderError(
            "REFUSED: the saved Zoho connection holds sales-order scope(s) this commission "
            "never authorized: " + ", ".join(present)
        )


def organization_record(
    access_token: str, vault: dict[str, Any]
) -> tuple[dict[str, str], dict[str, Any]]:
    org_id = books_organization_id(vault)
    result = zoho_tool.api_get(access_token, str(vault["api_domain"]), "/books/v3/organizations")
    organizations = result.get("organizations")
    if not isinstance(organizations, list) or not organizations:
        raise SalesOrderError("Zoho returned no Books organizations.")
    frp = zoho_tool.frp_organization(organizations)
    if positive_id(frp.get("organization_id"), "live organization_id") != org_id:
        raise SalesOrderError(
            "REFUSED: the live FRP Depot Books organization does not match the vault."
        )
    currency = clean_text(frp.get("currency_code"), "organization currency_code", 8)
    return (
        {
            "organization_id": org_id,
            "name": clean_text(
                frp.get("name") or frp.get("organization_name"), "organization name", 200
            ),
            "currency_code": currency,
        },
        json_copy(frp),
    )


def _books_query(vault: dict[str, Any], extra: dict[str, Any] | None = None) -> str:
    return urlencode({**(extra or {}), "organization_id": books_organization_id(vault)})


def get_customer(access_token: str, vault: dict[str, Any]) -> dict[str, Any]:
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/contacts/{CUSTOMER_ID}?{_books_query(vault)}",
    )
    contact = result.get("contact")
    if not isinstance(contact, dict) or str(contact.get("contact_id") or "") != CUSTOMER_ID:
        raise SalesOrderError(
            f"REFUSED: Zoho customer {CUSTOMER_ID} was not found in this organization. This tool "
            "only ever raises an order against that one existing customer; it never creates one."
        )
    return json_copy(contact)


def get_customer_addresses(access_token: str, vault: dict[str, Any]) -> list[dict[str, Any]]:
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/contacts/{CUSTOMER_ID}/address?{_books_query(vault)}",
    )
    addresses = result.get("addresses")
    if not isinstance(addresses, list):
        raise SalesOrderError(f"Zoho returned no address list for customer {CUSTOMER_ID}.")
    return json_copy(addresses)


def get_item(access_token: str, vault: dict[str, Any]) -> dict[str, Any]:
    """The item is read from INVENTORY, which is where physical stock lives."""
    query = urlencode({"organization_id": inventory_organization_id(vault)})
    result = zoho_tool.api_get(
        access_token, str(vault["api_domain"]), f"/inventory/v1/items/{ITEM_ID}?{query}"
    )
    item = result.get("item")
    if not isinstance(item, dict) or str(item.get("item_id") or "") != ITEM_ID:
        raise SalesOrderError(
            f"REFUSED: Zoho item {ITEM_ID} was not found. The order line must name that one "
            "existing active item; this tool never creates or alters an item."
        )
    return json_copy(item)


def get_taxes(access_token: str, vault: dict[str, Any]) -> list[dict[str, Any]]:
    result = zoho_tool.api_get(
        access_token, str(vault["api_domain"]), f"/books/v3/settings/taxes?{_books_query(vault)}"
    )
    taxes = result.get("taxes")
    if not isinstance(taxes, list):
        raise SalesOrderError("Zoho returned no tax list for this organization.")
    return json_copy(taxes)


def get_salesorder(access_token: str, vault: dict[str, Any], salesorder_id: str) -> dict[str, Any]:
    salesorder_id = positive_id(salesorder_id, "salesorder_id")
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/salesorders/{salesorder_id}?{_books_query(vault)}",
    )
    salesorder = result.get("salesorder")
    if not isinstance(salesorder, dict) or str(salesorder.get("salesorder_id") or "") != salesorder_id:
        raise SalesOrderError(f"Zoho sales order {salesorder_id} was not found.")
    return json_copy(salesorder)


def list_salesorders(access_token: str, vault: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """The COMPLETE live sales-order list, with a guard against a partial read.

    A truncated sweep must never be reported as "no duplicate found", so both
    the page ceiling and a missing page_context fail closed.
    """
    rows: list[dict[str, Any]] = []
    pages = 0
    for page in range(1, MAX_SALESORDER_PAGES + 1):
        payload = zoho_tool.api_get(
            access_token,
            str(vault["api_domain"]),
            SALESORDER_COLLECTION_PATH
            + "?"
            + _books_query(vault, {"page": page, "per_page": SALESORDER_PAGE_SIZE}),
        )
        batch = payload.get("salesorders")
        if not isinstance(batch, list):
            raise SalesOrderError(
                f"Zoho returned no sales-order list on page {page}; the duplicate check cannot be "
                "completed and nothing was staged."
            )
        rows.extend(json_copy(batch))
        pages = page
        try:
            has_more = zoho_tool.require_has_more_page(
                payload, SALESORDER_COLLECTION_PATH, page, SalesOrderError
            )
        except SalesOrderError as exc:
            raise SalesOrderError(
                f"Zoho did not state whether sales-order page {page} was the last, so the sweep "
                "cannot be proven complete. Nothing was staged."
            ) from exc
        if not has_more:
            break
    else:
        raise SalesOrderError(
            f"REFUSED: the sales-order list still reported more pages after "
            f"{MAX_SALESORDER_PAGES} pages. The duplicate check cannot be proven complete."
        )
    return rows, pages


def stable_read(reader: Callable[[], Any], label: str) -> Any:
    """Read the same live state twice and require an identical answer.

    A value that moves between two back-to-back reads is not a value this tool
    will build a fixed plan on.
    """
    first = reader()
    for attempt in range(2, STABLE_READ_ATTEMPTS + 1):
        again = reader()
        if canonical(again) != canonical(first):
            raise SalesOrderError(
                f"REFUSED: {label} changed between two consecutive live reads (attempt {attempt}). "
                "The live state is not stable enough to stage a fixed plan."
            )
    return first


# ---------------------------------------------------------------------------
# Preconditions -- every fixed fact re-proved against live state
# ---------------------------------------------------------------------------


def customer_evidence(contact: dict[str, Any]) -> dict[str, Any]:
    customer_id = positive_id(contact.get("contact_id"), "customer contact_id")
    if customer_id != CUSTOMER_ID:
        raise SalesOrderError(
            f"REFUSED: Zoho returned customer {customer_id}, not the fixed {CUSTOMER_ID}."
        )
    name = clean_text(
        contact.get("contact_name") or contact.get("company_name"), "customer name", 300
    )
    if name != CUSTOMER_NAME:
        raise SalesOrderError(
            f"REFUSED: live customer {customer_id} is named {name!r}, not the fixed "
            f"{CUSTOMER_NAME!r}."
        )
    contact_type = str(contact.get("contact_type") or "").strip()
    if contact_type != "customer":
        raise SalesOrderError(
            f"REFUSED: contact {customer_id} is a {contact_type or 'unknown'} record, not a customer."
        )
    status = str(contact.get("status") or "").strip()
    if status != "active":
        raise SalesOrderError(
            f"REFUSED: customer {customer_id} is {status or 'unknown'}, not active."
        )
    currency = str(contact.get("currency_code") or "").strip()
    if currency != CURRENCY_CODE:
        raise SalesOrderError(
            f"REFUSED: customer {customer_id} bills in {currency or 'an unknown currency'}, not "
            f"{CURRENCY_CODE}. This tool never sets a currency or an exchange rate."
        )
    row = {
        "customer_id": customer_id,
        "customer_name": name,
        "company_name": str(contact.get("company_name") or ""),
        "status": status,
        "contact_type": contact_type,
        "currency_code": currency,
        "currency_id": str(contact.get("currency_id") or ""),
        "payment_terms": str(contact.get("payment_terms") if contact.get("payment_terms") is not None else ""),
        "payment_terms_label": str(contact.get("payment_terms_label") or ""),
    }
    return {**row, "fingerprint_sha256": digest_for(row)}


def address_evidence(
    addresses: list[dict[str, Any]], contact: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for entry in addresses:
        if isinstance(entry, dict):
            address_id = str(entry.get("address_id") or "").strip()
            if ID_RE.fullmatch(address_id):
                index[address_id] = entry
    for key in ("billing_address", "shipping_address"):
        entry = contact.get(key)
        if isinstance(entry, dict):
            address_id = str(entry.get("address_id") or "").strip()
            if ID_RE.fullmatch(address_id) and address_id not in index:
                index[address_id] = entry
    result: dict[str, dict[str, Any]] = {}
    for field, wanted in (
        ("billing_address_id", BILLING_ADDRESS_ID),
        ("shipping_address_id", SHIPPING_ADDRESS_ID),
    ):
        record = index.get(wanted)
        if record is None:
            raise SalesOrderError(
                f"REFUSED: {field} {wanted} is not one of the addresses owned by live customer "
                f"{CUSTOMER_ID}. Known address IDs: {', '.join(sorted(index)) or 'none'}."
            )
        row = {
            "address_id": wanted,
            "address": str(record.get("address") or ""),
            "city": str(record.get("city") or ""),
            "zip": str(record.get("zip") or ""),
            "country": str(record.get("country") or ""),
        }
        result[field] = {**row, "fingerprint_sha256": digest_for(row)}
    return result


def contact_person_evidence(contact: dict[str, Any]) -> dict[str, Any]:
    people = contact.get("contact_persons")
    if not isinstance(people, list):
        raise SalesOrderError(
            f"REFUSED: live customer {CUSTOMER_ID} exposes no contact-person list, so "
            f"{CONTACT_PERSON_NAME} cannot be confirmed."
        )
    for person in people:
        if not isinstance(person, dict):
            continue
        if str(person.get("contact_person_id") or "") != CONTACT_PERSON_ID:
            continue
        row = {
            "contact_person_id": CONTACT_PERSON_ID,
            "first_name": str(person.get("first_name") or ""),
            "last_name": str(person.get("last_name") or ""),
            "email": str(person.get("email") or ""),
        }
        full_name = f"{row['first_name']} {row['last_name']}".strip()
        if full_name != CONTACT_PERSON_NAME:
            raise SalesOrderError(
                f"REFUSED: contact person {CONTACT_PERSON_ID} is {full_name!r}, not the fixed "
                f"{CONTACT_PERSON_NAME!r}."
            )
        return {**row, "fingerprint_sha256": digest_for(row)}
    raise SalesOrderError(
        f"REFUSED: contact person {CONTACT_PERSON_ID} ({CONTACT_PERSON_NAME}) is not on live "
        f"customer {CUSTOMER_ID}."
    )


def item_evidence(item: dict[str, Any]) -> dict[str, Any]:
    item_id = positive_id(item.get("item_id"), "item_id")
    if item_id in REFUSED_ITEM_IDS:
        raise SalesOrderError(
            f"REFUSED: item {item_id} is {REFUSED_ITEM_IDS[item_id]}. It is never used for this "
            "order."
        )
    if item_id != ITEM_ID:
        raise SalesOrderError(f"REFUSED: Zoho returned item {item_id}, not the fixed {ITEM_ID}.")
    name = clean_text(item.get("name"), f"item {item_id} name", 300)
    if name != ITEM_NAME:
        raise SalesOrderError(
            f"REFUSED: live item {item_id} is named {name!r}, not the fixed {ITEM_NAME!r}."
        )
    sku = str(item.get("sku") or "")
    if sku != ITEM_SKU:
        raise SalesOrderError(
            f"REFUSED: live item {item_id} has SKU {sku!r}, not the fixed {ITEM_SKU!r}."
        )
    status = str(item.get("status") or "").strip()
    if status != "active":
        raise SalesOrderError(
            f"REFUSED: item {item_id} is {status or 'unknown'}, not active."
        )
    unit = str(item.get("unit") or "")
    if unit != ITEM_UNIT:
        raise SalesOrderError(
            f"REFUSED: live item {item_id} is sold in {unit!r}, not the fixed {ITEM_UNIT!r}."
        )
    # PHYSICAL stock, not the committed/available projection: the PO ships two
    # couplings that must actually be on the shelf.
    physical = live_number(
        item.get("actual_available_stock"), f"item {item_id} actual_available_stock"
    )
    if physical < REQUIRED_PHYSICAL_STOCK:
        raise SalesOrderError(
            f"REFUSED: item {item_id} has {physical} physical available stock, fewer than the "
            f"{REQUIRED_PHYSICAL_STOCK} this order ships. Nothing was staged and no stock was "
            "adjusted -- this tool cannot adjust or allocate stock at all."
        )
    row = {
        "item_id": item_id,
        "name": name,
        "sku": sku,
        "status": status,
        "unit": unit,
        "live_rate": money_text(optional_number(item.get("rate"), f"item {item_id} rate")),
        "stock_on_hand": format(optional_number(item.get("stock_on_hand"), "stock_on_hand"), "f"),
        "available_stock": format(
            optional_number(item.get("available_stock"), "available_stock"), "f"
        ),
        "actual_available_stock": format(physical, "f"),
    }
    return {**row, "fingerprint_sha256": digest_for(row)}


def tax_evidence(taxes: list[dict[str, Any]]) -> dict[str, Any]:
    for record in taxes:
        if not isinstance(record, dict) or str(record.get("tax_id") or "") != TAX_ID:
            continue
        name = clean_text(record.get("tax_name"), f"tax {TAX_ID} name", 200)
        if name != TAX_NAME:
            raise SalesOrderError(
                f"REFUSED: live tax {TAX_ID} is named {name!r}, not the fixed {TAX_NAME!r}."
            )
        percentage = live_number(record.get("tax_percentage"), f"tax {TAX_ID} percentage")
        if percentage != Decimal(TAX_PERCENTAGE):
            raise SalesOrderError(
                f"REFUSED: live tax {TAX_ID} is {percentage}%, not the fixed {TAX_PERCENTAGE}%."
            )
        kind = str(record.get("tax_type") or "tax").strip() or "tax"
        row = {
            "tax_id": TAX_ID,
            "tax_name": name,
            "tax_percentage": format(percentage, "f"),
            "kind": kind,
        }
        return {**row, "fingerprint_sha256": digest_for(row)}
    raise SalesOrderError(
        f"REFUSED: tax {TAX_ID} ({TAX_NAME} at {TAX_PERCENTAGE}%) is not a live tax in this "
        "organization."
    )


def contract_evidence(probe: dict[str, Any]) -> dict[str, Any]:
    """Which optional sales-order fields this live contract actually exposes.

    Presence of a key on a real existing order is positive proof the field is
    part of the contract. Absence proves nothing -- Zoho omits keys it has no
    value for -- so an absent key means NOT PROVEN, and the field is then
    omitted from the payload rather than guessed at.
    """
    probe_id = positive_id(probe.get("salesorder_id"), "probe salesorder_id")
    if probe_id != CONTRACT_PROBE_SALESORDER_ID:
        raise SalesOrderError(
            f"REFUSED: the contract probe returned sales order {probe_id}, not the fixed "
            f"{CONTRACT_PROBE_SALESORDER_ID}."
        )
    number = clean_text(probe.get("salesorder_number"), "probe salesorder_number", 100)
    if number != CONTRACT_PROBE_SALESORDER_NUMBER:
        raise SalesOrderError(
            f"REFUSED: the contract probe order is numbered {number!r}, not the fixed "
            f"{CONTRACT_PROBE_SALESORDER_NUMBER!r}."
        )
    missing = [key for key in REQUIRED_CONTRACT_KEYS if key not in probe]
    if missing:
        raise SalesOrderError(
            "REFUSED: the live sales-order contract does not expose " + ", ".join(missing)
            + ". PO26330 states Net 30 explicitly, so the payment terms cannot be left to a "
            "customer default. Nothing was staged."
        )
    supported = sorted(key for key in OPTIONAL_CONTRACT_KEYS if key in probe)
    exposed = sorted(set(probe))
    shipment = "shipment_date" in probe
    row = {
        "probe_salesorder_id": probe_id,
        "probe_salesorder_number": number,
        "exposed_keys": exposed,
        "supported": supported,
        "shipment_date_supported": shipment,
        "contact_persons_supported": "contact_persons" in probe,
        "documents_supported": "documents" in probe,
        "required_date_placement": (
            "shipment_date on the order AND the fixed notes" if shipment
            else (
                "the fixed notes ONLY -- the live contract did not prove a shipment/required date "
                "field, so none is guessed at"
            )
        ),
    }
    return {**row, "fingerprint_sha256": digest_for(row)}


def duplicate_evidence(
    rows: list[dict[str, Any]],
    pages: int,
    detail_reader: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Refuse a real duplicate; never invent one from a coincidence.

    A match is a normalized reference of 26330 / PO26330 on ANY customer, or a
    same-customer order that is still open and names this PO in its notes or
    terms. A historical order that merely shares the money total -- SO-00020 is
    exactly that -- is NOT a duplicate and is left alone.
    """
    refused = sorted({normalized_reference(PO_NUMBER_RAW), normalized_reference(PO_REFERENCE)})
    matches: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise SalesOrderError("Zoho returned an unreadable sales-order row.")
        salesorder_id = str(row.get("salesorder_id") or "")
        reference = normalized_reference(row.get("reference_number"))
        if reference and reference in refused:
            matches.append({
                "salesorder_id": salesorder_id,
                "salesorder_number": str(row.get("salesorder_number") or ""),
                "customer_id": str(row.get("customer_id") or ""),
                "reason": f"reference_number normalizes to {reference!r}",
            })
            continue
        if str(row.get("customer_id") or "") != CUSTOMER_ID:
            continue
        status = str(row.get("status") or "").strip().casefold()
        if status in OPEN_SALESORDER_STATUSES:
            candidates.append(row)
    if len(candidates) > MAX_DUPLICATE_DETAIL_READS:
        raise SalesOrderError(
            f"REFUSED: {len(candidates)} open orders for this customer exceed the "
            f"{MAX_DUPLICATE_DETAIL_READS}-read ceiling, so the duplicate check cannot be proven "
            "complete. Nothing was staged."
        )
    inspected: list[str] = []
    for row in candidates:
        salesorder_id = positive_id(row.get("salesorder_id"), "candidate salesorder_id")
        inspected.append(salesorder_id)
        detail = detail_reader(salesorder_id)
        haystack = " ".join(
            str(detail.get(key) or "") for key in ("notes", "terms", "reference_number")
        )
        if PO_NUMBER_RAW in NON_ALNUM_RE.sub("", haystack.casefold()):
            matches.append({
                "salesorder_id": salesorder_id,
                "salesorder_number": str(detail.get("salesorder_number") or ""),
                "customer_id": str(detail.get("customer_id") or ""),
                "reason": "an open order for this customer already names this PO in its text",
            })
    if matches:
        described = "; ".join(
            f"{match['salesorder_number'] or match['salesorder_id']} ({match['reason']})"
            for match in matches
        )
        raise SalesOrderError(
            f"REFUSED: PO{PO_NUMBER_RAW} already appears on an existing Zoho sales order: "
            f"{described}. Nothing was staged and no order was created."
        )
    row_evidence = {
        "normalized_references_refused": refused,
        "salesorders_scanned": len(rows),
        "pages_read": pages,
        "same_customer_open_orders_inspected": sorted(inspected),
        "matches": [],
    }
    return {**row_evidence, "fingerprint_sha256": digest_for(row_evidence)}


# ---------------------------------------------------------------------------
# Deterministic builder -- the single projection of live state -> plan
# ---------------------------------------------------------------------------


def build_totals() -> dict[str, Any]:
    """Independent Decimal arithmetic, checked against the approved figures.

    The SUB-TOTAL is the client PO's own. The TAX is Ontario HST at 13% per
    Rachad's 2026-08-11 correction, so it deliberately does NOT equal the PO's
    own CAD 5.02 GST line and is never checked against it.

    One line and one simple single-rate tax, so Zoho's result is fully
    deterministic: the per-line and net-subtotal tax bases coincide and every
    figure is asserted on read-back.
    """
    quantity = Decimal(QUANTITY)
    rate = Decimal(RATE)
    sub_total = (quantity * rate).quantize(CENT, rounding=ROUND_HALF_UP)
    tax_total = (sub_total * Decimal(TAX_PERCENTAGE) / Decimal(100)).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    total = (sub_total + tax_total).quantize(CENT, rounding=ROUND_HALF_UP)
    computed = {
        "sub_total": money_text(sub_total),
        "tax_total": money_text(tax_total),
        "total": money_text(total),
    }
    approved = {
        "sub_total": EXPECTED_SUB_TOTAL,
        "tax_total": EXPECTED_TAX_TOTAL,
        "total": EXPECTED_TOTAL,
    }
    if computed != approved:
        raise SalesOrderError(
            "REFUSED: the independently calculated totals "
            f"{computed} do not equal the approved figures {approved}. Nothing was staged."
        )
    return {
        "quantity": QUANTITY,
        "rate": RATE,
        "sub_total": computed["sub_total"],
        "tax_percentage": TAX_PERCENTAGE,
        "tax_total": computed["tax_total"],
        "total": computed["total"],
        "currency_code": CURRENCY_CODE,
        "basis": (
            f"one line, one simple single-rate {TAX_NAME} at {TAX_PERCENTAGE}%, no discount, no "
            "shipping charge and no adjustment: the per-line and net-subtotal tax bases coincide, "
            "so every total is exact and is asserted against the live order after creation. The "
            f"sub-total and tax match the revised client PO's {CLIENT_PO_TAX_LABEL} CAD "
            f"{CLIENT_PO_TAX_TOTAL}, consistent with Rachad's 2026-08-11 Ontario correction"
        ),
        "deterministic": True,
    }


def build_payload(contract: dict[str, Any]) -> dict[str, Any]:
    """The complete POST body. Unproven fields are OMITTED, never guessed.

    salesorder_number is absent so Zoho's own numbering assigns it; status,
    currency, exchange rate, discount, shipping charge, adjustment, salesperson,
    template, custom fields, warehouse, package/shipment/invoice fields and
    every mail or send flag are absent because they are outside this commission
    and are not in the allowlist at all.
    """
    order_date = parse_date(ORDER_DATE, "order date")
    required_date = parse_date(REQUIRED_DATE, "required date")
    if required_date < order_date:
        raise SalesOrderError("REFUSED: the required date is before the order date.")

    line: dict[str, Any] = {
        "item_id": ITEM_ID,
        "quantity": exact_float(Decimal(QUANTITY), "line quantity"),
        "rate": exact_float(Decimal(RATE), "line rate"),
        "description": LINE_DESCRIPTION,
        "tax_id": TAX_ID,
    }
    post: dict[str, Any] = {
        "customer_id": CUSTOMER_ID,
        "date": ORDER_DATE,
        "reference_number": PO_REFERENCE,
        "payment_terms": PAYMENT_TERMS,
        "payment_terms_label": PAYMENT_TERMS_LABEL,
        "billing_address_id": BILLING_ADDRESS_ID,
        "shipping_address_id": SHIPPING_ADDRESS_ID,
        "notes": NOTES,
        "line_items": [line],
    }
    if contract["shipment_date_supported"]:
        post["shipment_date"] = REQUIRED_DATE
    if contract["contact_persons_supported"]:
        post["contact_persons"] = [CONTACT_PERSON_ID]
    require_post_shape(post)
    return post


ALLOWED_POST_KEYS = {
    "customer_id", "date", "shipment_date", "reference_number", "payment_terms",
    "payment_terms_label", "billing_address_id", "shipping_address_id", "contact_persons",
    "notes", "line_items",
}
REQUIRED_POST_KEYS = {
    "customer_id", "date", "reference_number", "payment_terms", "payment_terms_label",
    "billing_address_id", "shipping_address_id", "notes", "line_items",
}
LINE_POST_KEYS = ("item_id", "quantity", "rate", "description", "tax_id")
# Keys that would change what the order IS, or would transmit it. None of them
# is in the allowlist; they are named so a test can prove the refusal.
REFUSED_POST_KEYS = (
    "salesorder_number", "ignore_auto_number_generation", "status", "order_status", "currency_id",
    "currency_code", "exchange_rate", "discount", "discount_type", "is_discount_before_tax",
    "shipping_charge", "adjustment", "adjustment_description", "salesperson_id",
    "salesperson_name", "template_id", "custom_fields", "warehouse_id", "location_id",
    "package_ids", "shipment_id", "invoice_id", "invoiced_status", "shipped_status",
    "send", "can_send_in_mail", "email", "to_mail_ids", "cc_mail_ids", "bcc_mail_ids",
    "notification", "reminder",
)


def require_post_shape(post: dict[str, Any]) -> None:
    if not isinstance(post, dict):
        raise SalesOrderError("REFUSED: the sales-order POST payload must be an object.")
    named = sorted(set(post) & set(REFUSED_POST_KEYS))
    if named:
        raise SalesOrderError(
            "REFUSED: the sales-order POST payload names field(s) this commission explicitly "
            "places out of reach: " + ", ".join(named)
            + ". The order number is Zoho's to assign, and nothing here may set a status, a "
            "currency, an exchange rate, a discount, a shipping charge, an adjustment, a "
            "salesperson, a template, a custom field, a warehouse, a package/shipment/invoice "
            "link, or any mail or send flag."
        )
    extra = sorted(set(post) - ALLOWED_POST_KEYS)
    if extra:
        raise SalesOrderError(
            "REFUSED: the sales-order POST payload names uncommissioned field(s): "
            + ", ".join(extra)
            + ". The order number is assigned by Zoho, and the status, currency, exchange rate, "
            "discount, shipping charge, adjustment, salesperson, template, custom fields, "
            "warehouse, package/shipment/invoice fields and every mail or send flag are "
            "unreachable."
        )
    missing = sorted(REQUIRED_POST_KEYS - set(post))
    if missing:
        raise SalesOrderError(
            "REFUSED: the sales-order POST payload is missing required field(s): "
            + ", ".join(missing)
        )
    if post["customer_id"] != CUSTOMER_ID:
        raise SalesOrderError("REFUSED: the payload names a customer other than the fixed one.")
    if post["reference_number"] != PO_REFERENCE:
        raise SalesOrderError("REFUSED: the payload reference is not the fixed PO reference.")
    if post["date"] != ORDER_DATE:
        raise SalesOrderError("REFUSED: the payload date is not the fixed order date.")
    if post.get("shipment_date", REQUIRED_DATE) != REQUIRED_DATE:
        raise SalesOrderError("REFUSED: the payload shipment date is not the fixed required date.")
    if post["payment_terms"] != PAYMENT_TERMS or post["payment_terms_label"] != PAYMENT_TERMS_LABEL:
        raise SalesOrderError(
            f"REFUSED: the payload payment terms are not the fixed {PAYMENT_TERMS} / "
            f"{PAYMENT_TERMS_LABEL} taken from the client PO."
        )
    if post["billing_address_id"] != BILLING_ADDRESS_ID:
        raise SalesOrderError("REFUSED: the payload billing address is not the fixed one.")
    if post["shipping_address_id"] != SHIPPING_ADDRESS_ID:
        raise SalesOrderError("REFUSED: the payload shipping address is not the fixed one.")
    if post["notes"] != NOTES:
        raise SalesOrderError("REFUSED: the payload notes are not the exact fixed notes.")
    if "contact_persons" in post and post["contact_persons"] != [CONTACT_PERSON_ID]:
        raise SalesOrderError(
            "REFUSED: the payload names a contact person other than the fixed one."
        )
    lines = post["line_items"]
    if not isinstance(lines, list) or len(lines) != 1:
        raise SalesOrderError(
            "REFUSED: this order carries exactly one line. No line may be added or removed."
        )
    line = lines[0]
    if not isinstance(line, dict):
        raise SalesOrderError("REFUSED: the POST line must be an object.")
    unknown = sorted(set(line) - set(LINE_POST_KEYS))
    if unknown:
        raise SalesOrderError(
            "REFUSED: the POST line names uncommissioned field(s): " + ", ".join(unknown)
        )
    if sorted(line) != sorted(LINE_POST_KEYS):
        raise SalesOrderError("REFUSED: the POST line must state exactly the commissioned fields.")
    if line["item_id"] != ITEM_ID:
        raise SalesOrderError(
            f"REFUSED: the POST line names item {line['item_id']!r}, not the fixed {ITEM_ID}."
        )
    if Decimal(str(line["quantity"])) != Decimal(QUANTITY):
        raise SalesOrderError("REFUSED: the POST line quantity is not the fixed 2.")
    if Decimal(str(line["rate"])) != Decimal(RATE):
        raise SalesOrderError(f"REFUSED: the POST line rate is not the fixed {RATE}.")
    if line["tax_id"] != TAX_ID:
        raise SalesOrderError(
            f"REFUSED: the POST line tax is not the fixed {TAX_NAME} tax {TAX_ID} at "
            f"{TAX_PERCENTAGE}%."
        )
    if line["description"] != LINE_DESCRIPTION:
        raise SalesOrderError("REFUSED: the POST line description is not the fixed description.")


def build_evidence(
    customer: dict[str, Any],
    addresses: dict[str, dict[str, Any]],
    contact_person: dict[str, Any],
    item: dict[str, Any],
    tax: dict[str, Any],
    contract: dict[str, Any],
    duplicates: dict[str, Any],
    attachment: dict[str, Any],
) -> dict[str, Any]:
    """Stage builds this from live reads; the plan validator rebuilds it from
    the plan's own stored evidence and requires an identical result, so a
    tampered figure, payload key, endpoint or fingerprint cannot survive review.
    """
    return {
        "customer": customer,
        "addresses": addresses,
        "contact_person": contact_person,
        "item": item,
        "tax": tax,
        "contract": contract,
        "duplicates": duplicates,
        "attachment": attachment,
        "totals": build_totals(),
        "post_endpoint": f"POST {SALESORDER_COLLECTION_PATH}",
        "post_payload": build_payload(contract),
        "attachment_endpoint_template": (
            "POST /books/v3/salesorders/{new_salesorder_id}/attachment "
            "(the ID comes only from the create response)"
        ),
        # This tool contains no mail transport. Nothing here can transmit anything.
        "email_sent": False,
    }


def payload_record(contract: dict[str, Any]) -> dict[str, Any]:
    """The human-readable statement of the one transaction, with every source."""
    return {
        "customer_id": CUSTOMER_ID,
        "customer_name": CUSTOMER_NAME,
        "reference_number": PO_REFERENCE,
        "po_number": PO_NUMBER_RAW,
        "date": ORDER_DATE,
        "required_date": REQUIRED_DATE,
        "required_date_placement": contract["required_date_placement"],
        "payment_terms": PAYMENT_TERMS,
        "payment_terms_label": PAYMENT_TERMS_LABEL,
        "billing_address_id": BILLING_ADDRESS_ID,
        "shipping_address_id": SHIPPING_ADDRESS_ID,
        "contact_person_id": CONTACT_PERSON_ID,
        "contact_person_name": CONTACT_PERSON_NAME,
        "contact_person_included": contract["contact_persons_supported"],
        "item_id": ITEM_ID,
        "item_sku": ITEM_SKU,
        "item_name": ITEM_NAME,
        "quantity": QUANTITY,
        "rate": RATE,
        "tax_id": TAX_ID,
        "description": LINE_DESCRIPTION,
        "notes": NOTES,
        "attachment_file_name": SOURCE_PDF_NAME,
        "attachment_sha256": SOURCE_PDF_SHA256,
        "sources": dict(sorted(VALUE_SOURCES.items())),
    }


# ---------------------------------------------------------------------------
# Stage -- GET-only, writes nothing but the local plan and its receipt
# ---------------------------------------------------------------------------


def gather_live_evidence(access_token: str, vault: dict[str, Any]) -> dict[str, Any]:
    """Every live read this plan depends on, each one read twice and required
    to be identical, so the plan rests on stable state."""
    contact = stable_read(lambda: get_customer(access_token, vault), "the live customer record")
    customer = customer_evidence(contact)
    addresses = address_evidence(
        stable_read(
            lambda: get_customer_addresses(access_token, vault), "the live customer addresses"
        ),
        contact,
    )
    contact_person = contact_person_evidence(contact)
    item = item_evidence(stable_read(lambda: get_item(access_token, vault), "the live item record"))
    tax = tax_evidence(stable_read(lambda: get_taxes(access_token, vault), "the live tax list"))
    contract = contract_evidence(
        stable_read(
            lambda: get_salesorder(access_token, vault, CONTRACT_PROBE_SALESORDER_ID),
            "the live sales-order contract probe",
        )
    )
    rows, pages = stable_read(
        lambda: list_salesorders(access_token, vault), "the live sales-order list"
    )
    duplicates = duplicate_evidence(
        rows, pages, lambda salesorder_id: get_salesorder(access_token, vault, salesorder_id)
    )
    return build_evidence(
        customer, addresses, contact_person, item, tax, contract, duplicates,
        attachment_evidence(),
    )


def write_plan(organization: dict[str, str], evidence: dict[str, Any]) -> Path:
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "action": ACTION,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "approval_required": APPROVAL_WORD,
        "origin": origin_record(),
        "organization": organization,
        "payload": payload_record(evidence["contract"]),
        "risk": {
            "atomic": False,
            "writes": 2,
            "email_sent": False,
            "note": RISK_NOTE,
            "attachment_contract": ATTACHMENT_CONTRACT_NOTE,
        },
        "live_evidence": evidence,
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = (PLAN_DIR / f"{stamp}_{ACTION}_{digest[:16]}.json").resolve()
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError as exc:
        raise SalesOrderError("Refused to overwrite an existing sales-order plan.") from exc
    return path


def command_stage(_: argparse.Namespace) -> None:
    vault = zoho_tool.load_vault()
    # Staging is read-only, so it must never demand the write scope.
    require_read_scopes(vault)
    require_no_forbidden_scopes(vault)
    access_token, vault = zoho_tool.refresh_access_token(vault)
    organization, _record = organization_record(access_token, vault)
    if organization["currency_code"] != CURRENCY_CODE:
        raise SalesOrderError(
            f"REFUSED: the FRP Depot organization reports {organization['currency_code']}, not "
            f"{CURRENCY_CODE}."
        )
    evidence = gather_live_evidence(access_token, vault)
    zoho_tool.save_vault(vault)
    path = write_plan(organization, evidence)
    plan = read_json_object(path, "Plan")
    zoho_tool.append_receipt(
        f"zoho_books_{ACTION}_plan_staged_not_committed",
        f"plan={path}; sha256={plan['sha256']}; customer={CUSTOMER_NAME} ({CUSTOMER_ID}); "
        f"reference={PO_REFERENCE}; total={EXPECTED_TOTAL} {CURRENCY_CODE}; "
        f"attachment={SOURCE_PDF_NAME}; zoho_writes=0; salesorders_created=0; "
        "attachments_uploaded=0; email_sent=false",
    )
    print_stage_summary(plan, path)


def print_stage_summary(plan: dict[str, Any], path: Path) -> None:
    evidence = plan["live_evidence"]
    totals = evidence["totals"]
    contract = evidence["contract"]
    item = evidence["item"]
    print("STAGED_NOT_COMMITTED")
    print(f"Tool: {TOOL_NAME} {TOOL_VERSION}")
    print("Action: create ONE NEW Sales Order in DRAFT status and attach the original client PO")
    print(
        f"Organization: {plan['organization']['name']} "
        f"({plan['organization']['organization_id']}), {plan['organization']['currency_code']}"
    )
    print("")
    print(f"Customer: {CUSTOMER_NAME} ({CUSTOMER_ID}), {evidence['customer']['status']}")
    print(f"  currency {evidence['customer']['currency_code']} -- the customer's own, never overridden")
    print(
        f"  billing address {BILLING_ADDRESS_ID}, shipping address {SHIPPING_ADDRESS_ID} "
        "(both owned by this customer)"
    )
    print(
        f"  contact person {CONTACT_PERSON_NAME} ({CONTACT_PERSON_ID}): "
        + ("included on the order" if contract["contact_persons_supported"]
           else "NOT included -- the live contract did not prove the field")
    )
    print("")
    print(f"Reference number: {PO_REFERENCE}   [{VALUE_SOURCES['reference_number']}]")
    print(f"Order date: {ORDER_DATE}   [{VALUE_SOURCES['date']}]")
    print(f"Required date: {REQUIRED_DATE} -- recorded in: {contract['required_date_placement']}")
    print(
        f"Payment terms: {PAYMENT_TERMS} / {PAYMENT_TERMS_LABEL}   "
        f"[{VALUE_SOURCES['payment_terms']}]"
    )
    print(f"Sales Order number: assigned by Zoho's own numbering -- never supplied or overridden")
    print("")
    print("Line (exactly one):")
    print(f"  item {ITEM_NAME}")
    print(f"    id {ITEM_ID}, SKU {ITEM_SKU}, unit {item['unit']}, status {item['status']}")
    print(
        f"    physical available stock {item['actual_available_stock']} "
        f"(this order ships {QUANTITY})"
    )
    print(
        f"    qty {QUANTITY} x rate {RATE} = {totals['sub_total']} {CURRENCY_CODE}   "
        f"[{VALUE_SOURCES['rate']}]"
    )
    print(
        f"    the live item sells at {item['live_rate']}; THIS order is deliberately {RATE} and "
        "the item itself is not altered"
    )
    print(f"    tax {TAX_NAME} ({TAX_ID}) at {TAX_PERCENTAGE}%   [{VALUE_SOURCES['tax']}]")
    print(
        f"    the revised client PO prints {CLIENT_PO_TAX_LABEL} CAD {CLIENT_PO_TAX_TOTAL}; "
        "this order matches it exactly"
    )
    print(f"    description: {LINE_DESCRIPTION!r}")
    print("")
    print("Notes (exact):")
    for line in NOTES.split("\n"):
        print(f"  | {line}")
    print("")
    print("Independently calculated totals (exact Decimal, half-up):")
    print(f"  sub_total {totals['sub_total']} {CURRENCY_CODE}")
    print(f"  tax_total {totals['tax_total']}")
    print(f"  total     {totals['total']}")
    print(f"  basis: {totals['basis']}")
    print("")
    print(f"Attachment: {SOURCE_PDF_NAME}")
    print(f"  {SOURCE_PDF_SIZE} bytes, sha256 {SOURCE_PDF_SHA256}")
    print(f"  source: {SOURCE_PDF_PATH}")
    print("")
    duplicates = evidence["duplicates"]
    print(
        f"Duplicate check: {duplicates['salesorders_scanned']} sales orders across "
        f"{duplicates['pages_read']} page(s); no order carries reference "
        + " or ".join(duplicates["normalized_references_refused"])
        + f"; {len(duplicates['same_customer_open_orders_inspected'])} open order(s) for this "
        "customer were read in full and none names this PO."
    )
    print(
        "  SO-00020 carries CAD 105.42 -- what this order totalled before the Ontario-HST "
        "correction -- but has no PO reference and used the now-legacy item. A money total is "
        "never a match criterion: it is historical, not a duplicate, and is not touched."
    )
    print("")
    print(f"Endpoint 1: {evidence['post_endpoint']}")
    print(f"Endpoint 2: {evidence['attachment_endpoint_template']}")
    print("Zoho writes performed by this stage: 0 (read-only GETs only)")
    print("Emails sent or queued by this tool, ever: 0 (it has no mail transport)")
    print(f"Plan: {path}")
    print(f"Plan sha256: {plan['sha256']}")
    print(f"Expires: {plan['expires_utc']} (24-hour maximum)")
    print("")
    print(f"RISK: {RISK_NOTE}")
    print(f"ATTACHMENT CONTRACT: {ATTACHMENT_CONTRACT_NOTE}")
    print(
        f"To commit, Rachad must answer THIS plan with the exact one word {APPROVAL_WORD}. "
        "Commissioning and staging are not approval."
    )


# ---------------------------------------------------------------------------
# Plan validation
# ---------------------------------------------------------------------------


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    closed_fields(plan, PLAN_FIELDS, "Plan")
    if secrets.compare_digest(str(plan.get("sha256") or ""), SUPERSEDED_PLAN_SHA256):
        raise SalesOrderError(
            "REFUSED: this is the SUPERSEDED plan staged under the GST-5% build. Rachad corrected "
            "the tax to Ontario HST 13% on 2026-08-11 (\"we want to charge sale of Ontario\"), so "
            "its approved figures -- tax CAD 5.02 and total CAD 105.42 -- are wrong. It was never "
            "approved and never committed, and it can never be committed by this build. Stage a "
            "new plan and show Rachad the corrected figures."
        )
    if (
        plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("tool") != TOOL_NAME
        or plan.get("tool_version") != TOOL_VERSION
        or plan.get("action") != ACTION
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise SalesOrderError(
            "Plan schema version, tool, tool version, action, or approval requirement is invalid."
        )
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise SalesOrderError("Plan nonce is invalid.")
    require_origin(plan.get("origin"))
    organization = plan.get("organization")
    if not isinstance(organization, dict):
        raise SalesOrderError("Plan organization is invalid.")
    closed_fields(organization, ORGANIZATION_FIELDS, "Plan organization")
    positive_id(organization["organization_id"], "plan organization_id")
    clean_text(organization["name"], "plan organization name", 200)
    if organization["currency_code"] != CURRENCY_CODE:
        raise SalesOrderError("Plan organization currency is not the fixed CAD.")
    created = parse_time(plan["created_utc"], "creation time")
    expires = parse_time(plan["expires_utc"], "expiry")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise SalesOrderError("Plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise SalesOrderError("Plan creation time is in the future.")
    if now >= expires:
        raise SalesOrderError("Plan expired. Stage a new plan for review.")

    risk = plan.get("risk")
    if not isinstance(risk, dict):
        raise SalesOrderError("Plan risk disclosure is invalid.")
    closed_fields(risk, RISK_FIELDS, "Plan risk")
    if (
        risk["atomic"] is not False
        or risk["writes"] != 2
        or risk["email_sent"] is not False
        or risk["note"] != RISK_NOTE
        or risk["attachment_contract"] != ATTACHMENT_CONTRACT_NOTE
    ):
        raise SalesOrderError(
            "Plan must disclose the exact two-write, non-atomic risk and the attachment contract."
        )

    evidence = plan.get("live_evidence")
    if not isinstance(evidence, dict):
        raise SalesOrderError("Plan live evidence is invalid.")
    closed_fields(evidence, EVIDENCE_FIELDS, "Plan live evidence")
    if evidence["email_sent"] is not False:
        raise SalesOrderError("Plan must record that no email is sent.")

    for label, value, fields in (
        ("customer", evidence["customer"], CUSTOMER_EVIDENCE_FIELDS),
        ("contact person", evidence["contact_person"], CONTACT_PERSON_EVIDENCE_FIELDS),
        ("item", evidence["item"], ITEM_EVIDENCE_FIELDS),
        ("tax", evidence["tax"], TAX_EVIDENCE_FIELDS),
        ("contract", evidence["contract"], CONTRACT_EVIDENCE_FIELDS),
        ("duplicates", evidence["duplicates"], DUPLICATE_EVIDENCE_FIELDS),
        ("attachment", evidence["attachment"], ATTACHMENT_EVIDENCE_FIELDS),
        ("totals", evidence["totals"], TOTALS_EVIDENCE_FIELDS),
    ):
        if not isinstance(value, dict):
            raise SalesOrderError(f"Plan {label} evidence is invalid.")
        closed_fields(value, fields, f"Plan {label} evidence")
    addresses = evidence["addresses"]
    if not isinstance(addresses, dict) or set(addresses) != {
        "billing_address_id", "shipping_address_id"
    }:
        raise SalesOrderError("Plan address evidence must name exactly both fixed addresses.")
    for field, record in addresses.items():
        if not isinstance(record, dict):
            raise SalesOrderError("Plan address evidence is invalid.")
        closed_fields(record, ADDRESS_EVIDENCE_FIELDS, f"Plan {field} evidence")

    # Every stored fingerprint must be the digest of its own record.
    for label, record in (
        ("customer", evidence["customer"]),
        ("contact person", evidence["contact_person"]),
        ("item", evidence["item"]),
        ("tax", evidence["tax"]),
        ("contract", evidence["contract"]),
        ("duplicates", evidence["duplicates"]),
        ("billing address", addresses["billing_address_id"]),
        ("shipping address", addresses["shipping_address_id"]),
    ):
        body = {key: value for key, value in record.items() if key != "fingerprint_sha256"}
        if not secrets.compare_digest(str(record["fingerprint_sha256"]), digest_for(body)):
            raise SalesOrderError(f"Plan {label} fingerprint is invalid.")

    if evidence["attachment"] != attachment_evidence():
        raise SalesOrderError(
            "Plan attachment evidence does not match the exact client PO on disk."
        )

    # Re-derive the ENTIRE projection from the plan's own stored live evidence.
    rebuilt = build_evidence(
        evidence["customer"], addresses, evidence["contact_person"], evidence["item"],
        evidence["tax"], evidence["contract"], evidence["duplicates"], evidence["attachment"],
    )
    if rebuilt != evidence:
        raise SalesOrderError(
            "Plan evidence is not the canonical projection of the staged live state."
        )
    if plan["payload"] != payload_record(evidence["contract"]):
        raise SalesOrderError("Plan payload statement is not canonical.")
    require_post_shape(evidence["post_payload"])
    if evidence["post_endpoint"] != f"POST {SALESORDER_COLLECTION_PATH}":
        raise SalesOrderError("Plan endpoint is not the one commissioned sales-order route.")
    return evidence


def load_plan(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = read_json_object(path, "Plan")
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise SalesOrderError("Plan hash check failed. The plan changed after review.")
    return plan, validate_plan(plan)


def lock_path(plan_sha256: str) -> Path:
    if not HEX_64_RE.fullmatch(str(plan_sha256)):
        raise SalesOrderError("Plan digest is invalid for replay locking.")
    return PLAN_DIR / ".commit-locks" / f"{plan_sha256}.json"


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise SalesOrderError(
            "REFUSED: this plan has already entered commit and cannot be replayed."
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")
    # Durable: the lock must survive a crash between the lock and the POST.
    try:
        descriptor = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


# ---------------------------------------------------------------------------
# The transports -- ONE network call site, two write routes, no other verb
# ---------------------------------------------------------------------------


def _perform(
    method: str, url: str, headers: dict[str, str], data: bytes | None, label: str
) -> bytes:
    """The single network call site in this module.

    Every route below funnels through here, so there is exactly one place to
    audit. The method allowlist holds GET and POST and nothing else, so no
    update or delete verb can be issued even by a future edit that tried.
    """
    if method not in ALLOWED_METHODS:
        raise SalesOrderError(
            f"REFUSED: {method!r} is not a verb this tool may issue. Only "
            + " and ".join(ALLOWED_METHODS)
            + " exist here; there is no update or delete route anywhere in this module."
        )
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=120) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SalesOrderError(
            f"Zoho sales-order {label} failed with HTTP {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise SalesOrderError(
            f"Zoho sales-order {label} outcome is indeterminate: {exc.reason}"
        ) from exc


def _json_result(raw: bytes, label: str) -> dict[str, Any]:
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SalesOrderError(
            f"Zoho sales-order {label} returned invalid JSON; the outcome is indeterminate."
        ) from exc
    if not isinstance(result, dict) or result.get("code") != 0:
        message = result.get("message") if isinstance(result, dict) else "invalid response"
        raise SalesOrderError(
            f"Zoho sales-order {label} returned an invalid or unknown result: " + str(message)
        )
    return result


def _auth_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Accept": "application/json",
    }


def oauth_salesorder_create_write_allowed(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """The complete create transport and its exact allowlist.

    Only POST. Only the one sales-order collection route -- no ID-suffixed
    route, no confirm/open/mark-sent/approve/void/convert route, no bulk route,
    and no mail parameter of any kind exists here or anywhere else in this
    module. The query string is exactly the organization id, so Zoho's own
    numbering runs and the result is a Draft.
    """
    if method != "POST":
        raise SalesOrderError("REFUSED: the sales-order creation is a POST and nothing else.")
    if not SALESORDER_COLLECTION_RE.fullmatch(str(path)):
        raise SalesOrderError(
            "REFUSED: creation targets the one exact sales-order collection route. Updating, "
            "deleting, voiding, status changes, confirmation, mail, packages, shipments, "
            "invoicing, templates and every bulk route are unreachable."
        )
    require_post_shape(payload)
    org_id = positive_id(organization_id, "organization_id")
    url = (
        api_domain.rstrip("/") + path + "?" + urlencode({"organization_id": org_id})
    )
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {**_auth_headers(access_token), "Content-Type": "application/json"}
    return _json_result(_perform("POST", url, headers, body, "creation"), "creation")


def build_multipart_body(content: bytes) -> tuple[bytes, str]:
    """One part, one file, one field name. Nothing else is encoded."""
    if not isinstance(content, bytes) or not content:
        raise SalesOrderError("REFUSED: the attachment body must be the file's own bytes.")
    if any(character in SOURCE_PDF_NAME for character in ('"', "\r", "\n")):
        raise SalesOrderError("REFUSED: the attachment file name is not safely encodable.")
    for _attempt in range(8):
        boundary = "----FRPDepotSCTPO26330" + secrets.token_hex(24)
        marker = boundary.encode("ascii")
        if marker not in content:
            break
    else:  # pragma: no cover - a 24-byte random collision does not occur
        raise SalesOrderError("REFUSED: no multipart boundary could be chosen safely.")
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{ATTACHMENT_FORM_FIELD}"; '
        f'filename="{SOURCE_PDF_NAME}"\r\n'
        f"Content-Type: {SOURCE_PDF_CONTENT_TYPE}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return head + content + tail, boundary


def oauth_salesorder_attachment_write_allowed(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    created_salesorder_id: str,
    content: bytes,
) -> dict[str, Any]:
    """The complete attachment transport and its exact allowlist.

    Only POST, only the attachment route of ONE sales order, and that ID must
    be the one the create response just returned -- a pre-existing order can
    never be reached. The query string is exactly the organization id: there is
    no can_send_in_mail parameter here or anywhere in this module.
    """
    if method != "POST":
        raise SalesOrderError("REFUSED: the attachment upload is a POST and nothing else.")
    match = ATTACHMENT_PATH_RE.fullmatch(str(path))
    if not match:
        raise SalesOrderError(
            "REFUSED: the attachment targets the one exact sales-order attachment route."
        )
    target = positive_id(match.group(1), "attachment endpoint ID")
    if target != positive_id(created_salesorder_id, "created salesorder_id"):
        raise SalesOrderError(
            f"REFUSED: the attachment route names sales order {target}, which is not the order "
            "this commit just created. No pre-existing sales order is reachable."
        )
    digest = hashlib.sha256(content).hexdigest()
    if len(content) != SOURCE_PDF_SIZE or not secrets.compare_digest(digest, SOURCE_PDF_SHA256):
        raise SalesOrderError(
            "REFUSED: the bytes offered for upload are not the exact client PO. Nothing was sent."
        )
    org_id = positive_id(organization_id, "organization_id")
    url = api_domain.rstrip("/") + path + "?" + urlencode({"organization_id": org_id})
    body, boundary = build_multipart_body(content)
    headers = {
        **_auth_headers(access_token),
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    return _json_result(_perform("POST", url, headers, body, "attachment upload"), "attachment upload")


def download_attachment(
    access_token: str, api_domain: str, organization_id: str, salesorder_id: str
) -> bytes:
    """The documented read-back: GET the stored file itself and return its bytes."""
    salesorder_id = positive_id(salesorder_id, "salesorder_id")
    path = f"/books/v3/salesorders/{salesorder_id}/attachment"
    if not ATTACHMENT_PATH_RE.fullmatch(path):  # pragma: no cover - built from a checked ID
        raise SalesOrderError("REFUSED: the attachment read-back route is malformed.")
    org_id = positive_id(organization_id, "organization_id")
    url = api_domain.rstrip("/") + path + "?" + urlencode({"organization_id": org_id})
    raw = _perform(
        "GET", url, {"Authorization": f"Zoho-oauthtoken {access_token}"}, None,
        "attachment read-back",
    )
    if not isinstance(raw, bytes) or not raw:
        raise SalesOrderError(
            "The attachment read-back returned no content, so the upload cannot be confirmed."
        )
    if raw.lstrip()[:1] in (b"{", b"["):
        # Zoho answered with a message instead of the file: not a verification.
        detail = raw.decode("utf-8", errors="replace")[:500]
        raise SalesOrderError(
            "The attachment read-back returned a JSON message rather than the stored file, so the "
            f"upload cannot be confirmed: {detail}"
        )
    return raw


# ---------------------------------------------------------------------------
# Read-back verification
# ---------------------------------------------------------------------------


def verify_created_salesorder(after: dict[str, Any], evidence: dict[str, Any]) -> str:
    """Everything the approved plan promised, checked against the LIVE order."""
    salesorder_id = positive_id(after.get("salesorder_id"), "read-back salesorder_id")
    status = str(after.get("status") or "")
    if status != DRAFT_STATUS:
        raise SalesOrderError(
            f"Read-back status is {status!r}, not exactly {DRAFT_STATUS!r}. Sales order "
            f"{salesorder_id} exists but is NOT the Draft that was approved. Stop and reconcile by "
            "hand; nothing will be retried, deleted or changed."
        )
    number = clean_text(after.get("salesorder_number"), "read-back salesorder_number", 100)
    post = evidence["post_payload"]
    if "salesorder_number" in post:  # pragma: no cover - impossible by allowlist
        raise SalesOrderError("The order number must be assigned by Zoho, never supplied.")
    if normalized_reference(number) in {
        normalized_reference(PO_NUMBER_RAW), normalized_reference(PO_REFERENCE)
    }:
        raise SalesOrderError(
            f"Read-back order number {number!r} is the client PO number itself, which means Zoho's "
            "own numbering did not assign it. Stop and reconcile."
        )
    for key, expected, label in (
        ("customer_id", CUSTOMER_ID, "customer"),
        ("customer_name", CUSTOMER_NAME, "customer name"),
        ("reference_number", PO_REFERENCE, "reference number"),
        ("date", ORDER_DATE, "order date"),
        ("currency_code", CURRENCY_CODE, "currency"),
        ("notes", NOTES, "notes"),
    ):
        actual = after.get(key)
        actual_text = "" if actual is None else str(actual)
        if actual_text != expected:
            raise SalesOrderError(
                f"Read-back {label} is {actual_text!r}, not the approved {expected!r}. Stop and "
                "reconcile."
            )
    if str(after.get("payment_terms") or "") != str(PAYMENT_TERMS):
        raise SalesOrderError(
            f"Read-back payment terms are {after.get('payment_terms')!r}, not the approved "
            f"{PAYMENT_TERMS}."
        )
    if str(after.get("payment_terms_label") or "") != PAYMENT_TERMS_LABEL:
        raise SalesOrderError(
            f"Read-back payment terms label is {after.get('payment_terms_label')!r}, not the "
            f"approved {PAYMENT_TERMS_LABEL!r}."
        )
    exchange_rate = after.get("exchange_rate")
    if exchange_rate not in (None, "") and live_number(
        exchange_rate, "read-back exchange_rate"
    ) != Decimal(1):
        raise SalesOrderError(
            f"Read-back carries exchange rate {exchange_rate!r}. This tool never sets one and the "
            "customer bills in the organization's own CAD. Stop and reconcile."
        )
    for field, expected_id in (
        ("billing_address", BILLING_ADDRESS_ID),
        ("shipping_address", SHIPPING_ADDRESS_ID),
    ):
        block = after.get(field)
        actual = str(block.get("address_id") or "") if isinstance(block, dict) else ""
        actual = actual or str(after.get(field + "_id") or "")
        if actual != expected_id:
            raise SalesOrderError(
                f"Read-back {field} is {actual!r}, not the approved {expected_id!r}."
            )
    contract = evidence["contract"]
    if contract["shipment_date_supported"]:
        actual = str(after.get("shipment_date") or "")
        if actual != REQUIRED_DATE:
            raise SalesOrderError(
                f"Read-back shipment date is {actual!r}, not the approved {REQUIRED_DATE!r}."
            )
    if contract["contact_persons_supported"]:
        people = after.get("contact_persons")
        actual_ids = (
            [str(person) for person in people] if isinstance(people, list) else []
        )
        if actual_ids != [CONTACT_PERSON_ID]:
            raise SalesOrderError(
                f"Read-back contact persons are {actual_ids!r}, not the approved "
                f"[{CONTACT_PERSON_ID!r}]."
            )
    if bool(after.get("is_emailed")):
        raise SalesOrderError(
            f"Read-back says sales order {number} has already been emailed. This tool has no mail "
            "transport; something else acted. Stop and reconcile."
        )

    lines = after.get("line_items")
    if not isinstance(lines, list) or len(lines) != 1:
        raise SalesOrderError(
            f"Read-back has {len(lines) if isinstance(lines, list) else 'no'} line(s), not the one "
            "approved line. Stop and reconcile."
        )
    line = lines[0]
    if not isinstance(line, dict):
        raise SalesOrderError("Read-back line is not an object.")
    item = evidence["item"]
    if str(line.get("item_id") or "") != ITEM_ID:
        raise SalesOrderError(
            f"Read-back line carries item {line.get('item_id')!r}, not the approved {ITEM_ID!r}. "
            "Stop and reconcile."
        )
    if str(line.get("name") or "") != item["name"]:
        raise SalesOrderError(
            f"Read-back line names {line.get('name')!r}, not the approved item {item['name']!r}."
        )
    actual_sku = str(line.get("sku") or "")
    if actual_sku and actual_sku != ITEM_SKU:
        raise SalesOrderError(
            f"Read-back line SKU is {actual_sku!r}, not the approved {ITEM_SKU!r}."
        )
    if live_number(line.get("quantity"), "read-back line quantity") != Decimal(QUANTITY):
        raise SalesOrderError(
            f"Read-back line quantity is {line.get('quantity')!r}, not the approved {QUANTITY}."
        )
    if live_number(line.get("rate"), "read-back line rate") != Decimal(RATE):
        raise SalesOrderError(
            f"Read-back line rate is {line.get('rate')!r}, not the approved {RATE}."
        )
    if str(line.get("description") or "") != LINE_DESCRIPTION:
        raise SalesOrderError(
            f"Read-back line description is {line.get('description')!r}, not the approved "
            f"{LINE_DESCRIPTION!r}."
        )
    if str(line.get("tax_id") or "") != TAX_ID:
        raise SalesOrderError(
            f"Read-back line tax is {line.get('tax_id')!r}, not the approved {TAX_ID!r}."
        )
    if optional_number(line.get("tax_percentage"), "read-back line tax rate") != Decimal(
        TAX_PERCENTAGE
    ):
        raise SalesOrderError(
            f"Read-back line tax rate is {line.get('tax_percentage')!r}, not the approved "
            f"{TAX_PERCENTAGE}%."
        )
    if optional_number(line.get("discount"), "read-back line discount") != 0:
        raise SalesOrderError(
            f"Read-back line carries discount {line.get('discount')!r}, but none was approved."
        )

    totals = evidence["totals"]
    shipping = optional_number(after.get("shipping_charge"), "read-back shipping_charge")
    adjustment = optional_number(after.get("adjustment"), "read-back adjustment")
    if shipping != 0 or adjustment != 0:
        raise SalesOrderError(
            f"Read-back carries shipping {shipping} and adjustment {adjustment}; this tool sets "
            "neither and approved neither. Stop and reconcile."
        )
    if bool(after.get("is_inclusive_tax")):
        raise SalesOrderError(
            "Read-back prices are tax-inclusive, which the approved calculation did not assume. "
            "Stop and reconcile."
        )
    for key in ("sub_total", "tax_total", "total"):
        actual = live_number(after.get(key), f"read-back {key}")
        if money_text(actual) != totals[key]:
            raise SalesOrderError(
                f"Read-back {key} is {money_text(actual)}, not the calculated {totals[key]}. "
                "Stop and reconcile."
            )
    return number


def verify_attachment(
    downloaded: bytes, after: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    """The upload is proven by the bytes Zoho gives back, not by its own reply."""
    expected = evidence["attachment"]
    size = len(downloaded)
    digest = hashlib.sha256(downloaded).hexdigest()
    if size != expected["size_bytes"] or not secrets.compare_digest(digest, expected["sha256"]):
        raise SalesOrderError(
            f"The attachment Zoho stored is {size} bytes hashing to {digest}, not the exact "
            f"{expected['size_bytes']} bytes hashing to {expected['sha256']}. The stored file is "
            "not the original client PO. Stop and reconcile."
        )
    metadata: dict[str, Any] = {"documents_checked": False, "file_name": "", "file_size": ""}
    documents = after.get("documents")
    if evidence["contract"]["documents_supported"] or isinstance(documents, list):
        if not isinstance(documents, list) or len(documents) != 1:
            raise SalesOrderError(
                f"Read-back lists {len(documents) if isinstance(documents, list) else 'no'} "
                "attached document(s), not the single client PO. Stop and reconcile."
            )
        document = documents[0]
        if not isinstance(document, dict):
            raise SalesOrderError("Read-back attachment metadata is not an object.")
        name = str(document.get("file_name") or "")
        if name != expected["file_name"]:
            raise SalesOrderError(
                f"Read-back attachment is named {name!r}, not the approved "
                f"{expected['file_name']!r}."
            )
        raw_size = document.get("file_size_formatted", document.get("file_size"))
        if isinstance(document.get("file_size"), (int, float)) and not isinstance(
            document.get("file_size"), bool
        ):
            if int(document["file_size"]) != expected["size_bytes"]:
                raise SalesOrderError(
                    f"Read-back attachment metadata reports {document['file_size']} bytes, not the "
                    f"approved {expected['size_bytes']}."
                )
        metadata = {
            "documents_checked": True,
            "file_name": name,
            "file_size": str(raw_size if raw_size is not None else ""),
        }
    return {
        "verified_size_bytes": size,
        "verified_sha256": digest,
        "matches_original": True,
        **metadata,
    }


def verify_no_second_copy(
    rows: list[dict[str, Any]], salesorder_id: str
) -> None:
    """After the write, exactly one order carries this PO and it is ours."""
    refused = {normalized_reference(PO_NUMBER_RAW), normalized_reference(PO_REFERENCE)}
    found = [
        str(row.get("salesorder_id") or "")
        for row in rows
        if isinstance(row, dict) and normalized_reference(row.get("reference_number")) in refused
    ]
    if found != [salesorder_id]:
        raise SalesOrderError(
            f"A fresh duplicate sweep sees sales order(s) {found or 'none'} carrying "
            f"{PO_REFERENCE}, not exactly the one just created ({salesorder_id}). Stop and "
            "reconcile."
        )


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def recheck_live_state(
    access_token: str, vault: dict[str, Any], evidence: dict[str, Any]
) -> None:
    """Fresh reads of every dependency, immediately before the lock.

    A drift here is a FREE refusal: it happens before the exclusive lock, so
    the approved plan is not burned and can be committed once the live state
    settles.
    """
    contact = get_customer(access_token, vault)
    if customer_evidence(contact) != evidence["customer"]:
        raise SalesOrderError(
            f"Customer {CUSTOMER_ID} changed after review. No sales order was created."
        )
    if address_evidence(get_customer_addresses(access_token, vault), contact) != evidence["addresses"]:
        raise SalesOrderError(
            "The approved billing or shipping address changed after review. No sales order was "
            "created."
        )
    if contact_person_evidence(contact) != evidence["contact_person"]:
        raise SalesOrderError(
            f"Contact person {CONTACT_PERSON_ID} changed after review. No sales order was created."
        )
    if item_evidence(get_item(access_token, vault)) != evidence["item"]:
        raise SalesOrderError(
            f"Item {ITEM_ID} changed after review -- check its stock, status, name and SKU. No "
            "sales order was created."
        )
    if tax_evidence(get_taxes(access_token, vault)) != evidence["tax"]:
        raise SalesOrderError(f"Tax {TAX_ID} changed after review. No sales order was created.")
    probe = get_salesorder(access_token, vault, CONTRACT_PROBE_SALESORDER_ID)
    if contract_evidence(probe) != evidence["contract"]:
        raise SalesOrderError(
            "The live sales-order contract changed after review, so the approved payload may no "
            "longer be the right shape. No sales order was created."
        )
    rows, pages = list_salesorders(access_token, vault)
    fresh = duplicate_evidence(
        rows, pages, lambda salesorder_id: get_salesorder(access_token, vault, salesorder_id)
    )
    if fresh["matches"] or fresh["normalized_references_refused"] != evidence["duplicates"][
        "normalized_references_refused"
    ]:
        raise SalesOrderError(
            f"A fresh duplicate sweep no longer agrees that {PO_REFERENCE} is unused. No sales "
            "order was created."
        )


def command_commit(args: argparse.Namespace) -> None:
    plan_path = contained_plan(args.plan)
    plan, evidence = load_plan(plan_path)
    # Approval is checked before the lock, the vault, the token and the network.
    require_rachad_approval(args.approval)
    # The exact client PO must still be on disk, byte for byte, before anything.
    content = read_source_pdf()

    vault = zoho_tool.load_vault()
    # The write scope is required BEFORE the lock and before any network write,
    # so a missing grant is a free refusal that leaves the plan committable.
    if CREATE_SCOPE not in (vault.get("scopes") or []):
        raise SalesOrderError(
            f"Saved Zoho connection lacks {CREATE_SCOPE}. Run PREPARE_DADO_ZOHO_ACCESS.bat, "
            "create the grant in the Zoho API Console, then REAUTHORIZE_DADO_ZOHO.bat and "
            "CHECK_DADO_ZOHO.bat. Nothing was locked and no sales order was created."
        )
    require_read_scopes(vault)
    require_no_forbidden_scopes(vault)

    access_token, vault = zoho_tool.refresh_access_token(vault)
    organization, _record = organization_record(access_token, vault)
    if organization != plan["organization"]:
        raise SalesOrderError(
            "REFUSED: the live FRP Depot Books organization does not match the organization "
            "recorded in the plan. No sales order was created."
        )
    org_id = organization["organization_id"]
    recheck_live_state(access_token, vault, evidence)

    lock = lock_path(plan["sha256"])
    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": ACTION,
        "status": "in_flight",
        "customer_id": CUSTOMER_ID,
        "reference_number": PO_REFERENCE,
        "started_utc": utc_now().isoformat(),
    }, exclusive=True)

    create_attempted = False
    attachment_attempted = False
    salesorder_id = ""
    salesorder_number = ""
    attachment_report: dict[str, Any] = {}
    try:
        create_attempted = True
        result = oauth_salesorder_create_write_allowed(
            access_token,
            str(vault["api_domain"]),
            "POST",
            SALESORDER_COLLECTION_PATH,
            org_id,
            evidence["post_payload"],
        )
        created = result.get("salesorder")
        if not isinstance(created, dict):
            raise SalesOrderError(
                "Zoho accepted the creation but returned no sales-order record, so the new Sales "
                "Order ID is UNKNOWN."
            )
        salesorder_id = positive_id(created.get("salesorder_id"), "created salesorder_id")
        verified = get_salesorder(access_token, vault, salesorder_id)
        salesorder_number = verify_created_salesorder(verified, evidence)

        attachment_attempted = True
        oauth_salesorder_attachment_write_allowed(
            access_token,
            str(vault["api_domain"]),
            "POST",
            f"/books/v3/salesorders/{salesorder_id}/attachment",
            org_id,
            salesorder_id,
            content,
        )
        downloaded = download_attachment(
            access_token, str(vault["api_domain"]), org_id, salesorder_id
        )
        after = get_salesorder(access_token, vault, salesorder_id)
        attachment_report = verify_attachment(downloaded, after, evidence)
        # The order itself must still be exactly what was approved after the
        # attachment landed, and no second copy may exist.
        verify_created_salesorder(after, evidence)
        rows, _pages = list_salesorders(access_token, vault)
        verify_no_second_copy(rows, salesorder_id)
        zoho_tool.save_vault(vault)
    except Exception as exc:
        if attachment_attempted:
            status = "indeterminate_after_create_and_attachment_attempt"
        elif create_attempted:
            status = "indeterminate"
        else:  # pragma: no cover - the lock is taken immediately before the create
            status = "aborted_before_write"
        write_lock(lock, {
            "plan_sha256": plan["sha256"],
            "action": ACTION,
            "status": status,
            "salesorder_id": salesorder_id,
            "salesorder_number": salesorder_number,
            "create_attempted": create_attempted,
            "attachment_attempted": attachment_attempted,
            "plan_locked_indeterminate": create_attempted,
            "updated_utc": utc_now().isoformat(),
            "reason": str(exc)[:2000],
            "no_retry": True,
        })
        zoho_tool.append_receipt(
            f"zoho_books_{ACTION}_{status}_no_retry",
            f"status={status}; salesorder_id={salesorder_id or 'unknown'}; "
            f"salesorder_number={salesorder_number or 'unknown'}; "
            f"create_attempted={str(create_attempted).lower()}; "
            f"attachment_attempted={str(attachment_attempted).lower()}; plan={plan_path}; "
            f"sha256={plan['sha256']}; email_sent=false",
        )
        raise SalesOrderError(
            f"The SCT PO26330 sales order is {status} and this plan is permanently locked against "
            "retry. "
            + (
                "A create POST was ISSUED. Sales Order ID: "
                + (salesorder_id or "UNKNOWN -- Zoho did not return one")
                + (f" ({salesorder_number})" if salesorder_number else "")
                + ". "
                + (
                    "An attachment POST was ALSO issued, so the client PO may or may not be "
                    "attached. "
                    if attachment_attempted else
                    "NO attachment was uploaded, so the order may exist WITHOUT the client PO. "
                )
                + "Its live state is unconfirmed; NOTHING was cleaned up, deleted, voided, "
                "restatused or attached again, and nothing will be retried."
                if create_attempted else
                "No POST was issued and no sales order exists."
            )
            + f" No email was sent; this tool has no mail transport. Reason: {exc} "
            "Read the live sales order in Zoho and reconcile before staging anything new."
        ) from exc

    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": ACTION,
        "status": "committed_verified",
        "salesorder_id": salesorder_id,
        "salesorder_number": salesorder_number,
        "attachment_sha256": attachment_report["verified_sha256"],
        "updated_utc": utc_now().isoformat(),
        "no_retry": True,
    })
    zoho_tool.append_receipt(
        f"zoho_books_{ACTION}_committed_verified",
        f"salesorder={salesorder_number} ({salesorder_id}); status=draft; "
        f"customer={CUSTOMER_NAME} ({CUSTOMER_ID}); reference={PO_REFERENCE}; "
        f"total={evidence['totals']['total']} {CURRENCY_CODE}; "
        f"attachment={SOURCE_PDF_NAME} sha256={attachment_report['verified_sha256']}; "
        f"plan={plan_path}; sha256={plan['sha256']}; email_sent=false",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "action": ACTION,
        "plan_sha256": plan["sha256"],
        "salesorder_id": salesorder_id,
        "salesorder_number": salesorder_number,
        "salesorder_number_assigned_by": "Zoho auto-numbering",
        "salesorder_status_verified": DRAFT_STATUS,
        "customer_id": CUSTOMER_ID,
        "customer_name": CUSTOMER_NAME,
        "reference_number": PO_REFERENCE,
        "currency_code": CURRENCY_CODE,
        "totals_verified": {
            key: evidence["totals"][key] for key in ("sub_total", "tax_total", "total")
        },
        "attachment": attachment_report,
        "email_sent": False,
        "atomic": False,
        "replay_locked": True,
    }, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{TOOL_NAME} {TOOL_VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage-sct-po26330")
    stage.set_defaults(func=command_stage)
    commit = commands.add_parser("commit-sct-po26330")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (SalesOrderError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
