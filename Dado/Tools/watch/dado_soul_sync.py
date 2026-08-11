"""Keep Dado's LIVE SOUL.md equal to the repo mirror she is reviewed against.

WHY THIS EXISTS. The repo copy `DadoProfile\\SOUL.md` is what gets committed,
reviewed and pushed; the file Dado actually reads is
`%LOCALAPPDATA%\\hermes\\profiles\\dado\\SOUL.md`. Twice on 2026-08-11 a
commissioned rule was written to the mirror and never reached the live profile,
so Dado ran for hours without a rule the record said she had. Nothing detected
it either time - it is invisible to every existing health signal.

WHAT IT WILL AND WILL NOT DO. It only ever copies mirror -> live, and only when
that is purely additive: if the LIVE file holds a line the mirror does not, it
REFUSES and leaves both alone, because that means someone edited the live
profile directly and copying would destroy their work. A refusal is reported,
never silently swallowed - the whole point is that drift stops being invisible.

It writes via a temporary file and os.replace, so an interrupted run can never
leave a truncated HARD RULES file, and it keeps one rolling backup of whatever
it replaced.

Silent on success with nothing to do, so it is safe to call every few minutes
from dado_gateway_watchdog.ps1. Exit code is 0 unless the sync was needed and
could not be completed.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_MIRROR = Path(r"C:\FRPDepot\DadoProfile\SOUL.md")
RECEIPTS = Path(r"C:\FRPDepot\Dado\40_Logs\receipts.jsonl")
BACKUP_NAME = "SOUL.md.bak_autosync"


def live_soul_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise RuntimeError("LOCALAPPDATA is not set; cannot locate the live profile.")
    return Path(base) / "hermes" / "profiles" / "dado" / "SOUL.md"


def normalized_lines(raw: bytes) -> list[str]:
    return raw.decode("utf-8").replace("\r\n", "\n").splitlines()


def lines_only_in_live(live: bytes, mirror: bytes) -> list[str]:
    """Content the live profile would LOSE. Non-empty means: refuse."""
    return [
        row[1:]
        for row in difflib.unified_diff(
            normalized_lines(live), normalized_lines(mirror), lineterm="", n=0
        )
        if row.startswith("-") and not row.startswith("---")
    ]


def append_receipt(action: str, evidence: str) -> None:
    """Into the same log the nightly conduct review already bundles.

    Drift needs to be NOTICED, not merely logged somewhere nobody reads, and a
    receipt gets there without paging Rachad for something that is not an outage.
    """
    try:
        RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "evidence": evidence,
        }
        with RECEIPTS.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        # A receipt failure must never stop the sync or the watchdog.
        pass


def write_live(live_path: Path, mirror_bytes: bytes, live_bytes: bytes) -> None:
    """Replace the live file atomically, preserving its own newline convention."""
    newline = "\r\n" if live_bytes.count(b"\r\n") else "\n"
    text = "\n".join(normalized_lines(mirror_bytes)) + "\n"
    backup = live_path.with_name(BACKUP_NAME)
    shutil.copy2(live_path, backup)
    temporary = live_path.with_name(live_path.name + ".autosync.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline=newline)
        os.replace(temporary, live_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def sync(mirror_path: Path, live_path: Path, *, dry_run: bool) -> int:
    if not mirror_path.exists():
        print(f"SOUL SYNC SKIPPED: no repo mirror at {mirror_path}")
        return 0
    if not live_path.exists():
        print(f"SOUL SYNC SKIPPED: no live profile at {live_path}")
        return 0
    mirror_bytes = mirror_path.read_bytes()
    live_bytes = live_path.read_bytes()
    if mirror_bytes == live_bytes:
        return 0

    lost = lines_only_in_live(live_bytes, mirror_bytes)
    if lost:
        message = (
            f"SOUL SYNC REFUSED: the live profile holds {len(lost)} line(s) the repo "
            "mirror does not, so copying would destroy them. Someone edited the live "
            "SOUL.md directly. Reconcile by hand; nothing was changed."
        )
        print(message)
        append_receipt("dado_soul_sync_refused_live_has_unmirrored_content", message)
        return 1

    added = len(normalized_lines(mirror_bytes)) - len(normalized_lines(live_bytes))
    if dry_run:
        print(f"SOUL SYNC WOULD UPDATE the live profile (+{added} line(s)).")
        return 0
    write_live(live_path, mirror_bytes, live_bytes)
    # Compare CONTENT, not raw bytes: the live file keeps its own newline
    # convention on purpose, so a CRLF profile is byte-different from an LF
    # mirror even on a perfectly correct sync.
    if normalized_lines(live_path.read_bytes()) != normalized_lines(mirror_bytes):
        message = "SOUL SYNC FAILED: the live profile does not match the mirror after writing."
        print(message)
        append_receipt("dado_soul_sync_failed_verification", message)
        return 1
    message = (
        f"live SOUL.md updated from the repo mirror (+{added} line(s)); "
        f"previous copy kept as {BACKUP_NAME}"
    )
    print(f"SOUL SYNC: {message}")
    append_receipt("dado_soul_sync_applied", message)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    args = parser.parse_args(argv)
    try:
        return sync(REPO_MIRROR, live_soul_path(), dry_run=args.dry_run)
    except Exception as exc:  # never take the watchdog down
        print(f"SOUL SYNC ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
