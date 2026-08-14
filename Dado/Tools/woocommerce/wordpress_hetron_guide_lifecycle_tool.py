#!/usr/bin/env python
"""Approval-gated retirement of one obsolete FRP Depot Hetron attachment route.

Commissioned by Rachad Homsi on 2026-08-13. Commissioning authorises BUILDING and
TESTING this named tool. It is not approval of the live change: ``commit`` still
requires Rachad's byte-exact, unpadded one-word ``APPROVED`` against one exact
24-hour staged plan.

The fixed live identity was established read-only on 2026-08-13:

* WordPress attachment ID 1832, slug ``hetron-cr-guide-2007_ineos``;
* status ``inherit``, no parent, no template, MIME ``application/pdf``;
* attachment route ``/hetron-cr-guide-2007_ineos/`` and query alias
  ``/?attachment_id=1832`` both return Yoast SEO 301 to one fixed PDF;
* the PDF and its four WordPress-generated preview JPEGs are separate upload
  files. They are historical assets and this tool preserves all five byte-for-byte.

There are exactly two commands. ``stage`` is read-only on WordPress and writes one
immutable local plan. ``commit`` makes exactly one authenticated same-origin
``POST /wp-json/wp/v2/media/1832`` with the literal payload
``{"status":"private"}``. The caller cannot provide an ID, slug, URL, status,
payload, field, selector or action. The only caller inputs to commit are the plan
path and approval word.

The lifecycle write preserves attachment 1832 and every linked file. It does not
make the nginx-served PDF secret: someone who already knows the exact uploads URL
can still fetch it. It makes both WordPress attachment routes unavailable to an
anonymous caller while retaining the object and files for private history. A
separate business decision and separately commissioned mechanism would be needed
if the historical upload bytes must also become non-public.

Commit holds the shared Windows WordPress mutex around the whole operation and
acquires it before the plan's exclusive attempt lock. The plan has one attempt and
no retry. After the one write, success requires a fresh authenticated full-object
readback showing status ``private``, both anonymous attachment routes returning
404/410 without redirect, and all five historical assets retaining their fixed
size and SHA-256. Any uncertainty permanently closes the plan as indeterminate.

This module has no delete/trash/file operation, no arbitrary content/page/media/
settings/menu/plugin/product route, no product/order/customer/payment capability,
no email, no shell, no credential store and no browser login. It only attaches to
the already-authenticated loopback WordPress browser on CDP 9229.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.append(str(Path(__file__).resolve().parent.parent / "common"))
from ui_lane_lock import UiLaneBusy, UiLaneLockError, ui_browser_lock

TOOL_NAME = "FRP Depot Hetron Guide Attachment Lifecycle Tool"
TOOL_VERSION = "1.0.1"
SCHEMA_VERSION = 2
ACTION = "attachment_1832_inherit_to_private"
COMMANDS = ("stage", "commit")

ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "wordpress_hetron_guide_lifecycle_plans"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
PLAN_LIFETIME_HOURS = 24
PLAN_CLOCK_SKEW_MINUTES = 5
APPROVAL_WORD = "APPROVED"

EXACT_ORIGIN = "https://frpdepots.com"
ALLOWED_HOST = "frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"

ATTACHMENT_ID = 1832
ATTACHMENT_TYPE = "attachment"
ATTACHMENT_SLUG = "hetron-cr-guide-2007_ineos"
BEFORE_STATUS = "inherit"
AFTER_STATUS = "private"
ATTACHMENT_TITLE = "HETRON CR Guide 2007_Ineos"
ATTACHMENT_MIME = "application/pdf"
ATTACHMENT_PARENT = None
ATTACHMENT_TEMPLATE = ""
ATTACHMENT_FILENAME = "HETRON-CR-Guide-2007_Ineos.pdf"
ATTACHMENT_FILESIZE = 5_740_139
ATTACHMENT_DATE_GMT = "2026-03-17T15:20:38"

PUBLIC_GUIDE_URL = f"{EXACT_ORIGIN}/hetron-cr-guide-2007_ineos/"
PUBLIC_QUERY_URL = f"{EXACT_ORIGIN}/?attachment_id={ATTACHMENT_ID}"
PDF_URL = (
    f"{EXACT_ORIGIN}/wp-content/uploads/2026/03/"
    "HETRON-CR-Guide-2007_Ineos.pdf"
)
ADMIN_EDIT_URL = f"{EXACT_ORIGIN}/wp-admin/post.php?post={ATTACHMENT_ID}&action=edit"
REST_URL = f"{EXACT_ORIGIN}/wp-json/wp/v2/media/{ATTACHMENT_ID}"
REST_READ_URL = REST_URL + "?context=edit"
REST_NONCE_URL = f"{EXACT_ORIGIN}/wp-admin/admin-ajax.php?action=rest-nonce"

ALLOWED_ADMIN_URLS = frozenset({ADMIN_EDIT_URL})
ALLOWED_PUBLIC_URLS = frozenset({PUBLIC_GUIDE_URL, PUBLIC_QUERY_URL, PDF_URL})
ALLOWED_UNAVAILABLE_STATUSES = frozenset({404, 410})

# These are the historical bytes attached to object 1832. They remain in place.
FIXED_ASSETS: tuple[dict[str, Any], ...] = (
    {
        "role": "original_pdf",
        "url": PDF_URL,
        "content_type": "application/pdf",
        "bytes": 5_740_139,
        "sha256": "b9993ac63eeeb4994c17dd34a79d6db8e154d3ae65de1d6a98b188fb766986c5",
    },
    {
        "role": "pdf_preview_full",
        "url": (
            f"{EXACT_ORIGIN}/wp-content/uploads/2026/03/"
            "HETRON-CR-Guide-2007_Ineos-pdf.jpg"
        ),
        "content_type": "image/jpeg",
        "bytes": 118_876,
        "sha256": "c5a8cc7d2a188e6ecc6b85c52ca815c9c461234fe73602ba810f6bc817b949d2",
    },
    {
        "role": "pdf_preview_medium",
        "url": (
            f"{EXACT_ORIGIN}/wp-content/uploads/2026/03/"
            "HETRON-CR-Guide-2007_Ineos-pdf-229x300.jpg"
        ),
        "content_type": "image/jpeg",
        "bytes": 10_781,
        "sha256": "5c15f53bc6559882c0718ee4c670a40d769b1fd37d5bdf40363d7ca6a072c973",
    },
    {
        "role": "pdf_preview_large",
        "url": (
            f"{EXACT_ORIGIN}/wp-content/uploads/2026/03/"
            "HETRON-CR-Guide-2007_Ineos-pdf-783x1024.jpg"
        ),
        "content_type": "image/jpeg",
        "bytes": 67_358,
        "sha256": "5ba6213b9a7e81a9e8d000516c210af115f9f3b9b27178aeffc9989bfcb08e55",
    },
    {
        "role": "pdf_preview_thumbnail",
        "url": (
            f"{EXACT_ORIGIN}/wp-content/uploads/2026/03/"
            "HETRON-CR-Guide-2007_Ineos-pdf-115x150.jpg"
        ),
        "content_type": "image/jpeg",
        "bytes": 5_927,
        "sha256": "7d64983d04041f9f3a069077ec5f8115110ca77305e1b000677b069a06f68ced",
    },
)
ALLOWED_PUBLIC_URLS = frozenset({PUBLIC_GUIDE_URL, PUBLIC_QUERY_URL,
                                 *(asset["url"] for asset in FIXED_ASSETS)})
MAX_ASSET_BYTES = 8_000_000
MAX_ROUTE_BODY_BYTES = 64_000
PUBLIC_TIMEOUT_SECONDS = 60
PUBLIC_USER_AGENT = "FRPDepot-Dado-Hetron-Lifecycle/1.0"

NAV_TIMEOUT_MS = 45_000
ACTION_TIMEOUT_MS = 15_000
REST_TIMEOUT_MS = 45_000
LOGIN_FORM_SELECTOR = "form#loginform"

# Full edit-context objects currently have exactly these top-level keys. The plan
# stores the complete object and its full hash, rather than a convenient subset.
OBJECT_KEYS = frozenset({
    "id", "date", "date_gmt", "guid", "modified", "modified_gmt", "slug",
    "status", "type", "link", "title", "author", "featured_media",
    "comment_status", "ping_status", "template", "meta", "permalink_template",
    "generated_slug", "class_list", "description", "caption", "alt_text",
    "media_type", "mime_type", "media_details", "post", "source_url",
    "missing_image_sizes", "filename", "filesize", "_links",
})
# WordPress legitimately changes these lifecycle/rendering fields when status is
# changed. Every other field in the complete staged object is hash-protected.
LIFECYCLE_FIELDS = frozenset({
    "status", "modified", "modified_gmt", "class_list", "link", "_links",
})
PLAN_KEYS = frozenset({
    "schema_version", "tool", "tool_version", "origin", "action", "created_utc",
    "expires_utc", "nonce", "object_id", "rest_route", "write_payload",
    "before", "public_before", "historical_assets", "after_expected", "risk",
})
BEFORE_KEYS = frozenset({"object", "full_sha256", "protected_sha256"})
PUBLIC_BEFORE_KEYS = frozenset({
    "guide_status", "guide_location", "query_status", "query_location",
    "redirect_by",
})
AFTER_KEYS = frozenset({
    "status", "anonymous_guide_status", "anonymous_query_status",
    "redirect_allowed", "authenticated_readback", "historical_assets_preserved",
    "object_deleted", "files_deleted", "retry",
})
ASSET_KEYS = frozenset({"role", "url", "content_type", "bytes", "sha256"})
WRITE_PAYLOAD = {"status": AFTER_STATUS}
AFTER_EXPECTED = {
    "status": AFTER_STATUS,
    "anonymous_guide_status": sorted(ALLOWED_UNAVAILABLE_STATUSES),
    "anonymous_query_status": sorted(ALLOWED_UNAVAILABLE_STATUSES),
    "redirect_allowed": False,
    "authenticated_readback": True,
    "historical_assets_preserved": True,
    "object_deleted": False,
    "files_deleted": False,
    "retry": False,
}
RISK = (
    "This one write is not rolled back or retried. It changes attachment 1832 from "
    "inherit to private, preserving the attachment and all five historical files. "
    "Both WordPress attachment routes must become anonymously unavailable. The "
    "direct uploads PDF remains publicly fetchable to someone who already knows "
    "its exact URL; hiding those bytes requires a separate business decision and "
    "separately commissioned mechanism."
)


class LifecycleError(RuntimeError):
    """A clean refusal whose message carries no page content or credential."""


class IndeterminateError(LifecycleError):
    """The fixed write was attempted but the complete result was not proven."""


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
    record = {"ts": utc_now().isoformat(), "action": action, "evidence": evidence}
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def holds_wordpress_browser(purpose: str):
    """Take the shared browser mutex before any per-plan attempt lock exists."""
    def decorate(function):
        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any):
            with ui_browser_lock("wordpress", purpose=purpose):
                return function(*args, **kwargs)
        return wrapper
    return decorate


def require_approval(value: Any) -> None:
    if not isinstance(value, str) or value != APPROVAL_WORD:
        raise LifecycleError(
            "Rachad must reply to this exact staged plan with the byte-exact, "
            "unpadded one-word approval APPROVED. Commissioning is not approval."
        )


def assert_exact_origin(url: str) -> None:
    parsed = urlsplit(str(url or ""))
    try:
        port = parsed.port
    except ValueError as exc:
        raise LifecycleError("REFUSED: invalid URL port.") from exc
    if (parsed.scheme != "https" or (parsed.hostname or "").casefold() != ALLOWED_HOST
            or port not in (None, 443) or parsed.username or parsed.password):
        raise LifecycleError(f"REFUSED: URL is outside exact origin {EXACT_ORIGIN}.")


def assert_admin_url(url: str) -> None:
    assert_exact_origin(url)
    if str(url) not in ALLOWED_ADMIN_URLS:
        raise LifecycleError("REFUSED: browser left the one fixed attachment edit URL.")


def assert_public_url(url: str) -> None:
    assert_exact_origin(url)
    if str(url) not in ALLOWED_PUBLIC_URLS:
        raise LifecycleError("REFUSED: public URL is outside the fixed attachment/assets set.")


def protected_projection(obj: dict[str, Any]) -> dict[str, Any]:
    return {key: obj[key] for key in sorted(set(obj) - LIFECYCLE_FIELDS)}


def _require_int(value: Any, expected: int, label: str) -> None:
    if type(value) is not int or value != expected:
        raise LifecycleError(f"REFUSED: fixed attachment {label} changed.")


def assert_live_object(obj: Any, *, expected_status: str) -> dict[str, Any]:
    """Validate the full authenticated edit-context shape and fixed identity."""
    if not isinstance(obj, dict) or set(obj) != OBJECT_KEYS:
        raise LifecycleError("REFUSED: fixed attachment full-object schema changed.")
    if len(canonical(obj).encode("utf-8")) > 1_000_000:
        raise LifecycleError("REFUSED: fixed attachment object exceeded the bounded plan size.")
    _require_int(obj.get("id"), ATTACHMENT_ID, "ID")
    _require_int(obj.get("filesize"), ATTACHMENT_FILESIZE, "file size")
    if (obj.get("type") != ATTACHMENT_TYPE or obj.get("slug") != ATTACHMENT_SLUG
            or obj.get("status") != expected_status or obj.get("template") != ATTACHMENT_TEMPLATE
            or obj.get("post") is not ATTACHMENT_PARENT or obj.get("mime_type") != ATTACHMENT_MIME
            or obj.get("filename") != ATTACHMENT_FILENAME or obj.get("source_url") != PDF_URL
            or obj.get("date_gmt") != ATTACHMENT_DATE_GMT
            or obj.get("generated_slug") != ATTACHMENT_SLUG):
        raise LifecycleError("REFUSED: fixed attachment identity/lifecycle fields changed.")
    guid = obj.get("guid")
    title = obj.get("title")
    if (not isinstance(guid, dict) or guid.get("raw") != PDF_URL or guid.get("rendered") != PDF_URL
            or not isinstance(title, dict) or title.get("raw") != ATTACHMENT_TITLE
            or title.get("rendered") != ATTACHMENT_TITLE):
        raise LifecycleError("REFUSED: fixed attachment GUID/title identity changed.")
    permalink = obj.get("permalink_template")
    if permalink != PUBLIC_QUERY_URL:
        raise LifecycleError("REFUSED: fixed attachment permalink template changed.")
    meta = obj.get("media_details")
    if not isinstance(meta, dict) or meta.get("filesize") != ATTACHMENT_FILESIZE:
        raise LifecycleError("REFUSED: fixed attachment media details changed.")
    classes = obj.get("class_list")
    if not isinstance(classes, list) or f"post-{ATTACHMENT_ID}" not in classes \
            or ATTACHMENT_TYPE not in classes or f"status-{expected_status}" not in classes:
        raise LifecycleError("REFUSED: fixed attachment class identity is inconsistent.")
    return obj


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _public_request(url: str, *, max_bytes: int) -> dict[str, Any]:
    assert_public_url(url)
    request = Request(url, method="GET", headers={"User-Agent": PUBLIC_USER_AGENT})
    opener = build_opener(NoRedirectHandler())
    try:
        response = opener.open(request, timeout=PUBLIC_TIMEOUT_SECONDS)
    except HTTPError as exc:
        response = exc
    except (OSError, URLError) as exc:
        raise LifecycleError("Public verification transport failed.") from exc
    try:
        status_code = int(getattr(response, "status", response.getcode()))
        headers = response.headers
        # WordPress/Divi may render a large branded body for a legitimate 404.
        # Unavailability is proven by status + no Location header; those error
        # bytes are neither hashed nor otherwise trusted.  Do not let an
        # irrelevant oversized 404 body turn a landed lifecycle write into an
        # indeterminate result.  Successful asset reads still consume and hash
        # the complete bounded body below.
        body = (b"" if status_code in ALLOWED_UNAVAILABLE_STATUSES
                else response.read(max_bytes + 1))
    finally:
        response.close()
    if len(body) > max_bytes:
        raise LifecycleError("Public verification response exceeded its fixed byte bound.")
    return {
        "status": status_code,
        "location": headers.get("Location"),
        "redirect_by": headers.get("X-Redirect-By"),
        "content_type": str(headers.get("Content-Type") or "").split(";", 1)[0].strip().casefold(),
        "body": body,
    }


def read_public_before() -> dict[str, Any]:
    guide = _public_request(PUBLIC_GUIDE_URL, max_bytes=MAX_ROUTE_BODY_BYTES)
    query = _public_request(PUBLIC_QUERY_URL, max_bytes=MAX_ROUTE_BODY_BYTES)
    for label, result in (("guide", guide), ("query", query)):
        if result["status"] != 301 or result["location"] != PDF_URL:
            raise LifecycleError(f"REFUSED: fixed public {label} route no longer has the audited redirect.")
    if guide["redirect_by"] != "Yoast SEO" or query["redirect_by"] != "Yoast SEO":
        raise LifecycleError("REFUSED: fixed attachment redirect is no longer attributed to Yoast SEO.")
    return {
        "guide_status": 301,
        "guide_location": PDF_URL,
        "query_status": 301,
        "query_location": PDF_URL,
        "redirect_by": "Yoast SEO",
    }


def require_public_after_unavailable() -> dict[str, Any]:
    guide = _public_request(PUBLIC_GUIDE_URL, max_bytes=MAX_ROUTE_BODY_BYTES)
    query = _public_request(PUBLIC_QUERY_URL, max_bytes=MAX_ROUTE_BODY_BYTES)
    for label, result in (("guide", guide), ("query", query)):
        if result["status"] not in ALLOWED_UNAVAILABLE_STATUSES or result["location"] is not None:
            raise IndeterminateError(
                f"Fixed public {label} route is still available or redirects after the lifecycle write."
            )
    return {"guide_status": guide["status"], "query_status": query["status"],
            "redirect": False}


def verify_historical_assets() -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for expected in FIXED_ASSETS:
        result = _public_request(expected["url"], max_bytes=MAX_ASSET_BYTES)
        observed = {
            "role": expected["role"], "url": expected["url"],
            "content_type": result["content_type"], "bytes": len(result["body"]),
            "sha256": hashlib.sha256(result["body"]).hexdigest(),
        }
        if result["status"] != 200 or result["location"] is not None \
                or observed != expected:
            raise LifecycleError("REFUSED: a fixed historical attachment asset changed or is unavailable.")
        findings.append(observed)
    return findings


class AdminPage:
    """Actor limited to the fixed attachment screen and exact REST resource."""

    def __init__(self, page: Any):
        self._page = page

    @property
    def url(self) -> str:
        return str(self._page.url)

    def goto_fixed_attachment(self) -> None:
        assert_admin_url(ADMIN_EDIT_URL)
        self._page.goto(ADMIN_EDIT_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(self.url)
        if self._page.query_selector(LOGIN_FORM_SELECTOR) is not None:
            raise LifecycleError("Authenticated WordPress browser is signed out.")
        values = self._page.evaluate(
            """() => ({
                id: document.querySelector('#post_ID')?.value || '',
                type: document.querySelector('#post_type')?.value || '',
                status: document.querySelector('#original_post_status')?.value || ''
            })"""
        )
        if values != {"id": str(ATTACHMENT_ID), "type": ATTACHMENT_TYPE,
                      "status": BEFORE_STATUS}:
            raise LifecycleError("REFUSED: fixed attachment edit-screen identity changed.")

    def _rest(self, *, write: bool) -> dict[str, Any]:
        """One fixed same-origin request. No route/payload comes from the caller."""
        assert_admin_url(self.url)
        result = self._page.evaluate(
            """async ({write, nonceUrl, readUrl, writeUrl}) => {
                const nonceResponse = await fetch(nonceUrl, {
                    method: 'GET', credentials: 'same-origin', redirect: 'error'
                });
                const nonce = await nonceResponse.text();
                if (nonceResponse.status !== 200 || !/^[0-9a-f]{10}$/.test(nonce)) {
                    return {nonce_status: nonceResponse.status, status: 0, data: null};
                }
                const options = {
                    method: write ? 'POST' : 'GET',
                    credentials: 'same-origin',
                    redirect: 'error',
                    headers: {'X-WP-Nonce': nonce}
                };
                if (write) {
                    options.headers['Content-Type'] = 'application/json';
                    options.body = JSON.stringify({status: 'private'});
                }
                const response = await fetch(write ? writeUrl : readUrl, options);
                let data = null;
                try { data = await response.json(); } catch (_) { data = null; }
                return {nonce_status: nonceResponse.status, status: response.status, data};
            }""",
            {"write": bool(write), "nonceUrl": REST_NONCE_URL,
             "readUrl": REST_READ_URL, "writeUrl": REST_URL},
        )
        if not isinstance(result, dict) or result.get("nonce_status") != 200 \
                or result.get("status") != 200 or not isinstance(result.get("data"), dict):
            if write:
                raise IndeterminateError("Fixed attachment lifecycle REST write did not return a proven object.")
            raise LifecycleError("Authenticated fixed attachment REST read failed.")
        return result["data"]

    def read_full(self, *, expected_status: str) -> dict[str, Any]:
        return assert_live_object(self._rest(write=False), expected_status=expected_status)

    def make_private_once(self) -> dict[str, Any]:
        result = self._rest(write=True)
        return assert_live_object(result, expected_status=AFTER_STATUS)


@contextlib.contextmanager
def admin_session() -> Iterator[AdminPage]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=ACTION_TIMEOUT_MS)
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            raise LifecycleError("Authenticated WordPress browser is unavailable.") from exc
        if not browser.contexts or not browser.contexts[0].pages:
            raise LifecycleError("Authenticated WordPress browser has no open page.")
        yield AdminPage(browser.contexts[0].pages[0])


def plan_lock_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".attempt-lock.json")


def result_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".result.json")


def _exclusive_json(path: Path, payload: dict[str, Any], mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError as exc:
        raise LifecycleError(f"Immutable file already exists: {path}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n")
    os.chmod(path, mode)


def write_plan(before_obj: dict[str, Any], public_before: dict[str, Any],
               assets: list[dict[str, Any]]) -> Path:
    created = utc_now()
    before = {
        "object": before_obj,
        "full_sha256": digest_for(before_obj),
        "protected_sha256": digest_for(protected_projection(before_obj)),
    }
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "origin": EXACT_ORIGIN,
        "action": ACTION,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "object_id": ATTACHMENT_ID,
        "rest_route": f"/wp-json/wp/v2/media/{ATTACHMENT_ID}",
        "write_payload": dict(WRITE_PAYLOAD),
        "before": before,
        "public_before": public_before,
        "historical_assets": assets,
        "after_expected": dict(AFTER_EXPECTED),
        "risk": RISK,
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{ACTION}_{digest[:16]}.json"
    _exclusive_json(path, plan, stat.S_IREAD)
    append_receipt("wordpress_hetron_guide_lifecycle_plan_staged",
                   f"object_id={ATTACHMENT_ID}; sha256={digest}; write=false")
    return path


def resolve_plan_path(raw: str) -> Path:
    path = Path(raw).resolve()
    if PLAN_DIR.resolve() not in path.parents:
        raise LifecycleError("Plan must be inside the fixed Hetron lifecycle plan directory.")
    return path


def _validate_asset_records(value: Any) -> None:
    if not isinstance(value, list) or len(value) != len(FIXED_ASSETS):
        raise LifecycleError("Plan historical asset set is invalid.")
    for saved, expected in zip(value, FIXED_ASSETS, strict=True):
        if not isinstance(saved, dict) or set(saved) != ASSET_KEYS or saved != expected:
            raise LifecycleError("Plan historical asset identity/hash changed.")


def load_plan(raw: str) -> dict[str, Any]:
    path = resolve_plan_path(raw)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleError("Plan is unreadable.") from exc
    if not isinstance(value, dict):
        raise LifecycleError("Plan must be one JSON object.")
    saved_hash = value.pop("sha256", None)
    if not isinstance(saved_hash, str) or not secrets.compare_digest(saved_hash, digest_for(value)):
        raise LifecycleError("Plan hash failed; reviewed plan is not immutable.")
    if set(value) != PLAN_KEYS:
        raise LifecycleError("Plan schema is not the exact closed set.")
    if (value["schema_version"] != SCHEMA_VERSION or value["tool"] != TOOL_NAME
            or value["tool_version"] != TOOL_VERSION or value["origin"] != EXACT_ORIGIN
            or value["action"] != ACTION or value["object_id"] != ATTACHMENT_ID
            or value["rest_route"] != f"/wp-json/wp/v2/media/{ATTACHMENT_ID}"
            or value["write_payload"] != WRITE_PAYLOAD or value["risk"] != RISK
            or value["after_expected"] != AFTER_EXPECTED):
        raise LifecycleError("Plan fixed identity/action contract is invalid.")
    if set(value["public_before"] or {}) != PUBLIC_BEFORE_KEYS or value["public_before"] != {
            "guide_status": 301, "guide_location": PDF_URL, "query_status": 301,
            "query_location": PDF_URL, "redirect_by": "Yoast SEO"}:
        raise LifecycleError("Plan public-before redirect contract is invalid.")
    before = value["before"]
    if not isinstance(before, dict) or set(before) != BEFORE_KEYS:
        raise LifecycleError("Plan full before record is invalid.")
    obj = assert_live_object(before["object"], expected_status=BEFORE_STATUS)
    if before["full_sha256"] != digest_for(obj) \
            or before["protected_sha256"] != digest_for(protected_projection(obj)):
        raise LifecycleError("Plan full before fingerprint is invalid.")
    _validate_asset_records(value["historical_assets"])
    try:
        created = datetime.fromisoformat(str(value["created_utc"]))
        expires = datetime.fromisoformat(str(value["expires_utc"]))
    except (TypeError, ValueError) as exc:
        raise LifecycleError("Plan timestamps are invalid.") from exc
    if (created.tzinfo is None or created.utcoffset() != timedelta(0)
            or expires.tzinfo is None or expires.utcoffset() != timedelta(0)
            or expires != created + timedelta(hours=PLAN_LIFETIME_HOURS)):
        raise LifecycleError("Plan is not an exact 24-hour UTC plan.")
    now = utc_now()
    if created > now + timedelta(minutes=PLAN_CLOCK_SKEW_MINUTES):
        raise LifecycleError("Plan creation time is in the future.")
    if now >= expires:
        raise LifecycleError("Plan expired; stage a new read-only plan.")
    if not re.fullmatch(r"[0-9a-f]{32}", str(value["nonce"])):
        raise LifecycleError("Plan nonce is invalid.")
    value["sha256"] = saved_hash
    return value


def write_attempt_lock(path: Path, plan: dict[str, Any]) -> None:
    _exclusive_json(path, {
        "plan_sha256": plan["sha256"], "object_id": ATTACHMENT_ID,
        "status": "in_flight", "started_utc": utc_now().isoformat(),
        "attempts_allowed": 1, "attempts_started": 1, "retry": False,
    }, stat.S_IREAD | stat.S_IWRITE)


def close_attempt(path: Path, plan: dict[str, Any], status_text: str,
                  detail: dict[str, Any]) -> None:
    payload = {
        "plan_sha256": plan["sha256"], "object_id": ATTACHMENT_ID,
        "status": status_text, "updated_utc": utc_now().isoformat(),
        "attempts_allowed": 1, "attempts_started": 1, "retry": False, **detail,
    }
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
                    encoding="utf-8")
    os.chmod(path, stat.S_IREAD)


def record_result(plan_path: Path, plan: dict[str, Any], status_text: str,
                  detail: dict[str, Any]) -> None:
    payload = {
        "status": status_text, "action": ACTION, "object_id": ATTACHMENT_ID,
        "plan": str(plan_path), "plan_sha256": plan["sha256"],
        "recorded_utc": utc_now().isoformat(), "retry": False, **detail,
    }
    _exclusive_json(result_path(plan_path), payload, stat.S_IREAD)
    append_receipt("wordpress_hetron_guide_lifecycle_" + status_text.casefold(),
                   f"object_id={ATTACHMENT_ID}; sha256={plan['sha256']}; status={status_text}")


def _open_commit(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    path = resolve_plan_path(args.plan)
    plan = load_plan(str(path))
    require_approval(args.approval)
    if plan_lock_path(path).exists() or result_path(path).exists():
        raise LifecycleError("Plan already entered its one allowed attempt; no retry.")
    return path, plan


def _assert_exact_before(live: dict[str, Any], plan: dict[str, Any]) -> None:
    if not secrets.compare_digest(digest_for(live), plan["before"]["full_sha256"]):
        raise LifecycleError("REFUSED: complete attachment object changed after review.")
    if digest_for(protected_projection(live)) != plan["before"]["protected_sha256"]:
        raise LifecycleError("REFUSED: protected attachment state changed after review.")


def _assert_after_preserved(before: dict[str, Any], after: dict[str, Any]) -> None:
    if digest_for(protected_projection(after)) != digest_for(protected_projection(before)):
        raise IndeterminateError("Authenticated readback changed fields outside the fixed lifecycle set.")
    if after["status"] != AFTER_STATUS:
        raise IndeterminateError("Authenticated readback did not confirm private status.")


@holds_wordpress_browser("WordPress read-only stage: retire fixed Hetron attachment 1832")
def command_stage(_args: argparse.Namespace) -> None:
    with admin_session() as admin:
        admin.goto_fixed_attachment()
        before = admin.read_full(expected_status=BEFORE_STATUS)
        public_before = read_public_before()
        assets = verify_historical_assets()
    path = write_plan(before, public_before, assets)
    plan = json.loads(path.read_text(encoding="utf-8"))
    emit({
        "status": "STAGED_NOT_COMMITTED",
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "action": ACTION,
        "object_id": ATTACHMENT_ID,
        "before_full_sha256": plan["before"]["full_sha256"],
        "before_protected_sha256": plan["before"]["protected_sha256"],
        "write_payload": WRITE_PAYLOAD,
        "after_expected": AFTER_EXPECTED,
        "risk": RISK,
        "approval_required": APPROVAL_WORD,
        "external_write_performed": False,
    })


@holds_wordpress_browser("WordPress commit: make fixed Hetron attachment 1832 private")
def command_commit(args: argparse.Namespace) -> None:
    plan_path, plan = _open_commit(args)
    lock = plan_lock_path(plan_path)
    with admin_session() as admin:
        admin.goto_fixed_attachment()
        live_before = admin.read_full(expected_status=BEFORE_STATUS)
        _assert_exact_before(live_before, plan)
        if read_public_before() != plan["public_before"]:
            raise LifecycleError("REFUSED: fixed public redirect changed after review.")
        if verify_historical_assets() != plan["historical_assets"]:
            raise LifecycleError("REFUSED: fixed historical assets changed after review.")
        write_attempt_lock(lock, plan)
        try:
            write_response = admin.make_private_once()
            _assert_after_preserved(live_before, write_response)
            authenticated_after = admin.read_full(expected_status=AFTER_STATUS)
            _assert_after_preserved(live_before, authenticated_after)
            public_after = require_public_after_unavailable()
            assets_after = verify_historical_assets()
            if assets_after != plan["historical_assets"]:
                raise IndeterminateError("Historical asset hashes changed after lifecycle write.")
        except Exception as exc:
            detail = {"stage": "write_or_verification", "reason": type(exc).__name__,
                      "write_attempted": True, "write_count": 1}
            with contextlib.suppress(Exception):
                record_result(plan_path, plan, "INDETERMINATE", detail)
            with contextlib.suppress(Exception):
                close_attempt(lock, plan, "indeterminate", detail)
            raise IndeterminateError(
                "Fixed attachment write or verification was not completely proven; "
                "plan permanently closed with no retry."
            ) from exc
    detail = {
        "write_count": 1,
        "write_payload": dict(WRITE_PAYLOAD),
        "authenticated_after_status": authenticated_after["status"],
        "authenticated_after_full_sha256": digest_for(authenticated_after),
        "authenticated_after_protected_sha256": digest_for(protected_projection(authenticated_after)),
        "public_after": public_after,
        "historical_assets_preserved": True,
        "object_preserved": True,
        "files_preserved": True,
    }
    record_result(plan_path, plan, "COMMITTED_AND_VERIFIED", detail)
    close_attempt(lock, plan, "committed_verified", detail)
    emit({
        "status": "COMMITTED_AND_VERIFIED",
        "action": ACTION,
        "object_id": ATTACHMENT_ID,
        "authenticated_status": AFTER_STATUS,
        "public_after": public_after,
        "historical_assets_preserved": True,
        "direct_pdf_still_public_by_exact_url": True,
        "object_deleted": False,
        "files_deleted": False,
        "write_count": 1,
        "retry": False,
        "plan_sha256": plan["sha256"],
        "replay_locked": True,
    })


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stage").set_defaults(func=command_stage)
    commit = commands.add_parser("commit")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (LifecycleError, OSError, ValueError, UiLaneBusy, UiLaneLockError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
