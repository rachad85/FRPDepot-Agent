"""Independent no-network safety tests for the fixed backing-ring stock tool."""
from __future__ import annotations

import argparse
import ast
import copy
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

import zoho_backing_ring_stock_tool as stock_tool
import zoho_tool

HERE = Path(__file__).resolve().parent
ORG_ID = "110002157575"
BOOKS_ORG_ID = "110002157576"
ADJUSTMENT_ID = "96274000009999999"


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
    committed = float(target["invoice_quantity"])
    return {
        "item_id": target["item_id"],
        "name": target["name"],
        "sku": target["sku"],
        "status": "active",
        "unit": "pcs",
        "item_type": "inventory",
        "product_type": "goods",
        "track_inventory": True,
        "is_combo_product": False,
        "can_be_sold": True,
        "can_be_purchased": True,
        "rate": float(target["invoice_rate_cad"]),
        "sales_rate": float(target["invoice_rate_cad"]),
        "pricebook_rate": float(target["invoice_rate_cad"]),
        "default_price_brackets": [{"start_quantity": 1.0, "end_quantity": 1.0, "pricebook_rate": float(target["invoice_rate_cad"])}],
        "sales_margin": "",
        "purchase_rate": float(target["purchase_rate_cad"]),
        "stock_on_hand": 0.0,
        "available_stock": -committed,
        "available_for_sale_stock": -committed,
        "actual_available_stock": 0.0,
        "actual_available_for_sale_stock": -committed,
        "committed_stock": 0.0,
        "actual_committed_stock": committed,
        "initial_stock": 0.0,
        "initial_stock_rate": 0.0,
        "purchase_account_id": "96274000000000439",
        "inventory_account_id": "96274000000000442",
        "tax_id": "",
        "custom_fields": [],
        "last_modified_time": "2026-08-11T10:00:00-0400",
    }


def fake_invoice() -> dict:
    return {
        "invoice_id": stock_tool.INVOICE_ID,
        "invoice_number": stock_tool.INVOICE_NUMBER,
        "reference_number": stock_tool.INVOICE_REFERENCE,
        "customer_id": "96274000001552001",
        "customer_name": "Ralmax Contracting Ltd.",
        "status": "overdue",
        "line_items": [
            {
                "line_item_id": target["invoice_line_item_id"],
                "item_id": target["item_id"],
                "name": target["name"],
                "sku": target["sku"],
                "quantity": float(target["invoice_quantity"]),
                "rate": float(target["invoice_rate_cad"]),
            }
            for target in stock_tool.TARGETS
        ],
    }


class StockToolTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.root = Path(self.temp.name).resolve()
        self.plan_dir = self.root / "plans"
        self.lock_dir = self.plan_dir / ".commit-locks"
        self.items = {row["item_id"]: fake_item(row) for row in stock_tool.TARGETS}
        self.invoice = fake_invoice()
        self.adjustments: dict[str, dict] = {}
        self.write_calls: list[dict] = []
        self.vault = {
            "api_domain": "https://www.zohoapis.ca",
            "inventory_organization_id": ORG_ID,
            "books_organization_id": BOOKS_ORG_ID,
            "scopes": [
                stock_tool.INVENTORY_ADJUSTMENT_CREATE_SCOPE,
                stock_tool.ITEM_UPDATE_SCOPE,
            ],
        }
        self.patchers = [
            mock.patch.object(stock_tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(stock_tool, "LOCK_DIR", self.lock_dir),
            mock.patch.object(stock_tool, "verify_source_files", return_value={"source": {"sha256": "a" * 64}}),
            mock.patch.object(stock_tool.zoho_tool, "load_vault", side_effect=lambda: self.vault),
            mock.patch.object(stock_tool.zoho_tool, "refresh_access_token", side_effect=lambda vault=None: ("token", self.vault)),
            mock.patch.object(stock_tool.zoho_tool, "save_vault"),
            mock.patch.object(stock_tool.zoho_tool, "append_receipt"),
            mock.patch.object(stock_tool.zoho_tool, "api_get", side_effect=self.api_get),
            mock.patch.object(stock_tool, "urlopen", side_effect=AssertionError("live network write forbidden")),
        ]
        started = [p.start() for p in self.patchers]
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
        if path.startswith(f"/books/v3/invoices/{stock_tool.INVOICE_ID}?"):
            return {"invoice": copy.deepcopy(self.invoice)}
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
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            stock_tool.command_stage(argparse.Namespace())
        plans = sorted(self.plan_dir.glob("*.json"))
        self.assertEqual(len(plans), 1)
        self.stage_output = out.getvalue()
        return plans[0]

    def allow_writes(self, fail_at: int | None = None, mutate_adjustment=None) -> None:
        def transport(request, timeout=60):
            payload = json.loads(request.data.decode("utf-8"))
            record = {
                "method": request.get_method(),
                "url": request.full_url,
                "payload": payload,
                "lock_exists": bool(list(self.lock_dir.glob("*.json"))),
            }
            self.write_calls.append(record)
            if fail_at is not None and len(self.write_calls) == fail_at:
                raise HTTPError(request.full_url, 400, "test failure", {}, io.BytesIO(b'{"code":15,"message":"test"}'))
            if request.get_method() == "POST":
                lines = []
                for source, target in zip(payload["line_items"], stock_tool.TARGETS):
                    quantity = float(target["quantity_adjusted"])
                    for field in (
                        "stock_on_hand", "available_stock", "available_for_sale_stock",
                        "actual_available_stock", "actual_available_for_sale_stock",
                    ):
                        self.items[target["item_id"]][field] += quantity
                    lines.append({
                        "line_item_id": str(int(ADJUSTMENT_ID) + len(lines) + 1),
                        "item_id": target["item_id"],
                        "name": target["name"],
                        "unit": "pcs",
                        "adjustment_account_id": stock_tool.ADJUSTMENT_ACCOUNT_ID,
                        "adjustment_account_name": stock_tool.ADJUSTMENT_ACCOUNT_NAME,
                        "quantity_adjusted": quantity,
                        "item_total": float(target["item_total_cad"]),
                    })
                adjustment = {
                    "inventory_adjustment_id": ADJUSTMENT_ID,
                    "date": stock_tool.ADJUSTMENT_DATE,
                    "reason": stock_tool.ADJUSTMENT_REASON,
                    "reason_id": stock_tool.ADJUSTMENT_REASON_ID,
                    "description": stock_tool.ADJUSTMENT_DESCRIPTION,
                    "reference_number": stock_tool.ADJUSTMENT_REFERENCE,
                    "adjustment_type": "quantity",
                    "status": "adjusted",
                    "total": 18421.5,
                    "line_items": lines,
                }
                if mutate_adjustment is not None:
                    mutate_adjustment(adjustment)
                self.adjustments[ADJUSTMENT_ID] = adjustment
                return FakeResponse({"code": 0, "inventory_adjustment": {"inventory_adjustment_id": ADJUSTMENT_ID}})
            item_id = request.full_url.split("/items/", 1)[1].split("?", 1)[0]
            item = self.items[item_id]
            item["name"] = payload["name"]
            item["rate"] = payload["rate"]
            item["sales_rate"] = payload["rate"]
            item["pricebook_rate"] = payload["rate"]
            item["default_price_brackets"] = [{"start_quantity": 1.0, "end_quantity": 1.0, "pricebook_rate": payload["rate"]}]
            item["last_modified_time"] = "2026-08-11T18:00:00-0400"
            return FakeResponse({"code": 0, "message": "success", "item": {"item_id": item_id}})

        self.urlopen_mock.side_effect = transport

    def commit(self, plan: Path, approval: str = "APPROVED") -> str:
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            stock_tool.command_commit(argparse.Namespace(plan=str(plan), approval=approval))
        return out.getvalue()


class TestFixedScopeAndStaging(StockToolTestCase):
    def test_exact_fixed_business_constants(self) -> None:
        self.assertEqual(stock_tool.TARGET_IDS, ("96274000001518002", "96274000001518014"))
        self.assertEqual([str(x["quantity_adjusted"]) for x in stock_tool.TARGETS], ["12", "101"])
        self.assertEqual([str(x["target_rate_cad"]) for x in stock_tool.TARGETS], ["108.00", "468.00"])
        self.assertEqual([str(x["item_total_cad"]) for x in stock_tool.TARGETS], ["696.00", "17725.50"])
        self.assertEqual(stock_tool.INVOICE_ID, "96274000001559012")

    def test_stage_is_read_only_and_builds_exact_plan(self) -> None:
        plan_path = self.stage()
        self.urlopen_mock.assert_not_called()
        plan = stock_tool.load_plan(str(plan_path))
        self.assertEqual(plan["payload"], stock_tool.build_payload())
        self.assertEqual(plan["risk"]["write_count"], 3)
        self.assertFalse(plan["risk"]["atomic"])
        self.assertEqual(plan["approval_required"], "APPROVED")
        self.assertEqual(len(plan["live_evidence"]["items"]), 2)
        self.assertFalse(plan["live_evidence"]["inventory_adjustment_reference_absent"] is False)

    def test_payload_has_no_location_batch_serial_or_foreign_item(self) -> None:
        text = json.dumps(stock_tool.build_payload()).casefold()
        for forbidden in ("location", "warehouse", "batch", "serial", "96274000000034747"):
            self.assertNotIn(forbidden, text)

    def test_stage_refuses_source_failure_before_live_read(self) -> None:
        self.verify_sources_mock.side_effect = stock_tool.BackingRingToolError("source mismatch")
        with self.assertRaisesRegex(stock_tool.BackingRingToolError, "source mismatch"):
            stock_tool.command_stage(argparse.Namespace())
        self.refresh_mock.assert_not_called()
        self.urlopen_mock.assert_not_called()

    def test_stage_refuses_duplicate_adjustment_reference(self) -> None:
        self.adjustments["123"] = {"reference_number": stock_tool.ADJUSTMENT_REFERENCE}
        with self.assertRaisesRegex(stock_tool.BackingRingToolError, "already exists"):
            stock_tool.command_stage(argparse.Namespace())
        self.urlopen_mock.assert_not_called()

    def test_stage_refuses_item_identity_rate_purchase_rate_and_tracking_mutations(self) -> None:
        mutations = [
            ("sku", "WRONG"),
            ("rate", 98.0),
            ("purchase_rate", 59.0),
            ("track_inventory", False),
            ("is_combo_product", True),
        ]
        for field, value in mutations:
            with self.subTest(field=field):
                self.items[stock_tool.TARGETS[0]["item_id"]][field] = value
                with self.assertRaises(stock_tool.BackingRingToolError):
                    stock_tool.command_stage(argparse.Namespace())
                self.items[stock_tool.TARGETS[0]["item_id"]] = fake_item(stock_tool.TARGETS[0])

    def test_stage_refuses_invoice_item_link_quantity_or_rate_change(self) -> None:
        line = self.invoice["line_items"][0]
        originals = copy.deepcopy(line)
        for field, value in (("item_id", "99"), ("quantity", 25), ("rate", 98)):
            with self.subTest(field=field):
                line[field] = value
                with self.assertRaises(stock_tool.BackingRingToolError):
                    stock_tool.command_stage(argparse.Namespace())
                line.clear()
                line.update(copy.deepcopy(originals))

    def test_plan_hash_and_closed_schema_reject_tampering(self) -> None:
        path = self.stage()
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan["payload"]["inventory_adjustment"]["line_items"][0]["quantity_adjusted"] = 13
        path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(stock_tool.BackingRingToolError, "hash"):
            stock_tool.load_plan(str(path))

    def test_expiry_is_exactly_24_hours(self) -> None:
        plan = stock_tool.load_plan(str(self.stage()))
        created = stock_tool.parse_utc(plan["created_utc"], "created")
        expires = stock_tool.parse_utc(plan["expires_utc"], "expires")
        self.assertEqual(expires - created, timedelta(hours=24))


class TestCommitSafetyAndVerification(StockToolTestCase):
    def test_approval_is_byte_exact(self) -> None:
        plan = self.stage()
        self.load_vault_mock.reset_mock()
        for bad in ("approved", " APPROVED", "APPROVED ", "yes", ""):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(stock_tool.BackingRingToolError, "exactly"):
                    self.commit(plan, bad)
        self.load_vault_mock.assert_not_called()
        self.urlopen_mock.assert_not_called()

    def test_missing_scope_refuses_before_token_network_and_lock(self) -> None:
        plan = self.stage()
        self.refresh_mock.reset_mock()
        self.api_get_mock.reset_mock()
        self.vault["scopes"] = [stock_tool.ITEM_UPDATE_SCOPE]
        with self.assertRaisesRegex(stock_tool.BackingRingToolError, "REFUSED BEFORE LOCK"):
            self.commit(plan)
        self.refresh_mock.assert_not_called()
        self.api_get_mock.assert_not_called()
        self.assertFalse(self.lock_dir.exists())
        self.urlopen_mock.assert_not_called()

    def test_live_stock_or_invoice_change_refuses_before_lock(self) -> None:
        plan = self.stage()
        self.items[stock_tool.TARGETS[0]["item_id"]]["stock_on_hand"] = 1.0
        with self.assertRaisesRegex(stock_tool.BackingRingToolError, "stock changed"):
            self.commit(plan)
        self.assertFalse(self.lock_dir.exists())
        self.urlopen_mock.assert_not_called()

        self.items[stock_tool.TARGETS[0]["item_id"]] = fake_item(stock_tool.TARGETS[0])
        self.invoice["status"] = "paid"
        with self.assertRaisesRegex(stock_tool.BackingRingToolError, "invoice/order"):
            self.commit(plan)
        self.assertFalse(self.lock_dir.exists())

    def test_success_is_exact_three_writes_in_disclosed_order_with_lock_first(self) -> None:
        plan = self.stage()
        self.allow_writes()
        output = self.commit(plan)
        self.assertIn("COMMITTED AND VERIFIED", output)
        self.assertEqual([x["method"] for x in self.write_calls], ["POST", "PUT", "PUT"])
        self.assertTrue(all(x["lock_exists"] for x in self.write_calls))
        self.assertIn("/inventory/v1/inventoryadjustments?", self.write_calls[0]["url"])
        self.assertIn(f"/items/{stock_tool.TARGETS[0]['item_id']}?", self.write_calls[1]["url"])
        self.assertIn(f"/items/{stock_tool.TARGETS[1]['item_id']}?", self.write_calls[2]["url"])
        self.assertEqual(self.write_calls[0]["payload"], stock_tool.build_payload()["inventory_adjustment"])
        self.assertEqual(self.write_calls[1]["payload"], {"name": stock_tool.TARGETS[0]["name"], "rate": 108.0})
        self.assertEqual(self.write_calls[2]["payload"], {"name": stock_tool.TARGETS[1]["name"], "rate": 468.0})
        lock = next(self.lock_dir.glob("*.json"))
        self.assertEqual(json.loads(lock.read_text(encoding="utf-8"))["state"], "verified")

    def test_success_preserves_order_links_and_changes_exact_stock_and_rates(self) -> None:
        original_invoice = copy.deepcopy(self.invoice)
        self.allow_writes()
        self.commit(self.stage())
        self.assertEqual(self.invoice, original_invoice)
        expected = ((12.0, -12.0, 108.0), (101.0, 65.0, 468.0))
        for target, (stock, available, rate) in zip(stock_tool.TARGETS, expected):
            item = self.items[target["item_id"]]
            self.assertEqual(item["stock_on_hand"], stock)
            self.assertEqual(item["actual_available_stock"], stock)
            self.assertEqual(item["available_stock"], available)
            self.assertEqual(item["actual_available_for_sale_stock"], available)
            self.assertEqual(item["rate"], rate)
            self.assertEqual(item["purchase_rate"], float(target["purchase_rate_cad"]))

    def test_adjustment_readback_mismatch_locks_indeterminate_and_stops(self) -> None:
        def mutate(adjustment: dict) -> None:
            adjustment["line_items"][0]["quantity_adjusted"] = 13

        self.allow_writes(mutate_adjustment=mutate)
        with self.assertRaisesRegex(stock_tool.BackingRingToolError, "quantity mismatch"):
            self.commit(self.stage())
        self.assertEqual(len(self.write_calls), 1)
        lock = next(self.lock_dir.glob("*.json"))
        data = json.loads(lock.read_text(encoding="utf-8"))
        self.assertEqual(data["state"], "indeterminate")
        self.assertTrue(data["details"]["no_retry"])

    def test_second_write_failure_locks_and_never_attempts_third(self) -> None:
        self.allow_writes(fail_at=2)
        with self.assertRaises(stock_tool.BackingRingToolError):
            self.commit(self.stage())
        self.assertEqual([x["method"] for x in self.write_calls], ["POST", "PUT"])
        lock = next(self.lock_dir.glob("*.json"))
        self.assertEqual(json.loads(lock.read_text(encoding="utf-8"))["state"], "indeterminate")

    def test_replay_is_refused_after_verified_commit(self) -> None:
        plan = self.stage()
        self.allow_writes()
        self.commit(plan)
        with self.assertRaisesRegex(stock_tool.BackingRingToolError, "already commit-locked"):
            self.commit(plan)
        self.assertEqual(len(self.write_calls), 3)

    def test_transport_refuses_foreign_routes_methods_items_and_payloads(self) -> None:
        cases = [
            ("DELETE", stock_tool.INVENTORY_ADJUSTMENT_PATH, stock_tool.build_payload()["inventory_adjustment"]),
            ("POST", "/inventory/v1/inventoryadjustments/123/approve", {}),
            ("POST", stock_tool.INVENTORY_ADJUSTMENT_PATH, {"date": "2026-08-11"}),
            ("PUT", "/inventory/v1/items/999", {"name": "x", "rate": 1}),
            ("PUT", f"/inventory/v1/items/{stock_tool.TARGETS[0]['item_id']}", {"name": stock_tool.TARGETS[0]["name"], "rate": 109}),
        ]
        for method, path, payload in cases:
            with self.subTest(method=method, path=path):
                with self.assertRaisesRegex(stock_tool.BackingRingToolError, "REFUSED"):
                    stock_tool.perform_write("token", "https://www.zohoapis.ca", ORG_ID, method, path, payload)
        self.urlopen_mock.assert_not_called()


class TestStaticSurface(unittest.TestCase):
    def test_source_artifact_parser_uses_live_intake_schema(self) -> None:
        with tempfile.TemporaryDirectory(dir=HERE) as folder:
            root = Path(folder)
            image = root / "sheet.jpeg"
            master = root / "master.xlsx"
            january = root / "january.pdf"
            intake = root / "intake.json"
            image.write_bytes(b"sheet")
            master.write_bytes(b"master")
            january.write_bytes(b"january")
            intake.write_text(json.dumps({"rows": [
                {"size_in": "4", "colour": "WHITE", "side_note": None, "count": 4},
                {"size_in": "4", "colour": "BLACK", "side_note": None, "count": 8},
                {"size_in": "10", "colour": "BLACK", "side_note": None, "count": 83},
                {"size_in": "10", "colour": "BLACK", "side_note": None, "count": 18},
            ]}), encoding="utf-8")
            with (
                mock.patch.object(stock_tool, "SOURCE_IMAGE", image),
                mock.patch.object(stock_tool, "SOURCE_IMAGE_SHA256", hashlib.sha256(b"sheet").hexdigest()),
                mock.patch.object(stock_tool, "FEI_MASTER", master),
                mock.patch.object(stock_tool, "FEI_MASTER_SHA256", hashlib.sha256(b"master").hexdigest()),
                mock.patch.object(stock_tool, "FEI_JANUARY", january),
                mock.patch.object(stock_tool, "FEI_JANUARY_SHA256", hashlib.sha256(b"january").hexdigest()),
                mock.patch.object(stock_tool, "INTAKE_PATH", intake),
            ):
                evidence = stock_tool.verify_source_files()
            self.assertEqual(evidence[str(intake)]["verified_counts"]["10_inch_black_rows"], [83, 18])

    def test_global_scope_is_narrowly_commissioned(self) -> None:
        self.assertIn(stock_tool.INVENTORY_ADJUSTMENT_CREATE_SCOPE, zoho_tool.ALLOWED_WRITE_SCOPES)
        for forbidden in (
            "ZohoInventory.inventoryadjustments.UPDATE",
            "ZohoInventory.inventoryadjustments.DELETE",
            "ZohoInventory.inventoryadjustments.ALL",
            "ZohoInventory.fullaccess.all",
        ):
            self.assertNotIn(forbidden, zoho_tool.SCOPES)
            with self.assertRaises(zoho_tool.ZohoError):
                zoho_tool.validate_scopes([forbidden])

    def test_source_has_one_write_transport_call_site_and_no_forbidden_modules(self) -> None:
        path = Path(stock_tool.__file__)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        urlopen_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "urlopen"]
        self.assertEqual(len(urlopen_calls), 1)
        lowered = source.casefold()
        for forbidden in (
            "requests.", "selenium", "playwright", "connect_over_cdp", "wp-json",
            "consumer_secret", "mail.send", "sendmail", "method=\"delete\"",
            "method=\"patch\"", "/approve", "/submit", "/reject", "/email",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_cli_exposes_only_stage_and_commit(self) -> None:
        parser = stock_tool.build_parser()
        subparsers = [action for action in parser._actions if action.__class__.__name__ == "_SubParsersAction"]
        self.assertEqual(len(subparsers), 1)
        self.assertEqual(set(subparsers[0].choices), {"stage", "commit"})


if __name__ == "__main__":
    unittest.main()
