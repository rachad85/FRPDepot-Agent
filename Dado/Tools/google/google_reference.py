"""Search Dado's private Google reference cache; never contacts Google.

Gmail remains screened. Drive is unrestricted by Rachad's explicit instruction
and no Drive result is withheld by company marker. Files with no recoverable
text remain metadata-only and therefore cannot produce content snippets.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

DB = Path(os.environ["LOCALAPPDATA"]) / "FRPDepot-Google" / "reference" / "google_reference.sqlite"

REQUIRED_MARKER_VERSION = "2026-07-24-deep"


def gmail_screen_ok(con: sqlite3.Connection) -> bool:
    """Keep the Gmail screen self-enforcing without gating unrestricted Drive."""
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(gmail_messages)")}
        if "tdi_quarantined" not in cols:
            return False
        row = con.execute("SELECT value FROM meta WHERE key='rescreen_marker_version'").fetchone()
        return bool(row) and row[0] == REQUIRED_MARKER_VERSION
    except sqlite3.Error:
        return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("query")
    p.add_argument("--source", choices=["gmail", "drive", "all"], default="all")
    p.add_argument("--limit", type=int, default=20)
    a = p.parse_args()
    limit = min(max(a.limit, 1), 50)
    con = sqlite3.connect(DB)
    if not gmail_screen_ok(con):
        print("Google reference cache refused: Gmail has not been re-screened.\n"
              "Run google_rescreen.py --apply first.",
              file=sys.stderr)
        return 2
    out: dict[str, list[dict]] = {}
    withheld = 0
    # Gmail remains screened. Drive is deliberately unrestricted.
    if a.source in {"gmail", "all"}:
        rows = con.execute("""SELECT m.id,m.date_header,m.subject,m.sender,m.recipients,
                              snippet(gmail_fts,6,'[',']',' … ',24)
                              FROM gmail_fts JOIN gmail_messages m ON m.id=gmail_fts.id
                              WHERE gmail_fts MATCH ? AND m.tdi_quarantined=0
                              ORDER BY rank LIMIT ?""", (a.query, limit)).fetchall()
        out["gmail"] = [dict(zip(["id","date","subject","from","to","match"], x)) for x in rows]
        withheld += con.execute("""SELECT count(*) FROM gmail_fts
                                   JOIN gmail_messages m ON m.id=gmail_fts.id
                                   WHERE gmail_fts MATCH ? AND m.tdi_quarantined=1""",
                                (a.query,)).fetchone()[0]
    if a.source in {"drive", "all"}:
        rows = con.execute("""SELECT d.id,d.name,d.mime_type,d.modified_time,d.web_view_link,d.content_status,
                              snippet(drive_fts,5,'[',']',' … ',24)
                              FROM drive_fts JOIN drive_files d ON d.id=drive_fts.id
                              WHERE drive_fts MATCH ?
                              ORDER BY rank LIMIT ?""", (a.query, limit)).fetchall()
        out["drive"] = [dict(zip(["id","name","mime_type","modified","link","content_status","match"], x)) for x in rows]
    if withheld:
        out["withheld_tdi"] = withheld
        out["note"] = f"{withheld} matching Gmail row(s) withheld. Drive is unrestricted."
    out["limit_drive_metadata_only"] = (
        "Drive rows whose file could not be read hold no content; treat a metadata-only "
        "Drive hit as a pointer to inspect the original file.")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
