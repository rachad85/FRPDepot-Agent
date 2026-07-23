#!/usr/bin/env python
"""Pull complete live Outlook conversations for pricing candidates; read-only."""
from __future__ import annotations
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlencode, urlsplit

import outlook_tool

ROOT = Path(r"C:\FRPDepot")
OUT_ROOT = ROOT / "Dado" / "20_Working" / "pricing_requests"
INTERNAL_DOMAIN = "frpdepots.com"
FORBIDDEN_DOMAINS = {"troydualam.com"}


def address(field):
    return str(((field or {}).get("emailAddress") or {}).get("address") or "").casefold()


def addresses(message):
    values = [message.get("from"), message.get("sender")]
    values.extend(message.get("toRecipients") or [])
    values.extend(message.get("ccRecipients") or [])
    return sorted({address(value) for value in values if address(value)})


def clean_body(message):
    content = str((message.get("body") or {}).get("content") or message.get("bodyPreview") or "")
    if str((message.get("body") or {}).get("contentType") or "").casefold() == "html":
        content = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", content)
        content = re.sub(r"(?i)<br\s*/?>", "\n", content)
        content = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", content)
        content = re.sub(r"(?s)<[^>]+>", " ", content)
        content = html.unescape(content)
    content = content.replace("\r", "\n")
    content = re.sub(r"[ \t]+", " ", content)
    content = re.sub(r"\n\s*\n\s*\n+", "\n\n", content)
    return content.strip()


def next_path(url):
    parsed = urlsplit(url)
    marker = "/v1.0"
    pos = parsed.path.find(marker)
    path = parsed.path[pos + len(marker):] if pos >= 0 else parsed.path
    return path + ("?" + parsed.query if parsed.query else "")


def fetch_conversation(token, conversation_id):
    params = {
        "$filter": f"conversationId eq '{conversation_id}'",
        "$select": "id,conversationId,subject,from,sender,toRecipients,ccRecipients,receivedDateTime,sentDateTime,createdDateTime,body,bodyPreview,hasAttachments,isDraft,parentFolderId",
        "$top": 100,
    }
    path = "/me/messages?" + urlencode(params)
    values = []
    while path:
        result = outlook_tool.graph_request(token, "GET", path)
        values.extend(result.get("value") or [])
        link = str(result.get("@odata.nextLink") or "")
        path = next_path(link) if link else ""
    rows = []
    for message in values:
        participants = addresses(message)
        if any(any(value.endswith("@" + domain) for domain in FORBIDDEN_DOMAINS) for value in participants):
            continue
        sender = address(message.get("from")) or address(message.get("sender"))
        website_submission = (
            sender == "sales@frpdepots.com"
            and str(message.get("subject") or "").strip().casefold() == "new submission from contact"
        )
        rows.append({
            "id": message.get("id"),
            "subject": message.get("subject"),
            "from_name": str(((message.get("from") or {}).get("emailAddress") or {}).get("name") or ""),
            "from_address": sender,
            "to": [address(value) for value in message.get("toRecipients") or []],
            "cc": [address(value) for value in message.get("ccRecipients") or []],
            "datetime": message.get("receivedDateTime") or message.get("sentDateTime") or message.get("createdDateTime"),
            "direction": "inbound_web_form" if website_submission else ("outbound" if sender.endswith("@" + INTERNAL_DOMAIN) else "inbound"),
            "is_draft": bool(message.get("isDraft")),
            "has_attachments": bool(message.get("hasAttachments")),
            "body": clean_body(message),
        })
    rows.sort(key=lambda row: str(row.get("datetime") or ""))
    return rows


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: pull_pricing_threads.py candidate.json conversation_id [conversation_id ...]")
    candidate_path = Path(sys.argv[1])
    candidates = json.loads(candidate_path.read_text(encoding="utf-8"))["candidates"]
    by_conversation = {row["conversation_id"]: row for row in candidates}
    wanted = list(dict.fromkeys(sys.argv[2:]))
    token, _ = outlook_tool.refresh_access_token()
    threads = []
    for conversation_id in wanted:
        messages = fetch_conversation(token, conversation_id)
        seed = by_conversation.get(conversation_id) or {}
        latest_non_draft = next((row for row in reversed(messages) if not row["is_draft"]), None)
        threads.append({
            "conversation_id": conversation_id,
            "seed_subject": seed.get("subject"),
            "seed_from": seed.get("from_address"),
            "messages": messages,
            "message_count": len(messages),
            "latest_non_draft_direction": (latest_non_draft or {}).get("direction"),
            "latest_non_draft_datetime": (latest_non_draft or {}).get("datetime"),
            "open_no_later_outbound": bool(latest_non_draft and latest_non_draft["direction"] in {"inbound", "inbound_web_form"}),
        })
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = OUT_ROOT / f"{stamp}_live_pricing_threads.json"
    output.write_text(json.dumps({"generated_utc": datetime.now(timezone.utc).isoformat(), "mailbox_modified": False, "threads": threads}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    outlook_tool.append_receipt("outlook_pricing_threads_pulled", str(output))
    print(json.dumps({"output": str(output), "threads": [{key: thread[key] for key in ("conversation_id", "seed_subject", "seed_from", "message_count", "latest_non_draft_direction", "latest_non_draft_datetime", "open_no_later_outbound")} for thread in threads]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
