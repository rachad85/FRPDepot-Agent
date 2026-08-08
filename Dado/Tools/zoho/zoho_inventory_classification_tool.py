#!/usr/bin/env python
"""FRP Depot Zoho Inventory catalog-classification tool.

Commissioned by Rachad Homsi on 2026-08-07. Building or staging with this
module is not approval of a business write.

The only service writes reachable through this named tool are:
* one browser-session POST to ``/api/v1/settings/fields`` creating the fixed
  item dropdown custom field ``Catalog Classification``; and
* one to twenty OAuth PUTs to ``/inventory/v1/items/{positive item_id}``, each
  containing exactly the staged item name and the complete custom-field value
  serializer needed to alter/add only that classification field.

There is no generic write function. Deletes and changes to names, SKUs, prices,
stock, accounts, taxes, groups, status, images, documents, or any other field
are not commissioned and are rejected by the transports and read-back checks.
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
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

import zoho_tool

TOOL_NAME = "FRP Depot Zoho Inventory Catalog Classification Tool"
SCHEMA_VERSION = 1
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_classification_plans"
PLAN_LIFETIME_HOURS = 24
APPROVAL_WORD = "APPROVED"
UPDATE_SCOPE = "ZohoInventory.items.UPDATE"
CDP_ENDPOINT = "http://127.0.0.1:9228"
UI_HOST = "inventory.zohocloud.ca"
FIELD_PATH = "/api/v1/settings/fields"
FIELD_SETTINGS_URL = (
    f"https://{UI_HOST}/app#/settings/preferences/item/customfields"
)
ITEM_PATH_RE = re.compile(r"^/inventory/v1/items/([1-9][0-9]*)$")
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
FIELD_LABEL = "Catalog Classification"
FIELD_API_NAME = "cf_catalog_classification"
CLASSIFICATIONS = (
    "Website Catalog",
    "Custom / Customer-Specific",
    "Review / Unclassified",
)
UI_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=UTF-8"

# Key insertion order is intentional. This is the exact JSONString intercepted
# from Zoho Inventory's own field-creation UI and aborted before network.
FIXED_FIELD_DEFINITION: dict[str, Any] = {
    "is_mandatory": False,
    "is_basecurrency_amount": True,
    "data_type": "dropdown",
    "pii_type": "non_pii",
    "default_value": "",
    "entity": "item",
    "values": [
        {"name": "Website Catalog", "order": 1, "is_active": True},
        {"name": "Custom / Customer-Specific", "order": 2, "is_active": True},
        {"name": "Review / Unclassified", "order": 3, "is_active": True},
    ],
    "is_unique": False,
    "label": FIELD_LABEL,
    "selected_txn_entities": [],
    "external_fields": [None],
    "field_preferences": {"is_color_code_supported": False},
    "show_on_pdf": False,
}

CREATE_INPUT_FIELDS = {"source"}
ASSIGN_INPUT_FIELDS = {"classification", "item_ids", "sources"}
PLAN_FIELDS = {
    "schema_version", "tool", "action", "created_utc", "expires_utc",
    "nonce", "approval_required", "payload", "sources", "live_evidence",
    "sha256",
}
FIELD_EVIDENCE_FIELDS = {
    "customfield_id", "label", "data_type", "is_active", "values",
}
OPTION_EVIDENCE_FIELDS = {"name", "order", "is_active"}
ITEM_EVIDENCE_FIELDS = {
    "item_id", "name", "target_present", "current_target_value",
    "current_state", "current_state_sha256", "current_custom_fields",
    "assignment_custom_fields", "protected_state", "protected_state_sha256",
}
# Zoho rebuilds custom_field_hash from custom_fields after a custom-field PUT.
# It includes the intended target value under API-name-derived keys, so comparing
# the raw derived hash creates a false positive.  The authoritative custom_fields
# rows are handled separately below: the target is verified exactly and every
# non-target custom-field id/value remains protected.
ITEM_READBACK_EXCLUSIONS = {
    "custom_fields", "custom_field_hash", "last_modified_time",
}


class ClassificationToolError(RuntimeError):
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
        raise ClassificationToolError(
            "Zoho returned evidence that is not JSON serializable."
        ) from exc


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClassificationToolError(f"{label} JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ClassificationToolError(f"{label} JSON must contain exactly one object.")
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
    raise ClassificationToolError(
        f"{label} must use the exact closed schema ({'; '.join(details)})."
    )


def clean_text(value: Any, label: str, maximum: int = 2000) -> str:
    if not isinstance(value, str):
        raise ClassificationToolError(f"{label} must be text.")
    result = value.strip()
    if not result:
        raise ClassificationToolError(f"{label} cannot be blank.")
    if len(result) > maximum:
        raise ClassificationToolError(
            f"{label} exceeds the {maximum}-character safety limit."
        )
    if any(ord(character) < 32 for character in result):
        raise ClassificationToolError(f"{label} contains control characters.")
    return result


def positive_id(value: Any, label: str) -> str:
    if isinstance(value, bool):
        raise ClassificationToolError(f"{label} must be a positive Zoho ID.")
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise ClassificationToolError(f"{label} must be a positive Zoho ID.")
    return text


def vault_organization_id(vault: dict[str, Any]) -> str:
    return positive_id(vault.get("inventory_organization_id"), "inventory_organization_id")


def validate_create_input(raw: dict[str, Any]) -> dict[str, Any]:
    closed_fields(raw, CREATE_INPUT_FIELDS, "Create input")
    source = clean_text(raw["source"], "source")
    if source != raw["source"]:
        raise ClassificationToolError("source must not have surrounding whitespace.")
    return {"source": source}


def validate_assign_input(raw: dict[str, Any]) -> dict[str, Any]:
    closed_fields(raw, ASSIGN_INPUT_FIELDS, "Assignment input")
    classification = raw["classification"]
    if classification not in CLASSIFICATIONS:
        raise ClassificationToolError(
            "classification must be exactly one of: " + ", ".join(CLASSIFICATIONS)
        )
    item_values = raw["item_ids"]
    if not isinstance(item_values, list):
        raise ClassificationToolError(
            "item_ids must be a list of 1-20 unique positive Zoho IDs."
        )
    if not 1 <= len(item_values) <= 20:
        raise ClassificationToolError("item_ids must contain 1-20 items.")
    item_ids = [
        positive_id(value, f"item_ids[{index}]")
        for index, value in enumerate(item_values)
    ]
    if len(set(item_ids)) != len(item_ids):
        raise ClassificationToolError("item_ids must be unique.")
    sources = raw["sources"]
    if not isinstance(sources, dict) or set(sources) != {"classification"}:
        raise ClassificationToolError("sources must contain exactly classification.")
    source = clean_text(sources["classification"], "sources.classification")
    if source != sources["classification"]:
        raise ClassificationToolError(
            "sources.classification must not have surrounding whitespace."
        )
    return {
        "classification": classification,
        "item_ids": item_ids,
        "sources": {"classification": source},
    }


def require_rachad_approval(approval: Any) -> None:
    # Surrounding whitespace and case do not matter; a second word never does.
    if (
        not isinstance(approval, str)
        or approval.strip().casefold() != APPROVAL_WORD.casefold()
    ):
        raise ClassificationToolError(
            "Rachad must answer this exact staged plan with the one-word approval: "
            "APPROVED. Building or staging is not approval, and Dado cannot supply it."
        )


def contained_plan(raw_path: Any) -> Path:
    candidate = Path(str(raw_path if raw_path is not None else ""))
    if not candidate.is_absolute():
        raise ClassificationToolError(
            "Plan must be an absolute path inside the exact classification plan folder."
        )
    try:
        lexical_root = PLAN_DIR.absolute()
        lexical_candidate = candidate.absolute()
        lexical_candidate.relative_to(lexical_root)
    except (OSError, ValueError) as exc:
        raise ClassificationToolError(
            "Plan is outside the exact allowlisted classification plan folder."
        ) from exc
    cursor = lexical_candidate
    while True:
        if cursor.is_symlink():
            raise ClassificationToolError("Plan paths and parents must not be symlinks.")
        if cursor == lexical_root:
            break
        parent = cursor.parent
        if parent == cursor:
            raise ClassificationToolError(
                "Plan is outside the exact allowlisted classification plan folder."
            )
        cursor = parent
    try:
        root = PLAN_DIR.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ClassificationToolError(
            "Plan does not resolve inside the exact classification plan folder."
        ) from exc
    if (
        root not in resolved.parents
        or not resolved.is_file()
        or resolved.suffix.casefold() != ".json"
    ):
        raise ClassificationToolError(
            "Plan is outside the exact allowlisted classification plan folder."
        )
    return resolved


def parse_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ClassificationToolError(f"Plan {label} is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ClassificationToolError(f"Plan {label} must include a timezone.")
    return parsed


def _field_rows(response: dict[str, Any]) -> list[Any]:
    for key in ("fields", "custom_fields"):
        rows = response.get(key)
        if isinstance(rows, list):
            return rows
    data = response.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("fields", "custom_fields"):
            rows = data.get(key)
            if isinstance(rows, list):
                return rows
    raise ClassificationToolError("Zoho field GET returned no fields list.")


def _field_id(row: dict[str, Any]) -> str:
    present = [
        key for key in ("customfield_id", "custom_field_id", "field_id")
        if row.get(key) not in (None, "")
    ]
    if len(present) != 1:
        raise ClassificationToolError(
            "Catalog Classification field metadata has no unique custom-field ID."
        )
    return positive_id(row[present[0]], "Catalog Classification customfield_id")


def _is_target_field_row(row: dict[str, Any]) -> bool:
    """Recognize built-in-style and Zoho custom-field metadata fail-closed."""
    label = row.get("label")
    if isinstance(label, str) and label.strip().casefold() == FIELD_LABEL.casefold():
        return True
    if label not in (None, ""):
        return False
    formatted = row.get("field_name_formatted")
    return (
        isinstance(formatted, str)
        and formatted.strip().casefold() == FIELD_LABEL.casefold()
        and row.get("is_custom_field") is True
        and row.get("field_name") == FIELD_API_NAME
        and row.get("api_name") == FIELD_API_NAME
    )


def project_field_metadata(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ClassificationToolError("Zoho returned a non-object field row.")
    if not _is_target_field_row(row):
        raise ClassificationToolError("Catalog Classification field label is invalid.")
    label = row.get("label")
    projected_label = (
        label if isinstance(label, str) else row.get("field_name_formatted")
    )
    if not isinstance(projected_label, str):
        raise ClassificationToolError("Catalog Classification field label is invalid.")
    values = row.get("values")
    if not isinstance(values, list):
        raise ClassificationToolError("Catalog Classification field values are invalid.")
    projected_values: list[dict[str, Any]] = []
    for option in values:
        if not isinstance(option, dict):
            raise ClassificationToolError(
                "Catalog Classification contains a non-object dropdown option."
            )
        projected_values.append({
            "name": option.get("name"),
            "order": option.get("order"),
            "is_active": option.get("is_active"),
        })
    return {
        "customfield_id": _field_id(row),
        "label": projected_label,
        "data_type": row.get("data_type"),
        "is_active": row.get("is_active"),
        "values": projected_values,
    }


def target_field_candidates(response: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in _field_rows(response):
        if not isinstance(row, dict):
            continue
        if _is_target_field_row(row):
            candidates.append(project_field_metadata(row))
    return candidates


def validate_exact_target_field(field: dict[str, Any]) -> dict[str, Any]:
    closed_fields(field, FIELD_EVIDENCE_FIELDS, "Target field evidence")
    customfield_id = positive_id(field["customfield_id"], "target customfield_id")
    expected_values = [
        {"name": name, "order": index, "is_active": True}
        for index, name in enumerate(CLASSIFICATIONS, start=1)
    ]
    if (
        field["customfield_id"] != customfield_id
        or field["label"] != FIELD_LABEL
        or field["data_type"] != "dropdown"
        or field["is_active"] is not True
        or field["values"] != expected_values
    ):
        raise ClassificationToolError(
            "Catalog Classification must be one active dropdown with the exact approved label, options, and order."
        )
    for option in field["values"]:
        if not isinstance(option, dict):
            raise ClassificationToolError("Target dropdown option evidence is invalid.")
        closed_fields(option, OPTION_EVIDENCE_FIELDS, "Target dropdown option evidence")
    return json_copy(field)


def require_exact_target_field(response: dict[str, Any]) -> dict[str, Any]:
    candidates = target_field_candidates(response)
    if len(candidates) != 1:
        raise ClassificationToolError(
            "Zoho must contain exactly one Catalog Classification item custom field."
        )
    return validate_exact_target_field(candidates[0])


def require_target_absent(response: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = target_field_candidates(response)
    if candidates:
        # Validate it first so a wrong-shaped collision has an explicit refusal.
        if len(candidates) == 1:
            validate_exact_target_field(candidates[0])
        raise ClassificationToolError(
            "Catalog Classification already exists; no field creation was staged or committed."
        )
    return []


def _decode_ui_result(raw: Any, *, write: bool) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"status", "ok", "text"}:
        raise ClassificationToolError(
            "Zoho UI transport returned an unknown response; write outcome is indeterminate."
            if write else "Zoho UI field GET returned an unknown response."
        )
    status = raw["status"]
    if isinstance(status, bool) or not isinstance(status, int):
        raise ClassificationToolError("Zoho UI transport returned an invalid HTTP status.")
    if raw["ok"] is not True or not 200 <= status < 300:
        raise ClassificationToolError(
            f"Zoho UI {'field create' if write else 'field GET'} failed with HTTP {status}."
        )
    if not isinstance(raw["text"], str):
        raise ClassificationToolError("Zoho UI transport returned a non-text response.")
    try:
        payload = json.loads(raw["text"])
    except json.JSONDecodeError as exc:
        raise ClassificationToolError(
            "Zoho UI field create returned invalid JSON; write outcome is indeterminate."
            if write else "Zoho UI field GET returned invalid JSON."
        ) from exc
    if not isinstance(payload, dict):
        raise ClassificationToolError(
            "Zoho UI field response was not one object."
        )
    if write:
        if payload.get("code") != 0:
            raise ClassificationToolError(
                "Zoho UI field create returned an invalid or unknown result; write outcome is indeterminate."
            )
    elif payload.get("code") not in (None, 0):
        raise ClassificationToolError(
            "Zoho UI field GET failed: " + str(payload.get("message") or payload.get("code"))
        )
    return payload


def _execute_ui_request(url: str) -> dict[str, Any]:
    """Execute one same-origin GET in an authenticated local Inventory page.

    Only status, success, and response text cross the CDP boundary. Cookies,
    storage, request headers, and credentials are never read or returned.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ClassificationToolError(
            "No authenticated live Zoho Inventory page is available. Run CONNECT_DADO_ZOHO_UI.bat."
        ) from exc
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=10_000)
            pages = [page for context in browser.contexts for page in context.pages]
            page = next((
                candidate for candidate in pages
                if (
                    urlsplit(candidate.url).scheme == "https"
                    and urlsplit(candidate.url).hostname == UI_HOST
                    and (
                        urlsplit(candidate.url).path == "/app"
                        or urlsplit(candidate.url).path.startswith("/app/")
                    )
                )
            ), None)
            if page is None:
                raise ClassificationToolError(
                    "No authenticated live Zoho Inventory page is available. "
                    "Run CONNECT_DADO_ZOHO_UI.bat."
                )
            return page.evaluate(
                """async (url) => {
                    const headers = {"Accept": "application/json"};
                    const options = {method: "GET", headers, credentials: "include"};
                    const response = await fetch(url, options);
                    const text = await response.text();
                    return {status: response.status, ok: response.ok, text};
                }""",
                url,
            )
    except ClassificationToolError:
        raise
    except Exception as exc:
        raise ClassificationToolError(
            "No authenticated live Zoho Inventory page is available. "
            "Run CONNECT_DADO_ZOHO_UI.bat."
        ) from exc


def _validate_native_field_post(
    url: str,
    method: str,
    post_data: str | None,
    organization_id: str,
    expected_body: str,
) -> None:
    """Fail closed unless a browser-generated request is the one fixed field POST.

    This intentionally does not read request-header values. Zoho's own browser
    code supplies its role/source/CSRF headers; the tool only validates the
    public destination and the non-secret fixed form payload.
    """
    org_id = positive_id(organization_id, "organization_id")
    parsed = urlsplit(str(url or ""))
    if (
        method != "POST"
        or parsed.scheme != "https"
        or parsed.hostname != UI_HOST
        or parsed.path != FIELD_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise ClassificationToolError(
            "REFUSED: native field Save attempted an unsupported request."
        )
    if not isinstance(post_data, str):
        raise ClassificationToolError(
            "REFUSED: native field Save did not produce a form body."
        )
    try:
        actual = parse_qs(post_data, strict_parsing=True, keep_blank_values=True)
        expected = parse_qs(expected_body, strict_parsing=True, keep_blank_values=True)
    except ValueError as exc:
        raise ClassificationToolError(
            "REFUSED: native field Save produced an invalid form body."
        ) from exc
    if actual != expected or set(actual) != {"JSONString", "organization_id"}:
        raise ClassificationToolError(
            "REFUSED: native field Save payload differs from the approved fixed payload."
        )
    if actual["organization_id"] != [org_id] or len(actual["JSONString"]) != 1:
        raise ClassificationToolError(
            "REFUSED: native field Save organization or JSONString is invalid."
        )
    try:
        decoded = json.loads(actual["JSONString"][0])
    except json.JSONDecodeError as exc:
        raise ClassificationToolError(
            "REFUSED: native field Save JSONString is invalid JSON."
        ) from exc
    if decoded != FIXED_FIELD_DEFINITION:
        raise ClassificationToolError(
            "REFUSED: native field Save JSONString differs from the fixed definition."
        )


def _validate_dropdown_slots(values: list[str], *, filled: bool) -> None:
    """Accept Zoho's extra blank row without widening the three fixed values."""
    if len(values) < len(CLASSIFICATIONS) or any(
        not isinstance(value, str) for value in values
    ):
        raise ClassificationToolError(
            "Zoho dropdown option inputs changed; no field Save was attempted."
        )
    if not filled:
        if any(value != "" for value in values):
            raise ClassificationToolError(
                "Zoho dropdown option inputs were not blank; no field Save was attempted."
            )
        return
    if tuple(values[:len(CLASSIFICATIONS)]) != CLASSIFICATIONS or any(
        value != "" for value in values[len(CLASSIFICATIONS):]
    ):
        raise ClassificationToolError(
            "Zoho dropdown option values differ from the fixed definition; "
            "no field Save was attempted."
        )


def _execute_native_field_create(
    organization_id: str,
    expected_body: str,
) -> dict[str, Any]:
    """Submit the fixed field through Zoho's native UI Save path.

    Exactly one validated field-create POST may leave the browser. Every other
    non-read request is aborted. Header values, cookies, and credentials never
    cross the CDP boundary or enter this function.
    """
    org_id = positive_id(organization_id, "organization_id")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ClassificationToolError(
            "No authenticated live Zoho Inventory page is available. Run CONNECT_DADO_ZOHO_UI.bat."
        ) from exc
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=10_000)
            pages = [page for context in browser.contexts for page in context.pages]
            page = next((
                candidate for candidate in pages
                if (
                    urlsplit(candidate.url).scheme == "https"
                    and urlsplit(candidate.url).hostname == UI_HOST
                    and (
                        urlsplit(candidate.url).path == "/app"
                        or urlsplit(candidate.url).path.startswith("/app/")
                    )
                )
            ), None)
            if page is None:
                raise ClassificationToolError(
                    "No authenticated live Zoho Inventory page is available. "
                    "Run CONNECT_DADO_ZOHO_UI.bat."
                )

            page.goto(FIELD_SETTINGS_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(1_500)
            cancel = page.get_by_role("button", name="Cancel", exact=True)
            if cancel.count() and cancel.first.is_visible():
                cancel.first.click(timeout=10_000)
                page.wait_for_timeout(500)
            page.get_by_role("button", name="New Field", exact=True).click(timeout=10_000)
            label_input = page.locator(
                'input[data-auto-gen-binding-key="model.label"]:visible'
            )
            label_input.wait_for(state="visible", timeout=10_000)
            if label_input.count() != 1:
                raise ClassificationToolError(
                    "Zoho field-label input was not found uniquely."
                )
            label_input.fill(FIELD_LABEL)

            combos = page.locator('[role="combobox"]:visible')
            if combos.count() < 2:
                raise ClassificationToolError("Zoho Data Type selector was not found.")
            combos.last.click(timeout=10_000)
            page.get_by_role("option", name="Dropdown", exact=True).click(timeout=10_000)
            option_inputs = page.locator(
                'input[data-auto-gen-binding-key="name"]:visible'
            )
            option_inputs.first.wait_for(state="visible", timeout=10_000)
            option_count = option_inputs.count()
            _validate_dropdown_slots(
                [option_inputs.nth(index).input_value() for index in range(option_count)],
                filled=False,
            )
            for index, value in enumerate(CLASSIFICATIONS):
                option_inputs.nth(index).fill(value)
            option_count = option_inputs.count()
            _validate_dropdown_slots(
                [option_inputs.nth(index).input_value() for index in range(option_count)],
                filled=True,
            )

            allowed_count = 0
            refused_target_requests: list[str] = []

            def intercept(route: Any, request: Any) -> None:
                nonlocal allowed_count
                if request.method in {"GET", "HEAD"}:
                    route.continue_()
                    return
                parsed_request = urlsplit(request.url)
                is_target = (
                    parsed_request.hostname == UI_HOST
                    and parsed_request.path == FIELD_PATH
                )
                if is_target and allowed_count == 0:
                    try:
                        _validate_native_field_post(
                            request.url,
                            request.method,
                            request.post_data,
                            org_id,
                            expected_body,
                        )
                    except ClassificationToolError as exc:
                        refused_target_requests.append(str(exc))
                        route.abort("blockedbyclient")
                        return
                    allowed_count = 1
                    route.continue_()
                    return
                if is_target:
                    refused_target_requests.append(
                        "REFUSED: more than one native field-create POST was attempted."
                    )
                route.abort("blockedbyclient")

            page.route("**/*", intercept)
            try:
                with page.expect_response(
                    lambda response: (
                        response.request.method == "POST"
                        and urlsplit(response.url).hostname == UI_HOST
                        and urlsplit(response.url).path == FIELD_PATH
                    ),
                    timeout=30_000,
                ) as pending_response:
                    page.get_by_role("button", name="Save", exact=True).click(
                        timeout=10_000
                    )
                response = pending_response.value
                raw = {
                    "status": response.status,
                    "ok": response.ok,
                    "text": response.text(),
                }
            except Exception as exc:
                if refused_target_requests:
                    raise ClassificationToolError(refused_target_requests[0]) from exc
                raise ClassificationToolError(
                    "Zoho native field Save did not return a response; write outcome is indeterminate."
                ) from exc
            finally:
                page.unroute("**/*", intercept)
            if refused_target_requests or allowed_count != 1:
                raise ClassificationToolError(
                    refused_target_requests[0]
                    if refused_target_requests
                    else "Zoho native field Save did not issue exactly one approved POST."
                )
            return raw
    except ClassificationToolError:
        raise
    except Exception as exc:
        raise ClassificationToolError(
            "No authenticated live Zoho Inventory page is available. "
            "Run CONNECT_DADO_ZOHO_UI.bat."
        ) from exc


def ui_transport_allowed(
    method: str,
    path: str,
    organization_id: str,
    *,
    content_type: str | None = None,
    outer_fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The complete UI field transport with exact verb/path/body allowlists."""
    org_id = positive_id(organization_id, "organization_id")
    if method == "GET":
        if path != FIELD_PATH or content_type is not None or outer_fields is not None:
            raise ClassificationToolError(
                "REFUSED: field metadata read is only the exact commissioned GET."
            )
        url = FIELD_PATH + "?" + urlencode({"entity": "item", "organization_id": org_id})
        raw = _execute_ui_request(url)
        payload = _decode_ui_result(raw, write=False)
        _field_rows(payload)
        return payload
    if method != "POST":
        raise ClassificationToolError(
            "REFUSED: only exact field GET and commissioned field-create POST are allowed."
        )
    if path != FIELD_PATH or content_type != UI_CONTENT_TYPE:
        raise ClassificationToolError(
            "REFUSED: field create requires the exact POST path and content type."
        )
    if not isinstance(outer_fields, dict) or set(outer_fields) != {
        "JSONString", "organization_id"
    }:
        raise ClassificationToolError(
            "REFUSED: field create form body must contain exactly JSONString and organization_id."
        )
    if outer_fields["organization_id"] != org_id:
        raise ClassificationToolError(
            "REFUSED: field create organization_id must equal the vault Inventory organization."
        )
    json_string = outer_fields["JSONString"]
    if not isinstance(json_string, str):
        raise ClassificationToolError("REFUSED: field create JSONString must be text.")
    try:
        decoded = json.loads(json_string)
    except json.JSONDecodeError as exc:
        raise ClassificationToolError("REFUSED: field create JSONString is invalid JSON.") from exc
    if decoded != FIXED_FIELD_DEFINITION:
        raise ClassificationToolError(
            "REFUSED: field create JSONString differs from the intercepted fixed definition."
        )
    body = urlencode([
        ("JSONString", json_string),
        ("organization_id", org_id),
    ])
    decoded_form = parse_qs(body, strict_parsing=True, keep_blank_values=True)
    if set(decoded_form) != {"JSONString", "organization_id"} or any(
        len(values) != 1 for values in decoded_form.values()
    ):
        raise ClassificationToolError("REFUSED: encoded field-create form changed shape.")
    if (
        decoded_form["organization_id"][0] != org_id
        or json.loads(decoded_form["JSONString"][0]) != FIXED_FIELD_DEFINITION
    ):
        raise ClassificationToolError("REFUSED: encoded field-create form failed equality checks.")
    raw = _execute_native_field_create(org_id, body)
    return _decode_ui_result(raw, write=True)


def ui_list_fields(organization_id: str) -> dict[str, Any]:
    return ui_transport_allowed("GET", FIELD_PATH, organization_id)


def ui_create_fixed_field(organization_id: str) -> dict[str, Any]:
    json_string = json.dumps(
        FIXED_FIELD_DEFINITION, ensure_ascii=False, separators=(",", ":")
    )
    return ui_transport_allowed(
        "POST",
        FIELD_PATH,
        organization_id,
        content_type=UI_CONTENT_TYPE,
        outer_fields={"JSONString": json_string, "organization_id": organization_id},
    )


def get_item(access_token: str, vault: dict[str, Any], item_id: str) -> dict[str, Any]:
    item_id = positive_id(item_id, "item_id")
    query = urlencode({"organization_id": vault_organization_id(vault)})
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/inventory/v1/items/{item_id}?{query}",
    )
    item = result.get("item")
    if not isinstance(item, dict) or str(item.get("item_id") or "") != item_id:
        raise ClassificationToolError(f"Zoho item {item_id} was not found.")
    return json_copy(item)


def project_custom_fields(item: dict[str, Any]) -> list[dict[str, Any]]:
    raw = item.get("custom_fields", [])
    if not isinstance(raw, list):
        raise ClassificationToolError("Zoho item custom_fields must be a list.")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(raw):
        if not isinstance(row, dict) or "customfield_id" not in row or "value" not in row:
            raise ClassificationToolError(
                f"Zoho item custom_fields[{index}] is malformed."
            )
        customfield_id = positive_id(
            row["customfield_id"], f"custom_fields[{index}].customfield_id"
        )
        if customfield_id in seen:
            raise ClassificationToolError("Zoho item contains duplicate customfield_id values.")
        seen.add(customfield_id)
        result.append({
            "customfield_id": customfield_id,
            "value": json_copy(row["value"]),
        })
    return result


def assignment_custom_fields(
    current_fields: list[dict[str, Any]],
    target_id: str,
    classification: str,
) -> list[dict[str, Any]]:
    target_id = positive_id(target_id, "target customfield_id")
    if classification not in CLASSIFICATIONS:
        raise ClassificationToolError("Assignment classification is not approved.")
    result = json_copy(current_fields)
    matched = False
    for row in result:
        if row["customfield_id"] == target_id:
            if matched:
                raise ClassificationToolError("Target custom field appears more than once.")
            row["value"] = classification
            matched = True
    if not matched:
        result.append({"customfield_id": target_id, "value": classification})
    return result


def protected_item_state(item: dict[str, Any], target_id: str) -> dict[str, Any]:
    top_level = {
        key: json_copy(value)
        for key, value in item.items()
        if key not in ITEM_READBACK_EXCLUSIONS
    }
    other_values = [
        row for row in project_custom_fields(item)
        if row["customfield_id"] != target_id
    ]
    other_values.sort(key=lambda row: (len(row["customfield_id"]), row["customfield_id"]))
    return {"top_level": top_level, "other_custom_fields": other_values}


def item_evidence(
    item: dict[str, Any],
    target_field: dict[str, Any],
    classification: str,
) -> dict[str, Any]:
    item_id = positive_id(item.get("item_id"), "live item_id")
    name = clean_text(item.get("name"), f"item {item_id} name", 500)
    current_state = json_copy(item)
    current_fields = project_custom_fields(current_state)
    target_id = target_field["customfield_id"]
    target_rows = [row for row in current_fields if row["customfield_id"] == target_id]
    if len(target_rows) > 1:
        raise ClassificationToolError(f"Item {item_id} has duplicate target custom-field rows.")
    target_present = bool(target_rows)
    current_target_value = json_copy(target_rows[0]["value"]) if target_rows else None
    if target_present and current_target_value == classification:
        raise ClassificationToolError(
            f"Item {item_id} is already classified as {classification}; nothing was staged."
        )
    assigned = assignment_custom_fields(current_fields, target_id, classification)
    protected = protected_item_state(current_state, target_id)
    return {
        "item_id": item_id,
        "name": name,
        "target_present": target_present,
        "current_target_value": current_target_value,
        "current_state": current_state,
        "current_state_sha256": digest_for(current_state),
        "current_custom_fields": current_fields,
        "assignment_custom_fields": assigned,
        "protected_state": protected,
        "protected_state_sha256": digest_for(protected),
    }


def validate_custom_field_serializer(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ClassificationToolError("REFUSED: custom_fields must be a list.")
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"customfield_id", "value"}:
            raise ClassificationToolError(
                "REFUSED: each custom_fields row must contain exactly customfield_id and value."
            )
        try:
            customfield_id = positive_id(
                row["customfield_id"], f"custom_fields[{index}].customfield_id"
            )
        except ClassificationToolError as exc:
            raise ClassificationToolError(
                "REFUSED: customfield_id must be canonical positive-ID text."
            ) from exc
        if row["customfield_id"] != customfield_id:
            raise ClassificationToolError("REFUSED: customfield_id is not canonical text.")
        if customfield_id in seen:
            raise ClassificationToolError("REFUSED: duplicate customfield_id in write payload.")
        seen.add(customfield_id)
        clean.append({"customfield_id": customfield_id, "value": json_copy(row["value"])})
    return clean


def oauth_item_write_allowed(
    access_token: str,
    api_domain: str,
    method: str,
    path: str,
    organization_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """The complete OAuth item-write transport and its exact allowlist."""
    if method != "PUT" or not ITEM_PATH_RE.fullmatch(path):
        raise ClassificationToolError(
            "REFUSED: classification assignment is only PUT to one exact positive-ID item endpoint."
        )
    if not isinstance(payload, dict) or set(payload) != {"name", "custom_fields"}:
        raise ClassificationToolError(
            "REFUSED: classification PUT payload must contain exactly name and custom_fields."
        )
    endpoint_id = positive_id(ITEM_PATH_RE.fullmatch(path).group(1), "item endpoint ID")
    _ = endpoint_id
    name = clean_text(payload["name"], "preserved item name", 500)
    if name != payload["name"]:
        raise ClassificationToolError("REFUSED: preserved item name is not canonical.")
    validate_custom_field_serializer(payload["custom_fields"])
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
        raise ClassificationToolError(
            f"Zoho classification PUT failed with HTTP {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise ClassificationToolError(
            f"Zoho classification PUT outcome is indeterminate: {exc.reason}"
        ) from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClassificationToolError(
            "Zoho classification PUT returned invalid JSON; outcome is indeterminate."
        ) from exc
    if not isinstance(result, dict) or result.get("code") != 0:
        message = result.get("message") if isinstance(result, dict) else "invalid response"
        raise ClassificationToolError(
            "Zoho classification PUT returned an invalid or unknown result: "
            + str(message or (result.get("code") if isinstance(result, dict) else "invalid response"))
        )
    return result


def verify_assignment_readback(
    item: dict[str, Any],
    evidence: dict[str, Any],
    target_field: dict[str, Any],
    classification: str,
) -> None:
    item_id = evidence["item_id"]
    if str(item.get("item_id") or "") != item_id:
        raise ClassificationToolError(f"Item {item_id} read-back returned the wrong item.")
    rows = project_custom_fields(item)
    target_rows = [
        row for row in rows
        if row["customfield_id"] == target_field["customfield_id"]
    ]
    if len(target_rows) != 1 or target_rows[0]["value"] != classification:
        raise ClassificationToolError(
            f"Item {item_id} live read-back did not verify {classification}."
        )
    protected = protected_item_state(item, target_field["customfield_id"])
    if (
        protected != evidence["protected_state"]
        or not secrets.compare_digest(
            digest_for(protected), evidence["protected_state_sha256"]
        )
    ):
        raise ClassificationToolError(
            f"Item {item_id} non-target fields or other custom-field values changed. Stop and reconcile."
        )


def stage_plan(
    action: str,
    payload: dict[str, Any],
    sources: dict[str, str],
    live_evidence: dict[str, Any],
) -> Path:
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
        raise ClassificationToolError(
            "Refused to overwrite an existing classification plan."
        ) from exc
    zoho_tool.append_receipt(
        f"zoho_inventory_{action}_plan_staged_not_committed",
        f"plan={path}; sha256={digest}",
    )
    return path


def validate_common_plan(plan: dict[str, Any], action: str) -> None:
    closed_fields(plan, PLAN_FIELDS, "Plan")
    if (
        plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("tool") != TOOL_NAME
        or plan.get("action") != action
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise ClassificationToolError(
            "Plan schema version, tool, action, or approval requirement is invalid."
        )
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise ClassificationToolError("Plan nonce is invalid.")
    created = parse_time(plan["created_utc"], "creation time")
    expires = parse_time(plan["expires_utc"], "expiry")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise ClassificationToolError("Plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise ClassificationToolError("Plan creation time is in the future.")
    if now >= expires:
        raise ClassificationToolError("Plan expired. Stage a new plan for review.")


def validate_create_plan(plan: dict[str, Any]) -> None:
    validate_common_plan(plan, "classification_field_create")
    payload = plan.get("payload")
    if not isinstance(payload, dict):
        raise ClassificationToolError("Create plan payload is invalid.")
    closed_fields(payload, {"field_definition"}, "Create plan payload")
    if payload["field_definition"] != FIXED_FIELD_DEFINITION:
        raise ClassificationToolError("Create plan does not contain the exact fixed field definition.")
    sources = plan.get("sources")
    if not isinstance(sources, dict):
        raise ClassificationToolError("Create plan sources are invalid.")
    closed_fields(sources, {"source"}, "Create plan sources")
    if clean_text(sources["source"], "sources.source") != sources["source"]:
        raise ClassificationToolError("Create plan source is not canonical.")
    live = plan.get("live_evidence")
    if not isinstance(live, dict):
        raise ClassificationToolError("Create live evidence is invalid.")
    closed_fields(live, {"target_fields", "target_fields_sha256"}, "Create live evidence")
    if live["target_fields"] != []:
        raise ClassificationToolError("Create plan evidence is not target-field absence.")
    if not secrets.compare_digest(
        str(live["target_fields_sha256"]), digest_for(live["target_fields"])
    ):
        raise ClassificationToolError("Create target-field evidence hash is invalid.")


def validate_assignment_item_evidence(
    row: dict[str, Any], field: dict[str, Any], classification: str
) -> None:
    closed_fields(row, ITEM_EVIDENCE_FIELDS, "Assignment item evidence")
    if not isinstance(row["current_state"], dict):
        raise ClassificationToolError("Assignment current-state evidence must be an object.")
    if not secrets.compare_digest(
        str(row["current_state_sha256"]), digest_for(row["current_state"])
    ):
        raise ClassificationToolError("Assignment current-state evidence hash is invalid.")
    regenerated = item_evidence(row["current_state"], field, classification)
    if regenerated != row:
        raise ClassificationToolError(
            "Assignment item evidence is not the canonical projection of immutable live state."
        )
    if not secrets.compare_digest(
        str(row["protected_state_sha256"]), digest_for(row["protected_state"])
    ):
        raise ClassificationToolError("Assignment protected-state evidence hash is invalid.")
    validate_custom_field_serializer(row["current_custom_fields"])
    validate_custom_field_serializer(row["assignment_custom_fields"])


def validate_assign_plan(plan: dict[str, Any]) -> None:
    validate_common_plan(plan, "classification_assign")
    payload = plan.get("payload")
    if not isinstance(payload, dict):
        raise ClassificationToolError("Assignment plan payload is invalid.")
    closed_fields(payload, {"classification", "item_ids"}, "Assignment plan payload")
    validated = validate_assign_input({
        "classification": payload["classification"],
        "item_ids": payload["item_ids"],
        "sources": plan.get("sources"),
    })
    if payload != {
        "classification": validated["classification"],
        "item_ids": validated["item_ids"],
    }:
        raise ClassificationToolError("Assignment plan payload is not canonical.")
    live = plan.get("live_evidence")
    if not isinstance(live, dict):
        raise ClassificationToolError("Assignment live evidence is invalid.")
    closed_fields(live, {"field", "items"}, "Assignment live evidence")
    if not isinstance(live["field"], dict):
        raise ClassificationToolError("Assignment field evidence is invalid.")
    field = validate_exact_target_field(live["field"])
    items = live["items"]
    if not isinstance(items, list) or len(items) != len(payload["item_ids"]):
        raise ClassificationToolError(
            "Assignment item evidence count does not match the payload."
        )
    for row in items:
        if not isinstance(row, dict):
            raise ClassificationToolError(
                "Assignment item evidence contains a non-object row."
            )
        validate_assignment_item_evidence(row, field, payload["classification"])
    if [row["item_id"] for row in items] != payload["item_ids"]:
        raise ClassificationToolError(
            "Assignment item evidence order/IDs do not match the payload."
        )


def load_plan(path: Path, action: str) -> dict[str, Any]:
    plan = read_json_object(path, "Plan")
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(
        saved, digest_for(core)
    ):
        raise ClassificationToolError(
            "Plan hash check failed. The plan changed after review."
        )
    if action == "classification_field_create":
        validate_create_plan(plan)
    elif action == "classification_assign":
        validate_assign_plan(plan)
    else:
        raise ClassificationToolError("Unsupported classification plan action.")
    return plan


def lock_path(plan_sha256: str) -> Path:
    if not HEX_64_RE.fullmatch(str(plan_sha256)):
        raise ClassificationToolError("Plan digest is invalid for replay locking.")
    return PLAN_DIR / ".commit-locks" / f"{plan_sha256}.json"


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise ClassificationToolError(
            "This plan has already entered commit and cannot be replayed."
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def command_list_field(_: argparse.Namespace) -> None:
    vault = zoho_tool.load_vault()
    org_id = vault_organization_id(vault)
    response = ui_list_fields(org_id)
    candidates = target_field_candidates(response)
    print(json.dumps({
        "status": "READ_ONLY",
        "method": "GET",
        "endpoint": FIELD_PATH,
        "entity": "item",
        "target_label": FIELD_LABEL,
        "target_fields": candidates,
        "exact_target_count": len(candidates),
        "credentials_exposed": False,
    }, ensure_ascii=False, indent=2))


def command_stage_create(args: argparse.Namespace) -> None:
    # Closed input rejection intentionally precedes vault/session/network access.
    expected = validate_create_input(
        read_json_object(Path(args.input), "Create input")
    )
    vault = zoho_tool.load_vault()
    org_id = vault_organization_id(vault)
    absent = require_target_absent(ui_list_fields(org_id))
    path = stage_plan(
        "classification_field_create",
        {"field_definition": json_copy(FIXED_FIELD_DEFINITION)},
        {"source": expected["source"]},
        {"target_fields": absent, "target_fields_sha256": digest_for(absent)},
    )
    plan = read_json_object(path, "Staged plan")
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED",
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "action": "classification_field_create",
        "field_definition": FIXED_FIELD_DEFINITION,
        "source": expected["source"],
        "approval": APPROVAL_WORD,
    }, ensure_ascii=False, indent=2))


def command_stage_assign(args: argparse.Namespace) -> None:
    # Closed schema and 1-20 checks precede vault/session/token/network access.
    expected = validate_assign_input(
        read_json_object(Path(args.input), "Assignment input")
    )
    vault = zoho_tool.load_vault()
    org_id = vault_organization_id(vault)
    field = require_exact_target_field(ui_list_fields(org_id))
    access_token, vault = zoho_tool.refresh_access_token(vault)
    evidence: list[dict[str, Any]] = []
    for item_id in expected["item_ids"]:
        evidence.append(item_evidence(
            get_item(access_token, vault, item_id),
            field,
            expected["classification"],
        ))
    path = stage_plan(
        "classification_assign",
        {
            "classification": expected["classification"],
            "item_ids": expected["item_ids"],
        },
        expected["sources"],
        {"field": field, "items": evidence},
    )
    plan = read_json_object(path, "Staged plan")
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED",
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "action": "classification_assign",
        "classification": expected["classification"],
        "field": field,
        "items": [{
            "item_id": row["item_id"],
            "name": row["name"],
            "target_present": row["target_present"],
            "current_target_value": row["current_target_value"],
            "current_state_sha256": row["current_state_sha256"],
            "protected_state_sha256": row["protected_state_sha256"],
        } for row in evidence],
        "sources": expected["sources"],
        "approval": APPROVAL_WORD,
        "atomic": False,
    }, ensure_ascii=False, indent=2))


def command_commit_create(args: argparse.Namespace) -> None:
    plan_path = contained_plan(args.plan)
    plan = load_plan(plan_path, "classification_field_create")
    # Approval is checked before lock, vault, UI session, token, or network.
    require_rachad_approval(args.approval)
    lock = lock_path(plan["sha256"])
    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": "classification_field_create",
        "status": "in_flight",
        "started_utc": utc_now().isoformat(),
    }, exclusive=True)
    write_attempted = False
    try:
        vault = zoho_tool.load_vault()
        org_id = vault_organization_id(vault)
        require_target_absent(ui_list_fields(org_id))
        write_attempted = True
        ui_create_fixed_field(org_id)
        created = require_exact_target_field(ui_list_fields(org_id))
    except Exception as exc:
        status = "indeterminate" if write_attempted else "aborted_before_write"
        write_lock(lock, {
            "plan_sha256": plan["sha256"],
            "action": "classification_field_create",
            "status": status,
            "updated_utc": utc_now().isoformat(),
            "reason": str(exc)[:2000],
            "no_retry": True,
        })
        zoho_tool.append_receipt(
            "zoho_inventory_classification_field_create_failed_permanently_locked",
            f"status={status}; plan={plan_path}; sha256={plan['sha256']}",
        )
        raise ClassificationToolError(
            f"Classification-field creation is {status} and permanently locked against replay: {exc}"
        ) from exc
    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": "classification_field_create",
        "status": "committed_verified",
        "field": created,
        "updated_utc": utc_now().isoformat(),
        "no_retry": True,
    })
    zoho_tool.append_receipt(
        "zoho_inventory_classification_field_created_committed_verified",
        f"customfield_id={created['customfield_id']}; plan={plan_path}; sha256={plan['sha256']}",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "action": "classification_field_create",
        "plan_sha256": plan["sha256"],
        "field": created,
        "replay_locked": True,
    }, ensure_ascii=False, indent=2))


def command_commit_assign(args: argparse.Namespace) -> None:
    plan_path = contained_plan(args.plan)
    plan = load_plan(plan_path, "classification_assign")
    # Approval is checked before lock, vault, UI session, token, or network.
    require_rachad_approval(args.approval)
    lock = lock_path(plan["sha256"])
    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": "classification_assign",
        "status": "in_flight",
        "completed_item_ids": [],
        "started_utc": utc_now().isoformat(),
    }, exclusive=True)
    completed_item_ids: list[str] = []
    write_in_flight_item_id = ""
    try:
        vault = zoho_tool.load_vault()
        if UPDATE_SCOPE not in (vault.get("scopes") or []):
            raise ClassificationToolError(f"Saved Zoho connection lacks {UPDATE_SCOPE}.")
        org_id = vault_organization_id(vault)
        access_token, vault = zoho_tool.refresh_access_token(vault)
        staged_field = plan["live_evidence"]["field"]
        classification = plan["payload"]["classification"]
        for evidence in plan["live_evidence"]["items"]:
            # Re-read exact field definition and exact full item state before
            # every non-atomic PUT. A mismatch aborts this permanently locked plan.
            current_field = require_exact_target_field(ui_list_fields(org_id))
            if current_field != staged_field:
                raise ClassificationToolError(
                    "Catalog Classification changed after review. A new plan is required."
                )
            item_id = evidence["item_id"]
            current = get_item(access_token, vault, item_id)
            if not secrets.compare_digest(
                digest_for(current), evidence["current_state_sha256"]
            ) or current != evidence["current_state"]:
                raise ClassificationToolError(
                    f"Item {item_id} changed after review. No PUT was issued for this item or any later item."
                )
            write_in_flight_item_id = item_id
            oauth_item_write_allowed(
                access_token,
                str(vault["api_domain"]),
                "PUT",
                f"/inventory/v1/items/{item_id}",
                org_id,
                {
                    "name": evidence["name"],
                    "custom_fields": json_copy(evidence["assignment_custom_fields"]),
                },
            )
            verified = get_item(access_token, vault, item_id)
            verify_assignment_readback(
                verified, evidence, staged_field, classification
            )
            completed_item_ids.append(item_id)
            write_in_flight_item_id = ""
        zoho_tool.save_vault(vault)
    except Exception as exc:
        if completed_item_ids:
            status = "partial"
        elif write_in_flight_item_id:
            status = "indeterminate"
        else:
            status = "aborted_before_write"
        write_lock(lock, {
            "plan_sha256": plan["sha256"],
            "action": "classification_assign",
            "status": status,
            "completed_item_ids": completed_item_ids,
            "write_in_flight_item_id": write_in_flight_item_id,
            "updated_utc": utc_now().isoformat(),
            "reason": str(exc)[:2000],
            "no_retry": True,
        })
        zoho_tool.append_receipt(
            "zoho_inventory_classification_assignment_partial_indeterminate_or_aborted_no_retry",
            f"status={status}; completed={','.join(completed_item_ids) or 'none'}; "
            f"write_in_flight={write_in_flight_item_id or 'none'}; plan={plan_path}; sha256={plan['sha256']}",
        )
        raise ClassificationToolError(
            f"Classification assignment is {status} and permanently locked against retry. "
            f"Completed item IDs: {completed_item_ids}. Reconcile live Zoho state before any new plan."
        ) from exc
    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": "classification_assign",
        "status": "committed_verified",
        "completed_item_ids": completed_item_ids,
        "classification": plan["payload"]["classification"],
        "updated_utc": utc_now().isoformat(),
        "no_retry": True,
    })
    zoho_tool.append_receipt(
        "zoho_inventory_classification_assignment_committed_verified",
        f"classification={plan['payload']['classification']}; completed={','.join(completed_item_ids)}; "
        f"plan={plan_path}; sha256={plan['sha256']}",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "action": "classification_assign",
        "plan_sha256": plan["sha256"],
        "classification": plan["payload"]["classification"],
        "completed_item_ids": completed_item_ids,
        "atomic": False,
        "replay_locked": True,
    }, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    list_field = commands.add_parser("list-field")
    list_field.set_defaults(func=command_list_field)
    stage_create = commands.add_parser("stage-create")
    stage_create.add_argument("--input", required=True)
    stage_create.set_defaults(func=command_stage_create)
    commit_create = commands.add_parser("commit-create")
    commit_create.add_argument("--plan", required=True)
    commit_create.add_argument("--approval", required=True)
    commit_create.set_defaults(func=command_commit_create)
    stage_assign = commands.add_parser("stage-assign")
    stage_assign.add_argument("--input", required=True)
    stage_assign.set_defaults(func=command_stage_assign)
    commit_assign = commands.add_parser("commit-assign")
    commit_assign.add_argument("--plan", required=True)
    commit_assign.add_argument("--approval", required=True)
    commit_assign.set_defaults(func=command_commit_assign)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (ClassificationToolError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
