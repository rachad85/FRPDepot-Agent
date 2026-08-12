#!/usr/bin/env python
"""Fixed, approval-gated WooCommerce go-live transaction for FNPT parent 2061.

This module exposes exactly one transaction. ``stage`` performs WooCommerce and
Zoho Inventory GETs plus local file hashing only. ``commit`` accepts only the
staged plan path and the byte-exact word ``APPROVED``. It reruns the fixed 64-SKU
Zoho physical-stock comparison immediately before its replay lock and first
write. The transaction is deliberately non-atomic: it locks before its first
PUT, stops on the first failure, never retries, never rolls back, and never
deletes anything.

Write order is fixed:
1. verify already-correct variation 2062, then update the other 63 existing
   variations while parent 2061 is draft;
2. assign the six fixed original images while the parent is still draft;
3. verify the draft parent and exact six-image gallery;
4. publish parent 2061 with a final PUT whose payload is only status=publish;
5. fresh-read and verify the complete parent/variation result.
"""
from __future__ import annotations

import argparse
import csv
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

import woocommerce_common as wc

ZOHO_TOOL_DIR = Path(r"C:\FRPDepot\Dado\Tools\zoho")
if str(ZOHO_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(ZOHO_TOOL_DIR))
import zoho_tool

TOOL_NAME = "FRP Depot Fixed WooCommerce FNPT Go-Live Tool"
TOOL_VERSION = "2.0.0"
SCHEMA_VERSION = 3
APPROVAL_WORD = "APPROVED"
PLAN_LIFETIME_HOURS = 24
# Use the canonical exact origin enforced by woocommerce_common. Its origin
# validator accepts only HTTPS and the implicit/explicit standard port 443,
# then normalizes both forms to this byte-exact value.
EXACT_ORIGIN = wc.ALLOWED_ORIGIN
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "woocommerce_fnpt_go_live_plans"
IMAGE_DIR = (
    ROOT / "Dado" / "20_Working" / "pricing_requests" / "fnpt"
    / "original_product_images_20260811"
)
IMAGE_MANIFEST = IMAGE_DIR / "manifest.json"
IMAGE_MANIFEST_SHA256 = "1b2ae0293858d6b473a397a35ecfbdb592b8a5b7563951604a4d37cb44fb4d04"
PARENT_ID = 2061
PARENT_NAME = "FNPT Coupling, Threaded On both ends"
PARENT_SKU = "ZOHO-GROUP-5961888EC5DB"
PARENT_TYPE = "variable"
EXPECTED_VARIATION_COUNT = 64
SUPPORTED_COUNT = 32
UNSUPPORTED_COUNT = 32
PREEXISTING_CORRECT_IDS = frozenset({2062})
HEX64 = re.compile(r"^[0-9a-f]{64}$")

FNPT_WORK = ROOT / "Dado" / "20_Working" / "pricing_requests" / "fnpt"
SUPPLIER_WORKBOOK = FNPT_WORK / "FNPT_Quotation_Sheet_Rev_01_Aug_12_2026_supplier.xlsx"
SUPPLIER_WORKBOOK_SHA256 = "fc99d4a46d289062540535a686dc482d7224b944d3fe9bf51f7caf18ce4d416e"
SUPPLIER_COSTS = FNPT_WORK / "fnpt_aug12_supplier_catalog_costs.csv"
SUPPLIER_COSTS_SHA256 = "a937b4ddbc364d7ef8e008bf86e5bf407ef578a971e393b0bf2c7936dc35e24e"
PRICING_CSV = FNPT_WORK / "fnpt_aug12_final_online_prices.csv"
PRICING_CSV_SHA256 = "e344f6160b7f6ca8e945dfd57bac9de8d23e04f55977e973af6b81774e59b1c5"
PRICING_EVIDENCE = FNPT_WORK / "fnpt_aug12_frpsupply_refresh_evidence.json"
PRICING_EVIDENCE_SHA256 = "793fbcd4f0a6798c7c04f435159de42a8ae68f8bcbeac8057f78fa55f53e5026"
PRICING_SUMMARY = FNPT_WORK / "fnpt_aug12_pricing_summary.json"
PRICING_SUMMARY_SHA256 = "c7b0fcff37f2803caca7e5c6acb0cbe042100ba0ede506e7835c64b8a93059ea"
FAILED_PLAN = PLAN_DIR / "20260812T025338Z_fnpt_2061_fc0f84f4fd089038.json"
FAILED_PLAN_FILE_SHA256 = "8b20d908a063b1fb72147022951bd76893b4d559c3e45f4707467fd52fbae22a"
FAILED_LOCK = PLAN_DIR / "20260812T025338Z_fnpt_2061_fc0f84f4fd089038.commit-lock.json"
FAILED_LOCK_SHA256 = "ebacbd06c1ad70d26893414e241d3c721640fd09b827e2368988e4c9989f60fd"

# Fixed six-file block, kept isolated for narrow review and future deliberate
# manifest rotation. Positions 1-6 exactly match the accepted hashed manifest;
# no rejected generation URL is present or reachable from the transaction.
IMAGE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "order": 1,
        "file": str(IMAGE_DIR / "01_fnpt_family_hero.png"),
        "src": "https://v3b.fal.media/files/b/0aa5fae2/WgEpw2Vkq39Ps8IfmsZ3T_C996GHLq.png",
        "sha256": "ff928a5c99fbdbbb01cbd0a03b983eff00ceecabcebd2a86d562f107f5a9bacd",
        "alt": "FRP female NPT couplings in three sizes",
    },
    {
        "order": 2,
        "file": str(IMAGE_DIR / "02_fnpt_single_detail.png"),
        "src": "https://v3b.fal.media/files/b/0aa5fae2/WzFzU4V23EholeSSA1lcU_LUvnsyOV.png",
        "sha256": "5aeaaa394668a1e3f93ad8508ceebc8dc055c2cb40239422b9321ec5f5a304b9",
        "alt": "FRP female NPT coupling threaded detail",
    },
    {
        "order": 3,
        "file": str(IMAGE_DIR / "03_fnpt_three_size_top_view.png"),
        "src": "https://v3b.fal.media/files/b/0aa5fae9/JlCG1mG8TioGem-CvTnhy_1VmeHfhQ.png",
        "sha256": "489cd7ef23e424d9dcc0cdf1d28004eb72a4c4b8ffa24e581e624e725491e168",
        "alt": "Three FRP female NPT coupling sizes, top view",
    },
    {
        "order": 4,
        "file": str(IMAGE_DIR / "04_fnpt_horizontal_through_bore.png"),
        "src": "https://v3b.fal.media/files/b/0aa5fb95/Uqdtkj3Q9YRuceD2T4_3R_JsmypqN5.png",
        "sha256": "963fd65885cff24655bcc14b63bb992828ecce354a115ced957de58aa819c6cc",
        "alt": "Horizontal FRP female NPT coupling with internal threads",
    },
    {
        "order": 5,
        "file": str(IMAGE_DIR / "05_fnpt_upright_thread_detail.png"),
        "src": "https://v3b.fal.media/files/b/0aa5fb8e/--QMEOI7A6NcuBE6lAekR_M5lVjiz9.png",
        "sha256": "1ea905d1b8dbf2ca08679838ca0a9d67a8602f774cf219887622917ea5960693",
        "alt": "Upright FRP female NPT coupling with internal threads",
    },
    {
        "order": 6,
        "file": str(IMAGE_DIR / "06_fnpt_two_size_comparison.png"),
        "src": "https://v3b.fal.media/files/b/0aa5fb8e/ndcL_ORz09mlmO5K_HE4d_7Sq4vo7b.png",
        "sha256": "31e6a9e456319d30b8a5a49c94d3eca7c23d2f782dece81996a03e21785e855e",
        "alt": "Two FRP female NPT coupling sizes",
    },
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

# Exact values pinned from the Aug. 12 supplier workbook and the bounded public
# FRP Supply comparison. No CLI input can alter this mapping.
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
UNSUPPORTED_IDS = frozenset(variation_id for variation_id, _ in EXPECTED_VARIATIONS) - frozenset(SUPPORTED_PRICES)
WRITE_TARGET_IDS = frozenset(variation_id for variation_id, _ in EXPECTED_VARIATIONS) - PREEXISTING_CORRECT_IDS

# Values WooCommerce derives from an allowed variation save are excluded from
# the protected fingerprint. Everything else in the REST record is protected.
VARIATION_ALLOWED_OR_DERIVED = frozenset({
    "status", "regular_price", "price", "purchasable", "on_sale", "permalink",
    "date_modified", "date_modified_gmt",
})
# Parent price/purchasability can derive from child pricing; images and status are
# the only explicit parent writes. All remaining fields are protected.
PARENT_ALLOWED_OR_DERIVED = frozenset({
    "images", "status", "price", "price_html", "purchasable", "on_sale", "permalink",
    "date_modified", "date_modified_gmt", "yoast_head", "yoast_head_json",
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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise GoLiveError(f"Required fixed image file is unreadable: {path}") from exc
    return digest.hexdigest()


def read_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoLiveError(f"Plan JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise GoLiveError("Plan JSON must contain one object.")
    return value


def expected_variation_map() -> dict[int, str]:
    return dict(EXPECTED_VARIATIONS)


def variation_endpoint(variation_id: int) -> str:
    if variation_id not in expected_variation_map():
        raise GoLiveError("REFUSED: variation ID is outside fixed parent 2061.")
    return f"/products/{PARENT_ID}/variations/{variation_id}"


def validate_route(method: str, endpoint: str) -> None:
    verb = str(method).upper()
    allowed = (
        (verb == "GET" and endpoint == f"/products/{PARENT_ID}")
        or (verb == "GET" and endpoint == f"/products/{PARENT_ID}/variations")
        or (verb == "GET" and re.fullmatch(
            rf"/products/{PARENT_ID}/variations/(?:{'|'.join(str(v) for v, _ in EXPECTED_VARIATIONS)})",
            endpoint,
        ))
        or (verb == "PUT" and re.fullmatch(
            rf"/products/{PARENT_ID}/variations/(?:{'|'.join(str(v) for v in sorted(WRITE_TARGET_IDS))})",
            endpoint,
        ))
        or (verb == "PUT" and endpoint == f"/products/{PARENT_ID}")
    )
    if not allowed:
        raise GoLiveError("REFUSED: method/path pair is outside the fixed FNPT transaction.")


def stock_projection(record: dict[str, Any]) -> dict[str, Any]:
    return {field: record.get(field) for field in STOCK_FIELDS}


def protected_fingerprint(record: dict[str, Any], *, parent: bool) -> str:
    excluded = PARENT_ALLOWED_OR_DERIVED if parent else VARIATION_ALLOWED_OR_DERIVED
    projection = {key: record.get(key) for key in sorted(record) if key not in excluded}
    return digest_value(projection)


def full_fingerprint(record: dict[str, Any]) -> str:
    return digest_value(record)


def public_image_specs() -> list[dict[str, Any]]:
    return [
        {key: spec[key] for key in ("order", "file", "src", "sha256", "alt")}
        for spec in IMAGE_SPECS
    ]


def image_payload() -> dict[str, Any]:
    return {"images": [{"src": spec["src"], "alt": spec["alt"]} for spec in IMAGE_SPECS]}


def validate_image_assets() -> None:
    if (not IMAGE_MANIFEST.is_file()
            or not secrets.compare_digest(file_sha256(IMAGE_MANIFEST), IMAGE_MANIFEST_SHA256)):
        raise GoLiveError("Fixed six-image manifest hash mismatch.")
    try:
        manifest = json.loads(IMAGE_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoLiveError("Fixed six-image manifest is unreadable.") from exc
    manifest_images = manifest.get("images") if isinstance(manifest, dict) else None
    if not isinstance(manifest_images, list) or len(manifest_images) != 6:
        raise GoLiveError("Fixed image manifest must contain exactly six images.")
    if len(IMAGE_SPECS) != 6:
        raise GoLiveError("Exactly six fixed image specifications are required.")
    orders = [spec.get("order") for spec in IMAGE_SPECS]
    if orders != [1, 2, 3, 4, 5, 6]:
        raise GoLiveError("Fixed image order must be exactly 1 through 6.")
    sources: list[str] = []
    files: list[Path] = []
    for index, spec in enumerate(IMAGE_SPECS, start=1):
        if set(spec) != {"order", "file", "src", "sha256", "alt"}:
            raise GoLiveError(f"IMAGE_SPECS position {index} has an invalid shape.")
        path = Path(str(spec["file"])).resolve()
        try:
            path.relative_to(IMAGE_DIR.resolve())
        except ValueError as exc:
            raise GoLiveError("Every fixed image file must be inside the fixed FNPT image folder.") from exc
        if path.suffix.casefold() != ".png":
            raise GoLiveError("Every fixed FNPT image must be a PNG.")
        src = str(spec["src"])
        parsed = urlparse(src)
        if (parsed.scheme != "https" or parsed.hostname != "v3b.fal.media"
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.query or parsed.fragment or not parsed.path.endswith(".png")):
            raise GoLiveError("A fixed image source URL is outside the closed HTTPS source shape.")
        expected_hash = str(spec["sha256"])
        if not HEX64.fullmatch(expected_hash):
            raise GoLiveError("A fixed image SHA-256 is invalid.")
        alt = spec["alt"]
        if (not isinstance(alt, str) or not alt or alt != alt.strip() or len(alt) > 250
                or any(char in alt for char in "<>\r\n\t")):
            raise GoLiveError("A fixed image alt value is invalid.")
        if not path.is_file() or not secrets.compare_digest(file_sha256(path), expected_hash):
            raise GoLiveError(f"Fixed image file hash mismatch at gallery position {index}: {path}")
        manifest_row = manifest_images[index - 1]
        if (not isinstance(manifest_row, dict)
                or Path(str(manifest_row.get("file") or "")).resolve() != path
                or manifest_row.get("generated_url") != src
                or manifest_row.get("sha256") != expected_hash):
            raise GoLiveError(f"Fixed image manifest row mismatch at gallery position {index}.")
        sources.append(src)
        files.append(path)
    if len(set(sources)) != 6 or len(set(files)) != 6:
        raise GoLiveError("The fixed gallery must contain six unique files and six unique source URLs.")


def validate_fixed_evidence() -> None:
    for label, path, expected_hash in (
        ("Aug. 12 supplier workbook", SUPPLIER_WORKBOOK, SUPPLIER_WORKBOOK_SHA256),
        ("32-row supplier cost CSV", SUPPLIER_COSTS, SUPPLIER_COSTS_SHA256),
        ("32-row final price CSV", PRICING_CSV, PRICING_CSV_SHA256),
        ("bounded FRP Supply evidence", PRICING_EVIDENCE, PRICING_EVIDENCE_SHA256),
        ("Aug. 12 pricing summary", PRICING_SUMMARY, PRICING_SUMMARY_SHA256),
        ("failed immutable predecessor plan", FAILED_PLAN, FAILED_PLAN_FILE_SHA256),
        ("permanent predecessor lock", FAILED_LOCK, FAILED_LOCK_SHA256),
    ):
        if not path.is_file() or not secrets.compare_digest(file_sha256(path), expected_hash):
            raise GoLiveError(f"Fixed {label} hash mismatch.")
    try:
        with PRICING_CSV.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise GoLiveError("Fixed 32-row final price CSV is unreadable.") from exc
    csv_prices = {
        int(row["variation_id"]): str(row["final_online_price_cad"])
        for row in rows
    }
    if len(rows) != SUPPORTED_COUNT or csv_prices != SUPPORTED_PRICES:
        raise GoLiveError("Fixed 32-row final price CSV differs from the closed transaction.")
    failed_lock = read_json(FAILED_LOCK)
    if (failed_lock.get("plan_sha256")
            != "fc0f84f4fd089038de5fe456186029d82414e56e4abab089f70a13c35b9af649"
            or failed_lock.get("status") != "indeterminate"
            or failed_lock.get("phase") != "variation_2063_parent_draft_guard"
            or failed_lock.get("writes_completed") != 1):
        raise GoLiveError("The fixed predecessor lock no longer proves its one-write stop state.")


def fixed_sources() -> dict[str, Any]:
    return {
        "aug12_supplier_workbook": str(SUPPLIER_WORKBOOK),
        "aug12_supplier_workbook_sha256": SUPPLIER_WORKBOOK_SHA256,
        "aug12_supplier_costs": str(SUPPLIER_COSTS),
        "aug12_supplier_costs_sha256": SUPPLIER_COSTS_SHA256,
        "aug12_final_prices": str(PRICING_CSV),
        "aug12_final_prices_sha256": PRICING_CSV_SHA256,
        "frp_supply_refresh_evidence": str(PRICING_EVIDENCE),
        "frp_supply_refresh_evidence_sha256": PRICING_EVIDENCE_SHA256,
        "aug12_pricing_summary": str(PRICING_SUMMARY),
        "aug12_pricing_summary_sha256": PRICING_SUMMARY_SHA256,
        "failed_predecessor_plan": str(FAILED_PLAN),
        "failed_predecessor_plan_file_sha256": FAILED_PLAN_FILE_SHA256,
        "failed_predecessor_plan_declared_sha256": (
            "fc0f84f4fd089038de5fe456186029d82414e56e4abab089f70a13c35b9af649"
        ),
        "permanent_predecessor_lock": str(FAILED_LOCK),
        "permanent_predecessor_lock_sha256": FAILED_LOCK_SHA256,
        "preexisting_correct_variation_ids": sorted(PREEXISTING_CORRECT_IDS),
        "unsupported_basis": (
            "all 32 Hetron 922 and Derakane 510A variations are different resins "
            "with no quoted cost or exact-equivalent evidence"
        ),
    }


def transaction_contract() -> dict[str, Any]:
    return {
        "parent_id": PARENT_ID,
        "parent_name": PARENT_NAME,
        "parent_sku": PARENT_SKU,
        "parent_type": PARENT_TYPE,
        "expected_variation_count": EXPECTED_VARIATION_COUNT,
        "supported_publish_count": SUPPORTED_COUNT,
        "unsupported_private_count": UNSUPPORTED_COUNT,
        "non_atomic": True,
        "failure_policy": "lock_indeterminate_stop_no_retry_no_rollback_no_delete",
        "stock_rule": (
            "stage and commit both require exact equality for all 64 between WooCommerce "
            "stock_quantity and Zoho Inventory actual_available_stock; commit check is "
            "immediately before its replay lock and first write"
        ),
        "write_order": [
            "verify_preexisting_correct_variation_2062_without_write",
            "63_remaining_variation_price_status_updates_while_parent_draft",
            "six_fixed_image_parent_put_while_parent_draft",
            "draft_parent_and_exact_gallery_verification",
            "final_parent_status_publish_only_put",
            "fresh_complete_final_verification",
        ],
    }


def require_exact_approval(approval: Any) -> None:
    if not isinstance(approval, str) or approval != APPROVAL_WORD:
        raise GoLiveError(
            "Rachad must approve this exact staged plan with the byte-exact uppercase "
            "word APPROVED. Spaces, punctuation, quotes, or different case are refused."
        )


def validate_vault(vault: dict[str, Any]) -> None:
    if vault.get("declared_permissions") != "read_write":
        raise GoLiveError("Saved WooCommerce key is not declared Read/Write.")
    try:
        origin = wc.normalize_site_url(str(vault.get("site_url") or ""))
    except wc.WooError as exc:
        raise GoLiveError("Saved WooCommerce origin is not the exact FRP Depot origin.") from exc
    if origin != wc.ALLOWED_ORIGIN:
        raise GoLiveError("Saved WooCommerce origin is not the exact FRP Depot origin.")


def read_catalog(vault: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    parent_ep = f"/products/{PARENT_ID}"
    variations_ep = f"/products/{PARENT_ID}/variations"
    validate_route("GET", parent_ep)
    validate_route("GET", variations_ep)
    parent, _ = wc.api_get(parent_ep, vault=vault)
    variations = wc.get_all(
        variations_ep,
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
    """GET the exact fixed FNPT set and return Zoho physical availability by SKU."""
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
    """Require exact equality between Woo quantity and Zoho physical availability."""
    expected = expected_variation_map()
    woo_by_id = {int(row.get("id") or 0): row for row in variations}
    if set(woo_by_id) != set(expected) or len(variations) != EXPECTED_VARIATION_COUNT:
        raise GoLiveError("WooCommerce variation set is not the fixed 64 before stock preflight.")
    zoho_by_sku = read_zoho_physical_stock()
    fingerprint_rows: list[dict[str, str]] = []
    in_stock = 0
    for variation_id, sku in EXPECTED_VARIATIONS:
        row = woo_by_id[variation_id]
        if row.get("sku") != sku or row.get("manage_stock") is not True:
            raise GoLiveError(f"WooCommerce variation {variation_id} is not managed stock with its fixed SKU.")
        woo_qty = stock_decimal(
            row.get("stock_quantity"), label=f"WooCommerce stock quantity for {sku}"
        )
        zoho_qty = zoho_by_sku[sku]
        if woo_qty != zoho_qty:
            raise GoLiveError(
                f"Physical-stock mismatch for {sku}: WooCommerce {woo_qty} vs Zoho {zoho_qty}."
            )
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


def validate_catalog_identity(parent: dict[str, Any], variations: list[dict[str, Any]],
                              *, require_draft: bool, require_empty_gallery: bool) -> None:
    if int(parent.get("id") or 0) != PARENT_ID:
        raise GoLiveError("REFUSED: the fixed parent product ID is not 2061.")
    if (parent.get("name") != PARENT_NAME or parent.get("sku") != PARENT_SKU
            or parent.get("type") != PARENT_TYPE):
        raise GoLiveError("REFUSED: parent 2061 fixed name, SKU, or variable type changed.")
    if require_draft and parent.get("status") != "draft":
        raise GoLiveError("REFUSED: fixed parent 2061 must still be draft.")
    images = parent.get("images")
    if require_empty_gallery and images != []:
        raise GoLiveError("REFUSED: parent 2061 no longer has the staged empty gallery.")
    if len(variations) != EXPECTED_VARIATION_COUNT:
        raise GoLiveError("REFUSED: parent 2061 must have exactly 64 existing variations.")
    expected = expected_variation_map()
    seen: dict[int, str] = {}
    for row in variations:
        variation_id = int(row.get("id") or 0)
        sku = row.get("sku")
        if variation_id in seen:
            raise GoLiveError("REFUSED: duplicate variation ID returned for parent 2061.")
        if variation_id not in expected or sku != expected[variation_id]:
            raise GoLiveError("REFUSED: variation ID/SKU set for parent 2061 is not exact.")
        if row.get("parent_id") not in (None, PARENT_ID):
            raise GoLiveError("REFUSED: a fixed variation belongs to a different parent.")
        if row.get("type") not in (None, "variation"):
            raise GoLiveError("REFUSED: a fixed child is not a variation.")
        seen[variation_id] = str(sku)
    if seen != expected:
        raise GoLiveError("REFUSED: variation ID/SKU set for parent 2061 is not exact.")


def target_payload(variation_id: int) -> dict[str, Any]:
    if variation_id in SUPPORTED_PRICES:
        return {"regular_price": SUPPORTED_PRICES[variation_id], "status": "publish"}
    if variation_id in UNSUPPORTED_IDS:
        return {"status": "private"}
    raise GoLiveError("REFUSED: variation is outside the fixed transaction.")


def make_target(record: dict[str, Any]) -> dict[str, Any]:
    variation_id = int(record["id"])
    return {
        "variation_id": variation_id,
        "sku": expected_variation_map()[variation_id],
        "supported": variation_id in SUPPORTED_PRICES,
        "preexisting_correct_no_write": variation_id in PREEXISTING_CORRECT_IDS,
        "payload": target_payload(variation_id),
        "before_regular_price": str(record.get("regular_price") or ""),
        "before_status": str(record.get("status") or ""),
        "before_stock": stock_projection(record),
        "before_full_fingerprint": full_fingerprint(record),
        "before_protected_fingerprint": protected_fingerprint(record, parent=False),
        "before_date_modified_gmt": str(record.get("date_modified_gmt") or ""),
    }


def make_parent_before(parent: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": PARENT_ID,
        "name": PARENT_NAME,
        "sku": PARENT_SKU,
        "type": PARENT_TYPE,
        "status": "draft",
        "images": [],
        "before_stock": stock_projection(parent),
        "before_full_fingerprint": full_fingerprint(parent),
        "before_protected_fingerprint": protected_fingerprint(parent, parent=True),
        "before_date_modified_gmt": str(parent.get("date_modified_gmt") or ""),
    }


def stage_plan(parent: dict[str, Any], variations: list[dict[str, Any]],
               stock_preflight: dict[str, Any]) -> Path:
    created = utc_now()
    rows = {int(row["id"]): row for row in variations}
    targets = [make_target(rows[variation_id]) for variation_id, _ in EXPECTED_VARIATIONS]
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "origin": EXACT_ORIGIN,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "transaction": transaction_contract(),
        "sources": fixed_sources(),
        "image_specs": public_image_specs(),
        "image_payload": image_payload(),
        "stock_preflight": stock_preflight,
        "parent_before": make_parent_before(parent),
        "variation_targets": targets,
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    path = PLAN_DIR / f"{created.strftime('%Y%m%dT%H%M%SZ')}_fnpt_2061_{digest[:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    wc.append_receipt("woocommerce_fnpt_go_live_plan_staged", str(path))
    return path


def expected_target_semantics(target: dict[str, Any], variation_id: int, sku: str) -> None:
    expected_keys = {
        "variation_id", "sku", "supported", "preexisting_correct_no_write",
        "payload", "before_regular_price",
        "before_status", "before_stock", "before_full_fingerprint",
        "before_protected_fingerprint", "before_date_modified_gmt",
    }
    if set(target) != expected_keys:
        raise GoLiveError("Plan variation target shape is invalid.")
    if target["variation_id"] != variation_id or target["sku"] != sku:
        raise GoLiveError("Plan variation IDs/SKUs differ from the fixed 64-resource set.")
    supported = variation_id in SUPPORTED_PRICES
    if (target["supported"] is not supported
            or target["preexisting_correct_no_write"] is not (variation_id in PREEXISTING_CORRECT_IDS)
            or target["payload"] != target_payload(variation_id)):
        raise GoLiveError("Plan variation price/status/no-write semantics differ from the fixed transaction.")
    if variation_id in PREEXISTING_CORRECT_IDS and (
            target["before_regular_price"] != SUPPORTED_PRICES[variation_id]
            or target["before_status"] != "publish"):
        raise GoLiveError("Pre-existing variation 2062 is not already at its exact final price/status.")
    if set(target["before_stock"]) != set(STOCK_FIELDS):
        raise GoLiveError("Plan stock baseline shape is invalid.")
    for field in ("before_full_fingerprint", "before_protected_fingerprint"):
        if not isinstance(target[field], str) or not HEX64.fullmatch(target[field]):
            raise GoLiveError("Plan resource fingerprint is invalid.")
    if not isinstance(target["before_regular_price"], str):
        raise GoLiveError("Plan regular-price baseline is invalid.")
    if not isinstance(target["before_status"], str):
        raise GoLiveError("Plan status baseline is invalid.")
    if not isinstance(target["before_date_modified_gmt"], str):
        raise GoLiveError("Plan modification baseline is invalid.")
    if any(field in target["payload"] for field in STOCK_FIELDS):
        raise GoLiveError("REFUSED: stock fields can never enter a variation payload.")


def load_plan(path: str | Path) -> dict[str, Any]:
    plan = read_json(path)
    saved = plan.pop("sha256", None)
    if not isinstance(saved, str) or not secrets.compare_digest(saved, digest_for(plan)):
        raise GoLiveError("Plan hash check failed. The plan changed after review.")
    required = {
        "schema_version", "tool", "tool_version", "origin", "created_utc", "expires_utc",
        "nonce", "transaction", "sources", "image_specs", "image_payload",
        "stock_preflight", "parent_before", "variation_targets",
    }
    if set(plan) != required:
        raise GoLiveError("Plan schema is not closed and exact.")
    if (plan["schema_version"] != SCHEMA_VERSION or plan["tool"] != TOOL_NAME
            or plan["tool_version"] != TOOL_VERSION or plan["origin"] != EXACT_ORIGIN):
        raise GoLiveError("Plan schema, tool version, or exact origin is invalid.")
    if plan["transaction"] != transaction_contract() or plan["sources"] != fixed_sources():
        raise GoLiveError("Plan transaction or evidence differs from the fixed commission.")
    if plan["image_specs"] != public_image_specs() or plan["image_payload"] != image_payload():
        raise GoLiveError("Plan image assets, URLs, hashes, alt text, or order are not exact.")
    stock_preflight = plan["stock_preflight"]
    stock_keys = {
        "source", "matched_count", "mismatch_count", "in_stock_count",
        "out_of_stock_count", "stock_fingerprint",
    }
    if (not isinstance(stock_preflight, dict) or set(stock_preflight) != stock_keys
            or stock_preflight.get("source")
            != "Zoho Inventory actual_available_stock vs WooCommerce stock_quantity"
            or stock_preflight.get("matched_count") != EXPECTED_VARIATION_COUNT
            or stock_preflight.get("mismatch_count") != 0
            or not isinstance(stock_preflight.get("in_stock_count"), int)
            or not isinstance(stock_preflight.get("out_of_stock_count"), int)
            or stock_preflight["in_stock_count"] + stock_preflight["out_of_stock_count"]
            != EXPECTED_VARIATION_COUNT
            or not HEX64.fullmatch(str(stock_preflight.get("stock_fingerprint") or ""))):
        raise GoLiveError("Plan Zoho physical-stock preflight is invalid.")
    try:
        created = datetime.fromisoformat(str(plan["created_utc"]))
        expires = datetime.fromisoformat(str(plan["expires_utc"]))
    except ValueError as exc:
        raise GoLiveError("Plan timestamps are invalid.") from exc
    if created.tzinfo is None or expires.tzinfo is None:
        raise GoLiveError("Plan timestamps must be timezone-aware.")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise GoLiveError("Plan lifetime is not exactly 24 hours.")
    now = utc_now()
    if created > now + timedelta(minutes=1):
        raise GoLiveError("Plan creation time is in the future.")
    if now >= expires:
        raise GoLiveError("Plan expired. Stage a new fixed FNPT plan for review.")
    if not isinstance(plan["nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", plan["nonce"]):
        raise GoLiveError("Plan nonce is invalid.")
    parent = plan["parent_before"]
    parent_keys = {
        "id", "name", "sku", "type", "status", "images", "before_stock",
        "before_full_fingerprint", "before_protected_fingerprint",
        "before_date_modified_gmt",
    }
    if (not isinstance(parent, dict) or set(parent) != parent_keys
            or {key: parent[key] for key in ("id", "name", "sku", "type", "status", "images")} != {
                "id": PARENT_ID, "name": PARENT_NAME, "sku": PARENT_SKU,
                "type": PARENT_TYPE, "status": "draft", "images": [],
            }
            or set(parent["before_stock"]) != set(STOCK_FIELDS)
            or not HEX64.fullmatch(str(parent["before_full_fingerprint"]))
            or not HEX64.fullmatch(str(parent["before_protected_fingerprint"]))
            or not isinstance(parent["before_date_modified_gmt"], str)):
        raise GoLiveError("Plan parent baseline is invalid.")
    targets = plan["variation_targets"]
    if not isinstance(targets, list) or len(targets) != EXPECTED_VARIATION_COUNT:
        raise GoLiveError("Plan must enumerate exactly 64 fixed variations.")
    for target, (variation_id, sku) in zip(targets, EXPECTED_VARIATIONS):
        if not isinstance(target, dict):
            raise GoLiveError("Plan variation target is invalid.")
        expected_target_semantics(target, variation_id, sku)
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


def assert_parent_matches_baseline(parent: dict[str, Any], baseline: dict[str, Any],
                                   *, full: bool, required_status: str) -> None:
    validate_catalog_identity(parent, [
        {"id": variation_id, "sku": sku} for variation_id, sku in EXPECTED_VARIATIONS
    ], require_draft=False, require_empty_gallery=False)
    if parent.get("status") != required_status:
        raise GoLiveError(f"Parent 2061 is not {required_status}.")
    if stock_projection(parent) != baseline["before_stock"]:
        raise GoLiveError("Parent stock state changed.")
    if protected_fingerprint(parent, parent=True) != baseline["before_protected_fingerprint"]:
        raise GoLiveError("Parent protected state changed.")
    if full and (full_fingerprint(parent) != baseline["before_full_fingerprint"]
                 or str(parent.get("date_modified_gmt") or "")
                 != baseline["before_date_modified_gmt"]):
        raise GoLiveError("Parent 2061 changed after staging.")


def assert_variation_identity(record: dict[str, Any], target: dict[str, Any]) -> None:
    if (int(record.get("id") or 0) != target["variation_id"]
            or record.get("sku") != target["sku"]
            or record.get("parent_id") not in (None, PARENT_ID)
            or record.get("type") not in (None, "variation")):
        raise GoLiveError("A fixed variation identity, type, or parent changed.")


def assert_variation_staged(record: dict[str, Any], target: dict[str, Any]) -> None:
    assert_variation_identity(record, target)
    if (full_fingerprint(record) != target["before_full_fingerprint"]
            or str(record.get("date_modified_gmt") or "")
            != target["before_date_modified_gmt"]):
        raise GoLiveError(
            f"Variation {target['variation_id']} changed after staging; nothing was written."
        )


def assert_variation_final(record: dict[str, Any], target: dict[str, Any]) -> None:
    assert_variation_identity(record, target)
    expected_status = "publish" if target["supported"] else "private"
    expected_price = (
        SUPPORTED_PRICES[target["variation_id"]]
        if target["supported"] else target["before_regular_price"]
    )
    if record.get("status") != expected_status or str(record.get("regular_price") or "") != expected_price:
        raise GoLiveError(f"Variation {target['variation_id']} price/status verification failed.")
    if stock_projection(record) != target["before_stock"]:
        raise GoLiveError(f"Variation {target['variation_id']} stock state changed.")
    if protected_fingerprint(record, parent=False) != target["before_protected_fingerprint"]:
        raise GoLiveError(f"Variation {target['variation_id']} protected state changed.")


def assert_prelock_snapshot(plan: dict[str, Any], parent: dict[str, Any],
                            variations: list[dict[str, Any]]) -> None:
    validate_catalog_identity(parent, variations, require_draft=True, require_empty_gallery=True)
    assert_parent_matches_baseline(parent, plan["parent_before"], full=True, required_status="draft")
    rows = {int(row["id"]): row for row in variations}
    for target in plan["variation_targets"]:
        assert_variation_staged(rows[target["variation_id"]], target)


def assert_parent_draft_protected(parent: dict[str, Any], plan: dict[str, Any]) -> None:
    if (int(parent.get("id") or 0) != PARENT_ID or parent.get("name") != PARENT_NAME
            or parent.get("sku") != PARENT_SKU or parent.get("type") != PARENT_TYPE
            or parent.get("status") != "draft"):
        raise GoLiveError("Parent 2061 identity/type/draft status changed during commit.")
    if stock_projection(parent) != plan["parent_before"]["before_stock"]:
        raise GoLiveError("Parent stock state changed during commit.")
    if protected_fingerprint(parent, parent=True) != plan["parent_before"]["before_protected_fingerprint"]:
        raise GoLiveError("Parent protected state changed during commit.")


def gallery_receipt(parent: dict[str, Any]) -> list[dict[str, Any]]:
    images = parent.get("images")
    if not isinstance(images, list) or len(images) != 6:
        raise GoLiveError("Parent gallery does not contain exactly six images.")
    receipt: list[dict[str, Any]] = []
    ids: list[int] = []
    for index, (image, spec) in enumerate(zip(images, IMAGE_SPECS), start=1):
        if not isinstance(image, dict):
            raise GoLiveError("Parent gallery returned an invalid image record.")
        image_id = int(image.get("id") or 0)
        if image_id <= 0 or image.get("alt") != spec["alt"] or not str(image.get("src") or ""):
            raise GoLiveError(f"Parent gallery position {index} failed ID/alt/source verification.")
        ids.append(image_id)
        receipt.append({"order": index, "id": image_id, "alt": spec["alt"]})
    if len(set(ids)) != 6:
        raise GoLiveError("Parent gallery image IDs are not six unique attachments.")
    return receipt


def assert_gallery_exact(parent: dict[str, Any], approved: list[dict[str, Any]]) -> None:
    if gallery_receipt(parent) != approved:
        raise GoLiveError("Parent gallery IDs, alt values, or order changed after verification.")


def perform_put(endpoint: str, payload: dict[str, Any], vault: dict[str, Any]) -> dict[str, Any]:
    validate_route("PUT", endpoint)
    if endpoint == f"/products/{PARENT_ID}":
        allowed = payload == image_payload() or payload == {"status": "publish"}
    else:
        match = re.fullmatch(rf"/products/{PARENT_ID}/variations/([0-9]+)", endpoint)
        allowed = bool(match and payload == target_payload(int(match.group(1))))
    if not allowed or any(field in payload for field in STOCK_FIELDS):
        raise GoLiveError("REFUSED: write payload is outside the fixed transaction.")
    result, _ = wc.api_request("PUT", endpoint, payload=payload, vault=vault)
    if not isinstance(result, dict):
        raise GoLiveError("WooCommerce PUT returned an unexpected response.")
    return result


def fresh_parent(vault: dict[str, Any]) -> dict[str, Any]:
    endpoint = f"/products/{PARENT_ID}"
    validate_route("GET", endpoint)
    value, _ = wc.api_get(endpoint, vault=vault)
    if not isinstance(value, dict) or int(value.get("id") or 0) != PARENT_ID:
        raise GoLiveError("Fresh parent read returned the wrong product.")
    return value


def fresh_variation(target: dict[str, Any], vault: dict[str, Any]) -> dict[str, Any]:
    endpoint = variation_endpoint(target["variation_id"])
    validate_route("GET", endpoint)
    value, _ = wc.api_get(endpoint, vault=vault)
    if not isinstance(value, dict):
        raise GoLiveError("Fresh variation read returned an unexpected response.")
    assert_variation_identity(value, target)
    return value


def final_verify(plan: dict[str, Any], gallery: list[dict[str, Any]],
                 vault: dict[str, Any]) -> dict[str, Any]:
    parent, variations = read_catalog(vault)
    validate_catalog_identity(parent, variations, require_draft=False, require_empty_gallery=False)
    if parent.get("status") != "publish":
        raise GoLiveError("Final parent status is not publish.")
    if stock_projection(parent) != plan["parent_before"]["before_stock"]:
        raise GoLiveError("Final parent stock state changed.")
    if protected_fingerprint(parent, parent=True) != plan["parent_before"]["before_protected_fingerprint"]:
        raise GoLiveError("Final parent protected state changed.")
    assert_gallery_exact(parent, gallery)
    rows = {int(row["id"]): row for row in variations}
    for target in plan["variation_targets"]:
        assert_variation_final(rows[target["variation_id"]], target)
    supported = [target for target in plan["variation_targets"] if target["supported"]]
    unsupported = [target for target in plan["variation_targets"] if not target["supported"]]
    if len(supported) != SUPPORTED_COUNT or len(unsupported) != UNSUPPORTED_COUNT:
        raise GoLiveError("Final supported/private counts are not exact.")
    return {
        "parent_id": PARENT_ID,
        "parent_status": "publish",
        "gallery_count": 6,
        "supported_published_count": len(supported),
        "unsupported_private_count": len(unsupported),
        "stock_preserved_count": EXPECTED_VARIATION_COUNT,
        "protected_state_verified": True,
    }


def command_stage(_: argparse.Namespace) -> None:
    # File integrity is checked before credentials or any network read.
    validate_fixed_evidence()
    validate_image_assets()
    vault = wc.load_vault()
    validate_vault(vault)
    parent, variations = read_catalog(vault)
    validate_catalog_identity(parent, variations, require_draft=True, require_empty_gallery=True)
    rows = {int(row["id"]): row for row in variations}
    for variation_id in PREEXISTING_CORRECT_IDS:
        record = rows[variation_id]
        if (record.get("status") != "publish"
                or str(record.get("regular_price") or "") != SUPPORTED_PRICES[variation_id]):
            raise GoLiveError("Pre-existing variation 2062 is not at its exact verified recovery baseline.")
    stock_preflight = verify_zoho_physical_stock(variations)
    path = stage_plan(parent, variations, stock_preflight)
    plan = read_json(path)
    preview = [
        {
            "variation_id": target["variation_id"],
            "sku": target["sku"],
            "before_regular_price": target["before_regular_price"],
            "after_regular_price": (
                SUPPORTED_PRICES[target["variation_id"]] if target["supported"]
                else target["before_regular_price"]
            ),
            "before_status": target["before_status"],
            "after_status": "publish" if target["supported"] else "private",
            "write_required": not target["preexisting_correct_no_write"],
            "stock_unchanged": target["before_stock"],
        }
        for target in plan["variation_targets"]
    ]
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED",
        "external_write_performed": False,
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "parent_id": PARENT_ID,
        "parent_before_status": "draft",
        "parent_after_status": "publish",
        "supported_publish_count": SUPPORTED_COUNT,
        "unsupported_private_count": UNSUPPORTED_COUNT,
        "stock_preflight": stock_preflight,
        "gallery": public_image_specs(),
        "variations": preview,
        "approval": APPROVAL_WORD,
        "non_atomic_disclosure": (
            "63 remaining variation PUTs, one six-image parent PUT, and one final publish "
            "PUT are non-atomic. Variation 2062 is verified and not written again. Commit "
            "locks before the first PUT, stops on any failure, and never retries, rolls "
            "back, or deletes."
        ),
    }, indent=2, ensure_ascii=False))


def command_commit(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve()
    try:
        plan_path.relative_to(PLAN_DIR.resolve())
    except ValueError as exc:
        raise GoLiveError("Plan must be inside the fixed FNPT go-live plan folder.") from exc
    plan = load_plan(plan_path)
    require_exact_approval(args.approval)
    lock = lock_path(plan_path)
    if lock.exists():
        raise GoLiveError("This plan has already entered commit and cannot be replayed.")

    # Every check through this point is free: no replay lock and no write. A stale
    # parent/variation or image mismatch is therefore refused before lock/write.
    validate_fixed_evidence()
    validate_image_assets()
    vault = wc.load_vault()
    validate_vault(vault)
    parent, variations = read_catalog(vault)
    assert_prelock_snapshot(plan, parent, variations)
    # This is deliberately the final free check: fresh Zoho physical availability
    # must still equal all 64 Woo stock quantities immediately before the replay
    # lock and first WooCommerce write.
    commit_stock_preflight = verify_zoho_physical_stock(variations)

    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "status": "in_flight",
        "started_utc": utc_now().isoformat(),
        "attempt": 1,
        "non_atomic": True,
        "commit_stock_preflight": commit_stock_preflight,
    }, exclusive=True)
    phase = "locked_before_first_write"
    writes_completed = 0
    gallery: list[dict[str, Any]] = []
    try:
        # Phase 1: every child, with a fresh parent-draft guard and fresh child
        # stale check immediately before that child's sole write.
        for target in plan["variation_targets"]:
            variation_id = target["variation_id"]
            phase = f"variation_{variation_id}_parent_draft_guard"
            assert_parent_draft_protected(fresh_parent(vault), plan)
            phase = f"variation_{variation_id}_prewrite"
            current = fresh_variation(target, vault)
            assert_variation_staged(current, target)
            if target["preexisting_correct_no_write"]:
                phase = f"variation_{variation_id}_preexisting_final_verification"
                assert_variation_final(current, target)
                continue
            phase = f"variation_{variation_id}_write"
            response = perform_put(variation_endpoint(variation_id), target["payload"], vault)
            if int(response.get("id") or 0) != variation_id:
                raise GoLiveError("WooCommerce variation PUT returned the wrong resource ID.")
            writes_completed += 1
            phase = f"variation_{variation_id}_readback"
            assert_variation_final(fresh_variation(target, vault), target)

        # Phases 2 and 3: gallery assignment and exact draft readback.
        phase = "pre_gallery_parent_draft_guard"
        draft_parent = fresh_parent(vault)
        assert_parent_draft_protected(draft_parent, plan)
        if draft_parent.get("images") != []:
            raise GoLiveError("Parent gallery changed before the approved image assignment.")
        phase = "six_image_gallery_write"
        response = perform_put(f"/products/{PARENT_ID}", image_payload(), vault)
        if int(response.get("id") or 0) != PARENT_ID:
            raise GoLiveError("WooCommerce gallery PUT returned the wrong parent ID.")
        writes_completed += 1
        phase = "draft_gallery_verification"
        draft_with_gallery = fresh_parent(vault)
        assert_parent_draft_protected(draft_with_gallery, plan)
        gallery = gallery_receipt(draft_with_gallery)

        # Phase 4: publication is the last write and its payload contains only status.
        phase = "final_parent_publish_write"
        response = perform_put(f"/products/{PARENT_ID}", {"status": "publish"}, vault)
        if int(response.get("id") or 0) != PARENT_ID:
            raise GoLiveError("WooCommerce publish PUT returned the wrong parent ID.")
        writes_completed += 1

        # Phase 5: fresh complete read, never response-body trust.
        phase = "fresh_complete_final_verification"
        outcome = final_verify(plan, gallery, vault)
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
            "commit_stock_preflight": commit_stock_preflight,
            **reason,
        })
        wc.append_receipt(
            "woocommerce_fnpt_go_live_indeterminate_no_retry",
            f"plan={plan_path}; sha256={plan['sha256']}; phase={phase}; "
            f"writes_completed={writes_completed}; reason_class={reason['reason_class']}",
        )
        raise GoLiveError(
            f"FNPT go-live failed during {phase}. The non-atomic plan is locked "
            "indeterminate; it will not continue, retry, roll back, or delete. Reconcile "
            "parent 2061 and all 64 variations before staging any new plan."
        ) from exc

    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "status": "committed_verified",
        "updated_utc": utc_now().isoformat(),
        "attempt": 1,
        "writes_completed": writes_completed,
        "commit_stock_preflight": commit_stock_preflight,
        "gallery": gallery,
        "outcome": outcome,
    })
    wc.append_receipt(
        "woocommerce_fnpt_go_live_committed_verified",
        f"parent_id={PARENT_ID}; plan={plan_path}; sha256={plan['sha256']}; "
        f"writes_completed={writes_completed}; supported={SUPPORTED_COUNT}; "
        f"private={UNSUPPORTED_COUNT}; gallery=6",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "plan_sha256": plan["sha256"],
        "replay_locked": True,
        "attempts": 1,
        "writes_completed": writes_completed,
        "commit_stock_preflight": commit_stock_preflight,
        "outcome": outcome,
        "gallery": gallery,
        "non_atomic": True,
    }, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage", help="read-only stage of the one fixed transaction")
    stage.set_defaults(func=command_stage)
    commit = commands.add_parser("commit", help="commit one exact staged plan once")
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
