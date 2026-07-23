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
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import uuid

ROOT = Path(r"C:\FRPDepot")
FIT_PROFILE = ROOT / "Dado" / "30_Memory" / "fit_profile.md"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
VAULT_DIR = LOCALAPPDATA / "FRPDepot-Outlook"
CONFIG_PATH = VAULT_DIR / "config.json"
TOKEN_PATH = VAULT_DIR / "refresh_token.dpapi"
OFFICIAL_SIGNATURE_DIR = ROOT / "Dado" / "20_Working" / "outlook_signature"
OFFICIAL_SIGNATURE_BUNDLE = OFFICIAL_SIGNATURE_DIR / "official_signature_bundle.json"
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
FORBIDDEN_REPLY_DOMAINS = {"troydualam.com"}


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
            error_value = detail.get("error")
            error_code = error_value if isinstance(error_value, str) else ""
            nested_message = error_value.get("message") if isinstance(error_value, dict) else ""
            message = detail.get("error_description") or nested_message or body
        except json.JSONDecodeError:
            error_code = ""
            message = body
        code_prefix = f"{error_code}: " if error_code else ""
        raise OutlookError(
            f"Microsoft sign-in returned HTTP {exc.code}: {code_prefix}{message}"
        ) from exc
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
    marker = "- Rachad's standard email signature block"
    for index, line in enumerate(text.splitlines()):
        if line.startswith(marker):
            same_line = line.split(":", 1)[1].strip() if ":" in line else ""
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


def load_official_signature_bundle(path: Path = OFFICIAL_SIGNATURE_BUNDLE) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        html_path = Path(str(bundle["signature_html_path"]))
        signature_html = html_path.read_text(encoding="utf-8")
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise OutlookError("Official Outlook signature bundle is unreadable.") from exc
    if "Rachad Homsi" not in signature_html or "frpdepots.com" not in signature_html.casefold():
        raise OutlookError("Official Outlook signature bundle failed its identity check.")
    inline_attachments = bundle.get("inline_attachments") or []
    if not isinstance(inline_attachments, list):
        raise OutlookError("Official Outlook signature attachments are invalid.")
    return {
        "html": signature_html,
        "inline_attachments": inline_attachments,
        "source_message_id": bundle.get("source_message_id"),
        "source_sent_datetime": bundle.get("source_sent_datetime"),
    }


def plain_text_to_html(value: str) -> str:
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", value.strip()):
        lines = "<br>".join(html.escape(line) for line in paragraph.splitlines())
        paragraphs.append(f'<p style="font-family:Calibri,Arial,sans-serif;font-size:11pt">{lines}</p>')
    return "".join(paragraphs)


def message_address(field: dict[str, Any] | None) -> str:
    return str(((field or {}).get("emailAddress") or {}).get("address") or "").strip().casefold()


def recipient_addresses(values: list[dict[str, Any]] | None) -> list[str]:
    return [address for address in (message_address(value) for value in values or []) if address]


def message_participants(message: dict[str, Any]) -> set[str]:
    values = [message.get("from"), message.get("sender")]
    values.extend(message.get("toRecipients") or [])
    values.extend(message.get("ccRecipients") or [])
    values.extend(message.get("replyTo") or [])
    return {address for address in (message_address(value) for value in values) if address}


def assert_reply_participants_safe(addresses: set[str]) -> None:
    forbidden = sorted(
        address
        for address in addresses
        if any(address.endswith("@" + domain) for domain in FORBIDDEN_REPLY_DOMAINS)
    )
    if forbidden:
        raise OutlookError(
            "Reply All blocked by the company wall; use only the sanctioned inter-company relay."
        )


def message_datetime(message: dict[str, Any]) -> str:
    return str(
        message.get("receivedDateTime")
        or message.get("sentDateTime")
        or message.get("createdDateTime")
        or ""
    )


def conversation_messages(access_token: str, conversation_id: str) -> list[dict[str, Any]]:
    safe_conversation_id = conversation_id.replace("'", "''")
    query = urlencode(
        {
            "$filter": f"conversationId eq '{safe_conversation_id}'",
            "$select": (
                "id,conversationId,subject,from,sender,toRecipients,ccRecipients,replyTo,"
                "receivedDateTime,sentDateTime,createdDateTime,lastModifiedDateTime,isDraft"
            ),
            "$top": "100",
        }
    )
    result = graph_request(access_token, "GET", "/me/messages?" + query)
    if result.get("@odata.nextLink"):
        raise OutlookError("Reply All blocked: the conversation exceeds the 100-message safety limit.")
    return list(result.get("value") or [])


def latest_non_draft(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [message for message in messages if message.get("isDraft") is not True]
    return max(candidates, key=message_datetime) if candidates else None


def resolve_source_message(access_token: str, match: str) -> dict[str, Any]:
    """Resolve a reply target by a short, reliable search term instead of a raw
    ~150-char Graph message id. Hand-carrying that id through a JSON input file is
    what corrupts it and produces the 'malformed id' HTTP 400 - the encoding and
    the general /me/messages/{id} endpoint are both fine with a clean id.

    Returns the newest EXTERNAL, non-draft message whose sender address or subject
    contains *match* (case-insensitive). Raises with a candidate list when the term
    is empty, matches nothing, or spans more than one conversation."""
    needle = str(match or "").strip().casefold()
    if not needle:
        raise OutlookError("Reply All resolve: source_match is empty.")
    query = (
        "/me/messages?$top=50&$orderby="
        + quote("receivedDateTime desc", safe="")
        + "&$select="
        + quote("id,conversationId,subject,from,sender,receivedDateTime,isDraft", safe="")
    )
    messages = graph_request(access_token, "GET", query).get("value") or []
    hits: list[dict[str, Any]] = []
    for message in messages:
        if message.get("isDraft") is True:
            continue
        sender = message_address(message.get("from")) or message_address(message.get("sender"))
        if not sender or sender.endswith("@frpdepots.com"):
            continue  # external senders only
        subject = str(message.get("subject") or "").casefold()
        if needle in sender or needle in subject:
            hits.append(message)
    if not hits:
        raise OutlookError(
            f"Reply All resolve: no external message in the recent mailbox matches '{match}'."
        )
    newest_by_conversation: dict[str, dict[str, Any]] = {}
    for message in hits:
        cid = str(message.get("conversationId") or "")
        if cid not in newest_by_conversation or message_datetime(message) > message_datetime(
            newest_by_conversation[cid]
        ):
            newest_by_conversation[cid] = message
    if len(newest_by_conversation) > 1:
        listing = "; ".join(
            f'"{str(m.get("subject") or "")[:50]}" from '
            f'{message_address(m.get("from")) or message_address(m.get("sender"))}'
            for m in sorted(newest_by_conversation.values(), key=message_datetime, reverse=True)[:5]
        )
        raise OutlookError(
            f"Reply All resolve: '{match}' matches {len(newest_by_conversation)} conversations - "
            f"use a more specific source_match. Candidates: {listing}"
        )
    return max(hits, key=message_datetime)


def find_standalone_drafts(access_token: str, recipient_address: str) -> list[dict[str, Any]]:
    """Draft messages addressed (To or Cc) to *recipient_address*. Used to locate an
    obsolete standalone draft to supersede without hand-carrying its id either."""
    target = str(recipient_address or "").strip().casefold()
    if not target:
        return []
    query = (
        "/me/mailFolders/drafts/messages?$top=50&$orderby="
        + quote("lastModifiedDateTime desc", safe="")
        + "&$select="
        + quote("id,subject,toRecipients,ccRecipients,conversationId,isDraft,createdDateTime", safe="")
    )
    drafts = graph_request(access_token, "GET", query).get("value") or []
    matches: list[dict[str, Any]] = []
    for draft in drafts:
        if draft.get("isDraft") is not True:
            continue
        recipients = recipient_addresses(draft.get("toRecipients")) + recipient_addresses(
            draft.get("ccRecipients")
        )
        if target in recipients:
            matches.append(draft)
    return matches


def add_official_inline_attachments(
    access_token: str,
    draft_id: str,
    signature_bundle: dict[str, Any],
) -> int:
    encoded_id = quote(draft_id, safe="")
    expected_content_ids: set[str] = set()
    for attachment in signature_bundle["inline_attachments"]:
        attachment_path = Path(str(attachment.get("path") or ""))
        try:
            content_bytes = base64.b64encode(attachment_path.read_bytes()).decode("ascii")
        except OSError as exc:
            raise OutlookError(f"Official signature image is unreadable: {attachment_path}") from exc
        content_id = str(attachment.get("content_id") or "")
        attachment_payload = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": str(attachment.get("name") or attachment_path.name),
            "contentType": str(attachment.get("content_type") or "application/octet-stream"),
            "contentBytes": content_bytes,
            "contentId": content_id,
            "isInline": True,
        }
        graph_request(
            access_token,
            "POST",
            f"/me/messages/{encoded_id}/attachments",
            attachment_payload,
        )
        if content_id:
            expected_content_ids.add(content_id)

    verified = graph_request(access_token, "GET", f"/me/messages/{encoded_id}?$select=id,isDraft")
    if verified.get("isDraft") is not True:
        raise OutlookError("Microsoft Graph could not re-verify the signed message as a draft.")
    # Microsoft documents that hasAttachments stays false for inline-only attachments.
    attachment_rows = graph_request(
        access_token,
        "GET",
        f"/me/messages/{encoded_id}/attachments",
    ).get("value") or []
    found_content_ids = {
        str(row.get("contentId") or "")
        for row in attachment_rows
        if row.get("isInline") is True
    }
    if not expected_content_ids.issubset(found_content_ids):
        raise OutlookError("Microsoft Graph did not confirm the inline signature logo attachment.")
    return len(signature_bundle["inline_attachments"])


def command_draft(args: argparse.Namespace) -> None:
    signature_bundle = load_official_signature_bundle()
    plain_signature = fit_profile_signature()
    if not signature_bundle and not plain_signature:
        raise OutlookError(
            "Draft blocked: Rachad's standard email signature is missing from Outlook and fit_profile.md."
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
    body_html = str(draft_input.get("body_html") or "").strip()
    if not subject or not (body_text or body_html):
        raise OutlookError("Draft input needs a subject and body_text or body_html.")

    if signature_bundle:
        message_body = body_html or plain_text_to_html(body_text)
        full_body = message_body + '<br><br><div class="frp-depots-official-signature">' + str(signature_bundle["html"]) + "</div>"
        content_type = "HTML"
    else:
        full_body = body_text + "\n\n" + plain_signature.rstrip()
        content_type = "Text"

    def graph_recipients(values: list[str]) -> list[dict[str, dict[str, str]]]:
        return [{"emailAddress": {"address": str(value).strip()}} for value in values]

    payload = {
        "subject": subject,
        "body": {"contentType": content_type, "content": full_body},
        "toRecipients": graph_recipients(recipients),
        "ccRecipients": graph_recipients(draft_input.get("cc") or []),
    }
    access_token, _ = refresh_access_token()
    created = graph_request(access_token, "POST", "/me/messages", payload)
    draft_id = str(created.get("id") or "")
    if not draft_id or created.get("isDraft") is not True:
        raise OutlookError("Microsoft Graph did not confirm that the message is a draft.")

    inline_count = 0
    if signature_bundle:
        inline_count = add_official_inline_attachments(access_token, draft_id, signature_bundle)

    append_receipt("outlook_draft_created", draft_id)
    print(
        json.dumps(
            {
                "status": "DRAFT_CREATED_NOT_SENT",
                "id": draft_id,
                "to": recipients,
                "cc": draft_input.get("cc") or [],
                "subject": subject,
                "body_text": body_text,
                "content_type": content_type,
                "official_signature_source_message_id": (signature_bundle or {}).get("source_message_id"),
                "official_signature_source_sent_datetime": (signature_bundle or {}).get("source_sent_datetime"),
                "inline_signature_images": inline_count,
            },
            indent=2,
        )
    )


def command_reply_all(args: argparse.Namespace) -> None:
    signature_bundle = load_official_signature_bundle()
    if not signature_bundle:
        raise OutlookError("Reply All blocked: the official HTML Outlook signature bundle is missing.")
    try:
        draft_input = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OutlookError("Reply All input must be a readable JSON file.") from exc

    # Identify the message to reply to WITHOUT hand-carrying a fragile ~150-char
    # Graph id: prefer a short `source_match` (sender address or subject substring)
    # that the tool resolves to the exact id itself. A clean `source_message_id`
    # still works for callers that already hold one.
    source_message_id = str(draft_input.get("source_message_id") or "").strip()
    source_match = str(draft_input.get("source_match") or "").strip()

    # Body may be inline (`body_text`/`body_html`) or read raw from a file
    # (`body_text_file`/`body_html_file`) - the file form avoids corruption when the
    # body is long or was produced by a line-numbering reader.
    body_text = str(draft_input.get("body_text") or "").rstrip()
    body_html = str(draft_input.get("body_html") or "").strip()
    for field, is_html in (("body_text_file", False), ("body_html_file", True)):
        path_value = str(draft_input.get(field) or "").strip()
        if not path_value:
            continue
        try:
            content = Path(path_value).read_text(encoding="utf-8")
        except OSError as exc:
            raise OutlookError(f"Reply All input: cannot read {field} '{path_value}'.") from exc
        if is_html and not body_html:
            body_html = content.strip()
        elif not is_html and not body_text:
            body_text = content.rstrip()

    if not (source_message_id or source_match) or not (body_text or body_html):
        raise OutlookError(
            "Reply All input needs source_message_id or source_match, plus body_text/body_html "
            "(or body_text_file/body_html_file)."
        )

    superseded_draft_id = str(draft_input.get("superseded_draft_id") or "").strip()
    superseded_subject = str(draft_input.get("superseded_subject") or "").strip()
    replace_standalone = bool(draft_input.get("replace_standalone"))
    if bool(superseded_draft_id) != bool(superseded_subject):
        raise OutlookError(
            "Replacing a draft requires both superseded_draft_id and superseded_subject."
        )

    access_token, _ = refresh_access_token()
    if not source_message_id:
        source_message_id = str(resolve_source_message(access_token, source_match).get("id") or "")
        if not source_message_id:
            raise OutlookError("Reply All resolve: could not determine a source message id.")
    encoded_source_id = quote(source_message_id, safe="")
    source = graph_request(
        access_token,
        "GET",
        (
            f"/me/messages/{encoded_source_id}?$select=id,conversationId,subject,from,sender,"
            "toRecipients,ccRecipients,replyTo,receivedDateTime,sentDateTime,createdDateTime,isDraft"
        ),
    )
    if source.get("isDraft") is True:
        raise OutlookError("Reply All blocked: the selected source message is a draft.")
    conversation_id = str(source.get("conversationId") or "")
    if not conversation_id:
        raise OutlookError("Reply All blocked: the source message has no Outlook conversation ID.")
    source_sender = message_address(source.get("from")) or message_address(source.get("sender"))
    if not source_sender or source_sender.endswith("@frpdepots.com"):
        raise OutlookError("Reply All blocked: select the latest external message in the thread.")
    assert_reply_participants_safe(message_participants(source))

    # Auto-detect the obsolete standalone draft to supersede (so its id, too, need
    # not be hand-carried). Only a draft OUTSIDE this conversation counts as the
    # standalone one; same-thread drafts are handled by the duplicate-draft guard.
    if replace_standalone and not superseded_draft_id:
        standalones = [
            draft
            for draft in find_standalone_drafts(access_token, source_sender)
            if str(draft.get("conversationId") or "") != conversation_id
        ]
        if len(standalones) == 1:
            superseded_draft_id = str(standalones[0].get("id") or "")
            superseded_subject = str(standalones[0].get("subject") or "")
        elif len(standalones) > 1:
            raise OutlookError(
                "Reply All: more than one standalone draft is addressed to this recipient; "
                "set superseded_draft_id explicitly to choose one."
            )
        # zero standalone drafts -> nothing to supersede; proceed

    messages = conversation_messages(access_token, conversation_id)
    latest = latest_non_draft(messages)
    if not latest or str(latest.get("id") or "") != source_message_id:
        raise OutlookError("Reply All blocked: a newer non-draft message exists in the live thread.")
    same_response_drafts = [
        message
        for message in messages
        if message.get("isDraft") is True
        and message_datetime(message) >= message_datetime(source)
    ]
    if same_response_drafts:
        raise OutlookError("Reply All blocked: an active reply draft already exists for this response.")

    if superseded_draft_id:
        encoded_superseded_id = quote(superseded_draft_id, safe="")
        superseded = graph_request(
            access_token,
            "GET",
            f"/me/messages/{encoded_superseded_id}?$select=id,isDraft,subject",
        )
        if superseded.get("isDraft") is not True or superseded.get("subject") != superseded_subject:
            raise OutlookError("The named superseded Outlook draft did not pass its identity check.")

    created = graph_request(
        access_token,
        "POST",
        f"/me/messages/{encoded_source_id}/createReplyAll",
    )
    draft_id = str(created.get("id") or "")
    if not draft_id or created.get("isDraft") is not True:
        raise OutlookError("Microsoft Graph did not confirm the Reply All message as a draft.")
    encoded_draft_id = quote(draft_id, safe="")
    reply_state = graph_request(
        access_token,
        "GET",
        (
            f"/me/messages/{encoded_draft_id}?$select=id,isDraft,conversationId,subject,body,"
            "toRecipients,ccRecipients,bccRecipients"
        ),
    )
    generated_to = recipient_addresses(reply_state.get("toRecipients"))
    generated_cc = recipient_addresses(reply_state.get("ccRecipients"))
    generated_bcc = recipient_addresses(reply_state.get("bccRecipients"))
    if not generated_to or generated_bcc:
        raise OutlookError("Reply All blocked: Microsoft generated unsafe recipient fields.")
    if len(generated_to + generated_cc) != len(set(generated_to + generated_cc)):
        raise OutlookError("Reply All blocked: Microsoft generated duplicate recipients.")
    assert_reply_participants_safe(set(generated_to + generated_cc))

    quoted_body = str((reply_state.get("body") or {}).get("content") or "")
    quoted_type = str((reply_state.get("body") or {}).get("contentType") or "")
    quoted_html = quoted_body if quoted_type.casefold() == "html" else plain_text_to_html(quoted_body)
    new_body_html = body_html or plain_text_to_html(body_text)
    full_body = (
        new_body_html
        + '<br><br><div class="frp-depots-official-signature">'
        + str(signature_bundle["html"])
        + "</div><br><br>"
        + quoted_html
    )
    graph_request(
        access_token,
        "PATCH",
        f"/me/messages/{encoded_draft_id}",
        {"body": {"contentType": "HTML", "content": full_body}},
    )
    inline_count = add_official_inline_attachments(
        access_token,
        draft_id,
        signature_bundle,
    )

    final = graph_request(
        access_token,
        "GET",
        (
            f"/me/messages/{encoded_draft_id}?$select=id,isDraft,conversationId,subject,body,"
            "toRecipients,ccRecipients,bccRecipients"
        ),
    )
    final_body = str((final.get("body") or {}).get("content") or "")
    final_to = recipient_addresses(final.get("toRecipients"))
    final_cc = recipient_addresses(final.get("ccRecipients"))
    final_bcc = recipient_addresses(final.get("bccRecipients"))
    checks = {
        "is_draft": final.get("isDraft") is True,
        "same_conversation": final.get("conversationId") == conversation_id,
        "subject_preserved": final.get("subject") == reply_state.get("subject"),
        "to_preserved": set(final_to) == set(generated_to),
        "cc_preserved": set(final_cc) == set(generated_cc),
        "bcc_empty": not final_bcc,
        "quoted_history_preserved": quoted_html in final_body,
        "official_signature_once": final_body.count('class="frp-depots-official-signature"') == 1,
    }
    if not all(checks.values()):
        raise OutlookError("Reply All draft failed final verification: " + json.dumps(checks))
    assert_reply_participants_safe(set(final_to + final_cc))

    latest_after = latest_non_draft(conversation_messages(access_token, conversation_id))
    if not latest_after or str(latest_after.get("id") or "") != source_message_id:
        raise OutlookError("Reply All draft blocked: a newer source message arrived during drafting.")

    if superseded_draft_id:
        graph_request(
            access_token,
            "DELETE",
            f"/me/messages/{quote(superseded_draft_id, safe='')}",
        )
        append_receipt("outlook_superseded_draft_removed", superseded_draft_id)

    append_receipt("outlook_reply_all_draft_created", draft_id)
    print(
        json.dumps(
            {
                "status": "REPLY_ALL_DRAFT_CREATED_NOT_SENT",
                "id": draft_id,
                "source_message_id": source_message_id,
                "conversation_id": conversation_id,
                "to": final_to,
                "cc": final_cc,
                "subject": final.get("subject"),
                "body_text": body_text,
                "quoted_history_preserved": True,
                "official_signature_source_message_id": signature_bundle.get("source_message_id"),
                "inline_signature_images": inline_count,
                "superseded_draft_removed": bool(superseded_draft_id),
                "checks": checks,
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
    reply_all = commands.add_parser(
        "reply-all",
        help="Create a Reply All draft in the live Outlook conversation from JSON",
    )
    reply_all.add_argument("--input", required=True)
    reply_all.set_defaults(func=command_reply_all)
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
