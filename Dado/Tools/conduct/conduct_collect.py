"""Collect one day of Dado behavior evidence into a single review bundle.

Deterministic and read-only. Called by conduct_review.py (the nightly
judgment layer) but runnable by hand any time:
    python conduct_collect.py [YYYY-MM-DD]   (default: yesterday, local)
Writes C:\\FRPDepot\\Dado\\40_Logs\\conduct\\<date>.md and prints the path.

Lighter than Aze's collector on purpose (Rachad, 2026-07-22: "much
quieter and easier"). The "## Auto-flags" section replaces Aze's
15-minute tripwire cron: the same circling/error-loop detection, computed
once nightly instead of pinging all day.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
from collections import Counter
from pathlib import Path

OUT_DIR = Path(r"C:\FRPDepot\Dado\40_Logs\conduct")
MEM = Path(r"C:\FRPDepot\Dado\30_Memory")
LOGS40 = Path(r"C:\FRPDepot\Dado\40_Logs")

TURN_RE = re.compile(
    r"response ready: platform=(\S+) chat=(\S+) time=([\d.]+)s api_calls=(\d+)"
)
SLOW_SECONDS = 600   # >10 min on one reply = circling for Dado's workload
MANY_CALLS = 30
ERROR_REPEATS = 3


def profile_logs() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "profiles" / "dado" / "logs"


def day_lines(path: Path, day: str, limit: int = 3000) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [l for l in lines if l.startswith(day)][-limit:]


def tail_jsonl(path: Path, day: str, limit: int = 60) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [l for l in lines if day in l][-limit:]


def whole_file(path: Path, limit_chars: int = 8000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit_chars:]
    except OSError:
        return "(missing)"


def auto_flags(turns: list[str], errors: list[str]) -> list[str]:
    flags: list[str] = []
    for line in turns:
        match = TURN_RE.search(line)
        if not match:
            continue
        seconds, calls = float(match.group(3)), int(match.group(4))
        if seconds > SLOW_SECONDS or calls >= MANY_CALLS:
            flags.append(f"CIRCLING TURN ({seconds/60:.0f} min / {calls} steps): {line.strip()}")
    counts = Counter(
        re.sub(r"^\S+ \S+ ", "", l).strip() for l in errors if l.strip()
    )
    for message, count in counts.items():
        if count >= ERROR_REPEATS:
            flags.append(f"REPEATED ERROR x{count}: {message[:160]}")
    return flags


def main() -> int:
    if len(sys.argv) > 1:
        day = sys.argv[1]
    else:
        day = (dt.date.today() - dt.timedelta(days=1)).isoformat()

    gw = day_lines(profile_logs() / "gateway.log", day)
    inbound = [l for l in gw if "inbound message:" in l]
    turns = [l for l in gw if "response ready:" in l]
    errors = day_lines(profile_logs() / "errors.log", day, limit=200)
    flags = auto_flags(turns, errors)

    sections = [
        f"# Dado conduct bundle -- {day}",
        "## Auto-flags (deterministic circling/error-loop detection)",
        "\n".join(flags) or "(none)",
        "## Turn statistics (every gateway turn: duration + internal steps)",
        "\n".join(turns) or "(no turns)",
        "## Inbound messages (what Rachad sent her)",
        "\n".join(inbound) or "(none)",
        "## Errors that day",
        "\n".join(errors) or "(none)",
        "## Receipts recorded (receipts.jsonl entries for the day)",
        "\n".join(tail_jsonl(LOGS40 / "receipts.jsonl", day)) or "(none)",
        "## Fit profile (company facts she is allowed to state)",
        whole_file(MEM / "fit_profile.md"),
    ]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{day}.md"
    out.write_text("\n\n".join(sections), encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
