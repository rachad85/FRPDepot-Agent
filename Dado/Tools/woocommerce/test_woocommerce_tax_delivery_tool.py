"""No-real-network safety tests for WooCommerce delivery-tax correction safety v2."""
from __future__ import annotations

import argparse
import contextlib
import copy
from datetime import timedelta
from decimal import Decimal
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import woocommerce_common as wc  # noqa: E402
import woocommerce_tax_delivery_tool as tool  # noqa: E402

OLD_V1_PLAN = Path(r"C:\FRPDepot\Dado\20_Working\woocommerce_tax_delivery_plans\20260814T151308Z_tax_delivery_2f019d351041bc5c.json")


def vault() -> dict[str, str]:
    return {
        "site_url": wc.ALLOWED_ORIGIN,
        "consumer_key": "ck_abcdefghijklmnopqrstuvwxyz",
        "consumer_secret": "cs_abcdefghijklmnopqrstuvwxyz",
        "declared_permissions": "read_write",
    }


def before_rows() -> list[dict]:
    rows = copy.deepcopy(list(tool.EXPECTED_BEFORE_ROWS))
    for row in rows:
        row["_links"] = {
            "self": [{"href": f"https://frpdepots.com/wp-json/wc/v3/taxes/{row['id']}"}],
            "collection": [{"href": "https://frpdepots.com/wp-json/wc/v3/taxes"}],
        }
    return rows


def projected(rows: list[dict]) -> list[dict]:
    return [{field: copy.deepcopy(row[field]) for field in tool.TAX_FIELDS} for row in rows]


@contextlib.contextmanager
def noop_global_lock(*, purpose: str, wait_seconds: float = 0):
    del purpose, wait_seconds
    yield


class FakeWoo:
    """Independent exact-route fake. It never uses sockets or urllib."""

    def __init__(self):
        self.rows = before_rows()
        self.settings = {setting_id: expected for _, setting_id, expected in tool.SETTING_SPECS}
        self.classes = copy.deepcopy(list(tool.EXPECTED_TAX_CLASSES))
        self.plugins = [
            {"plugin": identity["plugin"], "name": identity["name"], "version": version}
            for identity, version in zip(tool.VERSION_PLUGINS.values(), ("11.0.1", "3.9.12", "2.0.4"))
        ]
        self.get_calls: list[tuple[str, dict | None]] = []
        self.calls: list[tuple[str, str]] = []
        self.writes: list[tuple[str, str, dict]] = []
        self.tax_snapshots: list[list[dict]] = []
        self.fail_position: int | None = None
        self.timeout_position: int | None = None
        self.malformed_position: int | None = None
        self.wrong_id_position: int | None = None
        self.after_apply = None
        self.global_guard_held = False
        self.per_plan_lock: Path | None = None

    def api_get(self, endpoint, params=None, vault=None):
        del vault
        self.get_calls.append((endpoint, copy.deepcopy(params)))
        self.calls.append(("GET", endpoint))
        if endpoint == "/taxes":
            expected = {"per_page": 100, "page": 1, "order": "asc", "orderby": "order"}
            if params != expected:
                raise AssertionError(f"Unexpected /taxes params: {params}")
            self.tax_snapshots.append(projected(self.rows))
            return copy.deepcopy(self.rows), {"x-wp-total": str(len(self.rows))}
        for group, setting_id, _ in tool.SETTING_SPECS:
            if endpoint == f"/settings/{group}/{setting_id}":
                if params != {"_fields": "id,value"}:
                    raise AssertionError(f"Unexpected setting params: {params}")
                return {
                    "id": setting_id,
                    "value": self.settings[setting_id],
                    "_links": {
                        "self": [{"href": f"https://frpdepots.com/wp-json/wc/v3/settings/{group}/{setting_id}"}],
                        "collection": [{"href": f"https://frpdepots.com/wp-json/wc/v3/settings/{group}"}],
                    },
                }, {}
        if endpoint == "/taxes/classes":
            if params != {"_fields": "slug,name"}:
                raise AssertionError(f"Unexpected classes params: {params}")
            return copy.deepcopy(self.classes), {}
        if endpoint == "/system_status":
            if params != {"_fields": "active_plugins"}:
                raise AssertionError(f"Unexpected system status params: {params}")
            return {"active_plugins": copy.deepcopy(self.plugins)}, {}
        raise AssertionError(f"Unexpected authenticated GET: {endpoint}")

    def api_request(self, method, endpoint, params=None, payload=None, vault=None, timeout=60):
        del vault, timeout
        if not self.global_guard_held:
            raise AssertionError("PUT occurred outside the global single-flight guard")
        if self.per_plan_lock is not None and not self.per_plan_lock.exists():
            raise AssertionError("PUT occurred before the permanent per-plan lock")
        if params is not None:
            raise AssertionError("PUT carried query parameters")
        copied = copy.deepcopy(payload)
        self.calls.append((method, endpoint))
        self.writes.append((method, endpoint, copied))
        position = len(self.writes)
        expected_id = tool.WRITE_ORDER[position - 1]
        if method != "PUT" or endpoint != f"/taxes/{expected_id}":
            raise AssertionError(f"Wrong write route/order: {method} {endpoint}")
        if copied != tool.payload_for_rate(expected_id):
            raise AssertionError(f"Wrong minimal payload: {copied}")
        if self.fail_position == position:
            raise wc.WooError("bounded sentinel", status=503, code="sentinel")
        row = next(item for item in self.rows if item["id"] == expected_id)
        row.update(copied)
        if self.after_apply:
            self.after_apply(self, position)
        if self.timeout_position == position:
            raise TimeoutError("timeout after server application")
        if self.malformed_position == position:
            return ["not", "an", "object"], {}
        if self.wrong_id_position == position:
            return {"id": 1}, {}
        return {"id": expected_id, "server_may_include_other_fields": True}, {}

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
        for patcher in (
            mock.patch.object(tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(tool, "validate_diagnosis_evidence", return_value=tool.evidence_contract()),
            mock.patch.object(tool.wc, "append_receipt",
                              side_effect=lambda action, evidence: self.receipts.append((action, evidence))),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def stage(self, store: FakeWoo) -> tuple[Path, Path, dict]:
        output = io.StringIO()
        with store.patched(), contextlib.redirect_stdout(output):
            tool.command_stage(argparse.Namespace())
        result = json.loads(output.getvalue())
        return Path(result["plan"]), Path(result["summary"]), result

    def commit(self, store: FakeWoo, path: Path, approval: str = "APPROVED",
               probe_result: dict | Exception | None = None) -> dict:
        if probe_result is None:
            probe_result = {
                "status": "ALL_13_DESTINATIONS_AND_2_CROSS_CASES_VERIFIED",
                "case_count": 15, "request_count": 60, "checkout_submitted": False,
                "orders_created": 0, "payments_created": 0, "emails_sent": 0, "cases": [],
            }
        output = io.StringIO()
        store.per_plan_lock = tool.lock_path(path.resolve())

        @contextlib.contextmanager
        def held_lock(*, purpose: str, wait_seconds: float = 0):
            del purpose, wait_seconds
            if store.global_guard_held:
                raise AssertionError("guard unexpectedly reentered")
            store.global_guard_held = True
            try:
                yield
            finally:
                store.global_guard_held = False

        side_effect = probe_result if isinstance(probe_result, Exception) else None
        with (
            store.patched(),
            mock.patch.object(tool, "tax_delivery_commit_lock", side_effect=held_lock),
            mock.patch.object(tool, "run_store_probes", side_effect=side_effect,
                              return_value=None if side_effect else probe_result),
            contextlib.redirect_stdout(output),
        ):
            tool.command_commit(argparse.Namespace(plan=str(path), approval=approval))
        return json.loads(output.getvalue())

    @staticmethod
    def rehash(path: Path, mutate) -> None:
        value = json.loads(path.read_text(encoding="utf-8"))
        value.pop("sha256")
        mutate(value)
        value["sha256"] = tool.digest_for(value)
        path.write_text(json.dumps(value), encoding="utf-8")


class ClosedScopeAndStateTests(unittest.TestCase):
    def test_versions_and_schema_make_v1_plan_unreachable(self):
        self.assertEqual(tool.TOOL_VERSION, "2.0.0")
        self.assertEqual(tool.SCHEMA_VERSION, 2)
        self.assertTrue(OLD_V1_PLAN.exists())
        with self.assertRaises(tool.TaxDeliveryError):
            tool.load_plan(OLD_V1_PLAN)

    def test_old_plan_refuses_before_mutex_vault_or_network(self):
        with (
            mock.patch.object(tool, "PLAN_DIR", OLD_V1_PLAN.parent),
            mock.patch.object(tool, "tax_delivery_commit_lock") as guard,
            mock.patch.object(tool.wc, "load_vault") as load,
            mock.patch.object(tool.wc, "api_get") as get,
            mock.patch.object(tool.wc, "api_request") as request,
            self.assertRaises(tool.TaxDeliveryError),
        ):
            tool.command_commit(argparse.Namespace(plan=str(OLD_V1_PLAN), approval="APPROVED"))
        guard.assert_not_called()
        load.assert_not_called()
        get.assert_not_called()
        request.assert_not_called()
        self.assertFalse(tool.lock_path(OLD_V1_PLAN).exists())

    def test_cli_is_closed(self):
        parser = tool.build_parser()
        self.assertIs(parser.parse_args(["stage"]).func, tool.command_stage)
        self.assertIs(parser.parse_args(["commit", "--plan", "p", "--approval", "APPROVED"]).func,
                      tool.command_commit)
        for argv in (["stage", "--id", "2"], ["commit", "--plan", "p", "--approval", "APPROVED", "--retry"],
                     ["delete"], ["commit", "--approval", "APPROVED"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(argv)

    def test_exact_fixed_order_pairs_multicomponent_provinces(self):
        self.assertEqual(tool.WRITE_ORDER, (2, 3, 15, 4, 16, 5, 6, 7, 8, 9, 10, 11, 12, 17, 13, 18, 14))
        writes = tool.write_contract()
        self.assertEqual([row["rate_id"] for row in writes], list(tool.WRITE_ORDER))
        for sequence, row in enumerate(writes, 1):
            rate_id = tool.WRITE_ORDER[sequence - 1]
            self.assertEqual(row, {
                "sequence": sequence, "rate_id": rate_id, "method": "PUT",
                "endpoint": f"/taxes/{rate_id}", "payload": tool.payload_for_rate(rate_id),
            })
        self.assertEqual(sum("name" in row["payload"] for row in writes), 2)

    def test_routes_allow_exact_gets_and_puts_only(self):
        for route in tool.authenticated_get_contract():
            tool.validate_route("GET", route["endpoint"])
        for rate_id in tool.TARGET_IDS:
            tool.validate_route("PUT", f"/taxes/{rate_id}")
        for pair in (("POST", "/taxes"), ("DELETE", "/taxes/2"), ("PUT", "/taxes/1"),
                     ("PUT", "/taxes/batch"), ("PUT", "/settings/tax/x"),
                     ("GET", "/orders"), ("GET", "/products"), ("POST", "/checkout")):
            with self.subTest(pair=pair), self.assertRaises(tool.TaxDeliveryError):
                tool.validate_route(*pair)

    def test_payload_is_minimal_json_boolean_and_closed(self):
        for rate_id in tool.TARGET_IDS:
            payload = tool.payload_for_rate(rate_id)
            self.assertIs(payload["shipping"], True)
            self.assertEqual(set(payload), {"shipping", "name"} if rate_id in (3, 17) else {"shipping"})
        for rate_id, payload in ((2, {"shipping": 1}), (2, {"shipping": "true"}),
                                 (3, {"shipping": True}), (17, {"shipping": True, "name": "PST (9.975%)"}),
                                 (18, {"shipping": True, "rate": "6.0000"}), (1, {"shipping": True})):
            with self.subTest(rate_id=rate_id), self.assertRaises(tool.TaxDeliveryError):
                tool.validate_payload(rate_id, payload)

    def test_exact_rate_shape_fields_types_rows_and_order(self):
        self.assertEqual(tool.validate_tax_collection(before_rows()), list(tool.EXPECTED_BEFORE_ROWS))
        mutations = []
        for rate_id, field, value in (
            (1, "shipping", True), (2, "state", "BC"), (3, "name", "GST (5%)"),
            (10, "rate", "13.1"), (12, "postcode", "H0H"), (17, "priority", 1),
            (18, "compound", True), (18, "class", "reduced-rate"),
        ):
            rows = before_rows(); next(row for row in rows if row["id"] == rate_id)[field] = value; mutations.append(rows)
        rows = before_rows(); rows[1]["priority"] = True; mutations.append(rows)
        rows = before_rows(); rows[1]["shipping"] = 0; mutations.append(rows)
        rows = before_rows(); rows[1]["new_mutable"] = "surprise"; mutations.append(rows)
        rows = before_rows(); rows[1].pop("city"); mutations.append(rows)
        rows = before_rows(); rows.reverse(); mutations.append(rows)
        mutations.extend((before_rows()[:-1], before_rows() + [copy.deepcopy(before_rows()[-1])]))
        for rows in mutations:
            with self.subTest(), self.assertRaises(tool.TaxDeliveryError):
                tool.validate_tax_collection(rows)

    def test_derived_links_are_validated_then_excluded_from_semantic_fingerprint(self):
        rows = before_rows()
        projection = tool.validate_tax_collection(rows)
        self.assertNotIn("_links", json.dumps(tool.rate_state_contract(projection)))
        rows[1]["_links"]["self"][0]["href"] += "?drift"
        with self.assertRaises(tool.TaxDeliveryError):
            tool.validate_tax_collection(rows)

    def test_prefix_projection_changes_only_fixed_verified_prefix(self):
        for count in range(18):
            prefix = tool.prefix_projection(count)
            completed = set(tool.WRITE_ORDER[:count])
            for before, now in zip(tool.EXPECTED_BEFORE_ROWS, prefix):
                if now["id"] in completed:
                    self.assertTrue(now["shipping"])
                    if now["id"] in tool.NAME_CHANGES:
                        self.assertEqual(now["name"], tool.NAME_CHANGES[now["id"]])
                else:
                    self.assertEqual(now, before)
        self.assertEqual(tool.prefix_projection(17), tool.target_projection())
        self.assertEqual(tool.target_projection()[0], tool.EXPECTED_BEFORE_ROWS[0])

    def test_source_has_one_fixed_put_call_and_no_other_business_write_surface(self):
        source = Path(tool.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count('wc.api_request("PUT", endpoint'), 1)
        for token in ('wc.api_request("POST"', 'wc.api_request("DELETE"', 'wc.api_request("PATCH"',
                      '"/taxes/batch"', '"/orders/', '"/products/', '"/customers/',
                      "smtplib", "playwright", "selenium"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)


class StageAndPlanTests(PlanFixture):
    def test_stage_is_two_complete_get_only_snapshots_and_immutable_plan(self):
        store = FakeWoo()
        path, summary_path, result = self.stage(store)
        exact_routes = [(row["endpoint"], row["params"]) for row in tool.authenticated_get_contract()]
        self.assertEqual(store.get_calls, exact_routes + exact_routes)
        self.assertEqual(store.writes, [])
        self.assertFalse(result["external_write_performed"])
        self.assertEqual(result["stage_authenticated_get_count"], 18)
        self.assertEqual(result["tax_or_store_puts"], 0)
        self.assertEqual(result["persistent_store_posts"], 0)
        plan = tool.load_plan(path)
        self.assertEqual(plan["tax_rates_before"]["rows"], list(tool.EXPECTED_BEFORE_ROWS))
        self.assertEqual(plan["tax_rates_expected_final"]["rows"], tool.target_projection())
        self.assertEqual(plan["transaction"]["fixed_write_order"], list(tool.WRITE_ORDER))
        self.assertEqual(plan["semantic_state"]["settings"], store.settings)
        self.assertEqual(plan["semantic_state"]["tax_classes"], store.classes)
        self.assertEqual(plan["semantic_state"]["versions"]["woocommerce_core"]["version"], "11.0.1")
        self.assertEqual(tool.datetime.fromisoformat(plan["expires_utc"]) - tool.datetime.fromisoformat(plan["created_utc"]),
                         timedelta(hours=24))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["semantic_plan_sha256"], plan["sha256"])
        self.assertEqual(summary["physical_plan_file_sha256"], tool.file_sha256(path))
        self.assertEqual(summary["orders_created"], 0)

    def test_stage_refuses_settings_classes_and_required_plugin_identity_drift(self):
        stores = []
        store = FakeWoo(); store.settings["woocommerce_tax_based_on"] = "billing"; stores.append(store)
        store = FakeWoo(); store.classes.append({"slug": "new", "name": "New"}); stores.append(store)
        store = FakeWoo(); store.plugins[1]["version"] = ""; stores.append(store)
        store = FakeWoo(); store.plugins.pop(); stores.append(store)
        for store in stores:
            with self.subTest(), self.assertRaises(tool.TaxDeliveryError):
                self.stage(store)
            self.assertEqual(store.writes, [])

    def test_second_snapshot_drift_leaves_no_plan(self):
        store = FakeWoo()
        original = store.api_get
        count = {"tax": 0}
        def drift(endpoint, params=None, vault=None):
            if endpoint == "/taxes":
                count["tax"] += 1
                if count["tax"] == 2:
                    store.rows[1]["shipping"] = True
            return original(endpoint, params, vault)
        with (
            mock.patch.object(tool.wc, "api_get", side_effect=drift),
            mock.patch.object(tool.wc, "load_vault", return_value=vault()),
            self.assertRaises(tool.TaxDeliveryError),
        ):
            tool.command_stage(argparse.Namespace())
        self.assertFalse(self.plan_dir.exists() and list(self.plan_dir.glob("*.json")))

    def test_hash_and_rehashed_semantic_tamper_refused(self):
        path, _, _ = self.stage(FakeWoo())
        value = json.loads(path.read_text(encoding="utf-8"))
        value["transaction"]["write_count"] = 18
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(tool.TaxDeliveryError):
            tool.load_plan(path)
        mutations = (
            lambda plan: plan["transaction"]["writes"][0].update(endpoint="/taxes/1"),
            lambda plan: plan["transaction"]["writes"][1]["payload"].update(name="wrong"),
            lambda plan: plan["tax_rates_before"]["rows"][0].update(name="drift"),
            lambda plan: plan["semantic_state"]["settings"].update(woocommerce_calc_taxes="no"),
            lambda plan: plan["semantic_state"]["versions"]["ups_shipping"].update(version="99.0"),
            lambda plan: plan.update(extra="open schema"),
        )
        for mutate in mutations:
            fresh, _, _ = self.stage(FakeWoo())
            self.rehash(fresh, mutate)
            with self.subTest(), self.assertRaises(tool.TaxDeliveryError):
                tool.load_plan(fresh)

    def test_plan_records_per_field_and_aggregate_fingerprints(self):
        path, _, _ = self.stage(FakeWoo())
        plan = tool.load_plan(path)
        self.assertEqual(set(plan["tax_rates_before"]["per_field_sha256"]), {str(i) for i in range(1, 19)})
        self.assertEqual(set(plan["tax_rates_before"]["per_field_sha256"]["1"]), set(tool.TAX_FIELDS))
        self.assertRegex(plan["semantic_state"]["fingerprints"]["aggregate_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(plan["full_staged_state_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(plan["evidence"]["independent_safety_review_file_sha256"],
                         tool.INDEPENDENT_REVIEW_SHA256)


class CommitSafetyTests(PlanFixture):
    def test_approval_variants_refuse_before_mutex_vault_network_or_plan_lock(self):
        path, _, _ = self.stage(FakeWoo())
        for approval in ("approved", "Approved", " APPROVED", "APPROVED ", "APPROVED\n",
                         "APPROVED.", "ＡＰＰＲＯＶＥＤ", "", None, b"APPROVED"):
            with (
                self.subTest(approval=approval),
                mock.patch.object(tool, "tax_delivery_commit_lock") as guard,
                mock.patch.object(tool.wc, "load_vault") as load,
                mock.patch.object(tool.wc, "api_get") as get,
                self.assertRaises(tool.TaxDeliveryError),
            ):
                tool.command_commit(argparse.Namespace(plan=str(path), approval=approval))
            guard.assert_not_called(); load.assert_not_called(); get.assert_not_called()
            self.assertFalse(tool.lock_path(path.resolve()).exists())

    def test_global_busy_is_free_before_vault_network_and_permanent_lock(self):
        path, _, _ = self.stage(FakeWoo())
        with (
            mock.patch.object(tool, "tax_delivery_commit_lock",
                              side_effect=tool.TaxDeliveryCommitBusy("held")),
            mock.patch.object(tool.wc, "load_vault") as load,
            mock.patch.object(tool.wc, "api_get") as get,
            self.assertRaises(tool.TaxDeliveryCommitBusy),
        ):
            tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        load.assert_not_called(); get.assert_not_called()
        self.assertFalse(tool.lock_path(path.resolve()).exists())

    def test_commit_exact_order_payload_and_full_prefix_after_every_put(self):
        store = FakeWoo()
        path, _, _ = self.stage(store)
        store.get_calls.clear(); store.tax_snapshots.clear(); store.calls.clear()
        result = self.commit(store, path)
        self.assertEqual(store.writes, [
            ("PUT", f"/taxes/{rate_id}", tool.payload_for_rate(rate_id)) for rate_id in tool.WRITE_ORDER
        ])
        self.assertEqual(len(store.tax_snapshots), 19)  # preflight + 17 prefixes + after carts
        self.assertEqual(store.tax_snapshots[0], tool.prefix_projection(0))
        for sequence in range(1, 18):
            self.assertEqual(store.tax_snapshots[sequence], tool.prefix_projection(sequence))
        self.assertEqual(store.tax_snapshots[-1], tool.target_projection())
        route_block = [(row["endpoint"], row["params"]) for row in tool.authenticated_get_contract()]
        self.assertEqual(store.get_calls, route_block * 19)
        self.assertEqual(result["attempted_count"], 17)
        self.assertEqual(result["http_successful_count"], 17)
        self.assertEqual(result["fresh_get_verified_count"], 17)
        lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")
        self.assertEqual(lock["attempted_targets"], list(tool.WRITE_ORDER))

    def test_preflight_settings_classes_and_version_drift_is_free(self):
        mutations = (
            lambda store: store.settings.update(woocommerce_calc_taxes="no"),
            lambda store: store.settings.update(woocommerce_shipping_tax_class="standard"),
            lambda store: store.classes.pop(),
            lambda store: store.plugins[0].update(version="11.0.2"),
            lambda store: store.plugins[1].update(version="3.9.13"),
            lambda store: store.plugins[2].update(version="2.0.5"),
        )
        for mutate in mutations:
            store = FakeWoo(); path, _, _ = self.stage(store); mutate(store)
            with self.subTest(), self.assertRaises(tool.TaxDeliveryError):
                self.commit(store, path)
            self.assertEqual(store.writes, [])
            self.assertFalse(tool.lock_path(path.resolve()).exists())

    def test_preflight_every_rate_field_extra_missing_duplicate_and_reorder_drift_is_free(self):
        mutations = (
            lambda rows: rows[0].update(name="protected drift"),
            lambda rows: rows[1].update(country=""),
            lambda rows: rows[2].update(city="Vancouver"),
            lambda rows: rows[9].update(rate="13.1"),
            lambda rows: rows[16].update(priority=1),
            lambda rows: rows[17].update(compound=True),
            lambda rows: rows[1].update(new_field="surprise"),
            lambda rows: rows.reverse(),
            lambda rows: rows.pop(),
            lambda rows: rows.append(copy.deepcopy(rows[-1])),
        )
        for mutate in mutations:
            store = FakeWoo(); path, _, _ = self.stage(store); mutate(store.rows)
            with self.subTest(), self.assertRaises(tool.TaxDeliveryError):
                self.commit(store, path)
            self.assertEqual(store.writes, [])
            self.assertFalse(tool.lock_path(path.resolve()).exists())

    def test_http_failure_at_each_position_stops_without_later_write_and_counts_exactly(self):
        for position in range(1, 18):
            store = FakeWoo(); path, _, _ = self.stage(store); store.fail_position = position
            with self.subTest(position=position), self.assertRaises(tool.TaxDeliveryError):
                self.commit(store, path)
            self.assertEqual(len(store.writes), position)
            self.assertEqual([int(endpoint.rsplit("/", 1)[1]) for _, endpoint, _ in store.writes],
                             list(tool.WRITE_ORDER[:position]))
            lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
            self.assertEqual(lock["status"], "indeterminate_permanently_locked")
            self.assertEqual(lock["attempted_count"], position)
            self.assertEqual(lock["http_successful_count"], position - 1)
            self.assertEqual(lock["fresh_get_verified_count"], position - 1)
            self.assertFalse(lock["retry"]); self.assertFalse(lock["rollback"]); self.assertFalse(lock["resume"])

    def test_timeout_after_application_at_each_position_never_retries_and_counts_exactly(self):
        for position in range(1, 18):
            store = FakeWoo(); path, _, _ = self.stage(store); store.timeout_position = position
            with self.subTest(position=position), self.assertRaises(tool.TaxDeliveryError):
                self.commit(store, path)
            self.assertEqual(len(store.writes), position)
            landed_id = tool.WRITE_ORDER[position - 1]
            self.assertTrue(next(row for row in store.rows if row["id"] == landed_id)["shipping"])
            lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
            self.assertEqual(lock["attempted_count"], position)
            self.assertEqual(lock["http_successful_count"], position - 1)
            self.assertEqual(lock["fresh_get_verified_count"], position - 1)
            self.assertEqual(lock["status"], "indeterminate_permanently_locked")

    def test_malformed_or_wrong_id_response_is_http_successful_but_unverified(self):
        for mode in ("malformed_position", "wrong_id_position"):
            store = FakeWoo(); path, _, _ = self.stage(store); setattr(store, mode, 6)
            with self.subTest(mode=mode), self.assertRaises(tool.TaxDeliveryError):
                self.commit(store, path)
            self.assertEqual(len(store.writes), 6)
            lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
            self.assertEqual((lock["attempted_count"], lock["http_successful_count"],
                              lock["fresh_get_verified_count"]), (6, 6, 5))
            self.assertEqual(lock["status"], "indeterminate_permanently_locked")

    def test_crashes_before_request_after_response_before_and_after_fresh_get_stop_once(self):
        # Commit write_json calls are: permanent lock, before request, after
        # response, then after fresh-GET verification. A one-shot crash at any
        # boundary must never cause a later or repeated PUT.
        for failing_write_call, expected_writes, expected_counts in (
            (2, 0, (1, 0, 0)),
            (3, 1, (1, 1, 0)),
            (4, 1, (1, 1, 1)),
        ):
            store = FakeWoo(); path, _, _ = self.stage(store)
            original_write = tool.write_json
            calls = {"count": 0}
            def crash_once(*args, **kwargs):
                calls["count"] += 1
                if calls["count"] == failing_write_call:
                    raise OSError("simulated process-boundary persistence crash")
                return original_write(*args, **kwargs)
            with mock.patch.object(tool, "write_json", side_effect=crash_once):
                with self.subTest(boundary=failing_write_call), self.assertRaises(tool.TaxDeliveryError):
                    self.commit(store, path)
            self.assertEqual(len(store.writes), expected_writes)
            saved = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
            self.assertEqual((saved["attempted_count"], saved["http_successful_count"],
                              saved["fresh_get_verified_count"]), expected_counts)

        store = FakeWoo(); path, _, _ = self.stage(store)
        original_snapshot = tool.semantic_snapshot
        calls = {"count": 0}
        def fail_before_get(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:  # preflight is 1; first post-PUT read is 2
                raise TimeoutError("timeout before fresh complete GET")
            return original_snapshot(*args, **kwargs)
        with mock.patch.object(tool, "semantic_snapshot", side_effect=fail_before_get):
            with self.assertRaises(tool.TaxDeliveryError):
                self.commit(store, path)
        self.assertEqual(len(store.writes), 1)
        saved = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual((saved["attempted_count"], saved["http_successful_count"],
                          saved["fresh_get_verified_count"]), (1, 1, 0))

    def test_cross_row_drift_after_successful_put_is_caught_at_same_prefix(self):
        store = FakeWoo(); path, _, _ = self.stage(store)
        def drift(fake, position):
            if position == 5:
                next(row for row in fake.rows if row["id"] == 1)["name"] = "Concurrent drift"
        store.after_apply = drift
        with self.assertRaises(tool.TaxDeliveryError):
            self.commit(store, path)
        self.assertEqual(len(store.writes), 5)
        lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual((lock["attempted_count"], lock["http_successful_count"],
                          lock["fresh_get_verified_count"]), (5, 5, 4))

    def test_settings_classes_or_plugin_drift_after_put_is_caught_before_next_put(self):
        mutations = (
            lambda fake: fake.settings.update(woocommerce_tax_round_at_subtotal="yes"),
            lambda fake: fake.classes.pop(),
            lambda fake: fake.plugins[1].update(version="3.9.13"),
        )
        for mutate in mutations:
            store = FakeWoo(); path, _, _ = self.stage(store)
            store.after_apply = lambda fake, position, mutate=mutate: mutate(fake) if position == 4 else None
            with self.subTest(), self.assertRaises(tool.TaxDeliveryError):
                self.commit(store, path)
            self.assertEqual(len(store.writes), 4)
            lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
            self.assertEqual((lock["attempted_count"], lock["http_successful_count"],
                              lock["fresh_get_verified_count"]), (4, 4, 3))

    def test_post_cart_failure_is_permanently_locked_with_all_counters(self):
        store = FakeWoo(); path, _, _ = self.stage(store)
        with self.assertRaises(tool.TaxDeliveryError):
            self.commit(store, path, probe_result=tool.TaxDeliveryError("cart mismatch"))
        lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "failed_permanently_locked")
        self.assertEqual((lock["attempted_count"], lock["http_successful_count"],
                          lock["fresh_get_verified_count"]), (17, 17, 17))
        self.assertEqual(len(store.writes), 17)

    def test_replay_refuses_before_vault_and_network(self):
        store = FakeWoo(); path, _, _ = self.stage(store); self.commit(store, path)
        with (
            mock.patch.object(tool.wc, "load_vault") as load,
            mock.patch.object(tool.wc, "api_get") as get,
            mock.patch.object(tool, "tax_delivery_commit_lock") as guard,
            self.assertRaises(tool.TaxDeliveryError),
        ):
            tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        load.assert_not_called(); get.assert_not_called(); guard.assert_not_called()

    def test_plan_outside_folder_refuses_before_vault(self):
        path, _, _ = self.stage(FakeWoo())
        outside = Path(self.tmp.name) / "outside.json"; outside.write_bytes(path.read_bytes())
        with mock.patch.object(tool.wc, "load_vault") as load, self.assertRaises(tool.TaxDeliveryError):
            tool.command_commit(argparse.Namespace(plan=str(outside), approval="APPROVED"))
        load.assert_not_called()


class CartContractTests(unittest.TestCase):
    @staticmethod
    def cart_for(case: dict, item: Decimal = Decimal("50.40"),
                 shipping: Decimal = Decimal("100.10"), include_tax_lines: bool = True,
                 mismatch: str | None = None) -> dict:
        rows = tool.target_projection()
        rates = tool.province_rates(rows, case["delivery"]["state"])
        item_tax, _ = tool.expected_tax(item, rates)
        shipping_tax, _ = tool.expected_tax(shipping, rates)
        if mismatch == "item": item_tax += Decimal("0.01")
        if mismatch == "shipping": shipping_tax += Decimal("0.01")
        to_minor = lambda amount: str(int((amount * 100).to_integral_exact()))
        cart = {
            "items": [{"id": tool.STORE_VARIATION_ID, "quantity": 1,
                       "totals": {"line_total": to_minor(item), "line_total_tax": to_minor(item_tax)}}],
            "billing_address": tool.full_address(case["billing"]),
            "shipping_address": tool.full_address(case["delivery"]),
            "shipping_rates": [{"shipping_rates": [{
                "rate_id": "ups:2:01", "name": "Next Day Air (UPS)", "method_id": "ups",
                "selected": True, "price": to_minor(shipping), "taxes": to_minor(shipping_tax),
            }]}],
            "totals": {"currency_code": "CAD", "currency_minor_unit": 2,
                       "total_items": to_minor(item), "total_items_tax": to_minor(item_tax),
                       "total_shipping": to_minor(shipping), "total_shipping_tax": to_minor(shipping_tax),
                       "total_tax": to_minor(item_tax + shipping_tax)},
            "errors": [],
        }
        if include_tax_lines:
            cart["tax_lines"] = []
            for rate in rates:
                component = tool.expected_tax(item, [rate])[0] + tool.expected_tax(shipping, [rate])[0]
                cart["tax_lines"].append({"name": rate["name"], "rate": rate["rate"],
                                          "price": to_minor(component)})
        return cart

    def test_exact_15_cases_cover_all_destinations_and_two_cross_cases(self):
        self.assertEqual(len(tool.PROBE_CASES), 15)
        self.assertEqual([case["delivery"]["state"] for case in tool.PROBE_CASES[:13]],
                         ["AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"])
        self.assertEqual([(case["billing"]["state"], case["delivery"]["state"])
                          for case in tool.PROBE_CASES[-2:]], [("ON", "QC"), ("QC", "AB")])

    def test_all_15_carts_verify_delivery_item_shipping_components_labels_and_zero_orders(self):
        rows = tool.target_projection()
        for case in tool.PROBE_CASES:
            with self.subTest(case=case["label"]):
                result = tool.verify_cart(case, self.cart_for(case), rows, [200, 201, 200, 200])
                self.assertEqual(result["merchandise_tax_live_cad"], result["merchandise_tax_expected_cad"])
                self.assertEqual(result["shipping_tax_live_cad"], result["shipping_tax_expected_cad"])
                self.assertEqual(result["labels_verified_from"],
                                 "store_api_tax_lines_and_authenticated_exact_tax_rate_rows")
                self.assertFalse(result["checkout_submitted"]); self.assertFalse(result["order_created"])
                if case in tool.PROBE_CASES[-2:]:
                    self.assertTrue(result["cross_address_proof"]["actual_matches_delivery_not_billing"])
        qc = tool.verify_cart(tool.PROBE_CASES[10], self.cart_for(tool.PROBE_CASES[10]), rows,
                              [200, 201, 200, 200])
        self.assertEqual(qc["delivery_rate_labels"], ["GST (5%)", "QST (9.975%)"])

    def test_half_up_rounding_is_per_component_and_summed(self):
        rows = tool.target_projection()
        # 0.10 * 5% = 0.005 -> 0.01, proving ROUND_HALF_UP rather than bankers rounding.
        total, parts = tool.expected_tax(Decimal("0.10"), tool.province_rates(rows, "BC"))
        self.assertEqual(total, Decimal("0.02"))
        self.assertEqual([part["expected_cad"] for part in parts], ["0.01", "0.01"])
        qc_total, qc_parts = tool.expected_tax(Decimal("50.40"), tool.province_rates(rows, "QC"))
        self.assertEqual(qc_total, Decimal("7.55"))
        self.assertEqual([part["expected_cad"] for part in qc_parts], ["2.52", "5.03"])

    def test_protected_wildcard_id1_is_untouched_and_shipping_false(self):
        target = tool.target_projection()
        self.assertEqual(target[0], tool.EXPECTED_BEFORE_ROWS[0])
        self.assertIs(target[0]["shipping"], False)

    def test_probe_case_uses_only_four_exact_transient_cart_calls(self):
        case = tool.PROBE_CASES[-2]
        cart = self.cart_for(case)
        responses = [({}, "t1", 200), ({}, "t2", 201), ({}, "t3", 200), (cart, None, 200)]
        calls = []
        def requester(opener, method, endpoint, **kwargs):
            del opener
            calls.append((method, endpoint, copy.deepcopy(kwargs)))
            return responses[len(calls) - 1]
        result = tool.probe_case(case, tool.target_projection(), requester=requester)
        self.assertEqual([(method, endpoint) for method, endpoint, _ in calls], [
            ("GET", "/cart"), ("POST", "/cart/add-item"),
            ("POST", "/cart/update-customer"), ("GET", "/cart"),
        ])
        self.assertEqual(calls[1][2]["payload"], {"id": 2028, "quantity": 1})
        self.assertNotIn("checkout", json.dumps(calls))
        self.assertTrue(result["cross_address_proof"]["actual_matches_delivery_not_billing"])

    def test_run_store_probes_is_exactly_15_cases_60_requests_zero_orders(self):
        seen = []
        def fake_probe(case, rows):
            del rows
            seen.append(case["label"])
            return {"label": case["label"], "order_created": False}
        with mock.patch.object(tool, "probe_case", side_effect=fake_probe):
            result = tool.run_store_probes(tool.target_projection())
        self.assertEqual(seen, [case["label"] for case in tool.PROBE_CASES])
        self.assertEqual(result["case_count"], 15)
        self.assertEqual(result["request_count"], 60)
        self.assertEqual(result["orders_created"], 0)

    def test_cart_mismatches_wrong_labels_and_wrong_contract_refuse(self):
        case = tool.PROBE_CASES[10]
        for mismatch in ("item", "shipping"):
            with self.subTest(mismatch=mismatch), self.assertRaises(tool.TaxDeliveryError):
                tool.verify_cart(case, self.cart_for(case, mismatch=mismatch), tool.target_projection(),
                                 [200, 201, 200, 200])
        cart = self.cart_for(case); cart["tax_lines"][1]["name"] = "PST (9.975%)"
        with self.assertRaises(tool.TaxDeliveryError):
            tool.verify_cart(case, cart, tool.target_projection(), [200, 201, 200, 200])
        cart = self.cart_for(case); cart["shipping_rates"][0]["shipping_rates"][0]["method_id"] = "flat_rate"
        with self.assertRaises(tool.TaxDeliveryError):
            tool.verify_cart(case, cart, tool.target_projection(), [200, 201, 200, 200])


if __name__ == "__main__":
    unittest.main(verbosity=2)
