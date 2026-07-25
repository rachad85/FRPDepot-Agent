"""One bounded refresh after Rachad removed every Drive company restriction.

Runs sequentially so SQLite has one writer:
1. Reprocess OCR files whose prior extracted text was discarded.
2. Re-index Drive files formerly represented only by withheld hashes.
3. OCR any newly indexed scans/messages.

Each phase is bounded. The final verification fails honestly if Drive is still
quarantined, has withheld hashes, has OCR candidates, or the Drive sweep stopped
early.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = Path(os.environ["LOCALAPPDATA"]) / "FRPDepot-Google" / "reference" / "google_reference.sqlite"
PYTHON = sys.executable


def run_phase(name: str, script: str, *args: str) -> int:
    command = [PYTHON, str(HERE / script), *args]
    print(json.dumps({"phase": name, "status": "starting", "command_script": script}), flush=True)
    result = subprocess.run(command, cwd=str(HERE))
    print(json.dumps({"phase": name, "status": "finished", "exit_code": result.returncode}), flush=True)
    return result.returncode


def verify() -> dict:
    import google_backfill

    con = sqlite3.connect(DB)
    try:
        kinds = set(google_backfill.EXTRACTORS)
        return {
            "drive_quarantined": con.execute(
                "SELECT count(*) FROM drive_files WHERE tdi_quarantined=1"
            ).fetchone()[0],
            "drive_withheld_hashes": con.execute(
                "SELECT count(*) FROM withheld_hashes WHERE kind='drive'"
            ).fetchone()[0],
            "ocr_candidates": len(google_backfill.candidates(con, kinds)),
            "drive_sweep_status": (con.execute(
                "SELECT value FROM meta WHERE key='drive_sweep_status'"
            ).fetchone() or ["missing"])[0],
            "drive_screening": (con.execute(
                "SELECT value FROM meta WHERE key='drive_screening'"
            ).fetchone() or ["missing"])[0],
        }
    finally:
        con.close()


def main() -> int:
    phases = [
        ("released_ocr", "google_backfill.py", "--max-minutes", "45"),
        ("unrestricted_drive_index", "google_indexer.py", "--mode", "drive", "--max-minutes", "45"),
        ("new_file_ocr", "google_backfill.py", "--max-minutes", "45"),
    ]
    failures = []
    for phase in phases:
        code = run_phase(*phase)
        if code:
            failures.append({"phase": phase[0], "exit_code": code})
            break
    final = verify()
    final["phase_failures"] = failures
    ok = (
        not failures
        and final["drive_quarantined"] == 0
        and final["drive_withheld_hashes"] == 0
        and final["ocr_candidates"] == 0
        and final["drive_screening"] == "none"
        and not str(final["drive_sweep_status"]).startswith("incomplete")
    )
    final["status"] = "complete_verified" if ok else "incomplete"
    print(json.dumps({"unrestricted_drive_refresh": final}, indent=2), flush=True)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
