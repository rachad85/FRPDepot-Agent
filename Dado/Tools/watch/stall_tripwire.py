"""Deterministic stall tripwire for Dado -- catches a turn that is stuck NOW.

Why this exists (2026-07-24): Rachad gave Dado a task at 16:02. She launched a
background job and then blocked on a 600-second status poll SEVENTEEN times in a
row, sending him nothing for three hours, and would have hit her 60-call ceiling
and died mid-job. Nothing stopped her:

- SOUL "## LONG JOBS" (progress ping every ~10 min) depends on the model choosing
  to speak, and a blocking 600s tool call leaves it no chance to. It had already
  failed the same way on 2026-07-22.
- config tool_loop_guardrails count FAILING tool calls. A poll that succeeds and
  says "still running" is a successful call, so the counter never moves.
- Aze's behavior_tripwires.py reads "response ready" from gateway.log, which is
  only written AFTER a turn completes. A turn stuck for three hours writes that
  line never. Post-hoc detection cannot catch an in-flight stall.

So this checks the one thing those three miss: a turn that is OPEN RIGHT NOW,
older than the threshold, with no sign of life to Rachad. Deterministic,
read-only, silent when clean. Runs no-agent so it cannot itself stall.

Cron:
    hermes -p dado cron create "*/15 * * * *" --name dado-stall-tripwire \
        --no-agent --script stall_tripwire.py --deliver telegram:891365639
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

STATE_PATH = Path(r"C:\FRPDepot\Dado\40_Logs\stall_tripwire_state.json")
STALL_MINUTES = 20          # SOUL promises a sign of life every 10-15 min
POLL_LOOP_REPEATS = 3       # N near-timeout tool calls in one turn = babysitting
POLL_NEAR_TIMEOUT_S = 300   # terminal.timeout is 600; anything over 300s is a block
REALERT_MINUTES = 60        # while still stuck, remind at most hourly
MAX_ALERTS_PER_TURN = 3     # never spam: three notices then stay quiet
MAX_PROBLEMS_PER_TICK = 3   # printed per run; the rest stay tracked, unspent
TAIL_LINES = 30_000
TAIL_BYTES = 8 * 1024 * 1024  # read backwards this far; ~a week of agent.log

TS = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+"
TURN_START = re.compile(TS + r".*agent\.turn_context: conversation turn: session=(\S+)")
TURN_END = re.compile(TS + r".*agent\.conversation_loop: Turn ended:.*session=(\S+)")
TOOL_DONE = re.compile(TS + r".*\[(\S+)\] agent\.tool_executor: tool (\S+) completed \(([\d.]+)s")
API_CALL = re.compile(TS + r".*\[(\S+)\] agent\.conversation_loop: API call #(\d+):")
PLATFORM = re.compile(r"platform=(\S+)")


def log_path() -> Path:
    local = os.environ.get("LOCALAPPDATA", "")
    return Path(local) / "hermes" / "profiles" / "dado" / "logs" / "agent.log"


def parse_ts(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def load_state() -> dict:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(state, dict):
            return state
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(STATE_PATH)


def read_tail(path: Path) -> list[str]:
    """The tail of the log, read backwards by bytes rather than whole-file.

    readlines() loaded the entire agent.log on every 15-minute tick and kept the
    last TAIL_LINES. Two problems: it gets slower as the log grows (already
    1.1 MB after two days), and a turn whose START line has scrolled out of the
    window becomes invisible - open_turns can only see a turn it has a start
    line for, so the long stall this script exists to catch disappears exactly
    when the log is busiest. Reading by bytes lets the window be generous
    without the cost, and TAIL_BYTES is sized well past a day of traffic.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                fh.readline()  # drop the partial first line
            raw = fh.read()
    except OSError:
        return []
    lines = raw.decode("utf-8", errors="replace").splitlines(keepends=True)
    return lines[-TAIL_LINES:]


def rotated_siblings(path: Path) -> list[Path]:
    """agent.log.1, agent.log.2026-07-24 and friends, newest first.

    A rotation while a turn is open would otherwise orphan that turn's start
    line and hide the stall completely.
    """
    try:
        found = [p for p in path.parent.glob(path.name + ".*") if p.is_file()]
    except OSError:
        return []
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)[:2]


def open_turns(lines: list[str]) -> dict[str, dict]:
    """Per session: the newest turn start that has no matching Turn ended after it.

    Sessions are tracked separately because cron sweeps run their own hermes
    process and interleave their turns into this same log; one stuck Telegram
    turn must not be masked by a healthy cron turn finishing beside it.
    """
    turns: dict[str, dict] = {}
    for line in lines:
        start = TURN_START.search(line)
        if start:
            stamp, session = start.group(1), start.group(2)
            platform = PLATFORM.search(line)
            turns[session] = {
                "started": parse_ts(stamp),
                "platform": platform.group(1) if platform else "?",
                "long_tools": 0,
                "last_activity": parse_ts(stamp),
                "api_calls": 0,
            }
            continue
        end = TURN_END.search(line)
        if end:
            turns.pop(end.group(2), None)
            continue
        tool = TOOL_DONE.search(line)
        if tool:
            session = tool.group(2)
            entry = turns.get(session)
            if entry:
                entry["last_activity"] = parse_ts(tool.group(1))
                if float(tool.group(4)) >= POLL_NEAR_TIMEOUT_S:
                    entry["long_tools"] += 1
            continue
        call = API_CALL.search(line)
        if call:
            entry = turns.get(call.group(2))
            if entry:
                entry["last_activity"] = parse_ts(call.group(1))
                entry["api_calls"] = int(call.group(3))
    return turns


def main() -> int:
    path = log_path()
    lines = read_tail(path)
    if not lines:
        return 0
    # If the newest turn start is not in the window, a rotation may have carried
    # it into a sibling file. Prepend those so an open turn cannot be orphaned.
    if not any(TURN_START.search(line) for line in lines):
        for sibling in rotated_siblings(path):
            lines = read_tail(sibling) + lines
            if any(TURN_START.search(line) for line in lines):
                break
    state = load_state()
    now = dt.datetime.now()
    problems: list[tuple[str, str, dict]] = []
    seen: set[str] = set()

    for session, turn in open_turns(lines).items():
        age_min = (now - turn["started"]).total_seconds() / 60
        if age_min < STALL_MINUTES:
            continue
        # A turn whose log went silent long ago is an abandoned/crashed process,
        # not a live stall; report it once rather than forever.
        idle_min = (now - turn["last_activity"]).total_seconds() / 60
        key = f"{session}@{turn['started'].isoformat()}"
        seen.add(key)
        record = state.get(key) or {"alerts": 0, "last_alert": None}
        if record["alerts"] >= MAX_ALERTS_PER_TURN:
            continue
        if record["last_alert"]:
            since = (now - dt.datetime.fromisoformat(record["last_alert"])).total_seconds() / 60
            if since < REALERT_MINUTES:
                continue

        if turn["long_tools"] >= POLL_LOOP_REPEATS:
            detail = (
                f"She has made {turn['long_tools']} blocking tool calls of "
                f"{POLL_NEAR_TIMEOUT_S}s or more in this one turn - that is the "
                "babysitting-a-background-job pattern, not real work."
            )
        elif idle_min >= STALL_MINUTES:
            detail = f"Nothing has happened in her log for {idle_min:.0f} min - the turn may be abandoned."
        else:
            detail = "The turn is still active but has produced no reply."

        # The counter is NOT touched here. It used to be incremented for every
        # stalled session found while only problems[:3] were printed, so a 4th
        # concurrent session burned 1, 2, 3 alerts across three passes, hit
        # MAX_ALERTS_PER_TURN and went permanently quiet having never once
        # reached Rachad. The budget is spent below, only on what is said.
        problems.append((
            f"Dado has been on one {turn['platform']} reply for {age_min:.0f} min "
            f"({turn['api_calls']} internal steps) and has sent you nothing. {detail} "
            "Ask her what she is doing, or tell the backend to look.",
            key, record,
        ))

    # Forget turns that have finished, so the state file cannot grow forever.
    for key in [k for k in state if k not in seen]:
        del state[key]

    shown = problems[:MAX_PROBLEMS_PER_TICK]
    held = len(problems) - len(shown)
    for _, key, record in shown:
        record["alerts"] += 1
        record["last_alert"] = now.isoformat(timespec="seconds")
        state[key] = record
    save_state(state)

    if shown:
        lines = [text for text, _, _ in shown]
        if held > 0:
            lines.append(f"({held} more stalled turn(s) not listed; still being tracked.)")
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
