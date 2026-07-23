#!/usr/bin/env python
"""Classify every sanitized FRP Depot mailbox message for operational triage."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

import outlook_tool

ROOT = Path(r"C:\FRPDepot")
AUDIT_ROOT = ROOT / "Dado" / "20_Working" / "outlook_mailbox_audit"
INTERNAL_DOMAIN = "frpdepots.com"
FORBIDDEN_NEEDLES = ("troydualam", "troy dualam")
CATEGORY_TERMS = {
    "payment_finance": (
        "invoice", "payment", "remittance", "statement", "credit memo", "outstanding",
        "deposit", "wire", "bank", "transfer", "airwallex", "balance due", "refund",
    ),
    "quote_sales": (
        "quote", "quotation", "estimate", "pricing", "price", "inquiry", "enquiry",
        "rfq", "request for quote", "lead",
    ),
    "order_procurement": (
        "purchase order", "sales order", "order confirmation", "po-", "production",
        "manufactur", "procurement",
    ),
    "logistics_shipping": (
        "shipment", "shipping", "freight", "delivery", "pickup", "pick up", "container",
        "tracking", "customs", "bill of lading", "packing list", "carrier", "warehouse",
    ),
    "product_quality": (
        "frp", "grating", "rebar", "profile", "fiberglass", "resin", "panel", "sample",
        "specification", "drawing", "cad", "damage", "repair", "quality", "pultrud",
    ),
    "systems_accounts": (
        "zoho", "microsoft", "shiphero", "shopify", "account verification", "subscription",
        "login", "domain", "website",
    ),
}
AUTOMATED_MARKERS = (
    "do not reply", "automated message", "unsubscribe", "manage your preferences",
    "notification", "password reset", "verification code", "security alert",
)
AUTOMATED_LOCAL_PARTS = ("no-reply", "noreply", "donotreply", "notifications", "message-service")


def email_address(field: Any) -> str:
    return str(((field or {}).get("emailAddress") or {}).get("address") or "").casefold()


def message_datetime(message: dict[str, Any]) -> str:
    return str(
        message.get("receivedDateTime")
        or message.get("sentDateTime")
        or message.get("createdDateTime")
        or ""
    )


def clean_snippet(message: dict[str, Any]) -> str:
    body = str((message.get("body") or {}).get("content") or message.get("bodyPreview") or "")
    body = body.replace("\r", "\n")
    for separator in ("-----Original Message-----", "________________________________", "\nFrom:"):
        if separator in body:
            body = body.split(separator, 1)[0]
    body = re.sub(r"\s+", " ", body).strip()
    return body[:400]


def classify(message: dict[str, Any]) -> dict[str, Any]:
    sender = email_address(message.get("from"))
    folder = str(message.get("_folderName") or "")
    direction = "outbound" if folder == "Sent Items" or sender.endswith("@" + INTERNAL_DOMAIN) else "inbound"
    subject = str(message.get("subject") or "")
    snippet = clean_snippet(message)
    haystack = (subject + "\n" + snippet).casefold()
    categories = [
        category for category, terms in CATEGORY_TERMS.items() if any(term in haystack for term in terms)
    ]
    if not categories:
        categories = ["general"]
    local_part = sender.split("@", 1)[0]
    automated = any(part in local_part for part in AUTOMATED_LOCAL_PARTS) or any(
        marker in haystack for marker in AUTOMATED_MARKERS
    )
    return {
        "id": message.get("id"),
        "conversation_id": message.get("conversationId"),
        "folder": folder,
        "direction": direction,
        "datetime": message_datetime(message),
        "from_name": str(((message.get("from") or {}).get("emailAddress") or {}).get("name") or ""),
        "from_address": sender,
        "subject": subject,
        "categories": categories,
        "automated": automated,
        "has_attachments": bool(message.get("hasAttachments")),
        "importance": message.get("importance"),
        "snippet": snippet,
    }


def process(run_dir: Path) -> tuple[Path, Path]:
    messages_path = run_dir / "messages.jsonl"
    raw_text = messages_path.read_text(encoding="utf-8")
    lowered = raw_text.casefold()
    if any(needle in lowered for needle in FORBIDDEN_NEEDLES):
        raise RuntimeError("Company-wall material remains in the sanitized mailbox export.")
    messages = [json.loads(line) for line in raw_text.splitlines() if line.strip()]
    records = [classify(message) for message in messages]
    output_path = run_dir / "message_triage.jsonl"
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    by_conversation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = str(record.get("conversation_id") or record.get("id") or "")
        by_conversation[key].append(record)
    open_candidates: list[dict[str, Any]] = []
    for items in by_conversation.values():
        items.sort(key=lambda item: str(item.get("datetime") or ""))
        latest = items[-1]
        if latest["direction"] == "inbound" and not latest["automated"]:
            open_candidates.append(
                {
                    **latest,
                    "conversation_message_count": len(items),
                    "reason": "Latest message in the conversation is inbound with no later outbound reply.",
                }
            )
    open_candidates.sort(key=lambda item: str(item.get("datetime") or ""), reverse=True)
    category_counts = Counter(category for record in records for category in record["categories"])
    metrics = {
        "processed_utc": datetime.now(timezone.utc).isoformat(),
        "messages_processed": len(records),
        "inbound": sum(record["direction"] == "inbound" for record in records),
        "outbound": sum(record["direction"] == "outbound" for record in records),
        "automated": sum(record["automated"] for record in records),
        "category_counts": dict(sorted(category_counts.items())),
        "conversations": len(by_conversation),
        "open_conversation_candidates": len(open_candidates),
        "open_candidates": open_candidates,
        "mailbox_modified": False,
        "output": str(output_path),
    }
    metrics_path = run_dir / "triage_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    outlook_tool.append_receipt("outlook_messages_classified", str(metrics_path))
    print(
        json.dumps(
            {
                "messages_processed": len(records),
                "conversations": len(by_conversation),
                "open_conversation_candidates": len(open_candidates),
                "mailbox_modified": False,
                "metrics": str(metrics_path),
            },
            indent=2,
        )
    )
    return output_path, metrics_path


def main() -> int:
    latest = (AUDIT_ROOT / "LATEST.txt").read_text(encoding="utf-8").strip()
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(latest)
    try:
        process(run_dir)
        return 0
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
