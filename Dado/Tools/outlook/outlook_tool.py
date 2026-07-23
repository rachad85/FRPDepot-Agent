#!/usr/bin/env python
"""FRP Depot Outlook connector.

Security boundaries:
- Delegated Microsoft Graph access only.
- Requests Mail.ReadWrite, Calendars.Read, User.Read, and offline_access.
- Never requests or accepts mail-sending permission.
- Persists only a DPAPI-encrypted refresh token under LOCALAPPDATA.
- Provides no send command or send endpoint.
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
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

ROOT = Path(r"C:\FRPDepot")
FIT_PROFILE = ROOT / "Dado" / "30_Memory" / "fit_profile.md"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
VAULT_DIR = LOCALAPPDATA / "FRPDepot-Outlook"
CONFIG_PATH = VAULT_DIR / "config.json"
TOKEN_PATH = VAULT_DIR / "refresh_token.dpapi"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
REQUESTED_SCOPES = (
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Calendars.Read",
    "offline_access",
)
REQUIRED_TOKEN_SCOPES = {"User.Read", "Mail.ReadWrite", "Calendars.Read"}
FORBIDDEN_TOKEN_SCOPE = "Mail.Send"
DPAPI_DESCRIPTION = "FRP Depot Outlook refresh token"


class OutlookError(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise OutlookError("Windows DPAPI is required for the Outlook token vault.")
    in_blob, in_buffer = _blob(data)
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        DPAPI_DESCRIPTION,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
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
        raise OutlookError("Windows DPAPI is required for the Outlook token vault.")
    in_blob, in_buffer = _blob(data)
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    _ = in_buffer
    if not ok:
        raise OutlookError("The Outlook token vault cannot be opened by this Windows user.")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "evidence": evidence,
    }
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def ensure_vault_dir() -> None:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        user = getpass.getuser()
        result = subprocess.run(
            [
                "icacls",
                str(VAULT_DIR),
                "/inheritance:r",
                "/grant:r",
                f"{user}:(OI)(CI)F",
                "SYSTEM:(OI)(CI)F",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise OutlookError(
                "Windows could not restrict the Outlook vault folder. "
                "Fix the folder permissions before connecting."
            )


def validate_guid(value: str, label: str) -> str:
    candidate = value.strip()
    try:
        return str(uuid.UUID(candidate))
    except ValueError as exc:
        raise OutlookError(f"{label} must be the GUID copied from Microsoft Entra.") from exc


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise OutlookError(
            f"Outlook app settings are missing. Run SET_DADO_OUTLOOK_APP.bat first."
        )
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OutlookError("Outlook app settings are unreadable. Run setup again.") from exc
    config["tenant_id"] = validate_guid(str(config.get("tenant_id", "")), "Tenant ID")
    config["client_id"] = validate_guid(str(config.get("client_id", "")), "Client ID")
    return config


def save_config(config: dict[str, Any]) -> None:
    ensure_vault_dir()
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def save_refresh_token(refresh_token: str) -> None:
    if not refresh_token:
        raise OutlookError("Microsoft did not return an offline refresh token.")
    ensure_vault_dir()
    TOKEN_PATH.write_bytes(dpapi_protect(refresh_token.encode("utf-8")))


def load_refresh_token() -> str:
    if not TOKEN_PATH.exists():
        raise OutlookError("Outlook is not connected. Run CONNECT_DADO_OUTLOOK.bat first.")
    return dpapi_unprotect(TOKEN_PATH.read_bytes()).decode("utf-8")


def http_form(url: str, fields: dict[str, str]) -> dict[str, Any]:
    request = Request(
        url,
        data=urlencode(fields).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
            message = detail.get("error_description") or detail.get("error", {}).get("message") or body
        except json.JSONDecodeError:
            message = body
        raise OutlookError(f"Microsoft sign-in returned HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise OutlookError(f"Microsoft sign-in could not be reached: {exc.reason}") from exc


def decode_token_scopes(access_token: str) -> set[str]:
    try:
        payload_segment = access_token.split(".")[1]
        payload_segment += "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_segment).decode("utf-8"))
        scopes = set(str(payload.get("scp", "")).split())
    except (IndexError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OutlookError("Microsoft returned an access token with unreadable permission claims.") from exc
    if FORBIDDEN_TOKEN_SCOPE in scopes:
        raise OutlookError(
            "REFUSED: Microsoft returned Mail.Send permission. Dado is drafts-only; remove that permission."
        )
    missing = REQUIRED_TOKEN_SCOPES - scopes
    if missing:
        raise OutlookError("Microsoft token is missing: " + ", ".join(sorted(missing)))
    return scopes


def token_endpoint(config: dict[str, Any]) -> str:
    return f"https://login.microsoftonline.com/{config['tenant_id']}/oauth2/v2.0/token"


def connect_interactively(config: dict[str, Any]) -> tuple[str, set[str]]:
    device_url = (
        f"https://login.microsoftonline.com/{config['tenant_id']}/oauth2/v2.0/devicecode"
    )
    device = http_form(
        device_url,
        {"client_id": config["client_id"], "scope": " ".join(REQUESTED_SCOPES)},
    )
    user_code = device.get("user_code")
    verification_uri = device.get("verification_uri") or "https://microsoft.com/devicelogin"
    if not user_code or not device.get("device_code"):
        raise OutlookError("Microsoft did not return a usable device sign-in code.")

    print("\nMicrosoft sign-in is ready.")
    print(f"1. Open: {verification_uri}")
    print(f"2. Enter the one-time code shown here: {user_code}")
    print("3. Sign in only to the FRP Depot mailbox.")
    print("4. Approve only Mail.ReadWrite, Calendars.Read, and User.Read.")
    print("\nWaiting for sign-in...\n")

    interval = max(int(device.get("interval", 5)), 5)
    deadline = time.monotonic() + int(device.get("expires_in", 900))
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            token = http_form(
                token_endpoint(config),
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": config["client_id"],
                    "device_code": device["device_code"],
                },
            )
        except OutlookError as exc:
            text = str(exc)
            if "authorization_pending" in text:
                continue
            if "slow_down" in text:
                interval += 5
                continue
            raise
        access_token = str(token.get("access_token", ""))
        refresh_token = str(token.get("refresh_token", ""))
        scopes = decode_token_scopes(access_token)
        return refresh_token, scopes
    raise OutlookError("The Microsoft device code expired. Run the connection button again.")


def refresh_access_token() -> tuple[str, set[str]]:
    config = load_config()
    refresh_token = load_refresh_token()
    token = http_form(
        token_endpoint(config),
        {
            "grant_type": "refresh_token",
            "client_id": config["client_id"],
            "refresh_token": refresh_token,
            "scope": " ".join(REQUESTED_SCOPES),
        },
    )
    access_token = str(token.get("access_token", ""))
    scopes = decode_token_scopes(access_token)
    rotated = str(token.get("refresh_token", ""))
    if rotated:
        save_refresh_token(rotated)
    return access_token, scopes


def graph_request(
    access_token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(GRAPH_BASE + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=45) as response:
            body = response.read()
            return json.loads(body.decode("utf-8")) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
            message = detail.get("error", {}).get("message") or body
        except json.JSONDecodeError:
            message = body
        raise OutlookError(f"Microsoft Graph {method} {path} failed with HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise OutlookError(f"Microsoft Graph could not be reached: {exc.reason}") from exc


def mailbox_address(me: dict[str, Any]) -> str:
    return str(me.get("mail") or me.get("userPrincipalName") or "").strip()


def fit_profile_signature(path: Path = FIT_PROFILE) -> str:
    text = path.read_text(encoding="utf-8")
    marker = "- Rachad's standard email signature block (paste exactly):"
    for index, line in enumerate(text.splitlines()):
        if line.startswith(marker):
            same_line = line[len(marker) :].strip()
            if same_line:
                return same_line
            collected: list[str] = []
            for following in text.splitlines()[index + 1 :]:
                if following.startswith("## ") or (
                    following.startswith("-") and not following.startswith("  ")
                ):
                    break
                if following.startswith("  "):
                    collected.append(following[2:])
                elif following.strip() and collected:
                    collected.append(following)
            return "\n".join(collected).strip()
    return ""


def record_mailbox_in_fit_profile(email: str) -> None:
    text = FIT_PROFILE.read_text(encoding="utf-8")
    pattern = r"(?m)^- Rachad's FRP Depot email address:.*$"
    replacement = f"- Rachad's FRP Depot email address: {email} (verified by Microsoft Graph)"
    updated, count = re.subn(pattern, replacement, text, count=1)
    if count != 1:
        raise OutlookError("Could not record the verified mailbox in the FRP Depot fit profile.")
    FIT_PROFILE.write_text(updated, encoding="utf-8")
    append_receipt("fit_profile_mailbox_recorded", str(FIT_PROFILE))


def command_configure(_: argparse.Namespace) -> None:
    print("FRP Depot Outlook app settings")
    print("These are identifiers, not a password or client secret.")
    tenant_id = validate_guid(input("Directory (tenant) ID: "), "Tenant ID")
    client_id = validate_guid(input("Application (client) ID: "), "Client ID")
    save_config({"tenant_id": tenant_id, "client_id": client_id})
    append_receipt("outlook_app_configured", str(CONFIG_PATH))
    print(f"Saved locally: {CONFIG_PATH}")
    print("Next: run CONNECT_DADO_OUTLOOK.bat")


def command_connect(_: argparse.Namespace) -> None:
    config = load_config()
    refresh_token, scopes = connect_interactively(config)
    token = http_form(
        token_endpoint(config),
        {
            "grant_type": "refresh_token",
            "client_id": config["client_id"],
            "refresh_token": refresh_token,
            "scope": " ".join(REQUESTED_SCOPES),
        },
    )
    access_token = str(token.get("access_token", ""))
    scopes = decode_token_scopes(access_token)
    me = graph_request(
        access_token,
        "GET",
        "/me?$select=displayName,mail,userPrincipalName",
    )
    email = mailbox_address(me)
    print(f"Signed in mailbox: {email}")
    confirmation = input("Type YES if this is the FRP Depot mailbox: ").strip()
    if confirmation != "YES":
        raise OutlookError("Connection cancelled. No Outlook token was saved.")
    save_refresh_token(str(token.get("refresh_token") or refresh_token))
    config["mailbox"] = email
    config["display_name"] = str(me.get("displayName") or "")
    save_config(config)
    record_mailbox_in_fit_profile(email)
    append_receipt("outlook_connected", str(TOKEN_PATH))
    print("Outlook connected: VERIFIED")
    print("Mailbox read permission: PRESENT")
    print("Calendar read permission: PRESENT")
    print("Draft permission: PRESENT")
    print("Send permission: ABSENT")
    print("Granted scopes: " + ", ".join(sorted(scopes)))


def command_check(_: argparse.Namespace) -> None:
    access_token, scopes = refresh_access_token()
    me = graph_request(
        access_token,
        "GET",
        "/me?$select=displayName,mail,userPrincipalName",
    )
    inbox = graph_request(
        access_token,
        "GET",
        "/me/mailFolders/inbox?$select=displayName,totalItemCount,unreadItemCount",
    )
    graph_request(access_token, "GET", "/me/events?$top=1&$select=id")
    config = load_config()
    expected = str(config.get("mailbox") or "").casefold()
    actual = mailbox_address(me).casefold()
    if expected and actual != expected:
        raise OutlookError(
            f"Mailbox mismatch: the vault expects {expected}, but Microsoft returned {actual}."
        )
    append_receipt("outlook_connection_verified", str(TOKEN_PATH))
    print("Outlook connection: VERIFIED")
    print(f"Mailbox: {mailbox_address(me)}")
    print(f"Inbox total: {inbox.get('totalItemCount', 'unknown')}")
    print(f"Inbox unread: {inbox.get('unreadItemCount', 'unknown')}")
    print("Calendar read: VERIFIED")
    print("Draft permission: PRESENT")
    print("Send permission: ABSENT")
    print("Granted scopes: " + ", ".join(sorted(scopes)))


def command_unread(args: argparse.Namespace) -> None:
    access_token, _ = refresh_access_token()
    limit = min(max(args.limit, 1), 50)
    query = (
        f"/me/mailFolders/inbox/messages?$filter=isRead%20eq%20false&$top={limit}"
        "&$select=id,subject,from,receivedDateTime,hasAttachments,importance"
        "&$orderby=receivedDateTime%20desc"
    )
    result = graph_request(access_token, "GET", query)
    messages: list[dict[str, Any]] = []
    for item in result.get("value", []):
        sender = item.get("from", {}).get("emailAddress", {})
        messages.append(
            {
                "id": item.get("id"),
                "received": item.get("receivedDateTime"),
                "from_name": sender.get("name"),
                "from_address": sender.get("address"),
                "subject": item.get("subject"),
                "importance": item.get("importance"),
                "has_attachments": item.get("hasAttachments"),
            }
        )
    print(json.dumps({"mailbox": load_config().get("mailbox"), "unread": messages}, indent=2))


def command_draft(args: argparse.Namespace) -> None:
    signature = fit_profile_signature()
    if not signature:
        raise OutlookError(
            "Draft blocked: Rachad's standard email signature is missing from fit_profile.md."
        )
    try:
        draft_input = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OutlookError("Draft input must be a readable JSON file.") from exc
    recipients = draft_input.get("to") or []
    if not isinstance(recipients, list) or not recipients:
        raise OutlookError("Draft input needs at least one To address.")
    subject = str(draft_input.get("subject") or "").strip()
    body_text = str(draft_input.get("body_text") or "").rstrip()
    if not subject or not body_text:
        raise OutlookError("Draft input needs both subject and body_text.")
    full_body = body_text + "\n\n" + signature.rstrip()

    def graph_recipients(values: list[str]) -> list[dict[str, dict[str, str]]]:
        return [{"emailAddress": {"address": str(value).strip()}} for value in values]

    payload = {
        "subject": subject,
        "body": {"contentType": "Text", "content": full_body},
        "toRecipients": graph_recipients(recipients),
        "ccRecipients": graph_recipients(draft_input.get("cc") or []),
    }
    access_token, _ = refresh_access_token()
    created = graph_request(access_token, "POST", "/me/messages", payload)
    draft_id = str(created.get("id") or "")
    if not draft_id or created.get("isDraft") is not True:
        raise OutlookError("Microsoft Graph did not confirm that the message is a draft.")
    append_receipt("outlook_draft_created", draft_id)
    print(
        json.dumps(
            {
                "status": "DRAFT_CREATED_NOT_SENT",
                "id": draft_id,
                "to": recipients,
                "cc": draft_input.get("cc") or [],
                "subject": subject,
                "body": full_body,
            },
            indent=2,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FRP Depot Outlook read/draft-only connector")
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure", help="Store Microsoft app identifiers")
    configure.set_defaults(func=command_configure)
    connect = commands.add_parser("connect", help="Connect the FRP Depot mailbox")
    connect.set_defaults(func=command_connect)
    check = commands.add_parser("check", help="Verify mailbox and calendar access")
    check.set_defaults(func=command_check)
    unread = commands.add_parser("unread", help="List unread inbox messages")
    unread.add_argument("--limit", type=int, default=10)
    unread.set_defaults(func=command_unread)
    draft = commands.add_parser("draft", help="Create an Outlook draft from JSON")
    draft.add_argument("--input", required=True)
    draft.set_defaults(func=command_draft)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
        return 0
    except (OutlookError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
