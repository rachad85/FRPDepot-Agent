from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import zoho_tool as tool
import zoho_customer_quote_tool as draft


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
            {"ZohoBooks.contacts.CREATE", "ZohoBooks.estimates.CREATE"},
        )
        self.assertFalse(
            [
                scope for scope in tool.SCOPES
                if scope.startswith("ZohoInventory.") and not scope.endswith(".READ")
            ]
        )

    def test_uncommissioned_scopes_are_refused(self) -> None:
        for scopes in (
            ["ZohoBooks.contacts.UPDATE"],
            ["ZohoBooks.estimates.DELETE"],
            ["ZohoBooks.fullaccess.all"],
            ["ZohoInventory.items.CREATE"],
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
