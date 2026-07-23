#!/usr/bin/env python
"""FRP Depot Zoho Inventory Item Catalog Tool.

Commissioned by Rachad Homsi on 2026-07-23.

Allowed service writes:
- POST /inventory/v1/items: create one approved item.
- PUT /inventory/v1/items/{item_id}: change only approved item name/SKU.

Forbidden: delete, stock/opening quantity, stock rate, adjustments, prices on
existing items, status changes, grouping, transfers, orders, invoices, and mail.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import zoho_tool

TOOL_NAME = "FRP Depot Zoho Inventory Item Catalog Tool"
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_item_plans"
CREATE_SCOPE = "ZohoInventory.items.CREATE"
UPDATE_SCOPE = "ZohoInventory.items.UPDATE"
CREATE_PATH = "/inventory/v1/items"
UPDATE_PATH_RE = re.compile(r"^/inventory/v1/items/[0-9]+$")

CREATE_FIELDS = {
    "name", "sku", "unit", "unit_id", "item_type", "product_type",
    "can_be_sold", "can_be_purchased", "track_inventory", "is_taxable",
    "tax_id", "description", "purchase_description", "purchase_account_id",
    "inventory_account_id", "rate", "purchase_rate", "reorder_level", "upc",
    "ean", "isbn", "part_number", "vendor_id",
}
NUMERIC_FIELDS = {"rate", "purchase_rate", "reorder_level"}
ID_FIELDS = {"unit_id", "tax_id", "purchase_account_id", "inventory_account_id", "vendor_id"}
ENUMS = {
    "item_type": {"inventory", "sales", "purchases", "sales_and_purchases"},
    "product_type": {"goods", "service"},
}
FORBIDDEN_CREATE_FIELDS = {
    "initial_stock", "initial_stock_rate", "locations", "warehouses",
    "opening_stock", "stock_on_hand", "available_stock", "status", "group_id",
    "custom_fields", "documents", "image", "pricebook_rate",
}


class ItemToolError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(plan: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(plan).encode("utf-8")).hexdigest()


def read_json(path: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ItemToolError(f"Input JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ItemToolError("Input JSON must contain one object.")
    return value


def clean_text(value: Any, label: str, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ItemToolError(f"{label} is required.")
    return result


def numeric(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ItemToolError(f"{label} must be a number.")
    result = float(value)
    if result < 0:
        raise ItemToolError(f"{label} cannot be negative.")
    return result


def verify_sources(payload: dict[str, Any], sources: Any) -> dict[str, str]:
    if not isinstance(sources, dict):
        raise ItemToolError("sources must be an object. Every item field needs a source.")
    extra = sorted(set(sources) - set(payload))
    if extra:
        raise ItemToolError("sources contains field(s) not being created: " + ", ".join(extra))
    clean: dict[str, str] = {}
    for field in payload:
        source = clean_text(sources.get(field), f"sources.{field}", required=True)
        clean[field] = source
    return clean


def stage_plan(kind: str, payload: dict[str, Any], sources: dict[str, str], summary: dict[str, Any]) -> Path:
    core = {
        "tool": TOOL_NAME,
        "kind": kind,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "sources": sources,
        "summary": summary,
    }
    digest = digest_for(core)
    plan = dict(core)
    plan["sha256"] = digest
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{kind}_{digest[:8]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt(f"zoho_inventory_{kind}_plan_staged", str(path))
    return path


def load_plan(path: str, kind: str) -> dict[str, Any]:
    plan = read_json(path)
    saved = clean_text(plan.pop("sha256", ""), "sha256", required=True)
    actual = digest_for(plan)
    if saved != actual:
        raise ItemToolError("Plan hash check failed. The plan changed after review.")
    if plan.get("tool") != TOOL_NAME or plan.get("kind") != kind:
        raise ItemToolError("The plan belongs to a different tool or action.")
    plan["sha256"] = saved
    return plan


def list_all_items(access_token: str, vault: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for page in range(1, 101):
        query = urlencode(
            {
                "organization_id": vault["inventory_organization_id"],
                "page": page,
                "per_page": 200,
            }
        )
        response = zoho_tool.api_get(
            access_token,
            str(vault["api_domain"]),
            f"/inventory/v1/items?{query}",
        )
        result.extend(response.get("items") or [])
        page_context = response.get("page_context") or {}
        if not page_context.get("has_more_page"):
            return result
    raise ItemToolError("Item duplicate check exceeded 20,000 records and stopped safely.")


def exact_sku_match(items: list[dict[str, Any]], sku: str, exclude_item_id: str = "") -> dict[str, Any] | None:
    target = sku.strip().casefold()
    if not target:
        return None
    for item in items:
        item_id = str(item.get("item_id") or "")
        if item_id != exclude_item_id and str(item.get("sku") or "").strip().casefold() == target:
            return item
    return None


def get_item(access_token: str, vault: dict[str, Any], item_id: str) -> dict[str, Any]:
    query = urlencode({"organization_id": vault["inventory_organization_id"]})
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/inventory/v1/items/{item_id}?{query}",
    )
    item = result.get("item") or {}
    if str(item.get("item_id") or "") != item_id:
        raise ItemToolError(f"Zoho item {item_id} was not found.")
    return item


def command_stage_create(args: argparse.Namespace) -> None:
    raw = read_json(args.input)
    if set(raw) & FORBIDDEN_CREATE_FIELDS:
        raise ItemToolError(
            "REFUSED: stock, status, grouping, or other uncommissioned item field was requested: "
            + ", ".join(sorted(set(raw) & FORBIDDEN_CREATE_FIELDS))
        )
    extra = sorted(set(raw) - CREATE_FIELDS - {"sources"})
    if extra:
        raise ItemToolError("Unsupported create-item field(s): " + ", ".join(extra))
    name = clean_text(raw.get("name"), "name", required=True)
    payload: dict[str, Any] = {"name": name}
    for field in sorted(CREATE_FIELDS - {"name"}):
        if field not in raw or raw[field] in (None, ""):
            continue
        value = raw[field]
        if field in NUMERIC_FIELDS:
            value = numeric(value, field)
        elif field in ID_FIELDS:
            value = clean_text(value, field, required=True)
            if not value.isdigit():
                raise ItemToolError(f"{field} must be a numeric Zoho ID.")
        elif field in ENUMS:
            value = clean_text(value, field, required=True).casefold()
            if value not in ENUMS[field]:
                raise ItemToolError(f"{field} must be one of: {', '.join(sorted(ENUMS[field]))}")
        elif field in {"can_be_sold", "can_be_purchased", "track_inventory", "is_taxable"}:
            if not isinstance(value, bool):
                raise ItemToolError(f"{field} must be true or false.")
        else:
            value = clean_text(value, field, required=True)
        payload[field] = value
    sources = verify_sources(payload, raw.get("sources"))
    summary = {
        "name": payload["name"],
        "sku": payload.get("sku"),
        "unit": payload.get("unit") or payload.get("unit_id"),
        "item_type": payload.get("item_type"),
        "product_type": payload.get("product_type"),
        "rate": payload.get("rate"),
        "purchase_rate": payload.get("purchase_rate"),
        "reorder_level": payload.get("reorder_level"),
        "stock_or_opening_quantity_included": False,
    }
    path = stage_plan("item_create", payload, sources, summary)
    digest = json.loads(path.read_text(encoding="utf-8"))["sha256"]
    print(json.dumps({"plan": str(path), "summary": summary, "approval": f"APPROVE ITEM {digest[:8]}"}, indent=2))


def command_stage_name_sku(args: argparse.Namespace) -> None:
    raw = read_json(args.input)
    allowed = {"item_id", "new_name", "new_sku", "sources"}
    extra = sorted(set(raw) - allowed)
    if extra:
        raise ItemToolError("Unsupported item-change field(s): " + ", ".join(extra))
    item_id = clean_text(raw.get("item_id"), "item_id", required=True)
    if not item_id.isdigit():
        raise ItemToolError("item_id must be a numeric Zoho item ID.")
    if "new_name" not in raw and "new_sku" not in raw:
        raise ItemToolError("Provide new_name, new_sku, or both.")
    vault = zoho_tool.load_vault()
    access_token, vault = zoho_tool.refresh_access_token(vault)
    current = get_item(access_token, vault, item_id)
    zoho_tool.save_vault(vault)
    before = {
        "item_id": item_id,
        "name": clean_text(current.get("name"), "current name", required=True),
        "sku": clean_text(current.get("sku"), "current sku"),
    }
    desired_name = clean_text(raw.get("new_name"), "new_name") if "new_name" in raw else before["name"]
    desired_sku = clean_text(raw.get("new_sku"), "new_sku") if "new_sku" in raw else before["sku"]
    if not desired_name:
        raise ItemToolError("Item name cannot be blank.")
    changed: dict[str, str] = {}
    if desired_name != before["name"]:
        changed["name"] = desired_name
    if desired_sku != before["sku"]:
        changed["sku"] = desired_sku
    if not changed:
        raise ItemToolError("No name or SKU change was detected.")
    source_input = raw.get("sources")
    if not isinstance(source_input, dict):
        raise ItemToolError("sources must identify Rachad's source for each changed field.")
    sources = {
        field: clean_text(source_input.get(field), f"sources.{field}", required=True)
        for field in changed
    }
    extra_sources = sorted(set(source_input) - set(changed))
    if extra_sources:
        raise ItemToolError("sources contains unchanged field(s): " + ", ".join(extra_sources))
    payload = {"name": desired_name}
    if "new_sku" in raw:
        payload["sku"] = desired_sku
    summary = {"before": before, "after": {"item_id": item_id, "name": desired_name, "sku": desired_sku}, "changed": changed}
    path = stage_plan("item_name_sku", payload, sources, summary)
    digest = json.loads(path.read_text(encoding="utf-8"))["sha256"]
    print(json.dumps({"plan": str(path), "summary": summary, "approval": f"APPROVE ITEM CHANGE {digest[:8]}"}, indent=2))


def api_write_allowed(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if method == "POST":
        if path != CREATE_PATH:
            raise ItemToolError("REFUSED: item-create endpoint is not allowlisted.")
    elif method == "PUT":
        if not UPDATE_PATH_RE.fullmatch(path):
            raise ItemToolError("REFUSED: item-update endpoint is not the exact item endpoint.")
        if set(payload) - {"name", "sku"}:
            raise ItemToolError("REFUSED: existing-item updates are limited to name and SKU.")
        if "name" not in payload:
            raise ItemToolError("REFUSED: Zoho item update requires the preserved or approved item name.")
    else:
        raise ItemToolError("REFUSED: only the named POST and PUT operations are allowed.")
    query = urlencode({"organization_id": organization_id})
    request = Request(
        api_domain.rstrip("/") + path + "?" + query,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Zoho-oauthtoken {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ItemToolError(f"Zoho item write failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise ItemToolError(f"Zoho Inventory could not be reached: {exc.reason}") from exc
    if result.get("code") not in (None, 0):
        raise ItemToolError(f"Zoho item write failed: {result.get('message') or result.get('code')}")
    return result


def command_commit_create(args: argparse.Namespace) -> None:
    plan = load_plan(args.plan, "item_create")
    expected = f"APPROVE ITEM {plan['sha256'][:8]}"
    if args.approval != expected:
        raise ItemToolError(f"Exact Rachad approval required: {expected}")
    vault = zoho_tool.load_vault()
    if CREATE_SCOPE not in (vault.get("scopes") or []):
        raise ItemToolError(f"Saved Zoho connection lacks {CREATE_SCOPE}.")
    access_token, vault = zoho_tool.refresh_access_token(vault)
    sku = clean_text(plan["payload"].get("sku"), "sku")
    if sku:
        duplicate = exact_sku_match(list_all_items(access_token, vault), sku)
        if duplicate:
            raise ItemToolError(
                f"SKU already exists on item {duplicate.get('item_id')} ({duplicate.get('name')}); no item was created."
            )
    result = api_write_allowed(
        access_token,
        str(vault["api_domain"]),
        "POST",
        CREATE_PATH,
        str(vault["inventory_organization_id"]),
        plan["payload"],
    )
    zoho_tool.save_vault(vault)
    item = result.get("item") or {}
    item_id = clean_text(item.get("item_id"), "Zoho item ID", required=True)
    zoho_tool.append_receipt(
        "zoho_inventory_item_created_by_named_tool",
        f"item_id={item_id}; plan={args.plan}; sha256={plan['sha256']}",
    )
    print(json.dumps({"created": "inventory_item", "item_id": item_id, "name": item.get("name"), "sku": item.get("sku")}, indent=2))


def command_commit_name_sku(args: argparse.Namespace) -> None:
    plan = load_plan(args.plan, "item_name_sku")
    expected = f"APPROVE ITEM CHANGE {plan['sha256'][:8]}"
    if args.approval != expected:
        raise ItemToolError(f"Exact Rachad approval required: {expected}")
    vault = zoho_tool.load_vault()
    if UPDATE_SCOPE not in (vault.get("scopes") or []):
        raise ItemToolError(f"Saved Zoho connection lacks {UPDATE_SCOPE}.")
    access_token, vault = zoho_tool.refresh_access_token(vault)
    before = plan["summary"]["before"]
    item_id = str(before["item_id"])
    current = get_item(access_token, vault, item_id)
    current_pair = {
        "item_id": item_id,
        "name": clean_text(current.get("name"), "current name", required=True),
        "sku": clean_text(current.get("sku"), "current sku"),
    }
    if current_pair != before:
        raise ItemToolError("The item name or SKU changed after review. A new plan is required.")
    new_sku = clean_text(plan["summary"]["after"].get("sku"), "new sku")
    if new_sku:
        duplicate = exact_sku_match(list_all_items(access_token, vault), new_sku, exclude_item_id=item_id)
        if duplicate:
            raise ItemToolError(
                f"SKU already exists on item {duplicate.get('item_id')} ({duplicate.get('name')}); no change was made."
            )
    result = api_write_allowed(
        access_token,
        str(vault["api_domain"]),
        "PUT",
        f"/inventory/v1/items/{item_id}",
        str(vault["inventory_organization_id"]),
        plan["payload"],
    )
    zoho_tool.save_vault(vault)
    updated = result.get("item") or {}
    updated_name = clean_text(updated.get("name"), "updated name", required=True)
    updated_sku = clean_text(updated.get("sku"), "updated sku")
    expected_after = plan["summary"]["after"]
    if updated_name != expected_after["name"] or updated_sku != expected_after["sku"]:
        zoho_tool.append_receipt(
            "zoho_inventory_item_name_sku_unexpected_result",
            f"item_id={item_id}; plan={args.plan}; sha256={plan['sha256']}",
        )
        raise ItemToolError(f"Zoho returned unexpected name/SKU for item {item_id}. No further action taken.")
    zoho_tool.append_receipt(
        "zoho_inventory_item_name_sku_updated_by_named_tool",
        f"item_id={item_id}; plan={args.plan}; sha256={plan['sha256']}",
    )
    print(json.dumps({"updated": "item_name_sku", "item_id": item_id, "name": updated_name, "sku": updated_sku}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage_create = commands.add_parser("stage-create")
    stage_create.add_argument("--input", required=True)
    stage_create.set_defaults(func=command_stage_create)
    stage_change = commands.add_parser("stage-name-sku")
    stage_change.add_argument("--input", required=True)
    stage_change.set_defaults(func=command_stage_name_sku)
    commit_create = commands.add_parser("commit-create")
    commit_create.add_argument("--plan", required=True)
    commit_create.add_argument("--approval", required=True)
    commit_create.set_defaults(func=command_commit_create)
    commit_change = commands.add_parser("commit-name-sku")
    commit_change.add_argument("--plan", required=True)
    commit_change.add_argument("--approval", required=True)
    commit_change.set_defaults(func=command_commit_name_sku)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (ItemToolError, zoho_tool.ZohoError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
