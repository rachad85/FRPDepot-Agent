#!/usr/bin/env python
"""One fixed FRP Depot backing-ring stock and sales-rate merge.

Commissioned by Rachad Homsi on 2026-08-11. Building, testing and staging are
not approval of a live Zoho write.

This module has one action and no parameterised business surface:

* create one positive quantity Inventory Adjustment dated 2026-08-11 for the
  two existing generic backing-ring items only: +12 of 4-inch item
  96274000001518002 and +101 of 10-inch item 96274000001518014;
* preserve those item IDs so INV-000051 / SO-00050 continues to consume them;
* update only their future sales rates to CAD 108.00 and CAD 468.00 using the
  supplier-USD-cost x 3.6 rule.

The writes are deliberately NOT atomic: one Inventory Adjustment POST is
followed by two item-rate PUTs. The adjustment is first because retaining the
physical-stock merge is the primary business objective. Any failure or
indeterminate result permanently locks the plan. There is no retry, rollback,
delete, adjustment update, invoice/order write, status/approval route, email,
attachment, batch route, generic item route, browser path, or WooCommerce path.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
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
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import zoho_tool

TOOL_NAME = "FRP Depot Fixed Backing Ring Stock and Rate Merge Tool"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = 1
ACTION = "backing_ring_stock_and_rate_merge"
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_backing_ring_stock_plans"
LOCK_DIR = PLAN_DIR / ".commit-locks"
PLAN_LIFETIME_HOURS = 24
APPROVAL_WORD = "APPROVED"
INVENTORY_ADJUSTMENT_CREATE_SCOPE = "ZohoInventory.inventoryadjustments.CREATE"
ITEM_UPDATE_SCOPE = "ZohoInventory.items.UPDATE"
INVENTORY_ADJUSTMENT_PATH = "/inventory/v1/inventoryadjustments"
ITEM_PATH_RE = re.compile(r"^/inventory/v1/items/([1-9][0-9]*)$")
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
CENT = Decimal("0.01")

SOURCE_IMAGE = Path(
    r"C:\Users\TDI-service\AppData\Local\hermes\profiles\dado\cache\images\img_d4c402b494ec.jpeg"
)
SOURCE_IMAGE_SHA256 = "b85bab550eb2550703e4abec61460fa02ef524004ffdeefc235c5bfb386a47e3"
INTAKE_PATH = ROOT / "Dado" / "20_Working" / "packing_rings" / "packing_ring_stock_intake_20260811.json"
FEI_MASTER = ROOT / "Dado" / "20_Working" / "packing_rings" / "fei_pricing_sources" / "Master Sheete-SKU COST -FRP JRAIN.xlsx"
FEI_MASTER_SHA256 = "9d88cfd8a7ae8d0bc256c49c51de921b6969f15607f288bbbc1ea15be8c082f2"
FEI_JANUARY = ROOT / "Dado" / "20_Working" / "packing_rings" / "fei_pricing_sources" / "Flange, Goose Neck and Manhole 2026.01.06.pdf"
FEI_JANUARY_SHA256 = "e728e66d83e62a2422458e43e6915e4d66fb362af9694da0f7435263eef0b0e7"

INVOICE_ID = "96274000001559012"
INVOICE_NUMBER = "INV-000051"
INVOICE_REFERENCE = "SO-00050"
ADJUSTMENT_DATE = "2026-08-11"
ADJUSTMENT_REASON = "Inventory Revaluation"
ADJUSTMENT_REASON_ID = "96274000000014310"
ADJUSTMENT_ACCOUNT_ID = "96274000000896100"
ADJUSTMENT_ACCOUNT_NAME = "Inventory Adjustment"
ADJUSTMENT_REFERENCE = "BACKING-RINGS-2026-08-11"
ADJUSTMENT_DESCRIPTION = (
    "Physical count from Rachad Homsi's 2026-08-11 FRP backing-ring stock sheet; "
    "4-inch black and white merged into the existing generic 4-inch item, and both "
    "10-inch rows merged into the existing generic 10-inch item."
)

TARGETS = (
    {
        "item_id": "96274000001518002",
        "name": 'FRP BACKING RING-4"/150PSI/D411',
        "sku": "BRDN100150PSI411",
        "quantity_adjusted": Decimal("12"),
        "sheet_rows": (("WHITE", 4), ("BLACK", 8)),
        "purchase_rate_cad": Decimal("58.00"),
        "item_total_cad": Decimal("696.00"),
        "supplier_cost_usd": Decimal("30.00"),
        "target_rate_cad": Decimal("108.00"),
        "invoice_line_item_id": "96274000001559019",
        "invoice_quantity": Decimal("24"),
        "invoice_rate_cad": Decimal("97.00"),
    },
    {
        "item_id": "96274000001518014",
        "name": 'FRP BACKING RING-10"/150PSI/D411',
        "sku": "BRDN250150PSI411",
        "quantity_adjusted": Decimal("101"),
        "sheet_rows": (("BLACK", 83), ("BLACK", 18)),
        "purchase_rate_cad": Decimal("175.50"),
        "item_total_cad": Decimal("17725.50"),
        "supplier_cost_usd": Decimal("130.00"),
        "target_rate_cad": Decimal("468.00"),
        "invoice_line_item_id": "96274000001559020",
        "invoice_quantity": Decimal("36"),
        "invoice_rate_cad": Decimal("297.00"),
    },
)
TARGET_BY_ID = {row["item_id"]: row for row in TARGETS}
TARGET_IDS = tuple(row["item_id"] for row in TARGETS)

PLAN_FIELDS = {
    "schema_version", "tool", "tool_version", "action", "created_utc", "expires_utc",
    "nonce", "approval_required", "organization", "payload", "risk", "source_evidence",
    "live_evidence", "sha256",
}
ORGANIZATION_FIELDS = {
    "inventory_organization_id", "books_organization_id", "name", "currency_code",
}
PAYLOAD_FIELDS = {"inventory_adjustment", "rate_updates"}
ADJUSTMENT_FIELDS = {
    "date", "reason", "description", "reference_number", "adjustment_type", "line_items",
}
ADJUSTMENT_LINE_FIELDS = {
    "item_id", "name", "description", "quantity_adjusted", "item_total", "unit",
    "adjustment_account_id",
}
RATE_UPDATE_FIELDS = {"item_id", "name", "rate"}
RISK_FIELDS = {"atomic", "write_count", "write_order", "note"}
SOURCE_FIELDS = {"stock_sheet", "prices", "valuation", "order_link"}
LIVE_FIELDS = {
    "items", "invoice_projection", "inventory_adjustment_reference_absent",
    "inventory_adjustment_reason", "inventory_adjustment_account",
}

RATE_FAMILY_FIELDS = {
    "rate", "sales_rate", "pricebook_rate", "default_price_brackets", "sales_margin",
}
STOCK_FIELDS = {
    "stock_on_hand", "available_stock", "available_for_sale_stock",
    "actual_available_stock", "actual_available_for_sale_stock", "committed_stock",
    "actual_committed_stock", "initial_stock", "initial_stock_rate",
}
VOLATILE_FIELDS = {"last_modified_time"}
UNPROTECTED_ITEM_FIELDS = RATE_FAMILY_FIELDS | STOCK_FIELDS | VOLATILE_FIELDS
REQUIRED_STOCK_FIELDS = {
    "stock_on_hand", "available_stock", "available_for_sale_stock",
    "actual_available_stock", "actual_available_for_sale_stock", "committed_stock",
    "actual_committed_stock",
}

RISK_NOTE = (
    "NOT ATOMIC: write 1 creates one permanent positive Inventory Adjustment for both fixed "
    "items; writes 2 and 3 update only the preserved item name plus future sales rate. A "
    "failure or indeterminate result permanently locks the plan. Earlier successful writes "
    "remain in Zoho. No retry, rollback, deletion, cleanup or status change exists."
)


class BackingRingToolError(RuntimeError):
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
        raise BackingRingToolError(f"Required source file is unreadable: {path}") from exc
    return hasher.hexdigest()


def json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise BackingRingToolError("Zoho returned non-JSON evidence.") from exc


def positive_id(value: Any, label: str) -> str:
    text = str(value if value is not None else "")
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise BackingRingToolError(f"{label} must be a positive Zoho ID.")
    return text


def decimal_value(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise BackingRingToolError(f"{label} is not numeric.")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise BackingRingToolError(f"{label} is not a valid number.") from exc
    if not result.is_finite():
        raise BackingRingToolError(f"{label} must be finite.")
    return result


def decimal_text(value: Any, label: str) -> str:
    return format(decimal_value(value, label).quantize(CENT), "f")


def closed_fields(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BackingRingToolError(f"{label} must be an object.")
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise BackingRingToolError(
            f"{label} must use the exact closed schema; missing={missing}, unsupported={extra}."
        )
    return value


def parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or value != value.strip():
        raise BackingRingToolError(f"{label} must be unpadded UTC text.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BackingRingToolError(f"{label} is not a valid timestamp.") from exc
    if parsed.tzinfo is None:
        raise BackingRingToolError(f"{label} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def protected_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: json_copy(value)
        for key, value in item.items()
        if key not in UNPROTECTED_ITEM_FIELDS
    }


def stock_projection(item: dict[str, Any]) -> dict[str, str]:
    missing = sorted(REQUIRED_STOCK_FIELDS - set(item))
    if missing:
        raise BackingRingToolError("Zoho item is missing required stock fields: " + ", ".join(missing))
    return {key: decimal_text(item.get(key), f"item.{key}") for key in sorted(STOCK_FIELDS) if key in item}


def invoice_projection(invoice: dict[str, Any]) -> dict[str, Any]:
    lines = []
    for line in invoice.get("line_items") or []:
        lines.append(
            {
                "line_item_id": str(line.get("line_item_id") or ""),
                "item_id": str(line.get("item_id") or ""),
                "name": str(line.get("name") or ""),
                "sku": str(line.get("sku") or ""),
                "quantity": decimal_text(line.get("quantity"), "invoice quantity"),
                "rate": decimal_text(line.get("rate"), "invoice rate"),
            }
        )
    return {
        "invoice_id": str(invoice.get("invoice_id") or ""),
        "invoice_number": str(invoice.get("invoice_number") or ""),
        "reference_number": str(invoice.get("reference_number") or ""),
        "customer_id": str(invoice.get("customer_id") or ""),
        "customer_name": str(invoice.get("customer_name") or ""),
        "status": str(invoice.get("status") or ""),
        "line_items": lines,
    }


def get_item(token: str, domain: str, organization_id: str, item_id: str) -> dict[str, Any]:
    result = zoho_tool.api_get(
        token,
        domain,
        f"/inventory/v1/items/{positive_id(item_id, 'item_id')}?{urlencode({'organization_id': organization_id})}",
    )
    item = result.get("item") or {}
    if str(item.get("item_id") or "") != item_id:
        raise BackingRingToolError(f"Zoho did not return fixed item {item_id}.")
    return item


def get_invoice(token: str, domain: str, books_organization_id: str) -> dict[str, Any]:
    result = zoho_tool.api_get(
        token,
        domain,
        f"/books/v3/invoices/{INVOICE_ID}?{urlencode({'organization_id': books_organization_id})}",
    )
    invoice = result.get("invoice") or {}
    if str(invoice.get("invoice_id") or "") != INVOICE_ID:
        raise BackingRingToolError(f"Zoho did not return fixed invoice {INVOICE_ID}.")
    return invoice


def inventory_organization(token: str, domain: str, expected_id: str) -> dict[str, str]:
    result = zoho_tool.api_get(token, domain, "/inventory/v1/organizations")
    matches = [
        row for row in result.get("organizations") or []
        if str(row.get("organization_id") or "") == expected_id
    ]
    if len(matches) != 1:
        raise BackingRingToolError("Zoho did not return exactly the saved FRP Depot Inventory organization.")
    row = matches[0]
    name = str(row.get("name") or row.get("organization_name") or "")
    currency = str(row.get("currency_code") or "")
    if "frpdepot" not in "".join(ch for ch in name.casefold() if ch.isalnum()):
        raise BackingRingToolError("Saved Inventory organization is not named FRP Depot.")
    if currency != "CAD":
        raise BackingRingToolError(f"Inventory organization currency must be CAD, not {currency!r}.")
    return {"name": name, "currency_code": currency}


def list_adjustments(token: str, domain: str, organization_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(1, 101):
        query = urlencode({"organization_id": organization_id, "page": page, "per_page": 200})
        result = zoho_tool.api_get(token, domain, f"/inventory/v1/inventoryadjustments?{query}")
        rows.extend(result.get("inventory_adjustments") or [])
        if not (result.get("page_context") or {}).get("has_more_page"):
            return rows
    raise BackingRingToolError("Inventory Adjustment duplicate scan exceeded 20,000 records.")


def require_reference_absent(token: str, domain: str, organization_id: str) -> None:
    matches = [
        row for row in list_adjustments(token, domain, organization_id)
        if str(row.get("reference_number") or "") == ADJUSTMENT_REFERENCE
    ]
    if matches:
        raise BackingRingToolError(
            f"REFUSED: Inventory Adjustment reference {ADJUSTMENT_REFERENCE} already exists; no duplicate is allowed."
        )


def verify_source_files() -> dict[str, Any]:
    expected = {
        SOURCE_IMAGE: SOURCE_IMAGE_SHA256,
        FEI_MASTER: FEI_MASTER_SHA256,
        FEI_JANUARY: FEI_JANUARY_SHA256,
    }
    evidence: dict[str, Any] = {}
    for path, wanted in expected.items():
        actual = sha256_file(path)
        if actual != wanted:
            raise BackingRingToolError(f"Source SHA-256 mismatch: {path}")
        evidence[str(path)] = {"sha256": actual, "bytes": path.stat().st_size}
    try:
        intake = json.loads(INTAKE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackingRingToolError("Packing-ring intake artifact is unreadable.") from exc
    rows = intake.get("rows") or []
    observed: dict[tuple[str, str, Any], list[int]] = {}
    for row in rows:
        key = (str(row.get("size_in")), str(row.get("colour")), row.get("side_note"))
        observed.setdefault(key, []).append(row.get("count"))
    if observed.get(("4", "WHITE", None)) != [4] or observed.get(("4", "BLACK", None)) != [8]:
        raise BackingRingToolError("Intake artifact no longer proves 4-inch counts 4 white + 8 black.")
    if observed.get(("10", "BLACK", None)) != [83, 18]:
        raise BackingRingToolError("Intake artifact no longer proves two 10-inch black counts 83 + 18.")
    evidence[str(INTAKE_PATH)] = {
        "sha256": sha256_file(INTAKE_PATH),
        "verified_counts": {
            "4_inch_white": 4,
            "4_inch_black": 8,
            "10_inch_black_rows": [83, 18],
        },
    }
    return evidence


def validate_fixed_item(item: dict[str, Any], target: dict[str, Any]) -> None:
    fixed = {
        "item_id": target["item_id"],
        "name": target["name"],
        "sku": target["sku"],
        "status": "active",
        "unit": "pcs",
        "item_type": "inventory",
        "product_type": "goods",
        "track_inventory": True,
        "is_combo_product": False,
    }
    for field, expected in fixed.items():
        if item.get(field) != expected:
            raise BackingRingToolError(
                f"Fixed item {target['item_id']} {field} changed: expected {expected!r}, got {item.get(field)!r}."
            )
    if decimal_value(item.get("purchase_rate"), "purchase_rate") != target["purchase_rate_cad"]:
        raise BackingRingToolError(f"Fixed item {target['item_id']} purchase rate changed.")
    if decimal_value(item.get("rate"), "rate") != target["invoice_rate_cad"]:
        raise BackingRingToolError(
            f"Fixed item {target['item_id']} no longer has the diagnosed starting sales rate."
        )
    if target["quantity_adjusted"] * target["purchase_rate_cad"] != target["item_total_cad"]:
        raise BackingRingToolError("Fixed inventory valuation arithmetic is inconsistent.")
    if target["supplier_cost_usd"] * Decimal("3.6") != target["target_rate_cad"]:
        raise BackingRingToolError("Fixed sales-rate multiplier arithmetic is inconsistent.")
    stock_projection(item)


def validate_fixed_invoice(projection: dict[str, Any]) -> None:
    if projection["invoice_id"] != INVOICE_ID or projection["invoice_number"] != INVOICE_NUMBER:
        raise BackingRingToolError("Fixed invoice identity changed.")
    if projection["reference_number"] != INVOICE_REFERENCE:
        raise BackingRingToolError("Fixed invoice reference changed.")
    lines = projection["line_items"]
    for target in TARGETS:
        matches = [line for line in lines if line["line_item_id"] == target["invoice_line_item_id"]]
        if len(matches) != 1:
            raise BackingRingToolError(f"Fixed invoice line {target['invoice_line_item_id']} is missing or duplicated.")
        line = matches[0]
        if line["item_id"] != target["item_id"] or line["sku"] != target["sku"]:
            raise BackingRingToolError("Fixed invoice no longer links to the existing generic item ID/SKU.")
        if decimal_value(line["quantity"], "invoice quantity") != target["invoice_quantity"]:
            raise BackingRingToolError("Fixed invoice quantity changed.")
        if decimal_value(line["rate"], "invoice rate") != target["invoice_rate_cad"]:
            raise BackingRingToolError("Fixed invoice historical line rate changed.")


def build_payload() -> dict[str, Any]:
    return {
        "inventory_adjustment": {
            "date": ADJUSTMENT_DATE,
            "reason": ADJUSTMENT_REASON,
            "description": ADJUSTMENT_DESCRIPTION,
            "reference_number": ADJUSTMENT_REFERENCE,
            "adjustment_type": "quantity",
            "line_items": [
                {
                    "item_id": row["item_id"],
                    "name": row["name"],
                    "description": "Existing generic backing-ring item; physical stock merged from Rachad's count sheet.",
                    "quantity_adjusted": int(row["quantity_adjusted"]),
                    "item_total": float(row["item_total_cad"]),
                    "unit": "pcs",
                    "adjustment_account_id": ADJUSTMENT_ACCOUNT_ID,
                }
                for row in TARGETS
            ],
        },
        "rate_updates": [
            {"item_id": row["item_id"], "name": row["name"], "rate": float(row["target_rate_cad"])}
            for row in TARGETS
        ],
    }


def build_sources(file_evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock_sheet": {
            "instruction": "Rachad Homsi 2026-08-11: merge 4-inch and 10-inch sheet stock into the existing generic items so the current order consumes it.",
            "4_inch": "4 white + 8 black = 12 pieces",
            "10_inch": "83 black + 18 black = 101 pieces",
            "files": file_evidence,
        },
        "prices": {
            "rule": "supplier USD unit cost x 3.6, Decimal exact",
            "4_inch": "Fei 2026-01-07 quotation: USD 30.00 x 3.6 = CAD 108.00",
            "10_inch": "Fei 2025-12-14 master price sheet: USD 130.00 x 3.6 = CAD 468.00",
        },
        "valuation": {
            "4_inch": "Live existing Zoho purchase rate CAD 58.00 x 12 = CAD 696.00",
            "10_inch": "Live existing Zoho purchase rate CAD 175.50 x 101 = CAD 17,725.50",
            "total": "CAD 18,421.50",
            "account": f"Live Inventory Adjustment account {ADJUSTMENT_ACCOUNT_ID}",
        },
        "order_link": {
            "invoice": INVOICE_NUMBER,
            "reference": INVOICE_REFERENCE,
            "4_inch_line": "existing item ID 96274000001518002, quantity 24, historical rate CAD 97.00",
            "10_inch_line": "existing item ID 96274000001518014, quantity 36, historical rate CAD 297.00",
        },
    }


def validate_payload(payload: Any) -> dict[str, Any]:
    payload = closed_fields(payload, PAYLOAD_FIELDS, "payload")
    adjustment = closed_fields(payload["inventory_adjustment"], ADJUSTMENT_FIELDS, "inventory_adjustment")
    expected = build_payload()
    if canonical(payload) != canonical(expected):
        raise BackingRingToolError("Plan payload does not equal the one fixed commissioned payload.")
    if len(adjustment["line_items"]) != 2 or len(payload["rate_updates"]) != 2:
        raise BackingRingToolError("Fixed payload must contain exactly two adjustment lines and two rate updates.")
    for index, line in enumerate(adjustment["line_items"]):
        closed_fields(line, ADJUSTMENT_LINE_FIELDS, f"adjustment line {index + 1}")
    for index, line in enumerate(payload["rate_updates"]):
        closed_fields(line, RATE_UPDATE_FIELDS, f"rate update {index + 1}")
    return payload


def validate_plan(plan: Any) -> dict[str, Any]:
    plan = closed_fields(plan, PLAN_FIELDS, "plan")
    saved_hash = plan["sha256"]
    if not isinstance(saved_hash, str) or not HEX_64_RE.fullmatch(saved_hash):
        raise BackingRingToolError("Plan SHA-256 is malformed.")
    unsigned = dict(plan)
    unsigned.pop("sha256")
    if digest_for(unsigned) != saved_hash:
        raise BackingRingToolError("Plan hash check failed; the staged plan changed.")
    if plan["schema_version"] != SCHEMA_VERSION or plan["tool"] != TOOL_NAME:
        raise BackingRingToolError("Plan belongs to another tool or schema.")
    if plan["tool_version"] != TOOL_VERSION or plan["action"] != ACTION:
        raise BackingRingToolError("Plan action or tool version is not the fixed commissioned action.")
    if plan["approval_required"] != APPROVAL_WORD:
        raise BackingRingToolError("Plan approval word changed.")
    if not isinstance(plan["nonce"], str) or not NONCE_RE.fullmatch(plan["nonce"]):
        raise BackingRingToolError("Plan nonce is malformed.")
    created = parse_utc(plan["created_utc"], "created_utc")
    expires = parse_utc(plan["expires_utc"], "expires_utc")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise BackingRingToolError("Plan lifetime is not exactly 24 hours.")
    organization = closed_fields(plan["organization"], ORGANIZATION_FIELDS, "organization")
    positive_id(organization["inventory_organization_id"], "inventory organization ID")
    positive_id(organization["books_organization_id"], "books organization ID")
    if organization["currency_code"] != "CAD":
        raise BackingRingToolError("Plan organization currency is not CAD.")
    validate_payload(plan["payload"])
    risk = closed_fields(plan["risk"], RISK_FIELDS, "risk")
    if risk != {
        "atomic": False,
        "write_count": 3,
        "write_order": ["POST inventory adjustment", "PUT 4-inch sales rate", "PUT 10-inch sales rate"],
        "note": RISK_NOTE,
    }:
        raise BackingRingToolError("Plan risk disclosure changed.")
    closed_fields(plan["source_evidence"], SOURCE_FIELDS, "source_evidence")
    live = closed_fields(plan["live_evidence"], LIVE_FIELDS, "live_evidence")
    if live["inventory_adjustment_reference_absent"] is not True:
        raise BackingRingToolError("Plan did not prove the adjustment reference absent.")
    if live["inventory_adjustment_reason"] != {
        "reason": ADJUSTMENT_REASON, "reason_id": ADJUSTMENT_REASON_ID
    }:
        raise BackingRingToolError("Plan adjustment reason evidence changed.")
    if live["inventory_adjustment_account"] != {
        "name": ADJUSTMENT_ACCOUNT_NAME, "account_id": ADJUSTMENT_ACCOUNT_ID
    }:
        raise BackingRingToolError("Plan adjustment account evidence changed.")
    if not isinstance(live["items"], list) or len(live["items"]) != 2:
        raise BackingRingToolError("Plan must contain exactly two live item evidence rows.")
    ids = [str(row.get("item_id") or "") for row in live["items"]]
    if ids != list(TARGET_IDS):
        raise BackingRingToolError("Plan item order or identity changed.")
    projection = live["invoice_projection"]
    validate_fixed_invoice(projection)
    return plan


def write_plan(core: dict[str, Any]) -> Path:
    plan = dict(core)
    plan["sha256"] = digest_for(core)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{ACTION}_{plan['sha256'][:12]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt("zoho_backing_ring_stock_plan_staged", str(path))
    return path


def load_plan(path_text: str) -> dict[str, Any]:
    path = Path(path_text).expanduser().resolve()
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackingRingToolError(f"Plan is unreadable: {path}") from exc
    return validate_plan(plan)


def lock_path(plan: dict[str, Any]) -> Path:
    return LOCK_DIR / f"{plan['sha256']}.json"


def acquire_lock(plan: dict[str, Any]) -> Path:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = lock_path(plan)
    record = {
        "plan_sha256": plan["sha256"],
        "action": ACTION,
        "locked_utc": utc_now().isoformat(),
        "state": "commit_started",
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise BackingRingToolError("REFUSED: this plan is already commit-locked; no retry is allowed.") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def update_lock(path: Path, state: str, details: dict[str, Any]) -> None:
    current = json.loads(path.read_text(encoding="utf-8"))
    current["state"] = state
    current["updated_utc"] = utc_now().isoformat()
    current["details"] = details
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_scopes_before_lock() -> dict[str, Any]:
    vault = zoho_tool.load_vault()
    scopes = set(vault.get("scopes") or [])
    required = {INVENTORY_ADJUSTMENT_CREATE_SCOPE, ITEM_UPDATE_SCOPE}
    missing = sorted(required - scopes)
    if missing:
        raise BackingRingToolError(
            "REFUSED BEFORE LOCK: saved Zoho connection lacks required scope(s): "
            + ", ".join(missing)
            + ". Run PREPARE_DADO_ZOHO_ACCESS.bat, create the grant, then "
              "REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat."
        )
    return vault


def request_result(request: Request) -> dict[str, Any]:
    """The sole live write call site; route and method are validated before it."""
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise BackingRingToolError(
            f"Zoho write failed with HTTP {exc.code}: {detail}; plan is permanently locked."
        ) from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise BackingRingToolError(
            f"Zoho write result is indeterminate: {exc}; plan is permanently locked and must not be retried."
        ) from exc
    if result.get("code") not in (None, 0):
        raise BackingRingToolError(
            f"Zoho write failed: {result.get('message') or result.get('code')}; plan is permanently locked."
        )
    return result


def perform_write(
    token: str,
    domain: str,
    organization_id: str,
    method: str,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if method == "POST":
        if path != INVENTORY_ADJUSTMENT_PATH or canonical(payload) != canonical(build_payload()["inventory_adjustment"]):
            raise BackingRingToolError("REFUSED: POST is not the one fixed Inventory Adjustment create.")
    elif method == "PUT":
        match = ITEM_PATH_RE.fullmatch(path)
        item_id = match.group(1) if match else ""
        expected_updates = {row["item_id"]: row for row in build_payload()["rate_updates"]}
        if item_id not in expected_updates or canonical(payload) != canonical(
            {"name": expected_updates[item_id]["name"], "rate": expected_updates[item_id]["rate"]}
        ):
            raise BackingRingToolError("REFUSED: PUT is not one of the two fixed name-preserving rate updates.")
    else:
        raise BackingRingToolError("REFUSED: only one fixed POST and two fixed PUT writes exist.")
    query = urlencode({"organization_id": organization_id})
    request = Request(
        domain.rstrip("/") + path + "?" + query,
        data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Zoho-oauthtoken {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    return request_result(request)


def adjustment_id_from_response(result: dict[str, Any]) -> str:
    candidates = [
        result.get("inventory_adjustment_id"),
        (result.get("inventory_adjustment") or {}).get("inventory_adjustment_id"),
    ]
    for value in candidates:
        text = str(value or "")
        if re.fullmatch(r"[1-9][0-9]*", text):
            return text
    raise BackingRingToolError(
        "Inventory Adjustment POST succeeded without a verifiable adjustment ID; result is indeterminate and plan is locked."
    )


def verify_adjustment(
    token: str, domain: str, organization_id: str, adjustment_id: str
) -> dict[str, Any]:
    result = zoho_tool.api_get(
        token,
        domain,
        f"/inventory/v1/inventoryadjustments/{positive_id(adjustment_id, 'adjustment ID')}?"
        + urlencode({"organization_id": organization_id}),
    )
    adjustment = result.get("inventory_adjustment") or {}
    fixed = {
        "inventory_adjustment_id": adjustment_id,
        "date": ADJUSTMENT_DATE,
        "reason": ADJUSTMENT_REASON,
        "reason_id": ADJUSTMENT_REASON_ID,
        "description": ADJUSTMENT_DESCRIPTION,
        "reference_number": ADJUSTMENT_REFERENCE,
        "adjustment_type": "quantity",
        "status": "adjusted",
    }
    for field, expected in fixed.items():
        if str(adjustment.get(field) if adjustment.get(field) is not None else "") != str(expected):
            raise BackingRingToolError(
                f"Inventory Adjustment verification failed for {field}; plan is locked and no retry is allowed."
            )
    lines = adjustment.get("line_items") or []
    if len(lines) != 2:
        raise BackingRingToolError("Inventory Adjustment read-back does not contain exactly two lines.")
    if [str(row.get("item_id") or "") for row in lines] != list(TARGET_IDS):
        raise BackingRingToolError("Inventory Adjustment item order or identity differs from the plan.")
    for line, target in zip(lines, TARGETS):
        checks = {
            "name": target["name"],
            "unit": "pcs",
            "adjustment_account_id": ADJUSTMENT_ACCOUNT_ID,
            "adjustment_account_name": ADJUSTMENT_ACCOUNT_NAME,
        }
        for field, expected in checks.items():
            if str(line.get(field) or "") != str(expected):
                raise BackingRingToolError(f"Inventory Adjustment line {target['item_id']} {field} mismatch.")
        if decimal_value(line.get("quantity_adjusted"), "quantity_adjusted") != target["quantity_adjusted"]:
            raise BackingRingToolError("Inventory Adjustment quantity mismatch.")
        if decimal_value(line.get("item_total"), "item_total").quantize(CENT) != target["item_total_cad"]:
            raise BackingRingToolError("Inventory Adjustment valuation mismatch.")
    return {
        "inventory_adjustment_id": adjustment_id,
        "status": "adjusted",
        "reference_number": ADJUSTMENT_REFERENCE,
        "total": decimal_text(adjustment.get("total"), "adjustment total"),
    }


def verify_item_after(item: dict[str, Any], evidence: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    validate_fixed_item_identity_only(item, target)
    if protected_item(item) != evidence["protected_item"]:
        raise BackingRingToolError(f"Protected fields changed on item {target['item_id']}.")
    if decimal_value(item.get("rate"), "rate") != target["target_rate_cad"]:
        raise BackingRingToolError(f"Item {target['item_id']} sales rate did not reach the target.")
    for field in ("sales_rate", "pricebook_rate"):
        if field in item and decimal_value(item[field], field) != target["target_rate_cad"]:
            raise BackingRingToolError(f"Item {target['item_id']} {field} did not mirror the target rate.")
    for bracket in item.get("default_price_brackets") or []:
        if "pricebook_rate" in bracket and decimal_value(bracket["pricebook_rate"], "bracket rate") != target["target_rate_cad"]:
            raise BackingRingToolError("Default price bracket did not mirror the target rate.")
    before_stock = evidence["stock_before"]
    after_stock = stock_projection(item)
    quantity = target["quantity_adjusted"]
    changed_by_quantity = {
        "stock_on_hand", "available_stock", "available_for_sale_stock",
        "actual_available_stock", "actual_available_for_sale_stock",
    }
    unchanged = {"committed_stock", "actual_committed_stock", "initial_stock", "initial_stock_rate"}
    for field in changed_by_quantity:
        if decimal_value(after_stock[field], field) != decimal_value(before_stock[field], field) + quantity:
            raise BackingRingToolError(f"Item {target['item_id']} {field} did not increase by {quantity}.")
    for field in unchanged:
        if field in before_stock and decimal_value(after_stock[field], field) != decimal_value(before_stock[field], field):
            raise BackingRingToolError(f"Item {target['item_id']} {field} changed unexpectedly.")
    return {
        "item_id": target["item_id"],
        "sku": target["sku"],
        "rate": decimal_text(item.get("rate"), "rate"),
        "stock_before": before_stock,
        "stock_after": after_stock,
    }


def validate_fixed_item_identity_only(item: dict[str, Any], target: dict[str, Any]) -> None:
    for field, expected in {
        "item_id": target["item_id"], "name": target["name"], "sku": target["sku"],
        "status": "active", "unit": "pcs", "item_type": "inventory",
        "product_type": "goods", "track_inventory": True, "is_combo_product": False,
    }.items():
        if item.get(field) != expected:
            raise BackingRingToolError(f"Fixed item {target['item_id']} identity field {field} changed.")
    if decimal_value(item.get("purchase_rate"), "purchase_rate") != target["purchase_rate_cad"]:
        raise BackingRingToolError(f"Fixed item {target['item_id']} purchase rate changed.")


def command_stage(_: argparse.Namespace) -> None:
    file_evidence = verify_source_files()
    vault = zoho_tool.load_vault()
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = str(vault.get("api_domain") or "")
    inventory_org_id = positive_id(vault.get("inventory_organization_id"), "inventory organization ID")
    books_org_id = positive_id(vault.get("books_organization_id"), "books organization ID")
    organization = inventory_organization(token, domain, inventory_org_id)
    require_reference_absent(token, domain, inventory_org_id)

    item_evidence = []
    for target in TARGETS:
        item = get_item(token, domain, inventory_org_id, target["item_id"])
        validate_fixed_item(item, target)
        item_evidence.append(
            {
                "item_id": target["item_id"],
                "name": target["name"],
                "sku": target["sku"],
                "rate_before": decimal_text(item.get("rate"), "rate"),
                "rate_target": decimal_text(target["target_rate_cad"], "rate target"),
                "purchase_rate": decimal_text(item.get("purchase_rate"), "purchase rate"),
                "quantity_adjusted": decimal_text(target["quantity_adjusted"], "quantity"),
                "item_total": decimal_text(target["item_total_cad"], "item total"),
                "stock_before": stock_projection(item),
                "protected_item": protected_item(item),
                "protected_item_sha256": digest_for(protected_item(item)),
            }
        )

    invoice = get_invoice(token, domain, books_org_id)
    projected_invoice = invoice_projection(invoice)
    validate_fixed_invoice(projected_invoice)
    zoho_tool.save_vault(vault)

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
        "organization": {
            "inventory_organization_id": inventory_org_id,
            "books_organization_id": books_org_id,
            "name": organization["name"],
            "currency_code": organization["currency_code"],
        },
        "payload": build_payload(),
        "risk": {
            "atomic": False,
            "write_count": 3,
            "write_order": ["POST inventory adjustment", "PUT 4-inch sales rate", "PUT 10-inch sales rate"],
            "note": RISK_NOTE,
        },
        "source_evidence": build_sources(file_evidence),
        "live_evidence": {
            "items": item_evidence,
            "invoice_projection": projected_invoice,
            "inventory_adjustment_reference_absent": True,
            "inventory_adjustment_reason": {"reason": ADJUSTMENT_REASON, "reason_id": ADJUSTMENT_REASON_ID},
            "inventory_adjustment_account": {"name": ADJUSTMENT_ACCOUNT_NAME, "account_id": ADJUSTMENT_ACCOUNT_ID},
        },
    }
    path = write_plan(core)
    plan = load_plan(str(path))
    print(json.dumps({
        "status": "STAGED ONLY - ZERO ZOHO WRITES",
        "plan": str(path),
        "sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "approval_required": APPROVAL_WORD,
        "scope_ready": INVENTORY_ADJUSTMENT_CREATE_SCOPE in set(vault.get("scopes") or []),
        "risk": plan["risk"],
        "inventory_adjustment": plan["payload"]["inventory_adjustment"],
        "rate_updates": plan["payload"]["rate_updates"],
        "order_link": plan["source_evidence"]["order_link"],
        "valuation": plan["source_evidence"]["valuation"],
    }, ensure_ascii=False, indent=2))


def command_commit(args: argparse.Namespace) -> None:
    if args.approval != APPROVAL_WORD:
        raise BackingRingToolError("REFUSED: approval must be exactly unpadded uppercase APPROVED.")
    plan = load_plan(args.plan)
    now = utc_now()
    if now > parse_utc(plan["expires_utc"], "expires_utc"):
        raise BackingRingToolError("REFUSED: staged plan expired; stage a fresh read-only plan.")
    if lock_path(plan).exists():
        raise BackingRingToolError("REFUSED: this plan is already commit-locked; no retry is allowed.")

    # FREE refusal: scope is checked before token refresh, network reads and lock.
    vault = require_scopes_before_lock()
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = str(vault.get("api_domain") or "")
    inventory_org_id = positive_id(vault.get("inventory_organization_id"), "inventory organization ID")
    books_org_id = positive_id(vault.get("books_organization_id"), "books organization ID")
    if inventory_org_id != plan["organization"]["inventory_organization_id"] or books_org_id != plan["organization"]["books_organization_id"]:
        raise BackingRingToolError("REFUSED BEFORE LOCK: saved organization differs from the staged plan.")
    organization = inventory_organization(token, domain, inventory_org_id)
    if organization["name"] != plan["organization"]["name"] or organization["currency_code"] != "CAD":
        raise BackingRingToolError("REFUSED BEFORE LOCK: live organization differs from the staged plan.")
    require_reference_absent(token, domain, inventory_org_id)

    fresh_items: dict[str, dict[str, Any]] = {}
    for evidence, target in zip(plan["live_evidence"]["items"], TARGETS):
        item = get_item(token, domain, inventory_org_id, target["item_id"])
        validate_fixed_item(item, target)
        if stock_projection(item) != evidence["stock_before"]:
            raise BackingRingToolError("REFUSED BEFORE LOCK: live stock changed since staging; stage a fresh plan.")
        if protected_item(item) != evidence["protected_item"]:
            raise BackingRingToolError("REFUSED BEFORE LOCK: protected item state changed since staging.")
        fresh_items[target["item_id"]] = item
    projected_invoice = invoice_projection(get_invoice(token, domain, books_org_id))
    if projected_invoice != plan["live_evidence"]["invoice_projection"]:
        raise BackingRingToolError("REFUSED BEFORE LOCK: invoice/order link state changed since staging.")
    zoho_tool.save_vault(vault)

    lock = acquire_lock(plan)
    adjustment_id = ""
    completed: list[str] = []
    try:
        adjustment_response = perform_write(
            token, domain, inventory_org_id, "POST", INVENTORY_ADJUSTMENT_PATH,
            plan["payload"]["inventory_adjustment"],
        )
        adjustment_id = adjustment_id_from_response(adjustment_response)
        adjustment_verification = verify_adjustment(token, domain, inventory_org_id, adjustment_id)
        completed.append("inventory adjustment " + adjustment_id)

        for update in plan["payload"]["rate_updates"]:
            item_id = update["item_id"]
            perform_write(
                token, domain, inventory_org_id, "PUT", f"/inventory/v1/items/{item_id}",
                {"name": update["name"], "rate": update["rate"]},
            )
            completed.append("item rate " + item_id)

        item_results = []
        for evidence, target in zip(plan["live_evidence"]["items"], TARGETS):
            item = get_item(token, domain, inventory_org_id, target["item_id"])
            item_results.append(verify_item_after(item, evidence, target))
        final_invoice = invoice_projection(get_invoice(token, domain, books_org_id))
        if final_invoice != plan["live_evidence"]["invoice_projection"]:
            raise BackingRingToolError("Post-write invoice/order link verification changed; result is indeterminate.")

        details = {
            "inventory_adjustment": adjustment_verification,
            "items": item_results,
            "invoice_projection": final_invoice,
            "completed_writes": completed,
            "emails_sent": 0,
        }
        update_lock(lock, "verified", details)
        zoho_tool.append_receipt("zoho_backing_ring_stock_and_rate_merge_verified", str(lock))
        print(json.dumps({"status": "COMMITTED AND VERIFIED", "details": details, "lock": str(lock)}, indent=2))
    except Exception as exc:
        update_lock(lock, "indeterminate", {
            "error": str(exc), "completed_writes": completed,
            "inventory_adjustment_id": adjustment_id or None,
            "no_retry": True,
        })
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage", help="Read live state and stage the one fixed plan; no writes.")
    stage.set_defaults(func=command_stage)
    commit = sub.add_parser("commit", help="Commit one immutable staged plan once.")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.func(args)
        return 0
    except (BackingRingToolError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
