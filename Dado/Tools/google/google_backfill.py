"""Read the Drive files the indexer could not extract normally.

Drive is unrestricted by Rachad's explicit instruction. Extracted text is
indexed without company-marker screening. Gmail policy is not handled here.

WHAT IT ADDS, all offline - no installer runs on this box (SRP blocks them):
  images (1,142)  -> OCR via rapidocr_onnxruntime (ONNX, bundled in vendor)
  .msg    (126)   -> extract_msg (Outlook OLE)
  .eml    (127)   -> stdlib email parser
  scanned PDFs    -> PyMuPDF page render, then OCR
  legacy .xls (9) -> xlrd
  zip     (101)   -> member NAMES only (cheap, and names are often revealing)


    python google_backfill.py --dry-run           what would be attempted
    python google_backfill.py --max-minutes 45    do the work, bounded + resumable
    python google_backfill.py --types image,msg   one class at a time

Long runs go through job_runner (SOUL: never wait on a job inside your turn):
    python ..\\watch\\job_runner.py start --name drive-backfill -- \\
        <venv python> google_backfill.py --max-minutes 45
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# Vendor goes at the END: it carries its own cryptography/numpy copies, and the
# hermes venv pins cryptography==46.0.7. Shadowing that would break the gateway.
VENDOR = Path(r"C:\FRPDepot\Dado\Tools\vendor")
if str(VENDOR) not in sys.path:
    sys.path.append(str(VENDOR))

import google_auth

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

DB_PATH = Path(os.environ["LOCALAPPDATA"]) / "FRPDepot-Google" / "reference" / "google_reference.sqlite"
RECEIPTS = Path(r"C:\FRPDepot\Dado\40_Logs\receipts.jsonl")
MAX_TEXT = 500_000
MAX_BYTES = 30 * 1024 * 1024
OCR_MAX_PDF_PAGES = 12

_OCR = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def receipt(action: str, evidence: str) -> None:
    try:
        RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
        with RECEIPTS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now(), "action": action, "evidence": evidence}) + "\n")
    except OSError:
        pass


def ocr_engine():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


def ocr_image_bytes(raw: bytes) -> str:
    import numpy as np
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:
        raise ValueError(f"unreadable image: {type(exc).__name__}") from exc
    if img.mode != "RGB":
        img = img.convert("RGB")
    # Very small images carry no recoverable text; very large ones cost time for
    # nothing, so cap the long edge.
    if max(img.size) > 2600:
        scale = 2600 / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    result, _ = ocr_engine()(np.array(img))
    return "\n".join(line[1] for line in (result or []))


class Extraction(NamedTuple):
    """What we got, and whether it is the WHOLE artifact.

    `complete` exists because a partial read used to be indistinguishable from a
    full one: every extractor returned a plain string, so a 30-page scan whose
    first 12 pages were OCR'd, a workbook truncated at MAX_TEXT, and a zip we
    only listed the names of were all written with the same read-and-clear
    status and left the unscreened pool for good. Callers must record partials
    differently so they read as "a pointer to verify", not "read and cleared".
    """
    text: str
    complete: bool


def text_from_eml(raw: bytes) -> Extraction:
    from email import policy
    from email.parser import BytesParser
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    parts = [f"Subject: {msg.get('subject', '')}", f"From: {msg.get('from', '')}",
             f"To: {msg.get('to', '')}", f"Cc: {msg.get('cc', '')}",
             f"Date: {msg.get('date', '')}"]
    body = ""
    complete = True
    try:
        best = msg.get_body(preferencelist=("plain", "html"))
        if best is None:
            # A message whose top level is not text - e.g. multipart/related
            # carrying only an attachment. There IS a body we did not read.
            complete = False
        else:
            body = best.get_content()
            if best.get_content_type() == "text/html":
                body = re.sub(r"(?s)<[^>]+>", " ", body)
    except Exception:
        # An unknown charset or a broken content-transfer-encoding. This used to
        # be `body = ""`, which made a FAILED body read look identical to an
        # empty one - and since the header block is always non-empty, the row was
        # still written as read-and-clear with its body never seen.
        complete = False
    names = [p.get_filename() for p in msg.walk() if p.get_filename()]
    if names:
        parts.append("Attachments: " + " | ".join(names))
    return Extraction("\n".join(parts + ["", body]), complete)


def text_from_msg(raw: bytes) -> Extraction:
    import extract_msg
    # A UNIQUE temp name per item. The old name was per-PID, so every .msg in a
    # run reused one path: once a file was left undeletable (below), the next
    # write_bytes() onto that still-open handle raised PermissionError and every
    # remaining .msg in the run failed with it - 122 files queued behind one.
    fd, tmp_name = tempfile.mkstemp(prefix="_dado_backfill_", suffix=".msg")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(raw)
        # closing() so the handle is released even when an attribute access below
        # raises - a malformed OLE stream, a missing property, a bad unicode body.
        # m.close() used to sit on the success path only, so on any parse error
        # the handle stayed open, tmp.unlink() then failed (Windows will not
        # delete a file with an open handle), `except OSError: pass` swallowed
        # that, and the raw bytes of a Drive file were left in %TEMP% for good.
        with contextlib.closing(extract_msg.Message(str(tmp))) as m:
            parts = [f"Subject: {m.subject or ''}", f"From: {m.sender or ''}",
                     f"To: {m.to or ''}", f"Cc: {m.cc or ''}", f"Date: {m.date or ''}"]
            names = [a.longFilename or a.shortFilename or "" for a in (m.attachments or [])]
            if any(names):
                parts.append("Attachments: " + " | ".join(n for n in names if n))
            parts += ["", m.body or ""]
        # The body IS fully read here. Attachment CONTENT is names-only for this
        # whole tool, which the module docstring states up front.
        return Extraction("\n".join(parts), True)
    finally:
        try:
            tmp.unlink()
        except OSError as exc:
            # A temp file that will not delete is exactly the case worth hearing
            # about: it means a handle is still open and a copy of someone's
            # Drive file is sitting in %TEMP%. Never swallow it silently again.
            print(json.dumps({"phase": "warn", "temp_unlink_failed": str(tmp),
                              "error": f"{type(exc).__name__}: {exc}"}), flush=True)


def text_from_scanned_pdf(raw: bytes) -> Extraction:
    """Render pages and OCR them - for PDFs that carry no embedded text layer."""
    import fitz  # PyMuPDF, in the venv
    out = []
    complete = True
    with fitz.open(stream=raw, filetype="pdf") as doc:
        if len(doc) > OCR_MAX_PDF_PAGES:
            # Pages past the cap are never looked at by this or any later pass,
            # so a 30-page scan whose first 12 pages are a title sheet and
            # general notes must not be recorded as fully read.
            complete = False
        for page in doc[:OCR_MAX_PDF_PAGES]:
            pix = page.get_pixmap(dpi=200)
            out.append(ocr_image_bytes(pix.tobytes("png")))
            if sum(len(x) for x in out) > MAX_TEXT:
                complete = False
                break
    return Extraction("\n".join(out), complete)


def text_from_xls(raw: bytes) -> Extraction:
    import xlrd
    book = xlrd.open_workbook(file_contents=raw)
    out = []
    complete = True
    for sheet in book.sheets():
        out.append(f"[Sheet: {sheet.name}]")
        if sheet.nrows > 4000:
            complete = False   # a TDI tab past the row cap would never be seen
        for r in range(min(sheet.nrows, 4000)):
            row = "\t".join(str(c.value) for c in sheet.row(r))
            if row.strip():
                out.append(row)
            if sum(len(x) for x in out) > MAX_TEXT:
                return Extraction("\n".join(out), False)
    return Extraction("\n".join(out), complete)


def text_from_zip(raw: bytes) -> Extraction:
    """Member names only. Cheap, bounded, and a filename list is often enough to
    reveal whose documents these are."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = "\n".join(i.filename for i in zf.infolist()[:2000])
    # NEVER complete: member content is not read, only the listing.
    return Extraction("ZIP contents:\n" + names, False)


def classify(mime: str, name: str) -> str:
    mime = (mime or "").lower()
    lower = (name or "").lower()
    if mime.startswith("image/") and "dwg" not in mime and "svg" not in mime:
        return "image"
    if mime == "application/vnd.ms-outlook" or lower.endswith(".msg"):
        return "msg"
    if mime == "message/rfc822" or lower.endswith(".eml"):
        return "eml"
    if mime == "application/pdf":
        return "pdf"
    if mime == "application/vnd.ms-excel" or lower.endswith(".xls"):
        return "xls"
    if "zip" in mime or lower.endswith(".zip"):
        return "zip"
    return "skip"


EXTRACTORS = {
    "image": lambda raw: Extraction(ocr_image_bytes(raw), True),
    "msg": text_from_msg,
    "eml": text_from_eml,
    "pdf": text_from_scanned_pdf,
    "xls": text_from_xls,
    "zip": text_from_zip,
}


def download(drive, file_id: str) -> bytes:
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, drive.files().get_media(fileId=file_id),
                                     chunksize=4 * 1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
        if fh.tell() > MAX_BYTES:
            raise ValueError("exceeds download limit")
    return fh.getvalue()


def candidates(con: sqlite3.Connection, kinds: set[str]) -> list[tuple]:
    """Rows that hold no content, whatever status they were given.

    This used to select on `content_status NOT LIKE 'indexed%'`, but the defect
    being fixed is "the row has no content" and the two diverge for exactly the
    headline case. An image-only PDF has no text layer, so the indexer's
    parse_pdf returns "" for every page, raises nothing, and the row is stored
    content="" with status 'indexed' - which `NOT LIKE 'indexed%'` then excluded
    FOREVER. The live plan showed it: pdf was 7 of 1,484 candidates while
    scanned-PDF OCR is the tool's headline capability. Same shape for any
    docx/xlsx/pptx/text that extracted to an empty string.

    Selecting on content presence also survives status-string drift. The
    `backfill_%` exclusion stays so a run still converges (a row this tool has
    already attempted is not retried, including the deliberate no-text and
    partial outcomes).
    """
    rows = con.execute("""
        SELECT id, name, mime_type, size FROM drive_files
        WHERE (content IS NULL OR trim(content) = '')
          AND content_status NOT LIKE 'backfill_%'
          AND content_status != 'folder_metadata'
        ORDER BY size ASC
    """).fetchall()
    out = []
    for fid, name, mime, size in rows:
        kind = classify(mime, name or "")
        if kind == "skip" or kind not in kinds:
            continue
        if (size or 0) > MAX_BYTES:
            continue
        out.append((fid, name, mime, size, kind))
    return out


def _write(con: sqlite3.Connection, stats: dict, sql: str, params: tuple) -> bool:
    """One bookkeeping write. A lock contention counts and continues, never kills."""
    try:
        con.execute(sql, params)
        con.commit()
        return True
    except sqlite3.Error as exc:
        stats["db_errors"] = stats.get("db_errors", 0) + 1
        stats.setdefault("first_db_error", f"{type(exc).__name__}: {exc}"[:200])
        con.rollback()
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-minutes", type=float, default=45.0)
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--types", default="image,msg,eml,pdf,xls,zip",
                        help="comma list: image,msg,eml,pdf,xls,zip")
    args = parser.parse_args()
    kinds = {k.strip() for k in args.types.split(",") if k.strip()}

    con = sqlite3.connect(DB_PATH)
    # google_indexer and google_rescreen are long-running writers on this same
    # file. sqlite's default lock timeout is 5 seconds, which one INSERT OR
    # REPLACE sweep can easily exceed.
    con.execute("PRAGMA busy_timeout=30000")
    try:
        todo = candidates(con, kinds)
        by_kind: dict[str, int] = {}
        for *_, kind in todo:
            by_kind[kind] = by_kind.get(kind, 0) + 1
        print(json.dumps({"phase": "plan", "candidates": len(todo), "by_kind": by_kind}, indent=1),
              flush=True)
        if args.dry_run:
            print("dry run - nothing downloaded or written")
            return 0
        if not todo:
            print("nothing to do")
            return 0

        creds = google_auth.get_creds(interactive=False)
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        deadline = time.monotonic() + args.max_minutes * 60
        stats = {"read": 0, "partial": 0, "quarantined": 0, "still_empty": 0,
                 "errors": 0, "db_errors": 0, "stopped_early": 0}

        for n, (fid, name, mime, _size, kind) in enumerate(todo, 1):
            if time.monotonic() >= deadline or (args.max_items and n > args.max_items):
                stats["stopped_early"] = 1
                break
            try:
                raw = download(drive, fid)
                got = EXTRACTORS[kind](raw)
                text = (got.text or "").strip()[:MAX_TEXT]
                complete = got.complete and len(got.text or "") <= MAX_TEXT
            except Exception as exc:
                stats["errors"] += 1
                stats.setdefault("first_error", f"{kind}: {type(exc).__name__}: {exc}"[:200])
                _write(con, stats, "UPDATE drive_files SET content_status=? WHERE id=?",
                       (f"backfill_error:{type(exc).__name__}", fid))
                continue

            if not text:
                stats["still_empty"] += 1
                _write(con, stats,
                       "UPDATE drive_files SET content_status='backfill_no_text' WHERE id=?",
                       (fid,))
                continue

            # A partial read is recorded as such, so nothing downstream can mistake
            # "we OCR'd the first 12 of 30 pages" for "we read this file".
            status = f"backfill_{kind}" if complete else f"backfill_partial_{kind}"
            if not complete:
                stats["partial"] = stats.get("partial", 0) + 1
            try:
                # Writes live INSIDE the try now, and the connection carries a
                # busy_timeout. They used to sit outside it with sqlite's default
                # 5s lock timeout, so a concurrent google_indexer/google_rescreen
                # writer raised "database is locked", the exception escaped main()
                # and the whole 45-minute run died with no summary and no receipt.
                con.execute(
                    "UPDATE drive_files SET content=?, content_status=?, indexed_at=?, "
                    "tdi_quarantined=0, tdi_marker=NULL WHERE id=?",
                    (text, status, now(), fid))
                con.execute("DELETE FROM drive_fts WHERE id=?", (fid,))
                owners = con.execute("SELECT owners FROM drive_files WHERE id=?",
                                     (fid,)).fetchone()
                description = con.execute("SELECT description FROM drive_files WHERE id=?",
                                          (fid,)).fetchone()
                con.execute(
                    "INSERT INTO drive_fts VALUES(?,?,?,?,?,?)",
                    (fid, name or "", mime or "",
                     (owners or [""])[0] or "", (description or [""])[0] or "", text))
                con.commit()
            except sqlite3.Error as exc:
                stats["db_errors"] = stats.get("db_errors", 0) + 1
                stats.setdefault("first_db_error", f"{type(exc).__name__}: {exc}"[:200])
                con.rollback()
                continue
            stats["read"] += 1
            if n % 20 == 0:
                print(json.dumps({"phase": "backfill", "done": n, "of": len(todo), **stats}),
                      flush=True)

        summary = {"status": "stopped_at_limit" if stats["stopped_early"] else "complete",
                   "processed": min(n, len(todo)), "of": len(todo), **stats}
        print(json.dumps(summary, indent=1), flush=True)
        receipt("google_drive_backfill", f"{DB_PATH}#read={stats['read']},quarantined={stats['quarantined']}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
