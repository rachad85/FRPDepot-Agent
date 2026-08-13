#!/usr/bin/env python
"""Approval-gated final publication recovery for WooCommerce FNPT parent 2061.

This build exposes exactly one business write: ``PUT /products/2061`` with the
literal payload ``{"status": "publish"}``. Stage is read-only. It proves the
locked predecessor's 31 variation writes and timed-out gallery request landed
exactly, verifies all 64 WooCommerce quantities against Zoho physical stock,
downloads and hashes the six live same-origin gallery images, then writes a
24-hour immutable plan. Commit repeats the complete preflight before its replay
lock, attempts the one publication PUT once, and fresh-verifies the final state.
There is no variation, gallery, price, stock, delete, order, customer, mail, or
browser write route in this build.
"""
from __future__ import annotations

import argparse
import copy
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
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import woocommerce_common as wc

ZOHO_TOOL_DIR = Path(r"C:\FRPDepot\Dado\Tools\zoho")
if str(ZOHO_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(ZOHO_TOOL_DIR))
import zoho_tool

TOOL_NAME = "FRP Depot Fixed WooCommerce FNPT Final Publication Recovery Tool"
TOOL_VERSION = "4.0.0"
SCHEMA_VERSION = 5
APPROVAL_WORD = "APPROVED"
PLAN_LIFETIME_HOURS = 24
EXACT_ORIGIN = wc.ALLOWED_ORIGIN
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "woocommerce_fnpt_go_live_plans"
PARENT_ID = 2061
PARENT_NAME = "FNPT Coupling, Threaded On both ends"
PARENT_SKU = "ZOHO-GROUP-5961888EC5DB"
PARENT_TYPE = "variable"
EXPECTED_VARIATION_COUNT = 64
SUPPORTED_COUNT = 32
UNSUPPORTED_COUNT = 32
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_IMAGE_BYTES = 20 * 1024 * 1024

IMAGE_DIR = (
    ROOT / "Dado" / "20_Working" / "pricing_requests" / "fnpt"
    / "original_product_images_20260811"
)
IMAGE_MANIFEST = IMAGE_DIR / "manifest.json"
IMAGE_MANIFEST_SHA256 = "1b2ae0293858d6b473a397a35ecfbdb592b8a5b7563951604a4d37cb44fb4d04"
IMAGE_SPECS: tuple[dict[str, Any], ...] = (
    {"order": 1, "file": str(IMAGE_DIR / "01_fnpt_family_hero.png"),
     "sha256": "ff928a5c99fbdbbb01cbd0a03b983eff00ceecabcebd2a86d562f107f5a9bacd",
     "alt": "FRP female NPT couplings in three sizes"},
    {"order": 2, "file": str(IMAGE_DIR / "02_fnpt_single_detail.png"),
     "sha256": "5aeaaa394668a1e3f93ad8508ceebc8dc055c2cb40239422b9321ec5f5a304b9",
     "alt": "FRP female NPT coupling threaded detail"},
    {"order": 3, "file": str(IMAGE_DIR / "03_fnpt_three_size_top_view.png"),
     "sha256": "489cd7ef23e424d9dcc0cdf1d28004eb72a4c4b8ffa24e581e624e725491e168",
     "alt": "Three FRP female NPT coupling sizes, top view"},
    {"order": 4, "file": str(IMAGE_DIR / "04_fnpt_horizontal_through_bore.png"),
     "sha256": "963fd65885cff24655bcc14b63bb992828ecce354a115ced957de58aa819c6cc",
     "alt": "Horizontal FRP female NPT coupling with internal threads"},
    {"order": 5, "file": str(IMAGE_DIR / "05_fnpt_upright_thread_detail.png"),
     "sha256": "1ea905d1b8dbf2ca08679838ca0a9d67a8602f774cf219887622917ea5960693",
     "alt": "Upright FRP female NPT coupling with internal threads"},
    {"order": 6, "file": str(IMAGE_DIR / "06_fnpt_two_size_comparison.png"),
     "sha256": "31e6a9e456319d30b8a5a49c94d3eca7c23d2f782dece81996a03e21785e855e",
     "alt": "Two FRP female NPT coupling sizes"},
)

EXPECTED_VARIATIONS: tuple[tuple[int, str], ...] = (
    (2062, 'FNPTCOUPLING-DERAKANE470-1/2"6"'),
    (2063, 'FNPTCOUPLING-DERAKANE470-1/2"8"'),
    (2064, 'FNPTCOUPLING-DERAKANE470-1"6"'),
    (2065, 'FNPTCOUPLING-DERAKANE470-1"8"'),
    (2066, 'FNPTCOUPLING-DERAKANE470-1-1/4"6"'),
    (2067, 'FNPTCOUPLING-DERAKANE470-1-1/4"8"'),
    (2068, 'FNPTCOUPLING-DERAKANE470-1-1/2"6"'),
    (2069, 'FNPTCOUPLING-DERAKANE470-1-1/2"8"'),
    (2070, 'FNPTCOUPLING-DERAKANE470-2"6"'),
    (2071, 'FNPTCOUPLING-DERAKANE470-2"8"'),
    (2072, 'FNPTCOUPLING-DERAKANE470-3"6"'),
    (2073, 'FNPTCOUPLING-DERAKANE470-3"8"'),
    (2074, 'FNPTCOUPLING-DERAKANE470-3/4"6"'),
    (2075, 'FNPTCOUPLING-DERAKANE470-3/4"8"'),
    (2076, 'FNPTCOUPLING-DERAKANE470-6"6"'),
    (2077, 'FNPTCOUPLING-DERAKANE470-6"8"'),
    (2078, 'FNPTCOUPLING-DERAKANE411-1/2"6"'),
    (2079, 'FNPTCOUPLING-DERAKANE411-1/2"8"'),
    (2080, 'FNPTCOUPLING-DERAKANE411-1"6"'),
    (2081, 'FNPTCOUPLING-DERAKANE411-1"8"'),
    (2082, 'FNPTCOUPLING-DERAKANE411-1-1/4"6"'),
    (2083, 'FNPTCOUPLING-DERAKANE411-1-1/4"8"'),
    (2084, 'FNPTCOUPLING-DERAKANE411-1-1/2"6"'),
    (2085, 'FNPTCOUPLING-DERAKANE411-1-1/2"8"'),
    (2086, 'FNPTCOUPLING-DERAKANE411-2"6"'),
    (2087, 'FNPTCOUPLING-DERAKANE411-2"8"'),
    (2088, 'FNPTCOUPLING-DERAKANE411-3"6"'),
    (2089, 'FNPTCOUPLING-DERAKANE411-3"8"'),
    (2090, 'FNPTCOUPLING-DERAKANE411-3/4"6"'),
    (2091, 'FNPTCOUPLING-DERAKANE411-3/4"8"'),
    (2092, 'FNPTCOUPLING-DERAKANE411-6"6"'),
    (2093, 'FNPTCOUPLING-DERAKANE411-6"8"'),
    (2094, 'FNPTCOUPLING-HETRON9221/2"6"'),
    (2095, 'FNPTCOUPLING-HETRON9221/2"8"'),
    (2096, 'FNPTCOUPLING-HETRON9221"6"'),
    (2097, 'FNPTCOUPLING-HETRON9221"8"'),
    (2098, 'FNPTCOUPLING-HETRON9221-1/4"6"'),
    (2099, 'FNPTCOUPLING-HETRON9221-1/4"8"'),
    (2100, 'FNPTCOUPLING-HETRON9221-1/2"6"'),
    (2101, 'FNPTCOUPLING-HETRON9221-1/2"8"'),
    (2102, 'FNPTCOUPLING-HETRON9222"6"'),
    (2103, 'FNPTCOUPLING-HETRON9222"8"'),
    (2104, 'FNPTCOUPLING-HETRON9223"6"'),
    (2105, 'FNPTCOUPLING-HETRON9223"8"'),
    (2106, 'FNPTCOUPLING-HETRON9223/4"6"'),
    (2107, 'FNPTCOUPLING-HETRON9223/4"8"'),
    (2108, 'FNPTCOUPLING-HETRON9226"6"'),
    (2109, 'FNPTCOUPLING-HETRON9226"8"'),
    (2110, 'FNPTCOUPLING-DERAKANE510A1/2"6"'),
    (2111, 'FNPTCOUPLING-DERAKANE510A1/2"8"'),
    (2112, 'FNPTCOUPLING-DERAKANE510A1"6"'),
    (2113, 'FNPTCOUPLING-DERAKANE510A1"8"'),
    (2114, 'FNPTCOUPLING-DERAKANE510A1-1/4"6"'),
    (2115, 'FNPTCOUPLING-DERAKANE510A1-1/4"8"'),
    (2116, 'FNPTCOUPLING-DERAKANE510A1-1/2"6"'),
    (2117, 'FNPTCOUPLING-DERAKANE510A1-1/2"8"'),
    (2118, 'FNPTCOUPLING-DERAKANE510A2"6"'),
    (2119, 'FNPTCOUPLING-DERAKANE510A2"8"'),
    (2120, 'FNPTCOUPLING-DERAKANE510A3"6"'),
    (2121, 'FNPTCOUPLING-DERAKANE510A3"8"'),
    (2122, 'FNPTCOUPLING-DERAKANE510A3/4"6"'),
    (2123, 'FNPTCOUPLING-DERAKANE510A3/4"8"'),
    (2124, 'FNPTCOUPLING-DERAKANE510A6"6"'),
    (2125, 'FNPTCOUPLING-DERAKANE510A6"8"'),
)
SUPPORTED_PRICES: dict[int, str] = {
    2062: "37.44", 2063: "44.64", 2064: "47.16", 2065: "49.32",
    2066: "59.04", 2067: "74.52", 2068: "59.04", 2069: "74.82",
    2070: "81.00", 2071: "93.54", 2072: "102.89", 2073: "112.24",
    2074: "44.28", 2075: "46.08", 2076: "182.52", 2077: "187.06",
    2078: "23.40", 2079: "40.32", 2080: "35.08", 2081: "42.48",
    2082: "37.05", 2083: "46.76", 2084: "37.05", 2085: "46.76",
    2086: "50.65", 2087: "58.46", 2088: "64.31", 2089: "70.15",
    2090: "33.13", 2091: "41.04", 2092: "114.14", 2093: "116.91",
}
UNSUPPORTED_IDS = frozenset(range(2094, 2126))

LOCKED_PLAN = PLAN_DIR / "20260812T160249Z_fnpt_2061_a16c66328ea9b903.json"
LOCKED_PLAN_FILE_SHA256 = "b949632ff6270ee09210d8ea002489b170aa6b56194315c5ce11611bce5207e1"
LOCKED_PLAN_DECLARED_SHA256 = "a16c66328ea9b903d0d4d564cda2ea55abbe377f731229bc09b61631b5a275d3"
LOCKED_LOCK = LOCKED_PLAN.with_suffix(".commit-lock.json")
LOCKED_LOCK_SHA256 = "73ef87415626c150b5b9523c540c4f3ce39b07448b1778dc83b14039f44ff22d"
RECONCILIATION = PLAN_DIR / "20260812T160249Z_fnpt_2061_a16c66328ea9b903_readonly_reconciliation.json"
RECONCILIATION_SHA256 = "477b43fb36689f22e1bc972c4d586e9b952077763351831067e3d10c155c1c3d"
BUSINESS_RECONCILIATION = PLAN_DIR / "20260812T160249Z_fnpt_2061_a16c66328ea9b903_business_field_reconciliation.json"
BUSINESS_RECONCILIATION_SHA256 = "83036866d96879c025ae9bb6229c0db650abe54d62c11de672cf04ce3ba82cc9"

# These are the exact predecessor projections. Images are checked separately;
# parent status and documented WooCommerce-derived values are the only allowed
# changes around publication.
VARIATION_ALLOWED_OR_DERIVED = frozenset({
    "status", "regular_price", "price", "purchasable", "on_sale", "permalink",
    "date_modified", "date_modified_gmt",
})
PARENT_ALLOWED_OR_DERIVED = frozenset({
    "images", "status", "price", "price_html", "purchasable", "on_sale", "permalink",
    "date_modified", "date_modified_gmt", "yoast_head", "yoast_head_json", "variations",
})
STOCK_FIELDS = ("manage_stock", "stock_quantity", "stock_status")


class GoLiveError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_value(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def digest_for(core: dict[str, Any]) -> str:
    return digest_value(core)


def strict_positive_id(value: Any, *, label: str, expected: int | None = None) -> int:
    if type(value) is not int or value <= 0:
        raise GoLiveError(f"{label} must be a canonical positive integer.")
    if expected is not None and value != expected:
        raise GoLiveError(f"{label} is not the fixed ID {expected}.")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise GoLiveError(f"Required fixed evidence is unreadable: {path}") from exc
    return digest.hexdigest()


def read_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoLiveError(f"Plan/evidence JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise GoLiveError("Plan/evidence JSON must contain one object.")
    return value


def stock_projection(record: dict[str, Any]) -> dict[str, Any]:
    return {field: record.get(field) for field in STOCK_FIELDS}


def protected_fingerprint(record: dict[str, Any], *, parent: bool) -> str:
    excluded = PARENT_ALLOWED_OR_DERIVED if parent else VARIATION_ALLOWED_OR_DERIVED
    projection = {key: record.get(key) for key in sorted(record) if key not in excluded}
    return digest_value(projection)


def normalized_variation_protected_fingerprint(record: dict[str, Any]) -> str:
    normalized = copy.deepcopy(record)
    normalized["image"] = None
    return protected_fingerprint(normalized, parent=False)


def full_fingerprint(record: dict[str, Any]) -> str:
    return digest_value(record)


def validate_vault(vault: dict[str, Any]) -> None:
    if vault.get("declared_permissions") != "read_write":
        raise GoLiveError("Saved WooCommerce key is not declared Read/Write.")
    try:
        origin = wc.normalize_site_url(str(vault.get("site_url") or ""))
    except wc.WooError as exc:
        raise GoLiveError("Saved WooCommerce origin is not the exact FRP Depot origin.") from exc
    if origin != EXACT_ORIGIN:
        raise GoLiveError("Saved WooCommerce origin is not the exact FRP Depot origin.")


def validate_route(method: str, endpoint: str) -> None:
    pair = (str(method).upper(), endpoint)
    allowed = {
        ("GET", f"/products/{PARENT_ID}"),
        ("GET", f"/products/{PARENT_ID}/variations"),
        ("PUT", f"/products/{PARENT_ID}"),
    }
    if pair not in allowed:
        raise GoLiveError("REFUSED: method/path pair is outside the one-write publication recovery.")


def read_catalog(vault: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    parent_endpoint = f"/products/{PARENT_ID}"
    variations_endpoint = f"/products/{PARENT_ID}/variations"
    validate_route("GET", parent_endpoint)
    validate_route("GET", variations_endpoint)
    parent, _ = wc.api_get(parent_endpoint, vault=vault)
    variations = wc.get_all(
        variations_endpoint,
        {"status": "any", "orderby": "id", "order": "asc"},
        vault=vault,
        max_pages=2,
        max_items=EXPECTED_VARIATION_COUNT,
    )
    if not isinstance(parent, dict) or not all(isinstance(row, dict) for row in variations):
        raise GoLiveError("WooCommerce returned an unexpected FNPT catalog response.")
    return parent, variations


def stock_decimal(value: Any, *, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise GoLiveError(f"{label} is missing or invalid.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise GoLiveError(f"{label} is not a valid quantity.") from exc
    if not result.is_finite():
        raise GoLiveError(f"{label} is not a finite quantity.")
    return result


def read_zoho_physical_stock() -> dict[str, Decimal]:
    vault = zoho_tool.load_vault()
    token, vault = zoho_tool.refresh_access_token(vault)
    try:
        domain = str(vault["api_domain"])
        organization_id = str(vault["inventory_organization_id"])
    except KeyError as exc:
        raise GoLiveError("Saved Zoho Inventory connection is incomplete.") from exc
    rows: list[dict[str, Any]] = []
    for page in range(1, 101):
        query = urlencode({"organization_id": organization_id, "page": page, "per_page": 200})
        response = zoho_tool.api_get(token, domain, f"/inventory/v1/items?{query}")
        if not isinstance(response, dict) or not isinstance(response.get("items") or [], list):
            raise GoLiveError("Zoho Inventory returned an invalid item listing.")
        rows.extend(row for row in (response.get("items") or []) if isinstance(row, dict))
        if not (response.get("page_context") or {}).get("has_more_page"):
            break
    else:
        raise GoLiveError("Zoho Inventory pagination exceeded the fixed 100-page guard.")
    expected_skus = {sku for _, sku in EXPECTED_VARIATIONS}
    fnpt_rows = [row for row in rows if str(row.get("sku") or "").startswith("FNPTCOUPLING")]
    found_skus = [str(row.get("sku") or "") for row in fnpt_rows]
    if len(found_skus) != len(set(found_skus)):
        raise GoLiveError("Zoho Inventory returned duplicate FNPT SKUs.")
    if set(found_skus) != expected_skus or len(found_skus) != EXPECTED_VARIATION_COUNT:
        raise GoLiveError("Zoho Inventory FNPT SKU set is not the exact fixed 64-SKU set.")
    return {
        str(row["sku"]): stock_decimal(
            row.get("actual_available_stock"),
            label=f"Zoho physical availability for {row.get('sku')}",
        )
        for row in fnpt_rows
    }


def verify_zoho_physical_stock(variations: list[dict[str, Any]]) -> dict[str, Any]:
    rows: dict[int, dict[str, Any]] = {}
    expected = dict(EXPECTED_VARIATIONS)
    for row in variations:
        variation_id = strict_positive_id(row.get("id"), label="WooCommerce variation ID")
        if variation_id in rows:
            raise GoLiveError("WooCommerce returned a duplicate variation ID.")
        rows[variation_id] = row
    if set(rows) != set(expected) or len(variations) != EXPECTED_VARIATION_COUNT:
        raise GoLiveError("WooCommerce variation set is not the fixed 64 before stock preflight.")
    zoho_by_sku = read_zoho_physical_stock()
    fingerprint_rows: list[dict[str, str]] = []
    in_stock = 0
    for variation_id, sku in EXPECTED_VARIATIONS:
        row = rows[variation_id]
        if row.get("sku") != sku or row.get("manage_stock") is not True:
            raise GoLiveError(f"WooCommerce variation {variation_id} is not managed stock with its fixed SKU.")
        woo_qty = stock_decimal(row.get("stock_quantity"), label=f"WooCommerce stock quantity for {sku}")
        zoho_qty = zoho_by_sku[sku]
        if woo_qty != zoho_qty:
            raise GoLiveError(f"Physical-stock mismatch for {sku}: WooCommerce {woo_qty} vs Zoho {zoho_qty}.")
        normalized = format(zoho_qty, "f")
        fingerprint_rows.append({"variation_id": str(variation_id), "sku": sku, "quantity": normalized})
        if zoho_qty > 0:
            in_stock += 1
    return {
        "source": "Zoho Inventory actual_available_stock vs WooCommerce stock_quantity",
        "matched_count": EXPECTED_VARIATION_COUNT,
        "mismatch_count": 0,
        "in_stock_count": in_stock,
        "out_of_stock_count": EXPECTED_VARIATION_COUNT - in_stock,
        "stock_fingerprint": digest_value(fingerprint_rows),
    }


def load_locked_source_plan() -> dict[str, Any]:
    if (not LOCKED_PLAN.is_file()
            or not secrets.compare_digest(file_sha256(LOCKED_PLAN), LOCKED_PLAN_FILE_SHA256)):
        raise GoLiveError("Locked predecessor plan file hash mismatch.")
    value = read_json(LOCKED_PLAN)
    saved = value.pop("sha256", None)
    if (saved != LOCKED_PLAN_DECLARED_SHA256
            or not secrets.compare_digest(digest_for(value), LOCKED_PLAN_DECLARED_SHA256)):
        raise GoLiveError("Locked predecessor plan declared hash mismatch.")
    targets = value.get("variation_targets")
    if not isinstance(targets, list) or len(targets) != EXPECTED_VARIATION_COUNT:
        raise GoLiveError("Locked predecessor plan does not contain the exact 64 targets.")
    for target, (variation_id, sku) in zip(targets, EXPECTED_VARIATIONS):
        if (not isinstance(target, dict)
                or target.get("variation_id") != variation_id
                or target.get("sku") != sku
                or type(target.get("variation_id")) is not int):
            raise GoLiveError("Locked predecessor variation identity/order is not exact.")
        expected_supported = variation_id in SUPPORTED_PRICES
        if target.get("supported") is not expected_supported:
            raise GoLiveError("Locked predecessor support semantics changed.")
        expected_payload = (
            {"regular_price": SUPPORTED_PRICES[variation_id], "status": "publish"}
            if expected_supported else {"status": "private"}
        )
        if target.get("payload") != expected_payload:
            raise GoLiveError("Locked predecessor price/status semantics changed.")
        if not HEX64.fullmatch(str(target.get("before_protected_fingerprint") or "")):
            raise GoLiveError("Locked predecessor protected fingerprint is invalid.")
    value["sha256"] = saved
    return value


def validate_local_images() -> None:
    if (not IMAGE_MANIFEST.is_file()
            or not secrets.compare_digest(file_sha256(IMAGE_MANIFEST), IMAGE_MANIFEST_SHA256)):
        raise GoLiveError("Fixed six-image manifest hash mismatch.")
    for index, spec in enumerate(IMAGE_SPECS, start=1):
        if set(spec) != {"order", "file", "sha256", "alt"} or spec["order"] != index:
            raise GoLiveError("Fixed image specification shape/order is invalid.")
        path = Path(str(spec["file"])).resolve()
        try:
            path.relative_to(IMAGE_DIR.resolve())
        except ValueError as exc:
            raise GoLiveError("Fixed image file is outside the fixed image folder.") from exc
        if (path.suffix.casefold() != ".png" or not path.is_file()
                or not secrets.compare_digest(file_sha256(path), str(spec["sha256"]))):
            raise GoLiveError(f"Fixed image file hash mismatch at gallery position {index}.")


def validate_fixed_evidence() -> tuple[dict[str, Any], dict[str, Any]]:
    source_plan = load_locked_source_plan()
    for label, path, expected_hash in (
        ("permanent predecessor lock", LOCKED_LOCK, LOCKED_LOCK_SHA256),
        ("same-origin gallery reconciliation", RECONCILIATION, RECONCILIATION_SHA256),
        ("business-field reconciliation", BUSINESS_RECONCILIATION, BUSINESS_RECONCILIATION_SHA256),
    ):
        if not path.is_file() or not secrets.compare_digest(file_sha256(path), expected_hash):
            raise GoLiveError(f"Fixed {label} hash mismatch.")
    lock = read_json(LOCKED_LOCK)
    if (lock.get("plan_sha256") != LOCKED_PLAN_DECLARED_SHA256
            or lock.get("status") != "indeterminate"
            or lock.get("attempt") != 1
            or lock.get("phase") != "six_image_gallery_write"
            or lock.get("writes_completed") != 31
            or lock.get("reason_class") != "TimeoutError"):
        raise GoLiveError("Permanent predecessor lock no longer proves the exact timed-out state.")
    reconciliation = read_json(RECONCILIATION)
    parent = reconciliation.get("parent") or {}
    variations = reconciliation.get("variations") or {}
    stock = reconciliation.get("zoho_physical_stock_check") or {}
    gallery = reconciliation.get("gallery") or {}
    if (reconciliation.get("external_write_performed") is not False
            or parent.get("status") != "draft"
            or parent.get("visible_variation_ids_exact") is not True
            or variations.get("stock_mismatch_vs_plan_count") != 0
            or stock.get("matched_count") != 64
            or stock.get("mismatch_count") != 0
            or gallery.get("count") != 6
            or gallery.get("unique_positive_integer_ids") is not True
            or gallery.get("exact_by_same_origin_download_hash") is not True):
        raise GoLiveError("Pinned same-origin reconciliation is not the exact post-timeout state.")
    business = read_json(BUSINESS_RECONCILIATION)
    business_parent = business.get("parent") or {}
    business_variations = business.get("variation_business_fields") or {}
    if (business.get("external_write_performed") is not False
            or business_parent.get("status") != "draft"
            or business_parent.get("gallery_count") != 6
            or business_parent.get("visible_variation_ids") != sorted(SUPPORTED_PRICES)
            or business_variations.get("checked_count") != 64
            or business_variations.get("exact_count") != 64
            or business_variations.get("mismatch_count") != 0
            or business_variations.get("inherited_parent_lead_image_count") != 64
            or business_variations.get("inherited_parent_lead_image_ids") != list(range(2062, 2126))):
        raise GoLiveError("Pinned business-field reconciliation is not the exact final-draft state.")
    validate_local_images()
    return source_plan, reconciliation


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def validate_live_image_url(src: Any) -> str:
    if not isinstance(src, str) or not src:
        raise GoLiveError("Live gallery image source is missing.")
    parsed = urlparse(src)
    try:
        port = parsed.port
    except ValueError as exc:
        raise GoLiveError("Live gallery image source has an invalid port.") from exc
    if (parsed.scheme != "https" or parsed.hostname != "frpdepots.com"
            or parsed.username or parsed.password or port not in (None, 443)
            or parsed.query or parsed.fragment
            or not parsed.path.startswith("/wp-content/uploads/")
            or not parsed.path.casefold().endswith(".png")):
        raise GoLiveError("Live gallery image source is outside the fixed same-origin PNG shape.")
    return src


def download_live_image(src: str) -> dict[str, Any]:
    request = Request(src, headers={"User-Agent": "FRPDepot-Dado-FNPT-Verify/1.0"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().casefold()
            data = response.read(MAX_IMAGE_BYTES + 1)
    except Exception as exc:
        raise GoLiveError(f"Live gallery image download failed: {type(exc).__name__}.") from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise GoLiveError("Live gallery image exceeds the fixed 20 MiB bound.")
    if content_type != "image/png":
        raise GoLiveError("Live gallery image is not served as image/png.")
    return {"bytes": len(data), "content_type": content_type,
            "sha256": hashlib.sha256(data).hexdigest()}


def expected_gallery(reconciliation: dict[str, Any]) -> list[dict[str, Any]]:
    images = ((reconciliation.get("gallery") or {}).get("images") or [])
    if not isinstance(images, list) or len(images) != 6:
        raise GoLiveError("Pinned reconciliation gallery is not six records.")
    result: list[dict[str, Any]] = []
    for index, (row, spec) in enumerate(zip(images, IMAGE_SPECS), start=1):
        if not isinstance(row, dict):
            raise GoLiveError("Pinned reconciliation gallery row is invalid.")
        image_id = strict_positive_id(row.get("id"), label=f"Pinned gallery ID {index}")
        download = row.get("download") or {}
        expected = {
            "order": index,
            "id": image_id,
            "src": validate_live_image_url(row.get("src")),
            "name": row.get("name"),
            "alt": spec["alt"],
            "bytes": download.get("bytes"),
            "content_type": "image/png",
            "sha256": spec["sha256"],
        }
        if (row.get("position") != index or row.get("alt") != spec["alt"]
                or row.get("expected_source_file_sha256") != spec["sha256"]
                or row.get("alt_matches") is not True
                or row.get("download_sha256_matches_source_file") is not True
                or download.get("downloaded") is not True
                or download.get("content_type") != "image/png"
                or download.get("sha256") != spec["sha256"]
                or type(download.get("bytes")) is not int or download.get("bytes") <= 0
                or not isinstance(row.get("name"), str) or not row.get("name")):
            raise GoLiveError("Pinned reconciliation gallery semantics are invalid.")
        result.append(expected)
    if len({row["id"] for row in result}) != 6 or len({row["src"] for row in result}) != 6:
        raise GoLiveError("Pinned reconciliation gallery IDs/sources are not unique.")
    return result


def gallery_receipt(parent: dict[str, Any], expected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    images = parent.get("images")
    if not isinstance(images, list) or len(images) != 6:
        raise GoLiveError("Parent gallery does not contain exactly six images.")
    receipt: list[dict[str, Any]] = []
    for index, (image, approved) in enumerate(zip(images, expected), start=1):
        if not isinstance(image, dict):
            raise GoLiveError("Parent gallery returned an invalid image record.")
        image_id = strict_positive_id(image.get("id"), label=f"Live gallery ID {index}")
        src = validate_live_image_url(image.get("src"))
        download = download_live_image(src)
        value = {
            "order": index, "id": image_id, "src": src,
            "name": image.get("name"), "alt": image.get("alt"), **download,
        }
        if value != approved:
            raise GoLiveError(f"Parent gallery position {index} differs from its exact approved attachment/content.")
        receipt.append(value)
    return receipt


def validate_catalog_identity(parent: dict[str, Any], variations: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    strict_positive_id(parent.get("id"), label="Parent ID", expected=PARENT_ID)
    if (parent.get("name") != PARENT_NAME or parent.get("sku") != PARENT_SKU
            or parent.get("type") != PARENT_TYPE):
        raise GoLiveError("Parent 2061 fixed name, SKU, or variable type changed.")
    if len(variations) != EXPECTED_VARIATION_COUNT:
        raise GoLiveError("Parent 2061 must have exactly 64 existing variations.")
    expected = dict(EXPECTED_VARIATIONS)
    rows: dict[int, dict[str, Any]] = {}
    for row in variations:
        variation_id = strict_positive_id(row.get("id"), label="Variation ID")
        if variation_id in rows:
            raise GoLiveError("Duplicate variation ID returned for parent 2061.")
        if variation_id not in expected or row.get("sku") != expected[variation_id]:
            raise GoLiveError("Variation ID/SKU set for parent 2061 is not exact.")
        strict_positive_id(row.get("parent_id"), label=f"Variation {variation_id} parent ID", expected=PARENT_ID)
        if row.get("type") != "variation":
            raise GoLiveError("A fixed child is not a canonical variation record.")
        rows[variation_id] = row
    if list(sorted(rows)) != [variation_id for variation_id, _ in EXPECTED_VARIATIONS]:
        raise GoLiveError("Variation ID/SKU set for parent 2061 is not exact.")
    return rows


def parent_visible_variation_ids(parent: dict[str, Any]) -> list[int]:
    values = parent.get("variations")
    if not isinstance(values, list) or any(type(value) is not int for value in values):
        raise GoLiveError("Parent 2061 returned an invalid derived variations list.")
    if len(values) != len(set(values)) or any(value not in dict(EXPECTED_VARIATIONS) for value in values):
        raise GoLiveError("Parent 2061 derived variations list is duplicate or foreign.")
    return list(values)


def assert_variation_final(record: dict[str, Any], target: dict[str, Any], lead_image: dict[str, Any]) -> None:
    variation_id = strict_positive_id(record.get("id"), label="Variation ID", expected=target["variation_id"])
    if record.get("sku") != target["sku"]:
        raise GoLiveError(f"Variation {variation_id} SKU changed.")
    strict_positive_id(record.get("parent_id"), label=f"Variation {variation_id} parent ID", expected=PARENT_ID)
    if record.get("type") != "variation":
        raise GoLiveError(f"Variation {variation_id} type changed.")
    supported = variation_id in SUPPORTED_PRICES
    expected_status = "publish" if supported else "private"
    expected_price = SUPPORTED_PRICES[variation_id] if supported else str(target["before_regular_price"])
    if record.get("status") != expected_status or str(record.get("regular_price") or "") != expected_price:
        raise GoLiveError(f"Variation {variation_id} final price/status is not exact.")
    if stock_projection(record) != target["before_stock"]:
        raise GoLiveError(f"Variation {variation_id} stock state changed.")
    if normalized_variation_protected_fingerprint(record) != target["before_protected_fingerprint"]:
        raise GoLiveError(f"Variation {variation_id} protected business state changed.")
    image = record.get("image")
    if not isinstance(image, dict):
        raise GoLiveError(f"Variation {variation_id} no longer inherits the parent lead image.")
    image_projection = {key: image.get(key) for key in ("id", "src", "name", "alt")}
    if image_projection != lead_image:
        raise GoLiveError(f"Variation {variation_id} inherited image identity changed.")


def assert_fixed_final_state(parent: dict[str, Any], variations: list[dict[str, Any]],
                             source_plan: dict[str, Any], expected_gallery_rows: list[dict[str, Any]],
                             *, required_status: str) -> list[dict[str, Any]]:
    rows = validate_catalog_identity(parent, variations)
    if parent.get("status") != required_status:
        raise GoLiveError(f"Parent 2061 is not {required_status}.")
    baseline = source_plan["parent_before"]
    if stock_projection(parent) != baseline["before_stock"]:
        raise GoLiveError("Parent stock state changed.")
    if protected_fingerprint(parent, parent=True) != baseline["before_protected_fingerprint"]:
        raise GoLiveError("Parent protected state changed.")
    if parent_visible_variation_ids(parent) != sorted(SUPPORTED_PRICES):
        raise GoLiveError("Parent visible variation list is not the exact 32 supported children.")
    gallery = gallery_receipt(parent, expected_gallery_rows)
    lead = {key: gallery[0][key] for key in ("id", "src", "name", "alt")}
    for target in source_plan["variation_targets"]:
        assert_variation_final(rows[target["variation_id"]], target, lead)
    return gallery


def snapshot(parent: dict[str, Any], variations: list[dict[str, Any]], gallery: list[dict[str, Any]]) -> dict[str, Any]:
    rows = validate_catalog_identity(parent, variations)
    return {
        "parent_full_fingerprint": full_fingerprint(parent),
        "parent_protected_fingerprint": protected_fingerprint(parent, parent=True),
        "parent_stock": stock_projection(parent),
        "visible_variation_ids": parent_visible_variation_ids(parent),
        "gallery": gallery,
        "variation_fingerprints": [
            {"variation_id": variation_id, "sku": dict(EXPECTED_VARIATIONS)[variation_id],
             "full_fingerprint": full_fingerprint(rows[variation_id]),
             "protected_fingerprint": protected_fingerprint(rows[variation_id], parent=False)}
            for variation_id, _ in EXPECTED_VARIATIONS
        ],
    }


def transaction_contract() -> dict[str, Any]:
    return {
        "parent_id": PARENT_ID,
        "write_count": 1,
        "only_write": {"method": "PUT", "endpoint": f"/products/{PARENT_ID}",
                       "payload": {"status": "publish"}},
        "prewrite_status": "draft",
        "postwrite_status": "publish",
        "failure_policy": "lock_before_put_one_attempt_no_retry_no_rollback_no_delete",
        "preflight": (
            "exact locked-predecessor evidence; exact parent/64 variations; exact six attachment IDs, "
            "same-origin URLs, names, alt text and downloaded SHA-256; exact 64 Woo/Zoho physical stock"
        ),
    }


def fixed_sources() -> dict[str, Any]:
    return {
        "locked_plan": str(LOCKED_PLAN), "locked_plan_file_sha256": LOCKED_PLAN_FILE_SHA256,
        "locked_plan_declared_sha256": LOCKED_PLAN_DECLARED_SHA256,
        "locked_lock": str(LOCKED_LOCK), "locked_lock_sha256": LOCKED_LOCK_SHA256,
        "reconciliation": str(RECONCILIATION), "reconciliation_sha256": RECONCILIATION_SHA256,
        "business_reconciliation": str(BUSINESS_RECONCILIATION),
        "business_reconciliation_sha256": BUSINESS_RECONCILIATION_SHA256,
        "image_manifest": str(IMAGE_MANIFEST), "image_manifest_sha256": IMAGE_MANIFEST_SHA256,
    }


def stage_plan(current_snapshot: dict[str, Any], stock_preflight: dict[str, Any]) -> Path:
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "origin": EXACT_ORIGIN, "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16), "transaction": transaction_contract(),
        "sources": fixed_sources(), "stock_preflight": stock_preflight,
        "prewrite_snapshot": current_snapshot,
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    path = PLAN_DIR / f"{created.strftime('%Y%m%dT%H%M%SZ')}_fnpt_2061_publish_{digest[:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    wc.append_receipt("woocommerce_fnpt_publication_plan_staged", str(path))
    return path


def validate_stock_preflight(value: Any) -> None:
    keys = {"source", "matched_count", "mismatch_count", "in_stock_count",
            "out_of_stock_count", "stock_fingerprint"}
    if (not isinstance(value, dict) or set(value) != keys
            or value.get("source") != "Zoho Inventory actual_available_stock vs WooCommerce stock_quantity"
            or value.get("matched_count") != 64 or value.get("mismatch_count") != 0
            or type(value.get("in_stock_count")) is not int
            or type(value.get("out_of_stock_count")) is not int
            or value["in_stock_count"] + value["out_of_stock_count"] != 64
            or not HEX64.fullmatch(str(value.get("stock_fingerprint") or ""))):
        raise GoLiveError("Plan Zoho physical-stock preflight is invalid.")


def load_plan(path: str | Path) -> dict[str, Any]:
    plan = read_json(path)
    saved = plan.pop("sha256", None)
    if not isinstance(saved, str) or not secrets.compare_digest(saved, digest_for(plan)):
        raise GoLiveError("Plan hash check failed. The plan changed after review.")
    required = {"schema_version", "tool", "tool_version", "origin", "created_utc",
                "expires_utc", "nonce", "transaction", "sources", "stock_preflight",
                "prewrite_snapshot"}
    if set(plan) != required:
        raise GoLiveError("Plan schema is not closed and exact.")
    if (plan["schema_version"] != SCHEMA_VERSION or plan["tool"] != TOOL_NAME
            or plan["tool_version"] != TOOL_VERSION or plan["origin"] != EXACT_ORIGIN
            or plan["transaction"] != transaction_contract() or plan["sources"] != fixed_sources()):
        raise GoLiveError("Plan fixed transaction/evidence differs from this build.")
    try:
        created = datetime.fromisoformat(str(plan["created_utc"]))
        expires = datetime.fromisoformat(str(plan["expires_utc"]))
    except ValueError as exc:
        raise GoLiveError("Plan timestamps are invalid.") from exc
    if (created.tzinfo is None or expires.tzinfo is None
            or expires - created != timedelta(hours=PLAN_LIFETIME_HOURS)
            or created > utc_now() + timedelta(minutes=1) or utc_now() >= expires):
        raise GoLiveError("Plan timing is invalid or expired.")
    if not isinstance(plan["nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", plan["nonce"]):
        raise GoLiveError("Plan nonce is invalid.")
    validate_stock_preflight(plan["stock_preflight"])
    snap = plan["prewrite_snapshot"]
    snap_keys = {"parent_full_fingerprint", "parent_protected_fingerprint", "parent_stock",
                 "visible_variation_ids", "gallery", "variation_fingerprints"}
    if not isinstance(snap, dict) or set(snap) != snap_keys:
        raise GoLiveError("Plan prewrite snapshot shape is invalid.")
    if (not HEX64.fullmatch(str(snap["parent_full_fingerprint"]))
            or not HEX64.fullmatch(str(snap["parent_protected_fingerprint"]))
            or snap["parent_stock"] != {"manage_stock": False, "stock_quantity": None,
                                        "stock_status": "instock"}
            or snap["visible_variation_ids"] != sorted(SUPPORTED_PRICES)
            or not isinstance(snap["gallery"], list) or len(snap["gallery"]) != 6
            or not isinstance(snap["variation_fingerprints"], list)
            or len(snap["variation_fingerprints"]) != 64):
        raise GoLiveError("Plan prewrite snapshot semantics are invalid.")
    for value, (variation_id, sku) in zip(snap["variation_fingerprints"], EXPECTED_VARIATIONS):
        if (not isinstance(value, dict)
                or set(value) != {"variation_id", "sku", "full_fingerprint", "protected_fingerprint"}
                or type(value.get("variation_id")) is not int
                or value.get("variation_id") != variation_id or value.get("sku") != sku
                or not HEX64.fullmatch(str(value.get("full_fingerprint") or ""))
                or not HEX64.fullmatch(str(value.get("protected_fingerprint") or ""))):
            raise GoLiveError("Plan variation fingerprint list is invalid.")
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
        raise GoLiveError("This plan has already entered commit and cannot be replayed.") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def require_exact_approval(approval: Any) -> None:
    if not isinstance(approval, str) or approval != APPROVAL_WORD:
        raise GoLiveError("Rachad must approve this exact plan with the byte-exact word APPROVED.")


def assert_snapshot_exact(plan: dict[str, Any], parent: dict[str, Any],
                          variations: list[dict[str, Any]], gallery: list[dict[str, Any]]) -> None:
    if snapshot(parent, variations, gallery) != plan["prewrite_snapshot"]:
        raise GoLiveError("Live FNPT state changed after staging; nothing was written.")


def perform_publish(vault: dict[str, Any]) -> dict[str, Any]:
    endpoint = f"/products/{PARENT_ID}"
    payload = {"status": "publish"}
    validate_route("PUT", endpoint)
    result, _ = wc.api_request("PUT", endpoint, payload=payload, vault=vault)
    if not isinstance(result, dict):
        raise GoLiveError("WooCommerce publication PUT returned an unexpected response.")
    strict_positive_id(result.get("id"), label="Publication response parent ID", expected=PARENT_ID)
    return result


def command_stage(_: argparse.Namespace) -> None:
    source_plan, reconciliation = validate_fixed_evidence()
    vault = wc.load_vault()
    validate_vault(vault)
    parent, variations = read_catalog(vault)
    expected = expected_gallery(reconciliation)
    gallery = assert_fixed_final_state(
        parent, variations, source_plan, expected, required_status="draft"
    )
    stock_preflight = verify_zoho_physical_stock(variations)
    path = stage_plan(snapshot(parent, variations, gallery), stock_preflight)
    plan = read_json(path)
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED", "external_write_performed": False,
        "plan": str(path), "plan_sha256": plan["sha256"], "expires_utc": plan["expires_utc"],
        "parent_id": PARENT_ID, "parent_before_status": "draft",
        "parent_after_status": "publish", "write_count": 1,
        "only_write": transaction_contract()["only_write"],
        "supported_published_verified": SUPPORTED_COUNT,
        "unsupported_private_verified": UNSUPPORTED_COUNT,
        "gallery_verified": len(gallery), "stock_preflight": stock_preflight,
        "approval": APPROVAL_WORD,
        "disclosure": (
            "One parent-status PUT only. The replay lock is created before it; the request is "
            "attempted once; any failure or indeterminate result is permanent no-retry, with no "
            "rollback or deletion. No variation, gallery, price, stock, order, customer, or mail write exists."
        ),
    }, indent=2, ensure_ascii=False))


def command_commit(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve()
    try:
        plan_path.relative_to(PLAN_DIR.resolve())
    except ValueError as exc:
        raise GoLiveError("Plan must be inside the fixed FNPT plan folder.") from exc
    plan = load_plan(plan_path)
    require_exact_approval(args.approval)
    lock = lock_path(plan_path)
    if lock.exists():
        raise GoLiveError("This plan has already entered commit and cannot be replayed.")

    source_plan, reconciliation = validate_fixed_evidence()
    vault = wc.load_vault()
    validate_vault(vault)
    parent, variations = read_catalog(vault)
    expected = expected_gallery(reconciliation)
    gallery = assert_fixed_final_state(
        parent, variations, source_plan, expected, required_status="draft"
    )
    assert_snapshot_exact(plan, parent, variations, gallery)
    commit_stock_preflight = verify_zoho_physical_stock(variations)
    if commit_stock_preflight != plan["stock_preflight"]:
        raise GoLiveError("Fresh Zoho/WooCommerce stock preflight differs from the staged snapshot.")

    write_lock(lock, {
        "plan_sha256": plan["sha256"], "status": "in_flight",
        "started_utc": utc_now().isoformat(), "attempt": 1,
        "write_count": 1, "commit_stock_preflight": commit_stock_preflight,
    }, exclusive=True)
    phase = "parent_status_publish_only_write"
    writes_completed = 0
    try:
        perform_publish(vault)
        writes_completed = 1
        phase = "fresh_complete_final_verification"
        final_parent, final_variations = read_catalog(vault)
        final_gallery = assert_fixed_final_state(
            final_parent, final_variations, source_plan, expected, required_status="publish"
        )
        rows = validate_catalog_identity(final_parent, final_variations)
        staged_variations = {
            value["variation_id"]: value["protected_fingerprint"]
            for value in plan["prewrite_snapshot"]["variation_fingerprints"]
        }
        if any(protected_fingerprint(row, parent=False) != staged_variations[variation_id]
               for variation_id, row in rows.items()):
            raise GoLiveError("A variation protected field changed during parent publication.")
        final_stock = verify_zoho_physical_stock(final_variations)
        if final_stock != commit_stock_preflight:
            raise GoLiveError("Zoho/WooCommerce stock state changed during publication.")
        outcome = {
            "parent_id": PARENT_ID, "parent_status": "publish",
            "write_count": 1, "gallery_count": len(final_gallery),
            "supported_published_count": SUPPORTED_COUNT,
            "unsupported_private_count": UNSUPPORTED_COUNT,
            "stock_preserved_count": EXPECTED_VARIATION_COUNT,
            "protected_state_verified": True,
        }
    except Exception as exc:
        reason = {
            "reason_class": type(exc).__name__,
            "http_status": exc.status if isinstance(exc, wc.WooError) else None,
            "rest_code": exc.code if isinstance(exc, wc.WooError) else None,
        }
        write_lock(lock, {
            "plan_sha256": plan["sha256"], "status": "indeterminate",
            "updated_utc": utc_now().isoformat(), "attempt": 1,
            "phase": phase, "writes_completed": writes_completed,
            "commit_stock_preflight": commit_stock_preflight, **reason,
        })
        wc.append_receipt(
            "woocommerce_fnpt_publication_indeterminate_no_retry",
            f"plan={plan_path}; sha256={plan['sha256']}; phase={phase}; "
            f"writes_completed={writes_completed}; reason_class={reason['reason_class']}",
        )
        raise GoLiveError(
            f"FNPT publication recovery failed during {phase}. The one-write plan is permanently "
            "locked indeterminate; it will not retry, roll back, or delete."
        ) from exc

    write_lock(lock, {
        "plan_sha256": plan["sha256"], "status": "committed_verified",
        "updated_utc": utc_now().isoformat(), "attempt": 1,
        "writes_completed": 1, "commit_stock_preflight": commit_stock_preflight,
        "gallery": final_gallery, "outcome": outcome,
    })
    wc.append_receipt(
        "woocommerce_fnpt_publication_committed_verified",
        f"parent_id={PARENT_ID}; plan={plan_path}; sha256={plan['sha256']}; writes_completed=1",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED", "plan_sha256": plan["sha256"],
        "replay_locked": True, "attempts": 1, "writes_completed": 1,
        "commit_stock_preflight": commit_stock_preflight, "outcome": outcome,
        "gallery": final_gallery,
    }, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage", help="read-only stage of the fixed publication recovery")
    stage.set_defaults(func=command_stage)
    commit = commands.add_parser("commit", help="commit one exact staged publication once")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (GoLiveError, wc.WooError, OSError, ValueError) as exc:
        print("ERROR: " + wc.scrub(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
