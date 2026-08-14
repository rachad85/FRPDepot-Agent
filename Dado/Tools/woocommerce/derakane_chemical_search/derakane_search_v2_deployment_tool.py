#!/usr/bin/env python
"""Fixed deployment tool for the exact Derakane chemical-search v2 plugin.

This module has one write target and two write actions:

* install-or-replace the pinned v2 ZIP, ending installed and INACTIVE;
* activate that exact installed v2 plugin, followed by fixed anonymous page/API QA.

There is no delete command, generic URL/path/slug/ZIP/selector input, generic browser
operation, settings/page/content/user/product/order write, shell route, credential
route, or retry. Plans are closed-schema, nonce-bearing, exact 24-hour, SHA-256
hashed, created read-only, and one-attempt. Commit requires the exact unpadded word
APPROVED. The shared WordPress browser mutex wraps the whole commit and is acquired
before the permanent attempt lock. The attempt lock precedes the first upload or
click.

The currently installed legacy v1.1 plugin uses a DIFFERENT plugin file. It is read
only as a fixed activation-conflict precondition and can never be clicked by this
tool. v2 activation is refused while that legacy plugin is active because both
front ends bind the same search surface. If activation QA fails after v2 was
confirmed active, this tool deactivates v2 exactly once and verifies restoration of
the approved anonymous baseline. An ACTIVE same-slug replacement is refused: no
pinned prior same-slug artifact exists, so pretending it could be restored would be
unsafe. An inactive same-slug replacement is disclosed as file-rollback-unavailable.
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

sys.path.append(str(Path(__file__).resolve().parents[2] / "common"))
from ui_lane_lock import UiLaneBusy, UiLaneLockError, ui_browser_lock  # noqa: E402

TOOL_NAME = "FRP Depot Derakane Chemical Search v2 Deployment Tool"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = 1
ROOT = Path(r"C:\FRPDepot")
TOOL_DIR = ROOT / "Dado" / "Tools" / "woocommerce" / "derakane_chemical_search"
PLAN_DIR = ROOT / "Dado" / "20_Working" / "derakane_search_v2_plans"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
PLAN_LIFETIME_HOURS = 24
PLAN_CLOCK_SKEW_MINUTES = 5
APPROVAL_WORD = "APPROVED"

EXACT_ORIGIN = "https://frpdepots.com"
ALLOWED_HOST = "frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
PLUGIN_NAME = "Derakane™ Resin Chemical Resistance Guide Search"
PLUGIN_SLUG = "frpdepot-derakane-chemical-search"
PLUGIN_FILE = f"{PLUGIN_SLUG}/frpdepot-derakane-chemical-search.php"
ARTIFACT_PATH = TOOL_DIR / "artifacts" / "frpdepot-derakane-chemical-search-2.0.0.zip"
ARTIFACT_VERSION = "2.0.0"
ARTIFACT_BYTES = 791167
ARTIFACT_SHA256 = "fb0c73d63bfda241910231446d93d9260b8dad75562385aabf3d08e4560e85fa"
DATASET_SHA256 = "6a650b48400d4ddfa6e7fa8d75ba1424b17568ffedc2375b2647bb3b94810bc8"
MANIFEST_SHA256 = "effee39814215f58d207f10845911ddbf405b5d68e9ab57f471d4a6a283ea589"
SOURCE_DOCUMENT_SHA256 = "1cbd9522b49d5ac10d01f268b78e4d062b06e61c7e4011da69c989abba32c241"
ARTIFACT_MEMBER_SHA256 = {
    f"{PLUGIN_SLUG}/frpdepot-derakane-chemical-search.php":
        "5ab7b81fd193d29b5bc77d29432a46c4d1a800bdb536839c2518b68e0140ca42",
    f"{PLUGIN_SLUG}/assets/derakane-search.js":
        "b744b2a202ab6736fa55e9806195a4ea966c86a804b563f5bfb94e43b178ecfb",
    f"{PLUGIN_SLUG}/assets/derakane-search.css":
        "c4ca9715dc8a943b6dbf3219d6645b85389618597556c400fc38d0fd66fc364e",
    f"{PLUGIN_SLUG}/readme.txt":
        "bf66528c5a537292a0c66dfaaaf7be3a277d179f6dc36d20d9718821ea190cec",
    f"{PLUGIN_SLUG}/data/import-manifest.json": MANIFEST_SHA256,
    f"{PLUGIN_SLUG}/data/derakane-dataset.json": DATASET_SHA256,
}
ARTIFACT_MEMBERS = tuple(ARTIFACT_MEMBER_SHA256)
SAME_SLUG_REPLACE_FROM_VERSIONS = frozenset({"1.0.0", "1.1.0"})

# Fixed read-only conflict identity. There is intentionally no selector/action method
# that can click this row.
LEGACY_PLUGIN_NAME = "Derakane Chemical Search"
LEGACY_PLUGIN_FILE = "derakane-chemical-search-v1.1.0/derakane-chemical-search.php"
LEGACY_ASSET_FRAGMENT = "/wp-content/plugins/derakane-chemical-search-v1.1.0/"

ACTIONS = ("plugin_install_or_replace", "plugin_activate")
COMMANDS = (
    "inspect", "stage-install-or-replace", "commit-install-or-replace",
    "stage-activate", "commit-activate",
)
PLUGINS_URL = f"{EXACT_ORIGIN}/wp-admin/plugins.php"
UPLOAD_URL = f"{EXACT_ORIGIN}/wp-admin/plugin-install.php?tab=upload"
PUBLIC_URL = f"{EXACT_ORIGIN}/derakane-resin-resistance-search/"
REST_URL = f"{EXACT_ORIGIN}/wp-json/frpdepot-derakane/v1/search"
ALLOWED_ADMIN_PATHS = frozenset({
    "/wp-admin/plugins.php", "/wp-admin/plugin-install.php", "/wp-admin/update.php",
})
ALLOWED_PUBLIC_PATHS = frozenset({
    "/derakane-resin-resistance-search/", "/wp-json/frpdepot-derakane/v1/search",
})

NAV_TIMEOUT_MS = 45_000
LOAD_STATE_TIMEOUT_MS = 45_000
ACTION_TIMEOUT_MS = 15_000
SEARCH_TIMEOUT_MS = 30_000
MIN_RENDERED_TEXT = 80
FATAL_MARKERS = (
    "there has been a critical error on this website", "fatal error", "parse error:",
    "call to undefined function", "uncaught error",
)
ROW_SELECTOR = f'tr[data-plugin="{PLUGIN_FILE}"]:not(.plugin-update-tr)'
UPDATE_ROW_SELECTOR = f'tr.plugin-update-tr[data-plugin="{PLUGIN_FILE}"]'
LEGACY_ROW_SELECTOR = f'tr[data-plugin="{LEGACY_PLUGIN_FILE}"]:not(.plugin-update-tr)'
ACTIVATE_SELECTOR = ".row-actions .activate a"
DEACTIVATE_SELECTOR = ".row-actions .deactivate a"
VERSION_SELECTOR = ".plugin-version-author-uri"
VERSION_PATTERN = re.compile(r"(?i)\bversion\s+([0-9][0-9A-Za-z.\-+_]*)")

INSTALL_VALIDATION_CONTRACT = {
    "target_end_state": "exact v2 installed and inactive",
    "anonymous_page_and_api_must_match_preinstall_projection": True,
    "v2_api_while_inactive": "404 rest_no_route",
    "active_same_slug_replacement_allowed": False,
    "inactive_same_slug_file_rollback_available": False,
    "reason_file_rollback_unavailable": "no pinned prior same-slug artifact",
    "delete_allowed": False,
    "activation_allowed": False,
    "attempts_allowed": 1,
    "retry": False,
}
ACTIVATION_VALIDATION_CONTRACT = {
    "target_end_state": "exact v2 installed and active",
    "legacy_different_slug_must_be_inactive_or_absent": True,
    "anonymous": True,
    "persistent_profile": False,
    "page": PUBLIC_URL,
    "api": REST_URL,
    "page_status": 200,
    "exact_v2_root_count": 1,
    "exact_v2_heading_count": 1,
    "exact_v2_js_count": 1,
    "exact_v2_css_count": 1,
    "legacy_asset_count": 0,
    "dataset_sha256": DATASET_SHA256,
    "source_document_sha256": SOURCE_DOCUMENT_SHA256,
    "short_query_status": 400,
    "short_query_code": "derakane_short_query",
    "known_query": "hydrochloric acid",
    "known_query_status": 200,
    "known_query_requires_results": True,
    "browser_search_requires_results": True,
    "on_any_failure": "deactivate exact v2 once and verify approved anonymous baseline",
    "attempts_allowed": 1,
    "retry": False,
}


class DeploymentError(RuntimeError):
    """A clean refusal or verified negative result."""


class IndeterminateError(DeploymentError):
    """A write was attempted but the final contract could not be proven."""


class KnownValidationFailure(DeploymentError):
    """Fixed anonymous validation returned a known negative projection."""

    def __init__(self, findings: dict[str, Any]):
        super().__init__("Known anonymous Derakane v2 validation failure.")
        self.findings = findings


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


def holds_wordpress_browser(purpose: str):
    def decorate(function):
        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any):
            with ui_browser_lock("wordpress", purpose=purpose):
                return function(*args, **kwargs)
        return wrapper
    return decorate


def require_approval(value: str) -> None:
    if not isinstance(value, str) or value != APPROVAL_WORD:
        raise DeploymentError("Approval must be the exact unpadded uppercase word APPROVED.")


def assert_origin(url: str) -> None:
    parsed = urlsplit(str(url or ""))
    try:
        port = parsed.port
    except ValueError as exc:
        raise DeploymentError("REFUSED: invalid URL port.") from exc
    if (parsed.scheme != "https" or (parsed.hostname or "").casefold() != ALLOWED_HOST
            or port not in (None, 443) or parsed.username or parsed.password):
        raise DeploymentError(f"REFUSED: browser is outside {EXACT_ORIGIN}.")


def assert_admin_url(url: str) -> None:
    assert_origin(url)
    if (urlsplit(str(url)).path or "/") not in ALLOWED_ADMIN_PATHS:
        raise DeploymentError("REFUSED: admin path is outside the three fixed plugin pages.")


def assert_public_url(url: str) -> None:
    assert_origin(url)
    if (urlsplit(str(url)).path or "/") not in ALLOWED_PUBLIC_PATHS:
        raise DeploymentError("REFUSED: public path is outside the fixed page/API routes.")


def verify_artifact(path: Path | None = None) -> dict[str, Any]:
    artifact = Path(path or ARTIFACT_PATH)
    if artifact.resolve() != ARTIFACT_PATH.resolve():
        raise DeploymentError("REFUSED: only the hardcoded Derakane v2 artifact is allowed.")
    if not artifact.is_file():
        raise DeploymentError(f"Artifact missing: {artifact}")
    raw = artifact.read_bytes()
    if len(raw) != ARTIFACT_BYTES:
        raise DeploymentError("REFUSED: exact artifact byte count mismatch.")
    archive_sha = hashlib.sha256(raw).hexdigest()
    if not secrets.compare_digest(archive_sha, ARTIFACT_SHA256):
        raise DeploymentError("REFUSED: exact artifact SHA-256 mismatch.")
    try:
        with zipfile.ZipFile(artifact) as archive:
            members = tuple(sorted(archive.namelist()))
            payload = {name: archive.read(name) for name in members}
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise DeploymentError("REFUSED: artifact is unreadable or incomplete.") from exc
    if members != tuple(sorted(ARTIFACT_MEMBERS)) or len(members) != 6:
        raise DeploymentError("REFUSED: artifact does not contain the exact six members.")
    member_sha = {name: hashlib.sha256(payload[name]).hexdigest() for name in members}
    if member_sha != ARTIFACT_MEMBER_SHA256:
        raise DeploymentError("REFUSED: artifact member SHA-256 map mismatch.")
    if (member_sha[f"{PLUGIN_SLUG}/data/derakane-dataset.json"] != DATASET_SHA256
            or member_sha[f"{PLUGIN_SLUG}/data/import-manifest.json"] != MANIFEST_SHA256):
        raise DeploymentError("REFUSED: pinned dataset or manifest member SHA-256 mismatch.")
    source = payload[f"{PLUGIN_SLUG}/frpdepot-derakane-chemical-search.php"].decode(
        "utf-8", errors="replace")
    version_match = re.search(r"(?im)^\s*\*\s*Version:\s*(\S+)\s*$", source)
    if (not version_match or version_match.group(1) != ARTIFACT_VERSION
            or f"Plugin Name: {PLUGIN_NAME}" not in source):
        raise DeploymentError("REFUSED: artifact plugin identity/version mismatch.")
    manifest_raw = payload[f"{PLUGIN_SLUG}/data/import-manifest.json"]
    try:
        manifest = json.loads(manifest_raw)
    except json.JSONDecodeError as exc:
        raise DeploymentError("REFUSED: pinned import manifest is invalid JSON.") from exc
    if (manifest.get("dataset", {}).get("sha256") != DATASET_SHA256
            or manifest.get("dataset", {}).get("bytes") != 21044912
            or manifest.get("verification", {}).get("status") != "VERIFIED"
            or manifest.get("verification", {}).get("unresolved_count") != 0
            or manifest.get("summary", {}).get("row_count") != 1285
            or manifest.get("summary", {}).get("rating_count") != 11565
            or manifest.get("summary", {}).get("source_special_count") != 17
            or manifest.get("summary", {}).get("parser_uncertainty_count") != 0
            or manifest.get("summary", {}).get("footnote_count") != 25
            or manifest.get("summary", {}).get("cas_entry_count") != 716
            or manifest.get("summary", {}).get("cas_public_searchable_count") != 709
            or manifest.get("summary", {}).get("cas_excluded_count") != 7):
        raise DeploymentError("REFUSED: pinned manifest summary/verification contract mismatch.")
    return {"path": str(artifact), "sha256": archive_sha, "version": ARTIFACT_VERSION,
            "members": list(members), "member_sha256": member_sha, "bytes": len(raw),
            "dataset_sha256": DATASET_SHA256, "manifest_sha256": MANIFEST_SHA256}


def row_fingerprint(row: dict[str, Any]) -> str:
    safe = {key: row[key] for key in
            ("present", "active", "version", "update_marker", "plugin_file")}
    return digest_for(safe)


def project_row(plugin_file: str, present: bool, active: bool | None, version: str,
                update_marker: bool = False) -> dict[str, Any]:
    row = {"present": bool(present), "active": active, "version": str(version),
           "update_marker": bool(update_marker), "plugin_file": plugin_file}
    row["fingerprint"] = row_fingerprint(row)
    return row


class AdminPage:
    """Narrow reader/actor for the exact v2 row; legacy row is read-only."""

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

    def _read_fixed_row(self, selector: str, plugin_file: str,
                        allow_absent: bool) -> dict[str, Any]:
        assert_admin_url(self.url)
        rows = self._page.query_selector_all(selector)
        if len(rows) > 1:
            raise DeploymentError("REFUSED: fixed plugin row is ambiguous.")
        if not rows:
            if allow_absent:
                return project_row(plugin_file, False, None, "", False)
            raise DeploymentError("REFUSED: fixed plugin is not installed.")
        row = rows[0]
        tokens = set(str(row.get_attribute("class") or "").split())
        by_active = "active" in tokens
        by_inactive = "inactive" in tokens
        has_activate = row.query_selector(ACTIVATE_SELECTOR) is not None
        has_deactivate = row.query_selector(DEACTIVATE_SELECTOR) is not None
        if by_active == by_inactive or has_activate == has_deactivate or by_active != has_deactivate:
            raise DeploymentError("REFUSED: fixed plugin row state/action is ambiguous.")
        cell = row.query_selector(VERSION_SELECTOR)
        found = VERSION_PATTERN.search(str(cell.inner_text() or "")) if cell else None
        if not found:
            raise DeploymentError("REFUSED: fixed plugin row version is unreadable.")
        update_selector = (UPDATE_ROW_SELECTOR if plugin_file == PLUGIN_FILE else
                           f'tr.plugin-update-tr[data-plugin="{plugin_file}"]')
        updates = self._page.query_selector_all(update_selector)
        if len(updates) > 1:
            raise DeploymentError("REFUSED: fixed plugin update marker is ambiguous.")
        return project_row(plugin_file, True, by_active, found.group(1),
                           "update" in tokens or bool(updates))

    def read_target(self, allow_absent: bool = False) -> dict[str, Any]:
        return self._read_fixed_row(ROW_SELECTOR, PLUGIN_FILE, allow_absent)

    def read_legacy_conflict(self) -> dict[str, Any]:
        return self._read_fixed_row(LEGACY_ROW_SELECTOR, LEGACY_PLUGIN_FILE, True)

    def _target_row(self) -> Any:
        rows = self._page.query_selector_all(ROW_SELECTOR)
        if len(rows) != 1:
            raise DeploymentError("REFUSED: exact v2 row is missing or ambiguous.")
        return rows[0]

    def activate(self) -> dict[str, Any]:
        before = self.read_target()
        if before["version"] != ARTIFACT_VERSION or before["active"] is not False:
            raise DeploymentError("REFUSED: activation requires exact v2 installed inactive.")
        link = self._target_row().query_selector(ACTIVATE_SELECTOR)
        if link is None:
            raise DeploymentError("REFUSED: exact scoped activation control is unavailable.")
        assert_admin_url(self.url)
        link.click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_STATE_TIMEOUT_MS)
        assert_admin_url(self.url)
        after = self.read_target()
        if after["version"] != ARTIFACT_VERSION or after["active"] is not True:
            raise IndeterminateError("Activation did not read back exact v2 active.")
        return after

    def deactivate_for_rollback(self) -> dict[str, Any]:
        before = self.read_target()
        if before["version"] != ARTIFACT_VERSION:
            raise IndeterminateError("Rollback target is not exact v2.")
        if before["active"] is False:
            return before
        link = self._target_row().query_selector(DEACTIVATE_SELECTOR)
        if link is None:
            raise IndeterminateError("Exact v2 rollback control is unavailable.")
        assert_admin_url(self.url)
        link.click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_STATE_TIMEOUT_MS)
        assert_admin_url(self.url)
        after = self.read_target()
        if after["active"] is not False or after["version"] != ARTIFACT_VERSION:
            raise IndeterminateError("Rollback did not read back exact v2 inactive.")
        return after

    @staticmethod
    def _comparison_cell(table: Any, label: str) -> str:
        wanted = re.compile(label, re.IGNORECASE)
        for row in table.query_selector_all("tr"):
            cells = row.query_selector_all("td")
            if len(cells) >= 2 and wanted.fullmatch(str(cells[0].inner_text() or "").strip()):
                return str(cells[-1].inner_text() or "").strip()
        return ""

    def upload_install_or_replace(self, artifact: Path, existed_before: bool) -> dict[str, Any]:
        if artifact.resolve() != ARTIFACT_PATH.resolve():
            raise DeploymentError("REFUSED: upload is not the hardcoded artifact.")
        self.goto_upload()
        chooser = self._page.query_selector('input[type="file"][name="pluginzip"]')
        submit = self._page.query_selector("#install-plugin-submit")
        if chooser is None or submit is None:
            raise DeploymentError("REFUSED: exact upload controls are unavailable.")
        chooser.set_input_files(str(artifact), timeout=ACTION_TIMEOUT_MS)
        assert_admin_url(self.url)
        submit.click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_STATE_TIMEOUT_MS)
        assert_admin_url(self.url)
        tables = self._page.query_selector_all("table.update-from-upload-comparison")
        overwrite = self._page.query_selector_all("a.update-from-upload-overwrite")
        route = "install"
        comparison: dict[str, str] | None = None
        if existed_before:
            if len(tables) != 1 or len(overwrite) != 1:
                raise DeploymentError("REFUSED: exact replace-current confirmation is unavailable.")
            name = self._comparison_cell(tables[0], r"(plugin\s+)?name")
            version = self._comparison_cell(tables[0], r"version")
            if name != PLUGIN_NAME or version != ARTIFACT_VERSION:
                raise DeploymentError("REFUSED: replacement comparison identity/version mismatch.")
            comparison = {"uploaded_name": name, "uploaded_version": version}
            overwrite[0].click(timeout=ACTION_TIMEOUT_MS)
            self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_STATE_TIMEOUT_MS)
            assert_admin_url(self.url)
            route = "replace"
        elif tables or overwrite:
            raise DeploymentError("REFUSED: fresh install unexpectedly entered replacement route.")
        self.goto_plugins()
        after = self.read_target()
        if after["version"] != ARTIFACT_VERSION or after["active"] is not False:
            raise IndeterminateError("Install/replace did not read back exact v2 inactive.")
        return {"route": route, "comparison": comparison, "after": after}


class PublicPage:
    """Fixed anonymous reader for one public page and one GET-only REST route."""

    def __init__(self, page: Any, request: Any):
        self._page = page
        self._request = request

    def _goto_page(self, query: str = "") -> tuple[int | None, str]:
        url = PUBLIC_URL + query
        assert_public_url(url)
        response = self._page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_public_url(str(self._page.url))
        status = response.status if response is not None else None
        text = str(self._page.inner_text("body", timeout=ACTION_TIMEOUT_MS) or "")
        return status, text

    @staticmethod
    def _health(text: str) -> dict[str, bool]:
        lowered = text.casefold()
        return {"blank": len(text.strip()) < MIN_RENDERED_TEXT,
                "fatal": any(marker in lowered for marker in FATAL_MARKERS)}

    def _api_get(self, parameters: str) -> dict[str, Any]:
        url = REST_URL + "?" + parameters
        assert_public_url(url)
        response = self._request.get(url, headers={"Accept": "application/json"},
                                     timeout=NAV_TIMEOUT_MS, fail_on_status_code=False)
        status = response.status
        try:
            payload = response.json()
        except Exception:
            payload = None
        return {"status": status, "payload": payload}

    def baseline_projection(self) -> dict[str, Any]:
        status, text = self._goto_page()
        html = str(self._page.content() or "")
        api = self._api_get("chemical=a")
        payload = api["payload"] if isinstance(api["payload"], dict) else {}
        projection = {
            "page_status": status,
            **self._health(text),
            "search_root_count": len(self._page.query_selector_all("[data-derakane-search]")),
            "legacy_asset_count": html.count(LEGACY_ASSET_FRAGMENT),
            "v2_js_count": html.count(
                f"/{PLUGIN_SLUG}/assets/derakane-search.js?ver={ARTIFACT_VERSION}"),
            "v2_css_count": html.count(
                f"/{PLUGIN_SLUG}/assets/derakane-search.css?ver={ARTIFACT_VERSION}"),
            "dataset_sha256_count": text.count(DATASET_SHA256),
            "source_document_sha256_count": text.count(SOURCE_DOCUMENT_SHA256),
            "unavailable_count": text.count("Search unavailable:"),
            "api_short_status": api["status"],
            "api_short_code": payload.get("code"),
        }
        projection["fingerprint"] = digest_for(projection)
        return projection

    def activation_findings(self) -> dict[str, Any]:
        status, text = self._goto_page()
        html = str(self._page.content() or "")
        health = self._health(text)
        page = {
            "status": status, **health,
            "root_count": len(self._page.query_selector_all("section[data-derakane-search]")),
            "heading_count": len(self._page.query_selector_all(
                "section[data-derakane-search] h1")),
            "form_count": len(self._page.query_selector_all("[data-derakane-search-form]")),
            "legacy_asset_count": html.count(LEGACY_ASSET_FRAGMENT),
            "v2_js_count": html.count(
                f"/{PLUGIN_SLUG}/assets/derakane-search.js?ver={ARTIFACT_VERSION}"),
            "v2_css_count": html.count(
                f"/{PLUGIN_SLUG}/assets/derakane-search.css?ver={ARTIFACT_VERSION}"),
            "dataset_sha256_count": text.count(DATASET_SHA256),
            "source_document_sha256_count": text.count(SOURCE_DOCUMENT_SHA256),
            "unavailable_count": text.count("Search unavailable:"),
        }
        short = self._api_get("chemical=a")
        short_payload = short["payload"] if isinstance(short["payload"], dict) else {}
        known = self._api_get("chemical=hydrochloric%20acid")
        known_payload = known["payload"] if isinstance(known["payload"], dict) else {}
        source = known_payload.get("source") if isinstance(known_payload.get("source"), dict) else {}
        groups = known_payload.get("groups") if isinstance(known_payload.get("groups"), list) else []
        api = {
            "short_status": short["status"], "short_code": short_payload.get("code"),
            "known_status": known["status"],
            "known_total_positive": isinstance(known_payload.get("total"), int)
            and known_payload["total"] > 0,
            "known_groups_nonempty": bool(groups),
            "dataset_sha256_matches": source.get("dataset_sha256") == DATASET_SHA256,
        }
        browser = {"status_ready": False, "result_count_positive": False}
        try:
            self._goto_page("?chemical=hydrochloric%20acid")
            self._page.wait_for_function(
                """() => {
                    const n = document.querySelector('[data-derakane-search-status]');
                    return n && /^Showing [1-9][0-9]* of /.test(n.textContent || '');
                }""", timeout=SEARCH_TIMEOUT_MS)
            browser["status_ready"] = True
            browser["result_count_positive"] = len(
                self._page.query_selector_all(".derakane-result")) > 0
        except Exception:
            pass
        passed = bool(
            page == {"status": 200, "blank": False, "fatal": False, "root_count": 1,
                     "heading_count": 1, "form_count": 1, "legacy_asset_count": 0,
                     "v2_js_count": 1, "v2_css_count": 1, "dataset_sha256_count": 1,
                     "source_document_sha256_count": 1, "unavailable_count": 0}
            and api == {"short_status": 400, "short_code": "derakane_short_query",
                        "known_status": 200, "known_total_positive": True,
                        "known_groups_nonempty": True, "dataset_sha256_matches": True}
            and browser == {"status_ready": True, "result_count_positive": True}
        )
        return {"passed": passed, "page": page, "api": api, "browser": browser}


@contextlib.contextmanager
def admin_session() -> Iterator[AdminPage]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    with ui_browser_lock("wordpress", purpose="Derakane v2 admin session"), sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=15_000)
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            raise DeploymentError("Authenticated WordPress browser is unavailable.") from exc
        if not browser.contexts or not browser.contexts[0].pages:
            raise DeploymentError("Authenticated WordPress browser has no open page.")
        yield AdminPage(browser.contexts[0].pages[0])


@contextlib.contextmanager
def anonymous_session() -> Iterator[PublicPage]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        try:
            context = browser.new_context()
            context.set_default_timeout(ACTION_TIMEOUT_MS)
            context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            try:
                yield PublicPage(context.new_page(), context.request)
            finally:
                context.close()
        finally:
            browser.close()


def attempt_lock_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".attempt-lock.json")


def result_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".result.json")


def _exclusive_json(path: Path, payload: dict[str, Any], mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError as exc:
        raise DeploymentError(f"File already exists and is immutable: {path}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")
    os.chmod(path, mode)


def write_attempt_lock(path: Path, plan: dict[str, Any], stage: str) -> None:
    _exclusive_json(path, {"plan_sha256": plan["sha256"], "status": "in_flight",
                           "started_utc": utc_now().isoformat(), "stage": stage,
                           "attempts_allowed": 1, "retry": False},
                    stat.S_IREAD | stat.S_IWRITE)


def update_attempt_lock(path: Path, payload: dict[str, Any]) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IREAD)


ROW_KEYS = frozenset({"present", "active", "version", "update_marker", "plugin_file",
                      "fingerprint"})
PUBLIC_KEYS = frozenset({
    "page_status", "blank", "fatal", "search_root_count", "legacy_asset_count",
    "v2_js_count", "v2_css_count", "dataset_sha256_count",
    "source_document_sha256_count", "unavailable_count", "api_short_status",
    "api_short_code", "fingerprint",
})
ARTIFACT_KEYS = frozenset({
    "path", "sha256", "version", "members", "member_sha256", "bytes",
    "dataset_sha256", "manifest_sha256",
})
PLAN_KEYS = frozenset({
    "schema_version", "tool", "tool_version", "origin", "action", "created_utc",
    "expires_utc", "nonce", "plugin_name", "plugin_slug", "plugin_file", "artifact",
    "before", "legacy_conflict", "public_before", "after_expected", "validation",
    "rollback_limits",
})


def expected_after(action: str, before: dict[str, Any]) -> dict[str, Any]:
    if action == "plugin_activate":
        return project_row(PLUGIN_FILE, True, True, ARTIFACT_VERSION,
                           bool(before["update_marker"]))
    return project_row(PLUGIN_FILE, True, False, ARTIFACT_VERSION, False)


def rollback_limits(action: str, before: dict[str, Any]) -> dict[str, Any]:
    if action == "plugin_activate":
        return {"automatic_v2_deactivation": True,
                "approved_anonymous_baseline_readback": True,
                "legacy_plugin_write": False, "file_restoration": False,
                "retry": False}
    return {"automatic_v2_deactivation": False,
            "file_restoration": False,
            "prior_same_slug_artifact_available": False,
            "fresh_install_delete_rollback": False,
            "inactive_replace_can_restore_prior_files": False,
            "route": "install" if not before["present"] else "replace",
            "retry": False}


def write_plan(action: str, before: dict[str, Any], legacy: dict[str, Any],
               public_before: dict[str, Any], artifact: dict[str, Any] | None) -> Path:
    if action not in ACTIONS:
        raise DeploymentError("Unsupported action.")
    created = utc_now()
    validation = (INSTALL_VALIDATION_CONTRACT if action == "plugin_install_or_replace"
                  else ACTIVATION_VALIDATION_CONTRACT)
    core = {
        "schema_version": SCHEMA_VERSION, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "origin": EXACT_ORIGIN, "action": action, "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16), "plugin_name": PLUGIN_NAME,
        "plugin_slug": PLUGIN_SLUG, "plugin_file": PLUGIN_FILE, "artifact": artifact,
        "before": before, "legacy_conflict": legacy, "public_before": public_before,
        "after_expected": expected_after(action, before), "validation": dict(validation),
        "rollback_limits": rollback_limits(action, before),
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{action}_{digest}.json"
    _exclusive_json(path, plan, stat.S_IREAD)
    append_receipt("derakane_search_v2_plan_staged",
                   f"action={action}; plan={path}; sha256={digest}")
    return path


def resolve_plan_path(raw: str) -> Path:
    path = Path(raw).resolve()
    if PLAN_DIR.resolve() not in path.parents:
        raise DeploymentError("Plan must be inside the fixed Derakane v2 plan directory.")
    return path


def _validate_row(row: Any, plugin_file: str, label: str) -> None:
    if (not isinstance(row, dict) or set(row) != ROW_KEYS
            or row.get("plugin_file") != plugin_file
            or row_fingerprint(row) != row.get("fingerprint")):
        raise DeploymentError(f"Plan {label} row projection is invalid.")


def _validate_public(projection: Any) -> None:
    if not isinstance(projection, dict) or set(projection) != PUBLIC_KEYS:
        raise DeploymentError("Plan public baseline projection is invalid.")
    core = dict(projection)
    saved = core.pop("fingerprint")
    if not isinstance(saved, str) or not secrets.compare_digest(saved, digest_for(core)):
        raise DeploymentError("Plan public baseline fingerprint is invalid.")


def load_plan(raw: str) -> dict[str, Any]:
    path = resolve_plan_path(raw)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError("Plan is unreadable.") from exc
    if not isinstance(stored, dict):
        raise DeploymentError("Plan must be one object.")
    saved = str(stored.pop("sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(stored)):
        raise DeploymentError("Plan hash failed; reviewed plan is not immutable.")
    if set(stored) != PLAN_KEYS:
        raise DeploymentError("Plan schema is not the exact closed set.")
    if (stored["schema_version"] != SCHEMA_VERSION or stored["tool"] != TOOL_NAME
            or stored["tool_version"] != TOOL_VERSION or stored["origin"] != EXACT_ORIGIN
            or stored["plugin_name"] != PLUGIN_NAME or stored["plugin_slug"] != PLUGIN_SLUG
            or stored["plugin_file"] != PLUGIN_FILE or stored["action"] not in ACTIONS):
        raise DeploymentError("Plan identity is invalid.")
    try:
        created = datetime.fromisoformat(str(stored["created_utc"]))
        expires = datetime.fromisoformat(str(stored["expires_utc"]))
    except (TypeError, ValueError) as exc:
        raise DeploymentError("Plan timestamps are invalid.") from exc
    if (created.tzinfo is None or created.utcoffset() != timedelta(0)
            or expires.tzinfo is None or expires.utcoffset() != timedelta(0)
            or expires != created + timedelta(hours=24)):
        raise DeploymentError("Plan is not an exact 24-hour UTC plan.")
    now = utc_now()
    if created > now + timedelta(minutes=PLAN_CLOCK_SKEW_MINUTES):
        raise DeploymentError("Plan creation time is in the future.")
    if now >= expires:
        raise DeploymentError("Plan expired; stage a new one.")
    if not re.fullmatch(r"[0-9a-f]{32}", str(stored["nonce"])):
        raise DeploymentError("Plan nonce is invalid.")
    _validate_row(stored["before"], PLUGIN_FILE, "before")
    _validate_row(stored["after_expected"], PLUGIN_FILE, "after_expected")
    _validate_row(stored["legacy_conflict"], LEGACY_PLUGIN_FILE, "legacy_conflict")
    _validate_public(stored["public_before"])
    if stored["after_expected"] != expected_after(stored["action"], stored["before"]):
        raise DeploymentError("Plan expected state is invalid.")
    action = stored["action"]
    if action == "plugin_install_or_replace":
        artifact = stored["artifact"]
        if not isinstance(artifact, dict) or set(artifact) != ARTIFACT_KEYS:
            raise DeploymentError("Install/replace artifact record is invalid.")
        if (Path(artifact["path"]).resolve() != ARTIFACT_PATH.resolve()
                or artifact["sha256"] != ARTIFACT_SHA256
                or artifact["version"] != ARTIFACT_VERSION
                or artifact["bytes"] != ARTIFACT_BYTES
                or tuple(artifact["members"]) != tuple(sorted(ARTIFACT_MEMBERS))
                or artifact["member_sha256"] != {
                    key: ARTIFACT_MEMBER_SHA256[key] for key in sorted(ARTIFACT_MEMBER_SHA256)}
                or artifact["dataset_sha256"] != DATASET_SHA256
                or artifact["manifest_sha256"] != MANIFEST_SHA256):
            raise DeploymentError("Plan does not name the exact canonical v2 artifact.")
        expected_validation = INSTALL_VALIDATION_CONTRACT
    else:
        if stored["artifact"] is not None:
            raise DeploymentError("Only install/replace may carry an artifact.")
        expected_validation = ACTIVATION_VALIDATION_CONTRACT
    if stored["validation"] != expected_validation:
        raise DeploymentError("Plan validation contract is invalid.")
    if stored["rollback_limits"] != rollback_limits(action, stored["before"]):
        raise DeploymentError("Plan rollback-limit disclosure is invalid.")
    stored["sha256"] = saved
    return stored


def record_result(plan_path: Path, plan: dict[str, Any], status_text: str,
                  detail: dict[str, Any]) -> None:
    payload = {"status": status_text, "action": plan["action"], "plan": str(plan_path),
               "plan_sha256": plan["sha256"], "plugin_file": PLUGIN_FILE,
               "recorded_utc": utc_now().isoformat(), "retry": False, **detail}
    _exclusive_json(result_path(plan_path), payload, stat.S_IREAD)
    append_receipt("derakane_search_v2_" + status_text.casefold(),
                   f"action={plan['action']}; plan={plan_path}; sha256={plan['sha256']}")


def _read_live_rows() -> tuple[dict[str, Any], dict[str, Any]]:
    with admin_session() as admin:
        admin.goto_plugins()
        return admin.read_target(allow_absent=True), admin.read_legacy_conflict()


def _read_public_baseline() -> dict[str, Any]:
    with anonymous_session() as public:
        return public.baseline_projection()


def _stage_report(action: str, before: dict[str, Any], legacy: dict[str, Any],
                  public_before: dict[str, Any], artifact: dict[str, Any] | None) -> None:
    path = write_plan(action, before, legacy, public_before, artifact)
    plan = json.loads(path.read_text(encoding="utf-8"))
    emit({"status": "STAGED_NOT_COMMITTED", "plan": str(path),
          "plan_sha256": plan["sha256"], "expires_utc": plan["expires_utc"],
          "action": action, "plugin_file": PLUGIN_FILE, "before": before,
          "legacy_conflict": legacy, "public_before": public_before,
          "after_expected": plan["after_expected"], "validation": plan["validation"],
          "rollback_limits": plan["rollback_limits"], "approval": APPROVAL_WORD,
          "external_write_performed": False})


@holds_wordpress_browser("WordPress: read-only Derakane v2 inspection")
def command_inspect(_: argparse.Namespace) -> None:
    artifact = verify_artifact()
    target, legacy = _read_live_rows()
    public = _read_public_baseline()
    emit({"status": "INSPECTED", "site": EXACT_ORIGIN, "target": target,
          "legacy_conflict": legacy, "artifact": artifact, "public": public,
          "same_slug_v1_active": bool(target["present"] and target["active"]
                                      and target["version"].startswith("1.")),
          "different_slug_legacy_active": bool(legacy["present"] and legacy["active"]),
          "business_write_performed": False})


@holds_wordpress_browser("WordPress: stage exact Derakane v2 inactive install or replacement")
def command_stage_install_or_replace(_: argparse.Namespace) -> None:
    artifact = verify_artifact()
    before, legacy = _read_live_rows()
    if before["present"] and before["active"] is not False:
        raise DeploymentError(
            "REFUSED: active same-slug replacement would change live code immediately and no "
            "pinned prior same-slug artifact exists for automatic restoration. Nothing staged.")
    if before["present"] and before["version"] == ARTIFACT_VERSION:
        raise DeploymentError("No change needed: exact v2 is already installed inactive.")
    if before["present"] and before["version"] not in SAME_SLUG_REPLACE_FROM_VERSIONS:
        raise DeploymentError("REFUSED: same-slug installed version is not an allowlisted v1.")
    public_before = _read_public_baseline()
    if public_before["blank"] or public_before["fatal"] or public_before["page_status"] != 200:
        raise DeploymentError("REFUSED: anonymous public baseline is unhealthy.")
    _stage_report("plugin_install_or_replace", before, legacy, public_before, artifact)


@holds_wordpress_browser("WordPress: stage exact Derakane v2 activation")
def command_stage_activate(_: argparse.Namespace) -> None:
    before, legacy = _read_live_rows()
    if before["version"] != ARTIFACT_VERSION or before["active"] is not False:
        raise DeploymentError("REFUSED: activation requires exact v2 installed inactive.")
    if legacy["present"] and legacy["active"] is True:
        raise DeploymentError(
            "REFUSED: the fixed different-slug legacy Derakane v1.1 plugin is active. Both front "
            "ends bind the same search surface; this exact-v2-only tool cannot click the legacy "
            "row. Deactivate/migrate legacy under a separately reviewed capability first.")
    public_before = _read_public_baseline()
    if (public_before["blank"] or public_before["fatal"]
            or public_before["page_status"] != 200
            or public_before["v2_js_count"] != 0 or public_before["v2_css_count"] != 0
            or public_before["api_short_status"] != 404
            or public_before["api_short_code"] != "rest_no_route"):
        raise DeploymentError("REFUSED: anonymous pre-activation page/API baseline is not safe.")
    _stage_report("plugin_activate", before, legacy, public_before, None)


def _open_commit(args: argparse.Namespace, action: str) -> tuple[Path, dict[str, Any]]:
    path = resolve_plan_path(args.plan)
    plan = load_plan(str(path))
    if plan["action"] != action:
        raise DeploymentError("Plan action does not match command.")
    # Load and hash-check the local plan first; approval is still required before
    # artifact re-open, browser/CDP, anonymous HTTP, vault, or any site interaction.
    require_approval(args.approval)
    if attempt_lock_path(path).exists() or result_path(path).exists():
        raise DeploymentError("Plan already entered its one allowed attempt; no retry.")
    return path, plan


def _verify_rows(plan: dict[str, Any]) -> None:
    target, legacy = _read_live_rows()
    if target["fingerprint"] != plan["before"]["fingerprint"]:
        raise DeploymentError("REFUSED: exact v2 row changed after review.")
    if legacy["fingerprint"] != plan["legacy_conflict"]["fingerprint"]:
        raise DeploymentError("REFUSED: fixed legacy-conflict row changed after review.")


def _verify_public(plan: dict[str, Any]) -> None:
    live = _read_public_baseline()
    if live["fingerprint"] != plan["public_before"]["fingerprint"]:
        raise DeploymentError("REFUSED: anonymous page/API baseline changed after review.")


def _close_attempt(lock: Path, plan: dict[str, Any], status_text: str,
                   detail: dict[str, Any]) -> None:
    update_attempt_lock(lock, {"plan_sha256": plan["sha256"], "status": status_text,
                               "updated_utc": utc_now().isoformat(), "retry": False, **detail})


def _finish_failed(lock: Path, plan_path: Path, plan: dict[str, Any], status_text: str,
                   stage: str, exc: Exception, detail: dict[str, Any]) -> None:
    _close_attempt(lock, plan, status_text.casefold(),
                   {"stage": stage, "reason": type(exc).__name__, **detail})
    record_result(plan_path, plan, status_text,
                  {"stage": stage, "reason": type(exc).__name__, **detail})


@holds_wordpress_browser("WordPress: commit exact Derakane v2 inactive install or replacement")
def command_commit_install_or_replace(args: argparse.Namespace) -> None:
    plan_path, plan = _open_commit(args, "plugin_install_or_replace")
    artifact = verify_artifact(Path(plan["artifact"]["path"]))
    if artifact != plan["artifact"]:
        raise DeploymentError("REFUSED: artifact changed after plan review.")
    _verify_rows(plan)
    _verify_public(plan)
    # Re-read admin state after the anonymous preflight and immediately before the
    # lock/write. The command-level mutex spans every read and the following write.
    _verify_rows(plan)
    lock = attempt_lock_path(plan_path)
    with admin_session() as admin:
        admin.goto_plugins()
        target = admin.read_target(allow_absent=True)
        legacy = admin.read_legacy_conflict()
        if (target["fingerprint"] != plan["before"]["fingerprint"]
                or legacy["fingerprint"] != plan["legacy_conflict"]["fingerprint"]):
            raise DeploymentError("REFUSED: plugin state drifted before attempt lock.")
        write_attempt_lock(lock, plan, "install_or_replace")
        try:
            result = admin.upload_install_or_replace(ARTIFACT_PATH, bool(target["present"]))
        except Exception as exc:
            detail = {"file_restoration_attempted": False,
                      "file_restoration_available": False,
                      "delete_attempted": False, "retry": False}
            _finish_failed(lock, plan_path, plan, "INDETERMINATE",
                           "install_or_replace", exc, detail)
            raise IndeterminateError(
                "Install/replace unverified; no prior artifact/delete rollback exists; plan "
                "permanently closed with no retry.") from exc
    try:
        public_after = _read_public_baseline()
        final_target, final_legacy = _read_live_rows()
        if (public_after["fingerprint"] != plan["public_before"]["fingerprint"]
                or final_target != plan["after_expected"]
                or final_legacy["fingerprint"] != plan["legacy_conflict"]["fingerprint"]):
            raise KnownValidationFailure({"public_after": public_after,
                                          "target": final_target,
                                          "legacy_conflict": final_legacy})
    except Exception as exc:
        detail = {"upload_readback": result, "file_restoration_attempted": False,
                  "file_restoration_available": False, "delete_attempted": False,
                  "retry": False}
        _finish_failed(lock, plan_path, plan, "INDETERMINATE",
                       "complete_live_readback", exc, detail)
        raise IndeterminateError(
            "Inactive install/replace complete live readback failed; file rollback is unavailable; "
            "plan permanently closed with no retry.") from exc
    detail = {"after": final_target, "legacy_conflict": final_legacy,
              "public_after": public_after, "route": result["route"],
              "comparison": result["comparison"], "activated": False,
              "file_restoration_available": False}
    _close_attempt(lock, plan, "committed_verified", detail)
    record_result(plan_path, plan, "COMMITTED_AND_VERIFIED", detail)
    emit({"status": "COMMITTED_AND_VERIFIED", "action": plan["action"],
          "route": result["route"], "after": final_target, "legacy_conflict": final_legacy,
          "public_after": public_after, "activated": False,
          "file_restoration_available": False, "plan_sha256": plan["sha256"],
          "replay_locked": True})


def _rollback_activation(plan: dict[str, Any]) -> dict[str, Any]:
    rollback: dict[str, Any] = {"attempted": True, "deactivate_clicked": False,
                                "inactive_confirmed": False, "baseline_restored": False}
    with admin_session() as admin:
        admin.goto_plugins()
        before = admin.read_target()
        after = admin.deactivate_for_rollback()
        rollback["deactivate_clicked"] = before["active"] is True
        rollback["after"] = after
        rollback["inactive_confirmed"] = after["active"] is False
    try:
        public = _read_public_baseline()
        rollback["public_after"] = public
        rollback["baseline_restored"] = (
            public["fingerprint"] == plan["public_before"]["fingerprint"])
    except Exception as exc:
        rollback["public_error"] = type(exc).__name__
    return rollback


@holds_wordpress_browser("WordPress: commit exact Derakane v2 activation")
def command_commit_activate(args: argparse.Namespace) -> None:
    plan_path, plan = _open_commit(args, "plugin_activate")
    _verify_rows(plan)
    _verify_public(plan)
    _verify_rows(plan)
    lock = attempt_lock_path(plan_path)
    activation_attempted = False
    activation_error: Exception | None = None
    after: dict[str, Any] = {}
    with admin_session() as admin:
        admin.goto_plugins()
        target = admin.read_target()
        legacy = admin.read_legacy_conflict()
        if (target["fingerprint"] != plan["before"]["fingerprint"]
                or legacy["fingerprint"] != plan["legacy_conflict"]["fingerprint"]):
            raise DeploymentError("REFUSED: plugin state drifted before attempt lock.")
        if legacy["present"] and legacy["active"] is True:
            raise DeploymentError("REFUSED: legacy conflict is active; activation not attempted.")
        write_attempt_lock(lock, plan, "activate")
        try:
            activation_attempted = True
            after = admin.activate()
        except Exception as exc:
            activation_error = exc
    if activation_error is not None:
        try:
            rollback = _rollback_activation(plan) if activation_attempted else {
                "attempted": False, "inactive_confirmed": False, "baseline_restored": False}
        except Exception as rollback_exc:
            rollback = {"attempted": activation_attempted, "inactive_confirmed": False,
                        "baseline_restored": False, "reason": type(rollback_exc).__name__}
        detail = {"activation_confirmed": False, "rollback": rollback, "retry": False}
        _finish_failed(lock, plan_path, plan, "INDETERMINATE", "activate",
                       activation_error, detail)
        raise IndeterminateError(
            "Activation unverified; exact v2 deactivation/restoration was attempted once; plan "
            "permanently closed with no retry.") from activation_error
    validation_error: Exception | None = None
    findings: dict[str, Any] = {}
    try:
        with anonymous_session() as public:
            findings = public.activation_findings()
        if not findings["passed"]:
            raise KnownValidationFailure(findings)
        final_target, final_legacy = _read_live_rows()
        if (final_target != plan["after_expected"]
                or final_legacy["fingerprint"] != plan["legacy_conflict"]["fingerprint"]):
            raise IndeterminateError("Final admin live readback drifted after anonymous QA.")
    except Exception as exc:
        validation_error = exc
    if validation_error is not None:
        try:
            rollback = _rollback_activation(plan)
        except Exception as rollback_exc:
            rollback = {"attempted": True, "inactive_confirmed": False,
                        "baseline_restored": False, "reason": type(rollback_exc).__name__}
        known = isinstance(validation_error, KnownValidationFailure)
        fully_restored = rollback.get("inactive_confirmed") is True \
            and rollback.get("baseline_restored") is True
        status = "FAILED_CLOSED" if known and fully_restored else "INDETERMINATE"
        detail = {"activation_confirmed": True, "validation": findings,
                  "rollback": rollback, "retry": False}
        _finish_failed(lock, plan_path, plan, status, "anonymous_page_api_validation",
                       validation_error, detail)
        if status == "FAILED_CLOSED":
            raise DeploymentError(
                "Known v2 page/API validation failure; exact v2 auto-deactivated once and approved "
                "anonymous baseline restored; plan permanently closed.") from validation_error
        raise IndeterminateError(
            "v2 validation/readback indeterminate; rollback attempted once but complete restoration "
            "was not proven; plan permanently closed.") from validation_error
    detail = {"after": final_target, "legacy_conflict": final_legacy,
              "anonymous_validation": findings, "emergency_deactivated": False}
    _close_attempt(lock, plan, "committed_verified", detail)
    record_result(plan_path, plan, "COMMITTED_AND_VERIFIED", detail)
    emit({"status": "COMMITTED_AND_VERIFIED", "action": plan["action"],
          "after": final_target, "legacy_conflict": final_legacy,
          "anonymous_validation": findings, "emergency_deactivated": False,
          "plan_sha256": plan["sha256"], "replay_locked": True})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler in (
        ("inspect", command_inspect),
        ("stage-install-or-replace", command_stage_install_or_replace),
        ("stage-activate", command_stage_activate),
    ):
        commands.add_parser(name).set_defaults(func=handler)
    for name, handler in (
        ("commit-install-or-replace", command_commit_install_or_replace),
        ("commit-activate", command_commit_activate),
    ):
        command = commands.add_parser(name)
        command.add_argument("--plan", required=True)
        command.add_argument("--approval", required=True)
        command.set_defaults(func=handler)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (DeploymentError, OSError, ValueError, UiLaneBusy, UiLaneLockError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
