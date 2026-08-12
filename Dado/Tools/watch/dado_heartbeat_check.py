"""Notice when DADO'S OWN watchdog stops running at all.

Built 2026-08-11, the mirror image of C:\\AgentTeam\\Sync\\aze_heartbeat_check.py
and the reason the pair is worth having at all.

THE GAP THIS FILLS. dado_gateway_watchdog.ps1 is what keeps Dado alive, and
since earlier today it is also what watches Aze's watchdog. That made it the
single point the whole chain hangs off -- and nothing watched IT. If the
"FRPDepot Dado Gateway Keep-Alive" task is disabled, deleted, or silently stops
firing, then Dado loses her auto-recovery AND Aze loses her monitor, and the
silence looks exactly like health from every angle. That is the 2026-07-25 shape
(thirteen hours unreachable, every other signal green) with a bigger blast
radius.

So Dado's watchdog stamps a heartbeat on every run, and this reads its AGE from
a COMPLETELY SEPARATE SCHEDULER: the "TDI Aze Always-On Watchdog" task, a
different task on a different 5-minute cycle owned by a different profile. That
independence is the whole point -- a checker sharing a scheduler with the thing
it checks dies with it.

Neither side can report the case where BOTH schedulers are dead. That is
inherent to mutual monitoring and is stated rather than hidden; a machine with
no scheduled tasks running at all is a different (and much louder) problem.

WHY THE MTIME AND NOT THE CONTENT: the file's modification time is set by the
filesystem, needs no parsing, and cannot be wrong about locale or timezone. The
exit context written inside is for a human, never the signal.

WHERE IT LIVES AND WHY. This file, its state, its logging and its alerting all
stay inside the FRP tree and use Dado's own alerter -- a Dado problem should
arrive from Dado's bot. Aze's watchdog only invokes it if present, detached, and
discards everything it produces, so no FRP detail ever reaches a TDI log.

SILENT WHEN HEALTHY. It has no recovery power at all, by design: it reports, and
Rachad decides.

Usage:
    python dado_heartbeat_check.py
    python dado_heartbeat_check.py --dry-run
Exit: 0 healthy / silent, 1 alert raised.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(r"C:\FRPDepot")
LOGS = ROOT / "Dado" / "40_Logs"
HEARTBEAT = LOGS / "dado_watchdog_heartbeat.txt"
STATE_FILE = LOGS / "dado_heartbeat_state.json"
ALERTER = ROOT / "Dado" / "Tools" / "watch" / "dado_urgent_alert.py"
# If this is missing, FRP Depot is not installed on this PC. Staying silent is
# then the only correct answer -- otherwise a TDI-only machine would page Rachad
# every ten minutes forever.
WATCHDOG_SCRIPT = ROOT / "Dado" / "Tools" / "watch" / "dado_gateway_watchdog.ps1"
# STOP_DADO.bat writes this. The watchdog still RUNS (and still stamps the
# heartbeat) while it is set -- it just declines to start her -- so the flag is
# not a reason to go quiet here. It is read only so the alert text is honest.
DISABLE_FLAG = LOGS / "gateway_disabled.flag"
VENV_PYTHON = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe"

# The watchdog fires every 5 minutes. 20 minutes is four consecutive missed
# runs -- comfortably past a reboot or a machine briefly under load, while still
# catching a stopped task the same morning rather than the same week.
STALE_MINUTES = 20

# Two consecutive stale samples before paging, at Aze's 5-minute cadence. This
# is a "your safety net is gone" alert, not an emergency: Dado is very likely
# still running as it fires. ~25 minutes to report is the right trade for never
# crying wolf over a slow boot.
CONSECUTIVE_SAMPLES_BEFORE_ALERTING = 2

REASON = "watchdog_not_running"


def heartbeat_age_minutes() -> float | None:
    """Minutes since the watchdog last completed a run, or None if never."""
    try:
        return (time.time() - HEARTBEAT.stat().st_mtime) / 60.0
    except OSError:
        return None


def assess() -> dict[str, Any]:
    if not WATCHDOG_SCRIPT.exists():
        return {"verdict": "not_installed",
                "detail": "FRP Depot is not installed on this PC; nothing is expected"}

    age = heartbeat_age_minutes()
    if age is None:
        return {"verdict": "watchdog_not_running", "age_minutes": None,
                "detail": "the watchdog has never stamped a heartbeat"}
    if age > STALE_MINUTES:
        return {"verdict": "watchdog_not_running", "age_minutes": round(age, 1),
                "detail": f"last run was {round(age)} minutes ago "
                          f"(expected every 5, stale past {STALE_MINUTES})"}
    return {"verdict": "healthy", "age_minutes": round(age, 1)}


def alert_message(verdict: dict[str, Any]) -> str:
    lines = [
        "DADO'S WATCHDOG HAS STOPPED RUNNING.",
        "",
        f"{verdict['detail']}.",
        "",
        "This is not the same as Dado being down - she is probably still up right "
        "now. What is gone is her AUTO-RECOVERY: if she dies from this point, "
        "nothing will restart her. It also means AZE IS NO LONGER BEING WATCHED, "
        "because Dado's keep-alive is what checks Aze's watchdog is still alive. "
        "This is reported by the TDI Aze watchdog, a separate scheduled task, "
        "because a stopped watchdog cannot report itself.",
        "",
        "To check: Task Scheduler -> 'FRPDepot Dado Gateway Keep-Alive' - is it "
        "Enabled, and what is its Last Run Time? Also look at "
        "C:\\FRPDepot\\Dado\\40_Logs\\gateway_watchdog.log.",
    ]
    if DISABLE_FLAG.exists():
        lines += [
            "",
            "NOTE: gateway_disabled.flag is set, so Dado was stopped on purpose "
            "with STOP_DADO.bat. That alone does NOT explain this - the watchdog "
            "still runs and still stamps its heartbeat while she is stopped.",
        ]
    return "\n".join(lines)


def load_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(value: dict[str, Any]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass  # bookkeeping only; never fail the check over it


def run_alerter(args: list[str]) -> str:
    if not ALERTER.exists() or not VENV_PYTHON.exists():
        return json.dumps({"status": "ALERTER_MISSING"})
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), str(ALERTER), *args],
            capture_output=True, text=True, timeout=120,
        )
        return (result.stdout or result.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001 - must never break the caller's watchdog
        return json.dumps({"status": "ALERTER_FAILED", "error": str(exc)[:500]})


STALLED_RUNS_FILE = VENV_PYTHON.parents[3] / "profiles" / "dado" / "cron" / "stalled_runs.json"
# A SEPARATE state file, deliberately: save_state() rewrites its whole dict, so
# a key added there would be dropped by the next verdict write.
STALLED_STATE_FILE = LOGS / "dado_cron_stalled_state.json"
MAX_REMEMBERED_STALL_EPISODES = 200
STALLED_REASON = "cron_stalled_runs"


def check_cron_stalled_runs(dry_run: bool = False) -> dict[str, Any]:
    """Alert once per wedged-run episode. Silent when the marker is absent.

    The cron runtime writes stalled_runs.json ONLY while at least one run is
    wedged and removes it the moment things are clean, so file-present IS the
    signal and no threshold logic belongs here.

    Alerts go through the raw-Telegram alerter, not cron delivery, ON PURPOSE:
    the condition is that a cron job is stuck, and a watcher must never share a
    failure mode with the thing it watches.

    DATA WALL: reads only Dado's OWN profile directory under %LOCALAPPDATA%.
    Nothing here touches C:\\AgentTeam.

    NEVER calls the alerter with --clear - that also deletes the shared Desktop
    marker and would erase an unread alert raised for another reason.
    """
    if not STALLED_RUNS_FILE.exists():
        return {"status": "clean", "alerted": 0}
    try:
        report = json.loads(STALLED_RUNS_FILE.read_text(encoding="utf-8"))
        entries = report.get("stalled") or []
    except (OSError, ValueError) as exc:
        return {"status": "unreadable", "error": str(exc)[:200], "alerted": 0}
    if not entries:
        return {"status": "clean", "alerted": 0}

    try:
        seen = json.loads(STALLED_STATE_FILE.read_text(encoding="utf-8"))
        already = seen.get("episodes") if isinstance(seen, dict) else []
    except (OSError, ValueError):
        already = []
    already = [str(x) for x in (already or [])]

    fresh = [e for e in entries if str(e.get("execution_id")) not in already]
    if dry_run:
        return {"status": "wedged", "total": len(entries), "new": len(fresh),
                "would_alert": [e.get("job_name") for e in fresh]}
    if not fresh:
        return {"status": "wedged", "total": len(entries), "alerted": 0}

    lines = ["Cron jobs wedged on Dado - they are still running, nothing was killed."]
    for e in fresh:
        mins = int(e.get("age_seconds") or 0) // 60
        lines.append(
            f"- '{e.get('job_name')}' in flight {mins} min "
            f"({e.get('skipped_occurrences') or 0} scheduled run(s) skipped meanwhile)."
        )
    lines.append("")
    lines.append("Check with: hermes -p dado cron status")
    sent = run_alerter(["--reason", STALLED_REASON, "--message", "\n".join(lines)])

    # Persist ONLY on a confirmed send, so a failed alert is retried on the next
    # run rather than being marked reported and lost (backlog B-08).
    if '"status": "SENT"' in sent or '"status":"SENT"' in sent:
        already.extend(str(e.get("execution_id")) for e in fresh)
        try:
            STALLED_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STALLED_STATE_FILE.write_text(
                json.dumps({"episodes": already[-MAX_REMEMBERED_STALL_EPISODES:]}),
                encoding="utf-8",
            )
        except OSError:
            pass
        return {"status": "wedged", "total": len(entries), "alerted": len(fresh)}
    return {"status": "wedged", "total": len(entries), "alerted": 0,
            "delivery": sent[:200]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Alert when Dado's watchdog stops running at all. "
                    "Silent when healthy. Never starts or restarts anything."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the verdict and send nothing.")
    args = parser.parse_args(argv)

    # Independent of watchdog health - the gateway can be perfectly alive while
    # a cron job is wedged - so this never feeds the verdict state machine.
    stalled = check_cron_stalled_runs(dry_run=args.dry_run)

    verdict = assess()

    if args.dry_run:
        print(json.dumps({"verdict": verdict, "cron_stalled_runs": stalled}, indent=2))
        return 0

    state = load_state()
    streak = int(state.get("consecutive") or 0)
    alerted = bool(state.get("alerted"))

    if verdict["verdict"] != "watchdog_not_running":
        # Recovered, or never broken, or FRP is not installed here. Retire the
        # alert ONLY if we actually raised one: dado_urgent_alert --clear also
        # deletes the shared Desktop marker, which is written for ANY reason, so
        # calling it on every healthy run would quietly erase the marker left by
        # a real gateway-down alert Rachad had not read yet.
        if alerted and verdict["verdict"] == "healthy":
            run_alerter(["--clear", "--reason", REASON])
            alerted = False
        save_state({"verdict": verdict["verdict"], "consecutive": 0, "alerted": alerted})
        return 0

    streak += 1
    if streak < CONSECUTIVE_SAMPLES_BEFORE_ALERTING:
        save_state({"verdict": verdict["verdict"], "consecutive": streak, "alerted": alerted})
        return 0

    print(run_alerter(["--reason", REASON, "--message", alert_message(verdict)]))
    save_state({"verdict": verdict["verdict"], "consecutive": streak, "alerted": True})
    return 1


if __name__ == "__main__":
    sys.exit(main())
