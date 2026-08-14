"""No-network safety tests for the fixed FNPT parent-2061 catalog cleanup."""
from __future__ import annotations

import argparse
import contextlib
import copy
from datetime import timedelta
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import woocommerce_common as wc  # noqa: E402
import woocommerce_fnpt_catalog_cleanup_tool as tool  # noqa: E402

SECRET = "SENTINEL-PROTECTED-PARENT-DATA-MUST-NOT-LEAK"


def vault() -> dict[str, str]:
    return {
        "site_url": wc.ALLOWED_ORIGIN,
        "consumer_key": "ck_abcdefghijklmnopqrstuvwxyz",
        "consumer_secret": "cs_abcdefghijklmnopqrstuvwxyz",
        "declared_permissions": "read_write",
    }


def attributes_before() -> list[dict]:
    return [
        {
            "id": 0,
            "name": "RESIN TYPE",
            "position": 0,
            "visible": True,
            "variation": True,
            "options": [
                "Derakane 470-300",
                "Derakane 411-350",
                "Hetron 922",
                "Derakane 510 A-40",
            ],
        },
        {
            "id": 0,
            "name": "SIZE",
            "position": 1,
            "visible": True,
            "variation": True,
            "options": ["1/2\"", "3/4\"", "1\"", "6\""],
        },
        {
            "id": 0,
            "name": "Length",
            "position": 2,
            "visible": True,
            "variation": True,
            "options": ["6\"", "8\""],
        },
    ]


def attributes_after() -> list[dict]:
    value = attributes_before()
    value[0]["options"].remove("Hetron 922")
    return value


def live_attributes_before() -> list[dict]:
    value = attributes_before()
    for attribute in value:
        attribute["slug"] = attribute["name"]
    return value


def parent_record(**overrides) -> dict:
    value = {
        "id": 2061,
        "name": "FNPT Coupling, Threaded On both ends",
        "slug": "fnpt-coupling-threaded-on-both-ends",
        "permalink": "https://frpdepots.com/product/fnpt-coupling-threaded-on-both-ends/",
        "date_created": "2026-08-10T12:00:00",
        "date_created_gmt": "2026-08-10T20:00:00",
        "date_modified": "2026-08-13T07:00:00",
        "date_modified_gmt": "2026-08-13T15:00:00",
        "type": "variable",
        "status": "publish",
        "featured": False,
        "catalog_visibility": "visible",
        "description": SECRET,
        "short_description": "Fixed short description",
        "sku": "ZOHO-GROUP-5961888EC5DB",
        "price": "23.40",
        "regular_price": "",
        "sale_price": "",
        "on_sale": False,
        "purchasable": True,
        "total_sales": 0,
        "virtual": False,
        "downloadable": False,
        "downloads": [],
        "download_limit": -1,
        "download_expiry": -1,
        "external_url": "",
        "button_text": "",
        "tax_status": "taxable",
        "tax_class": "",
        "manage_stock": False,
        "stock_quantity": None,
        "backorders": "no",
        "backorders_allowed": False,
        "backordered": False,
        "low_stock_amount": None,
        "sold_individually": False,
        "weight": "",
        "dimensions": {"length": "", "width": "", "height": ""},
        "shipping_required": True,
        "shipping_taxable": True,
        "shipping_class": "",
        "shipping_class_id": 0,
        "reviews_allowed": True,
        "average_rating": "0.00",
        "rating_count": 0,
        "upsell_ids": [],
        "cross_sell_ids": [],
        "parent_id": 0,
        "purchase_note": "",
        "categories": [{"id": 17, "name": "Uncategorized", "slug": "uncategorized"}],
        "tags": [],
        "images": [{"id": 2149, "src": "https://frpdepots.com/a.png", "alt": "FNPT"}],
        "attributes": attributes_before(),
        "default_attributes": [],
        "variations": list(range(2062, 2126)),
        "grouped_products": [],
        "menu_order": 0,
        "price_html": "derived price",
        "related_ids": [1368],
        "meta_data": [{"id": 9001, "key": "_private", "value": SECRET}],
        "yoast_head": "protected SEO",
        "yoast_head_json": {"title": "protected SEO"},
    }
    value.update(overrides)
    return value


def category_record(**overrides) -> dict:
    value = {
        "id": 58,
        "name": "FRP Fittings",
        "slug": "frp-fittings",
        "parent": 0,
        "description": "Existing category",
        "display": "default",
        "image": None,
        "menu_order": 0,
        "count": 0,
    }
    value.update(overrides)
    return value


class FakeStore:
    def __init__(self):
        self.parent = parent_record()
        self.category = category_record()
        self.calls: list[tuple[str, str]] = []
        self.writes: list[tuple[str, str, dict]] = []
        self.fail_write = False
        self.fail_readback = False
        self.corrupt_readback: dict | None = None
        self.assert_lock = None

    def api_get(self, endpoint, params=None, vault=None):
        self.calls.append(("GET", endpoint))
        if endpoint == tool.PARENT_ENDPOINT:
            if self.fail_readback and self.writes:
                raise wc.WooError("readback unavailable", status=503, code="sentinel_read")
            return copy.deepcopy(self.parent), {}
        if endpoint == tool.TARGET_CATEGORY_ENDPOINT:
            return copy.deepcopy(self.category), {}
        raise AssertionError(f"Unexpected GET route: {endpoint}")

    def api_request(self, method, endpoint, params=None, payload=None, vault=None, timeout=60):
        copied = copy.deepcopy(payload)
        self.calls.append((method, endpoint))
        self.writes.append((method, endpoint, copied))
        if self.assert_lock is not None:
            self.assert_lock()
        if self.fail_write:
            raise wc.WooError("write unavailable", status=503, code="sentinel_write")
        if method != "PUT" or endpoint != "/products/2061":
            raise AssertionError(f"Unexpected write route: {method} {endpoint}")
        if set(copied or {}) != {"attributes", "categories"}:
            raise AssertionError(f"Unexpected write fields: {copied}")
        if copied != {
            "attributes": attributes_after(),
            "categories": [{"id": 58}],
        }:
            raise AssertionError("Write payload was not the exact fixed cleanup")
        self.parent["attributes"] = copy.deepcopy(copied["attributes"])
        self.parent["categories"] = [
            {"id": 58, "name": self.category["name"], "slug": self.category["slug"]}
        ]
        self.parent["date_modified"] = "2026-08-13T08:00:00"
        self.parent["date_modified_gmt"] = "2026-08-13T16:00:00"
        if self.corrupt_readback:
            self.parent.update(copy.deepcopy(self.corrupt_readback))
        # Deliberately return a success body that the verifier must not trust.
        return {"id": 2061, "attributes": "UNTRUSTED-PUT-RESPONSE"}, {}

    @contextlib.contextmanager
    def patched(self):
        with (
            mock.patch.object(tool.wc, "api_get", side_effect=self.api_get),
            mock.patch.object(tool.wc, "api_request", side_effect=self.api_request),
            mock.patch.object(tool.wc, "load_vault", return_value=vault()),
        ):
            yield


class PlanFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plan_dir = Path(self.tmp.name) / "plans"
        self.receipts: list[tuple[str, str]] = []
        self.plan_patch = mock.patch.object(tool, "PLAN_DIR", self.plan_dir)
        self.receipt_patch = mock.patch.object(
            tool.wc, "append_receipt",
            side_effect=lambda action, evidence: self.receipts.append((action, evidence)),
        )
        self.plan_patch.start()
        self.receipt_patch.start()
        self.addCleanup(self.plan_patch.stop)
        self.addCleanup(self.receipt_patch.stop)

    def stage(self, store: FakeStore) -> tuple[Path, dict]:
        output = io.StringIO()
        with store.patched(), contextlib.redirect_stdout(output):
            tool.command_stage(argparse.Namespace())
        result = json.loads(output.getvalue())
        return Path(result["plan"]), result

    def commit(self, store: FakeStore, path: Path, approval: str = "APPROVED") -> dict:
        output = io.StringIO()
        with store.patched(), contextlib.redirect_stdout(output):
            tool.command_commit(argparse.Namespace(plan=str(path), approval=approval))
        return json.loads(output.getvalue())

    @staticmethod
    def rehash(path: Path, mutate) -> None:
        value = json.loads(path.read_text(encoding="utf-8"))
        value.pop("sha256")
        mutate(value)
        value["sha256"] = tool.digest_for(value)
        path.write_text(json.dumps(value), encoding="utf-8")


class RouteAndPayloadClosureTests(unittest.TestCase):
    def test_cli_has_only_argument_free_stage_and_fixed_commit(self):
        parser = tool.build_parser()
        self.assertIs(parser.parse_args(["stage"]).func, tool.command_stage)
        self.assertIs(
            parser.parse_args(["commit", "--plan", "p", "--approval", "APPROVED"]).func,
            tool.command_commit,
        )
        for argv in (
            ["stage", "--product-id", "999"],
            ["stage", "--category-id", "57"],
            ["commit", "--plan", "p", "--approval", "APPROVED", "--method", "POST"],
            ["delete"],
        ):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(argv)

    def test_only_two_gets_and_one_fixed_put_route_are_allowed(self):
        for pair in (
            ("GET", "/products/2061"),
            ("GET", "/products/categories/58"),
            ("PUT", "/products/2061"),
        ):
            tool.validate_route(*pair)
        refused = (
            ("PUT", "/products/999"),
            ("POST", "/products/2061"),
            ("DELETE", "/products/2061"),
            ("PUT", "/products/2061/variations/2094"),
            ("GET", "/products/2061/variations"),
            ("PUT", "/products/batch"),
            ("PUT", "/products/categories/58"),
            ("POST", "/products/categories"),
            ("GET", "/orders"),
        )
        for pair in refused:
            with self.subTest(pair=pair), self.assertRaises(tool.CatalogCleanupError):
                tool.validate_route(*pair)

    def test_payload_is_exact_and_preserves_all_other_parent_attributes(self):
        parent = parent_record()
        payload = tool.fixed_payload_from_parent(parent)
        self.assertEqual(payload, {
            "attributes": attributes_after(),
            "categories": [{"id": 58}],
        })
        self.assertEqual(payload["attributes"][1:], parent["attributes"][1:])
        self.assertEqual(payload["attributes"][0], {
            **parent["attributes"][0],
            "options": ["Derakane 470-300", "Derakane 411-350", "Derakane 510 A-40"],
        })
        self.assertEqual(set(payload), {"attributes", "categories"})

    def test_live_edit_context_slug_is_validated_then_omitted_from_put_projection(self):
        parent = parent_record(attributes=live_attributes_before())
        self.assertEqual(tool.fixed_payload_from_parent(parent), {
            "attributes": attributes_after(),
            "categories": [{"id": 58}],
        })
        changed = live_attributes_before()
        changed[0]["slug"] = "unexpected-derived-slug"
        with self.assertRaises(tool.CatalogCleanupError):
            tool.fixed_payload_from_parent(parent_record(attributes=changed))

    def test_parent_before_state_must_be_exact(self):
        mutations = (
            ("id", 2061.0), ("name", "Other"), ("sku", "OTHER"),
            ("type", "simple"), ("status", "draft"),
            ("categories", [{"id": 17}, {"id": 58}]),
            ("categories", [{"id": "17"}]),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value), self.assertRaises(tool.CatalogCleanupError):
                tool.fixed_payload_from_parent(parent_record(**{field: value}))

    def test_resin_option_and_complete_attribute_before_shape_must_be_exact(self):
        bad_values = []
        missing = attributes_before()
        missing[0]["options"].remove("Hetron 922")
        bad_values.append(missing)
        reordered = attributes_before()
        reordered[0]["options"].reverse()
        bad_values.append(reordered)
        duplicate_resin = attributes_before() + [copy.deepcopy(attributes_before()[0])]
        bad_values.append(duplicate_resin)
        open_shape = attributes_before()
        open_shape[0]["unexpected"] = "field"
        bad_values.append(open_shape)
        global_attribute = attributes_before()
        global_attribute[0]["id"] = 3
        bad_values.append(global_attribute)
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(tool.CatalogCleanupError):
                tool.fixed_payload_from_parent(parent_record(attributes=value))

    def test_no_generic_write_constructor_or_child_transport_surface(self):
        source = Path(tool.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count('wc.api_request("PUT", PARENT_ENDPOINT'), 1)
        for token in (
            'wc.api_request("POST"', 'wc.api_request("DELETE"',
            'wc.api_request("PATCH"', 'wc.get_all(', '"/products/batch"',
            '"/products/2061/variations', '"/products/categories"',
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, source)


class StageTests(PlanFixture):
    def test_stage_is_read_only_with_closed_24_hour_sha256_nonce_plan(self):
        store = FakeStore()
        path, result = self.stage(store)
        self.assertEqual(store.writes, [])
        self.assertEqual(store.calls, [
            ("GET", "/products/2061"),
            ("GET", "/products/categories/58"),
        ])
        self.assertFalse(result["external_write_performed"])
        self.assertEqual(result["write_count"], 1)
        self.assertEqual(result["child_writes"], 0)
        self.assertTrue(result["commissioning_is_not_commit_approval"])
        plan = tool.load_plan(path)
        created = tool.datetime.fromisoformat(plan["created_utc"])
        expires = tool.datetime.fromisoformat(plan["expires_utc"])
        self.assertEqual(expires - created, timedelta(hours=24))
        self.assertRegex(plan["nonce"], r"^[0-9a-f]{32}$")
        self.assertRegex(plan["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(plan["parent_before"]["full_fingerprint"],
                         tool.full_fingerprint(parent_record()))
        self.assertEqual(plan["target_category"]["full_fingerprint"],
                         tool.full_fingerprint(category_record()))

    def test_stage_refuses_missing_or_wrong_existing_category(self):
        for category in (category_record(id=57), {"name": "no id"}):
            store = FakeStore()
            store.category = category
            with self.subTest(category=category), self.assertRaises(tool.CatalogCleanupError):
                self.stage(store)
            self.assertEqual(store.writes, [])

    def test_hash_tamper_and_rehashed_semantic_tamper_are_refused(self):
        path, _ = self.stage(FakeStore())
        value = json.loads(path.read_text(encoding="utf-8"))
        value["transaction"]["write_count"] = 2
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(tool.CatalogCleanupError):
            tool.load_plan(path)

        path, _ = self.stage(FakeStore())
        mutations = (
            lambda plan: plan["transaction"]["only_write"].update(endpoint="/products/999"),
            lambda plan: plan["transaction"]["only_write"]["payload"].update(status="draft"),
            lambda plan: plan["transaction"]["only_write"]["payload"]["categories"].__setitem__(0, {"id": 57}),
            lambda plan: plan["transaction"]["only_write"]["payload"]["attributes"][0]["options"].append("Hetron 922"),
            lambda plan: plan.update(extra="open schema"),
        )
        for mutate in mutations:
            fresh, _ = self.stage(FakeStore())
            self.rehash(fresh, mutate)
            with self.subTest(mutate=mutate), self.assertRaises(tool.CatalogCleanupError):
                tool.load_plan(fresh)

    def test_plan_contains_hashes_not_protected_parent_values_or_credentials(self):
        path, result = self.stage(FakeStore())
        text = path.read_text(encoding="utf-8") + json.dumps(result)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(vault()["consumer_key"], text)
        self.assertNotIn(vault()["consumer_secret"], text)


class CommitTests(PlanFixture):
    def test_exact_unpadded_uppercase_approval_precedes_credentials_and_network(self):
        path, _ = self.stage(FakeStore())
        bad = (
            "approved", "Approved", " APPROVED", "APPROVED ", "APPROVED\n",
            "APPROVED.", "ＡＰＰＲＯＶＥＤ", "", None,
        )
        for approval in bad:
            lock = tool.lock_path(path.resolve())
            with (
                self.subTest(approval=approval),
                mock.patch.object(tool.wc, "load_vault") as load,
                mock.patch.object(tool.wc, "api_get") as get,
                self.assertRaises(tool.CatalogCleanupError),
            ):
                tool.command_commit(argparse.Namespace(plan=str(path), approval=approval))
            load.assert_not_called()
            get.assert_not_called()
            self.assertFalse(lock.exists())

    def test_commit_locks_before_one_fixed_put_and_uses_fresh_live_readback(self):
        store = FakeStore()
        path, _ = self.stage(store)
        lock_path = tool.lock_path(path.resolve())
        store.assert_lock = lambda: self.assertTrue(lock_path.exists())
        result = self.commit(store, path)
        self.assertEqual(store.writes, [(
            "PUT", "/products/2061",
            {"attributes": attributes_after(), "categories": [{"id": 58}]},
        )])
        self.assertEqual(store.calls, [
            ("GET", "/products/2061"),
            ("GET", "/products/categories/58"),
            ("GET", "/products/2061"),
            ("GET", "/products/categories/58"),
            ("PUT", "/products/2061"),
            ("GET", "/products/2061"),
        ])
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["writes_completed"], 1)
        self.assertEqual(result["outcome"]["child_writes"], 0)
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")
        self.assertEqual(lock["writes_completed"], 1)

    def test_complete_prelock_parent_fingerprint_refuses_any_drift_before_lock(self):
        mutations = (
            {"description": "drift"},
            {"price": "99.99"},
            {"stock_status": "outofstock"},
            {"images": []},
            {"meta_data": []},
            {"date_modified_gmt": "2026-08-13T15:00:01"},
        )
        for mutation in mutations:
            store = FakeStore()
            path, _ = self.stage(store)
            store.parent.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(tool.CatalogCleanupError):
                self.commit(store, path)
            self.assertEqual(store.writes, [])
            self.assertFalse(tool.lock_path(path.resolve()).exists())

    def test_related_ids_order_is_normalized_but_membership_remains_protected(self):
        first = parent_record(related_ids=[1368, 1397, 1455])
        reordered = parent_record(related_ids=[1455, 1368, 1397])
        changed = parent_record(related_ids=[1368, 1397, 2061])
        self.assertEqual(tool.full_fingerprint(first), tool.full_fingerprint(reordered))
        self.assertEqual(tool.protected_fingerprint(first), tool.protected_fingerprint(reordered))
        self.assertNotEqual(tool.full_fingerprint(first), tool.full_fingerprint(changed))
        self.assertNotEqual(tool.protected_fingerprint(first), tool.protected_fingerprint(changed))

    def test_related_ids_reordering_does_not_block_prelock_or_readback(self):
        store = FakeStore()
        store.parent["related_ids"] = [1368, 1397, 1455]
        path, _ = self.stage(store)
        store.parent["related_ids"] = [1455, 1368, 1397]
        store.corrupt_readback = {"related_ids": [1397, 1455, 1368]}
        result = self.commit(store, path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(len(store.writes), 1)

    def test_related_ids_membership_drift_is_still_refused_before_lock(self):
        store = FakeStore()
        store.parent["related_ids"] = [1368, 1397, 1455]
        path, _ = self.stage(store)
        store.parent["related_ids"] = [1368, 1397, 2061]
        with self.assertRaises(tool.CatalogCleanupError):
            self.commit(store, path)
        self.assertEqual(store.writes, [])
        self.assertFalse(tool.lock_path(path.resolve()).exists())

    def test_target_category_full_fingerprint_drift_is_refused_before_lock(self):
        store = FakeStore()
        path, _ = self.stage(store)
        store.category["name"] = "Changed"
        with self.assertRaises(tool.CatalogCleanupError):
            self.commit(store, path)
        self.assertEqual(store.writes, [])
        self.assertFalse(tool.lock_path(path.resolve()).exists())

    def test_write_failure_is_one_attempt_indeterminate_and_cannot_replay(self):
        store = FakeStore()
        path, _ = self.stage(store)
        store.fail_write = True
        with self.assertRaises(tool.CatalogCleanupError):
            self.commit(store, path)
        self.assertEqual(len(store.writes), 1)
        lock_path = tool.lock_path(path.resolve())
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate")
        self.assertEqual(lock["phase"], "fixed_parent_put")
        self.assertEqual(lock["writes_completed"], 0)
        with (
            mock.patch.object(tool.wc, "load_vault") as load,
            mock.patch.object(tool.wc, "api_get") as get,
            self.assertRaises(tool.CatalogCleanupError),
        ):
            tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        load.assert_not_called()
        get.assert_not_called()
        self.assertEqual(len(store.writes), 1)

    def test_readback_network_failure_is_indeterminate_after_one_write(self):
        store = FakeStore()
        path, _ = self.stage(store)
        store.fail_readback = True
        with self.assertRaises(tool.CatalogCleanupError):
            self.commit(store, path)
        self.assertEqual(len(store.writes), 1)
        lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate")
        self.assertEqual(lock["phase"], "fresh_live_parent_readback")
        self.assertEqual(lock["writes_completed"], 1)

    def test_readback_rejects_protected_field_category_attribute_and_option_drift(self):
        mutations = (
            {"description": "changed by hook"},
            {"status": "draft"},
            {"regular_price": "1.00"},
            {"stock_quantity": 1},
            {"name": "Changed"},
            {"sku": "Changed"},
            {"images": []},
            {"categories": [{"id": 17}]},
            {"attributes": attributes_before()},
        )
        for mutation in mutations:
            store = FakeStore()
            path, _ = self.stage(store)
            store.corrupt_readback = mutation
            with self.subTest(mutation=mutation), self.assertRaises(tool.CatalogCleanupError):
                self.commit(store, path)
            self.assertEqual(len(store.writes), 1)
            lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
            self.assertEqual(lock["status"], "indeterminate")
            self.assertEqual(lock["writes_completed"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
