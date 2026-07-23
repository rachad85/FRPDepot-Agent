from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import zoho_tool as tool
import zoho_customer_quote_tool as draft
import zoho_inventory_item_tool as item_tool


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ZohoToolTests(unittest.TestCase):
    def test_scopes_are_read_or_exactly_commissioned_writes(self) -> None:
        tool.validate_scopes(tool.SCOPES)
        self.assertEqual(
            set(tool.ALLOWED_WRITE_SCOPES),
            {
                "ZohoBooks.contacts.CREATE",
                "ZohoBooks.estimates.CREATE",
                "ZohoInventory.items.CREATE",
                "ZohoInventory.items.UPDATE",
            },
        )
        self.assertEqual(
            {
                scope for scope in tool.SCOPES
                if scope.startswith("ZohoInventory.") and not scope.endswith(".READ")
            },
            {"ZohoInventory.items.CREATE", "ZohoInventory.items.UPDATE"},
        )

    def test_uncommissioned_scopes_are_refused(self) -> None:
        for scopes in (
            ["ZohoBooks.contacts.UPDATE"],
            ["ZohoBooks.estimates.DELETE"],
            ["ZohoBooks.fullaccess.all"],
            ["ZohoInventory.inventoryadjustments.CREATE"],
            ["ZohoBooks.invoices.CREATE"],
        ):
            with self.assertRaises(tool.ZohoError):
                tool.validate_scopes(scopes)

    def test_dpapi_round_trip(self) -> None:
        plaintext = b"not-real-zoho-credentials"
        encrypted = tool.dpapi_protect(plaintext)
        self.assertNotEqual(encrypted, plaintext)
        self.assertEqual(tool.dpapi_unprotect(encrypted), plaintext)

    def test_read_helper_uses_get_only(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout):
            captured["method"] = request.get_method()
            captured["authorization"] = request.headers.get("Authorization")
            return FakeResponse({"code": 0, "items": []})

        with patch.object(tool, "urlopen", side_effect=fake_urlopen):
            result = tool.api_get("fake-access-token", tool.EXPECTED_API_DOMAIN, "/inventory/v1/items")
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["authorization"], "Zoho-oauthtoken fake-access-token")
        self.assertEqual(result["code"], 0)

    def test_write_helper_allows_only_two_exact_post_endpoints(self) -> None:
        self.assertEqual(
            draft.ALLOWED_POSTS,
            {"customer": "/books/v3/contacts", "quote": "/books/v3/estimates"},
        )
        captured = {}

        def fake_urlopen(request, timeout):
            captured["method"] = request.get_method()
            captured["url"] = request.full_url
            return FakeResponse({"code": 0, "contact": {"contact_id": "123"}})

        with patch.object(draft, "urlopen", side_effect=fake_urlopen):
            draft.api_post_allowed("token", tool.EXPECTED_API_DOMAIN, "customer", "99", {"contact_name": "Test"})
        self.assertEqual(captured["method"], "POST")
        self.assertIn("/books/v3/contacts?organization_id=99", captured["url"])
        with self.assertRaises(draft.DraftToolError):
            draft.api_post_allowed("token", tool.EXPECTED_API_DOMAIN, "email", "99", {})

    def test_quote_staging_requires_sources_and_forces_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            input_path = temp_path / "quote.json"
            input_path.write_text(
                json.dumps(
                    {
                        "customer_id": "1001",
                        "reference_number": "TEST-1",
                        "line_items": [
                            {
                                "item_id": "2002",
                                "quantity": 2,
                                "rate": 125.5,
                                "quantity_source": "Rachad's words",
                                "rate_source": "approved price list",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(draft, "PLAN_DIR", temp_path / "plans"), patch.object(
                draft.zoho_tool, "append_receipt"
            ):
                draft.command_stage_quote(argparse.Namespace(input=str(input_path)))
            plans = list((temp_path / "plans").glob("*.json"))
            self.assertEqual(len(plans), 1)
            plan = json.loads(plans[0].read_text(encoding="utf-8"))
            self.assertEqual(plan["payload"]["status"], "draft")
            self.assertEqual(plan["sources"]["line_items"][0]["rate"], "approved price list")

            bad_path = temp_path / "bad_quote.json"
            bad_path.write_text(
                json.dumps(
                    {
                        "customer_id": "1001",
                        "line_items": [{"item_id": "2002", "quantity": 1, "rate": 10}],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(draft.DraftToolError, "source"):
                draft.command_stage_quote(argparse.Namespace(input=str(bad_path)))

    def test_customer_tool_refuses_vendor_or_portal_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "customer.json"
            path.write_text(
                json.dumps({"contact_name": "Example", "contact_type": "vendor"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(draft.DraftToolError, "customers only"):
                draft.command_stage_customer(argparse.Namespace(input=str(path), source="test"))
            path.write_text(
                json.dumps({"contact_name": "Example", "enable_portal": True}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(draft.DraftToolError, "Unsupported"):
                draft.command_stage_customer(argparse.Namespace(input=str(path), source="test"))

    def test_exactly_one_frp_organization_is_required(self) -> None:
        result = tool.frp_organization(
            [
                {"organization_id": "1", "name": "FRP Depots Inc."},
                {"organization_id": "2", "name": "Unrelated Example"},
            ]
        )
        self.assertEqual(result["organization_id"], "1")
        with self.assertRaisesRegex(tool.ZohoError, "exactly one"):
            tool.frp_organization([])
        with self.assertRaisesRegex(tool.ZohoError, "exactly one"):
            tool.frp_organization(
                [
                    {"organization_id": "1", "name": "FRP Depot"},
                    {"organization_id": "2", "name": "FRP Depot Test"},
                ]
            )


    def test_item_tool_write_endpoints_and_payload_are_narrow(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append((request.get_method(), request.full_url, json.loads(request.data)))
            return FakeResponse({"code": 0, "item": {"item_id": "123", "name": "Panel", "sku": "P-1"}})

        with patch.object(item_tool, "urlopen", side_effect=fake_urlopen):
            item_tool.api_write_allowed(
                "token", tool.EXPECTED_API_DOMAIN, "POST", "/inventory/v1/items", "99",
                {"name": "Panel", "sku": "P-1"},
            )
            item_tool.api_write_allowed(
                "token", tool.EXPECTED_API_DOMAIN, "PUT", "/inventory/v1/items/123", "99",
                {"name": "Panel revised", "sku": "P-2"},
            )
        self.assertEqual(captured[0][0], "POST")
        self.assertEqual(captured[1][0], "PUT")
        with self.assertRaises(item_tool.ItemToolError):
            item_tool.api_write_allowed(
                "token", tool.EXPECTED_API_DOMAIN, "PUT", "/inventory/v1/items/123", "99",
                {"name": "Panel", "initial_stock": 100},
            )
        with self.assertRaises(item_tool.ItemToolError):
            item_tool.api_write_allowed(
                "token", tool.EXPECTED_API_DOMAIN, "DELETE", "/inventory/v1/items/123", "99", {},
            )
        with self.assertRaises(item_tool.ItemToolError):
            item_tool.api_write_allowed(
                "token", tool.EXPECTED_API_DOMAIN, "POST", "/inventory/v1/items/active", "99", {},
            )

    def test_item_create_staging_requires_sources_and_forbids_stock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            input_path = temp_path / "item.json"
            input_path.write_text(
                json.dumps(
                    {
                        "name": "FRP panel",
                        "sku": "FRP-001",
                        "rate": 25,
                        "sources": {
                            "name": "Rachad's words",
                            "sku": "Rachad's words",
                            "rate": "approved price list",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(item_tool, "PLAN_DIR", temp_path / "plans"), patch.object(
                item_tool.zoho_tool, "append_receipt"
            ):
                item_tool.command_stage_create(argparse.Namespace(input=str(input_path)))
            plans = list((temp_path / "plans").glob("*.json"))
            self.assertEqual(len(plans), 1)
            plan = json.loads(plans[0].read_text(encoding="utf-8"))
            self.assertEqual(plan["payload"]["sku"], "FRP-001")
            self.assertNotIn("initial_stock", plan["payload"])

            input_path.write_text(
                json.dumps(
                    {
                        "name": "Bad stock write",
                        "initial_stock": 5,
                        "sources": {"name": "test", "initial_stock": "test"},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(item_tool.ItemToolError, "REFUSED"):
                item_tool.command_stage_create(argparse.Namespace(input=str(input_path)))

            input_path.write_text(
                json.dumps({"name": "Missing source", "sku": "X", "sources": {"name": "test"}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(item_tool.ItemToolError, "sources.sku"):
                item_tool.command_stage_create(argparse.Namespace(input=str(input_path)))

    def test_name_sku_plan_reads_current_item_and_changes_only_name_sku(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            input_path = temp_path / "change.json"
            input_path.write_text(
                json.dumps(
                    {
                        "item_id": "123",
                        "new_name": "Panel - Standard",
                        "new_sku": "PNL-STD",
                        "sources": {"name": "Rachad's words", "sku": "Rachad's words"},
                    }
                ),
                encoding="utf-8",
            )
            fake_vault = {"api_domain": tool.EXPECTED_API_DOMAIN, "inventory_organization_id": "99"}
            with patch.object(item_tool, "PLAN_DIR", temp_path / "plans"), patch.object(
                item_tool.zoho_tool, "append_receipt"
            ), patch.object(item_tool.zoho_tool, "load_vault", return_value=fake_vault), patch.object(
                item_tool.zoho_tool, "refresh_access_token", return_value=("token", fake_vault)
            ), patch.object(item_tool.zoho_tool, "save_vault"), patch.object(
                item_tool, "get_item", return_value={"item_id": "123", "name": "Old", "sku": "OLD"}
            ):
                item_tool.command_stage_name_sku(argparse.Namespace(input=str(input_path)))
            plan_path = next((temp_path / "plans").glob("*.json"))
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(plan["payload"], {"name": "Panel - Standard", "sku": "PNL-STD"})
            self.assertEqual(plan["summary"]["before"]["name"], "Old")
            self.assertNotIn("rate", plan["payload"])
            self.assertNotIn("stock_on_hand", plan["payload"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
