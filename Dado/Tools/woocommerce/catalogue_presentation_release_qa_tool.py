#!/usr/bin/env python
"""Permanent read-only release QA for FRP Depot's catalogue presentation.

Two commands exist:

* ``audit`` performs fixed anonymous public checks, captures screenshots, and
  fresh-reads the fixed plugin row in a new tab of the authenticated WordPress
  browser while holding the shared browser mutex. It has no website write route.
* ``finalize`` accepts an explicit pixel-review manifest for every captured
  screenshot and writes one immutable reviewed result.

Automation can pass while the release remains incomplete: pixel review is a
separate required evidence layer. This module has no POST/PUT/PATCH/DELETE,
upload, form-fill, activation, deactivation, settings, product, order, customer,
mail or generic admin action.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any, Iterable
from urllib.parse import urlsplit

TOOL_NAME = "catalogue_presentation_release_qa_tool"
TOOL_VERSION = "1.1.1"
SCHEMA_VERSION = 1
EXACT_ORIGIN = "https://frpdepots.com"
ALLOWED_HOST = "frpdepots.com"
ROOT = Path(r"C:\FRPDepot")
WORK = ROOT / "Dado" / "20_Working"
RUN_ROOT = WORK / "catalogue_release_qa_runs"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
RUNTIME_TEMP = WORK / "catalogue_release_qa_runtime_temp"
COMMON = ROOT / "Dado" / "Tools" / "common"
CDP_ENDPOINT = "http://127.0.0.1:9229"
PLUGINS_URL = f"{EXACT_ORIGIN}/wp-admin/plugins.php"
PLUGIN_FILE = "frpdepot-automatic-catalogue-presentation/frpdepot-automatic-catalogue-presentation.php"
PLUGIN_VERSION = "1.1.1"
PLUGIN_ROW_SELECTOR = f'tr[data-plugin="{PLUGIN_FILE}"]:not(.plugin-update-tr)'
PLUGIN_UPDATE_SELECTOR = f'tr.plugin-update-tr[data-plugin="{PLUGIN_FILE}"]'
ACTIVATE_SELECTOR = ".row-actions .activate a"
DEACTIVATE_SELECTOR = ".row-actions .deactivate a"
VERSION_SELECTOR = ".plugin-version-author-uri"
VERSION_PATTERN = re.compile(r"(?i)\bversion\s+([0-9][0-9A-Za-z.\-+_]*)")

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
    "stub_flanges": {"filename": "FRP_Depots_Stub_Flanges_2026.pdf", "bytes": 1310780,
                     "sha256": "f009764259b11136a3f7126de6a678773e0e4ed21293cd9e95a71c0f0a4cd4b6"},
    "manways_and_covers": {"filename": "FRP_Depots_Manways_and_Covers_2026.pdf", "bytes": 1503479,
                            "sha256": "09b8bc59a2d81fdff4c3d4ad7110f3fac27752c0f03d162ee24165b5e4ed3e65"},
    "elbows_90": {"filename": "FRP_Depots_90_Degree_Elbows_2026.pdf", "bytes": 4558184,
                  "sha256": "ead009c50b7f8cc338b80928084c6ef24141477e5addea7806a4b7da6547fcb2"},
    "filament_wound_pipe": {"filename": "FRP_Depots_Filament_Wound_Pipe_2026.pdf", "bytes": 477528,
                            "sha256": "f4fbaf8cb72e7b41f22ddb170185595e12d109394c136edde79f902dd2f65fc2"},
    "fnpt_couplings": {"filename": "FRP_Depots_FNPT_Couplings_2026.pdf", "bytes": 2001467,
                       "sha256": "19efa8d20a1be17a1451ad24f6b1d45c2f1e53b3fb58cece5aed496acc91db33"},
}
SECTION_PDF_URLS = {
    key: f"{EXACT_ORIGIN}/wp-content/plugins/frpdepot-automatic-catalogue-presentation/"
         f"catalogue-sections/{row['filename']}"
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
DERAKANE_PAGE_URL = f"{EXACT_ORIGIN}/derakane-resin-resistance-search/"
DERAKANE_REST_URL = f"{EXACT_ORIGIN}/wp-json/frpdepot-derakane/v1/search"
FNPT_STORE_API_URL = f"{EXACT_ORIGIN}/wp-json/wc/store/v1/products/2061"
HETRON_ATTACHMENT_URL = f"{EXACT_ORIGIN}/hetron-cr-guide-2007_ineos/"
HETRON_ATTACHMENT_QUERY_URL = f"{EXACT_ORIGIN}/?attachment_id=1832"
HETRON_DIRECT_PDF_URL = (
    f"{EXACT_ORIGIN}/wp-content/uploads/2026/03/HETRON-CR-Guide-2007_Ineos.pdf"
)
HETRON_URL = HETRON_ATTACHMENT_URL
DERAKANE_OLD_URL = f"{EXACT_ORIGIN}/derakane-resin-selection-guide/"
DERAKANE_NEW_URL = DERAKANE_PAGE_URL
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

PUBLIC_PAGES = [
    ("home", f"{EXACT_ORIGIN}/"),
    ("shop_all", f"{EXACT_ORIGIN}/products/"),
    *[(f"category_{category_id}", CATEGORY_URLS[category_id]) for category_id in sorted(CATEGORY_URLS)],
    *[(f"product_{product_id}", PRODUCT_URLS[product_id]) for product_id in sorted(PRODUCT_URLS)],
    ("derakane_v2", DERAKANE_PAGE_URL),
]
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 1000},
    "mobile": {"width": 390, "height": 844},
}
ALLOWED_PUBLIC_PATHS = frozenset(
    {urlsplit(url).path or "/" for _, url in PUBLIC_PAGES}
    | {
        urlsplit(FNPT_STORE_API_URL).path,
        urlsplit(DERAKANE_REST_URL).path,
        urlsplit(HETRON_ATTACHMENT_URL).path,
        urlsplit(HETRON_DIRECT_PDF_URL).path,
        *(urlsplit(url).path for url in SECTION_PDF_URLS.values()),
    }
)
MIN_BODY_CHARS = 40
MAX_OVERFLOW_PX = 2
NAV_TIMEOUT_MS = 60_000
WAIT_AFTER_LOAD_MS = 1_000

FATAL_TEXT = (
    "there has been a critical error on this website",
    "fatal error",
    "parse error",
    "call to undefined function",
)
VISIBLE_ERROR_PATTERNS = (
    re.compile(r"(?i)\b(?:php\s+)?(?:warning|notice|deprecated|fatal error|parse error):\s"),
    re.compile(r"(?i)\bundefined\s+(?:property|variable|index|array key|offset)\b"),
    re.compile(r"(?i)\buncaught\s+(?:error|exception)\b"),
    re.compile(r"(?i)\bstack\s+trace\b"),
)
SERVER_PATH_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:\\(?:[^\\\r\n<>:\"|?*]+\\)+[^\\\r\n<>:\"|?*]+\.php(?:\s+on\s+line\s+\d+)?"),
    re.compile(r"(?i)/(?:home|var/www|srv|opt|mnt|usr/local)/[^\s<>\"']+\.php(?:\s+on\s+line\s+\d+)?"),
    re.compile(r"(?i)wp-(?:content|includes)/[^\s<>\"']+\.php\s+on\s+line\s+\d+"),
)

REPORT_NAME = "automated_report.json"
REVIEW_NAME = "pixel_review.json"
FINAL_NAME = "final_reviewed_result.json"
REPORT_KEYS = frozenset({
    "schema_version", "tool", "tool_version", "run_id", "started_utc", "finished_utc",
    "origin", "read_only", "plugin_state", "pages", "functional", "contact_sheets",
    "summary", "pixel_review_required", "automated_report_sha256",
})
REVIEW_KEYS = frozenset({
    "schema_version", "automated_report_sha256", "reviewed_utc", "reviewer",
    "screenshots", "overall", "notes",
})
REVIEW_SCREENSHOT_KEYS = frozenset({"path", "sha256", "result", "notes"})


class QaError(RuntimeError):
    """A read-only QA refusal or detected release failure."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": utc_now().isoformat(), "action": action, "evidence": evidence}
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n")


def ensure_runtime_temp() -> None:
    names = ("TMP", "TEMP", "TMPDIR")
    if not all(os.environ.get(name) and Path(os.environ[name]).is_dir() for name in names):
        RUNTIME_TEMP.mkdir(parents=True, exist_ok=True)
        stable = str(RUNTIME_TEMP.resolve())
        for name in names:
            os.environ[name] = stable
    if not all(Path(os.environ[name]).is_dir() for name in names):
        raise QaError("Fixed Playwright runtime temp is unavailable.")


def assert_frp_url(url: str, *, admin: bool = False) -> None:
    parsed = urlsplit(str(url or ""))
    try:
        port = parsed.port
    except ValueError as exc:
        raise QaError("Invalid URL port.") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != ALLOWED_HOST
        or port not in (None, 443)
        or parsed.username
        or parsed.password
    ):
        raise QaError("URL is outside the fixed FRP Depot HTTPS origin.")
    path = parsed.path or "/"
    if admin:
        if path != "/wp-admin/plugins.php":
            raise QaError("Admin read is outside the fixed Plugins page.")
    elif path not in ALLOWED_PUBLIC_PATHS:
        raise QaError("Public read is outside the fixed release-QA routes.")


def cache_busted(url: str, token: str) -> str:
    assert_frp_url(url)
    separator = "&" if urlsplit(url).query else "?"
    return f"{url}{separator}_frp_release_qa={token}"


def new_run_dir() -> tuple[str, Path]:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    run_id = now.strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(4)
    path = RUN_ROOT / run_id
    path.mkdir(parents=False, exist_ok=False)
    return run_id, path


def resolve_report_path(raw: str) -> Path:
    path = Path(raw).resolve()
    if path.name != REPORT_NAME or path.parent.parent != RUN_ROOT.resolve():
        raise QaError("Report must be one fixed automated report under the QA run root.")
    return path


def resolve_review_path(raw: str, report_path: Path) -> Path:
    path = Path(raw).resolve()
    if path.name != REVIEW_NAME or path.parent != report_path.parent:
        raise QaError("Pixel review must be pixel_review.json beside its automated report.")
    return path


def exclusive_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError as exc:
        raise QaError(f"Immutable evidence already exists: {path}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")[:80]


def scan_runtime_disclosures(text: str) -> dict[str, list[str]]:
    folded = text.casefold()
    fatal = sorted({marker for marker in FATAL_TEXT if marker in folded})
    runtime = sorted({
        match.group(0).strip()
        for pattern in VISIBLE_ERROR_PATTERNS
        for match in pattern.finditer(text)
    })
    paths = sorted({
        match.group(0).strip()
        for pattern in SERVER_PATH_PATTERNS
        for match in pattern.finditer(text)
    })
    return {"fatal_markers": fatal, "runtime_error_markers": runtime, "server_paths": paths}


def page_metrics(page: Any) -> dict[str, Any]:
    return page.evaluate(
        r"""() => {
            const root = document.documentElement;
            const bodyText = (document.body?.innerText || '').replace(/\s+/g, ' ').trim();
            const visible = el => {
                const s = getComputedStyle(el), r = el.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
            };
            return {
                title: document.title,
                h1: [...document.querySelectorAll('h1')].filter(visible)
                    .map(h => (h.textContent || '').replace(/\s+/g, ' ').trim()),
                body_text_chars: bodyText.length,
                horizontal_overflow_px: Math.max(0, root.scrollWidth - root.clientWidth),
                document_width: root.scrollWidth,
                viewport_width: root.clientWidth,
                document_height: root.scrollHeight,
                broken_images: [...document.images]
                    .filter(img => img.complete && img.naturalWidth === 0)
                    .map(img => ({src: img.currentSrc || img.src || '', alt: img.alt || ''})),
                empty_headings: [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')]
                    .filter(visible).filter(h => !(h.textContent || '').trim())
                    .map(h => h.outerHTML.slice(0, 300)),
                placeholder_links: [...document.querySelectorAll('a[href]')]
                    .filter(visible)
                    .map(a => ({text:(a.textContent || '').replace(/\s+/g,' ').trim(),
                                href:a.getAttribute('href'), role:a.getAttribute('role') || ''}))
                    .filter(x => !x.href || x.href === '#' || /^javascript:/i.test(x.href)),
                hetron_text_count: (bodyText.match(/hetron/gi) || []).length
            };
        }"""
    )


def _class_id(element: Any, prefix: str) -> int | None:
    matches = [
        token[len(prefix):]
        for token in str(element.get_attribute("class") or "").split()
        if token.startswith(prefix) and token[len(prefix):].isdigit()
    ]
    return int(matches[0]) if len(matches) == 1 else None


def menu_projection(page: Any, selector: str) -> dict[str, Any]:
    roots = page.query_selector_all(selector)
    if len(roots) != 1:
        return {
            "present": False, "shop_roots": [], "categories": [], "products": [],
            "grouping": [], "order_fingerprint": "", "deduplicated": False,
            "fully_grouped": False, "nonempty_groups": False,
        }
    root = roots[0]
    shop_roots = []
    for link in root.query_selector_all("li > a"):
        href = str(link.get_attribute("href") or "")
        if (urlsplit(href).path or "") == SHOP_PATH:
            shop_roots.append({
                "title": str(link.text_content() or "").strip(), "path": SHOP_PATH,
            })
    category_items = root.query_selector_all(".frpdepot-acp-category-item")
    product_items = root.query_selector_all(".frpdepot-acp-product-item")
    categories = [_class_id(item, "frpdepot-acp-category-") for item in category_items]
    products = [_class_id(item, "frpdepot-acp-product-") for item in product_items]
    parents = [_class_id(item, "frpdepot-acp-category-parent-") for item in product_items]
    valid = None not in categories and None not in products and None not in parents
    grouping: list[list[Any]] = []
    if valid:
        grouping = [
            [category_id, [product_id for product_id, parent in zip(products, parents)
                           if parent == category_id]]
            for category_id in categories
        ]
    grouped = [product_id for _, group in grouping for product_id in group]
    order = {"categories": categories, "products": products, "grouping": grouping}
    return {
        "present": True,
        "shop_roots": shop_roots,
        "categories": categories,
        "products": products,
        "grouping": grouping,
        "order_fingerprint": digest_for(order),
        "deduplicated": valid and len(categories) == len(set(categories))
        and len(products) == len(set(products)),
        "fully_grouped": valid and grouped == products,
        "nonempty_groups": valid and all(group for _, group in grouping),
    }


def projection_matches(projection: dict[str, Any], reference: dict[str, Any]) -> bool:
    return bool(
        projection["present"]
        and projection["deduplicated"]
        and projection["fully_grouped"]
        and projection["nonempty_groups"]
        and EXPECTED_CATEGORY_IDS.issubset(projection["categories"])
        and EXPECTED_PRODUCT_IDS.issubset(projection["products"])
        and projection["order_fingerprint"] == reference["order_fingerprint"]
        and projection["categories"] == reference["categories"]
        and projection["products"] == reference["products"]
        and projection["grouping"] == reference["grouping"]
    )


def request_get(request: Any, url: str, *, max_redirects: int = 20) -> dict[str, Any]:
    assert_frp_url(url)
    options: dict[str, Any] = {
        "headers": {
            "Accept": "application/json,text/html,application/pdf;q=0.9,*/*;q=0.1",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        "timeout": NAV_TIMEOUT_MS,
        "fail_on_status_code": False,
    }
    if max_redirects == 0:
        options["max_redirects"] = 0
    response = request.get(url, **options)
    payload: Any = None
    try:
        payload = response.json()
    except Exception:
        pass
    body = response.body()
    return {
        "status": response.status,
        "headers": dict(response.headers),
        "body_bytes": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "pdf_signature": body.startswith(b"%PDF-"),
        "payload": payload,
    }


def goto_healthy(page: Any, url: str, token: str) -> dict[str, Any]:
    assert_frp_url(url)
    response = page.goto(cache_busted(url, token), wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    page.wait_for_timeout(WAIT_AFTER_LOAD_MS)
    body = str(page.inner_text("body") or "")
    html = str(page.content() or "")
    scan = scan_runtime_disclosures(body + "\n" + html)
    return {
        "status": response.status if response else None,
        "body_text_chars": len(body.strip()),
        **scan,
        "passed": bool(
            response
            and response.status == 200
            and (urlsplit(page.url).path or "/") == (urlsplit(url).path or "/")
            and len(body.strip()) >= MIN_BODY_CHARS
            and not any(scan.values())
        ),
    }


def guide_findings(page: Any, product_id: int, token: str) -> dict[str, Any]:
    url = PRODUCT_URLS[product_id]
    section_key = PRODUCT_SECTION_KEYS[product_id]
    section_url = SECTION_PDF_URLS[section_key]
    health = goto_healthy(page, url, token)
    html = str(page.content() or "")
    counts = {
        "hetron_text_count": html.casefold().count("hetron"),
        "hetron_url_count": html.count(HETRON_URL),
        "hetron_card_count": len(page.query_selector_all(
            ".et_pb_blurb_1_tb_body h4.et_pb_module_header")),
        "derakane_old_url_count": html.count(DERAKANE_OLD_URL),
        "derakane_new_card_url_count": html.count(DERAKANE_NEW_URL),
        "derakane_card_count": len(page.query_selector_all(
            ".et_pb_blurb_0_tb_body h4.et_pb_module_header")),
        "inline_cta_count": len(page.query_selector_all(
            f'.et_pb_text_1_tb_body a[href="{INLINE_CTA_PATH}"]')),
        "full_catalogue_url_count": html.count(FULL_CATALOGUE_URL),
        "section_catalogue_url_count": html.count(section_url),
        "section_catalogue_card_count": len(page.query_selector_all(
            ".et_pb_blurb_2_tb_body h4.et_pb_module_header")),
    }
    expected = {
        "hetron_text_count": 0,
        "hetron_url_count": 0,
        "hetron_card_count": 0,
        "derakane_old_url_count": 0,
        "derakane_new_card_url_count": 1,
        "derakane_card_count": 1,
        "inline_cta_count": 1,
        "full_catalogue_url_count": 0,
        "section_catalogue_url_count": 1,
        "section_catalogue_card_count": 1,
    }
    return {
        "product_id": product_id, "url": url, "section_key": section_key,
        "section_url": section_url, "health": health, **counts,
        "passed": health["passed"] and counts == expected,
    }


def category_catalogue_findings(page: Any, category_id: int, token: str) -> dict[str, Any]:
    url = CATEGORY_URLS[category_id]
    health = goto_healthy(page, url, token)
    html = str(page.content() or "")
    panels = page.query_selector_all(".frpdepot-acp-section-catalogues")
    links = page.query_selector_all(".frpdepot-acp-section-catalogue-link")
    hrefs = [str(link.get_attribute("href") or "") for link in links]
    targets = [str(link.get_attribute("target") or "") for link in links]
    rels = [str(link.get_attribute("rel") or "").split() for link in links]
    expected_hrefs = [SECTION_PDF_URLS[key] for key in CATEGORY_SECTION_KEYS[category_id]]
    passed = bool(health["passed"] and len(panels) == 1 and hrefs == expected_hrefs
                  and targets == ["_blank"] * len(expected_hrefs)
                  and all("noopener" in rel for rel in rels)
                  and html.count(FULL_CATALOGUE_URL) == 0)
    return {"passed": passed, "category_id": category_id, "url": url,
            "health": health, "panel_count": len(panels), "hrefs": hrefs,
            "expected_hrefs": expected_hrefs,
            "full_catalogue_url_count": html.count(FULL_CATALOGUE_URL)}


def section_pdf_findings(request: Any) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for key, expected in SECTION_PDFS.items():
        result = request_get(request, SECTION_PDF_URLS[key])
        content_type = str(result["headers"].get("content-type") or "").casefold()
        row = {
            "url": SECTION_PDF_URLS[key], "status": result["status"],
            "content_type": content_type, "bytes": result["body_bytes"],
            "sha256": result["body_sha256"], "pdf_signature": result["pdf_signature"],
        }
        row["passed"] = bool(
            row["status"] == 200 and "application/pdf" in content_type
            and row["bytes"] == expected["bytes"]
            and row["sha256"] == expected["sha256"] and row["pdf_signature"]
        )
        files[key] = row
    return {"passed": all(row["passed"] for row in files.values()), "files": files}


def functional_findings(context: Any, run_id: str) -> dict[str, Any]:
    page = context.new_page()
    install_document_no_cache(page)
    try:
        home_health = goto_healthy(page, f"{EXACT_ORIGIN}/", run_id + "-functional")
        projections = {
            "desktop": menu_projection(page, MAIN_MENU_SELECTOR),
            "footer": menu_projection(page, FOOTER_MENU_SELECTOR),
            "mobile": menu_projection(page, HEADER_MOBILE_SELECTOR),
            "footer_mobile": menu_projection(page, FOOTER_MOBILE_SELECTOR),
        }
        reference = projections["desktop"]
        for projection in projections.values():
            projection["matches_expected"] = projection_matches(projection, reference)
        roots = {
            "desktop": reference["shop_roots"],
            "mobile": projections["mobile"]["shop_roots"],
        }
        roots["passed"] = all(
            value == [{"title": SHOP_TITLE, "path": SHOP_PATH}]
            for value in roots.values()
        )
        guides = {
            str(product_id): guide_findings(page, product_id, run_id + f"-guide-{product_id}")
            for product_id in sorted(PRODUCT_URLS)
        }
        category_catalogues = {
            str(category_id): category_catalogue_findings(
                page, category_id, run_id + f"-category-catalogue-{category_id}")
            for category_id in sorted(CATEGORY_URLS)
        }
        section_pdfs = section_pdf_findings(context.request)
        fnpt_result = request_get(context.request, FNPT_STORE_API_URL)
        fnpt_payload = fnpt_result["payload"] if isinstance(fnpt_result["payload"], dict) else {}
        categories = fnpt_payload.get("categories") if isinstance(fnpt_payload.get("categories"), list) else []
        category_ids = [row.get("id") for row in categories if isinstance(row, dict)]
        attributes = fnpt_payload.get("attributes") if isinstance(fnpt_payload.get("attributes"), list) else []
        resin = [row for row in attributes if isinstance(row, dict) and row.get("name") == "RESIN TYPE"]
        resin_options: list[Any] = []
        if len(resin) == 1 and isinstance(resin[0].get("terms"), list):
            resin_options = [row.get("name") for row in resin[0]["terms"] if isinstance(row, dict)]
        fnpt = {
            "status": fnpt_result["status"],
            "product_id": fnpt_payload.get("id"),
            "category_ids": category_ids,
            "resin_options": resin_options,
        }
        fnpt["passed"] = bool(
            fnpt["status"] == 200
            and fnpt["product_id"] == FNPT_PRODUCT_ID
            and category_ids == [FNPT_TARGET_CATEGORY_ID]
            and REMOVED_RESIN_OPTION not in resin_options
        )
        derakane_health = goto_healthy(page, DERAKANE_PAGE_URL, run_id + "-derakane")
        derakane_page = {
            "health": derakane_health,
            "root_count": len(page.query_selector_all("section[data-derakane-search]")),
            "heading_count": len(page.query_selector_all("section[data-derakane-search] h1")),
        }
        derakane_api_result = request_get(
            context.request,
            DERAKANE_REST_URL + "?chemical=hydrochloric%20acid",
        )
        derakane_payload = (
            derakane_api_result["payload"]
            if isinstance(derakane_api_result["payload"], dict)
            else {}
        )
        derakane_api = {
            "status": derakane_api_result["status"],
            "total": derakane_payload.get("total"),
            "groups_nonempty": bool(derakane_payload.get("groups")),
        }
        derakane = {
            "page": derakane_page,
            "api": derakane_api,
        }
        derakane["passed"] = bool(
            derakane_health["passed"]
            and derakane_page["root_count"] == 1
            and derakane_page["heading_count"] == 1
            and derakane_api["status"] == 200
            and isinstance(derakane_api["total"], int)
            and derakane_api["total"] > 0
            and derakane_api["groups_nonempty"]
        )
        protected_urls = {
            "attachment": HETRON_ATTACHMENT_URL,
            "attachment_query": HETRON_ATTACHMENT_QUERY_URL,
            "direct_pdf": HETRON_DIRECT_PDF_URL,
        }
        protected_routes = {}
        for label, url in protected_urls.items():
            result = request_get(context.request, url, max_redirects=0)
            protected_routes[label] = {
                "status": result["status"],
                "location": result["headers"].get("location"),
            }
        protected = {
            "routes": protected_routes,
            "passed": all(
                row["status"] in {404, 410} and not row["location"]
                for row in protected_routes.values()
            ),
        }
        passed = bool(
            home_health["passed"]
            and all(row["matches_expected"] for row in projections.values())
            and roots["passed"]
            and all(row["passed"] for row in guides.values())
            and all(row["passed"] for row in category_catalogues.values())
            and section_pdfs["passed"]
            and fnpt["passed"]
            and derakane["passed"]
            and protected["passed"]
        )
        return {
            "passed": passed,
            "home_health": home_health,
            "projections": projections,
            "shop_root": roots,
            "guides": guides,
            "category_catalogues": category_catalogues,
            "section_pdfs": section_pdfs,
            "fnpt": fnpt,
            "derakane_v2": derakane,
            "hetron_unavailable": protected,
        }
    finally:
        page.close()


def _same_origin(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme == "https" and (parsed.hostname or "").casefold() == ALLOWED_HOST
    except ValueError:
        return False


def install_document_no_cache(page: Any) -> None:
    """Add no-cache headers only to FRP Depot document navigations.

    Context-wide extra headers are deliberately forbidden here. They propagate
    to cross-origin fonts and payment frames, trigger CORS preflights those
    providers do not allow, and manufacture console errors that a normal visitor
    does not receive.
    """
    def handle(route: Any) -> None:
        request = route.request
        if request.resource_type == "document" and _same_origin(str(request.url)):
            headers = dict(request.headers)
            headers["Cache-Control"] = "no-cache"
            headers["Pragma"] = "no-cache"
            route.continue_(headers=headers)
            return
        route.continue_()

    page.route("**/*", handle)


def capture_page(context: Any, run_dir: Path, run_id: str, viewport_name: str,
                 viewport: dict[str, int], label: str, url: str) -> dict[str, Any]:
    page = context.new_page()
    install_document_no_cache(page)
    console_errors: list[str] = []
    failed_responses: list[dict[str, Any]] = []
    failed_requests: list[dict[str, Any]] = []

    def on_console(message: Any) -> None:
        if message.type == "error":
            console_errors.append(str(message.text))

    def on_response(response: Any) -> None:
        if response.status >= 400 and _same_origin(str(response.url)):
            failed_responses.append({
                "status": response.status,
                "url": str(response.url),
                "resource_type": str(response.request.resource_type),
            })

    def on_request_failed(request: Any) -> None:
        if _same_origin(str(request.url)):
            failure = request.failure
            failed_requests.append({
                "url": str(request.url),
                "resource_type": str(request.resource_type),
                "failure": str(failure or "request failed"),
            })

    page.on("console", on_console)
    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)
    response = None
    navigation_error = None
    metrics: dict[str, Any] = {}
    scan = {"fatal_markers": [], "runtime_error_markers": [], "server_paths": []}
    screenshots: list[dict[str, str]] = []
    mobile_menu = {"opened": False, "visible_links": 0, "screenshot": None}
    try:
        response = page.goto(
            cache_busted(url, f"{run_id}-{viewport_name}-{label}"),
            wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT_MS,
        )
        page.wait_for_timeout(WAIT_AFTER_LOAD_MS)
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(800)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
        metrics = page_metrics(page)
        body = str(page.inner_text("body") or "")
        html = str(page.content() or "")
        scan = scan_runtime_disclosures(body + "\n" + html)
        full_path = run_dir / f"{viewport_name}__{safe_slug(label)}__full.png"
        page.screenshot(path=str(full_path), full_page=True)
        screenshots.append({"kind": "full_page", "path": str(full_path), "sha256": sha256_file(full_path)})
        if viewport_name == "mobile":
            toggle = page.locator(".mobile_menu_bar, .mobile_menu_bar_toggle").first
            if toggle.count() == 1 and toggle.is_visible():
                toggle.click(timeout=5_000)
                page.wait_for_timeout(300)
                menu_path = run_dir / f"{viewport_name}__{safe_slug(label)}__menu_open.png"
                page.screenshot(path=str(menu_path), full_page=False)
                screenshots.append({"kind": "mobile_menu", "path": str(menu_path), "sha256": sha256_file(menu_path)})
                mobile_menu = {
                    "opened": True,
                    "visible_links": page.locator(".et_mobile_menu a:visible").count(),
                    "screenshot": str(menu_path),
                }
    except Exception as exc:
        navigation_error = f"{type(exc).__name__}: {exc}"
    finally:
        final_url = str(page.url)
        page.close()
    return {
        "viewport": viewport_name,
        "viewport_size": dict(viewport),
        "label": label,
        "url": url,
        "expected_path": urlsplit(url).path or "/",
        "http_status": response.status if response else None,
        "final_url": final_url,
        "navigation_error": navigation_error,
        "metrics": metrics,
        **scan,
        "console_errors": console_errors[:50],
        "failed_same_origin_responses": failed_responses[:50],
        "failed_same_origin_requests": failed_requests[:50],
        "screenshots": screenshots,
        "mobile_menu": mobile_menu,
    }


def issue(severity: str, scope: str, message: str) -> dict[str, str]:
    return {"severity": severity, "scope": scope, "issue": message}


def page_issues(row: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    scope = f"{row['viewport']}:{row['label']}"
    findings: list[dict[str, str]] = []
    observations: list[dict[str, str]] = []
    metrics = row.get("metrics") or {}
    if row.get("navigation_error"):
        findings.append(issue("high", scope, str(row["navigation_error"])))
    if row.get("http_status") != 200:
        findings.append(issue("high", scope, f"HTTP {row.get('http_status')}"))
    if (urlsplit(str(row.get("final_url") or "")).path or "/") != row.get("expected_path"):
        findings.append(issue("high", scope, f"Unexpected redirect to {row.get('final_url')}"))
    if metrics.get("body_text_chars", 0) < MIN_BODY_CHARS:
        findings.append(issue("high", scope, "Blank or abnormally short body"))
    if row.get("fatal_markers"):
        findings.append(issue("high", scope, f"Fatal markers: {row['fatal_markers']}"))
    if row.get("runtime_error_markers"):
        findings.append(issue("high", scope, f"Runtime errors: {row['runtime_error_markers']}"))
    if row.get("server_paths"):
        findings.append(issue("high", scope, "Server filesystem path disclosure"))
    if row.get("console_errors"):
        findings.append(issue("high", scope, f"Console errors: {row['console_errors']}"))
    if row.get("failed_same_origin_responses"):
        findings.append(issue("high", scope, f"Failed same-origin responses: {row['failed_same_origin_responses']}"))
    if row.get("failed_same_origin_requests"):
        findings.append(issue("high", scope, f"Failed same-origin requests: {row['failed_same_origin_requests']}"))
    if metrics.get("horizontal_overflow_px", 0) > MAX_OVERFLOW_PX:
        findings.append(issue("medium", scope, f"Horizontal overflow {metrics['horizontal_overflow_px']}px"))
    if metrics.get("broken_images"):
        findings.append(issue("medium", scope, f"Broken images: {metrics['broken_images']}"))
    if row["viewport"] == "mobile" and not row.get("mobile_menu", {}).get("opened"):
        findings.append(issue("medium", scope, "Fixed mobile menu toggle did not open"))
    if not row.get("screenshots"):
        findings.append(issue("high", scope, "No screenshot captured"))
    if metrics.get("empty_headings"):
        observations.append(issue("low", scope, f"Visible empty headings: {len(metrics['empty_headings'])}"))
    if metrics.get("placeholder_links"):
        observations.append(issue("low", scope, f"Visible placeholder/control links: {len(metrics['placeholder_links'])}"))
    return findings, observations


def make_contact_sheet(entries: Iterable[tuple[Path, str]], output: Path,
                       *, crop_height: int | None = None) -> dict[str, str]:
    from PIL import Image, ImageDraw

    rows = list(entries)
    if not rows:
        raise QaError("Cannot build an empty contact sheet.")
    tile_width = 320
    tile_height = 1120
    label_height = 40
    columns = 4
    count_rows = (len(rows) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * tile_width, count_rows * tile_height), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (path, label) in enumerate(rows):
        with Image.open(path) as source:
            image = source.convert("RGB")
            if crop_height is not None:
                image = image.crop((0, 0, image.width, min(image.height, crop_height)))
            image.thumbnail((tile_width - 20, tile_height - label_height - 20))
            x = (index % columns) * tile_width + (tile_width - image.width) // 2
            y0 = (index // columns) * tile_height
            y = y0 + label_height + 10
            canvas.paste(image, (x, y))
            draw.text((index % columns * tile_width + 8, y0 + 10), label[:48], fill="black")
    canvas.save(output, format="PNG")
    return {"path": str(output), "sha256": sha256_file(output)}


def build_contact_sheets(run_dir: Path, pages: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    desktop = []
    mobile = []
    menus = []
    full = []
    for row in pages:
        for shot in row.get("screenshots") or []:
            item = (Path(shot["path"]), f"{row['viewport']} {row['label']} {shot['kind']}")
            if shot["kind"] == "full_page":
                full.append(item)
                (desktop if row["viewport"] == "desktop" else mobile).append(item)
            elif shot["kind"] == "mobile_menu":
                menus.append(item)
    return {
        "desktop_first_view": make_contact_sheet(
            desktop, run_dir / "contact_sheet_desktop_first_view.png", crop_height=VIEWPORTS["desktop"]["height"]
        ),
        "mobile_first_view": make_contact_sheet(
            mobile, run_dir / "contact_sheet_mobile_first_view.png", crop_height=VIEWPORTS["mobile"]["height"]
        ),
        "mobile_menus": make_contact_sheet(
            menus, run_dir / "contact_sheet_mobile_menus.png"
        ),
        "full_page_overview": make_contact_sheet(
            full, run_dir / "contact_sheet_full_page_overview.png"
        ),
    }


def read_installed_plugin_state() -> dict[str, Any]:
    ensure_runtime_temp()
    if str(COMMON) not in sys.path:
        sys.path.insert(0, str(COMMON))
    from ui_lane_lock import ui_browser_lock
    from playwright.sync_api import sync_playwright

    with ui_browser_lock(
        "wordpress",
        purpose="read-only catalogue release QA installed-state proof",
        wait_seconds=0,
    ), sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=15_000)
        if not browser.contexts:
            raise QaError("Authenticated WordPress browser has no context.")
        page = browser.contexts[0].new_page()
        try:
            assert_frp_url(PLUGINS_URL, admin=True)
            page.goto(PLUGINS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            assert_frp_url(str(page.url), admin=True)
            rows = page.query_selector_all(PLUGIN_ROW_SELECTOR)
            if len(rows) != 1:
                raise QaError("Fixed catalogue plugin row is absent or ambiguous.")
            row = rows[0]
            tokens = set(str(row.get_attribute("class") or "").split())
            active = "active" in tokens
            inactive = "inactive" in tokens
            if active == inactive:
                raise QaError("Fixed catalogue plugin state class is ambiguous.")
            has_activate = row.query_selector(ACTIVATE_SELECTOR) is not None
            has_deactivate = row.query_selector(DEACTIVATE_SELECTOR) is not None
            if has_activate == has_deactivate or active != has_deactivate:
                raise QaError("Fixed catalogue plugin state and action links disagree.")
            cell = row.query_selector(VERSION_SELECTOR)
            found = VERSION_PATTERN.search(str(cell.inner_text() or "")) if cell else None
            if not found:
                raise QaError("Fixed catalogue plugin version is unreadable.")
            update_rows = page.query_selector_all(PLUGIN_UPDATE_SELECTOR)
            if len(update_rows) > 1:
                raise QaError("Fixed catalogue plugin update marker is ambiguous.")
            state = {
                "plugin_file": PLUGIN_FILE,
                "version": found.group(1),
                "active": active,
                "update_marker": "update" in tokens or bool(update_rows),
            }
            state["passed"] = state == {
                "plugin_file": PLUGIN_FILE,
                "version": PLUGIN_VERSION,
                "active": True,
                "update_marker": False,
            }
            state["read_method"] = "new authenticated Plugins-page tab under shared browser mutex; no click"
            return state
        finally:
            page.close()


def report_summary(pages: list[dict[str, Any]], functional: dict[str, Any],
                   plugin_state: dict[str, Any], contact_sheets: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    observations: list[dict[str, str]] = []
    for row in pages:
        row_findings, row_observations = page_issues(row)
        findings.extend(row_findings)
        observations.extend(row_observations)
    if not functional.get("passed"):
        findings.append(issue("high", "functional", "Protected catalogue functional contract failed"))
    if not plugin_state.get("passed"):
        findings.append(issue("high", "installed_state", "Exact plugin version/active/update state failed"))
    if set(contact_sheets) != {
        "desktop_first_view", "mobile_first_view", "mobile_menus", "full_page_overview"
    }:
        findings.append(issue("high", "screenshots", "Required contact-sheet set is incomplete"))
    screenshots = [shot for row in pages for shot in row.get("screenshots") or []]
    expected_screenshots = len(PUBLIC_PAGES) * 3
    if len(screenshots) != expected_screenshots:
        findings.append(issue(
            "high", "screenshots", f"Expected {expected_screenshots} screenshots; found {len(screenshots)}"
        ))
    high = sum(item["severity"] == "high" for item in findings)
    medium = sum(item["severity"] == "medium" for item in findings)
    return {
        "public_pages": len(PUBLIC_PAGES),
        "viewport_checks": len(pages),
        "screenshots": len(screenshots),
        "expected_screenshots": expected_screenshots,
        "high": high,
        "medium": medium,
        "findings": findings,
        "observations": observations,
        "automated_passed": high == 0 and medium == 0,
        "automated_status": (
            "AUTOMATED_GATES_PASSED_PIXEL_REVIEW_REQUIRED"
            if high == 0 and medium == 0
            else "AUTOMATED_GATES_FAILED"
        ),
    }


def run_audit() -> tuple[Path, dict[str, Any]]:
    ensure_runtime_temp()
    run_id, run_dir = new_run_dir()
    started = utc_now()
    pages: list[dict[str, Any]] = []
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            for viewport_name, viewport in VIEWPORTS.items():
                context = browser.new_context(viewport=viewport, device_scale_factor=1)
                try:
                    for label, url in PUBLIC_PAGES:
                        pages.append(capture_page(
                            context, run_dir, run_id, viewport_name, viewport, label, url
                        ))
                finally:
                    context.close()
            functional_context = browser.new_context(viewport=VIEWPORTS["desktop"])
            try:
                functional = functional_findings(functional_context, run_id)
            finally:
                functional_context.close()
        finally:
            browser.close()
    plugin_state = read_installed_plugin_state()
    contact_sheets = build_contact_sheets(run_dir, pages)
    summary = report_summary(pages, functional, plugin_state, contact_sheets)
    report_core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "run_id": run_id,
        "started_utc": started.isoformat(),
        "finished_utc": utc_now().isoformat(),
        "origin": EXACT_ORIGIN,
        "read_only": True,
        "plugin_state": plugin_state,
        "pages": pages,
        "functional": functional,
        "contact_sheets": contact_sheets,
        "summary": summary,
        "pixel_review_required": True,
    }
    report = {**report_core, "automated_report_sha256": digest_for(report_core)}
    report_path = run_dir / REPORT_NAME
    exclusive_json(report_path, report)
    append_receipt("catalogue_release_automated_qa_issued", str(report_path))
    return report_path, report


def parse_utc(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise QaError(f"{label} is not an ISO timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise QaError(f"{label} must be UTC.")
    return parsed


def load_automated_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QaError("Automated report is unreadable.") from exc
    if not isinstance(report, dict) or set(report) != REPORT_KEYS:
        raise QaError("Automated report schema is not exact.")
    saved = str(report.pop("automated_report_sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(report)):
        raise QaError("Automated report digest failed.")
    report["automated_report_sha256"] = saved
    if (
        report["schema_version"] != SCHEMA_VERSION
        or report["tool"] != TOOL_NAME
        or report["tool_version"] != TOOL_VERSION
        or report["origin"] != EXACT_ORIGIN
        or report["read_only"] is not True
        or report["pixel_review_required"] is not True
    ):
        raise QaError("Automated report identity is invalid.")
    if report.get("summary", {}).get("automated_passed") is not True:
        raise QaError("Cannot finalize a release whose automated gates failed.")
    return report


def expected_screenshots(report: dict[str, Any]) -> dict[str, str]:
    screenshots: dict[str, str] = {}
    run_dir = (RUN_ROOT / str(report["run_id"])).resolve()
    for row in report["pages"]:
        for screenshot in row.get("screenshots") or []:
            path = Path(str(screenshot.get("path") or "")).resolve()
            digest = str(screenshot.get("sha256") or "")
            if path.parent != run_dir or path.suffix.casefold() != ".png":
                raise QaError("Automated report screenshot escaped its fixed run directory.")
            if str(path) in screenshots:
                raise QaError("Automated report contains a duplicate screenshot path.")
            if not path.is_file() or not secrets.compare_digest(sha256_file(path), digest):
                raise QaError("Automated screenshot is missing or changed.")
            screenshots[str(path)] = digest
    if len(screenshots) != int(report["summary"]["expected_screenshots"]):
        raise QaError("Automated screenshot set is incomplete.")
    return screenshots


def validate_pixel_review(report: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(review, dict) or set(review) != REVIEW_KEYS:
        raise QaError("Pixel-review schema is not exact.")
    if review["schema_version"] != SCHEMA_VERSION:
        raise QaError("Pixel-review schema version is invalid.")
    if not secrets.compare_digest(
        str(review["automated_report_sha256"]), str(report["automated_report_sha256"])
    ):
        raise QaError("Pixel review is for a different automated report.")
    reviewed_utc = parse_utc(review["reviewed_utc"], "reviewed_utc")
    if reviewed_utc > utc_now() + timedelta(minutes=5):
        raise QaError("Pixel review time is in the future.")
    if str(review["reviewer"]).strip() != "Dado pixel review":
        raise QaError("Pixel-review reviewer identity is invalid.")
    if review["overall"] not in {"pass", "fail"}:
        raise QaError("Pixel-review overall must be pass or fail.")
    if not isinstance(review["notes"], str) or not review["notes"].strip():
        raise QaError("Pixel-review overall notes are required.")
    expected = expected_screenshots(report)
    rows = review["screenshots"]
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise QaError("Pixel review must cover every screenshot exactly once.")
    seen: set[str] = set()
    failed: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != REVIEW_SCREENSHOT_KEYS:
            raise QaError("Pixel-review screenshot row schema is not exact.")
        path = str(Path(str(row["path"])).resolve())
        if path in seen or path not in expected:
            raise QaError("Pixel-review screenshot path is duplicate or unexpected.")
        seen.add(path)
        if not secrets.compare_digest(str(row["sha256"]), expected[path]):
            raise QaError("Pixel-review screenshot digest mismatch.")
        if row["result"] not in {"pass", "fail"}:
            raise QaError("Pixel-review screenshot result must be pass or fail.")
        if not isinstance(row["notes"], str) or not row["notes"].strip():
            raise QaError("Every screenshot needs explicit visual notes.")
        if row["result"] == "fail":
            failed.append(path)
    if seen != set(expected):
        raise QaError("Pixel-review screenshot coverage is incomplete.")
    should_pass = not failed
    if (review["overall"] == "pass") != should_pass:
        raise QaError("Pixel-review overall result contradicts screenshot results.")
    return {
        "reviewed_utc": reviewed_utc.isoformat(),
        "reviewer": review["reviewer"],
        "overall": review["overall"],
        "notes": review["notes"],
        "screenshots_reviewed": len(rows),
        "failed_screenshots": failed,
    }


def finalize(report_path: Path, review_path: Path) -> tuple[Path, dict[str, Any]]:
    report = load_automated_report(report_path)
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QaError("Pixel review is unreadable.") from exc
    visual = validate_pixel_review(report, review)
    review_sha256 = sha256_file(review_path)
    final = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "status": "PASSED" if visual["overall"] == "pass" else "FAILED_VISUAL_REVIEW",
        "recorded_utc": utc_now().isoformat(),
        "origin": EXACT_ORIGIN,
        "release": {
            "plugin_file": PLUGIN_FILE,
            "version": PLUGIN_VERSION,
            "active": True,
        },
        "automated_report": str(report_path),
        "automated_report_sha256": report["automated_report_sha256"],
        "pixel_review": str(review_path),
        "pixel_review_sha256": review_sha256,
        "automated_summary": report["summary"],
        "visual_review": visual,
        "protected_regressions": report["functional"],
        "plugin_state": report["plugin_state"],
        "side_effects": {
            "website_writes": 0,
            "product_writes": 0,
            "order_writes": 0,
            "customer_writes": 0,
            "zoho_writes": 0,
            "email_sends": 0,
        },
        "read_only": True,
    }
    final["sha256"] = digest_for(final)
    final_path = report_path.parent / FINAL_NAME
    exclusive_json(final_path, final)
    append_receipt("catalogue_release_reviewed_qa_issued", str(final_path))
    return final_path, final


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit", help="Run fixed read-only automated QA and capture screenshots.")
    finalize_parser = sub.add_parser(
        "finalize", help="Validate complete pixel review and write immutable final evidence."
    )
    finalize_parser.add_argument("--report", required=True)
    finalize_parser.add_argument("--review", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "audit":
            report_path, report = run_audit()
            print(json.dumps({
                "status": report["summary"]["automated_status"],
                "report": str(report_path),
                "summary": report["summary"],
                "website_writes": 0,
            }, indent=2, ensure_ascii=False))
            return 0 if report["summary"]["automated_passed"] else 2
        report_path = resolve_report_path(args.report)
        review_path = resolve_review_path(args.review, report_path)
        final_path, result = finalize(report_path, review_path)
        print(json.dumps({
            "status": result["status"],
            "result": str(final_path),
            "sha256": result["sha256"],
            "website_writes": 0,
        }, indent=2, ensure_ascii=False))
        return 0 if result["status"] == "PASSED" else 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
