#!/usr/bin/env python
"""Commissioned deployment tool for FRP Depot Media Mutation Guard.

Closed scope:
- install exactly the pinned plugin ZIP, inactive, only when absent;
- activate exactly that installed version;
- deactivate exactly that installed version as the fixed emergency path.

Every action uses an immutable authenticated 24-hour plan and a later exact
APPROVED. There is no replace, delete, arbitrary plugin, generic browser,
setting/content/user/media/product/order/customer/payment/email, retry, rollback,
or cleanup route.
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
from urllib.parse import parse_qsl, urljoin, urlsplit
import zipfile

ROOT = Path(r"C:\FRPDepot")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Dado.Tools.common.ui_lane_lock import ui_browser_lock

TOOL_NAME = "wordpress_media_guard_deployment_tool"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = 1
APPROVAL_WORD = "APPROVED"
ORIGIN = "https://frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
PLUGINS_URL = f"{ORIGIN}/wp-admin/plugins.php"
UPLOAD_URL = f"{ORIGIN}/wp-admin/plugin-install.php?tab=upload"
GUARD_URL = f"{ORIGIN}/wp-admin/tools.php?page=frpd-media-mutation-guard"
PLUGIN_NAME = "FRP Depot Media Mutation Guard"
PLUGIN_SLUG = "frpdepot-media-mutation-guard"
PLUGIN_FILE = f"{PLUGIN_SLUG}/frpdepot-media-mutation-guard.php"
PLUGIN_VERSION = "1.0.0"
ARTIFACT_PATH = ROOT / "Dado" / "Tools" / "woocommerce" / "media_mutation_guard" / f"{PLUGIN_SLUG}.zip"
ARTIFACT_SHA256 = "539cb97fbb25c5e7517bfed77562497f790f4af8c1c6b6da82754e9d8d07c5ab"
ARTIFACT_BYTES = 13508
ARTIFACT_MEMBERS = (
    f"{PLUGIN_SLUG}/approved-media.json",
    f"{PLUGIN_SLUG}/frpdepot-media-mutation-guard.php",
    f"{PLUGIN_SLUG}/readme.txt",
)
ARTIFACT_MEMBER_SHA256 = {
    f"{PLUGIN_SLUG}/approved-media.json": "2e8fdde2ba90aedb07de5bddb64a4dc4d02b82a2db88deba4605bdbfa6f18d8b",
    f"{PLUGIN_SLUG}/frpdepot-media-mutation-guard.php": "d8222383345c5590a84f35c9ee2564ac69bc899f7fabbdafa26791698bb159cc",
    f"{PLUGIN_SLUG}/readme.txt": "b84066af53580caa8a98e1b3494cfed02bf2840b7e0fe6cf4729acc3899225ac",
}
ACTIONS = frozenset({"install_inactive", "activate", "deactivate"})
PLAN_DIR = ROOT / "Dado" / "20_Working" / "wordpress_media_guard_deployment_plans"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
LOCAL_STATE = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "FRPDepot-WordPress" / "media-guard-deployment"
REGISTRY_KEY = LOCAL_STATE / "stage-registry.key"
REGISTRY_DIR = LOCAL_STATE / "stages"
ATTEMPT_DIR = LOCAL_STATE / "attempts"
RESULT_DIR = LOCAL_STATE / "results"
RUNTIME_TEMP = ROOT / "Dado" / "Temp" / "playwright-runtime"
PLAN_LIFETIME = timedelta(hours=24)
COMMIT_EXPIRY_MARGIN = timedelta(minutes=2)
NAV_TIMEOUT_MS = 45_000
ACTION_TIMEOUT_MS = 30_000
POST_WRITE_READ_ROUNDS = 3
POST_WRITE_READ_DELAY_MS = 1_000
ROW_SELECTOR = f'tr[data-plugin="{PLUGIN_FILE}"]'
ACTIVATE_SELECTOR = f"#activate-{PLUGIN_SLUG}"
DEACTIVATE_SELECTOR = f"#deactivate-{PLUGIN_SLUG}"
VERSION_PATTERN = re.compile(r"\bVersion\s+([0-9]+(?:\.[0-9]+){1,3})\b", re.I)
PLAN_KEYS = frozenset({
    "schema_version", "tool", "tool_version", "origin", "action", "created_utc",
    "expires_utc", "nonce", "operation_sha256", "plugin_name", "plugin_slug",
    "plugin_file", "artifact", "before", "after_expected", "writes_if_committed",
    "risk", "forbidden", "sha256",
})
ROW_KEYS = frozenset({"present", "active", "version", "update_marker", "plugin_file", "fingerprint"})


class DeploymentError(RuntimeError):
    pass


class IndeterminateError(DeploymentError):
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
        raise DeploymentError("Approval must be the exact unpadded uppercase word APPROVED.")


def is_reparse(path: Path) -> bool:
    try:
        result = path.lstat()
    except OSError:
        return True
    return stat.S_ISLNK(result.st_mode) or bool(
        getattr(result, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def validate_artifact_payload() -> tuple[dict[str, Any], bytes]:
    fixed = ARTIFACT_PATH
    if is_reparse(fixed):
        raise DeploymentError("REFUSED: fixed plugin artifact is missing or aliased.")
    try:
        path = fixed.resolve(strict=True)
    except OSError as exc:
        raise DeploymentError("REFUSED: fixed plugin artifact is missing or aliased.") from exc
    absolute = Path(os.path.abspath(fixed))
    if os.path.normcase(str(path)) != os.path.normcase(str(absolute)) or not path.is_file():
        raise DeploymentError("REFUSED: fixed plugin artifact is missing or aliased.")
    try:
        with path.open("rb") as handle:
            opened_before = os.fstat(handle.fileno())
            if getattr(opened_before, "st_nlink", 1) != 1:
                raise DeploymentError("REFUSED: fixed plugin artifact is missing or aliased.")
            raw = handle.read()
            opened_after = os.fstat(handle.fileno())
        named_after = path.stat()
    except OSError as exc:
        raise DeploymentError("REFUSED: fixed plugin artifact is unreadable.") from exc
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, getattr(item, "st_nlink", 1))
    if identity(opened_before) != identity(opened_after) or identity(opened_after) != identity(named_after):
        raise DeploymentError("REFUSED: fixed plugin artifact changed while it was opened.")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != ARTIFACT_SHA256 or len(raw) != ARTIFACT_BYTES:
        raise DeploymentError("REFUSED: fixed plugin artifact bytes changed.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = tuple(sorted(archive.namelist()))
            payloads = {name: archive.read(name) for name in members}
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise DeploymentError("REFUSED: fixed plugin artifact is unreadable.") from exc
    if members != tuple(sorted(ARTIFACT_MEMBERS)):
        raise DeploymentError("REFUSED: fixed plugin artifact member set changed.")
    member_hashes = {name: hashlib.sha256(payloads[name]).hexdigest() for name in members}
    if member_hashes != ARTIFACT_MEMBER_SHA256:
        raise DeploymentError("REFUSED: fixed plugin artifact member bytes changed.")
    php = payloads[f"{PLUGIN_SLUG}/frpdepot-media-mutation-guard.php"].decode("utf-8", errors="strict")
    if f"Plugin Name: {PLUGIN_NAME}" not in php or f"Version: {PLUGIN_VERSION}" not in php:
        raise DeploymentError("REFUSED: fixed plugin artifact identity/version changed.")
    artifact = {
        "path": str(path), "sha256": digest, "bytes": len(raw), "version": PLUGIN_VERSION,
        "members": list(members), "member_sha256": member_hashes,
    }
    return artifact, raw


def validate_artifact() -> dict[str, Any]:
    artifact, _raw = validate_artifact_payload()
    return artifact


def assert_admin_url(url: str, *, mode: str) -> None:
    parsed = urlsplit(str(url or ""))
    expected = urlsplit(PLUGINS_URL)
    if parsed.scheme != "https" or parsed.hostname != expected.hostname or parsed.port not in (None, 443):
        raise DeploymentError("REFUSED: browser left the fixed WordPress origin.")
    if parsed.username or parsed.password or parsed.fragment:
        raise DeploymentError("REFUSED: WordPress admin URL is ambiguous.")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if len({key for key, _value in pairs}) != len(pairs):
        raise DeploymentError("REFUSED: WordPress admin URL repeats a query key.")
    if mode == "plugins":
        valid = parsed.path == "/wp-admin/plugins.php" and pairs == []
    elif mode == "guard":
        valid = parsed.path == "/wp-admin/tools.php" and pairs == [("page", "frpd-media-mutation-guard")]
    elif mode == "upload":
        valid = parsed.path == "/wp-admin/plugin-install.php" and len(pairs) in (1, 2)
        valid = valid and pairs[0] == ("tab", "upload")
        if valid and len(pairs) == 2:
            valid = pairs[1][0] == "_dado_refresh" and bool(re.fullmatch(r"[0-9a-f]{32}", pairs[1][1]))
    elif mode == "install_result":
        valid = (parsed.path == "/wp-admin/update.php" and pairs == [("action", "upload-plugin")]) \
            or (parsed.path == "/wp-admin/plugins.php" and pairs == [])
    elif mode == "install_submit":
        valid = parsed.path == "/wp-admin/update.php" and pairs == [("action", "upload-plugin")]
    elif mode == "state_result":
        valid = parsed.path == "/wp-admin/plugins.php" and pairs == []
        if parsed.path == "/wp-admin/plugins.php" and len(pairs) == 4:
            values = dict(pairs)
            flags = [name for name in ("activate", "deactivate") if name in values]
            valid = len(flags) == 1 and set(values) == {flags[0], "plugin_status", "paged", "s"}
            valid = valid and values[flags[0]] == "true" and values["plugin_status"] == "all"
            valid = valid and values["paged"] == "1" and values["s"] == ""
    else:
        raise DeploymentError("REFUSED: URL validation mode is not fixed.")
    if not valid:
        raise DeploymentError("REFUSED: WordPress administration URL is outside the exact fixed route.")


def assert_state_action_url(url: str, action: str) -> None:
    if action not in ("activate", "deactivate"):
        raise DeploymentError("REFUSED: plugin state action is not fixed.")
    absolute = urljoin(f"{ORIGIN}/wp-admin/", str(url or ""))
    parsed = urlsplit(absolute)
    expected = urlsplit(PLUGINS_URL)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(pairs)
    valid = parsed.scheme == "https" and parsed.hostname == expected.hostname and parsed.port in (None, 443)
    valid = valid and not parsed.username and not parsed.password and not parsed.fragment
    valid = valid and parsed.path == "/wp-admin/plugins.php" and len(values) == len(pairs)
    valid = valid and set(values) == {"action", "plugin", "plugin_status", "paged", "s", "_wpnonce"}
    valid = valid and values.get("action") == action and values.get("plugin") == PLUGIN_FILE
    valid = valid and values.get("plugin_status") == "all" and values.get("paged") == "1" and values.get("s") == ""
    valid = valid and bool(re.fullmatch(r"[A-Za-z0-9_-]{8,32}", values.get("_wpnonce", "")))
    if not valid:
        raise DeploymentError("REFUSED: fixed plugin action URL is ambiguous or outside scope.")


def ensure_runtime_temp() -> None:
    names = ("TMP", "TEMP", "TMPDIR")
    if not all(os.environ.get(name) and Path(os.environ[name]).is_dir() for name in names):
        RUNTIME_TEMP.mkdir(parents=True, exist_ok=True)
        stable = str(RUNTIME_TEMP.resolve())
        for name in names:
            os.environ[name] = stable


def row_fingerprint(row: dict[str, Any]) -> str:
    return digest_for({key: row[key] for key in ("present", "active", "version", "update_marker", "plugin_file")})


def project_row(present: bool, active: bool | None, version: str, update_marker: bool = False) -> dict[str, Any]:
    row = {"present": bool(present), "active": active, "version": str(version),
           "update_marker": bool(update_marker), "plugin_file": PLUGIN_FILE}
    row["fingerprint"] = row_fingerprint(row)
    return row


def expected_after(action: str) -> dict[str, Any]:
    if action == "install_inactive":
        return project_row(True, False, PLUGIN_VERSION, False)
    if action == "activate":
        return project_row(True, True, PLUGIN_VERSION, False)
    if action == "deactivate":
        return project_row(True, False, PLUGIN_VERSION, False)
    raise DeploymentError("REFUSED: action is not fixed.")


class AdminPage:
    def __init__(self, page: Any):
        self.page = page

    def goto(self, url: str) -> None:
        mode = "guard" if url == GUARD_URL else "plugins"
        assert_admin_url(url, mode=mode)
        self.page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(str(self.page.url), mode=mode)

    def goto_plugins(self) -> None:
        self.goto(PLUGINS_URL)

    def goto_upload(self) -> None:
        url = f"{UPLOAD_URL}&_dado_refresh={secrets.token_hex(16)}"
        assert_admin_url(url, mode="upload")
        self.page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(str(self.page.url), mode="upload")

    def rows(self) -> list[Any]:
        rows = self.page.query_selector_all(ROW_SELECTOR)
        if len(rows) > 1:
            raise DeploymentError("REFUSED: fixed plugin row is ambiguous.")
        return rows

    def read_row(self, *, allow_absent: bool = False) -> dict[str, Any]:
        rows = self.rows()
        if not rows:
            if allow_absent:
                return project_row(False, None, "", False)
            raise DeploymentError("REFUSED: fixed plugin is not installed.")
        row = rows[0]
        tokens = set(str(row.get_attribute("class") or "").split())
        active = "active" in tokens
        inactive = "inactive" in tokens
        if active == inactive:
            raise DeploymentError("REFUSED: fixed plugin state is ambiguous.")
        has_activate = row.query_selector(ACTIVATE_SELECTOR) is not None
        has_deactivate = row.query_selector(DEACTIVATE_SELECTOR) is not None
        if has_activate == has_deactivate or active != has_deactivate:
            raise DeploymentError("REFUSED: fixed plugin row actions disagree with state.")
        text = str(row.inner_text() or "")
        match = VERSION_PATTERN.search(text)
        if not match:
            raise DeploymentError("REFUSED: fixed plugin version is unreadable.")
        update_rows = self.page.query_selector_all(f"tr.plugin-update-tr[data-plugin='{PLUGIN_FILE}']")
        if len(update_rows) > 1:
            raise DeploymentError("REFUSED: fixed plugin update marker is ambiguous.")
        return project_row(True, active, match.group(1), bool(update_rows) or "update" in tokens)

    def prepare_install(self) -> tuple[Any, Any]:
        self.goto_upload()
        forms = self.page.query_selector_all("form#plugin-upload-form")
        if len(forms) != 1:
            raise DeploymentError("REFUSED: exact fixed plugin upload form is unavailable.")
        form = forms[0]
        assert_admin_url(urljoin(UPLOAD_URL, str(form.get_attribute("action") or "")), mode="install_submit")
        choosers = form.query_selector_all('input[type="file"][name="pluginzip"]')
        submits = form.query_selector_all("#install-plugin-submit")
        nonces = form.query_selector_all('input[type="hidden"][name="_wpnonce"]')
        nonce = str(nonces[0].get_attribute("value") or "") if len(nonces) == 1 else ""
        if len(choosers) != 1 or len(submits) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{8,32}", nonce):
            raise DeploymentError("REFUSED: exact fixed plugin upload controls are unavailable.")
        return choosers[0], submits[0]

    def execute_install(self, chooser: Any, submit: Any, artifact_raw: bytes) -> dict[str, Any]:
        if len(artifact_raw) != ARTIFACT_BYTES or hashlib.sha256(artifact_raw).hexdigest() != ARTIFACT_SHA256:
            raise DeploymentError("REFUSED: validated in-memory plugin artifact changed.")
        chooser.set_input_files({
            "name": f"{PLUGIN_SLUG}.zip", "mimeType": "application/zip", "buffer": artifact_raw,
        }, timeout=ACTION_TIMEOUT_MS)
        submit.click(timeout=ACTION_TIMEOUT_MS)
        self.page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(str(self.page.url), mode="install_result")
        if self.page.query_selector_all("table.update-from-upload-comparison") or self.page.query_selector_all("a.update-from-upload-overwrite"):
            raise IndeterminateError("Fresh install unexpectedly reached a replace route; no overwrite clicked.")
        return self.read_bounded(expected_after("install_inactive"))

    def prepare_state_click(self, action: str) -> Any:
        before = self.read_row()
        required_active = action == "deactivate"
        if before != project_row(True, required_active, PLUGIN_VERSION, False):
            raise DeploymentError("REFUSED: fixed plugin is not in the exact staged state.")
        selector = DEACTIVATE_SELECTOR if action == "deactivate" else ACTIVATE_SELECTOR
        link = self.rows()[0].query_selector(selector)
        if link is None:
            raise DeploymentError("REFUSED: fixed plugin action is unavailable.")
        assert_state_action_url(str(link.get_attribute("href") or ""), action)
        return link

    def execute_state_click(self, action: str, link: Any) -> dict[str, Any]:
        link.click(timeout=ACTION_TIMEOUT_MS)
        self.page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(str(self.page.url), mode="state_result")
        return self.read_bounded(expected_after(action))

    def read_bounded(self, wanted: dict[str, Any]) -> dict[str, Any]:
        for index in range(POST_WRITE_READ_ROUNDS):
            self.goto_plugins()
            observed = self.read_row(allow_absent=True)
            if observed == wanted:
                return observed
            if index + 1 < POST_WRITE_READ_ROUNDS:
                self.page.wait_for_timeout(POST_WRITE_READ_DELAY_MS)
        raise IndeterminateError("Fixed plugin did not read back in the exact expected state.")

    def verify_guard_health(self) -> dict[str, Any]:
        errors: list[str] = []
        def on_console(message: Any) -> None:
            if str(getattr(message, "type", "")) == "error":
                errors.append("console_error")
        def on_page_error(error: Any) -> None:
            errors.append("page_error")
        self.page.on("console", on_console)
        self.page.on("pageerror", on_page_error)
        self.goto(GUARD_URL)
        self.page.wait_for_timeout(500)
        version = str(self.page.locator("#frpd-mg-version").inner_text(timeout=ACTION_TIMEOUT_MS) or "").strip()
        status = str(self.page.locator("#frpd-mg-status").inner_text(timeout=ACTION_TIMEOUT_MS) or "").strip()
        if errors or version != f"Version {PLUGIN_VERSION}" or status != "Guard inactive":
            raise IndeterminateError("Active media guard plugin health page is not exact and clean.")
        return {"url": "/wp-admin/tools.php?page=frpd-media-mutation-guard",
                "version": PLUGIN_VERSION, "guard_active": False, "javascript_errors": 0}


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
            raise DeploymentError("Authenticated WordPress browser is unavailable.") from exc
        if not browser.contexts or not browser.contexts[0].pages:
            raise DeploymentError("Authenticated WordPress browser has no open page.")
        yield AdminPage(browser.contexts[0].pages[0])


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
        raise DeploymentError("REFUSED: local stage-registry key is missing or aliased.")
    key = REGISTRY_KEY.read_bytes()
    if len(key) != 32:
        raise DeploymentError("REFUSED: local stage-registry key is invalid.")
    return key


def registry_mac(core: dict[str, Any]) -> str:
    return hmac.new(registry_key(), canonical(core).encode("ascii"), hashlib.sha256).hexdigest()


def exclusive_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise DeploymentError("REFUSED: immutable evidence already exists; no replay or overwrite.") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload); handle.flush(); os.fsync(handle.fileno())


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": utc_now().isoformat(), "action": action, "evidence": evidence}
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def operation_sha(action: str, artifact: dict[str, Any], before: dict[str, Any]) -> str:
    return digest_for({
        "schema_version": SCHEMA_VERSION, "tool": TOOL_NAME, "action": action,
        "plugin_file": PLUGIN_FILE, "artifact_sha256": artifact["sha256"],
        "artifact_member_sha256": artifact["member_sha256"], "before": before,
        "after_expected": expected_after(action),
    })


def plan_path(created: datetime, action: str, plan_sha: str) -> Path:
    return (PLAN_DIR / f"{created.strftime('%Y%m%dT%H%M%SZ')}_{action}_{plan_sha[:16]}.json").resolve()


def stage_registry_path(plan_sha: str) -> Path:
    return REGISTRY_DIR / f"{plan_sha}.json"


def attempt_path(operation: str) -> Path:
    return ATTEMPT_DIR / f"{operation}.json"


def result_path(operation: str) -> Path:
    return RESULT_DIR / f"{operation}.json"


def validate_before(action: str, before: dict[str, Any]) -> None:
    if set(before) != ROW_KEYS:
        raise DeploymentError("REFUSED: live plugin projection is invalid.")
    if action == "install_inactive" and before != project_row(False, None, "", False):
        raise DeploymentError("REFUSED: fixed plugin must be absent for install; replace is unreachable.")
    if action == "activate" and before != project_row(True, False, PLUGIN_VERSION, False):
        raise DeploymentError("REFUSED: activation requires exact installed inactive version without update marker.")
    if action == "deactivate" and before != project_row(True, True, PLUGIN_VERSION, False):
        raise DeploymentError("REFUSED: deactivation requires exact installed active version without update marker.")


def stage(action: str, before: dict[str, Any], artifact: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    validate_before(action, before)
    created = utc_now()
    operation = operation_sha(action, artifact, before)
    core = {
        "schema_version": SCHEMA_VERSION, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "origin": ORIGIN, "action": action, "created_utc": created.isoformat(),
        "expires_utc": (created + PLAN_LIFETIME).isoformat(), "nonce": secrets.token_hex(16),
        "operation_sha256": operation, "plugin_name": PLUGIN_NAME, "plugin_slug": PLUGIN_SLUG,
        "plugin_file": PLUGIN_FILE, "artifact": artifact, "before": before,
        "after_expected": expected_after(action),
        "writes_if_committed": [{
            "install_inactive": "one fixed plugin ZIP upload; plugin remains inactive",
            "activate": "one fixed plugin activation click; its hook creates and verifies one fixed InnoDB guard-state table",
            "deactivate": "one fixed plugin deactivation click",
        }[action]],
        "risk": "One attempt only. A write may land even if verification becomes indeterminate. No retry, replace, delete, rollback, or cleanup route exists. Runtime locking requires one authoritative MySQL server with no split, proxy multiplexing, pooled connection ownership change, or independent primary. As an ordinary plugin it cannot stop direct database/filesystem mutation, malicious PHP, or privileged plugin disable/replace.",
        "forbidden": ["replace", "delete", "retry", "rollback", "cleanup", "arbitrary plugin",
                      "generic browser", "setting", "content", "user", "media", "product", "order",
                      "customer", "payment", "email", "Zoho", "Drive"],
    }
    plan = {**core, "sha256": digest_for(core)}
    path = plan_path(created, action, plan["sha256"])
    exclusive_json(path, plan)
    raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    registry_core = {
        "schema_version": SCHEMA_VERSION, "tool_version": TOOL_VERSION,
        "plan_sha256": plan["sha256"], "plan_file_sha256": raw_hash,
        "plan_path": str(path), "operation_sha256": operation, "action": action,
        "nonce": plan["nonce"], "created_utc": plan["created_utc"], "expires_utc": plan["expires_utc"],
    }
    exclusive_json(stage_registry_path(plan["sha256"]), {**registry_core, "hmac_sha256": registry_mac(registry_core)})
    append_receipt("wordpress_media_guard_deployment_plan_staged", str(path))
    return path, plan


def fixed_plan_path(raw: str) -> Path:
    path = Path(raw).resolve()
    if (path.parent != PLAN_DIR.resolve() or path.suffix.lower() != ".json" or not path.is_file()
            or is_reparse(path) or getattr(path.stat(), "st_nlink", 1) != 1):
        raise DeploymentError("REFUSED: plan is not a regular canonical file in the fixed plan folder.")
    return path


def load_plan(raw_path: str) -> tuple[Path, dict[str, Any]]:
    path = fixed_plan_path(raw_path)
    raw = path.read_bytes()
    try:
        plan = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError("REFUSED: plan is unreadable.") from exc
    if not isinstance(plan, dict) or set(plan) != PLAN_KEYS:
        raise DeploymentError("REFUSED: plan schema is not exact.")
    core = dict(plan); saved = str(core.pop("sha256", ""))
    if not secrets.compare_digest(saved, digest_for(core)):
        raise DeploymentError("REFUSED: plan hash failed.")
    if (plan["schema_version"] != SCHEMA_VERSION or plan["tool"] != TOOL_NAME
            or plan["tool_version"] != TOOL_VERSION or plan["origin"] != ORIGIN
            or plan["action"] not in ACTIONS or plan["plugin_name"] != PLUGIN_NAME
            or plan["plugin_slug"] != PLUGIN_SLUG or plan["plugin_file"] != PLUGIN_FILE):
        raise DeploymentError("REFUSED: plan identity is invalid.")
    created = datetime.fromisoformat(str(plan["created_utc"])); expires = datetime.fromisoformat(str(plan["expires_utc"]))
    if created.tzinfo is None or expires.tzinfo is None or expires - created != PLAN_LIFETIME or utc_now() > expires:
        raise DeploymentError("REFUSED: plan is expired or has an invalid lifetime.")
    expected_name = plan_path(created.astimezone(timezone.utc), plan["action"], saved).name
    if path.name != expected_name:
        raise DeploymentError("REFUSED: plan filename is not canonical.")
    artifact = validate_artifact()
    if plan["artifact"] != artifact or plan["after_expected"] != expected_after(plan["action"]):
        raise DeploymentError("REFUSED: plan artifact or expected state changed.")
    validate_before(plan["action"], plan["before"])
    operation = operation_sha(plan["action"], artifact, plan["before"])
    if not secrets.compare_digest(plan["operation_sha256"], operation):
        raise DeploymentError("REFUSED: stable operation identity is invalid.")
    registry = json.loads(stage_registry_path(saved).read_text(encoding="ascii"))
    registry_core = {
        "schema_version": SCHEMA_VERSION, "tool_version": TOOL_VERSION,
        "plan_sha256": saved, "plan_file_sha256": hashlib.sha256(raw).hexdigest(),
        "plan_path": str(path), "operation_sha256": operation, "action": plan["action"],
        "nonce": plan["nonce"], "created_utc": plan["created_utc"], "expires_utc": plan["expires_utc"],
    }
    if (not isinstance(registry, dict) or set(registry) != set(registry_core) | {"hmac_sha256"}
            or {key: registry.get(key) for key in registry_core} != registry_core
            or not isinstance(registry.get("hmac_sha256"), str)
            or not hmac.compare_digest(registry["hmac_sha256"], registry_mac(registry_core))):
        raise DeploymentError("REFUSED: authenticated stage registry failed.")
    plan["sha256"] = saved
    return path, plan


def assert_commit_execution_window(plan: dict[str, Any]) -> None:
    expires = datetime.fromisoformat(str(plan.get("expires_utc", "")))
    if expires.tzinfo is None or utc_now() + COMMIT_EXPIRY_MARGIN >= expires:
        raise DeploymentError(
            "REFUSED: plan has insufficient authorization time remaining; stage a fresh plan."
        )


def command_stage(args: argparse.Namespace) -> None:
    action = args.action
    artifact = validate_artifact()
    with admin_session(f"WordPress media guard read-only stage: {action}") as admin:
        admin.goto_plugins()
        before = admin.read_row(allow_absent=True)
        if action == "deactivate":
            admin.verify_guard_health()
    path, plan = stage(action, before, artifact)
    print_json({
        "status": "STAGED_READ_ONLY", "plan": str(path), "plan_sha256": plan["sha256"],
        "operation_sha256": plan["operation_sha256"], "action": action, "before": before,
        "after_expected": plan["after_expected"], "risk": plan["risk"],
        "website_writes": 0, "approval_required": APPROVAL_WORD,
    })


def command_commit(args: argparse.Namespace) -> None:
    require_approval(args.approval)
    path, plan = load_plan(args.plan)
    action = plan["action"]
    operation = plan["operation_sha256"]
    if attempt_path(operation).exists() or result_path(operation).exists():
        raise DeploymentError("REFUSED: stable operation is permanently replay-locked.")
    artifact_raw: bytes | None = None
    if action == "install_inactive":
        current_artifact, artifact_raw = validate_artifact_payload()
        if current_artifact != plan["artifact"]:
            raise DeploymentError("REFUSED: fixed plugin artifact changed after plan loading.")
    result: dict[str, Any]
    locked = False
    try:
        with admin_session(f"WordPress media guard commit: {action}") as admin:
            admin.goto_plugins()
            live = admin.read_row(allow_absent=True)
            if live != plan["before"]:
                raise DeploymentError("REFUSED: fixed plugin state changed after staging; stage a fresh plan.")
            if action == "install_inactive":
                chooser, control = admin.prepare_install()
            else:
                if action == "deactivate":
                    admin.verify_guard_health()
                    admin.goto_plugins()
                control = admin.prepare_state_click(action)
                chooser = None
            assert_commit_execution_window(plan)
            exclusive_json(attempt_path(operation), {
                "schema": 1, "tool": TOOL_NAME, "action": action, "operation_sha256": operation,
                "plan_sha256": plan["sha256"], "locked_utc": utc_now().isoformat(),
                "status": "attempt_started_no_retry",
            })
            locked = True
            if action == "install_inactive":
                if artifact_raw is None:
                    raise DeploymentError("REFUSED: exact in-memory plugin artifact is unavailable.")
                after = admin.execute_install(chooser, control, artifact_raw)
                health = None
            else:
                after = admin.execute_state_click(action, control)
                health = admin.verify_guard_health() if action == "activate" else None
            result = {
                "schema": 1, "tool": TOOL_NAME, "action": action, "operation_sha256": operation,
                "plan_sha256": plan["sha256"], "status": "verified", "completed_utc": utc_now().isoformat(),
                "before": plan["before"], "after": after, "guard_health": health,
                "writes": 1, "emails": 0,
            }
            exclusive_json(result_path(operation), result)
    except Exception as exc:
        if locked:
            result = {
                "schema": 1, "tool": TOOL_NAME, "action": action, "operation_sha256": operation,
                "plan_sha256": plan["sha256"], "status": "indeterminate_no_retry",
                "failed_utc": utc_now().isoformat(), "error_type": type(exc).__name__,
                "error": str(exc),
            }
            try:
                exclusive_json(result_path(operation), result)
            except DeploymentError:
                pass
        raise
    append_receipt(f"wordpress_media_guard_{action}_verified", str(result_path(operation)))
    print_json(result)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    stage_parser = sub.add_parser("stage")
    stage_parser.add_argument("--action", required=True, choices=sorted(ACTIONS))
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
    except DeploymentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
