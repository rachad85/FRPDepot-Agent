#!/usr/bin/env python
"""Fixed approval-gated cleanup for WooCommerce FNPT parent product 2061.

Commissioned by Rachad Homsi on 2026-08-13. Commissioning authorizes building,
testing, and staging this tool; it is not approval to commit a store change.

This build has one possible write, attempted at most once after a replay lock:

    PUT /products/2061

The payload preserves the complete live parent-attribute list while removing the
one exact RESIN TYPE option ``Hetron 922`` and replaces the sole category 17 with
the already-existing category 58. There is no configurable resource, route,
method, field, category, option, batch, deletion, category-term write, or child
write. Stage is read-only. Commit requires the byte-exact approval ``APPROVED``
before credentials or network are touched, rechecks the complete staged parent
fingerprint before locking, performs one PUT with no retry, and verifies a fresh
live parent readback. Every failure after locking remains permanently locked as
indeterminate.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any

import woocommerce_common as wc

TOOL_NAME = "FRP Depot Fixed FNPT Catalog Cleanup Tool"
TOOL_VERSION = "1.0.2"
SCHEMA_VERSION = 3
PLAN_LIFETIME_HOURS = 24
APPROVAL_BYTES = b"APPROVED"
EXACT_ORIGIN = wc.ALLOWED_ORIGIN
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "woocommerce_fnpt_catalog_cleanup_plans"

PARENT_ID = 2061
PARENT_ENDPOINT = "/products/2061"
PARENT_NAME = "FNPT Coupling, Threaded On both ends"
PARENT_SKU = "ZOHO-GROUP-5961888EC5DB"
PARENT_TYPE = "variable"
PARENT_STATUS = "publish"
SOURCE_CATEGORY_ID = 17
TARGET_CATEGORY_ID = 58
TARGET_CATEGORY_ENDPOINT = "/products/categories/58"
RESIN_ATTRIBUTE_NAME = "RESIN TYPE"
REMOVED_RESIN_OPTION = "Hetron 922"
EXPECTED_RESIN_OPTIONS = (
    "Derakane 470-300",
    "Derakane 411-350",
    REMOVED_RESIN_OPTION,
    "Derakane 510 A-40",
)
TARGET_RESIN_OPTIONS = tuple(
    option for option in EXPECTED_RESIN_OPTIONS if option != REMOVED_RESIN_OPTION
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
NONCE32 = re.compile(r"^[0-9a-f]{32}$")
ATTRIBUTE_WRITE_KEYS = frozenset({"id", "name", "position", "visible", "variation", "options"})
ATTRIBUTE_READ_KEYS = ATTRIBUTE_WRITE_KEYS | {"slug"}
# These two timestamps are ordinary server save metadata. Every other parent
# field, including status, prices, stock, descriptions, images, SKU, name,
# metadata, and derived/plugin fields, remains protected after the write.
POSTWRITE_ALLOWED_PARENT_FIELDS = frozenset(
    {"attributes", "categories", "date_modified", "date_modified_gmt"}
)


class CatalogCleanupError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_value(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def digest_for(core: dict[str, Any]) -> str:
    return digest_value(core)


def fingerprint_projection(record: dict[str, Any]) -> dict[str, Any]:
    """Copy a product response while normalizing derived unordered ID sets.

    WooCommerce regenerates ``related_ids`` and may return the same IDs in a
    different order on every GET.  It is response-derived recommendation data,
    not stored parent state.  Sorting retains exact membership and duplicate
    detection while preventing random response order from defeating drift and
    readback verification.  No other field is normalized or omitted here.
    """
    projection = copy.deepcopy(record)
    related_ids = projection.get("related_ids")
    if isinstance(related_ids, list) and all(type(value) is int for value in related_ids):
        projection["related_ids"] = sorted(related_ids)
    return projection


def full_fingerprint(record: dict[str, Any]) -> str:
    """Hash every parent key/value, normalizing only unordered related IDs."""
    return digest_value(fingerprint_projection(record))


def protected_fingerprint(record: dict[str, Any]) -> str:
    """Hash everything except the two intended fields and save timestamps."""
    normalized = fingerprint_projection(record)
    projection = {
        key: normalized[key]
        for key in sorted(normalized)
        if key not in POSTWRITE_ALLOWED_PARENT_FIELDS
    }
    return digest_value(projection)


def read_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogCleanupError(f"Plan JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise CatalogCleanupError("Plan JSON must contain exactly one object.")
    return value


def strict_fixed_id(value: Any, expected: int, label: str) -> int:
    if type(value) is not int or value != expected:
        raise CatalogCleanupError(f"{label} must be the canonical integer {expected}.")
    return value


def validate_vault(vault: dict[str, Any]) -> None:
    if vault.get("declared_permissions") != "read_write":
        raise CatalogCleanupError("Saved WooCommerce key is not declared Read/Write.")
    try:
        origin = wc.normalize_site_url(str(vault.get("site_url") or ""))
    except wc.WooError as exc:
        raise CatalogCleanupError("Saved WooCommerce origin is not the exact FRP Depot origin.") from exc
    if origin != EXACT_ORIGIN:
        raise CatalogCleanupError("Saved WooCommerce origin is not the exact FRP Depot origin.")


def validate_route(method: str, endpoint: str) -> None:
    pair = (method, endpoint)
    allowed = {
        ("GET", PARENT_ENDPOINT),
        ("GET", TARGET_CATEGORY_ENDPOINT),
        ("PUT", PARENT_ENDPOINT),
    }
    if pair not in allowed:
        raise CatalogCleanupError(
            "REFUSED: method/path is outside the fixed one-parent catalog cleanup."
        )


def category_projection(value: Any) -> list[dict[str, int]]:
    if not isinstance(value, list) or not value:
        raise CatalogCleanupError("Parent categories must be a non-empty list.")
    result: list[dict[str, int]] = []
    seen: set[int] = set()
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise CatalogCleanupError(f"Parent category {index} is not an object.")
        category_id = row.get("id")
        if type(category_id) is not int or category_id <= 0 or category_id in seen:
            raise CatalogCleanupError("Parent category IDs must be unique canonical positive integers.")
        result.append({"id": category_id})
        seen.add(category_id)
    return result


def validate_attributes(value: Any) -> list[dict[str, Any]]:
    """Validate and copy the complete fixed-parent custom-attribute representation."""
    if not isinstance(value, list) or not value or len(value) > 100:
        raise CatalogCleanupError("Parent attributes must be a list of 1 to 100 records.")
    result: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, row in enumerate(value):
        # WooCommerce's edit-context GET includes a derived `slug` on custom
        # attributes. It is response-only: validate it, then project the fixed
        # six writable fields so it can never enter the PUT.
        if not isinstance(row, dict) or frozenset(row) not in {
                ATTRIBUTE_WRITE_KEYS, ATTRIBUTE_READ_KEYS}:
            raise CatalogCleanupError(
                f"Parent attribute {index} must have exactly id, name, position, visible, variation, options, "
                "with only WooCommerce's optional derived slug permitted."
            )
        # All three current FNPT selectors are named custom attributes. Refusing a
        # global or malformed record is safer than silently changing its write shape.
        if type(row["id"]) is not int or row["id"] != 0:
            raise CatalogCleanupError("Every FNPT parent selector must remain a custom attribute with id 0.")
        name = row["name"]
        if (not isinstance(name, str) or not name or len(name) > 200
                or name in seen_names or any(ord(char) < 32 for char in name)):
            raise CatalogCleanupError("FNPT attribute names must be unique non-empty plain strings.")
        if "slug" in row and row["slug"] != name:
            raise CatalogCleanupError("Every live FNPT custom-attribute slug must exactly match its name.")
        if type(row["position"]) is not int or row["position"] < 0:
            raise CatalogCleanupError("FNPT attribute positions must be canonical non-negative integers.")
        if type(row["visible"]) is not bool or type(row["variation"]) is not bool:
            raise CatalogCleanupError("FNPT attribute visible and variation flags must be booleans.")
        options = row["options"]
        if not isinstance(options, list) or not options or len(options) > 100:
            raise CatalogCleanupError("Every FNPT attribute must have 1 to 100 options.")
        if any(not isinstance(option, str) or not option or len(option) > 200
               or any(ord(char) < 32 for char in option) for option in options):
            raise CatalogCleanupError("FNPT attribute options must be non-empty plain strings.")
        if len(options) != len(set(options)):
            raise CatalogCleanupError("FNPT attribute options must not contain exact duplicates.")
        result.append({
            "id": 0,
            "name": name,
            "position": row["position"],
            "visible": row["visible"],
            "variation": row["variation"],
            "options": list(options),
        })
        seen_names.add(name)
    return result


def validate_parent_identity(record: dict[str, Any]) -> None:
    strict_fixed_id(record.get("id"), PARENT_ID, "Parent ID")
    if (record.get("name") != PARENT_NAME or record.get("sku") != PARENT_SKU
            or record.get("type") != PARENT_TYPE or record.get("status") != PARENT_STATUS):
        raise CatalogCleanupError(
            "Parent 2061 must remain the exact published variable FNPT product with its fixed name and SKU."
        )


def target_attributes_from_before(before_attributes: Any) -> list[dict[str, Any]]:
    attributes = validate_attributes(before_attributes)
    resin_indexes = [
        index for index, attribute in enumerate(attributes)
        if attribute["name"] == RESIN_ATTRIBUTE_NAME
    ]
    if len(resin_indexes) != 1:
        raise CatalogCleanupError("Parent 2061 must have exactly one exact RESIN TYPE attribute.")
    resin_index = resin_indexes[0]
    if attributes[resin_index]["options"] != list(EXPECTED_RESIN_OPTIONS):
        raise CatalogCleanupError(
            "Parent RESIN TYPE options are not the exact commissioned four-option before-state."
        )
    target = copy.deepcopy(attributes)
    target[resin_index]["options"] = list(TARGET_RESIN_OPTIONS)
    return target


def fixed_payload_from_parent(record: dict[str, Any]) -> dict[str, Any]:
    validate_parent_identity(record)
    if category_projection(record.get("categories")) != [{"id": SOURCE_CATEGORY_ID}]:
        raise CatalogCleanupError("Parent 2061 must have only source category 17 before staging/commit.")
    before_attributes = validate_attributes(record.get("attributes"))
    return {
        "attributes": target_attributes_from_before(before_attributes),
        "categories": [{"id": TARGET_CATEGORY_ID}],
    }


def validate_payload_against_before(payload: Any, before_attributes: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"attributes", "categories"}:
        raise CatalogCleanupError("Cleanup payload must contain exactly attributes and categories.")
    expected = {
        "attributes": target_attributes_from_before(before_attributes),
        "categories": [{"id": TARGET_CATEGORY_ID}],
    }
    if payload != expected:
        raise CatalogCleanupError("Cleanup payload is not the exact option-removal/category-reassignment payload.")
    return expected


def validate_target_category(record: Any) -> str:
    if not isinstance(record, dict):
        raise CatalogCleanupError("Existing category 58 lookup returned an unexpected response.")
    strict_fixed_id(record.get("id"), TARGET_CATEGORY_ID, "Target category ID")
    return full_fingerprint(record)


def read_parent(vault: dict[str, Any]) -> dict[str, Any]:
    validate_route("GET", PARENT_ENDPOINT)
    value, _ = wc.api_get(PARENT_ENDPOINT, vault=vault)
    if not isinstance(value, dict):
        raise CatalogCleanupError("WooCommerce parent 2061 GET returned an unexpected response.")
    return value


def read_target_category(vault: dict[str, Any]) -> dict[str, Any]:
    validate_route("GET", TARGET_CATEGORY_ENDPOINT)
    value, _ = wc.api_get(TARGET_CATEGORY_ENDPOINT, vault=vault)
    if not isinstance(value, dict):
        raise CatalogCleanupError("WooCommerce category 58 GET returned an unexpected response.")
    return value


def commission_contract() -> dict[str, Any]:
    return {
        "requestor": "Rachad Homsi",
        "request_date": "2026-08-13",
        "commissioning_scope": "build_test_and_stage_only_not_commit",
        "commit_requires_separate_exact_plan_approval": "APPROVED",
    }


def transaction_contract(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "parent_id": PARENT_ID,
        "write_count": 1,
        "only_write": {
            "method": "PUT",
            "endpoint": PARENT_ENDPOINT,
            "payload": copy.deepcopy(payload),
        },
        "category_transition": {
            "from_only_id": SOURCE_CATEGORY_ID,
            "to_only_existing_id": TARGET_CATEGORY_ID,
        },
        "attribute_transition": {
            "attribute_name": RESIN_ATTRIBUTE_NAME,
            "remove_exact_option": REMOVED_RESIN_OPTION,
            "all_other_attributes_and_options": "preserve_exactly",
        },
        "failure_policy": "lock_before_one_put_one_attempt_no_retry_no_rollback_indeterminate_stays_locked",
        "forbidden_write_scope": (
            "children,batch,delete,status,price,stock,name,sku,descriptions,images,category_terms"
        ),
    }


def stage_plan(parent: dict[str, Any], category: dict[str, Any], payload: dict[str, Any]) -> Path:
    created = utc_now()
    before_attributes = validate_attributes(parent.get("attributes"))
    category_hash = validate_target_category(category)
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "origin": EXACT_ORIGIN,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "commission": commission_contract(),
        "parent_before": {
            "full_fingerprint": full_fingerprint(parent),
            "protected_fingerprint": protected_fingerprint(parent),
            "categories": [{"id": SOURCE_CATEGORY_ID}],
            "attributes": before_attributes,
        },
        "target_category": {
            "id": TARGET_CATEGORY_ID,
            "full_fingerprint": category_hash,
        },
        "transaction": transaction_contract(payload),
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{created.strftime('%Y%m%dT%H%M%SZ')}_fnpt_2061_catalog_cleanup_{digest[:16]}.json"
    path = PLAN_DIR / filename
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    wc.append_receipt("woocommerce_fnpt_catalog_cleanup_plan_staged", str(path))
    return path


def load_plan(path: str | Path) -> dict[str, Any]:
    plan = read_json(path)
    saved = plan.pop("sha256", None)
    if (not isinstance(saved, str) or not HEX64.fullmatch(saved)
            or not secrets.compare_digest(saved, digest_for(plan))):
        raise CatalogCleanupError("Plan SHA-256 check failed; the reviewed bytes changed.")
    required = {
        "schema_version", "tool", "tool_version", "origin", "created_utc", "expires_utc",
        "nonce", "commission", "parent_before", "target_category", "transaction",
    }
    if set(plan) != required:
        raise CatalogCleanupError("Plan schema is not closed and exact.")
    if (plan["schema_version"] != SCHEMA_VERSION or plan["tool"] != TOOL_NAME
            or plan["tool_version"] != TOOL_VERSION or plan["origin"] != EXACT_ORIGIN
            or plan["commission"] != commission_contract()):
        raise CatalogCleanupError("Plan fixed identity or commissioning contract differs from this build.")
    try:
        created = datetime.fromisoformat(plan["created_utc"])
        expires = datetime.fromisoformat(plan["expires_utc"])
    except (TypeError, ValueError) as exc:
        raise CatalogCleanupError("Plan timestamps are invalid.") from exc
    now = utc_now()
    if (created.tzinfo is None or expires.tzinfo is None
            or created.utcoffset() != timedelta(0) or expires.utcoffset() != timedelta(0)
            or expires - created != timedelta(hours=PLAN_LIFETIME_HOURS)
            or created > now + timedelta(minutes=1) or now >= expires):
        raise CatalogCleanupError("Plan timing is invalid or its exact 24-hour lifetime expired.")
    if not isinstance(plan["nonce"], str) or not NONCE32.fullmatch(plan["nonce"]):
        raise CatalogCleanupError("Plan nonce must be exactly 16 random bytes encoded as lowercase hex.")

    parent_before = plan["parent_before"]
    if not isinstance(parent_before, dict) or set(parent_before) != {
        "full_fingerprint", "protected_fingerprint", "categories", "attributes"
    }:
        raise CatalogCleanupError("Plan parent-before schema is not closed and exact.")
    if (not isinstance(parent_before["full_fingerprint"], str)
            or not HEX64.fullmatch(parent_before["full_fingerprint"])
            or not isinstance(parent_before["protected_fingerprint"], str)
            or not HEX64.fullmatch(parent_before["protected_fingerprint"])
            or parent_before["categories"] != [{"id": SOURCE_CATEGORY_ID}]):
        raise CatalogCleanupError("Plan parent-before fingerprints/categories are invalid.")
    before_attributes = validate_attributes(parent_before["attributes"])
    if before_attributes != parent_before["attributes"]:
        raise CatalogCleanupError("Plan parent-before attributes are not canonical.")

    target_category = plan["target_category"]
    if (not isinstance(target_category, dict)
            or set(target_category) != {"id", "full_fingerprint"}
            or type(target_category.get("id")) is not int
            or target_category.get("id") != TARGET_CATEGORY_ID
            or not isinstance(target_category.get("full_fingerprint"), str)
            or not HEX64.fullmatch(target_category["full_fingerprint"])):
        raise CatalogCleanupError("Plan target-category evidence is invalid.")

    transaction = plan["transaction"]
    if not isinstance(transaction, dict):
        raise CatalogCleanupError("Plan transaction must be one object.")
    only_write = transaction.get("only_write")
    payload = only_write.get("payload") if isinstance(only_write, dict) else None
    validated_payload = validate_payload_against_before(payload, before_attributes)
    if transaction != transaction_contract(validated_payload):
        raise CatalogCleanupError("Plan transaction differs from the one fixed write contract.")
    plan["sha256"] = saved
    return plan


def lock_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".commit-lock.json")


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise CatalogCleanupError("This plan already entered commit and cannot be replayed.") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def require_exact_approval(value: Any) -> None:
    if not isinstance(value, str):
        raise CatalogCleanupError("Rachad must approve with the byte-exact word APPROVED.")
    try:
        supplied = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:  # pragma: no cover - Python strings normally encode
        raise CatalogCleanupError("Rachad must approve with the byte-exact word APPROVED.") from exc
    if not secrets.compare_digest(supplied, APPROVAL_BYTES):
        raise CatalogCleanupError(
            "Rachad must approve this exact staged plan with the unpadded uppercase word APPROVED."
        )


def assert_prelock_state(plan: dict[str, Any], parent: dict[str, Any], category: dict[str, Any]) -> dict[str, Any]:
    """Require a complete parent-byte projection match immediately before lock."""
    payload = fixed_payload_from_parent(parent)
    expected_payload = plan["transaction"]["only_write"]["payload"]
    if payload != expected_payload:
        raise CatalogCleanupError("Live cleanup target differs from the staged exact payload.")
    if not secrets.compare_digest(
        full_fingerprint(parent), plan["parent_before"]["full_fingerprint"]
    ):
        raise CatalogCleanupError("Full parent state changed after staging; nothing was locked or written.")
    if not secrets.compare_digest(
        protected_fingerprint(parent), plan["parent_before"]["protected_fingerprint"]
    ):
        raise CatalogCleanupError("Protected parent state changed after staging; nothing was locked or written.")
    category_hash = validate_target_category(category)
    if not secrets.compare_digest(category_hash, plan["target_category"]["full_fingerprint"]):
        raise CatalogCleanupError("Existing target category 58 changed after staging; nothing was written.")
    return payload


def perform_only_write(vault: dict[str, Any], payload: dict[str, Any],
                       before_attributes: list[dict[str, Any]]) -> None:
    validated = validate_payload_against_before(payload, before_attributes)
    validate_route("PUT", PARENT_ENDPOINT)
    # Exactly one transport invocation. No loop, retry, rollback, alternate route,
    # or second PUT exists in this module.
    wc.api_request("PUT", PARENT_ENDPOINT, payload=validated, vault=vault)


def verify_live_readback(plan: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    validate_parent_identity(record)
    payload = plan["transaction"]["only_write"]["payload"]
    if category_projection(record.get("categories")) != [{"id": TARGET_CATEGORY_ID}]:
        raise CatalogCleanupError("Live readback is not assigned only to existing category 58.")
    attributes = validate_attributes(record.get("attributes"))
    if attributes != payload["attributes"]:
        raise CatalogCleanupError("Live readback does not contain the exact approved parent attributes.")
    if not secrets.compare_digest(
        protected_fingerprint(record), plan["parent_before"]["protected_fingerprint"]
    ):
        raise CatalogCleanupError("A non-approved parent field changed during the catalog cleanup.")
    return {
        "parent_id": PARENT_ID,
        "category_ids": [TARGET_CATEGORY_ID],
        "removed_parent_option": REMOVED_RESIN_OPTION,
        "protected_parent_fields_preserved": True,
        "child_writes": 0,
    }


def command_stage(_: argparse.Namespace) -> None:
    vault = wc.load_vault()
    validate_vault(vault)
    parent = read_parent(vault)
    category = read_target_category(vault)
    payload = fixed_payload_from_parent(parent)
    validate_target_category(category)
    path = stage_plan(parent, category, payload)
    plan = read_json(path)
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED",
        "external_write_performed": False,
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "parent_id": PARENT_ID,
        "write_count": 1,
        "only_write": transaction_contract(payload)["only_write"],
        "approval": "APPROVED",
        "commissioning_is_not_commit_approval": True,
        "child_writes": 0,
    }, indent=2, ensure_ascii=False))


def command_commit(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve()
    try:
        plan_path.relative_to(PLAN_DIR.resolve())
    except ValueError as exc:
        raise CatalogCleanupError("Plan must be inside the fixed FNPT catalog-cleanup plan folder.") from exc
    plan = load_plan(plan_path)

    # Exact byte approval is deliberately checked before lock inspection, vault
    # decryption, credential validation, or either network GET.
    require_exact_approval(args.approval)
    lock = lock_path(plan_path)
    if lock.exists():
        raise CatalogCleanupError("This plan already entered commit and cannot be replayed.")

    vault = wc.load_vault()
    validate_vault(vault)
    parent = read_parent(vault)
    category = read_target_category(vault)
    payload = assert_prelock_state(plan, parent, category)

    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "status": "in_flight",
        "started_utc": utc_now().isoformat(),
        "attempt": 1,
        "write_count": 1,
        "prelock_parent_full_fingerprint": full_fingerprint(parent),
    }, exclusive=True)
    phase = "fixed_parent_put"
    writes_completed = 0
    try:
        perform_only_write(vault, payload, plan["parent_before"]["attributes"])
        writes_completed = 1
        phase = "fresh_live_parent_readback"
        readback = read_parent(vault)
        outcome = verify_live_readback(plan, readback)
    except Exception as exc:
        reason = {
            "reason_class": type(exc).__name__,
            "http_status": exc.status if isinstance(exc, wc.WooError) else None,
            "rest_code": exc.code if isinstance(exc, wc.WooError) else None,
        }
        write_lock(lock, {
            "plan_sha256": plan["sha256"],
            "status": "indeterminate",
            "updated_utc": utc_now().isoformat(),
            "attempt": 1,
            "phase": phase,
            "writes_completed": writes_completed,
            **reason,
        })
        wc.append_receipt(
            "woocommerce_fnpt_catalog_cleanup_indeterminate_no_retry",
            f"plan={plan_path}; sha256={plan['sha256']}; phase={phase}; "
            f"writes_completed={writes_completed}; reason_class={reason['reason_class']}",
        )
        raise CatalogCleanupError(
            "FNPT catalog cleanup failed after locking. The one-attempt plan remains permanently "
            "locked indeterminate and will not retry, roll back, delete, or write a child."
        ) from exc

    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "status": "committed_verified",
        "updated_utc": utc_now().isoformat(),
        "attempt": 1,
        "writes_completed": 1,
        "outcome": outcome,
    })
    wc.append_receipt(
        "woocommerce_fnpt_catalog_cleanup_committed_verified",
        f"parent_id={PARENT_ID}; plan={plan_path}; sha256={plan['sha256']}; writes_completed=1",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "plan_sha256": plan["sha256"],
        "replay_locked": True,
        "attempts": 1,
        "writes_completed": 1,
        "outcome": outcome,
    }, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage", help="read-only stage of the fixed parent cleanup")
    stage.set_defaults(func=command_stage)
    commit = commands.add_parser("commit", help="commit one separately approved fixed plan once")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (CatalogCleanupError, wc.WooError, OSError, ValueError) as exc:
        print("ERROR: " + wc.scrub(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
