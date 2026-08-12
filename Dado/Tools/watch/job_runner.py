"""Run a long job WITHOUT holding Dado's turn hostage.

The 2026-07-24 failure: Dado started a 16-hour Google index, then sat in her own
turn polling it with a blocking 600-second call, seventeen times, telling Rachad
nothing for three hours, on course to hit her 60-call ceiling and die mid-job.

The rule this enforces: a long job is NOT something you watch. You start it, you
tell Rachad it started, and you END YOUR TURN so you stay reachable. The job
reports itself when it finishes.

    start   launch a job fully detached and return immediately
    status  one-line-per-job snapshot; costs milliseconds, never blocks
    watch   cron mode: announce jobs that finished or died, once, then stay silent

Usage:
    python job_runner.py start --name google-index -- <python.exe> <script.py> --mode drive
    python job_runner.py status
    python job_runner.py watch          (cron; silent when nothing changed)

Cron:
    hermes -p dado cron create "*/10 * * * *" --name dado-job-watch \
        --no-agent --script job_runner.py --deliver telegram:891365639
    (the cron passes no args, and argv-less runs default to `watch`)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

JOBS_DIR = Path(r"C:\FRPDepot\Dado\40_Logs\jobs")
RECEIPTS = Path(r"C:\FRPDepot\Dado\40_Logs\receipts.jsonl")
STALE_HEARTBEAT_MINUTES = 30    # job alive but its log has not moved
MAX_WATCH_MESSAGES = 5          # per tick; the remainder is HELD, never dropped
OUTPUT_TAIL_CHARS = 400

# A finite job that never ends is a leak. `proc.wait()` had no timeout at all,
# so a job that hung held its supervisor, its log handle and its "running"
# record forever. Six hours is far longer than any real Dado job (the 16-hour
# Google index in the docstring above was the reason this tool exists, and it
# is started with an explicit --timeout-hours).
DEFAULT_TIMEOUT_HOURS = 6.0

# ...but SOME jobs are supposed to run forever, and killing those is the more
# expensive mistake. `zoho-ui-live` is an authenticated Edge session holding a
# CDP endpoint on 127.0.0.1:9228; eight scripts under Dado\20_Working attach to
# it with playwright's connect_over_cdp instead of logging in again, including
# the SCT payment status check. On 2026-08-07 it was tracked as an ordinary
# finite job, so it looked "stuck" after 30 minutes, fired a false stall alert
# to Rachad, and sat "running" for 20 hours looking like a leak.
#
# A service job is exempt from BOTH the timeout and the stall alert. It is
# still watched for death, which is the thing that actually matters for a
# session other tools depend on.
SERVICE_JOB = "service"

# Windows detached-launch flags: the child must survive its parent, and must not
# die when the launching console (or Dado's turn) goes away.
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def now() -> dt.datetime:
    return dt.datetime.now()


def stamp() -> str:
    return now().isoformat(timespec="seconds")


def receipt_stamp() -> str:
    """UTC, timezone-AWARE - the one format receipts.jsonl uses.

    This used to be stamp(), which is dt.datetime.now() with no tzinfo, and it
    was the only naive writer in the tree: 13 of 4,829 receipt lines carried no
    timezone at all while 4,807 were UTC-aware. Comparing a naive datetime to an
    aware one raises TypeError in Python, so any receipt-freshness check either
    crashes or silently skips those rows - it broke a freshness query during the
    2026-07-25 sweep. Job records keep local wall-clock times in their own file;
    only the shared audit log is normalised here.
    """
    return dt.datetime.now(dt.timezone.utc).isoformat()


def receipt(action: str, evidence: str) -> None:
    try:
        RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
        with RECEIPTS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": receipt_stamp(), "action": action,
                                 "evidence": evidence}) + "\n")
    except OSError:
        pass


def job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def log_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.log"


def load(job_id: str) -> dict:
    return json.loads(job_path(job_id).read_text(encoding="utf-8"))


def save(job: dict) -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = job_path(job["id"])
    # Unique per process: the temp name used to be derived from the job id alone,
    # so the supervisor and the watch cron could open the identical
    # <job_id>.json.tmp at once and raise PermissionError on Windows - uncaught,
    # inside cmd_watch's loop, after earlier jobs had already been marked
    # reported.
    tmp = path.with_suffix(f".json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(job, indent=1), encoding="utf-8")
    tmp.replace(path)


def save_flags(job_id: str, **flags) -> None:
    """Re-read, set only these keys, write back.

    cmd_watch used to mutate a job dict it had loaded up to 20 seconds earlier -
    the `tasklist` call in pid_alive() is that slow - and save the whole stale
    object. If the supervisor recorded status=done, exit_code=0 inside that
    window, watch would overwrite it with status=died, exit_code=None and
    announce a successful 45-minute job as dead. Only the supervisor owns
    `status`; watch now touches nothing but its own bookkeeping flags.
    """
    try:
        job = load(job_id)
    except (OSError, json.JSONDecodeError, ValueError):
        return
    job.update(flags)
    save(job)


def all_jobs() -> tuple[list[dict], list[str]]:
    """Every job on record, plus the names of any files that would not parse.

    Unreadable files are RETURNED, not swallowed. Silently skipping one means a
    job is forgotten forever and its completion is never announced -- silence
    that looks exactly like "nothing to report". (utf-8-sig so a stray BOM,
    which is what PowerShell writes by default, cannot orphan a job.)
    """
    if not JOBS_DIR.exists():
        return [], []
    jobs: list[dict] = []
    broken: list[str] = []
    for path in sorted(JOBS_DIR.glob("*.json")):
        try:
            jobs.append(json.loads(path.read_text(encoding="utf-8-sig")))
        except (OSError, json.JSONDecodeError, ValueError):
            broken.append(path.name)
    return jobs, broken


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=20,
         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return True  # cannot tell -> assume alive rather than cry wolf
    return str(pid) in out


def tail_of_log(job_id: str, chars: int = OUTPUT_TAIL_CHARS) -> str:
    try:
        text = log_path(job_id).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-chars:].strip()


def summarize_output(job_id: str) -> str:
    """One useful line for Rachad's phone.

    The naive "last line of the log" is often junk: a job ending in
    pretty-printed JSON leaves a bare "}". So prefer a trailing JSON object's
    own summary fields, then fall back to the last line that carries real text.
    """
    try:
        text = log_path(job_id).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tail = text[-4000:]
    start = tail.rfind("\n{")
    if start != -1:
        try:
            data = json.loads(tail[start:])
        except (json.JSONDecodeError, ValueError):
            data = None
        if isinstance(data, dict):
            bits = [f"{k}={data[k]}" for k in ("status", "resume") if data.get(k)]
            counts = {k: v for k, v in data.items() if isinstance(v, dict)}
            for name, block in counts.items():
                interesting = {k: v for k, v in block.items()
                               if k in {"processed", "fetched", "indexed", "withheld", "errors"} and v}
                if interesting:
                    bits.append(f"{name}: " + ", ".join(f"{k}={v}" for k, v in interesting.items()))
            if bits:
                return " | ".join(bits)[:300]
    for line in reversed(tail.splitlines()):
        stripped = line.strip().strip("{}[],")
        if stripped:
            return line.strip()[:200]
    return ""


def heartbeat_age_minutes(job_id: str) -> float | None:
    try:
        mtime = log_path(job_id).stat().st_mtime
    except OSError:
        return None
    return (now().timestamp() - mtime) / 60


# --------------------------------------------------------------------------- start

def cmd_start(args: argparse.Namespace) -> int:
    if not args.command:
        print("nothing to run: put the command after --")
        return 2
    job_id = f"{now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    job = {
        "id": job_id,
        "name": args.name,
        "command": args.command,
        "status": "starting",
        "pid": None,
        "started": stamp(),
        "finished": None,
        "exit_code": None,
        "reported": False,
        "note": args.note or "",
        # A service job runs until something stops it: no timeout, no stall
        # alert. Everything else is finite and gets a ceiling.
        "kind": SERVICE_JOB if args.service else "job",
        "timeout_hours": None if args.service else float(args.timeout_hours),
    }
    save(job)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    log_path(job_id).write_text("", encoding="utf-8")
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "_run", job_id],
        creationflags=DETACHED,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    receipt("background_job_started", f"{job_path(job_id)}#{args.name}")
    print(json.dumps({
        "job_id": job_id,
        "name": args.name,
        "status": "started",
        "log": str(log_path(job_id)),
        "next": ("Job is running detached. END YOUR TURN and tell Rachad it started. "
                 "Do NOT poll it. dado-job-watch will announce the result."),
    }, indent=1))
    return 0


# ----------------------------------------------------------------------------- run

def cmd_run(args: argparse.Namespace) -> int:
    """Supervisor: executes the job, then records how it ended. Runs detached."""
    job_id = args.job_id
    job = load(job_id)
    job.update(status="running", pid=os.getpid())
    save(job)
    code = -1
    timed_out = False
    # A service job is meant to outlive its start; everything else gets a
    # ceiling so a hang cannot hold the supervisor forever (see DEFAULT_TIMEOUT_HOURS).
    if job.get("kind") == SERVICE_JOB:
        wait_seconds = None
    else:
        try:
            hours = float(job.get("timeout_hours") or DEFAULT_TIMEOUT_HOURS)
        except (TypeError, ValueError):
            hours = DEFAULT_TIMEOUT_HOURS
        wait_seconds = hours * 3600 if hours > 0 else None
    try:
        with log_path(job_id).open("w", encoding="utf-8", errors="replace") as out:
            proc = subprocess.Popen(
                job["command"],
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                code = proc.wait(timeout=wait_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                # Kill the TREE. These jobs launch browsers and interpreters
                # that spawn their own children; terminating only the direct
                # child leaves the grandchildren running and the port held.
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True, timeout=30,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except Exception:
                    proc.kill()
                try:
                    code = proc.wait(timeout=30)
                except Exception:
                    code = -1
                out.write(
                    f"\nSUPERVISOR: killed after the {wait_seconds / 3600:.1f} h "
                    f"timeout. If this job is supposed to run indefinitely, start "
                    f"it with --service so it is never timed out.\n"
                )
    except Exception as exc:
        try:
            with log_path(job_id).open("a", encoding="utf-8") as out:
                out.write(f"\nSUPERVISOR ERROR: {type(exc).__name__}: {exc}\n")
        except OSError:
            pass
        code = -1
    job = load(job_id)
    # "timeout" is its own outcome, not a plain failure. A job we killed on the
    # clock and a job that ran and returned non-zero need different responses,
    # and flattening them into "failed" hides which one happened.
    job.update(
        status="timeout" if timed_out else ("done" if code == 0 else "failed"),
        exit_code=code,
        finished=stamp(),
    )
    save(job)
    receipt("background_job_finished", f"{job_path(job_id)}#exit={code}")
    return 0


# -------------------------------------------------------------------------- status

def cmd_status(args: argparse.Namespace) -> int:
    jobs, broken = all_jobs()
    for name in broken:
        print(f"UNREADABLE job file: {name}")
    if not jobs:
        print("no background jobs on record")
        return 0
    for job in jobs:
        age = heartbeat_age_minutes(job["id"])
        beat = f"{age:.0f}m ago" if age is not None else "n/a"
        line = (f"{job['id']}  {job['name']:<22s} {job['status']:<8s} "
                f"exit={job['exit_code']}  last_output={beat}")
        print(line)
        if args.verbose:
            tail = tail_of_log(job["id"])
            if tail:
                print("    " + tail.replace("\n", "\n    "))
    return 0


# --------------------------------------------------------------------------- watch

def cmd_watch(_: argparse.Namespace) -> int:
    """Cron mode. Announce each finished/dead job ONCE. Silent otherwise."""
    # (message, job_id, flags-to-set-once-it-is-actually-printed). Nothing is
    # marked reported until it survives the print cap below - jobs 6+ in one tick
    # used to be flagged on disk and then discarded, losing the announcement for
    # good.
    pending: list[tuple[str, str | None, dict]] = []
    jobs, broken = all_jobs()
    for job in jobs:
        status = job.get("status")

        if status in {"done", "failed", "timeout"} and not job.get("reported"):
            if status == "done":
                verdict = "finished"
            elif status == "timeout":
                verdict = "was KILLED on its timeout (it never finished)"
            else:
                verdict = f"FAILED (exit {job['exit_code']})"
            summary = summarize_output(job["id"])
            detail = f" {summary}" if summary else ""
            pending.append((f"Background job '{job['name']}' {verdict}.{detail}",
                            job["id"], {"reported": True}))
            continue

        if status in {"running", "starting"}:
            # A supervisor that died without recording an outcome would otherwise
            # leave the job "running" forever and never be announced.
            if job.get("pid") and not pid_alive(job["pid"]):
                # Re-read before believing it: the supervisor may have finished
                # during the ~20s pid_alive() spent shelling out to tasklist.
                try:
                    fresh = load(job["id"])
                except (OSError, json.JSONDecodeError, ValueError):
                    fresh = job
                if fresh.get("status") in {"done", "failed"}:
                    if not fresh.get("reported"):
                        verdict = ("finished" if fresh["status"] == "done"
                                   else f"FAILED (exit {fresh['exit_code']})")
                        summary = summarize_output(job["id"])
                        detail = f" {summary}" if summary else ""
                        pending.append((f"Background job '{job['name']}' {verdict}.{detail}",
                                        job["id"], {"reported": True}))
                    continue
                if not job.get("reported"):
                    pending.append((
                        f"Background job '{job['name']}' died without finishing "
                        f"(process gone, no exit code recorded). It may need restarting.",
                        job["id"], {"status": "died", "reported": True}))
                continue
            age = heartbeat_age_minutes(job["id"])
            if (age is not None and age >= STALE_HEARTBEAT_MINUTES
                    and job.get("kind") != SERVICE_JOB
                    and not job.get("stall_reported")):
                # A SEPARATE flag from `reported`. Sharing one meant the stall
                # warning consumed the completion notice: a long OCR job that
                # went quiet, was flagged stuck, then finished cleanly was never
                # reported as finished at all.
                pending.append((
                    f"Background job '{job['name']}' has produced no output for "
                    f"{age:.0f} min. It may be stuck.",
                    job["id"], {"stall_reported": True}))

    # Broken-file notices go LAST. Appended first, five unparseable files refilled
    # the cap on every tick and permanently starved real job results.
    broken_msgs = [
        (f"A background job record could not be read ({name}). Whatever job it "
         "tracked will never report its result — the backend should look.", None, {})
        for name in broken
    ]
    pending.extend(broken_msgs)

    shown = pending[:MAX_WATCH_MESSAGES]
    held = len(pending) - len(shown)
    if shown:
        lines = [text for text, _, _ in shown]
        if held > 0:
            lines.append(f"({held} more job update(s) held for the next tick.)")
        message = "\n".join(lines)
        # The print STAYS. It is the durable record under cron\output\<job_id>\
        # and the audit trail for what this tick decided; it is no longer the
        # delivery. Removing it because "the script sends now" would delete that.
        print(message)

        # BACKLOG B-08. This used to persist reported/stall_reported here,
        # unconditionally, on the strength of the print(). Delivery happened
        # later, out of process, with no callback - so a dropped message left
        # state saying "announced" and it was never re-emitted. cron
        # deliver:telegram has no retry and no undelivered queue, and nothing
        # reads last_delivery_error.
        #
        # Now the script owns delivery and the flag follows the CONFIRMED send.
        # queue_on_failure=False on purpose: this alert is re-derived from the
        # job records every 10 minutes, so leaving the flag unset IS the queue.
        if _deliver(message):
            for _, job_id, flags in shown:
                if job_id and flags:
                    save_flags(job_id, **flags)
        else:
            # Nothing persisted: the next tick re-derives and tries again.
            print("(delivery not confirmed - these updates stay owed and will "
                  "be re-sent on the next tick.)")
    return 0


def _deliver(message: str) -> bool:
    """Send an announcement and report whether Telegram actually took it.

    The reasoner import is LAZY and local on purpose: `job_runner start` is how
    Dado launches every long job, and a broken import in a sibling module must
    never be able to take that path down.
    """
    # *** NEVER SEND FROM A TEST RUN. *** Added 2026-08-12 after wiring delivery
    # into this path caused a plain `pytest` run of the existing suite to put 14
    # real messages on Rachad's phone. The suite exercises the announce path, so
    # any future test that touches it would do the same. Reported as delivered
    # so the caller's persist-on-confirmed-send logic is still exercised; a test
    # that wants to cover a FAILED send patches send_clean directly.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        print("(test run - delivery simulated, nothing sent.)")
        return True
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from dado_inbox_reasoner import send_clean
    except Exception as exc:  # noqa: BLE001
        print(f"(could not load the sender - {type(exc).__name__}: {exc}; "
              "this tick's updates stay owed.)")
        return False
    try:
        return bool(send_clean(message, queue_on_failure=False))
    except Exception as exc:  # noqa: BLE001
        print(f"(send raised - {type(exc).__name__}: {exc}; updates stay owed.)")
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detached job runner for Dado")
    sub = parser.add_subparsers(dest="mode")

    start = sub.add_parser("start", help="launch a job detached and return at once")
    start.add_argument("--name", required=True)
    start.add_argument("--note", default="")
    start.add_argument(
        "--timeout-hours", type=float, default=DEFAULT_TIMEOUT_HOURS,
        help=f"kill the job after this many hours (default {DEFAULT_TIMEOUT_HOURS}); "
             "0 means no limit")
    start.add_argument(
        "--service", action="store_true",
        help="this job is SUPPOSED to run indefinitely (e.g. an authenticated "
             "browser session other tools attach to). Never timed out, never "
             "reported as stalled; still watched for death.")
    start.add_argument("command", nargs=argparse.REMAINDER)
    start.set_defaults(func=cmd_start)

    run = sub.add_parser("_run", help=argparse.SUPPRESS)
    run.add_argument("job_id")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="snapshot of all jobs (never blocks)")
    status.add_argument("-v", "--verbose", action="store_true")
    status.set_defaults(func=cmd_status)

    watch = sub.add_parser("watch", help="cron mode: announce changes once")
    watch.set_defaults(func=cmd_watch)
    return parser


def main() -> int:
    parser = build_parser()
    argv = sys.argv[1:] or ["watch"]      # cron invokes us with no arguments
    args = parser.parse_args(argv)
    # argparse.REMAINDER keeps the separating "--"; drop it so the command is clean
    command = getattr(args, "command", None)
    if command and command[0] == "--":
        args.command = command[1:]

    # B-08 consequence, and the trap in that change: this job now runs with cron
    # `deliver: local`, so hermes delivers NOTHING for it - including the
    # "Script exited with code 1" summary that used to reach Rachad when this
    # script crashed. A watcher that dies silently is worse than no watcher, so
    # the watch tick reports its own crash through the out-of-band alerter (not
    # through cron, which is exactly what is no longer listening).
    if getattr(args, "mode", None) == "watch" or argv[0] == "watch":
        try:
            return args.func(args)
        except Exception as exc:  # noqa: BLE001
            import traceback
            detail = traceback.format_exc(limit=4)
            print(f"JOB WATCH FAILED: {type(exc).__name__}: {exc}")
            try:
                if os.environ.get("PYTEST_CURRENT_TEST"):
                    raise RuntimeError("test run - alerter suppressed")
                subprocess.run(
                    [sys.executable,
                     str(Path(__file__).resolve().parent / "dado_urgent_alert.py"),
                     "--reason", "job_watch_crashed",
                     "--message",
                     "Dado's background-job watch crashed and is not reporting "
                     "finished or failed jobs.\n\n"
                     f"{type(exc).__name__}: {exc}\n\n{detail[-600:]}"],
                    capture_output=True, text=True, timeout=120,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:
                pass  # the print above is still in the cron output file
            return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
