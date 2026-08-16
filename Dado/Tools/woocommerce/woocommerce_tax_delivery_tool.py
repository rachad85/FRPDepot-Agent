#!/usr/bin/env python
"""Fixed, approval-gated WooCommerce delivery-tax correction (safety v2).

This one-purpose tool can stage with authenticated GETs only or, after a later
byte-exact APPROVED command, attempt exactly 17 non-atomic tax-rate PUTs.  It has
no configurable write IDs, order, route, method or payload.  Stage and commit
require the exact diagnosed 18-row table and the exact semantic settings/classes;
WooCommerce, UPS and Freight Checkout Guard versions are pinned by the plan.

Commit takes a kernel-owned GLOBAL single-flight mutex before credentials,
network, or the per-plan permanent attempt lock.  After every PUT it fresh-GETs
the complete 18-row table and all semantic state and verifies the exact partial
prefix.  Attempted, HTTP-successful and fresh-GET-verified counters are persisted
separately.  Any uncertainty stops with no retry, rollback, resume or cleanup.
After all writes, 15 isolated anonymous Store API carts validate all Canadian
provinces/territories plus two billing/delivery cross cases without checkout,
order, payment or email creation, followed by another protected-state readback.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import woocommerce_common as wc

COMMON_DIR = Path(__file__).resolve().parents[1] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))
from tax_delivery_commit_lock import (  # noqa: E402
    TaxDeliveryCommitBusy,
    TaxDeliveryCommitLockError,
    tax_delivery_commit_lock,
)

TOOL_NAME = "FRP Depot Fixed WooCommerce Delivery-Tax Correction Tool"
TOOL_VERSION = "2.0.0"
SCHEMA_VERSION = 2
PLAN_LIFETIME_HOURS = 24
APPROVAL_BYTES = b"APPROVED"
EXACT_ORIGIN = wc.ALLOWED_ORIGIN
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "woocommerce_tax_delivery_plans"
DIAGNOSIS_DIR = ROOT / "Dado" / "20_Working" / "tax_delivery_diagnosis"
DIAGNOSIS_PATH = DIAGNOSIS_DIR / "live_tax_delivery_diagnosis_20260814.json"
DIAGNOSIS_SHA256 = "3a88703427b59dd930695271d3c3320ac7b7c105e4868090a08e7963677d254d"
DIAGNOSIS_SUMMARY_PATH = DIAGNOSIS_DIR / "tax_delivery_diagnosis_summary_20260814.json"
DIAGNOSIS_SUMMARY_SHA256 = "76369eac397cc39a02daa0de27e4d655620e4b1ef0450f517c6fe79c77489379"
INDEPENDENT_REVIEW_PATH = DIAGNOSIS_DIR / "tax_tool_independent_safety_review_20260814.md"
INDEPENDENT_REVIEW_SHA256 = "fb3f222c4ed204dc0ea6c3b82ea2c65665c8535fc68e63cf6bb4f91f7de743b6"
TAXES_ENDPOINT = "/taxes"
TARGET_IDS = tuple(range(2, 19))
# Province components are adjacent; callers cannot alter this order.
WRITE_ORDER = (2, 3, 15, 4, 16, 5, 6, 7, 8, 9, 10, 11, 12, 17, 13, 18, 14)
PROTECTED_RATE_ID = 1
NAME_CHANGES = {3: "GST (5%)", 17: "QST (9.975%)"}
DIAGNOSED_TAX_FIELDS = (
    "id", "country", "state", "postcode", "city", "rate", "name", "priority",
    "compound", "shipping", "order", "class",
)
# Woo 11 also exposes plural location restrictions. They are mutable/matching-
# significant and therefore fingerprinted even though the older diagnosis
# projection recorded only the singular compatibility fields.
TAX_FIELDS = (
    "id", "country", "state", "postcode", "postcodes", "city", "cities", "rate",
    "name", "priority", "compound", "shipping", "order", "class",
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
NONCE32 = re.compile(r"^[0-9a-f]{32}$")
VERSION_TEXT = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")
STORE_BASE = EXACT_ORIGIN + "/wp-json/wc/store/v1"
STORE_VARIATION_ID = 2028
STORE_REQUEST_TIMEOUT_SECONDS = 30
STORE_REQUESTS_PER_CASE = 4
STORE_CASE_COUNT = 15
MONEY_QUANTUM = Decimal("0.01")

SETTING_SPECS: tuple[tuple[str, str, str], ...] = (
    ("general", "woocommerce_calc_taxes", "yes"),
    ("tax", "woocommerce_tax_based_on", "shipping"),
    ("tax", "woocommerce_shipping_tax_class", "inherit"),
    ("tax", "woocommerce_tax_round_at_subtotal", "no"),
    ("tax", "woocommerce_prices_include_tax", "no"),
    ("shipping", "woocommerce_ship_to_destination", "billing"),
)
EXPECTED_TAX_CLASSES = (
    {"slug": "reduced-rate", "name": "Reduced rate"},
    {"slug": "standard", "name": "Standard rate"},
    {"slug": "zero-rate", "name": "Zero rate"},
)
VERSION_PLUGINS = {
    "woocommerce_core": {
        "plugin": "woocommerce/woocommerce.php", "name": "WooCommerce",
    },
    "ups_shipping": {
        "plugin": "woocommerce-shipping-ups/woocommerce-shipping-ups.php",
        "name": "WooCommerce UPS Shipping",
    },
    "freight_checkout_guard": {
        "plugin": "frpdepot-freight-checkout-guard/frpdepot-freight-checkout-guard.php",
        "name": "FRP Depot Freight Checkout Guard",
    },
}


def _rate(rate_id: int, state: str, rate: str, name: str, priority: int,
          order: int) -> dict[str, Any]:
    return {
        "id": rate_id, "country": "CA", "state": state,
        "postcode": "", "postcodes": [], "city": "", "cities": [],
        "rate": rate, "name": name, "priority": priority, "compound": False,
        "shipping": False, "order": order, "class": "standard",
    }


EXPECTED_BEFORE_ROWS: tuple[dict[str, Any], ...] = (
    {
        "id": 1, "country": "", "state": "", "postcode": "", "postcodes": [],
        "city": "", "cities": [], "rate": "13.0000", "name": "Tax", "priority": 1, "compound": False,
        "shipping": False, "order": 0, "class": "standard",
    },
    _rate(2, "AB", "5.0000", "GST (5%)", 1, 0),
    _rate(3, "BC", "5.0000", "GST 5%)", 1, 1),
    _rate(4, "MB", "5.0000", "GST (5%)", 1, 2),
    _rate(5, "NB", "15.0000", "HST (15%)", 1, 3),
    _rate(6, "NL", "15.0000", "HST (15%)", 1, 4),
    _rate(7, "NS", "14.0000", "HST (14%)", 1, 5),
    _rate(8, "NT", "5.0000", "GST (5%)", 1, 6),
    _rate(9, "NU", "5.0000", "GST (5%)", 1, 7),
    _rate(10, "ON", "13.0000", "HST (13%)", 1, 8),
    _rate(11, "PE", "15.0000", "HST (15%)", 1, 9),
    _rate(12, "QC", "5.0000", "GST (5%)", 1, 10),
    _rate(13, "SK", "5.0000", "GST (5%)", 1, 11),
    _rate(14, "YT", "5.0000", "GST (5%)", 1, 12),
    _rate(15, "BC", "7.0000", "PST (7%)", 2, 13),
    _rate(16, "MB", "7.0000", "PST (7%)", 2, 14),
    _rate(17, "QC", "9.9750", "PST (9.975%)", 2, 15),
    _rate(18, "SK", "6.0000", "PST (6%)", 2, 16),
)


def _place(label: str, city: str, state: str, postcode: str) -> dict[str, str]:
    return {"label": label, "city": city, "state": state, "postcode": postcode, "country": "CA"}


PROVINCE_PLACES: tuple[dict[str, str], ...] = (
    _place("Alberta", "Edmonton", "AB", "T5J 0N3"),
    _place("British Columbia", "Vancouver", "BC", "V6B 1A1"),
    _place("Manitoba", "Winnipeg", "MB", "R3C 4T3"),
    _place("New Brunswick", "Fredericton", "NB", "E3B 1B5"),
    _place("Newfoundland and Labrador", "St. John's", "NL", "A1C 1A1"),
    _place("Nova Scotia", "Halifax", "NS", "B3J 1A1"),
    _place("Northwest Territories", "Yellowknife", "NT", "X1A 2P7"),
    _place("Nunavut", "Iqaluit", "NU", "X0A 0H0"),
    _place("Ontario", "Toronto", "ON", "M5X 1A9"),
    _place("Prince Edward Island", "Charlottetown", "PE", "C1A 1A1"),
    _place("Quebec", "Trois-Rivières", "QC", "G8T 1A1"),
    _place("Saskatchewan", "Regina", "SK", "S4P 3Y2"),
    _place("Yukon", "Whitehorse", "YT", "Y1A 1A1"),
)
PLACE_BY_STATE = {place["state"]: place for place in PROVINCE_PLACES}
PROBE_CASES: tuple[dict[str, Any], ...] = tuple(
    {"label": place["label"], "billing": place, "delivery": place}
    for place in PROVINCE_PLACES
) + (
    {
        "label": "Billing Ontario; delivery Quebec",
        "billing": PLACE_BY_STATE["ON"], "delivery": PLACE_BY_STATE["QC"],
    },
    {
        "label": "Billing Quebec; delivery Alberta",
        "billing": PLACE_BY_STATE["QC"], "delivery": PLACE_BY_STATE["AB"],
    },
)


class TaxDeliveryError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_value(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def digest_for(core: dict[str, Any]) -> str:
    return digest_value(core)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise TaxDeliveryError(f"Safety artifact is unreadable: {path}") from exc
    return digest.hexdigest()


def read_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaxDeliveryError(f"Plan/evidence JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise TaxDeliveryError("Plan/evidence JSON must contain exactly one object.")
    return value


def evidence_contract() -> dict[str, Any]:
    return {
        "live_diagnosis_path": str(DIAGNOSIS_PATH),
        "live_diagnosis_file_sha256": DIAGNOSIS_SHA256,
        "diagnosis_summary_path": str(DIAGNOSIS_SUMMARY_PATH),
        "diagnosis_summary_file_sha256": DIAGNOSIS_SUMMARY_SHA256,
        "independent_safety_review_path": str(INDEPENDENT_REVIEW_PATH),
        "independent_safety_review_file_sha256": INDEPENDENT_REVIEW_SHA256,
        "diagnosed_projection_sha256": digest_value([
            {field: row[field] for field in DIAGNOSED_TAX_FIELDS}
            for row in EXPECTED_BEFORE_ROWS
        ]),
        "diagnosed_conclusion": "merchandise_tax_follows_delivery_but_shipping_tax_is_disabled",
        "accounting_corroboration": "Zoho SO-00046 taxes shipping at GST plus QST",
    }


def validate_diagnosis_evidence() -> dict[str, Any]:
    for label, path, expected_hash in (
        ("live tax-delivery diagnosis", DIAGNOSIS_PATH, DIAGNOSIS_SHA256),
        ("tax-delivery diagnosis summary", DIAGNOSIS_SUMMARY_PATH, DIAGNOSIS_SUMMARY_SHA256),
        ("independent safety review", INDEPENDENT_REVIEW_PATH, INDEPENDENT_REVIEW_SHA256),
    ):
        if not secrets.compare_digest(file_sha256(path), expected_hash):
            raise TaxDeliveryError(f"Pinned {label} SHA-256 changed; operation is refused.")
    diagnosis = read_json(DIAGNOSIS_PATH)
    settings = diagnosis.get("current_settings")
    rows = settings.get("tax_rates") if isinstance(settings, dict) else None
    expected_diagnosed = [
        {field: row[field] for field in DIAGNOSED_TAX_FIELDS}
        for row in EXPECTED_BEFORE_ROWS
    ]
    if rows != expected_diagnosed:
        raise TaxDeliveryError("Pinned diagnosis no longer contains the exact diagnosed 18-rate projection.")
    summary = read_json(DIAGNOSIS_SUMMARY_PATH)
    if (diagnosis.get("status") != "CURRENT_CONFIGURATION_USES_DELIVERY_ADDRESS"
            or summary.get("status") != "ITEM_TAX_BY_DELIVERY_CONFIRMED__SHIPPING_TAX_DISABLED"
            or (summary.get("safety") or {}).get("tax_rate_changes") != 0
            or (summary.get("safety") or {}).get("website_configuration_changes") != 0):
        raise TaxDeliveryError("Pinned diagnosis safety contract changed.")
    return evidence_contract()


def validate_vault(vault: dict[str, Any]) -> None:
    if vault.get("declared_permissions") != "read_write":
        raise TaxDeliveryError("Saved WooCommerce key is not declared Read/Write.")
    try:
        origin = wc.normalize_site_url(str(vault.get("site_url") or ""))
    except wc.WooError as exc:
        raise TaxDeliveryError("Saved WooCommerce origin is not exact.") from exc
    if origin != EXACT_ORIGIN:
        raise TaxDeliveryError("Saved WooCommerce origin is not exact.")


def strict_rate_id(value: Any, *, target: bool | None = None) -> int:
    if type(value) is not int or value <= 0:
        raise TaxDeliveryError("Tax rate ID must be a canonical positive integer.")
    if target is True and value not in TARGET_IDS:
        raise TaxDeliveryError("Tax rate ID is outside fixed IDs 2-18.")
    if target is False and value != PROTECTED_RATE_ID:
        raise TaxDeliveryError("Protected tax rate identity is not exact ID 1.")
    return value


def setting_endpoint(group: str, setting_id: str) -> str:
    if (group, setting_id) not in {(g, i) for g, i, _ in SETTING_SPECS}:
        raise TaxDeliveryError("Setting route is outside the fixed semantic read set.")
    return f"/settings/{group}/{setting_id}"


def authenticated_get_contract() -> list[dict[str, Any]]:
    routes = [{
        "method": "GET", "endpoint": TAXES_ENDPOINT,
        "params": {"per_page": 100, "page": 1, "order": "asc", "orderby": "order"},
    }]
    routes.extend({
        "method": "GET", "endpoint": setting_endpoint(group, setting_id),
        "params": {"_fields": "id,value"},
    } for group, setting_id, _ in SETTING_SPECS)
    routes.extend((
        {"method": "GET", "endpoint": "/taxes/classes", "params": {"_fields": "slug,name"}},
        {"method": "GET", "endpoint": "/system_status", "params": {"_fields": "active_plugins"}},
    ))
    return routes


def validate_route(method: str, endpoint: str) -> None:
    allowed = {("GET", row["endpoint"]) for row in authenticated_get_contract()}
    allowed |= {("PUT", f"/taxes/{rate_id}") for rate_id in TARGET_IDS}
    if (method, endpoint) not in allowed:
        raise TaxDeliveryError("REFUSED: method/path is outside the fixed delivery-tax correction.")


def validate_store_route(method: str, endpoint: str) -> None:
    if (method, endpoint) not in {
        ("GET", "/cart"), ("POST", "/cart/add-item"), ("POST", "/cart/update-customer"),
    }:
        raise TaxDeliveryError("REFUSED: Store API route is outside the isolated cart proof.")


def _validate_links(value: Any, rate_id: int) -> None:
    if not isinstance(value, dict) or set(value) != {"self", "collection"}:
        raise TaxDeliveryError(f"Tax rate {rate_id} has an unexpected derived _links envelope.")
    expected = {
        "self": f"{EXACT_ORIGIN}/wp-json/wc/v3/taxes/{rate_id}",
        "collection": f"{EXACT_ORIGIN}/wp-json/wc/v3/taxes",
    }
    for relation, href in expected.items():
        entries = value.get(relation)
        if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
            raise TaxDeliveryError(f"Tax rate {rate_id} has malformed derived {relation} links.")
        entry = entries[0]
        if entry.get("href") != href or any(key not in {"href", "targetHints"} for key in entry):
            raise TaxDeliveryError(f"Tax rate {rate_id} derived {relation} link is not exact.")


def _validate_setting_links(value: Any, group: str, setting_id: str) -> None:
    if not isinstance(value, dict) or set(value) != {"self", "collection"}:
        raise TaxDeliveryError(f"Semantic setting {setting_id} has an unexpected derived _links envelope.")
    expected = {
        "self": f"{EXACT_ORIGIN}/wp-json/wc/v3/settings/{group}/{setting_id}",
        "collection": f"{EXACT_ORIGIN}/wp-json/wc/v3/settings/{group}",
    }
    for relation, href in expected.items():
        entries = value.get(relation)
        if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
            raise TaxDeliveryError(f"Semantic setting {setting_id} has malformed derived {relation} links.")
        entry = entries[0]
        if entry.get("href") != href or any(key not in {"href", "targetHints"} for key in entry):
            raise TaxDeliveryError(f"Semantic setting {setting_id} derived {relation} link is not exact.")


def validate_rate_shape(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise TaxDeliveryError("Every tax rate must be an object.")
    keys = set(row)
    required = set(TAX_FIELDS)
    if keys not in (required, required | {"_links"}):
        raise TaxDeliveryError("Tax-rate REST shape exposed missing or unexpected mutable fields.")
    rate_id = strict_rate_id(row.get("id"))
    if "_links" in row:
        _validate_links(row["_links"], rate_id)
    projected = {field: copy.deepcopy(row[field]) for field in TAX_FIELDS}
    scalar_strings = ("country", "state", "postcode", "city", "rate", "name", "class")
    if any(type(projected[field]) is not str for field in scalar_strings):
        raise TaxDeliveryError(f"Tax rate {rate_id} contains a non-canonical string field.")
    for field in ("postcodes", "cities"):
        if (type(projected[field]) is not list
                or any(type(item) is not str for item in projected[field])):
            raise TaxDeliveryError(f"Tax rate {rate_id} {field} is not a canonical string list.")
    if type(projected["priority"]) is not int or type(projected["order"]) is not int:
        raise TaxDeliveryError(f"Tax rate {rate_id} priority/order is not a canonical integer.")
    if type(projected["compound"]) is not bool or type(projected["shipping"]) is not bool:
        raise TaxDeliveryError(f"Tax rate {rate_id} compound/shipping is not a canonical boolean.")
    return projected


def target_projection() -> list[dict[str, Any]]:
    rows = copy.deepcopy(list(EXPECTED_BEFORE_ROWS))
    by_id = {row["id"]: row for row in rows}
    for rate_id in WRITE_ORDER:
        by_id[rate_id]["shipping"] = True
        if rate_id in NAME_CHANGES:
            by_id[rate_id]["name"] = NAME_CHANGES[rate_id]
    return rows


def prefix_projection(verified_count: int) -> list[dict[str, Any]]:
    if type(verified_count) is not int or not 0 <= verified_count <= len(WRITE_ORDER):
        raise TaxDeliveryError("Verified prefix count is outside 0-17.")
    rows = copy.deepcopy(list(EXPECTED_BEFORE_ROWS))
    by_id = {row["id"]: row for row in rows}
    for rate_id in WRITE_ORDER[:verified_count]:
        by_id[rate_id]["shipping"] = True
        if rate_id in NAME_CHANGES:
            by_id[rate_id]["name"] = NAME_CHANGES[rate_id]
    return rows


def validate_tax_collection(rows: Any, *, expected: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or len(rows) != 18:
        raise TaxDeliveryError("Live tax table must contain exactly 18 rows.")
    projected = [validate_rate_shape(row) for row in rows]
    ids = [row["id"] for row in projected]
    if ids != list(range(1, 19)) or len(ids) != len(set(ids)):
        raise TaxDeliveryError("Live tax rows must be the exact unique semantic order IDs 1-18.")
    expected_rows = copy.deepcopy(expected if expected is not None else list(EXPECTED_BEFORE_ROWS))
    if projected != expected_rows:
        raise TaxDeliveryError("Live complete tax table differs from the exact required state.")
    return projected


def full_fingerprint(rows: list[dict[str, Any]]) -> str:
    return digest_value(rows)


def field_fingerprints(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    return {
        str(row["id"]): {field: digest_value(row[field]) for field in TAX_FIELDS}
        for row in rows
    }


def rate_state_contract(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": 18,
        "ids_in_semantic_order": list(range(1, 19)),
        "rows": copy.deepcopy(rows),
        "aggregate_sha256": full_fingerprint(rows),
        "per_field_sha256": field_fingerprints(rows),
    }


def read_all_rates(vault: dict[str, Any]) -> list[dict[str, Any]]:
    validate_route("GET", TAXES_ENDPOINT)
    params = {"per_page": 100, "page": 1, "order": "asc", "orderby": "order"}
    value, headers = wc.api_get(TAXES_ENDPOINT, params, vault)
    if not isinstance(value, list):
        raise TaxDeliveryError("WooCommerce complete tax-table GET returned a non-list.")
    total_text = str((headers or {}).get("x-wp-total") or "")
    if not total_text:
        raise TaxDeliveryError("WooCommerce complete tax-table GET omitted x-wp-total.")
    try:
        total = int(total_text)
    except ValueError as exc:
        raise TaxDeliveryError("WooCommerce tax-table total header is invalid.") from exc
    if total != len(value) or total != 18:
        raise TaxDeliveryError("WooCommerce tax table did not fit the exact one-page 18-row read.")
    return value


def read_setting(vault: dict[str, Any], group: str, setting_id: str, expected: str) -> str:
    endpoint = setting_endpoint(group, setting_id)
    validate_route("GET", endpoint)
    value, _ = wc.api_get(endpoint, {"_fields": "id,value"}, vault)
    if (not isinstance(value, dict) or set(value) != {"id", "value", "_links"}
            or value.get("id") != setting_id or type(value.get("value")) is not str):
        raise TaxDeliveryError(f"Semantic setting {setting_id} returned an unexpected exact shape.")
    _validate_setting_links(value["_links"], group, setting_id)
    if value["value"] != expected:
        raise TaxDeliveryError(f"Semantic setting {setting_id} is not exact expected value {expected!r}.")
    return value["value"]


def read_tax_classes(vault: dict[str, Any]) -> list[dict[str, str]]:
    endpoint = "/taxes/classes"
    validate_route("GET", endpoint)
    value, _ = wc.api_get(endpoint, {"_fields": "slug,name"}, vault)
    if not isinstance(value, list) or not all(isinstance(row, dict) and set(row) == {"slug", "name"}
                                               and type(row["slug"]) is str and type(row["name"]) is str
                                               for row in value):
        raise TaxDeliveryError("Tax class GET returned an unexpected exact shape.")
    normalized = sorted(copy.deepcopy(value), key=lambda row: row["slug"])
    if normalized != list(EXPECTED_TAX_CLASSES):
        raise TaxDeliveryError("WooCommerce tax class set is not the exact staged set.")
    return normalized


def read_versions(vault: dict[str, Any]) -> dict[str, dict[str, str]]:
    endpoint = "/system_status"
    validate_route("GET", endpoint)
    value, _ = wc.api_get(endpoint, {"_fields": "active_plugins"}, vault)
    if not isinstance(value, dict) or set(value) != {"active_plugins"} or not isinstance(value["active_plugins"], list):
        raise TaxDeliveryError("WooCommerce system status returned an unexpected exact shape.")
    plugins = value["active_plugins"]
    result: dict[str, dict[str, str]] = {}
    for label, identity in VERSION_PLUGINS.items():
        matches = [row for row in plugins if isinstance(row, dict) and row.get("plugin") == identity["plugin"]]
        if len(matches) != 1:
            raise TaxDeliveryError(f"Required active plugin {identity['name']} is missing or duplicated.")
        row = matches[0]
        version = row.get("version")
        if row.get("name") != identity["name"] or type(version) is not str or not VERSION_TEXT.fullmatch(version):
            raise TaxDeliveryError(f"Required active plugin {identity['name']} has invalid identity/version.")
        result[label] = {**identity, "version": version}
    return result


def semantic_snapshot(vault: dict[str, Any], *, expected_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = validate_tax_collection(read_all_rates(vault), expected=expected_rows)
    settings = {
        setting_id: read_setting(vault, group, setting_id, expected)
        for group, setting_id, expected in SETTING_SPECS
    }
    classes = read_tax_classes(vault)
    versions = read_versions(vault)
    return {
        "tax_rates": rows,
        "settings": settings,
        "tax_classes": classes,
        "versions": versions,
    }


def semantic_fingerprints(snapshot: dict[str, Any]) -> dict[str, Any]:
    semantic = {
        "settings": snapshot["settings"],
        "tax_classes": snapshot["tax_classes"],
        "versions": snapshot["versions"],
    }
    return {
        "aggregate_sha256": digest_value(semantic),
        "setting_sha256": {key: digest_value(value) for key, value in snapshot["settings"].items()},
        "tax_classes_sha256": digest_value(snapshot["tax_classes"]),
        "version_sha256": {key: digest_value(value) for key, value in snapshot["versions"].items()},
    }


def semantic_state_contract(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "settings": copy.deepcopy(snapshot["settings"]),
        "tax_classes": copy.deepcopy(snapshot["tax_classes"]),
        "versions": copy.deepcopy(snapshot["versions"]),
        "fingerprints": semantic_fingerprints(snapshot),
    }


def full_snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    """Aggregate the complete table and every matching-significant semantic read."""
    return digest_value({
        "tax_rates": snapshot["tax_rates"],
        "settings": snapshot["settings"],
        "tax_classes": snapshot["tax_classes"],
        "versions": snapshot["versions"],
    })


def validate_semantic_state_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"settings", "tax_classes", "versions", "fingerprints"}:
        raise TaxDeliveryError("Plan semantic-state schema is not closed and exact.")
    expected_settings = {setting_id: expected for _, setting_id, expected in SETTING_SPECS}
    if value["settings"] != expected_settings or value["tax_classes"] != list(EXPECTED_TAX_CLASSES):
        raise TaxDeliveryError("Plan settings/tax-class semantic state is not exact.")
    versions = value["versions"]
    if not isinstance(versions, dict) or set(versions) != set(VERSION_PLUGINS):
        raise TaxDeliveryError("Plan version set is not exact.")
    for label, identity in VERSION_PLUGINS.items():
        row = versions[label]
        if (not isinstance(row, dict) or set(row) != {"plugin", "name", "version"}
                or row.get("plugin") != identity["plugin"] or row.get("name") != identity["name"]
                or type(row.get("version")) is not str or not VERSION_TEXT.fullmatch(row["version"])):
            raise TaxDeliveryError("Plan plugin identity/version is invalid.")
    snapshot = {"settings": value["settings"], "tax_classes": value["tax_classes"], "versions": versions}
    if value["fingerprints"] != semantic_fingerprints(snapshot):
        raise TaxDeliveryError("Plan semantic per-field/aggregate fingerprints changed.")
    return copy.deepcopy(value)


def assert_snapshot_matches_plan(plan: dict[str, Any], snapshot: dict[str, Any], verified_count: int) -> None:
    expected_rows = prefix_projection(verified_count)
    if snapshot["tax_rates"] != expected_rows:
        raise TaxDeliveryError(f"Complete tax table does not equal exact verified prefix {verified_count}/17.")
    live_semantic = semantic_state_contract(snapshot)
    if live_semantic != plan["semantic_state"]:
        raise TaxDeliveryError("Woo settings, tax classes, or required plugin versions drifted.")


def payload_for_rate(rate_id: Any) -> dict[str, Any]:
    fixed_id = strict_rate_id(rate_id, target=True)
    payload: dict[str, Any] = {"shipping": True}
    if fixed_id in NAME_CHANGES:
        payload["name"] = NAME_CHANGES[fixed_id]
    return payload


def validate_payload(rate_id: Any, payload: Any) -> dict[str, Any]:
    expected = payload_for_rate(rate_id)
    # Python considers True == 1, so ordinary dict equality is not an exact JSON
    # type check.  Require the canonical boolean and canonical strings first.
    if (not isinstance(payload, dict) or payload.get("shipping") is not True
            or any(type(value) is not str for key, value in payload.items() if key == "name")
            or set(payload) != set(expected) or payload != expected):
        raise TaxDeliveryError(f"Tax rate {rate_id} payload is not exact.")
    return copy.deepcopy(expected)


def write_contract() -> list[dict[str, Any]]:
    return [{
        "sequence": sequence, "rate_id": rate_id, "method": "PUT",
        "endpoint": f"/taxes/{rate_id}", "payload": payload_for_rate(rate_id),
    } for sequence, rate_id in enumerate(WRITE_ORDER, start=1)]


def transaction_contract() -> dict[str, Any]:
    return {
        "write_count": 17,
        "writes": write_contract(),
        "fixed_write_order": list(WRITE_ORDER),
        "paired_components_adjacent": {"BC": [3, 15], "MB": [4, 16], "QC": [12, 17], "SK": [13, 18]},
        "non_atomic": True,
        "non_atomic_disclosure": (
            "17 sequential PUTs are NOT atomic; a failure or checkout can observe a partial table. "
            "A global single-flight mutex spans preflight, writes, readbacks and carts. The plan locks "
            "permanently before PUT 1 and has no retry, rollback, resume, delete or cleanup."
        ),
        "allowed_changes": {
            "ids_2_through_18": "shipping false to true",
            "id_3_name": "GST 5%) -> GST (5%)",
            "id_17_name": "PST (9.975%) -> QST (9.975%)",
        },
        "protected_scope": "ID 1 and every non-enumerated field, row, setting, class, plugin and version",
        "failure_policy": "attempt_once_permanent_indeterminate_no_retry_no_rollback_no_resume_no_cleanup",
    }


def commission_contract() -> dict[str, Any]:
    return {
        "requestor": "Rachad Homsi", "request_date": "2026-08-14",
        "commissioning_scope": "build_test_and_get_only_stage_not_commit",
        "commit_requires_later_exact_plan_approval": "APPROVED",
    }


def verification_contract() -> dict[str, Any]:
    return {
        "after_each_put": (
            "fresh complete 18-rate GET plus six exact settings, exact tax classes and three plugin versions; "
            "completed rows after-state and unattempted rows before-state"
        ),
        "anonymous_store_api": {
            "case_count": STORE_CASE_COUNT,
            "request_count": STORE_CASE_COUNT * STORE_REQUESTS_PER_CASE,
            "request_timeout_seconds": STORE_REQUEST_TIMEOUT_SECONDS,
            "variation_id": STORE_VARIATION_ID,
            "province_territory_states": [place["state"] for place in PROVINCE_PLACES],
            "cross_cases": [PROBE_CASES[-2]["label"], PROBE_CASES[-1]["label"]],
            "methods_and_paths_per_cart": [
                "GET /cart", "POST /cart/add-item", "POST /cart/update-customer", "GET /cart",
            ],
            "checkout_submitted": False, "orders_created": 0, "payments_created": 0,
            "tax_math": "Decimal ROUND_HALF_UP CAD 0.01 per configured component; component totals summed",
            "labels": "live configured rate labels and optional Store API tax lines must match exact components",
        },
        "after_carts": "fresh complete protected semantic snapshot equals final table and staged settings/classes/versions",
        "mismatch_policy": "permanent_locked_failure_no_retry_or_rollback",
    }


def _validate_rate_state_contract(value: Any, expected_rows: list[dict[str, Any]], label: str) -> None:
    if not isinstance(value, dict) or value != rate_state_contract(expected_rows):
        raise TaxDeliveryError(f"Plan {label} exact table/per-field fingerprints changed.")


def plan_core(snapshot: dict[str, Any], created: datetime) -> dict[str, Any]:
    before = copy.deepcopy(list(EXPECTED_BEFORE_ROWS))
    after = target_projection()
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "origin": EXACT_ORIGIN,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "commission": commission_contract(),
        "evidence": evidence_contract(),
        "tax_rates_before": rate_state_contract(before),
        "tax_rates_expected_final": rate_state_contract(after),
        "semantic_state": semantic_state_contract(snapshot),
        "full_staged_state_sha256": full_snapshot_fingerprint(snapshot),
        "stage_get_routes_per_snapshot": authenticated_get_contract(),
        "transaction": transaction_contract(),
        "postcommit_verification": verification_contract(),
    }


def write_json(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise TaxDeliveryError(f"Safety artifact already exists and will not be overwritten: {path}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def stage_plan(snapshot: dict[str, Any]) -> tuple[Path, Path]:
    validate_tax_collection(snapshot["tax_rates"])
    validate_semantic_state_contract(semantic_state_contract(snapshot))
    created = utc_now()
    core = plan_core(snapshot, created)
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{created.strftime('%Y%m%dT%H%M%SZ')}_tax_delivery_{digest[:16]}"
    path = PLAN_DIR / f"{stem}.json"
    summary_path = PLAN_DIR / f"{stem}_summary.json"
    write_json(path, plan, exclusive=True)
    summary = {
        "status": "STAGED_NOT_COMMITTED_GET_ONLY",
        "external_woocommerce_writes": 0,
        "plan": str(path), "semantic_plan_sha256": digest,
        "physical_plan_file_sha256": file_sha256(path),
        "expires_utc": core["expires_utc"],
        "fixed_write_order_if_later_approved": list(WRITE_ORDER),
        "exact_changes": transaction_contract()["allowed_changes"],
        "write_count_if_later_approved": 17,
        "approval_required_from_rachad": "APPROVED",
        "commissioning_is_not_commit_approval": True,
        "non_atomic_warning": transaction_contract()["non_atomic_disclosure"],
        "stage_authenticated_get_routes_per_snapshot": authenticated_get_contract(),
        "stage_snapshot_count": 2,
        "stage_authenticated_get_count": 2 * len(authenticated_get_contract()),
        "stage_store_api_cart_requests": 0,
        "tax_or_store_puts": 0, "persistent_store_posts": 0,
        "orders_created": 0, "payments_created": 0, "emails_sent": 0,
        "semantic_fingerprint": core["semantic_state"]["fingerprints"]["aggregate_sha256"],
        "full_staged_state_sha256": core["full_staged_state_sha256"],
        "tax_table_fingerprint": core["tax_rates_before"]["aggregate_sha256"],
    }
    write_json(summary_path, summary, exclusive=True)
    wc.append_receipt(
        "woocommerce_tax_delivery_v2_plan_staged_get_only",
        f"plan={path}; semantic_sha256={digest}; physical_sha256={file_sha256(path)}; "
        f"summary={summary_path}; authenticated_gets={2 * len(authenticated_get_contract())}; "
        "tax_or_store_puts=0; persistent_store_posts=0; orders_created=0",
    )
    return path, summary_path


def load_plan(path: str | Path) -> dict[str, Any]:
    plan = read_json(path)
    saved = plan.pop("sha256", None)
    if (not isinstance(saved, str) or not HEX64.fullmatch(saved)
            or not secrets.compare_digest(saved, digest_for(plan))):
        raise TaxDeliveryError("Plan SHA-256 check failed; the reviewed plan changed.")
    required = {
        "schema_version", "tool", "tool_version", "origin", "created_utc", "expires_utc", "nonce",
        "commission", "evidence", "tax_rates_before", "tax_rates_expected_final", "semantic_state",
        "full_staged_state_sha256", "stage_get_routes_per_snapshot", "transaction",
        "postcommit_verification",
    }
    if set(plan) != required:
        raise TaxDeliveryError("Plan schema is not closed and exact.")
    if (plan["schema_version"] != SCHEMA_VERSION or plan["tool"] != TOOL_NAME
            or plan["tool_version"] != TOOL_VERSION or plan["origin"] != EXACT_ORIGIN
            or plan["commission"] != commission_contract() or plan["evidence"] != evidence_contract()
            or plan["stage_get_routes_per_snapshot"] != authenticated_get_contract()
            or plan["transaction"] != transaction_contract()
            or plan["postcommit_verification"] != verification_contract()):
        raise TaxDeliveryError("Plan identity/semantic fixed contract differs from safety v2.")
    _validate_rate_state_contract(plan["tax_rates_before"], list(EXPECTED_BEFORE_ROWS), "before-state")
    _validate_rate_state_contract(plan["tax_rates_expected_final"], target_projection(), "expected-final-state")
    validate_semantic_state_contract(plan["semantic_state"])
    staged_snapshot = {
        "tax_rates": list(EXPECTED_BEFORE_ROWS),
        "settings": plan["semantic_state"]["settings"],
        "tax_classes": plan["semantic_state"]["tax_classes"],
        "versions": plan["semantic_state"]["versions"],
    }
    if (not isinstance(plan["full_staged_state_sha256"], str)
            or not HEX64.fullmatch(plan["full_staged_state_sha256"])
            or plan["full_staged_state_sha256"] != full_snapshot_fingerprint(staged_snapshot)):
        raise TaxDeliveryError("Plan full staged semantic-state fingerprint changed.")
    try:
        created = datetime.fromisoformat(plan["created_utc"])
        expires = datetime.fromisoformat(plan["expires_utc"])
    except (TypeError, ValueError) as exc:
        raise TaxDeliveryError("Plan timestamps are invalid.") from exc
    now = utc_now()
    if (created.tzinfo is None or expires.tzinfo is None
            or created.utcoffset() != timedelta(0) or expires.utcoffset() != timedelta(0)
            or expires - created != timedelta(hours=PLAN_LIFETIME_HOURS)
            or created > now + timedelta(minutes=1) or now >= expires):
        raise TaxDeliveryError("Plan timing is invalid or expired.")
    if not isinstance(plan["nonce"], str) or not NONCE32.fullmatch(plan["nonce"]):
        raise TaxDeliveryError("Plan nonce is invalid.")
    plan["sha256"] = saved
    return plan


def lock_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".commit-lock.json")


def require_exact_approval(value: Any) -> None:
    if not isinstance(value, str):
        raise TaxDeliveryError("Rachad must approve with byte-exact APPROVED.")
    try:
        supplied = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:  # pragma: no cover
        raise TaxDeliveryError("Rachad must approve with byte-exact APPROVED.") from exc
    if not secrets.compare_digest(supplied, APPROVAL_BYTES):
        raise TaxDeliveryError("Approval must be the unpadded uppercase byte-exact word APPROVED.")


def validate_write_entry(entry: Any, sequence: int) -> tuple[int, str, dict[str, Any]]:
    expected = write_contract()[sequence - 1]
    if not isinstance(entry, dict) or entry != expected:
        raise TaxDeliveryError(f"Plan write {sequence} differs from fixed contract.")
    rate_id = strict_rate_id(entry["rate_id"], target=True)
    endpoint = f"/taxes/{rate_id}"
    validate_route("PUT", endpoint)
    return rate_id, endpoint, validate_payload(rate_id, entry["payload"])


def validate_put_response(value: Any, expected_id: int) -> None:
    if not isinstance(value, dict) or type(value.get("id")) is not int or value["id"] != expected_id:
        raise TaxDeliveryError(f"PUT response did not identify exact canonical tax rate {expected_id}.")


def full_address(place: dict[str, str]) -> dict[str, str]:
    if (set(place) != {"label", "city", "state", "postcode", "country"}
            or place["country"] != "CA" or place not in PROVINCE_PLACES):
        raise TaxDeliveryError("Probe address is outside the fixed Canadian address set.")
    return {
        "first_name": "Tax", "last_name": "Verification", "company": "",
        "address_1": "", "address_2": "", "city": place["city"], "state": place["state"],
        "postcode": place["postcode"], "country": "CA", "email": "", "phone": "",
    }


def store_request(opener: Any, method: str, endpoint: str, *, token: str | None = None,
                  payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], str | None, int]:
    validate_store_route(method, endpoint)
    if method == "GET" and payload is not None:
        raise TaxDeliveryError("Anonymous Store API GET refuses a payload.")
    if method == "POST" and payload is None:
        raise TaxDeliveryError("Anonymous Store API POST requires fixed cart payload.")
    headers = {"Accept": "application/json", "User-Agent": "FRPDepot-Tax-Delivery-Verify/2.0"}
    if token:
        headers["Cart-Token"] = token
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    request = Request(STORE_BASE + endpoint, data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=STORE_REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read()
            value = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(value, dict):
                raise TaxDeliveryError("Anonymous Store API returned a non-object.")
            return value, response.headers.get("Cart-Token"), int(response.status)
    except HTTPError as exc:
        raise TaxDeliveryError(f"Anonymous Store API {method} {endpoint} failed with HTTP {exc.code}.") from exc
    except URLError as exc:
        raise TaxDeliveryError("Anonymous Store API network failure.") from exc
    except json.JSONDecodeError as exc:
        raise TaxDeliveryError("Anonymous Store API returned invalid JSON.") from exc


def decimal_from(value: Any, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise TaxDeliveryError(f"{label} is missing or invalid.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TaxDeliveryError(f"{label} is not decimal.") from exc
    if not result.is_finite():
        raise TaxDeliveryError(f"{label} is not finite.")
    return result


def minor_amount(value: Any, minor_unit: int, label: str) -> Decimal:
    if type(minor_unit) is not int or minor_unit != 2:
        raise TaxDeliveryError("Cart currency minor unit must be canonical CAD cents (2).")
    amount = decimal_from(value, label) / Decimal("100")
    if amount < 0:
        raise TaxDeliveryError(f"{label} must not be negative.")
    return amount.quantize(MONEY_QUANTUM)


def province_rates(rows: list[dict[str, Any]], state: str) -> list[dict[str, Any]]:
    matches = [row for row in rows if row["country"] == "CA" and row["state"] == state
               and row["class"] == "standard"]
    matches.sort(key=lambda row: (row["priority"], row["order"], row["id"]))
    if not matches or any(row["shipping"] is not True or row["compound"] is not False for row in matches):
        raise TaxDeliveryError(f"Province {state} lacks exact non-compound shipping-enabled rates.")
    priorities = [row["priority"] for row in matches]
    if priorities != list(range(1, len(matches) + 1)):
        raise TaxDeliveryError(f"Province {state} priorities are not exact sequential components.")
    if state == "QC" and [row["name"] for row in matches] != ["GST (5%)", "QST (9.975%)"]:
        raise TaxDeliveryError("Quebec labels must be exact GST/QST, never GST/PST.")
    return matches


def expected_tax(amount: Decimal, rates: list[dict[str, Any]]) -> tuple[Decimal, list[dict[str, str]]]:
    total = Decimal("0.00")
    components: list[dict[str, str]] = []
    for row in rates:
        rate = decimal_from(row["rate"], f"tax rate {row['id']}")
        component = (amount * rate / Decimal("100")).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        total += component
        components.append({
            "rate_id": str(row["id"]), "name": row["name"],
            "rate_percent": format(rate, "f"), "expected_cad": format(component, ".2f"),
        })
    return total.quantize(MONEY_QUANTUM), components


def selected_ups_rate(cart: dict[str, Any], unit: int) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    for package in cart.get("shipping_rates") or []:
        if not isinstance(package, dict):
            raise TaxDeliveryError("Cart shipping package is malformed.")
        for rate in package.get("shipping_rates") or []:
            if isinstance(rate, dict) and rate.get("selected") is True:
                selected.append(rate)
    if len(selected) != 1 or selected[0].get("method_id") != "ups":
        raise TaxDeliveryError("Cart must have exactly one selected UPS rate.")
    row = selected[0]
    return {
        "rate_id": row.get("rate_id"), "name": row.get("name"), "method_id": "ups",
        "price": minor_amount(row.get("price"), unit, "selected UPS price"),
        "tax": minor_amount(row.get("taxes"), unit, "selected UPS tax"),
    }


def validate_optional_tax_lines(cart: dict[str, Any], rates: list[dict[str, Any]], unit: int,
                                item_amount: Decimal, shipping_amount: Decimal) -> str:
    lines = cart.get("tax_lines")
    if lines in (None, []):
        return "authenticated_exact_tax_rate_rows"
    if not isinstance(lines, list) or len(lines) != len(rates):
        raise TaxDeliveryError("Store API tax-line component count differs from configured province rates.")
    for line, rate in zip(lines, rates):
        if not isinstance(line, dict) or line.get("name") != rate["name"]:
            raise TaxDeliveryError("Store API tax-line label differs from configured GST/HST/PST/QST label.")
        if decimal_from(line.get("rate"), "Store API tax-line rate") != decimal_from(rate["rate"], "configured rate"):
            raise TaxDeliveryError("Store API tax-line percent differs from configured component.")
        item_part = expected_tax(item_amount, [rate])[0]
        ship_part = expected_tax(shipping_amount, [rate])[0]
        if minor_amount(line.get("price"), unit, "Store API tax-line price") != item_part + ship_part:
            raise TaxDeliveryError("Store API tax-line amount is not component-rounded item plus shipping tax.")
    return "store_api_tax_lines_and_authenticated_exact_tax_rate_rows"


def verify_cart(case: dict[str, Any], cart: dict[str, Any], rows: list[dict[str, Any]],
                statuses: list[int]) -> dict[str, Any]:
    if case not in PROBE_CASES or statuses != [200, 201, 200, 200]:
        raise TaxDeliveryError("Cart case/status contract is not exact.")
    if cart.get("errors") not in (None, []):
        raise TaxDeliveryError("Anonymous cart contains Store API errors.")
    totals = cart.get("totals")
    if not isinstance(totals, dict) or totals.get("currency_code") != "CAD":
        raise TaxDeliveryError("Verification cart is not CAD.")
    unit = totals.get("currency_minor_unit")
    if type(unit) is not int:
        raise TaxDeliveryError("Currency minor unit is not canonical integer.")
    items = cart.get("items")
    if (not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict)
            or items[0].get("id") != STORE_VARIATION_ID or items[0].get("quantity") != 1):
        raise TaxDeliveryError("Cart must contain only fixed variation 2028 quantity 1.")
    for label, live, expected in (
        ("delivery", cart.get("shipping_address") or {}, case["delivery"]),
        ("billing", cart.get("billing_address") or {}, case["billing"]),
    ):
        if not isinstance(live, dict) or any(live.get(field) != expected[field]
                                             for field in ("city", "state", "postcode", "country")):
            raise TaxDeliveryError(f"Cart {label} address differs from fixed case.")
    item_amount = minor_amount(totals.get("total_items"), unit, "merchandise amount")
    item_tax = minor_amount(totals.get("total_items_tax"), unit, "merchandise tax")
    shipping_amount = minor_amount(totals.get("total_shipping"), unit, "shipping amount")
    shipping_tax = minor_amount(totals.get("total_shipping_tax"), unit, "shipping tax")
    total_tax = minor_amount(totals.get("total_tax"), unit, "total tax")
    if item_amount <= 0 or shipping_amount <= 0 or shipping_tax <= 0:
        raise TaxDeliveryError("Merchandise, UPS shipping, and shipping tax must be positive.")
    item_totals = items[0].get("totals") or {}
    if (minor_amount(item_totals.get("line_total"), unit, "line amount") != item_amount
            or minor_amount(item_totals.get("line_total_tax"), unit, "line tax") != item_tax):
        raise TaxDeliveryError("Single item line totals differ from cart merchandise totals.")
    ups = selected_ups_rate(cart, unit)
    if ups["price"] != shipping_amount or ups["tax"] != shipping_tax:
        raise TaxDeliveryError("Selected UPS tax/amount differs from cart shipping totals.")
    rates = province_rates(rows, case["delivery"]["state"])
    expected_item, item_components = expected_tax(item_amount, rates)
    expected_shipping, shipping_components = expected_tax(shipping_amount, rates)
    if item_tax != expected_item or shipping_tax != expected_shipping:
        raise TaxDeliveryError("Item or shipping tax does not equal delivery component-rounded live rates.")
    if total_tax != (item_tax + shipping_tax).quantize(MONEY_QUANTUM):
        raise TaxDeliveryError("Total tax differs from item plus shipping tax.")
    labels_source = validate_optional_tax_lines(cart, rates, unit, item_amount, shipping_amount)
    cross_proof = None
    if case["billing"]["state"] != case["delivery"]["state"]:
        billing_rates = province_rates(rows, case["billing"]["state"])
        billing_item = expected_tax(item_amount, billing_rates)[0]
        billing_shipping = expected_tax(shipping_amount, billing_rates)[0]
        if item_tax == billing_item or shipping_tax == billing_shipping:
            raise TaxDeliveryError("Cross-address cart does not distinguish delivery tax from billing tax.")
        cross_proof = {"actual_matches_delivery_not_billing": True,
                       "billing_state": case["billing"]["state"]}
    return {
        "label": case["label"], "billing_state": case["billing"]["state"],
        "delivery_state": case["delivery"]["state"], "http_statuses": statuses,
        "variation_id": STORE_VARIATION_ID,
        "merchandise_cad": format(item_amount, ".2f"),
        "merchandise_tax_live_cad": format(item_tax, ".2f"),
        "merchandise_tax_expected_cad": format(expected_item, ".2f"),
        "shipping_cad": format(shipping_amount, ".2f"),
        "shipping_tax_live_cad": format(shipping_tax, ".2f"),
        "shipping_tax_expected_cad": format(expected_shipping, ".2f"),
        "delivery_rate_ids": [row["id"] for row in rates],
        "delivery_rate_labels": [row["name"] for row in rates],
        "labels_verified_from": labels_source,
        "item_tax_components": item_components, "shipping_tax_components": shipping_components,
        "cross_address_proof": cross_proof,
        "checkout_submitted": False, "order_created": False, "payment_created": False,
    }


StoreRequester = Callable[..., tuple[dict[str, Any], str | None, int]]


def probe_case(case: dict[str, Any], rows: list[dict[str, Any]],
               requester: StoreRequester = store_request) -> dict[str, Any]:
    if case not in PROBE_CASES:
        raise TaxDeliveryError("Probe case is outside fixed 15-case set.")
    opener = build_opener(HTTPCookieProcessor(CookieJar()), wc.NoRedirectHandler())
    _, token, status1 = requester(opener, "GET", "/cart")
    if not isinstance(token, str) or not token:
        raise TaxDeliveryError("Fresh anonymous cart omitted Cart-Token.")
    _, token2, status2 = requester(
        opener, "POST", "/cart/add-item", token=token,
        payload={"id": STORE_VARIATION_ID, "quantity": 1},
    )
    token = token2 or token
    _, token2, status3 = requester(
        opener, "POST", "/cart/update-customer", token=token,
        payload={"billing_address": full_address(case["billing"]),
                 "shipping_address": full_address(case["delivery"])},
    )
    token = token2 or token
    cart, _, status4 = requester(opener, "GET", "/cart", token=token)
    return verify_cart(case, cart, rows, [status1, status2, status3, status4])


def run_store_probes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    validate_tax_collection(rows, expected=target_projection())
    results = [probe_case(case, rows) for case in PROBE_CASES]
    if len(results) != 15 or not all(row.get("order_created") is False for row in results):
        raise TaxDeliveryError("Exact 15-cart verification did not complete safely.")
    return {
        "status": "ALL_13_DESTINATIONS_AND_2_CROSS_CASES_VERIFIED",
        "case_count": 15, "request_count": 60,
        "checkout_submitted": False, "orders_created": 0,
        "payments_created": 0, "emails_sent": 0, "cases": results,
    }


def command_stage(_: argparse.Namespace) -> None:
    validate_diagnosis_evidence()
    vault = wc.load_vault()
    validate_vault(vault)
    first = semantic_snapshot(vault, expected_rows=list(EXPECTED_BEFORE_ROWS))
    # A second complete fresh GET-only snapshot proves no live drift/change across staging.
    second = semantic_snapshot(vault, expected_rows=list(EXPECTED_BEFORE_ROWS))
    if first != second:
        raise TaxDeliveryError("Post-stage fresh GET-only semantic snapshot drifted; no plan was written.")
    path, summary_path = stage_plan(first)
    plan = read_json(path)
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED_GET_ONLY",
        "external_write_performed": False, "live_store_changes": 0,
        "plan": str(path), "semantic_plan_sha256": plan["sha256"],
        "physical_plan_file_sha256": file_sha256(path), "expires_utc": plan["expires_utc"],
        "summary": str(summary_path), "summary_file_sha256": file_sha256(summary_path),
        "tax_table_fingerprint": plan["tax_rates_before"]["aggregate_sha256"],
        "semantic_fingerprint": plan["semantic_state"]["fingerprints"]["aggregate_sha256"],
        "full_staged_state_sha256": plan["full_staged_state_sha256"],
        "fixed_write_order_if_later_approved": list(WRITE_ORDER),
        "write_count_if_later_approved": 17, "approval": "APPROVED",
        "commissioning_is_not_commit_approval": True,
        "stage_snapshot_count": 2,
        "stage_authenticated_get_routes_per_snapshot": authenticated_get_contract(),
        "stage_authenticated_get_count": 2 * len(authenticated_get_contract()),
        "stage_store_api_cart_requests": 0, "tax_or_store_puts": 0,
        "persistent_store_posts": 0, "orders_created": 0, "payments_created": 0,
        "emails_sent": 0,
    }, indent=2, ensure_ascii=False))


def _lock_record(plan: dict[str, Any], **updates: Any) -> dict[str, Any]:
    return {
        "plan_sha256": plan["sha256"], "tool_version": TOOL_VERSION,
        "schema_version": SCHEMA_VERSION, "attempt": 1, "write_count": 17,
        "non_atomic": True, "replay_locked_forever": True,
        "retry": False, "rollback": False, "resume": False, "cleanup": False,
        **updates,
    }


def command_commit(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve()
    try:
        plan_path.relative_to(PLAN_DIR.resolve())
    except ValueError as exc:
        raise TaxDeliveryError("Plan must be inside fixed delivery-tax plan folder.") from exc
    # Loading first makes v1/schema-1 plans unreachable before approval, mutex,
    # vault, credentials, network, or any permanent attempt lock.
    plan = load_plan(plan_path)
    require_exact_approval(args.approval)
    lock = lock_path(plan_path)
    if lock.exists():
        raise TaxDeliveryError("This plan already entered commit and cannot be replayed.")

    purpose = f"tax delivery plan {plan['sha256'][:16]}"
    with tax_delivery_commit_lock(purpose=purpose, wait_seconds=0):
        # Recheck after global acquisition: another prior holder may have used this plan.
        if lock.exists():
            raise TaxDeliveryError("This plan already entered commit and cannot be replayed.")
        validate_diagnosis_evidence()
        vault = wc.load_vault()
        validate_vault(vault)
        preflight = semantic_snapshot(vault, expected_rows=list(EXPECTED_BEFORE_ROWS))
        assert_snapshot_matches_plan(plan, preflight, 0)

        attempted_count = 0
        successful_count = 0
        verified_count = 0
        attempted_targets: list[int] = []
        phase = "permanent_attempt_lock"
        current_target: int | None = None
        write_json(lock, _lock_record(
            plan, status="in_flight", started_utc=utc_now().isoformat(), phase=phase,
            attempted_count=0, http_successful_count=0, fresh_get_verified_count=0,
            attempted_targets=[], current_target=None,
            preflight_tax_sha256=full_fingerprint(preflight["tax_rates"]),
            preflight_semantic_sha256=semantic_fingerprints(preflight)["aggregate_sha256"],
        ), exclusive=True)

        try:
            for sequence, entry in enumerate(plan["transaction"]["writes"], start=1):
                rate_id, endpoint, payload = validate_write_entry(entry, sequence)
                current_target = rate_id
                attempted_count = sequence
                attempted_targets.append(rate_id)
                phase = f"before_put_{sequence}_of_17_rate_{rate_id}"
                # Persist target/count BEFORE the only request invocation.
                write_json(lock, _lock_record(
                    plan, status="in_flight", updated_utc=utc_now().isoformat(), phase=phase,
                    attempted_count=attempted_count, http_successful_count=successful_count,
                    fresh_get_verified_count=verified_count,
                    attempted_targets=attempted_targets, current_target=current_target,
                ))
                response, _ = wc.api_request("PUT", endpoint, payload=payload, vault=vault)
                successful_count = sequence
                phase = f"after_http_success_{sequence}_of_17_rate_{rate_id}"
                write_json(lock, _lock_record(
                    plan, status="in_flight", updated_utc=utc_now().isoformat(), phase=phase,
                    attempted_count=attempted_count, http_successful_count=successful_count,
                    fresh_get_verified_count=verified_count,
                    attempted_targets=attempted_targets, current_target=current_target,
                ))
                validate_put_response(response, rate_id)
                phase = f"fresh_semantic_prefix_readback_{sequence}_of_17_rate_{rate_id}"
                current = semantic_snapshot(vault, expected_rows=prefix_projection(sequence))
                assert_snapshot_matches_plan(plan, current, sequence)
                verified_count = sequence
                write_json(lock, _lock_record(
                    plan, status="in_flight", updated_utc=utc_now().isoformat(), phase=phase,
                    attempted_count=attempted_count, http_successful_count=successful_count,
                    fresh_get_verified_count=verified_count,
                    attempted_targets=attempted_targets, current_target=current_target,
                    last_verified_tax_sha256=full_fingerprint(current["tax_rates"]),
                ))

            phase = "fifteen_isolated_anonymous_store_api_carts"
            probe_evidence = run_store_probes(current["tax_rates"])
            phase = "post_cart_protected_semantic_readback"
            protected_after_carts = semantic_snapshot(vault, expected_rows=target_projection())
            assert_snapshot_matches_plan(plan, protected_after_carts, 17)
        except Exception as exc:
            postwrite_validation = phase.startswith("fifteen_") or phase.startswith("post_cart_")
            status = "failed_permanently_locked" if postwrite_validation else "indeterminate_permanently_locked"
            reason = {
                "reason_class": type(exc).__name__,
                "http_status": exc.status if isinstance(exc, wc.WooError) else None,
                "rest_code": exc.code if isinstance(exc, wc.WooError) else None,
            }
            write_json(lock, _lock_record(
                plan, status=status, updated_utc=utc_now().isoformat(), phase=phase,
                attempted_count=attempted_count, http_successful_count=successful_count,
                fresh_get_verified_count=verified_count, attempted_targets=attempted_targets,
                current_target=current_target, writes_may_be_live=attempted_count > 0, **reason,
            ))
            wc.append_receipt(
                "woocommerce_tax_delivery_v2_failed_or_indeterminate_no_retry",
                f"plan={plan_path}; sha256={plan['sha256']}; phase={phase}; "
                f"attempted={attempted_count}; http_successful={successful_count}; "
                f"fresh_get_verified={verified_count}; status={status}; reason_class={reason['reason_class']}",
            )
            raise TaxDeliveryError(
                "Delivery-tax commit stopped after permanent lock. Earlier PUTs may be live; "
                "this plan will never retry, roll back, resume, delete, or clean up."
            ) from exc

        outcome = {
            "attempted_count": 17, "http_successful_count": 17, "fresh_get_verified_count": 17,
            "fixed_write_order": list(WRITE_ORDER), "shipping_enabled_count": 17,
            "renamed_rate_ids": {"3": NAME_CHANGES[3], "17": NAME_CHANGES[17]},
            "protected_rate_1_preserved": True, "protected_fields_preserved": True,
            "settings_classes_versions_preserved": True,
            "anonymous_cart_verification": probe_evidence,
            "post_cart_tax_sha256": full_fingerprint(protected_after_carts["tax_rates"]),
            "orders_created": 0, "payments_created": 0, "emails_sent": 0,
        }
        write_json(lock, _lock_record(
            plan, status="committed_verified", updated_utc=utc_now().isoformat(),
            phase="complete", attempted_count=17, http_successful_count=17,
            fresh_get_verified_count=17, attempted_targets=list(WRITE_ORDER),
            current_target=None, outcome=outcome,
        ))
        wc.append_receipt(
            "woocommerce_tax_delivery_v2_committed_and_verified",
            f"plan={plan_path}; sha256={plan['sha256']}; attempted=17; successful=17; "
            "verified=17; cart_cases=15; orders_created=0",
        )
        print(json.dumps({
            "status": "COMMITTED_AND_VERIFIED", "plan_sha256": plan["sha256"],
            "replay_locked": True, "attempts": 1, **outcome,
        }, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage", help="GET-only stage of fixed correction")
    stage.set_defaults(func=command_stage)
    commit = commands.add_parser("commit", help="commit one later byte-exact approved plan once")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (TaxDeliveryError, TaxDeliveryCommitBusy, TaxDeliveryCommitLockError,
            wc.WooError, OSError, ValueError) as exc:
        print("ERROR: " + wc.scrub(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
