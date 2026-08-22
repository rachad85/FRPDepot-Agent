"""Safety tests for the J26-403 fixed revision tool.

Commissioned by Rachad on 2026-08-21: six dip tubes and twenty-four lifting lugs
must be added to the already-emailed PO-00010 and to the accepted Troy Dualam
quote QT-000034, as non-standard free-text lines, with the Troy Dualam rate at
the supplier USD unit cost times 3.6.

NO TEST IN THIS FILE PERFORMS A LIVE CALL. Every read is a fake api_get and
every write is a fake urlopen; the real transports are asserted never to run,
and staging patches urlopen to raise so a stray write cannot pass unnoticed.

The commit-path tests patch CONTRACT_FACTS to `proven` so the happy path is
actually exercised. THE SHIPPED DEFAULT IS UNPROVEN, and ContractProofTests
pins that the module as delivered refuses every commit before the lock, the
vault, the token and the network.
"""

from __future__ import annotations

import argparse
import ast
import copy
import io
import json
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.error import URLError

import zoho_tool as tool
import zoho_j26_403_revision_tool as j26

ORG_ID = "96274000000000001"
ON_HST = j26.ON_HST_TAX_ID
GST = "96274000000035512"
GST_QST_GROUP = "96274000001071139"

ACTIVE_TAXES = [
    {"tax_id": ON_HST, "tax_name": "ON HST", "tax_percentage": 13,
     "tax_type": "tax", "status": "Active", "is_inactive": False},
    {"tax_id": GST, "tax_name": "GST", "tax_percentage": 5,
     "tax_type": "tax", "status": "Active", "is_inactive": False},
    {"tax_id": GST_QST_GROUP, "tax_name": "Gst & Qst", "tax_percentage": 14.975,
     "tax_type": "tax_group", "status": "Active", "is_inactive": False},
]

LINE_NAMES = (
    'FRP NOZZLE-2"/50PSI/D441',
    'FRP NOZZLE-4"/50PSI/D441',
    'FRP NOZZLE-6"/50PSI/D441',
    'FRP MANWAY-24"/15PSI/D441',
    'FRP MANWAY COVER-24"/15PSI/D441',
    'FRP MANWAY-30"/25PSI/D441',
    'FRP MANWAY COVER-30"/25PSI/D441',
)
LUG_COST = "38.75"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def cents(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def live_purchase_order() -> dict:
    """PO-00010 as Zoho returned it read-only on 2026-08-21."""
    lines = []
    for order, ((line_id, item_id, qty, rate), name) in enumerate(
        zip(j26.PURCHASE_ORDER_LINES, LINE_NAMES), start=1
    ):
        quantity, unit_rate = Decimal(qty), Decimal(rate)
        lines.append({
            "line_item_id": line_id,
            "item_id": item_id,
            "name": name,
            "sku": f"SKU{order}",
            "description": "",
            "item_order": order,
            "quantity": float(quantity),
            "rate": float(unit_rate),
            "unit": "pcs",
            "discount": 0.0,
            "tax_id": "",
            "item_total": cents(quantity * unit_rate),
            "bcy_rate": float(unit_rate),
        })
    sub_total = sum(Decimal(str(line["item_total"])) for line in lines)
    return {
        "purchaseorder_id": j26.PURCHASE_ORDER_TARGET["record_id"],
        "purchaseorder_number": "PO-00010",
        "reference_number": "J26-403-AIR-R1",
        "vendor_id": j26.PURCHASE_ORDER_TARGET["party_id"],
        "vendor_name": "JRAIN FRP LIMITED",
        "status": "open",
        "is_emailed": True,
        "date": "2026-08-18",
        "delivery_date": "",
        "ship_via": "Air freight from Jizhou, China to Toronto Airport, Canada",
        "notes": "All 7 items must use Derakane 441 and RTP-1 CCMMMM liner.",
        "terms": "",
        "currency_code": "USD",
        "currency_id": "96274000000000099",
        "exchange_rate": 1.3906,
        "template_id": "96274000000000541",
        "salesperson_id": "",
        "discount": 0.0,
        "discount_type": "item_level",
        "is_discount_before_tax": True,
        "shipping_charge": 0.0,
        "adjustment": 0.0,
        "billed_status": "unbilled",
        "received_status": "pending",
        "bills": [],
        "custom_fields": [],
        "sub_total": float(sub_total),
        "tax_total": 0.0,
        "total": float(sub_total),
        "taxes": [],
        "purchaseorder_url": "https://example.invalid/staged-secure-po-url",
        "last_modified_time": "2026-08-18T10:00:00-0400",
        "line_items": lines,
    }


def live_estimate() -> dict:
    """QT-000034 as Zoho returned it read-only on 2026-08-21."""
    lines = []
    for order, ((line_id, item_id, qty, rate), name) in enumerate(
        zip(j26.ESTIMATE_LINES, LINE_NAMES), start=1
    ):
        quantity, unit_rate = Decimal(qty), Decimal(rate)
        net = (quantity * unit_rate).quantize(Decimal("0.01"))
        lines.append({
            "line_item_id": line_id,
            "item_id": item_id,
            "name": name,
            "sku": f"SKU{order}",
            "description": f"{name} as quoted.",
            "item_order": order,
            "quantity": float(quantity),
            "rate": float(unit_rate),
            "unit": "pcs",
            "discount": 0.0,
            "discount_amount": 0.0,
            "discounts": [],
            "tax_id": ON_HST,
            "tax_name": "ON HST",
            "tax_percentage": 13,
            "item_total": float(net),
            "bcy_rate": float(unit_rate),
            "line_item_taxes": [{
                "tax_id": ON_HST, "tax_name": "ON HST (13%)",
                "tax_amount": cents(net * Decimal("13") / Decimal("100")),
            }],
        })
    sub_total = sum(Decimal(str(line["item_total"])) for line in lines)
    tax_total = (sub_total * Decimal("13") / Decimal("100")).quantize(Decimal("0.01"))
    return {
        "estimate_id": j26.ESTIMATE_TARGET["record_id"],
        "estimate_number": "QT-000034",
        "reference_number": "J26-403",
        "customer_id": j26.ESTIMATE_TARGET["party_id"],
        "customer_name": "Troy Dualam Inc.",
        "status": "accepted",
        "date": "2026-08-18",
        "expiry_date": "",
        "notes": "Looking forward for your business.",
        "terms": "",
        "currency_code": "CAD",
        "currency_id": "96274000000000087",
        "exchange_rate": 1.0,
        "template_id": "96274000000000539",
        "salesperson_id": "",
        "discount": 0.0,
        "discount_type": "item_level",
        "is_discount_before_tax": True,
        "shipping_charge": 0.0,
        "adjustment": 0.0,
        "invoice_ids": [],
        "salesorders": [],
        "invoiced_amount": 0.0,
        "custom_fields": [],
        "sub_total": float(sub_total),
        "tax_total": float(tax_total),
        "total": float(sub_total + tax_total),
        "taxes": [{"tax_name": "ON HST", "tax_amount": float(tax_total)}],
        "estimate_url": "https://example.invalid/staged-secure-estimate-url",
        "last_modified_time": "2026-08-18T11:00:00-0400",
        "line_items": lines,
    }


LIVE = {
    j26.ACTION_PURCHASE_ORDER: live_purchase_order,
    j26.ACTION_ESTIMATE: live_estimate,
}
STAGE_COMMAND = {
    j26.ACTION_PURCHASE_ORDER: j26.command_stage_purchase_order_revision,
    j26.ACTION_ESTIMATE: j26.command_stage_estimate_revision,
}
COMMIT_COMMAND = {
    j26.ACTION_PURCHASE_ORDER: j26.command_commit_purchase_order_revision,
    j26.ACTION_ESTIMATE: j26.command_commit_estimate_revision,
}
BOTH = (j26.ACTION_PURCHASE_ORDER, j26.ACTION_ESTIMATE)
# Default for the commit helper: his go timed one minute AFTER the plan was
# written (A3/A5: --approval-message-utc is required on every money commit).
AFTER_PLAN = object()


def after_plan(plan: dict) -> str:
    created = j26.parse_plan_time(plan["created_utc"], "creation time")
    return (created + j26.timedelta(minutes=1)).isoformat()


def revised_record(action: str, before: dict | None = None, lug_cost: str = LUG_COST) -> dict:
    """What Zoho should return once the two appended lines land."""
    before = copy.deepcopy(before or LIVE[action]())
    target = j26.TARGETS[action]
    multiplier = (
        Decimal("1") if action == j26.ACTION_PURCHASE_ORDER else j26.TDI_MULTIPLIER
    )
    additions = (
        (j26.DIP_TUBE_NAME, j26.DIP_TUBE_DESCRIPTION, j26.DIP_TUBE_QUANTITY,
         (j26.DIP_TUBE_SUPPLIER_RATE_USD * multiplier).quantize(Decimal("0.01"))),
        (j26.LIFTING_LUG_NAME, j26.LIFTING_LUG_DESCRIPTION, j26.LIFTING_LUG_QUANTITY,
         (Decimal(lug_cost) * multiplier).quantize(Decimal("0.01"))),
    )
    tax_id = target["new_line_tax_id"]
    for offset, (name, description, quantity, rate) in enumerate(additions):
        net = (quantity * rate).quantize(Decimal("0.01"))
        line = {
            "line_item_id": f"9627400000199900{offset + 1}",
            "item_id": "",
            "name": name,
            "description": description,
            "item_order": len(before["line_items"]) + 1,
            "quantity": float(quantity),
            "rate": float(rate),
            "unit": j26.NEW_LINE_UNIT,
            "discount": 0.0,
            "tax_id": tax_id,
            "item_total": float(net),
        }
        if tax_id:
            line["tax_name"] = "ON HST"
            line["tax_percentage"] = 13
            line["line_item_taxes"] = [{
                "tax_id": tax_id, "tax_name": "ON HST (13%)",
                "tax_amount": cents(net * Decimal("13") / Decimal("100")),
            }]
        before["line_items"].append(line)
    sub_total = sum(Decimal(str(line["item_total"])) for line in before["line_items"])
    before["sub_total"] = float(sub_total)
    if tax_id:
        tax_total = (sub_total * Decimal("13") / Decimal("100")).quantize(Decimal("0.01"))
        before["taxes"] = [{"tax_name": "ON HST", "tax_amount": float(tax_total)}]
    else:
        tax_total = Decimal("0")
    before["tax_total"] = float(tax_total)
    before["total"] = float(sub_total + tax_total)
    volatile = "purchaseorder_url" if action == j26.ACTION_PURCHASE_ORDER else "estimate_url"
    before[volatile] = "https://example.invalid/fresh-secure-url"
    before["last_modified_time"] = "2026-08-21T16:45:00-0400"
    return before


def fake_vault(scopes=None) -> dict:
    return {
        "api_domain": tool.EXPECTED_API_DOMAIN,
        "books_organization_id": ORG_ID,
        "scopes": list(tool.SCOPES) if scopes is None else list(scopes),
    }


def proven_contract_facts() -> dict:
    """A copy of the shipped registry with every fact marked proven.

    Only the commit-path tests use it. The shipped default is unproven and
    ContractProofTests pins that, so this can never make the real tool commit.
    """
    facts = copy.deepcopy(j26.CONTRACT_FACTS)
    for fact in facts.values():
        fact["proven"] = True
    return facts


class J26TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.plan_dir = self.root / "j26_plans"
        self.plan_dir.mkdir(parents=True)
        self.addCleanup(self._temp.cleanup)
        # Default: the SHIPPED registry, where every contract fact is unproven.
        # Commit-path classes flip this so the happy path is actually exercised;
        # ContractProofTests pins the shipped default.
        self.contract_proven = False
        # A real file inside the fixed source folder, so the artifact gate is
        # exercised against a genuine path and a genuine digest.
        self.artifact = j26.SOURCE_DIR / "manifest.json"
        self.artifact_sha = j26.file_digest(self.artifact)

    # -- inputs ---------------------------------------------------------

    def lug_input(self, **overrides) -> dict:
        entry = {
            "value": LUG_COST,
            "source": (
                "Fei quotation email received 2026-08-21 16:20 UTC, line 2: SS316L anchor "
                "clip USD 38.75 each for 24 pcs."
            ),
            "artifact_path": str(self.artifact),
            "artifact_sha256": self.artifact_sha,
        }
        entry.update(overrides.pop("entry", {}))
        payload = {"lifting_lug_supplier_unit_cost_usd": entry}
        payload.update(overrides)
        return payload

    def write_input(self, payload: dict, name: str = "j26_input.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    # -- fake Zoho ------------------------------------------------------

    def _reader(self, records: list, taxes: list | None = None):
        pending = list(records)
        tax_rows = ACTIVE_TAXES if taxes is None else taxes
        calls = {"records": 0, "taxes": 0, "paths": []}

        def fake_api_get(access_token, api_domain, path):
            calls["paths"].append(path)
            if "/settings/taxes" in path:
                calls["taxes"] += 1
                return {"code": 0, "taxes": copy.deepcopy(tax_rows)}
            calls["records"] += 1
            if not pending:
                raise AssertionError("unexpected extra record GET")
            record = pending.pop(0)
            key = "purchaseorder" if "purchaseorders" in path else "estimate"
            return {"code": 0, key: record}

        return fake_api_get, calls

    # -- staging --------------------------------------------------------

    def stage(
        self,
        action: str = j26.ACTION_ESTIMATE,
        payload: dict | None = None,
        before: dict | None = None,
        taxes: list | None = None,
        scopes=None,
    ) -> Path:
        payload = self.lug_input() if payload is None else payload
        before = copy.deepcopy(before if before is not None else LIVE[action]())
        input_path = self.write_input(payload)
        reader, self.stage_calls = self._reader([before], taxes)
        vault = fake_vault(scopes)
        existing = set(self.plan_dir.glob("*.json"))
        facts = proven_contract_facts() if self.contract_proven else j26.CONTRACT_FACTS
        with patch.object(j26, "PLAN_DIR", self.plan_dir), patch.object(
            j26, "CONTRACT_FACTS", facts
        ), patch.object(
            j26.zoho_tool, "append_receipt"
        ), patch.object(
            j26.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            j26.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(j26.zoho_tool, "save_vault"), patch.object(
            j26.zoho_tool, "api_get", side_effect=reader
        ), patch.object(
            j26, "urlopen", side_effect=AssertionError("staging must never write")
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                STAGE_COMMAND[action](argparse.Namespace(input=str(input_path)))
            self.last_stage_output = output.getvalue()
        created = set(self.plan_dir.glob("*.json")) - existing
        self.assertEqual(len(created), 1)
        return created.pop()

    def stage_expecting_error(self, **kwargs) -> Exception:
        with self.assertRaises(j26.J26RevisionToolError) as caught:
            self.stage(**kwargs)
        return caught.exception

    # -- plan helpers ---------------------------------------------------

    def plan_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def rewrite(self, path: Path, plan: dict) -> Path:
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def resign(self, path: Path, plan: dict) -> Path:
        core = dict(plan)
        core.pop("sha256", None)
        plan["sha256"] = j26.digest_for(core)
        return self.rewrite(path, plan)

    def lock_exists(self, path: Path) -> bool:
        plan = self.plan_json(path)
        return (self.plan_dir / j26.LOCK_DIRNAME / f"{plan['sha256']}.json").exists()

    def lock_json(self, path: Path) -> dict:
        plan = self.plan_json(path)
        return json.loads(
            (self.plan_dir / j26.LOCK_DIRNAME / f"{plan['sha256']}.json").read_text(
                encoding="utf-8"
            )
        )

    # -- commit ---------------------------------------------------------

    def commit(
        self,
        path: Path,
        action: str = j26.ACTION_ESTIMATE,
        approval: str = j26.APPROVAL_WORD,
        before: dict | None = None,
        after: dict | None = None,
        readback: dict | None = None,
        taxes: list | None = None,
        scopes=None,
        put_error: Exception | None = None,
        proven: bool | None = None,
        approval_message_utc=AFTER_PLAN,
    ) -> dict:
        if approval_message_utc is AFTER_PLAN:
            approval_message_utc = after_plan(self.plan_json(path))
        before = copy.deepcopy(before if before is not None else LIVE[action]())
        after = copy.deepcopy(after if after is not None else revised_record(action))
        readback = copy.deepcopy(readback if readback is not None else after)
        reader, self.commit_calls = self._reader([before, readback], taxes)
        vault = fake_vault(scopes)
        calls = {"puts": [], "requests": []}

        def fake_urlopen(request, timeout=None):
            calls["requests"].append(request)
            calls["puts"].append({
                "method": request.get_method(),
                "url": request.full_url,
                "body": json.loads(request.data.decode("utf-8")),
            })
            if put_error is not None:
                raise put_error
            key = "purchaseorder" if action == j26.ACTION_PURCHASE_ORDER else "estimate"
            return FakeResponse({"code": 0, key: after})

        if proven is None:
            proven = self.contract_proven
        facts = proven_contract_facts() if proven else j26.CONTRACT_FACTS
        with patch.object(j26, "PLAN_DIR", self.plan_dir), patch.object(
            j26, "CONTRACT_FACTS", facts
        ), patch.object(j26.zoho_tool, "append_receipt"), patch.object(
            j26.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            j26.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(j26.zoho_tool, "save_vault"), patch.object(
            j26.zoho_tool, "api_get", side_effect=reader
        ), patch.object(j26, "urlopen", side_effect=fake_urlopen):
            output = io.StringIO()
            try:
                with redirect_stdout(output):
                    COMMIT_COMMAND[action](argparse.Namespace(
                        plan=str(path),
                        approval=approval,
                        approval_message_utc=approval_message_utc,
                    ))
            finally:
                self.last_calls = calls
                self.last_commit_output = output.getvalue()
        return calls

    def commit_expecting_error(self, path: Path, **kwargs) -> Exception:
        with self.assertRaises(j26.J26RevisionToolError) as caught:
            self.commit(path, **kwargs)
        return caught.exception


class CommitCapableTestCase(J26TestCase):
    """Stages AND commits with the contract registry marked proven.

    The registry is patched for BOTH staging and commit so the two agree: a plan
    staged while the contract is unproven deliberately cannot be committed once
    it becomes proven, because its own disclosed contract block is part of the
    canonical projection. That property is pinned by
    ContractProofTests.test_a_hand_edited_contract_claim_cannot_unblock_a_plan.
    """

    def setUp(self) -> None:
        super().setUp()
        self.contract_proven = True


# ---------------------------------------------------------------------------
# Fixed identity and the two independent actions
# ---------------------------------------------------------------------------


class FixedTargetTests(J26TestCase):
    def test_the_two_records_are_the_commissioned_ones(self) -> None:
        self.assertEqual(j26.PURCHASE_ORDER_TARGET["record_id"], "96274000001598034")
        self.assertEqual(j26.PURCHASE_ORDER_TARGET["record_number"], "PO-00010")
        self.assertEqual(j26.PURCHASE_ORDER_TARGET["reference_number"], "J26-403-AIR-R1")
        self.assertEqual(j26.PURCHASE_ORDER_TARGET["party_id"], "96274000000027889")
        self.assertEqual(j26.PURCHASE_ORDER_TARGET["currency_code"], "USD")
        self.assertEqual(j26.PURCHASE_ORDER_TARGET["status"], "open")
        self.assertIs(j26.PURCHASE_ORDER_TARGET["is_emailed"], True)
        self.assertEqual(j26.ESTIMATE_TARGET["record_id"], "96274000001602028")
        self.assertEqual(j26.ESTIMATE_TARGET["record_number"], "QT-000034")
        self.assertEqual(j26.ESTIMATE_TARGET["reference_number"], "J26-403")
        self.assertEqual(j26.ESTIMATE_TARGET["party_id"], "96274000000060001")
        self.assertEqual(j26.ESTIMATE_TARGET["currency_code"], "CAD")
        self.assertEqual(j26.ESTIMATE_TARGET["status"], "accepted")
        self.assertEqual(j26.ESTIMATE_TARGET["new_line_tax_id"], "96274000000035516")

    def test_only_four_commands_exist(self) -> None:
        parser = j26.build_parser()
        actions = [
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        self.assertEqual(len(actions), 1)
        self.assertEqual(sorted(actions[0].choices), [
            "commit-estimate-revision",
            "commit-purchase-order-revision",
            "stage-estimate-revision",
            "stage-purchase-order-revision",
        ])

    def test_the_pinned_lines_match_the_live_read(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                record = LIVE[action]()
                pinned = j26.TARGET_LINES[action]
                self.assertEqual(len(record["line_items"]), len(pinned))
                for line, (line_id, item_id, qty, rate) in zip(record["line_items"], pinned):
                    self.assertEqual(line["line_item_id"], line_id)
                    self.assertEqual(line["item_id"], item_id)
                    self.assertEqual(Decimal(str(line["quantity"])), Decimal(qty))
                    self.assertEqual(Decimal(str(line["rate"])), Decimal(rate))

    def test_a_plan_for_one_action_cannot_be_committed_by_the_other(self) -> None:
        po_plan = self.stage(action=j26.ACTION_PURCHASE_ORDER)
        estimate_plan = self.stage(action=j26.ACTION_ESTIMATE)
        error = self.commit_expecting_error(po_plan, action=j26.ACTION_ESTIMATE)
        self.assertIn("one approval can never answer two plans", str(error))
        self.assertFalse(self.lock_exists(po_plan))
        error = self.commit_expecting_error(
            estimate_plan, action=j26.ACTION_PURCHASE_ORDER
        )
        self.assertIn("one plan can never carry both records", str(error))
        self.assertFalse(self.lock_exists(estimate_plan))

    def test_one_plan_names_one_record_and_one_endpoint(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                plan = self.plan_json(self.stage(action=action))
                target = j26.TARGETS[action]
                other = j26.TARGETS[
                    j26.ACTION_ESTIMATE if action == j26.ACTION_PURCHASE_ORDER
                    else j26.ACTION_PURCHASE_ORDER
                ]
                evidence = plan["live_evidence"]
                self.assertEqual(plan["record_id"], target["record_id"])
                self.assertEqual(
                    evidence["put_endpoint"],
                    f"PUT {j26.record_path(action, target['record_id'])}",
                )
                self.assertEqual(evidence["other_record_untouched"], other["record_number"])
                serialized = json.dumps(evidence["put_payload"])
                self.assertNotIn(other["record_id"], serialized)
                self.assertIs(plan["risk"]["one_record_only"], True)


# ---------------------------------------------------------------------------
# The happy plan
# ---------------------------------------------------------------------------


class HappyPlanTests(J26TestCase):
    def test_staging_reads_only_and_never_writes(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                self.stage(action=action)
                self.assertEqual(self.stage_calls["records"], 1)
                for path in self.stage_calls["paths"]:
                    self.assertTrue(
                        path.startswith("/books/v3/") and "?" in path, path
                    )

    def test_the_purchase_order_plan_prices_the_two_additions_at_supplier_cost(self) -> None:
        evidence = self.plan_json(
            self.stage(action=j26.ACTION_PURCHASE_ORDER)
        )["live_evidence"]
        appended = {row["key"]: row for row in evidence["appended_lines"]}
        self.assertEqual(appended["dip_tube"]["quantity"], "6")
        self.assertEqual(appended["dip_tube"]["rate"], "460.00")
        self.assertEqual(appended["dip_tube"]["item_total"], "2760.00")
        self.assertIs(appended["dip_tube"]["pricing"]["multiplier_applied"], False)
        self.assertEqual(appended["lifting_lug"]["quantity"], "24")
        self.assertEqual(appended["lifting_lug"]["rate"], "38.75")
        self.assertEqual(appended["lifting_lug"]["item_total"], "930.00")
        self.assertIs(appended["lifting_lug"]["pricing"]["multiplier_applied"], False)
        self.assertEqual(evidence["current_totals"]["sub_total"], "5942.00")
        self.assertEqual(evidence["expected_totals"]["sub_total"], "9632.00")
        self.assertEqual(evidence["expected_totals"]["tax_total"], "0.00")
        self.assertEqual(evidence["expected_totals"]["total"], "9632.00")

    def test_the_estimate_plan_prices_the_two_additions_at_3_6_times_cost(self) -> None:
        evidence = self.plan_json(self.stage(action=j26.ACTION_ESTIMATE))["live_evidence"]
        appended = {row["key"]: row for row in evidence["appended_lines"]}
        self.assertEqual(appended["dip_tube"]["rate"], "1656.00")
        self.assertEqual(appended["dip_tube"]["item_total"], "9936.00")
        self.assertEqual(appended["dip_tube"]["pricing"]["unrounded"], "1656.000")
        self.assertEqual(appended["lifting_lug"]["rate"], "139.50")
        self.assertEqual(appended["lifting_lug"]["item_total"], "3348.00")
        self.assertEqual(appended["lifting_lug"]["pricing"]["supplier_unit_cost_usd"], "38.75")
        self.assertEqual(appended["lifting_lug"]["pricing"]["multiplier"], "3.6")
        self.assertEqual(appended["lifting_lug"]["pricing"]["unrounded"], "139.500")
        self.assertEqual(evidence["current_totals"]["sub_total"], "21391.20")
        self.assertEqual(evidence["current_totals"]["tax_total"], "2780.86")
        self.assertEqual(evidence["expected_totals"]["sub_total"], "34675.20")
        self.assertEqual(evidence["expected_totals"]["tax_total"], "4507.78")
        self.assertEqual(evidence["expected_totals"]["total"], "39182.98")

    def test_the_plan_states_that_it_creates_no_zoho_item_and_sends_no_email(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                plan = self.plan_json(self.stage(action=action))
                self.assertIs(plan["live_evidence"]["creates_zoho_item"], False)
                self.assertIs(plan["live_evidence"]["email_sent"], False)
                self.assertIs(plan["risk"]["creates_zoho_item"], False)
                self.assertIs(plan["risk"]["email_sent"], False)
                self.assertIs(plan["risk"]["reversible"], False)
                self.assertIs(plan["risk"]["single_put"], True)
                self.assertIn("no mail transport", plan["risk"]["note"])

    def test_the_plan_records_the_source_digests(self) -> None:
        evidence = self.plan_json(self.stage())["live_evidence"]
        recorded = {entry["name"]: entry["sha256"] for entry in evidence["sources"]}
        self.assertEqual(recorded, {
            "DIP TUBE.pdf":
                "5f9ac494770c7c0193a2c08a32c47300ba927b2e339362ed8edd17e06d65df04",
            "Quotation of Dip Tube - revised.xlsx":
                "d066ac0ab0a500623c9e46a45ba9beefa82bbb1f939ed647dadf0171ad5bb5fd",
            "ANCHOR CLIPS.pdf":
                "0e919b082b43cdb6201c1f9236dec85d4d94e4df7bc968ba50f8fb4c4f340364",
        })

    def test_the_stage_summary_shows_the_arithmetic_and_the_blockers(self) -> None:
        self.stage(action=j26.ACTION_ESTIMATE)
        output = self.last_stage_output
        self.assertIn("38.75", output)
        self.assertIn("3.6", output)
        self.assertIn("139.50", output)
        self.assertIn("COMMIT WILL REFUSE", output)
        self.assertIn("NO WRITE HAS BEEN MADE", output)
        self.assertIn("PO-00010 is NOT touched", output)


# ---------------------------------------------------------------------------
# Rounding
# ---------------------------------------------------------------------------


class RoundingTests(J26TestCase):
    def test_the_multiplier_rounds_half_up_to_two_decimals(self) -> None:
        cases = (
            ("38.75", "139.50"),
            ("10.00", "36.00"),
            ("1.115", "4.01"),      # 4.014 -> 4.01
            ("1.1125", "4.01"),     # 4.005 -> 4.01 half UP, not banker's
            ("0.9875", "3.56"),     # 3.555 -> 3.56 half UP
            ("460.00", "1656.00"),
        )
        for cost, expected in cases:
            with self.subTest(cost=cost):
                intent = {"lifting_lug_supplier_unit_cost_usd": {
                    "value": cost, "source": "x", "artifact_name": "x",
                    "artifact_path": "x", "artifact_sha256": "x", "artifact_bytes": 1,
                }}
                pricing = j26.lug_pricing(j26.ACTION_ESTIMATE, intent)
                self.assertEqual(pricing["posted_rate"], expected)
                self.assertEqual(
                    Decimal(pricing["unrounded"]), Decimal(cost) * Decimal("3.6")
                )

    def test_bankers_rounding_is_not_used(self) -> None:
        # Decimal's default ROUND_HALF_EVEN would give 4.00 here.
        self.assertEqual(
            j26.money_text(Decimal("1.1125") * Decimal("3.6")), "4.01"
        )

    def test_the_purchase_order_never_multiplies(self) -> None:
        intent = {"lifting_lug_supplier_unit_cost_usd": {
            "value": "38.75", "source": "x", "artifact_name": "x",
            "artifact_path": "x", "artifact_sha256": "x", "artifact_bytes": 1,
        }}
        pricing = j26.lug_pricing(j26.ACTION_PURCHASE_ORDER, intent)
        self.assertEqual(pricing["posted_rate"], "38.75")
        self.assertIs(pricing["multiplier_applied"], False)


# ---------------------------------------------------------------------------
# Disclosed source problems
# ---------------------------------------------------------------------------


class DisclosureTests(J26TestCase):
    def test_the_air_shipment_cut_is_disclosed_not_invented(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                evidence = self.plan_json(self.stage(action=action))["live_evidence"]
                keys = {entry["key"] for entry in evidence["disclosures"]}
                self.assertIn("dip_tube_air_shipment_cut_not_in_any_source", keys)
                statement = next(
                    entry["statement"] for entry in evidence["disclosures"]
                    if entry["key"] == "dip_tube_air_shipment_cut_not_in_any_source"
                )
                self.assertIn("NEITHER source establishes it", statement)
                # The quantity is NOT changed to represent a cut.
                dip = next(
                    row for row in evidence["appended_lines"] if row["key"] == "dip_tube"
                )
                self.assertEqual(dip["quantity"], "6")
                self.assertNotIn("three sections", dip["description"])
                self.assertNotIn("cut", dip["description"].casefold())

    def test_the_workbook_freight_inconsistency_is_disclosed_and_no_freight_line_is_added(
        self,
    ) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                evidence = self.plan_json(self.stage(action=action))["live_evidence"]
                statement = next(
                    entry["statement"] for entry in evidence["disclosures"]
                    if entry["key"] == "dip_tube_workbook_freight_cell_inconsistent"
                )
                self.assertIn("2,500", statement)
                self.assertIn("1,800", statement)
                self.assertIn("4,560", statement)
                self.assertEqual(len(evidence["appended_lines"]), 2)
                for row in evidence["appended_lines"]:
                    self.assertNotIn("freight", row["name"].casefold())
                    self.assertNotIn("shipping", row["name"].casefold())
                # The existing notes header is resent verbatim, never rewritten.
                payload = evidence["put_payload"]
                before_notes = evidence["record"]["before_state"].get("notes")
                if before_notes:
                    self.assertEqual(payload["notes"], before_notes)

    def test_the_ss316l_versus_ss316_split_is_disclosed_in_the_line_itself(self) -> None:
        evidence = self.plan_json(self.stage())["live_evidence"]
        lug = next(row for row in evidence["appended_lines"] if row["key"] == "lifting_lug")
        self.assertIn("SS316L", lug["name"])
        self.assertIn("22-inch bar", lug["name"])
        self.assertIn("SS316L", lug["description"])
        self.assertIn("SS 316", lug["description"])
        self.assertIn("ANCHOR CLIPS.pdf", lug["description"])
        keys = {entry["key"] for entry in evidence["disclosures"]}
        self.assertIn("lifting_lug_material_grade_two_sources_disagree", keys)
        self.assertIn("lifting_lug_bar_length_is_email_not_drawing", keys)

    def test_the_dip_tube_description_carries_the_drawing_facts(self) -> None:
        for fragment in (
            "Derakane 441/MEKP", "2.00 inch ID", "164.00 inch length", "0.65 inch",
            "CCMMMM", "MMMMCC", "13 holes on each side", "0.50 inch holes",
            "6 inches between holes",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, j26.DIP_TUBE_DESCRIPTION)

    def test_every_disclosure_reaches_both_plans(self) -> None:
        expected = {entry["key"] for entry in j26.DISCLOSURES}
        self.assertEqual(len(expected), len(j26.DISCLOSURES))
        for action in BOTH:
            with self.subTest(action=action):
                evidence = self.plan_json(self.stage(action=action))["live_evidence"]
                self.assertEqual({e["key"] for e in evidence["disclosures"]}, expected)


# ---------------------------------------------------------------------------
# The lifting-lug price evidence
# ---------------------------------------------------------------------------


class LugCostEvidenceTests(J26TestCase):
    def test_a_missing_lug_cost_refuses_both_plans(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                error = self.stage_expecting_error(action=action, payload={})
                self.assertIn("lifting_lug_supplier_unit_cost_usd", str(error))
                self.assertIn("never guessed", str(error))

    def test_a_cost_without_an_artifact_is_refused(self) -> None:
        payload = {"lifting_lug_supplier_unit_cost_usd": {
            "value": "38.75",
            "source": "Fei quotation email received 2026-08-21 16:20 UTC, line 2.",
        }}
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("asserted price with no artifact", str(error))

    def test_a_wrong_artifact_digest_is_refused(self) -> None:
        payload = self.lug_input(entry={"artifact_sha256": "0" * 64})
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("not the stated", str(error))
        self.assertIn("not evidenced", str(error))

    def test_a_source_artifact_outside_the_fixed_folder_is_refused(self) -> None:
        stray = self.root / "stray_quote.json"
        stray.write_text("{}", encoding="utf-8")
        payload = self.lug_input(entry={
            "artifact_path": str(stray), "artifact_sha256": j26.file_digest(stray),
        })
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("outside the one allowlisted source folder", str(error))

    def test_a_missing_artifact_file_is_refused(self) -> None:
        payload = self.lug_input(entry={
            "artifact_path": str(j26.SOURCE_DIR / "does_not_exist.xlsx"),
        })
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("does not resolve to an existing file", str(error))

    def test_a_relative_artifact_path_is_refused(self) -> None:
        payload = self.lug_input(entry={"artifact_path": "manifest.json"})
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("absolute path", str(error))

    def test_the_three_fixed_sources_cannot_evidence_a_lug_price(self) -> None:
        for entry in j26.FIXED_SOURCES:
            with self.subTest(source=entry["name"]):
                path = j26.SOURCE_DIR / entry["name"]
                payload = self.lug_input(entry={
                    "artifact_path": str(path), "artifact_sha256": entry["sha256"],
                })
                error = self.stage_expecting_error(payload=payload)
                self.assertIn("NONE of them carries a lifting-lug price", str(error))

    def test_blank_generic_and_asserted_only_sources_are_refused(self) -> None:
        cases = (
            ("", "must be nonblank"),
            ("supplier", "names no particular document"),
            ("the quote", "names no particular document"),
            ("as discussed", "names no particular document"),
            ("Fei said around USD 40 each, assumed for now on 2026-08-21", "asserts a price"),
            ("Approximate 2026-08-21 figure of USD 38.75 from Fei", "asserts a price"),
            ("Verbal from Fei on 2026-08-21, USD 38.75 each", "asserts a price"),
            ("TBD, use 38.75 for the 2026-08-21 quote", "asserts a price"),
            ("Fei quotation email of yesterday morning", "no date, amount or document number"),
            ("Fei 2026-08-21", "too short to identify a document"),
        )
        for source, fragment in cases:
            with self.subTest(source=source):
                payload = self.lug_input(entry={"source": source})
                error = self.stage_expecting_error(payload=payload)
                self.assertIn(fragment, str(error))

    def test_a_padded_source_is_refused(self) -> None:
        payload = self.lug_input(entry={
            "source": "  Fei quotation email 2026-08-21 16:20 UTC, USD 38.75 each.  ",
        })
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("must not be padded", str(error))

    def test_a_zero_or_negative_cost_is_refused(self) -> None:
        for value in ("0", "-1", "0.00"):
            with self.subTest(value=value):
                error = self.stage_expecting_error(payload=self.lug_input(entry={"value": value}))
                self.assertIn("greater than zero", str(error))

    def test_a_non_numeric_cost_is_refused(self) -> None:
        error = self.stage_expecting_error(payload=self.lug_input(entry={"value": "cheap"}))
        self.assertIn("not a valid number", str(error))

    def test_the_recorded_evidence_reaches_the_plan(self) -> None:
        evidence = self.plan_json(self.stage())["live_evidence"]
        recorded = evidence["lug_cost_evidence"]
        self.assertEqual(recorded["value"], LUG_COST)
        self.assertEqual(recorded["artifact_sha256"], self.artifact_sha)
        self.assertEqual(recorded["artifact_name"], "manifest.json")
        self.assertIn("2026-08-21", recorded["source"])


# ---------------------------------------------------------------------------
# The closed input schema
# ---------------------------------------------------------------------------


class InputSchemaTests(J26TestCase):
    def test_prohibited_input_fields_are_refused_by_name(self) -> None:
        cases = (
            ("quantity", "fixed by this commission"),
            ("rate", "No line rate is caller-supplied"),
            ("multiplier", "fixed at 3.6"),
            ("description", "fixed constants"),
            ("item_id", "carry no item_id"),
            ("sku", "carry no SKU"),
            ("tax_id", "Tax is fixed"),
            ("estimate_id", "QT-000034 and nothing else"),
            ("purchaseorder_id", "PO-00010 and nothing else"),
            ("status", "No status field is sent"),
            ("reference_number", "preserved, never set"),
            ("notes", "preserved, never changed"),
            ("freight", "No freight line is added"),
            ("email", "no mail transport"),
            ("send", "no mail transport"),
            ("action", "no lifecycle, status or conversion action"),
            ("create_item", "never creates a Zoho item"),
            ("custom_fields", "outside this commission"),
            ("attachment", "cannot attach"),
            ("exchange_rate", "preserved and never set"),
        )
        for field, fragment in cases:
            with self.subTest(field=field):
                payload = self.lug_input()
                payload[field] = "anything"
                error = self.stage_expecting_error(payload=payload)
                self.assertIn(fragment, str(error))

    def test_an_unknown_input_field_is_refused(self) -> None:
        payload = self.lug_input()
        payload["hurry_up"] = True
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("uncommissioned field(s): hurry_up", str(error))

    def test_an_extra_key_inside_the_cost_entry_is_refused(self) -> None:
        payload = self.lug_input()
        payload["lifting_lug_supplier_unit_cost_usd"]["override"] = True
        error = self.stage_expecting_error(payload=payload)
        self.assertIn("must be exactly", str(error))

    def test_the_operator_note_is_optional_and_recorded(self) -> None:
        payload = self.lug_input(operator_source_note="Rachad relayed Fei's revised price.")
        evidence = self.plan_json(self.stage(payload=payload))["live_evidence"]
        self.assertEqual(
            evidence["operator_source_note"], "Rachad relayed Fei's revised price."
        )


# ---------------------------------------------------------------------------
# Live-record drift
# ---------------------------------------------------------------------------


class LiveRecordDriftTests(J26TestCase):
    def test_a_wrong_record_id_is_refused(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                record = LIVE[action]()
                record[j26.TARGETS[action]["id_key"]] = "96274000009999999"
                error = self.stage_expecting_error(action=action, before=record)
                # The GET envelope check fires first: this tool asks for exactly
                # one fixed id, so a record answering with a different id is not
                # the requested record at all.
                self.assertIn(
                    f"Zoho returned no {j26.TARGETS[action]['record']} record for "
                    f"{j26.TARGETS[action]['record_id']}",
                    str(error),
                )

    def test_a_record_identity_gate_refuses_a_swapped_body(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                record = LIVE[action]()
                with self.assertRaises(j26.J26RevisionToolError) as caught:
                    j26.validate_live_record(
                        action, dict(record, **{j26.TARGETS[action]["id_key"]: "96274000009999999"})
                    )
                self.assertIn("one record per action", str(caught.exception))

    def test_a_wrong_number_is_refused(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                record = LIVE[action]()
                record[j26.TARGETS[action]["number_key"]] = "PO-99999"
                error = self.stage_expecting_error(action=action, before=record)
                self.assertIn("not the fixed", str(error))

    def test_a_wrong_reference_is_refused(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                record = LIVE[action]()
                record["reference_number"] = "J26-999"
                error = self.stage_expecting_error(action=action, before=record)
                self.assertIn("live reference is", str(error))

    def test_a_wrong_customer_or_vendor_is_refused(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                record = LIVE[action]()
                record[j26.TARGETS[action]["party_key"]] = "96274000000099999"
                error = self.stage_expecting_error(action=action, before=record)
                self.assertIn("not the fixed", str(error))

    def test_a_wrong_currency_is_refused(self) -> None:
        for action, wrong in ((j26.ACTION_PURCHASE_ORDER, "CAD"), (j26.ACTION_ESTIMATE, "USD")):
            with self.subTest(action=action):
                record = LIVE[action]()
                record["currency_code"] = wrong
                error = self.stage_expecting_error(action=action, before=record)
                self.assertIn("live currency is", str(error))

    def test_a_moved_status_is_refused(self) -> None:
        cases = (
            (j26.ACTION_PURCHASE_ORDER, ("draft", "billed", "closed", "cancelled", "received")),
            (j26.ACTION_ESTIMATE, ("draft", "sent", "declined", "invoiced", "expired", "void")),
        )
        for action, statuses in cases:
            for status in statuses:
                with self.subTest(action=action, status=status):
                    record = LIVE[action]()
                    record["status"] = status
                    error = self.stage_expecting_error(action=action, before=record)
                    self.assertIn("lifecycle has moved", str(error))

    def test_an_unknown_status_is_still_refused(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                record = LIVE[action]()
                record["status"] = "some_new_zoho_state"
                error = self.stage_expecting_error(action=action, before=record)
                self.assertIn("not the exact", str(error))

    def test_an_unexpected_is_emailed_flag_is_refused(self) -> None:
        record = live_purchase_order()
        record["is_emailed"] = False
        error = self.stage_expecting_error(
            action=j26.ACTION_PURCHASE_ORDER, before=record
        )
        self.assertIn("is_emailed", str(error))

    def test_a_dropped_reordered_or_substituted_line_is_refused(self) -> None:
        for action in BOTH:
            with self.subTest(action=action, case="dropped"):
                record = LIVE[action]()
                record["line_items"].pop()
                error = self.stage_expecting_error(action=action, before=record)
                self.assertIn("line(s), not the", str(error))
            with self.subTest(action=action, case="reordered"):
                record = LIVE[action]()
                record["line_items"][0], record["line_items"][1] = (
                    record["line_items"][1], record["line_items"][0]
                )
                error = self.stage_expecting_error(action=action, before=record)
                self.assertIn("reordered, dropped or replaced", str(error))
            with self.subTest(action=action, case="substituted"):
                record = LIVE[action]()
                record["line_items"][2]["item_id"] = "96274000009999999"
                error = self.stage_expecting_error(action=action, before=record)
                self.assertIn("not the pinned", str(error))
            with self.subTest(action=action, case="extra"):
                record = LIVE[action]()
                record["line_items"].append(copy.deepcopy(record["line_items"][0]))
                error = self.stage_expecting_error(action=action, before=record)
                self.assertIn("line(s), not the", str(error))

    def test_a_changed_original_quantity_or_rate_is_refused(self) -> None:
        for action in BOTH:
            for field in ("quantity", "rate"):
                with self.subTest(action=action, field=field):
                    record = LIVE[action]()
                    record["line_items"][3][field] = 99.0
                    error = self.stage_expecting_error(action=action, before=record)
                    self.assertIn("appends lines and never restates one", str(error))

    def test_a_changed_header_total_is_refused(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                record = LIVE[action]()
                record["sub_total"] = 1.0
                error = self.stage_expecting_error(action=action, before=record)
                self.assertIn("The record changed", str(error))

    def test_a_shipping_charge_or_adjustment_is_refused(self) -> None:
        for action in BOTH:
            for field in ("shipping_charge", "adjustment"):
                with self.subTest(action=action, field=field):
                    record = LIVE[action]()
                    record[field] = 25.0
                    error = self.stage_expecting_error(action=action, before=record)
                    self.assertIn("no commissioned representation", str(error))

    def test_a_line_or_entity_discount_is_refused(self) -> None:
        for action in BOTH:
            with self.subTest(action=action, level="entity"):
                record = LIVE[action]()
                record["discount"] = 5.0
                error = self.stage_expecting_error(action=action, before=record)
                self.assertIn("entity-level discount", str(error))
            with self.subTest(action=action, level="line"):
                record = LIVE[action]()
                record["line_items"][0]["discount"] = 10.0
                error = self.stage_expecting_error(action=action, before=record)
                self.assertIn("line discount", str(error))


# ---------------------------------------------------------------------------
# Lifecycle and downstream links
# ---------------------------------------------------------------------------


class LifecycleTests(CommitCapableTestCase):
    def test_an_invoiced_or_converted_estimate_refuses_before_any_lock(self) -> None:
        cases = (
            ("invoice_ids", ["96274000001111111"]),
            ("salesorders", [{"salesorder_id": "96274000001111112"}]),
            ("invoiced_amount", 100.0),
            ("packages", [{"package_id": "1"}]),
            ("shipments", [{"shipment_id": "1"}]),
            ("payments", [{"payment_id": "1"}]),
        )
        for key, value in cases:
            with self.subTest(key=key):
                record = live_estimate()
                record[key] = value
                error = self.stage_expecting_error(action=j26.ACTION_ESTIMATE, before=record)
                self.assertIn("moved downstream", str(error).replace(
                    "already been converted", "moved downstream"
                ))

    def test_a_billed_or_received_purchase_order_refuses(self) -> None:
        cases = (
            ("bills", [{"bill_id": "1"}], "moved downstream"),
            ("purchasereceives", [{"receive_id": "1"}], "moved downstream"),
            ("salesorder_id", "96274000001111113", "moved downstream"),
            ("billed_status", "billed", "beyond an open"),
            ("received_status", "received", "beyond an open"),
        )
        for key, value, fragment in cases:
            with self.subTest(key=key):
                record = live_purchase_order()
                record[key] = value
                error = self.stage_expecting_error(
                    action=j26.ACTION_PURCHASE_ORDER, before=record
                )
                self.assertIn(fragment, str(error))

    def test_a_deleted_or_void_flag_refuses(self) -> None:
        for action in BOTH:
            for key in ("is_deleted", "is_void", "is_cancelled"):
                with self.subTest(action=action, key=key):
                    record = LIVE[action]()
                    record[key] = True
                    error = self.stage_expecting_error(action=action, before=record)
                    self.assertIn(key, str(error))

    def test_a_conversion_appearing_across_the_write_locks_indeterminate(self) -> None:
        path = self.stage(action=j26.ACTION_ESTIMATE)
        after = revised_record(j26.ACTION_ESTIMATE)
        after["invoice_ids"] = ["96274000001111111"]
        error = self.commit_expecting_error(path, after=after)
        self.assertIn("indeterminate", str(error))
        self.assertTrue(self.lock_exists(path))
        self.assertIs(self.lock_json(path)["permanent_lock"], False)


# ---------------------------------------------------------------------------
# Tax
# ---------------------------------------------------------------------------


class TaxTests(J26TestCase):
    def test_the_appended_estimate_lines_reuse_the_live_on_hst_row(self) -> None:
        evidence = self.plan_json(self.stage(action=j26.ACTION_ESTIMATE))["live_evidence"]
        for row in evidence["appended_lines"]:
            self.assertEqual(row["tax_id"], ON_HST)
        for line in evidence["put_payload"]["line_items"][-2:]:
            self.assertEqual(line["tax_id"], ON_HST)
        self.assertEqual(sorted(evidence["tax_rows_used"]), [ON_HST])
        self.assertEqual(evidence["tax_rows_used"][ON_HST]["tax_percentage"], "13")

    def test_the_appended_purchase_order_lines_carry_no_tax(self) -> None:
        evidence = self.plan_json(
            self.stage(action=j26.ACTION_PURCHASE_ORDER)
        )["live_evidence"]
        for row in evidence["appended_lines"]:
            self.assertEqual(row["tax_id"], "")
        for line in evidence["put_payload"]["line_items"][-2:]:
            self.assertNotIn("tax_id", line)
        self.assertEqual(evidence["tax_rows_used"], {})

    def test_a_repriced_on_hst_is_refused(self) -> None:
        taxes = copy.deepcopy(ACTIVE_TAXES)
        taxes[0]["tax_percentage"] = 15
        error = self.stage_expecting_error(action=j26.ACTION_ESTIMATE, taxes=taxes)
        self.assertIn("not the 13% this plan prices", str(error))

    def test_an_inactive_on_hst_is_refused(self) -> None:
        taxes = copy.deepcopy(ACTIVE_TAXES)
        taxes[0]["is_inactive"] = True
        error = self.stage_expecting_error(action=j26.ACTION_ESTIMATE, taxes=taxes)
        self.assertIn("not active", str(error))

    def test_a_missing_on_hst_is_refused_and_never_created(self) -> None:
        taxes = [row for row in ACTIVE_TAXES if row["tax_id"] != ON_HST]
        error = self.stage_expecting_error(action=j26.ACTION_ESTIMATE, taxes=taxes)
        self.assertIn("cannot create a tax", str(error))

    def test_a_tax_group_in_the_on_hst_slot_is_refused(self) -> None:
        taxes = copy.deepcopy(ACTIVE_TAXES)
        taxes[0]["tax_type"] = "tax_group"
        error = self.stage_expecting_error(action=j26.ACTION_ESTIMATE, taxes=taxes)
        self.assertIn("not a simple tax", str(error))

    def test_an_original_line_taxed_differently_is_refused(self) -> None:
        record = live_estimate()
        record["line_items"][1]["tax_id"] = GST
        error = self.stage_expecting_error(action=j26.ACTION_ESTIMATE, before=record)
        self.assertIn("not the estimate's own", str(error))

    def test_a_taxed_purchase_order_line_is_refused(self) -> None:
        record = live_purchase_order()
        record["line_items"][1]["tax_id"] = GST
        error = self.stage_expecting_error(action=j26.ACTION_PURCHASE_ORDER, before=record)
        self.assertIn("no line on this purchase order is", str(error))

    def test_the_bucket_method_is_corroborated_against_zohos_own_figure(self) -> None:
        evidence = self.plan_json(self.stage(action=j26.ACTION_ESTIMATE))["live_evidence"]
        corroboration = evidence["tax_corroboration"]
        self.assertIs(corroboration["corroborated_against_live_record"], True)
        self.assertEqual(corroboration["live_tax_total"], "2780.86")
        self.assertEqual(corroboration["recomputed_before_tax_total"], "2780.86")
        # The per-line method gives a different cent on this exact record, which
        # is precisely why the choice is settled by evidence and not preference.
        self.assertEqual(corroboration["recomputed_before_per_line_tax_total"], "2780.85")
        self.assertIs(evidence["expected_totals"]["tax_total_asserted"], True)

    def test_a_tax_total_zoho_does_not_reproduce_is_not_asserted(self) -> None:
        record = live_estimate()
        record["tax_total"] = 2780.85
        record["total"] = float(Decimal("21391.20") + Decimal("2780.85"))
        with patch.dict(j26.ESTIMATE_TARGET, {"tax_total": "2780.85", "total": "24172.05"}):
            evidence = self.plan_json(
                self.stage(action=j26.ACTION_ESTIMATE, before=record)
            )["live_evidence"]
        self.assertIs(
            evidence["tax_corroboration"]["corroborated_against_live_record"], False
        )
        self.assertIs(evidence["expected_totals"]["tax_total_asserted"], False)
        self.assertEqual(evidence["expected_totals"]["tax_certainty"], "disclosed_uncertain")


# ---------------------------------------------------------------------------
# The PUT payload
# ---------------------------------------------------------------------------


class PayloadTests(J26TestCase):
    def test_every_original_line_is_resent_once_in_order_with_its_own_ids(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                evidence = self.plan_json(self.stage(action=action))["live_evidence"]
                lines = evidence["put_payload"]["line_items"]
                pinned = j26.TARGET_LINES[action]
                self.assertEqual(len(lines), len(pinned) + 2)
                for line, (line_id, item_id, qty, rate) in zip(lines, pinned):
                    self.assertEqual(line["line_item_id"], line_id)
                    self.assertEqual(line["item_id"], item_id)
                    self.assertEqual(Decimal(str(line["quantity"])), Decimal(qty))
                    self.assertEqual(Decimal(str(line["rate"])), Decimal(rate))
                ids = [line["line_item_id"] for line in lines if "line_item_id" in line]
                self.assertEqual(len(ids), len(set(ids)))

    def test_the_two_appended_lines_carry_no_item_id_sku_or_line_item_id(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                evidence = self.plan_json(self.stage(action=action))["live_evidence"]
                appended = evidence["put_payload"]["line_items"][-2:]
                self.assertEqual(len(appended), 2)
                names = [line["name"] for line in appended]
                self.assertEqual(names, [j26.DIP_TUBE_NAME, j26.LIFTING_LUG_NAME])
                for line in appended:
                    for forbidden in j26.FORBIDDEN_NEW_LINE_KEYS:
                        self.assertNotIn(forbidden, line)
                    self.assertEqual(line["unit"], "pcs")
                    self.assertTrue(line["description"])

    def test_no_status_or_mail_key_is_ever_in_the_payload(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                evidence = self.plan_json(self.stage(action=action))["live_evidence"]
                payload = evidence["put_payload"]
                for forbidden in (
                    "status", "send", "email", "to_mail_ids", "action", "is_emailed",
                    "template_type", "converted", "invoice_id",
                ):
                    self.assertNotIn(forbidden, payload)

    def test_the_header_values_are_the_live_ones(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                path = self.stage(action=action)
                evidence = self.plan_json(path)["live_evidence"]
                payload = evidence["put_payload"]
                before = evidence["record"]["before_state"]
                for key in j26.PUT_HEADER_KEYS[action]:
                    if key in payload:
                        self.assertEqual(payload[key], before[key])
                self.assertEqual(payload.get("currency_id"), before["currency_id"])
                self.assertEqual(
                    Decimal(str(payload["exchange_rate"])),
                    Decimal(str(before["exchange_rate"])),
                )

    def test_the_payload_keys_stay_inside_the_commissioned_set(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                evidence = self.plan_json(self.stage(action=action))["live_evidence"]
                payload = evidence["put_payload"]
                self.assertTrue(set(payload).issubset(j26.ALLOWED_PUT_KEYS[action]))
                self.assertTrue(j26.REQUIRED_PUT_KEYS[action].issubset(payload))


# ---------------------------------------------------------------------------
# The contract-proof gate -- the shipped default
# ---------------------------------------------------------------------------


class ContractProofTests(J26TestCase):
    def test_every_shipped_fact_is_unproven(self) -> None:
        self.assertTrue(j26.CONTRACT_FACTS)
        for key, fact in j26.CONTRACT_FACTS.items():
            with self.subTest(fact=key):
                self.assertIs(fact["proven"], False)
                self.assertTrue(fact["why_unproven"].strip())
                self.assertTrue(fact["what_would_prove_it"].strip())

    def test_both_actions_report_commit_blocked(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                status = j26.contract_status(action)
                self.assertIs(status["all_proven"], False)
                self.assertIs(status["commit_blocked"], True)
                self.assertEqual(
                    sorted(status["unproven"]), sorted(j26.ACTION_REQUIRED_FACTS[action])
                )

    def test_the_shipped_build_refuses_commit_before_lock_vault_token_and_network(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                path = self.stage(action=action)
                vault_opened = {"count": 0}

                def exploding_vault():
                    vault_opened["count"] += 1
                    raise AssertionError("the vault must not be opened")

                with patch.object(j26, "PLAN_DIR", self.plan_dir), patch.object(
                    j26.zoho_tool, "append_receipt"
                ), patch.object(
                    j26.zoho_tool, "load_vault", side_effect=exploding_vault
                ), patch.object(
                    j26.zoho_tool, "refresh_access_token",
                    side_effect=AssertionError("no token"),
                ), patch.object(
                    j26.zoho_tool, "api_get", side_effect=AssertionError("no read")
                ), patch.object(
                    j26, "urlopen", side_effect=AssertionError("no network")
                ):
                    with self.assertRaises(j26.J26RevisionToolError) as caught:
                        COMMIT_COMMAND[action](argparse.Namespace(
                            plan=str(path), approval=j26.APPROVAL_WORD,
                            approval_message_utc=after_plan(self.plan_json(path)),
                        ))
                message = str(caught.exception)
                self.assertIn("not proven here", message)
                self.assertIn("will not invent a payload shape", message)
                self.assertEqual(vault_opened["count"], 0)
                self.assertFalse(self.lock_exists(path))

    def test_the_plan_discloses_every_unproven_fact(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                evidence = self.plan_json(self.stage(action=action))["live_evidence"]
                contract = evidence["contract"]
                self.assertIs(contract["commit_blocked"], True)
                self.assertEqual(
                    [fact["key"] for fact in contract["facts"]],
                    list(j26.ACTION_REQUIRED_FACTS[action]),
                )
                for fact in contract["facts"]:
                    self.assertIs(fact["proven"], False)
                    self.assertTrue(fact["what_would_prove_it"])

    def test_a_hand_edited_contract_claim_cannot_unblock_a_plan(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        for fact in plan["live_evidence"]["contract"]["facts"]:
            fact["proven"] = True
        plan["live_evidence"]["contract"]["all_proven"] = True
        plan["live_evidence"]["contract"]["commit_blocked"] = False
        plan["live_evidence"]["contract"]["unproven"] = []
        self.resign(path, plan)
        error = self.commit_expecting_error(path, proven=False)
        self.assertIn("canonical projection", str(error))
        self.assertFalse(self.lock_exists(path))


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class ApprovalTests(CommitCapableTestCase):
    def test_a_conditional_or_blank_word_never_commits(self) -> None:
        """2026-08-21 (A3): exact APPROVED is no longer REQUIRED -- his unambiguous
        go to THIS plan counts -- but a condition, a question or a blank still
        refuses before the contract gate, the lock, the vault and the network."""
        for approval in ("approved but wait", "hold on", "APPROVED?", "", "not yet"):
            with self.subTest(approval=approval):
                path = self.stage()
                error = self.commit_expecting_error(path, approval=approval)
                self.assertIn("unambiguous go" if approval else "no approval text", str(error))
                self.assertFalse(self.lock_exists(path))
                self.assertEqual(self.last_calls["puts"], [])

    def test_proceed_sent_before_the_plan_existed_is_refused_by_its_time_not_its_wording(self) -> None:
        """His 'Proceed' of 2026-08-21 authorised the BUILD: it came before any
        plan existed. The timestamp rule keeps that true; the same word sent
        AFTER the plan is his go to it."""
        path = self.stage()
        plan = self.plan_json(path)
        created = j26.parse_plan_time(plan["created_utc"], "creation time")
        early = (created - j26.timedelta(minutes=1)).isoformat()
        error = self.commit_expecting_error(path, approval="Proceed", approval_message_utc=early)
        self.assertIn("BEFORE this plan was created", str(error))
        self.assertFalse(self.lock_exists(path))
        late = (created + j26.timedelta(minutes=1)).isoformat()
        calls = self.commit(path, approval="Proceed", approval_message_utc=late)
        self.assertEqual(len(calls["puts"]), 1)
        lock = self.lock_json(path)
        self.assertEqual(lock["owner_go"], "Proceed")
        self.assertEqual(lock["owner_go_sent_utc"], late)

    def test_approval_is_checked_before_the_contract_gate_and_the_vault(self) -> None:
        path = self.stage()
        with patch.object(j26, "PLAN_DIR", self.plan_dir), patch.object(
            j26, "CONTRACT_FACTS", proven_contract_facts()
        ), patch.object(
            j26.zoho_tool, "load_vault", side_effect=AssertionError("vault must not open")
        ), patch.object(j26, "urlopen", side_effect=AssertionError("no network")):
            with self.assertRaises(j26.J26RevisionToolError) as caught:
                j26.command_commit_estimate_revision(argparse.Namespace(
                    plan=str(path), approval="approved but wait", approval_message_utc=None,
                ))
        self.assertIn("unambiguous go", str(caught.exception))

    def test_an_approval_sent_before_the_plan_existed_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        created = j26.parse_plan_time(plan["created_utc"], "creation time")
        stale = (created - j26.timedelta(minutes=1)).isoformat()
        error = self.commit_expecting_error(path, approval_message_utc=stale)
        self.assertIn("BEFORE this plan", str(error))
        self.assertFalse(self.lock_exists(path))

    def test_an_approval_with_no_time_is_refused_and_names_the_plan_time(self) -> None:
        """A3/A5: without the time of his message, 'after the plan' cannot be
        proven -- refused before the contract gate, the vault and the network,
        naming --approval-message-utc and the plan's creation time."""
        path = self.stage()
        plan = self.plan_json(path)
        created = j26.parse_plan_time(plan["created_utc"], "creation time")
        for missing in (None, ""):
            with self.subTest(missing=missing):
                error = self.commit_expecting_error(path, approval_message_utc=missing)
                self.assertIn("--approval-message-utc", str(error))
                self.assertIn(created.isoformat(), str(error))
                self.assertFalse(self.lock_exists(path))
                self.assertEqual(self.last_calls["puts"], [])

    def test_an_approval_sent_after_expiry_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        expires = j26.parse_plan_time(plan["expires_utc"], "expiry")
        error = self.commit_expecting_error(path, approval_message_utc=expires.isoformat())
        self.assertIn("expired", str(error))

    def test_an_approval_inside_the_window_is_accepted_and_recorded(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        created = j26.parse_plan_time(plan["created_utc"], "creation time")
        moment = (created + j26.timedelta(minutes=3)).isoformat()
        self.commit(path, approval_message_utc=moment)
        result = json.loads(self.last_commit_output)
        self.assertEqual(result["approval_message_utc"], moment)

    def test_the_plan_states_the_caller_side_time_comparison_duty(self) -> None:
        plan = self.plan_json(self.stage())
        binding = plan["approval_binding"]
        self.assertIs(binding["answers_exactly_one_plan"], True)
        self.assertIs(binding["caller_must_compare_message_time"], True)
        self.assertEqual(binding["plan_created_utc"], plan["created_utc"])
        self.assertIn("SENT AFTER", binding["rule"])


# ---------------------------------------------------------------------------
# Plan integrity and tampering
# ---------------------------------------------------------------------------


class PlanIntegrityTests(CommitCapableTestCase):
    def test_editing_the_plan_breaks_its_hash(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["expected_totals"]["total"] = "1.00"
        self.rewrite(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("Plan hash check failed", str(error))

    def test_a_resigned_total_cannot_outvote_the_projection(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["expected_totals"]["total"] = "1.00"
        self.resign(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("canonical projection", str(error))

    def test_a_resigned_rate_cannot_outvote_the_projection(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["put_payload"]["line_items"][-1]["rate"] = 1.0
        self.resign(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("canonical projection", str(error))

    def test_a_resigned_endpoint_cannot_be_redirected(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["put_endpoint"] = "PUT /books/v3/invoices/96274000001598034"
        self.resign(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("canonical projection", str(error))

    def test_a_resigned_source_digest_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["live_evidence"]["sources"][0]["sha256"] = "0" * 64
        self.resign(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("not the pinned one", str(error))

    def test_a_resigned_risk_note_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["risk"]["note"] = "Harmless."
        self.resign(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("exact single-atomic-PUT", str(error))

    def test_a_resigned_reversible_claim_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["risk"]["reversible"] = True
        self.resign(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("exact single-atomic-PUT", str(error))

    def test_a_resigned_intent_cannot_smuggle_a_new_price(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["intent"]["lifting_lug_supplier_unit_cost_usd"]["value"] = "500.00"
        self.resign(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("canonical", str(error))

    def test_a_resigned_generic_source_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["intent"]["lifting_lug_supplier_unit_cost_usd"]["source"] = "supplier"
        self.resign(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("names no particular document", str(error))

    def test_an_expired_plan_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        created = j26.utc_now() - j26.timedelta(hours=30)
        plan["created_utc"] = created.isoformat()
        plan["expires_utc"] = (created + j26.timedelta(hours=24)).isoformat()
        plan["approval_binding"] = j26.approval_binding(
            created, created + j26.timedelta(hours=24)
        )
        self.resign(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("Plan expired", str(error))

    def test_a_lifetime_other_than_24_hours_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        created = j26.parse_plan_time(plan["created_utc"], "creation time")
        expires = created + j26.timedelta(hours=48)
        plan["expires_utc"] = expires.isoformat()
        plan["approval_binding"] = j26.approval_binding(created, expires)
        self.resign(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("exactly a 24-hour lifetime", str(error))

    def test_a_plan_from_a_different_build_is_refused(self) -> None:
        path = self.stage()
        plan = self.plan_json(path)
        plan["tool_version"] = "0.9.0"
        self.resign(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("different tool, build or schema version", str(error))

    def test_a_plan_outside_the_plan_folder_is_refused(self) -> None:
        path = self.stage()
        stray = self.root / "stray_plan.json"
        stray.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        error = self.commit_expecting_error(stray)
        self.assertIn("outside the exact allowlisted plan folder", str(error))


# ---------------------------------------------------------------------------
# The write allowlist and transport
# ---------------------------------------------------------------------------


class WriteContainmentTests(CommitCapableTestCase):
    def _evidence(self, action: str):
        path = self.stage(action=action)
        evidence = self.plan_json(path)["live_evidence"]
        return path, evidence

    def test_only_put_is_accepted(self) -> None:
        for action in BOTH:
            _, evidence = self._evidence(action)
            target = j26.TARGETS[action]
            for verb in ("POST", "PATCH", "DELETE", "GET"):
                with self.subTest(action=action, verb=verb):
                    with self.assertRaises(j26.J26RevisionToolError) as caught:
                        j26.require_put_allowed(
                            action, verb, j26.record_path(action, target["record_id"]),
                            ORG_ID, evidence["put_payload"], evidence["put_payload"], evidence,
                        )
                    self.assertIn("PUT and nothing else", str(caught.exception))

    def test_only_the_one_record_route_is_accepted(self) -> None:
        for action in BOTH:
            _, evidence = self._evidence(action)
            other = j26.TARGETS[
                j26.ACTION_ESTIMATE if action == j26.ACTION_PURCHASE_ORDER
                else j26.ACTION_PURCHASE_ORDER
            ]
            routes = (
                "/books/v3/estimates/96274000009999999",
                "/books/v3/purchaseorders/96274000009999999",
                f"/books/v3/invoices/{j26.TARGETS[action]['record_id']}",
                f"/books/v3/estimates/{j26.TARGETS[action]['record_id']}/status/sent",
                f"/books/v3/estimates/{j26.TARGETS[action]['record_id']}/email",
                j26.record_path(
                    j26.ACTION_ESTIMATE if action == j26.ACTION_PURCHASE_ORDER
                    else j26.ACTION_PURCHASE_ORDER,
                    other["record_id"],
                ),
            )
            for route in routes:
                with self.subTest(action=action, route=route):
                    with self.assertRaises(j26.J26RevisionToolError) as caught:
                        j26.require_put_allowed(
                            action, "PUT", route, ORG_ID,
                            evidence["put_payload"], evidence["put_payload"], evidence,
                        )
                    self.assertIn("and nothing else", str(caught.exception))

    def test_an_extra_payload_key_is_refused_at_the_transport_gate(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                _, evidence = self._evidence(action)
                payload = copy.deepcopy(evidence["put_payload"])
                payload["status"] = "sent"
                with self.assertRaises(j26.J26RevisionToolError) as caught:
                    j26.require_put_allowed(
                        action, "PUT",
                        j26.record_path(action, j26.TARGETS[action]["record_id"]),
                        ORG_ID, payload, evidence["put_payload"], evidence,
                    )
                self.assertIn("uncommissioned field(s)", str(caught.exception))

    def test_an_appended_line_carrying_an_item_id_is_refused_at_the_gate(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                _, evidence = self._evidence(action)
                payload = copy.deepcopy(evidence["put_payload"])
                payload["line_items"][-1]["item_id"] = "96274000001555194"
                with self.assertRaises(j26.J26RevisionToolError) as caught:
                    j26.require_put_allowed(
                        action, "PUT",
                        j26.record_path(action, j26.TARGETS[action]["record_id"]),
                        ORG_ID, payload, evidence["put_payload"], evidence,
                    )
                self.assertIn("never creates a Zoho item", str(caught.exception))

    def test_a_dropped_original_line_is_refused_at_the_gate(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                _, evidence = self._evidence(action)
                payload = copy.deepcopy(evidence["put_payload"])
                payload["line_items"].pop(0)
                with self.assertRaises(j26.J26RevisionToolError) as caught:
                    j26.require_put_allowed(
                        action, "PUT",
                        j26.record_path(action, j26.TARGETS[action]["record_id"]),
                        ORG_ID, payload, evidence["put_payload"], evidence,
                    )
                self.assertIn("every original line plus", str(caught.exception))

    def test_a_reordered_line_is_refused_at_the_gate(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                _, evidence = self._evidence(action)
                payload = copy.deepcopy(evidence["put_payload"])
                payload["line_items"][0], payload["line_items"][1] = (
                    payload["line_items"][1], payload["line_items"][0]
                )
                with self.assertRaises(j26.J26RevisionToolError) as caught:
                    j26.require_put_allowed(
                        action, "PUT",
                        j26.record_path(action, j26.TARGETS[action]["record_id"]),
                        ORG_ID, payload, evidence["put_payload"], evidence,
                    )
                self.assertIn("reviewed order", str(caught.exception))

    def test_a_third_appended_line_is_refused_at_the_gate(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                _, evidence = self._evidence(action)
                payload = copy.deepcopy(evidence["put_payload"])
                payload["line_items"].append(copy.deepcopy(payload["line_items"][-1]))
                with self.assertRaises(j26.J26RevisionToolError) as caught:
                    j26.require_put_allowed(
                        action, "PUT",
                        j26.record_path(action, j26.TARGETS[action]["record_id"]),
                        ORG_ID, payload, evidence["put_payload"], evidence,
                    )
                self.assertIn("every original line plus", str(caught.exception))

    def test_a_renamed_appended_line_is_refused_at_the_gate(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                _, evidence = self._evidence(action)
                payload = copy.deepcopy(evidence["put_payload"])
                payload["line_items"][-1]["name"] = "Something else entirely"
                with self.assertRaises(j26.J26RevisionToolError) as caught:
                    j26.require_put_allowed(
                        action, "PUT",
                        j26.record_path(action, j26.TARGETS[action]["record_id"]),
                        ORG_ID, payload, evidence["put_payload"], evidence,
                    )
                self.assertIn("not one of the two fixed additions", str(caught.exception))

    def test_exactly_one_put_is_attempted(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                path = self.stage(action=action)
                calls = self.commit(path, action=action)
                self.assertEqual(len(calls["puts"]), 1)
                self.assertEqual(calls["puts"][0]["method"], "PUT")

    def test_the_query_string_carries_only_the_organization_id(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                path = self.stage(action=action)
                calls = self.commit(path, action=action)
                url = calls["puts"][0]["url"]
                self.assertTrue(url.endswith(f"?organization_id={ORG_ID}"))
                for forbidden in ("send", "email", "status", "action", "accept"):
                    self.assertNotIn(f"{forbidden}=", url)

    def test_the_lock_exists_before_the_put_leaves(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                path = self.stage(action=action)
                observed = {}

                original = j26.send_put

                def watching(*args, **kwargs):
                    observed["locked"] = self.lock_exists(path)
                    return original(*args, **kwargs)

                with patch.object(j26, "send_put", side_effect=watching):
                    self.commit(path, action=action)
                self.assertIs(observed["locked"], True)

    def test_a_transport_failure_is_reported_and_needs_restage_not_a_permanent_lock(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                path = self.stage(action=action)
                error = self.commit_expecting_error(
                    path, action=action, put_error=URLError("timed out")
                )
                self.assertIn("indeterminate", str(error))
                self.assertIn("re-stage", str(error).casefold())
                self.assertIn("his go to the NEW plan", str(error))
                self.assertNotIn("permanent", str(error).casefold())
                lock = self.lock_json(path)
                self.assertEqual(lock["status"], "indeterminate_needs_restage")
                self.assertIs(lock["permanent_lock"], False)
                self.assertIs(lock["write_attempted"], True)

    def test_a_locked_plan_cannot_be_replayed(self) -> None:
        path = self.stage()
        self.commit(path)
        error = self.commit_expecting_error(path)
        self.assertIn("cannot be replayed", str(error))
        self.assertEqual(self.last_calls["puts"], [])

    def test_no_prohibited_verb_or_route_exists_in_the_module(self) -> None:
        source = Path(j26.__file__).read_text(encoding="utf-8")
        for forbidden in ('method="POST"', 'method="PATCH"', 'method="DELETE"'):
            with self.subTest(verb=forbidden):
                self.assertNotIn(forbidden, source)
        tree = ast.parse(source)
        strings = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        for fragment in (
            "/books/v3/estimates/{estimate_id}/email",
            "/books/v3/items",
            "/inventory/v1/items",
            "/books/v3/invoices",
        ):
            with self.subTest(route=fragment):
                self.assertNotIn(fragment, strings)

    def test_the_read_surface_is_three_routes(self) -> None:
        allowed = ("/books/v3/purchaseorders/1", "/books/v3/estimates/1",
                   "/books/v3/settings/taxes")
        for path in allowed:
            with self.subTest(path=path):
                self.assertEqual(j26.require_read_path(path), path)
        for path in (
            "/books/v3/items/1", "/books/v3/invoices/1", "/books/v3/contacts/1",
            "/inventory/v1/items", "/books/v3/estimates",
        ):
            with self.subTest(path=path):
                with self.assertRaises(j26.J26RevisionToolError):
                    j26.require_read_path(path)


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------


class ScopeTests(CommitCapableTestCase):
    def test_the_prepared_list_carries_both_narrow_update_scopes(self) -> None:
        self.assertIn("ZohoBooks.purchaseorders.UPDATE", tool.SCOPES)
        self.assertIn("ZohoBooks.estimates.UPDATE", tool.SCOPES)
        self.assertIn("ZohoBooks.purchaseorders.UPDATE", tool.ALLOWED_WRITE_SCOPES)

    def test_the_prepare_bat_scope_list_carries_the_new_scope(self) -> None:
        """PREPARE_DADO_ZOHO_ACCESS.bat delegates to zoho_tool.py scope-list.

        The .bat needs no edit of its own -- it copies exactly tool.SCOPES --
        so what has to be pinned is that the copied list now carries the one new
        scope and still carries nothing wider.
        """
        with patch.object(tool.subprocess, "run") as mocked:
            mocked.return_value.returncode = 0
            tool.command_scope_list(argparse.Namespace(copy=True))
        copied = mocked.call_args.kwargs["input"]
        self.assertEqual(copied, ",".join(tool.SCOPES))
        self.assertIn("ZohoBooks.purchaseorders.UPDATE", copied.split(","))
        self.assertIn("ZohoBooks.estimates.UPDATE", copied.split(","))
        for widened in j26.FORBIDDEN_SCOPES:
            with self.subTest(scope=widened):
                self.assertNotIn(widened, copied.split(","))
        bat = (
            Path(j26.__file__).resolve().parents[3] / "PREPARE_DADO_ZOHO_ACCESS.bat"
        )
        self.assertTrue(bat.is_file())
        self.assertIn("scope-list --copy", bat.read_text(encoding="utf-8"))

    def test_no_broader_purchase_order_or_estimate_scope_is_prepared(self) -> None:
        for forbidden in j26.FORBIDDEN_SCOPES:
            with self.subTest(scope=forbidden):
                self.assertNotIn(forbidden, tool.SCOPES)

    def test_staging_refuses_when_the_narrow_update_scope_is_missing(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                needed = j26.ACTION_UPDATE_SCOPES[action]
                scopes = [s for s in tool.SCOPES if s != needed]
                error = self.stage_expecting_error(action=action, scopes=scopes)
                self.assertIn(needed, str(error))
                self.assertIn("PREPARE_DADO_ZOHO_ACCESS.bat", str(error))

    def test_commit_refuses_before_the_lock_when_the_scope_is_missing(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                path = self.stage(action=action)
                needed = j26.ACTION_UPDATE_SCOPES[action]
                scopes = [s for s in tool.SCOPES if s != needed]
                error = self.commit_expecting_error(path, action=action, scopes=scopes)
                self.assertIn(needed, str(error))
                self.assertFalse(self.lock_exists(path))
                self.assertEqual(self.last_calls["puts"], [])

    def test_a_prohibited_broader_scope_refuses_the_whole_tool(self) -> None:
        for widened in (
            "ZohoBooks.purchaseorders.DELETE", "ZohoBooks.estimates.ALL",
            "ZohoInventory.purchaseorders.UPDATE",
        ):
            with self.subTest(scope=widened):
                scopes = list(tool.SCOPES) + [widened]
                # zoho_tool.validate_scopes is the outer gate and raises its own
                # ZohoError first; either refusal names the offending scope.
                with self.assertRaises((j26.J26RevisionToolError, tool.ZohoError)) as caught:
                    self.stage(scopes=scopes)
                self.assertIn(widened, str(caught.exception))

    def test_this_tools_own_gate_names_every_widening_scope(self) -> None:
        for widened in j26.FORBIDDEN_SCOPES:
            with self.subTest(scope=widened):
                scopes = [s for s in tool.SCOPES] + [widened]
                with self.assertRaises(Exception) as caught:
                    j26.require_update_scopes(j26.ACTION_ESTIMATE, scopes)
                self.assertIn(widened, str(caught.exception))

    def test_no_inventory_write_scope_is_used_by_this_tool(self) -> None:
        for scope in j26.ACTION_UPDATE_SCOPES.values():
            with self.subTest(scope=scope):
                self.assertTrue(scope.startswith("ZohoBooks."))


# ---------------------------------------------------------------------------
# Commit and verification
# ---------------------------------------------------------------------------


class CommitTests(CommitCapableTestCase):
    def test_the_whole_purchase_order_flow_is_one_verified_put(self) -> None:
        path = self.stage(action=j26.ACTION_PURCHASE_ORDER)
        calls = self.commit(path, action=j26.ACTION_PURCHASE_ORDER)
        result = json.loads(self.last_commit_output)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["record_number"], "PO-00010")
        self.assertEqual(result["record_status"], "open")
        self.assertEqual(result["original_lines_preserved"], 7)
        self.assertEqual(result["final_line_count"], 9)
        self.assertEqual(result["sub_total"], "9632.00")
        self.assertEqual(result["total"], "9632.00")
        self.assertEqual(result["other_record_untouched"], "QT-000034")
        self.assertIs(result["email_sent"], False)
        self.assertIs(result["creates_zoho_item"], False)
        self.assertIs(result["replay_locked"], True)
        self.assertEqual(len(calls["puts"]), 1)

    def test_the_whole_estimate_flow_is_one_verified_put(self) -> None:
        path = self.stage(action=j26.ACTION_ESTIMATE)
        self.commit(path, action=j26.ACTION_ESTIMATE)
        result = json.loads(self.last_commit_output)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["record_number"], "QT-000034")
        self.assertEqual(result["record_status"], "accepted")
        self.assertEqual(result["sub_total"], "34675.20")
        self.assertEqual(result["tax_total"], "4507.78")
        self.assertEqual(result["total"], "39182.98")
        self.assertEqual(result["other_record_untouched"], "PO-00010")

    def test_a_status_that_moved_across_the_write_locks_indeterminate(self) -> None:
        path = self.stage(action=j26.ACTION_ESTIMATE)
        after = revised_record(j26.ACTION_ESTIMATE)
        after["status"] = "draft"
        error = self.commit_expecting_error(path, after=after)
        self.assertIn("not the preserved 'accepted'", str(error))
        self.assertTrue(self.lock_exists(path))

    def test_an_emailed_flag_cleared_across_the_write_locks_indeterminate(self) -> None:
        path = self.stage(action=j26.ACTION_PURCHASE_ORDER)
        after = revised_record(j26.ACTION_PURCHASE_ORDER)
        after["is_emailed"] = False
        error = self.commit_expecting_error(
            path, action=j26.ACTION_PURCHASE_ORDER, after=after
        )
        self.assertIn("is_emailed", str(error))
        self.assertTrue(self.lock_exists(path))

    def test_an_appended_line_that_came_back_linked_to_an_item_locks_indeterminate(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                path = self.stage(action=action)
                after = revised_record(action)
                after["line_items"][-1]["item_id"] = "96274000001555194"
                error = self.commit_expecting_error(path, action=action, after=after)
                self.assertIn("must create or link NO item", str(error))
                self.assertTrue(self.lock_exists(path))

    def test_an_appended_line_returned_twice_locks_indeterminate(self) -> None:
        path = self.stage(action=j26.ACTION_ESTIMATE)
        after = revised_record(j26.ACTION_ESTIMATE)
        after["line_items"][-1] = copy.deepcopy(after["line_items"][-2])
        after["line_items"][-1]["line_item_id"] = "96274000001999003"
        error = self.commit_expecting_error(path, after=after)
        self.assertIn("Stop and reconcile", str(error))
        self.assertTrue(self.lock_exists(path))

    def test_a_changed_original_line_in_the_readback_locks_indeterminate(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                path = self.stage(action=action)
                after = revised_record(action)
                after["line_items"][2]["quantity"] = 99.0
                error = self.commit_expecting_error(path, action=action, after=after)
                self.assertIn("Stop and reconcile", str(error))
                self.assertTrue(self.lock_exists(path))

    def test_a_protected_header_change_locks_indeterminate(self) -> None:
        path = self.stage(action=j26.ACTION_ESTIMATE)
        readback = revised_record(j26.ACTION_ESTIMATE)
        readback["terms"] = "Payment in advance."
        error = self.commit_expecting_error(path, readback=readback)
        self.assertIn("changed protected field(s)", str(error))
        self.assertTrue(self.lock_exists(path))

    def test_a_wrong_total_in_the_readback_locks_indeterminate(self) -> None:
        path = self.stage(action=j26.ACTION_ESTIMATE)
        readback = revised_record(j26.ACTION_ESTIMATE)
        readback["sub_total"] = 1.0
        error = self.commit_expecting_error(path, readback=readback)
        self.assertIn("sub_total", str(error))
        self.assertTrue(self.lock_exists(path))

    def test_drift_between_staging_and_commit_refuses_before_the_lock(self) -> None:
        for action in BOTH:
            with self.subTest(action=action):
                path = self.stage(action=action)
                drifted = LIVE[action]()
                drifted["notes"] = "Rachad edited the notes after review."
                error = self.commit_expecting_error(path, action=action, before=drifted)
                self.assertIn("changed after review", str(error))
                self.assertFalse(self.lock_exists(path))
                self.assertEqual(self.last_calls["puts"], [])

    def test_a_regenerated_secure_url_alone_does_not_block_the_commit(self) -> None:
        for action, key in (
            (j26.ACTION_PURCHASE_ORDER, "purchaseorder_url"),
            (j26.ACTION_ESTIMATE, "estimate_url"),
        ):
            with self.subTest(action=action):
                path = self.stage(action=action)
                fresh = LIVE[action]()
                fresh[key] = "https://example.invalid/rotated-secure-url"
                calls = self.commit(path, action=action, before=fresh)
                self.assertEqual(len(calls["puts"]), 1)

    def test_a_source_that_disappeared_after_review_refuses_before_the_lock(self) -> None:
        path = self.stage()
        with patch.object(j26, "SOURCE_DIR", self.root / "gone"):
            error = self.commit_expecting_error(path)
        self.assertIn("outside the one allowlisted source folder", str(error))
        self.assertFalse(self.lock_exists(path))

    def test_a_tax_change_after_review_refuses_before_the_lock(self) -> None:
        path = self.stage(action=j26.ACTION_ESTIMATE)
        taxes = copy.deepcopy(ACTIVE_TAXES)
        taxes[0]["tax_percentage"] = 14
        error = self.commit_expecting_error(path, taxes=taxes)
        self.assertIn("not the 13% this plan prices", str(error))
        self.assertFalse(self.lock_exists(path))
        self.assertEqual(self.last_calls["puts"], [])

    def test_a_vanished_lug_artifact_refuses_before_any_lock_or_write(self) -> None:
        path = self.stage()
        moved = self.root / "moved"
        moved.mkdir()
        with patch.object(j26, "SOURCE_DIR", moved):
            error = self.commit_expecting_error(path)
        self.assertIn("lifting_lug_supplier_unit_cost_usd.artifact_path", str(error))
        self.assertFalse(self.lock_exists(path))
        self.assertEqual(self.last_calls["puts"], [])

    def test_a_changed_lug_artifact_refuses_at_commit(self) -> None:
        """The price artifact is re-hashed at commit, not trusted from the plan."""
        path = self.stage()
        intent = self.plan_json(path)["intent"]
        tampered = copy.deepcopy(intent)
        tampered["lifting_lug_supplier_unit_cost_usd"]["artifact_sha256"] = "0" * 64
        with self.assertRaises(j26.J26RevisionToolError) as caught:
            j26.require_live_lug_artifact(tampered)
        self.assertIn("The evidence changed after review", str(caught.exception))
        # And the untampered intent still passes against the real file on disk.
        j26.require_live_lug_artifact(intent)


if __name__ == "__main__":
    unittest.main()
