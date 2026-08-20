#!/usr/bin/env python
"""Fixed cleanup tool for four known orphan WordPress media records.

Commissioned by Rachad Homsi on 2026-08-20 after the permanently locked
open-manway gallery operation uploaded four files but never assigned a product
gallery. This tool can do exactly one thing: permanently delete attachment IDs
5521, 5523, 5525 and 5527 after a read-only immutable 24-hour plan and Rachad's
later exact APPROVED.

The four deletes are independent WordPress actions. They are NOT atomic and
there is NO rollback, restore, retry, upload, edit, attachment replacement,
product write, plugin write, generic browser, order/customer/payment or mail
route. Any failure after the permanent attempt lock leaves the plan
INDETERMINATE_NO_RETRY; earlier deletions remain live and later ones are not
attempted.
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
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(r"C:\FRPDepot")
THIS_DIR = Path(__file__).resolve().parent
sys.path.append(str(THIS_DIR))
sys.path.append(str(THIS_DIR.parent / "common"))
import woocommerce_common as wc  # noqa: E402
import wordpress_packing_ring_media_tool as media_base  # noqa: E402
import wordpress_product_family_media_tool as family_media  # noqa: E402
from ui_lane_lock import UiLaneBusy, UiLaneLockError, ui_browser_lock  # noqa: E402

TOOL_NAME = "FRP Depot Fixed Orphan Media Cleanup Tool"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = 1
ACTION = "delete_four_fixed_orphan_attachments"
APPROVAL_WORD = "APPROVED"
ORIGIN = "https://frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
PLAN_LIFETIME = timedelta(hours=24)
COMMIT_EXPIRY_MARGIN = timedelta(minutes=45)
PLAN_DIR = ROOT / "Dado" / "20_Working" / "wordpress_orphan_media_cleanup_plans"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
LOCAL_STATE = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "FRPDepot-WordPress" / "orphan-media-cleanup"
REGISTRY_KEY = LOCAL_STATE / "stage-registry.key"
REGISTRY_DIR = LOCAL_STATE / "stages"
ATTEMPT_DIR = LOCAL_STATE / "attempts"
RESULT_DIR = LOCAL_STATE / "results"
EVENT_DIR = LOCAL_STATE / "events"
RUNTIME_TEMP = ROOT / "Dado" / "Temp" / "playwright-runtime"
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
    return {
        "result_path": str(SOURCE_RESULT.resolve()),
        "result_file_sha256": SOURCE_RESULT_SHA256,
        "operation_sha256": SOURCE_OPERATION_SHA256,
        "plan_sha256": SOURCE_PLAN_SHA256,
        "status": "INDETERMINATE_NO_RETRY",
        "product_may_have_changed": False,
        "gallery_payload": None,
        "delete_performed": False,
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
    by_url: dict[str, tuple[str | None, int | None]] = {}
    for attachment in attachments:
        spec = target_spec(attachment["attachment_id"])
        for url in attachment.get("registered_urls") or []:
            if not isinstance(url, str):
                raise CleanupError("REFUSED: registered public media URL is invalid.")
            expected = (spec["sha256"], spec["bytes"]) if url == spec["source_url"] else (None, None)
            if url in by_url and by_url[url] != expected:
                raise CleanupError("REFUSED: registered public media URL provenance conflicts.")
            by_url[url] = expected
    rows = [
        public_url_state(url, require_present=expected_sha is not None,
                         expected_sha256=expected_sha, expected_bytes=expected_bytes)
        for url, (expected_sha, expected_bytes) in sorted(by_url.items())
    ]
    if not rows or sum(row["state"] == "present" for row in rows) < len(TARGETS):
        raise CleanupError("REFUSED: public source provenance is incomplete.")
    return rows


def public_absence_evidence(urls: list[str]) -> list[dict[str, Any]]:
    rows = [public_url_state(url) for url in sorted(set(urls))]
    if any(row["state"] != "not_found" for row in rows):
        raise IndeterminateError("One or more registered public media files remain available.")
    return rows


def library_projection(snapshot: dict[str, Any], *,
                       expected_present_ids: tuple[int, ...] = TARGET_IDS) -> dict[str, Any]:
    rows = snapshot.get("rows") if isinstance(snapshot, dict) else None
    if (snapshot.get("complete") is not True or type(snapshot.get("total")) is not int
            or not isinstance(rows, list) or len(rows) != snapshot["total"]):
        raise CleanupError("REFUSED: complete Media Library identity enumeration failed.")
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
    return {
        "total": snapshot["total"],
        "rows": projected,
        "rows_sha256": digest_for(projected),
        "survivor_rows_sha256": digest_for(survivors),
        "target_rows": fixed,
    }


def strict_get_all(endpoint: str, params: dict[str, Any], vault: dict[str, Any],
                   *, max_items: int = 20000) -> list[dict[str, Any]]:
    """Closed, total-proven WooCommerce GET paginator; never drops malformed rows."""
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


def product_projection(vault: dict[str, Any]) -> dict[str, Any]:
    fields = "id,status,images,variations,description,short_description,downloads,meta_data"
    products = strict_get_all("/products", {"_fields": fields}, vault,
                              max_items=media_base.MAX_LIBRARY_ROWS)
    projected: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    variations_checked = 0
    for product in products:
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
                variation_projection.append({"variation_id": variation["id"], "image_id": image_id})
        variation_projection.sort(key=lambda row: row["variation_id"])
        projected.append({
            "product_id": product_id, "status": str(product.get("status") or ""),
            "image_ids": image_ids, "variations": variation_projection,
        })
    projected.sort(key=lambda row: row["product_id"])
    if references:
        raise CleanupError("REFUSED: a fixed attachment ID, filename or URL is referenced by a product or variation.")
    return {
        "products_checked": len(projected),
        "variations_checked": variations_checked,
        "product_and_variation_galleries_sha256": digest_for(projected),
        "target_references": [],
        "strict_totals_proven": True,
    }


def guard_projection(proof: dict[str, Any], *,
                     expected_failure_ids: tuple[int, ...]) -> dict[str, Any]:
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
    if (proof.get("schema") != family_media.GUARD_PROOF_SCHEMA
            or proof.get("plugin_version") != family_media.GUARD_PLUGIN_VERSION
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
    for field in ("name_conflicts", "hash_conflicts", "fixed_matches"):
        rows = proof.get(field)
        if rows != []:
            count = len(rows) if isinstance(rows, list) else -1
            raise CleanupError(f"REFUSED: server-side guard {field} is non-empty ({count}).")
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
        "complete": complete_wanted,
        "snapshot_sha256": str(proof.get("snapshot_sha256") or ""),
    }


class CleanupAdmin:
    """Narrow actor: read evidence plus one exact attachment-delete click only."""

    def __init__(self, page: Any):
        self._page = page
        self._reader = family_media.ProductFamilyAdmin(page, frozenset())

    def atomic_snapshot(self, family: str) -> dict[str, Any]:
        if family != "open_manway":
            raise CleanupError("REFUSED: only the fixed open_manway guard snapshot is reachable.")
        return self._reader.atomic_snapshot(family)

    def enumerate_library(self) -> dict[str, Any]:
        return self._reader.enumerate_library()

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
        values: dict[str, str] = {}
        for key, selector in (
            ("post_id", "#post_ID"),
            ("post_type", "#post_type"),
            ("original_post_status", "#original_post_status"),
        ):
            elements = self._page.query_selector_all(selector)
            if len(elements) != 1:
                raise CleanupError("REFUSED: exact attachment edit identity fields are unavailable.")
            values[key] = str(elements[0].input_value() or "")
        if (values["post_id"] != str(attachment_id)
                or values["post_type"] != "attachment"
                or values["original_post_status"] != "inherit"):
            raise CleanupError("REFUSED: fixed attachment edit identity changed.")
        return values

    def _media_model_projection(self, attachment_id: int) -> dict[str, Any]:
        value = self._page.evaluate("""async (expectedId) => {
            if (!window.wp || !wp.media || typeof wp.media.attachment !== 'function') {
                return {error: 'media_model_unavailable'};
            }
            try {
                const model = wp.media.attachment(expectedId);
                await model.fetch();
                const a = model.toJSON();
                const sizes = [];
                const rawSizes = a.sizes && typeof a.sizes === 'object' ? a.sizes : {};
                for (const key of Object.keys(rawSizes).sort()) {
                    const row = rawSizes[key] || {};
                    sizes.push({
                        name: String(key),
                        url: typeof row.url === 'string' ? row.url : '',
                        width: Number.isInteger(row.width) ? row.width : null,
                        height: Number.isInteger(row.height) ? row.height : null
                    });
                }
                return {
                    id: a.id,
                    filename: typeof a.filename === 'string' ? a.filename : '',
                    url: typeof a.url === 'string' ? a.url : '',
                    mime: typeof a.mime === 'string' ? a.mime : '',
                    type: typeof a.type === 'string' ? a.type : '',
                    subtype: typeof a.subtype === 'string' ? a.subtype : '',
                    uploaded_to: Number.isInteger(a.uploadedTo) ? a.uploadedTo : null,
                    sizes: sizes
                };
            } catch (error) {
                return {error: 'media_model_fetch_failed'};
            }
        }""", attachment_id)
        if not isinstance(value, dict) or set(value) != {
            "id", "filename", "url", "mime", "type", "subtype", "uploaded_to", "sizes"
        }:
            raise CleanupError("REFUSED: exact authenticated attachment metadata is unavailable.")
        spec = target_spec(attachment_id)
        if (value["id"] != attachment_id or value["filename"] != spec["filename"]
                or value["url"] != spec["source_url"]
                or value["mime"] not in ("image/png", "")
                or value["type"] != "image" or value["subtype"] != "png"
                or value["uploaded_to"] != 0 or not isinstance(value["sizes"], list)):
            raise CleanupError("REFUSED: fixed attachment metadata is not exact and unattached.")
        sizes: list[dict[str, Any]] = []
        for row in value["sizes"]:
            if (not isinstance(row, dict) or set(row) != {"name", "url", "width", "height"}
                    or not isinstance(row["name"], str) or not row["name"]
                    or not isinstance(row["url"], str) or not row["url"]
                    or type(row["width"]) is not int or row["width"] <= 0
                    or type(row["height"]) is not int or row["height"] <= 0):
                raise CleanupError("REFUSED: registered attachment derivative metadata is invalid.")
            media_base.assert_public_upload_url(row["url"], allowed_extensions=(".png",))
            sizes.append(dict(row))
        if len({row["name"] for row in sizes}) != len(sizes):
            raise CleanupError("REFUSED: registered attachment derivative names repeat.")
        return {**value, "sizes": sizes}

    def read_target(self, attachment_id: int) -> dict[str, Any]:
        spec = target_spec(attachment_id)
        identity = self.read_attachment(attachment_id, expected_basename=spec["filename"])
        if identity.get("source_url") != spec["source_url"]:
            raise CleanupError("REFUSED: fixed attachment public URL changed.")
        edit_identity = self._edit_identity(attachment_id)
        model = self._media_model_projection(attachment_id)
        controls = self._page.query_selector_all("#delete-action > a.submitdelete.deletion")
        controls = [control for control in controls
                    if "delete permanently" == " ".join(
                        str(control.inner_text() or "").casefold().split()
                    )]
        if len(controls) != 1 or not self._delete_control_exact(controls[0], attachment_id):
            raise CleanupError("REFUSED: exact fixed permanent-delete control is unavailable.")
        registered_urls = sorted({spec["source_url"], *(row["url"] for row in model["sizes"])})
        return {
            "attachment_id": attachment_id,
            "filename": identity["filename"],
            "source_url": identity["source_url"],
            "edit_identity": edit_identity,
            "uploaded_to": 0,
            "registered_derivatives": model["sizes"],
            "registered_urls": registered_urls,
            "delete_control_exact": True,
        }

    def media_model_missing(self, attachment_id: int) -> dict[str, Any]:
        target_spec(attachment_id)
        self._reader._goto(media_base.MEDIA_LIBRARY_URL)
        result = self._page.evaluate("""async (expectedId) => {
            if (!window.wp || typeof wp.apiFetch !== 'function') {
                return {missing: false, code: 'api_fetch_unavailable', status: null};
            }
            try {
                await wp.apiFetch({path: '/wp/v2/media/' + String(expectedId) + '?context=edit'});
                return {missing: false, code: 'record_present', status: 200};
            } catch (error) {
                const status = error && error.data && Number.isInteger(error.data.status)
                    ? error.data.status : null;
                return {missing: status === 404, code: String(error && error.code || ''), status: status};
            }
        }""", attachment_id)
        if (not isinstance(result, dict) or set(result) != {"missing", "code", "status"}
                or result["missing"] is not True or result["status"] != 404
                or result["code"] != "rest_post_invalid_id"):
            raise IndeterminateError("Authenticated media record did not prove absent.")
        return result

    def delete_one(self, attachment_id: int, on_write_attempt: Any) -> dict[str, Any]:
        before = self.read_target(attachment_id)
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
        on_write_attempt()
        try:
            controls[0].click(timeout=media_base.ACTION_TIMEOUT_MS)
            self._page.wait_for_load_state("domcontentloaded", timeout=media_base.UPLOAD_TIMEOUT_MS)
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
    identities = [admin.read_target(attachment_id) for attachment_id in TARGET_IDS]
    products = product_projection(vault)
    public = public_evidence(identities)
    return {
        "guard": guard,
        "library": library,
        "attachments": identities,
        "products": products,
        "public_files": public,
    }


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
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "action": ACTION,
        "targets": list(TARGETS),
        "source_operation_sha256": SOURCE_OPERATION_SHA256,
        "source_plan_sha256": SOURCE_PLAN_SHA256,
        "source_result_sha256": source_evidence["result_file_sha256"],
    })


def plan_path(created: datetime, plan_sha: str) -> Path:
    return (PLAN_DIR / f"{created.strftime('%Y%m%dT%H%M%SZ')}_four_orphans_{plan_sha[:16]}.json").resolve()


def stage_registry_path(plan_sha: str) -> Path:
    return REGISTRY_DIR / f"{plan_sha}.json"


def attempt_path(operation: str) -> Path:
    return ATTEMPT_DIR / f"{operation}.json"


def result_path(operation: str) -> Path:
    return RESULT_DIR / f"{operation}.json"


def stage_plan(source_evidence: dict[str, Any], before: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
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
            "four independent permanent WordPress attachment deletions, in fixed ID order 5521, 5523, 5525, 5527"
        ],
        "risk": (
            "NOT ATOMIC AND NOT REVERSIBLE. Each attachment deletion is one independent "
            "permanent WordPress action. If deletion N fails or verification becomes "
            "uncertain, earlier deletions remain, later deletions are not attempted, and "
            "the plan is permanently INDETERMINATE_NO_RETRY. There is no rollback, "
            "restore, retry, cleanup or re-upload route. WordPress may also delete every "
            "registered thumbnail/derived file inventoried in this plan. The browser lane "
            "excludes Dado's other lane but cannot eliminate a human/plugin/cron race; "
            "fresh reference and guard proofs run immediately before every click."
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
    append_receipt("wordpress_orphan_media_cleanup_plan_staged", str(path))
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
    remaining_rows = [admin.read_target(attachment_id) for attachment_id in remaining]
    if remaining_rows != [before_by_id[attachment_id] for attachment_id in remaining]:
        raise IndeterminateError("An untouched fixed attachment changed during cleanup.")
    missing_records = [admin.media_model_missing(attachment_id) for attachment_id in deleted_ids]
    deleted_urls = [
        url for attachment_id in deleted_ids
        for url in before_by_id[attachment_id]["registered_urls"]
    ]
    public_absent = public_absence_evidence(deleted_urls)
    return {
        "deleted_ids": list(deleted_ids),
        "remaining_ids": list(remaining),
        "library": library,
        "guard": guard,
        "products": products,
        "authenticated_missing": missing_records,
        "public_absent": public_absent,
    }


def command_stage(_: argparse.Namespace) -> None:
    source = validate_fixed_contract()
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
    operation = plan["operation_sha256"]
    if attempt_path(operation).exists() or result_path(operation).exists():
        raise CleanupError("REFUSED: stable operation is permanently replay-locked.")
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

            for position, attachment_id in enumerate(TARGET_IDS, 1):
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

                landed = admin.delete_one(attachment_id, note_write)
                step = verify_after_step(
                    admin, vault, plan, TARGET_IDS[:position]
                )
                step_summary = {
                    "deleted_ids": step["deleted_ids"],
                    "remaining_ids": step["remaining_ids"],
                    "library_total": step["library"]["total"],
                    "library_rows_sha256": step["library"]["rows_sha256"],
                    "guard": step["guard"],
                    "products": step["products"],
                    "authenticated_missing": step["authenticated_missing"],
                    "public_absent": step["public_absent"],
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
    append_receipt("wordpress_four_orphan_media_cleanup_verified", str(result_path(operation)))
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
