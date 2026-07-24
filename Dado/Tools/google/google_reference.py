"""Search Dado's private Google reference cache. Read-only; never contacts Google.

GATED CLOSED 2026-07-24 — HARD RULE 4 (company wall).

The cache was built from Rachad's personal Google account, screened only for
"dualam" and "tdi". Bare "troy" is deliberately NOT a filter term (common name
and city), so Troy Dualam material that never spells out either term passed
straight through. Verified in the live index: 'aze_active_task.json' stored with
its content, 187 Drive rows and 240 Gmail bodies mentioning "aze", 1,168 Gmail
bodies mentioning "troy", 4 rows naming TDI's troy_history database, 120 Drive
names carrying TDI's Q26- quote numbering. A further 2,610 Drive rows were
stored after only their FILENAME was screened, because unreadable, oversize and
failed-extraction files return empty content and skip the content screen while
still being written.

An earlier check here looked for "dualam"/"tdi" in stored rows and found none.
That was circular — those are exactly the terms the filter removes — and it is
why this was first reported clean. It was not.

Serving from this index would put TDI content into FRP Depot answers, logs, and
the nightly GitHub push. So it fails CLOSED until Rachad decides what the cache
should be (see CLAUDE.md). Nothing has been deleted; the data is intact for
whatever he chooses. To re-enable, the index must be rebuilt under a corrected
screen — not patched in place, since the indexer skips ids already stored and
so can never re-screen them.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

DB = Path(os.environ["LOCALAPPDATA"]) / "FRPDepot-Google" / "reference" / "google_reference.sqlite"

# Flip to False only after the index has been rebuilt under a corrected screen.
GATED_PENDING_RACHAD = True
GATE_MESSAGE = (
    "Google reference cache is CLOSED and cannot be searched.\n"
    "Reason: Hard Rule 4 (company wall). It was built from the personal Google\n"
    "account with a screen that misses Troy Dualam material not spelling out\n"
    "'dualam' or 'tdi', and it verifiably holds TDI content. 2,610 more rows were\n"
    "stored with their content never screened at all.\n"
    "Nothing was deleted. Rachad decides whether this cache exists and at what\n"
    "scope; it must then be REBUILT, not patched, because the indexer skips ids\n"
    "already stored and cannot re-screen them.\n"
    "Use the live, per-query, TDI-filtered tool (google_tool.py) meanwhile."
)


def main() -> int:
    if GATED_PENDING_RACHAD:
        print(GATE_MESSAGE, file=sys.stderr)
        return 2
    p = argparse.ArgumentParser()
    p.add_argument("query")
    p.add_argument("--source", choices=["gmail", "drive", "all"], default="all")
    p.add_argument("--limit", type=int, default=20)
    a = p.parse_args()
    limit = min(max(a.limit, 1), 50)
    con = sqlite3.connect(DB)
    out: dict[str, list[dict]] = {}
    if a.source in {"gmail", "all"}:
        rows = con.execute("""SELECT m.id,m.date_header,m.subject,m.sender,m.recipients,
                              snippet(gmail_fts,6,'[',']',' … ',24)
                              FROM gmail_fts JOIN gmail_messages m ON m.id=gmail_fts.id
                              WHERE gmail_fts MATCH ? ORDER BY rank LIMIT ?""", (a.query, limit)).fetchall()
        out["gmail"] = [dict(zip(["id","date","subject","from","to","match"], x)) for x in rows]
    if a.source in {"drive", "all"}:
        rows = con.execute("""SELECT d.id,d.name,d.mime_type,d.modified_time,d.web_view_link,d.content_status,
                              snippet(drive_fts,5,'[',']',' … ',24)
                              FROM drive_fts JOIN drive_files d ON d.id=drive_fts.id
                              WHERE drive_fts MATCH ? ORDER BY rank LIMIT ?""", (a.query, limit)).fetchall()
        out["drive"] = [dict(zip(["id","name","mime_type","modified","link","content_status","match"], x)) for x in rows]
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
