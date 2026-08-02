from __future__ import annotations

import argparse
import copy
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import google_investments_auth as auth
import google_loans_tool as tool


def serial(year: int, month: int, day: int) -> int:
    return (date(year, month, day) - tool.SHEETS_EPOCH).days


class FakeExecutable:
    def __init__(self, label: str, harness: "FakeGoogle", payload):
        self._label = label
        self._harness = harness
        self._payload = payload

    def execute(self, num_retries=None, **_kwargs):
        self._harness.calls.append((self._label, num_retries))
        value = self._payload() if callable(self._payload) else self._payload
        if isinstance(value, Exception):
            raise value
        return value


class FakeDriveFiles:
    def __init__(self, harness: "FakeGoogle"):
        self._harness = harness

    def get(self, fileId=None, supportsAllDrives=None, fields=None):
        return FakeExecutable(
            f"drive.files.get:{fileId}", self._harness,
            lambda: self._harness.drive_item(str(fileId)),
        )


class FakeDrive:
    def __init__(self, harness: "FakeGoogle"):
        self._harness = harness

    def files(self):
        return FakeDriveFiles(self._harness)


class FakeValues:
    def __init__(self, harness: "FakeGoogle"):
        self._harness = harness

    def get(self, spreadsheetId=None, range=None, majorDimension=None,
            valueRenderOption=None, dateTimeRenderOption=None):
        harness = self._harness
        harness.values_get_kwargs.append({
            "spreadsheetId": spreadsheetId, "range": range,
            "majorDimension": majorDimension, "valueRenderOption": valueRenderOption,
            "dateTimeRenderOption": dateTimeRenderOption,
        })
        return FakeExecutable("values.get", harness, lambda: {
            "range": str(range or "").replace("'", ""),
            "majorDimension": "ROWS",
            "values": copy.deepcopy(harness.grid),
        })

    def append(self, spreadsheetId=None, range=None, valueInputOption=None,
               insertDataOption=None, includeValuesInResponse=None, body=None):
        harness = self._harness
        harness.append_kwargs.append({
            "spreadsheetId": spreadsheetId, "range": range,
            "valueInputOption": valueInputOption, "insertDataOption": insertDataOption,
            "includeValuesInResponse": includeValuesInResponse, "body": copy.deepcopy(body),
        })
        return FakeExecutable("values.append", harness, lambda: harness.perform_append(body))


class FakeSpreadsheets:
    def __init__(self, harness: "FakeGoogle"):
        self._harness = harness

    def get(self, spreadsheetId=None, includeGridData=None, fields=None):
        return FakeExecutable(
            "spreadsheets.get", self._harness, lambda: self._harness.identity_payload()
        )

    def values(self):
        return FakeValues(self._harness)


class FakeSheets:
    def __init__(self, harness: "FakeGoogle"):
        self._harness = harness

    def spreadsheets(self):
        return FakeSpreadsheets(self._harness)


class FakeGoogle:
    """One consistent fake Drive + Sheets pair. It never touches the network."""

    def __init__(self, grid=None, *, mode="ccivs"):
        self.mode = mode
        if grid is None and mode == "stefe":
            grid = [
                [], [], [],
                ["Balance", tool.STEFE_TOTAL_FORMULA, ""],
                ["DATE", "Amount", "Description"],
                [serial(2026, 7, 1), 633, "Opening Balance"],
                [serial(2026, 7, 4), -108, "Kids Quran"],
                [serial(2026, 7, 9), -490, "Solar Panels"],
            ]
            while len(grid) < tool.STEFE_NOTE_ROW - 1:
                grid.append([])
            grid.append([tool.STEFE_NOTE, "", ""])
        self.grid = grid if grid is not None else [
            ["CCIVS", ""],
            [],
            ["Balance", "=SUM(B4:B60)"],
            [serial(2026, 1, 2), 20000],
        ]
        self.calls: list[tuple[str, int | None]] = []
        self.append_kwargs: list[dict] = []
        self.values_get_kwargs: list[dict] = []
        self.title = tool.SPREADSHEET_NAME
        self.mime = tool.SPREADSHEET_MIME
        self.locale = tool.EXPECTED_LOCALE
        self.time_zone = tool.EXPECTED_TIME_ZONE
        self.tab_title = tool.STEFE_SHEET_TITLE if mode == "stefe" else tool.SHEET_TITLE
        self.sheet_id = tool.STEFE_SHEET_ID if mode == "stefe" else tool.SHEET_ID
        self.editable = True
        self.modified = "2026-07-26T10:00:00.000Z"
        self.version = "412"
        self.parent_titles = dict(zip(tool.EXPECTED_PARENT_IDS, tool.EXPECTED_PARENT_PATH))
        self.append_error: Exception | None = None
        self.append_response = None
        self.append_row: int | None = None
        self.mutate_after_append = None

    # -- Drive -------------------------------------------------------------
    def drive_item(self, file_id: str) -> dict:
        if file_id == tool.EXPECTED_FILE_ID:
            return {
                "id": tool.EXPECTED_FILE_ID,
                "title": self.title,
                "mimeType": self.mime,
                "parents": [{"id": tool.EXPECTED_PARENT_IDS[-1]}],
                "modifiedDate": self.modified,
                "version": self.version,
                "editable": self.editable,
            }
        chain = list(tool.EXPECTED_PARENT_IDS)
        if file_id not in chain:
            raise AssertionError(f"unexpected Drive lookup: {file_id}")
        index = chain.index(file_id)
        parents = [{"id": chain[index - 1]}] if index > 0 else []
        return {"id": file_id, "title": self.parent_titles[file_id], "parents": parents}

    # -- Sheets ------------------------------------------------------------
    def identity_payload(self) -> dict:
        return {
            "spreadsheetId": tool.EXPECTED_FILE_ID,
            "properties": {
                "title": self.title, "locale": self.locale, "timeZone": self.time_zone,
            },
            "sheets": [
                {"properties": {"sheetId": 0, "title": "Sheet1"}},
                {"properties": {"sheetId": self.sheet_id, "title": self.tab_title}},
            ],
        }

    def next_free_row(self) -> int:
        if self.mode == "stefe":
            state = tool.stefe_table_state(tool._normalize_stefe_grid(self.grid))
        else:
            state = tool.table_state(tool._normalize_grid(self.grid))
        return int(state["next_row"])

    def perform_append(self, body):
        if self.append_error is not None:
            raise self.append_error
        row = self.append_row or self.next_free_row()
        values = list(body["values"][0])
        while len(self.grid) < row:
            self.grid.append([])
        width = 3 if self.mode == "stefe" else 2
        cells = list(self.grid[row - 1])
        while len(cells) < width:
            cells.append("")
        # Sheets stores a USER_ENTERED date as a serial number; prove the tool
        # reads that back correctly rather than only its own literal string.
        parsed = datetime.strptime(str(values[0]), "%m/%d/%Y").date()
        cells[0] = (parsed - tool.SHEETS_EPOCH).days
        cells[1] = values[1]
        if self.mode == "stefe":
            cells[2] = values[2]
        self.grid[row - 1] = cells
        if self.mutate_after_append is not None:
            self.mutate_after_append(self)
        if self.append_response is not None:
            return self.append_response
        updated = (
            f"Stefe!C{row}:E{row}" if self.mode == "stefe"
            else f"CCIVS!A{row}:B{row}"
        )
        table_range = (
            f"Stefe!C6:E{row - 1}" if self.mode == "stefe"
            else f"CCIVS!A4:B{row - 1}"
        )
        return {
            "spreadsheetId": tool.EXPECTED_FILE_ID,
            "tableRange": table_range,
            "updates": {
                "spreadsheetId": tool.EXPECTED_FILE_ID,
                "updatedRange": updated,
                "updatedRows": 1,
                "updatedColumns": width,
                "updatedCells": width,
            },
        }

    # -- Wiring ------------------------------------------------------------
    def drive_service(self, *_args, **_kwargs):
        return FakeDrive(self)

    def sheets_service(self, *_args, **_kwargs):
        return FakeSheets(self)

    def appends(self) -> int:
        return sum(1 for label, _ in self.calls if label == "values.append")


class LoansToolTestCase(unittest.TestCase):
    mode = "ccivs"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.plan_dir = base / "loans_plans"
        self.plan_dir.mkdir()
        self.backup_dir = base / "backups"
        self.receipts = base / "receipts.jsonl"
        for name, value in (
            ("PLAN_DIR", self.plan_dir),
            ("BACKUP_DIR", self.backup_dir),
            ("RECEIPTS", self.receipts),
        ):
            patcher = mock.patch.object(tool, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.google = FakeGoogle(mode=self.mode)
        for target, attribute, replacement in (
            (tool.auth, "drive_service", self.google.drive_service),
            (tool, "sheets_service", self.google.sheets_service),
        ):
            patcher = mock.patch.object(target, attribute, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def stage(self, *, entry_date="2026-07-26", amount="1000",
              source="Rachad instruction") -> Path:
        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            tool.command_stage(argparse.Namespace(
                date=entry_date, amount=amount, source=source
            ))
        self.staged_output = json.loads(buffer.getvalue())
        plans = sorted(self.plan_dir.glob("*.json"))
        self.assertEqual(len(plans), 1)
        return plans[0]

    def stage_stefe(self, *, entry_date="2026-07-31", amount="50",
                    description="Wissam", source="Rachad instruction") -> Path:
        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            tool.command_stage_stefe(argparse.Namespace(
                date=entry_date, amount=amount, description=description, source=source
            ))
        self.staged_output = json.loads(buffer.getvalue())
        plans = sorted(self.plan_dir.glob("*.json"))
        self.assertEqual(len(plans), 1)
        return plans[0]

    def commit(self, plan_path: Path, approval="APPROVED") -> dict:
        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            tool.command_commit(argparse.Namespace(plan=str(plan_path), approval=approval))
        return json.loads(buffer.getvalue())

    def locks(self) -> list[dict]:
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((self.plan_dir / ".commit-locks").glob("*.json"))
        ]


# --------------------------------------------------------------------------
# Constants, identity and path chain
# --------------------------------------------------------------------------


class ConstantsTests(LoansToolTestCase):
    def test_exact_constants_are_the_commissioned_ones(self):
        self.assertEqual(tool.EXPECTED_FILE_ID, "1WfstxvtOkfbX0zitJEirEUL94eYOo8Hm5mHOX_47TGQ")
        self.assertEqual(tool.SPREADSHEET_NAME, "Loans")
        self.assertEqual(tool.SPREADSHEET_MIME, "application/vnd.google-apps.spreadsheet")
        self.assertEqual(tool.SHEET_TITLE, "CCIVS")
        self.assertEqual(tool.SHEET_ID, 909361371)
        self.assertEqual(tool.EXPECTED_LOCALE, "en_US")
        self.assertEqual(tool.EXPECTED_TIME_ZONE, "America/Los_Angeles")
        self.assertEqual(tool.TOTAL_CELL, "B3")
        self.assertEqual(tool.TOTAL_FORMULA, "=SUM(B4:B60)")
        self.assertEqual((tool.FIRST_ROW, tool.LAST_ROW), (4, 60))
        self.assertEqual(tool.READ_RANGE, "'CCIVS'!A1:B60")
        self.assertEqual(tool.APPEND_RANGE, "'CCIVS'!A4:B60")
        self.assertEqual(tool.APPROVAL_WORD, "APPROVED")
        self.assertEqual(tool.PLAN_LIFETIME_HOURS, 24)

    def test_parent_chain_is_the_immutable_business_folder_chain(self):
        import google_investments_tool as investments

        self.assertEqual(tool.EXPECTED_PARENT_IDS, investments.EXPECTED_PARENT_IDS)
        self.assertEqual(tool.EXPECTED_PARENT_PATH, investments.EXPECTED_PARENT_PATH)
        self.assertEqual(tool.BUSINESS_FOLDER_ID, "12C-CPb_1PWt-WHTQOd3PDLeJ_IV9zSdw")
        self.assertEqual(tool.EXPECTED_PARENT_IDS[-1], tool.BUSINESS_FOLDER_ID)
        self.assertEqual(len(tool.EXPECTED_PARENT_IDS), 4)

    def test_authorization_reuses_the_validated_drive_only_credential(self):
        self.assertEqual(auth.DRIVE_SCOPE, "https://www.googleapis.com/auth/drive")
        source = Path(tool.__file__).read_text(encoding="utf-8")
        self.assertIn("import google_investments_auth as auth", source)
        for forbidden in ("SCOPES =", "InstalledAppFlow", "token.json", "grant.json",
                          "flow.run_local_server", "_save_grant", "gmail"):
            self.assertNotIn(forbidden, source)

    def test_live_identity_checks_reject_a_changed_workbook(self):
        drive = self.google.drive_service()
        sheets = self.google.sheets_service()
        self.assertEqual(tool.resolve_file(drive)["id"], tool.EXPECTED_FILE_ID)
        self.assertEqual(tool.read_identity(sheets)["sheet_id"], tool.SHEET_ID)

        for attribute, value, message in (
            ("title", "Loans Copy", "not the commissioned"),
            ("mime", "application/vnd.ms-excel", "not the commissioned"),
            ("editable", False, "cannot edit"),
        ):
            with self.subTest(attribute=attribute):
                original = getattr(self.google, attribute)
                setattr(self.google, attribute, value)
                try:
                    with self.assertRaisesRegex(tool.LoansError, message):
                        tool.resolve_file(self.google.drive_service())
                finally:
                    setattr(self.google, attribute, original)

        for attribute, value, message in (
            ("locale", "fr_CA", "locale changed"),
            ("time_zone", "America/Toronto", "time zone changed"),
            ("tab_title", "CCIVS2", "tab is missing"),
            ("sheet_id", 12345, "tab id changed"),
        ):
            with self.subTest(attribute=attribute):
                original = getattr(self.google, attribute)
                setattr(self.google, attribute, value)
                try:
                    with self.assertRaisesRegex(tool.LoansError, message):
                        tool.read_identity(self.google.sheets_service())
                finally:
                    setattr(self.google, attribute, original)

    def test_wrong_parent_folder_is_rejected(self):
        self.google.parent_titles = dict(self.google.parent_titles)
        self.google.parent_titles[tool.BUSINESS_FOLDER_ID] = "Somewhere Else"
        with self.assertRaisesRegex(tool.LoansError, "commissioned Drive path"):
            tool.resolve_file(self.google.drive_service())

    def test_grid_read_uses_formula_render_and_the_ccivs_range(self):
        tool.read_grid(self.google.sheets_service())
        kwargs = self.google.values_get_kwargs[-1]
        self.assertEqual(kwargs["range"], "'CCIVS'!A1:B60")
        self.assertEqual(kwargs["valueRenderOption"], "FORMULA")
        self.assertEqual(kwargs["spreadsheetId"], tool.EXPECTED_FILE_ID)


# --------------------------------------------------------------------------
# Validation and arithmetic
# --------------------------------------------------------------------------


class ValidationTests(unittest.TestCase):
    def test_amount_validation_and_negative_conversion(self):
        self.assertEqual(tool.clean_amount("1000"), "1000")
        self.assertEqual(tool.clean_amount("10.50"), "10.5")
        self.assertEqual(tool.negative_amount("1000"), "-1000")
        self.assertEqual(tool.negative_amount("10.50"), "-10.5")
        self.assertEqual(tool.sheet_number("-1000"), -1000)
        self.assertIsInstance(tool.sheet_number("-1000"), int)
        self.assertEqual(tool.sheet_number("-10.5"), -10.5)
        for bad in ("0", "-1", "-1000", "1.234", "=1000", "nan", "1e3", "100000001", " ", "1,000"):
            with self.subTest(bad=bad), self.assertRaises(tool.LoansError):
                tool.clean_amount(bad)

    def test_date_validation_and_cell_format(self):
        self.assertEqual(tool.clean_date("2026-07-26"), "2026-07-26")
        self.assertEqual(tool.sheet_date_text("2026-07-26"), "7/26/2026")
        self.assertEqual(tool.sheet_date_text("2026-11-05"), "11/5/2026")
        for bad in ("07/26/2026", "26-07-2026", "2026-13-01", "", "today"):
            with self.subTest(bad=bad), self.assertRaises(tool.LoansError):
                tool.clean_date(bad)

    def test_source_validation(self):
        self.assertEqual(tool.clean_source("  Rachad Telegram  "), "Rachad Telegram")
        for bad in ("", "   ", "x" * 501, "bad\x01source"):
            with self.subTest(bad=bad), self.assertRaises(tool.LoansError):
                tool.clean_source(bad)

    def test_cell_readers_handle_serials_strings_and_formulas(self):
        self.assertEqual(tool.cell_date(str(serial(2026, 7, 26))), date(2026, 7, 26))
        self.assertEqual(tool.cell_date("7/26/2026"), date(2026, 7, 26))
        self.assertEqual(tool.cell_date("2026-07-26"), date(2026, 7, 26))
        self.assertIsNone(tool.cell_date("=TODAY()"))
        self.assertIsNone(tool.cell_date(""))
        self.assertEqual(tool.cell_decimal("-1000"), tool.Decimal("-1000"))
        self.assertEqual(tool.cell_decimal("$1,000.50"), tool.Decimal("1000.50"))
        self.assertEqual(tool.cell_decimal("(250)"), tool.Decimal("-250"))
        self.assertIsNone(tool.cell_decimal("=SUM(B4:B60)"))
        self.assertIsNone(tool.cell_decimal("paid in full"))


class TableTests(unittest.TestCase):
    def grid(self, rows):
        return tool._normalize_grid(rows)

    def base_rows(self):
        return [
            ["CCIVS", ""],
            [],
            ["Balance", "=SUM(B4:B60)"],
            [serial(2026, 1, 2), 20000],
        ]

    def test_current_deduction_and_resulting_balance_fixture(self):
        entry = tool.plan_entry(self.grid(self.base_rows()), "2026-07-26", "1000")
        self.assertEqual(entry["current_balance_cad"], "20000")
        self.assertEqual(entry["deduction_cad"], "1000")
        self.assertEqual(entry["appended_amount_cad"], "-1000")
        self.assertEqual(entry["resulting_balance_cad"], "19000")
        self.assertEqual(entry["row"], 5)
        self.assertEqual(entry["range"], "CCIVS!A5:B5")
        self.assertEqual(entry["date_cell"], "7/26/2026")
        self.assertEqual(entry["tab"], "CCIVS")
        self.assertEqual(entry["sheet_id"], tool.SHEET_ID)
        self.assertEqual(entry["total_formula"], "=SUM(B4:B60)")

    def test_balance_sums_existing_negative_rows_with_decimal(self):
        rows = self.base_rows() + [
            [serial(2026, 2, 2), -5000.25],
            [serial(2026, 3, 2), -0.1],
            [serial(2026, 4, 2), -0.2],
        ]
        state = tool.table_state(self.grid(rows))
        self.assertEqual(state["current_balance"], tool.Decimal("14999.45"))
        self.assertEqual(state["next_row"], 8)
        self.assertEqual(state["numeric_rows"], 4)

    def test_duplicate_payment_is_rejected(self):
        rows = self.base_rows() + [[serial(2026, 7, 26), -1000]]
        with self.assertRaisesRegex(tool.LoansError, "already appears at CCIVS row 5"):
            tool.plan_entry(self.grid(rows), "2026-07-26", "1000")
        # A different date or a different amount is not a duplicate.
        self.assertEqual(tool.plan_entry(self.grid(rows), "2026-07-27", "1000")["row"], 6)
        self.assertEqual(tool.plan_entry(self.grid(rows), "2026-07-26", "1500")["row"], 6)

    def test_duplicate_detection_also_sees_a_text_date_cell(self):
        rows = self.base_rows() + [["7/26/2026", -1000]]
        with self.assertRaisesRegex(tool.LoansError, "already appears"):
            tool.plan_entry(self.grid(rows), "2026-07-26", "1000")

    def test_changed_total_formula_is_rejected(self):
        rows = self.base_rows()
        rows[2] = ["Balance", "=SUM(B4:B61)"]
        with self.assertRaisesRegex(tool.LoansError, "total formula"):
            tool.table_state(self.grid(rows))
        rows[2] = ["Balance", ""]
        with self.assertRaisesRegex(tool.LoansError, "total formula"):
            tool.table_state(self.grid(rows))

    def test_formula_in_the_amount_column_is_rejected(self):
        rows = self.base_rows() + [[serial(2026, 2, 2), "=B4*2"]]
        with self.assertRaisesRegex(tool.LoansError, "B5 holds a formula"):
            tool.table_state(self.grid(rows))

    def test_non_numeric_amount_is_rejected(self):
        rows = self.base_rows() + [[serial(2026, 2, 2), "paid"]]
        with self.assertRaisesRegex(tool.LoansError, "B5 is not a plain number"):
            tool.table_state(self.grid(rows))

    def test_full_table_through_row_60_is_rejected(self):
        rows = self.base_rows()
        for index in range(5, 61):
            rows.append([serial(2026, 1, 2) + index, -1])
        grid = self.grid(rows)
        self.assertEqual(tool.table_state(grid)["next_row"], 61)
        with self.assertRaisesRegex(tool.LoansError, "full through row 60"):
            tool.plan_entry(grid, "2026-07-26", "1000")

    def test_row_60_is_still_usable_when_it_is_the_only_blank_row(self):
        rows = self.base_rows()
        for index in range(5, 60):
            rows.append([serial(2026, 1, 2) + index, -1])
        entry = tool.plan_entry(self.grid(rows), "2026-07-26", "1000")
        self.assertEqual(entry["row"], 60)
        self.assertEqual(entry["range"], "CCIVS!A60:B60")

    def test_next_row_follows_the_last_used_row_across_a_gap(self):
        rows = self.base_rows() + [[], [], [serial(2026, 5, 2), -100]]
        state = tool.table_state(self.grid(rows))
        self.assertEqual(state["last_used_row"], 7)
        self.assertEqual(state["next_row"], 8)

    def test_short_grid_is_rejected(self):
        with self.assertRaisesRegex(tool.LoansError, "rows 1 to 60"):
            tool.check_layout([["", ""]] * 59)

    def test_readback_verification_catches_collateral_damage(self):
        before = self.grid(self.base_rows())
        entry = tool.plan_entry(before, "2026-07-26", "1000")
        good = [list(row) for row in before]
        good[4] = [str(serial(2026, 7, 26)), "-1000"]
        tool.verify_readback(before, good, entry)

        wrong_amount = [list(row) for row in good]
        wrong_amount[4] = [str(serial(2026, 7, 26)), "-100"]
        with self.assertRaisesRegex(tool.LoansError, "B5 does not read back"):
            tool.verify_readback(before, wrong_amount, entry)

        wrong_date = [list(row) for row in good]
        wrong_date[4] = [str(serial(2026, 7, 27)), "-1000"]
        with self.assertRaisesRegex(tool.LoansError, "A5 does not read back"):
            tool.verify_readback(before, wrong_date, entry)

        shifted_total = [list(row) for row in good]
        shifted_total[2] = ["Balance", "=SUM(B4:B61)"]
        with self.assertRaisesRegex(tool.LoansError, "total formula"):
            tool.verify_readback(before, shifted_total, entry)

        clobbered = [list(row) for row in good]
        clobbered[3] = [str(serial(2026, 1, 2)), "999"]
        with self.assertRaisesRegex(tool.LoansError, "row 4 changed"):
            tool.verify_readback(before, clobbered, entry)


# --------------------------------------------------------------------------
# Plan discipline
# --------------------------------------------------------------------------


class PlanTests(LoansToolTestCase):
    def valid_plan(self) -> dict:
        grid = tool._normalize_grid(self.google.grid)
        entry = tool.plan_entry(grid, "2026-07-26", "1000")
        item = self.google.drive_item(tool.EXPECTED_FILE_ID)
        identity = tool.read_identity(self.google.sheets_service())
        return tool.build_plan(item, identity, grid, entry, "Rachad instruction")

    def write(self, plan: dict) -> Path:
        path = self.plan_dir / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return path

    def reseal(self, plan: dict) -> dict:
        core = dict(plan)
        core.pop("sha256", None)
        return {**core, "sha256": tool.digest_for(core)}

    def test_plan_round_trip_and_tamper_detection(self):
        plan = self.valid_plan()
        path = self.write(plan)
        self.assertEqual(tool.load_plan(path)["sha256"], plan["sha256"])
        tampered = copy.deepcopy(plan)
        tampered["entry"]["deduction_cad"] = "9000"
        self.write(tampered)
        with self.assertRaisesRegex(tool.LoansError, "hash check failed"):
            tool.load_plan(path)

    def test_expired_and_future_plans_are_refused(self):
        created = datetime.now(timezone.utc) - timedelta(hours=25)
        plan = self.valid_plan()
        plan["created_utc"] = created.isoformat()
        plan["expires_utc"] = (created + timedelta(hours=24)).isoformat()
        with self.assertRaisesRegex(tool.LoansError, "expired"):
            tool.load_plan(self.write(self.reseal(plan)))

        future = datetime.now(timezone.utc) + timedelta(hours=2)
        plan = self.valid_plan()
        plan["created_utc"] = future.isoformat()
        plan["expires_utc"] = (future + timedelta(hours=24)).isoformat()
        with self.assertRaisesRegex(tool.LoansError, "future"):
            tool.load_plan(self.write(self.reseal(plan)))

    def test_plan_lifetime_must_be_exactly_24_hours(self):
        created = datetime.now(timezone.utc)
        plan = self.valid_plan()
        plan["created_utc"] = created.isoformat()
        plan["expires_utc"] = (created + timedelta(hours=72)).isoformat()
        with self.assertRaisesRegex(tool.LoansError, "exactly 24 hours"):
            tool.load_plan(self.write(self.reseal(plan)))

    def test_closed_schema_rejects_added_and_missing_fields(self):
        plan = self.valid_plan()
        plan["extra"] = "surprise"
        with self.assertRaisesRegex(tool.LoansError, "closed schema"):
            tool.load_plan(self.write(self.reseal(plan)))

        plan = self.valid_plan()
        plan.pop("source")
        with self.assertRaisesRegex(tool.LoansError, "closed schema"):
            tool.load_plan(self.write(self.reseal(plan)))

        plan = self.valid_plan()
        plan["file"]["surprise"] = 1
        with self.assertRaisesRegex(tool.LoansError, "commissioned Loans spreadsheet"):
            tool.load_plan(self.write(self.reseal(plan)))

    def test_plan_must_target_the_commissioned_file_and_tab(self):
        for field, value in (
            ("id", "someOtherFileId"),
            ("name", "Loans Copy"),
            ("mime_type", "application/vnd.ms-excel"),
            ("locale", "fr_CA"),
            ("time_zone", "America/Toronto"),
            ("tab", "Sheet1"),
            ("sheet_id", 1),
            ("content_sha256", "not-a-digest"),
        ):
            with self.subTest(field=field):
                plan = self.valid_plan()
                plan["file"][field] = value
                with self.assertRaisesRegex(tool.LoansError, "commissioned Loans spreadsheet"):
                    tool.load_plan(self.write(self.reseal(plan)))

    def test_plan_entry_allowlist_and_arithmetic(self):
        plan = self.valid_plan()
        plan["entry"]["row"] = 61
        with self.assertRaisesRegex(tool.LoansError, "allowlist"):
            tool.load_plan(self.write(self.reseal(plan)))

        plan = self.valid_plan()
        plan["entry"]["appended_amount_cad"] = "1000"
        with self.assertRaisesRegex(tool.LoansError, "allowlist"):
            tool.load_plan(self.write(self.reseal(plan)))

        plan = self.valid_plan()
        plan["entry"]["resulting_balance_cad"] = "20000"
        with self.assertRaisesRegex(tool.LoansError, "arithmetic"):
            tool.load_plan(self.write(self.reseal(plan)))

        plan = self.valid_plan()
        plan["nonce"] = "short"
        with self.assertRaisesRegex(tool.LoansError, "nonce"):
            tool.load_plan(self.write(self.reseal(plan)))

    def test_approval_is_one_plain_word_and_the_digest_stays_internal(self):
        digest = "f" * 64
        self.assertEqual(tool.approval_phrase(digest), "APPROVED")
        plan = self.valid_plan()
        staged = json.dumps(tool.staged_view(plan, self.plan_dir / "plan.json"))
        committed = json.dumps(tool.committed_view(plan, self.plan_dir / "backup.json"))
        self.assertEqual(staged.count(plan["sha256"]), 0)
        self.assertEqual(committed.count(plan["sha256"]), 0)
        self.assertIn('"approval": "APPROVED"', staged)

    def test_replay_lock_is_digest_keyed_and_exclusive(self):
        digest = "a" * 64
        first = tool.lock_path(digest)
        self.assertEqual(first, tool.lock_path(digest))
        self.assertNotEqual(first, tool.lock_path("b" * 64))
        tool.write_lock(first, {"status": "in_flight"}, exclusive=True)
        with self.assertRaisesRegex(tool.LoansError, "cannot be replayed"):
            tool.write_lock(tool.lock_path(digest), {"status": "in_flight"}, exclusive=True)
        for bad in ("", "zz", "A" * 64, "a" * 63):
            with self.subTest(bad=bad), self.assertRaises(tool.LoansError):
                tool.lock_path(bad)


# --------------------------------------------------------------------------
# Stefe fixed-table extension
# --------------------------------------------------------------------------


class StefeFlowTests(LoansToolTestCase):
    mode = "stefe"

    def test_exact_stefe_constants_and_layout(self):
        self.assertEqual(tool.STEFE_SHEET_TITLE, "Stefe")
        self.assertEqual(tool.STEFE_SHEET_ID, 396384971)
        self.assertEqual(tool.STEFE_READ_RANGE, "'Stefe'!C1:E44")
        self.assertEqual(tool.STEFE_APPEND_RANGE, "'Stefe'!C6:E43")
        self.assertEqual(tool.STEFE_TOTAL_FORMULA, "=SUM(D6:D212)")
        grid = tool._normalize_stefe_grid(self.google.grid)
        state = tool.stefe_table_state(grid)
        self.assertEqual(state["next_row"], 9)
        self.assertEqual(state["current_balance"], tool.Decimal("35"))

    def test_stefe_plan_is_exact_and_duplicate_guarded(self):
        grid = tool._normalize_stefe_grid(self.google.grid)
        entry = tool.plan_stefe_entry(grid, "2026-07-31", "50", "Wissam")
        self.assertEqual(entry["range"], "Stefe!C9:E9")
        self.assertEqual(entry["date_cell"], "7/31/2026")
        self.assertEqual(entry["appended_amount_cad"], "-50")
        self.assertEqual(entry["description"], "Wissam")
        self.assertEqual(entry["current_balance_cad"], "35")
        self.assertEqual(entry["resulting_balance_cad"], "-15")
        duplicate = tool.expected_stefe_grid_after(grid, entry)
        with self.assertRaisesRegex(tool.LoansError, "already appears"):
            tool.plan_stefe_entry(duplicate, "2026-07-31", "50", "wissam")
        for bad in ("", "=IMPORTXML('x')", "+cmd", "x\nnext"):
            with self.subTest(bad=bad), self.assertRaises(tool.LoansError):
                tool.clean_description(bad)

    def test_stefe_check_and_stage_are_read_only(self):
        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            tool.command_check_stefe(argparse.Namespace())
        check = json.loads(buffer.getvalue())
        self.assertEqual(check["tab"], "Stefe")
        self.assertEqual(check["next_row"], 9)
        self.assertEqual(check["current_balance_cad"], "35")
        self.assertFalse(check["remote_write_performed"])
        self.assertEqual(self.google.appends(), 0)

        path = self.stage_stefe()
        self.assertEqual(self.google.appends(), 0)
        self.assertEqual(self.staged_output["cell_range"], "Stefe!C9:E9")
        self.assertEqual(self.staged_output["value_written"], "-50")
        self.assertEqual(self.staged_output["description"], "Wissam")
        self.assertEqual(self.staged_output["resulting_balance_cad"], "-15")
        plan = tool.load_plan(path)
        self.assertEqual(plan["action"], tool.STEFE_ACTION)
        self.assertEqual(plan["entry"]["description"], "Wissam")

    def test_stefe_commit_appends_one_row_and_verifies_all_three_cells(self):
        path = self.stage_stefe()
        result = self.commit(path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["cell_range"], "Stefe!C9:E9")
        self.assertEqual(result["description"], "Wissam")
        self.assertEqual(result["resulting_balance_cad"], "-15")
        self.assertEqual(self.google.appends(), 1)
        kwargs = self.google.append_kwargs[0]
        self.assertEqual(kwargs["range"], "'Stefe'!C6:E43")
        self.assertEqual(kwargs["body"], {"values": [["7/31/2026", -50, "Wissam"]]})
        locks = self.locks()
        self.assertEqual(locks[0]["status"], "committed_verified")
        backup = json.loads(Path(locks[0]["backup"]).read_text(encoding="utf-8"))
        self.assertEqual(backup["range"], "'Stefe'!C1:E44")
        self.assertEqual(len(backup["values"]), 44)
        self.assertIn("loans_stefe_adjustment_committed_verified",
                      self.receipts.read_text(encoding="utf-8"))

    def test_stefe_layout_drift_and_collateral_damage_fail_closed(self):
        grid = tool._normalize_stefe_grid(self.google.grid)
        for row_index, column_index, value, message in (
            (3, 1, "=SUM(D6:D211)", "total formula"),
            (4, 2, "Memo", "headers changed"),
            (43, 0, "changed", "note changed"),
        ):
            changed = copy.deepcopy(grid)
            changed[row_index][column_index] = value
            with self.subTest(message=message), self.assertRaisesRegex(tool.LoansError, message):
                tool.stefe_table_state(changed)
        entry = tool.plan_stefe_entry(grid, "2026-07-31", "50", "Wissam")
        after = tool.expected_stefe_grid_after(grid, entry)
        after[6][2] = "Changed historical description"
        with self.assertRaisesRegex(tool.LoansError, "row 7 changed"):
            tool.verify_stefe_readback(grid, after, entry)


# --------------------------------------------------------------------------
# Static write-surface assertions
# --------------------------------------------------------------------------


class WriteSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.source = Path(tool.__file__).read_text(encoding="utf-8")

    def test_exactly_one_values_append_write_surface(self):
        self.assertEqual(self.source.count("spreadsheets().values().append("), 1)
        self.assertEqual(self.source.count(".values().append("), 1)
        self.assertEqual(self.source.count("append_request"), 2)
        self.assertIn('append_range = STEFE_APPEND_RANGE if is_stefe else APPEND_RANGE', self.source)
        self.assertIn('range=append_range,', self.source)
        self.assertIn('valueInputOption="USER_ENTERED"', self.source)
        self.assertIn('insertDataOption="OVERWRITE"', self.source)

    def test_no_other_sheets_or_drive_write_call_exists(self):
        for forbidden in (
            ".values().update(", ".values().clear(", ".values().batchUpdate(",
            ".values().batchClear(", ".values().batchUpdateByDataFilter(",
            "spreadsheets().batchUpdate(", "spreadsheets().create(",
            "spreadsheets().sheets(", "developerMetadata",
            "files().update(", "files().insert(", "files().patch(",
            "files().copy(", "files().delete(", "files().trash(", "files().touch(",
            "permissions()", "revisions()", "MediaIoBaseUpload", "MediaFileUpload",
            "http.request(", "requests.post", "requests.put",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)

    def test_the_single_write_never_retries(self):
        self.assertEqual(self.source.count("num_retries=0"), 1)
        self.assertIn("response = append_request.execute(num_retries=0)", self.source)

    def test_only_drive_reads_are_used_for_identity(self):
        self.assertEqual(self.source.count("drive.files().get("), 2)


# --------------------------------------------------------------------------
# End-to-end with fakes; no live call is ever made
# --------------------------------------------------------------------------


class CommitFlowTests(LoansToolTestCase):
    def test_check_is_read_only(self):
        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            tool.command_check(argparse.Namespace())
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["status"], "ACCESS_VERIFIED_READ_ONLY_CHECK")
        self.assertEqual(payload["current_balance_cad"], "20000")
        self.assertEqual(payload["next_row"], 5)
        self.assertFalse(payload["remote_write_performed"])
        self.assertEqual(self.google.appends(), 0)

    def test_stage_writes_a_plan_and_performs_no_remote_write(self):
        path = self.stage()
        self.assertEqual(self.google.appends(), 0)
        self.assertEqual(self.staged_output["status"], "STAGED_NOT_COMMITTED")
        self.assertEqual(self.staged_output["approval"], "APPROVED")
        self.assertEqual(self.staged_output["value_written"], "-1000")
        self.assertEqual(self.staged_output["current_balance_cad"], "20000")
        self.assertEqual(self.staged_output["resulting_balance_cad"], "19000")
        self.assertEqual(self.staged_output["cell_range"], "CCIVS!A5:B5")
        self.assertFalse(self.staged_output["remote_write_performed"])
        plan = tool.load_plan(path)
        self.assertEqual(plan["entry"]["appended_amount_cad"], "-1000")
        self.assertEqual(self.locks(), [])

    def test_commit_preflights_locks_appends_once_and_verifies(self):
        path = self.stage()
        payload = self.commit(path, approval="  approved \n")
        self.assertEqual(payload["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(payload["value_written"], "-1000")
        self.assertEqual(payload["resulting_balance_cad"], "19000")
        self.assertEqual(payload["cell_range"], "CCIVS!A5:B5")
        self.assertTrue(payload["replay_locked"])

        labels = [label for label, _ in self.google.calls]
        self.assertEqual(labels.count("values.append"), 1)
        index = labels.index("values.append")
        # Identity, sheet identity and the values grid are all re-read after the
        # lock and immediately before the single write.
        self.assertIn("spreadsheets.get", labels[:index])
        self.assertIn("values.get", labels[:index])
        self.assertIn("values.get", labels[index + 1:])
        self.assertEqual([retries for label, retries in self.google.calls
                          if label == "values.append"], [0])

        kwargs = self.google.append_kwargs[0]
        self.assertEqual(kwargs["spreadsheetId"], tool.EXPECTED_FILE_ID)
        self.assertEqual(kwargs["range"], "'CCIVS'!A4:B60")
        self.assertEqual(kwargs["valueInputOption"], "USER_ENTERED")
        self.assertEqual(kwargs["insertDataOption"], "OVERWRITE")
        self.assertEqual(kwargs["body"], {"values": [["7/26/2026", -1000]]})

        locks = self.locks()
        self.assertEqual(len(locks), 1)
        self.assertEqual(locks[0]["status"], "committed_verified")
        backup = json.loads(Path(locks[0]["backup"]).read_text(encoding="utf-8"))
        self.assertEqual(backup["range"], "'CCIVS'!A1:B60")
        self.assertEqual(len(backup["values"]), 60)
        self.assertEqual(backup["values"][4], ["", ""])
        self.assertIn("loans_ccivs_payment_committed_verified",
                      self.receipts.read_text(encoding="utf-8"))

    def test_lock_is_taken_before_the_write_and_blocks_replay(self):
        path = self.stage()
        self.commit(path)
        with self.assertRaisesRegex(tool.LoansError, "already entered commit"):
            self.commit(path)
        self.assertEqual(self.google.appends(), 1)

    def test_wrong_approval_word_never_reaches_the_write(self):
        path = self.stage()
        for bad in ("approve", "yes", "APPROVED NOW", "", "OK"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(tool.LoansError, "one-word approval"):
                    self.commit(path, approval=bad)
        self.assertEqual(self.google.appends(), 0)
        self.assertEqual(self.locks(), [])

    def test_plan_outside_the_plan_folder_is_refused(self):
        path = self.stage()
        outside = Path(self._tmp.name) / "plan.json"
        outside.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(tool.LoansError, "inside Dado's loans plan folder"):
            self.commit(outside)
        self.assertEqual(self.google.appends(), 0)

    def test_content_change_after_staging_aborts_before_any_write(self):
        path = self.stage()
        self.google.grid.append([serial(2026, 7, 20), -250])
        with self.assertRaisesRegex(tool.LoansError, "changed after review"):
            self.commit(path)
        self.assertEqual(self.google.appends(), 0)
        self.assertEqual(self.locks(), [])

    def test_drive_version_only_change_does_not_block_exact_cell_content(self):
        path = self.stage()
        self.google.version = "413"
        self.commit(path)
        self.assertEqual(self.google.appends(), 1)

    def test_drive_modified_time_change_after_staging_aborts_before_any_write(self):
        path = self.stage()
        self.google.modified = "2026-07-26T11:00:00.000Z"
        with self.assertRaisesRegex(tool.LoansError, "changed after review"):
            self.commit(path)
        self.assertEqual(self.google.appends(), 0)

    def test_failed_append_is_never_retried_and_is_marked_indeterminate(self):
        path = self.stage()
        self.google.append_error = RuntimeError("backend error 503")
        with self.assertRaisesRegex(tool.LoansError, "Reconciliation required"):
            self.commit(path)
        self.assertEqual(self.google.appends(), 1)
        locks = self.locks()
        self.assertEqual(len(locks), 1)
        self.assertEqual(locks[0]["status"], "indeterminate")
        self.assertTrue(Path(locks[0]["backup"]).exists())
        self.assertIn("loans_ccivs_payment_indeterminate_no_retry",
                      self.receipts.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(tool.LoansError, "already entered commit"):
            self.commit(path)
        self.assertEqual(self.google.appends(), 1)

    def test_unexpected_updated_range_is_indeterminate_without_retry(self):
        path = self.stage()
        self.google.append_row = 6
        self.google.append_response = {
            "spreadsheetId": tool.EXPECTED_FILE_ID,
            "updates": {
                "updatedRange": "CCIVS!A6:B6",
                "updatedRows": 1, "updatedColumns": 2, "updatedCells": 2,
            },
        }
        with self.assertRaisesRegex(tool.LoansError, "Reconciliation required"):
            self.commit(path)
        self.assertEqual(self.google.appends(), 1)
        self.assertEqual(self.locks()[0]["status"], "indeterminate")

    def test_multi_row_append_response_is_refused(self):
        path = self.stage()
        self.google.append_response = {
            "spreadsheetId": tool.EXPECTED_FILE_ID,
            "updates": {
                "updatedRange": "CCIVS!A5:B5",
                "updatedRows": 2, "updatedColumns": 2, "updatedCells": 4,
            },
        }
        with self.assertRaisesRegex(tool.LoansError, "Reconciliation required"):
            self.commit(path)
        self.assertEqual(self.locks()[0]["status"], "indeterminate")

    def test_quoted_updated_range_from_sheets_is_accepted(self):
        path = self.stage()
        self.google.append_response = {
            "spreadsheetId": tool.EXPECTED_FILE_ID,
            "updates": {
                "updatedRange": "'CCIVS'!A5:B5",
                "updatedRows": 1, "updatedColumns": 2, "updatedCells": 2,
            },
        }
        self.assertEqual(self.commit(path)["status"], "COMMITTED_AND_VERIFIED")

    def test_readback_mismatch_is_indeterminate_without_retry(self):
        path = self.stage()

        def corrupt(harness):
            harness.grid[2] = ["Balance", "=SUM(B4:B61)"]

        self.google.mutate_after_append = corrupt
        with self.assertRaisesRegex(tool.LoansError, "Reconciliation required"):
            self.commit(path)
        self.assertEqual(self.google.appends(), 1)
        self.assertEqual(self.locks()[0]["status"], "indeterminate")

    def test_errors_never_leak_tokens(self):
        message = "refresh_token: 1//0gSECRETVALUE and ya29.AnotherSecretValue end"
        cleaned = tool.scrub(message)
        self.assertNotIn("1//0gSECRETVALUE", cleaned)
        self.assertNotIn("ya29.AnotherSecretValue", cleaned)
        self.assertIn("[REDACTED]", cleaned)


if __name__ == "__main__":
    unittest.main()
