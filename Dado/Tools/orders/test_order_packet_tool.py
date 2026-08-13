from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import order_packet_tool as tool

ZOHO_DIR = Path(__file__).resolve().parents[1] / "zoho"
sys.path.insert(0, str(ZOHO_DIR))
import zoho_customer_quote_tool as quote_tool
import zoho_invoice_revision_tool as invoice_tool


class OrderPacketToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.po = self.root / "Customer PO 0000031.pdf"
        self.po.write_bytes(b"test customer purchase order 0000031")
        self.packet = self.base_packet()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def sourced(self, value: str, source: str = "Direct customer thread and verified live source") -> dict:
        return {"value": value, "source": source}

    def base_packet(self) -> dict:
        digest = hashlib.sha256(self.po.read_bytes()).hexdigest()
        return {
            "schema_version": 1,
            "packet_id": "customer-po-0000031",
            "customer": {
                "name": "Example Customer Ltd",
                "state": "existing",
                "zoho_customer_id": "96274000001569002",
                "source": "Fresh live Zoho customer read",
            },
            "source_review": {
                "full_thread_read": True,
                "outlook_conversation_id": "AAQk-example",
                "latest_external_message_id": "AAMk-latest-external",
                "latest_external_received_utc": "2026-08-12T18:00:00+00:00",
                "thread_source": "Complete live FRP Depot Outlook thread",
                "internal_document_numbers_seen": ["QT-000099", "SO-00050"],
                "attachments": [{
                    "path": str(self.po),
                    "role": "client_po",
                    "inspected": True,
                    "sha256": digest,
                    "bytes": self.po.stat().st_size,
                    "source": "Original attachment on latest customer message",
                }],
            },
            "client_po": {
                "state": "issued",
                "value": "0000031",
                "source_value_exact": "0000031",
                "evidence_kind": "client_po_attachment",
                "evidence_locator": str(self.po),
                "no_po_exception_authorized_by": "",
                "no_po_exception_source": "",
            },
            "requested_actions": [
                {
                    "kind": "quote",
                    "operation": "create_draft",
                    "required": True,
                    "reference_number": "0000031",
                    "target_record_id": "",
                    "target_record_number": "",
                    "recipients": [],
                    "attachments": [],
                    "source": "Customer asks for formal quote",
                },
                {
                    "kind": "invoice",
                    "operation": "create_draft",
                    "required": True,
                    "reference_number": "0000031",
                    "target_record_id": "",
                    "target_record_number": "",
                    "recipients": [],
                    "attachments": [],
                    "source": "Customer asks for invoice after approval",
                },
                {
                    "kind": "email_draft",
                    "operation": "reply_all",
                    "required": True,
                    "reference_number": "",
                    "target_record_id": "",
                    "target_record_number": "",
                    "recipients": [{
                        "email": "buyer@example.com",
                        "source": "Outlook Reply All recipient from latest external message",
                        "verification": "outlook_thread",
                    }],
                    "attachments": ["rendered_quote_pdf"],
                    "source": "Customer asked for the document by email",
                },
            ],
            "commercial_terms": {
                "document_date": self.sourced("2026-08-12"),
                "quote_expiry_date": self.sourced("2026-09-11"),
                "invoice_due_date": self.sourced("2026-09-11"),
                "currency": self.sourced("CAD"),
                "billing_address": self.sourced("1 Customer Road, Ottawa, ON"),
                "billing_address_id": self.sourced("96274000000060020"),
                "shipping_address": self.sourced("1 Customer Road, Ottawa, ON"),
                "shipping_address_id": self.sourced("96274000000060021"),
                "shipping_instructions": self.sourced("Purolator collect"),
                "payment_terms": self.sourced("Net 30"),
                "required_date": self.sourced("2026-09-15"),
                "document_notes": self.sourced("Customer PO 0000031"),
            },
            "lines": [{
                "item_id": "96274000000523055",
                "item_name": "FNPT Coupling 3/4 x 6",
                "sku": "FNPTCOUPLING-DERAKANE470-3/4X6",
                "item_source": "Fresh live Zoho active item read",
                "description": self.sourced("3/4-inch coupling, 6 inches long"),
                "unit": "pcs",
                "quantity": self.sourced("2", "Original customer PO line 1 quantity"),
                "rate": self.sourced("50.20", "Customer PO and Rachad accepted offer"),
                "discount": {"kind": "percentage", "value": "10%", "source": "Rachad customer discount rule"},
                "tax": {"state": "taxable", "tax_id": "96274000000035516", "percentage": "13", "source": "Ontario HST live tax record and delivery treatment"},
                "availability": {
                    "state": "sufficient_physical_stock",
                    "physical_available_for_sale": "5",
                    "checked_utc": "2026-08-12T18:05:00+00:00",
                    "source": "Zoho item Overview Physical Available for Sale / actual_available_stock",
                },
            }],
        }

    def validate(self, packet: dict | None = None) -> dict:
        return tool.validate_packet(packet or self.packet, self.root)

    def assert_refused(self, fragment: str, packet: dict | None = None) -> None:
        with self.assertRaisesRegex(tool.OrderPacketError, fragment):
            self.validate(packet)

    def test_valid_packet_preserves_exact_po(self) -> None:
        result = self.validate()
        self.assertEqual(result["client_po"]["value"], "0000031")
        self.assertTrue(result["source_review"]["full_thread_read"])

    def test_full_thread_must_be_read(self) -> None:
        self.packet["source_review"]["full_thread_read"] = False
        self.assert_refused("full_thread_read")

    def test_internal_document_number_is_not_client_po(self) -> None:
        self.packet["client_po"]["value"] = "SO-00050"
        self.packet["client_po"]["source_value_exact"] = "SO-00050"
        for action in self.packet["requested_actions"][:2]:
            action["reference_number"] = "SO-00050"
        self.assert_refused("internal FRP Depot")

    def test_internal_number_seen_in_thread_is_not_client_po(self) -> None:
        self.packet["client_po"]["value"] = "QT-000099"
        self.packet["client_po"]["source_value_exact"] = "QT-000099"
        self.assert_refused("internal FRP Depot")

    def test_po_normalization_is_refused(self) -> None:
        self.packet["client_po"]["value"] = "31"
        self.assert_refused("byte-equal")

    def test_ambiguous_po_is_fail_closed(self) -> None:
        self.packet["client_po"]["state"] = "ambiguous"
        self.assert_refused("ambiguous is fail-closed")

    def test_issued_po_requires_direct_evidence(self) -> None:
        self.packet["client_po"]["evidence_kind"] = "none"
        self.assert_refused("direct customer evidence")

    def test_attachment_must_be_inspected(self) -> None:
        self.packet["source_review"]["attachments"][0]["inspected"] = False
        self.assert_refused("inspected must be true")

    def test_attachment_hash_drift_is_refused(self) -> None:
        self.po.write_bytes(b"changed")
        self.assert_refused("no longer matches")

    def test_po_attachment_needs_client_po_role(self) -> None:
        self.packet["source_review"]["attachments"][0]["role"] = "other"
        self.assert_refused("role client_po")

    def test_valid_no_po_exception(self) -> None:
        p = self.packet
        p["client_po"] = {
            "state": "none", "value": "", "source_value_exact": "", "evidence_kind": "none",
            "evidence_locator": "", "no_po_exception_authorized_by": "Rachad Homsi",
            "no_po_exception_source": "Rachad direct message: no PO was issued for this order",
        }
        for action in p["requested_actions"][:2]:
            action["reference_number"] = ""
        result = self.validate(p)
        self.assertEqual(result["client_po"]["state"], "none")

    def test_no_po_exception_requires_rachad(self) -> None:
        p = self.packet
        p["client_po"] = {
            "state": "none", "value": "", "source_value_exact": "", "evidence_kind": "none",
            "evidence_locator": "", "no_po_exception_authorized_by": "Customer",
            "no_po_exception_source": "Customer said no PO",
        }
        self.assert_refused("requires Rachad Homsi", p)

    def test_document_reference_must_equal_po(self) -> None:
        self.packet["requested_actions"][1]["reference_number"] = "31"
        self.assert_refused("byte-equal the customer PO")

    def test_send_action_is_unreachable(self) -> None:
        self.packet["requested_actions"][2]["operation"] = "send"
        self.assert_refused("unapproved action")

    def test_email_needs_verified_recipient(self) -> None:
        self.packet["requested_actions"][2]["recipients"] = []
        self.assert_refused("requires verified recipients")

    def test_email_body_only_recipient_is_refused(self) -> None:
        self.packet["requested_actions"][2]["recipients"][0]["verification"] = "email_body"
        self.assert_refused("body-only addresses")

    def test_non_email_action_cannot_carry_recipients(self) -> None:
        self.packet["requested_actions"][0]["recipients"] = copy.deepcopy(
            self.packet["requested_actions"][2]["recipients"]
        )
        self.assert_refused("non-email action")

    def test_percentage_discount_requires_percent_string(self) -> None:
        self.packet["lines"][0]["discount"]["value"] = "10"
        self.assert_refused("ending in %")

    def test_zero_none_discount_is_allowed(self) -> None:
        self.packet["lines"][0]["discount"] = {"kind": "none", "value": "0.00", "source": "No discount on customer PO"}
        self.validate()

    def test_sufficient_stock_must_cover_quantity(self) -> None:
        self.packet["lines"][0]["availability"]["physical_available_for_sale"] = "1"
        self.assert_refused("less than ordered quantity")

    def test_accounting_availability_source_is_refused(self) -> None:
        self.packet["lines"][0]["availability"]["source"] = "Zoho Inventory Summary accounting availability"
        self.assert_refused("Physical Available for Sale")

    def test_backorder_acceptance_can_cover_zero_physical(self) -> None:
        availability = self.packet["lines"][0]["availability"]
        availability["state"] = "backorder_accepted"
        availability["physical_available_for_sale"] = "0"
        availability["source"] = "Zoho actual_available_stock 0; customer accepted backorder in live thread"
        self.validate()

    def test_taxable_line_needs_tax_id(self) -> None:
        self.packet["lines"][0]["tax"]["tax_id"] = ""
        self.assert_refused("must not be blank")

    def test_invoice_requires_due_date(self) -> None:
        self.packet["commercial_terms"]["invoice_due_date"]["value"] = ""
        self.assert_refused("requires invoice_due_date")

    def test_invoice_due_date_cannot_precede_document_date(self) -> None:
        self.packet["commercial_terms"]["invoice_due_date"]["value"] = "2026-08-11"
        self.assert_refused("before document_date")

    def test_duplicate_action_is_refused(self) -> None:
        self.packet["requested_actions"].append(copy.deepcopy(self.packet["requested_actions"][0]))
        self.assert_refused("duplicates an earlier")

    def test_customer_creation_is_a_blocking_prerequisite(self) -> None:
        self.packet["customer"]["state"] = "create_required"
        self.packet["customer"]["zoho_customer_id"] = ""
        packet = self.validate()
        routes, blockers = tool.route_actions(packet)
        self.assertTrue(blockers)
        self.assertEqual(routes[0]["state"], "PREREQUISITE")

    def test_sales_order_is_routed_to_separate_commission(self) -> None:
        self.packet["requested_actions"].insert(1, {
            "kind": "sales_order", "operation": "create_draft", "required": True,
            "reference_number": "0000031", "target_record_id": "", "target_record_number": "",
            "recipients": [], "attachments": [str(self.po)], "source": "Customer placed the order",
        })
        packet = self.validate()
        routes, blockers = tool.route_actions(packet)
        route = next(r for r in routes if r["kind"] == "sales_order")
        self.assertEqual(route["state"], "BLOCKED_SEPARATE_COMMISSION_REQUIRED")
        self.assertTrue(blockers)

    def test_quote_input_carries_exact_reference_and_sources(self) -> None:
        packet = self.validate()
        value = tool.build_quote_input(packet)
        self.assertEqual(value["reference_number"], "0000031")
        self.assertEqual(value["line_items"][0]["discount"], "10%")
        self.assertIn("quantity_source", value["line_items"][0])

    def test_invoice_input_carries_exact_reference_and_per_value_sources(self) -> None:
        packet = self.validate()
        value = tool.build_invoice_input(packet)
        self.assertEqual(value["fields"]["reference_number"]["value"], "0000031")
        self.assertEqual(value["lines"][0]["tax_id"]["tax_percentage"], "13")
        self.assertIn("source", value["lines"][0]["rate"])

    def test_generated_invoice_input_passes_actual_downstream_validator(self) -> None:
        value = tool.build_invoice_input(self.validate())
        accepted = invoice_tool.validate_create_input(value)
        self.assertEqual(accepted["fields"]["reference_number"]["value"], "0000031")
        self.assertEqual(accepted["lines"][0]["discount"]["value"], "10%")

    def test_generated_quote_input_passes_actual_downstream_stage_validator(self) -> None:
        value = tool.build_quote_input(self.validate())
        input_path = self.root / "quote_input.json"
        fake_plan = self.root / "fake_quote_plan.json"
        input_path.write_text(json.dumps(value), encoding="utf-8")

        def fake_stage(kind: str, payload: dict, sources: dict, summary: dict) -> Path:
            self.assertEqual(kind, "quote")
            self.assertEqual(payload["reference_number"], "0000031")
            self.assertEqual(sources["line_items"][0]["rate"], value["line_items"][0]["rate_source"])
            self.assertEqual(summary["reference_number"], "0000031")
            fake_plan.write_text(json.dumps({"sha256": "a" * 64}), encoding="utf-8")
            return fake_plan

        with mock.patch.object(quote_tool, "stage_plan", side_effect=fake_stage):
            with contextlib.redirect_stdout(io.StringIO()):
                quote_tool.command_stage_quote(Namespace(input=str(input_path)))

    def test_validate_command_generates_inputs_and_zero_write_report(self) -> None:
        input_path = self.root / "packet.json"
        output_dir = self.root / "out"
        input_path.write_text(json.dumps(self.packet), encoding="utf-8")
        args = type("Args", (), {"input": str(input_path), "output_dir": str(output_dir)})()
        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = tool.validate_command(args)
        self.assertEqual(exit_code, 0)
        report = json.loads((output_dir / "customer-po-0000031.validated.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "READY_FOR_STAGING")
        self.assertEqual(report["business_writes"], 0)
        self.assertEqual(report["emails_sent"], 0)
        self.assertEqual(len(report["generated_stage_inputs"]), 2)

    def test_module_has_no_network_subprocess_or_business_tool_import(self) -> None:
        tree = ast.parse(Path(tool.__file__).read_text(encoding="utf-8"))
        imports = set()
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
        forbidden_imports = {"socket", "subprocess", "urllib", "requests", "httpx", "zoho_tool"}
        self.assertFalse(imports & forbidden_imports)
        self.assertFalse(calls & {"urlopen", "api_get", "api_post", "api_put", "send", "replyAll"})

    def test_closed_schema_rejects_unreviewed_field(self) -> None:
        self.packet["auto_send"] = True
        self.assert_refused("non-closed schema")

    def test_output_hash_is_stable_for_same_validated_packet(self) -> None:
        first = tool.canonical_hash(self.validate())
        second = tool.canonical_hash(self.validate())
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
