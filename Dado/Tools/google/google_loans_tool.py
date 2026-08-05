#!/usr/bin/env python
"""FRP Depot Loans Spreadsheet Approved Adjustment Tool.

Commissioned by Rachad Homsi on 2026-07-26; fixed Stefe-table extension
commissioned by his direct instruction and tab confirmation on 2026-07-31;
fixed In-Laws-table extension commissioned by his direct instruction on
2026-08-04.

The only remote write in this module is one Sheets v4 values append-with-OVERWRITE on the exact
native spreadsheet My Drive/My Files/Rachad/Bussiness Folder/Loans. Three fixed operations exist:
CCIVS A4:B60 (date + negative repayment), Stefe C6:E43, and In-Laws A7:C61
(date + negative amount + description).
It cannot create, delete, rename, move, share, clear, batch, or otherwise restructure anything.
Every write requires a 24-hour, full-SHA-256, single-use staged approval.

Authorization reuses the separately validated Drive-only credential built by
google_investments_auth. Full Drive already authorizes the Sheets API for this
file; no token, grant, or scope is touched here.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any

import google_investments_auth as auth

TOOL_NAME = "FRP Depot Loans Spreadsheet Approved Repayment Tool"
SCHEMA_VERSION = 1
ACTION = "ccivs_repayment_append"  # Backward-compatible CCIVS action.
STEFE_ACTION = "stefe_adjustment_append"
IN_LAWS_ACTION = "in_laws_adjustment_append"
ALLOWED_ACTIONS = {ACTION, STEFE_ACTION, IN_LAWS_ACTION}
APPROVAL_WORD = "APPROVED"
PLAN_LIFETIME_HOURS = 24
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "loans_plans"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
BACKUP_DIR = auth.VAULT / "loans_backups"

SPREADSHEET_NAME = "Loans"
SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
EXPECTED_FILE_ID = "1WfstxvtOkfbX0zitJEirEUL94eYOo8Hm5mHOX_47TGQ"
EXPECTED_PARENT_PATH = ("My Drive", "My Files", "Rachad", "Bussiness Folder")
EXPECTED_PARENT_IDS = (
    "0ACKbTL9Q6AISUk9PVA",
    "1y-WtVIKC0APKhFN_GjEC5wBRjsLODzhQ",
    "14JcHCth2XM1968eQn1hA4jGKquQqDxMx",
    "12C-CPb_1PWt-WHTQOd3PDLeJ_IV9zSdw",
)
BUSINESS_FOLDER_ID = EXPECTED_PARENT_IDS[-1]
EXPECTED_LOCALE = "en_US"
EXPECTED_TIME_ZONE = "America/Los_Angeles"

SHEET_TITLE = "CCIVS"
SHEET_ID = 909361371
TOTAL_CELL = "B3"
TOTAL_FORMULA = "=SUM(B4:B60)"
FIRST_ROW = 4
LAST_ROW = 60
READ_RANGE = "'CCIVS'!A1:B60"
APPEND_RANGE = "'CCIVS'!A4:B60"

# Rachad commissioned this second fixed table on 2026-07-31. The live tab is
# spelled "Stefe". Only C:E rows 6:43 may receive one staged adjustment row;
# row 44 is a pinned note and must never be crossed.
STEFE_SHEET_TITLE = "Stefe"
STEFE_SHEET_ID = 396384971
STEFE_TOTAL_CELL = "D4"
STEFE_TOTAL_FORMULA = "=SUM(D6:D212)"
STEFE_HEADER_ROW = 5
STEFE_FIRST_ROW = 6
STEFE_LAST_ROW = 43
STEFE_NOTE_ROW = 44
STEFE_NOTE = "*Positive is owning, Negative are owed"
# The sheet's own Balance cell D4 sums D6:D212 - far past the modeled table.
# The read must cover everything that formula counts, and rows 45:212 must be
# EMPTY, or the balances this tool shows Rachad diverge from the sheet's own
# Balance cell (2026-07-31 review FINDING 3: only C1:E44 was read, so content
# below row 44 could silently falsify current/resulting_balance_cad).
STEFE_SCAN_LAST_ROW = 212
STEFE_READ_RANGE = "'Stefe'!C1:E212"
STEFE_APPEND_RANGE = "'Stefe'!C6:E43"

# Rachad commissioned the existing In-Laws table by direct instruction on
# 2026-08-04. Only A:C rows 7:61 may receive one staged adjustment row; row 62
# is a pinned note. The B5 Balance formula counts through row 234, so the read
# covers A1:C234 and any content below the note fails closed.
IN_LAWS_SHEET_TITLE = "In-Laws"
IN_LAWS_SHEET_ID = 1167058143
IN_LAWS_TOTAL_CELL = "B5"
IN_LAWS_TOTAL_CELL_ROW = 5
IN_LAWS_TOTAL_FORMULA = "=SUM(B7:B234)"
IN_LAWS_HEADER_ROW = 6
IN_LAWS_FIRST_ROW = 7
IN_LAWS_LAST_ROW = 61
IN_LAWS_NOTE_ROW = 62
IN_LAWS_NOTE = "*Positive is owning, Negative are owed"
IN_LAWS_SCAN_LAST_ROW = 234
IN_LAWS_READ_RANGE = "'In-Laws'!A1:C234"
IN_LAWS_APPEND_RANGE = "'In-Laws'!A7:C61"

AMOUNT_RE = re.compile(r"^[0-9]{1,9}(?:\.[0-9]{1,2})?$")
DESCRIPTION_RE = re.compile(r"^[^\r\n\x00-\x1f]{1,100}$")
NUMBER_RE = re.compile(r"^-?[0-9]{1,15}(?:\.[0-9]{1,10})?$")
SECRET_RE = re.compile(
    r"(?i)(?:ya29\.[A-Za-z0-9._-]+|access[_-]?token[=: ]+[^\s,}]+|refresh[_-]?token[=: ]+[^\s,}]+)"
)
SHEETS_EPOCH = date(1899, 12, 30)


class LoansError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def scrub(value: str) -> str:
    return SECRET_RE.sub("[REDACTED]", str(value))


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": utc_now().isoformat(), "action": action, "evidence": evidence}
    with RECEIPTS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=True) + "\n")


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------


def clean_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise LoansError("date must be YYYY-MM-DD.") from exc
    return parsed.isoformat()


def clean_amount(value: str) -> str:
    """Rachad supplies the deduction as a positive number; the sheet stores it negative."""
    text = str(value).strip()
    if not AMOUNT_RE.fullmatch(text):
        raise LoansError("amount must be a positive CAD number with at most two decimals.")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise LoansError("amount is invalid.") from exc
    if number <= 0 or number > Decimal("100000000"):
        raise LoansError("amount must be greater than 0 and no more than CAD 100,000,000.")
    return format(number.normalize(), "f")


def negative_amount(value: str) -> str:
    return format(-Decimal(clean_amount(value)), "f")


def clean_source(value: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 500:
        raise LoansError("source is required and must be at most 500 characters.")
    if any(ord(char) < 32 for char in text):
        raise LoansError("source contains control characters.")
    return text


def clean_description(value: str) -> str:
    text = str(value or "").strip()
    if not DESCRIPTION_RE.fullmatch(text):
        raise LoansError("description is required, must be 1-100 characters, and cannot contain controls.")
    if text.startswith(("=", "+", "-", "@")):
        raise LoansError("description cannot begin with a spreadsheet formula marker.")
    return text


def sheet_date_text(iso_date: str) -> str:
    parsed = date.fromisoformat(clean_date(iso_date))
    return f"{parsed.month}/{parsed.day}/{parsed.year}"


def sheet_number(text: str) -> int | float:
    number = Decimal(text)
    return int(number) if number == number.to_integral_value() else float(number)


# --------------------------------------------------------------------------
# Cell helpers
# --------------------------------------------------------------------------


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _normalize_grid(values: Any) -> list[list[str]]:
    rows = list(values or [])
    grid: list[list[str]] = []
    for index in range(LAST_ROW):
        raw = list(rows[index]) if index < len(rows) and isinstance(rows[index], list) else []
        grid.append([cell_text(raw[column]) if column < len(raw) else "" for column in range(2)])
    return grid


def _normalize_stefe_grid(values: Any) -> list[list[str]]:
    rows = list(values or [])
    grid: list[list[str]] = []
    for index in range(STEFE_SCAN_LAST_ROW):
        raw = list(rows[index]) if index < len(rows) and isinstance(rows[index], list) else []
        grid.append([cell_text(raw[column]) if column < len(raw) else "" for column in range(3)])
    return grid


def _normalize_in_laws_grid(values: Any) -> list[list[str]]:
    rows = list(values or [])
    grid: list[list[str]] = []
    for index in range(IN_LAWS_SCAN_LAST_ROW):
        raw = list(rows[index]) if index < len(rows) and isinstance(rows[index], list) else []
        grid.append([cell_text(raw[column]) if column < len(raw) else "" for column in range(3)])
    return grid


def cell_decimal(text: str) -> Decimal | None:
    value = str(text).strip()
    if not value or value.startswith("="):
        return None
    cleaned = value.replace(",", "").replace("$", "").replace("\u00a0", "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1].strip()
    if not NUMBER_RE.fullmatch(cleaned):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def cell_date(text: str) -> date | None:
    value = str(text).strip()
    if not value or value.startswith("="):
        return None
    number = cell_decimal(value)
    if number is not None:
        if number <= 0 or number > Decimal("2958465"):
            return None
        return SHEETS_EPOCH + timedelta(days=int(number))
    for pattern in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Live Drive and Sheets identity
# --------------------------------------------------------------------------


def sheets_service():
    """Sheets client built from the already-connected Drive-only credential."""
    from googleapiclient.discovery import build

    return build("sheets", "v4", credentials=auth.get_creds(), cache_discovery=False)


def _parent_identity(drive, item: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    parents = list(item.get("parents") or [])
    if len(parents) != 1:
        raise LoansError("The Loans spreadsheet must have exactly one parent folder.")
    names: list[str] = []
    identifiers: list[str] = []
    seen: set[str] = set()
    current = str((parents[0] or {}).get("id") or "")
    while current:
        if current in seen or len(names) > 12:
            raise LoansError("Drive parent path is cyclic or unexpectedly deep.")
        seen.add(current)
        parent = drive.files().get(
            fileId=current, supportsAllDrives=True, fields="id,title,parents(id)"
        ).execute(num_retries=3)
        names.append(str(parent.get("title") or ""))
        identifiers.append(str(parent.get("id") or ""))
        next_parents = list(parent.get("parents") or [])
        if len(next_parents) > 1:
            raise LoansError("A Loans parent folder has multiple parents.")
        current = str((next_parents[0] or {}).get("id") or "") if next_parents else ""
    return tuple(reversed(identifiers)), tuple(reversed(names))


def _drive_metadata(drive) -> dict[str, Any]:
    return drive.files().get(
        fileId=EXPECTED_FILE_ID,
        supportsAllDrives=True,
        fields="id,title,mimeType,parents(id),modifiedDate,version,editable",
    ).execute(num_retries=3)


def _verify_exact_file(drive, item: dict[str, Any]) -> dict[str, Any]:
    if (
        item.get("id") != EXPECTED_FILE_ID
        or item.get("title") != SPREADSHEET_NAME
        or item.get("mimeType") != SPREADSHEET_MIME
    ):
        raise LoansError("The pinned Drive item is not the commissioned Loans spreadsheet.")
    parent_ids, path = _parent_identity(drive, item)
    if parent_ids != EXPECTED_PARENT_IDS or path != EXPECTED_PARENT_PATH:
        raise LoansError(
            "The Loans spreadsheet is not at the commissioned Drive path: "
            + " / ".join(EXPECTED_PARENT_PATH)
        )
    if parent_ids[-1] != BUSINESS_FOLDER_ID:
        raise LoansError("The Loans spreadsheet is not inside the commissioned Business Folder.")
    return {
        **item,
        "verified_parent_ids": list(parent_ids),
        "verified_parent_path": list(path),
    }


def resolve_file(drive) -> dict[str, Any]:
    item = _verify_exact_file(drive, _drive_metadata(drive))
    if not item.get("editable"):
        raise LoansError("Google reports that this account cannot edit the Loans spreadsheet.")
    return item


def read_identity(sheets) -> dict[str, Any]:
    info = sheets.spreadsheets().get(
        spreadsheetId=EXPECTED_FILE_ID,
        includeGridData=False,
        fields="spreadsheetId,properties(title,locale,timeZone),sheets(properties(sheetId,title))",
    ).execute(num_retries=3)
    if str(info.get("spreadsheetId") or "") != EXPECTED_FILE_ID:
        raise LoansError("Sheets returned a different spreadsheet id than the commissioned one.")
    properties = info.get("properties") or {}
    if str(properties.get("title") or "") != SPREADSHEET_NAME:
        raise LoansError("The spreadsheet title is no longer the commissioned Loans workbook.")
    if str(properties.get("locale") or "") != EXPECTED_LOCALE:
        raise LoansError("The Loans spreadsheet locale changed; stopped.")
    if str(properties.get("timeZone") or "") != EXPECTED_TIME_ZONE:
        raise LoansError("The Loans spreadsheet time zone changed; stopped.")
    tabs = [dict(sheet.get("properties") or {}) for sheet in (info.get("sheets") or [])]
    matches = [tab for tab in tabs if str(tab.get("title") or "") == SHEET_TITLE]
    if len(matches) != 1:
        raise LoansError("The CCIVS tab is missing or duplicated; stopped.")
    if int(matches[0].get("sheetId") or -1) != SHEET_ID:
        raise LoansError("The CCIVS tab id changed; stopped.")
    return {
        "spreadsheet_title": SPREADSHEET_NAME,
        "locale": EXPECTED_LOCALE,
        "time_zone": EXPECTED_TIME_ZONE,
        "tab": SHEET_TITLE,
        "sheet_id": SHEET_ID,
    }


def read_stefe_identity(sheets) -> dict[str, Any]:
    info = sheets.spreadsheets().get(
        spreadsheetId=EXPECTED_FILE_ID,
        includeGridData=False,
        fields="spreadsheetId,properties(title,locale,timeZone),sheets(properties(sheetId,title))",
    ).execute(num_retries=3)
    if str(info.get("spreadsheetId") or "") != EXPECTED_FILE_ID:
        raise LoansError("Sheets returned a different spreadsheet id than the commissioned one.")
    properties = info.get("properties") or {}
    if str(properties.get("title") or "") != SPREADSHEET_NAME:
        raise LoansError("The spreadsheet title is no longer the commissioned Loans workbook.")
    if str(properties.get("locale") or "") != EXPECTED_LOCALE:
        raise LoansError("The Loans spreadsheet locale changed; stopped.")
    if str(properties.get("timeZone") or "") != EXPECTED_TIME_ZONE:
        raise LoansError("The Loans spreadsheet time zone changed; stopped.")
    tabs = [dict(sheet.get("properties") or {}) for sheet in (info.get("sheets") or [])]
    matches = [tab for tab in tabs if str(tab.get("title") or "") == STEFE_SHEET_TITLE]
    if len(matches) != 1:
        raise LoansError("The Stefe tab is missing or duplicated; stopped.")
    if int(matches[0].get("sheetId") or -1) != STEFE_SHEET_ID:
        raise LoansError("The Stefe tab id changed; stopped.")
    return {
        "spreadsheet_title": SPREADSHEET_NAME,
        "locale": EXPECTED_LOCALE,
        "time_zone": EXPECTED_TIME_ZONE,
        "tab": STEFE_SHEET_TITLE,
        "sheet_id": STEFE_SHEET_ID,
    }


def read_in_laws_identity(sheets) -> dict[str, Any]:
    info = sheets.spreadsheets().get(
        spreadsheetId=EXPECTED_FILE_ID,
        includeGridData=False,
        fields="spreadsheetId,properties(title,locale,timeZone),sheets(properties(sheetId,title))",
    ).execute(num_retries=3)
    if str(info.get("spreadsheetId") or "") != EXPECTED_FILE_ID:
        raise LoansError("Sheets returned a different spreadsheet id than the commissioned one.")
    properties = info.get("properties") or {}
    if str(properties.get("title") or "") != SPREADSHEET_NAME:
        raise LoansError("The spreadsheet title is no longer the commissioned Loans workbook.")
    if str(properties.get("locale") or "") != EXPECTED_LOCALE:
        raise LoansError("The Loans spreadsheet locale changed; stopped.")
    if str(properties.get("timeZone") or "") != EXPECTED_TIME_ZONE:
        raise LoansError("The Loans spreadsheet time zone changed; stopped.")
    tabs = [dict(sheet.get("properties") or {}) for sheet in (info.get("sheets") or [])]
    matches = [tab for tab in tabs if str(tab.get("title") or "") == IN_LAWS_SHEET_TITLE]
    if len(matches) != 1:
        raise LoansError("The In-Laws tab is missing or duplicated; stopped.")
    if int(matches[0].get("sheetId") or -1) != IN_LAWS_SHEET_ID:
        raise LoansError("The In-Laws tab id changed; stopped.")
    return {
        "spreadsheet_title": SPREADSHEET_NAME,
        "locale": EXPECTED_LOCALE,
        "time_zone": EXPECTED_TIME_ZONE,
        "tab": IN_LAWS_SHEET_TITLE,
        "sheet_id": IN_LAWS_SHEET_ID,
    }


def read_grid(sheets) -> list[list[str]]:
    response = sheets.spreadsheets().values().get(
        spreadsheetId=EXPECTED_FILE_ID,
        range=READ_RANGE,
        majorDimension="ROWS",
        valueRenderOption="FORMULA",
        dateTimeRenderOption="SERIAL_NUMBER",
    ).execute(num_retries=3)
    returned = str(response.get("range") or "")
    if returned and returned.split("!")[0].strip("'") != SHEET_TITLE:
        raise LoansError("The values read did not come from the CCIVS tab.")
    return _normalize_grid(response.get("values"))


def read_stefe_grid(sheets) -> list[list[str]]:
    response = sheets.spreadsheets().values().get(
        spreadsheetId=EXPECTED_FILE_ID,
        range=STEFE_READ_RANGE,
        majorDimension="ROWS",
        valueRenderOption="FORMULA",
        dateTimeRenderOption="SERIAL_NUMBER",
    ).execute(num_retries=3)
    returned = str(response.get("range") or "")
    if returned and returned.split("!")[0].strip("'") != STEFE_SHEET_TITLE:
        raise LoansError("The values read did not come from the Stefe tab.")
    return _normalize_stefe_grid(response.get("values"))


def read_in_laws_grid(sheets) -> list[list[str]]:
    response = sheets.spreadsheets().values().get(
        spreadsheetId=EXPECTED_FILE_ID,
        range=IN_LAWS_READ_RANGE,
        majorDimension="ROWS",
        valueRenderOption="FORMULA",
        dateTimeRenderOption="SERIAL_NUMBER",
    ).execute(num_retries=3)
    returned = str(response.get("range") or "")
    if returned and returned.split("!")[0].strip("'") != IN_LAWS_SHEET_TITLE:
        raise LoansError("The values read did not come from the In-Laws tab.")
    return _normalize_in_laws_grid(response.get("values"))


# --------------------------------------------------------------------------
# Table reasoning
# --------------------------------------------------------------------------


def check_layout(grid: list[list[str]]) -> None:
    if len(grid) != LAST_ROW:
        raise LoansError("The CCIVS read did not cover rows 1 to 60.")
    total = grid[2][1].strip()
    if total != TOTAL_FORMULA:
        raise LoansError(
            f"CCIVS {TOTAL_CELL} is not the commissioned total formula {TOTAL_FORMULA}; stopped."
        )


def table_state(grid: list[list[str]]) -> dict[str, Any]:
    check_layout(grid)
    last_used = 0
    balance = Decimal("0")
    numeric_rows = 0
    for row in range(FIRST_ROW, LAST_ROW + 1):
        date_cell, amount_cell = grid[row - 1]
        if not date_cell.strip() and not amount_cell.strip():
            continue
        last_used = row
        if amount_cell.strip().startswith("="):
            raise LoansError(f"CCIVS B{row} holds a formula; the balance cannot be trusted.")
        if amount_cell.strip():
            value = cell_decimal(amount_cell)
            if value is None:
                raise LoansError(f"CCIVS B{row} is not a plain number; stopped.")
            balance += value
            numeric_rows += 1
    next_row = last_used + 1 if last_used else FIRST_ROW
    return {
        "last_used_row": last_used,
        "next_row": next_row,
        "numeric_rows": numeric_rows,
        "current_balance": balance,
    }


def plan_entry(grid: list[list[str]], entry_date: str, amount_cad: str) -> dict[str, Any]:
    state = table_state(grid)
    next_row = int(state["next_row"])
    if not FIRST_ROW <= next_row <= LAST_ROW:
        raise LoansError(
            f"The CCIVS table is full through row {LAST_ROW}; no blank row remains inside A4:B60."
        )
    if grid[next_row - 1][0].strip() or grid[next_row - 1][1].strip():
        raise LoansError(f"CCIVS row {next_row} is not blank; stopped.")
    wanted_date = date.fromisoformat(entry_date)
    stored_amount = Decimal(negative_amount(amount_cad))
    for row in range(FIRST_ROW, LAST_ROW + 1):
        existing_date = cell_date(grid[row - 1][0])
        existing_amount = cell_decimal(grid[row - 1][1])
        if existing_date == wanted_date and existing_amount == stored_amount:
            raise LoansError(f"That repayment already appears at CCIVS row {row}; stopped.")
    current_balance = Decimal(state["current_balance"])
    resulting_balance = current_balance + stored_amount
    return {
        "tab": SHEET_TITLE,
        "sheet_id": SHEET_ID,
        "row": next_row,
        "range": f"{SHEET_TITLE}!A{next_row}:B{next_row}",
        "date": entry_date,
        "date_cell": sheet_date_text(entry_date),
        "deduction_cad": clean_amount(amount_cad),
        "appended_amount_cad": format(stored_amount, "f"),
        "total_formula": TOTAL_FORMULA,
        "current_balance_cad": format(current_balance, "f"),
        "resulting_balance_cad": format(resulting_balance, "f"),
    }


def expected_grid_after(grid: list[list[str]], entry: dict[str, Any]) -> list[list[str]]:
    after = [list(row) for row in grid]
    after[int(entry["row"]) - 1] = [str(entry["date_cell"]), str(entry["appended_amount_cad"])]
    return after


def verify_readback(before: list[list[str]], after: list[list[str]], entry: dict[str, Any]) -> None:
    check_layout(after)
    row = int(entry["row"])
    wanted_date = date.fromisoformat(str(entry["date"]))
    wanted_amount = Decimal(str(entry["appended_amount_cad"]))
    if cell_date(after[row - 1][0]) != wanted_date:
        raise LoansError(f"CCIVS A{row} does not read back as {entry['date_cell']}.")
    if cell_decimal(after[row - 1][1]) != wanted_amount:
        raise LoansError(f"CCIVS B{row} does not read back as {entry['appended_amount_cad']}.")
    expected = expected_grid_after(before, entry)
    for index in range(LAST_ROW):
        if index == row - 1:
            continue
        if after[index] != expected[index]:
            raise LoansError(
                f"CCIVS row {index + 1} changed during the append; reconciliation required."
            )
    state = table_state(after)
    if format(Decimal(state["current_balance"]), "f") != str(entry["resulting_balance_cad"]):
        raise LoansError("The CCIVS balance after the append does not match the approved plan.")


def check_stefe_layout(grid: list[list[str]]) -> None:
    if len(grid) != STEFE_SCAN_LAST_ROW or any(len(row) != 3 for row in grid):
        raise LoansError("The Stefe read did not cover C1:E212 exactly.")
    if grid[3][0].strip() != "Balance" or grid[3][1].strip() != STEFE_TOTAL_FORMULA:
        raise LoansError(
            f"Stefe {STEFE_TOTAL_CELL} is not the commissioned total formula {STEFE_TOTAL_FORMULA}; stopped."
        )
    if [cell.strip() for cell in grid[STEFE_HEADER_ROW - 1]] != ["DATE", "Amount", "Description"]:
        raise LoansError("Stefe C5:E5 headers changed; stopped.")
    if grid[STEFE_NOTE_ROW - 1][0].strip() != STEFE_NOTE:
        raise LoansError("Stefe row 44 note changed; stopped.")
    if grid[STEFE_NOTE_ROW - 1][1].strip() or grid[STEFE_NOTE_ROW - 1][2].strip():
        raise LoansError("Stefe row 44 contains unexpected values; stopped.")
    # Fail closed on ANY content below the note row: the D4 Balance formula
    # counts D6:D212, so a value in D45:D212 makes every balance this tool
    # reports or verifies wrong - and stray C/E content down there means the
    # table is no longer the one Rachad commissioned.
    for row in range(STEFE_NOTE_ROW + 1, STEFE_SCAN_LAST_ROW + 1):
        if any(cell.strip() for cell in grid[row - 1]):
            raise LoansError(
                f"Stefe row {row} contains data below the row-44 note. The D4 "
                f"Balance formula sums D6:D212, so the tool's balance would be "
                "wrong; clear rows 45:212 or recommission the table. Stopped."
            )


def stefe_table_state(grid: list[list[str]]) -> dict[str, Any]:
    check_stefe_layout(grid)
    last_used = 0
    balance = Decimal("0")
    numeric_rows = 0
    for row in range(STEFE_FIRST_ROW, STEFE_LAST_ROW + 1):
        date_cell, amount_cell, description_cell = grid[row - 1]
        cells = [date_cell.strip(), amount_cell.strip(), description_cell.strip()]
        if not any(cells):
            continue
        if not all(cells):
            raise LoansError(f"Stefe row {row} is partially filled; stopped.")
        if cell_date(date_cell) is None:
            raise LoansError(f"Stefe C{row} is not a plain date; stopped.")
        if amount_cell.strip().startswith("="):
            raise LoansError(f"Stefe D{row} holds a formula; the balance cannot be trusted.")
        value = cell_decimal(amount_cell)
        if value is None:
            raise LoansError(f"Stefe D{row} is not a plain number; stopped.")
        last_used = row
        balance += value
        numeric_rows += 1
    next_row = last_used + 1 if last_used else STEFE_FIRST_ROW
    return {
        "last_used_row": last_used,
        "next_row": next_row,
        "numeric_rows": numeric_rows,
        "current_balance": balance,
    }


def plan_stefe_entry(grid: list[list[str]], entry_date: str, amount_cad: str,
                     description: str) -> dict[str, Any]:
    state = stefe_table_state(grid)
    next_row = int(state["next_row"])
    if not STEFE_FIRST_ROW <= next_row <= STEFE_LAST_ROW:
        raise LoansError(
            f"The Stefe table is full through row {STEFE_LAST_ROW}; no blank row remains before the row 44 note."
        )
    if any(cell.strip() for cell in grid[next_row - 1]):
        raise LoansError(f"Stefe row {next_row} is not blank; stopped.")
    wanted_date = date.fromisoformat(clean_date(entry_date))
    stored_amount = Decimal(negative_amount(amount_cad))
    wanted_description = clean_description(description)
    for row in range(STEFE_FIRST_ROW, STEFE_LAST_ROW + 1):
        existing_date = cell_date(grid[row - 1][0])
        existing_amount = cell_decimal(grid[row - 1][1])
        existing_description = grid[row - 1][2].strip()
        if (
            existing_date == wanted_date
            and existing_amount == stored_amount
            and existing_description.casefold() == wanted_description.casefold()
        ):
            raise LoansError(f"That adjustment already appears at Stefe row {row}; stopped.")
    current_balance = Decimal(state["current_balance"])
    resulting_balance = current_balance + stored_amount
    return {
        "tab": STEFE_SHEET_TITLE,
        "sheet_id": STEFE_SHEET_ID,
        "row": next_row,
        "range": f"{STEFE_SHEET_TITLE}!C{next_row}:E{next_row}",
        "date": clean_date(entry_date),
        "date_cell": sheet_date_text(entry_date),
        "deduction_cad": clean_amount(amount_cad),
        "appended_amount_cad": format(stored_amount, "f"),
        "description": wanted_description,
        "total_formula": STEFE_TOTAL_FORMULA,
        "current_balance_cad": format(current_balance, "f"),
        "resulting_balance_cad": format(resulting_balance, "f"),
    }


def expected_stefe_grid_after(grid: list[list[str]], entry: dict[str, Any]) -> list[list[str]]:
    after = [list(row) for row in grid]
    after[int(entry["row"]) - 1] = [
        str(entry["date_cell"]), str(entry["appended_amount_cad"]), str(entry["description"])
    ]
    return after


def verify_stefe_readback(before: list[list[str]], after: list[list[str]],
                          entry: dict[str, Any]) -> None:
    check_stefe_layout(after)
    row = int(entry["row"])
    if cell_date(after[row - 1][0]) != date.fromisoformat(str(entry["date"])):
        raise LoansError(f"Stefe C{row} does not read back as {entry['date_cell']}.")
    if cell_decimal(after[row - 1][1]) != Decimal(str(entry["appended_amount_cad"])):
        raise LoansError(f"Stefe D{row} does not read back as {entry['appended_amount_cad']}.")
    if after[row - 1][2].strip() != str(entry["description"]):
        raise LoansError(f"Stefe E{row} does not read back as the approved description.")
    expected = expected_stefe_grid_after(before, entry)
    for index in range(STEFE_SCAN_LAST_ROW):
        if index == row - 1:
            continue
        if after[index] != expected[index]:
            raise LoansError(f"Stefe row {index + 1} changed during the append; reconciliation required.")
    state = stefe_table_state(after)
    if format(Decimal(state["current_balance"]), "f") != str(entry["resulting_balance_cad"]):
        raise LoansError("The Stefe balance after the append does not match the approved plan.")


def check_in_laws_layout(grid: list[list[str]]) -> None:
    if len(grid) != IN_LAWS_SCAN_LAST_ROW or any(len(row) != 3 for row in grid):
        raise LoansError("The In-Laws read did not cover A1:C234 exactly.")
    if grid[IN_LAWS_TOTAL_CELL_ROW - 1][0].strip() != "Balance" or grid[IN_LAWS_TOTAL_CELL_ROW - 1][1].strip() != IN_LAWS_TOTAL_FORMULA:
        raise LoansError(
            f"In-Laws {IN_LAWS_TOTAL_CELL} is not the commissioned total formula {IN_LAWS_TOTAL_FORMULA}; stopped."
        )
    if [cell.strip() for cell in grid[IN_LAWS_HEADER_ROW - 1]] != ["DATE", "Amount", "Description"]:
        raise LoansError("In-Laws A6:C6 headers changed; stopped.")
    if grid[IN_LAWS_NOTE_ROW - 1][0].strip() != IN_LAWS_NOTE:
        raise LoansError("In-Laws row 62 note changed; stopped.")
    if grid[IN_LAWS_NOTE_ROW - 1][1].strip() or grid[IN_LAWS_NOTE_ROW - 1][2].strip():
        raise LoansError("In-Laws row 62 contains unexpected values; stopped.")
    for row in range(IN_LAWS_NOTE_ROW + 1, IN_LAWS_SCAN_LAST_ROW + 1):
        if any(cell.strip() for cell in grid[row - 1]):
            raise LoansError(
                f"In-Laws row {row} contains data below the row-62 note. The B5 "
                f"Balance formula sums B7:B234, so the tool's balance would be "
                "wrong; clear rows 63:234 or recommission the table. Stopped."
            )


def in_laws_table_state(grid: list[list[str]]) -> dict[str, Any]:
    check_in_laws_layout(grid)
    last_used = 0
    balance = Decimal("0")
    numeric_rows = 0
    for row in range(IN_LAWS_FIRST_ROW, IN_LAWS_LAST_ROW + 1):
        date_cell, amount_cell, description_cell = grid[row - 1]
        cells = [date_cell.strip(), amount_cell.strip(), description_cell.strip()]
        if not any(cells):
            continue
        if not all(cells):
            raise LoansError(f"In-Laws row {row} is partially filled; stopped.")
        if cell_date(date_cell) is None:
            raise LoansError(f"In-Laws A{row} is not a plain date; stopped.")
        if amount_cell.startswith("="):
            raise LoansError(f"In-Laws B{row} holds a formula; the balance cannot be trusted.")
        value = cell_decimal(amount_cell)
        if value is None:
            raise LoansError(f"In-Laws B{row} is not a plain number; stopped.")
        last_used = row
        balance += value
        numeric_rows += 1
    next_row = last_used + 1 if last_used else IN_LAWS_FIRST_ROW
    return {
        "last_used_row": last_used,
        "next_row": next_row,
        "numeric_rows": numeric_rows,
        "current_balance": balance,
    }


def plan_in_laws_entry(grid: list[list[str]], entry_date: str, amount_cad: str,
                       description: str) -> dict[str, Any]:
    state = in_laws_table_state(grid)
    next_row = int(state["next_row"])
    if not IN_LAWS_FIRST_ROW <= next_row <= IN_LAWS_LAST_ROW:
        raise LoansError(
            f"The In-Laws table is full through row {IN_LAWS_LAST_ROW}; no blank row remains before the row 62 note."
        )
    if any(cell.strip() for cell in grid[next_row - 1]):
        raise LoansError(f"In-Laws row {next_row} is not blank; stopped.")
    wanted_date = date.fromisoformat(clean_date(entry_date))
    stored_amount = Decimal(negative_amount(amount_cad))
    wanted_description = clean_description(description)
    for row in range(IN_LAWS_FIRST_ROW, IN_LAWS_LAST_ROW + 1):
        existing_date = cell_date(grid[row - 1][0])
        existing_amount = cell_decimal(grid[row - 1][1])
        existing_description = grid[row - 1][2].strip()
        if (
            existing_date == wanted_date
            and existing_amount == stored_amount
            and existing_description.casefold() == wanted_description.casefold()
        ):
            raise LoansError(f"That adjustment already appears at In-Laws row {row}; stopped.")
    current_balance = Decimal(state["current_balance"])
    resulting_balance = current_balance + stored_amount
    return {
        "tab": IN_LAWS_SHEET_TITLE,
        "sheet_id": IN_LAWS_SHEET_ID,
        "row": next_row,
        "range": f"{IN_LAWS_SHEET_TITLE}!A{next_row}:C{next_row}",
        "date": clean_date(entry_date),
        "date_cell": sheet_date_text(entry_date),
        "deduction_cad": clean_amount(amount_cad),
        "appended_amount_cad": format(stored_amount, "f"),
        "description": wanted_description,
        "total_formula": IN_LAWS_TOTAL_FORMULA,
        "current_balance_cad": format(current_balance, "f"),
        "resulting_balance_cad": format(resulting_balance, "f"),
    }


def expected_in_laws_grid_after(grid: list[list[str]], entry: dict[str, Any]) -> list[list[str]]:
    after = [list(row) for row in grid]
    after[int(entry["row"]) - 1] = [
        str(entry["date_cell"]), str(entry["appended_amount_cad"]), str(entry["description"])
    ]
    return after


def verify_in_laws_readback(before: list[list[str]], after: list[list[str]],
                            entry: dict[str, Any]) -> None:
    check_in_laws_layout(after)
    row = int(entry["row"])
    if cell_date(after[row - 1][0]) != date.fromisoformat(str(entry["date"])):
        raise LoansError(f"In-Laws A{row} does not read back as {entry['date_cell']}.")
    if cell_decimal(after[row - 1][1]) != Decimal(str(entry["appended_amount_cad"])):
        raise LoansError(f"In-Laws B{row} does not read back as {entry['appended_amount_cad']}.")
    if after[row - 1][2].strip() != str(entry["description"]):
        raise LoansError(f"In-Laws C{row} does not read back as the approved description.")
    expected = expected_in_laws_grid_after(before, entry)
    for index in range(IN_LAWS_SCAN_LAST_ROW):
        if index == row - 1:
            continue
        if after[index] != expected[index]:
            raise LoansError(f"In-Laws row {index + 1} changed during the append; reconciliation required.")
    state = in_laws_table_state(after)
    if format(Decimal(state["current_balance"]), "f") != str(entry["resulting_balance_cad"]):
        raise LoansError("The In-Laws balance after the append does not match the approved plan.")


# --------------------------------------------------------------------------
# Plan handling
# --------------------------------------------------------------------------


def approval_phrase(digest: str) -> str:
    # The full digest stays internal and is validated by load_plan; Rachad
    # approved a plain one-word confirmation so he never copies checksum text.
    return APPROVAL_WORD


def lock_path(plan_digest: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", str(plan_digest)):
        raise LoansError("Plan digest is invalid for replay locking.")
    return PLAN_DIR / ".commit-locks" / f"{plan_digest}.json"


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise LoansError("This plan has already entered commit and cannot be replayed.") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def build_plan(item: dict[str, Any], identity: dict[str, Any], grid: list[list[str]],
               entry: dict[str, Any], source: str, *, action: str = ACTION,
               created: datetime | None = None) -> dict[str, Any]:
    created = created or utc_now()
    if action not in ALLOWED_ACTIONS:
        raise LoansError("Uncommissioned loans action.")
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "action": action,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "file": {
            "id": str(item["id"]),
            "name": SPREADSHEET_NAME,
            "parent_ids": list(EXPECTED_PARENT_IDS),
            "parent_path": list(EXPECTED_PARENT_PATH),
            "mime_type": SPREADSHEET_MIME,
            "modified_time": str(item.get("modifiedDate") or ""),
            "version": str(item.get("version") or ""),
            "spreadsheet_title": str(identity["spreadsheet_title"]),
            "locale": str(identity["locale"]),
            "time_zone": str(identity["time_zone"]),
            "tab": str(identity["tab"]),
            "sheet_id": int(identity["sheet_id"]),
            "content_sha256": content_sha256(grid),
        },
        "entry": entry,
        "source": source,
    }
    return {**core, "sha256": digest_for(core)}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LoansError("Plan is unreadable.") from exc
    if not isinstance(value, dict):
        raise LoansError("Plan must contain one JSON object.")
    return value


def load_plan(path: Path) -> dict[str, Any]:
    plan = read_json(path)
    saved = str(plan.pop("sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(plan)):
        raise LoansError("Plan hash check failed. The plan changed after review.")
    expected_keys = {
        "schema_version", "tool", "action", "created_utc", "expires_utc",
        "nonce", "file", "entry", "source",
    }
    if set(plan) != expected_keys:
        raise LoansError("Plan fields do not match the closed schema.")
    action = str(plan.get("action") or "")
    if (
        plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("tool") != TOOL_NAME
        or action not in ALLOWED_ACTIONS
    ):
        raise LoansError("Plan schema, tool, or action is invalid.")
    try:
        created = datetime.fromisoformat(str(plan["created_utc"]))
        expires = datetime.fromisoformat(str(plan["expires_utc"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise LoansError("Plan creation or expiry time is invalid.") from exc
    if created.tzinfo is None or expires.tzinfo is None:
        raise LoansError("Plan times must include a timezone.")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise LoansError("Plan lifetime must be exactly 24 hours.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise LoansError("Plan creation time is in the future.")
    if now >= expires:
        raise LoansError("Plan expired. Stage a new plan.")
    if not re.fullmatch(r"[0-9a-f]{32}", str(plan.get("nonce") or "")):
        raise LoansError("Plan nonce is invalid.")
    file_info = plan.get("file")
    if not isinstance(file_info, dict):
        raise LoansError("Plan file identity is invalid.")
    expected_file_keys = {
        "id", "name", "parent_ids", "parent_path", "mime_type", "modified_time",
        "version", "spreadsheet_title", "locale", "time_zone", "tab", "sheet_id",
        "content_sha256",
    }
    if action == STEFE_ACTION:
        expected_tab, expected_sheet_id = STEFE_SHEET_TITLE, STEFE_SHEET_ID
    elif action == IN_LAWS_ACTION:
        expected_tab, expected_sheet_id = IN_LAWS_SHEET_TITLE, IN_LAWS_SHEET_ID
    else:
        expected_tab, expected_sheet_id = SHEET_TITLE, SHEET_ID
    if (
        set(file_info) != expected_file_keys
        or file_info.get("id") != EXPECTED_FILE_ID
        or file_info.get("name") != SPREADSHEET_NAME
        or file_info.get("parent_ids") != list(EXPECTED_PARENT_IDS)
        or file_info.get("parent_path") != list(EXPECTED_PARENT_PATH)
        or file_info.get("mime_type") != SPREADSHEET_MIME
        or file_info.get("spreadsheet_title") != SPREADSHEET_NAME
        or file_info.get("locale") != EXPECTED_LOCALE
        or file_info.get("time_zone") != EXPECTED_TIME_ZONE
        or file_info.get("tab") != expected_tab
        or file_info.get("sheet_id") != expected_sheet_id
        or not re.fullmatch(r"[0-9a-f]{64}", str(file_info.get("content_sha256") or ""))
    ):
        raise LoansError("Plan does not target the commissioned Loans spreadsheet.")
    entry = plan.get("entry")
    if not isinstance(entry, dict):
        raise LoansError("Plan entry is invalid.")
    row = int(entry.get("row") or 0)
    deduction = clean_amount(str(entry.get("deduction_cad") or ""))
    entry_date = clean_date(str(entry.get("date") or ""))
    current = str(entry.get("current_balance_cad") or "")
    resulting = str(entry.get("resulting_balance_cad") or "")
    if not NUMBER_RE.fullmatch(current) or not NUMBER_RE.fullmatch(resulting):
        raise LoansError("Plan balances are invalid.")
    if action == STEFE_ACTION:
        description = clean_description(str(entry.get("description") or ""))
        rebuilt = {
            "tab": STEFE_SHEET_TITLE,
            "sheet_id": STEFE_SHEET_ID,
            "row": row,
            "range": f"{STEFE_SHEET_TITLE}!C{row}:E{row}",
            "date": entry_date,
            "date_cell": sheet_date_text(entry_date),
            "deduction_cad": deduction,
            "appended_amount_cad": negative_amount(deduction),
            "description": description,
            "total_formula": STEFE_TOTAL_FORMULA,
            "current_balance_cad": current,
            "resulting_balance_cad": resulting,
        }
        row_allowed = STEFE_FIRST_ROW <= row <= STEFE_LAST_ROW
    elif action == IN_LAWS_ACTION:
        description = clean_description(str(entry.get("description") or ""))
        rebuilt = {
            "tab": IN_LAWS_SHEET_TITLE,
            "sheet_id": IN_LAWS_SHEET_ID,
            "row": row,
            "range": f"{IN_LAWS_SHEET_TITLE}!A{row}:C{row}",
            "date": entry_date,
            "date_cell": sheet_date_text(entry_date),
            "deduction_cad": deduction,
            "appended_amount_cad": negative_amount(deduction),
            "description": description,
            "total_formula": IN_LAWS_TOTAL_FORMULA,
            "current_balance_cad": current,
            "resulting_balance_cad": resulting,
        }
        row_allowed = IN_LAWS_FIRST_ROW <= row <= IN_LAWS_LAST_ROW
    else:
        rebuilt = {
            "tab": SHEET_TITLE,
            "sheet_id": SHEET_ID,
            "row": row,
            "range": f"{SHEET_TITLE}!A{row}:B{row}",
            "date": entry_date,
            "date_cell": sheet_date_text(entry_date),
            "deduction_cad": deduction,
            "appended_amount_cad": negative_amount(deduction),
            "total_formula": TOTAL_FORMULA,
            "current_balance_cad": current,
            "resulting_balance_cad": resulting,
        }
        row_allowed = FIRST_ROW <= row <= LAST_ROW
    if rebuilt != entry or not row_allowed:
        raise LoansError("Plan entry failed the current allowlist validation.")
    if Decimal(current) + Decimal(rebuilt["appended_amount_cad"]) != Decimal(resulting):
        raise LoansError("Plan balance arithmetic does not hold.")
    if clean_source(str(plan.get("source") or "")) != plan.get("source"):
        raise LoansError("Plan source failed validation.")
    plan["sha256"] = saved
    return plan


def _same_live_state(item: dict[str, Any], planned: dict[str, Any]) -> bool:
    return all(
        str(item.get(live) or "") == str(planned.get(saved) or "")
        for live, saved in (
            ("id", "id"),
            ("modifiedDate", "modified_time"),
        )
    )


def recheck_live(drive, sheets, plan: dict[str, Any]) -> list[list[str]]:
    """Full identity and content recheck; returns the current FORMULA grid."""
    item = _verify_exact_file(drive, _drive_metadata(drive))
    if not item.get("editable"):
        raise LoansError("Google no longer permits editing the Loans spreadsheet.")
    if not _same_live_state(item, plan["file"]):
        raise LoansError("The live Loans spreadsheet changed after review. Stage a new plan.")
    action = plan["action"]
    if action == STEFE_ACTION:
        identity = read_stefe_identity(sheets)
        grid = read_stefe_grid(sheets)
    elif action == IN_LAWS_ACTION:
        identity = read_in_laws_identity(sheets)
        grid = read_in_laws_grid(sheets)
    else:
        identity = read_identity(sheets)
        grid = read_grid(sheets)
    if any(
        identity[key] != plan["file"][key]
        for key in ("spreadsheet_title", "locale", "time_zone", "tab", "sheet_id")
    ):
        raise LoansError("The live Sheets identity changed after review. Stage a new plan.")
    if content_sha256(grid) != plan["file"]["content_sha256"]:
        raise LoansError(f"The live {identity['tab']} values changed after review. Stage a new plan.")
    entry = plan["entry"]
    row = int(entry["row"])
    if any(cell.strip() for cell in grid[row - 1]):
        raise LoansError(f"{identity['tab']} row {row} is no longer blank. Stage a new plan.")
    if action == STEFE_ACTION:
        state = stefe_table_state(grid)
    elif action == IN_LAWS_ACTION:
        state = in_laws_table_state(grid)
    else:
        state = table_state(grid)
    if int(state["next_row"]) != row:
        raise LoansError(f"The {identity['tab']} next free row moved after review. Stage a new plan.")
    if format(Decimal(state["current_balance"]), "f") != str(entry["current_balance_cad"]):
        raise LoansError(f"The {identity['tab']} balance changed after review. Stage a new plan.")
    return grid


def _check_append_response(response: Any, entry: dict[str, Any]) -> None:
    if not isinstance(response, dict):
        raise LoansError("Sheets returned no usable append response.")
    if str(response.get("spreadsheetId") or "") != EXPECTED_FILE_ID:
        raise LoansError("The append response names a different spreadsheet.")
    updates = response.get("updates") or {}
    actual = str(updates.get("updatedRange") or "")
    normalized = actual.replace("'", "")
    expected_range = str(entry["range"])
    if normalized != expected_range:
        raise LoansError(
            f"Sheets wrote {actual or 'an unreported range'} instead of the planned "
            f"{expected_range}; reconciliation required."
        )
    expected_columns = 2 if entry["tab"] == SHEET_TITLE else 3
    if (
        int(updates.get("updatedRows") or 0) != 1
        or int(updates.get("updatedColumns") or 0) != expected_columns
        or int(updates.get("updatedCells") or 0) != expected_columns
    ):
        raise LoansError("The append touched more than the one planned row; reconciliation required.")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def command_check(_args: argparse.Namespace) -> None:
    drive = auth.drive_service()
    item = resolve_file(drive)
    sheets = sheets_service()
    identity = read_identity(sheets)
    grid = read_grid(sheets)
    state = table_state(grid)
    print(json.dumps({
        "status": "ACCESS_VERIFIED_READ_ONLY_CHECK",
        "account": auth.EXPECTED_ACCOUNT,
        "spreadsheet": SPREADSHEET_NAME,
        "path": " / ".join(EXPECTED_PARENT_PATH),
        "tab": identity["tab"],
        "sheet_id": identity["sheet_id"],
        "locale": identity["locale"],
        "time_zone": identity["time_zone"],
        "total_formula": TOTAL_FORMULA,
        "last_used_row": state["last_used_row"],
        "next_row": state["next_row"],
        "current_balance_cad": format(Decimal(state["current_balance"]), "f"),
        "can_edit": bool(item.get("editable")),
        "remote_write_performed": False,
    }, indent=2))


def command_stage(args: argparse.Namespace) -> None:
    entry_date = clean_date(args.date)
    amount_cad = clean_amount(args.amount)
    source = clean_source(args.source)
    drive = auth.drive_service()
    item = resolve_file(drive)
    sheets = sheets_service()
    identity = read_identity(sheets)
    grid = read_grid(sheets)
    entry = plan_entry(grid, entry_date, amount_cad)
    plan = build_plan(item, identity, grid, entry, source)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(str(plan["created_utc"])).strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{ACTION}_{str(plan['sha256'])[:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_receipt("loans_ccivs_payment_plan_staged", f"{path}; sha256={plan['sha256']}")
    print(json.dumps(staged_view(plan, path), indent=2, ensure_ascii=False))


def command_check_stefe(_args: argparse.Namespace) -> None:
    drive = auth.drive_service()
    item = resolve_file(drive)
    sheets = sheets_service()
    identity = read_stefe_identity(sheets)
    grid = read_stefe_grid(sheets)
    state = stefe_table_state(grid)
    print(json.dumps({
        "status": "ACCESS_VERIFIED_READ_ONLY_CHECK",
        "account": auth.EXPECTED_ACCOUNT,
        "spreadsheet": SPREADSHEET_NAME,
        "path": " / ".join(EXPECTED_PARENT_PATH),
        "tab": identity["tab"],
        "sheet_id": identity["sheet_id"],
        "locale": identity["locale"],
        "time_zone": identity["time_zone"],
        "total_formula": STEFE_TOTAL_FORMULA,
        "last_used_row": state["last_used_row"],
        "next_row": state["next_row"],
        "current_balance_cad": format(Decimal(state["current_balance"]), "f"),
        "can_edit": bool(item.get("editable")),
        "remote_write_performed": False,
    }, indent=2))


def command_stage_stefe(args: argparse.Namespace) -> None:
    entry_date = clean_date(args.date)
    amount_cad = clean_amount(args.amount)
    description = clean_description(args.description)
    source = clean_source(args.source)
    drive = auth.drive_service()
    item = resolve_file(drive)
    sheets = sheets_service()
    identity = read_stefe_identity(sheets)
    grid = read_stefe_grid(sheets)
    entry = plan_stefe_entry(grid, entry_date, amount_cad, description)
    plan = build_plan(item, identity, grid, entry, source, action=STEFE_ACTION)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(str(plan["created_utc"])).strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{STEFE_ACTION}_{str(plan['sha256'])[:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_receipt("loans_stefe_adjustment_plan_staged", f"{path}; sha256={plan['sha256']}")
    print(json.dumps(staged_view(plan, path), indent=2, ensure_ascii=False))


def command_check_in_laws(_args: argparse.Namespace) -> None:
    drive = auth.drive_service()
    item = resolve_file(drive)
    sheets = sheets_service()
    identity = read_in_laws_identity(sheets)
    grid = read_in_laws_grid(sheets)
    state = in_laws_table_state(grid)
    print(json.dumps({
        "status": "ACCESS_VERIFIED_READ_ONLY_CHECK",
        "account": auth.EXPECTED_ACCOUNT,
        "spreadsheet": SPREADSHEET_NAME,
        "path": " / ".join(EXPECTED_PARENT_PATH),
        "tab": identity["tab"],
        "sheet_id": identity["sheet_id"],
        "locale": identity["locale"],
        "time_zone": identity["time_zone"],
        "total_formula": IN_LAWS_TOTAL_FORMULA,
        "last_used_row": state["last_used_row"],
        "next_row": state["next_row"],
        "current_balance_cad": format(Decimal(state["current_balance"]), "f"),
        "can_edit": bool(item.get("editable")),
        "remote_write_performed": False,
    }, indent=2))


def command_stage_in_laws(args: argparse.Namespace) -> None:
    entry_date = clean_date(args.date)
    amount_cad = clean_amount(args.amount)
    description = clean_description(args.description)
    source = clean_source(args.source)
    drive = auth.drive_service()
    item = resolve_file(drive)
    sheets = sheets_service()
    identity = read_in_laws_identity(sheets)
    grid = read_in_laws_grid(sheets)
    entry = plan_in_laws_entry(grid, entry_date, amount_cad, description)
    plan = build_plan(item, identity, grid, entry, source, action=IN_LAWS_ACTION)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(str(plan["created_utc"])).strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{IN_LAWS_ACTION}_{str(plan['sha256'])[:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_receipt("loans_in_laws_adjustment_plan_staged", f"{path}; sha256={plan['sha256']}")
    print(json.dumps(staged_view(plan, path), indent=2, ensure_ascii=False))


def staged_view(plan: dict[str, Any], path: Path) -> dict[str, Any]:
    """Rachad-facing plan summary. The full digest is deliberately absent."""
    entry = plan["entry"]
    view = {
        "status": "STAGED_NOT_COMMITTED",
        "plan": str(path),
        "expires_utc": plan["expires_utc"],
        "spreadsheet": SPREADSHEET_NAME,
        "path": " / ".join(EXPECTED_PARENT_PATH),
        "tab": entry["tab"],
        "cell_range": entry["range"],
        "date": entry["date_cell"],
        "deduction_cad": entry["deduction_cad"],
        "value_written": entry["appended_amount_cad"],
        "current_balance_cad": entry["current_balance_cad"],
        "resulting_balance_cad": entry["resulting_balance_cad"],
        "source": plan["source"],
        "approval": approval_phrase(str(plan["sha256"])),
        "remote_write_performed": False,
    }
    if plan["action"] in {STEFE_ACTION, IN_LAWS_ACTION}:
        view["description"] = entry["description"]
    return view


def committed_view(plan: dict[str, Any], backup: Path) -> dict[str, Any]:
    entry = plan["entry"]
    view = {
        "status": "COMMITTED_AND_VERIFIED",
        "spreadsheet": SPREADSHEET_NAME,
        "path": " / ".join(EXPECTED_PARENT_PATH),
        "tab": entry["tab"],
        "cell_range": entry["range"],
        "date": entry["date_cell"],
        "value_written": entry["appended_amount_cad"],
        "resulting_balance_cad": entry["resulting_balance_cad"],
        "plan_reference": str(plan["sha256"])[:12],
        "replay_locked": True,
        "local_backup": str(backup),
    }
    if plan["action"] in {STEFE_ACTION, IN_LAWS_ACTION}:
        view["description"] = entry["description"]
    return view


def command_commit(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve()
    if PLAN_DIR.resolve() not in plan_path.parents:
        raise LoansError("Plan must be inside Dado's loans plan folder.")
    plan = load_plan(plan_path)
    digest = str(plan["sha256"])
    expected = approval_phrase(digest)
    if not secrets.compare_digest(str(args.approval).strip().casefold(), expected.casefold()):
        raise LoansError("Rachad must reply with the one-word approval: APPROVED.")
    lock = lock_path(digest)
    if lock.exists():
        raise LoansError("This plan has already entered commit and cannot be replayed.")
    drive = auth.drive_service()
    sheets = sheets_service()
    entry = plan["entry"]
    action = plan["action"]
    if action == STEFE_ACTION:
        tab_title = STEFE_SHEET_TITLE
        read_range = STEFE_READ_RANGE
        append_range = STEFE_APPEND_RANGE
        backup_label = "STEFE_C1_E212"
        receipt_prefix = "loans_stefe_adjustment"
    elif action == IN_LAWS_ACTION:
        tab_title = IN_LAWS_SHEET_TITLE
        read_range = IN_LAWS_READ_RANGE
        append_range = IN_LAWS_APPEND_RANGE
        backup_label = "IN_LAWS_A1_C234"
        receipt_prefix = "loans_in_laws_adjustment"
    else:
        tab_title = SHEET_TITLE
        read_range = READ_RANGE
        append_range = APPEND_RANGE
        backup_label = "CCIVS_A1_B60"
        receipt_prefix = "loans_ccivs_payment"
    before = recheck_live(drive, sheets, plan)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / (utc_now().strftime("%Y%m%dT%H%M%SZ_") + digest[:16] + f"_{backup_label}.json")
    backup.write_text(json.dumps({
        "spreadsheet_id": EXPECTED_FILE_ID,
        "tab": tab_title,
        "range": read_range,
        "value_render": "FORMULA",
        "captured_utc": utc_now().isoformat(),
        "content_sha256": content_sha256(before),
        "values": before,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write_lock(lock, {
        "plan_sha256": digest,
        "status": "in_flight",
        "started_utc": utc_now().isoformat(),
        "backup": str(backup),
    }, exclusive=True)

    write_attempted = False
    try:
        # Immediately before the single remote write, re-verify every piece of
        # identity and the exact staged content. Nothing is retried past here.
        if recheck_live(drive, sheets, plan) != before:
            raise LoansError(f"The live {tab_title} values changed immediately before the write.")
        if utc_now() >= datetime.fromisoformat(str(plan["expires_utc"])):
            raise LoansError("Plan expired immediately before the write.")
        row_values: list[Any] = [
            str(entry["date_cell"]),
            sheet_number(str(entry["appended_amount_cad"])),
        ]
        if action in {STEFE_ACTION, IN_LAWS_ACTION}:
            row_values.append(str(entry["description"]))
        write_attempted = True
        append_request = sheets.spreadsheets().values().append(
            spreadsheetId=EXPECTED_FILE_ID,
            range=append_range,
            valueInputOption="USER_ENTERED",
            insertDataOption="OVERWRITE",
            includeValuesInResponse=False,
            body={"values": [row_values]},
        )
        response = append_request.execute(num_retries=0)
        _check_append_response(response, entry)
        if action == STEFE_ACTION:
            verify_stefe_readback(before, read_stefe_grid(sheets), entry)
            identity_after = read_stefe_identity(sheets)
        elif action == IN_LAWS_ACTION:
            verify_in_laws_readback(before, read_in_laws_grid(sheets), entry)
            identity_after = read_in_laws_identity(sheets)
        else:
            verify_readback(before, read_grid(sheets), entry)
            identity_after = read_identity(sheets)
        after_item = _verify_exact_file(drive, _drive_metadata(drive))
        if after_item.get("id") != EXPECTED_FILE_ID or after_item.get("title") != SPREADSHEET_NAME:
            raise LoansError("The Drive identity no longer matches after the append.")
        if identity_after != {
            key: plan["file"][key]
            for key in ("spreadsheet_title", "locale", "time_zone", "tab", "sheet_id")
        }:
            raise LoansError("The Sheets identity no longer matches after the append.")
    except Exception as exc:
        status = "indeterminate" if write_attempted else "aborted_before_write"
        write_lock(lock, {
            "plan_sha256": digest,
            "status": status,
            "updated_utc": utc_now().isoformat(),
            "backup": str(backup),
            "reason": scrub(str(exc)),
        })
        append_receipt(
            f"{receipt_prefix}_{status}_no_retry",
            f"plan={plan_path}; sha256={digest}; backup={backup}",
        )
        if write_attempted:
            raise LoansError(
                f"The {tab_title} append failed or could not be verified. The plan is locked and will "
                f"not retry. Reconciliation required: compare the live {tab_title} tab against the "
                f"local backup {backup} and Google Sheets version history."
            ) from exc
        raise LoansError(
            "Stopped before writing anything; the spreadsheet is unchanged. The plan is locked "
            "and will not retry. Stage a new plan. Reason: " + scrub(str(exc))
        ) from exc

    write_lock(lock, {
        "plan_sha256": digest,
        "status": "committed_verified",
        "updated_utc": utc_now().isoformat(),
        "backup": str(backup),
        "row": int(entry["row"]),
        "resulting_balance_cad": str(entry["resulting_balance_cad"]),
    })
    append_receipt(
        f"{receipt_prefix}_committed_verified",
        f"plan={plan_path}; sha256={digest}; range={entry['range']}; backup={backup}",
    )
    print(json.dumps(committed_view(plan, backup), indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.set_defaults(func=command_check)
    check_stefe = commands.add_parser("check-stefe")
    check_stefe.set_defaults(func=command_check_stefe)
    check_in_laws = commands.add_parser("check-in-laws")
    check_in_laws.set_defaults(func=command_check_in_laws)
    stage = commands.add_parser("stage-ccivs-payment")
    stage.add_argument("--date", required=True)
    stage.add_argument("--amount", required=True)
    stage.add_argument("--source", required=True)
    stage.set_defaults(func=command_stage)
    stage_stefe = commands.add_parser("stage-stefe-adjustment")
    stage_stefe.add_argument("--date", required=True)
    stage_stefe.add_argument("--amount", required=True)
    stage_stefe.add_argument("--description", required=True)
    stage_stefe.add_argument("--source", required=True)
    stage_stefe.set_defaults(func=command_stage_stefe)
    stage_in_laws = commands.add_parser("stage-in-laws-adjustment")
    stage_in_laws.add_argument("--date", required=True)
    stage_in_laws.add_argument("--amount", required=True)
    stage_in_laws.add_argument("--description", required=True)
    stage_in_laws.add_argument("--source", required=True)
    stage_in_laws.set_defaults(func=command_stage_in_laws)
    commit = commands.add_parser("commit")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (LoansError, auth.InvestmentsAuthError, OSError, ValueError) as exc:
        print("ERROR: " + scrub(str(exc)), file=sys.stderr)
        return 1
    except Exception as exc:
        print("ERROR: Loans tool failed safely: " + scrub(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
