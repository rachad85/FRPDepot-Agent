"""Dado's personal Gmail + unrestricted Drive tool (read + draft-only).

Run:  python google_tool.py connect                       one-time / weekly sign-in
      python google_tool.py check                         verify access, no browser
      python google_tool.py gmail-search --query "..."     [--limit 10]
      python google_tool.py gmail-read --id <messageId>
      python google_tool.py gmail-draft --to a@b.com --subject "..." --body-file body.txt
      python google_tool.py drive-search --query "..."     [--limit 10]
      python google_tool.py drive-read --id <fileId>

SCREENING, as it stands 2026-07-24:
  GMAIL - screened through tdi_filter.is_tdi_flagged(). Flagged results are
          withheld, not silently dropped: search reports how many were held
          back, and a direct --id read of a flagged message is refused. This
          is a keyword screen, not a guarantee - see tdi_filter.py.
  DRIVE - NOT screened. Rachad removed the Drive filter on 2026-07-24:
          "no walls/restrictions for Drive and zoho ... do not add any walls
          unless I specifically ask for it". It is his own Drive, spanning
          both his companies.
Do not add screening to either path on your own initiative; Rachad asks for
restrictions when he wants them.

No send path exists anywhere in this file (Golden Rule 1). gmail-draft only
ever calls users.drafts.create; there is no users.messages.send call in this
tree, on purpose.

Search --limit is capped at MAX_SEARCH_LIMIT: the 2026-07-22 conduct review
(FINDING 1) traced a 69-minute circling stall to an uncapped bulk pull done
in one shot instead of small batches. Keep single calls small here too.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import google_auth
from tdi_filter import is_tdi_flagged

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
DRIVE = "https://www.googleapis.com/drive/v3"
MAX_SEARCH_LIMIT = 50


class GoogleError(Exception):
    pass


def _call(method: str, url: str, token: str, body: dict | None = None) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise GoogleError(f"{method} {url} -> HTTP {e.code}: {detail[:500]}") from e
    except URLError as e:
        raise GoogleError(f"{method} {url} -> {e.reason}") from e


def _header(headers: list, name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _plain_text_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        return _b64url_decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _plain_text_body(part)
        if text:
            return text
    return ""


def command_connect(args: argparse.Namespace) -> None:
    if not google_auth.self_check(interactive=True):
        raise GoogleError("sign-in incomplete - see the warnings above")


def command_reconnect(args: argparse.Namespace) -> None:
    """Force a brand-new sign-in even if the stored one still works.

    'connect' returns early on a healthy token and never opens a browser, so it
    cannot replace a token minted under the old "Testing" publishing status.
    Use this whenever a NEW token is the point, not merely a working one.
    """
    if not google_auth.self_check(interactive=True, force=True):
        raise GoogleError("re-consent incomplete - see the warnings above")


def command_check(args: argparse.Namespace) -> None:
    if not google_auth.self_check(interactive=False):
        raise GoogleError("check failed - see the warnings above")


def _message_is_tdi(msg: dict) -> bool:
    """Screen a WHOLE message - every header value, the snippet, and the full
    body text.

    Screening only Subject/From is not enough and was a live bug (found
    2026-07-24 during Rachad's first connected verification): a Gmail `q=`
    search matches on BODY text, so a TDI bank thread whose subject is
    "Re: Documents" from a banker's own address passed the metadata check
    and was handed to Dado. Screen everything the caller could see, and
    screen it on full content - not on the handful of fields we happen to
    display.

    Deliberately still the NARROW is_tdi_flagged, not the wider
    deep_tdi_marker used by the reference cache. Probing the cache on
    2026-07-24 found TDI material the narrow terms miss (Q26-#### quote
    numbers, Aze artifacts, the "Dumalac" misspelling), and widening this
    screen too was considered and REVERTED: the header of this file records
    Rachad's standing instruction not to add screening to either path
    unasked. Raise it with him; do not tighten it here on your own.
    """
    headers = msg.get("payload", {}).get("headers", [])
    header_values = [h.get("value", "") for h in headers]
    body = _plain_text_body(msg.get("payload", {}))
    return is_tdi_flagged(*header_values, msg.get("snippet", ""), body)


def command_gmail_search(args: argparse.Namespace) -> None:
    token = google_auth.get_token(interactive=False)
    limit = min(args.limit, MAX_SEARCH_LIMIT)
    listing = _call("GET", f"{GMAIL}/messages?{urlencode({'q': args.query, 'maxResults': limit})}", token)
    results, withheld = [], 0
    for item in listing.get("messages", []):
        # format=full (not metadata): the body must be screened, because that
        # is what the search itself matched on.
        msg = _call("GET", f"{GMAIL}/messages/{item['id']}?format=full", token)
        if _message_is_tdi(msg):
            withheld += 1
            continue
        headers = msg.get("payload", {}).get("headers", [])
        results.append({"id": msg["id"], "subject": _header(headers, "Subject"),
                         "from": _header(headers, "From"), "date": _header(headers, "Date"),
                         "snippet": msg.get("snippet", "")})
    print(json.dumps({"results": results, "withheld_tdi_flagged": withheld}, indent=2))


def command_gmail_read(args: argparse.Namespace) -> None:
    token = google_auth.get_token(interactive=False)
    msg = _call("GET", f"{GMAIL}/messages/{args.id}?format=full", token)
    if _message_is_tdi(msg):
        raise GoogleError(f"BLOCKED: message {args.id} is TDI-flagged (a header, the snippet, or the "
                           "body matched tdi_filter.py). Ask Rachad to pull this through Aze instead.")
    headers = msg.get("payload", {}).get("headers", [])
    body = _plain_text_body(msg.get("payload", {})) or msg.get("snippet", "")
    print(json.dumps({"id": msg["id"], "subject": _header(headers, "Subject"),
                       "from": _header(headers, "From"), "body": body}, indent=2))


def command_gmail_draft(args: argparse.Namespace) -> None:
    token = google_auth.get_token(interactive=False)
    body_text = Path(args.body_file).read_text(encoding="utf-8")
    msg = EmailMessage()
    msg["To"] = args.to
    msg["Subject"] = args.subject
    msg.set_content(body_text)
    if args.body_html_file:
        msg.add_alternative(Path(args.body_html_file).read_text(encoding="utf-8"), subtype="html")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    created = _call("POST", f"{GMAIL}/drafts", token, {"message": {"raw": raw}})
    draft_id = created["id"]
    # Read back what was actually created before calling it done - same
    # discipline as Aze's KYC draft workflow (verify To/subject/body, not
    # just trust the create response).
    check = _call("GET", f"{GMAIL}/drafts/{draft_id}", token)
    dmsg = check.get("message", {})
    dheaders = dmsg.get("payload", {}).get("headers", [])
    print(json.dumps({
        "draft_id": draft_id,
        "to": _header(dheaders, "To"),
        "subject": _header(dheaders, "Subject"),
        "snippet": dmsg.get("snippet", ""),
        "status": "DRAFT ONLY - not sent, no send path exists",
    }, indent=2))


def command_drive_search(args: argparse.Namespace) -> None:
    token = google_auth.get_token(interactive=False)
    limit = min(args.limit, MAX_SEARCH_LIMIT)
    q = f"fullText contains {json.dumps(args.query)} and trashed = false"
    listing = _call("GET", f"{DRIVE}/files?"
                     f"{urlencode({'q': q, 'pageSize': limit, 'fields': 'files(id,name,mimeType,parents,webViewLink)'})}",
                     token)
    # NO TDI SCREEN ON DRIVE. Rachad removed it 2026-07-24: "no walls/
    # restrictions for Drive and zoho ... do not add any walls unless I
    # specifically ask for it". Drive is his own account across both his
    # companies. Do not reintroduce filtering here without him asking.
    results = list(listing.get("files", []))
    print(json.dumps({"results": results, "screening": "none - Drive is unfiltered by Rachad's instruction"},
                      indent=2))


def command_drive_read(args: argparse.Namespace) -> None:
    token = google_auth.get_token(interactive=False)
    meta = _call("GET", f"{DRIVE}/files/{args.id}?fields=id,name,mimeType", token)
    if meta.get("mimeType", "").startswith("application/vnd.google-apps"):
        url = f"{DRIVE}/files/{args.id}/export?{urlencode({'mimeType': 'text/plain'})}"
    else:
        url = f"{DRIVE}/files/{args.id}?alt=media"
    req = Request(url)
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urlopen(req, timeout=30) as r:
            content = r.read()
    except HTTPError as e:
        raise GoogleError(f"GET {url} -> HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}") from e
    text = content.decode("utf-8", "replace")
    # No content screen either - same instruction as drive-search above.
    print(json.dumps({"id": meta["id"], "name": meta["name"], "text": text[:20000],
                       "truncated": len(text) > 20000}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    connect = commands.add_parser("connect", help="One-time / weekly Google sign-in")
    connect.set_defaults(func=command_connect)
    check = commands.add_parser("check", help="Verify Gmail + Drive access, no browser")
    check.set_defaults(func=command_check)
    reconnect = commands.add_parser(
        "reconnect", help="Force a brand-new sign-in even if the current one still works")
    reconnect.set_defaults(func=command_reconnect)

    gs = commands.add_parser("gmail-search", help="Search personal Gmail (TDI-filtered)")
    gs.add_argument("--query", required=True, help="Gmail search syntax, e.g. 'from:x subject:y'")
    gs.add_argument("--limit", type=int, default=10, help=f"max {MAX_SEARCH_LIMIT}")
    gs.set_defaults(func=command_gmail_search)

    gr = commands.add_parser("gmail-read", help="Read one Gmail message by id (TDI-filtered)")
    gr.add_argument("--id", required=True)
    gr.set_defaults(func=command_gmail_read)

    gd = commands.add_parser("gmail-draft", help="Create a Gmail draft (never sent - no send path exists)")
    gd.add_argument("--to", required=True)
    gd.add_argument("--subject", required=True)
    gd.add_argument("--body-file", required=True, help="Path to a plain-text file holding ONLY the draft body")
    gd.add_argument("--body-html-file", help="Optional path to an HTML alternative body")
    gd.set_defaults(func=command_gmail_draft)

    ds = commands.add_parser("drive-search", help="Search personal Drive (unrestricted)")
    ds.add_argument("--query", required=True, help="Text to search for in file contents/names")
    ds.add_argument("--limit", type=int, default=10, help=f"max {MAX_SEARCH_LIMIT}")
    ds.set_defaults(func=command_drive_search)

    dr = commands.add_parser("drive-read", help="Read one Drive file's text content by id (unrestricted)")
    dr.add_argument("--id", required=True)
    dr.set_defaults(func=command_drive_read)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
        return 0
    except (GoogleError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
