#!/usr/bin/env python
"""FRP Depot Zoho Banking Reconciliation Tool.

Commissioned by Rachad Homsi on 2026-08-07. Staging is read-only and is not
approval. This named tool can issue exactly one of these approved Books writes
per immutable, single-use plan:

- POST /books/v3/banktransactions/uncategorized/{id}/match
- POST /books/v3/banktransactions/uncategorized/{id}/categorize
- POST /books/v3/banktransactions/{id}/unmatch
- POST /books/v3/banktransactions/{id}/uncategorize
- PUT /books/v3/banktransactions/{id} (existing transfer account links only)

There is no generic bank-transaction creation/update, delete, rule, exclusion,
restoration, or bank-account write path in this module. The sole PUT reconstructs
an existing ``transfer_fund`` from its immutable live state and can change only
its from/to account IDs after a separately staged exact approval.

2026-08-10: the existing ``categorize`` operation gained ONE fail-closed recovery
path, hard-pinned to the single imported line Rachad Homsi uncategorized himself
(``96274000001423074``). It is not a new capability -- categorizing an imported
line as an internal transfer was already commissioned -- it only replaces an
outgoing-counterpart proof that uncategorization made impossible with the
immutable replay-locked original-transfer plan plus fresh live checks. See
``AIRWALLEX_USD_RECOVERY``. Every other Airwallex line still requires its live
outgoing counterpart, and no write verb, scope or approval rule changed.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import zoho_tool

TOOL_NAME = "FRP Depot Zoho Banking Reconciliation Tool"
SCHEMA_VERSION = 1
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_banking_plans"
PLAN_LIFETIME_HOURS = 24
APPROVAL_WORD = "APPROVED"
READ_SCOPE = "ZohoBooks.banking.READ"
CREATE_SCOPE = "ZohoBooks.banking.CREATE"
UPDATE_SCOPE = "ZohoBooks.banking.UPDATE"
AIRWALLEX_AMOUNTS = {Decimal("78146.27"), Decimal("21642.71")}
AIRWALLEX_DATE_TOLERANCE_DAYS = 7
CDP_ENDPOINT = "http://127.0.0.1:9228"
BOOKS_UI_ORIGIN = "https://books.zohocloud.ca"
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
POSITIVE_ID_RE = re.compile(r"^[1-9][0-9]*$")
TRANSACTION_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
GENERIC_TRANSACTION_TYPES = {
    "deposit", "refund", "transfer_fund", "card_payment",
    "sales_without_invoices", "expense_refund", "owner_contribution",
    "interest_income", "other_income", "owner_drawings", "sales_return",
}
MATCH_TRANSACTION_TYPES = GENERIC_TRANSACTION_TYPES | {"invoice"}
ACTIONS = {"match", "categorize", "unmatch", "uncategorize", "update_transfer_accounts"}
POST_ACTIONS = {"match", "categorize", "unmatch", "uncategorize"}
SOURCE_MODES = {
    "match": "regular",
    "categorize": "regular",
    "unmatch": "regular",
    "uncategorize": "regular",
    "update_transfer_accounts": "regular",
}
CATEGORIZE_RESULT_CONTAINERS = (
    "banktransaction", "bank_transaction", "transaction", "categorized_transaction",
)
AFTER_SOURCE_MODES = {
    "match": "regular",
    "categorize": "regular",
    "unmatch": "regular",
    "uncategorize": "regular",
    "update_transfer_accounts": "regular",
}
POST_PATHS = {
    "match": "/books/v3/banktransactions/uncategorized/{id}/match",
    "categorize": "/books/v3/banktransactions/uncategorized/{id}/categorize",
    "unmatch": "/books/v3/banktransactions/{id}/unmatch",
    "uncategorize": "/books/v3/banktransactions/{id}/uncategorize",
}
MATCH_PAYLOAD_FIELDS = {"transactions_to_be_matched"}
MATCH_TARGET_FIELDS = {"transaction_id", "transaction_type"}
CATEGORIZE_PAYLOAD_FIELDS = {
    "from_account_id", "to_account_id", "transaction_type", "amount", "date",
    "reference_number", "exchange_rate", "description", "currency_id",
}
CATEGORIZE_REQUIRED_FIELDS = {
    "from_account_id", "to_account_id", "transaction_type", "amount", "date",
}
CATEGORIZE_INPUT_FIELDS = CATEGORIZE_PAYLOAD_FIELDS - {"amount", "date"}
UPDATE_TRANSFER_INPUT_PAYLOAD_FIELDS = {"new_from_account_id", "new_to_account_id"}
UPDATE_TRANSFER_PAYLOAD_FIELDS = {
    "from_account_id", "to_account_id", "transaction_type", "amount", "date",
    "exchange_rate", "reference_number", "description", "currency_id",
}
UPDATE_TRANSFER_REQUIRED_FIELDS = {
    "from_account_id", "to_account_id", "transaction_type", "amount", "date",
}
BASE_INPUT_FIELDS = {"source_transaction_id", "sources", "evidence"}
INPUT_FIELDS = {
    "match": BASE_INPUT_FIELDS | {"transactions_to_be_matched"},
    "categorize": BASE_INPUT_FIELDS | {"payload"},
    "unmatch": BASE_INPUT_FIELDS | {"target_transaction_ids"},
    "uncategorize": BASE_INPUT_FIELDS | {"target_account_ids"},
    "update_transfer_accounts": BASE_INPUT_FIELDS | {"payload"},
}
# The ONLY optional input key in this module. It is accepted for ``categorize``
# alone, and only ever satisfies the one hard-pinned Airwallex USD recovery below.
OPTIONAL_INPUT_FIELDS = {"categorize": {"recovery"}}
PLAN_FIELDS = {
    "schema_version", "tool", "action", "created_utc", "expires_utc", "nonce",
    "approval_required", "organization", "payload", "sources", "evidence",
    "source_snapshot", "target_snapshots", "human_summary", "sha256",
}
SNAPSHOT_FIELDS = {
    "record_type", "role", "requested_id", "get_mode", "current_state",
    "current_state_sha256", "before_state",
}
TRANSACTION_BEFORE_FIELDS = {
    "transaction_id", "account_id", "account_name", "date", "amount", "currency",
    "status", "payee", "reference", "transaction_type",
}
ACCOUNT_BEFORE_FIELDS = {"account_id", "name", "currency", "status"}
ORGANIZATION_FIELDS = {"organization_id", "name"}
SOURCES_FIELDS = {"instruction", "statement_line"}
EVIDENCE_INPUT_FIELDS = {"basis", "airwallex_outgoing_transaction_id"}
EVIDENCE_FIELDS = {
    "basis", "airwallex_guarded", "airwallex_outgoing_transaction_id",
    "airwallex_verification", "airwallex_recovery",
}
RECOVERY_INPUT_FIELDS = {
    "mode", "historical_plan", "historical_plan_sha256",
    "superseded_transfer_transaction_id",
}
RECOVERY_EVIDENCE_FIELDS = {
    "mode", "statement", "source_transaction_id", "superseded_transfer_transaction_id",
    "superseded_transfer_verified_absent", "historical_plan", "historical_plan_sha256",
    "historical_plan_lock_status", "live_outgoing_counterpart_required",
}
RECOVERY_SUMMARY_FIELDS = {
    "kind", "statement", "source_statement_line_id", "transaction_type",
    "from_account", "to_account", "amount", "currency", "currency_id", "date",
    "reference_number", "description", "superseded_transfer_transaction_id",
    "superseded_transfer_verified_absent", "historical_plan_sha256", "write",
    "emails_sent", "revenue_or_income_classification", "write_performed_yet",
}
RECOVERY_PAYLOAD_FIELDS = {
    "from_account_id", "to_account_id", "transaction_type", "amount", "date",
    "reference_number", "description", "currency_id",
}
AIRWALLEX_VERIFICATION_FIELDS = {
    "outgoing_transaction_id", "amount_compatible", "currency_compatible",
    "date_compatible", "date_difference_days", "outgoing_direction_verified",
}
UPDATE_EVIDENCE_INPUT_FIELDS = {"basis"}
UPDATE_EVIDENCE_FIELDS = {
    "basis", "airwallex_guarded", "existing_transfer_is_source_ledger_evidence",
    "airwallex_mapping",
}
AIRWALLEX_MAPPING_FIELDS = {
    "transaction_id", "amount", "current_from_account_id", "approved_from_account_id",
    "approved_to_account_id",
}
SUMMARY_FIELDS = {
    "action", "account", "date", "payee", "reference", "amount", "currency",
    "status", "target",
}
UPDATE_SUMMARY_FIELDS = {
    "action", "transaction_id", "transaction_type", "status", "amount", "date",
    "currency", "exchange_rate", "before", "after",
}
UPDATE_SUMMARY_SIDE_FIELDS = {"from_account", "to_account"}

AIRWALLEX_ACCOUNT_UPDATE_CONSTRAINTS: dict[str, dict[str, Any]] = {
    "96274000001533058": {
        "amount": Decimal("78146.27"),
        "current_from_account_id": "96274000000097003",
        "current_from_account_name": "FRPDepot Inc.",
        "current_from_currency": "CAD",
        "new_from_account_id": "96274000000149537",
        "new_from_account_name": "AWX_FRPDepot Inc._CAD",
        "new_from_currency": "CAD",
        "to_account_id": "96274000001409019",
        "to_account_name": "Chequing account (C)",
        "to_currency": "CAD",
    },
    "96274000001535012": {
        "amount": Decimal("21642.71"),
        "current_from_account_id": "96274000000097003",
        "current_from_account_name": "FRPDepot Inc.",
        "current_from_currency": "CAD",
        "new_from_account_id": "96274000000149257",
        "new_from_account_name": "AWX_FRPDepot Inc._USD",
        "new_from_currency": "USD",
        "to_account_id": "96274000001409012",
        "to_account_name": "USD Desjardins corporate build-up account",
        "to_currency": "USD",
    },
}


# --------------------------------------------------------------------------
# ONE hard-pinned recovery of ONE already-commissioned categorize action.
#
# Rachad Homsi uncategorized the USD Airwallex closure statement line himself on
# 2026-08-10 and asked Dado to redo the correction.  Uncategorizing it destroyed
# transfer 96274000001535012, so the ordinary Airwallex guard's demand for a LIVE
# outgoing counterpart transaction can no longer be satisfied by anything real --
# the Books ledger for 2026-07-20..2026-07-31 holds no 21642.71 row at all.
#
# The counterpart proof is therefore replaced, for this ONE statement line only,
# by the immutable replay-locked plan that recorded the very same imported line,
# amount, date, description and account mapping while the transfer still existed,
# PLUS a fresh live proof that the old transfer is really gone.  Nothing else is
# relaxed: every other Airwallex line still needs its live outgoing counterpart.
# --------------------------------------------------------------------------
AIRWALLEX_USD_RECOVERY_MODE = "airwallex_usd_closure_uncategorized_by_rachad"
AIRWALLEX_USD_RECOVERY_STATEMENT = (
    "RECOVERY of the user-uncategorized USD closure transfer: Rachad Homsi uncategorized "
    "the 2026-07-23 Airwallex USD 21,642.71 statement line himself on 2026-08-10, so this "
    "recategorizes it as the internal transfer AWX_FRPDepot Inc._USD -> USD Desjardins "
    "corporate build-up account. It is not revenue, not income and not a customer receipt."
)
AIRWALLEX_USD_RECOVERY: dict[str, Any] = {
    "source_transaction_id": "96274000001423074",
    "source_status": "uncategorized",
    "source_account_id": "96274000001409012",
    "amount": Decimal("21642.71"),
    "date": "2026-07-23",
    "currency_code": "USD",
    "currency_id": "96274000000000081",
    "description": "Funds transfer received /FRPDepot Inc. /",
    "reference_number": "Closing Balance From Airwallex Account",
    "transaction_type": "transfer_fund",
    "from_account_id": "96274000000149257",
    "from_account_name": "AWX_FRPDepot Inc._USD",
    "from_currency": "USD",
    "to_account_id": "96274000001409012",
    "to_account_name": "USD Desjardins corporate build-up account",
    "to_currency": "USD",
    "superseded_transfer_transaction_id": "96274000001535012",
    "historical_plan_name": "20260808T031444Z_update_transfer_accounts_973274b986060804.json",
    "historical_plan_sha256": (
        "973274b9860608042d421e929b21ce688dfd8bacc42b7ca635b201f63a41d908"
    ),
    "historical_plan_lock_status": "indeterminate",
}


class BankingToolError(RuntimeError):
    pass


class BankingRecordAbsent(BankingToolError):
    """The record exists in neither the Books API nor the live imported feed.

    A distinct class, not a message, so an absence proof can never be satisfied by
    a network failure, an expired session or a browser that is not running.
    """


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
        raise BankingToolError("Zoho returned evidence that is not JSON serializable.") from exc


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BankingToolError(f"{label} JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise BankingToolError(f"{label} JSON must contain exactly one object.")
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
    raise BankingToolError(f"{label} must use the exact closed schema ({'; '.join(details)}).")


def allowed_fields(value: dict[str, Any], allowed: set[str], label: str) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise BankingToolError(f"Unsupported {label} field(s): {', '.join(extra)}")


def clean_text(value: Any, label: str, maximum: int = 2000, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise BankingToolError(f"{label} must be text.")
    result = value.strip()
    if required and not result:
        raise BankingToolError(f"{label} cannot be blank.")
    if len(result) > maximum:
        raise BankingToolError(f"{label} exceeds the {maximum}-character safety limit.")
    if any(ord(character) < 32 for character in result):
        raise BankingToolError(f"{label} contains control characters.")
    return result


def positive_id(value: Any, label: str) -> str:
    if isinstance(value, bool):
        raise BankingToolError(f"{label} must be a positive Zoho ID.")
    text = str(value if value is not None else "").strip()
    if not POSITIVE_ID_RE.fullmatch(text):
        raise BankingToolError(f"{label} must be a positive Zoho ID.")
    return text


def transaction_type(value: Any, label: str) -> str:
    text = clean_text(value, label, 64).casefold()
    if not TRANSACTION_TYPE_RE.fullmatch(text) or text not in GENERIC_TRANSACTION_TYPES:
        raise BankingToolError(
            f"{label} must be one of the generic banking transaction types: "
            + ", ".join(sorted(GENERIC_TRANSACTION_TYPES))
        )
    return text


def match_transaction_type(value: Any, label: str) -> str:
    text = clean_text(value, label, 64).casefold()
    if not TRANSACTION_TYPE_RE.fullmatch(text) or text not in MATCH_TRANSACTION_TYPES:
        raise BankingToolError(
            f"{label} must be one of the allowlisted match transaction types: "
            + ", ".join(sorted(MATCH_TRANSACTION_TYPES))
        )
    return text


def decimal_value(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or value in (None, ""):
        raise BankingToolError(f"{label} must be a finite amount.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BankingToolError(f"{label} must be a finite amount.") from exc
    if not result.is_finite():
        raise BankingToolError(f"{label} must be a finite amount.")
    return result


def decimal_text(value: Any, label: str) -> str:
    result = decimal_value(value, label)
    text = format(result, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def date_text(value: Any, label: str) -> str:
    text = clean_text(str(value) if value is not None else "", label, 32)
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError as exc:
        raise BankingToolError(f"{label} must begin with an ISO YYYY-MM-DD date.") from exc
    return parsed.isoformat()


def first_value(value: dict[str, Any], names: tuple[str, ...], default: Any = "") -> Any:
    for name in names:
        if value.get(name) not in (None, ""):
            return value[name]
    return default


def normalized_status(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")


def require_source_status(action: str, status: str) -> None:
    normalized = normalized_status(status)
    allowed = {
        "match": {"uncategorized", "unmatched"},
        "categorize": {"uncategorized", "unmatched"},
        "unmatch": {"matched", "manually_matched"},
        "uncategorize": {"categorized", "manually_categorized"},
        "update_transfer_accounts": {"manually_added"},
    }[action]
    if normalized not in allowed:
        raise BankingToolError(
            f"Source status {status!r} is not valid for {action}; expected one of {sorted(allowed)}."
        )


def require_rachad_approval(approval: Any) -> None:
    if approval != APPROVAL_WORD:
        raise BankingToolError(
            "Rachad must answer this exact staged plan with the case-sensitive one-word "
            "approval APPROVED. Staging is not approval, and Dado cannot supply it."
        )


def parse_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise BankingToolError(f"Plan {label} is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BankingToolError(f"Plan {label} must include a timezone.")
    return parsed


def contained_plan(raw_path: Any) -> Path:
    candidate = Path(str(raw_path if raw_path is not None else ""))
    if not candidate.is_absolute():
        raise BankingToolError("Plan must be an absolute path inside the exact banking plan folder.")
    try:
        resolved = candidate.resolve(strict=True)
        root = PLAN_DIR.resolve(strict=True)
    except OSError as exc:
        raise BankingToolError("Plan does not resolve inside the exact banking plan folder.") from exc
    if root not in resolved.parents or not resolved.is_file() or resolved.suffix.casefold() != ".json":
        raise BankingToolError("Plan is outside the exact allowlisted banking plan folder.")
    return resolved


def source_fields(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise BankingToolError("sources must be an object.")
    closed_fields(value, SOURCES_FIELDS, "sources")
    return {
        "instruction": clean_text(value["instruction"], "sources.instruction", 5000),
        "statement_line": clean_text(value["statement_line"], "sources.statement_line", 5000),
    }


def input_evidence(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise BankingToolError("evidence must be an object.")
    allowed_fields(value, EVIDENCE_INPUT_FIELDS, "evidence")
    if "basis" not in value:
        raise BankingToolError("evidence.basis is required.")
    outgoing = value.get("airwallex_outgoing_transaction_id", "")
    return {
        "basis": clean_text(value["basis"], "evidence.basis", 5000),
        "airwallex_outgoing_transaction_id": (
            positive_id(outgoing, "evidence.airwallex_outgoing_transaction_id") if outgoing not in (None, "") else ""
        ),
    }


def update_input_evidence(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise BankingToolError("evidence must be an object.")
    closed_fields(value, UPDATE_EVIDENCE_INPUT_FIELDS, "update_transfer_accounts evidence")
    return {"basis": clean_text(value["basis"], "evidence.basis", 5000)}


def require_rachad_sent_item_source(sources: dict[str, str]) -> None:
    citation = " ".join(sources.values()).casefold()
    has_date = any(marker in citation for marker in (
        "2026-07-24", "2026/07/24", "july 24, 2026", "july 24 2026",
    ))
    if "rachad" not in citation or "sent item" not in citation or not has_date:
        raise BankingToolError(
            "update_transfer_accounts sources must cite Rachad's 2026-07-24 live Sent Item."
        )


def positive_id_list(value: Any, label: str, maximum: int = 20) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise BankingToolError(f"{label} must contain 1-{maximum} unique positive Zoho IDs.")
    ids = [positive_id(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(set(ids)) != len(ids):
        raise BankingToolError(f"{label} must contain unique IDs.")
    return ids


def transaction_before(current: dict[str, Any], requested_id: str) -> dict[str, str]:
    transaction_id = positive_id(
        first_value(current, ("transaction_id", "bank_transaction_id", "banktransaction_id")),
        "live transaction_id",
    )
    if transaction_id != requested_id:
        raise BankingToolError(f"Zoho returned transaction {transaction_id}, not requested ID {requested_id}.")
    account_id_raw = first_value(current, ("account_id", "bank_account_id", "from_account_id"))
    account_id = positive_id(account_id_raw, "live transaction account_id")
    currency = clean_text(
        str(first_value(current, ("currency_code", "currency_id", "currency"))),
        "live transaction currency",
        64,
    )
    status = clean_text(
        str(first_value(current, ("status", "transaction_status"))),
        "live transaction status",
        100,
    )
    return {
        "transaction_id": transaction_id,
        "account_id": account_id,
        "account_name": str(first_value(current, ("account_name", "bank_account_name"))).strip(),
        "date": date_text(first_value(current, ("date", "transaction_date")), "live transaction date"),
        "amount": decimal_text(first_value(current, ("amount", "transaction_amount")), "live transaction amount"),
        "currency": currency,
        "status": status,
        "payee": str(first_value(current, ("payee", "payee_name", "vendor_name", "customer_name"))).strip(),
        "reference": str(first_value(current, ("reference_number", "reference", "transaction_reference"))).strip(),
        "transaction_type": str(first_value(current, ("transaction_type", "type"))).strip().casefold(),
    }


def account_before(current: dict[str, Any], requested_id: str) -> dict[str, str]:
    account_id = positive_id(
        first_value(current, ("account_id", "chart_of_account_id", "bank_account_id")),
        "live account_id",
    )
    if account_id != requested_id:
        raise BankingToolError(f"Zoho returned account {account_id}, not requested ID {requested_id}.")
    status_raw = first_value(current, ("status", "account_status"), "")
    if status_raw in (None, "") and isinstance(current.get("is_active"), bool):
        status_raw = "active" if current["is_active"] else "inactive"
    if status_raw in (None, ""):
        status_raw = "active"
    return {
        "account_id": account_id,
        "name": clean_text(str(first_value(current, ("account_name", "name"))), "live account name", 500),
        "currency": str(first_value(current, ("currency_code", "currency_id", "currency"))).strip(),
        "status": str(status_raw).strip(),
    }


def transaction_snapshot(
    current: dict[str, Any], requested_id: str, role: str, get_mode: str = "regular"
) -> dict[str, Any]:
    state = json_copy(current)
    return {
        "record_type": "transaction",
        "role": role,
        "requested_id": requested_id,
        "get_mode": get_mode,
        "current_state": state,
        "current_state_sha256": digest_for(state),
        "before_state": transaction_before(state, requested_id),
    }


def account_snapshot(current: dict[str, Any], requested_id: str, role: str) -> dict[str, Any]:
    state = json_copy(current)
    return {
        "record_type": "account",
        "role": role,
        "requested_id": requested_id,
        "get_mode": "account",
        "current_state": state,
        "current_state_sha256": digest_for(state),
        "before_state": account_before(state, requested_id),
    }


def enrich_missing_transaction_status(
    access_token: str,
    vault: dict[str, Any],
    transaction_id: str,
    current: dict[str, Any],
) -> dict[str, Any]:
    """Join the detail record to its source-account list row when detail omits status."""
    if first_value(current, ("status", "transaction_status"), "") not in (None, ""):
        return json_copy(current)
    transaction_date = date_text(
        first_value(current, ("date", "transaction_date")), "live transaction date"
    )
    source_account_id = positive_id(
        first_value(current, ("from_account_id", "account_id", "bank_account_id")),
        "live transaction source account_id",
    )
    query = urlencode({
        "organization_id": positive_id(vault["books_organization_id"], "organization_id"),
        "date_start": transaction_date,
        "date_end": transaction_date,
        "filter_by": "Status.All",
        "page": 1,
        "per_page": 200,
    })
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/banktransactions?{query}",
    )
    rows = result.get("banktransactions")
    if not isinstance(rows, list):
        raise BankingToolError("Zoho did not return bank transaction list rows for status verification.")
    if zoho_tool.require_has_more_page(
        result, "/books/v3/banktransactions", 1, BankingToolError
    ):
        raise BankingToolError("Zoho returned more than 200 same-day bank rows; status verification is ambiguous.")
    statuses = {
        str(first_value(row, ("status", "transaction_status"))).strip()
        for row in rows
        if isinstance(row, dict)
        and str(first_value(row, ("transaction_id", "bank_transaction_id"))).strip() == transaction_id
        and str(first_value(row, ("account_id", "bank_account_id", "from_account_id"))).strip()
        == source_account_id
        and first_value(row, ("status", "transaction_status"), "") not in (None, "")
    }
    if len(statuses) != 1:
        raise BankingToolError(
            f"Could not uniquely verify live status for transaction {transaction_id} on its source account."
        )
    enriched = json_copy(current)
    enriched["status"] = statuses.pop()
    return enriched


def books_ui_get(path: str, organization_id: str) -> dict[str, Any]:
    """GET one of the two allowlisted Books imported-feed routes via the live UI session."""
    organization_id = positive_id(organization_id, "organization_id")
    parsed = urlparse(path)
    allowed_paths = {
        "/api/v3/banktransactions/uncategorized",
        "/api/v3/banktransactions/uncategorized/match",
    }
    if parsed.scheme or parsed.netloc or parsed.path not in allowed_paths:
        raise BankingToolError("REFUSED: Books UI GET path is outside the imported-feed allowlist.")
    organizations = parse_qs(parsed.query).get("organization_id", [])
    if organizations != [organization_id]:
        raise BankingToolError("REFUSED: Books UI GET is not locked to the saved FRP Depot organization.")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BankingToolError("Playwright is unavailable for the commissioned Books UI read path.") from exc
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=10_000)
            pages = [
                page for context in browser.contexts for page in context.pages
                if page.url.startswith(BOOKS_UI_ORIGIN + "/app")
            ]
            if len(pages) != 1:
                raise BankingToolError(
                    "Exactly one authenticated Canadian Zoho Books app page must be open for imported-feed reads."
                )
            result = pages[0].evaluate(
                """async path => {
                    const response = await fetch(path, {
                        method: 'GET', credentials: 'same-origin',
                        headers: {Accept: 'application/json'}
                    });
                    let body;
                    try { body = await response.json(); }
                    catch (_) { body = null; }
                    return {http_status: response.status, body};
                }""",
                path,
            )
            browser.close()
    except BankingToolError:
        raise
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        raise BankingToolError(f"Zoho Books imported-feed GET failed: {exc}") from exc
    if not isinstance(result, dict) or result.get("http_status") != 200:
        status = result.get("http_status") if isinstance(result, dict) else "invalid response"
        raise BankingToolError(f"Zoho Books imported-feed GET returned HTTP {status}.")
    body = result.get("body")
    if not isinstance(body, dict) or body.get("code") not in (None, 0):
        raise BankingToolError("Zoho Books imported-feed GET returned an invalid or unsuccessful body.")
    return json_copy(body)


def list_uncategorized_ui_transactions(vault: dict[str, Any]) -> list[dict[str, Any]]:
    organization_id = positive_id(vault["books_organization_id"], "organization_id")
    rows_out: list[dict[str, Any]] = []
    allowed = {
        "transaction_id", "date", "transaction_type", "status", "amount",
        "bank_charges", "gross_amount", "source", "account_id", "account_name",
        "account_type", "payee", "description", "currency_id", "currency_code",
        "currency_symbol", "price_precision", "debit_or_credit", "reference_number",
    }
    for page_number in range(1, 51):
        query = urlencode({
            "page": page_number,
            "per_page": 200,
            "response_option": 1,
            "organization_id": organization_id,
        })
        result = books_ui_get(f"/api/v3/banktransactions/uncategorized?{query}", organization_id)
        rows = result.get("transactions")
        if not isinstance(rows, list):
            raise BankingToolError("Zoho Books imported-feed GET omitted its transactions list.")
        for row in rows:
            if not isinstance(row, dict):
                raise BankingToolError("Zoho Books imported-feed GET returned a non-object transaction.")
            projected = {key: value for key, value in row.items() if key in allowed}
            transaction_id = positive_id(
                first_value(projected, ("transaction_id", "bank_transaction_id")),
                "live imported-feed transaction_id",
            )
            transaction_before(projected, transaction_id)
            rows_out.append(projected)
        if not zoho_tool.require_has_more_page(
            result,
            "/api/v3/banktransactions/uncategorized",
            page_number,
            BankingToolError,
        ):
            break
    else:
        raise BankingToolError("Zoho Books imported-feed GET exceeded the 50-page safety limit.")
    ids = [row["transaction_id"] for row in rows_out]
    if len(set(ids)) != len(ids):
        raise BankingToolError("Zoho Books imported feed returned duplicate transaction IDs.")
    return rows_out


def get_uncategorized_ui_transaction(
    vault: dict[str, Any], transaction_id: str
) -> dict[str, Any]:
    transaction_id = positive_id(transaction_id, "transaction_id")
    found = [
        row for row in list_uncategorized_ui_transactions(vault)
        if row["transaction_id"] == transaction_id
    ]
    if not found:
        # Absent from BOTH the Books API (404) and the live imported feed. Same
        # message and same BankingToolError base as before; the narrower class only
        # lets the recovery absence proof tell "really gone" from "read failed".
        raise BankingRecordAbsent(
            f"Zoho Books imported feed did not return exactly one transaction {transaction_id}."
        )
    if len(found) != 1:
        raise BankingToolError(
            f"Zoho Books imported feed did not return exactly one transaction {transaction_id}."
        )
    return found[0]


def get_bank_transaction(
    access_token: str, vault: dict[str, Any], transaction_id: str, mode: str
) -> dict[str, Any]:
    transaction_id = positive_id(transaction_id, "transaction_id")
    if mode != "regular":
        raise BankingToolError("Unsupported banking GET mode.")
    query = urlencode({"organization_id": positive_id(vault["books_organization_id"], "organization_id")})
    try:
        result = zoho_tool.api_get(
            access_token,
            str(vault["api_domain"]),
            f"/books/v3/banktransactions/{transaction_id}?{query}",
        )
    except zoho_tool.ZohoError as exc:
        cause = exc.__cause__
        if not isinstance(cause, HTTPError) or cause.code != 404:
            raise
        return get_uncategorized_ui_transaction(vault, transaction_id)
    for key in ("banktransaction", "bank_transaction", "transaction", "uncategorized_transaction"):
        row = result.get(key)
        if isinstance(row, dict):
            row = enrich_missing_transaction_status(access_token, vault, transaction_id, row)
            transaction_before(row, transaction_id)
            return json_copy(row)
    raise BankingToolError(f"Zoho did not return bank transaction {transaction_id}.")


def get_match_target(
    access_token: str,
    vault: dict[str, Any],
    source_transaction_id: str,
    target_transaction_id: str,
    target_transaction_type: str,
) -> dict[str, Any]:
    source_transaction_id = positive_id(source_transaction_id, "source_transaction_id")
    target_transaction_id = positive_id(target_transaction_id, "target_transaction_id")
    target_transaction_type = match_transaction_type(
        target_transaction_type, "target_transaction_type"
    )
    if target_transaction_type != "invoice":
        return get_bank_transaction(access_token, vault, target_transaction_id, "regular")

    organization_id = positive_id(vault["books_organization_id"], "organization_id")
    candidate_query = urlencode({
        "statement_ids": source_transaction_id,
        "organization_id": organization_id,
    })
    candidate_result = books_ui_get(
        f"/api/v3/banktransactions/uncategorized/match?{candidate_query}", organization_id
    )
    candidate_rows = candidate_result.get("matching_transactions")
    if not isinstance(candidate_rows, list):
        raise BankingToolError("Zoho Books match-candidate GET omitted matching_transactions.")
    candidates = [
        row for row in candidate_rows
        if isinstance(row, dict)
        and str(row.get("transaction_id") or "").strip() == target_transaction_id
        and str(row.get("transaction_type") or "").strip().casefold() == "invoice"
    ]
    if len(candidates) != 1:
        raise BankingToolError(
            f"Zoho Books did not return exactly one invoice candidate {target_transaction_id}."
        )
    candidate = candidates[0]
    if candidate.get("is_best_match") is not True:
        raise BankingToolError("The requested invoice is not Zoho's current best match for the statement line.")

    invoice_query = urlencode({"organization_id": organization_id})
    invoice_result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/invoices/{target_transaction_id}?{invoice_query}",
    )
    invoice = invoice_result.get("invoice")
    if not isinstance(invoice, dict):
        raise BankingToolError(f"Zoho did not return invoice {target_transaction_id}.")
    if positive_id(invoice.get("invoice_id"), "live invoice_id") != target_transaction_id:
        raise BankingToolError("Zoho invoice ID differs from the requested match target.")
    balance = decimal_value(invoice.get("balance"), "live invoice balance")
    if balance <= 0 or normalized_status(invoice.get("status")) in {"paid", "void", "draft"}:
        raise BankingToolError("The requested invoice is not open with a positive live balance.")
    if decimal_value(candidate.get("amount"), "candidate amount") != balance:
        raise BankingToolError("Zoho's candidate amount does not equal the live invoice balance.")
    invoice_number = clean_text(invoice.get("invoice_number"), "live invoice number", 100)
    if str(candidate.get("transaction_number") or "").strip() != invoice_number:
        raise BankingToolError("Zoho's candidate number does not equal the live invoice number.")
    customer_id = positive_id(invoice.get("customer_id"), "live invoice customer_id")
    customer_name = clean_text(invoice.get("customer_name"), "live invoice customer name", 500)
    if str(candidate.get("contact_name") or "").strip() != customer_name:
        raise BankingToolError("Zoho's candidate customer does not equal the live invoice customer.")
    projected = {
        "transaction_id": target_transaction_id,
        "account_id": customer_id,
        "account_name": customer_name,
        "date": date_text(invoice.get("date"), "live invoice date"),
        "amount": float(balance),
        "currency_code": clean_text(invoice.get("currency_code"), "live invoice currency", 64),
        "status": clean_text(invoice.get("status"), "live invoice status", 100),
        "payee": customer_name,
        "reference_number": str(invoice.get("reference_number") or "").strip(),
        "transaction_type": "invoice",
        "description": f"Invoice {invoice_number}",
        "transaction_number": invoice_number,
        "match_source_transaction_id": source_transaction_id,
        "candidate_is_best_match": True,
        "candidate_is_exact_match": candidate.get("is_exact_match") is True,
    }
    transaction_before(projected, target_transaction_id)
    return projected


def get_account(access_token: str, vault: dict[str, Any], account_id: str) -> dict[str, Any]:
    account_id = positive_id(account_id, "account_id")
    query = urlencode({"organization_id": positive_id(vault["books_organization_id"], "organization_id")})
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/chartofaccounts/{account_id}?{query}",
    )
    for key in ("chart_of_account", "chartofaccount", "account"):
        row = result.get(key)
        if isinstance(row, dict):
            if str(row.get("account_type") or "").strip().casefold() == "bank" and not str(
                first_value(row, ("currency_code", "currency_id", "currency"), "")
            ).strip():
                bank_query = urlencode({
                    "organization_id": positive_id(vault["books_organization_id"], "organization_id"),
                    "page": 1,
                    "per_page": 200,
                })
                bank_result = zoho_tool.api_get(
                    access_token,
                    str(vault["api_domain"]),
                    f"/books/v3/bankaccounts?{bank_query}",
                )
                bank_rows = bank_result.get("bankaccounts")
                if not isinstance(bank_rows, list):
                    raise BankingToolError("Zoho did not return bank-account rows for currency verification.")
                if zoho_tool.require_has_more_page(
                    bank_result, "/books/v3/bankaccounts", 1, BankingToolError
                ):
                    raise BankingToolError(
                        "Zoho returned more than 200 bank accounts; currency verification is ambiguous."
                    )
                matches = [
                    bank_row for bank_row in bank_rows
                    if isinstance(bank_row, dict)
                    and str(first_value(bank_row, ("account_id", "bank_account_id"))).strip() == account_id
                ]
                if len(matches) != 1:
                    raise BankingToolError(
                        f"Could not uniquely verify bank-account currency for account {account_id}."
                    )
                bank_row = matches[0]
                row = json_copy(row)
                row["currency_code"] = clean_text(
                    str(first_value(bank_row, ("currency_code", "currency_id", "currency"))),
                    "live bank account currency",
                    64,
                )
                if not str(first_value(row, ("status", "account_status"), "")).strip():
                    active = bank_row.get("is_active")
                    if isinstance(active, bool):
                        row["status"] = "active" if active else "inactive"
            account_before(row, account_id)
            return json_copy(row)
    raise BankingToolError(f"Zoho did not return chart-of-account record {account_id}.")


def get_frp_organization(access_token: str, vault: dict[str, Any]) -> dict[str, str]:
    organization_id = positive_id(vault.get("books_organization_id"), "books_organization_id")
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/organizations/{organization_id}",
    )
    row = result.get("organization")
    if not isinstance(row, dict):
        raise BankingToolError("Zoho did not return the locked Books organization.")
    live_id = positive_id(row.get("organization_id"), "live organization_id")
    name = clean_text(str(row.get("name") or row.get("organization_name") or ""), "organization name", 500)
    normalized = "".join(character for character in name.casefold() if character.isalnum())
    if live_id != organization_id or "frpdepot" not in normalized:
        raise BankingToolError("REFUSED: the live Books organization is not the locked FRP Depot organization.")
    saved_name = str(vault.get("books_organization_name") or "").strip()
    if saved_name:
        saved_normalized = "".join(character for character in saved_name.casefold() if character.isalnum())
        if "frpdepot" not in saved_normalized:
            raise BankingToolError("REFUSED: the saved Books organization name is not FRP Depot.")
    return {"organization_id": live_id, "name": name}


def collect_scalar_strings(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            result.update(collect_scalar_strings(item))
    elif isinstance(value, list):
        for item in value:
            result.update(collect_scalar_strings(item))
    elif value is not None and not isinstance(value, (dict, list)):
        result.add(str(value))
    return result


def airwallex_guarded(source: dict[str, Any], sources: dict[str, str]) -> bool:
    before = source["before_state"]
    amount_match = abs(decimal_value(before["amount"], "source amount")) in AIRWALLEX_AMOUNTS
    text = sources["statement_line"].casefold()
    return amount_match or "airwallex" in text


def outgoing_direction_verified(snapshot: dict[str, Any]) -> bool:
    before = snapshot["before_state"]
    if decimal_value(before["amount"], "outgoing amount") < 0:
        return True
    direction_text = " ".join((
        before.get("transaction_type", ""), before.get("status", ""),
        canonical(snapshot["current_state"]),
    )).casefold()
    return any(word in direction_text for word in ("outgoing", "withdraw", "debit", "transfer_fund"))


def airwallex_verification(source: dict[str, Any], outgoing: dict[str, Any]) -> dict[str, Any]:
    if source["requested_id"] == outgoing["requested_id"]:
        raise BankingToolError("Airwallex outgoing transaction ID must differ from the statement transaction ID.")
    source_before = source["before_state"]
    outgoing_before = outgoing["before_state"]
    source_date = date.fromisoformat(source_before["date"])
    outgoing_date = date.fromisoformat(outgoing_before["date"])
    difference = abs((source_date - outgoing_date).days)
    result = {
        "outgoing_transaction_id": outgoing["requested_id"],
        "amount_compatible": abs(decimal_value(source_before["amount"], "source amount")) == abs(
            decimal_value(outgoing_before["amount"], "outgoing amount")
        ),
        "currency_compatible": source_before["currency"].casefold() == outgoing_before["currency"].casefold(),
        "date_compatible": difference <= AIRWALLEX_DATE_TOLERANCE_DAYS,
        "date_difference_days": difference,
        "outgoing_direction_verified": outgoing_direction_verified(outgoing),
    }
    if not all((
        result["amount_compatible"], result["currency_compatible"],
        result["date_compatible"], result["outgoing_direction_verified"],
    )):
        raise BankingToolError(
            "Airwallex outgoing transaction failed amount/currency/date/direction compatibility checks."
        )
    return result


def validate_match_targets(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise BankingToolError("transactions_to_be_matched must contain 1-20 unique targets.")
    result: list[dict[str, str]] = []
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise BankingToolError(f"transactions_to_be_matched[{index}] must be an object.")
        closed_fields(row, MATCH_TARGET_FIELDS, f"transactions_to_be_matched[{index}]")
        result.append({
            "transaction_id": positive_id(row["transaction_id"], f"transactions_to_be_matched[{index}].transaction_id"),
            "transaction_type": match_transaction_type(
                row["transaction_type"], f"transactions_to_be_matched[{index}].transaction_type"
            ),
        })
    ids = [row["transaction_id"] for row in result]
    if len(set(ids)) != len(ids):
        raise BankingToolError("transactions_to_be_matched transaction IDs must be unique.")
    return result


def validate_categorize_input_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BankingToolError("categorize payload must be an object.")
    allowed_fields(value, CATEGORIZE_INPUT_FIELDS, "categorize payload")
    required = {"from_account_id", "to_account_id", "transaction_type"}
    missing = sorted(required - set(value))
    if missing:
        raise BankingToolError("categorize payload is missing: " + ", ".join(missing))
    from_id = positive_id(value["from_account_id"], "payload.from_account_id")
    to_id = positive_id(value["to_account_id"], "payload.to_account_id")
    if from_id == to_id:
        raise BankingToolError("from_account_id and to_account_id must differ.")
    result: dict[str, Any] = {
        "from_account_id": from_id,
        "to_account_id": to_id,
        "transaction_type": transaction_type(value["transaction_type"], "payload.transaction_type"),
    }
    for field in ("reference_number", "description", "currency_id"):
        if field in value:
            result[field] = clean_text(value[field], f"payload.{field}", 2000)
    if "exchange_rate" in value:
        rate = decimal_value(value["exchange_rate"], "payload.exchange_rate")
        if rate <= 0:
            raise BankingToolError("payload.exchange_rate must be greater than zero.")
        result["exchange_rate"] = float(rate)
    return result


def validate_update_transfer_input_payload(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise BankingToolError("update_transfer_accounts payload must be an object.")
    closed_fields(value, UPDATE_TRANSFER_INPUT_PAYLOAD_FIELDS, "update_transfer_accounts payload")
    from_id = positive_id(value["new_from_account_id"], "payload.new_from_account_id")
    to_id = positive_id(value["new_to_account_id"], "payload.new_to_account_id")
    if from_id == to_id:
        raise BankingToolError("new_from_account_id and new_to_account_id must differ.")
    return {"new_from_account_id": from_id, "new_to_account_id": to_id}


def live_transfer_details(current: dict[str, Any], requested_id: str) -> dict[str, str]:
    before = transaction_before(current, requested_id)
    from_id = positive_id(first_value(current, ("from_account_id",)), "live from_account_id")
    to_id = positive_id(first_value(current, ("to_account_id",)), "live to_account_id")
    transfer_type = str(first_value(current, ("transaction_type", "type"))).strip().casefold()
    if transfer_type != "transfer_fund":
        raise BankingToolError("update_transfer_accounts requires live transaction_type exactly transfer_fund.")
    exchange_raw = first_value(current, ("exchange_rate",), "")
    exchange_rate = ""
    if exchange_raw not in (None, ""):
        rate = decimal_value(exchange_raw, "live exchange_rate")
        if rate <= 0:
            raise BankingToolError("Live transfer exchange_rate must be greater than zero when present.")
        exchange_rate = decimal_text(rate, "live exchange_rate")
    return {
        "transaction_id": before["transaction_id"],
        "from_account_id": from_id,
        "to_account_id": to_id,
        "transaction_type": transfer_type,
        "amount": before["amount"],
        "date": before["date"],
        "currency": before["currency"],
        "exchange_rate": exchange_rate,
        "status": before["status"],
    }


def construct_update_transfer_payload(
    current: dict[str, Any], requested_id: str, new_from_id: str, new_to_id: str
) -> dict[str, Any]:
    details = live_transfer_details(current, requested_id)
    new_from_id = positive_id(new_from_id, "new_from_account_id")
    new_to_id = positive_id(new_to_id, "new_to_account_id")
    if new_from_id == new_to_id:
        raise BankingToolError("from_account_id and to_account_id must differ.")
    if (
        new_from_id == details["from_account_id"]
        and new_to_id == details["to_account_id"]
    ):
        raise BankingToolError("At least one transfer account link must actually change.")
    payload: dict[str, Any] = {
        "from_account_id": new_from_id,
        "to_account_id": new_to_id,
        "transaction_type": "transfer_fund",
        "amount": float(decimal_value(details["amount"], "live transfer amount")),
        "date": details["date"],
    }
    if details["exchange_rate"]:
        payload["exchange_rate"] = float(
            decimal_value(details["exchange_rate"], "live transfer exchange_rate")
        )
    for output_field, live_fields in (
        ("reference_number", ("reference_number", "reference")),
        ("description", ("description",)),
        ("currency_id", ("currency_id",)),
    ):
        present = next((field for field in live_fields if field in current), None)
        if present is not None and current[present] is not None:
            payload[output_field] = clean_text(
                str(current[present]), f"live {output_field}", 2000, required=False
            )
    return payload


def snapshots_by_role(targets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for snapshot in targets:
        role = snapshot["role"]
        if role in result:
            raise BankingToolError(f"Duplicate target snapshot role: {role}.")
        result[role] = snapshot
    return result


def require_general_update_currency(
    payload: dict[str, Any], targets: list[dict[str, Any]]
) -> None:
    roles = snapshots_by_role(targets)
    from_currency = roles["proposed_from_account"]["before_state"]["currency"].strip().casefold()
    to_currency = roles["proposed_to_account"]["before_state"]["currency"].strip().casefold()
    if (not from_currency or not to_currency or from_currency != to_currency) and "exchange_rate" not in payload:
        raise BankingToolError(
            "Proposed transfer account currencies must match unless the live transfer has an explicit "
            "positive exchange_rate that is preserved in the PUT payload."
        )


def apply_transfer_account_currency_policy(
    source_id: str, payload: dict[str, Any], targets: list[dict[str, Any]]
) -> dict[str, Any]:
    canonical = dict(payload)
    if positive_id(source_id, "source transaction_id") != "96274000001535012":
        return canonical
    roles = snapshots_by_role(targets)
    proposed_from = roles["proposed_from_account"]["before_state"]
    proposed_to = roles["proposed_to_account"]["before_state"]
    if proposed_from["currency"].upper() != "USD" or proposed_to["currency"].upper() != "USD":
        raise BankingToolError("Verified USD Airwallex transfer requires two protected USD accounts.")
    canonical.pop("currency_id", None)
    canonical.pop("exchange_rate", None)
    return canonical


def validate_airwallex_account_update(
    source: dict[str, Any], payload: dict[str, Any], targets: list[dict[str, Any]],
    sources: dict[str, str],
) -> dict[str, str] | None:
    details = live_transfer_details(source["current_state"], source["requested_id"])
    amount = decimal_value(details["amount"], "live transfer amount")
    guarded = (
        details["transaction_id"] in AIRWALLEX_ACCOUNT_UPDATE_CONSTRAINTS
        or abs(amount) in AIRWALLEX_AMOUNTS
        or "airwallex" in " ".join(sources.values()).casefold()
    )
    if not guarded:
        return None
    constraint = AIRWALLEX_ACCOUNT_UPDATE_CONSTRAINTS.get(details["transaction_id"])
    if constraint is None:
        raise BankingToolError(
            "Airwallex account-link updates are restricted to the two verified transfer IDs."
        )
    roles = snapshots_by_role(targets)
    current_from = roles["current_from_account"]["before_state"]
    current_to = roles["current_to_account"]["before_state"]
    proposed_from = roles["proposed_from_account"]["before_state"]
    proposed_to = roles["proposed_to_account"]["before_state"]
    expected_values = {
        "amount": (amount, constraint["amount"]),
        "current from account": (details["from_account_id"], constraint["current_from_account_id"]),
        "current destination account": (details["to_account_id"], constraint["to_account_id"]),
        "new source account": (payload["from_account_id"], constraint["new_from_account_id"]),
        "new destination account": (payload["to_account_id"], constraint["to_account_id"]),
        "current source snapshot ID": (current_from["account_id"], constraint["current_from_account_id"]),
        "current source name": (current_from["name"], constraint["current_from_account_name"]),
        "current source currency": (current_from["currency"].upper(), constraint["current_from_currency"]),
        "current destination snapshot ID": (current_to["account_id"], constraint["to_account_id"]),
        "current destination name": (current_to["name"], constraint["to_account_name"]),
        "current destination currency": (current_to["currency"].upper(), constraint["to_currency"]),
        "new source snapshot ID": (proposed_from["account_id"], constraint["new_from_account_id"]),
        "new source name": (proposed_from["name"], constraint["new_from_account_name"]),
        "new source currency": (proposed_from["currency"].upper(), constraint["new_from_currency"]),
        "new destination snapshot ID": (proposed_to["account_id"], constraint["to_account_id"]),
        "new destination name": (proposed_to["name"], constraint["to_account_name"]),
        "new destination currency": (proposed_to["currency"].upper(), constraint["to_currency"]),
    }
    mismatches = [label for label, (actual, expected) in expected_values.items() if actual != expected]
    if normalized_status(current_from["status"]) != "active":
        mismatches.append("current source active status")
    if mismatches:
        raise BankingToolError(
            "Airwallex hard-coded account mapping constraint failed: " + ", ".join(mismatches)
        )
    return {
        "transaction_id": details["transaction_id"],
        "amount": decimal_text(amount, "Airwallex transfer amount"),
        "current_from_account_id": details["from_account_id"],
        "approved_from_account_id": payload["from_account_id"],
        "approved_to_account_id": payload["to_account_id"],
    }


def validate_recovery_input(value: Any) -> dict[str, str]:
    """Explicit recovery request, hard-pinned to the one uncategorized USD line."""
    spec = AIRWALLEX_USD_RECOVERY
    if not isinstance(value, dict):
        raise BankingToolError("recovery must be an object.")
    closed_fields(value, RECOVERY_INPUT_FIELDS, "recovery")
    mode = clean_text(value["mode"], "recovery.mode", 100)
    if mode != AIRWALLEX_USD_RECOVERY_MODE:
        raise BankingToolError(
            "recovery.mode must be exactly " + AIRWALLEX_USD_RECOVERY_MODE
            + "; no other recovery mode exists in this tool."
        )
    digest = clean_text(value["historical_plan_sha256"], "recovery.historical_plan_sha256", 64)
    if not HEX_64_RE.fullmatch(digest) or not secrets.compare_digest(
        digest, spec["historical_plan_sha256"]
    ):
        raise BankingToolError(
            "recovery.historical_plan_sha256 is not the pinned immutable evidence digest."
        )
    superseded = positive_id(
        value["superseded_transfer_transaction_id"], "recovery.superseded_transfer_transaction_id"
    )
    if superseded != spec["superseded_transfer_transaction_id"]:
        raise BankingToolError(
            "recovery.superseded_transfer_transaction_id must be exactly "
            + spec["superseded_transfer_transaction_id"] + "."
        )
    return {
        "mode": mode,
        "historical_plan": clean_text(value["historical_plan"], "recovery.historical_plan", 4000),
        "historical_plan_sha256": digest,
        "superseded_transfer_transaction_id": superseded,
    }


def verify_recovery_historical_plan(raw_path: Any) -> dict[str, str]:
    """Re-derive the immutable original-transfer evidence from disk. Never trusted on faith."""
    spec = AIRWALLEX_USD_RECOVERY
    resolved = contained_plan(raw_path)
    if resolved.name != spec["historical_plan_name"]:
        raise BankingToolError(
            "Recovery evidence must be the pinned historical plan " + spec["historical_plan_name"] + "."
        )
    plan = read_json_object(resolved, "Recovery historical plan")
    closed_fields(plan, PLAN_FIELDS, "Recovery historical plan")
    saved = str(plan.get("sha256") or "")
    core = {key: value for key, value in plan.items() if key != "sha256"}
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise BankingToolError(
            "Recovery historical plan failed its own digest check; the immutable evidence changed."
        )
    if not secrets.compare_digest(saved, spec["historical_plan_sha256"]):
        raise BankingToolError("Recovery historical plan is not the pinned immutable evidence plan.")
    if (
        plan["tool"] != TOOL_NAME
        or plan["action"] != "update_transfer_accounts"
        or plan["schema_version"] != SCHEMA_VERSION
    ):
        raise BankingToolError("Recovery historical plan is not this tool's transfer-account plan.")

    payload = plan["payload"]
    source_snapshot = plan["source_snapshot"]
    live = source_snapshot["current_state"]
    imported = live.get("imported_transactions")
    if not isinstance(imported, list) or len(imported) != 1 or not isinstance(imported[0], dict):
        raise BankingToolError(
            "Recovery historical plan must record exactly one imported statement line."
        )
    line = imported[0]
    mapping = plan["evidence"].get("airwallex_mapping")
    if not isinstance(mapping, dict):
        raise BankingToolError("Recovery historical plan lacks its Airwallex account mapping evidence.")
    expected = {
        "historical payload from_account_id": (payload.get("from_account_id"), spec["from_account_id"]),
        "historical payload to_account_id": (payload.get("to_account_id"), spec["to_account_id"]),
        "historical payload transaction_type": (payload.get("transaction_type"), spec["transaction_type"]),
        "historical payload amount": (
            decimal_text(payload.get("amount"), "historical payload amount"),
            decimal_text(spec["amount"], "recovery amount"),
        ),
        "historical payload date": (date_text(payload.get("date"), "historical payload date"), spec["date"]),
        "historical payload reference_number": (
            str(payload.get("reference_number") or "").strip(), spec["reference_number"],
        ),
        "historical payload description": (
            str(payload.get("description") or "").strip(), spec["description"],
        ),
        "historical superseded transfer ID": (
            source_snapshot.get("requested_id"), spec["superseded_transfer_transaction_id"],
        ),
        "historical transfer amount": (
            decimal_text(source_snapshot["before_state"]["amount"], "historical transfer amount"),
            decimal_text(spec["amount"], "recovery amount"),
        ),
        "historical transfer date": (source_snapshot["before_state"]["date"], spec["date"]),
        "historical imported line ID": (
            str(line.get("imported_transaction_id") or "").strip(), spec["source_transaction_id"],
        ),
        "historical imported line amount": (
            decimal_text(line.get("amount"), "historical imported line amount"),
            decimal_text(spec["amount"], "recovery amount"),
        ),
        "historical imported line date": (
            date_text(line.get("date"), "historical imported line date"), spec["date"],
        ),
        "historical imported line description": (
            str(line.get("description") or "").strip(), spec["description"],
        ),
        "historical imported line account": (
            str(line.get("account_id") or "").strip(), spec["to_account_id"],
        ),
        "historical mapping transaction ID": (
            mapping.get("transaction_id"), spec["superseded_transfer_transaction_id"],
        ),
        "historical mapping approved source": (
            mapping.get("approved_from_account_id"), spec["from_account_id"],
        ),
        "historical mapping approved destination": (
            mapping.get("approved_to_account_id"), spec["to_account_id"],
        ),
    }
    roles = snapshots_by_role(plan["target_snapshots"])
    for role, key_id, key_name, key_currency in (
        ("proposed_from_account", "from_account_id", "from_account_name", "from_currency"),
        ("proposed_to_account", "to_account_id", "to_account_name", "to_currency"),
    ):
        snapshot = roles.get(role)
        if snapshot is None:
            raise BankingToolError(f"Recovery historical plan lacks its {role} snapshot.")
        before = snapshot["before_state"]
        expected[f"historical {role} ID"] = (before["account_id"], spec[key_id])
        expected[f"historical {role} name"] = (before["name"], spec[key_name])
        expected[f"historical {role} currency"] = (before["currency"].upper(), spec[key_currency])
    mismatches = [label for label, (actual, want) in expected.items() if actual != want]
    if mismatches:
        raise BankingToolError(
            "Recovery historical evidence does not carry the exact original mapping: "
            + ", ".join(mismatches)
        )

    lock = lock_path(saved)
    if not lock.is_file():
        raise BankingToolError(
            "Recovery historical plan carries no commit lock; it is not proven spent and replay-locked."
        )
    lock_state = read_json_object(lock, "Recovery historical plan commit lock")
    if (
        str(lock_state.get("plan_sha256") or "") != saved
        or lock_state.get("no_retry") is not True
        or str(lock_state.get("status") or "") != spec["historical_plan_lock_status"]
    ):
        raise BankingToolError(
            "Recovery historical plan lock is not the permanently locked "
            + spec["historical_plan_lock_status"] + " record."
        )
    return {
        "historical_plan": str(resolved),
        "historical_plan_sha256": saved,
        "historical_plan_lock_status": spec["historical_plan_lock_status"],
    }


def require_airwallex_usd_recovery_facts(
    source: dict[str, Any], payload: dict[str, Any], targets: list[dict[str, Any]]
) -> None:
    """Every fixed fact of the one recoverable line, checked against live snapshots."""
    spec = AIRWALLEX_USD_RECOVERY
    before = source["before_state"]
    live = source["current_state"]
    if source["requested_id"] != spec["source_transaction_id"]:
        raise BankingToolError(
            "The recovery path is reachable only for statement line "
            + spec["source_transaction_id"] + "."
        )
    closed_fields(payload, RECOVERY_PAYLOAD_FIELDS, "recovery categorize payload")
    roles = snapshots_by_role([row for row in targets if row["record_type"] == "account"])
    from_account = roles.get("from_account")
    to_account = roles.get("to_account")
    if from_account is None or to_account is None:
        raise BankingToolError("Recovery requires freshly read from/to account snapshots.")
    charges_present = "bank_charges" in live
    gross_present = "gross_amount" in live
    if not charges_present and not gross_present:
        raise BankingToolError(
            "Recovery requires the live statement line to expose bank_charges or gross_amount."
        )
    expected = {
        "source status": (normalized_status(before["status"]), spec["source_status"]),
        "source statement account": (before["account_id"], spec["source_account_id"]),
        "source amount": (
            decimal_text(before["amount"], "live source amount"),
            decimal_text(spec["amount"], "recovery amount"),
        ),
        "source date": (before["date"], spec["date"]),
        "source currency": (before["currency"].upper(), spec["currency_code"]),
        "source currency ID": (str(live.get("currency_id") or "").strip(), spec["currency_id"]),
        "source description": (str(live.get("description") or "").strip(), spec["description"]),
        "payload transaction_type": (payload["transaction_type"], spec["transaction_type"]),
        "payload from_account_id": (payload["from_account_id"], spec["from_account_id"]),
        "payload to_account_id": (payload["to_account_id"], spec["to_account_id"]),
        "payload currency_id": (payload["currency_id"], spec["currency_id"]),
        "payload description": (payload["description"], spec["description"]),
        "payload reference_number": (payload["reference_number"], spec["reference_number"]),
        "payload amount": (
            decimal_text(payload["amount"], "recovery payload amount"),
            decimal_text(spec["amount"], "recovery amount"),
        ),
        "payload date": (date_text(payload["date"], "recovery payload date"), spec["date"]),
        "new source account ID": (from_account["before_state"]["account_id"], spec["from_account_id"]),
        "new source account name": (from_account["before_state"]["name"], spec["from_account_name"]),
        "new source account currency": (
            from_account["before_state"]["currency"].upper(), spec["from_currency"],
        ),
        "new source account status": (
            normalized_status(from_account["before_state"]["status"]), "active",
        ),
        "destination account ID": (to_account["before_state"]["account_id"], spec["to_account_id"]),
        "destination account name": (to_account["before_state"]["name"], spec["to_account_name"]),
        "destination account currency": (
            to_account["before_state"]["currency"].upper(), spec["to_currency"],
        ),
        "destination account status": (
            normalized_status(to_account["before_state"]["status"]), "active",
        ),
    }
    if charges_present:
        expected["source bank charges"] = (
            decimal_text(live.get("bank_charges"), "live bank_charges"), "0",
        )
    if gross_present:
        expected["source gross amount"] = (
            decimal_text(live.get("gross_amount"), "live gross_amount"),
            decimal_text(spec["amount"], "recovery amount"),
        )
    mismatches = [label for label, (actual, want) in expected.items() if actual != want]
    if mismatches:
        raise BankingToolError(
            "Airwallex USD recovery fixed-fact check failed: " + ", ".join(mismatches)
        )


def prove_superseded_transfer_absent(access_token: str, vault: dict[str, Any]) -> None:
    """Fresh live proof through the existing read path that the old transfer is gone."""
    transfer_id = AIRWALLEX_USD_RECOVERY["superseded_transfer_transaction_id"]
    try:
        get_bank_transaction(access_token, vault, transfer_id, "regular")
    except BankingRecordAbsent:
        return
    raise BankingToolError(
        f"REFUSED: superseded transfer {transfer_id} is live again. The recovery precondition "
        "is false; reconcile the real Zoho state instead of recategorizing."
    )


def build_airwallex_usd_recovery_evidence(
    recovery_input: dict[str, str], historical: dict[str, str]
) -> dict[str, Any]:
    spec = AIRWALLEX_USD_RECOVERY
    return {
        "mode": AIRWALLEX_USD_RECOVERY_MODE,
        "statement": AIRWALLEX_USD_RECOVERY_STATEMENT,
        "source_transaction_id": spec["source_transaction_id"],
        "superseded_transfer_transaction_id": recovery_input["superseded_transfer_transaction_id"],
        "superseded_transfer_verified_absent": True,
        "historical_plan": historical["historical_plan"],
        "historical_plan_sha256": historical["historical_plan_sha256"],
        "historical_plan_lock_status": historical["historical_plan_lock_status"],
        "live_outgoing_counterpart_required": False,
    }


def validate_recovery_evidence(
    evidence: Any, source: dict[str, Any], payload: dict[str, Any], targets: list[dict[str, Any]]
) -> None:
    """Re-derive the whole recovery block offline; equality is the only accepted answer."""
    if not isinstance(evidence, dict):
        raise BankingToolError("evidence.airwallex_recovery must be an object.")
    closed_fields(evidence, RECOVERY_EVIDENCE_FIELDS, "airwallex_recovery")
    if evidence.get("superseded_transfer_verified_absent") is not True:
        raise BankingToolError("Recovery evidence must record the superseded transfer as verified absent.")
    if evidence.get("live_outgoing_counterpart_required") is not False:
        raise BankingToolError("Recovery evidence must record that no live counterpart exists to require.")
    recovery_input = validate_recovery_input({
        "mode": evidence.get("mode"),
        "historical_plan": evidence.get("historical_plan"),
        "historical_plan_sha256": evidence.get("historical_plan_sha256"),
        "superseded_transfer_transaction_id": evidence.get("superseded_transfer_transaction_id"),
    })
    historical = verify_recovery_historical_plan(recovery_input["historical_plan"])
    require_airwallex_usd_recovery_facts(source, payload, targets)
    if evidence != build_airwallex_usd_recovery_evidence(recovery_input, historical):
        raise BankingToolError("Recovery evidence does not match its immutable re-derived form.")


def recovery_summary(
    evidence: dict[str, Any], payload: dict[str, Any], targets: list[dict[str, Any]]
) -> dict[str, Any]:
    spec = AIRWALLEX_USD_RECOVERY
    roles = snapshots_by_role([row for row in targets if row["record_type"] == "account"])
    return {
        "kind": AIRWALLEX_USD_RECOVERY_MODE,
        "statement": AIRWALLEX_USD_RECOVERY_STATEMENT,
        "source_statement_line_id": spec["source_transaction_id"],
        "transaction_type": payload["transaction_type"],
        "from_account": roles["from_account"]["before_state"],
        "to_account": roles["to_account"]["before_state"],
        "amount": decimal_text(payload["amount"], "recovery payload amount"),
        "currency": spec["currency_code"],
        "currency_id": payload["currency_id"],
        "date": payload["date"],
        "reference_number": payload["reference_number"],
        "description": payload["description"],
        "superseded_transfer_transaction_id": evidence["superseded_transfer_transaction_id"],
        "superseded_transfer_verified_absent": True,
        "historical_plan_sha256": evidence["historical_plan_sha256"],
        "write": (
            "exactly one POST /books/v3/banktransactions/uncategorized/"
            + spec["source_transaction_id"] + "/categorize"
        ),
        "emails_sent": 0,
        "revenue_or_income_classification": "impossible: transfer_fund only, no income route exists",
        "write_performed_yet": False,
    }


def require_match_compatibility(source: dict[str, Any], targets: list[dict[str, Any]]) -> None:
    source_before = source["before_state"]
    total = sum(
        (abs(decimal_value(target["before_state"]["amount"], "target amount")) for target in targets),
        Decimal("0"),
    )
    if total != abs(decimal_value(source_before["amount"], "source amount")):
        raise BankingToolError("Match target amounts do not total the source statement amount.")
    if any(
        target["before_state"]["currency"].casefold() != source_before["currency"].casefold()
        for target in targets
    ):
        raise BankingToolError("Match target currency does not equal the source statement currency.")


def require_ids_in_source(source: dict[str, Any], ids: list[str], label: str) -> None:
    values = collect_scalar_strings(source["current_state"])
    missing = [item for item in ids if item not in values]
    if missing:
        raise BankingToolError(f"{label} ID(s) are not linked in the live source transaction: {', '.join(missing)}")


def require_categorize_accounts_active(targets: list[dict[str, Any]]) -> None:
    """Refuse a categorize onto an inactive or deleted account.

    Zoho rejects those with HTTP 400 code 11015, and the account GET already
    carries the answer - so checking it here costs nothing, while letting it
    through burns the plan's single-use lock and forces a live reconcile on a
    write that was never going to land. 2026-08-11: from_account
    96274000000149257 staged with is_active False recorded in its own snapshot.
    """
    inactive = [
        f"{row['role']} {row['requested_id']}"
        for row in targets
        if row["record_type"] == "account"
        and normalized_status(row["before_state"]["status"]) != "active"
    ]
    if inactive:
        raise BankingToolError(
            "Categorization requires active accounts; Zoho rejects inactive or deleted "
            "accounts with code 11015. Inactive: " + ", ".join(inactive)
        )


def summary_for(
    action: str, source: dict[str, Any], payload: dict[str, Any], targets: list[dict[str, Any]],
    recovery: dict[str, Any] | None = None,
) -> dict[str, Any]:
    before = source["before_state"]
    if action == "update_transfer_accounts":
        details = live_transfer_details(source["current_state"], source["requested_id"])
        roles = snapshots_by_role(targets)
        return {
            "action": action,
            "transaction_id": details["transaction_id"],
            "transaction_type": details["transaction_type"],
            "status": details["status"],
            "amount": details["amount"],
            "date": details["date"],
            "currency": details["currency"],
            "exchange_rate": details["exchange_rate"],
            "before": {
                "from_account": roles["current_from_account"]["before_state"],
                "to_account": roles["current_to_account"]["before_state"],
            },
            "after": {
                "from_account": roles["proposed_from_account"]["before_state"],
                "to_account": roles["proposed_to_account"]["before_state"],
            },
        }
    if action == "match":
        target: Any = payload["transactions_to_be_matched"]
    elif action == "categorize":
        target = {
            "transaction_type": payload["transaction_type"],
            "from_account_id": payload["from_account_id"],
            "to_account_id": payload["to_account_id"],
            "accounts": [
                row["before_state"] for row in targets if row["record_type"] == "account"
            ],
        }
    elif action == "unmatch":
        target = {"unmatch_transaction_ids": [
            row["requested_id"] for row in targets if row["role"] == "matched_transaction"
        ]}
    else:
        target = {"uncategorize_account_ids": [
            row["requested_id"] for row in targets if row["record_type"] == "account"
        ]}
    summary = {
        "action": action,
        "account": {"id": before["account_id"], "name": before["account_name"]},
        "date": before["date"],
        "payee": before["payee"],
        "reference": before["reference"],
        "amount": before["amount"],
        "currency": before["currency"],
        "status": before["status"],
        "target": target,
    }
    if recovery is not None:
        summary["recovery"] = recovery_summary(recovery, payload, targets)
    return summary


def stage_plan(
    action: str,
    organization: dict[str, str],
    payload: dict[str, Any],
    sources: dict[str, str],
    evidence: dict[str, Any],
    source: dict[str, Any],
    targets: list[dict[str, Any]],
    summary: dict[str, Any],
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
        "organization": organization,
        "payload": payload,
        "sources": sources,
        "evidence": evidence,
        "source_snapshot": source,
        "target_snapshots": targets,
        "human_summary": summary,
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
        raise BankingToolError("Refused to overwrite an existing banking plan.") from exc
    zoho_tool.append_receipt(
        f"zoho_banking_{action}_plan_staged_not_committed",
        f"plan={path}; sha256={digest}",
    )
    return path


def command_stage(args: argparse.Namespace, action: str) -> None:
    if action not in ACTIONS:
        raise BankingToolError("Unsupported banking stage action.")
    raw = read_json_object(Path(args.input), f"{action} input")
    expected_input = INPUT_FIELDS[action]
    optional_supplied = OPTIONAL_INPUT_FIELDS.get(action, set()) & set(raw)
    closed_fields(raw, expected_input | optional_supplied, f"{action} input")
    recovery_input = validate_recovery_input(raw["recovery"]) if "recovery" in raw else None
    source_id = positive_id(raw["source_transaction_id"], "source_transaction_id")
    sources = source_fields(raw["sources"])
    if action == "update_transfer_accounts":
        supplied_evidence = update_input_evidence(raw["evidence"])
        require_rachad_sent_item_source(sources)
    else:
        supplied_evidence = input_evidence(raw["evidence"])
    if action == "match":
        match_targets = validate_match_targets(raw["transactions_to_be_matched"])
    elif action == "categorize":
        category_input = validate_categorize_input_payload(raw["payload"])
    elif action == "unmatch":
        correction_ids = positive_id_list(raw["target_transaction_ids"], "target_transaction_ids")
    elif action == "uncategorize":
        correction_ids = positive_id_list(raw["target_account_ids"], "target_account_ids", 5)
    else:
        update_input = validate_update_transfer_input_payload(raw["payload"])

    vault = zoho_tool.load_vault()
    scopes = [str(scope) for scope in vault.get("scopes") or []]
    zoho_tool.validate_scopes(scopes)
    if READ_SCOPE not in scopes:
        raise BankingToolError(f"Saved Zoho connection lacks {READ_SCOPE}.")
    access_token, vault = zoho_tool.refresh_access_token(vault)
    organization = get_frp_organization(access_token, vault)
    current_source = get_bank_transaction(access_token, vault, source_id, SOURCE_MODES[action])
    source = transaction_snapshot(current_source, source_id, "source", SOURCE_MODES[action])
    require_source_status(action, source["before_state"]["status"])
    targets: list[dict[str, Any]] = []

    if action == "match":
        payload = {"transactions_to_be_matched": match_targets}
        for row in match_targets:
            current = get_match_target(
                access_token,
                vault,
                source_id,
                row["transaction_id"],
                row["transaction_type"],
            )
            target = transaction_snapshot(current, row["transaction_id"], "match_target")
            if target["before_state"]["transaction_type"] != row["transaction_type"]:
                raise BankingToolError(
                    f"Match target {row['transaction_id']} live transaction_type does not equal "
                    f"the requested {row['transaction_type']}."
                )
            targets.append(target)
        require_match_compatibility(source, targets)
    elif action == "categorize":
        payload = {
            **category_input,
            "amount": float(decimal_value(source["before_state"]["amount"], "source amount")),
            "date": source["before_state"]["date"],
        }
        for role, account_id in (
            ("from_account", payload["from_account_id"]),
            ("to_account", payload["to_account_id"]),
        ):
            targets.append(account_snapshot(get_account(access_token, vault, account_id), account_id, role))
        require_categorize_accounts_active(targets)
        if source["before_state"]["account_id"] not in {payload["from_account_id"], payload["to_account_id"]}:
            raise BankingToolError("Categorization must use the source bank account as from_account_id or to_account_id.")
    elif action == "unmatch":
        payload = {}
        require_ids_in_source(source, correction_ids, "Matched transaction")
        for transaction_id in correction_ids:
            current = get_bank_transaction(access_token, vault, transaction_id, "regular")
            targets.append(transaction_snapshot(current, transaction_id, "matched_transaction"))
    elif action == "uncategorize":
        payload = {}
        require_ids_in_source(source, correction_ids, "Category account")
        for account_id in correction_ids:
            targets.append(account_snapshot(get_account(access_token, vault, account_id), account_id, "category_account"))
    else:
        details = live_transfer_details(current_source, source_id)
        payload = construct_update_transfer_payload(
            current_source,
            source_id,
            update_input["new_from_account_id"],
            update_input["new_to_account_id"],
        )
        account_cache: dict[str, dict[str, Any]] = {}
        for role, account_id in (
            ("current_from_account", details["from_account_id"]),
            ("current_to_account", details["to_account_id"]),
            ("proposed_from_account", payload["from_account_id"]),
            ("proposed_to_account", payload["to_account_id"]),
        ):
            if account_id not in account_cache:
                account_cache[account_id] = get_account(access_token, vault, account_id)
            targets.append(account_snapshot(account_cache[account_id], account_id, role))
        # The verified USD closure transfer is falsely labelled CAD only because its
        # current source link is a CAD account.  The commissioned write changes
        # account links only: omit stale CAD metadata and let Zoho derive USD from
        # the protected USD source and destination snapshots.
        payload = apply_transfer_account_currency_policy(source_id, payload, targets)
        require_general_update_currency(payload, targets)

    if action == "update_transfer_accounts":
        mapping = validate_airwallex_account_update(source, payload, targets, sources)
        evidence = {
            "basis": supplied_evidence["basis"],
            "airwallex_guarded": mapping is not None,
            "existing_transfer_is_source_ledger_evidence": True,
            "airwallex_mapping": mapping,
        }
    else:
        guarded = airwallex_guarded(source, sources)
        outgoing_id = supplied_evidence["airwallex_outgoing_transaction_id"]
        verification: dict[str, Any] | None = None
        recovery_evidence: dict[str, Any] | None = None
        if recovery_input is not None:
            if not guarded:
                raise BankingToolError("The recovery path exists only for the guarded Airwallex line.")
            if outgoing_id:
                raise BankingToolError(
                    "The recovery path replaces the outgoing counterpart proof; it cannot also carry one."
                )
        if guarded:
            if action == "match" and any(
                row["transaction_type"] != "transfer_fund" for row in payload["transactions_to_be_matched"]
            ):
                raise BankingToolError("Airwallex statement lines may only be matched as transfer_fund, never income/revenue.")
            if action == "categorize" and payload["transaction_type"] != "transfer_fund":
                raise BankingToolError("Airwallex statement lines may only be categorized as transfer_fund, never income/revenue.")
            if recovery_input is not None:
                # The one exemption. Every fixed fact must match, the immutable original
                # plan must re-derive, and the old transfer must be freshly proved gone.
                require_airwallex_usd_recovery_facts(source, payload, targets)
                historical = verify_recovery_historical_plan(recovery_input["historical_plan"])
                prove_superseded_transfer_absent(access_token, vault)
                recovery_evidence = build_airwallex_usd_recovery_evidence(recovery_input, historical)
            elif not outgoing_id:
                raise BankingToolError("Airwallex plans require a verified corresponding outgoing transaction ID in evidence.")
            if outgoing_id:
                outgoing = next(
                    (row for row in targets if row["record_type"] == "transaction" and row["requested_id"] == outgoing_id),
                    None,
                )
                if outgoing is None:
                    current = get_bank_transaction(access_token, vault, outgoing_id, "regular")
                    outgoing = transaction_snapshot(current, outgoing_id, "airwallex_outgoing")
                    targets.append(outgoing)
                verification = airwallex_verification(source, outgoing)
        elif outgoing_id:
            raise BankingToolError("Airwallex outgoing evidence is only accepted for an Airwallex-guarded statement line.")

        evidence = {
            "basis": supplied_evidence["basis"],
            "airwallex_guarded": guarded,
            "airwallex_outgoing_transaction_id": outgoing_id,
            "airwallex_verification": verification,
            "airwallex_recovery": recovery_evidence,
        }
    summary = summary_for(
        action, source, payload, targets,
        None if action == "update_transfer_accounts" else evidence["airwallex_recovery"],
    )
    path = stage_plan(action, organization, payload, sources, evidence, source, targets, summary)
    plan = read_json_object(path, "Staged plan")
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED",
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "action": action,
        "summary": summary,
        "sources": sources,
        "evidence": evidence,
        "approval": APPROVAL_WORD,
    }, ensure_ascii=False, indent=2))


def validate_snapshot(snapshot: Any, label: str) -> None:
    if not isinstance(snapshot, dict):
        raise BankingToolError(f"{label} must be an object.")
    closed_fields(snapshot, SNAPSHOT_FIELDS, label)
    record_type = snapshot["record_type"]
    if record_type not in {"transaction", "account"}:
        raise BankingToolError(f"{label} record_type is invalid.")
    clean_text(snapshot["role"], f"{label}.role", 100)
    requested_id = positive_id(snapshot["requested_id"], f"{label}.requested_id")
    expected_mode = {"transaction": {"regular"}, "account": {"account"}}[record_type]
    if snapshot["get_mode"] not in expected_mode:
        raise BankingToolError(f"{label} get_mode is invalid.")
    current = snapshot["current_state"]
    before = snapshot["before_state"]
    if not isinstance(current, dict) or not isinstance(before, dict):
        raise BankingToolError(f"{label} current_state and before_state must be objects.")
    if not secrets.compare_digest(str(snapshot["current_state_sha256"]), digest_for(current)):
        raise BankingToolError(f"{label} current-state hash is invalid.")
    if record_type == "transaction":
        closed_fields(before, TRANSACTION_BEFORE_FIELDS, f"{label}.before_state")
        expected_before = transaction_before(current, requested_id)
    else:
        closed_fields(before, ACCOUNT_BEFORE_FIELDS, f"{label}.before_state")
        expected_before = account_before(current, requested_id)
    if before != expected_before:
        raise BankingToolError(f"{label} before_state does not match its immutable live snapshot.")


def validate_plan_payload(plan: dict[str, Any]) -> None:
    action = plan["action"]
    payload = plan["payload"]
    if not isinstance(payload, dict):
        raise BankingToolError("Plan payload must be an object.")
    source = plan["source_snapshot"]
    targets = plan["target_snapshots"]
    if action == "match":
        closed_fields(payload, MATCH_PAYLOAD_FIELDS, "match payload")
        rows = validate_match_targets(payload["transactions_to_be_matched"])
        if rows != payload["transactions_to_be_matched"]:
            raise BankingToolError("match payload is not canonical.")
        expected_ids = [row["transaction_id"] for row in rows]
        actual_ids = [row["requested_id"] for row in targets if row["role"] == "match_target"]
        if expected_ids != actual_ids:
            raise BankingToolError("match targets do not match the payload.")
        actual_types = [
            row["before_state"]["transaction_type"]
            for row in targets if row["role"] == "match_target"
        ]
        if [row["transaction_type"] for row in rows] != actual_types:
            raise BankingToolError("match target live transaction types do not match the payload.")
        require_match_compatibility(source, [row for row in targets if row["role"] == "match_target"])
    elif action == "categorize":
        allowed_fields(payload, CATEGORIZE_PAYLOAD_FIELDS, "categorize payload")
        missing = sorted(CATEGORIZE_REQUIRED_FIELDS - set(payload))
        if missing:
            raise BankingToolError("categorize payload is missing: " + ", ".join(missing))
        clean_input = validate_categorize_input_payload({
            key: value for key, value in payload.items() if key not in {"amount", "date"}
        })
        expected = {
            **clean_input,
            "amount": float(decimal_value(source["before_state"]["amount"], "source amount")),
            "date": source["before_state"]["date"],
        }
        if payload != expected:
            raise BankingToolError("categorize payload is not canonical or changed source amount/date.")
        account_ids = [
            row["requested_id"] for row in targets if row["role"] in {"from_account", "to_account"}
        ]
        if account_ids != [payload["from_account_id"], payload["to_account_id"]]:
            raise BankingToolError("categorize account targets do not match the payload.")
    elif action == "update_transfer_accounts":
        allowed_fields(payload, UPDATE_TRANSFER_PAYLOAD_FIELDS, "update_transfer_accounts payload")
        missing = sorted(UPDATE_TRANSFER_REQUIRED_FIELDS - set(payload))
        if missing:
            raise BankingToolError("update_transfer_accounts payload is missing: " + ", ".join(missing))
        details = live_transfer_details(source["current_state"], source["requested_id"])
        expected = construct_update_transfer_payload(
            source["current_state"], source["requested_id"],
            payload["from_account_id"], payload["to_account_id"],
        )
        expected = apply_transfer_account_currency_policy(
            source["requested_id"], expected, targets
        )
        if payload != expected:
            raise BankingToolError(
                "update_transfer_accounts payload is not canonical or changed immutable live fields."
            )
        expected_roles = [
            "current_from_account", "current_to_account",
            "proposed_from_account", "proposed_to_account",
        ]
        if [row["role"] for row in targets] != expected_roles:
            raise BankingToolError("update_transfer_accounts must snapshot current/proposed accounts in order.")
        expected_ids = [
            details["from_account_id"], details["to_account_id"],
            payload["from_account_id"], payload["to_account_id"],
        ]
        if [row["requested_id"] for row in targets] != expected_ids:
            raise BankingToolError("update_transfer_accounts account snapshots do not match before/after links.")
        require_general_update_currency(payload, targets)
    else:
        closed_fields(payload, set(), f"{action} payload")
        role = "matched_transaction" if action == "unmatch" else "category_account"
        if not any(row["role"] == role for row in targets):
            raise BankingToolError(f"{action} plan must include its live target records.")


def validate_plan(plan: dict[str, Any], expected_action: str) -> None:
    closed_fields(plan, PLAN_FIELDS, "Plan")
    if (
        plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("tool") != TOOL_NAME
        or plan.get("action") != expected_action
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise BankingToolError("Plan schema version, tool, action, or approval requirement is invalid.")
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise BankingToolError("Plan nonce is invalid.")
    created = parse_time(plan["created_utc"], "creation time")
    expires = parse_time(plan["expires_utc"], "expiry")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise BankingToolError("Plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise BankingToolError("Plan creation time is in the future.")
    if now >= expires:
        raise BankingToolError("Plan expired. Stage a new banking plan for review.")
    organization = plan["organization"]
    if not isinstance(organization, dict):
        raise BankingToolError("Plan organization lock is invalid.")
    closed_fields(organization, ORGANIZATION_FIELDS, "organization")
    positive_id(organization["organization_id"], "organization.organization_id")
    name = clean_text(organization["name"], "organization.name", 500)
    if "frpdepot" not in "".join(character for character in name.casefold() if character.isalnum()):
        raise BankingToolError("Plan is not locked to FRP Depot.")
    sources = source_fields(plan["sources"])
    evidence = plan["evidence"]
    if not isinstance(evidence, dict):
        raise BankingToolError("Plan evidence must be an object.")
    if expected_action == "update_transfer_accounts":
        closed_fields(evidence, UPDATE_EVIDENCE_FIELDS, "evidence")
        clean_text(evidence["basis"], "evidence.basis", 5000)
        if not isinstance(evidence["airwallex_guarded"], bool):
            raise BankingToolError("evidence.airwallex_guarded must be true or false.")
        if evidence["existing_transfer_is_source_ledger_evidence"] is not True:
            raise BankingToolError(
                "update_transfer_accounts must use the existing transfer as source-side ledger evidence."
            )
        require_rachad_sent_item_source(sources)
    else:
        closed_fields(evidence, EVIDENCE_FIELDS, "evidence")
        clean_text(evidence["basis"], "evidence.basis", 5000)
        if not isinstance(evidence["airwallex_guarded"], bool):
            raise BankingToolError("evidence.airwallex_guarded must be true or false.")
        outgoing_id = evidence["airwallex_outgoing_transaction_id"]
        if outgoing_id:
            positive_id(outgoing_id, "evidence.airwallex_outgoing_transaction_id")
    validate_snapshot(plan["source_snapshot"], "source_snapshot")
    if plan["source_snapshot"]["role"] != "source" or plan["source_snapshot"]["get_mode"] != SOURCE_MODES[expected_action]:
        raise BankingToolError("Plan source snapshot has the wrong role or GET mode.")
    require_source_status(expected_action, plan["source_snapshot"]["before_state"]["status"])
    targets = plan["target_snapshots"]
    if not isinstance(targets, list) or not 1 <= len(targets) <= 21:
        raise BankingToolError("Plan must include 1-21 live target snapshots.")
    seen: set[tuple[str, ...]] = set()
    for index, snapshot in enumerate(targets):
        validate_snapshot(snapshot, f"target_snapshots[{index}]")
        key = (
            (snapshot["record_type"], snapshot["requested_id"], snapshot["role"])
            if expected_action == "update_transfer_accounts"
            else (snapshot["record_type"], snapshot["requested_id"])
        )
        if key in seen:
            raise BankingToolError("Plan target snapshots contain a duplicate protected record/role.")
        seen.add(key)
    validate_plan_payload(plan)
    if expected_action == "update_transfer_accounts":
        expected_mapping = validate_airwallex_account_update(
            plan["source_snapshot"], plan["payload"], targets, sources
        )
        guarded = expected_mapping is not None
        if evidence["airwallex_guarded"] != guarded:
            raise BankingToolError("Airwallex update guard evidence is inconsistent with live snapshots.")
        mapping = evidence["airwallex_mapping"]
        if expected_mapping is None:
            if mapping is not None:
                raise BankingToolError("Non-Airwallex transfer update contains Airwallex-only mapping evidence.")
        else:
            if not isinstance(mapping, dict):
                raise BankingToolError("Airwallex transfer update lacks its hard-coded mapping evidence.")
            closed_fields(mapping, AIRWALLEX_MAPPING_FIELDS, "airwallex_mapping")
            if mapping != expected_mapping:
                raise BankingToolError("Airwallex mapping evidence does not match immutable live snapshots.")
    else:
        guarded = airwallex_guarded(plan["source_snapshot"], sources)
        recovery = evidence["airwallex_recovery"]
        if evidence["airwallex_guarded"] != guarded:
            raise BankingToolError("Airwallex guard evidence is inconsistent with the statement line/amount.")
        if guarded:
            if expected_action == "match" and any(
                row["transaction_type"] != "transfer_fund"
                for row in plan["payload"]["transactions_to_be_matched"]
            ):
                raise BankingToolError("Airwallex matching is restricted to transfer_fund.")
            if expected_action == "categorize" and plan["payload"]["transaction_type"] != "transfer_fund":
                raise BankingToolError("Airwallex categorization is restricted to transfer_fund.")
            if recovery is not None:
                if expected_action != "categorize":
                    raise BankingToolError("The Airwallex recovery exemption exists only for categorize.")
                if outgoing_id or evidence["airwallex_verification"] is not None:
                    raise BankingToolError(
                        "A recovery plan replaces the outgoing counterpart proof and cannot also carry one."
                    )
                validate_recovery_evidence(
                    recovery, plan["source_snapshot"], plan["payload"], targets
                )
            else:
                if not outgoing_id:
                    raise BankingToolError("Airwallex plan lacks its outgoing transaction evidence ID.")
                outgoing = next(
                    (row for row in targets if row["record_type"] == "transaction" and row["requested_id"] == outgoing_id),
                    None,
                )
                if outgoing is None:
                    raise BankingToolError("Airwallex outgoing transaction snapshot is absent.")
                expected_verification = airwallex_verification(plan["source_snapshot"], outgoing)
                verification = evidence["airwallex_verification"]
                if not isinstance(verification, dict):
                    raise BankingToolError("Airwallex compatibility verification is absent.")
                closed_fields(verification, AIRWALLEX_VERIFICATION_FIELDS, "airwallex_verification")
                if verification != expected_verification:
                    raise BankingToolError("Airwallex compatibility evidence does not match the live snapshots.")
        elif outgoing_id or evidence["airwallex_verification"] is not None or recovery is not None:
            raise BankingToolError("Non-Airwallex plan contains Airwallex-only evidence.")
    summary = plan["human_summary"]
    if not isinstance(summary, dict):
        raise BankingToolError("Plan human summary must be an object.")
    if expected_action == "update_transfer_accounts":
        closed_fields(summary, UPDATE_SUMMARY_FIELDS, "human_summary")
        for side in ("before", "after"):
            if not isinstance(summary[side], dict):
                raise BankingToolError(f"human_summary.{side} must be an object.")
            closed_fields(summary[side], UPDATE_SUMMARY_SIDE_FIELDS, f"human_summary.{side}")
            for role in UPDATE_SUMMARY_SIDE_FIELDS:
                if not isinstance(summary[side][role], dict):
                    raise BankingToolError(f"human_summary.{side}.{role} must be an object.")
                closed_fields(
                    summary[side][role], ACCOUNT_BEFORE_FIELDS,
                    f"human_summary.{side}.{role}",
                )
        recovery = None
    else:
        closed_fields(
            summary, SUMMARY_FIELDS | ({"recovery"} if recovery is not None else set()),
            "human_summary",
        )
        if recovery is not None:
            if not isinstance(summary["recovery"], dict):
                raise BankingToolError("human_summary.recovery must be an object.")
            closed_fields(summary["recovery"], RECOVERY_SUMMARY_FIELDS, "human_summary.recovery")
    expected_summary = summary_for(
        expected_action, plan["source_snapshot"], plan["payload"], targets, recovery
    )
    if summary != expected_summary:
        raise BankingToolError("Plan human summary does not match the immutable source/target evidence.")


def load_plan(path: Path, expected_action: str) -> dict[str, Any]:
    plan = read_json_object(path, "Plan")
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise BankingToolError("Plan hash check failed. The plan changed after review.")
    validate_plan(plan, expected_action)
    return plan


def lock_path(plan_sha256: str) -> Path:
    if not HEX_64_RE.fullmatch(str(plan_sha256)):
        raise BankingToolError("Plan digest is invalid for replay locking.")
    return PLAN_DIR / ".commit-locks" / f"{plan_sha256}.json"


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise BankingToolError("This banking plan has already entered commit and cannot be replayed.") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def api_post_allowed(
    access_token: str,
    api_domain: str,
    action: str,
    transaction_id: str,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """The POST half of the complete Books write transport: exactly four actions."""
    if action not in POST_ACTIONS:
        raise BankingToolError("REFUSED: banking POST action is not allowlisted.")
    transaction_id = positive_id(transaction_id, "transaction_id")
    if not isinstance(payload, dict):
        raise BankingToolError("REFUSED: banking POST payload must be one object.")
    if action == "match":
        closed_fields(payload, MATCH_PAYLOAD_FIELDS, "match POST payload")
        canonical_targets = validate_match_targets(payload["transactions_to_be_matched"])
        if canonical_targets != payload["transactions_to_be_matched"]:
            raise BankingToolError("REFUSED: match POST payload is not canonical.")
    elif action == "categorize":
        allowed_fields(payload, CATEGORIZE_PAYLOAD_FIELDS, "categorize POST payload")
        if not CATEGORIZE_REQUIRED_FIELDS.issubset(payload):
            raise BankingToolError("REFUSED: categorize POST payload lacks required fields.")
        clean_input = validate_categorize_input_payload({
            key: value for key, value in payload.items() if key not in {"amount", "date"}
        })
        canonical_payload = {
            **clean_input,
            "amount": float(decimal_value(payload["amount"], "payload.amount")),
            "date": date_text(payload["date"], "payload.date"),
        }
        if payload != canonical_payload:
            raise BankingToolError("REFUSED: categorize POST payload is not canonical.")
    else:
        closed_fields(payload, set(), f"{action} POST payload")
    path = POST_PATHS[action].format(id=transaction_id)
    query = urlencode({"organization_id": positive_id(organization_id, "organization_id")})
    request = Request(
        api_domain.rstrip("/") + path + "?" + query,
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
        raise BankingToolError(f"Zoho banking {action} failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise BankingToolError(f"Zoho banking {action} outcome is indeterminate: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise BankingToolError(f"Zoho banking {action} returned invalid JSON; outcome is indeterminate.") from exc
    if not isinstance(result, dict) or result.get("code") not in (None, 0):
        message = result.get("message") if isinstance(result, dict) else "invalid response"
        raise BankingToolError(f"Zoho banking {action} failed: {message or result.get('code')}")
    return result


def api_put_transfer_accounts_allowed(
    access_token: str,
    api_domain: str,
    transaction_id: str,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """The sole PUT transport: update account links on one existing transfer."""
    if not isinstance(payload, dict):
        raise BankingToolError("REFUSED: transfer-account PUT payload must be one object.")
    allowed_fields(payload, UPDATE_TRANSFER_PAYLOAD_FIELDS, "transfer-account PUT payload")
    missing = sorted(UPDATE_TRANSFER_REQUIRED_FIELDS - set(payload))
    if missing:
        raise BankingToolError("REFUSED: transfer-account PUT payload is missing: " + ", ".join(missing))
    from_id = positive_id(payload["from_account_id"], "payload.from_account_id")
    to_id = positive_id(payload["to_account_id"], "payload.to_account_id")
    if from_id == to_id:
        raise BankingToolError("REFUSED: transfer-account PUT from/to IDs must differ.")
    if payload["transaction_type"] != "transfer_fund":
        raise BankingToolError("REFUSED: transfer-account PUT transaction_type must be exactly transfer_fund.")
    canonical_payload: dict[str, Any] = {
        "from_account_id": from_id,
        "to_account_id": to_id,
        "transaction_type": "transfer_fund",
        "amount": float(decimal_value(payload["amount"], "payload.amount")),
        "date": date_text(payload["date"], "payload.date"),
    }
    if "exchange_rate" in payload:
        rate = decimal_value(payload["exchange_rate"], "payload.exchange_rate")
        if rate <= 0:
            raise BankingToolError("REFUSED: transfer-account PUT exchange_rate must be positive.")
        canonical_payload["exchange_rate"] = float(rate)
    for field in ("reference_number", "description", "currency_id"):
        if field in payload:
            canonical_payload[field] = clean_text(
                payload[field], f"payload.{field}", 2000, required=False
            )
    if payload != canonical_payload:
        raise BankingToolError("REFUSED: transfer-account PUT payload is not canonical.")
    transaction_id = positive_id(transaction_id, "bank_transaction_id")
    query = urlencode({"organization_id": positive_id(organization_id, "organization_id")})
    path = f"/books/v3/banktransactions/{transaction_id}?{query}"
    request = Request(
        api_domain.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise BankingToolError(
            f"Zoho banking update_transfer_accounts failed with HTTP {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise BankingToolError(
            f"Zoho banking update_transfer_accounts outcome is indeterminate: {exc.reason}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise BankingToolError(
            "Zoho banking update_transfer_accounts returned invalid JSON; outcome is indeterminate."
        ) from exc
    if not isinstance(result, dict) or result.get("code") not in (None, 0):
        message = result.get("message") if isinstance(result, dict) else "invalid response"
        code = result.get("code") if isinstance(result, dict) else "invalid response"
        raise BankingToolError(
            f"Zoho banking update_transfer_accounts failed: {message or code}"
        )
    return result


def require_snapshot_unchanged(current: dict[str, Any], snapshot: dict[str, Any]) -> None:
    state = json_copy(current)
    if not secrets.compare_digest(digest_for(state), snapshot["current_state_sha256"]):
        raise BankingToolError(
            f"{snapshot['record_type'].title()} target/source {snapshot['requested_id']} changed after review."
        )
    if state != snapshot["current_state"]:
        raise BankingToolError(f"Record {snapshot['requested_id']} exact before-state evidence does not match.")
    if snapshot["record_type"] == "transaction":
        if transaction_before(state, snapshot["requested_id"]) != snapshot["before_state"]:
            raise BankingToolError(f"Transaction {snapshot['requested_id']} identity/amount/date/status changed.")
    elif account_before(state, snapshot["requested_id"]) != snapshot["before_state"]:
        raise BankingToolError(f"Account {snapshot['requested_id']} identity/currency/status changed.")


def refetch_snapshot(access_token: str, vault: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot["record_type"] == "transaction":
        if (
            snapshot["role"] == "match_target"
            and snapshot["before_state"]["transaction_type"] == "invoice"
        ):
            source_id = positive_id(
                snapshot["current_state"].get("match_source_transaction_id"),
                "match target source_transaction_id",
            )
            current = get_match_target(
                access_token,
                vault,
                source_id,
                snapshot["requested_id"],
                "invoice",
            )
        else:
            current = get_bank_transaction(
                access_token, vault, snapshot["requested_id"], snapshot["get_mode"]
            )
    else:
        current = get_account(access_token, vault, snapshot["requested_id"])
    require_snapshot_unchanged(current, snapshot)
    if snapshot["record_type"] == "transaction":
        return transaction_snapshot(current, snapshot["requested_id"], snapshot["role"], snapshot["get_mode"])
    return account_snapshot(current, snapshot["requested_id"], snapshot["role"])


def verify_invoice_match_readback(
    plan: dict[str, Any], access_token: str, vault: dict[str, Any]
) -> dict[str, str]:
    source = plan["source_snapshot"]
    source_before = source["before_state"]
    source_id = source["requested_id"]
    targets = [
        row for row in plan["target_snapshots"] if row["role"] == "match_target"
    ]
    if not targets or any(
        row["before_state"]["transaction_type"] != "invoice" for row in targets
    ):
        raise BankingToolError("Invoice match readback requires only protected invoice targets.")
    if any(
        row["transaction_id"] == source_id
        for row in list_uncategorized_ui_transactions(vault)
    ):
        raise BankingToolError("Live readback still shows the matched source in the uncategorized feed.")

    organization_id = positive_id(vault["books_organization_id"], "organization_id")
    expected_allocations = {
        row["requested_id"]: decimal_value(row["before_state"]["amount"], "approved invoice amount")
        for row in targets
    }
    source_amount = abs(decimal_value(source_before["amount"], "approved source amount"))
    source_date = source_before["date"]
    source_account_id = source_before["account_id"]
    candidates: list[dict[str, Any]] = []
    for page_number in range(1, 51):
        query = urlencode({
            "organization_id": organization_id,
            "date_start": source_date,
            "date_end": source_date,
            "page": page_number,
            "per_page": 200,
        })
        result = zoho_tool.api_get(
            access_token,
            str(vault["api_domain"]),
            f"/books/v3/customerpayments?{query}",
        )
        rows = result.get("customerpayments")
        if not isinstance(rows, list):
            raise BankingToolError("Zoho did not return customer payments for match readback.")
        candidates.extend(
            row for row in rows
            if isinstance(row, dict)
            and str(row.get("account_id") or "").strip() == source_account_id
            and date_text(row.get("date"), "live payment date") == source_date
            and decimal_value(row.get("amount"), "live payment amount") == source_amount
        )
        if not zoho_tool.require_has_more_page(
            result, "/books/v3/customerpayments", page_number, BankingToolError
        ):
            break
    else:
        raise BankingToolError("Customer-payment readback exceeded the 50-page safety limit.")

    verified_payments: list[dict[str, Any]] = []
    for candidate in candidates:
        payment_id = positive_id(candidate.get("payment_id"), "live payment_id")
        query = urlencode({"organization_id": organization_id})
        result = zoho_tool.api_get(
            access_token,
            str(vault["api_domain"]),
            f"/books/v3/customerpayments/{payment_id}?{query}",
        )
        payment = result.get("payment") or result.get("customerpayment")
        if not isinstance(payment, dict):
            raise BankingToolError(f"Zoho did not return customer payment {payment_id}.")
        allocations = payment.get("invoices")
        if not isinstance(allocations, list):
            raise BankingToolError(f"Customer payment {payment_id} omitted invoice allocations.")
        actual_allocations: dict[str, Decimal] = {}
        for allocation in allocations:
            if not isinstance(allocation, dict):
                raise BankingToolError("Customer payment returned an invalid invoice allocation.")
            invoice_id = positive_id(allocation.get("invoice_id"), "payment allocation invoice_id")
            if invoice_id in actual_allocations:
                raise BankingToolError("Customer payment returned a duplicate invoice allocation.")
            actual_allocations[invoice_id] = decimal_value(
                allocation.get("amount_applied"), "payment amount_applied"
            )
        if actual_allocations != expected_allocations:
            continue
        if positive_id(payment.get("account_id"), "live payment account_id") != source_account_id:
            continue
        if date_text(payment.get("date"), "live payment date") != source_date:
            continue
        if decimal_value(payment.get("amount"), "live payment amount") != source_amount:
            continue
        if decimal_value(payment.get("unused_amount"), "live payment unused_amount") != 0:
            continue
        source_description = str(source["current_state"].get("description") or "").strip()
        payment_description = str(payment.get("description") or "").strip()
        if source_description and payment_description != source_description:
            continue
        verified_payments.append(payment)
    if len(verified_payments) != 1:
        raise BankingToolError("Live readback did not find exactly one payment with the approved invoice allocations.")

    payment = verified_payments[0]
    for target in targets:
        target_id = target["requested_id"]
        query = urlencode({"organization_id": organization_id})
        result = zoho_tool.api_get(
            access_token,
            str(vault["api_domain"]),
            f"/books/v3/invoices/{target_id}?{query}",
        )
        invoice = result.get("invoice")
        if not isinstance(invoice, dict):
            raise BankingToolError(f"Zoho did not return matched invoice {target_id}.")
        before = target["before_state"]
        protected = {
            "invoice_id": positive_id(invoice.get("invoice_id"), "live invoice_id"),
            "customer_id": positive_id(invoice.get("customer_id"), "live invoice customer_id"),
            "customer_name": str(invoice.get("customer_name") or "").strip(),
            "date": date_text(invoice.get("date"), "live invoice date"),
            "currency": clean_text(invoice.get("currency_code"), "live invoice currency", 64),
            "reference": str(invoice.get("reference_number") or "").strip(),
            "invoice_number": str(invoice.get("invoice_number") or "").strip(),
        }
        expected = {
            "invoice_id": target_id,
            "customer_id": before["account_id"],
            "customer_name": before["account_name"],
            "date": before["date"],
            "currency": before["currency"],
            "reference": before["reference"],
            "invoice_number": str(target["current_state"].get("transaction_number") or "").strip(),
        }
        if protected != expected:
            raise BankingToolError(f"Matched invoice {target_id} changed protected identity fields.")
        if normalized_status(invoice.get("status")) != "paid":
            raise BankingToolError(f"Matched invoice {target_id} is not paid.")
        if decimal_value(invoice.get("balance"), "live matched invoice balance") != 0:
            raise BankingToolError(f"Matched invoice {target_id} does not have zero balance.")
    return {
        "status": "matched",
        "payment_id": positive_id(payment.get("payment_id"), "verified payment_id"),
    }


def categorized_result_id(post_result: Any) -> str:
    """The transaction ID Zoho's categorize response reports, or "" if unreadable.

    A categorize CONSUMES the imported feed line and returns a DIFFERENT record:
    2026-08-11, line 96274000001423074 became transfer 96274000001558075 and the
    old ID stopped resolving. Reading the response is the only way to learn the
    new ID, so a readback of the staged source ID can never confirm success.
    Ambiguity is not resolved by guessing - two differing IDs return "".
    """
    if not isinstance(post_result, dict):
        return ""
    candidates: set[str] = set()
    rows = [post_result] + [
        post_result[name] for name in CATEGORIZE_RESULT_CONTAINERS
        if isinstance(post_result.get(name), dict)
    ]
    for row in rows:
        value = str(first_value(row, ("transaction_id", "bank_transaction_id"), "")).strip()
        if POSITIVE_ID_RE.fullmatch(value):
            candidates.add(value)
    return candidates.pop() if len(candidates) == 1 else ""


def get_categorized_result(
    access_token: str, vault: dict[str, Any], transaction_id: str
) -> dict[str, Any]:
    """Authoritative read of the record a categorize produced.

    Deliberately NOT get_bank_transaction: that falls back to the uncategorized
    feed on 404, which both drags in the authenticated UI session and would read
    the very state a successful categorize removes.
    """
    transaction_id = positive_id(transaction_id, "resulting transaction_id")
    query = urlencode({
        "organization_id": positive_id(vault["books_organization_id"], "organization_id"),
    })
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/banktransactions/{transaction_id}?{query}",
    )
    for key in CATEGORIZE_RESULT_CONTAINERS:
        row = result.get(key)
        if isinstance(row, dict):
            return json_copy(row)
    raise BankingToolError(f"Zoho did not return resulting bank transaction {transaction_id}.")


def verify_categorize_result(
    plan: dict[str, Any],
    access_token: str,
    vault: dict[str, Any],
    post_result: dict[str, Any],
) -> dict[str, str]:
    """Verify the record the categorize produced, not the line it consumed."""
    source_id = plan["source_snapshot"]["requested_id"]
    resulting_id = categorized_result_id(post_result)
    if not resulting_id:
        raise BankingToolError(
            "Zoho accepted the categorize but its response carried no single resulting "
            f"transaction ID, so the outcome for imported line {source_id} is unverified "
            "here. Reconcile the live transfer before staging anything else."
        )
    current = get_categorized_result(access_token, vault, resulting_id)
    payload = plan["payload"]
    before = plan["source_snapshot"]["before_state"]
    actual_type = str(first_value(current, ("transaction_type",), "")).strip()
    if actual_type != payload["transaction_type"]:
        raise BankingToolError(
            f"Resulting transaction {resulting_id} is {actual_type!r}, not the approved "
            f"{payload['transaction_type']!r}."
        )
    checks: dict[str, tuple[Any, Any]] = {
        "amount": (
            decimal_value(first_value(current, ("amount",)), "resulting amount"),
            decimal_value(before["amount"], "approved source amount"),
        ),
        "date": (
            date_text(first_value(current, ("date",)), "resulting date"),
            before["date"],
        ),
        "currency": (
            str(first_value(current, ("currency_code", "currency"), "")).upper(),
            before["currency"].upper(),
        ),
    }
    mismatched = [label for label, (actual, want) in checks.items() if actual != want]
    if mismatched:
        raise BankingToolError(
            f"Resulting transaction {resulting_id} does not preserve the approved "
            + ", ".join(mismatched)
            + "."
        )
    values = collect_scalar_strings(current)
    for field in ("from_account_id", "to_account_id"):
        if payload[field] not in values:
            raise BankingToolError(
                f"Resulting transaction {resulting_id} does not show approved {field}."
            )
    for field in ("reference_number", "description"):
        if field in payload and str(current.get(field, "")) != payload[field]:
            raise BankingToolError(
                f"Resulting transaction {resulting_id} did not preserve approved {field}."
            )
    return {
        "status": actual_type,
        "resulting_transaction_id": resulting_id,
        "consumed_imported_line": source_id,
    }


def verify_readback(plan: dict[str, Any], current: dict[str, Any]) -> dict[str, str]:
    source_id = plan["source_snapshot"]["requested_id"]
    action = plan["action"]
    if action == "update_transfer_accounts":
        before_transfer = live_transfer_details(plan["source_snapshot"]["current_state"], source_id)
        after_transfer = live_transfer_details(current, source_id)
        protected_fields = ["transaction_id", "transaction_type", "amount", "date"]
        if source_id != "96274000001535012":
            protected_fields.extend(["currency", "exchange_rate"])
        for field in protected_fields:
            if after_transfer[field] != before_transfer[field]:
                raise BankingToolError(f"Live readback changed protected transfer field {field}.")
        if source_id == "96274000001535012":
            if after_transfer["currency"].upper() != "USD":
                raise BankingToolError("Live USD Airwallex readback did not derive currency USD.")
            if decimal_value(after_transfer["exchange_rate"], "live USD Airwallex exchange_rate") != 1:
                raise BankingToolError("Live USD Airwallex readback did not derive exchange_rate 1.")
        payload = plan["payload"]
        if after_transfer["from_account_id"] != payload["from_account_id"]:
            raise BankingToolError("Live readback did not verify approved from_account_id.")
        if after_transfer["to_account_id"] != payload["to_account_id"]:
            raise BankingToolError("Live readback did not verify approved to_account_id.")
        for field in ("reference_number", "description", "currency_id"):
            if field in payload and str(current.get(field, "")) != payload[field]:
                raise BankingToolError(f"Live readback did not preserve approved {field}.")
        return after_transfer

    after = transaction_before(current, source_id)
    before = plan["source_snapshot"]["before_state"]
    for field in ("transaction_id", "account_id", "date", "amount", "currency"):
        if after[field] != before[field]:
            raise BankingToolError(f"Live readback changed protected source field {field}.")
    status = normalized_status(after["status"])
    accepted = {
        "match": {"matched", "manually_matched"},
        "categorize": {"categorized", "manually_categorized"},
        "unmatch": {"uncategorized", "unmatched"},
        "uncategorize": {"uncategorized", "unmatched"},
    }[action]
    if status not in accepted:
        raise BankingToolError(f"Live readback status {after['status']!r} did not verify {action}.")
    values = collect_scalar_strings(current)
    if action == "match":
        target_ids = [row["transaction_id"] for row in plan["payload"]["transactions_to_be_matched"]]
        if any(target_id not in values for target_id in target_ids):
            raise BankingToolError("Live readback did not show every approved matched transaction ID.")
    elif action == "categorize":
        payload = plan["payload"]
        if after["transaction_type"] != payload["transaction_type"]:
            raise BankingToolError("Live readback did not verify the approved categorize transaction_type.")
        if payload["from_account_id"] not in values or payload["to_account_id"] not in values:
            raise BankingToolError("Live readback did not verify the approved category account IDs.")
    return after


def command_commit(args: argparse.Namespace, expected_action: str) -> None:
    plan_path = contained_plan(args.plan)
    plan = load_plan(plan_path, expected_action)
    require_rachad_approval(args.approval)
    lock = lock_path(plan["sha256"])
    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": expected_action,
        "status": "reserved_before_network_write",
        "started_utc": utc_now().isoformat(),
    }, exclusive=True)
    write_attempted = False
    post_result: dict[str, Any] | None = None
    try:
        vault = zoho_tool.load_vault()
        scopes = [str(scope) for scope in vault.get("scopes") or []]
        zoho_tool.validate_scopes(scopes)
        required_scopes = (
            (READ_SCOPE, UPDATE_SCOPE)
            if expected_action == "update_transfer_accounts"
            else (READ_SCOPE, CREATE_SCOPE)
        )
        for required_scope in required_scopes:
            if required_scope not in scopes:
                raise BankingToolError(f"Saved Zoho connection lacks {required_scope}.")
        if str(vault.get("books_organization_id") or "") != plan["organization"]["organization_id"]:
            raise BankingToolError("Saved Books organization ID differs from the staged FRP Depot organization lock.")
        access_token, vault = zoho_tool.refresh_access_token(vault)
        if get_frp_organization(access_token, vault) != plan["organization"]:
            raise BankingToolError("Live FRP Depot organization changed after review.")
        current_source = get_bank_transaction(
            access_token, vault, plan["source_snapshot"]["requested_id"],
            plan["source_snapshot"]["get_mode"],
        )
        require_snapshot_unchanged(current_source, plan["source_snapshot"])
        refreshed_targets = [
            refetch_snapshot(access_token, vault, snapshot) for snapshot in plan["target_snapshots"]
        ]
        current_source_snapshot = transaction_snapshot(
            current_source, plan["source_snapshot"]["requested_id"], "source",
            plan["source_snapshot"]["get_mode"],
        )
        if expected_action == "update_transfer_accounts":
            require_general_update_currency(plan["payload"], refreshed_targets)
            refreshed_mapping = validate_airwallex_account_update(
                current_source_snapshot, plan["payload"], refreshed_targets, plan["sources"]
            )
            if refreshed_mapping != plan["evidence"]["airwallex_mapping"]:
                raise BankingToolError("Transfer account mapping evidence changed after review.")
        elif plan["evidence"]["airwallex_recovery"] is not None:
            recovery_evidence = plan["evidence"]["airwallex_recovery"]
            require_airwallex_usd_recovery_facts(
                current_source_snapshot, plan["payload"], refreshed_targets
            )
            refreshed_historical = verify_recovery_historical_plan(
                recovery_evidence["historical_plan"]
            )
            if refreshed_historical != {
                "historical_plan": recovery_evidence["historical_plan"],
                "historical_plan_sha256": recovery_evidence["historical_plan_sha256"],
                "historical_plan_lock_status": recovery_evidence["historical_plan_lock_status"],
            }:
                raise BankingToolError("Recovery historical evidence changed after review.")
            prove_superseded_transfer_absent(access_token, vault)
        elif plan["evidence"]["airwallex_guarded"]:
            outgoing_id = plan["evidence"]["airwallex_outgoing_transaction_id"]
            outgoing = next(
                row for row in refreshed_targets
                if row["record_type"] == "transaction" and row["requested_id"] == outgoing_id
            )
            if airwallex_verification(current_source_snapshot, outgoing) != plan["evidence"]["airwallex_verification"]:
                raise BankingToolError("Airwallex outgoing compatibility changed after review.")
        write_attempted = True
        if expected_action == "update_transfer_accounts":
            api_put_transfer_accounts_allowed(
                access_token,
                str(vault["api_domain"]),
                plan["source_snapshot"]["requested_id"],
                plan["organization"]["organization_id"],
                plan["payload"],
            )
        else:
            post_result = api_post_allowed(
                access_token,
                str(vault["api_domain"]),
                expected_action,
                plan["source_snapshot"]["requested_id"],
                plan["organization"]["organization_id"],
                plan["payload"],
            )
        invoice_only_match = (
            expected_action == "match"
            and all(
                row["transaction_type"] == "invoice"
                for row in plan["payload"]["transactions_to_be_matched"]
            )
        )
        if invoice_only_match:
            verified = verify_invoice_match_readback(plan, access_token, vault)
        elif expected_action == "categorize":
            verified = verify_categorize_result(plan, access_token, vault, post_result or {})
        else:
            readback = get_bank_transaction(
                access_token, vault, plan["source_snapshot"]["requested_id"],
                AFTER_SOURCE_MODES[expected_action],
            )
            verified = verify_readback(plan, readback)
        zoho_tool.save_vault(vault)
    except Exception as exc:
        status = "indeterminate" if write_attempted else "aborted_before_write"
        record = {
            "plan_sha256": plan["sha256"],
            "action": expected_action,
            "status": status,
            "updated_utc": utc_now().isoformat(),
            "reason": str(exc)[:2000],
            "no_retry": True,
        }
        # An indeterminate that Zoho ACCEPTED is a different reconcile from one it
        # rejected, so say which - and name the record it created when it can be
        # read, because that ID is what a live reconcile has to look for.
        if post_result is not None:
            record["zoho_accepted_the_write"] = True
            resulting_id = categorized_result_id(post_result)
            if resulting_id:
                record["resulting_transaction_id"] = resulting_id
        write_lock(lock, record)
        zoho_tool.append_receipt(
            "zoho_banking_commit_failed_permanently_locked",
            f"action={expected_action}; status={status}; plan={plan_path}; sha256={plan['sha256']}",
        )
        raise BankingToolError(
            f"Banking {expected_action} commit is {status} and permanently locked against replay. "
            "Reconcile live Zoho state before staging another plan: " + str(exc)
        ) from exc
    success = {
        "plan_sha256": plan["sha256"],
        "action": expected_action,
        "status": "committed_verified",
        "source_transaction_id": plan["source_snapshot"]["requested_id"],
        "verified_status": verified["status"],
        "updated_utc": utc_now().isoformat(),
        "no_retry": True,
    }
    if "resulting_transaction_id" in verified:
        success["resulting_transaction_id"] = verified["resulting_transaction_id"]
    write_lock(lock, success)
    zoho_tool.append_receipt(
        "zoho_banking_reconciliation_committed_verified",
        f"action={expected_action}; transaction_id={plan['source_snapshot']['requested_id']}; "
        f"plan={plan_path}; sha256={plan['sha256']}",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "action": expected_action,
        "source_transaction_id": plan["source_snapshot"]["requested_id"],
        "verified_status": verified["status"],
        "plan_sha256": plan["sha256"],
        "replay_locked": True,
    }, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    cli_actions = (
        ("match", "match"),
        ("categorize", "categorize"),
        ("unmatch", "unmatch"),
        ("uncategorize", "uncategorize"),
        ("update_transfer_accounts", "update-transfer-accounts"),
    )
    for action, cli_action in cli_actions:
        stage = commands.add_parser(f"stage-{cli_action}")
        stage.add_argument("--input", required=True)
        stage.set_defaults(func=lambda args, selected=action: command_stage(args, selected))
        commit = commands.add_parser(f"commit-{cli_action}")
        commit.add_argument("--plan", required=True)
        commit.add_argument("--approval", required=True)
        commit.set_defaults(func=lambda args, selected=action: command_commit(args, selected))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (BankingToolError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
