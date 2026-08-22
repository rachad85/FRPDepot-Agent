#!/usr/bin/env python
"""One fixed successor correction for four unreadable orphan media records.

Commissioned by Rachad Homsi on 2026-08-21 after the predecessor cleanup
operation was permanently locked by a recorded write attempt on attachment
5521 while all four exact records still appeared in the complete Media Library
and in the guard's unreadable-original failure set. This is not a replay of the
predecessor plan: it has a distinct fixed correction action, isolated state,
and requires the immutable predecessor event as part of its source evidence.

The tool may permanently delete only attachment IDs 5521, 5523, 5525 and 5527,
after a new read-only immutable 24-hour plan and Rachad's later exact APPROVED.
It deliberately replaces the failed wp.media model fetch with a closed
attachment-edit DOM identity route plus complete library, guard, product and
variation proofs. The four deletes are independent WordPress actions. They are
NOT atomic and there is NO rollback, restore, retry, upload, edit, attachment
replacement, product write, plugin write, generic browser, order/customer/
payment or mail route. Any failure after the new permanent attempt lock leaves
the correction INDETERMINATE_NO_RETRY; earlier deletions remain live and later
ones are not attempted.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Iterator
from urllib.parse import parse_qsl, urljoin, urlsplit

ROOT = Path(r"C:\FRPDepot")
THIS_DIR = Path(__file__).resolve().parent
sys.path.append(str(THIS_DIR))
sys.path.append(str(THIS_DIR.parent / "common"))
import woocommerce_common as wc  # noqa: E402
import wordpress_packing_ring_media_tool as media_base  # noqa: E402
import wordpress_product_family_media_tool as family_media  # noqa: E402
from ui_lane_lock import UiLaneBusy, UiLaneLockError, ui_browser_lock  # noqa: E402

TOOL_NAME = "FRP Depot Fixed Orphan Media Record Correction Tool"
TOOL_VERSION = "1.0.1"
SCHEMA_VERSION = 2
OPERATION_SCHEMA_VERSION = 1
ACTION = "correct_four_fixed_unreadable_orphan_records_after_locked_cleanup"
APPROVAL_WORD = "APPROVED"
ORIGIN = "https://frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
PLAN_LIFETIME = timedelta(hours=24)
COMMIT_EXPIRY_MARGIN = timedelta(minutes=45)
PLAN_DIR = ROOT / "Dado" / "20_Working" / "wordpress_orphan_media_correction_plans"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
LOCAL_STATE = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "FRPDepot-WordPress" / "orphan-media-correction-v1"
REGISTRY_KEY = LOCAL_STATE / "stage-registry.key"
REGISTRY_DIR = LOCAL_STATE / "stages"
ATTEMPT_DIR = LOCAL_STATE / "attempts"
RESULT_DIR = LOCAL_STATE / "results"
EVENT_DIR = LOCAL_STATE / "events"
RUNTIME_TEMP = ROOT / "Dado" / "Temp" / "playwright-runtime"
PREDECESSOR_OPERATION_SHA256 = "7045aee2e8fb340ffee491c9dfd5413b50b6c30c6d26cc6ad2379d0a9eb27dae"
PREDECESSOR_PLAN_SHA256 = "f13801a092261916cbef87512c624ee85f24247d6255aa70b52a4a38a512bd2d"
PREDECESSOR_EVENT_SHA256 = "e1c81bd795708a73ac5840c363e82ff665febdcdc9f5ca1aea3b995a8e297b9d"
PREDECESSOR_STATE = (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    / "FRPDepot-WordPress" / "orphan-media-cleanup"
)
PREDECESSOR_EVENT = (
    PREDECESSOR_STATE / "events"
    / PREDECESSOR_OPERATION_SHA256 / "01-5521-attempted.json"
)
PINNED_GUARD_PLUGIN_VERSION = "1.0.5"
PINNED_GUARD_PROOF_SCHEMA = 2
PINNED_GUARD_ZIP_SHA256 = "f001bb217ae7aa16b2dd1f0cd08bcb0f6d825bb013c98e1a886ef1f2f436db74"
PINNED_GUARD_PLUGIN_PHP_SHA256 = "7d09ad8eb45e552e7dfb31ecf50b800ea50ea1c8074a8e1a899e375f47a2f887"
PINNED_GUARD_RUNTIME_MANIFEST_SHA256 = "23e1800e779ca7a4068c6eff090b9b53524cd3e3cefad9e53f5337ecfcefe565"
WORDPRESS_CORE_VERSION = "7.0.3"
WORDPRESS_MEDIA_LIST_SOURCE_SHA256 = "e480fa867d3a6b63c5e9bb973f860707d7f9f69b1a6bc7a3803f1a61d7c14763"
WORDPRESS_EDIT_FORM_SOURCE_SHA256 = "a74058bfbc0768353858872e0a170c34220ca52f9d726f37ecbb325e75b9aab6"
WORDPRESS_MEDIA_SOURCE_SHA256 = "9bbe54a96d9c62e50edb13ccd7215db78cac215b9907aaa024cf40c85749fa4d"
SOURCE_OPERATION_SHA256 = "877ff133b0e4fbf560b3be5877b755c72e5c33dc217c7e4affb23c1a314e2a26"
SOURCE_PLAN_SHA256 = "0403dcf8b8cc597086439f801dd8493ae6c0b1461887be3f1dc3f0f2ba79fab5"
SOURCE_RESULT_SHA256 = "0fa72aceb74bbf231618d4b09026f6d7442962a4827c839bfa005187f4ea8ddf"
SUPERSEDED_PLAN_SHA256S: frozenset[str] = frozenset()
SOURCE_RESULT = (
    ROOT / "Dado" / "20_Working" / "wordpress_product_family_media_plans"
    / "results" / f"{SOURCE_OPERATION_SHA256}.result.json"
)
PRIVATE_EXCEPTION_ID = 1832
TARGETS: tuple[dict[str, Any], ...] = (
    {
        "attachment_id": 5521,
        "filename": "01_manway_real_hero.png",
        "sha256": "db886ee83d211d755ffc5e095b3546351f9b01478be73d1a71c5b299a1643be6",
        "bytes": 261492,
        "source_url": "https://frpdepots.com/wp-content/uploads/2026/08/01_manway_real_hero.png",
    },
    {
        "attachment_id": 5523,
        "filename": "02_manway_real_alternate.png",
        "sha256": "07d1678e976152a5fdc8ccdc0396a43a92e0055125fffc587508b354c747484b",
        "bytes": 366491,
        "source_url": "https://frpdepots.com/wp-content/uploads/2026/08/02_manway_real_alternate.png",
    },
    {
        "attachment_id": 5525,
        "filename": "03_manway_real_laminate_detail.png",
        "sha256": "572741ffd433acbc8b2bd36dbd9cb2afe02dbd8b6346978c38a7c0d4f8a352d9",
        "bytes": 301011,
        "source_url": "https://frpdepots.com/wp-content/uploads/2026/08/03_manway_real_laminate_detail.png",
    },
    {
        "attachment_id": 5527,
        "filename": "04_manway_real_bore_flange_detail.png",
        "sha256": "c5742b9ee84370d2ed6034d891955ff1a7774e89c1f1ad1ffd5b2b5d14bfd753",
        "bytes": 416461,
        "source_url": "https://frpdepots.com/wp-content/uploads/2026/08/04_manway_real_bore_flange_detail.png",
    },
)
TARGET_IDS = tuple(row["attachment_id"] for row in TARGETS)
TARGET_BY_ID = {row["attachment_id"]: row for row in TARGETS}
PROTECTED_SURVIVOR_GALLERY: tuple[dict[str, Any], ...] = (
    {"attachment_id": 5823, "fixed_position": 1,
     "source_url": "https://frpdepots.com/wp-content/uploads/2026/08/01_manway_real_hero-1.png"},
    {"attachment_id": 5824, "fixed_position": 2,
     "source_url": "https://frpdepots.com/wp-content/uploads/2026/08/02_manway_real_alternate-1.png"},
    {"attachment_id": 5825, "fixed_position": 3,
     "source_url": "https://frpdepots.com/wp-content/uploads/2026/08/03_manway_real_laminate_detail-1.png"},
    {"attachment_id": 5826, "fixed_position": 4,
     "source_url": "https://frpdepots.com/wp-content/uploads/2026/08/04_manway_real_bore_flange_detail-1.png"},
)
PLAN_KEYS = frozenset({
    "schema_version", "tool", "tool_version", "origin", "action", "created_utc",
    "expires_utc", "nonce", "operation_sha256", "targets", "source_evidence",
    "before", "after_expected", "writes_if_committed", "risk", "forbidden", "sha256",
})


class CleanupError(RuntimeError):
    pass


class IndeterminateError(CleanupError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sanitized_row_link_shape(value: Any) -> dict[str, Any]:
    """Describe one row link without retaining any query value or credential."""
    raw = str(value or "")
    raw_form = "other"
    if raw.startswith("/") and not raw.startswith("//"):
        raw_form = "root_relative"
    elif raw.startswith("https://"):
        raw_form = "absolute_https"
    try:
        resolved = urljoin(media_base.library_page_url(1), raw)
        parsed = urlsplit(resolved)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return {
            "raw_form": raw_form, "malformed": True, "same_origin": False,
            "path": "", "query_keys": [], "actions": [], "fragment": False,
            "credentials": False, "resolved_exact_attachment_edit": False,
        }
    origin = urlsplit(ORIGIN)
    return {
        "raw_form": raw_form,
        "malformed": False,
        "same_origin": (
            parsed.scheme == origin.scheme and parsed.hostname == origin.hostname
            and parsed.port == origin.port
            and not parsed.username and not parsed.password
        ),
        "path": parsed.path,
        "query_keys": sorted(key for key, _item in pairs),
        "actions": sorted({item for key, item in pairs if key == "action"}),
        "fragment": bool(parsed.fragment),
        "credentials": bool(parsed.username or parsed.password),
        "resolved_exact_attachment_edit": (
            family_media.parse_exact_attachment_edit_url(resolved) is not None
        ),
    }


def digest_for(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def require_approval(value: str) -> None:
    if not isinstance(value, str) or value != APPROVAL_WORD:
        raise CleanupError("Approval must be the exact unpadded uppercase word APPROVED.")


def is_reparse(path: Path) -> bool:
    try:
        item = path.lstat()
    except OSError:
        return True
    return stat.S_ISLNK(item.st_mode) or bool(
        getattr(item, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": utc_now().isoformat(), "action": action, "evidence": evidence}
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def exclusive_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("ascii")
    pending = path.with_name(f".{path.name}.{secrets.token_hex(12)}.pending")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = None
    try:
        descriptor = os.open(str(pending), flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            if handle.write(payload) != len(payload):
                raise OSError("immutable evidence write was short")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(str(pending), str(path))
    except FileExistsError as exc:
        raise CleanupError("REFUSED: immutable evidence already exists; no replay or overwrite.") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            pending.unlink(missing_ok=True)
        except OSError:
            pass


def registry_key() -> bytes:
    REGISTRY_KEY.parent.mkdir(parents=True, exist_ok=True)
    if not REGISTRY_KEY.exists():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(str(REGISTRY_KEY), flags, 0o600)
        except FileExistsError:
            descriptor = None
        if descriptor is not None:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(secrets.token_bytes(32))
                handle.flush()
                os.fsync(handle.fileno())
    if (not REGISTRY_KEY.is_file() or is_reparse(REGISTRY_KEY)
            or getattr(REGISTRY_KEY.stat(), "st_nlink", 1) != 1):
        raise CleanupError("REFUSED: local stage-registry key is missing or aliased.")
    value = REGISTRY_KEY.read_bytes()
    if len(value) != 32:
        raise CleanupError("REFUSED: local stage-registry key is invalid.")
    return value


def registry_mac(core: dict[str, Any]) -> str:
    return hmac.new(registry_key(), canonical(core).encode("ascii"), hashlib.sha256).hexdigest()


def validate_fixed_contract() -> dict[str, Any]:
    if TARGET_IDS != (5521, 5523, 5525, 5527) or len(TARGET_BY_ID) != 4:
        raise CleanupError("REFUSED: fixed target identity changed.")
    if any(set(row) != {"attachment_id", "filename", "sha256", "bytes", "source_url"}
           for row in TARGETS):
        raise CleanupError("REFUSED: fixed target schema changed.")
    for row in TARGETS:
        if (not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                or int(row["bytes"]) <= 0
                or row["source_url"] != f"{ORIGIN}/wp-content/uploads/2026/08/{row['filename']}"):
            raise CleanupError("REFUSED: fixed target bytes or URL contract changed.")
    if (family_media.GUARD_PLUGIN_VERSION != PINNED_GUARD_PLUGIN_VERSION
            or family_media.GUARD_PROOF_SCHEMA != PINNED_GUARD_PROOF_SCHEMA
            or family_media.GUARD_ZIP_SHA256 != PINNED_GUARD_ZIP_SHA256
            or family_media.GUARD_PLUGIN_PHP_SHA256 != PINNED_GUARD_PLUGIN_PHP_SHA256
            or family_media.GUARD_RUNTIME_MANIFEST_SHA256
            != PINNED_GUARD_RUNTIME_MANIFEST_SHA256):
        raise CleanupError("REFUSED: pinned Media Mutation Guard contract drifted.")
    try:
        family_media.validate_guard_manifest_contract()
    except family_media.FamilyMediaError as exc:
        raise CleanupError("REFUSED: pinned Media Mutation Guard files failed validation.") from exc
    if (not SOURCE_RESULT.is_file() or is_reparse(SOURCE_RESULT)
            or getattr(SOURCE_RESULT.stat(), "st_nlink", 1) != 1):
        raise CleanupError("REFUSED: fixed source result is missing or aliased.")
    try:
        source_bytes = SOURCE_RESULT.read_bytes()
        source = json.loads(source_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CleanupError("REFUSED: fixed source result is unreadable.") from exc
    uploaded = source.get("uploaded_verified") if isinstance(source, dict) else None
    projected = [
        {key: row.get(key) for key in ("attachment_id", "filename", "sha256", "bytes", "source_url")}
        for row in uploaded
    ] if isinstance(uploaded, list) and all(isinstance(row, dict) for row in uploaded) else []
    if (hashlib.sha256(source_bytes).hexdigest() != SOURCE_RESULT_SHA256
            or source.get("status") != "INDETERMINATE_NO_RETRY"
            or source.get("operation_sha256") != SOURCE_OPERATION_SHA256
            or source.get("plan_sha256") != SOURCE_PLAN_SHA256
            or source.get("product_may_have_changed") is not False
            or source.get("gallery_payload") is not None
            or source.get("delete_performed") is not False
            or projected != list(TARGETS)):
        raise CleanupError("REFUSED: fixed source result no longer proves these four orphan uploads.")
    if (not PREDECESSOR_EVENT.is_file() or is_reparse(PREDECESSOR_EVENT)
            or getattr(PREDECESSOR_EVENT.stat(), "st_nlink", 1) != 1):
        raise CleanupError("REFUSED: immutable predecessor attempt evidence is missing or aliased.")
    try:
        predecessor_bytes = PREDECESSOR_EVENT.read_bytes()
        predecessor = json.loads(predecessor_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CleanupError("REFUSED: immutable predecessor attempt evidence is unreadable.") from exc
    if (hashlib.sha256(predecessor_bytes).hexdigest() != PREDECESSOR_EVENT_SHA256
            or predecessor.get("schema") != 1
            or predecessor.get("tool") != "FRP Depot Fixed Orphan Media Cleanup Tool"
            or predecessor.get("operation_sha256") != PREDECESSOR_OPERATION_SHA256
            or predecessor.get("plan_sha256") != PREDECESSOR_PLAN_SHA256
            or predecessor.get("position") != 1
            or predecessor.get("attachment_id") != 5521
            or predecessor.get("status") != "write_attempted_no_retry"):
        raise CleanupError("REFUSED: predecessor attempt evidence is not the exact locked cleanup event.")
    if not PREDECESSOR_STATE.is_dir() or is_reparse(PREDECESSOR_STATE):
        raise CleanupError("REFUSED: predecessor state root is missing or aliased.")
    observed_predecessor_files: list[str] = []
    try:
        for entry in PREDECESSOR_STATE.rglob("*"):
            if is_reparse(entry):
                raise CleanupError("REFUSED: predecessor state contains an alias or reparse point.")
            if entry.is_file():
                observed_predecessor_files.append(entry.relative_to(PREDECESSOR_STATE).as_posix())
    except OSError as exc:
        raise CleanupError("REFUSED: predecessor state could not be enumerated completely.") from exc
    expected_predecessor_file = PREDECESSOR_EVENT.relative_to(PREDECESSOR_STATE).as_posix()
    if sorted(observed_predecessor_files) != [expected_predecessor_file]:
        raise CleanupError("REFUSED: predecessor state is not the exact one-event permanent lock.")
    return {
        "source_result_path": str(SOURCE_RESULT.resolve()),
        "source_result_sha256": SOURCE_RESULT_SHA256,
        "source_operation_sha256": SOURCE_OPERATION_SHA256,
        "source_plan_sha256": SOURCE_PLAN_SHA256,
        "source_status": "INDETERMINATE_NO_RETRY",
        "source_product_may_have_changed": False,
        "source_gallery_payload": None,
        "source_delete_performed": False,
        "predecessor_event_path": str(PREDECESSOR_EVENT.resolve()),
        "predecessor_event_sha256": PREDECESSOR_EVENT_SHA256,
        "predecessor_operation_sha256": PREDECESSOR_OPERATION_SHA256,
        "predecessor_plan_sha256": PREDECESSOR_PLAN_SHA256,
        "predecessor_attachment_id": 5521,
        "predecessor_status": "write_attempted_no_retry",
        "correction_reason": "replace_failed_wp_media_model_fetch_with_closed_edit_dom_identity",
    }


def target_spec(attachment_id: int) -> dict[str, Any]:
    if type(attachment_id) is not int or attachment_id not in TARGET_BY_ID:
        raise CleanupError("REFUSED: attachment ID is outside the four fixed orphan records.")
    return TARGET_BY_ID[attachment_id]


def public_url_state(url: str, *, require_present: bool = False,
                     expected_sha256: str | None = None,
                     expected_bytes: int | None = None) -> dict[str, Any]:
    """Anonymous redirect-free proof of exact bytes or already-missing state."""
    media_base.assert_public_upload_url(url, allowed_extensions=(".png",))
    try:
        data = media_base.download_public_bytes(url, allowed_extensions=(".png",))
    except media_base.MediaUploadError as exc:
        match = re.search(r"HTTP (404|410)\.", str(exc))
        if not match:
            raise CleanupError("REFUSED: registered public media state is ambiguous.") from exc
        if require_present:
            raise CleanupError("REFUSED: fixed original public bytes are unexpectedly absent.") from exc
        return {"url": url, "state": "not_found", "http_status": int(match.group(1))}
    digest = hashlib.sha256(data).hexdigest()
    if require_present and (digest != expected_sha256 or len(data) != expected_bytes):
        raise CleanupError("REFUSED: fixed original public bytes no longer match source provenance.")
    return {"url": url, "state": "present", "http_status": 200,
            "bytes": len(data), "sha256": digest}


def public_evidence(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prove the four fixed records point only at their now-unreadable originals.

    The immutable source result already proves the original upload bytes and
    hashes. The correction eligibility state instead requires each current
    attachment-edit screen to expose the same fixed URL while that public URL
    is now 404/410, matching the guard's unreadable_original evidence.
    """
    if [row.get("attachment_id") for row in attachments] != list(TARGET_IDS):
        raise CleanupError("REFUSED: attachment identity list is not the exact fixed set.")
    rows = [public_url_state(target_spec(row["attachment_id"])["source_url"])
            for row in attachments]
    if ([row["url"] for row in rows] != sorted(item["source_url"] for item in TARGETS)
            or any(row["state"] != "not_found" for row in rows)):
        raise CleanupError("REFUSED: the four fixed unreadable original URLs are not exactly absent.")
    return rows


def public_absence_evidence(urls: list[str]) -> list[dict[str, Any]]:
    rows = [public_url_state(url) for url in sorted(set(urls))]
    if any(row["state"] != "not_found" for row in rows):
        raise IndeterminateError("One or more registered public media files remain available.")
    return rows


def library_projection(snapshot: dict[str, Any], *,
                       expected_present_ids: tuple[int, ...] = TARGET_IDS) -> dict[str, Any]:
    rows = snapshot.get("rows") if isinstance(snapshot, dict) else None
    target_parent_states = (
        snapshot.get("target_parent_states") if isinstance(snapshot, dict) else None
    )
    if (snapshot.get("complete") is not True or type(snapshot.get("total")) is not int
            or not isinstance(rows, list) or len(rows) != snapshot["total"]):
        summary = {
            "complete": snapshot.get("complete") if isinstance(snapshot, dict) else None,
            "total": snapshot.get("total") if isinstance(snapshot, dict) else None,
            "rows_type": type(rows).__name__,
            "rows_count": len(rows) if isinstance(rows, list) else None,
            "pages": snapshot.get("pages") if isinstance(snapshot, dict) else None,
            "unidentified": snapshot.get("unidentified") if isinstance(snapshot, dict) else None,
            "sanitized_row_link_diagnostic": (
                snapshot.get("sanitized_row_link_diagnostic")
                if isinstance(snapshot, dict) else None
            ),
        }
        raise CleanupError(
            "REFUSED: complete Media Library identity enumeration failed: "
            + canonical(summary)
        )
    projected: list[dict[str, Any]] = []
    for row in rows:
        if (not isinstance(row, dict) or type(row.get("id")) is not int
                or row["id"] <= 0 or not isinstance(row.get("filename"), str)
                or not row["filename"]):
            raise CleanupError("REFUSED: Media Library identity row is invalid.")
        projected.append({"id": row["id"], "filename": row["filename"]})
    if len({row["id"] for row in projected}) != len(projected):
        raise CleanupError("REFUSED: Media Library attachment IDs are not unique.")
    projected.sort(key=lambda row: row["id"])
    fixed = [row for row in projected if row["id"] in TARGET_BY_ID]
    survivors = [row for row in projected if row["id"] not in TARGET_BY_ID]
    if any(attachment_id not in TARGET_BY_ID for attachment_id in expected_present_ids):
        raise CleanupError("REFUSED: expected Media Library target set is outside fixed scope.")
    expected = [
        {"id": row["attachment_id"], "filename": row["filename"]}
        for row in TARGETS if row["attachment_id"] in expected_present_ids
    ]
    if fixed != expected:
        raise CleanupError("REFUSED: fixed Media Library presence/absence state has drifted.")
    expected_parent_states = [
        {"id": attachment_id, "state": "unattached", "attach_control_exact": True}
        for attachment_id in expected_present_ids
    ]
    if target_parent_states != expected_parent_states:
        raise CleanupError(
            "REFUSED: fixed Media Library Uploaded-to state is not exact."
        )
    return {
        "total": snapshot["total"],
        "rows": projected,
        "rows_sha256": digest_for(projected),
        "survivor_rows_sha256": digest_for(survivors),
        "target_rows": fixed,
        "target_parent_states": expected_parent_states,
    }


def strict_get_all(endpoint: str, params: dict[str, Any], vault: dict[str, Any],
                   *, max_items: int = 20000) -> list[dict[str, Any]]:
    """Closed, total-proven WooCommerce GET paginator; never drops malformed rows."""
    product_fields = "id,status,images,variations,description,short_description,downloads,meta_data"
    variation_fields = "id,image,description,downloads,meta_data"
    if endpoint == "/products":
        expected_params = {"_fields": product_fields}
    elif re.fullmatch(r"/products/[1-9][0-9]*/variations", endpoint):
        expected_params = {"_fields": variation_fields}
    else:
        raise CleanupError("REFUSED: WooCommerce read endpoint is outside the correction allowlist.")
    if params != expected_params or max_items != media_base.MAX_LIBRARY_ROWS:
        raise CleanupError("REFUSED: WooCommerce read parameters are outside the correction allowlist.")
    per_page = 100
    collected: list[dict[str, Any]] = []
    expected_total: int | None = None
    expected_pages: int | None = None
    for page in range(1, 201):
        query = dict(params)
        query.update({"per_page": per_page, "page": page})
        data, headers = wc.api_get(endpoint, query, vault)
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise CleanupError("REFUSED: WooCommerce complete-reference walk returned malformed rows.")
        try:
            total = int(headers["x-wp-total"])
            total_pages = int(headers["x-wp-totalpages"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CleanupError("REFUSED: WooCommerce complete-reference totals are absent.") from exc
        if total < 0 or total_pages < 0 or (total == 0) != (total_pages == 0):
            raise CleanupError("REFUSED: WooCommerce complete-reference totals are invalid.")
        if expected_total is None:
            expected_total, expected_pages = total, total_pages
        elif (total, total_pages) != (expected_total, expected_pages):
            raise CleanupError("REFUSED: WooCommerce totals drifted during complete-reference walk.")
        collected.extend(data)
        if len(collected) > max_items:
            raise CleanupError("REFUSED: WooCommerce complete-reference walk exceeded its bound.")
        if page >= total_pages:
            if len(collected) != total:
                raise CleanupError("REFUSED: WooCommerce complete-reference row count did not reconcile.")
            ids = [row.get("id") for row in collected]
            if (any(type(value) is not int or value <= 0 for value in ids)
                    or len(ids) != len(set(ids))):
                raise CleanupError("REFUSED: WooCommerce complete-reference IDs are invalid or repeated.")
            return collected
    raise CleanupError("REFUSED: WooCommerce complete-reference walk exceeded 200 pages.")


def _contains_fixed_marker(value: Any) -> list[str]:
    text = canonical(value)
    return sorted({marker for row in TARGETS
                   for marker in (row["filename"], row["source_url"])
                   if marker in text})


def _contains_fixed_id_marker(value: Any) -> list[int]:
    text = canonical(value)
    return [attachment_id for attachment_id in TARGET_IDS
            if re.search(rf"(?<![0-9]){attachment_id}(?![0-9])", text)]


def product_projection(vault: dict[str, Any]) -> dict[str, Any]:
    fields = "id,status,images,variations,description,short_description,downloads,meta_data"
    products = strict_get_all("/products", {"_fields": fields}, vault,
                              max_items=media_base.MAX_LIBRARY_ROWS)
    projected: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    variations_checked = 0
    protected_gallery: list[dict[str, Any]] | None = None
    for product in products:
        if (set(product) != set(fields.split(","))
                or type(product.get("id")) is not int or product["id"] <= 0
                or not isinstance(product.get("status"), str)
                or not isinstance(product.get("images"), list)
                or not isinstance(product.get("variations"), list)
                or not isinstance(product.get("description"), str)
                or not isinstance(product.get("short_description"), str)
                or not isinstance(product.get("downloads"), list)
                or not isinstance(product.get("meta_data"), list)):
            raise CleanupError("REFUSED: WooCommerce product projection schema is not exact.")
        product_id = product["id"]
        image_ids: list[int] = []
        for position, image in enumerate(product.get("images") or [], 1):
            if not isinstance(image, dict) or type(image.get("id")) is not int or image["id"] <= 0:
                raise CleanupError("REFUSED: WooCommerce product gallery projection is invalid.")
            image_ids.append(image["id"])
            if image["id"] in TARGET_BY_ID:
                references.append({
                    "kind": "product_image", "product_id": product_id,
                    "position": position, "attachment_id": image["id"],
                })
        if product_id == 1397:
            if protected_gallery is not None:
                raise CleanupError("REFUSED: the protected FRP MANWAY product is duplicated.")
            protected_gallery = [
                {"attachment_id": image["id"],
                 "fixed_position": position,
                 "source_url": str(image.get("src") or "")}
                for position, image in enumerate(product.get("images") or [], 1)
            ]
            if protected_gallery != list(PROTECTED_SURVIVOR_GALLERY):
                raise CleanupError(
                    "REFUSED: the protected FRP MANWAY live gallery is not the exact "
                    "5823/5824/5825/5826 survivor set."
                )
        markers = _contains_fixed_marker({
            "images": product.get("images") or [],
            "description": product.get("description") or "",
            "short_description": product.get("short_description") or "",
            "downloads": product.get("downloads") or [],
            "meta_data": product.get("meta_data") or [],
        })
        if markers:
            references.append({"kind": "product_text_or_meta", "product_id": product_id,
                               "markers": markers})
        id_markers = _contains_fixed_id_marker({
            "description": product["description"],
            "short_description": product["short_description"],
            "downloads": product["downloads"],
            "meta_data": product["meta_data"],
        })
        if id_markers:
            references.append({"kind": "product_numeric_reference", "product_id": product_id,
                               "attachment_ids": id_markers})
        expected_variation_ids = product.get("variations") or []
        if (not isinstance(expected_variation_ids, list)
                or any(type(value) is not int or value <= 0 for value in expected_variation_ids)
                or len(expected_variation_ids) != len(set(expected_variation_ids))):
            raise CleanupError("REFUSED: WooCommerce product variation identity list is invalid.")
        variation_projection: list[dict[str, Any]] = []
        if expected_variation_ids:
            variation_fields = "id,image,description,downloads,meta_data"
            variations = strict_get_all(
                f"/products/{product_id}/variations", {"_fields": variation_fields}, vault,
                max_items=media_base.MAX_LIBRARY_ROWS,
            )
            if sorted(row["id"] for row in variations) != sorted(expected_variation_ids):
                raise CleanupError("REFUSED: WooCommerce variation walk is incomplete.")
            variations_checked += len(variations)
            for variation in variations:
                if (set(variation) != set(variation_fields.split(","))
                        or type(variation.get("id")) is not int or variation["id"] <= 0
                        or variation.get("image") is not None
                        and not isinstance(variation.get("image"), dict)
                        or not isinstance(variation.get("description"), str)
                        or not isinstance(variation.get("downloads"), list)
                        or not isinstance(variation.get("meta_data"), list)):
                    raise CleanupError("REFUSED: WooCommerce variation projection schema is not exact.")
                image = variation.get("image")
                image_id = None
                if image is not None:
                    if not isinstance(image, dict) or type(image.get("id")) is not int or image["id"] <= 0:
                        raise CleanupError("REFUSED: WooCommerce variation image projection is invalid.")
                    image_id = image["id"]
                    if image_id in TARGET_BY_ID:
                        references.append({
                            "kind": "variation_image", "product_id": product_id,
                            "variation_id": variation["id"], "attachment_id": image_id,
                        })
                variation_markers = _contains_fixed_marker({
                    "image": image, "description": variation.get("description") or "",
                    "downloads": variation.get("downloads") or [],
                    "meta_data": variation.get("meta_data") or [],
                })
                if variation_markers:
                    references.append({
                        "kind": "variation_text_or_meta", "product_id": product_id,
                        "variation_id": variation["id"], "markers": variation_markers,
                    })
                variation_id_markers = _contains_fixed_id_marker({
                    "description": variation["description"],
                    "downloads": variation["downloads"],
                    "meta_data": variation["meta_data"],
                })
                if variation_id_markers:
                    references.append({
                        "kind": "variation_numeric_reference", "product_id": product_id,
                        "variation_id": variation["id"],
                        "attachment_ids": variation_id_markers,
                    })
                variation_projection.append({"variation_id": variation["id"], "image_id": image_id})
        variation_projection.sort(key=lambda row: row["variation_id"])
        projected.append({
            "product_id": product_id, "status": str(product.get("status") or ""),
            "image_ids": image_ids, "variations": variation_projection,
        })
    projected.sort(key=lambda row: row["product_id"])
    if protected_gallery is None:
        raise CleanupError("REFUSED: the protected FRP MANWAY product 1397 is missing.")
    if references:
        raise CleanupError("REFUSED: a fixed attachment ID, filename or URL is referenced by a product or variation.")
    return {
        "products_checked": len(projected),
        "variations_checked": variations_checked,
        "product_and_variation_galleries_sha256": digest_for(projected),
        "target_references": [],
        "protected_survivor_gallery": protected_gallery,
        "strict_totals_proven": True,
    }


def guard_projection(proof: dict[str, Any], *,
                     expected_failure_ids: tuple[int, ...]) -> dict[str, Any]:
    if (not isinstance(proof, dict)
            or proof.get("schema") != PINNED_GUARD_PROOF_SCHEMA
            or proof.get("plugin_version") != PINNED_GUARD_PLUGIN_VERSION):
        raise CleanupError("REFUSED: server-side guard is not the pinned 1.0.5/schema-2 contract.")
    try:
        proof = family_media.validate_guard_snapshot_proof(
            proof, "open_manway", "atomic_snapshot", False
        )
    except family_media.FamilyMediaError as exc:
        raise CleanupError("REFUSED: server-side guard proof failed the full closed validator.") from exc
    failures = proof.get("failures") if isinstance(proof, dict) else None
    private = proof.get("private_exceptions") if isinstance(proof, dict) else None
    if not isinstance(failures, list) or not isinstance(private, list):
        raise CleanupError("REFUSED: server-side guard proof is incomplete.")
    failure_projection = sorted(
        ({"attachment_id": row.get("attachment_id"), "reason": row.get("reason")}
         for row in failures if isinstance(row, dict)),
        key=lambda row: int(row.get("attachment_id") or 0),
    )
    wanted = [
        {"attachment_id": attachment_id, "reason": "unreadable_original"}
        for attachment_id in expected_failure_ids
    ]
    private_ids = sorted(
        int(row.get("attachment_id") or 0) for row in private if isinstance(row, dict)
    )
    if (proof.get("schema") != PINNED_GUARD_PROOF_SCHEMA
            or proof.get("plugin_version") != PINNED_GUARD_PLUGIN_VERSION
            or proof.get("mode") != "atomic_snapshot"
            or proof.get("family") != "open_manway"
            or proof.get("guard_active") is not False):
        raise CleanupError("REFUSED: server-side guard identity or activity state drifted.")
    if failure_projection != wanted:
        observed = ", ".join(
            f"{row['attachment_id']}:{row['reason']}" for row in failure_projection
        ) or "none"
        raise CleanupError(
            "REFUSED: server-side guard failure set is not the fixed target set: " + observed
        )
    if private_ids != [PRIVATE_EXCEPTION_ID]:
        raise CleanupError("REFUSED: server-side guard private exception set drifted.")
    conflict_fields = ("name_conflicts", "hash_conflicts", "fixed_matches")
    conflict_projection: dict[tuple[int, int], dict[str, Any]] = {}
    for field in conflict_fields:
        rows = proof.get(field)
        if not isinstance(rows, list):
            raise CleanupError(f"REFUSED: server-side guard {field} is malformed.")
        for row in rows:
            if (not isinstance(row, dict) or type(row.get("attachment_id")) is not int
                    or type(row.get("fixed_position")) is not int
                    or row["attachment_id"] <= 0
                    or not 1 <= row["fixed_position"] <= family_media.expected_image_count("open_manway")):
                raise CleanupError(f"REFUSED: server-side guard {field} row is malformed.")
            key = (row["attachment_id"], row["fixed_position"])
            detail = conflict_projection.setdefault(key, {
                "attachment_id": row["attachment_id"],
                "fixed_position": row["fixed_position"],
                "name_match": False,
                "hash_match": False,
                "exact_fixed_match": False,
            })
            detail[{
                "name_conflicts": "name_match",
                "hash_conflicts": "hash_match",
                "fixed_matches": "exact_fixed_match",
            }[field]] = True
    details = sorted(
        conflict_projection.values(),
        key=lambda row: (row["fixed_position"], row["attachment_id"]),
    )
    # Guard v1.0.5 protects the six premium Open Manway images, not the
    # current four-image survivor gallery pinned above. Therefore these old
    # survivor bytes must produce no fixed-image conflict under the active
    # guard. Their survival is proved separately and exactly by the complete
    # WooCommerce product/variation projection.
    expected_details: list[dict[str, Any]] = []
    if details != expected_details:
        raise CleanupError(
            "REFUSED: server-side guard reported a fixed-media conflict outside the "
            "exact v1.0.5 cleanup contract: "
            + canonical(details)
        )
    complete_wanted = not expected_failure_ids
    if proof.get("complete") is not complete_wanted:
        raise CleanupError("REFUSED: server-side guard completeness disagrees with the fixed cleanup state.")
    attachment_total = proof.get("attachment_total")
    hashed_total = proof.get("hashed_total")
    if (type(attachment_total) is not int or type(hashed_total) is not int
            or attachment_total != hashed_total + len(private) + len(failures)):
        raise CleanupError("REFUSED: server-side guard counters do not reconcile.")
    return {
        "plugin_version": proof["plugin_version"],
        "family": proof["family"],
        "attachment_total": attachment_total,
        "hashed_total": hashed_total,
        "failures": failure_projection,
        "private_exception_ids": private_ids,
        "name_conflicts": [],
        "hash_conflicts": [],
        "fixed_matches": [],
        "guard_fixed_conflicts": details,
        "complete": complete_wanted,
        "snapshot_sha256": str(proof.get("snapshot_sha256") or ""),
    }


def assert_correction_eligible(state: dict[str, Any],
                               expected_present_ids: tuple[int, ...]) -> dict[str, Any]:
    """One closed predicate shared by stage, commit and the delete adapter."""
    allowed_states = tuple(TARGET_IDS[index:] for index in range(len(TARGET_IDS) + 1))
    if expected_present_ids not in allowed_states:
        raise CleanupError("REFUSED: correction eligibility target state is not a fixed suffix.")
    if not isinstance(state, dict) or set(state) != {
        "guard", "library", "attachments", "products", "public_files"
    }:
        raise CleanupError("REFUSED: correction eligibility projection schema is not exact.")
    guard = state["guard"]
    expected_failures = [
        {"attachment_id": attachment_id, "reason": "unreadable_original"}
        for attachment_id in expected_present_ids
    ]
    if (not isinstance(guard, dict)
            or guard.get("plugin_version") != PINNED_GUARD_PLUGIN_VERSION
            or guard.get("family") != "open_manway"
            or guard.get("failures") != expected_failures
            or guard.get("private_exception_ids") != [PRIVATE_EXCEPTION_ID]
            or guard.get("name_conflicts") != []
            or guard.get("hash_conflicts") != []
            or guard.get("fixed_matches") != []
            or guard.get("guard_fixed_conflicts") != []
            or guard.get("complete") is not (not expected_present_ids)
            or not re.fullmatch(r"[0-9a-f]{64}", str(guard.get("snapshot_sha256") or ""))):
        raise CleanupError("REFUSED: correction eligibility guard state is not exact.")
    library = state["library"]
    expected_target_rows = [
        {"id": row["attachment_id"], "filename": row["filename"]}
        for row in TARGETS if row["attachment_id"] in expected_present_ids
    ]
    if (not isinstance(library, dict)
            or set(library) != {
                "total", "rows", "rows_sha256", "survivor_rows_sha256",
                "target_rows", "target_parent_states",
            }
            or type(library.get("total")) is not int or library["total"] < len(expected_present_ids)
            or library.get("target_rows") != expected_target_rows
            or library.get("target_parent_states") != [
                {"id": attachment_id, "state": "unattached", "attach_control_exact": True}
                for attachment_id in expected_present_ids
            ]
            or not re.fullmatch(r"[0-9a-f]{64}", str(library.get("rows_sha256") or ""))
            or not re.fullmatch(r"[0-9a-f]{64}", str(library.get("survivor_rows_sha256") or ""))):
        raise CleanupError("REFUSED: correction eligibility Media Library state is not exact.")
    expected_attachments = []
    for attachment_id in expected_present_ids:
        spec = target_spec(attachment_id)
        expected_attachments.append({
            "attachment_id": attachment_id,
            "filename": spec["filename"],
            "source_url": spec["source_url"],
            "edit_identity": {
                "post_id": str(attachment_id),
                "post_type": "attachment",
                "original_post_status": "inherit",
                "uploaded_to_box": "absent",
            },
            "library_parent_state": {
                "id": attachment_id,
                "state": "unattached",
                "attach_control_exact": True,
            },
            "identity_route": "canonical_attachment_edit_dom_plus_complete_library_parent",
            "source_provenance": "immutable_locked_upload_result",
            "registered_urls": [spec["source_url"]],
            "delete_control_exact": True,
        })
    if state["attachments"] != expected_attachments:
        raise CleanupError("REFUSED: correction eligibility attachment DOM identity is not exact.")
    products = state["products"]
    if (not isinstance(products, dict)
            or set(products) != {
                "products_checked", "variations_checked",
                "product_and_variation_galleries_sha256", "target_references",
                "protected_survivor_gallery", "strict_totals_proven",
            }
            or type(products.get("products_checked")) is not int
            or products["products_checked"] <= 0
            or type(products.get("variations_checked")) is not int
            or products["variations_checked"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}",
                                str(products.get("product_and_variation_galleries_sha256") or ""))
            or products.get("target_references") != []
            or products.get("protected_survivor_gallery") != list(PROTECTED_SURVIVOR_GALLERY)
            or products.get("strict_totals_proven") is not True):
        raise CleanupError("REFUSED: correction eligibility product/variation state is not exact.")
    public_files = state["public_files"]
    expected_urls = sorted(row["source_url"] for row in TARGETS)
    if (not isinstance(public_files, list)
            or [row.get("url") for row in public_files] != expected_urls
            or any(not isinstance(row, dict)
                   or set(row) != {"url", "state", "http_status"}
                   or row["state"] != "not_found"
                   or row["http_status"] not in (404, 410)
                   for row in public_files)):
        raise CleanupError("REFUSED: correction eligibility public-file absence is not exact.")
    return state


class CleanupAdmin:
    """Narrow actor: read evidence plus one exact attachment-delete click only."""

    def __init__(self, page: Any):
        self._page = page
        self._reader = family_media.ProductFamilyAdmin(page, frozenset())

    def atomic_snapshot(self, family: str) -> dict[str, Any]:
        if family != "open_manway":
            raise CleanupError("REFUSED: only the fixed open_manway guard snapshot is reachable.")
        button = self._reader._family_guard_button(
            family, "snapshot", "Guard inactive"
        )
        # The guard POST can render its complete proof before Playwright's
        # element-click navigation waiter settles. The live 2026-08-20 read-only
        # diagnostic hit that exact shape: click completed, then timed out waiting
        # for scheduled navigation. Suppress only the click's implicit wait and
        # replace it with one explicit navigation expectation established before
        # the POST. This method has no write callback and remains snapshot-only.
        with self._page.expect_navigation(
                wait_until="domcontentloaded", timeout=media_base.UPLOAD_TIMEOUT_MS):
            button.click(
                timeout=media_base.ACTION_TIMEOUT_MS, no_wait_after=True
            )
        self._reader._assert_landed()
        proof = self._reader._read_guard_proof()
        return family_media.validate_guard_snapshot_proof(
            proof, family, "atomic_snapshot", False
        )

    def _sanitized_row_link_diagnostic(self) -> dict[str, Any]:
        """Read page-one link shapes without retaining URL query values."""
        self._reader._goto(media_base.library_page_url(1))
        rows = self._page.query_selector_all(media_base.LIST_ROW_SELECTOR)
        shape_counts: dict[str, int] = {}
        link_count = 0
        for row in rows:
            for link in row.query_selector_all(media_base.ROW_LINK_SELECTOR):
                link_count += 1
                shape = sanitized_row_link_shape(link.get_attribute("href"))
                key = canonical(shape)
                shape_counts[key] = shape_counts.get(key, 0) + 1
        return {
            "page": 1,
            "row_count": len(rows),
            "link_count": link_count,
            "shapes": [
                {"count": shape_counts[key], "shape": json.loads(key)}
                for key in sorted(shape_counts)
            ],
        }

    def enumerate_library(self) -> dict[str, Any]:
        diagnostic = self._sanitized_row_link_diagnostic()
        snapshot = self._reader.enumerate_library()
        if not isinstance(snapshot, dict):
            raise CleanupError("REFUSED: Media Library reader returned a malformed snapshot.")
        rows = snapshot.get("rows")
        pages = snapshot.get("pages")
        if (snapshot.get("complete") is not True or not isinstance(rows, list)
                or type(pages) is not int or not 1 <= pages <= media_base.MAX_LIBRARY_PAGES):
            raise CleanupError("REFUSED: Media Library parent proof cannot use an incomplete walk.")
        present_ids = tuple(
            attachment_id for attachment_id in TARGET_IDS
            if any(isinstance(row, dict) and row.get("id") == attachment_id for row in rows)
        )
        found: dict[int, dict[str, Any]] = {}
        for page_number in range(1, pages + 1):
            self._reader._goto(media_base.library_page_url(page_number))
            for attachment_id in present_ids:
                matches = self._page.query_selector_all(
                    f"{media_base.LIST_ROW_SELECTOR}#post-{attachment_id}"
                )
                if len(matches) > 1 or (matches and attachment_id in found):
                    raise CleanupError(
                        "REFUSED: fixed Media Library row identity is duplicated."
                    )
                if not matches:
                    continue
                cells = matches[0].query_selector_all("td.parent.column-parent")
                if len(cells) != 1:
                    raise CleanupError(
                        "REFUSED: fixed Media Library Uploaded-to cell is unavailable."
                    )
                cell = cells[0]
                links = cell.query_selector_all("a[href]")
                controls = cell.query_selector_all(
                    'a[href="#the-list"].hide-if-no-js.aria-button-if-js'
                )
                text = " ".join(str(cell.inner_text() or "").casefold().split())
                control_text = (
                    " ".join(str(controls[0].inner_text() or "").casefold().split())
                    if len(controls) == 1 else ""
                )
                onclick = (
                    " ".join(str(controls[0].get_attribute("onclick") or "").split())
                    if len(controls) == 1 else ""
                )
                expected_onclick = (
                    f"findPosts.open( 'media[]', '{attachment_id}' ); return false;"
                )
                if (text != "(unattached) attach" or len(links) != 1
                        or len(controls) != 1 or control_text != "attach"
                        or onclick != expected_onclick):
                    raise CleanupError(
                        "REFUSED: fixed Media Library row is not exactly unattached."
                    )
                found[attachment_id] = {
                    "id": attachment_id,
                    "state": "unattached",
                    "attach_control_exact": True,
                }
        if tuple(found) != present_ids:
            raise CleanupError(
                "REFUSED: complete Media Library walk did not prove every fixed parent state."
            )
        snapshot["target_parent_states"] = [found[attachment_id] for attachment_id in present_ids]
        snapshot["sanitized_row_link_diagnostic"] = diagnostic
        return snapshot

    def leave_on_media_list(self) -> None:
        self._reader.leave_on_media_list()

    def read_attachment(self, attachment_id: int, *, expected_basename: str) -> dict[str, Any]:
        target_spec(attachment_id)
        return self._reader.read_attachment(
            attachment_id, expected_basename=expected_basename,
            allowed_extensions=(".png",),
        )

    @staticmethod
    def _delete_control_exact(control: Any, attachment_id: int) -> bool:
        return bool(control.evaluate("""(el, expectedId) => {
            try {
                const u = new URL(el.href, window.location.href);
                const keys = [...u.searchParams.keys()].sort();
                const wanted = ['_wpnonce','action','post'].sort();
                return el.tagName === 'A'
                    && el.matches('#delete-action > a.submitdelete.deletion')
                    && u.origin === window.location.origin
                    && u.pathname === '/wp-admin/post.php'
                    && keys.length === wanted.length && keys.every((v,i) => v === wanted[i])
                    && u.searchParams.getAll('action').length === 1
                    && u.searchParams.get('action') === 'delete'
                    && u.searchParams.getAll('post').length === 1
                    && u.searchParams.get('post') === String(expectedId)
                    && u.searchParams.getAll('_wpnonce').length === 1
                    && /^[A-Za-z0-9_-]{8,64}$/.test(u.searchParams.get('_wpnonce'))
                    && !u.username && !u.password && !u.hash;
            } catch (_) { return false; }
        }""", attachment_id))

    def _edit_identity(self, attachment_id: int) -> dict[str, str]:
        forms = self._page.query_selector_all("form#post")
        if len(forms) != 1:
            raise CleanupError("REFUSED: exact attachment edit form is unavailable.")
        required = (
            ("post_id", 'input#post_ID[name="post_ID"][type="hidden"]'),
            ("post_type", 'input#post_type[name="post_type"][type="hidden"]'),
            ("original_post_status",
             'input#original_post_status[name="original_post_status"][type="hidden"]'),
        )
        matched = {
            key: self._page.query_selector_all(selector)
            for key, selector in required
        }
        if any(len(elements) != 1 for elements in matched.values()):
            inputs = self._page.query_selector_all("form#post input")
            shapes = sorted((
                {
                    "id": str(element.get_attribute("id") or "")[:80],
                    "name": str(element.get_attribute("name") or "")[:80],
                    "type": str(element.get_attribute("type") or "")[:32].casefold(),
                }
                for element in inputs[:100]
            ), key=canonical)
            diagnostic = {
                "required_counts": {
                    key: len(matched[key]) for key, _selector in required
                },
                "form_input_count": len(inputs),
                "form_input_shapes": shapes,
                "shape_limit": 100,
                "values_retained": False,
            }
            raise CleanupError(
                "REFUSED: exact attachment edit identity fields are unavailable; "
                "sanitized no-value diagnostic=" + canonical(diagnostic)
            )
        values = {
            key: str(matched[key][0].input_value() or "")
            for key, _selector in required
        }
        if (values["post_id"] != str(attachment_id)
                or values["post_type"] != "attachment"
                or values["original_post_status"] != "inherit"):
            raise CleanupError("REFUSED: fixed attachment edit identity changed.")
        # WordPress 7.0.3 renders `.misc-pub-uploadedto` only when
        # get_post(post_parent) resolves to a live parent. The complete Media
        # Library walk independently proves the canonical `(Unattached) Attach`
        # parent-column state. No nonexistent `post_parent` hidden input is
        # invented by this tool.
        if self._page.query_selector_all(".misc-pub-uploadedto"):
            raise CleanupError("REFUSED: fixed attachment edit page reports a live parent.")
        return {**values, "uploaded_to_box": "absent"}

    def _edit_dom_projection(self, attachment_id: int, *,
                             library_parent_state: dict[str, Any]) -> dict[str, Any]:
        """Project only fields already rendered on the fixed attachment edit page.

        This intentionally does not call any model, REST, asynchronous admin,
        or page-supplied URL reader. The predecessor failed specifically at
        that metadata-fetch boundary. Identity comes from the exact canonical edit
        route, the attachment URL/name/type boxes, hidden post identity fields,
        the complete Media Library parent row, and the single allowlisted
        permanent-delete control.
        """
        spec = target_spec(attachment_id)
        if library_parent_state != {
            "id": attachment_id,
            "state": "unattached",
            "attach_control_exact": True,
        }:
            raise CleanupError("REFUSED: fixed attachment parent proof is not exact.")
        identity = self.read_attachment(
            attachment_id, expected_basename=spec["filename"]
        )
        landed = urlsplit(str(self._page.url or ""))
        landed_pairs = parse_qsl(landed.query, keep_blank_values=True)
        if (landed.scheme != "https" or landed.netloc != "frpdepots.com"
                or landed.path != "/wp-admin/post.php"
                or sorted(landed_pairs) != [("action", "edit"), ("post", str(attachment_id))]
                or len(landed_pairs) != 2 or landed.fragment
                or landed.username or landed.password):
            raise CleanupError("REFUSED: attachment reader did not land on the exact fixed edit route.")
        if identity != {
            "attachment_id": attachment_id,
            "filename": spec["filename"],
            "source_url": spec["source_url"],
            "extension": ".png",
            "filetype_matches_name": True,
        }:
            raise CleanupError("REFUSED: fixed attachment edit DOM identity is not exact.")
        edit_identity = self._edit_identity(attachment_id)
        controls = [control for control in self._page.query_selector_all(
            "#delete-action > a.submitdelete.deletion"
        ) if "delete permanently" == " ".join(
            str(control.inner_text() or "").casefold().split()
        )]
        if len(controls) != 1 or not self._delete_control_exact(controls[0], attachment_id):
            raise CleanupError("REFUSED: exact fixed permanent-delete control is unavailable.")
        return {
            "attachment_id": attachment_id,
            "filename": spec["filename"],
            "source_url": spec["source_url"],
            "edit_identity": edit_identity,
            "library_parent_state": library_parent_state,
            "identity_route": "canonical_attachment_edit_dom_plus_complete_library_parent",
            "source_provenance": "immutable_locked_upload_result",
            "registered_urls": [spec["source_url"]],
            "delete_control_exact": True,
        }

    def read_target(self, attachment_id: int, *,
                    library_parent_state: dict[str, Any]) -> dict[str, Any]:
        return self._edit_dom_projection(
            attachment_id, library_parent_state=library_parent_state
        )

    def delete_one(self, attachment_id: int, on_write_attempt: Any, *,
                   eligibility_state: dict[str, Any],
                   expected_present_ids: tuple[int, ...]) -> dict[str, Any]:
        assert_correction_eligible(eligibility_state, expected_present_ids)
        if not expected_present_ids or attachment_id != expected_present_ids[0]:
            raise CleanupError("REFUSED: delete adapter target is not the next fixed eligible record.")
        parent_by_id = {
            row["id"]: row
            for row in eligibility_state["library"]["target_parent_states"]
        }
        before = self.read_target(
            attachment_id, library_parent_state=parent_by_id[attachment_id]
        )
        expected_before = eligibility_state["attachments"][0]
        if before != expected_before:
            raise CleanupError("REFUSED: fixed attachment changed after eligibility proof.")
        controls = [control for control in self._page.query_selector_all(
            "#delete-action > a.submitdelete.deletion"
        ) if "delete permanently" == " ".join(
            str(control.inner_text() or "").casefold().split()
        )]
        if len(controls) != 1 or not self._delete_control_exact(controls[0], attachment_id):
            raise CleanupError("REFUSED: exact fixed permanent-delete control drifted.")
        dialogs: list[str] = []

        def handle_dialog(dialog: Any) -> None:
            dialog_type = str(getattr(dialog, "type", ""))
            message = " ".join(str(getattr(dialog, "message", "") or "").casefold().split())
            if dialog_type == "confirm" and "permanently delete" in message:
                dialogs.append("confirm")
                dialog.accept()
            else:
                dialogs.append("unexpected")
                dialog.dismiss()

        self._page.once("dialog", handle_dialog)
        try:
            on_write_attempt()
            with self._page.expect_navigation(
                    wait_until="domcontentloaded", timeout=media_base.UPLOAD_TIMEOUT_MS):
                controls[0].click(
                    timeout=media_base.ACTION_TIMEOUT_MS, no_wait_after=True
                )
        finally:
            try:
                self._page.remove_listener("dialog", handle_dialog)
            except Exception:
                pass
        landed = urlsplit(str(self._page.url or ""))
        pairs = parse_qsl(landed.query, keep_blank_values=True)
        if (landed.scheme != "https" or landed.netloc != "frpdepots.com"
                or landed.path != "/wp-admin/upload.php"
                or pairs != [("deleted", "1")]
                or dialogs not in ([], ["confirm"])):
            raise IndeterminateError("WordPress permanent-delete result route was not exact.")
        return {**before, "wordpress_deleted_marker": 1, "dialog": dialogs[0] if dialogs else None}


@contextlib.contextmanager
def admin_session(purpose: str) -> Iterator[CleanupAdmin]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    RUNTIME_TEMP.mkdir(parents=True, exist_ok=True)
    stable = str(RUNTIME_TEMP.resolve())
    for name in ("TMP", "TEMP", "TMPDIR"):
        if not os.environ.get(name) or not Path(os.environ[name]).is_dir():
            os.environ[name] = stable
    with ui_browser_lock("wordpress", purpose=purpose), sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=15_000)
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            raise CleanupError(
                "REFUSED: authenticated WordPress browser is unavailable. Run "
                "CONNECT_DADO_WORDPRESS_UI.bat and keep it open."
            ) from exc
        if not browser.contexts or not browser.contexts[0].pages:
            raise CleanupError("REFUSED: authenticated WordPress browser has no open page.")
        admin = CleanupAdmin(browser.contexts[0].pages[0])
        try:
            yield admin
        finally:
            admin.leave_on_media_list()


def collect_before(admin: CleanupAdmin, vault: dict[str, Any]) -> dict[str, Any]:
    guard = guard_projection(
        admin.atomic_snapshot("open_manway"), expected_failure_ids=TARGET_IDS
    )
    library_raw = admin.enumerate_library()
    library = library_projection(library_raw)
    parent_by_id = {row["id"]: row for row in library["target_parent_states"]}
    identities = [
        admin.read_target(
            attachment_id, library_parent_state=parent_by_id[attachment_id]
        )
        for attachment_id in TARGET_IDS
    ]
    products = product_projection(vault)
    public = public_evidence(identities)
    state = {
        "guard": guard,
        "library": library,
        "attachments": identities,
        "products": products,
        "public_files": public,
    }
    return assert_correction_eligible(state, TARGET_IDS)


def expected_after(before: dict[str, Any]) -> dict[str, Any]:
    registered_urls = sorted({
        url for attachment in before["attachments"]
        for url in attachment.get("registered_urls", [])
    })
    return {
        "attachment_ids_absent": list(TARGET_IDS),
        "library_total": int(before["library"]["total"]) - len(TARGET_IDS),
        "registered_original_and_derivative_urls_not_found": registered_urls,
        "guard_complete": True,
        "guard_failures": [],
        "product_galleries_unchanged": True,
        "emails": 0,
    }


def operation_sha(source_evidence: dict[str, Any], _before: dict[str, Any]) -> str:
    # Stable across all staged observations and partial outcomes. Mutable live
    # evidence must never create a second operation identity after any attempt.
    return digest_for({
        "schema_version": OPERATION_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "action": ACTION,
        "targets": list(TARGETS),
        "source_operation_sha256": SOURCE_OPERATION_SHA256,
        "source_plan_sha256": SOURCE_PLAN_SHA256,
        "source_result_sha256": source_evidence["source_result_sha256"],
        "predecessor_operation_sha256": source_evidence["predecessor_operation_sha256"],
        "predecessor_plan_sha256": source_evidence["predecessor_plan_sha256"],
        "predecessor_event_sha256": source_evidence["predecessor_event_sha256"],
        "correction_reason": source_evidence["correction_reason"],
    })


def plan_path(created: datetime, plan_sha: str) -> Path:
    return (PLAN_DIR / f"{created.strftime('%Y%m%dT%H%M%SZ')}_four_orphan_correction_{plan_sha[:16]}.json").resolve()


def stage_registry_path(plan_sha: str) -> Path:
    return REGISTRY_DIR / f"{plan_sha}.json"


def attempt_path(operation: str) -> Path:
    return ATTEMPT_DIR / f"{operation}.json"


def result_path(operation: str) -> Path:
    return RESULT_DIR / f"{operation}.json"


def assert_operation_not_attempted(operation: str) -> None:
    if attempt_path(operation).exists() or result_path(operation).exists():
        raise CleanupError("REFUSED: stable operation is permanently replay-locked.")
    directory = EVENT_DIR / operation
    try:
        events = sorted(path.name for path in directory.iterdir()) if directory.exists() else []
    except OSError as exc:
        raise CleanupError("REFUSED: stable operation event state is unreadable.") from exc
    if events:
        raise CleanupError(
            "REFUSED: stable operation has permanent per-attachment attempt evidence; "
            "no retry or restaging is allowed."
        )


def stage_plan(source_evidence: dict[str, Any], before: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    assert_correction_eligible(before, TARGET_IDS)
    created = utc_now()
    operation = operation_sha(source_evidence, before)
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "origin": ORIGIN,
        "action": ACTION,
        "created_utc": created.isoformat(),
        "expires_utc": (created + PLAN_LIFETIME).isoformat(),
        "nonce": secrets.token_hex(16),
        "operation_sha256": operation,
        "targets": list(TARGETS),
        "source_evidence": source_evidence,
        "before": before,
        "after_expected": expected_after(before),
        "writes_if_committed": [
            "four independent permanent WordPress orphan-record deletions, in fixed ID order 5521, 5523, 5525, 5527"
        ],
        "risk": (
            "SEPARATE CORRECTION; NOT A REPLAY OF PREDECESSOR OPERATION 7045aee2... . "
            "NOT ATOMIC AND NOT REVERSIBLE. Each attachment-record deletion is one "
            "independent permanent WordPress action. If deletion N fails or verification "
            "becomes uncertain, earlier deletions remain, later deletions are not attempted, "
            "and this correction is permanently INDETERMINATE_NO_RETRY. There is no "
            "rollback, restore, retry, cleanup or re-upload route. Exact current identity is "
            "proved from canonical attachment-edit DOM fields rather than the failed "
            "wp.media model route. The browser lane excludes Dado's other lane but cannot "
            "eliminate a human/plugin/cron race; fresh complete reference and guard proofs "
            "run before locking and after every deletion."
        ),
        "forbidden": [
            "any attachment except 5521,5523,5525,5527", "upload", "edit", "rename",
            "replace", "detach", "attach", "product write", "plugin write", "setting",
            "content", "user", "order", "customer", "payment", "email", "retry",
            "rollback", "restore", "generic browser", "Zoho", "Drive",
        ],
    }
    plan = {**core, "sha256": digest_for(core)}
    path = plan_path(created, plan["sha256"])
    exclusive_json(path, plan)
    raw_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    registry_core = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "plan_sha256": plan["sha256"],
        "plan_file_sha256": raw_sha,
        "plan_path": str(path),
        "operation_sha256": operation,
        "nonce": plan["nonce"],
        "created_utc": plan["created_utc"],
        "expires_utc": plan["expires_utc"],
    }
    exclusive_json(stage_registry_path(plan["sha256"]), {
        **registry_core, "hmac_sha256": registry_mac(registry_core),
    })
    append_receipt("wordpress_orphan_media_correction_plan_staged", str(path))
    return path, plan


def fixed_plan_path(raw: str) -> Path:
    path = Path(raw).resolve()
    if (path.parent != PLAN_DIR.resolve() or path.suffix.lower() != ".json"
            or not path.is_file() or is_reparse(path)
            or getattr(path.stat(), "st_nlink", 1) != 1):
        raise CleanupError("REFUSED: plan is not a regular canonical file in the fixed plan folder.")
    return path


def load_plan(raw_path: str) -> tuple[Path, dict[str, Any]]:
    path = fixed_plan_path(raw_path)
    raw = path.read_bytes()
    try:
        plan = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CleanupError("REFUSED: plan is unreadable.") from exc
    if not isinstance(plan, dict) or set(plan) != PLAN_KEYS:
        raise CleanupError("REFUSED: plan schema is not exact.")
    core = dict(plan)
    saved = str(core.pop("sha256", ""))
    if not secrets.compare_digest(saved, digest_for(core)):
        raise CleanupError("REFUSED: plan hash failed.")
    if saved in SUPERSEDED_PLAN_SHA256S:
        raise CleanupError("REFUSED: this exact plan hash is permanently superseded.")
    if (plan["schema_version"] != SCHEMA_VERSION or plan["tool"] != TOOL_NAME
            or plan["tool_version"] != TOOL_VERSION or plan["origin"] != ORIGIN
            or plan["action"] != ACTION or plan["targets"] != list(TARGETS)):
        raise CleanupError("REFUSED: plan identity is invalid.")
    created = datetime.fromisoformat(str(plan["created_utc"]))
    expires = datetime.fromisoformat(str(plan["expires_utc"]))
    if (created.tzinfo is None or expires.tzinfo is None
            or created.utcoffset() != timedelta(0) or expires.utcoffset() != timedelta(0)
            or created > utc_now() + timedelta(minutes=1)
            or expires - created != PLAN_LIFETIME or utc_now() > expires):
        raise CleanupError("REFUSED: plan is expired or has an invalid lifetime.")
    if path.name != plan_path(created.astimezone(timezone.utc), saved).name:
        raise CleanupError("REFUSED: plan filename is not canonical.")
    source = validate_fixed_contract()
    if plan["source_evidence"] != source:
        raise CleanupError("REFUSED: fixed source evidence changed.")
    operation = operation_sha(source, plan["before"])
    if (plan["operation_sha256"] != operation
            or plan["after_expected"] != expected_after(plan["before"])):
        raise CleanupError("REFUSED: stable operation identity is invalid.")
    try:
        registry = json.loads(stage_registry_path(saved).read_text(encoding="ascii"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CleanupError("REFUSED: authenticated stage registry is missing.") from exc
    registry_core = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "plan_sha256": saved,
        "plan_file_sha256": hashlib.sha256(raw).hexdigest(),
        "plan_path": str(path),
        "operation_sha256": operation,
        "nonce": plan["nonce"],
        "created_utc": plan["created_utc"],
        "expires_utc": plan["expires_utc"],
    }
    if (not isinstance(registry, dict) or set(registry) != set(registry_core) | {"hmac_sha256"}
            or {key: registry.get(key) for key in registry_core} != registry_core
            or not isinstance(registry.get("hmac_sha256"), str)
            or not hmac.compare_digest(registry["hmac_sha256"], registry_mac(registry_core))):
        raise CleanupError("REFUSED: authenticated stage registry failed.")
    plan["sha256"] = saved
    return path, plan


def assert_execution_window(plan: dict[str, Any]) -> None:
    expires = datetime.fromisoformat(str(plan.get("expires_utc", "")))
    if expires.tzinfo is None or utc_now() + COMMIT_EXPIRY_MARGIN >= expires:
        raise CleanupError("REFUSED: plan has insufficient authorization time remaining; stage a fresh plan.")


def event_path(operation: str, position: int, attachment_id: int, kind: str) -> Path:
    if (not re.fullmatch(r"[0-9a-f]{64}", operation) or position not in (1, 2, 3, 4)
            or attachment_id != TARGET_IDS[position - 1]
            or kind not in {"attempted", "verified"}):
        raise CleanupError("REFUSED: fixed per-attachment event identity is invalid.")
    return EVENT_DIR / operation / f"{position:02d}-{attachment_id}-{kind}.json"


def verify_after_step(admin: CleanupAdmin, vault: dict[str, Any], plan: dict[str, Any],
                      deleted_ids: tuple[int, ...]) -> dict[str, Any]:
    if deleted_ids != TARGET_IDS[:len(deleted_ids)] or not 1 <= len(deleted_ids) <= 4:
        raise CleanupError("REFUSED: fixed deletion prefix is invalid.")
    remaining = TARGET_IDS[len(deleted_ids):]
    library = library_projection(
        admin.enumerate_library(), expected_present_ids=remaining
    )
    expected_rows = [
        row for row in plan["before"]["library"]["rows"]
        if row["id"] not in deleted_ids
    ]
    if (library["rows"] != expected_rows
            or library["total"] != plan["before"]["library"]["total"] - len(deleted_ids)
            or library["survivor_rows_sha256"]
            != plan["before"]["library"]["survivor_rows_sha256"]):
        raise IndeterminateError("Media Library changed beyond the fixed deletion prefix.")
    guard = guard_projection(
        admin.atomic_snapshot("open_manway"), expected_failure_ids=remaining
    )
    baseline_guard = plan["before"]["guard"]
    if (guard["attachment_total"] != baseline_guard["attachment_total"] - len(deleted_ids)
            or guard["hashed_total"] != baseline_guard["hashed_total"]
            or guard["snapshot_sha256"] != baseline_guard["snapshot_sha256"]):
        raise IndeterminateError("Server guard changed beyond the fixed deletion prefix.")
    products = product_projection(vault)
    if products != plan["before"]["products"]:
        raise IndeterminateError("A WooCommerce product or variation reference changed during cleanup.")
    before_by_id = {row["attachment_id"]: row for row in plan["before"]["attachments"]}
    parent_by_id = {row["id"]: row for row in library["target_parent_states"]}
    remaining_rows = [
        admin.read_target(
            attachment_id, library_parent_state=parent_by_id[attachment_id]
        )
        for attachment_id in remaining
    ]
    if remaining_rows != [before_by_id[attachment_id] for attachment_id in remaining]:
        raise IndeterminateError("An untouched fixed attachment changed during cleanup.")
    all_fixed_urls = [row["source_url"] for row in TARGETS]
    public_absent = public_absence_evidence(all_fixed_urls)
    record_removal_proof = [
        {
            "attachment_id": attachment_id,
            "media_library_absent": all(
                row["id"] != attachment_id for row in library["rows"]
            ),
            "guard_failure_absent": all(
                row["attachment_id"] != attachment_id for row in guard["failures"]
            ),
        }
        for attachment_id in deleted_ids
    ]
    if any(row["media_library_absent"] is not True
           or row["guard_failure_absent"] is not True
           for row in record_removal_proof):
        raise IndeterminateError("A deleted record did not disappear from both complete proofs.")
    eligibility_state = {
        "guard": guard,
        "library": library,
        "attachments": remaining_rows,
        "products": products,
        "public_files": public_absent,
    }
    assert_correction_eligible(eligibility_state, remaining)
    return {
        "deleted_ids": list(deleted_ids),
        "remaining_ids": list(remaining),
        "library": library,
        "guard": guard,
        "products": products,
        "record_removal_proof": record_removal_proof,
        "public_absent": public_absent,
        "eligibility_state": eligibility_state,
    }


def command_stage(_: argparse.Namespace) -> None:
    source = validate_fixed_contract()
    assert_operation_not_attempted(operation_sha(source, {}))
    with admin_session("WordPress read-only stage: four fixed orphan attachments") as admin:
        vault = wc.load_vault()
        before = collect_before(admin, vault)
        bookend = collect_before(admin, vault)
        if bookend != before:
            raise CleanupError("REFUSED: staged read-only evidence was not stable across two complete passes.")
    path, plan = stage_plan(source, before)
    print_json({
        "status": "STAGED_READ_ONLY",
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "operation_sha256": plan["operation_sha256"],
        "targets": plan["targets"],
        "before": before,
        "after_expected": plan["after_expected"],
        "risk": plan["risk"],
        "website_writes": 0,
        "approval_required": APPROVAL_WORD,
    })


def command_commit(args: argparse.Namespace) -> None:
    require_approval(args.approval)
    path, plan = load_plan(args.plan)
    assert_correction_eligible(plan["before"], TARGET_IDS)
    operation = plan["operation_sha256"]
    assert_operation_not_attempted(operation)
    locked = False
    write_attempts = 0
    deleted: list[dict[str, Any]] = []
    result: dict[str, Any]
    try:
        with admin_session("WordPress commit: delete four fixed orphan attachments") as admin:
            vault = wc.load_vault()
            fresh = collect_before(admin, vault)
            bookend = collect_before(admin, vault)
            if fresh != bookend or fresh != plan["before"]:
                raise CleanupError("REFUSED: live WordPress/WooCommerce state drifted after staging; stage a fresh plan.")
            assert_execution_window(plan)
            exclusive_json(attempt_path(operation), {
                "schema": 1,
                "tool": TOOL_NAME,
                "operation_sha256": operation,
                "plan_sha256": plan["sha256"],
                "locked_utc": utc_now().isoformat(),
                "status": "attempt_started_no_retry",
            })
            locked = True

            eligibility_state = fresh
            for position, attachment_id in enumerate(TARGET_IDS, 1):
                expected_present_ids = TARGET_IDS[position - 1:]
                def note_write(*, _position: int = position,
                               _attachment_id: int = attachment_id) -> None:
                    nonlocal write_attempts
                    exclusive_json(event_path(
                        operation, _position, _attachment_id, "attempted"
                    ), {
                        "schema": 1,
                        "tool": TOOL_NAME,
                        "operation_sha256": operation,
                        "plan_sha256": plan["sha256"],
                        "position": _position,
                        "attachment_id": _attachment_id,
                        "status": "write_attempted_no_retry",
                        "ts": utc_now().isoformat(),
                    })
                    write_attempts += 1

                landed = admin.delete_one(
                    attachment_id,
                    note_write,
                    eligibility_state=eligibility_state,
                    expected_present_ids=expected_present_ids,
                )
                step = verify_after_step(
                    admin, vault, plan, TARGET_IDS[:position]
                )
                eligibility_state = step["eligibility_state"]
                step_summary = {
                    "deleted_ids": step["deleted_ids"],
                    "remaining_ids": step["remaining_ids"],
                    "library_total": step["library"]["total"],
                    "library_rows_sha256": step["library"]["rows_sha256"],
                    "guard": step["guard"],
                    "products": step["products"],
                    "record_removal_proof": step["record_removal_proof"],
                    "public_absent": step["public_absent"],
                    "eligibility_state_sha256": digest_for(step["eligibility_state"]),
                }
                exclusive_json(event_path(
                    operation, position, attachment_id, "verified"
                ), {
                    "schema": 1,
                    "tool": TOOL_NAME,
                    "operation_sha256": operation,
                    "plan_sha256": plan["sha256"],
                    "position": position,
                    "attachment_id": attachment_id,
                    "status": "deletion_verified",
                    "ts": utc_now().isoformat(),
                    "evidence": step_summary,
                })
                deleted.append({**landed, "step_verification": step_summary})
            final_step = deleted[-1]["step_verification"]
            result = {
                "schema": 1,
                "tool": TOOL_NAME,
                "operation_sha256": operation,
                "plan_sha256": plan["sha256"],
                "status": "COMMITTED_AND_VERIFIED",
                "completed_utc": utc_now().isoformat(),
                "deleted": deleted,
                "deleted_attachment_ids": list(TARGET_IDS),
                "writes_attempted": write_attempts,
                "before_library_rows_sha256": plan["before"]["library"]["rows_sha256"],
                "after_library_rows_sha256": final_step["library_rows_sha256"],
                "after_library_total": final_step["library_total"],
                "final_guard": final_step["guard"],
                "products": final_step["products"],
                "registered_public_absent": final_step["public_absent"],
                "emails": 0,
                "replay_locked": True,
            }
            exclusive_json(result_path(operation), result)
    except Exception as exc:
        if locked:
            result = {
                "schema": 1,
                "tool": TOOL_NAME,
                "operation_sha256": operation,
                "plan_sha256": plan["sha256"],
                "status": "INDETERMINATE_NO_RETRY",
                "failed_utc": utc_now().isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "write_attempts": write_attempts,
                "deleted_verified_before_failure": deleted,
                "no_retry": True,
                "rollback": False,
                "emails": 0,
            }
            try:
                exclusive_json(result_path(operation), result)
            except CleanupError:
                pass
        raise
    append_receipt("wordpress_four_orphan_media_correction_verified", str(result_path(operation)))
    print_json(result)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    stage_parser = sub.add_parser("stage")
    stage_parser.set_defaults(func=command_stage)
    commit_parser = sub.add_parser("commit")
    commit_parser.add_argument("--plan", required=True)
    commit_parser.add_argument("--approval", required=True)
    commit_parser.set_defaults(func=command_commit)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except (CleanupError, family_media.FamilyMediaError, media_base.MediaUploadError,
            wc.WooError, UiLaneBusy, UiLaneLockError) as exc:
        print("ERROR: " + wc.scrub(str(exc)), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {wc.scrub(str(exc))}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
