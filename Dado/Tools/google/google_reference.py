"""Search Dado's private Google reference cache. Read-only; never contacts Google.

OPEN, with TDI rows quarantined at query time (2026-07-24).

History worth keeping: the cache was built with a screen that knows only
"dualam" and "tdi", so Troy Dualam material spelling out neither term was
stored. An early check here searched the stored rows for those same two terms,
found none, and reported the wall clean. That was CIRCULAR — they are exactly
the terms the filter removes, so the query could only ever return zero. Probing
for markers the filter does NOT screen found real TDI content: Aze's runtime
artifacts, TDI authority documents naming troy_history, a CRA/mortgage thread
about "Troy Dumalac INC" (a misspelling of Dualam), and 128 files carrying
TDI's Q26-#### quote numbering.

google_rescreen.py now flags those rows (144 of 70,733 — 0.20%) and every query
below excludes them. Nothing was deleted: the server and the data are Rachad's,
and the goal is to stop TDI material reaching an FRP Depot answer, not to
destroy his own files. `google_rescreen.py --release-all` reverses it entirely.

Bare "troy" is deliberately NOT a marker: 368 of its hits are parcel deliveries
to a person named Troy and only 5 are the company, so blocking it would wall off
Rachad's own mail for nothing.

REMAINING LIMIT, stated honestly: 2,610 Drive rows hold no content because the
file was an unreadable type, oversize, or failed to extract — so only their
name, owner and description were ever screened. A scanned TDI document with a
neutral filename would not be caught. Those rows expose no content through this
tool (there is none to expose), but treat a Drive HIT as a pointer to verify,
never as cleared.
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


def rescreen_ok(con: sqlite3.Connection) -> bool:
    """Serve only from an index that has actually been re-screened.

    Fails closed: if the quarantine columns or the marker-version stamp are
    missing, this is an index built under the old narrow screen and must not be
    queried. That is what makes the gate self-enforcing rather than a promise.
    """
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(drive_files)")}
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
    if not rescreen_ok(con):
        print("Google reference cache refused: this index has not been re-screened for\n"
              "TDI content (Hard Rule 4). Run google_rescreen.py --apply first.",
              file=sys.stderr)
        return 2
    out: dict[str, list[dict]] = {}
    withheld = 0
    # tdi_quarantined=0 is applied in SQL, so a flagged row cannot reach the
    # result set at all -- not even its snippet.
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
                              WHERE drive_fts MATCH ? AND d.tdi_quarantined=0
                              ORDER BY rank LIMIT ?""", (a.query, limit)).fetchall()
        out["drive"] = [dict(zip(["id","name","mime_type","modified","link","content_status","match"], x)) for x in rows]
        withheld += con.execute("""SELECT count(*) FROM drive_fts
                                   JOIN drive_files d ON d.id=drive_fts.id
                                   WHERE drive_fts MATCH ? AND d.tdi_quarantined=1""",
                                (a.query,)).fetchone()[0]
    if withheld:
        out["withheld_tdi"] = withheld
        out["note"] = (f"{withheld} matching row(s) withheld as Troy Dualam content "
                       "(company wall). Ask Rachad if you need them.")
    out["limit_drive_metadata_only"] = (
        "Drive rows whose file could not be read hold no content and were screened "
        "on name/owner only - treat a Drive hit as a pointer to verify, not as cleared.")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
