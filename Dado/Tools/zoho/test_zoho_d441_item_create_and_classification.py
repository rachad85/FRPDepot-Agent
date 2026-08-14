"""Diagnosis and regression tests for the 2026-08-13 D441 item-create failure.

WHAT ACTUALLY FAILED, proven here rather than assumed: the four D441 inputs
wrapped every writable field in a top-level "payload" object, so
`zoho_inventory_item_tool.py stage-create` saw one unknown field and refused.
The shape is FLAT -- writable fields at the root beside "sources". The item tool
itself was never broken: it created eight backing-ring items on 2026-08-12.

These tests also pin the two things that were misread alongside it:
* item creation publishes NOTHING to the website, and
* classification is a SEPARATE approved plan, because it needs the real item IDs
  that creation returns. The commissioned non-web value is the exact string
  "Custom / Customer-Specific"; there is no literal "Non Website" value.

NO TEST IN THIS FILE PERFORMS A LIVE CALL. stage-create is entirely offline and
the real transports are asserted never to run.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import zoho_inventory_item_tool as item_tool
import zoho_inventory_classification_tool as classification

D441_DIR = Path(r"C:\FRPDepot") / "Dado" / "20_Working" / "pricing_requests" / "d441"
D441_INPUT_NAMES = (
    "nozzle_2in_zoho_item_create_input.json",
    "nozzle_6in_zoho_item_create_input.json",
    "manway_24in_zoho_item_create_input.json",
    "manway_cover_24in_zoho_item_create_input.json",
)

# The 2-inch D441 nozzle exactly as prepare_d441_working_artifacts.py now emits
# it: writable fields at the ROOT, sources beside them.
FLAT_INPUT = {
    "name": 'FRP NOZZLE-2"/50PSI/D441',
    "sku": "NZDN5050PSI441",
    "unit": "pcs",
    "unit_id": "96274000000014040",
    "item_type": "inventory",
    "product_type": "goods",
    "can_be_sold": True,
    "can_be_purchased": True,
    "track_inventory": True,
    "is_taxable": True,
    "description": 'FRP nozzle, 2", 50 PSI, 150# drilling, Derakane 441, CCMMMM liner, RTP-1.',
    "purchase_description": 'FRP nozzle, 2", 50 PSI, Derakane 441, CCMMMM liner.',
    "purchase_account_id": "96274000000000439",
    "inventory_account_id": "96274000000000442",
    "rate": 79.2,
    "vendor_id": "96274000000027889",
    "sources": {
        "name": "Attached workbook 'D441 Resin Nozzles'; normalized to FRP Depot naming.",
        "sku": "Proposed FRP Depot SKU following the live DN/pressure/resin pattern.",
        "unit": "Live comparable Zoho FRP items use pcs.",
        "unit_id": "Live Zoho pcs unit ID from comparable FRP items.",
        "item_type": "Rachad instructed creation as a stocked item.",
        "product_type": "The workbook describes physical goods.",
        "can_be_sold": "Rachad instructed pricing and a quote to TDI.",
        "can_be_purchased": "Rachad instructed a purchase order to GRP Jrain.",
        "track_inventory": "Comparable FRP goods track inventory.",
        "is_taxable": "Live comparable Zoho FRP items are taxable.",
        "description": "Workbook, with the liner corrected to CCMMMM on Rachad's instruction.",
        "purchase_description": "Workbook, with the liner corrected to CCMMMM.",
        "purchase_account_id": "Live purchase account ID on comparable FRP items.",
        "inventory_account_id": "Live inventory account ID on comparable FRP items.",
        "rate": "Rachad's instruction: supplier USD unit cost x 3.6, expressed in CAD.",
        "vendor_id": "Live active Zoho vendor JRAIN FRP LIMITED, ID 96274000000027889.",
    },
}


def wrapped(flat: dict) -> dict:
    """The shape that actually failed on 2026-08-13."""
    payload = {key: value for key, value in flat.items() if key != "sources"}
    return {"payload": payload, "sources": copy.deepcopy(flat["sources"])}


class StageCreateShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name).resolve()
        self.plan_dir = self.root / "zoho_item_plans"
        self.plan_dir.mkdir(parents=True)
        self.addCleanup(self._temp.cleanup)

    def stage(self, payload: dict) -> Path:
        path = self.root / "input.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        existing = set(self.plan_dir.glob("*.json"))
        with patch.object(item_tool, "PLAN_DIR", self.plan_dir), patch.object(
            item_tool.zoho_tool, "append_receipt"
        ), patch.object(
            item_tool.zoho_tool, "load_vault",
            side_effect=AssertionError("stage-create must not open the vault"),
        ), patch.object(
            item_tool, "urlopen", side_effect=AssertionError("stage-create must never write")
        ):
            item_tool.command_stage_create(argparse.Namespace(input=str(path)))
        made = set(self.plan_dir.glob("*.json")) - existing
        self.assertEqual(len(made), 1)
        return made.pop()

    def stage_expecting_error(self, payload: dict) -> Exception:
        with self.assertRaises(item_tool.ItemToolError) as caught:
            self.stage(payload)
        return caught.exception

    def test_the_wrapped_payload_shape_is_refused_with_a_message_that_says_why(self) -> None:
        error = self.stage_expecting_error(wrapped(FLAT_INPUT))
        text = str(error)
        self.assertIn("top-level \"payload\" object", text)
        self.assertIn("FLAT object", text)
        self.assertIn("beside \"sources\"", text)
        # The message must also list what IS writable, so the fix is obvious.
        self.assertIn("purchase_account_id", text)

    def test_any_other_wrapper_name_is_also_refused(self) -> None:
        for wrapper in ("item", "body", "data", "fields"):
            with self.subTest(wrapper=wrapper):
                payload = {wrapper: {"name": "x"}, "sources": {}}
                self.assertIn(
                    "Unsupported create-item field(s)",
                    str(self.stage_expecting_error(payload)),
                )

    def test_the_flattened_shape_is_accepted_offline(self) -> None:
        plan_path = self.stage(FLAT_INPUT)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["kind"], "item_create")
        self.assertEqual(plan["payload"]["name"], FLAT_INPUT["name"])
        self.assertEqual(plan["payload"]["sku"], "NZDN5050PSI441")
        self.assertEqual(plan["payload"]["rate"], 79.2)
        self.assertEqual(plan["payload"]["vendor_id"], "96274000000027889")
        self.assertNotIn("sources", plan["payload"])
        self.assertEqual(set(plan["sources"]), set(plan["payload"]))
        self.assertFalse(plan["summary"]["stock_or_opening_quantity_included"])

    def test_staging_opens_no_vault_and_makes_no_call(self) -> None:
        # The patches in stage() assert this; this test names it explicitly.
        self.stage(FLAT_INPUT)

    def test_every_field_still_needs_its_own_source(self) -> None:
        payload = copy.deepcopy(FLAT_INPUT)
        payload["sources"].pop("rate")
        self.assertIn("sources.rate", str(self.stage_expecting_error(payload)))

    def test_stock_and_status_fields_are_still_refused(self) -> None:
        for field in ("initial_stock", "opening_stock", "status", "custom_fields", "image"):
            with self.subTest(field=field):
                payload = copy.deepcopy(FLAT_INPUT)
                payload[field] = "x"
                error = self.stage_expecting_error(payload)
                self.assertIn("REFUSED", str(error))
                self.assertIn(field, str(error))

    def test_a_classification_cannot_ride_along_with_the_create(self) -> None:
        payload = copy.deepcopy(FLAT_INPUT)
        payload["custom_fields"] = [
            {"label": "Catalog Classification", "value": "Custom / Customer-Specific"}
        ]
        error = self.stage_expecting_error(payload)
        self.assertIn("custom_fields", str(error))


class GeneratedD441InputTests(unittest.TestCase):
    """The four real inputs prepare_d441_working_artifacts.py emits."""

    def existing(self) -> list[Path]:
        return [D441_DIR / name for name in D441_INPUT_NAMES if (D441_DIR / name).is_file()]

    def test_the_generated_inputs_are_flat_and_carry_sources(self) -> None:
        paths = self.existing()
        if not paths:
            self.skipTest("the D441 working inputs are not present in this checkout")
        self.assertEqual(len(paths), 4)
        for path in paths:
            with self.subTest(path=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("payload", payload)
                self.assertIn("sources", payload)
                writable = set(payload) - {"sources"}
                self.assertTrue(writable)
                self.assertEqual(writable - item_tool.CREATE_FIELDS, set())
                self.assertEqual(set(payload["sources"]), writable)
                self.assertEqual(writable & item_tool.FORBIDDEN_CREATE_FIELDS, set())

    def test_the_generator_emits_root_level_fields(self) -> None:
        script = D441_DIR / "prepare_d441_working_artifacts.py"
        if not script.is_file():
            self.skipTest("prepare_d441_working_artifacts.py is not present in this checkout")
        source = script.read_text(encoding="utf-8")
        self.assertIn('json.dumps({**payload, "sources": sources}', source)
        self.assertNotIn('"payload": payload', source)


class ClassificationValueTests(unittest.TestCase):
    def test_the_three_fixed_values_are_exact(self) -> None:
        self.assertEqual(
            classification.CLASSIFICATIONS,
            ("Website Catalog", "Custom / Customer-Specific", "Review / Unclassified"),
        )

    def test_there_is_no_literal_non_website_value(self) -> None:
        self.assertNotIn("Non Website", classification.CLASSIFICATIONS)
        for source_file in (
            Path(classification.__file__), Path(item_tool.__file__),
        ):
            with self.subTest(file=source_file.name):
                self.assertNotIn("Non Website", source_file.read_text(encoding="utf-8"))

    def test_custom_customer_specific_is_the_commissioned_non_web_value(self) -> None:
        # Recorded for the D441 customer-specific items: the non-web option is
        # this exact string, not an invented "Non Website".
        non_web = "Custom / Customer-Specific"
        self.assertIn(non_web, classification.CLASSIFICATIONS)
        self.assertEqual(classification.CLASSIFICATIONS.index(non_web), 1)
        self.assertNotEqual(non_web, "Website Catalog")

    def test_no_fourth_value_can_be_assigned(self) -> None:
        for invented in ("Non Website", "non-website", "Custom", "Website", ""):
            with self.subTest(value=invented):
                self.assertNotIn(invented, classification.CLASSIFICATIONS)

    def test_the_dropdown_definition_matches_the_three_values(self) -> None:
        source = Path(classification.__file__).read_text(encoding="utf-8")
        for value in classification.CLASSIFICATIONS:
            with self.subTest(value=value):
                self.assertIn(f'"name": "{value}"', source)


class NoWebsitePublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.item_source = Path(item_tool.__file__).read_text(encoding="utf-8")
        self.item_tree = ast.parse(self.item_source)
        # The module docstring now SAYS the words "WooCommerce" and "website" in
        # order to state that neither is reachable, so the executable-surface
        # scan is run against the code with that docstring removed.
        body = list(self.item_tree.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        self.item_code = "\n".join(ast.unparse(node) for node in body)

    def test_item_creation_has_no_website_route_of_any_kind(self) -> None:
        for forbidden in (
            "woocommerce", "WooCommerce", "wp-json", "wp-admin", "wordpress",
            "WordPress", "frpdepots.com", "storefront", "connect_over_cdp",
            "playwright", "/products",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.item_code)

    def test_every_item_tool_request_targets_zoho_inventory_only(self) -> None:
        requests = [
            node for node in ast.walk(self.item_tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "Request"
        ]
        # ONE transport, gated by api_write_allowed, which itself accepts only
        # POST to the fixed item-create path and PUT to the two fixed
        # Zoho Inventory routes.
        self.assertEqual(len(requests), 1)
        self.assertEqual(item_tool.CREATE_PATH, "/inventory/v1/items")
        self.assertEqual(item_tool.UPDATE_PATH_RE.pattern, r"^/inventory/v1/items/[0-9]+$")
        self.assertEqual(
            item_tool.GROUP_UPDATE_PATH_RE.pattern, r"^/inventory/v1/itemgroups/[0-9]+$"
        )
        for method in ("GET", "DELETE", "PATCH"):
            with self.subTest(method=method):
                with self.assertRaises(item_tool.ItemToolError):
                    item_tool.api_write_allowed(
                        "token", "https://www.zohoapis.ca", method,
                        item_tool.CREATE_PATH, "99", {"name": "x"},
                    )

    def test_item_creation_cannot_carry_a_custom_field(self) -> None:
        self.assertIn("custom_fields", item_tool.FORBIDDEN_CREATE_FIELDS)
        self.assertNotIn("custom_fields", item_tool.CREATE_FIELDS)

    def test_classification_is_a_separate_post_create_plan(self) -> None:
        # It targets an item by ID, which only exists after creation.
        self.assertTrue(
            classification.ITEM_PATH_RE.fullmatch("/inventory/v1/items/96274000000019605")
        )
        self.assertIsNone(classification.ITEM_PATH_RE.fullmatch("/inventory/v1/items"))
        self.assertIsNone(classification.ITEM_PATH_RE.fullmatch("/inventory/v1/items/0"))

    def test_the_item_tool_docstring_states_both_facts(self) -> None:
        doc = item_tool.__doc__ or ""
        self.assertIn("NEVER publishes to the website", doc)
        self.assertIn("NEVER sets a Catalog Classification", doc)
        self.assertIn("FLAT", doc)


if __name__ == "__main__":
    unittest.main()
