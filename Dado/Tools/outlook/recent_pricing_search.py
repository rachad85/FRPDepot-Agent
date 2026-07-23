#!/usr/bin/env python
"""Find recent live FRP Depot pricing requests without modifying Outlook."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import outlook_tool

ROOT = Path(r"C:\FRPDepot")
OUT_ROOT = ROOT / "Dado" / "20_Working" / "pricing_requests"
FORBIDDEN_DOMAINS = {"troydualam.com"}
INTERNAL_DOMAIN = "frpdepots.com"
PRICING = re.compile(r"\b(quote|quotation|pricing|price|estimate|rfq|budgetary|offer)\b", re.I)
REQUEST = re.compile(r"\b(please|request|could you|can you|need|looking for|interested|inquiry|enquiry|provide|send|require)\b", re.I)
AUTOMATED = re.compile(r"(no-?reply|notification|unsubscribe|security alert|verification code)", re.I)


def address(field):
    return str(((field or {}).get("emailAddress") or {}).get("address") or "").casefold()


def all_addresses(message):
    fields = [message.get("from")] + list(message.get("toRecipients") or []) + list(message.get("ccRecipients") or [])
    return [address(value) for value in fields if address(value)]


def is_website_submission(message):
    return (
        address(message.get("from")) == "sales@frpdepots.com"
        and str(message.get("subject") or "").strip().casefold() == "new submission from contact"
    )


def page_path(next_link):
    parsed = urlsplit(next_link)
    marker = "/v1.0"
    pos = parsed.path.find(marker)
    path = parsed.path[pos + len(marker):] if pos >= 0 else parsed.path
    return path + ("?" + parsed.query if parsed.query else "")


def fetch_recent(access_token, limit=200):
    select = "id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview,hasAttachments,isRead,importance"
    path = f"/me/mailFolders/inbox/messages?$top=100&$orderby=receivedDateTime%20desc&$select={select}"
    messages = []
    while path and len(messages) < limit:
        result = outlook_tool.graph_request(access_token, "GET", path)
        messages.extend(result.get("value") or [])
        link = str(result.get("@odata.nextLink") or "")
        path = page_path(link) if link else ""
    return messages[:limit]


def score(message):
    subject = str(message.get("subject") or "")
    preview = str(message.get("bodyPreview") or "")
    sender = address(message.get("from"))
    value = 0
    value += 6 * len(PRICING.findall(subject))
    value += 2 * len(PRICING.findall(preview))
    value += 2 if REQUEST.search(subject + " " + preview) else 0
    value += 1 if message.get("hasAttachments") else 0
    value += 1 if not message.get("isRead") else 0
    if is_website_submission(message):
        value += 8
    elif sender.endswith("@" + INTERNAL_DOMAIN):
        value -= 10
    if AUTOMATED.search(sender + " " + subject + " " + preview):
        value -= 8
    return value


def main():
    token, _ = outlook_tool.refresh_access_token()
    recent = fetch_recent(token)
    candidates = []
    forbidden_skipped = 0
    for message in recent:
        participants = all_addresses(message)
        if any(any(addr.endswith("@" + domain) for domain in FORBIDDEN_DOMAINS) for addr in participants):
            forbidden_skipped += 1
            continue
        value = score(message)
        if value <= 0 or not PRICING.search(str(message.get("subject") or "") + " " + str(message.get("bodyPreview") or "")):
            continue
        sender = (message.get("from") or {}).get("emailAddress") or {}
        candidates.append({
            "id": message.get("id"),
            "conversation_id": message.get("conversationId"),
            "received": message.get("receivedDateTime"),
            "from_name": sender.get("name"),
            "from_address": sender.get("address"),
            "subject": message.get("subject"),
            "preview": message.get("bodyPreview"),
            "has_attachments": bool(message.get("hasAttachments")),
            "is_read": bool(message.get("isRead")),
            "importance": message.get("importance"),
            "score": value,
        })
    candidates.sort(key=lambda row: (row["received"] or "", row["score"]), reverse=True)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT_ROOT / f"{stamp}_recent_pricing_candidates.json"
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "messages_scanned": len(recent),
        "mailbox_modified": False,
        "forbidden_company_messages_skipped": forbidden_skipped,
        "candidates": candidates,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    outlook_tool.append_receipt("outlook_recent_pricing_search", str(path))
    print(json.dumps({"output": str(path), "messages_scanned": len(recent), "candidate_count": len(candidates), "top_candidates": candidates[:12]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
