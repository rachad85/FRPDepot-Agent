#!/usr/bin/env python
"""Reference-only repair of the client PO on twelve fixed historical records.

Commissioned by Rachad Homsi on 2026-08-12 after the read-only client-PO audit
proved that six historical Sales Orders and their linked invoices display an
INTERNAL quote or order number where the customer's own PO belongs. Zoho renders
that field to the customer: a Sales Order PDF prints it as "Ref# :" and an
invoice PDF prints it as "P.O.# :".

This tool can change exactly one field -- `reference_number` -- on exactly one of
twelve fixed records per plan. It cannot select any other record, cannot change
any other field, cannot create, delete, void, restatus, convert, mail or attach
anything, and has no browser route. Stage is read-only. Commit needs Rachad's
own later byte-exact APPROVED, one plan at a time.

INV-000020 is a fixed dependency that must keep reading PO5079. It is
verification-only and cannot be selected for staging or commit.

THE WRITE CONTRACT IS PROVEN, NOT GUESSED. Zoho's own published OpenAPI bundle
is pinned by SHA-256 beside this tree (see CONTRACT_FILES). It states:
  PUT /salesorders/{id}  required body = ["customer_id"]
  PUT /invoices/{id}     required body = ["customer_id", "line_items"]
So an order payload is exactly {customer_id, reference_number} and carries NO
lines at all, while an invoice payload must carry lines -- and therefore resends
every live line once, in original order, with its own line_item_id and item_id.
Nothing is ever omitted in the hope that Zoho preserves it.
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

TOOL_NAME = "FRP Depot Fixed Historical Client PO Reference Repair Tool"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = 1
ACTION = "historical_client_po_reference_repair"
APPROVAL_WORD = "APPROVED"
PLAN_LIFETIME_HOURS = 24

ROOT = Path(r"C:\FRPDepot")
WORKING = ROOT / "Dado" / "20_Working"
PLAN_DIR = WORKING / "zoho_historical_client_po_reference_plans"
LOCK_DIR = PLAN_DIR / ".commit-locks"

SALESORDER_SCOPE = "ZohoBooks.salesorders.UPDATE"
INVOICE_SCOPE = "ZohoBooks.invoices.UPDATE"
SALESORDER_READ_SCOPE = "ZohoBooks.salesorders.READ"
INVOICE_READ_SCOPE = "ZohoBooks.invoices.READ"
EXPECTED_API_DOMAIN = "https://www.zohoapis.ca"

SALESORDER_KIND = "salesorder"
INVOICE_KIND = "invoice"
SEGMENTS = {SALESORDER_KIND: "salesorders", INVOICE_KIND: "invoices"}
RECORD_KEYS = {SALESORDER_KIND: "salesorder", INVOICE_KIND: "invoice"}
UPDATE_SCOPES = {SALESORDER_KIND: SALESORDER_SCOPE, INVOICE_KIND: INVOICE_SCOPE}
READ_SCOPES = {SALESORDER_KIND: SALESORDER_READ_SCOPE, INVOICE_KIND: INVOICE_READ_SCOPE}
# Zoho prints the reference under a different caption on each document type.
PDF_LABELS = {SALESORDER_KIND: "Ref#", INVOICE_KIND: "P.O.#"}

HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
ZOHO_ID_RE = re.compile(r"^[1-9][0-9]*$")
MAX_PDF_BYTES = 8 * 1024 * 1024
STABLE_READS = 3
CENT = Decimal("0.01")

# ---------------------------------------------------------------------------
# The immutable scope artifact and its three sources. Hashes are hard-coded so a
# rewritten artifact cannot widen anything: the artifact is verified to still
# AGREE with the constants below, and the constants -- never the file -- decide
# what is reachable.
# ---------------------------------------------------------------------------

SCOPE_ARTIFACT = WORKING / "historical_client_po_reference_fixed_scope_20260812.json"
SCOPE_ARTIFACT_SHA256 = "0905014d92d68939abdefe6f827a2dbe8e4f38c205a1bc2637b12cb28482df1e"
SOURCE_FILES = {
    "audit": (
        WORKING / "zoho_order_reference_audit" / "20260812T202705Z" / "order_reference_audit.json",
        "2ba66cdd77ff2b28bf34232610e5210de80b1da2293ee2da54d294e8c1bb22eb",
    ),
    "recovery": (
        WORKING / "zoho_client_po_recovery" / "20260812T204642Z" / "client_po_recovery.json",
        "69d5078629400e8a7e751f84e22ed6691f813e67645f2e271a2ef76a43b780fe",
    ),
    "repair_summary": (
        WORKING / "historical_client_po_repair_set_20260812.json",
        "01c407eea73745e601a78469e35ad22373268523adda23fce3fdba37c88537f0",
    ),
}
CONTRACT_DIR = WORKING / "historical_client_po_reference_contract"
CONTRACT_FILES = {
    "sales-order.yml": "7202417f6b78bca32632cf377bedd4dca085ef93157e9a4c60b1b3901e294ea4",
    "invoices.yml": "83a757a84965d5d4c74b0c60c36e128feefd3da2e90ddc4f1243e1d8da1828ed",
}

# ---------------------------------------------------------------------------
# The six fixed cases. Externally evidenced PO spellings, never the recovery
# tool's normalized digits: PO5079 and PO26078 keep their prefixes because the
# customer's own message or attached PO (and, for PO5079, the already-correct linked
# invoice) spell them that way.
# ---------------------------------------------------------------------------

CASES = (
    {
        "case_key": "so00013-po104662",
        "target": "104662",
        "customer_id": "96274000000060019",
        "customer_name": "Troy Dualam Services Inc.",
        "evidence": {
            "source_type": "outlook_customer_message_linked_to_quote_or_order",
            "subject": "Update - PO 104662 (FRP DEPOTS)",
            "attachment_name": None,
            "exact_spelling_source": None,
        },
    },
    {
        "case_key": "so00016-po5072",
        "target": "PO5072",
        "customer_id": "96274000000060001",
        "customer_name": "Troy Dualam Inc.",
        "evidence": {
            "source_type": "outlook_customer_message_linked_to_quote_or_order",
            "subject": "RE: PO5072",
            "attachment_name": None,
            "exact_spelling_source": None,
        },
    },
    {
        "case_key": "so00019-po5079",
        "target": "PO5079",
        "customer_id": "96274000000060001",
        "customer_name": "Troy Dualam Inc.",
        "evidence": {
            "source_type": "outlook_customer_message_linked_to_quote_or_order_plus_existing_linked_invoice",
            "subject": "PO 5079",
            "attachment_name": None,
            "exact_spelling_source": "INV-000020 live Reference# already equals PO5079",
        },
    },
    {
        "case_key": "so00021-po26078",
        "target": "PO26078",
        "customer_id": "96274000000186533",
        "customer_name": "Structural Composites Technologies Ltd",
        "evidence": {
            "source_type": "outlook_customer_attachment_linked_to_quote_or_order",
            "subject": 'FW: New PO26078 - 10" FW PIPE D470',
            "attachment_name": "PO26078-FRP Depot-D470 Pipe.pdf",
            "exact_spelling_source": None,
        },
    },
    {
        "case_key": "so00040-po2127",
        "target": "2127",
        "customer_id": "96274000001026001",
        "customer_name": "Fibre Mauricie",
        "evidence": {
            "source_type": "outlook_customer_attachment_linked_to_quote_or_order",
            "subject": "Payment - please approve",
            "attachment_name": "3304817439 July 162026 [amount redacted].pdf",
            "exact_spelling_source": None,
        },
    },
    {
        "case_key": "so00044-po4500021643",
        "target": "4500021643",
        "customer_id": "96274000001140071",
        "customer_name": "Ex Trade LLC",
        "evidence": {
            "source_type": "outlook_customer_message_linked_to_quote_or_order",
            "subject": "Fwd: Invoice - INV-000043 from FRP DEPOTS",
            "attachment_name": None,
            "exact_spelling_source": None,
        },
    },
)
CASES_BY_KEY = {case["case_key"]: case for case in CASES}

# The exact twelve writable records. `record_key` is the ONLY selector a caller
# may supply, and it can name nothing else.
WRITABLE_RECORDS = (
    {
        "record_key": "so00013-order", "case_key": "so00013-po104662", "kind": SALESORDER_KIND,
        "record_id": "96274000000317001", "number": "SO-00013", "before": "QT-000012",
        "currency_code": "CAD", "status": "invoiced", "order_status": "closed",
        "invoiced_status": "invoiced", "shipped_status": "shipped", "paid_status": "paid",
        "linked": ("inv000014-invoice",),
    },
    {
        "record_key": "inv000014-invoice", "case_key": "so00013-po104662", "kind": INVOICE_KIND,
        "record_id": "96274000000312107", "number": "INV-000014", "before": "SO-00013",
        "currency_code": "CAD", "status": "paid",
        "salesorder_id": "96274000000317001", "salesorder_number": "SO-00013",
        "linked": ("so00013-order",),
    },
    {
        "record_key": "so00016-order", "case_key": "so00016-po5072", "kind": SALESORDER_KIND,
        "record_id": "96274000000409073", "number": "SO-00016", "before": "QT-000015",
        "currency_code": "CAD", "status": "invoiced", "order_status": "closed",
        "invoiced_status": "invoiced", "shipped_status": "shipped", "paid_status": "paid",
        "linked": ("inv000018-invoice",),
    },
    {
        "record_key": "inv000018-invoice", "case_key": "so00016-po5072", "kind": INVOICE_KIND,
        "record_id": "96274000000411047", "number": "INV-000018", "before": "SO-00016",
        "currency_code": "CAD", "status": "paid",
        "salesorder_id": "96274000000409073", "salesorder_number": "SO-00016",
        "linked": ("so00016-order",),
    },
    {
        "record_key": "so00019-order", "case_key": "so00019-po5079", "kind": SALESORDER_KIND,
        "record_id": "96274000000466136", "number": "SO-00019", "before": "QT-000016",
        "currency_code": "CAD", "status": "invoiced", "order_status": "closed",
        "invoiced_status": "invoiced", "shipped_status": "shipped", "paid_status": "paid",
        "linked": ("inv000020-invoice-verify-only",),
    },
    {
        "record_key": "so00021-order", "case_key": "so00021-po26078", "kind": SALESORDER_KIND,
        "record_id": "96274000000575001", "number": "SO-00021", "before": "QT-000017",
        "currency_code": "CAD", "status": "invoiced", "order_status": "closed",
        "invoiced_status": "invoiced", "shipped_status": "shipped", "paid_status": "paid",
        "linked": ("inv000023-invoice",),
    },
    {
        "record_key": "inv000023-invoice", "case_key": "so00021-po26078", "kind": INVOICE_KIND,
        "record_id": "96274000000579007", "number": "INV-000023", "before": "SO-00021",
        "currency_code": "CAD", "status": "paid",
        "salesorder_id": "96274000000575001", "salesorder_number": "SO-00021",
        "linked": ("so00021-order",),
    },
    {
        "record_key": "so00040-order", "case_key": "so00040-po2127", "kind": SALESORDER_KIND,
        "record_id": "96274000001030001", "number": "SO-00040", "before": "QT-000022",
        "currency_code": "CAD", "status": "invoiced", "order_status": "closed",
        "invoiced_status": "invoiced", "shipped_status": "shipped", "paid_status": "paid",
        "linked": ("inv000039-invoice",),
    },
    {
        "record_key": "inv000039-invoice", "case_key": "so00040-po2127", "kind": INVOICE_KIND,
        "record_id": "96274000001052009", "number": "INV-000039", "before": "SO-00040",
        "currency_code": "CAD", "status": "paid",
        "salesorder_id": "96274000001030001", "salesorder_number": "SO-00040",
        "linked": ("so00040-order",),
    },
    {
        "record_key": "so00044-order", "case_key": "so00044-po4500021643", "kind": SALESORDER_KIND,
        "record_id": "96274000001140080", "number": "SO-00044", "before": "",
        "currency_code": "USD", "status": "invoiced", "order_status": "closed",
        "invoiced_status": "invoiced", "shipped_status": "fulfilled", "paid_status": "paid",
        "linked": ("inv000043-invoice", "inv000045-invoice"),
    },
    {
        "record_key": "inv000043-invoice", "case_key": "so00044-po4500021643", "kind": INVOICE_KIND,
        "record_id": "96274000001140095", "number": "INV-000043", "before": "SO-00044",
        "currency_code": "USD", "status": "paid",
        "salesorder_id": "96274000001140080", "salesorder_number": "SO-00044",
        "linked": ("so00044-order",),
    },
    {
        "record_key": "inv000045-invoice", "case_key": "so00044-po4500021643", "kind": INVOICE_KIND,
        "record_id": "96274000001212003", "number": "INV-000045", "before": "SO-00044",
        "currency_code": "USD", "status": "paid",
        "salesorder_id": "96274000001140080", "salesorder_number": "SO-00044",
        "linked": ("so00044-order",),
    },
)

# Fixed dependency that is READ AND CHECKED BUT NEVER WRITTEN. Its reference is
# already the correct customer PO and is the evidence for SO-00019's spelling.
VERIFY_ONLY_RECORDS = (
    {
        "record_key": "inv000020-invoice-verify-only", "case_key": "so00019-po5079",
        "kind": INVOICE_KIND, "record_id": "96274000000552009", "number": "INV-000020",
        "required_reference": "PO5079", "currency_code": "CAD", "status": "paid",
        "salesorder_id": "96274000000466136", "salesorder_number": "SO-00019",
        "linked": ("so00019-order",),
    },
)

WRITABLE_BY_KEY = {row["record_key"]: row for row in WRITABLE_RECORDS}
VERIFY_ONLY_BY_KEY = {row["record_key"]: row for row in VERIFY_ONLY_RECORDS}
ALL_BY_KEY = {**WRITABLE_BY_KEY, **VERIFY_ONLY_BY_KEY}
WRITABLE_IDS = frozenset(row["record_id"] for row in WRITABLE_RECORDS)
VERIFY_ONLY_IDS = frozenset(row["record_id"] for row in VERIFY_ONLY_RECORDS)
READABLE_IDS = WRITABLE_IDS | VERIFY_ONLY_IDS
# Every writable path this tool will ever build, precomputed so the write guard
# compares against a closed set rather than re-deriving a route from input.
WRITABLE_PATHS = frozenset(
    f"/books/v3/{SEGMENTS[row['kind']]}/{row['record_id']}" for row in WRITABLE_RECORDS
)

# ---------------------------------------------------------------------------
# Fingerprint policy
# ---------------------------------------------------------------------------

# The one field this tool changes. Exempt from the protected fingerprint and
# then asserted explicitly in both directions instead.
CHANGED_FIELD = "reference_number"
# Zoho stamps the first two on ANY update. Its invoice GET also regenerates the
# secure ``invoice_url`` on every read (measured across three consecutive GETs
# on INV-000014 on 2026-08-12), even when every business field is unchanged.
# These values are excluded from the business fingerprint and still recorded
# in the plan/receipt as observed volatile metadata.
VOLATILE_FIELDS = ("last_modified_time", "last_modified_by_id", "invoice_url")
# A sales order carries a read-only mirror of each linked invoice's reference,
# and an invoice carries a read-only mirror of its order's reference. Another
# approved plan in the same case may legitimately move the mirrored value, so
# the mirror is replaced by a sentinel inside the fingerprint and then checked
# against a CLOSED set of allowed values -- never simply excused.
MIRROR_FIELD = {SALESORDER_KIND: "invoices", INVOICE_KIND: "salesorders"}
MIRROR_ID_FIELD = {SALESORDER_KIND: "invoice_id", INVOICE_KIND: "salesorder_id"}
MIRROR_SENTINEL = "<mirrored-reference-checked-separately>"

# Exactly the invoice line keys resent on the mandatory line payload: identity
# first, then every value Zoho documents as updatable. Nothing is invented and
# nothing live is dropped -- a key absent from the live line stays absent.
LINE_PUT_KEYS = (
    "line_item_id", "item_id", "salesorder_item_id", "name", "description",
    "quantity", "rate", "discount", "tax_id", "unit", "item_order",
)

PLAN_FIELDS = {
    "schema_version", "tool", "tool_version", "action", "created_utc", "expires_utc",
    "nonce", "approval_required", "record_key", "case_key", "organization", "selection",
    "endpoint", "payload", "risk", "source_evidence", "live_evidence", "rendered_before",
    "sha256",
}
ORGANIZATION_FIELDS = {"organization_id", "name"}
SELECTION_FIELDS = {
    "kind", "record_id", "number", "customer_id", "customer_name",
    "before_reference", "target_reference", "currency_code", "dependencies",
}
ENDPOINT_FIELDS = {"method", "path"}
RISK_FIELDS = {"atomic", "write_count", "batch", "rollback", "note"}
SOURCE_FIELDS = {"scope_artifact", "sources", "api_contract", "evidence"}
LIVE_FIELDS = {"stable_read_count", "stable_fingerprint", "record", "dependencies"}
RENDERED_FIELDS = {
    "proven", "endpoint", "sha256", "bytes", "label", "label_present",
    "displayed_reference", "before_visible", "target_visible",
}

RISK_NOTE = (
    "ONE record, ONE field, ONE PUT, attempted exactly once. Plans are "
    "INDEPENDENT AND NOT ATOMIC AS A BATCH: an earlier approved plan that "
    "already succeeded stays applied if a later one fails. Any failure, "
    "timeout, readback mismatch or unproven rendered document permanently locks "
    "this plan with no retry, rollback or cleanup -- reconcile in Zoho by hand. "
    "This action never changes a document number, customer, status, lifecycle, "
    "date, currency, total, balance, payment, credit, tax, address, note, term, "
    "custom field or line, never adds or removes a line, never touches any "
    "record outside the fixed twelve, and never writes INV-000020. There is no "
    "mail transport, no browser route and no attachment route anywhere in this "
    "module."
)


class ClientPoReferenceError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ClientPoReferenceError(f"Required evidence is unreadable: {path}") from exc


def read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClientPoReferenceError(f"{label} is unreadable.") from exc


def json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ClientPoReferenceError("Zoho returned non-JSON evidence.") from exc


def text_of(value: Any) -> str:
    return str(value) if value is not None else ""


def money_text(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ClientPoReferenceError(f"{label} is not numeric.")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ClientPoReferenceError(f"{label} is invalid.") from exc
    if not result.is_finite():
        raise ClientPoReferenceError(f"{label} must be finite.")
    return format(result.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def positive_id(value: Any, label: str) -> str:
    value_text = text_of(value)
    if not ZOHO_ID_RE.fullmatch(value_text):
        raise ClientPoReferenceError(f"{label} must be a positive Zoho ID.")
    return value_text


def closed_fields(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        actual = set(value) if isinstance(value, dict) else set()
        raise ClientPoReferenceError(
            f"{label} must use the exact closed schema; "
            f"missing={sorted(expected - actual)}, unsupported={sorted(actual - expected)}."
        )
    return value


def parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or value != value.strip():
        raise ClientPoReferenceError(f"{label} must be unpadded timestamp text.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClientPoReferenceError(f"{label} is invalid.") from exc
    if parsed.tzinfo is None:
        raise ClientPoReferenceError(f"{label} must include a timezone.")
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Selection -- the ONLY way a record is ever named
# ---------------------------------------------------------------------------


def select_record(record_key: Any) -> dict[str, Any]:
    """Resolve one fixed selector. Nothing outside the twelve is reachable."""
    if not isinstance(record_key, str) or record_key != record_key.strip():
        raise ClientPoReferenceError("REFUSED: record key must be unpadded fixed text.")
    if record_key in VERIFY_ONLY_BY_KEY:
        raise ClientPoReferenceError(
            f"REFUSED: {record_key} is a verification-only dependency and can never be "
            "staged or committed. Its reference is already the correct customer PO."
        )
    record = WRITABLE_BY_KEY.get(record_key)
    if record is None:
        raise ClientPoReferenceError(
            "REFUSED: unknown record key. Run list-fixed to see the twelve fixed selectors."
        )
    return record


def case_of(record: dict[str, Any]) -> dict[str, Any]:
    return CASES_BY_KEY[record["case_key"]]


def target_of(record: dict[str, Any]) -> str:
    return case_of(record)["target"]


def path_of(record: dict[str, Any]) -> str:
    return f"/books/v3/{SEGMENTS[record['kind']]}/{record['record_id']}"


def dependencies_of(record: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(ALL_BY_KEY[key] for key in record["linked"])


def allowed_mirror_values(record: dict[str, Any]) -> str:
    """What a mirrored reference may read as, given fixed constants only."""
    if record["record_key"] in VERIFY_ONLY_BY_KEY:
        return record["required_reference"]
    return record["before"]


# ---------------------------------------------------------------------------
# Fixed source evidence
# ---------------------------------------------------------------------------


def verify_source_files() -> dict[str, Any]:
    """Every pinned artifact, by exact digest. Then the scope file must AGREE
    with the constants above -- it can never widen them."""
    files: dict[str, Any] = {}
    scope_actual = sha256_file(SCOPE_ARTIFACT)
    if scope_actual != SCOPE_ARTIFACT_SHA256:
        raise ClientPoReferenceError(
            f"REFUSED: fixed scope artifact SHA-256 mismatch: {SCOPE_ARTIFACT}"
        )
    files["scope_artifact"] = {
        "path": str(SCOPE_ARTIFACT), "sha256": scope_actual,
        "bytes": SCOPE_ARTIFACT.stat().st_size,
    }
    sources: dict[str, Any] = {}
    for label, (path, wanted) in SOURCE_FILES.items():
        actual = sha256_file(path)
        if actual != wanted:
            raise ClientPoReferenceError(f"REFUSED: source SHA-256 mismatch for {label}: {path}")
        sources[label] = {"path": str(path), "sha256": actual, "bytes": path.stat().st_size}
    files["sources"] = sources

    contract: dict[str, Any] = {}
    for name, wanted in CONTRACT_FILES.items():
        path = CONTRACT_DIR / name
        actual = sha256_file(path)
        if actual != wanted:
            raise ClientPoReferenceError(f"REFUSED: Zoho API contract SHA-256 mismatch: {path}")
        contract[name] = {"path": str(path), "sha256": actual, "bytes": path.stat().st_size}
    files["api_contract"] = {
        "files": contract,
        "salesorder_put_required": ["customer_id"],
        "invoice_put_required": ["customer_id", "line_items"],
        "rendered_document_read": "GET the same record with accept=pdf",
        "source": "Zoho Books published OpenAPI bundle, SHA-256 pinned above",
    }

    scope = read_json(SCOPE_ARTIFACT, "Fixed scope artifact")
    verify_scope_agrees(scope)
    return files


def verify_scope_agrees(scope: Any) -> None:
    """The artifact must still describe exactly the hard-coded twelve + one."""
    if not isinstance(scope, dict):
        raise ClientPoReferenceError("Fixed scope artifact is not an object.")
    embedded = scope.get("source_artifacts")
    if not isinstance(embedded, dict) or set(embedded) != set(SOURCE_FILES):
        raise ClientPoReferenceError("Fixed scope artifact does not carry its three sources.")
    for label, (_, wanted) in SOURCE_FILES.items():
        if str((embedded.get(label) or {}).get("sha256") or "") != wanted:
            raise ClientPoReferenceError(
                f"Fixed scope artifact no longer pins the {label} SHA-256 this tool requires."
            )
    cases = scope.get("fixed_cases")
    if not isinstance(cases, list) or len(cases) != len(CASES):
        raise ClientPoReferenceError("Fixed scope artifact case count changed.")
    seen_writable: set[str] = set()
    seen_verify: set[str] = set()
    for entry in cases:
        case = CASES_BY_KEY.get(str((entry or {}).get("key") or ""))
        if case is None:
            raise ClientPoReferenceError("Fixed scope artifact names an unknown case.")
        if str(entry.get("client_po_reference") or "") != case["target"]:
            raise ClientPoReferenceError(
                f"Fixed scope artifact target PO changed for {case['case_key']}."
            )
        if str(entry.get("customer_id") or "") != case["customer_id"]:
            raise ClientPoReferenceError(
                f"Fixed scope artifact customer changed for {case['case_key']}."
            )
        rows = [entry.get("salesorder") or {}] + list(entry.get("invoices") or [])
        for row in rows:
            record_id = str(row.get("id") or "")
            if record_id not in READABLE_IDS:
                raise ClientPoReferenceError(
                    "Fixed scope artifact names a record this tool cannot reach: " + record_id
                )
            action = str(row.get("action") or "")
            if action == "verify_only_never_write":
                seen_verify.add(record_id)
                if record_id not in VERIFY_ONLY_IDS:
                    raise ClientPoReferenceError("Verification-only record set changed.")
                continue
            if action != "change_reference":
                raise ClientPoReferenceError("Fixed scope artifact carries an unknown action.")
            seen_writable.add(record_id)
            if record_id not in WRITABLE_IDS:
                raise ClientPoReferenceError("Writable record set changed.")
            fixed = next(r for r in WRITABLE_RECORDS if r["record_id"] == record_id)
            if str(row.get("number") or "") != fixed["number"]:
                raise ClientPoReferenceError(f"Document number changed for {record_id}.")
            if str(row.get("current_reference") or "") != fixed["before"]:
                raise ClientPoReferenceError(f"Recorded before reference changed for {record_id}.")
    if seen_writable != set(WRITABLE_IDS) or seen_verify != set(VERIFY_ONLY_IDS):
        raise ClientPoReferenceError("Fixed scope artifact no longer covers exactly the fixed set.")


def build_evidence(record: dict[str, Any]) -> dict[str, Any]:
    case = case_of(record)
    return {
        "case_key": case["case_key"],
        "target_reference": case["target"],
        "customer_id": case["customer_id"],
        "customer_name": case["customer_name"],
        "source_type": case["evidence"]["source_type"],
        "subject": case["evidence"]["subject"],
        "attachment_name": case["evidence"]["attachment_name"],
        "exact_spelling_source": case["evidence"]["exact_spelling_source"],
        "note": (
            "Hash-pinned provenance recorded at staging. No mailbox is read at commit "
            "and the target is never normalized, inferred or regenerated."
        ),
    }


# ---------------------------------------------------------------------------
# Live reads
# ---------------------------------------------------------------------------


def get_record(token: str, domain: str, organization_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """GET one fixed record. The ID can only come from the fixed tables."""
    record_id = record["record_id"]
    if record_id not in READABLE_IDS:
        raise ClientPoReferenceError("REFUSED: read requested for a record outside the fixed set.")
    query = urlencode({"organization_id": organization_id})
    result = zoho_tool.api_get(token, domain, f"{path_of(record)}?{query}")
    body = result.get(RECORD_KEYS[record["kind"]]) or {}
    if text_of(body.get(f"{RECORD_KEYS[record['kind']]}_id")) != record_id:
        raise ClientPoReferenceError(f"Zoho did not return fixed record {record['number']}.")
    return body


def books_organization(token: str, domain: str, expected_id: str) -> dict[str, str]:
    result = zoho_tool.api_get(token, domain, "/books/v3/organizations")
    rows = [
        row for row in result.get("organizations") or []
        if text_of(row.get("organization_id")) == expected_id
    ]
    if len(rows) != 1:
        raise ClientPoReferenceError("Zoho did not return exactly the saved FRP Depot organization.")
    name = text_of(rows[0].get("name") or rows[0].get("organization_name"))
    if "frpdepot" not in "".join(ch for ch in name.casefold() if ch.isalnum()):
        raise ClientPoReferenceError("Saved Books organization is not FRP Depot.")
    return {"organization_id": expected_id, "name": name}


def require_identity(body: dict[str, Any], record: dict[str, Any]) -> None:
    """Exact document identity, customer and historical lifecycle state."""
    kind_key = RECORD_KEYS[record["kind"]]
    case = case_of(record) if record["record_key"] in WRITABLE_BY_KEY else None
    customer_id = case["customer_id"] if case else CASES_BY_KEY[record["case_key"]]["customer_id"]
    customer_name = case["customer_name"] if case else CASES_BY_KEY[record["case_key"]]["customer_name"]
    checks = [
        (f"{kind_key}_id", record["record_id"]),
        (f"{kind_key}_number", record["number"]),
        ("customer_id", customer_id),
        ("customer_name", customer_name),
        ("status", record["status"]),
        ("currency_code", record["currency_code"]),
    ]
    if record["kind"] == SALESORDER_KIND:
        checks.extend([
            ("order_status", record["order_status"]),
            ("invoiced_status", record["invoiced_status"]),
            ("shipped_status", record["shipped_status"]),
            ("paid_status", record["paid_status"]),
        ])
    else:
        checks.extend([
            ("salesorder_id", record["salesorder_id"]),
            ("salesorder_number", record["salesorder_number"]),
        ])
    for field, expected in checks:
        if text_of(body.get(field)) != expected:
            raise ClientPoReferenceError(
                f"REFUSED: {record['number']} {field} is {text_of(body.get(field))!r}, "
                f"not the fixed {expected!r}."
            )
    if not (body.get("line_items") or []):
        raise ClientPoReferenceError(f"REFUSED: {record['number']} returned no lines.")


def require_linkage(body: dict[str, Any], record: dict[str, Any]) -> None:
    """The fixed historical linkage stated in the scope artifact, both ways."""
    mirror_rows = body.get(MIRROR_FIELD[record["kind"]]) or []
    observed = {text_of(row.get(MIRROR_ID_FIELD[record["kind"]])) for row in mirror_rows}
    expected = {ALL_BY_KEY[key]["record_id"] for key in record["linked"]}
    if record["kind"] == SALESORDER_KIND:
        if observed != expected:
            raise ClientPoReferenceError(
                f"REFUSED: {record['number']} linked invoice set changed."
            )
    elif not expected <= observed:
        raise ClientPoReferenceError(
            f"REFUSED: {record['number']} no longer links its fixed sales order."
        )


def require_mirror_values(body: dict[str, Any], record: dict[str, Any]) -> dict[str, str]:
    """Each mirrored reference must read as its own fixed before OR target."""
    observed: dict[str, str] = {}
    for row in body.get(MIRROR_FIELD[record["kind"]]) or []:
        linked_id = text_of(row.get(MIRROR_ID_FIELD[record["kind"]]))
        linked = next((r for r in ALL_BY_KEY.values() if r["record_id"] == linked_id), None)
        if linked is None:
            raise ClientPoReferenceError(
                f"REFUSED: {record['number']} mirrors an unknown record {linked_id}."
            )
        value = text_of(row.get(CHANGED_FIELD))
        allowed = {allowed_mirror_values(linked)}
        if linked["record_key"] in WRITABLE_BY_KEY:
            allowed.add(target_of(linked))
        if value not in allowed:
            raise ClientPoReferenceError(
                f"REFUSED: {record['number']} mirrors {linked['number']} reference "
                f"{value!r}, which is neither its fixed before nor its fixed target."
            )
        observed[linked["number"]] = value
    return observed


def protected_fingerprint(body: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Every returned business field, byte-for-byte, except the one changed
    field, the two update stamps, and mirrored references (checked separately)."""
    projection = {
        key: json_copy(value)
        for key, value in body.items()
        if key != CHANGED_FIELD and key not in VOLATILE_FIELDS
    }
    mirror_field = MIRROR_FIELD[record["kind"]]
    if mirror_field in projection and isinstance(projection[mirror_field], list):
        projection[mirror_field] = [
            {**row, CHANGED_FIELD: MIRROR_SENTINEL} if isinstance(row, dict) and CHANGED_FIELD in row else row
            for row in projection[mirror_field]
        ]
    return projection


def dependency_projection(body: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """What a dependency must prove: identity, lifecycle, linkage and money."""
    kind_key = RECORD_KEYS[record["kind"]]
    projection = {
        "record_key": record["record_key"],
        "record_id": text_of(body.get(f"{kind_key}_id")),
        "number": text_of(body.get(f"{kind_key}_number")),
        "customer_id": text_of(body.get("customer_id")),
        "customer_name": text_of(body.get("customer_name")),
        "status": text_of(body.get("status")),
        "currency_code": text_of(body.get("currency_code")),
        "reference_number": text_of(body.get(CHANGED_FIELD)),
        "total": money_text(body.get("total"), f"{record['number']} total"),
        "balance": money_text(body.get("balance"), f"{record['number']} balance"),
        "line_item_ids": [text_of(line.get("line_item_id")) for line in body.get("line_items") or []],
    }
    if record["kind"] == INVOICE_KIND:
        projection["payment_made"] = money_text(
            body.get("payment_made"), f"{record['number']} payment_made"
        )
    return projection


def require_dependency(body: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    require_identity(body, record)
    require_linkage(body, record)
    projection = dependency_projection(body, record)
    if record["record_key"] in VERIFY_ONLY_BY_KEY:
        if projection["reference_number"] != record["required_reference"]:
            raise ClientPoReferenceError(
                f"REFUSED: {record['number']} must still read "
                f"{record['required_reference']!r} and reads "
                f"{projection['reference_number']!r}. It is never written by this tool."
            )
    else:
        allowed = {record["before"], target_of(record)}
        if projection["reference_number"] not in allowed:
            raise ClientPoReferenceError(
                f"REFUSED: dependency {record['number']} reference "
                f"{projection['reference_number']!r} is neither its fixed before nor target."
            )
    return projection


def live_round(
    token: str, domain: str, organization_id: str, record: dict[str, Any]
) -> dict[str, Any]:
    """One complete read of the selected record plus every fixed dependency."""
    body = get_record(token, domain, organization_id, record)
    require_identity(body, record)
    require_linkage(body, record)
    mirrors = require_mirror_values(body, record)
    dependencies = [
        require_dependency(get_record(token, domain, organization_id, dependency), dependency)
        for dependency in dependencies_of(record)
    ]
    return {
        "record": {
            "record_key": record["record_key"],
            "record_id": record["record_id"],
            "number": record["number"],
            "reference_number": text_of(body.get(CHANGED_FIELD)),
            "mirrored_references": mirrors,
            "total": money_text(body.get("total"), f"{record['number']} total"),
            "balance": money_text(body.get("balance"), f"{record['number']} balance"),
            "line_item_ids": [
                text_of(line.get("line_item_id")) for line in body.get("line_items") or []
            ],
            "protected": protected_fingerprint(body, record),
            "protected_sha256": digest_for(protected_fingerprint(body, record)),
            "volatile_observed": {key: json_copy(body.get(key)) for key in VOLATILE_FIELDS},
        },
        "dependencies": dependencies,
    }


def stable_business_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return only the state that must be equal across reads and at commit.

    Volatile metadata remains visible in each rehearsal/plan/receipt, but it is
    not allowed to re-enter the stable digest after protected_fingerprint has
    deliberately excluded it.
    """
    record = json_copy(state["record"])
    record.pop("volatile_observed", None)
    return {
        "record": record,
        "dependencies": json_copy(state["dependencies"]),
    }


def stable_rehearsal(
    token: str, domain: str, organization_id: str, record: dict[str, Any], reads: int = STABLE_READS
) -> dict[str, Any]:
    """Read the whole dependency set N times and refuse business-state drift."""
    rounds = [live_round(token, domain, organization_id, record) for _ in range(reads)]
    fingerprints = [digest_for(stable_business_state(row)) for row in rounds]
    if len(set(fingerprints)) != 1:
        raise ClientPoReferenceError(
            "Live record state moved during the bounded read-only rehearsal; no plan staged."
        )
    return {"stable_read_count": reads, "stable_fingerprint": fingerprints[0], **rounds[-1]}


def require_before_reference(state: dict[str, Any], record: dict[str, Any]) -> None:
    """The live value must be the fixed before. Already-correct is reported,
    never staged. A third value is drift."""
    live = state["record"]["reference_number"]
    target = target_of(record)
    if live == target:
        raise ClientPoReferenceError(
            f"ALREADY CORRECT: {record['number']} already reads {target!r}. "
            "Nothing staged and nothing to approve."
        )
    if live != record["before"]:
        raise ClientPoReferenceError(
            f"REFUSED: {record['number']} reference is {live!r}, which is neither the "
            f"fixed before {record['before']!r} nor the fixed target {target!r}."
        )


# ---------------------------------------------------------------------------
# Rendered customer-facing document -- GET only
# ---------------------------------------------------------------------------


def _transport(request: Request, label: str) -> tuple[bytes, str]:
    """The ONE network call site in this module. Every caller has already been
    through its own guard; nothing here constructs a route."""
    try:
        with urlopen(request, timeout=90) as response:
            raw = response.read(MAX_PDF_BYTES + 1)
            return raw, text_of(response.headers.get("Content-Type"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise ClientPoReferenceError(f"Zoho {label} failed with HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ClientPoReferenceError(f"Zoho {label} is indeterminate: {exc}") from exc


def rendered_endpoint(record: dict[str, Any], organization_id: str) -> str:
    return f"{path_of(record)}?" + urlencode({"organization_id": organization_id, "accept": "pdf"})


def fetch_rendered_pdf(
    token: str, domain: str, organization_id: str, record: dict[str, Any]
) -> tuple[bytes, str]:
    """Bounded read-only fetch of the exact document the customer sees."""
    if record["record_id"] not in READABLE_IDS:
        raise ClientPoReferenceError("REFUSED: rendered read requested outside the fixed set.")
    path = rendered_endpoint(record, organization_id)
    request = Request(
        domain.rstrip("/") + path,
        headers={"Authorization": f"Zoho-oauthtoken {token}", "Accept": "application/pdf"},
        method="GET",
    )
    raw, content_type = _transport(request, f"rendered document read for {record['number']}")
    if len(raw) > MAX_PDF_BYTES:
        raise ClientPoReferenceError(
            f"Rendered document for {record['number']} exceeds the {MAX_PDF_BYTES}-byte bound."
        )
    if not raw.startswith(b"%PDF-"):
        raise ClientPoReferenceError(
            f"Zoho did not return a rendered PDF for {record['number']} "
            f"(content type {content_type!r})."
        )
    return raw, path


def extract_pdf_text(raw: bytes) -> str:
    """PyMuPDF only. Poppler is deliberately never used."""
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - venv carries PyMuPDF
        raise ClientPoReferenceError("PyMuPDF (fitz) is unavailable; rendered check NOT PROVEN.") from exc
    try:
        with fitz.open(stream=raw, filetype="pdf") as document:
            return "\n".join(page.get_text() for page in document)
    except Exception as exc:  # noqa: BLE001 - any extraction failure is a refusal
        raise ClientPoReferenceError(f"Rendered document text could not be extracted: {exc}") from exc


def displayed_reference(text: str, record: dict[str, Any]) -> tuple[bool, str]:
    """Read the value Zoho printed under this document type's own caption."""
    flat = re.sub(r"\s+", " ", text)
    label = PDF_LABELS[record["kind"]]
    match = re.search(re.escape(label) + r"\s*:\s*(\S+)", flat)
    if not match:
        return False, ""
    return True, match.group(1)


def rendered_evidence(
    raw: bytes, path: str, record: dict[str, Any], expected: str
) -> dict[str, Any]:
    """What the customer-facing document actually shows right now."""
    text = extract_pdf_text(raw)
    present, shown = displayed_reference(text, record)
    return {
        "proven": True,
        "endpoint": path,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "label": PDF_LABELS[record["kind"]],
        "label_present": present,
        "displayed_reference": shown,
        "before_visible": bool(record["before"]) and record["before"] in text,
        "target_visible": bool(expected) and expected in text,
    }


def require_rendered_before(evidence: dict[str, Any], record: dict[str, Any]) -> None:
    """Prove the wrong value is what the customer sees today -- or that the
    caption is genuinely absent because the field is blank."""
    if record["before"]:
        if not evidence["label_present"]:
            raise ClientPoReferenceError(
                f"Rendered verification NOT PROVEN: {record['number']} shows no "
                f"{PDF_LABELS[record['kind']]} caption although its reference is "
                f"{record['before']!r}. No committable plan is created."
            )
        if evidence["displayed_reference"] != record["before"]:
            raise ClientPoReferenceError(
                f"Rendered verification NOT PROVEN: {record['number']} displays "
                f"{evidence['displayed_reference']!r} under {PDF_LABELS[record['kind']]}, "
                f"not the fixed before {record['before']!r}."
            )
    elif evidence["label_present"]:
        raise ClientPoReferenceError(
            f"Rendered verification NOT PROVEN: {record['number']} has a blank reference "
            f"but its document already prints a {PDF_LABELS[record['kind']]} caption."
        )


def require_rendered_after(evidence: dict[str, Any], record: dict[str, Any], target: str) -> None:
    """After the write the customer must actually see the client PO."""
    if not evidence["target_visible"]:
        raise ClientPoReferenceError(
            f"Rendered document for {record['number']} does not show the approved client "
            f"PO {target!r}; the result is indeterminate."
        )
    if not evidence["label_present"]:
        raise ClientPoReferenceError(
            f"Rendered document for {record['number']} shows no "
            f"{PDF_LABELS[record['kind']]} caption after the write; result indeterminate."
        )
    if evidence["displayed_reference"] != target:
        raise ClientPoReferenceError(
            f"Rendered document for {record['number']} still displays "
            f"{evidence['displayed_reference']!r} under {PDF_LABELS[record['kind']]} "
            f"instead of {target!r}; the result is indeterminate."
        )


# ---------------------------------------------------------------------------
# The write payload -- only what Zoho documents as mandatory, plus the one field
# ---------------------------------------------------------------------------


def build_payload(body: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    target = target_of(record)
    customer_id = case_of(record)["customer_id"]
    payload: dict[str, Any] = {"customer_id": customer_id, CHANGED_FIELD: target}
    if record["kind"] == SALESORDER_KIND:
        # Zoho requires only customer_id here, so no line ever enters the body
        # and omission cannot reach a line at all.
        return payload
    lines = body.get("line_items") or []
    if not lines:
        raise ClientPoReferenceError(f"REFUSED: {record['number']} has no line to preserve.")
    payload["line_items"] = [
        {key: json_copy(line[key]) for key in LINE_PUT_KEYS if key in line and line[key] not in (None, "")}
        | {"line_item_id": text_of(line.get("line_item_id")), "item_id": text_of(line.get("item_id"))}
        for line in lines
    ]
    for index, (entry, line) in enumerate(zip(payload["line_items"], lines)):
        if not entry["line_item_id"] or not entry["item_id"]:
            raise ClientPoReferenceError(
                f"REFUSED: {record['number']} line {index + 1} lacks a stable identity."
            )
        if entry["line_item_id"] != text_of(line.get("line_item_id")):
            raise ClientPoReferenceError("REFUSED: line identity was not preserved.")
    if len(payload["line_items"]) != len(lines):
        raise ClientPoReferenceError("REFUSED: every live line must be resent exactly once.")
    return payload


def validate_payload(payload: Any, record: dict[str, Any], live_line_ids: list[str]) -> dict[str, Any]:
    """A payload may carry the mandatory fields and nothing else, ever."""
    if not isinstance(payload, dict):
        raise ClientPoReferenceError("Plan payload is not an object.")
    expected_keys = {"customer_id", CHANGED_FIELD}
    if record["kind"] == INVOICE_KIND:
        expected_keys.add("line_items")
    if set(payload) != expected_keys:
        raise ClientPoReferenceError(
            f"Plan payload must carry exactly {sorted(expected_keys)}; got {sorted(payload)}."
        )
    if payload["customer_id"] != case_of(record)["customer_id"]:
        raise ClientPoReferenceError("Plan payload customer is not the fixed customer.")
    if payload[CHANGED_FIELD] != target_of(record):
        raise ClientPoReferenceError("Plan payload target is not the fixed evidenced client PO.")
    if record["kind"] == SALESORDER_KIND:
        return payload
    lines = payload["line_items"]
    if not isinstance(lines, list) or not lines:
        raise ClientPoReferenceError("Invoice payload must resend every live line.")
    if [text_of(line.get("line_item_id")) for line in lines] != live_line_ids:
        raise ClientPoReferenceError(
            "Invoice payload line identity or order differs from the live invoice."
        )
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise ClientPoReferenceError(f"Invoice payload line {index + 1} is not an object.")
        unsupported = set(line) - set(LINE_PUT_KEYS)
        if unsupported:
            raise ClientPoReferenceError(
                f"Invoice payload line {index + 1} carries unsupported keys: {sorted(unsupported)}."
            )
    return payload


# ---------------------------------------------------------------------------
# Plan lifecycle
# ---------------------------------------------------------------------------


def build_risk() -> dict[str, Any]:
    return {
        "atomic": True,
        "write_count": 1,
        "batch": False,
        "rollback": False,
        "note": RISK_NOTE,
    }


def validate_plan(plan: Any) -> dict[str, Any]:
    plan = closed_fields(plan, PLAN_FIELDS, "plan")
    saved_hash = plan["sha256"]
    if not isinstance(saved_hash, str) or not HEX_64_RE.fullmatch(saved_hash):
        raise ClientPoReferenceError("Plan SHA-256 is malformed.")
    unsigned = dict(plan)
    unsigned.pop("sha256")
    if digest_for(unsigned) != saved_hash:
        raise ClientPoReferenceError("Plan hash check failed; the staged plan changed.")
    if (plan["schema_version"], plan["tool"], plan["tool_version"], plan["action"]) != (
        SCHEMA_VERSION, TOOL_NAME, TOOL_VERSION, ACTION
    ):
        raise ClientPoReferenceError("Plan belongs to another tool, version or action.")
    if plan["approval_required"] != APPROVAL_WORD:
        raise ClientPoReferenceError("Plan approval requirement changed.")
    if not isinstance(plan["nonce"], str) or not NONCE_RE.fullmatch(plan["nonce"]):
        raise ClientPoReferenceError("Plan nonce is malformed.")
    created = parse_utc(plan["created_utc"], "created_utc")
    expires = parse_utc(plan["expires_utc"], "expires_utc")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise ClientPoReferenceError("Plan lifetime is not exactly 24 hours.")

    record = select_record(plan["record_key"])
    if plan["case_key"] != record["case_key"]:
        raise ClientPoReferenceError("Plan case does not match its fixed record.")
    case = case_of(record)

    organization = closed_fields(plan["organization"], ORGANIZATION_FIELDS, "organization")
    positive_id(organization["organization_id"], "organization ID")

    selection = closed_fields(plan["selection"], SELECTION_FIELDS, "selection")
    expected_selection = {
        "kind": record["kind"],
        "record_id": record["record_id"],
        "number": record["number"],
        "customer_id": case["customer_id"],
        "customer_name": case["customer_name"],
        "before_reference": record["before"],
        "target_reference": case["target"],
        "currency_code": record["currency_code"],
        "dependencies": [ALL_BY_KEY[key]["number"] for key in record["linked"]],
    }
    if selection != expected_selection:
        raise ClientPoReferenceError("Plan selection does not match the fixed record constants.")

    endpoint = closed_fields(plan["endpoint"], ENDPOINT_FIELDS, "endpoint")
    if endpoint["method"] != "PUT" or endpoint["path"] != path_of(record):
        raise ClientPoReferenceError("Plan endpoint is not this record's one fixed write route.")
    if endpoint["path"] not in WRITABLE_PATHS:
        raise ClientPoReferenceError("Plan endpoint is outside the fixed write route set.")

    live = closed_fields(plan["live_evidence"], LIVE_FIELDS, "live_evidence")
    if live["stable_read_count"] != STABLE_READS or not HEX_64_RE.fullmatch(
        text_of(live["stable_fingerprint"])
    ):
        raise ClientPoReferenceError("Plan lacks the bounded stable read rehearsal.")
    if digest_for(stable_business_state(live)) != live["stable_fingerprint"]:
        raise ClientPoReferenceError("Plan stable live fingerprint is inconsistent.")
    if live["record"]["reference_number"] != record["before"]:
        raise ClientPoReferenceError("Plan live evidence does not show the fixed before reference.")

    validate_payload(plan["payload"], record, list(live["record"]["line_item_ids"]))

    if plan["risk"] != build_risk():
        raise ClientPoReferenceError("Plan risk disclosure changed.")

    source = closed_fields(plan["source_evidence"], SOURCE_FIELDS, "source_evidence")
    if source["evidence"] != build_evidence(record):
        raise ClientPoReferenceError("Plan external evidence provenance changed.")
    if text_of((source["scope_artifact"] or {}).get("sha256")) != SCOPE_ARTIFACT_SHA256:
        raise ClientPoReferenceError("Plan does not pin the fixed scope artifact digest.")
    for label, (_, wanted) in SOURCE_FILES.items():
        if text_of(((source["sources"] or {}).get(label) or {}).get("sha256")) != wanted:
            raise ClientPoReferenceError(f"Plan does not pin the {label} digest.")

    rendered = closed_fields(plan["rendered_before"], RENDERED_FIELDS, "rendered_before")
    if rendered["proven"] is not True:
        raise ClientPoReferenceError(
            "Plan rendered verification is NOT PROVEN and can never be committed."
        )
    if rendered["label"] != PDF_LABELS[record["kind"]]:
        raise ClientPoReferenceError("Plan rendered caption is not this document type's caption.")
    require_rendered_before(rendered, record)
    return plan


def write_plan(core: dict[str, Any]) -> Path:
    plan = dict(core)
    plan["sha256"] = digest_for(core)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    path = PLAN_DIR / (
        f"{utc_now().strftime('%Y%m%dT%H%M%SZ')}_{plan['record_key']}_{plan['sha256'][:12]}.json"
    )
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt("zoho_historical_client_po_reference_plan_staged", str(path))
    return path


def load_plan(path_text: str) -> dict[str, Any]:
    path = Path(path_text).expanduser().resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClientPoReferenceError(f"Plan is unreadable: {path}") from exc
    return validate_plan(raw)


def lock_path(plan: dict[str, Any]) -> Path:
    return LOCK_DIR / f"{plan['sha256']}.json"


def acquire_lock(plan: dict[str, Any]) -> Path:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = lock_path(plan)
    record = {
        "plan_sha256": plan["sha256"], "action": ACTION, "record_key": plan["record_key"],
        "record_id": plan["selection"]["record_id"], "number": plan["selection"]["number"],
        "locked_utc": utc_now().isoformat(), "state": "commit_started",
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ClientPoReferenceError(
            "REFUSED: this plan is already commit-locked; no retry is allowed."
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def update_lock(path: Path, state: str, details: dict[str, Any]) -> None:
    current = json.loads(path.read_text(encoding="utf-8"))
    current.update({"state": state, "updated_utc": utc_now().isoformat(), "details": details})
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_scopes_before_lock(record: dict[str, Any]) -> dict[str, Any]:
    vault = zoho_tool.load_vault()
    scopes = set(str(scope) for scope in vault.get("scopes") or [])
    needed = UPDATE_SCOPES[record["kind"]]
    if needed not in scopes:
        raise ClientPoReferenceError(
            f"REFUSED BEFORE LOCK: the saved Zoho connection lacks {needed}. "
            "Run PREPARE_DADO_ZOHO_ACCESS.bat, create a fresh one-time grant code in the "
            "Zoho API console, then run REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat."
        )
    if READ_SCOPES[record["kind"]] not in scopes:
        raise ClientPoReferenceError(
            f"REFUSED BEFORE LOCK: the saved Zoho connection lacks {READ_SCOPES[record['kind']]}."
        )
    for forbidden in (
        "ZohoBooks.salesorders.DELETE", "ZohoBooks.invoices.DELETE",
        "ZohoBooks.salesorders.ALL", "ZohoBooks.invoices.ALL", "ZohoBooks.fullaccess.all",
    ):
        if forbidden in scopes:
            raise ClientPoReferenceError(
                f"REFUSED BEFORE LOCK: the saved connection carries {forbidden}, "
                "which this tool must never run beside."
            )
    return vault


def require_domain(vault: dict[str, Any]) -> str:
    domain = text_of(vault.get("api_domain")).rstrip("/")
    if domain != EXPECTED_API_DOMAIN:
        raise ClientPoReferenceError(
            "REFUSED: the saved Zoho connection is not on the Canadian API domain."
        )
    return domain


def perform_put(
    token: str, domain: str, organization_id: str, record: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """The ONE write mechanism. Route, verb and body are all closed sets."""
    path = path_of(record)
    if path not in WRITABLE_PATHS:
        raise ClientPoReferenceError("REFUSED: write route is outside the fixed set.")
    if record["record_id"] not in WRITABLE_IDS:
        raise ClientPoReferenceError("REFUSED: write requested for a non-writable record.")
    expected_keys = {"customer_id", CHANGED_FIELD} | (
        {"line_items"} if record["kind"] == INVOICE_KIND else set()
    )
    if set(payload) != expected_keys:
        raise ClientPoReferenceError("REFUSED: write body is not the exact approved payload.")
    if payload[CHANGED_FIELD] != target_of(record):
        raise ClientPoReferenceError("REFUSED: write body target is not the fixed client PO.")
    if payload["customer_id"] != case_of(record)["customer_id"]:
        raise ClientPoReferenceError("REFUSED: write body customer is not the fixed customer.")
    request = Request(
        domain.rstrip("/") + path + "?" + urlencode({"organization_id": organization_id}),
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Zoho-oauthtoken {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    raw, _ = _transport(request, f"write for {record['number']}")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientPoReferenceError(
            f"Zoho write result for {record['number']} is unreadable: {exc}"
        ) from exc
    if result.get("code") not in (None, 0):
        raise ClientPoReferenceError(
            f"Zoho write failed: {result.get('message') or result.get('code')}"
        )
    return result


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def command_list_fixed(_: argparse.Namespace) -> None:
    rows = []
    for record in WRITABLE_RECORDS:
        case = case_of(record)
        rows.append({
            "record_key": record["record_key"],
            "kind": record["kind"],
            "number": record["number"],
            "record_id": record["record_id"],
            "customer_name": case["customer_name"],
            "before_reference": record["before"],
            "target_client_po": case["target"],
            "rendered_caption": PDF_LABELS[record["kind"]],
        })
    verify_only = [{
        "record_key": row["record_key"], "number": row["number"], "record_id": row["record_id"],
        "required_reference": row["required_reference"],
        "writable": False,
        "note": "Verification-only dependency. It can never be staged or committed.",
    } for row in VERIFY_ONLY_RECORDS]
    print(json.dumps({
        "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "writable_records": len(rows), "records": rows,
        "verification_only": verify_only,
        "one_record_per_plan": True,
        "approval_required": APPROVAL_WORD,
        "zoho_writes": 0,
    }, ensure_ascii=False, indent=2))


def command_stage(args: argparse.Namespace) -> None:
    record = select_record(args.record_key)
    files = verify_source_files()
    vault = zoho_tool.load_vault()
    scopes = set(str(scope) for scope in vault.get("scopes") or [])
    needed = UPDATE_SCOPES[record["kind"]]
    if needed not in scopes:
        raise ClientPoReferenceError(
            f"REFUSED BEFORE STAGING: the saved Zoho connection lacks {needed}, so this "
            f"{record['kind']} could never be committed. Run PREPARE_DADO_ZOHO_ACCESS.bat, "
            "create a fresh one-time grant code in the Zoho API console, then run "
            "REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat. Nothing was staged."
        )
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = require_domain(vault)
    organization_id = positive_id(vault.get("books_organization_id"), "Books organization ID")
    organization = books_organization(token, domain, organization_id)

    rehearsal = stable_rehearsal(token, domain, organization_id, record)
    require_before_reference(rehearsal, record)

    body = get_record(token, domain, organization_id, record)
    payload = build_payload(body, record)
    validate_payload(payload, record, list(rehearsal["record"]["line_item_ids"]))

    raw, pdf_path = fetch_rendered_pdf(token, domain, organization_id, record)
    rendered = rendered_evidence(raw, pdf_path, record, target_of(record))
    require_rendered_before(rendered, record)

    zoho_tool.save_vault(vault)
    case = case_of(record)
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "action": ACTION, "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16), "approval_required": APPROVAL_WORD,
        "record_key": record["record_key"], "case_key": record["case_key"],
        "organization": organization,
        "selection": {
            "kind": record["kind"], "record_id": record["record_id"], "number": record["number"],
            "customer_id": case["customer_id"], "customer_name": case["customer_name"],
            "before_reference": record["before"], "target_reference": case["target"],
            "currency_code": record["currency_code"],
            "dependencies": [ALL_BY_KEY[key]["number"] for key in record["linked"]],
        },
        "endpoint": {"method": "PUT", "path": path_of(record)},
        "payload": payload,
        "risk": build_risk(),
        "source_evidence": {
            "scope_artifact": files["scope_artifact"],
            "sources": files["sources"],
            "api_contract": files["api_contract"],
            "evidence": build_evidence(record),
        },
        "live_evidence": rehearsal,
        "rendered_before": rendered,
    }
    path = write_plan(core)
    plan = load_plan(str(path))
    print(json.dumps({
        "status": "STAGED ONLY - ZERO ZOHO WRITES",
        "plan": str(path), "sha256": plan["sha256"], "expires_utc": plan["expires_utc"],
        "approval_required": APPROVAL_WORD,
        "record": plan["selection"], "endpoint": plan["endpoint"], "payload": plan["payload"],
        "rendered_before": {
            "caption": rendered["label"], "displayed_reference": rendered["displayed_reference"],
            "label_present": rendered["label_present"], "sha256": rendered["sha256"],
        },
        "dependencies": plan["live_evidence"]["dependencies"],
        "stable_read_count": plan["live_evidence"]["stable_read_count"],
        "risk": plan["risk"],
    }, ensure_ascii=False, indent=2))


def command_commit(args: argparse.Namespace) -> None:
    if args.approval != APPROVAL_WORD:
        raise ClientPoReferenceError(
            "REFUSED: approval must be exactly unpadded uppercase APPROVED."
        )
    plan = load_plan(args.plan)
    if utc_now() > parse_utc(plan["expires_utc"], "expires_utc"):
        raise ClientPoReferenceError("REFUSED: staged plan expired; stage a fresh read-only plan.")
    record = select_record(plan["record_key"])
    if lock_path(plan).exists():
        raise ClientPoReferenceError(
            "REFUSED: this plan is already commit-locked; no retry is allowed."
        )
    verify_source_files()

    vault = require_scopes_before_lock(record)
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = require_domain(vault)
    organization_id = positive_id(vault.get("books_organization_id"), "Books organization ID")
    if organization_id != plan["organization"]["organization_id"]:
        raise ClientPoReferenceError("REFUSED BEFORE LOCK: saved organization differs from plan.")
    organization = books_organization(token, domain, organization_id)
    if organization != plan["organization"]:
        raise ClientPoReferenceError("REFUSED BEFORE LOCK: live organization differs from plan.")

    current = stable_rehearsal(token, domain, organization_id, record)
    staged = {
        key: plan["live_evidence"][key]
        for key in ("stable_read_count", "stable_fingerprint", "record", "dependencies")
    }
    if (
        current["stable_read_count"] != staged["stable_read_count"]
        or current["stable_fingerprint"] != staged["stable_fingerprint"]
        or stable_business_state(current) != stable_business_state(staged)
    ):
        raise ClientPoReferenceError(
            "REFUSED BEFORE LOCK: live record state changed since staging; stage a fresh plan."
        )
    if current["record"]["reference_number"] != record["before"]:
        raise ClientPoReferenceError("REFUSED BEFORE LOCK: live reference is no longer the fixed before.")

    zoho_tool.save_vault(vault)
    target = target_of(record)
    lock = acquire_lock(plan)
    wrote = False
    try:
        perform_put(token, domain, organization_id, record, plan["payload"])
        wrote = True

        after = get_record(token, domain, organization_id, record)
        require_identity(after, record)
        require_linkage(after, record)
        require_mirror_values(after, record)
        if text_of(after.get(CHANGED_FIELD)) != target:
            raise ClientPoReferenceError(
                f"Readback shows {text_of(after.get(CHANGED_FIELD))!r} instead of the approved "
                f"{target!r}."
            )
        before_protected = plan["live_evidence"]["record"]["protected"]
        after_protected = protected_fingerprint(after, record)
        if after_protected != before_protected:
            changed = sorted(
                key for key in set(before_protected) | set(after_protected)
                if before_protected.get(key) != after_protected.get(key)
            )
            raise ClientPoReferenceError(
                f"Protected fields changed on {record['number']}: {changed}."
            )
        after_line_ids = [text_of(line.get("line_item_id")) for line in after.get("line_items") or []]
        if after_line_ids != list(plan["live_evidence"]["record"]["line_item_ids"]):
            raise ClientPoReferenceError(f"Line identity or order changed on {record['number']}.")
        for field in ("total", "balance"):
            if money_text(after.get(field), field) != plan["live_evidence"]["record"][field]:
                raise ClientPoReferenceError(f"{record['number']} {field} changed.")

        dependencies_after = [
            require_dependency(get_record(token, domain, organization_id, dependency), dependency)
            for dependency in dependencies_of(record)
        ]

        raw, pdf_path = fetch_rendered_pdf(token, domain, organization_id, record)
        rendered = rendered_evidence(raw, pdf_path, record, target)
        require_rendered_after(rendered, record, target)

        details = {
            "record_key": record["record_key"], "record_id": record["record_id"],
            "number": record["number"], "plan_sha256": plan["sha256"],
            "target_client_po": target, "live_reference_after": text_of(after.get(CHANGED_FIELD)),
            "protected_fields_unchanged": True,
            "lines_unchanged": len(after_line_ids),
            "totals_unchanged": {
                "total": plan["live_evidence"]["record"]["total"],
                "balance": plan["live_evidence"]["record"]["balance"],
            },
            "dependencies_verified": dependencies_after,
            "rendered_after": {
                "endpoint": rendered["endpoint"], "sha256": rendered["sha256"],
                "bytes": rendered["bytes"], "caption": rendered["label"],
                "displayed_reference": rendered["displayed_reference"],
                "target_visible": rendered["target_visible"],
            },
            "volatile_after": {key: json_copy(after.get(key)) for key in VOLATILE_FIELDS},
            "write_count": 1, "emails_sent": 0, "records_changed": 1,
        }
        update_lock(lock, "verified", details)
        zoho_tool.append_receipt("zoho_historical_client_po_reference_verified", str(lock))
        print(json.dumps({
            "status": "COMMITTED AND VERIFIED", "details": details, "lock": str(lock),
        }, ensure_ascii=False, indent=2))
    except Exception as exc:
        update_lock(lock, "indeterminate", {
            "error": str(exc), "record_key": record["record_key"],
            "record_id": record["record_id"], "number": record["number"],
            "write_attempted": wrote, "no_retry": True, "emails_sent": 0,
            "guidance": (
                "Reconcile with fresh read-only Zoho reads only. This plan is permanently "
                "locked; nothing here retries, rolls back or cleans up."
            ),
        })
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list-fixed", help="Print the fixed selectors. Read-only.")
    listing.set_defaults(func=command_list_fixed)
    stage = commands.add_parser("stage", help="Read live state and stage ONE fixed record. No writes.")
    stage.add_argument("--record-key", required=True, dest="record_key")
    stage.set_defaults(func=command_stage)
    commit = commands.add_parser("commit", help="Commit ONE immutable staged plan exactly once.")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.func(args)
        return 0
    except (ClientPoReferenceError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
