"""Safety tests for the FRP Depot WooCommerce shipping-policy capability.

Two artifacts are under test:

1. woocommerce_shipping_policy_tool.py -- the approval-gated stage/commit tool
   for the freight shipping class and its explicitly enumerated assignments.
   Plan schema 2 (2026-08-09) added per-field protected diagnostics after the
   first approved assignment plan stopped at /products/1455/variations/1456 with
   an aggregate fingerprint that could not say WHICH field had moved. The tests
   below hold two properties at once: a mismatch must now name the exact field
   and the exact metadata entry, and it must still be refused rather than
   accepted, normalised or retried.

   Plan schema 3 (2026-08-09) added a bounded, read-only convergence wait after
   the schema-2 diagnostic proved the exact cause on
   /products/1455/variations/2056: one metadata entry, id 63040, key
   _wc_gla_sync_status, moving to a transient value and back. The tests here hold
   the harder property: the transient is recognised EXACTLY, waited on within a
   fixed 90-second bound, and is NEVER accepted as success. Only the complete
   staged protected state ends a commit successfully. Nothing in these tests
   actually waits -- the clock and the sleeper are injected and patched.

   Plan schema 4 (2026-08-10) repairs the verifier itself. Schema 3 treated the
   settled value as the only lawful before-state, and that deadlocked every
   resource resting at the pending one -- reproduced live on
   /products/1455/variations/1457, which refused to stage at all. Schema 4 adds a
   SECOND closed baseline for exactly the already-proven pending digest, gated on
   a bounded read-only stability proof at staging, re-proven before the write, and
   allowed exactly two successful endings: the unchanged pending state (success
   immediately) or the same entry settling, confirmed across a fixed bounded
   schedule. The tests below hold both halves: the new path exists and is honest,
   and the settled path is byte-for-byte the behaviour schema 3 proved.

   Plan schema 5 (2026-08-11) repairs the verifier again, and for the same class
   of reason: schema 4 was right about the transition and wrong about its SHAPE.
   It recognised a settlement only when EXACTLY ONE metadata entry moved. On the
   31-target FRP Pipe plan the 22nd target, /products/1455/variations/1476, had its
   single approved PUT land correctly and came back with THREE entries changed --
   `_wc_gla_sync_status` pending -> settled plus `_wc_gla_synced_at` and
   `_wc_gla_sync_hash` -- same ids, same keys, same indexes, same count, same
   order. Schema 4 could only call that a third-party edit; it locked the plan
   indeterminate mid-run over a resource that was in fact exactly right. Schema 5
   adds that one measured shape as a second CLOSED settlement, gated on the same
   pinned status transition, and the tests below hold the hard property: the exact
   triplet is accepted, and every neighbouring shape -- a fourth changed value, a
   status that does not move pending -> settled, a duplicate, a malformed entry, an
   identity or index drift, an add/remove/reorder, the triplet on the wrong
   baseline, and a settled state that will not hold still -- is still refused,
   still locked, still never retried.
2. freight_checkout_guard/ -- the site-side WordPress plugin that blocks checkout.

The checkout-guard scenarios live in freight_checkout_guard/freight_guard_scenarios.json
and are executed against the REAL PHP when a php interpreter is present. When it is
not, they run against a Python reference model of the same decision core, and the
suite says so out loud rather than reporting a PHP pass that never happened. A
separate test compares the reason vocabulary of the two implementations so they
cannot drift apart silently.

Nothing here contacts WooCommerce. Every transport call is mocked.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import woocommerce_common as wc  # noqa: E402
import woocommerce_shipping_policy_tool as policy  # noqa: E402

GUARD_DIR = Path(__file__).resolve().parent / "freight_checkout_guard"
PLUGIN_DIR = GUARD_DIR / "frpdepot-freight-checkout-guard"
PLUGIN_PHP = PLUGIN_DIR / "frpdepot-freight-checkout-guard.php"
PLUGIN_JS = PLUGIN_DIR / "assets" / "frpdepot-freight-notice.js"
PLUGIN_README = PLUGIN_DIR / "readme.txt"
ALLOWLIST_JSON = PLUGIN_DIR / "ups-allowlist.json"
SCENARIOS_PATH = GUARD_DIR / "freight_guard_scenarios.json"
PHP_TEST = GUARD_DIR / "tests" / "test-freight-guard.php"
EXACT_MESSAGE = "Contact us for a freight quote."

# The generic shell WooCommerce rendered instead of the message on 2026-08-09.
GENERIC_CART_ERROR = (
    "There are some issues with the items in your cart. Please go back to the "
    "cart page and resolve these issues before checking out."
)
# The withdrawn release, and the ZIP hash that failed production validation.
WITHDRAWN_VERSION = "1.0.0"
FAILED_ZIP_SHA256 = "4d8396d95baf0907754730e578ad4c41b98908f77992718c41b293434e07fe25"


def _vault() -> dict[str, str]:
    return {
        "site_url": wc.ALLOWED_ORIGIN,
        "consumer_key": "ck_abcdefghijklmnopqrstuvwxyz",
        "consumer_secret": "cs_abcdefghijklmnopqrstuvwxyz",
        "declared_permissions": "read_write",
    }


# ---------------------------------------------------------------------------
# In-memory WooCommerce fake. Records every call so the tests can prove exactly
# one write per target and a genuine fresh read before each one.
# ---------------------------------------------------------------------------
class FakeStore:
    def __init__(self, records: dict[str, dict], classes: list[dict] | None = None):
        self.records = {key: dict(value) for key, value in records.items()}
        self.classes = list(classes or [])
        self.gets: list[str] = []
        self.writes: list[tuple[str, str, dict]] = []
        # Every transport call in order, so a test can prove that target 2 is not
        # started until target 1 has converged.
        self.calls: list[tuple[str, str]] = []
        self.next_class_id = 55
        self.corrupt_on_write: dict[str, dict] | None = None
        self.fail_write_with: Exception | None = None
        # A queue of record patches applied to successive GETs of an endpoint
        # AFTER its write, one per read. This is how a store that settles (or
        # refuses to settle) after a save is expressed.
        self.after_write_gets: dict[str, list[dict]] = {}
        self._queued: dict[str, list[dict]] = {}

    def api_get(self, endpoint, params=None, vault=None):
        self.gets.append(endpoint)
        self.calls.append(("GET", endpoint))
        if endpoint == "/products/shipping_classes":
            slug = (params or {}).get("slug")
            rows = [row for row in self.classes if not slug or row.get("slug") == slug]
            return rows, {}
        if endpoint in self.records:
            queue = self._queued.get(endpoint)
            if queue:
                self.records[endpoint].update(queue.pop(0))
            return dict(self.records[endpoint]), {}
        raise wc.WooError(f"{endpoint} not found", status=404)

    def api_request(self, method, endpoint, params=None, payload=None, vault=None, timeout=60):
        self.writes.append((method, endpoint, dict(payload or {})))
        self.calls.append((method, endpoint))
        if self.fail_write_with is not None:
            raise self.fail_write_with
        if method == "POST" and endpoint == "/products/shipping_classes":
            created = {"id": self.next_class_id, **(payload or {})}
            self.next_class_id += 1
            self.classes.append(created)
            return dict(created), {}
        if method == "PUT" and endpoint in self.records:
            record = self.records[endpoint]
            record.update(payload or {})
            record["date_modified_gmt"] = "2026-08-09T10:00:00"
            if self.corrupt_on_write and endpoint in self.corrupt_on_write:
                record.update(self.corrupt_on_write[endpoint])
            if endpoint in self.after_write_gets:
                self._queued[endpoint] = [dict(patch)
                                          for patch in self.after_write_gets[endpoint]]
            return dict(record), {}
        raise wc.WooError(f"{method} {endpoint} not found", status=404)

    def patch(self):
        return (
            mock.patch.object(policy.wc, "api_get", side_effect=self.api_get),
            mock.patch.object(policy.wc, "api_request", side_effect=self.api_request),
            mock.patch.object(policy.wc, "load_vault", return_value=_vault()),
        )


# Sentinels that must never reach a plan, lock, receipt or error message. They
# stand in for the real thing: a metadata VALUE is where a licence key, an order
# reference or a customer note would live.
SECRET_META_VALUE = "SENTINEL-META-VALUE-MUST-NOT-LEAK"
SECRET_DESCRIPTION = "SENTINEL-DESCRIPTION-MUST-NOT-LEAK"


def meta_entries() -> list[dict]:
    return [
        {"id": 8801, "key": "_wc_facebook_sync_enabled", "value": "yes"},
        {"id": 8802, "key": "_frp_packing_group", "value": SECRET_META_VALUE},
        {"id": 8803, "key": "_yoast_wpseo_focuskw", "value": ["a", {"b": 1}]},
    ]


def product_record(product_id: int, **overrides) -> dict:
    record = {
        "id": product_id, "name": f"Product {product_id}", "sku": f"SKU-{product_id}",
        "type": "variable", "status": "publish", "regular_price": "100.00",
        "price": "100.00", "description": SECRET_DESCRIPTION, "short_description": "s",
        "categories": [{"id": 9}], "attributes": [], "images": [],
        "shipping_class": "", "shipping_class_id": 0, "parent_id": 0,
        "stock_status": "instock", "manage_stock": False, "weight": "1.5",
        "dimensions": {"length": "1", "width": "1", "height": "1"},
        "virtual": False, "downloadable": False, "tax_class": "", "tax_status": "taxable",
        "meta_data": meta_entries(),
        "date_modified_gmt": "2026-08-01T09:00:00",
    }
    record.update(overrides)
    return record


def variation_record(parent_id: int, variation_id: int, **overrides) -> dict:
    record = product_record(variation_id, parent_id=parent_id, type="variation",
                            name=f"Variation {variation_id}")
    record.update(overrides)
    return record


FREIGHT_CLASS_ROW = {"id": 55, "name": policy.FREIGHT_CLASS_NAME,
                     "slug": policy.FREIGHT_CLASS_SLUG}

# The exact Google Listings & Ads entry the 2026-08-09 diagnostic commit proved
# moved on /products/1455/variations/2056: same id, same key, one value flip.
GLA_KEY = "_wc_gla_sync_status"
GLA_ID = 63040
GLA_STAGED = "synced"
GLA_TRANSIENT = "pending"
# Fixed vocabulary the tool is allowed to say out loud. It is contract naming,
# not a value it read back from the store. Every token here is a COMPOUND word:
# none of them is the bare string "pending" or "synced", so stripping them cannot
# also swallow a genuine leak of the live metadata value.
FIXED_VOCABULARY = (
    "gla_sync_pending_to_synced", "pending_not_accepted",
    # Schema 4.
    "pending_baseline", "settled_baseline",
    "exact_staged_pending_state_or_confirmed_settled_state",
    "exact_staged_pending_state", "confirmed_stable_settled_state",
    "pending_settle_confirmation", "gla_pending_stability",
    "pending_stability_schedule_seconds", "pending_stability_max_seconds",
    "pending_settle_confirm_schedule_seconds", "pending_settle_confirm_max_seconds",
    "pending_baseline_value_sha256", "pending_final_requirement",
    "pending_settled_confirmed_targets",
    # Schema 5. `_wc_gla_synced_at` is a WordPress FIELD NAME hard-coded in the
    # tool's source, exactly like `_wc_gla_sync_status`, and metadata_projection
    # deliberately stores key names in clear -- Rachad cannot act on "some entry
    # changed". It is stripped here because it literally contains the substring
    # "synced": without stripping it, the leak checks below could not tell a field
    # name the tool always knew from a VALUE it read back off a record.
    "_wc_gla_synced_at", "_wc_gla_sync_hash",
)


def gla_meta_entries(value: str = GLA_STAGED) -> list[dict]:
    return meta_entries() + [{"id": GLA_ID, "key": GLA_KEY, "value": value}]


# The exact live shape on /products/1455/variations/1476 (SKU PIDN450150PSI411),
# the target schema 4 locked: seven entries, with the Google status flag at index
# 1 between its save stamp at index 0 and its content hash at index 5. The three
# indexes and the two companion ids are the measured ones from the 2026-08-11
# commit lock; the status entry keeps this file's existing GLA_ID so the schema-3
# and schema-4 fixtures stay comparable.
GLA_SYNCED_AT_KEY = "_wc_gla_synced_at"
GLA_SYNC_HASH_KEY = "_wc_gla_sync_hash"
GLA_SYNCED_AT_ID = 45151
GLA_SYNC_HASH_ID = 74838
GLA_VISIBILITY_ID = 45153
# Sentinels, not plausible values: a stamp and a content hash are as much
# "metadata value" as a licence key is, and must never reach a plan or a lock.
SYNCED_AT_BEFORE = "SENTINEL-SYNCED-AT-BEFORE-MUST-NOT-LEAK"
SYNCED_AT_AFTER = "SENTINEL-SYNCED-AT-AFTER-MUST-NOT-LEAK"
SYNC_HASH_BEFORE = "SENTINEL-SYNC-HASH-BEFORE-MUST-NOT-LEAK"
SYNC_HASH_AFTER = "SENTINEL-SYNC-HASH-AFTER-MUST-NOT-LEAK"


def triplet_meta_entries(status: str = GLA_TRANSIENT,
                         synced_at: str = SYNCED_AT_BEFORE,
                         sync_hash: str = SYNC_HASH_BEFORE) -> list[dict]:
    """Seven entries in the store's own order, matching the measured indexes."""
    return (
        [{"id": GLA_SYNCED_AT_ID, "key": GLA_SYNCED_AT_KEY, "value": synced_at},
         {"id": GLA_ID, "key": GLA_KEY, "value": status}]
        + meta_entries()
        + [{"id": GLA_SYNC_HASH_ID, "key": GLA_SYNC_HASH_KEY, "value": sync_hash},
           {"id": GLA_VISIBILITY_ID, "key": "_wc_gla_visibility",
            "value": SECRET_META_VALUE}]
    )


def settled_triplet_meta_entries() -> list[dict]:
    """The readback shape: all three moved value, nothing else moved at all."""
    return triplet_meta_entries(GLA_STAGED, SYNCED_AT_AFTER, SYNC_HASH_AFTER)


def triplet_variation_record(parent_id: int, variation_id: int,
                             status: str = GLA_TRANSIENT, **overrides) -> dict:
    record = variation_record(parent_id, variation_id,
                              meta_data=triplet_meta_entries(status))
    record.update(overrides)
    return record


def gla_product_record(product_id: int, value: str = GLA_STAGED, **overrides) -> dict:
    return product_record(product_id, meta_data=gla_meta_entries(value), **overrides)


def gla_variation_record(parent_id: int, variation_id: int,
                         value: str = GLA_STAGED, **overrides) -> dict:
    return variation_record(parent_id, variation_id,
                            meta_data=gla_meta_entries(value), **overrides)


def strip_fixed_vocabulary(text: str) -> str:
    """Remove the fixed contract tokens so a leak check can be exact.

    `gla_sync_pending_to_synced`, `pending_not_accepted` and the schema-4 baseline
    names are hard-coded vocabulary in the tool's own source. Everything else that
    spells either live value would have to have come from a record, which is the
    leak this guards.

    LONGEST FIRST, deliberately: several tokens contain another one
    (`exact_staged_pending_state` sits inside
    `exact_staged_pending_state_or_confirmed_settled_state`), and stripping the
    short one first would leave an orphan tail that reads like a leak.
    """
    for token in sorted(FIXED_VOCABULARY, key=len, reverse=True):
        text = text.replace(token, "")
    return text


class PlanFixture(unittest.TestCase):
    """Base class that isolates the plan directory and silences receipts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.plan_dir = Path(self.tmp.name) / "plans"
        self.plan_dir.mkdir(parents=True)
        self.receipts: list[tuple[str, str]] = []
        self._patches = [
            mock.patch.object(policy.wc, "append_receipt",
                              lambda action, evidence: self.receipts.append((action, evidence))),
            mock.patch.object(policy, "PLAN_DIR", self.plan_dir),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in reversed(self._patches):
            item.stop()
        self.tmp.cleanup()

    def _input(self, data: dict) -> Path:
        path = Path(self.tmp.name) / "input.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _stage(self, store: FakeStore, data: dict):
        get_patch, request_patch, _ = store.patch()
        path = self._input(data)
        with get_patch, request_patch:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                policy.command_stage(argparse.Namespace(input=str(path)))
        result = json.loads(output.getvalue())
        return Path(result["plan"]), result

    def _commit(self, store: FakeStore, plan_path: Path, approval="APPROVED"):
        get_patch, request_patch, vault_patch = store.patch()
        with get_patch, request_patch, vault_patch:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                policy.command_commit(argparse.Namespace(plan=str(plan_path), approval=approval))
        return json.loads(output.getvalue())

    def _assign_store(self):
        return FakeStore(
            {
                "/products/1423": product_record(1423),
                "/products/1423/variations/1424": variation_record(1423, 1424),
                "/products/1423/variations/1425": variation_record(1423, 1425),
            },
            classes=[dict(FREIGHT_CLASS_ROW)],
        )

    def _assign_input(self, targets=None):
        return {
            "action": "shipping_class_assign",
            "targets": targets or [
                {"kind": "variation", "product_id": 1423, "variation_id": 1424},
                {"kind": "variation", "product_id": 1423, "variation_id": 1425},
            ],
            "sources": {"policy": "shipping_policy_manifest.json 2026-08-09; all FRP Pipe/Manway freight"},
        }

    def _rewrite(self, plan_path: Path, mutate, *, rehash: bool):
        """Edit a staged plan on disk, optionally recomputing its digest."""
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan.pop("sha256")
        mutate(plan)
        plan["sha256"] = policy.digest_for(plan) if rehash else "0" * 64
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def _lock(self, plan_path: Path) -> dict:
        return json.loads(policy.lock_path(plan_path.resolve()).read_text(encoding="utf-8"))

    def _commit_expecting_failure(self, store: FakeStore, plan_path: Path) -> str:
        get_patch, request_patch, vault_patch = store.patch()
        with get_patch, request_patch, vault_patch:
            with self.assertRaises(policy.ShippingPolicyError) as caught:
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        return str(caught.exception)


# ---------------------------------------------------------------------------
# Route, payload and schema closure
# ---------------------------------------------------------------------------
class RouteAndPayloadClosureTests(unittest.TestCase):
    def test_only_three_actions_exist(self):
        self.assertEqual(policy.ACTIONS, {
            "shipping_class_create", "shipping_class_assign", "shipping_class_remove",
        })

    def test_only_allowlisted_method_path_pairs_are_accepted(self):
        for method, endpoint in (
            ("POST", "/products/shipping_classes"),
            ("PUT", "/products/1423"),
            ("PUT", "/products/1423/variations/1424"),
        ):
            policy.validate_write_route(method, endpoint)

    def test_every_other_route_is_refused(self):
        refused = (
            ("DELETE", "/products/1423"),
            ("DELETE", "/products/shipping_classes/55"),
            ("POST", "/products/batch"),
            ("PUT", "/products/batch"),
            ("POST", "/products/shipping_classes/batch"),
            ("PUT", "/products/shipping_classes/55"),
            ("POST", "/products/1423/variations/batch"),
            ("PUT", "/orders/1"),
            ("PUT", "/customers/1"),
            ("PUT", "/coupons/1"),
            ("PUT", "/webhooks/1"),
            ("PUT", "/settings/general/woocommerce_default_country"),
            ("POST", "/shipping/zones/1/methods"),
            ("PUT", "/shipping/zones/1/methods/2"),
            ("POST", "/plugins"),
            ("PUT", "/system_status/tools/1"),
            ("PUT", "/products/1423?force=true"),
            ("PUT", "/products/1423/extra"),
            ("PUT", "/products/0"),
            ("PATCH", "/products/1423"),
            ("GET", "/products/1423"),
        )
        for method, endpoint in refused:
            with self.subTest(method=method, endpoint=endpoint):
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.validate_write_route(method, endpoint)

    def test_transport_itself_refuses_delete(self):
        with self.assertRaises(wc.WooError):
            wc.api_request("DELETE", "/products/1423", vault=_vault())

    def test_tool_source_contains_no_delete_or_batch_route(self):
        source = Path(policy.__file__).read_text(encoding="utf-8")
        body = "\n".join(
            line for line in source.splitlines()
            if not line.strip().startswith("#")
        )
        self.assertNotIn('"DELETE"', body)
        self.assertNotIn("/batch", body)

    def test_class_creation_payload_is_the_fixed_class_only(self):
        policy.validate_payload("shipping_class_create", {
            "name": policy.FREIGHT_CLASS_NAME, "slug": policy.FREIGHT_CLASS_SLUG,
        })
        refused = (
            {"name": "Freight Quote Required"},
            {"slug": "freight-quote-required"},
            {"name": "Anything Else", "slug": "freight-quote-required"},
            {"name": "Freight Quote Required", "slug": "another-slug"},
            {"name": "Freight Quote Required", "slug": "freight-quote-required", "description": "x"},
            {"name": "Freight Quote Required", "slug": "freight-quote-required", "id": 1},
        )
        for payload in refused:
            with self.subTest(payload=payload):
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.validate_payload("shipping_class_create", payload)

    def test_assignment_payload_is_one_field_with_one_fixed_value(self):
        policy.validate_payload("shipping_class_assign",
                                {"shipping_class": policy.FREIGHT_CLASS_SLUG})
        policy.validate_payload("shipping_class_remove", {"shipping_class": ""})
        refused = (
            ("shipping_class_assign", {"shipping_class": "some-other-class"}),
            ("shipping_class_assign", {"shipping_class": ""}),
            ("shipping_class_remove", {"shipping_class": policy.FREIGHT_CLASS_SLUG}),
            ("shipping_class_assign", {"shipping_class": policy.FREIGHT_CLASS_SLUG, "name": "x"}),
            ("shipping_class_assign", {"shipping_class": policy.FREIGHT_CLASS_SLUG, "status": "draft"}),
            ("shipping_class_assign", {"regular_price": "1.00"}),
            ("shipping_class_assign", {"shipping_class_id": 55}),
            ("shipping_class_assign", {}),
            ("shipping_class_assign", "shipping_class=freight"),
        )
        for action, payload in refused:
            with self.subTest(action=action, payload=payload):
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.validate_payload(action, payload)

    def test_target_schema_is_closed(self):
        good = [
            {"kind": "product", "product_id": 1423},
            {"kind": "variation", "product_id": 1423, "variation_id": 1424},
        ]
        self.assertEqual(policy.clean_targets(good), [
            {"kind": "product", "product_id": 1423, "variation_id": 0},
            {"kind": "variation", "product_id": 1423, "variation_id": 1424},
        ])
        refused = (
            [],
            "everything",
            [{"kind": "product"}],
            [{"kind": "product", "product_id": 0}],
            [{"kind": "product", "product_id": -1}],
            [{"kind": "product", "product_id": True}],
            [{"kind": "product", "product_id": 1423, "shipping_class": "x"}],
            [{"kind": "variation", "product_id": 1423}],
            [{"kind": "variation", "product_id": 1423, "variation_id": 1423}],
            [{"kind": "category", "product_id": 1423}],
            [{"kind": "order", "product_id": 1}],
            [{"kind": "product", "product_id": 1423}, {"kind": "product", "product_id": 1423}],
        )
        for value in refused:
            with self.subTest(value=value):
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.clean_targets(value)

    def test_target_count_ceiling(self):
        too_many = [{"kind": "product", "product_id": index}
                    for index in range(1, policy.MAX_TARGETS + 2)]
        with self.assertRaises(policy.ShippingPolicyError):
            policy.clean_targets(too_many)

    def test_checkout_guard_deployment_is_hard_disabled(self):
        with self.assertRaises(policy.ShippingPolicyError) as caught:
            policy.command_deploy_checkout_guard(argparse.Namespace())
        self.assertIn("hard-disabled", str(caught.exception))

    def test_deploy_command_is_reachable_from_the_cli_and_always_refuses(self):
        args = policy.build_parser().parse_args(["deploy-checkout-guard"])
        with self.assertRaises(policy.ShippingPolicyError):
            args.func(args)


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------
class StageTests(PlanFixture):
    def test_stage_writes_a_hashed_expiring_plan_with_no_write(self):
        store = self._assign_store()
        plan_path, result = self._stage(store, self._assign_input())
        self.assertEqual(store.writes, [])
        self.assertEqual(result["status"], "STAGED_NOT_COMMITTED")
        self.assertIs(result["external_write_performed"], False)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        saved = plan.pop("sha256")
        self.assertEqual(saved, policy.digest_for(plan))
        self.assertEqual(len(saved), 64)
        self.assertTrue(plan["nonce"])
        self.assertEqual(plan["origin"], policy.EXACT_ORIGIN)
        expires = datetime.fromisoformat(plan["expires_utc"])
        created = datetime.fromisoformat(plan["created_utc"])
        self.assertEqual(expires - created, timedelta(hours=24))
        self.assertEqual(plan["payload"], {"shipping_class": policy.FREIGHT_CLASS_SLUG})
        self.assertEqual(result["approval"], "APPROVED")

    def test_stage_records_a_fingerprint_for_every_enumerated_target(self):
        store = self._assign_store()
        plan_path, result = self._stage(store, self._assign_input())
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(len(plan["targets"]), 2)
        for target in plan["targets"]:
            self.assertTrue(target["before_stale_fingerprint"])
            self.assertTrue(target["before_protected_fingerprint"])
            self.assertEqual(target["before_shipping_class"], "")
        self.assertEqual(result["target_count"], 2)

    def test_stage_refuses_assignment_when_the_class_does_not_exist(self):
        store = FakeStore({"/products/1423": product_record(1423)}, classes=[])
        with self.assertRaises(policy.ShippingPolicyError):
            self._stage(store, self._assign_input(
                [{"kind": "product", "product_id": 1423}]))
        self.assertEqual(store.writes, [])

    def test_stage_refuses_a_no_op_plan(self):
        store = FakeStore(
            {"/products/1423": product_record(1423, shipping_class=policy.FREIGHT_CLASS_SLUG)},
            classes=[dict(FREIGHT_CLASS_ROW)],
        )
        with self.assertRaises(policy.ShippingPolicyError):
            self._stage(store, self._assign_input([{"kind": "product", "product_id": 1423}]))

    def test_stage_refuses_a_variation_that_is_not_a_child_of_the_named_parent(self):
        store = FakeStore(
            {"/products/1423/variations/1424": variation_record(9999, 1424)},
            classes=[dict(FREIGHT_CLASS_ROW)],
        )
        with self.assertRaises(policy.ShippingPolicyError):
            self._stage(store, self._assign_input(
                [{"kind": "variation", "product_id": 1423, "variation_id": 1424}]))
        self.assertEqual(store.writes, [])

    def test_stage_requires_a_policy_source(self):
        store = self._assign_store()
        for sources in ({}, {"policy": ""}, {"policy": "   "}):
            with self.subTest(sources=sources):
                with self.assertRaises(policy.ShippingPolicyError):
                    self._stage(store, {**self._assign_input(), "sources": sources})

    def test_stage_refuses_unknown_top_level_fields(self):
        store = self._assign_store()
        with self.assertRaises(policy.ShippingPolicyError):
            self._stage(store, {**self._assign_input(), "shipping_class": "anything"})

    def test_class_creation_stage_refuses_targets_and_an_existing_class(self):
        store = FakeStore({}, classes=[])
        plan_path, result = self._stage(store, {
            "action": "shipping_class_create",
            "sources": {"policy": "shipping_policy_manifest.json 2026-08-09"},
        })
        self.assertEqual(result["payload"], {
            "name": policy.FREIGHT_CLASS_NAME, "slug": policy.FREIGHT_CLASS_SLUG,
        })
        self.assertEqual(store.writes, [])
        self.assertTrue(plan_path.exists())

        existing = FakeStore({}, classes=[dict(FREIGHT_CLASS_ROW)])
        with self.assertRaises(policy.ShippingPolicyError):
            self._stage(existing, {
                "action": "shipping_class_create",
                "sources": {"policy": "x"},
            })


# ---------------------------------------------------------------------------
# Plan immutability
# ---------------------------------------------------------------------------
class PlanImmutabilityTests(PlanFixture):
    def _staged(self):
        return self._stage(self._assign_store(), self._assign_input())

    def test_any_tamper_without_rehash_fails_the_hash_check(self):
        mutations = (
            lambda plan: plan["payload"].__setitem__("shipping_class", "other-class"),
            lambda plan: plan.__setitem__("method", "DELETE"),
            lambda plan: plan.__setitem__("action", "shipping_class_remove"),
            lambda plan: plan["targets"][0].__setitem__("product_id", 99),
            lambda plan: plan.__setitem__("origin", "https://evil.example:443"),
            lambda plan: plan.__setitem__("shipping_class_slug", "other-slug"),
            lambda plan: plan.__setitem__("nonce", "0" * 32),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                plan_path, _ = self._staged()
                self._rewrite(plan_path, mutate, rehash=False)
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.load_plan(str(plan_path))

    def test_rehashed_tampering_still_fails_the_semantic_checks(self):
        mutations = (
            lambda plan: plan["payload"].__setitem__("shipping_class", "other-class"),
            lambda plan: plan["payload"].__setitem__("regular_price", "0.00"),
            lambda plan: plan.__setitem__("method", "DELETE"),
            lambda plan: plan.__setitem__("method", "POST"),
            lambda plan: plan.__setitem__("action", "order_update"),
            lambda plan: plan.__setitem__("origin", "https://evil.example:443"),
            lambda plan: plan.__setitem__("tool", "Some Other Tool"),
            lambda plan: plan.__setitem__("schema_version", 99),
            lambda plan: plan.__setitem__("shipping_class_slug", "other-slug"),
            lambda plan: plan.__setitem__("shipping_class_name", "Other Name"),
            lambda plan: plan["targets"][0].__setitem__("kind", "order"),
            lambda plan: plan["targets"][0].__setitem__("product_id", 0),
            lambda plan: plan["targets"].__setitem__(1, plan["targets"][0]),
            lambda plan: plan["targets"][0].pop("before_protected_fingerprint"),
            lambda plan: plan["targets"][0].__setitem__("extra", "x"),
            lambda plan: plan.__setitem__("targets", []),
            # -- schema 2 additions ---------------------------------------
            lambda plan: plan.__setitem__("schema_version", 1),
            lambda plan: plan.__setitem__("tool_version", "1.0.0"),
            lambda plan: plan.pop("tool_version"),
            lambda plan: plan["targets"][0].pop("before_protected_field_fingerprints"),
            lambda plan: plan["targets"][0].pop("before_meta_data_projection"),
            lambda plan: plan["targets"][0]["before_protected_field_fingerprints"].pop("weight"),
            lambda plan: plan["targets"][0]["before_protected_field_fingerprints"].__setitem__(
                "shipping_class", "0" * 64),
            lambda plan: plan["targets"][0]["before_protected_field_fingerprints"].__setitem__(
                "weight", "0" * 63),
            lambda plan: plan["targets"][0]["before_protected_field_fingerprints"].__setitem__(
                "weight", "A" * 64),
            lambda plan: plan["targets"][0]["before_protected_field_fingerprints"].__setitem__(
                "weight", None),
            lambda plan: plan["targets"][0].__setitem__(
                "before_protected_field_fingerprints", {}),
            lambda plan: plan["targets"][0].__setitem__("before_protected_fingerprint", "0" * 63),
            lambda plan: plan["targets"][0].__setitem__("before_meta_data_projection", {}),
            lambda plan: plan["targets"][0]["before_meta_data_projection"].reverse(),
            lambda plan: plan["targets"][0]["before_meta_data_projection"][0].__setitem__(
                "extra", "x"),
            lambda plan: plan["targets"][0]["before_meta_data_projection"][0].pop("entry_sha256"),
            lambda plan: plan["targets"][0]["before_meta_data_projection"][0].__setitem__(
                "value_sha256", "0" * 10),
            lambda plan: plan["targets"][0]["before_meta_data_projection"][0].__setitem__(
                "shape", "entry-ish"),
            lambda plan: plan["targets"][0]["before_meta_data_projection"][0].__setitem__(
                "key", None),
            lambda plan: plan["targets"][0]["before_meta_data_projection"][0].__setitem__(
                "id", -1),
            lambda plan: plan["targets"][0]["before_meta_data_projection"].pop(0),
            # -- schema 3 additions ---------------------------------------
            lambda plan: plan.__setitem__("schema_version", 2),
            lambda plan: plan.__setitem__("tool_version", "2.0.0"),
            lambda plan: plan.pop("convergence_contract"),
            lambda plan: plan.__setitem__("convergence_contract", {}),
            lambda plan: plan.__setitem__("convergence_contract", "gla"),
            lambda plan: plan["convergence_contract"].__setitem__("extra", "x"),
            lambda plan: plan["convergence_contract"].pop("kind"),
            lambda plan: plan["convergence_contract"].__setitem__("kind", "anything_else"),
            lambda plan: plan["convergence_contract"].__setitem__("meta_key", "_other_key"),
            lambda plan: plan["convergence_contract"].__setitem__(
                "staged_value_sha256", "0" * 64),
            lambda plan: plan["convergence_contract"].__setitem__(
                "transient_value_sha256", "0" * 64),
            lambda plan: plan["convergence_contract"].__setitem__(
                "staged_value_sha256", "0" * 63),
            # A reordered schedule is not the schedule.
            lambda plan: plan["convergence_contract"].__setitem__(
                "schedule_seconds", [30, 30, 16, 8, 4, 2]),
            # 2.0 is not 2, even though Python says they are equal.
            lambda plan: plan["convergence_contract"].__setitem__(
                "schedule_seconds", [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]),
            lambda plan: plan["convergence_contract"].__setitem__(
                "schedule_seconds", [2, 4, 8, 16, 30]),
            lambda plan: plan["convergence_contract"].__setitem__(
                "schedule_seconds", [2, 4, 8, 16, 30, 30, 30]),
            # A longer wait than the fixed ceiling.
            lambda plan: plan["convergence_contract"].update(
                {"schedule_seconds": [600, 600], "max_seconds": 1200}),
            lambda plan: plan["convergence_contract"].__setitem__("max_seconds", 3600),
            lambda plan: plan["convergence_contract"].__setitem__("max_seconds", 90.0),
            lambda plan: plan["convergence_contract"].__setitem__(
                "final_requirement", "accept_the_transient_state"),
            lambda plan: plan["convergence_contract"].__setitem__(
                "allowed_changed_protected_fields_during_wait", ["meta_data", "price"]),
            lambda plan: plan["convergence_contract"].__setitem__(
                "allowed_changed_protected_fields_during_wait", []),
            # Eligibility is re-derived from the projection, never trusted.
            lambda plan: plan["targets"][0].__setitem__("gla_convergence_eligible", True),
            lambda plan: plan["targets"][0].__setitem__("gla_convergence_eligible", "yes"),
            lambda plan: plan["targets"][0].__setitem__("gla_convergence_eligible", 1),
            lambda plan: plan["targets"][0].pop("gla_convergence_eligible"),
            # -- schema 4 additions ---------------------------------------
            lambda plan: plan.__setitem__("schema_version", 3),
            lambda plan: plan.__setitem__("tool_version", "3.0.0"),
            lambda plan: plan["convergence_contract"].pop("baseline_modes"),
            lambda plan: plan["convergence_contract"].pop("pending_final_requirement"),
            lambda plan: plan["convergence_contract"].__setitem__(
                "baseline_modes", ["absent"]),
            lambda plan: plan["convergence_contract"].__setitem__(
                "pending_baseline_value_sha256", "0" * 64),
            lambda plan: plan["convergence_contract"].__setitem__(
                "settled_baseline_value_sha256", "0" * 63),
            lambda plan: plan["convergence_contract"].update(
                {"pending_stability_schedule_seconds": [600, 600],
                 "pending_stability_max_seconds": 1200}),
            lambda plan: plan["convergence_contract"].__setitem__(
                "pending_stability_max_seconds", 60),
            lambda plan: plan["convergence_contract"].update(
                {"pending_settle_confirm_schedule_seconds": [600, 600],
                 "pending_settle_confirm_max_seconds": 1200}),
            lambda plan: plan["convergence_contract"].__setitem__(
                "pending_final_requirement", "accept_anything"),
            # The baseline mode is re-derived from the projection, never trusted.
            lambda plan: plan["targets"][0].pop("gla_baseline_mode"),
            lambda plan: plan["targets"][0].pop("gla_pending_stability"),
            lambda plan: plan["targets"][0].__setitem__(
                "gla_baseline_mode", "pending_baseline"),
            lambda plan: plan["targets"][0].__setitem__(
                "gla_baseline_mode", "settled_baseline"),
            lambda plan: plan["targets"][0].__setitem__("gla_baseline_mode", "anything"),
            lambda plan: plan["targets"][0].__setitem__("gla_baseline_mode", None),
            lambda plan: plan["targets"][0].__setitem__(
                "gla_pending_stability", {"stable": True}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                plan_path, _ = self._staged()
                self._rewrite(plan_path, mutate, rehash=True)
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.load_plan(str(plan_path))

    def test_expired_plan_is_refused_even_with_a_valid_hash(self):
        plan_path, _ = self._staged()
        self._rewrite(
            plan_path,
            lambda plan: plan.__setitem__(
                "expires_utc",
                (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            ),
            rehash=True,
        )
        with self.assertRaises(policy.ShippingPolicyError):
            policy.load_plan(str(plan_path))

    def test_a_plan_outside_the_plan_folder_is_refused(self):
        plan_path, _ = self._staged()
        stray = Path(self.tmp.name) / "stray.json"
        stray.write_text(plan_path.read_text(encoding="utf-8"), encoding="utf-8")
        with mock.patch.object(policy.wc, "load_vault") as load:
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(plan=str(stray), approval="APPROVED"))
            load.assert_not_called()

    def test_class_creation_plan_cannot_gain_targets(self):
        store = FakeStore({}, classes=[])
        plan_path, _ = self._stage(store, {
            "action": "shipping_class_create", "sources": {"policy": "manifest"},
        })
        self._rewrite(
            plan_path,
            lambda plan: plan.__setitem__("targets", [
                {"kind": "product", "product_id": 1423, "variation_id": 0,
                 "before_shipping_class": "", "before_stale_fingerprint": "x",
                 "before_protected_fingerprint": "y", "before_date_modified_gmt": "z"},
            ]),
            rehash=True,
        )
        with self.assertRaises(policy.ShippingPolicyError):
            policy.load_plan(str(plan_path))


# ---------------------------------------------------------------------------
# Approval, locking and commit
# ---------------------------------------------------------------------------
class CommitTests(PlanFixture):
    def test_wrong_approval_is_refused_before_any_network_access(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        store.gets.clear()
        for approval in (
            # 2026-08-21 (A1): a plain yes now counts. What still refuses before
            # any network access is a condition, a question, a blank, a bare slug,
            # a multi-line value, or something that is not a string at all.
            "yes but not the 8 inch", "wait", "APPROVED?", "", "APPROVED\nnow",
            policy.FREIGHT_CLASS_SLUG, "hold on", "not yet",
            # Not even a string.
            None, 123, True, ["APPROVED"],
        ):
            with self.subTest(approval=approval):
                with mock.patch.object(policy.wc, "load_vault") as load, \
                     mock.patch.object(policy.wc, "api_get") as get, \
                     mock.patch.object(policy.wc, "api_request") as write:
                    with self.assertRaises(policy.ShippingPolicyError):
                        policy.command_commit(argparse.Namespace(
                            plan=str(plan_path), approval=approval))
                    load.assert_not_called()
                    get.assert_not_called()
                    write.assert_not_called()
        self.assertFalse(policy.lock_path(plan_path.resolve()).exists())

    def test_approved_commit_writes_once_per_target_and_reads_back(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        result = self._commit(store, plan_path)

        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertTrue(result["replay_locked"])
        self.assertEqual(store.writes, [
            ("PUT", "/products/1423/variations/1424",
             {"shipping_class": policy.FREIGHT_CLASS_SLUG}),
            ("PUT", "/products/1423/variations/1425",
             {"shipping_class": policy.FREIGHT_CLASS_SLUG}),
        ])
        for endpoint in ("/products/1423/variations/1424", "/products/1423/variations/1425"):
            self.assertEqual(store.records[endpoint]["shipping_class"],
                             policy.FREIGHT_CLASS_SLUG)
        lock = json.loads(policy.lock_path(plan_path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")

    def test_commit_protects_every_other_product_field(self):
        store = self._assign_store()
        before = {key: dict(value) for key, value in store.records.items()}
        plan_path, _ = self._stage(store, self._assign_input())
        self._commit(store, plan_path)
        for endpoint, original in before.items():
            after = store.records[endpoint]
            for field in policy.PROTECTED_FIELDS:
                with self.subTest(endpoint=endpoint, field=field):
                    self.assertEqual(after.get(field), original.get(field))

    def test_commit_aborts_when_the_store_silently_changes_a_protected_field(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        store.corrupt_on_write = {
            "/products/1423/variations/1424": {"regular_price": "0.01"},
        }
        get_patch, request_patch, vault_patch = store.patch()
        with get_patch, request_patch, vault_patch:
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        self.assertEqual(len(store.writes), 1)
        lock = json.loads(policy.lock_path(plan_path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate_needs_restage")

    def test_stale_record_blocks_the_write_entirely(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        store.records["/products/1423/variations/1424"]["name"] = "Renamed elsewhere"
        get_patch, request_patch, vault_patch = store.patch()
        with get_patch, request_patch, vault_patch:
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        self.assertEqual(store.writes, [])

    def test_a_moved_modification_stamp_blocks_the_write(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        store.records["/products/1423/variations/1424"]["date_modified_gmt"] = "2026-08-02T09:00:00"
        get_patch, request_patch, vault_patch = store.patch()
        with get_patch, request_patch, vault_patch:
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        self.assertEqual(store.writes, [])

    def test_a_committed_plan_can_never_be_replayed(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        self._commit(store, plan_path)
        store.writes.clear()
        get_patch, request_patch, vault_patch = store.patch()
        with get_patch, request_patch, vault_patch:
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        self.assertEqual(store.writes, [])

    def test_an_indeterminate_plan_needs_restage_and_the_same_plan_is_not_retried(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        store.fail_write_with = wc.WooError("gateway timeout", status=504)
        get_patch, request_patch, vault_patch = store.patch()
        with get_patch, request_patch, vault_patch:
            with self.assertRaises(policy.ShippingPolicyError) as caught:
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        self.assertIn("re-stage", str(caught.exception).casefold())
        self.assertNotIn("permanent", str(caught.exception).casefold())
        self.assertEqual(len(store.writes), 1)
        lock = json.loads(policy.lock_path(plan_path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertFalse(lock["permanent_lock"])

        store.fail_write_with = None
        store.writes.clear()
        get_patch, request_patch, vault_patch = store.patch()
        with get_patch, request_patch, vault_patch:
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        self.assertEqual(store.writes, [])

    def test_the_lock_exists_before_the_first_write_reaches_the_store(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        lock = policy.lock_path(plan_path.resolve())
        seen: list[bool] = []
        original = store.api_request

        def observing_request(*args, **kwargs):
            seen.append(lock.exists())
            return original(*args, **kwargs)

        with mock.patch.object(policy.wc, "api_get", side_effect=store.api_get), \
             mock.patch.object(policy.wc, "api_request", side_effect=observing_request), \
             mock.patch.object(policy.wc, "load_vault", return_value=_vault()):
            with contextlib.redirect_stdout(io.StringIO()):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        self.assertTrue(seen)
        self.assertTrue(all(seen))

    def test_commit_refuses_a_key_that_is_not_declared_read_write(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        weak = {**_vault(), "declared_permissions": "read"}
        with mock.patch.object(policy.wc, "load_vault", return_value=weak), \
             mock.patch.object(policy.wc, "api_get", side_effect=store.api_get), \
             mock.patch.object(policy.wc, "api_request", side_effect=store.api_request):
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        self.assertEqual(store.writes, [])

    def test_commit_refuses_a_vault_pointing_at_another_origin(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        elsewhere = {**_vault(), "site_url": "https://evil.example"}
        with mock.patch.object(policy.wc, "load_vault", return_value=elsewhere), \
             mock.patch.object(policy.wc, "api_get", side_effect=store.api_get), \
             mock.patch.object(policy.wc, "api_request", side_effect=store.api_request):
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        self.assertEqual(store.writes, [])

    def test_class_creation_commits_once_and_verifies_name_and_slug(self):
        store = FakeStore({}, classes=[])
        plan_path, _ = self._stage(store, {
            "action": "shipping_class_create", "sources": {"policy": "manifest 2026-08-09"},
        })
        result = self._commit(store, plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(store.writes, [
            ("POST", "/products/shipping_classes",
             {"name": policy.FREIGHT_CLASS_NAME, "slug": policy.FREIGHT_CLASS_SLUG}),
        ])
        self.assertEqual(result["outcome"]["name"], "Freight Quote Required")
        self.assertEqual(result["outcome"]["slug"], "freight-quote-required")

    def test_class_creation_aborts_when_the_store_returns_a_different_slug(self):
        store = FakeStore({}, classes=[])
        plan_path, _ = self._stage(store, {
            "action": "shipping_class_create", "sources": {"policy": "manifest"},
        })

        def slug_drifting_request(method, endpoint, params=None, payload=None,
                                  vault=None, timeout=60):
            store.writes.append((method, endpoint, dict(payload or {})))
            created = {"id": 55, "name": policy.FREIGHT_CLASS_NAME,
                       "slug": "freight-quote-required-2"}
            store.classes.append(created)
            return dict(created), {}

        with mock.patch.object(policy.wc, "api_get", side_effect=store.api_get), \
             mock.patch.object(policy.wc, "api_request", side_effect=slug_drifting_request), \
             mock.patch.object(policy.wc, "load_vault", return_value=_vault()):
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        lock = json.loads(policy.lock_path(plan_path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate_needs_restage")

    def test_removal_plan_clears_only_the_shipping_class(self):
        store = FakeStore(
            {"/products/1423": product_record(1423, shipping_class=policy.FREIGHT_CLASS_SLUG)},
            classes=[dict(FREIGHT_CLASS_ROW)],
        )
        before = dict(store.records["/products/1423"])
        plan_path, _ = self._stage(store, {
            "action": "shipping_class_remove",
            "targets": [{"kind": "product", "product_id": 1423}],
            "sources": {"policy": "Rachad's rollback instruction"},
        })
        result = self._commit(store, plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(store.writes, [("PUT", "/products/1423", {"shipping_class": ""})])
        self.assertEqual(store.records["/products/1423"]["shipping_class"], "")
        for field in policy.PROTECTED_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(store.records["/products/1423"].get(field), before.get(field))


# ---------------------------------------------------------------------------
# Schema 2: per-field protected diagnostics
#
# Schema 1 stored ONE aggregate fingerprint, so the 2026-08-09 failure on
# /products/1455/variations/1456 could say only "a protected field changed".
# These tests hold the property that made that unanswerable: a mismatch must now
# name the exact field, and the exact metadata entry, without ever recording a
# raw product value.
# ---------------------------------------------------------------------------
HEX64 = re.compile(r"[0-9a-f]{64}")


class PerFieldFingerprintTests(unittest.TestCase):
    def test_one_full_digest_per_protected_field_and_nothing_else(self):
        hashes = policy.protected_field_fingerprints(product_record(1423))
        self.assertEqual(set(hashes), set(policy.PROTECTED_FIELDS))
        self.assertEqual(len(hashes), len(policy.PROTECTED_FIELDS))
        for field, digest in hashes.items():
            with self.subTest(field=field):
                self.assertEqual(len(digest), 64)
                self.assertRegex(digest, HEX64)

    def test_the_mapping_is_deterministic_and_holds_no_raw_value(self):
        record = product_record(1423)
        self.assertEqual(policy.protected_field_fingerprints(record),
                         policy.protected_field_fingerprints(dict(record)))
        text = json.dumps(policy.protected_field_fingerprints(record))
        for leak in (SECRET_META_VALUE, SECRET_DESCRIPTION, "SKU-1423", "100.00", "1.5"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, text)

    def test_exactly_the_moved_field_changes_its_own_digest(self):
        before = policy.protected_field_fingerprints(product_record(1423))
        after = policy.protected_field_fingerprints(product_record(1423, weight="9.9"))
        moved = sorted(field for field in policy.PROTECTED_FIELDS
                       if before[field] != after[field])
        self.assertEqual(moved, ["weight"])

    def test_the_shipping_class_itself_is_not_protected(self):
        self.assertNotIn("shipping_class", policy.PROTECTED_FIELDS)
        self.assertNotIn("shipping_class_id", policy.PROTECTED_FIELDS)

    def test_a_missing_extra_or_malformed_mapping_is_refused(self):
        good = policy.protected_field_fingerprints(product_record(1423))
        policy.validate_field_fingerprints(good, "target")
        refused = (
            {},
            {**good, "shipping_class": "0" * 64},
            {key: value for key, value in good.items() if key != "weight"},
            {**good, "weight": "0" * 63},
            {**good, "weight": "0" * 65},
            {**good, "weight": good["weight"].upper()},
            {**good, "weight": None},
            {**good, "weight": 0},
            {**good, "weight": " " + good["weight"][:-1]},
            [good],
            "0" * 64,
        )
        for value in refused:
            with self.subTest(value=str(value)[:60]):
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.validate_field_fingerprints(value, "target")


class MetadataProjectionTests(unittest.TestCase):
    def test_the_projection_is_closed_ordered_and_value_free(self):
        rows = policy.metadata_projection(product_record(1423))
        self.assertEqual(len(rows), 3)
        for position, row in enumerate(rows):
            with self.subTest(position=position):
                self.assertEqual(set(row), set(policy.META_PROJECTION_KEYS))
                self.assertEqual(row["index"], position)
                self.assertEqual(row["shape"], "entry")
                for field in ("key_sha256", "value_sha256", "entry_sha256"):
                    self.assertRegex(row[field], HEX64)
        self.assertEqual([row["id"] for row in rows], [8801, 8802, 8803])
        self.assertEqual([row["key"] for row in rows],
                         ["_wc_facebook_sync_enabled", "_frp_packing_group",
                          "_yoast_wpseo_focuskw"])
        self.assertNotIn(SECRET_META_VALUE, json.dumps(rows))
        self.assertNotIn("yes", json.dumps([row["value_sha256"] for row in rows]))

    def test_an_absent_metadata_list_projects_to_nothing(self):
        self.assertEqual(policy.metadata_projection({"id": 1}), [])

    def test_duplicate_entries_stay_separate_rows(self):
        entry = {"id": 8801, "key": "_dup", "value": "one"}
        rows = policy.metadata_projection({"meta_data": [dict(entry), dict(entry)]})
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["index"] for row in rows], [0, 1])
        self.assertEqual(rows[0]["entry_sha256"], rows[1]["entry_sha256"])

    def test_malformed_entries_remain_representable_rather_than_collapsed(self):
        rows = policy.metadata_projection({"meta_data": [
            "not an object",
            {"id": 1, "key": 5, "value": "v"},
            {"id": "not-an-id", "key": "_sound_key", "value": "v"},
            {"id": 2, "key": "ctrl\u0001key", "value": "v"},
            {"key": "_no_id", "value": "v"},
        ]})
        self.assertEqual([row["shape"] for row in rows],
                         ["malformed", "malformed", "malformed", "malformed", "entry"])
        self.assertEqual([row["index"] for row in rows], [0, 1, 2, 3, 4])
        self.assertIsNone(rows[0]["key"])
        self.assertIsNone(rows[1]["key"])
        # A sound key beside an unsound id keeps the key: Rachad still needs to
        # know WHICH field it was.
        self.assertEqual(rows[2]["key"], "_sound_key")
        self.assertIsNone(rows[2]["id"])
        # A non-printable key is withheld but stays identified by its hash.
        self.assertIsNone(rows[3]["key"])
        self.assertRegex(rows[3]["key_sha256"], HEX64)
        # An entry with no id at all is ordinary, not malformed.
        self.assertIsNone(rows[4]["id"])
        self.assertEqual(rows[4]["key"], "_no_id")

    def test_a_boolean_id_is_not_a_numeric_id(self):
        rows = policy.metadata_projection({"meta_data": [
            {"id": True, "key": "_k", "value": "v"}]})
        self.assertIsNone(rows[0]["id"])
        self.assertEqual(rows[0]["shape"], "malformed")

    def test_oversized_or_unreadable_metadata_is_refused_not_truncated(self):
        refused = (
            {"meta_data": "not a list"},
            {"meta_data": {"key": "value"}},
            {"meta_data": [{"id": index, "key": f"_k{index}", "value": "v"}
                           for index in range(policy.MAX_META_ENTRIES + 1)]},
            {"meta_data": [{"id": 1, "key": "_k",
                            "value": "x" * (policy.MAX_META_ENTRY_CHARS + 10)}]},
            {"meta_data": [{"id": 1, "key": "_" * (policy.MAX_META_KEY_CHARS + 1),
                            "value": "v"}]},
        )
        for record in refused:
            with self.subTest(record=str(record)[:60]):
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.metadata_projection(record)

    def test_the_entry_ceiling_itself_is_accepted(self):
        rows = policy.metadata_projection({"meta_data": [
            {"id": index, "key": f"_k{index}", "value": "v"}
            for index in range(policy.MAX_META_ENTRIES)]})
        self.assertEqual(len(rows), policy.MAX_META_ENTRIES)

    def test_a_projection_carried_in_a_plan_is_revalidated(self):
        good = policy.metadata_projection(product_record(1423))
        policy.validate_meta_projection(good, "target")

        def mutated(mutate):
            copy = [dict(row) for row in good]
            mutate(copy)
            return copy

        refused = (
            {"not": "a list"},
            mutated(lambda rows: rows.reverse()),
            mutated(lambda rows: rows.pop(0)),
            mutated(lambda rows: rows[0].__setitem__("extra", "x")),
            mutated(lambda rows: rows[0].pop("entry_sha256")),
            mutated(lambda rows: rows[0].__setitem__("value_sha256", "0" * 10)),
            mutated(lambda rows: rows[0].__setitem__("entry_sha256", None)),
            mutated(lambda rows: rows[0].__setitem__("shape", "whatever")),
            mutated(lambda rows: rows[0].__setitem__("key", None)),
            mutated(lambda rows: rows[0].__setitem__("key", 5)),
            mutated(lambda rows: rows[0].__setitem__(
                "key", "_" * (policy.MAX_META_KEY_CHARS + 1))),
            mutated(lambda rows: rows[0].__setitem__("id", -1)),
            mutated(lambda rows: rows[0].__setitem__("id", "8801")),
            mutated(lambda rows: rows[0].__setitem__("index", 7)),
            [{"index": 0}],
            [{"index": index, "id": None, "key": "_k", "key_sha256": "0" * 64,
              "value_sha256": "0" * 64, "entry_sha256": "0" * 64, "shape": "entry"}
             for index in range(policy.MAX_META_ENTRIES + 1)],
        )
        for value in refused:
            with self.subTest(value=str(value)[:70]):
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.validate_meta_projection(value, "target")


class MetadataDiagnosticTests(unittest.TestCase):
    def _projection(self, entries):
        return policy.metadata_projection({"meta_data": entries})

    def test_an_added_entry_is_named_by_id_and_key_only(self):
        before = self._projection(meta_entries())
        after = self._projection(meta_entries() + [
            {"id": 9001, "key": "_wc_last_edited_by", "value": SECRET_META_VALUE}])
        report = policy.metadata_diagnostic(before, after)
        self.assertEqual(report["added_entry_count"], 1)
        self.assertEqual(report["removed_entry_count"], 0)
        self.assertEqual(report["value_changed_entry_count"], 0)
        added = report["added_entries"][0]
        self.assertEqual(added["id"], 9001)
        self.assertEqual(added["key"], "_wc_last_edited_by")
        self.assertEqual(added["index"], 3)
        self.assertRegex(added["value_sha256"], HEX64)
        self.assertNotIn(SECRET_META_VALUE, json.dumps(report))

    def test_a_removed_entry_is_named(self):
        before = self._projection(meta_entries())
        after = self._projection(meta_entries()[:2])
        report = policy.metadata_diagnostic(before, after)
        self.assertEqual(report["removed_entry_count"], 1)
        self.assertEqual(report["added_entry_count"], 0)
        self.assertEqual(report["removed_entries"][0]["key"], "_yoast_wpseo_focuskw")
        self.assertEqual(report["staged_entry_count"], 3)
        self.assertEqual(report["readback_entry_count"], 2)

    def test_a_value_change_keeps_the_identity_and_reports_both_hashes(self):
        entries = meta_entries()
        changed = [dict(row) for row in entries]
        changed[1]["value"] = "a different packing group"
        before = self._projection(entries)
        after = self._projection(changed)
        report = policy.metadata_diagnostic(before, after)
        self.assertEqual(report["added_entry_count"], 0)
        self.assertEqual(report["removed_entry_count"], 0)
        self.assertEqual(report["value_changed_entry_count"], 1)
        row = report["value_changed_entries"][0]
        self.assertEqual(row["id"], 8802)
        self.assertEqual(row["key"], "_frp_packing_group")
        self.assertIs(row["value_differs"], True)
        self.assertNotEqual(row["staged_value_sha256"], row["readback_value_sha256"])
        self.assertRegex(row["staged_value_sha256"], HEX64)
        self.assertNotIn(SECRET_META_VALUE, json.dumps(report))
        self.assertNotIn("a different packing group", json.dumps(report))

    def test_an_entry_that_moves_without_its_value_moving_is_still_reported(self):
        entries = meta_entries()
        changed = [dict(row) for row in entries]
        changed[0]["display_value"] = "Yes"
        report = policy.metadata_diagnostic(self._projection(entries),
                                            self._projection(changed))
        row = report["value_changed_entries"][0]
        self.assertIs(row["value_differs"], False)
        self.assertNotEqual(row["staged_entry_sha256"], row["readback_entry_sha256"])

    def test_an_id_or_key_change_at_a_position_is_reported(self):
        entries = meta_entries()
        changed = [dict(row) for row in entries]
        changed[0] = {"id": 8801, "key": "_renamed_key", "value": "yes"}
        report = policy.metadata_diagnostic(self._projection(entries),
                                            self._projection(changed))
        self.assertEqual(report["identity_changed_position_count"], 1)
        position = report["identity_changed_positions"][0]
        self.assertEqual(position["index"], 0)
        self.assertEqual(position["staged_key"], "_wc_facebook_sync_enabled")
        self.assertEqual(position["readback_key"], "_renamed_key")
        self.assertNotEqual(position["staged_key_sha256"], position["readback_key_sha256"])

    def test_a_pure_reorder_is_reported_as_an_order_change(self):
        entries = meta_entries()
        report = policy.metadata_diagnostic(self._projection(entries),
                                            self._projection(list(reversed(entries))))
        self.assertIs(report["order_changed"], True)
        self.assertEqual(report["added_entry_count"], 0)
        self.assertEqual(report["removed_entry_count"], 0)

    def test_identical_projections_report_no_change_at_all(self):
        entries = meta_entries()
        report = policy.metadata_diagnostic(self._projection(entries),
                                            self._projection(entries))
        self.assertEqual(report["added_entry_count"], 0)
        self.assertEqual(report["removed_entry_count"], 0)
        self.assertEqual(report["value_changed_entry_count"], 0)
        self.assertEqual(report["identity_changed_position_count"], 0)
        self.assertIs(report["order_changed"], False)

    def test_the_report_is_bounded_and_says_what_it_omitted(self):
        limit = policy.MAX_META_DIAGNOSTIC_ENTRIES
        after = [{"id": 9000 + index, "key": f"_added{index}", "value": "v"}
                 for index in range(limit + 7)]
        report = policy.metadata_diagnostic([], self._projection(after))
        self.assertEqual(report["added_entry_count"], limit + 7)
        self.assertEqual(len(report["added_entries"]), limit)
        self.assertEqual(report["omitted_from_report"]["added"], 7)
        self.assertEqual(report["diagnostic_entry_limit"], limit)

    def test_duplicate_identities_are_paired_in_order_not_collapsed(self):
        entry = {"id": 8801, "key": "_dup", "value": "one"}
        before = self._projection([dict(entry), dict(entry)])
        after = self._projection([dict(entry)])
        report = policy.metadata_diagnostic(before, after)
        self.assertEqual(report["removed_entry_count"], 1)
        # The surplus is taken from the tail so the leading pair still compares.
        self.assertEqual(report["removed_entries"][0]["index"], 1)
        self.assertEqual(report["value_changed_entry_count"], 0)


class SchemaVersionTests(PlanFixture):
    def test_a_staged_plan_declares_schema_five_and_the_tool_version(self):
        store = self._assign_store()
        plan_path, result = self._stage(store, self._assign_input())
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(policy.SCHEMA_VERSION, 5)
        self.assertEqual(policy.TOOL_VERSION, "5.0.0")
        self.assertEqual(plan["schema_version"], 5)
        self.assertEqual(plan["tool_version"], "5.0.0")
        self.assertEqual(result["schema_version"], 5)
        self.assertEqual(result["tool_version"], "5.0.0")

    def test_every_target_carries_the_closed_per_field_and_metadata_evidence(self):
        store = self._assign_store()
        plan_path, result = self._stage(store, self._assign_input())
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        for target in plan["targets"]:
            self.assertEqual(set(target), {
                "kind", "product_id", "variation_id", "before_shipping_class",
                "before_stale_fingerprint", "before_protected_fingerprint",
                "before_date_modified_gmt", "before_protected_field_fingerprints",
                "before_meta_data_projection", "gla_baseline_mode",
                "gla_convergence_eligible", "gla_pending_stability",
            })
            # These fixtures carry no Google-sync entry at all, so neither bounded
            # path can ever apply to them, and there is nothing to prove still.
            self.assertEqual(target["gla_baseline_mode"], "absent")
            self.assertIs(target["gla_convergence_eligible"], False)
            self.assertIsNone(target["gla_pending_stability"])
            fingerprints = target["before_protected_field_fingerprints"]
            self.assertEqual(set(fingerprints), set(policy.PROTECTED_FIELDS))
            for field, digest in fingerprints.items():
                with self.subTest(field=field):
                    self.assertEqual(len(digest), 64)
                    self.assertRegex(digest, HEX64)
            self.assertEqual(len(target["before_protected_fingerprint"]), 64)
            policy.validate_meta_projection(target["before_meta_data_projection"], "t")
            self.assertEqual(len(target["before_meta_data_projection"]), 3)
        for row in result["targets"]:
            self.assertEqual(row["protected_fields_fingerprinted"],
                             len(policy.PROTECTED_FIELDS))
            self.assertEqual(row["meta_data_entries_projected"], 3)
        # The plan is loadable as staged.
        policy.load_plan(str(plan_path))

    def test_a_schema_one_plan_is_refused_before_any_network_even_when_rehashed(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())

        def downgrade(plan):
            plan["schema_version"] = 1
            plan.pop("tool_version")
            for target in plan["targets"]:
                target.pop("before_protected_field_fingerprints")
                target.pop("before_meta_data_projection")

        self._rewrite(plan_path, downgrade, rehash=True)
        with mock.patch.object(policy.wc, "load_vault") as load, \
             mock.patch.object(policy.wc, "api_get") as get, \
             mock.patch.object(policy.wc, "api_request") as write:
            with self.assertRaises(policy.ShippingPolicyError) as caught:
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
            load.assert_not_called()
            get.assert_not_called()
            write.assert_not_called()
        self.assertIn("schema", str(caught.exception).casefold())
        self.assertFalse(policy.lock_path(plan_path.resolve()).exists())

    def test_a_schema_two_plan_is_refused_before_any_network_even_when_rehashed(self):
        """Schema 2 carries no contract and no eligibility, so the wait has no
        evidence to stand on. Rehashing one does not revive it."""
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())

        def downgrade(plan):
            plan["schema_version"] = 2
            plan["tool_version"] = "2.0.0"
            plan.pop("convergence_contract")
            for target in plan["targets"]:
                target.pop("gla_convergence_eligible")
                target.pop("gla_baseline_mode")
                target.pop("gla_pending_stability")

        self._rewrite(plan_path, downgrade, rehash=True)
        with mock.patch.object(policy.wc, "load_vault") as load, \
             mock.patch.object(policy.wc, "api_get") as get, \
             mock.patch.object(policy.wc, "api_request") as write:
            with self.assertRaises(policy.ShippingPolicyError) as caught:
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
            load.assert_not_called()
            get.assert_not_called()
            write.assert_not_called()
        self.assertIn("schema", str(caught.exception).casefold())
        self.assertFalse(policy.lock_path(plan_path.resolve()).exists())

    def test_a_schema_three_plan_is_refused_before_any_network_even_when_rehashed(self):
        """Schema 3 carries no baseline mode and no stability proof, so a pending
        before-state in one could never have been measured still. It is refused
        exactly like schema 1 and 2 -- before the vault, before the network."""
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())

        def downgrade(plan):
            plan["schema_version"] = 3
            plan["tool_version"] = "3.0.0"
            for field in ("baseline_modes", "settled_baseline_value_sha256",
                          "pending_baseline_value_sha256",
                          "pending_stability_schedule_seconds",
                          "pending_stability_max_seconds",
                          "pending_settle_confirm_schedule_seconds",
                          "pending_settle_confirm_max_seconds",
                          "pending_final_requirement"):
                plan["convergence_contract"].pop(field)
            for target in plan["targets"]:
                target.pop("gla_baseline_mode")
                target.pop("gla_pending_stability")

        self._rewrite(plan_path, downgrade, rehash=True)
        with mock.patch.object(policy.wc, "load_vault") as load, \
             mock.patch.object(policy.wc, "api_get") as get, \
             mock.patch.object(policy.wc, "api_request") as write:
            with self.assertRaises(policy.ShippingPolicyError) as caught:
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
            load.assert_not_called()
            get.assert_not_called()
            write.assert_not_called()
        self.assertIn("schema", str(caught.exception).casefold())
        self.assertFalse(policy.lock_path(plan_path.resolve()).exists())

    def test_a_schema_four_plan_is_refused_before_any_network_even_when_rehashed(self):
        """Schema 4's verifier recognised only the narrower settlement shape, so
        the measured three-entry Google settlement locked it indeterminate mid-run.
        A schema-4 plan is refused exactly like 1, 2 and 3 -- before the vault,
        before the network -- and rehashing one does not revive it. This is what
        makes every already-staged schema-4 Manway and Manway Cover plan dead."""
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())

        def downgrade(plan):
            plan["schema_version"] = 4
            plan["tool_version"] = "4.0.0"
            for field in ("settlement_shapes", "settlement_status_meta_key",
                          "settlement_companion_meta_keys",
                          "settlement_max_changed_meta_entries"):
                plan["convergence_contract"].pop(field)

        self._rewrite(plan_path, downgrade, rehash=True)
        with mock.patch.object(policy.wc, "load_vault") as load, \
             mock.patch.object(policy.wc, "api_get") as get, \
             mock.patch.object(policy.wc, "api_request") as write:
            with self.assertRaises(policy.ShippingPolicyError) as caught:
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
            load.assert_not_called()
            get.assert_not_called()
            write.assert_not_called()
        self.assertIn("schema", str(caught.exception).casefold())
        self.assertFalse(policy.lock_path(plan_path.resolve()).exists())

    def test_a_schema_four_plan_is_refused_even_with_the_new_contract_grafted_on(self):
        """Belt and braces: the version bump alone refuses it, and so would the
        contract alone. Neither is load-bearing by itself."""
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        # Version downgraded, contract left entirely current.
        self._rewrite(plan_path, lambda plan: plan.update(
            {"schema_version": 4, "tool_version": "4.0.0"}), rehash=True)
        with mock.patch.object(policy.wc, "load_vault") as load, \
             mock.patch.object(policy.wc, "api_get") as get:
            with self.assertRaises(policy.ShippingPolicyError):
                policy.load_plan(str(plan_path))
            load.assert_not_called()
            get.assert_not_called()
        # Version current, contract downgraded to the schema-4 field set.
        self._rewrite(plan_path, lambda plan: (
            plan.update({"schema_version": 5, "tool_version": "5.0.0"}),
            [plan["convergence_contract"].pop(field) for field in
             ("settlement_shapes", "settlement_status_meta_key",
              "settlement_companion_meta_keys",
              "settlement_max_changed_meta_entries")]), rehash=True)
        with mock.patch.object(policy.wc, "load_vault") as load, \
             mock.patch.object(policy.wc, "api_get") as get:
            with self.assertRaises(policy.ShippingPolicyError) as caught:
                policy.load_plan(str(plan_path))
            load.assert_not_called()
            get.assert_not_called()
        self.assertIn("convergence_contract", str(caught.exception))

    def test_only_the_current_schema_and_tool_version_load(self):
        """Every earlier schema is unusable, and so is a version-only edit."""
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        for schema, tool_version in ((1, "1.0.0"), (2, "2.0.0"), (3, "3.0.0"),
                                     (4, "4.0.0"), (6, "6.0.0"), (5, "4.0.0"),
                                     (4, "5.0.0"), (3, "5.0.0")):
            with self.subTest(schema=schema, tool_version=tool_version):
                self._rewrite(plan_path, lambda plan: plan.update(
                    {"schema_version": schema, "tool_version": tool_version}),
                    rehash=True)
                with mock.patch.object(policy.wc, "load_vault") as load, \
                     mock.patch.object(policy.wc, "api_get") as get:
                    with self.assertRaises(policy.ShippingPolicyError):
                        policy.load_plan(str(plan_path))
                    load.assert_not_called()
                    get.assert_not_called()

    def test_the_prior_indeterminate_lock_is_not_disturbed_by_the_new_schema(self):
        """A schema-1 plan that already holds a lock stays locked AND refused."""
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        self._rewrite(plan_path, lambda plan: plan.__setitem__("schema_version", 1),
                      rehash=True)
        lock = policy.lock_path(plan_path.resolve())
        policy.write_lock(lock, {"plan_sha256": "x", "status": "indeterminate"})
        with mock.patch.object(policy.wc, "load_vault") as load:
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
            load.assert_not_called()
        self.assertEqual(json.loads(lock.read_text(encoding="utf-8"))["status"],
                         "indeterminate")


class PreWriteDiagnosticTests(PlanFixture):
    """Every per-field hash and the whole metadata projection are checked BEFORE
    the PUT. A mismatch there means the plan is stale, and nothing is written."""

    def _staged(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        return store, plan_path

    def test_a_single_per_field_mismatch_refuses_before_any_put(self):
        store, plan_path = self._staged()
        self._rewrite(
            plan_path,
            lambda plan: plan["targets"][0]["before_protected_field_fingerprints"]
            .__setitem__("weight", "1" * 64),
            rehash=True,
        )
        message = self._commit_expecting_failure(store, plan_path)
        self.assertEqual(store.writes, [])
        self.assertIn("pre-write mismatch", message)
        self.assertIn("weight", message)
        lock = self._lock(plan_path)
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertEqual(lock["diagnostic"]["phase"], "pre_write")
        self.assertEqual(lock["diagnostic"]["changed_protected_fields"], ["weight"])

    def test_a_metadata_projection_mismatch_refuses_before_any_put(self):
        store, plan_path = self._staged()
        self._rewrite(
            plan_path,
            lambda plan: plan["targets"][0]["before_meta_data_projection"][1].update(
                {"value_sha256": "2" * 64, "entry_sha256": "3" * 64}),
            rehash=True,
        )
        message = self._commit_expecting_failure(store, plan_path)
        self.assertEqual(store.writes, [])
        self.assertIn("pre-write mismatch", message)
        self.assertIn("meta_data", message)
        diagnostic = self._lock(plan_path)["diagnostic"]
        self.assertEqual(diagnostic["phase"], "pre_write")
        self.assertEqual(diagnostic["meta_data_projection"]["status"], "changed")
        detail = diagnostic["meta_data_projection"]["diagnostic"]
        self.assertEqual(detail["value_changed_entry_count"], 1)
        self.assertEqual(detail["value_changed_entries"][0]["key"], "_frp_packing_group")

    def test_metadata_that_cannot_be_projected_refuses_before_any_put(self):
        store, plan_path = self._staged()
        store.records["/products/1423/variations/1424"]["meta_data"] = "not a list"
        self._commit_expecting_failure(store, plan_path)
        self.assertEqual(store.writes, [])

    def test_a_clean_plan_still_commits_normally(self):
        store, plan_path = self._staged()
        result = self._commit(store, plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(len(store.writes), 2)


class PostWriteDiagnosticTests(PlanFixture):
    def _staged(self, records=None):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        if records:
            store.corrupt_on_write = records
        return store, plan_path

    def test_one_changed_field_is_named_exactly_and_locks_indeterminate(self):
        store, plan_path = self._staged(
            {"/products/1423/variations/1424": {"weight": "99.9"}})
        message = self._commit_expecting_failure(store, plan_path)
        self.assertIn("protected readback mismatch: weight", message)
        self.assertIn("/products/1423/variations/1424", message)
        lock = self._lock(plan_path)
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertEqual(lock["reason_class"], "ProtectedStateMismatch")
        diagnostic = lock["diagnostic"]
        self.assertEqual(diagnostic["phase"], "post_write")
        self.assertEqual(diagnostic["endpoint"], "/products/1423/variations/1424")
        self.assertEqual(diagnostic["changed_protected_fields"], ["weight"])
        self.assertEqual(set(diagnostic["field_fingerprints"]), {"weight"})
        pair = diagnostic["field_fingerprints"]["weight"]
        self.assertNotEqual(pair["staged_sha256"], pair["readback_sha256"])
        self.assertRegex(pair["staged_sha256"], HEX64)
        self.assertRegex(pair["readback_sha256"], HEX64)
        self.assertEqual(diagnostic["meta_data_projection"]["status"], "matched")
        self.assertIsNone(diagnostic["meta_data_projection"]["diagnostic"])

    def test_the_aggregate_fingerprint_is_still_checked_and_reported(self):
        store, plan_path = self._staged(
            {"/products/1423/variations/1424": {"weight": "99.9"}})
        self._commit_expecting_failure(store, plan_path)
        aggregate = self._lock(plan_path)["diagnostic"]["aggregate_protected_fingerprint"]
        self.assertIs(aggregate["matches"], False)
        self.assertRegex(aggregate["staged_sha256"], HEX64)
        self.assertRegex(aggregate["readback_sha256"], HEX64)
        self.assertNotEqual(aggregate["staged_sha256"], aggregate["readback_sha256"])

    def test_several_changed_fields_are_sorted_and_complete(self):
        store, plan_path = self._staged({"/products/1423/variations/1424": {
            "weight": "99.9", "name": "Renamed by a plugin", "tax_class": "reduced",
        }})
        message = self._commit_expecting_failure(store, plan_path)
        self.assertIn("protected readback mismatch: name, tax_class, weight", message)
        diagnostic = self._lock(plan_path)["diagnostic"]
        self.assertEqual(diagnostic["changed_protected_fields"],
                         ["name", "tax_class", "weight"])
        self.assertEqual(set(diagnostic["field_fingerprints"]),
                         {"name", "tax_class", "weight"})

    def test_a_metadata_side_effect_is_attributed_to_the_exact_entry(self):
        """The shape the 2026-08-09 failure most likely had: a save hook that
        appends or rewrites one meta_data entry."""
        appended = meta_entries() + [
            {"id": 9100, "key": "_wc_shipping_class_touched", "value": SECRET_META_VALUE}]
        store, plan_path = self._staged(
            {"/products/1423/variations/1424": {"meta_data": appended}})
        message = self._commit_expecting_failure(store, plan_path)
        self.assertIn("protected readback mismatch: meta_data", message)
        diagnostic = self._lock(plan_path)["diagnostic"]
        self.assertEqual(diagnostic["changed_protected_fields"], ["meta_data"])
        detail = diagnostic["meta_data_projection"]["diagnostic"]
        self.assertEqual(detail["added_entry_count"], 1)
        self.assertEqual(detail["added_entries"][0]["key"], "_wc_shipping_class_touched")
        self.assertEqual(detail["added_entries"][0]["id"], 9100)
        self.assertEqual(detail["removed_entry_count"], 0)
        self.assertEqual(detail["staged_entry_count"], 3)
        self.assertEqual(detail["readback_entry_count"], 4)

    def test_a_metadata_value_rewrite_is_attributed_without_the_value(self):
        rewritten = [dict(row) for row in meta_entries()]
        rewritten[1]["value"] = "REWRITTEN-BY-A-PLUGIN"
        store, plan_path = self._staged(
            {"/products/1423/variations/1424": {"meta_data": rewritten}})
        message = self._commit_expecting_failure(store, plan_path)
        self.assertIn("meta_data", message)
        detail = self._lock(plan_path)["diagnostic"]["meta_data_projection"]["diagnostic"]
        self.assertEqual(detail["value_changed_entry_count"], 1)
        row = detail["value_changed_entries"][0]
        self.assertEqual(row["key"], "_frp_packing_group")
        self.assertIs(row["value_differs"], True)
        text = json.dumps(self._lock(plan_path))
        self.assertNotIn("REWRITTEN-BY-A-PLUGIN", text)
        self.assertNotIn(SECRET_META_VALUE, text)

    def test_a_metadata_reorder_alone_is_still_a_refusal(self):
        store, plan_path = self._staged({"/products/1423/variations/1424": {
            "meta_data": list(reversed(meta_entries()))}})
        self._commit_expecting_failure(store, plan_path)
        detail = self._lock(plan_path)["diagnostic"]["meta_data_projection"]["diagnostic"]
        self.assertIs(detail["order_changed"], True)
        self.assertEqual(detail["added_entry_count"], 0)
        self.assertEqual(detail["removed_entry_count"], 0)

    def test_no_second_target_is_written_after_the_first_mismatch(self):
        store, plan_path = self._staged(
            {"/products/1423/variations/1424": {"weight": "99.9"}})
        self._commit_expecting_failure(store, plan_path)
        self.assertEqual(len(store.writes), 1)
        self.assertEqual(store.writes[0][1], "/products/1423/variations/1424")
        # The second target was never touched.
        self.assertEqual(store.records["/products/1423/variations/1425"]["shipping_class"], "")

    def test_the_mismatch_is_never_accepted_normalised_or_retried(self):
        store, plan_path = self._staged(
            {"/products/1423/variations/1424": {"weight": "99.9"}})
        self._commit_expecting_failure(store, plan_path)
        store.corrupt_on_write = None
        store.writes.clear()
        # Replay is refused outright: the tool does not re-attempt a locked plan,
        # even once the store looks healthy again.
        self._commit_expecting_failure(store, plan_path)
        self.assertEqual(store.writes, [])
        self.assertEqual(self._lock(plan_path)["status"], "indeterminate_needs_restage")

    def test_the_attempted_target_is_never_rolled_back(self):
        store, plan_path = self._staged(
            {"/products/1423/variations/1424": {"weight": "99.9"}})
        self._commit_expecting_failure(store, plan_path)
        # The assignment itself landed. The tool reports and stops; it never
        # writes a compensating change.
        self.assertEqual(store.records["/products/1423/variations/1424"]["shipping_class"],
                         policy.FREIGHT_CLASS_SLUG)
        self.assertEqual(len(store.writes), 1)
        self.assertNotIn("", [write[2].get("shipping_class") for write in store.writes])

    def test_the_receipt_carries_the_bounded_diagnostic_and_nothing_else(self):
        store, plan_path = self._staged(
            {"/products/1423/variations/1424": {"meta_data": meta_entries()[:1]}})
        self._commit_expecting_failure(store, plan_path)
        actions = [action for action, _ in self.receipts]
        self.assertIn("woocommerce_shipping_policy_indeterminate_needs_restage", actions)
        evidence = next(text for action, text in self.receipts
                        if action == "woocommerce_shipping_policy_indeterminate_needs_restage")
        self.assertIn("changed_protected_fields=meta_data", evidence)
        self.assertIn("phase=post_write", evidence)
        self.assertIn("endpoint=/products/1423/variations/1424", evidence)
        self.assertIn("meta_removed=2", evidence)
        self.assertNotIn(SECRET_META_VALUE, evidence)
        self.assertNotIn(SECRET_DESCRIPTION, evidence)


class DiagnosticContainmentTests(PlanFixture):
    """Nothing that leaves this tool may carry a raw value, credential or body."""

    FORBIDDEN = (
        SECRET_META_VALUE, SECRET_DESCRIPTION, "ck_abcdefghijklmnopqrstuvwxyz",
        "cs_abcdefghijklmnopqrstuvwxyz", "Authorization", "Basic ", "SKU-1424",
    )

    def _assert_clean(self, label, text):
        for token in self.FORBIDDEN:
            with self.subTest(label=label, token=token):
                self.assertNotIn(token, text)

    def test_the_plan_file_itself_holds_no_product_content(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        self._assert_clean("plan", plan_path.read_text(encoding="utf-8"))

    def test_a_mismatch_lock_error_and_receipt_hold_no_product_content(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        store.corrupt_on_write = {"/products/1423/variations/1424": {
            "meta_data": meta_entries() + [
                {"id": 9200, "key": "_touched", "value": SECRET_META_VALUE}],
            "description": SECRET_DESCRIPTION + "-CHANGED",
        }}
        message = self._commit_expecting_failure(store, plan_path)
        self._assert_clean("error", message)
        self._assert_clean("lock", json.dumps(self._lock(plan_path)))
        self._assert_clean("receipts", json.dumps(self.receipts))
        # It still says exactly what moved.
        self.assertIn("description, meta_data", message)

    def test_a_transport_failure_never_records_the_response_body(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        store.fail_write_with = wc.WooError(
            "WooCommerce PUT /products/1423/variations/1424 failed with HTTP 400: "
            '{"code":"rest_invalid","message":"' + SECRET_DESCRIPTION + '"}',
            status=400, code="rest_invalid",
        )
        self._commit_expecting_failure(store, plan_path)
        lock = self._lock(plan_path)
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertEqual(lock["reason_class"], "WooError")
        self.assertEqual(lock["http_status"], 400)
        self.assertEqual(lock["rest_code"], "rest_invalid")
        self._assert_clean("lock", json.dumps(lock))
        self._assert_clean("receipts", json.dumps(self.receipts))

    def test_an_unexpected_exception_is_reduced_to_its_class(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        store.fail_write_with = RuntimeError("stack trace with " + SECRET_DESCRIPTION)
        get_patch, request_patch, vault_patch = store.patch()
        with get_patch, request_patch, vault_patch:
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        lock = self._lock(plan_path)
        self.assertEqual(lock["reason_class"], "RuntimeError")
        self._assert_clean("lock", json.dumps(lock))


class DiagnosticScopeTests(PlanFixture):
    """A one-target plan is an ordinary assignment with a narrower blast radius.
    It is NOT a new action, a test-only mutation, or a lower approval bar."""

    def test_a_one_target_plan_is_flagged_as_a_diagnostic_scope(self):
        store = self._assign_store()
        plan_path, result = self._stage(store, self._assign_input(
            [{"kind": "variation", "product_id": 1423, "variation_id": 1424}]))
        self.assertIs(result["diagnostic_scope"], True)
        self.assertEqual(result["target_count"], 1)
        self.assertIn("diagnostic scope", result["scope_note"])
        # Same action, same fixed payload, same approval word. Nothing else moved.
        self.assertEqual(result["action"], "shipping_class_assign")
        self.assertEqual(result["payload"],
                         {"shipping_class": policy.FREIGHT_CLASS_SLUG})
        self.assertEqual(result["approval"], "APPROVED")
        self.assertIs(result["external_write_performed"], False)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["action"], "shipping_class_assign")
        self.assertEqual(plan["payload"], {"shipping_class": policy.FREIGHT_CLASS_SLUG})
        self.assertNotIn("diagnostic_scope", plan)

    def test_a_multi_target_plan_is_not_flagged(self):
        store = self._assign_store()
        _, result = self._stage(store, self._assign_input())
        self.assertIs(result["diagnostic_scope"], False)
        self.assertEqual(result["target_count"], 2)

    def test_a_one_target_plan_still_needs_his_clear_go(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input(
            [{"kind": "variation", "product_id": 1423, "variation_id": 1424}]))
        with mock.patch.object(policy.wc, "load_vault") as load, \
             mock.patch.object(policy.wc, "api_request") as write:
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="approved but wait"))
            load.assert_not_called()
            write.assert_not_called()

    def test_a_one_target_plan_writes_the_ordinary_fixed_payload_once(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input(
            [{"kind": "variation", "product_id": 1423, "variation_id": 1424}]))
        result = self._commit(store, plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(store.writes, [
            ("PUT", "/products/1423/variations/1424",
             {"shipping_class": policy.FREIGHT_CLASS_SLUG}),
        ])
        self.assertEqual(store.records["/products/1423/variations/1425"]["shipping_class"], "")


# ---------------------------------------------------------------------------
# Schema 3: the CLOSED convergence contract
# ---------------------------------------------------------------------------
class ConvergenceContractTests(PlanFixture):
    def test_the_contract_is_exactly_the_proven_incident(self):
        self.assertEqual(policy.convergence_contract(), {
            "kind": "gla_sync_pending_to_synced",
            "meta_key": "_wc_gla_sync_status",
            "staged_value_sha256":
                "bed425acaecb0b9f4dd17f2f763e28b52f027d43feb0265137f49c86bb875c8c",
            "transient_value_sha256":
                "12adac54ac6f7140109391b670b3cbcd51f083d8f5ee62ce26857c794ed67d36",
            "schedule_seconds": [2, 4, 8, 16, 30, 30],
            "max_seconds": 90,
            "final_requirement": "exact_staged_protected_state",
            "allowed_changed_protected_fields_during_wait": ["meta_data"],
            # Schema 4 adds the pending baseline. It introduces NO third digest:
            # the two baselines are the same two values the incident proved.
            "baseline_modes": ["absent", "settled_baseline", "pending_baseline"],
            "settled_baseline_value_sha256":
                "bed425acaecb0b9f4dd17f2f763e28b52f027d43feb0265137f49c86bb875c8c",
            "pending_baseline_value_sha256":
                "12adac54ac6f7140109391b670b3cbcd51f083d8f5ee62ce26857c794ed67d36",
            "pending_stability_schedule_seconds": [2, 4],
            "pending_stability_max_seconds": 6,
            "pending_settle_confirm_schedule_seconds": [2, 4],
            "pending_settle_confirm_max_seconds": 6,
            "pending_final_requirement":
                "exact_staged_pending_state_or_confirmed_settled_state",
            # Schema 5 adds the measured settlement shape. It introduces NO third
            # digest and NO third status value: the gate is still the same one
            # transition between the same two digests the incident proved.
            "settlement_shapes": ["gla_status_only",
                                  "gla_status_with_stamp_and_hash"],
            "settlement_status_meta_key": "_wc_gla_sync_status",
            "settlement_companion_meta_keys": ["_wc_gla_synced_at",
                                               "_wc_gla_sync_hash"],
            "settlement_max_changed_meta_entries": 3,
        })

    def test_the_settlement_shapes_are_closed_and_name_only_three_keys(self):
        self.assertEqual(policy.SETTLEMENT_SHAPES,
                         ("gla_status_only", "gla_status_with_stamp_and_hash"))
        self.assertEqual(policy.GLA_SETTLEMENT_TRIPLET_KEYS,
                         ("_wc_gla_sync_status", "_wc_gla_synced_at",
                          "_wc_gla_sync_hash"))
        self.assertEqual(policy.GLA_SETTLEMENT_COMPANION_KEYS,
                         ("_wc_gla_synced_at", "_wc_gla_sync_hash"))
        self.assertEqual(policy.GLA_SETTLEMENT_TRIPLET_ENTRY_COUNT, 3)
        # The status key is the gate and is never demoted to a companion.
        self.assertNotIn(policy.GLA_META_KEY, policy.GLA_SETTLEMENT_COMPANION_KEYS)
        self.assertEqual(policy.GLA_SETTLEMENT_TRIPLET_KEY_SHA256[policy.GLA_META_KEY],
                         policy.GLA_META_KEY_SHA256)
        for key, digest in policy.GLA_SETTLEMENT_TRIPLET_KEY_SHA256.items():
            with self.subTest(key=key):
                self.assertEqual(digest, policy.sha256_of(key))
        # No shape name is a bare live value either.
        for shape in policy.SETTLEMENT_SHAPES:
            with self.subTest(shape=shape):
                self.assertNotEqual(shape, GLA_STAGED)
                self.assertNotEqual(shape, GLA_TRANSIENT)

    def test_the_two_baselines_reuse_the_two_proven_digests_and_add_no_third(self):
        contract = policy.convergence_contract()
        self.assertEqual(contract["settled_baseline_value_sha256"],
                         contract["staged_value_sha256"])
        self.assertEqual(contract["pending_baseline_value_sha256"],
                         contract["transient_value_sha256"])
        self.assertEqual(set(policy.BASELINE_VALUE_SHA256.values()),
                         {policy.GLA_STAGED_VALUE_SHA256,
                          policy.GLA_TRANSIENT_VALUE_SHA256})
        self.assertEqual(set(policy.BASELINE_VALUE_SHA256),
                         {"settled_baseline", "pending_baseline"})
        self.assertEqual(policy.BASELINE_MODES,
                         ("absent", "settled_baseline", "pending_baseline"))

    def test_the_two_pending_schedules_are_fixed_and_inside_their_own_ceilings(self):
        for schedule, total, ceiling in (
            (policy.PENDING_STABILITY_SCHEDULE_SECONDS,
             policy.PENDING_STABILITY_MAX_SECONDS,
             policy.PENDING_STABILITY_CEILING_SECONDS),
            (policy.PENDING_SETTLE_CONFIRM_SCHEDULE_SECONDS,
             policy.PENDING_SETTLE_CONFIRM_MAX_SECONDS,
             policy.PENDING_SETTLE_CONFIRM_CEILING_SECONDS),
        ):
            with self.subTest(schedule=schedule):
                self.assertEqual(schedule, (2, 4))
                self.assertEqual(sum(schedule), total)
                self.assertEqual(total, 6)
                self.assertLessEqual(total, ceiling)
                self.assertEqual(ceiling, 6)
                self.assertTrue(all(isinstance(step, int) and step > 0
                                    for step in schedule))
        # Three observations at staging (the first read plus two more); at least
        # one confirming read after a settling write.
        self.assertEqual(policy.PENDING_STABILITY_OBSERVATIONS, 3)
        self.assertGreaterEqual(policy.PENDING_SETTLE_CONFIRM_OBSERVATIONS, 1)
        self.assertEqual(policy.PENDING_SETTLE_CONFIRM_OBSERVATIONS, 2)

    def test_no_baseline_mode_is_a_bare_live_value(self):
        """A mode name must never be confusable with a value read from the store."""
        for mode in policy.BASELINE_MODES:
            with self.subTest(mode=mode):
                self.assertNotEqual(mode, GLA_STAGED)
                self.assertNotEqual(mode, GLA_TRANSIENT)

    def test_the_digests_are_the_canonical_hashes_of_the_two_observed_values(self):
        self.assertEqual(policy.sha256_of(GLA_STAGED), policy.GLA_STAGED_VALUE_SHA256)
        self.assertEqual(policy.sha256_of(GLA_TRANSIENT), policy.GLA_TRANSIENT_VALUE_SHA256)
        self.assertEqual(policy.sha256_of(GLA_KEY), policy.GLA_META_KEY_SHA256)
        self.assertNotEqual(policy.GLA_STAGED_VALUE_SHA256,
                            policy.GLA_TRANSIENT_VALUE_SHA256)

    def test_the_schedule_is_fixed_bounded_and_totals_its_own_ceiling(self):
        schedule = policy.CONVERGENCE_SCHEDULE_SECONDS
        self.assertEqual(schedule, (2, 4, 8, 16, 30, 30))
        self.assertEqual(sum(schedule), policy.CONVERGENCE_MAX_SECONDS)
        self.assertEqual(policy.CONVERGENCE_MAX_SECONDS, 90)
        self.assertLessEqual(policy.CONVERGENCE_MAX_SECONDS,
                             policy.CONVERGENCE_CEILING_SECONDS)
        self.assertTrue(all(isinstance(step, int) and step > 0 for step in schedule))
        self.assertEqual(policy.CONVERGENCE_FINAL_REQUIREMENT,
                         "exact_staged_protected_state")
        self.assertEqual(policy.CONVERGENCE_TIMEOUT_FINAL_STATE, "pending_not_accepted")

    def test_the_validator_accepts_only_the_fixed_contract(self):
        policy.validate_convergence_contract(policy.convergence_contract())

        def mutated(mutate):
            contract = policy.convergence_contract()
            mutate(contract)
            return contract

        refused = (
            None, "gla", [], {},
            mutated(lambda c: c.__setitem__("extra", "x")),
            mutated(lambda c: c.pop("meta_key")),
            mutated(lambda c: c.__setitem__("kind", "gla_sync_anything")),
            mutated(lambda c: c.__setitem__("meta_key", "_wc_gla_sync_status2")),
            mutated(lambda c: c.__setitem__("staged_value_sha256", "0" * 64)),
            mutated(lambda c: c.__setitem__("transient_value_sha256", "0" * 64)),
            mutated(lambda c: c.__setitem__("staged_value_sha256", "0" * 63)),
            mutated(lambda c: c.__setitem__(
                "staged_value_sha256", policy.GLA_STAGED_VALUE_SHA256.upper())),
            # The two digests may never be swapped: that would make the transient
            # the success condition.
            mutated(lambda c: c.update({
                "staged_value_sha256": policy.GLA_TRANSIENT_VALUE_SHA256,
                "transient_value_sha256": policy.GLA_STAGED_VALUE_SHA256})),
            mutated(lambda c: c["schedule_seconds"].reverse()),
            mutated(lambda c: c.__setitem__("schedule_seconds",
                                            [2.0, 4.0, 8.0, 16.0, 30.0, 30.0])),
            mutated(lambda c: c.__setitem__("schedule_seconds", [2, 4, 8, 16, 30])),
            mutated(lambda c: c.__setitem__("schedule_seconds", (2, 4, 8, 16, 30, 30))),
            mutated(lambda c: c.__setitem__("schedule_seconds", [0, 4, 8, 16, 30, 30])),
            mutated(lambda c: c.__setitem__("schedule_seconds", [-2, 4, 8, 16, 30, 32])),
            mutated(lambda c: c.update({"schedule_seconds": [600, 600, 600, 600, 600, 600],
                                        "max_seconds": 3600})),
            mutated(lambda c: c.__setitem__("max_seconds", 91)),
            mutated(lambda c: c.__setitem__("max_seconds", 90.0)),
            mutated(lambda c: c.__setitem__("max_seconds", True)),
            mutated(lambda c: c.__setitem__("final_requirement", "accept_the_transient")),
            mutated(lambda c: c.__setitem__(
                "allowed_changed_protected_fields_during_wait", ["meta_data", "price"])),
            mutated(lambda c: c.__setitem__(
                "allowed_changed_protected_fields_during_wait", [])),
            mutated(lambda c: c.__setitem__(
                "allowed_changed_protected_fields_during_wait", "meta_data")),
            # Schema 4. Neither pending schedule may be widened, re-ordered,
            # floated, emptied or decoupled from its own declared total.
            mutated(lambda c: c.__setitem__("pending_stability_schedule_seconds",
                                            [600, 600])),
            mutated(lambda c: c.__setitem__("pending_stability_max_seconds", 1200)),
            mutated(lambda c: c.update({"pending_stability_schedule_seconds": [600, 600],
                                        "pending_stability_max_seconds": 1200})),
            mutated(lambda c: c.__setitem__("pending_stability_schedule_seconds",
                                            [2.0, 4.0])),
            mutated(lambda c: c.__setitem__("pending_stability_schedule_seconds", [2])),
            mutated(lambda c: c.__setitem__("pending_stability_schedule_seconds", [])),
            mutated(lambda c: c.__setitem__("pending_stability_schedule_seconds",
                                            [0, 6])),
            mutated(lambda c: c["pending_stability_schedule_seconds"].reverse()),
            mutated(lambda c: c.__setitem__("pending_stability_max_seconds", 6.0)),
            mutated(lambda c: c.__setitem__("pending_settle_confirm_schedule_seconds",
                                            [600, 600])),
            mutated(lambda c: c.update({
                "pending_settle_confirm_schedule_seconds": [600, 600],
                "pending_settle_confirm_max_seconds": 1200})),
            mutated(lambda c: c.__setitem__("pending_settle_confirm_schedule_seconds",
                                            [])),
            mutated(lambda c: c["pending_settle_confirm_schedule_seconds"].reverse()),
            # Nor may a baseline be added, renamed, dropped or re-pointed.
            mutated(lambda c: c.__setitem__(
                "baseline_modes", ["absent", "settled_baseline", "pending_baseline",
                                   "anything"])),
            mutated(lambda c: c.__setitem__("baseline_modes", ["absent"])),
            mutated(lambda c: c.__setitem__(
                "baseline_modes", ["pending_baseline", "settled_baseline", "absent"])),
            mutated(lambda c: c.__setitem__("pending_baseline_value_sha256", "0" * 64)),
            mutated(lambda c: c.__setitem__("pending_baseline_value_sha256", "0" * 63)),
            mutated(lambda c: c.__setitem__("settled_baseline_value_sha256", "0" * 64)),
            # Swapping the two baselines would make the pending state the success
            # condition of the settled path, and vice versa.
            mutated(lambda c: c.update({
                "settled_baseline_value_sha256": policy.GLA_TRANSIENT_VALUE_SHA256,
                "pending_baseline_value_sha256": policy.GLA_STAGED_VALUE_SHA256})),
            mutated(lambda c: c.__setitem__("pending_final_requirement",
                                            "accept_anything")),
            # Schema 5. No third shape, no fourth companion key, no wider entry
            # ceiling, no re-pointed status key, and no float in place of the
            # integer count.
            mutated(lambda c: c.__setitem__(
                "settlement_shapes", ["gla_status_only",
                                      "gla_status_with_stamp_and_hash", "anything"])),
            mutated(lambda c: c.__setitem__("settlement_shapes", ["gla_status_only"])),
            mutated(lambda c: c.__setitem__(
                "settlement_shapes", ["gla_status_with_stamp_and_hash",
                                      "gla_status_only"])),
            mutated(lambda c: c.__setitem__("settlement_shapes", [])),
            mutated(lambda c: c.__setitem__("settlement_status_meta_key",
                                            "_wc_gla_synced_at")),
            mutated(lambda c: c.__setitem__("settlement_status_meta_key",
                                            "_wc_gla_sync_status2")),
            mutated(lambda c: c["settlement_companion_meta_keys"].append("_anything")),
            mutated(lambda c: c["settlement_companion_meta_keys"].reverse()),
            mutated(lambda c: c.__setitem__("settlement_companion_meta_keys", [])),
            mutated(lambda c: c.__setitem__(
                "settlement_companion_meta_keys",
                ["_wc_gla_synced_at", "_wc_gla_sync_status"])),
            mutated(lambda c: c.__setitem__("settlement_max_changed_meta_entries", 4)),
            mutated(lambda c: c.__setitem__("settlement_max_changed_meta_entries", 250)),
            mutated(lambda c: c.__setitem__("settlement_max_changed_meta_entries", 3.0)),
            mutated(lambda c: c.__setitem__("settlement_max_changed_meta_entries", True)),
        )
        for value in refused:
            with self.subTest(value=str(value)[:70]):
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.validate_convergence_contract(value)

    def test_a_staged_plan_carries_the_contract_and_reloads_with_it(self):
        store = self._assign_store()
        plan_path, result = self._stage(store, self._assign_input())
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["convergence_contract"], policy.convergence_contract())
        self.assertEqual(result["convergence_contract"], policy.convergence_contract())
        policy.load_plan(str(plan_path))

    def test_a_rehashed_contract_edit_is_refused_before_vault_or_network(self):
        store = self._assign_store()
        plan_path, _ = self._stage(store, self._assign_input())
        self._rewrite(
            plan_path,
            lambda plan: plan["convergence_contract"].__setitem__("max_seconds", 3600),
            rehash=True,
        )
        with mock.patch.object(policy.wc, "load_vault") as load, \
             mock.patch.object(policy.wc, "api_get") as get, \
             mock.patch.object(policy.wc, "api_request") as write:
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
            load.assert_not_called()
            get.assert_not_called()
            write.assert_not_called()
        self.assertFalse(policy.lock_path(plan_path.resolve()).exists())

    def test_a_request_can_neither_supply_nor_widen_the_contract(self):
        store = self._assign_store()
        refused = (
            {**self._assign_input(), "convergence_contract": policy.convergence_contract()},
            {**self._assign_input(), "convergence_contract": {"kind": "anything"}},
            {**self._assign_input(), "schedule_seconds": [1, 1]},
            {**self._assign_input(), "max_seconds": 3600},
            {**self._assign_input(), "gla_convergence_eligible": True},
            {**self._assign_input(), "final_requirement": "accept_the_transient"},
            # Schema 4: a request may not choose a baseline, a digest, a schedule
            # or a ceiling either.
            {**self._assign_input(), "gla_baseline_mode": "pending_baseline"},
            {**self._assign_input(), "baseline_modes": ["pending_baseline"]},
            {**self._assign_input(), "gla_pending_stability": {"stable": True}},
            {**self._assign_input(), "pending_baseline_value_sha256": "0" * 64},
            {**self._assign_input(), "pending_stability_schedule_seconds": [0]},
            {**self._assign_input(), "pending_stability_max_seconds": 0},
            {**self._assign_input(), "pending_settle_confirm_schedule_seconds": []},
            {**self._assign_input(), "pending_settle_confirm_max_seconds": 0},
            {**self._assign_input(), "pending_final_requirement": "accept_anything"},
            # Schema 5: a request may not name a settlement shape, a companion key
            # or a changed-entry ceiling either.
            {**self._assign_input(), "settlement_shapes": ["anything"]},
            {**self._assign_input(), "settlement_shape": "gla_status_only"},
            {**self._assign_input(),
             "settlement_companion_meta_keys": ["_wc_gla_synced_at", "_anything"]},
            {**self._assign_input(), "settlement_max_changed_meta_entries": 250},
            {**self._assign_input(), "settlement_status_meta_key": "_anything"},
        )
        for data in refused:
            with self.subTest(extra=sorted(set(data) - {"action", "targets", "sources"})):
                with self.assertRaises(policy.ShippingPolicyError):
                    self._stage(store, data)
        # Nor may a target row carry its own baseline, eligibility or timing.
        for row in (
            {"kind": "variation", "product_id": 1423, "variation_id": 1424,
             "gla_convergence_eligible": True},
            {"kind": "variation", "product_id": 1423, "variation_id": 1424,
             "max_seconds": 3600},
            {"kind": "variation", "product_id": 1423, "variation_id": 1424,
             "gla_baseline_mode": "pending_baseline"},
            {"kind": "variation", "product_id": 1423, "variation_id": 1424,
             "gla_pending_stability": {"stable": True}},
            {"kind": "variation", "product_id": 1423, "variation_id": 1424,
             "pending_stability_max_seconds": 0},
        ):
            with self.subTest(row=sorted(row)):
                with self.assertRaises(policy.ShippingPolicyError):
                    self._stage(store, self._assign_input([row]))
        self.assertEqual(store.writes, [])


# ---------------------------------------------------------------------------
# Schema 4: the closed baseline modes (schema 3's eligibility, widened by one)
# ---------------------------------------------------------------------------
class GlaBaselineModeTests(PlanFixture):
    def _projection(self, entries):
        return policy.metadata_projection({"meta_data": entries})

    def test_exactly_one_settled_entry_is_the_settled_baseline(self):
        projection = self._projection(gla_meta_entries())
        self.assertEqual(policy.gla_baseline_mode(projection), "settled_baseline")
        self.assertIs(policy.gla_convergence_eligible(projection), True)

    def test_exactly_one_pending_entry_is_the_pending_baseline(self):
        """Schema 3 refused this outright. That refusal was the deadlock."""
        projection = self._projection(gla_meta_entries(GLA_TRANSIENT))
        self.assertEqual(policy.gla_baseline_mode(projection), "pending_baseline")
        # It is a lawful baseline, but it is NOT the settled path's precondition.
        self.assertIs(policy.gla_convergence_eligible(projection), False)

    def test_an_absent_key_is_the_absent_baseline(self):
        for projection in ([], self._projection(meta_entries()),
                           self._projection([{"id": 1, "key": "_wc_gla_sync_status_other",
                                              "value": GLA_STAGED}])):
            with self.subTest(projection=len(projection)):
                self.assertEqual(policy.gla_baseline_mode(projection), "absent")
                self.assertIs(policy.gla_convergence_eligible(projection), False)

    def test_duplicate_malformed_or_undiagnosed_entries_refuse_staging(self):
        refused = (
            # Two entries for one key: which one is the plan's before-state?
            gla_meta_entries() + [{"id": 63041, "key": GLA_KEY, "value": GLA_STAGED}],
            gla_meta_entries() + [{"id": 63040, "key": GLA_KEY, "value": GLA_STAGED}],
            gla_meta_entries(GLA_TRANSIENT) + [
                {"id": 63041, "key": GLA_KEY, "value": GLA_TRANSIENT}],
            # A value nobody has diagnosed. Schema 4 adds a SECOND known state,
            # not a tolerance for unknown ones.
            gla_meta_entries("error"),
            gla_meta_entries(""),
            gla_meta_entries("SYNCED"),
            gla_meta_entries("PENDING"),
            gla_meta_entries("pending "),
            meta_entries() + [{"id": 63040, "key": GLA_KEY, "value": ["synced"]}],
            meta_entries() + [{"id": 63040, "key": GLA_KEY, "value": ["pending"]}],
            # Unidentifiable entries.
            meta_entries() + [{"id": True, "key": GLA_KEY, "value": GLA_STAGED}],
            meta_entries() + [{"id": "63040", "key": GLA_KEY, "value": GLA_STAGED}],
            meta_entries() + [{"key": GLA_KEY, "value": GLA_STAGED}],
            meta_entries() + [{"key": GLA_KEY, "value": GLA_TRANSIENT}],
        )
        for entries in refused:
            with self.subTest(entries=str(entries[-1])[:60]):
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.gla_baseline_mode(self._projection(entries))
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.gla_convergence_eligible(self._projection(entries))

    def test_the_refusal_for_an_undiagnosed_value_names_no_value(self):
        with self.assertRaises(policy.ShippingPolicyError) as caught:
            policy.gla_baseline_mode(self._projection(gla_meta_entries("error")))
        self.assertIn(GLA_KEY, str(caught.exception))
        message = strip_fixed_vocabulary(str(caught.exception))
        # Neither the value it found nor either value it expected.
        self.assertNotIn("error", message)
        self.assertNotIn(GLA_STAGED, message)
        self.assertNotIn(GLA_TRANSIENT, message)

    def test_staging_records_eligibility_and_names_it_without_a_value(self):
        store = FakeStore(
            {"/products/1455/variations/2056": gla_variation_record(1455, 2056)},
            classes=[dict(FREIGHT_CLASS_ROW)],
        )
        plan_path, result = self._stage(store, {
            "action": "shipping_class_assign",
            "targets": [{"kind": "variation", "product_id": 1455, "variation_id": 2056}],
            "sources": {"policy": "shipping_policy_manifest.json 2026-08-09"},
        })
        self.assertIs(result["targets"][0]["gla_convergence_eligible"], True)
        self.assertEqual(result["targets"][0]["gla_baseline_mode"], "settled_baseline")
        self.assertIsNone(result["targets"][0]["gla_pending_stability"])
        self.assertEqual(result["baseline_modes"],
                         {"absent": 0, "settled_baseline": 1, "pending_baseline": 0})
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertIs(plan["targets"][0]["gla_convergence_eligible"], True)
        self.assertEqual(plan["targets"][0]["gla_baseline_mode"], "settled_baseline")
        self.assertIsNone(plan["targets"][0]["gla_pending_stability"])
        # The key is named; neither value is anywhere in the plan or the preview.
        text = strip_fixed_vocabulary(
            plan_path.read_text(encoding="utf-8") + json.dumps(result))
        self.assertIn(GLA_KEY, text)
        self.assertNotIn(GLA_STAGED, text)
        self.assertNotIn(GLA_TRANSIENT, text)
        self.assertNotIn(SECRET_META_VALUE, text)

    def test_staging_refuses_a_target_at_an_undiagnosed_sync_value(self):
        """Rachad is never asked to approve a plan built on a state nobody has
        diagnosed. Schema 4 added ONE more known state, not a tolerance."""
        store = FakeStore(
            {"/products/1455/variations/2056":
                gla_variation_record(1455, 2056, "error")},
            classes=[dict(FREIGHT_CLASS_ROW)],
        )
        with self.assertRaises(policy.ShippingPolicyError) as caught:
            self._stage(store, {
                "action": "shipping_class_assign",
                "targets": [{"kind": "variation", "product_id": 1455,
                             "variation_id": 2056}],
                "sources": {"policy": "manifest"},
            })
        self.assertIn(GLA_KEY, str(caught.exception))
        message = strip_fixed_vocabulary(str(caught.exception))
        self.assertNotIn(GLA_TRANSIENT, message)
        self.assertNotIn("error", message)
        self.assertEqual(store.writes, [])
        self.assertEqual(list(self.plan_dir.glob("*.json")), [])


# ---------------------------------------------------------------------------
# Schema 3: the exact transient detector (pure)
# ---------------------------------------------------------------------------
class GlaTransientDetectorTests(unittest.TestCase):
    ENDPOINT = "/products/1455/variations/2056"

    def _target(self, record, eligible=None, mode=None):
        projection = policy.metadata_projection(record)
        derived = policy.gla_baseline_mode(projection)
        return {
            "kind": "variation", "product_id": 1455, "variation_id": 2056,
            "before_shipping_class": "",
            "before_stale_fingerprint": policy.stale_fingerprint(record),
            "before_protected_fingerprint": policy.protected_fingerprint(record),
            "before_date_modified_gmt": str(record.get("date_modified_gmt") or ""),
            "before_protected_field_fingerprints":
                policy.protected_field_fingerprints(record),
            "before_meta_data_projection": projection,
            "gla_baseline_mode": derived if mode is None else mode,
            "gla_convergence_eligible": (derived == "settled_baseline"
                                         if eligible is None else eligible),
            "gla_pending_stability": None,
        }

    def _pair(self, staged_value=GLA_STAGED, readback_value=GLA_TRANSIENT,
              eligible=None, mode=None, **readback):
        staged = gla_variation_record(1455, 2056, staged_value)
        target = self._target(staged, eligible=eligible, mode=mode)
        moved = gla_variation_record(1455, 2056, readback_value,
                                     shipping_class=policy.FREIGHT_CLASS_SLUG)
        moved.update(readback)
        return target, policy.protected_diagnostic(
            self.ENDPOINT, "post_write", target, moved)

    def test_the_exact_transient_is_recognised(self):
        target, diagnostic = self._pair()
        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic["changed_protected_fields"], ["meta_data"])
        detail = diagnostic["meta_data_projection"]["diagnostic"]
        self.assertEqual(detail["value_changed_entry_count"], 1)
        self.assertEqual(detail["value_changed_entries"][0]["key"], GLA_KEY)
        self.assertEqual(detail["value_changed_entries"][0]["id"], GLA_ID)
        self.assertIs(policy.is_gla_sync_transient(target, diagnostic, True), True)

    def test_a_class_that_does_not_match_is_never_the_transient(self):
        target, diagnostic = self._pair()
        self.assertIs(policy.is_gla_sync_transient(target, diagnostic, False), False)

    def test_an_ineligible_target_is_never_the_transient(self):
        target, diagnostic = self._pair(eligible=False)
        self.assertIs(policy.is_gla_sync_transient(target, diagnostic, True), False)

    def test_the_reverse_transition_is_not_waitable(self):
        """Transient -> staged is not the contract, and the staged side of the
        contract is the only permitted before-state."""
        target, diagnostic = self._pair(staged_value=GLA_TRANSIENT,
                                        readback_value=GLA_STAGED, eligible=True)
        self.assertIs(policy.is_gla_sync_transient(target, diagnostic, True), False)

    def test_any_other_readback_value_is_not_the_transient(self):
        for value in ("error", "", "not-synced", "PENDING", "pending "):
            with self.subTest(value=value):
                target, diagnostic = self._pair(readback_value=value)
                self.assertIs(policy.is_gla_sync_transient(target, diagnostic, True),
                              False)

    def test_another_protected_field_moving_alongside_it_is_not_the_transient(self):
        for field, value in (("weight", "99.9"), ("regular_price", "0.01"),
                             ("name", "Renamed"), ("sku", "OTHER"), ("status", "draft")):
            with self.subTest(field=field):
                target, diagnostic = self._pair(**{field: value})
                self.assertEqual(diagnostic["changed_protected_fields"],
                                 sorted(["meta_data", field]))
                self.assertIs(policy.is_gla_sync_transient(target, diagnostic, True),
                              False)

    def test_a_protected_field_moving_without_it_is_not_the_transient(self):
        target, diagnostic = self._pair(readback_value=GLA_STAGED, weight="99.9")
        self.assertEqual(diagnostic["changed_protected_fields"], ["weight"])
        self.assertIs(policy.is_gla_sync_transient(target, diagnostic, True), False)

    def test_any_other_metadata_movement_is_not_the_transient(self):
        transient = gla_meta_entries(GLA_TRANSIENT)
        renamed = [dict(row) for row in transient]
        renamed[0] = {"id": 8801, "key": "_renamed", "value": "yes"}
        doubled = [dict(row) for row in transient]
        doubled[1] = {**doubled[1], "value": "also changed"}
        for label, entries in (
            ("added", transient + [{"id": 9500, "key": "_added", "value": "v"}]),
            ("removed", transient[1:]),
            ("reordered", list(reversed(transient))),
            ("identity_changed", renamed),
            ("two_values_changed", doubled),
            ("duplicated", transient + [{"id": GLA_ID, "key": GLA_KEY,
                                         "value": GLA_TRANSIENT}]),
        ):
            with self.subTest(label=label):
                target, diagnostic = self._pair(meta_data=entries)
                self.assertIsNotNone(diagnostic)
                self.assertIs(policy.is_gla_sync_transient(target, diagnostic, True),
                              False)

    def test_a_hand_built_diagnostic_cannot_fake_the_transient(self):
        """The detector reads a real diagnostic; a partial or inconsistent one
        must never satisfy it."""
        target, diagnostic = self._pair()
        for label, mutate in (
            ("no_aggregate_mismatch",
             lambda d: d["aggregate_protected_fingerprint"].__setitem__("matches", True)),
            ("meta_status_matched",
             lambda d: d["meta_data_projection"].__setitem__("status", "matched")),
            ("meta_refused",
             lambda d: d["meta_data_projection"].__setitem__("status", "refused")),
            ("no_meta_detail",
             lambda d: d["meta_data_projection"].__setitem__("diagnostic", None)),
            ("extra_changed_field",
             lambda d: d.__setitem__("changed_protected_fields", ["meta_data", "price"])),
            ("empty_changed_field", lambda d: d.__setitem__("changed_protected_fields", [])),
            ("identity_moved",
             lambda d: d["meta_data_projection"]["diagnostic"].__setitem__(
                 "identity_changed_position_count", 1)),
            ("order_moved",
             lambda d: d["meta_data_projection"]["diagnostic"].__setitem__(
                 "order_changed", True)),
            ("counts_differ",
             lambda d: d["meta_data_projection"]["diagnostic"].__setitem__(
                 "readback_entry_count", 9)),
            ("omitted_rows",
             lambda d: d["meta_data_projection"]["diagnostic"]["omitted_from_report"]
             .__setitem__("value_changed", 3)),
            ("index_moved",
             lambda d: d["meta_data_projection"]["diagnostic"]["value_changed_entries"][0]
             .__setitem__("readback_index", 9)),
            ("id_dropped",
             lambda d: d["meta_data_projection"]["diagnostic"]["value_changed_entries"][0]
             .__setitem__("id", None)),
            ("key_changed",
             lambda d: d["meta_data_projection"]["diagnostic"]["value_changed_entries"][0]
             .__setitem__("key", "_other_key")),
            ("value_not_differing",
             lambda d: d["meta_data_projection"]["diagnostic"]["value_changed_entries"][0]
             .__setitem__("value_differs", False)),
        ):
            with self.subTest(label=label):
                copy = json.loads(json.dumps(diagnostic))
                mutate(copy)
                self.assertIs(policy.is_gla_sync_transient(target, copy, True), False)


# ---------------------------------------------------------------------------
# Schema 4: the settling detector (pure) -- the reverse half of the same move
# ---------------------------------------------------------------------------
class GlaSettlingDetectorTests(GlaTransientDetectorTests):
    """Same fixture, mirrored contract: pending -> settled, on a pending baseline.

    Inherits the transient tests deliberately: whatever schema 4 does, the settled
    path's detector must keep answering exactly as it did under schema 3.
    """

    def _settling(self, **kwargs):
        kwargs.setdefault("staged_value", GLA_TRANSIENT)
        kwargs.setdefault("readback_value", GLA_STAGED)
        kwargs.setdefault("mode", "pending_baseline")
        return self._pair(**kwargs)

    def test_the_exact_settling_move_is_recognised(self):
        target, diagnostic = self._settling()
        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic["changed_protected_fields"], ["meta_data"])
        detail = diagnostic["meta_data_projection"]["diagnostic"]
        self.assertEqual(detail["value_changed_entry_count"], 1)
        self.assertEqual(detail["value_changed_entries"][0]["id"], GLA_ID)
        self.assertIs(policy.is_gla_sync_settling(target, diagnostic, True), True)
        # And it is NOT the settled path's transient, in either direction.
        self.assertIs(policy.is_gla_sync_transient(target, diagnostic, True), False)

    def test_the_two_detectors_never_both_fire(self):
        for label, pair in (("settled_baseline", self._pair()),
                            ("pending_baseline", self._settling())):
            with self.subTest(label=label):
                target, diagnostic = pair
                self.assertNotEqual(
                    policy.is_gla_sync_transient(target, diagnostic, True),
                    policy.is_gla_sync_settling(target, diagnostic, True))

    def test_a_class_that_does_not_match_is_never_settling(self):
        target, diagnostic = self._settling()
        self.assertIs(policy.is_gla_sync_settling(target, diagnostic, False), False)

    def test_only_a_pending_baseline_target_can_settle(self):
        for mode in ("absent", "settled_baseline", "", "pending", "PENDING_BASELINE"):
            with self.subTest(mode=mode):
                target, diagnostic = self._settling(mode=mode)
                self.assertIs(policy.is_gla_sync_settling(target, diagnostic, True),
                              False)
        # A target with no mode at all -- a schema-3 shape -- cannot settle either.
        target, diagnostic = self._settling()
        target.pop("gla_baseline_mode")
        self.assertIs(policy.is_gla_sync_settling(target, diagnostic, True), False)

    def test_the_forward_transition_is_not_a_settlement(self):
        target, diagnostic = self._pair(mode="pending_baseline")
        self.assertIs(policy.is_gla_sync_settling(target, diagnostic, True), False)

    def test_any_other_readback_value_is_not_a_settlement(self):
        for value in ("error", "", "not-synced", "SYNCED", "synced ", GLA_TRANSIENT):
            with self.subTest(value=value):
                target, diagnostic = self._settling(readback_value=value)
                self.assertIs(policy.is_gla_sync_settling(target, diagnostic, True),
                              False)

    def test_another_protected_field_moving_alongside_it_is_not_a_settlement(self):
        for field, value in (("weight", "99.9"), ("regular_price", "0.01"),
                             ("name", "Renamed"), ("sku", "OTHER"), ("status", "draft")):
            with self.subTest(field=field):
                target, diagnostic = self._settling(**{field: value})
                self.assertEqual(diagnostic["changed_protected_fields"],
                                 sorted(["meta_data", field]))
                self.assertIs(policy.is_gla_sync_settling(target, diagnostic, True),
                              False)

    def test_any_other_metadata_movement_is_not_a_settlement(self):
        settled = gla_meta_entries(GLA_STAGED)
        renamed = [dict(row) for row in settled]
        renamed[0] = {"id": 8801, "key": "_renamed", "value": "yes"}
        doubled = [dict(row) for row in settled]
        doubled[1] = {**doubled[1], "value": "also changed"}
        for label, entries in (
            ("added", settled + [{"id": 9500, "key": "_added", "value": "v"}]),
            ("removed", settled[1:]),
            ("reordered", list(reversed(settled))),
            ("identity_changed", renamed),
            ("two_values_changed", doubled),
            ("duplicated", settled + [{"id": GLA_ID, "key": GLA_KEY,
                                       "value": GLA_STAGED}]),
        ):
            with self.subTest(label=label):
                target, diagnostic = self._settling(meta_data=entries)
                self.assertIsNotNone(diagnostic)
                self.assertIs(policy.is_gla_sync_settling(target, diagnostic, True),
                              False)

    def test_a_hand_built_diagnostic_cannot_fake_a_settlement(self):
        target, diagnostic = self._settling()
        for label, mutate in (
            ("no_aggregate_mismatch",
             lambda d: d["aggregate_protected_fingerprint"].__setitem__("matches", True)),
            ("meta_status_matched",
             lambda d: d["meta_data_projection"].__setitem__("status", "matched")),
            ("no_meta_detail",
             lambda d: d["meta_data_projection"].__setitem__("diagnostic", None)),
            ("extra_changed_field",
             lambda d: d.__setitem__("changed_protected_fields", ["meta_data", "price"])),
            ("identity_moved",
             lambda d: d["meta_data_projection"]["diagnostic"].__setitem__(
                 "identity_changed_position_count", 1)),
            ("order_moved",
             lambda d: d["meta_data_projection"]["diagnostic"].__setitem__(
                 "order_changed", True)),
            ("counts_differ",
             lambda d: d["meta_data_projection"]["diagnostic"].__setitem__(
                 "readback_entry_count", 9)),
            ("omitted_rows",
             lambda d: d["meta_data_projection"]["diagnostic"]["omitted_from_report"]
             .__setitem__("value_changed", 3)),
            ("index_moved",
             lambda d: d["meta_data_projection"]["diagnostic"]["value_changed_entries"][0]
             .__setitem__("readback_index", 9)),
            ("id_dropped",
             lambda d: d["meta_data_projection"]["diagnostic"]["value_changed_entries"][0]
             .__setitem__("id", None)),
            ("key_changed",
             lambda d: d["meta_data_projection"]["diagnostic"]["value_changed_entries"][0]
             .__setitem__("key", "_other_key")),
            ("value_not_differing",
             lambda d: d["meta_data_projection"]["diagnostic"]["value_changed_entries"][0]
             .__setitem__("value_differs", False)),
        ):
            with self.subTest(label=label):
                copy = json.loads(json.dumps(diagnostic))
                mutate(copy)
                self.assertIs(policy.is_gla_sync_settling(target, copy, True), False)


# ---------------------------------------------------------------------------
# Schema 5: the measured three-entry settlement (pure)
# ---------------------------------------------------------------------------
class GlaSettlementTripletDetectorTests(unittest.TestCase):
    """The exact movement schema 4 could not see, and everything it is not.

    The endpoint is the real one schema 4 locked. Pure predicate tests: no store,
    no clock, no plan, no I/O.
    """

    ENDPOINT = "/products/1455/variations/1476"
    PRODUCT_ID = 1455
    VARIATION_ID = 1476

    def _target(self, record, mode="pending_baseline"):
        projection = policy.metadata_projection(record)
        derived = policy.gla_baseline_mode(projection)
        return {
            "kind": "variation", "product_id": self.PRODUCT_ID,
            "variation_id": self.VARIATION_ID,
            "before_shipping_class": "",
            "before_stale_fingerprint": policy.stale_fingerprint(record),
            "before_protected_fingerprint": policy.protected_fingerprint(record),
            "before_date_modified_gmt": str(record.get("date_modified_gmt") or ""),
            "before_protected_field_fingerprints":
                policy.protected_field_fingerprints(record),
            "before_meta_data_projection": projection,
            "gla_baseline_mode": derived if mode is None else mode,
            "gla_convergence_eligible": derived == "settled_baseline",
            "gla_pending_stability": None,
        }

    def _diagnostic(self, target, entries, **readback):
        moved = triplet_variation_record(self.PRODUCT_ID, self.VARIATION_ID,
                                         shipping_class=policy.FREIGHT_CLASS_SLUG)
        moved["meta_data"] = entries
        moved.update(readback)
        return policy.protected_diagnostic(self.ENDPOINT, "post_write", target, moved)

    def _pair(self, readback_entries=None, staged_entries=None,
              staged_status=GLA_TRANSIENT, mode="pending_baseline", **readback):
        staged = triplet_variation_record(self.PRODUCT_ID, self.VARIATION_ID,
                                          staged_status)
        if staged_entries is not None:
            staged["meta_data"] = staged_entries
        target = self._target(staged, mode=mode)
        entries = (settled_triplet_meta_entries() if readback_entries is None
                   else readback_entries)
        return target, self._diagnostic(target, entries, **readback)

    def _refused(self, target, diagnostic):
        self.assertIs(policy.is_gla_sync_settling(target, diagnostic, True), False)
        self.assertIsNone(policy.gla_settlement_shape(target, diagnostic, True))

    # -- the accepted shape --------------------------------------------------
    def test_the_exact_three_field_settlement_is_recognised(self):
        target, diagnostic = self._pair()
        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic["changed_protected_fields"], ["meta_data"])
        detail = diagnostic["meta_data_projection"]["diagnostic"]
        # The measured shape, entry for entry: same count either side, three
        # values moved, nothing added, removed, re-identified or reordered.
        self.assertEqual(detail["staged_entry_count"], 7)
        self.assertEqual(detail["readback_entry_count"], 7)
        self.assertEqual(detail["value_changed_entry_count"], 3)
        self.assertEqual(detail["added_entry_count"], 0)
        self.assertEqual(detail["removed_entry_count"], 0)
        self.assertEqual(detail["identity_changed_position_count"], 0)
        self.assertIs(detail["order_changed"], False)
        rows = {row["key"]: row for row in detail["value_changed_entries"]}
        self.assertEqual(set(rows), {GLA_KEY, GLA_SYNCED_AT_KEY, GLA_SYNC_HASH_KEY})
        self.assertEqual(rows[GLA_KEY]["id"], GLA_ID)
        self.assertEqual(rows[GLA_KEY]["staged_index"], 1)
        self.assertEqual(rows[GLA_SYNCED_AT_KEY]["staged_index"], 0)
        self.assertEqual(rows[GLA_SYNCED_AT_KEY]["id"], GLA_SYNCED_AT_ID)
        self.assertEqual(rows[GLA_SYNC_HASH_KEY]["staged_index"], 5)
        self.assertEqual(rows[GLA_SYNC_HASH_KEY]["id"], GLA_SYNC_HASH_ID)
        for key, row in rows.items():
            with self.subTest(key=key):
                self.assertEqual(row["staged_index"], row["readback_index"])
                self.assertIs(row["value_differs"], True)
        # Exactly the pinned status transition, and nothing invented for it.
        self.assertEqual(rows[GLA_KEY]["staged_value_sha256"],
                         policy.GLA_TRANSIENT_VALUE_SHA256)
        self.assertEqual(rows[GLA_KEY]["readback_value_sha256"],
                         policy.GLA_STAGED_VALUE_SHA256)
        self.assertIs(policy.is_gla_sync_settling(target, diagnostic, True), True)
        self.assertEqual(policy.gla_settlement_shape(target, diagnostic, True),
                         "gla_status_with_stamp_and_hash")

    def test_the_schema_four_predicate_alone_still_cannot_see_it(self):
        """The defect itself, pinned. The repair is a SECOND closed shape, not a
        loosened first one -- so the single-entry rule must answer exactly as it
        did, and it is the wider rule that recognises this movement."""
        target, diagnostic = self._pair()
        self.assertIs(policy._is_single_gla_value_move(
            diagnostic, True, policy.GLA_TRANSIENT_VALUE_SHA256,
            policy.GLA_STAGED_VALUE_SHA256), False)
        self.assertIs(
            policy._is_gla_settlement_triplet_move(target, diagnostic, True), True)

    def test_the_narrow_status_only_settlement_still_wins_its_own_name(self):
        """A resource with no companion entries settles exactly as schema 4 said."""
        staged = gla_variation_record(1455, 1476, GLA_TRANSIENT)
        target = self._target(staged)
        moved = gla_variation_record(1455, 1476, GLA_STAGED,
                                     shipping_class=policy.FREIGHT_CLASS_SLUG)
        diagnostic = policy.protected_diagnostic(
            self.ENDPOINT, "post_write", target, moved)
        self.assertEqual(policy.gla_settlement_shape(target, diagnostic, True),
                         "gla_status_only")

    # -- the status transition is the gate -----------------------------------
    def test_the_status_must_move_the_pinned_pending_to_the_pinned_settled(self):
        for label, status in (("unchanged", GLA_TRANSIENT),
                              ("a_third_value", "error"),
                              ("empty", ""),
                              ("case_shifted", GLA_STAGED.upper()),
                              ("padded", GLA_STAGED + " "),
                              ("prefixed", "not-" + GLA_STAGED)):
            with self.subTest(label=label):
                target, diagnostic = self._pair(
                    readback_entries=triplet_meta_entries(
                        status, SYNCED_AT_AFTER, SYNC_HASH_AFTER))
                self._refused(target, diagnostic)

    def test_the_forward_transition_with_its_companions_is_not_a_settlement(self):
        """settled -> pending, even carrying the same two companions, is the other
        half of the convergence. It is never a settlement, on any baseline."""
        target, diagnostic = self._pair(
            staged_status=GLA_STAGED,
            readback_entries=triplet_meta_entries(
                GLA_TRANSIENT, SYNCED_AT_AFTER, SYNC_HASH_AFTER))
        self._refused(target, diagnostic)
        # Nor is it the settled path's waitable transient: that one is ONE entry.
        settled_target = self._target(
            triplet_variation_record(self.PRODUCT_ID, self.VARIATION_ID, GLA_STAGED),
            mode="settled_baseline")
        settled_target["gla_convergence_eligible"] = True
        self.assertIs(
            policy.is_gla_sync_transient(settled_target, diagnostic, True), False)

    def test_a_class_that_does_not_match_is_never_a_settlement(self):
        target, diagnostic = self._pair()
        self.assertIs(policy.is_gla_sync_settling(target, diagnostic, False), False)
        self.assertIsNone(policy.gla_settlement_shape(target, diagnostic, False))

    def test_the_triplet_from_any_other_baseline_is_not_a_settlement(self):
        for mode in ("absent", "settled_baseline", "", "pending", "PENDING_BASELINE"):
            with self.subTest(mode=mode):
                target, diagnostic = self._pair(mode=mode)
                self._refused(target, diagnostic)
        # A schema-3 shaped target, carrying no mode at all, reaches neither path.
        target, diagnostic = self._pair()
        target.pop("gla_baseline_mode")
        self._refused(target, diagnostic)

    def test_a_settled_baseline_resource_can_never_be_settled_confirmed(self):
        """Its before-state is already the settled digest, so the pinned pending ->
        settled transition cannot be what its readback shows."""
        target, diagnostic = self._pair(staged_status=GLA_STAGED,
                                        mode="settled_baseline")
        self._refused(target, diagnostic)

    # -- nothing else may move ----------------------------------------------
    def test_a_fourth_metadata_value_change_is_not_a_settlement(self):
        settled = settled_triplet_meta_entries()
        for label, index in (("an_unrelated_third_party_entry", 2),
                             ("a_fourth_google_entry", 6)):
            with self.subTest(label=label):
                entries = [dict(row) for row in settled]
                entries[index] = {**entries[index], "value": "MOVED-AS-WELL"}
                target, diagnostic = self._pair(readback_entries=entries)
                detail = diagnostic["meta_data_projection"]["diagnostic"]
                self.assertEqual(detail["value_changed_entry_count"], 4)
                self._refused(target, diagnostic)

    def test_only_the_two_observed_counts_are_settlements(self):
        """One entry (schema 3's proven shape) and three (the measured one). Two is
        a shape nobody has observed, so it is refused rather than interpolated."""
        for label, entries in (
            ("status_and_stamp_only",
             triplet_meta_entries(GLA_STAGED, SYNCED_AT_AFTER, SYNC_HASH_BEFORE)),
            ("status_and_hash_only",
             triplet_meta_entries(GLA_STAGED, SYNCED_AT_BEFORE, SYNC_HASH_AFTER)),
            ("companions_only",
             triplet_meta_entries(GLA_TRANSIENT, SYNCED_AT_AFTER, SYNC_HASH_AFTER)),
        ):
            with self.subTest(label=label):
                target, diagnostic = self._pair(readback_entries=entries)
                self._refused(target, diagnostic)
        # ...and the status moving ALONE, while both companions sit still, is the
        # narrow shape, not a refusal. The two shapes are the two observed counts.
        target, diagnostic = self._pair(readback_entries=triplet_meta_entries(
            GLA_STAGED, SYNCED_AT_BEFORE, SYNC_HASH_BEFORE))
        self.assertEqual(
            diagnostic["meta_data_projection"]["diagnostic"]["value_changed_entry_count"],
            1)
        self.assertEqual(policy.gla_settlement_shape(target, diagnostic, True),
                         "gla_status_only")

    def test_any_add_remove_or_reorder_is_not_a_settlement(self):
        settled = settled_triplet_meta_entries()
        for label, entries in (
            ("added", settled + [{"id": 9500, "key": "_added",
                                  "value": SECRET_META_VALUE}]),
            ("removed", settled[:-1]),
            ("status_removed", [row for row in settled if row["key"] != GLA_KEY]),
            ("companion_removed",
             [row for row in settled if row["key"] != GLA_SYNC_HASH_KEY]),
            ("reordered", list(reversed(settled))),
            ("triplet_reordered", [settled[1], settled[0]] + settled[2:]),
        ):
            with self.subTest(label=label):
                target, diagnostic = self._pair(readback_entries=entries)
                self.assertIsNotNone(diagnostic)
                self._refused(target, diagnostic)

    def test_an_identity_change_in_any_of_the_three_is_not_a_settlement(self):
        settled = settled_triplet_meta_entries()
        for entry, index in (("status", 1), ("stamp", 0), ("hash", 5)):
            # Swapped to ANOTHER of the three, never to its own key: relabelling an
            # entry as one of its siblings must not read as "still one of the three".
            sibling = GLA_KEY if entry != "status" else GLA_SYNC_HASH_KEY
            for drift, patch in (("id_changed", {"id": 99999}),
                                 ("id_dropped", {"id": None}),
                                 ("key_renamed", {"key": "_renamed"}),
                                 ("key_swapped", {"key": sibling})):
                with self.subTest(entry=entry, drift=drift):
                    entries = [dict(row) for row in settled]
                    entries[index] = {**entries[index], **patch}
                    target, diagnostic = self._pair(readback_entries=entries)
                    self.assertIsNotNone(diagnostic)
                    self._refused(target, diagnostic)

    def test_an_index_change_in_any_of_the_three_is_not_a_settlement(self):
        """Same entries, same ids, same keys -- one of them moved position."""
        settled = settled_triplet_meta_entries()
        for entry, index in (("status", 1), ("stamp", 0), ("hash", 5)):
            with self.subTest(entry=entry):
                entries = [dict(row) for row in settled]
                moved = entries.pop(index)
                entries.append(moved)
                target, diagnostic = self._pair(readback_entries=entries)
                self.assertIsNotNone(diagnostic)
                self._refused(target, diagnostic)

    # -- duplicates and malformed entries ------------------------------------
    def test_a_duplicate_appearing_on_the_readback_is_not_a_settlement(self):
        settled = settled_triplet_meta_entries()
        for entry, key, entry_id in (("status", GLA_KEY, GLA_ID),
                                     ("stamp", GLA_SYNCED_AT_KEY, GLA_SYNCED_AT_ID),
                                     ("hash", GLA_SYNC_HASH_KEY, GLA_SYNC_HASH_ID)):
            with self.subTest(entry=entry):
                entries = settled + [{"id": entry_id, "key": key, "value": "AGAIN"}]
                target, diagnostic = self._pair(readback_entries=entries)
                self._refused(target, diagnostic)

    def test_a_companion_duplicated_in_the_before_state_can_never_settle(self):
        """A duplicated status entry already refuses at staging. A duplicated
        COMPANION does not, so the triplet rule counts all three itself rather
        than guessing which of two Google meant to move."""
        staged_entries = triplet_meta_entries() + [
            {"id": 74839, "key": GLA_SYNC_HASH_KEY, "value": SYNC_HASH_BEFORE}]
        readback_entries = [dict(row) for row in staged_entries]
        readback_entries[0] = {**readback_entries[0], "value": SYNCED_AT_AFTER}
        readback_entries[1] = {**readback_entries[1], "value": GLA_STAGED}
        readback_entries[5] = {**readback_entries[5], "value": SYNC_HASH_AFTER}
        target, diagnostic = self._pair(staged_entries=staged_entries,
                                        readback_entries=readback_entries)
        # Structurally this is three values moved, each keeping its identity --
        # the count check alone would accept it. The singularity rule refuses it.
        detail = diagnostic["meta_data_projection"]["diagnostic"]
        self.assertEqual(detail["value_changed_entry_count"], 3)
        self.assertEqual(detail["added_entry_count"], 0)
        self.assertEqual(detail["identity_changed_position_count"], 0)
        self._refused(target, diagnostic)
        self.assertIs(policy._triplet_entries_are_singular(
            target["before_meta_data_projection"]), False)

    def test_a_malformed_relevant_entry_is_not_a_settlement(self):
        settled = settled_triplet_meta_entries()
        for entry, index in (("status", 1), ("stamp", 0), ("hash", 5)):
            for drift, replacement in (
                ("not_an_object", "just a string"),
                ("no_key", {"id": 4242, "value": "v"}),
                ("renamed_key", {"id": 4242, "key": "_renamed", "value": "v"}),
                ("unprintable_key", {"id": 4242, "key": "bad\nkey", "value": "v"}),
                ("negative_id", {"id": -1, "key": GLA_SYNCED_AT_KEY, "value": "v"}),
            ):
                with self.subTest(entry=entry, drift=drift):
                    entries = [dict(row) if isinstance(row, dict) else row
                               for row in settled]
                    entries[index] = replacement
                    target, diagnostic = self._pair(readback_entries=entries)
                    self.assertIsNotNone(diagnostic)
                    self._refused(target, diagnostic)

    def test_a_malformed_status_entry_in_the_before_state_refuses_outright(self):
        """gla_baseline_mode is the gate at staging; it never reaches a detector."""
        entries = [dict(row) for row in triplet_meta_entries()]
        entries[1] = {"key": GLA_KEY, "value": GLA_TRANSIENT}   # no stable id
        record = triplet_variation_record(self.PRODUCT_ID, self.VARIATION_ID)
        record["meta_data"] = entries
        with self.assertRaises(policy.ShippingPolicyError):
            policy.gla_baseline_mode(policy.metadata_projection(record))

    # -- another protected field --------------------------------------------
    def test_another_protected_field_moving_alongside_it_is_not_a_settlement(self):
        for field, value in (("weight", "99.9"), ("regular_price", "0.01"),
                             ("name", "Renamed"), ("sku", "OTHER"),
                             ("status", "draft")):
            with self.subTest(field=field):
                target, diagnostic = self._pair(**{field: value})
                self.assertEqual(diagnostic["changed_protected_fields"],
                                 sorted(["meta_data", field]))
                self._refused(target, diagnostic)

    # -- hand-built diagnostics ----------------------------------------------
    def test_a_hand_built_diagnostic_cannot_fake_the_triplet(self):
        target, diagnostic = self._pair()
        rows = "value_changed_entries"
        for label, mutate in (
            ("no_aggregate_mismatch",
             lambda d: d["aggregate_protected_fingerprint"].__setitem__("matches", True)),
            ("meta_status_matched",
             lambda d: d["meta_data_projection"].__setitem__("status", "matched")),
            ("meta_refused",
             lambda d: d["meta_data_projection"].__setitem__("status", "refused")),
            ("no_meta_detail",
             lambda d: d["meta_data_projection"].__setitem__("diagnostic", None)),
            ("extra_changed_field",
             lambda d: d.__setitem__("changed_protected_fields", ["meta_data", "price"])),
            ("empty_changed_field",
             lambda d: d.__setitem__("changed_protected_fields", [])),
            ("field_fingerprints_equal",
             lambda d: d["field_fingerprints"]["meta_data"].__setitem__(
                 "readback_sha256",
                 d["field_fingerprints"]["meta_data"]["staged_sha256"])),
            ("identity_moved",
             lambda d: d["meta_data_projection"]["diagnostic"].__setitem__(
                 "identity_changed_position_count", 1)),
            ("order_moved",
             lambda d: d["meta_data_projection"]["diagnostic"].__setitem__(
                 "order_changed", True)),
            ("counts_differ",
             lambda d: d["meta_data_projection"]["diagnostic"].__setitem__(
                 "readback_entry_count", 9)),
            ("added_claimed",
             lambda d: d["meta_data_projection"]["diagnostic"].__setitem__(
                 "added_entry_count", 1)),
            ("removed_claimed",
             lambda d: d["meta_data_projection"]["diagnostic"].__setitem__(
                 "removed_entry_count", 1)),
            ("omitted_rows",
             lambda d: d["meta_data_projection"]["diagnostic"]["omitted_from_report"]
             .__setitem__("value_changed", 3)),
            ("count_says_three_but_two_rows",
             lambda d: d["meta_data_projection"]["diagnostic"].__setitem__(
                 rows, d["meta_data_projection"]["diagnostic"][rows][:2])),
            ("a_row_is_not_an_object",
             lambda d: d["meta_data_projection"]["diagnostic"][rows]
             .__setitem__(0, "not an object")),
            ("a_key_is_unhashable",
             lambda d: d["meta_data_projection"]["diagnostic"][rows][0]
             .__setitem__("key", ["_wc_gla_synced_at"])),
            ("a_key_digest_disagrees_with_its_name",
             lambda d: d["meta_data_projection"]["diagnostic"][rows][0]
             .__setitem__("key_sha256", "0" * 64)),
            ("a_foreign_key_joins_the_three",
             lambda d: d["meta_data_projection"]["diagnostic"][rows][0]
             .__setitem__("key", "_frp_packing_group")),
            ("index_moved",
             lambda d: d["meta_data_projection"]["diagnostic"][rows][0]
             .__setitem__("readback_index", 9)),
            ("id_dropped",
             lambda d: d["meta_data_projection"]["diagnostic"][rows][0]
             .__setitem__("id", None)),
            ("id_is_a_bool",
             lambda d: d["meta_data_projection"]["diagnostic"][rows][0]
             .__setitem__("id", True)),
            ("value_not_differing",
             lambda d: d["meta_data_projection"]["diagnostic"][rows][0]
             .__setitem__("value_differs", False)),
            ("entry_hashes_equal",
             lambda d: d["meta_data_projection"]["diagnostic"][rows][0]
             .__setitem__("readback_entry_sha256",
                          d["meta_data_projection"]["diagnostic"][rows][0]
                          ["staged_entry_sha256"])),
        ):
            with self.subTest(label=label):
                copy = json.loads(json.dumps(diagnostic))
                mutate(copy)
                self._refused(target, copy)

    def test_a_hand_built_status_row_cannot_invent_the_transition(self):
        """Row 0 of the fixture is the stamp. Relabelling it as the status entry
        must not produce a second status row that satisfies the transition."""
        target, diagnostic = self._pair()
        copy = json.loads(json.dumps(diagnostic))
        detail = copy["meta_data_projection"]["diagnostic"]
        detail["value_changed_entries"][0].update({
            "key": GLA_KEY, "key_sha256": policy.GLA_META_KEY_SHA256,
            "staged_value_sha256": policy.GLA_TRANSIENT_VALUE_SHA256,
            "readback_value_sha256": policy.GLA_STAGED_VALUE_SHA256,
        })
        self._refused(target, copy)

    def test_the_predicates_never_raise_on_a_malformed_diagnostic(self):
        target, _ = self._pair()
        for diagnostic in (None, "", [], {}, {"changed_protected_fields": None},
                           {"meta_data_projection": "x"},
                           {"changed_protected_fields": ["meta_data"],
                            "field_fingerprints": None}):
            with self.subTest(diagnostic=str(diagnostic)[:40]):
                self.assertIs(
                    policy.is_gla_sync_settling(target, diagnostic, True), False)
                self.assertIs(
                    policy.is_gla_sync_transient(target, diagnostic, True), False)

    def test_neither_predicate_stores_or_returns_a_metadata_value(self):
        target, diagnostic = self._pair()
        text = strip_fixed_vocabulary(json.dumps(diagnostic) + json.dumps(target))
        for token in (GLA_STAGED, GLA_TRANSIENT, SYNCED_AT_BEFORE, SYNCED_AT_AFTER,
                      SYNC_HASH_BEFORE, SYNC_HASH_AFTER, SECRET_META_VALUE,
                      SECRET_DESCRIPTION):
            with self.subTest(token=token):
                self.assertNotIn(token, text)


# ---------------------------------------------------------------------------
# Schema 3: the bounded, read-only convergence wait
# ---------------------------------------------------------------------------
class ConvergenceFixture(PlanFixture):
    """Google-sync plans with the clock and the sleeper patched.

    Nothing in these tests waits: sleep() only advances a counter, and monotonic()
    reads that counter. A test that hung would prove the loop is unbounded.
    """

    ENDPOINT = "/products/1455/variations/2056"
    SECOND = "/products/1455/variations/2057"

    def setUp(self):
        super().setUp()
        self.sleeps: list[float] = []
        self.clock = 1_000.0
        self._clock_patches = [
            mock.patch.object(policy, "sleep", self._sleep),
            mock.patch.object(policy, "monotonic", lambda: self.clock),
        ]
        for item in self._clock_patches:
            item.start()

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock += seconds

    def tearDown(self):
        for item in reversed(self._clock_patches):
            item.stop()
        super().tearDown()

    def _store(self):
        return FakeStore(
            {
                self.ENDPOINT: gla_variation_record(1455, 2056),
                self.SECOND: gla_variation_record(1455, 2057),
            },
            classes=[dict(FREIGHT_CLASS_ROW)],
        )

    def _request(self, targets=None):
        return {
            "action": "shipping_class_assign",
            "targets": targets or [
                {"kind": "variation", "product_id": 1455, "variation_id": 2056}],
            "sources": {"policy": "shipping_policy_manifest.json 2026-08-09; FRP Pipe"},
        }

    def _both(self):
        return self._request([
            {"kind": "variation", "product_id": 1455, "variation_id": 2056},
            {"kind": "variation", "product_id": 1455, "variation_id": 2057},
        ])

    def _meta(self, value):
        return {"meta_data": gla_meta_entries(value)}

    def _armed(self, script, targets=None):
        """Stage a plan, then arm the store to serve `script` after each write."""
        store = self._store()
        plan_path, _ = self._stage(store, targets or self._request())
        store.after_write_gets = dict(script)
        store.calls.clear()
        return store, plan_path


class ConvergenceCommitTests(ConvergenceFixture):
    def test_an_exact_readback_succeeds_with_no_sleep_and_no_extra_read(self):
        store, plan_path = self._armed({})
        result = self._commit(store, plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(self.sleeps, [])
        # Exactly two reads of the target: the pre-write read and the read-back.
        self.assertEqual(store.calls.count(("GET", self.ENDPOINT)), 2)
        row = result["outcome"][0]
        self.assertIs(row["written"], True)
        self.assertIs(row["convergence_used"], False)
        self.assertNotIn("convergence_attempts", row)
        self.assertEqual(result["convergence"]["targets_converged"], 0)

    def test_the_exact_transient_converges_within_the_bound_with_one_write(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT), self._meta(GLA_STAGED)]})
        result = self._commit(store, plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        row = result["outcome"][0]
        self.assertIs(row["convergence_used"], True)
        self.assertEqual(row["convergence_attempts"], 1)
        self.assertEqual(row["convergence_elapsed_seconds"], 2.0)
        self.assertEqual(row["convergence_meta_key"], GLA_KEY)
        self.assertEqual(row["final_state"], "exact_staged_protected_state")
        self.assertEqual(row["final_requirement"], "exact_staged_protected_state")
        self.assertEqual(self.sleeps, [2])
        # One write in the entire commit, and it is the fixed payload.
        self.assertEqual(store.writes, [
            ("PUT", self.ENDPOINT, {"shipping_class": policy.FREIGHT_CLASS_SLUG})])
        self.assertEqual(store.calls.count(("GET", self.ENDPOINT)), 3)
        self.assertEqual(result["convergence"]["targets_converged"], 1)
        self.assertEqual(self._lock(plan_path)["status"], "committed_verified")

    def test_several_transient_observations_then_the_staged_state_succeeds(self):
        store, plan_path = self._armed({self.ENDPOINT: [
            self._meta(GLA_TRANSIENT)] * 5 + [self._meta(GLA_STAGED)]})
        result = self._commit(store, plan_path)
        row = result["outcome"][0]
        self.assertIs(row["convergence_used"], True)
        self.assertEqual(row["convergence_attempts"], 5)
        self.assertEqual(row["convergence_elapsed_seconds"], 60.0)
        self.assertEqual(self.sleeps, [2, 4, 8, 16, 30])
        self.assertEqual(len(store.writes), 1)

    def test_the_transient_until_timeout_fails_and_never_becomes_success(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT)] * 20})
        message = self._commit_expecting_failure(store, plan_path)
        self.assertIn("NOT accepted as success", message)
        self.assertNotIn("COMMITTED", message)
        self.assertEqual(self.sleeps, list(policy.CONVERGENCE_SCHEDULE_SECONDS))
        lock = self._lock(plan_path)
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertEqual(lock["reason_class"], "ConvergenceTimeout")
        record = lock["convergence"]
        self.assertEqual(record["final_state"], "pending_not_accepted")
        self.assertEqual(record["final_requirement"], "exact_staged_protected_state")
        self.assertEqual(record["convergence_attempts"], 6)
        self.assertEqual(record["convergence_elapsed_seconds"], 90.0)
        self.assertLessEqual(record["convergence_elapsed_seconds"], 90)
        self.assertEqual(record["max_seconds"], 90)
        self.assertEqual(record["endpoint"], self.ENDPOINT)
        self.assertEqual(record["meta_key"], GLA_KEY)
        self.assertEqual(record["staged_value_sha256"], policy.GLA_STAGED_VALUE_SHA256)
        self.assertEqual(record["transient_value_sha256"],
                         policy.GLA_TRANSIENT_VALUE_SHA256)
        # One write, then reads only. No retry, no rollback, no second attempt.
        self.assertEqual(store.writes, [
            ("PUT", self.ENDPOINT, {"shipping_class": policy.FREIGHT_CLASS_SLUG})])

    def test_the_timeout_receipt_is_bounded_and_says_it_was_not_accepted(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT)] * 20})
        self._commit_expecting_failure(store, plan_path)
        actions = [action for action, _ in self.receipts]
        self.assertIn("woocommerce_shipping_policy_indeterminate_needs_restage", actions)
        self.assertNotIn("woocommerce_shipping_policy_committed", actions)
        evidence = next(text for action, text in self.receipts
                        if action == "woocommerce_shipping_policy_indeterminate_needs_restage")
        self.assertIn("reason_class=ConvergenceTimeout", evidence)
        self.assertIn(f"endpoint={self.ENDPOINT}", evidence)
        self.assertIn(f"meta_key={GLA_KEY}", evidence)
        self.assertIn("convergence_attempts=6", evidence)
        self.assertIn("final_state=pending_not_accepted", evidence)
        self.assertIn(f"staged_value_sha256={policy.GLA_STAGED_VALUE_SHA256}", evidence)

    def test_a_timed_out_plan_is_locked_and_can_never_be_replayed(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT)] * 20})
        self._commit_expecting_failure(store, plan_path)
        store.after_write_gets = {}
        store.writes.clear()
        self.sleeps.clear()
        self._commit_expecting_failure(store, plan_path)
        self.assertEqual(store.writes, [])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self._lock(plan_path)["status"], "indeterminate_needs_restage")

    def test_a_different_value_during_the_wait_fails_immediately(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT), self._meta("error")]})
        message = self._commit_expecting_failure(store, plan_path)
        self.assertIn("bounded Google-sync wait", message)
        self.assertIn("meta_data", message)
        self.assertEqual(self.sleeps, [2])
        self.assertEqual(len(store.writes), 1)
        lock = self._lock(plan_path)
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertEqual(lock["reason_class"], "ProtectedStateMismatch")
        self.assertEqual(lock["diagnostic"]["phase"], "convergence")

    def test_another_protected_field_moving_during_the_wait_fails_immediately(self):
        store, plan_path = self._armed({self.ENDPOINT: [
            self._meta(GLA_TRANSIENT),
            {**self._meta(GLA_TRANSIENT), "weight": "99.9"},
        ]})
        message = self._commit_expecting_failure(store, plan_path)
        self.assertIn("weight", message)
        self.assertEqual(self.sleeps, [2])
        diagnostic = self._lock(plan_path)["diagnostic"]
        self.assertEqual(diagnostic["phase"], "convergence")
        self.assertEqual(diagnostic["changed_protected_fields"], ["meta_data", "weight"])
        self.assertEqual(len(store.writes), 1)

    def test_any_other_metadata_movement_during_the_wait_fails_immediately(self):
        movements = {
            "added": gla_meta_entries(GLA_TRANSIENT) + [
                {"id": 9500, "key": "_added_by_a_plugin", "value": SECRET_META_VALUE}],
            "removed": gla_meta_entries(GLA_TRANSIENT)[1:],
            "reordered": list(reversed(gla_meta_entries(GLA_TRANSIENT))),
            "identity_changed": [{"id": 8801, "key": "_renamed", "value": "yes"}]
            + gla_meta_entries(GLA_TRANSIENT)[1:],
            "duplicated": gla_meta_entries(GLA_TRANSIENT) + [
                {"id": GLA_ID, "key": GLA_KEY, "value": GLA_TRANSIENT}],
        }
        for label, entries in movements.items():
            with self.subTest(label=label):
                store, plan_path = self._armed({self.ENDPOINT: [
                    self._meta(GLA_TRANSIENT), {"meta_data": entries}]})
                self._commit_expecting_failure(store, plan_path)
                lock = self._lock(plan_path)
                self.assertEqual(lock["status"], "indeterminate_needs_restage")
                self.assertEqual(lock["reason_class"], "ProtectedStateMismatch")
                self.assertEqual(lock["diagnostic"]["phase"], "convergence")
                self.assertEqual(len(store.writes), 1)
                self.assertNotIn(SECRET_META_VALUE, json.dumps(lock))

    def test_a_shipping_class_drift_during_the_wait_fails_immediately(self):
        store, plan_path = self._armed({self.ENDPOINT: [
            self._meta(GLA_TRANSIENT),
            {**self._meta(GLA_TRANSIENT), "shipping_class": "some-other-class"},
        ]})
        message = self._commit_expecting_failure(store, plan_path)
        self.assertIn("no longer carries the approved shipping class", message)
        self.assertEqual(self.sleeps, [2])
        lock = self._lock(plan_path)
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertIs(lock["diagnostic"]["shipping_class_matches_plan"], False)
        # No repair write of any kind.
        self.assertEqual(store.writes, [
            ("PUT", self.ENDPOINT, {"shipping_class": policy.FREIGHT_CLASS_SLUG})])

    def test_an_ineligible_target_never_waits_at_all(self):
        """No Google-sync entry staged means no wait is available to this target."""
        store = FakeStore(
            {"/products/1423/variations/1424": variation_record(1423, 1424)},
            classes=[dict(FREIGHT_CLASS_ROW)],
        )
        plan_path, result = self._stage(store, {
            "action": "shipping_class_assign",
            "targets": [{"kind": "variation", "product_id": 1423, "variation_id": 1424}],
            "sources": {"policy": "manifest"},
        })
        self.assertIs(result["targets"][0]["gla_convergence_eligible"], False)
        store.corrupt_on_write = {"/products/1423/variations/1424": {
            "meta_data": meta_entries() + [
                {"id": GLA_ID, "key": GLA_KEY, "value": GLA_TRANSIENT}]}}
        self._commit_expecting_failure(store, plan_path)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self._lock(plan_path)["diagnostic"]["phase"], "post_write")

    def test_the_second_target_starts_only_after_the_first_is_exact(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT), self._meta(GLA_STAGED)]},
            targets=self._both(),
        )
        result = self._commit(store, plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        first = store.calls.index(("PUT", self.ENDPOINT))
        second = store.calls.index(("PUT", self.SECOND))
        self.assertLess(first, second)
        # Between the two writes: the first target's read-back, its one
        # convergence read, then the second target's pre-write read. Nothing else.
        self.assertEqual(store.calls[first + 1:second], [
            ("GET", self.ENDPOINT), ("GET", self.ENDPOINT), ("GET", self.SECOND)])
        self.assertIs(result["outcome"][0]["convergence_used"], True)
        self.assertIs(result["outcome"][1]["convergence_used"], False)

    def test_a_timeout_never_starts_the_second_target(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT)] * 20}, targets=self._both())
        self._commit_expecting_failure(store, plan_path)
        self.assertNotIn(("PUT", self.SECOND), store.calls)
        self.assertNotIn(("GET", self.SECOND), store.calls)
        self.assertEqual(len(store.writes), 1)
        self.assertEqual(store.records[self.SECOND]["shipping_class"], "")

    def test_a_mismatch_during_the_wait_never_starts_the_second_target(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT), self._meta("error")]},
            targets=self._both(),
        )
        self._commit_expecting_failure(store, plan_path)
        self.assertNotIn(("PUT", self.SECOND), store.calls)
        self.assertEqual(store.records[self.SECOND]["shipping_class"], "")

    def test_the_wait_writes_nothing_and_never_rolls_back(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT)] * 20})
        self._commit_expecting_failure(store, plan_path)
        methods = {method for method, _, _ in store.writes}
        self.assertEqual(methods, {"PUT"})
        for _, _, payload in store.writes:
            self.assertEqual(set(payload), {"shipping_class"})
            self.assertEqual(payload["shipping_class"], policy.FREIGHT_CLASS_SLUG)
        # The assignment itself stands; the tool reports and stops.
        self.assertEqual(store.records[self.ENDPOINT]["shipping_class"],
                         policy.FREIGHT_CLASS_SLUG)

    def test_the_schedule_is_the_only_pacing_and_is_never_exceeded(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT)] * 50})
        self._commit_expecting_failure(store, plan_path)
        self.assertEqual(self.sleeps, [2, 4, 8, 16, 30, 30])
        self.assertEqual(sum(self.sleeps), 90)
        self.assertLessEqual(sum(self.sleeps), policy.CONVERGENCE_CEILING_SECONDS)
        # Six waits, six convergence reads, plus the pre-write read and read-back.
        self.assertEqual(store.calls.count(("GET", self.ENDPOINT)), 8)

    def test_no_real_time_passes_because_the_clock_and_sleeper_are_injected(self):
        started = time.monotonic()
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT)] * 20})
        self._commit_expecting_failure(store, plan_path)
        self.assertEqual(sum(self.sleeps), 90)
        self.assertLess(time.monotonic() - started, 10)

    def test_no_record_of_the_wait_carries_a_raw_metadata_value(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT)] * 20})
        message = self._commit_expecting_failure(store, plan_path)
        text = strip_fixed_vocabulary(
            message + json.dumps(self._lock(plan_path)) + json.dumps(self.receipts))
        for token in (GLA_STAGED, GLA_TRANSIENT, SECRET_META_VALUE, SECRET_DESCRIPTION,
                      "ck_abcdefghijklmnopqrstuvwxyz", "cs_abcdefghijklmnopqrstuvwxyz",
                      "SKU-2056", "Authorization"):
            with self.subTest(token=token):
                self.assertNotIn(token, text)
        # It still names the key and both fixed digests.
        self.assertIn(GLA_KEY, json.dumps(self._lock(plan_path)))

    def test_a_successful_convergence_record_carries_no_raw_value_either(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT), self._meta(GLA_STAGED)]})
        result = self._commit(store, plan_path)
        text = strip_fixed_vocabulary(json.dumps(result) + json.dumps(self.receipts))
        for token in (GLA_STAGED, GLA_TRANSIENT, SECRET_META_VALUE, SECRET_DESCRIPTION):
            with self.subTest(token=token):
                self.assertNotIn(token, text)
        self.assertIn(GLA_KEY, json.dumps(result))

    def test_an_unexpected_read_error_during_the_wait_stays_sanitized(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT), self._meta(GLA_STAGED)]})
        original = store.api_get
        state = {"seen": 0}

        def failing_get(endpoint, params=None, vault=None):
            if endpoint == self.ENDPOINT:
                state["seen"] += 1
                if state["seen"] >= 3:      # pre-write, read-back, then the wait
                    raise wc.WooError(
                        "GET failed with HTTP 500: " + SECRET_DESCRIPTION, status=500,
                        code="rest_error")
            return original(endpoint, params, vault)

        with mock.patch.object(policy.wc, "api_get", side_effect=failing_get), \
             mock.patch.object(policy.wc, "api_request", side_effect=store.api_request), \
             mock.patch.object(policy.wc, "load_vault", return_value=_vault()):
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        lock = self._lock(plan_path)
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertEqual(lock["reason_class"], "WooError")
        self.assertEqual(lock["http_status"], 500)
        self.assertNotIn(SECRET_DESCRIPTION, json.dumps(lock))
        self.assertEqual(len(store.writes), 1)

    def test_the_wait_still_requires_his_clear_go(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT), self._meta(GLA_STAGED)]})
        with mock.patch.object(policy.wc, "load_vault") as load, \
             mock.patch.object(policy.wc, "api_get") as get, \
             mock.patch.object(policy.wc, "api_request") as write:
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="approved but wait"))
            load.assert_not_called()
            get.assert_not_called()
            write.assert_not_called()
        self.assertEqual(self.sleeps, [])
        self.assertEqual(store.writes, [])


# ---------------------------------------------------------------------------
# Schema 4: staging a pending baseline -- the bounded read-only stability proof
# ---------------------------------------------------------------------------
class PendingBaselineFixture(ConvergenceFixture):
    """The deadlocked shape, reproduced: a resource resting at the pending digest.

    Live evidence on /products/1455/variations/1457 and the next five blank FRP
    Pipe candidates: they sit there and stay there, long past the 90-second
    transient window. Schema 3 refused to stage any of them.
    """

    def _store(self):
        return FakeStore(
            {
                self.ENDPOINT: gla_variation_record(1455, 2056, GLA_TRANSIENT),
                self.SECOND: gla_variation_record(1455, 2057, GLA_TRANSIENT),
            },
            classes=[dict(FREIGHT_CLASS_ROW)],
        )

    def _armed(self, script, targets=None):
        store, plan_path = super()._armed(script, targets)
        self.sleeps.clear()
        return store, plan_path

    def _stage_with_drift(self, store, patches, data=None):
        """Stage while the store mutates the record before its Nth read of it.

        Read 1 is staging's own first read; 2 and 3 are the stability observations.
        """
        original = store.api_get
        seen = {"count": 0}

        def drifting(endpoint, params=None, vault=None):
            if endpoint == self.ENDPOINT:
                seen["count"] += 1
                patch = patches.get(seen["count"])
                if patch:
                    store.records[endpoint].update(patch)
            return original(endpoint, params, vault)

        path = self._input(data or self._request())
        with mock.patch.object(policy.wc, "api_get", side_effect=drifting), \
             mock.patch.object(policy.wc, "api_request", side_effect=store.api_request):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                policy.command_stage(argparse.Namespace(input=str(path)))
        return json.loads(output.getvalue())


class PendingBaselineStagingTests(PendingBaselineFixture):
    def test_a_pending_baseline_stages_after_three_exactly_matching_reads(self):
        """The repair itself: schema 3 refused this outright."""
        store = self._store()
        plan_path, result = self._stage(store, self._request())
        self.assertEqual(result["status"], "STAGED_NOT_COMMITTED")
        self.assertEqual(result["targets"][0]["gla_baseline_mode"], "pending_baseline")
        self.assertIs(result["targets"][0]["gla_convergence_eligible"], False)
        self.assertEqual(result["baseline_modes"],
                         {"absent": 0, "settled_baseline": 0, "pending_baseline": 1})
        # Three fresh reads of the same exact resource, paced only by the schedule.
        self.assertEqual(store.calls.count(("GET", self.ENDPOINT)), 3)
        self.assertEqual(self.sleeps, [2, 4])
        self.assertEqual(sum(self.sleeps), 6)
        # Read-only: staging wrote nothing to the store.
        self.assertEqual(store.writes, [])
        proof = json.loads(plan_path.read_text(encoding="utf-8"))["targets"][0]
        evidence = proof["gla_pending_stability"]
        self.assertEqual(evidence, {
            "mode": "pending_baseline", "observations": 3,
            "schedule_seconds": [2, 4], "max_seconds": 6, "elapsed_seconds": 6.0,
            "value_sha256": policy.GLA_TRANSIENT_VALUE_SHA256,
            "meta_entry_index": 3, "meta_entry_id": GLA_ID, "stable": True,
        })
        policy.load_plan(str(plan_path))

    def test_the_proof_holds_no_raw_metadata_value(self):
        store = self._store()
        plan_path, result = self._stage(store, self._request())
        text = strip_fixed_vocabulary(
            plan_path.read_text(encoding="utf-8") + json.dumps(result))
        self.assertIn(GLA_KEY, text)
        for token in (GLA_STAGED, GLA_TRANSIENT, SECRET_META_VALUE, SECRET_DESCRIPTION):
            with self.subTest(token=token):
                self.assertNotIn(token, text)

    def test_a_baseline_that_moves_between_observations_refuses_and_stages_nothing(self):
        drifts = {
            "settles_on_the_second_read": {"meta_data": gla_meta_entries(GLA_STAGED)},
            "another_protected_field": {"weight": "99.9"},
            "modification_stamp": {"date_modified_gmt": "2026-08-10T11:22:33"},
            "metadata_entry_added": {
                "meta_data": gla_meta_entries(GLA_TRANSIENT)
                + [{"id": 9500, "key": "_added", "value": SECRET_META_VALUE}]},
            "metadata_entry_removed": {
                "meta_data": gla_meta_entries(GLA_TRANSIENT)[1:]},
            "metadata_reordered": {
                "meta_data": list(reversed(gla_meta_entries(GLA_TRANSIENT)))},
            "entry_id_changed": {
                "meta_data": meta_entries()
                + [{"id": 63099, "key": GLA_KEY, "value": GLA_TRANSIENT}]},
            "shipping_class": {"shipping_class": "some-other-class"},
            "undiagnosed_value": {"meta_data": gla_meta_entries("error")},
        }
        for read_index in (2, 3):
            for label, patch in drifts.items():
                with self.subTest(read=read_index, label=label):
                    store = self._store()
                    with self.assertRaises(policy.ShippingPolicyError) as caught:
                        self._stage_with_drift(store, {read_index: patch})
                    self.assertEqual(store.writes, [])
                    self.assertEqual(list(self.plan_dir.glob("*.json")), [])
                    message = strip_fixed_vocabulary(str(caught.exception))
                    self.assertNotIn(SECRET_META_VALUE, message)
                    self.assertNotIn(GLA_TRANSIENT, message)

    def test_a_drifting_baseline_never_leaves_a_partial_plan_behind(self):
        store = self._store()
        with self.assertRaises(policy.ShippingPolicyError):
            self._stage_with_drift(
                store, {3: {"meta_data": gla_meta_entries(GLA_STAGED)}},
                data=self._both())
        self.assertEqual(list(self.plan_dir.glob("*.json")), [])
        self.assertEqual(store.writes, [])

    def test_the_stability_schedule_is_the_only_pacing_and_is_never_exceeded(self):
        store = self._store()
        self._stage(store, self._request())
        self.assertEqual(self.sleeps, list(policy.PENDING_STABILITY_SCHEDULE_SECONDS))
        self.assertLessEqual(sum(self.sleeps), policy.PENDING_STABILITY_CEILING_SECONDS)

    def test_an_absent_baseline_stages_with_no_proof_and_no_extra_read(self):
        """Only a pending baseline pays for the proof."""
        store = FakeStore(
            {"/products/1423/variations/1424": variation_record(1423, 1424)},
            classes=[dict(FREIGHT_CLASS_ROW)],
        )
        plan_path, result = self._stage(store, {
            "action": "shipping_class_assign",
            "targets": [{"kind": "variation", "product_id": 1423, "variation_id": 1424}],
            "sources": {"policy": "manifest"},
        })
        self.assertEqual(result["targets"][0]["gla_baseline_mode"], "absent")
        self.assertIsNone(result["targets"][0]["gla_pending_stability"])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(store.calls.count(("GET", "/products/1423/variations/1424")), 1)

    def test_a_settled_baseline_stages_with_no_proof_and_no_extra_read(self):
        store = FakeStore(
            {self.ENDPOINT: gla_variation_record(1455, 2056, GLA_STAGED)},
            classes=[dict(FREIGHT_CLASS_ROW)],
        )
        _, result = self._stage(store, self._request())
        self.assertEqual(result["targets"][0]["gla_baseline_mode"], "settled_baseline")
        self.assertIsNone(result["targets"][0]["gla_pending_stability"])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(store.calls.count(("GET", self.ENDPOINT)), 1)


class PendingBaselinePlanIntegrityTests(PendingBaselineFixture):
    """A rehashed edit may not invent, move or flip a baseline."""

    def _refuse(self, plan_path):
        with mock.patch.object(policy.wc, "load_vault") as load, \
             mock.patch.object(policy.wc, "api_get") as get, \
             mock.patch.object(policy.wc, "api_request") as write:
            with self.assertRaises(policy.ShippingPolicyError) as caught:
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
            load.assert_not_called()
            get.assert_not_called()
            write.assert_not_called()
        self.assertFalse(policy.lock_path(plan_path.resolve()).exists())
        return str(caught.exception)

    def test_a_rehashed_plan_cannot_flip_a_pending_target_to_settled(self):
        store = self._store()
        plan_path, _ = self._stage(store, self._request())
        self._rewrite(plan_path, lambda plan: plan["targets"][0].update(
            {"gla_baseline_mode": "settled_baseline", "gla_convergence_eligible": True,
             "gla_pending_stability": None}), rehash=True)
        self._refuse(plan_path)

    def test_a_rehashed_plan_cannot_invent_a_pending_baseline(self):
        """An absent-baseline target cannot be given a proof it never earned."""
        store = FakeStore(
            {"/products/1423/variations/1424": variation_record(1423, 1424)},
            classes=[dict(FREIGHT_CLASS_ROW)],
        )
        plan_path, _ = self._stage(store, {
            "action": "shipping_class_assign",
            "targets": [{"kind": "variation", "product_id": 1423, "variation_id": 1424}],
            "sources": {"policy": "manifest"},
        })
        self._rewrite(plan_path, lambda plan: plan["targets"][0].update(
            {"gla_baseline_mode": "pending_baseline", "gla_convergence_eligible": False,
             "gla_pending_stability": {
                 "mode": "pending_baseline", "observations": 3,
                 "schedule_seconds": [2, 4], "max_seconds": 6, "elapsed_seconds": 6.0,
                 "value_sha256": policy.GLA_TRANSIENT_VALUE_SHA256,
                 "meta_entry_index": 3, "meta_entry_id": GLA_ID, "stable": True}}),
            rehash=True)
        self._refuse(plan_path)

    def test_a_rehashed_plan_cannot_weaken_the_proof_it_carries(self):
        store = self._store()
        plan_path, _ = self._stage(store, self._request())
        original = json.loads(plan_path.read_text(encoding="utf-8"))
        proof = original["targets"][0]["gla_pending_stability"]
        for label, patch in (
            ("dropped", None),
            ("not_stable", {"stable": False}),
            ("one_observation", {"observations": 1}),
            ("no_observations", {"observations": 0}),
            ("widened_schedule", {"schedule_seconds": [600, 600], "max_seconds": 1200}),
            ("float_schedule", {"schedule_seconds": [2.0, 4.0]}),
            ("shorter_schedule", {"schedule_seconds": [2], "max_seconds": 2}),
            ("no_time_passed", {"elapsed_seconds": 0}),
            ("less_than_the_schedule", {"elapsed_seconds": 5.9}),
            ("absurd_elapsed", {"elapsed_seconds": 999999}),
            ("settled_digest", {"value_sha256": policy.GLA_STAGED_VALUE_SHA256}),
            ("blank_digest", {"value_sha256": "0" * 64}),
            ("other_entry_id", {"meta_entry_id": 1}),
            ("other_entry_index", {"meta_entry_index": 0}),
            ("other_mode", {"mode": "settled_baseline"}),
            ("extra_field", {"waived": True}),
        ):
            with self.subTest(label=label):
                value = None if patch is None else {**proof, **patch}
                self._rewrite(
                    plan_path,
                    lambda plan, v=value: plan["targets"][0].__setitem__(
                        "gla_pending_stability", v),
                    rehash=True)
                self._refuse(plan_path)
                # Restore for the next case.
                self._rewrite(
                    plan_path,
                    lambda plan: plan["targets"][0].__setitem__(
                        "gla_pending_stability", proof),
                    rehash=True)

    def test_an_unrehashed_edit_still_fails_the_hash_check_first(self):
        store = self._store()
        plan_path, _ = self._stage(store, self._request())
        self._rewrite(plan_path, lambda plan: plan["targets"][0].__setitem__(
            "gla_baseline_mode", "settled_baseline"), rehash=False)
        self.assertIn("hash", self._refuse(plan_path).casefold())

    def test_a_settled_target_may_not_carry_a_proof(self):
        store = FakeStore(
            {self.ENDPOINT: gla_variation_record(1455, 2056, GLA_STAGED)},
            classes=[dict(FREIGHT_CLASS_ROW)],
        )
        plan_path, _ = self._stage(store, self._request())
        self._rewrite(plan_path, lambda plan: plan["targets"][0].__setitem__(
            "gla_pending_stability", {
                "mode": "pending_baseline", "observations": 3,
                "schedule_seconds": [2, 4], "max_seconds": 6, "elapsed_seconds": 6.0,
                "value_sha256": policy.GLA_TRANSIENT_VALUE_SHA256,
                "meta_entry_index": 3, "meta_entry_id": GLA_ID, "stable": True}),
            rehash=True)
        self._refuse(plan_path)


class PendingBaselineCommitTests(PendingBaselineFixture):
    def test_the_baseline_is_reproven_freshly_before_the_put(self):
        """The plan is stale the moment the resource settles. Refuse, never write."""
        store = self._store()
        plan_path, _ = self._stage(store, self._request())
        # The [2, 4] sleeps belong to the required pending-baseline STAGING
        # stability proof. Clear them so this assertion measures commit preflight
        # only: a stale plan must refuse immediately, without another wait.
        self.assertEqual(self.sleeps, [2, 4])
        self.sleeps.clear()
        store.records[self.ENDPOINT]["meta_data"] = gla_meta_entries(GLA_STAGED)
        message = self._commit_expecting_failure(store, plan_path)
        self.assertIn("changed after review", message)
        self.assertEqual(store.writes, [])
        self.assertEqual(self._lock(plan_path)["status"], "indeterminate_needs_restage")
        self.assertEqual(self.sleeps, [])

    def test_the_preflight_proof_is_exact_about_the_entry_itself(self):
        """A unit check of the pre-write proof: same mode, index, id and digest."""
        record = gla_variation_record(1455, 2056, GLA_TRANSIENT)
        projection = policy.metadata_projection(record)
        target = {
            "before_meta_data_projection": projection,
            "gla_baseline_mode": "pending_baseline",
        }
        self.assertEqual(
            policy.prove_staged_baseline_live(target, record, self.ENDPOINT),
            "pending_baseline")
        for label, moved in (
            ("settled", gla_variation_record(1455, 2056, GLA_STAGED)),
            ("absent", variation_record(1455, 2056)),
            ("undiagnosed", gla_variation_record(1455, 2056, "error")),
            ("other_id", variation_record(1455, 2056, meta_data=meta_entries() + [
                {"id": 63099, "key": GLA_KEY, "value": GLA_TRANSIENT}])),
            ("other_index", variation_record(1455, 2056, meta_data=[
                {"id": GLA_ID, "key": GLA_KEY, "value": GLA_TRANSIENT}]
                + meta_entries())),
        ):
            with self.subTest(label=label):
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.prove_staged_baseline_live(target, moved, self.ENDPOINT)

    def test_an_unchanged_pending_state_is_success_immediately(self):
        """Requirement, stated plainly: a pending baseline must NEVER wait for
        settlement merely to call an unchanged pending state successful."""
        store, plan_path = self._armed({})
        result = self._commit(store, plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        row = result["outcome"][0]
        self.assertIs(row["written"], True)
        self.assertEqual(row["baseline_mode"], "pending_baseline")
        self.assertIs(row["convergence_used"], False)
        self.assertEqual(row["final_state"], "exact_staged_pending_state")
        # No confirmation reads and no waiting at all.
        self.assertEqual(self.sleeps, [])
        self.assertEqual(store.calls.count(("GET", self.ENDPOINT)), 2)
        self.assertEqual(store.writes, [
            ("PUT", self.ENDPOINT, {"shipping_class": policy.FREIGHT_CLASS_SLUG})])
        self.assertEqual(self._lock(plan_path)["status"], "committed_verified")

    def test_a_settling_write_is_confirmed_before_it_succeeds(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_STAGED)] * 3})
        result = self._commit(store, plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        row = result["outcome"][0]
        self.assertEqual(row["baseline_mode"], "pending_baseline")
        self.assertIs(row["convergence_used"], True)
        self.assertEqual(row["convergence_attempts"], 2)
        self.assertEqual(row["convergence_elapsed_seconds"], 6.0)
        self.assertEqual(row["final_state"], "confirmed_stable_settled_state")
        self.assertEqual(row["final_requirement"],
                         "exact_staged_pending_state_or_confirmed_settled_state")
        self.assertEqual(self.sleeps, [2, 4])
        # The read-back plus both confirming reads. One write, and only one.
        self.assertEqual(store.calls.count(("GET", self.ENDPOINT)), 4)
        self.assertEqual(store.writes, [
            ("PUT", self.ENDPOINT, {"shipping_class": policy.FREIGHT_CLASS_SLUG})])
        self.assertEqual(result["convergence"]["pending_settled_confirmed_targets"], 1)

    def test_a_settlement_that_does_not_hold_fails_and_locks(self):
        cases = {
            "flips_back_on_the_first_confirmation": [
                self._meta(GLA_STAGED), self._meta(GLA_TRANSIENT)],
            "flips_back_on_the_second_confirmation": [
                self._meta(GLA_STAGED), self._meta(GLA_STAGED),
                self._meta(GLA_TRANSIENT)],
            "moves_on_to_an_undiagnosed_value": [
                self._meta(GLA_STAGED), self._meta("error")],
            "another_protected_field_moves": [
                self._meta(GLA_STAGED),
                {**self._meta(GLA_STAGED), "weight": "99.9"}],
            "a_metadata_entry_appears": [
                self._meta(GLA_STAGED),
                {"meta_data": gla_meta_entries(GLA_STAGED) + [
                    {"id": 9500, "key": "_added", "value": SECRET_META_VALUE}]}],
            "the_modification_stamp_moves_again": [
                self._meta(GLA_STAGED),
                {**self._meta(GLA_STAGED), "date_modified_gmt": "2026-08-10T12:00:00"}],
            "the_class_drifts_away": [
                self._meta(GLA_STAGED),
                {**self._meta(GLA_STAGED), "shipping_class": "some-other-class"}],
        }
        for label, script in cases.items():
            with self.subTest(label=label):
                store, plan_path = self._armed({self.ENDPOINT: script})
                message = self._commit_expecting_failure(store, plan_path)
                lock = self._lock(plan_path)
                self.assertEqual(lock["status"], "indeterminate_needs_restage")
                self.assertEqual(lock["reason_class"], "ProtectedStateMismatch")
                self.assertEqual(lock["diagnostic"]["phase"],
                                 "pending_settle_confirmation")
                self.assertNotIn("COMMITTED", message)
                # One write, then reads only. No retry, no rollback, no repair.
                self.assertEqual(store.writes, [
                    ("PUT", self.ENDPOINT,
                     {"shipping_class": policy.FREIGHT_CLASS_SLUG})])
                self.assertNotIn(SECRET_META_VALUE, json.dumps(lock))
                self.assertNotIn(GLA_TRANSIENT,
                                 strip_fixed_vocabulary(json.dumps(lock) + message))

    def test_a_class_drift_during_the_confirmation_says_so(self):
        store, plan_path = self._armed({self.ENDPOINT: [
            self._meta(GLA_STAGED),
            {**self._meta(GLA_STAGED), "shipping_class": "some-other-class"}]})
        message = self._commit_expecting_failure(store, plan_path)
        self.assertIn("no longer carries the approved shipping class", message)
        self.assertIs(self._lock(plan_path)["diagnostic"]["shipping_class_matches_plan"],
                      False)

    def test_any_other_post_write_movement_fails_before_any_confirmation(self):
        cases = {
            "undiagnosed_value": self._meta("error"),
            "another_protected_field": {**self._meta(GLA_TRANSIENT), "weight": "99.9"},
            "metadata_entry_added": {"meta_data": gla_meta_entries(GLA_TRANSIENT) + [
                {"id": 9500, "key": "_added", "value": SECRET_META_VALUE}]},
            "metadata_entry_removed": {
                "meta_data": gla_meta_entries(GLA_TRANSIENT)[1:]},
            "metadata_reordered": {
                "meta_data": list(reversed(gla_meta_entries(GLA_TRANSIENT)))},
            "entry_id_changed": {"meta_data": meta_entries() + [
                {"id": 63099, "key": GLA_KEY, "value": GLA_TRANSIENT}]},
            "entry_duplicated": {"meta_data": gla_meta_entries(GLA_TRANSIENT) + [
                {"id": GLA_ID, "key": GLA_KEY, "value": GLA_TRANSIENT}]},
        }
        for label, patch in cases.items():
            with self.subTest(label=label):
                store, plan_path = self._armed({self.ENDPOINT: [patch]})
                self._commit_expecting_failure(store, plan_path)
                lock = self._lock(plan_path)
                self.assertEqual(lock["status"], "indeterminate_needs_restage")
                self.assertEqual(lock["diagnostic"]["phase"], "post_write")
                # It failed on the read-back; no confirmation was ever attempted.
                self.assertEqual(self.sleeps, [])
                self.assertEqual(len(store.writes), 1)
                self.assertNotIn(SECRET_META_VALUE, json.dumps(lock))

    def test_a_pending_target_never_reaches_the_ninety_second_settled_wait(self):
        """The settled path is 90 seconds; the pending path is 6. They are separate."""
        store, plan_path = self._armed({self.ENDPOINT: [self._meta(GLA_STAGED)] * 3})
        self._commit(store, plan_path)
        self.assertEqual(self.sleeps, [2, 4])
        self.assertNotEqual(self.sleeps, list(policy.CONVERGENCE_SCHEDULE_SECONDS))
        self.assertLessEqual(sum(self.sleeps),
                             policy.PENDING_SETTLE_CONFIRM_CEILING_SECONDS)

    def test_a_get_error_during_the_confirmation_is_sanitised_and_locks(self):
        store, plan_path = self._armed({self.ENDPOINT: [self._meta(GLA_STAGED)] * 3})
        original = store.api_get
        seen = {"count": 0}

        def failing_get(endpoint, params=None, vault=None):
            if endpoint == self.ENDPOINT:
                seen["count"] += 1
                if seen["count"] >= 3:     # pre-write, read-back, then confirmation
                    raise wc.WooError("GET failed with HTTP 500: " + SECRET_DESCRIPTION,
                                      status=500, code="rest_error")
            return original(endpoint, params, vault)

        with mock.patch.object(policy.wc, "api_get", side_effect=failing_get), \
             mock.patch.object(policy.wc, "api_request", side_effect=store.api_request), \
             mock.patch.object(policy.wc, "load_vault", return_value=_vault()):
            with self.assertRaises(policy.ShippingPolicyError):
                policy.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"))
        lock = self._lock(plan_path)
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertEqual(lock["reason_class"], "WooError")
        self.assertEqual(lock["http_status"], 500)
        self.assertNotIn(SECRET_DESCRIPTION, json.dumps(lock))
        self.assertEqual(len(store.writes), 1)

    def test_a_failed_confirmation_can_never_be_replayed(self):
        store, plan_path = self._armed({self.ENDPOINT: [
            self._meta(GLA_STAGED), self._meta(GLA_TRANSIENT)]})
        self._commit_expecting_failure(store, plan_path)
        store.after_write_gets = {}
        store.writes.clear()
        self.sleeps.clear()
        self._commit_expecting_failure(store, plan_path)
        self.assertEqual(store.writes, [])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self._lock(plan_path)["status"], "indeterminate_needs_restage")

    def test_the_second_target_starts_only_after_the_first_is_confirmed(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_STAGED)] * 3}, targets=self._both())
        result = self._commit(store, plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        first = store.calls.index(("PUT", self.ENDPOINT))
        second = store.calls.index(("PUT", self.SECOND))
        self.assertLess(first, second)
        # Read-back, both confirming reads, then the second target's pre-write read.
        self.assertEqual(store.calls[first + 1:second], [
            ("GET", self.ENDPOINT), ("GET", self.ENDPOINT), ("GET", self.ENDPOINT),
            ("GET", self.SECOND)])

    def test_a_failed_confirmation_never_starts_the_second_target(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_STAGED), self._meta(GLA_TRANSIENT)]},
            targets=self._both())
        self._commit_expecting_failure(store, plan_path)
        self.assertNotIn(("PUT", self.SECOND), store.calls)
        self.assertEqual(store.records[self.SECOND]["shipping_class"], "")
        self.assertEqual(len(store.writes), 1)

    def test_exactly_one_put_and_no_metadata_write_on_any_pending_path(self):
        for label, script in (
            ("unchanged", {}),
            ("settles", {self.ENDPOINT: [self._meta(GLA_STAGED)] * 3}),
            ("fails", {self.ENDPOINT: [self._meta(GLA_STAGED),
                                       self._meta(GLA_TRANSIENT)]}),
        ):
            with self.subTest(label=label):
                store, plan_path = self._armed(script)
                try:
                    self._commit(store, plan_path)
                except policy.ShippingPolicyError:
                    pass
                self.assertEqual(len(store.writes), 1)
                method, endpoint, payload = store.writes[0]
                self.assertEqual(method, "PUT")
                self.assertEqual(endpoint, self.ENDPOINT)
                self.assertEqual(payload,
                                 {"shipping_class": policy.FREIGHT_CLASS_SLUG})
                # The assignment stands either way; nothing is ever rolled back.
                self.assertEqual(store.records[self.ENDPOINT]["shipping_class"],
                                 policy.FREIGHT_CLASS_SLUG)

    def test_the_pending_paths_still_require_his_clear_go(self):
        store, plan_path = self._armed({self.ENDPOINT: [self._meta(GLA_STAGED)] * 3})
        for approval in ("approved but wait", "hold", "APPROVED?", "", None):
            with self.subTest(approval=repr(approval)):
                with mock.patch.object(policy.wc, "load_vault") as load, \
                     mock.patch.object(policy.wc, "api_get") as get, \
                     mock.patch.object(policy.wc, "api_request") as write:
                    with self.assertRaises(policy.ShippingPolicyError):
                        policy.command_commit(argparse.Namespace(
                            plan=str(plan_path), approval=approval))
                    load.assert_not_called()
                    get.assert_not_called()
                    write.assert_not_called()
        self.assertEqual(self.sleeps, [])
        self.assertEqual(store.writes, [])

    def test_no_successful_pending_record_carries_a_raw_metadata_value(self):
        store, plan_path = self._armed({self.ENDPOINT: [self._meta(GLA_STAGED)] * 3})
        result = self._commit(store, plan_path)
        text = strip_fixed_vocabulary(
            json.dumps(result) + json.dumps(self.receipts)
            + json.dumps(self._lock(plan_path)))
        for token in (GLA_STAGED, GLA_TRANSIENT, SECRET_META_VALUE, SECRET_DESCRIPTION,
                      "ck_abcdefghijklmnopqrstuvwxyz", "SKU-2056"):
            with self.subTest(token=token):
                self.assertNotIn(token, text)
        self.assertIn(GLA_KEY, json.dumps(result))

    def test_the_committed_receipt_reports_the_baselines_it_used(self):
        store, plan_path = self._armed({self.ENDPOINT: [self._meta(GLA_STAGED)] * 3})
        self._commit(store, plan_path)
        evidence = next(text for action, text in self.receipts
                        if action == "woocommerce_shipping_policy_committed")
        self.assertIn("baseline_pending_baseline_targets=1", evidence)
        self.assertIn("baseline_settled_baseline_targets=0", evidence)
        self.assertIn("baseline_absent_targets=0", evidence)
        self.assertIn("pending_settled_confirmed_targets=1", evidence)


# ---------------------------------------------------------------------------
# Schema 5: the measured three-entry settlement, end to end
# ---------------------------------------------------------------------------
class TripletSettlementFixture(PendingBaselineFixture):
    """The live 2026-08-11 shape: a pending resource carrying all three Google
    entries, whose one approved PUT settles the status, the save stamp and the
    content hash together."""

    ENDPOINT = "/products/1455/variations/1476"
    SECOND = "/products/1455/variations/1477"

    def _store(self):
        return FakeStore(
            {
                self.ENDPOINT: triplet_variation_record(1455, 1476),
                self.SECOND: triplet_variation_record(1455, 1477),
            },
            classes=[dict(FREIGHT_CLASS_ROW)],
        )

    def _request(self, targets=None):
        return {
            "action": "shipping_class_assign",
            "targets": targets or [
                {"kind": "variation", "product_id": 1455, "variation_id": 1476}],
            "sources": {"policy": "shipping_policy_manifest.json 2026-08-11; FRP Pipe"},
        }

    def _both(self):
        return self._request([
            {"kind": "variation", "product_id": 1455, "variation_id": 1476},
            {"kind": "variation", "product_id": 1455, "variation_id": 1477},
        ])

    def _meta(self, status, synced_at=SYNCED_AT_BEFORE, sync_hash=SYNC_HASH_BEFORE):
        return {"meta_data": triplet_meta_entries(status, synced_at, sync_hash)}

    def _settled(self):
        return {"meta_data": settled_triplet_meta_entries()}


class TripletSettlementCommitTests(TripletSettlementFixture):
    def test_a_triplet_resource_stages_on_the_ordinary_pending_proof(self):
        store = self._store()
        plan_path, result = self._stage(store, self._request())
        self.assertEqual(result["status"], "STAGED_NOT_COMMITTED")
        self.assertEqual(result["targets"][0]["gla_baseline_mode"], "pending_baseline")
        self.assertEqual(store.calls.count(("GET", self.ENDPOINT)), 3)
        self.assertEqual(self.sleeps, [2, 4])
        self.assertEqual(store.writes, [])
        proof = json.loads(plan_path.read_text(encoding="utf-8"))["targets"][0]
        # The status entry, at its measured index, is what the proof stands on --
        # never the stamp beside it.
        self.assertEqual(proof["gla_pending_stability"]["meta_entry_index"], 1)
        self.assertEqual(proof["gla_pending_stability"]["meta_entry_id"], GLA_ID)
        self.assertEqual(len(proof["before_meta_data_projection"]), 7)
        policy.load_plan(str(plan_path))

    def test_the_measured_settlement_now_commits_after_a_stable_confirmation(self):
        """THE regression. This exact readback is what locked
        20260811T204921Z_shipping_class_assign_3e02e445093c9afb indeterminate at
        target 22 of 31, on a resource that was in fact exactly right."""
        store, plan_path = self._armed({self.ENDPOINT: [self._settled()] * 3})
        result = self._commit(store, plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        row = result["outcome"][0]
        self.assertIs(row["written"], True)
        self.assertEqual(row["baseline_mode"], "pending_baseline")
        self.assertEqual(row["settlement_shape"], "gla_status_with_stamp_and_hash")
        self.assertIs(row["convergence_used"], True)
        self.assertEqual(row["convergence_attempts"], 2)
        self.assertEqual(row["convergence_elapsed_seconds"], 6.0)
        self.assertEqual(row["final_state"], "confirmed_stable_settled_state")
        self.assertEqual(row["final_requirement"],
                         "exact_staged_pending_state_or_confirmed_settled_state")
        # One PUT, then reads only: the read-back plus both confirming reads.
        self.assertEqual(store.writes, [
            ("PUT", self.ENDPOINT, {"shipping_class": policy.FREIGHT_CLASS_SLUG})])
        self.assertEqual(store.calls.count(("GET", self.ENDPOINT)), 4)
        self.assertEqual(self.sleeps, [2, 4])
        self.assertLessEqual(sum(self.sleeps),
                             policy.PENDING_SETTLE_CONFIRM_CEILING_SECONDS)
        self.assertEqual(result["convergence"]["pending_settled_confirmed_targets"], 1)
        self.assertEqual(result["convergence"]["settlement_shapes"],
                         {"gla_status_only": 0, "gla_status_with_stamp_and_hash": 1})
        self.assertEqual(self._lock(plan_path)["status"], "committed_verified")

    def test_the_unchanged_pending_triplet_state_is_still_immediate_success(self):
        store, plan_path = self._armed({})
        result = self._commit(store, plan_path)
        row = result["outcome"][0]
        self.assertIs(row["convergence_used"], False)
        self.assertEqual(row["final_state"], "exact_staged_pending_state")
        self.assertNotIn("settlement_shape", row)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(result["convergence"]["settlement_shapes"],
                         {"gla_status_only": 0, "gla_status_with_stamp_and_hash": 0})

    def test_a_status_only_settlement_on_a_triplet_resource_keeps_its_own_name(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_STAGED)] * 3})
        result = self._commit(store, plan_path)
        row = result["outcome"][0]
        self.assertEqual(row["settlement_shape"], "gla_status_only")
        self.assertEqual(row["final_state"], "confirmed_stable_settled_state")
        self.assertEqual(result["convergence"]["settlement_shapes"],
                         {"gla_status_only": 1, "gla_status_with_stamp_and_hash": 0})

    def test_an_unstable_settled_state_during_confirmation_fails_closed(self):
        third_stamp = "SENTINEL-SYNCED-AT-THIRD-MUST-NOT-LEAK"
        third_hash = "SENTINEL-SYNC-HASH-THIRD-MUST-NOT-LEAK"
        cases = {
            "the_stamp_moves_again": [
                self._settled(),
                self._meta(GLA_STAGED, third_stamp, SYNC_HASH_AFTER)],
            "the_hash_moves_again": [
                self._settled(),
                self._meta(GLA_STAGED, SYNCED_AT_AFTER, third_hash)],
            "it_moves_again_on_the_second_observation": [
                self._settled(), self._settled(),
                self._meta(GLA_STAGED, SYNCED_AT_AFTER, third_hash)],
            "the_status_flips_back": [
                self._settled(),
                self._meta(GLA_TRANSIENT, SYNCED_AT_AFTER, SYNC_HASH_AFTER)],
            "it_moves_on_to_an_undiagnosed_value": [
                self._settled(),
                self._meta("error", SYNCED_AT_AFTER, SYNC_HASH_AFTER)],
            "another_protected_field_moves": [
                self._settled(), {**self._settled(), "weight": "99.9"}],
            "the_class_drifts_away": [
                self._settled(),
                {**self._settled(), "shipping_class": "some-other-class"}],
        }
        for label, script in cases.items():
            with self.subTest(label=label):
                store, plan_path = self._armed({self.ENDPOINT: script})
                message = self._commit_expecting_failure(store, plan_path)
                lock = self._lock(plan_path)
                self.assertEqual(lock["status"], "indeterminate_needs_restage")
                self.assertEqual(lock["reason_class"], "ProtectedStateMismatch")
                self.assertEqual(lock["diagnostic"]["phase"],
                                 "pending_settle_confirmation")
                self.assertNotIn("COMMITTED", message)
                # One write, then reads only. No retry, no rollback, no repair.
                self.assertEqual(store.writes, [
                    ("PUT", self.ENDPOINT,
                     {"shipping_class": policy.FREIGHT_CLASS_SLUG})])
                for token in (SYNCED_AT_AFTER, SYNC_HASH_AFTER, third_stamp,
                              third_hash, SECRET_META_VALUE):
                    self.assertNotIn(token, json.dumps(lock))

    def test_any_other_post_write_movement_fails_before_any_confirmation(self):
        fourth = [dict(row) for row in settled_triplet_meta_entries()]
        fourth[2] = {**fourth[2], "value": "MOVED-AS-WELL"}
        fourth_google = [dict(row) for row in settled_triplet_meta_entries()]
        fourth_google[6] = {**fourth_google[6], "value": "MOVED-AS-WELL"}
        cases = {
            "a_fourth_entry_moves": {"meta_data": fourth},
            "a_fourth_google_entry_moves": {"meta_data": fourth_google},
            "only_the_companions_move": self._meta(
                GLA_TRANSIENT, SYNCED_AT_AFTER, SYNC_HASH_AFTER),
            "the_status_moves_to_an_undiagnosed_value": self._meta(
                "error", SYNCED_AT_AFTER, SYNC_HASH_AFTER),
            "an_entry_is_added": {"meta_data": settled_triplet_meta_entries() + [
                {"id": 9500, "key": "_added", "value": SECRET_META_VALUE}]},
            "an_entry_is_removed": {
                "meta_data": settled_triplet_meta_entries()[:-1]},
            "the_entries_are_reordered": {
                "meta_data": list(reversed(settled_triplet_meta_entries()))},
            "a_companion_id_changes": {"meta_data": [
                {"id": 99999, "key": GLA_SYNCED_AT_KEY, "value": SYNCED_AT_AFTER}]
                + settled_triplet_meta_entries()[1:]},
            "a_companion_is_duplicated": {
                "meta_data": settled_triplet_meta_entries() + [
                    {"id": 74839, "key": GLA_SYNC_HASH_KEY, "value": SYNC_HASH_AFTER}]},
            "another_protected_field_moves": {
                **self._settled(), "weight": "99.9"},
        }
        for label, patch in cases.items():
            with self.subTest(label=label):
                store, plan_path = self._armed({self.ENDPOINT: [patch]})
                self._commit_expecting_failure(store, plan_path)
                lock = self._lock(plan_path)
                self.assertEqual(lock["status"], "indeterminate_needs_restage")
                self.assertEqual(lock["diagnostic"]["phase"], "post_write")
                # It failed on the read-back; no confirmation was ever attempted.
                self.assertEqual(self.sleeps, [])
                self.assertEqual(len(store.writes), 1)
                self.assertNotIn(SECRET_META_VALUE, json.dumps(lock))

    def test_a_failed_triplet_confirmation_can_never_be_replayed(self):
        store, plan_path = self._armed({self.ENDPOINT: [
            self._settled(),
            self._meta(GLA_TRANSIENT, SYNCED_AT_AFTER, SYNC_HASH_AFTER)]})
        self._commit_expecting_failure(store, plan_path)
        store.after_write_gets = {}
        store.writes.clear()
        self.sleeps.clear()
        self._commit_expecting_failure(store, plan_path)
        self.assertEqual(store.writes, [])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self._lock(plan_path)["status"], "indeterminate_needs_restage")

    def test_the_second_target_starts_only_after_the_first_is_confirmed(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._settled()] * 3}, targets=self._both())
        result = self._commit(store, plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        first = store.calls.index(("PUT", self.ENDPOINT))
        second = store.calls.index(("PUT", self.SECOND))
        self.assertLess(first, second)
        self.assertEqual(store.calls[first + 1:second], [
            ("GET", self.ENDPOINT), ("GET", self.ENDPOINT), ("GET", self.ENDPOINT),
            ("GET", self.SECOND)])

    def test_a_failed_settlement_never_starts_the_second_target(self):
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._settled(),
                             self._meta(GLA_TRANSIENT, SYNCED_AT_AFTER,
                                        SYNC_HASH_AFTER)]},
            targets=self._both())
        self._commit_expecting_failure(store, plan_path)
        self.assertNotIn(("PUT", self.SECOND), store.calls)
        self.assertEqual(store.records[self.SECOND]["shipping_class"], "")
        self.assertEqual(len(store.writes), 1)

    def test_exactly_one_put_and_no_metadata_write_on_any_triplet_path(self):
        for label, script in (
            ("unchanged", {}),
            ("settles", {self.ENDPOINT: [self._settled()] * 3}),
            ("fails", {self.ENDPOINT: [
                self._settled(),
                self._meta(GLA_TRANSIENT, SYNCED_AT_AFTER, SYNC_HASH_AFTER)]}),
        ):
            with self.subTest(label=label):
                store, plan_path = self._armed(script)
                try:
                    self._commit(store, plan_path)
                except policy.ShippingPolicyError:
                    pass
                self.assertEqual(len(store.writes), 1)
                method, endpoint, payload = store.writes[0]
                self.assertEqual(method, "PUT")
                self.assertEqual(endpoint, self.ENDPOINT)
                self.assertEqual(payload,
                                 {"shipping_class": policy.FREIGHT_CLASS_SLUG})
                self.assertEqual(store.records[self.ENDPOINT]["shipping_class"],
                                 policy.FREIGHT_CLASS_SLUG)

    def test_the_triplet_path_still_requires_his_clear_go(self):
        store, plan_path = self._armed({self.ENDPOINT: [self._settled()] * 3})
        for approval in ("approved but wait", "hold", "APPROVED?", "", None, "PROCEED?"):
            with self.subTest(approval=repr(approval)):
                with mock.patch.object(policy.wc, "load_vault") as load, \
                     mock.patch.object(policy.wc, "api_get") as get, \
                     mock.patch.object(policy.wc, "api_request") as write:
                    with self.assertRaises(policy.ShippingPolicyError):
                        policy.command_commit(argparse.Namespace(
                            plan=str(plan_path), approval=approval))
                    load.assert_not_called()
                    get.assert_not_called()
                    write.assert_not_called()
        self.assertEqual(self.sleeps, [])
        self.assertEqual(store.writes, [])

    def test_the_committed_receipt_names_the_settlement_shape_it_accepted(self):
        store, plan_path = self._armed({self.ENDPOINT: [self._settled()] * 3})
        self._commit(store, plan_path)
        evidence = next(text for action, text in self.receipts
                        if action == "woocommerce_shipping_policy_committed")
        self.assertIn("settlement_gla_status_with_stamp_and_hash_targets=1", evidence)
        self.assertIn("settlement_gla_status_only_targets=0", evidence)
        self.assertIn("pending_settled_confirmed_targets=1", evidence)

    def test_no_successful_triplet_record_carries_a_raw_metadata_value(self):
        store, plan_path = self._armed({self.ENDPOINT: [self._settled()] * 3})
        result = self._commit(store, plan_path)
        text = strip_fixed_vocabulary(
            json.dumps(result) + json.dumps(self.receipts)
            + json.dumps(self._lock(plan_path))
            + plan_path.read_text(encoding="utf-8"))
        for token in (GLA_STAGED, GLA_TRANSIENT, SYNCED_AT_BEFORE, SYNCED_AT_AFTER,
                      SYNC_HASH_BEFORE, SYNC_HASH_AFTER, SECRET_META_VALUE,
                      SECRET_DESCRIPTION, "ck_abcdefghijklmnopqrstuvwxyz"):
            with self.subTest(token=token):
                self.assertNotIn(token, text)
        # It still names the three keys it is allowed to name.
        contract = json.loads(plan_path.read_text(
            encoding="utf-8"))["convergence_contract"]
        self.assertEqual(contract["settlement_companion_meta_keys"],
                         [GLA_SYNCED_AT_KEY, GLA_SYNC_HASH_KEY])
        self.assertIn(GLA_KEY, json.dumps(result))

    def test_no_failed_triplet_record_carries_a_raw_metadata_value_either(self):
        store, plan_path = self._armed({self.ENDPOINT: [
            self._settled(),
            self._meta(GLA_TRANSIENT, SYNCED_AT_AFTER, SYNC_HASH_AFTER)]})
        message = self._commit_expecting_failure(store, plan_path)
        text = strip_fixed_vocabulary(
            message + json.dumps(self._lock(plan_path)) + json.dumps(self.receipts))
        for token in (GLA_STAGED, GLA_TRANSIENT, SYNCED_AT_BEFORE, SYNCED_AT_AFTER,
                      SYNC_HASH_BEFORE, SYNC_HASH_AFTER, SECRET_META_VALUE,
                      SECRET_DESCRIPTION):
            with self.subTest(token=token):
                self.assertNotIn(token, text)


class SettledBaselineIsUnchangedTests(ConvergenceFixture):
    """Schema 4 added a path. It must not have moved the one already proven."""

    def test_the_settled_schedule_and_final_requirement_are_untouched(self):
        self.assertEqual(policy.CONVERGENCE_SCHEDULE_SECONDS, (2, 4, 8, 16, 30, 30))
        self.assertEqual(policy.CONVERGENCE_MAX_SECONDS, 90)
        self.assertEqual(policy.CONVERGENCE_CEILING_SECONDS, 90)
        self.assertEqual(policy.CONVERGENCE_FINAL_REQUIREMENT,
                         "exact_staged_protected_state")
        self.assertEqual(policy.CONVERGENCE_TIMEOUT_FINAL_STATE, "pending_not_accepted")

    def test_a_settled_target_still_converges_over_the_full_ninety_seconds(self):
        store, plan_path = self._armed({self.ENDPOINT: [
            self._meta(GLA_TRANSIENT)] * 5 + [self._meta(GLA_STAGED)]})
        result = self._commit(store, plan_path)
        row = result["outcome"][0]
        self.assertEqual(row["baseline_mode"], "settled_baseline")
        self.assertIs(row["convergence_used"], True)
        self.assertEqual(row["final_state"], "exact_staged_protected_state")
        self.assertEqual(self.sleeps, [2, 4, 8, 16, 30])

    def test_a_settled_target_still_times_out_rather_than_accepting_the_transient(self):
        store, plan_path = self._armed({self.ENDPOINT: [self._meta(GLA_TRANSIENT)] * 20})
        message = self._commit_expecting_failure(store, plan_path)
        self.assertIn("NOT accepted as success", message)
        lock = self._lock(plan_path)
        self.assertEqual(lock["reason_class"], "ConvergenceTimeout")
        self.assertEqual(lock["convergence"]["final_state"], "pending_not_accepted")
        self.assertEqual(sum(self.sleeps), 90)

    def test_the_settled_ninety_second_contract_survived_schema_five(self):
        """The schema-5 repair touched the pending path only. Every fixed value of
        the settled contract is still exactly what schema 3 proved."""
        contract = policy.convergence_contract()
        self.assertEqual(contract["schedule_seconds"], [2, 4, 8, 16, 30, 30])
        self.assertEqual(contract["max_seconds"], 90)
        self.assertEqual(contract["final_requirement"], "exact_staged_protected_state")
        self.assertEqual(contract["allowed_changed_protected_fields_during_wait"],
                         ["meta_data"])
        self.assertEqual(contract["staged_value_sha256"],
                         policy.GLA_STAGED_VALUE_SHA256)
        self.assertEqual(contract["transient_value_sha256"],
                         policy.GLA_TRANSIENT_VALUE_SHA256)

    def test_a_settled_target_seeing_the_three_entry_move_is_refused_not_settled(self):
        """The wider settlement shape belongs to the pending baseline alone. On a
        settled baseline the same three-entry readback is an undiagnosed edit, and
        it must lock rather than converge or confirm."""
        settled_triplet = triplet_variation_record(1455, 2056, GLA_STAGED)
        store = FakeStore({self.ENDPOINT: settled_triplet},
                          classes=[dict(FREIGHT_CLASS_ROW)])
        plan_path, staged = self._stage(store, self._request())
        self.assertEqual(staged["targets"][0]["gla_baseline_mode"], "settled_baseline")
        store.after_write_gets = {self.ENDPOINT: [{"meta_data": triplet_meta_entries(
            GLA_TRANSIENT, SYNCED_AT_AFTER, SYNC_HASH_AFTER)}]}
        store.calls.clear()
        self.sleeps.clear()
        self._commit_expecting_failure(store, plan_path)
        lock = self._lock(plan_path)
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertEqual(lock["diagnostic"]["phase"], "post_write")
        self.assertEqual(self.sleeps, [])
        self.assertEqual(len(store.writes), 1)

    def test_a_settled_target_is_never_confirmed_by_the_pending_path(self):
        """A settled target whose entry goes to the transient value must use the
        90-second wait, never the 6-second settled-state confirmation."""
        store, plan_path = self._armed(
            {self.ENDPOINT: [self._meta(GLA_TRANSIENT), self._meta(GLA_STAGED)]})
        result = self._commit(store, plan_path)
        row = result["outcome"][0]
        self.assertEqual(row["final_state"], "exact_staged_protected_state")
        self.assertNotEqual(row["final_state"], "confirmed_stable_settled_state")
        self.assertEqual(result["convergence"]["pending_settled_confirmed_targets"], 0)


class ExactApprovalTests(unittest.TestCase):
    def test_his_clear_go_is_accepted_and_a_condition_is_not(self):
        """2026-08-21 (A1): the shared detector decides. APPROVED still works,
        a plain yes counts, a condition/question/non-string refuses, and a word
        that arrived over the relay never authorises."""
        self.assertTrue(policy.require_rachad_approval("APPROVED").exact_word)
        for value in ("approved", "Approved", "APPROVE", " APPROVED ", "APPROVED.", "yes",
                      "yes go ahead", "do it", "proceed", "ok"):
            with self.subTest(value=repr(value)):
                self.assertEqual(policy.require_rachad_approval(value).kind, "reversible")
        for value in (
            "yes but not the 8 inch", "wait", "APPROVED?", "", "APPROVED\r\nnow",
            # A fullwidth spelling is not a word the detector knows.
            "\uff21\uff30\uff30\uff32\uff2f\uff36\uff25\uff24",
            None, 0, 1, True, ["APPROVED"], {"approval": "APPROVED"},
        ):
            with self.subTest(value=repr(value)):
                with self.assertRaises(policy.ShippingPolicyError):
                    policy.require_rachad_approval(value)
        with self.assertRaises(policy.ShippingPolicyError):
            policy.require_rachad_approval("yes", "relay")

    def test_the_tool_never_produces_the_word_on_rachads_behalf(self):
        source = Path(policy.__file__).read_text(encoding="utf-8")
        # The word appears only through the shared constant, in messages that ASK
        # for it, and in the staging preview that shows Rachad what to reply.
        self.assertEqual(source.count("APPROVAL_WORD = owner_authority.EXACT_WORD"), 1)
        self.assertNotIn('APPROVAL_WORD = "APPROVED"', source)
        self.assertNotIn('approval="APPROVED"', source)
        self.assertNotIn("approval = APPROVAL_WORD", source)


# ---------------------------------------------------------------------------
# Checkout guard: real PHP when available, reference model otherwise
# ---------------------------------------------------------------------------
def _php() -> str | None:
    return shutil.which("php")


def _load_scenarios() -> dict:
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def _is_int(value) -> bool:
    """PHP is_int(): booleans are NOT integers."""
    return isinstance(value, int) and not isinstance(value, bool)


def _php_strtotime(value: str):
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return False


class GuardReference:
    """Python mirror of the plugin's decision core, used only when php is absent."""

    FREIGHT_SLUG = "freight-quote-required"
    MESSAGE = EXACT_MESSAGE

    @staticmethod
    def prepare_allowlist(raw, now_ts):
        empty = {"ok": False, "reason": "", "map": {}}
        if not isinstance(raw, (dict, list)):
            return {**empty, "reason": "allowlist_missing_or_unreadable"}
        expires_raw = raw.get("expires_utc") if isinstance(raw, dict) else None
        if expires_raw is None or not isinstance(expires_raw, str):
            return {**empty, "reason": "allowlist_missing_expiry"}
        expires = _php_strtotime(expires_raw)
        if expires is False:
            return {**empty, "reason": "allowlist_invalid_expiry"}
        if now_ts >= expires:
            return {**empty, "reason": "allowlist_stale"}
        items = raw.get("items")
        if items is None or not isinstance(items, (dict, list)):
            return {**empty, "reason": "allowlist_missing_items"}
        mapping = {}
        for entry in items:
            if not isinstance(entry, dict):
                return {**empty, "reason": "allowlist_malformed_entry"}
            if any(entry.get(key) is None for key in ("product_id", "variation_id", "sku")):
                return {**empty, "reason": "allowlist_malformed_entry"}
            product_id, variation_id, sku = (entry["product_id"], entry["variation_id"],
                                             entry["sku"])
            ok = (_is_int(product_id) and product_id > 0
                  and _is_int(variation_id) and variation_id >= 0
                  and isinstance(sku, str) and sku.strip() != "")
            if not ok:
                return {**empty, "reason": "allowlist_malformed_entry"}
            mapping[f"{product_id}:{variation_id}"] = {"sku": sku.strip()}
        return {"ok": True, "reason": "", "map": mapping}

    @classmethod
    def item_reason(cls, item, allowlist):
        if not isinstance(item, dict):
            return "item_unreadable"
        product_id = item.get("product_id")
        variation_id = item.get("variation_id", 0)
        sku = item.get("sku", "")
        shipping_class = item.get("shipping_class", "")
        if not _is_int(product_id) or product_id <= 0:
            return "unresolvable_product"
        if not _is_int(variation_id) or variation_id < 0:
            return "unresolvable_variation"
        if not isinstance(shipping_class, str):
            return "unresolvable_shipping_class"
        if shipping_class == cls.FREIGHT_SLUG:
            return "freight_shipping_class"
        if not allowlist["ok"]:
            return allowlist["reason"] or "allowlist_unusable"
        if not isinstance(sku, str) or sku.strip() == "":
            return "missing_sku"
        key = f"{product_id}:{variation_id}"
        if key not in allowlist["map"]:
            return "not_ups_verified"
        if allowlist["map"][key]["sku"] != sku.strip():
            return "sku_mismatch"
        if shipping_class != "":
            return "unexpected_shipping_class"
        return ""

    @classmethod
    def decide(cls, items, raw, now_ts):
        result = {"freight_required": False, "reasons": [], "message": cls.MESSAGE}
        if not isinstance(items, list) or not items:
            return result
        allowlist = cls.prepare_allowlist(raw, now_ts)
        for index, item in enumerate(items):
            reason = cls.item_reason(item, allowlist)
            if reason:
                result["freight_required"] = True
                result["reasons"].append(f"{index}:{reason}")
        return result


class CheckoutGuardScenarioTests(unittest.TestCase):
    """Every scenario, executed against the real PHP when php is on PATH."""

    @classmethod
    def setUpClass(cls):
        cls.scenarios = _load_scenarios()
        cls.php = _php()
        cls.now_ts = datetime.fromisoformat(cls.scenarios["now_utc"]).timestamp()
        if cls.php is None:
            print(
                "\n[checkout guard] php was not found on PATH. Scenarios run against the "
                "Python reference model in this file, NOT against the plugin PHP. Run "
                f"`php {PHP_TEST}` on a host with PHP to exercise the real plugin.",
                file=sys.stderr,
            )

    def test_php_harness_passes_when_php_is_available(self):
        if self.php is None:
            self.skipTest("php is not installed on this host; reference model used instead")
        completed = subprocess.run(
            [self.php, str(PHP_TEST)], capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(completed.returncode, 0,
                         f"PHP guard tests failed:\n{completed.stdout}\n{completed.stderr}")
        self.assertIn("failures=0", completed.stdout)

    def test_every_scenario_produces_the_required_decision(self):
        for case in self.scenarios["cases"]:
            with self.subTest(case=case["name"]):
                allowlist = self.scenarios["allowlists"][case["allowlist"]]
                decision = GuardReference.decide(case["items"], allowlist, self.now_ts)
                self.assertEqual(decision["freight_required"], case["expect_freight"])
                self.assertEqual(decision["reasons"], case["expect_reasons"])
                self.assertEqual(decision["message"], EXACT_MESSAGE)

    def test_named_requirements_are_each_covered_by_a_scenario(self):
        names = {case["name"] for case in self.scenarios["cases"]}
        required = {
            "freight_classified_item_blocks",
            "blank_class_unknown_item_blocks",
            "missing_sku_blocks_even_when_ids_match",
            "mixed_cart_verified_plus_freight_blocks",
            "stale_allowlist_blocks_previously_verified_item",
            "verified_item_alone_allows_ups",
            "variation_inheriting_parent_freight_class_blocks",
            "virtual_flag_does_not_exempt_a_freight_item",
        }
        self.assertTrue(required.issubset(names), sorted(required - names))

    def test_a_mixed_cart_blocks_even_though_one_item_is_ups_verified(self):
        current = self.scenarios["allowlists"]["current"]
        verified = {"product_id": 900001, "variation_id": 900002,
                    "sku": "TEST-UPS-VERIFIED-1", "shipping_class": ""}
        alone = GuardReference.decide([verified], current, self.now_ts)
        self.assertFalse(alone["freight_required"])
        for companion in (
            {"product_id": 1423, "variation_id": 1424, "sku": "X",
             "shipping_class": "freight-quote-required"},
            {"product_id": 700001, "variation_id": 0, "sku": "X", "shipping_class": ""},
            {"product_id": 900001, "variation_id": 900002, "sku": "", "shipping_class": ""},
        ):
            with self.subTest(companion=companion):
                mixed = GuardReference.decide([verified, companion], current, self.now_ts)
                self.assertTrue(mixed["freight_required"])


class GuardSourceTests(unittest.TestCase):
    """Structural guarantees about the plugin source itself."""

    @classmethod
    def setUpClass(cls):
        cls.source = PLUGIN_PHP.read_text(encoding="utf-8")

    def test_exact_message_constant(self):
        self.assertIn(f"const FRPDEPOT_FCG_MESSAGE = '{EXACT_MESSAGE}';", self.source)

    def test_message_is_not_translated_so_it_cannot_drift(self):
        self.assertNotIn("__( 'Contact us", self.source)
        self.assertNotIn("_e( 'Contact us", self.source)

    def test_direct_submission_paths_are_blocked_not_just_the_rate_list(self):
        for hook in (
            "woocommerce_check_cart_items",
            "woocommerce_checkout_process",
            "woocommerce_after_checkout_validation",
            "woocommerce_store_api_cart_errors",
            "woocommerce_store_api_checkout_update_order_from_request",
        ):
            with self.subTest(hook=hook):
                self.assertIn(f"'{hook}'", self.source)

    def test_rate_suppression_hook_is_registered(self):
        self.assertIn("add_filter( 'woocommerce_package_rates'", self.source)

    def test_plugin_performs_no_writes_network_calls_or_code_execution(self):
        forbidden = (
            "eval(", "exec(", "shell_exec", "system(", "passthru", "popen",
            "file_put_contents", "fopen(", "unlink(", "wp_remote_get", "wp_remote_post",
            "curl_init", "update_option", "update_post_meta", "wp_insert_post",
            "$wpdb", "create_function", "assert(", "base64_decode",
            # 1.0.1 registers a bundled script; it must never inline one.
            "wp_add_inline_script", "wp_localize_script",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)

    def test_plugin_exits_when_loaded_outside_wordpress(self):
        self.assertIn("if ( ! defined( 'ABSPATH' ) ) {", self.source)

    def test_reason_vocabulary_matches_the_python_reference_model(self):
        php_reasons = set(re.findall(r"return '([a-z_]+)';", self.source))
        php_reasons.discard("")
        reference_reasons = {
            "item_unreadable", "unresolvable_product", "unresolvable_variation",
            "unresolvable_shipping_class", "freight_shipping_class", "missing_sku",
            "not_ups_verified", "sku_mismatch", "unexpected_shipping_class",
        }
        self.assertEqual(php_reasons, reference_reasons)

    def test_allowlist_failure_reasons_match_the_python_reference_model(self):
        php_reasons = set(re.findall(r"\$empty\['reason'\] = '([a-z_]+)';", self.source))
        self.assertEqual(php_reasons, {
            "allowlist_missing_or_unreadable", "allowlist_missing_expiry",
            "allowlist_invalid_expiry", "allowlist_stale", "allowlist_missing_items",
            "allowlist_malformed_entry",
        })

    def test_shipped_allowlist_is_empty_because_nothing_is_ups_verified_yet(self):
        document = json.loads(ALLOWLIST_JSON.read_text(encoding="utf-8"))
        self.assertEqual(document["items"], [])
        self.assertEqual(document["verified_packing_groups"], 0)
        self.assertIn("expires_utc", document)

    def test_the_shipped_allowlist_blocks_every_cart_today(self):
        now = datetime.now(timezone.utc).timestamp()
        document = json.loads(ALLOWLIST_JSON.read_text(encoding="utf-8"))
        for item in (
            {"product_id": 1423, "variation_id": 1424, "sku": "ELDN25150PSI411",
             "shipping_class": ""},
            {"product_id": 1423, "variation_id": 1424, "sku": "ELDN25150PSI411",
             "shipping_class": "freight-quote-required"},
        ):
            with self.subTest(item=item):
                decision = GuardReference.decide([item], document, now)
                self.assertTrue(decision["freight_required"])
                self.assertEqual(decision["message"], EXACT_MESSAGE)


class GuardMessageVisibilityTests(unittest.TestCase):
    """Regression cover for the 2026-08-09 production validation failure.

    1.0.0 blocked checkout but the customer never saw the required sentence: an
    error notice on woocommerce_check_cart_items makes WooCommerce render
    checkout/cart-errors.php -- the generic "There are some issues with the items
    in your cart..." shell -- instead of the checkout form, and then call
    wc_clear_notices(), destroying the message unprinted. The block was never
    mounted in that branch, so the Store API was never called and
    woocommerce_store_api_cart_errors never ran either.

    The behavioural proof lives in tests/test-freight-guard.php and needs a PHP
    runtime. These tests hold the structural invariants that make that behaviour
    possible, and they run everywhere.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = PLUGIN_PHP.read_text(encoding="utf-8")
        cls.script = PLUGIN_JS.read_text(encoding="utf-8")
        cls.php_test = PHP_TEST.read_text(encoding="utf-8")
        cls.readme = PLUGIN_README.read_text(encoding="utf-8")

    # -- version ----------------------------------------------------------
    def _declared_version(self) -> str:
        header = re.search(r"^\s*\*\s*Version:\s*(\S+)\s*$", self.source, re.M)
        self.assertIsNotNone(header, "plugin header declares no Version")
        return header.group(1)

    def test_version_is_newer_than_the_withdrawn_release(self):
        declared = self._declared_version()
        self.assertIn(f"const FRPDEPOT_FCG_VERSION = '{declared}';", self.source)
        self.assertIn(f"Stable tag: {declared}", self.readme)
        self.assertNotEqual(declared, WITHDRAWN_VERSION)
        as_tuple = tuple(int(part) for part in declared.split("."))
        self.assertGreater(as_tuple, (1, 0, 0))

    def test_the_withdrawn_release_is_recorded_not_erased(self):
        self.assertIn(WITHDRAWN_VERSION, self.readme)

    # -- the cart-errors shell no longer replaces the message --------------
    def test_the_generic_shell_is_preempted_by_the_required_message(self):
        self.assertIn(
            "const FRPDEPOT_FCG_CART_ERRORS_TEMPLATE = 'checkout/cart-errors.php';",
            self.source,
        )
        self.assertIn(
            "add_action( 'woocommerce_before_template_part', "
            "'frpdepot_fcg_before_template_part', 1, 4 );",
            self.source,
        )
        self.assertIn(
            "if ( FRPDEPOT_FCG_CART_ERRORS_TEMPLATE !== $template_name ) {",
            self.source,
        )
        self.assertIn(
            "add_action( 'woocommerce_cart_has_errors', 'frpdepot_fcg_cart_has_errors', 1 );",
            self.source,
        )

    def test_the_plugin_never_ships_the_generic_wording_itself(self):
        self.assertNotIn(GENERIC_CART_ERROR, self.source)
        self.assertNotIn(GENERIC_CART_ERROR, self.script)

    def test_the_php_harness_covers_the_live_failure(self):
        """The behavioural proof must exist, even though PHP cannot run here."""
        for marker in (
            "frpdepot_fcg_before_template_part( 'checkout/cart-errors.php'",
            "wc_clear_notices();",
            "the required message is still on the page after the queue is cleared",
            "exactly one required message across every surface",
            "Store API order placement throws with the exact message",
            "a blocked cart loses every shipping rate",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.php_test)

    # -- exactly one visible message --------------------------------------
    def test_every_emission_routes_through_the_single_latch(self):
        self.assertIn("function frpdepot_fcg_emit_message() {", self.source)
        self.assertIn(
            "if ( ! empty( $GLOBALS['frpdepot_fcg_message_emitted'] ) ) {", self.source
        )
        self.assertIn("$GLOBALS['frpdepot_fcg_message_emitted'] = true;", self.source)
        # One output statement in the whole plugin, and it prints the latch result.
        echoes = re.findall(r"^\s*echo\s+(\S+)", self.source, re.M)
        self.assertEqual(echoes, ["$markup;"])
        self.assertIn("$markup = frpdepot_fcg_emit_message();", self.source)
        # The message constant is never sent to the browser directly.
        self.assertIsNone(re.search(r"echo[^;\n]*FRPDEPOT_FCG_MESSAGE", self.source))

    def test_the_no_shipping_slot_does_not_print_a_second_copy(self):
        self.assertIn("return '' !== $markup ? $markup : $html;", self.source)

    def test_the_blocking_notice_is_deduplicated(self):
        guard = "wc_has_notice( FRPDEPOT_FCG_MESSAGE, 'error' )"
        self.assertGreaterEqual(self.source.count(guard), 2)

    # -- blocking is unchanged --------------------------------------------
    def test_direct_submission_is_still_blocked_on_both_paths(self):
        for hook in (
            "add_action( 'woocommerce_check_cart_items'",
            "add_action( 'woocommerce_checkout_process'",
            "add_action( 'woocommerce_after_checkout_validation'",
            "add_filter( 'woocommerce_store_api_cart_errors'",
            "add_action( 'woocommerce_store_api_checkout_update_order_from_request'",
        ):
            with self.subTest(hook=hook):
                self.assertIn(hook, self.source)
        self.assertIn("RouteException", self.source)

    def test_rate_suppression_still_returns_an_empty_rate_set(self):
        self.assertIn("add_filter( 'woocommerce_package_rates'", self.source)
        rate_filter = self.source.split("function frpdepot_fcg_filter_package_rates")[1]
        rate_filter = rate_filter.split("\n}\n")[0]
        self.assertIn("return array();", rate_filter)

    # -- the bundled Blocks script ----------------------------------------
    def test_the_blocks_script_is_bundled_and_registered_from_a_local_file(self):
        self.assertTrue(PLUGIN_JS.is_file())
        self.assertIn(
            "const FRPDEPOT_FCG_SCRIPT_FILE = 'assets/frpdepot-freight-notice.js';",
            self.source,
        )
        self.assertIn("wp_register_script(", self.source)
        self.assertIn("plugins_url( FRPDEPOT_FCG_SCRIPT_FILE, __FILE__ )", self.source)
        self.assertIn("FRPDEPOT_FCG_SCRIPT_HANDLE,", self.source)
        self.assertIn("add_action( 'wp_enqueue_scripts', "
                      "'frpdepot_fcg_enqueue_blocks_notice', 100 );", self.source)

    def test_the_blocks_script_is_enqueued_only_for_a_blocked_cart_page(self):
        self.assertIn(
            "if ( ! frpdepot_fcg_is_cart_or_checkout() || ! frpdepot_fcg_cart_requires_quote() ) {",
            self.source,
        )
        self.assertIn("if ( ! is_readable( __DIR__ . '/' . FRPDEPOT_FCG_SCRIPT_FILE ) ) {",
                      self.source)

    def test_the_blocks_script_message_is_identical_to_the_php_constant(self):
        self.assertIn(f"var MESSAGE = '{EXACT_MESSAGE}';", self.script)
        self.assertEqual(self.script.count(f"'{EXACT_MESSAGE}'"), 1,
                         "the sentence must be declared once, as a single constant")

    def test_the_blocks_script_performs_no_network_storage_or_code_execution(self):
        forbidden = (
            "eval(", "new Function", "Function(", "fetch(", "XMLHttpRequest",
            "WebSocket", "importScripts", "innerHTML", "outerHTML", "document.write",
            "insertAdjacentHTML", "localStorage", "sessionStorage", "document.cookie",
            "atob(", "btoa(", "import(", "http://", "https://", "ajax", "jQuery",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.script)

    def test_the_blocks_script_sets_text_not_markup_and_cannot_duplicate(self):
        self.assertIn("note.textContent = MESSAGE;", self.script)
        self.assertIn("function nativeMessageVisible()", self.script)
        self.assertIn("function serverMessageVisible()", self.script)
        # It withdraws its own copy the moment WooCommerce shows the sentence.
        self.assertIn("removeOurs();", self.script)
        # And it always stops: no unbounded observer.
        self.assertIn("observer.disconnect();", self.script)
        self.assertIn("var DEADLINE_MS =", self.script)

    def test_the_default_deny_policy_and_empty_allowlist_are_untouched(self):
        self.assertIn("const FRPDEPOT_FCG_FREIGHT_SLUG = 'freight-quote-required';",
                      self.source)
        document = json.loads(ALLOWLIST_JSON.read_text(encoding="utf-8"))
        self.assertEqual(document["items"], [])
        self.assertEqual(document["verified_packing_groups"], 0)


class PluginPackagingTests(unittest.TestCase):
    def test_zip_build_is_reproducible_and_contains_only_the_plugin(self):
        builder_path = GUARD_DIR / "build_plugin_zip.py"
        self.assertTrue(builder_path.is_file())
        sys.path.insert(0, str(GUARD_DIR))
        try:
            import build_plugin_zip  # noqa: PLC0415
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as tmp:
            first = build_plugin_zip.build(Path(tmp) / "a.zip")
            second = build_plugin_zip.build(Path(tmp) / "b.zip")
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["members"], [
            "frpdepot-freight-checkout-guard/assets/frpdepot-freight-notice.js",
            "frpdepot-freight-checkout-guard/frpdepot-freight-checkout-guard.php",
            "frpdepot-freight-checkout-guard/readme.txt",
            "frpdepot-freight-checkout-guard/ups-allowlist.json",
        ])
        self.assertIs(first["uploaded"], False)
        self.assertIs(first["installed"], False)

    def test_the_corrected_zip_is_not_the_one_that_failed_production(self):
        """A new artifact must be distinguishable from the withdrawn 1.0.0 ZIP."""
        sys.path.insert(0, str(GUARD_DIR))
        try:
            import build_plugin_zip  # noqa: PLC0415
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as tmp:
            built = build_plugin_zip.build(Path(tmp) / "corrected.zip")
        self.assertNotEqual(built["sha256"], FAILED_ZIP_SHA256)
        self.assertEqual(len(built["sha256"]), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
