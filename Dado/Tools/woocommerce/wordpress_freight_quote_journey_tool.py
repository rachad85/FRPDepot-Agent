#!/usr/bin/env python3
"""Fixed, commissioned, CDP9229-only freight-quote journey deployer.

Exactly three commands exist: ``stage``, ``apply --plan PATH`` and
``rollback --plan PATH``. Rachad's direct Choice-A commission is the final
authorization; this fixed route has no later approval phrase or parameter.

The capability is closed over one origin, one plugin row, one forward 2.0.3
ZIP, one rollback 1.0.1 ZIP, one upload/overwrite route and one status/rollback
control.  It never launches a browser and never closes the persistent browser
behind CDP9229.  The shared WordPress UI lane is acquired once around the whole
command, before an apply/rollback one-attempt lock can be created.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timedelta, timezone
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Iterator
from urllib.parse import urlsplit
import zipfile

sys.path.append(str(Path(__file__).resolve().parent.parent / "common"))
from ui_lane_lock import UiLaneBusy, UiLaneLockError, ui_browser_lock  # noqa: E402

TOOL_NAME = "FRP Depot Fixed Freight Quote Journey Deployment Tool"
TOOL_VERSION = "2.0.7"
SCHEMA_VERSION = 8
SPECIFICATION_SHA256 = "5348ef3f357676f5629cf72696fd3fe0be718a3847854974f20cf28cc7047400"
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "freight_quote_journey_plans"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "freight_quote_journey_receipts.jsonl"
PLAN_LIFETIME_HOURS = 24
PLAN_CLOCK_SKEW_MINUTES = 5

EXACT_ORIGIN = "https://frpdepots.com"
ALLOWED_HOST = "frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
PLUGIN_NAME = "FRP Depot Freight Checkout Guard"
PLUGIN_SLUG = "frpdepot-freight-checkout-guard"
PLUGIN_FILE = f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php"
FROM_VERSION = "1.0.1"
TO_VERSION = "2.0.3"
ROLLBACK_VERSION = "1.0.1"
ACTION = "freight_journey_2_0_3_direct_commission_active_overwrite_apply"

ARTIFACT_PATH = (ROOT / "Dado" / "Tools" / "woocommerce" / "freight_quote_journey"
                 / f"{PLUGIN_SLUG}-2.0.3.zip")
ARTIFACT_SHA256 = "8202de8ad490fbbf379c4706f7d0901659e73bc71ae6e4fbba99b570a79db47c"
ARTIFACT_BYTES = 26798
ARTIFACT_MEMBERS = (
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.css",
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.js",
    f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php",
    f"{PLUGIN_SLUG}/readme.txt",
    f"{PLUGIN_SLUG}/ups-allowlist.json",
)
ARTIFACT_MEMBER_SHA256 = {
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.css":
        "3488e16872885011009b838b5959d22fc71a0b4c7ca78ac4ae44cd8addda9f0c",
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.js":
        "d644d29d47e051203c04ab13100b724e41a922a45a83896a422470dcd8e206b0",
    f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php":
        "f471a2aa8d6af7c1acd6698a5ec1f8bafcbf476cf6d2a83ae8256e8784be5e27",
    f"{PLUGIN_SLUG}/readme.txt":
        "abcbac5461b92d13a05d7f18168eb89b51034de1ce81f26df9b46a5796d68261",
    f"{PLUGIN_SLUG}/ups-allowlist.json":
        "b4ac24e198d70d5bf971739dbbb7718e209a805d82d050256108fbed842ff276",
}

ROLLBACK_ARTIFACT_PATH = (ROOT / "Dado" / "Tools" / "woocommerce" / "freight_checkout_guard"
                          / f"{PLUGIN_SLUG}.zip")
ROLLBACK_ARTIFACT_SHA256 = "fe6fa440ea3a08169bf568ae0fbb06f666ad71c1110e58f9b2b6bb0acc8be6cb"
ROLLBACK_ARTIFACT_BYTES = 31216
ROLLBACK_ARTIFACT_MEMBERS = (
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-notice.js",
    f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php",
    f"{PLUGIN_SLUG}/readme.txt",
    f"{PLUGIN_SLUG}/ups-allowlist.json",
)
ROLLBACK_ARTIFACT_MEMBER_SHA256 = {
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-notice.js":
        "3fa0c27f70bd08ba17ce5b3b030bb7f8797ffce1413ffbe87a657748d2447040",
    f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php":
        "69c7ee4cb5e534d7c4e18c50c6489014f9add4fc2416332cbb6f0b1f6bb9f9a4",
    f"{PLUGIN_SLUG}/readme.txt":
        "2bdad127871b04376e5f37b74d2b9373c71c927a91d9c9519e194335a300f422",
    f"{PLUGIN_SLUG}/ups-allowlist.json":
        "b4ac24e198d70d5bf971739dbbb7718e209a805d82d050256108fbed842ff276",
}
BASELINE_ALLOWLIST_SHA256 = ROLLBACK_ARTIFACT_MEMBER_SHA256[
    f"{PLUGIN_SLUG}/ups-allowlist.json"
]
# A reviewed forward artifact must carry these implementation-contract markers.
# The currently pinned bytes are deliberately rejected if they do not.
FORWARD_CONTRACT_MARKERS = (
	"FRPDEPOT_FQJ_BACKUP_FORM_OPTION",
	"FRPDEPOT_FQJ_BACKUP_PAGE_OPTION",
	"FRPDEPOT_FQJ_BACKUP_CONTACT_OPTION",
	"FRPDEPOT_FQJ_BACKUP_ROUTE_OPTION",
    "FRPDEPOT_FQJ_FORM_ADMIN_MARKER",
    "frpdepot_fqj_resolve_owned_form",
    "partial or multiple match",
    "previous_receipt_sha256",
    "receipt_sha256",
    "ROLLBACK_BLOCKED_DRIFT",
    "receipt_chain_valid",
    "receipt_append_only",
    "privacy",
    "admin_post_frpdepot_fqj_fixed_apply",
)
FORWARD_FORBIDDEN_MARKERS = ("register_activation_hook",)
ENFORCE_PLUGIN_CONTRACT = True

CONTACT_ID = 469
SOURCE_FORM_ID = 1
ADMIN_SLUG = "frpdepot-freight-quote-journey"
PLUGINS_URL = f"{EXACT_ORIGIN}/wp-admin/plugins.php"
UPLOAD_URL = f"{EXACT_ORIGIN}/wp-admin/plugin-install.php?tab=upload"
STATUS_URL = f"{EXACT_ORIGIN}/wp-admin/tools.php?page={ADMIN_SLUG}"
ALLOWED_ADMIN_PATHS = frozenset({
    "/wp-admin/plugins.php",
    "/wp-admin/plugin-install.php",
    "/wp-admin/update.php",
    "/wp-admin/tools.php",
    "/wp-admin/admin-post.php",
})

COMMANDS = ("stage", "apply", "rollback")
NAV_TIMEOUT_MS = 45_000
LOAD_TIMEOUT_MS = 45_000
ACTION_TIMEOUT_MS = 15_000
ROW_SELECTOR = f'tr[data-plugin="{PLUGIN_FILE}"]:not(.plugin-update-tr)'
VERSION_SELECTOR = ".plugin-version-author-uri"
VERSION_PATTERN = re.compile(r"(?i)\bversion\s+([0-9][0-9A-Za-z.\-+_]*)")
ACTIVATE_SELECTOR = ".row-actions .activate a"
DEACTIVATE_SELECTOR = ".row-actions .deactivate a"
UPDATE_ROW_SELECTOR = f'tr.plugin-update-tr[data-plugin="{PLUGIN_FILE}"]'
UPLOAD_INPUT_SELECTOR = 'input[type="file"][name="pluginzip"]'
UPLOAD_SUBMIT_SELECTOR = "#install-plugin-submit"
COMPARISON_SELECTOR = "table.update-from-upload-comparison"
OVERWRITE_SELECTOR = "a.update-from-upload-overwrite"
STATUS_SELECTOR = "#frpdepot-fqj-status"
APPLY_FORM_SELECTOR = "#frpdepot-fqj-apply-form"
APPLY_BUTTON_SELECTOR = "#frpdepot-fqj-apply"
ROLLBACK_FORM_SELECTOR = "#frpdepot-fqj-rollback-form"
ROLLBACK_BUTTON_SELECTOR = "#frpdepot-fqj-rollback"

PLAN_KEYS = frozenset({
    "schema_version", "tool", "tool_version", "action", "origin", "created_utc",
    "expires_utc", "nonce", "spec_sha256", "new_artifact", "rollback_artifact",
    "plugin_before", "rollback_target", "artifact_contract",
    "ui_contract", "validation_contract",
})
ROW_KEYS = frozenset({
    "present", "active", "version", "update_marker", "plugin_file", "fingerprint",
})
ARTIFACT_KEYS = frozenset({"path", "version", "sha256", "bytes", "members"})
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX32 = re.compile(r"^[0-9a-f]{32}$")

PRIVACY_STATUS = {
    "recipient_values_projected": False,
    "customer_values_projected": False,
    "artifact_content_projected": False,
    "route_hash_only": True,
}
BACKUP_STATUS_KEYS = (
    "form_backup_present", "quote_page_backup_present",
    "contact_backup_present", "route_backup_present",
)
STATUS_KEYS = frozenset({
    "spec_sha256", "status", "deployment_id", "source_form_id",
    "source_notification_name_match", "route_sha256", "form_id", "form_owned",
    "form_sha256", "page_id", "page_owned", "page_sha256", "contact_id",
    "contact_new_count", "contact_old_count", "contact_sha256",
    *BACKUP_STATUS_KEYS, "receipt_count", "receipt_schema_valid",
    "receipt_chain_valid", "receipt_append_only", "receipt_head_sha256",
    "apply_receipt_head_sha256", "rollback_drift_free", "rollback_blocked_artifact",
    "form_before_sha256", "quote_page_before_sha256", "contact_before_sha256",
    "privacy",
})
MIN_APPLY_RECEIPTS = 11

RECEIPT_KEYS = frozenset({
    "schema_version", "deployment_id", "sequence", "utc", "spec_sha256",
    "operation", "artifact", "artifact_id", "before_sha256", "after_sha256",
    "status", "previous_receipt_sha256", "receipt_sha256",
})
RECEIPT_OPERATIONS = frozenset({
    "backup", "create", "replace", "verify", "restore", "delete", "finalize",
})
RECEIPT_ARTIFACTS = frozenset({
    "plugin", "form", "quote_page", "contact_faq", "route", "journey",
})
RECEIPT_STATUSES = frozenset({
    "OK", "FAILED_CLOSED", "INDETERMINATE", "ROLLBACK_BLOCKED_DRIFT", "ROLLED_BACK",
})
ZERO_SHA256 = "0" * 64


class DeploymentError(RuntimeError):
    """Closed refusal whose message contains no page, route or customer content."""


class IndeterminateError(DeploymentError):
    """A fixed write was attempted but its complete result was not proven."""


class RollbackDriftError(DeploymentError):
    """The plugin reported drift and correctly left the drifted artifact untouched."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_for(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def holds_wordpress_browser(purpose: str):
    """Acquire the non-nested command lock once, before any attempt lock exists."""
    def decorate(function):
        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any):
            with ui_browser_lock("wordpress", purpose=purpose):
                return function(*args, **kwargs)
        return wrapper
    return decorate


def assert_origin(url: str) -> None:
    parsed = urlsplit(str(url or ""))
    try:
        port = parsed.port
    except ValueError as exc:
        raise DeploymentError("REFUSED: invalid browser URL port.") from exc
    if (parsed.scheme != "https" or (parsed.hostname or "").casefold() != ALLOWED_HOST
            or port not in (None, 443) or parsed.username or parsed.password):
        raise DeploymentError(f"REFUSED: browser is outside {EXACT_ORIGIN}.")


def assert_admin_url(url: str) -> None:
    assert_origin(url)
    if (urlsplit(str(url)).path or "/") not in ALLOWED_ADMIN_PATHS:
        raise DeploymentError("REFUSED: browser left the fixed plugin/status admin paths.")


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def _artifact_constants(rollback: bool):
    if rollback:
        return (ROLLBACK_ARTIFACT_PATH, ROLLBACK_ARTIFACT_SHA256,
                ROLLBACK_ARTIFACT_BYTES, ROLLBACK_VERSION, ROLLBACK_ARTIFACT_MEMBERS,
                ROLLBACK_ARTIFACT_MEMBER_SHA256)
    return (ARTIFACT_PATH, ARTIFACT_SHA256, ARTIFACT_BYTES, TO_VERSION,
            ARTIFACT_MEMBERS, ARTIFACT_MEMBER_SHA256)


def verify_artifact(*, rollback: bool = False, path: Path | None = None) -> dict[str, Any]:
    fixed_path, fixed_hash, fixed_bytes, fixed_version, fixed_members, fixed_member_hashes = \
        _artifact_constants(rollback)
    artifact = Path(path or fixed_path)
    if artifact.resolve() != fixed_path.resolve():
        raise DeploymentError("REFUSED: only the hard-coded artifact path is accepted.")
    if not artifact.is_file():
        raise DeploymentError("REFUSED: the hard-coded artifact is missing.")
    raw = artifact.read_bytes()
    if len(raw) != fixed_bytes or not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), fixed_hash):
        raise DeploymentError("REFUSED: hard-coded artifact bytes or SHA-256 do not match.")
    try:
        with zipfile.ZipFile(artifact) as archive:
            names = tuple(archive.namelist())
            if names != tuple(sorted(names)):
                raise DeploymentError("REFUSED: fixed artifact member order is not deterministic.")
            data = {name: archive.read(name) for name in names}
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise DeploymentError("REFUSED: fixed artifact is unreadable.") from exc
    if names != tuple(sorted(fixed_members)):
        raise DeploymentError("REFUSED: fixed artifact member set does not match.")
    member_hashes = {name: hashlib.sha256(data[name]).hexdigest() for name in names}
    if member_hashes != fixed_member_hashes:
        raise DeploymentError("REFUSED: fixed artifact member hash mismatch.")
    php = data[PLUGIN_FILE].decode("utf-8", errors="replace")
    match = re.search(r"(?im)^\s*\*\s*Version:\s*(\S+)\s*$", php)
    version = match.group(1) if match else ""
    if version != fixed_version or f"Plugin Name: {PLUGIN_NAME}" not in php:
        raise DeploymentError("REFUSED: fixed artifact plugin identity/version mismatch.")
    if not rollback and SPECIFICATION_SHA256 not in php:
        raise DeploymentError("REFUSED: forward artifact is not pinned to the specification.")
    if not rollback and ENFORCE_PLUGIN_CONTRACT:
        allowlist_name = f"{PLUGIN_SLUG}/ups-allowlist.json"
        if member_hashes[allowlist_name] != BASELINE_ALLOWLIST_SHA256:
            raise DeploymentError(
                "REFUSED: forward artifact did not carry the audited 1.0.1 allowlist byte-for-byte."
            )
        missing = [marker for marker in FORWARD_CONTRACT_MARKERS if marker not in php]
        if missing:
            raise DeploymentError(
                "REFUSED: forward artifact lacks the reviewed backup/receipt/privacy/drift contract."
            )
        if any(marker in php for marker in FORWARD_FORBIDDEN_MARKERS):
            raise DeploymentError(
                "REFUSED: forward artifact can trigger the business transaction during activation."
            )
    return {
        "path": str(artifact), "version": version, "sha256": fixed_hash,
        "bytes": fixed_bytes, "members": list(names),
    }


def project_row(present: bool, active: bool | None, version: str,
                update_marker: bool = False) -> dict[str, Any]:
    row = {
        "present": bool(present), "active": active, "version": str(version or ""),
        "update_marker": bool(update_marker), "plugin_file": PLUGIN_FILE,
    }
    row["fingerprint"] = digest_for(row)
    return row


def artifact_contract() -> dict[str, Any]:
    return {
        "forward_member_sha256": dict(sorted(ARTIFACT_MEMBER_SHA256.items())),
        "rollback_member_sha256": dict(sorted(ROLLBACK_ARTIFACT_MEMBER_SHA256.items())),
        "baseline_allowlist_sha256": BASELINE_ALLOWLIST_SHA256,
        "forward_contract_markers": list(FORWARD_CONTRACT_MARKERS),
        "forward_forbidden_markers": list(FORWARD_FORBIDDEN_MARKERS),
        "fixed_versions": [FROM_VERSION, TO_VERSION, ROLLBACK_VERSION],
        "deterministic_member_order": True,
    }


def ui_contract() -> dict[str, Any]:
    return {
        "cdp_endpoint": CDP_ENDPOINT,
        "plugins_path": "/wp-admin/plugins.php",
        "upload_path": "/wp-admin/plugin-install.php?tab=upload",
        "update_path": "/wp-admin/update.php",
        "status_path": f"/wp-admin/tools.php?page={ADMIN_SLUG}",
        "row": ROW_SELECTOR,
        "upload_input": UPLOAD_INPUT_SELECTOR,
        "upload_submit": UPLOAD_SUBMIT_SELECTOR,
        "comparison": COMPARISON_SELECTOR,
        "overwrite": OVERWRITE_SELECTOR,
        "status": STATUS_SELECTOR,
        "apply": APPLY_BUTTON_SELECTOR,
        "rollback": ROLLBACK_BUTTON_SELECTOR,
    }


def validation_contract() -> dict[str, Any]:
    return {
        "status_keys": sorted(STATUS_KEYS),
        "privacy": PRIVACY_STATUS,
        "separate_backup_keys": list(BACKUP_STATUS_KEYS),
        "minimum_apply_receipts": MIN_APPLY_RECEIPTS,
        "receipt_hash": "sha256-canonical-json",
        "receipt_chain_required": True,
        "receipt_append_only_required": True,
        "rollback_drift_free_required": True,
        "starting_guard": FROM_VERSION,
        "current_state_evidence": "fresh live read proves active fail-closed 1.0.1; failed 2.0.2 is not live; content-write-free 2.0.3 activation and recovery projection required before explicit Apply",
        "continuous_guard_active_overwrite": True,
        "business_transaction_control": APPLY_BUTTON_SELECTOR,
        "form_ownership": {
            "search_all_forms_first": True,
            "identity": ["exact_title", "exact_admin_marker", "complete_form_projection"],
            "reuse_exactly_one_complete_match": True,
            "refuse_partial_or_multiple_matches": True,
            "never_duplicate_owned_form": True,
        },
    }


class AdminPage:
    """Narrow actor over only the exact plugin row, upload, status and rollback controls."""

    def __init__(self, page: Any):
        self._page = page

    @property
    def url(self) -> str:
        return str(self._page.url)

    def _goto(self, url: str) -> None:
        assert_admin_url(url)
        self._page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(self.url)

    def goto_plugins(self) -> None:
        self._goto(PLUGINS_URL)

    def goto_upload(self) -> None:
        self._goto(UPLOAD_URL)

    def goto_status(self) -> None:
        self._goto(STATUS_URL)

    def _row(self) -> Any:
        assert_admin_url(self.url)
        rows = self._page.query_selector_all(ROW_SELECTOR)
        if len(rows) != 1:
            raise DeploymentError("REFUSED: the one fixed freight-guard row is missing or ambiguous.")
        return rows[0]

    def read_row(self) -> dict[str, Any]:
        row = self._row()
        classes = set(str(row.get_attribute("class") or "").split())
        by_active = "active" in classes
        by_inactive = "inactive" in classes
        activate = row.query_selector(ACTIVATE_SELECTOR) is not None
        deactivate = row.query_selector(DEACTIVATE_SELECTOR) is not None
        if by_active == by_inactive or activate == deactivate or by_active != deactivate:
            raise DeploymentError("REFUSED: fixed plugin state signals are ambiguous.")
        cell = row.query_selector(VERSION_SELECTOR)
        match = VERSION_PATTERN.search(str(cell.inner_text() or "")) if cell else None
        if not match:
            raise DeploymentError("REFUSED: fixed plugin version is unreadable.")
        updates = self._page.query_selector_all(UPDATE_ROW_SELECTOR)
        if len(updates) > 1:
            raise DeploymentError("REFUSED: fixed update marker is ambiguous.")
        return project_row(True, by_active, match.group(1), "update" in classes or bool(updates))

    def _state_write(self, selector: str, before_version: str, before_active: bool,
                     after_version: str, after_active: bool) -> dict[str, Any]:
        before = self.read_row()
        if before["version"] != before_version or before["active"] is not before_active:
            raise DeploymentError("REFUSED: fixed plugin changed before the scoped state write.")
        link = self._row().query_selector(selector)
        if link is None:
            raise DeploymentError("REFUSED: fixed scoped plugin action is unavailable.")
        link.click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_TIMEOUT_MS)
        assert_admin_url(self.url)
        after = self.read_row()
        if after["version"] != after_version or after["active"] is not after_active:
            raise IndeterminateError("Fixed plugin state write did not read back expected state.")
        return after

    def deactivate(self, version: str) -> dict[str, Any]:
        return self._state_write(DEACTIVATE_SELECTOR, version, True, version, False)

    def activate(self, version: str) -> dict[str, Any]:
        return self._state_write(ACTIVATE_SELECTOR, version, False, version, True)

    @staticmethod
    def _comparison_cell(table: Any, label: str) -> str:
        wanted = re.compile(label, re.IGNORECASE)
        for row in table.query_selector_all("tr"):
            cells = row.query_selector_all("td")
            if len(cells) >= 2 and wanted.fullmatch(str(cells[0].inner_text() or "").strip()):
                return str(cells[-1].inner_text() or "").strip()
        return ""

    def replace(self, artifact: Path, expected_version: str, *, preserve_active: bool = False) -> dict[str, Any]:
        fixed = ARTIFACT_PATH if expected_version == TO_VERSION else ROLLBACK_ARTIFACT_PATH
        if artifact.resolve() != fixed.resolve():
            raise DeploymentError("REFUSED: replacement path is not the fixed artifact.")
        before = self.read_row()
        if preserve_active:
            if before["active"] is not True:
                raise DeploymentError("REFUSED: continuous-guard replacement requires the fixed plugin active.")
        elif before["active"] is not False:
            raise DeploymentError("REFUSED: replacement requires the fixed plugin inactive.")
        self.goto_upload()
        chooser = self._page.query_selector(UPLOAD_INPUT_SELECTOR)
        submit = self._page.query_selector(UPLOAD_SUBMIT_SELECTOR)
        if chooser is None or submit is None:
            raise DeploymentError("REFUSED: fixed WordPress upload controls are unavailable.")
        chooser.set_input_files(str(artifact), timeout=ACTION_TIMEOUT_MS)
        submit.click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_TIMEOUT_MS)
        assert_admin_url(self.url)
        if (urlsplit(self.url).path or "/") != "/wp-admin/update.php":
            raise DeploymentError("REFUSED: upload did not reach the exact WordPress update path.")
        tables = self._page.query_selector_all(COMPARISON_SELECTOR)
        links = self._page.query_selector_all(OVERWRITE_SELECTOR)
        if len(tables) != 1 or len(links) != 1:
            raise DeploymentError("REFUSED: exact replace-current confirmation is unavailable.")
        name = self._comparison_cell(tables[0], r"(plugin\s+)?name")
        version = self._comparison_cell(tables[0], r"version")
        if name != PLUGIN_NAME or version != expected_version:
            raise DeploymentError("REFUSED: replace comparison identity/version mismatch.")
        links[0].click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_TIMEOUT_MS)
        assert_admin_url(self.url)
        self.goto_plugins()
        after = self.read_row()
        expected_active = True if preserve_active else False
        if after["version"] != expected_version or after["active"] is not expected_active:
            raise IndeterminateError("Replacement did not read back the fixed version and active state.")
        return after

    def read_status(self) -> dict[str, Any]:
        self.goto_status()
        rows = self._page.query_selector_all(STATUS_SELECTOR)
        if len(rows) != 1:
            raise DeploymentError("REFUSED: fixed freight-journey status projection is unavailable.")
        raw = str(rows[0].get_attribute("data-projection") or "")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeploymentError("REFUSED: fixed status projection is not valid JSON.") from exc
        if not isinstance(value, dict) or set(value) != STATUS_KEYS:
            raise DeploymentError("REFUSED: fixed status projection schema changed.")
        _validate_status_types(value)
        return value

    def click_fixed_apply(self, before: dict[str, Any]) -> dict[str, Any]:
        require_recovery_source_status(before)
        forms = self._page.query_selector_all(APPLY_FORM_SELECTOR)
        buttons = self._page.query_selector_all(APPLY_BUTTON_SELECTOR)
        if len(forms) != 1 or len(buttons) != 1:
            raise DeploymentError("REFUSED: fixed freight-journey apply control is missing or ambiguous.")
        buttons[0].click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_TIMEOUT_MS)
        assert_admin_url(self.url)
        after = self.read_status()
        require_applied_status(after)
        return after

    def click_internal_rollback(self, before: dict[str, Any]) -> dict[str, Any]:
        require_rollback_ready_status(before)
        forms = self._page.query_selector_all(ROLLBACK_FORM_SELECTOR)
        buttons = self._page.query_selector_all(ROLLBACK_BUTTON_SELECTOR)
        if len(forms) != 1 or len(buttons) != 1:
            raise DeploymentError("REFUSED: fixed rollback control is missing or ambiguous.")
        buttons[0].click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_TIMEOUT_MS)
        assert_admin_url(self.url)
        after = self.read_status()
        if after["status"] == "rollback_blocked_drift" or not after["rollback_drift_free"]:
            raise RollbackDriftError(
                "ROLLBACK_BLOCKED_DRIFT: plugin left the drifted artifact untouched and stopped."
            )
        require_rolled_back_status(before, after)
        return after


@contextlib.contextmanager
def owned_admin_page(context: Any) -> Iterator[Any]:
    """Create and close one command-owned tab; preserve every pre-existing browser tab."""
    page = context.new_page()
    try:
        yield page
    finally:
        if not page.is_closed():
            page.close()


@contextlib.contextmanager
def admin_session() -> Iterator[AdminPage]:
    """Attach to CDP9229; command owns the lane. Never close the persistent browser."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=15_000)
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            raise DeploymentError("Authenticated WordPress CDP9229 browser is unavailable.") from exc
        contexts = browser.contexts
        if len(contexts) != 1 or not contexts[0].pages:
            raise DeploymentError("CDP9229 must expose exactly one authenticated browser context.")
        with owned_admin_page(contexts[0]) as page:
            yield AdminPage(page)


def _exclusive_json(path: Path, value: dict[str, Any], mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError as exc:
        raise DeploymentError("Immutable transaction evidence already exists.") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, mode)


def plan_apply_lock(path: Path) -> Path:
    return path.with_suffix(".apply-attempt.json")


def plan_rollback_lock(path: Path) -> Path:
    return path.with_suffix(".rollback-attempt.json")


def plan_result(path: Path) -> Path:
    return path.with_suffix(".result.json")


def plan_rollback_result(path: Path) -> Path:
    return path.with_suffix(".rollback-result.json")


def write_attempt_lock(path: Path, plan: dict[str, Any], operation: str) -> None:
    if operation not in ("apply", "rollback"):
        raise DeploymentError("Internal attempt operation is invalid.")
    _exclusive_json(path, {
        "plan_sha256": plan["sha256"], "operation": operation,
        "started_utc": utc_now().isoformat(), "status": "in_flight",
        "attempts_allowed": 1, "retry": False,
    }, stat.S_IREAD | stat.S_IWRITE)


def close_attempt(path: Path, plan: dict[str, Any], operation: str, status_text: str,
                  stage: str, before_hash: str, after_hash: str) -> None:
    value = {
        "plan_sha256": plan["sha256"], "operation": operation, "status": status_text,
        "stage": stage, "updated_utc": utc_now().isoformat(), "retry": False,
        "before_sha256": before_hash, "after_sha256": after_hash,
    }
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    os.chmod(path, stat.S_IREAD)


def write_result(path: Path, value: dict[str, Any]) -> None:
    _exclusive_json(path, value, stat.S_IREAD)


def _read_receipts() -> list[dict[str, Any]]:
    if not RECEIPTS.exists():
        return []
    try:
        return [json.loads(line) for line in RECEIPTS.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError("REFUSED: local append-only receipt evidence is unreadable.") from exc


def _receipt_without_hash(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "receipt_sha256"}


def validate_receipt_chain(deployment_id: str) -> tuple[int, str]:
    rows = [row for row in _read_receipts()
            if isinstance(row, dict) and row.get("deployment_id") == deployment_id]
    previous = ZERO_SHA256
    for sequence, row in enumerate(rows, 1):
        if set(row) != RECEIPT_KEYS or row["sequence"] != sequence:
            raise DeploymentError("REFUSED: local receipt schema or sequence is invalid.")
        if (row["schema_version"] != SCHEMA_VERSION
                or row["spec_sha256"] != SPECIFICATION_SHA256
                or row["operation"] not in RECEIPT_OPERATIONS
                or row["artifact"] not in RECEIPT_ARTIFACTS
                or row["status"] not in RECEIPT_STATUSES
                or row["previous_receipt_sha256"] != previous
                or not HEX64.fullmatch(str(row["before_sha256"]))
                or not HEX64.fullmatch(str(row["after_sha256"]))):
            raise DeploymentError("REFUSED: local receipt chain contract is invalid.")
        expected = digest_for(_receipt_without_hash(row))
        if row["receipt_sha256"] != expected:
            raise DeploymentError("REFUSED: local receipt hash chain failed.")
        previous = expected
    return len(rows), previous


def append_receipt(plan: dict[str, Any], operation: str, artifact: str, artifact_id: Any,
                   before_hash: str, after_hash: str, status_text: str) -> dict[str, Any]:
    if (operation not in RECEIPT_OPERATIONS or artifact not in RECEIPT_ARTIFACTS
            or status_text not in RECEIPT_STATUSES
            or not HEX64.fullmatch(str(before_hash)) or not HEX64.fullmatch(str(after_hash))):
        raise DeploymentError("Internal receipt value is outside the closed vocabulary.")
    deployment_id = str(plan["nonce"])
    count, previous = validate_receipt_chain(deployment_id)
    core = {
        "schema_version": SCHEMA_VERSION,
        "deployment_id": deployment_id,
        "sequence": count + 1,
        "utc": utc_now().isoformat(),
        "spec_sha256": SPECIFICATION_SHA256,
        "operation": operation,
        "artifact": artifact,
        "artifact_id": artifact_id,
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "status": status_text,
        "previous_receipt_sha256": previous,
    }
    row = {**core, "receipt_sha256": digest_for(core)}
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(RECEIPTS), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return row


def write_plan(before: dict[str, Any], new_artifact: dict[str, Any],
               rollback_artifact: dict[str, Any]) -> Path:
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "action": ACTION,
        "origin": EXACT_ORIGIN,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "spec_sha256": SPECIFICATION_SHA256,
        "new_artifact": new_artifact,
        "rollback_artifact": rollback_artifact,
        "plugin_before": before,
        "rollback_target": project_row(True, True, ROLLBACK_VERSION, False),
        "artifact_contract": artifact_contract(),
        "ui_contract": ui_contract(),
        "validation_contract": validation_contract(),
    }
    plan = {**core, "sha256": digest_for(core)}
    path = PLAN_DIR / f"{created.strftime('%Y%m%dT%H%M%SZ')}_freight_quote_{plan['sha256'][:16]}.json"
    _exclusive_json(path, plan, stat.S_IREAD)
    append_receipt(plan, "verify", "plugin", PLUGIN_FILE,
                   before["fingerprint"], digest_for({"new": new_artifact, "rollback": rollback_artifact}),
                   "OK")

    return path


def resolve_plan_path(raw: str) -> Path:
    path = Path(raw).resolve()
    if PLAN_DIR.resolve() not in path.parents:
        raise DeploymentError("REFUSED: plan is outside the fixed freight-journey directory.")
    return path


def _artifact_matches(value: Any, fixed: dict[str, Any]) -> bool:
    return isinstance(value, dict) and set(value) == ARTIFACT_KEYS and value == fixed


def _newer_plan_with_same_contract_exists(path: Path, plan: dict[str, Any]) -> bool:
    """A later structurally valid plan permanently supersedes this one."""
    current_created = datetime.fromisoformat(str(plan["created_utc"]))
    current_key = (current_created, path.name)
    for candidate in PLAN_DIR.glob("*_freight_quote_*.json"):
        candidate = candidate.resolve()
        if candidate == path:
            continue
        try:
            other = json.loads(candidate.read_text(encoding="utf-8"))
            if not isinstance(other, dict) or set(other) != PLAN_KEYS | {"sha256"}:
                continue
            saved = str(other["sha256"])
            core = {key: other[key] for key in other if key != "sha256"}
            if not saved or not secrets.compare_digest(saved, digest_for(core)):
                continue
            if not (
                other["schema_version"] == SCHEMA_VERSION
                and other["tool"] == TOOL_NAME
                and other["tool_version"] == TOOL_VERSION
                and other["action"] == ACTION
                and other["origin"] == EXACT_ORIGIN
                and other["spec_sha256"] == SPECIFICATION_SHA256
                and other["new_artifact"] == plan["new_artifact"]
                and other["rollback_artifact"] == plan["rollback_artifact"]
                and other["plugin_before"] == plan["plugin_before"]
                and other["rollback_target"] == plan["rollback_target"]
                and other["artifact_contract"] == plan["artifact_contract"]
                and other["ui_contract"] == plan["ui_contract"]
                and other["validation_contract"] == plan["validation_contract"]
                and HEX32.fullmatch(str(other["nonce"]))
            ):
                continue
            other_created = datetime.fromisoformat(str(other["created_utc"]))
            other_expires = datetime.fromisoformat(str(other["expires_utc"]))
            if (other_created.tzinfo is None or other_created.utcoffset() != timedelta(0)
                    or other_expires != other_created + timedelta(hours=PLAN_LIFETIME_HOURS)):
                continue
            if (other_created, candidate.name) > current_key:
                return True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return False


def load_plan(raw: str, *, allow_expired_for_rollback: bool = False) -> tuple[Path, dict[str, Any]]:
    path = resolve_plan_path(raw)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError("REFUSED: plan is unreadable.") from exc
    if not isinstance(plan, dict):
        raise DeploymentError("REFUSED: plan is not one object.")
    saved = str(plan.pop("sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(plan)):
        raise DeploymentError("REFUSED: immutable plan hash failed.")
    if set(plan) != PLAN_KEYS:
        raise DeploymentError("REFUSED: plan schema is not the exact closed set.")
    identities = (
        plan["schema_version"] == SCHEMA_VERSION,
        plan["tool"] == TOOL_NAME,
        plan["tool_version"] == TOOL_VERSION,
        plan["action"] == ACTION,
        plan["origin"] == EXACT_ORIGIN,
        plan["spec_sha256"] == SPECIFICATION_SHA256,
        plan["artifact_contract"] == artifact_contract(),
        plan["ui_contract"] == ui_contract(),
        plan["validation_contract"] == validation_contract(),
    )
    if not all(identities):
        raise DeploymentError("REFUSED: fixed plan identity or contract changed.")
    row = plan["plugin_before"]
    if not isinstance(row, dict) or set(row) != ROW_KEYS:
        raise DeploymentError("REFUSED: plan plugin privacy projection schema changed.")
    row_core = {key: row[key] for key in row if key != "fingerprint"}
    if row["fingerprint"] != digest_for(row_core):
        raise DeploymentError("REFUSED: plan plugin privacy projection hash failed.")
    rollback_target = plan["rollback_target"]
    if not isinstance(rollback_target, dict) or set(rollback_target) != ROW_KEYS:
        raise DeploymentError("REFUSED: plan rollback target projection schema changed.")
    if rollback_target != project_row(True, True, ROLLBACK_VERSION, False):
        raise DeploymentError("REFUSED: plan rollback target is not exact 1.0.1 active state.")
    forward = verify_artifact()
    rollback = verify_artifact(rollback=True)
    if not _artifact_matches(plan["new_artifact"], forward):
        raise DeploymentError("REFUSED: plan forward artifact is not exact.")
    if not _artifact_matches(plan["rollback_artifact"], rollback):
        raise DeploymentError("REFUSED: plan rollback artifact is not exact.")
    try:
        created = datetime.fromisoformat(str(plan["created_utc"]))
        expires = datetime.fromisoformat(str(plan["expires_utc"]))
    except (ValueError, TypeError) as exc:
        raise DeploymentError("REFUSED: plan timestamps are invalid.") from exc
    if (created.tzinfo is None or created.utcoffset() != timedelta(0)
            or expires != created + timedelta(hours=PLAN_LIFETIME_HOURS)):
        raise DeploymentError("REFUSED: plan lifetime is not exact.")
    now = utc_now()
    if created > now + timedelta(minutes=PLAN_CLOCK_SKEW_MINUTES):
        raise DeploymentError("REFUSED: plan was created in the future.")
    if not allow_expired_for_rollback and now >= expires:
        raise DeploymentError("REFUSED: plan expired before apply.")
    if not HEX32.fullmatch(str(plan["nonce"])):
        raise DeploymentError("REFUSED: plan nonce is invalid.")
    plan["sha256"] = saved
    count, _head = validate_receipt_chain(plan["nonce"])
    if count < 1:
        raise DeploymentError("REFUSED: staged append-only receipt evidence is missing.")
    if not allow_expired_for_rollback and _newer_plan_with_same_contract_exists(path, plan):
        raise DeploymentError("REFUSED: a newer immutable freight-journey plan supersedes this plan.")
    return path, plan


def _validate_status_types(status: dict[str, Any]) -> None:
    hashes = (
        "route_sha256", "form_sha256", "page_sha256", "contact_sha256",
        "receipt_head_sha256", "apply_receipt_head_sha256", "form_before_sha256",
        "quote_page_before_sha256", "contact_before_sha256",
    )
    if any(not HEX64.fullmatch(str(status[key])) for key in hashes):
        raise DeploymentError("REFUSED: status projection contains an invalid SHA-256.")
    if not HEX32.fullmatch(str(status["deployment_id"])):
        raise DeploymentError("REFUSED: status deployment identity is invalid.")
    if status["privacy"] != PRIVACY_STATUS:
        raise DeploymentError("REFUSED: plugin privacy status projection changed.")
    if (type(status["source_form_id"]) is not int
            or type(status["form_id"]) is not int
            or type(status["page_id"]) is not int
            or type(status["contact_id"]) is not int
            or type(status["receipt_count"]) is not int):
        raise DeploymentError("REFUSED: status projection integer field changed type.")
    bool_keys = (
        "source_notification_name_match", "form_owned", "page_owned",
        *BACKUP_STATUS_KEYS, "receipt_schema_valid", "receipt_chain_valid",
        "receipt_append_only", "rollback_drift_free",
    )
    if any(type(status[key]) is not bool for key in bool_keys):
        raise DeploymentError("REFUSED: status projection boolean field changed type.")
    if not isinstance(status["rollback_blocked_artifact"], str):
        raise DeploymentError("REFUSED: status rollback drift projection changed type.")


def require_recovery_source_status(status: dict[str, Any]) -> None:
    """Prove the failed 2.0.0 activation made no form/page/contact business write."""
    if not isinstance(status, dict) or set(status) != STATUS_KEYS:
        raise DeploymentError("REFUSED: recovery status projection schema changed.")
    _validate_status_types(status)
    empty = digest_for(None)
    if not (
        status["spec_sha256"] == SPECIFICATION_SHA256
        and status["status"] == "not_applied"
        and status["deployment_id"] == "0" * 32
        and status["source_form_id"] == SOURCE_FORM_ID
        and status["source_notification_name_match"] is False
        and status["route_sha256"] == empty
        and status["form_id"] == 0 and status["form_owned"] is False
        and status["form_sha256"] == empty
        and status["page_id"] == 0 and status["page_owned"] is False
        and status["page_sha256"] == empty
        and status["contact_id"] == CONTACT_ID
        and status["contact_new_count"] == 0 and status["contact_old_count"] == 1
        and all(status[key] is False for key in BACKUP_STATUS_KEYS)
        and status["receipt_count"] == 0
        and status["receipt_schema_valid"] is False
        and status["receipt_chain_valid"] is False
        and status["receipt_append_only"] is False
        and status["receipt_head_sha256"] == empty
        and status["apply_receipt_head_sha256"] == empty
        and status["rollback_drift_free"] is False
        and status["rollback_blocked_artifact"] == ""
        and status["form_before_sha256"] == empty
        and status["quote_page_before_sha256"] == empty
        and status["contact_before_sha256"] == empty
        and status["privacy"] == PRIVACY_STATUS
    ):
        raise DeploymentError("REFUSED: live 2.0.0 is not the proven zero-content-write recovery state.")


def require_not_applied_zero_write_status(status: dict[str, Any]) -> None:
    """Prove no journey transaction started, without constraining unrelated Contact content."""
    if not isinstance(status, dict) or set(status) != STATUS_KEYS:
        raise DeploymentError("REFUSED: zero-write status projection schema changed.")
    _validate_status_types(status)
    empty = digest_for(None)
    if not (
        status["spec_sha256"] == SPECIFICATION_SHA256
        and status["status"] == "not_applied"
        and status["deployment_id"] == "0" * 32
        and status["source_form_id"] == SOURCE_FORM_ID
        and status["source_notification_name_match"] is False
        and status["route_sha256"] == empty
        and status["form_id"] == 0 and status["form_owned"] is False
        and status["form_sha256"] == empty
        and status["page_id"] == 0 and status["page_owned"] is False
        and status["page_sha256"] == empty
        and status["contact_id"] == CONTACT_ID
        and all(status[key] is False for key in BACKUP_STATUS_KEYS)
        and status["receipt_count"] == 0
        and status["receipt_schema_valid"] is False
        and status["receipt_chain_valid"] is False
        and status["receipt_append_only"] is False
        and status["receipt_head_sha256"] == empty
        and status["apply_receipt_head_sha256"] == empty
        and status["rollback_drift_free"] is False
        and status["rollback_blocked_artifact"] == ""
        and status["form_before_sha256"] == empty
        and status["quote_page_before_sha256"] == empty
        and status["contact_before_sha256"] == empty
        and status["privacy"] == PRIVACY_STATUS
    ):
        raise DeploymentError("REFUSED: live state does not prove a zero-write journey transaction.")


def _require_common_status(status: dict[str, Any]) -> None:
    if not isinstance(status, dict) or set(status) != STATUS_KEYS:
        raise DeploymentError("REFUSED: fixed status projection schema changed.")
    _validate_status_types(status)
    if not (
        status["spec_sha256"] == SPECIFICATION_SHA256
        and status["source_form_id"] == SOURCE_FORM_ID
        and status["source_notification_name_match"] is True
        and all(status[key] is True for key in BACKUP_STATUS_KEYS)
        and status["receipt_schema_valid"] is True
        and status["receipt_chain_valid"] is True
        and status["receipt_append_only"] is True
    ):
        raise IndeterminateError("Plugin projection does not prove route, backups, or receipt chain.")


def require_applied_status(status: dict[str, Any], *, require_drift_free: bool = True) -> None:
    _require_common_status(status)
    if not (
        status["status"] == "applied"
        and status["form_id"] > SOURCE_FORM_ID
        and status["form_owned"] is True
        and status["page_id"] > 0
        and status["page_owned"] is True
        and status["contact_id"] == CONTACT_ID
        and status["contact_new_count"] == 1
        and status["contact_old_count"] == 0
        and status["receipt_count"] >= MIN_APPLY_RECEIPTS
        and status["receipt_head_sha256"] == status["apply_receipt_head_sha256"]
    ):
        raise IndeterminateError("Fixed activation projection does not prove every commissioned write.")
    if require_drift_free and (
            status["rollback_drift_free"] is not True
            or status["rollback_blocked_artifact"] != ""):
        raise IndeterminateError("Fixed activation projection does not prove a drift-free rollback point.")


def require_rollback_ready_status(status: dict[str, Any]) -> None:
    require_applied_status(status, require_drift_free=False)
    if not status["rollback_drift_free"] or status["rollback_blocked_artifact"]:
        raise RollbackDriftError("ROLLBACK_BLOCKED_DRIFT: preflight detected drift; nothing was clicked.")


def require_rolled_back_status(before: dict[str, Any], after: dict[str, Any]) -> None:
    _require_common_status(after)
    if not (
        after["status"] == "rolled_back"
        and after["deployment_id"] == before["deployment_id"]
        and after["route_sha256"] == before["route_sha256"]
        and after["form_id"] == before["form_id"]
        and after["form_owned"] is False
        and after["form_sha256"] == before["form_before_sha256"]
        and after["page_id"] == before["page_id"]
        and after["page_owned"] is False
        and after["page_sha256"] == before["quote_page_before_sha256"]
        and after["contact_id"] == CONTACT_ID
        and after["contact_old_count"] == 1
        and after["contact_new_count"] == 0
        and after["contact_sha256"] == before["contact_before_sha256"]
        and after["receipt_count"] > before["receipt_count"]
        and after["apply_receipt_head_sha256"] == before["receipt_head_sha256"]
        and after["receipt_head_sha256"] != before["receipt_head_sha256"]
        and after["rollback_drift_free"] is True
        and after["rollback_blocked_artifact"] == ""
    ):
        raise IndeterminateError("Internal rollback did not prove exact before hashes and chain continuity.")


def _receipt_for_row(plan: dict[str, Any], before: dict[str, Any], after: dict[str, Any],
                     status_text: str = "OK") -> None:
    append_receipt(plan, "replace", "plugin", PLUGIN_FILE,
                   before["fingerprint"], after["fingerprint"], status_text)


def _restore_old_plugin(admin: AdminPage, plan: dict[str, Any],
                        status_before: dict[str, Any], local_status: str) -> tuple[dict[str, Any], dict[str, Any]]:
    status_after = admin.click_internal_rollback(status_before)
    append_receipt(plan, "restore", "journey", status_before["deployment_id"],
                   digest_for(status_before), digest_for(status_after), "ROLLED_BACK")
    admin.goto_plugins()
    live_new = admin.read_row()
    after_final = admin.replace(
        Path(plan["rollback_artifact"]["path"]), ROLLBACK_VERSION, preserve_active=True
    )
    _receipt_for_row(plan, live_new, after_final, local_status)
    if after_final["fingerprint"] != plan["rollback_target"]["fingerprint"]:
        raise IndeterminateError("Rollback plugin privacy projection did not return to fixed 1.0.1 state.")
    return status_after, after_final


def _restore_old_binary(admin: AdminPage, plan: dict[str, Any],
                        local_status: str) -> dict[str, Any]:
    admin.goto_plugins()
    current = admin.read_row()
    if current["version"] != TO_VERSION:
        raise IndeterminateError(f"Emergency binary restore could not identify freight guard {TO_VERSION}.")
    if current["active"] is not True:
        raise IndeterminateError("Emergency binary restore refused because the freight guard is not active.")
    after_final = admin.replace(
        Path(plan["rollback_artifact"]["path"]), ROLLBACK_VERSION, preserve_active=True
    )
    _receipt_for_row(plan, current, after_final, local_status)
    if after_final["fingerprint"] != plan["rollback_target"]["fingerprint"]:
        raise IndeterminateError("Emergency binary restore did not return to fixed 1.0.1 state.")
    return after_final


@holds_wordpress_browser("WordPress: stage fixed commissioned freight quote journey")
def command_stage(_: argparse.Namespace) -> None:
    new_artifact = verify_artifact()
    rollback_artifact = verify_artifact(rollback=True)
    with admin_session() as admin:
        admin.goto_plugins()
        before = admin.read_row()
        if (before["version"] != FROM_VERSION or before["present"] is not True
                or before["active"] is not True or before["update_marker"] is not False):
            raise DeploymentError("REFUSED: stage requires the exact current active fail-closed freight guard 1.0.1 row.")
    path = write_plan(before, new_artifact, rollback_artifact)
    plan = json.loads(path.read_text(encoding="utf-8"))
    emit({
        "status": "STAGED_EVIDENCE_NO_LIVE_WRITE",
        "commission_is_final_authorization": True,
        "runtime_approval_gate": False,
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "new_artifact_sha256": ARTIFACT_SHA256,
        "rollback_artifact_sha256": ROLLBACK_ARTIFACT_SHA256,
        "spec_sha256": SPECIFICATION_SHA256,
        "plugin_before": before,
        "rollback_target": project_row(True, True, ROLLBACK_VERSION, False),
        "live_write_performed": False,
    })


@holds_wordpress_browser("WordPress: apply fixed commissioned freight quote journey")
def command_apply(args: argparse.Namespace) -> None:
    path, plan = load_plan(args.plan)
    lock = plan_apply_lock(path)
    if lock.exists() or plan_result(path).exists():
        raise DeploymentError("REFUSED: apply already entered its one allowed attempt.")
    with admin_session() as admin:
        admin.goto_plugins()
        live = admin.read_row()
        if live["fingerprint"] != plan["plugin_before"]["fingerprint"]:
            raise DeploymentError("REFUSED: fixed plugin changed after stage evidence.")
        verify_artifact(path=Path(plan["new_artifact"]["path"]))
        write_attempt_lock(lock, plan, "apply")
        stage = "replace_active_2_0_3"
        status: dict[str, Any] | None = None
        try:
            after_replace = admin.replace(
                Path(plan["new_artifact"]["path"]), TO_VERSION, preserve_active=True
            )
            _receipt_for_row(plan, live, after_replace)
            stage = "verify_zero_write_recovery_projection"
            recovery_status = admin.read_status()
            require_recovery_source_status(recovery_status)
            stage = "apply_fixed_transaction_2_0_3"
            status = admin.click_fixed_apply(recovery_status)
            stage = "verify_plugin_projection"
            transaction_hash = digest_for(status)
            append_receipt(plan, "verify", "journey", status["deployment_id"],
                           after_replace["fingerprint"], transaction_hash, "OK")
        except Exception as exc:
            reason = type(exc).__name__
            current_hash = digest_for({"stage": stage, "reason": reason})
            if stage in {"apply_fixed_transaction_2_0_3", "verify_plugin_projection"} and status is None:
                try:
                    status = admin.read_status()
                except Exception:
                    status = None
            if status is not None:
                try:
                    if status["status"] == "applied":
                        require_rollback_ready_status(status)
                        status_after, old_after = _restore_old_plugin(
                            admin, plan, status, "FAILED_CLOSED"
                        )
                    elif status["status"] in {"not_applied", "rolled_back", "absent"}:
                        status_after = status
                        old_after = _restore_old_binary(admin, plan, "FAILED_CLOSED")
                    else:
                        raise IndeterminateError("Emergency restore found an unrecognized transaction state.")
                    append_receipt(plan, "finalize", "journey", status["deployment_id"],
                                   digest_for(status), digest_for(status_after), "FAILED_CLOSED")
                    close_attempt(lock, plan, "apply", "failed_closed_rolled_back", stage,
                                  live["fingerprint"], old_after["fingerprint"])
                    write_result(plan_result(path), {
                        "status": "APPLY_FAILED_CLOSED_AND_ROLLED_BACK",
                        "operation": "apply", "stage": stage, "reason": reason,
                        "plan_sha256": plan["sha256"], "plugin_after": old_after,
                        "emergency_rollbacks": 1, "retry": False,
                        "recorded_utc": utc_now().isoformat(),
                    })
                    raise DeploymentError(
                        "Apply validation failed; one fixed emergency rollback restored 1.0.1."
                    ) from exc
                except DeploymentError as rollback_exc:
                    if str(rollback_exc).startswith("Apply validation failed"):
                        raise
                    current_hash = digest_for({
                        "stage": "emergency_rollback", "reason": type(rollback_exc).__name__,
                    })
            append_receipt(plan, "finalize", "journey", plan["nonce"],
                           live["fingerprint"], current_hash, "INDETERMINATE")
            close_attempt(lock, plan, "apply", "indeterminate", stage,
                          live["fingerprint"], current_hash)
            write_result(plan_result(path), {
                "status": "INDETERMINATE_NO_RETRY", "operation": "apply",
                "stage": stage, "reason": reason, "plan_sha256": plan["sha256"],
                "emergency_rollbacks": 0, "retry": False,
                "recorded_utc": utc_now().isoformat(),
            })
            raise IndeterminateError(
                "Apply did not prove its full transaction; plan is closed and cannot be retried."
            ) from exc
    append_receipt(plan, "finalize", "journey", status["deployment_id"],
                   live["fingerprint"], transaction_hash, "OK")
    receipt_count, receipt_head = validate_receipt_chain(plan["nonce"])
    close_attempt(lock, plan, "apply", "committed_verified", "complete",
                  live["fingerprint"], transaction_hash)
    write_result(plan_result(path), {
        "status": "APPLIED_AND_VERIFIED", "operation": "apply",
        "plan_sha256": plan["sha256"], "plugin_after": after_replace,
        "transaction_projection_sha256": transaction_hash,
        "separate_backup_presence": {key: status[key] for key in BACKUP_STATUS_KEYS},
        "plugin_receipt_count": status["receipt_count"],
        "plugin_receipt_head_sha256": status["receipt_head_sha256"],
        "local_receipt_count": receipt_count, "local_receipt_head_sha256": receipt_head,
        "recorded_utc": utc_now().isoformat(), "retry": False,
    })
    emit({
        "status": "APPLIED_AND_VERIFIED", "plan_sha256": plan["sha256"],
        "plugin_version": TO_VERSION, "active": True,
        "separate_backups_verified": True, "privacy_projection_verified": True,
        "plugin_receipt_count": status["receipt_count"],
        "plugin_receipt_head_sha256": status["receipt_head_sha256"],
        "transaction_projection_sha256": transaction_hash, "replay_locked": True,
    })


@holds_wordpress_browser("WordPress: rollback fixed commissioned freight quote journey")
def command_rollback(args: argparse.Namespace) -> None:
    path, plan = load_plan(args.plan, allow_expired_for_rollback=True)
    lock = plan_rollback_lock(path)
    if lock.exists() or plan_rollback_result(path).exists():
        raise DeploymentError("REFUSED: rollback already entered its one allowed attempt.")
    if not plan_apply_lock(path).exists():
        raise DeploymentError("REFUSED: rollback requires evidence that apply entered a write attempt.")
    with admin_session() as admin:
        admin.goto_plugins()
        live = admin.read_row()
        if live["version"] != TO_VERSION or live["active"] is not True:
            raise DeploymentError(f"REFUSED: rollback requires fixed freight guard {TO_VERSION} active.")
        status_before = admin.read_status()
        binary_only = status_before["status"] == "not_applied"
        if binary_only:
            # A failed precheck can leave only the forward binary active. Prove
            # no business artifact ever started, then restore the source guard.
            require_not_applied_zero_write_status(status_before)
        else:
            # Validate the application identity without assuming away later drift.
            # A valid drift projection is a rollback attempt outcome and must be
            # recorded under the one-attempt lock, not treated as a free lane refusal.
            require_applied_status(status_before, require_drift_free=False)
        verify_artifact(rollback=True, path=Path(plan["rollback_artifact"]["path"]))
        write_attempt_lock(lock, plan, "rollback")
        stage = "binary_only_restore_not_applied" if binary_only else "rollback_drift_preflight"
        try:
            if binary_only:
                status_after = status_before
                after_final = _restore_old_binary(admin, plan, "ROLLED_BACK")
            else:
                require_rollback_ready_status(status_before)
                stage = "internal_content_restore"
                status_after, after_final = _restore_old_plugin(admin, plan, status_before, "ROLLED_BACK")
            stage = "verify_original_plugin_projection"
            if after_final["fingerprint"] != plan["rollback_target"]["fingerprint"]:
                raise IndeterminateError("Original plugin projection did not read back exactly.")
            append_receipt(plan, "verify", "plugin", PLUGIN_FILE,
                           live["fingerprint"], after_final["fingerprint"], "ROLLED_BACK")
        except Exception as exc:
            outcome = "ROLLBACK_BLOCKED_DRIFT" if isinstance(exc, RollbackDriftError) else "INDETERMINATE"
            current_hash = digest_for({"stage": stage, "reason": type(exc).__name__})
            append_receipt(plan, "finalize", "journey", status_before["deployment_id"],
                           digest_for(status_before), current_hash, outcome)
            close_attempt(lock, plan, "rollback", outcome.casefold(), stage,
                          live["fingerprint"], current_hash)
            write_result(plan_rollback_result(path), {
                "status": outcome, "operation": "rollback", "stage": stage,
                "reason": type(exc).__name__, "plan_sha256": plan["sha256"],
                "plugin_rollback_attempted": False if outcome == "ROLLBACK_BLOCKED_DRIFT" else None,
                "recorded_utc": utc_now().isoformat(), "retry": False,
            })
            if isinstance(exc, RollbackDriftError):
                raise
            raise IndeterminateError("Rollback did not prove every fixed restore write; no retry.") from exc
    append_receipt(plan, "finalize", "journey", status_before["deployment_id"],
                   digest_for(status_before), digest_for(status_after), "ROLLED_BACK")
    receipt_count, receipt_head = validate_receipt_chain(plan["nonce"])
    close_attempt(lock, plan, "rollback", "rolled_back_verified", "complete",
                  live["fingerprint"], after_final["fingerprint"])
    write_result(plan_rollback_result(path), {
        "status": "ROLLED_BACK_AND_VERIFIED", "operation": "rollback",
        "plan_sha256": plan["sha256"], "plugin_after": after_final,
        "content_status": status_after["status"],
        "content_write_occurred": not binary_only,
        "immutable_backups_retained": all(status_after[key] for key in BACKUP_STATUS_KEYS),
        "plugin_receipt_count": status_after["receipt_count"],
        "plugin_receipt_head_sha256": status_after["receipt_head_sha256"],
        "local_receipt_count": receipt_count, "local_receipt_head_sha256": receipt_head,
        "recorded_utc": utc_now().isoformat(), "retry": False,
    })
    emit({
        "status": "ROLLED_BACK_AND_VERIFIED", "plan_sha256": plan["sha256"],
        "plugin_version": ROLLBACK_VERSION, "active": after_final["active"],
        "content_restored": not binary_only,
        "content_write_occurred": not binary_only,
        "immutable_backups_retained": all(status_after[key] for key in BACKUP_STATUS_KEYS),
        "receipt_chain_verified": True, "replay_locked": True,
    })


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stage").set_defaults(func=command_stage)
    for name, function in (("apply", command_apply), ("rollback", command_rollback)):
        command = commands.add_parser(name)
        command.add_argument("--plan", required=True)
        command.set_defaults(func=function)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (DeploymentError, UiLaneBusy, UiLaneLockError, OSError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
