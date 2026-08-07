#!/usr/bin/env python
"""Connect/check a dedicated local Zoho Inventory UI session.

The browser profile lives outside the repository. This helper never reads,
prints, exports, or copies passwords, cookies, tokens, local storage, account
names, or page content. It performs no business write.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

APP_URL = "https://inventory.zoho.com/app"
APP_PREFIX = "https://inventory.zoho.com/app"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
SESSION_ROOT = LOCALAPPDATA / "FRPDepot-Zoho-UI"
PROFILE_DIR = SESSION_ROOT / "edge-profile"
STATUS_FILE = SESSION_ROOT / "connection_status.json"
CONNECT_TIMEOUT_SECONDS = 600


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


def command_connect(_args: argparse.Namespace) -> int:
    ensure_prerequisites()
    print("A dedicated Microsoft Edge window will open.")
    print("Sign in to Zoho Inventory there. Never paste a password or code into Telegram.")
    print("Do not save the password when Edge asks. The window will close after Inventory loads.")
    sys.stdout.flush()
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                executable_path=str(EDGE),
                headless=False,
                no_viewport=True,
                args=launch_args(),
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(APP_URL, wait_until="domcontentloaded", timeout=60_000)
                deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
                while time.monotonic() < deadline:
                    if page.url.startswith(APP_PREFIX):
                        page.wait_for_timeout(3000)
                        STATUS_FILE.write_text(json.dumps({
                            "status": "CONNECTED",
                            "connected_utc": utc_now(),
                            "origin": "https://inventory.zoho.com",
                            "profile": str(PROFILE_DIR),
                            "business_write_performed": False,
                        }, indent=2) + "\n", encoding="utf-8")
                        output({
                            "status": "CONNECTED",
                            "session_location": str(PROFILE_DIR),
                            "business_write_performed": False,
                        })
                        return 0
                    page.wait_for_timeout(1000)
                raise SessionError("Zoho Inventory did not finish signing in within 10 minutes.")
            finally:
                context.close()
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        raise SessionError(f"Zoho UI connection failed: {exc}") from exc


def command_check(_args: argparse.Namespace) -> int:
    ensure_prerequisites()
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                executable_path=str(EDGE),
                headless=True,
                args=launch_args(),
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(APP_URL, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(3000)
                connected = page.url.startswith(APP_PREFIX)
            finally:
                context.close()
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        raise SessionError(f"Zoho UI session check failed: {exc}") from exc
    output({
        "status": "CONNECTED" if connected else "NOT_CONNECTED",
        "business_write_performed": False,
    })
    return 0 if connected else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FRP Depot dedicated Zoho Inventory UI session")
    commands = parser.add_subparsers(dest="command", required=True)
    connect = commands.add_parser("connect")
    connect.set_defaults(func=command_connect)
    check = commands.add_parser("check")
    check.set_defaults(func=command_check)
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
