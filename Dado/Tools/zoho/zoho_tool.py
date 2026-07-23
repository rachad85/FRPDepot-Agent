#!/usr/bin/env python
"""FRP Depot Zoho Books and Inventory restricted connector.

This tool exchanges a Zoho self-client grant for an encrypted refresh token and
provides GET-only verification/report commands. The separately named customer
and quote tool may use the two commissioned Books CREATE scopes. This connector
contains no service-API write endpoint itself.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(r"C:\FRPDepot")
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
VAULT_DIR = LOCALAPPDATA / "FRPDepot-Zoho"
VAULT_PATH = VAULT_DIR / "zoho_access.dpapi"
ACCOUNTS_URL = "https://accounts.zohocloud.ca"
EXPECTED_API_DOMAIN = "https://www.zohoapis.ca"
DPAPI_DESCRIPTION = "FRP Depot Zoho restricted credentials"
READ_SCOPES = (
    "ZohoBooks.settings.READ",
    "ZohoBooks.contacts.READ",
    "ZohoBooks.items.READ",
    "ZohoBooks.estimates.READ",
    "ZohoBooks.salesorders.READ",
    "ZohoBooks.invoices.READ",
    "ZohoBooks.customerpayments.READ",
    "ZohoBooks.creditnotes.READ",
    "ZohoBooks.purchaseorders.READ",
    "ZohoBooks.bills.READ",
    "ZohoBooks.vendorpayments.READ",
    "ZohoInventory.settings.READ",
    "ZohoInventory.contacts.READ",
    "ZohoInventory.items.READ",
    "ZohoInventory.compositeitems.READ",
    "ZohoInventory.inventoryadjustments.READ",
    "ZohoInventory.transferorders.READ",
    "ZohoInventory.salesorders.READ",
    "ZohoInventory.packages.READ",
    "ZohoInventory.shipmentorders.READ",
    "ZohoInventory.invoices.READ",
    "ZohoInventory.customerpayments.READ",
    "ZohoInventory.salesreturns.READ",
    "ZohoInventory.creditnotes.READ",
    "ZohoInventory.purchaseorders.READ",
    "ZohoInventory.purchasereceives.READ",
    "ZohoInventory.bills.READ",
)
ALLOWED_WRITE_SCOPES = (
    "ZohoBooks.contacts.CREATE",
    "ZohoBooks.estimates.CREATE",
    "ZohoInventory.items.CREATE",
    "ZohoInventory.items.UPDATE",
)
SCOPES = READ_SCOPES + ALLOWED_WRITE_SCOPES
FORBIDDEN_SCOPE_PARTS = (".UPDATE", ".DELETE", ".ALL", "fullaccess")


class ZohoError(RuntimeError):
    pass


def validate_scopes(scopes: list[str] | tuple[str, ...]) -> None:
    for scope in scopes:
        if scope in ALLOWED_WRITE_SCOPES:
            continue
        lowered = scope.casefold()
        if any(part.casefold() in lowered for part in FORBIDDEN_SCOPE_PARTS):
            raise ZohoError(f"REFUSED: forbidden Zoho scope configured: {scope}")
        if scope.endswith(".READ"):
            continue
        raise ZohoError(f"REFUSED: uncommissioned Zoho write scope configured: {scope}")


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def dpapi_protect(data: bytes) -> bytes:
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
        ctypes.byref(in_blob), DPAPI_DESCRIPTION, None, None, None, 0x1, ctypes.byref(out_blob)
    )
    _ = in_buffer
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def dpapi_unprotect(data: bytes) -> bytes:
    in_blob, in_buffer = _blob(data)
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0x1, ctypes.byref(out_blob)
    )
    _ = in_buffer
    if not ok:
        raise ZohoError("The Zoho vault cannot be opened by this Windows user.")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(), "action": action, "evidence": evidence}
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record) + "\n")


def ensure_vault_dir() -> None:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    user = getpass.getuser()
    result = subprocess.run(
        ["icacls", str(VAULT_DIR), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F", "SYSTEM:(OI)(CI)F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ZohoError("Windows could not restrict the Zoho vault folder permissions.")


def save_vault(value: dict[str, Any]) -> None:
    ensure_vault_dir()
    VAULT_PATH.write_bytes(dpapi_protect(json.dumps(value).encode("utf-8")))


def load_vault() -> dict[str, Any]:
    if not VAULT_PATH.exists():
        raise ZohoError("Zoho is not connected. Run CONNECT_DADO_ZOHO.bat first.")
    try:
        return json.loads(dpapi_unprotect(VAULT_PATH.read_bytes()).decode("utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ZohoError("The Zoho credential vault is unreadable.") from exc


def redact(text: str, values: list[str]) -> str:
    for value in values:
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def token_post(fields: dict[str, str], secret_values: list[str]) -> dict[str, Any]:
    request = Request(
        ACCOUNTS_URL + "/oauth/v2/token",
        data=urlencode(fields).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = redact(exc.read().decode("utf-8", errors="replace"), secret_values)
        raise ZohoError(f"Zoho token exchange failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise ZohoError(f"Zoho Accounts could not be reached: {exc.reason}") from exc
    if result.get("error"):
        raise ZohoError("Zoho token exchange failed: " + redact(str(result.get("error")), secret_values))
    return result


def refresh_access_token(vault: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    vault = vault or load_vault()
    fields = {
        "refresh_token": str(vault["refresh_token"]),
        "client_id": str(vault["client_id"]),
        "client_secret": str(vault["client_secret"]),
        "grant_type": "refresh_token",
    }
    result = token_post(fields, list(fields.values()))
    access_token = str(result.get("access_token") or "")
    if not access_token:
        raise ZohoError("Zoho did not return an access token.")
    api_domain = str(result.get("api_domain") or vault.get("api_domain") or "")
    if api_domain.rstrip("/") != EXPECTED_API_DOMAIN:
        raise ZohoError("Zoho returned a non-Canadian API domain. Connection refused.")
    vault["api_domain"] = api_domain.rstrip("/")
    return access_token, vault


def api_get(access_token: str, api_domain: str, path: str) -> dict[str, Any]:
    if not path.startswith("/"):
        raise ZohoError("Zoho API path must begin with a slash.")
    request = Request(
        api_domain.rstrip("/") + path,
        headers={"Authorization": f"Zoho-oauthtoken {access_token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ZohoError(f"Zoho GET {path} failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise ZohoError(f"Zoho API could not be reached: {exc.reason}") from exc
    if result.get("code") not in (None, 0):
        raise ZohoError(f"Zoho GET {path} failed: {result.get('message') or result.get('code')}")
    return result


def frp_organization(organizations: list[dict[str, Any]]) -> dict[str, Any]:
    matches = []
    for organization in organizations:
        name = str(organization.get("name") or organization.get("organization_name") or "")
        normalized = "".join(character for character in name.casefold() if character.isalnum())
        if "frpdepot" in normalized:
            matches.append(organization)
    if len(matches) != 1:
        raise ZohoError(
            "Zoho did not return exactly one FRP Depot organization. Other organizations were not displayed."
        )
    return matches[0]


def organization_id(organization: dict[str, Any]) -> str:
    value = str(organization.get("organization_id") or "")
    if not value:
        raise ZohoError("The FRP Depot Zoho organization has no organization ID.")
    return value


def command_connect(_: argparse.Namespace) -> None:
    validate_scopes(SCOPES)
    print("FRP Depot Zoho restricted connection")
    print("Enter credentials only in this local window. They will not be displayed.")
    client_id = input("Zoho Self Client ID: ").strip()
    client_secret = getpass.getpass("Zoho Client Secret (hidden): ").strip()
    grant_code = getpass.getpass("Zoho one-time Grant Code (hidden): ").strip()
    if not client_id or not client_secret or not grant_code:
        raise ZohoError("Client ID, client secret, and grant code are all required.")
    fields = {
        "code": grant_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "scope": ",".join(SCOPES),
    }
    result = token_post(fields, [client_secret, grant_code])
    access_token = str(result.get("access_token") or "")
    refresh_token = str(result.get("refresh_token") or "")
    api_domain = str(result.get("api_domain") or "").rstrip("/")
    if not access_token or not refresh_token:
        raise ZohoError("Zoho did not return both access and refresh tokens.")
    if api_domain != EXPECTED_API_DOMAIN:
        raise ZohoError("Zoho returned a non-Canadian API domain. Connection refused.")

    books_result = api_get(access_token, api_domain, "/books/v3/organizations")
    inventory_result = api_get(access_token, api_domain, "/inventory/v1/organizations")
    books_org = frp_organization(books_result.get("organizations") or [])
    inventory_org = frp_organization(inventory_result.get("organizations") or [])
    books_id = organization_id(books_org)
    inventory_id = organization_id(inventory_org)
    books_name = str(books_org.get("name") or books_org.get("organization_name") or "FRP Depot")
    inventory_name = str(
        inventory_org.get("name") or inventory_org.get("organization_name") or "FRP Depot"
    )
    print(f"Books organization found: {books_name}")
    print(f"Inventory organization found: {inventory_name}")
    confirmation = input("Type YES if both are the FRP Depot organizations: ").strip()
    if confirmation != "YES":
        raise ZohoError("Connection cancelled. No Zoho credentials were saved.")
    vault = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "api_domain": api_domain,
        "accounts_url": ACCOUNTS_URL,
        "books_organization_id": books_id,
        "inventory_organization_id": inventory_id,
        "books_organization_name": books_name,
        "inventory_organization_name": inventory_name,
        "scopes": list(SCOPES),
        "connected_utc": datetime.now(timezone.utc).isoformat(),
    }
    save_vault(vault)
    append_receipt("zoho_connected_restricted_named_write_tools", str(VAULT_PATH))
    print("Zoho Books: CONNECTED AND VERIFIED")
    print("Zoho Inventory: CONNECTED AND VERIFIED RESTRICTED")
    print("Books writes: CUSTOMER CREATE + DRAFT ESTIMATE CREATE ONLY")
    print("Inventory writes: ITEM CREATE + ITEM NAME/SKU UPDATE THROUGH NAMED TOOL ONLY")
    print("Delete/stock-adjustment/order/invoice/send scopes: ABSENT")


def command_check(_: argparse.Namespace) -> None:
    vault = load_vault()
    scopes = [str(scope) for scope in vault.get("scopes") or []]
    validate_scopes(scopes)
    if not set(ALLOWED_WRITE_SCOPES).issubset(scopes):
        missing = sorted(set(ALLOWED_WRITE_SCOPES) - set(scopes))
        raise ZohoError(
            "The saved Zoho connection lacks newly commissioned scope(s): "
            + ", ".join(missing)
            + ". Generate a new grant code and run CONNECT_DADO_ZOHO.bat."
        )
    access_token, vault = refresh_access_token(vault)
    api_domain = str(vault["api_domain"])
    books_org = api_get(
        access_token,
        api_domain,
        f"/books/v3/organizations/{vault['books_organization_id']}",
    )
    inventory_items = api_get(
        access_token,
        api_domain,
        f"/inventory/v1/items?organization_id={vault['inventory_organization_id']}&page=1&per_page=1",
    )
    invoice_result = api_get(
        access_token,
        api_domain,
        f"/books/v3/invoices?organization_id={vault['books_organization_id']}&page=1&per_page=1",
    )
    save_vault(vault)
    append_receipt("zoho_restricted_connection_verified", str(VAULT_PATH))
    organization = books_org.get("organization") or {}
    print("Zoho Books connection: VERIFIED RESTRICTED")
    print("Zoho Inventory connection: VERIFIED RESTRICTED")
    print(f"Organization: {organization.get('name') or vault.get('books_organization_name')}")
    print(f"Books invoice read: VERIFIED ({len(invoice_result.get('invoices') or [])} sample row)")
    print(f"Inventory item read: VERIFIED ({len(inventory_items.get('items') or [])} sample row)")
    print("Books writes: CUSTOMER CREATE + DRAFT ESTIMATE CREATE ONLY")
    print("Inventory writes: ITEM CREATE + ITEM NAME/SKU UPDATE THROUGH NAMED TOOL ONLY")
    print("Delete/stock-adjustment/order/invoice/send scopes: ABSENT")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FRP Depot Zoho restricted connector")
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
    except (ZohoError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
