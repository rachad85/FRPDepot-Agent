"""Safety tests for the GENERAL existing-estimate revision.

Commissioned by Rachad on 2026-08-13: an ordinary quote revision must amend the
customer's own estimate in place instead of creating a replacement. The present
need is SCT QT-000031, whose single 1-inch D411 stub-flange line goes from
quantity 2 to quantity 10, but the action is reusable.

NO TEST IN THIS FILE PERFORMS A LIVE CALL. Every read is a fake api_get and
every write is a fake urlopen; the real transports are asserted never to run.
"""

from __future__ import annotations

import argparse
import copy
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.error import URLError

import zoho_tool as tool
import zoho_customer_quote_tool as draft

ORG_ID = "96274000000000001"
# The real live SCT quote, read read-only on 2026-08-13.
QT31 = "96274000001566055"
QT31_LINE = "96274000001566056"
QT31_ITEM = "96274000000019583"
SCT_CUSTOMER = "96274000000186533"
GST_TAX = "96274000000035512"
ON_HST_TAX = "96274000000035516"
GST_QST_GROUP = "96274000001071139"

ACTIVE_TAXES = [
    {"tax_id": GST_TAX, "tax_name": "GST", "tax_percentage": 5,
     "tax_type": "tax", "status": "Active", "is_inactive": False},
    {"tax_id": ON_HST_TAX, "tax_name": "ON HST", "tax_percentage": 13,
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


def live_estimate() -> dict:
    """QT-000031 as Zoho actually returned it on 2026-08-13, one line, GST 5%."""
    return {
        "estimate_id": QT31,
        "estimate_number": "QT-000031",
        "reference_number": "",
        "customer_id": SCT_CUSTOMER,
        "customer_name": "Structural Composites Technologies Ltd",
        "status": "sent",
        "date": "2026-08-12",
        "expiry_date": "",
        "notes": "Looking forward for your business.",
        "terms": "",
        "currency_id": "96274000000000087",
        "currency_code": "CAD",
        "exchange_rate": 1.0,
        "template_id": "96274000000000539",
        "salesperson_id": "",
        "salesperson_name": "",
        "shipping_charge": 0.0,
        "adjustment": 0.0,
        "adjustment_description": "",
        "discount": 0.0,
        "discount_type": "item_level",
        "is_discount_before_tax": True,
        "discount_total": 0.0,
        "discount_percent": 0.0,
        "custom_fields": [],
        "invoice_ids": [],
        "salesorders": [],
        "invoiced_amount": 0.0,
        "is_viewed_by_client": False,
        "sub_total": 100.8,
        "sub_total_exclusive_of_discount": 100.8,
        "tax_total": 5.04,
        "total": 105.84,
        "bcy_sub_total": 100.8,
        "bcy_tax_total": 5.04,
        "bcy_total": 105.84,
        "taxes": [{"tax_name": "GST", "tax_amount": 5.04, "tax_amount_formatted": "CAD5.04"}],
        "estimate_url": "https://example.invalid/staged-secure-estimate-url",
        "last_modified_time": "2026-08-13T02:31:00-0400",
        "line_items": [
            {
                "line_item_id": QT31_LINE,
                "item_id": QT31_ITEM,
                "sku": "FLDN25150PSI411",
                "name": "FRP STUB FLANGE-1\"/150PSI/D411",
                "description": "FRP STUB FLANGE",
                "item_order": 1,
                "quantity": 2.0,
                "rate": 50.4,
                "bcy_rate": 50.4,
                "unit": "pcs",
                "pricing_scheme": "unit",
                "discount": 0.0,
                "discount_amount": 0.0,
                "discounts": [],
                "tax_id": GST_TAX,
                "tax_name": "GST",
                "tax_type": "tax",
                "tax_percentage": 5,
                "item_total": 100.8,
                "line_item_taxes": [
                    {"tax_id": GST_TAX, "tax_name": "GST (5%)", "tax_amount": 5.04}
                ],
            }
        ],
    }


def revised_estimate(before: dict | None = None, quantity: Decimal = Decimal("10")) -> dict:
    """What Zoho should return once the single quantity lands."""
    after = copy.deepcopy(before or live_estimate())
    line = after["line_items"][0]
    rate = Decimal(str(line["rate"]))
    net = (quantity * rate).quantize(Decimal("0.01"))
    tax = (net * Decimal("5") / Decimal("100")).quantize(Decimal("0.01"))
    line["quantity"] = float(quantity)
    line["item_total"] = float(net)
    line["line_item_taxes"][0]["tax_amount"] = float(tax)
    after["sub_total"] = float(net)
    after["sub_total_exclusive_of_discount"] = float(net)
    after["tax_total"] = float(tax)
    after["total"] = float(net + tax)
    after["bcy_sub_total"] = float(net)
    after["bcy_tax_total"] = float(tax)
    after["bcy_total"] = float(net + tax)
    after["taxes"] = [
        {"tax_name": "GST", "tax_amount": float(tax), "tax_amount_formatted": f"CAD{tax}"}
    ]
    after["estimate_url"] = "https://example.invalid/fresh-secure-estimate-url"
    after["last_modified_time"] = "2026-08-13T15:20:00-0400"
    return after


def qty_input(quantity=10, estimate_id: str = QT31, line_id: str = QT31_LINE) -> dict:
    return {
        "estimate_id": estimate_id,
        "reason": "Bon Bacani asked whether 10 are available; Rachad approved revising the quote.",
        "lines": [
            {
                "line_item_id": line_id,
                "fields": {
                    "quantity": {
                        "value": quantity,
                        "source": "Rachad's Telegram instruction 2026-08-13 to quote 10.",
                    }
                },
            }
        ],
    }


def fake_vault(scopes=None) -> dict:
    return {
        "api_domain": tool.EXPECTED_API_DOMAIN,
        "books_organization_id": ORG_ID,
        "scopes": list(tool.SCOPES) if scopes is None else list(scopes),
    }


class RevisionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.plan_dir = self.root / "zoho_plans"
        self.plan_dir.mkdir(parents=True)
        self.addCleanup(self._temp.cleanup)

    def write_input(self, payload: dict, name: str = "revision_input.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _reader(self, estimates: list, taxes: list | None = None):
        pending = list(estimates)
        tax_rows = ACTIVE_TAXES if taxes is None else taxes
        calls = {"estimates": 0, "taxes": 0}

        def fake_api_get(access_token, api_domain, path):
            if "/settings/taxes" in path:
                calls["taxes"] += 1
                return {"code": 0, "taxes": copy.deepcopy(tax_rows)}
            calls["estimates"] += 1
            if not pending:
                raise AssertionError("unexpected extra estimate GET")
            return {"code": 0, "estimate": pending.pop(0)}

        return fake_api_get, calls

    def stage(
        self,
        payload: dict | None = None,
        before: dict | None = None,
        taxes: list | None = None,
    ) -> Path:
        payload = qty_input() if payload is None else payload
        before = before or live_estimate()
        input_path = self.write_input(payload)
        reader, self.stage_calls = self._reader([copy.deepcopy(before)], taxes)
        vault = fake_vault()
        existing = set(self.plan_dir.glob("*.json"))
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool, "api_get", side_effect=reader
        ), patch.object(
            draft, "urlopen", side_effect=AssertionError("staging must never write")
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                draft.command_stage_estimate_revision(argparse.Namespace(input=str(input_path)))
            self.last_stage_output = output.getvalue()
        created = set(self.plan_dir.glob("*.json")) - existing
        self.assertEqual(len(created), 1)
        return created.pop()

    def stage_expecting_error(self, **kwargs) -> Exception:
        with self.assertRaises(draft.DraftToolError) as caught:
            self.stage(**kwargs)
        return caught.exception

    def plan_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def rehash(self, path: Path, plan: dict) -> Path:
        # A deliberately "resigned" tamper test must update the displayed card
        # too; otherwise the new permanent card binding correctly stops at the
        # outer hash before the deeper canonical-projection check is exercised.
        if isinstance(plan.get("live_evidence"), dict):
            plan["approval_card"] = draft.revision_approval_card(
                plan["live_evidence"], str(plan.get("expires_utc") or "")
            )
        plan["sha256"] = draft.approval_plan_digest(plan)
        plan["approval_card"]["plan_sha256"] = plan["sha256"]
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def commit(
        self,
        plan_path: Path,
        *,
        approval: str = draft.APPROVAL_WORD,
        before: dict | None = None,
        after: dict | None = None,
        put_result=None,
        put_error: Exception | None = None,
        scopes=None,
        organization_id: str = ORG_ID,
        taxes: list | None = None,
    ) -> dict:
        before = before or live_estimate()
        after = after or revised_estimate()
        reader, get_calls = self._reader(
            [copy.deepcopy(before), copy.deepcopy(after)], taxes
        )
        vault = fake_vault(scopes)
        vault["books_organization_id"] = organization_id
        calls: dict = {"puts": [], "gets": get_calls}
        self.last_calls = calls

        def fake_urlopen(request, timeout):
            calls["puts"].append({
                "method": request.get_method(),
                "url": request.full_url,
                "payload": json.loads(request.data.decode("utf-8")),
                "lock_exists": self.lock_exists(plan_path),
            })
            if put_error is not None:
                raise put_error
            return FakeResponse(
                put_result if put_result is not None else {"code": 0, "estimate": after}
            )

        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool, "api_get", side_effect=reader
        ), patch.object(draft, "urlopen", side_effect=fake_urlopen):
            draft.command_commit_estimate_revision(
                argparse.Namespace(plan=str(plan_path), approval=approval)
            )
        return calls

    def commit_expecting_error(self, plan_path: Path, **kwargs) -> Exception:
        with self.assertRaises(draft.DraftToolError) as caught:
            self.commit(plan_path, **kwargs)
        return caught.exception

    def lock_exists(self, plan_path: Path) -> bool:
        with patch.object(draft, "PLAN_DIR", self.plan_dir):
            return draft.correction_lock_path(self.plan_json(plan_path)["sha256"]).exists()

    def lock_record(self, plan_path: Path) -> dict:
        with patch.object(draft, "PLAN_DIR", self.plan_dir):
            lock = draft.correction_lock_path(self.plan_json(plan_path)["sha256"])
        return json.loads(lock.read_text(encoding="utf-8")) if lock.exists() else {}


# ---------------------------------------------------------------------------
# The realistic QT-000031 quantity 2 -> 10 case
# ---------------------------------------------------------------------------


class Qt31FixtureTests(RevisionTestCase):
    def test_the_exact_target_is_derivable(self) -> None:
        path = self.stage()
        evidence = self.plan_json(path)["live_evidence"]
        expected = evidence["expected"]
        self.assertEqual(expected["sub_total"], "504.00")
        self.assertEqual(expected["tax_total"], "25.20")
        self.assertEqual(expected["total"], "529.20")
        self.assertEqual(expected["tax_certainty"], "exact")
        self.assertTrue(expected["tax_total_asserted"])
        self.assertEqual(expected["tax_uncertainty_reasons"], [])
        self.assertEqual(evidence["current"]["sub_total"], "100.80")
        self.assertEqual(evidence["current"]["total"], "105.84")

    def test_the_live_line_carries_gst_five_percent_not_hst(self) -> None:
        # Recorded because the commissioning brief called this an HST case. The
        # live SCT customer is in Winnipeg and the live line is GST 5%.
        path = self.stage()
        rows = self.plan_json(path)["live_evidence"]["expected"]["tax_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tax_id"], GST_TAX)
        self.assertEqual(rows[0]["tax_name"], "GST")
        self.assertEqual(rows[0]["tax_percentage"], "5")
        self.assertEqual(rows[0]["tax_amount"], "25.20")

    def test_identity_currency_and_status_are_preserved_in_the_plan(self) -> None:
        estimate = self.plan_json(self.stage())["live_evidence"]["estimate"]
        self.assertEqual(estimate["estimate_number"], "QT-000031")
        self.assertEqual(estimate["customer_id"], SCT_CUSTOMER)
        self.assertEqual(estimate["currency_code"], "CAD")
        self.assertEqual(estimate["exchange_rate"], "1.0")
        self.assertEqual(estimate["status"], "sent")
        self.assertEqual(estimate["line_count"], 1)

    def test_the_whole_commit_flow_is_one_verified_put(self) -> None:
        path = self.stage()
        calls = self.commit(path)
        self.assertEqual(len(calls["puts"]), 1)
        self.assertEqual(calls["puts"][0]["method"], "PUT")
        self.assertIn(f"/books/v3/estimates/{QT31}?organization_id={ORG_ID}",
                      calls["puts"][0]["url"])
        payload = calls["puts"][0]["payload"]
        self.assertEqual(payload["line_items"][0]["quantity"], 10)
        self.assertEqual(self.lock_record(path)["status"], "committed_verified")

    def test_staging_makes_no_write_and_reads_only(self) -> None:
        self.stage()
        self.assertEqual(self.stage_calls["estimates"], 1)
        self.assertEqual(self.stage_calls["taxes"], 1)


# ---------------------------------------------------------------------------
# Permanent concise approval card and fresh-projection regression
# ---------------------------------------------------------------------------


class ConciseRevisionApprovalCardTests(RevisionTestCase):
    def hst_input(self) -> dict:
        payload = qty_input()
        payload["lines"][0]["fields"]["tax_id"] = {
            "value": ON_HST_TAX,
            "source": "Rachad: charge Ontario HST on this quote.",
        }
        return payload

    def hst_after(self) -> dict:
        after = revised_estimate()
        line = after["line_items"][0]
        line["tax_id"] = ON_HST_TAX
        line["tax_name"] = "ON HST"
        line["tax_percentage"] = 13
        line["line_item_taxes"] = [
            {"tax_id": ON_HST_TAX, "tax_name": "ON HST (13%)", "tax_amount": 65.52}
        ]
        after["tax_total"] = 65.52
        after["total"] = 569.52
        after["bcy_tax_total"] = 65.52
        after["bcy_total"] = 569.52
        after["taxes"] = [
            {"tax_name": "ON HST", "tax_amount": 65.52, "tax_amount_formatted": "CAD65.52"}
        ]
        return after

    def test_revision_card_has_the_required_concise_fields_and_binding(self) -> None:
        path = self.stage(payload=self.hst_input())
        plan = self.plan_json(path)
        card = plan["approval_card"]
        self.assertEqual(card["operation"], draft.REVISION_OPERATION)
        self.assertEqual(card["scope"]["method"], "PUT")
        self.assertEqual(card["scope"]["route"], f"/books/v3/estimates/{QT31}")
        self.assertEqual(card["customer"]["name"], "Structural Composites Technologies Ltd")
        self.assertEqual(card["document"]["number"], "QT-000031")
        self.assertEqual(card["currency"]["code"], "CAD")
        self.assertEqual(card["lines"][0]["change_details"]["quantity"],
                         {"from": "2.0", "to": "10"})
        self.assertEqual(card["lines"][0]["change_details"]["tax_id"],
                         {"from": GST_TAX, "to": ON_HST_TAX})
        self.assertEqual(card["lines"][0]["tax"], "ON HST")
        self.assertEqual(card["totals"], {
            "subtotal": "504.00", "tax": "65.52", "total": "569.52",
            "tax_certainty": "exact",
        })
        self.assertTrue(card["risks"])
        self.assertEqual(card["expires_utc"], plan["expires_utc"])
        self.assertEqual(card["plan_sha256"], plan["sha256"])
        self.assertEqual(draft.approval_plan_digest(plan), plan["sha256"])

    def test_stage_prints_only_the_card_while_plan_keeps_full_evidence(self) -> None:
        path = self.stage(payload=self.hst_input())
        displayed = json.loads(self.last_stage_output)
        self.assertEqual(set(displayed), {"approval_card"})
        self.assertEqual(displayed["approval_card"], self.plan_json(path)["approval_card"])
        self.assertNotIn("live_evidence", displayed)
        plan = self.plan_json(path)
        self.assertIn("before_state", plan["live_evidence"]["estimate"])
        self.assertIn("put_payload", plan["live_evidence"])
        self.assertEqual(len(plan["live_evidence"]["expected"]["lines"]), 1)

    def test_fresh_qt31_quantity_and_tax_revision_ignores_only_get_telemetry(self) -> None:
        path = self.stage(payload=self.hst_input())
        fresh = live_estimate()
        fresh["estimate_url"] = "https://example.invalid/a-new-url-on-the-second-get"
        calls = self.commit(path, before=fresh, after=self.hst_after())
        self.assertEqual(len(calls["puts"]), 1)
        self.assertEqual(calls["puts"][0]["payload"]["line_items"][0]["quantity"], 10)
        self.assertEqual(calls["puts"][0]["payload"]["line_items"][0]["tax_id"], ON_HST_TAX)
        self.assertEqual(self.lock_record(path)["status"], "committed_verified")

    def test_true_business_drift_still_refuses_before_lock(self) -> None:
        path = self.stage(payload=self.hst_input())
        fresh = live_estimate()
        fresh["notes"] = "A real business field changed."
        error = self.commit_expecting_error(path, before=fresh, after=self.hst_after())
        self.assertIn("changed after review", str(error))
        self.assertFalse(self.lock_exists(path))
        self.assertEqual(self.last_calls["puts"], [])

    def test_tampered_card_or_hash_cannot_bind_approval_to_another_operation(self) -> None:
        path = self.stage(payload=self.hst_input())
        plan = self.plan_json(path)
        plan["approval_card"]["operation"] = draft.QUOTE_CREATE_OPERATION
        path.write_text(json.dumps(plan), encoding="utf-8")
        self.assertIn("hash check failed", str(self.commit_expecting_error(path)))
        plan = self.plan_json(self.stage(payload=self.hst_input()))
        plan["approval_card"]["scope"]["route"] = "/books/v3/estimates/999"
        resigned_path = path.parent / "resigned-wrong-scope.json"
        plan["approval_card"]["plan_sha256"] = ""
        plan["sha256"] = draft.approval_plan_digest(plan)
        plan["approval_card"]["plan_sha256"] = plan["sha256"]
        resigned_path.write_text(json.dumps(plan), encoding="utf-8")
        self.assertIn(
            "Approval card does not exactly match",
            str(self.commit_expecting_error(resigned_path)),
        )

    def test_non_exact_ambiguous_stale_and_expired_approvals_are_refused(self) -> None:
        # 2026-08-21 (A1): a padded or lowercase APPROVED now counts; a
        # condition, a question or a blank still refuses before the lock.
        for bad in ("approved but wait", "hold on", "APPROVED?", ""):
            with self.subTest(approval=bad):
                path = self.stage(payload=self.hst_input())
                self.assertIn("REFUSED", str(self.commit_expecting_error(path, approval=bad)))
                self.assertFalse(self.lock_exists(path))
        path = self.stage(payload=self.hst_input())
        plan = self.plan_json(path)
        plan["created_utc"] = "2026-08-01T00:00:00+00:00"
        plan["expires_utc"] = "2026-08-02T00:00:00+00:00"
        self.rehash(path, plan)
        self.assertIn("expired", str(self.commit_expecting_error(path)))


# ---------------------------------------------------------------------------
# Closed input schema
# ---------------------------------------------------------------------------


class InputSchemaTests(RevisionTestCase):
    def test_unknown_top_level_field_is_refused(self) -> None:
        payload = qty_input()
        payload["payload"] = {"quantity": 10}
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("uncommissioned field(s): payload", str(error))

    def test_unknown_header_field_is_refused(self) -> None:
        payload = qty_input()
        payload["header"] = {"status": {"value": "draft", "source": "x"}}
        self.assertIn("uneditable field(s): status", str(self.stage_expecting_error(payload=payload)))

    def test_unknown_line_field_is_refused(self) -> None:
        payload = qty_input()
        payload["lines"][0]["fields"]["item_id"] = {"value": "1", "source": "x"}
        self.assertIn("uneditable field(s): item_id", str(self.stage_expecting_error(payload=payload)))

    def test_customer_cannot_be_changed(self) -> None:
        payload = qty_input()
        payload["header"] = {"customer_id": {"value": "1", "source": "x"}}
        self.assertIn("uneditable field(s): customer_id",
                      str(self.stage_expecting_error(payload=payload)))

    def test_every_value_needs_a_nonblank_source(self) -> None:
        for source in ("", "   ", None, 5):
            with self.subTest(source=source):
                payload = qty_input()
                payload["lines"][0]["fields"]["quantity"] = {"value": 10, "source": source}
                self.assertIn("REFUSED", str(self.stage_expecting_error(payload=payload)))

    def test_a_bare_value_without_the_source_wrapper_is_refused(self) -> None:
        payload = qty_input()
        payload["lines"][0]["fields"]["quantity"] = 10
        self.assertIn("value", str(self.stage_expecting_error(payload=payload)))

    def test_an_empty_change_set_is_refused(self) -> None:
        payload = {"estimate_id": QT31, "reason": "nothing"}
        self.assertIn("proposes no change", str(self.stage_expecting_error(payload=payload)))

    def test_reason_is_required_and_nonblank(self) -> None:
        payload = qty_input()
        payload.pop("reason")
        self.assertIn("missing reason", str(self.stage_expecting_error(payload=payload)))

    def test_a_line_may_not_be_named_twice(self) -> None:
        payload = qty_input()
        payload["lines"].append(copy.deepcopy(payload["lines"][0]))
        self.assertIn("twice", str(self.stage_expecting_error(payload=payload)))

    def test_estimate_id_must_be_a_positive_numeric_id(self) -> None:
        for bad in ("0", "-1", "abc", "", "12a"):
            with self.subTest(bad=bad):
                self.assertIn(
                    "positive numeric Zoho estimate ID",
                    str(self.stage_expecting_error(payload=qty_input(estimate_id=bad))),
                )

    def test_dates_must_be_exact_calendar_dates(self) -> None:
        for bad in ("2026-8-13", "13/08/2026", "2026-13-01", "2026-08-32", "today"):
            with self.subTest(bad=bad):
                payload = qty_input()
                payload["header"] = {"date": {"value": bad, "source": "Rachad"}}
                self.assertIn("YYYY-MM-DD", str(self.stage_expecting_error(payload=payload)))

    def test_a_good_date_and_expiry_date_are_accepted(self) -> None:
        payload = qty_input()
        payload["header"] = {
            "date": {"value": "2026-08-13", "source": "Rachad's instruction"},
            "expiry_date": {"value": "2026-09-12", "source": "30 day validity"},
            "reference_number": {"value": "SCT-RFQ-88", "source": "Bon Bacani's email"},
        }
        evidence = self.plan_json(self.stage(payload=payload))["live_evidence"]
        self.assertEqual(
            [change["field"] for change in evidence["header_changes"]],
            ["reference_number", "date", "expiry_date"],
        )
        self.assertEqual(evidence["put_payload"]["date"], "2026-08-13")
        self.assertEqual(evidence["put_payload"]["expiry_date"], "2026-09-12")
        self.assertEqual(evidence["put_payload"]["reference_number"], "SCT-RFQ-88")

    def test_blank_header_values_are_refused_rather_than_clearing_a_field(self) -> None:
        payload = qty_input()
        payload["header"] = {"notes": {"value": "", "source": "Rachad"}}
        self.assertIn("nonblank", str(self.stage_expecting_error(payload=payload)))

    def test_quantity_must_be_positive_and_bounded(self) -> None:
        for bad in (0, -1, "0", 2000000):
            with self.subTest(bad=bad):
                self.assertIn(
                    "REFUSED", str(self.stage_expecting_error(payload=qty_input(quantity=bad)))
                )

    def test_a_rate_change_is_accepted_and_repriced(self) -> None:
        payload = qty_input()
        payload["lines"][0]["fields"]["rate"] = {
            "value": "60.00", "source": "Rachad's 2026-08-13 repricing instruction"
        }
        expected = self.plan_json(self.stage(payload=payload))["live_evidence"]["expected"]
        self.assertEqual(expected["sub_total"], "600.00")
        self.assertEqual(expected["tax_total"], "30.00")
        self.assertEqual(expected["total"], "630.00")


# ---------------------------------------------------------------------------
# Discount semantics -- the 2026-08-10 lesson
# ---------------------------------------------------------------------------


class DiscountSemanticsTests(RevisionTestCase):
    def test_a_percentage_discount_must_be_a_string(self) -> None:
        payload = qty_input()
        payload["lines"][0]["fields"]["discount"] = {"value": 10, "source": "Rachad"}
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("FLAT CAD amount", str(error))
        self.assertIn("10%", str(error))

    def test_a_percentage_string_is_accepted_and_priced(self) -> None:
        payload = qty_input()
        payload["lines"][0]["fields"]["discount"] = {
            "value": "10%", "source": "Rachad's standing 10% instruction"
        }
        expected = self.plan_json(self.stage(payload=payload))["live_evidence"]["expected"]
        self.assertEqual(expected["lines"][0]["discount_amount"], "50.40")
        self.assertEqual(expected["sub_total"], "453.60")
        self.assertEqual(expected["tax_total"], "22.68")
        self.assertEqual(expected["total"], "476.28")

    def test_zero_is_the_only_accepted_numeric_discount(self) -> None:
        payload = qty_input()
        payload["lines"][0]["fields"]["discount"] = {"value": 0, "source": "no discount"}
        expected = self.plan_json(self.stage(payload=payload))["live_evidence"]["expected"]
        self.assertEqual(expected["lines"][0]["discount_amount"], "0.00")

    def test_a_malformed_percentage_string_is_refused(self) -> None:
        for bad in ("110%", "-5%", "10 %", "10", "abc%", "10.123%"):
            with self.subTest(bad=bad):
                payload = qty_input()
                payload["lines"][0]["fields"]["discount"] = {"value": bad, "source": "x"}
                self.assertIn("REFUSED", str(self.stage_expecting_error(payload=payload)))

    def test_an_ambiguous_live_numeric_discount_refuses_the_whole_revision(self) -> None:
        before = live_estimate()
        line = before["line_items"][0]
        # Zoho read a flat CAD 10.00 off the line: the discount amount equals the
        # bare number, so it cannot be re-sent as a percentage.
        line["discount"] = 10.0
        line["discount_amount"] = 10.0
        line["item_total"] = 90.8
        before["sub_total"] = 90.8
        error = self.stage_expecting_error(before=before)
        self.assertIn("flat CAD amount", str(error))
        self.assertIn("will not guess", str(error))

    def test_a_live_percentage_echoed_as_a_bare_number_is_resent_as_a_string(self) -> None:
        before = live_estimate()
        line = before["line_items"][0]
        line["discount"] = 10.0
        line["discount_amount"] = 10.08
        line["item_total"] = 90.72
        before["sub_total"] = 90.72
        before["tax_total"] = 4.54
        before["total"] = 95.26
        path = self.stage(before=before)
        payload = self.plan_json(path)["live_evidence"]["put_payload"]
        self.assertEqual(payload["line_items"][0]["discount"], "10%")

    def test_an_entity_level_discount_refuses_a_line_discount_change(self) -> None:
        before = live_estimate()
        before["discount_type"] = "entity_level"
        payload = qty_input()
        payload["lines"][0]["fields"]["discount"] = {"value": "5%", "source": "x"}
        error = self.stage_expecting_error(payload=payload, before=before)
        self.assertIn("entity_level", str(error))

    def test_a_nonzero_entity_level_discount_refuses_the_revision(self) -> None:
        before = live_estimate()
        before["discount"] = 25.0
        self.assertIn("entity-level discount", str(self.stage_expecting_error(before=before)))


# ---------------------------------------------------------------------------
# Eligible statuses
# ---------------------------------------------------------------------------


class StatusTests(RevisionTestCase):
    def test_draft_and_sent_are_eligible(self) -> None:
        self.assertEqual(draft.REVISION_ELIGIBLE_STATUSES, ("draft", "sent"))
        for status in ("draft", "sent"):
            with self.subTest(status=status):
                before = live_estimate()
                before["status"] = status
                path = self.stage(before=before)
                self.assertEqual(
                    self.plan_json(path)["live_evidence"]["estimate"]["status"], status
                )

    def test_every_other_status_is_refused_before_any_write(self) -> None:
        for status in (
            "accepted", "declined", "invoiced", "expired", "void", "deleted",
            "converted", "", "DRAFT", "sent ", "something_new",
        ):
            with self.subTest(status=status):
                before = live_estimate()
                before["status"] = status
                error = self.stage_expecting_error(before=before)
                self.assertIn("Only an estimate", str(error))
                self.assertIn("Nothing staged", str(error))

    def test_no_status_field_is_ever_sent(self) -> None:
        payload = self.plan_json(self.stage())["live_evidence"]["put_payload"]
        self.assertNotIn("status", payload)
        for line in payload["line_items"]:
            self.assertNotIn("status", line)
        self.assertNotIn("status", draft.REVISION_ALLOWED_PUT_KEYS)

    def test_the_status_is_preserved_and_verified_after_the_write(self) -> None:
        path = self.stage()
        after = revised_estimate()
        after["status"] = "accepted"
        error = self.commit_expecting_error(path, after=after)
        self.assertIn("status", str(error))
        self.assertEqual(self.lock_record(path)["status"], "indeterminate_needs_restage")


# ---------------------------------------------------------------------------
# Line integrity: no omission, addition, reorder or substitution
# ---------------------------------------------------------------------------


def two_line_estimate() -> dict:
    before = live_estimate()
    second = copy.deepcopy(before["line_items"][0])
    second.update({
        "line_item_id": "96274000001566099",
        "item_id": "96274000000019605",
        "name": "FRP STUB FLANGE-1-1/2\"/150PSI/D411",
        "sku": "FLDN40150PSI411",
        "item_order": 2,
        "quantity": 4.0,
        "rate": 54.0,
        "bcy_rate": 54.0,
        "item_total": 216.0,
        "line_item_taxes": [{"tax_id": GST_TAX, "tax_name": "GST (5%)", "tax_amount": 10.8}],
    })
    before["line_items"].append(second)
    before["sub_total"] = 316.8
    before["sub_total_exclusive_of_discount"] = 316.8
    before["tax_total"] = 15.84
    before["total"] = 332.64
    before["bcy_sub_total"] = 316.8
    before["bcy_tax_total"] = 15.84
    before["bcy_total"] = 332.64
    before["taxes"] = [{"tax_name": "GST", "tax_amount": 15.84, "tax_amount_formatted": "CAD15.84"}]
    return before


class LineIntegrityTests(RevisionTestCase):
    def test_every_live_line_is_resent_once_in_order_with_both_ids(self) -> None:
        before = two_line_estimate()
        path = self.stage(before=before)
        lines = self.plan_json(path)["live_evidence"]["put_payload"]["line_items"]
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            [line["line_item_id"] for line in lines],
            [QT31_LINE, "96274000001566099"],
        )
        self.assertEqual(
            [line["item_id"] for line in lines],
            [QT31_ITEM, "96274000000019605"],
        )

    def test_the_untouched_line_keeps_its_exact_values(self) -> None:
        before = two_line_estimate()
        rows = self.plan_json(self.stage(before=before))["live_evidence"]["expected"]["lines"]
        self.assertEqual(rows[1]["changed_fields"], [])
        self.assertEqual(rows[1]["quantity"], "4.0")
        self.assertEqual(rows[1]["item_total"], "216.00")

    def test_a_line_id_not_on_the_estimate_is_refused(self) -> None:
        error = self.stage_expecting_error(payload=qty_input(line_id="96274000009999999"))
        self.assertIn("not on the live estimate", str(error))
        self.assertIn("cannot add, substitute or invent a line", str(error))

    def test_the_allowlist_refuses_a_dropped_line(self) -> None:
        before = two_line_estimate()
        path = self.stage(before=before)
        evidence = self.plan_json(path)["live_evidence"]
        payload = copy.deepcopy(evidence["put_payload"])
        payload["line_items"] = payload["line_items"][:1]
        with self.assertRaises(draft.DraftToolError) as caught:
            draft.require_revision_put_allowed(
                "PUT", f"/books/v3/estimates/{QT31}", ORG_ID, payload,
                evidence["put_payload"], evidence,
            )
        self.assertIn("complete live line list", str(caught.exception))

    def test_the_allowlist_refuses_an_added_line(self) -> None:
        path = self.stage()
        evidence = self.plan_json(path)["live_evidence"]
        payload = copy.deepcopy(evidence["put_payload"])
        payload["line_items"].append(copy.deepcopy(payload["line_items"][0]))
        with self.assertRaises(draft.DraftToolError):
            draft.require_revision_put_allowed(
                "PUT", f"/books/v3/estimates/{QT31}", ORG_ID, payload,
                evidence["put_payload"], evidence,
            )

    def test_the_allowlist_refuses_a_reordered_line(self) -> None:
        before = two_line_estimate()
        path = self.stage(before=before)
        evidence = self.plan_json(path)["live_evidence"]
        payload = copy.deepcopy(evidence["put_payload"])
        payload["line_items"].reverse()
        with self.assertRaises(draft.DraftToolError) as caught:
            draft.require_revision_put_allowed(
                "PUT", f"/books/v3/estimates/{QT31}", ORG_ID, payload,
                evidence["put_payload"], evidence,
            )
        self.assertIn("reviewed order", str(caught.exception))

    def test_the_allowlist_refuses_a_substituted_item(self) -> None:
        path = self.stage()
        evidence = self.plan_json(path)["live_evidence"]
        payload = copy.deepcopy(evidence["put_payload"])
        payload["line_items"][0]["item_id"] = "96274000000019605"
        with self.assertRaises(draft.DraftToolError) as caught:
            draft.require_revision_put_allowed(
                "PUT", f"/books/v3/estimates/{QT31}", ORG_ID, payload,
                evidence["put_payload"], evidence,
            )
        self.assertIn("item_id", str(caught.exception))

    def test_the_allowlist_refuses_a_duplicated_line_item_id(self) -> None:
        before = two_line_estimate()
        path = self.stage(before=before)
        evidence = self.plan_json(path)["live_evidence"]
        payload = copy.deepcopy(evidence["put_payload"])
        payload["line_items"][1]["line_item_id"] = QT31_LINE
        with self.assertRaises(draft.DraftToolError) as caught:
            draft.require_revision_put_allowed(
                "PUT", f"/books/v3/estimates/{QT31}", ORG_ID, payload,
                evidence["put_payload"], evidence,
            )
        self.assertIn("repeats or omits", str(caught.exception))

    def test_a_free_text_line_without_an_item_id_refuses_staging(self) -> None:
        before = live_estimate()
        before["line_items"][0]["item_id"] = ""
        error = self.stage_expecting_error(before=before)
        self.assertIn("not linked to a Zoho item", str(error))

    def test_a_missing_line_is_detected_at_read_back(self) -> None:
        path = self.stage()
        after = revised_estimate()
        after["line_items"] = []
        error = self.commit_expecting_error(path, after=after)
        self.assertIn("lines, not the preserved 1", str(error))


# ---------------------------------------------------------------------------
# Route, method and payload containment
# ---------------------------------------------------------------------------


class ContainmentTests(RevisionTestCase):
    def test_only_put_is_accepted(self) -> None:
        path = self.stage()
        evidence = self.plan_json(path)["live_evidence"]
        for method in ("POST", "DELETE", "PATCH", "GET", "put", ""):
            with self.subTest(method=method):
                with self.assertRaises(draft.DraftToolError) as caught:
                    draft.require_revision_put_allowed(
                        method, f"/books/v3/estimates/{QT31}", ORG_ID,
                        evidence["put_payload"], evidence["put_payload"], evidence,
                    )
                self.assertIn("PUT and nothing else", str(caught.exception))

    def test_only_the_reviewed_estimate_route_is_accepted(self) -> None:
        path = self.stage()
        evidence = self.plan_json(path)["live_evidence"]
        for route in (
            "/books/v3/estimates",
            "/books/v3/estimates/96274000001559037",
            f"/books/v3/estimates/{QT31}/email",
            f"/books/v3/estimates/{QT31}/status/sent",
            f"/books/v3/invoices/{QT31}",
            f"/books/v3/estimates/{QT31}/approve",
            f"/books/v3/estimates/{QT31}?organization_id=1",
        ):
            with self.subTest(route=route):
                with self.assertRaises(draft.DraftToolError) as caught:
                    draft.require_revision_put_allowed(
                        "PUT", route, ORG_ID, evidence["put_payload"],
                        evidence["put_payload"], evidence,
                    )
                self.assertIn("nothing else", str(caught.exception))

    def test_an_extra_payload_key_is_refused(self) -> None:
        path = self.stage()
        evidence = self.plan_json(path)["live_evidence"]
        for key, value in (
            ("status", "draft"), ("send", True), ("email", "x"),
            ("is_emailed", True), ("customer_name", "Someone Else"),
        ):
            with self.subTest(key=key):
                payload = copy.deepcopy(evidence["put_payload"])
                payload[key] = value
                with self.assertRaises(draft.DraftToolError) as caught:
                    draft.require_revision_put_allowed(
                        "PUT", f"/books/v3/estimates/{QT31}", ORG_ID, payload,
                        evidence["put_payload"], evidence,
                    )
                self.assertIn("uncommissioned field(s)", str(caught.exception))

    def test_a_changed_customer_is_refused_by_the_allowlist(self) -> None:
        path = self.stage()
        evidence = self.plan_json(path)["live_evidence"]
        payload = copy.deepcopy(evidence["put_payload"])
        payload["customer_id"] = "96274000000060019"
        with self.assertRaises(draft.DraftToolError) as caught:
            draft.require_revision_put_allowed(
                "PUT", f"/books/v3/estimates/{QT31}", ORG_ID, payload,
                evidence["put_payload"], evidence,
            )
        self.assertIn("different customer", str(caught.exception))

    def test_a_changed_estimate_number_is_refused_by_the_allowlist(self) -> None:
        path = self.stage()
        evidence = self.plan_json(path)["live_evidence"]
        payload = copy.deepcopy(evidence["put_payload"])
        payload["estimate_number"] = "QT-000099"
        with self.assertRaises(draft.DraftToolError) as caught:
            draft.require_revision_put_allowed(
                "PUT", f"/books/v3/estimates/{QT31}", ORG_ID, payload,
                evidence["put_payload"], evidence,
            )
        self.assertIn("estimate number", str(caught.exception))

    def test_a_bare_numeric_line_discount_is_refused_at_the_transport_gate(self) -> None:
        path = self.stage()
        evidence = self.plan_json(path)["live_evidence"]
        payload = copy.deepcopy(evidence["put_payload"])
        payload["line_items"][0]["discount"] = 10
        with self.assertRaises(draft.DraftToolError) as caught:
            draft.require_revision_put_allowed(
                "PUT", f"/books/v3/estimates/{QT31}", ORG_ID, payload,
                evidence["put_payload"], evidence,
            )
        self.assertIn("flat CAD amount", str(caught.exception))

    def test_the_query_string_carries_only_the_organization_id(self) -> None:
        path = self.stage()
        calls = self.commit(path)
        url = calls["puts"][0]["url"]
        self.assertTrue(url.endswith(f"?organization_id={ORG_ID}"))
        for forbidden in ("send", "status", "email", "action", "ignore_auto_number"):
            self.assertNotIn(f"&{forbidden}=", url)


# ---------------------------------------------------------------------------
# Approval exactness
# ---------------------------------------------------------------------------


class ApprovalTests(RevisionTestCase):
    def test_a_conditional_or_blank_word_never_commits(self) -> None:
        # 2026-08-21 (A1): "yes" / "OK" / a padded APPROVED now count for this
        # reversible work; a condition, a question or a blank still refuses.
        for approval in (
            "approved but wait", "hold on", "APPROVED?", "", "not yet",
        ):
            with self.subTest(approval=approval):
                path = self.stage()
                error = self.commit_expecting_error(path, approval=approval)
                self.assertIn("REFUSED", str(error))
                self.assertFalse(self.lock_exists(path))
                self.assertEqual(self.last_calls["puts"], [])

    def test_a_non_string_approval_is_refused(self) -> None:
        path = self.stage()
        with self.assertRaises(draft.DraftToolError):
            self.commit(path, approval=None)
        self.assertFalse(self.lock_exists(path))

    def test_approval_is_checked_before_the_vault_and_the_network(self) -> None:
        path = self.stage()
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "load_vault", side_effect=AssertionError("vault must not be opened")
        ), patch.object(
            draft, "urlopen", side_effect=AssertionError("no network")
        ):
            with self.assertRaises(draft.DraftToolError):
                draft.command_commit_estimate_revision(
                    argparse.Namespace(plan=str(path), approval="approved")
                )


# ---------------------------------------------------------------------------
# Plan integrity and tampering
# ---------------------------------------------------------------------------


class PlanTamperTests(RevisionTestCase):
    def test_editing_the_plan_breaks_its_hash(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["intent"]["lines"][0]["fields"]["quantity"]["value"] = "500"
        path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        error = self.commit_expecting_error(path)
        self.assertIn("Plan hash check failed", str(error))
        self.assertFalse(self.lock_exists(path))

    def test_a_resigned_quantity_cannot_outvote_the_projection(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["intent"]["lines"][0]["fields"]["quantity"]["value"] = "500"
        self.rehash(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("canonical projection", str(error))
        self.assertFalse(self.lock_exists(path))

    def test_a_resigned_total_cannot_outvote_the_projection(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["expected"]["total"] = "1.00"
        self.rehash(path, plan)
        self.assertIn("canonical projection", str(self.commit_expecting_error(path)))

    def test_a_resigned_endpoint_cannot_be_redirected(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["put_endpoint"] = "PUT /books/v3/invoices/96274000001566055"
        self.rehash(path, plan)
        self.assertIn("canonical projection", str(self.commit_expecting_error(path)))

    def test_a_resigned_payload_key_cannot_be_smuggled_in(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["put_payload"]["customer_id"] = "96274000000060019"
        self.rehash(path, plan)
        self.assertIn("canonical projection", str(self.commit_expecting_error(path)))

    def test_a_resigned_intent_field_outside_the_schema_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["intent"]["header"] = {"customer_id": {"value": "1", "source": "x"}}
        self.rehash(path, plan)
        self.assertIn("uneditable field(s)", str(self.commit_expecting_error(path)))

    def test_a_resigned_source_cannot_be_blanked(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["intent"]["lines"][0]["fields"]["quantity"]["source"] = ""
        self.rehash(path, plan)
        self.assertIn("nonblank explicit source", str(self.commit_expecting_error(path)))

    def test_a_resigned_risk_note_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["risk"]["note"] = "harmless"
        self.rehash(path, plan)
        self.assertIn("single-atomic-PUT risk", str(self.commit_expecting_error(path)))

    def test_a_plan_from_another_action_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["kind"] = draft.ITEM9_KIND
        self.rehash(path, plan)
        self.assertIn("different tool, action or schema version",
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
        with patch.object(draft, "PLAN_DIR", self.plan_dir):
            with self.assertRaises(draft.DraftToolError) as caught:
                draft.command_commit_estimate_revision(
                    argparse.Namespace(plan=str(outside), approval=draft.APPROVAL_WORD)
                )
        self.assertIn("plan folder", str(caught.exception))

    def test_a_relative_plan_path_is_refused(self) -> None:
        with patch.object(draft, "PLAN_DIR", self.plan_dir):
            with self.assertRaises(draft.DraftToolError):
                draft.command_commit_estimate_revision(
                    argparse.Namespace(plan="plan.json", approval=draft.APPROVAL_WORD)
                )


# ---------------------------------------------------------------------------
# Drift, locking and the single attempt
# ---------------------------------------------------------------------------


class DriftAndLockTests(RevisionTestCase):
    def test_drift_before_the_write_is_a_free_refusal(self) -> None:
        path = self.stage()
        drifted = live_estimate()
        drifted["line_items"][0]["rate"] = 55.0
        error = self.commit_expecting_error(path, before=drifted)
        self.assertIn("BEFORE the replay lock", str(error))
        self.assertFalse(self.lock_exists(path))
        self.assertEqual(self.last_calls["puts"], [])

    def test_a_changed_tax_row_before_the_write_is_a_free_refusal(self) -> None:
        path = self.stage()
        taxes = copy.deepcopy(ACTIVE_TAXES)
        taxes[0]["tax_percentage"] = 6
        error = self.commit_expecting_error(path, taxes=taxes)
        self.assertIn("tax rows", str(error))
        self.assertFalse(self.lock_exists(path))

    def test_a_deactivated_tax_before_the_write_is_a_free_refusal(self) -> None:
        path = self.stage()
        taxes = copy.deepcopy(ACTIVE_TAXES)
        taxes[0]["status"] = "Inactive"
        taxes[0]["is_inactive"] = True
        self.assertFalse(self.lock_exists(path))
        error = self.commit_expecting_error(path, taxes=taxes)
        self.assertIn("BEFORE the replay lock", str(error))
        self.assertFalse(self.lock_exists(path))

    def test_a_missing_scope_is_a_free_refusal(self) -> None:
        path = self.stage()
        scopes = [s for s in tool.SCOPES if s != draft.ESTIMATE_UPDATE_SCOPE]
        error = self.commit_expecting_error(path, scopes=scopes)
        self.assertIn(draft.ESTIMATE_UPDATE_SCOPE, str(error))
        self.assertFalse(self.lock_exists(path))

    def test_a_different_organization_is_a_free_refusal(self) -> None:
        path = self.stage()
        error = self.commit_expecting_error(path, organization_id="96274000000000999")
        self.assertIn("organization", str(error))
        self.assertFalse(self.lock_exists(path))

    def test_the_lock_exists_before_the_put_leaves(self) -> None:
        path = self.stage()
        calls = self.commit(path)
        self.assertTrue(calls["puts"][0]["lock_exists"])

    def test_a_transport_failure_locks_the_plan_permanently(self) -> None:
        path = self.stage()
        error = self.commit_expecting_error(path, put_error=URLError("connection reset"))
        self.assertIn("re-stage", str(error).casefold())
        record = self.lock_record(path)
        self.assertEqual(record["status"], "indeterminate_needs_restage")
        self.assertFalse(record["permanent_lock"])
        self.assertTrue(record["write_attempted"])

    def test_a_locked_plan_cannot_be_replayed(self) -> None:
        path = self.stage()
        self.commit(path)
        error = self.commit_expecting_error(path)
        self.assertIn("cannot be replayed", str(error))
        self.assertEqual(self.last_calls["puts"], [])

    def test_exactly_one_put_is_attempted(self) -> None:
        path = self.stage()
        calls = self.commit(path)
        self.assertEqual([call["method"] for call in calls["puts"]], ["PUT"])

    def test_a_wrong_read_back_total_locks_indeterminate(self) -> None:
        path = self.stage()
        after = revised_estimate()
        after["total"] = 999.99
        error = self.commit_expecting_error(path, after=after)
        self.assertIn("Stop and reconcile", str(error))
        self.assertEqual(self.lock_record(path)["status"], "indeterminate_needs_restage")


# ---------------------------------------------------------------------------
# Protected fingerprint
# ---------------------------------------------------------------------------


class ProtectedDriftTests(RevisionTestCase):
    def _read_back_mutation(self, mutate) -> Exception:
        path = self.stage()
        after = revised_estimate()
        mutate(after)
        return self.commit_expecting_error(path, after=after)

    def test_protected_header_fields_cannot_move(self) -> None:
        cases = {
            "template_id": lambda after: after.update({"template_id": "96274000000000000"}),
            "salesperson_id": lambda after: after.update({"salesperson_id": "96274000000000111"}),
            "currency_id": lambda after: after.update({"currency_id": "96274000000000081"}),
            "notes": lambda after: after.update({"notes": "changed behind our back"}),
            "shipping_charge": lambda after: after.update({"shipping_charge": 99.0}),
            "custom_fields": lambda after: after.update({"custom_fields": [{"label": "x"}]}),
            "invoice_ids": lambda after: after.update({"invoice_ids": ["96274000000000123"]}),
        }
        for name, mutate in cases.items():
            with self.subTest(field=name):
                error = self._read_back_mutation(mutate)
                self.assertIn("Stop and reconcile", str(error))

    def test_an_unchanged_line_field_cannot_move(self) -> None:
        def mutate(after):
            after["line_items"][0]["description"] = "silently rewritten"

        self.assertIn("Stop and reconcile", str(self._read_back_mutation(mutate)))

    def test_a_changed_field_is_exempt_from_the_fingerprint_but_asserted(self) -> None:
        path = self.stage()
        after = revised_estimate(quantity=Decimal("9"))
        error = self.commit_expecting_error(path, after=after)
        self.assertIn("quantity is 9", str(error))

    def test_a_tax_change_replaces_the_frozen_tax_rows_with_an_explicit_check(self) -> None:
        payload = qty_input()
        payload["lines"][0]["fields"]["tax_id"] = {
            "value": ON_HST_TAX, "source": "Rachad: charge Ontario HST on this quote."
        }
        path = self.stage(payload=payload)
        evidence = self.plan_json(path)["live_evidence"]
        self.assertTrue(evidence["tax_changed"])
        self.assertEqual(evidence["expected"]["tax_total"], "65.52")
        self.assertEqual(evidence["expected"]["total"], "569.52")
        self.assertTrue(
            evidence["estimate"]["protected_state"]["taxes_protected_skipped_due_to_tax_change"]
        )
        self.assertNotIn("taxes_protected", evidence["estimate"]["protected_state"])

    def test_an_unknown_tax_id_is_refused_before_any_write(self) -> None:
        payload = qty_input()
        payload["lines"][0]["fields"]["tax_id"] = {"value": "96274000009999999", "source": "x"}
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("not an active tax", str(error))


# ---------------------------------------------------------------------------
# Tax certainty is disclosed, never faked
# ---------------------------------------------------------------------------


class TaxCertaintyTests(RevisionTestCase):
    def group_taxed_estimate(self) -> dict:
        """The same live quote, but carrying the Quebec GST+QST tax GROUP."""
        before = live_estimate()
        line = before["line_items"][0]
        line["tax_id"] = GST_QST_GROUP
        line["tax_name"] = "Gst & Qst"
        line["tax_type"] = "tax_group"
        line["tax_percentage"] = 14.975
        line["line_item_taxes"] = [
            {"tax_id": GST_TAX, "tax_name": "GST (5%)", "tax_amount": 5.04},
            {"tax_id": "96274000001071131", "tax_name": "QST (9.975%)", "tax_amount": 10.05},
        ]
        before["tax_total"] = 15.09
        before["total"] = 115.89
        before["bcy_tax_total"] = 15.09
        before["bcy_total"] = 115.89
        before["taxes"] = [
            {"tax_name": "GST", "tax_amount": 5.04},
            {"tax_name": "QST", "tax_amount": 10.05},
        ]
        return before

    def test_a_tax_group_makes_the_prediction_non_exact_and_unasserted(self) -> None:
        before = self.group_taxed_estimate()
        path = self.stage(before=before)
        expected = self.plan_json(path)["live_evidence"]["expected"]
        self.assertEqual(expected["tax_certainty"], "disclosed_uncertain")
        self.assertFalse(expected["tax_total_asserted"])
        self.assertTrue(
            any("tax_group" in reason for reason in expected["tax_uncertainty_reasons"])
        )
        # The deterministic figures are still asserted exactly.
        self.assertEqual(expected["sub_total"], "504.00")

    def test_an_uncertain_tax_total_is_not_asserted_at_read_back(self) -> None:
        before = self.group_taxed_estimate()
        path = self.stage(before=before)
        after = copy.deepcopy(before)
        after["line_items"][0]["quantity"] = 10.0
        after["line_items"][0]["item_total"] = 504.0
        after["line_items"][0]["line_item_taxes"] = [
            {"tax_id": GST_TAX, "tax_name": "GST (5%)", "tax_amount": 25.2},
            {"tax_id": "96274000001071131", "tax_name": "QST (9.975%)", "tax_amount": 50.28},
        ]
        after["sub_total"] = 504.0
        after["sub_total_exclusive_of_discount"] = 504.0
        # Zoho's own component rounding, deliberately NOT the naive 75.47 this
        # tool would predict. The plan said the tax was not exact, so the commit
        # must accept Zoho's figure instead of failing on it.
        after["tax_total"] = 75.48
        after["total"] = 579.48
        after["bcy_sub_total"] = 504.0
        after["bcy_tax_total"] = 75.48
        after["bcy_total"] = 579.48
        after["taxes"] = [
            {"tax_name": "GST", "tax_amount": 25.2},
            {"tax_name": "QST", "tax_amount": 50.28},
        ]
        after["estimate_url"] = "https://example.invalid/fresh"
        after["last_modified_time"] = "2026-08-13T15:20:00-0400"
        self.assertNotEqual(
            self.plan_json(path)["live_evidence"]["expected"]["tax_total"], "75.48"
        )
        calls = self.commit(path, before=before, after=after)
        self.assertEqual(len(calls["puts"]), 1)
        self.assertEqual(self.lock_record(path)["status"], "committed_verified")

    def test_the_sub_total_is_still_asserted_when_tax_is_uncertain(self) -> None:
        before = live_estimate()
        before["line_items"][0]["tax_type"] = "tax_group"
        before["line_items"][0]["tax_id"] = GST_QST_GROUP
        path = self.stage(before=before)
        after = copy.deepcopy(before)
        after["line_items"][0]["quantity"] = 10.0
        after["line_items"][0]["item_total"] = 503.0
        after["sub_total"] = 503.0
        error = self.commit_expecting_error(path, before=before, after=after)
        self.assertIn("Stop and reconcile", str(error))


# ---------------------------------------------------------------------------
# Source-level containment for the new action
# ---------------------------------------------------------------------------


class SourceContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = Path(draft.__file__).read_text(encoding="utf-8")

    def test_the_revision_adds_exactly_two_parser_actions(self) -> None:
        choices = set(draft.build_parser()._subparsers._group_actions[0].choices)
        self.assertEqual(
            choices,
            {
                "stage-customer", "stage-quote", "commit-customer", "commit-quote",
                "stage-tds-discount-correction", "commit-tds-discount-correction",
                "stage-tds-item9-quantity-correction", "commit-tds-item9-quantity-correction",
                "stage-shm-inv000051-customer", "commit-shm-inv000051-customer",
                # Commissioned 2026-08-13: the one GENERAL in-place revision.
                "stage-estimate-revision", "commit-estimate-revision",
            },
        )
        self.assertEqual(
            {choice for choice in choices if "estimate-revision" in choice},
            {"stage-estimate-revision", "commit-estimate-revision"},
        )

    def test_the_revision_adds_no_new_transport(self) -> None:
        # Still ONE PUT and TWO POSTs: the revision reuses send_estimate_put.
        self.assertEqual(self.source.count('method="PUT"'), 1)
        self.assertEqual(self.source.count('method="POST"'), 2)
        self.assertEqual(self.source.count("urlopen(request"), 3)

    def test_no_mail_delete_or_lifecycle_route_exists(self) -> None:
        for forbidden in (
            'method="DELETE"', 'method="PATCH"', 'method="GET"',
            "/books/v3/estimates/email", "/status/", "markassent", "approve\"",
            "to_mail_ids", "send=true", "smtplib", "Mail.Send", "/submit", "/reject",
            "/converttoinvoice", "/attachment", "/reminder", "/bulk",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)

    def test_no_new_scope_is_required(self) -> None:
        self.assertEqual(draft.ESTIMATE_UPDATE_SCOPE, "ZohoBooks.estimates.UPDATE")
        self.assertIn(draft.ESTIMATE_UPDATE_SCOPE, tool.SCOPES)
        for widened in (
            "ZohoBooks.estimates.DELETE", "ZohoBooks.estimates.ALL",
            "ZohoBooks.fullaccess.all",
        ):
            with self.subTest(scope=widened):
                self.assertNotIn(widened, self.source)

    def test_the_editable_surface_is_exactly_the_commissioned_one(self) -> None:
        self.assertEqual(
            draft.REVISION_HEADER_FIELDS,
            ("reference_number", "date", "expiry_date", "notes", "terms"),
        )
        self.assertEqual(
            draft.REVISION_LINE_FIELDS,
            ("quantity", "rate", "discount", "description", "tax_id"),
        )
        self.assertEqual(draft.REVISION_INPUT_KEYS, {"estimate_id", "reason", "header", "lines"})

    def test_the_put_surface_is_exactly_the_commissioned_one(self) -> None:
        self.assertEqual(
            draft.REVISION_ALLOWED_PUT_KEYS,
            {
                "customer_id", "estimate_number", "reference_number", "date", "expiry_date",
                "notes", "terms", "discount_type", "is_discount_before_tax", "shipping_charge",
                "adjustment", "adjustment_description", "template_id", "salesperson_id",
                "currency_id", "exchange_rate", "line_items",
            },
        )
        self.assertEqual(
            set(draft.REVISION_LINE_PUT_KEYS),
            {
                "line_item_id", "item_id", "name", "description", "quantity", "rate",
                "unit", "discount", "tax_id", "item_order",
            },
        )

    def test_the_existing_fixed_corrections_are_untouched(self) -> None:
        self.assertEqual(
            set(draft.CORRECTION_TARGETS),
            {"96274000001559037", "96274000001558043"},
        )
        self.assertEqual(draft.CORRECTION_KIND, "tds_discount_correction")
        self.assertEqual(draft.ITEM9_KIND, "tds_item9_quantity_correction")
        self.assertEqual(draft.ITEM9_ESTIMATE_ID, "96274000001559037")
        self.assertEqual(draft.TDS_LINE_DISCOUNT, "10%")
        self.assertEqual(
            draft.ALLOWED_POSTS,
            {"customer": "/books/v3/contacts", "quote": "/books/v3/estimates"},
        )

    def test_the_approval_is_judged_by_the_one_shared_detector(self) -> None:
        """2026-08-21: no hand-written comparator -- the shared owner-authority
        module (a verbatim copy of Aze's detector) decides."""
        self.assertIn("owner_authority.require_owner_go(", self.source)
        self.assertNotIn("approval != APPROVAL_WORD", self.source)
        self.assertNotIn("approval.strip()", self.source)
        self.assertNotIn("approval.upper()", self.source)

    def test_the_tool_declares_no_mail_transport(self) -> None:
        self.assertIn("no mail transport", draft.REVISION_RISK_NOTE)


if __name__ == "__main__":
    unittest.main()
