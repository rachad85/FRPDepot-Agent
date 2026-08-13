"""Independent safety tests for the Zoho Books DRAFT INVOICE CREATION action.

The revision action of the same tool is covered by
``test_zoho_invoice_revision_tool.py``; these tests cover only the second
commissioned action, ``create_draft_invoice``.

These tests never touch live Zoho: every read transport is patched and the
OAuth write transport is patched to fail loudly unless a test explicitly opts
in to a simulated write.
"""
from __future__ import annotations

import argparse
import copy
from decimal import Decimal, ROUND_HALF_UP
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

import zoho_invoice_revision_tool as invoice_tool
import zoho_tool

HERE = Path(__file__).resolve().parent

ORGANIZATION = {
    "organization_id": "110002157575",
    "name": "FRP DEPOTS",
    "currency_code": "CAD",
}
CUSTOMER_ID = "96274000000123001"
CUSTOMER_NAME = "Existing Marine Ltd"
BILL_ADDRESS = "96274000000123005"
SHIP_ADDRESS = "96274000000123006"
OTHER_CUSTOMER_ID = "96274000000999001"
OTHER_BILL_ADDRESS = "96274000000999005"
ITEM_ONE = "96274000000523063"
ITEM_TWO = "96274000000523031"
ITEM_ONE_NAME = "FRP FW PIPE 8in"
ITEM_TWO_NAME = "FNPT Coupling"
TAX_ID = "96274000000000211"
TAX_GROUP_ID = "96274000001071139"
NEW_INVOICE_ID = "96274000001777001"
AUTO_NUMBER = "INV-000101"
SOURCE = "Rachad's written instruction 2026-08-10 11:02, thread 'New invoice'"
CENT = Decimal("0.01")


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def value(raw: object, source: str = SOURCE, **extra: object) -> dict:
    envelope = {"value": raw, "source": source}
    envelope.update(extra)
    return envelope


def fake_contact(
    contact_id: str = CUSTOMER_ID,
    name: str = CUSTOMER_NAME,
    bill: str = BILL_ADDRESS,
    ship: str = SHIP_ADDRESS,
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
        "billing_address": {"address_id": bill, "address": "1 Old Street", "city": "Montreal"},
        "shipping_address": {"address_id": ship, "address": "1 Old Yard", "city": "Montreal"},
    }
    contact.update(overrides)
    return contact


def fake_item(
    item_id: str = ITEM_ONE, name: str = ITEM_ONE_NAME, rate: float = 500.0, **overrides: object
) -> dict:
    item = {
        "item_id": item_id,
        "name": name,
        "sku": "PIDN200200PSI411",
        "status": "active",
        "unit": "pcs",
        "product_type": "goods",
        "item_type": "inventory",
        "rate": rate,
        "description": "catalog description",
        "stock_on_hand": 12.0,
    }
    item.update(overrides)
    return item


class DraftCreationTestCase(unittest.TestCase):
    """Shared harness. No live Zoho call can escape it."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.root = Path(self.temp.name).resolve()
        self.plan_dir = self.root / "zoho_invoice_revision_plans"
        self.plan_dir.mkdir()
        self.counter = 0
        self.organization_record = {**ORGANIZATION, "price_precision": 2}
        self.contacts = {
            CUSTOMER_ID: fake_contact(),
            OTHER_CUSTOMER_ID: fake_contact(
                contact_id=OTHER_CUSTOMER_ID, name="SHM Marine Constructors JV",
                bill=OTHER_BILL_ADDRESS, ship="96274000000999006",
            ),
        }
        self.items = {
            ITEM_ONE: fake_item(),
            ITEM_TWO: fake_item(item_id=ITEM_TWO, name=ITEM_TWO_NAME, rate=250.0),
        }
        self.taxes = [
            {"tax_id": TAX_ID, "tax_name": "GST", "tax_percentage": 5.0, "tax_type": "tax"},
        ]
        self.tax_groups = [{
            "tax_group_id": TAX_GROUP_ID,
            "tax_group_name": "GST+QST",
            "tax_group_percentage": 14.975,
            "taxes": [
                {"tax_id": TAX_ID, "tax_name": "GST", "tax_percentage": 5.0},
                {"tax_id": "96274000000000212", "tax_name": "QST", "tax_percentage": 9.975},
            ],
        }]
        self.invoices: dict[str, dict] = {}
        self.get_paths: list[str] = []
        self.write_calls: list[dict] = []
        self.vault = {
            "api_domain": "https://www.zohoapis.ca",
            "books_organization_id": ORGANIZATION["organization_id"],
            "scopes": [invoice_tool.UPDATE_SCOPE, invoice_tool.CREATE_SCOPE],
        }
        self.patchers = [
            mock.patch.object(invoice_tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(invoice_tool.zoho_tool, "load_vault", side_effect=self.load_vault),
            mock.patch.object(
                invoice_tool.zoho_tool,
                "refresh_access_token",
                side_effect=lambda vault=None: ("token", self.vault),
            ),
            mock.patch.object(invoice_tool.zoho_tool, "save_vault"),
            mock.patch.object(invoice_tool.zoho_tool, "append_receipt"),
            mock.patch.object(invoice_tool.zoho_tool, "api_get", side_effect=self.api_get),
            mock.patch.object(
                invoice_tool,
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
            return {"organizations": [copy.deepcopy(self.organization_record)]}
        if route == "/books/v3/settings/taxes":
            return {"taxes": copy.deepcopy(self.taxes)}
        if route == "/books/v3/settings/taxgroups":
            return {"tax_groups": copy.deepcopy(self.tax_groups)}
        match = re.fullmatch(r"/books/v3/items/([0-9]+)", route)
        if match:
            item = self.items.get(match.group(1))
            return {"item": copy.deepcopy(item) if item else None}
        match = re.fullmatch(r"/books/v3/invoices/([0-9]+)", route)
        if match:
            invoice = self.invoices.get(match.group(1))
            return {"invoice": copy.deepcopy(invoice) if invoice else None}
        match = re.fullmatch(r"/books/v3/contacts/([0-9]+)(/address)?", route)
        if match:
            contact = self.contacts.get(match.group(1))
            if contact is None:
                return {"contact": None, "addresses": []}
            if match.group(2):
                return {"addresses": [
                    copy.deepcopy(contact["billing_address"]),
                    copy.deepcopy(contact["shipping_address"]),
                ]}
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
                result = on_write(record)
                if result is not None:
                    return FakeResponse(result)
                return FakeResponse({"code": 0, "message": "created", "invoice": {
                    "invoice_id": NEW_INVOICE_ID,
                }})
            self.apply_create(body)
            return FakeResponse({"code": 0, "message": "The invoice has been created.", "invoice": {
                "invoice_id": NEW_INVOICE_ID,
            }})

        self.urlopen.side_effect = transport

    def tax_record(self, tax_id: str):
        for record in self.taxes:
            if record["tax_id"] == tax_id:
                return [Decimal(str(record["tax_percentage"]))], Decimal(
                    str(record["tax_percentage"])
                )
        for group in self.tax_groups:
            if group["tax_group_id"] == tax_id:
                return (
                    [Decimal(str(row["tax_percentage"])) for row in group["taxes"]],
                    Decimal(str(group["tax_group_percentage"])),
                )
        raise AssertionError(f"unknown tax {tax_id}")

    def apply_create(self, body: dict) -> dict:
        """A faithful-enough Zoho: create a Draft and recalculate every total."""
        contact = self.contacts[body["customer_id"]]
        lines = []
        sub_total = Decimal(0)
        tax_total = Decimal(0)
        discount_total = Decimal(0)
        for order, sent in enumerate(body["line_items"]):
            item = self.items[sent["item_id"]]
            quantity = Decimal(str(sent["quantity"]))
            rate = Decimal(str(sent["rate"]))
            gross = (quantity * rate).quantize(CENT, rounding=ROUND_HALF_UP)
            raw_discount = sent.get("discount", 0)
            if isinstance(raw_discount, str) and raw_discount.endswith("%"):
                discount = (gross * Decimal(raw_discount[:-1]) / Decimal(100)).quantize(
                    CENT, rounding=ROUND_HALF_UP
                )
            else:
                discount = Decimal(str(raw_discount or 0)).quantize(CENT, rounding=ROUND_HALF_UP)
            item_total = gross - discount
            line_tax = Decimal(0)
            percentage = Decimal(0)
            if sent.get("tax_id"):
                components, percentage = self.tax_record(sent["tax_id"])
                for component in components:
                    line_tax += (item_total * component / Decimal(100)).quantize(
                        CENT, rounding=ROUND_HALF_UP
                    )
            sub_total += item_total
            tax_total += line_tax
            discount_total += discount
            lines.append({
                "line_item_id": f"9627400000177710{order}",
                "item_id": sent["item_id"],
                "name": item["name"],
                "description": sent.get("description", item["description"]),
                "item_order": order,
                "unit": item["unit"],
                "quantity": float(quantity),
                "rate": float(rate),
                "discount": raw_discount or 0,
                "tax_id": sent.get("tax_id", ""),
                "tax_percentage": float(percentage),
                "item_total": float(item_total),
            })
        total = sub_total + tax_total
        billing = copy.deepcopy(contact["billing_address"])
        shipping = copy.deepcopy(contact["shipping_address"])
        for field, block in (("billing_address_id", "billing"), ("shipping_address_id", "shipping")):
            if field not in body:
                continue
            for candidate_contact in self.contacts.values():
                for candidate in (
                    candidate_contact["billing_address"], candidate_contact["shipping_address"]
                ):
                    if candidate["address_id"] == body[field]:
                        if block == "billing":
                            billing = copy.deepcopy(candidate)
                        else:
                            shipping = copy.deepcopy(candidate)
        invoice = {
            "invoice_id": NEW_INVOICE_ID,
            "invoice_number": AUTO_NUMBER,
            "status": "draft",
            "customer_id": body["customer_id"],
            "customer_name": contact["contact_name"],
            "reference_number": body.get("reference_number", ""),
            "date": body["date"],
            "due_date": body["due_date"],
            "currency_id": contact["currency_id"],
            "currency_code": contact["currency_code"],
            "exchange_rate": 1.0,
            "notes": body.get("notes", ""),
            "terms": body.get("terms", ""),
            "discount_type": "item_level",
            "is_inclusive_tax": False,
            "price_precision": 2,
            "shipping_charge": 0.0,
            "adjustment": 0.0,
            "sub_total": float(sub_total),
            "discount_total": float(discount_total),
            "tax_total": float(tax_total),
            "total": float(total),
            "balance": float(total),
            "payment_made": 0.0,
            "credits_applied": 0.0,
            "write_off_amount": 0.0,
            "payments": [],
            "creditnotes": [],
            "packages": [],
            "salesorder_id": "",
            "recurring_invoice_id": "",
            "shipping_status": "",
            "is_emailed": False,
            "billing_address": billing,
            "shipping_address": shipping,
            "line_items": lines,
            "last_modified_time": "2026-08-10T12:00:00-0400",
        }
        self.invoices[NEW_INVOICE_ID] = invoice
        return invoice

    def lock_files(self) -> list[str]:
        folder = self.plan_dir / ".commit-locks"
        return sorted(path.name for path in folder.glob("*.json")) if folder.exists() else []

    # -- helpers ---------------------------------------------------------
    def input_path(self, raw: object) -> Path:
        self.counter += 1
        path = self.root / f"input_{self.counter}.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return path

    def request(self, fields: dict | None = None, lines: list | None = None) -> dict:
        return {
            "customer_id": CUSTOMER_ID,
            "customer_name": CUSTOMER_NAME,
            "fields": fields if fields is not None else {
                "date": value("2026-08-10"),
                "due_date": value("2026-09-09"),
            },
            "lines": lines if lines is not None else [{
                "item_id": ITEM_ONE,
                "item_name": ITEM_ONE_NAME,
                "quantity": value("2"),
                "rate": value("500.00"),
            }],
        }

    def stage(self, payload: dict | None = None) -> Path:
        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            invoice_tool.command_stage_create(
                argparse.Namespace(input=str(self.input_path(payload or self.request())))
            )
        self.stage_output = buffer.getvalue()
        plans = sorted(self.plan_dir.glob("*.json"))
        self.assertTrue(plans, "stage did not write a plan")
        return plans[-1]

    def commit(self, plan: Path, approval: str = "APPROVED") -> str:
        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            invoice_tool.command_commit(argparse.Namespace(plan=str(plan), approval=approval))
        return buffer.getvalue()

    def rewrite_plan(self, plan: Path, mutate) -> Path:
        data = json.loads(plan.read_text(encoding="utf-8"))
        data.pop("sha256")
        mutate(data)
        data["sha256"] = invoice_tool.digest_for(data)
        plan.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return plan

    def assertNoWrites(self) -> None:
        self.assertEqual(self.write_calls, [], "a write was attempted")
        self.assertEqual(self.invoices, {}, "an invoice was created")

    def two_line_request(self) -> dict:
        return self.request(
            fields={
                "date": value("2026-08-10"),
                "due_date": value("2026-09-09"),
                "reference_number": value("SHM PO 0000031"),
                "notes": value("Thank you for your business."),
                "terms": value("Net 30"),
                "billing_address_id": value(BILL_ADDRESS),
                "shipping_address_id": value(SHIP_ADDRESS),
            },
            lines=[
                {
                    "item_id": ITEM_ONE, "item_name": ITEM_ONE_NAME,
                    "quantity": value("2"), "rate": value("500.00"),
                    "description": value("8 inch filament wound pipe"),
                    "tax_id": value(TAX_ID, tax_percentage="5"),
                },
                {
                    "item_id": ITEM_TWO, "item_name": ITEM_TWO_NAME,
                    "quantity": value("4"), "rate": value("250.00"),
                    "discount": value("10%"),
                    "tax_id": value(TAX_ID, tax_percentage="5"),
                },
            ],
        )


# ---------------------------------------------------------------------------
# OAuth scope surface
# ---------------------------------------------------------------------------


class CreateScopeTests(unittest.TestCase):
    def test_create_scope_is_prepared_and_exactly_allowlisted(self) -> None:
        self.assertEqual(invoice_tool.CREATE_SCOPE, "ZohoBooks.invoices.CREATE")
        self.assertIn(invoice_tool.CREATE_SCOPE, zoho_tool.ALLOWED_WRITE_SCOPES)
        self.assertIn(invoice_tool.CREATE_SCOPE, zoho_tool.SCOPES)
        zoho_tool.validate_scopes(zoho_tool.SCOPES)

    def test_the_prepared_invoice_scopes_are_exactly_update_and_create(self) -> None:
        self.assertEqual(
            sorted(scope for scope in zoho_tool.SCOPES if ".invoices." in scope),
            [
                "ZohoBooks.invoices.CREATE",
                "ZohoBooks.invoices.READ",
                "ZohoBooks.invoices.UPDATE",
                "ZohoInventory.invoices.READ",
            ],
        )

    def test_every_broader_or_different_invoice_scope_is_refused(self) -> None:
        for scope in (
            "ZohoBooks.invoices.DELETE",
            "ZohoBooks.invoices.ALL",
            "ZohoBooks.fullaccess.all",
            "ZohoInventory.invoices.CREATE",
            "ZohoInventory.invoices.UPDATE",
            "ZohoInventory.invoices.ALL",
            "ZohoBooks.creditnotes.CREATE",
            "ZohoBooks.customerpayments.CREATE",
            # ZohoBooks.salesorders.CREATE was commissioned on 2026-08-11 for
            # zoho_sales_order_tool.py and .UPDATE on 2026-08-12 for
            # zoho_historical_client_po_reference_tool.py, so neither is
            # uncommissioned any more. Every BROADER sales-order scope is still
            # refused, which is what this test is for.
            "ZohoBooks.salesorders.DELETE",
            "ZohoBooks.salesorders.ALL",
            "ZohoInventory.salesorders.CREATE",
            "ZohoBooks.contacts.UPDATE",
        ):
            with self.subTest(scope=scope):
                with self.assertRaises(zoho_tool.ZohoError):
                    zoho_tool.validate_scopes([scope])
                self.assertNotIn(scope, zoho_tool.SCOPES)

    def test_commissioned_estimate_update_scope_does_not_expand_invoice_tool(self) -> None:
        self.assertIn("ZohoBooks.estimates.UPDATE", zoho_tool.SCOPES)
        source = Path(invoice_tool.__file__).read_text(encoding="utf-8")
        self.assertNotIn("ZohoBooks.estimates.UPDATE", source)


# ---------------------------------------------------------------------------
# Input schema: every value carries a source, nothing else is reachable
# ---------------------------------------------------------------------------


class CreateInputSchemaTests(DraftCreationTestCase):
    def test_forbidden_header_fields_refused(self) -> None:
        for field in (
            "invoice_number", "ignore_auto_number_generation", "status", "currency_id",
            "currency_code", "exchange_rate", "adjustment", "shipping_charge", "balance",
            "custom_fields", "template_id", "salesperson_id", "send", "email", "to_mail_ids",
            "salesorder_id", "discount_type", "is_inclusive_tax", "line_items",
        ):
            with self.subTest(field=field):
                request = self.request()
                request["fields"][field] = value("x")
                with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
                    invoice_tool.validate_create_input(request)
                self.assertIn("uncommissioned", str(caught.exception))

    def test_forbidden_line_fields_refused(self) -> None:
        for field in (
            "name", "unit", "account_id", "item_total", "line_item_id", "warehouse_id",
            "item_custom_fields", "tax_percentage", "product_type", "header_name",
        ):
            with self.subTest(field=field):
                request = self.request()
                request["lines"][0][field] = value("x")
                with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
                    invoice_tool.validate_create_input(request)
                self.assertIn("uncommissioned", str(caught.exception))

    def test_a_line_must_name_an_existing_item(self) -> None:
        for missing in ("item_id", "item_name", "quantity", "rate"):
            with self.subTest(missing=missing):
                request = self.request()
                request["lines"][0].pop(missing)
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    invoice_tool.validate_create_input(request)

    def test_every_number_needs_an_explicit_source(self) -> None:
        for field in ("quantity", "rate"):
            with self.subTest(field=field):
                request = self.request()
                request["lines"][0][field] = {"value": "2"}
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    invoice_tool.validate_create_input(request)
                request = self.request()
                request["lines"][0][field] = value("2", source="")
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    invoice_tool.validate_create_input(request)
        request = self.request()
        request["fields"]["date"] = {"value": "2026-08-10"}
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            invoice_tool.validate_create_input(request)

    def test_numbers_must_be_canonical_decimal_text(self) -> None:
        for field, bad in (
            ("quantity", 3), ("rate", 12.5), ("quantity", "0"), ("quantity", "-2"),
            ("rate", "-1"), ("discount", 5), ("discount", "101%"), ("rate", "1e5"),
            ("quantity", "3.00000001"),
        ):
            with self.subTest(field=field, bad=bad):
                request = self.request()
                request["lines"][0][field] = value(bad)
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    invoice_tool.validate_create_input(request)

    def test_taxed_line_must_state_the_exact_rate_it_expects(self) -> None:
        request = self.request()
        request["lines"][0]["tax_id"] = value(TAX_ID)
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            invoice_tool.validate_create_input(request)
        request = self.request()
        request["lines"][0]["tax_id"] = value(TAX_ID, tax_percentage="five")
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            invoice_tool.validate_create_input(request)
        request = self.request()
        request["lines"][0]["tax_id"] = value(TAX_ID, tax_percentage="5")
        clean = invoice_tool.validate_create_input(request)
        self.assertEqual(clean["lines"][0]["tax_id"]["tax_percentage"], "5")

    def test_both_dates_are_required_and_ordered(self) -> None:
        for missing in ("date", "due_date"):
            with self.subTest(missing=missing):
                request = self.request()
                request["fields"].pop(missing)
                with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
                    invoice_tool.validate_create_input(request)
                self.assertIn(missing, str(caught.exception))
        request = self.request(fields={
            "date": value("2026-09-09"), "due_date": value("2026-08-10"),
        })
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            invoice_tool.validate_create_input(request)

    def test_at_least_one_line_is_required(self) -> None:
        for empty in ([], None, {}, "x", 0):
            with self.subTest(empty=empty):
                request = self.request()
                request["lines"] = empty
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    invoice_tool.validate_create_input(request)

    def test_duplicate_item_lines_need_distinct_descriptions(self) -> None:
        line = {
            "item_id": ITEM_ONE, "item_name": ITEM_ONE_NAME,
            "quantity": value("2"), "rate": value("500.00"),
        }
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            invoice_tool.validate_create_input(
                self.request(lines=[copy.deepcopy(line), copy.deepcopy(line)])
            )
        self.assertIn("distinct description", str(caught.exception))
        same = copy.deepcopy(line)
        same["description"] = value("run A")
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            invoice_tool.validate_create_input(
                self.request(lines=[copy.deepcopy(same), copy.deepcopy(same)])
            )
        first, second = copy.deepcopy(line), copy.deepcopy(line)
        first["description"] = value("run A")
        second["description"] = value("run B")
        clean = invoice_tool.validate_create_input(self.request(lines=[first, second]))
        self.assertEqual(len(clean["lines"]), 2)

    def test_ids_must_be_canonical(self) -> None:
        for bad in ("0", "-5", "01", "", "abc", 5, None, True):
            with self.subTest(bad=bad):
                request = self.request()
                request["customer_id"] = bad
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    invoice_tool.validate_create_input(request)
                request = self.request()
                request["lines"][0]["item_id"] = bad
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    invoice_tool.validate_create_input(request)

    def test_unknown_or_missing_top_level_fields_refused(self) -> None:
        request = self.request()
        request["extra"] = 1
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            invoice_tool.validate_create_input(request)
        request = self.request()
        request.pop("customer_name")
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            invoice_tool.validate_create_input(request)


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


class CreateStageTests(DraftCreationTestCase):
    def test_stage_reads_only_and_writes_nothing(self) -> None:
        plan_path = self.stage(self.two_line_request())
        self.assertNoWrites()
        self.urlopen.assert_not_called()
        self.assertEqual(self.lock_files(), [])
        self.assertIn("STAGED_NOT_COMMITTED", self.stage_output)
        self.assertIn("Zoho writes performed by this stage: 0", self.stage_output)
        self.assertIn("DRAFT status", self.stage_output)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["action"], "create_draft_invoice")
        self.assertIs(plan["live_evidence"]["email_sent"], False)
        self.assertIs(plan["risk"]["email_sent"], False)
        self.assertIs(plan["risk"]["single_post"], True)
        receipt = self.append_receipt.call_args[0]
        self.assertIn("create_draft_invoice_plan_staged_not_committed", receipt[0])
        self.assertIn("zoho_writes=0", receipt[1])
        self.assertIn("invoices_created=0", receipt[1])
        self.assertIn("email_sent=false", receipt[1])

    def test_plan_expiry_is_exactly_24_hours_and_hashed(self) -> None:
        plan = json.loads(self.stage().read_text(encoding="utf-8"))
        created = invoice_tool.parse_time(plan["created_utc"], "created")
        expires = invoice_tool.parse_time(plan["expires_utc"], "expires")
        self.assertEqual((expires - created).total_seconds(), 24 * 3600)
        core = dict(plan)
        core.pop("sha256")
        self.assertEqual(plan["sha256"], invoice_tool.digest_for(core))

    def test_post_payload_uses_auto_numbering_and_no_currency_override(self) -> None:
        plan = json.loads(self.stage(self.two_line_request()).read_text(encoding="utf-8"))
        post = plan["live_evidence"]["post_payload"]
        for forbidden in (
            "invoice_number", "ignore_auto_number_generation", "status", "currency_id",
            "currency_code", "exchange_rate", "adjustment", "shipping_charge", "custom_fields",
            "template_id", "send", "email", "to_mail_ids", "discount_type", "is_inclusive_tax",
        ):
            self.assertNotIn(forbidden, post)
        self.assertEqual(set(post) - invoice_tool.ALLOWED_POST_KEYS, set())
        self.assertEqual(plan["live_evidence"]["post_endpoint"], "POST /books/v3/invoices")
        self.assertEqual(
            plan["live_evidence"]["settings"]["invoice_numbering"],
            invoice_tool.AUTO_NUMBERING_NOTE,
        )
        self.assertEqual(plan["live_evidence"]["customer"]["currency_code"], "CAD")

    def test_lines_keep_the_requested_order_and_link_to_live_items(self) -> None:
        plan = json.loads(self.stage(self.two_line_request()).read_text(encoding="utf-8"))
        post = plan["live_evidence"]["post_payload"]
        self.assertEqual([line["item_id"] for line in post["line_items"]], [ITEM_ONE, ITEM_TWO])
        self.assertEqual([row["index"] for row in plan["live_evidence"]["lines"]], [0, 1])
        self.assertEqual(sorted(plan["live_evidence"]["items"]), sorted({ITEM_ONE, ITEM_TWO}))
        for row in plan["live_evidence"]["lines"]:
            self.assertTrue(row["sources"]["quantity"])
            self.assertTrue(row["sources"]["rate"])

    def test_customer_must_already_exist_and_be_an_active_customer(self) -> None:
        request = self.request()
        request["customer_id"] = "96274000000000001"
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("never creates one", str(caught.exception))
        for overrides in ({"contact_type": "vendor"}, {"status": "inactive"}):
            with self.subTest(overrides=overrides):
                self.contacts[CUSTOMER_ID] = fake_contact(**overrides)
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    self.stage()
                self.assertNoWrites()

    def test_customer_name_must_match_the_live_record(self) -> None:
        request = self.request()
        request["customer_name"] = "Someone Else Ltd"
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("not the stated", str(caught.exception))

    def test_items_must_exist_be_active_and_match_their_stated_name(self) -> None:
        request = self.request()
        request["lines"][0]["item_id"] = "96274000000000002"
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("never creates one", str(caught.exception))
        self.items[ITEM_ONE] = fake_item(status="inactive")
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.stage()
        self.assertIn("not active", str(caught.exception))
        self.items[ITEM_ONE] = fake_item()
        request = self.request()
        request["lines"][0]["item_name"] = "Some Other Pipe"
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("not the stated", str(caught.exception))
        self.assertNoWrites()

    def test_addresses_must_be_owned_by_the_selected_customer(self) -> None:
        request = self.request()
        request["fields"]["billing_address_id"] = value(OTHER_BILL_ADDRESS)
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("not one of the addresses owned", str(caught.exception))
        self.assertNoWrites()

    def test_tax_must_be_a_live_tax_at_the_stated_rate(self) -> None:
        request = self.request()
        request["lines"][0]["tax_id"] = value("96274000000000999", tax_percentage="5")
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("not live taxes or tax groups", str(caught.exception))
        request = self.request()
        request["lines"][0]["tax_id"] = value(TAX_ID, tax_percentage="13")
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("live at 5", str(caught.exception))
        self.assertNoWrites()

    def test_a_tax_group_resolves_but_its_cents_are_not_asserted(self) -> None:
        request = self.request()
        request["lines"][0]["tax_id"] = value(TAX_GROUP_ID, tax_percentage="14.975")
        plan = json.loads(self.stage(request).read_text(encoding="utf-8"))
        tax = plan["live_evidence"]["taxes"][TAX_GROUP_ID]
        self.assertEqual(tax["kind"], "tax_group")
        self.assertEqual(len(tax["components"]), 2)
        totals = plan["live_evidence"]["totals"]
        self.assertTrue(totals["sub_total_deterministic"])
        self.assertFalse(totals["tax_total_deterministic"])
        self.assertNotIn("total", totals["verified"])
        self.assertIn("sub_total", totals["verified"])
        self.assertIn("ESTIMATE", self.stage_output)

    def test_discount_may_not_exceed_the_line(self) -> None:
        request = self.request()
        request["lines"][0]["discount"] = value("5000.00")
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.stage(request)
        self.assertIn("exceeds the line value", str(caught.exception))


# ---------------------------------------------------------------------------
# Independent Decimal arithmetic
# ---------------------------------------------------------------------------


class CreateTotalsTests(DraftCreationTestCase):
    def test_totals_are_computed_exactly_and_shown_before_approval(self) -> None:
        plan = json.loads(self.stage(self.two_line_request()).read_text(encoding="utf-8"))
        rows = plan["live_evidence"]["lines"]
        # 2 x 500.00 = 1000.00; 4 x 250.00 = 1000.00 less 10% = 900.00
        self.assertEqual(rows[0]["expected_item_total"], "1000.00")
        self.assertEqual(rows[1]["expected_discount_amount"], "100.00")
        self.assertEqual(rows[1]["expected_item_total"], "900.00")
        totals = plan["live_evidence"]["totals"]["expected"]
        self.assertEqual(totals["sub_total"], "1900.00")
        self.assertEqual(totals["discount_total"], "100.00")
        self.assertEqual(totals["tax_total"], "95.00")
        self.assertEqual(totals["total"], "1995.00")
        self.assertEqual(totals["balance"], "1995.00")
        self.assertTrue(plan["live_evidence"]["totals"]["total_deterministic"])
        self.assertIn("1995.00", self.stage_output)

    def test_half_up_rounding_beats_binary_float(self) -> None:
        self.items[ITEM_ONE] = fake_item(rate=0.145)
        request = self.request(lines=[{
            "item_id": ITEM_ONE, "item_name": ITEM_ONE_NAME,
            "quantity": value("3"), "rate": value("0.145"),
        }])
        plan = json.loads(self.stage(request).read_text(encoding="utf-8"))
        # 3 x 0.145 = 0.435 -> half-up 0.44, which binary floats would give as 0.43.
        self.assertEqual(plan["live_evidence"]["lines"][0]["expected_item_total"], "0.44")
        self.assertEqual(plan["live_evidence"]["totals"]["expected"]["total"], "0.44")

    def test_assumed_precision_with_rounding_is_not_claimed_exact(self) -> None:
        self.organization_record.pop("price_precision")
        request = self.request(lines=[{
            "item_id": ITEM_ONE, "item_name": ITEM_ONE_NAME,
            "quantity": value("3"), "rate": value("0.145"),
        }])
        plan = json.loads(self.stage(request).read_text(encoding="utf-8"))
        totals = plan["live_evidence"]["totals"]
        self.assertFalse(totals["sub_total_deterministic"])
        self.assertEqual(totals["verified"], {})
        self.assertIn("two decimals were assumed", totals["basis"])
        self.assertEqual(
            plan["live_evidence"]["settings"]["price_precision_source"],
            "Zoho default: the organization record did not state it",
        )

    def test_assumed_precision_without_rounding_is_still_exact(self) -> None:
        self.organization_record.pop("price_precision")
        plan = json.loads(self.stage().read_text(encoding="utf-8"))
        totals = plan["live_evidence"]["totals"]
        self.assertTrue(totals["sub_total_deterministic"])
        self.assertEqual(totals["verified"]["total"], "1000.00")


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class CreateApprovalTests(DraftCreationTestCase):
    def test_wrong_approval_refused_before_lock_token_or_network(self) -> None:
        plan_path = self.stage()
        self.api_get_mock.reset_mock()
        for bad in ("approved", "Approved", " APPROVED", "APPROVED ", "APPROVED\n", "", "YES"):
            with self.subTest(bad=bad):
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    self.commit(plan_path, approval=bad)
                self.assertNoWrites()
                self.assertEqual(self.lock_files(), [], "no lock without approval")
                self.api_get_mock.assert_not_called()

    def test_staging_alone_never_creates_anything(self) -> None:
        self.stage(self.two_line_request())
        self.assertNoWrites()
        self.assertEqual(self.lock_files(), [])


# ---------------------------------------------------------------------------
# Plan integrity
# ---------------------------------------------------------------------------


class CreatePlanIntegrityTests(DraftCreationTestCase):
    def test_tampered_plan_refused(self) -> None:
        plan_path = self.stage()
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        data["payload"]["lines"][0]["quantity"]["value"] = "99"
        plan_path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn("hash check failed", str(caught.exception))
        self.assertNoWrites()

    def test_rehashed_tampering_still_refused(self) -> None:
        mutations = {
            "payload quantity": lambda d: d["payload"]["lines"][0]["quantity"].__setitem__(
                "value", "99"),
            "post payload field": lambda d: d["live_evidence"]["post_payload"].__setitem__(
                "invoice_number", "INV-999999"),
            "auto number override": lambda d: d["live_evidence"]["post_payload"].__setitem__(
                "ignore_auto_number_generation", True),
            "currency override": lambda d: d["live_evidence"]["post_payload"].__setitem__(
                "exchange_rate", 1.37),
            "post line rate": lambda d: d["live_evidence"]["post_payload"]["line_items"][0]
                .__setitem__("rate", 9.0),
            "extra post line": lambda d: d["live_evidence"]["post_payload"]["line_items"].append(
                {"item_id": ITEM_TWO, "quantity": 1.0, "rate": 250.0}),
            "endpoint": lambda d: d["live_evidence"].__setitem__(
                "post_endpoint", "POST /books/v3/invoices/" + NEW_INVOICE_ID),
            "expected total": lambda d: d["live_evidence"]["totals"]["expected"].__setitem__(
                "total", "1.00"),
            "verified total": lambda d: d["live_evidence"]["totals"]["verified"].__setitem__(
                "total", "1.00"),
            "line total": lambda d: d["live_evidence"]["lines"][0].__setitem__(
                "expected_item_total", "1.00"),
            "customer": lambda d: d["live_evidence"]["customer"].__setitem__(
                "customer_id", OTHER_CUSTOMER_ID),
            "item name": lambda d: d["live_evidence"]["items"][ITEM_ONE].__setitem__(
                "name", "Something Else"),
            "settings precision": lambda d: d["live_evidence"]["settings"].__setitem__(
                "price_precision", 4),
            "numbering note": lambda d: d["live_evidence"]["settings"].__setitem__(
                "invoice_numbering", "caller supplies the number"),
            "email flag": lambda d: d["live_evidence"].__setitem__("email_sent", True),
            "risk block": lambda d: d["risk"].__setitem__("single_post", False),
            "risk note": lambda d: d["risk"].__setitem__("note", "harmless"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                plan_path = self.stage()
                self.rewrite_plan(plan_path, mutate)
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    self.commit(plan_path)
                self.assertNoWrites()
                plan_path.unlink()

    def test_wrong_origin_tool_action_and_version_refused(self) -> None:
        mutations = (
            lambda d: d["origin"].__setitem__("tool_path", r"C:\evil\tool.py"),
            lambda d: d.__setitem__("tool", "Some Other Tool"),
            lambda d: d.__setitem__("action", "invoice_delete"),
            lambda d: d.__setitem__("action", "invoice_revision"),
            lambda d: d.__setitem__("schema_version", 2),
            lambda d: d.__setitem__("approval_required", "OK"),
            lambda d: d.__setitem__("nonce", "zz"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                plan_path = self.stage()
                self.rewrite_plan(plan_path, mutate)
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    self.commit(plan_path)
                self.assertNoWrites()
                plan_path.unlink()

    def test_expired_and_extended_plans_refused(self) -> None:
        plan_path = self.stage()

        def age(data):
            shift = invoice_tool.timedelta(hours=25)
            for key in ("created_utc", "expires_utc"):
                data[key] = (invoice_tool.parse_time(data[key], key) - shift).isoformat()

        self.rewrite_plan(plan_path, age)
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn("expired", str(caught.exception))
        plan_path.unlink()

        plan_path = self.stage()
        self.rewrite_plan(plan_path, lambda d: d.__setitem__(
            "expires_utc",
            (invoice_tool.parse_time(d["created_utc"], "c")
             + invoice_tool.timedelta(hours=72)).isoformat(),
        ))
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn("24-hour", str(caught.exception))
        self.assertNoWrites()

    def test_plan_outside_the_plan_folder_refused(self) -> None:
        plan_path = self.stage()
        outside = self.root / "outside.json"
        outside.write_text(plan_path.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            self.commit(outside)
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            self.commit(Path("relative.json"))
        self.assertNoWrites()

    def test_wrong_organization_refused_before_write(self) -> None:
        plan_path = self.stage()
        self.rewrite_plan(plan_path, lambda d: d["organization"].__setitem__(
            "organization_id", "110009999999"))
        self.allow_writes()
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            self.commit(plan_path)
        self.assertNoWrites()


# ---------------------------------------------------------------------------
# Write transport
# ---------------------------------------------------------------------------


class CreateWriteTransportTests(DraftCreationTestCase):
    def payload(self) -> dict:
        return {
            "customer_id": CUSTOMER_ID,
            "date": "2026-08-10",
            "due_date": "2026-09-09",
            "line_items": [{"item_id": ITEM_ONE, "quantity": 2.0, "rate": 500.0}],
        }

    def call(self, method="POST", path="/books/v3/invoices", payload=None):
        return invoice_tool.oauth_invoice_create_write_allowed(
            "token", "https://www.zohoapis.ca", method, path,
            ORGANIZATION["organization_id"], self.payload() if payload is None else payload,
        )

    def test_only_post_is_reachable(self) -> None:
        for method in ("GET", "PUT", "DELETE", "PATCH", "HEAD", "post"):
            with self.subTest(method=method):
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    self.call(method=method)
        self.assertNoWrites()

    def test_only_the_exact_invoice_collection_route_is_reachable(self) -> None:
        forbidden = (
            "/books/v3/invoices/",
            f"/books/v3/invoices/{NEW_INVOICE_ID}",
            f"/books/v3/invoices/{NEW_INVOICE_ID}/status/draft",
            f"/books/v3/invoices/{NEW_INVOICE_ID}/status/sent",
            f"/books/v3/invoices/{NEW_INVOICE_ID}/status/void",
            f"/books/v3/invoices/{NEW_INVOICE_ID}/email",
            f"/books/v3/invoices/{NEW_INVOICE_ID}/submit",
            f"/books/v3/invoices/{NEW_INVOICE_ID}/approve",
            f"/books/v3/invoices/{NEW_INVOICE_ID}/reminder",
            f"/books/v3/invoices/{NEW_INVOICE_ID}/payments",
            f"/books/v3/invoices/{NEW_INVOICE_ID}/attachment",
            "/books/v3/invoices/email",
            "/books/v3/invoices/fromsalesorder",
            "/books/v3/invoices/bulk",
            "/books/v3/estimates/1/status/accepted",
            "/books/v3/estimates",
            "/books/v3/contacts",
            "/books/v3/creditnotes",
            "/books/v3/customerpayments",
            "/inventory/v1/items",
            "/books/v3/invoices?send=true",
            "/books/v3/invoices?ignore_auto_number_generation=true",
            " /books/v3/invoices",
            "https://evil.invalid/books/v3/invoices",
        )
        for path in forbidden:
            with self.subTest(path=path):
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    self.call(path=path)
        self.assertNoWrites()

    def test_payload_field_allowlist_blocks_number_currency_and_mail(self) -> None:
        for extra in (
            "invoice_number", "ignore_auto_number_generation", "status", "currency_id",
            "currency_code", "exchange_rate", "adjustment", "shipping_charge", "custom_fields",
            "template_id", "send", "email", "to_mail_ids", "salesorder_id", "discount_type",
            "is_inclusive_tax", "payment_options", "balance",
        ):
            with self.subTest(extra=extra):
                payload = self.payload()
                payload[extra] = "x"
                with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
                    self.call(payload=payload)
                self.assertIn("uncommissioned", str(caught.exception))

    def test_required_fields_and_line_rules(self) -> None:
        for missing in ("customer_id", "date", "due_date", "line_items"):
            with self.subTest(missing=missing):
                payload = self.payload()
                payload.pop(missing)
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    self.call(payload=payload)
        payload = self.payload()
        payload["line_items"] = []
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            self.call(payload=payload)
        payload = self.payload()
        payload["line_items"][0].pop("item_id")
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.call(payload=payload)
        self.assertIn("existing item", str(caught.exception))
        payload = self.payload()
        payload["line_items"][0]["name"] = "free text line"
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            self.call(payload=payload)
        payload = self.payload()
        payload["line_items"][0]["item_total"] = 1.0
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            self.call(payload=payload)

    def test_url_is_built_from_the_zoho_domain_with_only_the_org_id(self) -> None:
        self.allow_writes()
        self.call()
        self.assertEqual(len(self.write_calls), 1)
        self.assertEqual(
            self.write_calls[0]["url"],
            "https://www.zohoapis.ca/books/v3/invoices"
            f"?organization_id={ORGANIZATION['organization_id']}",
        )
        self.assertEqual(self.write_calls[0]["method"], "POST")

    def test_non_zero_zoho_response_code_is_a_failure(self) -> None:
        self.urlopen.side_effect = lambda request, timeout=60: FakeResponse(
            {"code": 15, "message": "nope"}
        )
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            self.call()
        self.urlopen.side_effect = lambda request, timeout=60: FakeResponse({"ok": True})
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            self.call()


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


class CreateCommitTests(DraftCreationTestCase):
    def test_successful_creation_writes_once_and_verifies_a_draft(self) -> None:
        plan_path = self.stage(self.two_line_request())
        self.allow_writes()
        output = json.loads(self.commit(plan_path))
        self.assertEqual(output["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(output["action"], "create_draft_invoice")
        self.assertEqual(output["invoice_status_verified"], "draft")
        self.assertEqual(output["invoice_number"], AUTO_NUMBER)
        self.assertEqual(output["invoice_number_assigned_by"], "Zoho auto-numbering")
        self.assertEqual(output["invoice_id"], NEW_INVOICE_ID)
        self.assertEqual(output["lines_created"], 2)
        self.assertEqual(output["currency_code"], "CAD")
        self.assertIs(output["email_sent"], False)
        self.assertIs(output["replay_locked"], True)
        self.assertEqual(output["totals_verified"]["total"], "1995.00")
        self.assertEqual(len(self.write_calls), 1, "exactly one POST")
        created = self.invoices[NEW_INVOICE_ID]
        self.assertEqual(created["status"], "draft")
        self.assertIs(created["is_emailed"], False)
        self.assertEqual(created["total"], 1995.0)
        self.assertEqual([line["item_id"] for line in created["line_items"]], [ITEM_ONE, ITEM_TWO])
        lock = json.loads((self.plan_dir / ".commit-locks" / self.lock_files()[0]).read_text())
        self.assertEqual(lock["status"], "committed_verified")
        self.assertIs(lock["no_retry"], True)
        receipt = self.append_receipt.call_args[0]
        self.assertIn("create_draft_invoice_committed_verified", receipt[0])
        self.assertIn("status=draft", receipt[1])
        self.assertIn("email_sent=false", receipt[1])

    def test_commit_uses_exactly_one_post_to_the_collection_route(self) -> None:
        plan_path = self.stage()
        self.allow_writes()
        self.commit(plan_path)
        self.assertEqual(len(self.write_calls), 1)
        self.assertEqual(self.write_calls[0]["method"], "POST")
        self.assertEqual(
            self.write_calls[0]["url"].split("?", 1)[0],
            "https://www.zohoapis.ca/books/v3/invoices",
        )
        self.assertNotIn("send", self.write_calls[0]["url"])
        for path in self.get_paths:
            self.assertTrue(path.startswith("/books/v3/"), path)

    def test_lock_exists_before_the_post(self) -> None:
        plan_path = self.stage()
        self.allow_writes()
        self.commit(plan_path)
        self.assertEqual(len(self.write_calls[0]["locks_before"]), 1)

    def test_replayed_plan_refused(self) -> None:
        plan_path = self.stage()
        self.allow_writes()
        self.commit(plan_path)
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn("already entered commit", str(caught.exception))
        self.assertEqual(len(self.write_calls), 1, "a replay must not write again")

    def test_missing_create_scope_refused_before_write(self) -> None:
        plan_path = self.stage()
        self.vault["scopes"] = [invoice_tool.UPDATE_SCOPE, "ZohoBooks.invoices.READ"]
        self.allow_writes()
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn(invoice_tool.CREATE_SCOPE, str(caught.exception))
        self.assertNoWrites()

    def test_dependency_drift_after_review_refuses_before_write(self) -> None:
        cases = {
            "customer renamed": lambda self: self.contacts.__setitem__(
                CUSTOMER_ID, fake_contact(name="Existing Marine Ltd Renamed")),
            "customer deactivated": lambda self: self.contacts.__setitem__(
                CUSTOMER_ID, fake_contact(status="inactive")),
            "item renamed": lambda self: self.items.__setitem__(
                ITEM_ONE, fake_item(name="Renamed Pipe")),
            "item deactivated": lambda self: self.items.__setitem__(
                ITEM_ONE, fake_item(status="inactive")),
            "precision changed": lambda self: self.organization_record.__setitem__(
                "price_precision", 4),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                plan_path = self.stage()
                mutate(self)
                self.allow_writes()
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    self.commit(plan_path)
                self.assertNoWrites()
                plan_path.unlink()
                self.contacts[CUSTOMER_ID] = fake_contact()
                self.items[ITEM_ONE] = fake_item()
                self.organization_record["price_precision"] = 2
                for name in self.lock_files():
                    (self.plan_dir / ".commit-locks" / name).unlink()

    def test_tax_changed_after_review_refused(self) -> None:
        request = self.request()
        request["lines"][0]["tax_id"] = value(TAX_ID, tax_percentage="5")
        plan_path = self.stage(request)
        self.taxes[0]["tax_percentage"] = 13.0
        self.allow_writes()
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            self.commit(plan_path)
        self.assertNoWrites()

    def test_no_retry_after_a_failed_post(self) -> None:
        plan_path = self.stage()

        def fail(record):
            raise HTTPError(record["url"], 400, "Bad Request", None, io.BytesIO(b'{"code":15}'))

        self.allow_writes(on_write=fail)
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        message = str(caught.exception)
        self.assertIn("permanently locked", message)
        self.assertIn("POST was ISSUED", message)
        self.assertIn("NOTHING was cleaned up", message)
        self.assertEqual(len(self.write_calls), 1)
        lock = json.loads((self.plan_dir / ".commit-locks" / self.lock_files()[0]).read_text())
        self.assertEqual(lock["status"], "indeterminate")
        self.assertIs(lock["no_retry"], True)
        self.allow_writes()
        with self.assertRaises(invoice_tool.InvoiceRevisionError):
            self.commit(plan_path)
        self.assertEqual(len(self.write_calls), 1, "no second attempt is possible")

    def test_timeout_is_indeterminate_and_locks(self) -> None:
        plan_path = self.stage()

        def timeout(record):
            raise URLError("timed out")

        self.allow_writes(on_write=timeout)
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn("indeterminate", str(caught.exception))
        lock = json.loads((self.plan_dir / ".commit-locks" / self.lock_files()[0]).read_text())
        self.assertIs(lock["plan_locked_indeterminate"], True)

    def test_missing_invoice_id_in_the_response_is_indeterminate(self) -> None:
        plan_path = self.stage()
        self.allow_writes(on_write=lambda record: {"code": 0, "message": "created"})
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        message = str(caught.exception)
        self.assertIn("UNKNOWN", message)
        self.assertIn("POST was ISSUED", message)
        lock = json.loads((self.plan_dir / ".commit-locks" / self.lock_files()[0]).read_text())
        self.assertEqual(lock["invoice_id"], "")
        self.assertIs(lock["no_retry"], True)

    def test_readback_status_must_be_exactly_draft(self) -> None:
        for status in ("sent", "overdue", "void", "paid", "viewed", "unpaid", ""):
            with self.subTest(status=status):
                plan_path = self.stage()

                def drift(record, status=status):
                    invoice = self.apply_create(record["payload"])
                    invoice["status"] = status

                self.allow_writes(on_write=drift)
                with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
                    self.commit(plan_path)
                message = str(caught.exception)
                self.assertIn("is NOT the Draft that was approved", message)
                self.assertIn(NEW_INVOICE_ID, message)
                self.assertIn("nothing will be retried", message)
                lock = json.loads(
                    (self.plan_dir / ".commit-locks" / self.lock_files()[0]).read_text()
                )
                self.assertEqual(lock["status"], "indeterminate")
                self.assertEqual(lock["invoice_id"], NEW_INVOICE_ID)
                plan_path.unlink()
                (self.plan_dir / ".commit-locks" / self.lock_files()[0]).unlink()
                self.write_calls.clear()
                self.invoices.clear()

    def test_missing_readback_is_indeterminate_and_never_cleaned_up(self) -> None:
        plan_path = self.stage()
        self.allow_writes(on_write=lambda record: None)  # POST accepted, nothing stored
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn("POST was ISSUED", str(caught.exception))
        self.assertIn(NEW_INVOICE_ID, str(caught.exception))
        self.assertEqual(len(self.write_calls), 1)

    def test_readback_rejects_a_mailed_invoice(self) -> None:
        plan_path = self.stage()

        def drift(record):
            self.apply_create(record["payload"])["is_emailed"] = True

        self.allow_writes(on_write=drift)
        with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
            self.commit(plan_path)
        self.assertIn("already been mailed", str(caught.exception))

    def test_readback_rejects_wrong_header_customer_or_currency(self) -> None:
        cases = (
            ({"customer_id": OTHER_CUSTOMER_ID}, "customer_id"),
            ({"customer_name": "Someone Else"}, "customer_name"),
            ({"currency_code": "USD"}, "currency"),
            ({"currency_id": "96274000000000098"}, "currency_id"),
            ({"date": "2026-01-01"}, "date"),
            ({"due_date": "2027-01-01"}, "due_date"),
        )
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                plan_path = self.stage()

                def drift(record, overrides=overrides):
                    self.apply_create(record["payload"]).update(overrides)

                self.allow_writes(on_write=drift)
                with self.assertRaises(invoice_tool.InvoiceRevisionError) as caught:
                    self.commit(plan_path)
                self.assertIn(expected, str(caught.exception))
                plan_path.unlink()
                (self.plan_dir / ".commit-locks" / self.lock_files()[0]).unlink()
                self.write_calls.clear()
                self.invoices.clear()

    def test_readback_rejects_line_loss_reorder_swap_or_silent_edit(self) -> None:
        cases = {
            "dropped": lambda invoice: invoice["line_items"].pop(),
            "reordered": lambda invoice: invoice["line_items"].reverse(),
            "item swapped": lambda invoice: invoice["line_items"][0].__setitem__(
                "item_id", ITEM_TWO),
            "added": lambda invoice: invoice["line_items"].append(
                copy.deepcopy(invoice["line_items"][0])),
            "quantity edited": lambda invoice: invoice["line_items"][0].__setitem__(
                "quantity", 9.0),
            "rate edited": lambda invoice: invoice["line_items"][1].__setitem__("rate", 9.0),
            "discount removed": lambda invoice: invoice["line_items"][1].__setitem__(
                "discount", 0),
            "description edited": lambda invoice: invoice["line_items"][0].__setitem__(
                "description", "something else"),
            "tax removed": lambda invoice: invoice["line_items"][0].__setitem__("tax_id", ""),
            "tax rate wrong": lambda invoice: invoice["line_items"][0].__setitem__(
                "tax_percentage", 13.0),
            "line total wrong": lambda invoice: invoice["line_items"][0].__setitem__(
                "item_total", 1.0),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                plan_path = self.stage(self.two_line_request())

                def drift(record, mutate=mutate):
                    mutate(self.apply_create(record["payload"]))

                self.allow_writes(on_write=drift)
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    self.commit(plan_path)
                plan_path.unlink()
                (self.plan_dir / ".commit-locks" / self.lock_files()[0]).unlink()
                self.write_calls.clear()
                self.invoices.clear()

    def test_readback_rejects_wrong_totals_or_injected_charges(self) -> None:
        cases = (
            {"sub_total": 99.0},
            {"total": 99.0, "balance": 99.0},
            {"tax_total": 99.0},
            {"discount_total": 99.0},
            {"shipping_charge": 25.0},
            {"adjustment": -5.0},
            {"is_inclusive_tax": True},
            {"balance": 1.0},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                plan_path = self.stage(self.two_line_request())

                def drift(record, overrides=overrides):
                    self.apply_create(record["payload"]).update(overrides)

                self.allow_writes(on_write=drift)
                with self.assertRaises(invoice_tool.InvoiceRevisionError):
                    self.commit(plan_path)
                plan_path.unlink()
                (self.plan_dir / ".commit-locks" / self.lock_files()[0]).unlink()
                self.write_calls.clear()
                self.invoices.clear()

    def test_duplicate_item_lines_survive_a_full_round_trip(self) -> None:
        first = {
            "item_id": ITEM_ONE, "item_name": ITEM_ONE_NAME,
            "quantity": value("2"), "rate": value("500.00"),
            "description": value("run A, north bay"),
        }
        second = {
            "item_id": ITEM_ONE, "item_name": ITEM_ONE_NAME,
            "quantity": value("3"), "rate": value("450.00"),
            "description": value("run B, south bay"),
        }
        plan_path = self.stage(self.request(lines=[first, second]))
        self.allow_writes()
        output = json.loads(self.commit(plan_path))
        self.assertEqual(output["lines_created"], 2)
        created = self.invoices[NEW_INVOICE_ID]["line_items"]
        self.assertEqual([line["quantity"] for line in created], [2.0, 3.0])
        self.assertEqual(
            [line["description"] for line in created],
            ["run A, north bay", "run B, south bay"],
        )
        self.assertEqual(output["totals_verified"]["total"], "2350.00")


# ---------------------------------------------------------------------------
# No send capability, anywhere, even with the CREATE scope
# ---------------------------------------------------------------------------


class CreateNoSendSurfaceTests(unittest.TestCase):
    SOURCE = Path(invoice_tool.__file__).read_text(encoding="utf-8")

    def test_module_has_no_mail_transport_or_import(self) -> None:
        for marker in ("smtplib", "sendmail", "send_mail", "EmailMessage", "outlook",
                       "graph.microsoft.com", "Mail.Send", "import email"):
            self.assertNotIn(marker, self.SOURCE, f"{marker} must not appear in this tool")

    def test_no_mark_draft_or_mark_sent_endpoint_exists_despite_create_scope(self) -> None:
        for marker in ("/status/", "/email", "/remind", "/submit", "/approve", "/reject",
                       "/void", "/attachment", "/templates", "send=true", "?send",
                       "fromsalesorder", "ignore_auto_number_generation"):
            self.assertNotIn(marker, self.SOURCE, f"route {marker} must not appear in this tool")

    def test_no_generic_write_helper_is_exported(self) -> None:
        for name in ("api_post", "api_put", "api_delete", "write", "send", "email_invoice",
                     "mark_sent", "mark_draft", "void_invoice", "create_invoice",
                     "delete_invoice", "convert_estimate"):
            self.assertFalse(hasattr(invoice_tool, name), f"{name} must not exist")

    def test_the_two_actions_are_the_only_actions(self) -> None:
        self.assertEqual(invoice_tool.ACTIONS, ("invoice_revision", "create_draft_invoice"))
        self.assertEqual(invoice_tool.DRAFT_STATUS, "draft")

    def test_post_allowlist_contains_no_number_currency_lifecycle_or_mail_field(self) -> None:
        for field in ("invoice_number", "ignore_auto_number_generation", "status", "send",
                      "email", "to_mail_ids", "exchange_rate", "currency_id", "currency_code",
                      "adjustment", "shipping_charge", "balance", "payment_made",
                      "custom_fields", "template_id", "salesorder_id"):
            self.assertNotIn(field, invoice_tool.ALLOWED_POST_KEYS)
        for field in ("name", "item_total", "line_item_id", "account_id", "warehouse_id"):
            self.assertNotIn(field, invoice_tool.LINE_POST_KEYS)

    def test_commissioned_creation_surface_is_exactly_the_brief(self) -> None:
        self.assertEqual(set(invoice_tool.CREATE_HEADER_FIELDS), {
            "date", "due_date", "reference_number", "notes", "terms",
            "billing_address_id", "shipping_address_id",
        })
        self.assertEqual(invoice_tool.CREATE_LINE_FIELDS, {
            "item_id", "item_name", "quantity", "rate", "discount", "description", "tax_id",
        })
        self.assertEqual(invoice_tool.ALLOWED_POST_KEYS, {
            "customer_id", "date", "due_date", "reference_number", "notes", "terms",
            "billing_address_id", "shipping_address_id", "line_items",
        })

    def test_the_cli_offers_only_the_commissioned_stage_and_commit_pairs(self) -> None:
        parser = invoice_tool.build_parser()
        actions = [
            action for action in parser._subparsers._group_actions  # noqa: SLF001
        ]
        # The two generic actions, plus the fixed INV-000051 SHM correction
        # commissioned 2026-08-12. There is still no send, void, delete,
        # status or mail command of any kind.
        self.assertEqual(
            sorted(actions[0].choices),
            [
                "commit",
                "commit-inv000051-shm-correction",
                "stage",
                "stage-create",
                "stage-inv000051-shm-correction",
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
