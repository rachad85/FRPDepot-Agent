"""Dado's FRP Depot inbox + calendar check. READ-ONLY - only HTTP GETs exist here.

Run:  python outlook_check.py [n_messages]   inbox + calendar, each thread tagged
                                             with WHO SPOKE LAST + whether Rachad
                                             is a direct recipient
      python outlook_check.py --awaiting [days_back]
                                             JSON list of conversations that still
                                             wait on Rachad (the sweep's candidates)
      python outlook_check.py --waiting-on-them [days_back]
                                             JSON list of threads where RACHAD
                                             spoke last and nobody replied -
                                             the follow-up tracker's input
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
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import outlook_tool as ot  # noqa: E402  (auth + shared helpers, read-only use)

GRAPH = "https://graph.microsoft.com/v1.0"
INTERNAL_DOMAIN = "frpdepots.com"
# Rachad's working days are Eastern. Every wait-clock date is taken in this zone,
# never UTC - see business_days_since.
BUSINESS_TZ = ZoneInfo("America/Toronto")
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


# How long silence is allowed before a thread is worth raising, by what the
# thread IS. Rachad's choice 2026-07-24: tiered, because a quote legitimately
# takes a week while an unanswered question does not.
FOLLOWUP_BUSINESS_DAYS = {"rfq_quote": 5, "payment": 7, "general": 3}

# Money and new-work threads reach him the same sweep they go overdue; the rest
# wait for the morning digest. Winning work and getting paid outrank quiet rules.
URGENT_CATEGORIES = {"rfq_quote", "payment"}
# How long a chase WE created keeps a thread off the list. Matches the promise
# already in the digest prompt: "An item chased in the last 7 days is not chased
# again." Calendar days, not working days - it is a quiet period, not a deadline.
CHASE_QUIET_DAYS = 7


def recent_chases() -> dict[str, str]:
    """conversation_id -> ISO timestamp of the most recent chase WE created.

    Only reply-all drafts this tree wrote are counted, and only for
    CHASE_QUIET_DAYS. The old test was `any draft exists in the conversation`,
    which is a different question with a much wider answer: it also matched an
    ordinary reply draft, one of Rachad's own half-typed messages, and a chase
    he read and rejected (a deleted draft still comes back from /me/messages,
    which spans every folder). One such draft removed the thread from `overdue`
    and from overdue_count PERMANENTLY, and the digest prompt is told to ignore
    already_chased - so a live money thread could go quiet forever. Measured
    2026-07-24: a CAD 9,936 budgetary quote to Nashtec, one working day old and
    never chased, was already excluded on this basis.
    """
    log_path = ot.CHASE_LOG
    if not log_path.exists():
        return {}
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=CHASE_QUIET_DAYS)
    latest: dict[str, str] = {}
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            when = datetime.datetime.fromisoformat(str(row["ts"]))
        except (ValueError, KeyError, TypeError):
            continue  # a corrupt line must not blind the tracker
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        if when < cutoff:
            continue
        cid = str(row.get("conversation_id") or "")
        if cid and (cid not in latest or str(row["ts"]) > latest[cid]):
            latest[cid] = str(row["ts"])
    return latest

_RFQ_WORDS = re.compile(
    r"\b(rfq|quot(?:e|ation)|estimate|pricing|proposal|tender|bid|budgetary)\b", re.I)
_PAY_WORDS = re.compile(
    r"\b(invoice|inv-\d|payment|outstanding|overdue|remittance|balance due|past due|"
    r"deposit|wire|e-?transfer)\b", re.I)


# A quote/RFQ number in the SUBJECT is the strongest signal there is: it says
# what the thread is, regardless of which words appear further down.
_QUOTE_NUMBER = re.compile(r"\b(?:qt|rfq|quote)[-_ ]?\d", re.I)
# Where Outlook's quoted history starts inside a bodyPreview. Everything from
# here on was written by somebody else on an earlier message.
_QUOTED_HISTORY = re.compile(
    r"(-{2,}\s*original message|_{5,}|from:\s*\S+@|on .{0,40}\bwrote:|sent:\s*\w)", re.I)


def strip_quoted(preview: str) -> str:
    """The part of a preview Rachad actually wrote on THIS message.

    bodyPreview on a reply or forward leads with quoted history, so classifying
    the whole string let words from someone else's earlier mail set the
    category - and therefore the threshold and the urgency.
    """
    match = _QUOTED_HISTORY.search(preview or "")
    return (preview or "")[:match.start()] if match else (preview or "")


def classify_thread(subject: str, preview: str = "") -> str:
    """rfq_quote / payment / general - a hint, not a verdict.

    Deterministic so the wait-clock is reproducible; Dado still reads the whole
    thread before she says anything, and may overrule this.

    Two corrections over the first version (2026-07-25). Both were measured:
    (1) a quote-numbered subject wins over payment words, so
        classify_thread('Quote QT-000099 for FRP pipe', 'Deposit invoice
        attached') is rfq_quote and waits 5 working days, not 7; and
    (2) only Rachad's own added text is read, not the quoted history below it.
    """
    own_text = strip_quoted(preview)
    if _QUOTE_NUMBER.search(subject or ""):
        return "rfq_quote"
    haystack = f"{subject or ''} {own_text}"
    if _PAY_WORDS.search(haystack):
        return "payment"
    if _RFQ_WORDS.search(haystack):
        return "rfq_quote"
    return "general"


def business_days_since(iso_timestamp: str) -> int:
    """Working days elapsed in RACHAD'S timezone, Monday-Friday.

    Both ends are converted to America/Toronto before the date is taken. They
    used to be UTC dates while the thresholds were stated in his working days,
    which moved the clock by a whole day at each end. Measured against the old
    version: business_days_since('2026-07-20T19:30:00Z') was 4 and
    ('2026-07-21T01:30:00Z') was 3 - two mails sent the same Monday, 15:30 and
    21:30 Eastern, a full working day apart. Anything sent after ~20:00 ET went
    overdue a day late, and the 19:00 sweep (00:00 UTC next day under EST) added
    a phantom day to every thread, firing not-yet-due items early.
    """
    try:
        start = datetime.datetime.fromisoformat((iso_timestamp or "").replace("Z", "+00:00"))
    except ValueError:
        return 0
    if start.tzinfo is None:
        start = start.replace(tzinfo=datetime.timezone.utc)
    today = datetime.datetime.now(BUSINESS_TZ).date()
    day = start.astimezone(BUSINESS_TZ).date()
    count = 0
    while day < today:
        day += datetime.timedelta(days=1)
        if day.weekday() < 5:
            count += 1
    return count


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


FOLLOWUP_WATCH = Path(r"C:\FRPDepot\Dado\30_Memory\followup_watch.json")
SENT_PAGE_LIMIT = 20  # 20 x 250 = 5,000 sent messages; a backstop, not a budget


def _all_sent_since(token: str, cutoff: str) -> tuple[list[dict], bool]:
    """Every sent message in the window, following @odata.nextLink.

    The seed used to be a single $top=250 page with no paging. Ordering is
    sentDateTime desc, so overflow discarded the OLDEST mail - precisely the
    most overdue threads - and the output said nothing about it. Headroom was
    thin rather than comfortable: 99 sent messages in 60 days, and days_back is
    a free-form argument.
    """
    url = ("/me/mailFolders/sentitems/messages?$top=250"
           "&$select=subject,toRecipients,ccRecipients,sentDateTime,"
           "conversationId,bodyPreview"
           "&$filter=" + urllib.parse.quote(f"sentDateTime ge {cutoff}") +
           "&$orderby=sentDateTime%20desc")
    messages: list[dict] = []
    for _ in range(SENT_PAGE_LIMIT):
        data = get(token, url)
        messages.extend(data.get("value", []))
        nxt = data.get("@odata.nextLink") or ""
        if not nxt:
            return messages, False
        url = nxt.split("/v1.0", 1)[-1] if "/v1.0" in nxt else nxt
    return messages, True  # hit the page cap - say so rather than pretend


def load_followup_watch() -> dict[str, dict]:
    """Conversations the tracker has seen before, so ageing cannot hide them.

    Candidates are seeded from Sent Items within days_back, and the clock keeps
    running - so the longer a thread was ignored the closer it came to leaving
    the window entirely, with nothing distinguishing "resolved" from "aged out
    unchased". QT-000023 (last sent 2026-05-27, 42 working days silent) was due
    to vanish from every future digest on 2026-07-27.
    """
    if not FOLLOWUP_WATCH.exists():
        return {}
    try:
        data = json.loads(FOLLOWUP_WATCH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_followup_watch(candidates: list[dict]) -> None:
    """Remember every still-unanswered thread. Answered ones simply drop out."""
    try:
        FOLLOWUP_WATCH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            c["conversation_id"]: {
                "subject": c.get("subject", "")[:120],
                "last_sent": c.get("last_sent", ""),
                "first_tracked": (load_followup_watch().get(c["conversation_id"]) or {})
                                 .get("first_tracked") or c.get("last_sent", ""),
            }
            for c in candidates
        }
        FOLLOWUP_WATCH.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                                  encoding="utf-8")
    except OSError as exc:
        print(json.dumps({"warning": "follow-up watch not saved",
                          "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)


def show_waiting_on_them(token: str, days_back: int) -> None:
    """The reverse of --awaiting: threads where RACHAD spoke last and nobody replied.

    His sweep was one-directional until 2026-07-24 — [YOU replied last] meant
    "handled, never surface", so a quote or an RFQ he sent could go silent
    forever and nothing would notice. Measured when this was written: 20 such
    threads, 11 of them a week or older, including an RFQ silent for 28 days and
    CAD 4,101.30 outstanding.
    """
    my_addr = my_address(token)
    if not my_addr:
        # my_address() swallows both its lookups and returns "". The ownership
        # test below is `!= my_addr`, so an empty value skipped EVERY candidate:
        # valid JSON, overdue_count 0, digest silent - a permanent all-clear
        # produced by a broken /me call. Fail loudly instead.
        print(json.dumps({
            "error": "could not resolve the FRP Depot mailbox address (/me and the "
                     "Sent Items fallback both failed) - refusing to report an "
                     "all-clear that would only mean the lookup broke.",
        }, indent=2))
        sys.exit(2)
    chased = recent_chases()
    watch = load_followup_watch()
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sent_messages, truncated = _all_sent_since(token, cutoff)
    seen: set[str] = set()
    candidates = []
    # Threads carried forward from an earlier run are appended so a conversation
    # never falls out of the tracker just by ageing past the window (B-12).
    carried = [cid for cid in watch if cid not in {m.get("conversationId")
                                                   for m in sent_messages}]
    work = [(m, False) for m in sent_messages] + [({"conversationId": c}, True) for c in carried]
    for m, is_carried in work:
        cid = m.get("conversationId")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        msgs = _conversation(token, cid)
        human = [x for x in msgs if x.get("isDraft") is not True
                 and not _is_automated(_addr(x, "from"))]
        if not human:
            continue
        last = human[-1]
        # Someone else spoke last -> that is the --awaiting case, already covered.
        if _addr(last, "from") != my_addr:
            continue
        external = [r for r in _recipients(last) if r and not _is_internal(r)]
        if not external:
            continue
        # A thread STARTED by an automated sender is a bank/portal notice Rachad
        # forwarded, not a conversation anyone will reply to. _is_automated only
        # inspects the sender, and on a forward the sender is Rachad - so these
        # were landing in the overdue list as urgent payment items with a chase
        # draft prepared against a no-reply notification.
        root_sender = _addr(msgs[0], "from") if msgs else ""
        if root_sender and _is_automated(root_sender):
            continue
        # Informational only. This count must NEVER decide whether the thread is
        # tracked - that was the bug. Whether WE chased it is `chased_on`.
        drafts = [x for x in msgs if x.get("isDraft") is True]
        subject = m.get("subject") or last.get("subject") or ""
        preview = (m.get("bodyPreview") or last.get("bodyPreview") or "").strip()
        category = classify_thread(subject, preview)
        waited = business_days_since(_when(last))
        due_after = FOLLOWUP_BUSINESS_DAYS[category]
        candidates.append({
            "carried_forward": is_carried,
            "conversation_id": cid,
            "subject": subject,
            "to": external[0],
            "all_external": external,
            "last_sent": _when(last)[:16].replace("T", " "),
            "business_days_silent": waited,
            "category": category,
            "due_after_business_days": due_after,
            "overdue": waited >= due_after,
            "urgent": category in URGENT_CATEGORIES,
            "chase_draft_pending": cid in chased,
            "chase_drafted_on": (chased.get(cid) or "")[:10] or None,
            "drafts_in_thread": len(drafts),
            "messages_in_thread": len(human),
            "preview": preview[:300],
        })
    candidates.sort(key=lambda c: -c["business_days_silent"])
    overdue = [c for c in candidates if c["overdue"] and not c["chase_draft_pending"]]
    save_followup_watch(candidates)
    print(json.dumps({
        "you": my_addr,
        "days_back": days_back,
        "sent_window_truncated": truncated,
        "carried_forward_count": sum(1 for c in candidates if c["carried_forward"]),
        "thresholds_business_days": FOLLOWUP_BUSINESS_DAYS,
        "note": ("Threads where YOU spoke last to an outside party. 'overdue' applies the "
                 "per-category threshold. Read the full thread with --thread before "
                 "chasing: the answer may have arrived out of band. "
                 f"'chase_draft_waiting' means WE DRAFTED a chase within {CHASE_QUIET_DAYS} "
                 "days (chase_drafted_on) and it is sitting UNSENT - Rachad has not "
                 "pressed Send, so do not tell him the party was chased. It is not "
                 "inferred from drafts in the thread, so an unrelated draft no longer "
                 "hides a live thread. Threads marked "
                 "carried_forward are older than days_back and are kept on the list "
                 "until answered, so ageing cannot retire them unchased. If "
                 "sent_window_truncated is true the Sent scan hit its page cap and "
                 "the oldest mail in the window was not read."),
        "chase_quiet_days": CHASE_QUIET_DAYS,
        "overdue_count": len(overdue),
        "overdue": overdue,
        "not_yet_due": [c for c in candidates if not c["overdue"]],
        "chase_draft_waiting": [c for c in candidates if c["chase_draft_pending"]],
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
        if args and args[0] == "--waiting-on-them":
            show_waiting_on_them(token, int(args[1]) if len(args) > 1 else 60)
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
