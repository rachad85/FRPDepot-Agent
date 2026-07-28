from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import quote

ROOT = Path(r"C:\FRPDepot")
LOG_PATH = ROOT / "Dado" / "30_Memory" / "custom_quotes_log.csv"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
OUTLOOK_DIR = ROOT / "Dado" / "Tools" / "outlook"

FIELDS = [
    "internal_id",
    "quote_date",
    "sent_at_utc",
    "customer_company",
    "contact_name",
    "contact_email",
    "customer_reference",
    "subject",
    "currency",
    "total",
    "attachment_name",
    "attachment_sha256",
    "outlook_conversation_id",
    "outlook_sent_message_id",
    "delivery_channel",
    "outside_zoho",
    "zoho_estimate_id",
    "status",
    "source_pricing",
    "notes",
    "recorded_at_utc",
]

REQUIRED_INPUT = {
    "quote_date",
    "customer_company",
    "contact_name",
    "contact_email",
    "customer_reference",
    "subject",
    "currency",
    "total",
    "attachment_name",
    "sent_message_id",
    "source_pricing",
}


class QuoteLogError(RuntimeError):
    pass


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "evidence": evidence,
    }
    with RECEIPTS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise QuoteLogError("quote_date must use YYYY-MM-DD.") from exc


def normalize_currency(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", text):
        raise QuoteLogError("currency must be a three-letter code such as USD or CAD.")
    return text


def normalize_total(value: Any) -> str:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise QuoteLogError("total must be a valid number.") from exc
    if amount < 0:
        raise QuoteLogError("total cannot be negative.")
    return format(amount, "f")


def safe_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def read_rows() -> list[dict[str, str]]:
    if not LOG_PATH.exists():
        return []
    with LOG_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def initialize_log() -> bool:
    if LOG_PATH.exists():
        return False
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
    append_receipt("custom_quote_log_initialized", str(LOG_PATH))
    return True


def next_internal_id(rows: list[dict[str, str]], quote_date: str) -> str:
    year = quote_date[:4]
    prefix = f"CQ-{year}-"
    numbers = []
    for row in rows:
        value = str(row.get("internal_id") or "")
        if value.startswith(prefix):
            try:
                numbers.append(int(value[len(prefix):]))
            except ValueError:
                continue
    return f"{prefix}{(max(numbers, default=0) + 1):04d}"


def load_outlook_tool():
    sys.path.insert(0, str(OUTLOOK_DIR))
    import outlook_tool  # type: ignore
    return outlook_tool


def verify_sent_quote(data: dict[str, Any]) -> dict[str, Any]:
    ot = load_outlook_tool()
    token, scopes = ot.refresh_access_token()
    if "Mail.Send" in scopes:
        raise QuoteLogError("Outlook token unexpectedly includes Mail.Send; refusing to continue.")

    sent_message_id = str(data["sent_message_id"]).strip()
    encoded_message_id = quote(sent_message_id, safe="")
    message = ot.graph_request(
        token,
        "GET",
        (
            f"/me/messages/{encoded_message_id}?$select=id,isDraft,conversationId,subject,"
            "sentDateTime,from,sender,toRecipients,ccRecipients,bccRecipients"
        ),
    )
    sender = ot.message_address(message.get("from")) or ot.message_address(message.get("sender"))
    if message.get("isDraft") is not False:
        raise QuoteLogError("The Outlook message is not confirmed as sent.")
    if not sender or not ot.is_internal_address(sender):
        raise QuoteLogError("The Outlook message was not sent from the FRP Depot mailbox.")
    if str(message.get("subject") or "") != str(data["subject"]).strip():
        raise QuoteLogError("The live Outlook subject does not match the quote-log input.")

    contact_email = str(data["contact_email"]).strip().casefold()
    recipients = {
        address.casefold()
        for address in (
            ot.recipient_addresses(message.get("toRecipients"))
            + ot.recipient_addresses(message.get("ccRecipients"))
        )
    }
    if contact_email not in recipients:
        raise QuoteLogError("The stated customer contact is not a live To/Cc recipient.")
    if ot.recipient_addresses(message.get("bccRecipients")):
        raise QuoteLogError("The sent quote unexpectedly has Bcc recipients.")

    attachments = ot.graph_request(
        token,
        "GET",
        f"/me/messages/{encoded_message_id}/attachments",
    ).get("value") or []
    attachment_name = str(data["attachment_name"]).strip()
    matching = [
        row for row in attachments
        if row.get("isInline") is not True and row.get("name") == attachment_name
    ]
    if len(matching) != 1:
        raise QuoteLogError("Expected exactly one matching non-inline quote attachment in Sent Items.")
    if str(matching[0].get("contentType") or "").casefold() != "application/pdf":
        raise QuoteLogError("The sent quote attachment is not confirmed as a PDF.")

    attachment_id = quote(str(matching[0]["id"]), safe="")
    full_attachment = ot.graph_request(
        token,
        "GET",
        f"/me/messages/{encoded_message_id}/attachments/{attachment_id}",
    )
    encoded_content = str(full_attachment.get("contentBytes") or "")
    if not encoded_content:
        raise QuoteLogError("Outlook did not return the sent PDF contents for verification.")
    sent_pdf = base64.b64decode(encoded_content)
    attachment_sha256 = hashlib.sha256(sent_pdf).hexdigest()

    local_path_value = str(data.get("local_attachment_path") or "").strip()
    if local_path_value:
        local_path = Path(local_path_value).expanduser().resolve()
        if not local_path.is_file():
            raise QuoteLogError(f"Local quote PDF does not exist: {local_path}")
        if hashlib.sha256(local_path.read_bytes()).hexdigest() != attachment_sha256:
            raise QuoteLogError("The live Sent Items PDF does not match the local quote PDF.")

    return {
        "sent_at_utc": str(message.get("sentDateTime") or ""),
        "conversation_id": str(message.get("conversationId") or ""),
        "attachment_sha256": attachment_sha256,
        "to": ot.recipient_addresses(message.get("toRecipients")),
        "cc": ot.recipient_addresses(message.get("ccRecipients")),
        "mail_send_scope_present": False,
    }


def validate_input(data: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(key for key in REQUIRED_INPUT if not str(data.get(key) or "").strip())
    if missing:
        raise QuoteLogError("Missing required quote-log fields: " + ", ".join(missing))
    email = str(data["contact_email"]).strip().casefold()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise QuoteLogError("contact_email is invalid.")
    return {
        **data,
        "quote_date": normalize_date(data["quote_date"]),
        "currency": normalize_currency(data["currency"]),
        "total": normalize_total(data["total"]),
        "contact_email": email,
    }


def record_quote(data: dict[str, Any]) -> dict[str, Any]:
    cleaned = validate_input(data)
    initialize_log()
    rows = read_rows()
    sent_message_id = str(cleaned["sent_message_id"]).strip()
    if any(str(row.get("outlook_sent_message_id") or "") == sent_message_id for row in rows):
        raise QuoteLogError("This Outlook sent message is already logged.")

    verified = verify_sent_quote(cleaned)
    internal_id = next_internal_id(rows, cleaned["quote_date"])
    row = {
        "internal_id": internal_id,
        "quote_date": cleaned["quote_date"],
        "sent_at_utc": verified["sent_at_utc"],
        "customer_company": safe_text(cleaned["customer_company"]),
        "contact_name": safe_text(cleaned["contact_name"]),
        "contact_email": cleaned["contact_email"],
        "customer_reference": safe_text(cleaned["customer_reference"]),
        "subject": safe_text(cleaned["subject"]),
        "currency": cleaned["currency"],
        "total": cleaned["total"],
        "attachment_name": safe_text(cleaned["attachment_name"]),
        "attachment_sha256": verified["attachment_sha256"],
        "outlook_conversation_id": verified["conversation_id"],
        "outlook_sent_message_id": sent_message_id,
        "delivery_channel": "Outlook email",
        "outside_zoho": "yes",
        "zoho_estimate_id": "",
        "status": "sent",
        "source_pricing": safe_text(cleaned["source_pricing"]),
        "notes": safe_text(cleaned.get("notes")),
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    with LOG_PATH.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writerow(row)
    append_receipt("custom_quote_logged", f"{internal_id}:{sent_message_id}")
    return {
        "status": "CUSTOM_QUOTE_LOGGED_FROM_SENT_ITEMS",
        "internal_id": internal_id,
        "log_path": str(LOG_PATH),
        "customer_company": row["customer_company"],
        "customer_reference": row["customer_reference"],
        "currency": row["currency"],
        "total": row["total"],
        "sent_at_utc": row["sent_at_utc"],
        "attachment_name": row["attachment_name"],
        "attachment_sha256": row["attachment_sha256"],
        "to": verified["to"],
        "cc": verified["cc"],
        "outside_zoho": True,
        "sent_verified": True,
        "mail_send_scope_present": verified["mail_send_scope_present"],
    }


def command_init(_: argparse.Namespace) -> None:
    created = initialize_log()
    print(json.dumps({"status": "CREATED" if created else "ALREADY_EXISTS", "path": str(LOG_PATH)}, indent=2))


def command_record(args: argparse.Namespace) -> None:
    path = Path(args.input).expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(record_quote(data), indent=2, ensure_ascii=False))


def command_list(_: argparse.Namespace) -> None:
    print(json.dumps({"path": str(LOG_PATH), "count": len(read_rows()), "quotes": read_rows()}, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Log custom quotes sent by FRP Depot outside Zoho.")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create the CSV log header if needed")
    init.set_defaults(func=command_init)
    record = sub.add_parser("record", help="Verify a Sent Items quote and append it once")
    record.add_argument("--input", required=True, help="Path to the quote metadata JSON file")
    record.set_defaults(func=command_record)
    listing = sub.add_parser("list", help="Read the current quote log")
    listing.set_defaults(func=command_list)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (QuoteLogError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
