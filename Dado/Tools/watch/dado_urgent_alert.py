"""Reach Rachad when Dado's own machinery is the thing that is broken.

WHY THIS IS NOT `hermes send` (2026-07-25): the alert this exists for is "the
gateway is down and the watchdog could not bring it back". Every normal path to
Rachad - send_clean, the cron `deliver: telegram`, Dado herself - runs through
hermes, and in that scenario hermes is exactly what is suspect. An alerter that
shares a failure mode with the thing it reports on is not an alerter.

So this talks straight to the Telegram Bot API over HTTPS with nothing but the
standard library: no hermes, no gateway, no venv packages, no Dado tooling. The
bot token is read from the profile .env, which is the same secret hermes uses,
and is NEVER printed or logged (Golden Rule 2).

It is deliberately NOT a general-purpose sender. There is no send path in this
tree for mail (Golden Rule 1) and this does not create one - it posts operational
alerts about Dado's own health to Rachad's own chat, nothing else.

Usage:
    python dado_urgent_alert.py --reason gateway_down --message "..."
    python dado_urgent_alert.py --reason gateway_down --message "..." --dry-run
    python dado_urgent_alert.py --self-test        # config only, sends nothing

Exit: 0 sent (or suppressed by cooldown), 1 could not send.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV_FILE = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "profiles" / "dado" / ".env"
STATE_FILE = Path(r"C:\FRPDepot\Dado\40_Logs\urgent_alert_state.json")
LOG_FILE = Path(r"C:\FRPDepot\Dado\40_Logs\urgent_alert.log")
DESKTOP_MARKER = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "DADO_NEEDS_ATTENTION.txt"
# One alert per reason per hour. The watchdog runs every 5 minutes; without this
# a weekend outage would be 200+ identical messages, which is its own way of
# being ignored.
COOLDOWN_MINUTES = 60
TIMEOUT_SECONDS = 20


def log(line: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {line}\n")
    except OSError:
        pass


def read_env() -> dict[str, str]:
    """Parse the profile .env. Values stay in memory and are never logged."""
    if not ENV_FILE.exists():
        return {}
    found: dict[str, str] = {}
    try:
        text = ENV_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z0-9_]+)\s*=\s*(.*)$", line.strip())
        if match:
            found[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return found


def target_chat(env: dict[str, str]) -> str:
    """Rachad's own chat id. TELEGRAM_ALLOWED_USERS is the allowlist hermes uses."""
    allowed = (env.get("TELEGRAM_ALLOWED_USERS") or "").strip()
    first = [p.strip() for p in re.split(r"[,;\s]+", allowed) if p.strip()]
    return first[0] if first else ""


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass


def suppressed(reason: str) -> bool:
    """True if this reason was already alerted inside the cooldown."""
    last = (load_state().get(reason) or {}).get("last_sent")
    if not last:
        return False
    try:
        when = dt.datetime.fromisoformat(last)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.astimezone()
    age = (dt.datetime.now().astimezone() - when).total_seconds() / 60
    return age < COOLDOWN_MINUTES


def record_sent(reason: str) -> None:
    state = load_state()
    state[reason] = {"last_sent": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    save_state(state)


def drop_marker(message: str) -> None:
    """A local trace, for the case where even Telegram cannot be reached.

    Crude on purpose: a file on the Desktop is the one thing that still works
    when the network is the problem.
    """
    try:
        if DESKTOP_MARKER.parent.is_dir():
            stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
            DESKTOP_MARKER.write_text(
                f"Dado needs attention - {stamp}\n\n{message}\n", encoding="utf-8")
    except OSError:
        pass


def clear_marker() -> None:
    try:
        DESKTOP_MARKER.unlink()
    except OSError:
        pass


def send(token: str, chat: str, message: str) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": chat,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode()
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS,
                                    context=ssl.create_default_context()) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return bool(payload.get("ok")), ""
    except urllib.error.HTTPError as exc:
        # NEVER echo the URL - it carries the bot token.
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, type(exc).__name__


def main() -> int:
    parser = argparse.ArgumentParser(description="Out-of-band alert for Dado's own failures")
    parser.add_argument("--reason", default="general", help="Cooldown key, e.g. gateway_down")
    parser.add_argument("--message", help="Text to send")
    parser.add_argument("--dry-run", action="store_true", help="Resolve config, send nothing")
    parser.add_argument("--self-test", action="store_true", help="Report readiness only")
    parser.add_argument("--clear", action="store_true",
                        help="Recovered: clear the desktop marker and the cooldown")
    args = parser.parse_args()

    env = read_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat = target_chat(env)

    if args.clear:
        clear_marker()
        # Scoped to the reason, not a blanket wipe: clearing the gateway alert
        # must not silently reset the cooldown of some unrelated future alert.
        state = load_state()
        state.pop(args.reason, None)
        save_state(state)
        print(json.dumps({"status": "CLEARED", "reason": args.reason}))
        return 0

    if args.self_test or args.dry_run:
        print(json.dumps({
            "env_file_found": ENV_FILE.exists(),
            "token_present": bool(token),
            "token_len": len(token),          # length only, never the value
            "chat_resolved": bool(chat),
            "cooldown_minutes": COOLDOWN_MINUTES,
            "would_send": bool(args.dry_run and args.message),
            "suppressed_now": suppressed(args.reason),
        }, indent=1))
        return 0 if (token and chat) else 1

    if not args.message:
        print("ERROR: --message is required", file=sys.stderr)
        return 1
    if not token or not chat:
        log(f"cannot alert ({args.reason}): token_present={bool(token)} chat={bool(chat)}")
        drop_marker(args.message)
        print(json.dumps({"status": "NO_CONFIG", "marker": str(DESKTOP_MARKER)}))
        return 1

    if suppressed(args.reason):
        log(f"suppressed by cooldown: {args.reason}")
        print(json.dumps({"status": "SUPPRESSED_COOLDOWN", "reason": args.reason}))
        return 0

    ok, detail = send(token, chat, args.message)
    if ok:
        record_sent(args.reason)
        log(f"alert sent: {args.reason}")
        print(json.dumps({"status": "SENT", "reason": args.reason}))
        return 0

    log(f"alert FAILED ({args.reason}): {detail}")
    drop_marker(args.message)
    print(json.dumps({"status": "SEND_FAILED", "detail": detail,
                      "marker": str(DESKTOP_MARKER)}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
