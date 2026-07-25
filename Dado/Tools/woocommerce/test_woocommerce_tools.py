import argparse
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from unittest.mock import patch

import woocommerce_common as wc
import woocommerce_change_tool as change
import woocommerce_audit_tool as audit


class CommonSecurityTests(unittest.TestCase):
    def test_exact_site_origin(self):
        self.assertEqual(wc.normalize_site_url("https://frpdepots.com/"), wc.ALLOWED_ORIGIN)
        self.assertEqual(wc.normalize_site_url("https://frpdepots.com:443"), wc.ALLOWED_ORIGIN)
        for bad in (
            "http://frpdepots.com", "https://www.frpdepots.com", "https://evilfrpdepots.com",
            "https://shop.frpdepots.com", "https://user@frpdepots.com",
            "https://frpdepots.com:444", "https://frpdepots.com/wp-admin",
        ):
            with self.subTest(url=bad), self.assertRaises(wc.WooError):
                wc.normalize_site_url(bad)

    def test_secret_scrub(self):
        clean = wc.scrub("ck_abcdefghijklmnopqrstuvwxyz cs_abcdefghijklmnopqrstuvwxyz")
        self.assertNotIn("ck_", clean)
        self.assertNotIn("cs_", clean)
        self.assertEqual(clean.count("[REDACTED]"), 2)

    def test_transport_refuses_forbidden_methods(self):
        vault = self._vault()
        for method in ("DELETE", "PATCH", "HEAD"):
            with self.subTest(method=method), self.assertRaises(wc.WooError):
                wc.api_request(method, "/products/1", vault=vault)

    def test_transport_refuses_write_query(self):
        with self.assertRaises(wc.WooError):
            wc.api_request("PUT", "/products/1", params={"force": "true"},
                           payload={"name": "x"}, vault=self._vault())

    def test_endpoint_refuses_full_url_and_traversal(self):
        for bad in ("https://example.com/products", "/products/../orders", "products/1"):
            with self.subTest(endpoint=bad), self.assertRaises(wc.WooError):
                wc._safe_endpoint(bad)

    def test_redirect_handler_never_builds_followup(self):
        handler = wc.NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {},
                                                   "https://evil.example/"))

    @staticmethod
    def _vault():
        return {
            "site_url": wc.ALLOWED_ORIGIN,
            "consumer_key": "ck_abcdefghijklmnopqrstuvwxyz",
            "consumer_secret": "cs_abcdefghijklmnopqrstuvwxyz",
            "declared_permissions": "read_write",
        }


class ChangeValidationTests(unittest.TestCase):
    def test_only_four_write_actions(self):
        self.assertEqual(change.ACTIONS, {
            "product_create", "product_update", "variation_create", "variation_update"
        })

    def test_only_four_method_path_pairs(self):
        cases = [
            ("product_create", None, None, "POST", "/products"),
            ("product_update", 7, None, "PUT", "/products/7"),
            ("variation_create", None, 7, "POST", "/products/7/variations"),
            ("variation_update", 9, 7, "PUT", "/products/7/variations/9"),
        ]
        for action, rid, parent, method, endpoint in cases:
            self.assertEqual(change.endpoint_for(action, rid, parent), (method, endpoint))
            change.validate_write_route(method, endpoint)
        for method, endpoint in (
            ("POST", "/products/batch"), ("DELETE", "/products/1"),
            ("PUT", "/orders/1"), ("PUT", "/customers/1"),
            ("PUT", "/products/1?force=true"), ("PUT", "/products/1/extra"),
        ):
            with self.subTest(method=method, endpoint=endpoint), self.assertRaises(change.ChangeError):
                change.validate_write_route(method, endpoint)

    def test_forbidden_fields_rejected(self):
        forbidden = (
            "status", "catalog_visibility", "sale_price", "stock_quantity", "backorders",
            "meta_data", "images", "downloads", "external_url", "tax_status",
            "shipping_class", "customer_id", "order_id",
        )
        for field in forbidden:
            with self.subTest(field=field), self.assertRaises(change.ChangeError):
                change.clean_changes("product_update", {field: "x"})

    def test_product_create_forces_draft(self):
        changes = change.clean_changes("product_create", {
            "name": "Pipe", "type": "simple", "sku": "PIPE-1", "regular_price": "10.00"
        })
        self.assertNotIn("status", changes)
        self.assertEqual(change.payload_for("product_create", changes)["status"], "draft")

    def test_variation_create_forces_draft(self):
        changes = change.clean_changes("variation_create", {
            "sku": "PIPE-1-A", "regular_price": "11.00",
            "attributes": [{"id": 3, "option": "2 in"}],
        })
        self.assertEqual(change.payload_for("variation_create", changes)["status"], "draft")

    def test_unsafe_html_rejected(self):
        for value in ("<script>alert(1)</script>", '<p onclick="x()">bad</p>',
                      '<a href="javascript:bad()">bad</a>'):
            with self.subTest(value=value), self.assertRaises(change.ChangeError):
                change.clean_changes("product_update", {"description": value})

    def test_nested_schemas_are_closed(self):
        self.assertEqual(change.clean_categories([{"id": 7}]), [{"id": 7}])
        with self.assertRaises(change.ChangeError):
            change.clean_categories([{"id": 7, "name": "Injected"}])
        valid = [{"id": 2, "position": 0, "visible": True,
                  "variation": True, "options": ["A", "B"]}]
        self.assertEqual(change.clean_product_attributes(valid), valid)
        with self.assertRaises(change.ChangeError):
            change.clean_product_attributes([{"id": 2, "options": ["A"]}])

    def test_sources_must_match_exactly(self):
        changes = {"name": "A"}
        with self.assertRaises(change.ChangeError):
            change.verify_sources(changes, {}, "product_update")
        with self.assertRaises(change.ChangeError):
            change.verify_sources(changes, {"name": "Rachad", "sku": "extra"}, "product_update")

    def test_expanded_response_projection(self):
        response = {"categories": [{"id": 7, "name": "Pipe", "slug": "pipe"}]}
        template = {"categories": [{"id": 7}]}
        self.assertEqual(change.selected_state(response, template), template)


class StageCommitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.plan_dir = Path(self.tmp.name) / "plans"
        self.receipt_patch = mock.patch.object(change.wc, "append_receipt", lambda *args: None)
        self.dir_patch = mock.patch.object(change, "PLAN_DIR", self.plan_dir)
        self.receipt_patch.start()
        self.dir_patch.start()
        self.old = {
            "id": 7, "name": "Old name", "sku": "OLD-7", "description": "",
            "short_description": "", "regular_price": "10.00", "type": "simple",
            "categories": [], "attributes": [], "date_modified_gmt": "2026-07-24T12:00:00",
        }
        self.new = {**self.old, "name": "New name", "date_modified_gmt": "2026-07-24T12:01:00"}

    def tearDown(self):
        self.dir_patch.stop()
        self.receipt_patch.stop()
        self.tmp.cleanup()

    def _input(self, data):
        path = Path(self.tmp.name) / "input.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _stage_update(self):
        path = self._input({
            "action": "product_update", "resource_id": 7,
            "changes": {"name": "New name"},
            "sources": {"name": "Rachad's written instruction"},
        })
        with mock.patch.object(change.wc, "api_get", return_value=(self.old, {})):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                change.command_stage(argparse.Namespace(input=str(path)))
        result = json.loads(output.getvalue())
        return Path(result["plan"]), result

    def test_stage_creates_full_hash_expiring_plan(self):
        plan_path, result = self._stage_update()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        saved = plan.pop("sha256")
        self.assertEqual(saved, change.digest_for(plan))
        self.assertEqual(len(saved), 64)
        self.assertTrue(result["approval"].endswith(saved))
        self.assertEqual(result["status"], "STAGED_NOT_COMMITTED")
        self.assertEqual(plan["origin"], change.EXACT_ORIGIN)
        self.assertTrue(plan["nonce"])

    def test_tampered_plan_rejected(self):
        plan_path, _ = self._stage_update()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["payload"]["name"] = "Tampered"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(change.ChangeError):
            change.load_plan(str(plan_path))

    def test_expired_plan_rejected_even_with_valid_hash(self):
        plan_path, _ = self._stage_update()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan.pop("sha256")
        plan["expires_utc"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        plan["sha256"] = change.digest_for(plan)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(change.ChangeError):
            change.load_plan(str(plan_path))

    def test_wrong_approval_rejected_before_service_access(self):
        plan_path, _ = self._stage_update()
        with mock.patch.object(change.wc, "load_vault") as load:
            with self.assertRaises(change.ChangeError):
                change.command_commit(argparse.Namespace(plan=str(plan_path), approval="yes"))
            load.assert_not_called()

    def test_stale_record_rejected_without_write(self):
        plan_path, result = self._stage_update()
        stale = {**self.old, "description": "Changed elsewhere",
                 "date_modified_gmt": "2026-07-24T12:02:00"}
        with mock.patch.object(change.wc, "load_vault", return_value=self._vault()), \
             mock.patch.object(change.wc, "api_get", return_value=(stale, {})), \
             mock.patch.object(change.wc, "api_request") as write:
            with self.assertRaises(change.ChangeError):
                change.command_commit(argparse.Namespace(plan=str(plan_path), approval=result["approval"]))
            write.assert_not_called()

    def test_exact_approval_commits_once_reads_back_and_locks(self):
        plan_path, result = self._stage_update()
        with mock.patch.object(change.wc, "load_vault", return_value=self._vault()), \
             mock.patch.object(change.wc, "api_get", side_effect=[(self.old, {}), (self.new, {})]), \
             mock.patch.object(change.wc, "api_request", return_value=(self.new, {})) as write:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                change.command_commit(argparse.Namespace(plan=str(plan_path), approval=result["approval"]))
            write.assert_called_once_with("PUT", "/products/7", payload={"name": "New name"},
                                          vault=self._vault())
            response = json.loads(output.getvalue())
            self.assertEqual(response["status"], "COMMITTED_AND_VERIFIED")
            self.assertTrue(response["replay_locked"])
            self.assertTrue(change.lock_path(plan_path.resolve()).exists())

        with mock.patch.object(change.wc, "api_request") as second_write:
            with self.assertRaises(change.ChangeError):
                change.command_commit(argparse.Namespace(plan=str(plan_path), approval=result["approval"]))
            second_write.assert_not_called()

    def test_create_stage_payload_is_draft(self):
        path = self._input({
            "action": "product_create",
            "changes": {"name": "Pipe", "type": "simple", "sku": "P-1", "regular_price": "10.00"},
            "sources": {"name": "Rachad", "type": "Rachad", "sku": "Price list", "regular_price": "Price list"},
        })
        with mock.patch.object(change.wc, "api_get", return_value=([], {})):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                change.command_stage(argparse.Namespace(input=str(path)))
        result = json.loads(output.getvalue())
        self.assertEqual(result["payload"]["status"], "draft")

    @staticmethod
    def _vault():
        return {
            "site_url": wc.ALLOWED_ORIGIN,
            "consumer_key": "ck_abcdefghijklmnopqrstuvwxyz",
            "consumer_secret": "cs_abcdefghijklmnopqrstuvwxyz",
            "declared_permissions": "read_write",
        }


class AuditPrivacyTests(unittest.TestCase):
    def test_customer_projection_contains_no_identity_fields(self):
        low = audit.CUSTOMER_AUDIT_FIELDS.casefold()
        for forbidden in ("id", "email", "name", "username", "billing", "shipping",
                          "phone", "address", "meta_data", "avatar"):
            self.assertNotIn(forbidden, low)

    def test_order_projection_contains_no_identity_or_secret_fields(self):
        low = audit.ORDER_AUDIT_FIELDS.casefold()
        for forbidden in ("order_key", "ip_address", "user_agent", "customer_note",
                          "billing", "shipping", "email", "phone", "transaction_id",
                          "cart_hash", "coupon", "meta_data"):
            self.assertNotIn(forbidden, low)

    def test_safe_system_status_drops_paths_and_server_fingerprint(self):
        result = audit.safe_system_status({
            "environment": {"wp_version": "6", "server_info": "secret", "home_url": "x"},
            "database": {"database_prefix": "wp_", "maxmind_geoip_database": "C:/secret"},
            "active_plugins": [], "theme": {},
        })
        serialized = json.dumps(result).casefold()
        for forbidden in ("server_info", "home_url", "database_prefix", "maxmind"):
            self.assertNotIn(forbidden, serialized)

    def test_missing_metadata_and_schema_are_flagged(self):
        pages = [{
            "url": "https://frpdepots.com/product/example/", "status": 200, "seconds": 0.2,
            "title": "", "meta_description": "", "h1_count": 0, "canonical": "",
            "mixed_content_links": 0, "schema_types": [],
        }]
        issues = {row["issue"] for row in audit.derive_public_findings(pages, [{"status": 200}])}
        self.assertIn("Missing page title", issues)
        self.assertIn("Missing meta description", issues)
        self.assertIn("Product page lacks detectable Product schema", issues)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class OptionalEndpointTests(unittest.TestCase):
    """B-28 and B-29: a partial audit must never read as a complete one.

    The audit enumerates then fetches in several places (settings groups,
    shipping-zone locations and methods, product-attribute terms). Anything
    listed can be gone, or advertised by a plugin that cannot serve it, by the
    time it is fetched. That 404 used to propagate out of command_store and kill
    the whole run AFTER every catalog, order and customer page had been fetched.
    The one place it was handled matched on exception TEXT, and the resulting
    warning reached neither the markdown, the summary, stdout, nor the receipt.
    """

    def test_woo_error_carries_status_and_code_separately(self):
        exc = wc.WooError("boom", status=404, code="rest_setting_setting_group_invalid")
        self.assertEqual(exc.status, 404)
        self.assertEqual(exc.code, "rest_setting_setting_group_invalid")

    def test_woo_error_defaults_are_none_not_crashes(self):
        exc = wc.WooError("plain message")
        self.assertIsNone(exc.status)
        self.assertIsNone(exc.code)

    def test_a_404_returns_none_and_is_recorded(self):
        skipped = []
        err = wc.WooError("gone", status=404, code="rest_no_route")
        with patch.object(wc, "api_get", side_effect=err):
            result = wc.api_get_optional("/settings/advanced", None, {}, skipped)
        self.assertIsNone(result)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["endpoint"], "/settings/advanced")
        self.assertEqual(skipped[0]["code"], "rest_no_route")

    def test_a_real_failure_still_raises(self):
        """Auth and 5xx mean the numbers are WRONG, not merely incomplete."""
        for status in (401, 403, 500, 502, None):
            with self.subTest(status=status):
                skipped = []
                err = wc.WooError("nope", status=status)
                with patch.object(wc, "api_get", side_effect=err):
                    with self.assertRaises(wc.WooError):
                        wc.api_get_optional("/settings/x", None, {}, skipped)
                self.assertEqual(skipped, [])

    def test_a_successful_call_is_passed_straight_through(self):
        skipped = []
        with patch.object(wc, "api_get", return_value=([{"id": "woocommerce_x"}], {})):
            result = wc.api_get_optional("/settings/general", None, {}, skipped)
        self.assertEqual(result, [{"id": "woocommerce_x"}])
        self.assertEqual(skipped, [])
