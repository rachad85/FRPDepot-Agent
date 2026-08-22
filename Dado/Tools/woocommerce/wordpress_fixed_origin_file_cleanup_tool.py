#!/usr/bin/env python
"""Fixed one-use cleanup of four exact unregistered WordPress origin files.

Commissioned separately by Rachad Homsi on 2026-08-21 after attachment records
5521, 5523, 5525 and 5527 disappeared while their four exact origin files still
served publicly. This named tool exposes one bounded ``diagnose-library`` read-only
command plus ``stage`` and ``commit``. Diagnose-library reports only complete-walk
counts and sanitized row/link shapes and creates no plan. Stage performs fixed
read-only WordPress/public reads and writes one authenticated immutable 24-hour
local plan. Commit requires Rachad's later byte-exact unpadded uppercase
``APPROVED``.

The commit is NOT ATOMIC. It installs only the pinned one-use plugin inactive,
activates it once, and the activation hook preflights all four paths, exact sizes,
SHA-256 values, fixed-ID absence and exact _wp_attached_file-record absence before
any unlink. The hook then unlinks positions 1 through 4 sequentially and schedules
its own deactivation. The tool verifies cache-busted public 404/410 responses and
record 404s, then deletes ONLY that exact inactive plugin after full success.

A later failure leaves every earlier effect in place: partial file deletion is
possible and the plugin may remain installed/inactive. There is NO rollback,
restore, cleanup-after-failure or retry. One immutable operation gets one attempt.
There is no arbitrary path, URL, ZIP, slug, plugin deployment, public/admin
mutation route, email, WooCommerce/Zoho/product/order/customer/payment write, or
generic browser capability.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Iterator
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit
import zipfile

ROOT = Path(r"C:\FRPDepot")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from Dado.Tools.common.ui_lane_lock import UiLaneBusy, ui_browser_lock
from Dado.Tools.woocommerce import wordpress_packing_ring_media_tool as media_base

TOOL_NAME = "wordpress_fixed_origin_file_cleanup_tool"
# 1.0.2/schema 2: when WordPress omits the usual filename node, the complete
# read-only Media Library reader may use one exact same-origin /wp-content/uploads/
# file link from the same canonically identified row. Ambiguous, foreign, queried,
# fragmented and malformed links remain unidentified. No write surface changed.
TOOL_VERSION = "1.0.2"
SCHEMA_VERSION = 2
OPERATION_SCHEMA_VERSION = 1
APPROVAL_WORD = "APPROVED"
ORIGIN = "https://frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
PLUGINS_URL = ORIGIN + "/wp-admin/plugins.php"
UPLOAD_URL = ORIGIN + "/wp-admin/plugin-install.php?tab=upload"
PLUGIN_NAME = "FRP Depot Fixed Four Origin File Cleanup"
PLUGIN_SLUG = "frpdepot-fixed-four-origin-file-cleanup"
PLUGIN_FILE = f"{PLUGIN_SLUG}/frpdepot-fixed-four-origin-file-cleanup.php"
PLUGIN_VERSION = "1.0.0"
ROW_SELECTOR = f'tr[data-plugin="{PLUGIN_FILE}"]:not(.plugin-update-tr)'
UPDATE_ROW_SELECTOR = f'tr.plugin-update-tr[data-plugin="{PLUGIN_FILE}"]'
ACTIVATE_SELECTOR = ".row-actions .activate a"
DEACTIVATE_SELECTOR = ".row-actions .deactivate a"
DELETE_SELECTOR = ".row-actions .delete a"
VERSION_PATTERN = re.compile(r"(?i)\bversion\s+([0-9][0-9A-Za-z.\-+_]*)")
UPLOAD_FORM_SELECTOR = "form.wp-upload-form"
NAV_TIMEOUT_MS = 45_000
ACTION_TIMEOUT_MS = 30_000
POST_WRITE_READ_ROUNDS = 3
POST_WRITE_READ_DELAY_MS = 1_000
PLAN_LIFETIME = timedelta(hours=24)
COMMIT_EXPIRY_MARGIN = timedelta(minutes=2)

PACKAGE_DIR = ROOT / "Dado" / "Tools" / "woocommerce" / "fixed_origin_file_cleanup"
ARTIFACT_PATH = PACKAGE_DIR / f"{PLUGIN_SLUG}-1.0.0.zip"
ARTIFACT_MANIFEST_PATH = PACKAGE_DIR / f"{PLUGIN_SLUG}-1.0.0.manifest.json"
ARTIFACT_SHA256 = "66bbbafd63557fb2a626d372c2269df1b07d2c8f79f216a81aa94f37964253ae"
ARTIFACT_BYTES = 3687
ARTIFACT_MANIFEST_SHA256 = "27bca756c0c16edd850176c2e1a5456687e71d80c0023886130c7088b068c00b"
ARTIFACT_MEMBERS = (
    f"{PLUGIN_SLUG}/frpdepot-fixed-four-origin-file-cleanup.php",
    f"{PLUGIN_SLUG}/readme.txt",
)
ARTIFACT_MEMBER_SHA256 = {
    f"{PLUGIN_SLUG}/frpdepot-fixed-four-origin-file-cleanup.php":
        "3fc318d11261f6f8234b6a9848893884b846746fd97d6a60146b096d4e8fbcf0",
    f"{PLUGIN_SLUG}/readme.txt":
        "34ae30c9cd0197f675c4e5abfd2a30ff9635593cedca2392fbb7a8c4f3431b1e",
}
ARTIFACT_MEMBER_BYTES = {
    f"{PLUGIN_SLUG}/frpdepot-fixed-four-origin-file-cleanup.php": 7442,
    f"{PLUGIN_SLUG}/readme.txt": 978,
}

TARGETS = (
    {"position": 1, "attachment_id": 5521,
     "upload_path": "wp-content/uploads/2026/08/01_manway_real_hero.png",
     "url": ORIGIN + "/wp-content/uploads/2026/08/01_manway_real_hero.png",
     "bytes": 261492,
     "sha256": "db886ee83d211d755ffc5e095b3546351f9b01478be73d1a71c5b299a1643be6"},
    {"position": 2, "attachment_id": 5523,
     "upload_path": "wp-content/uploads/2026/08/02_manway_real_alternate.png",
     "url": ORIGIN + "/wp-content/uploads/2026/08/02_manway_real_alternate.png",
     "bytes": 366491,
     "sha256": "07d1678e976152a5fdc8ccdc0396a43a92e0055125fffc587508b354c747484b"},
    {"position": 3, "attachment_id": 5525,
     "upload_path": "wp-content/uploads/2026/08/03_manway_real_laminate_detail.png",
     "url": ORIGIN + "/wp-content/uploads/2026/08/03_manway_real_laminate_detail.png",
     "bytes": 301011,
     "sha256": "572741ffd433acbc8b2bd36dbd9cb2afe02dbd8b6346978c38a7c0d4f8a352d9"},
    {"position": 4, "attachment_id": 5527,
     "upload_path": "wp-content/uploads/2026/08/04_manway_real_bore_flange_detail.png",
     "url": ORIGIN + "/wp-content/uploads/2026/08/04_manway_real_bore_flange_detail.png",
     "bytes": 416461,
     "sha256": "c5742b9ee84370d2ed6034d891955ff1a7774e89c1f1ad1ffd5b2b5d14bfd753"},
)
TARGET_IDS = tuple(row["attachment_id"] for row in TARGETS)

PLAN_DIR = ROOT / "Dado" / "20_Working" / "wordpress_fixed_origin_file_cleanup_plans"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
LOCAL_STATE = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) \
    / "FRPDepot-WordPress" / "fixed-four-origin-file-cleanup"
REGISTRY_KEY = LOCAL_STATE / "stage-registry.key"
REGISTRY_DIR = LOCAL_STATE / "stages"
ATTEMPT_DIR = LOCAL_STATE / "attempts"
RESULT_DIR = LOCAL_STATE / "results"
EVENT_DIR = LOCAL_STATE / "events"
RUNTIME_TEMP = ROOT / "Dado" / "Temp" / "playwright-runtime"

PHASES = frozenset({"preinstall", "preactivate", "predelete", "final"})
PLAN_KEYS = frozenset({
    "schema_version", "tool", "tool_version", "origin", "created_utc", "expires_utc",
    "nonce", "operation_sha256", "plugin", "artifact", "targets", "before",
    "after_expected", "write_sequence", "risk", "forbidden", "sha256",
})
ROW_KEYS = frozenset({"present", "active", "version", "update_marker", "plugin_file", "fingerprint"})
STATE_KEYS = frozenset({"plugin", "public_files", "attachment_records", "media_catalog"})
MEDIA_CATALOG_KEYS = frozenset({"total", "enumerated", "pages", "rows_sha256", "target_conflicts"})


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
        value = path.lstat()
    except OSError:
        return True
    return stat.S_ISLNK(value.st_mode) or bool(
        getattr(value, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _strict_file_bytes(fixed: Path, description: str) -> tuple[Path, bytes]:
    if is_reparse(fixed):
        raise CleanupError(f"REFUSED: fixed {description} is missing or aliased.")
    try:
        path = fixed.resolve(strict=True)
    except OSError as exc:
        raise CleanupError(f"REFUSED: fixed {description} is missing or aliased.") from exc
    absolute = Path(os.path.abspath(fixed))
    if os.path.normcase(str(path)) != os.path.normcase(str(absolute)) or not path.is_file():
        raise CleanupError(f"REFUSED: fixed {description} is missing or aliased.")
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if getattr(before, "st_nlink", 1) != 1:
                raise CleanupError(f"REFUSED: fixed {description} is aliased.")
            raw = handle.read()
            after = os.fstat(handle.fileno())
        named = path.stat()
    except OSError as exc:
        raise CleanupError(f"REFUSED: fixed {description} is unreadable.") from exc
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
        getattr(item, "st_nlink", 1),
    )
    if identity(before) != identity(after) or identity(after) != identity(named):
        raise CleanupError(f"REFUSED: fixed {description} changed while opened.")
    return path, raw


def validate_artifact_payload() -> tuple[dict[str, Any], bytes]:
    path, raw = _strict_file_bytes(ARTIFACT_PATH, "plugin artifact")
    manifest_path, manifest_raw = _strict_file_bytes(ARTIFACT_MANIFEST_PATH, "artifact manifest")
    if (len(raw) != ARTIFACT_BYTES or hashlib.sha256(raw).hexdigest() != ARTIFACT_SHA256
            or hashlib.sha256(manifest_raw).hexdigest() != ARTIFACT_MANIFEST_SHA256):
        raise CleanupError("REFUSED: fixed artifact or manifest bytes changed.")
    try:
        manifest = json.loads(manifest_raw.decode("ascii"))
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = tuple(archive.namelist())
            payloads = {name: archive.read(name) for name in members}
    except (UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile, KeyError) as exc:
        raise CleanupError("REFUSED: fixed artifact package is unreadable.") from exc
    if members != ARTIFACT_MEMBERS:
        raise CleanupError("REFUSED: fixed artifact member identity/order changed.")
    member_hashes = {name: hashlib.sha256(payloads[name]).hexdigest() for name in members}
    member_bytes = {name: len(payloads[name]) for name in members}
    if member_hashes != ARTIFACT_MEMBER_SHA256 or member_bytes != ARTIFACT_MEMBER_BYTES:
        raise CleanupError("REFUSED: fixed artifact member bytes changed.")
    expected_manifest = {
        "schema": 1, "plugin_name": PLUGIN_NAME, "plugin_slug": PLUGIN_SLUG,
        "plugin_file": PLUGIN_FILE, "plugin_version": PLUGIN_VERSION,
        "artifact_name": ARTIFACT_PATH.name, "artifact_bytes": ARTIFACT_BYTES,
        "artifact_sha256": ARTIFACT_SHA256,
        "members": {
            name: {"bytes": ARTIFACT_MEMBER_BYTES[name], "sha256": ARTIFACT_MEMBER_SHA256[name]}
            for name in ARTIFACT_MEMBERS
        },
    }
    if manifest != expected_manifest:
        raise CleanupError("REFUSED: fixed artifact manifest contract changed.")
    php = payloads[ARTIFACT_MEMBERS[0]].decode("utf-8", errors="strict")
    if (f"Plugin Name: {PLUGIN_NAME}" not in php or f"Version: {PLUGIN_VERSION}" not in php
            or "register_activation_hook( __FILE__, 'frpd_ffoc_activate' );" not in php):
        raise CleanupError("REFUSED: fixed plugin identity or activation hook changed.")
    artifact = {
        "path": str(path), "manifest_path": str(manifest_path), "sha256": ARTIFACT_SHA256,
        "bytes": ARTIFACT_BYTES, "manifest_sha256": ARTIFACT_MANIFEST_SHA256,
        "version": PLUGIN_VERSION, "members": list(members),
        "member_sha256": member_hashes, "member_bytes": member_bytes,
    }
    return artifact, raw


def validate_artifact() -> dict[str, Any]:
    artifact, _raw = validate_artifact_payload()
    return artifact


def row_fingerprint(row: dict[str, Any]) -> str:
    return digest_for({key: row[key] for key in ("present", "active", "version", "update_marker", "plugin_file")})


def project_row(present: bool, active: bool | None, version: str,
                update_marker: bool = False) -> dict[str, Any]:
    value = {"present": bool(present), "active": active, "version": str(version),
             "update_marker": bool(update_marker), "plugin_file": PLUGIN_FILE}
    value["fingerprint"] = row_fingerprint(value)
    return value


def expected_plugin_for_phase(phase: str) -> dict[str, Any]:
    if phase in {"preinstall", "final"}:
        return project_row(False, None, "", False)
    if phase in {"preactivate", "predelete"}:
        return project_row(True, False, PLUGIN_VERSION, False)
    raise CleanupError("REFUSED: cleanup phase is not fixed.")


def assert_cleanup_eligible(state: dict[str, Any], phase: str) -> dict[str, Any]:
    """The single normalized predicate used by stage, fresh preflight and adapters."""
    if phase not in PHASES or not isinstance(state, dict) or set(state) != STATE_KEYS:
        raise CleanupError("REFUSED: fixed cleanup state projection is invalid.")
    if state["plugin"] != expected_plugin_for_phase(phase):
        raise CleanupError("REFUSED: fixed cleanup plugin state is not exact.")
    expected_present = phase in {"preinstall", "preactivate"}
    public = state["public_files"]
    records = state["attachment_records"]
    catalog = state["media_catalog"]
    if not isinstance(public, list) or not isinstance(records, list) \
            or len(public) != 4 or len(records) != 4:
        raise CleanupError("REFUSED: four-file/readback projection is incomplete.")
    if (not isinstance(catalog, dict) or set(catalog) != MEDIA_CATALOG_KEYS
            or not isinstance(catalog.get("total"), int)
            or isinstance(catalog.get("total"), bool)
            or not isinstance(catalog.get("enumerated"), int)
            or isinstance(catalog.get("enumerated"), bool)
            or catalog["total"] < 0 or catalog["enumerated"] != catalog["total"]
            or not isinstance(catalog.get("pages"), int)
            or isinstance(catalog.get("pages"), bool) or catalog["pages"] < 1
            or not isinstance(catalog.get("rows_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", catalog["rows_sha256"])
            or catalog.get("target_conflicts") != []):
        raise CleanupError("REFUSED: authenticated complete media catalog is incomplete or conflicts with a fixed target.")
    for target, observed, record in zip(TARGETS, public, records):
        identity = {key: target[key] for key in ("position", "upload_path", "url", "bytes", "sha256")}
        if {key: observed.get(key) for key in identity} != identity:
            raise CleanupError("REFUSED: public fixed-file identity changed.")
        if (observed.get("redirected") is not False
                or set(observed) != set(identity) | {"status", "redirected", "body_bytes", "body_sha256"}):
            raise CleanupError("REFUSED: public fixed-file response shape is invalid.")
        if expected_present:
            if (observed.get("status") != 200 or observed.get("body_bytes") != target["bytes"]
                    or observed.get("body_sha256") != target["sha256"]):
                raise CleanupError("REFUSED: exact origin file bytes are not all present.")
        elif (observed.get("status") not in (404, 410)
              or observed.get("body_bytes") is not None
              or observed.get("body_sha256") is not None):
            raise CleanupError("REFUSED: exact origin file is not authoritatively absent.")
        if record != {"attachment_id": target["attachment_id"], "status": 404,
                       "redirected": False}:
            raise CleanupError("REFUSED: fixed attachment record is not absent.")
    return state


READ_CONTRACT_SCRIPT = r"""
async ({targets, attachmentIds, expectPresent, nonce}) => {
  const noCache = {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'};
  const addNonce = (raw, suffix) => {
    const u = new URL(raw);
    u.searchParams.set('_frpd_ffoc', nonce + suffix);
    return u.href;
  };
  const sha256 = async (response, maximum) => {
    if (!response.body) throw new Error('missing_body');
    const reader = response.body.getReader();
    const chunks = [];
    let total = 0;
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      total += part.value.byteLength;
      if (total > maximum) { await reader.cancel(); throw new Error('body_too_large'); }
      chunks.push(part.value);
    }
    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
    return {body_bytes: total, body_sha256: [...digest].map(v => v.toString(16).padStart(2,'0')).join('')};
  };
  const publicFiles = [];
  for (const target of targets) {
    const response = await fetch(addNonce(target.url, '-f' + target.position), {
      method: 'GET', credentials: 'omit', cache: 'no-store', redirect: 'manual', headers: noCache
    });
    let body = {body_bytes: null, body_sha256: null};
    if (expectPresent && response.status === 200) body = await sha256(response, target.bytes);
    publicFiles.push({position: target.position, upload_path: target.upload_path,
      url: target.url, bytes: target.bytes, sha256: target.sha256,
      status: response.status, redirected: response.redirected, ...body});
  }
  const attachmentRecords = [];
  for (const id of attachmentIds) {
    const response = await fetch(addNonce(location.origin + '/wp-json/wp/v2/media/' + id, '-r' + id), {
      method: 'GET', credentials: 'omit', cache: 'no-store', redirect: 'manual', headers: noCache
    });
    attachmentRecords.push({attachment_id: id, status: response.status, redirected: response.redirected});
  }
  return {public_files: publicFiles, attachment_records: attachmentRecords};
}
"""


def _query(url: str) -> tuple[str, list[tuple[str, str]]]:
    parsed = urlsplit(str(url or ""))
    if (parsed.scheme != "https" or parsed.hostname != "frpdepots.com"
            or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment):
        raise CleanupError("REFUSED: browser left the exact FRP Depot HTTPS origin.")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if len({key for key, _value in pairs}) != len(pairs):
        raise CleanupError("REFUSED: WordPress route repeats a query key.")
    return parsed.path, pairs


def assert_admin_url(url: str, mode: str) -> None:
    path, pairs = _query(url)
    values = dict(pairs)
    if mode == "plugins":
        valid = path == "/wp-admin/plugins.php" and not pairs
    elif mode == "upload":
        valid = path == "/wp-admin/plugin-install.php" and values.get("tab") == "upload"
        valid = valid and set(values) in ({"tab"}, {"tab", "_dado_refresh"})
        if valid and "_dado_refresh" in values:
            valid = bool(re.fullmatch(r"[0-9a-f]{32}", values["_dado_refresh"]))
    elif mode == "install_submit":
        valid = path == "/wp-admin/update.php" and values == {"action": "upload-plugin"}
    elif mode == "install_result":
        valid = path == "/wp-admin/update.php" and values == {"action": "upload-plugin"}
    elif mode == "activation_result":
        valid = path == "/wp-admin/plugins.php" and values == {
            "activate": "true", "plugin_status": "all", "paged": "1", "s": "",
            "frpd_ffoc_result": "deleted-4-self-deactivation-scheduled",
            "frpd_ffoc_count": "4",
        }
    elif mode == "delete_result":
        valid = path == "/wp-admin/plugins.php" and values == {
            "deleted": "true", "plugin_status": "all", "paged": "1", "s": "",
        }
    else:
        raise CleanupError("REFUSED: admin URL validation mode is not fixed.")
    if not valid:
        raise CleanupError("REFUSED: WordPress administration route is outside the fixed contract.")


def assert_state_action_url(url: str, action: str) -> None:
    if action != "activate":
        raise CleanupError("REFUSED: only the fixed activation action is reachable here.")
    absolute = urljoin(PLUGINS_URL, str(url or ""))
    path, pairs = _query(absolute)
    values = dict(pairs)
    valid = (path == "/wp-admin/plugins.php"
             and set(values) == {"action", "plugin", "plugin_status", "paged", "s", "_wpnonce"}
             and values.get("action") == "activate" and values.get("plugin") == PLUGIN_FILE
             and values.get("plugin_status") == "all" and values.get("paged") == "1"
             and values.get("s") == ""
             and bool(re.fullmatch(r"[A-Za-z0-9_-]{8,64}", values.get("_wpnonce", ""))))
    if not valid:
        raise CleanupError("REFUSED: fixed activation URL is ambiguous or outside scope.")


def assert_delete_action_url(url: str) -> None:
    absolute = urljoin(PLUGINS_URL, str(url or ""))
    path, pairs = _query(absolute)
    values = dict(pairs)
    valid = (path == "/wp-admin/plugins.php"
             and set(values) == {"action", "checked[]", "plugin_status", "paged", "s", "_wpnonce"}
             and values.get("action") == "delete-selected" and values.get("checked[]") == PLUGIN_FILE
             and values.get("plugin_status") == "all" and values.get("paged") == "1"
             and values.get("s") == ""
             and bool(re.fullmatch(r"[A-Za-z0-9_-]{8,64}", values.get("_wpnonce", ""))))
    if not valid:
        raise CleanupError("REFUSED: fixed plugin-delete URL is ambiguous or outside scope.")


def assert_delete_confirm_url(url: str) -> None:
    assert_delete_action_url(url)


MAX_DIAGNOSTIC_ISSUES = 20


def sanitized_row_link_shape(value: Any) -> dict[str, Any]:
    """Describe one Media Library link without retaining any query value."""
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
            "path": "", "query_keys": [], "fragment": False,
            "credentials": False, "parsed_attachment_id": None,
            "post_id_hint": None, "action_edit": False,
        }
    origin = urlsplit(ORIGIN)
    post_values = [item for key, item in pairs if key == "post"]
    post_id_hint = None
    if len(post_values) == 1 and post_values[0].isdigit() and int(post_values[0]) > 0:
        post_id_hint = int(post_values[0])
    return {
        "raw_form": raw_form,
        "malformed": False,
        "same_origin": (
            parsed.scheme == origin.scheme and parsed.hostname == origin.hostname
            and parsed.port == origin.port and not parsed.username and not parsed.password
        ),
        "path": parsed.path,
        "query_keys": sorted(key for key, _item in pairs),
        "fragment": bool(parsed.fragment),
        "credentials": bool(parsed.username or parsed.password),
        "parsed_attachment_id": media_base.parse_attachment_edit_link(resolved),
        "post_id_hint": post_id_hint,
        "action_edit": any(key == "action" and item == "edit" for key, item in pairs),
    }


def _diagnostic_page_number(url: str) -> int:
    try:
        values = dict(parse_qsl(urlsplit(str(url or "")).query, keep_blank_values=True))
        raw = values.get("paged", "1")
        return int(raw) if str(raw).isdigit() and int(raw) > 0 else 1
    except (TypeError, ValueError):
        return 1


def cleanup_row_identity(row: Any) -> tuple[int | None, list[int]]:
    """Use WordPress's canonical tr#post-ID plus a matching exact edit link."""
    match = re.fullmatch(r"post-([1-9][0-9]*)", str(row.get_attribute("id") or ""))
    attachment_id = int(match.group(1)) if match else None
    exact_ids = sorted({
        found for found in (
            media_base.parse_attachment_edit_link(str(link.get_attribute("href") or ""))
            for link in row.query_selector_all("a[href]")
        ) if found is not None
    })
    if attachment_id is None or attachment_id not in exact_ids:
        return None, exact_ids
    return attachment_id, exact_ids


def cleanup_row_upload_filename(row: Any) -> str | None:
    """Return one strict same-origin upload basename, or refuse ambiguity.

    This is a read-only fallback for WordPress rows that preserve their canonical
    post identity and file link but omit ``td.title p.filename strong``. It never
    derives a name from the attachment permalink or arbitrary row text.
    """
    expected = urlsplit(ORIGIN)
    candidates: set[str] = set()
    for link in row.query_selector_all("a[href]"):
        raw = str(link.get_attribute("href") or "").strip()
        if not raw:
            continue
        try:
            parsed = urlsplit(urljoin(ORIGIN + "/wp-admin/upload.php", raw))
        except ValueError:
            continue
        if (parsed.scheme != expected.scheme or parsed.hostname != expected.hostname
                or parsed.port != expected.port or parsed.username or parsed.password
                or parsed.query or parsed.fragment
                or not parsed.path.startswith("/wp-content/uploads/")):
            continue
        basename = unquote(Path(parsed.path).name)
        if (not basename or basename in {".", ".."} or len(basename) > 255
                or "/" in basename or "\\" in basename
                or any(ord(character) < 32 or ord(character) == 127 for character in basename)):
            continue
        candidates.add(basename)
    return candidates.pop() if len(candidates) == 1 else None


class StrictCleanupMediaReader(media_base.AdminPage):
    """Complete reader using WordPress 7.0.3's canonical list-row identity."""

    def _row_records(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for row in self._page.query_selector_all(media_base.LIST_ROW_SELECTOR):
            if (not row.query_selector_all("a[href]")
                    and len(row.query_selector_all(media_base.EMPTY_TABLE_CELL_SELECTOR)) == 1):
                continue
            attachment_id, _exact_ids = cleanup_row_identity(row)
            name_node = row.query_selector(media_base.ROW_FILENAME_SELECTOR)
            raw_name = str(name_node.inner_text() or "") if name_node is not None else ""
            filename = re.sub(r"(?i)^\s*file\s*name\s*:\s*", "", raw_name).strip()
            if attachment_id is not None and not filename:
                filename = cleanup_row_upload_filename(row) or ""
            if attachment_id is None or not filename:
                found.append({"id": None, "filename": "", "stem": ""})
                continue
            found.append({
                "id": attachment_id,
                "filename": filename,
                "stem": media_base._normalise_stem(filename),
            })
        return found


class DiagnosticMediaReader(StrictCleanupMediaReader):
    """The proven reader plus bounded, credential-free failure structure."""

    def __init__(self, page: Any):
        super().__init__(page)
        self.page_metrics: list[dict[str, Any]] = []
        self.issue_rows: list[dict[str, Any]] = []
        self.issue_count = 0
        self.total_reads: list[dict[str, Any]] = []

    def _library_total(self) -> int | None:
        total = super()._library_total()
        self.total_reads.append({
            "page": _diagnostic_page_number(str(self._page.url or "")),
            "total": total,
        })
        return total

    def _row_records(self) -> list[dict[str, Any]]:
        records = super()._row_records()
        raw_rows = self._page.query_selector_all(media_base.LIST_ROW_SELECTOR)
        page = _diagnostic_page_number(str(self._page.url or ""))
        self.page_metrics.append({
            "page": page,
            "raw_rows": len(raw_rows),
            "parsed_records": len(records),
            "identified_records": sum(1 for row in records if row.get("id") is not None),
            "unidentified_records": sum(1 for row in records if row.get("id") is None),
        })
        for row_index, row in enumerate(raw_rows, start=1):
            if (not row.query_selector_all("a[href]")
                    and len(row.query_selector_all(media_base.EMPTY_TABLE_CELL_SELECTOR)) == 1):
                continue
            attachment_id, exact_ids = cleanup_row_identity(row)
            primary_links = row.query_selector_all(media_base.ROW_LINK_SELECTOR)
            name_node = row.query_selector(media_base.ROW_FILENAME_SELECTOR)
            raw_name = str(name_node.inner_text() or "") if name_node is not None else ""
            filename = re.sub(r"(?i)^\s*file\s*name\s*:\s*", "", raw_name).strip()
            reason = ""
            if attachment_id is None:
                reason = "canonical_row_identity_unavailable_or_unmatched"
            elif not filename:
                fallback = cleanup_row_upload_filename(row)
                if fallback is None:
                    reason = "filename_unavailable"
                else:
                    filename = fallback
            if not reason:
                continue
            self.issue_count += 1
            if len(self.issue_rows) >= MAX_DIAGNOSTIC_ISSUES:
                continue
            shape_counts: dict[str, dict[str, Any]] = {}
            for link in row.query_selector_all("a[href]"):
                shape = sanitized_row_link_shape(link.get_attribute("href"))
                key = canonical(shape)
                if key not in shape_counts:
                    shape_counts[key] = {"count": 0, "shape": shape}
                shape_counts[key]["count"] += 1
            self.issue_rows.append({
                "page": page,
                "row_index": row_index,
                "reason": reason,
                "canonical_row_attachment_id": attachment_id,
                "exact_attachment_ids": exact_ids,
                "primary_column_link_count": len(primary_links),
                "all_link_count": len(row.query_selector_all("a[href]")),
                "filename_present": bool(filename),
                "filename_length": len(filename),
                "filename_extension": Path(filename).suffix.casefold() if filename else "",
                "filename_sha256": hashlib.sha256(filename.encode("utf-8")).hexdigest() if filename else None,
                "link_shapes": [shape_counts[key] for key in sorted(shape_counts)],
            })
        return records

    def diagnostic(self) -> dict[str, Any]:
        snapshot = self.enumerate_library()
        rows = snapshot.get("rows") if isinstance(snapshot, dict) else None
        return {
            "snapshot": {
                "complete": snapshot.get("complete") if isinstance(snapshot, dict) else None,
                "total": snapshot.get("total") if isinstance(snapshot, dict) else None,
                "pages": snapshot.get("pages") if isinstance(snapshot, dict) else None,
                "identified_rows": len(rows) if isinstance(rows, list) else None,
                "unidentified_rows": snapshot.get("unidentified") if isinstance(snapshot, dict) else None,
                "identified_rows_sha256": digest_for(rows) if isinstance(rows, list) else None,
            },
            "total_reads": self.total_reads,
            "page_metrics": self.page_metrics,
            "issue_count": self.issue_count,
            "issue_rows": self.issue_rows,
            "issues_truncated": self.issue_count > len(self.issue_rows),
        }


class AdminPage:
    def __init__(self, page: Any):
        self.page = page
        self._media_reader = StrictCleanupMediaReader(page)

    def read_media_catalog_diagnostic(self) -> dict[str, Any]:
        return DiagnosticMediaReader(self.page).diagnostic()

    def read_media_catalog(self) -> dict[str, Any]:
        """Prove the authenticated Media Library is complete and target-free."""
        try:
            snapshot = self._media_reader.enumerate_library()
        except Exception as exc:
            raise CleanupError("REFUSED: authenticated complete Media Library walk failed.") from exc
        if not isinstance(snapshot, dict):
            raise CleanupError("REFUSED: authenticated Media Library snapshot is malformed.")
        rows = snapshot.get("rows")
        total = snapshot.get("total")
        pages = snapshot.get("pages")
        if (snapshot.get("complete") is not True or not isinstance(rows, list)
                or not isinstance(total, int) or isinstance(total, bool) or total < 0
                or not isinstance(pages, int) or isinstance(pages, bool) or pages < 1
                or len(rows) != total):
            raise CleanupError("REFUSED: authenticated Media Library walk is incomplete.")
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"id", "filename", "stem"}
                    or not isinstance(row.get("id"), int) or isinstance(row.get("id"), bool)
                    or row["id"] <= 0 or not isinstance(row.get("filename"), str)
                    or not row["filename"].strip() or not isinstance(row.get("stem"), str)):
                raise CleanupError("REFUSED: authenticated Media Library row is malformed.")
            normalized.append({"id": row["id"], "filename": row["filename"]})
        normalized.sort(key=lambda item: item["id"])
        if len({item["id"] for item in normalized}) != len(normalized):
            raise CleanupError("REFUSED: authenticated Media Library identity is duplicated.")
        target_names = {Path(target["upload_path"]).name for target in TARGETS}
        conflicts = [
            item for item in normalized
            if item["id"] in TARGET_IDS or item["filename"] in target_names
        ]
        return {
            "total": total,
            "enumerated": len(normalized),
            "pages": pages,
            "rows_sha256": digest_for(normalized),
            "target_conflicts": conflicts,
        }

    def goto_plugins(self) -> None:
        assert_admin_url(PLUGINS_URL, "plugins")
        self.page.goto(PLUGINS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(str(self.page.url), "plugins")

    def rows(self) -> list[Any]:
        rows = self.page.query_selector_all(ROW_SELECTOR)
        if len(rows) > 1:
            raise CleanupError("REFUSED: fixed cleanup plugin row is ambiguous.")
        return rows

    def read_row(self, allow_absent: bool = False) -> dict[str, Any]:
        rows = self.rows()
        if not rows:
            if allow_absent:
                return project_row(False, None, "", False)
            raise CleanupError("REFUSED: fixed cleanup plugin is not installed.")
        row = rows[0]
        tokens = set(str(row.get_attribute("class") or "").split())
        active, inactive = "active" in tokens, "inactive" in tokens
        if active == inactive:
            raise CleanupError("REFUSED: fixed cleanup plugin state is ambiguous.")
        has_activate = row.query_selector(ACTIVATE_SELECTOR) is not None
        has_deactivate = row.query_selector(DEACTIVATE_SELECTOR) is not None
        if has_activate == has_deactivate or active != has_deactivate:
            raise CleanupError("REFUSED: fixed cleanup plugin action/state disagrees.")
        match = VERSION_PATTERN.search(str(row.inner_text() or ""))
        if not match:
            raise CleanupError("REFUSED: fixed cleanup plugin version is unreadable.")
        updates = self.page.query_selector_all(UPDATE_ROW_SELECTOR)
        if len(updates) > 1:
            raise CleanupError("REFUSED: fixed cleanup plugin update marker is ambiguous.")
        return project_row(True, active, match.group(1), bool(updates) or "update" in tokens)

    def read_origin_contract(self, expect_present: bool) -> dict[str, Any]:
        try:
            value = self.page.evaluate(READ_CONTRACT_SCRIPT, {
                "targets": list(TARGETS), "attachmentIds": list(TARGET_IDS),
                "expectPresent": bool(expect_present), "nonce": secrets.token_hex(16),
            })
        except Exception as exc:
            raise CleanupError("Fixed anonymous origin/record reads failed.") from exc
        if not isinstance(value, dict) or set(value) != {"public_files", "attachment_records"}:
            raise CleanupError("REFUSED: fixed anonymous read projection is invalid.")
        return value

    def prepare_install(self) -> tuple[Any, Any]:
        url = f"{UPLOAD_URL}&_dado_refresh={secrets.token_hex(16)}"
        assert_admin_url(url, "upload")
        self.page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(str(self.page.url), "upload")
        forms = self.page.query_selector_all(UPLOAD_FORM_SELECTOR)
        if len(forms) != 1:
            raise CleanupError("REFUSED: exact fixed plugin upload form is unavailable.")
        form = forms[0]
        assert_admin_url(urljoin(UPLOAD_URL, str(form.get_attribute("action") or "")), "install_submit")
        choosers = form.query_selector_all('input[type="file"][name="pluginzip"]')
        submits = form.query_selector_all("#install-plugin-submit")
        nonces = form.query_selector_all('input[type="hidden"][name="_wpnonce"]')
        if len(choosers) != 1 or len(submits) != 1 or len(nonces) != 1:
            raise CleanupError("REFUSED: exact fixed plugin upload controls are unavailable.")
        return choosers[0], submits[0]

    def execute_install(self, chooser: Any, submit: Any, artifact_raw: bytes,
                        eligibility_state: dict[str, Any]) -> dict[str, Any]:
        assert_cleanup_eligible(eligibility_state, "preinstall")
        if len(artifact_raw) != ARTIFACT_BYTES \
                or hashlib.sha256(artifact_raw).hexdigest() != ARTIFACT_SHA256:
            raise CleanupError("REFUSED: validated in-memory plugin artifact changed.")
        chooser.set_input_files({"name": ARTIFACT_PATH.name, "mimeType": "application/zip",
                                 "buffer": artifact_raw}, timeout=ACTION_TIMEOUT_MS)
        submit.click(timeout=ACTION_TIMEOUT_MS)
        self.page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(str(self.page.url), "install_result")
        if (self.page.query_selector_all("table.update-from-upload-comparison")
                or self.page.query_selector_all("a.update-from-upload-overwrite")):
            raise IndeterminateError("Install unexpectedly reached a replace route; no overwrite clicked.")
        return self.read_bounded(project_row(True, False, PLUGIN_VERSION, False))

    def prepare_activation(self, eligibility_state: dict[str, Any]) -> Any:
        assert_cleanup_eligible(eligibility_state, "preactivate")
        self.goto_plugins()
        if self.read_row() != expected_plugin_for_phase("preactivate"):
            raise CleanupError("REFUSED: fixed cleanup plugin drifted before activation.")
        link = self.rows()[0].query_selector(ACTIVATE_SELECTOR)
        if link is None:
            raise CleanupError("REFUSED: fixed cleanup activation control is unavailable.")
        assert_state_action_url(str(link.get_attribute("href") or ""), "activate")
        return link

    def execute_activation(self, link: Any, eligibility_state: dict[str, Any]) -> dict[str, Any]:
        assert_cleanup_eligible(eligibility_state, "preactivate")
        link.click(timeout=ACTION_TIMEOUT_MS)
        self.page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(str(self.page.url), "activation_result")
        marker = {"result": "deleted-4-self-deactivation-scheduled", "deleted_count": 4}
        after = self.read_bounded(project_row(True, False, PLUGIN_VERSION, False))
        return {"bounded_plugin_result": marker, "after": after}

    def prepare_delete(self, eligibility_state: dict[str, Any]) -> Any:
        assert_cleanup_eligible(eligibility_state, "predelete")
        self.goto_plugins()
        if self.read_row() != expected_plugin_for_phase("predelete"):
            raise CleanupError("REFUSED: fixed cleanup plugin drifted before its own deletion.")
        links = self.rows()[0].query_selector_all(DELETE_SELECTOR)
        if len(links) != 1:
            raise CleanupError("REFUSED: exact fixed cleanup plugin Delete link is unavailable.")
        assert_delete_action_url(str(links[0].get_attribute("href") or ""))
        links[0].click(timeout=ACTION_TIMEOUT_MS)
        self.page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_delete_confirm_url(str(self.page.url))
        candidates = []
        for form in self.page.query_selector_all("form"):
            checked = form.query_selector_all('input[name="checked[]"]')
            verify = form.query_selector_all('input[name="verify-delete"][value="1"]')
            submit = form.query_selector_all('input#submit[type="submit"]')
            if len(checked) == len(verify) == len(submit) == 1:
                candidates.append((form, checked[0], submit[0]))
        if len(candidates) != 1:
            raise CleanupError("REFUSED: exact fixed plugin-delete confirmation form is unavailable.")
        form, checked, submit = candidates[0]
        if str(checked.get_attribute("value") or "") != PLUGIN_FILE:
            raise CleanupError("REFUSED: plugin-delete confirmation targets another plugin.")
        assert_delete_confirm_url(urljoin(str(self.page.url), str(form.get_attribute("action") or "")))
        return submit

    def execute_delete(self, submit: Any, eligibility_state: dict[str, Any]) -> dict[str, Any]:
        assert_cleanup_eligible(eligibility_state, "predelete")
        submit.click(timeout=ACTION_TIMEOUT_MS)
        self.page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(str(self.page.url), "delete_result")
        self.goto_plugins()
        after = self.read_row(allow_absent=True)
        if after != project_row(False, None, "", False):
            raise IndeterminateError("Fixed cleanup plugin did not read back absent after deletion.")
        return after

    def read_bounded(self, wanted: dict[str, Any]) -> dict[str, Any]:
        for index in range(POST_WRITE_READ_ROUNDS):
            self.goto_plugins()
            observed = self.read_row(allow_absent=True)
            if observed == wanted:
                return observed
            if index + 1 < POST_WRITE_READ_ROUNDS:
                self.page.wait_for_timeout(POST_WRITE_READ_DELAY_MS)
        raise IndeterminateError("Fixed cleanup plugin row did not reach the exact expected state.")


def collect_state(admin: AdminPage, phase: str) -> dict[str, Any]:
    if phase not in PHASES:
        raise CleanupError("REFUSED: cleanup state-read phase is not fixed.")
    admin.goto_plugins()
    plugin = admin.read_row(allow_absent=True)
    reads = admin.read_origin_contract(phase in {"preinstall", "preactivate"})
    catalog = admin.read_media_catalog()
    state = {"plugin": plugin, "public_files": reads["public_files"],
             "attachment_records": reads["attachment_records"],
             "media_catalog": catalog}
    return assert_cleanup_eligible(state, phase)


def ensure_runtime_temp() -> None:
    names = ("TMP", "TEMP", "TMPDIR")
    if not all(os.environ.get(name) and Path(os.environ[name]).is_dir() for name in names):
        RUNTIME_TEMP.mkdir(parents=True, exist_ok=True)
        stable = str(RUNTIME_TEMP.resolve())
        for name in names:
            os.environ[name] = stable


@contextlib.contextmanager
def admin_session(purpose: str) -> Iterator[AdminPage]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
    ensure_runtime_temp()
    with ui_browser_lock("wordpress", purpose=purpose), sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=15_000)
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            raise CleanupError("Authenticated WordPress browser is unavailable.") from exc
        pages = [page for context in browser.contexts for page in context.pages]
        page = next((item for item in pages if str(item.url).startswith(ORIGIN + "/wp-admin")), None)
        if page is None:
            raise CleanupError("Authenticated WordPress browser has no FRP Depot admin page.")
        yield AdminPage(page)


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
                handle.write(secrets.token_bytes(32)); handle.flush(); os.fsync(handle.fileno())
    if (not REGISTRY_KEY.is_file() or is_reparse(REGISTRY_KEY)
            or getattr(REGISTRY_KEY.stat(), "st_nlink", 1) != 1):
        raise CleanupError("REFUSED: local stage-registry key is missing or aliased.")
    key = REGISTRY_KEY.read_bytes()
    if len(key) != 32:
        raise CleanupError("REFUSED: local stage-registry key is invalid.")
    return key


def registry_mac(core: dict[str, Any]) -> str:
    return hmac.new(registry_key(), canonical(core).encode("ascii"), hashlib.sha256).hexdigest()


def exclusive_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    pending = path.with_name(f".{path.name}.{secrets.token_hex(12)}.pending")
    descriptor = None
    try:
        descriptor = os.open(str(pending), flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            if handle.write(payload) != len(payload):
                raise OSError("immutable evidence write was short")
            handle.flush(); os.fsync(handle.fileno())
    except BaseException:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        with contextlib.suppress(OSError):
            pending.unlink(missing_ok=True)
        raise
    try:
        os.link(str(pending), str(path))
    except FileExistsError as exc:
        with contextlib.suppress(OSError):
            pending.unlink(missing_ok=True)
        raise CleanupError("REFUSED: immutable evidence already exists; no replay.") from exc
    finally:
        with contextlib.suppress(OSError):
            pending.unlink(missing_ok=True)


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"ts": utc_now().isoformat(), "action": action,
                                 "evidence": evidence}, ensure_ascii=True) + "\n")


def operation_sha() -> str:
    return digest_for({
        "operation_schema_version": OPERATION_SCHEMA_VERSION, "tool": TOOL_NAME,
        "origin": ORIGIN, "plugin_file": PLUGIN_FILE, "artifact_sha256": ARTIFACT_SHA256,
        "targets": list(TARGETS),
        "write_sequence": ["install_fixed_plugin_inactive", "activate_fixed_plugin_once",
                           "plugin_preflight_all_four", "unlink_1", "unlink_2", "unlink_3",
                           "unlink_4", "self_deactivate", "verify_absence",
                           "delete_only_fixed_inactive_plugin", "final_readback"],
    })


def plan_path(created: datetime, plan_sha: str) -> Path:
    return (PLAN_DIR / f"{created.strftime('%Y%m%dT%H%M%SZ')}_fixed_origin_cleanup_{plan_sha[:16]}.json").resolve()


def stage_registry_path(plan_sha: str) -> Path:
    return REGISTRY_DIR / f"{plan_sha}.json"


def attempt_path(operation: str) -> Path:
    return ATTEMPT_DIR / f"{operation}.json"


def result_path(operation: str) -> Path:
    return RESULT_DIR / f"{operation}.json"


def event_path(operation: str, name: str) -> Path:
    if name not in {"02-activation-attempted", "03-plugin-delete-attempted"}:
        raise CleanupError("REFUSED: fixed event name is invalid.")
    return EVENT_DIR / operation / f"{name}.json"


def after_expected() -> dict[str, Any]:
    return {
        "plugin": project_row(False, None, "", False),
        "origin_http_status_allowed": [404, 410],
        "attachment_record_http_status": 404,
        "origin_files_absent": 4, "attachment_records_absent": list(TARGET_IDS),
    }


def stage_plan(artifact: dict[str, Any], before: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    assert_cleanup_eligible(before, "preinstall")
    if artifact != validate_artifact():
        raise CleanupError("REFUSED: fixed artifact changed before staging.")
    created = utc_now()
    operation = operation_sha()
    core = {
        "schema_version": SCHEMA_VERSION, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "origin": ORIGIN, "created_utc": created.isoformat(),
        "expires_utc": (created + PLAN_LIFETIME).isoformat(), "nonce": secrets.token_hex(16),
        "operation_sha256": operation,
        "plugin": {"name": PLUGIN_NAME, "slug": PLUGIN_SLUG, "file": PLUGIN_FILE,
                   "version": PLUGIN_VERSION},
        "artifact": artifact, "targets": list(TARGETS), "before": before,
        "after_expected": after_expected(),
        "write_sequence": [
            "1. Install the exact pinned fixed plugin ZIP inactive.",
            "2. Activate that exact plugin once.",
            "3. Activation preflights all four exact paths, sizes, SHA-256 values and record absences before any unlink.",
            "4. Activation unlinks positions 1, 2, 3 and 4 sequentially; earlier deletions remain if a later unlink fails.",
            "5. The plugin schedules self-deactivation in the activation request.",
            "6. Verify all four cache-busted public URLs are 404/410 and attachment IDs 5521/5523/5525/5527 remain 404.",
            "7. Only after full verification, delete only the exact fixed inactive cleanup plugin and verify it absent.",
        ],
        "risk": (
            "NOT ATOMIC. ONE ATTEMPT, NO RETRY, NO ROLLBACK. Plugin installation can land before "
            "activation. Activation preflights all four targets before deletion, then unlinks four "
            "files sequentially, so partial file deletion is possible and earlier deletions remain. "
            "If activation or later verification fails, the plugin may remain installed/inactive; "
            "its own plugin files are deleted only after full success. There is no cleanup after failure."
        ),
        "forbidden": ["arbitrary path", "arbitrary URL", "arbitrary ZIP", "generic plugin deployment",
                      "public mutation route", "admin mutation route", "retry", "rollback", "restore",
                      "email", "WooCommerce write", "Zoho write", "product write", "order write",
                      "customer write", "payment write", "unrelated plugin delete"],
    }
    plan = {**core, "sha256": digest_for(core)}
    path = plan_path(created, plan["sha256"])
    exclusive_json(path, plan)
    raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    registry_core = {
        "schema_version": SCHEMA_VERSION, "tool_version": TOOL_VERSION,
        "plan_sha256": plan["sha256"], "plan_file_sha256": raw_hash,
        "plan_path": str(path), "operation_sha256": operation, "nonce": plan["nonce"],
        "created_utc": plan["created_utc"], "expires_utc": plan["expires_utc"],
    }
    exclusive_json(stage_registry_path(plan["sha256"]),
                   {**registry_core, "hmac_sha256": registry_mac(registry_core)})
    return path, plan


def fixed_plan_path(raw: str) -> Path:
    path = Path(raw).resolve()
    if (path.parent != PLAN_DIR.resolve() or path.suffix.lower() != ".json" or not path.is_file()
            or is_reparse(path) or getattr(path.stat(), "st_nlink", 1) != 1):
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
    core = dict(plan); saved = str(core.pop("sha256", ""))
    if not secrets.compare_digest(saved, digest_for(core)):
        raise CleanupError("REFUSED: plan hash failed.")
    if (plan["schema_version"] != SCHEMA_VERSION or plan["tool"] != TOOL_NAME
            or plan["tool_version"] != TOOL_VERSION or plan["origin"] != ORIGIN
            or plan["plugin"] != {"name": PLUGIN_NAME, "slug": PLUGIN_SLUG,
                                  "file": PLUGIN_FILE, "version": PLUGIN_VERSION}
            or plan["targets"] != list(TARGETS) or plan["after_expected"] != after_expected()
            or plan["operation_sha256"] != operation_sha()):
        raise CleanupError("REFUSED: immutable fixed cleanup identity changed.")
    created = datetime.fromisoformat(plan["created_utc"])
    expires = datetime.fromisoformat(plan["expires_utc"])
    if (created.tzinfo is None or expires.tzinfo is None or expires - created != PLAN_LIFETIME
            or utc_now() > expires or path.name != plan_path(created.astimezone(timezone.utc), saved).name):
        raise CleanupError("REFUSED: plan lifetime, expiry or filename is invalid.")
    artifact = validate_artifact()
    if plan["artifact"] != artifact:
        raise CleanupError("REFUSED: plan artifact identity changed.")
    assert_cleanup_eligible(plan["before"], "preinstall")
    registry = json.loads(stage_registry_path(saved).read_text(encoding="ascii"))
    registry_core = {
        "schema_version": SCHEMA_VERSION, "tool_version": TOOL_VERSION,
        "plan_sha256": saved, "plan_file_sha256": hashlib.sha256(raw).hexdigest(),
        "plan_path": str(path), "operation_sha256": operation_sha(), "nonce": plan["nonce"],
        "created_utc": plan["created_utc"], "expires_utc": plan["expires_utc"],
    }
    if (not isinstance(registry, dict) or set(registry) != set(registry_core) | {"hmac_sha256"}
            or {key: registry.get(key) for key in registry_core} != registry_core
            or not isinstance(registry.get("hmac_sha256"), str)
            or not hmac.compare_digest(registry["hmac_sha256"], registry_mac(registry_core))):
        raise CleanupError("REFUSED: authenticated stage registry failed.")
    return path, plan


def assert_commit_execution_window(plan: dict[str, Any]) -> None:
    expires = datetime.fromisoformat(str(plan.get("expires_utc", "")))
    if expires.tzinfo is None or utc_now() + COMMIT_EXPIRY_MARGIN >= expires:
        raise CleanupError("REFUSED: insufficient plan time remains; stage a fresh plan.")


def command_diagnose_library(_args: argparse.Namespace) -> None:
    with admin_session("WordPress fixed-origin cleanup bounded read-only Media Library diagnostic") as admin:
        diagnostic = admin.read_media_catalog_diagnostic()
    print_json({
        "status": "READ_ONLY_MEDIA_LIBRARY_DIAGNOSTIC",
        "diagnostic": diagnostic,
        "plan_created": False,
        "attempt_lock_created": False,
        "website_writes": 0,
        "origin_files_deleted": 0,
        "plugin_installed": False,
        "emails": 0,
    })


def command_stage(_args: argparse.Namespace) -> None:
    artifact = validate_artifact()
    operation = operation_sha()
    if attempt_path(operation).exists() or result_path(operation).exists() or (EVENT_DIR / operation).exists():
        raise CleanupError("REFUSED: fixed one-use cleanup operation is permanently replay-locked.")
    with admin_session("WordPress fixed four-origin-file cleanup read-only stage") as admin:
        before = collect_state(admin, "preinstall")
    path, plan = stage_plan(artifact, before)
    print_json({
        "status": "STAGED_READ_ONLY", "plan": str(path), "plan_sha256": plan["sha256"],
        "operation_sha256": operation, "artifact_sha256": artifact["sha256"],
        "targets": list(TARGETS), "before": before, "after_expected": plan["after_expected"],
        "write_sequence": plan["write_sequence"], "risk": plan["risk"],
        "expires_utc": plan["expires_utc"], "approval_required": APPROVAL_WORD,
        "website_writes": 0, "emails": 0,
    })


def _write_event(operation: str, plan: dict[str, Any], name: str) -> None:
    exclusive_json(event_path(operation, name), {
        "schema": 1, "tool": TOOL_NAME, "operation_sha256": operation,
        "plan_sha256": plan["sha256"], "event": name, "utc": utc_now().isoformat(),
        "status": "attempted_no_retry",
    })


def command_commit(args: argparse.Namespace) -> None:
    require_approval(args.approval)
    path, plan = load_plan(args.plan)
    operation = plan["operation_sha256"]
    if attempt_path(operation).exists() or result_path(operation).exists() or (EVENT_DIR / operation).exists():
        raise CleanupError("REFUSED: fixed one-use cleanup operation is permanently replay-locked.")
    artifact, artifact_raw = validate_artifact_payload()
    if artifact != plan["artifact"]:
        raise CleanupError("REFUSED: fixed artifact changed after plan loading.")
    locked = False
    completed: list[str] = []
    try:
        with admin_session("WordPress fixed four-origin-file cleanup one-attempt commit") as admin:
            fresh = collect_state(admin, "preinstall")
            if fresh != plan["before"]:
                raise CleanupError("REFUSED: exact live state drifted after staging; stage a fresh plan.")
            chooser, install_submit = admin.prepare_install()
            assert_cleanup_eligible(fresh, "preinstall")
            assert_commit_execution_window(plan)
            exclusive_json(attempt_path(operation), {
                "schema": 1, "tool": TOOL_NAME, "operation_sha256": operation,
                "plan_sha256": plan["sha256"], "locked_utc": utc_now().isoformat(),
                "status": "attempt_started_no_retry",
            })
            locked = True
            installed = admin.execute_install(chooser, install_submit, artifact_raw, fresh)
            completed.append("plugin_installed_inactive_verified")

            preactivate = collect_state(admin, "preactivate")
            activation = admin.prepare_activation(preactivate)
            _write_event(operation, plan, "02-activation-attempted")
            activation_result = admin.execute_activation(activation, preactivate)
            completed.append("activation_cleanup_and_self_deactivation_verified")

            predelete = collect_state(admin, "predelete")
            completed.append("four_public_urls_and_attachment_records_absent_verified")
            delete_submit = admin.prepare_delete(predelete)
            _write_event(operation, plan, "03-plugin-delete-attempted")
            deleted_plugin = admin.execute_delete(delete_submit, predelete)
            completed.append("fixed_inactive_cleanup_plugin_deleted_verified")

            final = collect_state(admin, "final")
            completed.append("final_fixed_contract_verified")
            result = {
                "schema": 1, "tool": TOOL_NAME, "operation_sha256": operation,
                "plan_sha256": plan["sha256"], "status": "COMMITTED_AND_VERIFIED",
                "completed_utc": utc_now().isoformat(), "completed_steps": completed,
                "installed": installed, "activation_result": activation_result,
                "predelete_verification": predelete, "deleted_plugin": deleted_plugin,
                "final": final, "browser_write_attempts": 3, "origin_files_deleted": 4,
                "attachment_records_deleted": 0, "plugin_deleted": True,
                "no_retry": True, "rollback": False, "emails": 0,
                "woocommerce_writes": 0, "zoho_writes": 0,
            }
            exclusive_json(result_path(operation), result)
    except Exception as exc:
        if locked:
            failure = {
                "schema": 1, "tool": TOOL_NAME, "operation_sha256": operation,
                "plan_sha256": plan["sha256"], "status": "INDETERMINATE_NO_RETRY",
                "failed_utc": utc_now().isoformat(), "error_type": type(exc).__name__,
                "error": "One-attempt fixed cleanup did not complete every verification; no retry or rollback.",
                "completed_steps": completed, "earlier_effects_remain": True,
                "partial_origin_file_deletion_possible": True,
                "plugin_may_remain_installed_inactive": True, "no_retry": True,
                "rollback": False, "emails": 0, "woocommerce_writes": 0, "zoho_writes": 0,
            }
            with contextlib.suppress(CleanupError):
                exclusive_json(result_path(operation), failure)
            with contextlib.suppress(OSError):
                append_receipt("wordpress_fixed_origin_file_cleanup_indeterminate_no_retry",
                               str(result_path(operation)))
        raise
    append_receipt("wordpress_fixed_origin_file_cleanup_committed_and_verified",
                   str(result_path(operation)))
    print_json(result)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    diagnose = sub.add_parser(
        "diagnose-library",
        help="bounded authenticated Media Library reads only; no plan and no website write",
    )
    diagnose.set_defaults(func=command_diagnose_library)
    stage = sub.add_parser("stage", help="fixed read-only state and immutable 24-hour plan")
    stage.set_defaults(func=command_stage)
    commit = sub.add_parser("commit", help="one exact fixed operation; no retry")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except (CleanupError, UiLaneBusy) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
