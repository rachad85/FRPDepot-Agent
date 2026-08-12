"""Say something when a cron job's output never reached Rachad.

Nine of Dado's thirteen cron jobs deliver to him, including dado-job-watch and
dado-stall-tripwire - the two that exist to tell him something is wrong. When
hermes fails to hand one of those messages to Telegram, nothing says so: cron
computes the delivery outcome SEPARATELY from the run outcome, so mark_job_run
still writes last_status "ok" and the run looks perfect from every angle anyone
checks.

Measured on the other profile of this same shared hermes install, 2026-08-10: a
job with last_status "ok" and last_error null carried a hard
last_delivery_error and its message reached nobody. It was found weeks later by
an audit, and only survived that long because it was a one-shot - jobs.json's
last_delivery_error is last-write-wins, so a recurring job's identical failure
is erased by its next success. The same runtime serves Dado.

SILENT WHEN CLEAN. It never restarts, retries or re-sends anything: a message
hermes already dropped is not ours to re-send, and a duplicate alert is its own
kind of wrong. It reports; Rachad decides.

WHY IT IS NOT A CRON JOB: the thing it watches IS cron delivery. An alerter that
shares a failure mode with the thing it reports on is not an alerter (the rule
dado_urgent_alert.py was built on). So it runs from the 5-minute gateway
watchdog, out of band, and pages over the stdlib-only Telegram path.

TWO SOURCES, both opened READ-ONLY:
  1. cron\\executions.db column `delivery_outcome` - one durable row per ATTEMPT,
     so a drop cannot be erased by the next run. Needs the 20260811 hermes
     cron-delivery patch AND a gateway restart to start filling. Absent column
     => falls back to (2), the pre-patch behaviour.
  2. cron\\jobs.json `last_delivery_error` - last-write-wins, so it is a
     SUPPLEMENT, never the only source. It also covers attempts older than the
     ledger's 1000-row window.

REPORTED ONCE, BUT ONLY AFTER A CONFIRMED SEND. dado_urgent_alert.py applies a
60-minute per-reason cooldown and exits 0 when it suppresses. Marking findings
reported on a suppressed alert would destroy a second job's failure inside that
hour - which is exactly backlog item B-08, filed 2026-07-25 against job_runner
and stall_tripwire for the same mistake. So state advances only on
{"status": "SENT"}; anything else leaves the findings pending for the next tick.

Usage:
    python dado_delivery_watch.py
    python dado_delivery_watch.py --dry-run   # print findings, send nothing
Exit: 0 nothing to report (or send not confirmed), 1 alert sent.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

PROFILE = "dado"
STATE_DIR = Path(r"C:\FRPDepot\Dado\40_Logs")
STATE_FILE = STATE_DIR / "dado_delivery_watch_state.json"
# STOP_DADO.bat writes this and START_DADO.bat clears it; the gateway watchdog
# and the lane-health checker both honour it. So must we, or a deliberate stop
# pages him every five minutes.
DISABLE_FLAG = STATE_DIR / "gateway_disabled.flag"
ALERTER = Path(r"C:\FRPDepot\Dado\Tools\watch\dado_urgent_alert.py")
REASON = "cron_delivery"
# 'failed' = the adapter refused or errored. 'not_configured' = the job had
# something to say and nowhere to send it. Both are dropped messages.
# 'suppressed' is deliver=local working as intended and is NOT a finding.
FAILED_OUTCOMES = ("failed", "not_configured")
MAX_LINES = 10        # a long blind spell reports as a summary, not a wall
KEEP_REPORTED = 400   # bounded; the ledger holds at most 1000 rows anyway


def cron_dir() -> Path:
    return (Path(os.environ.get("LOCALAPPDATA", "")) / "hermes"
            / "profiles" / PROFILE / "cron")


def load_jobs() -> list[dict[str, Any]]:
    try:
        raw = json.loads((cron_dir() / "jobs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    jobs = raw.get("jobs") if isinstance(raw, dict) else raw
    return jobs if isinstance(jobs, list) else []


def ledger_findings(names: dict[str, str]) -> list[dict[str, Any]]:
    """Attempts whose delivery outcome was a drop.

    Returns [] when the column is absent (pre-patch runtime, or the gateway has
    not restarted yet) so the jobs.json pass still carries the watch.
    """
    db = cron_dir() / "executions.db"
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    try:
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute("PRAGMA table_info(executions)")}
        if "delivery_outcome" not in columns:
            return []
        rows = conn.execute(
            "SELECT id, job_id, claimed_at, finished_at, delivery_outcome,"
            "       delivery_error"
            "  FROM executions"
            " WHERE delivery_outcome IN (?, ?)"
            " ORDER BY claimed_at",
            FAILED_OUTCOMES,
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [
        {
            "key": f"exec:{row['id']}",
            "name": names.get(str(row["job_id"]), str(row["job_id"])),
            "when": row["finished_at"] or row["claimed_at"],
            "detail": row["delivery_error"] or f"delivery {row['delivery_outcome']}",
        }
        for row in rows
    ]


def jobs_findings(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Jobs carrying a last_delivery_error right now.

    Keyed by (job id + last_run_at) so the SAME failure is not re-reported every
    five minutes, while a NEW failure on the same job is a new key.
    """
    out = []
    for job in jobs:
        detail = job.get("last_delivery_error")
        if not detail:
            continue
        out.append({
            "key": f"job:{job.get('id')}@{job.get('last_run_at')}",
            "name": str(job.get("name") or job.get("id")),
            "when": str(job.get("last_run_at") or "?"),
            "detail": str(detail),
        })
    return out


def load_state() -> dict[str, Any]:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(state, dict) and isinstance(state.get("reported"), list):
            return state
    except (OSError, ValueError):
        pass
    return {"reported": []}


def save_state(state: dict[str, Any]) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state["reported"] = state["reported"][-KEEP_REPORTED:]
        STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass


def compose(findings: list[dict[str, Any]]) -> str:
    head = (
        f"{len(findings)} cron message(s) never reached you. The jobs themselves "
        f"ran; the delivery failed, and cron records that separately, so their "
        f"status still reads ok."
    )
    lines = [
        f"- {f['name']} ({f['when']}): {str(f['detail'])[:200]}"
        for f in findings[:MAX_LINES]
    ]
    if len(findings) > MAX_LINES:
        lines.append(f"- ...and {len(findings) - MAX_LINES} more.")
    tail = "Nothing was re-sent - a dropped message is not mine to re-send."
    return "\n".join([head, "", *lines, "", tail])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if DISABLE_FLAG.exists():
        return 0  # stopped on purpose is not a fault

    jobs = load_jobs()
    names = {str(j.get("id")): str(j.get("name") or j.get("id")) for j in jobs}

    seen: set[str] = set()
    findings: list[dict[str, Any]] = []
    for finding in ledger_findings(names) + jobs_findings(jobs):
        if finding["key"] in seen:
            continue
        seen.add(finding["key"])
        findings.append(finding)

    state = load_state()
    already = set(state.get("reported", []))
    fresh = [f for f in findings if f["key"] not in already]
    if not fresh:
        return 0  # silent when clean

    message = compose(fresh)
    if args.dry_run:
        print(message)
        return 0

    try:
        run = subprocess.run(
            [sys.executable, str(ALERTER), "--reason", REASON, "--message", message],
            capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        status = json.loads((run.stdout or "").strip().splitlines()[-1]).get("status")
    except Exception as exc:  # noqa: BLE001 - a broken alerter must not wedge the watchdog
        print(f"DELIVERY WATCH: could not reach the alerter - {type(exc).__name__}: {exc}")
        return 0

    if status != "SENT":
        # SUPPRESSED_COOLDOWN / SEND_FAILED / NO_CONFIG: he has NOT been told.
        # Leave every finding pending so the next tick tries again. Marking them
        # reported here is precisely the B-08 defect.
        print(f"DELIVERY WATCH: {len(fresh)} finding(s) pending, alert not confirmed ({status}).")
        return 0

    state["reported"] = list(state.get("reported", [])) + [f["key"] for f in fresh]
    save_state(state)
    return 1


if __name__ == "__main__":
    sys.exit(main())
