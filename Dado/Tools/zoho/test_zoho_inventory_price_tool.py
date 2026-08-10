"""Independent safety tests for the FNPT sales-rate tool.

These tests never touch live Zoho or WooCommerce: every transport is patched,
and the OAuth write transport is patched to fail loudly unless a test opts in
to a simulated write.
"""
from __future__ import annotations

import argparse
import copy
import csv
from decimal import Decimal, ROUND_HALF_UP
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

import zoho_inventory_price_tool as price_tool

HERE = Path(__file__).resolve().parent
REPO = Path(r"C:\FRPDepot")
FNPT_DIR = REPO / "Dado" / "20_Working" / "pricing_requests" / "fnpt"
COMPARISON_CSV = FNPT_DIR / "fnpt_supplier_vs_live_prices.csv"
STAGE_INPUT = FNPT_DIR / "zoho_fnpt_sales_rate_stage_input.json"
WORKBOOK = Path(
    r"C:\Users\TDI-service\AppData\Local\hermes\profiles\dado\cache\documents"
    r"\doc_6af5a5491980_FNPT Quotation Sheet.xlsx"
)

ORGANIZATION = {
    "organization_id": "110002157575",
    "name": "FRP DEPOTS",
    "currency_code": "CAD",
}
SKU_411 = 'FNPTCOUPLING-DERAKANE411-1/2"6"'
SKU_470 = 'FNPTCOUPLING-DERAKANE470-1/2"6"'
WORKBOOK_TEXT = str(WORKBOOK)


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def fake_item(
    item_id: str = "96274000000523063",
    sku: str = SKU_411,
    rate: float = 50.2,
    **overrides: object,
) -> dict:
    item = {
        "item_id": item_id,
        "name": 'FNPT Coupling, Threaded On both ends-Derakane 411-350/1/2"/6"',
        "sku": sku,
        "status": "active",
        "can_be_sold": True,
        "can_be_purchased": True,
        "is_combo_product": False,
        "item_type": "inventory",
        "product_type": "goods",
        "unit": "pcs",
        "rate": rate,
        "sales_rate": rate,
        "pricebook_rate": rate,
        "default_price_brackets": [
            {"start_quantity": 1.0, "end_quantity": 1.0, "pricebook_rate": rate}
        ],
        "sales_margin": "",
        "purchase_rate": 25.0,
        "stock_on_hand": 1.0,
        "available_stock": 1.0,
        "account_id": "96274000000000346",
        "purchase_account_id": "96274000000000439",
        "tax_id": "",
        "group_id": "96274000000523159",
        "custom_fields": [
            {"customfield_id": "96274000001547006", "value": "Website Catalog"}
        ],
        "custom_field_hash": {"cf_catalog_classification": "Website Catalog"},
        "last_modified_time": "2026-08-07T20:59:22-0400",
    }
    item.update(overrides)
    return item


def line_for(
    item_id: str = "96274000000523063",
    sku: str = SKU_411,
    cost: str = "11.2",
    cell: str = "E5",
    **overrides: object,
) -> dict:
    line = {
        "item_id": item_id,
        "sku": sku,
        "supplier_cost_usd": cost,
        "multiplier": "3.6",
        "target_rate_cad": price_tool.money_text(
            (Decimal(cost) * Decimal("3.6")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ),
        "source_workbook": WORKBOOK_TEXT,
        "source_sheet": "Sheet1",
        "source_cell": cell,
    }
    line.update(overrides)
    return line


class PriceToolTestCase(unittest.TestCase):
    """Shared harness. No live Zoho or WooCommerce call can escape it."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.root = Path(self.temp.name).resolve()
        self.plan_dir = self.root / "zoho_price_plans"
        self.plan_dir.mkdir()
        self.counter = 0
        self.items = {
            "96274000000523063": fake_item(),
            "96274000000523031": fake_item(
                item_id="96274000000523031", sku=SKU_470, rate=50.2
            ),
        }
        self.get_paths: list[str] = []
        self.write_calls: list[dict] = []
        self.vault = {
            "api_domain": "https://www.zohoapis.ca",
            "inventory_organization_id": ORGANIZATION["organization_id"],
            "scopes": [price_tool.UPDATE_SCOPE],
        }
        self.patchers = [
            mock.patch.object(price_tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(price_tool.zoho_tool, "load_vault", side_effect=self.load_vault),
            mock.patch.object(
                price_tool.zoho_tool,
                "refresh_access_token",
                side_effect=lambda vault=None: ("token", self.vault),
            ),
            mock.patch.object(price_tool.zoho_tool, "save_vault"),
            mock.patch.object(price_tool.zoho_tool, "append_receipt"),
            mock.patch.object(price_tool.zoho_tool, "api_get", side_effect=self.api_get),
            mock.patch.object(
                price_tool,
                "urlopen",
                side_effect=AssertionError("live Zoho/Woo write is forbidden in tests"),
            ),
        ]
        started = [patcher.start() for patcher in self.patchers]
        self.load_vault_mock = started[1]
        self.append_receipt = started[4]
        self.api_get_mock = started[5]
        self.urlopen = started[6]

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def reset_harness(self) -> None:
        """Restart the harness mid-test WITHOUT orphaning the live patchers."""
        self.tearDown()
        self.setUp()

    # -- fake transports -------------------------------------------------
    def load_vault(self) -> dict:
        return self.vault

    def api_get(self, access_token: str, api_domain: str, path: str) -> dict:
        self.get_paths.append(path)
        if path == "/inventory/v1/organizations":
            return {"organizations": [dict(ORGANIZATION)]}
        if path.startswith("/inventory/v1/items/"):
            item_id = path.split("/inventory/v1/items/", 1)[1].split("?", 1)[0]
            if item_id not in self.items:
                raise price_tool.zoho_tool.ZohoError(f"item {item_id} missing")
            return {"item": copy.deepcopy(self.items[item_id])}
        raise AssertionError(f"unexpected GET {path}")

    def allow_writes(self, on_write=None):
        """Opt in to a SIMULATED write transport; still no network."""

        def transport(request, timeout=60):
            body = json.loads(request.data.decode("utf-8"))
            record = {
                "url": request.full_url,
                "method": request.get_method(),
                "payload": body,
                "lock_existed": self.lock_files(),
            }
            self.write_calls.append(record)
            if on_write is not None:
                on_write(record)
            else:
                item_id = request.full_url.split("/items/", 1)[1].split("?", 1)[0]
                self.items[item_id]["rate"] = body["rate"]
                self.items[item_id]["sales_rate"] = body["rate"]
                self.items[item_id]["pricebook_rate"] = body["rate"]
                self.items[item_id]["default_price_brackets"] = [
                    {"start_quantity": 1.0, "end_quantity": 1.0, "pricebook_rate": body["rate"]}
                ]
                self.items[item_id]["last_modified_time"] = "2026-08-10T09:00:00-0400"
            return FakeResponse({"code": 0, "message": "ok"})

        self.urlopen.side_effect = transport

    def lock_files(self) -> list[str]:
        folder = self.plan_dir / ".commit-locks"
        return sorted(path.name for path in folder.glob("*.json")) if folder.exists() else []

    # -- helpers ---------------------------------------------------------
    def input_path(self, value: object) -> Path:
        self.counter += 1
        path = self.root / f"input_{self.counter}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def stage(self, lines: list[dict] | None = None, currency: str = "CAD") -> Path:
        payload = {"target_currency": currency, "lines": lines or [line_for()]}
        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            price_tool.command_stage(argparse.Namespace(input=str(self.input_path(payload))))
        self.stage_output = buffer.getvalue()
        plans = sorted(self.plan_dir.glob("*.json"))
        self.assertTrue(plans, "stage did not write a plan")
        return plans[-1]

    def commit(self, plan: Path, approval: str = "APPROVED") -> str:
        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            price_tool.command_commit(
                argparse.Namespace(plan=str(plan), approval=approval)
            )
        return buffer.getvalue()

    def rewrite_plan(self, plan: Path, mutate) -> Path:
        data = json.loads(plan.read_text(encoding="utf-8"))
        data.pop("sha256")
        mutate(data)
        data["sha256"] = price_tool.digest_for(data)
        plan.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return plan

    def assertNoWrites(self) -> None:
        self.assertEqual(self.write_calls, [], "a write was attempted")


class BusinessRuleTests(PriceToolTestCase):
    def test_multiplier_is_fixed_at_exactly_3_6(self) -> None:
        self.assertEqual(price_tool.MULTIPLIER_TEXT, "3.6")
        self.assertEqual(price_tool.MULTIPLIER, Decimal("3.6"))
        for bad in ["3.60", "3.5", "3.7", 3.6, "3", None, "3.6 "]:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.validate_line(line_for(multiplier=bad), 0)

    def test_decimal_half_up_rounding(self) -> None:
        cases = {
            "11.2": "40.32",
            "12.4": "44.64",
            "0.125": "0.45",     # 0.45 exactly
            "1.235": "4.45",     # 4.446 -> 4.45
            "1.0125": "3.65",    # 3.645 half-up -> 3.65, banker's would give 3.64
            "2.8125": "10.13",   # 10.125 half-up -> 10.13
        }
        for cost, expected in cases.items():
            self.assertEqual(
                price_tool.money_text(price_tool.compute_target(Decimal(cost))), expected
            )

    def test_target_must_equal_the_calculation(self) -> None:
        for bad in ["40.33", "40.31", "40.3", "40.320", "44.64"]:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.validate_line(line_for(target_rate_cad=bad), 0)
        self.assertEqual(
            price_tool.validate_line(line_for(), 0)["target_rate_cad"], "40.32"
        )

    def test_no_float_arithmetic_in_the_rule(self) -> None:
        # 0.1 + 0.2 style drift must be impossible: money is decimal text only.
        for bad in [11.2, 0, -1, True]:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.validate_line(line_for(supplier_cost_usd=bad), 0)

    def test_blank_zero_negative_and_malformed_prices_refused(self) -> None:
        for bad in ["", " ", "0", "0.00", "-5", "-5.00", "abc", "1e3", "1,5", "1.23456789",
                    "٣", "11.2 ", None, [], {}]:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.validate_line(line_for(supplier_cost_usd=bad), 0)
        for bad in ["", "0.00", "-40.32", "40", "abc", None, 40.32]:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.validate_line(line_for(target_rate_cad=bad), 0)


class InputSchemaTests(PriceToolTestCase):
    def test_only_the_two_exact_sku_prefixes_are_accepted(self) -> None:
        good = [
            'FNPTCOUPLING-DERAKANE411-1/2"6"',
            'FNPTCOUPLING-DERAKANE470-6"8"',
        ]
        for sku in good:
            self.assertEqual(price_tool.allowed_sku(sku), sku)
        bad = [
            'FNPTCOUPLING-DERAKANE510-1/2"6"',
            'fnptcoupling-derakane411-1/2"6"',
            ' FNPTCOUPLING-DERAKANE411-1/2"6"',
            'FNPTCOUPLING-DERAKANE41-1/2"6"',
            'XFNPTCOUPLING-DERAKANE411-1/2"6"',
            'PIDN750150PSI411',
            'MANWAY-COVER-24',
            'FNPTCOUPLING-DERAKANE411',
        ]
        for sku in bad:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.allowed_sku(sku)

    def test_unknown_and_missing_input_fields_refused(self) -> None:
        for bad in [
            {"lines": [line_for()]},
            {"target_currency": "CAD"},
            {"target_currency": "CAD", "lines": [line_for()], "purchase_rate": "1"},
            {"target_currency": "CAD", "lines": [line_for()], "force": True},
        ]:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.validate_input(bad)

    def test_unknown_and_missing_line_fields_refused(self) -> None:
        extras = ["purchase_rate", "cost", "stock_on_hand", "name", "new_sku",
                  "status", "description", "tax_id", "account_id", "warehouse_id"]
        for field in extras:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.validate_line({**line_for(), field: "1"}, 0)
        for field in sorted(price_tool.LINE_FIELDS):
            partial = line_for()
            partial.pop(field)
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.validate_line(partial, 0)

    def test_non_cad_target_currency_refused(self) -> None:
        for bad in ["USD", "cad", "CAD ", "", None, "EUR"]:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.validate_input({"target_currency": bad, "lines": [line_for()]})

    def test_source_workbook_sheet_and_cell_required_and_checked(self) -> None:
        for field, bad_values in {
            "source_workbook": ["", "   ", "notes.txt", "Sheet1", None, 5],
            "source_sheet": ["", "Sheet 1!", "a" * 65, None, 5],
            "source_cell": ["", "5E", "E", "e5", "E0", "AAAA1", "E5:E6", None, 5],
        }.items():
            for bad in bad_values:
                with self.assertRaises(price_tool.PriceToolError):
                    price_tool.validate_line(line_for(**{field: bad}), 0)
        clean = price_tool.validate_line(line_for(), 0)
        self.assertEqual(clean["source_sheet"], "Sheet1")
        self.assertEqual(clean["source_cell"], "E5")

    def test_duplicate_item_ids_and_skus_refused(self) -> None:
        with self.assertRaises(price_tool.PriceToolError):
            price_tool.validate_input({"target_currency": "CAD", "lines": [line_for(), line_for()]})
        with self.assertRaises(price_tool.PriceToolError):
            price_tool.validate_input({
                "target_currency": "CAD",
                "lines": [line_for(), line_for(item_id="96274000000523031")],
            })
        with self.assertRaises(price_tool.PriceToolError):
            price_tool.validate_input({
                "target_currency": "CAD",
                "lines": [line_for(), line_for(sku=SKU_470, cost="12.4", cell="H5")],
            })

    def test_line_count_bounds(self) -> None:
        with self.assertRaises(price_tool.PriceToolError):
            price_tool.validate_input({"target_currency": "CAD", "lines": []})
        many = [
            line_for(item_id=str(96274000000523063 + index * 2), sku=f"{SKU_411}{index}")
            for index in range(price_tool.MAX_LINES + 1)
        ]
        with self.assertRaises(price_tool.PriceToolError):
            price_tool.validate_input({"target_currency": "CAD", "lines": many})

    def test_item_id_must_be_canonical_positive_id(self) -> None:
        for bad in ["", "0", "-1", "12.0", " 962740", "abc", None, 96274000000523063, True]:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.validate_line(line_for(item_id=bad), 0)


class StageTests(PriceToolTestCase):
    def test_stage_reads_only_and_writes_nothing(self) -> None:
        plan_path = self.stage()
        self.assertNoWrites()
        self.urlopen.assert_not_called()
        self.assertIn("/inventory/v1/organizations", self.get_paths)
        self.assertTrue(
            any(path.startswith("/inventory/v1/items/96274000000523063?") for path in self.get_paths)
        )
        self.assertIn("STAGED_NOT_COMMITTED", self.stage_output)
        self.assertIn("Zoho writes performed by this stage: 0", self.stage_output)
        self.assertIn("50.20 -> 40.32 CAD", self.stage_output)
        self.assertIn("NOT ATOMIC", self.stage_output)
        self.assertIn("APPROVED", self.stage_output)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["action"], price_tool.ACTION)
        self.assertEqual(plan["risk"]["atomic"], False)
        self.assertEqual(plan["risk"]["sequential_writes"], 1)
        self.assertEqual(plan["organization"], ORGANIZATION)

    def test_plan_expiry_is_exactly_24_hours_and_hashed(self) -> None:
        plan_path = self.stage()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        created = price_tool.parse_time(plan["created_utc"], "created")
        expires = price_tool.parse_time(plan["expires_utc"], "expires")
        self.assertEqual((expires - created).total_seconds(), 24 * 3600)
        core = {key: value for key, value in plan.items() if key != "sha256"}
        self.assertEqual(plan["sha256"], price_tool.digest_for(core))

    def test_stage_records_complete_protected_fingerprint(self) -> None:
        plan_path = self.stage()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        evidence = plan["live_evidence"]["items"][0]
        protected = evidence["protected_state"]
        live = self.items["96274000000523063"]
        for key in live:
            if key in price_tool.UNPROTECTED_FIELDS:
                self.assertNotIn(key, protected)
            else:
                self.assertIn(key, protected)
        for key in ("purchase_rate", "stock_on_hand", "sku", "name", "status",
                    "custom_fields", "account_id", "group_id"):
            self.assertEqual(protected[key], live[key])
        self.assertEqual(evidence["before_rate"], "50.20")
        self.assertEqual(evidence["direction"], "DECREASE")
        self.assertEqual(evidence["endpoint"], "PUT /inventory/v1/items/96274000000523063")

    def test_item_id_mismatch_refused(self) -> None:
        self.items["96274000000523063"]["item_id"] = "96274000000999999"
        with self.assertRaises(price_tool.PriceToolError):
            self.stage()
        self.assertNoWrites()

    def test_sku_mismatch_refused(self) -> None:
        self.items["96274000000523063"]["sku"] = 'FNPTCOUPLING-DERAKANE411-3/4"6"'
        with self.assertRaises(price_tool.PriceToolError) as caught:
            self.stage()
        self.assertIn("live SKU", str(caught.exception))
        self.assertNoWrites()

    def test_non_fnpt_live_item_refused(self) -> None:
        self.items["96274000000523063"]["sku"] = "PIDN750150PSI411"
        with self.assertRaises(price_tool.PriceToolError):
            self.stage([line_for(sku="PIDN750150PSI411")])
        self.assertNoWrites()

    def test_inactive_unsellable_or_combo_item_refused(self) -> None:
        for field, value in [
            ("status", "inactive"),
            ("can_be_sold", False),
            ("is_combo_product", True),
        ]:
            self.reset_harness()
            self.items["96274000000523063"][field] = value
            with self.assertRaises(price_tool.PriceToolError):
                self.stage()
            self.assertNoWrites()

    def test_line_already_at_target_refused(self) -> None:
        self.items["96274000000523063"]["rate"] = 40.32
        with self.assertRaises(price_tool.PriceToolError) as caught:
            self.stage()
        self.assertIn("already at", str(caught.exception))
        self.assertNoWrites()

    def test_wrong_organization_refused(self) -> None:
        self.vault["inventory_organization_id"] = "999999999999"
        with self.assertRaises(price_tool.PriceToolError):
            self.stage()
        self.assertNoWrites()

    def test_non_cad_organization_refused(self) -> None:
        def api_get(access_token, api_domain, path):
            if path == "/inventory/v1/organizations":
                return {"organizations": [{**ORGANIZATION, "currency_code": "USD"}]}
            return self.api_get(access_token, api_domain, path)

        self.api_get_mock.side_effect = api_get
        with self.assertRaises(price_tool.PriceToolError) as caught:
            self.stage()
        self.assertIn("CAD", str(caught.exception))
        self.assertNoWrites()

    def test_non_frp_organization_refused(self) -> None:
        def api_get(access_token, api_domain, path):
            if path == "/inventory/v1/organizations":
                return {"organizations": [{**ORGANIZATION, "name": "Troy Dualam Inc"}]}
            return self.api_get(access_token, api_domain, path)

        self.api_get_mock.side_effect = api_get
        with self.assertRaises((price_tool.PriceToolError, price_tool.zoho_tool.ZohoError)):
            self.stage()
        self.assertNoWrites()


class ApprovalTests(PriceToolTestCase):
    def test_only_exact_uppercase_unpadded_approved(self) -> None:
        price_tool.require_rachad_approval("APPROVED")
        for bad in [
            "approved", "Approved", "APPROVED ", " APPROVED", "\tAPPROVED", "APPROVED\n",
            "APPROVE", "APPROVED!", "YES", "OK", "GO", "APPROVED APPROVED", "",
            None, True, 1, ["APPROVED"], "ＡＰＰＲＯＶＥＤ",
        ]:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.require_rachad_approval(bad)

    def test_wrong_approval_refused_before_any_network_or_lock(self) -> None:
        plan_path = self.stage()
        self.load_vault_mock.reset_mock()
        before_gets = len(self.get_paths)
        for bad in ["approved", "APPROVED ", "yes", ""]:
            with self.assertRaises(price_tool.PriceToolError):
                self.commit(plan_path, approval=bad)
        self.assertNoWrites()
        self.urlopen.assert_not_called()
        self.load_vault_mock.assert_not_called()
        self.assertEqual(len(self.get_paths), before_gets, "a live GET ran before approval")
        self.assertEqual(self.lock_files(), [], "a lock was created without approval")

    def test_staging_alone_never_commits(self) -> None:
        self.stage()
        self.assertNoWrites()
        self.assertEqual(self.lock_files(), [])


class PlanIntegrityTests(PriceToolTestCase):
    def test_tampered_plan_refused(self) -> None:
        plan_path = self.stage()
        original = plan_path.read_text(encoding="utf-8")
        data = json.loads(original)
        data["payload"]["lines"][0]["target_rate_cad"] = "999.99"
        plan_path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(price_tool.PriceToolError) as caught:
            self.commit(plan_path)
        self.assertIn("hash", str(caught.exception).casefold())
        self.assertNoWrites()

    def test_rehashed_tampered_plan_still_refused(self) -> None:
        plan_path = self.stage()
        for mutate in [
            lambda data: data["payload"]["lines"][0].__setitem__("target_rate_cad", "999.99"),
            lambda data: data["payload"]["lines"][0].__setitem__("multiplier", "4.0"),
            lambda data: data["payload"]["lines"][0].__setitem__("supplier_cost_usd", "99.9"),
            lambda data: data["live_evidence"]["items"][0].__setitem__("target_rate_cad", "999.99"),
            lambda data: data["live_evidence"]["items"][0].__setitem__("before_rate", "1.00"),
            lambda data: data["live_evidence"]["items"][0].__setitem__("name", "Renamed Item"),
            lambda data: data["live_evidence"]["items"][0].__setitem__("sku", SKU_470),
            lambda data: data["live_evidence"]["items"][0]["before_state"].__setitem__("rate", 1.0),
            lambda data: data["live_evidence"]["items"][0]["protected_state"].__setitem__(
                "purchase_rate", 1.0),
            lambda data: data["risk"].__setitem__("atomic", True),
            lambda data: data.__setitem__("action", "item_name_sku"),
            lambda data: data.__setitem__("tool", "Some Other Tool"),
            lambda data: data.__setitem__("schema_version", 2),
            lambda data: data.__setitem__("approval_required", "OK"),
            lambda data: data["organization"].__setitem__("organization_id", "999999"),
            lambda data: data["organization"].__setitem__("currency_code", "USD"),
        ]:
            self.reset_harness()
            plan_path = self.stage()
            self.rewrite_plan(plan_path, mutate)
            with self.assertRaises(price_tool.PriceToolError):
                self.commit(plan_path)
            self.assertNoWrites()

    def test_wrong_origin_refused(self) -> None:
        plan_path = self.stage()
        for mutate in [
            lambda data: data["origin"].__setitem__("tool_path", r"C:\AgentTeam\evil.py"),
            lambda data: data["origin"].__setitem__("repo_root", r"C:\AgentTeam"),
            lambda data: data["origin"].__setitem__("plan_dir", r"C:\Temp"),
        ]:
            self.reset_harness()
            plan_path = self.stage()
            self.rewrite_plan(plan_path, mutate)
            with self.assertRaises(price_tool.PriceToolError) as caught:
                self.commit(plan_path)
            self.assertIn("REFUSED", str(caught.exception))
            self.assertNoWrites()

    def test_expired_plan_refused(self) -> None:
        plan_path = self.stage()

        def age(data):
            data["created_utc"] = "2026-08-01T00:00:00+00:00"
            data["expires_utc"] = "2026-08-02T00:00:00+00:00"

        self.rewrite_plan(plan_path, age)
        with self.assertRaises(price_tool.PriceToolError) as caught:
            self.commit(plan_path)
        self.assertIn("expired", str(caught.exception).casefold())
        self.assertNoWrites()

    def test_plan_lifetime_longer_than_24h_refused(self) -> None:
        plan_path = self.stage()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        created = price_tool.parse_time(plan["created_utc"], "created")

        def stretch(data):
            data["expires_utc"] = (created.replace(year=created.year + 1)).isoformat()

        self.rewrite_plan(plan_path, stretch)
        with self.assertRaises(price_tool.PriceToolError):
            self.commit(plan_path)
        self.assertNoWrites()

    def test_plan_outside_the_plan_folder_refused(self) -> None:
        plan_path = self.stage()
        outside = self.root / "copied_plan.json"
        outside.write_text(plan_path.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(price_tool.PriceToolError):
            self.commit(outside)
        with self.assertRaises(price_tool.PriceToolError):
            self.commit(Path("relative_plan.json"))
        self.assertNoWrites()

    def test_evidence_count_or_order_mismatch_refused(self) -> None:
        plan_path = self.stage(
            [line_for(), line_for(item_id="96274000000523031", sku=SKU_470, cost="12.4", cell="H5")]
        )
        self.rewrite_plan(plan_path, lambda data: data["live_evidence"]["items"].reverse())
        with self.assertRaises(price_tool.PriceToolError):
            self.commit(plan_path)
        self.assertNoWrites()


class WriteTransportTests(PriceToolTestCase):
    def test_only_put_to_one_exact_item_endpoint(self) -> None:
        for method in ["GET", "POST", "DELETE", "PATCH", "put", ""]:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.oauth_rate_write_allowed(
                    "token", "https://www.zohoapis.ca", method,
                    "/inventory/v1/items/96274000000523063", "110002157575",
                    {"name": "x", "rate": 40.32},
                )
        for path in [
            "/inventory/v1/items",
            "/inventory/v1/items/0",
            "/inventory/v1/items/abc",
            "/inventory/v1/items/96274000000523063/",
            "/inventory/v1/items/96274000000523063?x=1",
            "/inventory/v1/itemgroups/96274000000523159",
            "/inventory/v1/items/batch",
            "/inventory/v1/items/bulk",
            "/books/v3/items/96274000000523063",
            "/inventory/v1/inventoryadjustments",
            "/inventory/v1/items/96274000000523063/active",
            "/wp-json/wc/v3/products/2061",
        ]:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.oauth_rate_write_allowed(
                    "token", "https://www.zohoapis.ca", "PUT", path, "110002157575",
                    {"name": "x", "rate": 40.32},
                )
        self.assertNoWrites()

    def test_payload_is_name_and_rate_only(self) -> None:
        bad_payloads = [
            {"rate": 40.32},
            {"name": "x"},
            {"name": "x", "rate": 40.32, "purchase_rate": 25.0},
            {"name": "x", "rate": 40.32, "sku": SKU_411},
            {"name": "x", "rate": 40.32, "status": "inactive"},
            {"name": "x", "rate": 40.32, "stock_on_hand": 0},
            {"name": "x", "rate": 40.32, "custom_fields": []},
            {"name": "", "rate": 40.32},
            {"name": " x ", "rate": 40.32},
            {"name": "x", "rate": "40.32"},
            {"name": "x", "rate": 0.0},
            {"name": "x", "rate": -40.32},
            {"name": "x", "rate": 40.325},
            {"name": "x", "rate": True},
            {"name": "x", "rate": None},
        ]
        for payload in bad_payloads:
            with self.assertRaises(price_tool.PriceToolError):
                price_tool.oauth_rate_write_allowed(
                    "token", "https://www.zohoapis.ca", "PUT",
                    "/inventory/v1/items/96274000000523063", "110002157575", payload,
                )
        self.assertNoWrites()

    def test_no_woocommerce_or_foreign_route_surface(self) -> None:
        source = Path(price_tool.__file__).read_text(encoding="utf-8").casefold()
        for forbidden in ["wp-json", "wc/v3", "consumer_key", "consumer_secret",
                          "frpdepots.com", "inventoryadjustments", "/books/v3/",
                          "itemgroups", "requests.", "http://"]:
            self.assertNotIn(forbidden, source)

    def test_write_url_is_built_from_the_zoho_api_domain_only(self) -> None:
        plan_path = self.stage()
        self.allow_writes()
        self.commit(plan_path)
        self.assertTrue(self.write_calls[0]["url"].startswith("https://www.zohoapis.ca/inventory/v1/items/"))


class CommitTests(PriceToolTestCase):
    def test_successful_commit_writes_verifies_and_locks(self) -> None:
        plan_path = self.stage()
        self.allow_writes()
        output = json.loads(self.commit(plan_path))
        self.assertEqual(output["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(output["atomic"], False)
        self.assertEqual(output["replay_locked"], True)
        self.assertEqual(len(self.write_calls), 1)
        call = self.write_calls[0]
        self.assertEqual(call["method"], "PUT")
        self.assertEqual(
            set(call["payload"]), {"name", "rate"}
        )
        self.assertEqual(call["payload"]["rate"], 40.32)
        self.assertEqual(
            call["payload"]["name"], self.items["96274000000523063"]["name"]
        )
        self.assertIn("/inventory/v1/items/96274000000523063?", call["url"])
        lock = json.loads(
            (self.plan_dir / ".commit-locks" / f"{output['plan_sha256']}.json").read_text()
        )
        self.assertEqual(lock["status"], "committed_verified")
        self.assertTrue(lock["no_retry"])

    def test_lock_exists_before_the_first_put(self) -> None:
        plan_path = self.stage()
        self.allow_writes()
        self.commit(plan_path)
        self.assertEqual(len(self.write_calls[0]["lock_existed"]), 1)

    def test_replayed_plan_refused(self) -> None:
        plan_path = self.stage()
        self.allow_writes()
        self.commit(plan_path)
        with self.assertRaises(price_tool.PriceToolError) as caught:
            self.commit(plan_path)
        self.assertIn("replay", str(caught.exception).casefold())
        self.assertEqual(len(self.write_calls), 1)

    def test_stale_live_rate_refused_before_any_write(self) -> None:
        plan_path = self.stage()
        self.items["96274000000523063"]["rate"] = 55.0
        self.allow_writes()
        with self.assertRaises(price_tool.PriceToolError) as caught:
            self.commit(plan_path)
        self.assertIn("changed after review", str(caught.exception))
        self.assertNoWrites()
        lock = json.loads(next((self.plan_dir / ".commit-locks").glob("*.json")).read_text())
        self.assertEqual(lock["status"], "aborted_before_write")
        self.assertTrue(lock["no_retry"])

    def test_stale_protected_state_refused_before_any_write(self) -> None:
        plan_path = self.stage()
        self.items["96274000000523063"]["purchase_rate"] = 26.0
        self.allow_writes()
        with self.assertRaises(price_tool.PriceToolError):
            self.commit(plan_path)
        self.assertNoWrites()

    def test_stale_sku_refused_before_any_write(self) -> None:
        plan_path = self.stage()
        self.items["96274000000523063"]["sku"] = SKU_470
        self.allow_writes()
        with self.assertRaises(price_tool.PriceToolError):
            self.commit(plan_path)
        self.assertNoWrites()

    def test_wrong_live_organization_refused_before_any_write(self) -> None:
        plan_path = self.stage()
        self.vault["inventory_organization_id"] = "999999999999"
        self.allow_writes()
        with self.assertRaises(price_tool.PriceToolError):
            self.commit(plan_path)
        self.assertNoWrites()

    def test_missing_update_scope_refused_before_any_write(self) -> None:
        plan_path = self.stage()
        self.vault["scopes"] = ["ZohoInventory.items.READ"]
        self.allow_writes()
        with self.assertRaises(price_tool.PriceToolError) as caught:
            self.commit(plan_path)
        self.assertIn(price_tool.UPDATE_SCOPE, str(caught.exception))
        self.assertNoWrites()

    def test_no_retry_after_a_put_failure(self) -> None:
        plan_path = self.stage()

        def failing(record):
            raise HTTPError(record["url"], 400, "Bad Request", None, io.BytesIO(b"{}"))

        self.allow_writes(on_write=failing)
        with self.assertRaises(price_tool.PriceToolError) as caught:
            self.commit(plan_path)
        self.assertIn("indeterminate", str(caught.exception))
        self.assertEqual(len(self.write_calls), 1, "the failed PUT was retried")
        lock = json.loads(next((self.plan_dir / ".commit-locks").glob("*.json")).read_text())
        self.assertEqual(lock["status"], "indeterminate")
        self.assertTrue(lock["plan_locked_indeterminate"])
        self.assertTrue(lock["no_retry"])
        self.assertEqual(lock["write_in_flight_item_id"], "96274000000523063")
        with self.assertRaises(price_tool.PriceToolError):
            self.commit(plan_path)
        self.assertEqual(len(self.write_calls), 1)

    def test_readback_rate_mismatch_locks_indeterminate(self) -> None:
        plan_path = self.stage()
        self.allow_writes(on_write=lambda record: None)  # accepted but nothing changed
        with self.assertRaises(price_tool.PriceToolError) as caught:
            self.commit(plan_path)
        self.assertIn("indeterminate", str(caught.exception))
        lock = json.loads(next((self.plan_dir / ".commit-locks").glob("*.json")).read_text())
        self.assertEqual(lock["status"], "indeterminate")
        self.assertIn("read-back rate", lock["reason"])

    def test_protected_field_mutation_after_write_refused(self) -> None:
        plan_path = self.stage()

        def mutate(record):
            item = self.items["96274000000523063"]
            item["rate"] = record["payload"]["rate"]
            item["sales_rate"] = record["payload"]["rate"]
            item["pricebook_rate"] = record["payload"]["rate"]
            item["default_price_brackets"] = [
                {"start_quantity": 1.0, "end_quantity": 1.0,
                 "pricebook_rate": record["payload"]["rate"]}
            ]
            item["purchase_rate"] = 1.0  # must never move

        self.allow_writes(on_write=mutate)
        with self.assertRaises(price_tool.PriceToolError) as caught:
            self.commit(plan_path)
        self.assertIn("indeterminate", str(caught.exception))
        lock = json.loads(next((self.plan_dir / ".commit-locks").glob("*.json")).read_text())
        self.assertIn("changed outside the approved sales rate", lock["reason"])

    def test_rate_mirror_drift_refused(self) -> None:
        plan_path = self.stage()

        def mutate(record):
            item = self.items["96274000000523063"]
            item["rate"] = record["payload"]["rate"]
            item["sales_rate"] = 999.99  # neither the old rate nor the target

        self.allow_writes(on_write=mutate)
        with self.assertRaises(price_tool.PriceToolError):
            self.commit(plan_path)

    def test_sequential_partial_failure_is_explicit(self) -> None:
        plan_path = self.stage(
            [line_for(), line_for(item_id="96274000000523031", sku=SKU_470, cost="12.4", cell="H5")]
        )

        def mutate(record):
            item_id = record["url"].split("/items/", 1)[1].split("?", 1)[0]
            item = self.items[item_id]
            item["rate"] = record["payload"]["rate"]
            item["sales_rate"] = record["payload"]["rate"]
            item["pricebook_rate"] = record["payload"]["rate"]
            item["default_price_brackets"] = [
                {"start_quantity": 1.0, "end_quantity": 1.0,
                 "pricebook_rate": record["payload"]["rate"]}
            ]
            # Somebody else edits the SECOND item between the two writes.
            self.items["96274000000523031"]["purchase_rate"] = 99.0

        self.allow_writes(on_write=mutate)
        with self.assertRaises(price_tool.PriceToolError) as caught:
            self.commit(plan_path)
        message = str(caught.exception)
        self.assertIn("partial_stopped", message)
        self.assertIn("96274000000523063", message)
        self.assertIn("permanently locked", message)
        self.assertEqual(len(self.write_calls), 1, "a later line was written after the stop")
        lock = json.loads(next((self.plan_dir / ".commit-locks").glob("*.json")).read_text())
        self.assertEqual(lock["status"], "partial_stopped")
        self.assertEqual([row["item_id"] for row in lock["completed"]], ["96274000000523063"])
        self.assertTrue(lock["no_retry"])
        self.assertEqual(self.items["96274000000523031"]["rate"], 50.2)


class BuildPlanDataTests(unittest.TestCase):
    """The 26-line build input, checked independently of the tool that made it."""

    @classmethod
    def setUpClass(cls) -> None:
        if not COMPARISON_CSV.exists():
            raise unittest.SkipTest(f"missing {COMPARISON_CSV}")
        with COMPARISON_CSV.open(encoding="utf-8-sig", newline="") as handle:
            cls.rows = list(csv.DictReader(handle))

    def test_source_has_26_calculable_and_6_blank_cost_catalog_matches(self) -> None:
        matched = [row for row in self.rows if row["zoho_item_id"]]
        with_cost = [row for row in matched if row["supplier_cost_present"] == "true"]
        blank = [row for row in matched if row["supplier_cost_present"] != "true"]
        self.assertEqual(len(with_cost), 26)
        self.assertEqual(len(blank), 6)
        for row in blank:
            self.assertEqual(row["supplier_cost_usd"], "")

    def test_stage_input_is_exactly_the_26_calculable_items(self) -> None:
        if not STAGE_INPUT.exists():
            self.skipTest(f"missing {STAGE_INPUT}")
        data = json.loads(STAGE_INPUT.read_text(encoding="utf-8"))
        validated = price_tool.validate_input(data)
        self.assertEqual(len(validated["lines"]), 26)
        expected = {
            row["zoho_item_id"]: row
            for row in self.rows
            if row["zoho_item_id"] and row["supplier_cost_present"] == "true"
        }
        blank_skus = {
            row["zoho_sku"]
            for row in self.rows
            if row["zoho_item_id"] and row["supplier_cost_present"] != "true"
        }
        self.assertEqual(len(blank_skus), 6)
        for line in validated["lines"]:
            self.assertIn(line["item_id"], expected)
            source = expected[line["item_id"]]
            self.assertEqual(line["sku"], source["zoho_sku"])
            self.assertNotIn(line["sku"], blank_skus)
            self.assertEqual(
                Decimal(line["supplier_cost_usd"]), Decimal(source["supplier_cost_usd"])
            )
            self.assertEqual(line["source_cell"], source["source_price_cell"])
            self.assertEqual(line["source_sheet"], source["source_sheet"])
            self.assertEqual(
                line["target_rate_cad"],
                price_tool.money_text(
                    price_tool.compute_target(Decimal(line["supplier_cost_usd"]))
                ),
            )
        self.assertEqual(len({line["item_id"] for line in validated["lines"]}), 26)

    def test_stage_input_costs_match_the_source_workbook_cells(self) -> None:
        if not STAGE_INPUT.exists():
            self.skipTest(f"missing {STAGE_INPUT}")
        if not WORKBOOK.exists():
            self.skipTest(f"missing {WORKBOOK}")
        import openpyxl

        workbook = openpyxl.load_workbook(WORKBOOK, data_only=True)
        data = json.loads(STAGE_INPUT.read_text(encoding="utf-8"))
        for line in data["lines"]:
            self.assertEqual(line["source_workbook"], str(WORKBOOK))
            sheet = workbook[line["source_sheet"]]
            cell = sheet[line["source_cell"]].value
            self.assertIsNotNone(cell, f"{line['source_cell']} is blank in the workbook")
            self.assertEqual(
                Decimal(str(cell)), Decimal(line["supplier_cost_usd"]),
                f"{line['source_cell']} disagrees with the staged cost",
            )

    def test_blank_cost_cells_really_are_blank_in_the_workbook(self) -> None:
        if not WORKBOOK.exists():
            self.skipTest(f"missing {WORKBOOK}")
        import openpyxl

        workbook = openpyxl.load_workbook(WORKBOOK, data_only=True)
        blank = [
            row for row in self.rows
            if row["zoho_item_id"] and row["supplier_cost_present"] != "true"
        ]
        self.assertEqual(len(blank), 6)
        for row in blank:
            sheet = workbook[row["source_sheet"]]
            self.assertIsNone(
                sheet[row["source_price_cell"]].value,
                f"{row['source_price_cell']} is not blank; the exclusion is wrong",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
