#!/usr/bin/env python
"""Fixed value-only correction for eight FRP backing-ring items.

Commissioned by Rachad Homsi on 2026-08-11 after Inventory Adjustment
96274000001555048 loaded the approved 713 pieces but valued them at CAD 0.00.
This tool can create ONE immutable eight-line VALUE adjustment for CAD 78,816.51.
It cannot alter quantity, an item record, a rate, the source adjustment, or any
other Zoho record. Stage is read-only; commit needs Rachad's later exact APPROVED.
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

TOOL_NAME = "FRP Depot Fixed Eight Backing Ring Value Correction Tool"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = 1
ACTION = "eight_backing_ring_zero_value_correction"
APPROVAL_WORD = "APPROVED"
PLAN_LIFETIME_HOURS = 24
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_backing_ring_eight_value_correction_plans"
LOCK_DIR = PLAN_DIR / ".commit-locks"
CREATE_SCOPE = "ZohoInventory.inventoryadjustments.CREATE"
ADJUSTMENT_PATH = "/inventory/v1/inventoryadjustments"
CENT = Decimal("0.01")
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")

ADJUSTMENT_DATE = "2026-08-11"
REASON = "Inventory Revaluation"
REASON_ID = "96274000000014310"
ACCOUNT_ID = "96274000000896100"
ACCOUNT_NAME = "Inventory Adjustment"
REFERENCE = "BACKING-RINGS-8-VALUE-2026-08-11"
DESCRIPTION = (
    "Value-only correction for Inventory Adjustment 96274000001555048, which loaded "
    "713 backing rings on 2026-08-11 but Zoho valued every line at CAD 0.00 because "
    "the new items had zero cost. Adds the approved tentative landed value only: Fei "
    "supplier USD unit cost plus 20% landing allowance, converted at Bank of Canada "
    "2026-08-11 daily average 1 USD = CAD 1.3927. No quantity or item rate changes."
)
SOURCE_ADJUSTMENT_ID = "96274000001555048"
SOURCE_REFERENCE = "BACKING-RINGS-8-SIZES-2026-08-11"
SOURCE_PLAN_LOGICAL_SHA256 = "fd77238cca9e0552c216e9b79cac8569354cea1dfb310e5b53ff906aa01b696b"
FX_DATE = "2026-08-11"
FX_RATE = Decimal("1.3927")
LANDING_FACTOR = Decimal("1.20")
TOTAL_QUANTITY = Decimal("713")
TOTAL_VALUE_CAD = Decimal("78816.51")

TARGETS = (
    {"size_in": "1", "item_id": "96274000001556231", "name": 'FRP BACKING RING-1"/150PSI/D411', "sku": "BRDN25150PSI411", "quantity": Decimal("218"), "supplier_usd": Decimal("14.00"), "sales_rate_cad": Decimal("50.40"), "price_source": "master price sheet"},
    {"size_in": "1.5", "item_id": "96274000001556243", "name": 'FRP BACKING RING-1-1/2"/150PSI/D411', "sku": "BRDN40150PSI411", "quantity": Decimal("85"), "supplier_usd": Decimal("14.50"), "sales_rate_cad": Decimal("52.20"), "price_source": "master price sheet"},
    {"size_in": "2", "item_id": "96274000001556255", "name": 'FRP BACKING RING-2"/150PSI/D411', "sku": "BRDN50150PSI411", "quantity": Decimal("32"), "supplier_usd": Decimal("16.00"), "sales_rate_cad": Decimal("57.60"), "price_source": "January quotation"},
    {"size_in": "3", "item_id": "96274000001556267", "name": 'FRP BACKING RING-3"/150PSI/D411', "sku": "BRDN80150PSI411", "quantity": Decimal("39"), "supplier_usd": Decimal("20.00"), "sales_rate_cad": Decimal("72.00"), "price_source": "January quotation"},
    {"size_in": "6", "item_id": "96274000001556279", "name": 'FRP BACKING RING-6"/150PSI/D411', "sku": "BRDN150150PSI411", "quantity": Decimal("22"), "supplier_usd": Decimal("60.00"), "sales_rate_cad": Decimal("216.00"), "price_source": "January quotation"},
    {"size_in": "8", "item_id": "96274000001556291", "name": 'FRP BACKING RING-8"/150PSI/D411', "sku": "BRDN200150PSI411", "quantity": Decimal("238"), "supplier_usd": Decimal("95.00"), "sales_rate_cad": Decimal("342.00"), "price_source": "master price sheet"},
    {"size_in": "12", "item_id": "96274000001555023", "name": 'FRP BACKING RING-12"/150PSI/D411', "sku": "BRDN300150PSI411", "quantity": Decimal("47"), "supplier_usd": Decimal("202.00"), "sales_rate_cad": Decimal("727.20"), "price_source": "master price sheet"},
    {"size_in": "14", "item_id": "96274000001555035", "name": 'FRP BACKING RING-14"/150PSI/D411', "sku": "BRDN350150PSI411", "quantity": Decimal("32"), "supplier_usd": Decimal("255.00"), "sales_rate_cad": Decimal("918.00"), "price_source": "master price sheet"},
)
TARGET_IDS = tuple(row["item_id"] for row in TARGETS)

PACKING = ROOT / "Dado" / "20_Working" / "packing_rings"
SOURCE_PLAN_PATH = ROOT / "Dado" / "20_Working" / "zoho_backing_ring_eight_stock_plans" / "20260812T013942Z_eight_backing_ring_tentative_landed_stock_fd77238cca9e.json"
SOURCE_LOCK_PATH = ROOT / "Dado" / "20_Working" / "zoho_backing_ring_eight_stock_plans" / ".commit-locks" / f"{SOURCE_PLAN_LOGICAL_SHA256}.json"
RECONCILIATION_PATH = PACKING / "eight_stock_indeterminate_reconciliation_20260812.json"
LIVE_ADJUSTMENT_PATH = PACKING / "eight_stock_live_adjustment_96274000001555048_20260812.json"
BASELINE_PATH = PACKING / "eight_valuation_correction_live_baseline_20260812.json"
CONTRACT_YAML = PACKING / "zoho_value_adjustment_contract" / "inventoryadjustments.yml"
CONTRACT_HELP = PACKING / "zoho_value_adjustment_contract" / "inventory-adjustments.md"
BOC_FX_PATH = PACKING / "fx_sources" / "bank_of_canada_fxusdcad_2026-08-11.json"
SOURCE_FILES = {
    SOURCE_PLAN_PATH: "02c2b54553a8e163bd876b3825246035006bfdebf3b17c0c2cfb580bbe8cf820",
    SOURCE_LOCK_PATH: "32e24ccbaf0da9a323973aee607e7ec773548b740503441a6af5f59f8b0393c8",
    RECONCILIATION_PATH: "ecfa1370cecc61ddf820e279f6a02792c4605cf155df3721393eb8e53485402b",
    LIVE_ADJUSTMENT_PATH: "8509847d216d363d0ee13fc46899660ff1f492bca5c3756c579b3d8c470c7162",
    BASELINE_PATH: "902ba9ba7689d4a5106ada7a79e1219af249dc40c3d6cdf123dce181955765db",
    CONTRACT_YAML: "2f53def43227a57daca69b7f0e4cce84d07e8ad24a7a32d627bbfa6c1bca9bdf",
    CONTRACT_HELP: "828c873fe77bfd1d43b817e5baf626e75b334591a1f76d368f1c7418c1197cb4",
    BOC_FX_PATH: "7df4cf0beb129329a1488d7c409b8dd5b103009bba8dd2200df2b931f650bd9d",
}

PLAN_FIELDS = {"schema_version", "tool", "tool_version", "action", "created_utc", "expires_utc", "nonce", "approval_required", "organization", "payload", "risk", "source_evidence", "live_evidence", "sha256"}
ORGANIZATION_FIELDS = {"organization_id", "name", "currency_code"}
PAYLOAD_FIELDS = {"date", "reason", "description", "reference_number", "adjustment_type", "line_items"}
LINE_FIELDS = {"item_id", "name", "description", "value_adjusted", "unit", "adjustment_account_id"}
RISK_FIELDS = {"atomic", "write_count", "write_order", "note"}
SOURCE_FIELDS = {"failed_quantity_adjustment", "valuation_basis", "zoho_contract", "files"}
LIVE_FIELDS = {"stable_read_count", "stable_fingerprint", "source_adjustment", "items", "correction_reference_absent", "reason", "account"}

STOCK_FIELDS = {
    "stock_on_hand", "available_stock", "available_for_sale_stock",
    "actual_available_stock", "actual_available_for_sale_stock", "committed_stock",
    "actual_committed_stock", "initial_stock", "initial_stock_rate",
}
REQUIRED_STOCK_FIELDS = set(STOCK_FIELDS)
VALUATION_FIELDS = {"asset_value", "asset_price", "average_cost", "purchase_price"}
VOLATILE_ITEM_FIELDS = {"last_modified_time", "modified_time", "updated_time"}

RISK_NOTE = (
    "ATOMIC AT THE REQUEST LEVEL: one POST creates one permanent eight-line VALUE "
    "adjustment for CAD 78,816.51. It contains value_adjusted only; quantity_adjusted "
    "and item_total are absent. The source quantity adjustment is never updated. The "
    "plan is locked before the POST. Any failure, timeout or indeterminate result leaves "
    "it permanently locked with no retry, rollback, delete or cleanup. No item, rate, "
    "quantity, invoice, order, website record or email is writable."
)


class ValueCorrectionError(RuntimeError):
    pass


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
        raise ValueCorrectionError(f"Required evidence is unreadable: {path}") from exc


def read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueCorrectionError(f"{label} is unreadable.") from exc


def json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueCorrectionError("Zoho returned non-JSON evidence.") from exc


def decimal_value(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueCorrectionError(f"{label} is not numeric.")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueCorrectionError(f"{label} is invalid.") from exc
    if not result.is_finite():
        raise ValueCorrectionError(f"{label} must be finite.")
    return result


def money(value: Any, label: str) -> Decimal:
    return decimal_value(value, label).quantize(CENT, rounding=ROUND_HALF_UP)


def money_text(value: Any, label: str) -> str:
    return format(money(value, label), "f")


def zero_like(value: Any, label: str) -> bool:
    if value in (None, ""):
        return True
    return money(value, label) == Decimal("0.00")


def positive_id(value: Any, label: str) -> str:
    text = str(value if value is not None else "")
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise ValueCorrectionError(f"{label} must be a positive Zoho ID.")
    return text


def closed_fields(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        actual = set(value) if isinstance(value, dict) else set()
        raise ValueCorrectionError(
            f"{label} must use the exact closed schema; missing={sorted(expected-actual)}, unsupported={sorted(actual-expected)}."
        )
    return value


def parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or value != value.strip():
        raise ValueCorrectionError(f"{label} must be unpadded timestamp text.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueCorrectionError(f"{label} is invalid.") from exc
    if parsed.tzinfo is None:
        raise ValueCorrectionError(f"{label} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def exact_unit_cad(target: dict[str, Any]) -> Decimal:
    return target["supplier_usd"] * LANDING_FACTOR * FX_RATE


def line_value_cad(target: dict[str, Any]) -> Decimal:
    return (target["quantity"] * exact_unit_cad(target)).quantize(CENT, rounding=ROUND_HALF_UP)


def validate_arithmetic() -> None:
    if sum((row["quantity"] for row in TARGETS), Decimal("0")) != TOTAL_QUANTITY:
        raise ValueCorrectionError("Fixed quantity total is inconsistent.")
    if sum((line_value_cad(row) for row in TARGETS), Decimal("0")) != TOTAL_VALUE_CAD:
        raise ValueCorrectionError("Fixed valuation total is inconsistent.")
    for row in TARGETS:
        if row["supplier_usd"] * Decimal("3.6") != row["sales_rate_cad"]:
            raise ValueCorrectionError(f"Selling-rate provenance is inconsistent for {row['sku']}.")


def build_payload() -> dict[str, Any]:
    validate_arithmetic()
    return {
        "date": ADJUSTMENT_DATE,
        "reason": REASON,
        "description": DESCRIPTION,
        "reference_number": REFERENCE,
        "adjustment_type": "value",
        "line_items": [
            {
                "item_id": row["item_id"],
                "name": row["name"],
                "description": (
                    f"Value-only correction of source adjustment {SOURCE_ADJUSTMENT_ID}: "
                    f"Fei {row['price_source']} USD {row['supplier_usd']:.2f} + 20% x Bank of "
                    f"Canada {FX_DATE} FX {FX_RATE} CAD/USD; exact unit basis CAD "
                    f"{exact_unit_cad(row):f}, changed value CAD {line_value_cad(row):.2f}."
                ),
                "value_adjusted": float(line_value_cad(row)),
                "unit": "pcs",
                "adjustment_account_id": ACCOUNT_ID,
            }
            for row in TARGETS
        ],
    }


def verify_source_files() -> dict[str, Any]:
    files: dict[str, Any] = {}
    for path, wanted in SOURCE_FILES.items():
        actual = sha256_file(path)
        if actual != wanted:
            raise ValueCorrectionError(f"Source SHA-256 mismatch: {path}")
        files[str(path)] = {"sha256": actual, "bytes": path.stat().st_size}

    source_plan = read_json(SOURCE_PLAN_PATH, "Approved source plan")
    if source_plan.get("sha256") != SOURCE_PLAN_LOGICAL_SHA256:
        raise ValueCorrectionError("Approved source plan logical SHA-256 changed.")
    lock = read_json(SOURCE_LOCK_PATH, "Source commit lock")
    if (
        lock.get("plan_sha256") != SOURCE_PLAN_LOGICAL_SHA256
        or lock.get("state") != "indeterminate"
        or (lock.get("details") or {}).get("inventory_adjustment_id") != SOURCE_ADJUSTMENT_ID
        or (lock.get("details") or {}).get("no_retry") is not True
    ):
        raise ValueCorrectionError("Source adjustment lock no longer proves one permanent no-retry attempt.")
    reconciled = read_json(RECONCILIATION_PATH, "Source reconciliation")
    if (
        reconciled.get("status") != "live_result_exact_and_stable_but_zero_valued"
        or reconciled.get("inventory_adjustment_id") != SOURCE_ADJUSTMENT_ID
        or reconciled.get("quantity_loaded") != 713
        or str(reconciled.get("live_total_cad")) != "0.00"
        or str(reconciled.get("planned_tentative_total_cad")) != "78816.51"
        or reconciled.get("stable") is not True
        or reconciled.get("no_retry") is not True
    ):
        raise ValueCorrectionError("Source reconciliation no longer proves the stable zero-valued load.")

    contract = CONTRACT_YAML.read_text(encoding="utf-8")
    help_text = CONTRACT_HELP.read_text(encoding="utf-8")
    if (
        "given only when adjustment_type is given as value" not in contract
        or "value_adjusted:" not in contract
        or "Allowed values are <code>quantity</code> and <code>value</code> only" not in contract
        or "Enter the Changed Value or the Adjusted Value" not in help_text
        or "You can only make value adjustments for items which are in stock" not in help_text
    ):
        raise ValueCorrectionError("Saved Zoho contract evidence does not prove value_adjusted semantics.")

    fx = read_json(BOC_FX_PATH, "Bank of Canada FX source")
    observations = fx.get("observations") or []
    if len(observations) != 1 or observations[0].get("d") != FX_DATE or str((observations[0].get("FXUSDCAD") or {}).get("v")) != str(FX_RATE):
        raise ValueCorrectionError("Bank of Canada evidence no longer proves FX 1.3927 on 2026-08-11.")
    return files


def get_item(token: str, domain: str, organization_id: str, item_id: str) -> dict[str, Any]:
    result = zoho_tool.api_get(token, domain, f"/inventory/v1/items/{positive_id(item_id, 'item_id')}?{urlencode({'organization_id': organization_id})}")
    item = result.get("item") or {}
    if str(item.get("item_id") or "") != item_id:
        raise ValueCorrectionError(f"Zoho did not return fixed item {item_id}.")
    return item


def get_adjustment(token: str, domain: str, organization_id: str, adjustment_id: str) -> dict[str, Any]:
    result = zoho_tool.api_get(token, domain, f"/inventory/v1/inventoryadjustments/{positive_id(adjustment_id, 'adjustment ID')}?{urlencode({'organization_id': organization_id})}")
    adjustment = result.get("inventory_adjustment") or {}
    if str(adjustment.get("inventory_adjustment_id") or "") != adjustment_id:
        raise ValueCorrectionError(f"Zoho did not return adjustment {adjustment_id}.")
    return adjustment


def list_adjustments(token: str, domain: str, organization_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(1, 101):
        query = urlencode({"organization_id": organization_id, "page": page, "per_page": 200})
        result = zoho_tool.api_get(token, domain, f"/inventory/v1/inventoryadjustments?{query}")
        rows.extend(result.get("inventory_adjustments") or [])
        if not zoho_tool.require_has_more_page(
            result,
            "/inventory/v1/inventoryadjustments",
            page,
            ValueCorrectionError,
        ):
            return rows
    raise ValueCorrectionError("Adjustment duplicate scan exceeded 20,000 records.")


def require_reference_absent(token: str, domain: str, organization_id: str) -> None:
    if any(str(row.get("reference_number") or "") == REFERENCE for row in list_adjustments(token, domain, organization_id)):
        raise ValueCorrectionError(f"REFUSED: value-adjustment reference {REFERENCE} already exists.")


def inventory_organization(token: str, domain: str, expected_id: str) -> dict[str, str]:
    result = zoho_tool.api_get(token, domain, "/inventory/v1/organizations")
    rows = [row for row in result.get("organizations") or [] if str(row.get("organization_id") or "") == expected_id]
    if len(rows) != 1:
        raise ValueCorrectionError("Zoho did not return exactly the saved FRP Depot organization.")
    name = str(rows[0].get("name") or rows[0].get("organization_name") or "")
    currency = str(rows[0].get("currency_code") or "")
    if "frpdepot" not in "".join(ch for ch in name.casefold() if ch.isalnum()) or currency != "CAD":
        raise ValueCorrectionError("Saved organization is not FRP Depot in CAD.")
    return {"name": name, "currency_code": currency}


def stock_projection(item: dict[str, Any]) -> dict[str, str]:
    missing = sorted(REQUIRED_STOCK_FIELDS - set(item))
    if missing:
        raise ValueCorrectionError("Zoho item is missing required stock fields: " + ", ".join(missing))
    return {key: money_text(item[key], f"item.{key}") for key in sorted(REQUIRED_STOCK_FIELDS)}


def valuation_projection(item: dict[str, Any]) -> dict[str, Any]:
    return {key: json_copy(item.get(key)) for key in sorted(VALUATION_FIELDS)}


def business_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: json_copy(value) for key, value in item.items() if key not in VOLATILE_ITEM_FIELDS}


def validate_item_before(item: dict[str, Any], target: dict[str, Any]) -> None:
    fixed = {
        "item_id": target["item_id"], "name": target["name"], "sku": target["sku"],
        "status": "active", "unit": "pcs", "item_type": "inventory",
        "product_type": "goods", "track_inventory": True, "is_combo_product": False,
    }
    for field, expected in fixed.items():
        if item.get(field) != expected:
            raise ValueCorrectionError(f"Fixed item {target['item_id']} {field} changed.")
    if money(item.get("rate"), "rate") != target["sales_rate_cad"]:
        raise ValueCorrectionError(f"Fixed item {target['item_id']} selling rate changed.")
    if money(item.get("purchase_rate"), "purchase_rate") != Decimal("0.00"):
        raise ValueCorrectionError(f"Fixed item {target['item_id']} purchase rate is no longer zero.")
    stock = stock_projection(item)
    expected_quantity = target["quantity"]
    increased = {"stock_on_hand", "available_stock", "available_for_sale_stock", "actual_available_stock", "actual_available_for_sale_stock"}
    zero = {"committed_stock", "actual_committed_stock", "initial_stock", "initial_stock_rate"}
    for field in increased:
        if decimal_value(stock[field], field) != expected_quantity:
            raise ValueCorrectionError(f"Fixed item {target['item_id']} {field} is not {expected_quantity}.")
    for field in zero:
        if decimal_value(stock[field], field) != 0:
            raise ValueCorrectionError(f"Fixed item {target['item_id']} {field} is not zero.")
    for field in VALUATION_FIELDS:
        if not zero_like(item.get(field), f"item.{field}"):
            raise ValueCorrectionError(f"Fixed item {target['item_id']} {field} is no longer zero/blank.")


def source_adjustment_projection(adjustment: dict[str, Any]) -> dict[str, Any]:
    lines = adjustment.get("line_items") or []
    return {
        "inventory_adjustment_id": str(adjustment.get("inventory_adjustment_id") or ""),
        "date": adjustment.get("date"), "reason": adjustment.get("reason"),
        "reason_id": str(adjustment.get("reason_id") or ""),
        "description": adjustment.get("description"), "reference_number": adjustment.get("reference_number"),
        "adjustment_type": adjustment.get("adjustment_type"), "status": adjustment.get("status"),
        "total": money_text(adjustment.get("total"), "source total"),
        "is_inventory_valuation_pending": adjustment.get("is_inventory_valuation_pending"),
        "line_items": [
            {
                "line_item_id": str(line.get("line_item_id") or ""),
                "item_id": str(line.get("item_id") or ""), "name": line.get("name"),
                "description": line.get("description"),
                "quantity_adjusted": money_text(line.get("quantity_adjusted"), "source quantity"),
                "item_total": money_text(line.get("item_total"), "source line total"),
                "unit": line.get("unit"), "adjustment_account_id": str(line.get("adjustment_account_id") or ""),
            }
            for line in lines
        ],
    }


def validate_source_adjustment(adjustment: dict[str, Any]) -> dict[str, Any]:
    projection = source_adjustment_projection(adjustment)
    expected = {
        "inventory_adjustment_id": SOURCE_ADJUSTMENT_ID, "date": ADJUSTMENT_DATE,
        "reason": REASON, "reason_id": REASON_ID, "reference_number": SOURCE_REFERENCE,
        "adjustment_type": "quantity", "status": "adjusted", "total": "0.00",
        "is_inventory_valuation_pending": False,
    }
    for field, wanted in expected.items():
        if projection[field] != wanted:
            raise ValueCorrectionError(f"Source quantity adjustment {field} changed.")
    lines = projection["line_items"]
    if len(lines) != 8 or [row["item_id"] for row in lines] != list(TARGET_IDS):
        raise ValueCorrectionError("Source quantity adjustment line identity/order changed.")
    for line, target in zip(lines, TARGETS):
        if (
            decimal_value(line["quantity_adjusted"], "source quantity") != target["quantity"]
            or line["item_total"] != "0.00" or line["name"] != target["name"]
            or line["unit"] != "pcs" or line["adjustment_account_id"] != ACCOUNT_ID
        ):
            raise ValueCorrectionError(f"Source quantity adjustment line changed for {target['sku']}.")
    return projection


def live_round(token: str, domain: str, organization_id: str) -> dict[str, Any]:
    source = validate_source_adjustment(get_adjustment(token, domain, organization_id, SOURCE_ADJUSTMENT_ID))
    items = []
    for target in TARGETS:
        item = get_item(token, domain, organization_id, target["item_id"])
        validate_item_before(item, target)
        items.append({
            "item_id": target["item_id"], "name": target["name"], "sku": target["sku"],
            "stock": stock_projection(item), "valuation": valuation_projection(item),
            "selling_rate_cad": money_text(item.get("rate"), "rate"),
            "purchase_rate_cad": money_text(item.get("purchase_rate"), "purchase rate"),
            "business_item": business_item(item), "business_item_sha256": digest_for(business_item(item)),
        })
    return {"source_adjustment": source, "items": items}


def stable_rehearsal(token: str, domain: str, organization_id: str, reads: int = 3) -> dict[str, Any]:
    rounds = [live_round(token, domain, organization_id) for _ in range(reads)]
    fingerprints = [digest_for(row) for row in rounds]
    if len(set(fingerprints)) != 1:
        raise ValueCorrectionError("Live source/item state moved during the bounded read-only rehearsal; no plan staged.")
    return {"stable_read_count": reads, "stable_fingerprint": fingerprints[0], **rounds[-1]}


def build_source_evidence(files: dict[str, Any]) -> dict[str, Any]:
    return {
        "failed_quantity_adjustment": {
            "plan_sha256": SOURCE_PLAN_LOGICAL_SHA256,
            "inventory_adjustment_id": SOURCE_ADJUSTMENT_ID,
            "reference_number": SOURCE_REFERENCE,
            "quantity_loaded": int(TOTAL_QUANTITY), "live_value_cad": "0.00",
            "state": "exact and stable, permanently replay-locked, no retry",
        },
        "valuation_basis": {
            "formula": "line CAD = quantity x Fei USD unit cost x 1.20 x 1.3927; ROUND_HALF_UP once per line to CAD 0.01",
            "landing_allowance": "20.00% separate tentative freight/duty/landing allowance",
            "exchange_rate": {"source": "Bank of Canada FXUSDCAD", "date": FX_DATE, "rate": str(FX_RATE)},
            "lines": [
                {
                    "sku": row["sku"], "quantity": int(row["quantity"]),
                    "supplier_usd_unit": format(row["supplier_usd"], ".2f"),
                    "supplier_source": row["price_source"],
                    "landed_usd_unit": format(row["supplier_usd"] * LANDING_FACTOR, ".2f"),
                    "exact_landed_cad_unit": format(exact_unit_cad(row), "f"),
                    "changed_value_cad": money_text(line_value_cad(row), "line value"),
                    "independent_selling_rate_cad": format(row["sales_rate_cad"], ".2f"),
                }
                for row in TARGETS
            ],
            "total_quantity": int(TOTAL_QUANTITY), "total_changed_value_cad": money_text(TOTAL_VALUE_CAD, "total"),
            "selling_rate_rule": "Fei USD x 3.6 remains independent and is not part of this value adjustment.",
        },
        "zoho_contract": {
            "adjustment_type": "value", "line_write_field": "value_adjusted",
            "quantity_adjusted_absent": True, "item_total_absent": True,
            "source": "Zoho Inventory published OpenAPI and user guide, SHA-256 pinned in files",
        },
        "files": files,
    }


def validate_payload(payload: Any) -> dict[str, Any]:
    payload = closed_fields(payload, PAYLOAD_FIELDS, "payload")
    if canonical(payload) != canonical(build_payload()):
        raise ValueCorrectionError("Plan payload is not the one fixed commissioned value payload.")
    lines = payload["line_items"]
    if not isinstance(lines, list) or len(lines) != 8:
        raise ValueCorrectionError("Fixed value payload must contain exactly eight lines.")
    for index, line in enumerate(lines):
        closed_fields(line, LINE_FIELDS, f"line {index+1}")
        if "quantity_adjusted" in line or "item_total" in line:
            raise ValueCorrectionError("Value correction payload cannot contain quantity_adjusted or item_total.")
    if [str(line["item_id"]) for line in lines] != list(TARGET_IDS):
        raise ValueCorrectionError("Fixed value payload item identity/order changed.")
    return payload


def validate_plan(plan: Any) -> dict[str, Any]:
    plan = closed_fields(plan, PLAN_FIELDS, "plan")
    saved_hash = plan["sha256"]
    if not isinstance(saved_hash, str) or not HEX_64_RE.fullmatch(saved_hash):
        raise ValueCorrectionError("Plan SHA-256 is malformed.")
    unsigned = dict(plan); unsigned.pop("sha256")
    if digest_for(unsigned) != saved_hash:
        raise ValueCorrectionError("Plan hash check failed; the staged plan changed.")
    if (plan["schema_version"], plan["tool"], plan["tool_version"], plan["action"]) != (SCHEMA_VERSION, TOOL_NAME, TOOL_VERSION, ACTION):
        raise ValueCorrectionError("Plan belongs to another tool/version/action.")
    if plan["approval_required"] != APPROVAL_WORD:
        raise ValueCorrectionError("Plan approval requirement changed.")
    if not isinstance(plan["nonce"], str) or not NONCE_RE.fullmatch(plan["nonce"]):
        raise ValueCorrectionError("Plan nonce is malformed.")
    created = parse_utc(plan["created_utc"], "created_utc")
    expires = parse_utc(plan["expires_utc"], "expires_utc")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise ValueCorrectionError("Plan lifetime is not exactly 24 hours.")
    org = closed_fields(plan["organization"], ORGANIZATION_FIELDS, "organization")
    positive_id(org["organization_id"], "organization ID")
    if org["currency_code"] != "CAD":
        raise ValueCorrectionError("Plan organization currency is not CAD.")
    validate_payload(plan["payload"])
    risk = closed_fields(plan["risk"], RISK_FIELDS, "risk")
    if risk != {"atomic": True, "write_count": 1, "write_order": ["POST one eight-line value adjustment"], "note": RISK_NOTE}:
        raise ValueCorrectionError("Plan risk disclosure changed.")
    source = closed_fields(plan["source_evidence"], SOURCE_FIELDS, "source_evidence")
    if source != build_source_evidence(source["files"]):
        raise ValueCorrectionError("Plan source evidence changed.")
    live = closed_fields(plan["live_evidence"], LIVE_FIELDS, "live_evidence")
    if live["stable_read_count"] != 3 or not HEX_64_RE.fullmatch(str(live["stable_fingerprint"])):
        raise ValueCorrectionError("Plan lacks the three-read stable rehearsal.")
    if live["correction_reference_absent"] is not True:
        raise ValueCorrectionError("Plan did not prove correction reference absent.")
    if live["reason"] != {"reason": REASON, "reason_id": REASON_ID} or live["account"] != {"name": ACCOUNT_NAME, "account_id": ACCOUNT_ID}:
        raise ValueCorrectionError("Plan reason/account evidence changed.")
    if digest_for({"source_adjustment": live["source_adjustment"], "items": live["items"]}) != live["stable_fingerprint"]:
        raise ValueCorrectionError("Plan stable live fingerprint is inconsistent.")
    return plan


def write_plan(core: dict[str, Any]) -> Path:
    plan = dict(core); plan["sha256"] = digest_for(core)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    path = PLAN_DIR / f"{utc_now().strftime('%Y%m%dT%H%M%SZ')}_{ACTION}_{plan['sha256'][:12]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt("zoho_eight_backing_ring_value_correction_plan_staged", str(path))
    return path


def load_plan(path_text: str) -> dict[str, Any]:
    path = Path(path_text).expanduser().resolve()
    try:
        return validate_plan(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueCorrectionError(f"Plan is unreadable: {path}") from exc


# Shared owner authority (autonomy programme 2026-08-21, spec A3/A4/A5). A VALUE
# Inventory Adjustment is a financial record, so this stays MONEY work: stage,
# then his own unambiguous go to THAT plan, sent after the plan was written.
# Exact APPROVED is no longer REQUIRED; a failed commit is reported and
# re-staged; nothing is permanently locked. The approved plan of 2026-08-12 is
# spent and stays spent. The SOURCE lock this tool reads as a precondition is
# the eight-stock tool's historical on-disk record and is left exactly as it is.
sys.path.append(str(Path(__file__).resolve().parent.parent / "common"))
import owner_authority  # noqa: E402

_STATE_TO_STATUS = {
    "verified": owner_authority.STATUS_COMMITTED,
    "indeterminate": owner_authority.STATUS_INDETERMINATE,
    "commit_started": owner_authority.STATUS_IN_FLIGHT,
}


def refuse_existing_lock(path: Path) -> None:
    record = owner_authority.read_json_if_exists(path) or {}
    state = str(record.get("state") or "")
    owner_authority.refuse_replay(ValueCorrectionError, {"status": _STATE_TO_STATUS.get(state, state)},
                                  what="value-correction plan")


def lock_path(plan: dict[str, Any]) -> Path:
    return LOCK_DIR / f"{plan['sha256']}.json"


def acquire_lock(plan: dict[str, Any], go: owner_authority.OwnerGo | None = None) -> Path:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = lock_path(plan)
    record = {"plan_sha256": plan["sha256"], "action": ACTION, "locked_utc": utc_now().isoformat(),
              "state": "commit_started", "permanent_lock": False,
              **(go.as_record() if go is not None else {})}
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        refuse_existing_lock(path)
        raise  # unreachable
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
    return path


def update_lock(path: Path, state: str, details: dict[str, Any]) -> None:
    current = json.loads(path.read_text(encoding="utf-8"))
    current.update({"state": state, "updated_utc": utc_now().isoformat(), "details": details})
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_scope_before_lock() -> dict[str, Any]:
    vault = zoho_tool.load_vault()
    if CREATE_SCOPE not in set(vault.get("scopes") or []):
        raise ValueCorrectionError("REFUSED BEFORE LOCK: saved Zoho connection lacks ZohoInventory.inventoryadjustments.CREATE.")
    return vault


def request_result(request: Request) -> dict[str, Any]:
    """Sole live write call site; route and body are validated by perform_create."""
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueCorrectionError(f"Zoho write failed with HTTP {exc.code}: {detail}; this plan needs re-stage.") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ValueCorrectionError(f"Zoho write result is indeterminate: {exc}; this plan needs re-stage.") from exc
    if result.get("code") not in (None, 0):
        raise ValueCorrectionError(f"Zoho write failed: {result.get('message') or result.get('code')}; this plan needs re-stage.")
    return result


def perform_create(token: str, domain: str, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if canonical(payload) != canonical(build_payload()):
        raise ValueCorrectionError("REFUSED: payload is not the fixed eight-line value adjustment.")
    request = Request(
        domain.rstrip("/") + ADJUSTMENT_PATH + "?" + urlencode({"organization_id": organization_id}),
        data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Zoho-oauthtoken {token}", "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    return request_result(request)


def adjustment_id_from_response(result: dict[str, Any]) -> str:
    for value in (result.get("inventory_adjustment_id"), (result.get("inventory_adjustment") or {}).get("inventory_adjustment_id")):
        text = str(value or "")
        if re.fullmatch(r"[1-9][0-9]*", text):
            return text
    raise ValueCorrectionError("Value-adjustment response lacks a verifiable ID; result is indeterminate and locked.")


def verify_created_adjustment(adjustment: dict[str, Any], adjustment_id: str) -> dict[str, Any]:
    fixed = {
        "inventory_adjustment_id": adjustment_id, "date": ADJUSTMENT_DATE,
        "reason": REASON, "reason_id": REASON_ID, "description": DESCRIPTION,
        "reference_number": REFERENCE, "adjustment_type": "value", "status": "adjusted",
    }
    for field, expected in fixed.items():
        if str(adjustment.get(field) if adjustment.get(field) is not None else "") != str(expected):
            raise ValueCorrectionError(f"Value adjustment verification failed for {field}.")
    lines = adjustment.get("line_items") or []
    if len(lines) != 8 or [str(line.get("item_id") or "") for line in lines] != list(TARGET_IDS):
        raise ValueCorrectionError("Value adjustment line identity/order differs from plan.")
    for line, expected, target in zip(lines, build_payload()["line_items"], TARGETS):
        for field in ("name", "description", "unit", "adjustment_account_id"):
            if str(line.get(field) or "") != str(expected[field]):
                raise ValueCorrectionError(f"Value adjustment {target['sku']} {field} mismatch.")
        if line.get("quantity_adjusted") not in (None, "", 0, 0.0, "0", "0.0"):
            raise ValueCorrectionError("Value adjustment unexpectedly changed quantity.")
        observable = []
        for field in ("value_adjusted", "item_total"):
            if line.get(field) not in (None, ""):
                observable.append(money(line[field], field))
        if not observable or any(value != line_value_cad(target) for value in observable):
            raise ValueCorrectionError(f"Value adjustment line value mismatch for {target['sku']}.")
    if money(adjustment.get("total"), "adjustment total") != TOTAL_VALUE_CAD:
        raise ValueCorrectionError("Value adjustment total mismatch.")
    if adjustment.get("is_inventory_valuation_pending") is not False:
        raise ValueCorrectionError("Zoho reports inventory valuation pending; result is indeterminate.")
    return {"inventory_adjustment_id": adjustment_id, "status": "adjusted", "reference_number": REFERENCE, "total_cad": money_text(adjustment.get("total"), "total")}


def verify_item_after(item: dict[str, Any], evidence: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    if stock_projection(item) != evidence["stock"]:
        raise ValueCorrectionError(f"Item {target['item_id']} stock/quantity changed unexpectedly.")
    if money(item.get("rate"), "rate") != target["sales_rate_cad"] or money(item.get("purchase_rate"), "purchase rate") != Decimal("0.00"):
        raise ValueCorrectionError(f"Item {target['item_id']} selling or purchase rate changed unexpectedly.")
    before = evidence["business_item"]
    after = business_item(item)
    protected_before = {k: v for k, v in before.items() if k not in VALUATION_FIELDS}
    protected_after = {k: v for k, v in after.items() if k not in VALUATION_FIELDS}
    if protected_after != protected_before:
        raise ValueCorrectionError(f"Protected item fields changed on {target['item_id']}.")
    observed = valuation_projection(item)
    numeric_observed = {k: money(v, k) for k, v in observed.items() if v not in (None, "")}
    if numeric_observed and all(value == Decimal("0.00") for value in numeric_observed.values()):
        raise ValueCorrectionError(f"Zoho still exposes only zero valuation on {target['item_id']}.")
    return {
        "item_id": target["item_id"], "sku": target["sku"], "stock_unchanged": evidence["stock"],
        "selling_rate_cad": money_text(item.get("rate"), "rate"),
        "purchase_rate_cad": money_text(item.get("purchase_rate"), "purchase rate"),
        "valuation_fields_after": observed,
    }


def command_stage(_: argparse.Namespace) -> None:
    files = verify_source_files()
    vault = zoho_tool.load_vault()
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = str(vault.get("api_domain") or "")
    organization_id = positive_id(vault.get("inventory_organization_id"), "inventory organization ID")
    organization = inventory_organization(token, domain, organization_id)
    require_reference_absent(token, domain, organization_id)
    rehearsal = stable_rehearsal(token, domain, organization_id, reads=3)
    zoho_tool.save_vault(vault)
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "action": ACTION, "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16), "approval_required": APPROVAL_WORD,
        "organization": {"organization_id": organization_id, "name": organization["name"], "currency_code": organization["currency_code"]},
        "payload": build_payload(),
        "risk": {"atomic": True, "write_count": 1, "write_order": ["POST one eight-line value adjustment"], "note": RISK_NOTE},
        "source_evidence": build_source_evidence(files),
        "live_evidence": {
            **rehearsal, "correction_reference_absent": True,
            "reason": {"reason": REASON, "reason_id": REASON_ID},
            "account": {"name": ACCOUNT_NAME, "account_id": ACCOUNT_ID},
        },
    }
    path = write_plan(core)
    plan = load_plan(str(path))
    print(json.dumps({
        "status": "STAGED ONLY - ZERO ZOHO WRITES", "plan": str(path), "sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"], "approval_required": APPROVAL_WORD,
        "scope_ready": CREATE_SCOPE in set(vault.get("scopes") or []), "risk": plan["risk"],
        "source_adjustment_id": SOURCE_ADJUSTMENT_ID, "source_quantity_loaded": int(TOTAL_QUANTITY),
        "source_live_value_cad": "0.00", "correction_total_cad": money_text(TOTAL_VALUE_CAD, "total"),
        "lines": plan["source_evidence"]["valuation_basis"]["lines"],
        "payload": plan["payload"], "stable_read_count": plan["live_evidence"]["stable_read_count"],
    }, ensure_ascii=False, indent=2))


def command_commit(args: argparse.Namespace) -> None:
    plan = load_plan(args.plan)
    # His go is checked before the vault and the network (A3): his own
    # unambiguous go to THIS plan, after it was written (the message time is required).
    try:
        go = owner_authority.require_owner_go_after_plan(
            args.approval, plan_created_utc=plan.get("created_utc"), plan_expires_utc=plan.get("expires_utc"),
            sent_utc=getattr(args, "approval_message_utc", None), lane=getattr(args, "approval_lane", None),
            what="this value-correction plan",
        )
    except owner_authority.OwnerAuthorityRefused as exc:
        raise ValueCorrectionError(str(exc)) from exc
    if utc_now() > parse_utc(plan["expires_utc"], "expires_utc"):
        raise ValueCorrectionError("REFUSED: staged plan expired; stage a fresh read-only plan.")
    if lock_path(plan).exists():
        refuse_existing_lock(lock_path(plan))
    vault = require_scope_before_lock()
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = str(vault.get("api_domain") or "")
    organization_id = positive_id(vault.get("inventory_organization_id"), "inventory organization ID")
    if organization_id != plan["organization"]["organization_id"]:
        raise ValueCorrectionError("REFUSED BEFORE LOCK: saved organization differs from plan.")
    organization = inventory_organization(token, domain, organization_id)
    if organization["name"] != plan["organization"]["name"] or organization["currency_code"] != "CAD":
        raise ValueCorrectionError("REFUSED BEFORE LOCK: live organization differs from plan.")
    require_reference_absent(token, domain, organization_id)
    current = stable_rehearsal(token, domain, organization_id, reads=3)
    staged = {k: plan["live_evidence"][k] for k in ("stable_read_count", "stable_fingerprint", "source_adjustment", "items")}
    if current != staged:
        raise ValueCorrectionError("REFUSED BEFORE LOCK: live source/item state changed since staging; stage a fresh plan.")
    zoho_tool.save_vault(vault)
    lock = acquire_lock(plan, go)
    adjustment_id = ""
    try:
        result = perform_create(token, domain, organization_id, plan["payload"])
        adjustment_id = adjustment_id_from_response(result)
        adjustment = verify_created_adjustment(get_adjustment(token, domain, organization_id, adjustment_id), adjustment_id)
        items = []
        for evidence, target in zip(plan["live_evidence"]["items"], TARGETS):
            items.append(verify_item_after(get_item(token, domain, organization_id, target["item_id"]), evidence, target))
        source_after = validate_source_adjustment(get_adjustment(token, domain, organization_id, SOURCE_ADJUSTMENT_ID))
        if source_after != plan["live_evidence"]["source_adjustment"]:
            raise ValueCorrectionError("Source quantity adjustment changed unexpectedly.")
        details = {
            "value_adjustment": adjustment, "source_adjustment_preserved": SOURCE_ADJUSTMENT_ID,
            "items": items, "completed_writes": ["value adjustment " + adjustment_id],
            "quantity_writes": 0, "item_writes": 0, "website_writes": 0, "emails_sent": 0,
        }
        update_lock(lock, "verified", details)
        zoho_tool.append_receipt("zoho_eight_backing_ring_value_correction_verified", str(lock))
        print(json.dumps({"status": "COMMITTED AND VERIFIED", "details": details, "lock": str(lock)}, ensure_ascii=False, indent=2))
    except Exception as exc:
        update_lock(lock, "indeterminate", {
            "error": str(exc), "inventory_adjustment_id": adjustment_id or None, "permanent_lock": False,
            "guidance": owner_authority.explain_outcome(
                "The value correction", owner_authority.STATUS_INDETERMINATE,
                "Reconcile with fresh read-only Zoho reads; the re-stage's reference check refuses a "
                "second adjustment if the first one landed.", money=True,
            ),
        })
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage", help="Read live state and stage the fixed value plan; no writes.")
    stage.set_defaults(func=command_stage)
    commit = sub.add_parser("commit", help="Commit one immutable staged value plan once.")
    commit.add_argument("--plan", required=True)
    owner_authority.add_owner_go_arguments(commit, money=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args(); args.func(args); return 0
    except (ValueCorrectionError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
