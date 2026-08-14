#!/usr/bin/env python3
"""Exact commissioned deployer for the FRP Depot freight-quote companion.

This tool has exactly three commands: stage, apply and rollback.

* stage is read-only with respect to WordPress: it validates the fixed local ZIP
  and reads the fixed plugin row/public endpoints to fingerprint live state.
* apply installs the exact absent artifact (never replaces/deletes a plugin),
  activates only its fixed row, journals every write immediately, and
  automatically deactivates it if read-only public validation fails.
* rollback only deactivates that fixed row. It never deletes plugin files, a
  Gravity Form, a form entry, a page, an order or any payment object.

Authorization is the source-pinned commission/specification hash; there is no
runtime approval argument or token.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Iterator, Protocol
from urllib.parse import parse_qsl, urlsplit
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE / "freight_quote_journey"
ARTIFACT = ROOT / "artifacts" / "frpdepot-freight-quote-journey-1.0.0.zip"
ARTIFACT_MANIFEST = ROOT / "artifacts" / "frpdepot-freight-quote-journey-1.0.0.manifest.json"
STATE_DIR = ROOT / "deployment_state"
PLAN_DIR = STATE_DIR / "plans"
RUN_DIR = STATE_DIR / "runs"
RECEIPT_DIR = STATE_DIR / "receipts"
LOCK_DIR = STATE_DIR / "locks"

SPECIFICATION_SHA256 = "5348ef3f357676f5629cf72696fd3fe0be718a3847854974f20cf28cc7047400"
COMMISSION_ID = "FRP-DEPOT-FREIGHT-QUOTE-JOURNEY-CHOICE-A-2026-08-13"
ORIGIN = "https://frpdepots.com"
PLUGIN_SLUG = "frpdepot-freight-quote-journey"
PLUGIN_FILE = "frpdepot-freight-quote-journey/frpdepot-freight-quote-journey.php"
PLUGIN_VERSION = "1.0.0"
PLUGIN_ROW_SELECTOR = 'tr[data-plugin="frpdepot-freight-quote-journey/frpdepot-freight-quote-journey.php"]:not(.plugin-update-tr)'
PLUGIN_UPLOAD_INPUT_SELECTOR = 'input#pluginzip[type="file"][name="pluginzip"]'
PLUGIN_UPLOAD_SUBMIT_SELECTOR = 'input#install-plugin-submit[type="submit"]'
PUBLIC_SELECTOR = ".frpdepot-fq-page h1"
PUBLIC_HEADING = "Request a Product and Freight Quote"

PLUGINS_URL = ORIGIN + "/wp-admin/plugins.php"
UPLOAD_URL = ORIGIN + "/wp-admin/plugin-install.php?tab=upload"
INSTALL_URL_PREFIX = ORIGIN + "/wp-admin/update.php?action=upload-plugin"
PUBLIC_PATHS = (
    "/request-a-quote/",
    "/product/frp-fw-pipe/",
    "/product/frp-elbow-90/",
    "/product/frp-stub-flange/",
    "/product/frp-manway/",
    "/product/frp-manway-cover/",
    "/contact/",
)
FORBIDDEN_ROUTE_FRAGMENTS = (
    "/wp-login", "/wp-json/wc/", "/checkout", "/order", "/payment", "/cart",
    "gf_entries", "gf_entry", "gf_edit_forms", "post.php", "post-new.php",
)
ALLOWED_ADMIN_ROUTES = {
    ("/wp-admin/plugins.php", ()),
    ("/wp-admin/plugin-install.php", (("tab", "upload"),)),
    ("/wp-admin/update.php", (("action", "upload-plugin"),)),
}
ALLOWED_PUBLIC_ROUTES = {(path, ()) for path in PUBLIC_PATHS}
PLAN_SCHEMA_KEYS = (
    "schema", "commission_id", "specification_sha256", "created_utc", "expires_utc",
    "nonce", "artifact", "live_before", "write_order", "rollback_contract",
    "validation_contract", "external_write_performed", "plan_sha256",
)
RUN_SCHEMA_KEYS = (
    "schema", "run_id", "plan_path", "plan_sha256", "artifact_sha256", "live_before",
    "write_order", "backup_path", "receipt_path", "status", "created_utc", "run_sha256",
)


class DeploymentError(RuntimeError):
    pass


class PageLike(Protocol):
    url: str
    def goto(self, url: str, **kwargs: Any) -> Any: ...
    def query_selector(self, selector: str) -> Any: ...
    def set_input_files(self, selector: str, files: str) -> None: ...
    def wait_for_load_state(self, state: str, **kwargs: Any) -> None: ...
    def locator(self, selector: str) -> Any: ...


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def without_hash(value: dict[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def normalized_route(url: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "frpdepots.com" or parsed.port is not None or parsed.username or parsed.password or parsed.fragment:
        raise DeploymentError("URL is outside the exact FRP Depot HTTPS origin")
    route = (parsed.path or "/", tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True))))
    lower = url.lower()
    if any(fragment in lower for fragment in FORBIDDEN_ROUTE_FRAGMENTS):
        raise DeploymentError("forbidden credential, form, cart, checkout, order or payment route")
    return route


def assert_admin_url(url: str, expected: tuple[str, tuple[tuple[str, str], ...]] | None = None) -> tuple[str, tuple[tuple[str, str], ...]]:
    route = normalized_route(url)
    if route not in ALLOWED_ADMIN_ROUTES or (expected is not None and route != expected):
        raise DeploymentError(f"admin URL/query is not exactly allowlisted: {route!r}")
    return route


def assert_public_url(url: str, expected_path: str | None = None) -> tuple[str, tuple[tuple[str, str], ...]]:
    route = normalized_route(url)
    if route not in ALLOWED_PUBLIC_ROUTES or (expected_path is not None and route != (expected_path, ())):
        raise DeploymentError(f"public URL/query is not exactly allowlisted: {route!r}")
    return route


def ensure_dirs() -> None:
    for directory in (PLAN_DIR, RUN_DIR, RECEIPT_DIR, LOCK_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def durable_write(path: Path, data: bytes, exclusive: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    try:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass


def durable_json(path: Path, value: dict[str, Any], exclusive: bool = True) -> None:
    durable_write(path, json.dumps(value, sort_keys=True, indent=2).encode("utf-8") + b"\n", exclusive=exclusive)


def append_receipt(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = canonical_bytes(record)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(descriptor, "ab", closefd=False) as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def safe_state_path(raw: str, directory: Path, suffix: str) -> Path:
    path = Path(raw).resolve()
    root = directory.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise DeploymentError(f"state file must be under {root}") from exc
    if path.suffix != suffix or not path.is_file():
        raise DeploymentError("state file does not exist or has the wrong suffix")
    return path


def validate_artifact() -> dict[str, Any]:
    if not ARTIFACT.is_file() or not ARTIFACT_MANIFEST.is_file():
        raise DeploymentError("fixed artifact or manifest is missing; run the offline ZIP builder")
    manifest = json.loads(ARTIFACT_MANIFEST.read_text(encoding="utf-8"))
    expected_keys = {"schema", "specification_sha256", "plugin_slug", "plugin_version", "artifact_name", "artifact_sha256", "artifact_size", "files"}
    if set(manifest) != expected_keys:
        raise DeploymentError("artifact manifest has a non-closed schema")
    if manifest["schema"] != 1 or manifest["specification_sha256"] != SPECIFICATION_SHA256 or manifest["plugin_slug"] != PLUGIN_SLUG or manifest["plugin_version"] != PLUGIN_VERSION or manifest["artifact_name"] != ARTIFACT.name:
        raise DeploymentError("artifact identity does not match the commissioned fixed target")
    data = ARTIFACT.read_bytes()
    if sha256_bytes(data) != manifest["artifact_sha256"] or len(data) != manifest["artifact_size"]:
        raise DeploymentError("fixed artifact hash/size mismatch")
    with zipfile.ZipFile(ARTIFACT) as archive:
        names = archive.namelist()
        if names != sorted(manifest["files"]):
            raise DeploymentError("ZIP member identity/order is not exact")
        for name in names:
            member = archive.read(name)
            expected = manifest["files"][name]
            if sha256_bytes(member) != expected["sha256"] or len(member) != expected["size"]:
                raise DeploymentError(f"ZIP member mismatch: {name}")
        plugin_source = archive.read(PLUGIN_FILE).decode("utf-8")
    if f"Version: {PLUGIN_VERSION}" not in plugin_source or SPECIFICATION_SHA256 not in plugin_source:
        raise DeploymentError("plugin header/specification identity is not exact")
    return manifest


def _element_text(element: Any) -> str:
    if element is None:
        return ""
    if hasattr(element, "inner_text"):
        return str(element.inner_text()).strip()
    return ""


def fingerprint_plugin_row(page: PageLike) -> dict[str, Any]:
    assert_admin_url(PLUGINS_URL, ("/wp-admin/plugins.php", ()))
    page.goto(PLUGINS_URL, wait_until="domcontentloaded", timeout=45_000)
    assert_admin_url(page.url, ("/wp-admin/plugins.php", ()))
    row = page.query_selector(PLUGIN_ROW_SELECTOR)
    if row is None:
        return {"present": False, "active": False, "version": "", "row_identity": PLUGIN_FILE}
    classes = str(row.get_attribute("class") or "").split()
    text = _element_text(row)
    match = re.search(r"(?i)version\s+([0-9]+(?:\.[0-9]+){2})", text)
    return {"present": True, "active": "active" in classes, "version": match.group(1) if match else "", "row_identity": PLUGIN_FILE}


def activate_fixed_row(page: PageLike) -> None:
    assert_admin_url(page.url, ("/wp-admin/plugins.php", ()))
    row = page.query_selector(PLUGIN_ROW_SELECTOR)
    if row is None:
        raise DeploymentError("fixed plugin row missing before activation")
    if "active" in str(row.get_attribute("class") or "").split():
        raise DeploymentError("fixed plugin is already active")
    link = row.query_selector('a[href*="action=activate"]')
    if link is None:
        raise DeploymentError("fixed row has no activation link")
    href = str(link.get_attribute("href") or "")
    parsed = urlsplit(href)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if parsed.path != "/wp-admin/plugins.php" or query.get("action") != "activate" or query.get("plugin") != PLUGIN_FILE or "_wpnonce" not in query or set(query) != {"action", "plugin", "plugin_status", "paged", "s", "_wpnonce"}:
        raise DeploymentError("activation href is not the exact fixed plugin-row action")
    link.click()
    page.wait_for_load_state("domcontentloaded", timeout=45_000)
    assert_admin_url(page.url, ("/wp-admin/plugins.php", ()))


def deactivate_fixed_row(page: PageLike) -> None:
    assert_admin_url(page.url, ("/wp-admin/plugins.php", ()))
    row = page.query_selector(PLUGIN_ROW_SELECTOR)
    if row is None:
        raise DeploymentError("fixed plugin row missing before deactivation")
    if "active" not in str(row.get_attribute("class") or "").split():
        return
    link = row.query_selector('a[href*="action=deactivate"]')
    if link is None:
        raise DeploymentError("fixed row has no deactivation link")
    href = str(link.get_attribute("href") or "")
    parsed = urlsplit(href)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if parsed.path != "/wp-admin/plugins.php" or query.get("action") != "deactivate" or query.get("plugin") != PLUGIN_FILE or "_wpnonce" not in query or set(query) != {"action", "plugin", "plugin_status", "paged", "s", "_wpnonce"}:
        raise DeploymentError("deactivation href is not the exact fixed plugin-row action")
    link.click()
    page.wait_for_load_state("domcontentloaded", timeout=45_000)
    assert_admin_url(page.url, ("/wp-admin/plugins.php", ()))


def install_exact_artifact(page: PageLike) -> None:
    assert_admin_url(UPLOAD_URL, ("/wp-admin/plugin-install.php", (("tab", "upload"),)))
    page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=45_000)
    assert_admin_url(page.url, ("/wp-admin/plugin-install.php", (("tab", "upload"),)))
    page.set_input_files(PLUGIN_UPLOAD_INPUT_SELECTOR, str(ARTIFACT))
    submit = page.query_selector(PLUGIN_UPLOAD_SUBMIT_SELECTOR)
    if submit is None:
        raise DeploymentError("fixed plugin upload submit control missing")
    submit.click()
    page.wait_for_load_state("domcontentloaded", timeout=90_000)
    route = normalized_route(page.url)
    if route != ("/wp-admin/update.php", (("action", "upload-plugin"),)):
        raise DeploymentError("plugin upload ended outside the exact upload-plugin route")
    body = _element_text(page.query_selector("body"))
    if "Plugin installed successfully" not in body:
        raise DeploymentError("WordPress did not confirm exact plugin installation")
    # Never click WordPress's generic activation link. Re-enter the fixed plugin row.
    page.goto(PLUGINS_URL, wait_until="domcontentloaded", timeout=45_000)
    assert_admin_url(page.url, ("/wp-admin/plugins.php", ()))


def public_validate(page: PageLike) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for path in PUBLIC_PATHS:
        url = ORIGIN + path
        assert_public_url(url, path)
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        assert_public_url(page.url, path)
        body = _element_text(page.query_selector("body"))
        if not body:
            raise DeploymentError(f"public validation returned empty body for {path}")
        if path == "/request-a-quote/":
            heading = _element_text(page.query_selector(PUBLIC_SELECTOR))
            if heading != PUBLIC_HEADING or "Request Quote" not in body:
                raise DeploymentError("dedicated quote page failed exact validation")
        if path.startswith("/product/") and "Freight quote required" not in body:
            raise DeploymentError(f"Choice A journey missing from {path}")
        if path == "/contact/":
            old = "If your item is listed in the Products section, you can add it to cart; otherwise use the contact form for custom or non-standard requests."
            new = "Product selections approved for direct shipping can be purchased online. Selections requiring packing or freight review will show Request a Freight Quote. Submitting a quote request does not place an order or authorize payment."
            if old in body or new not in body:
                raise DeploymentError("Contact FAQ replacement failed exact validation")
        evidence[path] = {"status": 200, "body_sha256": sha256_bytes(body.encode("utf-8"))}
    return evidence


@contextlib.contextmanager
def browser_pages() -> Iterator[tuple[PageLike, PageLike]]:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9229", timeout=15_000)
        pages = [page for context in browser.contexts for page in context.pages]
        admin = next((page for page in pages if page.url.startswith(ORIGIN + "/wp-admin")), None)
        if admin is None:
            raise DeploymentError("no authenticated FRP Depot WordPress admin tab")
        yield admin, admin


@contextlib.contextmanager
def wordpress_mutex(purpose: str) -> Iterator[None]:
    common = HERE / ".." / "common"
    sys.path.insert(0, str(common.resolve()))
    try:
        from ui_lane_lock import ui_browser_lock
        with ui_browser_lock("wordpress", purpose=purpose, wait_seconds=30):
            yield
    finally:
        if sys.path and sys.path[0] == str(common.resolve()):
            sys.path.pop(0)


def plan_hash(plan: dict[str, Any]) -> str:
    return sha256_bytes(canonical_bytes(without_hash(plan, "plan_sha256")))


def validate_plan(plan: dict[str, Any]) -> None:
    if tuple(plan.keys()) != PLAN_SCHEMA_KEYS:
        raise DeploymentError("plan schema/order is not closed")
    if plan["schema"] != 1 or plan["commission_id"] != COMMISSION_ID or plan["specification_sha256"] != SPECIFICATION_SHA256 or plan["external_write_performed"] is not False:
        raise DeploymentError("plan commission identity is invalid")
    if plan_hash(plan) != plan["plan_sha256"]:
        raise DeploymentError("plan hash is invalid")
    if plan["write_order"] != ["plugin_install", "plugin_activate"] or plan["rollback_contract"] != ["plugin_deactivate"]:
        raise DeploymentError("plan mutation vocabulary is not exact")
    manifest = validate_artifact()
    if plan["artifact"] != {"path": str(ARTIFACT.resolve()), "sha256": manifest["artifact_sha256"], "version": PLUGIN_VERSION, "plugin_file": PLUGIN_FILE}:
        raise DeploymentError("plan artifact target is not exact")
    before = plan["live_before"]
    if set(before) != {"present", "active", "version", "row_identity"} or before["row_identity"] != PLUGIN_FILE:
        raise DeploymentError("plan live fingerprint is not closed")
    if before["present"] or before["active"] or before["version"]:
        raise DeploymentError("this additive deployer refuses existing/replacement plugin bytes")


def command_stage(_args: argparse.Namespace) -> int:
    ensure_dirs()
    manifest = validate_artifact()
    with wordpress_mutex("freight quote journey read-only stage"):
        with browser_pages() as (admin, _public):
            live = fingerprint_plugin_row(admin)
    if live["present"]:
        raise DeploymentError("stage refuses an already installed plugin; no replacement/delete route exists")
    created = utc_now()
    # The plan is deliberately short-lived and immutable. ISO lexical comparison is valid here.
    from datetime import timedelta
    expires = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    plan: dict[str, Any] = {
        "schema": 1,
        "commission_id": COMMISSION_ID,
        "specification_sha256": SPECIFICATION_SHA256,
        "created_utc": created,
        "expires_utc": expires,
        "nonce": secrets.token_hex(16),
        "artifact": {"path": str(ARTIFACT.resolve()), "sha256": manifest["artifact_sha256"], "version": PLUGIN_VERSION, "plugin_file": PLUGIN_FILE},
        "live_before": live,
        "write_order": ["plugin_install", "plugin_activate"],
        "rollback_contract": ["plugin_deactivate"],
        "validation_contract": {"public_paths": list(PUBLIC_PATHS), "quote_selector": PUBLIC_SELECTOR, "quote_heading": PUBLIC_HEADING},
        "external_write_performed": False,
        "plan_sha256": "",
    }
    plan["plan_sha256"] = plan_hash(plan)
    path = PLAN_DIR / f"stage-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{plan['nonce']}.json"
    durable_json(path, plan)
    print(json.dumps({"status": "STAGED", "plan": str(path), "plan_sha256": plan["plan_sha256"], "artifact_sha256": manifest["artifact_sha256"], "external_write_performed": False}, indent=2))
    return 0


def load_plan(raw_path: str) -> tuple[Path, dict[str, Any]]:
    path = safe_state_path(raw_path, PLAN_DIR, ".json")
    plan = json.loads(path.read_text(encoding="utf-8"))
    validate_plan(plan)
    if datetime.fromisoformat(plan["expires_utc"]) <= datetime.now(timezone.utc):
        raise DeploymentError("plan has expired")
    return path, plan


def make_run(plan_path: Path, plan: dict[str, Any]) -> tuple[Path, dict[str, Any], Path]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(8)
    backup_path = RUN_DIR / f"{run_id}.backup.json"
    receipt_path = RECEIPT_DIR / f"{run_id}.jsonl"
    backup = {"schema": 1, "run_id": run_id, "plan_sha256": plan["plan_sha256"], "live_before": plan["live_before"], "rollback_contract": ["plugin_deactivate"], "created_utc": utc_now()}
    durable_json(backup_path, backup)
    run: dict[str, Any] = {
        "schema": 1, "run_id": run_id, "plan_path": str(plan_path), "plan_sha256": plan["plan_sha256"],
        "artifact_sha256": plan["artifact"]["sha256"], "live_before": plan["live_before"],
        "write_order": plan["write_order"], "backup_path": str(backup_path), "receipt_path": str(receipt_path),
        "status": "BACKUP_READY", "created_utc": utc_now(), "run_sha256": "",
    }
    run["run_sha256"] = sha256_bytes(canonical_bytes(without_hash(run, "run_sha256")))
    run_path = RUN_DIR / f"{run_id}.run.json"
    durable_json(run_path, run)
    append_receipt(receipt_path, {"sequence": 0, "run_id": run_id, "plan_sha256": plan["plan_sha256"], "target": "backup", "operation": "BACKUP_READY", "before_sha256": sha256_bytes(canonical_bytes(plan["live_before"])), "after_sha256": sha256_file(backup_path), "timestamp_utc": utc_now(), "result": "verified"})
    return run_path, run, receipt_path


def receipt(sequence: int, run: dict[str, Any], target: str, operation: str, before: dict[str, Any], after: dict[str, Any], result: str = "verified") -> dict[str, Any]:
    return {"sequence": sequence, "run_id": run["run_id"], "plan_sha256": run["plan_sha256"], "target": target, "operation": operation, "before_sha256": sha256_bytes(canonical_bytes(before)), "after_sha256": sha256_bytes(canonical_bytes(after)), "timestamp_utc": utc_now(), "result": result}


def automatic_deactivate(admin: PageLike, run: dict[str, Any], receipt_path: Path, sequence: int) -> None:
    admin.goto(PLUGINS_URL, wait_until="domcontentloaded", timeout=45_000)
    before = fingerprint_plugin_row(admin)
    if before["present"] and before["active"] and before["version"] == PLUGIN_VERSION:
        deactivate_fixed_row(admin)
        after = fingerprint_plugin_row(admin)
        if after != {"present": True, "active": False, "version": PLUGIN_VERSION, "row_identity": PLUGIN_FILE}:
            raise DeploymentError("automatic deactivation did not reach exact inactive state")
        append_receipt(receipt_path, receipt(sequence, run, "plugin_row", "plugin_deactivate", before, after))


def command_apply(args: argparse.Namespace) -> int:
    ensure_dirs()
    plan_path, plan = load_plan(args.plan)
    validate_artifact()
    attempt_path = LOCK_DIR / f"{plan['plan_sha256']}.apply.lock"
    with wordpress_mutex("freight quote journey commissioned apply and emergency rollback"):
        with browser_pages() as (admin, public):
            fresh = fingerprint_plugin_row(admin)
            if fresh != plan["live_before"]:
                raise DeploymentError("pre-write live fingerprint drifted from stage")
            durable_write(attempt_path, canonical_bytes({"plan_sha256": plan["plan_sha256"], "started_utc": utc_now()}))
            run_path, run, receipt_path = make_run(plan_path, plan)
            sequence = 1
            wrote = False
            try:
                # Re-read immediately before every write.
                before_install = fingerprint_plugin_row(admin)
                if before_install != plan["live_before"]:
                    raise DeploymentError("live state drifted immediately before install")
                install_exact_artifact(admin)
                wrote = True
                after_install = fingerprint_plugin_row(admin)
                expected_inactive = {"present": True, "active": False, "version": PLUGIN_VERSION, "row_identity": PLUGIN_FILE}
                if after_install != expected_inactive:
                    raise DeploymentError("installed plugin row did not match exact inactive identity")
                append_receipt(receipt_path, receipt(sequence, run, "plugin_row", "plugin_install", before_install, after_install)); sequence += 1

                before_activate = fingerprint_plugin_row(admin)
                if before_activate != expected_inactive:
                    raise DeploymentError("live state drifted immediately before activation")
                activate_fixed_row(admin)
                after_activate = fingerprint_plugin_row(admin)
                expected_active = {"present": True, "active": True, "version": PLUGIN_VERSION, "row_identity": PLUGIN_FILE}
                if after_activate != expected_active:
                    raise DeploymentError("activation did not reach exact active identity")
                append_receipt(receipt_path, receipt(sequence, run, "plugin_row", "plugin_activate", before_activate, after_activate)); sequence += 1

                evidence = public_validate(public)
                append_receipt(receipt_path, {"sequence": sequence, "run_id": run["run_id"], "plan_sha256": run["plan_sha256"], "target": "public_validation", "operation": "validate", "before_sha256": "", "after_sha256": sha256_bytes(canonical_bytes(evidence)), "timestamp_utc": utc_now(), "result": "verified"})
                print(json.dumps({"status": "APPLIED", "run": str(run_path), "receipt": str(receipt_path), "writes": 2, "public_validation": "verified"}, indent=2))
                return 0
            except Exception:
                if wrote:
                    automatic_deactivate(admin, run, receipt_path, sequence)
                raise


def validate_run(run: dict[str, Any]) -> None:
    if tuple(run.keys()) != RUN_SCHEMA_KEYS:
        raise DeploymentError("run manifest schema/order is not closed")
    expected = sha256_bytes(canonical_bytes(without_hash(run, "run_sha256")))
    if expected != run["run_sha256"] or run["artifact_sha256"] != validate_artifact()["artifact_sha256"] or run["write_order"] != ["plugin_install", "plugin_activate"]:
        raise DeploymentError("run manifest identity/hash is invalid")
    if run["live_before"] != {"present": False, "active": False, "version": "", "row_identity": PLUGIN_FILE}:
        raise DeploymentError("run is not an additive absent-before deployment")
    backup_path = safe_state_path(run["backup_path"], RUN_DIR, ".json")
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    if backup.get("run_id") != run["run_id"] or backup.get("plan_sha256") != run["plan_sha256"] or backup.get("live_before") != run["live_before"] or backup.get("rollback_contract") != ["plugin_deactivate"]:
        raise DeploymentError("backup does not match the fixed run")


def command_rollback(args: argparse.Namespace) -> int:
    ensure_dirs()
    run_path = safe_state_path(args.run, RUN_DIR, ".json")
    if not run_path.name.endswith(".run.json"):
        raise DeploymentError("rollback requires the exact run manifest")
    run = json.loads(run_path.read_text(encoding="utf-8"))
    validate_run(run)
    rollback_lock = LOCK_DIR / f"{run['run_id']}.rollback.lock"
    receipt_path = safe_state_path(run["receipt_path"], RECEIPT_DIR, ".jsonl")
    with wordpress_mutex("freight quote journey explicit rollback"):
        with browser_pages() as (admin, _public):
            live = fingerprint_plugin_row(admin)
            expected_active = {"present": True, "active": True, "version": PLUGIN_VERSION, "row_identity": PLUGIN_FILE}
            if live != expected_active:
                raise DeploymentError("rollback refused: live plugin state is not this run's exact active state")
            durable_write(rollback_lock, canonical_bytes({"run_id": run["run_id"], "started_utc": utc_now()}))
            deactivate_fixed_row(admin)
            after = fingerprint_plugin_row(admin)
            expected_inactive = {"present": True, "active": False, "version": PLUGIN_VERSION, "row_identity": PLUGIN_FILE}
            if after != expected_inactive:
                raise DeploymentError("rollback deactivation did not verify")
            lines = receipt_path.read_text(encoding="utf-8").splitlines()
            append_receipt(receipt_path, receipt(len(lines), run, "plugin_row", "plugin_deactivate", live, after))
    print(json.dumps({"status": "ROLLED_BACK", "run": str(run_path), "plugin_files_retained": True, "form_entries_retained": True}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Commissioned FRP Depot freight-quote journey deployer")
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage", help="read-only live fingerprint and immutable plan")
    stage.set_defaults(func=command_stage)
    apply = commands.add_parser("apply", help="install exact absent artifact, activate fixed row and validate")
    apply.add_argument("--plan", required=True)
    apply.set_defaults(func=command_apply)
    rollback = commands.add_parser("rollback", help="deactivate the exact run; never delete files/forms/entries")
    rollback.add_argument("--run", required=True)
    rollback.set_defaults(func=command_rollback)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except (DeploymentError, OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
