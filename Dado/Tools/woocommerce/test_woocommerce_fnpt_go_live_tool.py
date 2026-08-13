"""No-network safety tests for the fixed FNPT parent-2061 publication recovery."""
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
import woocommerce_fnpt_go_live_tool as tool  # noqa: E402

SECRET = "ck_SENTINEL_MUST_NOT_LEAK"


def vault() -> dict[str, str]:
    return {
        "site_url": wc.ALLOWED_ORIGIN,
        "consumer_key": "ck_abcdefghijklmnopqrstuvwxyz",
        "consumer_secret": "cs_abcdefghijklmnopqrstuvwxyz",
        "declared_permissions": "read_write",
    }


def gallery_reconciliation() -> dict:
    rows = []
    for index, spec in enumerate(tool.IMAGE_SPECS, start=1):
        rows.append({
            "position": index,
            "id": 2148 + index,
            "src": f"https://frpdepots.com/wp-content/uploads/2026/08/fnpt-{index}.png",
            "name": f"fnpt-{index}.png",
            "alt": spec["alt"],
            "download": {
                "downloaded": True,
                "bytes": 1000 + index,
                "content_type": "image/png",
                "sha256": spec["sha256"],
            },
            "expected_source_file_sha256": spec["sha256"],
            "alt_matches": True,
            "download_sha256_matches_source_file": True,
        })
    return {"gallery": {"images": rows}}


def expected_gallery_rows(reconciliation: dict | None = None) -> list[dict]:
    return tool.expected_gallery(reconciliation or gallery_reconciliation())


def parent_record(**overrides) -> dict:
    expected = expected_gallery_rows()
    value = {
        "id": tool.PARENT_ID,
        "name": tool.PARENT_NAME,
        "sku": tool.PARENT_SKU,
        "type": tool.PARENT_TYPE,
        "status": "draft",
        "images": [
            {"id": row["id"], "src": row["src"], "name": row["name"], "alt": row["alt"]}
            for row in expected
        ],
        "variations": sorted(tool.SUPPORTED_PRICES),
        "price": "23.40",
        "price_html": "derived price",
        "yoast_head": "derived SEO",
        "yoast_head_json": {"derived": True},
        "purchasable": True,
        "on_sale": False,
        "permalink": "https://frpdepots.com/?post_type=product&p=2061",
        "description": "protected description",
        "short_description": "FNPT",
        "categories": [{"id": 15, "name": "Uncategorized"}],
        "attributes": [{"id": 0, "name": "RESIN TYPE", "options": ["D411"]}],
        "meta_data": [{"id": 1, "key": "_private", "value": SECRET}],
        "shipping_class": "",
        "manage_stock": False,
        "stock_quantity": None,
        "stock_status": "instock",
        "date_modified": "2026-08-12T08:20:30",
        "date_modified_gmt": "2026-08-12T16:20:30",
    }
    value.update(overrides)
    return value


def variation_record(variation_id: int, sku: str, **overrides) -> dict:
    expected = expected_gallery_rows()[0]
    supported = variation_id in tool.SUPPORTED_PRICES
    regular_price = tool.SUPPORTED_PRICES[variation_id] if supported else "50.20"
    value = {
        "id": variation_id,
        "parent_id": tool.PARENT_ID,
        "type": "variation",
        "name": f"FNPT child {variation_id}",
        "sku": sku,
        "status": "publish" if supported else "private",
        "regular_price": regular_price,
        "price": regular_price,
        "sale_price": "",
        "purchasable": supported,
        "on_sale": False,
        "permalink": f"https://frpdepots.com/?post_type=product&variation={variation_id}",
        "description": "protected description",
        "attributes": [{"id": 0, "name": "Length", "option": "6\""}],
        "image": {key: expected[key] for key in ("id", "src", "name", "alt")},
        "meta_data": [{"id": variation_id, "key": "_private", "value": SECRET}],
        "shipping_class": "",
        "manage_stock": True,
        "stock_quantity": 0,
        "stock_status": "outofstock",
        "backorders": "no",
        "date_modified": "2026-08-12T08:20:30",
        "date_modified_gmt": "2026-08-12T16:20:30",
    }
    value.update(overrides)
    return value


def catalog() -> tuple[dict, list[dict]]:
    return parent_record(), [variation_record(variation_id, sku)
                             for variation_id, sku in tool.EXPECTED_VARIATIONS]


def source_plan(parent: dict, variations: list[dict]) -> dict:
    targets = []
    for row in variations:
        variation_id = row["id"]
        normalized = copy.deepcopy(row)
        normalized["image"] = None
        targets.append({
            "variation_id": variation_id,
            "sku": row["sku"],
            "supported": variation_id in tool.SUPPORTED_PRICES,
            "payload": ({"regular_price": tool.SUPPORTED_PRICES[variation_id], "status": "publish"}
                        if variation_id in tool.SUPPORTED_PRICES else {"status": "private"}),
            "before_regular_price": row["regular_price"],
            "before_stock": tool.stock_projection(row),
            "before_protected_fingerprint": tool.protected_fingerprint(normalized, parent=False),
        })
    return {
        "parent_before": {
            "before_stock": tool.stock_projection(parent),
            "before_protected_fingerprint": tool.protected_fingerprint(parent, parent=True),
        },
        "variation_targets": targets,
    }


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
    def __init__(self):
        self.parent, rows = catalog()
        self.variations = {row["id"]: row for row in rows}
        self._source_plan = source_plan(self.parent, list(self.variations.values()))
        self.calls: list[tuple[str, str]] = []
        self.writes: list[tuple[str, str, dict]] = []
        self.fail_write = False
        self.corrupt_after_publish = False

    @property
    def source_plan(self) -> dict:
        return copy.deepcopy(self._source_plan)

    def api_get(self, endpoint, params=None, vault=None):
        self.calls.append(("GET", endpoint))
        if endpoint == f"/products/{tool.PARENT_ID}":
            return copy.deepcopy(self.parent), {}
        raise wc.WooError("not found", status=404)

    def get_all(self, endpoint, params=None, *, vault=None, max_pages=200, max_items=20000):
        self.calls.append(("GET", endpoint))
        if endpoint != f"/products/{tool.PARENT_ID}/variations":
            raise wc.WooError("not found", status=404)
        return [copy.deepcopy(self.variations[key]) for key in sorted(self.variations)]

    def api_request(self, method, endpoint, params=None, payload=None, vault=None, timeout=60):
        payload = copy.deepcopy(payload or {})
        self.calls.append((method, endpoint))
        self.writes.append((method, endpoint, payload))
        if self.fail_write:
            raise wc.WooError("sentinel failure", status=503, code="sentinel")
        if method != "PUT" or endpoint != f"/products/{tool.PARENT_ID}" or payload != {"status": "publish"}:
            raise wc.WooError("outside fixed write", status=405)
        self.parent["status"] = "publish"
        self.parent["permalink"] = "https://frpdepots.com/product/fnpt-coupling/"
        self.parent["date_modified"] = "2026-08-12T09:00:00"
        self.parent["date_modified_gmt"] = "2026-08-12T17:00:00"
        for record in self.variations.values():
            record["permalink"] = (
                "https://frpdepots.com/product/fnpt-coupling/"
                f"?attribute_variation={record['id']}"
            )
            record["date_modified"] = "2026-08-12T09:00:00"
            record["date_modified_gmt"] = "2026-08-12T17:00:00"
        if self.corrupt_after_publish:
            self.parent["description"] = "CORRUPTED"
        return copy.deepcopy(self.parent), {}

    @contextlib.contextmanager
    def patched(self):
        downloads = {
            row["src"]: {"bytes": row["bytes"], "content_type": row["content_type"],
                         "sha256": row["sha256"]}
            for row in expected_gallery_rows()
        }
        with (
            mock.patch.object(tool.wc, "api_get", side_effect=self.api_get),
            mock.patch.object(tool.wc, "get_all", side_effect=self.get_all),
            mock.patch.object(tool.wc, "api_request", side_effect=self.api_request),
            mock.patch.object(tool.wc, "load_vault", return_value=vault()),
            mock.patch.object(tool, "download_live_image", side_effect=lambda src: copy.deepcopy(downloads[src])),
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
        reconciliation = gallery_reconciliation()
        with (
            store.patched(),
            mock.patch.object(tool, "validate_fixed_evidence",
                              return_value=(store.source_plan, reconciliation)),
            mock.patch.object(tool, "verify_zoho_physical_stock", return_value=stock_preflight()),
            contextlib.redirect_stdout(output),
        ):
            tool.command_stage(argparse.Namespace())
        result = json.loads(output.getvalue())
        return Path(result["plan"]), result

    def commit(self, store: FakeStore, path: Path, approval: str = "APPROVED") -> dict:
        output = io.StringIO()
        reconciliation = gallery_reconciliation()
        with (
            store.patched(),
            mock.patch.object(tool, "validate_fixed_evidence",
                              return_value=(store.source_plan, reconciliation)),
            mock.patch.object(tool, "verify_zoho_physical_stock", return_value=stock_preflight()),
            contextlib.redirect_stdout(output),
        ):
            tool.command_commit(argparse.Namespace(plan=str(path), approval=approval))
        return json.loads(output.getvalue())


class ContractTests(unittest.TestCase):
    def test_cli_has_only_stage_and_commit(self):
        parser = tool.build_parser()
        self.assertIs(parser.parse_args(["stage"]).func, tool.command_stage)
        self.assertIs(parser.parse_args(["commit", "--plan", "p", "--approval", "APPROVED"]).func,
                      tool.command_commit)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["stage", "--product-id", "999"])

    def test_only_one_put_route_and_status_only_contract(self):
        tool.validate_route("PUT", "/products/2061")
        self.assertEqual(tool.transaction_contract()["only_write"], {
            "method": "PUT", "endpoint": "/products/2061", "payload": {"status": "publish"},
        })
        for pair in (
            ("PUT", "/products/2061/variations/2095"), ("POST", "/products/2061"),
            ("DELETE", "/products/2061"), ("PUT", "/products/999"),
            ("GET", "/orders"), ("PUT", "/products/2061/variations/batch"),
        ):
            with self.subTest(pair=pair), self.assertRaises(tool.GoLiveError):
                tool.validate_route(*pair)

    def test_strict_ids_reject_string_float_bool_fraction_and_zero(self):
        for value in ("2061", 2061.0, 2061.5, True, False, 0, -1, None):
            with self.subTest(value=value), self.assertRaises(tool.GoLiveError):
                tool.strict_positive_id(value, label="test", expected=2061)
        self.assertEqual(tool.strict_positive_id(2061, label="test", expected=2061), 2061)

    def test_catalog_rejects_noncanonical_parent_child_and_parent_id(self):
        parent, rows = catalog()
        mutations = (
            ("parent", "id", 2061.5),
            ("child", "id", 2062.5),
            ("child", "parent_id", "2061"),
            ("child", "type", None),
        )
        for where, field, value in mutations:
            changed_parent, changed_rows = copy.deepcopy(parent), copy.deepcopy(rows)
            if where == "parent":
                changed_parent[field] = value
            else:
                changed_rows[0][field] = value
            with self.subTest(where=where, field=field, value=value), self.assertRaises(tool.GoLiveError):
                tool.validate_catalog_identity(changed_parent, changed_rows)

    def test_gallery_verifier_binds_id_src_name_alt_and_download_hash(self):
        parent = parent_record()
        expected = expected_gallery_rows()
        downloads = {row["src"]: {"bytes": row["bytes"], "content_type": row["content_type"],
                                  "sha256": row["sha256"]} for row in expected}
        with mock.patch.object(tool, "download_live_image", side_effect=lambda src: downloads[src]):
            self.assertEqual(tool.gallery_receipt(parent, expected), expected)
        for field, wrong in (("id", 9999), ("src", expected[1]["src"]),
                             ("name", "wrong.png"), ("alt", "wrong alt")):
            changed = parent_record()
            changed["images"][0][field] = wrong
            with (self.subTest(field=field),
                  mock.patch.object(tool, "download_live_image", side_effect=lambda src: downloads[src]),
                  self.assertRaises(tool.GoLiveError)):
                tool.gallery_receipt(changed, expected)
        wrong_downloads = copy.deepcopy(downloads)
        wrong_downloads[expected[0]["src"]]["sha256"] = "0" * 64
        with (mock.patch.object(tool, "download_live_image", side_effect=lambda src: wrong_downloads[src]),
              self.assertRaises(tool.GoLiveError)):
            tool.gallery_receipt(parent_record(), expected)

    def test_download_url_refuses_off_origin_redirectable_or_non_png_shapes(self):
        for src in (
            "http://frpdepots.com/wp-content/uploads/a.png",
            "https://example.com/wp-content/uploads/a.png",
            "https://user@frpdepots.com/wp-content/uploads/a.png",
            "https://frpdepots.com:444/wp-content/uploads/a.png",
            "https://frpdepots.com/a.png",
            "https://frpdepots.com/wp-content/uploads/a.jpg",
            "https://frpdepots.com/wp-content/uploads/a.png?x=1",
        ):
            with self.subTest(src=src), self.assertRaises(tool.GoLiveError):
                tool.validate_live_image_url(src)

    def test_no_forbidden_mail_browser_or_generic_write_surface(self):
        source = Path(tool.__file__).read_text(encoding="utf-8").casefold()
        for token in ("smtplib", "mail.send", "outlook", "playwright", "selenium",
                      "api_request(\"post\"", "api_request(\"delete\"",
                      "api_request(\"patch\"", "/orders", "/customers", "/batch"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)


class StageTests(PlanFixture):
    def test_stage_is_read_only_and_makes_one_write_24_hour_plan(self):
        store = FakeStore()
        path, result = self.stage(store)
        self.assertEqual(store.writes, [])
        self.assertFalse(result["external_write_performed"])
        self.assertEqual(result["write_count"], 1)
        self.assertEqual(result["only_write"]["payload"], {"status": "publish"})
        self.assertEqual(result["gallery_verified"], 6)
        plan = tool.load_plan(path)
        created = tool.datetime.fromisoformat(plan["created_utc"])
        expires = tool.datetime.fromisoformat(plan["expires_utc"])
        self.assertEqual(expires - created, timedelta(hours=24))

    def test_stage_refuses_parent_variation_gallery_and_visible_list_drift(self):
        for name, mutate in (
            ("parent", lambda s: s.parent.update(description="changed")),
            ("variation", lambda s: s.variations[2062].update(description="changed")),
            ("gallery", lambda s: s.parent["images"][0].update(alt="wrong")),
            ("visible", lambda s: s.parent["variations"].reverse()),
            ("status", lambda s: s.parent.update(status="publish")),
        ):
            store = FakeStore()
            mutate(store)
            with self.subTest(name=name), self.assertRaises(tool.GoLiveError):
                self.stage(store)
            self.assertEqual(store.writes, [])

    def test_plan_hash_and_semantic_tamper_are_refused(self):
        path, _ = self.stage(FakeStore())
        value = json.loads(path.read_text(encoding="utf-8"))
        value["transaction"]["write_count"] = 2
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(tool.GoLiveError):
            tool.load_plan(path)
        path, _ = self.stage(FakeStore())
        value = json.loads(path.read_text(encoding="utf-8"))
        value["transaction"]["write_count"] = 2
        core = {key: item for key, item in value.items() if key != "sha256"}
        value["sha256"] = tool.digest_for(core)
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(tool.GoLiveError):
            tool.load_plan(path)

    def test_plan_does_not_expose_vault_secret_or_protected_text(self):
        path, result = self.stage(FakeStore())
        text = path.read_text(encoding="utf-8") + json.dumps(result)
        self.assertNotIn(vault()["consumer_key"], text)
        self.assertNotIn(vault()["consumer_secret"], text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("protected description", text)


class CommitTests(PlanFixture):
    def test_approval_is_exact_before_vault_network_and_lock(self):
        path, _ = self.stage(FakeStore())
        for approval in ("approved", " APPROVED", "APPROVED ", "APPROVED.", ""):
            with (self.subTest(approval=approval), mock.patch.object(tool.wc, "load_vault") as load,
                  self.assertRaises(tool.GoLiveError)):
                tool.command_commit(argparse.Namespace(plan=str(path), approval=approval))
            load.assert_not_called()
            self.assertFalse(tool.lock_path(path.resolve()).exists())

    def test_commit_makes_exactly_one_publish_write_and_verifies(self):
        store = FakeStore()
        path, _ = self.stage(store)
        result = self.commit(store, path)
        self.assertEqual(store.writes, [("PUT", "/products/2061", {"status": "publish"})])
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["writes_completed"], 1)
        self.assertEqual(result["outcome"]["parent_status"], "publish")
        lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")
        self.assertEqual(lock["writes_completed"], 1)

    def test_lock_exists_before_only_write(self):
        store = FakeStore()
        path, _ = self.stage(store)
        real = store.api_request
        seen = []
        def checked(*args, **kwargs):
            seen.append(tool.lock_path(path.resolve()).exists())
            return real(*args, **kwargs)
        with (
            mock.patch.object(store, "api_request", side_effect=checked),
            store.patched(),
            mock.patch.object(tool, "validate_fixed_evidence",
                              return_value=(store.source_plan, gallery_reconciliation())),
            mock.patch.object(tool, "verify_zoho_physical_stock", return_value=stock_preflight()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertEqual(seen, [True])

    def test_stale_state_is_free_refusal_before_lock(self):
        store = FakeStore()
        path, _ = self.stage(store)
        store.variations[2062]["description"] = "changed"
        with self.assertRaises(tool.GoLiveError):
            self.commit(store, path)
        self.assertEqual(store.writes, [])
        self.assertFalse(tool.lock_path(path.resolve()).exists())

    def test_write_failure_locks_indeterminate_and_never_retries(self):
        store = FakeStore()
        path, _ = self.stage(store)
        store.fail_write = True
        with self.assertRaises(tool.GoLiveError):
            self.commit(store, path)
        self.assertEqual(len(store.writes), 1)
        lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate")
        self.assertEqual(lock["phase"], "parent_status_publish_only_write")
        with self.assertRaises(tool.GoLiveError):
            self.commit(store, path)
        self.assertEqual(len(store.writes), 1)

    def test_postwrite_verification_failure_is_indeterminate_no_retry(self):
        store = FakeStore()
        path, _ = self.stage(store)
        store.corrupt_after_publish = True
        with self.assertRaises(tool.GoLiveError):
            self.commit(store, path)
        self.assertEqual(store.writes, [("PUT", "/products/2061", {"status": "publish"})])
        lock = json.loads(tool.lock_path(path.resolve()).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate")
        self.assertEqual(lock["phase"], "fresh_complete_final_verification")
        self.assertEqual(lock["writes_completed"], 1)

    def test_replay_after_success_refused_before_network(self):
        store = FakeStore()
        path, _ = self.stage(store)
        self.commit(store, path)
        writes = list(store.writes)
        with mock.patch.object(tool.wc, "load_vault") as load, self.assertRaises(tool.GoLiveError):
            tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        load.assert_not_called()
        self.assertEqual(store.writes, writes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
