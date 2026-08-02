"""Morning follow-up digest: the threads RACHAD is waiting on.

Until 2026-07-24 Dado's watch was one-directional. She surfaced mail waiting on
him ([awaits YOU]) and treated a thread he spoke last in as "handled, never
surface" - so a quote or an RFQ he sent could go silent forever and nothing
noticed. Measured the day this was built: 15 overdue threads, including
"Quote QT-000023 is awaiting your approval" silent for 42 business days, an RFQ
at 20 days, and CAD 4,101.30 outstanding at 9.

Rachad's choices (2026-07-24), which this implements:
  WHAT   every external thread he spoke last in, not just the obvious ones
  WHEN   tiered - RFQ/quote 5 business days, payment 7, general question 3
  ACTION tell him AND leave a reply-all follow-up DRAFT ready to send
  HOW    one morning digest; money/RFQ items also reach him the same sweep they
         go overdue, via the 2-hourly inbox watch

Delivery, the [SILENT] contract, the noise scrub and the undelivered queue are
reused from dado_inbox_reasoner so there is one battle-tested delivery path.

Cron:
    hermes -p dado cron create "0 8 * * 1-5" --name dado-followup-digest \
        --no-agent --script dado_followup_digest.py --deliver local \
        --workdir C:\\FRPDepot\\Dado
"""
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# One delivery path, one scrub, one retry policy - do not fork these.
from dado_inbox_reasoner import (  # noqa: E402
    hermes_exe, is_silent, scrub_noise, send_clean, flush_undelivered,
)

PROFILE = "dado"
WORKDIR = r"C:\FRPDepot\Dado"
LOG = Path(r"C:\FRPDepot\Dado\40_Logs\dado_followup_digest.log")
RUN_LOCK = Path(r"C:\FRPDepot\Dado\40_Logs\dado_followup_digest.lock")
RUN_LOCK_STALE_SECONDS = 3600
SWEEP_DIR = Path(r"C:\FRPDepot\Dado\20_Working\followups")
VENV_PY = r"C:\Users\TDI-service\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
CHECK_PY = r"C:\FRPDepot\Dado\Tools\outlook\outlook_check.py"
CLOSED_TASKS = Path(r"C:\FRPDepot\Dado\30_Memory\closed_task_threads.jsonl")
DAYS_BACK = 60


def load_closed_task_threads() -> dict[str, dict]:
    """Return the latest closure record per conversation."""
    closed: dict[str, dict] = {}
    if not CLOSED_TASKS.exists():
        return closed
    for raw in CLOSED_TASKS.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        conversation_id = str(row.get("conversation_id") or "").strip()
        if not conversation_id:
            continue
        prior = closed.get(conversation_id)
        if prior is None or str(row.get("closed_at") or "") >= str(prior.get("closed_at") or ""):
            closed[conversation_id] = row
    return closed


def suppress_closed_task_threads(data: dict) -> tuple[dict, int]:
    """Remove closed candidates unless a later sent message created a new task."""
    closed = load_closed_task_threads()
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
    return data, len(suppressed_ids)

PROMPT = r"""
You are preparing Rachad's MORNING FOLLOW-UP DIGEST: the threads HE is waiting
on, where he wrote last to an outside party and nobody replied.

FRESH DATA IS ALREADY ON DISK - collected from the live mailbox minutes ago.
Read it with your FILE tools, not the terminal:
  C:\FRPDepot\Dado\20_Working\followups\waiting_on_them.json
Fields per thread: subject, to, business_days_silent, category
(rfq_quote | payment | general), due_after_business_days, overdue, urgent,
chase_draft_pending, conversation_id, preview. The wait clock counts WORKING
days only and the thresholds are already applied - trust "overdue".

Work ONLY the "overdue" list. Ignore "not_yet_due". Ignore
"chase_draft_waiting" - those are threads WE drafted a chase for within the last
"chase_quiet_days" days (each shows "chase_drafted_on"). That draft is UNSENT:
Rachad has not pressed Send. So never tell him those parties "were chased" - the
honest phrasing is "a chase draft is waiting". Note "drafts_in_thread" is
informational only: a draft someone else left in a thread does NOT mean it was
chased, and such a thread stays on the overdue list.

When you prepare a chase draft, pass --chase to outlook_tool.py reply-all. That
flag is what records it. Do NOT pass it for an ordinary reply, or you will
suppress follow-up monitoring on that thread for days.

FOR EACH OVERDUE THREAD, IN THIS ORDER:
1. READ THE WHOLE THREAD FIRST. Run, exactly as written (forward slashes
   survive every shell, and quote the id):
     C:/Users/TDI-service/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe C:/FRPDepot/Dado/Tools/outlook/outlook_check.py --thread "<conversationId>"
   The answer may have arrived out of band - by phone, by portal, or on a
   different thread. If the ask is already satisfied, or the thread is an
   automated notice, a newsletter, or something that genuinely needs no reply,
   DROP IT SILENTLY and do not mention it.
2. CHECK THE LEDGER: C:\FRPDepot\Dado\30_Memory\alert_ledger.md. An item in
   CLOSED is permanently silent unless genuinely new mail arrived after the
   closing date. An item chased in the last 7 days is not chased again.
3. PREPARE A DRAFT (this is what Rachad asked for - a chase he can send in one
   click). Use the reply-all tool on the ORIGINAL thread so the history,
   subject and recipients are preserved:
     ...\Tools\outlook\outlook_tool.py reply-all ...
   Follow the fit-profile reply rules exactly: reply-all from the latest live
   external non-draft message, the official HTML signature once, new text above
   the quoted history. THREADS NOBODY EVER ANSWERED (Rachad wrote first, zero
   external messages in the conversation - the tool refuses the normal path):
   use the chase-own path he approved 2026-08-02 - add --chase-own, which
   creates the Reply All draft under HIS OWN last sent message with the
   original recipients. The tool itself refuses --chase-own whenever any live
   external message exists, so wrongly choosing it cannot misfire.
   Tone: short, warm, no pressure, no invented facts, no
   new numbers he has not approved. A chase is two or three sentences - refer
   to what was sent, ask if they need anything to move it forward.
   DRAFTS ONLY (HARD RULE 1). You never send.
4. RECORD IT: append one dated line per chased item to the ledger's ALERTED
   section, and a receipt to 40_Logs\receipts.jsonl.

THEN RETURN THE DIGEST - one message, worst first, in this shape:
  "3 threads have gone quiet on you. Drafts are ready in Outlook.
   - SCT Composites, quote QT-000023, 42 working days - draft ready
   - ..."
Order by money and new work first (payment, rfq_quote), then by age. One line
each: who, what, how long, and whether a draft is waiting. If you could not
prepare a draft for something, say so in that line and why, in a few words.
No preamble, no explanation of your process, no markdown tables.

Return exactly [SILENT] if nothing is genuinely overdue after your reading.
A quiet morning should be quiet.

Delivery contract:
- Do NOT send or deliver messages yourself. This wrapper handles delivery.
- Output ONLY the final message text. The FIRST character of your output is the
  first character Rachad reads. No status tags, no spinners, no tool names, no
  file paths, no mention of crons or JSON, and never the word [SILENT] inside a
  real message.

Style: Rachad's AI copy. Terse, worst news first, one recommendation attached.
"""


def log(line: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {line}\n")
    except Exception:
        pass


def acquire_run_lock() -> bool:
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
        os.write(fd, f"pid={os.getpid()}\n".encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def release_run_lock() -> None:
    try:
        RUN_LOCK.unlink()
    except FileNotFoundError:
        pass


def prefetch() -> int:
    """Collect the follow-up candidates deterministically. Returns overdue count.

    Collection must not depend on the model: if it fails, the wrapper says so
    itself rather than leaving monitoring silently broken (2026-07-24 lesson).
    """
    proc = subprocess.run(
        [VENV_PY, CHECK_PY, "--waiting-on-them", str(DAYS_BACK)],
        text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=900,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        raise RuntimeError(f"follow-up collection failed rc={proc.returncode}: "
                           f"{(proc.stderr or out or 'no output').strip()[:300]}")
    data = json.loads(out)
    data, suppressed = suppress_closed_task_threads(data)
    rendered = json.dumps(data, indent=2, ensure_ascii=False)
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    (SWEEP_DIR / "waiting_on_them.json").write_text(rendered + "\n", encoding="utf-8")
    if suppressed:
        log(f"suppressed {suppressed} Rachad-closed draft tasks")
    return int(data.get("overdue_count", 0))


def run_dado() -> str | None:
    """Returns the model's text, or None if the run never produced one.

    Same lesson as dado_inbox_reasoner.run_dado: "" was returned on failure and
    is_silent("") is True, so a failed run looked exactly like a quiet morning.
    Observed live 2026-07-24 - a run started 21:47 with 15 overdue threads on
    file (QT-000023 at 42 working days, CAD 4,101.30 at 9) died around 22:32 and
    left nothing but "collected: 15 overdue" in the log. Rachad was told nothing.
    """
    env = os.environ.copy()
    env["HERMES_ACCEPT_HOOKS"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [hermes_exe(), "-p", PROFILE, "-z", PROMPT],
            cwd=WORKDIR, env=env, text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=2700,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        log(f"dado run raised: {type(exc).__name__}: {exc}")
        return None
    if proc.returncode != 0:
        log(f"dado failed rc={proc.returncode} stderr={(proc.stderr or '')[:400]!r}")
        return None
    return (proc.stdout or "").strip()


def run_once() -> int:
    flush_undelivered()
    try:
        overdue = prefetch()
    except Exception as exc:
        log(f"prefetch failed: {type(exc).__name__}: {exc}")
        send_clean("Follow-up check failed before your mail could be reviewed - "
                   f"{str(exc)[:180]}. Backend attention needed.")
        return 0
    log(f"collected: {overdue} overdue")
    if overdue == 0:
        log("nothing overdue - silent")
        return 0
    msg = run_dado()
    if not msg:
        # None = the run failed or timed out. "" on a clean exit = it produced
        # nothing even though prefetch just PROVED there is work. The prompt
        # requires either a digest or the literal [SILENT], so neither is an
        # all-clear. Checked before is_silent, which returns True for both.
        log(f"no usable digest ({'run failed' if msg is None else 'empty output'}); alerting")
        send_clean(
            f"Follow-up digest could not be produced - {overdue} threads are "
            "overdue and were not reviewed, so no chase drafts were prepared. "
            "This is NOT an all-clear. Backend attention needed."
        )
        return 0
    if is_silent(msg):
        log("silent")
        return 0
    clean = scrub_noise(msg)
    if is_silent(clean) or len(clean) < 8:
        # It spoke, but nothing survived the scrub. With overdue > 0 that is a
        # lost digest, not a quiet morning.
        log(f"nothing survived the scrub; alerting. raw={msg[:200]!r}")
        send_clean(
            f"Follow-up digest came back unreadable - {overdue} threads are "
            "overdue and were not reviewed. Backend attention needed."
        )
        return 0
    send_clean(clean)
    return 0


def main() -> int:
    if not acquire_run_lock():
        log("prior follow-up digest still running; skipped")
        return 0
    try:
        return run_once()
    finally:
        release_run_lock()


if __name__ == "__main__":
    sys.exit(main())
