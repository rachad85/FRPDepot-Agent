"""Safety tests for the Draft Purchase Order tool.

Commissioned by Rachad on 2026-08-13: Dado must be able to prepare a Purchase
Order in Zoho Books that is ready for HIM to review and send. The realistic
fixture is the GRP Jrain (JRAIN FRP LIMITED) J26-403 four-line D441 order --
BUILT AND TESTED ONLY; nothing in this file stages or commits it live.

NO TEST IN THIS FILE PERFORMS A LIVE CALL. Every read is a fake api_get and
every write is a fake urlopen; the real transports are asserted never to run.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import ast
import copy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

import zoho_tool as tool
import zoho_purchase_order_tool as po

ORG_ID = "96274000000000001"
# The real live FRP Depot vendor, read read-only on 2026-08-13.
VENDOR_ID = "96274000000027889"
VENDOR_NAME = "JRAIN FRP LIMITED"
PERSON_A = "96274000000027891"
PERSON_B = "96274000000050739"
ZERO_RATE_TAX = "96274000000035520"
GST_TAX = "96274000000035512"
GST_QST_GROUP = "96274000001071139"
SOURCE = "Attached workbook 'D441 Resin Nozzles' and Rachad's 2026-08-13 instruction."

# The four D441 items the J26-403 order needs. They are pending creation in
# Zoho, so these IDs stand for them in the mocked reads.
JRAIN_LINES = [
    ("96274000001600001", 'FRP NOZZLE-2"/50PSI/D441', "NZDN5050PSI441", "21", "22.00", "462.00"),
    ("96274000001600002", 'FRP NOZZLE-6"/50PSI/D441', "NZDN15050PSI441", "5", "52.00", "260.00"),
    ("96274000001600003", 'FRP MANWAY-24"/15PSI/D441', "MWDN60015PSI441", "3", "450.00", "1350.00"),
    ("96274000001600004", 'FRP MANWAY COVER-24"/15PSI/D441', "MWCDN60015PSI441", "3", "330.00", "990.00"),
]

ACTIVE_TAXES = [
    {"tax_id": ZERO_RATE_TAX, "tax_name": "Zero Rate", "tax_percentage": 0,
     "tax_type": "tax", "status": "Active", "is_inactive": False},
    {"tax_id": GST_TAX, "tax_name": "GST", "tax_percentage": 5,
     "tax_type": "tax", "status": "Active", "is_inactive": False},
    {"tax_id": GST_QST_GROUP, "tax_name": "Gst & Qst", "tax_percentage": 14.975,
     "tax_type": "tax_group", "status": "Active", "is_inactive": False},
]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def vendor_record(**overrides) -> dict:
    record = {
        "contact_id": VENDOR_ID,
        "contact_name": VENDOR_NAME,
        "company_name": VENDOR_NAME,
        "contact_type": "vendor",
        "customer_sub_type": "business",
        "status": "active",
        "currency_code": "USD",
        "currency_id": "96274000000000081",
        "payment_terms_label": "Due on Receipt",
        "contact_persons": [
            {"contact_person_id": PERSON_A, "email": "china.grp@gmail.com",
             "is_primary_contact": True},
            {"contact_person_id": PERSON_B, "first_name": "Fei", "last_name": "Huiling",
             "email": "fei@jrain-frp.com", "is_primary_contact": False},
        ],
    }
    record.update(overrides)
    return record


def item_record(item_id: str, name: str, sku: str, **overrides) -> dict:
    record = {
        "item_id": item_id,
        "name": name,
        "sku": sku,
        "status": "active",
        "unit": "pcs",
        "purchase_rate": 0.0,
        "rate": 0.0,
        "item_type": "inventory",
    }
    record.update(overrides)
    return record


def existing_purchase_orders() -> list[dict]:
    """The six real FRP Depot purchase orders, all JRAIN, read on 2026-08-13."""
    return [
        {"purchaseorder_id": "96274000001043001", "purchaseorder_number": "PO-00006",
         "status": "billed", "vendor_id": VENDOR_ID, "reference_number": "", "date": "2026-05-24"},
        {"purchaseorder_id": "96274000000900001", "purchaseorder_number": "PO-00005",
         "status": "billed", "vendor_id": VENDOR_ID, "reference_number": "", "date": "2026-04-01"},
        {"purchaseorder_id": "96274000000800001", "purchaseorder_number": "PO-00004",
         "status": "billed", "vendor_id": VENDOR_ID, "reference_number": "", "date": "2026-03-01"},
        {"purchaseorder_id": "96274000000700001", "purchaseorder_number": "PO-00003",
         "status": "billed", "vendor_id": VENDOR_ID, "reference_number": "", "date": "2026-02-01"},
        {"purchaseorder_id": "96274000000600001", "purchaseorder_number": "PO-00001-R2",
         "status": "billed", "vendor_id": VENDOR_ID, "reference_number": "TDI PO#5046",
         "date": "2025-01-29"},
        {"purchaseorder_id": "96274000000500001", "purchaseorder_number": "PO-00002-R1",
         "status": "partially_billed", "vendor_id": VENDOR_ID,
         "reference_number": "TDI PO#5011", "date": "2025-12-09"},
    ]


def jrain_input(reference: str = "J26-403", tax_id: str = ZERO_RATE_TAX) -> dict:
    lines = []
    for item_id, name, sku, quantity, rate, _total in JRAIN_LINES:
        line = {
            "item_id": {"value": item_id, "source": f"Live Zoho item {sku}."},
            "quantity": {"value": quantity, "source": SOURCE},
            "rate": {"value": rate, "source": "Supplier USD unit cost from the attached workbook."},
        }
        if tax_id:
            line["tax_id"] = {"value": tax_id, "source": "Live active Zoho tax."}
        lines.append(line)
    payload = {
        "purpose": "Order the four D441 CCMMMM items GRP Jrain quoted for TDI job J26-403.",
        "vendor": {
            "vendor_id": VENDOR_ID,
            "vendor_name": VENDOR_NAME,
            "source": "Live active Zoho vendor JRAIN FRP LIMITED, currency USD.",
        },
        "date": {"value": "2026-08-13", "source": "Rachad's instruction on 2026-08-13."},
        "delivery_date": {"value": "2026-09-15", "source": "Air freight estimate in the workbook."},
        "notes": {
            "value": "All four items must use Derakane 441 and CCMMMM liner.",
            "source": "Rachad's direct correction of the workbook liner value.",
        },
        "contact_persons": {
            "value": [PERSON_B],
            "source": "Fei Huiling is the live vendor contact person for this order.",
        },
        "line_items": lines,
    }
    if reference:
        payload["reference_number"] = {
            "value": reference, "source": "TDI job number on the supplier quote."
        }
    return payload


def created_purchase_order(payload: dict, evidence: dict, **overrides) -> dict:
    """What Zoho should return for the created Draft."""
    lines = []
    for index, row in enumerate(evidence["lines"]):
        line = {
            "line_item_id": str(96274000001700000 + index),
            "item_id": row["item_id"],
            "name": row["name"],
            "sku": row["sku"],
            "description": row["description"],
            "quantity": float(Decimal(row["quantity"])),
            "rate": float(Decimal(row["rate"])),
            "unit": row["unit"],
            "item_order": index + 1,
            "item_total": float(Decimal(row["item_total"])),
        }
        if row["tax_id"]:
            line["tax_id"] = row["tax_id"]
        lines.append(line)
    totals = evidence["totals"]
    order = {
        "purchaseorder_id": "96274000001700999",
        "purchaseorder_number": "PO-00007",
        "status": "draft",
        "vendor_id": VENDOR_ID,
        "vendor_name": VENDOR_NAME,
        "currency_code": "USD",
        "currency_id": "96274000000000081",
        "exchange_rate": 1.3930,
        "is_emailed": False,
        "billed_status": "unbilled",
        "received_status": "pending",
        "order_status": "draft",
        "submitted_by": "",
        "bills": [],
        "approvers_list": [],
        "sub_total": float(Decimal(totals["sub_total"])),
        "tax_total": float(Decimal(totals["tax_total"])),
        "total": float(Decimal(totals["total"])),
        "line_items": lines,
    }
    for field in ("date", "delivery_date", "reference_number", "ship_via", "notes", "terms"):
        if field in payload:
            order[field] = payload[field]
    if "contact_persons" in payload:
        order["contact_persons"] = list(payload["contact_persons"])
    order.update(overrides)
    return order


def fake_vault(scopes=None) -> dict:
    return {
        "api_domain": tool.EXPECTED_API_DOMAIN,
        "books_organization_id": ORG_ID,
        "scopes": list(tool.SCOPES) if scopes is None else list(scopes),
    }


class PurchaseOrderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.plan_dir = self.root / "zoho_purchase_order_plans"
        self.plan_dir.mkdir(parents=True)
        self.addCleanup(self._temp.cleanup)

    def write_input(self, payload: dict, name: str = "po_input.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _reader(
        self,
        vendor: dict | None = None,
        items: dict | None = None,
        taxes: list | None = None,
        pages: list | None = None,
        created: dict | None = None,
    ):
        vendor = vendor if vendor is not None else vendor_record()
        if items is None:
            items = {
                item_id: item_record(item_id, name, sku)
                for item_id, name, sku, _q, _r, _t in JRAIN_LINES
            }
        tax_rows = ACTIVE_TAXES if taxes is None else taxes
        if pages is None:
            pages = [
                {"purchaseorders": existing_purchase_orders(),
                 "page_context": {"page": 1, "per_page": 200, "has_more_page": False}}
            ]
        calls = {"vendor": 0, "items": 0, "taxes": 0, "po_list": 0, "po_get": 0, "paths": []}
        page_cursor = {"index": 0}

        def fake_api_get(access_token, api_domain, path):
            base = path.split("?", 1)[0]
            calls["paths"].append(base)
            if base.startswith("/books/v3/contacts/"):
                calls["vendor"] += 1
                return {"code": 0, "contact": copy.deepcopy(vendor)}
            if base.startswith("/books/v3/items/"):
                calls["items"] += 1
                item_id = base.rsplit("/", 1)[1]
                if item_id not in items:
                    raise AssertionError(f"unexpected item GET {item_id}")
                return {"code": 0, "item": copy.deepcopy(items[item_id])}
            if base == "/books/v3/settings/taxes":
                calls["taxes"] += 1
                return {"code": 0, "taxes": copy.deepcopy(tax_rows)}
            if base == "/books/v3/purchaseorders":
                calls["po_list"] += 1
                index = min(page_cursor["index"], len(pages) - 1)
                page_cursor["index"] += 1
                return dict({"code": 0}, **copy.deepcopy(pages[index]))
            if base.startswith("/books/v3/purchaseorders/"):
                calls["po_get"] += 1
                if created is None:
                    raise AssertionError("unexpected purchase order GET")
                return {"code": 0, "purchaseorder": copy.deepcopy(created)}
            raise AssertionError(f"unexpected GET {base}")

        return fake_api_get, calls

    def stage(self, payload: dict | None = None, scopes=None, **reader) -> Path:
        payload = jrain_input() if payload is None else payload
        input_path = self.write_input(payload)
        reader_fn, self.stage_calls = self._reader(**reader)
        vault = fake_vault(scopes)
        existing = set(self.plan_dir.glob("*.json"))
        with patch.object(po, "PLAN_DIR", self.plan_dir), patch.object(
            po.zoho_tool, "append_receipt"
        ), patch.object(
            po.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            po.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(po.zoho_tool, "save_vault"), patch.object(
            po.zoho_tool, "api_get", side_effect=reader_fn
        ), patch.object(
            po, "urlopen", side_effect=AssertionError("staging must never write")
        ):
            po.command_stage_create(argparse.Namespace(input=str(input_path)))
        made = set(self.plan_dir.glob("*.json")) - existing
        self.assertEqual(len(made), 1)
        return made.pop()

    def stage_expecting_error(self, **kwargs) -> Exception:
        with self.assertRaises(po.PurchaseOrderToolError) as caught:
            self.stage(**kwargs)
        return caught.exception

    def plan_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def rehash(self, path: Path, plan: dict) -> Path:
        core = dict(plan)
        core.pop("sha256", None)
        plan["sha256"] = po.digest_for(core)
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def commit(
        self,
        plan_path: Path,
        *,
        approval: str = po.APPROVAL_WORD,
        created: dict | None = None,
        post_result=None,
        post_error: Exception | None = None,
        scopes=None,
        organization_id: str = ORG_ID,
        **reader,
    ) -> dict:
        plan = self.plan_json(plan_path)
        evidence = plan["live_evidence"]
        if created is None:
            created = created_purchase_order(evidence["post_payload"], evidence)
        reader_fn, get_calls = self._reader(created=created, **reader)
        vault = fake_vault(scopes)
        vault["books_organization_id"] = organization_id
        calls: dict = {"posts": [], "gets": get_calls}
        self.last_calls = calls

        def fake_urlopen(request, timeout):
            calls["posts"].append({
                "method": request.get_method(),
                "url": request.full_url,
                "payload": json.loads(request.data.decode("utf-8")),
                "lock_exists": self.lock_exists(plan_path),
            })
            if post_error is not None:
                raise post_error
            return FakeResponse(
                post_result if post_result is not None
                else {"code": 0, "purchaseorder": created}
            )

        with patch.object(po, "PLAN_DIR", self.plan_dir), patch.object(
            po.zoho_tool, "append_receipt"
        ), patch.object(
            po.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            po.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(po.zoho_tool, "save_vault"), patch.object(
            po.zoho_tool, "api_get", side_effect=reader_fn
        ), patch.object(po, "urlopen", side_effect=fake_urlopen):
            # His go is timed one minute AFTER the plan was written (A3/A5:
            # --approval-message-utc is required on every money commit).
            po.command_commit(argparse.Namespace(
                plan=str(plan_path), approval=approval,
                approval_message_utc=(datetime.fromisoformat(plan["created_utc"])
                                      + timedelta(minutes=1)).isoformat(),
            ))
        return calls

    def commit_expecting_error(self, plan_path: Path, **kwargs) -> Exception:
        with self.assertRaises(po.PurchaseOrderToolError) as caught:
            self.commit(plan_path, **kwargs)
        return caught.exception

    def lock_exists(self, plan_path: Path) -> bool:
        with patch.object(po, "PLAN_DIR", self.plan_dir):
            return po.lock_path(self.plan_json(plan_path)["sha256"]).exists()

    def lock_record(self, plan_path: Path) -> dict:
        with patch.object(po, "PLAN_DIR", self.plan_dir):
            lock = po.lock_path(self.plan_json(plan_path)["sha256"])
        return json.loads(lock.read_text(encoding="utf-8")) if lock.exists() else {}


# ---------------------------------------------------------------------------
# The realistic GRP Jrain four-line fixture
# ---------------------------------------------------------------------------


class JrainFixtureTests(PurchaseOrderTestCase):
    def test_the_four_line_order_prices_exactly(self) -> None:
        evidence = self.plan_json(self.stage())["live_evidence"]
        self.assertEqual(
            [line["item_total"] for line in evidence["lines"]],
            [total for *_rest, total in JRAIN_LINES],
        )
        totals = evidence["totals"]
        self.assertEqual(totals["sub_total"], "3062.00")
        self.assertEqual(totals["tax_total"], "0.00")
        self.assertEqual(totals["total"], "3062.00")
        self.assertEqual(totals["tax_certainty"], "exact")
        self.assertTrue(totals["tax_total_asserted"])

    def test_the_vendor_currency_is_preserved_and_never_set(self) -> None:
        evidence = self.plan_json(self.stage())["live_evidence"]
        self.assertEqual(evidence["vendor_currency_preserved"], "USD")
        for forbidden in ("currency_id", "currency_code", "exchange_rate"):
            self.assertNotIn(forbidden, evidence["post_payload"])

    def test_the_post_body_is_exactly_the_commissioned_shape(self) -> None:
        payload = self.plan_json(self.stage())["live_evidence"]["post_payload"]
        self.assertEqual(
            set(payload),
            {"vendor_id", "date", "delivery_date", "reference_number", "notes",
             "contact_persons", "line_items"},
        )
        self.assertEqual(payload["vendor_id"], VENDOR_ID)
        self.assertEqual(payload["reference_number"], "J26-403")
        self.assertEqual(payload["delivery_date"], "2026-09-15")
        self.assertEqual(payload["contact_persons"], [PERSON_B])
        self.assertEqual(len(payload["line_items"]), 4)
        self.assertEqual(
            set(payload["line_items"][0]),
            {"item_id", "name", "quantity", "rate", "unit", "tax_id"},
        )

    def test_staging_reads_only_and_never_writes(self) -> None:
        self.stage()
        self.assertEqual(self.stage_calls["vendor"], 1)
        self.assertEqual(self.stage_calls["items"], 4)
        self.assertEqual(self.stage_calls["taxes"], 1)
        self.assertEqual(self.stage_calls["po_list"], 1)
        self.assertEqual(self.stage_calls["po_get"], 0)

    def test_the_whole_commit_flow_is_one_verified_draft(self) -> None:
        path = self.stage()
        calls = self.commit(path)
        self.assertEqual(len(calls["posts"]), 1)
        self.assertEqual(calls["posts"][0]["method"], "POST")
        self.assertTrue(
            calls["posts"][0]["url"].endswith(
                f"/books/v3/purchaseorders?organization_id={ORG_ID}"
            )
        )
        record = self.lock_record(path)
        self.assertEqual(record["status"], "committed_verified")
        self.assertEqual(record["purchaseorder_number"], "PO-00007")

    def test_the_plan_discloses_that_it_is_not_reversible(self) -> None:
        plan = self.plan_json(self.stage())
        self.assertFalse(plan["risk"]["reversible"])
        self.assertIn("NOT REVERSIBLE FROM HERE", plan["risk"]["note"])
        self.assertIn("UNPROVEN", plan["live_evidence"]["unproven_field_note"])

    def test_every_business_value_carries_its_source(self) -> None:
        sources = self.plan_json(self.stage())["live_evidence"]["sources"]
        self.assertEqual(sources["vendor"], "Live active Zoho vendor JRAIN FRP LIMITED, currency USD.")
        for field in ("date", "delivery_date", "reference_number", "notes", "contact_persons"):
            self.assertTrue(sources[field].strip())
        self.assertEqual(len(sources["line_items"]), 4)
        for line in sources["line_items"]:
            for field in ("item_id", "quantity", "rate", "tax_id"):
                self.assertTrue(line[field].strip())


# ---------------------------------------------------------------------------
# Closed input schema
# ---------------------------------------------------------------------------


class InputSchemaTests(PurchaseOrderTestCase):
    def test_two_commands_and_no_third(self) -> None:
        choices = set(po.build_parser()._subparsers._group_actions[0].choices)
        self.assertEqual(choices, {"stage-create", "commit"})

    def test_the_caller_cannot_supply_a_purchase_order_number(self) -> None:
        payload = jrain_input()
        payload["purchaseorder_number"] = "PO-99999"
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("auto-numbering", str(error))

    def test_status_currency_and_exchange_rate_are_refused_by_name(self) -> None:
        for field, fragment in (
            ("status", "cannot set or change a status"),
            ("currency_id", "never set"),
            ("currency_code", "never set"),
            ("exchange_rate", "never set"),
            ("expected_delivery_date", "delivery_date is the proven key"),
            ("attachment", "cannot attach"),
            ("custom_fields", "outside this commission"),
        ):
            with self.subTest(field=field):
                payload = jrain_input()
                payload[field] = "x"
                self.assertIn(fragment, str(self.stage_expecting_error(payload=payload)))

    def test_an_unknown_top_level_field_is_refused(self) -> None:
        payload = jrain_input()
        payload["payload"] = {}
        self.assertIn("uncommissioned field(s): payload",
                      str(self.stage_expecting_error(payload=payload)))

    def test_a_line_without_an_item_id_is_refused(self) -> None:
        payload = jrain_input()
        payload["line_items"][0].pop("item_id")
        self.assertIn("missing item_id", str(self.stage_expecting_error(payload=payload)))

    def test_a_free_text_line_name_is_not_a_field_at_all(self) -> None:
        payload = jrain_input()
        payload["line_items"][0]["name"] = {"value": "Anything", "source": "x"}
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("uncommissioned field(s): name", str(error))

    def test_every_value_needs_a_nonblank_source(self) -> None:
        payload = jrain_input()
        payload["line_items"][0]["quantity"] = {"value": "21", "source": "  "}
        self.assertIn("nonblank explicit source",
                      str(self.stage_expecting_error(payload=payload)))

    def test_dates_must_be_exact_calendar_dates(self) -> None:
        for bad in ("2026-8-13", "13/08/2026", "2026-13-01", "next week"):
            with self.subTest(bad=bad):
                payload = jrain_input()
                payload["date"] = {"value": bad, "source": "Rachad"}
                self.assertIn("YYYY-MM-DD", str(self.stage_expecting_error(payload=payload)))

    def test_a_delivery_date_before_the_order_date_is_refused(self) -> None:
        payload = jrain_input()
        payload["delivery_date"] = {"value": "2026-08-01", "source": "typo"}
        self.assertIn("earlier than", str(self.stage_expecting_error(payload=payload)))

    def test_quantity_and_rate_bounds(self) -> None:
        for field, bad in (
            ("quantity", "0"), ("quantity", "-1"), ("quantity", "2000000"),
            ("rate", "-0.01"), ("rate", "20000000"), ("rate", "1.1234567"),
        ):
            with self.subTest(field=field, bad=bad):
                payload = jrain_input()
                payload["line_items"][0][field] = {"value": bad, "source": "x"}
                self.assertIn("REFUSED", str(self.stage_expecting_error(payload=payload)))

    def test_purpose_is_required(self) -> None:
        payload = jrain_input()
        payload.pop("purpose")
        self.assertIn("missing purpose", str(self.stage_expecting_error(payload=payload)))


# ---------------------------------------------------------------------------
# Vendor and item preflight
# ---------------------------------------------------------------------------


class PreflightTests(PurchaseOrderTestCase):
    def test_a_customer_record_is_never_a_vendor_by_inference(self) -> None:
        error = self.stage_expecting_error(vendor=vendor_record(contact_type="customer"))
        self.assertIn("not a vendor", str(error))
        self.assertIn("never treated as a vendor by inference", str(error))

    def test_an_inactive_vendor_is_refused(self) -> None:
        error = self.stage_expecting_error(vendor=vendor_record(status="inactive"))
        self.assertIn("not active", str(error))

    def test_a_vendor_whose_name_does_not_match_is_refused(self) -> None:
        error = self.stage_expecting_error(
            vendor=vendor_record(contact_name="Someone Else", company_name="Someone Else")
        )
        self.assertIn("not the stated", str(error))

    def test_a_vendor_without_a_currency_is_refused(self) -> None:
        error = self.stage_expecting_error(vendor=vendor_record(currency_code=""))
        self.assertIn("no live currency", str(error))

    def test_an_inactive_item_is_refused(self) -> None:
        items = {
            item_id: item_record(item_id, name, sku)
            for item_id, name, sku, _q, _r, _t in JRAIN_LINES
        }
        items[JRAIN_LINES[1][0]]["status"] = "inactive"
        error = self.stage_expecting_error(items=items)
        self.assertIn("not active", str(error))

    def test_a_contact_person_not_owned_by_the_vendor_is_refused(self) -> None:
        payload = jrain_input()
        payload["contact_persons"] = {"value": ["96274000009999999"], "source": "guessed"}
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("not owned by vendor", str(error))

    def test_an_unknown_tax_is_refused_and_never_created(self) -> None:
        payload = jrain_input(tax_id="96274000009999999")
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("not an active tax", str(error))
        self.assertIn("cannot create a tax", str(error))

    def test_an_inactive_tax_is_refused(self) -> None:
        taxes = copy.deepcopy(ACTIVE_TAXES)
        taxes[0]["status"] = "Inactive"
        taxes[0]["is_inactive"] = True
        self.assertIn("not active", str(self.stage_expecting_error(taxes=taxes)))

    def test_a_tax_group_makes_the_prediction_non_exact(self) -> None:
        evidence = self.plan_json(
            self.stage(payload=jrain_input(tax_id=GST_QST_GROUP))
        )["live_evidence"]
        totals = evidence["totals"]
        self.assertEqual(totals["tax_certainty"], "disclosed_uncertain")
        self.assertFalse(totals["tax_total_asserted"])
        self.assertTrue(any("tax_group" in reason for reason in totals["tax_uncertainty_reasons"]))
        self.assertEqual(totals["sub_total"], "3062.00")

    def test_a_real_tax_percentage_is_computed_half_up(self) -> None:
        totals = self.plan_json(
            self.stage(payload=jrain_input(tax_id=GST_TAX))
        )["live_evidence"]["totals"]
        self.assertEqual(totals["tax_total"], "153.10")
        self.assertEqual(totals["total"], "3215.10")


# ---------------------------------------------------------------------------
# Duplicate preflight
# ---------------------------------------------------------------------------


class DuplicatePreflightTests(PurchaseOrderTestCase):
    def test_a_matching_reference_for_the_same_vendor_is_refused(self) -> None:
        rows = existing_purchase_orders()
        rows[0]["reference_number"] = "J26-403"
        pages = [{"purchaseorders": rows,
                  "page_context": {"page": 1, "per_page": 200, "has_more_page": False}}]
        error = self.stage_expecting_error(pages=pages)
        self.assertIn("already has a live purchase order", str(error))
        self.assertIn("PO-00006", str(error))

    def test_the_reference_match_is_case_and_whitespace_insensitive(self) -> None:
        rows = existing_purchase_orders()
        rows[0]["reference_number"] = "  j26-403 "
        pages = [{"purchaseorders": rows,
                  "page_context": {"page": 1, "per_page": 200, "has_more_page": False}}]
        self.assertIn("already has a live purchase order", str(self.stage_expecting_error(pages=pages)))

    def test_punctuation_is_not_stripped_so_close_references_do_not_collide(self) -> None:
        rows = existing_purchase_orders()
        rows[0]["reference_number"] = "J26403"
        pages = [{"purchaseorders": rows,
                  "page_context": {"page": 1, "per_page": 200, "has_more_page": False}}]
        scan = self.plan_json(self.stage(pages=pages))["live_evidence"]["duplicate_preflight"]
        self.assertEqual(scan["duplicate_match_count"], 0)

    def test_a_voided_duplicate_does_not_block(self) -> None:
        rows = existing_purchase_orders()
        rows[0]["reference_number"] = "J26-403"
        rows[0]["status"] = "cancelled"
        pages = [{"purchaseorders": rows,
                  "page_context": {"page": 1, "per_page": 200, "has_more_page": False}}]
        scan = self.plan_json(self.stage(pages=pages))["live_evidence"]["duplicate_preflight"]
        self.assertEqual(scan["duplicate_match_count"], 0)

    def test_a_matching_reference_for_a_different_vendor_does_not_block(self) -> None:
        rows = existing_purchase_orders()
        rows[0]["reference_number"] = "J26-403"
        rows[0]["vendor_id"] = "96274000000099999"
        pages = [{"purchaseorders": rows,
                  "page_context": {"page": 1, "per_page": 200, "has_more_page": False}}]
        scan = self.plan_json(self.stage(pages=pages))["live_evidence"]["duplicate_preflight"]
        self.assertEqual(scan["duplicate_match_count"], 0)
        self.assertEqual(scan["vendor_purchase_orders"], 5)

    def test_a_matching_total_alone_is_never_a_duplicate(self) -> None:
        rows = existing_purchase_orders()
        rows[0]["total"] = 3062.0
        rows[0]["reference_number"] = ""
        pages = [{"purchaseorders": rows,
                  "page_context": {"page": 1, "per_page": 200, "has_more_page": False}}]
        scan = self.plan_json(self.stage(pages=pages))["live_evidence"]["duplicate_preflight"]
        self.assertEqual(scan["duplicate_match_count"], 0)
        self.assertTrue(scan["totals_are_not_used_for_duplicate_detection"])

    def test_pagination_walks_every_page(self) -> None:
        first = existing_purchase_orders()
        second = [dict(row, purchaseorder_id=row["purchaseorder_id"][:-1] + "9",
                       purchaseorder_number=row["purchaseorder_number"] + "-B")
                  for row in first]
        pages = [
            {"purchaseorders": first,
             "page_context": {"page": 1, "per_page": 200, "has_more_page": True}},
            {"purchaseorders": second,
             "page_context": {"page": 2, "per_page": 200, "has_more_page": False}},
        ]
        scan = self.plan_json(self.stage(pages=pages))["live_evidence"]["duplicate_preflight"]
        self.assertEqual(scan["pages"], 2)
        self.assertEqual(scan["enumerated"], 12)
        self.assertTrue(scan["complete"])

    def test_a_missing_has_more_page_refuses_rather_than_reporting_clean(self) -> None:
        pages = [{"purchaseorders": existing_purchase_orders(),
                  "page_context": {"page": 1, "per_page": 200}}]
        self.assertIn("has_more_page", str(self.stage_expecting_error(pages=pages)))

    def test_a_missing_page_context_refuses(self) -> None:
        pages = [{"purchaseorders": existing_purchase_orders()}]
        self.assertIn("page context", str(self.stage_expecting_error(pages=pages)))

    def test_a_wrong_page_number_refuses(self) -> None:
        pages = [{"purchaseorders": existing_purchase_orders(),
                  "page_context": {"page": 7, "per_page": 200, "has_more_page": False}}]
        self.assertIn("cannot be proven complete", str(self.stage_expecting_error(pages=pages)))

    def test_an_unreadable_list_refuses(self) -> None:
        pages = [{"purchaseorders": "everything is fine",
                  "page_context": {"page": 1, "per_page": 200, "has_more_page": False}}]
        self.assertIn("no readable purchase-order list",
                      str(self.stage_expecting_error(pages=pages)))

    def test_the_page_ceiling_is_a_refusal_not_a_partial_scan(self) -> None:
        page = {"purchaseorders": existing_purchase_orders(),
                "page_context": {"page": 1, "per_page": 200, "has_more_page": True}}
        pages = [dict(page, page_context=dict(page["page_context"], page=n))
                 for n in range(1, po.PO_MAX_PAGES + 3)]
        self.assertIn("page ceiling", str(self.stage_expecting_error(pages=pages)))

    def test_no_reference_number_is_disclosed_not_silently_skipped(self) -> None:
        scan = self.plan_json(
            self.stage(payload=jrain_input(reference=""))
        )["live_evidence"]["duplicate_preflight"]
        self.assertFalse(scan["reference_supplied"])
        self.assertEqual(scan["duplicate_match_count"], 0)

    def test_the_duplicate_walk_is_repeated_fresh_before_the_write(self) -> None:
        path = self.stage()
        rows = existing_purchase_orders()
        rows[0]["reference_number"] = "J26-403"
        pages = [{"purchaseorders": rows,
                  "page_context": {"page": 1, "per_page": 200, "has_more_page": False}}]
        error = self.commit_expecting_error(path, pages=pages)
        self.assertIn("BEFORE the replay lock", str(error))
        self.assertFalse(self.lock_exists(path))
        self.assertEqual(self.last_calls["posts"], [])


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------


class ScopeTests(PurchaseOrderTestCase):
    def test_only_the_create_scope_is_added(self) -> None:
        self.assertIn(po.PURCHASE_ORDER_CREATE_SCOPE, tool.SCOPES)
        self.assertEqual(po.PURCHASE_ORDER_CREATE_SCOPE, "ZohoBooks.purchaseorders.CREATE")
        for widened in po.FORBIDDEN_PURCHASE_ORDER_SCOPES:
            with self.subTest(scope=widened):
                self.assertNotIn(widened, tool.SCOPES)

    def test_no_send_status_or_payment_scope_exists_anywhere(self) -> None:
        for forbidden in (
            "ZohoBooks.purchaseorders.DELETE",
            "ZohoBooks.purchaseorders.ALL", "ZohoInventory.purchaseorders.CREATE",
            "ZohoBooks.vendorpayments.CREATE", "ZohoBooks.bills.CREATE",
            "ZohoInventory.purchasereceives.CREATE", "ZohoBooks.fullaccess.all",
        ):
            with self.subTest(scope=forbidden):
                self.assertNotIn(forbidden, tool.SCOPES)

    def test_this_tool_cannot_update_even_while_the_connection_holds_update(self) -> None:
        """The 2026-08-21 J26-403 commission put UPDATE on the shared connection.

        This tool no longer refuses to RUN because of it -- that would have
        killed the draft-PO capability outright. What it still cannot do is
        write anything but a create, and that containment is its own route and
        verb allowlist, not the connection's scope list.
        """
        self.assertIn("ZohoBooks.purchaseorders.UPDATE", tool.SCOPES)
        self.assertNotIn("ZohoBooks.purchaseorders.UPDATE", po.FORBIDDEN_PURCHASE_ORDER_SCOPES)
        self.assertEqual(po.ALLOWED_METHODS, ("GET", "POST"))
        for verb in ("PUT", "PATCH", "DELETE"):
            with self.subTest(verb=verb):
                with self.assertRaises(po.PurchaseOrderToolError) as caught:
                    po.require_create_allowed(verb, po.CREATE_PATH, "1", {}, {})
                self.assertIn("POST", str(caught.exception))
        source = Path(po.__file__).read_text(encoding="utf-8")
        for verb in ('method="PUT"', 'method="PATCH"', 'method="DELETE"'):
            with self.subTest(transport=verb):
                self.assertNotIn(verb, source)

    def test_staging_refuses_when_the_saved_connection_lacks_create(self) -> None:
        scopes = [s for s in tool.SCOPES if s != po.PURCHASE_ORDER_CREATE_SCOPE]
        error = self.stage_expecting_error(scopes=scopes)
        self.assertIn(po.PURCHASE_ORDER_CREATE_SCOPE, str(error))
        self.assertIn("PREPARE_DADO_ZOHO_ACCESS.bat", str(error))
        self.assertIn("REAUTHORIZE_DADO_ZOHO.bat", str(error))

    def test_commit_refuses_before_the_lock_when_create_is_missing(self) -> None:
        path = self.stage()
        scopes = [s for s in tool.SCOPES if s != po.PURCHASE_ORDER_CREATE_SCOPE]
        error = self.commit_expecting_error(path, scopes=scopes)
        self.assertIn(po.PURCHASE_ORDER_CREATE_SCOPE, str(error))
        self.assertFalse(self.lock_exists(path))
        self.assertEqual(self.last_calls["posts"], [])

    def test_a_widened_saved_scope_refuses_the_whole_tool(self) -> None:
        scopes = list(tool.SCOPES) + ["ZohoBooks.purchaseorders.DELETE"]
        with self.assertRaises(Exception) as caught:
            self.stage(scopes=scopes)
        self.assertIn("ZohoBooks.purchaseorders.DELETE", str(caught.exception))


# ---------------------------------------------------------------------------
# Approval, plan integrity and tampering
# ---------------------------------------------------------------------------


class ApprovalAndPlanTests(PurchaseOrderTestCase):
    def test_a_conditional_or_blank_word_never_commits(self) -> None:
        """2026-08-21 (A3): exact APPROVED is no longer REQUIRED -- his unambiguous
        go to THIS plan counts -- but a condition, a question or a blank still
        refuses before the lock, the vault and the network."""
        for approval in ("approved but wait", "hold on", "APPROVED?", "", "not yet"):
            with self.subTest(approval=approval):
                path = self.stage()
                error = self.commit_expecting_error(path, approval=approval)
                self.assertIn("unambiguous go" if approval else "no approval text", str(error))
                self.assertFalse(self.lock_exists(path))
                self.assertEqual(self.last_calls["posts"], [])

    def test_yes_go_ahead_after_the_plan_commits_and_a_word_before_the_plan_does_not(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        created = datetime.fromisoformat(plan["created_utc"])
        before = (created - timedelta(minutes=1)).isoformat()
        with patch.object(po, "PLAN_DIR", self.plan_dir), patch.object(
            po.zoho_tool, "load_vault", side_effect=AssertionError("vault must not be opened")
        ):
            with self.assertRaises(po.PurchaseOrderToolError) as caught:
                po.command_commit(argparse.Namespace(
                    plan=str(path), approval="yes go ahead", approval_message_utc=before,
                ))
        self.assertIn("BEFORE this plan was created", str(caught.exception))
        self.assertFalse(self.lock_exists(path))
        # A go with NO time cannot be shown to have come after the plan: refused,
        # and the refusal names the flag and the plan's creation time.
        with patch.object(po, "PLAN_DIR", self.plan_dir), patch.object(
            po.zoho_tool, "load_vault", side_effect=AssertionError("vault must not be opened")
        ):
            with self.assertRaises(po.PurchaseOrderToolError) as caught:
                po.command_commit(argparse.Namespace(plan=str(path), approval="yes go ahead"))
        self.assertIn("--approval-message-utc", str(caught.exception))
        self.assertIn(created.isoformat(), str(caught.exception))
        self.assertFalse(self.lock_exists(path))
        after = (created + timedelta(minutes=1)).isoformat()
        go = po.require_exact_approval("yes go ahead", plan, sent_utc=after)
        self.assertEqual((go.kind, go.sent_utc, go.exact_word), ("money", after, False))
        result = self.commit(path, approval="yes go ahead")
        self.assertEqual([call["method"] for call in result["posts"]], ["POST"])
        record = self.lock_record(path)
        self.assertEqual(record["status"], "committed_verified")
        self.assertEqual(record["owner_go"], "yes go ahead")
        self.assertEqual(record["owner_go_sent_utc"], after)
        self.assertFalse(record["permanent_lock"])

    def test_approval_is_checked_before_the_vault_and_the_network(self) -> None:
        path = self.stage()
        with patch.object(po, "PLAN_DIR", self.plan_dir), patch.object(
            po.zoho_tool, "load_vault", side_effect=AssertionError("vault must not be opened")
        ), patch.object(po, "urlopen", side_effect=AssertionError("no network")):
            with self.assertRaises(po.PurchaseOrderToolError):
                po.command_commit(argparse.Namespace(plan=str(path), approval="approved but wait"))

    def test_editing_the_plan_breaks_its_hash(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["post_payload"]["line_items"][0]["quantity"] = 2100
        path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        self.assertIn("Plan hash check failed", str(self.commit_expecting_error(path)))

    def test_a_resigned_quantity_cannot_outvote_the_projection(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["intent"]["line_items"][0]["quantity"]["value"] = "2100"
        self.rehash(path, plan)
        self.assertIn("canonical projection", str(self.commit_expecting_error(path)))

    def test_a_resigned_total_cannot_outvote_the_projection(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["totals"]["total"] = "1.00"
        self.rehash(path, plan)
        self.assertIn("canonical projection", str(self.commit_expecting_error(path)))

    def test_a_resigned_endpoint_cannot_be_redirected(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["post_endpoint"] = "POST /books/v3/bills"
        self.rehash(path, plan)
        self.assertIn("canonical projection", str(self.commit_expecting_error(path)))

    def test_a_resigned_duplicate_scan_cannot_hide_a_match(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["duplicate_preflight"]["complete"] = False
        self.rehash(path, plan)
        self.assertIn("duplicate check is not complete", str(self.commit_expecting_error(path)))

    def test_a_resigned_vendor_type_cannot_smuggle_in_a_customer(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["vendor"]["contact_type"] = "customer"
        self.rehash(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("never treated as a vendor by inference", str(error))
        self.assertFalse(self.lock_exists(path))

    def test_a_resigned_inactive_item_is_refused_by_the_projection(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["items"][JRAIN_LINES[0][0]]["status"] = "inactive"
        self.rehash(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("is not active", str(error))
        self.assertFalse(self.lock_exists(path))

    def test_a_resigned_vendor_evidence_schema_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["vendor"]["extra"] = "smuggled"
        self.rehash(path, plan)
        self.assertIn("exact closed schema", str(self.commit_expecting_error(path)))

    def test_a_resigned_risk_note_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["risk"]["reversible"] = True
        self.rehash(path, plan)
        self.assertIn("not-reversible risk", str(self.commit_expecting_error(path)))

    def test_a_plan_from_a_different_build_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["tool_version"] = "0.9.0"
        self.rehash(path, plan)
        self.assertIn("different tool, action, build or schema version",
                      str(self.commit_expecting_error(path)))

    def test_an_expired_plan_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["created_utc"] = "2026-08-01T00:00:00+00:00"
        plan["expires_utc"] = "2026-08-02T00:00:00+00:00"
        self.rehash(path, plan)
        self.assertIn("expired", str(self.commit_expecting_error(path)))

    def test_a_lifetime_other_than_24_hours_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["expires_utc"] = "2126-08-02T00:00:00+00:00"
        self.rehash(path, plan)
        self.assertIn("24-hour lifetime", str(self.commit_expecting_error(path)))

    def test_a_plan_outside_the_plan_folder_is_refused(self) -> None:
        path = self.stage()
        outside = self.root / "elsewhere.json"
        outside.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        with patch.object(po, "PLAN_DIR", self.plan_dir):
            with self.assertRaises(po.PurchaseOrderToolError) as caught:
                po.command_commit(
                    argparse.Namespace(plan=str(outside), approval=po.APPROVAL_WORD)
                )
        self.assertIn("plan folder", str(caught.exception))

    def test_a_relative_plan_path_is_refused(self) -> None:
        with patch.object(po, "PLAN_DIR", self.plan_dir):
            with self.assertRaises(po.PurchaseOrderToolError):
                po.command_commit(argparse.Namespace(plan="p.json", approval=po.APPROVAL_WORD))


# ---------------------------------------------------------------------------
# Write allowlist, lock timing and the single attempt
# ---------------------------------------------------------------------------


class WriteContainmentTests(PurchaseOrderTestCase):
    def payload_of(self, path: Path) -> dict:
        return self.plan_json(path)["live_evidence"]["post_payload"]

    def test_only_post_is_accepted(self) -> None:
        payload = self.payload_of(self.stage())
        for method in ("GET", "PUT", "PATCH", "DELETE", "post", ""):
            with self.subTest(method=method):
                with self.assertRaises(po.PurchaseOrderToolError) as caught:
                    po.require_create_allowed(method, po.CREATE_PATH, ORG_ID, payload, payload)
                self.assertIn("POST and nothing else", str(caught.exception))

    def test_only_the_create_route_is_accepted(self) -> None:
        payload = self.payload_of(self.stage())
        for route in (
            "/books/v3/purchaseorders/96274000001043001",
            "/books/v3/purchaseorders/96274000001043001/status/open",
            "/books/v3/purchaseorders/96274000001043001/email",
            "/books/v3/purchaseorders/96274000001043001/submit",
            "/books/v3/purchaseorders/96274000001043001/approve",
            "/books/v3/bills",
            "/books/v3/purchaseorders?organization_id=1",
        ):
            with self.subTest(route=route):
                with self.assertRaises(po.PurchaseOrderToolError) as caught:
                    po.require_create_allowed("POST", route, ORG_ID, payload, payload)
                self.assertIn("only write route", str(caught.exception))

    def test_an_extra_payload_key_is_refused_at_the_transport_gate(self) -> None:
        payload = self.payload_of(self.stage())
        for key, value in (
            ("status", "open"), ("purchaseorder_number", "PO-9"), ("currency_id", "1"),
            ("exchange_rate", 1.0), ("send", True), ("email", "x"),
        ):
            with self.subTest(key=key):
                mutated = copy.deepcopy(payload)
                mutated[key] = value
                with self.assertRaises(po.PurchaseOrderToolError) as caught:
                    po.require_create_allowed("POST", po.CREATE_PATH, ORG_ID, mutated, payload)
                self.assertIn("uncommissioned field(s)", str(caught.exception))

    def test_a_line_that_does_not_match_the_reviewed_plan_is_refused(self) -> None:
        payload = self.payload_of(self.stage())
        mutated = copy.deepcopy(payload)
        mutated["line_items"][0]["rate"] = 2200
        with self.assertRaises(po.PurchaseOrderToolError) as caught:
            po.require_create_allowed("POST", po.CREATE_PATH, ORG_ID, mutated, payload)
        self.assertIn("does not match the reviewed plan", str(caught.exception))

    def test_a_line_without_an_item_id_is_refused_at_the_transport_gate(self) -> None:
        payload = self.payload_of(self.stage())
        mutated = copy.deepcopy(payload)
        mutated["line_items"][0]["item_id"] = ""
        with self.assertRaises(po.PurchaseOrderToolError) as caught:
            po.require_create_allowed("POST", po.CREATE_PATH, ORG_ID, mutated, payload)
        self.assertIn("existing Zoho item", str(caught.exception))

    def test_the_query_string_carries_only_the_organization_id(self) -> None:
        path = self.stage()
        url = self.commit(path)["posts"][0]["url"]
        self.assertTrue(url.endswith(f"?organization_id={ORG_ID}"))
        for forbidden in ("send", "status", "email", "action", "ignore_auto_number"):
            self.assertNotIn(f"&{forbidden}=", url)

    def test_the_lock_exists_before_the_post_leaves(self) -> None:
        path = self.stage()
        self.assertTrue(self.commit(path)["posts"][0]["lock_exists"])

    def test_a_transport_failure_is_reported_and_needs_restage_not_a_permanent_lock(self) -> None:
        path = self.stage()
        error = self.commit_expecting_error(path, post_error=URLError("connection reset"))
        self.assertIn("re-stage", str(error).casefold())
        self.assertNotIn("permanent", str(error).casefold())
        self.assertIn("his go to the NEW plan", str(error))
        self.assertIn("Nothing was deleted, voided, cancelled", str(error))
        record = self.lock_record(path)
        self.assertEqual(record["status"], "indeterminate_needs_restage")
        self.assertFalse(record["permanent_lock"])
        self.assertTrue(record["write_attempted"])
        # The SAME plan is bound to stale live state: refused, no second POST.
        error = self.commit_expecting_error(path)
        self.assertIn("Re-stage", str(error))
        self.assertEqual(self.last_calls["posts"], [])

    def test_a_spent_plan_cannot_be_replayed(self) -> None:
        path = self.stage()
        self.commit(path)
        error = self.commit_expecting_error(path)
        self.assertIn("cannot be replayed", str(error))
        self.assertEqual(self.last_calls["posts"], [])

    def test_exactly_one_post_is_attempted(self) -> None:
        path = self.stage()
        self.assertEqual([call["method"] for call in self.commit(path)["posts"]], ["POST"])


# ---------------------------------------------------------------------------
# Created-state verification
# ---------------------------------------------------------------------------


class CreatedStateTests(PurchaseOrderTestCase):
    def _created(self, path: Path, **overrides) -> dict:
        evidence = self.plan_json(path)["live_evidence"]
        return created_purchase_order(evidence["post_payload"], evidence, **overrides)

    def test_a_non_draft_result_locks_indeterminate(self) -> None:
        for status in ("open", "issued", "approved", "pending_approval", "billed", "cancelled"):
            with self.subTest(status=status):
                path = self.stage()
                error = self.commit_expecting_error(
                    path, created=self._created(path, status=status)
                )
                self.assertIn("beyond Draft", str(error))
                self.assertEqual(self.lock_record(path)["status"], "indeterminate_needs_restage")

    def test_an_unknown_status_is_still_refused(self) -> None:
        path = self.stage()
        error = self.commit_expecting_error(path, created=self._created(path, status="whatever"))
        self.assertIn("not exactly 'draft'", str(error))

    def test_an_emailed_result_locks_indeterminate(self) -> None:
        path = self.stage()
        error = self.commit_expecting_error(path, created=self._created(path, is_emailed=True))
        self.assertIn("is_emailed", str(error))
        self.assertIn("no mail transport", str(error))

    def test_a_submitted_or_approved_result_is_refused(self) -> None:
        path = self.stage()
        self.assertIn(
            "already submitted",
            str(self.commit_expecting_error(path, created=self._created(path, submitted_by="RH"))),
        )
        path = self.stage()
        self.assertIn(
            "approvers_list",
            str(self.commit_expecting_error(
                path, created=self._created(path, approvers_list=["x"])
            )),
        )

    def test_a_billed_or_received_result_is_refused(self) -> None:
        path = self.stage()
        self.assertIn(
            "billed_status",
            str(self.commit_expecting_error(
                path, created=self._created(path, billed_status="billed")
            )),
        )
        path = self.stage()
        self.assertIn(
            "received_status",
            str(self.commit_expecting_error(
                path, created=self._created(path, received_status="received")
            )),
        )

    def test_a_wrong_vendor_currency_or_line_locks_indeterminate(self) -> None:
        for overrides, fragment in (
            ({"vendor_id": "96274000000099999"}, "names vendor"),
            ({"currency_code": "CAD"}, "bills in"),
            ({"sub_total": 1.0}, "sub_total"),
        ):
            with self.subTest(overrides=overrides):
                path = self.stage()
                error = self.commit_expecting_error(path, created=self._created(path, **overrides))
                self.assertIn(fragment, str(error))
                self.assertEqual(self.lock_record(path)["status"], "indeterminate_needs_restage")

    def test_a_dropped_line_is_detected(self) -> None:
        path = self.stage()
        created = self._created(path)
        created["line_items"] = created["line_items"][:2]
        error = self.commit_expecting_error(path, created=created)
        self.assertIn("lines, not the approved 4", str(error))

    def test_an_ignored_unproven_field_is_detected_rather_than_reported_as_landed(self) -> None:
        payload = jrain_input()
        payload["terms"] = {"value": "Net 45", "source": "Rachad's instruction."}
        path = self.stage(payload=payload)
        created = self._created(path)
        # Zoho silently ignoring `terms` is exactly the unproven-field risk.
        created["terms"] = ""
        error = self.commit_expecting_error(path, created=created)
        self.assertIn("terms", str(error))
        self.assertEqual(self.lock_record(path)["status"], "indeterminate_needs_restage")

    def test_the_number_comes_from_zoho_and_is_recorded(self) -> None:
        path = self.stage()
        self.commit(path)
        self.assertEqual(self.lock_record(path)["purchaseorder_number"], "PO-00007")

    def test_a_result_without_a_number_locks_indeterminate(self) -> None:
        path = self.stage()
        created = self._created(path)
        created["purchaseorder_number"] = ""
        error = self.commit_expecting_error(path, created=created)
        self.assertIn("no purchase order number", str(error))


# ---------------------------------------------------------------------------
# Source-level and AST containment
# ---------------------------------------------------------------------------


class SourceContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(po.__file__)
        self.source = self.path.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_exactly_one_urlopen_call_site_exists(self) -> None:
        calls = [
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "urlopen"
        ]
        self.assertEqual(len(calls), 1)

    def test_every_request_is_a_post(self) -> None:
        methods = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Request":
                for keyword in node.keywords:
                    if keyword.arg == "method":
                        self.assertIsInstance(keyword.value, ast.Constant)
                        methods.append(keyword.value.value)
        self.assertEqual(methods, ["POST"])

    def test_only_get_and_post_appear_as_http_methods(self) -> None:
        self.assertEqual(po.ALLOWED_METHODS, ("GET", "POST"))
        for forbidden in ('method="PUT"', 'method="PATCH"', 'method="DELETE"'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)

    def test_no_lifecycle_mail_or_browser_route_string_exists(self) -> None:
        for forbidden in (
            "/purchaseorders/email", "/submit", "/approve", "/markasopen",
            "/markasbilled", "/purchasereceives", "/converttobill", "/attachment",
            "/reminder", "/bulk", "to_mail_ids", "send=true", "smtplib", "Mail.Send",
            "Mail.ReadWrite", "graph.microsoft.com", "connect_over_cdp", "playwright",
            "outlook", "webbrowser",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)

    def test_the_read_routes_are_bounded_and_named(self) -> None:
        self.assertEqual(len(po.READ_PATH_PATTERNS), 5)
        for path in (
            "/books/v3/purchaseorders", "/books/v3/purchaseorders/96274000001043001",
            "/books/v3/contacts/96274000000027889", "/books/v3/items/96274000000019605",
            "/books/v3/settings/taxes",
        ):
            with self.subTest(path=path):
                self.assertEqual(po.require_read_path(path), path)
        for path in (
            "/books/v3/invoices", "/books/v3/estimates/1", "/books/v3/contacts",
            "/books/v3/purchaseorders/1/email", "/inventory/v1/items/1",
        ):
            with self.subTest(path=path):
                with self.assertRaises(po.PurchaseOrderToolError):
                    po.require_read_path(path)

    def test_the_create_route_is_the_only_write_constant(self) -> None:
        self.assertEqual(po.CREATE_PATH, "/books/v3/purchaseorders")
        self.assertEqual(po.ALLOWED_POST_KEYS, {
            "vendor_id", "date", "delivery_date", "reference_number", "ship_via",
            "notes", "terms", "contact_persons", "line_items",
        })
        self.assertEqual(set(po.ALLOWED_POST_LINE_KEYS), {
            "item_id", "name", "description", "quantity", "rate", "unit", "tax_id",
        })

    def test_the_approval_is_judged_by_the_one_shared_detector(self) -> None:
        """2026-08-21: no hand-written comparator of any kind -- the shared
        owner-authority module (a verbatim copy of Aze's detector) decides."""
        self.assertIn("owner_authority.require_owner_go_after_plan(", self.source)
        self.assertNotIn("approval != APPROVAL_WORD", self.source)
        for weakening in (
            "approval.strip()", "approval.upper()", "approval.casefold()", "approval.lower()",
        ):
            with self.subTest(weakening=weakening):
                self.assertNotIn(weakening, self.source)

    def test_the_delivery_date_key_choice_is_documented_from_live_evidence(self) -> None:
        self.assertIn("PO-00001-R2", self.source)
        self.assertIn("expected_delivery_date` is empty on all six", self.source)

    def test_there_is_no_retry_or_cleanup_route(self) -> None:
        for forbidden in ("def retry", "for attempt in", "while attempt", "def rollback",
                          "def cleanup", "def delete_", "def void_", "def cancel_"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)


if __name__ == "__main__":
    unittest.main()
