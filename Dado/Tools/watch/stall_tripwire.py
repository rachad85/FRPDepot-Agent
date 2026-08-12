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

# A turn killed by a gateway restart is NOT a stall - it is a corpse, and the
# message that turn was working on is lost with no reply and no record. The
# tripwire could not see it: open_turns() pops a session on TURN_END, a gateway
# death writes no such line, so the dead turn looks OPEN and gets described as
# "still active, ask her what she is doing" - an instruction to interrogate an
# agent with no memory of the request. Then the moment Rachad re-asks (exactly
# what a user does when ignored) the newer turn overwrites it and the evidence
# is gone. Proposed 2026-08-04, built 2026-08-12 after it recurred.
DISABLE_FLAG_PATH = Path(r"C:\FRPDepot\Dado\40_Logs\gateway_disabled.flag")
STOP_LEDGER_PATH = Path(r"C:\FRPDepot\Dado\40_Logs\gateway_stops.log")
ORPHAN_ALERTS = 1            # a lost message is announced once; repeating adds nothing
ORPHAN_LOOKBACK_HOURS = 12   # the tail holds ~a week; older than this is archaeology
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
INBOUND = re.compile(TS + r".*gateway\.run: inbound message: platform=(\S+) user=\S+ chat=(\S+) msg=(.*)")
RESPONSE_READY = re.compile(TS + r".*gateway\.run: response ready: platform=(\S+) chat=(\S+) ")
GATEWAY_START = re.compile(TS + r".*gateway\.run: Starting Hermes Gateway")


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


def gateway_state_path() -> Path:
    """profiles\\dado\\gateway_state.json.

    Derived from log_path() so a test redirects both with one patch, and so this
    can never read another profile's file.
    """
    return log_path().parent.parent / "gateway_state.json"


def gateway_starts(lines: list[str]) -> list[dt.datetime]:
    """Every gateway birth visible in the window, oldest first."""
    found = []
    for line in lines:
        match = GATEWAY_START.search(line)
        if match:
            found.append(parse_ts(match.group(1)))
    return found


def current_gateway_start(lines: list[str]) -> dt.datetime | None:
    """When the RUNNING gateway life began, or None if it cannot be established.

    Prefers gateway_state.json's start_time - psutil create_time in
    CENTISECONDS, the same field and the same /100 that dado_lane_health reads.
    It survives a rotation that carried the start line out of the tail, and it
    is the EARLIER of the two sources, which is the direction that cannot
    manufacture a false alarm: a later boundary would reclassify still-in-flight
    messages as orphans.

    None is a legitimate answer. No boundary established means the whole check
    is inert; it never guesses.
    """
    try:
        raw = json.loads(gateway_state_path().read_text(encoding="utf-8", errors="replace"))
        value = raw.get("start_time")
        if isinstance(value, (int, float)) and value > 0:
            when = dt.datetime.fromtimestamp(float(value) / 100.0).replace(microsecond=0)
            # A start in the future means the field is not what we think it is
            # (wrong unit, clock skew). Believing it would silence the open-turn
            # check for every turn on the box, so refuse rather than degrade.
            if when <= dt.datetime.now():
                return when
    except (OSError, json.JSONDecodeError, AttributeError, ValueError, OverflowError):
        pass
    starts = gateway_starts(lines)
    return starts[-1] if starts else None


def deliberate_stops() -> list[dt.datetime]:
    """Stamps written by STOP_DADO.bat / START_DADO.bat.

    The disable flag alone cannot answer "did he stop her on purpose?":
    START_DADO DELETES it, and the cron does not tick while she is stopped, so
    by the first run that could notice a killed turn the evidence is gone.
    STOP_DADO also ends in Stop-Process -Force, so a deliberate stop produces
    the same unclean exit as a crash - clean-vs-unclean is not a discriminator.
    """
    try:
        text = STOP_LEDGER_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    stamps = []
    for line in text.splitlines():
        head = line.strip().split(" ", 1)[0]
        try:
            stamps.append(dt.datetime.fromisoformat(head).replace(tzinfo=None))
        except ValueError:
            continue  # a comment or a hand-written line is not an error
    return stamps


def orphaned_messages(lines: list[str], started: dt.datetime | None) -> list[dict]:
    """Messages that arrived, were never answered, and whose gateway then died."""
    if started is None:
        return []
    life = 0
    waiting: dict[tuple[str, str], list[tuple[dt.datetime, str, int]]] = {}
    for line in lines:
        if GATEWAY_START.search(line):
            life += 1
            continue
        inbound = INBOUND.search(line)
        if inbound:
            lane = (inbound.group(2), inbound.group(3))
            text = inbound.group(4).split(" reply_to_id=")[0].strip().strip("'\"")
            waiting.setdefault(lane, []).append((parse_ts(inbound.group(1)), text, life))
            continue
        ready = RESPONSE_READY.search(line)
        if ready:
            lane = (ready.group(2), ready.group(3))
            # A reply clears only messages from the SAME gateway life. A
            # lane-wide reset looks equivalent and is not: a later answer to a
            # RE-ASKED question would erase the evidence of the lost one before
            # the next 15-minute tick. Within a life the reset IS lane-wide,
            # which is what keeps the pairing immune to drift - most messages
            # are folded into a turn already running and produce no response
            # line of their own, so FIFO pairing would accumulate error until it
            # named the wrong message.
            waiting[lane] = [item for item in waiting.get(lane, []) if item[2] != life]
    starts = gateway_starts(lines)

    orphans: list[dict] = []
    for (platform, chat), queue in waiting.items():
        lost = [item for item in queue if item[0] < started]
        if not lost:
            continue
        stamp, text, _ = lost[0]
        died = next((s for s in starts if s > stamp), started)
        orphans.append({"platform": platform, "chat": chat, "received": stamp,
                        "text": text, "count": len(lost), "died": died})
    orphans.sort(key=lambda item: item["received"])
    return orphans


def orphan_key(orphan: dict) -> str:
    return f"orphan:{orphan['platform']}:{orphan['chat']}@{orphan['received'].isoformat()}"


def orphan_message(orphan: dict) -> str:
    # A real inbound can carry no text at all - the live log holds
    # `msg=''` for an attachment, voice note or sticker. Quoting "" back at him
    # is worse than useless, so name what it was instead of showing an empty
    # quotation. Found by running this against the real log, not in review.
    text = orphan["text"]
    if text:
        excerpt = text[:70] + ("..." if len(text) > 70 else "")
        described = f'- "{excerpt}" -'
    else:
        described = "- an attachment or voice note, with no text -"
    extra = ""
    if orphan["count"] > 1:
        extra = f" The {orphan['count'] - 1} message(s) you sent after it went the same way."
    return (
        f"Your {orphan['platform']} message from "
        f"{orphan['received'].strftime('%Y-%m-%d %H:%M')} {described} never got a reply "
        f"and never will: her gateway restarted at {orphan['died'].strftime('%H:%M')} and the turn "
        f"working on it died with it.{extra} Nothing was sent to you and nothing was saved. "
        "Re-send it if you still need it."
    )


def main() -> int:
    # A deliberate stop wins over everything, exactly as dado_lane_health does.
    if DISABLE_FLAG_PATH.exists():
        return 0
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
    started = current_gateway_start(lines)
    stops = deliberate_stops()
    problems: list[tuple[str, str, dict]] = []
    orphan_problems: list[tuple[str, str, dict]] = []
    seen: set[str] = set()

    # A message that died with its gateway outranks a slow one: the slow turn may
    # still answer him, the dead one cannot.
    for orphan in orphaned_messages(lines, started):
        if (now - orphan["died"]).total_seconds() > ORPHAN_LOOKBACK_HOURS * 3600:
            continue
        # Silenced iff a stop stamp falls in [received, died] - which can only
        # happen if the stop occurred during the very life that was handling the
        # message. A stop from any other episode is strictly outside.
        if any(orphan["received"] <= stop <= orphan["died"] for stop in stops):
            continue  # he stopped her himself; reporting it is crying wolf
        key = orphan_key(orphan)
        seen.add(key)
        record = state.get(key) or {"alerts": 0, "last_alert": None}
        if record["alerts"] >= ORPHAN_ALERTS:
            continue
        orphan_problems.append((orphan_message(orphan), key, record))

    for session, turn in open_turns(lines).items():
        if started is not None and turn["started"] < started:
            # Not a stall - a corpse. This turn belongs to a gateway life that no
            # longer exists, so "she is still working, ask her what she is doing"
            # would be false. The orphan check above owns what he actually lost;
            # a killed cron or local turn is not his to chase.
            continue
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

    # An orphan outranks a stall: the slow turn may still answer him.
    problems = orphan_problems + problems

    # --dry-run is read off sys.argv rather than argparse ON PURPOSE: cron
    # invokes this as exactly [python, script], and an argument parser that
    # could reject an unexpected argv is a way for the watcher to die of its
    # own options.
    dry_run = "--dry-run" in sys.argv[1:]

    # The forget-finished-turns cleanup above is HOUSEKEEPING, not an alert, so
    # it persists either way - otherwise the state file grows forever whenever a
    # send fails. A rehearsal persists nothing at all.
    if not dry_run:
        save_state(state)

    shown = problems[:MAX_PROBLEMS_PER_TICK]
    held = len(problems) - len(shown)
    if shown:
        lines = [text for text, _, _ in shown]
        if held > 0:
            lines.append(f"({held} more stalled turn(s) not listed; still being tracked.)")
        message = "\n".join(lines)
        # The print stays: it is the durable record in the cron output file.
        print(message)

        # BACKLOG B-08. The alert budget (MAX_ALERTS_PER_TURN) and the re-alert
        # clock (REALERT_MINUTES) used to be spent HERE, before the print and
        # long before hermes attempted delivery - so a dropped message consumed
        # an alert Rachad never saw, and the 60-minute clock then suppressed the
        # retry. Both are now spent only on a CONFIRMED send.
        #
        # queue_on_failure=False: this alert is re-derived from the live turn
        # state every 15 minutes, so an unspent budget IS the queue.
        if dry_run:
            print("(rehearsal - nothing sent, no alert budget spent.)")
        elif _deliver(message):
            for _, key, record in shown:
                record["alerts"] += 1
                record["last_alert"] = now.isoformat(timespec="seconds")
                state[key] = record
            save_state(state)
        else:
            print("(delivery not confirmed - no alert budget was spent, so the "
                  "next tick will report this again.)")
    return 0


def _test_run_suppressed() -> bool:
    """Is this process a test run? Delegates to the canonical fuse.

    The canonical implementation is `dado_inbox_reasoner.is_test_run` - ONE
    copy, because four hand-copied guards is exactly how the unittest half of
    this protection went missing in the first place.

    FALLS BACK to the old env probe if that import fails, and the direction is
    deliberate: a broken sibling import must never SILENCE a real crash alert.
    Failing this way can only lose the unittest half of the guard, never the
    watcher's ability to tell Rachad it died.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from dado_inbox_reasoner import is_test_run
        return bool(is_test_run())
    except Exception:  # noqa: BLE001
        return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _deliver(message: str) -> bool:
    """Send an alert and report whether Telegram actually took it."""
    # *** NEVER SEND FROM A TEST RUN. *** Added 2026-08-12 after wiring delivery
    # into this path caused a plain `pytest` run of the existing suite to put 14
    # real messages on Rachad's phone. The suite exercises the announce path, so
    # any future test that touches it would do the same. Reported as delivered
    # so the caller's persist-on-confirmed-send logic is still exercised; a test
    # that wants to cover a FAILED send patches send_clean directly.
    if _test_run_suppressed():
        print("(test run - delivery simulated, nothing sent.)")
        return True
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from dado_inbox_reasoner import send_clean
    except Exception as exc:  # noqa: BLE001
        print(f"(could not load the sender - {type(exc).__name__}: {exc}; "
              "no alert budget spent.)")
        return False
    try:
        return bool(send_clean(message, queue_on_failure=False))
    except Exception as exc:  # noqa: BLE001
        print(f"(send raised - {type(exc).__name__}: {exc}; no budget spent.)")
        return False


if __name__ == "__main__":
    # B-08 consequence: this job now runs with cron `deliver: local`, so hermes
    # delivers nothing for it - including the "Script exited with code 1"
    # summary that used to reach Rachad on a crash. The tripwire exists to
    # notice silence; it must not become silent itself. Reports its own crash
    # out of band, through the alerter rather than through cron.
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as _exc:  # noqa: BLE001
        import subprocess
        import traceback

        _detail = traceback.format_exc(limit=4)
        print(f"STALL TRIPWIRE FAILED: {type(_exc).__name__}: {_exc}")
        try:
            if _test_run_suppressed():
                raise RuntimeError("test run - alerter suppressed")
            subprocess.run(
                [sys.executable,
                 str(Path(__file__).resolve().parent / "dado_urgent_alert.py"),
                 "--reason", "stall_tripwire_crashed",
                 "--message",
                 "Dado's stall tripwire crashed. Nothing is watching for a turn "
                 "that goes quiet.\n\n"
                 f"{type(_exc).__name__}: {_exc}\n\n{_detail[-600:]}"],
                capture_output=True, text=True, timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass  # the print above still lands in the cron output file
        sys.exit(1)
