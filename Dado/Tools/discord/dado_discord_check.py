"""Read-only health + identity check for Dado's Discord lane.

WHY THIS EXISTS (2026-08-10). Dado has already run as the WRONG COMPANY'S BOT.
On 2026-08-04 her gateway (profile dado, api_server on 8647) connected to
Discord and came up as **Aze#1753** -- Troy Dualam's bot. Her profile .env had
no DISCORD_BOT_TOKEN at all, so the token was inherited from the process
environment; hermes deliberately does NOT scrub credential keys from os.environ
(hermes_cli/env_loader.py, "_PROFILE_MANAGED_ENV_KEYS" comment). She lost a
token-collision race against Aze's gateway ("Discord bot token already in use
(PID 17152)"), won it on retry 3, and then served Rachad's DMs through TDI's
bot into an FRP Depot session. That is the exact mixing Hard Rule 4 exists to
prevent, and NOTHING flagged it.

So this tool answers the question no log line answers on its own: WHICH BOT IS
SHE? It asks Discord directly (GET /users/@me) and prints the bot's real
identity, then cross-checks the machine-local gateway lock to see whether some
OTHER hermes profile already owns that same token.

THREE TRAPS THIS TOOL IS BUILT AROUND, all verified on this box:

1. gateway_state.json LIES BY OMISSION. Per-platform entries are never cleared.
   Right now dado's file still says discord "connected" with updated_at
   2026-08-04T14:46:47Z on a gateway that started 2026-08-10. Reading `state`
   alone returns a FALSE POSITIVE. Every entry here is therefore compared
   against the gateway's own start_time and reported STALE when it predates it.

2. THE PORT PROVES NOTHING ABOUT CHAT. 8647 is API_SERVER_PORT, not Discord.
   Hermes keeps the gateway up when a chat adapter fails -- dado's own log has
   Discord failing for ~90s on 2026-08-04 while the gateway stayed "healthy".
   A listener on 8647 is not evidence that Discord is alive.

3. THE TOKEN LOCK IS KEYED BY TOKEN, NOT BY PROFILE. It is
   sha256(token)[:16] (gateway/status.py:279-284), machine-local and
   cross-HERMES_HOME. Two profiles holding the same token fight over it. So a
   matching lock owned by another profile is a POSITIVE identification of a
   shared bot, not a guess.

READ-ONLY AND SECRET-SAFE. No writes, no gateway control, no Discord message of
any kind. The token is never printed, never logged and never returned -- only
its lock hash (a one-way digest) and the identity Discord reports for it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROFILE = "dado"
DISCORD_API = "https://discord.com/api/v10"
HTTP_TIMEOUT = 15

# Troy Dualam's agent. Dado is FRP Depot. If her Discord identity resolves to
# this profile's bot, the company wall has been crossed -- see the module
# docstring. This is a DETECTOR for an existing boundary, not a new wall.
SIBLING_PROFILE = "aze"


class CheckError(RuntimeError):
    """A check could not be performed at all (not the same as a failed check)."""


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def hermes_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise CheckError("LOCALAPPDATA is not set; cannot locate the hermes install.")
    return Path(local) / "hermes"


def profile_dir() -> Path:
    return hermes_root() / "profiles" / PROFILE


def lock_dir() -> Path:
    profile_home = os.environ.get("USERPROFILE")
    if not profile_home:
        raise CheckError("USERPROFILE is not set; cannot locate the gateway lock directory.")
    return Path(profile_home) / ".local" / "state" / "hermes" / "gateway-locks"


# --------------------------------------------------------------------------
# .env reading (key -> value, no logging, no echo)
# --------------------------------------------------------------------------

def read_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv file into a dict. Values are NEVER printed by this tool."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CheckError(f"Could not read {path}: {exc}") from exc
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def scope_hash(identity: str) -> str:
    """Mirror gateway/status.py:_scope_hash -- sha256 of the token, first 16 hex."""
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Discord identity
# --------------------------------------------------------------------------

def fetch_bot_identity(token: str) -> dict[str, Any]:
    """GET /users/@me as the bot. This is a READ. It sends no message."""
    request = urllib.request.Request(
        f"{DISCORD_API}/users/@me",
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": "DadoDiscordCheck/1.0 (FRP Depot, read-only)",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise CheckError(
                "Discord rejected the token (HTTP 401 Unauthorized). It is wrong, "
                "revoked, or was reset in the Developer Portal. Re-run "
                "SET_DADO_DISCORD_TOKEN.bat with a freshly copied token."
            ) from exc
        raise CheckError(f"Discord returned HTTP {exc.code} for /users/@me.") from exc
    except urllib.error.URLError as exc:
        raise CheckError(f"Could not reach Discord: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise CheckError("Discord returned an unexpected payload for /users/@me.")
    return payload


# --------------------------------------------------------------------------
# gateway state
# --------------------------------------------------------------------------

def load_gateway_state() -> dict[str, Any] | None:
    path = profile_dir() / "gateway_state.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def gateway_start_epoch(state: dict[str, Any]) -> float | None:
    """start_time is a psutil create_time quantized to CENTISECONDS (see
    gateway/status.py:_get_process_start_time). Divide by 100 for epoch seconds."""
    raw = state.get("start_time")
    if not isinstance(raw, (int, float)):
        return None
    return float(raw) / 100.0


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def platform_entry_is_stale(entry: dict[str, Any], start_epoch: float | None) -> bool:
    """True when the entry was last written BEFORE the running gateway started.

    This is the whole defence against trap 1 in the module docstring.
    """
    if start_epoch is None:
        return False
    updated = parse_iso(entry.get("updated_at"))
    if updated is None:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return updated.timestamp() < start_epoch


def pid_is_alive(pid: int) -> bool:
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

class Report:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.warnings: list[str] = []

    def line(self, text: str = "") -> None:
        print(text)

    def ok(self, text: str) -> None:
        print(f"  [OK]    {text}")

    def warn(self, text: str) -> None:
        print(f"  [WARN]  {text}")
        self.warnings.append(text)

    def fail(self, text: str) -> None:
        print(f"  [FAIL]  {text}")
        self.problems.append(text)

    def info(self, text: str) -> None:
        print(f"          {text}")


def check_token_configured(report: Report, env: dict[str, str]) -> str | None:
    report.line("1. TOKEN IS SET IN DADO'S OWN PROFILE")
    token = env.get("DISCORD_BOT_TOKEN", "").strip()
    inherited = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

    if not token:
        report.fail("DISCORD_BOT_TOKEN is NOT in Dado's profile .env.")
        if inherited:
            report.info(
                "AND a DISCORD_BOT_TOKEN is present in this shell's environment. "
                "That is exactly the 2026-08-04 failure: with nothing in her own "
                ".env, the gateway picks up whatever the environment carries -- "
                "which was Troy Dualam's bot."
            )
        report.info("Fix: run SET_DADO_DISCORD_TOKEN.bat.")
        return None

    report.ok("DISCORD_BOT_TOKEN is set in her profile .env.")
    report.info(
        "Her .env is loaded with override=True (hermes_cli/env_loader.py:488), so "
        "this value beats any token inherited from the environment."
    )
    if inherited and inherited != token:
        report.info(
            "A DIFFERENT DISCORD_BOT_TOKEN also exists in this shell's environment. "
            "Hers wins because of that override -- no action needed."
        )
    return token


def check_allowlist(report: Report, env: dict[str, str]) -> None:
    report.line()
    report.line("2. WHO IS ALLOWED TO TALK TO HER")
    users = env.get("DISCORD_ALLOWED_USERS", "").strip()
    roles = env.get("DISCORD_ALLOWED_ROLES", "").strip()
    allow_all = env.get("DISCORD_ALLOW_ALL_USERS", "").strip().lower()
    gateway_all = env.get("GATEWAY_ALLOW_ALL_USERS", "").strip().lower()

    if allow_all in {"1", "true", "yes"} or gateway_all in {"1", "true", "yes"}:
        report.fail(
            "An ALLOW-ALL flag is set. Any Discord user who can reach the bot would "
            "get full tool access to FRP Depot. Remove it and list user IDs instead."
        )
    elif users:
        ids = [part.strip() for part in users.split(",") if part.strip()]
        report.ok(f"Locked to {len(ids)} Discord user id(s): {', '.join(ids)}")
    elif roles:
        report.ok(f"Locked to Discord role id(s): {roles}")
    else:
        report.fail(
            "No DISCORD_ALLOWED_USERS and no DISCORD_ALLOWED_ROLES. Hermes fails "
            "CLOSED, so the bot will connect and then silently ignore every message."
        )


def check_identity(report: Report, token: str, env: dict[str, str]) -> dict[str, Any] | None:
    report.line()
    report.line("3. WHICH BOT IS SHE, ACTUALLY?  (asks Discord directly)")
    try:
        identity = fetch_bot_identity(token)
    except CheckError as exc:
        report.fail(str(exc))
        return None

    name = identity.get("username") or "(unknown)"
    discriminator = identity.get("discriminator") or ""
    label = f"{name}#{discriminator}" if discriminator and discriminator != "0" else name
    live_id = str(identity.get("id") or "")

    # The identity must be LOAD-BEARING, not a line of prose the reader is
    # expected to audit. SET_DADO_DISCORD_TOKEN.ps1 records the bot id Rachad
    # confirmed at setup; a mismatch here means the token now resolves to a
    # different bot than the one he approved, which is exactly the 2026-08-04
    # failure and must FAIL rather than print ALL CLEAR.
    expected = env.get("DISCORD_EXPECTED_BOT_ID", "").strip()
    if not expected:
        report.warn(
            f"Discord says this token is: {label}  (id {live_id}) -- but no bot "
            "identity was recorded at setup, so this cannot be checked against "
            "anything. Re-run SET_DADO_DISCORD_TOKEN.bat to pin it."
        )
    elif expected != live_id:
        report.fail(
            f"WRONG BOT. This token now resolves to {label} (id {live_id}), but "
            f"the bot you approved at setup was id {expected}. Do not use it "
            "until you know why it changed."
        )
    else:
        report.ok(f"Discord says this token is: {label}   (id {live_id}) -- the bot you approved.")

    if "aze" in str(name).casefold():
        report.fail(
            "That bot is named like Troy Dualam's agent. FRP Depot conversations "
            "must not run through TDI's Discord identity."
        )
    return identity


def check_token_ownership(report: Report, token: str) -> None:
    report.line()
    report.line("4. IS ANOTHER PROFILE ALREADY USING THIS SAME BOT?")
    digest = scope_hash(token)
    path = lock_dir() / f"discord-bot-token-{digest}.lock"
    report.info(f"token lock id: discord-bot-token-{digest}  (one-way hash of the token)")

    if not path.exists():
        report.ok("No other gateway on this PC holds this Discord token.")
        return

    try:
        record = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        report.warn("A lock file exists for this token but could not be read.")
        return

    home = str(record.get("hermes_home") or "")
    owner = Path(home).name if home else "(unknown)"
    pid = record.get("pid")
    alive = isinstance(pid, int) and pid_is_alive(pid)

    if owner == PROFILE:
        if alive:
            report.ok(f"Dado's own gateway holds this token (pid {pid}). That is correct.")
        else:
            report.info(f"A stale lock from Dado's own previous gateway (pid {pid}, not running).")
        return

    if owner == SIBLING_PROFILE:
        report.fail(
            f"THIS IS TROY DUALAM'S BOT. The token is locked by profile '{owner}' "
            f"(pid {pid}, {'running' if alive else 'not running'}). Using it puts FRP "
            "Depot conversations through TDI's Discord identity -- the 2026-08-04 "
            "incident, repeated. Create a SEPARATE Discord application for FRP Depot."
        )
        return

    report.fail(
        f"Another hermes profile ('{owner}', pid {pid}, "
        f"{'running' if alive else 'not running'}) holds this exact token. Two "
        "gateways cannot poll one Discord bot -- they will steal it from each other."
    )


def check_gateway(report: Report) -> None:
    report.line()
    report.line("5. IS THE DISCORD LANE ACTUALLY UP RIGHT NOW?")
    state = load_gateway_state()
    if state is None:
        report.warn("No gateway_state.json yet -- she has not run since the lane was added.")
        return

    pid = state.get("pid")
    alive = isinstance(pid, int) and pid_is_alive(pid)
    if not alive:
        report.warn(f"No gateway is running (last recorded pid {pid}).")
        report.info("Start her with START_DADO.bat, then run this check again.")
        return

    start_epoch = gateway_start_epoch(state)
    platforms = state.get("platforms")
    if not isinstance(platforms, dict):
        report.warn("gateway_state.json has no platform section.")
        return

    entry = platforms.get("discord")
    if not isinstance(entry, dict):
        report.warn("Discord is not listed among her running platforms.")
        report.info("She has not picked up the token yet -- STOP_DADO.bat then START_DADO.bat.")
    else:
        stale = platform_entry_is_stale(entry, start_epoch)
        reported = str(entry.get("state") or "unknown")
        if stale:
            report.fail(
                f"Discord shows '{reported}' but that line is STALE -- it was written "
                f"{entry.get('updated_at')}, BEFORE this gateway started. Hermes never "
                "clears old per-platform entries, so this is a false positive, not proof "
                "she is on Discord. Restart her and re-check."
            )
        elif reported == "connected":
            report.ok(f"Discord is connected (as of {entry.get('updated_at')}).")
        else:
            report.fail(
                f"Discord state is '{reported}': "
                f"{entry.get('error_message') or entry.get('error_code') or 'no detail given'}"
            )

    # The other lane is reported unconditionally. A Discord problem must never
    # hide the state of Telegram -- that is the lane her alerts still go out on.
    telegram = platforms.get("telegram")
    if isinstance(telegram, dict):
        tg_stale = platform_entry_is_stale(telegram, start_epoch)
        tg_state = str(telegram.get("state") or "unknown")
        if tg_state == "connected" and not tg_stale:
            report.ok("Telegram lane is connected.")
        elif tg_stale:
            report.warn("Telegram's status line is stale; cannot confirm that lane.")
        else:
            report.warn(f"Telegram state is '{tg_state}' -- the other lane is not healthy.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only check of Dado's Discord lane: identity, allowlist, "
                    "token ownership and live state. Writes nothing, sends nothing."
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit a machine-readable summary instead of the human report.",
    )
    args = parser.parse_args()

    try:
        env = read_env_file(profile_dir() / ".env")
    except CheckError as exc:
        print(f"CANNOT CHECK: {exc}")
        return 2

    if args.json:
        token = env.get("DISCORD_BOT_TOKEN", "").strip()
        summary: dict[str, Any] = {
            "token_configured": bool(token),
            "allowlist_configured": bool(
                env.get("DISCORD_ALLOWED_USERS", "").strip()
                or env.get("DISCORD_ALLOWED_ROLES", "").strip()
            ),
            "token_lock_id": scope_hash(token) if token else None,
        }
        state = load_gateway_state()
        if state:
            platforms = state.get("platforms")
            entry = platforms.get("discord") if isinstance(platforms, dict) else None
            if isinstance(entry, dict):
                summary["discord_state"] = entry.get("state")
                summary["discord_stale"] = platform_entry_is_stale(
                    entry, gateway_start_epoch(state)
                )
        print(json.dumps(summary, indent=2))
        return 0

    report = Report()
    report.line("===============================================================")
    report.line("  Dado's Discord lane - check   (read-only, sends nothing)")
    report.line("===============================================================")
    report.line()

    token = check_token_configured(report, env)
    check_allowlist(report, env)
    if token:
        check_identity(report, token, env)
        check_token_ownership(report, token)
    check_gateway(report)

    report.line()
    report.line("---------------------------------------------------------------")
    if report.problems:
        report.line(f"NOT READY - {len(report.problems)} problem(s) above must be fixed.")
        return 1
    if report.warnings:
        # Exit 2, not 0. The .bat prints "CHECK PASSED" on a zero exit, and a
        # warning here means something is genuinely unproven -- she is not
        # running, there is no gateway state yet, or the bot identity was never
        # pinned. Reporting that as PASSED would be the same class of false
        # comfort as the stale "connected" line this tool exists to catch.
        report.line(f"NOT PROVEN - {len(report.warnings)} thing(s) could not be confirmed above.")
        return 2
    report.line("ALL CLEAR - the Discord lane is hers, locked to you, and live.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
