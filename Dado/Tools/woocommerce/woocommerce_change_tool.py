#!/usr/bin/env python
"""FRP Depot WooCommerce Audit & Approved Catalog Change Tool.

Commissioned by Rachad Homsi on 2026-07-24.

Only four write routes exist: product/variation create or update. Creation is
forced to draft. No delete, batch, order, customer, payment, refund, coupon,
webhook, settings, plugin/theme, user, stock, sale-price, image-source, or
publication write is available.

AUTONOMY PROGRAMME, 2026-08-21 (Rachad: "Go on all of them"; spec A1/A2/A4).
Catalogue content is REVERSIBLE work, so this tool follows the shared owner
authority in Dado/Tools/common/owner_authority.py: his clear go in his own
message covers the change (APPROVED still works; "yes" / "go ahead" count);
the live values an update is about to change are captured beside the plan
BEFORE the PUT and `restore --plan` puts them back through the same update
route; a failed or unverified write reads "needs re-stage"; nothing is
permanently locked. A committed plan is spent. A CREATE has no restore route
(there is no delete); it lands as a draft and the receipt records its ID.
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

# Shared owner authority (2026-08-21). Appending keeps the stdlib ahead of it.
sys.path.append(str(Path(__file__).resolve().parent.parent / "common"))
import owner_authority  # noqa: E402

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
# this workflow is not approval of any catalog change. Since 2026-08-21 any
# clear go of his counts (spec A1); APPROVED is still the word the plan names.
APPROVAL_WORD = owner_authority.EXACT_WORD
PRODUCT_FIELDS = {
    "name", "type", "sku", "description", "short_description", "regular_price",
    "categories", "attributes", "images",
}
VARIATION_STOCK_PUBLICATION_FIELDS = {
    "status", "manage_stock", "stock_quantity", "stock_status", "backorders",
}
VARIATION_FIELDS = {
    "sku", "description", "regular_price", "attributes",
    *VARIATION_STOCK_PUBLICATION_FIELDS,
}
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


def clean_product_images(value: Any) -> list[dict[str, Any]]:
    """Accept only an ordered, complete list of existing image IDs and plain alt text."""
    if not isinstance(value, list) or not value or len(value) > 100:
        raise ChangeError("images must be a list of 1 to 100 existing image IDs and alt values.")
    output = []
    seen: set[int] = set()
    for index, row in enumerate(value):
        if not isinstance(row, dict) or set(row) != {"id", "alt"}:
            raise ChangeError(f"images[{index}] must contain exactly id and alt.")
        image_id = int(clean_id(row["id"], f"images[{index}].id"))
        if image_id in seen:
            raise ChangeError(f"images[{index}] duplicates an earlier image ID.")
        alt = clean_text(row["alt"], f"images[{index}].alt", required=True, maximum=250).strip()
        if "<" in alt or ">" in alt or any(char in alt for char in "\r\n\t"):
            raise ChangeError(f"images[{index}].alt must be single-line plain text.")
        seen.add(image_id)
        output.append({"id": image_id, "alt": alt})
    return output


def assert_image_gallery_exact(record: dict[str, Any], desired: list[dict[str, Any]],
                               *, require_alt_match: bool) -> None:
    """Prevent image creation, removal, replacement or reordering around an alt-only edit."""
    live = record.get("images")
    if not isinstance(live, list) or len(live) != len(desired):
        raise ChangeError("Image-alt plans must preserve the complete existing image gallery.")
    live_ids = []
    for index, row in enumerate(live):
        if not isinstance(row, dict):
            raise ChangeError("The live image gallery contains an invalid record.")
        live_ids.append(int(clean_id(row.get("id"), f"live images[{index}].id")))
    if live_ids != [row["id"] for row in desired]:
        raise ChangeError("Image-alt plans must preserve every image ID and the exact gallery order.")
    if require_alt_match:
        live_projection = [
            {"id": image_id, "alt": str(row.get("alt") or "").strip()}
            for image_id, row in zip(live_ids, live)
        ]
        if live_projection != desired:
            raise ChangeError("Live image IDs, order, or alt values did not match the approved gallery.")


def clean_product_attributes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 100:
        raise ChangeError("attributes must be a list of at most 100 global or named custom attributes.")
    output = []
    seen: set[tuple[str, Any]] = set()
    global_keys = {"id", "position", "visible", "variation", "options"}
    custom_keys = {"id", "name", "position", "visible", "variation", "options"}
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise ChangeError(f"attributes[{index}] must be an object.")
        keys = set(row)
        if keys != global_keys and keys != custom_keys:
            raise ChangeError(
                f"attributes[{index}] must use the exact global shape "
                "id, position, visible, variation, options or the exact custom shape "
                "id, name, position, visible, variation, options."
            )
        position = row["position"]
        if isinstance(position, bool) or not isinstance(position, int) or position < 0:
            raise ChangeError(f"attributes[{index}].position must be a non-negative integer.")
        if not isinstance(row["visible"], bool) or not isinstance(row["variation"], bool):
            raise ChangeError(f"attributes[{index}] visible and variation must be true or false.")
        options = row["options"]
        if not isinstance(options, list) or not options or len(options) > 100:
            raise ChangeError(f"attributes[{index}].options must have 1 to 100 values.")
        cleaned_options = [
            clean_text(item, f"attributes[{index}].options", required=True, maximum=200)
            for item in options
        ]
        if len({item.casefold() for item in cleaned_options}) != len(cleaned_options):
            raise ChangeError(f"attributes[{index}].options contains duplicate values.")

        if keys == global_keys:
            attribute_id = int(clean_id(row["id"], f"attributes[{index}].id"))
            identity = ("id", attribute_id)
            cleaned: dict[str, Any] = {"id": attribute_id}
        else:
            raw_id = row["id"]
            if isinstance(raw_id, bool) or str(raw_id).strip() != "0":
                raise ChangeError(
                    f"attributes[{index}] may include name only for a custom attribute with id 0."
                )
            name = clean_text(
                row["name"], f"attributes[{index}].name", required=True, maximum=200
            ).strip()
            identity = ("name", name.casefold())
            cleaned = {"id": 0, "name": name}

        if identity in seen:
            raise ChangeError(f"attributes[{index}] duplicates another attribute.")
        seen.add(identity)
        cleaned.update({
            "position": position,
            "visible": row["visible"],
            "variation": row["variation"],
            "options": cleaned_options,
        })
        output.append(cleaned)
    return output


def clean_variation_attributes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > 100:
        raise ChangeError(
            "attributes must be a list of 1 to 100 global or named custom attribute selections."
        )
    output = []
    seen: set[tuple[str, Any]] = set()
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise ChangeError(f"attributes[{index}] must be an object.")
        keys = set(row)
        option = clean_text(
            row.get("option"), f"attributes[{index}].option", required=True, maximum=200
        )
        if keys == {"id", "option"}:
            attribute_id = int(clean_id(row["id"], f"attributes[{index}].id"))
            identity = ("id", attribute_id)
            cleaned = {"id": attribute_id, "option": option}
        elif keys == {"id", "name", "option"}:
            raw_id = row["id"]
            if isinstance(raw_id, bool) or str(raw_id).strip() != "0":
                raise ChangeError(
                    f"attributes[{index}] may include name only for a custom attribute with id 0."
                )
            name = clean_text(
                row["name"], f"attributes[{index}].name", required=True, maximum=200
            ).strip()
            identity = ("name", name.casefold())
            cleaned = {"id": 0, "name": name, "option": option}
        else:
            raise ChangeError(
                f"attributes[{index}] must contain exactly id and option for a global attribute, "
                "or id, name, and option for a custom attribute."
            )
        if identity in seen:
            raise ChangeError(f"attributes[{index}] duplicates an earlier attribute selection.")
        seen.add(identity)
        output.append(cleaned)
    return output


def clean_changes(action: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        raise ChangeError("changes must be a non-empty object.")
    allowed = PRODUCT_FIELDS if action.startswith("product_") else VARIATION_FIELDS
    extra = sorted(set(raw) - allowed)
    if extra:
        raise ChangeError("Unsupported change field(s): " + ", ".join(extra))
    lifecycle_fields = set(raw) & VARIATION_STOCK_PUBLICATION_FIELDS
    if lifecycle_fields:
        if action != "variation_update":
            raise ChangeError(
                "Stock/publication fields are allowed only when updating an existing variation."
            )
        if lifecycle_fields != VARIATION_STOCK_PUBLICATION_FIELDS:
            raise ChangeError(
                "Variation publication requires the complete fixed bundle: status, manage_stock, "
                "stock_quantity, stock_status, and backorders."
            )
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
        elif field == "status":
            if value != "publish":
                raise ChangeError("Variation publication status must be exactly publish.")
            output[field] = "publish"
        elif field == "manage_stock":
            if value is not True:
                raise ChangeError("Variation publication manage_stock must be exactly true.")
            output[field] = True
        elif field == "stock_quantity":
            if type(value) is not int or value != 0:
                raise ChangeError("Variation publication stock_quantity must be integer zero.")
            output[field] = 0
        elif field == "stock_status":
            if value != "outofstock":
                raise ChangeError("Variation publication stock_status must be exactly outofstock.")
            output[field] = "outofstock"
        elif field == "backorders":
            if value != "no":
                raise ChangeError("Variation publication backorders must be exactly no.")
            output[field] = "no"
        elif field == "categories":
            output[field] = clean_categories(value)
        elif field == "attributes":
            output[field] = (clean_product_attributes(value) if action.startswith("product_")
                             else clean_variation_attributes(value))
        elif field == "images":
            if action != "product_update":
                raise ChangeError("images are allowed only for alt-only updates to existing products.")
            output[field] = clean_product_images(value)
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
        # Scalar option lists must preserve the full live response. Otherwise
        # removing trailing parent options can look like a no-op at staging and
        # a failed removal can look verified after commit.
        if template and all(not isinstance(child, (dict, list)) for child in template):
            return list(source)
        return [_project_response(source[index] if index < len(source) else None, child)
                for index, child in enumerate(template)]
    return value


def selected_state(record: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    return {field: _project_response(record.get(field), desired)
            for field, desired in sorted(template.items())}


BLOCK_TAG_WHITESPACE_RE = re.compile(
    r"(?i)(</?(?:table|thead|tbody|tr|th|td|p|ul|ol|li|div|h[1-6])\b[^>]*>)[ \t\r\n]+"
    r"(?=</?(?:table|thead|tbody|tr|th|td|p|ul|ol|li|div|h[1-6])\b[^>]*>)"
)


def normalize_block_tag_whitespace(value: str) -> str:
    """Ignore only WordPress-added whitespace/newlines between structural block HTML tags."""
    return BLOCK_TAG_WHITESPACE_RE.sub(r"\1", value).strip()


def description_readback_matches(live: str, approved: str) -> bool:
    """Accept only proven, content-preserving WordPress serialization changes."""
    normalized_live = normalize_block_tag_whitespace(live)
    normalized_approved = normalize_block_tag_whitespace(approved)
    if normalized_live == normalized_approved:
        return True
    # WooCommerce wraps a plain-text variation description in one paragraph.
    # Keep this deliberately narrow: the approved value must contain no markup,
    # and the live value must be that exact text inside exactly one <p> element.
    return (
        "<" not in normalized_approved
        and ">" not in normalized_approved
        and normalized_live.strip() == f"<p>{normalized_approved}</p>"
    )


def approved_readback_matches(after: dict[str, Any], payload: dict[str, Any]) -> bool:
    if set(after) != set(payload):
        return False
    for field, approved in payload.items():
        live = after.get(field)
        if field in {"description", "short_description"}:
            if not isinstance(live, str) or not isinstance(approved, str):
                return False
            if not description_readback_matches(live, approved):
                return False
        elif live != approved:
            return False
    return True


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


def require_rachad_approval(approval: Any, lane: Any = None,
                            what: str = "this WooCommerce change plan") -> owner_authority.OwnerGo:
    """His clear go in his own message (spec A1); workflow authorization is not
    change approval and Dado never supplies it."""
    try:
        return owner_authority.require_owner_go(approval, lane=lane, what=what)
    except owner_authority.OwnerAuthorityRefused as exc:
        raise ChangeError(str(exc)) from exc


def lock_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".commit-lock.json")


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError:
        # A4: what the existing record says decides -- spent, or needs re-stage.
        owner_authority.refuse_replay(ChangeError, owner_authority.read_json_if_exists(path),
                                      what="WooCommerce change plan")
        raise  # unreachable
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
        if "images" in payload:
            assert_image_gallery_exact(record, payload["images"], require_alt_match=False)
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
    # Re-run closed payload/source validation. Only create actions inject Draft
    # status automatically; an update's status is an explicitly approved field.
    raw_payload = dict(plan.get("payload") or {})
    caller_changes = (
        {k: v for k, v in raw_payload.items() if k != "status"}
        if action.endswith("create") else raw_payload
    )
    validated_changes = clean_changes(action, caller_changes)
    expected_payload = payload_for(action, validated_changes)
    if expected_payload != plan.get("payload"):
        raise ChangeError("Plan payload failed the current allowlist validation.")
    raw_sources = dict(plan.get("sources") or {})
    caller_sources = (
        {k: v for k, v in raw_sources.items() if k != "status"}
        if action.endswith("create") else raw_sources
    )
    if verify_sources(validated_changes, caller_sources, action) != raw_sources:
        raise ChangeError("Plan field sources failed validation.")
    plan["sha256"] = saved
    return plan


def command_commit(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve()
    if PLAN_DIR.resolve() not in plan_path.parents:
        raise ChangeError("Plan must be inside Dado's WooCommerce plan folder.")
    plan = load_plan(str(plan_path))
    go = require_rachad_approval(args.approval, getattr(args, "approval_lane", None))
    if lock_path(plan_path).exists():
        owner_authority.refuse_replay(ChangeError, owner_authority.read_json_if_exists(lock_path(plan_path)),
                                      what="WooCommerce change plan")
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
        if "images" in plan["payload"]:
            assert_image_gallery_exact(current, plan["payload"]["images"], require_alt_match=False)
    assert_sku_unique(action, str(plan["payload"].get("sku") or ""), resource_id, parent_id, vault)
    method, endpoint = endpoint_for(action, resource_id, parent_id)
    validate_write_route(method, endpoint)
    backup: Path | None = None
    if existing:
        # A2: keep the live values this update changes beside the plan, BEFORE the PUT.
        backup = owner_authority.capture_live_state(
            plan_path,
            {"action": action, "method": method, "endpoint": endpoint, "resource_id": resource_id,
             "parent_id": parent_id, "before": selected_state(current, plan["payload"]),
             "after": plan["payload"], "before_date_modified_gmt": str(current.get("date_modified_gmt") or "")},
            what=f"WooCommerce {action} fields {sorted(plan['payload'])}", where=f"{EXACT_ORIGIN} {endpoint}",
            plan_sha256=plan["sha256"],
        )
    lock = lock_path(plan_path)
    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_IN_FLIGHT, plan_sha256=plan["sha256"], action=action, go=go,
        started_utc=utc_now().isoformat(),
    ), exclusive=True)
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
        if not approved_readback_matches(after, plan["payload"]):
            raise ChangeError("Live readback did not match every approved field.")
        if "images" in plan["payload"]:
            assert_image_gallery_exact(readback, plan["payload"]["images"], require_alt_match=True)
    except Exception as exc:
        reason = wc.scrub(str(exc), vault)
        write_lock(lock, owner_authority.attempt_record(
            owner_authority.STATUS_INDETERMINATE, plan_sha256=plan["sha256"], action=action, go=go,
            reason=reason, backup=str(backup) if backup else None,
        ))
        wc.append_receipt(
            "woocommerce_change_indeterminate_needs_restage",
            f"action={action}; backup={backup or 'none'}; plan={plan_path}; sha256={plan['sha256']}",
        )
        raise ChangeError(
            owner_authority.explain_outcome(
                f"WooCommerce {action}", owner_authority.STATUS_INDETERMINATE,
                "The write result is indeterminate or failed verification: " + reason,
            )
        ) from exc
    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_COMMITTED, plan_sha256=plan["sha256"], action=action, go=go,
        resource_id=result_id, backup=str(backup) if backup else None,
    ))
    wc.append_receipt(
        "woocommerce_approved_catalog_change_committed",
        f"action={action}; id={result_id}; backup={backup or 'none'}; plan={plan_path}; "
        f"sha256={plan['sha256']}; go_lane={go.lane}",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED", "action": action,
        "resource_id": result_id, "approved_payload": after,
        "plan_sha256": plan["sha256"], "replay_locked": True, "plan_spent": True,
        "live_state_backup": str(backup) if backup else None,
        "restore": (f"restore --plan {plan_path} --approval <his go>" if backup
                    else "not available for a create (no delete route); the draft stays and this receipt records its ID"),
    }, indent=2, ensure_ascii=False))


def load_plan_for_restore(path: str) -> dict[str, Any]:
    """The plan behind a restore: hash, schema and origin are checked; expiry is
    not, because putting the previous values back is allowed after the 24-hour
    plan window."""
    plan = read_json(path)
    saved = str(plan.pop("sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(plan)):
        raise ChangeError("Plan hash check failed. The plan changed after review.")
    if (plan.get("schema_version") != SCHEMA_VERSION or plan.get("tool") != TOOL_NAME
            or plan.get("action") not in ACTIONS or plan.get("origin") != EXACT_ORIGIN):
        raise ChangeError("The plan schema, tool, action, or origin is invalid.")
    plan["sha256"] = saved
    return plan


def command_restore(args: argparse.Namespace) -> None:
    """A2: put back the values captured before this plan's PUT (one PUT, same route)."""
    plan_path = Path(args.plan).resolve()
    if PLAN_DIR.resolve() not in plan_path.parents:
        raise ChangeError("Plan must be inside Dado's WooCommerce plan folder.")
    plan = load_plan_for_restore(str(plan_path))
    go = require_rachad_approval(args.approval, getattr(args, "approval_lane", None),
                                 what="restoring the values this plan changed")
    record = owner_authority.load_live_state(plan_path, ChangeError)
    if record.get("plan_sha256") != plan["sha256"]:
        raise ChangeError("The captured live state belongs to a different plan.")
    restore_path = owner_authority.restore_record_path(plan_path)
    if restore_path.exists():
        raise ChangeError(
            f"This plan's values were already restored ({restore_path.name}); stage a new plan "
            "for any further change."
        )
    state = record["state"]
    action = str(state["action"])
    resource_id = state["resource_id"]
    parent_id = state["parent_id"]
    method, endpoint = endpoint_for(action, resource_id, parent_id)
    validate_write_route(method, endpoint)
    if method != "PUT" or endpoint != state["endpoint"]:
        raise ChangeError("The captured route is not the plan's update route; refusing to restore.")
    vault = wc.load_vault()
    if vault.get("declared_permissions") != "read_write":
        raise ChangeError("Saved WooCommerce key is not declared Read/Write.")
    if wc.normalize_site_url(str(vault.get("site_url") or "")) != wc.ALLOWED_ORIGIN:
        raise ChangeError("Saved WooCommerce origin is not the exact FRP Depot origin.")
    before = state["before"]
    after = state["after"]
    current, _ = wc.api_get(endpoint, vault=vault)
    if not isinstance(current, dict) or int(current.get("id") or 0) != int(resource_id):
        raise ChangeError("Current resource verification returned an unexpected response.")
    live = selected_state(current, after)
    result: dict[str, Any] = {"plan_sha256": plan["sha256"], "action": action, "resource_id": resource_id,
                              **go.as_record()}
    if approved_readback_matches(live, before):
        result.update(status="NOT_RESTORED", reason="already at the captured values")
    elif not approved_readback_matches(live, after):
        result.update(status="NOT_RESTORED",
                      reason="moved since this plan wrote it (live values differ); left alone")
    else:
        try:
            written, _ = wc.api_request(method, endpoint, payload=before, vault=vault)
            if not isinstance(written, dict) or int(written.get("id") or 0) != int(resource_id):
                raise ChangeError("WooCommerce returned an unexpected resource after the restore PUT.")
            readback, _ = wc.api_get(endpoint, vault=vault)
            if not approved_readback_matches(selected_state(readback, before), before):
                raise ChangeError("Live readback after the restore did not match the captured values.")
        except Exception as exc:
            result.update(status="RESTORE_FAILED", reason=wc.scrub(str(exc), vault)[:1000])
            owner_authority.record_restore(plan_path, result)
            raise ChangeError(f"Restore of {action} {resource_id} did not verify: {wc.scrub(str(exc), vault)}") from exc
        result.update(status="RESTORED", **{"from": after, "to": before})
    owner_authority.record_restore(plan_path, result)
    wc.append_receipt(
        "woocommerce_catalog_change_restore",
        f"status={result['status']}; action={action}; id={resource_id}; "
        f"backup={owner_authority.live_state_path(plan_path)}; plan={plan_path}; sha256={plan['sha256']}",
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


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
    restore = commands.add_parser("restore", help="put back the values captured before this plan's PUT")
    restore.add_argument("--plan", required=True)
    owner_authority.add_owner_go_arguments(restore)
    restore.set_defaults(func=command_restore)
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
