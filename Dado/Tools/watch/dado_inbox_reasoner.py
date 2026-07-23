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

PROMPT = r"""
You are running Dado's scheduled FRP Depot inbox/Sent/calendar sweep.

Follow your SOUL exactly. Reason from the live sources, not from a fixed
checklist. Your check tool is READ-ONLY and safe to run:
  C:\Users\TDI-service\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe C:\FRPDepot\Dado\Tools\outlook\outlook_check.py --awaiting
      JSON list of conversations that still wait on Rachad - START HERE.
  C:\Users\TDI-service\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe C:\FRPDepot\Dado\Tools\outlook\outlook_check.py 15
      tagged inbox view + calendar (today + tomorrow).
  C:\Users\TDI-service\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe C:\FRPDepot\Dado\Tools\outlook\outlook_check.py --thread <conversationId>
      full one-conversation dump with bodies - REQUIRED before any alert.
  C:\Users\TDI-service\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe C:\FRPDepot\Dado\Tools\outlook\outlook_check.py --sent 15
      Rachad's own recent replies and promises.
Tags: [YOU replied last] = handled, never surface. [awaits YOU] = outside party
spoke last, candidate. [fwd internally-waiting] = his last mail went only to
internal addresses; the outside party still waits. [draft pending] = a prepared
reply draft exists that Rachad has NOT sent. [to you] vs [cc]: only [to you]
threads use the normal 1-2-business-day rule; [cc]-only threads stay silent.
Titles, tags and previews are triage only: before ANY alert, read the full
conversation (--thread) and, when ownership is unclear, --sent. If Rachad
already replied or the ask is resolved, clear it silently.

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
NOISE_LINE = re.compile(
    r"(ad-?hoc verification|suite green|verifier|hermes-verify|tmp_inbox_check|"
    r"cleanup (confirmed|done)|cleaned[_ ]?up|silent[_ ]?eligible|"
    r"\bjob[_ ]?id\b|cronjob response|tempfile under|scratch file)",
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
    r"|(?:tool\s+)?[a-z]+ing\s*\.{2,}"                            # a "<verb>ing..." ticker
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


def run_dado():
    env = os.environ.copy()
    env["HERMES_ACCEPT_HOOKS"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [hermes_exe(), "-p", PROFILE, "-z", PROMPT],
        cwd=WORKDIR,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=2700,
    )
    if proc.returncode != 0:
        log(f"dado failed rc={proc.returncode} stderr={(proc.stderr or '')[:500]!r}")
        return ""
    return (proc.stdout or "").strip()


def _try_send(message):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [hermes_exe(), "-p", PROFILE, "send", "-q", "-t", TARGET, message],
        cwd=WORKDIR,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=60,
    )
    return proc.returncode, (proc.stderr or proc.stdout or "").strip()


def send_clean(message):
    """Send, retrying transient failures; queue for the next run if it never
    lands so an alert is never silently lost (Aze's dropped-RFQ lesson)."""
    last = ""
    for attempt in range(1, 4):
        rc, err = _try_send(message)
        if rc == 0:
            log(f"sent business message (attempt {attempt})")
            return True
        last = err
        log(f"send attempt {attempt}/3 failed rc={rc} err={err[:200]!r}")
        if attempt < 3:
            time.sleep(5 * attempt)
    queue_undelivered(message)
    log(f"send failed after 3 attempts; queued for next sweep. last_err={last[:200]!r}")
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
    msg = run_dado()
    if is_silent(msg):
        log("silent")
        return 0
    clean = scrub_noise(msg)
    if is_silent(clean) or len(clean) < 8:
        log("suppressed tooling noise (no business content after scrub)")
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
