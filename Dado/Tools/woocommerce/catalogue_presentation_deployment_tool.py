#!/usr/bin/env python
"""Hardcoded deployment tool for one FRP Depot catalogue-presentation plugin.

Exactly six commands exist:

* stage-install-or-replace / commit-install-or-replace (always inactive)
* stage-activate / commit-activate
* stage-deactivate / commit-deactivate

There is no URL, slug, ZIP, selector, browser, delete, retry, generic-plugin or
free-form route. Stage commands only inspect the fixed plugin row and write a
24-hour immutable hashed local plan. Commit commands require the exact unpadded
uppercase word APPROVED. The shared WordPress browser mutex wraps the whole
commit before its exclusive one-attempt lock can be created. Public verification
uses a new anonymous, non-persistent Edge context. A confirmed activation whose
known validation contract fails is automatically deactivated exactly once.
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

# The existing cross-lane WordPress browser mutex. Appending prevents stdlib shadowing.
sys.path.append(str(Path(__file__).resolve().parent.parent / "common"))
from ui_lane_lock import UiLaneBusy, UiLaneLockError, ui_browser_lock  # noqa: E402

TOOL_NAME = "FRP Depot Automatic Catalogue Presentation Deployment Tool"
TOOL_VERSION = "1.1.1"
SCHEMA_VERSION = 5
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "catalogue_presentation_plugin_plans"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
RUNTIME_TEMP_DIR = ROOT / "Dado" / "20_Working" / "catalogue_playwright_tmp"
PLAN_LIFETIME_HOURS = 24
PLAN_CLOCK_SKEW_MINUTES = 5
APPROVAL_WORD = "APPROVED"

EXACT_ORIGIN = "https://frpdepots.com"
ALLOWED_HOST = "frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
PLUGIN_NAME = "FRP Depot Automatic Catalogue Presentation"
PLUGIN_SLUG = "frpdepot-automatic-catalogue-presentation"
PLUGIN_FILE = f"{PLUGIN_SLUG}/frpdepot-automatic-catalogue-presentation.php"
ARTIFACT_PATH = (ROOT / "Dado" / "Tools" / "woocommerce" / "catalogue_presentation"
                 / "frpdepot-automatic-catalogue-presentation.zip")
ARTIFACT_VERSION = "1.1.1"
PREDECESSOR_VERSION = "1.1.0"
DEACTIVATABLE_VERSIONS = frozenset({PREDECESSOR_VERSION, ARTIFACT_VERSION})
# Superseded artifact and plan identities are refused explicitly. This includes
# every earlier artifact; none can be restaged under the public-warning fix.
SUPERSEDED_ARTIFACT_SHA256 = frozenset({
    "b1e9ed3959ff38a003a44bd876ec0a0c29386fa54d5cd3cc5a879de99c098542",
    "9047bbb55637b46a30e2a3109affa24304d1e21d3c46479159214fb25dfb3f6c",
    "472d8fa0bd647b255093301386c2ce94779b8005daa76eadcf18169f372f468c",
    "efdfc817b3b37c93f8597e7dafb31f9da46e48163b0729a140076d132901e462",
    # v1.1.0 rendered every product projection and PDF exactly, but the live
    # Divi archive template omitted the Woo archive-description action. Its
    # five category panels therefore stayed absent and activation failed closed.
    "b19c54cad7439f25b00d92834f551169f1c111ee7da8a08ad4d64d2dcf5c5d4b",
})
SUPERSEDED_PLAN_SHA256 = frozenset({
    "1984a36095329ef5b20cd1a2f7c76fa3bf2120f21a1f124b564718bc406e6e8f",
    "d3d4cd48b43d0067760e4ac6e2bbc86a135d9087d55aa914c86dbe9d931e1bf9",
    # The 2026-08-14 replacement attempt reached WordPress's expired-link page.
    # Its permanent attempt lock remains authoritative; naming the digest here
    # also prevents a future build from interpreting that reviewed plan anew.
    "53863d55db4a555e98ee55daaf78a3c971b053878f6ebe0742c8dd2ca99289d1",
    # v1.0.4 staged predecessor deactivation but its commit guard still required
    # the new artifact version. It failed before clicking and is permanently closed.
    "46258fac8d0e42647094463beaa7b3b0d18711e6c6cec65fa2398c5537a75ba7",
    # The first v1.0.4 replacement attempt reached the overwrite path but fresh
    # readback still showed the predecessor. Its attempt is permanently closed.
    "3a1cc8096e6b13c1cf59d5319c64d7c5c21e57a4fa0cc9d784442da9ec8aa62f",
    # v1.1.0 deactivation, replacement and failed-closed activation plans are
    # preserved as history and can never be interpreted by the v1.1.1 tool.
    "53006df1284dad627f78e0b2879ddf4358acd6bb8525b9de537d49c1dac4977a",
    "7f08e94d923622e6d4064d9b62b7c7df84dc71cfb5e10dad435418281a78f003",
    "dc784d2e184bb423350860378ca2a4f5c285b5b0d68df91ab66e1cac6ee70239",
})
ARTIFACT_SHA256 = "1acbb05474b4d173cbdae72bdc5416a1826c7d12393f4cde62460f0979195bf9"
ARTIFACT_BYTES = 9676386
POST_WRITE_READ_ROUNDS = 3
POST_WRITE_READ_DELAY_MS = 1_000
ARTIFACT_MEMBERS = (
    f"{PLUGIN_SLUG}/frpdepot-automatic-catalogue-presentation.php",
    f"{PLUGIN_SLUG}/readme.txt",
    f"{PLUGIN_SLUG}/catalogue-sections/FRP_Depots_Stub_Flanges_2026.pdf",
    f"{PLUGIN_SLUG}/catalogue-sections/FRP_Depots_Manways_and_Covers_2026.pdf",
    f"{PLUGIN_SLUG}/catalogue-sections/FRP_Depots_90_Degree_Elbows_2026.pdf",
    f"{PLUGIN_SLUG}/catalogue-sections/FRP_Depots_Filament_Wound_Pipe_2026.pdf",
    f"{PLUGIN_SLUG}/catalogue-sections/FRP_Depots_FNPT_Couplings_2026.pdf",
)
ARTIFACT_MEMBER_SHA256 = {
    f"{PLUGIN_SLUG}/frpdepot-automatic-catalogue-presentation.php":
        "9d55a1d1e26a71233b83cac0d524bf4e3822c477a18024557a78917a9d523877",
    f"{PLUGIN_SLUG}/readme.txt":
        "150a6e38326c3263ec761fc03b41691c12c57d0e0985e1ac7cf3e0b2d2573188",
    f"{PLUGIN_SLUG}/catalogue-sections/FRP_Depots_Stub_Flanges_2026.pdf":
        "f009764259b11136a3f7126de6a678773e0e4ed21293cd9e95a71c0f0a4cd4b6",
    f"{PLUGIN_SLUG}/catalogue-sections/FRP_Depots_Manways_and_Covers_2026.pdf":
        "09b8bc59a2d81fdff4c3d4ad7110f3fac27752c0f03d162ee24165b5e4ed3e65",
    f"{PLUGIN_SLUG}/catalogue-sections/FRP_Depots_90_Degree_Elbows_2026.pdf":
        "ead009c50b7f8cc338b80928084c6ef24141477e5addea7806a4b7da6547fcb2",
    f"{PLUGIN_SLUG}/catalogue-sections/FRP_Depots_Filament_Wound_Pipe_2026.pdf":
        "f4fbaf8cb72e7b41f22ddb170185595e12d109394c136edde79f902dd2f65fc2",
    f"{PLUGIN_SLUG}/catalogue-sections/FRP_Depots_FNPT_Couplings_2026.pdf":
        "19efa8d20a1be17a1451ad24f6b1d45c2f1e53b3fb58cece5aed496acc91db33",
}

ACTIONS = ("plugin_install_or_replace", "plugin_activate", "plugin_deactivate")
COMMANDS = (
    "stage-install-or-replace", "commit-install-or-replace",
    "stage-activate", "commit-activate",
    "stage-deactivate", "commit-deactivate",
)

PLUGINS_URL = f"{EXACT_ORIGIN}/wp-admin/plugins.php"
UPLOAD_URL = f"{EXACT_ORIGIN}/wp-admin/plugin-install.php?tab=upload"
ALLOWED_ADMIN_PATHS = frozenset({
    "/wp-admin/plugins.php", "/wp-admin/plugin-install.php", "/wp-admin/update.php",
})
HOME_URL = f"{EXACT_ORIGIN}/"
SHOP_PATH = "/products/"
SHOP_TITLE = "Shop All"
PRODUCT_URLS = {
    1455: f"{EXACT_ORIGIN}/product/frp-fw-pipe/",
    1423: f"{EXACT_ORIGIN}/product/frp-elbow-90/",
    1368: f"{EXACT_ORIGIN}/product/frp-stub-flange/",
    1397: f"{EXACT_ORIGIN}/product/frp-manway/",
    1411: f"{EXACT_ORIGIN}/product/frp-manway-cover/",
    2061: f"{EXACT_ORIGIN}/product/fnpt-coupling-threaded-on-both-ends/",
}
CATEGORY_URLS = {
    60: f"{EXACT_ORIGIN}/product-category/manways/",
    58: f"{EXACT_ORIGIN}/product-category/piping-fluid-handling/",
    45: f"{EXACT_ORIGIN}/product-category/piping-fluid-handling/elbows/",
    57: f"{EXACT_ORIGIN}/product-category/piping-fluid-handling/flanges/",
    44: f"{EXACT_ORIGIN}/product-category/piping-fluid-handling/pipes/",
}
FULL_CATALOGUE_URL = f"{EXACT_ORIGIN}/frp-depots-final-hq/"
SECTION_PDFS = {
    "stub_flanges": {
        "filename": "FRP_Depots_Stub_Flanges_2026.pdf", "bytes": 1310780,
        "sha256": "f009764259b11136a3f7126de6a678773e0e4ed21293cd9e95a71c0f0a4cd4b6",
    },
    "manways_and_covers": {
        "filename": "FRP_Depots_Manways_and_Covers_2026.pdf", "bytes": 1503479,
        "sha256": "09b8bc59a2d81fdff4c3d4ad7110f3fac27752c0f03d162ee24165b5e4ed3e65",
    },
    "elbows_90": {
        "filename": "FRP_Depots_90_Degree_Elbows_2026.pdf", "bytes": 4558184,
        "sha256": "ead009c50b7f8cc338b80928084c6ef24141477e5addea7806a4b7da6547fcb2",
    },
    "filament_wound_pipe": {
        "filename": "FRP_Depots_Filament_Wound_Pipe_2026.pdf", "bytes": 477528,
        "sha256": "f4fbaf8cb72e7b41f22ddb170185595e12d109394c136edde79f902dd2f65fc2",
    },
    "fnpt_couplings": {
        "filename": "FRP_Depots_FNPT_Couplings_2026.pdf", "bytes": 2001467,
        "sha256": "19efa8d20a1be17a1451ad24f6b1d45c2f1e53b3fb58cece5aed496acc91db33",
    },
}
SECTION_PDF_URLS = {
    key: f"{EXACT_ORIGIN}/wp-content/plugins/{PLUGIN_SLUG}/catalogue-sections/{row['filename']}"
    for key, row in SECTION_PDFS.items()
}
PRODUCT_SECTION_KEYS = {
    1368: "stub_flanges", 1397: "manways_and_covers", 1411: "manways_and_covers",
    1423: "elbows_90", 1455: "filament_wound_pipe", 2061: "fnpt_couplings",
}
CATEGORY_SECTION_KEYS = {
    44: ("filament_wound_pipe",), 45: ("elbows_90",), 57: ("stub_flanges",),
    58: ("filament_wound_pipe", "stub_flanges", "elbows_90", "fnpt_couplings"),
    60: ("manways_and_covers",),
}
PRODUCT_URL = PRODUCT_URLS[1455]
FNPT_URL = PRODUCT_URLS[2061]
FNPT_STORE_API_URL = f"{EXACT_ORIGIN}/wp-json/wc/store/v1/products/2061"
DERAKANE_PAGE_URL = f"{EXACT_ORIGIN}/derakane-resin-resistance-search/"
DERAKANE_REST_URL = f"{EXACT_ORIGIN}/wp-json/frpdepot-derakane/v1/search"
HETRON_ATTACHMENT_URL = f"{EXACT_ORIGIN}/hetron-cr-guide-2007_ineos/"
HETRON_ATTACHMENT_QUERY_URL = f"{EXACT_ORIGIN}/?attachment_id=1832"
HETRON_DIRECT_PDF_URL = (
    f"{EXACT_ORIGIN}/wp-content/uploads/2026/03/HETRON-CR-Guide-2007_Ineos.pdf"
)
ALLOWED_PUBLIC_PATHS = frozenset({
    "/", SHOP_PATH, *(urlsplit(url).path for url in PRODUCT_URLS.values()),
    *(urlsplit(url).path for url in CATEGORY_URLS.values()),
    *(urlsplit(url).path for url in SECTION_PDF_URLS.values()),
    "/wp-json/wc/store/v1/products/2061", "/derakane-resin-resistance-search/",
    "/wp-json/frpdepot-derakane/v1/search", "/hetron-cr-guide-2007_ineos/",
    "/wp-content/uploads/2026/03/HETRON-CR-Guide-2007_Ineos.pdf",
})

NAV_TIMEOUT_MS = 45_000
LOAD_STATE_TIMEOUT_MS = 45_000
ACTION_TIMEOUT_MS = 15_000
MIN_RENDERED_TEXT = 40
FATAL_MARKERS = (
    "there has been a critical error on this website", "fatal error", "parse error:",
    "call to undefined function", "warning: undefined property:",
)
HETRON_URL = "https://frpdepots.com/hetron-cr-guide-2007_ineos/"
DERAKANE_OLD_URL = "https://frpdepots.com/derakane-resin-selection-guide/"
DERAKANE_NEW_URL = "https://frpdepots.com/derakane-resin-resistance-search/"
INLINE_CTA_PATH = "/derakane-resin-resistance-search/"
EXPECTED_CATEGORY_IDS = frozenset({44, 45, 57, 58, 60})
EXPECTED_PRODUCT_IDS = frozenset({1368, 1397, 1411, 1423, 1455, 2061})
FNPT_PRODUCT_ID = 2061
FNPT_TARGET_CATEGORY_ID = 58
REMOVED_RESIN_OPTION = "Hetron 922"
MAIN_MENU_SELECTOR = "#menu-main"
FOOTER_MENU_SELECTOR = "#menu-product-categories"
HEADER_MOBILE_SELECTOR = ".et_pb_menu_0_tb_header ul.et_mobile_menu"
FOOTER_MOBILE_SELECTOR = ".et_pb_menu_0_tb_footer ul.et_mobile_menu"

ROW_SELECTOR = f'tr[data-plugin="{PLUGIN_FILE}"]:not(.plugin-update-tr)'
UPDATE_ROW_SELECTOR = f'tr.plugin-update-tr[data-plugin="{PLUGIN_FILE}"]'
ACTIVATE_SELECTOR = ".row-actions .activate a"
DEACTIVATE_SELECTOR = ".row-actions .deactivate a"
VERSION_SELECTOR = ".plugin-version-author-uri"
VERSION_PATTERN = re.compile(r"(?i)\bversion\s+([0-9][0-9A-Za-z.\-+_]*)")

VALIDATION_CONTRACT = {
    "anonymous": True,
    "persistent_profile": False,
    "product_pages": [PRODUCT_URLS[product_id] for product_id in sorted(PRODUCT_URLS)],
    "category_pages": [CATEGORY_URLS[category_id] for category_id in sorted(CATEGORY_URLS)],
    "public_php_warnings_allowed": False,
    "fatal_or_blank_allowed": False,
    "hetron_url_count": 0,
    "hetron_card_count": 0,
    "derakane_old_url_count": 0,
    "derakane_new_card_url_count": 1,
    "inline_cta_count": 1,
    "full_catalogue_product_url_count": 0,
    "section_catalogue_product_url_count": 1,
    "section_catalogue_card_count": 1,
    "section_catalogue_category_link_counts": {
        str(category_id): len(keys) for category_id, keys in CATEGORY_SECTION_KEYS.items()
    },
    "section_pdf_sha256": {
        key: row["sha256"] for key, row in SECTION_PDFS.items()
    },
    "menus": ["desktop", "mobile", "footer", "footer_mobile"],
    "required_category_ids": sorted(EXPECTED_CATEGORY_IDS),
    "required_product_ids": sorted(EXPECTED_PRODUCT_IDS),
    "additional_nonempty_approved_descendant_categories_allowed": True,
    "duplicates_allowed": False,
    "menu_category_product_bytes_identical": True,
    "shop_root": {"title": SHOP_TITLE, "path": SHOP_PATH},
    "fnpt_product_id": FNPT_PRODUCT_ID,
    "fnpt_parent_category_id": FNPT_TARGET_CATEGORY_ID,
    "hetron_public_content_allowed": False,
    "derakane_v2_page_and_rest_required": True,
    "obsolete_hetron_attachment_and_direct_pdf_unavailable": True,
    "on_known_activation_validation_failure": "deactivate fixed plugin exactly once",
    "retry": False,
}

ACTIVATION_PREREQUISITE_CONTRACT = {
    "all_six_public_product_pages_have_exact_untransformed_shared_guide_source": True,
    "all_six_public_product_pages_have_one_exact_full_catalogue_source_card": True,
    "all_five_bundled_section_pdfs_match_exact_bytes_and_sha256": True,
    "all_fixed_category_pages_have_no_section_panel_while_plugin_is_inactive": True,
    "fnpt_parent_only_category_58": True,
    "fnpt_parent_option_hetron_922_absent": True,
    "derakane_v2_page_and_rest_work": True,
    "hetron_attachment_route_query_alias_and_direct_pdf_unavailable": True,
    "one_top_level_products_root_exists": True,
    "persistent_menu_or_template_write": False,
}


class DeploymentError(RuntimeError):
    """Clean refusal or verified failure."""


class IndeterminateError(DeploymentError):
    """A write was attempted but final live state could not be proven."""


class KnownValidationFailure(DeploymentError):
    """The closed anonymous validation contract returned a known negative result."""

    def __init__(self, findings: dict[str, Any]):
        super().__init__("Known anonymous catalogue presentation validation failure.")
        self.findings = findings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"ts": utc_now().isoformat(), "action": action,
                                 "evidence": evidence}, ensure_ascii=True) + "\n")


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def holds_wordpress_browser(purpose: str):
    """Acquire the shared WordPress browser mutex around an entire command."""
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


def ensure_runtime_temp_ready() -> None:
    """Give Playwright a fixed local temp when Hermes' inherited per-call temp vanished."""
    names = ("TMP", "TEMP", "TMPDIR")
    if not all(os.environ.get(name) and Path(os.environ[name]).is_dir() for name in names):
        try:
            RUNTIME_TEMP_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DeploymentError(
                "REFUSED: fixed local Playwright temp directory is unavailable; "
                "no browser action attempted.") from exc
        stable = str(RUNTIME_TEMP_DIR.resolve())
        for name in names:
            os.environ[name] = stable
    if not all(Path(os.environ[name]).is_dir() for name in names):
        raise DeploymentError(
            "REFUSED: runtime temp directory is unavailable; no browser action attempted.")


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
        raise DeploymentError("REFUSED: public path is outside the fixed validation pages.")


def verify_artifact(path: Path | None = None) -> dict[str, Any]:
    artifact = Path(path or ARTIFACT_PATH)
    if artifact.resolve() != ARTIFACT_PATH.resolve():
        raise DeploymentError("REFUSED: only the hardcoded catalogue plugin artifact is allowed.")
    if not artifact.is_file():
        raise DeploymentError(f"Artifact missing: {artifact}")
    artifact_bytes = artifact.read_bytes()
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    if digest in SUPERSEDED_ARTIFACT_SHA256:
        raise DeploymentError("REFUSED: superseded catalogue plugin artifact hash.")
    if not secrets.compare_digest(digest, ARTIFACT_SHA256):
        raise DeploymentError("REFUSED: hardcoded artifact SHA-256 mismatch.")
    if len(artifact_bytes) != ARTIFACT_BYTES:
        raise DeploymentError("REFUSED: hardcoded artifact byte count mismatch.")
    try:
        with zipfile.ZipFile(artifact) as archive:
            members = tuple(sorted(archive.namelist()))
            member_bytes = {name: archive.read(name) for name in members}
            php = member_bytes[f"{PLUGIN_SLUG}/frpdepot-automatic-catalogue-presentation.php"]
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise DeploymentError("REFUSED: artifact is unreadable or incomplete.") from exc
    if members != tuple(sorted(ARTIFACT_MEMBERS)):
        raise DeploymentError("REFUSED: artifact member set is not exact.")
    member_sha256 = {name: hashlib.sha256(member_bytes[name]).hexdigest() for name in members}
    if member_sha256 != ARTIFACT_MEMBER_SHA256:
        raise DeploymentError("REFUSED: hardcoded artifact member SHA-256 mismatch.")
    source = php.decode("utf-8", errors="replace")
    version_match = re.search(r"(?im)^\s*\*\s*Version:\s*(\S+)\s*$", source)
    version = version_match.group(1) if version_match else ""
    if version != ARTIFACT_VERSION or f"Plugin Name: {PLUGIN_NAME}" not in source:
        raise DeploymentError("REFUSED: artifact plugin identity/version mismatch.")
    return {"path": str(artifact), "sha256": digest, "version": version,
            "members": list(members), "bytes": artifact.stat().st_size}


def row_fingerprint(row: dict[str, Any]) -> str:
    safe = {key: row[key] for key in ("present", "active", "version", "update_marker",
                                      "plugin_file")}
    return digest_for(safe)


def project_row(present: bool, active: bool | None, version: str,
                update_marker: bool = False) -> dict[str, Any]:
    row = {"present": bool(present), "active": active, "version": str(version),
           "update_marker": bool(update_marker), "plugin_file": PLUGIN_FILE}
    row["fingerprint"] = row_fingerprint(row)
    return row


class AdminPage:
    """Only the fixed plugin row and fixed upload/replace controls are reachable."""

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
        # A long-lived authenticated browser returned WordPress's "The link you
        # followed has expired" after submitting a cached upload form on
        # 2026-08-14. Force a fresh GET so WordPress supplies a current form
        # nonce; never read, copy, log, or expose that nonce ourselves.
        self._goto(f"{UPLOAD_URL}&_dado_refresh={secrets.token_hex(8)}")

    def _rows(self) -> list[Any]:
        assert_admin_url(self.url)
        rows = self._page.query_selector_all(ROW_SELECTOR)
        if len(rows) > 1:
            raise DeploymentError("REFUSED: fixed plugin row is ambiguous.")
        return rows

    def read_row(self, allow_absent: bool = False) -> dict[str, Any]:
        rows = self._rows()
        if not rows:
            if allow_absent:
                return project_row(False, None, "", False)
            raise DeploymentError("REFUSED: fixed plugin is not installed.")
        row = rows[0]
        tokens = set(str(row.get_attribute("class") or "").split())
        by_active = "active" in tokens
        by_inactive = "inactive" in tokens
        if by_active == by_inactive:
            raise DeploymentError("REFUSED: fixed plugin state class is ambiguous.")
        has_activate = row.query_selector(ACTIVATE_SELECTOR) is not None
        has_deactivate = row.query_selector(DEACTIVATE_SELECTOR) is not None
        if has_activate == has_deactivate or by_active != has_deactivate:
            raise DeploymentError("REFUSED: fixed plugin state and action disagree.")
        cell = row.query_selector(VERSION_SELECTOR)
        found = VERSION_PATTERN.search(str(cell.inner_text() or "")) if cell else None
        if not found:
            raise DeploymentError("REFUSED: fixed plugin version is unreadable.")
        update_rows = self._page.query_selector_all(UPDATE_ROW_SELECTOR)
        if len(update_rows) > 1:
            raise DeploymentError("REFUSED: fixed plugin update-marker row is ambiguous.")
        update = "update" in tokens or bool(update_rows)
        return project_row(True, by_active, found.group(1), update)

    def _click_state_action(self, selector: str, active_before: bool) -> dict[str, Any]:
        before = self.read_row()
        allowed_versions = (DEACTIVATABLE_VERSIONS if active_before
                            else frozenset({ARTIFACT_VERSION}))
        if before["active"] is not active_before or before["version"] not in allowed_versions:
            raise DeploymentError("REFUSED: fixed plugin is not in the required version/state.")
        link = self._rows()[0].query_selector(selector)
        if link is None:
            raise DeploymentError("REFUSED: fixed scoped row action is unavailable.")
        assert_admin_url(self.url)
        link.click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_STATE_TIMEOUT_MS)
        assert_admin_url(self.url)
        after = self.read_row()
        return after

    def activate(self) -> dict[str, Any]:
        after = self._click_state_action(ACTIVATE_SELECTOR, False)
        if after["active"] is not True:
            raise IndeterminateError("Activation did not read back active.")
        return after

    def deactivate(self) -> dict[str, Any]:
        after = self._click_state_action(DEACTIVATE_SELECTOR, True)
        if after["active"] is not False:
            raise IndeterminateError("Deactivation did not read back inactive.")
        return after

    @staticmethod
    def _comparison_cell(table: Any, label: str) -> str:
        wanted = re.compile(label, re.IGNORECASE)
        for row in table.query_selector_all("tr"):
            cells = row.query_selector_all("td")
            if len(cells) >= 2 and wanted.fullmatch(str(cells[0].inner_text() or "").strip()):
                return str(cells[-1].inner_text() or "").strip()
        return ""

    def _read_inactive_artifact_bounded(self) -> dict[str, Any]:
        """Allow only bounded fresh GET/read reconciliation after the single overwrite click."""
        for read_round in range(POST_WRITE_READ_ROUNDS):
            self.goto_plugins()
            observed = self.read_row()
            if observed["version"] == ARTIFACT_VERSION and observed["active"] is False:
                return observed
            if read_round + 1 < POST_WRITE_READ_ROUNDS:
                self._page.wait_for_timeout(POST_WRITE_READ_DELAY_MS)
        raise IndeterminateError(
            "Install/replace did not read back exact version inactive after bounded fresh reads.")

    def upload_install_or_replace(self, artifact: Path, existed_before: bool) -> dict[str, Any]:
        if artifact.resolve() != ARTIFACT_PATH.resolve():
            raise DeploymentError("REFUSED: upload is not the hardcoded artifact.")
        self.goto_upload()
        chooser = self._page.query_selector('input[type="file"][name="pluginzip"]')
        submit = self._page.query_selector("#install-plugin-submit")
        if chooser is None or submit is None:
            raise DeploymentError("REFUSED: fixed upload controls are unavailable.")
        chooser.set_input_files(str(artifact), timeout=ACTION_TIMEOUT_MS)
        assert_admin_url(self.url)
        submit.click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_STATE_TIMEOUT_MS)
        assert_admin_url(self.url)

        tables = self._page.query_selector_all("table.update-from-upload-comparison")
        overwrite = self._page.query_selector_all("a.update-from-upload-overwrite")
        route = "install"
        if existed_before:
            if len(tables) != 1 or len(overwrite) != 1:
                raise DeploymentError("REFUSED: exact replace-current confirmation is unavailable.")
            name = self._comparison_cell(tables[0], r"(plugin\s+)?name")
            version = self._comparison_cell(tables[0], r"version")
            if name != PLUGIN_NAME or version != ARTIFACT_VERSION:
                raise DeploymentError("REFUSED: replace comparison identity/version mismatch.")
            overwrite[0].click(timeout=ACTION_TIMEOUT_MS)
            self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_STATE_TIMEOUT_MS)
            assert_admin_url(self.url)
            route = "replace"
        elif tables or overwrite:
            raise DeploymentError("REFUSED: fresh install unexpectedly entered replace route.")

        after = self._read_inactive_artifact_bounded()
        return {"route": route, "after": after}


class PublicPage:
    """Anonymous public reader. It exposes no click and no write route."""

    def __init__(self, page: Any, request: Any):
        self._page = page
        self._request = request

    @property
    def url(self) -> str:
        return str(self._page.url)

    def goto(self, url: str) -> None:
        assert_public_url(url)
        self._page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_public_url(self.url)

    def _request_get(self, url: str, *, max_redirects: int = 20) -> dict[str, Any]:
        assert_public_url(url)
        options = {
            "headers": {"Accept": "application/json,text/html,application/pdf;q=0.9,*/*;q=0.1"},
            "timeout": NAV_TIMEOUT_MS,
            "fail_on_status_code": False,
        }
        if max_redirects == 0:
            options["max_redirects"] = 0
        response = self._request.get(url, **options)
        body = response.body()
        payload: Any = None
        try:
            payload = response.json()
        except Exception:
            pass
        return {"status": response.status, "headers": response.headers,
                "body": body, "payload": payload}

    def _health(self) -> dict[str, bool]:
        text = str(self._page.inner_text("body", timeout=ACTION_TIMEOUT_MS) or "")
        lowered = text.casefold()
        return {"blank": len(text.strip()) < MIN_RENDERED_TEXT,
                "fatal": any(marker in lowered for marker in FATAL_MARKERS)}

    def require_healthy(self, url: str) -> dict[str, bool]:
        self.goto(url)
        health = self._health()
        if health["blank"] or health["fatal"]:
            raise DeploymentError("Anonymous public page is blank or fatal.")
        return health

    @staticmethod
    def _class_id(element: Any, prefix: str) -> int | None:
        matches = [token[len(prefix):] for token in
                   str(element.get_attribute("class") or "").split()
                   if token.startswith(prefix) and token[len(prefix):].isdigit()]
        return int(matches[0]) if len(matches) == 1 else None

    def _menu_projection(self, selector: str) -> dict[str, Any]:
        roots = self._page.query_selector_all(selector)
        if len(roots) != 1:
            return {"present": False, "shop_roots": [], "categories": [], "products": [],
                    "grouping": [], "order_bytes": "", "deduplicated": False,
                    "fully_grouped": False, "nonempty_groups": False}
        root = roots[0]
        shop_items = root.query_selector_all("li > a")
        shop_roots = []
        for link in shop_items:
            href = str(link.get_attribute("href") or "")
            if (urlsplit(href).path or "") == SHOP_PATH:
                shop_roots.append({"title": str(link.text_content() or "").strip(),
                                   "path": SHOP_PATH})
        category_items = root.query_selector_all(".frpdepot-acp-category-item")
        product_items = root.query_selector_all(".frpdepot-acp-product-item")
        categories = [self._class_id(item, "frpdepot-acp-category-") for item in category_items]
        products = [self._class_id(item, "frpdepot-acp-product-") for item in product_items]
        product_parents = [self._class_id(item, "frpdepot-acp-category-parent-")
                           for item in product_items]
        valid = None not in categories and None not in products and None not in product_parents
        grouping = []
        if valid:
            grouping = [[category_id, [product_id for product_id, parent_id
                                       in zip(products, product_parents)
                                       if parent_id == category_id]]
                        for category_id in categories]
        grouped_products = [product_id for _, group_products in grouping
                            for product_id in group_products]
        order_bytes = canonical({"categories": categories, "products": products,
                                 "grouping": grouping}).encode("utf-8").hex()
        return {"present": True, "shop_roots": shop_roots,
                "categories": categories, "products": products, "grouping": grouping,
                "order_bytes": order_bytes,
                "deduplicated": valid and len(products) == len(set(products))
                and len(categories) == len(set(categories)),
                "fully_grouped": valid and grouped_products == products,
                "nonempty_groups": valid and all(group_products
                                                   for _, group_products in grouping)}

    @staticmethod
    def _matches_catalogue_projection(projection: dict[str, Any],
                                      reference: dict[str, Any]) -> bool:
        """Require known live entries and byte-identical ordered grouping on every surface."""
        return bool(
            projection["present"] and projection["deduplicated"]
            and projection["fully_grouped"] and projection["nonempty_groups"]
            and EXPECTED_CATEGORY_IDS.issubset(projection["categories"])
            and EXPECTED_PRODUCT_IDS.issubset(projection["products"])
            and projection["order_bytes"] == reference["order_bytes"]
            and projection["categories"] == reference["categories"]
            and projection["products"] == reference["products"]
            and projection["grouping"] == reference["grouping"]
        )

    def _guide_findings(self, product_id: int, *, transformed: bool) -> dict[str, Any]:
        url = PRODUCT_URLS[product_id]
        section_key = PRODUCT_SECTION_KEYS[product_id]
        section_url = SECTION_PDF_URLS[section_key]
        self.require_healthy(url)
        html = str(self._page.content() or "")
        counts = {
            "hetron_text_count": html.casefold().count("hetron"),
            "hetron_url_count": html.count(HETRON_URL),
            "hetron_card_count": len(self._page.query_selector_all(
                ".et_pb_blurb_1_tb_body h4.et_pb_module_header")),
            "derakane_old_url_count": html.count(DERAKANE_OLD_URL),
            "derakane_new_card_url_count": html.count(DERAKANE_NEW_URL),
            "derakane_card_count": len(self._page.query_selector_all(
                ".et_pb_blurb_0_tb_body h4.et_pb_module_header")),
            "inline_cta_count": len(self._page.query_selector_all(
                f'.et_pb_text_1_tb_body a[href="{INLINE_CTA_PATH}"]')),
            "full_catalogue_url_count": html.count(FULL_CATALOGUE_URL),
            "section_catalogue_url_count": html.count(section_url),
            "section_catalogue_card_count": len(self._page.query_selector_all(
                ".et_pb_blurb_2_tb_body h4.et_pb_module_header")),
        }
        expected = ({"hetron_text_count": 0, "hetron_url_count": 0, "hetron_card_count": 0,
                     "derakane_old_url_count": 0, "derakane_new_card_url_count": 1,
                     "derakane_card_count": 1, "inline_cta_count": 1,
                     "full_catalogue_url_count": 0, "section_catalogue_url_count": 1,
                     "section_catalogue_card_count": 1}
                    if transformed else
                    {"hetron_text_count": 1, "hetron_url_count": 0, "hetron_card_count": 1,
                     "derakane_old_url_count": 1, "derakane_new_card_url_count": 0,
                     "derakane_card_count": 1, "inline_cta_count": 1,
                     "full_catalogue_url_count": 1, "section_catalogue_url_count": 0,
                     "section_catalogue_card_count": 1})
        return {"product_id": product_id, "url": url, "section_key": section_key,
                "section_url": section_url, **counts, "passed": counts == expected}

    def _section_pdf_findings(self) -> dict[str, Any]:
        findings: dict[str, Any] = {}
        for key, expected in SECTION_PDFS.items():
            url = SECTION_PDF_URLS[key]
            result = self._request_get(url)
            body = result["body"]
            content_type = str(result["headers"].get("content-type") or "").casefold()
            row = {
                "url": url,
                "status": result["status"],
                "content_type": content_type,
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "pdf_signature": body.startswith(b"%PDF-"),
            }
            row["passed"] = bool(
                row["status"] == 200 and "application/pdf" in content_type
                and row["bytes"] == expected["bytes"]
                and row["sha256"] == expected["sha256"]
                and row["pdf_signature"]
            )
            findings[key] = row
        return {"passed": all(row["passed"] for row in findings.values()),
                "files": findings}

    def _category_catalogue_findings(self, category_id: int,
                                     *, transformed: bool) -> dict[str, Any]:
        url = CATEGORY_URLS[category_id]
        health = self.require_healthy(url)
        html = str(self._page.content() or "")
        panels = self._page.query_selector_all(".frpdepot-acp-section-catalogues")
        links = self._page.query_selector_all(".frpdepot-acp-section-catalogue-link")
        hrefs = [str(link.get_attribute("href") or "") for link in links]
        targets = [str(link.get_attribute("target") or "") for link in links]
        rels = [str(link.get_attribute("rel") or "").split() for link in links]
        expected_hrefs = [SECTION_PDF_URLS[key] for key in CATEGORY_SECTION_KEYS[category_id]]
        if transformed:
            passed = bool(len(panels) == 1 and hrefs == expected_hrefs
                          and targets == ["_blank"] * len(expected_hrefs)
                          and all("noopener" in rel for rel in rels)
                          and html.count(FULL_CATALOGUE_URL) == 0)
        else:
            passed = bool(len(panels) == 0 and not hrefs)
        return {"passed": passed, "category_id": category_id, "url": url,
                "health": health, "transformed": transformed,
                "panel_count": len(panels), "hrefs": hrefs,
                "expected_hrefs": expected_hrefs if transformed else [],
                "full_catalogue_url_count": html.count(FULL_CATALOGUE_URL)}

    def _fnpt_findings(self) -> dict[str, Any]:
        result = self._request_get(FNPT_STORE_API_URL)
        payload = result["payload"] if isinstance(result["payload"], dict) else {}
        categories = payload.get("categories") if isinstance(payload.get("categories"), list) else []
        category_ids = [row.get("id") for row in categories if isinstance(row, dict)]
        attributes = payload.get("attributes") if isinstance(payload.get("attributes"), list) else []
        resin = [row for row in attributes if isinstance(row, dict)
                 and row.get("name") == "RESIN TYPE"]
        resin_options = []
        if len(resin) == 1 and isinstance(resin[0].get("terms"), list):
            resin_options = [row.get("name") for row in resin[0]["terms"] if isinstance(row, dict)]
        passed = bool(result["status"] == 200 and payload.get("id") == FNPT_PRODUCT_ID
                      and category_ids == [FNPT_TARGET_CATEGORY_ID]
                      and REMOVED_RESIN_OPTION not in resin_options)
        return {"passed": passed, "status": result["status"], "product_id": payload.get("id"),
                "category_ids": category_ids, "resin_options": resin_options,
                "variation_writes": 0}

    def _derakane_v2_findings(self) -> dict[str, Any]:
        self.require_healthy(DERAKANE_PAGE_URL)
        page = {
            "root_count": len(self._page.query_selector_all("section[data-derakane-search]")),
            "heading_count": len(self._page.query_selector_all(
                "section[data-derakane-search] h1")),
        }
        result = self._request_get(DERAKANE_REST_URL + "?chemical=hydrochloric%20acid")
        payload = result["payload"] if isinstance(result["payload"], dict) else {}
        api = {"status": result["status"], "total": payload.get("total"),
               "groups_nonempty": bool(payload.get("groups"))}
        passed = bool(page == {"root_count": 1, "heading_count": 1}
                      and api["status"] == 200 and isinstance(api["total"], int)
                      and api["total"] > 0 and api["groups_nonempty"])
        return {"passed": passed, "page": page, "api": api}

    def _hetron_unavailable_findings(self) -> dict[str, Any]:
        urls = {
            "attachment": HETRON_ATTACHMENT_URL,
            "attachment_query": HETRON_ATTACHMENT_QUERY_URL,
            "direct_pdf": HETRON_DIRECT_PDF_URL,
        }
        routes = {}
        for label, url in urls.items():
            result = self._request_get(url, max_redirects=0)
            routes[label] = {"status": result["status"],
                             "location": result["headers"].get("location")}
        passed = all(item["status"] in {404, 410} and not item["location"]
                     for item in routes.values())
        return {"passed": passed, "routes": routes}

    def activation_prerequisite_findings(self) -> dict[str, Any]:
        source_guides = {str(product_id): self._guide_findings(product_id, transformed=False)
                         for product_id in sorted(PRODUCT_URLS)}
        source_categories = {
            str(category_id): self._category_catalogue_findings(
                category_id, transformed=False)
            for category_id in sorted(CATEGORY_URLS)
        }
        section_pdfs = self._section_pdf_findings()
        self.require_healthy(HOME_URL)
        main = self._menu_projection(MAIN_MENU_SELECTOR)
        products_roots = [root for root in main["shop_roots"] if root["path"] == SHOP_PATH]
        findings = {
            "source_guides": source_guides,
            "source_categories": source_categories,
            "section_pdfs": section_pdfs,
            "fnpt": self._fnpt_findings(),
            "derakane_v2": self._derakane_v2_findings(),
            "hetron_unavailable": self._hetron_unavailable_findings(),
            "products_root": {"count": len(products_roots), "path": SHOP_PATH},
        }
        findings["passed"] = bool(
            all(item["passed"] for item in source_guides.values())
            and all(item["passed"] for item in source_categories.values())
            and section_pdfs["passed"]
            and findings["fnpt"]["passed"] and findings["derakane_v2"]["passed"]
            and findings["hetron_unavailable"]["passed"] and len(products_roots) == 1
        )
        findings["fingerprint"] = digest_for(findings)
        return findings

    def catalogue_findings(self) -> dict[str, Any]:
        self.require_healthy(HOME_URL)
        projections = {
            "desktop": self._menu_projection(MAIN_MENU_SELECTOR),
            "footer": self._menu_projection(FOOTER_MENU_SELECTOR),
            "mobile": self._menu_projection(HEADER_MOBILE_SELECTOR),
            "footer_mobile": self._menu_projection(FOOTER_MOBILE_SELECTOR),
        }
        desktop = projections["desktop"]
        for projection in projections.values():
            projection["matches_expected"] = self._matches_catalogue_projection(
                projection, desktop)
        root = {
            "desktop": desktop["shop_roots"],
            "mobile": projections["mobile"]["shop_roots"],
        }
        root["passed"] = all(value == [{"title": SHOP_TITLE, "path": SHOP_PATH}]
                                    for value in root.values())
        guides = {str(product_id): self._guide_findings(product_id, transformed=True)
                  for product_id in sorted(PRODUCT_URLS)}
        category_pages = {
            str(category_id): self._category_catalogue_findings(
                category_id, transformed=True)
            for category_id in sorted(CATEGORY_URLS)
        }
        section_pdfs = self._section_pdf_findings()
        fnpt = self._fnpt_findings()
        derakane_v2 = self._derakane_v2_findings()
        hetron_unavailable = self._hetron_unavailable_findings()
        passed = bool(all(item["matches_expected"] for item in projections.values())
                      and root["passed"] and all(item["passed"] for item in guides.values())
                      and all(item["passed"] for item in category_pages.values())
                      and section_pdfs["passed"]
                      and fnpt["passed"] and derakane_v2["passed"]
                      and hetron_unavailable["passed"])
        return {"passed": passed, "projections": projections, "shop_root": root,
                "guides": guides, "category_pages": category_pages,
                "section_pdfs": section_pdfs,
                "fnpt": fnpt, "derakane_v2": derakane_v2,
                "hetron_unavailable": hetron_unavailable}


@contextlib.contextmanager
def admin_session() -> Iterator[AdminPage]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    ensure_runtime_temp_ready()
    with ui_browser_lock("wordpress", purpose="catalogue plugin admin session"), sync_playwright() as pw:
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

    ensure_runtime_temp_ready()
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


def plan_lock_path(plan_path: Path) -> Path:
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


PLAN_KEYS = frozenset({
    "schema_version", "tool", "tool_version", "origin", "action", "created_utc",
    "expires_utc", "nonce", "plugin_name", "plugin_slug", "plugin_file", "artifact",
    "before", "after_expected", "validation", "activation_prerequisites",
})
ROW_KEYS = frozenset({"present", "active", "version", "update_marker", "plugin_file",
                      "fingerprint"})


def expected_after(action: str, before: dict[str, Any]) -> dict[str, Any]:
    if action == "plugin_activate":
        return project_row(True, True, ARTIFACT_VERSION, bool(before["update_marker"]))
    if action == "plugin_deactivate":
        return project_row(True, False, str(before["version"]), bool(before["update_marker"]))
    return project_row(True, False, ARTIFACT_VERSION, False)


def write_plan(action: str, before: dict[str, Any], artifact: dict[str, Any] | None,
               activation_prerequisites: dict[str, Any] | None = None) -> Path:
    if action not in ACTIONS:
        raise DeploymentError("Unsupported action.")
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION, "tool": TOOL_NAME, "tool_version": TOOL_VERSION,
        "origin": EXACT_ORIGIN, "action": action, "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16), "plugin_name": PLUGIN_NAME,
        "plugin_slug": PLUGIN_SLUG, "plugin_file": PLUGIN_FILE, "artifact": artifact,
        "before": before, "after_expected": expected_after(action, before),
        "validation": dict(VALIDATION_CONTRACT) if action == "plugin_activate" else None,
        "activation_prerequisites": activation_prerequisites
        if action == "plugin_activate" else None,
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{action}_{digest[:16]}.json"
    _exclusive_json(path, plan, stat.S_IREAD)
    append_receipt("catalogue_presentation_plugin_plan_staged", str(path))
    return path


def resolve_plan_path(raw: str) -> Path:
    path = Path(raw).resolve()
    if PLAN_DIR.resolve() not in path.parents:
        raise DeploymentError("Plan must be inside the fixed catalogue plan directory.")
    return path


def load_plan(raw: str) -> dict[str, Any]:
    path = resolve_plan_path(raw)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError("Plan is unreadable.") from exc
    if not isinstance(stored, dict):
        raise DeploymentError("Plan must be one object.")
    saved = str(stored.pop("sha256", ""))
    if saved in SUPERSEDED_PLAN_SHA256:
        raise DeploymentError("REFUSED: superseded catalogue deployment plan hash.")
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
            or expires != created + timedelta(hours=PLAN_LIFETIME_HOURS)):
        raise DeploymentError("Plan is not an exact 24-hour plan.")
    now = utc_now()
    if created > now + timedelta(minutes=PLAN_CLOCK_SKEW_MINUTES):
        raise DeploymentError("Plan creation time is in the future.")
    if now >= expires:
        raise DeploymentError("Plan expired; stage a new one.")
    if not re.fullmatch(r"[0-9a-f]{32}", str(stored["nonce"])):
        raise DeploymentError("Plan nonce is invalid.")
    for label in ("before", "after_expected"):
        row = stored[label]
        if not isinstance(row, dict) or set(row) != ROW_KEYS or row["plugin_file"] != PLUGIN_FILE:
            raise DeploymentError(f"Plan {label} projection is invalid.")
        if row_fingerprint(row) != row["fingerprint"]:
            raise DeploymentError(f"Plan {label} fingerprint is invalid.")
    if stored["after_expected"] != expected_after(stored["action"], stored["before"]):
        raise DeploymentError("Plan expected state is invalid.")
    artifact = stored["artifact"]
    if stored["action"] == "plugin_install_or_replace":
        if not isinstance(artifact, dict) or set(artifact) != {
                "path", "sha256", "version", "members", "bytes"}:
            raise DeploymentError("Install/replace plan artifact record is invalid.")
        if (Path(artifact["path"]).resolve() != ARTIFACT_PATH.resolve()
                or artifact["sha256"] != ARTIFACT_SHA256
                or artifact["version"] != ARTIFACT_VERSION
                or artifact["bytes"] != ARTIFACT_BYTES
                or tuple(artifact["members"]) != tuple(sorted(ARTIFACT_MEMBERS))):
            raise DeploymentError("Install/replace plan does not name the hardcoded artifact.")
    elif artifact is not None:
        raise DeploymentError("Only install/replace may carry an artifact.")
    expected_validation = VALIDATION_CONTRACT if stored["action"] == "plugin_activate" else None
    if stored["validation"] != expected_validation:
        raise DeploymentError("Plan validation contract is invalid.")
    prerequisites = stored["activation_prerequisites"]
    if stored["action"] == "plugin_activate":
        if (not isinstance(prerequisites, dict) or prerequisites.get("passed") is not True
                or not isinstance(prerequisites.get("fingerprint"), str)):
            raise DeploymentError("Plan activation prerequisite evidence is invalid.")
        core = dict(prerequisites)
        fingerprint = core.pop("fingerprint")
        if not secrets.compare_digest(fingerprint, digest_for(core)):
            raise DeploymentError("Plan activation prerequisite fingerprint is invalid.")
    elif prerequisites is not None:
        raise DeploymentError("Only activation may carry prerequisite evidence.")
    stored["sha256"] = saved
    return stored


def write_attempt_lock(path: Path, plan: dict[str, Any], stage: str) -> None:
    _exclusive_json(path, {"plan_sha256": plan["sha256"], "status": "in_flight",
                           "started_utc": utc_now().isoformat(), "stage": stage,
                           "attempts_allowed": 1, "retry": False}, stat.S_IREAD | stat.S_IWRITE)


def update_attempt_lock(path: Path, payload: dict[str, Any]) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IREAD)


def record_result(plan_path: Path, plan: dict[str, Any], status_text: str,
                  detail: dict[str, Any]) -> None:
    payload = {"status": status_text, "action": plan["action"], "plan": str(plan_path),
               "plan_sha256": plan["sha256"], "plugin_file": PLUGIN_FILE,
               "recorded_utc": utc_now().isoformat(), "retry": False, **detail}
    _exclusive_json(result_path(plan_path), payload, stat.S_IREAD)
    append_receipt("catalogue_presentation_plugin_" + status_text.casefold(),
                   f"action={plan['action']}; sha256={plan['sha256']}")


def _live_row(allow_absent: bool = False) -> dict[str, Any]:
    with admin_session() as admin:
        admin.goto_plugins()
        return admin.read_row(allow_absent=allow_absent)


def _stage_report(action: str, before: dict[str, Any], artifact: dict[str, Any] | None,
                  activation_prerequisites: dict[str, Any] | None = None) -> None:
    path = write_plan(action, before, artifact, activation_prerequisites)
    plan = json.loads(path.read_text(encoding="utf-8"))
    emit({"status": "STAGED_NOT_COMMITTED", "plan": str(path),
          "plan_sha256": plan["sha256"], "expires_utc": plan["expires_utc"],
          "action": action, "plugin_file": PLUGIN_FILE, "before": before,
          "after_expected": plan["after_expected"], "validation": plan["validation"],
          "activation_prerequisites": plan["activation_prerequisites"],
          "approval": APPROVAL_WORD, "external_write_performed": False})


def command_stage_install_or_replace(_: argparse.Namespace) -> None:
    artifact = verify_artifact()
    before = _live_row(allow_absent=True)
    if before["present"] and before["active"] is not False:
        raise DeploymentError("REFUSED: install/replace requires absent or inactive plugin.")
    if before["present"] and before["version"] == ARTIFACT_VERSION:
        raise DeploymentError("No change needed: exact artifact version already installed inactive.")
    _stage_report("plugin_install_or_replace", before, artifact)


def command_stage_activate(_: argparse.Namespace) -> None:
    before = _live_row()
    if before["version"] != ARTIFACT_VERSION or before["active"] is not False:
        raise DeploymentError("REFUSED: activation requires exact artifact version inactive.")
    with anonymous_session() as public:
        prerequisites = public.activation_prerequisite_findings()
    if not prerequisites["passed"]:
        raise DeploymentError(
            "REFUSED: activation prerequisite live conditions are incomplete; nothing staged.")
    _stage_report("plugin_activate", before, None, prerequisites)


def command_stage_deactivate(_: argparse.Namespace) -> None:
    before = _live_row()
    if before["version"] not in DEACTIVATABLE_VERSIONS or before["active"] is not True:
        raise DeploymentError(
            "REFUSED: deactivation requires the exact current or predecessor artifact active.")
    _stage_report("plugin_deactivate", before, None)


def _open_commit(args: argparse.Namespace, action: str) -> tuple[Path, dict[str, Any]]:
    path = resolve_plan_path(args.plan)
    plan = load_plan(str(path))
    if plan["action"] != action:
        raise DeploymentError("Plan action does not match command.")
    require_approval(args.approval)
    if plan_lock_path(path).exists() or result_path(path).exists():
        raise DeploymentError("Plan already entered its one allowed attempt; no retry.")
    return path, plan


def _verify_pre_state(admin: AdminPage, plan: dict[str, Any], allow_absent: bool = False) -> None:
    admin.goto_plugins()
    live = admin.read_row(allow_absent=allow_absent)
    if live["fingerprint"] != plan["before"]["fingerprint"]:
        raise DeploymentError("REFUSED: fixed plugin state changed after review.")


def _anonymous_health() -> dict[str, bool]:
    with anonymous_session() as public:
        return public.require_healthy(HOME_URL)


def _anonymous_catalogue_validation() -> dict[str, Any]:
    with anonymous_session() as public:
        findings = public.catalogue_findings()
    if not findings["passed"]:
        raise KnownValidationFailure(findings)
    return findings


def _close_attempt(lock: Path, plan: dict[str, Any], status_text: str,
                   detail: dict[str, Any]) -> None:
    update_attempt_lock(lock, {"plan_sha256": plan["sha256"], "status": status_text,
                               "updated_utc": utc_now().isoformat(), "retry": False, **detail})


def _failed_attempt(lock: Path, plan_path: Path, plan: dict[str, Any], stage: str,
                    exc: Exception, detail: dict[str, Any] | None = None) -> None:
    extra = detail or {}
    _close_attempt(lock, plan, "failed_closed", {"stage": stage,
                                                  "reason": type(exc).__name__, **extra})
    record_result(plan_path, plan, "FAILED_CLOSED", {"stage": stage,
                                                      "reason": type(exc).__name__, **extra})


def _indeterminate_attempt(lock: Path, plan_path: Path, plan: dict[str, Any], stage: str,
                           exc: Exception, detail: dict[str, Any] | None = None) -> None:
    """Permanently close one attempted write whose final contract is not proven."""
    extra = detail or {}
    _close_attempt(lock, plan, "indeterminate", {"stage": stage,
                                                 "reason": type(exc).__name__, **extra})
    record_result(plan_path, plan, "INDETERMINATE", {"stage": stage,
                                                      "reason": type(exc).__name__, **extra})


@holds_wordpress_browser("WordPress: install or replace catalogue presentation plugin inactive")
def command_commit_install_or_replace(args: argparse.Namespace) -> None:
    plan_path, plan = _open_commit(args, "plugin_install_or_replace")
    artifact = verify_artifact(Path(plan["artifact"]["path"]))
    if artifact["sha256"] != plan["artifact"]["sha256"]:
        raise DeploymentError("Artifact changed after plan review.")
    lock = plan_lock_path(plan_path)
    with admin_session() as admin:
        _verify_pre_state(admin, plan, allow_absent=True)
        write_attempt_lock(lock, plan, "install_or_replace")
        try:
            result = admin.upload_install_or_replace(Path(artifact["path"]),
                                                     bool(plan["before"]["present"]))
        except Exception as exc:
            _indeterminate_attempt(lock, plan_path, plan, "install_or_replace", exc)
            raise IndeterminateError("Install/replace unverified; plan closed with no retry.") from exc
    try:
        health = _anonymous_health()
    except Exception as exc:
        _indeterminate_attempt(lock, plan_path, plan, "anonymous_verification", exc)
        raise IndeterminateError("Anonymous verification failed; plan closed with no retry.") from exc
    after = result["after"]
    _close_attempt(lock, plan, "committed_verified", {"after": after, "route": result["route"]})
    record_result(plan_path, plan, "COMMITTED_AND_VERIFIED",
                  {"after": after, "route": result["route"], "anonymous_health": health,
                   "activated": False})
    emit({"status": "COMMITTED_AND_VERIFIED", "action": plan["action"],
          "route": result["route"], "active": False, "anonymous": True,
          "plan_sha256": plan["sha256"], "replay_locked": True})


def _emergency_deactivate_once() -> dict[str, Any]:
    """Read the fixed row and, if active, click its deactivation action at most once."""
    with admin_session() as admin:
        admin.goto_plugins()
        row = admin.read_row()
        if row["active"] is True:
            after = admin.deactivate()
            clicked = True
        elif row["active"] is False:
            after = row
            clicked = False
        else:
            raise IndeterminateError("Emergency deactivation state is unreadable.")
    rollback = {"after": after, "deactivate_clicked": clicked,
                "inactive_confirmed": after["active"] is False,
                "anonymous_health": None, "recovered": False}
    try:
        health = _anonymous_health()
        rollback["anonymous_health"] = health
        rollback["recovered"] = after["active"] is False
    except Exception as exc:
        rollback["health_error"] = type(exc).__name__
    return rollback


def _rollback_after_activation() -> dict[str, Any]:
    """Attempt one emergency deactivation and report confirmation without guessing."""
    try:
        return {"attempted": True, **_emergency_deactivate_once()}
    except Exception as exc:
        return {"attempted": True, "deactivate_clicked": False,
                "inactive_confirmed": False, "recovered": False,
                "reason": type(exc).__name__}


@holds_wordpress_browser("WordPress: activate catalogue presentation plugin")
def command_commit_activate(args: argparse.Namespace) -> None:
    plan_path, plan = _open_commit(args, "plugin_activate")
    # Activation eligibility is live, not merely historical plan metadata. Re-run the
    # complete source-guide/FNPT/Derakane/Hetron/root contract immediately before the
    # fixed plugin click, then re-read the plugin row before creating the attempt lock.
    with anonymous_session() as public:
        live_prerequisites = public.activation_prerequisite_findings()
    if (not live_prerequisites["passed"]
            or live_prerequisites["fingerprint"]
            != plan["activation_prerequisites"]["fingerprint"]):
        raise DeploymentError(
            "REFUSED: activation prerequisite live conditions changed or are incomplete.")
    lock = plan_lock_path(plan_path)
    activation_attempted = False
    activation_error: Exception | None = None
    after: dict[str, Any] = {}
    with admin_session() as admin:
        _verify_pre_state(admin, plan)
        write_attempt_lock(lock, plan, "activate")
        try:
            activation_attempted = True
            after = admin.activate()
        except Exception as exc:
            activation_error = exc
    if activation_error is not None:
        rollback = _rollback_after_activation() if activation_attempted else {
            "attempted": False, "deactivate_clicked": False,
            "inactive_confirmed": False, "recovered": False,
        }
        _indeterminate_attempt(lock, plan_path, plan, "activate", activation_error,
                               {"activation_confirmed": False, "rollback": rollback})
        raise IndeterminateError(
            "Activation unverified; emergency deactivation was attempted once; "
            "plan permanently closed with no retry."
        ) from activation_error
    try:
        findings = _anonymous_catalogue_validation()
    except KnownValidationFailure as exc:
        rollback = _rollback_after_activation()
        detail = {"activation_confirmed": True,
                  "emergency_deactivation_attempted": True,
                  "emergency_deactivated": rollback["inactive_confirmed"],
                  "validation": exc.findings, "rollback": rollback}
        if rollback["inactive_confirmed"]:
            _failed_attempt(lock, plan_path, plan, "anonymous_validation", exc, detail)
            raise DeploymentError("Known activation validation failure; plugin auto-deactivated "
                                  "once; plan permanently closed.") from exc
        _indeterminate_attempt(lock, plan_path, plan, "anonymous_validation", exc, detail)
        raise IndeterminateError("Known activation validation failure; emergency deactivation "
                                 "could not be confirmed; plan permanently closed.") from exc
    except Exception as exc:
        rollback = _rollback_after_activation()
        _indeterminate_attempt(lock, plan_path, plan, "anonymous_validation", exc,
                               {"activation_confirmed": True,
                                "emergency_deactivation_attempted": True,
                                "emergency_deactivated": rollback["inactive_confirmed"],
                                "rollback": rollback})
        raise IndeterminateError("Anonymous activation validation was indeterminate; emergency "
                                 "deactivation was attempted once; plan permanently closed.") from exc
    _close_attempt(lock, plan, "committed_verified", {"after": after,
                                                       "anonymous_validation": True})
    record_result(plan_path, plan, "COMMITTED_AND_VERIFIED",
                  {"after": after, "validation": findings,
                   "emergency_deactivated": False})
    emit({"status": "COMMITTED_AND_VERIFIED", "action": plan["action"], "active": True,
          "anonymous": True, "emergency_deactivated": False,
          "plan_sha256": plan["sha256"], "replay_locked": True})


@holds_wordpress_browser("WordPress: deactivate catalogue presentation plugin")
def command_commit_deactivate(args: argparse.Namespace) -> None:
    plan_path, plan = _open_commit(args, "plugin_deactivate")
    lock = plan_lock_path(plan_path)
    with admin_session() as admin:
        _verify_pre_state(admin, plan)
        write_attempt_lock(lock, plan, "deactivate")
        try:
            after = admin.deactivate()
        except Exception as exc:
            _indeterminate_attempt(lock, plan_path, plan, "deactivate", exc)
            raise IndeterminateError("Deactivation unverified; plan closed with no retry.") from exc
    try:
        health = _anonymous_health()
    except Exception as exc:
        _indeterminate_attempt(lock, plan_path, plan, "anonymous_verification", exc)
        raise IndeterminateError("Anonymous verification failed; plan closed with no retry.") from exc
    _close_attempt(lock, plan, "committed_verified", {"after": after})
    record_result(plan_path, plan, "COMMITTED_AND_VERIFIED",
                  {"after": after, "anonymous_health": health, "deleted": False})
    emit({"status": "COMMITTED_AND_VERIFIED", "action": plan["action"], "active": False,
          "still_installed": True, "deleted": False, "anonymous": True,
          "plan_sha256": plan["sha256"], "replay_locked": True})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler in (
        ("stage-install-or-replace", command_stage_install_or_replace),
        ("stage-activate", command_stage_activate),
        ("stage-deactivate", command_stage_deactivate),
    ):
        commands.add_parser(name).set_defaults(func=handler)
    for name, handler in (
        ("commit-install-or-replace", command_commit_install_or_replace),
        ("commit-activate", command_commit_activate),
        ("commit-deactivate", command_commit_deactivate),
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
