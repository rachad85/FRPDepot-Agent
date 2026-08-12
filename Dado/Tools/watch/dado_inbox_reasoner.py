"""Scheduled Dado inbox/Sent/calendar sweep for FRP Depot.

Runs Dado's LLM brain for the mailbox sweep, but does not let the Hermes cron
wrapper deliver raw agent output to Telegram. If Dado returns a real business
message, this script sends only that message. If Dado returns [SILENT] or
tooling noise, it sends nothing.

Adapted 2026-07-23 from Aze's aze_hourly_reasoner.py (sanctioned pattern reuse;
no TDI data). Differences: FRP Depot paths/profile, no multi-PC lease, no
active-task deferral layer (Dado does not have those systems), 2-hour cadence.
Source of truth: C:\\FRPDepot\\Dado\\Tools\\watch\\dado_inbox_reasoner.py — the
profile scripts copy is what the cron runs; keep them identical.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

TARGET = "telegram:891365639"
PROFILE = "dado"
WORKDIR = r"C:\FRPDepot\Dado"
LOG = Path(r"C:\FRPDepot\Dado\40_Logs\dado_inbox_reasoner.log")
RUN_LOCK = Path(r"C:\FRPDepot\Dado\40_Logs\dado_inbox_reasoner.lock")
RUN_LOCK_STALE_SECONDS = 3600
UNDELIVERED = Path(r"C:\FRPDepot\Dado\40_Logs\undelivered_alerts.txt")
MAX_QUEUE_AGE_HOURS = 24
VENV_PY = r"C:\Users\TDI-service\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
CHECK_PY = r"C:\FRPDepot\Dado\Tools\outlook\outlook_check.py"
CLOSED_TASKS = Path(r"C:\FRPDepot\Dado\30_Memory\closed_task_threads.jsonl")
SWEEP_DIR = Path(r"C:\FRPDepot\Dado\20_Working\inbox_watch")

PROMPT = r"""
You are running Dado's scheduled FRP Depot inbox/Sent/calendar sweep.

Follow your SOUL exactly. Reason from the live sources, not from a fixed
checklist. FRESH TRIAGE DATA IS ALREADY ON DISK - this run collected it from
the live mailbox minutes ago. Read these with your FILE tools (not terminal):
  C:\FRPDepot\Dado\20_Working\inbox_watch\awaiting.json
      conversations that still wait on Rachad, oldest first - START HERE.
  C:\FRPDepot\Dado\20_Working\inbox_watch\inbox_calendar.txt
      tagged inbox view + calendar (today + tomorrow).
  C:\FRPDepot\Dado\20_Working\inbox_watch\sent.txt
      Rachad's own recent replies and promises.
  C:\FRPDepot\Dado\20_Working\inbox_watch\waiting_on_them.json
      threads where RACHAD wrote last and nobody replied. See rule 3b - in THIS
      sweep you only ever raise the money/new-work ones; the rest are the
      morning digest's job, not yours.
Tags: [YOU replied last] = handled, never surface. [awaits YOU] = outside party
spoke last, candidate. [fwd internally-waiting] = his last mail went only to
internal addresses; the outside party still waits. [draft pending] = a prepared
reply draft exists that Rachad has NOT sent. [to you] vs [cc]: only [to you]
threads use the normal 1-2-business-day rule; [cc]-only threads stay silent.
Titles, tags and previews are triage only: before ANY alert you MUST read the
full conversation. Run in the terminal, exactly as written here including the
forward slashes (they survive every shell) and the quotes around the id:
  C:/Users/TDI-service/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe C:/FRPDepot/Dado/Tools/outlook/outlook_check.py --thread "<conversationId>"
If Rachad already replied or the ask is resolved, clear it silently. If the
deep read fails, do not guess from the preview - skip that item and say
nothing about it (uncertain = silent).

THE ALERT LEDGER IS BINDING: before composing any alert, read
C:\FRPDepot\Dado\30_Memory\alert_ledger.md. CLOSED item = silent permanently
unless genuinely NEW inbound mail arrived after the closing date. ALERTED item
= already on his phone; no repeat without new inbound mail or a deadline now
within 24h. AFTER deciding to alert, append one dated line per item to the
ALERTED section in the same run, and record a receipt (SOUL rule).

What deserves a message:
1. A new inbound RFQ / pricing / stock request not already being worked (check
   Dado\20_Working\ and your memory first; apply the fit-profile live-sweep
   rule - verify the latest non-draft message in the conversation is inbound
   before declaring it open). Summarize who/what, recommend a next step, ask
   for green light. Never start client-facing work without it.
2. An external thread [awaits YOU] where Rachad is a DIRECT recipient and has
   not replied for a reasoned 1-2 business days.
3. MONEY LANDING: any movement toward an order, PO, payment, or contract on
   FRP Depot business alerts the SAME sweep it appears, even if he already
   read it: name the thread, what the client is doing, and the one next step.
   Winning work outranks every quiet rule.
3b. HE IS WAITING ON THEM, and it is money or new work. From
   waiting_on_them.json, raise an entry ONLY when overdue=true AND urgent=true
   (category payment or rfq_quote) AND chase_draft_pending=false. Read the full
   thread first - the answer may have come by phone or portal. Alert once, name
   the customer, the amount or quote number, and how many working days it has
   been silent. Everything with urgent=false belongs to the morning digest;
   never raise it here. Winning work and getting paid outrank quiet rules.
4. [draft pending] reminder: a reply draft prepared for him that he has not
   sent for over 1 business day while the outside party still waits - once.
5. An admin/service/deadline notice (renewals, account changes, expiries):
   alert ONCE; re-alert only within 24h of the stated deadline if still
   unhandled.
6. A real calendar conflict or missing prep for a client meeting.
If the sweep itself fails before mail was actually reasoned over, return a
clean business alert that inbox monitoring failed and needs attention; failed
checks are not silence.

What does NOT deserve a message:
- Mail he already answered, and threads where the action sits with someone
  else ([handled internally], [cc]-only FYI mail).
- Newsletters, marketing, payment-processor confirmations and portal
  notifications that need no decision from him.
- Meeting started / join-now reminders.
- Same-day routine asks: watch them; surface only after 1-2 business days.
- Any summary or list of mail he can read himself.
- Anything you could not fully read or verify - when uncertain, stay silent.
- Internal verification, cleanup, or "silent was correct" reports.

OUT-OF-BAND COMPLETION: signatures, portal approvals, bank steps and phone
calls happen outside email; the trail cannot confirm them. NEVER assert Rachad
has not acted - phrase it as a verification question ("Did you already ...?").
If he says it is done, append it to CLOSED in the ledger.

Where a candidate touches price, stock, or payment status and the commissioned
Zoho read tools are connected, verify there before alerting; if they are not
connected, say what you could not verify - never invent a figure (SOUL).

Delivery contract:
- Do NOT send or deliver messages yourself. This wrapper handles delivery.
- DRAFTS ONLY stands (HARD RULE 1): never send email; prepare drafts only
  after Rachad's green light.
- Return exactly [SILENT] if nothing genuinely needs Rachad.
- Otherwise return ONLY the final Telegram text Rachad should see. The FIRST
  character of your output is the first character he reads. No status tags, no
  spinners, no tool names, no cron/job/file-path narration, and never the word
  [SILENT] inside a real message.
- If you appended an alert to the ledger this sweep, THAT alert is what you
  return. Never replace it with a report on your own verification, cleanup or
  scripts: the scrub deletes those and the alert is then lost silently, with
  the ledger claiming it was raised (2026-07-31 13:07, Q26-1549 PO cutoff).

Style: Rachad's AI copy. Terse, worst news first, one recommendation attached.
No markdown unless it materially improves a short operational note.
"""


def hermes_exe():
    return (
        shutil.which("hermes")
        or r"C:\Users\TDI-service\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe"
    )


def log(line):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {line}\n")
    except Exception:
        pass


def acquire_run_lock():
    RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    if RUN_LOCK.exists():
        try:
            age = dt.datetime.now().timestamp() - RUN_LOCK.stat().st_mtime
        except OSError:
            age = 0
        if age < RUN_LOCK_STALE_SECONDS:
            return False
        try:
            RUN_LOCK.unlink()
        except OSError:
            return False
    try:
        fd = os.open(str(RUN_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"pid={os.getpid()} started={dt.datetime.now().astimezone().isoformat()}\n".encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def release_run_lock():
    try:
        RUN_LOCK.unlink()
    except FileNotFoundError:
        pass


def is_silent(text):
    t = (text or "").strip()
    if not t:
        return True
    return t.upper() in {"[SILENT]", "SILENT", "NO_REPLY"}


# Lines that are pure tool-machinery narration. SOUL already forbids Dado from
# producing these; this is only a backstop. We STRIP matching lines rather than
# discard the whole message, so a real alert that merely mentions one of these
# tokens (e.g. a file path) is never silently lost. (Aze's battle-tested set.)
#
# ANCHORED to the start of the line (2026-07-25). These patterns used to match
# ANYWHERE, and the match discarded the WHOLE line - so "Their job ID 88-A needs
# the FRP grating quote priced by Friday" vanished entirely, and if it was the
# only substantive line the message then fell under the len(clean) < 8 guard and
# the alert was suppressed with nothing but a log entry. `job id` and `cleaned
# up` were dropped from the set outright: both are ordinary operations English
# and far too common to be safe as machinery markers.
NOISE_LINE = re.compile(
    r"^\s*(?:[-*•]\s*)?"
    r"(ad-?hoc verification|suite green|verifier|hermes-verify|tmp_inbox_check|"
    r"cleanup (confirmed|done)|silent[_ ]?eligible|"
    r"cronjob response|tempfile under|scratch file)",
    re.I,
)

# Machine-frame tokens the model or CLI sometimes PREPEND to a line: a bracketed
# status tag ([tool], [thinking], [SILENT]...), a kaomoji / box / braille spinner,
# or a bare "<verb>ing..." ticker. We PEEL these off the FRONT of a line and keep
# whatever real content follows — we never drop the whole line, because a frame
# token sharing a line with a real alert must not delete the alert. The bracket
# branch is an ALLOWLIST so business tags like [URGENT] are preserved; the ticker
# requires the "-ing" word to be immediately followed by the dots, so real
# content like "Waiting on Forte..." is kept while "processing..." is peeled.
_FRAME_WORDS = (r"silent|no[_ ]?reply|tool|tools|thinking|reasoning|status|working|"
                r"processing|debug|trace|spinner|thought|plan|analysis|analyzing|"
                r"assistant|agent|system|info|gathering|loading|running|done")
FRAME_TOKEN = re.compile(
    r"^\s*(?:"
    r"\[(?:" + _FRAME_WORDS + r")\]"                              # allowlisted [status] tag
    r"|\(\s*[^A-Za-z0-9\s)]{1,4}[_ ]?[^A-Za-z0-9\s)]{0,4}\s*\)"   # kaomoji face (non-letters only)
    r"|¯\\?_?\(ツ\)_?/?¯"                                         # shrug
    r"|[⠀-⣿◀-◿●○·]+"                          # braille / box / dot spinner glyphs
    # A ticker, but ONLY from the allowlist. This used to be any [a-z]+ing
    # followed by dots, which peeled the first real word off a business line:
    # "Pending... payment from SCT Composites" became "payment from SCT
    # Composites", inverting the meaning of the first word Rachad reads.
    r"|(?:tool\s+)?(?:" + _FRAME_WORDS + r")\s*\.{2,}"
    r")\s*[:.…]*\s*",
    re.I,
)


def _peel_frames(line):
    """Strip a run of leading machine-frame tokens; keep the remaining content."""
    prev = None
    cur = line
    while cur != prev:
        prev = cur
        cur = FRAME_TOKEN.sub("", cur, count=1)
    return cur


def scrub_noise(text):
    """Peel machine-frame prefixes and drop pure-machinery lines; keep business
    content. A line that still has real content after peeling is ALWAYS kept."""
    out = []
    for ln in (text or "").splitlines():
        if NOISE_LINE.search(ln):
            continue                      # whole-line tooling report -> drop
        peeled = _peel_frames(ln)
        if peeled.strip() or not ln.strip():
            out.append(peeled)            # keep content, or preserve a blank line
        # a line that HAD content but peeled to empty was pure frames -> dropped
    body = "\n".join(out)
    body = re.sub(r"(?im)^\s*(verified:|checked:|confirmed:)\s*$", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body)   # collapse blank runs left by drops
    return body.strip()


def collect(name, args_list, timeout=300):
    """Run outlook_check.py directly (argv list, no shell - immune to path
    mangling) and return its stdout. Raises on failure."""
    proc = subprocess.run(
        [VENV_PY, CHECK_PY] + args_list,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        detail = (proc.stderr or out or "no output").strip()[:300]
        raise RuntimeError(f"{name} collection failed rc={proc.returncode}: {detail}")
    return out


def suppress_closed_waiting_on_them(raw: str) -> str:
    """Hide Rachad-closed draft tasks while allowing later activity to reopen them."""
    data = json.loads(raw)
    closed: dict[str, dict] = {}
    if CLOSED_TASKS.exists():
        for line in CLOSED_TASKS.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            conversation_id = str(row.get("conversation_id") or "").strip()
            if conversation_id:
                closed[conversation_id] = row
    suppressed_ids: set[str] = set()
    for key in ("overdue", "not_yet_due", "chase_draft_waiting"):
        kept = []
        for item in data.get(key) or []:
            conversation_id = str(item.get("conversation_id") or "")
            record = closed.get(conversation_id)
            last_sent = str(item.get("last_sent") or "")
            last_sent_at_close = str((record or {}).get("last_sent_at_close") or "")
            if record and last_sent_at_close and last_sent <= last_sent_at_close:
                suppressed_ids.add(conversation_id)
                continue
            kept.append(item)
        data[key] = kept
    data["overdue_count"] = len(data.get("overdue") or [])
    data["closed_task_suppressed_count"] = len(suppressed_ids)
    return json.dumps(data, indent=2, ensure_ascii=False)


def prefetch_triage():
    """Deterministically pull the sweep's source data before the brain runs.
    Added 2026-07-23 after E2E runs 1-2: the brain's own terminal mangled
    script paths two different ways (bare `python` unresolved, MSYS rewrote
    C:\\ paths), and its failure message was then lost to the noise scrub.
    Collection and failure reporting must not depend on the model."""
    stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    awaiting = collect("awaiting", ["--awaiting"])
    inbox = collect("inbox", ["15"])
    sent = collect("sent", ["--sent", "15"])
    # What HE is waiting on. Only the money/new-work ones are actionable in this
    # sweep (charter rule 3b); the rest are the morning digest's.
    #
    # BEST-EFFORT, deliberately. This is the slowest call here - one Graph
    # conversation fetch per unique sent thread, each with its own 429 retries -
    # and the newest. Raising from it took down the whole sweep: the inbox,
    # awaiting and sent data already collected were thrown away and no mail was
    # reasoned over for two hours, so the newest feature became a single point
    # of failure for the pre-existing [awaits YOU] monitoring.
    try:
        waiting_on_them = collect("waiting_on_them", ["--waiting-on-them", "60"], timeout=900)
        waiting_on_them = suppress_closed_waiting_on_them(waiting_on_them)
    except Exception as exc:
        log(f"waiting-on-them collection failed, continuing without it: "
            f"{type(exc).__name__}: {exc}")
        waiting_on_them = json.dumps({
            "unavailable": True,
            "why": f"{type(exc).__name__}: {str(exc)[:200]}",
            "note": "Follow-up data could not be collected this run. Do NOT infer "
                    "that nothing is overdue - say it was unavailable if it matters.",
        }, indent=2)
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    (SWEEP_DIR / "waiting_on_them.json").write_text(waiting_on_them + "\n", encoding="utf-8")
    (SWEEP_DIR / "awaiting.json").write_text(awaiting + "\n", encoding="utf-8")
    (SWEEP_DIR / "inbox_calendar.txt").write_text(
        f"collected {stamp}\n{inbox}\n", encoding="utf-8")
    (SWEEP_DIR / "sent.txt").write_text(
        f"collected {stamp}\n{sent}\n", encoding="utf-8")
    log("triage data collected")


def run_dado():
    """Returns the model's text, or None if the run never produced one.

    None and "" mean different things and must not be conflated. run_dado used
    to return "" on failure, is_silent("") is True, so a quota exhaustion on the
    shared openai-codex plan, a gateway outage or a dead provider (there is no
    fallback, by design) logged "silent" and sent nothing - identical to a clean
    sweep. Rachad reads that quiet as "nothing needs me" when in fact no mail was
    reasoned over at all. Collection failures alerted; brain failures did not,
    which is what made the gap invisible.
    """
    env = os.environ.copy()
    env["HERMES_ACCEPT_HOOKS"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [hermes_exe(), "-p", PROFILE, "-z", PROMPT],
            cwd=WORKDIR,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=2700,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        # timeout=2700 raises TimeoutExpired, which used to escape run_once and
        # kill the process with a traceback - again with no message to Rachad.
        log(f"dado run raised: {type(exc).__name__}: {exc}")
        return None
    if proc.returncode != 0:
        log(f"dado failed rc={proc.returncode} stderr={(proc.stderr or '')[:500]!r}")
        return None
    return (proc.stdout or "").strip()


def _try_send(message):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [hermes_exe(), "-p", PROFILE, "send", "-q", "-t", TARGET, message],
            cwd=WORKDIR,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        # A hung Telegram call raises TimeoutExpired; a missing hermes binary
        # raises FileNotFoundError. Neither was caught, so the exception escaped
        # send_clean, queue_undelivered never ran, and the alert was destroyed
        # outright - in exactly the transient outage the retry policy exists for.
        # Report it as a failed attempt so the retry and the queue still happen.
        return -1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stderr or proc.stdout or "").strip()


def send_clean(message, queue_on_failure=True):
    """Send, retrying transient failures; queue for the next run if it never
    lands so an alert is never silently lost (the dropped-RFQ lesson).

    Returns True ONLY when Telegram accepted the message.

    queue_on_failure=False is for callers whose alert is RE-DERIVED from durable
    state on a short cadence - job_runner's 10-minute watch tick and
    stall_tripwire's 15-minute tick (backlog B-08). They keep the alert owed in
    their OWN state file and re-emit it themselves, so queueing here as well
    would deliver the same warning twice: once when the next inbox sweep flushes
    this queue, and once from the caller's own re-derivation.

    It also keeps them off the shared undelivered_alerts.txt, which is a
    read-all/rewrite-all over one file with no lock. With inbox-watch (2h),
    job-watch (10m) and the tripwire (15m) all touching it, job-watch and the
    tripwire would collide on the same minute every 30 minutes.
    """
    last = ""
    try:
        for attempt in range(1, 4):
            rc, err = _try_send(message)
            if rc == 0:
                log(f"sent business message (attempt {attempt})")
                return True
            last = err
            log(f"send attempt {attempt}/3 failed rc={rc} err={err[:200]!r}")
            if attempt < 3:
                time.sleep(5 * attempt)
    except Exception as exc:
        # Belt and braces. Nothing between here and queue_undelivered may throw,
        # or the alert dies with the traceback instead of waiting for next sweep.
        last = f"{type(exc).__name__}: {exc}"
        log(f"send loop raised, falling through to the queue: {last}")
    if queue_on_failure:
        queue_undelivered(message)
        log(f"send failed after 3 attempts; queued for next sweep. last_err={last[:200]!r}")
    else:
        log(f"send failed after 3 attempts; caller re-derives, not queued. "
            f"last_err={last[:200]!r}")
    return False


def queue_undelivered(message):
    try:
        UNDELIVERED.parent.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with UNDELIVERED.open("a", encoding="utf-8") as fh:
            fh.write(f"===ALERT {stamp}===\n{message}\n===END===\n")
    except Exception as exc:
        log(f"could not queue undelivered alert: {type(exc).__name__}: {exc}")


def flush_undelivered():
    """Re-send anything a prior sweep failed to deliver, so alerts survive a
    transient Telegram outage. Stale entries (older than the cap) are dropped."""
    if not UNDELIVERED.exists():
        return
    try:
        raw = UNDELIVERED.read_text(encoding="utf-8")
    except Exception:
        return
    entries = re.findall(r"===ALERT ([^\n]*)===\n(.*?)\n===END===", raw, re.S)
    if not entries:
        return
    now = dt.datetime.now().astimezone()
    remaining = []
    for stamp, body in entries:
        body = body.strip()
        if not body:
            continue
        try:
            age_h = (now - dt.datetime.fromisoformat(stamp)).total_seconds() / 3600
        except Exception:
            age_h = 0
        if age_h > MAX_QUEUE_AGE_HOURS:
            log(f"dropping stale undelivered alert ({age_h:.0f}h old)")
            continue
        rc, _ = _try_send("Earlier alert (delivery was delayed):\n\n" + body)
        if rc == 0:
            log("flushed a previously-undelivered alert")
        else:
            remaining.append((stamp, body))
    try:
        if remaining:
            with UNDELIVERED.open("w", encoding="utf-8") as fh:
                for stamp, body in remaining:
                    fh.write(f"===ALERT {stamp}===\n{body}\n===END===\n")
        else:
            UNDELIVERED.unlink()
    except Exception as exc:
        log(f"could not rewrite undelivered queue: {type(exc).__name__}: {exc}")


def run_once():
    flush_undelivered()
    try:
        prefetch_triage()
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        log(f"prefetch failed: {reason}")
        send_clean(
            "Inbox sweep failed before mail was checked - "
            f"{str(exc)[:200]}. Backend attention needed; next automatic "
            "attempt in about 2 hours."
        )
        return 0
    msg = run_dado()
    if msg is None:
        # Must be checked BEFORE is_silent - is_silent(None) is True, which is
        # precisely how this failure used to disguise itself as a quiet sweep.
        log("brain run failed; alerting rather than going quiet")
        send_clean(
            "Inbox sweep could not be reviewed - Dado's run failed or timed out, "
            "so no mail was read this cycle. This is NOT an all-clear. Backend "
            "attention needed; next automatic attempt in about 2 hours."
        )
        return 0
    if is_silent(msg):
        log("silent")
        return 0
    clean = scrub_noise(msg)
    if is_silent(clean) or len(clean) < 8:
        log(f"suppressed tooling noise (no business content after scrub); raw={msg[:200]!r}")
        return 0
    if clean != msg.strip():
        log("stripped tool-noise lines before send")
    send_clean(clean)
    return 0


def main():
    if not acquire_run_lock():
        log("prior inbox sweep still running; skipped overlapping run")
        return 0
    try:
        return run_once()
    finally:
        release_run_lock()


if __name__ == "__main__":
    sys.exit(main())
