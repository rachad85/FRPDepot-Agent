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


# Receipts a batch tool emits per chunk. They are progress telemetry, not proof
# of a business action, and a single bulk run can emit thousands -- on 2026-07-24
# the Google indexer wrote 3,817 of the day's 4,616 receipts, so the last 60 were
# ALL index batches and the review would have seen no draft, report or audit at
# all. Collapsed to one line each so they cannot crowd out the real receipts.
BATCH_RECEIPT = re.compile(r'"action":\s*"[^"]*(_batch|_index_batch)"')


TS_RE = re.compile(r'"ts":\s*"([^"]+)"')


def receipt_day(line: str, day: str) -> bool:
    """Receipt ts is UTC; the bundle day is LOCAL. Convert, never substring-match.

    A bare `day in line` was wrong twice over (found 2026-07-26): it matched the
    date anywhere, so a receipt was kept only because a FILENAME carried it, and
    every receipt after 20:00 local carries the next UTC date and vanished. 14 of
    that day's 17 receipts were invisible -- including both live-write commits,
    which is exactly what this review checks for.
    """
    m = TS_RE.search(line)
    if not m:
        return day in line
    try:
        return dt.datetime.fromisoformat(m.group(1)).astimezone().strftime("%Y-%m-%d") == day
    except ValueError:
        return day in line


def tail_jsonl(path: Path, day: str, limit: int = 60) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    today = [l for l in lines if receipt_day(l, day)]
    batch = [l for l in today if BATCH_RECEIPT.search(l)]
    real = [l for l in today if not BATCH_RECEIPT.search(l)]
    out = real[-limit:]
    if batch:
        out.append(
            f'(plus {len(batch)} per-batch progress receipts collapsed -- '
            f'telemetry, not business actions; last: {batch[-1][:160]})'
        )
    return out


def whole_file(path: Path, limit_chars: int = 8000) -> str:
    """Keep the HEAD as well as the tail, and say so when anything is cut.

    A bare [-limit_chars:] silently dropped the first 18 lines of
    fit_profile.md once it grew past 8000 chars (found 2026-07-27): legal
    name, what we sell, location, the verified mailbox address and the
    signature rules all vanished mid-word -- exactly the facts this review
    checks "invented company fact" against.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(missing)"
    if len(text) <= limit_chars:
        return text
    head = limit_chars // 2
    tail = limit_chars - head
    omitted = len(text) - limit_chars
    return f"{text[:head]}\n\n(... {omitted} chars omitted from the middle ...)\n\n{text[-tail:]}"


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
