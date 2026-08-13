#!/usr/bin/env python
"""FRP Depot historical client PO evidence recovery. READ-ONLY.

For every affected historical Zoho Books Sales Order named by
``zoho_order_reference_audit`` -- that is, every order whose Client PO reference
is actually an FRP Depot Quote number (``QT-...``) and every order whose Client
PO reference is blank -- this recovers the customer's OWN purchase order number
from direct evidence, or says plainly that no defensible evidence exists.

IT CHANGES NOTHING. There is no Zoho write verb, no Outlook draft route, no mail
transport, no Drive write and no browser session anywhere in this module. Zoho is
read through ``zoho_tool.api_get`` (GET-only); Outlook is read through
``outlook_tool.graph_request`` with the method pinned to GET; the Google Drive
reference cache is opened ``mode=ro``.

EVIDENCE RULES, and they are the whole point of the tool:

* A Client PO candidate is defensible only when a CUSTOMER-provided context
  explicitly labels it a PO / Purchase Order, or an original customer PO
  attachment visibly identifies it. A number that appears only in an FRP
  Depot-generated document is never a candidate.
* FRP Depot's own document numbers (``QT-``/``SO-``/``INV-``), dates, quantities,
  prices, totals and unlabelled job numbers are refused by name.
* Two tiers of evidence, because the customers here place many POs:
  TIER 1 is a message the order-specific query found (the linked Quote number or
  the Sales Order number appears in it), so it is tied to THIS order.
  TIER 2 is a customer message merely inside a tight window around the order
  date. Tier 2 is used only when Tier 1 is silent, and a busy customer therefore
  produces several PO values and lands at ``ambiguous`` on its own -- that
  self-protection is deliberate, not a limitation to engineer away.
* Never choose among conflicting candidates automatically.

Deliberate omission: no price, total, balance, tax, discount, payment amount,
bank detail or credential is projected, saved or printed. ``redact_amounts``
strips figures from every excerpt before it is stored and ``scan_for_leaks``
re-reads the finished reports and fails closed if one survived.

No company filtering is applied to FRP Depot's own mailbox. Troy Dualam is an
ordinary FRP Depot customer. TDI Gmail and C:\\AgentTeam are never read: this
module touches the Drive tables of the reference cache only, never gmail_*.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import csv
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote as url_quote

ROOT = Path(r"C:\FRPDepot")
ZOHO_TOOLS = ROOT / "Dado" / "Tools" / "zoho"
OUTLOOK_TOOLS = ROOT / "Dado" / "Tools" / "outlook"
for _tools in (ZOHO_TOOLS, OUTLOOK_TOOLS):
    if str(_tools) not in sys.path:
        sys.path.insert(0, str(_tools))
import outlook_tool  # noqa: E402
import zoho_tool  # noqa: E402

TOOL_NAME = "zoho_client_po_recovery"
TOOL_VERSION = "1.0.0"

OUTPUT_ROOT = ROOT / "Dado" / "20_Working" / "zoho_client_po_recovery"
AUDIT_ROOT = ROOT / "Dado" / "20_Working" / "zoho_order_reference_audit"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
# Transient attachment bytes are written OUTSIDE the repository, always, and are
# deleted as soon as the text has been extracted from them.
TRANSIENT_ROOT = LOCALAPPDATA / "FRPDepot-Outlook" / "po_recovery_transient"
DRIVE_REFERENCE_DB = LOCALAPPDATA / "FRPDepot-Google" / "reference" / "google_reference.sqlite"

JSON_REPORT_NAME = "client_po_recovery.json"
CSV_REPORT_NAME = "client_po_recovery.csv"
MARKDOWN_REPORT_NAME = "client_po_recovery.md"
BATCH_PREFIX = "source_queries"

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
INTERNAL_DOMAIN = "frpdepots.com"

# Ceilings. Every one of them FAILS CLOSED: the run aborts rather than reporting
# a partial sweep as though it were complete.
PAGE_SIZE = 100
MAX_PAGES_PER_QUERY = 25
MAX_MESSAGES_PER_QUERY = PAGE_SIZE * MAX_PAGES_PER_QUERY
MAX_BODY_FETCHES = 900
MAX_ATTACHMENT_DOWNLOADS = 400
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_DRIVE_ROWS_PER_QUERY = 50
BATCH_SIZE = 10

# Tier 1: the message already names this order's quote or sales order, so the
# window is only a sanity bound. Tier 2: proximity is the ONLY tie to the order,
# so it is deliberately tight.
LINKED_WINDOW_BEFORE_DAYS = 365
LINKED_WINDOW_AFTER_DAYS = 120
CUSTOMER_WINDOW_BEFORE_DAYS = 60
CUSTOMER_WINDOW_AFTER_DAYS = 14

REPORT_COLUMNS = (
    "salesorder_id",
    "salesorder_number",
    "date",
    "customer_id",
    "customer_name",
    "current_reference",
    "linked_quote_id",
    "linked_quote_number",
    "recovered_client_po",
    "confidence",
    "evidence_source_type",
    "evidence_message_id",
    "evidence_date",
    "evidence_subject",
    "evidence_sender",
    "evidence_attachment_name",
    "evidence_excerpt",
    "conflict_candidates",
    "recommended_action",
)

CONFIDENCE_CERTAIN = "certain"
CONFIDENCE_AMBIGUOUS = "ambiguous"
CONFIDENCE_NONE = "none"

ACTION_REPLACE_QUOTE = "replace_quote_reference_with_confirmed_client_po"
ACTION_FILL_BLANK = "fill_blank_reference_with_confirmed_client_po"
ACTION_MANUAL_REVIEW = "manual_review_conflicting_po_evidence"
ACTION_LEAVE_UNCHANGED = "leave_unchanged_no_defensible_po"
RECOMMENDED_ACTIONS = (
    ACTION_REPLACE_QUOTE,
    ACTION_FILL_BLANK,
    ACTION_MANUAL_REVIEW,
    ACTION_LEAVE_UNCHANGED,
)

SOURCE_NONE = "none"
SOURCE_MESSAGE_LINKED = "outlook_customer_message_linked_to_quote_or_order"
SOURCE_ATTACHMENT_LINKED = "outlook_customer_attachment_linked_to_quote_or_order"
SOURCE_MESSAGE_WINDOW = "outlook_customer_message_in_order_date_window"
SOURCE_ATTACHMENT_WINDOW = "outlook_customer_attachment_in_order_date_window"
SOURCE_DRIVE_CACHE = "drive_reference_cache_only_not_verified_against_original"
EVIDENCE_SOURCE_TYPES = (
    SOURCE_NONE,
    SOURCE_MESSAGE_LINKED,
    SOURCE_ATTACHMENT_LINKED,
    SOURCE_MESSAGE_WINDOW,
    SOURCE_ATTACHMENT_WINDOW,
    SOURCE_DRIVE_CACHE,
)

TIER_LINKED = "linked"
TIER_WINDOW = "window"
TIER_CACHE = "cache"

QUERY_KIND_QUOTE = "linked_quote_number"
QUERY_KIND_SALESORDER = "salesorder_number"
QUERY_KIND_CUSTOMER_NAME = "customer_name_phrase"
QUERY_KIND_CUSTOMER_DOMAIN = "customer_email_domain"
LINKED_QUERY_KINDS = (QUERY_KIND_QUOTE, QUERY_KIND_SALESORDER)

MAX_EXCERPT_CHARS = 240

# Attachment types this tool will download. Everything else stays on the server.
SUPPORTED_ATTACHMENT_SUFFIXES = frozenset(
    {".pdf", ".docx", ".xlsx", ".xls", ".eml", ".txt", ".csv", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
)
WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)

# Mailbox domains that can never identify a corporate customer.
GENERIC_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "yahoo.com",
        "yahoo.ca",
        "icloud.com",
        "me.com",
        "aol.com",
        "msn.com",
        "videotron.ca",
        "bell.net",
        "sympatico.ca",
    }
)

FRP_QUOTE_RE = re.compile(r"^QT[\s\-_]?0*\d+$", re.IGNORECASE)
FRP_SALESORDER_RE = re.compile(r"^SO[\s\-_]?0*\d+$", re.IGNORECASE)
FRP_INVOICE_RE = re.compile(r"^INV[\s\-_]?0*\d+$", re.IGNORECASE)

_PO_LABEL = r"(?:p\.?\s?o\.?|p/o|purchase\s+order|purchase\s+ord\.?)"
_PO_VALUE = r"[A-Za-z0-9][A-Za-z0-9\-/_]{1,23}"
# The first lookahead forces maximal munch: without it the engine happily
# backtracks and reports "10" out of the amount "105.42". The second then
# refuses a decimal outright.
PO_LABELLED_RE = re.compile(
    rf"(?i)\b{_PO_LABEL}\s*(?:#|no\.?|nbr\.?|num(?:ber)?)?\s*[:\-\u2013]?\s*"
    rf"({_PO_VALUE})(?![A-Za-z0-9\-/_])(?![.,]\d)"
)
# The glued form these customers actually write: PO5117, PO25592, P.O.104689.
# A SEPARATED form ("PO 5117") is left to the labelled pattern, which reports the
# bare number; only the glued spelling keeps its prefix, because that is how the
# customer wrote the reference.
PO_GLUED_RE = re.compile(r"(?i)\bP\.?O\.?(\d{3,10})(?![\d.,\-/])")

# A labelled capture that is really the next English word, not a PO number.
PO_STOPWORDS = frozenset(
    {
        "attached",
        "below",
        "confirmation",
        "confirmed",
        "copy",
        "date",
        "details",
        "for",
        "from",
        "here",
        "is",
        "number",
        "numbers",
        "of",
        "on",
        "our",
        "please",
        "received",
        "reference",
        "required",
        "see",
        "shortly",
        "soon",
        "tbd",
        "terms",
        "the",
        "this",
        "to",
        "today",
        "will",
        "with",
        "your",
        "yours",
    }
)

DATE_LIKE_RE = re.compile(
    r"^(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|(?:19|20)\d{2})$"
)
AMOUNT_LIKE_RE = re.compile(r"\d[\d,]*\.\d{1,2}\s*$|^\$")

# Redaction, applied to every excerpt before it is stored.
_MONEY_PATTERNS = (
    re.compile(r"(?i)(?:cad|usd|eur|gbp)\s*\$?\s*\d[\d,]*(?:\.\d+)?"),
    re.compile(r"\$\s*\d[\d,]*(?:\.\d+)?"),
    re.compile(r"\b\d[\d,]*\.\d{2}\b"),
)
REDACTION = "[amount redacted]"

# Leak scan, run against the finished report files.
LEAK_PATTERNS = (
    ("currency amount", re.compile(r"(?i)(?:cad|usd|eur|gbp)\s*\$?\s*\d")),
    ("currency symbol", re.compile(r"\$\s*\d")),
    ("decimal figure", re.compile(r"\b\d[\d,]*\.\d{2}\b")),
    ("credential", re.compile(r"(?i)\b(?:access[_ ]?token|refresh[_ ]?token|bearer\s+[A-Za-z0-9._-]{8,}|client[_ ]?secret|api[_ ]?key|password|passwd|cookie|authorization:)\b")),
    ("account number", re.compile(r"(?i)\b(?:iban|swift|sort\s?code|routing\s?number|account\s?number)\b")),
)


class RecoveryError(RuntimeError):
    """Anything that would make the recovery report untrue."""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_reference(value: object) -> str:
    text = unicodedata.normalize("NFKC", _text(value))
    return collapse_whitespace(text)


def is_frp_quote_number(value: object) -> bool:
    return bool(FRP_QUOTE_RE.match(normalize_reference(value)))


def is_frp_salesorder_number(value: object) -> bool:
    return bool(FRP_SALESORDER_RE.match(normalize_reference(value)))


def is_frp_invoice_number(value: object) -> bool:
    return bool(FRP_INVOICE_RE.match(normalize_reference(value)))


def is_frp_document_number(value: object) -> bool:
    return (
        is_frp_quote_number(value)
        or is_frp_salesorder_number(value)
        or is_frp_invoice_number(value)
    )


def parse_iso_date(value: object) -> date | None:
    text = _text(value)
    if not text:
        return None
    candidate = text[:10]
    try:
        return date.fromisoformat(candidate)
    except ValueError:
        return None


def address_domain(address: object) -> str:
    text = _text(address).casefold()
    if "@" not in text:
        return ""
    return text.rsplit("@", 1)[1].strip("<>? ")


def is_internal_address(address: object) -> bool:
    return address_domain(address) == INTERNAL_DOMAIN


# --------------------------------------------------------------------------
# affected-order selection
# --------------------------------------------------------------------------
def load_audit(path: Path) -> dict:
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"REFUSED: the order reference audit at {path} could not be read: {exc}") from exc
    if not isinstance(report, dict) or not isinstance(report.get("salesorders"), list):
        raise RecoveryError(f"REFUSED: {path} is not an order reference audit report")
    if report.get("read_only") is not True or report.get("zoho_modified") is not False:
        raise RecoveryError(f"REFUSED: {path} does not declare itself a read-only unmodified audit")
    return report


def is_affected(order: dict) -> bool:
    """QT-numbered reference, or no reference at all."""
    reference = normalize_reference(order.get("client_po_original"))
    if not reference:
        return True
    return is_frp_quote_number(reference)


def order_sort_key(order: dict) -> tuple:
    return (_text(order.get("date")), _text(order.get("salesorder_number")), _text(order.get("salesorder_id")))


def select_affected(orders: list[dict]) -> list[dict]:
    selected = [order for order in orders if is_affected(order)]
    identifiers = [_text(order.get("salesorder_id")) for order in selected]
    if not all(identifiers):
        raise RecoveryError("REFUSED: an affected sales order carries no salesorder_id")
    if len(set(identifiers)) != len(identifiers):
        raise RecoveryError("REFUSED: the audit lists the same sales order twice")
    return sorted(selected, key=order_sort_key)


def latest_audit_directory(root: Path = AUDIT_ROOT) -> Path:
    if not root.exists():
        raise RecoveryError(f"REFUSED: no order reference audit exists under {root}")
    candidates = sorted(
        (child for child in root.iterdir() if child.is_dir() and (child / "order_reference_audit.json").exists()),
        key=lambda child: child.name,
    )
    if not candidates:
        raise RecoveryError(f"REFUSED: no order_reference_audit.json exists under {root}")
    return candidates[-1]


# --------------------------------------------------------------------------
# PO extraction
# --------------------------------------------------------------------------
def clean_candidate_value(value: str) -> str:
    text = normalize_reference(value)
    text = text.strip(".,;:()[]{}<>\"'`")
    return text.strip("-/_ ")


def reject_reason(value: str) -> str:
    """Empty string means the value is an acceptable PO candidate."""
    text = clean_candidate_value(value)
    if not text:
        return "empty"
    lowered = text.casefold()
    if lowered in PO_STOPWORDS:
        return "english word, not a reference"
    if is_frp_quote_number(text):
        return "FRP Depot quote number"
    if is_frp_salesorder_number(text):
        return "FRP Depot sales order number"
    if is_frp_invoice_number(text):
        return "FRP Depot invoice number"
    if DATE_LIKE_RE.match(text):
        return "date, not a purchase order"
    if AMOUNT_LIKE_RE.search(text):
        return "amount, not a purchase order"
    if not any(char.isdigit() for char in text):
        return "no digit, not a purchase order number"
    if len(re.sub(r"[^A-Za-z0-9]", "", text)) < 3:
        return "too short to be a purchase order number"
    return ""


def clip_excerpt(text: str, start: int, end: int, limit: int = MAX_EXCERPT_CHARS) -> str:
    """The PO-labelled fragment only, redacted, never longer than the limit."""
    if limit <= 0:
        return ""
    span = max(0, end - start)
    padding = max(0, (limit - span)) // 2
    left = max(0, start - padding)
    fragment = text[left:left + limit + 40]
    fragment = redact_amounts(collapse_whitespace(fragment))
    if len(fragment) > limit:
        fragment = fragment[:limit].rstrip()
    return fragment


def redact_amounts(text: str) -> str:
    result = text or ""
    for pattern in _MONEY_PATTERNS:
        result = pattern.sub(REDACTION, result)
    return result


def candidate_key(value: str) -> str:
    """"PO25592" and "25592" are the same purchase order written two ways."""
    core = re.sub(r"[^A-Za-z0-9]", "", value or "").casefold()
    if core.startswith("po") and len(core) > 2:
        core = core[2:]
    return core or (value or "").casefold()


def extract_po_candidates(text: str) -> list[dict]:
    """Every explicitly PO-labelled value in the text, in the order it appears,
    deduplicated across the two spellings."""
    source = text or ""
    matches: list[tuple[int, int, str, int, int]] = []
    # priority 0 = the glued spelling, which keeps the customer's own prefix and
    # therefore wins a tie against the labelled pattern at the same position.
    for priority, pattern in ((0, PO_GLUED_RE), (1, PO_LABELLED_RE)):
        for match in pattern.finditer(source):
            value = clean_candidate_value(match.group(1))
            if priority == 0 and value:
                value = f"PO{value}"
            if reject_reason(value):
                continue
            matches.append((match.start(), priority, value, match.start(), match.end()))

    found: list[dict] = []
    seen: set[str] = set()
    for _start, _priority, value, span_start, span_end in sorted(matches, key=lambda item: (item[0], item[1])):
        key = candidate_key(value)
        if key in seen:
            continue
        seen.add(key)
        found.append({"value": value, "excerpt": clip_excerpt(source, span_start, span_end)})
    return found


def looks_frp_generated(text: str) -> bool:
    """An FRP Depot quote/order/invoice PDF is not customer-provided evidence."""
    lowered = (text or "").casefold()
    if "frp depot" not in lowered and "frpdepots.com" not in lowered:
        return False
    return any(
        marker in lowered
        for marker in ("quote #", "quote no", "estimate #", "sales order #", "invoice #", "qt-000", "so-000", "inv-000")
    )


# --------------------------------------------------------------------------
# customer identity
# --------------------------------------------------------------------------
def customer_name_phrase(name: object) -> str:
    """The distinctive part of a company name, for a mailbox phrase search."""
    text = normalize_reference(name)
    text = re.sub(r"(?i)[,.]?\s*\b(?:inc|inc\.|ltd|ltee|lt[ée]e|llc|llp|corp|corporation|co|company|group of companies|group|services|enterprises|limited)\b\.?", " ", text)
    text = re.sub(r"[^A-Za-z0-9&' ]+", " ", text)
    text = collapse_whitespace(text)
    words = [word for word in text.split(" ") if len(word) > 1]
    return " ".join(words[:3])


def contact_email_domains(contact: dict) -> list[str]:
    """Every corporate mail domain Zoho holds for this customer."""
    addresses: list[str] = []
    for key in ("email", "billing_email", "customer_email"):
        addresses.append(_text((contact or {}).get(key)))
    for person in (contact or {}).get("contact_persons") or []:
        if isinstance(person, dict):
            addresses.append(_text(person.get("email")))
    domains: list[str] = []
    for address in addresses:
        domain = address_domain(address)
        if not domain or domain == INTERNAL_DOMAIN or domain in GENERIC_EMAIL_DOMAINS:
            continue
        if domain not in domains:
            domains.append(domain)
    return sorted(domains)


def message_addresses(message: dict) -> list[str]:
    addresses: list[str] = []
    sender = ((message or {}).get("from") or {}).get("emailAddress") or {}
    addresses.append(_text(sender.get("address")))
    for key in ("toRecipients", "ccRecipients", "bccRecipients", "replyTo"):
        for entry in (message or {}).get(key) or []:
            addresses.append(_text(((entry or {}).get("emailAddress") or {}).get("address")))
    return [address for address in addresses if address]


def message_sender(message: dict) -> str:
    return _text((((message or {}).get("from") or {}).get("emailAddress") or {}).get("address"))


def message_involves_customer(message: dict, domains: set[str]) -> bool:
    if not domains:
        return False
    return any(address_domain(address) in domains for address in message_addresses(message))


def message_date(message: dict) -> date | None:
    return parse_iso_date(_text(message.get("receivedDateTime")) or _text(message.get("sentDateTime")))


def within_window(evidence_day: date | None, order_day: date | None, before: int, after: int) -> bool:
    if evidence_day is None or order_day is None:
        return False
    return (order_day - timedelta(days=before)) <= evidence_day <= (order_day + timedelta(days=after))


# --------------------------------------------------------------------------
# Microsoft Graph reading (GET only)
# --------------------------------------------------------------------------
def graph_reader(access_token: str):
    """The only Graph transport in this module, with the verb pinned to GET."""

    def read(path: str) -> dict:
        return outlook_tool.graph_request(access_token, "GET", path)

    return read


def relative_graph_path(next_link: str) -> str:
    if not next_link:
        return ""
    if not next_link.startswith(GRAPH_BASE + "/"):
        raise RecoveryError(f"REFUSED: Graph returned a next link outside {GRAPH_BASE}")
    return next_link[len(GRAPH_BASE):]


def graph_paginate(graph, path: str, max_pages: int = MAX_PAGES_PER_QUERY) -> list[dict]:
    """Follow @odata.nextLink. Exceeding the ceiling ABORTS; a partial sweep is
    never reported as a complete one."""
    records: list[dict] = []
    pages = 0
    while path:
        if pages >= max_pages:
            raise RecoveryError(
                f"REFUSED: Graph paging ceiling of {max_pages} pages reached; the sweep would be partial"
            )
        page = graph(path) or {}
        pages += 1
        values = page.get("value") or []
        if not isinstance(values, list):
            raise RecoveryError("REFUSED: Graph returned a non-list value collection")
        records.extend(values)
        path = relative_graph_path(_text(page.get("@odata.nextLink")))
    return records


SEARCH_SELECT = "id,conversationId,subject,bodyPreview,receivedDateTime,sentDateTime,hasAttachments,from,toRecipients,ccRecipients"


def search_messages(graph, term: str) -> list[dict]:
    """Free-text mailbox search across every folder, received and sent.

    This mailbox's Graph endpoint rejects KQL property restrictions outright
    (``character ':' is not valid``), so the query is a quoted phrase and every
    narrowing -- date window, customer, direction -- is applied in code below.
    """
    phrase = url_quote(f'"{term}"')
    path = f"/me/messages?$search={phrase}&$top={PAGE_SIZE}&$select={SEARCH_SELECT}"
    messages = graph_paginate(graph, path)
    if len(messages) > MAX_MESSAGES_PER_QUERY:
        raise RecoveryError(f"REFUSED: query {term!r} returned more than {MAX_MESSAGES_PER_QUERY} messages")
    return messages


def fetch_message_body(graph, message_id: str) -> dict:
    encoded = url_quote(message_id, safe="")
    return graph(f"/me/messages/{encoded}?$select=id,subject,body,bodyPreview,receivedDateTime,from,toRecipients,ccRecipients") or {}


def list_attachments(graph, message_id: str) -> list[dict]:
    encoded = url_quote(message_id, safe="")
    page = graph(f"/me/messages/{encoded}/attachments?$select=id,name,contentType,size,isInline") or {}
    values = page.get("value") or []
    if not isinstance(values, list):
        raise RecoveryError("REFUSED: Graph returned a non-list attachment collection")
    return values


def fetch_attachment(graph, message_id: str, attachment_id: str) -> dict:
    encoded_message = url_quote(message_id, safe="")
    encoded_attachment = url_quote(attachment_id, safe="")
    return graph(f"/me/messages/{encoded_message}/attachments/{encoded_attachment}") or {}


def body_text(message: dict) -> str:
    body = (message or {}).get("body") or {}
    content = _text(body.get("content"))
    if _text(body.get("contentType")).casefold() == "html" or "<" in content[:400]:
        return outlook_tool.html_to_normalized_text(content) if content else ""
    return content


# --------------------------------------------------------------------------
# attachment safety
# --------------------------------------------------------------------------
def safe_attachment_filename(name: object, index: int) -> str:
    """A name that can only ever land directly inside the transient directory."""
    raw = _text(name)
    if not raw:
        raise RecoveryError("REFUSED: attachment has no name")
    if "\x00" in raw:
        raise RecoveryError("REFUSED: attachment name holds a null byte")
    if any(separator in raw for separator in ("/", "\\")):
        raise RecoveryError(f"REFUSED: attachment name {raw!r} holds a path separator")
    if raw in {".", ".."} or raw.startswith(".."):
        raise RecoveryError(f"REFUSED: attachment name {raw!r} traverses the directory")
    if ":" in raw:
        raise RecoveryError(f"REFUSED: attachment name {raw!r} holds a drive or stream separator")
    if any(ord(char) < 32 for char in raw):
        raise RecoveryError(f"REFUSED: attachment name {raw!r} holds a control character")
    suffix = Path(raw).suffix.casefold()
    if suffix not in SUPPORTED_ATTACHMENT_SUFFIXES:
        raise RecoveryError(f"REFUSED: attachment type {suffix or '(none)'} is not a supported evidence format")
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(raw).stem)[:60] or "attachment"
    if stem.casefold() in WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
    return f"{index:04d}_{stem}{suffix}"


def attachment_is_evidence_candidate(attachment: dict) -> bool:
    if _text(attachment.get("@odata.type")).casefold() != "#microsoft.graph.fileattachment":
        return False
    if attachment.get("isInline"):
        return False
    suffix = Path(_text(attachment.get("name"))).suffix.casefold()
    if suffix not in SUPPORTED_ATTACHMENT_SUFFIXES:
        return False
    size = attachment.get("size")
    if isinstance(size, (int, float)) and size > MAX_ATTACHMENT_BYTES:
        return False
    return True


def attachment_text(graph, message_id: str, attachment: dict, directory: Path, index: int) -> str:
    """Download, extract, delete. The bytes never touch the repository and never
    outlive the extraction."""
    payload = fetch_attachment(graph, message_id, _text(attachment.get("id")))
    encoded = _text(payload.get("contentBytes"))
    if not encoded:
        return ""
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RecoveryError(f"REFUSED: attachment {attachment.get('name')!r} did not decode: {exc}") from exc
    if len(raw) > MAX_ATTACHMENT_BYTES:
        return ""
    # The repository check comes BEFORE mkdir. Checking a resolved file path
    # afterwards still leaves the directory itself created inside the tree.
    if ROOT.resolve() == directory.resolve() or ROOT.resolve() in directory.resolve().parents:
        raise RecoveryError("REFUSED: attachment bytes would be written inside the repository")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / safe_attachment_filename(attachment.get("name"), index)
    if target.resolve().parent != directory.resolve():
        raise RecoveryError(f"REFUSED: attachment would be written outside {directory}")
    try:
        target.write_bytes(raw)
        text, _mode, _metadata = attachment_extract_one(target, _text(attachment.get("contentType")))
    finally:
        try:
            target.unlink()
        except OSError:
            pass
    return text or ""


def attachment_extract_one(path: Path, content_type: str):
    """Imported lazily: the extractor pulls a large vendored OCR stack that a
    unit test has no reason to load."""
    import attachment_extract  # noqa: PLC0415

    return attachment_extract.extract_one(path, content_type)


# --------------------------------------------------------------------------
# query planning
# --------------------------------------------------------------------------
def build_queries(orders: list[dict], domains_by_customer: dict[str, list[str]]) -> list[dict]:
    """One deterministic query plan; every distinct term is searched ONCE."""
    plan: dict[str, dict] = {}

    def add(term: str, kind: str, order: dict) -> None:
        term = collapse_whitespace(term)
        if not term:
            return
        entry = plan.setdefault(term, {"term": term, "kinds": set(), "salesorder_ids": set()})
        entry["kinds"].add(kind)
        entry["salesorder_ids"].add(_text(order.get("salesorder_id")))

    for order in orders:
        quote_number = normalize_reference(order.get("quote_number"))
        if quote_number:
            add(quote_number, QUERY_KIND_QUOTE, order)
        reference = normalize_reference(order.get("client_po_original"))
        if reference and is_frp_quote_number(reference):
            add(reference, QUERY_KIND_QUOTE, order)
        add(normalize_reference(order.get("salesorder_number")), QUERY_KIND_SALESORDER, order)
        add(customer_name_phrase(order.get("customer_name")), QUERY_KIND_CUSTOMER_NAME, order)
        for domain in domains_by_customer.get(_text(order.get("customer_id")), []):
            add(domain, QUERY_KIND_CUSTOMER_DOMAIN, order)

    return [
        {
            "term": entry["term"],
            "kinds": sorted(entry["kinds"]),
            "salesorder_ids": sorted(entry["salesorder_ids"]),
        }
        for entry in sorted(plan.values(), key=lambda entry: entry["term"].casefold())
    ]


# --------------------------------------------------------------------------
# evidence gathering
# --------------------------------------------------------------------------
def _evidence_record(
    tier: str,
    source_type: str,
    message: dict,
    candidate: dict,
    attachment_name: str = "",
) -> dict:
    return {
        "tier": tier,
        "source_type": source_type,
        "message_id": _text(message.get("id")),
        "date": _text(message.get("receivedDateTime"))[:10],
        "subject": collapse_whitespace(redact_amounts(_text(message.get("subject"))))[:200],
        "sender": message_sender(message),
        # Attachment names are customer-controlled report text too. Redact
        # financial figures here just as we already do for subjects/excerpts;
        # a real Purolator filename containing "$208.75" correctly tripped the
        # finished-report leak scanner on 2026-08-12.
        "attachment_name": collapse_whitespace(redact_amounts(_text(attachment_name)))[:255],
        "value": candidate["value"],
        "excerpt": candidate["excerpt"],
    }


def evidence_sort_key(evidence: dict) -> tuple:
    return (
        evidence.get("date", ""),
        evidence.get("value", "").casefold(),
        evidence.get("attachment_name", ""),
        evidence.get("message_id", ""),
    )


def collect_outlook_evidence(
    graph,
    orders: list[dict],
    queries: list[dict],
    domains_by_customer: dict[str, list[str]],
    transient_directory: Path,
    counters: dict,
) -> dict[str, list[dict]]:
    """Search, dedupe by stable id, then read only what can carry evidence."""
    orders_by_id = {_text(order.get("salesorder_id")): order for order in orders}
    # message id -> {"message": ..., "linked_orders": set, "query_orders": set}
    index: dict[str, dict] = {}

    for query in queries:
        messages = search_messages(graph, query["term"])
        counters["messages_returned"] += len(messages)
        linked = any(kind in LINKED_QUERY_KINDS for kind in query["kinds"])
        for message in messages:
            message_id = _text(message.get("id"))
            if not message_id:
                continue
            entry = index.setdefault(
                message_id,
                {"message": message, "linked_orders": set(), "query_orders": set(), "terms": set()},
            )
            entry["terms"].add(query["term"])
            entry["query_orders"].update(query["salesorder_ids"])
            if linked:
                entry["linked_orders"].update(query["salesorder_ids"])
    counters["messages_unique"] = len(index)

    evidence_by_order: dict[str, list[dict]] = {identifier: [] for identifier in orders_by_id}
    seen_attachments: set[tuple[str, str]] = set()

    for message_id in sorted(index):
        entry = index[message_id]
        message = entry["message"]
        evidence_day = message_date(message)

        # Which affected orders may this message speak for, and at which tier?
        relevance: dict[str, str] = {}
        for order_id in sorted(entry["linked_orders"]):
            order = orders_by_id.get(order_id)
            if order is None:
                continue
            if within_window(evidence_day, parse_iso_date(order.get("date")), LINKED_WINDOW_BEFORE_DAYS, LINKED_WINDOW_AFTER_DAYS):
                relevance[order_id] = TIER_LINKED
        for order_id in sorted(entry["query_orders"]):
            if order_id in relevance:
                continue
            order = orders_by_id.get(order_id)
            if order is None:
                continue
            domains = set(domains_by_customer.get(_text(order.get("customer_id")), []))
            if not message_involves_customer(message, domains):
                continue
            if within_window(evidence_day, parse_iso_date(order.get("date")), CUSTOMER_WINDOW_BEFORE_DAYS, CUSTOMER_WINDOW_AFTER_DAYS):
                relevance[order_id] = TIER_WINDOW
        if not relevance:
            continue

        preview = " ".join([_text(message.get("subject")), _text(message.get("bodyPreview"))])
        wants_body = bool(extract_po_candidates(preview)) or any(tier == TIER_LINKED for tier in relevance.values())
        has_attachments = bool(message.get("hasAttachments"))
        if not wants_body and not has_attachments:
            continue

        # Past the guard above at least one of the two is true, so the body is
        # always read: an attachment still needs its message's own context.
        if counters["body_fetches"] >= MAX_BODY_FETCHES:
            raise RecoveryError(f"REFUSED: body fetch ceiling of {MAX_BODY_FETCHES} reached; the sweep would be partial")
        full = fetch_message_body(graph, message_id) or dict(message)
        counters["body_fetches"] += 1
        # Graph's body projection omits recipients the customer test still needs.
        for key in ("toRecipients", "ccRecipients", "from", "receivedDateTime"):
            if not full.get(key) and message.get(key):
                full[key] = message[key]
        text = "\n".join([_text(full.get("subject")), body_text(full)])

        sender_is_customer = not is_internal_address(message_sender(full)) and any(
            message_involves_customer(full, set(domains_by_customer.get(_text(orders_by_id[order_id].get("customer_id")), [])))
            for order_id in relevance
        )

        message_candidates = extract_po_candidates(text) if sender_is_customer else []

        attachment_findings: list[tuple[str, dict]] = []
        if has_attachments:
            for position, attachment in enumerate(list_attachments(graph, message_id)):
                if not attachment_is_evidence_candidate(attachment):
                    continue
                attachment_id = _text(attachment.get("id"))
                key = (message_id, attachment_id)
                if key in seen_attachments:
                    continue
                seen_attachments.add(key)
                if counters["attachment_downloads"] >= MAX_ATTACHMENT_DOWNLOADS:
                    raise RecoveryError(
                        f"REFUSED: attachment ceiling of {MAX_ATTACHMENT_DOWNLOADS} reached; the sweep would be partial"
                    )
                counters["attachment_downloads"] += 1
                extracted = attachment_text(graph, message_id, attachment, transient_directory, counters["attachment_downloads"])
                if not extracted or looks_frp_generated(extracted):
                    continue
                for candidate in extract_po_candidates(extracted):
                    attachment_findings.append((_text(attachment.get("name")), candidate))

        for order_id, tier in relevance.items():
            for candidate in message_candidates:
                source = SOURCE_MESSAGE_LINKED if tier == TIER_LINKED else SOURCE_MESSAGE_WINDOW
                evidence_by_order[order_id].append(_evidence_record(tier, source, full, candidate))
            for attachment_name, candidate in attachment_findings:
                source = SOURCE_ATTACHMENT_LINKED if tier == TIER_LINKED else SOURCE_ATTACHMENT_WINDOW
                evidence_by_order[order_id].append(
                    _evidence_record(tier, source, full, candidate, attachment_name=attachment_name)
                )

    return {order_id: sorted(items, key=evidence_sort_key) for order_id, items in evidence_by_order.items()}


# --------------------------------------------------------------------------
# Drive reference cache (secondary, historical, clearly labelled)
# --------------------------------------------------------------------------
def drive_cache_evidence(connection, order: dict) -> list[dict]:
    """Drive rows only. This function never reads a gmail table."""
    terms = [
        normalize_reference(order.get("quote_number")),
        normalize_reference(order.get("salesorder_number")),
    ]
    findings: list[dict] = []
    seen: set[str] = set()
    for term in [term for term in terms if term]:
        try:
            rows = connection.execute(
                "SELECT id, name, content FROM drive_fts WHERE drive_fts MATCH ? LIMIT ?",
                (f'"{term}"', MAX_DRIVE_ROWS_PER_QUERY),
            ).fetchall()
        except sqlite3.Error as exc:
            raise RecoveryError(f"REFUSED: the Drive reference cache could not be searched: {exc}") from exc
        for row in rows:
            file_id = _text(row[0])
            name = _text(row[1])
            content = _text(row[2])
            if looks_frp_generated(content):
                continue
            for candidate in extract_po_candidates("\n".join([name, content])):
                key = f"{file_id}:{candidate['value'].casefold()}"
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    {
                        "tier": TIER_CACHE,
                        "source_type": SOURCE_DRIVE_CACHE,
                        "message_id": "",
                        "date": "",
                        "subject": "",
                        "sender": "",
                        "attachment_name": name,
                        "value": candidate["value"],
                        "excerpt": candidate["excerpt"],
                    }
                )
    return sorted(findings, key=evidence_sort_key)


def open_drive_cache(path: Path = DRIVE_REFERENCE_DB):
    if not Path(path).exists():
        return None
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


# --------------------------------------------------------------------------
# decision
# --------------------------------------------------------------------------
def decide(order: dict, evidence: list[dict]) -> dict:
    """Tier 1 first; Tier 2 only when Tier 1 is silent; cache last and labelled."""
    reference = normalize_reference(order.get("client_po_original"))
    blank_reference = not reference

    chosen: list[dict] = []
    for tier in (TIER_LINKED, TIER_WINDOW, TIER_CACHE):
        chosen = [item for item in evidence if item["tier"] == tier]
        if chosen:
            break

    values: list[str] = []
    for item in chosen:
        if item["value"] not in values:
            values.append(item["value"])
    values.sort(key=str.casefold)

    row = {column: "" for column in REPORT_COLUMNS}
    row.update(
        {
            "salesorder_id": _text(order.get("salesorder_id")),
            "salesorder_number": _text(order.get("salesorder_number")),
            "date": _text(order.get("date")),
            "customer_id": _text(order.get("customer_id")),
            "customer_name": _text(order.get("customer_name")),
            "current_reference": reference,
            "linked_quote_id": _text(order.get("quote_id")),
            "linked_quote_number": _text(order.get("quote_number")),
            "evidence_source_type": SOURCE_NONE,
            "confidence": CONFIDENCE_NONE,
            "conflict_candidates": [],
            "recommended_action": ACTION_LEAVE_UNCHANGED,
        }
    )

    if not values:
        return row

    first = sorted(chosen, key=evidence_sort_key)[0]
    row.update(
        {
            "evidence_source_type": first["source_type"],
            "evidence_message_id": first["message_id"],
            "evidence_date": first["date"],
            "evidence_subject": first["subject"],
            "evidence_sender": first["sender"],
            "evidence_attachment_name": first["attachment_name"],
            "evidence_excerpt": first["excerpt"],
        }
    )

    if len(values) > 1:
        row["confidence"] = CONFIDENCE_AMBIGUOUS
        row["conflict_candidates"] = values
        row["recommended_action"] = ACTION_MANUAL_REVIEW
        return row

    row["recovered_client_po"] = values[0]
    row["confidence"] = CONFIDENCE_CERTAIN
    row["recommended_action"] = ACTION_FILL_BLANK if blank_reference else ACTION_REPLACE_QUOTE
    return row


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def _csv_value(value: object) -> str:
    if isinstance(value, list):
        return ";".join(_text(item) for item in value)
    return _text(value)


def assert_rows_are_closed(rows: list[dict], affected: list[dict]) -> None:
    expected_ids = [_text(order.get("salesorder_id")) for order in affected]
    seen_ids = [_text(row.get("salesorder_id")) for row in rows]
    if sorted(seen_ids) != sorted(expected_ids):
        raise RecoveryError("REFUSED: the report does not hold exactly the affected sales orders, once each")
    for row in rows:
        if set(row.keys()) != set(REPORT_COLUMNS):
            raise RecoveryError(f"REFUSED: row {row.get('salesorder_number')} does not carry the closed column set")
        if row["recommended_action"] not in RECOMMENDED_ACTIONS:
            raise RecoveryError(f"REFUSED: row {row.get('salesorder_number')} carries an unknown recommended action")
        if row["evidence_source_type"] not in EVIDENCE_SOURCE_TYPES:
            raise RecoveryError(f"REFUSED: row {row.get('salesorder_number')} carries an unknown evidence source type")
        if row["confidence"] not in (CONFIDENCE_CERTAIN, CONFIDENCE_AMBIGUOUS, CONFIDENCE_NONE):
            raise RecoveryError(f"REFUSED: row {row.get('salesorder_number')} carries an unknown confidence")
        if len(_text(row["evidence_excerpt"])) > MAX_EXCERPT_CHARS:
            raise RecoveryError(f"REFUSED: row {row.get('salesorder_number')} excerpt exceeds {MAX_EXCERPT_CHARS} characters")
        if row["confidence"] == CONFIDENCE_CERTAIN and not row["recovered_client_po"]:
            raise RecoveryError(f"REFUSED: row {row.get('salesorder_number')} claims certainty with no value")
        if row["confidence"] != CONFIDENCE_CERTAIN and row["recovered_client_po"]:
            raise RecoveryError(f"REFUSED: row {row.get('salesorder_number')} carries a value without certainty")
        if row["recovered_client_po"] and reject_reason(row["recovered_client_po"]):
            raise RecoveryError(
                f"REFUSED: row {row.get('salesorder_number')} recovered a refused value {row['recovered_client_po']!r}"
            )


def summarize(rows: list[dict], counters: dict) -> dict:
    certain = [row for row in rows if row["confidence"] == CONFIDENCE_CERTAIN]
    return {
        "affected_salesorders": len(rows),
        "quote_number_references": sum(1 for row in rows if is_frp_quote_number(row["current_reference"])),
        "blank_references": sum(1 for row in rows if not row["current_reference"]),
        "certain_recoveries": len(certain),
        "ambiguous_cases": sum(1 for row in rows if row["confidence"] == CONFIDENCE_AMBIGUOUS),
        "no_evidence_cases": sum(1 for row in rows if row["confidence"] == CONFIDENCE_NONE),
        "certain_quote_reference_replacements": sum(1 for row in certain if row["recommended_action"] == ACTION_REPLACE_QUOTE),
        "certain_blank_reference_fills": sum(1 for row in certain if row["recommended_action"] == ACTION_FILL_BLANK),
        "evidence_source_types": {
            source: sum(1 for row in rows if row["evidence_source_type"] == source)
            for source in EVIDENCE_SOURCE_TYPES
            if any(row["evidence_source_type"] == source for row in rows)
        },
        "search_queries": counters.get("queries", 0),
        "messages_returned": counters.get("messages_returned", 0),
        "messages_unique": counters.get("messages_unique", 0),
        "message_bodies_read": counters.get("body_fetches", 0),
        "attachments_extracted": counters.get("attachment_downloads", 0),
        "zoho_writes": 0,
        "outlook_drafts_created": 0,
        "emails_sent": 0,
    }


def write_query_batches(directory: Path, orders: list[dict], queries: list[dict], batch_size: int = BATCH_SIZE) -> list[str]:
    """The source queries, batched at no more than ten affected orders each."""
    if batch_size > BATCH_SIZE:
        raise RecoveryError(f"REFUSED: batch size {batch_size} exceeds the {BATCH_SIZE} order ceiling")
    directory.mkdir(parents=True, exist_ok=True)
    by_order: dict[str, list[dict]] = {}
    for order in orders:
        order_id = _text(order.get("salesorder_id"))
        by_order[order_id] = [
            {"term": query["term"], "kinds": query["kinds"]}
            for query in queries
            if order_id in query["salesorder_ids"]
        ]
    total = max(1, (len(orders) + batch_size - 1) // batch_size)
    written: list[str] = []
    for index in range(0, max(len(orders), 1), batch_size):
        chunk = orders[index:index + batch_size]
        if not chunk and orders:
            break
        path = directory / f"{BATCH_PREFIX}_batch_{index // batch_size + 1:03d}_of_{total:03d}.json"
        path.write_text(
            json.dumps(
                {
                    "collection": BATCH_PREFIX,
                    "batch_orders": len(chunk),
                    "orders": [
                        {
                            "salesorder_id": _text(order.get("salesorder_id")),
                            "salesorder_number": _text(order.get("salesorder_number")),
                            "date": _text(order.get("date")),
                            "customer_name": _text(order.get("customer_name")),
                            "current_reference": normalize_reference(order.get("client_po_original")),
                            "linked_quote_number": _text(order.get("quote_number")),
                            "queries": by_order.get(_text(order.get("salesorder_id")), []),
                        }
                        for order in chunk
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        written.append(str(path))
        if not orders:
            break
    return written


def write_reports(directory: Path, rows: list[dict], summary: dict, generated_utc: str, audit_path: str) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / JSON_REPORT_NAME
    csv_path = directory / CSV_REPORT_NAME
    markdown_path = directory / MARKDOWN_REPORT_NAME

    report = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "generated_utc": generated_utc,
        "source_audit": audit_path,
        "read_only": True,
        "zoho_modified": False,
        "outlook_modified": False,
        "emails_sent": 0,
        "financial_data_included": False,
        "columns": list(REPORT_COLUMNS),
        "summary": summary,
        "salesorders": rows,
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(REPORT_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column, "")) for column in REPORT_COLUMNS})

    lines = [
        "# FRP Depot historical client PO evidence recovery",
        "",
        f"Generated {generated_utc} UTC by {TOOL_NAME} {TOOL_VERSION}. READ-ONLY: "
        f"{summary['zoho_writes']} Zoho writes, {summary['outlook_drafts_created']} Outlook drafts, "
        f"{summary['emails_sent']} emails sent.",
        "",
        f"Source audit: {audit_path}",
        "",
        "No price, tax, discount, total, balance, payment or bank value appears in this report.",
        "",
        "| Count | Value |",
        "| --- | --- |",
        f"| Affected Sales Orders checked | {summary['affected_salesorders']} |",
        f"| Quote-number references | {summary['quote_number_references']} |",
        f"| Blank references | {summary['blank_references']} |",
        f"| Certain PO recoveries | {summary['certain_recoveries']} |",
        f"| Ambiguous cases | {summary['ambiguous_cases']} |",
        f"| No defensible evidence | {summary['no_evidence_cases']} |",
        f"| Certain: replace quote reference | {summary['certain_quote_reference_replacements']} |",
        f"| Certain: fill blank reference | {summary['certain_blank_reference_fills']} |",
        f"| Search queries run | {summary['search_queries']} |",
        f"| Unique messages examined | {summary['messages_unique']} |",
        f"| Message bodies read | {summary['message_bodies_read']} |",
        f"| Attachments extracted | {summary['attachments_extracted']} |",
        "",
        "## Every affected Sales Order",
        "",
        "| Sales Order | Date | Customer | Current reference | Recovered client PO | Confidence | Evidence | Action |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {number} | {date} | {customer} | {current} | {po} | {confidence} | {source} | {action} |".format(
                number=row["salesorder_number"] or row["salesorder_id"],
                date=row["date"],
                customer=_text(row["customer_name"]).replace("|", "/"),
                current=_text(row["current_reference"]).replace("|", "/") or "(blank)",
                po=_text(row["recovered_client_po"]).replace("|", "/")
                or (";".join(row["conflict_candidates"]).replace("|", "/") if row["conflict_candidates"] else "(none)"),
                confidence=row["confidence"],
                source=row["evidence_source_type"],
                action=row["recommended_action"],
            )
        )
    lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")

    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(markdown_path)}


def scan_for_leaks(paths: dict) -> None:
    """Re-read the finished files. A figure or credential that survived
    redaction aborts the run rather than being published."""
    for label, path in sorted(paths.items()):
        text = Path(path).read_text(encoding="utf-8-sig")
        for name, pattern in LEAK_PATTERNS:
            match = pattern.search(text)
            if match:
                raise RecoveryError(
                    f"REFUSED: the {label} report leaked a {name} at offset {match.start()}"
                )


def assert_no_raw_material(directory: Path) -> None:
    """Only the three reports and the query batches may exist in the output."""
    allowed_files = {JSON_REPORT_NAME, CSV_REPORT_NAME, MARKDOWN_REPORT_NAME}
    for child in sorted(Path(directory).iterdir()):
        if child.is_dir():
            if child.name != "batches":
                raise RecoveryError(f"REFUSED: unexpected directory {child} in the output folder")
            for batch in sorted(child.iterdir()):
                if not batch.name.startswith(BATCH_PREFIX) or batch.suffix != ".json":
                    raise RecoveryError(f"REFUSED: unexpected file {batch} in the batches folder")
            continue
        if child.name not in allowed_files:
            raise RecoveryError(f"REFUSED: unexpected file {child} in the output folder")


def append_receipt(paths: dict, summary: dict) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": "zoho_client_po_evidence_recovery_read_only",
        "evidence": [paths["json"], paths["csv"], paths["markdown"]],
        "zoho_writes": 0,
        "outlook_drafts_created": 0,
        "emails_sent": 0,
        "affected_salesorders": summary["affected_salesorders"],
        "certain_recoveries": summary["certain_recoveries"],
    }
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------
def resolve_customer_domains(zoho_get, orders: list[dict]) -> dict[str, list[str]]:
    domains: dict[str, list[str]] = {}
    for customer_id in sorted({_text(order.get("customer_id")) for order in orders if _text(order.get("customer_id"))}):
        payload = zoho_get(f"/books/v3/contacts/{customer_id}") or {}
        contact = payload.get("contact") or {}
        domains[customer_id] = contact_email_domains(contact)
    return domains


def run_recovery(
    graph,
    zoho_get,
    audit_path: Path,
    output_directory: Path,
    transient_directory: Path,
    drive_connection=None,
    generated_utc: str | None = None,
) -> dict:
    """The whole recovery against injected read transports. Nothing here writes
    to Zoho, Outlook, Drive or the mailbox."""
    generated_utc = generated_utc or datetime.now(timezone.utc).isoformat()
    audit = load_audit(audit_path)
    affected = select_affected(audit["salesorders"])
    if not affected:
        raise RecoveryError("REFUSED: the audit names no affected sales order")

    domains_by_customer = resolve_customer_domains(zoho_get, affected)
    queries = build_queries(affected, domains_by_customer)
    counters = {
        "queries": len(queries),
        "messages_returned": 0,
        "messages_unique": 0,
        "body_fetches": 0,
        "attachment_downloads": 0,
    }

    evidence_by_order = collect_outlook_evidence(
        graph, affected, queries, domains_by_customer, transient_directory, counters
    )

    if drive_connection is not None:
        for order in affected:
            order_id = _text(order.get("salesorder_id"))
            if evidence_by_order.get(order_id):
                continue
            evidence_by_order[order_id] = drive_cache_evidence(drive_connection, order)

    rows = [decide(order, evidence_by_order.get(_text(order.get("salesorder_id")), [])) for order in affected]
    assert_rows_are_closed(rows, affected)
    summary = summarize(rows, counters)

    batch_paths = write_query_batches(output_directory / "batches", affected, queries)
    paths = write_reports(output_directory, rows, summary, generated_utc, str(audit_path))
    scan_for_leaks(paths)
    assert_no_raw_material(output_directory)
    return {"paths": paths, "batch_paths": batch_paths, "summary": summary, "rows": rows}


def cleanup_transient(directory: Path) -> None:
    path = Path(directory)
    if not path.exists():
        return
    if ROOT.resolve() in path.resolve().parents:
        raise RecoveryError("REFUSED: the transient directory is inside the repository")
    for child in sorted(path.iterdir()):
        if child.is_file():
            try:
                child.unlink()
            except OSError:
                pass
    try:
        path.rmdir()
    except OSError:
        pass


def command_run(args: argparse.Namespace) -> int:
    audit_path = Path(args.audit) if args.audit else (latest_audit_directory() / "order_reference_audit.json")

    access_token, scopes = outlook_tool.refresh_access_token()
    if outlook_tool.FORBIDDEN_TOKEN_SCOPE in scopes:
        raise RecoveryError("REFUSED: the Outlook token carries a send scope")
    graph = graph_reader(access_token)

    vault = zoho_tool.load_vault()
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = str(vault["api_domain"])
    organization_id = str(vault["books_organization_id"])

    def zoho_get(path: str) -> dict:
        separator = "&" if "?" in path else "?"
        return zoho_tool.api_get(token, domain, f"{path}{separator}organization_id={organization_id}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_directory = Path(args.output_root or OUTPUT_ROOT) / stamp
    transient_directory = Path(args.transient_root or TRANSIENT_ROOT) / stamp

    drive_connection = None if args.no_drive else open_drive_cache()
    try:
        result = run_recovery(
            graph,
            zoho_get,
            audit_path,
            output_directory,
            transient_directory,
            drive_connection=drive_connection,
        )
    finally:
        if drive_connection is not None:
            drive_connection.close()
        cleanup_transient(transient_directory)

    append_receipt(result["paths"], result["summary"])
    print(
        json.dumps(
            {
                "report_paths": result["paths"],
                "batch_files": len(result["batch_paths"]),
                "summary": result["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FRP Depot READ-ONLY historical client PO evidence recovery"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Recover client PO evidence for every affected Sales Order")
    run.add_argument("--audit", default=None, help="Path to order_reference_audit.json (default: newest)")
    run.add_argument("--output-root", default=None)
    run.add_argument("--transient-root", default=None)
    run.add_argument("--no-drive", action="store_true", help="Skip the Drive reference cache fallback")
    run.set_defaults(func=command_run)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except (RecoveryError, zoho_tool.ZohoError, outlook_tool.OutlookError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
