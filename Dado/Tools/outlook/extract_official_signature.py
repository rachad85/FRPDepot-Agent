#!/usr/bin/env python
"""Extract Rachad's official HTML signature from a known safe FRP Depot sent message."""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import quote

import outlook_tool

ROOT = Path(r"C:\FRPDepot")
THREAD_FILE = ROOT / "Dado" / "20_Working" / "pricing_requests" / "20260723T175404Z_live_pricing_threads.json"
OUT = ROOT / "Dado" / "20_Working" / "outlook_signature"
SAFE_RECIPIENT = "brianb@nashtecllc.com"
FORBIDDEN_DOMAINS = {"troydualam.com"}


def addresses(message):
    values = []
    for key in ("from", "sender"):
        address = str((((message.get(key) or {}).get("emailAddress") or {}).get("address") or "")).casefold()
        if address:
            values.append(address)
    for key in ("toRecipients", "ccRecipients", "bccRecipients"):
        for recipient in message.get(key) or []:
            address = str(((recipient.get("emailAddress") or {}).get("address") or "")).casefold()
            if address:
                values.append(address)
    return values


def extract_signature_html(content):
    top = content
    separators = [
        r'<div[^>]+id=["\']divRplyFwdMsg["\'][^>]*>',
        r'<hr[^>]+tabindex=["\']-1["\'][^>]*>',
        r'<div[^>]+border-top:\s*solid\s+#B5C4DF',
    ]
    positions = []
    for pattern in separators:
        match = re.search(pattern, top, flags=re.I)
        if match:
            positions.append(match.start())
    if positions:
        top = top[: min(positions)]
    matches = list(re.finditer(r"Rachad\s+Homsi", top, flags=re.I))
    if not matches:
        raise RuntimeError("The selected sent message does not contain Rachad Homsi's signature.")
    name_match = matches[-1]
    paragraph_start = top.rfind("<p", 0, name_match.start())
    start = paragraph_start if paragraph_start >= 0 else name_match.start()
    signature = top[start:].strip()
    postal = re.search(r"K6T(?:&nbsp;|\s)*1A9", signature, flags=re.I)
    if postal:
        paragraph_end = signature.find("</p>", postal.end())
        if paragraph_end >= 0:
            signature = signature[: paragraph_end + len("</p>")]
    if "frpdepots.com" not in signature.casefold() or "ceo" not in signature.casefold():
        raise RuntimeError("The extracted block does not contain the expected FRP Depot signature details.")
    return signature


def main():
    thread_data = json.loads(THREAD_FILE.read_text(encoding="utf-8"))
    brian_thread = next(t for t in thread_data["threads"] if any(str(m.get("from_address") or "").casefold() == SAFE_RECIPIENT for m in t["messages"]))
    sent = [m for m in brian_thread["messages"] if m.get("direction") == "outbound" and not m.get("is_draft") and SAFE_RECIPIENT in [str(x).casefold() for x in m.get("to") or []]]
    if not sent:
        raise RuntimeError("No sent FRP Depot message to Brian was found in the verified thread.")
    seed = sent[-1]
    token, _ = outlook_tool.refresh_access_token()
    message_id = str(seed["id"])
    encoded = quote(message_id, safe="")
    message = outlook_tool.graph_request(token, "GET", f"/me/messages/{encoded}?$select=id,subject,from,sender,toRecipients,ccRecipients,bccRecipients,sentDateTime,body,hasAttachments,isDraft")
    participants = addresses(message)
    if SAFE_RECIPIENT not in participants:
        raise RuntimeError("Selected Outlook message recipient did not match Brian's verified address.")
    if any(any(addr.endswith("@" + domain) for domain in FORBIDDEN_DOMAINS) for addr in participants):
        raise RuntimeError("REFUSED: selected message crosses the FRP Depot company wall.")
    if message.get("isDraft"):
        raise RuntimeError("Selected message is a draft, not a previously sent signature source.")
    body = message.get("body") or {}
    if str(body.get("contentType") or "").casefold() != "html":
        raise RuntimeError("Selected sent message is not HTML.")
    signature = extract_signature_html(str(body.get("content") or ""))
    OUT.mkdir(parents=True, exist_ok=True)
    attachments_saved = []
    cids = set(re.findall(r"cid:([^\"'> ]+)", signature, flags=re.I))
    if cids or message.get("hasAttachments"):
        result = outlook_tool.graph_request(token, "GET", f"/me/messages/{encoded}/attachments")
        for attachment in result.get("value") or []:
            if not attachment.get("isInline"):
                continue
            content_id = str(attachment.get("contentId") or "")
            if cids and content_id not in cids:
                continue
            raw = attachment.get("contentBytes")
            if not raw:
                continue
            name = Path(str(attachment.get("name") or "signature-image.bin")).name
            image_path = OUT / name
            image_path.write_bytes(base64.b64decode(raw))
            attachments_saved.append({
                "path": str(image_path),
                "name": name,
                "content_type": attachment.get("contentType"),
                "content_id": content_id,
                "is_inline": True,
            })
    html_path = OUT / "official_signature.html"
    html_path.write_text(signature, encoding="utf-8")
    bundle = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source_message_id": message_id,
        "source_subject": message.get("subject"),
        "source_sent_datetime": message.get("sentDateTime"),
        "source_recipient": SAFE_RECIPIENT,
        "signature_html_path": str(html_path),
        "inline_attachments": attachments_saved,
    }
    bundle_path = OUT / "official_signature_bundle.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    outlook_tool.append_receipt("official_outlook_signature_extracted", str(bundle_path))
    print(json.dumps({"bundle": str(bundle_path), "html": str(html_path), "inline_attachment_count": len(attachments_saved), "source_sent_datetime": message.get("sentDateTime"), "contains_logo_tag": bool(re.search(r"<img\b", signature, flags=re.I))}, indent=2))


if __name__ == "__main__":
    main()
