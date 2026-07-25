#!/usr/bin/env python
"""Read-only public and authenticated WooCommerce audits for FRP Depot.

The authenticated audit may read products, variations, safe store settings,
customers, and orders. Positive API projections prevent customer names, emails,
addresses, phones, notes, metadata, credentials, and payment details from leaving
the store. Raw responses are aggregated in memory and are never cached.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import socket
import ssl
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import woocommerce_common as wc

PUBLIC_SITE = "https://frpdepots.com"
PUBLIC_MAX_URLS = 150
PUBLIC_MAX_SITEMAPS = 20
PRIVATE_MAX_ITEMS = 20000
PUBLIC_REPORT_DIR = Path(r"C:\FRPDepot\Dado\20_Working\woocommerce_audits")
SAFE_SETTING_IDS = {
    "woocommerce_currency", "woocommerce_currency_pos", "woocommerce_price_num_decimals",
    "woocommerce_weight_unit", "woocommerce_dimension_unit", "woocommerce_calc_taxes",
    "woocommerce_prices_include_tax", "woocommerce_manage_stock", "woocommerce_hold_stock_minutes",
    "woocommerce_notify_low_stock_amount", "woocommerce_notify_no_stock_amount",
    "woocommerce_enable_reviews", "woocommerce_review_rating_verification_required",
    "woocommerce_enable_guest_checkout", "woocommerce_enable_checkout_login_reminder",
    "woocommerce_enable_signup_and_login_from_checkout", "woocommerce_enable_myaccount_registration",
    "woocommerce_cart_redirect_after_add", "woocommerce_enable_ajax_add_to_cart",
}
PRODUCT_AUDIT_FIELDS = ",".join((
    "id", "name", "date_created_gmt", "date_modified_gmt", "type", "status",
    "catalog_visibility", "sku", "price", "regular_price", "sale_price", "on_sale",
    "purchasable", "total_sales", "manage_stock", "stock_quantity", "stock_status",
    "backorders", "weight", "dimensions", "shipping_class", "tax_status", "tax_class",
    "description", "short_description", "reviews_allowed", "average_rating", "rating_count",
    "categories", "images", "attributes", "default_attributes", "variations",
))
VARIATION_AUDIT_FIELDS = ",".join((
    "id", "date_created_gmt", "date_modified_gmt", "status", "description", "sku",
    "price", "regular_price", "sale_price", "on_sale", "purchasable", "manage_stock",
    "stock_quantity", "stock_status", "backorders", "weight", "dimensions",
    "shipping_class", "tax_status", "tax_class", "image", "attributes",
))
ORDER_AUDIT_FIELDS = ",".join((
    "date_created_gmt", "status", "currency", "total", "customer_id", "created_via",
    "payment_method", "line_items.product_id", "line_items.variation_id", "line_items.quantity",
))
CUSTOMER_AUDIT_FIELDS = "date_created_gmt,role,is_paying_customer"


class AuditError(RuntimeError):
    pass


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.in_title = False
        self.h1_count = 0
        self.meta_description = ""
        self.canonical = ""
        self.links: list[str] = []
        self.forms = 0
        self.mailto = 0
        self.tel = 0
        self.mixed_content = 0
        self.jsonld_parts: list[str] = []
        self.in_jsonld = False
        self.visible_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        values = {str(k).casefold(): str(v or "") for k, v in attrs}
        low = tag.casefold()
        if low == "title":
            self.in_title = True
        elif low == "h1":
            self.h1_count += 1
        elif low == "meta" and values.get("name", "").casefold() == "description":
            self.meta_description = values.get("content", "").strip()
        elif low == "link" and "canonical" in values.get("rel", "").casefold():
            self.canonical = values.get("href", "").strip()
        elif low == "a":
            href = values.get("href", "").strip()
            if href:
                self.links.append(href)
                self.mailto += int(href.casefold().startswith("mailto:"))
                self.tel += int(href.casefold().startswith("tel:"))
        elif low == "form":
            self.forms += 1
        elif low == "script" and values.get("type", "").casefold() == "application/ld+json":
            self.in_jsonld = True
        for attr in ("src", "href"):
            value = values.get(attr, "").strip().casefold()
            if value.startswith("http://"):
                self.mixed_content += 1

    def handle_endtag(self, tag: str):
        low = tag.casefold()
        if low == "title":
            self.in_title = False
        elif low == "script":
            self.in_jsonld = False

    def handle_data(self, data: str):
        if self.in_title:
            self.title_parts.append(data)
        if self.in_jsonld:
            self.jsonld_parts.append(data)
        self.visible_parts.append(data)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_fetch(url: str, timeout: int = 30, max_bytes: int = 2_500_000) -> dict[str, Any]:
    req = Request(url, headers={"User-Agent": "FRPDepot-Dado-Public-Audit/1.0", "Accept": "*/*"})
    start = time.perf_counter()
    try:
        with urlopen(req, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            return {
                "status": int(response.status), "url": response.geturl(),
                "headers": {k.casefold(): v for k, v in response.headers.items()},
                "body": body[:max_bytes], "truncated": len(body) > max_bytes,
                "seconds": round(time.perf_counter() - start, 3), "error": None,
            }
    except HTTPError as exc:
        body = exc.read(max_bytes)
        return {
            "status": int(exc.code), "url": exc.geturl(),
            "headers": {k.casefold(): v for k, v in exc.headers.items()},
            "body": body, "truncated": False,
            "seconds": round(time.perf_counter() - start, 3), "error": f"HTTP {exc.code}",
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {
            "status": None, "url": url, "headers": {}, "body": b"", "truncated": False,
            "seconds": round(time.perf_counter() - start, 3), "error": str(exc),
        }


def local_public_url(value: str) -> bool:
    host = (urlparse(value).hostname or "").casefold()
    return host == wc.ALLOWED_HOST


def sitemap_urls() -> tuple[list[str], list[dict[str, Any]]]:
    queue = [PUBLIC_SITE + "/sitemap_index.xml", PUBLIC_SITE + "/wp-sitemap.xml"]
    seen_sitemaps: set[str] = set()
    pages: list[str] = []
    sitemap_results: list[dict[str, Any]] = []
    while queue and len(seen_sitemaps) < PUBLIC_MAX_SITEMAPS and len(pages) < PUBLIC_MAX_URLS:
        url = queue.pop(0)
        if url in seen_sitemaps:
            continue
        seen_sitemaps.add(url)
        result = public_fetch(url)
        sitemap_results.append({"url": url, "status": result["status"], "error": result["error"]})
        if result["status"] != 200:
            continue
        try:
            root = ET.fromstring(result["body"])
        except ET.ParseError:
            continue
        locs = [str(node.text or "").strip() for node in root.iter() if node.tag.casefold().endswith("loc")]
        for loc in locs:
            if not local_public_url(loc):
                continue
            path = urlparse(loc).path.casefold()
            if path.endswith(".xml") and len(seen_sitemaps) + len(queue) < PUBLIC_MAX_SITEMAPS:
                queue.append(loc)
            elif loc not in pages and len(pages) < PUBLIC_MAX_URLS:
                pages.append(loc)
    return pages, sitemap_results


def jsonld_types(parts: list[str]) -> list[str]:
    text = "".join(parts).strip()
    if not text:
        return []
    types: set[str] = set()
    # Robust enough to identify schema types even when multiple JSON objects are concatenated.
    for match in re.finditer(r'"@type"\s*:\s*(?:"([^"]+)"|\[([^\]]+)\])', text, re.I):
        if match.group(1):
            types.add(match.group(1))
        elif match.group(2):
            types.update(re.findall(r'"([^"]+)"', match.group(2)))
    return sorted(types)


def page_audit(url: str) -> dict[str, Any]:
    result = public_fetch(url)
    row: dict[str, Any] = {
        "url": url, "final_url": result["url"], "status": result["status"],
        "seconds": result["seconds"], "bytes": len(result["body"]),
        "truncated": result["truncated"], "error": result["error"],
    }
    content_type = result["headers"].get("content-type", "")
    if result["status"] != 200 or "html" not in content_type.casefold():
        return row
    parser = PageParser()
    try:
        parser.feed(result["body"].decode("utf-8", errors="replace"))
    except Exception:
        row["parse_error"] = True
        return row
    visible = re.sub(r"\s+", " ", unescape(" ".join(parser.visible_parts))).strip().casefold()
    row.update({
        "title": re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip(),
        "meta_description": parser.meta_description,
        "canonical": parser.canonical,
        "h1_count": parser.h1_count,
        "schema_types": jsonld_types(parser.jsonld_parts),
        "forms": parser.forms, "mailto_links": parser.mailto, "tel_links": parser.tel,
        "mixed_content_links": parser.mixed_content,
        "has_quote_language": "quote" in visible or "request a quote" in visible,
        "has_contact_language": "contact" in visible,
    })
    return row


def ssl_summary() -> dict[str, Any]:
    context = ssl.create_default_context()
    with socket.create_connection(("frpdepots.com", 443), timeout=15) as raw:
        with context.wrap_socket(raw, server_hostname="frpdepots.com") as wrapped:
            cert = wrapped.getpeercert()
    not_after = str(cert.get("notAfter") or "")
    expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    return {
        "issuer": dict(x[0] for x in cert.get("issuer", [])).get("organizationName"),
        "expires_utc": expiry.isoformat(),
        "days_remaining": (expiry - datetime.now(timezone.utc)).days,
    }


def derive_public_findings(pages: list[dict[str, Any]], sitemap_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    titles: defaultdict[str, list[str]] = defaultdict(list)
    descriptions: defaultdict[str, list[str]] = defaultdict(list)
    for row in pages:
        url = str(row["url"])
        status = row.get("status")
        if status != 200:
            findings.append({"severity": "critical" if status is None or int(status or 0) >= 500 else "high",
                             "issue": "Page is not returning HTTP 200", "url": url, "evidence": status or row.get("error")})
            continue
        if row.get("seconds", 0) > 3:
            findings.append({"severity": "high", "issue": "Slow initial HTML response", "url": url,
                             "evidence": f"{row['seconds']} seconds"})
        if not row.get("title"):
            findings.append({"severity": "high", "issue": "Missing page title", "url": url})
        else:
            titles[str(row["title"]).casefold()].append(url)
        if not row.get("meta_description"):
            findings.append({"severity": "medium", "issue": "Missing meta description", "url": url})
        else:
            descriptions[str(row["meta_description"]).casefold()].append(url)
        if row.get("h1_count") != 1:
            findings.append({"severity": "medium", "issue": "Page should have exactly one H1", "url": url,
                             "evidence": row.get("h1_count")})
        if not row.get("canonical"):
            findings.append({"severity": "medium", "issue": "Missing canonical URL", "url": url})
        if row.get("mixed_content_links"):
            findings.append({"severity": "high", "issue": "HTTP asset/link found on HTTPS page", "url": url,
                             "evidence": row.get("mixed_content_links")})
        if "/product/" in urlparse(url).path and "Product" not in row.get("schema_types", []):
            findings.append({"severity": "high", "issue": "Product page lacks detectable Product schema", "url": url})
    for title, urls in titles.items():
        if title and len(urls) > 1:
            findings.append({"severity": "medium", "issue": "Duplicate page title", "urls": urls[:20], "count": len(urls)})
    for description, urls in descriptions.items():
        if description and len(urls) > 1:
            findings.append({"severity": "medium", "issue": "Duplicate meta description", "urls": urls[:20], "count": len(urls)})
    if not any(row.get("status") == 200 for row in sitemap_results):
        findings.append({"severity": "high", "issue": "No working XML sitemap found", "evidence": sitemap_results})
    return findings


def write_markdown(path: Path, title: str, report: dict[str, Any]) -> None:
    lines = [f"# {title}", "", f"Generated: {report.get('generated_utc')}", ""]
    summary = report.get("summary") or {}
    for key, value in summary.items():
        lines.append(f"- **{key.replace('_', ' ').title()}:** {value}")
    lines.extend(["", "## Findings", ""])
    findings = report.get("findings") or []
    if not findings:
        lines.append("No findings were recorded.")
    for index, finding in enumerate(findings, 1):
        lines.append(f"{index}. **{str(finding.get('severity', 'info')).upper()} — {finding.get('issue')}**")
        for key, value in finding.items():
            if key not in {"severity", "issue"}:
                lines.append(f"   - {key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def command_public(_: argparse.Namespace) -> None:
    discovered, sitemap_results = sitemap_urls()
    seed = [PUBLIC_SITE + path for path in ("/", "/products/", "/contact/", "/cart/", "/checkout/")]
    urls = []
    for url in seed + discovered:
        if url not in urls and local_public_url(url) and len(urls) < PUBLIC_MAX_URLS:
            urls.append(url)
    pages = [page_audit(url) for url in urls]
    findings = derive_public_findings(pages, sitemap_results)
    severity = Counter(str(row.get("severity") or "info") for row in findings)
    report = {
        "generated_utc": now(), "source": "Public HTTPS crawl; no credentials used",
        "site": PUBLIC_SITE, "ssl": ssl_summary(), "sitemaps": sitemap_results,
        "summary": {
            "urls_discovered": len(discovered), "pages_audited": len(pages),
            "critical_findings": severity["critical"], "high_findings": severity["high"],
            "medium_findings": severity["medium"],
        },
        "findings": findings, "pages": pages,
        "limits": ["No payment was submitted.", "No authenticated store data was read.",
                   "JavaScript execution and laboratory Core Web Vitals require a separate browser/Lighthouse run."],
    }
    PUBLIC_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = PUBLIC_REPORT_DIR / f"public_audit_{stamp}.json"
    md_path = PUBLIC_REPORT_DIR / f"public_audit_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(md_path, "FRP Depot public website audit", report)
    wc.append_receipt("woocommerce_public_audit_issued", str(md_path))
    print(json.dumps({"status": "PUBLIC_AUDIT_COMPLETE", "report": str(md_path),
                      "data": str(json_path), "summary": report["summary"]}, indent=2))


def dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except InvalidOperation:
        return Decimal("0")


def product_findings(products: list[dict[str, Any]], variations: dict[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    skus: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for product in products:
        pid = int(product.get("id") or 0)
        name = str(product.get("name") or "")
        status = str(product.get("status") or "")
        sku = str(product.get("sku") or "").strip()
        ref = {"product_id": pid, "product": name}
        if sku:
            skus[sku.casefold()].append(ref)
        if status == "publish":
            if not sku:
                findings.append({"severity": "high", "issue": "Published product has no SKU", **ref})
            if not str(product.get("description") or "").strip():
                findings.append({"severity": "high", "issue": "Published product has no full description", **ref})
            if not str(product.get("short_description") or "").strip():
                findings.append({"severity": "medium", "issue": "Published product has no short description", **ref})
            if not product.get("images"):
                findings.append({"severity": "high", "issue": "Published product has no image", **ref})
            elif any(not str(image.get("alt") or "").strip() for image in product.get("images") or []):
                findings.append({"severity": "medium", "issue": "Product image is missing alt text", **ref})
            if not product.get("categories"):
                findings.append({"severity": "medium", "issue": "Published product has no category", **ref})
            if not str(product.get("price") or "").strip() and product.get("type") != "variable":
                findings.append({"severity": "high", "issue": "Published non-variable product has no price", **ref})
            if not str(product.get("weight") or "").strip():
                findings.append({"severity": "medium", "issue": "Product has no shipping weight", **ref})
            dims = product.get("dimensions") or {}
            if not all(str(dims.get(k) or "").strip() for k in ("length", "width", "height")):
                findings.append({"severity": "medium", "issue": "Product has incomplete shipping dimensions", **ref})
        if product.get("manage_stock") is True:
            qty = product.get("stock_quantity")
            if qty is None:
                findings.append({"severity": "high", "issue": "Managed-stock product has no stock quantity", **ref})
            elif int(qty) <= 0 and product.get("stock_status") == "instock":
                findings.append({"severity": "critical", "issue": "Product is marked in stock with zero/negative quantity", **ref, "stock_quantity": qty})
            elif int(qty) > 0 and product.get("stock_status") == "outofstock":
                findings.append({"severity": "high", "issue": "Product is marked out of stock despite positive quantity", **ref, "stock_quantity": qty})
        if product.get("type") == "variable":
            rows = variations.get(pid, [])
            if not rows:
                findings.append({"severity": "critical", "issue": "Variable product has no variations", **ref})
            for variation in rows:
                vid = int(variation.get("id") or 0)
                vref = {**ref, "variation_id": vid}
                vsku = str(variation.get("sku") or "").strip()
                if vsku:
                    skus[vsku.casefold()].append(vref)
                else:
                    findings.append({"severity": "high", "issue": "Variation has no SKU", **vref})
                if not str(variation.get("regular_price") or "").strip():
                    findings.append({"severity": "high", "issue": "Variation has no regular price", **vref})
                if variation.get("manage_stock") is True and variation.get("stock_quantity") is None:
                    findings.append({"severity": "high", "issue": "Managed-stock variation has no quantity", **vref})
    for sku, refs in skus.items():
        if sku and len(refs) > 1:
            findings.append({"severity": "critical", "issue": "Duplicate SKU", "sku": sku, "records": refs})
    return findings


def safe_system_status(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    environment = raw.get("environment") or {}
    theme = raw.get("theme") or {}
    active_plugins = raw.get("active_plugins") or []
    security = raw.get("security") or {}
    pages = raw.get("pages") or {}
    return {
        "environment": {k: environment.get(k) for k in (
            "version", "wp_version", "wp_multisite", "wp_memory_limit", "wp_debug_mode",
            "wp_cron", "language", "php_version", "php_post_max_size",
            "php_max_execution_time", "php_max_input_vars", "max_upload_size",
            "default_timezone", "fsockopen_or_curl_enabled",
        ) if k in environment},
        "theme": {k: theme.get(k) for k in (
            "name", "version", "is_child_theme", "has_woocommerce_support",
            "has_woocommerce_file",
        ) if k in theme},
        "active_plugins": [
            {k: plugin.get(k) for k in ("plugin", "name", "version", "version_latest") if k in plugin}
            for plugin in active_plugins if isinstance(plugin, dict)
        ],
        "security": {k: security.get(k) for k in (
            "secure_connection", "hide_errors",
        ) if k in security},
        "pages": {k: bool(value) for k, value in pages.items()} if isinstance(pages, dict) else {},
    }


def command_store(_: argparse.Namespace) -> None:
    vault = wc.load_vault()
    products = wc.get_all(
        "/products",
        {"status": "any", "_fields": PRODUCT_AUDIT_FIELDS, "per_page": 50,
         "orderby": "id", "order": "asc"},
        vault=vault, max_items=PRIVATE_MAX_ITEMS,
    )
    variations: dict[int, list[dict[str, Any]]] = {}
    for product in products:
        if product.get("type") == "variable":
            pid = int(product.get("id") or 0)
            variations[pid] = wc.get_all(
                f"/products/{pid}/variations",
                {"_fields": VARIATION_AUDIT_FIELDS, "per_page": 50,
                 "orderby": "id", "order": "asc"},
                vault=vault, max_items=PRIVATE_MAX_ITEMS,
            )
    categories = wc.get_all(
        "/products/categories",
        {"hide_empty": "false", "_fields": "id,name,slug,parent,description,display,image,menu_order,count"},
        vault=vault, max_items=PRIVATE_MAX_ITEMS,
    )
    attributes = wc.get_all(
        "/products/attributes", {"_fields": "id,name,slug,type,order_by,has_archives"},
        vault=vault, max_items=PRIVATE_MAX_ITEMS,
    )
    attribute_terms_total = 0
    for attribute in attributes:
        aid = int(attribute.get("id") or 0)
        if aid:
            attribute_terms_total += len(wc.get_all(
                f"/products/attributes/{aid}/terms",
                {"hide_empty": "false", "_fields": "id,name,slug,description,menu_order,count"},
                vault=vault, max_items=PRIVATE_MAX_ITEMS,
            ))
    shipping_classes = wc.get_all(
        "/products/shipping_classes", {"hide_empty": "false", "_fields": "id,name,slug,description,count"},
        vault=vault, max_items=PRIVATE_MAX_ITEMS,
    )

    orders = wc.get_all(
        "/orders",
        {"status": "any", "_fields": ORDER_AUDIT_FIELDS, "per_page": 50,
         "orderby": "id", "order": "asc"},
        vault=vault, max_items=PRIVATE_MAX_ITEMS,
    )
    customers = wc.get_all(
        "/customers",
        {"role": "all", "_fields": CUSTOMER_AUDIT_FIELDS, "per_page": 100,
         "orderby": "id", "order": "asc"},
        vault=vault, max_items=PRIVATE_MAX_ITEMS,
    )

    # Collects every endpoint that was advertised but could not be served. Shared
    # by the shipping-zone and settings-group loops below, and surfaced as real
    # findings so a partial audit can never read as a complete one.
    settings_group_warnings: list[dict[str, str]] = []
    system_raw, _ = wc.api_get(
        "/system_status", {"_fields": "environment,active_plugins,theme,security,pages"}, vault
    )
    gateways_raw, _ = wc.api_get(
        "/payment_gateways",
        {"_fields": "id,title,order,enabled,method_title,method_supports"}, vault,
    )
    zones = wc.get_all(
        "/shipping/zones", {"_fields": "id,name,order"}, vault=vault, max_items=1000
    )
    shipping_methods = []
    zone_location_type_counts: Counter[str] = Counter()
    for zone in zones:
        zid = int(zone.get("id") or 0)
        # Same list-then-fetch race as the settings groups: a zone deleted
        # between the listing and this call, or one a shipping plugin advertises
        # but cannot serve, used to 404 and kill the whole audit after every
        # catalog, order and customer page had already been fetched.
        locations = wc.api_get_optional(
            f"/shipping/zones/{zid}/locations", {"_fields": "type"}, vault,
            settings_group_warnings,
        )
        for location in locations if isinstance(locations, list) else []:
            zone_location_type_counts[str(location.get("type") or "unknown")] += 1
        rows = wc.api_get_optional(
            f"/shipping/zones/{zid}/methods",
            {"_fields": "instance_id,title,order,enabled,method_id,method_title,method_description"},
            vault, settings_group_warnings,
        )
        for row in rows if isinstance(rows, list) else []:
            shipping_methods.append({
                "zone_id": zid, "zone_name": zone.get("name"), "method_id": row.get("method_id"),
                "instance_id": row.get("instance_id"), "title": row.get("title"),
                "enabled": row.get("enabled"), "order": row.get("order"),
            })

    # Two-pass settings read: metadata for all options, values only for reviewed safe IDs.
    # Some WooCommerce/plugin combinations advertise a settings group that returns
    # rest_setting_setting_group_invalid when opened. Record and skip that stale
    # advertisement instead of discarding the entire otherwise-valid store audit.
    settings_safe: dict[str, Any] = {}
    groups, _ = wc.api_get(
        "/settings", {"_fields": "id,label,parent_id,sub_groups"}, vault
    )
    for group in groups if isinstance(groups, list) else []:
        gid = str(group.get("id") or "")
        if not gid:
            continue
        metadata = wc.api_get_optional(
            f"/settings/{gid}", {"_fields": "id,label,type,group_id"}, vault,
            settings_group_warnings,
        )
        if metadata is None:
            continue
        for setting in metadata if isinstance(metadata, list) else []:
            sid = str(setting.get("id") or "")
            setting_type = str(setting.get("type") or "").casefold()
            if sid not in SAFE_SETTING_IDS or setting_type in {"password", "email"}:
                continue
            value_row, _ = wc.api_get(
                f"/settings/{gid}/{sid}", {"_fields": "id,type,value"}, vault
            )
            if isinstance(value_row, dict) and str(value_row.get("type") or "").casefold() not in {"password", "email"}:
                settings_safe[sid] = value_row.get("value")

    now_date = date.today()
    current_start = now_date - timedelta(days=90)
    previous_start = current_start - timedelta(days=90)
    totals_by_currency: defaultdict[str, Decimal] = defaultdict(Decimal)
    status_counts = Counter()
    current_counts = Counter()
    previous_counts = Counter()
    current_totals: defaultdict[str, Decimal] = defaultdict(Decimal)
    previous_totals: defaultdict[str, Decimal] = defaultdict(Decimal)
    product_order_counts = Counter()
    guest_orders = 0
    registered_orders = 0
    for order in orders:
        status = str(order.get("status") or "unknown")
        currency = str(order.get("currency") or "unknown")
        amount = dec(order.get("total"))
        status_counts[status] += 1
        totals_by_currency[currency] += amount
        registered = int(order.get("customer_id") or 0) > 0
        registered_orders += int(registered)
        guest_orders += int(not registered)
        try:
            order_date = date.fromisoformat(str(order.get("date_created_gmt") or "")[:10])
        except ValueError:
            order_date = None
        if order_date and order_date >= current_start:
            current_counts[status] += 1
            current_totals[currency] += amount
        elif order_date and previous_start <= order_date < current_start:
            previous_counts[status] += 1
            previous_totals[currency] += amount
        for line in order.get("line_items") or []:
            key = int(line.get("variation_id") or line.get("product_id") or 0)
            if key:
                product_order_counts[key] += int(line.get("quantity") or 0)

    findings = product_findings(products, variations)
    # A skipped endpoint becomes a real FINDING, before the severity count is
    # taken. It used to live only in configuration.settings_group_warnings inside
    # the JSON under %LOCALAPPDATA% - not in the markdown, not in the summary,
    # not in stdout, not in the receipt - so the operator saw STORE_AUDIT_COMPLETE
    # and a configuration block that looked authoritative while silently missing
    # every setting from the failed group.
    for warning in settings_group_warnings:
        findings.append({
            "severity": "medium",
            "issue": "Part of the store configuration could not be read and was skipped",
            "endpoint": warning.get("endpoint", warning.get("group", "?")),
            "code": warning.get("code", ""),
            "evidence": warning.get("warning", ""),
        })
    severity = Counter(str(row.get("severity") or "info") for row in findings)
    gateway_rows = [
        {"id": row.get("id"), "title": row.get("title"), "enabled": row.get("enabled"),
         "method_title": row.get("method_title")}
        for row in gateways_raw if isinstance(row, dict)
    ] if isinstance(gateways_raw, list) else []

    report = {
        "generated_utc": now(), "source": "WooCommerce REST API v3",
        "site": vault["site_url"],
        "summary": {
            "products": len(products), "variations": sum(len(v) for v in variations.values()),
            "categories": len(categories), "attributes": len(attributes),
            "attribute_terms": attribute_terms_total, "shipping_classes": len(shipping_classes),
            "orders": len(orders), "customers": len(customers),
            "critical_findings": severity["critical"], "high_findings": severity["high"],
            "medium_findings": severity["medium"],
            "endpoints_skipped": len(settings_group_warnings),
        },
        "catalog": {
            "status_counts": dict(Counter(str(p.get("status") or "unknown") for p in products)),
            "type_counts": dict(Counter(str(p.get("type") or "unknown") for p in products)),
            "findings": findings,
        },
        "orders": {
            "all_status_counts": dict(status_counts),
            "all_totals_by_currency": {k: str(v) for k, v in totals_by_currency.items()},
            "current_90_days_start": current_start.isoformat(),
            "current_90_days_status_counts": dict(current_counts),
            "current_90_days_totals_by_currency": {k: str(v) for k, v in current_totals.items()},
            "previous_90_days_start": previous_start.isoformat(),
            "previous_90_days_status_counts": dict(previous_counts),
            "previous_90_days_totals_by_currency": {k: str(v) for k, v in previous_totals.items()},
            "guest_orders": guest_orders, "registered_customer_orders": registered_orders,
            "top_ordered_resource_ids": product_order_counts.most_common(25),
        },
        "customers": {
            "registered_count": len(customers),
            "paying_count": sum(1 for row in customers if row.get("is_paying_customer") is True),
            "guest_unique_customers_estimated": False,
        },
        "configuration": {
            "settings": settings_safe,
            "settings_group_warnings": settings_group_warnings,
            "payment_gateways": gateway_rows,
            "shipping_methods": shipping_methods,
            "shipping_zone_location_type_counts": dict(zone_location_type_counts),
            "system_status": safe_system_status(system_raw),
        },
        "privacy": {
            "requested_customer_names": False, "requested_customer_emails": False,
            "requested_addresses": False, "requested_phone_numbers": False,
            "requested_order_notes": False, "requested_payment_details": False,
            "requested_metadata": False,
            "raw_records_cached": False,
            "guest_customer_note": "Unique guest customers are not estimated because that would require identifying data.",
        },
    }
    # The markdown report is intentionally limited to catalog findings and counts.
    public_report = {
        "generated_utc": report["generated_utc"], "summary": report["summary"],
        "findings": findings,
    }
    wc.AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = wc.AUDIT_DIR / f"store_audit_{stamp}.json"
    md_path = wc.AUDIT_DIR / f"store_audit_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(md_path, "FRP Depot WooCommerce private audit", public_report)
    wc.append_receipt("woocommerce_private_store_audit_issued", str(md_path))
    print(json.dumps({"status": "STORE_AUDIT_COMPLETE", "report": str(md_path),
                      "data": str(json_path), "summary": report["summary"],
                      "privacy": report["privacy"]}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FRP Depot WooCommerce read-only audit")
    commands = parser.add_subparsers(dest="command", required=True)
    public = commands.add_parser("public")
    public.set_defaults(func=command_public)
    store = commands.add_parser("store")
    store.set_defaults(func=command_store)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (AuditError, wc.WooError, OSError, ValueError, ssl.SSLError) as exc:
        print("ERROR: " + wc.scrub(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
