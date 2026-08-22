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
    @staticmethod
    def quote_evidence(payload, summary):
        customer_id = str(payload["customer_id"])
        items = {
            str(line["item_id"]): {
                "item_id": str(line["item_id"]),
                "name": f"Item {line['item_id']}",
                "sku": "TEST-SKU",
                "status": "active",
            }
            for line in payload["line_items"]
        }
        tax_ids = {
            str(line.get("tax_id") or "") for line in payload["line_items"]
            if line.get("tax_id")
        }
        taxes = {
            tax_id: {
                "tax_id": tax_id,
                "tax_name": "Gst & Qst",
                "tax_percentage": 14.975,
                "tax_type": "tax",
                "status": "active",
            }
            for tax_id in tax_ids
        }
        customer = {
            "customer_id": customer_id,
            "customer_name": (
                "Troy Dualam Services Inc."
                if customer_id == draft.TDS_CUSTOMER_ID
                else f"Customer {customer_id}"
            ),
            "status": "active",
            "contact_type": "customer",
            "currency_code": "CAD",
            "currency_id": "9988",
        }
        return {
            "customer": customer,
            "items": items,
            "taxes": taxes,
            "totals": draft.quote_totals(payload, summary, taxes),
        }

    def test_scopes_are_read_or_exactly_commissioned_writes(self) -> None:
        tool.validate_scopes(tool.SCOPES)
        self.assertEqual(
            set(tool.ALLOWED_WRITE_SCOPES),
            {
                "ZohoBooks.contacts.CREATE",
                "ZohoBooks.estimates.CREATE",
                # Commissioned 2026-08-10 for zoho_customer_quote_tool.py only:
                # the two fixed draft estimates whose line discount Zoho read as
                # CAD 10.00 instead of 10%. Discount-only, one PUT each.
                "ZohoBooks.estimates.UPDATE",
                "ZohoBooks.banking.CREATE",
                "ZohoBooks.banking.UPDATE",
                # Commissioned 2026-08-10 for zoho_invoice_revision_tool.py only:
                # UPDATE for one existing-invoice revision, CREATE for one draft
                # invoice. Nothing else in the tree may use either.
                "ZohoBooks.invoices.UPDATE",
                "ZohoBooks.invoices.CREATE",
                # Commissioned 2026-08-11 for zoho_sales_order_tool.py only: the
                # ONE fixed SCT PO26330 draft sales order and the upload of the
                # original client PO to the order that create just returned.
                "ZohoBooks.salesorders.CREATE",
                # Commissioned 2026-08-12 for
                # zoho_historical_client_po_reference_tool.py only: the six fixed
                # historical Sales Orders whose visible Reference# still shows an
                # internal quote number instead of the customer's own PO. One
                # field, reference_number, one record per approved plan.
                "ZohoBooks.salesorders.UPDATE",
                # Commissioned 2026-08-13 for zoho_purchase_order_tool.py only:
                # create ONE new Purchase Order in exactly Draft status so
                # Rachad can review and send it himself. The exact-set assertion
                # is EXTENDED, not relaxed, so any further scope still fails.
                "ZohoBooks.purchaseorders.CREATE",
                # Commissioned 2026-08-21 for zoho_j26_403_revision_tool.py
                # only: the ONE fixed purchase_order_revision on PO-00010, which
                # resends every original line untouched and appends exactly the
                # two fixed non-catalog J26-403 lines. The exact-set assertion is
                # EXTENDED, not relaxed, so any further scope still fails.
                "ZohoBooks.purchaseorders.UPDATE",
                "ZohoInventory.items.CREATE",
                "ZohoInventory.items.UPDATE",
                # Commissioned 2026-08-11 for zoho_backing_ring_stock_tool.py
                # only: one positive adjustment for the two fixed generic rings.
                "ZohoInventory.inventoryadjustments.CREATE",
            },
        )
        # No invoice DELETE/ALL/fullaccess scope may ever be prepared, so an
        # invoice can never be deleted, voided, marked or mailed.
        for forbidden in (
            "ZohoBooks.invoices.DELETE",
            "ZohoBooks.invoices.ALL",
            "ZohoBooks.fullaccess.all",
            # The discount correction gained UPDATE and nothing else: an
            # estimate still cannot be deleted, sent, marked or converted.
            "ZohoBooks.estimates.DELETE",
            "ZohoBooks.estimates.ALL",
            # The SCT PO26330 commission gained CREATE and the 2026-08-12 client-PO
            # reference repair gained UPDATE. Nothing else: a sales order still
            # cannot be deleted, voided, restatused, confirmed, converted or
            # mailed, and no Inventory sales-order write scope exists at all.
            "ZohoBooks.salesorders.DELETE",
            "ZohoBooks.salesorders.ALL",
            "ZohoInventory.salesorders.CREATE",
            "ZohoInventory.salesorders.UPDATE",
            "ZohoInventory.salesorders.DELETE",
            "ZohoInventory.salesorders.ALL",
            # The 2026-08-13 draft-PO commission gained CREATE and the
            # 2026-08-21 J26-403 line-append commission gained UPDATE. Nothing
            # else: a purchase order still cannot be deleted, voided, cancelled,
            # submitted, approved, received, billed, paid or mailed, and no
            # Inventory purchase-order write scope exists at all.
            "ZohoBooks.purchaseorders.DELETE",
            "ZohoBooks.purchaseorders.ALL",
            "ZohoInventory.purchaseorders.CREATE",
            "ZohoInventory.purchaseorders.UPDATE",
            "ZohoInventory.purchaseorders.DELETE",
            "ZohoInventory.purchaseorders.ALL",
            "ZohoInventory.purchasereceives.CREATE",
            "ZohoBooks.bills.CREATE",
            "ZohoBooks.vendorpayments.CREATE",
        ):
            self.assertNotIn(forbidden, tool.SCOPES)
        self.assertEqual(
            {
                scope for scope in tool.SCOPES
                if scope.startswith("ZohoInventory.") and not scope.endswith(".READ")
            },
            {
                "ZohoInventory.items.CREATE",
                "ZohoInventory.items.UPDATE",
                "ZohoInventory.inventoryadjustments.CREATE",
            },
        )
        self.assertTrue(all(scope.endswith(".READ") for scope in tool.READ_SCOPES))
        self.assertTrue(
            {
                "ZohoBooks.accountants.READ",
                "ZohoBooks.banking.READ",
                "ZohoBooks.expenses.READ",
                "ZohoInventory.warehouses.READ",
                "ZohoInventory.inventorycount.READ",
            }.issubset(set(tool.READ_SCOPES))
        )

    def test_status_narration_never_calls_a_commissioned_scope_absent(self) -> None:
        """The connect/reauthorize/check summaries must match the real scopes.

        Those three lines are what Rachad reads to know what Dado can do. They
        claimed "order write scopes: ABSENT" after ZohoBooks.salesorders.CREATE
        was commissioned, which is the same false comfort as a stale status
        line. Nothing here changes a guard: the scope lists above are the
        authority, and this only pins the wording to them.
        """
        source = Path(tool.__file__).read_text(encoding="utf-8")
        lines = source.splitlines()
        self.assertIn("ZohoBooks.salesorders.CREATE", tool.SCOPES)
        absent = [line for line in lines if "ABSENT" in line]
        self.assertEqual(len(absent), 3, "connect, reauthorize and check each state this")
        self.assertIn("ZohoBooks.salesorders.UPDATE", tool.SCOPES)
        for line in absent:
            lowered = line.casefold()
            self.assertNotIn("order write scopes: absent", lowered)
            # Since 2026-08-12 sales-order UPDATE is commissioned too, so only
            # DELETE remains absent. A bare "sales-order ... ABSENT", or one
            # still claiming UPDATE is absent, would be the same false comfort
            # as a stale status line.
            if "sales-order" in lowered:
                self.assertIn("sales-order delete", lowered)
                self.assertNotIn("sales-order update", lowered)
            # Since 2026-08-13 purchase-order CREATE is commissioned and since
            # 2026-08-21 so is purchase-order UPDATE, so only DELETE/ALL stay
            # absent. A line still claiming purchase-order UPDATE is absent, or
            # claiming purchase-order writes are absent outright, would be the
            # same false comfort as a stale status line.
            if "purchase-order" in lowered:
                self.assertIn("purchase-order delete/all", lowered)
                self.assertNotIn("purchase-order update/delete", lowered)
                self.assertNotIn("purchase-order create", lowered)
        self.assertIn("ZohoBooks.purchaseorders.CREATE", tool.SCOPES)
        purchase = [line for line in lines if "Books purchase-order writes:" in line]
        self.assertEqual(len(purchase), 3, "connect, reauthorize and check each state this")
        for line in purchase:
            self.assertIn("DRAFT PURCHASE ORDER", line)
            self.assertIn("NAMED TOOL ONLY", line)
        disclosed = [line for line in lines if "Books sales-order writes:" in line]
        self.assertEqual(len(disclosed), 3)
        for line in disclosed:
            self.assertIn("PO26330", line)
            self.assertIn("NAMED TOOL ONLY", line)
        updates = [line for line in lines if "Books sales-order updates:" in line]
        self.assertEqual(len(updates), 3, "connect, reauthorize and check each state this")
        for line in updates:
            self.assertIn("CLIENT-PO REFERENCE", line)
            self.assertIn("NAMED TOOL ONLY", line)

    def test_scope_copy_uses_only_validated_configured_scopes(self) -> None:
        with patch.object(tool.subprocess, "run") as mocked:
            mocked.return_value.returncode = 0
            tool.command_scope_list(argparse.Namespace(copy=True))
        copied = mocked.call_args.kwargs["input"]
        self.assertEqual(copied, ",".join(tool.SCOPES))
        self.assertNotIn(".DELETE", copied)
        self.assertNotIn("fullaccess", copied.casefold())

    def test_access_probes_are_get_only_read_paths(self) -> None:
        for _, product, template, _ in tool.READ_ACCESS_PROBES:
            self.assertIn(product, {"books", "inventory"})
            self.assertTrue(template.startswith(f"/{product}/"))
            self.assertNotIn("/email", template)
            self.assertNotIn("/send", template)

    def test_uncommissioned_scopes_are_refused(self) -> None:
        for scopes in (
            ["ZohoBooks.contacts.UPDATE"],
            ["ZohoBooks.estimates.DELETE"],
            ["ZohoBooks.fullaccess.all"],
            ["ZohoInventory.inventoryadjustments.UPDATE"],
            ["ZohoInventory.inventoryadjustments.DELETE"],
            ["ZohoBooks.invoices.DELETE"],
            ["ZohoBooks.contacts.UPDATE"],
            ["ZohoBooks.estimates.ALL"],
            ["ZohoBooks.banking.rules.UPDATE"],
            ["ZohoBooks.banking.DELETE"],
            ["ZohoBooks.banking.ALL"],
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
            ), patch.object(
                draft, "stage_quote_live_evidence", side_effect=self.quote_evidence
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

    def test_tds_quote_forces_quebec_tax_and_ten_percent_discount(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            input_path = temp_path / "tds_quote.json"
            input_path.write_text(
                json.dumps(
                    {
                        "customer_id": draft.TDS_CUSTOMER_ID,
                        "line_items": [
                            {
                                "item_id": "2002",
                                "quantity": 2,
                                "rate": 100,
                                "quantity_source": "Rachad's words",
                                "rate_source": "Zoho item rate",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(draft, "PLAN_DIR", temp_path / "plans"), patch.object(
                draft.zoho_tool, "append_receipt"
            ), patch.object(
                draft, "stage_quote_live_evidence", side_effect=self.quote_evidence
            ):
                draft.command_stage_quote(argparse.Namespace(input=str(input_path)))
            plan_path = next((temp_path / "plans").glob("*.json"))
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            payload = plan["payload"]
            self.assertEqual(payload["discount_type"], "item_level")
            self.assertIs(payload["is_discount_before_tax"], True)
            # The percentage is the STRING "10%": Zoho reads the bare number 10
            # as a flat CAD 10.00, which is exactly the 2026-08-10 defect.
            self.assertEqual(payload["line_items"][0]["discount"], "10%")
            self.assertEqual(payload["line_items"][0]["tax_id"], draft.TDS_GST_QST_TAX_ID)
            self.assertIn("Rachad's standing instruction", plan["sources"]["line_items"][0]["discount"])
            self.assertIn("Quebec", plan["sources"]["line_items"][0]["tax"])

            payload["line_items"][0]["discount"] = 0
            with self.assertRaisesRegex(draft.DraftToolError, "automatic 10% discount"):
                draft.validate_quote_customer_policy(payload)
            payload["line_items"][0]["discount"] = 10.0
            with self.assertRaisesRegex(draft.DraftToolError, "automatic 10% discount"):
                draft.validate_quote_customer_policy(payload)

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
