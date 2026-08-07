#!/usr/bin/env python
"""FRP Depot WooCommerce Audit & Approved Catalog Change Tool.

Commissioned by Rachad Homsi on 2026-07-24.

Only four write routes exist: product/variation create or update. Creation is
forced to draft. No delete, batch, order, customer, payment, refund, coupon,
webhook, settings, plugin/theme, user, stock, sale-price, image-source, or
publication write is available.
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

import woocommerce_common as wc

TOOL_NAME = "FRP Depot WooCommerce Audit & Approved Catalog Change Tool"
SCHEMA_VERSION = 2
EXACT_ORIGIN = "https://frpdepots.com:443"
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "woocommerce_plans"
PLAN_LIFETIME_HOURS = 24
ACTIONS = {"product_create", "product_update", "variation_create", "variation_update"}
# Rachad's explicit workflow authorization (2026-08-06): approval of a staged
# WooCommerce plan is his own one-word reply.  The full plan digest remains an
# internal integrity control and is never part of the approval text.  Building
# this workflow is not approval of any catalog change.
APPROVAL_WORD = "APPROVED"
PRODUCT_FIELDS = {
    "name", "type", "sku", "description", "short_description", "regular_price",
    "categories", "attributes",
}
VARIATION_FIELDS = {"sku", "description", "regular_price", "attributes"}
DECIMAL_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
UNSAFE_HTML_RE = re.compile(
    r"(?is)<\s*(?:script|iframe|object|embed|form|style|svg|math)\b|"
    r"javascript\s*:|\bon[a-z]+\s*="
)


class ChangeError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def read_json(path: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChangeError(f"Input JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ChangeError("Input JSON must contain one object.")
    return value


def clean_id(value: Any, label: str, required: bool = True) -> int | None:
    if value in (None, "") and not required:
        return None
    if isinstance(value, bool):
        raise ChangeError(f"{label} must be a positive integer.")
    text = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise ChangeError(f"{label} must be a positive integer.")
    return int(text)


def clean_decimal(value: Any, label: str) -> str:
    text = str(value if value is not None else "").strip()
    if not DECIMAL_RE.fullmatch(text):
        raise ChangeError(f"{label} must be a non-negative decimal string.")
    return text


def clean_text(value: Any, label: str, *, required: bool = False, maximum: int = 100000) -> str:
    text = str(value if value is not None else "")
    if required and not text.strip():
        raise ChangeError(f"{label} cannot be blank.")
    if len(text) > maximum:
        raise ChangeError(f"{label} exceeds the {maximum}-character safety limit.")
    if label in {"description", "short_description"} and UNSAFE_HTML_RE.search(text):
        raise ChangeError(f"{label} contains unsafe HTML or JavaScript.")
    if any(ord(char) < 32 and char not in "\r\n\t" for char in text):
        raise ChangeError(f"{label} contains control characters.")
    return text


def clean_categories(value: Any) -> list[dict[str, int]]:
    if not isinstance(value, list) or not value or len(value) > 100:
        raise ChangeError("categories must be a list of 1 to 100 existing category IDs.")
    output = []
    for index, row in enumerate(value):
        if not isinstance(row, dict) or set(row) != {"id"}:
            raise ChangeError(f"categories[{index}] must contain only id.")
        output.append({"id": int(clean_id(row["id"], f"categories[{index}].id"))})
    return output


def clean_product_attributes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 100:
        raise ChangeError("attributes must be a list of at most 100 global attributes.")
    output = []
    required = {"id", "position", "visible", "variation", "options"}
    for index, row in enumerate(value):
        if not isinstance(row, dict) or set(row) != required:
            raise ChangeError(
                f"attributes[{index}] must contain exactly id, position, visible, variation, options."
            )
        position = row["position"]
        if isinstance(position, bool) or not isinstance(position, int) or position < 0:
            raise ChangeError(f"attributes[{index}].position must be a non-negative integer.")
        if not isinstance(row["visible"], bool) or not isinstance(row["variation"], bool):
            raise ChangeError(f"attributes[{index}] visible and variation must be true or false.")
        options = row["options"]
        if not isinstance(options, list) or not options or len(options) > 100:
            raise ChangeError(f"attributes[{index}].options must have 1 to 100 values.")
        cleaned_options = [clean_text(item, f"attributes[{index}].options", required=True, maximum=200)
                           for item in options]
        output.append({
            "id": int(clean_id(row["id"], f"attributes[{index}].id")),
            "position": position, "visible": row["visible"],
            "variation": row["variation"], "options": cleaned_options,
        })
    return output


def clean_variation_attributes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > 100:
        raise ChangeError("attributes must be a list of 1 to 100 global attribute selections.")
    output = []
    for index, row in enumerate(value):
        if not isinstance(row, dict) or set(row) != {"id", "option"}:
            raise ChangeError(f"attributes[{index}] must contain exactly id and option.")
        output.append({
            "id": int(clean_id(row["id"], f"attributes[{index}].id")),
            "option": clean_text(row["option"], f"attributes[{index}].option", required=True, maximum=200),
        })
    return output


def clean_changes(action: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        raise ChangeError("changes must be a non-empty object.")
    allowed = PRODUCT_FIELDS if action.startswith("product_") else VARIATION_FIELDS
    extra = sorted(set(raw) - allowed)
    if extra:
        raise ChangeError("Unsupported change field(s): " + ", ".join(extra))
    output: dict[str, Any] = {}
    for field, value in raw.items():
        if field == "name":
            output[field] = clean_text(value, field, required=True, maximum=250)
        elif field == "type":
            text = str(value or "").strip().casefold()
            if text not in {"simple", "variable"}:
                raise ChangeError("type must be simple or variable.")
            output[field] = text
        elif field == "sku":
            output[field] = clean_text(value, field, required=True, maximum=100).strip()
        elif field in {"description", "short_description"}:
            output[field] = clean_text(value, field)
        elif field == "regular_price":
            output[field] = clean_decimal(value, field)
        elif field == "categories":
            output[field] = clean_categories(value)
        elif field == "attributes":
            output[field] = (clean_product_attributes(value) if action.startswith("product_")
                             else clean_variation_attributes(value))
        else:  # pragma: no cover - closed allowlist makes this unreachable
            raise ChangeError(f"Unsupported change field: {field}")
    if action == "product_create":
        for required in ("name", "type", "sku"):
            if required not in output:
                raise ChangeError(f"Product creation requires {required}.")
        if output["type"] == "simple" and "regular_price" not in output:
            raise ChangeError("Simple product creation requires regular_price.")
    if action == "variation_create":
        for required in ("sku", "regular_price", "attributes"):
            if required not in output:
                raise ChangeError(f"Variation creation requires {required}.")
    return output


def payload_for(action: str, changes: dict[str, Any]) -> dict[str, Any]:
    payload = dict(changes)
    if action.endswith("create"):
        payload["status"] = "draft"
    return payload


def verify_sources(changes: dict[str, Any], raw: Any, action: str) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != set(changes):
        raise ChangeError("sources must contain exactly the same fields as changes.")
    result = {}
    for field in changes:
        source = str(raw.get(field) or "").strip()
        if not source:
            raise ChangeError(f"sources.{field} is required.")
        result[field] = source
    if action.endswith("create"):
        result["status"] = "Tool safety rule: creation is always draft"
    return result


def endpoint_for(action: str, resource_id: int | None, parent_id: int | None) -> tuple[str, str]:
    if action == "product_create":
        return "POST", "/products"
    if action == "product_update":
        return "PUT", f"/products/{resource_id}"
    if action == "variation_create":
        return "POST", f"/products/{parent_id}/variations"
    if action == "variation_update":
        return "PUT", f"/products/{parent_id}/variations/{resource_id}"
    raise ChangeError("Unsupported action.")


def current_endpoint(action: str, resource_id: int | None, parent_id: int | None) -> str | None:
    if action == "product_update":
        return f"/products/{resource_id}"
    if action == "variation_update":
        return f"/products/{parent_id}/variations/{resource_id}"
    return None


def validate_write_route(method: str, endpoint: str) -> None:
    allowed = (
        (method == "POST" and endpoint == "/products")
        or (method == "PUT" and re.fullmatch(r"/products/[1-9][0-9]*", endpoint))
        or (method == "POST" and re.fullmatch(r"/products/[1-9][0-9]*/variations", endpoint))
        or (method == "PUT" and re.fullmatch(
            r"/products/[1-9][0-9]*/variations/[1-9][0-9]*", endpoint
        ))
    )
    if not allowed:
        raise ChangeError("REFUSED: write method/path pair is not allowlisted.")


def _project_response(value: Any, template: Any) -> Any:
    if isinstance(template, dict):
        source = value if isinstance(value, dict) else {}
        return {key: _project_response(source.get(key), child) for key, child in template.items()}
    if isinstance(template, list):
        source = value if isinstance(value, list) else []
        return [_project_response(source[index] if index < len(source) else None, child)
                for index, child in enumerate(template)]
    return value


def selected_state(record: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    return {field: _project_response(record.get(field), desired)
            for field, desired in sorted(template.items())}


def safe_resource_fingerprint(record: dict[str, Any], action: str) -> str:
    fields = PRODUCT_FIELDS if action.startswith("product_") else VARIATION_FIELDS
    safe = {field: record.get(field) for field in sorted(fields)}
    safe["id"] = record.get("id")
    safe["date_modified_gmt"] = record.get("date_modified_gmt")
    return hashlib.sha256(canonical(safe).encode("utf-8")).hexdigest()


def assert_sku_unique(action: str, sku: str, resource_id: int | None,
                      parent_id: int | None, vault: dict[str, Any] | None = None) -> None:
    if not sku:
        return
    endpoint = "/products" if action.startswith("product_") else f"/products/{parent_id}/variations"
    rows, _ = wc.api_get(endpoint, {"sku": sku, "per_page": 100, "_fields": "id,sku"}, vault)
    if not isinstance(rows, list):
        raise ChangeError("SKU uniqueness check returned an unexpected response.")
    conflicts = [int(row.get("id") or 0) for row in rows if int(row.get("id") or 0) != int(resource_id or 0)]
    if conflicts:
        raise ChangeError(f"SKU already belongs to another WooCommerce resource: {conflicts[:10]}")


def require_rachad_approval(approval: str) -> None:
    if str(approval).strip().casefold() != APPROVAL_WORD.casefold():
        raise ChangeError(
            f"Rachad must answer this staged plan with the one-word approval: {APPROVAL_WORD}. "
            "It must come from his own message; workflow authorization is not change approval."
        )


def lock_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".commit-lock.json")


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise ChangeError("This plan has already entered commit and cannot be replayed.") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def stage_plan(action: str, resource_id: int | None, parent_id: int | None,
               payload: dict[str, Any], sources: dict[str, str], before: dict[str, Any] | None,
               before_fingerprint: str | None, before_modified_gmt: str | None) -> Path:
    created = utc_now()
    method, endpoint = endpoint_for(action, resource_id, parent_id)
    validate_write_route(method, endpoint)
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "origin": EXACT_ORIGIN,
        "method": method,
        "endpoint": endpoint,
        "action": action,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "resource_id": resource_id,
        "parent_id": parent_id,
        "before": before,
        "before_fingerprint": before_fingerprint,
        "before_date_modified_gmt": before_modified_gmt,
        "payload": payload,
        "sources": sources,
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{action}_{digest[:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    wc.append_receipt("woocommerce_change_plan_staged", str(path))
    return path


def command_stage(args: argparse.Namespace) -> None:
    raw = read_json(args.input)
    extra = sorted(set(raw) - {"action", "resource_id", "parent_id", "changes", "sources"})
    if extra:
        raise ChangeError("Unsupported top-level field(s): " + ", ".join(extra))
    action = str(raw.get("action") or "").strip().casefold()
    if action not in ACTIONS:
        raise ChangeError("action must be one of: " + ", ".join(sorted(ACTIONS)))
    resource_id = clean_id(raw.get("resource_id"), "resource_id", required=action.endswith("update"))
    parent_id = clean_id(raw.get("parent_id"), "parent_id", required=action.startswith("variation_"))
    if action.startswith("product_") and parent_id is not None:
        raise ChangeError("parent_id is valid only for variations.")
    if action.endswith("create") and resource_id is not None:
        raise ChangeError("resource_id is valid only for updates.")
    changes = clean_changes(action, raw.get("changes"))
    payload = payload_for(action, changes)
    sources = verify_sources(changes, raw.get("sources"), action)
    before = None
    before_fingerprint = None
    before_modified_gmt = None
    existing = current_endpoint(action, resource_id, parent_id)
    if existing:
        record, _ = wc.api_get(existing)
        if not isinstance(record, dict) or int(record.get("id") or 0) != resource_id:
            raise ChangeError("The existing WooCommerce resource could not be verified.")
        before = selected_state(record, payload)
        if before == payload:
            raise ChangeError("No change was detected.")
        before_fingerprint = safe_resource_fingerprint(record, action)
        before_modified_gmt = str(record.get("date_modified_gmt") or "")
    assert_sku_unique(action, str(payload.get("sku") or ""), resource_id, parent_id)
    path = stage_plan(action, resource_id, parent_id, payload, sources, before,
                      before_fingerprint, before_modified_gmt)
    plan = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED",
        "plan": str(path),
        "expires_utc": plan["expires_utc"],
        "action": action,
        "method": plan["method"], "endpoint": plan["endpoint"],
        "resource_id": resource_id, "parent_id": parent_id,
        "before": before, "payload": payload, "sources": sources,
        "approval": APPROVAL_WORD,
    }, indent=2, ensure_ascii=False))


def load_plan(path: str) -> dict[str, Any]:
    plan = read_json(path)
    saved = str(plan.pop("sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(plan)):
        raise ChangeError("Plan hash check failed. The plan changed after review.")
    if (plan.get("schema_version") != SCHEMA_VERSION or plan.get("tool") != TOOL_NAME
            or plan.get("action") not in ACTIONS or plan.get("origin") != EXACT_ORIGIN):
        raise ChangeError("The plan schema, tool, action, or origin is invalid.")
    try:
        expires = datetime.fromisoformat(str(plan["expires_utc"]))
    except (KeyError, ValueError) as exc:
        raise ChangeError("Plan expiry is invalid.") from exc
    if utc_now() >= expires:
        raise ChangeError("Plan expired. Stage a new plan for review.")
    action = str(plan["action"])
    resource_id = clean_id(
        plan.get("resource_id"), "resource_id", required=action.endswith("update")
    )
    parent_id = clean_id(
        plan.get("parent_id"), "parent_id", required=action.startswith("variation_")
    )
    if action.startswith("product_") and parent_id is not None:
        raise ChangeError("Plan parent_id is valid only for variations.")
    if action.endswith("create") and resource_id is not None:
        raise ChangeError("Plan resource_id is valid only for updates.")
    method, endpoint = endpoint_for(action, resource_id, parent_id)
    validate_write_route(method, endpoint)
    if plan.get("method") != method or plan.get("endpoint") != endpoint:
        raise ChangeError("Plan method or endpoint is not the internally constructed allowlisted route.")
    # Re-run closed payload/source validation and immutable-draft injection.
    caller_changes = {k: v for k, v in dict(plan.get("payload") or {}).items() if k != "status"}
    validated_changes = clean_changes(action, caller_changes)
    expected_payload = payload_for(action, validated_changes)
    if expected_payload != plan.get("payload"):
        raise ChangeError("Plan payload failed the current allowlist validation.")
    raw_sources = dict(plan.get("sources") or {})
    caller_sources = {k: v for k, v in raw_sources.items() if k != "status"}
    if verify_sources(validated_changes, caller_sources, action) != raw_sources:
        raise ChangeError("Plan field sources failed validation.")
    plan["sha256"] = saved
    return plan


def command_commit(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve()
    if PLAN_DIR.resolve() not in plan_path.parents:
        raise ChangeError("Plan must be inside Dado's WooCommerce plan folder.")
    plan = load_plan(str(plan_path))
    require_rachad_approval(args.approval)
    if lock_path(plan_path).exists():
        raise ChangeError("This plan has already entered commit and cannot be replayed.")
    vault = wc.load_vault()
    if vault.get("declared_permissions") != "read_write":
        raise ChangeError("Saved WooCommerce key is not declared Read/Write.")
    if wc.normalize_site_url(str(vault.get("site_url") or "")) != wc.ALLOWED_ORIGIN:
        raise ChangeError("Saved WooCommerce origin is not the exact FRP Depot origin.")
    action = str(plan["action"])
    resource_id = plan.get("resource_id")
    parent_id = plan.get("parent_id")
    existing = current_endpoint(action, resource_id, parent_id)
    if existing:
        current, _ = wc.api_get(existing, vault=vault)
        if not isinstance(current, dict):
            raise ChangeError("Current resource verification returned an unexpected response.")
        if (safe_resource_fingerprint(current, action) != plan.get("before_fingerprint")
                or str(current.get("date_modified_gmt") or "") != plan.get("before_date_modified_gmt")):
            raise ChangeError("The WooCommerce resource changed after review. Stage a new plan.")
    assert_sku_unique(action, str(plan["payload"].get("sku") or ""), resource_id, parent_id, vault)
    method, endpoint = endpoint_for(action, resource_id, parent_id)
    validate_write_route(method, endpoint)
    lock = lock_path(plan_path)
    write_lock(lock, {
        "plan_sha256": plan["sha256"], "status": "in_flight", "started_utc": utc_now().isoformat()
    }, exclusive=True)
    try:
        result, _ = wc.api_request(method, endpoint, payload=plan["payload"], vault=vault)
        if not isinstance(result, dict) or not int(result.get("id") or 0):
            raise ChangeError("WooCommerce returned success without a resource ID.")
        result_id = int(result["id"])
        if action.endswith("update") and result_id != int(resource_id):
            raise ChangeError("WooCommerce returned a different resource ID after the update.")
        verify_endpoint = (f"/products/{result_id}" if action.startswith("product_")
                           else f"/products/{parent_id}/variations/{result_id}")
        readback, _ = wc.api_get(verify_endpoint, vault=vault)
        after = selected_state(readback, plan["payload"])
        if after != plan["payload"]:
            raise ChangeError("Live readback did not match every approved field.")
    except Exception as exc:
        write_lock(lock, {
            "plan_sha256": plan["sha256"], "status": "indeterminate",
            "updated_utc": utc_now().isoformat(), "reason": wc.scrub(str(exc), vault),
        })
        wc.append_receipt(
            "woocommerce_change_indeterminate_no_retry",
            f"action={action}; plan={plan_path}; sha256={plan['sha256']}",
        )
        raise ChangeError(
            "The write result is indeterminate or failed verification. This plan is locked and will not retry. "
            "Reconcile the SKU/resource in WooCommerce before any new plan."
        ) from exc
    write_lock(lock, {
        "plan_sha256": plan["sha256"], "status": "committed_verified",
        "resource_id": result_id, "updated_utc": utc_now().isoformat(),
    })
    wc.append_receipt(
        "woocommerce_approved_catalog_change_committed",
        f"action={action}; id={result_id}; plan={plan_path}; sha256={plan['sha256']}",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED", "action": action,
        "resource_id": result_id, "approved_payload": after,
        "plan_sha256": plan["sha256"], "replay_locked": True,
    }, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--input", required=True)
    stage.set_defaults(func=command_stage)
    commit = commands.add_parser("commit")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (ChangeError, wc.WooError, OSError, ValueError) as exc:
        print("ERROR: " + wc.scrub(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
