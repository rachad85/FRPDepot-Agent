#!/usr/bin/env python
"""Fixed, non-atomic three-plan migration for the live Derakane search page.

This capability exists only for one exact sequence after the separately approved
inactive v2 installation:

1. deactivate the exact active legacy v1.1.0 row;
2. replace one exact shortcode in WordPress page 1840;
3. activate the exact inactive v2 row and run fixed anonymous page/REST/search/UI QA.

WordPress cannot transactionally combine a plugin-row click and a page REST update.
Consequently this tool refuses to disguise the migration as one atomic approval. Each
write has a separate immutable 24-hour plan and exact predecessor result. Steps 1 and
2 intentionally leave the public search unavailable until the next separately
approved step. If a write/readback/QA fails after its attempt lock, compensating
restoration is attempted once in this order: deactivate exact v2, restore the pinned
page bytes, reactivate exact legacy, then prove the pinned legacy anonymous search.
Compensation is not atomic and can itself be indeterminate; the result says so.

There is no delete, settings, user, product, order, media, email, generic URL, generic
plugin, arbitrary page/content, shell, credential, or retry route. Browser admin scope
is only the two hardcoded plugin rows and exact page 1840. Commit requires the exact
unpadded uppercase word APPROVED. The shared WordPress mutex is acquired before the
one-attempt lock and spans every read, write, QA, and compensating restoration.
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
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import derakane_search_v2_deployment_tool as v2  # noqa: E402
sys.path.append(str(HERE.parents[2] / "common"))
from ui_lane_lock import UiLaneBusy, UiLaneLockError, ui_browser_lock  # noqa: E402

TOOL_NAME = "FRP Depot Derakane v1.1-to-v2 Page Migration Tool"
TOOL_VERSION = "1.0.1"
SCHEMA_VERSION = 2
RESULT_SCHEMA_VERSION = 2
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "derakane_search_v2_migration_plans"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
PLAN_LIFETIME_HOURS = 24
PLAN_CLOCK_SKEW_MINUTES = 5
APPROVAL_WORD = "APPROVED"

EXACT_ORIGIN = "https://frpdepots.com"
ALLOWED_HOST = "frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
PLUGINS_URL = f"{EXACT_ORIGIN}/wp-admin/plugins.php"
PUBLIC_URL = f"{EXACT_ORIGIN}/derakane-resin-resistance-search/"
V2_REST_URL = f"{EXACT_ORIGIN}/wp-json/frpdepot-derakane/v1/search"

V2_PLUGIN_NAME = v2.PLUGIN_NAME
V2_PLUGIN_FILE = "frpdepot-derakane-chemical-search/frpdepot-derakane-chemical-search.php"
V2_VERSION = "2.0.0"
LEGACY_PLUGIN_NAME = "Derakane Chemical Search"
LEGACY_PLUGIN_FILE = "derakane-chemical-search-v1.1.0/derakane-chemical-search.php"
LEGACY_VERSION = "1.1.0"
V2_ROW_SELECTOR = f'tr[data-plugin="{V2_PLUGIN_FILE}"]:not(.plugin-update-tr)'
LEGACY_ROW_SELECTOR = f'tr[data-plugin="{LEGACY_PLUGIN_FILE}"]:not(.plugin-update-tr)'
ACTIVATE_SELECTOR = ".row-actions .activate a"
DEACTIVATE_SELECTOR = ".row-actions .deactivate a"
VERSION_SELECTOR = ".plugin-version-author-uri"
VERSION_PATTERN = re.compile(r"(?i)\bversion\s+([0-9][0-9A-Za-z.\-+_]*)")

PAGE_ID = 1840
PAGE_SLUG = "derakane-resin-resistance-search"
PAGE_STATUS = "publish"
PAGE_LINK = PUBLIC_URL
PAGE_TITLE_RAW = "Derakane Resin Resistance Search"
PAGE_DATE_GMT = "2026-03-17T20:18:47"
PAGE_INITIAL_MODIFIED_GMT = "2026-03-31T14:22:29"
PAGE_EDIT_URL = f"{EXACT_ORIGIN}/wp-admin/post.php?post={PAGE_ID}&action=edit"
PAGE_REST_PATH = f"/wp-json/wp/v2/pages/{PAGE_ID}"
PAGE_REST_URL = f"{EXACT_ORIGIN}{PAGE_REST_PATH}"
PAGE_REST_READ_URL = (PAGE_REST_URL + "?context=edit&_fields="
                      "id,slug,status,link,date_gmt,modified_gmt,title,content")
OLD_SHORTCODE = "[derakane_chemical_search]"
NEW_SHORTCODE = "[frpdepot_derakane_search]"
PAGE_FIXTURE = HERE / (
    "page-1840-content-13e439db9e81a60894f70781b7117cb10420c7f207ea334e886f78cee92f48b6.txt")
PAGE_BEFORE_BYTES = 2393
PAGE_BEFORE_SHA256 = "13e439db9e81a60894f70781b7117cb10420c7f207ea334e886f78cee92f48b6"
PAGE_AFTER_BYTES = 2393
PAGE_AFTER_SHA256 = "4824bbe758c10decc743e3738e1becf2853ce1c7bdaabc025fb325e4b026158f"

# The successfully committed Batch 1 inactive installation is the sole
# commissioned predecessor.  Pin the exact reviewed plan and immutable result;
# a different installation, even of the same ZIP, cannot authorize migration.
INSTALL_PLAN_PATH = (ROOT / "Dado" / "20_Working" / "derakane_search_v2_plans" /
    "20260813T220805Z_plugin_install_or_replace_"
    "502af8cdcde53ccce9403dbdd265b6997f9eabab638d7b282063aa4c4b635326.json")
INSTALL_PLAN_SHA256 = "502af8cdcde53ccce9403dbdd265b6997f9eabab638d7b282063aa4c4b635326"

ACTIONS = ("legacy_deactivate", "page_shortcode_replace", "v2_activate")
COMMANDS = (
    "inspect",
    "stage-deactivate-legacy", "commit-deactivate-legacy",
    "stage-update-page", "commit-update-page",
    "stage-activate-v2", "commit-activate-v2",
)
ACTION_PREDECESSOR = {
    "legacy_deactivate": "inactive_v2_install",
    "page_shortcode_replace": "legacy_deactivate",
    "v2_activate": "page_shortcode_replace",
}

NAV_TIMEOUT_MS = 45_000
LOAD_TIMEOUT_MS = 45_000
ACTION_TIMEOUT_MS = 15_000
SEARCH_TIMEOUT_MS = 30_000
MIN_RENDERED_TEXT = 80
FATAL_MARKERS = (
    "there has been a critical error on this website", "fatal error", "parse error:",
    "call to undefined function", "uncaught error",
)

IDENTITIES = {
    "origin": EXACT_ORIGIN,
    "legacy_plugin_name": LEGACY_PLUGIN_NAME,
    "legacy_plugin_file": LEGACY_PLUGIN_FILE,
    "legacy_version": LEGACY_VERSION,
    "v2_plugin_name": V2_PLUGIN_NAME,
    "v2_plugin_file": V2_PLUGIN_FILE,
    "v2_version": V2_VERSION,
    "page_id": PAGE_ID,
    "page_slug": PAGE_SLUG,
    "page_status": PAGE_STATUS,
    "page_link": PAGE_LINK,
    "page_title_raw": PAGE_TITLE_RAW,
    "page_date_gmt": PAGE_DATE_GMT,
    "old_shortcode": OLD_SHORTCODE,
    "new_shortcode": NEW_SHORTCODE,
    "page_before_sha256": PAGE_BEFORE_SHA256,
    "page_after_sha256": PAGE_AFTER_SHA256,
}
VALIDATION = {
    "legacy_deactivate": {
        "exact_legacy_row_only": True,
        "target_end_state": "legacy inactive; v2 inactive; page bytes unchanged",
        "public_search_intentionally_unavailable": True,
        "admin_readback": "both exact rows and exact page 1840",
        "attempts_allowed": 1, "retry": False,
    },
    "page_shortcode_replace": {
        "exact_page_id": PAGE_ID,
        "replacement": "one exact old shortcode to one exact v2 shortcode",
        "all_other_page_bytes_unchanged": True,
        "target_end_state": "legacy inactive; v2 inactive; exact v2 shortcode page",
        "public_search_intentionally_unavailable": True,
        "admin_readback": "both exact rows and exact page 1840",
        "attempts_allowed": 1, "retry": False,
    },
    "v2_activate": {
        "exact_v2_row_only": True,
        "target_end_state": "legacy inactive; v2 active; exact v2 shortcode page",
        "anonymous_page": PUBLIC_URL,
        "anonymous_rest": V2_REST_URL,
        "known_search": "hydrochloric acid",
        "browser_ui_search": True,
        "complete_admin_readback": "both exact rows and exact page 1840",
        "dataset_sha256": v2.DATASET_SHA256,
        "source_document_sha256": v2.SOURCE_DOCUMENT_SHA256,
        "attempts_allowed": 1, "retry": False,
    },
}
ROLLBACK = {
    "atomic": False,
    "transactional": False,
    "compensating_only": True,
    "order": ["deactivate exact v2", "restore exact page 1840 bytes",
              "reactivate exact legacy v1.1.0", "prove anonymous legacy search"],
    "each_compensation_attempts_allowed": 1,
    "unknown_page_content_overwrite_allowed": False,
    "retry": False,
}


class MigrationError(RuntimeError):
    """A clean refusal or known closed failure."""


class IndeterminateError(MigrationError):
    """A side effect occurred but final/restored state could not be proven."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        raise MigrationError("Approval must be the exact unpadded uppercase word APPROVED.")


def verify_page_fixture() -> tuple[str, str]:
    try:
        raw = PAGE_FIXTURE.read_bytes()
    except OSError as exc:
        raise MigrationError("Pinned page-content fixture is unavailable.") from exc
    if len(raw) != PAGE_BEFORE_BYTES or file_sha256(PAGE_FIXTURE) != PAGE_BEFORE_SHA256:
        raise MigrationError("Pinned page-content fixture identity failed.")
    try:
        before = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationError("Pinned page-content fixture is not UTF-8.") from exc
    if before.count(OLD_SHORTCODE) != 1 or before.count(NEW_SHORTCODE) != 0:
        raise MigrationError("Pinned page fixture does not contain the one exact legacy shortcode.")
    after = before.replace(OLD_SHORTCODE, NEW_SHORTCODE)
    encoded = after.encode("utf-8")
    if (len(encoded) != PAGE_AFTER_BYTES
            or hashlib.sha256(encoded).hexdigest() != PAGE_AFTER_SHA256
            or after.count(OLD_SHORTCODE) != 0 or after.count(NEW_SHORTCODE) != 1):
        raise MigrationError("Pinned exact shortcode projection identity failed.")
    return before, after


def assert_origin(url: str) -> None:
    parsed = urlsplit(str(url or ""))
    try:
        port = parsed.port
    except ValueError as exc:
        raise MigrationError("REFUSED: invalid URL port.") from exc
    if (parsed.scheme != "https" or (parsed.hostname or "").casefold() != ALLOWED_HOST
            or port not in (None, 443) or parsed.username or parsed.password):
        raise MigrationError(f"REFUSED: browser is outside {EXACT_ORIGIN}.")


def assert_admin_url(url: str) -> None:
    assert_origin(url)
    parsed = urlsplit(url)
    if parsed.path == "/wp-admin/plugins.php":
        return
    if parsed.path == "/wp-admin/post.php":
        query = parse_qs(parsed.query, keep_blank_values=True)
        if query.get("post") == [str(PAGE_ID)] and query.get("action") == ["edit"]:
            return
    raise MigrationError("REFUSED: admin URL is not the fixed plugin list or exact page 1840.")


def assert_page_rest_url(url: str) -> None:
    assert_origin(url)
    if urlsplit(url).path != PAGE_REST_PATH:
        raise MigrationError("REFUSED: REST URL is not exact page 1840.")


def row_fingerprint(row: dict[str, Any]) -> str:
    return digest_for({key: row[key] for key in
                       ("present", "active", "version", "update_marker", "plugin_file")})


def project_row(plugin_file: str, present: bool, active: bool | None, version: str,
                update_marker: bool = False) -> dict[str, Any]:
    row = {"present": bool(present), "active": active, "version": str(version),
           "update_marker": bool(update_marker), "plugin_file": plugin_file}
    row["fingerprint"] = row_fingerprint(row)
    return row


def page_fingerprint(page: dict[str, Any]) -> str:
    return digest_for({key: page[key] for key in (
        "id", "slug", "status", "link", "date_gmt", "modified_gmt", "title_raw",
        "content_bytes", "content_sha256", "old_shortcode_count", "new_shortcode_count")})


def project_page(record: dict[str, Any]) -> dict[str, Any]:
    title = record.get("title") if isinstance(record.get("title"), dict) else {}
    content = record.get("content") if isinstance(record.get("content"), dict) else {}
    raw = content.get("raw")
    if not isinstance(raw, str):
        raise MigrationError("REFUSED: exact page raw content is unreadable.")
    page = {
        "id": record.get("id"), "slug": record.get("slug"),
        "status": record.get("status"), "link": record.get("link"),
        "date_gmt": record.get("date_gmt"), "modified_gmt": record.get("modified_gmt"),
        "title_raw": title.get("raw"), "content_bytes": len(raw.encode("utf-8")),
        "content_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "old_shortcode_count": raw.count(OLD_SHORTCODE),
        "new_shortcode_count": raw.count(NEW_SHORTCODE),
    }
    page["fingerprint"] = page_fingerprint(page)
    return page


def assert_page_identity(page: dict[str, Any]) -> None:
    fixed = {
        "id": PAGE_ID, "slug": PAGE_SLUG, "status": PAGE_STATUS, "link": PAGE_LINK,
        "date_gmt": PAGE_DATE_GMT, "title_raw": PAGE_TITLE_RAW,
    }
    if any(page.get(key) != value for key, value in fixed.items()):
        raise MigrationError("REFUSED: exact page 1840 identity/status/title/link drifted.")
    if page_fingerprint(page) != page.get("fingerprint"):
        raise MigrationError("REFUSED: exact page projection fingerprint is invalid.")


def page_has_old(page: dict[str, Any], *, initial_modified: bool) -> bool:
    return bool(page["content_bytes"] == PAGE_BEFORE_BYTES
                and page["content_sha256"] == PAGE_BEFORE_SHA256
                and page["old_shortcode_count"] == 1 and page["new_shortcode_count"] == 0
                and (not initial_modified
                     or page["modified_gmt"] == PAGE_INITIAL_MODIFIED_GMT))


def page_has_new(page: dict[str, Any]) -> bool:
    return bool(page["content_bytes"] == PAGE_AFTER_BYTES
                and page["content_sha256"] == PAGE_AFTER_SHA256
                and page["old_shortcode_count"] == 0 and page["new_shortcode_count"] == 1)


class Admin:
    """Reader/actor limited to two exact plugin rows and exact page 1840."""

    def __init__(self, page: Any):
        self._page = page

    @property
    def url(self) -> str:
        return str(self._page.url)

    def goto_plugins(self) -> None:
        self._page.goto(PLUGINS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(self.url)

    def goto_exact_page(self) -> None:
        self._page.goto(PAGE_EDIT_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(self.url)

    def _read_row(self, selector: str, plugin_file: str,
                  allow_absent: bool = False) -> dict[str, Any]:
        assert_admin_url(self.url)
        rows = self._page.query_selector_all(selector)
        if not rows and allow_absent:
            return project_row(plugin_file, False, None, "", False)
        if len(rows) != 1:
            raise MigrationError("REFUSED: exact plugin row is missing or ambiguous.")
        row = rows[0]
        tokens = set(str(row.get_attribute("class") or "").split())
        by_active = "active" in tokens
        by_inactive = "inactive" in tokens
        has_activate = row.query_selector(ACTIVATE_SELECTOR) is not None
        has_deactivate = row.query_selector(DEACTIVATE_SELECTOR) is not None
        if by_active == by_inactive or has_activate == has_deactivate or by_active != has_deactivate:
            raise MigrationError("REFUSED: exact plugin row state/action is ambiguous.")
        cell = row.query_selector(VERSION_SELECTOR)
        match = VERSION_PATTERN.search(str(cell.inner_text() or "")) if cell else None
        if not match:
            raise MigrationError("REFUSED: exact plugin row version is unreadable.")
        updates = self._page.query_selector_all(
            f'tr.plugin-update-tr[data-plugin="{plugin_file}"]')
        if len(updates) > 1:
            raise MigrationError("REFUSED: exact plugin update marker is ambiguous.")
        return project_row(plugin_file, True, by_active, match.group(1),
                           "update" in tokens or bool(updates))

    def read_rows(self, allow_v2_absent: bool = False) -> dict[str, dict[str, Any]]:
        self.goto_plugins()
        return {"v2": self._read_row(V2_ROW_SELECTOR, V2_PLUGIN_FILE, allow_v2_absent),
                "legacy": self._read_row(LEGACY_ROW_SELECTOR, LEGACY_PLUGIN_FILE)}

    def _click_row(self, selector: str, plugin_file: str, version: str,
                   before_active: bool, action_selector: str) -> dict[str, Any]:
        row_before = self._read_row(selector, plugin_file)
        if row_before["version"] != version or row_before["active"] is not before_active:
            raise MigrationError("REFUSED: exact plugin row/version is not in the required state.")
        rows = self._page.query_selector_all(selector)
        control = rows[0].query_selector(action_selector)
        if control is None:
            raise MigrationError("REFUSED: exact scoped plugin control is unavailable.")
        assert_admin_url(self.url)
        control.click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_TIMEOUT_MS)
        assert_admin_url(self.url)
        after = self._read_row(selector, plugin_file)
        if after["version"] != version or after["active"] is before_active:
            raise IndeterminateError("Exact plugin click did not produce the required readback.")
        return after

    def deactivate_legacy(self) -> dict[str, Any]:
        return self._click_row(LEGACY_ROW_SELECTOR, LEGACY_PLUGIN_FILE, LEGACY_VERSION,
                               True, DEACTIVATE_SELECTOR)

    def reactivate_legacy(self) -> dict[str, Any]:
        return self._click_row(LEGACY_ROW_SELECTOR, LEGACY_PLUGIN_FILE, LEGACY_VERSION,
                               False, ACTIVATE_SELECTOR)

    def activate_v2(self) -> dict[str, Any]:
        return self._click_row(V2_ROW_SELECTOR, V2_PLUGIN_FILE, V2_VERSION,
                               False, ACTIVATE_SELECTOR)

    def deactivate_v2(self) -> dict[str, Any]:
        return self._click_row(V2_ROW_SELECTOR, V2_PLUGIN_FILE, V2_VERSION,
                               True, DEACTIVATE_SELECTOR)

    def _nonce(self) -> str:
        value = self._page.evaluate(
            "() => window.wpApiSettings && window.wpApiSettings.nonce")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{10}", value):
            raise MigrationError("REFUSED: exact page REST nonce is unavailable.")
        return value

    def read_page(self) -> dict[str, Any]:
        self.goto_exact_page()
        assert_page_rest_url(PAGE_REST_READ_URL)
        response = self._page.context.request.get(
            PAGE_REST_READ_URL,
            headers={"Accept": "application/json", "X-WP-Nonce": self._nonce()},
            timeout=NAV_TIMEOUT_MS, fail_on_status_code=False)
        if response.status != 200:
            raise MigrationError("REFUSED: exact page 1840 admin REST read failed.")
        try:
            record = response.json()
        except Exception as exc:
            raise MigrationError("REFUSED: exact page 1840 admin REST JSON is unreadable.") from exc
        page = project_page(record)
        assert_page_identity(page)
        return page

    def write_page_content(self, content: str) -> dict[str, Any]:
        before, after = verify_page_fixture()
        if content not in (before, after):
            raise MigrationError("REFUSED: page write is not one of the two pinned exact contents.")
        self.goto_exact_page()
        assert_page_rest_url(PAGE_REST_URL)
        response = self._page.context.request.post(
            PAGE_REST_URL,
            headers={"Accept": "application/json", "X-WP-Nonce": self._nonce()},
            data={"content": content}, timeout=NAV_TIMEOUT_MS,
            fail_on_status_code=False)
        if response.status != 200:
            raise IndeterminateError("Exact page 1840 update did not return HTTP 200.")
        return self.read_page()


@contextlib.contextmanager
def admin_session() -> Iterator[Admin]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
    with ui_browser_lock("wordpress", purpose="Derakane exact migration admin session"), \
            sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=15_000)
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            raise MigrationError("Authenticated WordPress browser is unavailable.") from exc
        if not browser.contexts or not browser.contexts[0].pages:
            raise MigrationError("Authenticated WordPress browser has no open page.")
        yield Admin(browser.contexts[0].pages[0])


class Public:
    """Fresh anonymous exact page, exact v2 REST, and fixed browser-search QA."""

    def __init__(self, page: Any, request: Any):
        self._page = page
        self._request = request

    @staticmethod
    def _fingerprint(value: dict[str, Any]) -> dict[str, Any]:
        return {**value, "fingerprint": digest_for(value)}

    def _goto(self) -> tuple[int | None, str, str]:
        response = self._page.goto(PUBLIC_URL, wait_until="domcontentloaded",
                                   timeout=NAV_TIMEOUT_MS)
        assert_origin(str(self._page.url))
        text = str(self._page.inner_text("body", timeout=ACTION_TIMEOUT_MS) or "")
        html = str(self._page.content() or "")
        return response.status if response is not None else None, text, html

    @staticmethod
    def _health(text: str) -> tuple[bool, bool]:
        lowered = text.casefold()
        return len(text.strip()) < MIN_RENDERED_TEXT, any(x in lowered for x in FATAL_MARKERS)

    def downtime_projection(self) -> dict[str, Any]:
        status, text, html = self._goto()
        blank, fatal = self._health(text)
        response = self._request.get(
            V2_REST_URL + "?chemical=a", headers={"Accept": "application/json"},
            timeout=NAV_TIMEOUT_MS, fail_on_status_code=False)
        try:
            payload = response.json()
        except Exception:
            payload = None
        core = {
            "mode": "inactive_gap", "page_status": status, "blank": blank, "fatal": fatal,
            "root_count": len(self._page.query_selector_all("[data-derakane-search]")),
            "legacy_asset_count": html.count(v2.LEGACY_ASSET_FRAGMENT),
            "v2_js_count": html.count(
                f"/{v2.PLUGIN_SLUG}/assets/derakane-search.js?ver={V2_VERSION}"),
            "v2_css_count": html.count(
                f"/{v2.PLUGIN_SLUG}/assets/derakane-search.css?ver={V2_VERSION}"),
            "v2_rest_status": response.status,
            "v2_rest_code": payload.get("code") if isinstance(payload, dict) else None,
        }
        return self._fingerprint(core)

    def legacy_findings(self) -> dict[str, Any]:
        ajax: list[dict[str, Any]] = []
        def response_seen(response: Any) -> None:
            if urlsplit(response.url).path == "/wp-admin/admin-ajax.php":
                ajax.append({"status": response.status, "method": response.request.method})
        self._page.on("response", response_seen)
        status, text, html = self._goto()
        blank, fatal = self._health(text)
        short = self._request.get(
            V2_REST_URL + "?chemical=a", headers={"Accept": "application/json"},
            timeout=NAV_TIMEOUT_MS, fail_on_status_code=False)
        try:
            short_payload = short.json()
        except Exception:
            short_payload = None
        ui = {"status_text": "", "result_rows": 0, "ajax_posts_200": 0}
        try:
            self._page.locator("[data-derakane-search-input]").fill("hydrochloric acid")
            self._page.locator("[data-derakane-search-form]").evaluate(
                "form => form.requestSubmit()")
            self._page.wait_for_function(
                "() => document.querySelectorAll("
                "'[data-derakane-search-results] table tbody tr').length > 0",
                timeout=SEARCH_TIMEOUT_MS)
            ui["status_text"] = self._page.locator(
                "[data-derakane-search-status]").inner_text()
            ui["result_rows"] = self._page.locator(
                "[data-derakane-search-results] table tbody tr").count()
            ui["ajax_posts_200"] = sum(
                item == {"status": 200, "method": "POST"} for item in ajax)
        except Exception:
            pass
        core = {
            "mode": "legacy", "page_status": status, "blank": blank, "fatal": fatal,
            "root_count": len(self._page.query_selector_all("[data-derakane-search]")),
            "legacy_asset_count": html.count(v2.LEGACY_ASSET_FRAGMENT),
            "v2_js_count": html.count(
                f"/{v2.PLUGIN_SLUG}/assets/derakane-search.js?ver={V2_VERSION}"),
            "v2_css_count": html.count(
                f"/{v2.PLUGIN_SLUG}/assets/derakane-search.css?ver={V2_VERSION}"),
            "v2_rest_status": short.status,
            "v2_rest_code": (short_payload.get("code")
                             if isinstance(short_payload, dict) else None),
            "known_search_status_text": ui["status_text"],
            "known_search_result_rows": ui["result_rows"],
            "known_search_ajax_posts_200": ui["ajax_posts_200"],
        }
        findings = self._fingerprint(core)
        findings["passed"] = legacy_passed(findings)
        return findings

    def v2_findings(self) -> dict[str, Any]:
        return v2.PublicPage(self._page, self._request).activation_findings()


@contextlib.contextmanager
def anonymous_session() -> Iterator[Public]:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        try:
            context = browser.new_context()
            context.set_default_timeout(ACTION_TIMEOUT_MS)
            context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            try:
                yield Public(context.new_page(), context.request)
            finally:
                context.close()
        finally:
            browser.close()


def legacy_passed(value: dict[str, Any]) -> bool:
    return bool(value.get("page_status") == 200 and not value.get("blank")
                and not value.get("fatal") and value.get("root_count") == 1
                and value.get("legacy_asset_count") == 2
                and value.get("v2_js_count") == 0 and value.get("v2_css_count") == 0
                and value.get("v2_rest_status") == 404
                and value.get("v2_rest_code") == "rest_no_route"
                and str(value.get("known_search_status_text", "")).startswith(
                    'Showing results for "hydrochloric acid"')
                and isinstance(value.get("known_search_result_rows"), int)
                and value["known_search_result_rows"] > 0
                and value.get("known_search_ajax_posts_200") == 1)


def downtime_passed(value: dict[str, Any]) -> bool:
    return bool(value.get("mode") == "inactive_gap" and value.get("page_status") == 200
                and not value.get("blank") and not value.get("fatal")
                and value.get("root_count") == 0 and value.get("legacy_asset_count") == 0
                and value.get("v2_js_count") == 0 and value.get("v2_css_count") == 0
                and value.get("v2_rest_status") == 404
                and value.get("v2_rest_code") == "rest_no_route")


def read_admin_state() -> dict[str, Any]:
    with admin_session() as admin:
        rows = admin.read_rows()
        page = admin.read_page()
    return {"rows": rows, "page": page}


def expected_after(action: str, before: dict[str, Any]) -> dict[str, Any]:
    marker_v2 = bool(before["rows"]["v2"]["update_marker"])
    marker_legacy = bool(before["rows"]["legacy"]["update_marker"])
    page = dict(before["page"])
    if action == "legacy_deactivate":
        legacy_active, v2_active = False, False
    elif action == "page_shortcode_replace":
        legacy_active, v2_active = False, False
        page.update({"modified_gmt": "FROM_COMMIT_READBACK",
                     "content_bytes": PAGE_AFTER_BYTES,
                     "content_sha256": PAGE_AFTER_SHA256,
                     "old_shortcode_count": 0, "new_shortcode_count": 1,
                     "fingerprint": "FROM_COMMIT_READBACK"})
    elif action == "v2_activate":
        legacy_active, v2_active = False, True
    else:
        raise MigrationError("Unsupported migration action.")
    return {
        "rows": {
            "v2": project_row(V2_PLUGIN_FILE, True, v2_active, V2_VERSION, marker_v2),
            "legacy": project_row(LEGACY_PLUGIN_FILE, True, legacy_active,
                                  LEGACY_VERSION, marker_legacy),
        },
        "page": page,
    }


def attempt_lock_path(plan: Path) -> Path:
    return plan.with_suffix(".attempt-lock.json")


def result_path(plan: Path) -> Path:
    return plan.with_suffix(".result.json")


def _exclusive_json(path: Path, value: dict[str, Any], mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError as exc:
        raise MigrationError(f"File already exists and is immutable: {path}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=True) + "\n")
    os.chmod(path, mode)


def write_attempt_lock(path: Path, plan: dict[str, Any]) -> None:
    _exclusive_json(path, {"plan_sha256": plan["sha256"], "status": "in_flight",
                           "started_utc": utc_now().isoformat(), "action": plan["action"],
                           "attempts_allowed": 1, "retry": False},
                    stat.S_IREAD | stat.S_IWRITE)


def close_attempt(path: Path, plan: dict[str, Any], status_text: str,
                  detail: dict[str, Any]) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    value = {"plan_sha256": plan["sha256"], "status": status_text,
             "updated_utc": utc_now().isoformat(), "retry": False, **detail}
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IREAD)


def _dependency_record(plan_path: Path, plan_sha: str, result: Path,
                       result_sha: str, action: str) -> dict[str, Any]:
    return {"action": action, "plan": str(plan_path), "plan_sha256": plan_sha,
            "result": str(result), "result_file_sha256": result_sha,
            "required_status": "COMMITTED_AND_VERIFIED"}


def install_dependency() -> dict[str, Any]:
    if INSTALL_PLAN_PATH.resolve() != v2.resolve_plan_path(str(INSTALL_PLAN_PATH)):
        raise MigrationError("Inactive v2 install plan path identity failed.")
    plan = v2.load_plan(str(INSTALL_PLAN_PATH))
    if plan["sha256"] != INSTALL_PLAN_SHA256 or plan["action"] != "plugin_install_or_replace":
        raise MigrationError("Inactive v2 install plan identity/action failed.")
    result = v2.result_path(INSTALL_PLAN_PATH)
    try:
        value = json.loads(result.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError("Exact inactive v2 installation is not yet committed and verified.") from exc
    expected_after_row = project_row(V2_PLUGIN_FILE, True, False, V2_VERSION, False)
    if (value.get("status") != "COMMITTED_AND_VERIFIED"
            or value.get("action") != "plugin_install_or_replace"
            or value.get("plan_sha256") != INSTALL_PLAN_SHA256
            or Path(str(value.get("plan", ""))).resolve() != INSTALL_PLAN_PATH.resolve()
            or value.get("plugin_file") != V2_PLUGIN_FILE
            or value.get("after") != expected_after_row
            or value.get("activated") is not False or value.get("retry") is not False):
        raise MigrationError("Inactive v2 installation result contract failed.")
    return _dependency_record(INSTALL_PLAN_PATH, INSTALL_PLAN_SHA256, result,
                              file_sha256(result), "inactive_v2_install")


def resolve_plan_path(raw: str) -> Path:
    path = Path(raw).resolve()
    if PLAN_DIR.resolve() not in path.parents:
        raise MigrationError("Plan must be inside the fixed Derakane migration plan directory.")
    return path


def load_result(path: Path, expected_action: str | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError("Migration result is unavailable or unreadable.") from exc
    if not isinstance(value, dict):
        raise MigrationError("Migration result must be one object.")
    saved = str(value.pop("sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(value)):
        raise MigrationError("Migration result hash failed.")
    if (value.get("schema_version") != RESULT_SCHEMA_VERSION or value.get("tool") != TOOL_NAME
            or value.get("tool_version") != TOOL_VERSION
            or value.get("status") != "COMMITTED_AND_VERIFIED"
            or value.get("action") not in ACTIONS or value.get("retry") is not False
            or (expected_action is not None and value.get("action") != expected_action)):
        raise MigrationError("Migration predecessor result contract failed.")
    value["sha256"] = saved
    return value


def migration_dependency(raw_predecessor: str, expected_action: str) -> dict[str, Any]:
    path = resolve_plan_path(raw_predecessor)
    plan = load_plan(str(path))
    if plan["action"] != expected_action:
        raise MigrationError("Predecessor plan action is not the exact required action.")
    result = result_path(path)
    value = load_result(result, expected_action)
    if (value.get("plan_sha256") != plan["sha256"]
            or Path(str(value.get("plan", ""))).resolve() != path.resolve()):
        raise MigrationError("Predecessor result does not bind the exact plan.")
    return _dependency_record(path, plan["sha256"], result, file_sha256(result), expected_action)


def verify_dependency(dependency: dict[str, Any]) -> None:
    result = Path(dependency["result"]).resolve()
    if file_sha256(result) != dependency["result_file_sha256"]:
        raise MigrationError("REFUSED: predecessor result bytes drifted after plan review.")
    if dependency["action"] == "inactive_v2_install":
        live = install_dependency()
        if live != dependency:
            raise MigrationError("REFUSED: inactive-install dependency drifted.")
        return
    plan_path = resolve_plan_path(dependency["plan"])
    predecessor = load_plan(str(plan_path))
    value = load_result(result, dependency["action"])
    if (predecessor["sha256"] != dependency["plan_sha256"]
            or value["plan_sha256"] != dependency["plan_sha256"]
            or Path(value["plan"]).resolve() != plan_path):
        raise MigrationError("REFUSED: exact predecessor dependency failed.")


ROW_KEYS = frozenset({"present", "active", "version", "update_marker", "plugin_file",
                      "fingerprint"})
PAGE_KEYS = frozenset({"id", "slug", "status", "link", "date_gmt", "modified_gmt",
                       "title_raw", "content_bytes", "content_sha256",
                       "old_shortcode_count", "new_shortcode_count", "fingerprint"})
DEPENDENCY_KEYS = frozenset({"action", "plan", "plan_sha256", "result",
                             "result_file_sha256", "required_status"})
PLAN_KEYS = frozenset({"schema_version", "tool", "tool_version", "action", "origin",
                       "created_utc", "expires_utc", "nonce", "identities", "dependency",
                       "before", "expected_after", "public_before", "validation", "rollback"})


def validate_row(row: Any, plugin_file: str, version: str) -> None:
    if (not isinstance(row, dict) or set(row) != ROW_KEYS
            or row.get("present") is not True or row.get("plugin_file") != plugin_file
            or row.get("version") != version or not isinstance(row.get("active"), bool)
            or row_fingerprint(row) != row.get("fingerprint")):
        raise MigrationError("Plan exact plugin-row projection is invalid.")


def validate_page(page: Any) -> None:
    if not isinstance(page, dict) or set(page) != PAGE_KEYS:
        raise MigrationError("Plan exact page projection schema is invalid.")
    assert_page_identity(page)


def validate_public(value: Any, mode: str) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("fingerprint"), str):
        raise MigrationError("Plan anonymous baseline is invalid.")
    core = dict(value)
    saved = core.pop("fingerprint")
    if mode == "legacy":
        core.pop("passed", None)
        # legacy_findings fingerprints the fields before adding passed.
        if not legacy_passed(value):
            raise MigrationError("Plan legacy anonymous baseline is unhealthy.")
    elif not downtime_passed(value):
        raise MigrationError("Plan inactive-gap anonymous baseline is unhealthy.")
    if not secrets.compare_digest(saved, digest_for(core)):
        raise MigrationError("Plan anonymous baseline fingerprint is invalid.")


def write_plan(action: str, dependency: dict[str, Any], before: dict[str, Any],
               public_before: dict[str, Any]) -> Path:
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "action": action, "origin": EXACT_ORIGIN, "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=24)).isoformat(),
        "nonce": secrets.token_hex(16), "identities": dict(IDENTITIES),
        "dependency": dependency, "before": before,
        "expected_after": expected_after(action, before),
        "public_before": public_before, "validation": dict(VALIDATION[action]),
        "rollback": dict(ROLLBACK),
    }
    digest = digest_for(core)
    value = {**core, "sha256": digest}
    path = PLAN_DIR / f"{created.strftime('%Y%m%dT%H%M%SZ')}_{action}_{digest}.json"
    _exclusive_json(path, value, stat.S_IREAD)
    append_receipt("derakane_v2_migration_plan_staged",
                   f"action={action}; plan={path}; sha256={digest}")
    return path


def load_plan(raw: str) -> dict[str, Any]:
    path = resolve_plan_path(raw)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError("Migration plan is unreadable.") from exc
    if not isinstance(value, dict):
        raise MigrationError("Migration plan must be one object.")
    saved = str(value.pop("sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(value)):
        raise MigrationError("Plan hash failed; reviewed plan is not immutable.")
    if set(value) != PLAN_KEYS:
        raise MigrationError("Plan schema is not the exact closed set.")
    if (value["schema_version"] != SCHEMA_VERSION or value["tool"] != TOOL_NAME
            or value["tool_version"] != TOOL_VERSION or value["origin"] != EXACT_ORIGIN
            or value["action"] not in ACTIONS or value["identities"] != IDENTITIES):
        raise MigrationError("Plan identity is invalid.")
    try:
        created = datetime.fromisoformat(str(value["created_utc"]))
        expires = datetime.fromisoformat(str(value["expires_utc"]))
    except (TypeError, ValueError) as exc:
        raise MigrationError("Plan timestamps are invalid.") from exc
    if (created.tzinfo is None or created.utcoffset() != timedelta(0)
            or expires.tzinfo is None or expires.utcoffset() != timedelta(0)
            or expires != created + timedelta(hours=24)):
        raise MigrationError("Plan is not an exact 24-hour UTC plan.")
    now = utc_now()
    if created > now + timedelta(minutes=PLAN_CLOCK_SKEW_MINUTES):
        raise MigrationError("Plan creation time is in the future.")
    if now >= expires:
        raise MigrationError("Plan expired; stage a new exact step plan.")
    if not re.fullmatch(r"[0-9a-f]{32}", str(value["nonce"])):
        raise MigrationError("Plan nonce is invalid.")
    dep = value["dependency"]
    if (not isinstance(dep, dict) or set(dep) != DEPENDENCY_KEYS
            or dep["action"] != ACTION_PREDECESSOR[value["action"]]
            or dep["required_status"] != "COMMITTED_AND_VERIFIED"
            or not re.fullmatch(r"[0-9a-f]{64}", str(dep["plan_sha256"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(dep["result_file_sha256"]))):
        raise MigrationError("Plan exact predecessor dependency is invalid.")
    before = value["before"]
    if not isinstance(before, dict) or set(before) != {"rows", "page"}:
        raise MigrationError("Plan before state is invalid.")
    if not isinstance(before["rows"], dict) or set(before["rows"]) != {"v2", "legacy"}:
        raise MigrationError("Plan row state is invalid.")
    validate_row(before["rows"]["v2"], V2_PLUGIN_FILE, V2_VERSION)
    validate_row(before["rows"]["legacy"], LEGACY_PLUGIN_FILE, LEGACY_VERSION)
    validate_page(before["page"])
    action = value["action"]
    required = {
        "legacy_deactivate": (False, True, PAGE_BEFORE_SHA256),
        "page_shortcode_replace": (False, False, PAGE_BEFORE_SHA256),
        "v2_activate": (False, False, PAGE_AFTER_SHA256),
    }[action]
    if (before["rows"]["v2"]["active"], before["rows"]["legacy"]["active"],
            before["page"]["content_sha256"]) != required:
        raise MigrationError("Plan exact action precondition state is invalid.")
    if action in ("legacy_deactivate", "page_shortcode_replace") and not page_has_old(
            before["page"], initial_modified=True):
        raise MigrationError("Plan does not pin the exact original page bytes/timestamp.")
    if action == "v2_activate" and not page_has_new(before["page"]):
        raise MigrationError("Plan does not pin the exact migrated page bytes.")
    if value["expected_after"] != expected_after(action, before):
        raise MigrationError("Plan expected state is invalid.")
    if value["validation"] != VALIDATION[action] or value["rollback"] != ROLLBACK:
        raise MigrationError("Plan validation/rollback disclosure is invalid.")
    validate_public(value["public_before"],
                    "legacy" if action == "legacy_deactivate" else "inactive_gap")
    value["sha256"] = saved
    return value


def states_equal(live: dict[str, Any], expected: dict[str, Any]) -> bool:
    return bool(live["rows"] == expected["rows"]
                and live["page"]["fingerprint"] == expected["page"]["fingerprint"])


def dependency_after_state(dependency: dict[str, Any]) -> dict[str, Any] | None:
    if dependency["action"] == "inactive_v2_install":
        return None
    result = load_result(Path(dependency["result"]), dependency["action"])
    return result["after"]


def stage_preconditions(action: str, dependency: dict[str, Any], live: dict[str, Any]) -> None:
    verify_page_fixture()
    verify_dependency(dependency)
    validate_row(live["rows"]["v2"], V2_PLUGIN_FILE, V2_VERSION)
    validate_row(live["rows"]["legacy"], LEGACY_PLUGIN_FILE, LEGACY_VERSION)
    validate_page(live["page"])
    required = {
        "legacy_deactivate": (False, True, PAGE_BEFORE_SHA256),
        "page_shortcode_replace": (False, False, PAGE_BEFORE_SHA256),
        "v2_activate": (False, False, PAGE_AFTER_SHA256),
    }[action]
    actual = (live["rows"]["v2"]["active"], live["rows"]["legacy"]["active"],
              live["page"]["content_sha256"])
    if actual != required:
        raise MigrationError("REFUSED: exact live plugin/page state is not ready for this step.")
    if action in ("legacy_deactivate", "page_shortcode_replace"):
        if not page_has_old(live["page"], initial_modified=True):
            raise MigrationError("REFUSED: exact original page content/timestamp drifted.")
    elif not page_has_new(live["page"]):
        raise MigrationError("REFUSED: exact migrated page content is absent.")
    predecessor_after = dependency_after_state(dependency)
    if predecessor_after is not None and not states_equal(live, predecessor_after):
        raise MigrationError("REFUSED: live state does not exactly match predecessor readback.")


def stage_report(action: str, dependency: dict[str, Any], live: dict[str, Any],
                 public: dict[str, Any]) -> None:
    path = write_plan(action, dependency, live, public)
    plan = json.loads(path.read_text(encoding="utf-8"))
    emit({"status": "STAGED_NOT_COMMITTED", "action": action, "plan": str(path),
          "plan_sha256": plan["sha256"], "expires_utc": plan["expires_utc"],
          "dependency": dependency, "before": live,
          "expected_after": plan["expected_after"], "validation": plan["validation"],
          "rollback": plan["rollback"], "approval": APPROVAL_WORD,
          "non_atomic_downtime_disclosure": (
              "Separate approval is required for every step. After legacy deactivation and page "
              "replacement, public search remains unavailable until v2 activation succeeds."),
          "external_write_performed": False})


@holds_wordpress_browser("WordPress: read-only exact Derakane migration inspection")
def command_inspect(_: argparse.Namespace) -> None:
    before_text, after_text = verify_page_fixture()
    with admin_session() as admin:
        live = {"rows": admin.read_rows(allow_v2_absent=True), "page": admin.read_page()}
    with anonymous_session() as public:
        findings = (public.legacy_findings()
                    if live["rows"]["legacy"]["active"] else public.downtime_projection())
    install = {"plan": str(INSTALL_PLAN_PATH), "plan_sha256": INSTALL_PLAN_SHA256,
               "attempt_lock_exists": v2.attempt_lock_path(INSTALL_PLAN_PATH).exists(),
               "result_exists": v2.result_path(INSTALL_PLAN_PATH).exists()}
    emit({"status": "INSPECTED", "identities": IDENTITIES, "live": live,
          "page_fixture": {"path": str(PAGE_FIXTURE), "before_bytes": len(before_text.encode()),
                           "before_sha256": PAGE_BEFORE_SHA256,
                           "after_bytes": len(after_text.encode()),
                           "after_sha256": PAGE_AFTER_SHA256},
          "public": findings, "inactive_install": install,
          "ready_to_stage_deactivate": bool(
              install["result_exists"] and live["rows"]["v2"]["active"] is False
              and live["rows"]["legacy"]["active"] is True
              and page_has_old(live["page"], initial_modified=True)),
          "business_write_performed": False})


@holds_wordpress_browser("WordPress: stage exact legacy Derakane deactivation")
def command_stage_deactivate(_: argparse.Namespace) -> None:
    dependency = install_dependency()
    live = read_admin_state()
    stage_preconditions("legacy_deactivate", dependency, live)
    with anonymous_session() as public:
        projection = public.legacy_findings()
    if not projection["passed"]:
        raise MigrationError("REFUSED: exact legacy anonymous search baseline is unhealthy.")
    stage_report("legacy_deactivate", dependency, live, projection)


@holds_wordpress_browser("WordPress: stage exact Derakane page 1840 shortcode replacement")
def command_stage_update_page(args: argparse.Namespace) -> None:
    dependency = migration_dependency(args.predecessor_plan, "legacy_deactivate")
    live = read_admin_state()
    stage_preconditions("page_shortcode_replace", dependency, live)
    with anonymous_session() as public:
        projection = public.downtime_projection()
    if not downtime_passed(projection):
        raise MigrationError("REFUSED: inactive-gap public projection is unexpected.")
    stage_report("page_shortcode_replace", dependency, live, projection)


@holds_wordpress_browser("WordPress: stage exact Derakane v2 activation")
def command_stage_activate_v2(args: argparse.Namespace) -> None:
    dependency = migration_dependency(args.predecessor_plan, "page_shortcode_replace")
    live = read_admin_state()
    stage_preconditions("v2_activate", dependency, live)
    with anonymous_session() as public:
        projection = public.downtime_projection()
    if not downtime_passed(projection):
        raise MigrationError("REFUSED: inactive-gap public projection is unexpected.")
    stage_report("v2_activate", dependency, live, projection)


def open_commit(args: argparse.Namespace, action: str) -> tuple[Path, dict[str, Any]]:
    path = resolve_plan_path(args.plan)
    plan = load_plan(str(path))
    if plan["action"] != action:
        raise MigrationError("Plan action does not match command.")
    # Approval precedes dependency files, fixture, browser/CDP, anonymous HTTP, and lock.
    require_approval(args.approval)
    if attempt_lock_path(path).exists() or result_path(path).exists():
        raise MigrationError("Plan already entered its one allowed attempt; no retry.")
    return path, plan


def verify_preflight(plan: dict[str, Any]) -> None:
    verify_dependency(plan["dependency"])
    verify_page_fixture()
    live = read_admin_state()
    if not states_equal(live, plan["before"]):
        raise MigrationError("REFUSED: exact plugin/page state drifted after review.")
    predecessor_after = dependency_after_state(plan["dependency"])
    if predecessor_after is not None and not states_equal(live, predecessor_after):
        raise MigrationError("REFUSED: exact predecessor state no longer matches live.")
    with anonymous_session() as public:
        projection = (public.legacy_findings() if plan["action"] == "legacy_deactivate"
                      else public.downtime_projection())
    if projection["fingerprint"] != plan["public_before"]["fingerprint"]:
        raise MigrationError("REFUSED: anonymous baseline drifted after review.")


def record_result(plan_path: Path, plan: dict[str, Any], status_text: str,
                  detail: dict[str, Any]) -> None:
    core = {
        "schema_version": RESULT_SCHEMA_VERSION, "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION, "status": status_text, "action": plan["action"],
        "plan": str(plan_path), "plan_sha256": plan["sha256"],
        "dependency": plan["dependency"], "identities": IDENTITIES,
        "recorded_utc": utc_now().isoformat(), "retry": False, **detail,
    }
    value = {**core, "sha256": digest_for(core)}
    _exclusive_json(result_path(plan_path), value, stat.S_IREAD)
    append_receipt("derakane_v2_migration_" + status_text.casefold(),
                   f"action={plan['action']}; plan={plan_path}; sha256={plan['sha256']}")


def restore_legacy_service() -> dict[str, Any]:
    """Attempt each fixed compensation at most once; never overwrite unknown content."""
    before_text, _ = verify_page_fixture()
    restoration: dict[str, Any] = {
        "attempted": True, "atomic": False, "v2_deactivate_attempted": False,
        "page_restore_attempted": False, "legacy_reactivate_attempted": False,
        "errors": [], "fully_restored": False,
    }
    try:
        with admin_session() as admin:
            rows = admin.read_rows()
            if rows["v2"]["active"] is True:
                restoration["v2_deactivate_attempted"] = True
                try:
                    admin.deactivate_v2()
                except Exception as exc:
                    restoration["errors"].append("v2_deactivate:" + type(exc).__name__)
            try:
                page = admin.read_page()
                if page_has_new(page):
                    restoration["page_restore_attempted"] = True
                    admin.write_page_content(before_text)
                elif not page_has_old(page, initial_modified=False):
                    restoration["errors"].append("page_restore:unknown_content_refused")
            except Exception as exc:
                restoration["errors"].append("page_restore:" + type(exc).__name__)
            try:
                rows = admin.read_rows()
                if rows["legacy"]["active"] is False:
                    restoration["legacy_reactivate_attempted"] = True
                    admin.reactivate_legacy()
            except Exception as exc:
                restoration["errors"].append("legacy_reactivate:" + type(exc).__name__)
            try:
                restoration["admin_after"] = {"rows": admin.read_rows(),
                                               "page": admin.read_page()}
            except Exception as exc:
                restoration["errors"].append("admin_readback:" + type(exc).__name__)
    except Exception as exc:
        restoration["errors"].append("admin_session:" + type(exc).__name__)
    try:
        with anonymous_session() as public:
            restoration["public_after"] = public.legacy_findings()
    except Exception as exc:
        restoration["errors"].append("legacy_qa:" + type(exc).__name__)
    final = restoration.get("admin_after", {})
    rows = final.get("rows", {})
    page = final.get("page", {})
    restoration["fully_restored"] = bool(
        not restoration["errors"]
        and rows.get("v2", {}).get("active") is False
        and rows.get("legacy", {}).get("active") is True
        and isinstance(page, dict) and page_has_old(page, initial_modified=False)
        and restoration.get("public_after", {}).get("passed") is True)
    return restoration


def fail_after_attempt(lock: Path, plan_path: Path, plan: dict[str, Any], stage: str,
                       exc: Exception) -> None:
    restoration = restore_legacy_service()
    status = "FAILED_CLOSED_RESTORED" if restoration["fully_restored"] else "INDETERMINATE"
    detail = {"stage": stage, "reason": type(exc).__name__,
              "restoration": restoration, "after": restoration.get("admin_after")}
    close_attempt(lock, plan, status.casefold(), detail)
    record_result(plan_path, plan, status, detail)
    if restoration["fully_restored"]:
        raise MigrationError(
            "Step failed; compensating restoration returned exact legacy service; plan closed.") from exc
    raise IndeterminateError(
        "Step failed and complete compensating restoration was not proven; plan closed.") from exc


def complete_success(plan_path: Path, plan: dict[str, Any], lock: Path,
                     after: dict[str, Any], public_after: dict[str, Any]) -> None:
    detail = {"after": after, "public_after": public_after,
              "compensating_restoration_attempted": False}
    close_attempt(lock, plan, "committed_verified", detail)
    record_result(plan_path, plan, "COMMITTED_AND_VERIFIED", detail)
    emit({"status": "COMMITTED_AND_VERIFIED", "action": plan["action"],
          "after": after, "public_after": public_after, "plan_sha256": plan["sha256"],
          "replay_locked": True, "retry": False})


def final_prelock(plan: dict[str, Any]) -> tuple[Admin, Any, dict[str, Any]]:
    context = admin_session()
    admin = context.__enter__()
    try:
        live = {"rows": admin.read_rows(), "page": admin.read_page()}
        if not states_equal(live, plan["before"]):
            raise MigrationError("REFUSED: exact state drifted immediately before attempt lock.")
        return admin, context, live
    except Exception:
        context.__exit__(*sys.exc_info())
        raise


@holds_wordpress_browser("WordPress: commit exact legacy Derakane deactivation")
def command_commit_deactivate(args: argparse.Namespace) -> None:
    plan_path, plan = open_commit(args, "legacy_deactivate")
    verify_preflight(plan)
    lock = attempt_lock_path(plan_path)
    admin, context, _ = final_prelock(plan)
    write_attempt_lock(lock, plan)
    error: Exception | None = None
    try:
        admin.goto_plugins()
        admin.deactivate_legacy()
    except Exception as exc:
        error = exc
    finally:
        context.__exit__(None, None, None)
    if error is not None:
        fail_after_attempt(lock, plan_path, plan, "deactivate_legacy", error)
    try:
        after = read_admin_state()
        expected = plan["expected_after"]
        if not states_equal(after, expected):
            raise IndeterminateError("Complete admin readback did not match deactivation plan.")
        with anonymous_session() as public:
            public_after = public.downtime_projection()
        if not downtime_passed(public_after):
            raise MigrationError("Known inactive-gap public validation failed.")
    except Exception as exc:
        fail_after_attempt(lock, plan_path, plan, "complete_readback", exc)
    complete_success(plan_path, plan, lock, after, public_after)


@holds_wordpress_browser("WordPress: commit exact Derakane page 1840 shortcode replacement")
def command_commit_update_page(args: argparse.Namespace) -> None:
    plan_path, plan = open_commit(args, "page_shortcode_replace")
    verify_preflight(plan)
    lock = attempt_lock_path(plan_path)
    admin, context, _ = final_prelock(plan)
    write_attempt_lock(lock, plan)
    error: Exception | None = None
    try:
        _, after_text = verify_page_fixture()
        admin.write_page_content(after_text)
    except Exception as exc:
        error = exc
    finally:
        context.__exit__(None, None, None)
    if error is not None:
        fail_after_attempt(lock, plan_path, plan, "update_exact_page", error)
    try:
        after = read_admin_state()
        if (after["rows"] != plan["expected_after"]["rows"]
                or not page_has_new(after["page"])):
            raise IndeterminateError("Complete admin readback did not match exact page migration.")
        with anonymous_session() as public:
            public_after = public.downtime_projection()
        if not downtime_passed(public_after):
            raise MigrationError("Known inactive-gap public validation failed after page update.")
    except Exception as exc:
        fail_after_attempt(lock, plan_path, plan, "complete_readback", exc)
    complete_success(plan_path, plan, lock, after, public_after)


@holds_wordpress_browser("WordPress: commit exact Derakane v2 activation and QA")
def command_commit_activate_v2(args: argparse.Namespace) -> None:
    plan_path, plan = open_commit(args, "v2_activate")
    verify_preflight(plan)
    lock = attempt_lock_path(plan_path)
    admin, context, _ = final_prelock(plan)
    write_attempt_lock(lock, plan)
    error: Exception | None = None
    try:
        admin.goto_plugins()
        admin.activate_v2()
    except Exception as exc:
        error = exc
    finally:
        context.__exit__(None, None, None)
    if error is not None:
        fail_after_attempt(lock, plan_path, plan, "activate_v2", error)
    try:
        with anonymous_session() as public:
            public_after = public.v2_findings()
        if not public_after.get("passed"):
            raise MigrationError("Known anonymous v2 page/REST/search/browser UI QA failed.")
        after = read_admin_state()
        if (after["rows"] != plan["expected_after"]["rows"]
                or after["page"]["fingerprint"] != plan["before"]["page"]["fingerprint"]
                or not page_has_new(after["page"])
                or after["page"]["modified_gmt"] != plan["before"]["page"]["modified_gmt"]):
            raise IndeterminateError("Complete final admin readback drifted after v2 QA.")
    except Exception as exc:
        fail_after_attempt(lock, plan_path, plan, "v2_anonymous_and_admin_qa", exc)
    complete_success(plan_path, plan, lock, after, public_after)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect").set_defaults(func=command_inspect)
    commands.add_parser("stage-deactivate-legacy").set_defaults(func=command_stage_deactivate)
    for name, handler in (
        ("stage-update-page", command_stage_update_page),
        ("stage-activate-v2", command_stage_activate_v2),
    ):
        command = commands.add_parser(name)
        command.add_argument("--predecessor-plan", required=True)
        command.set_defaults(func=handler)
    for name, handler in (
        ("commit-deactivate-legacy", command_commit_deactivate),
        ("commit-update-page", command_commit_update_page),
        ("commit-activate-v2", command_commit_activate_v2),
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
    except (MigrationError, OSError, ValueError, UiLaneBusy, UiLaneLockError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
