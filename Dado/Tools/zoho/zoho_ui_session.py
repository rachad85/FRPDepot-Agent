#!/usr/bin/env python
"""Connect/check the dedicated local FRP Depot Zoho UI session.

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
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

APP_URLS = {
    "inventory": "https://inventory.zohocloud.ca/app",
    "books": "https://books.zohocloud.ca/app",
}
APP_HOSTS = {
    "inventory": {"inventory.zoho.com", "inventory.zohocloud.ca"},
    "books": {"books.zoho.com", "books.zohocloud.ca"},
}
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
SESSION_ROOT = LOCALAPPDATA / "FRPDepot-Zoho-UI"
PROFILE_DIR = SESSION_ROOT / "edge-profile"
STATUS_FILE = SESSION_ROOT / "connection_status.json"
CONNECT_TIMEOUT_SECONDS = 600
CDP_ENDPOINT = "http://127.0.0.1:9228"


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


def authenticated_app_url(url: str, service: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname in APP_HOSTS[service]
        and (parsed.path == "/app" or parsed.path.startswith("/app/"))
    )


def verify_services(context: Any) -> dict[str, bool]:
    """Verify both apps through fresh navigations without reading page content."""
    results: dict[str, bool] = {}
    probe = context.new_page()
    try:
        for service, requested in APP_URLS.items():
            probe.goto(requested, wait_until="domcontentloaded", timeout=60_000)
            probe.wait_for_timeout(5_000)
            results[service] = authenticated_app_url(probe.url, service)
    finally:
        probe.close()
    return results


def save_connected_status(results: dict[str, bool]) -> None:
    STATUS_FILE.write_text(
        json.dumps(
            {
                "status": "CONNECTED",
                "connected_utc": utc_now(),
                "services": results,
                "profile": str(PROFILE_DIR),
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
    print("A dedicated live Microsoft Edge window will open for Zoho Inventory and Books.")
    print("Sign in there. Never paste a password or code into Telegram.")
    print("KEEP THIS WINDOW OPEN while Dado uses the authenticated UI session.")
    sys.stdout.flush()
    command = [
        str(EDGE),
        f"--user-data-dir={PROFILE_DIR}",
        "--profile-directory=Default",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=9228",
        "--remote-allow-origins=http://127.0.0.1:9228",
        *launch_args(),
        *APP_URLS.values(),
    ]
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


def command_live_check(_args: argparse.Namespace) -> int:
    ensure_prerequisites()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=10_000)
            pages = [page for context in browser.contexts for page in context.pages]
            results = {
                service: any(authenticated_app_url(page.url, service) for page in pages)
                for service in APP_URLS
            }
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        raise SessionError(f"Zoho live UI session check failed: {exc}") from exc
    connected = all(results.values())
    if connected:
        save_connected_status(results)
    output(
        {
            "status": "CONNECTED" if connected else "NOT_CONNECTED",
            "services": results,
            "live_loopback_session": True,
            "business_write_performed": False,
        }
    )
    return 0 if connected else 1


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
                results = verify_services(context)
            finally:
                context.close()
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        raise SessionError(f"Zoho UI session check failed: {exc}") from exc
    connected = all(results.values())
    output(
        {
            "status": "CONNECTED" if connected else "NOT_CONNECTED",
            "services": results,
            "business_write_performed": False,
        }
    )
    return 0 if connected else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FRP Depot dedicated Zoho UI session")
    commands = parser.add_subparsers(dest="command", required=True)
    connect = commands.add_parser("connect")
    connect.set_defaults(func=command_connect)
    live_check = commands.add_parser("live-check")
    live_check.set_defaults(func=command_live_check)
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
