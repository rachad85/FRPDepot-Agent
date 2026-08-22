#!/usr/bin/env python
"""FRP Depot Zoho Inventory Item Category Tool.

Commissioned by Rachad Homsi on 2026-08-07. Building or staging with this
module is not approval of a business write.

The only service writes this named tool can issue are:
- POST /inventory/v1/categories with exactly {"name"}.
- PUT /inventory/v1/items/{positive item_id} with exactly
  {"name", "category_id"}; name must equal the staged live item name and is
  included because Zoho's item-update schema requires it.

No rename/delete/deactivate, item-group, price, stock, account, tax, unit,
description, image, or other item-field write is reachable through this tool.

AUTONOMY PROGRAMME, 2026-08-21 (Rachad: "Go on all of them"; spec A1/A2/A4/A6).
Categories are REVERSIBLE work, so this tool follows the shared owner authority
in Dado/Tools/common/owner_authority.py: his clear go in his own message covers
the whole batch (APPROVED still works; "yes" / "go ahead" count); a failed line
is recorded and the remaining lines still run; each item's live category is
captured beside the plan BEFORE its PUT and `restore-assign --plan` puts it
back (an item that had no category cannot be cleared through this transport and
is reported as such); a failed or unverified write reads "needs re-stage";
nothing is permanently locked. A committed plan is spent. Category CREATE has
no restore: Zoho deletes are walled (Hard Rule 3).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
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

TOOL_NAME = "FRP Depot Zoho Inventory Item Category Tool"
SCHEMA_VERSION = 1
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_category_plans"
PLAN_LIFETIME_HOURS = 24
# Still the word the plan names; since 2026-08-21 any clear go of his counts.
APPROVAL_WORD = owner_authority.EXACT_WORD
# The live GET endpoint is confirmed, but Zoho's published Inventory OpenAPI
# does not document a category-create request or category_id on item updates.
# Keep every commit command hard-disabled until an authorized UI request or
# other authoritative Zoho metadata proves the exact write bodies.
WRITE_SHAPES_VERIFIED = False
CREATE_SCOPE = "ZohoInventory.items.CREATE"
UPDATE_SCOPE = "ZohoInventory.items.UPDATE"
CATEGORY_PATH = "/inventory/v1/categories"
ITEM_PATH_RE = re.compile(r"^/inventory/v1/items/([1-9][0-9]*)$")
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
CREATE_INPUT_FIELDS = {"category_name", "sources"}
ASSIGN_INPUT_FIELDS = {"category_id", "item_ids", "sources"}
CREATE_PLAN_FIELDS = {
    "schema_version", "tool", "action", "created_utc", "expires_utc",
    "nonce", "approval_required", "payload", "sources", "live_evidence",
    "sha256",
}
ASSIGN_PLAN_FIELDS = set(CREATE_PLAN_FIELDS)
CATEGORY_EVIDENCE_FIELDS = {"category_id", "name"}
ITEM_EVIDENCE_FIELDS = {
    "item_id", "name", "sku", "group_id", "group_name", "category_id",
    "category_name", "proposed_category", "current_state",
    "current_state_sha256", "protected_state", "protected_state_sha256",
}
# The category fields are the commissioned change. Zoho necessarily advances
# last_modified_time on an item update, so that server-maintained timestamp is
# evidence, but not a protected business field. Every other returned item field
# is protected byte-for-byte at the canonical JSON-value level.
PROTECTED_STATE_EXCLUSIONS = {"category_id", "category_name", "last_modified_time"}


class CategoryToolError(RuntimeError):
    pass


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
        raise CategoryToolError("Zoho returned evidence that is not JSON serializable.") from exc


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CategoryToolError(f"{label} JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise CategoryToolError(f"{label} JSON must contain exactly one object.")
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
    raise CategoryToolError(f"{label} must use the exact closed schema ({'; '.join(details)}).")


def clean_text(value: Any, label: str, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise CategoryToolError(f"{label} must be text.")
    result = value.strip()
    if not result:
        raise CategoryToolError(f"{label} cannot be blank.")
    if len(result) > maximum:
        raise CategoryToolError(f"{label} exceeds the {maximum}-character safety limit.")
    if any(ord(character) < 32 for character in result):
        raise CategoryToolError(f"{label} contains control characters.")
    return result


def positive_id(value: Any, label: str) -> str:
    if isinstance(value, bool):
        raise CategoryToolError(f"{label} must be a positive Zoho ID.")
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise CategoryToolError(f"{label} must be a positive Zoho ID.")
    return text


def source_object(value: Any, key: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {key}:
        raise CategoryToolError(f"sources must contain exactly {key}.")
    return {key: clean_text(value[key], f"sources.{key}", 2000)}


def require_rachad_approval(approval: Any, lane: Any = None,
                            what: str = "this category plan") -> owner_authority.OwnerGo:
    """His clear go in his own message (spec A1). Building or staging is not
    approval, and Dado cannot supply it."""
    try:
        return owner_authority.require_owner_go(approval, lane=lane, what=what)
    except owner_authority.OwnerAuthorityRefused as exc:
        raise CategoryToolError(str(exc)) from exc


def require_verified_write_shapes() -> None:
    if not WRITE_SHAPES_VERIFIED:
        raise CategoryToolError(
            "REFUSED: category commit commands are disabled because Zoho's exact "
            "category-create and item-assignment write payloads are not yet verified."
        )


def contained_plan(raw_path: Any) -> Path:
    candidate = Path(str(raw_path if raw_path is not None else ""))
    if not candidate.is_absolute():
        raise CategoryToolError("Plan must be an absolute path inside the exact category plan folder.")
    try:
        resolved = candidate.resolve(strict=True)
        root = PLAN_DIR.resolve(strict=True)
    except OSError as exc:
        raise CategoryToolError("Plan does not resolve inside the exact category plan folder.") from exc
    if root not in resolved.parents or not resolved.is_file() or resolved.suffix.casefold() != ".json":
        raise CategoryToolError("Plan is outside the exact allowlisted category plan folder.")
    return resolved


def validate_create_input(raw: dict[str, Any]) -> dict[str, Any]:
    closed_fields(raw, CREATE_INPUT_FIELDS, "Create input")
    return {
        "category_name": clean_text(raw["category_name"], "category_name", 200),
        "sources": source_object(raw["sources"], "category_name"),
    }


def validate_assign_input(raw: dict[str, Any]) -> dict[str, Any]:
    closed_fields(raw, ASSIGN_INPUT_FIELDS, "Assignment input")
    category_id = positive_id(raw["category_id"], "category_id")
    item_values = raw["item_ids"]
    if not isinstance(item_values, list):
        raise CategoryToolError("item_ids must be a list of 1-20 unique positive Zoho IDs.")
    if not 1 <= len(item_values) <= 20:
        raise CategoryToolError("item_ids must contain 1-20 items.")
    item_ids = [positive_id(value, f"item_ids[{index}]") for index, value in enumerate(item_values)]
    if len(set(item_ids)) != len(item_ids):
        raise CategoryToolError("item_ids must be unique.")
    return {
        "category_id": category_id,
        "item_ids": item_ids,
        "sources": source_object(raw["sources"], "category_assignment"),
    }


def category_evidence(value: dict[str, Any]) -> dict[str, str]:
    return {
        "category_id": positive_id(value.get("category_id"), "live category_id"),
        "name": clean_text(value.get("name"), "live category name", 200),
    }


def list_all_categories(access_token: str, vault: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(1, 101):
        query = urlencode({
            "organization_id": vault["inventory_organization_id"],
            "page": page,
            "per_page": 200,
        })
        result = zoho_tool.api_get(
            access_token,
            str(vault["api_domain"]),
            f"{CATEGORY_PATH}?{query}",
        )
        page_rows = result.get("categories")
        if not isinstance(page_rows, list):
            raise CategoryToolError("Zoho category GET returned no categories list.")
        rows.extend(page_rows)
        if not zoho_tool.require_has_more_page(
            result, "/inventory/v1/categories", page, CategoryToolError
        ):
            return rows
    raise CategoryToolError("Category listing exceeded 20,000 records and stopped safely.")


def simplified_categories(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise CategoryToolError("Zoho returned a non-object category row.")
        # The live Canadian endpoint always includes one structural tree row:
        # category_id=-1, name=ROOT, parent_category_id=-1. It is not an
        # assignable category and must never enter a create/assignment plan.
        if (
            str(row.get("category_id") or "") == "-1"
            and str(row.get("name") or "") == "ROOT"
            and str(row.get("parent_category_id") or "") == "-1"
        ):
            continue
        result.append(category_evidence(row))
    return result


def category_by_id(rows: list[dict[str, Any]], category_id: str) -> dict[str, str]:
    matches = [row for row in simplified_categories(rows) if row["category_id"] == category_id]
    if len(matches) != 1:
        raise CategoryToolError(f"Zoho did not return exactly one category with ID {category_id}.")
    return matches[0]


def duplicate_category(rows: list[dict[str, Any]], name: str) -> dict[str, str] | None:
    target = name.strip().casefold()
    for row in simplified_categories(rows):
        if row["name"].strip().casefold() == target:
            return row
    return None


def get_item(access_token: str, vault: dict[str, Any], item_id: str) -> dict[str, Any]:
    query = urlencode({"organization_id": vault["inventory_organization_id"]})
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/inventory/v1/items/{item_id}?{query}",
    )
    item = result.get("item")
    if not isinstance(item, dict) or str(item.get("item_id") or "") != item_id:
        raise CategoryToolError(f"Zoho item {item_id} was not found.")
    return json_copy(item)


def protected_state(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: json_copy(value)
        for key, value in item.items()
        if key not in PROTECTED_STATE_EXCLUSIONS
    }


def item_evidence(item: dict[str, Any], proposed: dict[str, str]) -> dict[str, Any]:
    item_id = positive_id(item.get("item_id"), "live item_id")
    current = json_copy(item)
    protected = protected_state(current)
    return {
        "item_id": item_id,
        "name": clean_text(item.get("name"), f"item {item_id} name", 500),
        "sku": str(item.get("sku") or ""),
        "group_id": str(item.get("group_id") or ""),
        "group_name": str(item.get("group_name") or ""),
        "category_id": str(item.get("category_id") or ""),
        "category_name": str(item.get("category_name") or ""),
        "proposed_category": dict(proposed),
        "current_state": current,
        "current_state_sha256": digest_for(current),
        "protected_state": protected,
        "protected_state_sha256": digest_for(protected),
    }


def stage_plan(action: str, payload: dict[str, Any], sources: dict[str, str], live_evidence: dict[str, Any]) -> Path:
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "action": action,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "approval_required": APPROVAL_WORD,
        "payload": payload,
        "sources": sources,
        "live_evidence": live_evidence,
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
        raise CategoryToolError("Refused to overwrite an existing category plan.") from exc
    zoho_tool.append_receipt(
        f"zoho_inventory_{action}_plan_staged_not_committed",
        f"plan={path}; sha256={digest}",
    )
    return path


def command_list_categories(_: argparse.Namespace) -> None:
    vault = zoho_tool.load_vault()
    access_token, vault = zoho_tool.refresh_access_token(vault)
    rows = simplified_categories(list_all_categories(access_token, vault))
    print(json.dumps({
        "status": "READ_ONLY",
        "method": "GET",
        "endpoint": CATEGORY_PATH,
        "category_count": len(rows),
        "categories": rows,
    }, ensure_ascii=False, indent=2))


def command_stage_create(args: argparse.Namespace) -> None:
    # Closed-schema rejection intentionally precedes every token/service access.
    expected = validate_create_input(read_json_object(Path(args.input), "Create input"))
    vault = zoho_tool.load_vault()
    access_token, vault = zoho_tool.refresh_access_token(vault)
    categories = simplified_categories(list_all_categories(access_token, vault))
    duplicate = duplicate_category(categories, expected["category_name"])
    if duplicate:
        raise CategoryToolError(
            f"Category name already exists case-insensitively as ID {duplicate['category_id']}; nothing was staged."
        )
    live_evidence = {
        "existing_categories": categories,
        "existing_categories_sha256": digest_for(categories),
    }
    path = stage_plan(
        "category_create",
        {"name": expected["category_name"]},
        expected["sources"],
        live_evidence,
    )
    plan = read_json_object(path, "Staged plan")
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED",
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "action": "category_create",
        "category_name": expected["category_name"],
        "sources": expected["sources"],
        "approval": APPROVAL_WORD,
    }, ensure_ascii=False, indent=2))


def command_stage_assign(args: argparse.Namespace) -> None:
    # Closed-schema and 1-20 checks intentionally precede token/service access.
    expected = validate_assign_input(read_json_object(Path(args.input), "Assignment input"))
    vault = zoho_tool.load_vault()
    access_token, vault = zoho_tool.refresh_access_token(vault)
    proposed = category_by_id(
        list_all_categories(access_token, vault), expected["category_id"]
    )
    evidence: list[dict[str, Any]] = []
    for item_id in expected["item_ids"]:
        row = item_evidence(get_item(access_token, vault, item_id), proposed)
        if row["category_id"] == proposed["category_id"]:
            raise CategoryToolError(
                f"Item {item_id} is already assigned to category {proposed['name']}; nothing was staged."
            )
        evidence.append(row)
    path = stage_plan(
        "category_assign",
        {"category_id": proposed["category_id"], "item_ids": expected["item_ids"]},
        expected["sources"],
        {"category": proposed, "items": evidence},
    )
    plan = read_json_object(path, "Staged plan")
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED",
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "action": "category_assign",
        "category": proposed,
        "items": [{
            key: row[key]
            for key in (
                "item_id", "name", "sku", "group_id", "group_name",
                "category_id", "category_name", "proposed_category",
                "current_state_sha256", "protected_state_sha256",
            )
        } for row in evidence],
        "sources": expected["sources"],
        "approval": APPROVAL_WORD,
        "atomic": False,
    }, ensure_ascii=False, indent=2))


def parse_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise CategoryToolError(f"Plan {label} is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CategoryToolError(f"Plan {label} must include a timezone.")
    return parsed


def validate_common_plan(plan: dict[str, Any], action: str) -> None:
    expected_fields = CREATE_PLAN_FIELDS if action == "category_create" else ASSIGN_PLAN_FIELDS
    closed_fields(plan, expected_fields, "Plan")
    if (
        plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("tool") != TOOL_NAME
        or plan.get("action") != action
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise CategoryToolError("Plan schema version, tool, action, or approval requirement is invalid.")
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise CategoryToolError("Plan nonce is invalid.")
    created = parse_time(plan["created_utc"], "creation time")
    expires = parse_time(plan["expires_utc"], "expiry")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise CategoryToolError("Plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise CategoryToolError("Plan creation time is in the future.")
    if now >= expires:
        raise CategoryToolError("Plan expired. Stage a new plan for review.")


def validate_create_plan(plan: dict[str, Any]) -> None:
    validate_common_plan(plan, "category_create")
    payload = plan.get("payload")
    if not isinstance(payload, dict):
        raise CategoryToolError("Create plan payload is invalid.")
    closed_fields(payload, {"name"}, "Create plan payload")
    if payload["name"] != clean_text(payload["name"], "payload.name", 200):
        raise CategoryToolError("Create plan category name is not canonical.")
    source_object(plan.get("sources"), "category_name")
    live = plan.get("live_evidence")
    if not isinstance(live, dict):
        raise CategoryToolError("Create plan live evidence is invalid.")
    closed_fields(live, {"existing_categories", "existing_categories_sha256"}, "Create live evidence")
    rows = live["existing_categories"]
    if not isinstance(rows, list):
        raise CategoryToolError("Create plan category evidence must be a list.")
    for row in rows:
        if not isinstance(row, dict):
            raise CategoryToolError("Create plan category evidence contains a non-object row.")
        closed_fields(row, CATEGORY_EVIDENCE_FIELDS, "Category evidence row")
        if category_evidence(row) != row:
            raise CategoryToolError("Create plan category evidence is not canonical.")
    if not secrets.compare_digest(str(live["existing_categories_sha256"]), digest_for(rows)):
        raise CategoryToolError("Create plan live category evidence hash is invalid.")
    if duplicate_category(rows, payload["name"]):
        raise CategoryToolError("Create plan evidence already contains a duplicate category name.")


def validate_item_evidence(row: dict[str, Any], proposed: dict[str, str]) -> None:
    closed_fields(row, ITEM_EVIDENCE_FIELDS, "Assignment item evidence")
    item_id = positive_id(row["item_id"], "evidence.item_id")
    if row["item_id"] != item_id:
        raise CategoryToolError("Assignment evidence item_id is not canonical.")
    if row["name"] != clean_text(row["name"], f"item {item_id} evidence name", 500):
        raise CategoryToolError("Assignment evidence item name is not canonical.")
    for key in ("sku", "group_id", "group_name", "category_id", "category_name"):
        if not isinstance(row[key], str):
            raise CategoryToolError(f"Assignment evidence {key} must be text.")
    if row["proposed_category"] != proposed:
        raise CategoryToolError("Assignment evidence proposed category is inconsistent.")
    current = row["current_state"]
    protected = row["protected_state"]
    if not isinstance(current, dict) or not isinstance(protected, dict):
        raise CategoryToolError("Assignment current/protected evidence must be objects.")
    if not secrets.compare_digest(str(row["current_state_sha256"]), digest_for(current)):
        raise CategoryToolError("Assignment current-state evidence hash is invalid.")
    expected_protected = protected_state(current)
    if protected != expected_protected or not secrets.compare_digest(
        str(row["protected_state_sha256"]), digest_for(protected)
    ):
        raise CategoryToolError("Assignment protected-state evidence is invalid.")
    summary = {
        "item_id": str(current.get("item_id") or ""),
        "name": str(current.get("name") or "").strip(),
        "sku": str(current.get("sku") or ""),
        "group_id": str(current.get("group_id") or ""),
        "group_name": str(current.get("group_name") or ""),
        "category_id": str(current.get("category_id") or ""),
        "category_name": str(current.get("category_name") or ""),
    }
    if any(row[key] != summary[key] for key in summary):
        raise CategoryToolError("Assignment summary does not match immutable current-state evidence.")


def validate_assign_plan(plan: dict[str, Any]) -> None:
    validate_common_plan(plan, "category_assign")
    payload = plan.get("payload")
    if not isinstance(payload, dict):
        raise CategoryToolError("Assignment plan payload is invalid.")
    closed_fields(payload, {"category_id", "item_ids"}, "Assignment plan payload")
    validated = validate_assign_input({
        "category_id": payload["category_id"],
        "item_ids": payload["item_ids"],
        "sources": plan.get("sources"),
    })
    if payload != {"category_id": validated["category_id"], "item_ids": validated["item_ids"]}:
        raise CategoryToolError("Assignment plan payload is not canonical.")
    live = plan.get("live_evidence")
    if not isinstance(live, dict):
        raise CategoryToolError("Assignment live evidence is invalid.")
    closed_fields(live, {"category", "items"}, "Assignment live evidence")
    proposed_raw = live["category"]
    if not isinstance(proposed_raw, dict):
        raise CategoryToolError("Assignment category evidence is invalid.")
    closed_fields(proposed_raw, CATEGORY_EVIDENCE_FIELDS, "Assignment category evidence")
    proposed = category_evidence(proposed_raw)
    if proposed != proposed_raw or proposed["category_id"] != payload["category_id"]:
        raise CategoryToolError("Assignment category evidence is not canonical or does not match the payload.")
    items = live["items"]
    if not isinstance(items, list) or len(items) != len(payload["item_ids"]):
        raise CategoryToolError("Assignment item evidence count does not match the payload.")
    for row in items:
        if not isinstance(row, dict):
            raise CategoryToolError("Assignment item evidence contains a non-object row.")
        validate_item_evidence(row, proposed)
    if [row["item_id"] for row in items] != payload["item_ids"]:
        raise CategoryToolError("Assignment item evidence order/IDs do not match the payload.")


def load_plan(path: Path, action: str) -> dict[str, Any]:
    plan = read_json_object(path, "Plan")
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise CategoryToolError("Plan hash check failed. The plan changed after review.")
    if action == "category_create":
        validate_create_plan(plan)
    elif action == "category_assign":
        validate_assign_plan(plan)
    else:
        raise CategoryToolError("Unsupported category plan action.")
    return plan


def lock_path(plan_sha256: str) -> Path:
    if not HEX_64_RE.fullmatch(str(plan_sha256)):
        raise CategoryToolError("Plan digest is invalid for replay locking.")
    return PLAN_DIR / ".commit-locks" / f"{plan_sha256}.json"


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError:
        # A4: what the existing record says decides -- spent, or needs re-stage.
        owner_authority.refuse_replay(CategoryToolError, owner_authority.read_json_if_exists(path),
                                      what="category plan")
        raise  # unreachable
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def api_write_allowed(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """The complete write transport and its verb/path/payload allowlist."""
    if not isinstance(payload, dict):
        raise CategoryToolError("REFUSED: Zoho write payload must be one object.")
    if method == "POST":
        if path != CATEGORY_PATH or set(payload) != {"name"}:
            raise CategoryToolError(
                "REFUSED: category create is only POST /inventory/v1/categories with exactly name."
            )
        clean_text(payload["name"], "category create payload name", 200)
    elif method == "PUT":
        match = ITEM_PATH_RE.fullmatch(path)
        if not match or set(payload) != {"name", "category_id"}:
            raise CategoryToolError(
                "REFUSED: category assignment is only exact item PUT with exactly preserved name and category_id."
            )
        positive_id(match.group(1), "item endpoint ID")
        clean_text(payload["name"], "preserved item name", 500)
        positive_id(payload["category_id"], "assignment category_id")
    else:
        raise CategoryToolError("REFUSED: only the commissioned category POST and item-category PUT are allowed.")
    query = urlencode({"organization_id": positive_id(organization_id, "organization_id")})
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
        raise CategoryToolError(f"Zoho category write failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise CategoryToolError(f"Zoho Inventory category write outcome is indeterminate: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise CategoryToolError("Zoho category write returned invalid JSON; outcome is indeterminate.") from exc
    if not isinstance(result, dict) or result.get("code") not in (None, 0):
        message = result.get("message") if isinstance(result, dict) else "invalid response"
        raise CategoryToolError(f"Zoho category write failed: {message or result.get('code')}")
    return result


def exact_category_state(access_token: str, vault: dict[str, Any], expected: dict[str, str]) -> None:
    current = category_by_id(list_all_categories(access_token, vault), expected["category_id"])
    if current != expected:
        raise CategoryToolError("The target category changed after review. A new plan is required.")


def command_commit_create(args: argparse.Namespace) -> None:
    require_verified_write_shapes()
    plan_path = contained_plan(args.plan)
    plan = load_plan(plan_path, "category_create")
    go = require_rachad_approval(args.approval, getattr(args, "approval_lane", None),
                                 what="this category-create plan")
    lock = lock_path(plan["sha256"])
    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_IN_FLIGHT, plan_sha256=plan["sha256"], action="category_create", go=go,
        started_utc=utc_now().isoformat(),
    ), exclusive=True)
    write_attempted = False
    try:
        vault = zoho_tool.load_vault()
        if CREATE_SCOPE not in (vault.get("scopes") or []):
            raise CategoryToolError(f"Saved Zoho connection lacks {CREATE_SCOPE}.")
        access_token, vault = zoho_tool.refresh_access_token(vault)
        expected_name = plan["payload"]["name"]
        duplicate = duplicate_category(list_all_categories(access_token, vault), expected_name)
        if duplicate:
            raise CategoryToolError(
                f"Category name now exists case-insensitively as ID {duplicate['category_id']}; no category was created."
            )
        write_attempted = True
        result = api_write_allowed(
            access_token,
            str(vault["api_domain"]),
            "POST",
            CATEGORY_PATH,
            str(vault["inventory_organization_id"]),
            {"name": expected_name},
        )
        response_category = result.get("category") or {}
        response_id = str(response_category.get("category_id") or "") if isinstance(response_category, dict) else ""
        matches = [
            row for row in simplified_categories(list_all_categories(access_token, vault))
            if row["name"] == expected_name
        ]
        if len(matches) != 1 or (response_id and matches[0]["category_id"] != response_id):
            raise CategoryToolError("Live category read-back did not exactly verify the created category.")
        created = matches[0]
        zoho_tool.save_vault(vault)
    except Exception as exc:
        status = owner_authority.STATUS_INDETERMINATE if write_attempted else owner_authority.STATUS_NEEDS_RESTAGE
        write_lock(lock, owner_authority.attempt_record(
            status, plan_sha256=plan["sha256"], action="category_create", go=go, reason=str(exc),
            write_attempted=write_attempted,
        ))
        zoho_tool.append_receipt(
            f"zoho_inventory_category_create_{status}",
            f"write_attempted={write_attempted}; plan={plan_path}; sha256={plan['sha256']}",
        )
        raise CategoryToolError(
            owner_authority.explain_outcome("Category create", status, str(exc))
            + ("" if write_attempted else " No write was issued.")
        ) from exc
    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_COMMITTED, plan_sha256=plan["sha256"], action="category_create", go=go,
        category_id=created["category_id"], category_name=created["name"],
    ))
    zoho_tool.append_receipt(
        "zoho_inventory_category_created_committed_verified",
        f"category_id={created['category_id']}; plan={plan_path}; sha256={plan['sha256']}; go_lane={go.lane}",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "action": "category_create",
        "plan_sha256": plan["sha256"],
        "category": created,
        "replay_locked": True,
        "plan_spent": True,
        "restore": "not available: Zoho deletes are walled (Hard Rule 3); the category stays",
    }, ensure_ascii=False, indent=2))


def command_commit_assign(args: argparse.Namespace) -> None:
    require_verified_write_shapes()
    plan_path = contained_plan(args.plan)
    plan = load_plan(plan_path, "category_assign")
    go = require_rachad_approval(args.approval, getattr(args, "approval_lane", None),
                                 what="this category-assignment plan")
    lock = lock_path(plan["sha256"])
    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_IN_FLIGHT, plan_sha256=plan["sha256"], action="category_assign", go=go,
        completed_item_ids=[], started_utc=utc_now().isoformat(),
    ), exclusive=True)
    rows = plan["live_evidence"]["items"]
    progress = owner_authority.BatchProgress([row["item_id"] for row in rows])
    completed_item_ids: list[str] = []
    indeterminate: list[str] = []
    captured: dict[str, dict[str, Any]] = {}
    backup = owner_authority.live_state_path(plan_path)

    def finish_needs_restage(reason: str) -> None:
        status = owner_authority.STATUS_INDETERMINATE if indeterminate else owner_authority.STATUS_NEEDS_RESTAGE
        write_lock(lock, owner_authority.attempt_record(
            status, plan_sha256=plan["sha256"], action="category_assign", go=go, reason=reason,
            completed=completed_item_ids, failed=progress.failed, pending=progress.pending,
            completed_item_ids=completed_item_ids, indeterminate=indeterminate,
            backup=str(backup) if captured else None, restage_only=progress.summary()["restage_only"],
        ))
        zoho_tool.append_receipt(
            f"zoho_inventory_category_assignment_{status}",
            f"status={status}; completed={','.join(completed_item_ids) or 'none'}; "
            f"failed={','.join(progress.failed) or 'none'}; pending={','.join(progress.pending) or 'none'}; "
            f"backup={backup if captured else 'none'}; plan={plan_path}; sha256={plan['sha256']}",
        )
        raise CategoryToolError(
            owner_authority.explain_outcome(
                "Category assignment", status, reason,
                completed=completed_item_ids or None, failed=progress.failed or None,
                pending=progress.pending or None,
            )
            + f" Re-stage only these item IDs: {progress.summary()['restage_only']}."
        )

    try:
        vault = zoho_tool.load_vault()
        if UPDATE_SCOPE not in (vault.get("scopes") or []):
            raise CategoryToolError(f"Saved Zoho connection lacks {UPDATE_SCOPE}.")
        access_token, vault = zoho_tool.refresh_access_token(vault)
        proposed = plan["live_evidence"]["category"]
    except Exception as exc:
        finish_needs_restage(str(exc))
        raise  # unreachable
    for evidence in rows:
        item_id = evidence["item_id"]
        in_flight = False
        try:
            # Re-check the target and the exact immutable staged item state
            # immediately before every non-atomic PUT.
            exact_category_state(access_token, vault, proposed)
            current = get_item(access_token, vault, item_id)
            if not secrets.compare_digest(digest_for(current), evidence["current_state_sha256"]):
                raise CategoryToolError(
                    f"Item {item_id} changed after review. No PUT was issued for this item."
                )
            if current != evidence["current_state"]:
                raise CategoryToolError(f"Item {item_id} exact current-state evidence does not match.")
            # A2: keep this item's live category beside the plan BEFORE its PUT.
            captured[item_id] = {
                "item_id": item_id,
                "name": evidence["name"],
                "before_category_id": str(current.get("category_id") or ""),
                "before_category_name": str(current.get("category_name") or ""),
                "target_category_id": proposed["category_id"],
                "target_category_name": proposed["name"],
            }
            owner_authority.capture_live_state(
                plan_path, captured, what="Zoho Inventory item category_id",
                where=f"{vault['inventory_organization_id']} /inventory/v1/items", plan_sha256=plan["sha256"],
            )
            in_flight = True
            api_write_allowed(
                access_token,
                str(vault["api_domain"]),
                "PUT",
                f"/inventory/v1/items/{item_id}",
                str(vault["inventory_organization_id"]),
                {"name": evidence["name"], "category_id": proposed["category_id"]},
            )
            verified = get_item(access_token, vault, item_id)
            if (
                str(verified.get("category_id") or "") != proposed["category_id"]
                or str(verified.get("category_name") or "") != proposed["name"]
            ):
                raise CategoryToolError(f"Item {item_id} live read-back did not verify the approved category.")
            verified_protected = protected_state(verified)
            if (
                verified_protected != evidence["protected_state"]
                or not secrets.compare_digest(
                    digest_for(verified_protected), evidence["protected_state_sha256"]
                )
            ):
                raise CategoryToolError(
                    f"Item {item_id} protected fields changed during category assignment. Stop and reconcile."
                )
            in_flight = False
            completed_item_ids.append(item_id)
            progress.done(item_id)
        except Exception as exc:  # noqa: BLE001 - recorded per line, the batch goes on (A6)
            progress.fail(item_id, str(exc))
            if in_flight:
                indeterminate.append(item_id)
    zoho_tool.save_vault(vault)
    if progress.status != owner_authority.STATUS_COMMITTED:
        finish_needs_restage(next(iter(progress.failed.values()), "a line failed"))
    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_COMMITTED, plan_sha256=plan["sha256"], action="category_assign", go=go,
        completed=completed_item_ids, completed_item_ids=completed_item_ids,
        category=plan["live_evidence"]["category"], backup=str(backup),
    ))
    zoho_tool.append_receipt(
        "zoho_inventory_category_assignment_committed_verified",
        f"category_id={plan['payload']['category_id']}; completed={','.join(completed_item_ids)}; "
        f"backup={backup}; plan={plan_path}; sha256={plan['sha256']}; go_lane={go.lane}",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "action": "category_assign",
        "plan_sha256": plan["sha256"],
        "category": plan["live_evidence"]["category"],
        "completed_item_ids": completed_item_ids,
        "atomic": False,
        "replay_locked": True,
        "plan_spent": True,
        "live_state_backup": str(backup),
        "restore": f"restore-assign --plan {plan_path} --approval <his go>",
    }, ensure_ascii=False, indent=2))


def command_restore_assign(args: argparse.Namespace) -> None:
    """A2: put back the category each item carried before this plan's PUT.

    One PUT per captured item, only while the item still carries the category
    this plan assigned. An item that had NO category before cannot be cleared
    through this transport (Zoho needs a category_id) and is reported as such.
    """
    require_verified_write_shapes()
    plan_path = contained_plan(args.plan)
    plan = read_json_object(plan_path, "Plan")
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise CategoryToolError("Plan hash check failed. The plan changed after review.")
    if plan.get("tool") != TOOL_NAME or plan.get("action") != "category_assign":
        raise CategoryToolError("Plan tool or action is invalid for a category restore.")
    go = require_rachad_approval(args.approval, getattr(args, "approval_lane", None),
                                 what="restoring the previous categories of this plan's items")
    record = owner_authority.load_live_state(plan_path, CategoryToolError)
    if record.get("plan_sha256") != saved:
        raise CategoryToolError("The captured live state belongs to a different plan.")
    restore_path = owner_authority.restore_record_path(plan_path)
    if restore_path.exists():
        raise CategoryToolError(
            f"This plan's categories were already restored ({restore_path.name}); stage a new plan "
            "for any further change."
        )
    vault = zoho_tool.load_vault()
    if UPDATE_SCOPE not in (vault.get("scopes") or []):
        raise CategoryToolError(f"Saved Zoho connection lacks {UPDATE_SCOPE}.")
    access_token, vault = zoho_tool.refresh_access_token(vault)
    restored: list[dict[str, str]] = []
    skipped: dict[str, str] = {}
    failed: dict[str, str] = {}
    for item_id, snapshot in record["state"].items():
        try:
            current = get_item(access_token, vault, item_id)
            live = str(current.get("category_id") or "")
            if live == snapshot["before_category_id"]:
                skipped[item_id] = "already at the captured category"
                continue
            if live != snapshot["target_category_id"]:
                skipped[item_id] = f"moved since this plan wrote it (live category {live or 'none'}); left alone"
                continue
            if not snapshot["before_category_id"]:
                skipped[item_id] = ("had no category before; clearing a category is not a commissioned "
                                    "write (Zoho needs a category_id)")
                continue
            api_write_allowed(
                access_token, str(vault["api_domain"]), "PUT",
                f"/inventory/v1/items/{item_id}", str(vault["inventory_organization_id"]),
                {"name": snapshot["name"], "category_id": snapshot["before_category_id"]},
            )
            verified = get_item(access_token, vault, item_id)
            if str(verified.get("category_id") or "") != snapshot["before_category_id"]:
                raise CategoryToolError(f"Item {item_id} read-back did not verify the restored category.")
            restored.append({"item_id": item_id, "from": snapshot["target_category_id"],
                             "to": snapshot["before_category_id"]})
        except Exception as exc:  # noqa: BLE001 - per line, the others still run
            failed[item_id] = str(exc)[:1000]
    zoho_tool.save_vault(vault)
    result = {
        "status": "RESTORED" if not failed else "RESTORE_PARTIAL",
        "plan_sha256": saved, "restored": restored, "skipped": skipped, "failed": failed,
        **go.as_record(),
    }
    owner_authority.record_restore(plan_path, result)
    zoho_tool.append_receipt(
        "zoho_inventory_category_assignment_restored",
        f"restored={','.join(row['item_id'] for row in restored) or 'none'}; skipped={','.join(skipped) or 'none'}; "
        f"failed={','.join(failed) or 'none'}; backup={owner_authority.live_state_path(plan_path)}; "
        f"plan={plan_path}; sha256={saved}",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failed:
        raise CategoryToolError(
            "Restore did not complete for every item: " + "; ".join(f"{k}: {v}" for k, v in failed.items())
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    list_categories = commands.add_parser("list-categories")
    list_categories.set_defaults(func=command_list_categories)
    stage_create = commands.add_parser("stage-create")
    stage_create.add_argument("--input", required=True)
    stage_create.set_defaults(func=command_stage_create)
    commit_create = commands.add_parser("commit-create")
    commit_create.add_argument("--plan", required=True)
    owner_authority.add_owner_go_arguments(commit_create)
    commit_create.set_defaults(func=command_commit_create)
    stage_assign = commands.add_parser("stage-assign")
    stage_assign.add_argument("--input", required=True)
    stage_assign.set_defaults(func=command_stage_assign)
    commit_assign = commands.add_parser("commit-assign")
    commit_assign.add_argument("--plan", required=True)
    owner_authority.add_owner_go_arguments(commit_assign)
    commit_assign.set_defaults(func=command_commit_assign)
    restore_assign = commands.add_parser("restore-assign", help="put back the categories captured before the PUTs")
    restore_assign.add_argument("--plan", required=True)
    owner_authority.add_owner_go_arguments(restore_assign)
    restore_assign.set_defaults(func=command_restore_assign)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (CategoryToolError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
