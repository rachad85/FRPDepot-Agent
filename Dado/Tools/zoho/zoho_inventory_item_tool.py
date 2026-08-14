#!/usr/bin/env python
"""FRP Depot Zoho Inventory Item Catalog Tool.

Commissioned by Rachad Homsi on 2026-07-23. Existing item-group option
renames were narrowly added by Rachad on 2026-08-08.

Allowed service writes:
- POST /inventory/v1/items: create one approved item.
- PUT /inventory/v1/items/{item_id}: change only approved item name/SKU.
- PUT /inventory/v1/itemgroups/{group_id}: rename one existing attribute
  option while preserving every group, attribute, option, and item ID.

Forbidden: delete, stock/opening quantity, stock rate, adjustments, prices on
existing items, status changes, group creation/deletion/restructuring, transfers,
orders, invoices, and mail.

TWO THINGS THIS TOOL DELIBERATELY DOES NOT DO, spelled out because both were
misread on 2026-08-13:
1. It NEVER publishes to the website. There is no WooCommerce, WordPress or
   storefront route anywhere in this module; creating an item here changes Zoho
   Inventory and nothing else.
2. It NEVER sets a Catalog Classification. `custom_fields` is in
   FORBIDDEN_CREATE_FIELDS, so classification is a SEPARATE, separately approved
   plan in zoho_inventory_classification_tool.py -- it has to be, because that
   plan needs the real item IDs this create returns.

The stage-create input shape is FLAT: every writable field sits at the ROOT of
the JSON object beside "sources". A top-level "payload" wrapper is refused.
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
GROUP_UPDATE_PATH_RE = re.compile(r"^/inventory/v1/itemgroups/[0-9]+$")
GROUP_OPTION_TARGET = {
    "group_id": "96274000000034779",
    "group_name": "FRP FW PIPE",
    "attribute_id": "96274000000023957",
    "attribute_name": "SIZE",
    "option_id": "96274000000034781",
    "before_name": "30",
    "after_name": "30\"",
    "linked_item_ids": ["96274000000034771", "96274000000034773"],
    "linked_skus": ["PIDN750150PSI411", "PIDN750150PSI470"],
}
GROUP_OPTION_BASELINE_ATTRIBUTES = [
    {
        "id": "96274000000023957",
        "name": "SIZE",
        "options": [
            {"id": "96274000000023965", "name": "1/2\""},
            {"id": "96274000000023989", "name": "1\""},
            {"id": "96274000000023991", "name": "1-1/2\""},
            {"id": "96274000000023993", "name": "2\""},
            {"id": "96274000000023997", "name": "3\""},
            {"id": "96274000000023999", "name": "4\""},
            {"id": "96274000000024001", "name": "6\""},
            {"id": "96274000000024003", "name": "8\""},
            {"id": "96274000000024005", "name": "10\""},
            {"id": "96274000000024007", "name": "12\""},
            {"id": "96274000000024009", "name": "14\""},
            {"id": "96274000000024011", "name": "16\""},
            {"id": "96274000000024013", "name": "18\""},
            {"id": "96274000000030165", "name": "20\""},
            {"id": "96274000000030171", "name": "24\""},
            {"id": "96274000000034781", "name": "30"},
            {"id": "96274000000034783", "name": "36\""},
        ],
    },
    {
        "id": "96274000000020153",
        "name": "PRESSURE RATING",
        "options": [{"id": "96274000000023279", "name": "150PSI"}],
    },
    {
        "id": "96274000000020151",
        "name": "RESIN TYPE",
        "options": [
            {"id": "96274000000019033", "name": "D411"},
            {"id": "96274000000019035", "name": "D470"},
        ],
    },
]
# Rachad's ruling (2026-07-26, extended to this tool 2026-08-02): his approval
# is ONE PLAIN WORD, never a checksum — the plan digest stays internal and is
# validated by load_plan. The word must come FROM RACHAD'S OWN MESSAGE
# answering the staged plan (Hard Rule 3); Dado relays it, never supplies it.
APPROVAL_WORD = "APPROVED"

# Local plans that were staged but explicitly withdrawn before any approval or
# commit. They are permanently refused by full digest so an old file cannot be
# replayed after Rachad corrected the product mapping.
WITHDRAWN_ITEM_CREATE_PLAN_HASHES = {
    # 4-inch white/black and 10-inch black: Rachad mapped the stock to the
    # already-existing generic 4-inch/10-inch item IDs.
    "9d62d3962fa00f981f0ef6766448a176cc4d3a134b677c7982b1b8d7f012910a": "withdrawn: use existing generic 4-inch item",
    "0a2d2944fd59a273000cd901b794727cd204f88eeeb3d40f47977a88b0030c1a": "withdrawn: use existing generic 4-inch item",
    "0c192caad2b972e7c63a85f6e4944348c044aeef9f2d579bb356c04ffb50613d": "withdrawn: use existing generic 10-inch item",
    # Two OD-specific 1-1/2-inch plans: Rachad corrected them to one black
    # product, 85 pcs total, with no OD shown in the item name or SKU.
    "b44bc7cdb2e9c9bc023e630ffdfd550f63ff98c951974f6b90300e697844d38e": "withdrawn: corrected to one 1-1/2-inch black product with no OD",
    "dea4b48f0a55a2abccc5b76256a97ccab593f1c129a53c43c11035a6467728d0": "withdrawn: corrected to one 1-1/2-inch black product with no OD",
    # Colour-specific plans withdrawn when Rachad directed one item per nominal
    # size regardless of black/white colour. The short-lived colour-neutral
    # 1-1/2-inch BLACK plan is included because colour must not appear either.
    "a3b3d81ca7046d01fc6218e823da9de24d9cbd5485efe022aee063d35150e5ab": "withdrawn: merge colours into one 1-inch item",
    "30291ce09dbd505cc64f40417fed77a02ba23cc087a63b6e1342d74d6be7b884": "withdrawn: colour must not appear on the 1-1/2-inch item",
    "0eabc41727bf143ce31e0a1801f7e544da74205555ca1db1ba30dbeaa2434621": "withdrawn: merge colours into one 2-inch item",
    "fa721418e4bdab8fbafe46b9db6bc253267f3e350a8dd3b7b51506c46339dc14": "withdrawn: merge colours into one 3-inch item",
    "d09f392e6444d923acbde8aad387cf23c628ebae38c30e5ba2c17c8b90caaf52": "withdrawn: merge colours into one 6-inch item",
    "8b02f2ef477604a2c71f4ad2743010782e820339517674bca89124d1a1239a17": "withdrawn: merge colours into one 6-inch item",
    "7efdea461d7974c61dd94f6bf05aae7fe97565b3707360567ffe98943596829d": "withdrawn: merge colours into one 8-inch item",
    "7856e86eb4f7d7b22799561d5c07d4004203eb593d3e1bb80285eb9ca1d51f5d": "withdrawn: merge colours into one 12-inch item",
    "e7a8688670ac0f43e6115e3b6d6cada10f2723387b55cac1167d21fd857c4318": "withdrawn: merge colours into one 12-inch item",
    "dd88cb32d545be3b8619fbdcf7ce8a68d61adc8511a5a4c9347cdbeb35fe798f": "withdrawn: merge colours into one 14-inch item",
    "d5340e598d5a2ed4d862c2e69c33eafdbef06b8db4b0e66eae917bcd9d584313": "withdrawn: merge colours into one 14-inch item",
}


def require_rachad_approval(approval: str) -> None:
    if str(approval).strip().casefold() != APPROVAL_WORD.casefold():
        raise ItemToolError(
            f"Rachad must answer this staged plan with the one-word approval: "
            f"{APPROVAL_WORD}. It must come from his own message (Hard Rule 3) — "
            "never supplied by Dado."
        )

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


def stage_plan(
    kind: str,
    payload: dict[str, Any],
    sources: dict[str, str],
    summary: dict[str, Any],
    live_evidence: dict[str, Any] | None = None,
) -> Path:
    core = {
        "tool": TOOL_NAME,
        "kind": kind,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "sources": sources,
        "summary": summary,
    }
    if live_evidence is not None:
        core["live_evidence"] = live_evidence
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
    if kind == "item_create" and saved in WITHDRAWN_ITEM_CREATE_PLAN_HASHES:
        raise ItemToolError(
            "REFUSED: this uncommitted item-create plan was permanently withdrawn — "
            + WITHDRAWN_ITEM_CREATE_PLAN_HASHES[saved]
            + "."
        )
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


def get_item_group(access_token: str, vault: dict[str, Any], group_id: str) -> dict[str, Any]:
    query = urlencode({"organization_id": vault["inventory_organization_id"]})
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/inventory/v1/itemgroups/{group_id}?{query}",
    )
    group = result.get("item_group") or {}
    if str(group.get("group_id") or "") != group_id:
        raise ItemToolError(f"Zoho item group {group_id} was not found.")
    return group


def state_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def find_group_option(
    group: dict[str, Any],
    attribute_id: str,
    option_id: str,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    attributes = group.get("attributes")
    if not isinstance(attributes, list):
        raise ItemToolError("Zoho item group attributes are missing or malformed.")
    matches = [row for row in attributes if str((row or {}).get("id") or "") == attribute_id]
    if len(matches) != 1:
        raise ItemToolError(f"Expected one attribute {attribute_id}; found {len(matches)}.")
    attribute = matches[0]
    options = attribute.get("options")
    if not isinstance(options, list):
        raise ItemToolError(f"Zoho attribute {attribute_id} options are malformed.")
    option_matches = [row for row in options if str((row or {}).get("id") or "") == option_id]
    if len(option_matches) != 1:
        raise ItemToolError(f"Expected one option {option_id}; found {len(option_matches)}.")
    slots = [
        index
        for index in range(1, 4)
        if str(group.get(f"attribute_id{index}") or "") == attribute_id
    ]
    if len(slots) != 1:
        raise ItemToolError(f"Expected one attribute slot for {attribute_id}; found {len(slots)}.")
    return attribute, option_matches[0], slots[0]


def validate_group_update_payload(payload: dict[str, Any]) -> None:
    if set(payload) != {"group_name", "unit", "attributes"}:
        raise ItemToolError(
            "REFUSED: item-group option rename payload must contain exactly "
            "group_name, unit, and attributes."
        )
    clean_text(payload.get("group_name"), "group_name", required=True)
    clean_text(payload.get("unit"), "unit", required=True)
    attributes = payload.get("attributes")
    if not isinstance(attributes, list) or not 1 <= len(attributes) <= 3:
        raise ItemToolError("REFUSED: attributes must contain one to three preserved attributes.")
    attribute_ids: set[str] = set()
    for index, attribute in enumerate(attributes):
        if not isinstance(attribute, dict) or set(attribute) != {"id", "name", "options"}:
            raise ItemToolError(
                f"REFUSED: attributes[{index}] must contain exactly id, name, and options."
            )
        attribute_id = clean_text(attribute.get("id"), f"attributes[{index}].id", required=True)
        if not attribute_id.isdigit() or attribute_id in attribute_ids:
            raise ItemToolError(f"REFUSED: attributes[{index}].id must be a unique numeric ID.")
        attribute_ids.add(attribute_id)
        clean_text(attribute.get("name"), f"attributes[{index}].name", required=True)
        options = attribute.get("options")
        if not isinstance(options, list) or not 1 <= len(options) <= 100:
            raise ItemToolError(
                f"REFUSED: attributes[{index}].options must contain one to 100 preserved options."
            )
        option_ids: set[str] = set()
        option_names: set[str] = set()
        for option_index, option in enumerate(options):
            if not isinstance(option, dict) or set(option) != {"id", "name"}:
                raise ItemToolError(
                    f"REFUSED: attributes[{index}].options[{option_index}] must contain exactly id and name."
                )
            option_id = clean_text(
                option.get("id"),
                f"attributes[{index}].options[{option_index}].id",
                required=True,
            )
            option_name = clean_text(
                option.get("name"),
                f"attributes[{index}].options[{option_index}].name",
                required=True,
            )
            folded = option_name.casefold()
            if not option_id.isdigit() or option_id in option_ids:
                raise ItemToolError("REFUSED: item-group option IDs must be unique numeric IDs.")
            if folded in option_names:
                raise ItemToolError("REFUSED: item-group option names must be unique within an attribute.")
            option_ids.add(option_id)
            option_names.add(folded)


def fixed_group_option_payload(option_name: str) -> dict[str, Any]:
    attributes = json.loads(canonical(GROUP_OPTION_BASELINE_ATTRIBUTES))
    matches = [
        option
        for attribute in attributes
        if attribute["id"] == GROUP_OPTION_TARGET["attribute_id"]
        for option in attribute["options"]
        if option["id"] == GROUP_OPTION_TARGET["option_id"]
    ]
    if len(matches) != 1:
        raise ItemToolError("Commissioned fixed option definition is invalid.")
    matches[0]["name"] = option_name
    payload = {
        "group_name": GROUP_OPTION_TARGET["group_name"],
        "unit": "ft",
        "attributes": attributes,
    }
    validate_group_update_payload(payload)
    return payload


def build_group_option_payload(
    group: dict[str, Any],
    attribute_id: str,
    option_id: str,
    after_name: str,
) -> dict[str, Any]:
    find_group_option(group, attribute_id, option_id)
    attributes: list[dict[str, Any]] = []
    for raw_attribute in group["attributes"]:
        current_attribute_id = clean_text(raw_attribute.get("id"), "attribute id", required=True)
        options: list[dict[str, str]] = []
        for raw_option in raw_attribute.get("options") or []:
            current_option_id = clean_text(raw_option.get("id"), "option id", required=True)
            current_name = clean_text(raw_option.get("name"), "option name", required=True)
            if current_attribute_id == attribute_id and current_option_id == option_id:
                current_name = after_name
            options.append({"id": current_option_id, "name": current_name})
        attributes.append({
            "id": current_attribute_id,
            "name": clean_text(raw_attribute.get("name"), "attribute name", required=True),
            "options": options,
        })
    payload = {
        "group_name": clean_text(group.get("group_name"), "group_name", required=True),
        "unit": clean_text(group.get("unit"), "unit", required=True),
        "attributes": attributes,
    }
    validate_group_update_payload(payload)
    return payload


def expected_group_after_option_rename(
    group: dict[str, Any],
    attribute_id: str,
    option_id: str,
    after_name: str,
) -> dict[str, Any]:
    expected = json.loads(canonical(group))
    _, option, slot = find_group_option(expected, attribute_id, option_id)
    option["name"] = after_name
    items = expected.get("items")
    if not isinstance(items, list):
        raise ItemToolError("Zoho item group items are missing or malformed.")
    for item in items:
        if str(item.get(f"attribute_option_id{slot}") or "") == option_id:
            item[f"attribute_option_name{slot}"] = after_name
    return expected


def commit_lock_path(plan_path: str) -> Path:
    return Path(str(plan_path) + ".commit-lock.json")


def create_commit_lock(plan_path: str, plan_sha256: str) -> Path:
    path = commit_lock_path(plan_path)
    record = {
        "tool": TOOL_NAME,
        "plan": str(Path(plan_path)),
        "sha256": plan_sha256,
        "write_started_utc": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise ItemToolError(
            "REFUSED: this item-group plan already has a write-attempt lock and cannot be replayed."
        ) from exc
    return path


def command_stage_create(args: argparse.Namespace) -> None:
    raw = read_json(args.input)
    if set(raw) & FORBIDDEN_CREATE_FIELDS:
        raise ItemToolError(
            "REFUSED: stock, status, grouping, or other uncommissioned item field was requested: "
            + ", ".join(sorted(set(raw) & FORBIDDEN_CREATE_FIELDS))
        )
    extra = sorted(set(raw) - CREATE_FIELDS - {"sources"})
    if extra:
        # *** THE 2026-08-13 D441 FAILURE WAS EXACTLY THIS, AND THE OLD MESSAGE
        # DID NOT SAY SO. *** The inputs wrapped everything in a top-level
        # "payload" object, so every real field was invisible to this schema and
        # the only complaint was "Unsupported create-item field(s): payload".
        # The shape is FLAT: the writable fields sit at the root beside
        # "sources". No wrapper of any name is accepted.
        if "payload" in extra:
            raise ItemToolError(
                "REFUSED: this input wraps the item in a top-level \"payload\" object. "
                "stage-create takes a FLAT object: every writable field sits at the root "
                "beside \"sources\". Writable fields are: "
                + ", ".join(sorted(CREATE_FIELDS))
                + ". Unsupported create-item field(s): " + ", ".join(extra)
            )
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
    print(json.dumps({"plan": str(path), "summary": summary, "approval": APPROVAL_WORD}, indent=2))


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
    print(json.dumps({"plan": str(path), "summary": summary, "approval": APPROVAL_WORD}, indent=2))


def command_stage_group_option_rename(args: argparse.Namespace) -> None:
    raw = read_json(args.input)
    if set(raw) != {"source"}:
        raise ItemToolError(
            "This commissioned group-option input must contain exactly one field: source."
        )
    source = clean_text(raw.get("source"), "source", required=True)
    target = json.loads(canonical(GROUP_OPTION_TARGET))
    vault = zoho_tool.load_vault()
    access_token, vault = zoho_tool.refresh_access_token(vault)
    current = get_item_group(access_token, vault, target["group_id"])
    zoho_tool.save_vault(vault)
    if clean_text(current.get("group_name"), "current group name", required=True) != target["group_name"]:
        raise ItemToolError("The commissioned item group name no longer matches; no plan was staged.")
    if clean_text(current.get("status"), "current group status", required=True).casefold() != "active":
        raise ItemToolError("The commissioned item group is not active; no plan was staged.")
    attribute, option, slot = find_group_option(
        current,
        target["attribute_id"],
        target["option_id"],
    )
    if clean_text(attribute.get("name"), "current attribute name", required=True) != target["attribute_name"]:
        raise ItemToolError("The commissioned SIZE attribute name no longer matches; no plan was staged.")
    current_name = clean_text(option.get("name"), "current option name", required=True)
    if current_name != target["before_name"]:
        raise ItemToolError(
            f"The commissioned option is {current_name!r}, not {target['before_name']!r}; no plan was staged."
        )
    sibling_names = {
        clean_text(row.get("name"), "sibling option name", required=True).casefold()
        for row in attribute.get("options") or []
        if str(row.get("id") or "") != target["option_id"]
    }
    if target["after_name"].casefold() in sibling_names:
        raise ItemToolError("The requested 30-inch label already exists on a different option ID.")
    items = current.get("items")
    if not isinstance(items, list):
        raise ItemToolError("Zoho item group items are missing or malformed.")
    linked = [
        row
        for row in items
        if str(row.get(f"attribute_option_id{slot}") or "") == target["option_id"]
    ]
    linked_ids = sorted(str(row.get("item_id") or "") for row in linked)
    linked_skus = sorted(clean_text(row.get("sku"), "linked item SKU", required=True) for row in linked)
    if linked_ids != sorted(target["linked_item_ids"]) or linked_skus != sorted(target["linked_skus"]):
        raise ItemToolError(
            "The commissioned option is not linked to exactly the two approved 30-inch pipe items."
        )
    baseline_payload = build_group_option_payload(
        current,
        target["attribute_id"],
        target["option_id"],
        target["before_name"],
    )
    if baseline_payload != fixed_group_option_payload(target["before_name"]):
        raise ItemToolError(
            "The live group attributes/options differ from the commissioned fixed baseline."
        )
    payload = build_group_option_payload(
        current,
        target["attribute_id"],
        target["option_id"],
        target["after_name"],
    )
    if payload != fixed_group_option_payload(target["after_name"]):
        raise ItemToolError("The generated group-option payload is outside the commissioned fixed shape.")
    expected = expected_group_after_option_rename(
        current,
        target["attribute_id"],
        target["option_id"],
        target["after_name"],
    )
    linked_summary = [
        {
            "item_id": str(row.get("item_id") or ""),
            "name": clean_text(row.get("name"), "linked item name", required=True),
            "sku": clean_text(row.get("sku"), "linked item SKU", required=True),
            "rate": row.get("rate"),
            "purchase_rate": row.get("purchase_rate"),
            "stock_on_hand": row.get("stock_on_hand"),
            "available_stock": row.get("available_stock"),
            "actual_available_stock": row.get("actual_available_stock"),
        }
        for row in sorted(linked, key=lambda value: str(value.get("item_id") or ""))
    ]
    summary = {
        "target": target,
        "before": target["before_name"],
        "after": target["after_name"],
        "changed_fields": ["attributes.SIZE.options[96274000000034781].name"],
        "linked_items": linked_summary,
        "preserved": [
            "group ID and name",
            "all attribute and option IDs",
            "all other option names",
            "all item IDs, names, SKUs, prices, stock, status, pressure, and resin",
        ],
    }
    evidence = {
        "before_state": current,
        "before_state_sha256": state_sha256(current),
        "expected_state": expected,
        "expected_state_sha256": state_sha256(expected),
    }
    path = stage_plan(
        "group_option_rename",
        payload,
        {"option_name": source},
        summary,
        live_evidence=evidence,
    )
    print(json.dumps({"plan": str(path), "summary": summary, "approval": APPROVAL_WORD}, indent=2))


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
        if UPDATE_PATH_RE.fullmatch(path):
            if set(payload) - {"name", "sku"}:
                raise ItemToolError("REFUSED: existing-item updates are limited to name and SKU.")
            if "name" not in payload:
                raise ItemToolError("REFUSED: Zoho item update requires the preserved or approved item name.")
        elif GROUP_UPDATE_PATH_RE.fullmatch(path):
            expected_path = f"/inventory/v1/itemgroups/{GROUP_OPTION_TARGET['group_id']}"
            if path != expected_path:
                raise ItemToolError("REFUSED: only the commissioned FRP FW PIPE group endpoint is allowed.")
            validate_group_update_payload(payload)
            if payload != fixed_group_option_payload(GROUP_OPTION_TARGET["after_name"]):
                raise ItemToolError("REFUSED: item-group payload is not the one fixed 30-inch label correction.")
        else:
            raise ItemToolError("REFUSED: PUT endpoint is not an exact commissioned item or item-group endpoint.")
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
    require_rachad_approval(args.approval)
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
    require_rachad_approval(args.approval)
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


def command_commit_group_option_rename(args: argparse.Namespace) -> None:
    plan = load_plan(args.plan, "group_option_rename")
    require_rachad_approval(args.approval)
    expected_plan_keys = {
        "tool", "kind", "created_utc", "payload", "sources", "summary",
        "live_evidence", "sha256",
    }
    if set(plan) != expected_plan_keys:
        raise ItemToolError("The group-option plan has unsupported or missing top-level fields.")
    sources = plan.get("sources")
    if not isinstance(sources, dict) or set(sources) != {"option_name"}:
        raise ItemToolError("The group-option plan sources are malformed.")
    clean_text(sources.get("option_name"), "sources.option_name", required=True)
    summary = plan.get("summary")
    if not isinstance(summary, dict) or summary.get("target") != GROUP_OPTION_TARGET:
        raise ItemToolError("The plan target is not the one commissioned FRP FW PIPE option.")
    if summary.get("before") != GROUP_OPTION_TARGET["before_name"] or summary.get("after") != GROUP_OPTION_TARGET["after_name"]:
        raise ItemToolError("The plan before/after labels are outside the commissioned correction.")
    if plan.get("payload") != fixed_group_option_payload(GROUP_OPTION_TARGET["after_name"]):
        raise ItemToolError("The plan payload is not the one fixed 30-inch label correction.")
    evidence = plan.get("live_evidence")
    if not isinstance(evidence, dict) or set(evidence) != {
        "before_state", "before_state_sha256", "expected_state", "expected_state_sha256"
    }:
        raise ItemToolError("The group-option live evidence is missing or malformed.")
    before_state = evidence.get("before_state")
    expected_state = evidence.get("expected_state")
    if not isinstance(before_state, dict) or not isinstance(expected_state, dict):
        raise ItemToolError("The protected group states must be objects.")
    if evidence.get("before_state_sha256") != state_sha256(before_state):
        raise ItemToolError("The protected before-state fingerprint is invalid.")
    recomputed_expected = expected_group_after_option_rename(
        before_state,
        GROUP_OPTION_TARGET["attribute_id"],
        GROUP_OPTION_TARGET["option_id"],
        GROUP_OPTION_TARGET["after_name"],
    )
    if expected_state != recomputed_expected:
        raise ItemToolError("The protected expected state is not the one commissioned correction.")
    if evidence.get("expected_state_sha256") != state_sha256(expected_state):
        raise ItemToolError("The protected expected-state fingerprint is invalid.")
    if commit_lock_path(args.plan).exists():
        raise ItemToolError(
            "REFUSED: this item-group plan already has a write-attempt lock and cannot be replayed."
        )
    vault = zoho_tool.load_vault()
    if UPDATE_SCOPE not in (vault.get("scopes") or []):
        raise ItemToolError(f"Saved Zoho connection lacks {UPDATE_SCOPE}.")
    access_token, vault = zoho_tool.refresh_access_token(vault)
    current = get_item_group(access_token, vault, GROUP_OPTION_TARGET["group_id"])
    if current != before_state:
        raise ItemToolError("The item group changed after review. A new plan is required.")
    create_commit_lock(args.plan, plan["sha256"])
    api_write_allowed(
        access_token,
        str(vault["api_domain"]),
        "PUT",
        f"/inventory/v1/itemgroups/{GROUP_OPTION_TARGET['group_id']}",
        str(vault["inventory_organization_id"]),
        plan["payload"],
    )
    zoho_tool.save_vault(vault)
    fresh = get_item_group(access_token, vault, GROUP_OPTION_TARGET["group_id"])
    if fresh != expected_state:
        zoho_tool.append_receipt(
            "zoho_inventory_group_option_unexpected_result",
            f"group_id={GROUP_OPTION_TARGET['group_id']}; plan={args.plan}; sha256={plan['sha256']}; replay_locked=true",
        )
        raise ItemToolError(
            "Zoho returned an unexpected group/item state after the one write attempt. "
            "The plan is replay-locked; no retry is allowed."
        )
    zoho_tool.append_receipt(
        "zoho_inventory_group_option_renamed_by_named_tool",
        f"group_id={GROUP_OPTION_TARGET['group_id']}; option_id={GROUP_OPTION_TARGET['option_id']}; "
        f"before={GROUP_OPTION_TARGET['before_name']}; after={GROUP_OPTION_TARGET['after_name']}; "
        f"plan={args.plan}; sha256={plan['sha256']}",
    )
    print(json.dumps({
        "updated": "item_group_option_name",
        "group_id": GROUP_OPTION_TARGET["group_id"],
        "group_name": GROUP_OPTION_TARGET["group_name"],
        "attribute": GROUP_OPTION_TARGET["attribute_name"],
        "option_id": GROUP_OPTION_TARGET["option_id"],
        "before": GROUP_OPTION_TARGET["before_name"],
        "after": GROUP_OPTION_TARGET["after_name"],
        "linked_item_ids": GROUP_OPTION_TARGET["linked_item_ids"],
        "replay_locked": True,
    }, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage_create = commands.add_parser("stage-create")
    stage_create.add_argument("--input", required=True)
    stage_create.set_defaults(func=command_stage_create)
    stage_change = commands.add_parser("stage-name-sku")
    stage_change.add_argument("--input", required=True)
    stage_change.set_defaults(func=command_stage_name_sku)
    stage_group_option = commands.add_parser("stage-group-option-rename")
    stage_group_option.add_argument("--input", required=True)
    stage_group_option.set_defaults(func=command_stage_group_option_rename)
    commit_create = commands.add_parser("commit-create")
    commit_create.add_argument("--plan", required=True)
    commit_create.add_argument("--approval", required=True)
    commit_create.set_defaults(func=command_commit_create)
    commit_change = commands.add_parser("commit-name-sku")
    commit_change.add_argument("--plan", required=True)
    commit_change.add_argument("--approval", required=True)
    commit_change.set_defaults(func=command_commit_name_sku)
    commit_group_option = commands.add_parser("commit-group-option-rename")
    commit_group_option.add_argument("--plan", required=True)
    commit_group_option.add_argument("--approval", required=True)
    commit_group_option.set_defaults(func=command_commit_group_option_rename)
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
