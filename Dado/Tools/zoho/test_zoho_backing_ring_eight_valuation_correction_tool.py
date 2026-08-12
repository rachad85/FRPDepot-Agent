"""No-network/adversarial tests for the fixed eight-item value correction."""
from __future__ import annotations

import argparse
import ast
import copy
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

import zoho_backing_ring_eight_valuation_correction_tool as tool
import zoho_tool

HERE = Path(__file__).resolve().parent
ORG_ID = "110002157575"
NEW_ADJUSTMENT_ID = "96274000009999997"


class FakeResponse:
    def __init__(self, payload: dict): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def read(self) -> bytes: return json.dumps(self.payload).encode("utf-8")


def fake_item(target: dict) -> dict:
    q = float(target["quantity"]); rate = float(target["sales_rate_cad"])
    return {
        "item_id": target["item_id"], "name": target["name"], "sku": target["sku"],
        "status": "active", "unit": "pcs", "item_type": "inventory",
        "product_type": "goods", "track_inventory": True, "is_combo_product": False,
        "can_be_sold": True, "can_be_purchased": True,
        "rate": rate, "sales_rate": rate, "pricebook_rate": rate,
        "purchase_rate": 0.0, "asset_value": "", "asset_price": None,
        "average_cost": None, "purchase_price": None,
        "stock_on_hand": q, "available_stock": q, "available_for_sale_stock": q,
        "actual_available_stock": q, "actual_available_for_sale_stock": q,
        "committed_stock": 0.0, "actual_committed_stock": 0.0,
        "initial_stock": 0.0, "initial_stock_rate": 0.0,
        "inventory_account_id": "96274000000000442", "purchase_account_id": "96274000000000439",
        "tax_id": "96274000000035501", "custom_fields": [],
        "last_modified_time": "2026-08-11T21:00:00-0400",
    }


def fake_source_adjustment() -> dict:
    return {
        "inventory_adjustment_id": tool.SOURCE_ADJUSTMENT_ID,
        "date": tool.ADJUSTMENT_DATE, "reason": tool.REASON, "reason_id": tool.REASON_ID,
        "description": "preserved source adjustment description",
        "reference_number": tool.SOURCE_REFERENCE, "adjustment_type": "quantity",
        "status": "adjusted", "total": 0.0, "is_inventory_valuation_pending": False,
        "line_items": [
            {
                "line_item_id": str(96274000001555049 + index), "item_id": target["item_id"],
                "name": target["name"], "description": f"source {target['sku']}",
                "quantity_adjusted": float(target["quantity"]), "item_total": 0.0,
                "unit": "pcs", "adjustment_account_id": tool.ACCOUNT_ID,
            }
            for index, target in enumerate(tool.TARGETS)
        ],
    }


class ValueCorrectionCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.root = Path(self.temp.name).resolve()
        self.plan_dir = self.root / "plans"; self.lock_dir = self.plan_dir / ".commit-locks"
        self.items = {row["item_id"]: fake_item(row) for row in tool.TARGETS}
        self.adjustments = {tool.SOURCE_ADJUSTMENT_ID: fake_source_adjustment()}
        self.write_calls: list[dict] = []
        self.vault = {"api_domain": "https://www.zohoapis.ca", "inventory_organization_id": ORG_ID, "scopes": [tool.CREATE_SCOPE]}
        self.patchers = [
            mock.patch.object(tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(tool, "LOCK_DIR", self.lock_dir),
            mock.patch.object(tool, "verify_source_files", return_value={"sources": {"sha256": "a" * 64}}),
            mock.patch.object(tool.zoho_tool, "load_vault", side_effect=lambda: self.vault),
            mock.patch.object(tool.zoho_tool, "refresh_access_token", side_effect=lambda vault=None: ("token", self.vault)),
            mock.patch.object(tool.zoho_tool, "save_vault"),
            mock.patch.object(tool.zoho_tool, "append_receipt"),
            mock.patch.object(tool.zoho_tool, "api_get", side_effect=self.api_get),
            mock.patch.object(tool, "urlopen", side_effect=AssertionError("live write forbidden")),
        ]
        started = [p.start() for p in self.patchers]
        self.source_mock = started[2]; self.load_mock = started[3]
        self.refresh_mock = started[4]; self.api_mock = started[7]; self.urlopen_mock = started[8]

    def tearDown(self) -> None:
        for p in reversed(self.patchers): p.stop()
        self.temp.cleanup()

    def api_get(self, token: str, domain: str, path: str) -> dict:
        if path == "/inventory/v1/organizations":
            return {"organizations": [{"organization_id": ORG_ID, "name": "FRP DEPOTS", "currency_code": "CAD"}]}
        if path.startswith("/inventory/v1/items/"):
            item_id = path.split("/inventory/v1/items/", 1)[1].split("?", 1)[0]
            return {"item": copy.deepcopy(self.items[item_id])}
        if path.startswith("/inventory/v1/inventoryadjustments?"):
            return {"inventory_adjustments": [{"inventory_adjustment_id": k, "reference_number": v["reference_number"]} for k, v in self.adjustments.items()], "page_context": {"has_more_page": False}}
        if path.startswith("/inventory/v1/inventoryadjustments/"):
            aid = path.split("/inventory/v1/inventoryadjustments/", 1)[1].split("?", 1)[0]
            return {"inventory_adjustment": copy.deepcopy(self.adjustments[aid])}
        raise AssertionError(f"unexpected GET {path}")

    def stage(self) -> Path:
        output = io.StringIO()
        with mock.patch("sys.stdout", output): tool.command_stage(argparse.Namespace())
        paths = list(self.plan_dir.glob("*.json")); self.assertEqual(len(paths), 1)
        self.stage_output = output.getvalue(); return paths[0]

    def allow_write(self, *, fail=False, mutate=None, line_field="value_adjusted") -> None:
        def transport(request, timeout=60):
            payload = json.loads(request.data.decode("utf-8"))
            self.write_calls.append({"method": request.get_method(), "url": request.full_url, "payload": payload, "lock_exists": bool(list(self.lock_dir.glob('*.json')))})
            if fail:
                raise HTTPError(request.full_url, 400, "test", {}, io.BytesIO(b'{"code":15,"message":"test"}'))
            lines = []
            for index, (source, target) in enumerate(zip(payload["line_items"], tool.TARGETS)):
                value = float(tool.line_value_cad(target))
                line = {
                    "line_item_id": str(int(NEW_ADJUSTMENT_ID) + index + 1), "item_id": target["item_id"],
                    "name": target["name"], "description": source["description"], "unit": "pcs",
                    "adjustment_account_id": tool.ACCOUNT_ID, "quantity_adjusted": 0.0,
                }
                line[line_field] = value; lines.append(line)
                self.items[target["item_id"]]["asset_value"] = value
                self.items[target["item_id"]]["average_cost"] = value / float(target["quantity"])
                self.items[target["item_id"]]["last_modified_time"] = "2026-08-11T22:00:00-0400"
            adjustment = {
                "inventory_adjustment_id": NEW_ADJUSTMENT_ID, "date": tool.ADJUSTMENT_DATE,
                "reason": tool.REASON, "reason_id": tool.REASON_ID, "description": tool.DESCRIPTION,
                "reference_number": tool.REFERENCE, "adjustment_type": "value", "status": "adjusted",
                "total": float(tool.TOTAL_VALUE_CAD), "is_inventory_valuation_pending": False,
                "line_items": lines,
            }
            if mutate: mutate(adjustment)
            self.adjustments[NEW_ADJUSTMENT_ID] = adjustment
            return FakeResponse({"code": 0, "inventory_adjustment": {"inventory_adjustment_id": NEW_ADJUSTMENT_ID}})
        self.urlopen_mock.side_effect = transport

    def commit(self, plan: Path, approval="APPROVED") -> str:
        output = io.StringIO()
        with mock.patch("sys.stdout", output): tool.command_commit(argparse.Namespace(plan=str(plan), approval=approval))
        return output.getvalue()


class TestFixedContract(ValueCorrectionCase):
    def test_exact_values_and_total(self):
        self.assertEqual([str(tool.line_value_cad(x)) for x in tool.TARGETS], ["5100.62", "2059.80", "855.67", "1303.57", "2206.04", "37786.74", "15866.75", "13637.32"])
        self.assertEqual(str(tool.TOTAL_VALUE_CAD), "78816.51")
        self.assertEqual(sum((x["quantity"] for x in tool.TARGETS), tool.Decimal("0")), tool.TOTAL_QUANTITY)

    def test_payload_is_value_only(self):
        payload = tool.build_payload()
        self.assertEqual(payload["adjustment_type"], "value")
        self.assertEqual(set(payload), tool.PAYLOAD_FIELDS)
        self.assertEqual(len(payload["line_items"]), 8)
        for line, target in zip(payload["line_items"], tool.TARGETS):
            self.assertEqual(set(line), tool.LINE_FIELDS)
            self.assertEqual(line["value_adjusted"], float(tool.line_value_cad(target)))
            self.assertNotIn("quantity_adjusted", line)
            self.assertNotIn("item_total", line)
        text = json.dumps(payload).casefold()
        for forbidden in ("purchase_rate", "sales_rate", '"rate"', "initial_stock", "line_item_id"):
            self.assertNotIn(forbidden, text)

    def test_stage_is_three_read_rehearsal_and_zero_writes(self):
        plan = tool.load_plan(str(self.stage()))
        self.urlopen_mock.assert_not_called()
        self.assertEqual(plan["payload"], tool.build_payload())
        self.assertEqual(plan["live_evidence"]["stable_read_count"], 3)
        self.assertEqual(self.api_mock.call_count, 1 + 1 + 3 * 9)
        self.assertIn("STAGED ONLY - ZERO ZOHO WRITES", self.stage_output)

    def test_stage_refuses_nonzero_or_wrong_stock(self):
        target = tool.TARGETS[0]
        for field, value in (("stock_on_hand", 217), ("actual_available_stock", 217), ("purchase_rate", 1), ("asset_value", 1), ("rate", 99)):
            with self.subTest(field=field):
                self.items[target["item_id"]][field] = value
                with self.assertRaises(tool.ValueCorrectionError): tool.command_stage(argparse.Namespace())
                self.items[target["item_id"]] = fake_item(target)

    def test_stage_refuses_source_adjustment_drift(self):
        for field, value in (("total", 1), ("status", "draft"), ("adjustment_type", "value")):
            with self.subTest(field=field):
                self.adjustments[tool.SOURCE_ADJUSTMENT_ID][field] = value
                with self.assertRaises(tool.ValueCorrectionError): tool.command_stage(argparse.Namespace())
                self.adjustments[tool.SOURCE_ADJUSTMENT_ID] = fake_source_adjustment()

    def test_stage_refuses_duplicate_reference(self):
        self.adjustments["123"] = {"reference_number": tool.REFERENCE}
        with self.assertRaisesRegex(tool.ValueCorrectionError, "already exists"):
            tool.command_stage(argparse.Namespace())
        self.urlopen_mock.assert_not_called()

    def test_rehearsal_refuses_moving_state(self):
        original = tool.live_round; count = 0
        def moving(*args):
            nonlocal count
            result = original(*args); count += 1
            if count == 2: result["items"][0]["selling_rate_cad"] = "99.00"
            return result
        with mock.patch.object(tool, "live_round", side_effect=moving):
            with self.assertRaisesRegex(tool.ValueCorrectionError, "moved"):
                tool.command_stage(argparse.Namespace())

    def test_hash_closed_schema_and_lifetime(self):
        path = self.stage(); plan = tool.load_plan(str(path))
        self.assertEqual(tool.parse_utc(plan["expires_utc"], "x") - tool.parse_utc(plan["created_utc"], "x"), timedelta(hours=24))
        raw = json.loads(path.read_text()); raw["payload"]["line_items"][0]["value_adjusted"] += 1
        path.write_text(json.dumps(raw))
        with self.assertRaisesRegex(tool.ValueCorrectionError, "hash"): tool.load_plan(str(path))


class TestCommitSafety(ValueCorrectionCase):
    def test_approval_exact_before_vault_and_network(self):
        plan = self.stage(); self.load_mock.reset_mock()
        for bad in ("approved", " APPROVED", "APPROVED ", "yes", ""):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(tool.ValueCorrectionError, "exactly"): self.commit(plan, bad)
        self.load_mock.assert_not_called(); self.urlopen_mock.assert_not_called()

    def test_missing_scope_refuses_before_refresh_and_lock(self):
        plan = self.stage(); self.refresh_mock.reset_mock(); self.vault["scopes"] = []
        with self.assertRaisesRegex(tool.ValueCorrectionError, "REFUSED BEFORE LOCK"): self.commit(plan)
        self.refresh_mock.assert_not_called(); self.assertFalse(self.lock_dir.exists()); self.urlopen_mock.assert_not_called()

    def test_prewrite_state_drift_is_free_refusal(self):
        plan = self.stage(); self.items[tool.TARGETS[0]["item_id"]]["stock_on_hand"] = 217
        with self.assertRaises(tool.ValueCorrectionError): self.commit(plan)
        self.assertFalse(self.lock_dir.exists()); self.urlopen_mock.assert_not_called()

    def test_success_is_one_locked_post_and_quantity_unchanged(self):
        plan = self.stage(); before = copy.deepcopy(self.items); source_before = copy.deepcopy(self.adjustments[tool.SOURCE_ADJUSTMENT_ID])
        self.allow_write(); output = self.commit(plan)
        self.assertIn("COMMITTED AND VERIFIED", output)
        self.assertEqual(len(self.write_calls), 1); self.assertEqual(self.write_calls[0]["method"], "POST")
        self.assertTrue(self.write_calls[0]["lock_exists"]); self.assertEqual(self.write_calls[0]["payload"], tool.build_payload())
        for target in tool.TARGETS:
            after = self.items[target["item_id"]]; prior = before[target["item_id"]]
            for field in tool.STOCK_FIELDS: self.assertEqual(after[field], prior[field])
            self.assertEqual(after["rate"], prior["rate"]); self.assertEqual(after["purchase_rate"], prior["purchase_rate"])
        self.assertEqual(self.adjustments[tool.SOURCE_ADJUSTMENT_ID], source_before)
        lock = next(self.lock_dir.glob("*.json")); self.assertEqual(json.loads(lock.read_text())["state"], "verified")

    def test_response_can_expose_item_total_instead_of_value_adjusted(self):
        self.allow_write(line_field="item_total")
        self.assertIn("COMMITTED AND VERIFIED", self.commit(self.stage()))

    def test_readback_line_or_total_mismatch_locks_indeterminate(self):
        def mutate(a): a["line_items"][0]["value_adjusted"] += 1
        self.allow_write(mutate=mutate)
        with self.assertRaisesRegex(tool.ValueCorrectionError, "line value mismatch"): self.commit(self.stage())
        self.assertEqual(len(self.write_calls), 1)
        lock = next(self.lock_dir.glob("*.json")); self.assertEqual(json.loads(lock.read_text())["state"], "indeterminate")

    def test_write_failure_locks_and_never_retries(self):
        self.allow_write(fail=True); plan = self.stage()
        with self.assertRaises(tool.ValueCorrectionError): self.commit(plan)
        self.assertEqual(len(self.write_calls), 1)
        with self.assertRaisesRegex(tool.ValueCorrectionError, "already commit-locked"): self.commit(plan)
        self.assertEqual(len(self.write_calls), 1)

    def test_transport_rejects_any_payload_mutation(self):
        payload = tool.build_payload(); payload["line_items"][0]["value_adjusted"] += 1
        with self.assertRaisesRegex(tool.ValueCorrectionError, "REFUSED"): tool.perform_create("t", "https://www.zohoapis.ca", ORG_ID, payload)
        self.urlopen_mock.assert_not_called()


class TestStaticSurface(unittest.TestCase):
    def test_scope_is_create_only(self):
        self.assertIn(tool.CREATE_SCOPE, zoho_tool.ALLOWED_WRITE_SCOPES)
        for forbidden in ("ZohoInventory.inventoryadjustments.UPDATE", "ZohoInventory.inventoryadjustments.DELETE", "ZohoInventory.inventoryadjustments.ALL", "ZohoInventory.fullaccess.all"):
            self.assertNotIn(forbidden, zoho_tool.SCOPES)
            with self.assertRaises(zoho_tool.ZohoError): zoho_tool.validate_scopes([forbidden])

    def test_one_write_call_site_and_no_broad_surface(self):
        source = Path(tool.__file__).read_text(encoding="utf-8"); tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "urlopen"]
        self.assertEqual(len(calls), 1)
        lowered = source.casefold()
        for forbidden in ("requests.", "selenium", "playwright", "connect_over_cdp", 'method="put"', 'method="delete"', 'method="patch"', "/approve", "/submit", "/reject", "/email", "sendmail", "mail.send"):
            self.assertNotIn(forbidden, lowered)

    def test_cli_is_only_stage_and_commit(self):
        parser = tool.build_parser(); subs = [a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"]
        self.assertEqual(len(subs), 1); self.assertEqual(set(subs[0].choices), {"stage", "commit"})


if __name__ == "__main__": unittest.main(verbosity=2)
