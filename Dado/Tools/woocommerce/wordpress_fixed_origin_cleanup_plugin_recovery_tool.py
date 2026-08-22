#!/usr/bin/env python
"""One-use recovery for one exact inactive FRP Depot cleanup plugin.

Rachad Homsi separately commissioned this recovery tool on 2026-08-21 after the
permanently locked ``wordpress_fixed_origin_file_cleanup_tool`` operation left its
exact one-use plugin installed and inactive.  This tool cannot continue, retry or
repair that failed operation.  It preserves and verifies the failed plan, attempt,
activation-event and result evidence, then has only three commands:

``diagnose-delete-route``
    Read-only bounded inspection of the exact fixed plugin row's Delete-link
    structure.  It emits no raw URL, nonce, HTML, cookie or credential and can
    make no plan or website write.

``stage``
    Read-only WordPress/public verification.  If the exact plugin is already
    absent it reports ``VERIFIED_ALREADY_ABSENT`` and creates no plan.  If and only
    if the exact version 1.0.0 row is present, unambiguously inactive, has no update
    marker and exposes its exact Delete control, it writes a new immutable local
    24-hour recovery plan.  The four fixed public origins and four fixed media REST
    records must each remain exactly 404, and the authenticated Media Library walk
    must be complete and target-conflict-free.  Its observed total is recorded but
    is not hard-coded: unrelated media may make that count drift.

``commit --plan ... --approval APPROVED``
    Revalidates the immutable plan and old evidence, requires Rachad's later
    byte-exact unpadded uppercase approval, acquires the shared WordPress browser
    mutex, performs a fresh exact state/fingerprint comparison, and refuses every
    deterministic problem before its own one-attempt lock.  The lock is written
    immediately before clicking the exact fixed plugin Delete link.  The resulting
    exact WordPress confirmation form is validated without retaining or printing
    its nonce, then submitted exactly once.  Fresh readback must prove the plugin
    row absent and the fixed origin/media/complete-library contract still true.

The only website mutation reachable from this module is that one exact plugin
confirmation submission.  There is no activation, deactivation, installation,
replacement, upload, arbitrary plugin/path/URL, settings/content/user,
WooCommerce/product/order/customer, email, write-REST, AJAX, admin-post, shell or
WordPress-CLI route.  After the attempt lock, every failure is permanently
``INDETERMINATE_NO_RETRY``.  There is no retry, rollback or cleanup route.
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Dado.Tools.common.ui_lane_lock import UiLaneBusy, ui_browser_lock
# Reuse only the failed tool's proven, read-only complete Media Library reader.
# Do not import its AdminPage or any install/activation adapter.
from Dado.Tools.woocommerce.wordpress_fixed_origin_file_cleanup_tool import (
    StrictCleanupMediaReader,
)

TOOL_NAME = "wordpress_fixed_origin_cleanup_plugin_recovery_tool"
TOOL_VERSION = "1.0.4"
SCHEMA_VERSION = 1
OPERATION_SCHEMA_VERSION = 1
APPROVAL_WORD = "APPROVED"
PLAN_LIFETIME = timedelta(hours=24)
COMMIT_EXPIRY_MARGIN = timedelta(minutes=2)

ORIGIN = "https://frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
PLUGINS_URL = ORIGIN + "/wp-admin/plugins.php"
PLUGIN_NAME = "FRP Depot Fixed Four Origin File Cleanup"
PLUGIN_SLUG = "frpdepot-fixed-four-origin-file-cleanup"
PLUGIN_FILE = f"{PLUGIN_SLUG}/{PLUGIN_SLUG}.php"
PLUGIN_VERSION = "1.0.0"
ROW_SELECTOR = f'tr[data-plugin="{PLUGIN_FILE}"]:not(.plugin-update-tr)'
UPDATE_ROW_SELECTOR = f'tr.plugin-update-tr[data-plugin="{PLUGIN_FILE}"]'
ACTIVATE_SELECTOR = ".row-actions .activate a"
DEACTIVATE_SELECTOR = ".row-actions .deactivate a"
DELETE_SELECTOR = ".row-actions .delete a"
VERSION_PATTERN = re.compile(r"(?i)\bversion\s+([0-9][0-9A-Za-z.\-+_]*)")
NAV_TIMEOUT_MS = 45_000
ACTION_TIMEOUT_MS = 30_000

TARGETS: tuple[dict[str, Any], ...] = (
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
TARGET_IDS = tuple(item["attachment_id"] for item in TARGETS)

FAILED_TOOL_NAME = "wordpress_fixed_origin_file_cleanup_tool"
FAILED_TOOL_VERSION = "1.0.1"
FAILED_SCHEMA_VERSION = 2
FAILED_PLAN_SHA256 = "20f1b3ba6cd3cf53cfc0401d1e57365d92bec42edc789d40b1865a89e01f0dc1"
FAILED_OPERATION_SHA256 = "cf7cb909a71e09cf12e7ac9f8eb376817d8440781447660fcdad65483d81e9cc"
FAILED_PLAN_PATH = (ROOT / "Dado" / "20_Working"
                    / "wordpress_fixed_origin_file_cleanup_plans"
                    / "20260821T174818Z_fixed_origin_cleanup_20f1b3ba6cd3cf53.json")
FAILED_PLAN_FILE_SHA256 = "f2985731e56cde3e2c442e97426e64174315f9998358b4e40341ae573f610228"
FAILED_PLAN_BYTES = 8433

OLD_LOCAL_STATE = Path(os.environ.get(
    "LOCALAPPDATA", str(Path.home() / "AppData" / "Local")
)) / "FRPDepot-WordPress" / "fixed-four-origin-file-cleanup"
FAILED_ATTEMPT_PATH = OLD_LOCAL_STATE / "attempts" / f"{FAILED_OPERATION_SHA256}.json"
FAILED_ATTEMPT_SHA256 = "da2b2ce5e5707c1bae4e1c986137b61b5f7bf4299756164079940d25d4ae4a1d"
FAILED_ATTEMPT_BYTES = 339
FAILED_EVENT_PATH = (OLD_LOCAL_STATE / "events" / FAILED_OPERATION_SHA256
                     / "02-activation-attempted.json")
FAILED_EVENT_SHA256 = "f4d6a6766ffea11aedf4adf6835456055cbdbe56bb29afad49078547cf5e0442"
FAILED_EVENT_BYTES = 364
FAILED_RESULT_PATH = OLD_LOCAL_STATE / "results" / f"{FAILED_OPERATION_SHA256}.json"
FAILED_RESULT_SHA256 = "8a6d8d2405134f17bc7cde3e1edb1ef4b2e6674fd033c3de06e995c2407fbd5e"
FAILED_RESULT_BYTES = 771

PLAN_DIR = (ROOT / "Dado" / "20_Working"
            / "wordpress_fixed_origin_cleanup_plugin_recovery_plans")
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
LOCAL_STATE = Path(os.environ.get(
    "LOCALAPPDATA", str(Path.home() / "AppData" / "Local")
)) / "FRPDepot-WordPress" / "fixed-four-origin-cleanup-plugin-recovery"
REGISTRY_KEY = LOCAL_STATE / "stage-registry.key"
REGISTRY_DIR = LOCAL_STATE / "stages"
ATTEMPT_DIR = LOCAL_STATE / "attempts"
RESULT_DIR = LOCAL_STATE / "results"
EVENT_DIR = LOCAL_STATE / "events"
RUNTIME_TEMP = ROOT / "Dado" / "Temp" / "playwright-runtime"

PLUGIN_KEYS = frozenset({
    "present", "active", "version", "update_marker", "plugin_file",
    "delete_control_valid", "fingerprint",
})
STATE_KEYS = frozenset({
    "plugin", "public_files", "attachment_records", "media_catalog", "fingerprint",
})
MEDIA_CATALOG_KEYS = frozenset({
    "total", "enumerated", "pages", "rows_sha256", "target_conflicts",
})
PLAN_KEYS = frozenset({
    "schema_version", "tool", "tool_version", "origin", "created_utc", "expires_utc",
    "nonce", "operation_sha256", "plugin", "targets", "previous_failure_evidence",
    "before", "before_fingerprint", "after_expected", "write_sequence", "risk",
    "forbidden", "sha256",
})

WRITE_SEQUENCE = (
    "1. Acquire the shared WordPress browser mutex and fresh-read the complete fixed contract.",
    "2. Refuse drift or any non-exact plugin/media/origin state before the recovery attempt lock.",
    "3. Write the recovery one-attempt lock immediately before clicking only the exact fixed plugin Delete link.",
    "4. Validate the exact WordPress delete confirmation URL/form without retaining or printing its nonce.",
    "5. Submit that exact deletion confirmation once; no retry, rollback or cleanup is available.",
    "6. Verify the exact plugin row absent, four fixed origin URLs 404, four media REST IDs 404, and a complete target-free Media Library.",
)
RISK = (
    "ONE ATTEMPT, NO RETRY, NO ROLLBACK, NO CLEANUP. The Delete link navigation is "
    "inside the permanent attempt. Any failure from that click onward is "
    "INDETERMINATE_NO_RETRY. The only possible website mutation is one confirmation "
    "submission deleting exactly the inactive FRP Depot Fixed Four Origin File Cleanup "
    "plugin version 1.0.0. It cannot activate, deactivate, reinstall or replace it. "
    "The authenticated Media Library total is observed rather than hard-coded because "
    "unrelated media can drift; completeness and zero fixed-target conflicts are mandatory."
)
FORBIDDEN = (
    "activation", "deactivation", "plugin installation", "plugin replacement",
    "plugin upload", "arbitrary plugin", "arbitrary path", "arbitrary URL",
    "settings/content/user mutation", "WooCommerce/product/order/customer mutation",
    "email", "write REST", "AJAX", "admin-post", "shell/WordPress CLI",
    "retry", "rollback", "cleanup after failure", "unrelated plugin deletion",
)


class RecoveryError(RuntimeError):
    """Deterministic refusal or bounded recovery failure."""


class IndeterminateError(RecoveryError):
    """The locked deletion did not complete every required readback."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest_for(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def require_approval(value: Any) -> None:
    if not isinstance(value, str) or value != APPROVAL_WORD:
        raise RecoveryError("Approval must be the exact unpadded uppercase word APPROVED.")


def is_reparse(path: Path) -> bool:
    try:
        value = path.lstat()
    except OSError:
        return True
    return stat.S_ISLNK(value.st_mode) or bool(
        getattr(value, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _strict_fixed_bytes(path: Path, expected_sha256: str, expected_bytes: int,
                        description: str) -> bytes:
    """Read one fixed evidence file without following aliases or changing it."""
    if is_reparse(path):
        raise RecoveryError(f"REFUSED: fixed {description} is missing or aliased.")
    try:
        resolved = path.resolve(strict=True)
        absolute = Path(os.path.abspath(path))
        if (os.path.normcase(str(resolved)) != os.path.normcase(str(absolute))
                or not resolved.is_file()):
            raise OSError("not the fixed regular path")
        with resolved.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if getattr(before, "st_nlink", 1) != 1:
                raise RecoveryError(f"REFUSED: fixed {description} is aliased.")
            raw = handle.read()
            after = os.fstat(handle.fileno())
        named = resolved.stat()
    except RecoveryError:
        raise
    except OSError as exc:
        raise RecoveryError(f"REFUSED: fixed {description} is missing or unreadable.") from exc
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
        getattr(item, "st_nlink", 1),
    )
    if identity(before) != identity(after) or identity(after) != identity(named):
        raise RecoveryError(f"REFUSED: fixed {description} changed while read.")
    if len(raw) != expected_bytes or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RecoveryError(f"REFUSED: fixed {description} hash/size changed.")
    return raw


def _strict_local_bytes(path: Path, description: str) -> bytes:
    """Read authenticated local lifecycle evidence without a pre-read or alias."""
    if is_reparse(path):
        raise RecoveryError(f"REFUSED: {description} is missing or aliased.")
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise OSError("not a regular file")
        with resolved.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if getattr(before, "st_nlink", 1) != 1:
                raise RecoveryError(f"REFUSED: {description} is aliased.")
            raw = handle.read()
            after = os.fstat(handle.fileno())
        named = resolved.stat()
    except RecoveryError:
        raise
    except OSError as exc:
        raise RecoveryError(f"REFUSED: {description} is missing or unreadable.") from exc
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
        getattr(item, "st_nlink", 1),
    )
    if identity(before) != identity(after) or identity(after) != identity(named):
        raise RecoveryError(f"REFUSED: {description} changed while read.")
    return raw


def _json_evidence(path: Path, expected_sha256: str, expected_bytes: int,
                   description: str) -> dict[str, Any]:
    raw = _strict_fixed_bytes(path, expected_sha256, expected_bytes, description)
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"REFUSED: fixed {description} JSON is unreadable.") from exc
    if not isinstance(value, dict):
        raise RecoveryError(f"REFUSED: fixed {description} JSON is not an object.")
    return value


def validate_previous_failure_evidence() -> dict[str, Any]:
    """Prove the permanently locked failed operation without mutating its files."""
    failed_plan = _json_evidence(
        FAILED_PLAN_PATH, FAILED_PLAN_FILE_SHA256, FAILED_PLAN_BYTES, "failed plan"
    )
    plan_core = dict(failed_plan)
    saved_plan_sha = str(plan_core.pop("sha256", ""))
    if (saved_plan_sha != FAILED_PLAN_SHA256
            or not secrets.compare_digest(saved_plan_sha, digest_for(plan_core))
            or failed_plan.get("schema_version") != FAILED_SCHEMA_VERSION
            or failed_plan.get("tool") != FAILED_TOOL_NAME
            or failed_plan.get("tool_version") != FAILED_TOOL_VERSION
            or failed_plan.get("operation_sha256") != FAILED_OPERATION_SHA256
            or failed_plan.get("plugin") != {
                "name": PLUGIN_NAME, "slug": PLUGIN_SLUG,
                "file": PLUGIN_FILE, "version": PLUGIN_VERSION,
            }):
        raise RecoveryError("REFUSED: failed immutable plan identity is not exact.")

    attempt = _json_evidence(
        FAILED_ATTEMPT_PATH, FAILED_ATTEMPT_SHA256, FAILED_ATTEMPT_BYTES,
        "failed operation attempt",
    )
    if (set(attempt) != {"schema", "tool", "operation_sha256", "plan_sha256",
                        "locked_utc", "status"}
            or attempt.get("schema") != 1 or attempt.get("tool") != FAILED_TOOL_NAME
            or attempt.get("operation_sha256") != FAILED_OPERATION_SHA256
            or attempt.get("plan_sha256") != FAILED_PLAN_SHA256
            or attempt.get("status") != "attempt_started_no_retry"):
        raise RecoveryError("REFUSED: failed operation attempt evidence is not exact.")

    activation_event = _json_evidence(
        FAILED_EVENT_PATH, FAILED_EVENT_SHA256, FAILED_EVENT_BYTES,
        "failed operation activation event",
    )
    if (set(activation_event) != {"schema", "tool", "operation_sha256", "plan_sha256",
                                 "event", "utc", "status"}
            or activation_event.get("schema") != 1
            or activation_event.get("tool") != FAILED_TOOL_NAME
            or activation_event.get("operation_sha256") != FAILED_OPERATION_SHA256
            or activation_event.get("plan_sha256") != FAILED_PLAN_SHA256
            or activation_event.get("event") != "02-activation-attempted"
            or activation_event.get("status") != "attempted_no_retry"):
        raise RecoveryError("REFUSED: failed activation-attempt evidence is not exact.")

    result = _json_evidence(
        FAILED_RESULT_PATH, FAILED_RESULT_SHA256, FAILED_RESULT_BYTES,
        "failed permanent result",
    )
    if (set(result) != {"schema", "tool", "operation_sha256", "plan_sha256", "status",
                       "failed_utc", "error_type", "error", "completed_steps",
                       "earlier_effects_remain", "partial_origin_file_deletion_possible",
                       "plugin_may_remain_installed_inactive", "no_retry", "rollback",
                       "emails", "woocommerce_writes", "zoho_writes"}
            or result.get("schema") != 1 or result.get("tool") != FAILED_TOOL_NAME
            or result.get("operation_sha256") != FAILED_OPERATION_SHA256
            or result.get("plan_sha256") != FAILED_PLAN_SHA256
            or result.get("status") != "INDETERMINATE_NO_RETRY"
            or result.get("completed_steps") != ["plugin_installed_inactive_verified"]
            or result.get("no_retry") is not True or result.get("rollback") is not False):
        raise RecoveryError("REFUSED: failed permanent result evidence is not exact.")

    # Store only fixed paths, hashes, sizes and non-secret status/timestamp evidence.
    # In particular, the failed plan's registry nonce is never copied here.
    return {
        "failed_tool": {"name": FAILED_TOOL_NAME, "version": FAILED_TOOL_VERSION,
                        "schema_version": FAILED_SCHEMA_VERSION},
        "failed_operation_sha256": FAILED_OPERATION_SHA256,
        "failed_plan": {"path": str(FAILED_PLAN_PATH), "internal_sha256": FAILED_PLAN_SHA256,
                        "file_sha256": FAILED_PLAN_FILE_SHA256, "bytes": FAILED_PLAN_BYTES},
        "attempt": {"path": str(FAILED_ATTEMPT_PATH), "sha256": FAILED_ATTEMPT_SHA256,
                    "bytes": FAILED_ATTEMPT_BYTES, "status": attempt["status"],
                    "locked_utc": attempt["locked_utc"]},
        "activation_event": {"path": str(FAILED_EVENT_PATH), "sha256": FAILED_EVENT_SHA256,
                             "bytes": FAILED_EVENT_BYTES, "status": activation_event["status"],
                             "utc": activation_event["utc"]},
        "result": {"path": str(FAILED_RESULT_PATH), "sha256": FAILED_RESULT_SHA256,
                   "bytes": FAILED_RESULT_BYTES, "status": result["status"],
                   "failed_utc": result["failed_utc"],
                   "completed_steps": result["completed_steps"]},
    }


def plugin_fingerprint(plugin: dict[str, Any]) -> str:
    return digest_for({key: plugin[key] for key in (
        "present", "active", "version", "update_marker", "plugin_file",
        "delete_control_valid",
    )})


def project_plugin(present: bool, active: bool | None, version: str,
                   update_marker: bool, delete_control_valid: bool) -> dict[str, Any]:
    value = {
        "present": bool(present), "active": active, "version": str(version),
        "update_marker": bool(update_marker), "plugin_file": PLUGIN_FILE,
        "delete_control_valid": bool(delete_control_valid),
    }
    value["fingerprint"] = plugin_fingerprint(value)
    return value


def expected_plugin(phase: str) -> dict[str, Any]:
    if phase == "removal":
        return project_plugin(True, False, PLUGIN_VERSION, False, True)
    if phase == "final":
        return project_plugin(False, None, "", False, False)
    raise RecoveryError("REFUSED: recovery eligibility phase is not fixed.")


def state_fingerprint(state: dict[str, Any]) -> str:
    return digest_for({key: state[key] for key in (
        "plugin", "public_files", "attachment_records", "media_catalog",
    )})


def seal_state(plugin: dict[str, Any], public_files: list[dict[str, Any]],
               attachment_records: list[dict[str, Any]],
               media_catalog: dict[str, Any]) -> dict[str, Any]:
    state = {"plugin": plugin, "public_files": public_files,
             "attachment_records": attachment_records,
             "media_catalog": media_catalog}
    state["fingerprint"] = state_fingerprint(state)
    return state


def assert_recovery_eligible(state: dict[str, Any], phase: str) -> dict[str, Any]:
    """The one normalized predicate for stage, fresh preflight and both adapters."""
    if phase not in {"removal", "final"} or not isinstance(state, dict) \
            or set(state) != STATE_KEYS:
        raise RecoveryError("REFUSED: fixed recovery state projection is invalid.")
    if (not isinstance(state.get("plugin"), dict)
            or set(state["plugin"]) != PLUGIN_KEYS
            or state["plugin"].get("fingerprint") != plugin_fingerprint(state["plugin"])
            or state["plugin"] != expected_plugin(phase)):
        raise RecoveryError(
            "REFUSED: plugin must be exactly version 1.0.0, unambiguously inactive, "
            "update-free and expose only its exact Delete control."
            if phase == "removal" else
            "REFUSED: exact fixed cleanup plugin is not authoritatively absent."
        )
    if state.get("fingerprint") != state_fingerprint(state):
        raise RecoveryError("REFUSED: fixed recovery state fingerprint failed.")

    public = state.get("public_files")
    records = state.get("attachment_records")
    catalog = state.get("media_catalog")
    if not isinstance(public, list) or not isinstance(records, list) \
            or len(public) != len(TARGETS) or len(records) != len(TARGETS):
        raise RecoveryError("REFUSED: four fixed origin/media observations are incomplete.")
    for target, observed, record in zip(TARGETS, public, records):
        public_identity = {
            "position": target["position"], "upload_path": target["upload_path"],
            "url": target["url"], "bytes": target["bytes"], "sha256": target["sha256"],
        }
        if (not isinstance(observed, dict)
                or set(observed) != set(public_identity) | {"status", "redirected"}
                or {key: observed.get(key) for key in public_identity} != public_identity
                or observed.get("status") != 404 or observed.get("redirected") is not False):
            raise RecoveryError("REFUSED: every exact fixed origin URL must independently remain 404.")
        if record != {"attachment_id": target["attachment_id"],
                      "status": 404, "redirected": False}:
            raise RecoveryError("REFUSED: every exact fixed media REST ID must remain 404.")

    if (not isinstance(catalog, dict) or set(catalog) != MEDIA_CATALOG_KEYS
            or not isinstance(catalog.get("total"), int)
            or isinstance(catalog.get("total"), bool) or catalog["total"] < 0
            or not isinstance(catalog.get("enumerated"), int)
            or isinstance(catalog.get("enumerated"), bool)
            or catalog["enumerated"] != catalog["total"]
            or not isinstance(catalog.get("pages"), int)
            or isinstance(catalog.get("pages"), bool) or catalog["pages"] < 1
            or not isinstance(catalog.get("rows_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", catalog["rows_sha256"])
            or catalog.get("target_conflicts") != []):
        raise RecoveryError(
            "REFUSED: authenticated Media Library walk must be complete and fixed-target-free."
        )
    return state


READ_ABSENCE_SCRIPT = r"""
async ({targets, attachmentIds, cacheNonce}) => {
  const noCache = {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'};
  const cacheBust = (raw, suffix) => {
    const url = new URL(raw);
    url.searchParams.set('_frpd_recovery_read', cacheNonce + suffix);
    return url.href;
  };
  const getStatus = async (raw, suffix) => {
    const response = await fetch(cacheBust(raw, suffix), {
      method: 'GET', credentials: 'omit', cache: 'no-store', redirect: 'manual',
      headers: noCache, signal: AbortSignal.timeout(15000)
    });
    return {status: response.status, redirected: response.redirected};
  };
  const publicFiles = [];
  for (const target of targets) {
    const observed = await getStatus(target.url, '-origin-' + target.position);
    publicFiles.push({position: target.position, upload_path: target.upload_path,
      url: target.url, bytes: target.bytes, sha256: target.sha256, ...observed});
  }
  const attachmentRecords = [];
  for (const id of attachmentIds) {
    const observed = await getStatus(location.origin + '/wp-json/wp/v2/media/' + id,
                                     '-media-' + id);
    attachmentRecords.push({attachment_id: id, ...observed});
  }
  return {public_files: publicFiles, attachment_records: attachmentRecords};
}
"""


def _exact_origin_query(url: str) -> tuple[str, list[tuple[str, str]]]:
    parsed = urlsplit(str(url or ""))
    try:
        port = parsed.port
    except ValueError as exc:
        raise RecoveryError("REFUSED: WordPress URL carries an invalid port.") from exc
    if (parsed.scheme != "https" or parsed.hostname != "frpdepots.com"
            or port not in (None, 443) or parsed.username or parsed.password
            or parsed.fragment):
        raise RecoveryError("REFUSED: browser left the exact FRP Depot HTTPS origin.")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    keys = [key for key, _value in pairs]
    if len(keys) != len(set(keys)):
        raise RecoveryError("REFUSED: WordPress route repeats a query key.")
    return parsed.path, pairs


def assert_plugins_url(url: str, mode: str) -> None:
    path, pairs = _exact_origin_query(url)
    values = dict(pairs)
    if mode == "list":
        valid = path == "/wp-admin/plugins.php" and not pairs
    elif mode == "deleted_result":
        valid = path == "/wp-admin/plugins.php" and values == {
            "deleted": "1", "plugin_status": "all", "paged": "1", "s": "",
        }
    else:
        raise RecoveryError("REFUSED: fixed plugins URL mode is invalid.")
    if not valid:
        raise RecoveryError("REFUSED: WordPress plugins route is outside the fixed contract.")


def _delete_url_projection(url: str) -> tuple[str, str]:
    absolute = urljoin(PLUGINS_URL, str(url or ""))
    path, pairs = _exact_origin_query(absolute)
    values = dict(pairs)
    valid = (
        path == "/wp-admin/plugins.php"
        and set(values) == {"action", "checked[0]", "plugin_status", "paged", "s", "_wpnonce"}
        and values.get("action") == "delete-selected"
        and values.get("checked[0]") == PLUGIN_FILE
        and values.get("plugin_status") == "all"
        and values.get("paged") == "1"
        and values.get("s") == ""
        and bool(re.fullmatch(r"[A-Za-z0-9_-]{8,64}", values.get("_wpnonce", "")))
    )
    if not valid:
        raise RecoveryError("REFUSED: fixed plugin Delete URL is ambiguous or outside scope.")
    # Private transient return only.  Callers compare it and then discard it; it is
    # never placed in a state, plan, event, result, receipt or printed error.
    return absolute, values["_wpnonce"]


def assert_delete_action_url(url: str) -> None:
    _delete_url_projection(url)


def sanitized_delete_url_shape(url: Any) -> dict[str, Any]:
    """Bounded Delete-link diagnostics with no raw URL, values or nonce."""
    raw = str(url or "")
    absolute = urljoin(PLUGINS_URL, raw)
    try:
        parsed = urlsplit(absolute)
        port = parsed.port
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError):
        return {"malformed": True, "same_origin": False, "path": "",
                "query_keys": [], "duplicate_keys": True, "fragment": False,
                "credentials": False, "action_exact": False,
                "checked_exact_once": False, "plugin_status": "other",
                "paged_is_one": False, "search_is_empty": False,
                "nonce_present": False, "nonce_length": 0,
                "nonce_charset_valid": False}
    values: dict[str, list[str]] = {}
    for key, value in pairs:
        values.setdefault(key, []).append(value)
    origin = urlsplit(ORIGIN)
    nonce_values = values.get("_wpnonce", [])
    nonce = nonce_values[0] if len(nonce_values) == 1 else ""
    statuses = values.get("plugin_status", [])
    status = statuses[0] if len(statuses) == 1 and statuses[0] in {
        "all", "active", "inactive", "recent", "mustuse", "dropins",
        "paused", "upgrade", "auto-update-enabled", "auto-update-disabled",
    } else "other"
    return {
        "malformed": False,
        "same_origin": (
            parsed.scheme == origin.scheme and parsed.hostname == origin.hostname
            and port == origin.port and not parsed.username and not parsed.password
        ),
        "path": parsed.path,
        "query_keys": sorted(values),
        "duplicate_keys": any(len(items) != 1 for items in values.values()),
        "fragment": bool(parsed.fragment),
        "credentials": bool(parsed.username or parsed.password),
        "action_exact": values.get("action") == ["delete-selected"],
        "checked_exact_once": values.get("checked[0]") == [PLUGIN_FILE],
        "plugin_status": status,
        "paged_is_one": values.get("paged") == ["1"],
        "search_is_empty": values.get("s") == [""],
        "nonce_present": len(nonce_values) == 1,
        "nonce_length": len(nonce),
        "nonce_charset_valid": bool(re.fullmatch(r"[A-Za-z0-9_-]{8,64}", nonce)),
    }


def _exact_confirmation_submit(form: Any, current_url: str) -> Any:
    current_absolute, query_nonce = _delete_url_projection(current_url)
    if str(form.get_attribute("method") or "").casefold() != "post":
        raise RecoveryError("REFUSED: fixed plugin confirmation form method is not exact.")
    form_absolute, form_query_nonce = _delete_url_projection(
        urljoin(current_absolute, str(form.get_attribute("action") or ""))
    )
    if form_absolute != current_absolute or not hmac.compare_digest(form_query_nonce, query_nonce):
        raise RecoveryError("REFUSED: fixed plugin confirmation form action is not exact.")

    hidden = form.query_selector_all('input[type="hidden"]')
    by_name: dict[str, list[Any]] = {}
    for control in hidden:
        name = str(control.get_attribute("name") or "")
        by_name.setdefault(name, []).append(control)
    expected_names = {"checked[]", "verify-delete", "action", "_wpnonce", "_wp_http_referer"}
    if set(by_name) != expected_names or any(len(items) != 1 for items in by_name.values()):
        raise RecoveryError("REFUSED: fixed plugin confirmation hidden fields are not exact.")
    value = lambda name: str(by_name[name][0].get_attribute("value") or "")
    if (value("checked[]") != PLUGIN_FILE or value("verify-delete") != "1"
            or value("action") != "delete-selected"
            or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", value("_wpnonce"))
            or not hmac.compare_digest(value("_wpnonce"), query_nonce)):
        raise RecoveryError("REFUSED: fixed plugin confirmation values are not exact.")
    referer_absolute, referer_nonce = _delete_url_projection(
        urljoin(current_absolute, value("_wp_http_referer"))
    )
    if referer_absolute != current_absolute or not hmac.compare_digest(referer_nonce, query_nonce):
        raise RecoveryError("REFUSED: fixed plugin confirmation referer is not exact.")
    submits = form.query_selector_all('input#submit[type="submit"]')
    if len(submits) != 1:
        raise RecoveryError("REFUSED: fixed plugin confirmation submit is unavailable.")
    return submits[0]


class AdminPage:
    """Read-only fixed verifier plus exactly one Delete-link/form path."""

    def __init__(self, page: Any):
        self.page = page
        self._media_reader = StrictCleanupMediaReader(page)

    def goto_plugins(self) -> None:
        assert_plugins_url(PLUGINS_URL, "list")
        self.page.goto(PLUGINS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_plugins_url(str(self.page.url), "list")

    def rows(self) -> list[Any]:
        rows = self.page.query_selector_all(ROW_SELECTOR)
        if len(rows) > 1:
            raise RecoveryError("REFUSED: exact fixed cleanup plugin row is ambiguous.")
        return rows

    def read_row(self) -> dict[str, Any]:
        rows = self.rows()
        updates = self.page.query_selector_all(UPDATE_ROW_SELECTOR)
        if len(updates) > 1 or (not rows and updates):
            raise RecoveryError("REFUSED: exact fixed cleanup plugin/update row is ambiguous.")
        if not rows:
            return expected_plugin("final")
        row = rows[0]
        tokens = set(str(row.get_attribute("class") or "").split())
        active, inactive = "active" in tokens, "inactive" in tokens
        if active == inactive:
            raise RecoveryError("REFUSED: exact fixed cleanup plugin activity is ambiguous.")
        activates = row.query_selector_all(ACTIVATE_SELECTOR)
        deactivates = row.query_selector_all(DEACTIVATE_SELECTOR)
        deletes = row.query_selector_all(DELETE_SELECTOR)
        if active:
            if activates or len(deactivates) != 1 or deletes:
                raise RecoveryError("REFUSED: active fixed cleanup row controls are ambiguous.")
            delete_valid = False
        else:
            if len(activates) != 1 or deactivates or len(deletes) != 1:
                raise RecoveryError("REFUSED: inactive fixed cleanup row controls are ambiguous.")
            assert_delete_action_url(str(deletes[0].get_attribute("href") or ""))
            delete_valid = True
        text = str(row.inner_text() or "")
        versions = VERSION_PATTERN.findall(text)
        if PLUGIN_NAME not in text or len(versions) != 1:
            raise RecoveryError("REFUSED: exact fixed cleanup plugin identity/version is unreadable.")
        return project_plugin(True, active, versions[0], bool(updates) or "update" in tokens,
                              delete_valid)

    def read_origin_absence(self) -> dict[str, Any]:
        try:
            value = self.page.evaluate(READ_ABSENCE_SCRIPT, {
                "targets": list(TARGETS), "attachmentIds": list(TARGET_IDS),
                "cacheNonce": secrets.token_hex(16),
            })
        except Exception as exc:
            raise RecoveryError("REFUSED: bounded fixed origin/media reads failed.") from exc
        if not isinstance(value, dict) or set(value) != {"public_files", "attachment_records"}:
            raise RecoveryError("REFUSED: fixed origin/media read projection is invalid.")
        return value

    def read_media_catalog(self) -> dict[str, Any]:
        try:
            snapshot = self._media_reader.enumerate_library()
        except Exception as exc:
            raise RecoveryError("REFUSED: authenticated complete Media Library walk failed.") from exc
        if not isinstance(snapshot, dict):
            raise RecoveryError("REFUSED: authenticated Media Library snapshot is malformed.")
        rows = snapshot.get("rows")
        total = snapshot.get("total")
        pages = snapshot.get("pages")
        if (snapshot.get("complete") is not True or not isinstance(rows, list)
                or not isinstance(total, int) or isinstance(total, bool) or total < 0
                or not isinstance(pages, int) or isinstance(pages, bool) or pages < 1
                or len(rows) != total):
            raise RecoveryError("REFUSED: authenticated Media Library walk is incomplete.")
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"id", "filename", "stem"}
                    or not isinstance(row.get("id"), int) or isinstance(row.get("id"), bool)
                    or row["id"] <= 0 or not isinstance(row.get("filename"), str)
                    or not row["filename"].strip() or not isinstance(row.get("stem"), str)):
                raise RecoveryError("REFUSED: authenticated Media Library row is malformed.")
            normalized.append({"id": row["id"], "filename": row["filename"]})
        normalized.sort(key=lambda item: item["id"])
        if len({item["id"] for item in normalized}) != len(normalized):
            raise RecoveryError("REFUSED: authenticated Media Library identity is duplicated.")
        target_names = {Path(target["upload_path"]).name for target in TARGETS}
        conflicts = [item for item in normalized
                     if item["id"] in TARGET_IDS or item["filename"] in target_names]
        return {"total": total, "enumerated": len(normalized), "pages": pages,
                "rows_sha256": digest_for(normalized), "target_conflicts": conflicts}

    def preflight_delete_control(self, eligibility_state: dict[str, Any]) -> Any:
        """Final deterministic control lookup; performs no click."""
        assert_recovery_eligible(eligibility_state, "removal")
        self.goto_plugins()
        if self.read_row() != eligibility_state["plugin"]:
            raise RecoveryError("REFUSED: exact fixed cleanup plugin row drifted before lock.")
        links = self.rows()[0].query_selector_all(DELETE_SELECTOR)
        if len(links) != 1:
            raise RecoveryError("REFUSED: exact fixed cleanup plugin Delete control is unavailable.")
        assert_delete_action_url(str(links[0].get_attribute("href") or ""))
        return links[0]

    def prepare_delete(self, link: Any, eligibility_state: dict[str, Any]) -> Any:
        """Guard and click the exact Delete link, then return the exact form submit."""
        assert_recovery_eligible(eligibility_state, "removal")
        assert_delete_action_url(str(link.get_attribute("href") or ""))
        link.click(timeout=ACTION_TIMEOUT_MS)
        self.page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        current = str(self.page.url)
        assert_delete_action_url(current)
        candidates = []
        for form in self.page.query_selector_all("form"):
            try:
                candidates.append(_exact_confirmation_submit(form, current))
            except RecoveryError:
                continue
        if len(candidates) != 1:
            raise RecoveryError("REFUSED: exact fixed plugin Delete confirmation form is unavailable.")
        return candidates[0]

    def execute_delete(self, submit: Any, eligibility_state: dict[str, Any]) -> dict[str, Any]:
        """Guard and submit the one exact confirmation once; never retry."""
        assert_recovery_eligible(eligibility_state, "removal")
        submit.click(timeout=ACTION_TIMEOUT_MS)
        self.page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_plugins_url(str(self.page.url), "deleted_result")
        self.goto_plugins()
        absent = self.read_row()
        if absent != expected_plugin("final"):
            raise IndeterminateError("Exact fixed cleanup plugin did not read back absent.")
        return absent


def collect_state(admin: AdminPage) -> dict[str, Any]:
    admin.goto_plugins()
    plugin = admin.read_row()
    reads = admin.read_origin_absence()
    catalog = admin.read_media_catalog()
    return seal_state(plugin, reads["public_files"], reads["attachment_records"], catalog)


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
    with ui_browser_lock("wordpress", purpose=purpose), sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=15_000)
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            raise RecoveryError("Authenticated WordPress browser is unavailable.") from exc
        pages = [page for context in browser.contexts for page in context.pages]
        page = next((item for item in pages if str(item.url).startswith(ORIGIN + "/wp-admin")), None)
        if page is None:
            raise RecoveryError("Authenticated WordPress browser has no FRP Depot admin page.")
        yield AdminPage(page)


def registry_key(create: bool) -> bytes:
    REGISTRY_KEY.parent.mkdir(parents=True, exist_ok=True) if create else None
    if create and not REGISTRY_KEY.exists():
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
        raise RecoveryError("REFUSED: recovery stage-registry key is missing or aliased.")
    key = REGISTRY_KEY.read_bytes()
    if len(key) != 32:
        raise RecoveryError("REFUSED: recovery stage-registry key is invalid.")
    return key


def registry_mac(core: dict[str, Any], create_key: bool) -> str:
    return hmac.new(registry_key(create_key), canonical(core).encode("ascii"),
                    hashlib.sha256).hexdigest()


def exclusive_json(path: Path, value: dict[str, Any]) -> None:
    """Create immutable local evidence once; a partial write remains fail-closed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise RecoveryError("REFUSED: immutable recovery evidence exists; no replay.") from exc
    with os.fdopen(descriptor, "wb") as handle:
        if handle.write(payload) != len(payload):
            raise OSError("immutable recovery evidence write was short")
        handle.flush()
        os.fsync(handle.fileno())


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"ts": utc_now().isoformat(), "action": action,
                                 "evidence": evidence}, ensure_ascii=True) + "\n")


def operation_sha() -> str:
    return digest_for({
        "operation_schema_version": OPERATION_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "origin": ORIGIN,
        "plugin": {"name": PLUGIN_NAME, "slug": PLUGIN_SLUG,
                   "file": PLUGIN_FILE, "version": PLUGIN_VERSION},
        "failed_plan_sha256": FAILED_PLAN_SHA256,
        "failed_operation_sha256": FAILED_OPERATION_SHA256,
        "allowed_side_effects": ["click_exact_fixed_plugin_delete_link",
                                 "submit_exact_fixed_plugin_delete_confirmation_once"],
        "verification": ["plugin_row_absent", "four_origin_urls_404",
                         "four_media_rest_ids_404", "complete_target_free_media_library"],
    })


def plan_path(created: datetime, plan_sha: str) -> Path:
    return (PLAN_DIR / (
        f"{created.strftime('%Y%m%dT%H%M%SZ')}_fixed_cleanup_plugin_recovery_"
        f"{plan_sha[:16]}.json"
    )).resolve()


def stage_registry_path(plan_sha: str) -> Path:
    return REGISTRY_DIR / f"{plan_sha}.json"


def attempt_path(operation: str) -> Path:
    return ATTEMPT_DIR / f"{operation}.json"


def result_path(operation: str) -> Path:
    return RESULT_DIR / f"{operation}.json"


def event_path(operation: str, name: str) -> Path:
    if name not in {"01-delete-link-clicked", "02-delete-confirmation-submit-attempted"}:
        raise RecoveryError("REFUSED: recovery event name is invalid.")
    return EVENT_DIR / operation / f"{name}.json"


def recovery_locked(operation: str) -> bool:
    return (attempt_path(operation).exists() or result_path(operation).exists()
            or (EVENT_DIR / operation).exists())


def after_expected() -> dict[str, Any]:
    return {
        "plugin": expected_plugin("final"),
        "origin_http_status": 404,
        "attachment_record_http_status": 404,
        "attachment_ids": list(TARGET_IDS),
        "media_library": {"complete": True, "target_conflicts": [],
                          "total_is_observed_not_fixed": True},
    }


def stage_plan(before: dict[str, Any], previous_evidence: dict[str, Any]) \
        -> tuple[Path, dict[str, Any]]:
    assert_recovery_eligible(before, "removal")
    if previous_evidence != validate_previous_failure_evidence():
        raise RecoveryError("REFUSED: prior locked evidence changed before staging.")
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION, "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION, "origin": ORIGIN,
        "created_utc": created.isoformat(),
        "expires_utc": (created + PLAN_LIFETIME).isoformat(),
        "nonce": secrets.token_hex(16), "operation_sha256": operation_sha(),
        "plugin": {"name": PLUGIN_NAME, "slug": PLUGIN_SLUG,
                   "file": PLUGIN_FILE, "version": PLUGIN_VERSION},
        "targets": list(TARGETS),
        "previous_failure_evidence": previous_evidence,
        "before": before, "before_fingerprint": before["fingerprint"],
        "after_expected": after_expected(), "write_sequence": list(WRITE_SEQUENCE),
        "risk": RISK, "forbidden": list(FORBIDDEN),
    }
    plan = {**core, "sha256": digest_for(core)}
    path = plan_path(created, plan["sha256"])
    exclusive_json(path, plan)
    raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    registry_core = {
        "schema_version": SCHEMA_VERSION, "tool_version": TOOL_VERSION,
        "plan_sha256": plan["sha256"], "plan_file_sha256": raw_hash,
        "plan_path": str(path), "operation_sha256": operation_sha(),
        "nonce": plan["nonce"], "created_utc": plan["created_utc"],
        "expires_utc": plan["expires_utc"],
    }
    exclusive_json(stage_registry_path(plan["sha256"]), {
        **registry_core, "hmac_sha256": registry_mac(registry_core, create_key=True),
    })
    return path, plan


def fixed_plan_path(raw: str) -> Path:
    candidate = Path(raw)
    if is_reparse(candidate):
        raise RecoveryError("REFUSED: recovery plan path is missing or aliased.")
    try:
        path = candidate.resolve(strict=True)
    except OSError as exc:
        raise RecoveryError("REFUSED: recovery plan path is missing or aliased.") from exc
    absolute = Path(os.path.abspath(candidate))
    if (os.path.normcase(str(path)) != os.path.normcase(str(absolute))
            or path.parent != PLAN_DIR.resolve() or path.suffix.lower() != ".json"
            or not path.is_file() or getattr(path.stat(), "st_nlink", 1) != 1):
        raise RecoveryError("REFUSED: plan is not a canonical regular file in the fixed recovery folder.")
    return path


def load_plan(raw_path: str) -> tuple[Path, dict[str, Any]]:
    path = fixed_plan_path(raw_path)
    raw = path.read_bytes()
    try:
        plan = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryError("REFUSED: recovery plan is unreadable.") from exc
    if not isinstance(plan, dict) or set(plan) != PLAN_KEYS:
        raise RecoveryError("REFUSED: recovery plan schema is not exact.")
    core = dict(plan)
    saved = str(core.pop("sha256", ""))
    if not secrets.compare_digest(saved, digest_for(core)):
        raise RecoveryError("REFUSED: recovery plan hash failed.")
    fixed_plugin = {"name": PLUGIN_NAME, "slug": PLUGIN_SLUG,
                    "file": PLUGIN_FILE, "version": PLUGIN_VERSION}
    current_evidence = validate_previous_failure_evidence()
    if (plan["schema_version"] != SCHEMA_VERSION or plan["tool"] != TOOL_NAME
            or plan["tool_version"] != TOOL_VERSION or plan["origin"] != ORIGIN
            or plan["plugin"] != fixed_plugin or plan["targets"] != list(TARGETS)
            or plan["previous_failure_evidence"] != current_evidence
            or plan["operation_sha256"] != operation_sha()
            or plan["after_expected"] != after_expected()
            or plan["write_sequence"] != list(WRITE_SEQUENCE)
            or plan["risk"] != RISK or plan["forbidden"] != list(FORBIDDEN)
            or plan["before_fingerprint"] != plan["before"].get("fingerprint")):
        raise RecoveryError("REFUSED: immutable fixed recovery identity changed.")
    assert_recovery_eligible(plan["before"], "removal")
    created = datetime.fromisoformat(plan["created_utc"])
    expires = datetime.fromisoformat(plan["expires_utc"])
    if (created.tzinfo is None or expires.tzinfo is None
            or expires - created != PLAN_LIFETIME or utc_now() > expires
            or path.name != plan_path(created.astimezone(timezone.utc), saved).name):
        raise RecoveryError("REFUSED: recovery plan lifetime, expiry or filename is invalid.")
    registry_raw = _strict_local_bytes(
        stage_registry_path(saved), "recovery stage registry"
    )
    try:
        registry = json.loads(registry_raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryError("REFUSED: recovery stage registry is unreadable.") from exc
    registry_core = {
        "schema_version": SCHEMA_VERSION, "tool_version": TOOL_VERSION,
        "plan_sha256": saved, "plan_file_sha256": hashlib.sha256(raw).hexdigest(),
        "plan_path": str(path), "operation_sha256": operation_sha(),
        "nonce": plan["nonce"], "created_utc": plan["created_utc"],
        "expires_utc": plan["expires_utc"],
    }
    if (not isinstance(registry, dict)
            or set(registry) != set(registry_core) | {"hmac_sha256"}
            or {key: registry.get(key) for key in registry_core} != registry_core
            or not isinstance(registry.get("hmac_sha256"), str)
            or not hmac.compare_digest(registry["hmac_sha256"],
                                       registry_mac(registry_core, create_key=False))):
        raise RecoveryError("REFUSED: authenticated recovery stage registry failed.")
    return path, plan


def assert_commit_execution_window(plan: dict[str, Any]) -> None:
    expires = datetime.fromisoformat(str(plan.get("expires_utc", "")))
    if expires.tzinfo is None or utc_now() + COMMIT_EXPIRY_MARGIN >= expires:
        raise RecoveryError("REFUSED: insufficient recovery-plan time remains; stage a fresh plan.")


def _write_event(operation: str, plan: dict[str, Any], name: str) -> None:
    exclusive_json(event_path(operation, name), {
        "schema": 1, "tool": TOOL_NAME, "operation_sha256": operation,
        "plan_sha256": plan["sha256"], "event": name,
        "utc": utc_now().isoformat(), "status": "attempted_no_retry",
    })


def command_diagnose_delete_route(_args: argparse.Namespace) -> None:
    """Read only the fixed plugin row and emit bounded Delete-link structure."""
    validate_previous_failure_evidence()
    with admin_session("WordPress fixed cleanup-plugin recovery read-only route diagnosis") as admin:
        admin.goto_plugins()
        rows = admin.rows()
        updates = admin.page.query_selector_all(UPDATE_ROW_SELECTOR)
        if not rows:
            print_json({"status": "VERIFIED_PLUGIN_ROW_ABSENT", "row_count": 0,
                        "update_row_count": len(updates), "delete_link_count": 0,
                        "delete_route": None, "website_writes": 0,
                        "plans_created": 0, "attempt_locks": 0, "emails": 0})
            return
        row = rows[0]
        links = row.query_selector_all(DELETE_SELECTOR)
        route = sanitized_delete_url_shape(
            links[0].get_attribute("href") if len(links) == 1 else ""
        )
        tokens = set(str(row.get_attribute("class") or "").split())
        print_json({
            "status": "READ_ONLY_FIXED_DELETE_ROUTE_DIAGNOSIS",
            "row_count": len(rows), "update_row_count": len(updates),
            "row_active": "active" in tokens, "row_inactive": "inactive" in tokens,
            "delete_link_count": len(links), "delete_route": route,
            "website_writes": 0, "plans_created": 0, "attempt_locks": 0,
            "emails": 0,
        })


def command_stage(_args: argparse.Namespace) -> None:
    previous_evidence = validate_previous_failure_evidence()
    operation = operation_sha()
    with admin_session("WordPress fixed cleanup-plugin recovery read-only stage") as admin:
        observed = collect_state(admin)
    if observed.get("plugin", {}).get("present") is False:
        assert_recovery_eligible(observed, "final")
        print_json({
            "status": "VERIFIED_ALREADY_ABSENT", "plan_created": False,
            "operation_sha256": operation, "plugin": observed["plugin"],
            "verification": observed, "previous_failure_evidence": previous_evidence,
            "website_writes": 0, "plugin_deletions": 0, "emails": 0,
        })
        return
    assert_recovery_eligible(observed, "removal")
    if recovery_locked(operation):
        raise RecoveryError("REFUSED: fixed recovery operation is permanently replay-locked.")
    path, plan = stage_plan(observed, previous_evidence)
    print_json({
        "status": "STAGED_READ_ONLY", "plan": str(path),
        "plan_sha256": plan["sha256"], "operation_sha256": operation,
        "plugin": plan["plugin"], "before": observed,
        "previous_failure_evidence": previous_evidence,
        "after_expected": plan["after_expected"],
        "write_sequence": plan["write_sequence"], "risk": plan["risk"],
        "expires_utc": plan["expires_utc"], "approval_required": APPROVAL_WORD,
        "website_writes": 0, "plugin_deletions": 0, "emails": 0,
    })


def command_commit(args: argparse.Namespace) -> None:
    require_approval(args.approval)
    path, plan = load_plan(args.plan)
    operation = plan["operation_sha256"]
    if recovery_locked(operation):
        raise RecoveryError("REFUSED: fixed recovery operation is permanently replay-locked.")
    if plan["previous_failure_evidence"] != validate_previous_failure_evidence():
        raise RecoveryError("REFUSED: prior locked evidence changed before recovery commit.")

    locked = False
    completed: list[str] = []
    try:
        with admin_session("WordPress fixed cleanup-plugin recovery one-attempt commit") as admin:
            fresh = collect_state(admin)
            assert_recovery_eligible(fresh, "removal")
            if fresh != plan["before"] or fresh["fingerprint"] != plan["before_fingerprint"]:
                raise RecoveryError("REFUSED: exact live recovery state/fingerprint drifted; stage fresh.")
            delete_link = admin.preflight_delete_control(fresh)
            assert_commit_execution_window(plan)
            exclusive_json(attempt_path(operation), {
                "schema": 1, "tool": TOOL_NAME, "operation_sha256": operation,
                "plan_sha256": plan["sha256"], "plan_path": str(path),
                "locked_utc": utc_now().isoformat(), "status": "attempt_started_no_retry",
                "first_side_effect": "click_exact_fixed_plugin_delete_link",
            })
            locked = True

            confirmation_submit = admin.prepare_delete(delete_link, fresh)
            completed.append("exact_fixed_plugin_delete_link_clicked_and_confirmation_validated")
            _write_event(operation, plan, "01-delete-link-clicked")
            _write_event(operation, plan, "02-delete-confirmation-submit-attempted")
            deleted_plugin = admin.execute_delete(confirmation_submit, fresh)
            completed.append("exact_fixed_plugin_delete_confirmation_submitted_once_and_row_absent")

            final = collect_state(admin)
            assert_recovery_eligible(final, "final")
            completed.append("final_origin_media_library_and_plugin_contract_verified")
            result = {
                "schema": 1, "tool": TOOL_NAME, "operation_sha256": operation,
                "plan_sha256": plan["sha256"], "status": "COMMITTED_AND_VERIFIED",
                "completed_utc": utc_now().isoformat(), "completed_steps": completed,
                "previous_failure_evidence": plan["previous_failure_evidence"],
                "before": fresh, "deleted_plugin": deleted_plugin, "final": final,
                "delete_link_navigations": 1, "delete_confirmation_submissions": 1,
                "plugin_deleted": True, "no_retry": True, "rollback": False,
                "cleanup_after_failure": False, "emails": 0,
                "woocommerce_writes": 0, "zoho_writes": 0,
            }
            exclusive_json(result_path(operation), result)
    except Exception as exc:
        if locked:
            failure = {
                "schema": 1, "tool": TOOL_NAME, "operation_sha256": operation,
                "plan_sha256": plan["sha256"], "status": "INDETERMINATE_NO_RETRY",
                "failed_utc": utc_now().isoformat(), "error_type": type(exc).__name__,
                "error": "Locked fixed plugin recovery did not complete every verification; no retry, rollback or cleanup.",
                "completed_steps": completed,
                "previous_failure_evidence": plan["previous_failure_evidence"],
                "side_effect_may_have_landed": True, "no_retry": True,
                "rollback": False, "cleanup_after_failure": False, "emails": 0,
                "woocommerce_writes": 0, "zoho_writes": 0,
            }
            with contextlib.suppress(RecoveryError):
                exclusive_json(result_path(operation), failure)
            with contextlib.suppress(OSError):
                append_receipt("wordpress_fixed_origin_cleanup_plugin_recovery_indeterminate_no_retry",
                               str(result_path(operation)))
        raise
    append_receipt("wordpress_fixed_origin_cleanup_plugin_recovery_committed_and_verified",
                   str(result_path(operation)))
    print_json(result)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    diagnose = sub.add_parser(
        "diagnose-delete-route", help="bounded read-only fixed Delete-link structure"
    )
    diagnose.set_defaults(func=command_diagnose_delete_route)
    stage = sub.add_parser("stage", help="bounded read-only verification and optional fixed plan")
    stage.set_defaults(func=command_stage)
    commit = sub.add_parser("commit", help="one exact fixed-plugin recovery attempt")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except (RecoveryError, UiLaneBusy) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
