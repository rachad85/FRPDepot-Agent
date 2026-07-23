"""Dado's FRP Depot inbox + calendar check. READ-ONLY - only HTTP GETs exist here.

Run:  python outlook_check.py [n_messages]   inbox + calendar, each thread tagged
                                             with WHO SPOKE LAST + whether Rachad
                                             is a direct recipient
      python outlook_check.py --awaiting [days_back]
                                             JSON list of conversations that still
                                             wait on Rachad (the sweep's candidates)
      python outlook_check.py --sent [n]     recent Sent Items (his own promises)
      python outlook_check.py --thread <convId>
                                             full one-conversation dump with bodies

WHY THE REPLY TAGS EXIST: Dado must never nag Rachad about a thread he already
answered. The plain inbox view can't tell - the answer lives in Sent Items. So
for every message listed, this resolves the whole conversation and tags who
really spoke last:
  [YOU replied last]        Rachad answered the OUTSIDE party - handled, don't surface.
  [fwd internally-waiting]  Rachad's last mail went only to internal addresses;
                            the outside party is still waiting - NOT resolved.
  [handled internally]      another frpdepots.com address spoke last.
  [awaits YOU]              an outside party spoke last and no one has answered.
  [draft pending]           an unsent reply draft exists in the conversation
                            (Dado prepared it; Rachad has not pressed Send).
Automated senders (out-of-office, no-reply@, mailer-daemon) are ignored when
deciding who spoke last. Each inbox line also shows [to you] vs [cc]. No writes.

Adapted 2026-07-23 from Aze's outlook_check.py (sanctioned pattern reuse - the
logic only; no TDI data). Auth and Graph plumbing come from outlook_tool.py.
"""
from __future__ import annotations

import datetime
import html as html_lib
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import outlook_tool as ot  # noqa: E402  (auth + shared helpers, read-only use)

GRAPH = "https://graph.microsoft.com/v1.0"
INTERNAL_DOMAIN = "frpdepots.com"
AUTO_PREFIXES = ("no-reply", "noreply", "no_reply", "donotreply", "do-not-reply",
                 "mailer-daemon", "mailerdaemon", "postmaster", "bounce",
                 "notification", "notifications", "automated", "auto-reply")
_MY_ADDR = None
_THREAD_CACHE: dict[str, dict] = {}


def get(token: str, path: str, _tries: int = 3) -> dict:
    """GET with the Eastern-time Prefer header and a 429 retry (Aze's pattern)."""
    req = urllib.request.Request(GRAPH + path)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Prefer", 'outlook.timezone="Eastern Standard Time"')
    for attempt in range(_tries):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < _tries - 1:
                ra = e.headers.get("Retry-After")
                try:
                    delay = min(float(ra), 10) if ra else 2 * (attempt + 1)
                except ValueError:
                    delay = 2 * (attempt + 1)
                time.sleep(delay)
                continue
            raise
    return {}


def _addr(m: dict, field: str) -> str:
    return ((m.get(field) or {}).get("emailAddress") or {}).get("address", "").lower()


def _to_list(m: dict, field: str) -> list[str]:
    return [((r.get("emailAddress") or {}).get("address") or "").lower()
            for r in (m.get(field) or [])]


def _recipients(m: dict) -> list[str]:
    return [a for f in ("toRecipients", "ccRecipients") for a in _to_list(m, f) if a]


def _when(m: dict) -> str:
    return m.get("sentDateTime") or m.get("receivedDateTime") or ""


def _is_internal(addr: str) -> bool:
    a = (addr or "").lower()
    return a.endswith("@" + INTERNAL_DOMAIN) or a.endswith("." + INTERNAL_DOMAIN)


def _is_automated(addr: str) -> bool:
    a = (addr or "").lower()
    local = a.split("@", 1)[0]
    return any(local.startswith(p) for p in AUTO_PREFIXES) \
        or "mailer-daemon" in a or "postmaster" in a


def my_address(token: str) -> str:
    """Rachad's FRP Depot address via /me (User.Read is granted); Sent Items fallback."""
    global _MY_ADDR
    if _MY_ADDR is not None:
        return _MY_ADDR
    try:
        _MY_ADDR = ot.mailbox_address(get(token, "/me")).lower()
    except Exception:
        try:
            d = get(token, "/me/mailFolders/sentitems/messages?$top=1"
                           "&$orderby=sentDateTime%20desc&$select=from")
            v = d.get("value", [])
            _MY_ADDR = _addr(v[0], "from") if v else ""
        except Exception:
            _MY_ADDR = ""
    return _MY_ADDR


def _conversation(token: str, conversation_id: str) -> list[dict]:
    safe = conversation_id.replace("'", "''")
    flt = urllib.parse.quote(f"conversationId eq '{safe}'")
    data = get(token, "/me/messages?$filter=" + flt +
                      "&$select=id,subject,from,toRecipients,ccRecipients,"
                      "sentDateTime,receivedDateTime,isDraft&$top=50")
    msgs = data.get("value", [])
    msgs.sort(key=_when)
    return msgs


def thread_state(token: str, conversation_id: str, my_addr: str) -> dict:
    """Who really spoke last + whether an unsent draft exists. Fail-safe: any
    error -> empty tag (never a misleading one). Cached per conversationId."""
    if not conversation_id:
        return {"tag": "", "last_from": "", "last_when": "", "draft_pending": False}
    if conversation_id in _THREAD_CACHE:
        return _THREAD_CACHE[conversation_id]
    res = {"tag": "", "last_from": "", "last_when": "", "draft_pending": False}
    try:
        msgs = _conversation(token, conversation_id)
        if msgs:
            drafts = [m for m in msgs if m.get("isDraft") is True]
            human = [m for m in msgs
                     if m.get("isDraft") is not True
                     and not _is_automated(_addr(m, "from"))]
            ref = human[-1] if human else msgs[-1]
            lf = _addr(ref, "from")
            lw = _when(ref)[:16].replace("T", " ")
            if my_addr and lf == my_addr:
                ext = [r for r in _recipients(ref) if r and not _is_internal(r)]
                tag = "[YOU replied last]" if ext else "[fwd internally-waiting]"
            elif _is_internal(lf):
                tag = "[handled internally]"
            elif lf:
                tag = "[awaits YOU]"
            else:
                tag = ""
            res = {"tag": tag, "last_from": lf, "last_when": lw,
                   "draft_pending": bool(drafts), "n": len(msgs)}
    except Exception:
        pass
    _THREAD_CACHE[conversation_id] = res
    return res


def _waiting_since(msgs: list[dict], my_addr: str) -> str:
    """Date of the first external message nobody has answered (wait-clock start)."""
    human = [m for m in msgs
             if m.get("isDraft") is not True and not _is_automated(_addr(m, "from"))]
    last_owner_external = -1
    for i, m in enumerate(human):
        if _addr(m, "from") == my_addr and any(
                not _is_internal(r) for r in _recipients(m)):
            last_owner_external = i
    for m in human[last_owner_external + 1:]:
        if _addr(m, "from") and not _is_internal(_addr(m, "from")):
            return _when(m)
    return ""


def show_inbox(token: str, n: int) -> None:
    my_addr = my_address(token)
    inbox = get(token, "/me/mailFolders/inbox")
    print(f"INBOX: {inbox.get('unreadItemCount', '?')} unread / "
          f"{inbox.get('totalItemCount', '?')} total   (you = {my_addr or '?'})")
    print("flag date  from | subject | [to you]/[cc] | WHO SPOKE LAST")
    msgs = get(token, f"/me/mailFolders/inbox/messages?$top={n}"
                      "&$select=subject,from,toRecipients,ccRecipients,"
                      "receivedDateTime,isRead,conversationId"
                      "&$orderby=receivedDateTime%20desc")
    for m in msgs.get("value", []):
        frm = _addr(m, "from")
        flag = "  " if m.get("isRead") else "* "
        when = (m.get("receivedDateTime") or "")[:16].replace("T", " ")
        if my_addr and my_addr in _to_list(m, "toRecipients"):
            role = "[to you]"
        elif my_addr and my_addr in _to_list(m, "ccRecipients"):
            role = "[cc]"
        else:
            role = ""
        st = thread_state(token, m.get("conversationId"), my_addr)
        parts = [role, st["tag"]]
        if st.get("draft_pending"):
            parts.append("[draft pending]")
        tail = "  ".join(x for x in parts if x)
        tail = ("  " + tail) if tail else ""
        print(f"{flag}{when}  {frm[:32]:32s}  {m.get('subject', '')[:50]}{tail}")


def show_awaiting(token: str, days_back: int) -> None:
    """JSON candidates: every recent conversation that still waits on Rachad."""
    my_addr = my_address(token)
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = ("/me/mailFolders/inbox/messages?$top=150"
            "&$filter=" + urllib.parse.quote(f"receivedDateTime ge {cutoff}") +
            "&$select=subject,from,toRecipients,ccRecipients,receivedDateTime,"
            "bodyPreview,conversationId"
            "&$orderby=receivedDateTime%20desc")
    seen: set[str] = set()
    candidates = []
    for m in get(token, path).get("value", []):
        cid = m.get("conversationId")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        sender = _addr(m, "from")
        if not sender or _is_automated(sender):
            continue
        st = thread_state(token, cid, my_addr)
        if st["tag"] not in ("[awaits YOU]", "[fwd internally-waiting]") \
                and not st.get("draft_pending"):
            continue
        if my_addr and my_addr in _to_list(m, "toRecipients"):
            role = "to you"
        elif my_addr and my_addr in _to_list(m, "ccRecipients"):
            role = "cc"
        else:
            role = ""
        candidates.append({
            "conversation_id": cid,
            "subject": m.get("subject") or "",
            "tag": st["tag"],
            "draft_pending": bool(st.get("draft_pending")),
            "role": role,
            "last_from": st["last_from"],
            "last_when": st["last_when"],
            "waiting_since": _waiting_since(_conversation(token, cid), my_addr),
            "preview": (m.get("bodyPreview") or "").strip()[:300],
        })
    candidates.sort(key=lambda c: c["waiting_since"] or "9999")
    print(json.dumps({
        "you": my_addr,
        "days_back": days_back,
        "note": "oldest-waiting first; read --thread before alerting on any of these",
        "candidates": candidates,
    }, indent=2, ensure_ascii=False))


def show_sent(token: str, n: int) -> None:
    sent = get(token, f"/me/mailFolders/sentitems/messages?$top={n}"
                      "&$select=subject,toRecipients,sentDateTime,conversationId"
                      "&$orderby=sentDateTime%20desc")
    print("SENT ITEMS (most recent first):")
    for m in sent.get("value", []):
        to = ", ".join(_to_list(m, "toRecipients"))
        when = (m.get("sentDateTime") or "")[:16].replace("T", " ")
        print(f"  {when}  to {to[:40]:40s}  {m.get('subject', '')[:52]}")


def _body_text(m: dict) -> str:
    """Readable body that preserves paragraph boundaries (Aze's extraction)."""
    body = m.get("body") or {}
    text = body.get("content") or m.get("bodyPreview") or ""
    if (body.get("contentType") or "").lower() == "html":
        text = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</(?:p|div|li|tr|h[1-6])\s*>", "\n", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html_lib.unescape(text)
    text = text.replace("\r", "\n").replace("\xa0", " ")
    lines = [re.sub(r"[\t ]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def show_thread(token: str, conversation_id: str) -> None:
    my_addr = my_address(token)
    safe = conversation_id.replace("'", "''")
    flt = urllib.parse.quote(f"conversationId eq '{safe}'")
    data = get(token, "/me/messages?$filter=" + flt +
                      "&$select=id,subject,from,toRecipients,ccRecipients,"
                      "sentDateTime,receivedDateTime,isDraft,body,bodyPreview,"
                      "hasAttachments")
    msgs = data.get("value", [])
    msgs.sort(key=_when)
    print(f"THREAD ({len(msgs)} messages, oldest first):")
    for m in msgs:
        who = _addr(m, "from")
        if m.get("isDraft"):
            arrow = "DRA"
        elif who == my_addr:
            arrow = "OUT"
        elif _is_automated(who):
            arrow = "aut"
        else:
            arrow = "in "
        when = _when(m)[:16].replace("T", " ")
        print(f"  {arrow} {when}  {who[:32]:32s}  {m.get('subject', '')[:48]}")
        print(f"      MESSAGE ID: {m.get('id', '')}")
        print(f"      ATTACHMENTS: {'yes' if m.get('hasAttachments') else 'no'}")
        body = _body_text(m)
        print("      BODY:")
        if not body:
            print("      (empty)")
        else:
            shown = body[:12000]
            print("\n".join("      " + line for line in shown.splitlines()))
            if len(body) > len(shown):
                print(f"      [BODY TRUNCATED: {len(body) - len(shown)} characters remain]")
    if msgs:
        st = thread_state(token, conversation_id, my_addr)
        extra = "  [draft pending]" if st.get("draft_pending") else ""
        print(f"VERDICT: {st['tag'] or '(unclear)'}{extra}  (last real sender: {st['last_from']})")


def show_calendar(token: str) -> None:
    today = datetime.date.today()
    start = today.isoformat() + "T00:00:00"
    end = (today + datetime.timedelta(days=2)).isoformat() + "T00:00:00"
    cal = get(token, "/me/calendarView?startDateTime=" + start +
                     "&endDateTime=" + end +
                     "&$select=subject,start,end,location,organizer"
                     "&$orderby=start/dateTime")
    print("\nCALENDAR (today + tomorrow):")
    events = cal.get("value", [])
    if not events:
        print("  (no events)")
    for e in events:
        s = (e["start"]["dateTime"])[:16].replace("T", " ")
        loc = (e.get("location") or {}).get("displayName") or ""
        print(f"  {s}  {e.get('subject', '')[:60]}" + (f"  @ {loc}" if loc else ""))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = sys.argv[1:]
    try:
        token, _scopes = ot.refresh_access_token()
        if args and args[0] == "--sent":
            show_sent(token, int(args[1]) if len(args) > 1 else 15)
            return 0
        if args and args[0] == "--thread":
            if len(args) < 2:
                print("usage: outlook_check.py --thread <conversationId>")
                return 2
            show_thread(token, args[1])
            return 0
        if args and args[0] == "--awaiting":
            show_awaiting(token, int(args[1]) if len(args) > 1 else 14)
            return 0
        n = int(args[0]) if args and args[0].isdigit() else 10
        show_inbox(token, n)
        show_calendar(token)
        return 0
    except ot.OutlookError as exc:
        print(f"OUTLOOK CHECK FAILED: {exc}")
        return 1
    except urllib.error.HTTPError as exc:
        print(f"OUTLOOK CHECK FAILED: Microsoft Graph HTTP {exc.code} on {exc.url}")
        return 1
    except urllib.error.URLError as exc:
        print(f"OUTLOOK CHECK FAILED: Microsoft Graph unreachable ({exc.reason})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
