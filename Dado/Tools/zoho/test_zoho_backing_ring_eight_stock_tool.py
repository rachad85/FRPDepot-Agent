"""Independent no-network safety tests for the fixed eight-item backing-ring stock tool."""
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

import zoho_backing_ring_eight_stock_tool as tool
import zoho_tool

HERE = Path(__file__).resolve().parent
ORG_ID = "110002157575"
ADJUSTMENT_ID = "96274000009999998"


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def fake_item(target: dict) -> dict:
    rate = float(target["sales_rate_cad"])
    return {
        "item_id": target["item_id"], "name": target["name"], "sku": target["sku"],
        "status": "active", "unit": "pcs", "item_type": "inventory",
        "product_type": "goods", "track_inventory": True, "is_combo_product": False,
        "can_be_sold": True, "can_be_purchased": True,
        "rate": rate, "sales_rate": rate, "pricebook_rate": rate,
        "default_price_brackets": [{"start_quantity": 1.0, "end_quantity": 1.0, "pricebook_rate": rate}],
        "sales_margin": "", "purchase_rate": 0.0, "asset_value": "",
        "stock_on_hand": 0.0, "available_stock": 0.0, "available_for_sale_stock": 0.0,
        "actual_available_stock": 0.0, "actual_available_for_sale_stock": 0.0,
        "committed_stock": 0.0, "actual_committed_stock": 0.0,
        "initial_stock": 0.0, "initial_stock_rate": 0.0,
        "purchase_account_id": "96274000000000439",
        "inventory_account_id": "96274000000000442",
        "tax_id": "96274000000035501", "custom_fields": [],
        "last_modified_time": "2026-08-11T20:00:00-0400",
    }


class EightStockToolTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.root = Path(self.temp.name).resolve()
        self.plan_dir = self.root / "plans"
        self.lock_dir = self.plan_dir / ".commit-locks"
        self.items = {target["item_id"]: fake_item(target) for target in tool.TARGETS}
        self.adjustments: dict[str, dict] = {}
        self.write_calls: list[dict] = []
        self.vault = {
            "api_domain": "https://www.zohoapis.ca",
            "inventory_organization_id": ORG_ID,
            "scopes": [tool.INVENTORY_ADJUSTMENT_CREATE_SCOPE],
        }
        self.patchers = [
            mock.patch.object(tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(tool, "LOCK_DIR", self.lock_dir),
            mock.patch.object(tool, "verify_source_files", return_value={"source": {"sha256": "a" * 64}}),
            mock.patch.object(tool.zoho_tool, "load_vault", side_effect=lambda: self.vault),
            mock.patch.object(tool.zoho_tool, "refresh_access_token", side_effect=lambda vault=None: ("token", self.vault)),
            mock.patch.object(tool.zoho_tool, "save_vault"),
            mock.patch.object(tool.zoho_tool, "append_receipt"),
            mock.patch.object(tool.zoho_tool, "api_get", side_effect=self.api_get),
            mock.patch.object(tool, "urlopen", side_effect=AssertionError("live network write forbidden")),
        ]
        started = [patcher.start() for patcher in self.patchers]
        self.verify_sources_mock = started[2]
        self.load_vault_mock = started[3]
        self.refresh_mock = started[4]
        self.api_get_mock = started[7]
        self.urlopen_mock = started[8]

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def api_get(self, token: str, domain: str, path: str) -> dict:
        if path == "/inventory/v1/organizations":
            return {"organizations": [{"organization_id": ORG_ID, "name": "FRP DEPOTS", "currency_code": "CAD"}]}
        if path.startswith("/inventory/v1/items/"):
            item_id = path.split("/inventory/v1/items/", 1)[1].split("?", 1)[0]
            return {"item": copy.deepcopy(self.items[item_id])}
        if path.startswith("/inventory/v1/inventoryadjustments?"):
            return {
                "inventory_adjustments": [
                    {"inventory_adjustment_id": key, "reference_number": value["reference_number"]}
                    for key, value in self.adjustments.items()
                ],
                "page_context": {"has_more_page": False},
            }
        if path.startswith("/inventory/v1/inventoryadjustments/"):
            adjustment_id = path.split("/inventory/v1/inventoryadjustments/", 1)[1].split("?", 1)[0]
            return {"inventory_adjustment": copy.deepcopy(self.adjustments[adjustment_id])}
        raise AssertionError(f"unexpected GET: {path}")

    def stage(self) -> Path:
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            tool.command_stage(argparse.Namespace())
        plans = sorted(self.plan_dir.glob("*.json"))
        self.assertEqual(len(plans), 1)
        self.stage_output = output.getvalue()
        return plans[0]

    def allow_writes(self, fail: bool = False, mutate_adjustment=None) -> None:
        def transport(request, timeout=60):
            payload = json.loads(request.data.decode("utf-8"))
            self.write_calls.append({
                "method": request.get_method(), "url": request.full_url, "payload": payload,
                "lock_exists": bool(list(self.lock_dir.glob("*.json"))),
            })
            if fail:
                raise HTTPError(request.full_url, 400, "test", {}, io.BytesIO(b'{"code":15,"message":"test"}'))
            lines = []
            for source, target in zip(payload["line_items"], tool.TARGETS):
                quantity = float(target["quantity"])
                for field in (
                    "stock_on_hand", "available_stock", "available_for_sale_stock",
                    "actual_available_stock", "actual_available_for_sale_stock",
                ):
                    self.items[target["item_id"]][field] += quantity
                self.items[target["item_id"]]["asset_value"] = float(tool.line_total_cad(target))
                self.items[target["item_id"]]["last_modified_time"] = "2026-08-11T21:00:00-0400"
                lines.append({
                    "line_item_id": str(int(ADJUSTMENT_ID) + len(lines) + 1),
                    "item_id": target["item_id"], "name": target["name"],
                    "description": source["description"], "unit": "pcs",
                    "adjustment_account_id": tool.ADJUSTMENT_ACCOUNT_ID,
                    "adjustment_account_name": tool.ADJUSTMENT_ACCOUNT_NAME,
                    "quantity_adjusted": quantity, "item_total": float(tool.line_total_cad(target)),
                })
            adjustment = {
                "inventory_adjustment_id": ADJUSTMENT_ID, "date": tool.ADJUSTMENT_DATE,
                "reason": tool.ADJUSTMENT_REASON, "reason_id": tool.ADJUSTMENT_REASON_ID,
                "description": tool.ADJUSTMENT_DESCRIPTION, "reference_number": tool.ADJUSTMENT_REFERENCE,
                "adjustment_type": "quantity", "status": "adjusted",
                "total": float(tool.TOTAL_VALUE_CAD), "is_inventory_valuation_pending": False,
                "line_items": lines,
            }
            if mutate_adjustment:
                mutate_adjustment(adjustment)
            self.adjustments[ADJUSTMENT_ID] = adjustment
            return FakeResponse({"code": 0, "inventory_adjustment": {"inventory_adjustment_id": ADJUSTMENT_ID}})

        self.urlopen_mock.side_effect = transport

    def commit(self, plan: Path, approval: str = "APPROVED") -> str:
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            tool.command_commit(argparse.Namespace(plan=str(plan), approval=approval))
        return output.getvalue()


class TestFixedScopeAndArithmetic(EightStockToolTestCase):
    def test_exact_fixed_targets_quantities_and_values(self) -> None:
        self.assertEqual(len(tool.TARGETS), 8)
        self.assertEqual(tuple(row["item_id"] for row in tool.TARGETS), tool.TARGET_IDS)
        self.assertEqual([int(row["quantity"]) for row in tool.TARGETS], [218, 85, 32, 39, 22, 238, 47, 32])
        self.assertEqual(str(tool.FX_RATE), "1.3927")
        self.assertEqual(str(tool.LANDING_FACTOR), "1.20")
        self.assertEqual(str(tool.TOTAL_QUANTITY), "713")
        self.assertEqual(str(tool.TOTAL_VALUE_CAD), "78816.51")
        self.assertEqual(
            [format(tool.line_total_cad(row), ".2f") for row in tool.TARGETS],
            ["5100.62", "2059.80", "855.67", "1303.57", "2206.04", "37786.74", "15866.75", "13637.32"],
        )

    def test_calculation_carries_fx_precision_and_rounds_each_line_once(self) -> None:
        first = tool.TARGETS[0]
        self.assertEqual(tool.exact_unit_cad(first), tool.Decimal("23.397360"))
        self.assertEqual(str(tool.line_total_cad(first)), "5100.62")
        rounded_unit_then_quantity = tool.money(tool.exact_unit_cad(first), "unit") * first["quantity"]
        self.assertNotEqual(rounded_unit_then_quantity, tool.line_total_cad(first))

    def test_stage_is_read_only_and_builds_exact_plan(self) -> None:
        plan = tool.load_plan(str(self.stage()))
        self.urlopen_mock.assert_not_called()
        self.assertEqual(plan["payload"], tool.build_payload())
        self.assertEqual(plan["risk"]["write_count"], 1)
        self.assertTrue(plan["risk"]["atomic"])
        self.assertEqual(plan["approval_required"], "APPROVED")
        self.assertEqual(len(plan["live_evidence"]["items"]), 8)
        self.assertIn("STAGED ONLY - ZERO ZOHO WRITES", self.stage_output)

    def test_payload_changes_no_item_fields_or_rates(self) -> None:
        payload = tool.build_payload()
        self.assertEqual(set(payload), tool.PAYLOAD_FIELDS)
        text = json.dumps(payload).casefold()
        for forbidden in ("purchase_rate", "sales_rate", '"rate"', "initial_stock", "warehouse", "location", "batch", "serial"):
            self.assertNotIn(forbidden, text)

    def test_source_failure_refuses_before_vault_and_network(self) -> None:
        self.verify_sources_mock.side_effect = tool.EightBackingRingStockError("source mismatch")
        with self.assertRaisesRegex(tool.EightBackingRingStockError, "source mismatch"):
            tool.command_stage(argparse.Namespace())
        self.load_vault_mock.assert_not_called()
        self.urlopen_mock.assert_not_called()

    def test_duplicate_reference_refuses_staging(self) -> None:
        self.adjustments["123"] = {"reference_number": tool.ADJUSTMENT_REFERENCE}
        with self.assertRaisesRegex(tool.EightBackingRingStockError, "already exists"):
            tool.command_stage(argparse.Namespace())
        self.urlopen_mock.assert_not_called()

    def test_item_mutations_refuse_staging(self) -> None:
        cases = [("sku", "WRONG"), ("rate", 51), ("purchase_rate", 1), ("track_inventory", False), ("stock_on_hand", 1)]
        target = tool.TARGETS[0]
        for field, value in cases:
            with self.subTest(field=field):
                self.items[target["item_id"]][field] = value
                with self.assertRaises(tool.EightBackingRingStockError):
                    tool.command_stage(argparse.Namespace())
                self.items[target["item_id"]] = fake_item(target)

    def test_hash_and_closed_schema_reject_tampering(self) -> None:
        path = self.stage()
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan["payload"]["line_items"][0]["quantity_adjusted"] = 219
        path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(tool.EightBackingRingStockError, "hash"):
            tool.load_plan(str(path))

    def test_withdrawn_wording_plan_is_explicitly_refused(self) -> None:
        path = self.root / "withdrawn.json"
        path.write_text(json.dumps({
            "sha256": "fa5d1ab504f45993ea5d595f13575938ec1194a608b0ce61bcdd0171fbeb099b"
        }), encoding="utf-8")
        with self.assertRaisesRegex(tool.EightBackingRingStockError, "withdrawn before approval"):
            tool.load_plan(str(path))

    def test_plan_lifetime_is_exactly_24_hours(self) -> None:
        plan = tool.load_plan(str(self.stage()))
        self.assertEqual(tool.parse_utc(plan["expires_utc"], "expires") - tool.parse_utc(plan["created_utc"], "created"), timedelta(hours=24))


class TestCommitSafetyAndVerification(EightStockToolTestCase):
    def test_approval_is_byte_exact_before_vault_or_network(self) -> None:
        plan = self.stage()
        self.load_vault_mock.reset_mock()
        for bad in ("approved", " APPROVED", "APPROVED ", "yes", ""):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(tool.EightBackingRingStockError, "exactly"):
                    self.commit(plan, bad)
        self.load_vault_mock.assert_not_called()
        self.urlopen_mock.assert_not_called()

    def test_missing_scope_refuses_before_refresh_network_and_lock(self) -> None:
        plan = self.stage()
        self.refresh_mock.reset_mock()
        self.api_get_mock.reset_mock()
        self.vault["scopes"] = []
        with self.assertRaisesRegex(tool.EightBackingRingStockError, "REFUSED BEFORE LOCK"):
            self.commit(plan)
        self.refresh_mock.assert_not_called()
        self.api_get_mock.assert_not_called()
        self.assertFalse(self.lock_dir.exists())
        self.urlopen_mock.assert_not_called()

    def test_live_state_change_refuses_before_lock(self) -> None:
        plan = self.stage()
        self.items[tool.TARGETS[0]["item_id"]]["actual_available_stock"] = 1
        with self.assertRaises(tool.EightBackingRingStockError):
            self.commit(plan)
        self.assertFalse(self.lock_dir.exists())
        self.urlopen_mock.assert_not_called()

    def test_success_is_one_locked_post_and_verifies_stock(self) -> None:
        plan = self.stage()
        before = copy.deepcopy(self.items)
        self.allow_writes()
        output = self.commit(plan)
        self.assertIn("COMMITTED AND VERIFIED", output)
        self.assertEqual(len(self.write_calls), 1)
        self.assertEqual(self.write_calls[0]["method"], "POST")
        self.assertTrue(self.write_calls[0]["lock_exists"])
        self.assertIn("/inventory/v1/inventoryadjustments?", self.write_calls[0]["url"])
        self.assertEqual(self.write_calls[0]["payload"], tool.build_payload())
        for target in tool.TARGETS:
            item = self.items[target["item_id"]]
            self.assertEqual(item["stock_on_hand"], float(target["quantity"]))
            self.assertEqual(item["actual_available_stock"], float(target["quantity"]))
            self.assertEqual(item["rate"], before[target["item_id"]]["rate"])
            self.assertEqual(item["purchase_rate"], 0.0)
        lock = next(self.lock_dir.glob("*.json"))
        self.assertEqual(json.loads(lock.read_text(encoding="utf-8"))["state"], "verified")

    def test_readback_mismatch_locks_indeterminate(self) -> None:
        def mutate(adjustment: dict) -> None:
            adjustment["total"] = float(tool.TOTAL_VALUE_CAD) + 1

        self.allow_writes(mutate_adjustment=mutate)
        with self.assertRaisesRegex(tool.EightBackingRingStockError, "total mismatch"):
            self.commit(self.stage())
        self.assertEqual(len(self.write_calls), 1)
        lock = next(self.lock_dir.glob("*.json"))
        record = json.loads(lock.read_text(encoding="utf-8"))
        self.assertEqual(record["state"], "indeterminate")
        self.assertTrue(record["details"]["no_retry"])

    def test_write_failure_locks_and_never_retries(self) -> None:
        self.allow_writes(fail=True)
        with self.assertRaises(tool.EightBackingRingStockError):
            self.commit(self.stage())
        self.assertEqual(len(self.write_calls), 1)
        lock = next(self.lock_dir.glob("*.json"))
        self.assertEqual(json.loads(lock.read_text(encoding="utf-8"))["state"], "indeterminate")

    def test_verified_plan_cannot_replay(self) -> None:
        plan = self.stage()
        self.allow_writes()
        self.commit(plan)
        with self.assertRaisesRegex(tool.EightBackingRingStockError, "already commit-locked"):
            self.commit(plan)
        self.assertEqual(len(self.write_calls), 1)

    def test_transport_refuses_mutated_payload(self) -> None:
        payload = tool.build_payload()
        payload["line_items"][0]["item_total"] += 1
        with self.assertRaisesRegex(tool.EightBackingRingStockError, "REFUSED"):
            tool.perform_create("token", "https://www.zohoapis.ca", ORG_ID, payload)
        self.urlopen_mock.assert_not_called()


class TestStaticSurface(unittest.TestCase):
    def test_global_scope_remains_narrow(self) -> None:
        self.assertIn(tool.INVENTORY_ADJUSTMENT_CREATE_SCOPE, zoho_tool.ALLOWED_WRITE_SCOPES)
        for forbidden in (
            "ZohoInventory.inventoryadjustments.UPDATE", "ZohoInventory.inventoryadjustments.DELETE",
            "ZohoInventory.inventoryadjustments.ALL", "ZohoInventory.fullaccess.all",
        ):
            self.assertNotIn(forbidden, zoho_tool.SCOPES)
            with self.assertRaises(zoho_tool.ZohoError):
                zoho_tool.validate_scopes([forbidden])

    def test_source_has_one_write_call_site_and_no_other_surface(self) -> None:
        source = Path(tool.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "urlopen"]
        self.assertEqual(len(calls), 1)
        lowered = source.casefold()
        for forbidden in (
            "requests.", "selenium", "playwright", "connect_over_cdp", "wp-json",
            "consumer_secret", "mail.send", "sendmail", 'method="put"',
            'method="delete"', 'method="patch"', "/approve", "/submit", "/reject", "/email",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_cli_exposes_only_stage_and_commit(self) -> None:
        parser = tool.build_parser()
        subparsers = [action for action in parser._actions if action.__class__.__name__ == "_SubParsersAction"]
        self.assertEqual(len(subparsers), 1)
        self.assertEqual(set(subparsers[0].choices), {"stage", "commit"})


if __name__ == "__main__":
    unittest.main()
