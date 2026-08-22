#!/usr/bin/env python
"""Fixed eight-item FRP backing-ring stock load at tentative landed CAD value.

Commissioned by Rachad Homsi on 2026-08-11. This tool can stage and commit
one immutable Inventory Adjustment for the eight already-created generic
backing-ring items. It does not change item records or selling/purchase rates.
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

TOOL_NAME = "FRP Depot Fixed Eight Backing Ring Tentative Landed Stock Tool"
TOOL_VERSION = "1.0.1"
SCHEMA_VERSION = 1
ACTION = "eight_backing_ring_tentative_landed_stock"
APPROVAL_WORD = "APPROVED"
PLAN_LIFETIME_HOURS = 24
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_backing_ring_eight_stock_plans"
LOCK_DIR = PLAN_DIR / ".commit-locks"
INVENTORY_ADJUSTMENT_CREATE_SCOPE = "ZohoInventory.inventoryadjustments.CREATE"
INVENTORY_ADJUSTMENT_PATH = "/inventory/v1/inventoryadjustments"
CENT = Decimal("0.01")
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
WITHDRAWN_PLAN_HASHES = {
    "fa5d1ab504f45993ea5d595f13575938ec1194a608b0ce61bcdd0171fbeb099b":
        "Superseded before approval because the Zoho line descriptions repeated the supplier name.",
}

ADJUSTMENT_DATE = "2026-08-11"
ADJUSTMENT_REASON = "Inventory Revaluation"
ADJUSTMENT_REASON_ID = "96274000000014310"
ADJUSTMENT_ACCOUNT_ID = "96274000000896100"
ADJUSTMENT_ACCOUNT_NAME = "Inventory Adjustment"
ADJUSTMENT_REFERENCE = "BACKING-RINGS-8-SIZES-2026-08-11"
ADJUSTMENT_DESCRIPTION = (
    "Tentative landed valuation for Rachad Homsi's 2026-08-11 physical backing-ring count: "
    "Fei supplier USD unit cost plus 20% landing allowance, converted at Bank of Canada "
    "2026-08-11 daily average 1 USD = CAD 1.3927. One item per nominal size; colour and OD "
    "remain intake provenance only. Replace the tentative basis in accounting records only "
    "through a separately commissioned correction when actual supplier/freight costs are final."
)
FX_DATE = "2026-08-11"
FX_RATE = Decimal("1.3927")
LANDING_FACTOR = Decimal("1.20")
TOTAL_QUANTITY = Decimal("713")
TOTAL_VALUE_CAD = Decimal("78816.51")

SOURCE_IMAGE = Path(
    r"C:\Users\TDI-service\AppData\Local\hermes\profiles\dado\cache\images\img_d4c402b494ec.jpeg"
)
SOURCE_IMAGE_SHA256 = "b85bab550eb2550703e4abec61460fa02ef524004ffdeefc235c5bfb386a47e3"
INTAKE_PATH = ROOT / "Dado" / "20_Working" / "packing_rings" / "packing_ring_stock_intake_20260811.json"
INTAKE_SHA256 = "d066d9b5f6c0da503930e7af3e1c827ce37d2fdcee8527bef94c6eb6472187b5"
CATALOG_BATCH_PATH = ROOT / "Dado" / "20_Working" / "packing_rings" / "packing_ring_colour_neutral_plan_batch_20260812.json"
CATALOG_BATCH_SHA256 = "59cddb5944828a09545ef0beb94059133aa82f4c414298ab514685c8b7880da4"
LIVE_CREATION_PATH = ROOT / "Dado" / "20_Working" / "packing_rings" / "packing_ring_eight_item_live_verification_20260812.json"
LIVE_CREATION_SHA256 = "b8657294acb538c8c242a5a8c8917cd6581412c8baaa9a1c4acb645c9fcd786e"
BOC_FX_PATH = ROOT / "Dado" / "20_Working" / "packing_rings" / "fx_sources" / "bank_of_canada_fxusdcad_2026-08-11.json"
BOC_FX_SHA256 = "7df4cf0beb129329a1488d7c409b8dd5b103009bba8dd2200df2b931f650bd9d"
FEI_MASTER = ROOT / "Dado" / "20_Working" / "packing_rings" / "fei_pricing_sources" / "Master Sheete-SKU COST -FRP JRAIN.xlsx"
FEI_MASTER_SHA256 = "9d88cfd8a7ae8d0bc256c49c51de921b6969f15607f288bbbc1ea15be8c082f2"
FEI_JANUARY = ROOT / "Dado" / "20_Working" / "packing_rings" / "fei_pricing_sources" / "Flange, Goose Neck and Manhole 2026.01.06.pdf"
FEI_JANUARY_SHA256 = "e728e66d83e62a2422458e43e6915e4d66fb362af9694da0f7435263eef0b0e7"

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

PLAN_FIELDS = {"schema_version", "tool", "tool_version", "action", "created_utc", "expires_utc", "nonce", "approval_required", "organization", "payload", "risk", "source_evidence", "live_evidence", "sha256"}
ORGANIZATION_FIELDS = {"organization_id", "name", "currency_code"}
PAYLOAD_FIELDS = {"date", "reason", "description", "reference_number", "adjustment_type", "line_items"}
LINE_FIELDS = {"item_id", "name", "description", "quantity_adjusted", "item_total", "unit", "adjustment_account_id"}
RISK_FIELDS = {"atomic", "write_count", "write_order", "note"}
SOURCE_FIELDS = {"physical_count", "supplier_prices", "landing_allowance", "exchange_rate", "calculation", "files"}
LIVE_FIELDS = {"items", "inventory_adjustment_reference_absent", "inventory_adjustment_reason", "inventory_adjustment_account"}

STOCK_FIELDS = {"stock_on_hand", "available_stock", "available_for_sale_stock", "actual_available_stock", "actual_available_for_sale_stock", "committed_stock", "actual_committed_stock", "initial_stock", "initial_stock_rate"}
VALUATION_DERIVED_FIELDS = {"asset_value", "asset_price", "average_cost", "purchase_price", "last_modified_time"}
UNPROTECTED_ITEM_FIELDS = STOCK_FIELDS | VALUATION_DERIVED_FIELDS
REQUIRED_STOCK_FIELDS = {"stock_on_hand", "available_stock", "available_for_sale_stock", "actual_available_stock", "actual_available_for_sale_stock", "committed_stock", "actual_committed_stock", "initial_stock", "initial_stock_rate"}

RISK_NOTE = (
    "ATOMIC AT THE REQUEST LEVEL: one POST creates one permanent eight-line positive Inventory "
    "Adjustment. The plan is locked before that request. A failure, timeout or indeterminate "
    "result leaves it permanently locked with no retry, rollback or cleanup. No item field, "
    "selling rate, purchase rate, order, invoice, website record or email is writable."
)


class EightBackingRingStockError(RuntimeError):
    """Fail-closed staging, validation, transport or verification failure."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(block)
    except OSError as exc:
        raise EightBackingRingStockError(f"Required source file is unreadable: {path}") from exc
    return hasher.hexdigest()


def read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EightBackingRingStockError(f"{label} is unreadable.") from exc


def json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise EightBackingRingStockError("Zoho returned non-JSON evidence.") from exc


def decimal_value(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise EightBackingRingStockError(f"{label} is not numeric.")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise EightBackingRingStockError(f"{label} is invalid.") from exc
    if not result.is_finite():
        raise EightBackingRingStockError(f"{label} must be finite.")
    return result


def money(value: Any, label: str) -> Decimal:
    return decimal_value(value, label).quantize(CENT, rounding=ROUND_HALF_UP)


def money_text(value: Any, label: str) -> str:
    return format(money(value, label), "f")


def positive_id(value: Any, label: str) -> str:
    text = str(value if value is not None else "")
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise EightBackingRingStockError(f"{label} must be a positive Zoho ID.")
    return text


def closed_fields(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        actual = set(value) if isinstance(value, dict) else set()
        raise EightBackingRingStockError(
            f"{label} must use the exact closed schema; missing={sorted(expected - actual)}, unsupported={sorted(actual - expected)}."
        )
    return value


def parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or value != value.strip():
        raise EightBackingRingStockError(f"{label} must be unpadded timestamp text.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EightBackingRingStockError(f"{label} is invalid.") from exc
    if parsed.tzinfo is None:
        raise EightBackingRingStockError(f"{label} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def exact_unit_cad(target: dict[str, Any]) -> Decimal:
    return target["supplier_usd"] * LANDING_FACTOR * FX_RATE


def line_total_cad(target: dict[str, Any]) -> Decimal:
    return (target["quantity"] * exact_unit_cad(target)).quantize(CENT, rounding=ROUND_HALF_UP)


def validate_arithmetic() -> None:
    if sum((row["quantity"] for row in TARGETS), Decimal("0")) != TOTAL_QUANTITY:
        raise EightBackingRingStockError("Fixed quantity total is inconsistent.")
    if sum((line_total_cad(row) for row in TARGETS), Decimal("0")) != TOTAL_VALUE_CAD:
        raise EightBackingRingStockError("Fixed CAD valuation total is inconsistent.")
    for row in TARGETS:
        if row["supplier_usd"] * Decimal("3.6") != row["sales_rate_cad"]:
            raise EightBackingRingStockError(f"Sales-rate provenance is inconsistent for {row['sku']}.")


def build_payload() -> dict[str, Any]:
    validate_arithmetic()
    return {
        "date": ADJUSTMENT_DATE,
        "reason": ADJUSTMENT_REASON,
        "description": ADJUSTMENT_DESCRIPTION,
        "reference_number": ADJUSTMENT_REFERENCE,
        "adjustment_type": "quantity",
        "line_items": [
            {
                "item_id": row["item_id"],
                "name": row["name"],
                "description": (
                    f"Tentative landed cost: Fei {row['price_source']} USD {row['supplier_usd']:.2f} "
                    f"+ 20% x Bank of Canada {FX_DATE} FX {FX_RATE} CAD/USD; "
                    f"exact unit basis CAD {exact_unit_cad(row):f}, line total CAD {line_total_cad(row):.2f}."
                ),
                "quantity_adjusted": int(row["quantity"]),
                "item_total": float(line_total_cad(row)),
                "unit": "pcs",
                "adjustment_account_id": ADJUSTMENT_ACCOUNT_ID,
            }
            for row in TARGETS
        ],
    }


def protected_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: json_copy(value) for key, value in item.items() if key not in UNPROTECTED_ITEM_FIELDS}


def stock_projection(item: dict[str, Any]) -> dict[str, str]:
    missing = sorted(REQUIRED_STOCK_FIELDS - set(item))
    if missing:
        raise EightBackingRingStockError("Zoho item is missing required stock fields: " + ", ".join(missing))
    return {key: money_text(item[key], f"item.{key}") for key in sorted(REQUIRED_STOCK_FIELDS)}


def validate_item(item: dict[str, Any], target: dict[str, Any], require_zero: bool) -> None:
    fixed = {
        "item_id": target["item_id"], "name": target["name"], "sku": target["sku"],
        "status": "active", "unit": "pcs", "item_type": "inventory",
        "product_type": "goods", "track_inventory": True, "is_combo_product": False,
    }
    for field, expected in fixed.items():
        if item.get(field) != expected:
            raise EightBackingRingStockError(
                f"Fixed item {target['item_id']} {field} changed: expected {expected!r}, got {item.get(field)!r}."
            )
    if money(item.get("rate"), "rate") != target["sales_rate_cad"]:
        raise EightBackingRingStockError(f"Fixed item {target['item_id']} sales rate changed.")
    if money(item.get("purchase_rate"), "purchase_rate") != Decimal("0.00"):
        raise EightBackingRingStockError(f"Fixed item {target['item_id']} purchase rate is no longer zero.")
    stock = stock_projection(item)
    if require_zero and any(decimal_value(stock[field], field) != 0 for field in REQUIRED_STOCK_FIELDS):
        raise EightBackingRingStockError(f"Fixed item {target['item_id']} no longer has zero stock/commitment.")


def verify_source_files() -> dict[str, Any]:
    expected = {
        SOURCE_IMAGE: SOURCE_IMAGE_SHA256,
        INTAKE_PATH: INTAKE_SHA256,
        CATALOG_BATCH_PATH: CATALOG_BATCH_SHA256,
        LIVE_CREATION_PATH: LIVE_CREATION_SHA256,
        BOC_FX_PATH: BOC_FX_SHA256,
        FEI_MASTER: FEI_MASTER_SHA256,
        FEI_JANUARY: FEI_JANUARY_SHA256,
    }
    files: dict[str, Any] = {}
    for path, wanted in expected.items():
        actual = sha256_file(path)
        if actual != wanted:
            raise EightBackingRingStockError(f"Source SHA-256 mismatch: {path}")
        files[str(path)] = {"sha256": actual, "bytes": path.stat().st_size}

    intake = read_json(INTAKE_PATH, "Packing-ring intake")
    counts = {str(row.get("size_in")): row.get("count") for row in intake.get("rows") or []}
    required_counts = {row["size_in"]: int(row["quantity"]) for row in TARGETS}
    if counts != {"1": 218, "1.5": 85, "2": 32, "3": 39, "4": 12, "6": 22, "8": 238, "10": 101, "12": 47, "14": 32}:
        raise EightBackingRingStockError("Intake quantities changed from the approved nominal-size merge.")
    if any(counts.get(size) != quantity for size, quantity in required_counts.items()):
        raise EightBackingRingStockError("Intake no longer proves all eight fixed quantities.")

    batch = read_json(CATALOG_BATCH_PATH, "Colour-neutral catalog batch")
    plans = batch.get("plans") or []
    if len(plans) != len(TARGETS):
        raise EightBackingRingStockError("Catalog batch no longer contains exactly eight plans.")
    for plan, target in zip(plans, TARGETS):
        checks = {
            "size_in": target["size_in"], "name": target["name"], "sku": target["sku"],
            "sheet_count": int(target["quantity"]), "supplier_usd_cost": format(target["supplier_usd"].normalize(), "f"),
            "sales_rate_cad": format(target["sales_rate_cad"], ".2f"),
        }
        for field, expected_value in checks.items():
            actual = str(plan.get(field)) if field == "supplier_usd_cost" else plan.get(field)
            expected = str(expected_value) if field == "supplier_usd_cost" else expected_value
            if actual != expected:
                raise EightBackingRingStockError(f"Catalog batch {field} changed for {target['sku']}.")

    creation = read_json(LIVE_CREATION_PATH, "Eight-item live creation verification")
    items = creation.get("items") or []
    if creation.get("status") != "verified" or creation.get("created_item_count") != 8 or len(items) != 8:
        raise EightBackingRingStockError("Creation artifact no longer proves eight verified items.")
    for item, target in zip(items, TARGETS):
        if (
            item.get("item_id") != target["item_id"] or item.get("sku") != target["sku"]
            or item.get("sheet_stock_pending_adjustment") != int(target["quantity"])
            or str(item.get("live_actual_available_stock")) != "0.00"
            or str(item.get("live_actual_committed_stock")) != "0.00"
        ):
            raise EightBackingRingStockError(f"Creation verification changed for {target['sku']}.")

    fx = read_json(BOC_FX_PATH, "Bank of Canada FX source")
    observations = fx.get("observations") or []
    detail = (fx.get("seriesDetail") or {}).get("FXUSDCAD") or {}
    if (
        len(observations) != 1 or observations[0].get("d") != FX_DATE
        or str((observations[0].get("FXUSDCAD") or {}).get("v")) != str(FX_RATE)
        or detail.get("label") != "USD/CAD"
        or "1 unit of US dollar" not in str(detail.get("description") or "")
    ):
        raise EightBackingRingStockError("Bank of Canada file does not prove the fixed USD/CAD rate and date.")
    return files


def get_item(token: str, domain: str, organization_id: str, item_id: str) -> dict[str, Any]:
    result = zoho_tool.api_get(
        token, domain,
        f"/inventory/v1/items/{positive_id(item_id, 'item_id')}?{urlencode({'organization_id': organization_id})}",
    )
    item = result.get("item") or {}
    if str(item.get("item_id") or "") != item_id:
        raise EightBackingRingStockError(f"Zoho did not return fixed item {item_id}.")
    return item


def inventory_organization(token: str, domain: str, expected_id: str) -> dict[str, str]:
    result = zoho_tool.api_get(token, domain, "/inventory/v1/organizations")
    matches = [row for row in result.get("organizations") or [] if str(row.get("organization_id") or "") == expected_id]
    if len(matches) != 1:
        raise EightBackingRingStockError("Zoho did not return exactly the saved FRP Depot organization.")
    row = matches[0]
    name = str(row.get("name") or row.get("organization_name") or "")
    currency = str(row.get("currency_code") or "")
    if "frpdepot" not in "".join(ch for ch in name.casefold() if ch.isalnum()) or currency != "CAD":
        raise EightBackingRingStockError("Saved organization is not FRP Depot in CAD.")
    return {"name": name, "currency_code": currency}


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
            EightBackingRingStockError,
        ):
            return rows
    raise EightBackingRingStockError("Adjustment duplicate scan exceeded 20,000 records.")


def require_reference_absent(token: str, domain: str, organization_id: str) -> None:
    if any(str(row.get("reference_number") or "") == ADJUSTMENT_REFERENCE for row in list_adjustments(token, domain, organization_id)):
        raise EightBackingRingStockError(f"REFUSED: Inventory Adjustment reference {ADJUSTMENT_REFERENCE} already exists.")


def build_sources(files: dict[str, Any]) -> dict[str, Any]:
    return {
        "physical_count": {
            "source": "Rachad Homsi handwritten stock sheet received 2026-08-11",
            "catalog_rule": "One generic 150 PSI/D411 item per nominal size; colour and OD retained only as intake provenance.",
            "quantities": {row["sku"]: int(row["quantity"]) for row in TARGETS},
            "total_quantity": int(TOTAL_QUANTITY),
        },
        "supplier_prices": {
            "status": "tentative, per Rachad Homsi 2026-08-11",
            "currency": "USD",
            "unit_costs": {row["sku"]: format(row["supplier_usd"], ".2f") for row in TARGETS},
            "provenance": {row["sku"]: row["price_source"] for row in TARGETS},
        },
        "landing_allowance": {
            "source": "Rachad Homsi 2026-08-11",
            "percentage": "20.00%",
            "factor": str(LANDING_FACTOR),
            "treatment": "Separate tentative freight/duty/landing allowance; not selling markup and not currency conversion.",
        },
        "exchange_rate": {
            "source": "Bank of Canada Valet API FXUSDCAD daily average",
            "date": FX_DATE,
            "basis": "1 USD in CAD",
            "rate": str(FX_RATE),
        },
        "calculation": {
            "formula": "line CAD = quantity x Fei USD unit cost x 1.20 x 1.3927; ROUND_HALF_UP once per line to CAD 0.01",
            "lines": [
                {
                    "sku": row["sku"], "quantity": int(row["quantity"]),
                    "supplier_usd_unit": format(row["supplier_usd"], ".2f"),
                    "landing_allowance_usd_unit": format(row["supplier_usd"] * Decimal("0.20"), ".2f"),
                    "landed_usd_unit": format(row["supplier_usd"] * LANDING_FACTOR, ".2f"),
                    "exact_landed_cad_unit": format(exact_unit_cad(row), "f"),
                    "display_landed_cad_unit": money_text(exact_unit_cad(row), "unit"),
                    "line_total_cad": money_text(line_total_cad(row), "line"),
                }
                for row in TARGETS
            ],
            "total_cad": money_text(TOTAL_VALUE_CAD, "total"),
            "rounding_note": "Exact converted unit basis is retained through multiplication; only each posted CAD line total is rounded once.",
        },
        "files": files,
    }


def validate_payload(payload: Any) -> dict[str, Any]:
    payload = closed_fields(payload, PAYLOAD_FIELDS, "payload")
    if canonical(payload) != canonical(build_payload()):
        raise EightBackingRingStockError("Plan payload is not the one fixed commissioned payload.")
    lines = payload["line_items"]
    if not isinstance(lines, list) or len(lines) != 8:
        raise EightBackingRingStockError("Fixed payload must contain exactly eight lines.")
    for index, line in enumerate(lines):
        closed_fields(line, LINE_FIELDS, f"line {index + 1}")
    if [str(line["item_id"]) for line in lines] != list(TARGET_IDS):
        raise EightBackingRingStockError("Fixed payload item identity/order changed.")
    return payload


def validate_plan(plan: Any) -> dict[str, Any]:
    plan = closed_fields(plan, PLAN_FIELDS, "plan")
    saved_hash = plan["sha256"]
    if not isinstance(saved_hash, str) or not HEX_64_RE.fullmatch(saved_hash):
        raise EightBackingRingStockError("Plan SHA-256 is malformed.")
    unsigned = dict(plan)
    unsigned.pop("sha256")
    if digest_for(unsigned) != saved_hash:
        raise EightBackingRingStockError("Plan hash check failed; the staged plan changed.")
    if plan["schema_version"] != SCHEMA_VERSION or plan["tool"] != TOOL_NAME or plan["tool_version"] != TOOL_VERSION or plan["action"] != ACTION:
        raise EightBackingRingStockError("Plan belongs to another tool/version/action.")
    if plan["approval_required"] != APPROVAL_WORD:
        raise EightBackingRingStockError("Plan approval requirement changed.")
    if not isinstance(plan["nonce"], str) or not NONCE_RE.fullmatch(plan["nonce"]):
        raise EightBackingRingStockError("Plan nonce is malformed.")
    created = parse_utc(plan["created_utc"], "created_utc")
    expires = parse_utc(plan["expires_utc"], "expires_utc")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise EightBackingRingStockError("Plan lifetime is not exactly 24 hours.")
    organization = closed_fields(plan["organization"], ORGANIZATION_FIELDS, "organization")
    positive_id(organization["organization_id"], "organization ID")
    if organization["currency_code"] != "CAD":
        raise EightBackingRingStockError("Plan organization currency is not CAD.")
    validate_payload(plan["payload"])
    risk = closed_fields(plan["risk"], RISK_FIELDS, "risk")
    if risk != {"atomic": True, "write_count": 1, "write_order": ["POST eight-line Inventory Adjustment"], "note": RISK_NOTE}:
        raise EightBackingRingStockError("Plan risk disclosure changed.")
    closed_fields(plan["source_evidence"], SOURCE_FIELDS, "source_evidence")
    if plan["source_evidence"] != build_sources(plan["source_evidence"]["files"]):
        raise EightBackingRingStockError("Plan source evidence changed.")
    live = closed_fields(plan["live_evidence"], LIVE_FIELDS, "live_evidence")
    if live["inventory_adjustment_reference_absent"] is not True:
        raise EightBackingRingStockError("Plan did not prove adjustment reference absent.")
    if live["inventory_adjustment_reason"] != {"reason": ADJUSTMENT_REASON, "reason_id": ADJUSTMENT_REASON_ID}:
        raise EightBackingRingStockError("Adjustment reason evidence changed.")
    if live["inventory_adjustment_account"] != {"name": ADJUSTMENT_ACCOUNT_NAME, "account_id": ADJUSTMENT_ACCOUNT_ID}:
        raise EightBackingRingStockError("Adjustment account evidence changed.")
    items = live["items"]
    if not isinstance(items, list) or len(items) != 8 or [str(row.get("item_id") or "") for row in items] != list(TARGET_IDS):
        raise EightBackingRingStockError("Plan live item evidence identity/order changed.")
    return plan


def write_plan(core: dict[str, Any]) -> Path:
    plan = dict(core)
    plan["sha256"] = digest_for(core)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{ACTION}_{plan['sha256'][:12]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt("zoho_eight_backing_ring_stock_plan_staged", str(path))
    return path


def load_plan(path_text: str) -> dict[str, Any]:
    path = Path(path_text).expanduser().resolve()
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EightBackingRingStockError(f"Plan is unreadable: {path}") from exc
    saved_hash = plan.get("sha256") if isinstance(plan, dict) else None
    if saved_hash in WITHDRAWN_PLAN_HASHES:
        raise EightBackingRingStockError(
            "REFUSED: this staged plan was withdrawn before approval. "
            + WITHDRAWN_PLAN_HASHES[saved_hash]
        )
    return validate_plan(plan)


# Shared owner authority (autonomy programme 2026-08-21, spec A3/A4/A5). An
# Inventory Adjustment is a financial record, so this stays MONEY work: stage,
# then his own unambiguous go to THAT plan, sent after the plan was written.
# Exact APPROVED is no longer REQUIRED; a failed commit is reported and
# re-staged; nothing is permanently locked. The approved plan of 2026-08-11 is
# spent and stays spent; its on-disk lock is the record the valuation
# correction tool reads and is not rewritten.
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
    owner_authority.refuse_replay(EightBackingRingStockError, {"status": _STATE_TO_STATUS.get(state, state)},
                                  what="eight-item backing-ring plan")


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
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def update_lock(path: Path, state: str, details: dict[str, Any]) -> None:
    current = json.loads(path.read_text(encoding="utf-8"))
    current.update({"state": state, "updated_utc": utc_now().isoformat(), "details": details})
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_scope_before_lock() -> dict[str, Any]:
    vault = zoho_tool.load_vault()
    if INVENTORY_ADJUSTMENT_CREATE_SCOPE not in set(vault.get("scopes") or []):
        raise EightBackingRingStockError(
            "REFUSED BEFORE LOCK: saved Zoho connection lacks ZohoInventory.inventoryadjustments.CREATE. "
            "Run PREPARE_DADO_ZOHO_ACCESS.bat, create the grant, then REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat."
        )
    return vault


def request_result(request: Request) -> dict[str, Any]:
    """The sole live write call site; the caller validates its route and body."""
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise EightBackingRingStockError(f"Zoho write failed with HTTP {exc.code}: {detail}; this plan needs re-stage.") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise EightBackingRingStockError(f"Zoho write result is indeterminate: {exc}; this plan needs re-stage.") from exc
    if result.get("code") not in (None, 0):
        raise EightBackingRingStockError(f"Zoho write failed: {result.get('message') or result.get('code')}; this plan needs re-stage.")
    return result


def perform_create(token: str, domain: str, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if canonical(payload) != canonical(build_payload()):
        raise EightBackingRingStockError("REFUSED: payload is not the one fixed eight-line Inventory Adjustment.")
    query = urlencode({"organization_id": organization_id})
    request = Request(
        domain.rstrip("/") + INVENTORY_ADJUSTMENT_PATH + "?" + query,
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
    raise EightBackingRingStockError("Inventory Adjustment response lacks a verifiable ID; result is indeterminate and locked.")


def verify_adjustment(token: str, domain: str, organization_id: str, adjustment_id: str) -> dict[str, Any]:
    result = zoho_tool.api_get(
        token, domain,
        f"/inventory/v1/inventoryadjustments/{positive_id(adjustment_id, 'adjustment ID')}?{urlencode({'organization_id': organization_id})}",
    )
    adjustment = result.get("inventory_adjustment") or {}
    fixed = {
        "inventory_adjustment_id": adjustment_id, "date": ADJUSTMENT_DATE,
        "reason": ADJUSTMENT_REASON, "reason_id": ADJUSTMENT_REASON_ID,
        "description": ADJUSTMENT_DESCRIPTION, "reference_number": ADJUSTMENT_REFERENCE,
        "adjustment_type": "quantity", "status": "adjusted",
    }
    for field, expected in fixed.items():
        if str(adjustment.get(field) if adjustment.get(field) is not None else "") != str(expected):
            raise EightBackingRingStockError(f"Adjustment verification failed for {field}.")
    lines = adjustment.get("line_items") or []
    if len(lines) != 8 or [str(line.get("item_id") or "") for line in lines] != list(TARGET_IDS):
        raise EightBackingRingStockError("Adjustment line identity/order differs from the plan.")
    for line, expected_line, target in zip(lines, build_payload()["line_items"], TARGETS):
        for field in ("name", "description", "unit", "adjustment_account_id"):
            if str(line.get(field) or "") != str(expected_line[field]):
                raise EightBackingRingStockError(f"Adjustment line {target['item_id']} {field} mismatch.")
        if decimal_value(line.get("quantity_adjusted"), "quantity") != target["quantity"]:
            raise EightBackingRingStockError("Adjustment quantity mismatch.")
        if money(line.get("item_total"), "item_total") != line_total_cad(target):
            raise EightBackingRingStockError("Adjustment line valuation mismatch.")
    if money(adjustment.get("total"), "adjustment total") != TOTAL_VALUE_CAD:
        raise EightBackingRingStockError("Adjustment total mismatch.")
    if adjustment.get("is_inventory_valuation_pending") is not False:
        raise EightBackingRingStockError("Zoho reports inventory valuation pending; result is indeterminate.")
    return {"inventory_adjustment_id": adjustment_id, "status": "adjusted", "reference_number": ADJUSTMENT_REFERENCE, "total_cad": money_text(adjustment.get("total"), "total")}


def verify_item_after(item: dict[str, Any], evidence: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    validate_item(item, target, require_zero=False)
    if protected_item(item) != evidence["protected_item"]:
        raise EightBackingRingStockError(f"Protected fields changed on item {target['item_id']}.")
    before = evidence["stock_before"]
    after = stock_projection(item)
    quantity = target["quantity"]
    increased = {"stock_on_hand", "available_stock", "available_for_sale_stock", "actual_available_stock", "actual_available_for_sale_stock"}
    unchanged = {"committed_stock", "actual_committed_stock", "initial_stock", "initial_stock_rate"}
    for field in increased:
        if decimal_value(after[field], field) != decimal_value(before[field], field) + quantity:
            raise EightBackingRingStockError(f"Item {target['item_id']} {field} did not increase by {quantity}.")
    for field in unchanged:
        if decimal_value(after[field], field) != decimal_value(before[field], field):
            raise EightBackingRingStockError(f"Item {target['item_id']} {field} changed unexpectedly.")
    return {"item_id": target["item_id"], "sku": target["sku"], "sales_rate_cad": money_text(item.get("rate"), "rate"), "purchase_rate_cad": money_text(item.get("purchase_rate"), "purchase rate"), "stock_before": before, "stock_after": after}


def command_stage(_: argparse.Namespace) -> None:
    files = verify_source_files()
    vault = zoho_tool.load_vault()
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = str(vault.get("api_domain") or "")
    organization_id = positive_id(vault.get("inventory_organization_id"), "inventory organization ID")
    organization = inventory_organization(token, domain, organization_id)
    require_reference_absent(token, domain, organization_id)
    item_evidence = []
    for target in TARGETS:
        item = get_item(token, domain, organization_id, target["item_id"])
        validate_item(item, target, require_zero=True)
        item_evidence.append({
            "item_id": target["item_id"], "name": target["name"], "sku": target["sku"],
            "sales_rate": money_text(item.get("rate"), "rate"),
            "purchase_rate": money_text(item.get("purchase_rate"), "purchase rate"),
            "quantity_adjusted": money_text(target["quantity"], "quantity"),
            "item_total_cad": money_text(line_total_cad(target), "item total"),
            "stock_before": stock_projection(item),
            "protected_item": protected_item(item),
            "protected_item_sha256": digest_for(protected_item(item)),
        })
    zoho_tool.save_vault(vault)
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "action": ACTION, "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16), "approval_required": APPROVAL_WORD,
        "organization": {"organization_id": organization_id, "name": organization["name"], "currency_code": organization["currency_code"]},
        "payload": build_payload(),
        "risk": {"atomic": True, "write_count": 1, "write_order": ["POST eight-line Inventory Adjustment"], "note": RISK_NOTE},
        "source_evidence": build_sources(files),
        "live_evidence": {
            "items": item_evidence, "inventory_adjustment_reference_absent": True,
            "inventory_adjustment_reason": {"reason": ADJUSTMENT_REASON, "reason_id": ADJUSTMENT_REASON_ID},
            "inventory_adjustment_account": {"name": ADJUSTMENT_ACCOUNT_NAME, "account_id": ADJUSTMENT_ACCOUNT_ID},
        },
    }
    path = write_plan(core)
    plan = load_plan(str(path))
    print(json.dumps({
        "status": "STAGED ONLY - ZERO ZOHO WRITES", "plan": str(path), "sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"], "approval_required": APPROVAL_WORD,
        "scope_ready": INVENTORY_ADJUSTMENT_CREATE_SCOPE in set(vault.get("scopes") or []),
        "risk": plan["risk"], "total_quantity": int(TOTAL_QUANTITY),
        "total_value_cad": money_text(TOTAL_VALUE_CAD, "total"),
        "calculation": plan["source_evidence"]["calculation"],
        "inventory_adjustment": plan["payload"],
    }, ensure_ascii=False, indent=2))


def command_commit(args: argparse.Namespace) -> None:
    plan = load_plan(args.plan)
    # His go is checked before the vault and the network (A3): his own
    # unambiguous go to THIS plan, after it was written (the message time is required).
    try:
        go = owner_authority.require_owner_go_after_plan(
            args.approval, plan_created_utc=plan.get("created_utc"), plan_expires_utc=plan.get("expires_utc"),
            sent_utc=getattr(args, "approval_message_utc", None), lane=getattr(args, "approval_lane", None),
            what="this eight-item backing-ring stock plan",
        )
    except owner_authority.OwnerAuthorityRefused as exc:
        raise EightBackingRingStockError(str(exc)) from exc
    if utc_now() > parse_utc(plan["expires_utc"], "expires_utc"):
        raise EightBackingRingStockError("REFUSED: staged plan expired; stage a fresh read-only plan.")
    if lock_path(plan).exists():
        refuse_existing_lock(lock_path(plan))
    vault = require_scope_before_lock()
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = str(vault.get("api_domain") or "")
    organization_id = positive_id(vault.get("inventory_organization_id"), "inventory organization ID")
    if organization_id != plan["organization"]["organization_id"]:
        raise EightBackingRingStockError("REFUSED BEFORE LOCK: saved organization differs from plan.")
    organization = inventory_organization(token, domain, organization_id)
    if organization["name"] != plan["organization"]["name"] or organization["currency_code"] != "CAD":
        raise EightBackingRingStockError("REFUSED BEFORE LOCK: live organization differs from plan.")
    require_reference_absent(token, domain, organization_id)
    for evidence, target in zip(plan["live_evidence"]["items"], TARGETS):
        item = get_item(token, domain, organization_id, target["item_id"])
        validate_item(item, target, require_zero=True)
        if stock_projection(item) != evidence["stock_before"] or protected_item(item) != evidence["protected_item"]:
            raise EightBackingRingStockError("REFUSED BEFORE LOCK: item state changed since staging; stage a fresh plan.")
    zoho_tool.save_vault(vault)
    lock = acquire_lock(plan, go)
    adjustment_id = ""
    try:
        result = perform_create(token, domain, organization_id, plan["payload"])
        adjustment_id = adjustment_id_from_response(result)
        adjustment = verify_adjustment(token, domain, organization_id, adjustment_id)
        items = []
        for evidence, target in zip(plan["live_evidence"]["items"], TARGETS):
            item = get_item(token, domain, organization_id, target["item_id"])
            items.append(verify_item_after(item, evidence, target))
        details = {"inventory_adjustment": adjustment, "items": items, "completed_writes": ["inventory adjustment " + adjustment_id], "item_writes": 0, "website_writes": 0, "emails_sent": 0}
        update_lock(lock, "verified", details)
        zoho_tool.append_receipt("zoho_eight_backing_ring_stock_verified", str(lock))
        print(json.dumps({"status": "COMMITTED AND VERIFIED", "details": details, "lock": str(lock)}, ensure_ascii=False, indent=2))
    except Exception as exc:
        update_lock(lock, "indeterminate", {
            "error": str(exc), "inventory_adjustment_id": adjustment_id or None, "permanent_lock": False,
            "guidance": owner_authority.explain_outcome(
                "The eight-item stock load", owner_authority.STATUS_INDETERMINATE,
                "Reconcile with fresh read-only Zoho reads; the re-stage's reference check refuses a "
                "second adjustment if the first one landed.", money=True,
            ),
        })
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage", help="Read live state and stage the fixed plan; no writes.")
    stage.set_defaults(func=command_stage)
    commit = sub.add_parser("commit", help="Commit one immutable staged plan once.")
    commit.add_argument("--plan", required=True)
    owner_authority.add_owner_go_arguments(commit, money=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.func(args)
        return 0
    except (EightBackingRingStockError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
