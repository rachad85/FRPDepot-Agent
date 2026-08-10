#!/usr/bin/env python
"""Connect/check Dado's dedicated local FRP Depot WordPress UI session.

The browser profile lives outside the repository. This helper never reads,
prints, exports, or copies passwords, cookies, tokens, local storage, account
names, or page content. It performs no WordPress or business write.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ADMIN_URL = "https://frpdepots.com/wp-admin/"
ALLOWED_HOST = "frpdepots.com"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
SESSION_ROOT = LOCALAPPDATA / "FRPDepot-WordPress-UI"
PROFILE_DIR = SESSION_ROOT / "edge-profile"
STATUS_FILE = SESSION_ROOT / "connection_status.json"
CDP_ENDPOINT = "http://127.0.0.1:9229"


class SessionError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def output(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def launch_args() -> list[str]:
    return [
        "--start-maximized",
        "--no-first-run",
        "--disable-sync",
        "--disable-save-password-bubble",
        "--disable-features=PasswordManagerOnboarding,PasswordLeakDetection",
    ]


def ensure_prerequisites() -> None:
    if not EDGE.is_file():
        raise SessionError(f"Microsoft Edge was not found at the approved path: {EDGE}")
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def authenticated_admin_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == ALLOWED_HOST
        and (parsed.path == "/wp-admin" or parsed.path.startswith("/wp-admin/"))
        and not parsed.path.startswith("/wp-login")
    )


def save_connected_status(url: str) -> None:
    STATUS_FILE.write_text(
        json.dumps(
            {
                "status": "CONNECTED",
                "connected_utc": utc_now(),
                "site": "https://frpdepots.com",
                "authenticated_admin": True,
                "profile": str(PROFILE_DIR),
                "page_url": url,
                "loopback_cdp": CDP_ENDPOINT,
                "business_write_performed": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def command_connect(_args: argparse.Namespace) -> int:
    ensure_prerequisites()
    STATUS_FILE.unlink(missing_ok=True)
    print("A dedicated Microsoft Edge window will open for FRP Depot WordPress.")
    print("Sign in there directly. Never paste a password or code into Telegram.")
    print("KEEP THIS WINDOW OPEN while Dado uses approved WordPress UI access.")
    sys.stdout.flush()
    command = [
        str(EDGE),
        f"--user-data-dir={PROFILE_DIR}",
        "--profile-directory=Default",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=9229",
        "--remote-allow-origins=http://127.0.0.1:9229",
        *launch_args(),
        ADMIN_URL,
    ]
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


def command_live_check(_args: argparse.Namespace) -> int:
    ensure_prerequisites()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=10_000)
            pages = [page for context in browser.contexts for page in context.pages]
            matching = [page.url for page in pages if authenticated_admin_url(page.url)]
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        raise SessionError(f"WordPress live UI session check failed: {exc}") from exc
    connected = bool(matching)
    if connected:
        save_connected_status(matching[0])
    output(
        {
            "status": "CONNECTED" if connected else "NOT_CONNECTED",
            "site": "https://frpdepots.com",
            "authenticated_admin": connected,
            "live_loopback_session": True,
            "business_write_performed": False,
        }
    )
    return 0 if connected else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FRP Depot dedicated WordPress UI session")
    commands = parser.add_subparsers(dest="command", required=True)
    connect = commands.add_parser("connect")
    connect.set_defaults(func=command_connect)
    live_check = commands.add_parser("live-check")
    live_check.set_defaults(func=command_live_check)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except (SessionError, OSError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
