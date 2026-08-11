"""Keep the authenticated Zoho UI browser session alive.

WHY THIS EXISTS (2026-08-08). `zoho-ui-live` is an Edge session started by hand
on 2026-08-07 with remote debugging on 127.0.0.1:9228. Eight scripts under
Dado\\20_Working attach to it with playwright's connect_over_cdp INSTEAD OF
LOGGING IN AGAIN -- including check_sct_payment_live_status_ui.py, which is how
Dado answers questions about payment status. Nothing restarted it. A reboot, an
Edge update, or someone closing the window would have taken all eight tools
down silently, and the first sign would have been a script failing mid-task.

This is the same shape as dado_gateway_watchdog.ps1 and follows its rules:
silent when healthy, deliberate stops respected, and it alerts Rachad through
dado_urgent_alert.py when it cannot fix the problem itself.

TWO FAILURES, NOT ONE, and they need opposite responses:

  BROWSER GONE      -> restart it. The login lives in the on-disk Edge profile,
                       so a fresh launch normally comes back authenticated.
  BROWSER UP, ZOHO
  SESSION EXPIRED   -> DO NOT restart. Relaunching cannot fix an expired Zoho
                       cookie and would just churn. This needs Rachad to sign
                       in once, so say that plainly and stop.

Restarting is deliberately done THROUGH job_runner --service, so the new
session is tracked like the old one and is exempt from the timeout and the
stall alert (a healthy session is quiet for days by design).

Cron:
    hermes -p dado cron create "*/10 * * * *" --name dado-zoho-session-watch \\
        --no-agent --script zoho_session_keepalive.py --deliver telegram:891365639
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(r"C:\FRPDepot")
LOGS = ROOT / "Dado" / "40_Logs"
DISABLE_FLAG = LOGS / "zoho_session_disabled.flag"
STATE_FILE = LOGS / "zoho_session_keepalive_state.json"
LOG_FILE = LOGS / "zoho_session_keepalive.log"
JOB_RUNNER = ROOT / "Dado" / "Tools" / "watch" / "job_runner.py"
ALERTER = ROOT / "Dado" / "Tools" / "watch" / "dado_urgent_alert.py"

CDP_PORT = 9228
CDP = f"http://127.0.0.1:{CDP_PORT}"
STARTUP_WAIT_SECONDS = 45

# The canonical session command, verbatim from the 2026-08-07 job record that
# established it. Changing the port or the user-data-dir breaks every attached
# script, so this is the one place it is written down.
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
SESSION_COMMAND = [
    EDGE,
    "--user-data-dir=C:/Users/TDI-service/AppData/Local/FRPDepot-Zoho-UI/edge-profile",
    "--profile-directory=Default",
    "--remote-debugging-address=127.0.0.1",
    f"--remote-debugging-port={CDP_PORT}",
    f"--remote-allow-origins=http://127.0.0.1:{CDP_PORT}",
    "--no-first-run",
    "--disable-sync",
    "--disable-save-password-bubble",
    # The CANADIAN data centre. These must match zoho_ui_session.SESSIONS and
    # zoho_banking_reconciliation_tool.BOOKS_UI_ORIGIN, which keep only pages
    # under https://books.zohocloud.ca/app. Until 2026-08-11 these two lines
    # named the US hosts, so every automatic restart handed back a session on
    # the wrong data centre: the keepalive then saw a sign-in page, logged
    # "signed out; not restarting" and waited for a human. That is what took
    # the Zoho UI down 2026-08-10 23:30 -> 08-11 09:40 and killed the 08:15
    # daily banking review with "Exactly one authenticated Canadian Zoho Books
    # app page must be open".
    "https://inventory.zohocloud.ca/app",
    "https://books.zohocloud.ca/app",
]

# A page sitting on any of these means the browser is up but Zoho has signed us
# out. Restarting cannot fix that.
SIGNED_OUT_MARKERS = ("/signin", "accounts.zoho", "/login")


def log(message: str) -> None:
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {message}\n")
    except OSError:
        pass


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(state: dict) -> None:
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass


def cdp(path: str, timeout: int = 6):
    """One CDP query. Returns parsed JSON, or None if the endpoint is not there."""
    try:
        with urllib.request.urlopen(f"{CDP}{path}", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def session_pages() -> list[str]:
    targets = cdp("/json/list") or []
    return [t.get("url") or "" for t in targets if t.get("type") == "page"]


def alive() -> bool:
    return cdp("/json/version") is not None


def signed_out(pages: list[str]) -> bool:
    """True only when we can see pages AND every one is a sign-in page.

    Deliberately not 'any page is a sign-in page': one stray login tab beside
    working app tabs is not an outage, and treating it as one would page
    Rachad for nothing.
    """
    if not pages:
        return False
    return all(any(m in url.lower() for m in SIGNED_OUT_MARKERS) for url in pages)


def alert(reason: str, message: str) -> None:
    if not ALERTER.exists():
        log(f"ALERTER MISSING, could not report: {message}")
        return
    try:
        subprocess.run(
            [sys.executable, str(ALERTER), "--reason", reason, "--message", message],
            capture_output=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        log(f"alerter failed ({type(exc).__name__}: {exc}) for: {message}")


def restart() -> bool:
    """Relaunch through job_runner so the session stays a tracked service job."""
    try:
        subprocess.run(
            [sys.executable, str(JOB_RUNNER), "start",
             "--name", "zoho-ui-live", "--service",
             "--note", "Authenticated Zoho CDP session on 9228; restarted by "
                       "zoho_session_keepalive. Do not kill - eight scripts attach to it.",
             "--", *SESSION_COMMAND],
            capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        log(f"restart call failed: {type(exc).__name__}: {exc}")
        return False
    deadline = time.time() + STARTUP_WAIT_SECONDS
    while time.time() < deadline:
        time.sleep(3)
        if alive():
            return True
    return False


def main() -> int:
    if DISABLE_FLAG.exists():
        # A deliberate stop is not a fault. Say nothing and change nothing.
        return 0

    state = read_state()
    pages = session_pages() if alive() else None

    if pages is not None and not signed_out(pages):
        if state.get("down") or state.get("signed_out"):
            log("session healthy again")
            print("Zoho UI session is back up on 9228.")
        write_state({"down": False, "signed_out": False})
        return 0

    if pages is not None and signed_out(pages):
        # Up but logged out. Restarting cannot fix an expired Zoho cookie.
        if not state.get("signed_out"):
            log("session is signed out; not restarting")
            message = (
                "Dado's Zoho browser session is running but SIGNED OUT, so the "
                "UI tools (payment status, inventory checks) cannot read "
                "anything. Restarting will not fix it - it needs you to sign "
                "in once in that Edge window. Nothing was changed."
            )
            alert("zoho_session_signed_out", message)
            print(message)
        write_state({"down": False, "signed_out": True})
        return 0

    # Endpoint not answering at all -> the browser is gone. This one we fix.
    log("session down; restarting")
    if restart():
        log("restart succeeded")
        print("Dado's Zoho UI session had stopped; it was restarted and is "
              "answering on 9228 again.")
        write_state({"down": False, "signed_out": False})
        return 0

    if not state.get("down"):
        message = (
            "Dado's Zoho browser session (CDP 9228) is down and I could not "
            "restart it. The eight Zoho UI tools cannot run until it is back - "
            f"that includes payment-status checks. Log: {LOG_FILE}"
        )
        alert("zoho_session_down", message)
        print(message)
    log("restart FAILED")
    write_state({"down": True, "signed_out": False})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
