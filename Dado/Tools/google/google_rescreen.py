"""Maintain the Gmail screen and release obsolete Drive quarantine flags.

Why this exists (2026-07-24): the index was built with a screen that knows only
"dualam" and "tdi", so Troy Dualam material spelling out neither term was stored
- Aze's runtime artifacts, TDI authority documents naming troy_history, a
CRA/mortgage thread about "Troy Dumalac INC" (a misspelling), and 128 files
carrying TDI's Q26-#### quote numbering. That last group is the one that
matters commercially: FRP Depot and Troy Dualam are separate entities that
trade with each other, and pricing an FRP job against TDI's own internal quotes
is an arm's-length problem.

The index cannot be re-screened by re-running the indexer: both of its loops
skip ids already stored, so a widened term list can never revisit them. Hence
this pass, which walks the stored rows directly.

Drive is unrestricted by Rachad's instruction. --apply screens Gmail only.
--release-drive clears every Drive flag/hash and places OCR-quarantined files
back in the extraction queue because their prior text was intentionally not stored.

    python google_rescreen.py              # dry run: report only, changes nothing
    python google_rescreen.py --apply      # set the flags
    python google_rescreen.py --show       # current quarantine summary
    python google_rescreen.py --release-drive  # remove Drive-only restrictions
    python google_rescreen.py --release-all  # clear every flag (fully reversible)
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tdi_filter import deep_tdi_marker

DB_PATH = Path(os.environ["LOCALAPPDATA"]) / "FRPDepot-Google" / "reference" / "google_reference.sqlite"
RECEIPTS = Path(r"C:\FRPDepot\Dado\40_Logs\receipts.jsonl")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def receipt(action: str, evidence: str) -> None:
    try:
        RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
        with RECEIPTS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now(), "action": action, "evidence": evidence}) + "\n")
    except OSError:
        pass


def ensure_columns(con: sqlite3.Connection) -> None:
    """Add the quarantine columns if absent. Additive only - no data is moved."""
    for table in ("gmail_messages", "drive_files"):
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if "tdi_quarantined" not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN tdi_quarantined INTEGER DEFAULT 0")
        if "tdi_marker" not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN tdi_marker TEXT")
    con.commit()


def scan_gmail(con: sqlite3.Connection) -> list[tuple]:
    """Return Gmail hits as (id, marker). Drive is deliberately not scanned."""
    gmail_hits = []
    for rid, subject, sender, recipients, cc, snippet, body, att in con.execute(
        "SELECT id,subject,sender,recipients,cc,snippet,body,attachment_names FROM gmail_messages"
    ):
        marker = deep_tdi_marker(subject, sender, recipients, cc, snippet, body, att)
        if marker:
            gmail_hits.append((rid, marker))

    return gmail_hits


def summarize(hits: list[tuple], label: str) -> None:
    by_marker: dict[str, int] = {}
    for _, marker in hits:
        by_marker[marker] = by_marker.get(marker, 0) + 1
    print(f"  {label}: {len(hits):,} rows")
    for marker, n in sorted(by_marker.items(), key=lambda x: -x[1]):
        print(f"      {n:>5,}  {marker}")


def cmd_show(con: sqlite3.Connection) -> int:
    ensure_columns(con)
    for table in ("gmail_messages", "drive_files"):
        total = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        q = con.execute(f"SELECT count(*) FROM {table} WHERE tdi_quarantined=1").fetchone()[0]
        print(f"  {table}: {q:,} quarantined of {total:,}")
        for marker, n in con.execute(
            f"SELECT tdi_marker,count(*) FROM {table} WHERE tdi_quarantined=1 "
            "GROUP BY tdi_marker ORDER BY count(*) DESC"
        ):
            print(f"      {n:>5,}  {marker}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the flags (default is a dry run)")
    parser.add_argument("--show", action="store_true", help="show the current quarantine state")
    parser.add_argument("--release-drive", action="store_true", help="clear Drive flags/hashes and requeue OCR-quarantined files")
    parser.add_argument("--release-all", action="store_true", help="clear every flag")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"index not found at {DB_PATH}")
        return 1
    con = sqlite3.connect(DB_PATH)
    try:
        if args.show:
            return cmd_show(con)
        if args.release_drive:
            ensure_columns(con)
            flagged = con.execute("SELECT count(*) FROM drive_files WHERE tdi_quarantined=1").fetchone()[0]
            requeued = con.execute("SELECT count(*) FROM drive_files WHERE content_status='backfill_quarantined'").fetchone()[0]
            hashes = con.execute("SELECT count(*) FROM withheld_hashes WHERE kind='drive'").fetchone()[0]
            con.execute("UPDATE drive_files SET content_status='released_drive_reprocess' "
                        "WHERE content_status='backfill_quarantined'")
            con.execute("UPDATE drive_files SET tdi_quarantined=0, tdi_marker=NULL")
            con.execute("DELETE FROM withheld_hashes WHERE kind='drive'")
            con.execute("INSERT OR REPLACE INTO meta VALUES('drive_screening','none')")
            con.commit()
            result = {"status": "drive_unrestricted", "flags_cleared": flagged,
                      "ocr_files_requeued": requeued, "withheld_hashes_deleted": hashes}
            print(json.dumps(result, indent=2))
            receipt("google_drive_restrictions_released", f"{DB_PATH}#{json.dumps(result, sort_keys=True)}")
            return 0
        if args.release_all:
            ensure_columns(con)
            for table in ("gmail_messages", "drive_files"):
                con.execute(f"UPDATE {table} SET tdi_quarantined=0, tdi_marker=NULL")
            con.commit()
            print("all quarantine flags cleared")
            receipt("google_index_quarantine_released", str(DB_PATH))
            return 0

        ensure_columns(con)
        gmail_hits = scan_gmail(con)
        print(f"{'APPLYING' if args.apply else 'DRY RUN - nothing changed'}\n")
        summarize(gmail_hits, "gmail")
        total_rows = (con.execute("SELECT count(*) FROM gmail_messages").fetchone()[0]
                      + con.execute("SELECT count(*) FROM drive_files").fetchone()[0])
        found = len(gmail_hits)
        print(f"\n  {found:,} of {total_rows:,} stored rows "
              f"({found / total_rows * 100:.2f}%) carry a TDI marker.")

        if not args.apply:
            print("\n  Re-run with --apply to set the flags. Nothing is deleted either way.")
            return 0

        con.executemany(
            "UPDATE gmail_messages SET tdi_quarantined=1, tdi_marker=? WHERE id=?",
            [(m, i) for i, m in gmail_hits])

        con.execute("INSERT OR REPLACE INTO meta VALUES('rescreen_last_run',?)", (now(),))
        con.execute("INSERT OR REPLACE INTO meta VALUES('rescreen_marker_version','2026-07-24-deep')")
        con.commit()
        print(f"\n  flagged {found:,} rows. Nothing was deleted; "
              "--release-all reverses this completely.")
        receipt("google_index_rescreened", f"{DB_PATH}#quarantined={found}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
