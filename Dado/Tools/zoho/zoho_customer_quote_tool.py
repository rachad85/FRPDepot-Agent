#!/usr/bin/env python
"""FRP Depot Zoho Customer & Quote Draft Tool.

Commissioned by Rachad Homsi on 2026-07-23.

Allowed service writes:
- POST /books/v3/contacts (create customer only)
- POST /books/v3/estimates (create draft estimate only)
- PUT  /books/v3/estimates/{id} for the TWO fixed draft estimates named in
  CORRECTION_TARGETS, and only to rewrite each line's 10% discount from the
  numeric form Zoho read as CAD 10 into the exact percentage string "10%".
  Commissioned by Rachad on 2026-08-10 after that misreading was proven live.
- PUT  /books/v3/estimates/96274000001559037 (QT-000029) to change exactly one
  field: the quantity of line item_order 9 from 4 to 1, at the customer's
  written request. Commissioned by Rachad on 2026-08-11. See ITEM9_TARGET.
- POST /books/v3/contacts for the ONE fixed customer "SHM Marine Constructors
  JV", the record Elaine Iverson asked INV-000051 to be billed to. No field is
  caller-supplied. Commissioned by Rachad on 2026-08-12. See SHM_CUSTOMER_PAYLOAD.
- PUT  /books/v3/estimates/{id} to REVISE ONE EXISTING estimate IN PLACE, the
  general action Rachad commissioned on 2026-08-13 so an ordinary revision
  amends the customer's own quote instead of replacing it. Eligible only while
  the live estimate is exactly `draft` or `sent`; the editable surface is the
  header reference_number/date/expiry_date/notes/terms and, per EXISTING line,
  quantity/rate/discount/description/tax_id. Customer, estimate number,
  currency, exchange rate, template, salesperson, shipping charge, adjustment,
  custom fields and status are preserved, and no status field is ever sent.
  See REVISION_KIND and the section at the end of this module.

Forbidden: sending/emailing, marking sent/accepted/declined, deletes, any other
estimate update, Inventory writes, and any unapproved numeric value.
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
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import zoho_tool

TOOL_NAME = "FRP Depot Zoho Customer & Quote Draft Tool"
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_plans"
# Rachad's ruling (2026-07-26, extended to this tool 2026-08-02): his approval
# is ONE PLAIN WORD, never a checksum — the plan digest stays internal and is
# validated by load_verified_plan. The word must come FROM RACHAD'S OWN MESSAGE
# answering the staged plan (Hard Rule 3); Dado relays it, never supplies it.
APPROVAL_WORD = "APPROVED"
APPROVAL_CARD_VERSION = 1
APPROVAL_CARD_HASH_PLACEHOLDER = ""
APPROVAL_CARD_ID_PLACEHOLDER = ""
QUOTE_PLAN_SCHEMA_VERSION = 2
QUOTE_PLAN_LIFETIME_HOURS = 24
QUOTE_CREATE_OPERATION = "CREATE_DRAFT_QUOTE"
REVISION_OPERATION = "REVISE_EXISTING_ESTIMATE"
APPROVAL_REGISTRY_DIRNAME = ".approval-cards"
QUOTE_COMMIT_LOCK_DIRNAME = ".quote-commit-locks"
ALLOWED_POSTS = {
    "customer": "/books/v3/contacts",
    "quote": "/books/v3/estimates",
}
TDS_CUSTOMER_ID = "96274000000060019"
TDS_GST_QST_TAX_ID = "96274000001071139"
# *** PERCENTAGE DISCOUNTS ARE STRINGS, NEVER NUMBERS (2026-08-10). ***
# Zoho Books reads a NUMERIC line discount as a flat CAD amount and only a
# STRING carrying "%" as a percentage. Staging 10.0 for "10%" therefore took
# CAD 10.00 off each line instead of a tenth of it: QT-000029 landed at CAD
# 15,073.96 instead of 13,680.38 and QT-000030 at 6,507.31 instead of 5,929.02.
# Every percentage this tool sends is now the exact string, staging refuses a
# nonzero numeric line discount, and commit refuses a legacy plan that carries
# one before it touches the vault, the token or the network.
PERCENT_DISCOUNT_RE = re.compile(r"^(0|[1-9][0-9]?|100)(\.[0-9]{1,2})?%$")
TDS_LINE_DISCOUNT = "10%"
CUSTOMER_QUOTE_POLICIES = {
    TDS_CUSTOMER_ID: {
        "customer_name": "Troy Dualam Services Inc.",
        "province": "Quebec",
        "tax_id": TDS_GST_QST_TAX_ID,
        "tax_name": "Gst & Qst",
        "tax_percentage": 14.975,
        "line_discount": TDS_LINE_DISCOUNT,
        "line_discount_percent": Decimal("10"),
        "tax_source": (
            "Rachad's standing instruction 2026-07-30; live Zoho billing address "
            "for Troy Dualam Services Inc. is Quebec and tax group is Gst & Qst."
        ),
        "discount_source": "Rachad's standing instruction 2026-07-30: automatic 10% TDS discount.",
    }
}
CUSTOMER_FIELDS = {
    "contact_name", "company_name", "contact_type", "customer_sub_type",
    "website", "billing_address", "shipping_address", "contact_persons",
    "currency_id", "notes", "language_code",
}
ADDRESS_FIELDS = {
    "attention", "address", "street2", "city", "state", "state_code",
    "zip", "country", "phone", "fax",
}
CONTACT_PERSON_FIELDS = {
    "salutation", "first_name", "last_name", "email", "phone", "mobile",
    "designation", "department", "is_primary_contact",
}
QUOTE_FIELDS = {
    "customer_id", "date", "expiry_date", "reference_number", "line_items",
    "notes", "terms", "shipping_charge", "adjustment", "adjustment_description",
    "discount", "is_discount_before_tax", "discount_type", "template_id",
    "salesperson_id", "location_id",
}
LINE_ITEM_FIELDS = {
    "item_id", "name", "description", "quantity", "rate", "unit", "discount",
    "tax_id", "location_id",
}
SOURCE_FIELDS = {
    "quantity_source", "rate_source", "discount_source", "tax_source",
}
TOP_SOURCE_FIELDS = {
    "shipping_charge_source", "adjustment_source", "discount_source",
}

# ---------------------------------------------------------------------------
# TDS percentage-discount correction -- commissioned by Rachad 2026-08-10
#
# TWO fixed draft estimates, ONE field per line, ONE PUT per plan. The tool
# cannot reach any other estimate, any other field, or any other verb.
# ---------------------------------------------------------------------------

CORRECTION_KIND = "tds_discount_correction"
CORRECTION_SCHEMA_VERSION = 1
CORRECTION_PLAN_LIFETIME_HOURS = 24
ESTIMATE_UPDATE_SCOPE = "ZohoBooks.estimates.UPDATE"
ESTIMATE_PUT_PATH_RE = re.compile(r"^/books/v3/estimates/([1-9][0-9]*)$")
DRAFT_STATUS = "draft"
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
ID_RE = re.compile(r"^[1-9][0-9]*$")
CENT = Decimal("0.01")
WORKING_DIR = ROOT / "Dado" / "20_Working"
CORRECTION_LOCK_DIRNAME = ".commit-locks"
TOTALS_ARTIFACT = WORKING_DIR / "tds_draft_quote_totals.json"
DIAGNOSIS_ARTIFACT = ROOT / "Dado" / "30_Memory" / "tds_draft_estimates_discount_diagnosis_original_20260810.json"
# Quebec GST + QST, the tax group already on both estimates. 5 + 9.975 =
# 14.975; both the combined rate and the two components are checked against
# the live estimate's own tax breakdown before anything is staged.
GST_PERCENT = Decimal("5")
QST_PERCENT = Decimal("9.975")
COMBINED_TAX_PERCENT = Decimal("14.975")
CURRENT_WRONG_LINE_DISCOUNT = Decimal("10")
CORRECTION_RISK_NOTE = (
    "One PUT to one fixed draft estimate, changing only each line's discount from the numeric "
    "10 Zoho read as CAD 10.00 to the exact percentage string \"10%\". The complete live line "
    "list is resent with every line_item_id so nothing is added, removed or reordered. The plan "
    "is locked before the PUT and stays locked on any failure, timeout or indeterminate result; "
    "there is no retry, no rollback and no mail transport anywhere in this tool."
)
CORRECTION_TARGETS: dict[str, dict[str, Any]] = {
    "96274000001559037": {
        "estimate_number": "QT-000029",
        "reference_number": "PO 104750 / J6276",
        "customer_id": TDS_CUSTOMER_ID,
        "status": DRAFT_STATUS,
        "line_count": 11,
        # What the numeric discount actually produced, verified live 2026-08-10.
        "current_sub_total": Decimal("13110.64"),
        "current_tax_total": Decimal("1963.32"),
        "current_total": Decimal("15073.96"),
        # What Rachad approved, from the immutable totals artifact.
        "corrected_sub_total": Decimal("11898.57"),
        "corrected_tax_total": Decimal("1781.81"),
        "corrected_total": Decimal("13680.38"),
        "corrected_discount_total": Decimal("1322.07"),
        "source_plan": PLAN_DIR / "20260810T233750Z_quote_98c7ef68.json",
        "source_plan_sha256": "98c7ef68db9c2df43a37924ad6d40d551073b05040918b54c4ed61f823db2643",
    },
    "96274000001558043": {
        "estimate_number": "QT-000030",
        "reference_number": "PO 104751 / J6282",
        "customer_id": TDS_CUSTOMER_ID,
        "status": DRAFT_STATUS,
        "line_count": 7,
        "current_sub_total": Decimal("5659.76"),
        "current_tax_total": Decimal("847.55"),
        "current_total": Decimal("6507.31"),
        "corrected_sub_total": Decimal("5156.79"),
        "corrected_tax_total": Decimal("772.23"),
        "corrected_total": Decimal("5929.02"),
        "corrected_discount_total": Decimal("572.97"),
        "source_plan": PLAN_DIR / "20260810T233751Z_quote_cb27cc11.json",
        "source_plan_sha256": "cb27cc117bfc2262b3bccd04c3ef7b344897864e3a7fa14df20ca564db53210c",
    },
}
# The exact PUT surface. Every header value is the PRESERVED live value; only
# each line's discount changes. Nothing else is reachable.
CORRECTION_HEADER_KEYS = (
    "customer_id", "estimate_number", "reference_number", "date", "expiry_date",
    "notes", "terms",
)
CORRECTION_ALLOWED_PUT_KEYS = set(CORRECTION_HEADER_KEYS) | {
    "discount_type", "is_discount_before_tax", "line_items",
}
CORRECTION_REQUIRED_PUT_KEYS = {
    "customer_id", "estimate_number", "date", "discount_type",
    "is_discount_before_tax", "line_items",
}
CORRECTION_LINE_PUT_KEYS = (
    "line_item_id", "item_id", "name", "description", "quantity", "rate",
    "unit", "discount", "tax_id", "item_order",
)
CORRECTION_REQUIRED_LINE_KEYS = {
    "line_item_id", "item_id", "name", "quantity", "rate", "discount",
}
# Fields Zoho itself recomputes from the discount, plus pure telemetry. Each
# one is verified by its own explicit rule after the PUT; EVERY other returned
# field is fingerprinted byte-exact and must not move.
ESTIMATE_DERIVED_KEYS = (
    "sub_total", "sub_total_inclusive_of_tax", "discount_total", "discount_amount",
    "discount_percent", "tax_total", "total", "uninvoiced_amount", "taxes", "line_items",
    "bcy_sub_total", "bcy_discount_total", "bcy_tax_total", "bcy_total",
    "last_modified_time", "updated_time",
    "is_viewed_by_client", "client_viewed_time", "last_viewed_time",
)
# Zoho regenerates this secure client link between otherwise identical GETs.
# It is not business data and cannot be preserved by a discount-only PUT. This
# is the complete volatile allowlist: every other pre-write field still has to
# match the staged record byte-for-byte.
ESTIMATE_PREWRITE_VOLATILE_KEYS = frozenset({"estimate_url"})
ESTIMATE_LINE_DERIVED_KEYS = (
    "discount", "discounts", "discount_amount", "discount_amount_formatted",
    "item_total", "item_total_inclusive_of_tax", "line_item_total", "tax_amount",
    "line_item_taxes",
)
ESTIMATE_TAX_AMOUNT_KEYS = frozenset({"tax_amount", "tax_amount_formatted"})

# ---------------------------------------------------------------------------
# QT-000029 Item 9 quantity correction -- commissioned by Rachad 2026-08-11
#
# Jasmin Leblanc (Troy Dualam Services) asked for "Item 9 - 1 instead 4" on the
# already-sent QT-000029. ONE estimate, ONE line, ONE field, ONE PUT. The stage
# command takes no arguments at all: the estimate, the line and both quantities
# are fixed in code, so there is no business value for a caller to supply and
# nothing else in Zoho is reachable through this action.
#
# The starting state this correction requires is the VERIFIED RESULT of the
# 2026-08-10 percentage-discount correction above (its corrected_* figures),
# which is why the required current totals are asserted against that target's
# approved numbers rather than restated by hand.
# ---------------------------------------------------------------------------

ITEM9_KIND = "tds_item9_quantity_correction"
ITEM9_SCHEMA_VERSION = 1
ITEM9_PLAN_LIFETIME_HOURS = 24
ITEM9_ESTIMATE_ID = "96274000001559037"
SENT_STATUS = "sent"
# The bounded read-only stable-state rehearsal. Fixed count, fixed spacing and a
# fixed ceiling so it is testable and can never turn into an unbounded poll.
ITEM9_REHEARSAL_OBSERVATIONS = 3
ITEM9_REHEARSAL_INTERVAL_SECONDS = Decimal("2")
ITEM9_REHEARSAL_MAX_SECONDS = Decimal("30")
# Rachad's exact instruction, quoted from the customer message. This is the only
# source for the new quantity and it is fixed in code, so a plan file cannot
# invent a different request.
ITEM9_CUSTOMER_REQUEST = {
    "channel": "Outlook mailbox info@frpdepots.com",
    "from": "Jasmin Leblanc",
    "received": "2026-08-11 12:29",
    "quote": "Item 9 – 1 instead 4",
    "field": "line_items[item_order=9].quantity",
    "from_quantity": "4",
    "to_quantity": "1",
}
ITEM9_RISK_NOTE = (
    "One PUT to one fixed sent estimate, changing exactly one field: line item_order 9 "
    "(FRP ELBOW-12\"/150PSI/D411) drops from quantity 4 to quantity 1. The rate, the 10% "
    "percentage discount, the Quebec GST+QST tax and every other line are resent unchanged, "
    "and the complete 11-line list is resent with every line_item_id so nothing is added, "
    "removed, reordered or substituted. The estimate stays 'sent'; there is no status, "
    "acceptance, conversion, attachment or mail route in this tool. The plan is locked before "
    "the PUT and stays locked on any failure, timeout or indeterminate result; there is no "
    "retry and no rollback."
)
ITEM9_TARGET: dict[str, Any] = {
    "estimate_id": ITEM9_ESTIMATE_ID,
    "estimate_number": "QT-000029",
    "reference_number": "PO 104750 / J6276",
    "customer_id": TDS_CUSTOMER_ID,
    "customer_name": "Troy Dualam Services Inc.",
    "status": SENT_STATUS,
    "line_count": 11,
    # The one reachable line, pinned three ways.
    "line_item_id": "96274000001559046",
    "item_id": "96274000000030497",
    "item_order": 9,
    "item_name": "FRP ELBOW-12\"/150PSI/D411",
    "current_quantity": Decimal("4"),
    "new_quantity": Decimal("1"),
    "rate": Decimal("810.00"),
    "line_discount": TDS_LINE_DISCOUNT,
    "line_discount_percent": Decimal("10"),
    "tax_id": TDS_GST_QST_TAX_ID,
    # Required starting state -- the verified result of the discount correction.
    "current_sub_total": CORRECTION_TARGETS[ITEM9_ESTIMATE_ID]["corrected_sub_total"],
    "current_tax_total": CORRECTION_TARGETS[ITEM9_ESTIMATE_ID]["corrected_tax_total"],
    "current_total": CORRECTION_TARGETS[ITEM9_ESTIMATE_ID]["corrected_total"],
    # What this correction must produce, recomputed from scratch before staging.
    "expected_sub_total": Decimal("9711.57"),
    "expected_gst": Decimal("485.58"),
    "expected_qst": Decimal("968.73"),
    "expected_tax_total": Decimal("1454.31"),
    "expected_total": Decimal("11165.88"),
    "source_plan": CORRECTION_TARGETS[ITEM9_ESTIMATE_ID]["source_plan"],
    "source_plan_sha256": CORRECTION_TARGETS[ITEM9_ESTIMATE_ID]["source_plan_sha256"],
}
# The only fields of the ONE target line that this correction may move. Both are
# functions of the quantity and both are verified explicitly after the write;
# every other field of every line stays inside the byte-exact fingerprint.
ITEM9_TARGET_LINE_VOLATILE_KEYS = frozenset({"quantity", "quantity_formatted"})
# Header totals a QUANTITY change moves that a DISCOUNT change does not, so
# ESTIMATE_DERIVED_KEYS -- written for the discount correction and correct there
# -- does not list them. Leaving them inside the byte-exact fingerprint is what
# locked the 2026-08-11 item9 plan `indeterminate` AFTER its PUT had already
# landed correctly: the gross subtotal moved 13,220.64 -> 10,790.64 exactly as
# intended. They are exempted here and each is then asserted against the
# INDEPENDENTLY RECOMPUTED gross by verify_item9_result, which is strictly
# stronger than requiring them not to move.
ITEM9_GROSS_SUBTOTAL_KEYS = (
    "sub_total_exclusive_of_discount",
    "bcy_sub_total_exclusive_of_discount",
)
ITEM9_REHEARSAL_KEYS = frozenset({
    "observations", "interval_seconds", "max_seconds", "elapsed_seconds",
    "prewrite_sha256", "observation_prewrite_sha256", "volatile_keys_observed", "stable",
})


class DraftToolError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def plan_hash(plan_without_hash: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(plan_without_hash).encode("utf-8")).hexdigest()


def read_json(path: str) -> dict[str, Any]:
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DraftToolError(f"Input JSON is unreadable: {path}") from exc
    if not isinstance(result, dict):
        raise DraftToolError("Input JSON must contain one object.")
    return result


def reject_extra(value: dict[str, Any], allowed: set[str], label: str) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise DraftToolError(f"Unsupported {label} field(s): {', '.join(extra)}")


def clean_address(value: Any, label: str) -> dict[str, Any]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise DraftToolError(f"{label} must be an object.")
    reject_extra(value, ADDRESS_FIELDS, label)
    return {key: item for key, item in value.items() if item not in (None, "")}


def clean_contact_person(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DraftToolError(f"contact_persons[{index}] must be an object.")
    reject_extra(value, CONTACT_PERSON_FIELDS, f"contact_persons[{index}]")
    clean = {key: item for key, item in value.items() if item not in (None, "")}
    if not clean.get("email") and not clean.get("first_name") and not clean.get("last_name"):
        raise DraftToolError(f"contact_persons[{index}] needs a name or email.")
    return clean


def stage_plan(kind: str, payload: dict[str, Any], sources: dict[str, Any], summary: dict[str, Any]) -> Path:
    core = {
        "tool": TOOL_NAME,
        "kind": kind,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "sources": sources,
        "summary": summary,
    }
    digest = plan_hash(core)
    plan = dict(core)
    plan["sha256"] = digest
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{kind}_{digest[:8]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt("zoho_quote_plan_staged", str(path))
    activate_approval_cards([plan["approval_card"]], "single")
    return path


def approval_plan_digest(plan: dict[str, Any]) -> str:
    """Digest a plan whose displayed card repeats that same digest.

    ``approval_card.plan_sha256`` is a display/binding copy of the outer hash.
    Both stage and validation replace that one field with the fixed empty
    placeholder before hashing, avoiding a circular digest while keeping every
    other displayed field immutable.
    """
    core = json_copy(plan)
    core.pop("sha256", None)
    card = core.get("approval_card")
    if not isinstance(card, dict):
        raise DraftToolError("Plan approval card is missing or invalid.")
    card["plan_sha256"] = APPROVAL_CARD_HASH_PLACEHOLDER
    card["card_id"] = APPROVAL_CARD_ID_PLACEHOLDER
    return digest_for(core)


def approval_card_id(operation: str, plan_sha256: str) -> str:
    if not HEX_64_RE.fullmatch(str(plan_sha256)):
        return APPROVAL_CARD_ID_PLACEHOLDER
    prefix = "QC" if operation == QUOTE_CREATE_OPERATION else "QR"
    return f"{prefix}-{plan_sha256[:12].upper()}"


def approval_registry_path() -> Path:
    return PLAN_DIR / APPROVAL_REGISTRY_DIRNAME / "active.json"


def quote_commit_lock_path(plan_sha256: str) -> Path:
    if not HEX_64_RE.fullmatch(str(plan_sha256)):
        raise DraftToolError("Quote plan digest is invalid for replay locking.")
    return PLAN_DIR / QUOTE_COMMIT_LOCK_DIRNAME / f"{plan_sha256}.json"


def _write_json_durable(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise DraftToolError(
            "REFUSED: this plan has already entered commit and cannot be replayed."
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def activate_approval_card(card: dict[str, Any]) -> dict[str, Any]:
    """Make exactly one displayed immutable card the sole active approval."""
    digest = str(card.get("plan_sha256") or "")
    operation = str(card.get("operation") or "")
    expires = str(card.get("expires_utc") or "")
    if not HEX_64_RE.fullmatch(digest) or operation not in (
        QUOTE_CREATE_OPERATION, REVISION_OPERATION
    ):
        raise DraftToolError("Approval card identity is invalid.")
    parse_plan_time(expires, "approval-card expiry")
    entries = [{
        "plan_sha256": digest,
        "operation": operation,
        "expires_utc": expires,
        "status": "active",
    }]
    created = utc_now().isoformat()
    batch_core = {
        "version": APPROVAL_CARD_VERSION,
        "mode": "single",
        "created_utc": created,
        "approval_required": APPROVAL_WORD,
        "entries": entries,
    }
    registry = dict(batch_core)
    registry["batch_sha256"] = digest_for(batch_core)
    _write_json_durable(approval_registry_path(), registry)
    zoho_tool.append_receipt(
        "zoho_quote_approval_card_activated",
        f"mode=single; batch_sha256={registry['batch_sha256']}; plan={digest}",
    )
    return registry


def require_active_approval_card(plan: dict[str, Any]) -> dict[str, Any]:
    """Bare APPROVED is valid only for the sole current displayed card."""
    path = approval_registry_path()
    try:
        registry = read_json_value(path, "Active approval-card registry")
    except DraftToolError as exc:
        raise DraftToolError(
            "REFUSED: this plan is not the active approval card. Stage it again for review."
        ) from exc
    if not isinstance(registry, dict):
        raise DraftToolError("REFUSED: the active approval-card registry is invalid.")
    batch_sha = str(registry.get("batch_sha256") or "")
    core = dict(registry)
    core.pop("batch_sha256", None)
    if not HEX_64_RE.fullmatch(batch_sha) or not secrets.compare_digest(
        batch_sha, digest_for(core)
    ):
        raise DraftToolError("REFUSED: the active approval-card registry changed after review.")
    entries = registry.get("entries")
    mode = registry.get("mode")
    if not isinstance(entries, list) or mode != "single":
        raise DraftToolError("REFUSED: the active approval-card registry is malformed.")
    if len(entries) != 1:
        raise DraftToolError("REFUSED: the active single-card registry is ambiguous.")
    digest = str(plan.get("sha256") or "")
    operation = str((plan.get("approval_card") or {}).get("operation") or "")
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("plan_sha256") == digest]
    if len(matches) != 1:
        raise DraftToolError(
            "REFUSED: this plan is not the sole active approval card. Stage it again for review."
        )
    entry = matches[0]
    if entry.get("status") != "active" or entry.get("operation") != operation:
        raise DraftToolError("REFUSED: this approval card is consumed, superseded, or operation-mismatched.")
    if entry.get("expires_utc") != plan.get("expires_utc"):
        raise DraftToolError("REFUSED: the active approval-card expiry does not match the plan.")
    return registry


def mark_active_card_attempted(plan: dict[str, Any]) -> None:
    registry = require_active_approval_card(plan)
    digest = str(plan["sha256"])
    for entry in registry["entries"]:
        if entry["plan_sha256"] == digest:
            entry["status"] = "attempted"
            entry["attempted_utc"] = utc_now().isoformat()
            break
    core = dict(registry)
    core.pop("batch_sha256", None)
    registry["batch_sha256"] = digest_for(core)
    _write_json_durable(approval_registry_path(), registry)


def quote_approval_card(
    payload: dict[str, Any], summary: dict[str, Any], evidence: dict[str, Any], expires_utc: str,
    plan_sha256: str = APPROVAL_CARD_HASH_PLACEHOLDER,
) -> dict[str, Any]:
    """Build the permanent phone-sized card for a new Draft quote.

    The complete payload, source map, live GET evidence and arithmetic remain
    immutable inside the plan; this card is their concise hash-bound view.
    """
    customer = evidence["customer"]
    totals = evidence["totals"]
    item_names = {
        str(item_id): str(row.get("name") or item_id)
        for item_id, row in evidence["items"].items()
    }
    lines = [
        {
            "item": item_names.get(str(row.get("item_id") or ""), str(row.get("item_id") or "")),
            "quantity": row.get("quantity"),
            "rate": row.get("rate"),
            "discount": row.get("discount", 0),
            "tax": (evidence["taxes"].get(str(row.get("tax_id") or "")) or {}).get("tax_name", "None"),
            "net": row.get("line_net"),
        }
        for row in (summary.get("line_items") or [])
        if isinstance(row, dict)
    ]
    return {
        "version": APPROVAL_CARD_VERSION,
        "card_id": approval_card_id(QUOTE_CREATE_OPERATION, plan_sha256),
        "operation": QUOTE_CREATE_OPERATION,
        "scope": {
            "method": "POST",
            "route": ALLOWED_POSTS["quote"],
            "write_count": 1,
            "draft_only": True,
            "email_or_lifecycle_action": False,
        },
        "customer": {
            "id": customer["customer_id"],
            "name": customer["customer_name"],
        },
        "document": {
            "type": "quote/estimate",
            "number": "assigned by Zoho on Draft creation",
            "reference_number": payload.get("reference_number"),
            "status": "draft",
        },
        "currency": {
            "code": customer["currency_code"],
            "currency_id": customer["currency_id"],
            "basis": "live customer currency in Zoho; never overridden",
        },
        "lines": lines,
        "totals": {
            "subtotal": totals["sub_total"],
            "tax": totals["tax_total"],
            "total": totals["total"],
            "tax_certainty": totals["tax_certainty"],
        },
        "risks": [
            "One POST creates one Draft estimate only.",
            "Customer, item, currency and tax rows are read live again before the write.",
            "The created Draft is read back and checked against this card.",
            "No send, email, status, accept, decline, convert or delete route is available.",
        ],
        "expires_utc": expires_utc,
        "plan_sha256": plan_sha256,
        "approval": "Reply with exact one-word APPROVED to this displayed plan only.",
    }


def stage_quote_plan(
    payload: dict[str, Any], sources: dict[str, Any], summary: dict[str, Any],
    evidence: dict[str, Any],
) -> Path:
    created = utc_now()
    expires = (created + timedelta(hours=QUOTE_PLAN_LIFETIME_HOURS)).isoformat()
    core = {
        "tool": TOOL_NAME,
        "kind": "quote",
        "schema_version": QUOTE_PLAN_SCHEMA_VERSION,
        "nonce": secrets.token_hex(16),
        "created_utc": created.isoformat(),
        "expires_utc": expires,
        "approval_required": APPROVAL_WORD,
        "payload": payload,
        "sources": sources,
        "summary": summary,
        "live_evidence": evidence,
        "approval_card": quote_approval_card(payload, summary, evidence, expires),
    }
    digest = approval_plan_digest(core)
    plan = json_copy(core)
    plan["approval_card"]["plan_sha256"] = digest
    plan["approval_card"]["card_id"] = approval_card_id(QUOTE_CREATE_OPERATION, digest)
    plan["sha256"] = digest
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_quote_{digest[:8]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    activate_approval_card(plan["approval_card"])
    zoho_tool.append_receipt("zoho_quote_plan_staged", str(path))
    return path


def validate_quote_approval_plan(plan: dict[str, Any]) -> None:
    if (
        plan.get("tool") != TOOL_NAME
        or plan.get("kind") != "quote"
        or plan.get("schema_version") != QUOTE_PLAN_SCHEMA_VERSION
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise DraftToolError("The plan belongs to a different tool, operation or schema version.")
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise DraftToolError("Plan nonce is invalid.")
    created = parse_plan_time(plan.get("created_utc"), "creation time")
    expires = parse_plan_time(plan.get("expires_utc"), "expiry")
    if expires - created != timedelta(hours=QUOTE_PLAN_LIFETIME_HOURS):
        raise DraftToolError("Quote plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise DraftToolError("Plan creation time is in the future.")
    if now >= expires:
        raise DraftToolError("Plan expired. Stage a new Draft quote plan for review.")
    payload = plan.get("payload")
    sources = plan.get("sources")
    summary = plan.get("summary")
    evidence = plan.get("live_evidence")
    if (
        not isinstance(payload, dict) or not isinstance(sources, dict)
        or not isinstance(summary, dict) or not isinstance(evidence, dict)
    ):
        raise DraftToolError("Quote plan payload or detailed summary is invalid.")
    if payload.get("status") != "draft":
        raise DraftToolError("Quote creation plan is not Draft-only.")
    if "currency_id" in payload or "exchange_rate" in payload:
        raise DraftToolError(
            "Quote creation must preserve the live customer currency; currency and exchange-rate "
            "overrides are refused."
        )
    canonical_summary = canonical_quote_summary(payload, sources)
    if summary != canonical_summary:
        raise DraftToolError(
            "Quote summary is not the canonical projection of the immutable POST payload."
        )
    customer = evidence.get("customer")
    items = evidence.get("items")
    taxes = evidence.get("taxes")
    totals = evidence.get("totals")
    if not all(isinstance(value, dict) for value in (customer, items, taxes, totals)):
        raise DraftToolError("Quote live evidence is incomplete.")
    if (
        customer.get("customer_id") != payload.get("customer_id")
        or customer.get("status") != "active"
        or customer.get("contact_type") not in {"customer", "customer_vendor"}
    ):
        raise DraftToolError("Quote customer evidence does not match an active payload customer.")
    wanted_items = {str(line.get("item_id") or "") for line in payload["line_items"]}
    if set(items) != wanted_items or any(
        not isinstance(items[item_id], dict)
        or items[item_id].get("item_id") != item_id
        or items[item_id].get("status") != "active"
        or not str(items[item_id].get("name") or "").strip()
        for item_id in wanted_items
    ):
        raise DraftToolError("Quote item evidence does not exactly match the active payload items.")
    wanted_taxes = {
        str(line.get("tax_id") or "") for line in payload["line_items"] if line.get("tax_id")
    }
    if set(taxes) != wanted_taxes:
        raise DraftToolError("Quote tax evidence does not exactly match the payload taxes.")
    if totals != quote_totals(payload, canonical_summary, taxes):
        raise DraftToolError("Quote totals are not the canonical projection of payload and tax evidence.")
    expected_card = quote_approval_card(
        payload, canonical_summary, evidence, str(plan["expires_utc"]),
        str(plan.get("sha256") or "")
    )
    if plan.get("approval_card") != expected_card:
        raise DraftToolError(
            "Approval card does not exactly match the immutable Draft quote operation."
        )


def print_approval_card(card: dict[str, Any]) -> None:
    """Stage output is one concise immutable card, never the expanded evidence."""
    print(json.dumps({"approval_card": card}, ensure_ascii=False, indent=2))


def command_stage_customer(args: argparse.Namespace) -> None:
    raw = read_json(args.input)
    reject_extra(raw, CUSTOMER_FIELDS, "customer")
    name = str(raw.get("contact_name") or "").strip()
    if not name:
        raise DraftToolError("contact_name is required.")
    contact_type = str(raw.get("contact_type") or "customer").casefold()
    if contact_type != "customer":
        raise DraftToolError("This tool creates customers only, never vendors.")
    payload = {key: value for key, value in raw.items() if value not in (None, "", [], {})}
    payload["contact_name"] = name
    payload["contact_type"] = "customer"
    if "billing_address" in raw:
        payload["billing_address"] = clean_address(raw.get("billing_address"), "billing_address")
    if "shipping_address" in raw:
        payload["shipping_address"] = clean_address(raw.get("shipping_address"), "shipping_address")
    if "contact_persons" in raw:
        if not isinstance(raw["contact_persons"], list):
            raise DraftToolError("contact_persons must be a list.")
        payload["contact_persons"] = [
            clean_contact_person(value, index) for index, value in enumerate(raw["contact_persons"])
        ]
    summary = {
        "contact_name": name,
        "company_name": payload.get("company_name"),
        "primary_email": next(
            (person.get("email") for person in payload.get("contact_persons", []) if person.get("email")),
            None,
        ),
    }
    path = stage_plan("customer", payload, {"record_source": args.source}, summary)
    digest = json.loads(path.read_text(encoding="utf-8"))["sha256"]
    print(json.dumps({"plan": str(path), "summary": summary, "approval": APPROVAL_WORD}, indent=2))


def numeric(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DraftToolError(f"{label} must be a number.")
    return float(value)


def nonempty_source(value: Any, label: str) -> str:
    source = str(value or "").strip()
    if not source:
        raise DraftToolError(f"{label} is required. Every quote number needs a source.")
    return source


def percentage_discount(value: Any, label: str) -> Decimal:
    """The ONE accepted shape for a nonzero discount: a percentage string.

    A bare number reaches Zoho as a flat CAD amount, which is what silently
    turned "10%" into CAD 10.00 on 2026-08-10, so it is refused outright rather
    than reinterpreted. Numeric zero stays legal because it is unambiguous.
    """
    if isinstance(value, bool):
        raise DraftToolError(f"{label} must be a percentage string such as \"10%\".")
    if value is None:
        return Decimal("0")
    if isinstance(value, (int, float)):
        if float(value) == 0:
            return Decimal("0")
        raise DraftToolError(
            f"REFUSED: {label} is the number {value}. Zoho reads a numeric line discount as a "
            f"flat CAD amount, not a percentage. Write the percentage as a string, e.g. \"10%\"."
        )
    if not isinstance(value, str) or not PERCENT_DISCOUNT_RE.fullmatch(value):
        raise DraftToolError(
            f"{label} must be a percentage string between \"0%\" and \"100%\", e.g. \"10%\"."
        )
    return Decimal(value[:-1])


def canonical_quote_summary(
    payload: dict[str, Any], sources: dict[str, Any]
) -> dict[str, Any]:
    """Rebuild every displayed commercial number from the POST payload.

    The saved summary is never trusted as an independent source. Stage and
    commit both call this function, so a re-signed payload/card mismatch still
    refuses before credentials or network.
    """
    lines = payload.get("line_items")
    source_lines = sources.get("line_items") if isinstance(sources, dict) else None
    if not isinstance(lines, list) or not lines or not isinstance(source_lines, list):
        raise DraftToolError("Quote payload/source evidence has no complete line list.")
    if len(lines) != len(source_lines):
        raise DraftToolError("Quote payload and source evidence name different line counts.")
    summary_lines: list[dict[str, Any]] = []
    list_subtotal = Decimal("0")
    line_discount_total = Decimal("0")
    for index, line in enumerate(lines):
        if not isinstance(line, dict) or not isinstance(source_lines[index], dict):
            raise DraftToolError(f"Quote line {index + 1} or its source evidence is invalid.")
        quantity = live_decimal(line.get("quantity"), f"quote line {index + 1} quantity")
        rate = live_decimal(line.get("rate"), f"quote line {index + 1} rate")
        if quantity <= 0 or rate < 0:
            raise DraftToolError(f"Quote line {index + 1} has an invalid quantity or rate.")
        percent = percentage_discount(line.get("discount", 0), f"quote line {index + 1} discount")
        gross = (quantity * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        discount_amount = (gross * percent / Decimal("100")).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        list_subtotal += gross
        line_discount_total += discount_amount
        summary_lines.append({
            "item_id": str(line.get("item_id") or ""),
            "name": str(line.get("name") or line.get("description") or ""),
            "quantity": line.get("quantity"),
            "rate": line.get("rate"),
            "discount": line.get("discount", 0),
            "discount_is_percentage": bool(percent),
            "line_gross": format(gross, "f"),
            "line_discount_amount": format(discount_amount, "f"),
            "line_net": format(gross - discount_amount, "f"),
            "tax_id": line.get("tax_id"),
            "sources": json_copy(source_lines[index]),
        })
    line_net_subtotal = list_subtotal - line_discount_total
    header_percent = percentage_discount(payload.get("discount", 0), "discount")
    header_discount = (line_net_subtotal * header_percent / Decimal("100")).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    return {
        "customer_id": str(payload.get("customer_id") or ""),
        "reference_number": payload.get("reference_number"),
        "discount_semantics": (
            "A discount written as a string with % is a PERCENTAGE; a nonzero bare "
            "number is refused because Zoho reads it as flat currency."
        ),
        "line_items": summary_lines,
        "list_subtotal": format(list_subtotal, "f"),
        "line_discount_total": format(line_discount_total, "f"),
        # Backward-compatible name used by earlier reviewed plans/tests.
        "discount_total": format(line_discount_total, "f"),
        "net_subtotal_before_tax": format(line_net_subtotal, "f"),
        "header_discount_percent": format(header_percent, "f"),
        "header_discount_amount": format(header_discount, "f"),
        "sub_total": format(line_net_subtotal - header_discount, "f"),
        "shipping_charge": payload.get("shipping_charge", 0),
        "adjustment": payload.get("adjustment", 0),
        "discount": payload.get("discount", 0),
    }


def quote_customer_read(
    access_token: str, vault: dict[str, Any], customer_id: str
) -> dict[str, Any]:
    query = urlencode({"organization_id": books_organization_id(vault)})
    result = zoho_tool.api_get(
        access_token, str(vault["api_domain"]), f"/books/v3/contacts/{customer_id}?{query}"
    )
    contact = result.get("contact")
    if not isinstance(contact, dict) or str(contact.get("contact_id") or "") != customer_id:
        raise DraftToolError(f"Zoho customer {customer_id} was not found. Nothing staged.")
    status = str(contact.get("status") or "").casefold()
    contact_type = str(contact.get("contact_type") or "").casefold()
    if status != "active" or contact_type not in {"customer", "customer_vendor"}:
        raise DraftToolError(
            f"REFUSED: contact {customer_id} is {status or 'unknown'} / "
            f"{contact_type or 'unknown'}, not an active customer. Nothing staged."
        )
    name = str(contact.get("contact_name") or contact.get("company_name") or "").strip()
    if not name:
        raise DraftToolError(f"Zoho customer {customer_id} has no readable name. Nothing staged.")
    return {
        "customer_id": customer_id,
        "customer_name": name,
        "status": status,
        "contact_type": contact_type,
        "currency_code": str(contact.get("currency_code") or "").strip(),
        "currency_id": str(contact.get("currency_id") or "").strip(),
    }


def quote_item_read(
    access_token: str, vault: dict[str, Any], item_id: str
) -> dict[str, Any]:
    query = urlencode({"organization_id": books_organization_id(vault)})
    result = zoho_tool.api_get(
        access_token, str(vault["api_domain"]), f"/books/v3/items/{item_id}?{query}"
    )
    item = result.get("item")
    if not isinstance(item, dict) or str(item.get("item_id") or "") != item_id:
        raise DraftToolError(f"Zoho item {item_id} was not found. Nothing staged.")
    status = str(item.get("status") or "").casefold()
    name = str(item.get("name") or "").strip()
    if status != "active" or not name:
        raise DraftToolError(
            f"REFUSED: Zoho item {item_id} is {status or 'unknown'} or unnamed. Nothing staged."
        )
    return {
        "item_id": item_id,
        "name": name,
        "sku": str(item.get("sku") or ""),
        "status": status,
    }


def quote_totals(
    payload: dict[str, Any], summary: dict[str, Any], taxes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    sub_total = live_decimal(summary["sub_total"], "quote sub-total")
    tax_total = Decimal("0")
    tax_certainty = "deterministic_simple_tax"
    for line in summary["line_items"]:
        tax_id = str(line.get("tax_id") or "")
        if not tax_id:
            continue
        tax = taxes[tax_id]
        percentage = live_decimal(tax["tax_percentage"], f"tax {tax_id} percentage")
        line_net = live_decimal(line["line_net"], "quote line net")
        tax_total += (line_net * percentage / Decimal("100")).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        if str(tax.get("tax_type") or "").casefold() in {"tax_group", "compound_tax"}:
            tax_certainty = "disclosed_uncertain_tax_group_rounding"
    shipping = live_decimal(payload.get("shipping_charge", 0), "shipping_charge")
    adjustment = live_decimal(payload.get("adjustment", 0), "adjustment")
    total = sub_total + tax_total + shipping + adjustment
    return {
        "sub_total": money_text(sub_total),
        "tax_total": money_text(tax_total),
        "shipping_charge": money_text(shipping),
        "adjustment": money_text(adjustment),
        "total": money_text(total),
        "tax_certainty": tax_certainty,
    }


def quote_live_evidence(
    access_token: str, vault: dict[str, Any], payload: dict[str, Any], summary: dict[str, Any]
) -> dict[str, Any]:
    customer = quote_customer_read(access_token, vault, str(payload["customer_id"]))
    item_ids = sorted({str(line["item_id"]) for line in payload["line_items"]})
    items = {item_id: quote_item_read(access_token, vault, item_id) for item_id in item_ids}
    live_taxes = get_active_taxes(access_token, vault)
    wanted_taxes = sorted({
        str(line.get("tax_id") or "") for line in payload["line_items"] if line.get("tax_id")
    })
    taxes = revision_tax_evidence(live_taxes, wanted_taxes)
    currency_id = str(payload.get("currency_id") or customer["currency_id"])
    currency_code = customer["currency_code"]
    if payload.get("currency_id") and currency_id != customer["currency_id"]:
        currency_code = f"Zoho currency ID {currency_id}"
    customer = {**customer, "currency_id": currency_id, "currency_code": currency_code}
    return {
        "customer": customer,
        "items": items,
        "taxes": taxes,
        "totals": quote_totals(payload, summary, taxes),
    }


def stage_quote_live_evidence(
    payload: dict[str, Any], summary: dict[str, Any]
) -> dict[str, Any]:
    """GET-only staging evidence; no estimate is created or changed."""
    vault = zoho_tool.load_vault()
    zoho_tool.validate_scopes([str(scope) for scope in vault.get("scopes") or []])
    access_token, vault = zoho_tool.refresh_access_token(vault)
    evidence = quote_live_evidence(access_token, vault, payload, summary)
    zoho_tool.save_vault(vault)
    return evidence


def refuse_numeric_percentage_discounts(payload: dict[str, Any]) -> None:
    """Fail-closed gate for plans staged before the 2026-08-10 correction.

    Runs at commit BEFORE the vault, the token refresh and the network, so a
    legacy plan whose lines carry the numeric 10 can never reach Zoho again.
    """
    percentage_discount(payload.get("discount", 0), "discount")
    lines = payload.get("line_items")
    if not isinstance(lines, list):
        return
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            continue
        percentage_discount(line.get("discount", 0), f"line_items[{index}].discount")


def validate_quote_customer_policy(payload: dict[str, Any]) -> None:
    customer_id = str(payload.get("customer_id") or "")
    policy = CUSTOMER_QUOTE_POLICIES.get(customer_id)
    if not policy:
        return
    if payload.get("discount_type") != "item_level" or payload.get("is_discount_before_tax") is not True:
        raise DraftToolError(
            f"{policy['customer_name']} quotes must use a 10% item-level discount before tax."
        )
    lines = payload.get("line_items")
    if not isinstance(lines, list) or not lines:
        raise DraftToolError(f"{policy['customer_name']} quote has no line items.")
    for index, line in enumerate(lines):
        discount = line.get("discount", 0)
        if discount != policy["line_discount"]:
            raise DraftToolError(
                f"{policy['customer_name']} line_items[{index}] must have the automatic 10% discount "
                f"as the exact percentage string {policy['line_discount']!r}; Zoho reads the bare "
                "number as a flat CAD amount."
            )
        if str(line.get("tax_id") or "") != policy["tax_id"]:
            raise DraftToolError(
                f"{policy['customer_name']} line_items[{index}] must use Quebec combined GST + QST."
            )


def command_stage_quote(args: argparse.Namespace) -> None:
    raw = read_json(args.input)
    reject_extra(raw, QUOTE_FIELDS | TOP_SOURCE_FIELDS, "quote")
    customer_id = str(raw.get("customer_id") or "").strip()
    if not customer_id.isdigit():
        raise DraftToolError("customer_id must be the numeric Zoho customer ID.")
    policy = CUSTOMER_QUOTE_POLICIES.get(customer_id)
    raw_lines = raw.get("line_items")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise DraftToolError("At least one line item is required.")
    payload: dict[str, Any] = {
        key: value for key, value in raw.items()
        if key in QUOTE_FIELDS and key != "line_items" and value not in (None, "")
    }
    payload["customer_id"] = customer_id
    payload["status"] = "draft"
    if policy:
        if percentage_discount(payload.get("discount", 0), "discount") != 0:
            raise DraftToolError(
                f"Do not add an entity-level discount for {policy['customer_name']}; "
                "the tool applies its automatic 10% item-level discount."
            )
        payload.pop("discount", None)
        payload["discount_type"] = "item_level"
        payload["is_discount_before_tax"] = True
    sources: dict[str, Any] = {"line_items": []}
    clean_lines = []
    line_percents: list[Decimal] = []
    for index, raw_line in enumerate(raw_lines):
        if not isinstance(raw_line, dict):
            raise DraftToolError(f"line_items[{index}] must be an object.")
        reject_extra(raw_line, LINE_ITEM_FIELDS | SOURCE_FIELDS, f"line_items[{index}]")
        item_id = str(raw_line.get("item_id") or "").strip()
        if not item_id.isdigit():
            raise DraftToolError(f"line_items[{index}].item_id must be a numeric Zoho item ID.")
        quantity = numeric(raw_line.get("quantity"), f"line_items[{index}].quantity")
        rate = numeric(raw_line.get("rate"), f"line_items[{index}].rate")
        if quantity <= 0 or rate < 0:
            raise DraftToolError(f"line_items[{index}] has an invalid quantity or rate.")
        line_sources = {
            "quantity": nonempty_source(raw_line.get("quantity_source"), f"line_items[{index}].quantity_source"),
            "rate": nonempty_source(raw_line.get("rate_source"), f"line_items[{index}].rate_source"),
        }
        line_percent = percentage_discount(
            raw_line.get("discount", 0), f"line_items[{index}].discount"
        )
        if policy:
            line_sources["discount"] = policy["discount_source"]
            line_sources["tax"] = policy["tax_source"]
        elif line_percent != 0:
            line_sources["discount"] = nonempty_source(
                raw_line.get("discount_source"), f"line_items[{index}].discount_source"
            )
        if not policy and raw_line.get("tax_id"):
            line_sources["tax"] = nonempty_source(
                raw_line.get("tax_source"), f"line_items[{index}].tax_source"
            )
        clean_line = {
            key: value for key, value in raw_line.items()
            if key in LINE_ITEM_FIELDS and value not in (None, "")
        }
        clean_line["item_id"] = item_id
        clean_line["quantity"] = quantity
        clean_line["rate"] = rate
        if policy:
            clean_line["discount"] = policy["line_discount"]
            clean_line["tax_id"] = policy["tax_id"]
            line_percents.append(Decimal(policy["line_discount_percent"]))
        else:
            line_percents.append(line_percent)
        clean_lines.append(clean_line)
        sources["line_items"].append(line_sources)
    payload["line_items"] = clean_lines
    validate_quote_customer_policy(payload)
    refuse_numeric_percentage_discounts(payload)
    if "discount" in payload:
        percent = percentage_discount(payload["discount"], "discount")
        if percent != 0:
            sources["discount"] = nonempty_source(raw.get("discount_source"), "discount_source")
    for field in ("shipping_charge", "adjustment"):
        if field in payload:
            value = numeric(payload[field], field)
            payload[field] = value
            if value != 0:
                source_field = f"{field}_source"
                sources[field] = nonempty_source(raw.get(source_field), source_field)
    summary = canonical_quote_summary(payload, sources)
    evidence = stage_quote_live_evidence(payload, summary)
    path = stage_quote_plan(payload, sources, summary, evidence)
    plan = read_json(str(path))
    print_approval_card(plan["approval_card"])


def load_verified_plan(path: str, expected_kind: str) -> dict[str, Any]:
    plan = read_json(path)
    saved_hash = str(plan.get("sha256") or "")
    if expected_kind == "quote" and isinstance(plan.get("approval_card"), dict):
        actual_hash = approval_plan_digest(plan)
    else:
        legacy_core = dict(plan)
        legacy_core.pop("sha256", None)
        actual_hash = plan_hash(legacy_core)
    if not HEX_64_RE.fullmatch(saved_hash) or not secrets.compare_digest(saved_hash, actual_hash):
        raise DraftToolError("Plan hash check failed. The plan may have changed after review.")
    if plan.get("tool") != TOOL_NAME or plan.get("kind") != expected_kind:
        raise DraftToolError("The plan belongs to a different tool or action.")
    if expected_kind == "quote" and isinstance(plan.get("approval_card"), dict):
        validate_quote_approval_plan(plan)
    return plan


def api_post_allowed(
    access_token: str,
    api_domain: str,
    kind: str,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    path = ALLOWED_POSTS.get(kind)
    if path not in ALLOWED_POSTS.values():
        raise DraftToolError("REFUSED: service POST endpoint is not allowlisted.")
    request = Request(
        api_domain.rstrip("/") + path + "?" + urlencode({"organization_id": organization_id}),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DraftToolError(f"Zoho {kind} creation failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise DraftToolError(f"Zoho API could not be reached: {exc.reason}") from exc
    if result.get("code") not in (None, 0):
        raise DraftToolError(f"Zoho {kind} creation failed: {result.get('message') or result.get('code')}")
    return result


def exact_customer_exists(access_token: str, vault: dict[str, Any], contact_name: str) -> dict[str, Any] | None:
    query = urlencode(
        {
            "organization_id": vault["books_organization_id"],
            "contact_name_contains": contact_name,
            "page": 1,
            "per_page": 200,
        }
    )
    result = zoho_tool.api_get(access_token, str(vault["api_domain"]), f"/books/v3/contacts?{query}")
    for contact in result.get("contacts") or []:
        if str(contact.get("contact_name") or "").strip().casefold() == contact_name.strip().casefold():
            return contact
    return None


def verify_created_quote(after: dict[str, Any], plan: dict[str, Any]) -> None:
    """Verify the fresh live Draft against the exact approved card and payload."""
    payload = plan["payload"]
    evidence = plan["live_evidence"]
    estimate_id = str(after.get("estimate_id") or "")
    if not ID_RE.fullmatch(estimate_id):
        raise DraftToolError("Fresh read-back returned no usable estimate ID.")
    for field, expected in (
        ("status", "draft"),
        ("customer_id", str(payload["customer_id"])),
        ("reference_number", str(payload.get("reference_number") or "")),
    ):
        actual = str(after.get(field) or "").casefold() if field == "status" else str(after.get(field) or "")
        if actual != expected:
            raise DraftToolError(
                f"Fresh Draft read-back {field} is {actual!r}, not the approved {expected!r}."
            )
    expected_currency = str(evidence["customer"].get("currency_code") or "")
    actual_currency = str(after.get("currency_code") or "")
    if expected_currency and not expected_currency.startswith("Zoho currency ID "):
        if actual_currency != expected_currency:
            raise DraftToolError(
                f"Fresh Draft currency is {actual_currency!r}, not the approved {expected_currency!r}."
            )
    live_lines = after.get("line_items")
    expected_lines = payload["line_items"]
    if not isinstance(live_lines, list) or len(live_lines) != len(expected_lines):
        raise DraftToolError("Fresh Draft read-back changed the approved line count.")
    for index, (actual, expected) in enumerate(zip(live_lines, expected_lines, strict=True)):
        if not isinstance(actual, dict):
            raise DraftToolError(f"Fresh Draft line {index + 1} is unreadable.")
        for field in ("item_id", "tax_id"):
            if str(actual.get(field) or "") != str(expected.get(field) or ""):
                raise DraftToolError(f"Fresh Draft line {index + 1} changed {field}.")
        for field in ("quantity", "rate"):
            if live_decimal(actual.get(field), f"fresh line {index + 1} {field}") != live_decimal(
                expected.get(field), f"approved line {index + 1} {field}"
            ):
                raise DraftToolError(f"Fresh Draft line {index + 1} changed {field}.")
        expected_discount = percentage_discount(
            expected.get("discount", 0), f"approved line {index + 1} discount"
        )
        raw_live_discount = actual.get("discount", 0)
        if isinstance(raw_live_discount, str) and raw_live_discount.endswith("%"):
            live_discount = percentage_discount(raw_live_discount, f"fresh line {index + 1} discount")
        else:
            live_discount = live_decimal(raw_live_discount, f"fresh line {index + 1} discount")
        if live_discount != expected_discount:
            raise DraftToolError(f"Fresh Draft line {index + 1} changed discount.")
    totals = evidence["totals"]
    for field, expected in (
        ("sub_total", totals["sub_total"]),
        ("tax_total", totals["tax_total"]),
        ("total", totals["total"]),
    ):
        if totals["tax_certainty"] != "deterministic_simple_tax" and field in {"tax_total", "total"}:
            continue
        if money_text(live_decimal(after.get(field), f"fresh Draft {field}")) != expected:
            raise DraftToolError(
                f"Fresh Draft {field} does not match the approved calculated {expected}."
            )


def command_commit(args: argparse.Namespace, kind: str) -> None:
    plan = load_verified_plan(args.plan, kind)
    if kind == "quote":
        validate_quote_customer_policy(plan["payload"])
        # Before the vault, the token refresh and the network: a plan staged
        # before 2026-08-10 carries the numeric 10 that Zoho reads as CAD 10.
        refuse_numeric_percentage_discounts(plan["payload"])
        if not isinstance(plan.get("approval_card"), dict):
            raise DraftToolError(
                "This legacy Quote plan has no immutable concise approval card and cannot be "
                "approved. Stage a fresh Draft quote plan."
            )
    digest = plan["sha256"]
    require_exact_approval(args.approval)
    quote_lock: Path | None = None
    if kind == "quote":
        require_active_approval_card(plan)
        quote_lock = quote_commit_lock_path(digest)
        if quote_lock.exists():
            raise DraftToolError(
                "REFUSED: this displayed Draft quote plan has already entered commit and cannot "
                "be replayed. No Zoho write was attempted."
            )
    vault = zoho_tool.load_vault()
    zoho_tool.validate_scopes([str(scope) for scope in vault.get("scopes") or []])
    required_scope = "ZohoBooks.contacts.CREATE" if kind == "customer" else "ZohoBooks.estimates.CREATE"
    if required_scope not in (vault.get("scopes") or []):
        raise DraftToolError(f"Saved Zoho connection lacks {required_scope}.")
    access_token, vault = zoho_tool.refresh_access_token(vault)
    if kind == "customer":
        name = str(plan["payload"]["contact_name"])
        existing = exact_customer_exists(access_token, vault, name)
        if existing:
            raise DraftToolError(
                f"Customer already exists with Zoho ID {existing.get('contact_id')}; no duplicate was created."
            )
    if kind == "quote":
        assert quote_lock is not None
        fresh_evidence = quote_live_evidence(
            access_token, vault, plan["payload"], plan["summary"]
        )
        if fresh_evidence != plan["live_evidence"]:
            raise DraftToolError(
                "REFUSED: customer, item, tax, currency or calculated quote totals changed after "
                "review. No POST was issued and the plan remains unlocked; stage a fresh card."
            )
        _write_json_durable(quote_lock, {
            "plan_sha256": digest,
            "kind": "quote",
            "status": "in_flight",
            "started_utc": utc_now().isoformat(),
        }, exclusive=True)
        try:
            result = api_post_allowed(
                access_token,
                str(vault["api_domain"]),
                kind,
                str(vault["books_organization_id"]),
                plan["payload"],
            )
            record = result.get("estimate") or {}
            record_id = str(record.get("estimate_id") or "")
            if not ID_RE.fullmatch(record_id):
                raise DraftToolError("Zoho returned success without a usable estimate ID.")
            verified = get_estimate(access_token, vault, record_id)
            verify_created_quote(verified, plan)
            zoho_tool.save_vault(vault)
        except Exception as exc:
            _write_json_durable(quote_lock, {
                "plan_sha256": digest,
                "kind": "quote",
                "status": "indeterminate",
                "write_attempted": True,
                "no_retry": True,
                "reason": str(exc)[:2000],
                "updated_utc": utc_now().isoformat(),
            })
            raise
        _write_json_durable(quote_lock, {
            "plan_sha256": digest, "kind": "quote", "status": "committed_verified",
            "estimate_id": record_id, "no_retry": True, "updated_utc": utc_now().isoformat(),
        })
        mark_active_card_attempted(plan)
        zoho_tool.append_receipt(
            "zoho_draft_estimate_created_by_named_tool",
            f"estimate_id={record_id}; status=draft; plan={args.plan}; sha256={digest}",
        )
        print(json.dumps({
            "created": "draft_estimate", "estimate_id": record_id,
            "estimate_number": verified.get("estimate_number"), "status": "draft",
            "sent": False, "fresh_read_back_verified": True,
        }, indent=2))
        return
    result = api_post_allowed(
        access_token,
        str(vault["api_domain"]),
        kind,
        str(vault["books_organization_id"]),
        plan["payload"],
    )
    zoho_tool.save_vault(vault)
    record = result.get("contact") or {}
    record_id = str(record.get("contact_id") or "")
    if not record_id:
        raise DraftToolError("Zoho returned success without a customer ID.")
    zoho_tool.append_receipt(
        "zoho_customer_created_by_named_tool",
        f"contact_id={record_id}; plan={args.plan}; sha256={digest}",
    )
    print(json.dumps({"created": "customer", "contact_id": record_id, "contact_name": record.get("contact_name")}, indent=2))


# ===========================================================================
# TDS percentage-discount correction
#
# Read-only staging, then at most ONE PUT per plan against ONE of two fixed
# draft estimates. Nothing here can create, delete, send, mark, approve or
# otherwise change the lifecycle of any record.
# ===========================================================================


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def digest_for(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise DraftToolError("Zoho returned evidence that is not JSON serializable.") from exc


def read_json_value(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DraftToolError(f"{label} is unreadable: {path}") from exc


def file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise DraftToolError(f"Evidence file is unreadable: {path}") from exc


def money_text(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def live_decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise DraftToolError(f"{label} is missing or not a number.")
    text = str(value).strip()
    if not text:
        raise DraftToolError(f"{label} is blank.")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise DraftToolError(f"{label} is not a valid number: {value!r}") from exc
    if not result.is_finite():
        raise DraftToolError(f"{label} must be a finite number.")
    return result


def parse_plan_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise DraftToolError(f"Plan {label} is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DraftToolError(f"Plan {label} must include a timezone.")
    return parsed


def require_exact_approval(approval: Any) -> None:
    """Exact, unpadded, uppercase APPROVED. No strip(), no case folding.

    Deliberately stricter than the create-path comparator, matching the
    price and invoice tools: this word changes a live customer-facing record.
    """
    if not isinstance(approval, str) or approval != APPROVAL_WORD:
        raise DraftToolError(
            f"Rachad must answer this exact staged plan with the one-word approval: "
            f"{APPROVAL_WORD} (exact uppercase, no extra words or spaces). It must come from "
            "his own message (Hard Rule 3); staging is not approval and Dado cannot supply it."
        )


def require_correction_target(estimate_id: Any) -> tuple[str, dict[str, Any]]:
    """The complete reachable surface: two IDs, refused before any network."""
    key = str(estimate_id if estimate_id is not None else "").strip()
    target = CORRECTION_TARGETS.get(key)
    if target is None:
        raise DraftToolError(
            "REFUSED: this correction is commissioned for exactly two draft estimates -- "
            + ", ".join(
                f"{value['estimate_number']} ({identifier})"
                for identifier, value in CORRECTION_TARGETS.items()
            )
            + f". {key!r} is not one of them; no Zoho call was made."
        )
    return key, target


def books_organization_id(vault: dict[str, Any]) -> str:
    value = str(vault.get("books_organization_id") or "")
    if not ID_RE.fullmatch(value):
        raise DraftToolError("The saved Zoho connection has no FRP Depot Books organization ID.")
    return value


def get_estimate(access_token: str, vault: dict[str, Any], estimate_id: str) -> dict[str, Any]:
    """The only estimate read path used here: zoho_tool's GET-only helper."""
    query = urlencode({"organization_id": books_organization_id(vault)})
    result = zoho_tool.api_get(
        access_token, str(vault["api_domain"]), f"/books/v3/estimates/{estimate_id}?{query}"
    )
    estimate = result.get("estimate")
    if not isinstance(estimate, dict):
        raise DraftToolError(f"Zoho returned no estimate record for {estimate_id}.")
    if str(estimate.get("estimate_id") or "") != estimate_id:
        raise DraftToolError(
            f"Zoho returned estimate {estimate.get('estimate_id')!r} for a request for {estimate_id}."
        )
    return json_copy(estimate)


def correction_protected_state(estimate: dict[str, Any]) -> dict[str, Any]:
    """Everything Zoho returns EXCEPT what the discount itself recomputes.

    Each excluded key is verified by its own explicit rule after the PUT, so
    nothing is merely waved through.
    """
    result = {
        key: value for key, value in estimate.items()
        if key not in ESTIMATE_DERIVED_KEYS and key not in ESTIMATE_PREWRITE_VOLATILE_KEYS
    }
    result["taxes_protected"] = [
        {key: value for key, value in tax.items() if key not in ESTIMATE_TAX_AMOUNT_KEYS}
        for tax in (estimate.get("taxes") or [])
        if isinstance(tax, dict)
    ]
    result["line_items_protected"] = []
    for line in (estimate.get("line_items") or []):
        if not isinstance(line, dict):
            continue
        protected_line = {
            key: value for key, value in line.items() if key not in ESTIMATE_LINE_DERIVED_KEYS
        }
        protected_line["line_item_taxes_protected"] = [
            {key: value for key, value in tax.items() if key not in ESTIMATE_TAX_AMOUNT_KEYS}
            for tax in (line.get("line_item_taxes") or [])
            if isinstance(tax, dict)
        ]
        result["line_items_protected"].append(protected_line)
    return result


def correction_prewrite_state(estimate: dict[str, Any]) -> dict[str, Any]:
    """Full live record except Zoho's one proven per-GET volatile URL."""
    return {
        key: value for key, value in estimate.items()
        if key not in ESTIMATE_PREWRITE_VOLATILE_KEYS
    }


def live_estimate_lines(estimate: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    lines = estimate.get("line_items")
    if not isinstance(lines, list) or len(lines) != target["line_count"]:
        raise DraftToolError(
            f"{target['estimate_number']} must carry exactly {target['line_count']} live lines; "
            f"Zoho returned {len(lines) if isinstance(lines, list) else 'none'}."
        )
    seen: set[str] = set()
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise DraftToolError(f"Live line_items[{index}] is not an object.")
        line_item_id = str(line.get("line_item_id") or "")
        item_id = str(line.get("item_id") or "")
        if not ID_RE.fullmatch(line_item_id):
            raise DraftToolError(f"Live line_items[{index}] has no usable line_item_id.")
        if not ID_RE.fullmatch(item_id):
            raise DraftToolError(f"Live line_items[{index}] is not linked to a Zoho item.")
        if line_item_id in seen:
            raise DraftToolError(f"Live line_items[{index}] repeats line_item_id {line_item_id}.")
        seen.add(line_item_id)
        if not str(line.get("name") or "").strip():
            raise DraftToolError(f"Live line_items[{index}] has no item name.")
        if not str(line.get("tax_id") or "").strip():
            raise DraftToolError(
                f"Live line_items[{index}] carries no tax_id. This correction resends the live "
                "tax on every line and refuses to guess one."
            )
    return [json_copy(line) for line in lines]


def validate_live_estimate(estimate: dict[str, Any], estimate_id: str, target: dict[str, Any]) -> list[dict[str, Any]]:
    """Exact identity and exact current wrong state, or nothing is staged."""
    identity = (
        ("estimate_id", estimate_id),
        ("estimate_number", target["estimate_number"]),
        ("reference_number", target["reference_number"]),
        ("customer_id", target["customer_id"]),
        ("status", target["status"]),
    )
    for key, expected in identity:
        actual = str(estimate.get(key) if estimate.get(key) is not None else "")
        if actual != expected:
            raise DraftToolError(
                f"Live estimate {key} is {actual!r}, not the commissioned {expected!r}. Nothing staged."
            )
    if estimate.get("discount_type") != "item_level":
        raise DraftToolError(
            f"Live estimate discount_type is {estimate.get('discount_type')!r}, not 'item_level'."
        )
    if estimate.get("is_discount_before_tax") is not True:
        raise DraftToolError("Live estimate is_discount_before_tax is not exactly true.")
    for key, expected in (
        ("sub_total", target["current_sub_total"]),
        ("tax_total", target["current_tax_total"]),
        ("total", target["current_total"]),
    ):
        actual = live_decimal(estimate.get(key), f"live {key}")
        if actual != expected:
            raise DraftToolError(
                f"Live {key} is {actual}, not the diagnosed {expected}. The estimate changed "
                "since the diagnosis; nothing staged."
            )
    lines = live_estimate_lines(estimate, target)
    for index, line in enumerate(lines):
        discount = live_decimal(line.get("discount"), f"live line_items[{index}].discount")
        amount = live_decimal(line.get("discount_amount"), f"live line_items[{index}].discount_amount")
        if discount != CURRENT_WRONG_LINE_DISCOUNT or amount != CURRENT_WRONG_LINE_DISCOUNT:
            raise DraftToolError(
                f"Live line_items[{index}] is not in the diagnosed wrong state "
                f"(discount={discount}, discount_amount={amount}; both must be "
                f"{CURRENT_WRONG_LINE_DISCOUNT}). Nothing staged."
            )
    return lines


def source_plan_evidence(target: dict[str, Any]) -> dict[str, Any]:
    """The immutable create plan this estimate came from, hash-verified."""
    path = Path(target["source_plan"])
    plan = read_json_value(path, "Original source plan")
    if not isinstance(plan, dict):
        raise DraftToolError("Original source plan must contain one object.")
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if saved != target["source_plan_sha256"] or not secrets.compare_digest(saved, plan_hash(core)):
        raise DraftToolError(
            f"Original source plan {path} does not match its recorded digest. Nothing staged."
        )
    if plan.get("tool") != TOOL_NAME or plan.get("kind") != "quote":
        raise DraftToolError("Original source plan is not a quote plan from this tool.")
    payload = plan.get("payload")
    if not isinstance(payload, dict):
        raise DraftToolError("Original source plan has no payload.")
    if str(payload.get("customer_id") or "") != target["customer_id"]:
        raise DraftToolError("Original source plan names a different customer.")
    if str(payload.get("reference_number") or "") != target["reference_number"]:
        raise DraftToolError("Original source plan names a different reference number.")
    lines = payload.get("line_items")
    if not isinstance(lines, list) or len(lines) != target["line_count"]:
        raise DraftToolError("Original source plan does not carry the same number of lines.")
    return {
        "path": str(path),
        "sha256": saved,
        "line_items": json_copy(lines),
    }


def artifact_record(path: Path, label: str, match: Any) -> dict[str, Any]:
    """One record out of a live-diagnosis artifact, selected by exact key."""
    value = read_json_value(path, label)
    records = value.get("records") if isinstance(value, dict) else value
    if not isinstance(records, list):
        raise DraftToolError(f"{label} does not contain a record list.")
    chosen = [
        record for record in records
        if isinstance(record, dict)
        and (record.get("estimate_id") == match or record.get("reference_number") == match)
    ]
    if len(chosen) != 1:
        raise DraftToolError(f"{label} does not contain exactly one record for {match!r}.")
    return {
        "path": str(path),
        "file_sha256": file_digest(path),
        "record": json_copy(chosen[0]),
    }


def require_correction_sources(sources: Any) -> None:
    """The staged evidence must have the exact shape build_correction reads."""
    if not isinstance(sources, dict) or set(sources) != {
        "source_plan", "totals_artifact", "diagnosis_artifact"
    }:
        raise DraftToolError("Plan source evidence is not the exact closed schema.")
    plan_evidence = sources["source_plan"]
    if not isinstance(plan_evidence, dict) or set(plan_evidence) != {
        "path", "sha256", "line_items"
    } or not isinstance(plan_evidence["line_items"], list):
        raise DraftToolError("Plan source-plan evidence is invalid.")
    for key in ("totals_artifact", "diagnosis_artifact"):
        artifact = sources[key]
        if not isinstance(artifact, dict) or set(artifact) != {
            "path", "file_sha256", "record"
        } or not isinstance(artifact["record"], dict):
            raise DraftToolError(f"Plan {key} evidence is invalid.")


def collect_source_evidence(target: dict[str, Any], estimate_id: str) -> dict[str, Any]:
    return {
        "source_plan": source_plan_evidence(target),
        "totals_artifact": artifact_record(
            TOTALS_ARTIFACT, "Approved totals artifact", target["reference_number"]
        ),
        "diagnosis_artifact": artifact_record(
            DIAGNOSIS_ARTIFACT, "Live diagnosis artifact", estimate_id
        ),
    }


def cross_check_source_plan(lines: list[dict[str, Any]], source_lines: list[dict[str, Any]]) -> None:
    """Live line identity against the immutable plan the estimate was made from."""
    for index, (live, staged) in enumerate(zip(lines, source_lines)):
        if not isinstance(staged, dict):
            raise DraftToolError(f"Original source plan line {index} is not an object.")
        for key in ("item_id", "name", "unit"):
            expected = str(staged.get(key) or "")
            actual = str(live.get(key) or "")
            if expected and expected != actual:
                raise DraftToolError(
                    f"Line {index + 1} {key} is {actual!r} live but {expected!r} in the original "
                    "source plan. Nothing staged."
                )
        for key in ("quantity", "rate"):
            expected_value = live_decimal(staged.get(key), f"source line {index} {key}")
            actual_value = live_decimal(live.get(key), f"live line {index} {key}")
            if expected_value != actual_value:
                raise DraftToolError(
                    f"Line {index + 1} {key} is {actual_value} live but {expected_value} in the "
                    "original source plan. Nothing staged."
                )
        # A description WE stated must match exactly. Where the source plan
        # stated none, Zoho's own item default stands and is preserved verbatim.
        stated = str(staged.get("description") or "")
        if stated and stated != str(live.get("description") or ""):
            raise DraftToolError(
                f"Line {index + 1} description does not match the original source plan. Nothing staged."
            )


def cross_check_diagnosis(
    lines: list[dict[str, Any]], record: dict[str, Any], estimate_id: str, target: dict[str, Any]
) -> None:
    """The live GET must still show exactly what the 2026-08-10 diagnosis saw."""
    for key, expected in (
        ("estimate_id", estimate_id),
        ("estimate_number", target["estimate_number"]),
        ("reference_number", target["reference_number"]),
        ("status", target["status"]),
    ):
        if str(record.get(key) or "") != expected:
            raise DraftToolError(f"Live diagnosis artifact {key} is not {expected!r}.")
    if record.get("discount_type") != "item_level" or record.get("is_discount_before_tax") is not True:
        raise DraftToolError("Live diagnosis artifact does not show an item-level pre-tax discount.")
    for key, expected_total in (
        ("sub_total", target["current_sub_total"]),
        ("tax_total", target["current_tax_total"]),
        ("total", target["current_total"]),
    ):
        if live_decimal(record.get(key), f"diagnosis {key}") != expected_total:
            raise DraftToolError(f"Live diagnosis artifact {key} is not the diagnosed {expected_total}.")
    diagnosed = record.get("line_items")
    if not isinstance(diagnosed, list) or len(diagnosed) != target["line_count"]:
        raise DraftToolError("Live diagnosis artifact does not carry the same number of lines.")
    for index, (live, row) in enumerate(zip(lines, diagnosed)):
        if not isinstance(row, dict):
            raise DraftToolError(f"Live diagnosis artifact line {index} is not an object.")
        if str(row.get("name") or "") != str(live.get("name") or ""):
            raise DraftToolError(f"Line {index + 1} name does not match the live diagnosis artifact.")
        for key in ("quantity", "rate", "discount", "discount_amount", "item_total"):
            expected_value = live_decimal(row.get(key), f"diagnosis line {index} {key}")
            actual_value = live_decimal(live.get(key), f"live line {index} {key}")
            if expected_value != actual_value:
                raise DraftToolError(
                    f"Line {index + 1} {key} is {actual_value} live but {expected_value} in the "
                    "live diagnosis artifact. Nothing staged."
                )


def tax_on(net_subtotal: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Quebec GST + QST, computed BOTH ways on the net subtotal.

    Zoho's live figures on these two estimates match this basis exactly
    (13,110.64 -> 1,963.32 and 5,659.76 -> 847.55), which is why the corrected
    tax can be predicted at all. Both the combined rate and the two components
    must agree, or nothing is staged.
    """
    combined = (net_subtotal * COMBINED_TAX_PERCENT / Decimal("100")).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    gst = (net_subtotal * GST_PERCENT / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
    qst = (net_subtotal * QST_PERCENT / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
    return combined, gst, qst


def corroborate_current_tax(estimate: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Prove the tax basis on THIS estimate's own current live numbers."""
    combined, gst, qst = tax_on(target["current_sub_total"])
    if combined != target["current_tax_total"] or gst + qst != target["current_tax_total"]:
        raise DraftToolError(
            "The Quebec GST + QST calculation does not reproduce this estimate's current live tax "
            f"({target['current_tax_total']}); the corrected tax cannot be predicted. Nothing staged."
        )
    # Zoho's own breakdown, when it returns one: the components must add up to
    # the live tax exactly. Whether Zoho splits the cents the same way this
    # tool does is recorded, not enforced -- what the read-back asserts is the
    # TOTAL, and that total is reproduced exactly by both methods above.
    breakdown = estimate.get("taxes")
    components: list[dict[str, str]] = []
    split_matches = False
    if isinstance(breakdown, list) and breakdown:
        rows = [entry for entry in breakdown if isinstance(entry, dict)]
        if len(rows) != len(breakdown):
            raise DraftToolError("The live tax breakdown is not a list of objects. Nothing staged.")
        amounts = [
            live_decimal(entry.get("tax_amount"), "live tax component") for entry in rows
        ]
        if sum(amounts, Decimal("0")) != target["current_tax_total"]:
            raise DraftToolError(
                "The live tax components do not add up to this estimate's current live tax "
                f"({target['current_tax_total']}). Nothing staged."
            )
        split_matches = sorted(amounts) == sorted([gst, qst])
        components = [
            {
                "name": str(entry.get("tax_name") or ""),
                "current_amount": money_text(amount),
            }
            for entry, amount in zip(rows, amounts)
        ]
    return {
        "method": "Half-up on the net subtotal; combined 14.975% and 5% + 9.975% must agree.",
        "current_tax_reproduced": money_text(combined),
        "current_components_add_up": bool(components),
        "current_split_matches_gst_qst": split_matches,
        "live_components": components,
    }


def expected_correction(
    lines: list[dict[str, Any]], totals: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    """Recompute the corrected figures; never copy an approved number blindly.

    Every line's 10% is recomputed from the LIVE quantity and rate, then must
    equal the approved totals artifact, and the sums must equal the fixed
    figures Rachad approved. A tampered artifact cannot move a total.
    """
    artifact_lines = totals.get("lines")
    if not isinstance(artifact_lines, list) or len(artifact_lines) != target["line_count"]:
        raise DraftToolError("Approved totals artifact does not carry the same number of lines.")
    if str(totals.get("tax_rate_percent") or "") != format(COMBINED_TAX_PERCENT, "f"):
        raise DraftToolError("Approved totals artifact does not use the Quebec 14.975% rate.")
    expected_lines = []
    list_subtotal = Decimal("0")
    discount_total = Decimal("0")
    net_subtotal = Decimal("0")
    for index, (live, row) in enumerate(zip(lines, artifact_lines)):
        if not isinstance(row, dict):
            raise DraftToolError(f"Approved totals artifact line {index} is not an object.")
        if str(row.get("name") or "") != str(live.get("name") or ""):
            raise DraftToolError(f"Line {index + 1} name does not match the approved totals artifact.")
        quantity = live_decimal(live.get("quantity"), f"live line {index} quantity")
        rate = live_decimal(live.get("rate"), f"live line {index} rate")
        if live_decimal(row.get("quantity"), f"artifact line {index} quantity") != quantity:
            raise DraftToolError(f"Line {index + 1} quantity does not match the approved totals artifact.")
        if live_decimal(row.get("rate"), f"artifact line {index} rate") != rate:
            raise DraftToolError(f"Line {index + 1} rate does not match the approved totals artifact.")
        gross = (quantity * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        discount_amount = (
            gross * Decimal(TDS_LINE_DISCOUNT[:-1]) / Decimal("100")
        ).quantize(CENT, rounding=ROUND_HALF_UP)
        net = gross - discount_amount
        for key, computed in (("gross", gross), ("discount", discount_amount), ("net", net)):
            stated = live_decimal(row.get(key), f"artifact line {index} {key}")
            if stated != computed:
                raise DraftToolError(
                    f"Line {index + 1} {key} recomputes to {computed} but the approved totals "
                    f"artifact states {stated}. Nothing staged."
                )
        list_subtotal += gross
        discount_total += discount_amount
        net_subtotal += net
        expected_lines.append({
            "line_item_id": str(live.get("line_item_id") or ""),
            "name": str(live.get("name") or ""),
            "quantity": format(quantity, "f"),
            "rate": format(rate, "f"),
            "line_gross": money_text(gross),
            "discount_amount": money_text(discount_amount),
            "item_total": money_text(net),
        })
    combined, gst, qst = tax_on(net_subtotal)
    if gst + qst != combined:
        raise DraftToolError(
            "The combined Quebec rate and its two components disagree on the corrected tax. Nothing staged."
        )
    total = net_subtotal + combined
    stated_totals = (
        ("list_subtotal", list_subtotal),
        ("discount_total", discount_total),
        ("net_subtotal", net_subtotal),
        ("tax_total", combined),
        ("total", total),
    )
    for key, computed in stated_totals:
        stated = live_decimal(totals.get(key), f"artifact {key}")
        if stated != computed:
            raise DraftToolError(
                f"The approved totals artifact states {key} {stated} but it recomputes to "
                f"{computed}. Nothing staged."
            )
    approved = (
        ("sub_total", net_subtotal, target["corrected_sub_total"]),
        ("discount_total", discount_total, target["corrected_discount_total"]),
        ("tax_total", combined, target["corrected_tax_total"]),
        ("total", total, target["corrected_total"]),
    )
    for key, computed, fixed in approved:
        if computed != fixed:
            raise DraftToolError(
                f"The corrected {key} recomputes to {computed}, not the approved {fixed}. Nothing staged."
            )
    return {
        "lines": expected_lines,
        "list_subtotal": money_text(list_subtotal),
        "discount_total": money_text(discount_total),
        "sub_total": money_text(net_subtotal),
        "tax_total": money_text(combined),
        "tax_gst": money_text(gst),
        "tax_qst": money_text(qst),
        "total": money_text(total),
    }


def correction_put_payload(estimate: dict[str, Any], lines: list[dict[str, Any]]) -> dict[str, Any]:
    """The complete PUT body: preserved live values, one changed field.

    Zoho deletes existing lines that a PUT omits, so the COMPLETE live line
    list is always resent, each with its own line_item_id and item_id.
    """
    payload: dict[str, Any] = {}
    for key in CORRECTION_HEADER_KEYS:
        value = estimate.get(key)
        if key == "customer_id":
            payload[key] = str(value or "")
            continue
        if isinstance(value, str) and value.strip():
            payload[key] = value
    payload["discount_type"] = "item_level"
    payload["is_discount_before_tax"] = True
    put_lines = []
    for index, line in enumerate(lines):
        put_line: dict[str, Any] = {}
        for key in CORRECTION_LINE_PUT_KEYS:
            if key == "discount":
                continue
            value = line.get(key)
            if value is None or value == "":
                continue
            put_line[key] = value
        put_line["line_item_id"] = str(line.get("line_item_id") or "")
        put_line["item_id"] = str(line.get("item_id") or "")
        put_line["discount"] = TDS_LINE_DISCOUNT
        missing = sorted(CORRECTION_REQUIRED_LINE_KEYS - set(put_line))
        if missing:
            raise DraftToolError(
                f"Live line {index + 1} cannot be resent intact; missing {', '.join(missing)}."
            )
        put_lines.append(put_line)
    payload["line_items"] = put_lines
    extra = sorted(set(payload) - CORRECTION_ALLOWED_PUT_KEYS)
    if extra or not CORRECTION_REQUIRED_PUT_KEYS.issubset(payload):
        raise DraftToolError("The correction payload is not the exact commissioned shape.")
    return payload


def build_correction(
    before: dict[str, Any], estimate_id: str, target: dict[str, Any], sources: dict[str, Any]
) -> dict[str, Any]:
    """The whole projection, derived from immutable inputs alone.

    Commit re-runs this over the staged live state and refuses unless the
    result is byte-identical to the reviewed plan, so no figure, endpoint,
    payload key or fingerprint in a plan file can be tampered with.
    """
    lines = validate_live_estimate(before, estimate_id, target)
    cross_check_source_plan(lines, sources["source_plan"]["line_items"])
    cross_check_diagnosis(lines, sources["diagnosis_artifact"]["record"], estimate_id, target)
    tax_evidence = corroborate_current_tax(before, target)
    expected = expected_correction(lines, sources["totals_artifact"]["record"], target)
    protected = correction_protected_state(before)
    return {
        "estimate": {
            "estimate_id": estimate_id,
            "estimate_number": target["estimate_number"],
            "reference_number": target["reference_number"],
            "customer_id": target["customer_id"],
            "status": target["status"],
            "line_count": target["line_count"],
            "before_state": json_copy(before),
            "before_state_sha256": digest_for(before),
            "protected_state": protected,
            "protected_state_sha256": digest_for(protected),
        },
        "current": {
            "line_discount_each": money_text(CURRENT_WRONG_LINE_DISCOUNT),
            "sub_total": money_text(target["current_sub_total"]),
            "tax_total": money_text(target["current_tax_total"]),
            "total": money_text(target["current_total"]),
        },
        "change": {
            "field": "line_items[].discount",
            "from": "the number 10, which Zoho applies as CAD 10.00 per line",
            "to": TDS_LINE_DISCOUNT,
        },
        "tax_method": tax_evidence,
        "expected": expected,
        "put_endpoint": f"PUT /books/v3/estimates/{estimate_id}",
        "put_payload": correction_put_payload(before, lines),
        "email_sent": False,
    }


def stage_correction_plan(
    estimate_id: str, target: dict[str, Any], evidence: dict[str, Any],
    sources: dict[str, Any], organization_id: str,
) -> Path:
    created = utc_now()
    core = {
        "tool": TOOL_NAME,
        "kind": CORRECTION_KIND,
        "schema_version": CORRECTION_SCHEMA_VERSION,
        "nonce": secrets.token_hex(16),
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=CORRECTION_PLAN_LIFETIME_HOURS)).isoformat(),
        "approval_required": APPROVAL_WORD,
        "estimate_id": estimate_id,
        "books_organization_id": organization_id,
        "risk": {
            "atomic": True,
            "single_put": True,
            "email_sent": False,
            "note": CORRECTION_RISK_NOTE,
        },
        "source_evidence": sources,
        "live_evidence": evidence,
    }
    plan = dict(core)
    plan["sha256"] = digest_for(core)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{CORRECTION_KIND}_{plan['sha256'][:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt(
        "zoho_tds_discount_correction_plan_staged_read_only",
        f"estimate={target['estimate_number']} ({estimate_id}); plan={path}; "
        f"sha256={plan['sha256']}; writes=0; method=GET_ONLY",
    )
    return path


def print_correction_summary(plan: dict[str, Any], path: Path) -> None:
    evidence = plan["live_evidence"]
    estimate = evidence["estimate"]
    expected = evidence["expected"]
    print("=" * 78)
    print("STAGED PLAN -- TDS PERCENTAGE-DISCOUNT CORRECTION (NOT YET APPROVED)")
    print("=" * 78)
    print(f"Estimate      : {estimate['estimate_number']} ({estimate['estimate_id']})")
    print(f"Reference     : {estimate['reference_number']}")
    print(f"Customer      : Troy Dualam Services Inc. ({estimate['customer_id']})")
    print(f"Status        : {estimate['status']} (unchanged by this plan)")
    print(f"Endpoint      : {evidence['put_endpoint']} (one PUT, no retry)")
    print(f"Change        : every line's {evidence['change']['field']}")
    print(f"                from {evidence['change']['from']}")
    print(f"                to   {evidence['change']['to']}")
    print("-" * 78)
    print(f"{'#':>2}  {'Line':38} {'Gross':>10} {'Disc 10%':>10} {'Net':>10}")
    for index, line in enumerate(expected["lines"], start=1):
        print(
            f"{index:>2}  {line['name'][:38]:38} {line['line_gross']:>10} "
            f"{line['discount_amount']:>10} {line['item_total']:>10}"
        )
    print("-" * 78)
    print(f"List subtotal            : CAD {expected['list_subtotal']}")
    print(f"Discount total (10%)     : CAD {expected['discount_total']}")
    print(f"Corrected sub_total      : CAD {expected['sub_total']}")
    print(
        f"Corrected tax_total      : CAD {expected['tax_total']}"
        f"  (GST {expected['tax_gst']} + QST {expected['tax_qst']})"
    )
    print(f"Corrected total          : CAD {expected['total']}")
    print(f"Current wrong total      : CAD {evidence['current']['total']}")
    print("-" * 78)
    print(f"Lines resent intact      : {len(evidence['put_payload']['line_items'])} "
          f"(every line_item_id, nothing added, removed or reordered)")
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


def command_stage_tds_discount_correction(args: argparse.Namespace) -> None:
    estimate_id, target = require_correction_target(args.estimate_id)
    sources = collect_source_evidence(target, estimate_id)
    vault = zoho_tool.load_vault()
    zoho_tool.validate_scopes([str(scope) for scope in vault.get("scopes") or []])
    organization_id = books_organization_id(vault)
    access_token, vault = zoho_tool.refresh_access_token(vault)
    before = get_estimate(access_token, vault, estimate_id)
    zoho_tool.save_vault(vault)
    evidence = build_correction(before, estimate_id, target, sources)
    path = stage_correction_plan(estimate_id, target, evidence, sources, organization_id)
    plan = read_json(str(path))
    print_correction_summary(plan, path)


def contained_correction_plan(raw_path: Any) -> Path:
    candidate = Path(str(raw_path if raw_path is not None else ""))
    if not candidate.is_absolute():
        raise DraftToolError("Plan must be an absolute path inside the exact plan folder.")
    lexical_root = PLAN_DIR.absolute()
    try:
        candidate.absolute().relative_to(lexical_root)
    except ValueError as exc:
        raise DraftToolError("Plan is outside the exact allowlisted plan folder.") from exc
    cursor = candidate.absolute()
    while True:
        if cursor.is_symlink():
            raise DraftToolError("Plan paths and parents must not be symlinks.")
        if cursor == lexical_root:
            break
        parent = cursor.parent
        if parent == cursor:
            raise DraftToolError("Plan is outside the exact allowlisted plan folder.")
        cursor = parent
    try:
        root = PLAN_DIR.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise DraftToolError("Plan does not resolve inside the exact plan folder.") from exc
    if root not in resolved.parents or not resolved.is_file() or resolved.suffix.casefold() != ".json":
        raise DraftToolError("Plan is outside the exact allowlisted plan folder.")
    return resolved


def validate_correction_plan(plan: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if (
        plan.get("tool") != TOOL_NAME
        or plan.get("kind") != CORRECTION_KIND
        or plan.get("schema_version") != CORRECTION_SCHEMA_VERSION
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise DraftToolError("The plan belongs to a different tool, action or schema version.")
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise DraftToolError("Plan nonce is invalid.")
    created = parse_plan_time(plan.get("created_utc"), "creation time")
    expires = parse_plan_time(plan.get("expires_utc"), "expiry")
    if expires - created != timedelta(hours=CORRECTION_PLAN_LIFETIME_HOURS):
        raise DraftToolError("Plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise DraftToolError("Plan creation time is in the future.")
    if now >= expires:
        raise DraftToolError("Plan expired. Stage a new plan for review.")
    risk = plan.get("risk")
    if not isinstance(risk, dict) or (
        risk.get("atomic") is not True
        or risk.get("single_put") is not True
        or risk.get("email_sent") is not False
        or risk.get("note") != CORRECTION_RISK_NOTE
    ):
        raise DraftToolError("Plan must disclose the exact single-atomic-PUT risk.")
    estimate_id, target = require_correction_target(plan.get("estimate_id"))
    if not ID_RE.fullmatch(str(plan.get("books_organization_id") or "")):
        raise DraftToolError("Plan organization ID is invalid.")
    sources = plan.get("source_evidence")
    evidence = plan.get("live_evidence")
    if not isinstance(sources, dict) or not isinstance(evidence, dict):
        raise DraftToolError("Plan evidence is invalid.")
    require_correction_sources(sources)
    if str(sources["source_plan"].get("sha256") or "") != target["source_plan_sha256"]:
        raise DraftToolError("Plan does not cite the immutable original source plan.")
    staged_estimate = evidence.get("estimate")
    if not isinstance(staged_estimate, dict):
        raise DraftToolError("Plan estimate evidence is invalid.")
    before = staged_estimate.get("before_state")
    if not isinstance(before, dict):
        raise DraftToolError("Plan before-state evidence must be an object.")
    if not secrets.compare_digest(
        str(staged_estimate.get("before_state_sha256") or ""), digest_for(before)
    ):
        raise DraftToolError("Plan before-state evidence hash is invalid.")
    # Re-derive EVERYTHING from the staged live state. A tampered payload,
    # endpoint, fingerprint or corrected total cannot survive this.
    rebuilt = build_correction(before, estimate_id, target, sources)
    if rebuilt != evidence:
        raise DraftToolError(
            "Plan evidence is not the canonical projection of the staged live estimate state."
        )
    if evidence["put_endpoint"] != f"PUT /books/v3/estimates/{estimate_id}":
        raise DraftToolError("Plan endpoint is not the one commissioned estimate route.")
    return estimate_id, target, evidence


def load_correction_plan(path: Path) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    plan = read_json(str(path))
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise DraftToolError("Plan hash check failed. The plan changed after review.")
    estimate_id, target, evidence = validate_correction_plan(plan)
    return plan, estimate_id, target, evidence


def correction_lock_path(plan_sha256: str) -> Path:
    if not HEX_64_RE.fullmatch(str(plan_sha256)):
        raise DraftToolError("Plan digest is invalid for replay locking.")
    return PLAN_DIR / CORRECTION_LOCK_DIRNAME / f"{plan_sha256}.json"


def write_correction_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    """Durable single-use lock. Created BEFORE the PUT, never removed after."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise DraftToolError(
            "REFUSED: this plan has already entered commit and cannot be replayed."
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def oauth_estimate_discount_write_allowed(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """The complete write transport for the correction, and its allowlist.

    Only PUT. Only one of the two commissioned estimate IDs. Only the
    commissioned keys, with the complete line list and every line_item_id
    present so nothing can be dropped by omission. The query string is exactly
    the organization id: no action, no status, no send parameter exists here or
    anywhere else in this module.
    """
    if method != "PUT":
        raise DraftToolError("REFUSED: the discount correction is a PUT and nothing else.")
    match = ESTIMATE_PUT_PATH_RE.fullmatch(str(path))
    if not match or match.group(1) not in CORRECTION_TARGETS:
        raise DraftToolError(
            "REFUSED: the correction targets one of the two commissioned estimate endpoints. "
            "Creation, deletion, status, sending, mailing, approval, conversion, attachment, "
            "template and every bulk route are unreachable."
        )
    if not isinstance(payload, dict):
        raise DraftToolError("REFUSED: the estimate PUT payload must be an object.")
    extra = sorted(set(payload) - CORRECTION_ALLOWED_PUT_KEYS)
    if extra:
        raise DraftToolError(
            "REFUSED: the estimate PUT payload names uncommissioned field(s): " + ", ".join(extra)
        )
    if not CORRECTION_REQUIRED_PUT_KEYS.issubset(payload):
        raise DraftToolError(
            "REFUSED: the estimate PUT payload must carry the preserved customer, number and date, "
            "the item-level pre-tax discount flags and the complete line list."
        )
    if payload.get("discount_type") != "item_level" or payload.get("is_discount_before_tax") is not True:
        raise DraftToolError("REFUSED: the correction keeps an item-level discount before tax.")
    target = CORRECTION_TARGETS[match.group(1)]
    if str(payload.get("customer_id") or "") != target["customer_id"]:
        raise DraftToolError("REFUSED: the estimate PUT names a different customer.")
    if str(payload.get("estimate_number") or "") != target["estimate_number"]:
        raise DraftToolError("REFUSED: the estimate PUT does not preserve the estimate number.")
    lines = payload.get("line_items")
    if not isinstance(lines, list) or len(lines) != target["line_count"]:
        raise DraftToolError("REFUSED: the estimate PUT must carry the complete live line list.")
    seen: set[str] = set()
    for line in lines:
        if not isinstance(line, dict):
            raise DraftToolError("REFUSED: every PUT line must be an object.")
        unknown = sorted(set(line) - set(CORRECTION_LINE_PUT_KEYS))
        if unknown:
            raise DraftToolError(
                "REFUSED: a PUT line names uncommissioned field(s): " + ", ".join(unknown)
            )
        if not CORRECTION_REQUIRED_LINE_KEYS.issubset(line):
            raise DraftToolError("REFUSED: every PUT line must resend its own identity.")
        line_item_id = str(line.get("line_item_id") or "")
        if not ID_RE.fullmatch(line_item_id) or line_item_id in seen:
            raise DraftToolError("REFUSED: the estimate PUT repeats or omits a line_item_id.")
        seen.add(line_item_id)
        if line.get("discount") != TDS_LINE_DISCOUNT:
            raise DraftToolError(
                f"REFUSED: every PUT line discount must be the exact string {TDS_LINE_DISCOUNT!r}."
            )
    if not ID_RE.fullmatch(str(organization_id)):
        raise DraftToolError("REFUSED: the organization ID is invalid.")
    return send_estimate_put(
        access_token, api_domain, path, organization_id, payload, "estimate correction"
    )


def send_estimate_put(
    access_token: str,
    api_domain: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    """The ONE estimate PUT transport in this module.

    Reached only from an allowlist function that has already pinned the verb,
    the estimate ID and every payload key. The query string is exactly the
    organization id: no action, no status and no send parameter exists here or
    anywhere else in this module. One attempt, no retry.
    """
    request = Request(
        api_domain.rstrip("/") + path + "?" + urlencode({"organization_id": organization_id}),
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DraftToolError(
            f"Zoho {label} failed with HTTP {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise DraftToolError(
            f"Zoho {label} outcome is indeterminate: {exc.reason}"
        ) from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DraftToolError(
            f"Zoho {label} returned invalid JSON; the outcome is indeterminate."
        ) from exc
    if not isinstance(result, dict) or result.get("code") != 0:
        message = result.get("message") if isinstance(result, dict) else "invalid response"
        raise DraftToolError(
            f"Zoho {label} returned an invalid or unknown result: " + str(message)
        )
    return result


def returned_discount_percent(value: Any, label: str) -> Decimal:
    """Read back the discount Zoho now holds.

    Zoho echoes a percentage either as "10%" or as the bare number 10 beside a
    correct discount_amount, so the percent value alone is never conclusive --
    the exact discount_amount check beside this one is what proves the line is
    a percentage and not another CAD 10.00.
    """
    if isinstance(value, str) and value.endswith("%"):
        return live_decimal(value[:-1], label)
    return live_decimal(value, label)


def verify_correction_result(
    after: Any, evidence: dict[str, Any], label: str, *, full: bool = True
) -> None:
    """Post-state verification, applied to the PUT response AND a fresh GET.

    `full` adds the byte-exact protected fingerprint and the preserved per-line
    identity comparison. Those run against the FRESH GET, which is the
    authoritative record; the PUT response is checked for everything Zoho
    always returns -- identity, status, the complete line list in order, each
    line's percentage and its exact discount amount, and the totals -- so a
    wrong write is caught immediately without a response-shape difference
    turning a correct write into a permanent lock.
    """
    if not isinstance(after, dict):
        raise DraftToolError(f"{label} returned no estimate record.")
    estimate = evidence["estimate"]
    expected = evidence["expected"]
    for key, want in (
        ("estimate_id", estimate["estimate_id"]),
        ("estimate_number", estimate["estimate_number"]),
        ("reference_number", estimate["reference_number"]),
        ("customer_id", estimate["customer_id"]),
        ("status", estimate["status"]),
    ):
        actual = str(after.get(key) if after.get(key) is not None else "")
        if actual != want:
            raise DraftToolError(
                f"{label} {key} is {actual!r}, not the preserved {want!r}. Stop and reconcile."
            )
    if after.get("discount_type") != "item_level" or after.get("is_discount_before_tax") is not True:
        raise DraftToolError(f"{label} no longer shows an item-level discount before tax.")
    lines = after.get("line_items")
    if not isinstance(lines, list) or len(lines) != estimate["line_count"]:
        raise DraftToolError(
            f"{label} carries {len(lines) if isinstance(lines, list) else 'no'} lines, not the "
            f"preserved {estimate['line_count']}. Stop and reconcile."
        )
    before_lines = estimate["before_state"].get("line_items") or []
    for index, (line, want_line) in enumerate(zip(lines, expected["lines"])):
        if not isinstance(line, dict):
            raise DraftToolError(f"{label} line {index + 1} is not an object.")
        if str(line.get("line_item_id") or "") != want_line["line_item_id"]:
            raise DraftToolError(
                f"{label} line {index + 1} is line_item_id {line.get('line_item_id')!r}, not the "
                f"preserved {want_line['line_item_id']!r}. Stop and reconcile."
            )
        before_line = before_lines[index] if index < len(before_lines) else {}
        if full:
            for key in ("item_id", "name", "description", "unit", "tax_id", "item_order"):
                if str(before_line.get(key) or "") != str(line.get(key) or ""):
                    raise DraftToolError(
                        f"{label} line {index + 1} {key} moved from {before_line.get(key)!r} to "
                        f"{line.get(key)!r}. Stop and reconcile."
                    )
            for key in ("quantity", "rate"):
                if live_decimal(before_line.get(key), f"staged line {index} {key}") != live_decimal(
                    line.get(key), f"{label} line {index} {key}"
                ):
                    raise DraftToolError(
                        f"{label} line {index + 1} {key} moved. Stop and reconcile."
                    )
        percent = returned_discount_percent(
            line.get("discount"), f"{label} line {index} discount"
        )
        if percent != Decimal(TDS_LINE_DISCOUNT[:-1]):
            raise DraftToolError(
                f"{label} line {index + 1} discount is {line.get('discount')!r}, not 10 percent."
            )
        for key, want in (
            ("discount_amount", want_line["discount_amount"]),
            ("item_total", want_line["item_total"]),
        ):
            actual = live_decimal(line.get(key), f"{label} line {index} {key}")
            if actual != Decimal(want):
                raise DraftToolError(
                    f"{label} line {index + 1} {key} is {actual}, not the approved {want}. "
                    "Stop and reconcile."
                )
    for key in ("sub_total", "tax_total", "total"):
        actual = live_decimal(after.get(key), f"{label} {key}")
        if actual != Decimal(expected[key]):
            raise DraftToolError(
                f"{label} {key} is {actual}, not the approved {expected[key]}. Stop and reconcile."
            )
    if not full:
        return
    if "discount_total" in estimate["before_state"]:
        actual = live_decimal(after.get("discount_total"), f"{label} discount_total")
        if actual != Decimal(expected["discount_total"]):
            raise DraftToolError(
                f"{label} discount_total is {actual}, not the approved {expected['discount_total']}."
            )
    for key, want in (
        ("discount_percent", Decimal(TDS_LINE_DISCOUNT[:-1])),
        ("uninvoiced_amount", Decimal(expected["total"])),
    ):
        if key in estimate["before_state"]:
            actual = live_decimal(after.get(key), f"{label} {key}")
            if actual != want:
                raise DraftToolError(
                    f"{label} {key} is {actual}, not the expected calculated {want}."
                )
    tax_rows = after.get("taxes")
    if not isinstance(tax_rows, list) or len(tax_rows) != 2:
        raise DraftToolError(f"{label} must preserve the two GST/QST tax rows.")
    component_amounts: dict[str, Decimal] = {}
    for row in tax_rows:
        if not isinstance(row, dict):
            raise DraftToolError(f"{label} returned an invalid tax row.")
        name = str(row.get("tax_name") or "").upper()
        component = "GST" if name.startswith("GST") else "QST" if name.startswith("QST") else ""
        if not component or component in component_amounts:
            raise DraftToolError(f"{label} did not preserve one GST row and one QST row.")
        component_amounts[component] = live_decimal(
            row.get("tax_amount"), f"{label} {component} tax_amount"
        )
    for component, want in (
        ("GST", Decimal(expected["tax_gst"])),
        ("QST", Decimal(expected["tax_qst"])),
    ):
        if component_amounts.get(component) != want:
            raise DraftToolError(
                f"{label} {component} is {component_amounts.get(component)}, not the approved {want}."
            )
    protected = correction_protected_state(after)
    if protected != estimate["protected_state"] or not secrets.compare_digest(
        digest_for(protected), str(estimate["protected_state_sha256"])
    ):
        moved = sorted(
            key for key in set(protected) | set(estimate["protected_state"])
            if protected.get(key) != estimate["protected_state"].get(key)
        )
        raise DraftToolError(
            f"{label} changed protected field(s) that must not move: {', '.join(moved) or 'unknown'}. "
            "Stop and reconcile."
        )


def command_commit_tds_discount_correction(args: argparse.Namespace) -> None:
    plan_path = contained_correction_plan(args.plan)
    plan, estimate_id, target, evidence = load_correction_plan(plan_path)
    # Approval is checked before the lock, the vault, the token and the network.
    require_exact_approval(args.approval)
    number = target["estimate_number"]
    lock = correction_lock_path(plan["sha256"])
    write_correction_lock(lock, {
        "plan_sha256": plan["sha256"],
        "kind": CORRECTION_KIND,
        "status": "in_flight",
        "estimate_id": estimate_id,
        "started_utc": utc_now().isoformat(),
    }, exclusive=True)
    write_attempted = False
    try:
        vault = zoho_tool.load_vault()
        scopes = [str(scope) for scope in vault.get("scopes") or []]
        zoho_tool.validate_scopes(scopes)
        if ESTIMATE_UPDATE_SCOPE not in scopes:
            raise DraftToolError(
                f"Saved Zoho connection lacks {ESTIMATE_UPDATE_SCOPE}. Run "
                "PREPARE_DADO_ZOHO_ACCESS.bat, create the grant in the Zoho API Console, then "
                "REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat. No PUT was issued."
            )
        organization_id = books_organization_id(vault)
        if organization_id != str(plan["books_organization_id"]):
            raise DraftToolError(
                "REFUSED: the live FRP Depot Books organization does not match the plan."
            )
        access_token, vault = zoho_tool.refresh_access_token(vault)
        current = get_estimate(access_token, vault, estimate_id)
        staged_before = evidence["estimate"]["before_state"]
        current_prewrite = correction_prewrite_state(current)
        staged_prewrite = correction_prewrite_state(staged_before)
        if current_prewrite != staged_prewrite or not secrets.compare_digest(
            digest_for(current_prewrite), digest_for(staged_prewrite)
        ):
            raise DraftToolError(
                f"Estimate {number} changed after review. No PUT was issued."
            )
        validate_live_estimate(current, estimate_id, target)
        write_attempted = True
        result = oauth_estimate_discount_write_allowed(
            access_token,
            str(vault["api_domain"]),
            "PUT",
            f"/books/v3/estimates/{estimate_id}",
            organization_id,
            evidence["put_payload"],
        )
        verify_correction_result(result.get("estimate"), evidence, "PUT response", full=False)
        verified = get_estimate(access_token, vault, estimate_id)
        verify_correction_result(verified, evidence, "Fresh read-back", full=True)
        zoho_tool.save_vault(vault)
    except Exception as exc:
        status = "indeterminate" if write_attempted else "aborted_before_write"
        write_correction_lock(lock, {
            "plan_sha256": plan["sha256"],
            "kind": CORRECTION_KIND,
            "status": status,
            "estimate_id": estimate_id,
            "write_attempted": write_attempted,
            "plan_locked_indeterminate": write_attempted,
            "updated_utc": utc_now().isoformat(),
            "reason": str(exc)[:2000],
            "no_retry": True,
        })
        zoho_tool.append_receipt(
            f"zoho_tds_discount_correction_{status}_no_retry",
            f"estimate={number} ({estimate_id}); write_attempted={str(write_attempted).lower()}; "
            f"plan={plan_path}; sha256={plan['sha256']}; email_sent=false",
        )
        raise DraftToolError(
            f"The discount correction is {status} and this plan is permanently locked against "
            f"retry. Estimate: {number} ({estimate_id}). A PUT was "
            f"{'ISSUED -- the live estimate state is unconfirmed' if write_attempted else 'NOT issued'}. "
            f"No email was sent; this tool has no mail transport. Reason: {exc} "
            "Read the live estimate in Zoho and reconcile before staging anything new."
        ) from exc
    write_correction_lock(lock, {
        "plan_sha256": plan["sha256"],
        "kind": CORRECTION_KIND,
        "status": "committed_verified",
        "estimate_id": estimate_id,
        "updated_utc": utc_now().isoformat(),
        "no_retry": True,
    })
    zoho_tool.append_receipt(
        "zoho_tds_discount_correction_committed_verified",
        f"estimate={number} ({estimate_id}); plan={plan_path}; sha256={plan['sha256']}; "
        f"status={target['status']}; total={evidence['expected']['total']}; email_sent=false",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "kind": CORRECTION_KIND,
        "estimate_id": estimate_id,
        "estimate_number": number,
        "estimate_status": target["status"],
        "plan": str(plan_path),
        "plan_sha256": plan["sha256"],
        "line_discount": TDS_LINE_DISCOUNT,
        "sub_total": evidence["expected"]["sub_total"],
        "tax_total": evidence["expected"]["tax_total"],
        "total": evidence["expected"]["total"],
        "lines_preserved": evidence["estimate"]["line_count"],
        "email_sent": False,
        "atomic": True,
        "replay_locked": True,
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# QT-000029 Item 9 quantity correction -- implementation
#
# Everything below reaches exactly one estimate, exactly one line and exactly
# one field. The stage command takes no arguments, so there is no caller-
# supplied value anywhere in this action.
# ---------------------------------------------------------------------------


def monotonic_seconds() -> float:
    """Indirection so the fixed rehearsal window is testable without waiting."""
    return time.monotonic()


def pause(seconds: float) -> None:
    time.sleep(seconds)


def require_item9_target(estimate_id: Any) -> tuple[str, dict[str, Any]]:
    """The complete reachable surface: one ID, refused before any network."""
    key = str(estimate_id if estimate_id is not None else "").strip()
    if key != ITEM9_ESTIMATE_ID:
        raise DraftToolError(
            "REFUSED: the Item 9 quantity correction is commissioned for exactly one estimate -- "
            f"{ITEM9_TARGET['estimate_number']} ({ITEM9_ESTIMATE_ID}). {key!r} is not it; "
            "no Zoho call was made."
        )
    return key, ITEM9_TARGET


def quantity_json(value: Decimal) -> Any:
    """A whole quantity travels as a JSON integer, never as a formatted string."""
    integral = value.to_integral_value()
    return int(integral) if value == integral else float(value)


def line_figures(quantity: Decimal, rate: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Gross, 10% discount and net for one line, half-up to cents."""
    gross = (quantity * rate).quantize(CENT, rounding=ROUND_HALF_UP)
    discount = (
        gross * ITEM9_TARGET["line_discount_percent"] / Decimal("100")
    ).quantize(CENT, rounding=ROUND_HALF_UP)
    return gross, discount, gross - discount


def item9_protected_state(estimate: dict[str, Any]) -> dict[str, Any]:
    """correction_protected_state, minus what the ONE quantity change moves.

    That is the target line's quantity fields plus the header gross subtotals in
    ITEM9_GROSS_SUBTOTAL_KEYS. Every exempted key is a function of the single
    approved change and every one is verified explicitly by verify_item9_result
    against an independently recomputed figure. Every other field of every other
    line -- and every non-quantity field of the target line -- stays byte-exact.
    """
    protected = correction_protected_state(estimate)
    for key in ITEM9_GROSS_SUBTOTAL_KEYS:
        protected.pop(key, None)
    for line in protected.get("line_items_protected") or []:
        if not isinstance(line, dict):
            continue
        if str(line.get("line_item_id") or "") == ITEM9_TARGET["line_item_id"]:
            for key in ITEM9_TARGET_LINE_VOLATILE_KEYS:
                line.pop(key, None)
    return protected


def item9_target_line(
    lines: list[dict[str, Any]], target: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    matches = [
        (index, line) for index, line in enumerate(lines)
        if str(line.get("line_item_id") or "") == target["line_item_id"]
    ]
    if len(matches) != 1:
        raise DraftToolError(
            f"{target['estimate_number']} must carry exactly one line with line_item_id "
            f"{target['line_item_id']}; Zoho returned {len(matches)}. Nothing staged."
        )
    return matches[0]


def validate_item9_live_estimate(
    estimate: dict[str, Any], target: dict[str, Any]
) -> list[dict[str, Any]]:
    """Exact identity, exact sent state and exact starting arithmetic, or nothing."""
    for key, expected in (
        ("estimate_id", target["estimate_id"]),
        ("estimate_number", target["estimate_number"]),
        ("reference_number", target["reference_number"]),
        ("customer_id", target["customer_id"]),
        ("status", target["status"]),
    ):
        actual = str(estimate.get(key) if estimate.get(key) is not None else "")
        if actual != expected:
            raise DraftToolError(
                f"Live estimate {key} is {actual!r}, not the commissioned {expected!r}. Nothing staged."
            )
    if estimate.get("discount_type") != "item_level":
        raise DraftToolError(
            f"Live estimate discount_type is {estimate.get('discount_type')!r}, not 'item_level'."
        )
    if estimate.get("is_discount_before_tax") is not True:
        raise DraftToolError("Live estimate is_discount_before_tax is not exactly true.")
    for key, expected_total in (
        ("sub_total", target["current_sub_total"]),
        ("tax_total", target["current_tax_total"]),
        ("total", target["current_total"]),
    ):
        actual_total = live_decimal(estimate.get(key), f"live {key}")
        if actual_total != expected_total:
            raise DraftToolError(
                f"Live {key} is {actual_total}, not the required starting {expected_total}. "
                f"{target['estimate_number']} is not in the state this correction was "
                "commissioned against; nothing staged."
            )
    lines = live_estimate_lines(estimate, target)
    orders: set[int] = set()
    for index, line in enumerate(lines):
        if str(line.get("tax_id") or "") != target["tax_id"]:
            raise DraftToolError(
                f"Live line {index + 1} carries tax_id {line.get('tax_id')!r}, not the Quebec "
                f"GST+QST group {target['tax_id']}. Nothing staged."
            )
        order = line.get("item_order")
        if isinstance(order, bool) or not isinstance(order, int):
            raise DraftToolError(f"Live line {index + 1} has no integer item_order. Nothing staged.")
        if order in orders:
            raise DraftToolError(f"Live line {index + 1} repeats item_order {order}. Nothing staged.")
        orders.add(order)
        percent = returned_discount_percent(
            line.get("discount"), f"live line {index + 1} discount"
        )
        if percent != target["line_discount_percent"]:
            raise DraftToolError(
                f"Live line {index + 1} discount is {line.get('discount')!r}, not 10 percent. "
                "Nothing staged."
            )
        quantity = live_decimal(line.get("quantity"), f"live line {index + 1} quantity")
        rate = live_decimal(line.get("rate"), f"live line {index + 1} rate")
        gross, discount, net = line_figures(quantity, rate)
        # The percent field alone is never conclusive -- Zoho echoes a real
        # percentage as the bare number 10 too. The exact amount is what proves
        # this line is 10% and not another flat CAD 10.00 (the 2026-08-10 bug).
        actual_discount = live_decimal(
            line.get("discount_amount"), f"live line {index + 1} discount_amount"
        )
        if actual_discount != discount:
            raise DraftToolError(
                f"Live line {index + 1} discount_amount is {actual_discount}, not the exact 10% "
                f"({discount}) of its {gross} gross. It is not a percentage discount; nothing staged."
            )
        actual_net = live_decimal(line.get("item_total"), f"live line {index + 1} item_total")
        if actual_net != net:
            raise DraftToolError(
                f"Live line {index + 1} item_total is {actual_net}, not the recomputed {net}. "
                "Nothing staged."
            )
    _, target_line = item9_target_line(lines, target)
    for key, expected in (("item_id", target["item_id"]), ("name", target["item_name"])):
        if str(target_line.get(key) or "") != expected:
            raise DraftToolError(
                f"The target line {key} is {target_line.get(key)!r}, not the commissioned "
                f"{expected!r}. Nothing staged."
            )
    if target_line.get("item_order") != target["item_order"]:
        raise DraftToolError(
            f"The target line item_order is {target_line.get('item_order')!r}, not the "
            f"commissioned {target['item_order']}. Nothing staged."
        )
    for key, expected_value in (
        ("quantity", target["current_quantity"]),
        ("rate", target["rate"]),
    ):
        actual_value = live_decimal(target_line.get(key), f"target line {key}")
        if actual_value != expected_value:
            raise DraftToolError(
                f"The target line {key} is {actual_value}, not the commissioned {expected_value}. "
                "Nothing staged."
            )
    return lines


def item9_expected(lines: list[dict[str, Any]], target: dict[str, Any]) -> dict[str, Any]:
    """Recompute BOTH states from the live quantities and rates alone.

    The current figures must reproduce the required starting totals exactly and
    the new figures must reproduce the fixed approved totals exactly, or nothing
    is staged. No approved number is ever copied through.
    """
    target_index, _ = item9_target_line(lines, target)
    zero = Decimal("0")
    current_gross = current_discount = current_net = zero
    new_gross = new_discount = new_net = zero
    expected_lines: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        quantity = live_decimal(line.get("quantity"), f"live line {index + 1} quantity")
        rate = live_decimal(line.get("rate"), f"live line {index + 1} rate")
        gross, discount, net = line_figures(quantity, rate)
        current_gross += gross
        current_discount += discount
        current_net += net
        after_quantity = target["new_quantity"] if index == target_index else quantity
        next_gross, next_discount, next_net = line_figures(after_quantity, rate)
        new_gross += next_gross
        new_discount += next_discount
        new_net += next_net
        if index != target_index and after_quantity != quantity:
            raise DraftToolError(f"Line {index + 1} is not the target line and must not move.")
        expected_lines.append({
            "line_item_id": str(line.get("line_item_id") or ""),
            "item_order": line.get("item_order"),
            "name": str(line.get("name") or ""),
            "rate": format(rate, "f"),
            "quantity_before": format(quantity, "f"),
            "quantity": format(after_quantity, "f"),
            "line_gross": money_text(next_gross),
            "discount_amount": money_text(next_discount),
            "item_total": money_text(next_net),
            "changed": index == target_index,
        })
    if [row["changed"] for row in expected_lines].count(True) != 1:
        raise DraftToolError("Exactly one line may change. Nothing staged.")
    # The live starting arithmetic must reproduce itself, or the corrected
    # figures cannot be predicted from it.
    current_combined, current_gst, current_qst = tax_on(current_net)
    if (
        current_net != target["current_sub_total"]
        or current_combined != target["current_tax_total"]
        or current_gst + current_qst != current_combined
        or current_net + current_combined != target["current_total"]
    ):
        raise DraftToolError(
            f"The live lines recompute to sub_total {current_net} / tax {current_combined}, not "
            f"the required starting {target['current_sub_total']} / {target['current_tax_total']}. "
            "Nothing staged."
        )
    combined, gst, qst = tax_on(new_net)
    if gst + qst != combined:
        raise DraftToolError(
            "The combined Quebec rate and its two components disagree on the corrected tax. Nothing staged."
        )
    total = new_net + combined
    for key, computed, fixed in (
        ("sub_total", new_net, target["expected_sub_total"]),
        ("gst", gst, target["expected_gst"]),
        ("qst", qst, target["expected_qst"]),
        ("tax_total", combined, target["expected_tax_total"]),
        ("total", total, target["expected_total"]),
    ):
        if computed != fixed:
            raise DraftToolError(
                f"The corrected {key} recomputes to {computed}, not the commissioned {fixed}. Nothing staged."
            )
    target_row = expected_lines[target_index]
    before_gross, before_discount, before_net = line_figures(
        target["current_quantity"], target["rate"]
    )
    after_gross, after_discount, after_net = line_figures(
        target["new_quantity"], target["rate"]
    )
    if (
        Decimal(target_row["item_total"]) != after_net
        or current_net - new_net != before_net - after_net
        or current_net - new_net != target["current_sub_total"] - target["expected_sub_total"]
    ):
        raise DraftToolError(
            "The quantity-only delta does not account for the whole change in the subtotal. Nothing staged."
        )
    return {
        "lines": expected_lines,
        "target": {
            "line_item_id": target["line_item_id"],
            "item_order": target["item_order"],
            "name": target["item_name"],
            "rate": money_text(target["rate"]),
            "quantity_before": format(target["current_quantity"], "f"),
            "quantity_after": format(target["new_quantity"], "f"),
            "line_gross_before": money_text(before_gross),
            "line_gross_after": money_text(after_gross),
            "discount_before": money_text(before_discount),
            "discount_after": money_text(after_discount),
            "item_total_before": money_text(before_net),
            "item_total_after": money_text(after_net),
            "net_delta": money_text(before_net - after_net),
        },
        "current": {
            "list_subtotal": money_text(current_gross),
            "discount_total": money_text(current_discount),
            "sub_total": money_text(current_net),
            "tax_total": money_text(current_combined),
            "total": money_text(current_net + current_combined),
        },
        "list_subtotal": money_text(new_gross),
        "discount_total": money_text(new_discount),
        "sub_total": money_text(new_net),
        "tax_total": money_text(combined),
        "tax_gst": money_text(gst),
        "tax_qst": money_text(qst),
        "total": money_text(total),
    }


def item9_put_payload(
    estimate: dict[str, Any], lines: list[dict[str, Any]], target: dict[str, Any]
) -> dict[str, Any]:
    """The complete PUT body: preserved live values, one changed number.

    Zoho deletes existing lines that a PUT omits, so the COMPLETE live line list
    is always resent in order, each with its own line_item_id and item_id.
    """
    payload: dict[str, Any] = {}
    for key in CORRECTION_HEADER_KEYS:
        value = estimate.get(key)
        if key == "customer_id":
            payload[key] = str(value or "")
            continue
        if isinstance(value, str) and value.strip():
            payload[key] = value
    payload["discount_type"] = "item_level"
    payload["is_discount_before_tax"] = True
    target_index, _ = item9_target_line(lines, target)
    put_lines = []
    for index, line in enumerate(lines):
        put_line: dict[str, Any] = {}
        for key in CORRECTION_LINE_PUT_KEYS:
            if key in ("discount", "quantity"):
                continue
            value = line.get(key)
            if value is None or value == "":
                continue
            put_line[key] = value
        put_line["line_item_id"] = str(line.get("line_item_id") or "")
        put_line["item_id"] = str(line.get("item_id") or "")
        # A percentage discount MUST be the exact string. Echoing back the bare
        # number Zoho returns is what took CAD 10.00 off every line on
        # 2026-08-10; "10%" preserves the same 10 percent this line already has.
        put_line["discount"] = TDS_LINE_DISCOUNT
        if index == target_index:
            put_line["quantity"] = quantity_json(target["new_quantity"])
        else:
            quantity = line.get("quantity")
            live_decimal(quantity, f"live line {index + 1} quantity")
            put_line["quantity"] = quantity
        missing = sorted(CORRECTION_REQUIRED_LINE_KEYS - set(put_line))
        if missing:
            raise DraftToolError(
                f"Live line {index + 1} cannot be resent intact; missing {', '.join(missing)}."
            )
        put_lines.append(put_line)
    payload["line_items"] = put_lines
    extra = sorted(set(payload) - CORRECTION_ALLOWED_PUT_KEYS)
    if extra or not CORRECTION_REQUIRED_PUT_KEYS.issubset(payload):
        raise DraftToolError("The Item 9 quantity payload is not the exact commissioned shape.")
    return payload


def require_item9_sources(sources: Any, target: dict[str, Any]) -> None:
    """The staged evidence must be the exact closed schema, with the fixed request."""
    if not isinstance(sources, dict) or set(sources) != {"source_plan", "customer_request"}:
        raise DraftToolError("Plan source evidence is not the exact closed schema.")
    plan_evidence = sources["source_plan"]
    if not isinstance(plan_evidence, dict) or set(plan_evidence) != {
        "path", "sha256", "line_items"
    } or not isinstance(plan_evidence["line_items"], list):
        raise DraftToolError("Plan source-plan evidence is invalid.")
    if str(plan_evidence.get("sha256") or "") != target["source_plan_sha256"]:
        raise DraftToolError("Plan does not cite the immutable original source plan.")
    if sources["customer_request"] != ITEM9_CUSTOMER_REQUEST:
        raise DraftToolError(
            "Plan does not cite the exact customer request this correction was commissioned for."
        )
    if (
        live_decimal(ITEM9_CUSTOMER_REQUEST["from_quantity"], "request from_quantity")
        != target["current_quantity"]
        or live_decimal(ITEM9_CUSTOMER_REQUEST["to_quantity"], "request to_quantity")
        != target["new_quantity"]
    ):
        raise DraftToolError("The fixed customer request does not state the commissioned quantities.")


def collect_item9_source_evidence(target: dict[str, Any]) -> dict[str, Any]:
    sources = {
        "source_plan": source_plan_evidence(target),
        "customer_request": json_copy(ITEM9_CUSTOMER_REQUEST),
    }
    require_item9_sources(sources, target)
    return sources


def rehearse_item9_stable_state(
    access_token: str, vault: dict[str, Any], estimate_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bounded, read-only stable-state rehearsal of the untouched estimate.

    A fixed number of fresh GETs inside a fixed window. Anything that moves
    other than the already-proven volatile estimate_url refuses the staging, so
    the pre-write fingerprint a commit depends on is known to hold still BEFORE
    Rachad spends an approval on it. No write, no discovery-by-approval.
    """
    started = monotonic_seconds()
    observations: list[dict[str, Any]] = []
    elapsed = Decimal("0")
    for index in range(ITEM9_REHEARSAL_OBSERVATIONS):
        if index:
            pause(float(ITEM9_REHEARSAL_INTERVAL_SECONDS))
        observations.append(get_estimate(access_token, vault, estimate_id))
        elapsed = Decimal(str(round(monotonic_seconds() - started, 3)))
        if elapsed < 0 or elapsed > ITEM9_REHEARSAL_MAX_SECONDS:
            raise DraftToolError(
                f"The read-only stable-state rehearsal did not finish inside its fixed "
                f"{ITEM9_REHEARSAL_MAX_SECONDS}-second window. Nothing staged."
            )
    first = observations[0]
    baseline = correction_prewrite_state(first)
    volatile_seen: set[str] = set()
    for position, snapshot in enumerate(observations[1:], start=2):
        current = correction_prewrite_state(snapshot)
        if current != baseline:
            moved = sorted(
                key for key in set(current) | set(baseline)
                if current.get(key) != baseline.get(key)
            )
            raise DraftToolError(
                f"The live estimate is not stable: observation {position} moved field(s) "
                f"{', '.join(moved) or 'unknown'} while nothing was written. Nothing staged."
            )
        for key in ESTIMATE_PREWRITE_VOLATILE_KEYS:
            if snapshot.get(key) != first.get(key):
                volatile_seen.add(key)
    return first, {
        "observations": ITEM9_REHEARSAL_OBSERVATIONS,
        "interval_seconds": format(ITEM9_REHEARSAL_INTERVAL_SECONDS, "f"),
        "max_seconds": format(ITEM9_REHEARSAL_MAX_SECONDS, "f"),
        "elapsed_seconds": format(elapsed, "f"),
        "prewrite_sha256": digest_for(baseline),
        "observation_prewrite_sha256": [
            digest_for(correction_prewrite_state(snapshot)) for snapshot in observations
        ],
        "volatile_keys_observed": sorted(volatile_seen),
        "stable": True,
    }


def validate_item9_rehearsal(evidence: Any, before: dict[str, Any]) -> None:
    """Closed schema, fixed window, and cryptographically bound to before_state."""
    if not isinstance(evidence, dict) or set(evidence) != set(ITEM9_REHEARSAL_KEYS):
        raise DraftToolError("Plan stable-state rehearsal evidence is not the exact closed schema.")
    if evidence["stable"] is not True:
        raise DraftToolError("Plan stable-state rehearsal did not record a stable estimate.")
    if isinstance(evidence["observations"], bool) or evidence["observations"] != ITEM9_REHEARSAL_OBSERVATIONS:
        raise DraftToolError(
            f"Plan stable-state rehearsal must record exactly {ITEM9_REHEARSAL_OBSERVATIONS} observations."
        )
    if evidence["interval_seconds"] != format(ITEM9_REHEARSAL_INTERVAL_SECONDS, "f") or (
        evidence["max_seconds"] != format(ITEM9_REHEARSAL_MAX_SECONDS, "f")
    ):
        raise DraftToolError("Plan stable-state rehearsal does not use the fixed window.")
    elapsed = live_decimal(evidence["elapsed_seconds"], "rehearsal elapsed_seconds")
    if elapsed < 0 or elapsed > ITEM9_REHEARSAL_MAX_SECONDS:
        raise DraftToolError("Plan stable-state rehearsal window is invalid.")
    volatile = evidence["volatile_keys_observed"]
    if (
        not isinstance(volatile, list)
        or not all(isinstance(key, str) for key in volatile)
        or volatile != sorted(volatile)
        or len(set(volatile)) != len(volatile)
        or set(volatile) - set(ESTIMATE_PREWRITE_VOLATILE_KEYS)
    ):
        raise DraftToolError(
            "Plan stable-state rehearsal recorded field(s) moving that are not the proven "
            "volatile estimate_url."
        )
    expected_digest = digest_for(correction_prewrite_state(before))
    stated = str(evidence["prewrite_sha256"] or "")
    if not HEX_64_RE.fullmatch(stated) or not secrets.compare_digest(stated, expected_digest):
        raise DraftToolError(
            "Plan stable-state rehearsal is not bound to the staged live estimate state."
        )
    digests = evidence["observation_prewrite_sha256"]
    if not isinstance(digests, list) or len(digests) != ITEM9_REHEARSAL_OBSERVATIONS:
        raise DraftToolError("Plan stable-state rehearsal does not carry one digest per observation.")
    for index, digest in enumerate(digests):
        if not isinstance(digest, str) or not HEX_64_RE.fullmatch(digest) or not secrets.compare_digest(
            digest, expected_digest
        ):
            raise DraftToolError(
                f"Plan stable-state rehearsal observation {index + 1} does not match the staged state."
            )


def build_item9_correction(
    before: dict[str, Any], target: dict[str, Any], sources: dict[str, Any]
) -> dict[str, Any]:
    """The whole projection, derived from immutable inputs alone.

    Commit re-runs this over the staged live state and refuses unless the result
    is byte-identical to the reviewed plan, so no figure, endpoint, payload key
    or fingerprint in a plan file can be tampered with.
    """
    require_item9_sources(sources, target)
    lines = validate_item9_live_estimate(before, target)
    cross_check_source_plan(lines, sources["source_plan"]["line_items"])
    expected = item9_expected(lines, target)
    protected = item9_protected_state(before)
    return {
        "estimate": {
            "estimate_id": target["estimate_id"],
            "estimate_number": target["estimate_number"],
            "reference_number": target["reference_number"],
            "customer_id": target["customer_id"],
            "customer_name": target["customer_name"],
            "status": target["status"],
            "line_count": target["line_count"],
            "before_state": json_copy(before),
            "before_state_sha256": digest_for(before),
            "protected_state": protected,
            "protected_state_sha256": digest_for(protected),
        },
        "current": {
            "sub_total": money_text(target["current_sub_total"]),
            "tax_total": money_text(target["current_tax_total"]),
            "total": money_text(target["current_total"]),
        },
        "change": {
            "field": f"line_items[item_order={target['item_order']}].quantity",
            "line_item_id": target["line_item_id"],
            "item_id": target["item_id"],
            "item": target["item_name"],
            "from": format(target["current_quantity"], "f"),
            "to": format(target["new_quantity"], "f"),
            "rate_unchanged": money_text(target["rate"]),
            "discount_unchanged": target["line_discount"],
            "tax_unchanged": target["tax_id"],
        },
        "tax_method": {
            "basis": "Half-up on the net subtotal; combined 14.975% and 5% + 9.975% must agree.",
            "current_tax_reproduced": money_text(target["current_tax_total"]),
        },
        "expected": expected,
        "put_endpoint": f"PUT /books/v3/estimates/{target['estimate_id']}",
        "put_payload": item9_put_payload(before, lines, target),
        "status_unchanged": SENT_STATUS,
        "email_sent": False,
    }


def stage_item9_plan(
    target: dict[str, Any], evidence: dict[str, Any], sources: dict[str, Any],
    rehearsal: dict[str, Any], organization_id: str,
) -> Path:
    created = utc_now()
    core = {
        "tool": TOOL_NAME,
        "kind": ITEM9_KIND,
        "schema_version": ITEM9_SCHEMA_VERSION,
        "nonce": secrets.token_hex(16),
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=ITEM9_PLAN_LIFETIME_HOURS)).isoformat(),
        "approval_required": APPROVAL_WORD,
        "estimate_id": target["estimate_id"],
        "books_organization_id": organization_id,
        "risk": {
            "atomic": True,
            "single_put": True,
            "email_sent": False,
            "status_unchanged": SENT_STATUS,
            "write_attempted": False,
            "note": ITEM9_RISK_NOTE,
        },
        "source_evidence": sources,
        "stable_state_evidence": rehearsal,
        "live_evidence": evidence,
    }
    plan = dict(core)
    plan["sha256"] = digest_for(core)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{ITEM9_KIND}_{plan['sha256'][:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt(
        "zoho_tds_item9_quantity_correction_plan_staged_read_only",
        f"estimate={target['estimate_number']} ({target['estimate_id']}); plan={path}; "
        f"sha256={plan['sha256']}; writes=0; method=GET_ONLY",
    )
    return path


def print_item9_summary(plan: dict[str, Any], path: Path) -> None:
    evidence = plan["live_evidence"]
    estimate = evidence["estimate"]
    expected = evidence["expected"]
    change = evidence["change"]
    rehearsal = plan["stable_state_evidence"]
    print("=" * 78)
    print("STAGED PLAN -- QT-000029 ITEM 9 QUANTITY CORRECTION (NOT YET APPROVED)")
    print("=" * 78)
    print(f"Estimate      : {estimate['estimate_number']} ({estimate['estimate_id']})")
    print(f"Reference     : {estimate['reference_number']}")
    print(f"Customer      : {estimate['customer_name']} ({estimate['customer_id']})")
    print(f"Status        : {estimate['status']} (unchanged by this plan)")
    print(f"Endpoint      : {evidence['put_endpoint']} (one PUT, no retry)")
    print(f"Change        : {change['field']}")
    print(f"                {change['item']}")
    print(f"                quantity {change['from']} -> {change['to']}"
          f"  (rate {change['rate_unchanged']}, discount {change['discount_unchanged']}, tax unchanged)")
    print(f"Requested by  : {ITEM9_CUSTOMER_REQUEST['from']}, "
          f"{ITEM9_CUSTOMER_REQUEST['channel']}, {ITEM9_CUSTOMER_REQUEST['received']}")
    print(f"                \"{ITEM9_CUSTOMER_REQUEST['quote']}\"")
    print("-" * 78)
    print(f"{'#':>2}  {'Line':34} {'Qty':>6} {'Gross':>10} {'Disc 10%':>10} {'Net':>10}")
    for index, line in enumerate(expected["lines"], start=1):
        marker = "*" if line["changed"] else " "
        print(
            f"{index:>2}{marker} {line['name'][:33]:33} {line['quantity']:>6} "
            f"{line['line_gross']:>10} {line['discount_amount']:>10} {line['item_total']:>10}"
        )
    print("-" * 78)
    print(f"Current sub_total        : CAD {evidence['current']['sub_total']}")
    print(f"Current total            : CAD {evidence['current']['total']}")
    print(f"Line 9 net removed       : CAD {expected['target']['net_delta']}")
    print(f"New sub_total            : CAD {expected['sub_total']}")
    print(
        f"New tax_total            : CAD {expected['tax_total']}"
        f"  (GST {expected['tax_gst']} + QST {expected['tax_qst']})"
    )
    print(f"New total                : CAD {expected['total']}")
    print("-" * 78)
    print(f"Lines resent intact      : {len(evidence['put_payload']['line_items'])} "
          f"(every line_item_id, nothing added, removed or reordered)")
    print(f"Stable-state rehearsal   : {rehearsal['observations']} fresh GETs in "
          f"{rehearsal['elapsed_seconds']}s (limit {rehearsal['max_seconds']}s); "
          f"only volatile field(s) seen: "
          f"{', '.join(rehearsal['volatile_keys_observed']) or 'none'}")
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


def command_stage_item9_quantity_correction(args: argparse.Namespace) -> None:
    """GET-only. Takes no arguments: the estimate, line and quantities are fixed."""
    target = ITEM9_TARGET
    estimate_id, target = require_item9_target(target["estimate_id"])
    sources = collect_item9_source_evidence(target)
    vault = zoho_tool.load_vault()
    zoho_tool.validate_scopes([str(scope) for scope in vault.get("scopes") or []])
    organization_id = books_organization_id(vault)
    access_token, vault = zoho_tool.refresh_access_token(vault)
    before, rehearsal = rehearse_item9_stable_state(access_token, vault, estimate_id)
    zoho_tool.save_vault(vault)
    evidence = build_item9_correction(before, target, sources)
    validate_item9_rehearsal(rehearsal, before)
    path = stage_item9_plan(target, evidence, sources, rehearsal, organization_id)
    plan = read_json(str(path))
    print_item9_summary(plan, path)


def validate_item9_plan(plan: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if (
        plan.get("tool") != TOOL_NAME
        or plan.get("kind") != ITEM9_KIND
        or plan.get("schema_version") != ITEM9_SCHEMA_VERSION
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise DraftToolError("The plan belongs to a different tool, action or schema version.")
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise DraftToolError("Plan nonce is invalid.")
    created = parse_plan_time(plan.get("created_utc"), "creation time")
    expires = parse_plan_time(plan.get("expires_utc"), "expiry")
    if expires - created != timedelta(hours=ITEM9_PLAN_LIFETIME_HOURS):
        raise DraftToolError("Plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise DraftToolError("Plan creation time is in the future.")
    if now >= expires:
        raise DraftToolError("Plan expired. Stage a new plan for review.")
    risk = plan.get("risk")
    if not isinstance(risk, dict) or (
        risk.get("atomic") is not True
        or risk.get("single_put") is not True
        or risk.get("email_sent") is not False
        or risk.get("write_attempted") is not False
        or risk.get("status_unchanged") != SENT_STATUS
        or risk.get("note") != ITEM9_RISK_NOTE
    ):
        raise DraftToolError("Plan must disclose the exact single-atomic-PUT risk.")
    estimate_id, target = require_item9_target(plan.get("estimate_id"))
    if not ID_RE.fullmatch(str(plan.get("books_organization_id") or "")):
        raise DraftToolError("Plan organization ID is invalid.")
    sources = plan.get("source_evidence")
    evidence = plan.get("live_evidence")
    if not isinstance(sources, dict) or not isinstance(evidence, dict):
        raise DraftToolError("Plan evidence is invalid.")
    require_item9_sources(sources, target)
    # The cited source plan is re-read from disk and re-hashed here, so a plan
    # cannot carry a doctored copy of the lines it claims to be checked against.
    if sources != collect_item9_source_evidence(target):
        raise DraftToolError(
            "Plan source evidence does not match the immutable artifacts on disk. Nothing written."
        )
    staged_estimate = evidence.get("estimate")
    if not isinstance(staged_estimate, dict):
        raise DraftToolError("Plan estimate evidence is invalid.")
    before = staged_estimate.get("before_state")
    if not isinstance(before, dict):
        raise DraftToolError("Plan before-state evidence must be an object.")
    if not secrets.compare_digest(
        str(staged_estimate.get("before_state_sha256") or ""), digest_for(before)
    ):
        raise DraftToolError("Plan before-state evidence hash is invalid.")
    validate_item9_rehearsal(plan.get("stable_state_evidence"), before)
    # Re-derive EVERYTHING from the staged live state. A tampered payload,
    # endpoint, fingerprint or corrected total cannot survive this.
    if build_item9_correction(before, target, sources) != evidence:
        raise DraftToolError(
            "Plan evidence is not the canonical projection of the staged live estimate state."
        )
    if evidence["put_endpoint"] != f"PUT /books/v3/estimates/{estimate_id}":
        raise DraftToolError("Plan endpoint is not the one commissioned estimate route.")
    return estimate_id, target, evidence


def load_item9_plan(path: Path) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    plan = read_json(str(path))
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise DraftToolError("Plan hash check failed. The plan changed after review.")
    estimate_id, target, evidence = validate_item9_plan(plan)
    return plan, estimate_id, target, evidence


def require_item9_put_allowed(
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
    expected_payload: dict[str, Any],
) -> None:
    """The complete write allowlist for the Item 9 quantity correction.

    Only PUT. Only the ONE commissioned estimate. Only the commissioned keys,
    with the complete line list, every line_item_id and item_id present in
    order, every non-target field identical to the reviewed plan and exactly one
    changed quantity. Pure validation -- it touches nothing. Commit runs it once
    BEFORE the replay lock (so a bad payload is a free refusal, not a burned
    plan) and the write function runs it again as the transport's own gate.
    """
    if method != "PUT":
        raise DraftToolError("REFUSED: the Item 9 quantity correction is a PUT and nothing else.")
    match = ESTIMATE_PUT_PATH_RE.fullmatch(str(path))
    if not match or match.group(1) != ITEM9_ESTIMATE_ID:
        raise DraftToolError(
            f"REFUSED: the Item 9 quantity correction targets /books/v3/estimates/"
            f"{ITEM9_ESTIMATE_ID} and nothing else. Creation, deletion, status, sending, mailing, "
            "acceptance, conversion, attachment, template and every bulk route are unreachable."
        )
    target = ITEM9_TARGET
    if not isinstance(payload, dict) or not isinstance(expected_payload, dict):
        raise DraftToolError("REFUSED: the estimate PUT payload must be an object.")
    extra = sorted(set(payload) - CORRECTION_ALLOWED_PUT_KEYS)
    if extra:
        raise DraftToolError(
            "REFUSED: the estimate PUT payload names uncommissioned field(s): " + ", ".join(extra)
        )
    if not CORRECTION_REQUIRED_PUT_KEYS.issubset(payload):
        raise DraftToolError(
            "REFUSED: the estimate PUT payload must carry the preserved customer, number and date, "
            "the item-level pre-tax discount flags and the complete line list."
        )
    if payload.get("discount_type") != "item_level" or payload.get("is_discount_before_tax") is not True:
        raise DraftToolError("REFUSED: the correction keeps an item-level discount before tax.")
    if str(payload.get("customer_id") or "") != target["customer_id"]:
        raise DraftToolError("REFUSED: the estimate PUT names a different customer.")
    if str(payload.get("estimate_number") or "") != target["estimate_number"]:
        raise DraftToolError("REFUSED: the estimate PUT does not preserve the estimate number.")
    if str(payload.get("reference_number") or "") != target["reference_number"]:
        raise DraftToolError("REFUSED: the estimate PUT does not preserve the reference number.")
    lines = payload.get("line_items")
    if not isinstance(lines, list) or len(lines) != target["line_count"]:
        raise DraftToolError("REFUSED: the estimate PUT must carry the complete live line list.")
    expected_lines = expected_payload.get("line_items")
    if not isinstance(expected_lines, list) or len(expected_lines) != target["line_count"]:
        raise DraftToolError("REFUSED: the reviewed plan payload is not the commissioned shape.")
    changed = 0
    seen: set[str] = set()
    for index, (line, reviewed) in enumerate(zip(lines, expected_lines)):
        if not isinstance(line, dict) or not isinstance(reviewed, dict):
            raise DraftToolError("REFUSED: every PUT line must be an object.")
        unknown = sorted(set(line) - set(CORRECTION_LINE_PUT_KEYS))
        if unknown:
            raise DraftToolError(
                "REFUSED: a PUT line names uncommissioned field(s): " + ", ".join(unknown)
            )
        if not CORRECTION_REQUIRED_LINE_KEYS.issubset(line):
            raise DraftToolError("REFUSED: every PUT line must resend its own identity.")
        line_item_id = str(line.get("line_item_id") or "")
        if not ID_RE.fullmatch(line_item_id) or line_item_id in seen:
            raise DraftToolError("REFUSED: the estimate PUT repeats or omits a line_item_id.")
        seen.add(line_item_id)
        # Order and identity are pinned line by line against the reviewed plan,
        # so a line cannot be reordered, substituted or silently swapped.
        if line_item_id != str(reviewed.get("line_item_id") or ""):
            raise DraftToolError(
                f"REFUSED: PUT line {index + 1} is not the reviewed line in the reviewed order."
            )
        if line.get("discount") != TDS_LINE_DISCOUNT:
            raise DraftToolError(
                f"REFUSED: every PUT line discount must be the exact string {TDS_LINE_DISCOUNT!r}."
            )
        if set(line) != set(reviewed):
            raise DraftToolError(
                f"REFUSED: PUT line {index + 1} does not carry the reviewed field set."
            )
        for key in line:
            if line[key] == reviewed[key]:
                continue
            raise DraftToolError(
                f"REFUSED: PUT line {index + 1} {key} does not match the reviewed plan."
            )
        is_target = line_item_id == target["line_item_id"]
        quantity = live_decimal(line.get("quantity"), f"PUT line {index + 1} quantity")
        if is_target:
            if quantity != target["new_quantity"]:
                raise DraftToolError(
                    "REFUSED: the target line quantity must be exactly "
                    f"{target['new_quantity']}."
                )
            changed += 1
        elif str(line.get("item_id") or "") == "":
            raise DraftToolError("REFUSED: every PUT line must resend its Zoho item_id.")
    if changed != 1:
        raise DraftToolError(
            "REFUSED: the estimate PUT must carry the one commissioned target line exactly once."
        )
    for key in CORRECTION_ALLOWED_PUT_KEYS:
        if key == "line_items":
            continue
        if payload.get(key) != expected_payload.get(key):
            raise DraftToolError(
                f"REFUSED: the estimate PUT {key} does not match the reviewed plan."
            )
    if set(payload) != set(expected_payload):
        raise DraftToolError("REFUSED: the estimate PUT is not the reviewed payload.")
    if not ID_RE.fullmatch(str(organization_id)):
        raise DraftToolError("REFUSED: the organization ID is invalid.")


def oauth_estimate_item9_quantity_write_allowed(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
    expected_payload: dict[str, Any],
) -> dict[str, Any]:
    """The ONE write path for the Item 9 quantity correction, gated by its allowlist."""
    require_item9_put_allowed(method, path, organization_id, payload, expected_payload)
    return send_estimate_put(
        access_token, api_domain, path, organization_id, payload, "estimate quantity correction"
    )


def verify_item9_result(
    after: Any, evidence: dict[str, Any], label: str, *, full: bool = True
) -> None:
    """Post-state verification, applied to the PUT response AND a fresh GET.

    `full` adds the byte-exact protected fingerprint, the preserved per-line
    identity comparison and the tax-row breakdown. Those run against the FRESH
    GET, which is the authoritative record.
    """
    if not isinstance(after, dict):
        raise DraftToolError(f"{label} returned no estimate record.")
    estimate = evidence["estimate"]
    expected = evidence["expected"]
    for key, want in (
        ("estimate_id", estimate["estimate_id"]),
        ("estimate_number", estimate["estimate_number"]),
        ("reference_number", estimate["reference_number"]),
        ("customer_id", estimate["customer_id"]),
        ("status", estimate["status"]),
    ):
        actual = str(after.get(key) if after.get(key) is not None else "")
        if actual != want:
            raise DraftToolError(
                f"{label} {key} is {actual!r}, not the preserved {want!r}. Stop and reconcile."
            )
    if after.get("discount_type") != "item_level" or after.get("is_discount_before_tax") is not True:
        raise DraftToolError(f"{label} no longer shows an item-level discount before tax.")
    lines = after.get("line_items")
    if not isinstance(lines, list) or len(lines) != estimate["line_count"]:
        raise DraftToolError(
            f"{label} carries {len(lines) if isinstance(lines, list) else 'no'} lines, not the "
            f"preserved {estimate['line_count']}. Stop and reconcile."
        )
    before_lines = estimate["before_state"].get("line_items") or []
    for index, (line, want_line) in enumerate(zip(lines, expected["lines"])):
        if not isinstance(line, dict):
            raise DraftToolError(f"{label} line {index + 1} is not an object.")
        if str(line.get("line_item_id") or "") != want_line["line_item_id"]:
            raise DraftToolError(
                f"{label} line {index + 1} is line_item_id {line.get('line_item_id')!r}, not the "
                f"preserved {want_line['line_item_id']!r}. Stop and reconcile."
            )
        before_line = before_lines[index] if index < len(before_lines) else {}
        quantity = live_decimal(line.get("quantity"), f"{label} line {index + 1} quantity")
        if quantity != Decimal(want_line["quantity"]):
            raise DraftToolError(
                f"{label} line {index + 1} quantity is {quantity}, not the approved "
                f"{want_line['quantity']}. Stop and reconcile."
            )
        if full:
            for key in ("item_id", "name", "description", "unit", "tax_id", "item_order"):
                if str(before_line.get(key) or "") != str(line.get(key) or ""):
                    raise DraftToolError(
                        f"{label} line {index + 1} {key} moved from {before_line.get(key)!r} to "
                        f"{line.get(key)!r}. Stop and reconcile."
                    )
            if live_decimal(before_line.get("rate"), f"staged line {index + 1} rate") != live_decimal(
                line.get("rate"), f"{label} line {index + 1} rate"
            ):
                raise DraftToolError(f"{label} line {index + 1} rate moved. Stop and reconcile.")
            if want_line["changed"] and "quantity_formatted" in before_line:
                if "quantity_formatted" not in line:
                    raise DraftToolError(
                        f"{label} line {index + 1} dropped quantity_formatted. Stop and reconcile."
                    )
                if line.get("quantity_formatted") == before_line.get("quantity_formatted"):
                    raise DraftToolError(
                        f"{label} line {index + 1} still shows the old formatted quantity "
                        f"{before_line.get('quantity_formatted')!r}. Stop and reconcile."
                    )
        percent = returned_discount_percent(
            line.get("discount"), f"{label} line {index + 1} discount"
        )
        if percent != Decimal(TDS_LINE_DISCOUNT[:-1]):
            raise DraftToolError(
                f"{label} line {index + 1} discount is {line.get('discount')!r}, not 10 percent."
            )
        for key, want in (
            ("discount_amount", want_line["discount_amount"]),
            ("item_total", want_line["item_total"]),
        ):
            actual_value = live_decimal(line.get(key), f"{label} line {index + 1} {key}")
            if actual_value != Decimal(want):
                raise DraftToolError(
                    f"{label} line {index + 1} {key} is {actual_value}, not the approved {want}. "
                    "Stop and reconcile."
                )
    for key in ("sub_total", "tax_total", "total"):
        actual_value = live_decimal(after.get(key), f"{label} {key}")
        if actual_value != Decimal(expected[key]):
            raise DraftToolError(
                f"{label} {key} is {actual_value}, not the approved {expected[key]}. Stop and reconcile."
            )
    if not full:
        return
    before_state = estimate["before_state"]
    if "discount_total" in before_state:
        actual_value = live_decimal(after.get("discount_total"), f"{label} discount_total")
        if actual_value != Decimal(expected["discount_total"]):
            raise DraftToolError(
                f"{label} discount_total is {actual_value}, not the approved {expected['discount_total']}."
            )
    if "discount_percent" in before_state:
        if live_decimal(after.get("discount_percent"), f"{label} discount_percent") != live_decimal(
            before_state.get("discount_percent"), "staged discount_percent"
        ):
            raise DraftToolError(f"{label} discount_percent moved. Stop and reconcile.")
    if "uninvoiced_amount" in before_state:
        actual_value = live_decimal(after.get("uninvoiced_amount"), f"{label} uninvoiced_amount")
        if actual_value != Decimal(expected["total"]):
            raise DraftToolError(
                f"{label} uninvoiced_amount is {actual_value}, not the expected {expected['total']}."
            )
    # The gross subtotal before discount. Zoho recomputes it from quantity x rate,
    # so a correct quantity change MUST move it -- it is exempt from the byte-exact
    # fingerprint and asserted here instead, against the gross this tool derived
    # from the live quantities and rates alone.
    gross = Decimal(expected["list_subtotal"])
    if "sub_total_exclusive_of_discount" in before_state:
        actual_value = live_decimal(
            after.get("sub_total_exclusive_of_discount"),
            f"{label} sub_total_exclusive_of_discount",
        )
        if actual_value != gross:
            raise DraftToolError(
                f"{label} sub_total_exclusive_of_discount is {actual_value}, not the recomputed "
                f"{gross}. Stop and reconcile."
            )
    if (
        "bcy_sub_total_exclusive_of_discount" in before_state
        and "sub_total_exclusive_of_discount" in before_state
        and live_decimal(
            before_state.get("bcy_sub_total_exclusive_of_discount"),
            "staged bcy_sub_total_exclusive_of_discount",
        )
        == live_decimal(
            before_state.get("sub_total_exclusive_of_discount"),
            "staged sub_total_exclusive_of_discount",
        )
    ):
        actual_value = live_decimal(
            after.get("bcy_sub_total_exclusive_of_discount"),
            f"{label} bcy_sub_total_exclusive_of_discount",
        )
        if actual_value != gross:
            raise DraftToolError(
                f"{label} bcy_sub_total_exclusive_of_discount is {actual_value}, not the "
                f"recomputed {gross}. Stop and reconcile."
            )
    # Base-currency mirrors, checked only where the staged record proves CAD is
    # the base currency (so a foreign-currency estimate can never false-fail).
    for bcy_key, plain_key in (
        ("bcy_sub_total", "sub_total"), ("bcy_tax_total", "tax_total"), ("bcy_total", "total")
    ):
        if bcy_key in before_state and plain_key in before_state:
            if live_decimal(before_state.get(bcy_key), f"staged {bcy_key}") != live_decimal(
                before_state.get(plain_key), f"staged {plain_key}"
            ):
                continue
            actual_value = live_decimal(after.get(bcy_key), f"{label} {bcy_key}")
            if actual_value != Decimal(expected[plain_key]):
                raise DraftToolError(
                    f"{label} {bcy_key} is {actual_value}, not the approved {expected[plain_key]}."
                )
    tax_rows = after.get("taxes")
    if not isinstance(tax_rows, list) or len(tax_rows) != 2:
        raise DraftToolError(f"{label} must preserve the two GST/QST tax rows.")
    component_amounts: dict[str, Decimal] = {}
    for row in tax_rows:
        if not isinstance(row, dict):
            raise DraftToolError(f"{label} returned an invalid tax row.")
        name = str(row.get("tax_name") or "").upper()
        component = "GST" if name.startswith("GST") else "QST" if name.startswith("QST") else ""
        if not component or component in component_amounts:
            raise DraftToolError(f"{label} did not preserve one GST row and one QST row.")
        component_amounts[component] = live_decimal(
            row.get("tax_amount"), f"{label} {component} tax_amount"
        )
    for component, want in (
        ("GST", Decimal(expected["tax_gst"])),
        ("QST", Decimal(expected["tax_qst"])),
    ):
        if component_amounts.get(component) != want:
            raise DraftToolError(
                f"{label} {component} is {component_amounts.get(component)}, not the approved {want}."
            )
    protected = item9_protected_state(after)
    if protected != estimate["protected_state"] or not secrets.compare_digest(
        digest_for(protected), str(estimate["protected_state_sha256"])
    ):
        moved = sorted(
            key for key in set(protected) | set(estimate["protected_state"])
            if protected.get(key) != estimate["protected_state"].get(key)
        )
        raise DraftToolError(
            f"{label} changed protected field(s) that must not move: {', '.join(moved) or 'unknown'}. "
            "Stop and reconcile."
        )


def command_commit_item9_quantity_correction(args: argparse.Namespace) -> None:
    plan_path = contained_correction_plan(args.plan)
    plan, estimate_id, target, evidence = load_item9_plan(plan_path)
    # Approval is checked before the lock, the vault, the token and the network.
    require_exact_approval(args.approval)
    number = target["estimate_number"]
    lock = correction_lock_path(plan["sha256"])
    if lock.exists():
        raise DraftToolError(
            "REFUSED: this plan has already entered commit and cannot be replayed. "
            "No Zoho call was made."
        )
    try:
        vault = zoho_tool.load_vault()
        scopes = [str(scope) for scope in vault.get("scopes") or []]
        zoho_tool.validate_scopes(scopes)
        if ESTIMATE_UPDATE_SCOPE not in scopes:
            raise DraftToolError(
                f"Saved Zoho connection lacks {ESTIMATE_UPDATE_SCOPE}. Run "
                "PREPARE_DADO_ZOHO_ACCESS.bat, create the grant in the Zoho API Console, then "
                "REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat. No PUT was issued."
            )
        organization_id = books_organization_id(vault)
        if organization_id != str(plan["books_organization_id"]):
            raise DraftToolError(
                "REFUSED: the live FRP Depot Books organization does not match the plan."
            )
        access_token, vault = zoho_tool.refresh_access_token(vault)
        current = get_estimate(access_token, vault, estimate_id)
        staged_before = evidence["estimate"]["before_state"]
        current_prewrite = correction_prewrite_state(current)
        staged_prewrite = correction_prewrite_state(staged_before)
        if current_prewrite != staged_prewrite or not secrets.compare_digest(
            digest_for(current_prewrite), digest_for(staged_prewrite)
        ):
            moved = sorted(
                key for key in set(current_prewrite) | set(staged_prewrite)
                if current_prewrite.get(key) != staged_prewrite.get(key)
            )
            raise DraftToolError(
                f"Estimate {number} changed after review ({', '.join(moved) or 'unknown'}). "
                "No PUT was issued and this plan is not locked; stage a new plan."
            )
        current_lines = validate_item9_live_estimate(current, target)
        # Rebuild the body from the FRESH live record. The allowlist below then
        # compares the reviewed payload against reality field by field rather
        # than against itself.
        fresh_payload = item9_put_payload(current, current_lines, target)
        if fresh_payload != evidence["put_payload"]:
            raise DraftToolError(
                f"The reviewed payload no longer matches what the live {number} would produce. "
                "No PUT was issued and this plan is not locked; stage a new plan."
            )
        # The write allowlist runs here too, so a payload it would reject is a
        # free refusal rather than a permanently locked plan.
        require_item9_put_allowed(
            "PUT", f"/books/v3/estimates/{estimate_id}", organization_id,
            evidence["put_payload"], fresh_payload,
        )
    except Exception as exc:
        zoho_tool.append_receipt(
            "zoho_tds_item9_quantity_correction_refused_before_lock",
            f"estimate={number} ({estimate_id}); plan={plan_path}; sha256={plan['sha256']}; "
            f"write_attempted=false; locked=false; email_sent=false",
        )
        raise DraftToolError(
            f"The Item 9 quantity correction was refused BEFORE any write and BEFORE the replay "
            f"lock. Estimate: {number} ({estimate_id}). No PUT was issued and no email was sent. "
            f"Reason: {exc}"
        ) from exc
    write_correction_lock(lock, {
        "plan_sha256": plan["sha256"],
        "kind": ITEM9_KIND,
        "status": "in_flight",
        "estimate_id": estimate_id,
        "started_utc": utc_now().isoformat(),
    }, exclusive=True)
    write_attempted = False
    try:
        write_attempted = True
        result = oauth_estimate_item9_quantity_write_allowed(
            access_token,
            str(vault["api_domain"]),
            "PUT",
            f"/books/v3/estimates/{estimate_id}",
            organization_id,
            evidence["put_payload"],
            fresh_payload,
        )
        verify_item9_result(result.get("estimate"), evidence, "PUT response", full=False)
        verified = get_estimate(access_token, vault, estimate_id)
        verify_item9_result(verified, evidence, "Fresh read-back", full=True)
        zoho_tool.save_vault(vault)
    except Exception as exc:
        write_correction_lock(lock, {
            "plan_sha256": plan["sha256"],
            "kind": ITEM9_KIND,
            "status": "indeterminate",
            "estimate_id": estimate_id,
            "write_attempted": write_attempted,
            "plan_locked_indeterminate": True,
            "updated_utc": utc_now().isoformat(),
            "reason": str(exc)[:2000],
            "no_retry": True,
        })
        zoho_tool.append_receipt(
            "zoho_tds_item9_quantity_correction_indeterminate_no_retry",
            f"estimate={number} ({estimate_id}); write_attempted={str(write_attempted).lower()}; "
            f"plan={plan_path}; sha256={plan['sha256']}; email_sent=false",
        )
        raise DraftToolError(
            f"The Item 9 quantity correction is indeterminate and this plan is permanently locked "
            f"against retry. Estimate: {number} ({estimate_id}). A PUT was ISSUED -- the live "
            f"estimate state is unconfirmed. No email was sent; this tool has no mail transport. "
            f"Reason: {exc} Read the live estimate in Zoho and reconcile before staging anything new."
        ) from exc
    write_correction_lock(lock, {
        "plan_sha256": plan["sha256"],
        "kind": ITEM9_KIND,
        "status": "committed_verified",
        "estimate_id": estimate_id,
        "updated_utc": utc_now().isoformat(),
        "no_retry": True,
    })
    zoho_tool.append_receipt(
        "zoho_tds_item9_quantity_correction_committed_verified",
        f"estimate={number} ({estimate_id}); plan={plan_path}; sha256={plan['sha256']}; "
        f"status={target['status']}; total={evidence['expected']['total']}; email_sent=false",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "kind": ITEM9_KIND,
        "estimate_id": estimate_id,
        "estimate_number": number,
        "estimate_status": target["status"],
        "plan": str(plan_path),
        "plan_sha256": plan["sha256"],
        "changed_field": evidence["change"]["field"],
        "quantity_before": evidence["change"]["from"],
        "quantity_after": evidence["change"]["to"],
        "sub_total": evidence["expected"]["sub_total"],
        "tax_total": evidence["expected"]["tax_total"],
        "total": evidence["expected"]["total"],
        "lines_preserved": evidence["estimate"]["line_count"],
        "email_sent": False,
        "atomic": True,
        "replay_locked": True,
    }, ensure_ascii=False, indent=2))


# ===========================================================================
# PLAN A -- the ONE fixed SHM Marine Constructors JV customer
#
# Commissioned by Rachad on 2026-08-12 as the prerequisite for the INV-000051
# correction in zoho_invoice_revision_tool.py. Elaine Iverson asked for that
# invoice to be billed to SHM Marine Constructors JV, and the live read proved
# no such customer exists in FRP Depot's Books organization.
#
# The action takes NO business parameter. The contact name, company name, type,
# billing address, phone and the single primary contact are fixed in code, so
# no command line, plan file or environment value can redirect this to another
# customer or change one character of the record it creates.
#
# *** PLAN A AND PLAN B ARE DELIBERATELY NOT ATOMIC. *** This creates a real
# customer in Zoho. If the INV-000051 correction is then never staged, never
# approved, or fails, that customer REMAINS. There is no delete, deactivate,
# rename, merge, cleanup or retry route here by design -- a delete capability
# is far more dangerous than a spare customer record.
# ===========================================================================

SHM_KIND = "shm_inv000051_customer"
SHM_SCHEMA_VERSION = 1
SHM_PLAN_LIFETIME_HOURS = 24
CONTACTS_CREATE_SCOPE = "ZohoBooks.contacts.CREATE"
CONTACTS_COLLECTION_PATH = "/books/v3/contacts"
CONTACTS_COLLECTION_RE = re.compile(r"^/books/v3/contacts$")
SHM_CONTACT_NAME = "SHM Marine Constructors JV"
SHM_EXPECTED_CURRENCY = "CAD"

# The complete POST body. Nothing is computed, defaulted or read from input.
# Deliberately absent: shipping address (the client collects, so there is none
# to invent), website, tax registration, payment terms, fax, currency
# conversion, custom fields and every contact person other than Elaine.
SHM_CUSTOMER_PAYLOAD: dict[str, Any] = {
    "contact_name": SHM_CONTACT_NAME,
    "company_name": SHM_CONTACT_NAME,
    "contact_type": "customer",
    "customer_sub_type": "business",
    "billing_address": {
        "address": "343A Bay St",
        "city": "Victoria",
        "state": "BC",
        "zip": "V8T1P5",
        "country": "Canada",
        "phone": "250-590-7072",
    },
    "contact_persons": [
        {
            "first_name": "Elaine",
            "last_name": "Iverson",
            "email": "elaineiverson@ralmax.com",
            "is_primary_contact": True,
        }
    ],
}
SHM_BILLING_SUPPLIED_KEYS = ("address", "city", "state", "zip", "country", "phone")

SHM_PO_DIR = WORKING_DIR / "invoice_revision_po"
# Byte size AND digest, both fixed. A re-extracted, re-saved or re-downloaded
# source file is a different file and refuses before any network call.
SHM_SOURCE_FILES: tuple[dict[str, Any], ...] = (
    {
        "label": "client_po_pdf",
        "path": SHM_PO_DIR / "SHM PO#0031_FRP Depots.pdf",
        "bytes": 112548,
        "sha256": "623c47693f0552fa267d7a5ace7650772447f2787822c4e0d3119019b9d2e08c",
    },
    {
        "label": "client_po_text",
        "path": SHM_PO_DIR / "SHM PO#0031_FRP Depots.pdf.txt",
        "bytes": 1857,
        "sha256": "cedc078afc6467f9819ee358a744febd0af0114fb032e5eff535bb74d25d2ee8",
    },
    {
        "label": "live_preflight",
        "path": WORKING_DIR / "invoice_revision_live_preflight_20260812.json",
        "bytes": 2538,
        "sha256": "53b62f2623d482c929d6fad53ffc874c955b87a53497439de4b98f270449c671",
    },
)

# Duplicate detection walks the WHOLE contact list, unfiltered. A name search
# would only ever prove something about the rows the search matched, and the
# name Zoho stores may differ from the name searched by whitespace, case or a
# trailing character. These bounds are REFUSALS when exceeded, never a partial
# scan reported clean.
SHM_CONTACT_PER_PAGE = 200
SHM_CONTACT_MAX_PAGES = 200
SHM_CONTACT_MAX_ROWS = 40000

SHM_RISK_NOTE = (
    "ONE atomic POST that creates ONE new active customer in FRP Depot's Zoho "
    "Books organization. It is attempted exactly once: any failure, timeout or "
    "indeterminate result permanently locks this plan against retry, and no "
    "cleanup, deletion, deactivation, rename or second attempt is ever made -- "
    "reconcile in Zoho by hand. THIS PLAN IS NOT ATOMIC WITH THE INV-000051 "
    "CORRECTION: the customer created here REMAINS in Zoho even if that "
    "correction is never staged, never approved, or fails. This tool has no "
    "mail transport and sends nothing; it cannot reach an estimate, an invoice, "
    "a sales order, an attachment or the website."
)
SHM_DEFAULTS_NOTE = (
    "Zoho returns its own read-only defaults on a new contact (portal flags, "
    "payment terms, timestamps, an empty shipping address, a derived "
    "country_code and state_code). They are recorded in the receipt and are "
    "accepted only as defaults: every field this plan actually supplies is "
    "verified byte-exact against a fresh read, and any supplied field that "
    "differs is an indeterminate result, not a default."
)
SHM_PLAN_FIELDS = {
    "tool", "kind", "schema_version", "created_utc", "expires_utc", "nonce",
    "approval_required", "books_organization_id", "risk", "source_evidence",
    "live_evidence", "sha256",
}
SHM_EVIDENCE_FIELDS = {
    "organization", "duplicate_scan", "post_endpoint", "post_payload",
    "zoho_defaults_policy", "email_sent",
}


def shm_source_evidence() -> dict[str, Any]:
    """The three fixed local sources, by exact byte size and exact digest."""
    records: dict[str, Any] = {}
    for entry in SHM_SOURCE_FILES:
        path = Path(entry["path"])
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise DraftToolError(f"Source evidence is unreadable: {path}") from exc
        if size != entry["bytes"]:
            raise DraftToolError(
                f"REFUSED: source {entry['label']} is {size} bytes, not the fixed "
                f"{entry['bytes']}. Nothing staged."
            )
        digest = file_digest(path)
        if not secrets.compare_digest(digest, str(entry["sha256"])):
            raise DraftToolError(
                f"REFUSED: source {entry['label']} does not match its fixed SHA-256. Nothing staged."
            )
        records[str(entry["label"])] = {
            "path": str(path),
            "bytes": size,
            "sha256": digest,
        }
    return dict(sorted(records.items()))


def normalized_contact_name(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split()).casefold()


def enumerate_all_contacts(
    access_token: str, vault: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """EVERY contact in the organization, or a refusal. No sampling path.

    Zoho's own page_context.has_more_page is the termination proof; if it is
    absent or not a boolean the walk cannot be shown to be complete, so it
    refuses rather than returning what it happened to see.
    """
    org_id = books_organization_id(vault)
    rows: list[dict[str, Any]] = []
    page = 1
    pages = 0
    while True:
        if pages >= SHM_CONTACT_MAX_PAGES:
            raise DraftToolError(
                f"REFUSED: the contact list exceeded the {SHM_CONTACT_MAX_PAGES}-page ceiling, "
                "so a complete duplicate check could not be proven. Nothing staged."
            )
        query = urlencode(
            {
                "organization_id": org_id,
                "page": page,
                "per_page": SHM_CONTACT_PER_PAGE,
            }
        )
        result = zoho_tool.api_get(
            access_token, str(vault["api_domain"]), f"/books/v3/contacts?{query}"
        )
        batch = result.get("contacts")
        if not isinstance(batch, list):
            raise DraftToolError(
                f"REFUSED: Zoho returned no readable contact list on page {page}. "
                "A complete duplicate check is not possible. Nothing staged."
            )
        for row in batch:
            if not isinstance(row, dict):
                raise DraftToolError(
                    f"REFUSED: contact page {page} carries an unreadable row. Nothing staged."
                )
            rows.append(json_copy(row))
            if len(rows) > SHM_CONTACT_MAX_ROWS:
                raise DraftToolError(
                    f"REFUSED: the contact list exceeded the {SHM_CONTACT_MAX_ROWS}-row ceiling, "
                    "so a complete duplicate check could not be proven. Nothing staged."
                )
        pages += 1
        context = result.get("page_context")
        if not isinstance(context, dict):
            raise DraftToolError(
                f"REFUSED: Zoho returned no page context on contact page {page}, so the walk "
                "cannot be proven complete. Nothing staged."
            )
        has_more = context.get("has_more_page")
        if not isinstance(has_more, bool):
            raise DraftToolError(
                f"REFUSED: Zoho did not state has_more_page on contact page {page}, so the walk "
                "cannot be proven complete. Nothing staged."
            )
        try:
            reported_page = int(context.get("page"))
        except (TypeError, ValueError) as exc:
            raise DraftToolError(
                f"REFUSED: Zoho returned an unreadable page number on contact page {page}."
            ) from exc
        if reported_page != page:
            raise DraftToolError(
                f"REFUSED: Zoho answered contact page {reported_page} when page {page} was asked "
                "for. The walk cannot be proven complete. Nothing staged."
            )
        if not has_more:
            break
        page += 1
    return rows, {
        "pages": pages,
        "per_page": SHM_CONTACT_PER_PAGE,
        "enumerated": len(rows),
        "filtered": False,
        "complete": True,
    }


def scan_contacts_for_shm(
    rows: list[dict[str, Any]], totals: dict[str, Any]
) -> dict[str, Any]:
    """Exact duplicates refuse. Near matches are disclosed and NEVER substituted."""
    target = normalized_contact_name(SHM_CONTACT_NAME)
    exact: list[dict[str, Any]] = []
    near: list[dict[str, Any]] = []
    for row in rows:
        contact_id = str(row.get("contact_id") or "")
        names = {
            field: str(row.get(field) or "")
            for field in ("contact_name", "company_name")
        }
        matched = sorted(
            field for field, value in names.items()
            if normalized_contact_name(value) == target
        )
        record = {
            "contact_id": contact_id,
            "contact_name": names["contact_name"],
            "company_name": names["company_name"],
            "status": str(row.get("status") or ""),
            "contact_type": str(row.get("contact_type") or ""),
        }
        if matched:
            exact.append(dict(record, matched_fields=matched))
            continue
        if any("shm marine" in normalized_contact_name(value) for value in names.values()):
            near.append(record)
    scan = dict(totals)
    scan.update(
        {
            "target_name": SHM_CONTACT_NAME,
            "exact_matches": exact,
            "exact_match_count": len(exact),
            "near_matches": sorted(near, key=lambda item: item["contact_id"]),
            "near_match_count": len(near),
        }
    )
    return scan


def require_no_shm_duplicate(scan: dict[str, Any]) -> None:
    if scan.get("complete") is not True:
        raise DraftToolError(
            "REFUSED: the duplicate check is not complete, so a duplicate cannot be ruled out."
        )
    matches = scan.get("exact_matches") or []
    if matches:
        listed = ", ".join(
            f"{item.get('contact_name')!r} ({item.get('contact_id')})" for item in matches
        )
        raise DraftToolError(
            f"REFUSED: a customer already carries the exact name {SHM_CONTACT_NAME!r}: {listed}. "
            "No duplicate was created. Plan A is unnecessary -- the INV-000051 correction may use "
            "that existing record only if every field it requires matches live."
        )


def shm_organization_record(access_token: str, vault: dict[str, Any]) -> dict[str, str]:
    org_id = books_organization_id(vault)
    result = zoho_tool.api_get(
        access_token, str(vault["api_domain"]), "/books/v3/organizations"
    )
    organizations = result.get("organizations")
    if not isinstance(organizations, list) or not organizations:
        raise DraftToolError("Zoho returned no Books organizations.")
    frp = zoho_tool.frp_organization(organizations)
    if str(frp.get("organization_id") or "") != org_id:
        raise DraftToolError(
            "REFUSED: the live FRP Depot Books organization does not match the saved connection."
        )
    currency = str(frp.get("currency_code") or "")
    if currency != SHM_EXPECTED_CURRENCY:
        raise DraftToolError(
            f"REFUSED: the organization bills in {currency or 'an unknown currency'}, not "
            f"{SHM_EXPECTED_CURRENCY}. A new customer would not inherit the currency this "
            "invoice correction requires. Nothing staged."
        )
    return {
        "organization_id": org_id,
        "name": str(frp.get("name") or frp.get("organization_name") or ""),
        "currency_code": currency,
    }


def build_shm_customer(
    organization: dict[str, str], scan: dict[str, Any]
) -> dict[str, Any]:
    """The whole projection, derived from fixed constants and the live scan."""
    return {
        "organization": json_copy(organization),
        "duplicate_scan": json_copy(scan),
        "post_endpoint": f"POST {CONTACTS_COLLECTION_PATH}",
        "post_payload": json_copy(SHM_CUSTOMER_PAYLOAD),
        "zoho_defaults_policy": SHM_DEFAULTS_NOTE,
        "email_sent": False,
    }


def stage_shm_plan(
    organization: dict[str, str], sources: dict[str, Any], evidence: dict[str, Any]
) -> Path:
    created = utc_now()
    core = {
        "tool": TOOL_NAME,
        "kind": SHM_KIND,
        "schema_version": SHM_SCHEMA_VERSION,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=SHM_PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "approval_required": APPROVAL_WORD,
        "books_organization_id": organization["organization_id"],
        "risk": {
            "atomic": True,
            "single_post": True,
            "email_sent": False,
            "atomic_with_invoice_correction": False,
            "note": SHM_RISK_NOTE,
        },
        "source_evidence": sources,
        "live_evidence": evidence,
    }
    plan = dict(core)
    plan["sha256"] = digest_for(core)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{SHM_KIND}_{plan['sha256'][:8]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt(
        "zoho_shm_inv000051_customer_plan_staged",
        f"plan={path}; sha256={plan['sha256']}; enumerated="
        f"{evidence['duplicate_scan']['enumerated']}; exact_matches=0; write_attempted=false",
    )
    return path


def command_stage_shm_inv000051_customer(args: argparse.Namespace) -> None:
    sources = shm_source_evidence()
    vault = zoho_tool.load_vault()
    zoho_tool.validate_scopes([str(scope) for scope in vault.get("scopes") or []])
    access_token, vault = zoho_tool.refresh_access_token(vault)
    organization = shm_organization_record(access_token, vault)
    rows, totals = enumerate_all_contacts(access_token, vault)
    scan = scan_contacts_for_shm(rows, totals)
    require_no_shm_duplicate(scan)
    evidence = build_shm_customer(organization, scan)
    path = stage_shm_plan(organization, sources, evidence)
    zoho_tool.save_vault(vault)
    print_shm_summary(read_json(str(path)), path)


def print_shm_summary(plan: dict[str, Any], path: Path) -> None:
    payload = plan["live_evidence"]["post_payload"]
    scan = plan["live_evidence"]["duplicate_scan"]
    person = payload["contact_persons"][0]
    print(
        json.dumps(
            {
                "status": "STAGED_AWAITING_RACHADS_APPROVAL",
                "kind": SHM_KIND,
                "plan": str(path),
                "plan_sha256": plan["sha256"],
                "expires_utc": plan["expires_utc"],
                "creates": "ONE new Zoho Books customer",
                "contact_name": payload["contact_name"],
                "company_name": payload["company_name"],
                "billing_address": payload["billing_address"],
                "primary_contact": f"{person['first_name']} {person['last_name']} <{person['email']}>",
                "duplicate_check": {
                    "contacts_enumerated": scan["enumerated"],
                    "pages": scan["pages"],
                    "complete": scan["complete"],
                    "exact_matches": scan["exact_match_count"],
                    "near_matches": scan["near_match_count"],
                },
                "post_endpoint": plan["live_evidence"]["post_endpoint"],
                "email_sent": False,
                "atomic_with_invoice_correction": False,
                "not_atomic_warning": (
                    "This customer REMAINS in Zoho even if the INV-000051 correction is never "
                    "staged, never approved, or fails. There is no delete or undo route."
                ),
                "approval_required": APPROVAL_WORD,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def validate_shm_plan(plan: dict[str, Any]) -> dict[str, Any]:
    reject_extra(plan, SHM_PLAN_FIELDS, "SHM customer plan")
    if (
        plan.get("tool") != TOOL_NAME
        or plan.get("kind") != SHM_KIND
        or plan.get("schema_version") != SHM_SCHEMA_VERSION
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise DraftToolError("The plan belongs to a different tool, action or schema version.")
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise DraftToolError("Plan nonce is invalid.")
    created = parse_plan_time(plan.get("created_utc"), "creation time")
    expires = parse_plan_time(plan.get("expires_utc"), "expiry")
    if expires - created != timedelta(hours=SHM_PLAN_LIFETIME_HOURS):
        raise DraftToolError("Plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise DraftToolError("Plan creation time is in the future.")
    if now >= expires:
        raise DraftToolError("Plan expired. Stage a new plan for review.")
    risk = plan.get("risk")
    if not isinstance(risk, dict) or (
        risk.get("atomic") is not True
        or risk.get("single_post") is not True
        or risk.get("email_sent") is not False
        or risk.get("atomic_with_invoice_correction") is not False
        or risk.get("note") != SHM_RISK_NOTE
    ):
        raise DraftToolError(
            "Plan must disclose the exact single-atomic-POST risk and that it is NOT atomic "
            "with the INV-000051 correction."
        )
    if not ID_RE.fullmatch(str(plan.get("books_organization_id") or "")):
        raise DraftToolError("Plan organization ID is invalid.")
    sources = plan.get("source_evidence")
    if sources != shm_source_evidence():
        raise DraftToolError(
            "Plan source evidence is not the three fixed local sources at their fixed digests."
        )
    evidence = plan.get("live_evidence")
    if not isinstance(evidence, dict):
        raise DraftToolError("Plan live evidence is invalid.")
    reject_extra(evidence, SHM_EVIDENCE_FIELDS, "SHM customer evidence")
    organization = evidence.get("organization")
    scan = evidence.get("duplicate_scan")
    if not isinstance(organization, dict) or not isinstance(scan, dict):
        raise DraftToolError("Plan live evidence is invalid.")
    if str(organization.get("organization_id") or "") != str(plan["books_organization_id"]):
        raise DraftToolError("Plan organization evidence does not match the plan organization.")
    if organization.get("currency_code") != SHM_EXPECTED_CURRENCY:
        raise DraftToolError("Plan organization evidence is not the CAD organization.")
    # Re-derive EVERYTHING from the staged scan. A tampered endpoint, payload or
    # disclosure cannot survive this.
    if build_shm_customer(organization, scan) != evidence:
        raise DraftToolError(
            "Plan evidence is not the canonical projection of the staged live state."
        )
    if evidence["post_payload"] != SHM_CUSTOMER_PAYLOAD:
        raise DraftToolError(
            "REFUSED: the plan payload is not the ONE fixed SHM Marine Constructors JV record."
        )
    if evidence["post_endpoint"] != f"POST {CONTACTS_COLLECTION_PATH}":
        raise DraftToolError("Plan endpoint is not the one commissioned contact route.")
    require_no_shm_duplicate(scan)
    return evidence


def load_shm_plan(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = read_json(str(path))
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise DraftToolError("Plan hash check failed. The plan changed after review.")
    evidence = validate_shm_plan(plan)
    return plan, evidence


def require_shm_post_allowed(
    method: str, path: str, organization_id: str, payload: dict[str, Any]
) -> None:
    """The complete write allowlist for Plan A.

    Only POST. Only the one contact collection route -- no ID-suffixed route, no
    delete, no deactivate, no merge, no portal invite, no estimate, invoice,
    sales-order or mail route exists here or anywhere in this action. Only the
    ONE fixed payload, compared byte-for-byte against the constant.
    """
    if method != "POST":
        raise DraftToolError("REFUSED: the SHM customer creation is a POST and nothing else.")
    if not CONTACTS_COLLECTION_RE.fullmatch(str(path)):
        raise DraftToolError(
            "REFUSED: the SHM customer creation targets the one exact contact collection route. "
            "Deletion, deactivation, merging, portal invitation, estimates, invoices, sales "
            "orders, attachments, mail and every bulk route are unreachable."
        )
    if payload != SHM_CUSTOMER_PAYLOAD:
        raise DraftToolError(
            "REFUSED: the contact POST payload is not the ONE fixed SHM Marine Constructors JV "
            "record. No field of this action is caller-supplied."
        )
    if not ID_RE.fullmatch(str(organization_id)):
        raise DraftToolError("REFUSED: the organization ID is invalid.")


def oauth_shm_customer_write_allowed(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """The ONE write path for Plan A, gated by its own allowlist."""
    require_shm_post_allowed(method, path, organization_id, payload)
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
        raise DraftToolError(
            f"Zoho customer creation failed with HTTP {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise DraftToolError(
            f"Zoho customer creation outcome is indeterminate: {exc.reason}"
        ) from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DraftToolError(
            "Zoho customer creation returned invalid JSON; the outcome is indeterminate."
        ) from exc
    if not isinstance(result, dict) or result.get("code") != 0:
        message = result.get("message") if isinstance(result, dict) else "invalid response"
        raise DraftToolError(
            "Zoho customer creation returned an invalid or unknown result: " + str(message)
        )
    return result


def get_contact(access_token: str, vault: dict[str, Any], contact_id: str) -> dict[str, Any]:
    query = urlencode({"organization_id": books_organization_id(vault)})
    result = zoho_tool.api_get(
        access_token, str(vault["api_domain"]), f"/books/v3/contacts/{contact_id}?{query}"
    )
    contact = result.get("contact")
    if not isinstance(contact, dict) or str(contact.get("contact_id") or "") != str(contact_id):
        raise DraftToolError(f"Zoho contact {contact_id} was not found.")
    return json_copy(contact)


def verify_shm_contact(after: Any, label: str) -> dict[str, Any]:
    """Every field this plan supplied, proven byte-exact against a fresh read."""
    if not isinstance(after, dict):
        raise DraftToolError(f"{label} returned no contact record.")
    contact_id = str(after.get("contact_id") or "")
    if not ID_RE.fullmatch(contact_id):
        raise DraftToolError(f"{label} carries no usable customer ID.")
    for key in ("contact_name", "company_name", "contact_type", "customer_sub_type"):
        want = SHM_CUSTOMER_PAYLOAD[key]
        actual = after.get(key)
        if actual != want:
            raise DraftToolError(
                f"{label} {key} is {actual!r}, not the approved {want!r}. Stop and reconcile."
            )
    status = str(after.get("status") or "")
    if status != "active":
        raise DraftToolError(f"{label} status is {status!r}, not active. Stop and reconcile.")
    currency = str(after.get("currency_code") or "")
    if currency != SHM_EXPECTED_CURRENCY:
        raise DraftToolError(
            f"{label} bills in {currency!r}, not {SHM_EXPECTED_CURRENCY}. Stop and reconcile."
        )
    billing = after.get("billing_address")
    if not isinstance(billing, dict):
        raise DraftToolError(f"{label} carries no billing address. Stop and reconcile.")
    for key in SHM_BILLING_SUPPLIED_KEYS:
        want = SHM_CUSTOMER_PAYLOAD["billing_address"][key]
        actual = billing.get(key)
        if actual != want:
            raise DraftToolError(
                f"{label} billing {key} is {actual!r}, not the approved {want!r}. "
                "Stop and reconcile."
            )
    billing_address_id = str(billing.get("address_id") or "")
    if not ID_RE.fullmatch(billing_address_id):
        raise DraftToolError(
            f"{label} billing address carries no usable address ID. Stop and reconcile."
        )
    persons = after.get("contact_persons")
    if not isinstance(persons, list) or len(persons) != 1:
        raise DraftToolError(
            f"{label} carries {len(persons) if isinstance(persons, list) else 'no'} contact "
            "person(s), not the one approved Elaine Iverson record. Stop and reconcile."
        )
    person = persons[0]
    want_person = SHM_CUSTOMER_PAYLOAD["contact_persons"][0]
    if not isinstance(person, dict):
        raise DraftToolError(f"{label} contact person is not a record. Stop and reconcile.")
    for key in ("first_name", "last_name"):
        if person.get(key) != want_person[key]:
            raise DraftToolError(
                f"{label} contact person {key} is {person.get(key)!r}, not the approved "
                f"{want_person[key]!r}. Stop and reconcile."
            )
    if str(person.get("email") or "").casefold() != str(want_person["email"]).casefold():
        raise DraftToolError(
            f"{label} contact person email is {person.get('email')!r}, not the approved "
            f"{want_person['email']!r}. Stop and reconcile."
        )
    if person.get("is_primary_contact") is not True:
        raise DraftToolError(f"{label} contact person is not primary. Stop and reconcile.")
    supplied = set(SHM_CUSTOMER_PAYLOAD)
    return {
        "contact_id": contact_id,
        "billing_address_id": billing_address_id,
        "contact_person_id": str(person.get("contact_person_id") or ""),
        "zoho_default_keys": sorted(key for key in after if key not in supplied),
        "shipping_address_blank": not any(
            str(value or "").strip()
            for key, value in (after.get("shipping_address") or {}).items()
            if key not in ("address_id",)
        ),
    }


def command_commit_shm_inv000051_customer(args: argparse.Namespace) -> None:
    plan_path = contained_correction_plan(args.plan)
    plan, evidence = load_shm_plan(plan_path)
    # Approval is checked before the lock, the vault, the token and the network.
    require_exact_approval(args.approval)
    lock = correction_lock_path(plan["sha256"])
    if lock.exists():
        raise DraftToolError(
            "REFUSED: this plan has already entered commit and cannot be replayed. "
            "No Zoho call was made."
        )
    try:
        vault = zoho_tool.load_vault()
        scopes = [str(scope) for scope in vault.get("scopes") or []]
        zoho_tool.validate_scopes(scopes)
        if CONTACTS_CREATE_SCOPE not in scopes:
            raise DraftToolError(
                f"Saved Zoho connection lacks {CONTACTS_CREATE_SCOPE}. No POST was issued."
            )
        access_token, vault = zoho_tool.refresh_access_token(vault)
        organization = shm_organization_record(access_token, vault)
        if organization != evidence["organization"]:
            raise DraftToolError(
                "REFUSED: the live FRP Depot Books organization does not match the plan."
            )
        organization_id = organization["organization_id"]
        # A FRESH complete duplicate walk, not the staged one: a customer created
        # between staging and approval must still stop this.
        rows, totals = enumerate_all_contacts(access_token, vault)
        require_no_shm_duplicate(scan_contacts_for_shm(rows, totals))
        # The write allowlist runs here too, so a payload it would reject is a
        # free refusal rather than a permanently burned plan.
        require_shm_post_allowed(
            "POST", CONTACTS_COLLECTION_PATH, organization_id, evidence["post_payload"]
        )
    except Exception as exc:
        zoho_tool.append_receipt(
            "zoho_shm_inv000051_customer_refused_before_lock",
            f"plan={plan_path}; sha256={plan['sha256']}; write_attempted=false; locked=false; "
            "email_sent=false",
        )
        raise DraftToolError(
            "The SHM customer creation was refused BEFORE any write and BEFORE the replay lock. "
            f"No POST was issued and no email was sent. Reason: {exc}"
        ) from exc
    write_correction_lock(lock, {
        "plan_sha256": plan["sha256"],
        "kind": SHM_KIND,
        "status": "in_flight",
        "started_utc": utc_now().isoformat(),
    }, exclusive=True)
    write_attempted = False
    contact_id = ""
    try:
        write_attempted = True
        result = oauth_shm_customer_write_allowed(
            access_token,
            str(vault["api_domain"]),
            "POST",
            CONTACTS_COLLECTION_PATH,
            organization_id,
            evidence["post_payload"],
        )
        created = result.get("contact")
        verify_shm_contact(created, "POST response")
        contact_id = str(created.get("contact_id") or "")
        verified = verify_shm_contact(
            get_contact(access_token, vault, contact_id), "Fresh read-back"
        )
        if verified["contact_id"] != contact_id:
            raise DraftToolError("The fresh read-back returned a different customer ID.")
        zoho_tool.save_vault(vault)
    except Exception as exc:
        write_correction_lock(lock, {
            "plan_sha256": plan["sha256"],
            "kind": SHM_KIND,
            "status": "indeterminate",
            "contact_id": contact_id,
            "write_attempted": write_attempted,
            "plan_locked_indeterminate": True,
            "updated_utc": utc_now().isoformat(),
            "reason": str(exc)[:2000],
            "no_retry": True,
        })
        zoho_tool.append_receipt(
            "zoho_shm_inv000051_customer_indeterminate_no_retry",
            f"contact_id={contact_id or 'unknown'}; write_attempted={str(write_attempted).lower()}; "
            f"plan={plan_path}; sha256={plan['sha256']}; email_sent=false",
        )
        raise DraftToolError(
            "The SHM customer creation is indeterminate and this plan is permanently locked "
            "against retry. A POST was ISSUED -- the live customer list is unconfirmed. No "
            "email was sent; this tool has no mail transport. No deletion, deactivation or "
            f"second attempt was made. Reason: {exc} Read the customer list in Zoho and "
            "reconcile before staging anything new."
        ) from exc
    write_correction_lock(lock, {
        "plan_sha256": plan["sha256"],
        "kind": SHM_KIND,
        "status": "committed_verified",
        "contact_id": contact_id,
        "updated_utc": utc_now().isoformat(),
        "no_retry": True,
    })
    zoho_tool.append_receipt(
        "zoho_shm_inv000051_customer_committed_verified",
        f"contact_id={contact_id}; billing_address_id={verified['billing_address_id']}; "
        f"plan={plan_path}; sha256={plan['sha256']}; email_sent=false",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "kind": SHM_KIND,
        "contact_id": contact_id,
        "contact_name": SHM_CONTACT_NAME,
        "billing_address_id": verified["billing_address_id"],
        "contact_person_id": verified["contact_person_id"],
        "shipping_address_blank": verified["shipping_address_blank"],
        "zoho_default_keys": verified["zoho_default_keys"],
        "plan": str(plan_path),
        "plan_sha256": plan["sha256"],
        "email_sent": False,
        "atomic": True,
        "atomic_with_invoice_correction": False,
        "replay_locked": True,
    }, ensure_ascii=False, indent=2))


# ===========================================================================
# GENERAL EXISTING-ESTIMATE REVISION -- commissioned by Rachad 2026-08-13
#
# Rachad's instruction: an ordinary quote revision must REVISE THE EXISTING
# estimate in place, not create a replacement. Everything above this line is a
# fixed one-off correction; this action is the reusable one, and it is still
# narrow: ONE existing estimate, ONE atomic PUT, a closed input schema, an
# explicit nonblank source per proposed value, and a preserved customer,
# number, currency and status.
#
# The editable surface is exactly:
#   header : reference_number, date, expiry_date, notes, terms
#   line   : quantity, rate, discount, description, tax_id   (EXISTING lines)
# Nothing else is reachable. Lines cannot be added, deleted, omitted,
# reordered, substituted or duplicated: the complete live list is always resent
# once, in live order, each carrying its own line_item_id and item_id.
#
# *** DISCOUNT SEMANTICS ARE THE 2026-08-10 LESSON, NOT A PREFERENCE. ***
# Zoho reads a NUMERIC line discount as a flat CAD amount and only a STRING
# carrying "%" as a percentage. This action therefore accepts a percentage
# string or the exact integer 0 and refuses every other numeric discount, and
# when it RESENDS an untouched live discount it proves the live line is a
# percentage from its own discount_amount before echoing it back as a string.
# An ambiguous nonzero numeric live discount refuses the whole revision for
# free at staging rather than guessing.
# ===========================================================================

REVISION_KIND = "estimate_revision"
REVISION_SCHEMA_VERSION = 2
REVISION_PLAN_LIFETIME_HOURS = 24
# Only these two live states may be revised in place. Every other state -- and
# any state this build does not recognise -- is refused before any network call.
REVISION_ELIGIBLE_STATUSES = ("draft", "sent")
REVISION_HEADER_FIELDS = ("reference_number", "date", "expiry_date", "notes", "terms")
REVISION_LINE_FIELDS = ("quantity", "rate", "discount", "description", "tax_id")
REVISION_INPUT_KEYS = {"estimate_id", "reason", "header", "lines"}
REVISION_REQUIRED_INPUT_KEYS = {"estimate_id", "reason"}
REVISION_VALUE_KEYS = {"value", "source"}
REVISION_LINE_ENTRY_KEYS = {"line_item_id", "fields"}
REVISION_DATE_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")
REVISION_MAX_TEXT = 2000
REVISION_MAX_QUANTITY = Decimal("1000000")
REVISION_MAX_RATE = Decimal("10000000")
REVISION_MAX_LINES = 500
# The exact PUT surface for a revision. Every header key that is not being
# changed carries the PRESERVED live value; the control keys exist so that a
# full-record PUT cannot silently drop a template, salesperson, currency,
# shipping charge or adjustment by omission.
REVISION_PUT_HEADER_KEYS = (
    "customer_id", "estimate_number", "reference_number", "date", "expiry_date",
    "notes", "terms",
)
REVISION_PUT_CONTROL_KEYS = (
    "discount_type", "is_discount_before_tax", "shipping_charge", "adjustment",
    "adjustment_description", "template_id", "salesperson_id", "currency_id",
    "exchange_rate",
)
REVISION_ALLOWED_PUT_KEYS = (
    set(REVISION_PUT_HEADER_KEYS) | set(REVISION_PUT_CONTROL_KEYS) | {"line_items"}
)
REVISION_REQUIRED_PUT_KEYS = {
    "customer_id", "estimate_number", "date", "discount_type",
    "is_discount_before_tax", "line_items",
}
REVISION_LINE_PUT_KEYS = (
    "line_item_id", "item_id", "name", "description", "quantity", "rate",
    "unit", "discount", "tax_id", "item_order",
)
REVISION_REQUIRED_LINE_PUT_KEYS = {"line_item_id", "item_id", "name", "quantity", "rate"}
# Header fields Zoho itself recomputes from the lines, plus pure telemetry. Each
# money figure is verified by its own explicit rule after the PUT; EVERY other
# returned field stays inside the byte-exact protected fingerprint.
REVISION_DERIVED_KEYS = frozenset(ESTIMATE_DERIVED_KEYS) | frozenset(ITEM9_GROSS_SUBTOTAL_KEYS) | frozenset({
    "sub_total_inclusive_of_tax", "discount_applied_on_amount", "roundoff_value",
    "last_modified_by_id", "is_taxable", "tax_id", "tax_name", "tax_percentage",
    "total_quantity", "shipping_charge_exclusive_of_tax",
    "shipping_charge_inclusive_of_tax", "shipping_charge_exclusive_of_tax_formatted",
    "shipping_charge_inclusive_of_tax_formatted",
})
# Companion fields Zoho regenerates beside a field this action changed. They are
# dropped from the fingerprint ONLY for the exact line/field being changed, and
# the changed value itself is then asserted explicitly.
REVISION_LINE_FIELD_COMPANIONS = {
    "quantity": ("quantity_formatted",),
    "rate": ("rate_formatted", "bcy_rate", "bcy_rate_formatted", "pricing_scheme"),
    "discount": (),
    "description": (),
    "tax_id": (
        "tax_name", "tax_percentage", "tax_type", "tax_category_code",
        "tax_category_name", "product_tax_category",
    ),
}
REVISION_HEADER_FIELD_COMPANIONS = {
    "reference_number": (),
    "date": ("date_formatted",),
    "expiry_date": ("expiry_date_formatted",),
    "notes": (),
    "terms": (),
}
REVISION_RISK_NOTE = (
    "One PUT to one existing estimate, changing only the header and line values named in this "
    "plan. The customer, estimate number, currency, exchange rate, template, salesperson, "
    "shipping charge, adjustment, custom fields and status are preserved, and no status field "
    "is sent at all. The complete live line list is resent once in live order with every "
    "line_item_id and item_id, so no line can be added, removed, reordered, substituted or "
    "dropped by omission. The plan is locked before the PUT and stays locked on any failure, "
    "timeout or indeterminate result; there is no retry, no rollback, no lifecycle route and "
    "no mail transport anywhere in this tool."
)


def percent_text(value: Decimal) -> str:
    """A percentage as the exact string Zoho needs, never a bare number."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    result = (text or "0") + "%"
    if not PERCENT_DISCOUNT_RE.fullmatch(result):
        raise DraftToolError(f"REFUSED: {value} is not a percentage this tool can send safely.")
    return result


def number_json(value: Decimal) -> Any:
    """A money/quantity value as a JSON number, never a formatted string."""
    integral = value.to_integral_value()
    return int(integral) if value == integral else float(value)


def revision_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise DraftToolError(f"REFUSED: {label} must be text.")
    if value != value.strip():
        raise DraftToolError(f"REFUSED: {label} must not be padded with whitespace.")
    if not value:
        raise DraftToolError(
            f"REFUSED: {label} must be nonblank. This action changes a field to a stated value; "
            "it cannot clear one."
        )
    if len(value) > REVISION_MAX_TEXT:
        raise DraftToolError(f"REFUSED: {label} is longer than {REVISION_MAX_TEXT} characters.")
    return value


def revision_value_entry(raw: Any, label: str) -> dict[str, Any]:
    """Every proposed value is {value, source} with a nonblank explicit source."""
    if not isinstance(raw, dict) or set(raw) != REVISION_VALUE_KEYS:
        raise DraftToolError(
            f"REFUSED: {label} must be exactly {{\"value\": ..., \"source\": \"...\"}}."
        )
    source = raw.get("source")
    if not isinstance(source, str) or not source.strip():
        raise DraftToolError(f"REFUSED: {label} needs a nonblank explicit source.")
    if len(source) > REVISION_MAX_TEXT:
        raise DraftToolError(f"REFUSED: {label} source is longer than {REVISION_MAX_TEXT} characters.")
    return {"value": raw["value"], "source": source.strip()}


def revision_header_value(field: str, value: Any) -> str:
    label = f"header.{field}"
    text = revision_text(value, label)
    if field in ("date", "expiry_date") and not REVISION_DATE_RE.fullmatch(text):
        raise DraftToolError(f"REFUSED: {label} must be an exact YYYY-MM-DD calendar date.")
    return text


def revision_discount_value(value: Any, label: str) -> Any:
    """A percentage string, or the exact integer 0. Nothing else.

    A nonzero NUMBER is refused: Zoho would read it as a flat CAD amount, which
    is the 2026-08-10 defect. This tool has no strictly sourced flat-amount
    representation, so it refuses rather than guessing which one was meant.
    """
    if isinstance(value, bool):
        raise DraftToolError(f"REFUSED: {label} must be a percentage string or 0.")
    if isinstance(value, str):
        text = value.strip()
        if not PERCENT_DISCOUNT_RE.fullmatch(text):
            raise DraftToolError(
                f"REFUSED: {label} must be a percentage string such as \"10%\" (0-100, up to two "
                "decimals)."
            )
        return text
    if isinstance(value, int) and value == 0:
        return 0
    if isinstance(value, float) and value == 0.0:
        return 0
    raise DraftToolError(
        f"REFUSED: {label} is a nonzero number. Zoho reads a numeric line discount as a FLAT CAD "
        "amount, not a percentage; write \"10%\" for ten percent. This tool has no commissioned "
        "flat-amount discount representation."
    )


def revision_line_value(field: str, value: Any) -> Any:
    label = f"line.{field}"
    if field == "quantity":
        quantity = live_decimal(value, label)
        if quantity <= 0 or quantity > REVISION_MAX_QUANTITY:
            raise DraftToolError(
                f"REFUSED: {label} must be greater than 0 and at most {REVISION_MAX_QUANTITY}."
            )
        return quantity
    if field == "rate":
        rate = live_decimal(value, label)
        if rate < 0 or rate > REVISION_MAX_RATE:
            raise DraftToolError(
                f"REFUSED: {label} must be 0 or more and at most {REVISION_MAX_RATE}."
            )
        if -rate.as_tuple().exponent > 6:
            raise DraftToolError(f"REFUSED: {label} carries more than six decimal places.")
        return rate
    if field == "discount":
        return revision_discount_value(value, label)
    if field == "description":
        return revision_text(value, label)
    if field == "tax_id":
        text = revision_text(value, label)
        if not ID_RE.fullmatch(text):
            raise DraftToolError(f"REFUSED: {label} must be a positive numeric Zoho tax ID.")
        return text
    raise DraftToolError(f"REFUSED: {label} is not an editable line field.")


def revision_intent(raw: Any) -> dict[str, Any]:
    """The closed input schema, normalized once so stage and commit agree."""
    if not isinstance(raw, dict):
        raise DraftToolError("REFUSED: the revision input must be one JSON object.")
    unknown = sorted(set(raw) - REVISION_INPUT_KEYS)
    if unknown:
        raise DraftToolError(
            "REFUSED: the revision input names uncommissioned field(s): " + ", ".join(unknown)
            + ". The exact schema is: " + ", ".join(sorted(REVISION_INPUT_KEYS)) + "."
        )
    missing = sorted(REVISION_REQUIRED_INPUT_KEYS - set(raw))
    if missing:
        raise DraftToolError("REFUSED: the revision input is missing " + ", ".join(missing) + ".")
    estimate_id = str(raw.get("estimate_id") if raw.get("estimate_id") is not None else "").strip()
    if not ID_RE.fullmatch(estimate_id):
        raise DraftToolError(
            "REFUSED: estimate_id must be a positive numeric Zoho estimate ID. No Zoho call was made."
        )
    reason = revision_text(raw.get("reason"), "reason")
    header_raw = raw.get("header", {})
    if header_raw is None:
        header_raw = {}
    if not isinstance(header_raw, dict):
        raise DraftToolError("REFUSED: header must be an object of field -> {value, source}.")
    unknown = sorted(set(header_raw) - set(REVISION_HEADER_FIELDS))
    if unknown:
        raise DraftToolError(
            "REFUSED: header names uneditable field(s): " + ", ".join(unknown)
            + ". Editable header fields are: " + ", ".join(REVISION_HEADER_FIELDS) + "."
        )
    header: dict[str, Any] = {}
    for field in REVISION_HEADER_FIELDS:
        if field not in header_raw:
            continue
        entry = revision_value_entry(header_raw[field], f"header.{field}")
        header[field] = {
            "value": revision_header_value(field, entry["value"]),
            "source": entry["source"],
        }
    lines_raw = raw.get("lines", [])
    if lines_raw is None:
        lines_raw = []
    if not isinstance(lines_raw, list):
        raise DraftToolError("REFUSED: lines must be a list of existing-line change objects.")
    if len(lines_raw) > REVISION_MAX_LINES:
        raise DraftToolError(f"REFUSED: at most {REVISION_MAX_LINES} line changes are accepted.")
    lines: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(lines_raw):
        label = f"lines[{index}]"
        if not isinstance(entry, dict) or set(entry) != REVISION_LINE_ENTRY_KEYS:
            raise DraftToolError(
                f"REFUSED: {label} must be exactly {{\"line_item_id\": \"...\", \"fields\": {{...}}}}."
            )
        line_item_id = str(entry.get("line_item_id") if entry.get("line_item_id") is not None else "").strip()
        if not ID_RE.fullmatch(line_item_id):
            raise DraftToolError(f"REFUSED: {label}.line_item_id must be a positive numeric ID.")
        if line_item_id in seen:
            raise DraftToolError(
                f"REFUSED: {label} names line_item_id {line_item_id} twice. One entry per existing line."
            )
        seen.add(line_item_id)
        fields_raw = entry.get("fields")
        if not isinstance(fields_raw, dict) or not fields_raw:
            raise DraftToolError(f"REFUSED: {label}.fields must name at least one editable field.")
        unknown = sorted(set(fields_raw) - set(REVISION_LINE_FIELDS))
        if unknown:
            raise DraftToolError(
                f"REFUSED: {label}.fields names uneditable field(s): " + ", ".join(unknown)
                + ". Editable line fields are: " + ", ".join(REVISION_LINE_FIELDS) + "."
            )
        fields: dict[str, Any] = {}
        for field in REVISION_LINE_FIELDS:
            if field not in fields_raw:
                continue
            value_entry = revision_value_entry(fields_raw[field], f"{label}.fields.{field}")
            value = revision_line_value(field, value_entry["value"])
            if isinstance(value, Decimal):
                value = format(value, "f")
            fields[field] = {"value": value, "source": value_entry["source"]}
        lines.append({"line_item_id": line_item_id, "fields": fields})
    if not header and not lines:
        raise DraftToolError(
            "REFUSED: the revision input proposes no change. Name at least one header or line field."
        )
    lines.sort(key=lambda item: int(item["line_item_id"]))
    return {
        "estimate_id": estimate_id,
        "reason": reason,
        "header": header,
        "lines": lines,
    }


def revision_line_intent(intent: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["line_item_id"]: entry["fields"] for entry in intent["lines"]}


def get_active_taxes(access_token: str, vault: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The organization's active tax list, read-only, used to price a tax change."""
    query = urlencode({"organization_id": books_organization_id(vault)})
    result = zoho_tool.api_get(
        access_token, str(vault["api_domain"]), f"/books/v3/settings/taxes?{query}"
    )
    rows = result.get("taxes")
    if not isinstance(rows, list):
        raise DraftToolError("Zoho returned no readable tax list. Nothing staged.")
    taxes: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise DraftToolError("Zoho returned an unreadable tax row. Nothing staged.")
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


def revision_tax_evidence(
    taxes: dict[str, dict[str, Any]], tax_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Only the tax rows this revision actually needs, pinned into the plan."""
    evidence: dict[str, dict[str, Any]] = {}
    for tax_id in sorted(set(tax_ids)):
        row = taxes.get(tax_id)
        if row is None:
            raise DraftToolError(
                f"REFUSED: tax {tax_id} is not an active tax in this Zoho organization. Nothing staged."
            )
        if row["is_inactive"] or row["status"].casefold() != "active":
            raise DraftToolError(f"REFUSED: tax {tax_id} is not active. Nothing staged.")
        evidence[tax_id] = json_copy(row)
    return evidence


def revision_line_discount(
    line: dict[str, Any], index: int, gross: Decimal, discount_type: str
) -> Any:
    """The SAFE re-send representation of a line's untouched live discount.

    A live percentage is proven from discount_amount before it is echoed back as
    a string; a live flat amount, or a value that could be either, refuses the
    whole revision for free at staging.
    """
    if discount_type != "item_level":
        return None
    raw = line.get("discount")
    amount = live_decimal(line.get("discount_amount"), f"live line {index + 1} discount_amount")
    if isinstance(raw, str) and raw.strip().endswith("%"):
        text = raw.strip()
        if not PERCENT_DISCOUNT_RE.fullmatch(text):
            raise DraftToolError(
                f"REFUSED: live line {index + 1} carries the unusable discount {raw!r}. Nothing staged."
            )
        return text
    value = live_decimal(raw, f"live line {index + 1} discount")
    if value == 0:
        if amount != 0:
            raise DraftToolError(
                f"REFUSED: live line {index + 1} shows a zero discount but a {amount} discount "
                "amount. Nothing staged."
            )
        return 0
    as_percent = (gross * value / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
    if amount == as_percent and amount != value:
        return percent_text(value)
    raise DraftToolError(
        f"REFUSED: live line {index + 1} carries the bare number {raw!r} as its discount and its "
        f"{amount} discount amount does not prove it is a percentage. Zoho reads a bare number as a "
        "flat CAD amount; this tool has no commissioned flat-amount representation, so it will not "
        "guess. Nothing staged."
    )


def revision_live_lines(estimate: dict[str, Any]) -> list[dict[str, Any]]:
    """Every live line, proven usable and uniquely identified. No sampling."""
    lines = estimate.get("line_items")
    if not isinstance(lines, list) or not lines:
        raise DraftToolError("The live estimate carries no readable line list. Nothing staged.")
    if len(lines) > REVISION_MAX_LINES:
        raise DraftToolError(
            f"REFUSED: the live estimate carries more than {REVISION_MAX_LINES} lines. Nothing staged."
        )
    seen: set[str] = set()
    orders: set[Any] = set()
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise DraftToolError(f"Live line_items[{index}] is not an object. Nothing staged.")
        line_item_id = str(line.get("line_item_id") or "")
        item_id = str(line.get("item_id") or "")
        if not ID_RE.fullmatch(line_item_id):
            raise DraftToolError(f"Live line_items[{index}] has no usable line_item_id.")
        if not ID_RE.fullmatch(item_id):
            raise DraftToolError(
                f"Live line_items[{index}] is not linked to a Zoho item. This action resends every "
                "line with its own item_id and refuses a free-text line."
            )
        if line_item_id in seen:
            raise DraftToolError(f"Live line_items[{index}] repeats line_item_id {line_item_id}.")
        seen.add(line_item_id)
        if not str(line.get("name") or "").strip():
            raise DraftToolError(f"Live line_items[{index}] has no item name.")
        order = line.get("item_order")
        if isinstance(order, bool) or not isinstance(order, int):
            raise DraftToolError(f"Live line_items[{index}] has no integer item_order.")
        if order in orders:
            raise DraftToolError(f"Live line_items[{index}] repeats item_order {order}.")
        orders.add(order)
    return [json_copy(line) for line in lines]


def validate_revision_live_estimate(
    estimate: dict[str, Any], intent: dict[str, Any]
) -> list[dict[str, Any]]:
    """Exact identity, an eligible status and a revisable shape, or nothing."""
    estimate_id = intent["estimate_id"]
    if str(estimate.get("estimate_id") or "") != estimate_id:
        raise DraftToolError(
            f"Live estimate_id is {estimate.get('estimate_id')!r}, not the requested {estimate_id!r}."
        )
    status = str(estimate.get("status") or "")
    if status not in REVISION_ELIGIBLE_STATUSES:
        raise DraftToolError(
            f"REFUSED: estimate {estimate.get('estimate_number')!r} is {status!r}. Only an estimate "
            f"that is exactly {' or '.join(repr(value) for value in REVISION_ELIGIBLE_STATUSES)} may "
            "be revised in place. Accepted, declined, invoiced, expired, void, deleted and unknown "
            "states are refused. Nothing staged."
        )
    if not str(estimate.get("estimate_number") or "").strip():
        raise DraftToolError("The live estimate has no estimate number. Nothing staged.")
    if not ID_RE.fullmatch(str(estimate.get("customer_id") or "")):
        raise DraftToolError("The live estimate has no usable customer ID. Nothing staged.")
    discount_type = str(estimate.get("discount_type") or "")
    if discount_type not in ("item_level", "entity_level"):
        raise DraftToolError(
            f"REFUSED: the live estimate discount_type is {discount_type!r}, which this action does "
            "not know how to preserve. Nothing staged."
        )
    if not isinstance(estimate.get("is_discount_before_tax"), bool):
        raise DraftToolError("The live estimate is_discount_before_tax is not a boolean.")
    entity_discount = live_decimal(estimate.get("discount", 0), "live estimate discount")
    if entity_discount != 0:
        raise DraftToolError(
            f"REFUSED: the live estimate carries an entity-level discount of {entity_discount}. This "
            "action has no commissioned representation for it and will not risk restating it. "
            "Nothing staged."
        )
    lines = revision_live_lines(estimate)
    known = {str(line.get("line_item_id") or "") for line in lines}
    unknown = sorted(set(revision_line_intent(intent)) - known)
    if unknown:
        raise DraftToolError(
            "REFUSED: the revision names line_item_id(s) that are not on the live estimate: "
            + ", ".join(unknown)
            + ". This action cannot add, substitute or invent a line. Nothing staged."
        )
    return lines


def revision_line_projection(
    lines: list[dict[str, Any]], intent: dict[str, Any], discount_type: str
) -> list[dict[str, Any]]:
    """Before and after for every live line, in live order, exactly once each."""
    changes = revision_line_intent(intent)
    projection: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        line_item_id = str(line.get("line_item_id") or "")
        fields = changes.get(line_item_id, {})
        quantity_before = live_decimal(line.get("quantity"), f"live line {index + 1} quantity")
        rate_before = live_decimal(line.get("rate"), f"live line {index + 1} rate")
        gross_before = (quantity_before * rate_before).quantize(CENT, rounding=ROUND_HALF_UP)
        discount_before = revision_line_discount(line, index, gross_before, discount_type)
        description_before = str(line.get("description") or "")
        tax_before = str(line.get("tax_id") or "")
        quantity = (
            live_decimal(fields["quantity"]["value"], f"line {index + 1} new quantity")
            if "quantity" in fields else quantity_before
        )
        rate = (
            live_decimal(fields["rate"]["value"], f"line {index + 1} new rate")
            if "rate" in fields else rate_before
        )
        if "discount" in fields:
            if discount_type != "item_level":
                raise DraftToolError(
                    f"REFUSED: line {index + 1} asks for a line discount, but the live estimate uses "
                    f"a {discount_type} discount. Nothing staged."
                )
            discount = fields["discount"]["value"]
        else:
            discount = discount_before
        description = (
            fields["description"]["value"] if "description" in fields else description_before
        )
        tax_id = fields["tax_id"]["value"] if "tax_id" in fields else tax_before
        gross = (quantity * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        if isinstance(discount, str):
            percent = Decimal(discount[:-1])
            discount_amount = (gross * percent / Decimal("100")).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
        else:
            percent = Decimal("0")
            discount_amount = Decimal("0")
        net = gross - discount_amount
        changed = sorted(fields)
        projection.append({
            "line_item_id": line_item_id,
            "item_id": str(line.get("item_id") or ""),
            "item_order": line.get("item_order"),
            "name": str(line.get("name") or ""),
            "changed_fields": changed,
            "quantity_before": format(quantity_before, "f"),
            "quantity": format(quantity, "f"),
            "rate_before": format(rate_before, "f"),
            "rate": format(rate, "f"),
            "discount_before": discount_before,
            "discount": discount,
            "discount_percent": format(percent, "f"),
            "description_before": description_before,
            "description": description,
            "tax_id_before": tax_before,
            "tax_id": tax_id,
            "line_gross": money_text(gross),
            "discount_amount": money_text(discount_amount),
            "item_total": money_text(net),
        })
    return projection


def revision_totals(
    projection: list[dict[str, Any]], taxes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Independent Decimal totals, with tax uncertainty disclosed rather than hidden.

    Tax is computed per tax bucket on that bucket's net, which is how Zoho's
    entity-level tax rounding behaves, and cross-checked against a per-line sum.
    A tax group, a compound tax, an unknown tax row or a disagreement between the
    two methods makes the tax and grand total NON-EXACT, and the plan says so
    instead of asserting a figure this tool cannot prove.
    """
    zero = Decimal("0")
    gross_total = discount_total = net_total = zero
    buckets: dict[str, Decimal] = {}
    per_line_tax = zero
    uncertain: list[str] = []
    for row in projection:
        gross_total += Decimal(row["line_gross"])
        discount_total += Decimal(row["discount_amount"])
        net = Decimal(row["item_total"])
        net_total += net
        tax_id = row["tax_id"]
        if not tax_id:
            uncertain.append(f"line {row['line_item_id']} carries no tax")
            continue
        tax_row = taxes.get(tax_id)
        if tax_row is None:
            uncertain.append(f"tax {tax_id} is not in the pinned active tax list")
            continue
        if tax_row["tax_type"] != "tax":
            uncertain.append(
                f"tax {tax_row['tax_name']} ({tax_id}) is a {tax_row['tax_type']}, so its component "
                "rounding is not predictable here"
            )
        percentage = Decimal(tax_row["tax_percentage"])
        buckets[tax_id] = buckets.get(tax_id, zero) + net
        per_line_tax += (net * percentage / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
    bucket_rows = []
    bucket_tax = zero
    for tax_id in sorted(buckets):
        tax_row = taxes[tax_id]
        percentage = Decimal(tax_row["tax_percentage"])
        amount = (buckets[tax_id] * percentage / Decimal("100")).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        bucket_tax += amount
        bucket_rows.append({
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
        "list_subtotal": money_text(gross_total),
        "discount_total": money_text(discount_total),
        "sub_total": money_text(net_total),
        "tax_rows": bucket_rows,
        "tax_total": money_text(bucket_tax),
        "total": money_text(net_total + bucket_tax),
        "tax_certainty": "exact" if exact else "disclosed_uncertain",
        "tax_uncertainty_reasons": sorted(set(uncertain)),
        "tax_total_asserted": exact,
    }


def revision_put_payload(
    estimate: dict[str, Any], projection: list[dict[str, Any]], intent: dict[str, Any]
) -> dict[str, Any]:
    """The complete PUT body: preserved live values, the stated changes, nothing else.

    Zoho deletes existing lines that a PUT omits, so the COMPLETE live line list
    is always resent in live order, each carrying its own line_item_id and
    item_id. There is no status key here and no key that could mail, convert or
    restatus anything.
    """
    header = intent["header"]
    payload: dict[str, Any] = {"customer_id": str(estimate.get("customer_id") or "")}
    for key in REVISION_PUT_HEADER_KEYS:
        if key == "customer_id":
            continue
        if key in header:
            payload[key] = header[key]["value"]
            continue
        value = estimate.get(key)
        if isinstance(value, str) and value.strip():
            payload[key] = value
    payload["discount_type"] = str(estimate.get("discount_type") or "")
    payload["is_discount_before_tax"] = bool(estimate.get("is_discount_before_tax"))
    shipping = live_decimal(estimate.get("shipping_charge", 0), "live shipping_charge")
    if shipping != 0:
        payload["shipping_charge"] = number_json(shipping)
    adjustment = live_decimal(estimate.get("adjustment", 0), "live adjustment")
    if adjustment != 0:
        payload["adjustment"] = number_json(adjustment)
        description = estimate.get("adjustment_description")
        if isinstance(description, str) and description.strip():
            payload["adjustment_description"] = description
    for key in ("template_id", "salesperson_id", "currency_id"):
        value = estimate.get(key)
        if isinstance(value, str) and value.strip():
            payload[key] = value
    if "exchange_rate" in estimate:
        payload["exchange_rate"] = number_json(
            live_decimal(estimate.get("exchange_rate"), "live exchange_rate")
        )
    lines = estimate.get("line_items") or []
    put_lines = []
    for index, (line, row) in enumerate(zip(lines, projection)):
        if str(line.get("line_item_id") or "") != row["line_item_id"]:
            raise DraftToolError("The projected line order does not match the live line order.")
        put_line: dict[str, Any] = {}
        for key in REVISION_LINE_PUT_KEYS:
            if key in ("quantity", "rate", "discount", "description", "tax_id"):
                continue
            value = line.get(key)
            if value is None or value == "":
                continue
            put_line[key] = value
        put_line["line_item_id"] = row["line_item_id"]
        put_line["item_id"] = row["item_id"]
        put_line["quantity"] = number_json(Decimal(row["quantity"]))
        put_line["rate"] = number_json(Decimal(row["rate"]))
        if row["discount"] is not None:
            put_line["discount"] = row["discount"]
        if row["description"]:
            put_line["description"] = row["description"]
        if row["tax_id"]:
            put_line["tax_id"] = row["tax_id"]
        missing = sorted(REVISION_REQUIRED_LINE_PUT_KEYS - set(put_line))
        if missing:
            raise DraftToolError(
                f"Live line {index + 1} cannot be resent intact; missing {', '.join(missing)}."
            )
        put_lines.append(put_line)
    if len(put_lines) != len(lines):
        raise DraftToolError("The PUT body does not resend every live line exactly once.")
    payload["line_items"] = put_lines
    extra = sorted(set(payload) - REVISION_ALLOWED_PUT_KEYS)
    if extra or not REVISION_REQUIRED_PUT_KEYS.issubset(payload):
        raise DraftToolError("The revision payload is not the exact commissioned shape.")
    return payload


def revision_protected_state(
    estimate: dict[str, Any], intent: dict[str, Any], tax_changed: bool
) -> dict[str, Any]:
    """Everything Zoho returns EXCEPT what this exact revision legitimately moves.

    Each exemption is narrow -- a named header field the plan changes, a named
    field of the one line that changes it, or a figure Zoho recomputes -- and
    every exempted value is asserted explicitly by verify_revision_result.
    """
    result = {
        key: value for key, value in estimate.items()
        if key not in REVISION_DERIVED_KEYS and key not in ESTIMATE_PREWRITE_VOLATILE_KEYS
    }
    for field in intent["header"]:
        result.pop(field, None)
        for companion in REVISION_HEADER_FIELD_COMPANIONS[field]:
            result.pop(companion, None)
    if tax_changed:
        # A tax change rewrites the header tax rows by construction, so they are
        # asserted explicitly instead of being frozen.
        result["taxes_protected_skipped_due_to_tax_change"] = True
    else:
        result["taxes_protected"] = [
            {key: value for key, value in tax.items() if key not in ESTIMATE_TAX_AMOUNT_KEYS}
            for tax in (estimate.get("taxes") or [])
            if isinstance(tax, dict)
        ]
    changes = revision_line_intent(intent)
    result["line_items_protected"] = []
    for line in (estimate.get("line_items") or []):
        if not isinstance(line, dict):
            continue
        protected_line = {
            key: value for key, value in line.items() if key not in ESTIMATE_LINE_DERIVED_KEYS
        }
        fields = changes.get(str(line.get("line_item_id") or ""), {})
        for field in fields:
            protected_line.pop(field, None)
            for companion in REVISION_LINE_FIELD_COMPANIONS[field]:
                protected_line.pop(companion, None)
        if "tax_id" in fields:
            protected_line.pop("line_item_taxes_protected", None)
        else:
            protected_line["line_item_taxes_protected"] = [
                {key: value for key, value in tax.items() if key not in ESTIMATE_TAX_AMOUNT_KEYS}
                for tax in (line.get("line_item_taxes") or [])
                if isinstance(tax, dict)
            ]
        result["line_items_protected"].append(protected_line)
    return result


def build_revision(
    before: dict[str, Any], intent: dict[str, Any], taxes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """The whole projection, derived from immutable inputs alone.

    Commit re-runs this over the staged live state, the staged intent and the
    staged tax rows and refuses unless the result is byte-identical to the
    reviewed plan, so no figure, endpoint, payload key or fingerprint in a plan
    file can be tampered with on its own.
    """
    lines = validate_revision_live_estimate(before, intent)
    discount_type = str(before.get("discount_type") or "")
    projection = revision_line_projection(lines, intent, discount_type)
    current = revision_line_projection(lines, {"lines": [], "header": {}}, discount_type)
    tax_changed = any("tax_id" in row["changed_fields"] for row in projection)
    expected = revision_totals(projection, taxes)
    before_totals = revision_totals(current, taxes)
    changed_lines = [row for row in projection if row["changed_fields"]]
    if len(changed_lines) != len(intent["lines"]):
        raise DraftToolError("Every named line change must land on exactly one live line.")
    protected = revision_protected_state(before, intent, tax_changed)
    shipping = live_decimal(before.get("shipping_charge", 0), "live shipping_charge")
    adjustment = live_decimal(before.get("adjustment", 0), "live adjustment")
    return {
        "estimate": {
            "estimate_id": intent["estimate_id"],
            "estimate_number": str(before.get("estimate_number") or ""),
            "customer_id": str(before.get("customer_id") or ""),
            "customer_name": str(before.get("customer_name") or ""),
            "currency_code": str(before.get("currency_code") or ""),
            "currency_id": str(before.get("currency_id") or ""),
            "exchange_rate": format(
                live_decimal(before.get("exchange_rate", 1), "live exchange_rate"), "f"
            ),
            "status": str(before.get("status") or ""),
            "line_count": len(lines),
            "discount_type": discount_type,
            "shipping_charge": money_text(shipping),
            "adjustment": money_text(adjustment),
            "before_state": json_copy(before),
            "before_state_sha256": digest_for(before),
            "protected_state": protected,
            "protected_state_sha256": digest_for(protected),
        },
        "reason": intent["reason"],
        "header_changes": [
            {
                "field": field,
                "from": str(before.get(field) or ""),
                "to": intent["header"][field]["value"],
                "source": intent["header"][field]["source"],
            }
            for field in REVISION_HEADER_FIELDS if field in intent["header"]
        ],
        "line_changes": [
            {
                "line_item_id": row["line_item_id"],
                "item_id": row["item_id"],
                "name": row["name"],
                "fields": [
                    {
                        "field": field,
                        "from": row[f"{field}_before"],
                        "to": row[field],
                        "source": revision_line_intent(intent)[row["line_item_id"]][field]["source"],
                    }
                    for field in REVISION_LINE_FIELDS if field in row["changed_fields"]
                ],
            }
            for row in changed_lines
        ],
        "tax_rows_used": {tax_id: json_copy(taxes[tax_id]) for tax_id in sorted(taxes)},
        "tax_changed": tax_changed,
        "current": before_totals,
        "expected": dict(expected, lines=projection),
        # The subtotal identity is only asserted where it is deterministic. A
        # shipping charge or adjustment brings its own tax and rounding rules,
        # so the identity is disclosed as unasserted rather than asserted wrongly.
        "total_identity_asserted": shipping == 0 and adjustment == 0,
        "put_endpoint": f"PUT /books/v3/estimates/{intent['estimate_id']}",
        "put_payload": revision_put_payload(before, projection, intent),
        "status_unchanged": str(before.get("status") or ""),
        "email_sent": False,
    }


def revision_stable_projection(evidence: dict[str, Any]) -> dict[str, Any]:
    """Canonical reviewed projection with per-GET telemetry normalized out.

    Full raw GET evidence remains immutable in the saved plan. For the fresh
    pre-lock comparison, the before-state copy and its digest are replaced by
    the same stable business state already used by the explicit drift gate.
    This narrowly prevents a regenerated ``estimate_url`` from making two
    otherwise equal projections differ, while every business field, projected
    value, total, PUT key and protected fingerprint remains byte-exact.
    """
    stable = json_copy(evidence)
    estimate = stable.get("estimate")
    if not isinstance(estimate, dict) or not isinstance(estimate.get("before_state"), dict):
        raise DraftToolError("Revision evidence has no stable before-state projection.")
    before = correction_prewrite_state(estimate["before_state"])
    estimate["before_state"] = before
    estimate["before_state_sha256"] = digest_for(before)
    return stable


def revision_approval_card(
    evidence: dict[str, Any], expires_utc: str,
    plan_sha256: str = APPROVAL_CARD_HASH_PLACEHOLDER,
) -> dict[str, Any]:
    estimate = evidence["estimate"]
    expected = evidence["expected"]
    tax_names = {
        str(row.get("tax_id") or ""): str(row.get("tax_name") or "")
        for row in (expected.get("tax_rows") or []) if isinstance(row, dict)
    }
    change_details = {
        str(change.get("line_item_id") or ""): {
            str(field.get("field") or ""): {
                "from": field.get("from"), "to": field.get("to")
            }
            for field in (change.get("fields") or []) if isinstance(field, dict)
        }
        for change in (evidence.get("line_changes") or []) if isinstance(change, dict)
    }
    lines = [
        {
            "item": row["name"],
            "quantity": row["quantity"],
            "rate": row["rate"],
            "discount": row["discount"],
            "tax": tax_names.get(row["tax_id"]) or row["tax_id"] or "none",
            "net": row["item_total"],
            "changes": row["changed_fields"],
            "change_details": change_details.get(row["line_item_id"], {}),
        }
        for row in expected["lines"]
    ]
    risks = [
        "One atomic PUT to this existing estimate; no retry after the write starts.",
        "All live lines are resent in order with line_item_id and item_id preserved.",
        "Customer, number, currency and status are preserved; no email or lifecycle route exists.",
    ]
    if expected["tax_certainty"] != "exact":
        risks.append(
            "Tax projection is not exact: " + "; ".join(expected["tax_uncertainty_reasons"])
        )
    return {
        "version": APPROVAL_CARD_VERSION,
        "card_id": approval_card_id(REVISION_OPERATION, plan_sha256),
        "operation": REVISION_OPERATION,
        "scope": {
            "method": "PUT",
            "route": f"/books/v3/estimates/{estimate['estimate_id']}",
            "write_count": 1,
            "existing_document_only": True,
            "email_or_lifecycle_action": False,
        },
        "customer": {"id": estimate["customer_id"], "name": estimate["customer_name"]},
        "document": {
            "type": "quote/estimate",
            "id": estimate["estimate_id"],
            "number": estimate["estimate_number"],
            "reference_number": str(
                (estimate.get("before_state") or {}).get("reference_number") or ""
            ),
            "status": estimate["status"],
        },
        "currency": {
            "code": estimate["currency_code"],
            "currency_id": estimate["currency_id"],
            "exchange_rate": estimate["exchange_rate"],
        },
        "lines": lines,
        "totals": {
            "subtotal": expected["sub_total"],
            "tax": expected["tax_total"],
            "total": expected["total"],
            "tax_certainty": expected["tax_certainty"],
        },
        "risks": risks,
        "expires_utc": expires_utc,
        "plan_sha256": plan_sha256,
        "approval": "Reply with exact one-word APPROVED to this displayed plan only.",
    }


def stage_revision_plan(
    intent: dict[str, Any], evidence: dict[str, Any], organization_id: str, input_path: Path
) -> Path:
    created = utc_now()
    core = {
        "tool": TOOL_NAME,
        "kind": REVISION_KIND,
        "schema_version": REVISION_SCHEMA_VERSION,
        "nonce": secrets.token_hex(16),
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=REVISION_PLAN_LIFETIME_HOURS)).isoformat(),
        "approval_required": APPROVAL_WORD,
        "estimate_id": intent["estimate_id"],
        "books_organization_id": organization_id,
        "risk": {
            "atomic": True,
            "single_put": True,
            "email_sent": False,
            "status_unchanged": evidence["estimate"]["status"],
            "write_attempted": False,
            "note": REVISION_RISK_NOTE,
        },
        "input": {"path": str(input_path), "sha256": file_digest(input_path)},
        "intent": intent,
        "live_evidence": evidence,
        "approval_card": revision_approval_card(
            evidence, (created + timedelta(hours=REVISION_PLAN_LIFETIME_HOURS)).isoformat()
        ),
    }
    plan = json_copy(core)
    plan["sha256"] = approval_plan_digest(core)
    plan["approval_card"]["plan_sha256"] = plan["sha256"]
    plan["approval_card"]["card_id"] = approval_card_id(REVISION_OPERATION, plan["sha256"])
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{REVISION_KIND}_{plan['sha256'][:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    activate_approval_card(plan["approval_card"])
    zoho_tool.append_receipt(
        "zoho_estimate_revision_plan_staged_read_only",
        f"estimate={evidence['estimate']['estimate_number']} ({intent['estimate_id']}); "
        f"plan={path}; sha256={plan['sha256']}; writes=0; method=GET_ONLY",
    )
    return path


def print_revision_summary(plan: dict[str, Any], path: Path) -> None:
    """Compatibility name; the permanent stage presentation is one concise card."""
    print_approval_card(plan["approval_card"])


def command_stage_estimate_revision(args: argparse.Namespace) -> None:
    """GET-only. Reads the input, the live estimate and the active tax list."""
    input_path = Path(str(args.input))
    intent = revision_intent(read_json(str(input_path)))
    vault = zoho_tool.load_vault()
    zoho_tool.validate_scopes([str(scope) for scope in vault.get("scopes") or []])
    organization_id = books_organization_id(vault)
    access_token, vault = zoho_tool.refresh_access_token(vault)
    before = get_estimate(access_token, vault, intent["estimate_id"])
    lines = validate_revision_live_estimate(before, intent)
    live_taxes = get_active_taxes(access_token, vault)
    zoho_tool.save_vault(vault)
    projection = revision_line_projection(
        lines, intent, str(before.get("discount_type") or "")
    )
    taxes = revision_tax_evidence(
        live_taxes,
        [row["tax_id"] for row in projection if row["tax_id"]]
        + [row["tax_id_before"] for row in projection if row["tax_id_before"]],
    )
    evidence = build_revision(before, intent, taxes)
    path = stage_revision_plan(intent, evidence, organization_id, input_path)
    plan = read_json(str(path))
    print_revision_summary(plan, path)


def validate_revision_plan(plan: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if (
        plan.get("tool") != TOOL_NAME
        or plan.get("kind") != REVISION_KIND
        or plan.get("schema_version") != REVISION_SCHEMA_VERSION
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise DraftToolError("The plan belongs to a different tool, action or schema version.")
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise DraftToolError("Plan nonce is invalid.")
    created = parse_plan_time(plan.get("created_utc"), "creation time")
    expires = parse_plan_time(plan.get("expires_utc"), "expiry")
    if expires - created != timedelta(hours=REVISION_PLAN_LIFETIME_HOURS):
        raise DraftToolError("Plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise DraftToolError("Plan creation time is in the future.")
    if now >= expires:
        raise DraftToolError("Plan expired. Stage a new plan for review.")
    evidence = plan.get("live_evidence")
    if not isinstance(evidence, dict):
        raise DraftToolError("Plan evidence is invalid.")
    staged_estimate = evidence.get("estimate")
    if not isinstance(staged_estimate, dict):
        raise DraftToolError("Plan estimate evidence is invalid.")
    risk = plan.get("risk")
    if not isinstance(risk, dict) or (
        risk.get("atomic") is not True
        or risk.get("single_put") is not True
        or risk.get("email_sent") is not False
        or risk.get("write_attempted") is not False
        or risk.get("status_unchanged") != staged_estimate.get("status")
        or risk.get("note") != REVISION_RISK_NOTE
    ):
        raise DraftToolError("Plan must disclose the exact single-atomic-PUT risk.")
    # Re-normalizing through the same closed schema means a hand-edited intent
    # cannot smuggle in an uneditable field, a bare numeric discount or a second
    # entry for one line.
    intent = revision_intent(plan.get("intent"))
    if intent != plan.get("intent"):
        raise DraftToolError("Plan intent is not the canonical normalized form of its own input.")
    estimate_id = intent["estimate_id"]
    if str(plan.get("estimate_id") or "") != estimate_id:
        raise DraftToolError("Plan header and intent name different estimates.")
    if not ID_RE.fullmatch(str(plan.get("books_organization_id") or "")):
        raise DraftToolError("Plan organization ID is invalid.")
    before = staged_estimate.get("before_state")
    if not isinstance(before, dict):
        raise DraftToolError("Plan before-state evidence must be an object.")
    if not secrets.compare_digest(
        str(staged_estimate.get("before_state_sha256") or ""), digest_for(before)
    ):
        raise DraftToolError("Plan before-state evidence hash is invalid.")
    taxes = evidence.get("tax_rows_used")
    if not isinstance(taxes, dict):
        raise DraftToolError("Plan tax evidence is invalid.")
    # Re-derive EVERYTHING from the staged live state, intent and tax rows. A
    # tampered payload, endpoint, fingerprint or total cannot survive this.
    if build_revision(before, intent, taxes) != evidence:
        raise DraftToolError(
            "Plan evidence is not the canonical projection of the staged live estimate state."
        )
    if evidence["put_endpoint"] != f"PUT /books/v3/estimates/{estimate_id}":
        raise DraftToolError("Plan endpoint is not the one commissioned estimate route.")
    expected_card = revision_approval_card(
        evidence, str(plan["expires_utc"]), str(plan.get("sha256") or "")
    )
    if plan.get("approval_card") != expected_card:
        raise DraftToolError(
            "Approval card does not exactly match the immutable estimate revision operation."
        )
    return estimate_id, intent, evidence


def load_revision_plan(path: Path) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    plan = read_json(str(path))
    saved = str(plan.get("sha256") or "")
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(
        saved, approval_plan_digest(plan)
    ):
        raise DraftToolError("Plan hash check failed. The plan changed after review.")
    estimate_id, intent, evidence = validate_revision_plan(plan)
    return plan, estimate_id, intent, evidence


def require_revision_put_allowed(
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
    expected_payload: dict[str, Any],
    evidence: dict[str, Any],
) -> None:
    """The complete write allowlist for a general estimate revision.

    Only PUT. Only the estimate this reviewed plan names. Only the commissioned
    keys, with the complete line list, every line_item_id and item_id present in
    live order, and every value identical to the reviewed plan. Pure validation
    -- it touches nothing. Commit runs it once BEFORE the replay lock (so a bad
    payload is a free refusal, not a burned plan) and the write function runs it
    again as the transport's own gate.
    """
    if method != "PUT":
        raise DraftToolError("REFUSED: an estimate revision is a PUT and nothing else.")
    estimate = evidence["estimate"]
    match = ESTIMATE_PUT_PATH_RE.fullmatch(str(path))
    if not match or match.group(1) != estimate["estimate_id"]:
        raise DraftToolError(
            "REFUSED: an estimate revision targets /books/v3/estimates/<the reviewed estimate id> "
            "and nothing else. Creation, deletion, sending, mailing, acceptance, decline, "
            "conversion, attachment, template, lifecycle and every bulk route are unreachable."
        )
    if not isinstance(payload, dict) or not isinstance(expected_payload, dict):
        raise DraftToolError("REFUSED: the estimate PUT payload must be an object.")
    extra = sorted(set(payload) - REVISION_ALLOWED_PUT_KEYS)
    if extra:
        raise DraftToolError(
            "REFUSED: the estimate PUT payload names uncommissioned field(s): " + ", ".join(extra)
        )
    if not REVISION_REQUIRED_PUT_KEYS.issubset(payload):
        raise DraftToolError(
            "REFUSED: the estimate PUT payload must carry the preserved customer, number and date, "
            "the live discount flags and the complete line list."
        )
    if str(payload.get("customer_id") or "") != estimate["customer_id"]:
        raise DraftToolError("REFUSED: the estimate PUT names a different customer.")
    if str(payload.get("estimate_number") or "") != estimate["estimate_number"]:
        raise DraftToolError("REFUSED: the estimate PUT does not preserve the estimate number.")
    if payload.get("discount_type") != estimate["discount_type"]:
        raise DraftToolError("REFUSED: the estimate PUT does not preserve the live discount type.")
    lines = payload.get("line_items")
    if not isinstance(lines, list) or len(lines) != estimate["line_count"]:
        raise DraftToolError("REFUSED: the estimate PUT must carry the complete live line list.")
    expected_lines = expected_payload.get("line_items")
    if not isinstance(expected_lines, list) or len(expected_lines) != estimate["line_count"]:
        raise DraftToolError("REFUSED: the reviewed plan payload is not the commissioned shape.")
    reviewed_rows = evidence["expected"]["lines"]
    if len(reviewed_rows) != estimate["line_count"]:
        raise DraftToolError("REFUSED: the reviewed plan does not project every live line.")
    seen: set[str] = set()
    for index, (line, reviewed, row) in enumerate(zip(lines, expected_lines, reviewed_rows)):
        if not isinstance(line, dict) or not isinstance(reviewed, dict):
            raise DraftToolError("REFUSED: every PUT line must be an object.")
        unknown = sorted(set(line) - set(REVISION_LINE_PUT_KEYS))
        if unknown:
            raise DraftToolError(
                "REFUSED: a PUT line names uncommissioned field(s): " + ", ".join(unknown)
            )
        if not REVISION_REQUIRED_LINE_PUT_KEYS.issubset(line):
            raise DraftToolError("REFUSED: every PUT line must resend its own identity.")
        line_item_id = str(line.get("line_item_id") or "")
        if not ID_RE.fullmatch(line_item_id) or line_item_id in seen:
            raise DraftToolError("REFUSED: the estimate PUT repeats or omits a line_item_id.")
        seen.add(line_item_id)
        # Order and identity are pinned line by line against the reviewed plan,
        # so a line cannot be reordered, substituted or silently swapped.
        if line_item_id != str(reviewed.get("line_item_id") or "") or line_item_id != row["line_item_id"]:
            raise DraftToolError(
                f"REFUSED: PUT line {index + 1} is not the reviewed line in the reviewed order."
            )
        if str(line.get("item_id") or "") != row["item_id"] or not ID_RE.fullmatch(row["item_id"]):
            raise DraftToolError(
                f"REFUSED: PUT line {index + 1} does not resend the reviewed Zoho item_id."
            )
        if isinstance(line.get("discount"), (int, float)) and not isinstance(
            line.get("discount"), bool
        ) and Decimal(str(line.get("discount"))) != 0:
            raise DraftToolError(
                f"REFUSED: PUT line {index + 1} carries a bare numeric discount, which Zoho reads "
                "as a flat CAD amount."
            )
        if set(line) != set(reviewed):
            raise DraftToolError(
                f"REFUSED: PUT line {index + 1} does not carry the reviewed field set."
            )
        for key in line:
            if line[key] == reviewed[key]:
                continue
            raise DraftToolError(
                f"REFUSED: PUT line {index + 1} {key} does not match the reviewed plan."
            )
    for key in sorted(REVISION_ALLOWED_PUT_KEYS):
        if key == "line_items":
            continue
        if payload.get(key) != expected_payload.get(key):
            raise DraftToolError(
                f"REFUSED: the estimate PUT {key} does not match the reviewed plan."
            )
    if set(payload) != set(expected_payload):
        raise DraftToolError("REFUSED: the estimate PUT is not the reviewed payload.")
    if not ID_RE.fullmatch(str(organization_id)):
        raise DraftToolError("REFUSED: the organization ID is invalid.")


def oauth_estimate_revision_write_allowed(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
    expected_payload: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """The ONE write path for a general estimate revision, gated by its allowlist."""
    require_revision_put_allowed(
        method, path, organization_id, payload, expected_payload, evidence
    )
    return send_estimate_put(
        access_token, api_domain, path, organization_id, payload, "estimate revision"
    )


def verify_revision_result(
    after: Any, evidence: dict[str, Any], label: str, *, full: bool = True
) -> None:
    """Post-state verification, applied to the PUT response AND a fresh GET.

    `full` adds the byte-exact protected fingerprint and the preserved per-line
    identity comparison. Those run against the FRESH GET, which is the
    authoritative record.
    """
    if not isinstance(after, dict):
        raise DraftToolError(f"{label} returned no estimate record.")
    estimate = evidence["estimate"]
    expected = evidence["expected"]
    for key, want in (
        ("estimate_id", estimate["estimate_id"]),
        ("estimate_number", estimate["estimate_number"]),
        ("customer_id", estimate["customer_id"]),
        ("currency_code", estimate["currency_code"]),
        ("currency_id", estimate["currency_id"]),
        ("status", estimate["status"]),
        ("discount_type", estimate["discount_type"]),
    ):
        actual = str(after.get(key) if after.get(key) is not None else "")
        if actual != want:
            raise DraftToolError(
                f"{label} {key} is {actual!r}, not the preserved {want!r}. Stop and reconcile."
            )
    if str(after.get("status") or "") not in REVISION_ELIGIBLE_STATUSES:
        raise DraftToolError(f"{label} left the estimate in an ineligible state. Stop and reconcile.")
    for change in evidence["header_changes"]:
        actual = after.get(change["field"])
        if actual != change["to"]:
            raise DraftToolError(
                f"{label} {change['field']} is {actual!r}, not the approved {change['to']!r}. "
                "Stop and reconcile."
            )
    lines = after.get("line_items")
    if not isinstance(lines, list) or len(lines) != estimate["line_count"]:
        raise DraftToolError(
            f"{label} carries {len(lines) if isinstance(lines, list) else 'no'} lines, not the "
            f"preserved {estimate['line_count']}. Stop and reconcile."
        )
    before_lines = estimate["before_state"].get("line_items") or []
    for index, (line, want_line) in enumerate(zip(lines, expected["lines"])):
        if not isinstance(line, dict):
            raise DraftToolError(f"{label} line {index + 1} is not an object.")
        for key, want in (
            ("line_item_id", want_line["line_item_id"]),
            ("item_id", want_line["item_id"]),
        ):
            if str(line.get(key) or "") != want:
                raise DraftToolError(
                    f"{label} line {index + 1} {key} is {line.get(key)!r}, not the preserved "
                    f"{want!r}. Stop and reconcile."
                )
        for key in ("quantity", "rate"):
            actual_value = live_decimal(line.get(key), f"{label} line {index + 1} {key}")
            if actual_value != Decimal(want_line[key]):
                raise DraftToolError(
                    f"{label} line {index + 1} {key} is {actual_value}, not the approved "
                    f"{want_line[key]}. Stop and reconcile."
                )
        if want_line["tax_id"] and str(line.get("tax_id") or "") != want_line["tax_id"]:
            raise DraftToolError(
                f"{label} line {index + 1} tax_id is {line.get('tax_id')!r}, not the approved "
                f"{want_line['tax_id']!r}. Stop and reconcile."
            )
        if want_line["description"] and str(line.get("description") or "") != want_line["description"]:
            raise DraftToolError(
                f"{label} line {index + 1} description is not the approved text. Stop and reconcile."
            )
        for key, want in (
            ("discount_amount", want_line["discount_amount"]),
            ("item_total", want_line["item_total"]),
        ):
            actual_value = live_decimal(line.get(key), f"{label} line {index + 1} {key}")
            if actual_value != Decimal(want):
                raise DraftToolError(
                    f"{label} line {index + 1} {key} is {actual_value}, not the approved {want}. "
                    "Stop and reconcile."
                )
        if isinstance(want_line["discount"], str):
            percent = returned_discount_percent(
                line.get("discount"), f"{label} line {index + 1} discount"
            )
            if percent != Decimal(want_line["discount"][:-1]):
                raise DraftToolError(
                    f"{label} line {index + 1} discount is {line.get('discount')!r}, not the "
                    f"approved {want_line['discount']}. Stop and reconcile."
                )
        if full:
            before_line = before_lines[index] if index < len(before_lines) else {}
            for key in ("unit", "item_order"):
                if str(before_line.get(key) or "") != str(line.get(key) or ""):
                    raise DraftToolError(
                        f"{label} line {index + 1} {key} moved from {before_line.get(key)!r} to "
                        f"{line.get(key)!r}. Stop and reconcile."
                    )
    for key in ("sub_total", "discount_total"):
        actual_value = live_decimal(after.get(key), f"{label} {key}")
        if actual_value != Decimal(expected[key]):
            raise DraftToolError(
                f"{label} {key} is {actual_value}, not the approved {expected[key]}. Stop and reconcile."
            )
    tax_total = live_decimal(after.get("tax_total"), f"{label} tax_total")
    total = live_decimal(after.get("total"), f"{label} total")
    if expected["tax_total_asserted"]:
        if tax_total != Decimal(expected["tax_total"]):
            raise DraftToolError(
                f"{label} tax_total is {tax_total}, not the approved {expected['tax_total']}. "
                "Stop and reconcile."
            )
        if total != Decimal(expected["total"]):
            raise DraftToolError(
                f"{label} total is {total}, not the approved {expected['total']}. Stop and reconcile."
            )
    if evidence["total_identity_asserted"]:
        if total != Decimal(expected["sub_total"]) + tax_total:
            raise DraftToolError(
                f"{label} total {total} is not its own sub_total plus tax_total. Stop and reconcile."
            )
    tax_rows = after.get("taxes")
    if isinstance(tax_rows, list) and tax_rows:
        rows_total = Decimal("0")
        for row in tax_rows:
            if not isinstance(row, dict):
                raise DraftToolError(f"{label} returned an invalid tax row.")
            rows_total += live_decimal(row.get("tax_amount"), f"{label} tax row amount")
        if rows_total != tax_total:
            raise DraftToolError(
                f"{label} tax rows sum to {rows_total}, not its own tax_total {tax_total}. "
                "Stop and reconcile."
            )
    if not full:
        return
    protected = revision_protected_state(after, evidence_intent(evidence), evidence["tax_changed"])
    if protected != estimate["protected_state"] or not secrets.compare_digest(
        digest_for(protected), str(estimate["protected_state_sha256"])
    ):
        moved = sorted(
            key for key in set(protected) | set(estimate["protected_state"])
            if protected.get(key) != estimate["protected_state"].get(key)
        )
        raise DraftToolError(
            f"{label} changed protected field(s) that must not move: {', '.join(moved) or 'unknown'}. "
            "Stop and reconcile."
        )


def evidence_intent(evidence: dict[str, Any]) -> dict[str, Any]:
    """The exact change set the reviewed plan describes, rebuilt from its evidence."""
    return {
        "header": {
            change["field"]: {"value": change["to"], "source": change["source"]}
            for change in evidence["header_changes"]
        },
        "lines": [
            {
                "line_item_id": change["line_item_id"],
                "fields": {
                    field["field"]: {"value": field["to"], "source": field["source"]}
                    for field in change["fields"]
                },
            }
            for change in evidence["line_changes"]
        ],
    }


def command_commit_estimate_revision(args: argparse.Namespace) -> None:
    plan_path = contained_correction_plan(args.plan)
    plan, estimate_id, intent, evidence = load_revision_plan(plan_path)
    # Approval is checked before the lock, the vault, the token and the network.
    require_exact_approval(args.approval)
    number = evidence["estimate"]["estimate_number"]
    lock = correction_lock_path(plan["sha256"])
    if lock.exists():
        raise DraftToolError(
            "REFUSED: this plan has already entered commit and cannot be replayed. "
            "No Zoho call was made."
        )
    require_active_approval_card(plan)
    try:
        vault = zoho_tool.load_vault()
        scopes = [str(scope) for scope in vault.get("scopes") or []]
        zoho_tool.validate_scopes(scopes)
        if ESTIMATE_UPDATE_SCOPE not in scopes:
            raise DraftToolError(
                f"Saved Zoho connection lacks {ESTIMATE_UPDATE_SCOPE}. Run "
                "PREPARE_DADO_ZOHO_ACCESS.bat, create the grant in the Zoho API Console, then "
                "REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat. No PUT was issued."
            )
        organization_id = books_organization_id(vault)
        if organization_id != str(plan["books_organization_id"]):
            raise DraftToolError(
                "REFUSED: the live FRP Depot Books organization does not match the plan."
            )
        access_token, vault = zoho_tool.refresh_access_token(vault)
        current = get_estimate(access_token, vault, estimate_id)
        staged_before = evidence["estimate"]["before_state"]
        current_prewrite = correction_prewrite_state(current)
        staged_prewrite = correction_prewrite_state(staged_before)
        if current_prewrite != staged_prewrite or not secrets.compare_digest(
            digest_for(current_prewrite), digest_for(staged_prewrite)
        ):
            moved = sorted(
                key for key in set(current_prewrite) | set(staged_prewrite)
                if current_prewrite.get(key) != staged_prewrite.get(key)
            )
            raise DraftToolError(
                f"Estimate {number} changed after review ({', '.join(moved) or 'unknown'}). "
                "No PUT was issued and this plan is not locked; stage a new plan."
            )
        # The pinned tax rows are re-read live, so a tax deactivated or repriced
        # between review and approval stops this for free.
        live_taxes = get_active_taxes(access_token, vault)
        fresh_taxes = revision_tax_evidence(live_taxes, sorted(evidence["tax_rows_used"]))
        if fresh_taxes != evidence["tax_rows_used"]:
            raise DraftToolError(
                "The active Zoho tax rows this plan priced have changed since review. "
                "No PUT was issued and this plan is not locked; stage a new plan."
            )
        # Rebuild the whole projection from the FRESH live record.
        fresh_evidence = build_revision(current, intent, fresh_taxes)
        if revision_stable_projection(fresh_evidence) != revision_stable_projection(evidence):
            raise DraftToolError(
                f"The reviewed projection no longer matches what the live {number} would produce. "
                "No PUT was issued and this plan is not locked; stage a new plan."
            )
        fresh_payload = fresh_evidence["put_payload"]
        # The write allowlist runs here too, so a payload it would reject is a
        # free refusal rather than a permanently locked plan.
        require_revision_put_allowed(
            "PUT", f"/books/v3/estimates/{estimate_id}", organization_id,
            evidence["put_payload"], fresh_payload, evidence,
        )
    except Exception as exc:
        zoho_tool.append_receipt(
            "zoho_estimate_revision_refused_before_lock",
            f"estimate={number} ({estimate_id}); plan={plan_path}; sha256={plan['sha256']}; "
            f"write_attempted=false; locked=false; email_sent=false",
        )
        raise DraftToolError(
            f"The estimate revision was refused BEFORE any write and BEFORE the replay lock. "
            f"Estimate: {number} ({estimate_id}). No PUT was issued and no email was sent. "
            f"Reason: {exc}"
        ) from exc
    write_correction_lock(lock, {
        "plan_sha256": plan["sha256"],
        "kind": REVISION_KIND,
        "status": "in_flight",
        "estimate_id": estimate_id,
        "started_utc": utc_now().isoformat(),
    }, exclusive=True)
    write_attempted = False
    try:
        write_attempted = True
        result = oauth_estimate_revision_write_allowed(
            access_token,
            str(vault["api_domain"]),
            "PUT",
            f"/books/v3/estimates/{estimate_id}",
            organization_id,
            evidence["put_payload"],
            fresh_payload,
            evidence,
        )
        verify_revision_result(result.get("estimate"), evidence, "PUT response", full=False)
        verified = get_estimate(access_token, vault, estimate_id)
        verify_revision_result(verified, evidence, "Fresh read-back", full=True)
        zoho_tool.save_vault(vault)
    except Exception as exc:
        write_correction_lock(lock, {
            "plan_sha256": plan["sha256"],
            "kind": REVISION_KIND,
            "status": "indeterminate",
            "estimate_id": estimate_id,
            "write_attempted": write_attempted,
            "plan_locked_indeterminate": True,
            "updated_utc": utc_now().isoformat(),
            "reason": str(exc)[:2000],
            "no_retry": True,
        })
        zoho_tool.append_receipt(
            "zoho_estimate_revision_indeterminate_no_retry",
            f"estimate={number} ({estimate_id}); write_attempted={str(write_attempted).lower()}; "
            f"plan={plan_path}; sha256={plan['sha256']}; email_sent=false",
        )
        raise DraftToolError(
            f"The estimate revision is indeterminate and this plan is permanently locked against "
            f"retry. Estimate: {number} ({estimate_id}). A PUT was ISSUED -- the live estimate "
            f"state is unconfirmed. No email was sent; this tool has no mail transport. "
            f"Reason: {exc} Read the live estimate in Zoho and reconcile before staging anything new."
        ) from exc
    write_correction_lock(lock, {
        "plan_sha256": plan["sha256"],
        "kind": REVISION_KIND,
        "status": "committed_verified",
        "estimate_id": estimate_id,
        "updated_utc": utc_now().isoformat(),
        "no_retry": True,
    })
    mark_active_card_attempted(plan)
    zoho_tool.append_receipt(
        "zoho_estimate_revision_committed_verified",
        f"estimate={number} ({estimate_id}); plan={plan_path}; sha256={plan['sha256']}; "
        f"status={evidence['estimate']['status']}; total={evidence['expected']['total']}; "
        "email_sent=false",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "kind": REVISION_KIND,
        "estimate_id": estimate_id,
        "estimate_number": number,
        "estimate_status": evidence["estimate"]["status"],
        "plan": str(plan_path),
        "plan_sha256": plan["sha256"],
        "header_changes": evidence["header_changes"],
        "line_changes": evidence["line_changes"],
        "sub_total": evidence["expected"]["sub_total"],
        "tax_total": evidence["expected"]["tax_total"],
        "tax_certainty": evidence["expected"]["tax_certainty"],
        "total": evidence["expected"]["total"],
        "lines_preserved": evidence["estimate"]["line_count"],
        "email_sent": False,
        "atomic": True,
        "replay_locked": True,
    }, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage_customer = commands.add_parser("stage-customer")
    stage_customer.add_argument("--input", required=True)
    stage_customer.add_argument("--source", required=True)
    stage_customer.set_defaults(func=command_stage_customer)
    stage_quote = commands.add_parser("stage-quote")
    stage_quote.add_argument("--input", required=True)
    stage_quote.set_defaults(func=command_stage_quote)
    commit_customer = commands.add_parser("commit-customer")
    commit_customer.add_argument("--plan", required=True)
    commit_customer.add_argument("--approval", required=True)
    commit_customer.set_defaults(func=lambda args: command_commit(args, "customer"))
    commit_quote = commands.add_parser("commit-quote")
    commit_quote.add_argument("--plan", required=True)
    commit_quote.add_argument("--approval", required=True)
    commit_quote.set_defaults(func=lambda args: command_commit(args, "quote"))
    stage_correction = commands.add_parser("stage-tds-discount-correction")
    stage_correction.add_argument("--estimate-id", dest="estimate_id", required=True)
    stage_correction.set_defaults(func=command_stage_tds_discount_correction)
    commit_correction = commands.add_parser("commit-tds-discount-correction")
    commit_correction.add_argument("--plan", required=True)
    commit_correction.add_argument("--approval", required=True)
    commit_correction.set_defaults(func=command_commit_tds_discount_correction)
    # No --estimate-id and no business-value argument: the estimate, the line and
    # both quantities are fixed in code.
    stage_item9 = commands.add_parser("stage-tds-item9-quantity-correction")
    stage_item9.set_defaults(func=command_stage_item9_quantity_correction)
    commit_item9 = commands.add_parser("commit-tds-item9-quantity-correction")
    commit_item9.add_argument("--plan", required=True)
    commit_item9.add_argument("--approval", required=True)
    commit_item9.set_defaults(func=command_commit_item9_quantity_correction)
    # No --input, no --source and no business argument of any kind: the whole
    # SHM Marine Constructors JV record is fixed in code.
    stage_shm = commands.add_parser("stage-shm-inv000051-customer")
    stage_shm.set_defaults(func=command_stage_shm_inv000051_customer)
    commit_shm = commands.add_parser("commit-shm-inv000051-customer")
    commit_shm.add_argument("--plan", required=True)
    commit_shm.add_argument("--approval", required=True)
    commit_shm.set_defaults(func=command_commit_shm_inv000051_customer)
    # The one GENERAL action: revise an existing estimate in place. Its business
    # values come from a closed-schema input file, each with its own source.
    stage_revision = commands.add_parser("stage-estimate-revision")
    stage_revision.add_argument("--input", required=True)
    stage_revision.set_defaults(func=command_stage_estimate_revision)
    commit_revision = commands.add_parser("commit-estimate-revision")
    commit_revision.add_argument("--plan", required=True)
    commit_revision.add_argument("--approval", required=True)
    commit_revision.set_defaults(func=command_commit_estimate_revision)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (DraftToolError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
