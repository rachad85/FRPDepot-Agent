#!/usr/bin/env python
"""FRP Depot Packing Ring Media Upload Tool.

Commissioned by Rachad Homsi on 2026-08-12 (Discord, "Yes" to the explicit
question: build and test a fixed, approval-gated media-upload tool restricted to
the six approved Packing Ring image hashes). Commissioning authorises BUILDING,
TESTING and DOCUMENTING. It is NOT approval of any website change: every upload
still needs Rachad's own byte-exact one-word APPROVED against one exact staged
plan.

SCOPE -- SIX FIXED FILES, ONE WRITE ROUTE, NOTHING ELSE.

    stage    read-only. Validates the six files against disk and the approved
             manifest, proves no duplicate already exists in the Media Library,
             and writes ONE immutable local plan. Zero website writes.
    commit   uploads those six exact PNGs, in order, through Rachad's already
             authenticated WordPress admin browser. One upload per file.

The file paths, names, byte sizes, dimensions, colour mode, format, SHA-256
digests, upload order, site origin and CDP endpoint are all hard-coded constants.
A caller supplies a PLAN PATH and an APPROVAL WORD -- nothing else. There is no
argument anywhere in this module for an image path, URL, filename, hash, title,
alt text, attachment id, product id, selector, browser endpoint, site URL, count,
subset or ordering, so no seventh file, no review sheet, no ZIP, no source photo
and no arbitrary path can be reached through it.

WHAT THIS TOOL CANNOT DO, BY CONSTRUCTION: edit, crop, rotate, scale, rename,
convert, replace, delete, detach, re-attach or re-title media; create, update,
delete, publish or unpublish a WooCommerce product or variation; touch orders,
customers, payments, refunds, coupons, settings, themes, plugins, users, posts,
pages, comments, taxonomies, shipping, stock, Zoho or email; issue any REST call
of any verb; read or persist a cookie, password, API key, CSRF nonce, storage
entry or header; dump a page; retry a failed plan; or clean up after itself.
There is no REST client, no subprocess import, no credential store and no delete
route in this file.

*** THE UPLOAD IS NOT ATOMIC AND HAS NO ROLLBACK. THIS IS DELIBERATE. ***
Six files are six independent WordPress submissions. If upload 4 fails, uploads
1-3 REMAIN LIVE in the Media Library, the plan is permanently locked
`indeterminate` with `no_retry: true`, and uploads 5-6 never happen. Nothing here
deletes a successful upload, because a delete route is a far more dangerous
capability than a leftover unattached image, and unattached media is invisible to
customers until a separately commissioned tool attaches it. Every staged plan
states this in `risk` so Rachad approves the exposure, not just the intent.

BROWSER ACCESS. Admin work attaches to the ALREADY AUTHENTICATED loopback CDP
session at 127.0.0.1:9229 that Rachad opened. This tool never launches a browser,
never signs in, and never opens an anonymous or persistent profile. Public
verification downloads are plain HTTPS GETs with no credentials, no cookies and
redirects refused.

BROWSER LANE LOCK. Both commands hold the shared WordPress named mutex from
`ui_lane_lock` for their whole run -- taken BEFORE the plan's attempt lock, so a
busy browser is a FREE refusal that costs Rachad nothing rather than a mid-write
abort that permanently burns his approved plan.

DUPLICATE PREFLIGHT -- BOTH GATES ARE COMPLETE OR THE RUN IS A REFUSAL.
A public, credential-free media endpoint was NOT used, because it cannot be shown
sufficient: WordPress exposes unattached and draft-attached media inconsistently
to anonymous callers, so "not publicly visible" is not "not present". The check
therefore reads the authenticated Media Library list screen, read-only, under the
lane lock.

  * NAME GATE -- complete. Every attachment in the library is enumerated with a
    bounded page walk, and completeness is PROVEN against the list table's own
    "N items" count rather than assumed from a loop that ended. If the count
    cannot be read, or the enumerated ids do not reach it, the stage FAILS CLOSED
    rather than reporting a duplicate-free library it did not finish reading.
    A conflict is any attachment whose filename equals one of the six fixed
    basenames, or whose stem equals a fixed stem once WordPress's own `-N`
    de-duplication suffix is stripped (`01_hero_three_quarter-1.png` is exactly
    what a re-upload looks like, and it is a conflict).
  * HASH GATE -- COMPLETE over every enumerated image attachment. EVERY image row
    in the library -- not a sample, not only the name conflicts -- is opened
    on its own fixed attachment screen, its one public original URL is downloaded
    anonymously and byte-bounded, and its full SHA-256 is compared to all six
    fixed digests. An identical image stored under a completely unrelated OLDER
    filename IS therefore detected. There is no sampling path in this module: if
    one row cannot be identified, its URL cannot be proven safe, its download
    fails or redirects or exceeds the byte bound, or its bytes cannot be hashed,
    the whole check is INCOMPLETE and the run FAILS CLOSED before any attempt
    lock exists. If the library is larger than the hard row, page, image or
    cumulative-byte bounds, that is likewise a refusal -- never a partial scan
    reported as a clean one.

  THE COST IS REAL AND IS NOT A LIMIT ON COMPLETENESS: a complete hash gate
  downloads every image in the Media Library once per stage and once per commit.
  That is deliberate. A cheap check that cannot see an older duplicate is worth
  nothing here, because the whole point of the gate is that these six exact
  images are not already on the site under some other name.
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
from urllib.parse import parse_qsl, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

# One writer at a time on the shared authenticated WordPress window. Appended,
# never inserted first, so it cannot shadow a stdlib name.
sys.path.append(str(Path(__file__).resolve().parent.parent / "common"))
from ui_lane_lock import UiLaneBusy, UiLaneLockError, ui_browser_lock  # noqa: E402

TOOL_NAME = "FRP Depot Packing Ring Media Upload Tool"
# 2.0.0 / schema 2: the duplicate hash gate became COMPLETE over every enumerated
# image attachment. The version and schema are bumped together and checked on
# load so a plan cut under the old bounded-sample gate can never be committed by
# this build -- an incomplete duplicate proof must not survive a tool upgrade.
TOOL_VERSION = "2.0.0"
SCHEMA_VERSION = 2

ROOT = Path(r"C:\FRPDepot")
GALLERY_DIR = (ROOT / "Dado" / "20_Working" / "packing_rings"
               / "generated_gallery_20260812")
MANIFEST_PATH = GALLERY_DIR / "manifest.json"
PLAN_DIR = ROOT / "Dado" / "20_Working" / "wordpress_packing_ring_media_plans"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
PLAN_LIFETIME_HOURS = 24

# Rachad's approval is his own one word, compared BYTE-EXACT. Never stripped,
# never case-folded: "approved", " APPROVED", "Approved" and "yes" are refusals.
APPROVAL_WORD = "APPROVED"

ACTION = "packing_ring_media_upload"
CLI_ACTIONS = ("stage", "commit")

# ---------------------------------------------------------------------------
# Fixed identity. None of these is ever taken from a caller.
# ---------------------------------------------------------------------------
EXACT_ORIGIN = "https://frpdepots.com"
ALLOWED_HOST = "frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"

EXPECTED_FORMAT = "PNG"
EXPECTED_MODE = "RGB"
EXPECTED_WIDTH = 1024
EXPECTED_HEIGHT = 1024

# The ONLY six files this tool may ever upload, in the ONLY order it may use
# them. Verified against disk and against the approved manifest at build time,
# at stage time, and again immediately before the commit attempt lock.
FIXED_IMAGES: tuple[dict[str, Any], ...] = (
    {
        "position": 1,
        "filename": "01_hero_three_quarter.png",
        "label": "Hero \u2014 three-quarter",
        "bytes": 1115449,
        "sha256": "208889137ea0d182ddf982879c44550965054bc90027d5a12792ff2f02df7ba1",
    },
    {
        "position": 2,
        "filename": "02_top_view.png",
        "label": "Top view",
        "bytes": 1023232,
        "sha256": "b96b4027351fed5773ac88f92a5bf5fc1b16a86396d6dbf5262f86a6d727a682",
    },
    {
        "position": 3,
        "filename": "03_low_side_angle.png",
        "label": "Low side angle",
        "bytes": 1277080,
        "sha256": "0325575d0cd6e7ec9dd0f424f4c240b44d4d61e223c14df6191444e0302e5ceb",
    },
    {
        "position": 4,
        "filename": "04_opposite_face.png",
        "label": "Opposite face",
        "bytes": 1251390,
        "sha256": "7111d25fb66e8638092444fd3fe374d153bb138ad3197621bef07fde86c57e29",
    },
    {
        "position": 5,
        "filename": "05_laminate_macro.png",
        "label": "Laminate and hole detail",
        "bytes": 1387654,
        "sha256": "68d7b7a21649fbd601e52173cc7deef0baaed65be02b35956f0b79d1008fc6e1",
    },
    {
        "position": 6,
        "filename": "06_edge_profile.png",
        "label": "Edge profile",
        "bytes": 1251964,
        "sha256": "40eb6273e33cec0939dd069c384a586aeefbe4318cac1307e8bbf9f18b3d6bad",
    },
)

FIXED_FILENAMES = tuple(record["filename"] for record in FIXED_IMAGES)
FIXED_STEMS = tuple(Path(name).stem.casefold() for name in FIXED_FILENAMES)
FIXED_HASHES = {record["sha256"]: record["filename"] for record in FIXED_IMAGES}

# Present in the same folder and DELIBERATELY unreachable. Listed so the tests
# can assert unreachability by name rather than by absence of evidence, and so a
# later reader cannot mistake the omission for an oversight.
NEVER_UPLOAD = (
    "00_gallery_review_sheet.jpg",
    "frp_packing_ring_generated_gallery_draft_20260812.zip",
)

# What the images are and are not. Carried into every plan so the claim Rachad
# approves is the same claim the gallery manifest makes.
IMAGE_PROVENANCE = (
    "Representative marketing drafts generated for FRP Depot on 2026-08-12 and "
    "QC-approved in the gallery manifest. They are NOT dimensional evidence: "
    "bore, thickness, bolt circle, hole count and hole diameter are not claimed "
    "by these images and must not be inferred from them."
)

RISK_DISCLOSURE = (
    "NOT ATOMIC AND NOT REVERSIBLE BY THIS TOOL. The six files are six "
    "independent WordPress uploads. If upload N fails, uploads 1..N-1 remain "
    "live in the Media Library, uploads N+1..6 are never attempted, and this "
    "plan is permanently locked indeterminate with no_retry: true. This tool has "
    "no delete, detach, replace, rename or rollback route by design, so removing "
    "a leftover upload is a manual WordPress action or a separately commissioned "
    "tool. Uploaded media is unattached: it is not added to any product, is not "
    "published anywhere, and changes no price, stock, order or customer record."
)

# ---------------------------------------------------------------------------
# Fixed navigation. Three admin paths, each with a closed set of query shapes.
# Anything else -- another path, another key, a login screen, a redirect, a
# foreign host, http, a non-443 port -- is refused before anything is clicked.
# ---------------------------------------------------------------------------
MEDIA_NEW_PATH = "/wp-admin/media-new.php"
UPLOAD_LIST_PATH = "/wp-admin/upload.php"
POST_EDIT_PATH = "/wp-admin/post.php"

BROWSER_UPLOADER_KEY = "browser-uploader"
MEDIA_NEW_URL = f"{EXACT_ORIGIN}{MEDIA_NEW_PATH}?{BROWSER_UPLOADER_KEY}"
UPLOAD_LIST_URL = f"{EXACT_ORIGIN}{UPLOAD_LIST_PATH}"

ALLOWED_ADMIN_PATHS = frozenset({MEDIA_NEW_PATH, UPLOAD_LIST_PATH, POST_EDIT_PATH})

# Bounded, explicit, and NOTHING here converts a limit into a pass. Every one of
# these is a hard ceiling on how much may be read; exceeding one is a refusal,
# never a truncated scan reported as complete.
MAX_LIBRARY_PAGES = 200
MAX_LIBRARY_ROWS = 4000
MAX_IMAGE_ATTACHMENTS = 2000
MAX_DOWNLOAD_BYTES = 8_000_000
MAX_TOTAL_DOWNLOAD_BYTES = 1_500_000_000
PUBLIC_TIMEOUT_SECONDS = 45

NAV_TIMEOUT_MS = 45_000
LOAD_STATE_TIMEOUT_MS = 120_000
ACTION_TIMEOUT_MS = 15_000
UPLOAD_TIMEOUT_MS = 180_000

# Selectors. Every one is a fixed string; nothing is interpolated from a page,
# a plan or a caller except a positive integer attachment id that this module
# validated itself.
FILE_INPUT_SELECTOR = 'input[type="file"][name="async-upload"]'
SUBMIT_SELECTOR = 'input[type="submit"][name="html-upload"]'
LOGIN_FORM_SELECTOR = "form#loginform"
LIST_ROW_SELECTOR = "#the-list tr"
LIST_COUNT_SELECTOR = ".displaying-num"
ROW_LINK_SELECTOR = "a[href]"
ROW_FILENAME_SELECTOR = "p.filename"
ATTACHMENT_URL_SELECTOR = "input#attachment_url"
ATTACHMENT_URL_FALLBACK_SELECTOR = ".misc-pub-attachment input.urlfield"
ATTACHMENT_FILENAME_SELECTOR = ".misc-pub-filename"
ATTACHMENT_FILETYPE_SELECTOR = ".misc-pub-filetype"

LIST_COUNT_PATTERN = re.compile(r"([0-9][0-9,]*)\s+items?\b", re.IGNORECASE)
DEDUPE_SUFFIX_PATTERN = re.compile(r"-\d+$")
UPLOADS_PATH_PATTERN = re.compile(r"^/wp-content/uploads/\d{4}/\d{2}/(?P<name>[^/]+)$")

# The six approved files are PNG, so every UPLOAD path in this tool is PNG-only.
# The duplicate scan is deliberately wider: an identical image could have been
# stored on the site in any raster format, and a gate that only ever looked at
# PNGs would be another quiet hole. These are the only extensions the scan will
# open, and anything outside them is treated as not-an-image, never as read.
PNG_ONLY: tuple[str, ...] = (".png",)
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
SCANNED_EXTENSIONS: tuple[str, ...] = tuple(sorted(IMAGE_EXTENSIONS))
IMAGE_CONTENT_TYPES: dict[str, tuple[str, ...]] = {
    ".png": ("image/png",),
    ".jpg": ("image/jpeg",),
    ".jpeg": ("image/jpeg",),
    ".gif": ("image/gif",),
    ".webp": ("image/webp",),
}
# What the attachment screen's own "File type" box must state for each extension.
IMAGE_TYPE_TOKENS: dict[str, tuple[str, ...]] = {
    ".png": ("png",),
    ".jpg": ("jpeg", "jpg"),
    ".jpeg": ("jpeg", "jpg"),
    ".gif": ("gif",),
    ".webp": ("webp",),
}

PUBLIC_USER_AGENT = "FRPDepot-Dado-MediaVerify/1.0"

# The contract Rachad approves alongside the six files. Staged verbatim, checked
# verbatim at commit, so a plan cut under different rules can never be committed.
UPLOAD_CONTRACT: dict[str, Any] = {
    "uploads": len(FIXED_IMAGES),
    "one_submission_per_file": True,
    "atomic": False,
    "rollback_available": False,
    "delete_available": False,
    "retry_available": False,
    "attaches_to_product": False,
    "changes_product_or_variation": False,
    "changes_price_stock_or_order": False,
    "sends_email": False,
    "rest_api_used": False,
    "credentials_read": False,
    "browser": "existing authenticated loopback CDP session only",
    "cdp_endpoint": CDP_ENDPOINT,
    "admin_paths": sorted(ALLOWED_ADMIN_PATHS),
    "duplicate_gate": (
        "COMPLETE. The name gate covers every enumerated attachment; the hash "
        "gate covers EVERY enumerated image attachment -- each one opened on its "
        "own fixed attachment screen, its single public original URL downloaded "
        "anonymously and byte-bounded, and its full SHA-256 compared to all six "
        "fixed digests. No sampling, no recency window, no unproven older "
        "filename. Any row that cannot be identified, proven safe, downloaded "
        "within bounds or hashed makes the whole check INCOMPLETE and refuses "
        "before any attempt lock exists."
    ),
    "verification": (
        "per upload: exactly one new positive attachment id, its own fixed "
        "attachment screen, exactly one public original URL on the exact host, "
        "the exact expected basename with no -N suffix, PNG, then a credential-"
        "free bounded download whose full SHA-256 equals the fixed local digest; "
        "then one fresh read-only pass over all six"
    ),
    "approval_word": APPROVAL_WORD,
    "approval_comparison": "byte-exact, unpadded, not case-folded",
    "provenance": IMAGE_PROVENANCE,
    "risk": RISK_DISCLOSURE,
}

PLAN_KEYS = frozenset({
    "schema_version", "tool", "tool_version", "action", "origin", "cdp_endpoint",
    "created_utc", "expires_utc", "nonce", "approval_word", "risk", "provenance",
    "images", "local_integrity", "manifest", "duplicate_check", "upload_contract",
})
PLAN_IMAGE_KEYS = frozenset({
    "position", "filename", "label", "path", "sha256", "bytes", "width", "height",
    "format", "mode",
})
INTEGRITY_KEYS = frozenset({
    "position", "filename", "path", "sha256", "bytes", "width", "height", "format",
    "mode", "regular_file", "symlink", "inside_gallery", "manifest_qc",
})
MANIFEST_KEYS = frozenset({"path", "sha256", "images", "qc_all_approved"})
DUPLICATE_KEYS = frozenset({
    "method", "scope", "checked_utc", "library_total", "enumerated", "pages_read",
    "enumeration_complete", "image_rows", "image_hashes_completed", "hash_failures",
    "hash_bytes_read", "hash_complete", "complete", "name_conflicts",
    "hash_conflicts",
})
RESULT_KEYS = frozenset({
    "position", "filename", "sha256", "attachment_id", "source_url", "bytes",
    "verified_utc",
})


class MediaUploadError(RuntimeError):
    """A refusal or verified failure. Messages never carry page or secret text."""


class DuplicateFound(MediaUploadError):
    """The library already holds one of the fixed files. Nothing was uploaded."""


class IndeterminateError(MediaUploadError):
    """The live state could not be established. Never retried automatically."""


def holds_wordpress_browser(purpose: str):
    """Serialize a whole command against the shared WordPress admin browser.

    Applied to the COMMAND so the hold spans the duplicate preflight, all six
    uploads and the final fresh verification: the other lane must not be able to
    navigate the shared page between an upload and its read-back. Re-entrant per
    thread, so the nested admin_session() calls inside cost nothing.
    """
    def decorate(function):
        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any):
            with ui_browser_lock("wordpress", purpose=purpose):
                return function(*args, **kwargs)
        return wrapper
    return decorate


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": utc_now().isoformat(), "action": action, "evidence": evidence}
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def require_rachad_approval(approval: Any) -> None:
    """Byte-exact uppercase one-word approval, before any browser or network work.

    Deliberately NOT stripped and NOT case-folded. A padded or lower-case word is
    a different string, and a tool that quietly repairs it is a tool that accepts
    something Rachad did not type.
    """
    if not isinstance(approval, str) or approval != APPROVAL_WORD:
        raise MediaUploadError(
            f"Rachad must answer this staged plan with the exact one-word approval: "
            f"{APPROVAL_WORD}. It is compared byte-exact -- no trimming, no case "
            "folding, no synonyms. Commissioning is not change approval."
        )


class NoRedirectHandler(HTTPRedirectHandler):
    """Refuse redirects, so a public verification GET cannot be walked elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


# ---------------------------------------------------------------------------
# URL guards
# ---------------------------------------------------------------------------
def _split_query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlsplit(str(url)).query, keep_blank_values=True)


def assert_origin(url: str) -> None:
    parsed = urlsplit(str(url or ""))
    try:
        port = parsed.port
    except ValueError as exc:
        raise MediaUploadError("REFUSED: the URL carries an invalid port.") from exc
    if (parsed.scheme != "https" or (parsed.hostname or "").casefold() != ALLOWED_HOST
            or port not in (None, 443) or parsed.username or parsed.password):
        raise MediaUploadError(
            f"REFUSED: the URL is not on the exact FRP Depot origin {EXACT_ORIGIN} "
            "over HTTPS on port 443 with no user information."
        )


def assert_admin_url(url: str) -> None:
    """One of three admin paths, each with a closed set of query shapes."""
    assert_origin(url)
    parsed = urlsplit(str(url))
    path = parsed.path or "/"
    if path not in ALLOWED_ADMIN_PATHS:
        raise MediaUploadError(
            "REFUSED: the WordPress page is not one of the three allowlisted admin "
            "paths (media-new, upload list, attachment edit). Sign-in, settings, "
            "products and every other screen are out of scope."
        )
    pairs = _split_query(url)
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise MediaUploadError("REFUSED: the admin URL repeats a query key.")
    values = dict(pairs)

    if path == MEDIA_NEW_PATH:
        if keys != [BROWSER_UPLOADER_KEY] or values[BROWSER_UPLOADER_KEY] not in ("", "1"):
            raise MediaUploadError(
                "REFUSED: media-new.php may carry only the fixed browser-uploader "
                "switch and no other query key."
            )
        return

    if path == UPLOAD_LIST_PATH:
        if not keys:
            return
        if sorted(keys) == ["posted"]:
            _require_positive_int(values["posted"], "the upload result id")
            return
        if sorted(keys) == ["mode", "paged"]:
            if values["mode"] != "list":
                raise MediaUploadError("REFUSED: upload.php mode must be the fixed list mode.")
            page = _require_positive_int(values["paged"], "the media library page")
            if page > MAX_LIBRARY_PAGES:
                raise MediaUploadError("REFUSED: the media library page is beyond the read bound.")
            return
        raise MediaUploadError(
            "REFUSED: upload.php may carry only the fixed upload-result id or the "
            "fixed bounded list-mode page, and nothing else."
        )

    # POST_EDIT_PATH
    if sorted(keys) != ["action", "post"] or values["action"] != "edit":
        raise MediaUploadError(
            "REFUSED: post.php may carry only post=<id> and action=edit."
        )
    _require_positive_int(values["post"], "the attachment id")


def _require_positive_int(value: Any, label: str) -> int:
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise MediaUploadError(f"REFUSED: {label} is not a positive integer.")
    return int(text)


def attachment_edit_url(attachment_id: int) -> str:
    """Built here from a validated integer; never taken from a page or a caller."""
    checked = _require_positive_int(attachment_id, "the attachment id")
    return f"{EXACT_ORIGIN}{POST_EDIT_PATH}?post={checked}&action=edit"


def library_page_url(page: int) -> str:
    checked = _require_positive_int(page, "the media library page")
    if checked > MAX_LIBRARY_PAGES:
        raise MediaUploadError("REFUSED: the media library page is beyond the read bound.")
    return f"{EXACT_ORIGIN}{UPLOAD_LIST_PATH}?mode=list&paged={checked}"


def parse_attachment_edit_link(href: str) -> int | None:
    """Return the attachment id of an exact edit link, or None if it is not one."""
    try:
        assert_origin(href)
    except MediaUploadError:
        return None
    parsed = urlsplit(str(href))
    if (parsed.path or "") != POST_EDIT_PATH:
        return None
    values = dict(_split_query(href))
    if sorted(values) != ["action", "post"] or values["action"] != "edit":
        return None
    try:
        return _require_positive_int(values["post"], "the attachment id")
    except MediaUploadError:
        return None


def _extension_of(name: str) -> str:
    return Path(str(name)).suffix.casefold()


def assert_public_upload_url(url: str, *, expected_basename: str | None = None,
                             allowed_extensions: tuple[str, ...] = PNG_ONLY) -> str:
    """One public original-file URL on the exact host, in the uploads tree.

    `allowed_extensions` defaults to PNG only, which is what every UPLOAD path
    here needs. The duplicate scan widens it to the raster formats it is willing
    to open -- and nothing else is ever downloadable through this function.
    """
    assert_origin(url)
    parsed = urlsplit(str(url))
    if parsed.query or parsed.fragment:
        raise MediaUploadError(
            "REFUSED: the public file URL carries a query or fragment; only the "
            "plain original-file URL is accepted."
        )
    found = UPLOADS_PATH_PATTERN.fullmatch(parsed.path or "")
    if not found:
        raise MediaUploadError(
            "REFUSED: the public file URL is not a plain /wp-content/uploads/YYYY/MM/ path."
        )
    name = found.group("name")
    if "/" in name or "\\" in name or name in ("", ".", "..") or "%" in name:
        raise MediaUploadError("REFUSED: the public file name is not a safe plain name.")
    if _extension_of(name) not in allowed_extensions:
        raise MediaUploadError(
            "REFUSED: the public file is not one of the supported image originals "
            f"({', '.join(allowed_extensions)})."
        )
    if expected_basename is not None and name != expected_basename:
        raise MediaUploadError(
            "REFUSED: WordPress stored the upload under a different file name than "
            f"the fixed {expected_basename!r} (a -N suffix means a same-named file "
            "already existed). Nothing further was uploaded."
        )
    return name


def download_public_bytes(url: str, *, expected_basename: str | None = None,
                          allowed_extensions: tuple[str, ...] = PNG_ONLY) -> bytes:
    """Anonymous, redirect-free, byte-bounded GET of one public original file.

    No cookie jar, no credential header, no credentials of any kind: this must
    see exactly what the public sees, and it must not be walkable to another host.
    The served content type must match the extension the URL guard already
    accepted, so a PNG URL serving something else is a refusal rather than bytes.
    """
    name = assert_public_upload_url(url, expected_basename=expected_basename,
                                    allowed_extensions=allowed_extensions)
    wanted = IMAGE_CONTENT_TYPES[_extension_of(name)]
    accept = ", ".join(sorted({
        value for extension in allowed_extensions
        for value in IMAGE_CONTENT_TYPES[extension]
    }))
    request = Request(
        str(url),
        headers={"User-Agent": PUBLIC_USER_AGENT, "Accept": accept},
        method="GET",
    )
    opener = build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=PUBLIC_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status != 200:
                raise MediaUploadError(
                    f"REFUSED: the public file download answered HTTP {status}."
                )
            content_type = str(response.headers.get("Content-Type") or "").split(";")[0].strip()
            if content_type and content_type.casefold() not in wanted:
                raise MediaUploadError(
                    f"REFUSED: the public file is not served as {' or '.join(wanted)}."
                )
            data = response.read(MAX_DOWNLOAD_BYTES + 1)
    except HTTPError as exc:
        raise MediaUploadError(
            f"REFUSED: the public file download failed with HTTP {exc.code}."
        ) from exc
    except URLError as exc:
        raise MediaUploadError(
            "REFUSED: the public file could not be reached for verification."
        ) from exc
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise MediaUploadError(
            f"REFUSED: the public file exceeds the {MAX_DOWNLOAD_BYTES}-byte read bound."
        )
    return data


# ---------------------------------------------------------------------------
# Local file and manifest verification
# ---------------------------------------------------------------------------
def fixed_path(filename: str) -> Path:
    """The one hard-coded absolute path for a fixed filename."""
    if filename not in FIXED_FILENAMES:
        raise MediaUploadError(
            "REFUSED: only the six fixed Packing Ring images exist in this tool."
        )
    return GALLERY_DIR / filename


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)


def _verify_image_bytes(path: Path) -> tuple[str, str, int, int]:
    """Format, mode and size, read through Pillow with the bomb guard intact.

    Pillow's MAX_IMAGE_PIXELS ceiling is asserted rather than raised or disabled;
    a decompression-bomb warning is promoted to an error so an oversized payload
    cannot be read past. verify() then a fresh open()+load() is the documented
    order -- verify() leaves the file unusable, so re-opening is required rather
    than optional.
    """
    import warnings

    from PIL import Image

    if Image.MAX_IMAGE_PIXELS is None:
        raise MediaUploadError(
            "REFUSED: the Pillow decompression-bomb ceiling has been disabled in "
            "this process. Refusing to read image data without it."
        )
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(path) as probe:
                probe.verify()
            with Image.open(path) as image:
                image.load()
                return str(image.format or ""), str(image.mode or ""), int(image.width), int(image.height)
        except MediaUploadError:
            raise
        except Exception as exc:  # noqa: BLE001 - any decoder failure is a refusal
            raise MediaUploadError(
                f"REFUSED: {path.name} is not a readable, safe PNG image."
            ) from exc


def verify_manifest() -> dict[str, Any]:
    """The approved gallery manifest must still name all six, hash-for-hash, QC approved."""
    try:
        raw = MANIFEST_PATH.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaUploadError(
            f"REFUSED: the approved gallery manifest is unreadable: {MANIFEST_PATH}"
        ) from exc
    if not isinstance(manifest, dict):
        raise MediaUploadError("REFUSED: the gallery manifest must contain one object.")
    images = manifest.get("images")
    if not isinstance(images, list) or len(images) != len(FIXED_IMAGES):
        raise MediaUploadError(
            f"REFUSED: the gallery manifest does not describe exactly "
            f"{len(FIXED_IMAGES)} images."
        )
    projected: list[dict[str, Any]] = []
    for record, expected in zip(images, FIXED_IMAGES):
        if not isinstance(record, dict):
            raise MediaUploadError("REFUSED: a gallery manifest image record is not an object.")
        if str(record.get("filename") or "") != expected["filename"]:
            raise MediaUploadError(
                "REFUSED: the gallery manifest images are not the six fixed files in "
                "the fixed order."
            )
        if str(record.get("sha256") or "") != expected["sha256"]:
            raise MediaUploadError(
                f"REFUSED: the gallery manifest hash for {expected['filename']} is not "
                "the approved digest."
            )
        if int(record.get("bytes") or 0) != expected["bytes"]:
            raise MediaUploadError(
                f"REFUSED: the gallery manifest byte size for {expected['filename']} is "
                "not the approved size."
            )
        if (int(record.get("width") or 0) != EXPECTED_WIDTH
                or int(record.get("height") or 0) != EXPECTED_HEIGHT):
            raise MediaUploadError(
                f"REFUSED: the gallery manifest dimensions for {expected['filename']} are "
                f"not {EXPECTED_WIDTH}x{EXPECTED_HEIGHT}."
            )
        if str(record.get("qc") or "") != "approved":
            raise MediaUploadError(
                f"REFUSED: {expected['filename']} is not marked qc: approved in the "
                "gallery manifest."
            )
        projected.append({
            "filename": expected["filename"],
            "sha256": expected["sha256"],
            "bytes": expected["bytes"],
            "qc": "approved",
        })
    return {
        "path": str(MANIFEST_PATH),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "images": projected,
        "qc_all_approved": True,
    }


def verify_local_files() -> list[dict[str, Any]]:
    """Prove the six files on disk are byte-for-byte the six approved images.

    Every property the plan will assert is measured here: the exact hard-coded
    path, containment in the one gallery folder, a regular non-reparse file, the
    exact basename, the exact byte size, the full SHA-256, and PNG/RGB/1024x1024
    through a safe Pillow read. An extra, missing, renamed, resized, re-encoded,
    truncated, symlinked or reordered file fails here, before anything else runs.
    """
    manifest = verify_manifest()
    manifest_names = {record["filename"] for record in manifest["images"]}
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in FIXED_IMAGES:
        filename = record["filename"]
        if filename in seen:  # pragma: no cover - constants are unique
            raise MediaUploadError("REFUSED: the fixed image list repeats a filename.")
        seen.add(filename)
        path = fixed_path(filename)
        if path.name != filename:  # pragma: no cover - constructed from the name
            raise MediaUploadError("REFUSED: the resolved file name is not the fixed name.")
        if _is_reparse_point(path):
            raise MediaUploadError(
                f"REFUSED: {filename} is a symlink or reparse point, not a real file."
            )
        if not path.is_file():
            raise MediaUploadError(f"REFUSED: the approved image is missing: {path}")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise MediaUploadError(f"REFUSED: the approved image cannot be resolved: {path}") from exc
        if resolved != (GALLERY_DIR.resolve() / filename):
            raise MediaUploadError(
                f"REFUSED: {filename} does not resolve inside the one approved gallery folder."
            )
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise MediaUploadError(f"REFUSED: {filename} is not a regular file.")
        size = int(info.st_size)
        if size != record["bytes"]:
            raise MediaUploadError(
                f"REFUSED: {filename} is {size} bytes, not the approved {record['bytes']}."
            )
        data = path.read_bytes()
        if len(data) != record["bytes"]:
            raise MediaUploadError(f"REFUSED: {filename} changed size while being read.")
        digest = hashlib.sha256(data).hexdigest()
        if not secrets.compare_digest(digest, record["sha256"]):
            raise MediaUploadError(
                f"REFUSED: {filename} does not match its approved SHA-256. The approved "
                "image was changed on disk; nothing may be uploaded."
            )
        image_format, mode, width, height = _verify_image_bytes(path)
        if image_format != EXPECTED_FORMAT:
            raise MediaUploadError(f"REFUSED: {filename} is {image_format or 'unknown'}, not PNG.")
        if mode != EXPECTED_MODE:
            raise MediaUploadError(f"REFUSED: {filename} is mode {mode or 'unknown'}, not RGB.")
        if width != EXPECTED_WIDTH or height != EXPECTED_HEIGHT:
            raise MediaUploadError(
                f"REFUSED: {filename} is {width}x{height}, not "
                f"{EXPECTED_WIDTH}x{EXPECTED_HEIGHT}."
            )
        if filename not in manifest_names:  # pragma: no cover - checked above
            raise MediaUploadError(f"REFUSED: {filename} is not in the approved manifest.")
        evidence.append({
            "position": record["position"],
            "filename": filename,
            "path": str(path),
            "sha256": digest,
            "bytes": size,
            "width": width,
            "height": height,
            "format": image_format,
            "mode": mode,
            "regular_file": True,
            "symlink": False,
            "inside_gallery": True,
            "manifest_qc": "approved",
        })
    if [item["position"] for item in evidence] != list(range(1, len(FIXED_IMAGES) + 1)):
        raise MediaUploadError("REFUSED: the verified images are not in the fixed order 1..6.")
    return evidence


def plan_image_records() -> list[dict[str, Any]]:
    """The six fixed images as the plan states them. Derived only from constants."""
    return [{
        "position": record["position"],
        "filename": record["filename"],
        "label": record["label"],
        "path": str(fixed_path(record["filename"])),
        "sha256": record["sha256"],
        "bytes": record["bytes"],
        "width": EXPECTED_WIDTH,
        "height": EXPECTED_HEIGHT,
        "format": EXPECTED_FORMAT,
        "mode": EXPECTED_MODE,
    } for record in FIXED_IMAGES]


# ---------------------------------------------------------------------------
# The one narrow reader/actor over the WordPress admin screens
# ---------------------------------------------------------------------------
def _normalise_stem(filename: str) -> str:
    """WordPress's own de-duplication suffix removed, so a re-upload is visible.

    `01_hero_three_quarter-1.png` is exactly what WordPress writes when a file of
    that name already exists. Comparing raw stems would call that a different
    file and let a second copy through.
    """
    stem = Path(str(filename or "")).stem.casefold()
    return DEDUPE_SUFFIX_PATTERN.sub("", stem)


class AdminPage:
    """Read the Media Library, upload one fixed file, read one attachment back.

    Every navigation goes through the admin allowlist. Nothing here reads a
    cookie, a hidden input, a nonce, a header, a storage entry or a page dump:
    the SIGNED-IN PAGE ITSELF submits its own nonce with the form, which is the
    whole reason this route is safer than forging a REST call.
    """

    def __init__(self, page: Any):
        self._page = page

    @property
    def url(self) -> str:
        return str(self._page.url)

    def _goto(self, url: str) -> None:
        assert_admin_url(url)
        self._page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        self._assert_landed()

    def _assert_landed(self) -> None:
        """Where we actually are must still be allowlisted, and not a login screen."""
        assert_admin_url(self.url)
        if self._page.query_selector(LOGIN_FORM_SELECTOR) is not None:
            raise MediaUploadError(
                "REFUSED: the WordPress session is showing a sign-in screen. Ask "
                "Rachad to re-open CONNECT_DADO_WORDPRESS_UI.bat and sign in there. "
                "Nothing was uploaded."
            )

    def leave_on_media_list(self) -> None:
        """Finish on a harmless list screen, never on a primed form."""
        with contextlib.suppress(Exception):
            self._goto(UPLOAD_LIST_URL)

    # -- library enumeration ------------------------------------------------
    def _row_records(self) -> list[dict[str, Any]]:
        rows = self._page.query_selector_all(LIST_ROW_SELECTOR)
        found: list[dict[str, Any]] = []
        for row in rows:
            ids = set()
            for link in row.query_selector_all(ROW_LINK_SELECTOR):
                attachment_id = parse_attachment_edit_link(str(link.get_attribute("href") or ""))
                if attachment_id is not None:
                    ids.add(attachment_id)
            if len(ids) != 1:
                # A row we cannot identify unambiguously makes the enumeration
                # incomplete. Recorded, never silently skipped.
                found.append({"id": None, "filename": "", "stem": ""})
                continue
            attachment_id = ids.pop()
            name_node = row.query_selector(ROW_FILENAME_SELECTOR)
            raw = str(name_node.inner_text() or "") if name_node is not None else ""
            filename = re.sub(r"(?i)^\s*file\s*name\s*:\s*", "", raw).strip()
            if not filename:
                # A nameless row cannot be classified as image-or-not, so it can
                # neither be name-checked nor scheduled for hashing. Treating it
                # as "not an image" would be the sampling hole this gate exists
                # to close, so it counts as unidentified and fails the run closed.
                found.append({"id": None, "filename": "", "stem": ""})
                continue
            found.append({
                "id": attachment_id,
                "filename": filename,
                "stem": _normalise_stem(filename),
            })
        return found

    def _library_total(self) -> int | None:
        nodes = self._page.query_selector_all(LIST_COUNT_SELECTOR)
        if len(nodes) != 1:
            return None
        matched = LIST_COUNT_PATTERN.search(str(nodes[0].inner_text() or ""))
        if not matched:
            return None
        return int(matched.group(1).replace(",", ""))

    def enumerate_library(self) -> dict[str, Any]:
        """Walk the list screen with explicit bounds and prove we reached the end.

        Completeness is proven against the list table's own item count. A loop
        that merely stopped is not evidence that a library was fully read, and
        this check exists so "no duplicate" can never mean "no duplicate in the
        part I got through".
        """
        rows: list[dict[str, Any]] = []
        seen: set[int] = set()
        total: int | None = None
        pages = 0
        unidentified = 0
        for page in range(1, MAX_LIBRARY_PAGES + 1):
            self._goto(library_page_url(page))
            pages = page
            if total is None:
                total = self._library_total()
            page_rows = self._row_records()
            if not page_rows:
                break
            fresh = 0
            for record in page_rows:
                if record["id"] is None:
                    unidentified += 1
                    continue
                if record["id"] in seen:
                    continue
                seen.add(record["id"])
                rows.append(record)
                fresh += 1
            if len(rows) > MAX_LIBRARY_ROWS:
                raise MediaUploadError(
                    f"REFUSED: the media library exceeds the {MAX_LIBRARY_ROWS}-row read "
                    "bound, so duplicate absence cannot be proven."
                )
            if fresh == 0:
                break
            if total is not None and len(rows) >= total:
                break
        complete = (total is not None and unidentified == 0 and len(rows) == total)
        return {"rows": rows, "total": total, "pages": pages, "complete": complete,
                "unidentified": unidentified}

    # -- attachment read-back ----------------------------------------------
    def read_attachment(self, attachment_id: int,
                        expected_basename: str | None = None,
                        allowed_extensions: tuple[str, ...] = PNG_ONLY) -> dict[str, Any]:
        """Open one attachment's own fixed screen and project safe identity only.

        No HTML, no page text beyond the two identity boxes, no cookie, no token.
        `allowed_extensions` stays PNG-only for the upload paths; the complete
        duplicate scan widens it to the raster formats it is willing to open.
        """
        self._goto(attachment_edit_url(attachment_id))
        urls: set[str] = set()
        for node in self._page.query_selector_all(ATTACHMENT_URL_SELECTOR):
            urls.add(str(node.input_value(timeout=ACTION_TIMEOUT_MS) or "").strip())
        for node in self._page.query_selector_all(ATTACHMENT_URL_FALLBACK_SELECTOR):
            urls.add(str(node.input_value(timeout=ACTION_TIMEOUT_MS) or "").strip())
        urls.discard("")
        if not urls:
            raise MediaUploadError(
                "REFUSED: the attachment screen exposes no public file URL, so the "
                "upload cannot be verified."
            )
        if len(urls) > 1:
            raise MediaUploadError(
                "REFUSED: the attachment screen exposes more than one file URL. The "
                "target is ambiguous."
            )
        source_url = urls.pop()
        basename = assert_public_upload_url(source_url, expected_basename=expected_basename,
                                            allowed_extensions=allowed_extensions)

        names = self._page.query_selector_all(ATTACHMENT_FILENAME_SELECTOR)
        if len(names) != 1:
            raise MediaUploadError(
                "REFUSED: the attachment screen does not state exactly one file name."
            )
        stated = re.sub(r"(?i)^\s*file\s*name\s*:\s*", "",
                        str(names[0].inner_text() or "")).strip()
        if stated != basename:
            raise MediaUploadError(
                "REFUSED: the attachment screen's file name and its file URL disagree."
            )
        types = self._page.query_selector_all(ATTACHMENT_FILETYPE_SELECTOR)
        if len(types) != 1:
            raise MediaUploadError(
                "REFUSED: the attachment screen does not state exactly one file type."
            )
        filetype = str(types[0].inner_text() or "").casefold()
        extension = _extension_of(basename)
        if not any(token in filetype for token in IMAGE_TYPE_TOKENS[extension]):
            raise MediaUploadError(
                "REFUSED: the attachment screen's stated file type does not match its "
                "own file name."
            )
        return {
            "attachment_id": int(attachment_id),
            "filename": basename,
            "source_url": source_url,
            "extension": extension,
            "filetype_matches_name": True,
        }

    # -- the one write route ------------------------------------------------
    def upload_one(self, path: Path, known_ids: set[int]) -> int:
        """Submit ONE fixed file through the standard browser uploader.

        Returns the id of the ONE newly created attachment. Exactly one file
        input and exactly one submit control must exist first, so a redesigned or
        unexpected screen refuses instead of clicking something else.
        """
        resolved = Path(path).resolve()
        allowed = {(GALLERY_DIR.resolve() / name) for name in FIXED_FILENAMES}
        if resolved not in allowed:
            raise MediaUploadError(
                "REFUSED: only the six fixed Packing Ring images may be selected."
            )
        self._goto(MEDIA_NEW_URL)
        inputs = self._page.query_selector_all(FILE_INPUT_SELECTOR)
        if len(inputs) != 1:
            raise MediaUploadError(
                "REFUSED: the browser uploader does not present exactly one file input. "
                "Nothing was selected."
            )
        submits = self._page.query_selector_all(SUBMIT_SELECTOR)
        if len(submits) != 1:
            raise MediaUploadError(
                "REFUSED: the browser uploader does not present exactly one upload "
                "control. Nothing was submitted."
            )
        inputs[0].set_input_files(str(resolved), timeout=ACTION_TIMEOUT_MS)
        self._assert_landed()
        submits[0].click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=UPLOAD_TIMEOUT_MS)
        self._assert_landed()
        return self._identify_upload(known_ids)

    def _identify_upload(self, known_ids: set[int]) -> int:
        """The new attachment id, from the result query or one new edit link.

        Both routes require a POSITIVE id that is UNIQUE on the page and ABSENT
        from everything the duplicate preflight already saw, so "the newest row"
        can never be mistaken for "the row we just created".
        """
        landed = self.url
        if (urlsplit(landed).path or "") != UPLOAD_LIST_PATH:
            raise MediaUploadError(
                "REFUSED: the upload did not land on the media list screen, so its "
                "result cannot be identified."
            )
        values = dict(_split_query(landed))
        if "posted" in values:
            attachment_id = _require_positive_int(values["posted"], "the upload result id")
            if attachment_id in known_ids:
                raise MediaUploadError(
                    "REFUSED: the upload result names an attachment that already existed."
                )
            return attachment_id
        fresh = {record["id"] for record in self._row_records()
                 if record["id"] is not None} - known_ids
        if len(fresh) != 1:
            raise MediaUploadError(
                f"REFUSED: the media list shows {len(fresh)} new attachments after one "
                "upload, so the result is ambiguous. Nothing further was uploaded."
            )
        return fresh.pop()


# ---------------------------------------------------------------------------
# Session. Patched wholesale in tests; there is no network in the test suite.
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def admin_session() -> Iterator[AdminPage]:
    """Attach to Rachad's already-authenticated loopback CDP session.

    This never launches a browser profile and never signs in. If the window is
    closed or has left the admin origin, it refuses rather than recovering.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    # Belt and braces: the commands are decorated, but any future caller that
    # reaches for the admin browser directly is serialized too. Re-entrant, so
    # nesting inside a decorated command costs nothing.
    with ui_browser_lock("wordpress", purpose="WordPress media session"), sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=15_000)
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            raise MediaUploadError(
                "The authenticated FRP Depot WordPress window is not reachable on the "
                "loopback session. Ask Rachad to run CONNECT_DADO_WORDPRESS_UI.bat and "
                "keep it open. Nothing was uploaded."
            ) from exc
        contexts = browser.contexts
        if not contexts or not contexts[0].pages:
            raise MediaUploadError("The authenticated WordPress window has no open page.")
        yield AdminPage(contexts[0].pages[0])


# ---------------------------------------------------------------------------
# Duplicate preflight
# ---------------------------------------------------------------------------
DUPLICATE_SCOPE = (
    "COMPLETE. Name gate: every enumerated attachment, by basename and by "
    "WordPress -N stem. Hash gate: EVERY enumerated image attachment "
    f"({', '.join(SCANNED_EXTENSIONS)}) opened on its own fixed attachment "
    "screen, its one public original URL downloaded anonymously within the byte "
    "bounds, and its full SHA-256 compared to all six fixed digests. No sampling "
    "and no windowing of any kind: an identical image stored under an unrelated "
    "OLDER filename IS proven absent. Any unidentifiable row, unsafe URL, failed or "
    "over-bound download, or unhashable body makes the whole check incomplete "
    "and refuses."
)


def _hash_every_image(admin: AdminPage, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Open, download and hash EVERY image attachment in the enumerated library.

    There is no sampling branch here on purpose. The scan either finishes every
    image row inside the hard bounds, or it reports itself incomplete and the
    caller refuses. The first row that cannot be identified, proven safe,
    downloaded within bounds or hashed stops the walk immediately: once the proof
    is broken, reading the rest cannot repair it, and the bounds exist to keep a
    refusal cheap.
    """
    targets = [row["id"] for row in rows
               if _extension_of(row["filename"]) in IMAGE_EXTENSIONS]
    if len(targets) > MAX_IMAGE_ATTACHMENTS:
        raise MediaUploadError(
            f"REFUSED: the media library holds {len(targets)} image attachments, "
            f"beyond the {MAX_IMAGE_ATTACHMENTS}-image hash bound. Duplicate absence "
            "cannot be proven completely, and this tool does not sample. Nothing was "
            "staged or uploaded."
        )
    conflicts: list[dict[str, Any]] = []
    completed = 0
    failures = 0
    total_bytes = 0
    for attachment_id in targets:
        try:
            detail = admin.read_attachment(attachment_id,
                                           allowed_extensions=SCANNED_EXTENSIONS)
            data = download_public_bytes(detail["source_url"],
                                         allowed_extensions=SCANNED_EXTENSIONS)
        except MediaUploadError:
            failures += 1
            break
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_DOWNLOAD_BYTES:
            raise MediaUploadError(
                f"REFUSED: hashing the media library passed the "
                f"{MAX_TOTAL_DOWNLOAD_BYTES}-byte read bound before it finished, so "
                "duplicate absence cannot be proven completely. This tool does not "
                "sample. Nothing was staged or uploaded."
            )
        completed += 1
        digest = hashlib.sha256(data).hexdigest()
        if digest in FIXED_HASHES:
            conflicts.append({
                "attachment_id": attachment_id,
                "matches_fixed_image": FIXED_HASHES[digest],
            })
    return {
        "image_rows": len(targets),
        "image_hashes_completed": completed,
        "hash_failures": failures,
        "hash_bytes_read": total_bytes,
        "hash_conflicts": conflicts,
        "complete": failures == 0 and completed == len(targets),
    }


def duplicate_preflight(admin: AdminPage,
                        walked: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bounded, read-only, COMPLETE proof that none of the six is in the library.

    Writes nothing, authenticates through no API, reads no page token. Both gates
    are complete or the evidence says so and the caller fails closed -- see the
    module docstring.

    `walked` lets a caller reuse ONE library walk for both this check and its own
    pre-upload id snapshot. Walking twice would be a second chance for the two
    snapshots to disagree, which is exactly the ambiguity the new-id rule exists
    to remove.
    """
    walked = walked if walked is not None else admin.enumerate_library()
    rows = walked["rows"]
    enumeration_complete = bool(walked["complete"])
    name_conflicts = [
        {"attachment_id": row["id"], "filename": row["filename"], "stem": row["stem"]}
        for row in rows
        if row["filename"] in FIXED_FILENAMES or row["stem"] in FIXED_STEMS
    ]

    if enumeration_complete:
        hashed = _hash_every_image(admin, rows)
    else:
        # The row set itself is unproven, so hashing it could not prove anything
        # about the library. Reported as not-run rather than as a clean result.
        hashed = {
            "image_rows": len([row for row in rows
                               if _extension_of(row["filename"]) in IMAGE_EXTENSIONS]),
            "image_hashes_completed": 0,
            "hash_failures": 0,
            "hash_bytes_read": 0,
            "hash_conflicts": [],
            "complete": False,
        }

    return {
        "method": "authenticated media library list screen, read-only",
        "scope": DUPLICATE_SCOPE,
        "checked_utc": utc_now().isoformat(),
        "library_total": walked["total"],
        "enumerated": len(rows),
        "pages_read": walked["pages"],
        "enumeration_complete": enumeration_complete,
        "image_rows": hashed["image_rows"],
        "image_hashes_completed": hashed["image_hashes_completed"],
        "hash_failures": hashed["hash_failures"],
        "hash_bytes_read": hashed["hash_bytes_read"],
        "hash_complete": bool(hashed["complete"]),
        "complete": enumeration_complete and bool(hashed["complete"]),
        "name_conflicts": name_conflicts,
        "hash_conflicts": hashed["hash_conflicts"],
    }


def require_no_duplicates(evidence: dict[str, Any]) -> None:
    """The single refusal point. Every total is re-checked, not just `complete`.

    `complete` is a claim; the counts beside it are the proof. They are compared
    here so a flag that disagrees with its own arithmetic can never pass.
    """
    if evidence.get("enumeration_complete") is not True:
        raise MediaUploadError(
            "REFUSED: the Media Library could not be read completely, so duplicate "
            "absence cannot be proven. Failing closed; nothing was uploaded."
        )
    if (evidence.get("hash_complete") is not True
            or evidence.get("hash_failures") != 0
            or evidence.get("image_hashes_completed") != evidence.get("image_rows")):
        raise MediaUploadError(
            "REFUSED: not every image in the Media Library could be downloaded and "
            "hashed, so it is not proven that none of the six approved images is "
            "already on the site under another name. This tool does not sample and "
            "does not guess. Failing closed; nothing was uploaded."
        )
    if evidence.get("complete") is not True:
        raise MediaUploadError(
            "REFUSED: the duplicate evidence does not declare itself complete. "
            "Failing closed; nothing was uploaded."
        )
    if evidence.get("name_conflicts"):
        raise DuplicateFound(
            "REFUSED: the WordPress Media Library already holds a file with one of "
            "the six fixed names (or WordPress's own -N variant of one). Nothing was "
            "uploaded. Reconcile the existing media before staging anything new."
        )
    if evidence.get("hash_conflicts"):
        raise DuplicateFound(
            "REFUSED: the WordPress Media Library already holds a file whose SHA-256 "
            "is one of the six approved images. Nothing was uploaded."
        )


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------
def lock_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".commit-lock.json")


def result_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".result.json")


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise MediaUploadError(
            "This plan has already entered commit and cannot be replayed."
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def resolve_plan_path(raw: str) -> Path:
    """Only inside the one dedicated plan folder, with no symlink escape."""
    candidate = Path(str(raw))
    if _is_reparse_point(candidate):
        raise MediaUploadError("REFUSED: the plan path is a symlink or reparse point.")
    plan_path = candidate.resolve()
    if PLAN_DIR.resolve() not in plan_path.parents:
        raise MediaUploadError(
            "REFUSED: the plan must be inside Dado's Packing Ring media-plan folder."
        )
    return plan_path


def stage_plan(integrity: list[dict[str, Any]], manifest: dict[str, Any],
               duplicate: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "action": ACTION,
        "origin": EXACT_ORIGIN,
        "cdp_endpoint": CDP_ENDPOINT,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "approval_word": APPROVAL_WORD,
        "risk": RISK_DISCLOSURE,
        "provenance": IMAGE_PROVENANCE,
        "images": plan_image_records(),
        "local_integrity": list(integrity),
        "manifest": dict(manifest),
        "duplicate_check": dict(duplicate),
        "upload_contract": dict(UPLOAD_CONTRACT),
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{ACTION}_{digest[:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_receipt("packing_ring_media_plan_staged",
                   f"plan={path}; sha256={digest}; uploads=0; website_writes=0")
    return path, plan


def _check_closed_records(records: Any, keys: frozenset[str], label: str) -> None:
    if not isinstance(records, list):
        raise MediaUploadError(f"REFUSED: the plan's {label} is not a list.")
    for record in records:
        if not isinstance(record, dict) or set(record) != keys:
            raise MediaUploadError(
                f"REFUSED: a plan {label} record is not the closed projection."
            )


def load_plan(path: str | Path) -> dict[str, Any]:
    """Load, hash-check and identity-check one plan. No page and no network."""
    plan_path = Path(path)
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MediaUploadError(f"REFUSED: the plan JSON is unreadable: {plan_path}") from exc
    if not isinstance(plan, dict):
        raise MediaUploadError("REFUSED: a plan must contain one object.")
    saved = str(plan.pop("sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(plan)):
        raise MediaUploadError("REFUSED: plan hash check failed. The plan changed after review.")
    if set(plan) != PLAN_KEYS:
        raise MediaUploadError("REFUSED: the plan schema is not the exact closed set of fields.")
    if (plan["schema_version"] != SCHEMA_VERSION or plan["tool"] != TOOL_NAME
            or plan["tool_version"] != TOOL_VERSION or plan["action"] != ACTION):
        raise MediaUploadError(
            "REFUSED: the plan was not produced by this exact tool version and action. "
            "Stage a new plan."
        )
    if plan["origin"] != EXACT_ORIGIN or plan["cdp_endpoint"] != CDP_ENDPOINT:
        raise MediaUploadError("REFUSED: the plan names a different site origin or browser.")
    if plan["approval_word"] != APPROVAL_WORD:
        raise MediaUploadError("REFUSED: the plan names a different approval word.")
    if plan["risk"] != RISK_DISCLOSURE or plan["provenance"] != IMAGE_PROVENANCE:
        raise MediaUploadError(
            "REFUSED: the plan does not carry this tool's exact risk and provenance "
            "disclosure."
        )
    if plan["upload_contract"] != UPLOAD_CONTRACT:
        raise MediaUploadError("REFUSED: the plan's upload contract is not the current fixed contract.")
    nonce = plan["nonce"]
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise MediaUploadError("REFUSED: the plan nonce is not a 32-character random token.")

    _check_closed_records(plan["images"], PLAN_IMAGE_KEYS, "image")
    if plan["images"] != plan_image_records():
        raise MediaUploadError(
            "REFUSED: the plan's six image records are not the six fixed Packing Ring "
            "images, in the fixed order, byte-for-byte."
        )
    _check_closed_records(plan["local_integrity"], INTEGRITY_KEYS, "local integrity")
    if len(plan["local_integrity"]) != len(FIXED_IMAGES):
        raise MediaUploadError("REFUSED: the plan does not carry integrity evidence for all six images.")
    manifest = plan["manifest"]
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise MediaUploadError("REFUSED: the plan's manifest evidence is not the closed projection.")
    if manifest["qc_all_approved"] is not True:
        raise MediaUploadError("REFUSED: the plan's manifest evidence does not record QC approval.")
    duplicate = plan["duplicate_check"]
    if not isinstance(duplicate, dict) or set(duplicate) != DUPLICATE_KEYS:
        raise MediaUploadError("REFUSED: the plan's duplicate evidence is not the closed projection.")
    if duplicate["scope"] != DUPLICATE_SCOPE:
        raise MediaUploadError(
            "REFUSED: the plan's duplicate evidence does not carry this build's exact "
            "COMPLETE-gate scope statement. A plan staged under a bounded or sampled "
            "duplicate check is not committable."
        )
    require_no_duplicates(duplicate)
    if duplicate["name_conflicts"] or duplicate["hash_conflicts"]:
        raise MediaUploadError(
            "REFUSED: the plan was staged without a complete, conflict-free duplicate check."
        )

    try:
        created = datetime.fromisoformat(str(plan["created_utc"]))
        expires = datetime.fromisoformat(str(plan["expires_utc"]))
    except (TypeError, ValueError) as exc:
        raise MediaUploadError("REFUSED: the plan carries an invalid timestamp.") from exc
    if expires != created + timedelta(hours=PLAN_LIFETIME_HOURS):
        raise MediaUploadError("REFUSED: the plan expiry does not match its own creation time.")
    if utc_now() >= expires:
        raise MediaUploadError("REFUSED: the plan expired. Stage a new plan for review.")
    plan["sha256"] = saved
    return plan


def record_result(plan_path: Path, plan: dict[str, Any], status: str,
                  detail: dict[str, Any]) -> None:
    payload = {
        "status": status,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "action": ACTION,
        "plan": str(plan_path),
        "plan_sha256": plan["sha256"],
        "recorded_utc": utc_now().isoformat(),
        **detail,
    }
    result_path(plan_path).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
@holds_wordpress_browser("WordPress: read-only duplicate check for the six Packing Ring images")
def command_stage(_: argparse.Namespace) -> None:
    """Verify everything locally, prove the library is clean, write ONE plan.

    Zero website writes: this navigates read-only admin list and attachment
    screens and downloads public files anonymously. It clicks no form, selects no
    file and submits nothing.
    """
    integrity = verify_local_files()
    manifest = verify_manifest()
    with admin_session() as admin:
        duplicate = duplicate_preflight(admin)
        admin.leave_on_media_list()
    require_no_duplicates(duplicate)
    path, plan = stage_plan(integrity, manifest, duplicate)
    emit({
        "status": "STAGED_NOT_COMMITTED",
        "message": "STAGED ONLY - ZERO WEBSITE WRITES",
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "action": ACTION,
        "images": plan["images"],
        "manifest": plan["manifest"],
        "duplicate_check": plan["duplicate_check"],
        "risk": RISK_DISCLOSURE,
        "provenance": IMAGE_PROVENANCE,
        "approval": APPROVAL_WORD,
        "uploads": 0,
        "website_writes": 0,
        "products_changed": 0,
        "emails": 0,
    })
    print("STAGED ONLY - ZERO WEBSITE WRITES")


def _burn(lock: Path, plan: dict[str, Any], plan_path: Path, stage: str,
          exc: Exception | None, uploaded: list[dict[str, Any]]) -> None:
    """Close the plan permanently, naming the last safe step and what did land."""
    detail = {
        "stage": stage,
        "reason": type(exc).__name__ if exc is not None else "verification_failed",
        "uploaded_verified": list(uploaded),
        "uploads_completed": len(uploaded),
        "uploads_remaining": len(FIXED_IMAGES) - len(uploaded),
        "no_retry": True,
        "rollback_performed": False,
        "delete_performed": False,
        "products_changed": 0,
        "emails": 0,
    }
    write_lock(lock, {
        "plan_sha256": plan["sha256"], "status": "indeterminate",
        "updated_utc": utc_now().isoformat(), **detail,
    })
    record_result(plan_path, plan, "INDETERMINATE", detail)
    append_receipt(
        "packing_ring_media_indeterminate_no_retry",
        f"plan={plan_path}; sha256={plan['sha256']}; stage={stage}; "
        f"verified_uploads={len(uploaded)}; retry=false",
    )


@holds_wordpress_browser("WordPress: upload the six fixed Packing Ring images")
def command_commit(args: argparse.Namespace) -> None:
    """One approved plan, six independent uploads, one attempt, no retry.

    The order below is the safety property, not a style choice. The browser mutex
    is already held by the decorator; then the plan is resolved and verified, then
    the approval word is checked, then the local files and the library are
    re-proven -- and only after all of that is the permanent attempt lock created,
    immediately before the first file is selected. Everything that can refuse
    refuses while the plan is still usable.
    """
    plan_path = resolve_plan_path(args.plan)
    plan = load_plan(plan_path)
    require_rachad_approval(args.approval)
    lock = lock_path(plan_path)
    if lock.exists():
        raise MediaUploadError(
            "REFUSED: this plan has already entered commit and cannot be replayed."
        )

    # Re-proven on the live disk, not trusted from the plan.
    integrity = verify_local_files()
    if integrity != plan["local_integrity"]:
        raise MediaUploadError(
            "REFUSED: the approved images on disk no longer match the evidence in this "
            "plan. Nothing was uploaded."
        )
    manifest = verify_manifest()
    if manifest != plan["manifest"]:
        raise MediaUploadError(
            "REFUSED: the approved gallery manifest changed after this plan was staged. "
            "Nothing was uploaded."
        )

    uploaded: list[dict[str, Any]] = []
    with admin_session() as admin:
        walked = admin.enumerate_library()
        duplicate = duplicate_preflight(admin, walked=walked)
        require_no_duplicates(duplicate)
        known_ids = {record["id"] for record in walked["rows"]}

        # The permanent one-attempt lock: created AFTER every refusal path above
        # and STRICTLY BEFORE the first file selection.
        write_lock(lock, {
            "plan_sha256": plan["sha256"], "status": "acting",
            "started_utc": utc_now().isoformat(), "stage": "upload_1",
            "uploaded_verified": [], "no_retry": True,
        }, exclusive=True)

        for record, evidence in zip(FIXED_IMAGES, integrity):
            position = record["position"]
            try:
                attachment_id = admin.upload_one(Path(evidence["path"]), known_ids)
                known_ids.add(attachment_id)
                detail = admin.read_attachment(attachment_id,
                                               expected_basename=record["filename"])
                data = download_public_bytes(detail["source_url"],
                                             expected_basename=record["filename"])
                digest = hashlib.sha256(data).hexdigest()
                if not secrets.compare_digest(digest, record["sha256"]):
                    raise MediaUploadError(
                        f"REFUSED: the public copy of {record['filename']} does not hash "
                        "to the approved image."
                    )
                if len(data) != record["bytes"]:
                    raise MediaUploadError(
                        f"REFUSED: the public copy of {record['filename']} is not the "
                        "approved byte size."
                    )
            except Exception as exc:  # noqa: BLE001 - attributed, locked, re-raised
                _burn(lock, plan, plan_path, f"upload_{position}", exc, uploaded)
                admin.leave_on_media_list()
                raise IndeterminateError(
                    f"Upload {position} of {len(FIXED_IMAGES)} "
                    f"({record['filename']}) could not be completed and verified. "
                    f"{len(uploaded)} earlier upload(s) remain live in the Media "
                    "Library, the remaining files were NOT uploaded, and this plan is "
                    "permanently locked with no retry. Nothing was deleted or rolled "
                    "back. Check the WordPress Media Library before staging anything new."
                ) from exc
            uploaded.append({
                "position": position,
                "filename": record["filename"],
                "sha256": record["sha256"],
                "attachment_id": attachment_id,
                "source_url": detail["source_url"],
                "bytes": record["bytes"],
                "verified_utc": utc_now().isoformat(),
            })
            write_lock(lock, {
                "plan_sha256": plan["sha256"], "status": "acting",
                "updated_utc": utc_now().isoformat(),
                "stage": f"uploaded_{position}",
                "uploaded_verified": list(uploaded), "no_retry": True,
            })

        # One fresh read-only pass over all six, after every upload has landed.
        try:
            confirmed = _reverify_all(admin, uploaded)
        except Exception as exc:  # noqa: BLE001 - attributed, locked, re-raised
            _burn(lock, plan, plan_path, "final_verification", exc, uploaded)
            admin.leave_on_media_list()
            raise IndeterminateError(
                "All six uploads landed but the final fresh verification could not be "
                "completed, so the result is unverified. This plan is permanently locked "
                "with no retry and nothing was deleted."
            ) from exc
        admin.leave_on_media_list()

    for record in confirmed:
        if set(record) != RESULT_KEYS:  # pragma: no cover - constructed above
            raise MediaUploadError("Internal: a result record is not the closed projection.")
    write_lock(lock, {
        "plan_sha256": plan["sha256"], "status": "committed_verified",
        "updated_utc": utc_now().isoformat(), "uploaded_verified": list(confirmed),
        "no_retry": True,
    })
    detail = {
        "uploaded_verified": list(confirmed),
        "uploads_completed": len(confirmed),
        "attachment_ids": [item["attachment_id"] for item in confirmed],
        "sha256_to_attachment": {item["sha256"]: item["attachment_id"] for item in confirmed},
        "products_changed": 0,
        "variations_changed": 0,
        "emails": 0,
        "deleted": 0,
        "retried": 0,
        "no_retry": True,
    }
    record_result(plan_path, plan, "COMMITTED_AND_VERIFIED", detail)
    append_receipt(
        "packing_ring_media_uploads_committed",
        f"plan={plan_path}; sha256={plan['sha256']}; uploads={len(confirmed)}; "
        f"attachment_ids={[item['attachment_id'] for item in confirmed]}; "
        "products_changed=0; emails=0",
    )
    append_receipt(
        "packing_ring_media_result_verified",
        f"plan={plan_path}; result={result_path(plan_path)}; verified={len(confirmed)}",
    )
    emit({
        "status": "COMMITTED_AND_VERIFIED",
        "action": ACTION,
        "plan": str(plan_path),
        "plan_sha256": plan["sha256"],
        "result_manifest": str(result_path(plan_path)),
        "uploaded": confirmed,
        "uploads": len(confirmed),
        "products_changed": 0,
        "variations_changed": 0,
        "emails": 0,
        "deleted": 0,
        "replay_locked": True,
    })


def _reverify_all(admin: AdminPage, uploaded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One fresh read of every attachment: same id, same URL, same bytes."""
    confirmed: list[dict[str, Any]] = []
    for item in uploaded:
        detail = admin.read_attachment(item["attachment_id"],
                                       expected_basename=item["filename"])
        if detail["source_url"] != item["source_url"]:
            raise MediaUploadError(
                f"REFUSED: {item['filename']} now reports a different public URL."
            )
        data = download_public_bytes(detail["source_url"],
                                     expected_basename=item["filename"])
        digest = hashlib.sha256(data).hexdigest()
        if not secrets.compare_digest(digest, item["sha256"]):
            raise MediaUploadError(
                f"REFUSED: the final check of {item['filename']} does not hash to the "
                "approved image."
            )
        confirmed.append({**item, "verified_utc": utc_now().isoformat()})
    ids = [item["attachment_id"] for item in confirmed]
    if len(set(ids)) != len(FIXED_IMAGES):
        raise MediaUploadError("REFUSED: the six uploads did not produce six distinct attachments.")
    return confirmed


def build_parser() -> argparse.ArgumentParser:
    """Exactly two commands. Commit takes a plan path and an approval word only."""
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
    except (MediaUploadError, OSError, ValueError, UiLaneBusy, UiLaneLockError) as exc:
        # UiLaneBusy is a clean refusal, not a crash: the other lane holds the
        # shared WordPress window, no plan was locked and nothing was uploaded.
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
