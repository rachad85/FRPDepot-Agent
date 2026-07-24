"""Search Dado's private Google reference cache. Read-only; never contacts Google."""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
from pathlib import Path

DB = Path(os.environ["LOCALAPPDATA"]) / "FRPDepot-Google" / "reference" / "google_reference.sqlite"


def main() -> int:
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
