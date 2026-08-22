"""Independent safety tests for the Zoho Books invoice revision tool.

These tests never touch live Zoho: every read transport is patched and the
OAuth write transport is patched to fail loudly unless a test explicitly opts
in to a simulated write.
"""
from __future__ import annotations

import argparse
import copy
from datetime import timedelta
from decimal import Decimal
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

import zoho_invoice_revision_tool as revision_tool
import zoho_tool

HERE = Path(__file__).resolve().parent

ORGANIZATION = {
    "organization_id": "110002157575",
    "name": "FRP DEPOTS",
    "currency_code": "CAD",
}
INVOICE_ID = "96274000001559012"
INVOICE_NUMBER = "INV-000051"
OLD_CUSTOMER_ID = "96274000000123001"
NEW_CUSTOMER_ID = "96274000000999001"
OLD_BILL_ADDRESS = "96274000000123005"
OLD_SHIP_ADDRESS = "96274000000123006"
NEW_BILL_ADDRESS = "96274000000999005"
NEW_SHIP_ADDRESS = "96274000000999006"
LINE_ONE = "96274000001559101"
LINE_TWO = "96274000001559102"
ITEM_ONE = "96274000000523063"
ITEM_TWO = "96274000000523031"
TAX_ID = "96274000000000211"
SOURCE = "Client email 2026-08-10 10:14, thread 'Revised PO'"


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def fake_line(
    line_item_id: str = LINE_ONE,
    item_id: str = ITEM_ONE,
    quantity: float = 2.0,
    rate: float = 500.0,
    **overrides: object,
) -> dict:
    line = {
        "line_item_id": line_item_id,
        "item_id": item_id,
        "name": "FRP FW PIPE 8in",
        "description": "8 inch filament wound pipe",
        "item_order": 0,
        "unit": "pcs",
        "quantity": quantity,
        "rate": rate,
        "discount": 0,
        "tax_id": "",
        "tax_percentage": 0,
        "tax_name": "",
        "account_id": "96274000000000346",
        "item_total": round(quantity * rate, 2),
    }
    line.update(overrides)
    return line


def fake_invoice(**overrides: object) -> dict:
    lines = [
        fake_line(),
        fake_line(
            line_item_id=LINE_TWO, item_id=ITEM_TWO, quantity=1.0, rate=250.0,
            name="FNPT Coupling", description="coupling", item_order=1,
        ),
    ]
    sub_total = sum(line["item_total"] for line in lines)
    invoice = {
        "invoice_id": INVOICE_ID,
        "invoice_number": INVOICE_NUMBER,
        "status": "sent",
        "customer_id": OLD_CUSTOMER_ID,
        "customer_name": "Existing Marine Ltd",
        "contact_persons": [],
        "reference_number": "",
        "date": "2026-07-01",
        "due_date": "2026-12-31",
        "currency_id": "96274000000000097",
        "currency_code": "CAD",
        "currency_symbol": "$",
        "exchange_rate": 1.0,
        "notes": "Thank you for your business.",
        "terms": "Net 30",
        "discount_type": "item_level",
        "is_inclusive_tax": False,
        "price_precision": 2,
        "shipping_charge": 0.0,
        "adjustment": 0.0,
        "sub_total": sub_total,
        "tax_total": 0.0,
        "total": sub_total,
        "balance": sub_total,
        "payment_made": 0.0,
        "credits_applied": 0.0,
        "write_off_amount": 0.0,
        "payments": [],
        "creditnotes": [],
        "packages": [],
        "salesorder_id": "",
        "recurring_invoice_id": "",
        "shipping_status": "",
        "is_emailed": True,
        "template_id": "96274000000000123",
        "custom_fields": [{"customfield_id": "96274000001000001", "value": "Marine"}],
        "billing_address": {
            "address_id": OLD_BILL_ADDRESS, "address": "1 Old Street", "city": "Montreal",
        },
        "shipping_address": {
            "address_id": OLD_SHIP_ADDRESS, "address": "1 Old Yard", "city": "Montreal",
        },
        "line_items": lines,
        "last_modified_time": "2026-08-01T09:00:00-0400",
        "invoice_url": "https://example.invalid/inv/1",
    }
    invoice.update(overrides)
    return invoice


def fake_contact(
    contact_id: str = NEW_CUSTOMER_ID,
    name: str = "SHM Marine Constructors JV",
    bill: str = NEW_BILL_ADDRESS,
    ship: str = NEW_SHIP_ADDRESS,
    **overrides: object,
) -> dict:
    contact = {
        "contact_id": contact_id,
        "contact_name": name,
        "company_name": name,
        "currency_code": "CAD",
        "currency_id": "96274000000000097",
        "status": "active",
        "contact_type": "customer",
        "billing_address": {"address_id": bill, "address": "9 New Pier", "city": "Quebec"},
        "shipping_address": {"address_id": ship, "address": "9 New Yard", "city": "Quebec"},
    }
    contact.update(overrides)
    return contact


def change(value: object, source: str = SOURCE, **extra: object) -> dict:
    envelope = {"value": value, "source": source}
    envelope.update(extra)
    return envelope


class RevisionToolTestCase(unittest.TestCase):
    """Shared harness. No live Zoho call can escape it."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.root = Path(self.temp.name).resolve()
        self.plan_dir = self.root / "zoho_invoice_revision_plans"
        self.plan_dir.mkdir()
        self.counter = 0
        self.invoice = fake_invoice()
        self.contacts = {
            OLD_CUSTOMER_ID: fake_contact(
                contact_id=OLD_CUSTOMER_ID, name="Existing Marine Ltd",
                bill=OLD_BILL_ADDRESS, ship=OLD_SHIP_ADDRESS,
            ),
            NEW_CUSTOMER_ID: fake_contact(),
        }
        self.taxes = [
            {"tax_id": TAX_ID, "tax_name": "GST", "tax_percentage": 5.0, "tax_type": "tax"},
        ]
        self.get_paths: list[str] = []
        self.write_calls: list[dict] = []
        self.vault = {
            "api_domain": "https://www.zohoapis.ca",
            "books_organization_id": ORGANIZATION["organization_id"],
            "scopes": [revision_tool.UPDATE_SCOPE],
        }
        self.patchers = [
            mock.patch.object(revision_tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(revision_tool.zoho_tool, "load_vault", side_effect=self.load_vault),
            mock.patch.object(
                revision_tool.zoho_tool,
                "refresh_access_token",
                side_effect=lambda vault=None: ("token", self.vault),
            ),
            mock.patch.object(revision_tool.zoho_tool, "save_vault"),
            mock.patch.object(revision_tool.zoho_tool, "append_receipt"),
            mock.patch.object(revision_tool.zoho_tool, "api_get", side_effect=self.api_get),
            mock.patch.object(
                revision_tool,
                "urlopen",
                side_effect=AssertionError("a live Zoho write is forbidden in tests"),
            ),
        ]
        started = [patcher.start() for patcher in self.patchers]
        self.append_receipt = started[4]
        self.api_get_mock = started[5]
        self.urlopen = started[6]

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    # -- fake transports -------------------------------------------------
    def load_vault(self) -> dict:
        return self.vault

    def api_get(self, access_token: str, api_domain: str, path: str) -> dict:
        self.get_paths.append(path)
        route = path.split("?", 1)[0]
        if route == "/books/v3/organizations":
            return {"organizations": [dict(ORGANIZATION)]}
        if route == f"/books/v3/invoices/{INVOICE_ID}":
            return {"invoice": copy.deepcopy(self.invoice)}
        if route == "/books/v3/settings/taxes":
            return {"taxes": copy.deepcopy(self.taxes)}
        match = re.fullmatch(r"/books/v3/contacts/([0-9]+)(/address)?", route)
        if match:
            contact = self.contacts.get(match.group(1))
            if contact is None:
                return {"contact": None, "addresses": []}
            if match.group(2):
                return {
                    "addresses": [
                        copy.deepcopy(contact["billing_address"]),
                        copy.deepcopy(contact["shipping_address"]),
                    ]
                }
            return {"contact": copy.deepcopy(contact)}
        raise AssertionError(f"unexpected GET {path}")

    def allow_writes(self, on_write=None):
        """Opt in to a SIMULATED write transport. Still no network."""

        def transport(request, timeout=60):
            body = json.loads(request.data.decode("utf-8"))
            record = {
                "url": request.full_url,
                "method": request.get_method(),
                "payload": body,
                "locks_before": self.lock_files(),
            }
            self.write_calls.append(record)
            if on_write is not None:
                on_write(record)
            else:
                self.apply_write(body)
            return FakeResponse({"code": 0, "message": "Invoice information has been updated."})

        self.urlopen.side_effect = transport

    def apply_write(self, body: dict) -> None:
        """A faithful-enough Zoho: apply the payload and recalculate."""
        invoice = self.invoice
        for field in ("customer_id", "reference_number", "date", "due_date", "notes", "terms"):
            if field in body:
                invoice[field] = body[field]
        if "customer_id" in body:
            invoice["customer_name"] = self.contacts[body["customer_id"]]["contact_name"]
        for field, key in (
            ("billing_address_id", "billing_address"),
            ("shipping_address_id", "shipping_address"),
        ):
            if field not in body:
                continue
            for contact in self.contacts.values():
                for candidate in (contact["billing_address"], contact["shipping_address"]):
                    if candidate["address_id"] == body[field]:
                        invoice[key] = copy.deepcopy(candidate)
        by_id = {line["line_item_id"]: line for line in invoice["line_items"]}
        for sent in body["line_items"]:
            line = by_id[sent["line_item_id"]]
            for field in ("quantity", "rate", "discount", "description", "tax_id"):
                if field in sent:
                    line[field] = sent[field]
            discount = line.get("discount") or 0
            gross = Decimal(str(line["quantity"])) * Decimal(str(line["rate"]))
            if isinstance(discount, str) and discount.endswith("%"):
                total = gross * (Decimal(100) - Decimal(discount[:-1])) / Decimal(100)
            else:
                total = gross - Decimal(str(discount))
            line["item_total"] = float(round(total, 2))
            tax = next((row for row in self.taxes if row["tax_id"] == line.get("tax_id")), None)
            line["tax_percentage"] = tax["tax_percentage"] if tax else 0
            line["tax_name"] = tax["tax_name"] if tax else ""
        sub_total = sum(Decimal(str(line["item_total"])) for line in invoice["line_items"])
        tax_total = sum(
            (Decimal(str(line["item_total"])) * Decimal(str(line["tax_percentage"])) / 100).quantize(
                Decimal("0.01")
            )
            for line in invoice["line_items"]
        )
        invoice["sub_total"] = float(sub_total)
        invoice["tax_total"] = float(tax_total)
        invoice["total"] = float(sub_total + tax_total)
        invoice["balance"] = float(sub_total + tax_total)
        invoice["last_modified_time"] = "2026-08-10T12:00:00-0400"

    def lock_files(self) -> list[str]:
        folder = self.plan_dir / ".commit-locks"
        return sorted(path.name for path in folder.glob("*.json")) if folder.exists() else []

    # -- helpers ---------------------------------------------------------
    def input_path(self, value: object) -> Path:
        self.counter += 1
        path = self.root / f"input_{self.counter}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def request(self, changes: dict | None = None, line_changes: list | None = None) -> dict:
        return {
            "invoice_id": INVOICE_ID,
            "invoice_number": INVOICE_NUMBER,
            "changes": changes if changes is not None else {"reference_number": change("0000031")},
            "line_changes": line_changes or [],
        }

    def stage(self, payload: dict | None = None) -> Path:
        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            revision_tool.command_stage(
                argparse.Namespace(input=str(self.input_path(payload or self.request())))
            )
        self.stage_output = buffer.getvalue()
        plans = sorted(self.plan_dir.glob("*.json"))
        self.assertTrue(plans, "stage did not write a plan")
        return plans[-1]

    def commit(self, plan: Path, approval: str = "APPROVED") -> str:
        # His go is timed one minute AFTER the plan was written (A3/A5:
        # --approval-message-utc is required on every money commit).
        try:
            created = json.loads(plan.read_text(encoding="utf-8"))["created_utc"]
            after = (revision_tool.parse_time(created, "created_utc") + timedelta(minutes=1)).isoformat()
        except (OSError, ValueError, KeyError, revision_tool.InvoiceRevisionError):
            after = revision_tool.utc_now().isoformat()  # the tool refuses the path itself
        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            revision_tool.command_commit(
                argparse.Namespace(plan=str(plan), approval=approval, approval_message_utc=after)
            )
        return buffer.getvalue()

    def rewrite_plan(self, plan: Path, mutate) -> Path:
        data = json.loads(plan.read_text(encoding="utf-8"))
        data.pop("sha256")
        mutate(data)
        data["sha256"] = revision_tool.digest_for(data)
        plan.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return plan

    def assertNoWrites(self) -> None:
        self.assertEqual(self.write_calls, [], "a write was attempted")

    def customer_change_request(self) -> dict:
        return self.request(changes={
            "customer_id": change(NEW_CUSTOMER_ID),
            "billing_address_id": change(NEW_BILL_ADDRESS),
            "shipping_address_id": change(NEW_SHIP_ADDRESS),
            "reference_number": change("0000031"),
        })


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class InputSchemaTests(RevisionToolTestCase):
    def test_forbidden_header_fields_refused(self) -> None:
        for field in (
            "status", "invoice_number", "currency_id", "currency_code", "exchange_rate",
            "adjustment", "shipping_charge", "balance", "payment_made", "custom_fields",
            "template_id", "salesperson_id", "line_items", "email", "send",
        ):
            with self.subTest(field=field):
                with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
                    revision_tool.validate_input(self.request(changes={field: change("x")}))
                self.assertIn("uncommissioned", str(caught.exception))

    def test_forbidden_line_fields_refused(self) -> None:
        for field in ("item_id", "name", "unit", "account_id", "item_total", "line_item_id",
                      "warehouse_id", "item_custom_fields"):
            with self.subTest(field=field):
                request = self.request(changes={}, line_changes=[{
                    "line_item_id": LINE_ONE, "item_id": ITEM_ONE,
                    "changes": {field: change("x")},
                }])
                with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
                    revision_tool.validate_input(request)
                self.assertIn("uncommissioned", str(caught.exception))

    def test_unknown_and_missing_top_level_fields_refused(self) -> None:
        request = self.request()
        request["extra"] = 1
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            revision_tool.validate_input(request)
        request = self.request()
        request.pop("line_changes")
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            revision_tool.validate_input(request)

    def test_every_change_needs_a_source(self) -> None:
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            revision_tool.validate_input(self.request(changes={"reference_number": {"value": "1"}}))
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            revision_tool.validate_input(
                self.request(changes={"reference_number": change("1", source="")})
            )

    def test_quantity_rate_discount_need_canonical_decimal_text_and_a_source(self) -> None:
        for field, bad in (
            ("quantity", 3), ("rate", 12.5), ("discount", 5), ("quantity", "3.0000001"),
            ("rate", "-1"), ("discount", "101%"), ("quantity", "0"),
        ):
            with self.subTest(field=field, bad=bad):
                request = self.request(changes={}, line_changes=[{
                    "line_item_id": LINE_ONE, "item_id": ITEM_ONE,
                    "changes": {field: change(bad)},
                }])
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    revision_tool.validate_input(request)

    def test_tax_change_requires_exact_old_and_new_rates(self) -> None:
        base = {"line_item_id": LINE_ONE, "item_id": ITEM_ONE}
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            revision_tool.validate_input(self.request(changes={}, line_changes=[
                {**base, "changes": {"tax_id": change(TAX_ID)}}
            ]))
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            revision_tool.validate_input(self.request(changes={}, line_changes=[
                {**base, "changes": {"tax_id": change(
                    TAX_ID, old_tax_percentage="zero", new_tax_percentage="5"
                )}}
            ]))
        ok = revision_tool.validate_input(self.request(changes={}, line_changes=[
            {**base, "changes": {"tax_id": change(
                TAX_ID, old_tax_percentage="0", new_tax_percentage="5"
            )}}
        ]))
        self.assertEqual(ok["line_changes"][0]["changes"]["tax_id"]["new_tax_percentage"], "5")

    def test_customer_change_requires_both_addresses(self) -> None:
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            revision_tool.validate_input(
                self.request(changes={"customer_id": change(NEW_CUSTOMER_ID)})
            )
        self.assertIn("billing_address_id", str(caught.exception))

    def test_date_change_requires_due_date(self) -> None:
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            revision_tool.validate_input(self.request(changes={"date": change("2026-08-10")}))

    def test_empty_request_refused(self) -> None:
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            revision_tool.validate_input(self.request(changes={}, line_changes=[]))

    def test_duplicate_line_item_id_refused(self) -> None:
        row = {"line_item_id": LINE_ONE, "item_id": ITEM_ONE, "changes": {"quantity": change("3")}}
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            revision_tool.validate_input(
                self.request(changes={}, line_changes=[row, dict(row)])
            )

    def test_ids_must_be_canonical(self) -> None:
        for bad in ("0", "-5", "01", "", "abc", 5, None, True):
            with self.subTest(bad=bad):
                request = self.request()
                request["invoice_id"] = bad
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    revision_tool.validate_input(request)

    def test_dates_must_be_real_calendar_dates(self) -> None:
        for bad in ("2026-13-01", "2026-02-30", "10/08/2026", "2026-8-1"):
            with self.subTest(bad=bad):
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    revision_tool.validate_input(self.request(changes={
                        "date": change(bad), "due_date": change("2026-12-31"),
                    }))


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


class StageTests(RevisionToolTestCase):
    def test_stage_reads_only_and_writes_nothing(self) -> None:
        plan_path = self.stage()
        self.assertNoWrites()
        self.urlopen.assert_not_called()
        self.assertEqual(self.lock_files(), [])
        self.assertIn("STAGED_NOT_COMMITTED", self.stage_output)
        self.assertIn("Zoho writes performed by this stage: 0", self.stage_output)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertIs(plan["live_evidence"]["email_sent"], False)
        self.assertIs(plan["risk"]["email_sent"], False)
        receipt = self.append_receipt.call_args[0]
        self.assertIn("plan_staged_not_committed", receipt[0])
        self.assertIn("email_sent=false", receipt[1])

    def test_plan_expiry_is_exactly_24_hours_and_hashed(self) -> None:
        plan = json.loads(self.stage().read_text(encoding="utf-8"))
        created = revision_tool.parse_time(plan["created_utc"], "created")
        expires = revision_tool.parse_time(plan["expires_utc"], "expires")
        self.assertEqual((expires - created).total_seconds(), 24 * 3600)
        core = dict(plan)
        core.pop("sha256")
        self.assertEqual(plan["sha256"], revision_tool.digest_for(core))

    def test_header_only_plan_protects_lines_customer_and_totals(self) -> None:
        plan = json.loads(self.stage().read_text(encoding="utf-8"))
        protected = plan["live_evidence"]["invoice"]["protected_state"]
        for key in ("line_items", "sub_total", "total", "balance", "customer_id",
                    "status", "invoice_number", "currency_code", "exchange_rate",
                    "custom_fields", "template_id", "date", "due_date", "notes", "terms"):
            self.assertIn(key, protected, f"{key} must stay inside the fingerprint")
        # Only the one requested field plus Zoho's own write metadata leaves the
        # fingerprint. Nothing else is exempted speculatively.
        self.assertEqual(
            plan["live_evidence"]["unprotected_keys"],
            sorted(set(revision_tool.VOLATILE_FIELDS) | {"reference_number"}),
        )
        totals = plan["live_evidence"]["totals"]
        self.assertTrue(totals["total_deterministic"])
        self.assertEqual(totals["after"], totals["before"])

    def test_put_payload_carries_every_line_exactly_once_in_order(self) -> None:
        plan = json.loads(self.stage().read_text(encoding="utf-8"))
        put = plan["live_evidence"]["put_payload"]
        self.assertEqual(
            [line["line_item_id"] for line in put["line_items"]], [LINE_ONE, LINE_TWO]
        )
        self.assertEqual([line["item_id"] for line in put["line_items"]], [ITEM_ONE, ITEM_TWO])
        self.assertEqual(put["invoice_number"], INVOICE_NUMBER)
        self.assertEqual(put["customer_id"], OLD_CUSTOMER_ID)
        self.assertNotIn("status", put)
        self.assertNotIn("currency_id", put)
        self.assertNotIn("exchange_rate", put)

    def test_invoice_number_mismatch_refused(self) -> None:
        request = self.request()
        request["invoice_number"] = "INV-000999"
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("numbered", str(caught.exception))

    def test_only_draft_or_sent_is_revisable(self) -> None:
        for status in ("paid", "partially_paid", "void", "overdue", "viewed", "unpaid", "draft"):
            with self.subTest(status=status):
                self.invoice = fake_invoice(status=status)
                if status == "draft":
                    self.stage()
                    continue
                with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
                    self.stage()
                self.assertIn("status", str(caught.exception))
                self.assertNoWrites()

    def test_paid_credited_or_written_off_invoice_refused(self) -> None:
        cases = (
            {"payment_made": 100.0, "balance": 1150.0},
            {"credits_applied": 50.0, "balance": 1200.0},
            {"write_off_amount": 10.0, "balance": 1240.0},
            {"balance": 900.0},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.invoice = fake_invoice(**overrides)
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    self.stage()
                self.assertNoWrites()

    def test_dependent_records_refuse_revision(self) -> None:
        cases = (
            {"payments": [{"payment_id": "1"}]},
            {"creditnotes": [{"creditnote_id": "1"}]},
            {"packages": [{"package_id": "1"}]},
            {"recurring_invoice_id": "96274000000777001"},
            {"shipping_status": "shipped"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.invoice = fake_invoice(**overrides)
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    self.stage()
                self.assertNoWrites()

    def test_line_change_on_a_salesorder_linked_invoice_refused(self) -> None:
        self.invoice = fake_invoice(salesorder_id="96274000000888001")
        request = self.request(changes={}, line_changes=[{
            "line_item_id": LINE_ONE, "item_id": ITEM_ONE, "changes": {"quantity": change("3")},
        }])
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("sales order", str(caught.exception))
        # A header-only revision of the same invoice is still allowed.
        self.stage()

    def test_adding_a_line_is_impossible(self) -> None:
        request = self.request(changes={}, line_changes=[{
            "line_item_id": "96274000009999999", "item_id": ITEM_ONE,
            "changes": {"quantity": change("3")},
        }])
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("not on this invoice", str(caught.exception))

    def test_item_substitution_refused(self) -> None:
        request = self.request(changes={}, line_changes=[{
            "line_item_id": LINE_ONE, "item_id": ITEM_TWO,
            "changes": {"quantity": change("3")},
        }])
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("substitution", str(caught.exception))

    def test_customer_must_already_exist(self) -> None:
        request = self.customer_change_request()
        request["changes"]["customer_id"] = change("96274000000000001")
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("never creates one", str(caught.exception))
        self.assertNoWrites()

    def test_customer_must_be_an_active_customer_in_the_same_currency(self) -> None:
        cases = (
            {"contact_type": "vendor"},
            {"status": "inactive"},
            {"currency_code": "USD"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.contacts[NEW_CUSTOMER_ID] = fake_contact(**overrides)
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    self.stage(self.customer_change_request())

    def test_addresses_must_belong_to_the_selected_customer(self) -> None:
        request = self.customer_change_request()
        request["changes"]["billing_address_id"] = change(OLD_BILL_ADDRESS)
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("not one of the addresses owned", str(caught.exception))

    def test_customer_change_records_live_name_address_and_currency(self) -> None:
        plan = json.loads(self.stage(self.customer_change_request()).read_text(encoding="utf-8"))
        customer = plan["live_evidence"]["customer"]
        self.assertEqual(customer["customer_name"], "SHM Marine Constructors JV")
        self.assertEqual(customer["currency_code"], "CAD")
        addresses = plan["live_evidence"]["addresses"]
        self.assertEqual(addresses["billing_address_id"]["address_id"], NEW_BILL_ADDRESS)
        self.assertEqual(addresses["shipping_address_id"]["address_id"], NEW_SHIP_ADDRESS)

    def test_tax_change_needs_a_live_tax_and_truthful_rates(self) -> None:
        def tax_request(tax_id=TAX_ID, old="0", new="5"):
            return self.request(changes={}, line_changes=[{
                "line_item_id": LINE_ONE, "item_id": ITEM_ONE,
                "changes": {"tax_id": change(
                    tax_id, old_tax_percentage=old, new_tax_percentage=new
                )},
            }])

        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.stage(tax_request(tax_id="96274000000000999"))
        self.assertIn("not a live tax", str(caught.exception))
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.stage(tax_request(new="13"))
        self.assertIn("live at 5", str(caught.exception))
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.stage(tax_request(old="13"))
        self.assertIn("live at 0", str(caught.exception))
        plan = json.loads(self.stage(tax_request()).read_text(encoding="utf-8"))
        self.assertEqual(plan["live_evidence"]["taxes"][TAX_ID]["tax_percentage"], 5.0)
        self.assertFalse(plan["live_evidence"]["totals"]["total_deterministic"])
        self.assertTrue(plan["live_evidence"]["totals"]["sub_total_deterministic"])

    def test_no_op_requests_refused(self) -> None:
        for changes in (
            {"reference_number": change("")},
            {"notes": change("Thank you for your business.")},
            {"terms": change("Net 30")},
            {"date": change("2026-07-01"), "due_date": change("2026-12-31")},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    self.stage(self.request(changes=changes))

    def test_past_due_date_on_a_sent_invoice_refused(self) -> None:
        request = self.request(changes={
            "date": change("2026-01-01"), "due_date": change("2026-01-31"),
        })
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("overdue", str(caught.exception))

    def test_due_date_before_invoice_date_refused(self) -> None:
        request = self.request(changes={
            "date": change("2026-12-01"), "due_date": change("2026-11-01"),
        })
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.stage(request)

    def test_wrong_or_non_frp_organization_refused(self) -> None:
        self.vault["books_organization_id"] = "110009999999"
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.stage()
        self.vault["books_organization_id"] = ORGANIZATION["organization_id"]

        def foreign(access_token, api_domain, path):
            if path == "/books/v3/organizations":
                return {"organizations": [{"organization_id": "1", "name": "Troy Dualam"}]}
            return self.api_get(access_token, api_domain, path)

        self.api_get_mock.side_effect = foreign
        with self.assertRaises(zoho_tool.ZohoError):
            self.stage()

    def test_deterministic_totals_for_a_quantity_change(self) -> None:
        request = self.request(changes={}, line_changes=[{
            "line_item_id": LINE_ONE, "item_id": ITEM_ONE, "changes": {"quantity": change("3")},
        }])
        plan = json.loads(self.stage(request).read_text(encoding="utf-8"))
        totals = plan["live_evidence"]["totals"]
        self.assertEqual(totals["before"]["sub_total"], "1250.00")
        self.assertEqual(totals["after"]["sub_total"], "1750.00")
        self.assertEqual(totals["after"]["total"], "1750.00")
        self.assertEqual(totals["after"]["balance"], "1750.00")

    def test_line_changes_use_exact_decimal_arithmetic(self) -> None:
        self.invoice = fake_invoice()
        self.invoice["line_items"] = [fake_line(quantity=3.0, rate=0.07)]
        self.invoice["sub_total"] = 0.21
        self.invoice["total"] = 0.21
        self.invoice["balance"] = 0.21
        self.invoice["line_items"][0]["item_total"] = 0.21
        request = self.request(changes={}, line_changes=[{
            "line_item_id": LINE_ONE, "item_id": ITEM_ONE, "changes": {"rate": change("0.145")},
        }])
        plan = json.loads(self.stage(request).read_text(encoding="utf-8"))
        # 3 x 0.145 = 0.435 -> half-up 0.44, which binary floats would give as 0.43.
        self.assertEqual(plan["live_evidence"]["lines"][0]["expected_item_total"], "0.44")


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class ApprovalTests(RevisionToolTestCase):
    def test_his_unambiguous_go_after_the_plan_is_accepted_and_a_condition_is_not(self) -> None:
        """2026-08-21 (A3): exact APPROVED still works; "yes go ahead" to the
        shown plan counts; a condition, a question, a blank or a non-string
        refuses; a word sent before the plan existed cannot approve it."""
        plan = {"created_utc": "2026-08-10T10:00:00+00:00", "expires_utc": "2026-08-11T10:00:00+00:00"}
        after = "2026-08-10T10:01:00+00:00"
        for bad in ("approved but wait", "hold on", "APPROVED?", "APPROVED\nnow", "",
                    None, 1, b"APPROVED", ["APPROVED"]):
            with self.subTest(bad=bad):
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    revision_tool.require_rachad_approval(bad, plan, sent_utc=after)
        self.assertTrue(revision_tool.require_rachad_approval("APPROVED", plan, sent_utc=after).exact_word)
        self.assertEqual(revision_tool.require_rachad_approval("yes go ahead", plan, sent_utc=after).kind, "money")
        # A go with no time is refused: "after the plan" cannot be proven.
        with self.assertRaisesRegex(revision_tool.InvoiceRevisionError, "--approval-message-utc"):
            revision_tool.require_rachad_approval("APPROVED", plan)
        with self.assertRaisesRegex(revision_tool.InvoiceRevisionError, "BEFORE this plan was created"):
            revision_tool.require_rachad_approval("APPROVED", plan, sent_utc="2026-08-10T09:59:00+00:00")
        self.assertEqual(
            revision_tool.require_rachad_approval("APPROVED", plan, sent_utc=after).sent_utc, after,
        )
        # The plan is mandatory: there is no reversible fallback on a money check.
        with self.assertRaises(TypeError):
            revision_tool.require_rachad_approval("APPROVED")

    def test_wrong_approval_refused_before_lock_token_or_network(self) -> None:
        plan_path = self.stage()
        self.api_get_mock.reset_mock()
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.commit(plan_path, approval="approved but wait")
        self.assertNoWrites()
        self.assertEqual(self.lock_files(), [], "no lock may be created without approval")
        self.api_get_mock.assert_not_called()

    def test_staging_alone_never_commits(self) -> None:
        self.stage()
        self.assertNoWrites()
        self.assertEqual(self.lock_files(), [])


# ---------------------------------------------------------------------------
# Plan integrity
# ---------------------------------------------------------------------------


class PlanIntegrityTests(RevisionToolTestCase):
    def test_tampered_plan_refused(self) -> None:
        plan_path = self.stage()
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        data["payload"]["changes"]["reference_number"]["value"] = "9999999"
        plan_path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn("hash check failed", str(caught.exception))
        self.assertNoWrites()

    def test_rehashed_tampering_still_refused(self) -> None:
        mutations = {
            "payload value": lambda d: d["payload"]["changes"]["reference_number"].__setitem__(
                "value", "9999999"),
            "put payload field": lambda d: d["live_evidence"]["put_payload"].__setitem__(
                "status", "void"),
            "put line quantity": lambda d: d["live_evidence"]["put_payload"]["line_items"][0]
                .__setitem__("quantity", 99.0),
            "dropped put line": lambda d: d["live_evidence"]["put_payload"].__setitem__(
                "line_items", d["live_evidence"]["put_payload"]["line_items"][:1]),
            "endpoint": lambda d: d["live_evidence"].__setitem__(
                "put_endpoint", "DELETE /books/v3/invoices/" + INVOICE_ID),
            "fingerprint": lambda d: d["live_evidence"]["invoice"].__setitem__(
                "protected_state_sha256", "0" * 64),
            "before state": lambda d: d["live_evidence"]["invoice"]["before_state"].__setitem__(
                "status", "draft"),
            "email flag": lambda d: d["live_evidence"].__setitem__("email_sent", True),
            "risk block": lambda d: d["risk"].__setitem__("atomic", False),
            "recorded status": lambda d: d["live_evidence"]["invoice"].__setitem__(
                "status", "draft"),
            "unprotected keys": lambda d: d["live_evidence"].__setitem__(
                "unprotected_keys", ["status", "total", "last_modified_time"]),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                plan_path = self.stage()
                self.rewrite_plan(plan_path, mutate)
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    self.commit(plan_path)
                self.assertNoWrites()
                plan_path.unlink()

    def test_wrong_origin_tool_action_and_version_refused(self) -> None:
        mutations = (
            lambda d: d["origin"].__setitem__("tool_path", r"C:\evil\tool.py"),
            lambda d: d.__setitem__("tool", "Some Other Tool"),
            lambda d: d.__setitem__("action", "invoice_delete"),
            lambda d: d.__setitem__("schema_version", 2),
            lambda d: d.__setitem__("approval_required", "OK"),
            lambda d: d.__setitem__("nonce", "zz"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                plan_path = self.stage()
                self.rewrite_plan(plan_path, mutate)
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    self.commit(plan_path)
                self.assertNoWrites()
                plan_path.unlink()

    def test_expired_plan_refused(self) -> None:
        plan_path = self.stage()

        def age(data):
            created = revision_tool.parse_time(data["created_utc"], "c")
            expires = revision_tool.parse_time(data["expires_utc"], "e")
            shift = revision_tool.timedelta(hours=25)
            data["created_utc"] = (created - shift).isoformat()
            data["expires_utc"] = (expires - shift).isoformat()

        self.rewrite_plan(plan_path, age)
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn("expired", str(caught.exception))
        self.assertNoWrites()

    def test_extended_plan_lifetime_refused(self) -> None:
        plan_path = self.stage()
        self.rewrite_plan(plan_path, lambda d: d.__setitem__(
            "expires_utc",
            (revision_tool.parse_time(d["created_utc"], "c")
             + revision_tool.timedelta(hours=72)).isoformat(),
        ))
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn("24-hour", str(caught.exception))

    def test_plan_outside_the_plan_folder_refused(self) -> None:
        plan_path = self.stage()
        outside = self.root / "outside.json"
        outside.write_text(plan_path.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.commit(outside)
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.commit(Path("relative.json"))
        self.assertNoWrites()

    def test_wrong_organization_in_plan_refused_before_write(self) -> None:
        plan_path = self.stage()
        self.rewrite_plan(plan_path, lambda d: d["organization"].__setitem__(
            "organization_id", "110009999999"))
        self.allow_writes()
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.commit(plan_path)
        self.assertNoWrites()


# ---------------------------------------------------------------------------
# Write transport
# ---------------------------------------------------------------------------


class WriteTransportTests(RevisionToolTestCase):
    def payload(self) -> dict:
        return {
            "customer_id": OLD_CUSTOMER_ID,
            "invoice_number": INVOICE_NUMBER,
            "date": "2026-07-01",
            "due_date": "2026-12-31",
            "line_items": [
                {"line_item_id": LINE_ONE, "item_id": ITEM_ONE, "quantity": 2.0, "rate": 500.0},
            ],
        }

    def call(self, method="PUT", path=f"/books/v3/invoices/{INVOICE_ID}", payload=None):
        return revision_tool.oauth_invoice_revision_write_allowed(
            "token", "https://www.zohoapis.ca", method, path,
            ORGANIZATION["organization_id"], self.payload() if payload is None else payload,
        )

    def test_only_put_is_reachable(self) -> None:
        for method in ("GET", "POST", "DELETE", "PATCH", "HEAD", "put"):
            with self.subTest(method=method):
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    self.call(method=method)
        self.assertNoWrites()

    def test_no_create_delete_void_status_email_or_bulk_route_is_reachable(self) -> None:
        forbidden = (
            "/books/v3/invoices",
            "/books/v3/invoices/",
            f"/books/v3/invoices/{INVOICE_ID}/status/void",
            f"/books/v3/invoices/{INVOICE_ID}/status/sent",
            f"/books/v3/invoices/{INVOICE_ID}/status/draft",
            f"/books/v3/invoices/{INVOICE_ID}/email",
            f"/books/v3/invoices/{INVOICE_ID}/submit",
            f"/books/v3/invoices/{INVOICE_ID}/approve",
            f"/books/v3/invoices/{INVOICE_ID}/reject",
            f"/books/v3/invoices/{INVOICE_ID}/reminder",
            f"/books/v3/invoices/{INVOICE_ID}/payments",
            f"/books/v3/invoices/{INVOICE_ID}/creditsapplied",
            f"/books/v3/invoices/{INVOICE_ID}/attachment",
            f"/books/v3/invoices/{INVOICE_ID}/templates/1",
            "/books/v3/invoices/email",
            "/books/v3/contacts/1",
            "/books/v3/estimates/1",
            "/inventory/v1/items/1",
            f"/books/v3/invoices/{INVOICE_ID}?send=true",
            f"/books/v3/invoices/{INVOICE_ID}/",
            "/books/v3/invoices/0",
            "/books/v3/invoices/-1",
            "https://evil.invalid/books/v3/invoices/1",
        )
        for path in forbidden:
            with self.subTest(path=path):
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    self.call(path=path)
        self.assertNoWrites()

    def test_payload_field_allowlist(self) -> None:
        for extra in ("status", "invoice_number_new", "currency_id", "exchange_rate",
                      "adjustment", "shipping_charge", "custom_fields", "send", "email",
                      "payment_options", "template_id", "balance"):
            with self.subTest(extra=extra):
                payload = self.payload()
                payload[extra] = "x"
                with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
                    self.call(payload=payload)
                self.assertIn("uncommissioned", str(caught.exception))

    def test_required_preserved_fields_must_be_present(self) -> None:
        for missing in ("customer_id", "invoice_number", "date", "due_date", "line_items"):
            with self.subTest(missing=missing):
                payload = self.payload()
                payload.pop(missing)
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    self.call(payload=payload)

    def test_line_payload_rules(self) -> None:
        payload = self.payload()
        payload["line_items"] = []
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.call(payload=payload)
        payload = self.payload()
        payload["line_items"][0]["item_total"] = 1000.0
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.call(payload=payload)
        payload = self.payload()
        payload["line_items"].append(dict(payload["line_items"][0]))
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.call(payload=payload)
        payload = self.payload()
        payload["line_items"][0].pop("line_item_id")
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.call(payload=payload)

    def test_url_is_built_from_the_zoho_domain_with_only_the_org_id(self) -> None:
        self.allow_writes()
        self.call()
        self.assertEqual(len(self.write_calls), 1)
        url = self.write_calls[0]["url"]
        self.assertEqual(
            url,
            "https://www.zohoapis.ca/books/v3/invoices/"
            f"{INVOICE_ID}?organization_id={ORGANIZATION['organization_id']}",
        )
        self.assertEqual(self.write_calls[0]["method"], "PUT")

    def test_non_zero_zoho_response_code_is_a_failure(self) -> None:
        self.urlopen.side_effect = lambda request, timeout=60: FakeResponse(
            {"code": 15, "message": "nope"}
        )
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.call()
        self.urlopen.side_effect = lambda request, timeout=60: FakeResponse({"ok": True})
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.call()


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


class CommitTests(RevisionToolTestCase):
    def test_successful_header_revision_writes_once_and_verifies(self) -> None:
        plan_path = self.stage(self.customer_change_request())
        self.allow_writes()
        output = json.loads(self.commit(plan_path))
        self.assertEqual(output["status"], "COMMITTED_AND_VERIFIED")
        self.assertIs(output["email_sent"], False)
        self.assertIs(output["atomic"], True)
        self.assertEqual(output["invoice_number"], INVOICE_NUMBER)
        self.assertEqual(output["invoice_status_preserved"], "sent")
        self.assertEqual(output["lines_preserved"], 2)
        self.assertEqual(output["lines_changed"], 0)
        self.assertEqual(len(self.write_calls), 1, "exactly one PUT")
        self.assertEqual(self.invoice["customer_id"], NEW_CUSTOMER_ID)
        self.assertEqual(self.invoice["reference_number"], "0000031")
        self.assertEqual(self.invoice["invoice_number"], INVOICE_NUMBER)
        self.assertEqual(self.invoice["status"], "sent")
        self.assertEqual(self.invoice["total"], 1250.0)
        lock = json.loads((self.plan_dir / ".commit-locks" / self.lock_files()[0]).read_text())
        self.assertEqual(lock["status"], "committed_verified")
        self.assertIs(lock["permanent_lock"], False)
        receipt = self.append_receipt.call_args[0]
        self.assertIn("committed_verified", receipt[0])
        self.assertIn("email_sent=false", receipt[1])

    def test_successful_line_revision_preserves_ids_and_recalculates(self) -> None:
        request = self.request(changes={}, line_changes=[{
            "line_item_id": LINE_ONE, "item_id": ITEM_ONE,
            "changes": {"quantity": change("3"), "rate": change("400")},
        }])
        plan_path = self.stage(request)
        self.allow_writes()
        json.loads(self.commit(plan_path))
        self.assertEqual(len(self.write_calls), 1)
        self.assertEqual(
            [line["line_item_id"] for line in self.invoice["line_items"]], [LINE_ONE, LINE_TWO]
        )
        self.assertEqual(self.invoice["line_items"][0]["quantity"], 3.0)
        self.assertEqual(self.invoice["line_items"][0]["rate"], 400.0)
        self.assertEqual(self.invoice["line_items"][1], fake_invoice()["line_items"][1])
        self.assertEqual(self.invoice["total"], 1450.0)

    def test_lock_exists_before_the_put(self) -> None:
        plan_path = self.stage()
        self.allow_writes()
        self.commit(plan_path)
        self.assertEqual(len(self.write_calls[0]["locks_before"]), 1)

    def test_replayed_plan_refused(self) -> None:
        plan_path = self.stage()
        self.allow_writes()
        self.commit(plan_path)
        self.assertEqual(len(self.write_calls), 1)
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn("cannot be replayed", str(caught.exception))
        self.assertEqual(len(self.write_calls), 1, "a replay must not write again")

    def test_invoice_changed_after_review_refused_before_write(self) -> None:
        for overrides in (
            {"reference_number": "SOMEONE-ELSE"},
            {"total": 9999.0, "balance": 9999.0, "sub_total": 9999.0},
            {"status": "paid"},
            {"payment_made": 100.0},
        ):
            with self.subTest(overrides=overrides):
                self.invoice = fake_invoice()
                plan_path = self.stage()
                self.invoice = fake_invoice(**overrides)
                self.allow_writes()
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    self.commit(plan_path)
                self.assertNoWrites()
                plan_path.unlink()
                self.invoice = fake_invoice()

    def test_customer_or_address_changed_after_review_refused(self) -> None:
        plan_path = self.stage(self.customer_change_request())
        self.contacts[NEW_CUSTOMER_ID] = fake_contact(name="SHM Marine Constructors JV Renamed")
        self.allow_writes()
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.commit(plan_path)
        self.assertNoWrites()

    def test_tax_changed_after_review_refused(self) -> None:
        request = self.request(changes={}, line_changes=[{
            "line_item_id": LINE_ONE, "item_id": ITEM_ONE,
            "changes": {"tax_id": change(TAX_ID, old_tax_percentage="0", new_tax_percentage="5")},
        }])
        plan_path = self.stage(request)
        self.taxes[0]["tax_percentage"] = 13.0
        self.allow_writes()
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.commit(plan_path)
        self.assertNoWrites()

    def test_missing_update_scope_refused_before_write(self) -> None:
        plan_path = self.stage()
        self.vault["scopes"] = ["ZohoBooks.invoices.READ"]
        self.allow_writes()
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn(revision_tool.UPDATE_SCOPE, str(caught.exception))
        self.assertNoWrites()

    def test_a_failed_put_is_reported_and_needs_restage_not_a_permanent_lock(self) -> None:
        plan_path = self.stage()

        def fail(record):
            raise HTTPError(record["url"], 400, "Bad Request", None, io.BytesIO(b'{"code":15}'))

        self.allow_writes(on_write=fail)
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn("re-stage", str(caught.exception).casefold())
        self.assertIn("his go to the NEW plan", str(caught.exception))
        self.assertNotIn("permanent", str(caught.exception).casefold())
        self.assertIn("PUT was ISSUED", str(caught.exception))
        self.assertEqual(len(self.write_calls), 1)
        lock = json.loads((self.plan_dir / ".commit-locks" / self.lock_files()[0]).read_text())
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertIs(lock["permanent_lock"], False)
        self.allow_writes()
        # The SAME plan is bound to stale live state: refused, no silent second attempt.
        with self.assertRaisesRegex(revision_tool.InvoiceRevisionError, "Re-stage"):
            self.commit(plan_path)
        self.assertEqual(len(self.write_calls), 1, "no second attempt is possible")

    def test_timeout_is_indeterminate_and_needs_restage(self) -> None:
        plan_path = self.stage()

        def timeout(record):
            raise URLError("timed out")

        self.allow_writes(on_write=timeout)
        with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn("indeterminate", str(caught.exception))
        lock = json.loads((self.plan_dir / ".commit-locks" / self.lock_files()[0]).read_text())
        self.assertEqual(lock["status"], "indeterminate_needs_restage")

    def test_readback_identity_currency_and_status_are_enforced(self) -> None:
        cases = (
            ({"invoice_number": "INV-000052"}, "invoice_number"),
            ({"status": "draft"}, "status"),
            ({"currency_code": "USD"}, "currency_code"),
            ({"currency_id": "96274000000000098"}, "currency_id"),
            ({"exchange_rate": 1.37}, "exchange rate"),
        )
        for overrides, expected_field in cases:
            with self.subTest(overrides=overrides):
                self.invoice = fake_invoice()
                plan_path = self.stage()

                def drift(record, overrides=overrides):
                    self.apply_write(record["payload"])
                    self.invoice.update(overrides)

                self.allow_writes(on_write=drift)
                with self.assertRaises(revision_tool.InvoiceRevisionError) as caught:
                    self.commit(plan_path)
                message = str(caught.exception)
                self.assertIn(expected_field, message)
                self.assertIn("not the preserved", message)
                self.assertIn("PUT was ISSUED", message)
                lock = json.loads(
                    (self.plan_dir / ".commit-locks" / self.lock_files()[0]).read_text()
                )
                self.assertEqual(lock["status"], "indeterminate_needs_restage")
                self.assertIs(lock["permanent_lock"], False)
                plan_path.unlink()
                (self.plan_dir / ".commit-locks" / self.lock_files()[0]).unlink()
                self.write_calls.clear()

    def test_readback_rejects_line_loss_reorder_or_item_swap(self) -> None:
        cases = {
            "dropped": lambda invoice: invoice["line_items"].pop(),
            "reordered": lambda invoice: invoice["line_items"].reverse(),
            "item swapped": lambda invoice: invoice["line_items"][0].__setitem__(
                "item_id", "96274000000999999"),
            "added": lambda invoice: invoice["line_items"].append(
                fake_line(line_item_id="96274000001559103")),
            "silent edit": lambda invoice: invoice["line_items"][1].__setitem__("rate", 9.0),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                self.invoice = fake_invoice()
                plan_path = self.stage()

                def drift(record, mutate=mutate):
                    self.apply_write(record["payload"])
                    mutate(self.invoice)

                self.allow_writes(on_write=drift)
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    self.commit(plan_path)
                plan_path.unlink()
                (self.plan_dir / ".commit-locks" / self.lock_files()[0]).unlink()
                self.write_calls.clear()

    def test_readback_rejects_any_protected_field_drift(self) -> None:
        cases = (
            {"custom_fields": [{"customfield_id": "96274000001000001", "value": "Changed"}]},
            {"template_id": "96274000000000999"},
            {"notes": "Rewritten by someone else"},
            {"terms": "Net 90"},
            {"date": "2026-09-09"},
            {"payment_made": 100.0},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.invoice = fake_invoice()
                plan_path = self.stage()

                def drift(record, overrides=overrides):
                    self.apply_write(record["payload"])
                    self.invoice.update(overrides)

                self.allow_writes(on_write=drift)
                with self.assertRaises(revision_tool.InvoiceRevisionError):
                    self.commit(plan_path)
                plan_path.unlink()
                (self.plan_dir / ".commit-locks" / self.lock_files()[0]).unlink()
                self.write_calls.clear()

    def test_readback_rejects_a_balance_that_no_longer_matches_the_total(self) -> None:
        plan_path = self.stage()

        def drift(record):
            self.apply_write(record["payload"])
            self.invoice["balance"] = 1.0

        self.allow_writes(on_write=drift)
        with self.assertRaises(revision_tool.InvoiceRevisionError):
            self.commit(plan_path)

    def test_commit_uses_exactly_one_put_route(self) -> None:
        plan_path = self.stage()
        self.allow_writes()
        self.commit(plan_path)
        self.assertEqual(len(self.write_calls), 1)
        self.assertEqual(self.write_calls[0]["method"], "PUT")
        self.assertTrue(
            self.write_calls[0]["url"].startswith(
                f"https://www.zohoapis.ca/books/v3/invoices/{INVOICE_ID}?"
            )
        )
        # Every other transport used in the commit is a plain read.
        for path in self.get_paths:
            self.assertTrue(path.startswith("/books/v3/"), path)


# ---------------------------------------------------------------------------
# No send capability, anywhere
# ---------------------------------------------------------------------------


class NoSendSurfaceTests(unittest.TestCase):
    SOURCE = Path(revision_tool.__file__).read_text(encoding="utf-8")

    def test_module_has_no_mail_transport(self) -> None:
        for marker in ("smtplib", "sendmail", "send_mail", "EmailMessage", "outlook",
                       "graph.microsoft.com", "Mail.Send"):
            self.assertNotIn(marker, self.SOURCE, f"{marker} must not appear in this tool")

    def test_module_names_no_mail_status_or_lifecycle_route(self) -> None:
        for marker in ("/email", "/status/", "/remind", "/submit", "/approve", "/reject",
                       "/void", "/attachment", "/templates", "send=true", "?send"):
            self.assertNotIn(marker, self.SOURCE, f"route {marker} must not appear in this tool")

    def test_only_two_write_verbs_and_two_route_patterns_exist(self) -> None:
        # TWO PUTs -- the general one-invoice revision, and the fixed
        # INV-000051 SHM correction commissioned 2026-08-12 -- plus exactly one
        # POST (create one draft invoice). All three still funnel through the
        # SINGLE network call site, which is the property that matters.
        self.assertEqual(self.SOURCE.count('method="PUT"'), 2)
        self.assertEqual(self.SOURCE.count('method="POST"'), 1)
        self.assertEqual(self.SOURCE.count("urlopen("), 1)
        self.assertEqual(
            revision_tool.INVOICE_PATH_RE.pattern, r"^/books/v3/invoices/([1-9][0-9]*)$"
        )
        self.assertEqual(revision_tool.INVOICE_COLLECTION_RE.pattern, r"^/books/v3/invoices$")

    def test_no_generic_write_helper_is_exported(self) -> None:
        for name in ("api_post", "api_put", "api_delete", "write", "send", "email_invoice",
                     "mark_sent", "void_invoice", "create_invoice", "delete_invoice"):
            self.assertFalse(hasattr(revision_tool, name), f"{name} must not exist")

    def test_put_allowlist_contains_no_lifecycle_or_mail_field(self) -> None:
        for field in ("status", "send", "email", "to_mail_ids", "exchange_rate", "currency_id",
                      "adjustment", "shipping_charge", "balance", "payment_made", "custom_fields"):
            self.assertNotIn(field, revision_tool.ALLOWED_PUT_KEYS)

    def test_commissioned_change_surface_is_exactly_the_brief(self) -> None:
        self.assertEqual(set(revision_tool.HEADER_CHANGE_FIELDS), {
            "customer_id", "reference_number", "date", "due_date",
            "billing_address_id", "shipping_address_id", "notes", "terms",
        })
        self.assertEqual(set(revision_tool.LINE_CHANGE_FIELDS), {
            "quantity", "rate", "discount", "description", "tax_id",
        })
        self.assertEqual(set(revision_tool.ALLOWED_STATUSES), {"draft", "sent"})


# ---------------------------------------------------------------------------
# OAuth scope surface
# ---------------------------------------------------------------------------


class ScopeTests(unittest.TestCase):
    def test_invoice_update_scope_is_prepared_and_allowed(self) -> None:
        self.assertIn("ZohoBooks.invoices.UPDATE", zoho_tool.ALLOWED_WRITE_SCOPES)
        self.assertIn("ZohoBooks.invoices.UPDATE", zoho_tool.SCOPES)
        zoho_tool.validate_scopes(zoho_tool.SCOPES)

    def test_no_invoice_delete_all_or_fullaccess_scope(self) -> None:
        for scope in zoho_tool.SCOPES:
            self.assertNotIn(scope, (
                "ZohoBooks.invoices.DELETE",
                "ZohoBooks.invoices.ALL", "ZohoBooks.fullaccess.all",
            ))

    def test_existing_allowed_scopes_are_preserved(self) -> None:
        for scope in (
            "ZohoBooks.contacts.CREATE", "ZohoBooks.estimates.CREATE",
            "ZohoBooks.banking.CREATE", "ZohoBooks.banking.UPDATE",
            "ZohoInventory.items.CREATE", "ZohoInventory.items.UPDATE",
            "ZohoBooks.invoices.READ", "ZohoInventory.items.READ",
        ):
            self.assertIn(scope, zoho_tool.SCOPES)

    def test_uncommissioned_write_scopes_are_still_refused(self) -> None:
        for scope in (
            "ZohoBooks.invoices.DELETE", "ZohoBooks.invoices.ALL",
            "ZohoBooks.contacts.UPDATE", "ZohoBooks.creditnotes.UPDATE",
            "ZohoBooks.customerpayments.UPDATE", "ZohoInventory.packages.UPDATE",
            "ZohoBooks.fullaccess.all",
            # ZohoBooks.salesorders.CREATE was commissioned on 2026-08-11 for
            # zoho_sales_order_tool.py and .UPDATE on 2026-08-12 for
            # zoho_historical_client_po_reference_tool.py. Every broader
            # sales-order scope, and the Inventory sales-order writes, remain
            # refused.
            "ZohoBooks.salesorders.DELETE",
            "ZohoBooks.salesorders.ALL", "ZohoInventory.salesorders.CREATE",
        ):
            with self.subTest(scope=scope):
                with self.assertRaises(zoho_tool.ZohoError):
                    zoho_tool.validate_scopes([scope])
        # The commissioned sales-order UPDATE belongs to another named tool and
        # must stay unreachable from this one.
        source = Path(revision_tool.__file__).read_text(encoding="utf-8")
        self.assertNotIn("ZohoBooks.salesorders.UPDATE", source)
        self.assertEqual(revision_tool.INVOICE_PATH_RE.pattern, r"^/books/v3/invoices/([1-9][0-9]*)$")
        self.assertIsNone(revision_tool.INVOICE_PATH_RE.match("/books/v3/salesorders/96274000000317001"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
