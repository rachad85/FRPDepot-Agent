#!/usr/bin/env python
"""Export the FRP Depot Outlook mailbox for a read-only operational audit.

This script reads messages and attachments through Microsoft Graph. It does not
update, move, delete, draft, or send any mailbox item.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import outlook_tool

ROOT = Path(r"C:\FRPDepot")
AUDIT_ROOT = ROOT / "Dado" / "20_Working" / "outlook_mailbox_audit"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
FORBIDDEN_PARTICIPANT_DOMAINS = {"troydualam.com"}


class AuditError(RuntimeError):
    pass


def has_forbidden_participant(message: dict[str, Any]) -> bool:
    addresses: list[str] = []
    for field in ("from", "sender"):
        address = ((message.get(field) or {}).get("emailAddress") or {}).get("address")
        if address:
            addresses.append(str(address))
    for field in ("toRecipients", "ccRecipients", "replyTo"):
        for recipient in message.get(field) or []:
            address = (recipient.get("emailAddress") or {}).get("address")
            if address:
                addresses.append(str(address))
    return any(
        address.casefold().rsplit("@", 1)[-1] in FORBIDDEN_PARTICIPANT_DOMAINS
        for address in addresses
        if "@" in address
    )


def safe_detail(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
        return str(payload.get("error", {}).get("message") or text)
    except (json.JSONDecodeError, AttributeError):
        return text


def graph_get(
    token: str,
    url_or_path: str,
    *,
    as_bytes: bool = False,
    prefer_text_body: bool = False,
) -> Any:
    url = url_or_path if url_or_path.startswith("https://") else GRAPH_BASE + url_or_path
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if prefer_text_body:
        headers["Prefer"] = 'outlook.body-content-type="text"'
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=90) as response:
            data = response.read()
            if as_bytes:
                return data
            return json.loads(data.decode("utf-8")) if data else {}
    except HTTPError as exc:
        detail = safe_detail(exc.read())
        raise AuditError(f"Microsoft Graph GET failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise AuditError(f"Microsoft Graph could not be reached: {exc.reason}") from exc


def paged_values(
    token: str,
    url_or_path: str,
    *,
    prefer_text_body: bool = False,
) -> Iterator[dict[str, Any]]:
    next_url: str | None = url_or_path
    while next_url:
        page = graph_get(token, next_url, prefer_text_body=prefer_text_body)
        for item in page.get("value", []):
            yield item
        next_url = page.get("@odata.nextLink")


def collect_folders(token: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    selected = "id,displayName,parentFolderId,childFolderCount,totalItemCount,unreadItemCount"
    queue = [f"/me/mailFolders?includeHiddenFolders=true&$top=100&$select={selected}"]
    folders: list[dict[str, Any]] = []
    seen: set[str] = set()
    while queue:
        endpoint = queue.pop(0)
        for folder in paged_values(token, endpoint):
            folder_id = str(folder.get("id") or "")
            if not folder_id or folder_id in seen:
                continue
            seen.add(folder_id)
            folders.append(folder)
            if int(folder.get("childFolderCount") or 0) > 0:
                queue.append(
                    f"/me/mailFolders/{quote(folder_id, safe='')}/childFolders"
                    f"?includeHiddenFolders=true&$top=100&$select={selected}"
                )
    return folders, {str(item["id"]): str(item.get("displayName") or "") for item in folders}


def clean_filename(name: str, fallback: str) -> str:
    cleaned = re.sub(r"[<>:\\|?*\x00-\x1f]", "_", Path(name).name).strip(" .")
    return cleaned[:180] or fallback


def extension_for(name: str, content_type: str) -> str:
    suffix = Path(name).suffix
    if suffix:
        return suffix
    return mimetypes.guess_extension(content_type or "") or ".bin"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def audit_mailbox(download_attachments: bool = True) -> Path:
    token, scopes = outlook_tool.refresh_access_token()
    if outlook_tool.FORBIDDEN_TOKEN_SCOPE in scopes:
        raise AuditError("REFUSED: the Outlook token contains prohibited send permission.")

    me = graph_get(token, "/me?$select=displayName,mail,userPrincipalName")
    mailbox = outlook_tool.mailbox_address(me)
    expected = str(outlook_tool.load_config().get("mailbox") or "").casefold()
    if not mailbox or (expected and mailbox.casefold() != expected):
        raise AuditError("The connected Microsoft mailbox does not match the configured FRP Depot mailbox.")

    started = datetime.now(timezone.utc)
    run_dir = AUDIT_ROOT / started.strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    attachments_dir = run_dir / "attachments"
    folders, folder_names = collect_folders(token)
    write_json(run_dir / "folders.json", folders)

    message_select = (
        "id,parentFolderId,internetMessageId,conversationId,subject,from,sender,"
        "toRecipients,ccRecipients,replyTo,receivedDateTime,sentDateTime,createdDateTime,"
        "lastModifiedDateTime,isRead,isDraft,importance,hasAttachments,bodyPreview,body"
    )
    index_select = "id,from,sender,toRecipients,ccRecipients,replyTo"
    endpoint = f"/me/messages?$top=100&$select={index_select}"
    messages_path = run_dir / "messages.jsonl"
    attachment_index_path = run_dir / "attachments.jsonl"
    errors_path = run_dir / "errors.jsonl"

    message_count = 0
    mailbox_messages_seen = 0
    excluded_company_wall = 0
    attachment_count = 0
    attachment_bytes = 0
    skipped_attachments = 0
    errors: list[dict[str, Any]] = []
    folder_counts: dict[str, int] = {}

    with (
        messages_path.open("w", encoding="utf-8", newline="\n") as messages_file,
        attachment_index_path.open("w", encoding="utf-8", newline="\n") as attachments_file,
    ):
        for message_index in paged_values(token, endpoint):
            mailbox_messages_seen += 1
            if has_forbidden_participant(message_index):
                excluded_company_wall += 1
                continue
            message_id = str(message_index.get("id") or "")
            message = graph_get(
                token,
                f"/me/messages/{quote(message_id, safe='')}?$select={message_select}",
                prefer_text_body=True,
            )
            message_count += 1
            folder_id = str(message.get("parentFolderId") or "")
            folder_name = folder_names.get(folder_id, "Unknown")
            folder_counts[folder_name] = folder_counts.get(folder_name, 0) + 1
            message["_folderName"] = folder_name
            messages_file.write(json.dumps(message, ensure_ascii=False) + "\n")

            if message_count % 100 == 0:
                print(f"Messages read: {message_count}", flush=True)

            if not download_attachments or not message.get("hasAttachments"):
                continue

            message_key = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:16]
            try:
                attachment_endpoint = (
                    f"/me/messages/{quote(message_id, safe='')}/attachments?$top=100"
                    "&$select=id,name,contentType,size,isInline,lastModifiedDateTime"
                )
                for attachment in paged_values(token, attachment_endpoint):
                    entry = {
                        "message_id": message_id,
                        "message_subject": message.get("subject"),
                        "folder": folder_name,
                        "attachment_id": attachment.get("id"),
                        "name": attachment.get("name"),
                        "content_type": attachment.get("contentType"),
                        "size": attachment.get("size"),
                        "is_inline": attachment.get("isInline"),
                        "local_path": None,
                        "status": None,
                    }
                    size = int(attachment.get("size") or 0)
                    if attachment.get("isInline"):
                        entry["status"] = "skipped_inline"
                        skipped_attachments += 1
                    elif size > MAX_ATTACHMENT_BYTES:
                        entry["status"] = "skipped_over_25mb"
                        skipped_attachments += 1
                    else:
                        attachment_id = str(attachment.get("id") or "")
                        original_name = str(attachment.get("name") or "attachment")
                        fallback = "attachment" + extension_for(
                            original_name, str(attachment.get("contentType") or "")
                        )
                        filename = clean_filename(original_name, fallback)
                        destination_dir = attachments_dir / message_key
                        destination_dir.mkdir(parents=True, exist_ok=True)
                        destination = destination_dir / filename
                        if destination.exists():
                            stem, suffix = destination.stem, destination.suffix
                            destination = destination_dir / f"{stem}_{attachment_count + 1}{suffix}"
                        raw = graph_get(
                            token,
                            f"/me/messages/{quote(message_id, safe='')}/attachments/"
                            f"{quote(attachment_id, safe='')}/$value",
                            as_bytes=True,
                        )
                        destination.write_bytes(raw)
                        entry["local_path"] = str(destination)
                        entry["status"] = "downloaded"
                        attachment_count += 1
                        attachment_bytes += len(raw)
                    attachments_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except AuditError as exc:
                errors.append(
                    {
                        "message_id": message_id,
                        "subject": message.get("subject"),
                        "stage": "attachments",
                        "error": str(exc),
                    }
                )

    if errors:
        with errors_path.open("w", encoding="utf-8", newline="\n") as handle:
            for error in errors:
                handle.write(json.dumps(error, ensure_ascii=False) + "\n")

    completed = datetime.now(timezone.utc)
    summary = {
        "mailbox": mailbox,
        "started_utc": started.isoformat(),
        "completed_utc": completed.isoformat(),
        "messages_read": message_count,
        "mailbox_messages_seen": mailbox_messages_seen,
        "messages_excluded_company_wall": excluded_company_wall,
        "folders_found": len(folders),
        "messages_by_folder": dict(sorted(folder_counts.items())),
        "attachments_downloaded": attachment_count,
        "attachment_bytes_downloaded": attachment_bytes,
        "attachments_skipped": skipped_attachments,
        "attachment_errors": len(errors),
        "mailbox_modified": False,
        "send_permission_present": False,
        "messages_file": str(messages_path),
        "attachments_index": str(attachment_index_path),
    }
    write_json(run_dir / "summary.json", summary)
    (AUDIT_ROOT / "LATEST.txt").write_text(str(run_dir) + "\n", encoding="utf-8")
    outlook_tool.append_receipt("outlook_mailbox_audit_completed", str(run_dir / "summary.json"))
    print(json.dumps(summary, indent=2))
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only FRP Depot Outlook mailbox audit")
    parser.add_argument(
        "--skip-attachments",
        action="store_true",
        help="Read messages without downloading non-inline attachments",
    )
    args = parser.parse_args()
    try:
        audit_mailbox(download_attachments=not args.skip_attachments)
        return 0
    except (AuditError, outlook_tool.OutlookError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
