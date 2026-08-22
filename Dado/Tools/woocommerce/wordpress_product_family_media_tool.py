#!/usr/bin/env python
"""FRP Depot fixed product-family media upload and gallery assignment tool.

Commissioned by Rachad Homsi on 2026-08-15 for exactly five approved product
families and no sixth: stub flange (product 1368), open manway (1397), manway
cover (1411), 90-degree elbow (1423), and filament-wound pipe (1455). FNPT is
explicitly excluded because its gallery belongs to its separate recovery plan.

Commands:
    stage --family KEY   Read-only validation and one immutable family plan.
    stage-all            One read-only library scan, then five independent plans.
    commit --plan PATH --approval APPROVED

Each commit first acquires the exact active FRP Depot Media Mutation Guard for
its fixed family, then uploads the exact per-family hard-coded files through the same
already-authenticated WordPress browser. The tool verifies the server's guarded
snapshot, verifies every public copy by SHA-256, makes one PUT to the fixed
existing WooCommerce product containing only the ordered image IDs, and asks
the guard to prove and complete that exact gallery. The guard blocks every
other attachment mutation during its 30-minute owner session; this tool never
reads its nonce or owner cookie.

The guard acquisition, exact family uploads, gallery PUT, and guard completion are
independent writes. The operation is therefore NOT atomic and has NO rollback:
verified early uploads remain unattached if a later upload fails; if any write
lands but read-back cannot prove it, the plan is permanently indeterminate and
the guard may remain active until automatic expiry. No retry, delete, media
edit, product-content/price/stock/status write, customer/order, plugin, setting,
user, Drive, Zoho, or email route exists.
"""
from __future__ import annotations

import argparse
import copy
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any, Iterator
from urllib.parse import parse_qsl, urljoin, urlsplit

ROOT = Path(r"C:\FRPDepot")
THIS_DIR = Path(__file__).resolve().parent
sys.path.append(str(THIS_DIR))
sys.path.append(str(THIS_DIR.parent / "common"))
import woocommerce_common as wc  # noqa: E402
import wordpress_packing_ring_media_tool as media_base  # noqa: E402
from ui_lane_lock import UiLaneBusy, UiLaneLockError, ui_browser_lock  # noqa: E402

TOOL_NAME = "FRP Depot Fixed Product Family Media Tool"
TOOL_VERSION = "2.1.0"
SCHEMA_VERSION = 12
ACTION = "upload_and_assign_fixed_product_family_gallery"
APPROVAL_WORD = "APPROVED"
PLAN_LIFETIME_HOURS = 24
MIN_AUTHORIZATION_MARGIN = timedelta(minutes=2)
MIN_GUARD_COMPLETION_MARGIN = timedelta(minutes=2)
EXACT_ORIGIN = "https://frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
GUARD_PLUGIN_VERSION = "1.0.5"
GUARD_PROOF_SCHEMA = 2
GUARD_ZIP_SHA256 = "f001bb217ae7aa16b2dd1f0cd08bcb0f6d825bb013c98e1a886ef1f2f436db74"
GUARD_ZIP_BYTES = 21035
GUARD_PLUGIN_PHP_SHA256 = "7d09ad8eb45e552e7dfb31ecf50b800ea50ea1c8074a8e1a899e375f47a2f887"
GUARD_PLUGIN_PHP_BYTES = 86320
GUARD_RUNTIME_MANIFEST_SHA256 = "23e1800e779ca7a4068c6eff090b9b53524cd3e3cefad9e53f5337ecfcefe565"
GUARD_RUNTIME_MANIFEST_BYTES = 4475
GUARD_ZIP_PATH = THIS_DIR / "media_mutation_guard" / "frpdepot-media-mutation-guard-1.0.5.zip"
# *** THE 1.0.5 SOURCE PINS POINT AT THE IMMUTABLE RELEASE SNAPSHOT, NOT THE WORKING TREE. ***
# 2026-08-21: the working tree moved to Guard 1.0.6 while 1.0.5 is still the INSTALLED
# plugin this tool drives. `released/1.0.5/` holds the exact bytes extracted from the
# pinned 1.0.5 ZIP, so this tool keeps proving the version that is actually live.
# Do NOT repoint these back at `frpdepot-media-mutation-guard/` -- that directory is
# now the NEXT version's source and its hashes deliberately differ.
GUARD_RELEASE_DIR = THIS_DIR / "media_mutation_guard" / "released" / "1.0.5"
GUARD_PLUGIN_PHP_PATH = GUARD_RELEASE_DIR / "frpdepot-media-mutation-guard.php"
GUARD_RUNTIME_MANIFEST_PATH = GUARD_RELEASE_DIR / "approved-media.json"
GUARD_TTL_SECONDS = 1800
GUARD_ADMIN_PATH = "/wp-admin/tools.php"
GUARD_POST_PATH = "/wp-admin/admin-post.php"
GUARD_PAGE_SLUG = "frpd-media-mutation-guard"
GUARD_ADMIN_URL = f"{EXACT_ORIGIN}{GUARD_ADMIN_PATH}?page={GUARD_PAGE_SLUG}"
GUARD_POST_URL = f"{EXACT_ORIGIN}{GUARD_POST_PATH}"
GUARD_PROOF_SELECTOR = "script#frpd-media-guard-proof[type='application/json']"
GUARD_STATUS_SELECTOR = "#frpd-mg-status"
GUARD_VERSION_SELECTOR = "#frpd-mg-version"
PLAN_DIR = ROOT / "Dado" / "20_Working" / "wordpress_product_family_media_plans"
STAGE_REGISTRY_DIR = PLAN_DIR / "stage-registry"
ATTEMPT_LEDGER_DIR = PLAN_DIR / "attempt-ledger"
RESERVATION_DIR = PLAN_DIR / "result-reservations"
RESULT_DIR = PLAN_DIR / "results"
JOURNAL_DIR = PLAN_DIR / "event-journal"
REGISTRY_KEY_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / (
    "FRPDepot-WordPress/product-family-media-registry.key"
)
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
APPROVED_MANIFEST_PATH = (
    ROOT / "Dado" / "20_Working" / "frp_manway" / "approved_gallery_20260820"
    / "approved_product_family_media_manifest_open_manway_update_20260820.json"
)
APPROVED_MANIFEST_SHA256 = "b0e020eaa9de9a43a64b2441e0b194b4972ace28cd84fe52cf92d8e6a72f05c7"
APPROVED_MANIFEST_CONTENT_SHA256 = "46af190c549e882af2160acb8e49880208529848651f65abc485710676c1f100"
GUARD_PRIVATE_EXCEPTION = {
    "attachment_id": 1832,
    "attached_file": "2026/03/HETRON-CR-Guide-2007_Ineos.pdf",
    "post_name": "hetron-cr-guide-2007_ineos",
    "post_status": "private",
    "mime_type": "application/pdf",
    "post_date_gmt": "2026-03-17 15:20:38",
    "protector_plugin_file": (
        "frpdepot-hetron-private-history/frpdepot-hetron-private-history.php"
    ),
    "protector_plugin_sha256": (
        "8c06b73a3a76ac2da7e7e9bba25c3f8a31ecdad917b30d436e41b32784b17116"
    ),
}


class FamilyMediaError(RuntimeError):
    """Closed refusal or permanently attributed one-attempt failure."""


class FamilyMediaIndeterminate(FamilyMediaError):
    """A side effect may have landed; the plan is permanently no-retry."""


def parse_exact_attachment_edit_url(value: str) -> int | None:
    """Return one exact attachment ID from an absolute or root-relative admin link.

    WordPress currently renders Media Library edit links as root-relative paths.
    Accept only that one relative form after proving the raw path is already the
    exact admin edit path; protocol-relative, dot-segment, encoded-path and
    ordinary relative forms remain refused. The resolved URL must still pass the
    exact same-origin, path, fragment and two-key query checks below.
    """
    raw = str(value)
    try:
        initial = urlsplit(raw)
    except ValueError:
        return None
    if raw.startswith("/") and not raw.startswith("//"):
        if (initial.scheme or initial.netloc or initial.username or initial.password
                or initial.path != media_base.POST_EDIT_PATH or initial.fragment):
            return None
        raw = urljoin(media_base.EXACT_ORIGIN + "/", raw)
    elif not raw.startswith("https://"):
        return None
    try:
        media_base.assert_origin(raw)
        parsed = urlsplit(raw)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (FamilyMediaError, media_base.MediaUploadError, ValueError):
        return None
    if (parsed.path or "") != media_base.POST_EDIT_PATH or parsed.fragment:
        return None
    if len(pairs) != 2:
        return None
    if sorted(key for key, _value in pairs) != ["action", "post"]:
        return None
    values = dict(pairs)
    if values.get("action") != "edit":
        return None
    try:
        return media_base._require_positive_int(values.get("post"), "the attachment id")
    except media_base.MediaUploadError:
        return None


def is_attachment_edit_candidate_url(value: str) -> bool:
    """Whether a row link claims to be an attachment edit navigation.

    WordPress puts ordinary Delete and public View links in the same title cell,
    so not every link there is an edit candidate. Any row link that claims
    `action=edit` is a candidate regardless of origin or browser normalization
    and must pass the exact parser. Only an exact root-relative admin path is
    accepted; credentialed, Unicode-host, protocol-relative, ordinary-relative,
    percent-encoded and dot-segment forms fail closed.
    """
    try:
        # Resolve relative syntax to parse its query as a browser would, but do
        # NOT apply an origin gate here. A foreign, credentialed or normalized
        # edit claim is ambiguity, not a harmless title-cell link. The exact
        # parser remains the only accepting parser.
        resolved = urljoin(media_base.library_page_url(1), str(value))
        parsed = urlsplit(resolved)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return False
    return any(key == "action" and item == "edit" for key, item in pairs)


def image_record(position: int, path: str, byte_count: int, sha256: str,
                 width: int, height: int) -> dict[str, Any]:
    return {
        "position": position,
        "path": path,
        "filename": Path(path).name,
        "bytes": byte_count,
        "sha256": sha256,
        "width": width,
        "height": height,
        "format": "PNG",
        "mode": "RGB",
    }


FAMILY_SPECS: dict[str, dict[str, Any]] = {
    "stub_flange": {
        "product_id": 1368,
        "label": "FRP Stub Flange",
        "sku": "ZOHO-GROUP-7C4F5F43F56E",
        "type": "variable",
        "status": "publish",
        "permalink": "https://frpdepots.com/product/frp-stub-flange/",
        "images": (
            image_record(1, r"C:\FRPDepot\Dado\20_Working\stub_flange_chatgpt_views_v2_20260820\01_authentic_source_hero.png", 895251, "aa9c8da37cc4a1ee98b5f0b2c77dd5b369c327a583412938778210562936b3da", 1024, 1024),
            image_record(2, r"C:\FRPDepot\Dado\20_Working\stub_flange_chatgpt_views_v2_20260820\02_chatgpt_overhead_face.png", 1477996, "25f3c3ee9d3b3ae11297d4d6d4b9c8c1fcfc52806f1f585e9b9296d2f5ee2156", 1254, 1254),
            image_record(3, r"C:\FRPDepot\Dado\20_Working\stub_flange_chatgpt_views_v2_20260820\03_chatgpt_low_profile.png", 1462525, "00050f0ec36fb814f35e696572e5af3f38000aff1bd721ac85c5f3a265591ed3", 1306, 1204),
            image_record(4, r"C:\FRPDepot\Dado\20_Working\stub_flange_chatgpt_views_v2_20260820\04_chatgpt_straight_mating_face.png", 1968000, "3bd2cfb0e102c9b9dece6a67d2d0f463bcf418fc88d429ec22bee92ede8aebc6", 1254, 1254),
            image_record(5, r"C:\FRPDepot\Dado\20_Working\stub_flange_chatgpt_views_v2_20260820\05_chatgpt_laminate_detail.png", 2152308, "20dce528fd560c77f73eb96d11ca35fc72db91670bf73ad8ca44d6accc028d07", 1122, 1402),
            image_record(6, r"C:\FRPDepot\Dado\20_Working\stub_flange_chatgpt_views_v2_20260820\06_chatgpt_opposite_high_angle.png", 1583619, "010aaec08c1eca0117c0c30da92c51990a99e2ab1900cc01d872d584f6a711e2", 1254, 1254),
        ),
    },
    "open_manway": {
        "product_id": 1397,
        "label": "FRP MANWAY",
        "sku": "ZOHO-GROUP-3DCCB43DB14F",
        "type": "variable",
        "status": "publish",
        "permalink": "https://frpdepots.com/product/frp-manway/",
        "images": (
            image_record(1, r"C:\FRPDepot\Dado\20_Working\frp_manway\approved_gallery_20260820\01_manway_premium_hero.png", 1750111, "472b5e5b0aba9a7201444524c559e6797c266a0de008d7bc70b4f8ef1938d0cd", 1254, 1254),
            image_record(2, r"C:\FRPDepot\Dado\20_Working\frp_manway\approved_gallery_20260820\02_manway_top_oblique.png", 1821849, "0fd7e2c62fb88d425cdfaf949415520ac89a5d95d07cf75b8e9791d308ea8181", 1254, 1254),
            image_record(3, r"C:\FRPDepot\Dado\20_Working\frp_manway\approved_gallery_20260820\03_manway_low_side_angle.png", 1805796, "40ac3a69f5903d53f6fd71f952ac63ed237abc1a37a17c800a345c06211c8e63", 1402, 1122),
            image_record(4, r"C:\FRPDepot\Dado\20_Working\frp_manway\approved_gallery_20260820\04_manway_flange_bore_detail.png", 2118498, "d740be620cf0c083e7e399127c2205dd6f7b9e73fb08fdee31e1b79568d75950", 1402, 1122),
            image_record(5, r"C:\FRPDepot\Dado\20_Working\frp_manway\approved_gallery_20260820\05_manway_opposite_face.png", 1751997, "c54c9fd74fbdc55d0b9295b1bb7fb1dd0146cee645f455025d1b1895f21a543a", 1254, 1254),
            image_record(6, r"C:\FRPDepot\Dado\20_Working\frp_manway\approved_gallery_20260820\06_manway_laminate_detail.png", 2347237, "bfde2b6ab1f1de5cc6ad24b9aa556ef1ed46bd9cd43b34a2a5b1c77bb612e0e7", 1402, 1122),
        ),
    },
    "manway_cover": {
        "product_id": 1411,
        "label": "FRP MANWAY COVER",
        "sku": "ZOHO-GROUP-F37B494873F8",
        "type": "variable",
        "status": "publish",
        "permalink": "https://frpdepots.com/product/frp-manway-cover/",
        "images": (
            image_record(1, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\manway_cover_real_source_batch_20260815\01_manway_cover_real_hero.png", 328750, "50aeae1216f557cbbb77b905ecd51ad862d86b7f86807066fea5c823437d273a", 1024, 1024),
            image_record(2, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\manway_cover_real_source_batch_20260815\02_manway_cover_real_side.png", 271666, "52731ad56fb30d67b80a05ab4d6709f64e5c16f8f0a6c91e5f59e148ac60b187", 1024, 1024),
            image_record(3, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\manway_cover_real_source_batch_20260815\03_manway_cover_real_lid_handle_detail.png", 232784, "89f32db0131af8b54d791d2d78cdb71a804efe38389cce95748926222b177497", 1024, 1024),
            image_record(4, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\manway_cover_real_source_batch_20260815\04_manway_cover_real_factory_context.png", 1306617, "9bc666b86cb93eb14e88e2d642feb67145fd44cb4bd0740fa00da15d733a4362", 1024, 1024),
        ),
    },
    "elbow_90": {
        "product_id": 1423,
        "label": "FRP ELBOW 90",
        "sku": "ZOHO-GROUP-F568EAC33515",
        "type": "variable",
        "status": "publish",
        "permalink": "https://frpdepots.com/product/frp-elbow-90/",
        "images": (
            image_record(1, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\elbow_90_family_batch_20260815\01_elbow_90_approved_hero.png", 1885731, "61a7044dc7d104ff8073b7d3760453402ec58dc3fb3c9254c316139f311c31c2", 1254, 1254),
            image_record(2, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\elbow_90_family_batch_20260815\02_elbow_90_bore_transition_detail.png", 675410, "dc1aeef45aa609f36bd0212ed876e034ff45e27d9eeeb3eb9f1ba1ef1c411a9a", 1024, 1024),
            image_record(3, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\elbow_90_family_batch_20260815\03_elbow_90_real_factory_inventory.png", 795516, "a9513466ca8ef91e2eca55480c3b1f3abecf5bdea93613bfaa553a1ee3ec1b36", 1024, 1024),
            image_record(4, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\elbow_90_family_batch_20260815\04_elbow_90_real_standing_group.png", 990907, "9ea134fd38ef70765c8b2eacc5621f3fce5c4b88a86f7cc5d353931bbfc06516", 1024, 1024),
        ),
    },
    "pipe": {
        "product_id": 1455,
        "label": "FRP Pipe - Filament Wound",
        "sku": "ZOHO-GROUP-4AC07D30494A",
        "type": "variable",
        "status": "publish",
        "permalink": "https://frpdepots.com/product/frp-fw-pipe/",
        "images": (
            image_record(1, r"C:\FRPDepot\Dado\20_Working\clean_pipe_gallery_20260819\01_pipe_catalogue_single_diagonal_hero.png", 675601, "ab7a27849ff447ab1352280d41c7929e76f096b74ea6251bea0ef58d5810868c", 1024, 1024),
            image_record(2, r"C:\FRPDepot\Dado\20_Working\clean_pipe_gallery_20260819\02_pipe_catalogue_single_vertical_profile.png", 932123, "6525c5b71a189fd5b0485d61f4edf227bad46134e66b96df2fffcbcbcfc5aca2", 1024, 1024),
            image_record(3, r"C:\FRPDepot\Dado\20_Working\clean_pipe_gallery_20260819\03_pipe_catalogue_open_end_wall_detail.png", 729543, "e119539190a741b737be26750d3f157285a662ef200b77275a5ea8e1f5a6b403", 1024, 1024),
            image_record(4, r"C:\FRPDepot\Dado\20_Working\clean_pipe_gallery_20260819\04_pipe_catalogue_size_range_group.png", 356682, "202228668fff9438adaf6d7198fe7a2357cc048535c837fb0b38deb54d5b762a", 1024, 1024),
        ),
    },
}

FAMILY_KEYS = tuple(FAMILY_SPECS)
FAMILY_IMAGE_COUNTS = {
    "stub_flange": 6,
    "open_manway": 6,
    "manway_cover": 4,
    "elbow_90": 4,
    "pipe": 4,
}
STUB_FLANGE_REQUIRED_BEFORE_GALLERY_IDS = (4849, 4850, 4851, 4852)
REUSED_EXISTING_EXCEPTION = {
    "family": "stub_flange",
    "product_id": 1368,
    "position": 1,
    "attachment_id": 4849,
    "actual_filename": "01_stub_flange_real_source_hero-1.png",
    "source_basename": "01_stub_flange_real_source_hero-1.png",
    "approved_filename": "01_authentic_source_hero.png",
    "bytes": 895251,
    "sha256": "aa9c8da37cc4a1ee98b5f0b2c77dd5b369c327a583412938778210562936b3da",
    "width": 1024,
    "height": 1024,
    "format": "PNG",
    "mode": "RGB",
}
FIXED_PRODUCT_IDS = frozenset(int(spec["product_id"]) for spec in FAMILY_SPECS.values())
ALL_FIXED_PATHS = frozenset(Path(record["path"]).resolve()
                            for spec in FAMILY_SPECS.values() for record in spec["images"])
ALL_FIXED_FILENAMES = frozenset(record["filename"]
                                for spec in FAMILY_SPECS.values() for record in spec["images"])
ALL_FIXED_HASHES = {record["sha256"]: f"{family}:{record['filename']}"
                    for family, spec in FAMILY_SPECS.items() for record in spec["images"]}
ALL_FIXED_STEMS = frozenset(media_base._normalise_stem(name) for name in ALL_FIXED_FILENAMES)

PERMITTED_PRODUCT_READBACK_DRIFT_FIELDS = frozenset({
    "images", "date_modified", "date_modified_gmt", "yoast_head", "yoast_head_json",
})

def expected_image_count(key: str) -> int:
    if key not in FAMILY_IMAGE_COUNTS:
        raise FamilyMediaError("REFUSED: family must be one of: " + ", ".join(FAMILY_KEYS))
    return FAMILY_IMAGE_COUNTS[key]


def reuse_exception(key: str) -> dict[str, Any] | None:
    family_spec(key)
    if key != REUSED_EXISTING_EXCEPTION["family"]:
        return None
    hero = FAMILY_SPECS[key]["images"][0]
    expected = {
        "family": key,
        "product_id": FAMILY_SPECS[key]["product_id"],
        "position": hero["position"],
        "attachment_id": 4849,
        "actual_filename": "01_stub_flange_real_source_hero-1.png",
        "source_basename": "01_stub_flange_real_source_hero-1.png",
        "approved_filename": hero["filename"],
        "bytes": hero["bytes"],
        "sha256": hero["sha256"],
        "width": hero["width"],
        "height": hero["height"],
        "format": hero["format"],
        "mode": hero["mode"],
    }
    if REUSED_EXISTING_EXCEPTION != expected:
        raise FamilyMediaError("REFUSED: the one fixed existing-media reuse exception drifted.")
    return copy.deepcopy(expected)


def upload_image_records(key: str) -> list[dict[str, Any]]:
    spec = family_spec(key)
    reused = reuse_exception(key)
    rows = [copy.deepcopy(row) for row in spec["images"]
            if reused is None or row["position"] != reused["position"]]
    wanted_positions = (list(range(2, expected_image_count(key) + 1))
                        if reused is not None else list(range(1, expected_image_count(key) + 1)))
    if [row["position"] for row in rows] != wanted_positions:
        raise FamilyMediaError(f"REFUSED: fixed upload positions drifted for {key}.")
    return rows


def expected_upload_count(key: str) -> int:
    return len(upload_image_records(key))


def guard_reuse_proof(key: str) -> dict[str, Any] | None:
    reused = reuse_exception(key)
    if reused is None:
        return None
    return {
        field: reused[field] for field in (
            "family", "product_id", "position", "attachment_id",
            "actual_filename", "approved_filename", "bytes", "sha256",
        )
    } | {
        "must_be_current_product_hero": True,
        "required_current_raw_meta": {
            "_thumbnail_id": "4849",
            "_product_image_gallery": "4850,4851,4852",
        },
        "upload_positions": [2, 3, 4, 5, 6],
    }


def expected_public_basename(key: str, image: dict[str, Any]) -> str:
    reused = reuse_exception(key)
    if reused is not None and image["position"] == reused["position"]:
        return reused["actual_filename"]
    return str(image["filename"])


def risk_disclosure(key: str) -> str:
    count = expected_image_count(key)
    upload_count = expected_upload_count(key)
    reused = reuse_exception(key)
    reuse_text = (
        "The existing Stub Flange position-1 attachment 4849 is reused byte-for-byte without "
        "rename, metadata edit, detach, delete, or upload; " if reused is not None else ""
    )
    return (
        "NOT ATOMIC; NO ROLLBACK. One server-side guarded-commit acquisition happens first, "
        + reuse_text
        + f"then {upload_count} independent WordPress uploads, then one independent WooCommerce gallery PUT "
        f"containing only the {count} returned media IDs, then one guard-completion write. If "
        "upload N fails, it may itself have landed and earlier verified uploads remain live but "
        "unattached; the guard reserves each fixed filename durably before WordPress moves the file. "
        "Later uploads do "
        "not happen, the product gallery is not written, and the plan is permanently no-retry. "
        "If the gallery PUT lands but fresh API, media, public-page, JavaScript-error and "
        "protected-field checks do not all pass, the product may be changed and the plan is "
        "permanently indeterminate. Replaced gallery attachments remain in the Media Library; "
        "the tool does not delete or edit them. A failure after guard acquisition can leave the "
        "fixed or poisoned guard active until its authoritative 30-minute database expiry, blocking other "
        "attachment mutations during that interval. The tool cannot delete, detach, roll back, "
        "retry, clear, or clean up anything. Every attempted guard transition, upload and possible "
        "landed-media state is recorded in a hash-keyed append-only attempt journal. The guard "
        "serializes the fixed product's gallery metadata for the active family and atomically claims "
        "the one exact images-only PUT only when its non-secret If-Match SHA-256 still proves the complete "
        "pre-write gallery ID list. Independent writes to unrelated product fields are not serialized, "
        "but the images-only PUT cannot overwrite them; fresh protected reads before the PUT, after the "
        "PUT and after guard completion still fail closed on any such drift. The live guard UI "
        "exposes exact version, health, family keys and proof schemas but no source-byte or manifest-"
        "digest endpoint; live-byte identity relies on the previously pinned exact ZIP installation "
        "provenance plus those live checks."
    )


def writes_if_committed(key: str, product_id: int) -> list[str]:
    count = expected_image_count(key)
    upload_count = expected_upload_count(key)
    rows = [
        "one fixed server-side guarded-commit acquisition",
    ]
    if reuse_exception(key) is not None:
        rows.append(
            "reuse existing attachment 4849 at position 1 without any attachment mutation"
        )
    rows += [
        f"{upload_count} independent authenticated WordPress media uploads",
        f"one PUT /products/{product_id} containing only {count} image IDs",
        "one fixed guard-completion state transition after complete verification",
    ]
    return rows


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"ts": utc_now().isoformat(), "action": action,
                                 "evidence": evidence}, ensure_ascii=True) + "\n")


def require_approval(value: Any) -> None:
    if not isinstance(value, str) or not secrets.compare_digest(value, APPROVAL_WORD):
        raise FamilyMediaError("REFUSED: approval must be exact unpadded uppercase APPROVED.")


def validate_guard_manifest_contract() -> dict[str, Any]:
    try:
        zip_raw = GUARD_ZIP_PATH.read_bytes()
        plugin_raw = GUARD_PLUGIN_PHP_PATH.read_bytes()
        raw = GUARD_RUNTIME_MANIFEST_PATH.read_bytes()
        parsed = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FamilyMediaError("REFUSED: fixed guard package artifacts are unreadable.") from exc
    actual_zip_sha256 = hashlib.sha256(zip_raw).hexdigest()
    actual_plugin_sha256 = hashlib.sha256(plugin_raw).hexdigest()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if (len(zip_raw) != GUARD_ZIP_BYTES
            or len(plugin_raw) != GUARD_PLUGIN_PHP_BYTES
            or len(raw) != GUARD_RUNTIME_MANIFEST_BYTES
            or not secrets.compare_digest(actual_zip_sha256, GUARD_ZIP_SHA256)
            or not secrets.compare_digest(actual_plugin_sha256, GUARD_PLUGIN_PHP_SHA256)):
        raise FamilyMediaError("REFUSED: fixed guard package or PHP source hash changed.")
    if not secrets.compare_digest(actual_sha256, GUARD_RUNTIME_MANIFEST_SHA256):
        raise FamilyMediaError("REFUSED: fixed guard runtime manifest hash changed.")
    if (not isinstance(parsed, dict)
            or set(parsed) != {"schema", "source_manifest_sha256", "fixed_reuse", "families"}
            or parsed.get("schema") != 2
            or parsed.get("source_manifest_sha256") != APPROVED_MANIFEST_SHA256
            or parsed.get("fixed_reuse") != guard_reuse_proof("stub_flange")
            or not isinstance(parsed.get("families"), dict)
            or set(parsed["families"]) != set(FAMILY_KEYS)
            or "fnpt" in parsed["families"]):
        raise FamilyMediaError("REFUSED: fixed guard runtime manifest contract changed.")
    for key in FAMILY_KEYS:
        spec = FAMILY_SPECS[key]
        expected = {
            "product_id": spec["product_id"],
            "images": [{"position": row["position"], "filename": row["filename"],
                        "bytes": row["bytes"], "sha256": row["sha256"]}
                       for row in spec["images"]],
        }
        if parsed["families"].get(key) != expected:
            raise FamilyMediaError(
                f"REFUSED: guard runtime manifest disagrees with fixed family {key}."
            )
    return {
        "schema": 2,
        "zip_sha256": actual_zip_sha256,
        "plugin_php_sha256": actual_plugin_sha256,
        "sha256": actual_sha256,
        "source_manifest_sha256": APPROVED_MANIFEST_SHA256,
        "families": list(FAMILY_KEYS),
        "family_image_counts": dict(FAMILY_IMAGE_COUNTS),
        "fixed_reuse": guard_reuse_proof("stub_flange"),
        "fnpt_present": False,
    }


def guard_contract(key: str) -> dict[str, Any]:
    spec = family_spec(key)
    return {
        "plugin_version": GUARD_PLUGIN_VERSION,
        "proof_schema": GUARD_PROOF_SCHEMA,
        "runtime_manifest": validate_guard_manifest_contract(),
        "live_identity": {
            "checks": ["exact_version", "exact_health", "exact_family_keys",
                       "closed_proof_schema", "exact_reused_existing_proof"],
            "live_byte_digest_exposed": False,
            "byte_identity_basis": "pinned_exact_zip_installation_provenance",
        },
        "ttl_seconds": GUARD_TTL_SECONDS,
        "minimum_completion_margin_seconds": int(MIN_GUARD_COMPLETION_MARGIN.total_seconds()),
        "family": key,
        "product_id": spec["product_id"],
        "fixed_private_exception": copy.deepcopy(GUARD_PRIVATE_EXCEPTION),
        "image_count": expected_image_count(key),
        "upload_count": expected_upload_count(key),
        "fixed_reuse": guard_reuse_proof(key),
        "flow": ["atomic_snapshot", "acquire", "exact_family_uploads",
                 "post_acquire_owner_snapshot", "guarded_snapshot",
                 "images_only_put", "complete", "post_completion_public_verification"],
    }


def assert_guard_admin_url(url: str) -> None:
    media_base.assert_origin(url)
    parsed = urlsplit(str(url))
    if parsed.fragment:
        raise FamilyMediaError("REFUSED: guard administration URL carries a fragment.")
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    if len({key for key, _value in pairs}) != len(pairs):
        raise FamilyMediaError("REFUSED: guard administration URL repeats a query key.")
    if parsed.path == GUARD_ADMIN_PATH and pairs == [("page", GUARD_PAGE_SLUG)]:
        return
    if parsed.path == GUARD_POST_PATH and not pairs:
        return
    raise FamilyMediaError("REFUSED: WordPress guard URL is outside its two exact fixed routes.")


def assert_product_family_admin_url(url: str) -> None:
    if urlsplit(str(url)).path in media_base.ALLOWED_ADMIN_PATHS:
        media_base.assert_admin_url(url)
        return
    assert_guard_admin_url(url)


def _guard_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise FamilyMediaError(f"REFUSED: guard {label} timestamp is invalid.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FamilyMediaError(f"REFUSED: guard {label} timestamp is invalid.") from exc
    if parsed.tzinfo is None:
        raise FamilyMediaError(f"REFUSED: guard {label} timestamp has no timezone.")
    return value


def _guard_match_rows(value: Any, label: str, key: str) -> list[dict[str, int]]:
    if not isinstance(value, list):
        raise FamilyMediaError(f"REFUSED: guard {label} evidence is not a list.")
    rows: list[dict[str, int]] = []
    for row in value:
        if (not isinstance(row, dict) or set(row) != {"attachment_id", "fixed_position"}
                or type(row["attachment_id"]) is not int or row["attachment_id"] <= 0
                or type(row["fixed_position"]) is not int
                or row["fixed_position"] not in range(1, expected_image_count(key) + 1)):
            raise FamilyMediaError(f"REFUSED: guard {label} evidence has an invalid row.")
        rows.append({"attachment_id": row["attachment_id"],
                     "fixed_position": row["fixed_position"]})
    if len({(row["attachment_id"], row["fixed_position"]) for row in rows}) != len(rows):
        raise FamilyMediaError(f"REFUSED: guard {label} evidence repeats a row.")
    return rows


def _guard_private_exception_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or value != [GUARD_PRIVATE_EXCEPTION]:
        raise FamilyMediaError(
            "REFUSED: guard private-attachment exception evidence is not exact."
        )
    return copy.deepcopy(value)


def validate_guard_snapshot_proof(value: Any, key: str, mode: str,
                                  guard_active: bool) -> dict[str, Any]:
    base_keys = {
        "schema", "plugin_version", "mode", "family", "generated_utc",
        "attachment_total", "hashed_total", "total_bytes", "snapshot_sha256",
        "complete", "failures", "private_exceptions", "name_conflicts", "hash_conflicts",
        "fixed_matches", "guard_active",
    }
    if mode in {"guard_acquired", "guarded_snapshot"}:
        base_keys |= {"guard_expires_utc", "reserved_uploads"}
    if not isinstance(value, dict) or set(value) != base_keys:
        raise FamilyMediaError("REFUSED: guard snapshot proof has the wrong closed schema.")
    if (value["schema"] != GUARD_PROOF_SCHEMA or value["plugin_version"] != GUARD_PLUGIN_VERSION
            or value["mode"] != mode or value["family"] != key
            or value["guard_active"] is not guard_active):
        raise FamilyMediaError("REFUSED: guard snapshot identity or state is wrong.")
    _guard_timestamp(value["generated_utc"], "generated")
    for field in ("attachment_total", "hashed_total", "total_bytes"):
        if type(value[field]) is not int or value[field] < 0:
            raise FamilyMediaError("REFUSED: guard snapshot counters are invalid.")
    digest = value["snapshot_sha256"]
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)):
        raise FamilyMediaError("REFUSED: guard snapshot SHA-256 is invalid.")
    if type(value["complete"]) is not bool or not isinstance(value["failures"], list):
        raise FamilyMediaError("REFUSED: guard snapshot completeness evidence is invalid.")
    allowed_failure_reasons = {
        "private_attachment_proof_failed", "unreadable_original", "hash_failed",
    }
    failure_ids: set[int] = set()
    for failure in value["failures"]:
        if (not isinstance(failure, dict) or set(failure) != {"attachment_id", "reason"}
                or type(failure["attachment_id"]) is not int or failure["attachment_id"] <= 0
                or failure["attachment_id"] in failure_ids
                or failure["reason"] not in allowed_failure_reasons):
            raise FamilyMediaError("REFUSED: guard snapshot failure evidence is invalid.")
        failure_ids.add(failure["attachment_id"])
    private_exceptions = _guard_private_exception_rows(value["private_exceptions"])
    if value["attachment_total"] != (
            value["hashed_total"] + len(private_exceptions) + len(value["failures"])):
        raise FamilyMediaError("REFUSED: guard snapshot counters do not reconcile.")
    _guard_match_rows(value["name_conflicts"], "name-conflict", key)
    _guard_match_rows(value["hash_conflicts"], "hash-conflict", key)
    _guard_match_rows(value["fixed_matches"], "fixed-match", key)

    if mode in {"guard_acquired", "guarded_snapshot"}:
        _guard_timestamp(value["guard_expires_utc"], "expiry")
        if (type(value["reserved_uploads"]) is not int
                or not 0 <= value["reserved_uploads"] <= expected_upload_count(key)):
            raise FamilyMediaError("REFUSED: guard reserved-upload count is invalid.")
    return copy.deepcopy(value)


def require_empty_guard_snapshot(proof: Any, key: str, mode: str,
                                 duplicate: dict[str, Any] | None = None) -> dict[str, Any]:
    checked = validate_guard_snapshot_proof(
        proof, key, mode, mode in {"guard_acquired", "guarded_snapshot"}
    )
    if checked["failures"]:
        details = ", ".join(
            f"{row['attachment_id']}:{row['reason']}" for row in checked["failures"]
        )
        raise FamilyMediaError(
            f"REFUSED: server-side guard snapshot has unreadable attachment evidence: {details}."
        )
    reused = reuse_exception(key)
    expected_name_conflicts: list[dict[str, int]] = []
    expected_fixed_matches = ([{
        "attachment_id": reused["attachment_id"],
        "fixed_position": reused["position"],
    }] if reused is not None else [])
    expected_hash_conflicts = copy.deepcopy(expected_fixed_matches)
    if (checked["complete"] is not True or checked["failures"]
            or checked["attachment_total"] != checked["hashed_total"] + 1
            or checked["name_conflicts"] != expected_name_conflicts
            or checked["hash_conflicts"] != expected_hash_conflicts
            or checked["fixed_matches"] != expected_fixed_matches):
        raise FamilyMediaError("REFUSED: server-side guard snapshot did not prove duplicate absence.")
    if duplicate is not None and checked["attachment_total"] != duplicate.get("library_total"):
        raise FamilyMediaError("REFUSED: browser and server attachment totals disagree.")
    if mode == "guard_acquired" and checked["reserved_uploads"] != 0:
        raise FamilyMediaError("REFUSED: newly acquired guard already reserves an upload.")
    return checked


def require_guarded_upload_snapshot(proof: dict[str, Any], key: str,
                                    baseline_total: int,
                                    attachment_ids: list[int]) -> dict[str, Any]:
    checked = validate_guard_snapshot_proof(proof, key, "guarded_snapshot", True)
    count = expected_image_count(key)
    if len(attachment_ids) != count or len(set(attachment_ids)) != count:
        raise FamilyMediaError("REFUSED: guarded snapshot attachment set has the wrong exact count.")
    reused = reuse_exception(key)
    wanted_fixed = [{"attachment_id": attachment_id, "fixed_position": position}
                    for position, attachment_id in enumerate(attachment_ids, 1)]
    wanted_uploads = [row for row in wanted_fixed
                      if reused is None or row["fixed_position"] != reused["position"]]
    upload_count = expected_upload_count(key)
    if (checked["complete"] is not True or checked["failures"]
            or checked["attachment_total"] != baseline_total + upload_count
            or checked["hashed_total"] != baseline_total + upload_count - 1
            or checked["reserved_uploads"] != upload_count
            or sorted(checked["name_conflicts"], key=lambda row: row["fixed_position"])
                != wanted_uploads
            or sorted(checked["hash_conflicts"], key=lambda row: row["fixed_position"])
                != wanted_fixed
            or sorted(checked["fixed_matches"], key=lambda row: row["fixed_position"])
                != wanted_fixed):
        raise FamilyMediaError("Guarded snapshot does not prove the exact family uploaded attachments.")
    return checked


def validate_guard_completion_proof(value: Any, key: str,
                                    attachment_ids: list[int]) -> dict[str, Any]:
    expected_keys = {
        "schema", "plugin_version", "mode", "family", "product_id",
        "attachment_ids", "attachment_total", "snapshot_sha256",
    }
    spec = family_spec(key)
    count = expected_image_count(key)
    if (not isinstance(value, dict) or set(value) != expected_keys
            or value["schema"] != GUARD_PROOF_SCHEMA or value["plugin_version"] != GUARD_PLUGIN_VERSION
            or value["mode"] != "guard_completed" or value["family"] != key
            or value["product_id"] != spec["product_id"]
            or value["attachment_ids"] != attachment_ids
            or len(attachment_ids) != count or len(set(attachment_ids)) != count
            or type(value["attachment_total"]) is not int or value["attachment_total"] < count):
        raise FamilyMediaError("REFUSED: guard completion proof is not exact.")
    digest = value["snapshot_sha256"]
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)):
        raise FamilyMediaError("REFUSED: guard completion SHA-256 is invalid.")
    return copy.deepcopy(value)


def require_authorization_margin(plan: dict[str, Any]) -> None:
    expires = datetime.fromisoformat(str(plan["expires_utc"]))
    if expires - utc_now() < MIN_AUTHORIZATION_MARGIN:
        raise FamilyMediaError(
            "REFUSED: fewer than two minutes remain before plan expiry; stage a fresh plan."
        )


def require_guard_completion_margin(proof: dict[str, Any]) -> None:
    expires = datetime.fromisoformat(str(proof["guard_expires_utc"]).replace("Z", "+00:00"))
    if expires - utc_now() < MIN_GUARD_COMPLETION_MARGIN:
        raise FamilyMediaError(
            "REFUSED: fewer than two minutes remain before the active guard expires."
        )


def family_spec(key: str) -> dict[str, Any]:
    if key not in FAMILY_SPECS:
        raise FamilyMediaError("REFUSED: family must be one of: " + ", ".join(FAMILY_KEYS))
    spec = FAMILY_SPECS[key]
    count = expected_image_count(key)
    if (not isinstance(spec.get("images"), tuple) or len(spec["images"]) != count
            or [row.get("position") for row in spec["images"]] != list(range(1, count + 1))):
        raise FamilyMediaError(f"REFUSED: fixed image count/order is invalid for {key}.")
    return spec


def validate_local_images(key: str) -> list[dict[str, Any]]:
    spec = family_spec(key)
    evidence: list[dict[str, Any]] = []
    for expected in spec["images"]:
        path = Path(expected["path"])
        resolved = path.resolve()
        if resolved not in ALL_FIXED_PATHS or resolved != path.resolve():
            raise FamilyMediaError("REFUSED: an image escaped the fixed allowlist.")
        if not path.is_file() or media_base._is_reparse_point(path):
            raise FamilyMediaError(f"REFUSED: fixed image is missing, non-file, or reparse point: {path}")
        stat = path.stat()
        if stat.st_size != expected["bytes"]:
            raise FamilyMediaError(f"REFUSED: byte size changed for {expected['filename']}.")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if not secrets.compare_digest(digest, expected["sha256"]):
            raise FamilyMediaError(f"REFUSED: SHA-256 changed for {expected['filename']}.")
        actual = media_base._verify_image_bytes(path)
        wanted = (expected["format"], expected["mode"], expected["width"], expected["height"])
        if actual != wanted:
            raise FamilyMediaError(f"REFUSED: format/mode/dimensions changed for {expected['filename']}.")
        evidence.append({
            "position": expected["position"], "path": str(path),
            "filename": expected["filename"], "bytes": expected["bytes"],
            "sha256": expected["sha256"], "width": expected["width"],
            "height": expected["height"], "format": expected["format"],
            "mode": expected["mode"], "regular_file": True, "reparse_point": False,
        })
    wanted_positions = list(range(1, expected_image_count(key) + 1))
    if [row["position"] for row in evidence] != wanted_positions:
        raise FamilyMediaError(
            f"REFUSED: {key} must contain exactly positions 1-{expected_image_count(key)}."
        )
    return evidence


def verified_upload_payload(expected: dict[str, Any]) -> dict[str, Any]:
    """Read once from a verified regular handle and upload these exact bytes."""
    path = Path(str(expected.get("path") or ""))
    resolved = path.resolve()
    if resolved not in ALL_FIXED_PATHS or expected.get("filename") != path.name:
        raise FamilyMediaError("REFUSED: upload payload escaped the fixed file allowlist.")
    if not path.is_file() or media_base._is_reparse_point(path):
        raise FamilyMediaError("REFUSED: upload source is missing, non-file, or a reparse point.")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise FamilyMediaError("REFUSED: fixed upload source could not be safely opened.") from exc
    try:
        opened = os.fstat(descriptor)
        current = path.stat()
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
                current.st_dev, current.st_ino, current.st_size):
            raise FamilyMediaError("REFUSED: upload source changed while it was opened.")
        if media_base._is_reparse_point(path):
            raise FamilyMediaError("REFUSED: upload source became a reparse point.")
        maximum = int(expected["bytes"])
        chunks: list[bytes] = []
        total = 0
        while total <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(data) != int(expected["bytes"]):
        raise FamilyMediaError("REFUSED: upload source byte size changed immediately before upload.")
    digest = hashlib.sha256(data).hexdigest()
    if not secrets.compare_digest(digest, str(expected["sha256"])):
        raise FamilyMediaError("REFUSED: upload source SHA-256 changed immediately before upload.")
    mime_type = mimetypes.guess_type(path.name)[0]
    if mime_type != "image/png":
        raise FamilyMediaError("REFUSED: fixed upload source is not a PNG payload.")
    return {"name": path.name, "mimeType": mime_type, "buffer": data}


def validate_approved_manifest() -> dict[str, Any]:
    path = APPROVED_MANIFEST_PATH
    if not path.is_file() or media_base._is_reparse_point(path):
        raise FamilyMediaError("REFUSED: approved collection manifest is missing or a reparse point.")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if not secrets.compare_digest(digest, APPROVED_MANIFEST_SHA256):
        raise FamilyMediaError("REFUSED: approved collection manifest SHA-256 changed.")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FamilyMediaError("REFUSED: approved collection manifest is unreadable.") from exc
    expected_approval = {
        "exact_user_message": "APPROVED",
        "scope": "six-family working image collection only",
        "not_authorized": ["publication", "upload", "WooCommerce write", "Drive write", "email"],
    }
    expected_pipe_update = {
        "exact_user_message": "Yes stage the update",
        "scope": (
            "replace only WooCommerce product 1455 working gallery with four clean "
            "2026 catalogue pipe images; stage only"
        ),
        "not_authorized": ["publication", "upload", "WooCommerce write", "Drive write", "email"],
    }
    expected_stub_update = {
        "exact_user_message": "much better, proceed",
        "scope": "six reviewed Stub Flange images; staging only",
        "review_sheet": {
            "path": (
                r"C:\FRPDepot\Dado\20_Working\stub_flange_chatgpt_views_v2_20260820"
                r"\00_stub_flange_chatgpt_six_view_review.jpg"
            ),
            "sha256": "3d3fbbb409ff3a4eea810cbc9c726a79f623012acc23f7ee621cec4960831478",
        },
        "not_authorized": [
            "publication", "upload", "WordPress write", "WooCommerce write",
            "website write", "Drive write", "Zoho write", "email",
        ],
    }
    expected_manway_update = {
        "exact_user_message": "Yes",
        "scope": "six reviewed FRP Manway images; staging only",
        "review_evidence": {
            "path": (
                r"C:\FRPDepot\Dado\20_Working\frp_manway\approved_gallery_20260820"
                r"\approved_gallery_manifest.json"
            ),
            "sha256": "abac2f99e4b3dd5f292097b06f36f883a2d866a3c8d57c289dfc2dcb76aa3d45",
        },
        "order": "image 1 hero; images 2-6 gallery in shown order",
        "not_authorized": [
            "publication", "upload", "WordPress write", "WooCommerce write",
            "website write", "Drive write", "Zoho write", "email",
        ],
    }
    expected_fnpt = {
        "status": "approved existing benchmark; excluded from the five-family media tool",
        "separate_plan_required": True,
    }
    if (manifest.get("schema_version") != 1
            or manifest.get("status") != "APPROVED_WORKING_COLLECTION_NOT_PUBLISHED"
            or manifest.get("approval") != expected_approval
            or manifest.get("pipe_update_instruction") != expected_pipe_update
            or manifest.get("stub_flange_update_instruction") != expected_stub_update
            or manifest.get("open_manway_update_instruction") != expected_manway_update
            or manifest.get("fnpt") != expected_fnpt):
        raise FamilyMediaError("REFUSED: approved collection manifest status/scope is invalid.")
    content_sha256 = str(manifest.get("content_sha256") or "")
    content = dict(manifest)
    content.pop("content_sha256", None)
    actual_content_sha256 = hashlib.sha256(canonical(content).encode("utf-8")).hexdigest()
    if (not secrets.compare_digest(content_sha256, APPROVED_MANIFEST_CONTENT_SHA256)
            or not secrets.compare_digest(actual_content_sha256, APPROVED_MANIFEST_CONTENT_SHA256)):
        raise FamilyMediaError("REFUSED: approved collection content hash is invalid.")
    families = manifest.get("families")
    if not isinstance(families, dict) or set(families) != set(FAMILY_KEYS):
        raise FamilyMediaError("REFUSED: approved collection manifest does not contain exactly five families.")
    fields = ("position", "path", "filename", "sha256", "bytes", "width", "height", "format", "mode")
    for key, spec in FAMILY_SPECS.items():
        family = families.get(key)
        images = family.get("accepted_images") if isinstance(family, dict) else None
        if not isinstance(family, dict) or family.get("product_id") != spec["product_id"]:
            raise FamilyMediaError(f"REFUSED: approved collection identity is invalid for {key}.")
        if (not isinstance(images, list) or len(images) != expected_image_count(key)
                or not all(isinstance(row, dict) for row in images)):
            raise FamilyMediaError(f"REFUSED: approved collection image list is invalid for {key}.")
        projected = [{field: row.get(field) for field in fields} for row in images]
        expected = [{field: row[field] for field in fields} for row in spec["images"]]
        if projected != expected:
            raise FamilyMediaError(f"REFUSED: approved collection files/order changed for {key}.")
    return {
        "path": str(path), "sha256": digest,
        "content_sha256": str(manifest.get("content_sha256") or ""),
        "approved_at": str(manifest.get("approved_at") or ""),
        "status": str(manifest.get("status") or ""),
    }


def safe_gallery(record: dict[str, Any]) -> list[dict[str, Any]]:
    images = record.get("images")
    if not isinstance(images, list):
        raise FamilyMediaError("REFUSED: product images are not a list.")
    output = []
    for index, row in enumerate(images):
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0:
            raise FamilyMediaError(f"REFUSED: current image {index} has no positive integer ID.")
        output.append({"id": row["id"], "alt": str(row.get("alt") or "")})
    return output


def gallery_ids(record: dict[str, Any]) -> list[int]:
    return [row["id"] for row in safe_gallery(record)]


def protected_product_projection(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise FamilyMediaError("REFUSED: product read returned an unexpected response.")
    projection = copy.deepcopy({
        field: value for field, value in record.items()
        if field not in PERMITTED_PRODUCT_READBACK_DRIFT_FIELDS
    })
    # WooCommerce computes related_ids and may return the same membership in a
    # different order on consecutive GETs. Preserve the exact positive-ID set,
    # but canonicalize this one derived ordering so it cannot cause a false
    # pre-attempt refusal.
    if "related_ids" in projection:
        related = projection["related_ids"]
        if (not isinstance(related, list)
                or any(type(value) is not int or value <= 0 for value in related)
                or len(set(related)) != len(related)):
            raise FamilyMediaError("REFUSED: product related_ids are not a unique positive-ID list.")
        projection["related_ids"] = sorted(related)
    if "meta_data" in projection:
        raw_meta = projection["meta_data"]
        if not isinstance(raw_meta, list):
            raise FamilyMediaError("REFUSED: product meta_data is not a list.")
        normalized_meta: list[dict[str, Any]] = []
        for entry in raw_meta:
            if isinstance(entry, dict):
                normalized_meta.append({
                    "key": entry.get("key"),
                    "value": entry.get("value"),
                })
            else:
                normalized_meta.append({"raw": entry})
        normalized_meta.sort(key=lambda x: canonical(x))
        projection["meta_data"] = normalized_meta
    return projection


def assert_product_eligibility(key: str, evidence: dict[str, Any],
                               expected: dict[str, Any] | None = None) -> None:
    spec = family_spec(key)
    identity = {
        "id": spec["product_id"], "name": spec["label"], "sku": spec["sku"],
        "type": spec["type"], "status": spec["status"], "permalink": spec["permalink"],
    }
    if (not isinstance(evidence, dict) or evidence.get("product_id") != spec["product_id"]
            or evidence.get("identity") != identity):
        raise FamilyMediaError(f"REFUSED: fixed product eligibility failed for {key}.")
    projection = evidence.get("protected_projection")
    if (not isinstance(projection, dict) or not projection
            or any(field in projection for field in PERMITTED_PRODUCT_READBACK_DRIFT_FIELDS)
            or evidence.get("protected_fingerprint")
                != hashlib.sha256(canonical(projection).encode("utf-8")).hexdigest()):
        raise FamilyMediaError(f"REFUSED: fixed product protected evidence failed for {key}.")
    if expected is not None and evidence != expected:
        raise FamilyMediaError("REFUSED: fixed product changed after the approved stage read.")


def validate_reused_existing_record(key: str, value: Any) -> dict[str, Any] | None:
    exception = reuse_exception(key)
    if exception is None:
        if value is not None:
            raise FamilyMediaError("REFUSED: existing-media reuse is not allowed for this family.")
        return None
    expected_keys = set(exception) | {"source_url"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise FamilyMediaError("REFUSED: existing-media reuse record has the wrong closed schema.")
    if {field: value.get(field) for field in exception} != exception:
        raise FamilyMediaError("REFUSED: existing-media reuse is not the one exact Stub Flange hero.")
    source_url = str(value.get("source_url") or "")
    media_base.assert_public_upload_url(
        source_url, expected_basename=exception["source_basename"]
    )
    return copy.deepcopy(value)


def validate_final_attachment_ids(key: str, final_attachment_ids: Any) -> list[int]:
    """Validate the closed gallery-ID payload before any live product read or write."""
    count = expected_image_count(key)
    if (not isinstance(final_attachment_ids, list) or len(final_attachment_ids) != count
            or any(type(value) is not int or value <= 0 for value in final_attachment_ids)
            or len(set(final_attachment_ids)) != count):
        raise FamilyMediaError(
            f"REFUSED: gallery assignment requires exactly {count} unique positive attachment IDs."
        )
    exception = reuse_exception(key)
    if exception is not None:
        if final_attachment_ids[0] != exception["attachment_id"]:
            raise FamilyMediaError(
                "REFUSED: final Stub Flange gallery position 1 must be reused attachment 4849."
            )
        if exception["attachment_id"] in final_attachment_ids[1:]:
            raise FamilyMediaError("REFUSED: reused attachment 4849 may appear only at position 1.")
    return list(final_attachment_ids)


def assert_family_operation_eligibility(
        key: str, product: dict[str, Any], reused_existing: Any,
        *, expected_product: dict[str, Any] | None = None,
        expected_reused_existing: dict[str, Any] | None = None,
        final_attachment_ids: list[int] | None = None) -> None:
    """One predicate shared by stage, fresh commit preflight and the PUT adapter."""
    assert_product_eligibility(key, product, expected_product)
    checked_reuse = validate_reused_existing_record(key, reused_existing)
    exception = reuse_exception(key)
    if exception is not None:
        gallery = product.get("before_gallery")
        if (not isinstance(gallery, list)
                or any(not isinstance(row, dict) or set(row) != {"id", "alt"}
                       or type(row["id"]) is not int or row["id"] <= 0
                       or type(row["alt"]) is not str for row in gallery)
                or [row["id"] for row in gallery]
                    != list(STUB_FLANGE_REQUIRED_BEFORE_GALLERY_IDS)):
            raise FamilyMediaError(
                "REFUSED: current Stub Flange gallery is not the exact required "
                "[4849, 4850, 4851, 4852] baseline."
            )
    if expected_reused_existing is not None:
        checked_expected = validate_reused_existing_record(key, expected_reused_existing)
        if checked_reuse != checked_expected:
            raise FamilyMediaError(
                "REFUSED: existing Stub Flange hero evidence changed after staging."
            )
    if final_attachment_ids is not None:
        validate_final_attachment_ids(key, final_attachment_ids)


def product_evidence(key: str, vault: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = family_spec(key)
    product_id = int(spec["product_id"])
    record, _ = wc.api_get(f"/products/{product_id}", vault=vault)
    if not isinstance(record, dict) or int(record.get("id") or 0) != product_id:
        raise FamilyMediaError(f"REFUSED: fixed product {product_id} could not be verified.")
    expected_identity = {
        "id": product_id, "name": spec["label"], "sku": spec["sku"],
        "type": spec["type"], "status": spec["status"], "permalink": spec["permalink"],
    }
    live_identity = {field: record.get(field) for field in expected_identity}
    if live_identity != expected_identity:
        raise FamilyMediaError(f"REFUSED: fixed product identity drifted for {key}.")
    projection = protected_product_projection(record)
    evidence = {
        "product_id": product_id,
        "identity": expected_identity,
        "date_modified_gmt": str(record.get("date_modified_gmt") or ""),
        "before_gallery": safe_gallery(record),
        "protected_projection": projection,
        "protected_fingerprint": hashlib.sha256(canonical(projection).encode("utf-8")).hexdigest(),
    }
    assert_product_eligibility(key, evidence)
    return evidence


def gallery_precondition(before_gallery: Any) -> str:
    if (not isinstance(before_gallery, list)
            or any(not isinstance(row, dict) or set(row) != {"id", "alt"}
                   or type(row["id"]) is not int or row["id"] <= 0
                   or not isinstance(row["alt"], str) for row in before_gallery)
            or len({row["id"] for row in before_gallery}) != len(before_gallery)):
        raise FamilyMediaError("REFUSED: gallery precondition is not an exact unique ID list.")
    digest = hashlib.sha256(
        canonical([row["id"] for row in before_gallery]).encode("ascii")
    ).hexdigest()
    return f'"{digest}"'


def assign_fixed_gallery(key: str, attachment_ids: list[int], vault: dict[str, Any],
                         expected_product: dict[str, Any],
                         reused_existing: dict[str, Any] | None = None,
                         on_put_attempt: Any | None = None) -> dict[str, Any]:
    spec = family_spec(key)
    validate_final_attachment_ids(key, attachment_ids)
    fresh_product = product_evidence(key, vault)
    assert_family_operation_eligibility(
        key, fresh_product, reused_existing,
        expected_product=expected_product,
        expected_reused_existing=reused_existing,
        final_attachment_ids=attachment_ids,
    )
    if on_put_attempt is not None:
        on_put_attempt()
    payload = {"images": [{"id": value} for value in attachment_ids]}
    response, _ = wc.api_request(
        "PUT", f"/products/{spec['product_id']}", payload=payload, vault=vault,
        if_match=gallery_precondition(fresh_product["before_gallery"]),
    )
    if not isinstance(response, dict) or int(response.get("id") or 0) != spec["product_id"]:
        raise FamilyMediaError("Gallery PUT returned a different or missing product ID.")
    return response


class ProductFamilyAdmin(media_base.AdminPage):
    """Reuse the proven reader while replacing its packing-ring-only upload allowlist."""

    def __init__(self, page: Any, allowed_paths: frozenset[Path]):
        super().__init__(page)
        self._allowed_paths = allowed_paths

    def _goto(self, url: str) -> None:
        """Bind every navigation to the media or guard closed route and identity."""
        requested_attachment_id = parse_exact_attachment_edit_url(url)
        assert_product_family_admin_url(url)
        self._page.goto(url, wait_until="domcontentloaded", timeout=media_base.NAV_TIMEOUT_MS)
        self._assert_landed()
        if requested_attachment_id is not None:
            landed_attachment_id = parse_exact_attachment_edit_url(self.url)
            if landed_attachment_id != requested_attachment_id:
                raise FamilyMediaError(
                    "REFUSED: the attachment details screen redirected to a different "
                    "or ambiguous attachment ID."
                )

    def _assert_landed(self) -> None:
        assert_product_family_admin_url(self.url)
        if self._page.query_selector(media_base.LOGIN_FORM_SELECTOR) is not None:
            raise FamilyMediaError(
                "REFUSED: authenticated WordPress browser is showing a sign-in screen."
            )

    def _guard_status(self, expected: str) -> dict[str, Any]:
        self._goto(GUARD_ADMIN_URL)
        version_nodes = self._page.query_selector_all(GUARD_VERSION_SELECTOR)
        status_nodes = self._page.query_selector_all(GUARD_STATUS_SELECTOR)
        sections = self._page.query_selector_all("section[data-frpd-family]")
        families = [str(section.get_attribute("data-frpd-family") or "") for section in sections]
        if (len(version_nodes) != 1
                or str(version_nodes[0].inner_text() or "").strip()
                    != f"Version {GUARD_PLUGIN_VERSION}"
                or len(status_nodes) != 1
                or str(status_nodes[0].inner_text() or "").strip() != expected
                or len(families) != len(FAMILY_KEYS) or set(families) != set(FAMILY_KEYS)):
            raise FamilyMediaError("REFUSED: live Media Mutation Guard identity or state is not exact.")
        return {"plugin_version": GUARD_PLUGIN_VERSION, "status": expected,
                "families": sorted(families)}

    @staticmethod
    def _validate_guard_form(form: Any, *, action: str, family: str | None) -> Any:
        if (str(form.get_attribute("method") or "").casefold() != "post"
                or str(form.get_attribute("action") or "") != GUARD_POST_URL):
            raise FamilyMediaError("REFUSED: guard form method or destination is not exact.")
        inputs = form.query_selector_all("input[type='hidden'][name]")
        names = [str(node.get_attribute("name") or "") for node in inputs]
        expected_names = {"_wpnonce", "_wp_http_referer", "action"}
        if family is not None:
            expected_names.add("family")
        if len(names) != len(expected_names) or set(names) != expected_names:
            raise FamilyMediaError("REFUSED: guard form hidden-field surface is not exact.")
        action_nodes = form.query_selector_all("input[type='hidden'][name='action']")
        family_nodes = form.query_selector_all("input[type='hidden'][name='family']")
        if (len(action_nodes) != 1
                or str(action_nodes[0].get_attribute("value") or "") != action
                or (family is None and family_nodes)
                or (family is not None and (len(family_nodes) != 1
                    or str(family_nodes[0].get_attribute("value") or "") != family))):
            raise FamilyMediaError("REFUSED: guard form action or family is not exact.")
        # Nonce and owner-cookie values are deliberately never read.
        buttons = form.query_selector_all("button[type='submit'],button:not([type])")
        if len(buttons) != 1:
            raise FamilyMediaError("REFUSED: guard form does not expose exactly one submit button.")
        return buttons[0]

    def _family_guard_button(self, key: str, action: str, expected_status: str) -> Any:
        family_spec(key)
        self._guard_status(expected_status)
        sections = self._page.query_selector_all(f'section[data-frpd-family="{key}"]')
        if len(sections) != 1:
            raise FamilyMediaError("REFUSED: guard family section is missing or duplicated.")
        selector = f'form:has(button[data-frpd-action="{action}"][data-frpd-family="{key}"])'
        forms = sections[0].query_selector_all(selector)
        if len(forms) != 1:
            raise FamilyMediaError("REFUSED: exact guard family action is missing or duplicated.")
        return self._validate_guard_form(
            forms[0], action=f"frpd_media_guard_{action}", family=key
        )

    def _global_guard_button(self, selector: str, action: str) -> Any:
        self._guard_status("Guard active")
        buttons = self._page.query_selector_all(selector)
        if len(buttons) != 1:
            raise FamilyMediaError("REFUSED: exact active-guard action is missing or duplicated.")
        form = buttons[0].evaluate_handle("button => button.form").as_element()
        if form is None:
            raise FamilyMediaError("REFUSED: active-guard action has no exact form.")
        return self._validate_guard_form(form, action=action, family=None)

    def _read_guard_proof(self) -> dict[str, Any]:
        assert_guard_admin_url(self.url)
        if urlsplit(self.url).path != GUARD_POST_PATH:
            raise FamilyMediaError("REFUSED: guard proof did not land on the exact result route.")
        scripts = self._page.query_selector_all(GUARD_PROOF_SELECTOR)
        headings = self._page.query_selector_all("body > h1")
        if len(scripts) != 1 or len(headings) != 1:
            raise FamilyMediaError("REFUSED: guard proof page is missing or ambiguous.")
        try:
            value = json.loads(str(scripts[0].text_content() or ""))
        except json.JSONDecodeError as exc:
            raise FamilyMediaError("REFUSED: guard proof JSON is invalid.") from exc
        if not isinstance(value, dict):
            raise FamilyMediaError("REFUSED: guard proof is not one JSON object.")
        return value

    def _submit_guard_button(self, button: Any,
                             on_submit_attempt: Any | None = None) -> dict[str, Any]:
        if on_submit_attempt is not None:
            on_submit_attempt()
        button.click(timeout=media_base.ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=media_base.UPLOAD_TIMEOUT_MS)
        self._assert_landed()
        return self._read_guard_proof()

    def atomic_snapshot(self, key: str) -> dict[str, Any]:
        button = self._family_guard_button(key, "snapshot", "Guard inactive")
        return validate_guard_snapshot_proof(
            self._submit_guard_button(button), key, "atomic_snapshot", False
        )

    def prepare_guard_acquire(self, key: str) -> Any:
        return self._family_guard_button(key, "acquire", "Guard inactive")

    def acquire_prepared_guard(self, key: str, button: Any,
                               on_submit_attempt: Any | None = None) -> dict[str, Any]:
        return validate_guard_snapshot_proof(
            self._submit_guard_button(button, on_submit_attempt),
            key, "guard_acquired", True,
        )

    def guarded_snapshot(self, key: str) -> dict[str, Any]:
        button = self._global_guard_button(
            "#frpd-mg-guarded-snapshot", "frpd_media_guard_guarded_snapshot"
        )
        return validate_guard_snapshot_proof(
            self._submit_guard_button(button), key, "guarded_snapshot", True
        )

    def complete_guard(self, key: str, attachment_ids: list[int],
                       on_submit_attempt: Any | None = None) -> dict[str, Any]:
        button = self._global_guard_button(
            "#frpd-mg-complete", "frpd_media_guard_complete"
        )
        proof = validate_guard_completion_proof(
            self._submit_guard_button(button, on_submit_attempt), key, attachment_ids
        )
        health = self._guard_status("Guard inactive")
        return {"proof": proof, "post_completion_health": health}

    def _sanitized_row_structure_diagnostic(self) -> dict[str, Any]:
        """Describe page-one row/cell/link structure without retaining URL values."""
        try:
            self._goto(media_base.library_page_url(1))
            rows = self._page.query_selector_all(media_base.LIST_ROW_SELECTOR)
        except Exception as exc:  # noqa: BLE001 - diagnostic must not mask the refusal
            return {
                "page": 1,
                "row_count": 0,
                "sampled_rows": 0,
                "row_shapes": [],
                "reader_error_type": type(exc).__name__,
                "retains_query_values": False,
                "retains_link_text": False,
                "retains_control_values": False,
            }
        if not isinstance(rows, list):
            return {
                "page": 1,
                "row_count": 0,
                "sampled_rows": 0,
                "row_shapes": [],
                "reader_result_type": type(rows).__name__,
                "retains_query_values": False,
                "retains_link_text": False,
                "retains_control_values": False,
            }
        row_shapes: dict[str, int] = {}
        for row in rows[:3]:
            cells: list[dict[str, Any]] = []
            row_id = str(row.get_attribute("id") or "")
            if re.fullmatch(r"post-[1-9][0-9]*", row_id):
                row_id_shape = "post-positive-int"
            elif row_id:
                row_id_shape = "other"
            else:
                row_id_shape = "empty"
            for cell in row.query_selector_all("th,td"):
                class_tokens = sorted(set(str(cell.get_attribute("class") or "").split()))
                try:
                    tag = str(cell.evaluate("el => el.tagName.toLowerCase()") or "")
                except Exception:  # noqa: BLE001 - shape-only diagnostic
                    tag = "unknown"
                links: list[dict[str, Any]] = []
                for link in cell.query_selector_all("a[href]"):
                    raw = str(link.get_attribute("href") or "")
                    raw_form = "other"
                    if raw.startswith("/") and not raw.startswith("//"):
                        raw_form = "root_relative"
                    elif raw.startswith("https://"):
                        raw_form = "absolute_https"
                    elif raw.startswith("//"):
                        raw_form = "protocol_relative"
                    try:
                        parsed = urlsplit(urljoin(media_base.EXACT_ORIGIN + "/", raw))
                        pairs = parse_qsl(
                            parsed.query, keep_blank_values=True, strict_parsing=False
                        )
                        shape = {
                            "raw_form": raw_form,
                            "path": parsed.path,
                            "query_keys": sorted(key for key, _value in pairs),
                            "fragment_present": bool(parsed.fragment),
                            "exact_attachment_edit": (
                                parse_exact_attachment_edit_url(raw) is not None
                            ),
                        }
                    except ValueError:
                        shape = {
                            "raw_form": raw_form,
                            "path": "parse_error",
                            "query_keys": [],
                            "fragment_present": False,
                            "exact_attachment_edit": False,
                        }
                    links.append(shape)
                controls: list[dict[str, Any]] = []
                for control in cell.query_selector_all('input[name="media[]"]'):
                    raw_type = str(control.get_attribute("type") or "")
                    raw_id = str(control.get_attribute("id") or "")
                    raw_value = str(control.get_attribute("value") or "")
                    id_match = re.fullmatch(r"cb-select-([1-9][0-9]*)", raw_id)
                    value_match = re.fullmatch(r"[1-9][0-9]*", raw_value)
                    row_match = re.fullmatch(r"post-([1-9][0-9]*)", row_id)
                    controls.append({
                        "type_exact_checkbox": raw_type == "checkbox",
                        "id_shape": "cb-select-positive-int" if id_match else (
                            "empty" if not raw_id else "other"
                        ),
                        "value_shape": "positive-int" if value_match else (
                            "empty" if not raw_value else "other"
                        ),
                        "id_value_agree": bool(
                            id_match and value_match and id_match.group(1) == raw_value
                        ),
                        "row_value_agree": bool(
                            row_match and value_match and row_match.group(1) == raw_value
                        ),
                    })
                cells.append({
                    "tag": tag,
                    "classes": class_tokens,
                    "data_colname": str(cell.get_attribute("data-colname") or ""),
                    "link_count": len(links),
                    "link_shapes": links,
                    "media_control_count": len(controls),
                    "media_control_shapes": controls,
                })
            key = canonical({"row_id_shape": row_id_shape, "cells": cells})
            row_shapes[key] = row_shapes.get(key, 0) + 1
        return {
            "page": 1,
            "row_count": len(rows),
            "sampled_rows": min(len(rows), 3),
            "row_shapes": [
                {"count": row_shapes[key], **json.loads(key)}
                for key in sorted(row_shapes)
            ],
            "retains_query_values": False,
            "retains_link_text": False,
            "retains_control_values": False,
        }

    def _row_records(self) -> list[dict[str, Any]]:
        """Preserve one exact row identity when filename/title cells are absent.

        WordPress attachment 1767 proved this live shape on 2026-08-15: five
        links all named one attachment, while the list filename selector was
        empty. Keeping the unique ID lets `enumerate_library` resolve the
        filename from that attachment's own fixed, read-only identity screen.

        A sanitized complete-row diagnostic on 2026-08-20 then proved the
        current list view exposes one bulk-action checkbox in every populated
        row while all title/link cells are absent. WordPress posts the exact
        attachment ID in that fixed `media[]` checkbox. Accept that canonical
        control only when there is exactly one strict positive decimal value,
        and require it to agree with an exact edit-link identity whenever both
        are present.
        """
        rows = self._page.query_selector_all(media_base.LIST_ROW_SELECTOR)
        self._last_row_identity_diagnostics: list[dict[str, Any]] = []
        found: list[dict[str, Any]] = []
        identity_selector = 'input[name="media[]"]'
        for row in rows:
            identity_controls = row.query_selector_all(identity_selector)
            if (not row.query_selector_all("a[href]")
                    and not identity_controls
                    and len(row.query_selector_all(media_base.EMPTY_TABLE_CELL_SELECTOR)) == 1):
                continue
            ids: set[int] = set()
            exact_edit_link_count = 0
            invalid_edit_candidate = False
            for link in row.query_selector_all(media_base.ROW_LINK_SELECTOR):
                href = str(link.get_attribute("href") or "")
                attachment_id = parse_exact_attachment_edit_url(href)
                if attachment_id is not None:
                    exact_edit_link_count += 1
                    ids.add(attachment_id)
                elif is_attachment_edit_candidate_url(href):
                    invalid_edit_candidate = True
            checkbox_ids: set[int] = set()
            invalid_checkbox = False
            if len(identity_controls) > 1:
                invalid_checkbox = True
            elif len(identity_controls) == 1:
                control = identity_controls[0]
                raw_type = str(control.get_attribute("type") or "")
                raw_value = str(control.get_attribute("value") or "")
                raw_control_id = str(control.get_attribute("id") or "")
                raw_row_id = str(row.get_attribute("id") or "")
                if not re.fullmatch(r"[1-9][0-9]*", raw_value):
                    invalid_checkbox = True
                else:
                    checkbox_id = int(raw_value)
                    if (raw_type != "checkbox"
                            or raw_control_id != f"cb-select-{checkbox_id}"
                            or raw_row_id != f"post-{checkbox_id}"):
                        invalid_checkbox = True
                    else:
                        checkbox_ids.add(checkbox_id)
            if (invalid_edit_candidate or invalid_checkbox or len(ids) > 1
                    or len(checkbox_ids) > 1
                    or (ids and checkbox_ids and ids != checkbox_ids)):
                self._last_row_identity_diagnostics.append({
                    "reason": "invalid_identity_carriers",
                    "exact_edit_link_count": exact_edit_link_count,
                    "exact_edit_unique_count": len(ids),
                    "invalid_edit_candidate": invalid_edit_candidate,
                    "media_control_count": len(identity_controls),
                    "invalid_checkbox": invalid_checkbox,
                    "checkbox_unique_count": len(checkbox_ids),
                    "exact_checkbox_agree": bool(
                        ids and checkbox_ids and ids == checkbox_ids
                    ),
                    "retains_identity_values": False,
                })
                found.append({"id": None, "filename": "", "stem": ""})
                continue
            resolved_ids = ids or checkbox_ids
            if len(resolved_ids) != 1:
                self._last_row_identity_diagnostics.append({
                    "reason": "missing_unique_identity",
                    "exact_edit_link_count": exact_edit_link_count,
                    "exact_edit_unique_count": len(ids),
                    "invalid_edit_candidate": invalid_edit_candidate,
                    "media_control_count": len(identity_controls),
                    "invalid_checkbox": invalid_checkbox,
                    "checkbox_unique_count": len(checkbox_ids),
                    "exact_checkbox_agree": bool(
                        ids and checkbox_ids and ids == checkbox_ids
                    ),
                    "retains_identity_values": False,
                })
                found.append({"id": None, "filename": "", "stem": ""})
                continue
            attachment_id = next(iter(resolved_ids))
            name_node = row.query_selector(media_base.ROW_FILENAME_SELECTOR)
            raw = str(name_node.inner_text() or "") if name_node is not None else ""
            filename = re.sub(r"(?i)^\s*file\s*name\s*:\s*", "", raw).strip()
            found.append({
                "id": attachment_id,
                "filename": filename,
                "stem": media_base._normalise_stem(filename) if filename else "",
            })
        return found

    def enumerate_library(self) -> dict[str, Any]:
        """Prove the terminal empty page; never trust the displayed total as a stop.

        The inherited reader stops once enumerated rows reach WordPress's stated
        count. A stale low count could therefore hide a later populated page.
        This family-specific reader walks until the first empty page, requires
        every populated page to state the same total, and treats duplicate IDs
        as unidentified physical rows rather than harmless overlap.
        """
        rows: list[dict[str, Any]] = []
        seen: set[int] = set()
        total: int | None = None
        totals_consistent = True
        pages = 0
        unidentified = 0
        unidentified_diagnostic_counts: dict[str, int] = {}
        terminal_empty_page = False
        for page_number in range(1, media_base.MAX_LIBRARY_PAGES + 1):
            self._goto(media_base.library_page_url(page_number))
            pages = page_number
            self._last_row_identity_diagnostics = []
            page_rows = self._row_records()
            for diagnostic in self._last_row_identity_diagnostics:
                diagnostic_key = canonical({"page": page_number, **diagnostic})
                unidentified_diagnostic_counts[diagnostic_key] = (
                    unidentified_diagnostic_counts.get(diagnostic_key, 0) + 1
                )
            if not page_rows:
                terminal_empty_page = True
                break
            page_total = self._library_total()
            if page_total is None:
                totals_consistent = False
            elif total is None:
                total = page_total
            elif page_total != total:
                totals_consistent = False
            for record in page_rows:
                attachment_id = record.get("id") if isinstance(record, dict) else None
                if type(attachment_id) is not int or attachment_id <= 0:
                    unidentified += 1
                    continue
                if attachment_id in seen:
                    unidentified += 1
                    diagnostic_key = canonical({
                        "page": page_number,
                        "reason": "duplicate_row_identity",
                        "retains_identity_values": False,
                    })
                    unidentified_diagnostic_counts[diagnostic_key] = (
                        unidentified_diagnostic_counts.get(diagnostic_key, 0) + 1
                    )
                    continue
                seen.add(attachment_id)
                if not record.get("filename"):
                    detail = self.read_attachment(
                        attachment_id, allowed_extensions=media_base.SCANNED_EXTENSIONS
                    )
                    filename = str(detail.get("filename") or "").strip()
                    if not filename:
                        raise FamilyMediaError(
                            "REFUSED: a uniquely identified Media Library row still has no "
                            "filename on its own attachment screen."
                        )
                    record = {
                        "id": attachment_id,
                        "filename": filename,
                        "stem": media_base._normalise_stem(filename),
                    }
                rows.append(record)
                if len(rows) > media_base.MAX_LIBRARY_ROWS:
                    raise FamilyMediaError(
                        "REFUSED: Media Library exceeds complete enumeration bounds."
                    )
        complete = bool(
            terminal_empty_page
            and totals_consistent
            and total is not None
            and unidentified == 0
            and len(rows) == total
        )
        result = {
            "rows": rows,
            "total": total,
            "pages": pages,
            "complete": complete,
            "unidentified": unidentified,
        }
        if not complete:
            result["sanitized_row_structure_diagnostic"] = (
                self._sanitized_row_structure_diagnostic()
            )
            result["sanitized_unidentified_diagnostics"] = [
                {"count": unidentified_diagnostic_counts[key], **json.loads(key)}
                for key in sorted(unidentified_diagnostic_counts)
            ]
        return result

    def upload_one(self, expected: dict[str, Any], known_ids: set[int],
                   on_submit_attempt: Any | None = None) -> int:
        resolved = Path(str(expected.get("path") or "")).resolve()
        reused_hero_path = Path(FAMILY_SPECS["stub_flange"]["images"][0]["path"]).resolve()
        if resolved == reused_hero_path or expected.get("position") == 1 and (
                expected.get("sha256") == REUSED_EXISTING_EXCEPTION["sha256"]):
            raise FamilyMediaError(
                "REFUSED: Stub Flange position 1 is reuse-only and can never be uploaded."
            )
        if resolved not in self._allowed_paths or resolved not in ALL_FIXED_PATHS:
            raise FamilyMediaError("REFUSED: only the exact fixed files in this family plan may be uploaded.")
        payload = verified_upload_payload(expected)
        self._goto(media_base.MEDIA_NEW_URL)
        inputs = self._page.query_selector_all(media_base.FILE_INPUT_SELECTOR)
        submits = self._page.query_selector_all(media_base.SUBMIT_SELECTOR)
        if len(inputs) != 1 or len(submits) != 1:
            raise FamilyMediaError("REFUSED: WordPress does not expose exactly one file input and submit control.")
        inputs[0].set_input_files(str(resolved), timeout=media_base.ACTION_TIMEOUT_MS)
        self._assert_landed()
        if on_submit_attempt is not None:
            on_submit_attempt()
        with self._page.expect_navigation(wait_until="domcontentloaded", timeout=media_base.UPLOAD_TIMEOUT_MS):
            submits[0].click(timeout=media_base.UPLOAD_TIMEOUT_MS)
        self._assert_landed()
        if (urlsplit(self.url).path or "") == media_base.MEDIA_NEW_PATH:
            self._goto(media_base.UPLOAD_LIST_URL + "?mode=list")
        return self._identify_upload(known_ids)

    def verify_public_product(self, key: str, verified_source_urls: list[str]) -> dict[str, Any]:
        spec = family_spec(key)
        permalink = spec["permalink"]
        product_id = spec["product_id"]
        count = expected_image_count(key)
        if (not isinstance(verified_source_urls, list) or len(verified_source_urls) != count
                or any(not isinstance(value, str) for value in verified_source_urls)):
            raise FamilyMediaError(
                f"Public verification requires exactly {count} ordered verified attachment URLs."
            )
        for value, image in zip(verified_source_urls, spec["images"]):
            media_base.assert_public_upload_url(
                value, expected_basename=expected_public_basename(key, image)
            )
        javascript_errors: list[str] = []

        def on_console(message: Any) -> None:
            if str(getattr(message, "type", "")).casefold() == "error":
                javascript_errors.append("console_error")

        def on_pageerror(_: Any) -> None:
            javascript_errors.append("page_error")

        self._page.on("console", on_console)
        self._page.on("pageerror", on_pageerror)
        try:
            response = self._page.goto(
                permalink, wait_until="networkidle", timeout=media_base.UPLOAD_TIMEOUT_MS
            )
            if response is None or int(response.status) != 200:
                raise FamilyMediaError("Public product page did not return HTTP 200.")
            expected_url = urlsplit(permalink)
            landed = urlsplit(str(self._page.url))
            if ((landed.scheme, landed.netloc, landed.path.rstrip("/"))
                    != (expected_url.scheme, expected_url.netloc, expected_url.path.rstrip("/"))
                    or landed.query or landed.fragment):
                raise FamilyMediaError("Public product page redirected outside its fixed clean route.")
            body_class = str(self._page.get_attribute("body", "class") or "")
            if f"postid-{product_id}" not in body_class.split():
                raise FamilyMediaError("Public product page did not prove the fixed product ID.")
            gallery = self._page.locator(
                ".woocommerce-product-gallery__wrapper > .woocommerce-product-gallery__image"
            ).evaluate_all("""
                elements => elements.map((wrapper) => {
                    const links = Array.from(wrapper.querySelectorAll(':scope > a'));
                    const images = Array.from(wrapper.querySelectorAll('img'));
                    const image = images.length === 1 ? images[0] : null;
                    return {
                        link_count: links.length,
                        image_count: images.length,
                        href: links.length === 1 ? links[0].href : '',
                        complete: image ? image.complete : false,
                        natural_width: image ? image.naturalWidth : 0,
                        natural_height: image ? image.naturalHeight : 0,
                        current_src: image ? (image.currentSrc || image.src || '') : '',
                    };
                })
            """)
            if not isinstance(gallery, list) or len(gallery) != count:
                raise FamilyMediaError(
                    f"Public product page did not render exactly {count} gallery items."
                )
            for row, image, source_url in zip(gallery, spec["images"], verified_source_urls):
                public_basename = expected_public_basename(key, image)
                if (not isinstance(row, dict) or row.get("link_count") != 1
                        or row.get("image_count") != 1 or row.get("complete") is not True
                        or int(row.get("natural_width") or 0) <= 0
                        or int(row.get("natural_height") or 0) <= 0):
                    raise FamilyMediaError("Public product gallery contains a missing or broken rendered image.")
                href = urlsplit(str(row.get("href") or ""))
                current_src = urlsplit(str(row.get("current_src") or ""))
                source = urlsplit(source_url)
                media_base.assert_public_upload_url(str(row.get("href") or ""),
                                                    expected_basename=public_basename)
                if href != source:
                    raise FamilyMediaError("Public product gallery original link is not the verified attachment URL.")
                wanted_stem = Path(public_basename).stem
                current_name = Path(current_src.path).name
                media_base.assert_public_upload_url(str(row.get("current_src") or ""))
                if Path(current_src.path).parent != Path(source.path).parent:
                    raise FamilyMediaError("Public product gallery rendered source left the verified upload directory.")
                original_name = public_basename
                derivative = re.fullmatch(
                    re.escape(wanted_stem) + r"-(\d+)x(\d+)\.png", current_name
                )
                natural = (int(row["natural_width"]), int(row["natural_height"]))
                if current_name == original_name:
                    expected_dimensions = (int(image["width"]), int(image["height"]))
                elif derivative is not None:
                    expected_dimensions = (int(derivative.group(1)), int(derivative.group(2)))
                else:
                    raise FamilyMediaError("Public product gallery rendered source is not an exact PNG derivative.")
                if natural != expected_dimensions:
                    raise FamilyMediaError("Public product gallery rendered dimensions do not match its PNG source.")
            if javascript_errors:
                raise FamilyMediaError("Public product page produced one or more unknown JavaScript errors.")
            return {
                "url": permalink, "http_status": 200, "product_id": product_id,
                "fixed_images_in_order": count, "rendered_gallery_items": count,
                "broken_images": 0, "javascript_errors": 0,
            }
        finally:
            self._page.remove_listener("console", on_console)
            self._page.remove_listener("pageerror", on_pageerror)


@contextlib.contextmanager
def admin_session(allowed_paths: frozenset[Path]) -> Iterator[ProductFamilyAdmin]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=15_000)
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            raise FamilyMediaError(
                "REFUSED: authenticated WordPress browser is unavailable. Run "
                "CONNECT_DADO_WORDPRESS_UI.bat and keep it open."
            ) from exc
        if not browser.contexts or not browser.contexts[0].pages:
            raise FamilyMediaError("REFUSED: authenticated WordPress browser has no open page.")
        admin = ProductFamilyAdmin(browser.contexts[0].pages[0], allowed_paths)
        try:
            yield admin
        finally:
            admin.leave_on_media_list()


def _downloaded_image_identity(data: bytes) -> tuple[str, str, int, int]:
    import io
    import warnings

    from PIL import Image

    if Image.MAX_IMAGE_PIXELS is None:
        raise FamilyMediaError("REFUSED: Pillow decompression-bomb protection is disabled.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                return str(image.format or ""), str(image.mode), int(image.width), int(image.height)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise FamilyMediaError("REFUSED: downloaded reuse image exceeded safe dimensions.") from exc
    except Exception as exc:  # noqa: BLE001 - malformed image is a fail-closed scan
        raise FamilyMediaError("REFUSED: downloaded reuse image could not be decoded.") from exc


def _observed_reuse_record(key: str, attachment_id: int,
                           detail: dict[str, Any], data: bytes) -> dict[str, Any]:
    exception = reuse_exception(key)
    if exception is None:
        raise FamilyMediaError("REFUSED: no existing-media exception exists for this family.")
    source_url = str(detail.get("source_url") or "")
    source_basename = Path(urlsplit(source_url).path).name
    image_format, mode, width, height = _downloaded_image_identity(data)
    return {
        "family": key,
        "product_id": family_spec(key)["product_id"],
        "position": 1,
        "attachment_id": int(attachment_id),
        "actual_filename": str(detail.get("filename") or "").strip(),
        "source_basename": source_basename,
        "approved_filename": exception["approved_filename"],
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "width": width,
        "height": height,
        "format": image_format,
        "mode": mode,
        "source_url": source_url,
    }


def _is_reuse_candidate(key: str, attachment_id: int,
                        detail: dict[str, Any], digest: str) -> bool:
    exception = reuse_exception(key)
    if exception is None:
        return False
    source_basename = Path(urlsplit(str(detail.get("source_url") or "")).path).name
    return bool(
        attachment_id == exception["attachment_id"]
        or str(detail.get("filename") or "").strip() == exception["actual_filename"]
        or source_basename == exception["source_basename"]
        or secrets.compare_digest(digest, exception["sha256"])
    )


def read_reused_existing(admin: ProductFamilyAdmin, key: str) -> dict[str, Any] | None:
    exception = reuse_exception(key)
    if exception is None:
        return None
    detail = admin.read_attachment(
        exception["attachment_id"],
        expected_basename=exception["actual_filename"],
        allowed_extensions=media_base.SCANNED_EXTENSIONS,
    )
    data = media_base.download_public_bytes(
        detail["source_url"],
        expected_basename=exception["source_basename"],
        allowed_extensions=media_base.SCANNED_EXTENSIONS,
    )
    record = _observed_reuse_record(key, exception["attachment_id"], detail, data)
    return validate_reused_existing_record(key, record)


def duplicate_scan(admin: ProductFamilyAdmin, target_specs: dict[str, dict[str, Any]],
                   walked: dict[str, Any] | None = None) -> dict[str, Any]:
    def snapshot_projection(value: dict[str, Any]) -> tuple[int, tuple[tuple[int, str], ...]] | None:
        records = value.get("rows")
        total_value = value.get("total")
        if (value.get("complete") is not True or type(total_value) is not int
                or total_value < 0 or not isinstance(records, list)
                or value.get("unidentified") != 0 or len(records) != total_value):
            return None
        projected: list[tuple[int, str]] = []
        for record in records:
            if not isinstance(record, dict):
                return None
            attachment_id = record.get("id")
            filename = record.get("filename")
            if (type(attachment_id) is not int or attachment_id <= 0
                    or not isinstance(filename, str) or not filename):
                return None
            projected.append((attachment_id, filename))
        if len({attachment_id for attachment_id, _ in projected}) != len(projected):
            return None
        return total_value, tuple(sorted(projected))

    walked = walked if walked is not None else admin.enumerate_library()
    rows = walked["rows"]
    private_library_record = {
        "attachment_id": GUARD_PRIVATE_EXCEPTION["attachment_id"],
        "filename": Path(GUARD_PRIVATE_EXCEPTION["attached_file"]).name,
    }
    private_rows = [
        {"attachment_id": row.get("id"), "filename": row.get("filename")}
        for row in rows if isinstance(row, dict)
        and (row.get("id") == private_library_record["attachment_id"]
             or row.get("filename") == private_library_record["filename"])
    ]
    private_exception_proven = private_rows == [private_library_record]
    target_names = {record["filename"] for spec in target_specs.values() for record in spec["images"]}
    target_stems = {media_base._normalise_stem(name) for name in target_names}
    target_hashes = {record["sha256"]: f"{key}:{record['filename']}"
                     for key, spec in target_specs.items() for record in spec["images"]}
    name_conflicts: list[dict[str, Any]] = []
    image_rows = ([row for row in rows
                   if row.get("id") != private_library_record["attachment_id"]
                   and media_base._extension_of(str(row.get("filename") or "")) in media_base.IMAGE_EXTENSIONS]
                  if walked.get("complete") is True and private_exception_proven else [])
    completed = 0
    failures = 0
    total_bytes = 0
    hash_conflicts: list[dict[str, Any]] = []
    first_content: dict[int, tuple[str, str, str]] = {}
    reuse_key = "stub_flange" if "stub_flange" in target_specs else None
    first_reuse_candidates: list[dict[str, Any]] = []
    if walked.get("complete") is True:
        if len(image_rows) > media_base.MAX_IMAGE_ATTACHMENTS:
            raise FamilyMediaError("REFUSED: Media Library exceeds complete hash-scan bounds.")
        for row in image_rows:
            try:
                detail = admin.read_attachment(row["id"], allowed_extensions=media_base.SCANNED_EXTENSIONS)
                detail_filename = str(detail.get("filename") or "").strip()
                if detail_filename != row["filename"]:
                    raise FamilyMediaError(
                        "REFUSED: a Media Library list filename disagrees with its own "
                        "attachment details screen."
                    )
                detail_stem = media_base._normalise_stem(detail_filename)
                if detail_filename in target_names or detail_stem in target_stems:
                    name_conflicts.append({
                        "attachment_id": row["id"], "filename": detail_filename,
                    })
                data = media_base.download_public_bytes(
                    detail["source_url"], allowed_extensions=media_base.SCANNED_EXTENSIONS
                )
            except Exception:  # noqa: BLE001 - the result is an incomplete refusal
                failures += 1
                break
            total_bytes += len(data)
            if total_bytes > media_base.MAX_TOTAL_DOWNLOAD_BYTES:
                raise FamilyMediaError("REFUSED: Media Library hash scan exceeded byte bounds.")
            completed += 1
            digest = hashlib.sha256(data).hexdigest()
            first_content[int(row["id"])] = (
                detail_filename, str(detail["source_url"]), digest,
            )
            if digest in target_hashes:
                hash_conflicts.append({"attachment_id": row["id"],
                                       "matches_fixed_image": target_hashes[digest]})
            if (reuse_key is not None
                    and _is_reuse_candidate(reuse_key, int(row["id"]), detail, digest)):
                first_reuse_candidates.append(
                    _observed_reuse_record(reuse_key, int(row["id"]), detail, data)
                )
    rechecked: dict[str, Any] = {
        "rows": [], "total": 0, "pages": 0, "complete": False, "unidentified": 0,
    }
    snapshot_stable = False
    recheck_hashes_completed = 0
    recheck_hash_failures = 0
    recheck_hash_bytes = 0
    recheck_hash_complete = False
    content_stable = False
    final_checked: dict[str, Any] = {
        "rows": [], "total": 0, "pages": 0, "complete": False, "unidentified": 0,
    }
    final_snapshot_stable = False
    rechecked_reuse_candidates: list[dict[str, Any]] = []
    if (walked.get("complete") is True and failures == 0
            and completed == len(image_rows)):
        rechecked = admin.enumerate_library()
        before_projection = snapshot_projection(walked)
        after_projection = snapshot_projection(rechecked)
        snapshot_stable = before_projection is not None and before_projection == after_projection
        if snapshot_stable:
            content_stable = True
            recheck_image_rows = [
                row for row in rechecked["rows"]
                if row.get("id") != private_library_record["attachment_id"]
                and media_base._extension_of(str(row.get("filename") or "")) in media_base.IMAGE_EXTENSIONS
            ]
            for row in recheck_image_rows:
                try:
                    detail = admin.read_attachment(
                        row["id"], allowed_extensions=media_base.SCANNED_EXTENSIONS
                    )
                    detail_filename = str(detail.get("filename") or "").strip()
                    if detail_filename != row["filename"]:
                        raise FamilyMediaError(
                            "REFUSED: a closing Media Library filename disagrees with "
                            "its own attachment details screen."
                        )
                    data = media_base.download_public_bytes(
                        detail["source_url"], allowed_extensions=media_base.SCANNED_EXTENSIONS
                    )
                except Exception:  # noqa: BLE001 - closing proof is incomplete
                    recheck_hash_failures += 1
                    content_stable = False
                    break
                recheck_hash_bytes += len(data)
                if recheck_hash_bytes > media_base.MAX_TOTAL_DOWNLOAD_BYTES:
                    raise FamilyMediaError(
                        "REFUSED: closing Media Library hash scan exceeded byte bounds."
                    )
                recheck_hashes_completed += 1
                digest = hashlib.sha256(data).hexdigest()
                current_content = (
                    detail_filename, str(detail["source_url"]), digest,
                )
                if current_content != first_content.get(int(row["id"])):
                    content_stable = False
                if digest in target_hashes:
                    conflict = {
                        "attachment_id": row["id"],
                        "matches_fixed_image": target_hashes[digest],
                    }
                    if conflict not in hash_conflicts:
                        hash_conflicts.append(conflict)
                if (reuse_key is not None
                        and _is_reuse_candidate(reuse_key, int(row["id"]), detail, digest)):
                    rechecked_reuse_candidates.append(
                        _observed_reuse_record(reuse_key, int(row["id"]), detail, data)
                    )
            recheck_hash_complete = bool(
                recheck_hash_failures == 0
                and recheck_hashes_completed == len(recheck_image_rows)
            )
            if recheck_hash_complete:
                final_checked = admin.enumerate_library()
                final_projection = snapshot_projection(final_checked)
                final_snapshot_stable = (
                    before_projection is not None and before_projection == final_projection
                )
    recheck_rows = rechecked.get("rows")
    final_rows = final_checked.get("rows")
    evidence = {
        "checked_utc": utc_now().isoformat(),
        "library_total": walked.get("total"),
        "enumerated": len(rows),
        "pages_read": walked.get("pages"),
        "enumeration_complete": walked.get("complete") is True,
        "image_rows": len(image_rows),
        "image_hashes_completed": completed,
        "hash_failures": failures,
        "hash_bytes_read": total_bytes,
        "hash_complete": failures == 0 and completed == len(image_rows),
        "recheck_total": rechecked.get("total") if type(rechecked.get("total")) is int else 0,
        "recheck_enumerated": len(recheck_rows) if isinstance(recheck_rows, list) else 0,
        "recheck_pages": rechecked.get("pages") if type(rechecked.get("pages")) is int else 0,
        "recheck_complete": rechecked.get("complete") is True,
        "snapshot_stable": snapshot_stable,
        "recheck_image_hashes_completed": recheck_hashes_completed,
        "recheck_hash_failures": recheck_hash_failures,
        "recheck_hash_bytes_read": recheck_hash_bytes,
        "recheck_hash_complete": recheck_hash_complete,
        "content_stable": content_stable,
        "final_total": final_checked.get("total") if type(final_checked.get("total")) is int else 0,
        "final_enumerated": len(final_rows) if isinstance(final_rows, list) else 0,
        "final_pages": final_checked.get("pages") if type(final_checked.get("pages")) is int else 0,
        "final_complete": final_checked.get("complete") is True,
        "final_snapshot_stable": final_snapshot_stable,
        "private_exception": private_library_record if private_exception_proven else None,
        "private_exception_proven": private_exception_proven,
        "name_conflicts": name_conflicts,
        "hash_conflicts": hash_conflicts,
        "reuse_candidates": first_reuse_candidates,
        "target_families": sorted(target_specs),
    }
    if walked.get("complete") is not True:
        evidence["sanitized_row_structure_diagnostic"] = walked.get(
            "sanitized_row_structure_diagnostic"
        )
    evidence["complete"] = bool(
        evidence["enumeration_complete"] and evidence["private_exception_proven"]
        and evidence["hash_complete"]
        and evidence["recheck_complete"] and evidence["snapshot_stable"]
        and evidence["recheck_hash_complete"] and evidence["content_stable"]
        and evidence["final_complete"] and evidence["final_snapshot_stable"]
        and first_reuse_candidates == rechecked_reuse_candidates
    )
    return evidence


def require_no_duplicates(evidence: dict[str, Any]) -> None:
    if (evidence.get("complete") is not True
            or evidence.get("enumeration_complete") is not True
            or evidence.get("hash_complete") is not True
            or evidence.get("recheck_complete") is not True
            or evidence.get("snapshot_stable") is not True
            or evidence.get("recheck_hash_complete") is not True
            or evidence.get("content_stable") is not True
            or evidence.get("final_complete") is not True
            or evidence.get("final_snapshot_stable") is not True
            or evidence.get("private_exception_proven") is not True
            or evidence.get("private_exception") != {
                "attachment_id": GUARD_PRIVATE_EXCEPTION["attachment_id"],
                "filename": Path(GUARD_PRIVATE_EXCEPTION["attached_file"]).name,
            }
            or evidence.get("hash_failures") != 0
            or evidence.get("image_hashes_completed") != evidence.get("image_rows")
            or evidence.get("recheck_hash_failures") != 0
            or evidence.get("recheck_image_hashes_completed") != evidence.get("image_rows")):
        diagnostic_fields = (
            "library_total", "enumerated", "pages_read", "enumeration_complete",
            "image_rows", "image_hashes_completed", "hash_failures", "hash_complete",
            "recheck_total", "recheck_enumerated", "recheck_pages", "recheck_complete",
            "snapshot_stable", "recheck_image_hashes_completed", "recheck_hash_failures",
            "recheck_hash_complete", "content_stable", "final_total", "final_enumerated",
            "final_pages", "final_complete", "final_snapshot_stable",
            "private_exception_proven", "complete",
        )
        diagnostic = {field: evidence.get(field) for field in diagnostic_fields}
        if "sanitized_row_structure_diagnostic" in evidence:
            diagnostic["sanitized_row_structure_diagnostic"] = evidence.get(
                "sanitized_row_structure_diagnostic"
            )
        raise FamilyMediaError(
            "REFUSED: complete Media Library duplicate absence was not proven: "
            + json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))
        )
    targets = evidence.get("target_families")
    allows_stub_reuse = isinstance(targets, list) and "stub_flange" in targets
    expected_name_conflicts: list[dict[str, Any]] = []
    expected_hash_conflicts = ([{
        "attachment_id": REUSED_EXISTING_EXCEPTION["attachment_id"],
        "matches_fixed_image": (
            "stub_flange:" + REUSED_EXISTING_EXCEPTION["approved_filename"]
        ),
    }] if allows_stub_reuse else [])
    candidates = evidence.get("reuse_candidates")
    candidate_ok = False
    if allows_stub_reuse and isinstance(candidates, list) and len(candidates) == 1:
        try:
            validate_reused_existing_record("stub_flange", candidates[0])
            candidate_ok = True
        except FamilyMediaError:
            candidate_ok = False
    elif not allows_stub_reuse and candidates == []:
        candidate_ok = True
    if (evidence.get("name_conflicts") != expected_name_conflicts
            or evidence.get("hash_conflicts") != expected_hash_conflicts
            or not candidate_ok):
        conflict_diagnostic = {
            "name_conflicts": evidence.get("name_conflicts"),
            "hash_conflicts": evidence.get("hash_conflicts"),
            "reuse_candidates": evidence.get("reuse_candidates"),
        }
        raise FamilyMediaError(
            "REFUSED: Media Library conflicts are not the one exact reusable Stub Flange hero: "
            + json.dumps(conflict_diagnostic, sort_keys=True, separators=(",", ":"))
        )


def validate_duplicate_evidence(evidence: Any, key: str) -> None:
    expected_keys = {
        "checked_utc", "library_total", "enumerated", "pages_read", "enumeration_complete",
        "image_rows", "image_hashes_completed", "hash_failures", "hash_bytes_read",
        "hash_complete", "recheck_total", "recheck_enumerated", "recheck_pages",
        "recheck_complete", "snapshot_stable", "recheck_image_hashes_completed",
        "recheck_hash_failures", "recheck_hash_bytes_read", "recheck_hash_complete",
        "content_stable", "final_total", "final_enumerated", "final_pages",
        "final_complete", "final_snapshot_stable", "name_conflicts", "hash_conflicts",
        "reuse_candidates", "private_exception", "private_exception_proven",
        "target_families", "complete",
    }
    if not isinstance(evidence, dict) or set(evidence) != expected_keys:
        raise FamilyMediaError("REFUSED: duplicate evidence schema is invalid.")
    integer_fields = (
        "library_total", "enumerated", "pages_read", "image_rows",
        "image_hashes_completed", "hash_failures", "hash_bytes_read",
        "recheck_total", "recheck_enumerated", "recheck_pages",
        "recheck_image_hashes_completed", "recheck_hash_failures", "recheck_hash_bytes_read",
        "final_total", "final_enumerated", "final_pages",
    )
    if any(type(evidence[field]) is not int or evidence[field] < 0 for field in integer_fields):
        raise FamilyMediaError("REFUSED: duplicate evidence counters are invalid.")
    if (evidence["pages_read"] < 1 or evidence["enumerated"] != evidence["library_total"]
            or evidence["recheck_pages"] < 1
            or evidence["recheck_enumerated"] != evidence["recheck_total"]
            or evidence["recheck_total"] != evidence["library_total"]
            or evidence["final_pages"] < 1
            or evidence["final_enumerated"] != evidence["final_total"]
            or evidence["final_total"] != evidence["library_total"]):
        raise FamilyMediaError("REFUSED: duplicate evidence does not prove complete enumeration.")
    try:
        checked = datetime.fromisoformat(str(evidence["checked_utc"]))
    except ValueError as exc:
        raise FamilyMediaError("REFUSED: duplicate evidence timestamp is invalid.") from exc
    if checked.tzinfo is None:
        raise FamilyMediaError("REFUSED: duplicate evidence timestamp has no timezone.")
    targets = evidence.get("target_families")
    if targets not in ([key], sorted(FAMILY_KEYS)):
        raise FamilyMediaError("REFUSED: duplicate evidence covers the wrong families.")
    if (not isinstance(evidence["name_conflicts"], list)
            or not isinstance(evidence["hash_conflicts"], list)
            or not isinstance(evidence["reuse_candidates"], list)):
        raise FamilyMediaError("REFUSED: duplicate conflict evidence is invalid.")
    if (evidence["private_exception_proven"] is not True
            or evidence["private_exception"] != {
                "attachment_id": GUARD_PRIVATE_EXCEPTION["attachment_id"],
                "filename": Path(GUARD_PRIVATE_EXCEPTION["attached_file"]).name,
            }):
        raise FamilyMediaError("REFUSED: duplicate evidence lacks the exact private attachment.")
    require_no_duplicates(evidence)


def reused_existing_from_duplicate(evidence: dict[str, Any], key: str) -> dict[str, Any] | None:
    validate_duplicate_evidence(evidence, key)
    if reuse_exception(key) is None:
        return None
    candidates = evidence["reuse_candidates"]
    if len(candidates) != 1:
        raise FamilyMediaError("REFUSED: exact Stub Flange reuse evidence is missing or ambiguous.")
    return validate_reused_existing_record(key, candidates[0])


def _validated_plan_sha(plan_sha256: str) -> str:
    value = str(plan_sha256)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise FamilyMediaError("REFUSED: plan SHA-256 is invalid.")
    return value


def stage_registry_path(plan_sha256: str) -> Path:
    return STAGE_REGISTRY_DIR / f"{_validated_plan_sha(plan_sha256)}.stage.json"


def lock_path(plan_sha256: str) -> Path:
    return ATTEMPT_LEDGER_DIR / f"{_validated_plan_sha(plan_sha256)}.attempt.json"


def reservation_path(plan_sha256: str) -> Path:
    return RESERVATION_DIR / f"{_validated_plan_sha(plan_sha256)}.reserved.json"


def result_path(plan_sha256: str) -> Path:
    return RESULT_DIR / f"{_validated_plan_sha(plan_sha256)}.result.json"


def journal_path(plan_sha256: str, event: str) -> Path:
    if not event or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in event):
        raise FamilyMediaError("REFUSED: event journal key is invalid.")
    return JOURNAL_DIR / _validated_plan_sha(plan_sha256) / f"{event}.json"


def write_json(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    if exclusive:
        pending = path.with_name(f".{path.name}.{secrets.token_hex(12)}.pending")
        descriptor = None
        try:
            descriptor = os.open(str(pending), flags, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                written = handle.write(payload)
                if written != len(payload):
                    raise OSError("immutable evidence write was short")
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                pending.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        try:
            os.link(str(pending), str(path))
        except FileExistsError as exc:
            try:
                pending.unlink(missing_ok=True)
            except OSError:
                pass
            raise FamilyMediaError(
                "REFUSED: immutable evidence already exists; no replay or overwrite."
            ) from exc
        except BaseException:
            try:
                pending.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        try:
            pending.unlink(missing_ok=True)
        except OSError:
            pass
        return
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(str(temporary), flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            written = handle.write(payload)
            if written != len(payload):
                raise OSError("JSON evidence write was short")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def upload_file_evidence(key: str, local: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted_positions = [row["position"] for row in upload_image_records(key)]
    rows = [copy.deepcopy(row) for row in local if row.get("position") in wanted_positions]
    if [row.get("position") for row in rows] != wanted_positions:
        raise FamilyMediaError("REFUSED: local upload evidence does not contain exact upload positions.")
    return rows


def fixed_gallery_template(key: str, local: list[dict[str, Any]],
                           reused_existing: dict[str, Any] | None) -> list[dict[str, Any]]:
    validate_reused_existing_record(key, reused_existing)
    template: list[dict[str, Any]] = []
    exception = reuse_exception(key)
    for row in local:
        if exception is not None and row["position"] == exception["position"]:
            template.append({
                "position": row["position"], "sha256": row["sha256"],
                "filename": row["filename"], "disposition": "reuse_existing",
                "attachment_id": exception["attachment_id"],
                "actual_filename": exception["actual_filename"],
            })
        else:
            template.append({
                "position": row["position"], "sha256": row["sha256"],
                "filename": row["filename"], "disposition": "upload_once",
                "attachment_id": None,
            })
    return template


def operation_sha256(key: str, local: list[dict[str, Any]],
                     product: dict[str, Any], approval_manifest: dict[str, Any],
                     reused_existing: dict[str, Any] | None = None) -> str:
    spec = family_spec(key)
    semantic = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "action": ACTION,
        "family": key,
        "product_id": spec["product_id"],
        "approved_manifest_sha256": APPROVED_MANIFEST_SHA256,
        "approved_manifest": approval_manifest,
        "files": [{field: row[field] for field in ("position", "filename", "bytes", "sha256")}
                  for row in local],
        "upload_files": [{field: row[field] for field in ("position", "filename", "bytes", "sha256")}
                         for row in upload_file_evidence(key, local)],
        "reused_existing": validate_reused_existing_record(key, reused_existing),
        "gallery_payload_template": fixed_gallery_template(key, local, reused_existing),
        "product_identity": product["identity"],
        "protected_fingerprint": product["protected_fingerprint"],
        "before_gallery": product["before_gallery"],
        "guard_contract": guard_contract(key),
    }
    return digest_for(semantic)


def _registry_key() -> bytes:
    path = REGISTRY_KEY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(str(path), flags, 0o600)
        except FileExistsError:
            descriptor = None
        if descriptor is not None:
            key = secrets.token_bytes(32)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
    if (not path.is_file() or media_base._is_reparse_point(path)
            or getattr(path.stat(), "st_nlink", 1) != 1):
        raise FamilyMediaError("REFUSED: local stage-registry integrity key is missing or aliased.")
    key = path.read_bytes()
    if len(key) != 32:
        raise FamilyMediaError("REFUSED: local stage-registry integrity key is invalid.")
    return key


def _registry_mac(registry_core: dict[str, Any]) -> str:
    return hmac.new(_registry_key(), canonical(registry_core).encode("utf-8"), hashlib.sha256).hexdigest()


def canonical_plan_filename(created: datetime, key: str, plan_sha256: str) -> str:
    return f"{created.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{key}_{plan_sha256[:16]}.json"


def fixed_plan_path(raw: str) -> Path:
    path = Path(raw).resolve()
    directory = PLAN_DIR.resolve()
    if (path.parent != directory or path.suffix.casefold() != ".json"
            or not path.is_file() or media_base._is_reparse_point(path)):
        raise FamilyMediaError("REFUSED: plan must be a regular non-reparse JSON file directly inside the fixed plan folder.")
    stat_result = path.stat()
    if getattr(stat_result, "st_nlink", 1) != 1:
        raise FamilyMediaError("REFUSED: hard-linked plan aliases are not accepted.")
    return path


def _verify_stage_registry(path: Path, plan: dict[str, Any], raw: bytes,
                           created: datetime, *, authenticate: bool) -> None:
    plan_sha256 = plan["sha256"]
    expected_name = canonical_plan_filename(created, plan["family"], plan_sha256)
    if path.name != expected_name:
        raise FamilyMediaError("REFUSED: plan filename is not its canonical staged hash-bearing name.")
    registry_path = stage_registry_path(plan_sha256)
    if (not registry_path.is_file() or media_base._is_reparse_point(registry_path)
            or getattr(registry_path.stat(), "st_nlink", 1) != 1):
        raise FamilyMediaError("REFUSED: immutable stage registry evidence is missing or aliased.")
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FamilyMediaError("REFUSED: immutable stage registry evidence is unreadable.") from exc
    expected_core = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "plan_sha256": plan_sha256,
        "operation_sha256": plan["operation_sha256"],
        "plan_file_sha256": hashlib.sha256(raw).hexdigest(),
        "plan_path": str(path),
        "plan_filename": expected_name,
        "family": plan["family"],
        "product_id": plan["product_id"],
        "nonce": plan["nonce"],
        "created_utc": plan["created_utc"],
        "expires_utc": plan["expires_utc"],
    }
    expected_fields = set(expected_core) | {"hmac_sha256"}
    if (not isinstance(registry, dict) or set(registry) != expected_fields
            or {key: registry.get(key) for key in expected_core} != expected_core
            or not isinstance(registry.get("hmac_sha256"), str)
            or len(registry["hmac_sha256"]) != 64):
        raise FamilyMediaError("REFUSED: plan does not match its immutable stage registry.")
    if authenticate and not hmac.compare_digest(registry["hmac_sha256"], _registry_mac(expected_core)):
        raise FamilyMediaError("REFUSED: stage registry authentication failed.")


def stage_one(key: str, local: list[dict[str, Any]], product: dict[str, Any],
              duplicate: dict[str, Any],
              guard_stage_snapshot: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    created = utc_now()
    spec = family_spec(key)
    approval_manifest = validate_approved_manifest()
    validate_duplicate_evidence(duplicate, key)
    reused_existing = reused_existing_from_duplicate(duplicate, key)
    assert_family_operation_eligibility(key, product, reused_existing)
    guard_stage_snapshot = require_empty_guard_snapshot(
        guard_stage_snapshot, key, "atomic_snapshot", duplicate
    )
    operation = operation_sha256(key, local, product, approval_manifest, reused_existing)
    upload_files = upload_file_evidence(key, local)
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "origin": EXACT_ORIGIN,
        "action": ACTION,
        "family": key,
        "family_label": spec["label"],
        "product_id": spec["product_id"],
        "method": "PUT",
        "endpoint": f"/products/{spec['product_id']}",
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "operation_sha256": operation,
        "approval_manifest": approval_manifest,
        "files": local,
        "upload_files": upload_files,
        "reused_existing": reused_existing,
        "product_before": product,
        "duplicate_check": duplicate,
        "guard_contract": guard_contract(key),
        "guard_stage_snapshot": guard_stage_snapshot,
        "gallery_payload_template": fixed_gallery_template(key, local, reused_existing),
        "risk": risk_disclosure(key),
        "writes_if_committed": writes_if_committed(key, spec["product_id"]),
        "forbidden": [
            "delete", "rollback", "retry", "media edit", "alt text", "product content",
            "price", "stock", "status", "category", "attribute", "customer", "order",
            "payment", "plugin", "setting", "user", "email", "Zoho", "Drive", "FNPT",
        ],
    }
    plan = {**core, "sha256": digest_for(core)}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    path = (PLAN_DIR / canonical_plan_filename(created, key, plan["sha256"])).resolve()
    write_json(path, plan, exclusive=True)
    stat_result = path.stat()
    if getattr(stat_result, "st_nlink", 1) != 1 or media_base._is_reparse_point(path):
        raise FamilyMediaError("REFUSED: newly staged plan is aliased or a reparse point.")
    registry_core = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "plan_sha256": plan["sha256"],
        "operation_sha256": operation,
        "plan_file_sha256": _file_sha256(path),
        "plan_path": str(path),
        "plan_filename": path.name,
        "family": key,
        "product_id": spec["product_id"],
        "nonce": plan["nonce"],
        "created_utc": plan["created_utc"],
        "expires_utc": plan["expires_utc"],
    }
    registry = {**registry_core, "hmac_sha256": _registry_mac(registry_core)}
    write_json(stage_registry_path(plan["sha256"]), registry, exclusive=True)
    append_receipt("product_family_media_plan_staged", str(path))
    return path, plan


def load_plan(path: Path, *, authenticate_registry: bool = True) -> dict[str, Any]:
    path = Path(path).resolve()
    try:
        raw = path.read_bytes()
        loaded = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FamilyMediaError("REFUSED: plan is unreadable.") from exc
    if not isinstance(loaded, dict):
        raise FamilyMediaError("REFUSED: plan must contain exactly one object.")
    plan = dict(loaded)
    expected_keys = {
        "schema_version", "tool", "tool_version", "origin", "action", "family",
        "family_label", "product_id", "method", "endpoint", "created_utc", "expires_utc",
        "nonce", "operation_sha256", "approval_manifest", "files", "product_before", "duplicate_check",
        "upload_files", "reused_existing", "guard_contract", "guard_stage_snapshot",
        "gallery_payload_template", "risk", "writes_if_committed", "forbidden", "sha256",
    }
    if set(plan) != expected_keys:
        raise FamilyMediaError("REFUSED: plan fields are not the exact closed schema.")
    saved = str(plan.pop("sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(plan)):
        raise FamilyMediaError("REFUSED: plan hash failed; the plan changed after staging.")
    if (plan.get("schema_version") != SCHEMA_VERSION or plan.get("tool") != TOOL_NAME
            or plan.get("tool_version") != TOOL_VERSION or plan.get("origin") != EXACT_ORIGIN
            or plan.get("action") != ACTION):
        raise FamilyMediaError("REFUSED: plan schema/tool/version/origin/action is invalid.")
    key = str(plan.get("family") or "")
    spec = family_spec(key)
    if plan.get("product_id") != spec["product_id"] or plan.get("family_label") != spec["label"]:
        raise FamilyMediaError("REFUSED: plan family/product identity is invalid.")
    if plan.get("method") != "PUT" or plan.get("endpoint") != f"/products/{spec['product_id']}":
        raise FamilyMediaError("REFUSED: plan method/endpoint is not the fixed gallery route.")
    nonce = plan.get("nonce")
    if not isinstance(nonce, str) or len(nonce) != 32 or any(char not in "0123456789abcdef" for char in nonce):
        raise FamilyMediaError("REFUSED: plan nonce is invalid.")
    try:
        created = datetime.fromisoformat(str(plan["created_utc"]))
        expires = datetime.fromisoformat(str(plan["expires_utc"]))
    except (KeyError, ValueError) as exc:
        raise FamilyMediaError("REFUSED: plan timestamps are invalid.") from exc
    if (created.tzinfo is None or expires.tzinfo is None
            or expires - created != timedelta(hours=PLAN_LIFETIME_HOURS)):
        raise FamilyMediaError("REFUSED: plan lifetime is not exactly 24 hours.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise FamilyMediaError("REFUSED: plan creation time is in the future.")
    if now >= expires:
        raise FamilyMediaError("REFUSED: plan expired; stage a fresh read-only plan.")
    expected_local = validate_local_images(key)
    if plan.get("files") != expected_local:
        raise FamilyMediaError("REFUSED: plan files do not match the fixed approved evidence.")
    expected_upload_files = upload_file_evidence(key, expected_local)
    if plan.get("upload_files") != expected_upload_files:
        raise FamilyMediaError("REFUSED: plan does not carry the exact upload-only positions.")
    if plan.get("approval_manifest") != validate_approved_manifest():
        raise FamilyMediaError("REFUSED: plan does not carry the exact approved collection artifact.")
    reused_existing = validate_reused_existing_record(key, plan.get("reused_existing"))
    expected_template = fixed_gallery_template(key, expected_local, reused_existing)
    expected_writes = writes_if_committed(key, spec["product_id"])
    expected_forbidden = [
        "delete", "rollback", "retry", "media edit", "alt text", "product content",
        "price", "stock", "status", "category", "attribute", "customer", "order",
        "payment", "plugin", "setting", "user", "email", "Zoho", "Drive", "FNPT",
    ]
    if (plan.get("gallery_payload_template") != expected_template
            or plan.get("risk") != risk_disclosure(key)
            or plan.get("writes_if_committed") != expected_writes
            or plan.get("forbidden") != expected_forbidden):
        raise FamilyMediaError("REFUSED: plan write surface or risk disclosure is invalid.")
    product = plan.get("product_before")
    product_keys = {
        "product_id", "identity", "date_modified_gmt", "before_gallery",
        "protected_projection", "protected_fingerprint",
    }
    expected_identity = {
        "id": spec["product_id"], "name": spec["label"], "sku": spec["sku"],
        "type": spec["type"], "status": spec["status"], "permalink": spec["permalink"],
    }
    projection = product.get("protected_projection") if isinstance(product, dict) else None
    if (not isinstance(product, dict) or set(product) != product_keys
            or product.get("product_id") != spec["product_id"]
            or product.get("identity") != expected_identity
            or not isinstance(projection, dict) or not projection
            or any(field in projection for field in PERMITTED_PRODUCT_READBACK_DRIFT_FIELDS)
            or product.get("protected_fingerprint")
                != hashlib.sha256(canonical(projection).encode("utf-8")).hexdigest()):
        raise FamilyMediaError("REFUSED: staged product evidence schema/identity is invalid.")
    before_gallery = product.get("before_gallery")
    if not isinstance(before_gallery, list) or any(
            not isinstance(row, dict) or set(row) != {"id", "alt"} for row in before_gallery
    ):
        raise FamilyMediaError("REFUSED: staged current gallery evidence is invalid.")
    duplicate = plan.get("duplicate_check")
    validate_duplicate_evidence(duplicate, key)
    staged_reuse = reused_existing_from_duplicate(duplicate, key)
    if reused_existing != staged_reuse:
        raise FamilyMediaError("REFUSED: plan reuse record does not match duplicate proof.")
    assert_family_operation_eligibility(
        key, product, reused_existing,
        expected_reused_existing=staged_reuse,
    )
    if plan.get("guard_contract") != guard_contract(key):
        raise FamilyMediaError("REFUSED: plan does not carry the exact fixed guard contract.")
    require_empty_guard_snapshot(
        plan.get("guard_stage_snapshot"), key, "atomic_snapshot", duplicate
    )
    wanted_operation = operation_sha256(
        key, expected_local, product, plan["approval_manifest"], reused_existing
    )
    if (not isinstance(plan.get("operation_sha256"), str)
            or not secrets.compare_digest(plan["operation_sha256"], wanted_operation)):
        raise FamilyMediaError("REFUSED: stable operation identity is invalid.")
    plan["sha256"] = saved
    _verify_stage_registry(path, plan, raw, created, authenticate=authenticate_registry)
    return plan


def command_stage(args: argparse.Namespace) -> None:
    key = str(args.family)
    validate_approved_manifest()
    local = validate_local_images(key)
    with ui_browser_lock("wordpress", purpose=f"WordPress: read-only media stage for {key}"):
        vault = wc.load_vault()
        product = product_evidence(key, vault)
        allowed = frozenset(Path(row["path"]).resolve() for row in upload_file_evidence(key, local))
        with admin_session(allowed) as admin:
            duplicate = duplicate_scan(admin, {key: family_spec(key)})
            require_no_duplicates(duplicate)
            guard_snapshot = admin.atomic_snapshot(key)
            require_empty_guard_snapshot(guard_snapshot, key, "atomic_snapshot", duplicate)
    path, plan = stage_one(key, local, product, duplicate, guard_snapshot)
    emit({"status": "STAGED_NOT_COMMITTED", "website_writes": 0,
          "plan": str(path), "plan_sha256": plan["sha256"],
          "operation_sha256": plan["operation_sha256"],
          "expires_utc": plan["expires_utc"], "family": key,
          "product_id": plan["product_id"], "product_name": product["identity"]["name"],
          "before_gallery": product["before_gallery"], "files": plan["files"],
          "reused_existing": plan["reused_existing"], "upload_files": plan["upload_files"],
          "final_gallery_template": plan["gallery_payload_template"],
          "risk": risk_disclosure(key), "approval": APPROVAL_WORD})


def command_stage_all(_: argparse.Namespace) -> None:
    validate_approved_manifest()
    local_by_key = {key: validate_local_images(key) for key in FAMILY_KEYS}
    with ui_browser_lock("wordpress", purpose="WordPress: read-only media stage for five fixed families"):
        vault = wc.load_vault()
        products = {key: product_evidence(key, vault) for key in FAMILY_KEYS}
        with admin_session(ALL_FIXED_PATHS) as admin:
            duplicate = duplicate_scan(admin, FAMILY_SPECS)
            require_no_duplicates(duplicate)
            guard_snapshots = {key: admin.atomic_snapshot(key) for key in FAMILY_KEYS}
            for key, proof in guard_snapshots.items():
                require_empty_guard_snapshot(proof, key, "atomic_snapshot", duplicate)
    staged = []
    for key in FAMILY_KEYS:
        path, plan = stage_one(
            key, local_by_key[key], products[key], duplicate, guard_snapshots[key]
        )
        staged.append({"family": key, "product_id": plan["product_id"],
                       "product_name": products[key]["identity"]["name"], "plan": str(path),
                       "plan_sha256": plan["sha256"],
                       "operation_sha256": plan["operation_sha256"],
                       "expires_utc": plan["expires_utc"],
                       "risk": risk_disclosure(key)})
    emit({"status": "FIVE_FAMILY_PLANS_STAGED_NOT_COMMITTED", "website_writes": 0,
          "plans": staged,
          "risk_by_family": {key: risk_disclosure(key) for key in FAMILY_KEYS},
          "approval": "A fresh exact APPROVED is required separately for each plan."})


def record_event(operation_sha256: str, plan_sha256: str,
                 event: str, detail: dict[str, Any]) -> Path:
    path = journal_path(operation_sha256, event)
    write_json(path, {
        "operation_sha256": operation_sha256,
        "plan_sha256": plan_sha256,
        "event": event,
        "recorded_utc": utc_now().isoformat(),
        **detail,
    }, exclusive=True)
    return path


def _attempt_artifacts(operation_sha256: str) -> tuple[Path, Path, Path, Path]:
    return (
        lock_path(operation_sha256), reservation_path(operation_sha256),
        result_path(operation_sha256), JOURNAL_DIR / operation_sha256,
    )


def require_unattempted(operation_sha256: str) -> None:
    attempt, reservation, result, journal = _attempt_artifacts(operation_sha256)
    if attempt.exists() or reservation.exists() or result.exists() or journal.exists():
        raise FamilyMediaError("REFUSED: this stable operation already entered commit; permanent no-retry.")


def _record_indeterminate(plan_path: Path, plan: dict[str, Any], stage: str,
                          uploaded: list[dict[str, Any]],
                          gallery_payload: list[dict[str, Any]] | None,
                          product_may_have_changed: bool,
                          media_may_have_changed: bool,
                          guard_may_be_active: bool,
                          guard_acquisition: dict[str, Any] | None,
                          guard_owner_snapshot: dict[str, Any] | None,
                          guarded_snapshot: dict[str, Any] | None,
                          guard_completion: dict[str, Any] | None,
                          current_upload: dict[str, Any] | None,
                          current_upload_may_have_landed: bool,
                          observed_attachment_id: int | None,
                          exc: Exception) -> dict[str, Any]:
    detail = {
        "status": "INDETERMINATE_NO_RETRY", "plan_sha256": plan["sha256"],
        "operation_sha256": plan["operation_sha256"],
        "updated_utc": utc_now().isoformat(), "stage": stage,
        "reason": type(exc).__name__, "message": str(exc), "uploaded_verified": copy.deepcopy(uploaded),
        "current_upload": copy.deepcopy(current_upload),
        "current_upload_may_have_landed": current_upload_may_have_landed,
        "observed_attachment_id": observed_attachment_id,
        "media_may_have_changed": media_may_have_changed,
        "guard_may_be_active": guard_may_be_active,
        "guard_acquisition": copy.deepcopy(guard_acquisition),
        "guard_owner_snapshot": copy.deepcopy(guard_owner_snapshot),
        "guarded_snapshot": copy.deepcopy(guarded_snapshot),
        "guard_completion": copy.deepcopy(guard_completion),
        "guard_auto_expiry_seconds": GUARD_TTL_SECONDS,
        "gallery_payload": copy.deepcopy(gallery_payload),
        "product_may_have_changed": product_may_have_changed,
        "no_retry": True, "rollback_performed": False, "delete_performed": False,
        "emails": 0,
    }
    evidence_failures: list[str] = []
    try:
        record_event(plan["operation_sha256"], plan["sha256"], "990_indeterminate", detail)
    except Exception as evidence_exc:  # noqa: BLE001 - attempt record still proves no-retry
        evidence_failures.append("journal:" + type(evidence_exc).__name__)
    try:
        append_receipt("product_family_media_indeterminate_no_retry",
                       f"plan={plan_path}; sha256={plan['sha256']}; operation={plan['operation_sha256']}; stage={stage}")
    except Exception as evidence_exc:  # noqa: BLE001
        evidence_failures.append("receipt:" + type(evidence_exc).__name__)
    detail["evidence_write_failures"] = list(evidence_failures)
    try:
        write_json(result_path(plan["operation_sha256"]), detail, exclusive=True)
    except Exception as evidence_exc:  # noqa: BLE001
        evidence_failures.append("result:" + type(evidence_exc).__name__)
        detail["evidence_write_failures"] = list(evidence_failures)
    return detail


def command_commit(args: argparse.Namespace) -> None:
    plan_path = fixed_plan_path(args.plan)
    plan = load_plan(plan_path, authenticate_registry=False)
    require_approval(args.approval)
    plan = load_plan(plan_path, authenticate_registry=True)
    plan_sha256 = plan["sha256"]
    operation_id = plan["operation_sha256"]
    require_unattempted(operation_id)
    key = plan["family"]
    spec = family_spec(key)
    local = validate_local_images(key)
    upload_expected = upload_image_records(key)
    reused_existing = validate_reused_existing_record(key, plan["reused_existing"])
    vault = wc.load_vault()
    if vault.get("declared_permissions") != "read_write":
        raise FamilyMediaError("REFUSED: WooCommerce vault is not declared read/write.")
    if wc.normalize_site_url(str(vault.get("site_url") or "")) != EXACT_ORIGIN:
        raise FamilyMediaError("REFUSED: WooCommerce vault is not the exact FRP Depot origin.")
    allowed = frozenset(Path(row["path"]).resolve() for row in upload_expected)
    uploaded: list[dict[str, Any]] = []
    gallery_payload: list[dict[str, Any]] | None = None
    public_verification: dict[str, Any] | None = None
    guard_acquisition: dict[str, Any] | None = None
    guard_owner_snapshot: dict[str, Any] | None = None
    guarded_snapshot: dict[str, Any] | None = None
    guard_completion: dict[str, Any] | None = None
    stage = "pre_attempt"
    attempt_started = False
    product_may_have_changed = False
    media_may_have_changed = False
    guard_may_be_active = False
    current_upload: dict[str, Any] | None = None
    current_upload_may_have_landed = False
    observed_attachment_id: int | None = None

    try:
        with ui_browser_lock("wordpress", purpose=f"WordPress: approved media commit for {key}"):
            current = product_evidence(key, vault)
            assert_family_operation_eligibility(
                key, current, reused_existing,
                expected_product=plan["product_before"],
                expected_reused_existing=reused_existing,
            )
            with admin_session(allowed) as admin:
                walked = admin.enumerate_library()
                duplicate = duplicate_scan(admin, {key: spec}, walked=walked)
                require_no_duplicates(duplicate)
                fresh_reuse = reused_existing_from_duplicate(duplicate, key)
                assert_family_operation_eligibility(
                    key, current, fresh_reuse,
                    expected_product=plan["product_before"],
                    expected_reused_existing=reused_existing,
                )
                server_snapshot = admin.atomic_snapshot(key)
                require_empty_guard_snapshot(
                    server_snapshot, key, "atomic_snapshot", duplicate
                )
                prepared_guard = admin.prepare_guard_acquire(key)
                known_ids = {int(row["id"]) for row in walked["rows"]}
                require_unattempted(operation_id)
                require_authorization_margin(plan)
                stage = "attempt_marker"
                attempt_started = True
                write_json(lock_path(operation_id), {
                    "status": "ATTEMPT_STARTED", "plan_sha256": plan_sha256,
                    "operation_sha256": operation_id,
                    "plan_path": str(plan_path), "started_utc": utc_now().isoformat(),
                    "result_path_reserved": str(result_path(operation_id)), "no_retry": True,
                }, exclusive=True)

                stage = "guard_acquisition"
                def mark_guard_acquire_attempt() -> None:
                    nonlocal guard_may_be_active
                    guard_may_be_active = True

                guard_acquisition = admin.acquire_prepared_guard(
                    key, prepared_guard, mark_guard_acquire_attempt
                )
                guard_acquisition = require_empty_guard_snapshot(
                    guard_acquisition, key, "guard_acquired", duplicate
                )
                require_guard_completion_margin(guard_acquisition)
                record_event(operation_id, plan_sha256, "050_guard_acquired", {
                    "stage": stage, "guard_acquisition": copy.deepcopy(guard_acquisition),
                    "guard_may_be_active": True,
                })

                stage = "guard_owner_snapshot"
                guard_owner_snapshot = require_empty_guard_snapshot(
                    admin.guarded_snapshot(key), key, "guarded_snapshot", duplicate
                )
                if (guard_owner_snapshot["snapshot_sha256"]
                        != guard_acquisition["snapshot_sha256"]
                        or guard_owner_snapshot["guard_expires_utc"]
                        != guard_acquisition["guard_expires_utc"]):
                    raise FamilyMediaError(
                        "Post-acquire guarded owner proof disagrees with the acquisition baseline."
                    )
                require_guard_completion_margin(guard_owner_snapshot)
                record_event(operation_id, plan_sha256, "060_guard_owner_verified", {
                    "stage": stage,
                    "guard_owner_snapshot": copy.deepcopy(guard_owner_snapshot),
                    "guard_may_be_active": True,
                })

                for expected in upload_expected:
                    stage = f"upload_{expected['position']}"
                    current_upload = {
                        "position": expected["position"], "filename": expected["filename"],
                        "sha256": expected["sha256"], "bytes": expected["bytes"],
                    }
                    current_upload_may_have_landed = False
                    observed_attachment_id = None

                    def mark_submit_attempt() -> None:
                        nonlocal current_upload_may_have_landed, media_may_have_changed
                        current_upload_may_have_landed = True
                        media_may_have_changed = True

                    attachment_id = admin.upload_one(expected, known_ids, mark_submit_attempt)
                    observed_attachment_id = attachment_id
                    known_ids.add(attachment_id)
                    detail = admin.read_attachment(
                        attachment_id, expected_basename=expected["filename"]
                    )
                    data = media_base.download_public_bytes(
                        detail["source_url"], expected_basename=expected["filename"]
                    )
                    if len(data) != expected["bytes"] or not secrets.compare_digest(
                            hashlib.sha256(data).hexdigest(), expected["sha256"]):
                        raise FamilyMediaError("Uploaded public file does not match approved bytes/hash.")
                    uploaded.append({
                        "position": expected["position"], "filename": expected["filename"],
                        "sha256": expected["sha256"], "attachment_id": attachment_id,
                        "source_url": detail["source_url"],
                        "bytes": expected["bytes"], "verified_utc": utc_now().isoformat(),
                    })
                    record_event(operation_id, plan_sha256, f"1{expected['position']}0_upload_verified", {
                        "stage": stage, "uploaded_verified": copy.deepcopy(uploaded),
                        "media_may_have_changed": True,
                    })
                    current_upload = None
                    current_upload_may_have_landed = False
                    observed_attachment_id = None

                attachment_ids = ([reused_existing["attachment_id"]]
                                  if reused_existing is not None else [])
                attachment_ids += [row["attachment_id"] for row in uploaded]
                assert_family_operation_eligibility(
                    key, current, fresh_reuse,
                    expected_product=plan["product_before"],
                    expected_reused_existing=reused_existing,
                    final_attachment_ids=attachment_ids,
                )
                stage = "guarded_snapshot"
                guarded_snapshot = require_guarded_upload_snapshot(
                    admin.guarded_snapshot(key), key,
                    int(guard_acquisition["attachment_total"]), attachment_ids,
                )
                if guarded_snapshot["guard_expires_utc"] != guard_acquisition["guard_expires_utc"]:
                    raise FamilyMediaError("Final guarded snapshot belongs to a different guard expiry.")
                record_event(operation_id, plan_sha256, "450_guarded_snapshot_verified", {
                    "stage": stage, "guarded_snapshot": copy.deepcopy(guarded_snapshot),
                    "uploaded_verified": copy.deepcopy(uploaded),
                })

                stage = "pre_gallery_revalidation"
                pre_put_reuse = read_reused_existing(admin, key)
                if pre_put_reuse != reused_existing:
                    raise FamilyMediaError(
                        "Pre-PUT reused Stub Flange hero proof changed after fresh duplicate scan."
                    )
                for row, expected in zip(uploaded, upload_expected):
                    detail = admin.read_attachment(
                        row["attachment_id"], expected_basename=expected["filename"]
                    )
                    if detail["source_url"] != row["source_url"]:
                        raise FamilyMediaError("Pre-PUT attachment URL changed after upload verification.")
                    data = media_base.download_public_bytes(
                        detail["source_url"], expected_basename=expected["filename"]
                    )
                    if len(data) != expected["bytes"] or not secrets.compare_digest(
                            hashlib.sha256(data).hexdigest(), expected["sha256"]):
                        raise FamilyMediaError("Pre-PUT media revalidation did not match approved bytes/hash.")
                pre_put_product = product_evidence(key, vault)
                assert_family_operation_eligibility(
                    key, pre_put_product, pre_put_reuse,
                    expected_product=plan["product_before"],
                    expected_reused_existing=reused_existing,
                    final_attachment_ids=attachment_ids,
                )
                record_event(operation_id, plan_sha256, "500_pre_put_verified", {
                    "stage": stage, "uploaded_verified": copy.deepcopy(uploaded),
                    "product_fingerprint": plan["product_before"]["protected_fingerprint"],
                })

                gallery_payload = [{"id": value} for value in attachment_ids]
                stage = "guard_completion_margin"
                require_guard_completion_margin(guarded_snapshot)
                stage = "gallery_put_or_readback"
                def mark_put_attempt() -> None:
                    nonlocal product_may_have_changed
                    product_may_have_changed = True

                assign_fixed_gallery(
                    key, attachment_ids, vault, plan["product_before"],
                    pre_put_reuse, mark_put_attempt
                )
                readback, _ = wc.api_get(f"/products/{spec['product_id']}", vault=vault)
                if gallery_ids(readback) != attachment_ids:
                    raise FamilyMediaError("Fresh product read-back did not match gallery IDs/order.")
                if protected_product_projection(readback) != plan["product_before"]["protected_projection"]:
                    raise FamilyMediaError("A protected product field changed during gallery assignment.")

                # Completion immediately follows the verified Woo read-back. No local
                # evidence write or nonessential public navigation is allowed between them.
                stage = "guard_completion"
                def mark_guard_complete_attempt() -> None:
                    nonlocal guard_may_be_active
                    guard_may_be_active = True

                guard_completion = admin.complete_guard(
                    key, attachment_ids, mark_guard_complete_attempt
                )
                if (guard_completion["proof"]["attachment_total"]
                        != guarded_snapshot["attachment_total"]
                        or guard_completion["proof"]["snapshot_sha256"]
                        != guarded_snapshot["snapshot_sha256"]):
                    raise FamilyMediaError(
                        "Guard completion proof disagrees with the final guarded snapshot."
                    )
                guard_may_be_active = False
                record_event(operation_id, plan_sha256, "850_guard_completed_verified", {
                    "stage": stage, "guard_completion": copy.deepcopy(guard_completion),
                    "guard_may_be_active": False,
                })

                stage = "post_completion_final_verification"
                post_completion_product, _ = wc.api_get(
                    f"/products/{spec['product_id']}", vault=vault
                )
                if gallery_ids(post_completion_product) != attachment_ids:
                    raise FamilyMediaError(
                        "Post-completion product read-back did not preserve gallery IDs/order."
                    )
                if (protected_product_projection(post_completion_product)
                        != plan["product_before"]["protected_projection"]):
                    raise FamilyMediaError(
                        "A protected product field changed after guard completion."
                    )
                final_reuse = read_reused_existing(admin, key)
                if final_reuse != reused_existing:
                    raise FamilyMediaError(
                        "Final reused Stub Flange hero proof changed after gallery assignment."
                    )
                for row, expected in zip(uploaded, upload_expected):
                    detail = admin.read_attachment(
                        row["attachment_id"], expected_basename=expected["filename"]
                    )
                    if detail["source_url"] != row["source_url"]:
                        raise FamilyMediaError("Final attachment URL changed after upload verification.")
                    data = media_base.download_public_bytes(
                        detail["source_url"], expected_basename=expected["filename"]
                    )
                    if len(data) != expected["bytes"] or not secrets.compare_digest(
                            hashlib.sha256(data).hexdigest(), expected["sha256"]):
                        raise FamilyMediaError("Final media verification did not match approved bytes/hash.")
                public_verification = admin.verify_public_product(
                    key,
                    ([reused_existing["source_url"]] if reused_existing is not None else [])
                    + [row["source_url"] for row in uploaded]
                )

        final = {
            "status": "COMMITTED_AND_VERIFIED", "plan_sha256": plan_sha256,
            "operation_sha256": operation_id,
            "family": key, "product_id": spec["product_id"],
            "reused_existing_verified": reused_existing,
            "uploaded_verified": uploaded, "gallery": gallery_payload,
            "public_verification": public_verification,
            "guard_acquisition": guard_acquisition,
            "guard_owner_snapshot": guard_owner_snapshot,
            "guarded_snapshot": guarded_snapshot,
            "guard_completion": guard_completion,
            "guard_active_after_verification": False,
            "post_completion_product_verified": True,
            "protected_product_fields_unchanged": True, "updated_utc": utc_now().isoformat(),
            "replay_locked": True, "no_retry": True, "rollback_performed": False,
            "delete_performed": False, "emails": 0,
        }
        verified_event = record_event(operation_id, plan_sha256, "900_committed_verified", final)
        append_receipt("product_family_media_committed_verified",
                       f"plan={plan_path}; sha256={plan_sha256}; operation={operation_id}; evidence={verified_event}")
        write_json(result_path(operation_id), final, exclusive=True)
    except Exception as exc:  # noqa: BLE001 - one boundary owns every post-lock failure
        if not attempt_started:
            raise
        detail = _record_indeterminate(
            plan_path, plan, stage, uploaded, gallery_payload,
            product_may_have_changed, media_may_have_changed,
            guard_may_be_active, guard_acquisition, guard_owner_snapshot,
            guarded_snapshot, guard_completion,
            current_upload, current_upload_may_have_landed,
            observed_attachment_id, exc,
        )
        failures = detail.get("evidence_write_failures") or []
        suffix = f" Evidence-write failures: {', '.join(failures)}." if failures else ""
        raise FamilyMediaIndeterminate(
            f"{stage} failed after the permanent attempt lock. Media may have changed: "
            f"{media_may_have_changed}; product may have changed: {product_may_have_changed}. "
            f"Guard may remain active until its 30-minute expiry: {guard_may_be_active}. "
            f"No retry, delete, cleanup, or rollback.{suffix}"
        ) from exc

    emit(final)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--family", required=True, choices=FAMILY_KEYS)
    stage.set_defaults(func=command_stage)
    stage_all = commands.add_parser("stage-all")
    stage_all.set_defaults(func=command_stage_all)
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
    except (FamilyMediaError, media_base.MediaUploadError, wc.WooError,
            UiLaneBusy, UiLaneLockError, OSError, ValueError) as exc:
        print("ERROR: " + wc.scrub(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
