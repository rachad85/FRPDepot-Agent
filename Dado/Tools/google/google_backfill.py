"""Read the Drive files the indexer could not, and screen what they actually say.

The gap this closes (2026-07-24): 2,605 stored Drive rows have NO content.
extract_drive_content returns "" for unreadable types, oversize files and
extraction errors, so the `if content:` screen in upsert_drive is skipped and
the row is written anyway - screened on filename, owner and description only.
That is the inverse of this tree's own rule, "SCREEN WHAT YOU RETURN, not what
you happen to check", and it means a scanned TDI document called
Scan_20260722_212729.jpg would not be caught. 1,073 of those rows are exactly
that shape: JPEG scans.

WHAT IT ADDS, all offline - no installer runs on this box (SRP blocks them):
  images (1,142)  -> OCR via rapidocr_onnxruntime (ONNX, bundled in vendor)
  .msg    (126)   -> extract_msg (Outlook OLE)
  .eml    (127)   -> stdlib email parser
  scanned PDFs    -> PyMuPDF page render, then OCR
  legacy .xls (9) -> xlrd
  zip     (101)   -> member NAMES only (cheap, and names are often revealing)

Whatever is extracted is screened with deep_tdi_marker before it is stored, so
this pass can only ever move a row from "unscreened" to either "read and clear"
or "quarantined". It never widens what Dado can see without screening it first.

    python google_backfill.py --dry-run           what would be attempted
    python google_backfill.py --max-minutes 45    do the work, bounded + resumable
    python google_backfill.py --types image,msg   one class at a time

Long runs go through job_runner (SOUL: never wait on a job inside your turn):
    python ..\\watch\\job_runner.py start --name drive-backfill -- \\
        <venv python> google_backfill.py --max-minutes 45
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# Vendor goes at the END: it carries its own cryptography/numpy copies, and the
# hermes venv pins cryptography==46.0.7. Shadowing that would break the gateway.
VENDOR = Path(r"C:\FRPDepot\Dado\Tools\vendor")
if str(VENDOR) not in sys.path:
    sys.path.append(str(VENDOR))

import google_auth
from tdi_filter import deep_tdi_marker
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


def text_from_eml(raw: bytes) -> str:
    from email import policy
    from email.parser import BytesParser
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    parts = [f"Subject: {msg.get('subject', '')}", f"From: {msg.get('from', '')}",
             f"To: {msg.get('to', '')}", f"Cc: {msg.get('cc', '')}",
             f"Date: {msg.get('date', '')}"]
    body = ""
    try:
        best = msg.get_body(preferencelist=("plain", "html"))
        if best is not None:
            body = best.get_content()
            if best.get_content_type() == "text/html":
                body = re.sub(r"(?s)<[^>]+>", " ", body)
    except Exception:
        body = ""
    names = [p.get_filename() for p in msg.walk() if p.get_filename()]
    if names:
        parts.append("Attachments: " + " | ".join(names))
    return "\n".join(parts + ["", body])


def text_from_msg(raw: bytes) -> str:
    import extract_msg
    tmp = Path(os.environ.get("TEMP", ".")) / f"_dado_backfill_{os.getpid()}.msg"
    try:
        tmp.write_bytes(raw)
        m = extract_msg.Message(str(tmp))
        parts = [f"Subject: {m.subject or ''}", f"From: {m.sender or ''}",
                 f"To: {m.to or ''}", f"Cc: {m.cc or ''}", f"Date: {m.date or ''}"]
        names = [a.longFilename or a.shortFilename or "" for a in (m.attachments or [])]
        if any(names):
            parts.append("Attachments: " + " | ".join(n for n in names if n))
        parts += ["", m.body or ""]
        m.close()
        return "\n".join(parts)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def text_from_scanned_pdf(raw: bytes) -> str:
    """Render pages and OCR them - for PDFs that carry no embedded text layer."""
    import fitz  # PyMuPDF, in the venv
    out = []
    with fitz.open(stream=raw, filetype="pdf") as doc:
        for page in doc[:OCR_MAX_PDF_PAGES]:
            pix = page.get_pixmap(dpi=200)
            out.append(ocr_image_bytes(pix.tobytes("png")))
            if sum(len(x) for x in out) > MAX_TEXT:
                break
    return "\n".join(out)


def text_from_xls(raw: bytes) -> str:
    import xlrd
    book = xlrd.open_workbook(file_contents=raw)
    out = []
    for sheet in book.sheets():
        out.append(f"[Sheet: {sheet.name}]")
        for r in range(min(sheet.nrows, 4000)):
            row = "\t".join(str(c.value) for c in sheet.row(r))
            if row.strip():
                out.append(row)
            if sum(len(x) for x in out) > MAX_TEXT:
                return "\n".join(out)
    return "\n".join(out)


def text_from_zip(raw: bytes) -> str:
    """Member names only. Cheap, bounded, and a filename list is often enough to
    reveal whose documents these are."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        return "ZIP contents:\n" + "\n".join(i.filename for i in zf.infolist()[:2000])


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
    "image": ocr_image_bytes,
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
    rows = con.execute("""
        SELECT id, name, mime_type, size FROM drive_files
        WHERE content_status NOT LIKE 'indexed%'
          AND content_status NOT LIKE 'backfill_%'
          AND content_status != 'folder_metadata'
          AND tdi_quarantined = 0
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
        stats = {"read": 0, "quarantined": 0, "still_empty": 0, "errors": 0, "stopped_early": 0}

        for n, (fid, name, mime, _size, kind) in enumerate(todo, 1):
            if time.monotonic() >= deadline or (args.max_items and n > args.max_items):
                stats["stopped_early"] = 1
                break
            try:
                raw = download(drive, fid)
                text = (EXTRACTORS[kind](raw) or "").strip()[:MAX_TEXT]
            except Exception as exc:
                stats["errors"] += 1
                stats.setdefault("first_error", f"{kind}: {type(exc).__name__}: {exc}"[:200])
                con.execute("UPDATE drive_files SET content_status=? WHERE id=?",
                            (f"backfill_error:{type(exc).__name__}", fid))
                con.commit()
                continue

            if not text:
                stats["still_empty"] += 1
                con.execute("UPDATE drive_files SET content_status='backfill_no_text' WHERE id=?",
                            (fid,))
                con.commit()
                continue

            marker = deep_tdi_marker(text, name=name or "")
            if marker:
                # Screened on what we now RETURN, not on what we happened to check.
                con.execute(
                    "UPDATE drive_files SET tdi_quarantined=1, tdi_marker=?, "
                    "content_status='backfill_quarantined' WHERE id=?", (marker, fid))
                con.execute("DELETE FROM drive_fts WHERE id=?", (fid,))
                stats["quarantined"] += 1
            else:
                con.execute(
                    "UPDATE drive_files SET content=?, content_status=?, indexed_at=? WHERE id=?",
                    (text, f"backfill_{kind}", now(), fid))
                con.execute("DELETE FROM drive_fts WHERE id=?", (fid,))
                con.execute(
                    "INSERT INTO drive_fts VALUES(?,?,?,?,?,?)",
                    (fid, name or "", mime or "",
                     con.execute("SELECT owners FROM drive_files WHERE id=?", (fid,)).fetchone()[0] or "",
                     con.execute("SELECT description FROM drive_files WHERE id=?", (fid,)).fetchone()[0] or "",
                     text))
                stats["read"] += 1
            con.commit()
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
