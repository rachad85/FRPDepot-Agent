"""Safety tests for the QT-000029 Item 9 quantity correction.

Commissioned by Rachad on 2026-08-11 after Jasmin Leblanc (Troy Dualam
Services) wrote "Item 9 - 1 instead 4" against the already-sent QT-000029.

NO TEST IN THIS FILE PERFORMS A LIVE CALL. Every read is a fake api_get and
every write is a fake urlopen; the real transports are asserted never to run.
"""

from __future__ import annotations

import argparse
import copy
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import zoho_tool as tool
import zoho_customer_quote_tool as draft

QT29 = "96274000001559037"
QT30 = "96274000001558043"
ORG_ID = "96274000000000001"
TARGET_LINE_ID = "96274000001559046"
TARGET_ITEM_ID = "96274000000030497"
TARGET_INDEX = 8
OTHER_LINE_BASE = 96274000001559038


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def source_lines() -> list[dict]:
    plan = json.loads(
        Path(draft.ITEM9_TARGET["source_plan"]).read_text(encoding="utf-8")
    )
    return plan["payload"]["line_items"]


def line_id_for(index: int) -> str:
    return TARGET_LINE_ID if index == TARGET_INDEX else str(OTHER_LINE_BASE + index)


def live_estimate() -> dict:
    """QT-000029 exactly as it stands after the 2026-08-10 discount correction.

    Built from the real immutable source plan, so the item ids, names,
    quantities and rates are the live ones.
    """
    target = draft.ITEM9_TARGET
    lines = []
    gross_total = discount_total = net_total = Decimal("0")
    for index, source in enumerate(source_lines()):
        quantity = Decimal(str(source["quantity"]))
        rate = Decimal(str(source["rate"]))
        gross, discount, net = draft.line_figures(quantity, rate)
        gross_total += gross
        discount_total += discount
        net_total += net
        lines.append({
            "line_item_id": line_id_for(index),
            "item_id": source["item_id"],
            "name": source["name"],
            "description": source.get("description", ""),
            "quantity": source["quantity"],
            "quantity_formatted": format(quantity, "f"),
            "rate": source["rate"],
            "unit": source["unit"],
            "tax_id": draft.TDS_GST_QST_TAX_ID,
            "tax_name": "Gst & Qst",
            "item_order": index + 1,
            # Zoho echoes a real percentage as the bare number beside a correct
            # amount; the amount is what proves it is 10% and not CAD 10.00.
            "discount": 10.0,
            "discount_amount": float(discount),
            "item_total": float(net),
            "line_item_taxes": [
                {"tax_id": "96274000000035512", "tax_name": "GST (5%)",
                 "tax_percentage": 5.0, "tax_amount": float(net * Decimal("0.05"))},
                {"tax_id": "96274000001071131", "tax_name": "QST (9.975%)",
                 "tax_percentage": 9.975, "tax_amount": float(net * Decimal("0.09975"))},
            ],
        })
    combined, gst, qst = draft.tax_on(net_total)
    assert net_total == target["current_sub_total"], net_total
    assert combined == target["current_tax_total"], combined
    return {
        "estimate_id": QT29,
        "estimate_number": target["estimate_number"],
        "reference_number": target["reference_number"],
        "customer_id": target["customer_id"],
        "customer_name": target["customer_name"],
        "status": "sent",
        "date": "2026-08-10",
        "expiry_date": "",
        "notes": "Thank you for your business.",
        "terms": "",
        "currency_id": "96274000000000097",
        "currency_code": "CAD",
        "template_id": "96274000000000123",
        "salesperson_id": "",
        "discount_type": "item_level",
        "is_discount_before_tax": True,
        "discount_total": float(discount_total),
        "discount_percent": 10.0,
        "sub_total": float(net_total),
        "tax_total": float(combined),
        "total": float(net_total + combined),
        "bcy_sub_total": float(net_total),
        "bcy_tax_total": float(combined),
        "bcy_total": float(net_total + combined),
        "uninvoiced_amount": float(net_total + combined),
        "taxes": [
            {"tax_name": "GST", "tax_amount": float(gst)},
            {"tax_name": "QST", "tax_amount": float(qst)},
        ],
        "estimate_url": "https://example.invalid/staged-secure-estimate-url",
        "line_items": lines,
        "last_modified_time": "2026-08-10T23:41:02-0400",
    }


def corrected_estimate(before: dict | None = None) -> dict:
    """What Zoho should return once the single quantity lands."""
    target = draft.ITEM9_TARGET
    before = before or live_estimate()
    after = copy.deepcopy(before)
    gross_total = discount_total = net_total = Decimal("0")
    for index, line in enumerate(after["line_items"]):
        if index == TARGET_INDEX:
            line["quantity"] = float(target["new_quantity"])
            line["quantity_formatted"] = format(target["new_quantity"], "f")
        quantity = Decimal(str(line["quantity"]))
        rate = Decimal(str(line["rate"]))
        gross, discount, net = draft.line_figures(quantity, rate)
        gross_total += gross
        discount_total += discount
        net_total += net
        line["discount_amount"] = float(discount)
        line["item_total"] = float(net)
        line["line_item_taxes"][0]["tax_amount"] = float(net * Decimal("0.05"))
        line["line_item_taxes"][1]["tax_amount"] = float(net * Decimal("0.09975"))
    combined, gst, qst = draft.tax_on(net_total)
    after["discount_total"] = float(discount_total)
    after["sub_total"] = float(net_total)
    after["tax_total"] = float(combined)
    after["total"] = float(net_total + combined)
    after["bcy_sub_total"] = float(net_total)
    after["bcy_tax_total"] = float(combined)
    after["bcy_total"] = float(net_total + combined)
    after["uninvoiced_amount"] = float(net_total + combined)
    after["taxes"] = [
        {"tax_name": "GST", "tax_amount": float(gst)},
        {"tax_name": "QST", "tax_amount": float(qst)},
    ]
    after["estimate_url"] = "https://example.invalid/fresh-secure-estimate-url"
    after["last_modified_time"] = "2026-08-11T13:04:19-0400"
    assert Decimal(str(after["total"])) == target["expected_total"]
    return after


def fake_vault(scopes=None) -> dict:
    return {
        "api_domain": tool.EXPECTED_API_DOMAIN,
        "books_organization_id": ORG_ID,
        "scopes": list(tool.SCOPES) if scopes is None else list(scopes),
    }


class Item9TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.plan_dir = Path(self._temp.name).resolve() / "zoho_plans"
        self.plan_dir.mkdir(parents=True)
        self.addCleanup(self._temp.cleanup)
        self._clock = [1000.0]

    def _tick(self) -> float:
        self._clock[0] += 0.5
        return self._clock[0]

    def stage(self, before: dict | None = None, reads: list | None = None) -> Path:
        before = before or live_estimate()
        if reads is None:
            reads = [copy.deepcopy(before) for _ in range(draft.ITEM9_REHEARSAL_OBSERVATIONS)]
        vault = fake_vault()
        pending = list(reads)

        def fake_api_get(access_token, api_domain, path):
            if not pending:
                raise AssertionError("unexpected extra GET")
            return {"code": 0, "estimate": pending.pop(0)}

        existing = set(self.plan_dir.glob("*.json"))
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool, "api_get", side_effect=fake_api_get
        ), patch.object(draft, "pause"), patch.object(
            draft, "monotonic_seconds", side_effect=self._tick
        ), patch.object(
            draft, "urlopen", side_effect=AssertionError("staging must never write")
        ):
            draft.command_stage_item9_quantity_correction(argparse.Namespace())
        created = set(self.plan_dir.glob("*.json")) - existing
        self.assertEqual(len(created), 1)
        return created.pop()

    def plan_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def write_plan(self, path: Path, plan: dict) -> None:
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def rehash(self, path: Path, plan: dict) -> Path:
        core = dict(plan)
        core.pop("sha256", None)
        plan["sha256"] = draft.digest_for(core)
        self.write_plan(path, plan)
        return path

    def commit(
        self,
        plan_path: Path,
        *,
        approval: str = draft.APPROVAL_WORD,
        reads: list | None = None,
        put_result=None,
        put_error: Exception | None = None,
        scopes=None,
        organization_id: str = ORG_ID,
    ) -> dict:
        before = live_estimate()
        after = corrected_estimate(before)
        if reads is None:
            reads = [
                {"code": 0, "estimate": before},
                {"code": 0, "estimate": after},
            ]
        vault = fake_vault(scopes)
        vault["books_organization_id"] = organization_id
        calls: dict = {"puts": [], "gets": 0}
        self.last_calls = calls

        def fake_api_get(access_token, api_domain, path):
            calls["gets"] += 1
            if not reads:
                raise AssertionError("unexpected extra GET")
            return reads.pop(0)

        def fake_urlopen(request, timeout):
            calls["puts"].append({
                "method": request.get_method(),
                "url": request.full_url,
                "payload": json.loads(request.data.decode("utf-8")),
                "lock_exists": draft.correction_lock_path(
                    self.plan_json(plan_path)["sha256"]
                ).exists(),
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
            draft.zoho_tool, "api_get", side_effect=fake_api_get
        ), patch.object(draft, "urlopen", side_effect=fake_urlopen):
            draft.command_commit_item9_quantity_correction(
                argparse.Namespace(plan=str(plan_path), approval=approval)
            )
        return calls

    def commit_expecting_error(self, plan_path: Path, **kwargs):
        with self.assertRaises(draft.DraftToolError) as caught:
            self.commit(plan_path, **kwargs)
        return caught.exception

    def lock_record(self, plan_path: Path) -> dict:
        with patch.object(draft, "PLAN_DIR", self.plan_dir):
            lock = draft.correction_lock_path(self.plan_json(plan_path)["sha256"])
        return json.loads(lock.read_text(encoding="utf-8")) if lock.exists() else {}

    def lock_exists(self, plan_path: Path) -> bool:
        with patch.object(draft, "PLAN_DIR", self.plan_dir):
            return draft.correction_lock_path(self.plan_json(plan_path)["sha256"]).exists()

    def staged_drift(self, mutate) -> Path:
        """Stage a clean plan, then commit against a mutated live estimate."""
        path = self.stage()
        drifted = live_estimate()
        mutate(drifted)
        return path, drifted


# ---------------------------------------------------------------------------
# Fixed target and arithmetic
# ---------------------------------------------------------------------------


class FixedTargetTests(Item9TestCase):
    def test_the_commissioned_target_is_exact(self) -> None:
        target = draft.ITEM9_TARGET
        self.assertEqual(draft.ITEM9_ESTIMATE_ID, QT29)
        self.assertEqual(target["estimate_number"], "QT-000029")
        self.assertEqual(target["reference_number"], "PO 104750 / J6276")
        self.assertEqual(target["customer_id"], "96274000000060019")
        self.assertEqual(target["customer_name"], "Troy Dualam Services Inc.")
        self.assertEqual(target["status"], "sent")
        self.assertEqual(target["line_count"], 11)
        self.assertEqual(target["line_item_id"], TARGET_LINE_ID)
        self.assertEqual(target["item_id"], TARGET_ITEM_ID)
        self.assertEqual(target["item_order"], 9)
        self.assertEqual(target["item_name"], 'FRP ELBOW-12"/150PSI/D411')
        self.assertEqual(target["current_quantity"], Decimal("4"))
        self.assertEqual(target["new_quantity"], Decimal("1"))
        self.assertEqual(target["rate"], Decimal("810.00"))
        self.assertEqual(target["line_discount"], "10%")
        self.assertEqual(target["tax_id"], "96274000001071139")

    def test_required_start_is_the_verified_discount_correction_result(self) -> None:
        target = draft.ITEM9_TARGET
        prior = draft.CORRECTION_TARGETS[QT29]
        self.assertEqual(target["current_sub_total"], prior["corrected_sub_total"])
        self.assertEqual(target["current_tax_total"], prior["corrected_tax_total"])
        self.assertEqual(target["current_total"], prior["corrected_total"])
        self.assertEqual(target["current_total"], Decimal("13680.38"))

    def test_expected_totals_match_the_commissioned_figures(self) -> None:
        target = draft.ITEM9_TARGET
        self.assertEqual(target["expected_sub_total"], Decimal("9711.57"))
        self.assertEqual(target["expected_gst"], Decimal("485.58"))
        self.assertEqual(target["expected_qst"], Decimal("968.73"))
        self.assertEqual(target["expected_tax_total"], Decimal("1454.31"))
        self.assertEqual(target["expected_total"], Decimal("11165.88"))
        self.assertEqual(
            target["expected_gst"] + target["expected_qst"], target["expected_tax_total"]
        )
        self.assertEqual(
            target["expected_sub_total"] + target["expected_tax_total"],
            target["expected_total"],
        )

    def test_the_only_reachable_estimate_is_qt29(self) -> None:
        key, target = draft.require_item9_target(QT29)
        self.assertEqual(key, QT29)
        self.assertIs(target, draft.ITEM9_TARGET)
        for other in (
            QT30, "", None, "  ", "1", QT29 + "0", "0" + QT29, "abc",
            f"{QT29} ; DROP", "96274000001559046", 96274000001559037,
        ):
            with self.subTest(other=other):
                if other == 96274000001559037:
                    continue
                with self.assertRaisesRegex(draft.DraftToolError, "REFUSED"):
                    draft.require_item9_target(other)

    def test_stage_takes_no_estimate_id_or_business_value(self) -> None:
        parser = draft.build_parser()
        args = parser.parse_args(["stage-tds-item9-quantity-correction"])
        self.assertEqual(
            set(vars(args)) - {"command", "func"}, set(),
            "stage must accept no arguments at all",
        )
        self.assertIs(args.func, draft.command_stage_item9_quantity_correction)
        for extra in (["--estimate-id", QT30], ["--quantity", "2"], ["--plan", "x"]):
            with self.subTest(extra=extra):
                with self.assertRaises(SystemExit):
                    parser.parse_args(["stage-tds-item9-quantity-correction", *extra])

    def test_commit_parser_takes_only_plan_and_approval(self) -> None:
        parser = draft.build_parser()
        args = parser.parse_args([
            "commit-tds-item9-quantity-correction", "--plan", "p", "--approval", "APPROVED"
        ])
        self.assertEqual(set(vars(args)) - {"command", "func"}, {"plan", "approval"})
        self.assertIs(args.func, draft.command_commit_item9_quantity_correction)

    def test_independent_arithmetic_of_the_change(self) -> None:
        gross_before, discount_before, net_before = draft.line_figures(
            Decimal("4"), Decimal("810.00")
        )
        gross_after, discount_after, net_after = draft.line_figures(
            Decimal("1"), Decimal("810.00")
        )
        self.assertEqual((gross_before, discount_before, net_before),
                         (Decimal("3240.00"), Decimal("324.00"), Decimal("2916.00")))
        self.assertEqual((gross_after, discount_after, net_after),
                         (Decimal("810.00"), Decimal("81.00"), Decimal("729.00")))
        target = draft.ITEM9_TARGET
        self.assertEqual(
            target["current_sub_total"] - (net_before - net_after),
            target["expected_sub_total"],
        )
        combined, gst, qst = draft.tax_on(target["expected_sub_total"])
        self.assertEqual(combined, target["expected_tax_total"])
        self.assertEqual(gst, target["expected_gst"])
        self.assertEqual(qst, target["expected_qst"])


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


class StageTests(Item9TestCase):
    def test_stage_is_get_only_and_writes_one_plan(self) -> None:
        before = live_estimate()
        vault = fake_vault()
        receipts = []
        reads = [copy.deepcopy(before) for _ in range(draft.ITEM9_REHEARSAL_OBSERVATIONS)]
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt", side_effect=lambda a, e: receipts.append((a, e))
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool, "api_get",
            side_effect=lambda *a: {"code": 0, "estimate": reads.pop(0)},
        ) as fake_get, patch.object(draft, "pause") as fake_pause, patch.object(
            draft, "monotonic_seconds", side_effect=self._tick
        ), patch.object(
            draft, "urlopen", side_effect=AssertionError("staging must never write")
        ):
            draft.command_stage_item9_quantity_correction(argparse.Namespace())
        self.assertEqual(fake_get.call_count, draft.ITEM9_REHEARSAL_OBSERVATIONS)
        self.assertEqual(fake_pause.call_count, draft.ITEM9_REHEARSAL_OBSERVATIONS - 1)
        for call in fake_get.call_args_list:
            self.assertIn(f"/books/v3/estimates/{QT29}", call.args[2])
        plans = list(self.plan_dir.glob("*.json"))
        self.assertEqual(len(plans), 1)
        self.assertIn(draft.ITEM9_KIND, plans[0].name)
        self.assertEqual(len(receipts), 1)
        self.assertIn("writes=0", receipts[0][1])
        self.assertIn("GET_ONLY", receipts[0][1])

    def test_staged_plan_changes_exactly_one_quantity(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        payload = plan["live_evidence"]["put_payload"]
        before = live_estimate()
        self.assertEqual(len(payload["line_items"]), 11)
        for index, (line, source) in enumerate(zip(payload["line_items"], before["line_items"])):
            self.assertEqual(line["line_item_id"], source["line_item_id"])
            self.assertEqual(line["item_id"], source["item_id"])
            self.assertEqual(line["name"], source["name"])
            self.assertEqual(line["rate"], source["rate"])
            self.assertEqual(line["discount"], "10%")
            self.assertEqual(line["tax_id"], draft.TDS_GST_QST_TAX_ID)
            if index == TARGET_INDEX:
                self.assertEqual(line["quantity"], 1)
                self.assertNotIsInstance(line["quantity"], bool)
            else:
                self.assertEqual(line["quantity"], source["quantity"])
        self.assertEqual(payload["estimate_number"], "QT-000029")
        self.assertEqual(payload["reference_number"], "PO 104750 / J6276")
        self.assertEqual(payload["customer_id"], draft.TDS_CUSTOMER_ID)
        self.assertTrue(set(payload) <= draft.CORRECTION_ALLOWED_PUT_KEYS)

    def test_staged_plan_states_the_approved_totals(self) -> None:
        plan = self.plan_json(self.stage())
        expected = plan["live_evidence"]["expected"]
        self.assertEqual(expected["sub_total"], "9711.57")
        self.assertEqual(expected["tax_total"], "1454.31")
        self.assertEqual(expected["tax_gst"], "485.58")
        self.assertEqual(expected["tax_qst"], "968.73")
        self.assertEqual(expected["total"], "11165.88")
        self.assertEqual(expected["target"]["net_delta"], "2187.00")
        self.assertEqual(plan["live_evidence"]["current"]["total"], "13680.38")
        changed = [row for row in expected["lines"] if row["changed"]]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["line_item_id"], TARGET_LINE_ID)
        self.assertEqual(Decimal(changed[0]["quantity_before"]), Decimal("4"))
        self.assertEqual(changed[0]["quantity"], "1")
        self.assertEqual(changed[0]["item_total"], "729.00")

    def test_staged_plan_is_immutable_and_disclosed(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        self.assertEqual(plan["tool"], draft.TOOL_NAME)
        self.assertEqual(plan["kind"], draft.ITEM9_KIND)
        self.assertEqual(plan["schema_version"], draft.ITEM9_SCHEMA_VERSION)
        self.assertEqual(plan["approval_required"], "APPROVED")
        self.assertRegex(plan["nonce"], r"^[0-9a-f]{32}$")
        self.assertRegex(plan["sha256"], r"^[0-9a-f]{64}$")
        created = draft.parse_plan_time(plan["created_utc"], "c")
        expires = draft.parse_plan_time(plan["expires_utc"], "e")
        self.assertEqual((expires - created).total_seconds(), 24 * 3600)
        self.assertTrue(plan["risk"]["atomic"])
        self.assertTrue(plan["risk"]["single_put"])
        self.assertFalse(plan["risk"]["email_sent"])
        self.assertFalse(plan["risk"]["write_attempted"])
        self.assertEqual(plan["risk"]["status_unchanged"], "sent")
        self.assertEqual(plan["risk"]["note"], draft.ITEM9_RISK_NOTE)
        self.assertFalse(plan["live_evidence"]["email_sent"])
        self.assertEqual(plan["live_evidence"]["status_unchanged"], "sent")
        self.assertEqual(
            plan["live_evidence"]["put_endpoint"], f"PUT /books/v3/estimates/{QT29}"
        )

    def test_staged_plan_carries_the_source_evidence(self) -> None:
        plan = self.plan_json(self.stage())
        sources = plan["source_evidence"]
        self.assertEqual(set(sources), {"source_plan", "customer_request"})
        self.assertEqual(
            sources["source_plan"]["sha256"], draft.ITEM9_TARGET["source_plan_sha256"]
        )
        self.assertEqual(sources["customer_request"], draft.ITEM9_CUSTOMER_REQUEST)
        self.assertEqual(sources["customer_request"]["from"], "Jasmin Leblanc")
        self.assertEqual(sources["customer_request"]["received"], "2026-08-11 12:29")
        self.assertIn("1 instead 4", sources["customer_request"]["quote"])
        self.assertEqual(sources["customer_request"]["from_quantity"], "4")
        self.assertEqual(sources["customer_request"]["to_quantity"], "1")

    def test_staged_plan_carries_the_bounded_stable_rehearsal(self) -> None:
        plan = self.plan_json(self.stage())
        rehearsal = plan["stable_state_evidence"]
        self.assertEqual(set(rehearsal), set(draft.ITEM9_REHEARSAL_KEYS))
        self.assertEqual(rehearsal["observations"], draft.ITEM9_REHEARSAL_OBSERVATIONS)
        self.assertEqual(rehearsal["max_seconds"], "30")
        self.assertEqual(rehearsal["interval_seconds"], "2")
        self.assertTrue(rehearsal["stable"])
        self.assertEqual(rehearsal["volatile_keys_observed"], [])
        self.assertEqual(
            len(rehearsal["observation_prewrite_sha256"]), draft.ITEM9_REHEARSAL_OBSERVATIONS
        )
        self.assertEqual(
            set(rehearsal["observation_prewrite_sha256"]), {rehearsal["prewrite_sha256"]}
        )
        self.assertLessEqual(Decimal(rehearsal["elapsed_seconds"]), Decimal("30"))

    def test_rehearsal_tolerates_only_the_volatile_estimate_url(self) -> None:
        before = live_estimate()
        second = copy.deepcopy(before)
        second["estimate_url"] = "https://example.invalid/rotated-1"
        third = copy.deepcopy(before)
        third["estimate_url"] = "https://example.invalid/rotated-2"
        plan = self.plan_json(self.stage(before=before, reads=[before, second, third]))
        self.assertEqual(plan["stable_state_evidence"]["volatile_keys_observed"], ["estimate_url"])

    def test_rehearsal_refuses_any_other_moving_field(self) -> None:
        for key, value in (
            ("total", 99999.99), ("status", "draft"), ("reference_number", "PO OTHER"),
            ("last_modified_time", "2026-08-11T14:00:00-0400"), ("notes", "changed"),
        ):
            with self.subTest(key=key):
                before = live_estimate()
                moved = copy.deepcopy(before)
                moved[key] = value
                with self.assertRaisesRegex(draft.DraftToolError, "not stable"):
                    self.stage(before=before, reads=[before, moved, copy.deepcopy(before)])

    def test_rehearsal_refuses_a_moved_line_field(self) -> None:
        before = live_estimate()
        moved = copy.deepcopy(before)
        moved["line_items"][TARGET_INDEX]["quantity"] = 3.0
        with self.assertRaisesRegex(draft.DraftToolError, "not stable"):
            self.stage(before=before, reads=[before, moved, copy.deepcopy(before)])

    def test_rehearsal_window_is_bounded(self) -> None:
        before = live_estimate()
        reads = [copy.deepcopy(before) for _ in range(draft.ITEM9_REHEARSAL_OBSERVATIONS)]
        vault = fake_vault()
        clock = [0.0]

        def slow_clock():
            clock[0] += 100.0
            return clock[0]

        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool, "api_get",
            side_effect=lambda *a: {"code": 0, "estimate": reads.pop(0)},
        ), patch.object(draft, "pause"), patch.object(
            draft, "monotonic_seconds", side_effect=slow_clock
        ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
            with self.assertRaisesRegex(draft.DraftToolError, "window"):
                draft.command_stage_item9_quantity_correction(argparse.Namespace())
        self.assertEqual(list(self.plan_dir.glob("*.json")), [])

    def test_stage_never_reaches_the_write_transport(self) -> None:
        with patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
            path = self.stage()
        self.assertTrue(path.exists())


class StageRefusalTests(Item9TestCase):
    """Every live-state defect refuses before a plan exists."""

    def stage_expecting(self, mutate, pattern: str) -> None:
        before = live_estimate()
        mutate(before)
        with self.assertRaisesRegex(draft.DraftToolError, pattern):
            self.stage(before=before)
        self.assertEqual(list(self.plan_dir.glob("*.json")), [])

    def test_wrong_status_is_refused(self) -> None:
        for status in ("draft", "accepted", "declined", "expired", "invoiced", "", "Sent"):
            with self.subTest(status=status):
                self.stage_expecting(
                    lambda e, s=status: e.update(status=s), "status"
                )

    def test_wrong_identity_is_refused(self) -> None:
        for key, value in (
            ("estimate_id", QT30),
            ("estimate_number", "QT-000030"),
            ("reference_number", "PO 104751 / J6282"),
            ("customer_id", "96274000000060020"),
        ):
            with self.subTest(key=key):
                if key == "estimate_id":
                    # get_estimate itself rejects a mismatched record.
                    before = live_estimate()
                    before[key] = value
                    with self.assertRaises(draft.DraftToolError):
                        self.stage(before=before)
                    continue
                self.stage_expecting(lambda e, k=key, v=value: e.update({k: v}), key)

    def test_wrong_starting_totals_are_refused(self) -> None:
        for key in ("sub_total", "tax_total", "total"):
            with self.subTest(key=key):
                self.stage_expecting(
                    lambda e, k=key: e.update({k: float(Decimal(str(e[k])) + Decimal("0.01"))}),
                    "required starting",
                )

    def test_wrong_line_count_is_refused(self) -> None:
        self.stage_expecting(lambda e: e["line_items"].pop(), "exactly 11 live lines")
        self.stage_expecting(
            lambda e: e["line_items"].append(copy.deepcopy(e["line_items"][0])),
            "exactly 11 live lines",
        )

    def test_missing_or_duplicated_target_line_is_refused(self) -> None:
        self.stage_expecting(
            lambda e: e["line_items"][TARGET_INDEX].update(line_item_id="96274000009999999"),
            "exactly one line with line_item_id",
        )
        self.stage_expecting(
            lambda e: e["line_items"][0].update(line_item_id=TARGET_LINE_ID),
            "repeats line_item_id|exactly one line",
        )

    def test_target_field_mismatches_are_refused(self) -> None:
        cases = (
            ("item_id", "96274000000030481", "item_id"),
            ("name", "FRP ELBOW-10\"/150PSI/D411", "name"),
            ("item_order", 8, "item_order"),
        )
        for key, value, pattern in cases:
            with self.subTest(key=key):
                self.stage_expecting(
                    lambda e, k=key, v=value: e["line_items"][TARGET_INDEX].update({k: v}),
                    pattern,
                )

    def test_a_target_quantity_or_rate_that_is_not_the_commissioned_one_is_refused(self) -> None:
        """Internally consistent lines, so only the target pin can refuse them."""
        def rewrite(estimate, quantity, rate):
            line = estimate["line_items"][TARGET_INDEX]
            gross, discount, net = draft.line_figures(quantity, rate)
            line.update(
                quantity=float(quantity), quantity_formatted=format(quantity, "f"),
                rate=float(rate), discount_amount=float(discount), item_total=float(net),
            )
        for quantity, rate, pattern in (
            (Decimal("3"), Decimal("810.00"), r"quantity is 3\.0, not the commissioned 4"),
            (Decimal("1"), Decimal("810.00"), r"quantity is 1\.0, not the commissioned 4"),
            (Decimal("4"), Decimal("800.00"), r"rate is 800\.0, not the commissioned 810\.00"),
        ):
            with self.subTest(quantity=quantity, rate=rate):
                self.stage_expecting(
                    lambda e, q=quantity, r=rate: rewrite(e, q, r), pattern
                )

    def test_a_flat_cad_discount_is_refused(self) -> None:
        def flatten(estimate):
            line = estimate["line_items"][0]
            line["discount_amount"] = 10.0
            line["item_total"] = float(
                Decimal(str(line["quantity"])) * Decimal(str(line["rate"])) - Decimal("10")
            )
        self.stage_expecting(flatten, "not a percentage discount|not the exact 10%")

    def test_a_wrong_discount_percentage_is_refused(self) -> None:
        self.stage_expecting(
            lambda e: e["line_items"][0].update(discount=15.0), "not 10 percent"
        )
        self.stage_expecting(
            lambda e: e["line_items"][0].update(discount="12.5%"), "not 10 percent"
        )

    def test_a_foreign_tax_group_is_refused(self) -> None:
        self.stage_expecting(
            lambda e: e["line_items"][3].update(tax_id="96274000000035512"), "GST\\+QST group"
        )

    def test_entity_level_or_post_tax_discount_is_refused(self) -> None:
        self.stage_expecting(lambda e: e.update(discount_type="entity_level"), "item_level")
        self.stage_expecting(
            lambda e: e.update(is_discount_before_tax=False), "is_discount_before_tax"
        )

    def test_a_line_that_left_the_source_plan_is_refused(self) -> None:
        self.stage_expecting(
            lambda e: e["line_items"][2].update(item_id="96274000000019583"),
            "original source plan",
        )
        self.stage_expecting(
            lambda e: e["line_items"][4].update(name="Something else"), "original source plan"
        )

    def test_a_non_target_quantity_change_is_refused(self) -> None:
        # A different quantity than the source plan's cannot be staged at all.
        self.stage_expecting(
            lambda e: e["line_items"][5].update(
                quantity=5.0,
                discount_amount=float(draft.line_figures(Decimal("5"), Decimal("727.2"))[1]),
                item_total=float(draft.line_figures(Decimal("5"), Decimal("727.2"))[2]),
            ),
            "original source plan|required starting",
        )

    def test_a_missing_or_duplicated_item_order_is_refused(self) -> None:
        self.stage_expecting(lambda e: e["line_items"][2].update(item_order="3"), "item_order")
        self.stage_expecting(lambda e: e["line_items"][2].update(item_order=4), "item_order")


# ---------------------------------------------------------------------------
# Commit -- the happy path and the approval word
# ---------------------------------------------------------------------------


class CommitTests(Item9TestCase):
    def test_commit_puts_once_and_verifies_response_and_fresh_read(self) -> None:
        path = self.stage()
        calls = self.commit(path)
        self.assertEqual(len(calls["puts"]), 1)
        self.assertEqual(calls["puts"][0]["method"], "PUT")
        self.assertEqual(
            calls["puts"][0]["url"],
            f"{tool.EXPECTED_API_DOMAIN}/books/v3/estimates/{QT29}?organization_id={ORG_ID}",
        )
        # One GET before the PUT and one fresh GET after it.
        self.assertEqual(calls["gets"], 2)
        self.assertTrue(calls["puts"][0]["lock_exists"])
        record = self.lock_record(path)
        self.assertEqual(record["status"], "committed_verified")
        self.assertEqual(record["kind"], draft.ITEM9_KIND)
        self.assertTrue(record["no_retry"])

    def test_the_committed_payload_changes_only_item_9(self) -> None:
        path = self.stage()
        payload = self.commit(path)["puts"][0]["payload"]
        before = live_estimate()
        self.assertEqual(len(payload["line_items"]), 11)
        for index, (line, source) in enumerate(zip(payload["line_items"], before["line_items"])):
            self.assertEqual(line["line_item_id"], source["line_item_id"])
            self.assertEqual(line["item_id"], source["item_id"])
            self.assertEqual(Decimal(str(line["rate"])), Decimal(str(source["rate"])))
            self.assertEqual(line["discount"], "10%")
            want = Decimal("1") if index == TARGET_INDEX else Decimal(str(source["quantity"]))
            self.assertEqual(Decimal(str(line["quantity"])), want)
        for key in ("status", "send", "email", "to_mail_ids", "accept", "convert",
                    "currency_id", "exchange_rate", "adjustment", "shipping_charge",
                    "custom_fields", "template_id", "estimate_id"):
            self.assertNotIn(key, payload)

    def test_approval_must_be_exact_and_precedes_lock_vault_and_network(self) -> None:
        path = self.stage()
        digest = self.plan_json(path)["sha256"]
        for approval in ("approved", "Approved", " APPROVED", "APPROVED ", "APPROVED\n",
                         "APPROVED\t", "", "YES", "OK", digest, f"APPROVED {digest}",
                         None, True, 1, ["APPROVED"]):
            with self.subTest(approval=approval):
                with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
                    draft.zoho_tool, "load_vault", side_effect=AssertionError("vault must not open")
                ), patch.object(
                    draft.zoho_tool, "refresh_access_token", side_effect=AssertionError("no token")
                ), patch.object(
                    draft.zoho_tool, "api_get", side_effect=AssertionError("no GET")
                ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
                    with self.assertRaisesRegex(draft.DraftToolError, "APPROVED"):
                        draft.command_commit_item9_quantity_correction(
                            argparse.Namespace(plan=str(path), approval=approval)
                        )
        with patch.object(draft, "PLAN_DIR", self.plan_dir):
            self.assertFalse(draft.correction_lock_path(digest).exists())

    def test_missing_update_scope_is_refused_before_the_put(self) -> None:
        path = self.stage()
        scopes = [s for s in tool.SCOPES if s != draft.ESTIMATE_UPDATE_SCOPE]
        error = self.commit_expecting_error(path, scopes=scopes)
        self.assertIn(draft.ESTIMATE_UPDATE_SCOPE, str(error))
        self.assertEqual(self.last_calls["puts"], [])
        self.assertFalse(self.lock_exists(path))

    def test_a_different_organization_is_refused_before_the_put(self) -> None:
        path = self.stage()
        error = self.commit_expecting_error(path, organization_id="96274000000000002")
        self.assertIn("organization", str(error))
        self.assertEqual(self.last_calls["puts"], [])
        self.assertFalse(self.lock_exists(path))


# ---------------------------------------------------------------------------
# Commit -- plan integrity
# ---------------------------------------------------------------------------


class PlanIntegrityTests(Item9TestCase):
    def refuse(self, path: Path, pattern: str = "") -> None:
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", side_effect=AssertionError("vault must not open")
        ), patch.object(
            draft.zoho_tool, "api_get", side_effect=AssertionError("no GET")
        ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
            with self.assertRaisesRegex(draft.DraftToolError, pattern or ".") as caught:
                draft.command_commit_item9_quantity_correction(
                    argparse.Namespace(plan=str(path), approval="APPROVED")
                )
        self.assertFalse(self.lock_exists(path))
        return caught.exception

    def test_a_touched_plan_fails_its_hash(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["expected"]["total"] = "1.00"
        self.write_plan(path, plan)
        self.refuse(path, "hash check failed")

    def test_a_rehashed_semantic_change_still_fails_re_derivation(self) -> None:
        cases = (
            ("total", lambda p: p["live_evidence"]["expected"].update(total="1.00")),
            ("sub_total", lambda p: p["live_evidence"]["expected"].update(sub_total="1.00")),
            ("endpoint", lambda p: p["live_evidence"].update(
                put_endpoint=f"PUT /books/v3/estimates/{QT30}")),
            ("payload_quantity", lambda p: p["live_evidence"]["put_payload"]["line_items"]
                [TARGET_INDEX].update(quantity=2)),
            ("payload_rate", lambda p: p["live_evidence"]["put_payload"]["line_items"]
                [0].update(rate=1.0)),
            ("payload_discount", lambda p: p["live_evidence"]["put_payload"]["line_items"]
                [0].update(discount=10)),
            ("payload_extra_key", lambda p: p["live_evidence"]["put_payload"].update(
                status="sent")),
            ("payload_drop_line", lambda p: p["live_evidence"]["put_payload"]["line_items"].pop()),
            ("payload_reorder", lambda p: p["live_evidence"]["put_payload"]["line_items"].reverse()),
            ("protected_hash", lambda p: p["live_evidence"]["estimate"].update(
                protected_state_sha256="0" * 64)),
            ("change_to", lambda p: p["live_evidence"]["change"].update(to="2")),
            ("customer", lambda p: p["live_evidence"]["estimate"].update(
                customer_id="96274000000060020")),
            ("status", lambda p: p["live_evidence"]["estimate"].update(status="draft")),
        )
        for label, mutate in cases:
            with self.subTest(case=label):
                path = self.stage()
                plan = self.plan_json(path)
                mutate(plan)
                self.rehash(path, plan)
                self.refuse(path)

    def test_a_rehashed_before_state_change_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["estimate"]["before_state"]["total"] = 1.0
        self.rehash(path, plan)
        self.refuse(path)

    def test_a_before_state_with_a_recomputed_hash_still_fails_the_projection(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        before = plan["live_evidence"]["estimate"]["before_state"]
        before["line_items"][TARGET_INDEX]["quantity"] = 6.0
        plan["live_evidence"]["estimate"]["before_state_sha256"] = draft.digest_for(before)
        self.rehash(path, plan)
        self.refuse(path)

    def test_plan_headers_must_match_this_action(self) -> None:
        cases = (
            ("tool", lambda p: p.update(tool="Other tool")),
            ("kind", lambda p: p.update(kind=draft.CORRECTION_KIND)),
            ("schema", lambda p: p.update(schema_version=2)),
            ("approval_word", lambda p: p.update(approval_required="OK")),
            ("nonce", lambda p: p.update(nonce="zz")),
            ("estimate_id", lambda p: p.update(estimate_id=QT30)),
            ("organization", lambda p: p.update(books_organization_id="abc")),
            ("risk_atomic", lambda p: p["risk"].update(atomic=False)),
            ("risk_single_put", lambda p: p["risk"].update(single_put=False)),
            ("risk_email", lambda p: p["risk"].update(email_sent=True)),
            ("risk_write", lambda p: p["risk"].update(write_attempted=True)),
            ("risk_status", lambda p: p["risk"].update(status_unchanged="draft")),
            ("risk_note", lambda p: p["risk"].update(note="harmless")),
        )
        for label, mutate in cases:
            with self.subTest(case=label):
                path = self.stage()
                plan = self.plan_json(path)
                mutate(plan)
                self.rehash(path, plan)
                self.refuse(path)

    def test_an_expired_or_future_plan_is_refused(self) -> None:
        for label, created, expires in (
            ("expired", "2026-08-09T00:00:00+00:00", "2026-08-10T00:00:00+00:00"),
            ("future", "2099-01-01T00:00:00+00:00", "2099-01-02T00:00:00+00:00"),
        ):
            with self.subTest(case=label):
                path = self.stage()
                plan = self.plan_json(path)
                plan["created_utc"] = created
                plan["expires_utc"] = expires
                self.rehash(path, plan)
                self.refuse(path, "expired|future")

    def test_a_lifetime_other_than_24_hours_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        created = draft.parse_plan_time(plan["created_utc"], "c")
        plan["expires_utc"] = (created.replace(year=created.year + 1)).isoformat()
        self.rehash(path, plan)
        self.refuse(path, "24-hour")

    def test_source_evidence_tampering_is_refused(self) -> None:
        cases = (
            ("plan_sha", lambda p: p["source_evidence"]["source_plan"].update(sha256="0" * 64)),
            ("plan_lines", lambda p: p["source_evidence"]["source_plan"]["line_items"]
                [TARGET_INDEX].update(quantity=1)),
            ("plan_path", lambda p: p["source_evidence"]["source_plan"].update(path="C:\\x.json")),
            ("request_quote", lambda p: p["source_evidence"]["customer_request"].update(
                quote="Item 9 – 9 instead 4")),
            ("request_to", lambda p: p["source_evidence"]["customer_request"].update(
                to_quantity="2")),
            ("request_from", lambda p: p["source_evidence"]["customer_request"].update(
                **{"from": "Someone else"})),
            ("request_missing", lambda p: p["source_evidence"].pop("customer_request")),
            ("extra_source", lambda p: p["source_evidence"].update(extra={})),
        )
        for label, mutate in cases:
            with self.subTest(case=label):
                path = self.stage()
                plan = self.plan_json(path)
                mutate(plan)
                self.rehash(path, plan)
                self.refuse(path)

    def test_stable_rehearsal_tampering_is_refused(self) -> None:
        cases = (
            ("fewer", lambda p: p["stable_state_evidence"].update(observations=1)),
            ("more", lambda p: p["stable_state_evidence"].update(observations=99)),
            ("window", lambda p: p["stable_state_evidence"].update(max_seconds="99999")),
            ("interval", lambda p: p["stable_state_evidence"].update(interval_seconds="0")),
            ("elapsed", lambda p: p["stable_state_evidence"].update(elapsed_seconds="600")),
            ("unstable", lambda p: p["stable_state_evidence"].update(stable=False)),
            ("digest", lambda p: p["stable_state_evidence"].update(prewrite_sha256="0" * 64)),
            ("observation_digest", lambda p: p["stable_state_evidence"]
                ["observation_prewrite_sha256"].__setitem__(1, "0" * 64)),
            ("short_digests", lambda p: p["stable_state_evidence"]
                ["observation_prewrite_sha256"].pop()),
            ("volatile_widened", lambda p: p["stable_state_evidence"].update(
                volatile_keys_observed=["status", "total"])),
            ("extra_key", lambda p: p["stable_state_evidence"].update(extra=1)),
            ("missing", lambda p: p.pop("stable_state_evidence")),
        )
        for label, mutate in cases:
            with self.subTest(case=label):
                path = self.stage()
                plan = self.plan_json(path)
                mutate(plan)
                self.rehash(path, plan)
                self.refuse(path)

    def test_a_rehearsal_may_only_cite_the_proven_volatile_key(self) -> None:
        before = live_estimate()
        second = copy.deepcopy(before)
        second["estimate_url"] = "https://example.invalid/rotated"
        path = self.stage(before=before, reads=[before, second, copy.deepcopy(before)])
        self.assertEqual(
            self.plan_json(path)["stable_state_evidence"]["volatile_keys_observed"],
            ["estimate_url"],
        )
        self.commit(path)

    def test_a_discount_correction_plan_cannot_be_committed_here(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["kind"] = draft.CORRECTION_KIND
        self.rehash(path, plan)
        self.refuse(path, "different tool, action or schema")


class PlanPathTests(Item9TestCase):
    def test_a_plan_outside_the_exact_folder_is_refused(self) -> None:
        outside = Path(self._temp.name) / "elsewhere.json"
        outside.write_text("{}", encoding="utf-8")
        for candidate in (str(outside), "relative.json", "", str(self.plan_dir)):
            with self.subTest(candidate=candidate):
                with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
                    draft.zoho_tool, "load_vault", side_effect=AssertionError("no vault")
                ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
                    with self.assertRaises(draft.DraftToolError):
                        draft.command_commit_item9_quantity_correction(
                            argparse.Namespace(plan=candidate, approval="APPROVED")
                        )

    def test_a_traversal_path_is_refused(self) -> None:
        path = self.stage()
        sneaky = str(self.plan_dir / ".." / "zoho_plans" / ".." / path.name)
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "load_vault", side_effect=AssertionError("no vault")
        ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
            with self.assertRaises(draft.DraftToolError):
                draft.command_commit_item9_quantity_correction(
                    argparse.Namespace(plan=sneaky, approval="APPROVED")
                )

    def test_a_symlinked_plan_is_refused(self) -> None:
        path = self.stage()
        link = self.plan_dir / "link.json"
        try:
            os.symlink(str(path), str(link))
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest(f"symlinks unavailable on this host: {exc}")
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "load_vault", side_effect=AssertionError("no vault")
        ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
            with self.assertRaisesRegex(draft.DraftToolError, "symlink"):
                draft.command_commit_item9_quantity_correction(
                    argparse.Namespace(plan=str(link), approval="APPROVED")
                )

    def test_a_symlinked_plan_or_parent_is_refused_without_os_privileges(self) -> None:
        """Symlink creation needs a privilege this host lacks, so the guard is
        exercised directly rather than left unproven."""
        path = self.stage()
        real_is_symlink = Path.is_symlink
        for label, marked in (("plan", path), ("parent", self.plan_dir)):
            with self.subTest(case=label):
                def fake_is_symlink(self, marked=marked):
                    return Path(self) == marked or real_is_symlink(self)
                with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
                    Path, "is_symlink", fake_is_symlink
                ), patch.object(
                    draft.zoho_tool, "load_vault", side_effect=AssertionError("no vault")
                ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
                    with self.assertRaisesRegex(draft.DraftToolError, "symlink"):
                        draft.command_commit_item9_quantity_correction(
                            argparse.Namespace(plan=str(path), approval="APPROVED")
                        )


# ---------------------------------------------------------------------------
# Commit -- pre-write drift
# ---------------------------------------------------------------------------


class PreWriteDriftTests(Item9TestCase):
    def drift(self, mutate, pattern: str = "changed after review") -> None:
        path = self.stage()
        drifted = live_estimate()
        mutate(drifted)
        error = self.commit_expecting_error(
            path, reads=[{"code": 0, "estimate": drifted}]
        )
        self.assertRegex(str(error), pattern)
        self.assertEqual(self.last_calls["puts"], [])
        # A refusal before the write must not burn the plan.
        self.assertFalse(self.lock_exists(path))

    def test_any_header_drift_refuses_before_lock_and_write(self) -> None:
        for key, value in (
            ("total", 1.0), ("sub_total", 1.0), ("tax_total", 1.0), ("status", "draft"),
            ("reference_number", "PO OTHER"), ("customer_id", "96274000000060020"),
            ("notes", "edited"), ("date", "2026-08-11"), ("terms", "net 30"),
            ("last_modified_time", "2026-08-11T15:00:00-0400"),
            ("discount_type", "entity_level"), ("template_id", "96274000000000999"),
        ):
            with self.subTest(key=key):
                self.drift(lambda e, k=key, v=value: e.update({k: v}))

    def test_any_line_drift_refuses_before_lock_and_write(self) -> None:
        for index, key, value in (
            (0, "quantity", 9.0), (0, "rate", 1.0), (0, "name", "Other"),
            (0, "description", "changed"), (0, "unit", "ea"),
            (TARGET_INDEX, "quantity", 2.0), (TARGET_INDEX, "rate", 900.0),
            (TARGET_INDEX, "item_id", "96274000000030481"),
            (TARGET_INDEX, "item_order", 10), (5, "tax_id", "96274000000035512"),
            (5, "discount", 20.0), (5, "line_item_id", "96274000009999999"),
        ):
            with self.subTest(index=index, key=key):
                self.drift(lambda e, i=index, k=key, v=value: e["line_items"][i].update({k: v}))

    def test_line_add_drop_reorder_and_substitution_refuse_before_write(self) -> None:
        self.drift(lambda e: e["line_items"].pop())
        self.drift(lambda e: e["line_items"].append(copy.deepcopy(e["line_items"][0])))
        self.drift(lambda e: e["line_items"].reverse())
        self.drift(lambda e: e["line_items"].__setitem__(3, copy.deepcopy(e["line_items"][4])))

    def test_a_rotated_estimate_url_is_the_only_accepted_prewrite_difference(self) -> None:
        path = self.stage()
        current = live_estimate()
        current["estimate_url"] = "https://example.invalid/rotated-before-commit"
        after = corrected_estimate()
        calls = self.commit(
            path, reads=[{"code": 0, "estimate": current}, {"code": 0, "estimate": after}]
        )
        self.assertEqual(len(calls["puts"]), 1)
        self.assertEqual(self.lock_record(path)["status"], "committed_verified")


# ---------------------------------------------------------------------------
# Commit -- the write allowlist
# ---------------------------------------------------------------------------


class WriteAllowlistTests(Item9TestCase):
    def payload(self) -> dict:
        plan = self.plan_json(self.stage())
        return copy.deepcopy(plan["live_evidence"]["put_payload"])

    def call(self, *, method="PUT", path=None, payload=None, expected=None, org=ORG_ID):
        base = self.payload()
        payload = base if payload is None else payload
        expected = base if expected is None else expected
        return draft.oauth_estimate_item9_quantity_write_allowed(
            "token", tool.EXPECTED_API_DOMAIN, method,
            path if path is not None else f"/books/v3/estimates/{QT29}",
            org, payload, expected,
        )

    def refuse(self, pattern="REFUSED", **kwargs):
        with patch.object(draft, "urlopen", side_effect=AssertionError("must not reach network")):
            with self.assertRaisesRegex(draft.DraftToolError, pattern):
                self.call(**kwargs)

    def test_only_put_is_allowed(self) -> None:
        for method in ("POST", "DELETE", "PATCH", "GET", "HEAD", "put", ""):
            with self.subTest(method=method):
                self.refuse(method=method)

    def test_only_the_one_estimate_path_is_allowed(self) -> None:
        for path in (
            f"/books/v3/estimates/{QT30}",
            "/books/v3/estimates/96274000009999999",
            "/books/v3/estimates",
            f"/books/v3/estimates/{QT29}/status/sent",
            f"/books/v3/estimates/{QT29}/email",
            f"/books/v3/estimates/{QT29}/approve",
            f"/books/v3/invoices/{QT29}",
            f"/books/v3/estimates/{QT29}?send=true",
            f"/books/v3/estimates/{QT29}/",
            "",
        ):
            with self.subTest(path=path):
                self.refuse(path=path)

    def test_an_extra_or_missing_top_level_key_is_refused(self) -> None:
        for label, mutate in (
            ("status", lambda p: p.update(status="sent")),
            ("send", lambda p: p.update(send=True)),
            ("email", lambda p: p.update(to_mail_ids=["x@y.z"])),
            ("currency", lambda p: p.update(currency_id="1")),
            ("adjustment", lambda p: p.update(adjustment=1.0)),
            ("drop_customer", lambda p: p.pop("customer_id")),
            ("drop_lines", lambda p: p.pop("line_items")),
            ("drop_date", lambda p: p.pop("date")),
        ):
            with self.subTest(case=label):
                payload = self.payload()
                mutate(payload)
                self.refuse(payload=payload)

    def test_a_header_that_leaves_the_reviewed_plan_is_refused(self) -> None:
        for key, value in (
            ("customer_id", "96274000000060020"),
            ("estimate_number", "QT-000030"),
            ("reference_number", "PO OTHER"),
            ("date", "2026-01-01"),
            ("notes", "edited"),
        ):
            with self.subTest(key=key):
                payload = self.payload()
                payload[key] = value
                self.refuse(payload=payload)

    def test_a_line_that_leaves_the_reviewed_plan_is_refused(self) -> None:
        for index, key, value in (
            (0, "quantity", 9), (0, "rate", 1.0), (0, "name", "Other"),
            (0, "description", "edited"), (0, "tax_id", "96274000000035512"),
            (0, "item_id", "96274000000030481"), (0, "unit", "ea"),
            (TARGET_INDEX, "quantity", 2), (TARGET_INDEX, "quantity", 4),
            (TARGET_INDEX, "rate", 800.0),
        ):
            with self.subTest(index=index, key=key, value=value):
                payload = self.payload()
                payload["line_items"][index][key] = value
                self.refuse(payload=payload)

    def test_a_numeric_line_discount_is_refused(self) -> None:
        for value in (10, 10.0, "10", "", None, "9%"):
            with self.subTest(value=value):
                payload = self.payload()
                payload["line_items"][0]["discount"] = value
                self.refuse(payload=payload)

    def test_line_add_drop_reorder_and_substitution_are_refused(self) -> None:
        for label, mutate in (
            ("drop", lambda p: p["line_items"].pop()),
            ("add", lambda p: p["line_items"].append(copy.deepcopy(p["line_items"][0]))),
            ("reorder", lambda p: p["line_items"].reverse()),
            ("swap", lambda p: p["line_items"].__setitem__(2, copy.deepcopy(p["line_items"][3]))),
            ("dupe_id", lambda p: p["line_items"][1].update(
                line_item_id=p["line_items"][0]["line_item_id"])),
            ("drop_line_id", lambda p: p["line_items"][0].pop("line_item_id")),
            ("drop_item_id", lambda p: p["line_items"][0].pop("item_id")),
            ("extra_line_key", lambda p: p["line_items"][0].update(status="x")),
            ("blank_id", lambda p: p["line_items"][0].update(line_item_id="")),
        ):
            with self.subTest(case=label):
                payload = self.payload()
                mutate(payload)
                self.refuse(payload=payload)

    def test_the_target_line_must_be_present_exactly_once_at_quantity_one(self) -> None:
        payload = self.payload()
        payload["line_items"][TARGET_INDEX]["line_item_id"] = "96274000009999999"
        self.refuse(payload=payload)

    def test_an_invalid_organization_is_refused(self) -> None:
        for org in ("", "abc", "0", None, "1 ; DROP"):
            with self.subTest(org=org):
                self.refuse(org=org)

    def test_the_allowlist_is_a_pure_validator_that_touches_nothing(self) -> None:
        base = self.payload()
        with patch.object(draft, "urlopen", side_effect=AssertionError("must not reach network")):
            self.assertIsNone(
                draft.require_item9_put_allowed(
                    "PUT", f"/books/v3/estimates/{QT29}", ORG_ID, base, base
                )
            )
            bad = copy.deepcopy(base)
            bad["line_items"][TARGET_INDEX]["quantity"] = 4
            with self.assertRaisesRegex(draft.DraftToolError, "REFUSED"):
                draft.require_item9_put_allowed(
                    "PUT", f"/books/v3/estimates/{QT29}", ORG_ID, bad, base
                )

    def test_the_allowlist_runs_before_the_replay_lock(self) -> None:
        source = Path(draft.__file__).read_text(encoding="utf-8")
        commit = source.split("def command_commit_item9_quantity_correction")[1]
        self.assertLess(
            commit.index("require_item9_put_allowed"),
            commit.index("write_correction_lock"),
            "a payload the allowlist rejects must be a free refusal, not a burned plan",
        )

    def test_the_allowed_call_sends_exactly_one_put(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout):
            captured["method"] = request.get_method()
            captured["url"] = request.full_url
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse({"code": 0, "estimate": corrected_estimate()})

        with patch.object(draft, "urlopen", side_effect=fake_urlopen):
            self.call()
        self.assertEqual(captured["method"], "PUT")
        self.assertEqual(
            captured["url"],
            f"{tool.EXPECTED_API_DOMAIN}/books/v3/estimates/{QT29}?organization_id={ORG_ID}",
        )
        for forbidden in ("send", "email", "status", "accept", "convert"):
            self.assertNotIn(forbidden, captured["url"])
        self.assertEqual(captured["headers"].get("Authorization"), "Zoho-oauthtoken token")


# ---------------------------------------------------------------------------
# Commit -- read-back verification
# ---------------------------------------------------------------------------


class ReadBackTests(Item9TestCase):
    def bad_readback(self, mutate, pattern: str = "") -> None:
        path = self.stage()
        before = live_estimate()
        after = corrected_estimate(before)
        mutate(after)
        error = self.commit_expecting_error(
            path,
            reads=[{"code": 0, "estimate": before}, {"code": 0, "estimate": after}],
            put_result={"code": 0, "estimate": corrected_estimate()},
        )
        if pattern:
            self.assertRegex(str(error), pattern)
        self.assertIn("indeterminate", str(error))
        record = self.lock_record(path)
        self.assertEqual(record["status"], "indeterminate")
        self.assertTrue(record["write_attempted"])
        self.assertTrue(record["no_retry"])

    def test_a_status_change_after_the_write_is_caught(self) -> None:
        for status in ("draft", "accepted", "invoiced", "declined"):
            with self.subTest(status=status):
                self.bad_readback(lambda e, s=status: e.update(status=s), "status")

    def test_wrong_totals_after_the_write_are_caught(self) -> None:
        for key in ("sub_total", "tax_total", "total"):
            with self.subTest(key=key):
                self.bad_readback(
                    lambda e, k=key: e.update({k: float(Decimal(str(e[k])) + Decimal("0.01"))}),
                    key,
                )

    def test_a_target_quantity_that_did_not_land_is_caught(self) -> None:
        self.bad_readback(
            lambda e: e["line_items"][TARGET_INDEX].update(quantity=4.0), "quantity"
        )
        self.bad_readback(
            lambda e: e["line_items"][TARGET_INDEX].update(quantity=2.0), "quantity"
        )

    def test_a_stale_formatted_quantity_is_caught(self) -> None:
        stale = live_estimate()["line_items"][TARGET_INDEX]["quantity_formatted"]
        self.bad_readback(
            lambda e: e["line_items"][TARGET_INDEX].update(quantity_formatted=stale),
            "formatted quantity",
        )
        self.bad_readback(
            lambda e: e["line_items"][TARGET_INDEX].pop("quantity_formatted"),
            "quantity_formatted",
        )

    def test_a_non_target_line_that_moved_is_caught(self) -> None:
        for index, key, value in (
            (0, "quantity", 9.0), (0, "rate", 1.0), (0, "name", "Other"),
            (0, "description", "changed"), (0, "unit", "ea"), (0, "item_id", "96274000000019649"),
            (0, "item_order", 99), (0, "tax_id", "96274000000035512"),
        ):
            with self.subTest(index=index, key=key):
                self.bad_readback(lambda e, i=index, k=key, v=value: e["line_items"][i].update({k: v}))

    def test_a_flat_cad_discount_landing_instead_is_caught(self) -> None:
        def flatten(estimate):
            for line in estimate["line_items"]:
                line["discount_amount"] = 10.0
        self.bad_readback(flatten, "discount_amount")

    def test_a_dropped_or_added_line_is_caught(self) -> None:
        self.bad_readback(lambda e: e["line_items"].pop(), "lines")
        self.bad_readback(
            lambda e: e["line_items"].append(copy.deepcopy(e["line_items"][0])), "lines"
        )

    def test_a_protected_header_field_that_moved_is_caught(self) -> None:
        for key, value in (
            ("customer_name", "Someone Else"), ("currency_code", "USD"),
            ("template_id", "96274000000000999"), ("salesperson_id", "96274000000000111"),
            ("expiry_date", "2026-09-01"), ("date", "2026-08-11"), ("terms", "net 30"),
            ("notes", "edited"),
        ):
            with self.subTest(key=key):
                self.bad_readback(lambda e, k=key, v=value: e.update({k: v}))

    def test_a_missing_or_wrong_tax_row_is_caught(self) -> None:
        self.bad_readback(lambda e: e["taxes"].pop(), "GST/QST")
        self.bad_readback(
            lambda e: e["taxes"].__setitem__(0, {"tax_name": "GST", "tax_amount": 1.0}), "GST"
        )
        self.bad_readback(
            lambda e: e["taxes"].__setitem__(1, {"tax_name": "HST", "tax_amount": 1.0}), "GST|QST"
        )

    def test_a_wrong_uninvoiced_or_discount_total_is_caught(self) -> None:
        self.bad_readback(lambda e: e.update(uninvoiced_amount=1.0), "uninvoiced_amount")
        self.bad_readback(lambda e: e.update(discount_total=1.0), "discount_total")
        self.bad_readback(lambda e: e.update(discount_percent=25.0), "discount_percent")

    def test_a_wrong_base_currency_mirror_is_caught(self) -> None:
        for key in ("bcy_sub_total", "bcy_tax_total", "bcy_total"):
            with self.subTest(key=key):
                self.bad_readback(lambda e, k=key: e.update({k: 1.0}), key)

    def test_the_fresh_read_is_authoritative_over_the_put_response(self) -> None:
        path = self.stage()
        before = live_estimate()
        good = corrected_estimate(before)
        bad = corrected_estimate(before)
        bad["line_items"][0]["name"] = "Substituted"
        error = self.commit_expecting_error(
            path,
            reads=[{"code": 0, "estimate": before}, {"code": 0, "estimate": bad}],
            put_result={"code": 0, "estimate": good},
        )
        self.assertIn("indeterminate", str(error))
        self.assertEqual(self.lock_record(path)["status"], "indeterminate")

    def test_a_wrong_put_response_is_caught_immediately(self) -> None:
        path = self.stage()
        before = live_estimate()
        wrong = corrected_estimate(before)
        wrong["total"] = 1.0
        error = self.commit_expecting_error(
            path,
            reads=[{"code": 0, "estimate": before}],
            put_result={"code": 0, "estimate": wrong},
        )
        self.assertIn("indeterminate", str(error))
        self.assertEqual(self.lock_record(path)["status"], "indeterminate")


# ---------------------------------------------------------------------------
# Commit -- locking, replay and indeterminate outcomes
# ---------------------------------------------------------------------------


class LockAndReplayTests(Item9TestCase):
    def test_the_lock_exists_before_the_put_is_issued(self) -> None:
        path = self.stage()
        calls = self.commit(path)
        self.assertTrue(calls["puts"][0]["lock_exists"])

    def test_a_committed_plan_cannot_be_replayed(self) -> None:
        path = self.stage()
        self.commit(path)
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", side_effect=AssertionError("vault must not open")
        ), patch.object(
            draft.zoho_tool, "api_get", side_effect=AssertionError("no GET")
        ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
            with self.assertRaisesRegex(draft.DraftToolError, "REFUSED"):
                draft.command_commit_item9_quantity_correction(
                    argparse.Namespace(plan=str(path), approval="APPROVED")
                )

    def test_a_transport_failure_leaves_the_plan_locked_with_no_retry(self) -> None:
        for label, error in (
            ("http", HTTPError("u", 400, "Bad Request", None, None)),
            ("url", URLError("connection reset")),
            ("timeout", TimeoutError("timed out")),
        ):
            with self.subTest(case=label):
                path = self.stage()
                if label == "http":
                    error.read = lambda: b'{"code":15,"message":"nope"}'
                raised = self.commit_expecting_error(path, put_error=error)
                self.assertIn("indeterminate", str(raised))
                self.assertIn("permanently locked", str(raised))
                record = self.lock_record(path)
                self.assertEqual(record["status"], "indeterminate")
                self.assertTrue(record["write_attempted"])
                self.assertTrue(record["plan_locked_indeterminate"])
                # No retry: a second attempt is refused before any network call.
                with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
                    draft.zoho_tool, "append_receipt"
                ), patch.object(
                    draft.zoho_tool, "load_vault", side_effect=AssertionError("no vault")
                ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
                    with self.assertRaisesRegex(draft.DraftToolError, "REFUSED"):
                        draft.command_commit_item9_quantity_correction(
                            argparse.Namespace(plan=str(path), approval="APPROVED")
                        )

    def test_a_malformed_response_is_indeterminate_and_locked(self) -> None:
        class BadJson(FakeResponse):
            def read(self):
                return b"<html>gateway error</html>"

        path = self.stage()
        before = live_estimate()

        def fake_urlopen(request, timeout):
            return BadJson({})

        vault = fake_vault()
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool, "api_get", return_value={"code": 0, "estimate": before}
        ), patch.object(draft, "urlopen", side_effect=fake_urlopen):
            with self.assertRaisesRegex(draft.DraftToolError, "indeterminate"):
                draft.command_commit_item9_quantity_correction(
                    argparse.Namespace(plan=str(path), approval="APPROVED")
                )
        self.assertEqual(self.lock_record(path)["status"], "indeterminate")

    def test_a_nonzero_zoho_code_is_indeterminate_and_locked(self) -> None:
        path = self.stage()
        error = self.commit_expecting_error(
            path, put_result={"code": 15, "message": "attributes too long"}
        )
        self.assertIn("indeterminate", str(error))
        self.assertEqual(self.lock_record(path)["status"], "indeterminate")

    def test_only_one_put_is_ever_issued(self) -> None:
        path = self.stage()
        calls = self.commit(path)
        self.assertEqual(len(calls["puts"]), 1)
        methods = [call["method"] for call in calls["puts"]]
        self.assertEqual(methods, ["PUT"])


# ---------------------------------------------------------------------------
# Source-level containment
# ---------------------------------------------------------------------------


class SourceContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = Path(draft.__file__).read_text(encoding="utf-8")

    def test_exactly_two_new_parser_actions_exist(self) -> None:
        parser = draft.build_parser()
        choices = set(parser._subparsers._group_actions[0].choices)
        self.assertEqual(
            choices,
            {
                "stage-customer", "stage-quote", "commit-customer", "commit-quote",
                "stage-tds-discount-correction", "commit-tds-discount-correction",
                "stage-tds-item9-quantity-correction", "commit-tds-item9-quantity-correction",
            },
        )
        item9 = {c for c in choices if "item9" in c}
        self.assertEqual(item9, {
            "stage-tds-item9-quantity-correction", "commit-tds-item9-quantity-correction"
        })

    def test_the_module_still_holds_one_put_one_post_and_two_urlopen_sites(self) -> None:
        self.assertEqual(self.source.count('method="PUT"'), 1)
        self.assertEqual(self.source.count('method="POST"'), 1)
        self.assertEqual(self.source.count("urlopen(request"), 2)

    def test_no_mail_delete_or_lifecycle_route_exists(self) -> None:
        for forbidden in (
            'method="DELETE"', 'method="PATCH"', 'method="GET"',
            "/books/v3/estimates/email", "/status/", "markassent", "approve\"",
            "to_mail_ids", "send=true", "smtplib", "Mail.Send", "/submit", "/reject",
            "/converttoinvoice", "/attachment", "/reminder", "/bulk",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)

    def test_the_item9_target_is_fixed_in_source(self) -> None:
        self.assertIn('ITEM9_ESTIMATE_ID = "96274000001559037"', self.source)
        self.assertIn('"line_item_id": "96274000001559046"', self.source)
        self.assertIn('"item_id": "96274000000030497"', self.source)
        self.assertIn('"new_quantity": Decimal("1")', self.source)
        self.assertIn('"current_quantity": Decimal("4")', self.source)

    def test_the_approval_word_is_compared_byte_exactly(self) -> None:
        self.assertIn('approval != APPROVAL_WORD', self.source)
        self.assertNotIn("approval.strip()", self.source)
        self.assertNotIn("approval.upper()", self.source)
        self.assertNotIn("approval.casefold()", self.source)
        self.assertNotIn("approval.lower()", self.source)

    def test_no_new_scope_is_required(self) -> None:
        self.assertEqual(draft.ESTIMATE_UPDATE_SCOPE, "ZohoBooks.estimates.UPDATE")
        self.assertIn(draft.ESTIMATE_UPDATE_SCOPE, tool.SCOPES)
        # estimates.CREATE is the pre-existing draft-estimate capability, not a
        # widening; nothing beyond it may appear.
        for widened in (
            "ZohoBooks.estimates.DELETE", "ZohoBooks.estimates.ALL",
            "ZohoBooks.fullaccess.all", "ZohoBooks.invoices.DELETE",
        ):
            with self.subTest(scope=widened):
                self.assertNotIn(widened, self.source)

    def test_the_item9_allowlist_pins_one_path(self) -> None:
        self.assertIn(
            "match.group(1) != ITEM9_ESTIMATE_ID",
            self.source,
            "the Item 9 write allowlist must pin the single estimate id",
        )

    def test_the_existing_discount_correction_is_untouched(self) -> None:
        self.assertEqual(set(draft.CORRECTION_TARGETS), {QT29, QT30})
        self.assertEqual(draft.CORRECTION_TARGETS[QT29]["status"], "draft")
        self.assertEqual(draft.CORRECTION_KIND, "tds_discount_correction")
        self.assertEqual(draft.ALLOWED_POSTS, {
            "customer": "/books/v3/contacts", "quote": "/books/v3/estimates"
        })
        self.assertEqual(draft.TDS_LINE_DISCOUNT, "10%")

    def test_the_tool_declares_no_mail_transport(self) -> None:
        self.assertIn("email_sent", self.source)
        self.assertIn("no mail transport", self.source)


if __name__ == "__main__":
    unittest.main()
