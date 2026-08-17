#!/usr/bin/env python
"""Shared WooCommerce connection and transport for FRP Depot.

Security boundaries:
- Credentials are captured only through hidden local prompts.
- The complete vault is encrypted with Windows DPAPI and ACL-restricted.
- Credentials are sent only in an HTTPS Authorization header, never in URLs/logs.
- The connected host is restricted to frpdepots.com.
- This module provides transport; service writes are permitted only by the named
  stage/commit tool in woocommerce_change_tool.py.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(r"C:\FRPDepot")
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
VAULT_DIR = LOCALAPPDATA / "FRPDepot-WooCommerce"
VAULT_PATH = VAULT_DIR / "connection.dpapi"
AUDIT_DIR = VAULT_DIR / "audits"
ALLOWED_HOST = "frpdepots.com"
ALLOWED_ORIGIN = "https://frpdepots.com"
API_PREFIX = "/wp-json/wc/v3"
DPAPI_DESCRIPTION = "FRP Depot WooCommerce connection"
SECRET_PATTERN = re.compile(r"(?i)(?:ck|cs)_[a-z0-9]{8,}")


class WooError(RuntimeError):
    """A WooCommerce failure, carrying the HTTP status and REST code separately.

    Callers used to decide what a failure MEANT by searching the message text
    ("HTTP 404" in str(exc) and "rest_setting_setting_group_invalid" in ...).
    That is fragile in both directions: a wording change breaks the check, and a
    scrubbed response body containing those words fools it. Anything that needs
    to distinguish "this one endpoint is not there" from "the site is down"
    should read .status and .code.
    """

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise WooError("Windows DPAPI is required for the WooCommerce vault.")
    in_blob, in_buffer = _blob(data)
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob), DPAPI_DESCRIPTION, None, None, None, 0x1,
        ctypes.byref(out_blob),
    )
    _ = in_buffer
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise WooError("Windows DPAPI is required for the WooCommerce vault.")
    in_blob, in_buffer = _blob(data)
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0x1, ctypes.byref(out_blob),
    )
    _ = in_buffer
    if not ok:
        raise WooError("The WooCommerce vault cannot be opened by this Windows user.")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def ensure_vault_dir() -> None:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        user = getpass.getuser()
        result = subprocess.run(
            ["icacls", str(VAULT_DIR), "/inheritance:r", "/grant:r",
             f"{user}:(OI)(CI)F", "SYSTEM:(OI)(CI)F"],
            capture_output=True, text=True, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise WooError("Windows could not restrict the WooCommerce vault folder.")


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(), "action": action, "evidence": evidence}
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def normalize_site_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https":
        raise WooError("WooCommerce connection requires HTTPS.")
    if host != ALLOWED_HOST:
        raise WooError("Connection refused: exact host must be frpdepots.com.")
    if parsed.username or parsed.password:
        raise WooError("Connection refused: the site URL cannot contain user information.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise WooError("Connection refused: invalid site port.") from exc
    if port not in (None, 443):
        raise WooError("Connection refused: WooCommerce must use HTTPS port 443.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise WooError("Site URL must contain only the FRP Depot HTTPS origin.")
    return ALLOWED_ORIGIN


def save_vault(vault: dict[str, Any]) -> None:
    ensure_vault_dir()
    required = {"site_url", "consumer_key", "consumer_secret", "declared_permissions"}
    if not required.issubset(vault):
        raise WooError("WooCommerce vault is missing required connection fields.")
    raw = json.dumps(vault, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    VAULT_PATH.write_bytes(dpapi_protect(raw))


def load_vault() -> dict[str, Any]:
    if not VAULT_PATH.exists():
        raise WooError("WooCommerce is not connected. Run CONNECT_DADO_WOOCOMMERCE.bat.")
    try:
        vault = json.loads(dpapi_unprotect(VAULT_PATH.read_bytes()).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WooError("The WooCommerce vault is unreadable.") from exc
    vault["site_url"] = normalize_site_url(str(vault.get("site_url") or ""))
    if not str(vault.get("consumer_key") or "").startswith("ck_"):
        raise WooError("The WooCommerce vault contains an invalid consumer key.")
    if not str(vault.get("consumer_secret") or "").startswith("cs_"):
        raise WooError("The WooCommerce vault contains an invalid consumer secret.")
    return vault


def scrub(text: str, vault: dict[str, Any] | None = None) -> str:
    result = SECRET_PATTERN.sub("[REDACTED]", str(text or ""))
    if vault:
        for field in ("consumer_key", "consumer_secret"):
            secret = str(vault.get(field) or "")
            if secret:
                result = result.replace(secret, "[REDACTED]")
    return result


def _safe_endpoint(endpoint: str) -> str:
    candidate = str(endpoint or "").strip()
    if not candidate.startswith("/") or "://" in candidate or ".." in candidate:
        raise WooError("WooCommerce endpoint is invalid.")
    if not (candidate == "/" or candidate.startswith("/")):
        raise WooError("WooCommerce endpoint is invalid.")
    return candidate


class NoRedirectHandler(HTTPRedirectHandler):
    """Refuse redirects so credentials can never be forwarded elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def api_request(
    method: str,
    endpoint: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    vault: dict[str, Any] | None = None,
    timeout: int = 60,
    if_match: str | None = None,
) -> tuple[Any, dict[str, str]]:
    vault = vault or load_vault()
    verb = str(method or "").upper()
    if verb not in {"GET", "POST", "PUT"}:
        raise WooError("WooCommerce transport refuses methods other than GET, POST, and PUT.")
    if verb != "GET" and params:
        raise WooError("WooCommerce writes refuse query parameters.")
    if if_match is not None:
        if verb != "PUT" or not re.fullmatch(r'"[0-9a-f]{64}"', if_match):
            raise WooError(
                "WooCommerce conditional writes require PUT and one quoted lowercase SHA-256."
            )
    safe_endpoint = _safe_endpoint(endpoint)
    query = urlencode(params or {}, doseq=True)
    url = str(vault["site_url"]) + API_PREFIX + safe_endpoint
    if query:
        url += "?" + query
    credential = (str(vault["consumer_key"]) + ":" + str(vault["consumer_secret"])).encode("utf-8")
    headers = {
        "Authorization": "Basic " + base64.b64encode(credential).decode("ascii"),
        "Accept": "application/json",
        "User-Agent": "FRPDepot-Dado-WooCommerce/1.0",
    }
    if if_match is not None:
        headers["If-Match"] = if_match
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=verb)
    try:
        with build_opener(NoRedirectHandler()).open(request, timeout=timeout) as response:
            body = response.read()
            parsed = json.loads(body.decode("utf-8")) if body else {}
            response_headers = {k.casefold(): v for k, v in response.headers.items()}
            return parsed, response_headers
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        detail = scrub(raw, vault)
        rest_code = None
        try:
            rest_code = (json.loads(raw) or {}).get("code")
        except (ValueError, AttributeError):
            pass
        raise WooError(
            f"WooCommerce {verb} {safe_endpoint} failed with HTTP {exc.code}: {detail[:1000]}",
            status=exc.code,
            code=str(rest_code) if rest_code else None,
        ) from exc
    except URLError as exc:
        raise WooError(f"WooCommerce could not be reached: {scrub(str(exc.reason), vault)}") from exc


def api_get(endpoint: str, params: dict[str, Any] | None = None,
            vault: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
    return api_request("GET", endpoint, params=params, vault=vault)


def api_get_optional(endpoint: str, params: dict[str, Any] | None = None,
                     vault: dict[str, Any] | None = None,
                     skipped: list[dict[str, str]] | None = None) -> Any:
    """GET an endpoint that may legitimately not exist. Returns None if so.

    Every enumerate-then-fetch site in the audit has the same race: something is
    listed, then fetched individually, and it can be gone (or advertised by a
    plugin that cannot actually serve it) by the time we ask. A 404 there used to
    propagate out of command_store and kill the whole audit AFTER every catalog,
    order and customer page had already been fetched.

    Only a genuine not-found is swallowed. Anything else - auth, 5xx, a network
    failure - still raises, because those mean the audit's numbers are wrong
    rather than merely incomplete.
    """
    try:
        data, _ = api_get(endpoint, params, vault)
        return data
    except WooError as exc:
        if exc.status == 404:
            if skipped is not None:
                skipped.append({
                    "endpoint": endpoint,
                    "code": exc.code or "not_found",
                    "warning": "Endpoint was advertised but returned HTTP 404; skipped.",
                })
            return None
        raise


def get_all(
    endpoint: str,
    params: dict[str, Any] | None = None,
    *,
    vault: dict[str, Any] | None = None,
    max_pages: int = 200,
    max_items: int = 20000,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    base = dict(params or {})
    base["per_page"] = min(int(base.get("per_page") or 100), 100)
    for page in range(1, max_pages + 1):
        query = dict(base)
        query["page"] = page
        data, headers = api_get(endpoint, query, vault)
        if not isinstance(data, list):
            raise WooError(f"WooCommerce {endpoint} did not return a list.")
        collected.extend(item for item in data if isinstance(item, dict))
        if len(collected) > max_items:
            raise WooError(f"WooCommerce {endpoint} exceeded the {max_items}-record safety limit.")
        total_pages = int(headers.get("x-wp-totalpages") or 0)
        if not data or (total_pages and page >= total_pages) or len(data) < int(base["per_page"]):
            return collected
    raise WooError(f"WooCommerce {endpoint} exceeded the {max_pages}-page safety limit.")


def check_access(vault: dict[str, Any] | None = None) -> dict[str, Any]:
    vault = vault or load_vault()
    checks: dict[str, Any] = {"site": vault["site_url"], "declared_permissions": vault["declared_permissions"]}
    for label, endpoint in (
        ("products", "/products"),
        ("orders", "/orders"),
        ("customers", "/customers"),
        ("system_status", "/system_status"),
    ):
        data, headers = api_get(endpoint, {"per_page": 1}, vault)
        checks[label] = {
            "read": True,
            "total": int(headers.get("x-wp-total") or 0) if isinstance(data, list) else None,
        }
    return checks


def command_connect(_: argparse.Namespace) -> None:
    print("FRP Depot WooCommerce connection")
    print("Keys are hidden while entered and are never printed or logged.")
    site = normalize_site_url(input("Site URL [https://frpdepots.com]: ").strip() or "https://frpdepots.com")
    key = getpass.getpass("Consumer key (hidden): ").strip()
    secret = getpass.getpass("Consumer secret (hidden): ").strip()
    if not key.startswith("ck_") or len(key) < 20:
        raise WooError("The consumer key format is invalid.")
    if not secret.startswith("cs_") or len(secret) < 20:
        raise WooError("The consumer secret format is invalid.")
    confirmation = input("Type READ/WRITE to confirm the WooCommerce key was created with Read/Write permission: ").strip()
    if confirmation != "READ/WRITE":
        raise WooError("Connection cancelled: Read/Write permission was not confirmed.")
    vault = {
        "site_url": site,
        "consumer_key": key,
        "consumer_secret": secret,
        "declared_permissions": "read_write",
        "connected_utc": datetime.now(timezone.utc).isoformat(),
    }
    checks = check_access(vault)
    save_vault(vault)
    append_receipt("woocommerce_connected_read_write", str(VAULT_PATH))
    print("WooCommerce connection: VERIFIED")
    print("Store: frpdepots.com")
    print("Product read: VERIFIED")
    print("Order read: VERIFIED")
    print("Customer read: VERIFIED")
    print("System-status read: VERIFIED")
    print("No write was attempted during connection.")
    print("Declared key permission: " + str(checks["declared_permissions"]))


def command_check(_: argparse.Namespace) -> None:
    checks = check_access()
    append_receipt("woocommerce_connection_verified", str(VAULT_PATH))
    print(json.dumps({"status": "VERIFIED", **checks, "credentials_displayed": False}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FRP Depot WooCommerce secure connection")
    commands = parser.add_subparsers(dest="command", required=True)
    connect = commands.add_parser("connect")
    connect.set_defaults(func=command_connect)
    check = commands.add_parser("check")
    check.set_defaults(func=command_check)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (WooError, OSError, ValueError) as exc:
        print("ERROR: " + scrub(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
