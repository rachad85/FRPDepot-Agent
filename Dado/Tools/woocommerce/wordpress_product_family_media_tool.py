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
its fixed family, then uploads four exact hard-coded files through the same
already-authenticated WordPress browser. The tool verifies the server's guarded
snapshot, verifies every public copy by SHA-256, makes one PUT to the fixed
existing WooCommerce product containing only the ordered image IDs, and asks
the guard to prove and complete that exact gallery. The guard blocks every
other attachment mutation during its 30-minute owner session; this tool never
reads its nonce or owner cookie.

The guard acquisition, four uploads, gallery PUT, and guard completion are
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
TOOL_VERSION = "1.4.8"
SCHEMA_VERSION = 5
ACTION = "upload_and_assign_fixed_product_family_gallery"
APPROVAL_WORD = "APPROVED"
PLAN_LIFETIME_HOURS = 24
MIN_AUTHORIZATION_MARGIN = timedelta(minutes=2)
MIN_GUARD_COMPLETION_MARGIN = timedelta(minutes=2)
EXACT_ORIGIN = "https://frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
GUARD_PLUGIN_VERSION = "1.0.1"
GUARD_PROOF_SCHEMA = 2
GUARD_ZIP_SHA256 = "656d9cc1f428c409459b38e096ea427763dc69fdb88f8b1d08ec30ec66c1dbbd"
GUARD_PLUGIN_PHP_SHA256 = "65c3381601c6b61bd4a481e9cf082cfaf41d99df838f66c9667c68b037ba5451"
GUARD_RUNTIME_MANIFEST_SHA256 = "2e8fdde2ba90aedb07de5bddb64a4dc4d02b82a2db88deba4605bdbfa6f18d8b"
GUARD_ZIP_PATH = THIS_DIR / "media_mutation_guard" / "frpdepot-media-mutation-guard-1.0.1.zip"
GUARD_PLUGIN_PHP_PATH = (
    THIS_DIR / "media_mutation_guard" / "frpdepot-media-mutation-guard"
    / "frpdepot-media-mutation-guard.php"
)
GUARD_RUNTIME_MANIFEST_PATH = (
    THIS_DIR / "media_mutation_guard" / "frpdepot-media-mutation-guard"
    / "approved-media.json"
)
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
    ROOT / "Dado" / "20_Working" / "product_image_overhaul_20260815"
    / "final_family_review_20260815" / "approved_product_family_media_manifest_20260815.json"
)
APPROVED_MANIFEST_SHA256 = "9020dfbbedec473430fa02a4e07578284ec3da33009444a1467c28aa75cc9748"
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
    """Return one exact attachment ID; reject duplicate or extra query keys."""
    try:
        media_base.assert_origin(value)
        parsed = urlsplit(str(value))
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
    and must pass the exact parser. Relative, credentialed, Unicode-host,
    percent-encoded and dot-segment forms therefore fail closed.
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
            image_record(1, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\stub_flange_real_source_batch_20260815\01_stub_flange_real_source_hero.png", 895251, "aa9c8da37cc4a1ee98b5f0b2c77dd5b369c327a583412938778210562936b3da", 1024, 1024),
            image_record(2, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\stub_flange_real_source_batch_20260815\02_stub_flange_face_view.png", 1136874, "c84ac2a0a4b1d5144d1d595f4e9a77584c8f3eeafa108c691d0e9593c58f4da7", 1024, 1024),
            image_record(3, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\stub_flange_real_source_batch_20260815\03_stub_flange_side_transition.png", 1023979, "10c5377036bae9903d5f7ed13c0c880fcabba3777c4f3b7b0a141783d190dcdb", 1024, 1024),
            image_record(4, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\stub_flange_real_source_batch_20260815\04_stub_flange_owned_group_deterministic.png", 1458889, "5e9d0094d6bea212b7d9b5240776417baf907659fed095ae63450ac6548a9c1b", 1024, 1024),
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
            image_record(1, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\manway_real_source_batch_20260815\01_manway_real_hero.png", 261492, "db886ee83d211d755ffc5e095b3546351f9b01478be73d1a71c5b299a1643be6", 1024, 1024),
            image_record(2, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\manway_real_source_batch_20260815\02_manway_real_alternate.png", 366491, "07d1678e976152a5fdc8ccdc0396a43a92e0055125fffc587508b354c747484b", 1024, 1024),
            image_record(3, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\manway_real_source_batch_20260815\03_manway_real_laminate_detail.png", 301011, "572741ffd433acbc8b2bd36dbd9cb2afe02dbd8b6346978c38a7c0d4f8a352d9", 1024, 1024),
            image_record(4, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\manway_real_source_batch_20260815\04_manway_real_bore_flange_detail.png", 416461, "c5742b9ee84370d2ed6034d891955ff1a7774e89c1f1ad1ffd5b2b5d14bfd753", 1024, 1024),
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
            image_record(1, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\pipe_real_source_batch_20260815\01_pipe_real_group_hero.png", 397351, "ceada300546bf0e7a6452563389783c44bfaa0f4836222fb639e611e7657ddad", 1024, 1024),
            image_record(2, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\pipe_real_source_batch_20260815\02_pipe_real_open_end_laminate_detail.png", 372973, "d032965b5a89a210eb419d3e928ce6c1c274f91265b238b0bb2de6f7010880d8", 1024, 1024),
            image_record(3, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\pipe_real_source_batch_20260815\03_pipe_real_factory_inventory.png", 997867, "098bde709864ec5d8f38545c6c08810c8e73c5b43acadcc4946d78ffea4359eb", 1024, 1024),
            image_record(4, r"C:\FRPDepot\Dado\20_Working\product_image_overhaul_20260815\generated_review_batches\pipe_real_source_batch_20260815\04_pipe_real_skid_logistics.png", 731885, "4a3c3c2f061d9913d65c66a5e87f678c1c620132a533001a736bbf5bb2fc00a1", 1024, 1024),
        ),
    },
}

FAMILY_KEYS = tuple(FAMILY_SPECS)
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

RISK_DISCLOSURE = (
    "NOT ATOMIC; NO ROLLBACK. One server-side guarded-commit acquisition happens first, "
    "then four independent WordPress uploads, then one independent WooCommerce gallery PUT "
    "containing only the four returned media IDs, then one guard-completion write. If "
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
    if (not secrets.compare_digest(actual_zip_sha256, GUARD_ZIP_SHA256)
            or not secrets.compare_digest(actual_plugin_sha256, GUARD_PLUGIN_PHP_SHA256)):
        raise FamilyMediaError("REFUSED: fixed guard package or PHP source hash changed.")
    if not secrets.compare_digest(actual_sha256, GUARD_RUNTIME_MANIFEST_SHA256):
        raise FamilyMediaError("REFUSED: fixed guard runtime manifest hash changed.")
    if (not isinstance(parsed, dict)
            or set(parsed) != {"schema", "source_manifest_sha256", "families"}
            or parsed.get("schema") != 1
            or parsed.get("source_manifest_sha256") != APPROVED_MANIFEST_SHA256
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
        "schema": 1,
        "zip_sha256": actual_zip_sha256,
        "plugin_php_sha256": actual_plugin_sha256,
        "sha256": actual_sha256,
        "source_manifest_sha256": APPROVED_MANIFEST_SHA256,
        "families": list(FAMILY_KEYS),
        "fnpt_present": False,
    }


def guard_contract(key: str) -> dict[str, Any]:
    spec = family_spec(key)
    return {
        "plugin_version": GUARD_PLUGIN_VERSION,
        "proof_schema": GUARD_PROOF_SCHEMA,
        "runtime_manifest": validate_guard_manifest_contract(),
        "live_identity": {
            "checks": ["exact_version", "exact_health", "exact_family_keys", "closed_proof_schema"],
            "live_byte_digest_exposed": False,
            "byte_identity_basis": "pinned_exact_zip_installation_provenance",
        },
        "ttl_seconds": GUARD_TTL_SECONDS,
        "minimum_completion_margin_seconds": int(MIN_GUARD_COMPLETION_MARGIN.total_seconds()),
        "family": key,
        "product_id": spec["product_id"],
        "fixed_private_exception": copy.deepcopy(GUARD_PRIVATE_EXCEPTION),
        "flow": ["atomic_snapshot", "acquire", "four_fixed_uploads",
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


def _guard_match_rows(value: Any, label: str) -> list[dict[str, int]]:
    if not isinstance(value, list):
        raise FamilyMediaError(f"REFUSED: guard {label} evidence is not a list.")
    rows: list[dict[str, int]] = []
    for row in value:
        if (not isinstance(row, dict) or set(row) != {"attachment_id", "fixed_position"}
                or type(row["attachment_id"]) is not int or row["attachment_id"] <= 0
                or type(row["fixed_position"]) is not int
                or row["fixed_position"] not in (1, 2, 3, 4)):
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
    for failure in value["failures"]:
        if (not isinstance(failure, dict) or set(failure) != {"attachment_id", "reason"}
                or type(failure["attachment_id"]) is not int
                or not isinstance(failure["reason"], str) or not failure["reason"]):
            raise FamilyMediaError("REFUSED: guard snapshot failure evidence is invalid.")
    private_exceptions = _guard_private_exception_rows(value["private_exceptions"])
    if value["attachment_total"] != value["hashed_total"] + len(private_exceptions):
        raise FamilyMediaError("REFUSED: guard snapshot counters do not reconcile.")
    _guard_match_rows(value["name_conflicts"], "name-conflict")
    _guard_match_rows(value["hash_conflicts"], "hash-conflict")
    _guard_match_rows(value["fixed_matches"], "fixed-match")
    if mode in {"guard_acquired", "guarded_snapshot"}:
        _guard_timestamp(value["guard_expires_utc"], "expiry")
        if type(value["reserved_uploads"]) is not int or not 0 <= value["reserved_uploads"] <= 4:
            raise FamilyMediaError("REFUSED: guard reserved-upload count is invalid.")
    return copy.deepcopy(value)


def require_empty_guard_snapshot(proof: Any, key: str, mode: str,
                                 duplicate: dict[str, Any] | None = None) -> dict[str, Any]:
    checked = validate_guard_snapshot_proof(
        proof, key, mode, mode in {"guard_acquired", "guarded_snapshot"}
    )
    if (checked["complete"] is not True or checked["failures"]
            or checked["attachment_total"] != checked["hashed_total"] + 1
            or checked["name_conflicts"] or checked["hash_conflicts"]
            or checked["fixed_matches"]):
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
    wanted = [{"attachment_id": attachment_id, "fixed_position": position}
              for position, attachment_id in enumerate(attachment_ids, 1)]
    if (checked["complete"] is not True or checked["failures"]
            or checked["attachment_total"] != baseline_total + 4
            or checked["hashed_total"] != baseline_total + 3
            or checked["reserved_uploads"] != 4
            or sorted(checked["name_conflicts"], key=lambda row: row["fixed_position"]) != wanted
            or sorted(checked["hash_conflicts"], key=lambda row: row["fixed_position"]) != wanted
            or sorted(checked["fixed_matches"], key=lambda row: row["fixed_position"]) != wanted):
        raise FamilyMediaError("Guarded snapshot does not prove the four exact uploaded attachments.")
    return checked


def validate_guard_completion_proof(value: Any, key: str,
                                    attachment_ids: list[int]) -> dict[str, Any]:
    expected_keys = {
        "schema", "plugin_version", "mode", "family", "product_id",
        "attachment_ids", "attachment_total", "snapshot_sha256",
    }
    spec = family_spec(key)
    if (not isinstance(value, dict) or set(value) != expected_keys
            or value["schema"] != GUARD_PROOF_SCHEMA or value["plugin_version"] != GUARD_PLUGIN_VERSION
            or value["mode"] != "guard_completed" or value["family"] != key
            or value["product_id"] != spec["product_id"]
            or value["attachment_ids"] != attachment_ids
            or type(value["attachment_total"]) is not int or value["attachment_total"] < 4):
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
    return FAMILY_SPECS[key]


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
    if [row["position"] for row in evidence] != [1, 2, 3, 4]:
        raise FamilyMediaError("REFUSED: every family must contain exactly positions 1-4.")
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
    expected_fnpt = {
        "status": "approved existing benchmark; excluded from the five-family media tool",
        "separate_plan_required": True,
    }
    if (manifest.get("schema_version") != 1
            or manifest.get("status") != "APPROVED_WORKING_COLLECTION_NOT_PUBLISHED"
            or manifest.get("approval") != expected_approval
            or manifest.get("fnpt") != expected_fnpt):
        raise FamilyMediaError("REFUSED: approved collection manifest status/scope is invalid.")
    families = manifest.get("families")
    if not isinstance(families, dict) or set(families) != set(FAMILY_KEYS):
        raise FamilyMediaError("REFUSED: approved collection manifest does not contain exactly five families.")
    fields = ("position", "path", "filename", "sha256", "bytes", "width", "height", "format", "mode")
    for key, spec in FAMILY_SPECS.items():
        family = families.get(key)
        images = family.get("accepted_images") if isinstance(family, dict) else None
        if not isinstance(family, dict) or family.get("product_id") != spec["product_id"]:
            raise FamilyMediaError(f"REFUSED: approved collection identity is invalid for {key}.")
        if not isinstance(images, list) or len(images) != 4 or not all(isinstance(row, dict) for row in images):
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
                         on_put_attempt: Any | None = None) -> dict[str, Any]:
    spec = family_spec(key)
    if (not isinstance(attachment_ids, list) or len(attachment_ids) != 4
            or any(type(value) is not int or value <= 0 for value in attachment_ids)
            or len(set(attachment_ids)) != 4):
        raise FamilyMediaError("REFUSED: gallery assignment requires four unique positive attachment IDs.")
    fresh_product = product_evidence(key, vault)
    assert_product_eligibility(key, fresh_product, expected_product)
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

    def _row_records(self) -> list[dict[str, Any]]:
        """Preserve a unique attachment ID when the list filename cell is blank.

        WordPress attachment 1767 proved this live shape on 2026-08-15: five
        links all named one attachment, while the list filename selector was
        empty. The base scanner correctly fails closed, but discards the ID.
        Keeping the unique ID lets `enumerate_library` resolve the filename from
        that attachment's own fixed, read-only identity screen instead of
        sampling the row or weakening completeness.
        """
        rows = self._page.query_selector_all(media_base.LIST_ROW_SELECTOR)
        found: list[dict[str, Any]] = []
        for row in rows:
            if (not row.query_selector_all("a[href]")
                    and len(row.query_selector_all(media_base.EMPTY_TABLE_CELL_SELECTOR)) == 1):
                continue
            ids: set[int] = set()
            invalid_edit_candidate = False
            for link in row.query_selector_all(media_base.ROW_LINK_SELECTOR):
                href = str(link.get_attribute("href") or "")
                attachment_id = parse_exact_attachment_edit_url(href)
                if attachment_id is not None:
                    ids.add(attachment_id)
                elif is_attachment_edit_candidate_url(href):
                    invalid_edit_candidate = True
            if invalid_edit_candidate or len(ids) != 1:
                found.append({"id": None, "filename": "", "stem": ""})
                continue
            attachment_id = ids.pop()
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
        terminal_empty_page = False
        for page_number in range(1, media_base.MAX_LIBRARY_PAGES + 1):
            self._goto(media_base.library_page_url(page_number))
            pages = page_number
            page_rows = self._row_records()
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
        return {
            "rows": rows,
            "total": total,
            "pages": pages,
            "complete": complete,
            "unidentified": unidentified,
        }

    def upload_one(self, expected: dict[str, Any], known_ids: set[int],
                   on_submit_attempt: Any | None = None) -> int:
        resolved = Path(str(expected.get("path") or "")).resolve()
        if resolved not in self._allowed_paths or resolved not in ALL_FIXED_PATHS:
            raise FamilyMediaError("REFUSED: only the four fixed files in this plan may be uploaded.")
        payload = verified_upload_payload(expected)
        self._goto(media_base.MEDIA_NEW_URL)
        inputs = self._page.query_selector_all(media_base.FILE_INPUT_SELECTOR)
        submits = self._page.query_selector_all(media_base.SUBMIT_SELECTOR)
        if len(inputs) != 1 or len(submits) != 1:
            raise FamilyMediaError("REFUSED: WordPress does not expose exactly one file input and submit control.")
        inputs[0].set_input_files(payload, timeout=media_base.ACTION_TIMEOUT_MS)
        self._assert_landed()
        if on_submit_attempt is not None:
            on_submit_attempt()
        submits[0].click(timeout=media_base.ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=media_base.UPLOAD_TIMEOUT_MS)
        self._assert_landed()
        return self._identify_upload(known_ids)

    def verify_public_product(self, key: str, verified_source_urls: list[str]) -> dict[str, Any]:
        spec = family_spec(key)
        permalink = spec["permalink"]
        product_id = spec["product_id"]
        if (not isinstance(verified_source_urls, list) or len(verified_source_urls) != 4
                or any(not isinstance(value, str) for value in verified_source_urls)):
            raise FamilyMediaError("Public verification requires four ordered verified attachment URLs.")
        for value, image in zip(verified_source_urls, spec["images"]):
            media_base.assert_public_upload_url(value, expected_basename=image["filename"])
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
            if not isinstance(gallery, list) or len(gallery) != 4:
                raise FamilyMediaError("Public product page did not render exactly four gallery items.")
            for row, image, source_url in zip(gallery, spec["images"], verified_source_urls):
                if (not isinstance(row, dict) or row.get("link_count") != 1
                        or row.get("image_count") != 1 or row.get("complete") is not True
                        or int(row.get("natural_width") or 0) <= 0
                        or int(row.get("natural_height") or 0) <= 0):
                    raise FamilyMediaError("Public product gallery contains a missing or broken rendered image.")
                href = urlsplit(str(row.get("href") or ""))
                current_src = urlsplit(str(row.get("current_src") or ""))
                source = urlsplit(source_url)
                media_base.assert_public_upload_url(str(row.get("href") or ""),
                                                    expected_basename=image["filename"])
                if href != source:
                    raise FamilyMediaError("Public product gallery original link is not the verified attachment URL.")
                wanted_stem = Path(image["filename"]).stem
                current_name = Path(current_src.path).name
                media_base.assert_public_upload_url(str(row.get("current_src") or ""))
                if Path(current_src.path).parent != Path(source.path).parent:
                    raise FamilyMediaError("Public product gallery rendered source left the verified upload directory.")
                original_name = image["filename"]
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
                "fixed_images_in_order": 4, "rendered_gallery_items": 4,
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
        "target_families": sorted(target_specs),
    }
    evidence["complete"] = bool(
        evidence["enumeration_complete"] and evidence["private_exception_proven"]
        and evidence["hash_complete"]
        and evidence["recheck_complete"] and evidence["snapshot_stable"]
        and evidence["recheck_hash_complete"] and evidence["content_stable"]
        and evidence["final_complete"] and evidence["final_snapshot_stable"]
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
        raise FamilyMediaError("REFUSED: complete Media Library duplicate absence was not proven.")
    if evidence.get("name_conflicts") or evidence.get("hash_conflicts"):
        raise FamilyMediaError("REFUSED: Media Library already contains a fixed name or SHA-256.")


def validate_duplicate_evidence(evidence: Any, key: str) -> None:
    expected_keys = {
        "checked_utc", "library_total", "enumerated", "pages_read", "enumeration_complete",
        "image_rows", "image_hashes_completed", "hash_failures", "hash_bytes_read",
        "hash_complete", "recheck_total", "recheck_enumerated", "recheck_pages",
        "recheck_complete", "snapshot_stable", "recheck_image_hashes_completed",
        "recheck_hash_failures", "recheck_hash_bytes_read", "recheck_hash_complete",
        "content_stable", "final_total", "final_enumerated", "final_pages",
        "final_complete", "final_snapshot_stable", "name_conflicts", "hash_conflicts",
        "private_exception", "private_exception_proven", "target_families", "complete",
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
    if not isinstance(evidence["name_conflicts"], list) or not isinstance(evidence["hash_conflicts"], list):
        raise FamilyMediaError("REFUSED: duplicate conflict evidence is invalid.")
    if (evidence["private_exception_proven"] is not True
            or evidence["private_exception"] != {
                "attachment_id": GUARD_PRIVATE_EXCEPTION["attachment_id"],
                "filename": Path(GUARD_PRIVATE_EXCEPTION["attached_file"]).name,
            }):
        raise FamilyMediaError("REFUSED: duplicate evidence lacks the exact private attachment.")
    require_no_duplicates(evidence)


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


def operation_sha256(key: str, local: list[dict[str, Any]],
                     product: dict[str, Any], approval_manifest: dict[str, Any]) -> str:
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
    guard_stage_snapshot = require_empty_guard_snapshot(
        guard_stage_snapshot, key, "atomic_snapshot", duplicate
    )
    operation = operation_sha256(key, local, product, approval_manifest)
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
        "product_before": product,
        "duplicate_check": duplicate,
        "guard_contract": guard_contract(key),
        "guard_stage_snapshot": guard_stage_snapshot,
        "gallery_payload_template": [{"position": row["position"], "sha256": row["sha256"],
                                      "filename": row["filename"]} for row in local],
        "risk": RISK_DISCLOSURE,
        "writes_if_committed": [
            "one fixed server-side guarded-commit acquisition",
            "four independent authenticated WordPress media uploads",
            f"one PUT /products/{spec['product_id']} containing only four image IDs",
            "one fixed guard-completion state transition after complete verification",
        ],
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
        "guard_contract", "guard_stage_snapshot",
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
    if plan.get("approval_manifest") != validate_approved_manifest():
        raise FamilyMediaError("REFUSED: plan does not carry the exact approved collection artifact.")
    expected_template = [{"position": row["position"], "sha256": row["sha256"],
                          "filename": row["filename"]} for row in expected_local]
    expected_writes = [
        "one fixed server-side guarded-commit acquisition",
        "four independent authenticated WordPress media uploads",
        f"one PUT /products/{spec['product_id']} containing only four image IDs",
        "one fixed guard-completion state transition after complete verification",
    ]
    expected_forbidden = [
        "delete", "rollback", "retry", "media edit", "alt text", "product content",
        "price", "stock", "status", "category", "attribute", "customer", "order",
        "payment", "plugin", "setting", "user", "email", "Zoho", "Drive", "FNPT",
    ]
    if (plan.get("gallery_payload_template") != expected_template
            or plan.get("risk") != RISK_DISCLOSURE
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
    if plan.get("guard_contract") != guard_contract(key):
        raise FamilyMediaError("REFUSED: plan does not carry the exact fixed guard contract.")
    require_empty_guard_snapshot(
        plan.get("guard_stage_snapshot"), key, "atomic_snapshot", duplicate
    )
    wanted_operation = operation_sha256(key, expected_local, product, plan["approval_manifest"])
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
        allowed = frozenset(Path(row["path"]).resolve() for row in local)
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
          "risk": RISK_DISCLOSURE, "approval": APPROVAL_WORD})


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
                       "expires_utc": plan["expires_utc"]})
    emit({"status": "FIVE_FAMILY_PLANS_STAGED_NOT_COMMITTED", "website_writes": 0,
          "plans": staged, "risk": RISK_DISCLOSURE,
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
        "reason": type(exc).__name__, "uploaded_verified": copy.deepcopy(uploaded),
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
    vault = wc.load_vault()
    if vault.get("declared_permissions") != "read_write":
        raise FamilyMediaError("REFUSED: WooCommerce vault is not declared read/write.")
    if wc.normalize_site_url(str(vault.get("site_url") or "")) != EXACT_ORIGIN:
        raise FamilyMediaError("REFUSED: WooCommerce vault is not the exact FRP Depot origin.")
    allowed = frozenset(Path(row["path"]).resolve() for row in local)
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
            if current != plan["product_before"]:
                raise FamilyMediaError("REFUSED: fixed product changed after staging; stage a fresh plan.")
            with admin_session(allowed) as admin:
                walked = admin.enumerate_library()
                duplicate = duplicate_scan(admin, {key: spec}, walked=walked)
                require_no_duplicates(duplicate)
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

                for expected in spec["images"]:
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

                attachment_ids = [row["attachment_id"] for row in uploaded]
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
                for row, expected in zip(uploaded, spec["images"]):
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
                if pre_put_product != plan["product_before"]:
                    raise FamilyMediaError("Fixed product changed while uploads were running.")
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
                    key, attachment_ids, vault, plan["product_before"], mark_put_attempt
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
                for row, expected in zip(uploaded, spec["images"]):
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
                    key, [row["source_url"] for row in uploaded]
                )

        final = {
            "status": "COMMITTED_AND_VERIFIED", "plan_sha256": plan_sha256,
            "operation_sha256": operation_id,
            "family": key, "product_id": spec["product_id"],
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
