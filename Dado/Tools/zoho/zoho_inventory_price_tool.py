#!/usr/bin/env python
"""FRP Depot Zoho Inventory FNPT sales-rate tool.

Commissioned by Rachad Homsi on 2026-08-10. Building, testing, or staging with
this module is NOT approval of a business write.

The only service write reachable through this named tool is one OAuth
``PUT /inventory/v1/items/{positive item_id}`` per staged line, whose payload
contains exactly the preserved unchanged item name and the new sales ``rate``,
for an existing Zoho Inventory item whose live SKU begins with exactly one of:

* ``FNPTCOUPLING-DERAKANE411-``
* ``FNPTCOUPLING-DERAKANE470-``

The target is fixed by one business rule:
``target CAD sales rate = supplier USD unit cost x 3.6`` (decimal half-up, two
decimals). The 3.6 factor covers currency conversion, shipping, handling and
margin.

There is no generic write function. Purchase rates and costs, stock, warehouses
and adjustments, names, SKUs, descriptions, taxes, accounts, units, item type,
status, item creation or deletion, invoices, estimates, orders, bills,
contacts, batch endpoints and WooCommerce are all unreachable and are rejected
by the input schema, the plan validator, the write transport and the read-back.

Writes are sequential and NOT atomic: each line is an independent PUT.

AUTONOMY PROGRAMME, 2026-08-21 (Rachad: "Go on all of them"; spec A2/A4/A6).
Sales rates are REVERSIBLE work, so this tool now follows the shared owner
authority in ``Dado/Tools/common/owner_authority.py``:
* his clear go in his own message covers the whole batch -- exact ``APPROVED``
  still works but any plain "yes" / "go ahead" / "do it" counts;
* a failed line is recorded and the remaining lines still run; the plan then
  reads "needs re-stage" and ``<plan>.restage-input.json`` carries only the
  failed or untouched lines, so only those are re-staged and applied again;
* before each PUT the live rate is captured beside the plan
  (``<plan>.live-before.json``) and ``restore --plan`` puts it back;
* nothing is permanently locked. A committed-and-verified plan is SPENT
  (one plan, one write), which is correctness, not a lock on failure.
The 2026-08-10 commission above (scope, SKU prefixes, x 3.6 rule, name+rate
payload, read-back) is unchanged.
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

# Shared owner authority (2026-08-21). Appending keeps the stdlib ahead of it.
sys.path.append(str(Path(__file__).resolve().parent.parent / "common"))
import owner_authority  # noqa: E402

TOOL_NAME = "FRP Depot Zoho Inventory FNPT Sales Rate Tool"
SCHEMA_VERSION = 1
ACTION = "fnpt_sales_rate_update"
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_price_plans"
PLAN_LIFETIME_HOURS = 24
# Rachad's ruling (2026-07-26): his approval is ONE PLAIN WORD answering the
# displayed plan, never a checksum. Until 2026-08-21 this tool compared the
# word byte-exact; the autonomy programme (spec A1) made any clear affirmative
# in his own message count for this reversible work. APPROVED still works and
# is still the word the plan names.
APPROVAL_WORD = owner_authority.EXACT_WORD
UPDATE_SCOPE = "ZohoInventory.items.UPDATE"
ITEM_PATH_RE = re.compile(r"^/inventory/v1/items/([1-9][0-9]*)$")
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")

ALLOWED_SKU_PREFIXES = (
    "FNPTCOUPLING-DERAKANE411-",
    "FNPTCOUPLING-DERAKANE470-",
)
MULTIPLIER_TEXT = "3.6"
MULTIPLIER = Decimal(MULTIPLIER_TEXT)
TARGET_CURRENCY = "CAD"
CENT = Decimal("0.01")
MAX_LINES = 40
MAX_RATE = Decimal("100000.00")

COST_RE = re.compile(r"^(0|[1-9][0-9]{0,6})(\.[0-9]{1,4})?$")
TARGET_RE = re.compile(r"^(0|[1-9][0-9]{0,6})\.[0-9]{2}$")
SHEET_RE = re.compile(r"^[A-Za-z0-9 _\-.]{1,64}$")
CELL_RE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")

INPUT_FIELDS = {"target_currency", "lines"}
LINE_FIELDS = {
    "item_id", "sku", "supplier_cost_usd", "multiplier", "target_rate_cad",
    "source_workbook", "source_sheet", "source_cell",
}
PLAN_FIELDS = {
    "schema_version", "tool", "action", "created_utc", "expires_utc", "nonce",
    "approval_required", "origin", "organization", "payload", "risk",
    "live_evidence", "sha256",
}
ORIGIN_FIELDS = {"tool_path", "repo_root", "plan_dir"}
ORGANIZATION_FIELDS = {"organization_id", "name", "currency_code"}
RISK_FIELDS = {"atomic", "sequential_writes", "note"}
ITEM_EVIDENCE_FIELDS = {
    "item_id", "sku", "name", "supplier_cost_usd", "multiplier",
    "target_rate_cad", "before_rate", "direction", "operation", "endpoint",
    "source_workbook", "source_sheet", "source_cell",
    "before_state", "before_state_sha256",
    "protected_state", "protected_state_sha256", "rate_family_before",
}

# Fields Zoho itself recomputes from the sales rate. They are excluded from the
# byte-exact protected fingerprint because a correct rate change is REQUIRED to
# move them; each one is verified separately by explicit rule below, so nothing
# here is left unchecked.
RATE_FAMILY_FIELDS = (
    "rate",                    # the commissioned target field
    "sales_rate",              # Zoho mirror of rate
    "pricebook_rate",          # Zoho mirror of rate under the unit scheme
    "default_price_brackets",  # carries pricebook_rate per bracket
    "sales_margin",            # display margin derived from rate vs cost
)
# Volatile server write metadata: it changes on every save by definition.
VOLATILE_FIELDS = ("last_modified_time",)
UNPROTECTED_FIELDS = frozenset(RATE_FAMILY_FIELDS + VOLATILE_FIELDS)

RISK_NOTE = (
    "NOT ATOMIC. Each line is an independent sequential PUT. A line that fails "
    "or cannot be verified is recorded and the remaining lines still run; "
    "verified writes remain in Zoho, the live rate of every written line is "
    "captured beside the plan for restore, and only the failed or untouched "
    "lines are re-staged. Nothing stays locked."
)


class PriceToolError(RuntimeError):
    """A fail-closed validation, precondition, transport, or read-back error."""


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
        raise PriceToolError("Zoho returned evidence that is not JSON serializable.") from exc


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PriceToolError(f"{label} JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise PriceToolError(f"{label} JSON must contain exactly one object.")
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
    raise PriceToolError(f"{label} must use the exact closed schema ({'; '.join(details)}).")


def clean_text(value: Any, label: str, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise PriceToolError(f"{label} must be text.")
    if value != value.strip():
        raise PriceToolError(f"{label} must not have surrounding whitespace.")
    if not value:
        raise PriceToolError(f"{label} cannot be blank.")
    if len(value) > maximum:
        raise PriceToolError(f"{label} exceeds the {maximum}-character safety limit.")
    if any(ord(character) < 32 for character in value):
        raise PriceToolError(f"{label} contains control characters.")
    return value


def positive_id(value: Any, label: str) -> str:
    if isinstance(value, bool):
        raise PriceToolError(f"{label} must be a positive Zoho ID.")
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise PriceToolError(f"{label} must be a positive Zoho ID.")
    return text


def money_text(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def decimal_from(value: Any, label: str, pattern: re.Pattern[str]) -> Decimal:
    """Money is carried as canonical decimal TEXT; floats never enter the math."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise PriceToolError(f"{label} must be canonical decimal text, not a number or other type.")
    if not pattern.fullmatch(value):
        raise PriceToolError(f"{label} is not canonical positive decimal text: {value!r}")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise PriceToolError(f"{label} is not a valid decimal.") from exc
    if result <= 0:
        raise PriceToolError(f"{label} must be greater than zero.")
    return result


def compute_target(cost: Decimal) -> Decimal:
    """The one commissioned business rule, in exact decimal half-up arithmetic."""
    return (cost * MULTIPLIER).quantize(CENT, rounding=ROUND_HALF_UP)


def live_rate(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise PriceToolError(f"{label} is missing or not a number.")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise PriceToolError(f"{label} is not a valid number: {value!r}") from exc
    if not result.is_finite() or result < 0:
        raise PriceToolError(f"{label} must be a finite non-negative number.")
    if result > MAX_RATE:
        raise PriceToolError(f"{label} exceeds the {MAX_RATE} safety ceiling.")
    return result


def allowed_sku(sku: str) -> str:
    if not any(sku.startswith(prefix) for prefix in ALLOWED_SKU_PREFIXES):
        raise PriceToolError(
            "REFUSED: this tool only changes sales rates on existing D411/D470 FNPT "
            "coupling items. SKU must begin with exactly one of: "
            + ", ".join(ALLOWED_SKU_PREFIXES)
            + f" (got {sku!r})."
        )
    return sku


def validate_line(raw: Any, index: int) -> dict[str, str]:
    label = f"lines[{index}]"
    if not isinstance(raw, dict):
        raise PriceToolError(f"{label} must be an object.")
    closed_fields(raw, LINE_FIELDS, label)
    item_id = positive_id(raw["item_id"], f"{label}.item_id")
    if raw["item_id"] != item_id:
        raise PriceToolError(f"{label}.item_id must be canonical positive-ID text.")
    sku = allowed_sku(clean_text(raw["sku"], f"{label}.sku", 200))
    cost = decimal_from(raw["supplier_cost_usd"], f"{label}.supplier_cost_usd", COST_RE)
    if raw["multiplier"] != MULTIPLIER_TEXT:
        raise PriceToolError(
            f"REFUSED: {label}.multiplier is fixed at exactly {MULTIPLIER_TEXT} "
            f"(got {raw['multiplier']!r})."
        )
    target = decimal_from(raw["target_rate_cad"], f"{label}.target_rate_cad", TARGET_RE)
    expected = compute_target(cost)
    if target != expected or raw["target_rate_cad"] != money_text(expected):
        raise PriceToolError(
            f"REFUSED: {label}.target_rate_cad must equal supplier_cost_usd x "
            f"{MULTIPLIER_TEXT} rounded half-up to two decimals "
            f"({money_text(expected)}), not {raw['target_rate_cad']!r}."
        )
    if target > MAX_RATE:
        raise PriceToolError(f"{label}.target_rate_cad exceeds the {MAX_RATE} safety ceiling.")
    workbook = clean_text(raw["source_workbook"], f"{label}.source_workbook", 400)
    if not workbook.casefold().endswith((".xlsx", ".xlsm", ".xls")):
        raise PriceToolError(f"{label}.source_workbook must name a source workbook file.")
    sheet = clean_text(raw["source_sheet"], f"{label}.source_sheet", 64)
    if not SHEET_RE.fullmatch(sheet):
        raise PriceToolError(f"{label}.source_sheet is malformed: {sheet!r}")
    cell = clean_text(raw["source_cell"], f"{label}.source_cell", 16)
    if not CELL_RE.fullmatch(cell):
        raise PriceToolError(
            f"{label}.source_cell must be an A1-style workbook cell reference (got {cell!r})."
        )
    return {
        "item_id": item_id,
        "sku": sku,
        "supplier_cost_usd": raw["supplier_cost_usd"],
        "multiplier": MULTIPLIER_TEXT,
        "target_rate_cad": raw["target_rate_cad"],
        "source_workbook": workbook,
        "source_sheet": sheet,
        "source_cell": cell,
    }


def validate_input(raw: dict[str, Any]) -> dict[str, Any]:
    closed_fields(raw, INPUT_FIELDS, "Stage input")
    if raw["target_currency"] != TARGET_CURRENCY:
        raise PriceToolError(
            f"REFUSED: target_currency must be exactly {TARGET_CURRENCY} "
            f"(got {raw['target_currency']!r})."
        )
    lines = raw["lines"]
    if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES:
        raise PriceToolError(f"lines must be a list of 1-{MAX_LINES} FNPT sales-rate records.")
    clean = [validate_line(line, index) for index, line in enumerate(lines)]
    item_ids = [line["item_id"] for line in clean]
    if len(set(item_ids)) != len(item_ids):
        raise PriceToolError("REFUSED: duplicate item_id in the requested lines.")
    skus = [line["sku"] for line in clean]
    if len(set(skus)) != len(skus):
        raise PriceToolError("REFUSED: duplicate sku in the requested lines.")
    return {"target_currency": TARGET_CURRENCY, "lines": clean}


def require_rachad_approval(approval: Any, lane: Any = None,
                            what: str = "this FNPT sales-rate plan") -> owner_authority.OwnerGo:
    """His clear go in his own message (spec A1). APPROVED still works.

    Building or staging is not approval, and Dado cannot supply it; the
    refusal wording and the lane rule live in the shared module.
    """
    try:
        return owner_authority.require_owner_go(approval, lane=lane, what=what)
    except owner_authority.OwnerAuthorityRefused as exc:
        raise PriceToolError(str(exc)) from exc


def contained_plan(raw_path: Any) -> Path:
    candidate = Path(str(raw_path if raw_path is not None else ""))
    if not candidate.is_absolute():
        raise PriceToolError("Plan must be an absolute path inside the exact price-plan folder.")
    try:
        lexical_root = PLAN_DIR.absolute()
        candidate.absolute().relative_to(lexical_root)
    except (OSError, ValueError) as exc:
        raise PriceToolError("Plan is outside the exact allowlisted price-plan folder.") from exc
    cursor = candidate.absolute()
    while True:
        if cursor.is_symlink():
            raise PriceToolError("Plan paths and parents must not be symlinks.")
        if cursor == lexical_root:
            break
        parent = cursor.parent
        if parent == cursor:
            raise PriceToolError("Plan is outside the exact allowlisted price-plan folder.")
        cursor = parent
    try:
        root = PLAN_DIR.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise PriceToolError("Plan does not resolve inside the exact price-plan folder.") from exc
    if (
        root not in resolved.parents
        or not resolved.is_file()
        or resolved.suffix.casefold() != ".json"
    ):
        raise PriceToolError("Plan is outside the exact allowlisted price-plan folder.")
    return resolved


def parse_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise PriceToolError(f"Plan {label} is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PriceToolError(f"Plan {label} must include a timezone.")
    return parsed


def origin_record() -> dict[str, str]:
    return {
        "tool_path": str(Path(__file__).resolve()),
        "repo_root": str(ROOT),
        "plan_dir": str(PLAN_DIR),
    }


def require_origin(origin: Any) -> None:
    if not isinstance(origin, dict):
        raise PriceToolError("Plan origin is invalid.")
    closed_fields(origin, ORIGIN_FIELDS, "Plan origin")
    if origin != origin_record():
        raise PriceToolError(
            "REFUSED: the plan was staged by a different tool file, repository root, "
            "or plan folder than the one running now."
        )


# --------------------------------------------------------------------------
# Live read-only Zoho reads
# --------------------------------------------------------------------------


def verified_organization(access_token: str, vault: dict[str, Any]) -> dict[str, str]:
    """Confirm the vault's Inventory organization really is FRP Depot, in CAD."""
    org_id = positive_id(vault.get("inventory_organization_id"), "inventory_organization_id")
    result = zoho_tool.api_get(access_token, str(vault["api_domain"]), "/inventory/v1/organizations")
    organizations = result.get("organizations")
    if not isinstance(organizations, list) or not organizations:
        raise PriceToolError("Zoho returned no Inventory organizations.")
    frp = zoho_tool.frp_organization(organizations)
    if positive_id(frp.get("organization_id"), "live organization_id") != org_id:
        raise PriceToolError(
            "REFUSED: the live FRP Depot Inventory organization does not match the vault."
        )
    currency = clean_text(frp.get("currency_code"), "organization currency_code", 8)
    if currency != TARGET_CURRENCY:
        raise PriceToolError(
            f"REFUSED: the FRP Depot Inventory organization is {currency}, not {TARGET_CURRENCY}."
        )
    name = clean_text(frp.get("name") or frp.get("organization_name"), "organization name", 200)
    return {"organization_id": org_id, "name": name, "currency_code": currency}


def get_item(access_token: str, vault: dict[str, Any], item_id: str) -> dict[str, Any]:
    item_id = positive_id(item_id, "item_id")
    query = urlencode({"organization_id": positive_id(
        vault.get("inventory_organization_id"), "inventory_organization_id")})
    result = zoho_tool.api_get(
        access_token, str(vault["api_domain"]), f"/inventory/v1/items/{item_id}?{query}"
    )
    item = result.get("item")
    if not isinstance(item, dict) or str(item.get("item_id") or "") != item_id:
        raise PriceToolError(f"Zoho item {item_id} was not found.")
    return json_copy(item)


# --------------------------------------------------------------------------
# Protected state
# --------------------------------------------------------------------------


def protected_state(item: dict[str, Any]) -> dict[str, Any]:
    """Every returned item field except the rate family and write metadata.

    The rate family (rate, sales_rate, pricebook_rate, default_price_brackets,
    sales_margin) is excluded ONLY because Zoho recomputes it from the very
    value this tool changes; each member is verified separately and explicitly
    by verify_rate_family. last_modified_time is excluded as volatile server
    write metadata. Everything else — including name, sku, status, purchase_rate,
    every stock figure, accounts, taxes, units, group links and custom fields —
    is fingerprinted byte-exact and must not move.
    """
    if not isinstance(item, dict):
        raise PriceToolError("Item state must be an object.")
    return {key: json_copy(value) for key, value in item.items() if key not in UNPROTECTED_FIELDS}


def rate_family_state(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise PriceToolError("Item state must be an object.")
    return {key: json_copy(item.get(key)) for key in RATE_FAMILY_FIELDS}


def _bracket_rates_ok(before: Any, after: Any, allowed: set[Decimal]) -> bool:
    """Brackets may only move their pricebook_rate, and only to an allowed value."""
    if before is None and after is None:
        return True
    if not isinstance(before, list) or not isinstance(after, list) or len(before) != len(after):
        return False
    for before_row, after_row in zip(before, after):
        if not isinstance(before_row, dict) or not isinstance(after_row, dict):
            return False
        if set(before_row) != set(after_row):
            return False
        for key in before_row:
            if key == "pricebook_rate":
                try:
                    value = live_rate(after_row[key], "bracket pricebook_rate")
                except PriceToolError:
                    return False
                if value.quantize(CENT, rounding=ROUND_HALF_UP) not in allowed:
                    return False
            elif before_row[key] != after_row[key]:
                return False
    return True


def verify_rate_family(after: dict[str, Any], before: dict[str, Any], target: Decimal) -> None:
    """rate must BE the target; its Zoho mirrors may only hold the old or new rate."""
    actual = live_rate(after.get("rate"), "read-back rate").quantize(CENT, rounding=ROUND_HALF_UP)
    if actual != target:
        raise PriceToolError(
            f"Live read-back rate is {money_text(actual)}, not the approved "
            f"{money_text(target)}."
        )
    before_rate = live_rate(before.get("rate"), "staged rate").quantize(CENT, rounding=ROUND_HALF_UP)
    allowed = {before_rate, target}
    for key in ("sales_rate", "pricebook_rate"):
        if after.get(key) is None and before.get(key) is None:
            continue
        mirror = live_rate(after.get(key), f"read-back {key}").quantize(CENT, rounding=ROUND_HALF_UP)
        if mirror not in allowed:
            raise PriceToolError(
                f"Zoho mirror field {key} became {money_text(mirror)}, which is neither the "
                f"previous rate nor the approved target. Stop and reconcile."
            )
    if not _bracket_rates_ok(before.get("default_price_brackets"),
                             after.get("default_price_brackets"), allowed):
        raise PriceToolError(
            "Zoho default_price_brackets changed beyond the recomputed unit price. "
            "Stop and reconcile."
        )


def verify_protected_unchanged(item: dict[str, Any], evidence: dict[str, Any]) -> None:
    current = protected_state(item)
    if current != evidence["protected_state"] or not secrets.compare_digest(
        digest_for(current), str(evidence["protected_state_sha256"])
    ):
        raise PriceToolError(
            f"Item {evidence['item_id']} changed outside the approved sales rate "
            "(name, SKU, status, cost, stock, accounts, taxes or custom fields). "
            "Stop and reconcile."
        )


# --------------------------------------------------------------------------
# Stage
# --------------------------------------------------------------------------


def item_evidence(item: dict[str, Any], line: dict[str, str]) -> dict[str, Any]:
    item_id = line["item_id"]
    live_id = positive_id(item.get("item_id"), "live item_id")
    if live_id != item_id:
        raise PriceToolError(f"Zoho returned item {live_id} for requested item {item_id}.")
    sku = clean_text(item.get("sku"), f"item {item_id} live sku", 200)
    if sku != line["sku"]:
        raise PriceToolError(
            f"REFUSED: item {item_id} live SKU is {sku!r}, not the requested {line['sku']!r}."
        )
    allowed_sku(sku)
    name = clean_text(item.get("name"), f"item {item_id} name", 500)
    status = clean_text(item.get("status"), f"item {item_id} status", 32)
    if status != "active":
        raise PriceToolError(f"REFUSED: item {item_id} is {status}, not an active existing item.")
    if item.get("can_be_sold") is not True:
        raise PriceToolError(f"REFUSED: item {item_id} is not sellable; a sales rate does not apply.")
    if item.get("is_combo_product") is True:
        raise PriceToolError(f"REFUSED: item {item_id} is a combo product; its rate is composed.")
    before_rate = live_rate(item.get("rate"), f"item {item_id} current rate")
    before_text = money_text(before_rate)
    target = Decimal(line["target_rate_cad"])
    if Decimal(before_text) == target:
        raise PriceToolError(
            f"REFUSED: item {item_id} ({sku}) is already at {before_text} {TARGET_CURRENCY}; "
            "nothing was staged."
        )
    direction = "INCREASE" if target > Decimal(before_text) else "DECREASE"
    state = json_copy(item)
    protected = protected_state(state)
    return {
        "item_id": item_id,
        "sku": sku,
        "name": name,
        "supplier_cost_usd": line["supplier_cost_usd"],
        "multiplier": MULTIPLIER_TEXT,
        "target_rate_cad": line["target_rate_cad"],
        "before_rate": before_text,
        "direction": direction,
        "operation": "sales_rate_update",
        "endpoint": f"PUT /inventory/v1/items/{item_id}",
        "source_workbook": line["source_workbook"],
        "source_sheet": line["source_sheet"],
        "source_cell": line["source_cell"],
        "before_state": state,
        "before_state_sha256": digest_for(state),
        "protected_state": protected,
        "protected_state_sha256": digest_for(protected),
        "rate_family_before": rate_family_state(state),
    }


def stage_plan(
    payload: dict[str, Any],
    organization: dict[str, str],
    live_evidence: dict[str, Any],
) -> Path:
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "action": ACTION,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "approval_required": APPROVAL_WORD,
        "origin": origin_record(),
        "organization": organization,
        "payload": payload,
        "risk": {
            "atomic": False,
            "sequential_writes": len(payload["lines"]),
            "note": RISK_NOTE,
        },
        "live_evidence": live_evidence,
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
        raise PriceToolError("Refused to overwrite an existing FNPT sales-rate plan.") from exc
    zoho_tool.append_receipt(
        f"zoho_inventory_{ACTION}_plan_staged_not_committed",
        f"plan={path}; sha256={digest}; lines={len(payload['lines'])}",
    )
    return path


def command_stage(args: argparse.Namespace) -> None:
    raw = read_json_object(Path(args.input), "Stage input")
    validated = validate_input(raw)
    vault = zoho_tool.load_vault()
    access_token, vault = zoho_tool.refresh_access_token(vault)
    organization = verified_organization(access_token, vault)
    evidence_rows: list[dict[str, Any]] = []
    for line in validated["lines"]:
        item = get_item(access_token, vault, line["item_id"])
        evidence_rows.append(item_evidence(item, line))
    zoho_tool.save_vault(vault)
    path = stage_plan(validated, organization, {"items": evidence_rows})
    plan = read_json_object(path, "Plan")
    increases = sum(1 for row in evidence_rows if row["direction"] == "INCREASE")
    decreases = sum(1 for row in evidence_rows if row["direction"] == "DECREASE")
    print("STAGED_NOT_COMMITTED")
    print(f"Tool: {TOOL_NAME}")
    print(
        "Scope: existing Zoho Inventory sales rate (rate) only, on existing items whose "
        "live SKU begins with " + " or ".join(ALLOWED_SKU_PREFIXES)
    )
    print(f"Rule: target CAD rate = supplier USD cost x {MULTIPLIER_TEXT}, half-up to two decimals")
    print(f"Organization: {organization['name']} ({organization['organization_id']}), "
          f"{organization['currency_code']}")
    print(f"Lines: {len(evidence_rows)} ({increases} increase, {decreases} decrease)")
    print("")
    for row in evidence_rows:
        print(
            f"  {row['item_id']}  {row['sku']}  "
            f"[{row['source_sheet']}!{row['source_cell']} = USD {row['supplier_cost_usd']}]  "
            f"{row['before_rate']} -> {row['target_rate_cad']} {TARGET_CURRENCY}  {row['direction']}"
        )
    print("")
    print(f"Zoho writes performed by this stage: 0 (read-only GETs only)")
    print(f"Plan: {path}")
    print(f"Plan sha256: {plan['sha256']}")
    print(f"Expires: {plan['expires_utc']} (24-hour maximum)")
    print(f"RISK: {RISK_NOTE}")
    print(
        f"To commit, relay Rachad's own go to THIS plan ({APPROVAL_WORD}, yes, go ahead, "
        "do it ...) from his message. Staging is not approval."
    )


# --------------------------------------------------------------------------
# Plan validation
# --------------------------------------------------------------------------


def validate_item_evidence(row: Any, line: dict[str, str]) -> None:
    if not isinstance(row, dict):
        raise PriceToolError("Plan item evidence contains a non-object row.")
    closed_fields(row, ITEM_EVIDENCE_FIELDS, "Plan item evidence")
    if not isinstance(row["before_state"], dict):
        raise PriceToolError("Plan before-state evidence must be an object.")
    if not secrets.compare_digest(
        str(row["before_state_sha256"]), digest_for(row["before_state"])
    ):
        raise PriceToolError("Plan before-state evidence hash is invalid.")
    if not secrets.compare_digest(
        str(row["protected_state_sha256"]), digest_for(row["protected_state"])
    ):
        raise PriceToolError("Plan protected-state evidence hash is invalid.")
    # Every stored figure is re-derived from the immutable staged live state, so
    # a tampered rate, SKU, cost, cell or fingerprint cannot survive.
    if item_evidence(row["before_state"], line) != row:
        raise PriceToolError(
            "Plan item evidence is not the canonical projection of the staged live state."
        )


def validate_plan(plan: dict[str, Any]) -> None:
    closed_fields(plan, PLAN_FIELDS, "Plan")
    if (
        plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("tool") != TOOL_NAME
        or plan.get("action") != ACTION
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise PriceToolError(
            "Plan schema version, tool, action, or approval requirement is invalid."
        )
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise PriceToolError("Plan nonce is invalid.")
    require_origin(plan.get("origin"))
    organization = plan.get("organization")
    if not isinstance(organization, dict):
        raise PriceToolError("Plan organization is invalid.")
    closed_fields(organization, ORGANIZATION_FIELDS, "Plan organization")
    positive_id(organization["organization_id"], "plan organization_id")
    clean_text(organization["name"], "plan organization name", 200)
    if organization["currency_code"] != TARGET_CURRENCY:
        raise PriceToolError(f"REFUSED: the plan organization is not {TARGET_CURRENCY}.")
    created = parse_time(plan["created_utc"], "creation time")
    expires = parse_time(plan["expires_utc"], "expiry")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise PriceToolError("Plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise PriceToolError("Plan creation time is in the future.")
    if now >= expires:
        raise PriceToolError("Plan expired. Stage a new plan for review.")
    payload = plan.get("payload")
    if not isinstance(payload, dict):
        raise PriceToolError("Plan payload is invalid.")
    validated = validate_input(payload)
    if payload != validated:
        raise PriceToolError("Plan payload is not canonical.")
    risk = plan.get("risk")
    if not isinstance(risk, dict):
        raise PriceToolError("Plan risk disclosure is invalid.")
    closed_fields(risk, RISK_FIELDS, "Plan risk")
    if (
        risk["atomic"] is not False
        or risk["sequential_writes"] != len(payload["lines"])
        or risk["note"] != RISK_NOTE
    ):
        raise PriceToolError("Plan must disclose the exact non-atomic sequential-write risk.")
    live = plan.get("live_evidence")
    if not isinstance(live, dict):
        raise PriceToolError("Plan live evidence is invalid.")
    closed_fields(live, {"items"}, "Plan live evidence")
    items = live["items"]
    if not isinstance(items, list) or len(items) != len(payload["lines"]):
        raise PriceToolError("Plan item evidence count does not match the payload.")
    for row, line in zip(items, payload["lines"]):
        validate_item_evidence(row, line)
    if [row["item_id"] for row in items] != [line["item_id"] for line in payload["lines"]]:
        raise PriceToolError("Plan item evidence order or IDs do not match the payload.")


def load_plan(path: Path) -> dict[str, Any]:
    plan = read_json_object(path, "Plan")
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise PriceToolError("Plan hash check failed. The plan changed after review.")
    validate_plan(plan)
    return plan


def lock_path(plan_sha256: str) -> Path:
    if not HEX_64_RE.fullmatch(str(plan_sha256)):
        raise PriceToolError("Plan digest is invalid for replay locking.")
    return PLAN_DIR / ".commit-locks" / f"{plan_sha256}.json"


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise PriceToolError(
            "REFUSED: this plan has already entered commit and cannot be replayed."
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


# --------------------------------------------------------------------------
# The one write transport
# --------------------------------------------------------------------------


def oauth_rate_write_allowed(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """The complete OAuth write transport and its exact allowlist.

    Only PUT, only one positive-ID item endpoint, only the preserved item name
    plus the new rate. Zoho requires the item name on an item PUT (proven by the
    commissioned item and classification tools, whose live item PUTs all carry
    the preserved name); it is the one permitted preserved identifying field and
    the read-back proves it did not move.
    """
    if method != "PUT":
        raise PriceToolError("REFUSED: the FNPT sales-rate update is a PUT and nothing else.")
    match = ITEM_PATH_RE.fullmatch(str(path))
    if not match:
        raise PriceToolError(
            "REFUSED: the FNPT sales-rate update targets one exact positive-ID item "
            "endpoint. Batch, bulk and every other route are unreachable."
        )
    positive_id(match.group(1), "item endpoint ID")
    if not isinstance(payload, dict) or set(payload) != {"name", "rate"}:
        raise PriceToolError(
            "REFUSED: the sales-rate PUT payload must contain exactly the preserved "
            "name and the approved rate."
        )
    clean_text(payload["name"], "preserved item name", 500)
    rate = payload["rate"]
    if isinstance(rate, bool) or not isinstance(rate, float):
        raise PriceToolError("REFUSED: the sales-rate PUT rate must be the approved number.")
    value = live_rate(rate, "PUT rate")
    if value <= 0 or value != value.quantize(CENT, rounding=ROUND_HALF_UP):
        raise PriceToolError("REFUSED: the sales-rate PUT rate must be a positive two-decimal CAD amount.")
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
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PriceToolError(f"Zoho sales-rate PUT failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise PriceToolError(f"Zoho sales-rate PUT outcome is indeterminate: {exc.reason}") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PriceToolError(
            "Zoho sales-rate PUT returned invalid JSON; the outcome is indeterminate."
        ) from exc
    if not isinstance(result, dict) or result.get("code") != 0:
        message = result.get("message") if isinstance(result, dict) else "invalid response"
        raise PriceToolError("Zoho sales-rate PUT returned an invalid or unknown result: " + str(message))
    return result


# --------------------------------------------------------------------------
# Commit
# --------------------------------------------------------------------------


def restage_input_path(plan_path: Path) -> Path:
    return plan_path.with_name(plan_path.stem + ".restage-input.json")


def write_restage_input(plan_path: Path, plan: dict[str, Any], item_ids: list[str]) -> Path:
    """A6: the stage input for ONLY the failed or untouched lines of this plan."""
    lines = [line for line in plan["payload"]["lines"] if line["item_id"] in set(item_ids)]
    path = restage_input_path(plan_path)
    path.write_text(
        json.dumps({"target_currency": TARGET_CURRENCY, "lines": lines}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def command_commit(args: argparse.Namespace) -> None:
    plan_path = contained_plan(args.plan)
    plan = load_plan(plan_path)
    # His go is checked before the lock, the vault, the token, and the network.
    go = require_rachad_approval(args.approval, getattr(args, "approval_lane", None))
    lock = lock_path(plan["sha256"])
    if lock.exists():
        owner_authority.refuse_replay(PriceToolError, owner_authority.read_json_if_exists(lock),
                                      what="FNPT sales-rate plan")
    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_IN_FLIGHT, plan_sha256=plan["sha256"], action=ACTION, go=go,
        completed=[], started_utc=utc_now().isoformat(),
    ), exclusive=True)
    rows = plan["live_evidence"]["items"]
    progress = owner_authority.BatchProgress([row["item_id"] for row in rows])
    completed: list[dict[str, str]] = []
    captured: dict[str, dict[str, Any]] = {}
    indeterminate: list[str] = []
    backup = owner_authority.live_state_path(plan_path)

    def finish_needs_restage(reason: str) -> None:
        status = owner_authority.STATUS_INDETERMINATE if indeterminate else owner_authority.STATUS_NEEDS_RESTAGE
        summary = progress.summary()
        restage = write_restage_input(plan_path, plan, summary["restage_only"])
        write_lock(lock, owner_authority.attempt_record(
            status, plan_sha256=plan["sha256"], action=ACTION, go=go, reason=reason,
            completed=completed, failed=progress.failed, pending=progress.pending,
            indeterminate=indeterminate, backup=str(backup) if captured else None,
            restage_input=str(restage),
        ))
        zoho_tool.append_receipt(
            f"zoho_inventory_{ACTION}_{status}",
            f"status={status}; completed={','.join(row['item_id'] for row in completed) or 'none'}; "
            f"failed={','.join(progress.failed) or 'none'}; pending={','.join(progress.pending) or 'none'}; "
            f"backup={backup if captured else 'none'}; restage_input={restage}; plan={plan_path}; "
            f"sha256={plan['sha256']}",
        )
        raise PriceToolError(
            owner_authority.explain_outcome(
                "FNPT sales-rate commit", status, reason,
                completed=[row["item_id"] for row in completed] or None,
                failed=progress.failed or None, pending=progress.pending or None,
            )
            + f" Stage input carrying only those lines: {restage}"
        )

    try:
        vault = zoho_tool.load_vault()
        if UPDATE_SCOPE not in (vault.get("scopes") or []):
            raise PriceToolError(f"Saved Zoho connection lacks {UPDATE_SCOPE}.")
        access_token, vault = zoho_tool.refresh_access_token(vault)
        organization = verified_organization(access_token, vault)
        if organization != plan["organization"]:
            raise PriceToolError(
                "REFUSED: the live FRP Depot Inventory organization does not match the "
                "organization recorded in the plan."
            )
        org_id = organization["organization_id"]
    except Exception as exc:
        # Nothing was written: every line is still pending.
        finish_needs_restage(str(exc))
        raise  # unreachable; finish_needs_restage always raises

    for evidence in rows:
        item_id = evidence["item_id"]
        target = Decimal(evidence["target_rate_cad"])
        in_flight = False
        try:
            # Refresh immediately before this line's non-atomic PUT.
            current = get_item(access_token, vault, item_id)
            if str(current.get("item_id") or "") != item_id:
                raise PriceToolError(f"Zoho returned the wrong item for {item_id}.")
            if clean_text(current.get("sku"), "live sku", 200) != evidence["sku"]:
                raise PriceToolError(f"Item {item_id} SKU changed after review.")
            if money_text(live_rate(current.get("rate"), "live rate")) != evidence["before_rate"]:
                raise PriceToolError(
                    f"Item {item_id} sales rate changed after review. No PUT was issued for this item."
                )
            if not secrets.compare_digest(
                digest_for(current), str(evidence["before_state_sha256"])
            ) or current != evidence["before_state"]:
                raise PriceToolError(
                    f"Item {item_id} changed after review. No PUT was issued for this item."
                )
            rate_value = float(target)
            if Decimal(str(rate_value)) != target:
                raise PriceToolError(f"Item {item_id} target rate is not exactly representable.")
            # A2: keep the live state this line is about to change beside the plan,
            # BEFORE the PUT, so `restore` can put it back.
            captured[item_id] = {
                "item_id": item_id,
                "sku": evidence["sku"],
                "name": evidence["name"],
                "before_rate": evidence["before_rate"],
                "target_rate_cad": evidence["target_rate_cad"],
                "before_state": current,
            }
            owner_authority.capture_live_state(
                plan_path, captured, what="Zoho Inventory item sales rate (rate)",
                where=f"{org_id} /inventory/v1/items", plan_sha256=plan["sha256"],
            )
            in_flight = True
            oauth_rate_write_allowed(
                access_token,
                str(vault["api_domain"]),
                "PUT",
                f"/inventory/v1/items/{item_id}",
                org_id,
                {"name": evidence["name"], "rate": rate_value},
            )
            verified = get_item(access_token, vault, item_id)
            if str(verified.get("item_id") or "") != item_id:
                raise PriceToolError(f"Item {item_id} read-back returned the wrong item.")
            verify_rate_family(verified, evidence["rate_family_before"], target)
            verify_protected_unchanged(verified, evidence)
            in_flight = False
            completed.append({
                "item_id": item_id,
                "sku": evidence["sku"],
                "before_rate": evidence["before_rate"],
                "rate": evidence["target_rate_cad"],
            })
            progress.done(item_id)
        except Exception as exc:  # noqa: BLE001 - recorded per line, the batch goes on (A6)
            progress.fail(item_id, str(exc))
            if in_flight:
                indeterminate.append(item_id)
    zoho_tool.save_vault(vault)

    if progress.status != owner_authority.STATUS_COMMITTED:
        first = next(iter(progress.failed.values()), "a line failed")
        finish_needs_restage(first)

    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_COMMITTED, plan_sha256=plan["sha256"], action=ACTION, go=go,
        completed=completed, backup=str(backup),
    ))
    zoho_tool.append_receipt(
        f"zoho_inventory_{ACTION}_committed_verified",
        f"completed={','.join(row['item_id'] for row in completed)}; backup={backup}; "
        f"plan={plan_path}; sha256={plan['sha256']}; go_lane={go.lane}",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "action": ACTION,
        "plan_sha256": plan["sha256"],
        "target_currency": TARGET_CURRENCY,
        "multiplier": MULTIPLIER_TEXT,
        "completed": completed,
        "atomic": False,
        "plan_spent": True,
        "replay_locked": True,
        "live_state_backup": str(backup),
        "restore": f"restore --plan {plan_path} --approval <his go>",
    }, ensure_ascii=False, indent=2))


def load_plan_for_restore(path: Path) -> dict[str, Any]:
    """The plan behind a restore: hash, tool and action are checked; expiry is
    not, because putting a rate back is allowed after the 24-hour plan window."""
    plan = read_json_object(path, "Plan")
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise PriceToolError("Plan hash check failed. The plan changed after review.")
    if plan.get("tool") != TOOL_NAME or plan.get("action") != ACTION:
        raise PriceToolError("Plan tool or action is invalid for a sales-rate restore.")
    require_origin(plan.get("origin"))
    return plan


def command_restore(args: argparse.Namespace) -> None:
    """A2: put back the live sales rates captured before this plan's PUTs.

    One PUT per captured line, each only if the item still carries the rate
    this plan wrote; a rate that moved since is reported and left alone.
    """
    plan_path = contained_plan(args.plan)
    plan = load_plan_for_restore(plan_path)
    go = require_rachad_approval(
        args.approval, getattr(args, "approval_lane", None),
        what="restoring the sales rates this plan changed",
    )
    record = owner_authority.load_live_state(plan_path, PriceToolError)
    if record.get("plan_sha256") != plan["sha256"]:
        raise PriceToolError("The captured live state belongs to a different plan.")
    restore_path = owner_authority.restore_record_path(plan_path)
    if restore_path.exists():
        raise PriceToolError(
            f"This plan's rates were already restored ({restore_path.name}); stage a new plan "
            "for any further change."
        )
    vault = zoho_tool.load_vault()
    if UPDATE_SCOPE not in (vault.get("scopes") or []):
        raise PriceToolError(f"Saved Zoho connection lacks {UPDATE_SCOPE}.")
    access_token, vault = zoho_tool.refresh_access_token(vault)
    organization = verified_organization(access_token, vault)
    if organization != plan["organization"]:
        raise PriceToolError(
            "REFUSED: the live FRP Depot Inventory organization does not match the "
            "organization recorded in the plan."
        )
    org_id = organization["organization_id"]
    restored: list[dict[str, str]] = []
    skipped: dict[str, str] = {}
    failed: dict[str, str] = {}
    for item_id, snapshot in record["state"].items():
        try:
            current = get_item(access_token, vault, item_id)
            if str(current.get("item_id") or "") != item_id:
                raise PriceToolError(f"Zoho returned the wrong item for {item_id}.")
            live = money_text(live_rate(current.get("rate"), "live rate"))
            if live == snapshot["before_rate"]:
                skipped[item_id] = f"already at the captured rate {live}"
                continue
            if live != snapshot["target_rate_cad"]:
                skipped[item_id] = f"moved since this plan wrote it (live {live}); left alone"
                continue
            before = Decimal(snapshot["before_rate"])
            rate_value = float(before)
            if Decimal(str(rate_value)) != before:
                raise PriceToolError(f"Item {item_id} captured rate is not exactly representable.")
            oauth_rate_write_allowed(
                access_token, str(vault["api_domain"]), "PUT",
                f"/inventory/v1/items/{item_id}", org_id,
                {"name": snapshot["name"], "rate": rate_value},
            )
            verified = get_item(access_token, vault, item_id)
            if money_text(live_rate(verified.get("rate"), "read-back rate")) != snapshot["before_rate"]:
                raise PriceToolError(f"Item {item_id} read-back rate is not the restored rate.")
            restored.append({"item_id": item_id, "sku": snapshot["sku"],
                             "from": snapshot["target_rate_cad"], "to": snapshot["before_rate"]})
        except Exception as exc:  # noqa: BLE001 - per line, the others still run
            failed[item_id] = str(exc)[:1000]
    zoho_tool.save_vault(vault)
    result = {
        "status": "RESTORED" if not failed else "RESTORE_PARTIAL",
        "plan_sha256": plan["sha256"],
        "restored": restored,
        "skipped": skipped,
        "failed": failed,
        **go.as_record(),
    }
    owner_authority.record_restore(plan_path, result)
    zoho_tool.append_receipt(
        f"zoho_inventory_{ACTION}_restored",
        f"restored={','.join(row['item_id'] for row in restored) or 'none'}; "
        f"skipped={','.join(skipped) or 'none'}; failed={','.join(failed) or 'none'}; "
        f"backup={owner_authority.live_state_path(plan_path)}; plan={plan_path}; sha256={plan['sha256']}",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failed:
        raise PriceToolError(
            "Restore did not complete for every line: " + "; ".join(f"{k}: {v}" for k, v in failed.items())
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--input", required=True)
    stage.set_defaults(func=command_stage)
    commit = commands.add_parser("commit")
    commit.add_argument("--plan", required=True)
    owner_authority.add_owner_go_arguments(commit)
    commit.set_defaults(func=command_commit)
    restore = commands.add_parser("restore", help="put back the live rates captured before this plan's PUTs")
    restore.add_argument("--plan", required=True)
    owner_authority.add_owner_go_arguments(restore)
    restore.set_defaults(func=command_restore)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (PriceToolError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
