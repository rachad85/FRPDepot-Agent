"""Safety tests for the FRP Depot packing-experience collector.

Nothing here touches WooCommerce. The store is a small fake that deliberately
returns MORE than the tool asked for -- full billing and shipping addresses,
emails, phones, totals, taxes, payment methods, order keys, line-item names and
prices -- because the interesting property is not "we asked for a narrow
projection" but "nothing outside it can reach state, stdout, receipts or the
local data files even when the store ignores the projection".

The generic transport (``wc.api_request``) is replaced by a function that fails
on sight, so any route other than the read-only GET helper would be caught here
rather than in production. A source-level check backs that up: no HTTP verb
other than GET appears in the tool at all.

Every test redirects both the operational data root and the receipts file into
a temporary directory, so a test run can never write to
%LOCALAPPDATA% or to the repository's receipts.jsonl.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import json
import math
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
WATCH_DIR = HERE.parent / "watch"
for candidate in (HERE, WATCH_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import woocommerce_common as wc  # noqa: E402
import packing_observation_tool as pot  # noqa: E402
import packing_order_monitor as monitor  # noqa: E402
import packing_observation_weekly_reminder as weekly  # noqa: E402

CATALOG = pot.load_catalog()
GROUP_A = "PKG-001"
GROUP_B = "PKG-002"
TARGET_A = CATALOG.by_variation[CATALOG.groups[GROUP_A]["variation_ids"][0]]
TARGET_A2 = CATALOG.by_variation[CATALOG.groups[GROUP_A]["variation_ids"][1]]
TARGET_B = CATALOG.by_variation[CATALOG.groups[GROUP_B]["variation_ids"][0]]

BASE_TIME = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

# Values that must never appear anywhere the tool writes or prints.
PII_MARKERS = (
    "john.doe@example.com", "Doe", "555-0134", "123 Main Street", "Hamilton",
    "199.99", "24.99", "cheque", "wc_order_secretkey", "10.4.2.9",
    "Mozilla/5.0", "FRP Elbow 1 in 150 psi", "customer_note_text",
)


def module_body_without_docstring(module) -> str:
    """Source of a module with its leading docstring removed.

    Sliced by line rather than string-replaced: a docstring containing an
    escape (this tool's own ``%LOCALAPPDATA%\\...`` path) does not appear in the
    source verbatim, so a naive replace silently leaves the prose in place and
    the check below stops checking anything.
    """
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    first = tree.body[0] if tree.body else None
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        return "\n".join(source.splitlines()[first.end_lineno:])
    return source


def raw_order(order_id: int, status: str, minutes: int, lines: list[dict]) -> dict:
    """A WooCommerce order as a leaky store might really return it."""
    created = (BASE_TIME + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "id": order_id,
        "number": str(order_id),
        "status": status,
        "date_created_gmt": created,
        "date_modified_gmt": created,
        "currency": "CAD",
        "total": "199.99",
        "total_tax": "24.99",
        "discount_total": "0.00",
        "shipping_total": "35.00",
        "prices_include_tax": False,
        "customer_id": 42,
        "customer_note": "customer_note_text",
        "customer_ip_address": "10.4.2.9",
        "customer_user_agent": "Mozilla/5.0",
        "order_key": "wc_order_secretkey",
        "payment_method": "cheque",
        "payment_method_title": "Cheque payment",
        "transaction_id": "txn_9931",
        "date_paid": created,
        "billing": {
            "first_name": "John", "last_name": "Doe", "email": "john.doe@example.com",
            "phone": "555-0134", "address_1": "123 Main Street", "city": "Hamilton",
        },
        "shipping": {
            "first_name": "John", "last_name": "Doe", "address_1": "123 Main Street",
            "city": "Hamilton",
        },
        "meta_data": [{"id": 1, "key": "_billing_email", "value": "john.doe@example.com"}],
        "tax_lines": [{"id": 7, "rate_code": "HST", "tax_total": "24.99"}],
        "shipping_lines": [{"id": 8, "method_title": "Flat rate", "total": "35.00"}],
        "coupon_lines": [],
        "refunds": [],
        "line_items": lines,
    }


def raw_line(line_id: int, product_id: int, variation_id: int, quantity, sku: str) -> dict:
    return {
        "id": line_id,
        "name": "FRP Elbow 1 in 150 psi",
        "product_id": product_id,
        "variation_id": variation_id,
        "quantity": quantity,
        "sku": sku,
        "price": 99.99,
        "total": "199.99",
        "subtotal": "199.99",
        "total_tax": "24.99",
        "taxes": [{"id": 7, "total": "24.99"}],
        "meta_data": [{"id": 3, "key": "_customer_email", "value": "john.doe@example.com"}],
    }


def target_line(line_id: int, target: dict, quantity=1, sku: str | None = None) -> dict:
    return raw_line(line_id, 1400, int(target["variation_id"]), quantity,
                    target["sku"] if sku is None else sku)


class FakeStore:
    """A read-only WooCommerce stand-in. It can only be asked for orders."""

    def __init__(self):
        self.orders: dict[int, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.fail_list_with: Exception | None = None
        self.missing: set[int] = set()

    def add(self, order: dict) -> dict:
        self.orders[int(order["id"])] = order
        return order

    def api_get(self, endpoint, params=None, vault=None):
        params = dict(params or {})
        self.calls.append((endpoint, params))
        match = re.fullmatch(r"/orders/(\d+)", endpoint)
        if match:
            order_id = int(match.group(1))
            if order_id in self.missing or order_id not in self.orders:
                raise wc.WooError(f"WooCommerce GET /orders/{order_id} failed with HTTP 404",
                                  status=404, code="woocommerce_rest_shop_order_invalid_id")
            return self.orders[order_id], {}
        if endpoint != "/orders":
            raise AssertionError("the packing collector must only read orders: " + endpoint)
        if self.fail_list_with is not None:
            raise self.fail_list_with
        rows = sorted(self.orders.values(), key=lambda item: int(item["id"]))
        after = params.get("after")
        if after:
            cutoff = datetime.fromisoformat(after)
            rows = [row for row in rows
                    if datetime.fromisoformat(row["date_created_gmt"]) > cutoff]
        if str(params.get("order") or "asc") == "desc":
            rows = list(reversed(rows))
        per_page = int(params.get("per_page") or 10)
        page = int(params.get("page") or 1)
        total_pages = max(1, math.ceil(len(rows) / per_page)) if rows else 1
        chunk = rows[(page - 1) * per_page: page * per_page]
        return chunk, {"x-wp-total": str(len(rows)), "x-wp-totalpages": str(total_pages)}


class PackingTestCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="packing-test-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.data_root = self.temp / "data"
        self.receipts = self.temp / "receipts.jsonl"
        self._swap(pot, "DATA_ROOT", self.data_root)
        self._swap(wc, "RECEIPTS", self.receipts)
        self.store = FakeStore()
        self._swap(wc, "api_get", self.store.api_get)
        self._swap(wc, "load_vault", lambda: {
            "site_url": "https://frpdepots.com",
            "consumer_key": "ck_" + "a" * 32,
            "consumer_secret": "cs_" + "b" * 32,
            "declared_permissions": "read_write",
        })
        self._swap(wc, "api_request", self._forbidden_transport)

    def _swap(self, module, name, value):
        patcher = mock.patch.object(module, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _forbidden_transport(*args, **kwargs):
        raise AssertionError(
            "the packing collector reached the generic transport; only api_get/get_all are allowed"
        )

    # -- helpers -----------------------------------------------------------

    def initialize(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return pot.run_initialize(catalog=CATALOG)

    def scan(self):
        return pot.run_scan(catalog=CATALOG)

    def read_state(self):
        return json.loads(pot.state_path().read_text(encoding="utf-8"))

    def opportunities(self):
        return pot.read_jsonl(pot.opportunities_path())

    def events(self):
        return pot.read_jsonl(pot.events_path())

    def receipts_text(self):
        return self.receipts.read_text(encoding="utf-8") if self.receipts.exists() else ""

    def all_written_text(self):
        chunks = [self.receipts_text()]
        if self.data_root.exists():
            for path in sorted(self.data_root.rglob("*")):
                if path.is_file() and path.suffix != ".lock":
                    try:
                        chunks.append(path.read_text(encoding="utf-8"))
                    except UnicodeDecodeError:
                        pass
        return "\n".join(chunks)

    def assert_no_pii(self, text: str, label: str):
        for marker in PII_MARKERS:
            self.assertNotIn(marker, text, f"{label} leaked {marker!r}")

    def measurement_args(self, opportunity_id, package_number=1, packed_quantity="1",
                         length="20", width="18", height="10", weight="0.9",
                         material="Double-wall box", evidence="photo IMG-1471",
                         notes="", supersedes=None):
        namespace = argparse.Namespace(
            opportunity_id=opportunity_id, package_number=package_number,
            packed_quantity=packed_quantity, length_cm=length, width_cm=width,
            height_cm=height, gross_weight_kg=weight, packing_material=material,
            evidence_ref=evidence, notes=notes, json=False,
        )
        if supersedes is not None:
            namespace.supersedes_event_id = supersedes
        return namespace

    def queue_order(self, order_id, minutes, target=TARGET_A, quantity=1, status="processing"):
        self.store.add(raw_order(order_id, status, minutes,
                                 [target_line(order_id * 10, target, quantity)]))
        return self.scan()

    def record_single(self, opportunity_id, **kwargs):
        return pot.run_record(self.measurement_args(opportunity_id, **kwargs))


# ---------------------------------------------------------------------------
# 1-2  Catalog and projection
# ---------------------------------------------------------------------------

class CatalogAndProjectionTests(PackingTestCase):
    def test_catalog_is_37_groups_78_variations_and_hash_pinned(self):
        self.assertEqual(CATALOG.group_count, 37)
        self.assertEqual(CATALOG.variation_count, 78)
        self.assertEqual(len(CATALOG.by_sku), 78)
        self.assertEqual(CATALOG.sha256, pot.ESTIMATE_CSV_SHA256)
        self.assertTrue(all(variation > 0 for variation in CATALOG.by_variation))
        self.assertTrue(all(sku.strip() for sku in CATALOG.by_sku))
        for group in CATALOG.groups.values():
            self.assertEqual(len(group["variation_ids"]), len(group["skus"]))

    def test_a_different_estimate_revision_is_refused(self):
        copy = self.temp / "estimates.csv"
        copy.write_text(pot.CATALOG_PATH.read_text(encoding="utf-8-sig") + "\n# edited\n",
                        encoding="utf-8")
        with self.assertRaises(pot.PackingError) as caught:
            pot.load_catalog(copy)
        self.assertIn("commissioned revision", str(caught.exception))

    def test_narrative_columns_are_not_loaded(self):
        target = dict(TARGET_A)
        for banned in ("dimension_basis", "weight_basis", "ups_estimate_detail",
                       "selected_component_basis"):
            self.assertNotIn(banned, target)

    def test_projection_excludes_every_forbidden_field(self):
        self.assertEqual(
            pot.ORDER_PROJECTION,
            "id,number,date_created_gmt,date_modified_gmt,status,line_items.id,"
            "line_items.product_id,line_items.variation_id,line_items.quantity,line_items.sku",
        )
        for forbidden in ("billing", "shipping", "customer", "total", "payment", "meta_data",
                          "line_items.name", "line_items.total", "line_items.price"):
            self.assertNotIn(forbidden, pot.ORDER_PROJECTION)

        leaky = raw_order(9001, "processing", 1, [target_line(90010, TARGET_A, 2)])
        projected = pot.project_order(leaky)
        self.assertEqual(set(projected), set(pot.ALLOWED_ORDER_FIELDS) | {"line_items"})
        self.assertEqual(set(projected["line_items"][0]), set(pot.ALLOWED_LINE_FIELDS))
        self.assertEqual(pot.find_forbidden_keys(projected), [])
        self.assert_no_pii(json.dumps(projected), "projected order")

    def test_projection_survives_a_hostile_shape(self):
        projected = pot.project_order({"id": "7", "line_items": "not-a-list",
                                       "billing": {"email": "x@example.com"}})
        self.assertEqual(projected["id"], 7)
        self.assertEqual(projected["line_items"], [])
        self.assertEqual(pot.project_order(None)["line_items"], [])
        self.assertEqual(pot.project_line_item("garbage")["sku"], "")


# ---------------------------------------------------------------------------
# 3-4  initialize
# ---------------------------------------------------------------------------

class InitializeTests(PackingTestCase):
    def test_initialize_is_future_only_and_queues_nothing(self):
        self.store.add(raw_order(1001, "completed", 0, [target_line(10010, TARGET_A, 3)]))
        self.store.add(raw_order(1002, "processing", 1, [target_line(10020, TARGET_B, 1)]))
        result = self.initialize()

        self.assertEqual(result["baseline_order_id"], 1002)
        self.assertEqual(result["queued_opportunities"], 0)
        self.assertTrue(result["future_only"])
        state = self.read_state()
        self.assertTrue(state["future_only_baseline"])
        self.assertEqual(state["high_water_order_id"], 1002)
        self.assertEqual(state["pending_orders"], {})
        self.assertEqual(self.opportunities(), [])
        self.assert_no_pii(self.all_written_text(), "initialize output")
        self.assertIn("packing_observation_initialized", self.receipts_text())

    def test_initialize_with_an_empty_store(self):
        result = self.initialize()
        self.assertEqual(result["baseline_order_id"], 0)
        self.assertEqual(self.opportunities(), [])

    def test_initialize_refuses_when_state_already_exists(self):
        self.store.add(raw_order(1001, "completed", 0, []))
        self.initialize()
        before = pot.state_path().read_text(encoding="utf-8")
        with self.assertRaises(pot.PackingError) as caught:
            self.initialize()
        self.assertIn("already initialized", str(caught.exception))
        self.assertEqual(pot.state_path().read_text(encoding="utf-8"), before)

    def test_scan_refuses_before_initialization(self):
        with self.assertRaises(pot.PackingError) as caught:
            self.scan()
        self.assertEqual(caught.exception.signature, "not_initialized")


# ---------------------------------------------------------------------------
# 5-11  scan behaviour
# ---------------------------------------------------------------------------

class ScanTests(PackingTestCase):
    def setUp(self):
        super().setUp()
        self.store.add(raw_order(1000, "completed", 0, [target_line(10000, TARGET_A, 1)]))
        self.initialize()

    def test_new_eligible_order_creates_exactly_one_opportunity(self):
        result = self.queue_order(1010, 5, TARGET_A, quantity=2)
        self.assertEqual(result["new_opportunity_count"], 1)
        stored = self.opportunities()
        self.assertEqual(len(stored), 1)
        record = stored[0]
        self.assertEqual(record["order_id"], 1010)
        self.assertEqual(record["sku"], TARGET_A["sku"])
        self.assertEqual(record["ordered_quantity"], "2")
        self.assertEqual(record["group_id"], GROUP_A)
        self.assertEqual(record["estimate"]["packed_length_cm"],
                         CATALOG.groups[GROUP_A]["packed_length_cm"])
        self.assertEqual(record["estimate_csv_sha256"], pot.ESTIMATE_CSV_SHA256)
        self.assertEqual(pot.find_forbidden_keys(record), [])

    def test_every_fulfillment_status_is_eligible(self):
        for index, status in enumerate(sorted(pot.FULFILLMENT_STATUSES)):
            self.queue_order(1100 + index, 10 + index, TARGET_A, status=status)
        self.assertEqual(len(self.opportunities()), len(pot.FULFILLMENT_STATUSES))

    def test_two_target_lines_in_one_order_create_two_opportunities(self):
        self.store.add(raw_order(1020, "processing", 6, [
            target_line(10201, TARGET_A, 1),
            target_line(10202, TARGET_B, 4),
        ]))
        result = self.scan()
        self.assertEqual(result["new_opportunity_count"], 2)
        self.assertEqual({row["group_id"] for row in self.opportunities()}, {GROUP_A, GROUP_B})

    def test_irrelevant_product_is_ignored(self):
        self.store.add(raw_order(1030, "processing", 7, [
            raw_line(10301, 999, 0, 5, "NOT-A-PACKING-TARGET"),
            raw_line(10302, 998, 987654, 1, "ALSO-NOT-A-TARGET"),
        ]))
        result = self.scan()
        self.assertEqual(result["new_opportunity_count"], 0)
        self.assertEqual(self.opportunities(), [])
        self.assertEqual(result["pending_count"], 0)

    def test_terminal_statuses_never_become_opportunities(self):
        for index, status in enumerate(sorted(pot.TERMINAL_STATUSES)):
            self.store.add(raw_order(1200 + index, status, 20 + index,
                                     [target_line(12000 + index, TARGET_A, 1)]))
        result = self.scan()
        self.assertEqual(result["new_opportunity_count"], 0)
        self.assertEqual(self.opportunities(), [])
        self.assertEqual(sorted(result["terminal_orders"]),
                         sorted(1200 + i for i in range(len(pot.TERMINAL_STATUSES))))

    def test_pending_target_is_retained_and_queued_when_it_becomes_eligible(self):
        order = self.store.add(raw_order(1040, "pending", 8, [target_line(10400, TARGET_A, 1)]))
        first = self.scan()
        self.assertEqual(first["new_opportunity_count"], 0)
        self.assertEqual(first["pending_count"], 1)
        self.assertEqual(list(self.read_state()["pending_orders"]), ["1040"])
        self.assertEqual(set(self.read_state()["pending_orders"]["1040"]),
                         {"order_id", "status", "first_seen_utc", "last_checked_utc"})

        order["status"] = "processing"
        second = self.scan()
        self.assertEqual(second["new_opportunity_count"], 1)
        self.assertEqual(second["pending_rechecked"], 1)
        self.assertEqual(self.read_state()["pending_orders"], {})
        self.assertEqual(self.opportunities()[0]["order_id"], 1040)

    def test_pending_target_that_is_cancelled_leaves_the_pending_list(self):
        order = self.store.add(raw_order(1050, "pending", 9, [target_line(10500, TARGET_A, 1)]))
        self.scan()
        order["status"] = "cancelled"
        result = self.scan()
        self.assertEqual(result["pending_count"], 0)
        self.assertEqual(self.opportunities(), [])
        self.assertEqual(result["terminal_orders"], [1050])

    def test_pending_order_deleted_at_the_store_is_dropped(self):
        self.store.add(raw_order(1060, "pending", 10, [target_line(10600, TARGET_A, 1)]))
        self.scan()
        self.store.missing.add(1060)
        result = self.scan()
        self.assertEqual(result["pending_removed_gone"], [1060])
        self.assertEqual(self.read_state()["pending_orders"], {})

    def test_duplicate_scan_is_idempotent(self):
        self.queue_order(1070, 11, TARGET_A, quantity=1)
        self.assertEqual(len(self.opportunities()), 1)
        self.assertEqual(self.scan()["new_opportunity_count"], 0)

        # Rewind the cursor so the same order is genuinely re-read and re-matched:
        # idempotency must come from the deterministic opportunity id, not from
        # the high-water mark hiding the order.
        state = self.read_state()
        state["high_water_order_id"] = 1000
        state["cursor_date_created_gmt"] = pot.woo_timestamp(BASE_TIME)
        pot.save_state(state)
        replay = self.scan()
        self.assertEqual(replay["new_opportunity_count"], 0)
        self.assertEqual(len(self.opportunities()), 1)

    def test_opportunity_id_is_deterministic(self):
        first = pot.opportunity_id_for("https://frpdepots.com", 1010, 10100, 1424)
        self.assertEqual(first, pot.opportunity_id_for("https://frpdepots.com", 1010, 10100, 1424))
        self.assertNotEqual(first, pot.opportunity_id_for("https://frpdepots.com", 1011, 10100, 1424))
        self.assertNotEqual(first, pot.opportunity_id_for("https://other.example", 1010, 10100, 1424))

    def test_sku_mismatch_fails_before_any_local_mutation(self):
        before_state = pot.state_path().read_text(encoding="utf-8")
        self.store.add(raw_order(1080, "processing", 12,
                                 [target_line(10800, TARGET_A, 1, sku="WRONG-SKU")]))
        with self.assertRaises(pot.PackingError) as caught:
            self.scan()
        self.assertEqual(caught.exception.signature, "sku_mismatch")
        self.assertFalse(pot.opportunities_path().exists())
        self.assertEqual(pot.state_path().read_text(encoding="utf-8"), before_state)

    def test_missing_sku_on_a_target_variation_is_refused(self):
        self.store.add(raw_order(1085, "processing", 13,
                                 [target_line(10850, TARGET_A, 1, sku="")]))
        with self.assertRaises(pot.PackingError):
            self.scan()
        self.assertFalse(pot.opportunities_path().exists())

    def test_target_sku_on_a_foreign_variation_is_refused(self):
        self.store.add(raw_order(1086, "processing", 14,
                                 [raw_line(10860, 1400, 555555, 1, TARGET_A["sku"])]))
        with self.assertRaises(pot.PackingError) as caught:
            self.scan()
        self.assertEqual(caught.exception.signature, "sku_identity_conflict")
        self.assertFalse(pot.opportunities_path().exists())

    def test_cursor_updates_only_after_a_fully_successful_scan(self):
        self.queue_order(1090, 15, TARGET_A)
        good_state = self.read_state()
        self.assertEqual(good_state["high_water_order_id"], 1090)

        self.store.add(raw_order(1091, "processing", 16,
                                 [target_line(10910, TARGET_B, 1, sku="WRONG")]))
        with self.assertRaises(pot.PackingError):
            self.scan()
        self.assertEqual(self.read_state(), good_state)
        self.assertEqual(len(self.opportunities()), 1)

        self.store.fail_list_with = wc.WooError("WooCommerce could not be reached", status=None)
        self.store.orders.pop(1091)
        with self.assertRaises(wc.WooError):
            self.scan()
        self.assertEqual(self.read_state(), good_state)

    def test_scan_is_bounded(self):
        self.assertEqual(pot.SCAN_MAX_PAGES, 10)
        self.assertEqual(pot.SCAN_MAX_RECORDS, 1000)
        for index in range(25):
            self.store.add(raw_order(2000 + index, "processing", 40 + index,
                                     [target_line(20000 + index, TARGET_A, 1)]))
        result = self.scan()
        self.assertEqual(result["new_opportunity_count"], 25)
        pages = [params for endpoint, params in self.store.calls
                 if endpoint == "/orders" and "page" in params]
        self.assertTrue(all(int(params["per_page"]) <= 100 for params in pages))
        self.assertLessEqual(len(pages), pot.SCAN_MAX_PAGES)

    def test_site_change_is_refused(self):
        self._swap(wc, "load_vault", lambda: {
            "site_url": "https://example.invalid", "consumer_key": "ck_" + "a" * 32,
            "consumer_secret": "cs_" + "b" * 32, "declared_permissions": "read_write",
        })
        with self.assertRaises(pot.PackingError) as caught:
            self.scan()
        self.assertEqual(caught.exception.signature, "site_changed")


# ---------------------------------------------------------------------------
# 12-13  privacy and transport
# ---------------------------------------------------------------------------

class PrivacyAndTransportTests(PackingTestCase):
    def test_injected_pii_and_money_cannot_reach_state_files_stdout_or_receipts(self):
        self.store.add(raw_order(3000, "completed", 0, [target_line(30000, TARGET_A, 1)]))
        self.initialize()
        self.store.add(raw_order(3001, "processing", 5, [
            target_line(30010, TARGET_A, 1),
            target_line(30011, TARGET_B, 2),
        ]))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            pot.command_scan(argparse.Namespace(json=False))
            pot.command_pending(argparse.Namespace(limit=20, json=False))
            monitor.run([])
        opportunity = self.opportunities()[0]["opportunity_id"]
        with contextlib.redirect_stdout(stdout):
            pot.command_record(self.measurement_args(opportunity))
            pot.command_show(argparse.Namespace(opportunity_id=opportunity, json=True))
            pot.command_report(argparse.Namespace(json=False))

        self.assert_no_pii(stdout.getvalue(), "stdout")
        self.assert_no_pii(self.all_written_text(), "written files")
        for record in self.opportunities():
            self.assertEqual(pot.find_forbidden_keys(record), [])
        for record in self.events():
            self.assertEqual(pot.find_forbidden_keys(record), [])
        self.assertEqual(pot.find_forbidden_keys(self.read_state()), [])
        self.assertIn("packing_observation_opportunities_queued", self.receipts_text())

    def test_only_the_read_only_get_helper_is_used(self):
        self.store.add(raw_order(3100, "completed", 0, [target_line(31000, TARGET_A, 1)]))
        self.initialize()
        self.store.add(raw_order(3101, "processing", 3, [target_line(31010, TARGET_A, 1)]))
        self.scan()
        self.assertTrue(self.store.calls)
        for endpoint, _params in self.store.calls:
            self.assertTrue(endpoint == "/orders" or re.fullmatch(r"/orders/\d+", endpoint),
                            f"unexpected endpoint {endpoint}")
        with self.assertRaises(AssertionError):
            wc.api_request("GET", "/orders")

    def test_no_write_verb_appears_in_the_tool_source(self):
        body = module_body_without_docstring(pot)
        for verb in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertIsNone(re.search(rf"""['"]{verb}['"]""", body),
                              f"the packing collector must not name {verb}")
        self.assertNotIn("api_request(", body)
        self.assertNotIn("payload=", body)
        for banned in ("wordpress", "zoho", "smtp", "urlopen", "requests."):
            self.assertNotIn(banned, body.lower())

    def test_monitor_and_reminder_sources_hold_no_write_verb(self):
        for module in (monitor, weekly):
            body = module_body_without_docstring(module)
            for verb in ("POST", "PUT", "PATCH", "DELETE"):
                self.assertIsNone(re.search(rf"""['"]{verb}['"]""", body))


# ---------------------------------------------------------------------------
# 14-20  measurement events
# ---------------------------------------------------------------------------

class MeasurementTests(PackingTestCase):
    def setUp(self):
        super().setUp()
        self.store.add(raw_order(4000, "completed", 0, [target_line(40000, TARGET_A, 1)]))
        self.initialize()
        self.queue_order(4001, 5, TARGET_A, quantity=3)
        self.opportunity = self.opportunities()[0]["opportunity_id"]

    def test_record_appends_a_valid_event_and_sorts_dimensions(self):
        result = pot.run_record(self.measurement_args(
            self.opportunity, length="10", width="30", height="20", weight="1.25"))
        self.assertEqual(result["status"], "RECORDED")
        events = self.events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual((event["length_cm"], event["width_cm"], event["height_cm"]),
                         ("30", "20", "10"))
        self.assertEqual(event["gross_weight_kg"], "1.25")
        self.assertEqual(event["recorded_by"], "Rachad Homsi")
        self.assertEqual(event["event_type"], "measurement")
        self.assertIsNone(event["supersedes_event_id"])
        self.assertEqual(event["estimate_csv_sha256"], pot.ESTIMATE_CSV_SHA256)
        self.assertIn("packing_observation_measurement_recorded", self.receipts_text())
        self.assertTrue(pot.observations_csv_path().exists())

    def test_recorded_by_is_fixed(self):
        self.assertEqual(pot.RECORDED_BY, "Rachad Homsi")
        parser = pot.build_parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["record", "--opportunity-id", "x", "--recorded-by", "Someone Else"])

    def test_unknown_opportunity_is_refused(self):
        with self.assertRaises(pot.PackingError):
            pot.run_record(self.measurement_args("OPP-does-not-exist"))
        self.assertEqual(self.events(), [])

    def test_missing_zero_negative_and_huge_measurements_are_refused(self):
        cases = [
            {"length": None}, {"length": ""}, {"length": "0"}, {"length": "-3"},
            {"length": "1001"}, {"width": "0"}, {"width": "-1"}, {"height": "0"},
            {"height": "1000.5"}, {"weight": "0"}, {"weight": "-2"}, {"weight": "2000.1"},
            {"packed_quantity": "0"}, {"packed_quantity": "-1"},
            {"length": "abc"}, {"weight": "NaN"}, {"material": "  "}, {"evidence": ""},
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(pot.PackingError):
                pot.run_record(self.measurement_args(self.opportunity, **case))
        with self.assertRaises(pot.PackingError):
            pot.run_record(self.measurement_args(self.opportunity, package_number=0))
        self.assertEqual(self.events(), [])

    def test_boundary_values_are_accepted(self):
        pot.run_record(self.measurement_args(
            self.opportunity, length="1000", width="1000", height="1000", weight="2000"))
        self.assertEqual(len(self.events()), 1)

    def test_quantity_overrun_is_refused(self):
        self.record_single(self.opportunity, package_number=1, packed_quantity="2")
        with self.assertRaises(pot.PackingError) as caught:
            self.record_single(self.opportunity, package_number=2, packed_quantity="2")
        self.assertIn("exceeds the 3 ordered", str(caught.exception))
        self.assertEqual(len(self.events()), 1)
        self.record_single(self.opportunity, package_number=2, packed_quantity="1")
        self.assertEqual(len(self.events()), 2)

    def test_duplicate_active_package_number_is_refused(self):
        self.record_single(self.opportunity, package_number=1)
        with self.assertRaises(pot.PackingError) as caught:
            self.record_single(self.opportunity, package_number=1)
        self.assertIn("already has an active measurement", str(caught.exception))
        self.assertEqual(len(self.events()), 1)

    def test_correction_appends_and_supersedes_without_editing_history(self):
        first = pot.run_record(self.measurement_args(self.opportunity, length="20"))
        raw_before = pot.events_path().read_text(encoding="utf-8")
        second = pot.run_correct(self.measurement_args(
            self.opportunity, length="26", supersedes=first["event_id"]))

        raw_after = pot.events_path().read_text(encoding="utf-8")
        self.assertTrue(raw_after.startswith(raw_before), "history was rewritten")
        events = self.events()
        self.assertEqual(len(events), 2)
        live = pot.active_events(events)
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["event_id"], second["event_id"])
        self.assertEqual(live[0]["event_type"], "correction")
        self.assertEqual(live[0]["length_cm"], "26")
        self.assertEqual(live[0]["supersedes_event_id"], first["event_id"])
        self.assertEqual(pot.run_validate(CATALOG)["status"], "VALID")

    def test_correction_against_the_wrong_event_or_opportunity_is_refused(self):
        first = pot.run_record(self.measurement_args(self.opportunity))
        with self.assertRaises(pot.PackingError):
            pot.run_correct(self.measurement_args(self.opportunity, supersedes="not-an-event"))

        self.queue_order(4002, 9, TARGET_B, quantity=1)
        other = next(row["opportunity_id"] for row in self.opportunities()
                     if row["order_id"] == 4002)
        with self.assertRaises(pot.PackingError) as caught:
            pot.run_correct(self.measurement_args(other, supersedes=first["event_id"]))
        self.assertIn("belongs to", str(caught.exception))

        with self.assertRaises(pot.PackingError) as caught:
            pot.run_correct(self.measurement_args(
                self.opportunity, package_number=2, supersedes=first["event_id"]))
        self.assertIn("same package", str(caught.exception))

        corrected = pot.run_correct(self.measurement_args(
            self.opportunity, supersedes=first["event_id"]))
        with self.assertRaises(pot.PackingError) as caught:
            pot.run_correct(self.measurement_args(
                self.opportunity, supersedes=first["event_id"]))
        self.assertIn("already been superseded", str(caught.exception))
        self.assertEqual(len(pot.active_events(self.events())), 1)
        self.assertEqual(pot.active_events(self.events())[0]["event_id"], corrected["event_id"])

    def test_correction_still_enforces_the_ordered_quantity(self):
        first = pot.run_record(self.measurement_args(self.opportunity, packed_quantity="1"))
        self.record_single(self.opportunity, package_number=2, packed_quantity="2")
        with self.assertRaises(pot.PackingError):
            pot.run_correct(self.measurement_args(
                self.opportunity, packed_quantity="2", supersedes=first["event_id"]))
        self.assertEqual(len(self.events()), 2)

    def test_secret_looking_evidence_or_notes_are_refused(self):
        secrets = [
            {"evidence": "ck_1234567890abcdefghij"},
            {"evidence": "cs_1234567890abcdefghij"},
            {"evidence": "Bearer eyJhbGciOi"},
            {"evidence": "the wifi password is hunter2"},
            {"evidence": "api key attached"},
            {"evidence": "token=abc"},
            {"notes": "secret handshake"},
            {"notes": "A" * 40},
            {"material": "box, password protected"},
        ]
        for case in secrets:
            with self.subTest(case=case), self.assertRaises(pot.PackingError):
                pot.run_record(self.measurement_args(self.opportunity, **case))
        self.assertEqual(self.events(), [])

    def test_an_ordinary_evidence_reference_is_accepted(self):
        for reference in ("photo IMG_4471.jpg", "packing-list-2026-08-10-order-4001",
                          "UPS label 1Z999", "carrier document 4471-A"):
            with self.subTest(reference=reference):
                self.assertIsNone(pot.contains_secret(reference))

    def test_show_and_pending_are_bounded_and_quiet(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            pot.command_pending(argparse.Namespace(limit=20, json=False))
            pot.command_show(argparse.Namespace(opportunity_id=self.opportunity, json=False))
        text = stdout.getvalue()
        self.assertIn(pot.REVIEW_ONLY_BANNER, text)
        self.assert_no_pii(text, "pending/show")
        self.record_single(self.opportunity, packed_quantity="3")
        with contextlib.redirect_stdout(io.StringIO()) as after:
            pot.command_pending(argparse.Namespace(limit=20, json=True))
        self.assertEqual(json.loads(after.getvalue())["pending_count"], 0)


# ---------------------------------------------------------------------------
# 21-25  derived experience and recommendations
# ---------------------------------------------------------------------------

class ExperienceTests(PackingTestCase):
    def setUp(self):
        super().setUp()
        self.store.add(raw_order(5000, "completed", 0, [target_line(50000, TARGET_A, 1)]))
        self.initialize()
        self.next_minute = 5
        self.next_order = 5001

    def add_measured_order(self, quantity=1, packed_quantity="1", length="20", width="18",
                           height="10", weight="0.9", target=TARGET_A):
        order_id = self.next_order
        self.next_order += 1
        self.next_minute += 1
        self.queue_order(order_id, self.next_minute, target, quantity=quantity)
        opportunity = next(row["opportunity_id"] for row in self.opportunities()
                           if row["order_id"] == order_id)
        pot.run_record(self.measurement_args(
            opportunity, packed_quantity=packed_quantity, length=length, width=width,
            height=height, weight=weight))
        return opportunity

    def group_row(self, group_id=GROUP_A):
        experience = pot.build_experience(CATALOG)
        return next(row for row in experience["groups"] if row["group_id"] == group_id)

    def test_confidence_thresholds(self):
        self.assertEqual(pot.confidence_for(0), pot.CONFIDENCE_NO_DATA)
        self.assertEqual(pot.confidence_for(1), pot.CONFIDENCE_LOW)
        self.assertEqual(pot.confidence_for(2), pot.CONFIDENCE_LOW_MEDIUM)
        self.assertEqual(pot.confidence_for(3), pot.CONFIDENCE_MEDIUM)
        self.assertEqual(pot.confidence_for(4), pot.CONFIDENCE_MEDIUM)
        self.assertEqual(pot.confidence_for(5), pot.CONFIDENCE_MEDIUM_HIGH)
        self.assertEqual(pot.confidence_for(50), pot.CONFIDENCE_MEDIUM_HIGH)

    def test_confidence_climbs_with_distinct_orders(self):
        expected = {
            1: pot.CONFIDENCE_LOW, 2: pot.CONFIDENCE_LOW_MEDIUM,
            3: pot.CONFIDENCE_MEDIUM, 4: pot.CONFIDENCE_MEDIUM,
            5: pot.CONFIDENCE_MEDIUM_HIGH,
        }
        self.assertEqual(self.group_row()["data_confidence"], pot.CONFIDENCE_NO_DATA)
        for count in range(1, 6):
            self.add_measured_order()
            row = self.group_row()
            self.assertEqual(row["distinct_single_piece_orders"], count)
            self.assertEqual(row["data_confidence"], expected[count])

    def test_multi_piece_package_is_kept_but_never_improves_the_one_piece_estimate(self):
        self.add_measured_order(quantity=4, packed_quantity="4", length="90", weight="6")
        row = self.group_row()
        self.assertEqual(row["measured_packages"], 1)
        self.assertEqual(row["multi_piece_packages"], 1)
        self.assertEqual(row["single_piece_observations"], 0)
        self.assertEqual(row["distinct_single_piece_orders"], 0)
        self.assertEqual(row["data_confidence"], pot.CONFIDENCE_NO_DATA)
        self.assertEqual(row["recommendation_status"], pot.STATUS_COLLECT)
        self.assertEqual(row["actual_max_length_cm"], "")
        observations = list(csv_rows(pot.observations_csv_path()))
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["single_piece"], "no")
        self.assertEqual(observations[0]["actual_length_cm"], "90")

    def test_two_packages_in_one_order_count_as_one_distinct_order(self):
        order_id = 5100
        self.queue_order(order_id, 40, TARGET_A, quantity=2)
        opportunity = next(row["opportunity_id"] for row in self.opportunities()
                           if row["order_id"] == order_id)
        self.record_single(opportunity, package_number=1)
        self.record_single(opportunity, package_number=2)
        row = self.group_row()
        self.assertEqual(row["single_piece_observations"], 2)
        self.assertEqual(row["distinct_single_piece_orders"], 1)
        self.assertEqual(row["recommendation_status"], pot.STATUS_COLLECT)

    def test_fewer_than_three_orders_says_collect_more_data(self):
        for _ in range(2):
            self.add_measured_order()
        row = self.group_row()
        self.assertEqual(row["recommendation_status"], pot.STATUS_COLLECT)
        self.assertEqual(row["suggested_length_cm"], "")

    def test_three_orders_within_the_estimate_are_supported(self):
        estimate = CATALOG.groups[GROUP_A]
        for _ in range(3):
            self.add_measured_order(
                length=estimate["packed_length_cm"], width=estimate["packed_width_cm"],
                height=estimate["packed_height_cm"], weight=estimate["gross_weight_kg"])
        row = self.group_row()
        self.assertEqual(row["distinct_single_piece_orders"], 3)
        self.assertEqual(row["recommendation_status"], pot.STATUS_SUPPORTED)
        self.assertEqual(row["max_percent_variance"], "0")
        self.assertEqual(row["suggested_gross_weight_kg"], "")
        self.assertIn("REVIEW ONLY", row["recommendation_status"])

    def test_three_orders_over_the_estimate_recommend_a_revision(self):
        for _ in range(2):
            self.add_measured_order(length="20", width="18", height="10", weight="0.9")
        self.add_measured_order(length="31", width="26", height="16", weight="1.1")
        row = self.group_row()
        self.assertEqual(row["recommendation_status"], pot.STATUS_REVISE)
        self.assertEqual(row["actual_max_length_cm"], "31")
        self.assertEqual(row["actual_max_width_cm"], "26")
        self.assertEqual(row["actual_max_height_cm"], "16")
        self.assertEqual(row["actual_max_gross_weight_kg"], "1.1")
        self.assertEqual(row["review_note"], pot.REVIEW_ONLY_BANNER)

    def test_suggested_values_are_observed_maxima_rounded_as_specified(self):
        for _ in range(2):
            self.add_measured_order(length="20", width="18", height="10", weight="0.9")
        self.add_measured_order(length="31", width="26", height="16", weight="1.1")
        row = self.group_row()
        # Observed maxima 31 / 26 / 16 cm and 1.1 kg, rounded UP to the next
        # 5 cm and next 0.5 kg. No margin is added on top.
        self.assertEqual(row["suggested_length_cm"], "35")
        self.assertEqual(row["suggested_width_cm"], "30")
        self.assertEqual(row["suggested_height_cm"], "20")
        self.assertEqual(row["suggested_gross_weight_kg"], "1.5")
        self.assertEqual(pot.dec_str(pot.round_up_to(Decimal("25"), Decimal("5"))), "25")
        self.assertEqual(pot.dec_str(pot.round_up_to(Decimal("25.1"), Decimal("5"))), "30")
        self.assertEqual(pot.dec_str(pot.round_up_to(Decimal("2"), Decimal("0.5"))), "2")
        self.assertEqual(pot.dec_str(pot.round_up_to(Decimal("2.01"), Decimal("0.5"))), "2.5")

    def test_superseded_measurements_do_not_count(self):
        opportunity = self.add_measured_order(length="80", width="70", height="60", weight="9")
        event = self.events()[0]["event_id"]
        pot.run_correct(self.measurement_args(
            opportunity, length="20", width="18", height="10", weight="0.9",
            supersedes=event))
        row = self.group_row()
        self.assertEqual(row["measured_packages"], 1)
        self.assertEqual(row["actual_max_length_cm"], "20")

    def test_report_never_changes_the_estimate_and_never_calls_woocommerce(self):
        self.add_measured_order()
        digest_before = pot.sha256_file(pot.CATALOG_PATH)
        estimate_csv_before = pot.CATALOG_PATH.read_bytes()
        calls_before = len(self.store.calls)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            pot.command_report(argparse.Namespace(json=False))
        self.assertEqual(len(self.store.calls), calls_before)
        self.assertEqual(pot.sha256_file(pot.CATALOG_PATH), digest_before)
        self.assertEqual(pot.CATALOG_PATH.read_bytes(), estimate_csv_before)
        text = stdout.getvalue()
        self.assertIn(pot.REVIEW_ONLY_BANNER, text)
        report = pot.group_report_md_path().read_text(encoding="utf-8")
        self.assertIn(pot.REVIEW_ONLY_BANNER, report)
        self.assertIn("NOT PHYSICALLY VERIFIED - NOT UPS APPROVED", report)

    def test_group_report_covers_every_catalog_group_deterministically(self):
        self.add_measured_order()
        first = pot.group_report_csv_path().read_text(encoding="utf-8")
        rows = list(csv_rows(pot.group_report_csv_path()))
        self.assertEqual(len(rows), 37)
        self.assertEqual([row["group_id"] for row in rows], sorted(CATALOG.groups))
        pot.rebuild_derived_views(CATALOG)
        self.assertEqual(pot.group_report_csv_path().read_text(encoding="utf-8"), first)

    def test_estimate_columns_mirror_the_researched_csv(self):
        row = self.group_row()
        group = CATALOG.groups[GROUP_A]
        self.assertEqual(row["estimate_length_cm"], group["packed_length_cm"])
        self.assertEqual(row["estimate_gross_weight_kg"], group["gross_weight_kg"])
        self.assertEqual(row["estimate_verification_status"],
                         "RESEARCH-BASED ESTIMATE - NOT PHYSICALLY VERIFIED - NOT UPS APPROVED")


def csv_rows(path: Path):
    import csv as _csv

    with path.open("r", encoding="utf-8", newline="") as handle:
        yield from _csv.DictReader(handle)


# ---------------------------------------------------------------------------
# 26-28  monitor wrapper
# ---------------------------------------------------------------------------

class MonitorTests(PackingTestCase):
    def setUp(self):
        super().setUp()
        self.store.add(raw_order(6000, "completed", 0, [target_line(60000, TARGET_A, 1)]))
        self.initialize()

    def run_monitor(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = monitor.run([])
        return code, stdout.getvalue()

    def test_monitor_is_silent_when_nothing_new_is_relevant(self):
        code, output = self.run_monitor()
        self.assertEqual(code, 0)
        self.assertEqual(output, "")

        self.store.add(raw_order(6001, "processing", 3,
                                 [raw_line(60010, 999, 0, 1, "NOT-A-TARGET")]))
        code, output = self.run_monitor()
        self.assertEqual((code, output), (0, ""))

        self.store.add(raw_order(6002, "pending", 4, [target_line(60020, TARGET_A, 1)]))
        code, output = self.run_monitor()
        self.assertEqual(output, "", "a pending order is not yet an opportunity")

    def test_monitor_message_is_bounded_and_safe(self):
        self.store.add(raw_order(6100, "processing", 10, [target_line(61000, TARGET_A, 2)]))
        code, output = self.run_monitor()
        lines = output.strip().splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 1)
        self.assertEqual(
            lines[0],
            f"Packing data opportunity: Woo order 6100 has 2 x {TARGET_A['sku']} ({GROUP_A}). "
            "When packed, send actual package quantity, L x W x H cm, scale weight kg, "
            "packing material, and a photo/document reference.",
        )
        self.assert_no_pii(output, "monitor output")
        self.assertEqual(self.run_monitor()[1], "", "an opportunity is announced once")

    def test_monitor_consolidates_lines_of_one_order(self):
        self.store.add(raw_order(6200, "processing", 12, [
            target_line(62001, TARGET_A, 2),
            target_line(62002, TARGET_B, 1),
        ]))
        _code, output = self.run_monitor()
        lines = output.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn(f"2 x {TARGET_A['sku']}, 1 x {TARGET_B['sku']}", lines[0])
        self.assertIn(f"({GROUP_A}, {GROUP_B})", lines[0])

    def test_monitor_caps_a_flood_at_twelve_lines(self):
        for index in range(20):
            self.store.add(raw_order(6300 + index, "processing", 20 + index,
                                     [target_line(63000 + index, TARGET_A, 1)]))
        _code, output = self.run_monitor()
        lines = output.strip().splitlines()
        self.assertEqual(len(lines), 12)
        self.assertIn("+9 more order(s)", lines[-1])
        self.assert_no_pii(output, "monitor flood output")

    def test_repeated_identical_failures_are_deduped_and_recovery_clears_the_flag(self):
        self.store.fail_list_with = wc.WooError("WooCommerce could not be reached: timed out")
        code, first = self.run_monitor()
        self.assertEqual(code, 0)
        self.assertIn("could not read WooCommerce orders", first)
        self.assertTrue(pot.error_flag_path().exists())

        _code, second = self.run_monitor()
        self.assertEqual(second, "", "an identical repeated failure stays silent")
        self.assertEqual(json.loads(pot.error_flag_path().read_text(encoding="utf-8"))["count"], 2)

        self.store.fail_list_with = None
        self.store.add(raw_order(6400, "processing", 40, [target_line(64000, TARGET_A, 1)]))
        _code, third = self.run_monitor()
        self.assertIn("Packing data opportunity", third)
        self.assertFalse(pot.error_flag_path().exists())

    def test_a_different_failure_speaks_again(self):
        self.store.fail_list_with = wc.WooError("WooCommerce could not be reached: timed out")
        self.run_monitor()
        self.store.fail_list_with = None
        self.store.add(raw_order(6500, "processing", 50,
                                 [target_line(65000, TARGET_A, 1, sku="WRONG")]))
        _code, output = self.run_monitor()
        self.assertIn("could not read WooCommerce orders", output)
        self.assertEqual(
            json.loads(pot.error_flag_path().read_text(encoding="utf-8"))["signature"],
            "sku_mismatch")

    def test_monitor_never_records_a_measurement(self):
        self.store.add(raw_order(6600, "processing", 60, [target_line(66000, TARGET_A, 1)]))
        self.run_monitor()
        self.assertFalse(pot.events_path().exists())
        source = Path(monitor.__file__).read_text(encoding="utf-8")
        self.assertNotIn("run_record", source)
        self.assertNotIn("run_correct", source)


# ---------------------------------------------------------------------------
# 29  weekly reminder
# ---------------------------------------------------------------------------

class WeeklyReminderTests(PackingTestCase):
    def setUp(self):
        super().setUp()
        self.store.add(raw_order(7000, "completed", 0, [target_line(70000, TARGET_A, 1)]))
        self.initialize()

    def run_weekly(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = weekly.run([])
        return code, stdout.getvalue()

    def test_silent_with_nothing_outstanding(self):
        self.assertEqual(self.run_weekly(), (0, ""))

    def test_speaks_once_then_dedupes(self):
        self.queue_order(7001, 5, TARGET_A, quantity=1)
        code, first = self.run_weekly()
        self.assertEqual(code, 0)
        self.assertIn("Packing measurements outstanding: 1", first)
        self.assertIn("Woo order 7001", first)
        self.assertLessEqual(len(first.strip().splitlines()), 15)
        self.assert_no_pii(first, "weekly reminder")

        self.assertEqual(self.run_weekly()[1], "", "identical weekly content stays silent")

        self.queue_order(7002, 6, TARGET_B, quantity=1)
        _code, third = self.run_weekly()
        self.assertIn("Packing measurements outstanding: 2", third)

    def test_measured_opportunities_drop_out(self):
        self.queue_order(7010, 8, TARGET_A, quantity=1)
        opportunity = self.opportunities()[0]["opportunity_id"]
        self.record_single(opportunity)
        self.assertEqual(self.run_weekly(), (0, ""))

    def test_flood_is_capped_at_fifteen_lines(self):
        for index in range(30):
            self.queue_order(7100 + index, 20 + index, TARGET_A, quantity=1)
        _code, output = self.run_weekly()
        lines = output.strip().splitlines()
        self.assertLessEqual(len(lines), 15)
        self.assertTrue(any("more. Run:" in line for line in lines))

    def test_ready_groups_are_announced(self):
        for index in range(3):
            order_id = 7200 + index
            self.queue_order(order_id, 60 + index, TARGET_A, quantity=1)
            opportunity = next(row["opportunity_id"] for row in self.opportunities()
                               if row["order_id"] == order_id)
            self.record_single(opportunity)
        _code, output = self.run_weekly()
        self.assertIn("Ready for estimate review", output)
        self.assertIn(GROUP_A, output)
        state = json.loads(pot.weekly_state_path().read_text(encoding="utf-8"))
        self.assertEqual(state["announced_ready_groups"], [GROUP_A])

    def test_weekly_reminder_makes_no_network_call(self):
        self.queue_order(7300, 90, TARGET_A, quantity=1)
        before = len(self.store.calls)
        self.run_weekly()
        self.assertEqual(len(self.store.calls), before)


# ---------------------------------------------------------------------------
# 30  validate
# ---------------------------------------------------------------------------

class ValidateTests(PackingTestCase):
    def setUp(self):
        super().setUp()
        self.store.add(raw_order(8000, "completed", 0, [target_line(80000, TARGET_A, 1)]))
        self.initialize()
        self.queue_order(8001, 5, TARGET_A, quantity=2)
        self.opportunity = self.opportunities()[0]["opportunity_id"]

    def test_clean_data_validates(self):
        self.record_single(self.opportunity)
        result = pot.run_validate(CATALOG)
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["problems"], [])

    def test_malformed_json_is_caught(self):
        with pot.opportunities_path().open("a", encoding="utf-8") as handle:
            handle.write("{not json}\n")
        with self.assertRaises(pot.PackingError):
            pot.run_validate(CATALOG)

    def test_a_tampered_opportunity_identifier_is_caught(self):
        records = self.opportunities()
        records[0]["ordered_quantity"] = "99"
        records[0]["order_id"] = 999999
        pot.opportunities_path().write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in records) + "\n",
            encoding="utf-8")
        result = pot.run_validate(CATALOG)
        self.assertEqual(result["status"], "INVALID")
        self.assertTrue(any("does not match its order" in problem
                            for problem in result["problems"]))

    def test_an_injected_pii_field_is_caught(self):
        records = self.opportunities()
        records[0]["billing"] = {"email": "john.doe@example.com"}
        pot.opportunities_path().write_text(
            json.dumps(records[0], sort_keys=True) + "\n", encoding="utf-8")
        result = pot.run_validate(CATALOG)
        self.assertTrue(any("forbidden field" in problem for problem in result["problems"]))

    def test_a_broken_supersession_chain_is_caught(self):
        first = pot.run_record(self.measurement_args(self.opportunity))
        pot.append_jsonl(pot.events_path(), [
            {**self.events()[0], "event_id": "chain-a", "supersedes_event_id": "ghost-event"},
            {**self.events()[0], "event_id": "chain-b", "supersedes_event_id": first["event_id"]},
            {**self.events()[0], "event_id": "chain-c", "supersedes_event_id": first["event_id"]},
        ])
        result = pot.run_validate(CATALOG)
        self.assertEqual(result["status"], "INVALID")
        problems = " | ".join(result["problems"])
        self.assertIn("supersede unknown event ghost-event", problems)
        self.assertIn("superseded more than once", problems)

    def test_a_quantity_overrun_written_directly_into_the_file_is_caught(self):
        self.record_single(self.opportunity, package_number=1, packed_quantity="1")
        pot.append_jsonl(pot.events_path(), [
            {**self.events()[0], "event_id": "overrun", "package_number": 2,
             "packed_quantity": "9"},
        ])
        result = pot.run_validate(CATALOG)
        self.assertTrue(any("exceeds the ordered quantity" in problem
                            for problem in result["problems"]))

    def test_unsorted_or_impossible_stored_dimensions_are_caught(self):
        self.record_single(self.opportunity)
        pot.append_jsonl(pot.events_path(), [
            {**self.events()[0], "event_id": "unsorted", "package_number": 2,
             "length_cm": "5", "width_cm": "50", "height_cm": "1", "packed_quantity": "1"},
            {**self.events()[0], "event_id": "impossible", "package_number": 3,
             "length_cm": "-4", "width_cm": "-5", "height_cm": "-6", "packed_quantity": "0"},
        ])
        result = pot.run_validate(CATALOG)
        problems = " | ".join(result["problems"])
        self.assertIn("not stored as L >= W >= H", problems)
        self.assertIn("must be greater than zero", problems)

    def test_a_forged_recorder_or_secret_is_caught(self):
        self.record_single(self.opportunity)
        pot.append_jsonl(pot.events_path(), [
            {**self.events()[0], "event_id": "forged", "package_number": 2,
             "recorded_by": "Someone Else", "evidence_ref": "ck_" + "z" * 24},
        ])
        result = pot.run_validate(CATALOG)
        problems = " | ".join(result["problems"])
        self.assertIn("recorded_by is not Rachad Homsi", problems)
        self.assertIn("looks like a credential", problems)

    def test_validate_exits_nonzero_on_problems(self):
        records = self.opportunities()
        records[0]["order_id"] = 424242
        pot.opportunities_path().write_text(
            json.dumps(records[0], sort_keys=True) + "\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
            pot.command_validate(argparse.Namespace(json=False))
        self.assertEqual(caught.exception.code, 1)


# ---------------------------------------------------------------------------
# Locking and CLI surface
# ---------------------------------------------------------------------------

class InfrastructureTests(PackingTestCase):
    def test_the_data_lock_is_reentrant_and_exclusive(self):
        with pot.data_lock():
            with pot.data_lock():
                self.assertTrue(pot.lock_path().exists())
        with pot.data_lock():
            with self.assertRaises(pot.PackingError):
                _hold_lock_from_a_second_handle(timeout=0.2)

    def test_operational_data_never_lands_in_the_repository(self):
        self.assertNotIn("C:\\FRPDepot", str(pot.LOCALAPPDATA / "FRPDepot-Packing-Observations"))
        default_root = pot.LOCALAPPDATA / "FRPDepot-Packing-Observations"
        self.assertFalse(str(default_root).startswith(str(pot.REPO_ROOT)))

    def test_cli_offers_no_write_command(self):
        parser = pot.build_parser()
        actions = [action for action in parser._actions
                   if isinstance(action, argparse._SubParsersAction)]
        commands = set(actions[0].choices)
        self.assertEqual(commands, {"initialize", "scan", "pending", "show", "report",
                                    "record", "correct", "validate"})
        for forbidden in ("stage", "commit", "approve", "deploy", "push", "assign", "delete"):
            self.assertNotIn(forbidden, commands)

    def test_main_reports_errors_without_a_traceback(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = pot.main(["scan"])
        self.assertEqual(code, 1)
        self.assertIn("not initialized", stderr.getvalue())


def _hold_lock_from_a_second_handle(timeout: float):
    """Take the lock as a genuinely separate holder, bypassing re-entrancy."""
    saved = pot._LOCK_DEPTH
    pot._LOCK_DEPTH = 0
    try:
        with pot.data_lock(timeout=timeout):
            pass
    finally:
        pot._LOCK_DEPTH = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
