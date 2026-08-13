"""Safety tests for the SHM customer prerequisite and the INV-000051 correction.

Commissioned by Rachad on 2026-08-12 after Elaine Iverson asked for INV-000051
to be billed to SHM Marine Constructors JV against client PO 0000031, and after
he ruled the sale is a Brockville customer collection carrying Ontario HST 13%.

NO TEST IN THIS FILE PERFORMS A LIVE CALL. Every read is a fake api_get and
every write is a fake urlopen; the real transports are asserted never to run.
"""

from __future__ import annotations

import argparse
import copy
import inspect
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import zoho_customer_quote_tool as draft
import zoho_invoice_revision_tool as revision
import zoho_tool

HERE = Path(__file__).resolve().parent

ORG_ID = "110002157575"
ORGANIZATIONS = [
    {
        "organization_id": ORG_ID,
        "name": "FRP DEPOTS",
        "currency_code": "CAD",
    }
]
ORGANIZATION = {"organization_id": ORG_ID, "name": "FRP DEPOTS", "currency_code": "CAD"}

INVOICE_ID = revision.SHM_INVOICE_ID
INVOICE_NUMBER = revision.SHM_INVOICE_NUMBER
OLD_CUSTOMER_ID = revision.SHM_OLD_CUSTOMER_ID
SO_ID = revision.SHM_SALESORDER_ID
GST_ID = revision.SHM_OLD_TAX_ID
HST_ID = revision.SHM_NEW_TAX_ID
SHM_ID = "96274000001600001"
SHM_BILLING_ADDRESS_ID = "96274000001600003"
SHM_SHIPPING_ADDRESS_ID = "96274000001600004"
SHM_PERSON_ID = "96274000001600002"
LINE_ONE = revision.SHM_LINES[0]["line_item_id"]
LINE_TWO = revision.SHM_LINES[1]["line_item_id"]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def fake_vault(scopes=None) -> dict:
    return {
        "api_domain": "https://www.zohoapis.ca",
        "books_organization_id": ORG_ID,
        "scopes": list(
            scopes
            if scopes is not None
            else list(zoho_tool.READ_SCOPES) + list(zoho_tool.ALLOWED_WRITE_SCOPES)
        ),
    }


# ---------------------------------------------------------------------------
# Live-shaped fakes. Field names and shapes follow the 2026-08-12 read-only
# probe of the real records, including the live-stock line mirrors and the
# header-level tax mirror that Zoho recomputes.
# ---------------------------------------------------------------------------


def shm_billing_address(**overrides) -> dict:
    address = {
        "address_id": SHM_BILLING_ADDRESS_ID,
        "attention": "",
        "address": "343A Bay St",
        "street2": "",
        "city": "Victoria",
        "state": "BC",
        "state_code": "BC",
        "zip": "V8T1P5",
        "country": "Canada",
        "country_code": "CA",
        "county": "",
        "phone": "250-590-7072",
        "fax": "",
    }
    address.update(overrides)
    return address


def blank_address(address_id: str = "") -> dict:
    address = {
        "attention": "",
        "address": "",
        "street": "",
        "street2": "",
        "city": "",
        "state": "",
        "zip": "",
        "country": "",
        "country_code": "",
        "phone": "",
        "fax": "",
    }
    if address_id:
        address["address_id"] = address_id
    return address


def shm_contact(**overrides) -> dict:
    contact = {
        "contact_id": SHM_ID,
        "contact_name": revision.SHM_CUSTOMER_NAME,
        "company_name": revision.SHM_CUSTOMER_NAME,
        "contact_type": "customer",
        "customer_sub_type": "business",
        "status": "active",
        "currency_code": "CAD",
        "currency_id": "96274000000000097",
        "billing_address": shm_billing_address(),
        "shipping_address": blank_address(SHM_SHIPPING_ADDRESS_ID),
        "contact_persons": [
            {
                "contact_person_id": SHM_PERSON_ID,
                "first_name": "Elaine",
                "last_name": "Iverson",
                "email": "elaineiverson@ralmax.com",
                "is_primary_contact": True,
            }
        ],
    }
    contact.update(overrides)
    return contact


def shm_contact_row() -> dict:
    return {
        "contact_id": SHM_ID,
        "contact_name": revision.SHM_CUSTOMER_NAME,
        "company_name": revision.SHM_CUSTOMER_NAME,
        "contact_type": "customer",
        "status": "active",
    }


def other_contact_rows() -> list[dict]:
    return [
        {
            "contact_id": OLD_CUSTOMER_ID,
            "contact_name": "Ralmax Group of Companies",
            "company_name": "Ralmax Group of Companies",
            "contact_type": "customer",
            "status": "active",
        },
        {
            "contact_id": "96274000000060019",
            "contact_name": "Troy Dualam Services Inc.",
            "company_name": "Troy Dualam Services Inc.",
            "contact_type": "customer",
            "status": "active",
        },
    ]


def live_taxes() -> list[dict]:
    return [
        {"tax_id": GST_ID, "tax_name": "GST", "tax_percentage": 5, "status": "Active"},
        {"tax_id": HST_ID, "tax_name": "ON HST", "tax_percentage": 13, "status": "Active"},
        {
            "tax_id": "96274000000035514",
            "tax_name": "HST",
            "tax_percentage": 15,
            "status": "Active",
        },
    ]


def live_line(index: int, *, tax_id: str = GST_ID, tax_percentage: float = 5.0, **overrides) -> dict:
    fixed = revision.SHM_LINES[index]
    line = {
        "line_item_id": fixed["line_item_id"],
        "item_id": fixed["item_id"],
        "salesorder_item_id": fixed["salesorder_item_id"],
        "name": fixed["name"],
        "description": fixed["description"],
        "item_order": index + 1,
        "unit": "pcs",
        "quantity": float(fixed["quantity"]),
        "rate": float(fixed["rate"]),
        "discount": 0.0,
        "tax_id": tax_id,
        "tax_name": "GST" if tax_id == GST_ID else "ON HST",
        "tax_percentage": tax_percentage,
        "account_id": "96274000000000388",
        "product_type": "goods",
        "item_total": float(fixed["quantity"] * fixed["rate"]),
        # Live item stock, mirrored by Zoho onto the line. Excluded from the
        # pre-write drift projection because it moves on its own.
        "available_stock": 41.0,
        "available_for_sale_stock": 29.0,
        "committed_stock": 12.0,
        "stock_on_hand": 41.0,
    }
    line.update(overrides)
    return line


def ralmax_billing_address() -> dict:
    return {
        "attention": "Josh Caulfield",
        "address": "343A Bay Street",
        "street": "343A Bay Street",
        "street2": "",
        "city": "Victoria",
        "state": "BC",
        "zip": "V8T 1P5",
        "country": "Canada",
        "country_code": "CA",
        "phone": "672-974-4420",
        "fax": "",
    }


def live_invoice(**overrides) -> dict:
    invoice = {
        "invoice_id": INVOICE_ID,
        "invoice_number": INVOICE_NUMBER,
        "status": "overdue",
        "customer_id": OLD_CUSTOMER_ID,
        "customer_name": "Ralmax Group of Companies",
        "reference_number": "SO-00050",
        "date": "2026-08-10",
        "due_date": "2026-08-10",
        "currency_code": "CAD",
        "currency_id": "96274000000000097",
        "exchange_rate": 1.0,
        "salesorder_id": SO_ID,
        "salesorder_number": "SO-00050",
        "salesorders": [
            {
                "salesorder_id": SO_ID,
                "salesorder_number": "SO-00050",
                "total": 13671.0,
                "sub_total": 13020.0,
            }
        ],
        "is_emailed": True,
        "reminders_sent": 0,
        "payment_made": 0.0,
        "credits_applied": 0.0,
        "write_off_amount": 0.0,
        "payments": [],
        "credits": [],
        "packages": [],
        "shipments": [],
        "recurring_invoice_id": "",
        "billing_address": ralmax_billing_address(),
        "shipping_address": blank_address(),
        "billing_address_id": None,
        "shipping_address_id": None,
        "contact_persons": [],
        "contact_persons_details": [
            {
                "contact_person_id": "96274000001525002",
                "first_name": "Josh",
                "last_name": "Caulfield",
                "email": "JoshC@ralmax.com",
                "is_primary_contact": True,
            }
        ],
        "notes": "Thank you for the payment.",
        "terms": "",
        "template_id": "96274000000000537",
        "template_name": "Standard Template",
        "custom_fields": [],
        "tax_id": GST_ID,
        "tax_name": "GST",
        "tax_percentage": 5.0,
        "tax_override_preference": "no_override",
        "total_taxable_amount": 13020.0,
        "sub_total": 13020.0,
        "sub_total_exclusive_of_discount": 13020.0,
        "sub_total_inclusive_of_tax": 0.0,
        "tax_total": 651.0,
        "total": 13671.0,
        "balance": 13671.0,
        "discount_total": 0.0,
        "discount_amount": 0.0,
        "roundoff_value": 0.0,
        "shipping_charge": 0.0,
        "adjustment": 0.0,
        "bcy_sub_total": 13020.0,
        "bcy_discount_total": 0.0,
        "bcy_tax_total": 651.0,
        "bcy_total": 13671.0,
        "taxes": [{"tax_name": "GST", "tax_amount": 651.0}],
        "due_days": None,
        "is_overdue": None,
        "days_to_due": None,
        "next_reminder_date_formatted": "15/08/2026",
        "last_modified_time": "2026-08-10T09:00:00-0400",
        "last_modified_by_id": "96274000000060001",
        "invoice_url": "https://books.zohocloud.ca/invoice/one",
        "line_items": [live_line(0), live_line(1)],
    }
    invoice.update(overrides)
    return invoice


def corrected_invoice(before: dict | None = None, **overrides) -> dict:
    """The expected post-write record: ONLY exempt keys and lines differ."""
    after = copy.deepcopy(before or live_invoice())
    after.update(
        {
            "customer_id": SHM_ID,
            "customer_name": revision.SHM_CUSTOMER_NAME,
            "reference_number": revision.SHM_NEW_REFERENCE,
            "billing_address": {
                key: value
                for key, value in shm_billing_address().items()
                if key != "address_id"
            },
            "contact_persons_details": [
                {
                    "contact_person_id": SHM_PERSON_ID,
                    "first_name": "Elaine",
                    "last_name": "Iverson",
                    "email": "elaineiverson@ralmax.com",
                    "is_primary_contact": True,
                }
            ],
            "tax_id": HST_ID,
            "tax_name": "ON HST",
            "tax_percentage": 13.0,
            "sub_total": 13020.0,
            "tax_total": 1692.60,
            "total": 14712.60,
            "balance": 14712.60,
            "bcy_tax_total": 1692.60,
            "bcy_total": 14712.60,
            "taxes": [{"tax_name": "ON HST", "tax_amount": 1692.60}],
            "last_modified_time": "2026-08-12T18:00:00-0400",
            "line_items": [
                live_line(0, tax_id=HST_ID, tax_percentage=13.0),
                live_line(1, tax_id=HST_ID, tax_percentage=13.0),
            ],
        }
    )
    after.update(overrides)
    return after


def live_salesorder(**overrides) -> dict:
    order = {
        "salesorder_id": SO_ID,
        "salesorder_number": "SO-00050",
        "customer_id": OLD_CUSTOMER_ID,
        "customer_name": "Ralmax Group of Companies",
        "reference_number": "QT-000028",
        "status": "invoiced",
        "invoiced_status": "invoiced",
        "order_status": "closed",
        "shipped_status": "",
        "date": "2026-08-10",
        "currency_code": "CAD",
        "sub_total": 13020.0,
        "tax_total": 651.0,
        "total": 13671.0,
        "custom_fields": [],
        "notes": "",
        "terms": "",
        "line_items": [
            {
                "line_item_id": revision.SHM_LINES[0]["salesorder_item_id"],
                "item_id": revision.SHM_LINES[0]["item_id"],
                "quantity": 24.0,
                "rate": 97.0,
                "tax_id": GST_ID,
                "tax_name": "GST",
            },
            {
                "line_item_id": revision.SHM_LINES[1]["salesorder_item_id"],
                "item_id": revision.SHM_LINES[1]["item_id"],
                "quantity": 36.0,
                "rate": 297.0,
                "tax_id": GST_ID,
                "tax_name": "GST",
            },
        ],
        "invoices": [
            {
                "invoice_id": INVOICE_ID,
                "invoice_number": INVOICE_NUMBER,
                "reference_number": "SO-00050",
                "status": "overdue",
                "date": "2026-08-10",
                "due_date": "2026-08-10",
                "total": 13671.0,
                "balance": 13671.0,
            }
        ],
        "last_modified_time": "2026-08-10T09:00:00-0400",
        "salesorder_url": "https://books.zohocloud.ca/so/one",
    }
    order.update(overrides)
    return order


def corrected_salesorder(before: dict | None = None, **overrides) -> dict:
    """Only the order's read-only mirror of the invoice moves."""
    order = copy.deepcopy(before or live_salesorder())
    order["invoices"] = [
        dict(
            order["invoices"][0],
            reference_number=revision.SHM_NEW_REFERENCE,
            total=14712.60,
            balance=14712.60,
        )
    ]
    order["last_modified_time"] = "2026-08-12T18:00:00-0400"
    order.update(overrides)
    return order


def contact_pages(rows: list[dict], per_page: int) -> list[dict]:
    pages: list[dict] = []
    chunks = [rows[index : index + per_page] for index in range(0, len(rows), per_page)] or [[]]
    for number, chunk in enumerate(chunks, start=1):
        pages.append(
            {
                "code": 0,
                "contacts": chunk,
                "page_context": {
                    "page": number,
                    "per_page": per_page,
                    "has_more_page": number < len(chunks),
                },
            }
        )
    return pages


# ---------------------------------------------------------------------------
# PLAN A -- the fixed SHM customer
# ---------------------------------------------------------------------------


class ShmCustomerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.plan_dir = Path(self._temp.name).resolve() / "zoho_plans"
        self.plan_dir.mkdir(parents=True)
        self.addCleanup(self._temp.cleanup)

    def routed_api_get(self, *, contacts: list[dict] | None = None, contact: dict | None = None):
        rows = other_contact_rows() if contacts is None else contacts
        pages = contact_pages(rows, draft.SHM_CONTACT_PER_PAGE)
        self.api_calls: list[str] = []

        def api_get(access_token, api_domain, path):
            self.api_calls.append(path)
            if path.startswith("/books/v3/organizations"):
                return {"code": 0, "organizations": copy.deepcopy(ORGANIZATIONS)}
            if re.match(r"^/books/v3/contacts/\d+\?", path):
                if contact is None:
                    raise AssertionError("unexpected contact read")
                return {"code": 0, "contact": copy.deepcopy(contact)}
            if path.startswith("/books/v3/contacts?"):
                number = int(re.search(r"page=(\d+)", path).group(1))
                if number > len(pages):
                    raise AssertionError(f"unexpected contact page {number}")
                return copy.deepcopy(pages[number - 1])
            raise AssertionError(f"unexpected GET {path}")

        return api_get

    def stage(self, *, contacts: list[dict] | None = None) -> Path:
        vault = fake_vault()
        existing = set(self.plan_dir.glob("*.json"))
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool, "api_get", side_effect=self.routed_api_get(contacts=contacts)
        ), patch.object(
            draft, "urlopen", side_effect=AssertionError("staging must never write")
        ):
            draft.command_stage_shm_inv000051_customer(argparse.Namespace())
        created = set(self.plan_dir.glob("*.json")) - existing
        self.assertEqual(len(created), 1)
        return created.pop()

    def plan_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def rehash(self, path: Path, plan: dict) -> Path:
        core = dict(plan)
        core.pop("sha256", None)
        plan["sha256"] = draft.digest_for(core)
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def commit(
        self,
        plan_path: Path,
        *,
        approval: str = draft.APPROVAL_WORD,
        contacts: list[dict] | None = None,
        created: dict | None = None,
        readback: dict | None = None,
        write=None,
        scopes=None,
    ):
        vault = fake_vault(scopes)
        created = shm_contact() if created is None else created
        readback = created if readback is None else readback
        self.writes: list[tuple] = []

        def fake_urlopen(request, timeout=None):
            self.writes.append(
                (
                    request.get_method(),
                    request.full_url,
                    json.loads(request.data.decode("utf-8")),
                )
            )
            if write is not None:
                raise write
            return FakeResponse({"code": 0, "contact": copy.deepcopy(created)})

        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool,
            "api_get",
            side_effect=self.routed_api_get(contacts=contacts, contact=readback),
        ), patch.object(draft, "urlopen", side_effect=fake_urlopen):
            draft.command_commit_shm_inv000051_customer(
                argparse.Namespace(plan=str(plan_path), approval=approval)
            )


class ShmCustomerFixedRecordTests(ShmCustomerTestCase):
    def test_payload_is_exactly_the_commissioned_record(self):
        self.assertEqual(
            draft.SHM_CUSTOMER_PAYLOAD,
            {
                "contact_name": "SHM Marine Constructors JV",
                "company_name": "SHM Marine Constructors JV",
                "contact_type": "customer",
                "customer_sub_type": "business",
                "billing_address": {
                    "address": "343A Bay St",
                    "city": "Victoria",
                    "state": "BC",
                    "zip": "V8T1P5",
                    "country": "Canada",
                    "phone": "250-590-7072",
                },
                "contact_persons": [
                    {
                        "first_name": "Elaine",
                        "last_name": "Iverson",
                        "email": "elaineiverson@ralmax.com",
                        "is_primary_contact": True,
                    }
                ],
            },
        )

    def test_no_shipping_address_or_speculative_field_is_invented(self):
        for absent in (
            "shipping_address",
            "website",
            "tax_id",
            "payment_terms",
            "currency_id",
            "notes",
            "language_code",
        ):
            self.assertNotIn(absent, draft.SHM_CUSTOMER_PAYLOAD)

    def test_stage_command_takes_no_business_parameter(self):
        parser = draft.build_parser()
        action = parser._actions[1].choices["stage-shm-inv000051-customer"]
        supplied = [
            option
            for option in action._actions
            if option.dest not in ("help", "func")
        ]
        self.assertEqual(supplied, [])

    def test_commit_command_takes_only_plan_and_approval(self):
        parser = draft.build_parser()
        action = parser._actions[1].choices["commit-shm-inv000051-customer"]
        dests = sorted(
            option.dest for option in action._actions if option.dest not in ("help", "func")
        )
        self.assertEqual(dests, ["approval", "plan"])


class ShmCustomerSourceTests(ShmCustomerTestCase):
    def test_the_three_fixed_sources_are_verified(self):
        evidence = draft.shm_source_evidence()
        self.assertEqual(
            sorted(evidence), ["client_po_pdf", "client_po_text", "live_preflight"]
        )
        self.assertEqual(evidence["client_po_pdf"]["bytes"], 112548)
        self.assertEqual(
            evidence["client_po_pdf"]["sha256"],
            "623c47693f0552fa267d7a5ace7650772447f2787822c4e0d3119019b9d2e08c",
        )
        self.assertEqual(evidence["client_po_text"]["bytes"], 1857)
        self.assertEqual(evidence["live_preflight"]["bytes"], 2538)

    def test_a_wrong_sized_source_refuses(self):
        original = draft.SHM_SOURCE_FILES
        patched = list(copy.deepcopy(list(original)))
        patched[0] = dict(patched[0], bytes=1)
        with patch.object(draft, "SHM_SOURCE_FILES", tuple(patched)):
            with self.assertRaises(draft.DraftToolError) as caught:
                draft.shm_source_evidence()
        self.assertIn("not the fixed", str(caught.exception))

    def test_a_wrong_digest_source_refuses(self):
        patched = list(copy.deepcopy(list(draft.SHM_SOURCE_FILES)))
        patched[1] = dict(patched[1], sha256="0" * 64)
        with patch.object(draft, "SHM_SOURCE_FILES", tuple(patched)):
            with self.assertRaises(draft.DraftToolError) as caught:
                draft.shm_source_evidence()
        self.assertIn("SHA-256", str(caught.exception))


class ShmCustomerDuplicateTests(ShmCustomerTestCase):
    def test_duplicate_walk_is_complete_and_paginated(self):
        rows = [
            {
                "contact_id": str(96274000001700000 + index),
                "contact_name": f"Customer {index}",
                "company_name": "",
                "status": "active",
                "contact_type": "customer",
            }
            for index in range(450)
        ]
        with patch.object(draft, "SHM_CONTACT_PER_PAGE", 200):
            path = self.stage(contacts=rows)
        scan = self.plan_json(path)["live_evidence"]["duplicate_scan"]
        self.assertEqual(scan["enumerated"], 450)
        self.assertEqual(scan["pages"], 3)
        self.assertTrue(scan["complete"])
        self.assertFalse(scan["filtered"])

    def test_an_exact_duplicate_on_the_last_page_refuses(self):
        rows = [
            {
                "contact_id": str(96274000001700000 + index),
                "contact_name": f"Customer {index}",
                "company_name": "",
                "status": "active",
                "contact_type": "customer",
            }
            for index in range(399)
        ] + [shm_contact_row()]
        with self.assertRaises(draft.DraftToolError) as caught:
            self.stage(contacts=rows)
        self.assertIn("already carries the exact name", str(caught.exception))

    def test_a_case_and_whitespace_variant_is_an_exact_duplicate(self):
        rows = other_contact_rows() + [
            dict(shm_contact_row(), contact_name="  shm   marine constructors jv ")
        ]
        with self.assertRaises(draft.DraftToolError):
            self.stage(contacts=rows)

    def test_a_company_name_duplicate_refuses(self):
        rows = other_contact_rows() + [
            dict(
                shm_contact_row(),
                contact_name="SHM Marine Constructors JV Ltd",
                company_name=revision.SHM_CUSTOMER_NAME,
            )
        ]
        with self.assertRaises(draft.DraftToolError):
            self.stage(contacts=rows)

    def test_a_near_match_is_disclosed_and_never_substituted(self):
        rows = other_contact_rows() + [
            {
                "contact_id": "96274000001700999",
                "contact_name": "SHM Marine Constructors JV Ltd",
                "company_name": "",
                "status": "active",
                "contact_type": "customer",
            }
        ]
        path = self.stage(contacts=rows)
        scan = self.plan_json(path)["live_evidence"]["duplicate_scan"]
        self.assertEqual(scan["exact_match_count"], 0)
        self.assertEqual(scan["near_match_count"], 1)
        self.assertEqual(scan["near_matches"][0]["contact_id"], "96274000001700999")
        # A near match does not become the record: the payload is untouched.
        self.assertEqual(
            self.plan_json(path)["live_evidence"]["post_payload"], draft.SHM_CUSTOMER_PAYLOAD
        )

    def test_a_missing_page_context_refuses(self):
        def api_get(access_token, api_domain, path):
            if path.startswith("/books/v3/organizations"):
                return {"code": 0, "organizations": copy.deepcopy(ORGANIZATIONS)}
            return {"code": 0, "contacts": []}

        vault = fake_vault()
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool, "api_get", side_effect=api_get
        ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
            with self.assertRaises(draft.DraftToolError) as caught:
                draft.command_stage_shm_inv000051_customer(argparse.Namespace())
        self.assertIn("page context", str(caught.exception))

    def test_a_non_boolean_has_more_page_refuses(self):
        def api_get(access_token, api_domain, path):
            if path.startswith("/books/v3/organizations"):
                return {"code": 0, "organizations": copy.deepcopy(ORGANIZATIONS)}
            return {
                "code": 0,
                "contacts": [],
                "page_context": {"page": 1, "has_more_page": "false"},
            }

        vault = fake_vault()
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool, "api_get", side_effect=api_get
        ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
            with self.assertRaises(draft.DraftToolError) as caught:
                draft.command_stage_shm_inv000051_customer(argparse.Namespace())
        self.assertIn("has_more_page", str(caught.exception))

    def test_the_page_ceiling_is_a_refusal_not_a_partial_scan(self):
        with patch.object(draft, "SHM_CONTACT_MAX_PAGES", 0):
            with self.assertRaises(draft.DraftToolError) as caught:
                draft.enumerate_all_contacts("token", fake_vault())
        self.assertIn("page ceiling", str(caught.exception))

    def test_the_row_ceiling_is_a_refusal_not_a_partial_scan(self):
        rows = [
            {"contact_id": str(96274000001700000 + index), "contact_name": f"C{index}"}
            for index in range(10)
        ]
        pages = contact_pages(rows, 200)
        with patch.object(draft, "SHM_CONTACT_MAX_ROWS", 3), patch.object(
            draft.zoho_tool, "api_get", side_effect=lambda *a: copy.deepcopy(pages[0])
        ):
            with self.assertRaises(draft.DraftToolError) as caught:
                draft.enumerate_all_contacts("token", fake_vault())
        self.assertIn("row ceiling", str(caught.exception))


class ShmCustomerPlanTests(ShmCustomerTestCase):
    def test_a_clean_stage_produces_a_reviewable_plan(self):
        plan = self.plan_json(self.stage())
        self.assertEqual(plan["kind"], draft.SHM_KIND)
        self.assertEqual(plan["approval_required"], "APPROVED")
        self.assertRegex(plan["nonce"], r"^[0-9a-f]{32}$")
        self.assertRegex(plan["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(plan["live_evidence"]["post_endpoint"], "POST /books/v3/contacts")
        self.assertIs(plan["live_evidence"]["email_sent"], False)
        self.assertIs(plan["risk"]["atomic_with_invoice_correction"], False)
        self.assertIn("REMAINS", plan["risk"]["note"])

    def test_the_plan_discloses_non_atomicity_with_the_invoice_correction(self):
        plan = self.plan_json(self.stage())
        self.assertIn("NOT ATOMIC", plan["risk"]["note"])
        self.assertIn("deletion", plan["risk"]["note"].casefold())
        self.assertIn("remains", plan["risk"]["note"].casefold())

    def test_a_tampered_payload_is_refused(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["post_payload"]["contact_name"] = "Someone Else"
        self.rehash(path, plan)
        with self.assertRaises(draft.DraftToolError):
            draft.load_shm_plan(path)

    def test_a_tampered_endpoint_is_refused(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["post_endpoint"] = "POST /books/v3/invoices"
        self.rehash(path, plan)
        with self.assertRaises(draft.DraftToolError):
            draft.load_shm_plan(path)

    def test_a_rehashed_plan_still_fails_the_canonical_projection(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["zoho_defaults_policy"] = "anything goes"
        self.rehash(path, plan)
        with self.assertRaises(draft.DraftToolError) as caught:
            draft.load_shm_plan(path)
        self.assertIn("canonical projection", str(caught.exception))

    def test_a_tampered_scan_cannot_smuggle_a_duplicate_past_commit(self):
        # The staged scan is evidence only: commit walks the contact list again,
        # so editing the recorded counts changes nothing about safety.
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["duplicate_scan"]["enumerated"] = 99999
        self.rehash(path, plan)
        with self.assertRaises(draft.DraftToolError) as caught:
            self.commit(path, contacts=other_contact_rows() + [shm_contact_row()])
        self.assertEqual(self.writes, [])
        self.assertIn("already carries the exact name", str(caught.exception))

    def test_a_plan_whose_hash_was_not_updated_is_refused(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["post_payload"]["company_name"] = "Other"
        path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        with self.assertRaises(draft.DraftToolError) as caught:
            draft.load_shm_plan(path)
        self.assertIn("hash check failed", str(caught.exception))

    def test_a_wrong_tool_or_action_plan_is_refused(self):
        path = self.stage()
        for field, value in (("tool", "Other Tool"), ("kind", "customer"), ("schema_version", 2)):
            plan = self.plan_json(path)
            plan[field] = value
            self.rehash(path, plan)
            with self.assertRaises(draft.DraftToolError):
                draft.load_shm_plan(path)

    def test_an_expired_plan_is_refused(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["created_utc"] = "2026-08-01T00:00:00+00:00"
        plan["expires_utc"] = "2026-08-02T00:00:00+00:00"
        self.rehash(path, plan)
        with self.assertRaises(draft.DraftToolError) as caught:
            draft.load_shm_plan(path)
        self.assertIn("expired", str(caught.exception))

    def test_a_stretched_lifetime_is_refused(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["expires_utc"] = "2026-09-30T00:00:00+00:00"
        self.rehash(path, plan)
        with self.assertRaises(draft.DraftToolError) as caught:
            draft.load_shm_plan(path)
        self.assertIn("24-hour", str(caught.exception))

    def test_a_downgraded_risk_disclosure_is_refused(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["risk"]["atomic_with_invoice_correction"] = True
        self.rehash(path, plan)
        with self.assertRaises(draft.DraftToolError):
            draft.load_shm_plan(path)


class ShmCustomerApprovalTests(ShmCustomerTestCase):
    def test_a_correct_approval_creates_exactly_one_customer(self):
        path = self.stage()
        self.commit(path)
        self.assertEqual(len(self.writes), 1)
        method, url, body = self.writes[0]
        self.assertEqual(method, "POST")
        self.assertIn("/books/v3/contacts?", url)
        self.assertEqual(body, draft.SHM_CUSTOMER_PAYLOAD)

    def test_a_wrong_word_never_reaches_the_network(self):
        path = self.stage()
        for word in ("approve", "yes", "APPROVE", "OK"):
            with self.assertRaises(draft.DraftToolError):
                self.commit(path, approval=word)
            self.assertEqual(self.writes, [])

    def test_a_padded_or_lowercase_approval_is_refused(self):
        path = self.stage()
        for word in (" APPROVED", "APPROVED ", "approved", "Approved", "APPROVED\n"):
            with self.assertRaises(draft.DraftToolError):
                self.commit(path, approval=word)
            self.assertEqual(self.writes, [])

    def test_a_missing_create_scope_refuses_before_the_write(self):
        path = self.stage()
        scopes = [
            scope
            for scope in list(zoho_tool.READ_SCOPES) + list(zoho_tool.ALLOWED_WRITE_SCOPES)
            if scope != "ZohoBooks.contacts.CREATE"
        ]
        with self.assertRaises(draft.DraftToolError) as caught:
            self.commit(path, scopes=scopes)
        self.assertEqual(self.writes, [])
        self.assertIn("BEFORE any write", str(caught.exception))


class ShmCustomerCommitTests(ShmCustomerTestCase):
    def test_a_duplicate_appearing_after_approval_refuses_before_the_lock(self):
        path = self.stage()
        with self.assertRaises(draft.DraftToolError) as caught:
            self.commit(path, contacts=other_contact_rows() + [shm_contact_row()])
        self.assertEqual(self.writes, [])
        self.assertIn("BEFORE the replay lock", str(caught.exception))
        self.assertFalse(list((self.plan_dir / ".commit-locks").glob("*.json")))

    def test_the_lock_is_written_before_the_post(self):
        path = self.stage()
        plan = self.plan_json(path)
        lock = self.plan_dir / ".commit-locks" / f"{plan['sha256']}.json"
        observed: list[bool] = []

        original = draft.oauth_shm_customer_write_allowed

        def spy(*args, **kwargs):
            observed.append(lock.exists())
            return original(*args, **kwargs)

        with patch.object(draft, "oauth_shm_customer_write_allowed", side_effect=spy):
            self.commit(path)
        self.assertEqual(observed, [True])

    def test_a_committed_plan_cannot_be_replayed(self):
        path = self.stage()
        self.commit(path)
        with self.assertRaises(draft.DraftToolError) as caught:
            self.commit(path)
        self.assertEqual(self.writes, [])
        self.assertIn("already entered commit", str(caught.exception))

    def test_a_transport_failure_locks_the_plan_no_retry(self):
        path = self.stage()
        plan = self.plan_json(path)
        with self.assertRaises(draft.DraftToolError) as caught:
            self.commit(path, write=URLError("timed out"))
        self.assertIn("indeterminate", str(caught.exception))
        lock = json.loads(
            (self.plan_dir / ".commit-locks" / f"{plan['sha256']}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(lock["status"], "indeterminate")
        self.assertIs(lock["no_retry"], True)
        self.assertIs(lock["write_attempted"], True)
        # And it is never retried.
        with self.assertRaises(draft.DraftToolError):
            self.commit(path)

    def test_an_http_error_locks_the_plan_no_retry(self):
        path = self.stage()
        error = HTTPError("https://x", 400, "Bad Request", None, None)
        error.read = lambda: b'{"code":1001}'
        with self.assertRaises(draft.DraftToolError):
            self.commit(path, write=error)
        plan = self.plan_json(path)
        lock = json.loads(
            (self.plan_dir / ".commit-locks" / f"{plan['sha256']}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIs(lock["no_retry"], True)

    def test_a_wrong_readback_becomes_indeterminate(self):
        path = self.stage()
        with self.assertRaises(draft.DraftToolError) as caught:
            self.commit(path, readback=shm_contact(status="inactive"))
        self.assertIn("indeterminate", str(caught.exception))

    def test_a_readback_with_a_wrong_billing_field_is_indeterminate(self):
        path = self.stage()
        broken = shm_contact(billing_address=shm_billing_address(city="Vancouver"))
        with self.assertRaises(draft.DraftToolError) as caught:
            self.commit(path, readback=broken)
        self.assertIn("indeterminate", str(caught.exception))

    def test_a_readback_with_the_wrong_contact_email_is_indeterminate(self):
        path = self.stage()
        broken = shm_contact()
        broken["contact_persons"][0]["email"] = "someone@example.com"
        with self.assertRaises(draft.DraftToolError):
            self.commit(path, readback=broken)

    def test_zoho_defaults_are_recorded_not_rejected(self):
        path = self.stage()
        record = shm_contact(
            payment_terms=0, portal_status="disabled", created_time="2026-08-12"
        )
        self.commit(path, created=record, readback=record)
        plan = self.plan_json(path)
        lock = json.loads(
            (self.plan_dir / ".commit-locks" / f"{plan['sha256']}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(lock["status"], "committed_verified")


class ShmCustomerRouteTests(ShmCustomerTestCase):
    def test_only_post_is_allowed(self):
        for method in ("GET", "PUT", "PATCH", "DELETE"):
            with self.assertRaises(draft.DraftToolError):
                draft.require_shm_post_allowed(
                    method, "/books/v3/contacts", ORG_ID, draft.SHM_CUSTOMER_PAYLOAD
                )

    def test_only_the_contact_collection_route_is_allowed(self):
        for path in (
            "/books/v3/contacts/96274000001600001",
            "/books/v3/contacts/96274000001600001/active",
            "/books/v3/invoices",
            "/books/v3/estimates",
            "/books/v3/salesorders",
            "/books/v3/contacts?x=1",
            "/books/v3/contacts/",
        ):
            with self.assertRaises(draft.DraftToolError):
                draft.require_shm_post_allowed(
                    "POST", path, ORG_ID, draft.SHM_CUSTOMER_PAYLOAD
                )

    def test_any_altered_payload_is_refused(self):
        for mutate in (
            lambda body: body.update({"contact_name": "Other"}),
            lambda body: body.update({"website": "https://example.com"}),
            lambda body: body.pop("customer_sub_type"),
            lambda body: body["billing_address"].update({"city": "Vancouver"}),
            lambda body: body["contact_persons"].append({"first_name": "X"}),
        ):
            body = copy.deepcopy(draft.SHM_CUSTOMER_PAYLOAD)
            mutate(body)
            with self.assertRaises(draft.DraftToolError):
                draft.require_shm_post_allowed("POST", "/books/v3/contacts", ORG_ID, body)

    def test_the_action_reaches_no_other_write_route(self):
        source = inspect.getsource(draft.oauth_shm_customer_write_allowed)
        source += inspect.getsource(draft.command_commit_shm_inv000051_customer)
        source += inspect.getsource(draft.command_stage_shm_inv000051_customer)
        for forbidden in (
            "/books/v3/invoices",
            "/books/v3/estimates",
            "/books/v3/salesorders",
            "/books/v3/items",
            '/books/v3/contacts/',
            'method="PUT"',
            'method="DELETE"',
            'method="PATCH"',
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("CONTACTS_COLLECTION_PATH", source)
        self.assertIn('method="POST"', source)


# ---------------------------------------------------------------------------
# PLAN B -- the fixed INV-000051 correction
# ---------------------------------------------------------------------------


class ShmCorrectionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.plan_dir = Path(self._temp.name).resolve() / "zoho_invoice_revision_plans"
        self.plan_dir.mkdir(parents=True)
        self.addCleanup(self._temp.cleanup)
        self._origin = patch.object(
            revision,
            "origin_record",
            return_value={
                "tool_path": str(Path(revision.__file__).resolve()),
                "repo_root": str(revision.ROOT),
                "plan_dir": str(self.plan_dir),
            },
        )
        self._origin.start()
        self.addCleanup(self._origin.stop)

    def router(
        self,
        *,
        invoice,
        contact=None,
        addresses=None,
        taxes=None,
        salesorder=None,
        contacts=None,
    ):
        """One api_get for every read the action makes, in any order."""
        invoices = list(invoice) if isinstance(invoice, list) else [invoice]
        orders = (
            list(salesorder)
            if isinstance(salesorder, list)
            else [salesorder if salesorder is not None else live_salesorder()]
        )
        contact = contact if contact is not None else shm_contact()
        addresses = addresses if addresses is not None else [shm_billing_address()]
        taxes = taxes if taxes is not None else live_taxes()
        rows = other_contact_rows() + [shm_contact_row()] if contacts is None else contacts
        pages = contact_pages(rows, revision.SHM_CONTACT_PER_PAGE)
        self.invoice_reads = 0
        self.order_reads = 0

        def api_get(access_token, api_domain, path):
            if path.startswith("/books/v3/organizations"):
                return {"code": 0, "organizations": copy.deepcopy(ORGANIZATIONS)}
            if re.match(r"^/books/v3/invoices/\d+\?", path):
                index = min(self.invoice_reads, len(invoices) - 1)
                self.invoice_reads += 1
                return {"code": 0, "invoice": copy.deepcopy(invoices[index])}
            if re.match(r"^/books/v3/salesorders/\d+\?", path):
                index = min(self.order_reads, len(orders) - 1)
                self.order_reads += 1
                return {"code": 0, "salesorder": copy.deepcopy(orders[index])}
            if re.match(r"^/books/v3/contacts/\d+/address\?", path):
                return {"code": 0, "addresses": copy.deepcopy(addresses)}
            if re.match(r"^/books/v3/contacts/\d+\?", path):
                return {"code": 0, "contact": copy.deepcopy(contact)}
            if path.startswith("/books/v3/contacts?"):
                number = int(re.search(r"page=(\d+)", path).group(1))
                return copy.deepcopy(pages[number - 1])
            if path.startswith("/books/v3/settings/taxes"):
                return {"code": 0, "taxes": copy.deepcopy(taxes)}
            raise AssertionError(f"unexpected GET {path}")

        return api_get

    def stage(self, **kwargs) -> Path:
        vault = fake_vault()
        kwargs.setdefault("invoice", live_invoice())
        existing = set(self.plan_dir.glob("*.json"))
        with patch.object(revision, "PLAN_DIR", self.plan_dir), patch.object(
            revision.zoho_tool, "append_receipt"
        ), patch.object(
            revision.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            revision.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(revision.zoho_tool, "save_vault"), patch.object(
            revision.zoho_tool, "api_get", side_effect=self.router(**kwargs)
        ), patch.object(revision, "pause"), patch.object(
            revision, "urlopen", side_effect=AssertionError("staging must never write")
        ):
            revision.command_stage_shm_correction(argparse.Namespace())
        created = set(self.plan_dir.glob("*.json")) - existing
        self.assertEqual(len(created), 1)
        return created.pop()

    def plan_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def rehash(self, path: Path, plan: dict) -> Path:
        core = dict(plan)
        core.pop("sha256", None)
        plan["sha256"] = revision.digest_for(core)
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def commit(
        self,
        plan_path: Path,
        *,
        approval: str = revision.APPROVAL_WORD,
        invoice=None,
        after=None,
        salesorder=None,
        after_salesorder=None,
        write=None,
        scopes=None,
        **kwargs,
    ):
        vault = fake_vault(scopes)
        before = invoice if invoice is not None else live_invoice()
        after = after if after is not None else corrected_invoice(before)
        order_before = salesorder if salesorder is not None else live_salesorder()
        order_after = (
            after_salesorder
            if after_salesorder is not None
            else corrected_salesorder(order_before)
        )
        self.writes: list[tuple] = []

        def fake_urlopen(request, timeout=None):
            self.writes.append(
                (
                    request.get_method(),
                    request.full_url,
                    json.loads(request.data.decode("utf-8")),
                )
            )
            if write is not None:
                raise write
            return FakeResponse({"code": 0, "invoice": copy.deepcopy(after)})

        # Reads before the PUT see the pre-write records; reads after it see the
        # post-write records.
        router = self.router(
            invoice=[before, after], salesorder=[order_before, order_after], **kwargs
        )

        with patch.object(revision, "PLAN_DIR", self.plan_dir), patch.object(
            revision.zoho_tool, "append_receipt"
        ), patch.object(
            revision.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            revision.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(revision.zoho_tool, "save_vault"), patch.object(
            revision.zoho_tool, "api_get", side_effect=router
        ), patch.object(revision, "urlopen", side_effect=fake_urlopen):
            revision.command_commit_shm_correction(
                argparse.Namespace(plan=str(plan_path), approval=approval)
            )

    def lock_for(self, path: Path) -> dict:
        plan = self.plan_json(path)
        return json.loads(
            (self.plan_dir / ".commit-locks" / f"{plan['sha256']}.json").read_text(
                encoding="utf-8"
            )
        )


class CorrectionFixedTargetTests(ShmCorrectionTestCase):
    def test_the_target_is_pinned_to_one_invoice(self):
        self.assertEqual(revision.SHM_INVOICE_ID, "96274000001559012")
        self.assertEqual(revision.SHM_INVOICE_NUMBER, "INV-000051")
        self.assertEqual(revision.SHM_PUT_PATH, "/books/v3/invoices/96274000001559012")

    def test_the_commissioned_values_are_exactly_rachads(self):
        self.assertEqual(revision.SHM_NEW_REFERENCE, "0000031")
        self.assertEqual(revision.SHM_NEW_TAX_ID, "96274000000035516")
        self.assertEqual(revision.SHM_NEW_TAX_NAME, "ON HST")
        self.assertEqual(str(revision.SHM_NEW_TAX_PERCENT), "13")
        self.assertEqual(revision.SHM_CUSTOMER_NAME, "SHM Marine Constructors JV")

    def test_the_independent_decimal_target(self):
        totals = revision.shm_expected_totals()
        self.assertEqual(totals["sub_total"], "13020.00")
        self.assertEqual(totals["tax_total"], "1692.60")
        self.assertEqual(totals["tax_total_per_line_sum"], "1692.60")
        self.assertEqual(totals["total"], "14712.60")
        self.assertEqual(totals["balance"], "14712.60")
        self.assertEqual(totals["discount_total"], "0.00")
        self.assertEqual(totals["line_totals"], ["2328.00", "10692.00"])

    def test_the_commands_take_no_business_parameter(self):
        parser = revision.build_parser()
        stage = parser._actions[1].choices["stage-inv000051-shm-correction"]
        self.assertEqual(
            [a for a in stage._actions if a.dest not in ("help", "func")], []
        )
        commit = parser._actions[1].choices["commit-inv000051-shm-correction"]
        self.assertEqual(
            sorted(a.dest for a in commit._actions if a.dest not in ("help", "func")),
            ["approval", "plan"],
        )


class CorrectionStageTests(ShmCorrectionTestCase):
    def test_a_clean_stage_produces_a_reviewable_plan(self):
        plan = self.plan_json(self.stage())
        evidence = plan["live_evidence"]
        self.assertEqual(plan["action"], revision.SHM_ACTION)
        self.assertEqual(evidence["put_endpoint"], f"PUT {revision.SHM_PUT_PATH}")
        self.assertEqual(evidence["changes"]["customer_id"]["new"], SHM_ID)
        self.assertEqual(evidence["changes"]["reference_number"]["new"], "0000031")
        self.assertEqual(evidence["changes"]["line_1_tax_id"]["new"], HST_ID)
        self.assertEqual(evidence["changes"]["line_2_tax_id"]["new"], HST_ID)
        self.assertEqual(evidence["totals"]["after"]["total"], "14712.60")
        self.assertIs(evidence["email_sent"], False)
        self.assertIs(plan["risk"]["salesorder_written"], False)
        self.assertIs(plan["risk"]["atomic_with_customer_plan"], False)

    def test_the_put_payload_resends_both_lines_in_order(self):
        evidence = self.plan_json(self.stage())["live_evidence"]
        lines = evidence["put_payload"]["line_items"]
        self.assertEqual(len(lines), 2)
        self.assertEqual([line["line_item_id"] for line in lines], [LINE_ONE, LINE_TWO])
        self.assertEqual(
            [line["salesorder_item_id"] for line in lines],
            [
                revision.SHM_LINES[0]["salesorder_item_id"],
                revision.SHM_LINES[1]["salesorder_item_id"],
            ],
        )
        for line in lines:
            self.assertEqual(line["tax_id"], HST_ID)

    def test_the_payload_carries_no_lifecycle_mail_or_status_key(self):
        payload = self.plan_json(self.stage())["live_evidence"]["put_payload"]
        self.assertEqual(set(payload), revision.SHM_ALLOWED_PUT_KEYS)
        for forbidden in (
            "status",
            "send",
            "email",
            "to_mail_ids",
            "exchange_rate",
            "currency_id",
            "shipping_charge",
            "adjustment",
            "custom_fields",
            "shipping_address_id",
            "salesorder_id",
        ):
            self.assertNotIn(forbidden, payload)

    def test_the_plan_discloses_the_sales_order_divergence(self):
        evidence = self.plan_json(self.stage())["live_evidence"]
        self.assertIn("SO-00050", evidence["salesorder_disclosure"])
        self.assertIn("approved exception", evidence["salesorder_disclosure"])
        self.assertIn("no sales-order write route", evidence["salesorder_disclosure"])

    def test_the_sales_order_fingerprint_is_captured(self):
        evidence = self.plan_json(self.stage())["live_evidence"]
        order = evidence["salesorder"]
        self.assertEqual(order["salesorder_id"], SO_ID)
        self.assertEqual(order["salesorder_number"], "SO-00050")
        self.assertRegex(order["protected_state_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("invoices", order["protected_state"])
        self.assertEqual(len(order["invoice_mirror"]), 1)
        self.assertIs(order["write_route_exists"], False)

    def test_three_stable_rounds_are_required(self):
        plan = self.plan_json(self.stage())
        rehearsal = plan["live_evidence"]["rehearsal"]
        self.assertEqual(rehearsal["observations"], 3)
        self.assertIs(rehearsal["stable"], True)
        self.assertEqual(len(rehearsal["observation_sha256"]), 3)
        self.assertEqual(len(set(rehearsal["observation_sha256"])), 1)

    def test_an_invoice_that_moves_between_rounds_refuses(self):
        drifted = live_invoice(notes="Changed mid-read")
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.stage(invoice=[live_invoice(), live_invoice(), drifted])
        self.assertIn("not stable", str(caught.exception))

    def test_a_sales_order_that_moves_between_rounds_refuses(self):
        drifted = live_salesorder(terms="new terms")
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.stage(
                invoice=live_invoice(),
                salesorder=[live_salesorder(), live_salesorder(), drifted],
            )
        self.assertIn("not stable", str(caught.exception))

    def test_moving_line_stock_between_rounds_does_not_refuse(self):
        # Live item stock is not an invoice field and must not block staging.
        second = live_invoice()
        second["line_items"][0]["available_stock"] = 999.0
        second["line_items"][0]["stock_on_hand"] = 999.0
        self.stage(invoice=[live_invoice(), second, live_invoice()])


class CorrectionStageRefusalTests(ShmCorrectionTestCase):
    def test_a_missing_shm_customer_refuses(self):
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.stage(contacts=other_contact_rows())
        self.assertIn("no customer named", str(caught.exception))

    def test_two_customers_with_the_same_name_refuse(self):
        rows = other_contact_rows() + [
            shm_contact_row(),
            dict(shm_contact_row(), contact_id="96274000001600099"),
        ]
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.stage(contacts=rows)
        self.assertIn("will not guess", str(caught.exception))

    def test_an_inactive_customer_refuses(self):
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.stage(contact=shm_contact(status="inactive"))
        self.assertIn("not active", str(caught.exception))

    def test_a_wrong_currency_customer_refuses(self):
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.stage(contact=shm_contact(currency_code="USD"))
        self.assertIn("never changes an invoice's currency", str(caught.exception))

    def test_a_customer_billing_address_off_the_po_refuses(self):
        for key, wrong in (
            ("address", "343A Bay Street"),
            ("city", "Vancouver"),
            ("state", "ON"),
            ("zip", "V8T 1P5"),
            ("country", "USA"),
            ("phone", "672-974-4420"),
        ):
            with self.assertRaises(revision.InvoiceRevisionError) as caught:
                self.stage(contact=shm_contact(billing_address=shm_billing_address(**{key: wrong})))
            self.assertIn("client PO value", str(caught.exception))

    def test_a_customer_with_a_shipping_address_refuses(self):
        contact = shm_contact(
            shipping_address=dict(blank_address(SHM_SHIPPING_ADDRESS_ID), city="Victoria")
        )
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.stage(contact=contact)
        self.assertIn("shipping address", str(caught.exception))

    def test_a_wrong_primary_contact_email_refuses(self):
        contact = shm_contact()
        contact["contact_persons"][0]["email"] = "someone@example.com"
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.stage(contact=contact)
        self.assertIn("primary contact email", str(caught.exception))

    def test_an_address_not_owned_by_the_customer_refuses(self):
        index = revision.address_index([shm_billing_address()], shm_contact())
        self.assertIn(SHM_BILLING_ADDRESS_ID, index)
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            revision.owned_address("96274000000999999", index, SHM_ID, "the SHM billing address")
        self.assertIn("not one of the addresses owned by", str(caught.exception))

    def test_a_billing_address_missing_its_id_refuses(self):
        broken = shm_billing_address()
        broken.pop("address_id")
        with self.assertRaises(revision.InvoiceRevisionError):
            self.stage(contact=shm_contact(billing_address=broken), addresses=[broken])

    def test_a_missing_or_wrong_on_hst_refuses(self):
        for taxes, fragment in (
            ([{"tax_id": GST_ID, "tax_name": "GST", "tax_percentage": 5, "status": "Active"}], "does not exist"),
            ([{"tax_id": HST_ID, "tax_name": "ON HST", "tax_percentage": 15, "status": "Active"}], "not exactly"),
            ([{"tax_id": HST_ID, "tax_name": "ON HST", "tax_percentage": 13, "status": "Inactive"}], "not Active"),
            ([{"tax_id": HST_ID, "tax_name": "Tax", "tax_percentage": 13, "status": "Active"}], "not 'ON HST'"),
        ):
            with self.assertRaises(revision.InvoiceRevisionError) as caught:
                self.stage(taxes=taxes)
            self.assertIn(fragment, str(caught.exception))

    def test_a_wrong_starting_state_refuses(self):
        for overrides, fragment in (
            ({"customer_id": "96274000000999999"}, "not the expected starting value"),
            ({"reference_number": "SOMETHING"}, "not the expected starting value"),
            ({"date": "2026-08-11"}, "not the expected starting value"),
            ({"status": "paid"}, "commissioned for INV-000051"),
            ({"status": "draft"}, "commissioned for INV-000051"),
            ({"total": 999.0, "balance": 999.0}, "not the expected starting"),
            ({"exchange_rate": 1.35}, "exchange rate is not exactly 1"),
        ):
            with self.assertRaises(revision.InvoiceRevisionError) as caught:
                self.stage(invoice=live_invoice(**overrides))
            self.assertIn(fragment, str(caught.exception))

    def test_a_dependency_refuses(self):
        for overrides in (
            {"payment_made": 10.0, "balance": 13661.0},
            {"credits_applied": 5.0, "balance": 13666.0},
            {"write_off_amount": 1.0, "balance": 13670.0},
            {"payments": [{"payment_id": "1"}]},
            {"packages": [{"package_id": "1"}]},
            {"shipments": [{"shipment_id": "1"}]},
            {"recurring_invoice_id": "96274000000123456"},
        ):
            with self.assertRaises(revision.InvoiceRevisionError):
                self.stage(invoice=live_invoice(**overrides))

    def test_an_existing_shipping_address_on_the_invoice_refuses(self):
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.stage(invoice=live_invoice(shipping_address=ralmax_billing_address()))
        self.assertIn("blank before and after", str(caught.exception))

    def test_a_changed_line_refuses(self):
        for index, overrides, fragment in (
            (0, {"quantity": 25.0}, "quantity"),
            (0, {"rate": 98.0}, "rate"),
            (1, {"discount": 5.0}, "discount"),
            (0, {"description": "Something else"}, "description"),
            (0, {"tax_id": HST_ID}, "not the expected starting"),
            (0, {"item_id": "96274000001518999"}, "item_id"),
            (0, {"salesorder_item_id": "96274000001558999"}, "salesorder_item_id"),
        ):
            invoice = live_invoice()
            invoice["line_items"][index].update(overrides)
            with self.assertRaises(revision.InvoiceRevisionError) as caught:
                self.stage(invoice=invoice)
            self.assertIn(fragment, str(caught.exception))

    def test_an_added_or_dropped_line_refuses(self):
        invoice = live_invoice()
        invoice["line_items"] = invoice["line_items"][:1]
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.stage(invoice=invoice)
        self.assertIn("not the fixed 2", str(caught.exception))

        invoice = live_invoice()
        invoice["line_items"].append(live_line(0, line_item_id="96274000001559099"))
        with self.assertRaises(revision.InvoiceRevisionError):
            self.stage(invoice=invoice)

    def test_a_reordered_line_refuses(self):
        invoice = live_invoice()
        invoice["line_items"].reverse()
        with self.assertRaises(revision.InvoiceRevisionError):
            self.stage(invoice=invoice)

    def test_a_wrong_linked_sales_order_refuses(self):
        for overrides, fragment in (
            ({"salesorder_number": "SO-99999"}, "not 'SO-00050'"),
            ({"customer_id": "96274000000999999"}, "belongs to customer"),
        ):
            with self.assertRaises(revision.InvoiceRevisionError) as caught:
                self.stage(salesorder=live_salesorder(**overrides))
            self.assertIn(fragment, str(caught.exception))

    def test_a_sales_order_with_a_changed_line_refuses(self):
        order = live_salesorder()
        order["line_items"][1]["quantity"] = 37.0
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.stage(salesorder=order)
        self.assertIn("quantity", str(caught.exception))

    def test_an_invoice_linked_to_a_different_order_refuses(self):
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.stage(invoice=live_invoice(salesorder_id="96274000001558999"))
        self.assertIn("not the expected starting value", str(caught.exception))


class CorrectionGeneralActionUnchangedTests(unittest.TestCase):
    """The general action must keep both of its own refusals."""

    def test_the_general_action_still_refuses_an_overdue_invoice(self):
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            revision.dependency_state(live_invoice())
        self.assertIn("status is 'overdue'", str(caught.exception))
        self.assertEqual(revision.ALLOWED_STATUSES, ("draft", "sent"))

    def test_the_general_action_still_accepts_draft_and_sent(self):
        for status in ("draft", "sent"):
            revision.dependency_state(live_invoice(status=status))

    def test_the_general_action_still_refuses_line_edits_on_a_linked_invoice(self):
        source = inspect.getsource(revision.build_revision)
        self.assertIn("linked to sales order", source)
        self.assertIn("salesorder_id = str(invoice.get(\"salesorder_id\") or \"\").strip()", source)

    def test_the_fixed_action_does_not_use_the_general_precondition(self):
        source = inspect.getsource(revision.build_shm_correction)
        self.assertIn("shm_dependency_state(", source)
        self.assertIsNone(re.search(r"(?<!shm_)dependency_state\(", source))

    def test_only_the_fixed_action_accepts_overdue(self):
        state = revision.shm_dependency_state(live_invoice())
        self.assertEqual(state["status"], "overdue")
        for status in ("draft", "sent", "paid", "void"):
            with self.assertRaises(revision.InvoiceRevisionError):
                revision.shm_dependency_state(live_invoice(status=status))


class CorrectionPlanIntegrityTests(ShmCorrectionTestCase):
    def test_a_tampered_payload_is_refused_offline(self):
        for mutate in (
            lambda payload: payload.update({"customer_id": "96274000000999999"}),
            lambda payload: payload.update({"reference_number": "9999999"}),
            lambda payload: payload.update({"billing_address_id": "96274000000999999"}),
            lambda payload: payload["line_items"][0].update({"quantity": 25.0}),
            lambda payload: payload["line_items"][1].update({"tax_id": GST_ID}),
        ):
            path = self.stage()
            plan = self.plan_json(path)
            mutate(plan["live_evidence"]["put_payload"])
            self.rehash(path, plan)
            with self.assertRaises(revision.InvoiceRevisionError) as caught:
                revision.load_shm_plan(path)
            self.assertIn("canonical projection", str(caught.exception))

    def test_a_tampered_payload_is_also_refused_before_the_lock(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["put_payload"]["customer_id"] = "96274000000999999"
        plan["live_evidence"]["customer"]["customer_id"] = "96274000000999999"
        self.rehash(path, plan)
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path)
        self.assertEqual(self.writes, [])

    def test_a_tampered_total_is_refused(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["totals"]["after"]["total"] = "13671.00"
        self.rehash(path, plan)
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            revision.load_shm_plan(path)
        self.assertIn("independent Decimal derivation", str(caught.exception))

    def test_a_tampered_endpoint_is_refused(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["put_endpoint"] = "PUT /books/v3/invoices/96274000001559013"
        self.rehash(path, plan)
        with self.assertRaises(revision.InvoiceRevisionError):
            revision.load_shm_plan(path)

    def test_a_weakened_exemption_list_is_refused(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["unprotected_keys"].append("reminders_sent")
        self.rehash(path, plan)
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            revision.load_shm_plan(path)
        self.assertIn("fingerprint exemptions", str(caught.exception))

    def test_reminders_sent_is_never_exempt(self):
        self.assertNotIn("reminders_sent", revision.shm_unprotected_keys())
        self.assertNotIn("is_emailed", revision.shm_unprotected_keys())

    def test_a_dropped_sales_order_disclosure_is_refused(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["salesorder_disclosure"] = "nothing to see"
        self.rehash(path, plan)
        with self.assertRaises(revision.InvoiceRevisionError):
            revision.load_shm_plan(path)

    def test_a_wrong_tool_action_or_schema_is_refused(self):
        path = self.stage()
        for field, value in (
            ("tool", "Other"),
            ("action", "invoice_revision"),
            ("schema_version", 9),
            ("approval_required", "OK"),
        ):
            plan = self.plan_json(path)
            plan[field] = value
            self.rehash(path, plan)
            with self.assertRaises(revision.InvoiceRevisionError):
                revision.load_shm_plan(path)

    def test_an_expired_plan_is_refused(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["created_utc"] = "2026-08-01T00:00:00+00:00"
        plan["expires_utc"] = "2026-08-02T00:00:00+00:00"
        self.rehash(path, plan)
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            revision.load_shm_plan(path)
        self.assertIn("expired", str(caught.exception))

    def test_an_unhashed_edit_is_refused(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["put_payload"]["reference_number"] = "9999999"
        path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            revision.load_shm_plan(path)
        self.assertIn("hash check failed", str(caught.exception))

    def test_a_plan_outside_the_folder_is_refused(self):
        path = self.stage()
        outside = Path(self._temp.name) / "elsewhere.json"
        outside.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(revision.InvoiceRevisionError):
            revision.contained_plan(str(outside))

    def test_a_tampered_rehearsal_is_refused(self):
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["rehearsal"]["observations"] = 1
        self.rehash(path, plan)
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            revision.load_shm_plan(path)
        self.assertIn("exactly 3 rounds", str(caught.exception))


class CorrectionApprovalTests(ShmCorrectionTestCase):
    def test_a_correct_approval_issues_exactly_one_put(self):
        path = self.stage()
        self.commit(path)
        self.assertEqual(len(self.writes), 1)
        method, url, body = self.writes[0]
        self.assertEqual(method, "PUT")
        self.assertIn(revision.SHM_PUT_PATH, url)
        self.assertEqual(body["customer_id"], SHM_ID)
        self.assertEqual(body["reference_number"], "0000031")
        self.assertEqual([line["tax_id"] for line in body["line_items"]], [HST_ID, HST_ID])

    def test_a_wrong_word_never_reaches_the_network(self):
        path = self.stage()
        for word in ("approve", "yes", "APPROVE", "confirmed"):
            with self.assertRaises(revision.InvoiceRevisionError):
                self.commit(path, approval=word)
            self.assertEqual(self.writes, [])

    def test_a_padded_or_lowercase_approval_is_refused(self):
        path = self.stage()
        for word in (" APPROVED", "APPROVED ", "approved", "Approved", "APPROVED\t"):
            with self.assertRaises(revision.InvoiceRevisionError):
                self.commit(path, approval=word)
            self.assertEqual(self.writes, [])

    def test_a_missing_update_scope_refuses_before_the_lock(self):
        path = self.stage()
        scopes = [
            scope
            for scope in list(zoho_tool.READ_SCOPES) + list(zoho_tool.ALLOWED_WRITE_SCOPES)
            if scope != "ZohoBooks.invoices.UPDATE"
        ]
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.commit(path, scopes=scopes)
        self.assertEqual(self.writes, [])
        self.assertIn("BEFORE the replay lock", str(caught.exception))


class CorrectionCommitDriftTests(ShmCorrectionTestCase):
    def test_an_invoice_changed_after_review_refuses_before_the_lock(self):
        path = self.stage()
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.commit(path, invoice=live_invoice(notes="Changed"))
        self.assertEqual(self.writes, [])
        self.assertIn("BEFORE the replay lock", str(caught.exception))
        self.assertFalse(list((self.plan_dir / ".commit-locks").glob("*.json")))

    def test_a_sales_order_changed_after_review_refuses_before_the_lock(self):
        path = self.stage()
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path, salesorder=live_salesorder(terms="new"))
        self.assertEqual(self.writes, [])

    def test_a_customer_replaced_after_review_refuses_before_the_lock(self):
        path = self.stage()
        rows = other_contact_rows() + [dict(shm_contact_row(), contact_id="96274000001600555")]
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.commit(path, contacts=rows)
        self.assertEqual(self.writes, [])
        self.assertIn("not the approved", str(caught.exception))

    def test_the_lock_is_written_before_the_put(self):
        path = self.stage()
        plan = self.plan_json(path)
        lock = self.plan_dir / ".commit-locks" / f"{plan['sha256']}.json"
        observed: list[bool] = []
        original = revision.oauth_shm_correction_write_allowed

        def spy(*args, **kwargs):
            observed.append(lock.exists())
            return original(*args, **kwargs)

        with patch.object(revision, "oauth_shm_correction_write_allowed", side_effect=spy):
            self.commit(path)
        self.assertEqual(observed, [True])

    def test_a_committed_plan_cannot_be_replayed(self):
        path = self.stage()
        self.commit(path)
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.commit(path)
        self.assertEqual(self.writes, [])
        self.assertIn("already entered commit", str(caught.exception))

    def test_a_timeout_locks_the_plan_no_retry(self):
        path = self.stage()
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.commit(path, write=URLError("timed out"))
        self.assertIn("indeterminate", str(caught.exception))
        lock = self.lock_for(path)
        self.assertEqual(lock["status"], "indeterminate")
        self.assertIs(lock["no_retry"], True)
        self.assertIs(lock["write_attempted"], True)

    def test_an_api_error_locks_the_plan_no_retry(self):
        path = self.stage()
        error = HTTPError("https://x", 400, "Bad Request", None, None)
        error.read = lambda: b'{"code":1002,"message":"nope"}'
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path, write=error)
        self.assertIs(self.lock_for(path)["no_retry"], True)


class CorrectionReadBackTests(ShmCorrectionTestCase):
    def test_a_clean_commit_verifies_and_locks(self):
        path = self.stage()
        self.commit(path)
        lock = self.lock_for(path)
        self.assertEqual(lock["status"], "committed_verified")
        self.assertIs(lock["no_retry"], True)

    def test_a_wrong_total_after_the_write_is_indeterminate(self):
        path = self.stage()
        after = corrected_invoice(total=13671.0, balance=13671.0)
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.commit(path, after=after)
        self.assertIn("indeterminate", str(caught.exception))
        self.assertIs(self.lock_for(path)["no_retry"], True)

    def test_a_status_change_after_the_write_is_indeterminate(self):
        path = self.stage()
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path, after=corrected_invoice(status="paid"))
        self.assertEqual(self.lock_for(path)["status"], "indeterminate")

    def test_a_wrong_customer_after_the_write_is_indeterminate(self):
        path = self.stage()
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path, after=corrected_invoice(customer_id=OLD_CUSTOMER_ID))
        self.assertEqual(self.lock_for(path)["status"], "indeterminate")

    def test_a_wrong_reference_after_the_write_is_indeterminate(self):
        path = self.stage()
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path, after=corrected_invoice(reference_number="SO-00050"))

    def test_a_line_still_on_gst_after_the_write_is_indeterminate(self):
        path = self.stage()
        after = corrected_invoice()
        after["line_items"][1]["tax_id"] = GST_ID
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.commit(path, after=after)
        self.assertIn("indeterminate", str(caught.exception))

    def test_a_shipping_address_appearing_after_the_write_is_indeterminate(self):
        path = self.stage()
        after = corrected_invoice(shipping_address=ralmax_billing_address())
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path, after=after)

    def test_a_billing_address_off_the_po_after_the_write_is_indeterminate(self):
        path = self.stage()
        billing = {
            key: value for key, value in shm_billing_address(city="Vancouver").items()
            if key != "address_id"
        }
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path, after=corrected_invoice(billing_address=billing))

    def test_a_changed_quantity_or_rate_after_the_write_is_indeterminate(self):
        for index, overrides in ((0, {"quantity": 25.0}), (1, {"rate": 298.0})):
            path = self.stage()
            after = corrected_invoice()
            after["line_items"][index].update(overrides)
            with self.assertRaises(revision.InvoiceRevisionError):
                self.commit(path, after=after)

    def test_a_dropped_line_after_the_write_is_indeterminate(self):
        path = self.stage()
        after = corrected_invoice()
        after["line_items"] = after["line_items"][:1]
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path, after=after)

    def test_a_lost_salesorder_link_after_the_write_is_indeterminate(self):
        path = self.stage()
        after = corrected_invoice()
        after["line_items"][0]["salesorder_item_id"] = ""
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path, after=after)

    def test_a_reminder_sent_by_zoho_is_indeterminate(self):
        path = self.stage()
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.commit(path, after=corrected_invoice(reminders_sent=1))
        self.assertIn("indeterminate", str(caught.exception))

    def test_an_is_emailed_flip_is_indeterminate(self):
        path = self.stage()
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path, after=corrected_invoice(is_emailed=False))

    def test_a_protected_field_moving_is_indeterminate(self):
        path = self.stage()
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.commit(path, after=corrected_invoice(notes="Rewritten"))
        self.assertIn("indeterminate", str(caught.exception))

    def test_a_changed_invoice_number_is_indeterminate(self):
        path = self.stage()
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path, after=corrected_invoice(invoice_number="INV-000052"))


class CorrectionSalesOrderProtectionTests(ShmCorrectionTestCase):
    def test_an_unchanged_sales_order_passes(self):
        path = self.stage()
        self.commit(path)
        self.assertEqual(self.lock_for(path)["status"], "committed_verified")

    def test_a_sales_order_business_field_moving_is_indeterminate(self):
        for overrides in (
            {"customer_id": "96274000000999999"},
            {"total": 14712.60},
            {"status": "open"},
            {"invoiced_status": "not_invoiced"},
            {"reference_number": "0000031"},
            {"terms": "changed"},
        ):
            path = self.stage()
            with self.assertRaises(revision.InvoiceRevisionError) as caught:
                self.commit(path, after_salesorder=corrected_salesorder(**overrides))
            self.assertIn("indeterminate", str(caught.exception))

    def test_a_sales_order_line_moving_is_indeterminate(self):
        path = self.stage()
        order = corrected_salesorder()
        order["line_items"][0]["quantity"] = 25.0
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.commit(path, after_salesorder=order)
        self.assertIn("never move", str(caught.exception))

    def test_the_invoice_mirror_may_move_only_to_the_approved_figures(self):
        path = self.stage()
        order = corrected_salesorder()
        order["invoices"][0]["total"] = 99999.0
        with self.assertRaises(revision.InvoiceRevisionError) as caught:
            self.commit(path, after_salesorder=order)
        self.assertIn("not the approved consequence", str(caught.exception))

    def test_a_mirror_status_change_is_indeterminate(self):
        path = self.stage()
        order = corrected_salesorder()
        order["invoices"][0]["status"] = "paid"
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path, after_salesorder=order)

    def test_a_new_mirror_entry_is_indeterminate(self):
        path = self.stage()
        order = corrected_salesorder()
        order["invoices"].append(dict(order["invoices"][0], invoice_id="96274000001559099"))
        with self.assertRaises(revision.InvoiceRevisionError):
            self.commit(path, after_salesorder=order)

    def test_no_sales_order_write_route_exists(self):
        source = Path(revision.__file__).read_text(encoding="utf-8")
        self.assertNotIn("ZohoBooks.salesorders.UPDATE", source)
        for pattern in (
            r'method\s*=\s*"POST"[^)]*salesorders',
            r"salesorders/\{[^}]*\}\"[^\n]*method",
        ):
            self.assertIsNone(re.search(pattern, source))
        # Since 2026-08-12 the saved connection DOES carry a Books sales-order
        # UPDATE scope, commissioned for the fixed client-PO reference repair.
        # It is unreachable from this correction: the assertion above proves
        # this module never names it, and no Inventory sales-order write scope
        # exists at all.
        self.assertIn("ZohoBooks.salesorders.UPDATE", zoho_tool.ALLOWED_WRITE_SCOPES)
        self.assertNotIn("ZohoInventory.salesorders.UPDATE", zoho_tool.ALLOWED_WRITE_SCOPES)

    def test_the_sales_order_read_helper_is_get_only(self):
        source = inspect.getsource(revision.get_salesorder)
        self.assertIn("api_get", source)
        for forbidden in ("urlopen", "Request", "POST", "PUT", "DELETE"):
            self.assertNotIn(forbidden, source)


class CorrectionWriteAllowlistTests(ShmCorrectionTestCase):
    def payload(self) -> tuple[dict, dict]:
        invoice = live_invoice()
        lines = revision.validate_shm_live_invoice(invoice)
        body = revision.shm_put_payload(invoice, lines, SHM_ID, SHM_BILLING_ADDRESS_ID)
        return body, copy.deepcopy(body)

    def test_only_put_is_allowed(self):
        body, reviewed = self.payload()
        for method in ("GET", "POST", "PATCH", "DELETE"):
            with self.assertRaises(revision.InvoiceRevisionError):
                revision.require_shm_put_allowed(
                    method, revision.SHM_PUT_PATH, ORG_ID, body, reviewed
                )

    def test_only_the_one_invoice_route_is_allowed(self):
        body, reviewed = self.payload()
        for path in (
            "/books/v3/invoices/96274000001559013",
            "/books/v3/invoices",
            "/books/v3/invoices/96274000001559012/status/void",
            "/books/v3/invoices/96274000001559012/email",
            "/books/v3/salesorders/96274000001558003",
            "/books/v3/contacts",
        ):
            with self.assertRaises(revision.InvoiceRevisionError):
                revision.require_shm_put_allowed("PUT", path, ORG_ID, body, reviewed)

    def test_an_extra_or_missing_key_is_refused(self):
        for mutate in (
            lambda body: body.update({"status": "draft"}),
            lambda body: body.update({"send": True}),
            lambda body: body.update({"exchange_rate": 1.35}),
            lambda body: body.update({"shipping_address_id": "96274000000999999"}),
            lambda body: body.pop("billing_address_id"),
            lambda body: body.pop("invoice_number"),
        ):
            body, reviewed = self.payload()
            mutate(body)
            with self.assertRaises(revision.InvoiceRevisionError):
                revision.require_shm_put_allowed(
                    "PUT", revision.SHM_PUT_PATH, ORG_ID, body, reviewed
                )

    def test_a_changed_business_value_is_refused(self):
        for mutate in (
            lambda body: body.update({"reference_number": "SO-00050"}),
            lambda body: body.update({"invoice_number": "INV-000052"}),
            lambda body: body.update({"date": "2026-08-11"}),
            lambda body: body.update({"customer_id": OLD_CUSTOMER_ID}),
            lambda body: body["line_items"][0].update({"quantity": 25.0}),
            lambda body: body["line_items"][1].update({"rate": 298.0}),
            lambda body: body["line_items"][0].update({"discount": 5.0}),
            lambda body: body["line_items"][0].update({"tax_id": GST_ID}),
            lambda body: body["line_items"][0].update({"description": "other"}),
            lambda body: body["line_items"][0].update({"salesorder_item_id": "1"}),
        ):
            body, reviewed = self.payload()
            mutate(body)
            with self.assertRaises(revision.InvoiceRevisionError):
                revision.require_shm_put_allowed(
                    "PUT", revision.SHM_PUT_PATH, ORG_ID, body, reviewed
                )

    def test_a_dropped_added_or_reordered_line_is_refused(self):
        body, reviewed = self.payload()
        body["line_items"] = body["line_items"][:1]
        with self.assertRaises(revision.InvoiceRevisionError):
            revision.require_shm_put_allowed(
                "PUT", revision.SHM_PUT_PATH, ORG_ID, body, reviewed
            )

        body, reviewed = self.payload()
        body["line_items"].append(copy.deepcopy(body["line_items"][0]))
        with self.assertRaises(revision.InvoiceRevisionError):
            revision.require_shm_put_allowed(
                "PUT", revision.SHM_PUT_PATH, ORG_ID, body, reviewed
            )

        body, reviewed = self.payload()
        body["line_items"].reverse()
        with self.assertRaises(revision.InvoiceRevisionError):
            revision.require_shm_put_allowed(
                "PUT", revision.SHM_PUT_PATH, ORG_ID, body, reviewed
            )

    def test_a_duplicated_line_id_is_refused(self):
        body, reviewed = self.payload()
        body["line_items"][1]["line_item_id"] = LINE_ONE
        reviewed["line_items"][1]["line_item_id"] = LINE_ONE
        with self.assertRaises(revision.InvoiceRevisionError):
            revision.require_shm_put_allowed(
                "PUT", revision.SHM_PUT_PATH, ORG_ID, body, reviewed
            )

    def test_a_substituted_line_is_refused(self):
        body, reviewed = self.payload()
        body["line_items"][0]["line_item_id"] = "96274000001559099"
        reviewed["line_items"][0]["line_item_id"] = "96274000001559099"
        with self.assertRaises(revision.InvoiceRevisionError):
            revision.require_shm_put_allowed(
                "PUT", revision.SHM_PUT_PATH, ORG_ID, body, reviewed
            )

    def test_a_payload_diverging_from_the_reviewed_plan_is_refused(self):
        body, reviewed = self.payload()
        reviewed["billing_address_id"] = "96274000000999999"
        with self.assertRaises(revision.InvoiceRevisionError):
            revision.require_shm_put_allowed(
                "PUT", revision.SHM_PUT_PATH, ORG_ID, body, reviewed
            )


class CorrectionCapabilityTests(unittest.TestCase):
    """No mail, lifecycle, payment or foreign-record capability anywhere."""

    SOURCES = (
        "command_stage_shm_correction",
        "command_commit_shm_correction",
        "oauth_shm_correction_write_allowed",
        "require_shm_put_allowed",
        "shm_put_payload",
        "shm_read_round",
        "rehearse_shm_stable_state",
        "verify_shm_invoice",
        "verify_shm_salesorder_unchanged",
    )

    def action_source(self) -> str:
        return "\n".join(
            inspect.getsource(getattr(revision, name)) for name in self.SOURCES
        )

    def test_no_mail_transport_exists(self):
        source = self.action_source()
        for forbidden in (
            "smtplib",
            "sendmail",
            "/email",
            "to_mail_ids",
            "send=true",
            "outlook",
            "graph.microsoft",
        ):
            self.assertNotIn(forbidden, source.casefold())

    def test_no_lifecycle_payment_or_delete_route_exists(self):
        source = self.action_source()
        for forbidden in (
            "/status/",
            "/void",
            "/approve",
            "customerpayments",
            "creditnotes",
            "writeoff",
            "method=\"DELETE\"",
            "method=\"PATCH\"",
        ):
            self.assertNotIn(forbidden, source)

    def test_the_only_write_verb_in_the_action_is_put(self):
        source = inspect.getsource(revision.oauth_shm_correction_write_allowed)
        self.assertIn('method="PUT"', source)
        for forbidden in ('method="POST"', 'method="DELETE"', 'method="PATCH"'):
            self.assertNotIn(forbidden, source)

    def scope_literals(self, module) -> set[str]:
        """Every scope-shaped string the module actually declares in code."""
        return set(
            re.findall(
                r'"(Zoho(?:Books|Inventory)\.[A-Za-z]+\.[A-Z]+)"',
                Path(module.__file__).read_text(encoding="utf-8"),
            )
        )

    def test_the_module_declares_no_salesorder_or_delete_scope(self):
        declared = self.scope_literals(revision)
        self.assertIn("ZohoBooks.invoices.UPDATE", declared)
        for forbidden in (
            "ZohoBooks.invoices.DELETE",
            "ZohoBooks.invoices.ALL",
            "ZohoBooks.salesorders.UPDATE",
            "ZohoBooks.salesorders.CREATE",
            "ZohoBooks.contacts.CREATE",
        ):
            self.assertNotIn(forbidden, declared)

    def test_the_customer_action_declares_no_delete_scope(self):
        declared = self.scope_literals(draft)
        self.assertIn("ZohoBooks.contacts.CREATE", declared)
        for forbidden in (
            "ZohoBooks.contacts.DELETE",
            "ZohoBooks.contacts.ALL",
            "ZohoBooks.invoices.UPDATE",
            "ZohoBooks.salesorders.UPDATE",
        ):
            self.assertNotIn(forbidden, declared)

    def test_no_forbidden_scope_can_ever_be_saved(self):
        # zoho_tool refuses the whole connection if any of these appear.
        self.assertEqual(
            zoho_tool.FORBIDDEN_SCOPE_PARTS, (".UPDATE", ".DELETE", ".ALL", "fullaccess")
        )
        for allowed in ("ZohoBooks.contacts.CREATE", "ZohoBooks.invoices.UPDATE"):
            self.assertIn(allowed, zoho_tool.ALLOWED_WRITE_SCOPES)

    def test_neither_action_can_reach_the_other_records_write_route(self):
        customer_source = inspect.getsource(draft.oauth_shm_customer_write_allowed)
        invoice_source = inspect.getsource(revision.oauth_shm_correction_write_allowed)
        self.assertNotIn("invoices", customer_source)
        self.assertNotIn("contacts", invoice_source)


if __name__ == "__main__":
    unittest.main()
