"""Unit tests for the one fixed WooCommerce FNPT parent-2061 go-live transaction.

Every WooCommerce transport call is mocked. These tests make no network request
and never stage or commit Git content.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import csv
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
import woocommerce_fnpt_go_live_tool as tool  # noqa: E402

SECRET_VALUE = "ck_SENTINEL_SECRET_MUST_NOT_LEAK"
PROTECTED_SENTINEL = "SENTINEL-PROTECTED-DESCRIPTION-MUST-NOT-LEAK"


def vault() -> dict[str, str]:
    return {
        "site_url": wc.ALLOWED_ORIGIN,
        "consumer_key": "ck_abcdefghijklmnopqrstuvwxyz",
        "consumer_secret": "cs_abcdefghijklmnopqrstuvwxyz",
        "declared_permissions": "read_write",
    }


def parent_record(**overrides) -> dict:
    value = {
        "id": tool.PARENT_ID,
        "name": tool.PARENT_NAME,
        "sku": tool.PARENT_SKU,
        "type": tool.PARENT_TYPE,
        "status": "draft",
        "images": [],
        "price": "37.44",
        "regular_price": "",
        "price_html": "derived starting price",
        "yoast_head": "derived SEO",
        "yoast_head_json": {"derived": True},
        "purchasable": True,
        "on_sale": False,
        "permalink": "https://frpdepots.com/?post_type=product&p=2061",
        "description": PROTECTED_SENTINEL,
        "short_description": "FNPT",
        "categories": [{"id": 15, "name": "Uncategorized"}],
        "attributes": [{"id": 0, "name": "RESIN TYPE", "options": ["D411"]}],
        "meta_data": [{"id": 1, "key": "_private_key", "value": SECRET_VALUE}],
        "shipping_class": "",
        "manage_stock": False,
        "stock_quantity": None,
        "stock_status": "instock",
        "date_modified": "2026-08-12T01:00:00",
        "date_modified_gmt": "2026-08-12T01:00:00",
    }
    value.update(overrides)
    return value


def variation_record(variation_id: int, sku: str, index: int, **overrides) -> dict:
    if variation_id == 2062:
        regular = tool.SUPPORTED_PRICES[2062]
    else:
        regular = "60.00" if variation_id == 2087 else "75.00" if variation_id == 2088 else "50.20"
    managed = index % 3 != 0
    quantity = 0 if managed else None
    value = {
        "id": variation_id,
        "parent_id": tool.PARENT_ID,
        "type": "variation",
        "name": f"FNPT child {variation_id}",
        "sku": sku,
        "status": "publish",
        "regular_price": regular,
        "price": regular,
        "sale_price": "",
        "purchasable": True,
        "on_sale": False,
        "permalink": f"https://frpdepots.com/?post_type=product&variation={variation_id}",
        "description": PROTECTED_SENTINEL,
        "attributes": [{"id": 0, "name": "Length", "option": "6\""}],
        "image": None,
        "meta_data": [{"id": variation_id, "key": "_private_key", "value": SECRET_VALUE}],
        "shipping_class": "",
        "manage_stock": managed,
        "stock_quantity": quantity,
        "stock_status": "outofstock" if managed else "instock",
        "backorders": "no",
        "date_modified": "2026-08-12T01:00:00",
        "date_modified_gmt": "2026-08-12T01:00:00",
    }
    value.update(overrides)
    return value


def catalog() -> tuple[dict, list[dict]]:
    return parent_record(), [
        variation_record(variation_id, sku, index)
        for index, (variation_id, sku) in enumerate(tool.EXPECTED_VARIATIONS)
    ]


def stock_preflight() -> dict:
    return {
        "source": "Zoho Inventory actual_available_stock vs WooCommerce stock_quantity",
        "matched_count": 64,
        "mismatch_count": 0,
        "in_stock_count": 0,
        "out_of_stock_count": 64,
        "stock_fingerprint": "a" * 64,
    }


class FakeStore:
    """In-memory WooCommerce model with complete call/write recording."""

    def __init__(self, parent: dict | None = None, variations: list[dict] | None = None):
        default_parent, default_variations = catalog()
        self.parent = copy.deepcopy(parent if parent is not None else default_parent)
        rows = variations if variations is not None else default_variations
        self.variations = {int(row["id"]): copy.deepcopy(row) for row in rows}
        self.calls: list[tuple[str, str]] = []
        self.writes: list[tuple[str, str, dict]] = []
        self.fail_on_write_number: int | None = None
        self.fail_postwrite_get_endpoint: str | None = None
        self._postwrite_get_failed = False
        self.corrupt_protected_on_write_number: int | None = None
        self.bad_gallery = False
        self.corrupt_final_stock = False
        self._final_corrupted = False

    def api_get(self, endpoint, params=None, vault=None):
        self.calls.append(("GET", endpoint))
        if (self.fail_postwrite_get_endpoint == endpoint and self.writes
                and not self._postwrite_get_failed):
            self._postwrite_get_failed = True
            raise wc.WooError("sentinel read failure", status=503, code="sentinel")
        if endpoint == f"/products/{tool.PARENT_ID}":
            return copy.deepcopy(self.parent), {}
        prefix = f"/products/{tool.PARENT_ID}/variations/"
        if endpoint.startswith(prefix):
            variation_id = int(endpoint.rsplit("/", 1)[1])
            if variation_id in self.variations:
                return copy.deepcopy(self.variations[variation_id]), {}
        raise wc.WooError("not found", status=404, code="not_found")

    def get_all(self, endpoint, params=None, *, vault=None, max_pages=200, max_items=20000):
        self.calls.append(("GET", endpoint))
        if endpoint != f"/products/{tool.PARENT_ID}/variations":
            raise wc.WooError("not found", status=404)
        if self.corrupt_final_stock and self.parent.get("status") == "publish" and not self._final_corrupted:
            self.variations[2062]["stock_quantity"] = 999
            self._final_corrupted = True
        return [copy.deepcopy(self.variations[key]) for key in sorted(self.variations)]

    def api_request(self, method, endpoint, params=None, payload=None, vault=None, timeout=60):
        payload_copy = copy.deepcopy(payload or {})
        self.calls.append((method, endpoint))
        self.writes.append((method, endpoint, payload_copy))
        write_number = len(self.writes)
        if self.fail_on_write_number == write_number:
            raise wc.WooError("sentinel write failure", status=503, code="sentinel")
        if method != "PUT":
            raise wc.WooError("wrong verb", status=405)
        if endpoint == f"/products/{tool.PARENT_ID}":
            if "images" in payload_copy:
                self.parent["images"] = [
                    {
                        "id": 5000 + index,
                        "src": f"https://frpdepots.com/wp-content/uploads/fnpt-{index}.png",
                        "alt": row["alt"] if not (self.bad_gallery and index == 3) else "WRONG ALT",
                    }
                    for index, row in enumerate(payload_copy["images"], start=1)
                ]
            if "status" in payload_copy:
                self.parent["status"] = payload_copy["status"]
                self.parent["permalink"] = "https://frpdepots.com/fnpt-coupling/"
            self.parent["date_modified"] = "2026-08-12T02:00:00"
            self.parent["date_modified_gmt"] = "2026-08-12T02:00:00"
            return copy.deepcopy(self.parent), {}
        prefix = f"/products/{tool.PARENT_ID}/variations/"
        if endpoint.startswith(prefix):
            variation_id = int(endpoint.rsplit("/", 1)[1])
            record = self.variations[variation_id]
            record.update(payload_copy)
            if "regular_price" in payload_copy:
                record["price"] = payload_copy["regular_price"]
                # WooCommerce derives parent price HTML and Yoast fields from child saves.
                self.parent["price"] = payload_copy["regular_price"]
                self.parent["price_html"] = f"derived {payload_copy['regular_price']}"
                self.parent["yoast_head"] = f"derived SEO {payload_copy['regular_price']}"
                self.parent["yoast_head_json"] = {"derived_price": payload_copy["regular_price"]}
                self.parent["date_modified"] = "2026-08-12T02:00:00"
                self.parent["date_modified_gmt"] = "2026-08-12T02:00:00"
            record["purchasable"] = record["status"] == "publish"
            record["date_modified"] = "2026-08-12T02:00:00"
            record["date_modified_gmt"] = "2026-08-12T02:00:00"
            if self.corrupt_protected_on_write_number == write_number:
                record["description"] = "CORRUPTED"
            return copy.deepcopy(record), {}
        raise wc.WooError("not found", status=404)

    @contextlib.contextmanager
    def patched(self):
        with (
            mock.patch.object(tool.wc, "api_get", side_effect=self.api_get),
            mock.patch.object(tool.wc, "get_all", side_effect=self.get_all),
            mock.patch.object(tool.wc, "api_request", side_effect=self.api_request),
            mock.patch.object(tool.wc, "load_vault", return_value=vault()),
        ):
            yield


class PlanFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plan_dir = Path(self.tmp.name) / "plans"
        self.plan_patch = mock.patch.object(tool, "PLAN_DIR", self.plan_dir)
        self.receipt_patch = mock.patch.object(tool.wc, "append_receipt")
        self.plan_patch.start()
        self.receipt_patch.start()
        self.addCleanup(self.plan_patch.stop)
        self.addCleanup(self.receipt_patch.stop)

    def stage(self, store: FakeStore) -> tuple[Path, dict]:
        output = io.StringIO()
        with (
            store.patched(),
            mock.patch.object(tool, "validate_fixed_evidence"),
            mock.patch.object(tool, "validate_image_assets"),
            mock.patch.object(tool, "verify_zoho_physical_stock", return_value=stock_preflight()),
            contextlib.redirect_stdout(output),
        ):
            tool.command_stage(argparse.Namespace())
        result = json.loads(output.getvalue())
        return Path(result["plan"]), result

    def commit(self, store: FakeStore, path: Path, approval: str = "APPROVED") -> dict:
        output = io.StringIO()
        with (
            store.patched(),
            mock.patch.object(tool, "validate_fixed_evidence"),
            mock.patch.object(tool, "validate_image_assets"),
            mock.patch.object(tool, "verify_zoho_physical_stock", return_value=stock_preflight()),
            contextlib.redirect_stdout(output),
        ):
            tool.command_commit(argparse.Namespace(plan=str(path), approval=approval))
        return json.loads(output.getvalue())

    @staticmethod
    def rewrite(path: Path, mutate, *, rehash: bool) -> None:
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        if rehash:
            core = {key: item for key, item in value.items() if key != "sha256"}
            value["sha256"] = tool.digest_for(core)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class FixedContractTests(unittest.TestCase):
    def test_cli_exposes_only_stage_and_commit_and_no_generic_transaction_parameters(self):
        parser = tool.build_parser()
        stage = parser.parse_args(["stage"])
        self.assertIs(stage.func, tool.command_stage)
        commit = parser.parse_args(["commit", "--plan", "p", "--approval", "APPROVED"])
        self.assertIs(commit.func, tool.command_commit)
        for argv in (
            ["stage", "--product-id", "999"],
            ["stage", "--url", "https://example.com/x.png"],
            ["stage", "--price", "1.00"],
            ["commit", "--plan", "p", "--approval", "APPROVED", "--sku", "X"],
        ):
            with (
                self.subTest(argv=argv),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                parser.parse_args(argv)

    def test_fixed_parent_and_exact_64_ids_skus(self):
        self.assertEqual(tool.PARENT_ID, 2061)
        self.assertEqual(tool.EXACT_ORIGIN, wc.ALLOWED_ORIGIN)
        self.assertEqual(tool.PARENT_NAME, "FNPT Coupling, Threaded On both ends")
        self.assertEqual(tool.PARENT_TYPE, "variable")
        self.assertEqual(len(tool.EXPECTED_VARIATIONS), 64)
        self.assertEqual(len(dict(tool.EXPECTED_VARIATIONS)), 64)
        self.assertEqual([row[0] for row in tool.EXPECTED_VARIATIONS], list(range(2062, 2126)))

    def test_supported_count_and_all_32_exact_prices(self):
        expected = {
            2062: "37.44", 2063: "44.64", 2064: "47.16", 2065: "49.32",
            2066: "59.04", 2067: "74.52", 2068: "59.04", 2069: "74.82",
            2070: "81.00", 2071: "93.54", 2072: "102.89", 2073: "112.24",
            2074: "44.28", 2075: "46.08", 2076: "182.52", 2077: "187.06",
            2078: "23.40", 2079: "40.32", 2080: "35.08", 2081: "42.48",
            2082: "37.05", 2083: "46.76", 2084: "37.05", 2085: "46.76",
            2086: "50.65", 2087: "58.46", 2088: "64.31", 2089: "70.15",
            2090: "33.13", 2091: "41.04", 2092: "114.14", 2093: "116.91",
        }
        self.assertEqual(tool.SUPPORTED_PRICES, expected)
        self.assertEqual(len(tool.SUPPORTED_PRICES), 32)
        self.assertEqual(len(tool.UNSUPPORTED_IDS), 32)
        self.assertEqual(tool.PREEXISTING_CORRECT_IDS, {2062})
        self.assertEqual(len(tool.WRITE_TARGET_IDS), 63)
        self.assertEqual(set(tool.UNSUPPORTED_IDS) | set(expected), set(range(2062, 2126)))

    def test_fixed_prices_are_exactly_the_32_pinned_aug12_csv_rows(self):
        with tool.PRICING_CSV.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        csv_prices = {int(row["variation_id"]): row["final_online_price_cad"]
                      for row in rows}
        self.assertEqual(len(csv_prices), 32)
        self.assertEqual(tool.SUPPORTED_PRICES, csv_prices)
        self.assertEqual(
            {int(row["variation_id"]) for row in rows
             if row["pricing_basis"] == "SUPPLIER_X_3_6_NO_EXACT_FRP_SUPPLY_8IN_EQUIVALENT"},
            {2063, 2075, 2079, 2091},
        )

    def test_all_32_unsupported_payloads_are_private_and_do_not_touch_price(self):
        for variation_id in tool.UNSUPPORTED_IDS:
            with self.subTest(variation_id=variation_id):
                self.assertEqual(tool.target_payload(variation_id), {"status": "private"})
        for variation_id, price in tool.SUPPORTED_PRICES.items():
            self.assertEqual(tool.target_payload(variation_id),
                             {"regular_price": price, "status": "publish"})

    def test_stock_fields_are_absent_from_every_payload(self):
        payloads = [tool.target_payload(variation_id) for variation_id, _ in tool.EXPECTED_VARIATIONS]
        payloads += [tool.image_payload(), {"status": "publish"}]
        for payload in payloads:
            self.assertTrue(set(payload).isdisjoint(tool.STOCK_FIELDS))

    def test_only_six_fixed_source_urls_and_assets_are_reachable(self):
        specs = tool.public_image_specs()
        self.assertEqual(len(specs), 6)
        self.assertEqual([row["order"] for row in specs], [1, 2, 3, 4, 5, 6])
        self.assertEqual(len({row["src"] for row in specs}), 6)
        self.assertEqual(len({row["file"] for row in specs}), 6)
        self.assertTrue(all(row["src"].startswith("https://v3b.fal.media/files/") for row in specs))
        payload = tool.image_payload()
        self.assertEqual(set(payload), {"images"})
        self.assertEqual(payload["images"], [
            {"src": row["src"], "alt": row["alt"]} for row in specs
        ])
        source = Path(tool.__file__).read_text(encoding="utf-8")
        rejected_markers = (
            "0aa5fb95/Uqdtkj3Q9YRuceD2T4_3R_JsmypqN5",  # accepted; sanity below
        )
        self.assertIn(rejected_markers[0], source)
        self.assertEqual(source.count("https://v3b.fal.media/files/"), 6)

    def test_local_image_hash_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_dir = Path(tmp)
            specs = []
            for index in range(1, 7):
                path = image_dir / f"{index}.png"
                path.write_bytes(f"image-{index}".encode())
                specs.append({
                    "order": index, "file": str(path),
                    "src": f"https://v3b.fal.media/files/fixed/{index}.png",
                    "sha256": tool.file_sha256(path), "alt": f"Alt {index}",
                })
            specs[4]["sha256"] = "0" * 64
            manifest = image_dir / "manifest.json"
            manifest.write_text(json.dumps({"images": [
                {"file": row["file"], "generated_url": row["src"], "sha256": row["sha256"]}
                for row in specs
            ]}), encoding="utf-8")
            with (
                mock.patch.object(tool, "IMAGE_DIR", image_dir),
                mock.patch.object(tool, "IMAGE_MANIFEST", manifest),
                mock.patch.object(tool, "IMAGE_MANIFEST_SHA256", tool.file_sha256(manifest)),
                mock.patch.object(tool, "IMAGE_SPECS", tuple(specs)),
                self.assertRaises(tool.GoLiveError) as caught,
            ):
                tool.validate_image_assets()
            self.assertIn("position 5", str(caught.exception))

    def test_all_six_fixed_local_image_assets_match_the_pinned_hashes(self):
        tool.validate_fixed_evidence()
        tool.validate_image_assets()
        self.assertEqual(
            [tool.file_sha256(Path(row["file"])) for row in tool.IMAGE_SPECS],
            [row["sha256"] for row in tool.IMAGE_SPECS],
        )

    def test_only_fixed_get_put_routes_and_no_forbidden_verb(self):
        accepted = [
            ("GET", "/products/2061"),
            ("GET", "/products/2061/variations"),
            ("PUT", "/products/2061"),
            ("GET", "/products/2061/variations/2062"),
            ("PUT", "/products/2061/variations/2125"),
        ]
        for pair in accepted:
            tool.validate_route(*pair)
        with self.assertRaises(tool.GoLiveError):
            tool.validate_route("PUT", "/products/2061/variations/2062")
        refused = [
            ("DELETE", "/products/2061"), ("PATCH", "/products/2061"),
            ("POST", "/products/2061"), ("PUT", "/products/999"),
            ("PUT", "/products/2061/variations/2126"),
            ("PUT", "/products/2061/variations/batch"),
            ("GET", "/orders"), ("PUT", "/customers/1"),
            ("POST", "/refunds"), ("PUT", "/coupons/1"),
            ("PUT", "/settings/general"), ("POST", "/plugins"),
        ]
        for pair in refused:
            with self.subTest(pair=pair), self.assertRaises(tool.GoLiveError):
                tool.validate_route(*pair)

    def test_no_mail_browser_secret_or_generic_write_surface_in_source(self):
        source = Path(tool.__file__).read_text(encoding="utf-8").casefold()
        for token in (
            "smtplib", "mail.send", "outlook", "playwright", "selenium",
            "browser_", "api_request(\"post\"", "api_request(\"delete\"",
            "api_request(\"patch\"", "/orders", "/customers", "/payments",
            "/refunds", "/coupons", "/settings", "/plugins", "/batch",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_zoho_physical_stock_preflight_requires_exact_64_and_exact_quantities(self):
        _, rows = catalog()
        for row in rows:
            row["manage_stock"] = True
            row["stock_quantity"] = 0
            row["stock_status"] = "outofstock"
        zoho = {sku: tool.Decimal("0") for _, sku in tool.EXPECTED_VARIATIONS}
        with mock.patch.object(tool, "read_zoho_physical_stock", return_value=zoho):
            result = tool.verify_zoho_physical_stock(rows)
        self.assertEqual(result["matched_count"], 64)
        self.assertEqual(result["mismatch_count"], 0)
        self.assertEqual(result["in_stock_count"], 0)
        self.assertRegex(result["stock_fingerprint"], r"^[0-9a-f]{64}$")

        changed = dict(zoho)
        changed[tool.EXPECTED_VARIATIONS[0][1]] = tool.Decimal("1")
        with (
            mock.patch.object(tool, "read_zoho_physical_stock", return_value=changed),
            self.assertRaises(tool.GoLiveError),
        ):
            tool.verify_zoho_physical_stock(rows)

    def test_zoho_listing_requires_exact_sku_set_unique_rows_and_physical_field(self):
        rows = [
            {"sku": sku, "actual_available_stock": 0}
            for _, sku in tool.EXPECTED_VARIATIONS
        ]
        zoho_vault = {
            "api_domain": "https://www.zohoapis.ca",
            "inventory_organization_id": "96274000000000001",
        }
        with (
            mock.patch.object(tool.zoho_tool, "load_vault", return_value=zoho_vault),
            mock.patch.object(
                tool.zoho_tool, "refresh_access_token", return_value=("token", zoho_vault)
            ),
            mock.patch.object(
                tool.zoho_tool, "api_get",
                return_value={"items": rows, "page_context": {"has_more_page": False}},
            ),
        ):
            result = tool.read_zoho_physical_stock()
        self.assertEqual(set(result), {sku for _, sku in tool.EXPECTED_VARIATIONS})

        bad = copy.deepcopy(rows)
        bad[0].pop("actual_available_stock")
        with (
            mock.patch.object(tool.zoho_tool, "load_vault", return_value=zoho_vault),
            mock.patch.object(
                tool.zoho_tool, "refresh_access_token", return_value=("token", zoho_vault)
            ),
            mock.patch.object(
                tool.zoho_tool, "api_get",
                return_value={"items": bad, "page_context": {"has_more_page": False}},
            ),
            self.assertRaises(tool.GoLiveError),
        ):
            tool.read_zoho_physical_stock()


class StageAndPlanTests(PlanFixture):
    def test_stage_is_read_only_and_writes_24_hour_hashed_plan(self):
        store = FakeStore()
        path, result = self.stage(store)
        self.assertEqual(store.writes, [])
        self.assertTrue(path.is_file())
        self.assertFalse(result["external_write_performed"])
        self.assertEqual(result["supported_publish_count"], 32)
        self.assertEqual(result["unsupported_private_count"], 32)
        self.assertEqual(result["stock_preflight"], stock_preflight())
        plan = tool.load_plan(path)
        created = tool.datetime.fromisoformat(plan["created_utc"])
        expires = tool.datetime.fromisoformat(plan["expires_utc"])
        self.assertEqual(expires - created, timedelta(hours=24))
        self.assertEqual(len(plan["variation_targets"]), 64)

    def test_wrong_parent_id_identity_type_or_status_is_refused_without_write(self):
        changes = (
            {"id": 999}, {"name": "Other"}, {"sku": "OTHER"},
            {"type": "simple"}, {"status": "publish"},
        )
        for change in changes:
            with self.subTest(change=change):
                store = FakeStore(parent=parent_record(**change))
                with self.assertRaises(tool.GoLiveError):
                    self.stage(store)
                self.assertEqual(store.writes, [])

    def test_wrong_missing_duplicate_or_foreign_variation_is_refused_without_write(self):
        _, rows = catalog()
        mutations = []
        mutations.append(rows[:-1])
        wrong_id = copy.deepcopy(rows)
        wrong_id[0]["id"] = 999
        mutations.append(wrong_id)
        wrong_sku = copy.deepcopy(rows)
        wrong_sku[0]["sku"] = "WRONG"
        mutations.append(wrong_sku)
        foreign = copy.deepcopy(rows)
        foreign[0]["parent_id"] = 999
        mutations.append(foreign)
        duplicate = copy.deepcopy(rows)
        duplicate[-1] = copy.deepcopy(duplicate[0])
        mutations.append(duplicate)
        for changed in mutations:
            with self.subTest(count=len(changed), first=changed[0]["id"]):
                store = FakeStore(variations=changed)
                with self.assertRaises(tool.GoLiveError):
                    self.stage(store)
                self.assertEqual(store.writes, [])

    def test_nonempty_parent_gallery_is_refused_at_stage(self):
        store = FakeStore(parent=parent_record(images=[{"id": 1}]))
        with self.assertRaises(tool.GoLiveError):
            self.stage(store)
        self.assertEqual(store.writes, [])

    def test_hash_tamper_and_rehashed_semantic_tamper_are_refused(self):
        path, _ = self.stage(FakeStore())
        self.rewrite(path, lambda plan: plan["variation_targets"][0]["payload"].update(
            {"regular_price": "0.01"}), rehash=False)
        with self.assertRaises(tool.GoLiveError) as caught:
            tool.load_plan(path)
        self.assertIn("hash", str(caught.exception).casefold())

        path, _ = self.stage(FakeStore())
        self.rewrite(path, lambda plan: plan["variation_targets"][0].update(
            {"variation_id": 999}), rehash=True)
        with self.assertRaises(tool.GoLiveError):
            tool.load_plan(path)

    def test_expired_or_non_24_hour_plan_is_refused(self):
        path, _ = self.stage(FakeStore())
        def expire(plan):
            plan["expires_utc"] = plan["created_utc"]
        self.rewrite(path, expire, rehash=True)
        with self.assertRaises(tool.GoLiveError):
            tool.load_plan(path)

        path, _ = self.stage(FakeStore())
        def lengthen(plan):
            created = tool.datetime.fromisoformat(plan["created_utc"])
            plan["expires_utc"] = (created + timedelta(hours=25)).isoformat()
        self.rewrite(path, lengthen, rehash=True)
        with self.assertRaises(tool.GoLiveError):
            tool.load_plan(path)

    def test_plan_preview_and_plan_do_not_expose_secret_or_protected_values(self):
        store = FakeStore()
        path, result = self.stage(store)
        text = path.read_text(encoding="utf-8") + json.dumps(result)
        self.assertNotIn(SECRET_VALUE, text)
        self.assertNotIn(PROTECTED_SENTINEL, text)
        self.assertNotIn(vault()["consumer_key"], text)
        self.assertNotIn(vault()["consumer_secret"], text)


class CommitTests(PlanFixture):
    def test_approval_is_byte_exact_and_refused_before_vault_network_or_lock(self):
        path, _ = self.stage(FakeStore())
        wrong = ("approved", "Approved", " APPROVED", "APPROVED ", "APPROVED.",
                 '"APPROVED"', "APPROVED\n", "")
        for approval in wrong:
            with (
                self.subTest(approval=repr(approval)),
                mock.patch.object(tool.wc, "load_vault") as load,
                mock.patch.object(tool, "validate_image_assets") as images,
                self.assertRaises(tool.GoLiveError),
            ):
                tool.command_commit(argparse.Namespace(plan=str(path), approval=approval))
            load.assert_not_called()
            images.assert_not_called()
            self.assertFalse(tool.lock_path(path.resolve()).exists())
        tool.require_exact_approval("APPROVED")

    def test_stale_parent_or_variation_is_refused_before_lock_and_write(self):
        for kind in ("parent", "variation"):
            with self.subTest(kind=kind):
                store = FakeStore()
                path, _ = self.stage(store)
                if kind == "parent":
                    store.parent["description"] = "changed"
                else:
                    store.variations[2062]["description"] = "changed"
                with self.assertRaises(tool.GoLiveError):
                    self.commit(store, path)
                self.assertEqual(store.writes, [])
                self.assertFalse(tool.lock_path(path.resolve()).exists())

    def test_image_hash_mismatch_is_refused_before_vault_network_lock_and_write(self):
        store = FakeStore()
        path, _ = self.stage(store)
        with (
            mock.patch.object(tool, "validate_image_assets", side_effect=tool.GoLiveError("hash mismatch")),
            mock.patch.object(tool.wc, "load_vault") as load,
            self.assertRaises(tool.GoLiveError),
        ):
            tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        load.assert_not_called()
        self.assertFalse(tool.lock_path(path.resolve()).exists())
        self.assertEqual(store.writes, [])

    def test_successful_commit_is_exact_preserves_stock_and_final_verifies(self):
        store = FakeStore()
        before_stock = {key: tool.stock_projection(value) for key, value in store.variations.items()}
        path, _ = self.stage(store)
        result = self.commit(store, path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["writes_completed"], 65)
        self.assertEqual(len(store.writes), 65)
        self.assertFalse(any(endpoint.endswith("/variations/2062") for _, endpoint, _ in store.writes))
        self.assertEqual(result["outcome"], {
            "parent_id": 2061, "parent_status": "publish", "gallery_count": 6,
            "supported_published_count": 32, "unsupported_private_count": 32,
            "stock_preserved_count": 64, "protected_state_verified": True,
        })
        for variation_id, record in store.variations.items():
            self.assertEqual(tool.stock_projection(record), before_stock[variation_id])
            if variation_id in tool.SUPPORTED_PRICES:
                self.assertEqual(record["status"], "publish")
                self.assertEqual(record["regular_price"], tool.SUPPORTED_PRICES[variation_id])
            else:
                self.assertEqual(record["status"], "private")
                self.assertEqual(record["regular_price"],
                                 "60.00" if variation_id == 2087 else
                                 "75.00" if variation_id == 2088 else "50.20")
        lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")
        self.assertEqual(lock["attempt"], 1)

    def test_parent_publish_is_the_last_write_and_status_only(self):
        store = FakeStore()
        path, _ = self.stage(store)
        self.commit(store, path)
        self.assertEqual(store.writes[-1],
                         ("PUT", "/products/2061", {"status": "publish"}))
        self.assertEqual(store.writes[-2][0:2], ("PUT", "/products/2061"))
        self.assertEqual(store.writes[-2][2], tool.image_payload())
        self.assertTrue(all(endpoint != "/products/2061" for _, endpoint, _ in store.writes[:-2]))

    def test_lock_exists_before_first_write(self):
        store = FakeStore()
        path, _ = self.stage(store)
        real_request = store.api_request
        seen: list[bool] = []
        def checked(*args, **kwargs):
            seen.append(tool.lock_path(path.resolve()).exists())
            return real_request(*args, **kwargs)
        with (
            mock.patch.object(store, "api_request", side_effect=checked),
            store.patched(),
            mock.patch.object(tool, "validate_image_assets"),
            mock.patch.object(tool, "verify_zoho_physical_stock", return_value=stock_preflight()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertTrue(seen)
        self.assertTrue(all(seen))

    def test_failure_in_each_prepublication_phase_locks_stops_and_never_publishes(self):
        scenarios = (
            ("first_remaining_variation_write", {"fail_on_write_number": 1}, 1),
            ("variation_readback", {"fail_postwrite_get_endpoint":
                                    "/products/2061/variations/2063"}, 1),
            ("variation_protected_readback", {"corrupt_protected_on_write_number": 1}, 1),
            ("gallery_write", {"fail_on_write_number": 64}, 64),
            ("gallery_readback", {"bad_gallery": True}, 64),
            ("publish_write", {"fail_on_write_number": 65}, 65),
        )
        for name, settings, expected_attempts in scenarios:
            with self.subTest(name=name):
                store = FakeStore()
                path, _ = self.stage(store)
                for key, value in settings.items():
                    setattr(store, key, value)
                with self.assertRaises(tool.GoLiveError):
                    self.commit(store, path)
                lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
                self.assertEqual(lock["status"], "indeterminate")
                self.assertEqual(lock["attempt"], 1)
                self.assertEqual(len(store.writes), expected_attempts)
                self.assertEqual(store.parent["status"], "draft")
                publish_attempts = [row for row in store.writes
                                    if row == ("PUT", "/products/2061", {"status": "publish"})]
                self.assertEqual(len(publish_attempts), 1 if name == "publish_write" else 0)

    def test_final_verifier_failure_locks_after_last_write_and_never_retries(self):
        store = FakeStore()
        path, _ = self.stage(store)
        store.corrupt_final_stock = True
        with self.assertRaises(tool.GoLiveError):
            self.commit(store, path)
        self.assertEqual(len(store.writes), 65)
        self.assertEqual(store.writes[-1], ("PUT", "/products/2061", {"status": "publish"}))
        lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate")
        self.assertEqual(lock["attempt"], 1)
        self.assertEqual(lock["phase"], "fresh_complete_final_verification")
        writes = list(store.writes)
        with self.assertRaises(tool.GoLiveError):
            self.commit(store, path)
        self.assertEqual(store.writes, writes)

    def test_replay_is_refused_after_success_before_network_or_write(self):
        store = FakeStore()
        path, _ = self.stage(store)
        self.commit(store, path)
        writes = list(store.writes)
        with mock.patch.object(tool.wc, "load_vault") as load, self.assertRaises(tool.GoLiveError):
            tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        load.assert_not_called()
        self.assertEqual(store.writes, writes)

    def test_protected_parent_or_variation_readback_change_is_indeterminate(self):
        store = FakeStore()
        path, _ = self.stage(store)
        store.corrupt_protected_on_write_number = 10
        with self.assertRaises(tool.GoLiveError):
            self.commit(store, path)
        self.assertEqual(len(store.writes), 10)
        lock_text = tool.lock_path(path.resolve()).read_text(encoding="utf-8")
        self.assertIn('"status": "indeterminate"', lock_text)
        self.assertNotIn(SECRET_VALUE, lock_text)
        self.assertNotIn(PROTECTED_SENTINEL, lock_text)

    def test_commit_refuses_wrong_origin_or_permission_before_lock(self):
        for bad in (
            {**vault(), "declared_permissions": "read"},
            {**vault(), "site_url": "https://example.com"},
        ):
            with self.subTest(bad=bad):
                store = FakeStore()
                path, _ = self.stage(store)
                with (
                    store.patched(),
                    mock.patch.object(tool, "validate_image_assets"),
                    mock.patch.object(tool.wc, "load_vault", return_value=bad),
                    self.assertRaises(tool.GoLiveError),
                ):
                    tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
                self.assertFalse(tool.lock_path(path.resolve()).exists())
                self.assertEqual(store.writes, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
