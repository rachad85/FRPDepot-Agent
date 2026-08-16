from __future__ import annotations

import ast
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

import catalogue_presentation_release_qa_tool as tool


class UrlContractTests(unittest.TestCase):
    def test_all_fixed_public_routes_are_allowed(self) -> None:
        for _, url in tool.PUBLIC_PAGES:
            tool.assert_frp_url(url)
        for url in (
            tool.FNPT_STORE_API_URL,
            tool.DERAKANE_REST_URL + "?chemical=acid",
            tool.HETRON_ATTACHMENT_URL,
            tool.HETRON_ATTACHMENT_QUERY_URL,
            tool.HETRON_DIRECT_PDF_URL,
        ):
            tool.assert_frp_url(url)

    def test_external_and_unlisted_routes_are_refused(self) -> None:
        for url in (
            "https://example.com/",
            "http://frpdepots.com/",
            "https://frpdepots.com/wp-admin/users.php",
            "https://frpdepots.com/cart/",
            "https://user:pass@frpdepots.com/products/",
        ):
            with self.subTest(url=url):
                with self.assertRaises(tool.QaError):
                    tool.assert_frp_url(url)

    def test_admin_reader_is_only_plugins_page(self) -> None:
        tool.assert_frp_url(tool.PLUGINS_URL, admin=True)
        with self.assertRaises(tool.QaError):
            tool.assert_frp_url("https://frpdepots.com/wp-admin/plugin-install.php", admin=True)

    def test_cache_buster_preserves_existing_query(self) -> None:
        url = tool.cache_busted(tool.HETRON_ATTACHMENT_QUERY_URL, "abc")
        self.assertIn("attachment_id=1832&_frp_release_qa=abc", url)


class DisclosureScannerTests(unittest.TestCase):
    def test_detects_php_warning_and_undefined_property(self) -> None:
        scan = tool.scan_runtime_disclosures(
            "PHP Warning: Undefined property: stdClass::$post_parent"
        )
        self.assertTrue(scan["runtime_error_markers"])

    def test_detects_unix_and_windows_server_paths(self) -> None:
        text = (
            r"/home/site/public_html/wp-content/plugins/a.php on line 12 "
            r"C:\Sites\public_html\wp-includes\b.php on line 8"
        )
        scan = tool.scan_runtime_disclosures(text)
        self.assertGreaterEqual(len(scan["server_paths"]), 2)

    def test_normal_public_asset_url_is_not_a_server_path(self) -> None:
        scan = tool.scan_runtime_disclosures(
            "https://frpdepots.com/wp-content/plugins/example/assets/main.js"
        )
        self.assertEqual(scan["server_paths"], [])


class IssueContractTests(unittest.TestCase):
    @staticmethod
    def clean_row() -> dict:
        return {
            "viewport": "desktop",
            "label": "home",
            "expected_path": "/",
            "final_url": "https://frpdepots.com/?x=1",
            "navigation_error": None,
            "http_status": 200,
            "metrics": {
                "body_text_chars": 500,
                "horizontal_overflow_px": 0,
                "broken_images": [],
                "empty_headings": [],
                "placeholder_links": [],
            },
            "fatal_markers": [],
            "runtime_error_markers": [],
            "server_paths": [],
            "console_errors": [],
            "failed_same_origin_responses": [],
            "failed_same_origin_requests": [],
            "screenshots": [{"kind": "full_page", "path": "x", "sha256": "0"}],
            "mobile_menu": {"opened": False},
        }

    def test_console_and_network_errors_are_high(self) -> None:
        row = self.clean_row()
        row["console_errors"] = ["boom"]
        row["failed_same_origin_responses"] = [{"status": 500}]
        findings, _ = tool.page_issues(row)
        self.assertEqual(sum(item["severity"] == "high" for item in findings), 2)

    def test_medium_overflow_and_broken_image_failures_are_not_ignored(self) -> None:
        row = self.clean_row()
        row["metrics"]["horizontal_overflow_px"] = 3
        row["metrics"]["broken_images"] = [{"src": "x"}]
        findings, _ = tool.page_issues(row)
        self.assertEqual(sum(item["severity"] == "medium" for item in findings), 2)

    def test_placeholder_links_and_empty_headings_are_recorded_low(self) -> None:
        row = self.clean_row()
        row["metrics"]["placeholder_links"] = [{"href": "#"}]
        row["metrics"]["empty_headings"] = ["<h2></h2>"]
        findings, observations = tool.page_issues(row)
        self.assertEqual(findings, [])
        self.assertEqual(len(observations), 2)

    def test_mobile_menu_must_open(self) -> None:
        row = self.clean_row()
        row["viewport"] = "mobile"
        findings, _ = tool.page_issues(row)
        self.assertTrue(any("mobile menu" in item["issue"] for item in findings))

    def test_report_summary_fails_on_any_medium(self) -> None:
        row = self.clean_row()
        row["metrics"]["horizontal_overflow_px"] = 3
        row["screenshots"] = [
            {"kind": "a", "path": "1", "sha256": "1"},
            {"kind": "b", "path": "2", "sha256": "2"},
            {"kind": "c", "path": "3", "sha256": "3"},
        ]
        sheets = {name: {} for name in (
            "desktop_first_view", "mobile_first_view", "mobile_menus", "full_page_overview"
        )}
        with mock.patch.object(tool, "PUBLIC_PAGES", [("home", tool.EXACT_ORIGIN + "/")]):
            summary = tool.report_summary(
                [row], {"passed": True}, {"passed": True}, sheets
            )
        self.assertFalse(summary["automated_passed"])
        self.assertEqual(summary["medium"], 1)


class ProjectionContractTests(unittest.TestCase):
    def test_exact_reference_projection_passes(self) -> None:
        categories = sorted(tool.EXPECTED_CATEGORY_IDS)
        products = sorted(tool.EXPECTED_PRODUCT_IDS)
        grouping = [[categories[0], products]] + [[item, [9000 + item]] for item in categories[1:]]
        all_products = [product for _, group in grouping for product in group]
        projection = {
            "present": True,
            "shop_roots": [{"title": tool.SHOP_TITLE, "path": tool.SHOP_PATH}],
            "categories": categories,
            "products": all_products,
            "grouping": grouping,
            "order_fingerprint": tool.digest_for({
                "categories": categories, "products": all_products, "grouping": grouping
            }),
            "deduplicated": True,
            "fully_grouped": True,
            "nonempty_groups": True,
        }
        self.assertTrue(tool.projection_matches(projection, projection))

    def test_duplicate_projection_fails(self) -> None:
        projection = {
            "present": True,
            "categories": list(tool.EXPECTED_CATEGORY_IDS),
            "products": list(tool.EXPECTED_PRODUCT_IDS),
            "grouping": [],
            "order_fingerprint": "x",
            "deduplicated": False,
            "fully_grouped": True,
            "nonempty_groups": True,
        }
        self.assertFalse(tool.projection_matches(projection, projection))


class ContactSheetTests(unittest.TestCase):
    def test_contact_sheet_hashes_saved_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            one = root / "one.png"
            two = root / "two.png"
            Image.new("RGB", (200, 500), "red").save(one)
            Image.new("RGB", (200, 500), "blue").save(two)
            output = root / "sheet.png"
            result = tool.make_contact_sheet([(one, "one"), (two, "two")], output)
            self.assertTrue(output.is_file())
            self.assertEqual(result["sha256"], tool.sha256_file(output))


class NavigationHeaderTests(unittest.TestCase):
    class Request:
        def __init__(self, url: str, resource_type: str) -> None:
            self.url = url
            self.resource_type = resource_type
            self.headers = {"Accept": "text/html"}

    class Route:
        def __init__(self, request: "NavigationHeaderTests.Request") -> None:
            self.request = request
            self.calls = []

        def continue_(self, **kwargs) -> None:
            self.calls.append(kwargs)

    class Page:
        def route(self, pattern, handler) -> None:
            self.pattern = pattern
            self.handler = handler

    def test_no_cache_is_limited_to_same_origin_documents(self) -> None:
        page = self.Page()
        tool.install_document_no_cache(page)
        document = self.Route(self.Request(tool.EXACT_ORIGIN + "/products/", "document"))
        page.handler(document)
        self.assertEqual(document.calls[0]["headers"]["Cache-Control"], "no-cache")

        external_font = self.Route(self.Request("https://fonts.gstatic.com/font.woff2", "font"))
        page.handler(external_font)
        self.assertEqual(external_font.calls, [{}])


class PixelReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run_root = self.root / "runs"
        self.run_dir = self.run_root / "run1"
        self.run_dir.mkdir(parents=True)
        self.shot = self.run_dir / "shot.png"
        Image.new("RGB", (50, 50), "white").save(self.shot)
        self.shot_hash = tool.sha256_file(self.shot)
        core = {
            "schema_version": tool.SCHEMA_VERSION,
            "tool": tool.TOOL_NAME,
            "tool_version": tool.TOOL_VERSION,
            "run_id": "run1",
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "finished_utc": datetime.now(timezone.utc).isoformat(),
            "origin": tool.EXACT_ORIGIN,
            "read_only": True,
            "plugin_state": {"passed": True},
            "pages": [{"screenshots": [{"path": str(self.shot), "sha256": self.shot_hash}]}],
            "functional": {"passed": True},
            "contact_sheets": {},
            "summary": {"automated_passed": True, "expected_screenshots": 1},
            "pixel_review_required": True,
        }
        self.report = {**core, "automated_report_sha256": tool.digest_for(core)}
        self.report_path = self.run_dir / tool.REPORT_NAME
        self.report_path.write_text(json.dumps(self.report), encoding="utf-8")
        self.review = {
            "schema_version": tool.SCHEMA_VERSION,
            "automated_report_sha256": self.report["automated_report_sha256"],
            "reviewed_utc": datetime.now(timezone.utc).isoformat(),
            "reviewer": "Dado pixel review",
            "screenshots": [{
                "path": str(self.shot),
                "sha256": self.shot_hash,
                "result": "pass",
                "notes": "No warning, clipping, overlap or broken primary layout visible.",
            }],
            "overall": "pass",
            "notes": "All required pixels were inspected.",
        }
        self.patches = [
            mock.patch.object(tool, "RUN_ROOT", self.run_root),
            mock.patch.object(tool, "RECEIPTS", self.root / "receipts.jsonl"),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def test_valid_review_covers_every_screenshot(self) -> None:
        result = tool.validate_pixel_review(self.report, self.review)
        self.assertEqual(result["overall"], "pass")
        self.assertEqual(result["screenshots_reviewed"], 1)

    def test_missing_screenshot_refuses(self) -> None:
        review = dict(self.review)
        review["screenshots"] = []
        with self.assertRaises(tool.QaError):
            tool.validate_pixel_review(self.report, review)

    def test_changed_screenshot_refuses(self) -> None:
        Image.new("RGB", (50, 50), "black").save(self.shot)
        with self.assertRaises(tool.QaError):
            tool.validate_pixel_review(self.report, self.review)

    def test_overall_cannot_contradict_failed_row(self) -> None:
        review = json.loads(json.dumps(self.review))
        review["screenshots"][0]["result"] = "fail"
        with self.assertRaises(tool.QaError):
            tool.validate_pixel_review(self.report, review)

    def test_finalize_is_immutable(self) -> None:
        review_path = self.run_dir / tool.REVIEW_NAME
        review_path.write_text(json.dumps(self.review), encoding="utf-8")
        result_path, result = tool.finalize(self.report_path, review_path)
        self.assertEqual(result["status"], "PASSED")
        self.assertTrue(result_path.is_file())
        with self.assertRaises(tool.QaError):
            tool.finalize(self.report_path, review_path)

    def test_report_path_cannot_escape_run_root(self) -> None:
        with self.assertRaises(tool.QaError):
            tool.resolve_report_path(str(self.root / tool.REPORT_NAME))


class SourceSafetyTests(unittest.TestCase):
    def test_module_exposes_no_business_write_primitive(self) -> None:
        source = Path(tool.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        banned_attributes = {
            "post", "put", "patch", "delete", "fill", "type", "press",
            "set_input_files", "select_option", "check", "uncheck",
        }
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        offenders = [
            node.func.attr
            for node in calls
            if isinstance(node.func, ast.Attribute) and node.func.attr in banned_attributes
        ]
        self.assertEqual(offenders, [])
        click_calls = [
            node for node in calls
            if isinstance(node.func, ast.Attribute) and node.func.attr == "click"
        ]
        self.assertEqual(len(click_calls), 1, "Only the fixed public mobile-menu toggle may click")
        self.assertIn("toggle.click", source)

    def test_cli_has_only_audit_and_finalize(self) -> None:
        parser = tool.build_parser()
        subparsers = [
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        self.assertEqual(len(subparsers), 1)
        self.assertEqual(set(subparsers[0].choices), {"audit", "finalize"})


if __name__ == "__main__":
    unittest.main()
