"""Notice when one of Dado's CHAT LANES dies while her gateway stays up.

WHY THIS EXISTS (2026-08-10, added with the Discord lane). The keep-alive
watchdog treats "a TCP listener on 127.0.0.1:8647" as the single liveness
signal. That port is API_SERVER_PORT -- the HTTP api_server platform -- and it
has nothing to do with chat. Hermes deliberately keeps the gateway running when
a chat adapter fails to connect, which is the right call for the gateway and a
blind spot for the operator: dado's own log shows Discord failing for ~90 s on
2026-08-04 while the gateway, and therefore the watchdog, reported perfect
health throughout.

That is the 2026-07-25 shape exactly -- Dado unreachable for thirteen hours
while every other health signal looked fine. One lane was survivable because
there was only one; with two, a silently dead lane is worse, because the
surviving one keeps answering and makes everything look normal.

So this checks the thing that actually matters: for every chat platform that is
ENABLED right now, is it CONNECTED right now?

THE TRAP THIS IS BUILT AROUND. gateway_state.json never clears per-platform
entries. At the time of writing, dado's file still claimed discord "connected"
from 2026-08-04 on a gateway started 2026-08-10, and whatsapp "fatal" from a
configuration she no longer has. Reading `state` alone gives BOTH a false
positive and a false alarm. Every entry is therefore judged against the running
gateway's own start_time, and any entry written before the gateway started is
treated as absent rather than as truth.

SILENT WHEN HEALTHY, and it never restarts anything. Restarting a gateway kills
in-flight agent turns, so a dead adapter raises an out-of-band alert and leaves
the decision to Rachad.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROFILE = "dado"
ROOT = Path(r"C:\FRPDepot")
DISABLE_FLAG = ROOT / "Dado" / "40_Logs" / "gateway_disabled.flag"
ALERTER = ROOT / "Dado" / "Tools" / "watch" / "dado_urgent_alert.py"
STATE_FILE = ROOT / "Dado" / "40_Logs" / "lane_health_state.json"
VENV_PYTHON = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe"

# A lane must look down on TWO consecutive checks before Rachad is paged.
# Hermes reconnects on its own (Discord retries at +30s), so a single sample
# catching a normal reconnect would page him for something already fixing
# itself. Two samples at the watchdog's 5-minute cadence means a real outage is
# reported within ~10 minutes and a blip is never reported at all.
CONSECUTIVE_SAMPLES_BEFORE_ALERTING = 2

# A lane is only expected to be up if its credential is actually configured.
# Keyed by the env var whose presence is what enables the platform in hermes
# (gateway/config.py: `if getenv("<X>_BOT_TOKEN"): _enable_from_env(...)`).
CHAT_LANES: dict[str, str] = {
    "telegram": "TELEGRAM_BOT_TOKEN",
    "discord": "DISCORD_BOT_TOKEN",
}

# A gateway needs a moment to bring adapters up. Below this age, a missing
# entry means "still starting", not "broken".
STARTUP_GRACE_SECONDS = 180

# ---------------------------------------------------------------------------
# LANE 3: THE MODEL PROVIDER.
#
# A connected adapter proves the pipe. It proves nothing about what is at the
# far end. On 2026-08-10 telegram and discord were both "connected" for
# thirteen minutes while every turn died on a provider error; assess() returned
# "healthy" the whole way through, correctly and uselessly.
#
# *** DO NOT REBUILD THIS ON gateway.log "response ready". *** The gateway
# counts an error stub as a completed turn, so that signal shows healthy turns
# in a row during exactly this failure. The only honest record is the terminal
# error the conversation loop writes to the PROFILE's own errors.log.
#
# PER-PROFILE, deliberately: profiles\dado\logs\errors.log, NOT the shared
# %LOCALAPPDATA%\hermes\logs\errors.log. The shared file carries the other
# profile's failures too (it held 18 of this marker at the time of writing,
# none of them Dado's), and paging Rachad about a neighbour's outage from
# Dado's bot would be both wrong and unactionable.
PROVIDER_LOG_NAME = "errors.log"
PROVIDER_MARKER = "Non-retryable client error"
PROVIDER_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")

# Measured baseline over 2026-07-22 -> 2026-08-11 (21 days): this marker appears
# in her errors.log EXACTLY 5 times, all within 13:36:58 - 13:50:18 on
# 2026-08-10, i.e. the one real outage. Two inside twenty minutes has never
# happened for any other reason, so the threshold is evidence, not a guess.
PROVIDER_WINDOW_MINUTES = 20
PROVIDER_FAILURES_BEFORE_DOWN = 2

# ONE sample, unlike the chat lanes. The two-sample rule exists there because a
# single bad sample is usually hermes reconnecting on its own. There is no
# equivalent here: by the time this fires, two turns have ALREADY died in
# Rachad's chat and he has already been handed two stubs.
#
# Only the tail is read - this log reached 345 KB in three weeks and is never
# rotated by us.
MAX_PROVIDER_LOG_BYTES = 2_000_000


def profile_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA is not set.")
    return Path(local) / "hermes" / "profiles" / PROFILE


def read_env_keys(path: Path) -> set[str]:
    """Names of keys assigned in a dotenv file. Values are never read."""
    keys: set[str] = set()
    if not path.exists():
        return keys
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return keys
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, _, value = line.partition("=")
        if value.strip():
            keys.add(key.strip())
    return keys


def load_state() -> dict[str, Any] | None:
    path = profile_dir() / "gateway_state.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def pid_is_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    except Exception:
        return True


def start_epoch(state: dict[str, Any]) -> float | None:
    """start_time is psutil create_time quantized to centiseconds."""
    raw = state.get("start_time")
    if not isinstance(raw, (int, float)):
        return None
    return float(raw) / 100.0


def entry_epoch(entry: dict[str, Any]) -> float | None:
    raw = entry.get("updated_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def assess() -> dict[str, Any]:
    """Return a verdict without sending anything. The whole judgement lives here."""
    state = load_state()
    if state is None:
        return {"verdict": "unknown", "detail": "no gateway_state.json", "down": []}

    if not pid_is_alive(state.get("pid")):
        # The port watchdog owns this case; do not double-alert on it.
        return {"verdict": "gateway_down", "detail": "gateway process is not running", "down": []}

    started = start_epoch(state)
    now = datetime.now(timezone.utc).timestamp()
    if started is not None and (now - started) < STARTUP_GRACE_SECONDS:
        return {"verdict": "starting", "detail": "within startup grace", "down": []}

    env_path = profile_dir() / ".env"
    configured = read_env_keys(env_path)
    platforms = state.get("platforms")
    platforms = platforms if isinstance(platforms, dict) else {}

    # A credential added AFTER this gateway started cannot possibly be live yet
    # — the gateway reads .env at startup. The documented setup order creates
    # exactly this window: SET_DADO_DISCORD_TOKEN.bat writes the token (step 5)
    # and the restart is step 7, so without this the 5-minute watchdog would
    # page Rachad "DISCORD LANE IS DOWN" for a lane he is midway through
    # setting up. That is a promise-breaking false alarm, so it is suppressed
    # by evidence rather than by a timer.
    try:
        env_changed_at = env_path.stat().st_mtime
    except OSError:
        env_changed_at = None
    awaiting_restart = (
        started is not None
        and env_changed_at is not None
        and env_changed_at > started
    )

    down: list[dict[str, Any]] = []
    healthy: list[str] = []
    pending: list[str] = []

    for lane, env_key in CHAT_LANES.items():
        if env_key not in configured:
            continue  # not enabled for her; nothing is expected of it

        entry = platforms.get(lane)
        moment = entry_epoch(entry) if isinstance(entry, dict) else None
        stale = (
            started is not None and moment is not None and moment < started
        )
        never_reported = not isinstance(entry, dict) or stale

        if never_reported and awaiting_restart:
            # Configured after this gateway started: expected, not broken.
            pending.append(lane)
            continue

        if not isinstance(entry, dict):
            down.append({"lane": lane, "state": "missing",
                         "detail": "enabled but the gateway never reported it"})
            continue

        if stale:
            # Stale line from an earlier gateway. Treat as absent, never as truth.
            down.append({"lane": lane, "state": "missing",
                         "detail": "enabled but this gateway never reported it "
                                   f"(last line is stale, from {entry.get('updated_at')})"})
            continue

        reported = str(entry.get("state") or "unknown")
        if reported == "connected":
            healthy.append(lane)
        else:
            down.append({
                "lane": lane,
                "state": reported,
                "detail": entry.get("error_message") or entry.get("error_code") or "no detail given",
            })

    # LANE 3 - the model provider. Checked even when every chat lane is
    # connected, because that is exactly the shape of the 2026-08-10 outage.
    provider_hits = provider_failures()
    if len(provider_hits) >= PROVIDER_FAILURES_BEFORE_DOWN:
        down.append({
            "lane": "model provider",
            "state": "failing",
            "detail": f"{len(provider_hits)} non-retryable provider error(s) in the "
                      f"last {PROVIDER_WINDOW_MINUTES} min "
                      f"(latest {provider_hits[-1]}) - her chat lanes are connected "
                      f"but turns are dying at the model",
        })
    # Deliberately NOT added to `healthy` when it is fine. `healthy` is the
    # "Still working: ..." line, which is about the CHAT lanes Rachad can
    # actually switch to; a healthy provider there is noise. It also keeps the
    # existing contract exactly - four tests assert the chat-only list.

    if down:
        return {"verdict": "lane_down", "down": down, "healthy": healthy, "pending": pending}
    if pending:
        return {"verdict": "awaiting_restart", "down": [], "healthy": healthy, "pending": pending}
    return {"verdict": "healthy", "down": [], "healthy": healthy, "pending": []}


def provider_failures(now: datetime | None = None) -> list[str]:
    """Timestamps of non-retryable provider errors inside the recent window.

    Reads only the tail of the PROFILE's own errors.log. Unparseable or
    undated lines are ignored rather than guessed at: a false provider alarm
    would send Rachad chasing a working model.
    """
    path = profile_dir() / "logs" / PROVIDER_LOG_NAME
    try:
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            if size > MAX_PROVIDER_LOG_BYTES:
                handle.seek(size - MAX_PROVIDER_LOG_BYTES)
                handle.readline()          # discard the partial line
            candidates = [line for line in handle if PROVIDER_MARKER in line]
    except OSError:
        return []

    moment = now or datetime.now()
    cutoff = moment - timedelta(minutes=PROVIDER_WINDOW_MINUTES)
    recent = []
    for line in candidates:
        match = PROVIDER_TIMESTAMP.match(line)
        if not match:
            continue
        try:
            stamp = datetime.fromisoformat(match.group(1).replace(" ", "T"))
        except ValueError:
            continue
        if cutoff <= stamp <= moment:
            recent.append(match.group(1))
    return recent


def alert_message(down: list[dict[str, Any]], healthy: list[str]) -> str:
    names = ", ".join(item["lane"].upper() for item in down)
    parts = [
        f"DADO'S {names} LANE IS DOWN while her gateway is still running.",
        "",
        "Her gateway looks healthy to the keep-alive because port 8647 is the API "
        "server, not chat -- so nothing else would have told you.",
        "",
    ]
    for item in down:
        parts.append(f"  {item['lane']}: {item['state']} - {item['detail']}")
    if healthy:
        parts.append("")
        parts.append(f"Still working: {', '.join(name.upper() for name in healthy)}.")
    parts.append("")

    # The advice must match the failure. A restart cannot fix a token that
    # another gateway already holds, and recommending one anyway would send
    # Rachad round a loop -- while a restart DOES end whatever turn is running
    # on the surviving lane, which he should know before clicking.
    provider_down = [item for item in down if item["lane"] == "model provider"]
    if provider_down:
        parts.append("The model provider is the problem, not a chat lane. A restart "
                     "will NOT fix a provider outage or an expired credential, and it "
                     "would end whatever she is working on. Check the provider status "
                     "and her Codex sign-in first; the detail line above names the "
                     "error the turns actually died on.")
        return "\n".join(parts)

    fatal = [item for item in down if item["state"] not in ("missing", "disconnected")]
    if fatal:
        parts.append("Do NOT just restart her -- this looks like a configuration "
                     "problem, not a crash. Run CHECK_DADO_DISCORD.bat first: it "
                     "asks Discord which bot the token belongs to and whether "
                     "another agent on this PC is already using it.")
    else:
        parts.append("To fix: double-click STOP_DADO.bat then START_DADO.bat, then "
                     "run CHECK_DADO_DISCORD.bat. Note a restart ends anything she "
                     "is working on right now in the lane that still works.")
    return "\n".join(parts)


def load_watch_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_watch_state(value: dict[str, Any]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass  # bookkeeping only; never fail the check over it


def send_alert(message: str, reason: str) -> None:
    if not ALERTER.exists() or not VENV_PYTHON.exists():
        print(json.dumps({"status": "ALERTER_MISSING", "reason": reason}))
        return
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), str(ALERTER), "--reason", reason, "--message", message],
            capture_output=True, text=True, timeout=120,
        )
        print((result.stdout or result.stderr or "").strip())
    except Exception as exc:  # noqa: BLE001 - an alerter failure must not crash the watchdog
        print(json.dumps({"status": "ALERTER_FAILED", "error": str(exc)[:500]}))


def clear_alert(reason: str) -> None:
    if not ALERTER.exists() or not VENV_PYTHON.exists():
        return
    try:
        subprocess.run(
            [str(VENV_PYTHON), str(ALERTER), "--clear", "--reason", reason],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Alert when an enabled chat lane is down while the gateway is up. "
                    "Silent when healthy. Never restarts anything."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the verdict and send nothing.")
    args = parser.parse_args(argv)

    # A deliberate stop wins over everything, exactly as the port watchdog does.
    if DISABLE_FLAG.exists():
        return 0

    verdict = assess()

    if args.dry_run:
        print(json.dumps(verdict, indent=2))
        return 0

    state = load_watch_state()
    previously_alerted = bool(state.get("alerted"))
    streak = int(state.get("consecutive_down") or 0)

    if verdict["verdict"] != "lane_down":
        # Recovered (or never broken). Retire the alert ONLY if we actually
        # raised one: dado_urgent_alert --clear also deletes the shared
        # last-resort Desktop marker, which is written for ANY reason. Calling
        # it every five minutes would quietly erase the marker left by a real
        # gateway-down alert that Rachad had not read yet.
        if verdict["verdict"] == "healthy" and previously_alerted:
            clear_alert("chat_lane_down")
        save_watch_state({"verdict": verdict["verdict"], "consecutive_down": 0, "alerted": False})
        return 0

    streak += 1
    if streak < CONSECUTIVE_SAMPLES_BEFORE_ALERTING:
        # One bad sample is usually hermes reconnecting on its own. Record it
        # and say nothing; the next run decides.
        save_watch_state({"verdict": verdict["verdict"], "consecutive_down": streak,
                    "alerted": previously_alerted})
        return 0

    send_alert(alert_message(verdict["down"], verdict.get("healthy", [])), "chat_lane_down")
    save_watch_state({"verdict": verdict["verdict"], "consecutive_down": streak, "alerted": True})
    return 1


if __name__ == "__main__":
    sys.exit(main())
