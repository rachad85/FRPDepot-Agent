#!/usr/bin/env python
"""Commissioned deployment tool for FRP Depot Media Mutation Guard.

Closed scope:
- install exactly the pinned plugin ZIP, inactive, only when absent;
- activate exactly that installed version;
- deactivate exactly that installed version as the fixed emergency path.
- replace the exact active, healthy, inactive-state v1.0.5 guard once with exact v1.0.7.

Every action uses an immutable authenticated 24-hour plan and a later exact
APPROVED. There is no arbitrary replace, delete, arbitrary plugin, generic browser,
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
TOOL_VERSION = "1.7.0"
SCHEMA_VERSION = 11
APPROVAL_WORD = "APPROVED"
ORIGIN = "https://frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
PLUGINS_URL = f"{ORIGIN}/wp-admin/plugins.php"
UPLOAD_URL = f"{ORIGIN}/wp-admin/plugin-install.php?tab=upload"
GUARD_URL = f"{ORIGIN}/wp-admin/tools.php?page=frpd-media-mutation-guard"
PLUGIN_NAME = "FRP Depot Media Mutation Guard"
PLUGIN_SLUG = "frpdepot-media-mutation-guard"
PLUGIN_FILE = f"{PLUGIN_SLUG}/frpdepot-media-mutation-guard.php"
# The exact live version this build may replace, and the exact version it
# installs. 1.0.6 was BUILT, independently reviewed, REJECTED and never deployed:
# its bytes stay on disk unchanged as evidence and are refused here by hash.
CURRENT_PLUGIN_VERSION = "1.0.5"
WITHDRAWN_PLUGIN_VERSION = "1.0.5"
PLUGIN_VERSION = "1.0.7"
WITHDRAWN_ARTIFACTS = {
    "6a753c570d167075b8fa0a66349ab0a812aa7e222a7aedb2f6d374b913a7010e":
        "WITHDRAWN_NOT_DEPLOYED_NOT_STAGEABLE",
}
WITHDRAWN_ARTIFACT_VERSIONS = ("1.0.6",)
# Every plan and stable operation staged against an earlier artifact is permanently
# superseded and can never commit. The schema/tool-version bump alone already
# invalidates them; these sets state it explicitly so a stale file cannot be
# mistaken for a live authorization.
SUPERSEDED_PLAN_SHA256 = frozenset({
    "03b55733fdb767cc015be652a625e822cb5f4fb87df891d4a36f2371c2022367",
    "31e5fd889d25c3dc9ee1dbb1f0f8e74e527808b5b24cb92e12aebddaaafa76ac",
    "3fa113af990f34eb48e66975d7aadbade9b4e05a0563f06c03e7c1e937eec702",
    "4c10d1036a9248ac7941d40ac89f0211b833cb8ae2312739d5a2a4ea05126780",
    "5c45ac727f1d818c805f1f0da961af093ecb01839171da149b4bb842fa8018c7",
    "9bb2784da86697a61170ec861281e883c0e3cb6374f39b44b87665c57f44ab61",
    "dbb7382dcd731ae22c22354bac4f304e0304ccb7543939364c847443aa349223",
    "def4afe80095cc8ac6a711ef284e9c090c97feb2a69594789161f5b4b327adce",
})
SUPERSEDED_OPERATION_SHA256 = frozenset({
    "2805cd9c36499c8aef6d8985b307796bcf102d25f9bd23de7a65c9271a621fd6",
    "3ed56d4c3db984f468b0fee0539c358a598a3c93361f491b0e9b961058490c3c",
    "6e4b06c9d7ca1fcb3f3ad52853d9b2d6d64934753ef61e4c0689b6f60a5a4b2d",
    "743a22c75b78a376734057f0ac7471c7b0ee37b0344919bb199ee7b61c357064",
    "a78074ee9554727473a92fa39d45ec93ae2684ebb710de4c23d51921224dc737",
    "c11c56f47ac7c95f585496816766ec37de9aa3b0606b7b6c2ac01e08777ae505",
    "ed562dd452bfe65532eb80f57447c0750a8305082861420daaa2c4e54c0886ff",
})
ARTIFACT_PATH = ROOT / "Dado" / "Tools" / "woocommerce" / "media_mutation_guard" / f"{PLUGIN_SLUG}-1.0.7.zip"
ARTIFACT_SHA256 = "a1f6bf204e443dea9008699abcaf96e7da868a894a5f569215c572c9963ab2d1"
ARTIFACT_BYTES = 35656
ARTIFACT_MEMBERS = (
    f"{PLUGIN_SLUG}/approved-media.json",
    f"{PLUGIN_SLUG}/frpdepot-media-mutation-guard.php",
    f"{PLUGIN_SLUG}/readme.txt",
)
ARTIFACT_MEMBER_SHA256 = {
    f"{PLUGIN_SLUG}/approved-media.json": "adb9b81f7a8e55205c7224af6005c0c386ec833eef36be7281dca96313e9d900",
    f"{PLUGIN_SLUG}/frpdepot-media-mutation-guard.php": "87209d942828f2042c26225f48ebe18c91a336dbed1411a102290a3dbf1623bf",
    f"{PLUGIN_SLUG}/readme.txt": "336447756e6a51c18cd699033f517d7f352102ceeeb492ae480bfbd212aa6e22",
}
ARTIFACT_MEMBER_BYTES = {
    f"{PLUGIN_SLUG}/approved-media.json": 5910,
    f"{PLUGIN_SLUG}/frpdepot-media-mutation-guard.php": 146101,
    f"{PLUGIN_SLUG}/readme.txt": 7469,
}
# Non-secret proof, read from the live guard page, that the installed build really
# carries the one literal Open Manway recovery contract. It is compared exactly.
GUARD_CAPABILITY_SELECTOR = "#frpd-mg-capability"
EXPECTED_GUARD_MANIFEST_SHA256 = "adb9b81f7a8e55205c7224af6005c0c386ec833eef36be7281dca96313e9d900"
EXPECTED_GUARD_STATE_SCHEMA = 3
EXPECTED_GUARD_PROOF_SCHEMA = 3
EXPECTED_GUARD_CAPABILITY = {
    "schema": EXPECTED_GUARD_PROOF_SCHEMA,
    "plugin_version": PLUGIN_VERSION,
    "state_schema": EXPECTED_GUARD_STATE_SCHEMA,
    "proof_schema": EXPECTED_GUARD_PROOF_SCHEMA,
    "manifest_sha256": EXPECTED_GUARD_MANIFEST_SHA256,
    "families": ["elbow_90", "manway_cover", "open_manway", "pipe", "stub_flange"],
    "fixed_reuse_family": "stub_flange",
    "fixed_recovery": {
        "contract": "open_manway_recovery",
        "family": "open_manway",
        "product_id": 1397,
        "position": 1,
        "attachment_id": 7609,
        "filename": "01_manway_premium_hero.png",
        "prior_operation_sha256":
            "e0127fcaa04c023cbdd19a36726d6e8f03c3fb01f12f0367550d17c87674dc85",
        "recoverable_positions": [2, 3, 4, 5, 6],
    },
    "capabilities": {
        "existing_fixed_attachment_acquisition": True,
        "non_prefix_upload_reservation": True,
        "origin_only_file_enumeration": True,
        "owner_bound_gallery_commit": True,
    },
}
ACTIONS = frozenset({"install_inactive", "replace_active", "activate", "deactivate"})
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
ACTION_ID_SLUG = "frp-depot-media-mutation-guard"
ACTIVATE_SELECTOR = f"#activate-{ACTION_ID_SLUG}"
DEACTIVATE_SELECTOR = f"#deactivate-{ACTION_ID_SLUG}"
UPLOAD_FORM_SELECTOR = "form.wp-upload-form"
OVERWRITE_SELECTOR = "a.update-from-upload-overwrite"
OVERWRITE_SUCCESS_MARKER = "Plugin updated successfully."
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
    member_bytes = {name: len(payloads[name]) for name in members}
    if member_hashes != ARTIFACT_MEMBER_SHA256 or member_bytes != ARTIFACT_MEMBER_BYTES:
        raise DeploymentError("REFUSED: fixed plugin artifact member bytes changed.")
    php = payloads[f"{PLUGIN_SLUG}/frpdepot-media-mutation-guard.php"].decode("utf-8", errors="strict")
    if f"Plugin Name: {PLUGIN_NAME}" not in php or f"Version: {PLUGIN_VERSION}" not in php:
        raise DeploymentError("REFUSED: fixed plugin artifact identity/version changed.")
    for withdrawn in WITHDRAWN_ARTIFACT_VERSIONS:
        if f"Version: {withdrawn}" in php:
            raise DeploymentError(
                f"REFUSED: this artifact carries the WITHDRAWN v{withdrawn} plugin version."
            )
    if digest in WITHDRAWN_ARTIFACTS:
        raise DeploymentError(
            "REFUSED: this artifact is classified "
            f"{WITHDRAWN_ARTIFACTS[digest]} and can never be staged or deployed."
        )
    if (f"define('FRPD_MG_STATE_SCHEMA', {EXPECTED_GUARD_STATE_SCHEMA});" not in php
            or f"define('FRPD_MG_PROOF_SCHEMA', {EXPECTED_GUARD_PROOF_SCHEMA});" not in php
            or f"define('FRPD_MG_MANIFEST_SHA256', '{EXPECTED_GUARD_MANIFEST_SHA256}');"
                not in php):
        raise DeploymentError(
            "REFUSED: fixed plugin artifact state/proof schema or manifest digest is not "
            "the pinned one."
        )
    artifact = {
        "path": str(path), "sha256": digest, "bytes": len(raw), "version": PLUGIN_VERSION,
        "members": list(members), "member_sha256": member_hashes, "member_bytes": member_bytes,
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
        if parsed.path == "/wp-admin/plugins.php" and len(pairs) == 3:
            valid = pairs == [("plugin_status", "all"), ("paged", "1"), ("s", "")]
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
    if action == "replace_active":
        return project_row(True, True, PLUGIN_VERSION, False)
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
        forms = self.page.query_selector_all(UPLOAD_FORM_SELECTOR)
        if len(forms) != 1:
            raise DeploymentError("REFUSED: exact fixed plugin upload form is unavailable.")
        form = forms[0]
        assert_admin_url(urljoin(UPLOAD_URL, str(form.get_attribute("action") or "")), mode="install_submit")
        choosers = form.query_selector_all('input[type="file"][name="pluginzip"]')
        submits = form.query_selector_all("#install-plugin-submit")
        nonces = form.query_selector_all('input[type="hidden"][name="_wpnonce"]')
        if len(choosers) != 1 or len(submits) != 1 or len(nonces) != 1:
            raise DeploymentError("REFUSED: exact fixed plugin upload controls are unavailable.")
        return choosers[0], submits[0]

    @staticmethod
    def comparison_pair(table: Any, label: str) -> tuple[str, str]:
        wanted = re.compile(label, re.IGNORECASE)
        matches: list[tuple[str, str]] = []
        for row in table.query_selector_all("tr"):
            cells = row.query_selector_all("td")
            if len(cells) == 3 and wanted.fullmatch(str(cells[0].inner_text() or "").strip()):
                matches.append((str(cells[1].inner_text() or "").strip(),
                                str(cells[2].inner_text() or "").strip()))
        if len(matches) != 1:
            raise IndeterminateError("Exact plugin replacement comparison row is unavailable.")
        return matches[0]

    @staticmethod
    def overwrite_control_is_exact(link: Any) -> bool:
        return bool(link.evaluate("""el => {
            try {
                const u = new URL(el.href, window.location.href);
                const keys = [...u.searchParams.keys()].sort();
                const wanted = ['_wpnonce','action','overwrite','package'].sort();
                if (u.origin !== window.location.origin || u.pathname !== '/wp-admin/update.php') return false;
                if (keys.length !== wanted.length || keys.some((v,i) => v !== wanted[i])) return false;
                if (u.searchParams.getAll('action').length !== 1 || u.searchParams.get('action') !== 'upload-plugin') return false;
                if (u.searchParams.getAll('overwrite').length !== 1 || u.searchParams.get('overwrite') !== 'update-plugin') return false;
                if (u.searchParams.getAll('package').length !== 1 || !u.searchParams.get('package')) return false;
                if (u.searchParams.getAll('_wpnonce').length !== 1 || !/^[A-Za-z0-9_-]{8,64}$/.test(u.searchParams.get('_wpnonce'))) return false;
                return !u.username && !u.password && !u.hash;
            } catch (_) { return false; }
        }"""))

    def overwrite_result_is_exact(self) -> bool:
        return bool(self.page.evaluate("""() => {
            try {
                const u = new URL(window.location.href);
                const keys = [...u.searchParams.keys()].sort();
                const wanted = ['_wpnonce','action','overwrite','package'].sort();
                return u.origin === window.location.origin && u.pathname === '/wp-admin/update.php'
                    && keys.length === wanted.length && keys.every((v,i) => v === wanted[i])
                    && u.searchParams.getAll('action').length === 1 && u.searchParams.get('action') === 'upload-plugin'
                    && u.searchParams.getAll('overwrite').length === 1 && u.searchParams.get('overwrite') === 'update-plugin'
                    && u.searchParams.getAll('package').length === 1 && !!u.searchParams.get('package')
                    && u.searchParams.getAll('_wpnonce').length === 1
                    && /^[A-Za-z0-9_-]{8,64}$/.test(u.searchParams.get('_wpnonce'))
                    && !u.username && !u.password && !u.hash;
            } catch (_) { return false; }
        }"""))

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

    def execute_replace(self, chooser: Any, submit: Any, artifact_raw: bytes,
                        expected_before: dict[str, Any],
                        artifact: dict[str, Any] | None = None,
                        plan: dict[str, Any] | None = None) -> dict[str, Any]:
        if len(artifact_raw) != ARTIFACT_BYTES or hashlib.sha256(artifact_raw).hexdigest() != ARTIFACT_SHA256:
            raise DeploymentError("REFUSED: validated in-memory plugin artifact changed.")
        # *** FRESH, IMMEDIATELY BEFORE THE FIRST WEBSITE WRITE. ***
        # v1.6.0 set the artifact and clicked submit with nothing fresher than the
        # commit preflight, so anything that changed in between landed anyway. A
        # separate audit page is used so the upload form on this page is not lost.
        context = getattr(self.page, "context", None)
        if context is None or not callable(getattr(context, "new_page", None)):
            raise DeploymentError(
                "REFUSED: a separate pre-submit audit page is unavailable; nothing was uploaded."
            )
        preflight_page = context.new_page()
        try:
            preflight = AdminPage(preflight_page)
            fresh_health = preflight.verify_guard_health(CURRENT_PLUGIN_VERSION)
            preflight.goto_plugins()
            fresh_row = preflight.read_row(allow_absent=True)
            assert_deployment_eligibility(
                "replace_active", fresh_row, artifact=artifact, plan=plan,
                health=fresh_health)
            if fresh_row != expected_before:
                raise DeploymentError(
                    "REFUSED: the fixed plugin row changed immediately before the upload; "
                    "nothing was uploaded."
                )
        finally:
            try:
                preflight_page.close()
            except Exception:
                pass
        chooser.set_input_files({
            "name": f"{PLUGIN_SLUG}-{PLUGIN_VERSION}.zip", "mimeType": "application/zip",
            "buffer": artifact_raw,
        }, timeout=ACTION_TIMEOUT_MS)
        submit.click(timeout=ACTION_TIMEOUT_MS)
        self.page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(str(self.page.url), mode="install_result")
        tables = self.page.query_selector_all("table.update-from-upload-comparison")
        links = self.page.query_selector_all(OVERWRITE_SELECTOR)
        if len(tables) != 1 or len(links) != 1:
            raise IndeterminateError("Exact replace-current comparison is unavailable after upload.")
        current_name, uploaded_name = self.comparison_pair(tables[0], r"(plugin\s+)?name")
        current_version, uploaded_version = self.comparison_pair(tables[0], r"version")
        if ((current_name, uploaded_name) != (PLUGIN_NAME, PLUGIN_NAME)
                or current_version != WITHDRAWN_PLUGIN_VERSION
                or uploaded_version != PLUGIN_VERSION):
            raise IndeterminateError("Replacement comparison identity or versions are not exact.")
        if not self.overwrite_control_is_exact(links[0]):
            raise IndeterminateError("Exact WordPress replace-current route is unavailable.")
        context = getattr(self.page, "context", None)
        if context is None or not callable(getattr(context, "new_page", None)):
            raise IndeterminateError(
                "REFUSED: a separate active-state audit page is unavailable before replacement."
            )
        audit_page = context.new_page()
        try:
            audit = AdminPage(audit_page)
            audit.verify_guard_health(WITHDRAWN_PLUGIN_VERSION)
            audit.goto_plugins()
            audit.read_bounded(expected_before)
            links[0].click(timeout=ACTION_TIMEOUT_MS)
            self.page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        finally:
            try:
                audit_page.close()
            except Exception:
                pass
        if not self.overwrite_result_is_exact():
            raise IndeterminateError("WordPress replacement result route was not exact.")
        notices = [" ".join(str(node.inner_text() or "").split())
                   for node in self.page.query_selector_all(".wrap p")]
        if notices.count(OVERWRITE_SUCCESS_MARKER) != 1:
            raise IndeterminateError("WordPress did not show one exact plugin-update success marker.")
        after = self.read_bounded(expected_after("replace_active"))
        return {"comparison_name": uploaded_name, "comparison_current_version": current_version,
                "comparison_uploaded_version": uploaded_version,
                "wordpress_success_marker_exact": True,
                "active_withdrawn_version_reverified_immediately_before_overwrite": True,
                "after": after}

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

    def observe_guard_health(self) -> dict[str, Any]:
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
        families = sorted(str(node.get_attribute("data-frpd-family") or "")
                          for node in self.page.query_selector_all("section[data-frpd-family]"))
        return {
            "version_text": version,
            "status_text": status,
            "families": families,
            # A live guard row -- active OR a claimed gallery state -- renders the
            # guarded-snapshot and completion controls, and an Open Manway recovery
            # row additionally renders its own commit form. Their ABSENCE is the
            # page's own proof that no guard or recovery state is unresolved.
            "guarded_snapshot_controls": len(
                self.page.query_selector_all("#frpd-mg-guarded-snapshot")),
            "completion_controls": len(self.page.query_selector_all("#frpd-mg-complete")),
            "recovery_gallery_forms": len(
                self.page.query_selector_all("#frpd-mg-recovery-gallery-form")),
            "origin_proof_controls": len(
                self.page.query_selector_all("#frpd-mg-origin-proof")),
            "javascript_error_count": len(errors),
            "javascript_error_kinds": sorted(set(errors)),
        }

    def read_guard_capability(self) -> Any:
        """Read the non-secret capability projection, or None when absent."""
        nodes = self.page.query_selector_all(GUARD_CAPABILITY_SELECTOR)
        if len(nodes) != 1:
            return None
        try:
            return json.loads(str(nodes[0].text_content() or ""))
        except (json.JSONDecodeError, TypeError):
            return None

    def verify_guard_health(self, expected_version: str = PLUGIN_VERSION) -> dict[str, Any]:
        """Prove the whole live guard page, not just its version text.

        v1.6.0 checked version text, "Guard inactive", the presence/absence of a
        capability node and JavaScript errors. That could not tell an exact v1.0.5
        from any other build claiming to be 1.0.5, and it never proved the state
        or proof schemas of the build it was about to keep or replace.
        """
        observed = self.observe_guard_health()
        version = observed["version_text"]
        status = observed["status_text"]
        capability = self.read_guard_capability()
        is_target = expected_version == PLUGIN_VERSION
        # The pinned build publishes its exact capability projection, including
        # both schemas and the runtime-manifest digest. The CURRENT build does not
        # publish one at all, and that absence is its expected fixed shape.
        capability_exact = (capability == EXPECTED_GUARD_CAPABILITY if is_target
                            else capability is None)
        schemas_exact = (
            capability_exact and (
                not is_target or (
                    capability.get("state_schema") == EXPECTED_GUARD_STATE_SCHEMA
                    and capability.get("proof_schema") == EXPECTED_GUARD_PROOF_SCHEMA
                    and capability.get("manifest_sha256") == EXPECTED_GUARD_MANIFEST_SHA256)))
        families_exact = observed["families"] == sorted(
            ["elbow_90", "manway_cover", "open_manway", "pipe", "stub_flange"])
        # No active guard, no claimed gallery state, no unresolved Open Manway
        # recovery row: none of their controls may be rendered.
        state_absent = (observed["guarded_snapshot_controls"] == 0
                        and observed["completion_controls"] == 0
                        and observed["recovery_gallery_forms"] == 0)
        origin_proof_exact = observed["origin_proof_controls"] == (1 if is_target else 0)
        if (observed["javascript_error_count"]
                or version != f"Version {expected_version}"
                or status != "Guard inactive"
                or not capability_exact or not schemas_exact
                or not families_exact or not state_absent or not origin_proof_exact):
            diagnostic = {
                "expected_version": expected_version,
                "version_exact": version == f"Version {expected_version}",
                "guard_inactive_exact": status == "Guard inactive",
                "capability_exact": capability_exact,
                "capability_present": capability is not None,
                "schemas_exact": schemas_exact,
                "families_exact": families_exact,
                "guard_state_absent": state_absent,
                "origin_proof_control_exact": origin_proof_exact,
                "javascript_error_count": observed["javascript_error_count"],
                "javascript_error_kinds": observed["javascript_error_kinds"],
            }
            raise IndeterminateError(
                "Media guard plugin health page is not exact and clean: "
                + json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))
            )
        return {"url": "/wp-admin/tools.php?page=frpd-media-mutation-guard",
                "version": expected_version, "guard_active": False,
                "guard_state_absent": True,
                "families": list(observed["families"]),
                "state_schema": EXPECTED_GUARD_STATE_SCHEMA if is_target else None,
                "proof_schema": EXPECTED_GUARD_PROOF_SCHEMA if is_target else None,
                "manifest_sha256": EXPECTED_GUARD_MANIFEST_SHA256 if is_target else None,
                "recovery_capability_exact": is_target,
                "javascript_errors": 0}

    def verify_deployment_round(self, expected_version: str = PLUGIN_VERSION) -> dict[str, Any]:
        """ONE complete fresh proof: plugin row AND guard health together.

        v1.6.0 verified health three times but read the plugin row once,
        afterwards, so no single round proved active-and-healthy at the same
        moment. Each round now proves both.
        """
        health = self.verify_guard_health(expected_version)
        self.goto_plugins()
        row = self.read_row(allow_absent=True)
        wanted = project_row(True, True, expected_version, False)
        if row != wanted:
            raise IndeterminateError(
                "A post-replacement round did not read the exact active plugin row: "
                + json.dumps({"expected": wanted, "observed": row},
                             sort_keys=True, separators=(",", ":"))
            )
        return {"row": row, "health": health}

    def verify_guard_health_rounds(self, expected_version: str = PLUGIN_VERSION,
                                   rounds: int = POST_WRITE_READ_ROUNDS) -> list[dict[str, Any]]:
        """Three independent fresh COMPLETE reads, all of which must be exact."""
        proofs: list[dict[str, Any]] = []
        for index in range(rounds):
            if index:
                self.page.wait_for_timeout(POST_WRITE_READ_DELAY_MS)
            proofs.append(self.verify_deployment_round(expected_version))
        return proofs


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
        raise DeploymentError("REFUSED: immutable evidence already exists; no replay or overwrite.") from exc
    except BaseException:
        try:
            pending.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    # The final hard-link now names the fully flushed bytes. Cleanup failure cannot
    # invalidate or duplicate that immutable terminal evidence and must not cause a
    # second result-write attempt.
    try:
        pending.unlink(missing_ok=True)
    except OSError:
        pass


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": utc_now().isoformat(), "action": action, "evidence": evidence}
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def operation_sha(action: str, artifact: dict[str, Any], before: dict[str, Any]) -> str:
    return digest_for({
        "schema_version": SCHEMA_VERSION, "tool": TOOL_NAME, "action": action,
        "plugin_file": PLUGIN_FILE, "artifact_sha256": artifact["sha256"],
        "artifact_bytes": artifact["bytes"],
        "artifact_member_sha256": artifact["member_sha256"],
        "artifact_member_bytes": artifact["member_bytes"], "before": before,
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


def required_before(action: str) -> dict[str, Any]:
    """The ONE exact live plugin row each fixed action may start from."""
    if action == "install_inactive":
        return project_row(False, None, "", False)
    if action == "activate":
        return project_row(True, False, PLUGIN_VERSION, False)
    if action == "deactivate":
        return project_row(True, True, PLUGIN_VERSION, False)
    if action == "replace_active":
        return project_row(True, True, CURRENT_PLUGIN_VERSION, False)
    raise DeploymentError("REFUSED: action is not fixed.")


def assert_deployment_eligibility(action: str, before: dict[str, Any], *,
                                  artifact: dict[str, Any] | None = None,
                                  plan: dict[str, Any] | None = None,
                                  health: dict[str, Any] | None = None) -> None:
    """THE ONE eligibility predicate.

    v1.6.0 had `validate_before()` but used it only while staging; the commit path
    re-implemented a subset inline, and `AdminPage.execute_replace()` selected and
    submitted the artifact with no fresh row/health read at all. This is called at
    stage, at the fresh commit preflight, AND immediately before the first upload
    form submission, with whatever fresh evidence that caller holds.
    """
    if action not in ACTIONS:
        raise DeploymentError("REFUSED: action is not fixed.")
    if not isinstance(before, dict) or set(before) != ROW_KEYS:
        raise DeploymentError("REFUSED: live plugin projection is invalid.")
    wanted = required_before(action)
    if before != wanted:
        raise DeploymentError(
            f"REFUSED: {action} requires the exact live plugin row {wanted!r}; "
            f"the fresh read is {before!r}."
        )
    if artifact is not None:
        if artifact.get("version") != PLUGIN_VERSION:
            raise DeploymentError("REFUSED: the artifact is not the exact pinned version.")
        if artifact.get("sha256") != ARTIFACT_SHA256 or artifact.get("bytes") != ARTIFACT_BYTES:
            raise DeploymentError("REFUSED: the artifact bytes are not the exact pinned ones.")
        if artifact.get("sha256") in WITHDRAWN_ARTIFACTS:
            raise DeploymentError(
                "REFUSED: the artifact is a withdrawn build and can never be deployed.")
        if (artifact.get("member_sha256") != ARTIFACT_MEMBER_SHA256
                or artifact.get("member_bytes") != ARTIFACT_MEMBER_BYTES
                or list(artifact.get("members") or ()) != sorted(ARTIFACT_MEMBERS)):
            raise DeploymentError("REFUSED: the artifact member set is not the exact pinned one.")
    if plan is not None:
        for field, value in (("action", action), ("before", before),
                             ("after_expected", expected_after(action))):
            if plan.get(field) != value:
                raise DeploymentError(
                    f"REFUSED: the plan's {field} disagrees with the fresh live evidence."
                )
        if artifact is not None and plan.get("artifact") != artifact:
            raise DeploymentError(
                "REFUSED: the fixed plugin artifact changed after this plan was staged."
            )
    if health is not None:
        expected_health_version = (CURRENT_PLUGIN_VERSION if action == "replace_active"
                                   else PLUGIN_VERSION)
        if (health.get("version") != expected_health_version
                or health.get("guard_active") is not False
                or health.get("guard_state_absent") is not True
                or health.get("javascript_errors") != 0):
            raise DeploymentError(
                "REFUSED: the fresh guard health page is not the exact expected shape."
            )


def validate_before(action: str, before: dict[str, Any]) -> None:
    """Kept as the stage-time name; it is the same one predicate."""
    assert_deployment_eligibility(action, before)


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
            "replace_active": "one fixed ZIP upload to WordPress comparison, then one exact replace-current click; exactly v1.0.5 becomes exactly v1.0.7 and the plugin must remain active",
            "activate": "one fixed plugin activation click; its hook creates and verifies one fixed InnoDB guard-state table",
            "deactivate": "one fixed plugin deactivation click",
        }[action]],
        "risk": "One attempt only. For replace_active, upload and replacement are not atomic: the temporary upload can land before replacement, and the active v1.0.5 files can be replaced before verification. A write may land even if verification becomes indeterminate. No retry, arbitrary replace, delete, rollback, or cleanup route exists. Runtime locking requires one authoritative MySQL server with no split, proxy multiplexing, pooled connection ownership change, or independent primary. As an ordinary plugin it does not defend against direct database/filesystem mutation, malicious PHP, or a privileged administrator replacing or disabling it. A replacement plan authorizes the upload only; an exact overwrite control is discovered after that write and absence leaves the plan indeterminate with the temporary upload possibly present.",
        "forbidden": ["replace outside exact active healthy inactive-state v1.0.5-to-v1.0.7 update",
                      "deploy the withdrawn v1.0.6 artifact", "delete", "retry", "rollback", "cleanup", "arbitrary plugin",
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
    if saved in SUPERSEDED_PLAN_SHA256:
        raise DeploymentError(
            "REFUSED: this deployment plan targets a superseded guard artifact and cannot commit."
        )
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
        if action == "replace_active":
            admin.verify_guard_health(WITHDRAWN_PLUGIN_VERSION)
            admin.goto_plugins()
            before = admin.read_row()
    path, plan = stage(action, before, artifact)
    print_json({
        "status": "STAGED_READ_ONLY", "plan": str(path), "plan_sha256": plan["sha256"],
        "operation_sha256": plan["operation_sha256"], "action": action, "before": before,
        "after_expected": plan["after_expected"], "risk": plan["risk"],
        "website_writes": 0, "approval_required": APPROVAL_WORD,
    })


def command_diagnose_health(_args: argparse.Namespace) -> None:
    artifact = validate_artifact()
    with admin_session("WordPress media guard read-only health diagnosis") as admin:
        admin.goto_plugins()
        before = admin.read_row(allow_absent=True)
        observed = admin.observe_guard_health()
    print_json({
        "status": "READ_ONLY_DIAGNOSTIC",
        "plugin_row": before,
        "health": observed,
        "artifact": artifact,
        "website_writes": 0,
        "emails": 0,
    })


def command_commit(args: argparse.Namespace) -> None:
    require_approval(args.approval)
    path, plan = load_plan(args.plan)
    action = plan["action"]
    operation = plan["operation_sha256"]
    if operation in SUPERSEDED_OPERATION_SHA256:
        raise DeploymentError(
            "REFUSED: this stable operation targets a superseded guard artifact and cannot commit."
        )
    if attempt_path(operation).exists() or result_path(operation).exists():
        raise DeploymentError("REFUSED: stable operation is permanently replay-locked.")
    artifact_raw: bytes | None = None
    current_artifact: dict[str, Any] | None = None
    if action in {"install_inactive", "replace_active"}:
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
            preflight_health = None
            if action == "replace_active":
                preflight_health = admin.verify_guard_health(CURRENT_PLUGIN_VERSION)
                admin.goto_plugins()
                live = admin.read_row(allow_absent=True)
            # THE ONE predicate, on freshly read live evidence, at commit preflight.
            assert_deployment_eligibility(
                action, live,
                artifact=(current_artifact if action in {"install_inactive", "replace_active"}
                          else None),
                plan=plan, health=preflight_health)
            if action in {"install_inactive", "replace_active"}:
                chooser, control = admin.prepare_install()
            else:
                if action == "deactivate":
                    admin.verify_guard_health()
                    admin.goto_plugins()
                    live = admin.read_row(allow_absent=True)
                    assert_deployment_eligibility(action, live, plan=plan)
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
                replacement = None
            elif action == "replace_active":
                if artifact_raw is None:
                    raise DeploymentError("REFUSED: exact in-memory plugin artifact is unavailable.")
                replacement = admin.execute_replace(
                    chooser, control, artifact_raw, plan["before"],
                    artifact=plan["artifact"], plan=plan
                )
                after = replacement["after"]
                # Three independent fresh reads must all prove exact v1.0.6, active,
                # healthy, no update marker, the exact recovery capability and the
                # preserved family/reuse protections before this is called verified.
                # Three independent COMPLETE rounds: each proves the exact plugin
                # row, active, no update marker, exact health, exact capability,
                # exact schemas and manifest digest, no unexpected guard state and
                # no JavaScript error -- together, in the same round.
                rounds = admin.verify_guard_health_rounds()
                if len(rounds) != POST_WRITE_READ_ROUNDS:
                    raise IndeterminateError("The fixed post-replacement rounds did not all run.")
                after = rounds[-1]["row"]
                if any(round_proof["row"] != expected_after("replace_active")
                       for round_proof in rounds):
                    raise IndeterminateError(
                        "A post-replacement round did not prove the exact expected row.")
                admin.goto_plugins()
                after = admin.read_bounded(expected_after("replace_active"))
                health = rounds[-1]["health"]
                replacement["health_rounds"] = rounds
                replacement["after"] = after
            else:
                after = admin.execute_state_click(action, control)
                health = admin.verify_guard_health() if action == "activate" else None
                replacement = None
            result = {
                "schema": 1, "tool": TOOL_NAME, "action": action, "operation_sha256": operation,
                "plan_sha256": plan["sha256"], "status": "verified", "completed_utc": utc_now().isoformat(),
                "before": plan["before"], "after": after, "guard_health": health,
                "replacement": replacement,
                "writes": 2 if action == "replace_active" else 1, "emails": 0,
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
    diagnose_parser = sub.add_parser("diagnose-health")
    diagnose_parser.set_defaults(func=command_diagnose_health)
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
