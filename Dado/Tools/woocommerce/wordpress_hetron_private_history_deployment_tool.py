#!/usr/bin/env python
"""Approval-gated repair and protection of five fixed Hetron history assets.

This is a closed capability for one site, one active v1.0.0 plugin row, one exact
v1.1.0 replacement artifact, one attachment and five byte-pinned files. Commands
are inspect plus three separate stage/commit pairs: replace the active fixed
plugin, prepare one fixed hidden 0700 directory with a harmless canary, then move
the five exact assets only after nginx denial of that canary is independently
proven. Every plan lasts exactly 24 hours, requires Rachad's byte-exact unpadded
``APPROVED``, and has one attempt with no retry.

The shared WordPress mutex surrounds each whole command and is acquired before an
exclusive per-plan attempt lock. All live preflight reads happen before the lock;
the lock is created before the first side effect. No command deletes, restores,
deactivates, redirects, rewrites, edits content/media, sends email, accepts an
arbitrary URL/path/slug/selector/action, or calls an arbitrary WordPress route.

Replacement, prepare and protection are separate plans and are NOT atomic
together. Prepare comprises directory plus canary creation. Protection performs
five same-filesystem renames, each individually atomic but not atomic as a
five-file set. Any write/verification uncertainty permanently closes that plan.
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
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

sys.path.append(str(Path(__file__).resolve().parent.parent / "common"))
from ui_lane_lock import UiLaneBusy, UiLaneLockError, ui_browser_lock  # noqa: E402

TOOL_NAME = "FRP Depot Hetron Private History Deployment Tool"
TOOL_VERSION = "2.0.0"
SCHEMA_VERSION = 3
COMMANDS = (
    "inspect",
    "stage-replace", "commit-replace",
    "stage-prepare", "commit-prepare",
    "stage-protect", "commit-protect",
)
ACTIONS = ("plugin_replace_active", "prepare_private_root", "protect_five_assets")

ROOT = Path(r"C:\FRPDepot")
BASE = ROOT / "Dado" / "Tools" / "woocommerce" / "hetron_private_history"
ARTIFACT_PATH = BASE / "frpdepot-hetron-private-history-1.1.0.zip"
MANIFEST_PATH = BASE / "frpdepot-hetron-private-history-1.1.0.manifest.json"
PLAN_DIR = ROOT / "Dado" / "20_Working" / "wordpress_hetron_private_history_plans"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
PLAN_LIFETIME_HOURS = 24
PLAN_CLOCK_SKEW_MINUTES = 5
APPROVAL_WORD = "APPROVED"

EXACT_ORIGIN = "https://frpdepots.com"
ALLOWED_HOST = "frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
PLUGINS_URL = EXACT_ORIGIN + "/wp-admin/plugins.php"
UPLOAD_URL = EXACT_ORIGIN + "/wp-admin/plugin-install.php?tab=upload"
ADMIN_POST_URL = EXACT_ORIGIN + "/wp-admin/admin-post.php"
REST_NONCE_URL = EXACT_ORIGIN + "/wp-admin/admin-ajax.php?action=rest-nonce"
REST_ATTACHMENT_URL = EXACT_ORIGIN + "/wp-json/wp/v2/media/1832?context=edit"
ALLOWED_ADMIN_PATHS = frozenset({
    "/wp-admin/plugins.php", "/wp-admin/plugin-install.php", "/wp-admin/update.php",
})

PLUGIN_NAME = "FRP Depot Hetron Private History"
PLUGIN_SLUG = "frpdepot-hetron-private-history"
PLUGIN_FILE = f"{PLUGIN_SLUG}/frpdepot-hetron-private-history.php"
WITHDRAWN_PLUGIN_VERSION = "1.0.0"
PLUGIN_VERSION = "1.1.0"
ARTIFACT_SHA256 = "bb9012fc9daffeef43d5d551445f72f745ef36b95c72c7a9c428b958c98dd55d"
ARTIFACT_BYTES = 5_319
ARTIFACT_MEMBERS = (
    f"{PLUGIN_SLUG}/frpdepot-hetron-private-history.php",
    f"{PLUGIN_SLUG}/readme.txt",
)
ARTIFACT_MEMBER_SHA256 = {
    f"{PLUGIN_SLUG}/frpdepot-hetron-private-history.php":
        "8c06b73a3a76ac2da7e7e9bba25c3f8a31ecdad917b30d436e41b32784b17116",
    f"{PLUGIN_SLUG}/readme.txt":
        "ec13b87367a12747abdc1714812acd16679498e24ab1ac14a83fefaaefd94b00",
}

ATTACHMENT_ID = 1832
ATTACHMENT_SLUG = "hetron-cr-guide-2007_ineos"
ATTACHMENT_STATUS = "private"
ATTACHMENT_MIME = "application/pdf"
ATTACHMENT_FILENAME = "HETRON-CR-Guide-2007_Ineos.pdf"
ATTACHMENT_FILESIZE = 5_740_139
ATTACHMENT_DATE_GMT = "2026-03-17T15:20:38"
PDF_URL = EXACT_ORIGIN + "/wp-content/uploads/2026/03/HETRON-CR-Guide-2007_Ineos.pdf"
ATTACHMENT_URL = EXACT_ORIGIN + "/hetron-cr-guide-2007_ineos/"
ATTACHMENT_QUERY_URL = EXACT_ORIGIN + "/?attachment_id=1832"
ATTACHMENT_ROUTE_URLS = (ATTACHMENT_URL, ATTACHMENT_QUERY_URL)
ALLOWED_UNAVAILABLE_STATUSES = frozenset({404, 410})
ALLOWED_PRIVATE_DENIED_STATUSES = frozenset({403, 404, 410})

FIXED_ASSETS: tuple[dict[str, Any], ...] = (
    {"role": "original_pdf", "url": PDF_URL, "content_type": "application/pdf",
     "bytes": 5_740_139,
     "sha256": "b9993ac63eeeb4994c17dd34a79d6db8e154d3ae65de1d6a98b188fb766986c5",
     "download_action": "frpdepot_hetron_history_original_pdf"},
    {"role": "pdf_preview_full",
     "url": EXACT_ORIGIN + "/wp-content/uploads/2026/03/HETRON-CR-Guide-2007_Ineos-pdf.jpg",
     "content_type": "image/jpeg", "bytes": 118_876,
     "sha256": "c5a8cc7d2a188e6ecc6b85c52ca815c9c461234fe73602ba810f6bc817b949d2",
     "download_action": "frpdepot_hetron_history_preview_full"},
    {"role": "pdf_preview_medium",
     "url": EXACT_ORIGIN + "/wp-content/uploads/2026/03/HETRON-CR-Guide-2007_Ineos-pdf-229x300.jpg",
     "content_type": "image/jpeg", "bytes": 10_781,
     "sha256": "5c15f53bc6559882c0718ee4c670a40d769b1fd37d5bdf40363d7ca6a072c973",
     "download_action": "frpdepot_hetron_history_preview_medium"},
    {"role": "pdf_preview_large",
     "url": EXACT_ORIGIN + "/wp-content/uploads/2026/03/HETRON-CR-Guide-2007_Ineos-pdf-783x1024.jpg",
     "content_type": "image/jpeg", "bytes": 67_358,
     "sha256": "5ba6213b9a7e81a9e8d000516c210af115f9f3b9b27178aeffc9989bfcb08e55",
     "download_action": "frpdepot_hetron_history_preview_large"},
    {"role": "pdf_preview_thumbnail",
     "url": EXACT_ORIGIN + "/wp-content/uploads/2026/03/HETRON-CR-Guide-2007_Ineos-pdf-115x150.jpg",
     "content_type": "image/jpeg", "bytes": 5_927,
     "sha256": "7d64983d04041f9f3a069077ec5f8115110ca77305e1b000677b069a06f68ced",
     "download_action": "frpdepot_hetron_history_preview_thumbnail"},
)
ASSET_PLAN_RECORDS = tuple({key: asset[key] for key in
                            ("role", "url", "content_type", "bytes", "sha256")}
                           for asset in FIXED_ASSETS)
PRIVATE_DIRECTORY = ".frpdepot-private-history-hetron-1832"
PRIVATE_BASE_URL = EXACT_ORIGIN + "/wp-content/uploads/" + PRIVATE_DIRECTORY
CANARY_NAME = "access-probe.txt"
CANARY_URL = PRIVATE_BASE_URL + "/" + CANARY_NAME
CANARY_BYTES = b"FRP Depot Hetron private-history access probe\n"
CANARY_SHA256 = "289ded5fb9159b2ec424339cda238c37ace98a6b351d068da0d0781e4a51f7eb"
PRIVATE_ASSET_URLS = tuple(
    PRIVATE_BASE_URL + "/" + urlsplit(asset["url"]).path.rsplit("/", 1)[1]
    for asset in FIXED_ASSETS
)
CACHE_BUST_SUFFIX = "?frpdepot-hetron-verification=1"
PUBLIC_ASSET_FRESH_URLS = tuple(asset["url"] + CACHE_BUST_SUFFIX for asset in FIXED_ASSETS)
CANARY_FRESH_URL = CANARY_URL + CACHE_BUST_SUFFIX
PRIVATE_ASSET_FRESH_URLS = tuple(url + CACHE_BUST_SUFFIX for url in PRIVATE_ASSET_URLS)
PUBLIC_URLS = frozenset((
    *ATTACHMENT_ROUTE_URLS,
    *(asset["url"] for asset in FIXED_ASSETS),
    *PUBLIC_ASSET_FRESH_URLS,
    CANARY_URL, CANARY_FRESH_URL,
    *PRIVATE_ASSET_URLS, *PRIVATE_ASSET_FRESH_URLS,
))
DOWNLOAD_ACTIONS = {asset["role"]: asset["download_action"] for asset in FIXED_ASSETS}
MAX_ASSET_BYTES = 8_000_000
MAX_ERROR_BYTES = 64_000
PUBLIC_TIMEOUT_SECONDS = 60
PUBLIC_USER_AGENT = "FRPDepot-Dado-Hetron-Private-History/2.0"

ROW_SELECTOR = f'tr[data-plugin="{PLUGIN_FILE}"]:not(.plugin-update-tr)'
UPDATE_ROW_SELECTOR = f'tr.plugin-update-tr[data-plugin="{PLUGIN_FILE}"]'
ACTIVATE_SELECTOR = ".row-actions .activate a"
DEACTIVATE_SELECTOR = ".row-actions .deactivate a"
VERSION_SELECTOR = ".plugin-version-author-uri"
VERSION_PATTERN = re.compile(r"(?i)\bversion\s+([0-9][0-9A-Za-z.\-+_]*)")
NAV_TIMEOUT_MS = 45_000
ACTION_TIMEOUT_MS = 20_000
LOAD_TIMEOUT_MS = 90_000

ATTACHMENT_KEYS = frozenset({"id", "slug", "status", "mime_type", "filename",
                             "filesize", "source_url", "date_gmt"})
PLUGIN_ROW_KEYS = frozenset({"present", "active", "version", "update_marker",
                             "plugin_file", "fingerprint"})
PROBE_KEYS = frozenset({"state", "attachment_id", "attachment_status",
                        "private_root_hidden", "same_filesystem", "destination_safe",
                        "destination_writable", "canary_exact", "assets"})
PROBE_ASSET_KEYS = frozenset({"role", "location", "bytes", "sha256"})
PLAN_KEYS = frozenset({"schema_version", "tool", "tool_version", "origin", "action",
                       "created_utc", "expires_utc", "nonce", "artifact", "before",
                       "after_expected", "risk"})
ARTIFACT_KEYS = frozenset({"path", "sha256", "bytes", "version", "members"})

RISK_BY_ACTION = {
    "plugin_replace_active": (
        "One WordPress Upload Plugin replacement changes the code of the currently active fixed "
        "v1.0.0 slug immediately to the exact byte-pinned v1.1.0 artifact. It must remain active. "
        "No directory or historical file moves. No retry or automatic rollback."
    ),
    "prepare_private_root": (
        "One authenticated fixed prepare POST creates one dot-prefixed directory under uploads "
        "with mode 0700 and one harmless fixed canary. Directory and canary creation are not "
        "atomic together. No historical file moves. Success additionally requires independent "
        "anonymous nginx denial of the canary. No retry or cleanup/delete route."
    ),
    "protect_five_assets": (
        "One authenticated fixed protect POST starts five exact same-filesystem renames from "
        "public uploads to the already prepared and anonymously denied hidden directory. Each "
        "rename is atomic, but the five-file set and earlier writes are not atomic. There is no "
        "retry or automatic rollback. Any uncertainty is permanently indeterminate."
    ),
}


class DeploymentError(RuntimeError):
    """Clean refusal; messages contain no page, credential or response body."""


class IndeterminateError(DeploymentError):
    """A write may have happened and must never be retried under the same plan."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


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


def require_approval(value: Any) -> None:
    if not isinstance(value, str) or value != APPROVAL_WORD:
        raise DeploymentError("Approval must be the byte-exact unpadded uppercase word APPROVED.")


def assert_origin(url: str) -> None:
    parsed = urlsplit(str(url or ""))
    try:
        port = parsed.port
    except ValueError as exc:
        raise DeploymentError("REFUSED: invalid URL port.") from exc
    if (parsed.scheme != "https" or (parsed.hostname or "").casefold() != ALLOWED_HOST
            or port not in (None, 443) or parsed.username or parsed.password):
        raise DeploymentError("REFUSED: URL is outside the exact FRP Depot HTTPS origin.")


def assert_admin_url(url: str) -> None:
    assert_origin(url)
    if (urlsplit(str(url)).path or "/") not in ALLOWED_ADMIN_PATHS:
        raise DeploymentError("REFUSED: admin path is outside the fixed plugin pages.")


def assert_public_url(url: str) -> None:
    assert_origin(url)
    if str(url) not in PUBLIC_URLS:
        raise DeploymentError("REFUSED: public URL is outside the seven fixed Hetron routes.")


def verify_artifact(path: Path | None = None) -> dict[str, Any]:
    artifact = Path(path or ARTIFACT_PATH)
    if artifact.resolve() != ARTIFACT_PATH.resolve() or not artifact.is_file():
        raise DeploymentError("REFUSED: fixed plugin artifact is missing or path changed.")
    data = artifact.read_bytes()
    if len(data) != ARTIFACT_BYTES or not secrets.compare_digest(hashlib.sha256(data).hexdigest(),
                                                                  ARTIFACT_SHA256):
        raise DeploymentError("REFUSED: fixed plugin artifact bytes/hash changed.")
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        with zipfile.ZipFile(artifact) as archive:
            members = tuple(sorted(archive.namelist()))
            member_bytes = {name: archive.read(name) for name in members}
    except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise DeploymentError("REFUSED: artifact or manifest is unreadable.") from exc
    if members != tuple(sorted(ARTIFACT_MEMBERS)):
        raise DeploymentError("REFUSED: artifact member set changed.")
    observed = {name: hashlib.sha256(member_bytes[name]).hexdigest() for name in members}
    if observed != ARTIFACT_MEMBER_SHA256:
        raise DeploymentError("REFUSED: artifact member hashes changed.")
    expected_manifest = {
        "schema": 1, "plugin_name": PLUGIN_NAME, "plugin_slug": PLUGIN_SLUG,
        "plugin_file": PLUGIN_FILE, "plugin_version": PLUGIN_VERSION,
        "artifact_name": ARTIFACT_PATH.name, "artifact_bytes": ARTIFACT_BYTES,
        "artifact_sha256": ARTIFACT_SHA256,
        "members": {name: {"bytes": len(member_bytes[name]), "sha256": observed[name]}
                    for name in members},
    }
    if manifest != expected_manifest:
        raise DeploymentError("REFUSED: artifact manifest changed.")
    source = member_bytes[PLUGIN_FILE].decode("utf-8", errors="replace")
    if f"Plugin Name: {PLUGIN_NAME}" not in source or f"Version: {PLUGIN_VERSION}" not in source:
        raise DeploymentError("REFUSED: artifact plugin identity/version changed.")
    return {"path": str(ARTIFACT_PATH), "sha256": ARTIFACT_SHA256,
            "bytes": ARTIFACT_BYTES, "version": PLUGIN_VERSION,
            "members": list(ARTIFACT_MEMBERS)}


def row_projection(present: bool, active: bool | None, version: str,
                   update_marker: bool = False) -> dict[str, Any]:
    row = {"present": bool(present), "active": active, "version": str(version),
           "update_marker": bool(update_marker), "plugin_file": PLUGIN_FILE}
    row["fingerprint"] = digest_for(row)
    return row


def assert_attachment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeploymentError("REFUSED: authenticated attachment read is invalid.")
    projection = {key: value.get(key) for key in ATTACHMENT_KEYS}
    expected = {
        "id": ATTACHMENT_ID, "slug": ATTACHMENT_SLUG, "status": ATTACHMENT_STATUS,
        "mime_type": ATTACHMENT_MIME, "filename": ATTACHMENT_FILENAME,
        "filesize": ATTACHMENT_FILESIZE, "source_url": PDF_URL,
        "date_gmt": ATTACHMENT_DATE_GMT,
    }
    if projection != expected:
        raise DeploymentError("REFUSED: attachment 1832 is not the exact expected private object.")
    return projection


def normalize_probe(value: Any, *, expected_state: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("nonce"), str) \
            or not re.fullmatch(r"[0-9a-f]{10}", value["nonce"]):
        raise DeploymentError("REFUSED: authenticated protection probe/nonce is invalid.")
    projection = {key: value.get(key) for key in PROBE_KEYS}
    if set(projection) != PROBE_KEYS or projection["state"] != expected_state \
            or projection["attachment_id"] != ATTACHMENT_ID \
            or projection["attachment_status"] != ATTACHMENT_STATUS:
        raise DeploymentError("REFUSED: protection probe state/identity is invalid.")
    required_true = ("private_root_hidden", "same_filesystem",
                     "destination_safe", "destination_writable")
    if any(projection[key] is not True for key in required_true):
        raise DeploymentError("REFUSED: hidden destination was not proven safe/writable/same-filesystem.")
    expected_canary = expected_state != "public_uploads_exact_unprepared"
    if projection["canary_exact"] is not expected_canary:
        raise DeploymentError("REFUSED: fixed private-directory canary state is invalid.")
    assets = projection["assets"]
    if not isinstance(assets, list) or len(assets) != len(FIXED_ASSETS):
        raise DeploymentError("REFUSED: protection probe asset set is invalid.")
    expected_location = "private_history" if expected_state == "private_history_exact" \
        else "public_upload"
    for saved, fixed in zip(assets, FIXED_ASSETS, strict=True):
        expected = {"role": fixed["role"], "location": expected_location,
                    "bytes": fixed["bytes"], "sha256": fixed["sha256"]}
        if not isinstance(saved, dict) or set(saved) != PROBE_ASSET_KEYS or saved != expected:
            raise DeploymentError("REFUSED: protection probe asset hash/location changed.")
    return projection


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def public_request(url: str, *, max_bytes: int) -> dict[str, Any]:
    assert_public_url(url)
    request = Request(url, method="GET", headers={"User-Agent": PUBLIC_USER_AGENT,
                                                  "Cache-Control": "no-cache"})
    opener = build_opener(NoRedirectHandler())
    try:
        response = opener.open(request, timeout=PUBLIC_TIMEOUT_SECONDS)
    except HTTPError as exc:
        response = exc
    except (OSError, URLError) as exc:
        raise DeploymentError("Anonymous verification transport failed.") from exc
    try:
        status_code = int(getattr(response, "status", response.getcode()))
        headers = response.headers
        # A legitimate WordPress/Divi 404 can have a large branded HTML body.
        # For unavailable routes only the status and absence of Location are
        # security-relevant.  Skip those irrelevant bytes; 200 asset responses
        # are still read completely within the fixed bound and SHA-256 checked.
        body = (b"" if status_code in ALLOWED_PRIVATE_DENIED_STATUSES
                else response.read(max_bytes + 1))
    finally:
        response.close()
    if len(body) > max_bytes:
        raise DeploymentError("Anonymous verification exceeded its fixed body bound.")
    return {"status": status_code, "location": headers.get("Location"),
            "content_type": str(headers.get("Content-Type") or "").split(";", 1)[0].casefold(),
            "body": body}


def require_attachment_routes_unavailable() -> dict[str, int]:
    findings = {}
    for url in ATTACHMENT_ROUTE_URLS:
        result = public_request(url, max_bytes=MAX_ERROR_BYTES)
        if result["status"] not in ALLOWED_UNAVAILABLE_STATUSES or result["location"] is not None:
            raise DeploymentError("REFUSED: an anonymous attachment route is available or redirects.")
        findings[url] = result["status"]
    return findings


def verify_public_assets_available() -> list[dict[str, Any]]:
    findings = []
    for expected, saved in zip(FIXED_ASSETS, ASSET_PLAN_RECORDS, strict=True):
        result = public_request(expected["url"], max_bytes=MAX_ASSET_BYTES)
        observed = {"role": expected["role"], "url": expected["url"],
                    "content_type": result["content_type"], "bytes": len(result["body"]),
                    "sha256": hashlib.sha256(result["body"]).hexdigest()}
        if result["status"] != 200 or result["location"] is not None or observed != saved:
            raise DeploymentError("REFUSED: a public historical asset changed or is unavailable.")
        findings.append(observed)
    return findings


def require_canary_denied() -> dict[str, Any]:
    findings: dict[str, int] = {}
    for url in (CANARY_URL, CANARY_FRESH_URL):
        result = public_request(url, max_bytes=MAX_ERROR_BYTES)
        if result["status"] not in ALLOWED_PRIVATE_DENIED_STATUSES \
                or result["location"] is not None or result["body"] == CANARY_BYTES:
            raise DeploymentError("REFUSED: nginx did not deny the fixed private-directory canary.")
        findings[url] = result["status"]
    return {"url": CANARY_URL, "status": findings[CANARY_URL],
            "fresh_url": CANARY_FRESH_URL, "fresh_status": findings[CANARY_FRESH_URL],
            "sha256": CANARY_SHA256}


def require_all_public_unavailable() -> dict[str, int]:
    findings = {}
    public_routes = (*ATTACHMENT_ROUTE_URLS, *(asset["url"] for asset in FIXED_ASSETS),
                     *PUBLIC_ASSET_FRESH_URLS)
    for url in public_routes:
        result = public_request(url, max_bytes=MAX_ERROR_BYTES)
        if result["status"] not in ALLOWED_UNAVAILABLE_STATUSES or result["location"] is not None:
            raise IndeterminateError("A fixed anonymous Hetron route is still available or redirects.")
        findings[url] = result["status"]
    for url in (CANARY_URL, CANARY_FRESH_URL, *PRIVATE_ASSET_URLS,
                *PRIVATE_ASSET_FRESH_URLS):
        result = public_request(url, max_bytes=MAX_ERROR_BYTES)
        if result["status"] not in ALLOWED_PRIVATE_DENIED_STATUSES or result["location"] is not None:
            raise IndeterminateError("A hidden Hetron route is still available or redirects.")
        findings[url] = result["status"]
    return findings


class AdminPage:
    """Actor restricted to the fixed plugin row/upload and fixed same-origin APIs."""

    def __init__(self, page: Any):
        self._page = page

    @property
    def url(self) -> str:
        return str(self._page.url)

    def _goto(self, url: str) -> None:
        assert_admin_url(url)
        self._page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(self.url)
        if self._page.query_selector("form#loginform") is not None:
            raise DeploymentError("Authenticated WordPress browser is signed out.")

    def goto_plugins(self) -> None:
        self._goto(PLUGINS_URL)

    def read_row(self, *, allow_absent: bool = False) -> dict[str, Any]:
        assert_admin_url(self.url)
        rows = self._page.query_selector_all(ROW_SELECTOR)
        if len(rows) > 1:
            raise DeploymentError("REFUSED: fixed plugin row is ambiguous.")
        if not rows:
            if allow_absent:
                return row_projection(False, None, "", False)
            raise DeploymentError("REFUSED: fixed plugin row is absent.")
        row = rows[0]
        tokens = set(str(row.get_attribute("class") or "").split())
        active = "active" in tokens
        inactive = "inactive" in tokens
        has_activate = row.query_selector(ACTIVATE_SELECTOR) is not None
        has_deactivate = row.query_selector(DEACTIVATE_SELECTOR) is not None
        if active == inactive or has_activate == has_deactivate or active != has_deactivate:
            raise DeploymentError("REFUSED: fixed plugin row state/action is ambiguous.")
        version_node = row.query_selector(VERSION_SELECTOR)
        match = VERSION_PATTERN.search(str(version_node.inner_text() or "")) if version_node else None
        if not match:
            raise DeploymentError("REFUSED: fixed plugin version is unreadable.")
        updates = self._page.query_selector_all(UPDATE_ROW_SELECTOR)
        if len(updates) > 1:
            raise DeploymentError("REFUSED: fixed plugin update marker is ambiguous.")
        return row_projection(True, active, match.group(1), bool(updates) or "update" in tokens)

    def read_attachment(self) -> dict[str, Any]:
        assert_admin_url(self.url)
        result = self._page.evaluate(
            """async ({nonceUrl, readUrl}) => {
                const nr = await fetch(nonceUrl, {method:'GET', credentials:'same-origin', redirect:'error'});
                const nonce = await nr.text();
                if (nr.status !== 200 || !/^[0-9a-f]{10}$/.test(nonce)) return {status:0,data:null};
                const response = await fetch(readUrl, {method:'GET', credentials:'same-origin',
                    redirect:'error', headers:{'X-WP-Nonce':nonce}});
                let data = null; try { data = await response.json(); } catch (_) {}
                return {status:response.status,data};
            }""", {"nonceUrl": REST_NONCE_URL, "readUrl": REST_ATTACHMENT_URL})
        if not isinstance(result, dict) or result.get("status") != 200:
            raise DeploymentError("Authenticated fixed attachment read failed.")
        return assert_attachment(result.get("data"))

    def _reconcile_row_after_timeout(self, expected: dict[str, Any], operation: str) -> dict[str, Any]:
        """Read actual server state after a timed-out write; never repeat the write."""
        try:
            self.goto_plugins()
            observed = self.read_row(allow_absent=True)
        except Exception as exc:
            raise IndeterminateError(
                f"{operation} timed out and exact plugin state could not be reconciled."
            ) from exc
        if observed != expected:
            raise IndeterminateError(
                f"{operation} timed out and exact expected plugin state was not proven."
            )
        return observed

    @staticmethod
    def _comparison_cell(table: Any, label: str) -> str:
        wanted = re.compile(label, re.IGNORECASE)
        for row in table.query_selector_all("tr"):
            cells = row.query_selector_all("td")
            if len(cells) >= 2 and wanted.fullmatch(str(cells[0].inner_text() or "").strip()):
                return str(cells[-1].inner_text() or "").strip()
        return ""

    def replace_active_once(self) -> dict[str, Any]:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        before = self.read_row()
        expected_before = row_projection(True, True, WITHDRAWN_PLUGIN_VERSION, False)
        expected_after = row_projection(True, True, PLUGIN_VERSION, False)
        if before != expected_before:
            raise DeploymentError("REFUSED: replacement requires exact v1.0.0 active state.")
        self._goto(UPLOAD_URL)
        chooser = self._page.query_selector('input[type="file"][name="pluginzip"]')
        submit = self._page.query_selector("#install-plugin-submit")
        if chooser is None or submit is None:
            raise DeploymentError("REFUSED: exact Upload Plugin controls are unavailable.")
        chooser.set_input_files(str(ARTIFACT_PATH), timeout=ACTION_TIMEOUT_MS)
        assert_admin_url(self.url)
        try:
            submit.click(timeout=ACTION_TIMEOUT_MS)
            self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_TIMEOUT_MS)
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            raise IndeterminateError(
                "Replacement comparison screen was not proven after the upload submission."
            ) from exc
        assert_admin_url(self.url)
        tables = self._page.query_selector_all("table.update-from-upload-comparison")
        overwrite = self._page.query_selector_all("a.update-from-upload-overwrite")
        if len(tables) != 1 or len(overwrite) != 1:
            raise IndeterminateError("Exact replace-current confirmation is unavailable after upload.")
        name = self._comparison_cell(tables[0], r"(plugin\s+)?name")
        version = self._comparison_cell(tables[0], r"version")
        if name != PLUGIN_NAME or version != PLUGIN_VERSION:
            raise IndeterminateError("Replacement comparison identity/version mismatch.")
        assert_admin_url(self.url)
        try:
            overwrite[0].click(timeout=ACTION_TIMEOUT_MS)
            self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_TIMEOUT_MS)
        except (PlaywrightError, PlaywrightTimeoutError):
            after = self._reconcile_row_after_timeout(expected_after, "Plugin replacement")
            return {"comparison_name": name, "comparison_version": version, "after": after}
        assert_admin_url(self.url)
        self.goto_plugins()
        after = self.read_row()
        if after != expected_after:
            raise IndeterminateError("Replaced plugin did not read back as exact v1.1.0 active state.")
        return {"comparison_name": name, "comparison_version": version, "after": after}

    def probe(self, *, expected_state: str) -> tuple[dict[str, Any], str]:
        assert_admin_url(self.url)
        result = self._page.evaluate(
            """async ({url}) => {
                const response = await fetch(url, {method:'GET', credentials:'same-origin', redirect:'error'});
                let body = null; try { body = await response.json(); } catch (_) {}
                return {status:response.status,body};
            }""", {"url": ADMIN_POST_URL + "?action=frpdepot_hetron_private_history_probe"})
        if not isinstance(result, dict) or result.get("status") != 200 \
                or not isinstance(result.get("body"), dict) \
                or result["body"].get("success") is not True:
            raise DeploymentError("Authenticated fixed protection probe failed.")
        data = result["body"].get("data")
        projection = normalize_probe(data, expected_state=expected_state)
        return projection, data["nonce"]

    def prepare_once(self, nonce: str) -> dict[str, Any]:
        if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{10}", nonce):
            raise DeploymentError("REFUSED: prepare nonce is invalid.")
        assert_admin_url(self.url)
        result = self._page.evaluate(
            """async ({url,nonce}) => {
                const body = new URLSearchParams();
                body.set('action','frpdepot_hetron_private_history_prepare');
                body.set('_wpnonce',nonce);
                const response = await fetch(url, {method:'POST', credentials:'same-origin',
                    redirect:'error', headers:{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'},
                    body:body.toString()});
                let data = null; try { data = await response.json(); } catch (_) {}
                return {status:response.status,data};
            }""", {"url": ADMIN_POST_URL, "nonce": nonce})
        if not isinstance(result, dict) or result.get("status") != 200 \
                or not isinstance(result.get("data"), dict) \
                or result["data"].get("success") is not True:
            raise IndeterminateError("Fixed prepare POST did not return a proven success object.")
        data = result["data"].get("data")
        if not isinstance(data, dict):
            raise IndeterminateError("Fixed prepare POST success body was invalid.")
        synthetic = {**data, "nonce": nonce}
        try:
            return normalize_probe(synthetic, expected_state="public_uploads_exact_prepared")
        except DeploymentError as exc:
            raise IndeterminateError("Fixed prepare POST readback state was invalid.") from exc

    def protect_once(self, nonce: str) -> dict[str, Any]:
        if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{10}", nonce):
            raise DeploymentError("REFUSED: protect nonce is invalid.")
        assert_admin_url(self.url)
        result = self._page.evaluate(
            """async ({url,nonce}) => {
                const body = new URLSearchParams();
                body.set('action','frpdepot_hetron_private_history_protect');
                body.set('_wpnonce',nonce);
                const response = await fetch(url, {method:'POST', credentials:'same-origin',
                    redirect:'error', headers:{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'},
                    body:body.toString()});
                let data = null; try { data = await response.json(); } catch (_) {}
                return {status:response.status,data};
            }""", {"url": ADMIN_POST_URL, "nonce": nonce})
        if not isinstance(result, dict) or result.get("status") != 200 \
                or not isinstance(result.get("data"), dict) \
                or result["data"].get("success") is not True:
            raise IndeterminateError("Fixed protect POST did not return a proven success object.")
        data = result["data"].get("data")
        if not isinstance(data, dict):
            raise IndeterminateError("Fixed protect POST success body was invalid.")
        synthetic = {**data, "nonce": nonce}
        try:
            return normalize_probe(synthetic, expected_state="private_history_exact")
        except DeploymentError as exc:
            raise IndeterminateError("Fixed protect POST readback state was invalid.") from exc

    def verify_authenticated_downloads(self) -> list[dict[str, Any]]:
        assert_admin_url(self.url)
        findings = []
        for expected in FIXED_ASSETS:
            action = DOWNLOAD_ACTIONS[expected["role"]]
            result = self._page.evaluate(
                """async ({url}) => {
                    const response = await fetch(url, {method:'GET',credentials:'same-origin',redirect:'error'});
                    const buffer = await response.arrayBuffer();
                    const digest = await crypto.subtle.digest('SHA-256', buffer);
                    const hex = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2,'0')).join('');
                    return {status:response.status, bytes:buffer.byteLength, sha256:hex,
                        content_type:(response.headers.get('Content-Type') || '').split(';')[0].toLowerCase()};
                }""", {"url": ADMIN_POST_URL + "?action=" + action})
            observed = {"role": expected["role"], "content_type": result.get("content_type"),
                        "bytes": result.get("bytes"), "sha256": result.get("sha256")}
            wanted = {key: expected[key] for key in ("role", "content_type", "bytes", "sha256")}
            if result.get("status") != 200 or observed != wanted:
                raise IndeterminateError("Authenticated historical download hash verification failed.")
            findings.append(observed)
        return findings


@contextlib.contextmanager
def admin_session() -> Iterator[AdminPage]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=ACTION_TIMEOUT_MS)
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            raise DeploymentError("Authenticated WordPress browser is unavailable.") from exc
        if not browser.contexts or not browser.contexts[0].pages:
            raise DeploymentError("Authenticated WordPress browser has no page.")
        admin = AdminPage(browser.contexts[0].pages[0])
        admin.goto_plugins()
        yield admin


def plan_lock_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".attempt-lock.json")


def result_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".result.json")


def exclusive_json(path: Path, value: dict[str, Any], mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError as exc:
        raise DeploymentError(f"Immutable file already exists: {path}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, mode)


def before_snapshot(admin: AdminPage, *, action: str) -> dict[str, Any]:
    attachment = admin.read_attachment()
    routes = require_attachment_routes_unavailable()
    assets = verify_public_assets_available()
    row = admin.read_row(allow_absent=True)
    before = {"attachment": attachment, "attachment_routes": routes,
              "public_assets": assets, "plugin": row}
    if action == "plugin_replace_active":
        if row != row_projection(True, True, WITHDRAWN_PLUGIN_VERSION, False):
            raise DeploymentError("REFUSED: replace requires exact v1.0.0 active state.")
    elif action == "prepare_private_root":
        if row != row_projection(True, True, PLUGIN_VERSION, False):
            raise DeploymentError("REFUSED: prepare requires exact v1.1.0 active state.")
        probe, _nonce = admin.probe(expected_state="public_uploads_exact_unprepared")
        before["protection_probe"] = probe
    elif action == "protect_five_assets":
        if row != row_projection(True, True, PLUGIN_VERSION, False):
            raise DeploymentError("REFUSED: protect requires exact v1.1.0 active state.")
        probe, _nonce = admin.probe(expected_state="public_uploads_exact_prepared")
        before["protection_probe"] = probe
        before["canary_denial"] = require_canary_denied()
    else:
        raise DeploymentError("REFUSED: unknown internal action.")
    return before


def after_expected(action: str) -> dict[str, Any]:
    if action == "plugin_replace_active":
        return {"plugin": row_projection(True, True, PLUGIN_VERSION, False),
                "probe_state": "public_uploads_exact_unprepared",
                "historical_files_moved": False, "retry": False}
    if action == "prepare_private_root":
        return {"plugin": row_projection(True, True, PLUGIN_VERSION, False),
                "probe_state": "public_uploads_exact_prepared",
                "canary": "literal_and_cache_bust_403_404_or_410_no_redirect",
                "historical_files_moved": False, "retry": False}
    return {"plugin": row_projection(True, True, PLUGIN_VERSION, False),
            "probe_state": "private_history_exact",
            "public_routes": "literal_and_cache_bust_404_or_410_no_redirect",
            "hidden_routes": "literal_and_cache_bust_403_404_or_410_no_redirect",
            "authenticated_downloads": "five_exact_sha256", "historical_files_moved": True,
            "historical_files_deleted": False, "retry": False}


def write_plan(action: str, before: dict[str, Any], artifact: dict[str, Any]) -> Path:
    created = utc_now()
    core = {"schema_version": SCHEMA_VERSION, "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION, "origin": EXACT_ORIGIN, "action": action,
            "created_utc": created.isoformat(),
            "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
            "nonce": secrets.token_hex(16), "artifact": artifact, "before": before,
            "after_expected": after_expected(action), "risk": RISK_BY_ACTION[action]}
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{action}_{digest[:16]}.json"
    exclusive_json(path, plan, stat.S_IREAD)
    append_receipt("wordpress_hetron_private_history_plan_staged",
                   f"action={action}; sha256={digest}; write=false")
    return path


def resolve_plan_path(raw: str) -> Path:
    path = Path(raw).resolve()
    if PLAN_DIR.resolve() not in path.parents:
        raise DeploymentError("Plan must be inside the fixed Hetron private-history plan directory.")
    return path


def validate_before(before: Any, action: str) -> None:
    expected_keys = {"attachment", "attachment_routes", "public_assets", "plugin"}
    if action in {"prepare_private_root", "protect_five_assets"}:
        expected_keys.add("protection_probe")
    if action == "protect_five_assets":
        expected_keys.add("canary_denial")
    if not isinstance(before, dict) or set(before) != expected_keys:
        raise DeploymentError("Plan before-state schema is invalid.")
    if before["attachment"] != assert_attachment(before["attachment"]):
        raise DeploymentError("Plan attachment projection is invalid.")
    if before["attachment_routes"] != {url: before["attachment_routes"].get(url)
                                               for url in ATTACHMENT_ROUTE_URLS} \
            or any(code not in ALLOWED_UNAVAILABLE_STATUSES
                   for code in before["attachment_routes"].values()):
        raise DeploymentError("Plan attachment route findings are invalid.")
    if before["public_assets"] != list(ASSET_PLAN_RECORDS):
        raise DeploymentError("Plan public asset hashes are invalid.")
    required_row = {
        "plugin_replace_active": row_projection(True, True, WITHDRAWN_PLUGIN_VERSION, False),
        "prepare_private_root": row_projection(True, True, PLUGIN_VERSION, False),
        "protect_five_assets": row_projection(True, True, PLUGIN_VERSION, False),
    }[action]
    if before["plugin"] != required_row or set(before["plugin"]) != PLUGIN_ROW_KEYS:
        raise DeploymentError("Plan plugin before-state is invalid.")
    if action in {"prepare_private_root", "protect_five_assets"}:
        expected_state = ("public_uploads_exact_unprepared" if action == "prepare_private_root"
                          else "public_uploads_exact_prepared")
        synthetic = {**before["protection_probe"], "nonce": "0" * 10}
        if normalize_probe(synthetic, expected_state=expected_state) != before["protection_probe"]:
            raise DeploymentError("Plan protection probe is invalid.")
    if action == "protect_five_assets":
        canary = before["canary_denial"]
        if not isinstance(canary, dict) or canary != {
                "url": CANARY_URL, "status": canary.get("status"),
                "fresh_url": CANARY_FRESH_URL, "fresh_status": canary.get("fresh_status"),
                "sha256": CANARY_SHA256
        } or canary["status"] not in ALLOWED_PRIVATE_DENIED_STATUSES \
                or canary["fresh_status"] not in ALLOWED_PRIVATE_DENIED_STATUSES:
            raise DeploymentError("Plan canary-denial evidence is invalid.")


def load_plan(raw: str, *, expected_action: str) -> tuple[Path, dict[str, Any]]:
    path = resolve_plan_path(raw)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError("Plan is unreadable.") from exc
    if not isinstance(value, dict):
        raise DeploymentError("Plan must be one JSON object.")
    saved_hash = value.pop("sha256", None)
    if not isinstance(saved_hash, str) or not secrets.compare_digest(saved_hash, digest_for(value)):
        raise DeploymentError("Plan hash failed; reviewed plan is not immutable.")
    if set(value) != PLAN_KEYS or value["schema_version"] != SCHEMA_VERSION \
            or value["tool"] != TOOL_NAME or value["tool_version"] != TOOL_VERSION \
            or value["origin"] != EXACT_ORIGIN or value["action"] != expected_action \
            or value["artifact"] != verify_artifact() \
            or value["after_expected"] != after_expected(expected_action) \
            or value["risk"] != RISK_BY_ACTION[expected_action]:
        raise DeploymentError("Plan fixed identity/action contract is invalid.")
    if set(value["artifact"]) != ARTIFACT_KEYS:
        raise DeploymentError("Plan artifact schema is invalid.")
    validate_before(value["before"], expected_action)
    try:
        created = datetime.fromisoformat(str(value["created_utc"]))
        expires = datetime.fromisoformat(str(value["expires_utc"]))
    except (TypeError, ValueError) as exc:
        raise DeploymentError("Plan timestamps are invalid.") from exc
    if (created.tzinfo is None or created.utcoffset() != timedelta(0)
            or expires.tzinfo is None or expires.utcoffset() != timedelta(0)
            or expires != created + timedelta(hours=PLAN_LIFETIME_HOURS)):
        raise DeploymentError("Plan is not an exact 24-hour UTC plan.")
    now = utc_now()
    if created > now + timedelta(minutes=PLAN_CLOCK_SKEW_MINUTES) or now >= expires:
        raise DeploymentError("Plan is future-dated or expired.")
    if not re.fullmatch(r"[0-9a-f]{32}", str(value["nonce"])):
        raise DeploymentError("Plan nonce is invalid.")
    value["sha256"] = saved_hash
    return path, value


def write_attempt_lock(path: Path, plan: dict[str, Any]) -> None:
    exclusive_json(path, {"plan_sha256": plan["sha256"], "action": plan["action"],
                          "status": "in_flight", "started_utc": utc_now().isoformat(),
                          "attempts_allowed": 1, "attempts_started": 1, "retry": False},
                   stat.S_IREAD | stat.S_IWRITE)


def close_attempt(path: Path, plan: dict[str, Any], status_text: str,
                  detail: dict[str, Any]) -> None:
    payload = {"plan_sha256": plan["sha256"], "action": plan["action"],
               "status": status_text, "updated_utc": utc_now().isoformat(),
               "attempts_allowed": 1, "attempts_started": 1, "retry": False, **detail}
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IREAD)


def record_result(plan_path: Path, plan: dict[str, Any], status_text: str,
                  detail: dict[str, Any]) -> None:
    payload = {"status": status_text, "action": plan["action"], "plan": str(plan_path),
               "plan_sha256": plan["sha256"], "recorded_utc": utc_now().isoformat(),
               "retry": False, **detail}
    exclusive_json(result_path(plan_path), payload, stat.S_IREAD)
    append_receipt("wordpress_hetron_private_history_" + status_text.casefold(),
                   f"action={plan['action']}; sha256={plan['sha256']}; status={status_text}")


def assert_fresh_before(admin: AdminPage, plan: dict[str, Any]) -> str | None:
    live = before_snapshot(admin, action=plan["action"])
    if live != plan["before"]:
        raise DeploymentError("REFUSED: complete live before-state changed after review.")
    if plan["action"] in {"prepare_private_root", "protect_five_assets"}:
        expected_state = ("public_uploads_exact_unprepared"
                          if plan["action"] == "prepare_private_root"
                          else "public_uploads_exact_prepared")
        probe, nonce = admin.probe(expected_state=expected_state)
        if probe != plan["before"]["protection_probe"]:
            raise DeploymentError("REFUSED: protection probe changed after review.")
        if plan["action"] == "protect_five_assets" \
                and require_canary_denied() != plan["before"]["canary_denial"]:
            raise DeploymentError("REFUSED: canary-denial evidence changed after review.")
        return nonce
    return None


@holds_wordpress_browser("WordPress Hetron private-history read-only inspection")
def command_inspect(_args: argparse.Namespace) -> None:
    artifact = verify_artifact()
    with admin_session() as admin:
        attachment = admin.read_attachment()
        row = admin.read_row(allow_absent=True)
        routes = require_attachment_routes_unavailable()
        assets = verify_public_assets_available()
        probe = None
        if row == row_projection(True, True, PLUGIN_VERSION, False):
            last_error: Exception | None = None
            for state in ("public_uploads_exact_unprepared", "public_uploads_exact_prepared",
                          "private_history_exact"):
                try:
                    probe, _ = admin.probe(expected_state=state)
                    break
                except DeploymentError as exc:
                    last_error = exc
            if probe is None and last_error is not None:
                raise last_error
    emit({"status": "INSPECTED_READ_ONLY", "artifact": artifact, "attachment": attachment,
          "plugin": row, "attachment_routes": routes, "public_assets": assets,
          "protection_probe": probe, "external_write_performed": False})


def stage_action(action: str) -> None:
    artifact = verify_artifact()
    with admin_session() as admin:
        before = before_snapshot(admin, action=action)
    path = write_plan(action, before, artifact)
    plan = json.loads(path.read_text(encoding="utf-8"))
    emit({"status": "STAGED_NOT_COMMITTED", "plan": str(path),
          "plan_sha256": plan["sha256"], "expires_utc": plan["expires_utc"],
          "action": action, "before": before, "after_expected": after_expected(action),
          "risk": RISK_BY_ACTION[action], "approval_required": APPROVAL_WORD,
          "external_write_performed": False})


@holds_wordpress_browser("WordPress Hetron private-history read-only stage active replacement")
def command_stage_replace(_args: argparse.Namespace) -> None:
    stage_action("plugin_replace_active")


@holds_wordpress_browser("WordPress Hetron private-history read-only stage private-root prepare")
def command_stage_prepare(_args: argparse.Namespace) -> None:
    stage_action("prepare_private_root")


@holds_wordpress_browser("WordPress Hetron private-history read-only stage protect")
def command_stage_protect(_args: argparse.Namespace) -> None:
    stage_action("protect_five_assets")


def commit_action(args: argparse.Namespace, action: str) -> None:
    plan_path, plan = load_plan(args.plan, expected_action=action)
    require_approval(args.approval)
    lock = plan_lock_path(plan_path)
    if lock.exists() or result_path(plan_path).exists():
        raise DeploymentError("Plan already entered its one allowed attempt; no retry.")
    with admin_session() as admin:
        transition_nonce = assert_fresh_before(admin, plan)
        write_attempt_lock(lock, plan)
        try:
            if action == "plugin_replace_active":
                replacement = admin.replace_active_once()
                probe, _ = admin.probe(expected_state="public_uploads_exact_unprepared")
                projected = [{key: row[key] for key in ("role", "bytes", "sha256")}
                             for row in probe["assets"]]
                expected = [{key: row[key] for key in ("role", "bytes", "sha256")}
                            for row in plan["before"]["public_assets"]]
                if projected != expected:
                    raise IndeterminateError("Protection probe hashes changed after replacement.")
                detail = {"write_count": 1, "upload_submission_count": 1,
                          "overwrite_click_count": 1, "plugin_after": replacement["after"],
                          "replacement": replacement, "probe_after": probe,
                          "historical_files_moved": False}
            elif action == "prepare_private_root":
                if transition_nonce is None:
                    raise DeploymentError("Internal prepare nonce was not established before lock.")
                probe_after = admin.prepare_once(transition_nonce)
                canary_after = require_canary_denied()
                public_assets_after = verify_public_assets_available()
                probe_readback, _ = admin.probe(expected_state="public_uploads_exact_prepared")
                if probe_readback != probe_after or public_assets_after != plan["before"]["public_assets"]:
                    raise IndeterminateError("Prepared private-root readback or public assets changed.")
                detail = {"write_count": 1, "prepare_post_count": 1,
                          "plugin_after": admin.read_row(), "probe_after": probe_readback,
                          "canary_denial": canary_after, "public_assets_after": public_assets_after,
                          "historical_files_moved": False}
            else:
                if transition_nonce is None:
                    raise DeploymentError("Internal protect nonce was not established before lock.")
                probe_after = admin.protect_once(transition_nonce)
                public_after = require_all_public_unavailable()
                downloads = admin.verify_authenticated_downloads()
                probe_readback, _ = admin.probe(expected_state="private_history_exact")
                if probe_readback != probe_after:
                    raise IndeterminateError("Authenticated private-history probe readback changed.")
                detail = {"write_count": 1, "protect_post_count": 1,
                          "plugin_after": admin.read_row(), "probe_after": probe_readback,
                          "anonymous_after": public_after,
                          "authenticated_downloads": downloads,
                          "historical_files_moved": True,
                          "historical_files_deleted": False,
                          "attachment_preserved_private": True}
        except Exception as exc:
            failure = {"stage": "write_or_verification", "reason": type(exc).__name__,
                       "write_attempted": True, "write_count": 1}
            with contextlib.suppress(Exception):
                record_result(plan_path, plan, "INDETERMINATE", failure)
            with contextlib.suppress(Exception):
                close_attempt(lock, plan, "indeterminate", failure)
            raise IndeterminateError(
                "Fixed write or verification was not completely proven; plan permanently closed."
            ) from exc
    record_result(plan_path, plan, "COMMITTED_AND_VERIFIED", detail)
    close_attempt(lock, plan, "committed_verified", detail)
    emit({"status": "COMMITTED_AND_VERIFIED", "action": action,
          "plan_sha256": plan["sha256"], "replay_locked": True, "retry": False, **detail})


@holds_wordpress_browser("WordPress Hetron private-history commit exact active replacement")
def command_commit_replace(args: argparse.Namespace) -> None:
    commit_action(args, "plugin_replace_active")


@holds_wordpress_browser("WordPress Hetron private-history commit private-root prepare")
def command_commit_prepare(args: argparse.Namespace) -> None:
    commit_action(args, "prepare_private_root")


@holds_wordpress_browser("WordPress Hetron private-history commit protect five exact assets")
def command_commit_protect(args: argparse.Namespace) -> None:
    commit_action(args, "protect_five_assets")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect").set_defaults(func=command_inspect)
    for suffix, stage_func, commit_func in (
        ("replace", command_stage_replace, command_commit_replace),
        ("prepare", command_stage_prepare, command_commit_prepare),
        ("protect", command_stage_protect, command_commit_protect),
    ):
        commands.add_parser("stage-" + suffix).set_defaults(func=stage_func)
        commit = commands.add_parser("commit-" + suffix)
        commit.add_argument("--plan", required=True)
        commit.add_argument("--approval", required=True)
        commit.set_defaults(func=commit_func)
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
