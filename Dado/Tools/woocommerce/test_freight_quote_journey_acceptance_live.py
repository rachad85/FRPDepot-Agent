#!/usr/bin/env python3
"""Self-tests for the fixed live acceptance harness. No live command is run."""
from __future__ import annotations

import ast
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import freight_quote_journey_acceptance_live as live


class FakeRequest:
    def __init__(self, method: str, url: str, post_data: str = ""):
        self.method = method
        self.url = url
        self.post_data = post_data


class FakeRoute:
    def __init__(self, request: FakeRequest):
        self.request = request
        self.action = ""
        self.reason = ""

    def continue_(self) -> None:
        self.action = "continue"

    def abort(self, reason: str) -> None:
        self.action = "abort"
        self.reason = reason


class FakeAdapter:
    def __init__(self, *, controlled: bool = True, order_after: int = 42):
        self.recorder = live.RequestRecorder(submission_enabled=controlled)
        self.order_counts = iter((42, order_after))
        self.controlled = controlled
        self.submit_calls = 0
        self.events: list[dict[str, str]] = []

    def read_status(self):
        return {
            "spec_sha256": live.SPECIFICATION_SHA256,
            "form_owned": True,
            "form_id": 77,
        }

    def read_order_count(self):
        return next(self.order_counts)

    def product_observation(self, product_id, viewport):
        return {
            "product_id": product_id,
            "viewport": viewport,
            "heading": live.CHOICE_A_HEADING,
            "text": live.CHOICE_A_TEXT,
            "quote_label": live.QUOTE_BUTTON,
            "quote_visible": True,
            "quote_enabled": True,
            "add_to_cart_usable": False,
        }

    def future_allowlisted_observation(self):
        return {"available": True, "product_id": 1455, "quote_visible": False, "add_to_cart_usable": True}

    def product_handoff(self):
        return {"fields": {
            "product": "FRP FW Pipe",
            "product_id": "1455",
            "variation_id": "145501",
            "size": "4 in",
            "pressure_rating": "150 psi",
            "resin_type": "Vinyl ester",
            "quantity": "1",
        }}

    def cart_observation(self):
        return {
            "notice_count": 1,
            "quote_cta_count": 1,
            "shipping_visible": False,
            "checkout_visible": False,
            "payment_visible": False,
            "empty_cart": False,
            "cart_handoff_complete": True,
            "mixed_fixture": True,
            "eligible_rates_before": [{"id": "ups:ground"}],
            "eligible_rates_after": [{"id": "ups:ground"}],
        }

    def quote_form_observation(self):
        return {
            "form_id": 77,
            "form_marker": live.FORM_MARKER,
            "wrapper_count": 1,
            "country_values": ["CA", "US"],
        }

    def contact_observation(self):
        return {"replacement_count": 1}

    def controlled_submit(self):
        self.submit_calls += 1
        self.recorder.submission_count = 2
        self.events = [{
            "event": "generate_lead",
            "lead_type": "freight_quote",
            "form_id": "77",
            "product_id": "1455",
            "variation_id": "145501",
            "source_page": "product",
        }]
        return {
            "confirmation": True,
            "submission_count": 1,
            "post_attempt_count": 2,
            "notification_callback_count": 1,
            "validation_failure_generate_lead_count": 0,
            "validated_countries": ["US", "CA"],
        }

    def analytics_events(self):
        return self.events


class FixedInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(live.__file__).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_fixed_identity_urls_form_marker_and_test_marker(self):
        self.assertEqual("http://127.0.0.1:9229", live.CDP_ENDPOINT)
        self.assertEqual("wordpress", live.LOCK_LANE)
        self.assertEqual("https://frpdepots.com", live.ORIGIN)
        self.assertEqual("FRPDEPOT_FQJ_FIXED_FORM_V2_SPEC_5348EF3F", live.FORM_MARKER)
        self.assertEqual("FRPDEPOT-FQJ-ACCEPTANCE-20260813", live.TEST_MARKER)
        self.assertEqual(5, len(live.PRODUCT_URLS))
        self.assertEqual({1455, 1423, 1368, 1397, 1411}, set(live.PRODUCT_URLS))
        self.assertTrue(all(url.startswith(live.ORIGIN + "/") for url in live.FIXED_URLS))

    def test_plan_is_offline_closed_and_lists_all_15(self):
        plan = live.interface_plan()
        self.assertFalse(plan["live_executed"])
        self.assertFalse(plan["controlled_submission_default"])
        self.assertEqual(live.TEST_MARKER, plan["controlled_submission_enable_value"])
        self.assertEqual(2, plan["maximum_controlled_post_attempts"])
        self.assertEqual(["validation_failure", "success"], plan["controlled_attempt_sequence"])
        self.assertEqual(15, len(plan["tests"]))
        self.assertEqual(live.VIEWPORTS, plan["fresh_anonymous_contexts"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, live.main(["plan"]))
        self.assertEqual(plan, json.loads(output.getvalue()))

    def test_cli_has_no_caller_supplied_url_selector_or_payload(self):
        parser = live.build_parser()
        with tempfile.TemporaryDirectory() as directory:
            args = parser.parse_args(["run", "--output", str(Path(directory) / "evidence.json")])
        self.assertEqual("run", args.command)
        self.assertIsNone(args.enable_controlled_submission)
        with self.assertRaises(SystemExit):
            parser.parse_args(["run", "--url", "https://evil.invalid", "--output", "x.json"])

    def test_wrong_submission_marker_refuses_before_live_adapter(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(live, "live_adapter") as adapter:
            with self.assertRaises(live.HarnessRefusal):
                live.main([
                    "run", "--output", str(Path(directory) / "x.json"),
                    "--enable-controlled-submission", "WRONG",
                ])
            adapter.assert_not_called()

    def test_controlled_run_checks_offline_proof_then_permanent_lock_before_browser(self):
        order: list[str] = []

        def checked_proof():
            order.append("proof")
            return {"status": "PASS"}

        def locked_attempt():
            order.append("lock")

        def refused_browser(**kwargs):
            order.append("browser")
            raise live.HarnessRefusal("fixture stop")

        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(live, "_load_offline_proof", side_effect=checked_proof) as proof, \
                mock.patch.object(live, "_lock_controlled_acceptance", side_effect=locked_attempt) as lock, \
                mock.patch.object(live, "live_adapter", side_effect=refused_browser) as adapter:
            with self.assertRaisesRegex(live.HarnessRefusal, "fixture stop"):
                live.main([
                    "run", "--output", str(Path(directory) / "x.json"),
                    "--enable-controlled-submission", live.TEST_MARKER,
                ])
            proof.assert_called_once_with()
            lock.assert_called_once_with()
            adapter.assert_called_once_with(enable_submission=True)
            self.assertEqual(["proof", "lock", "browser"], order)

    def test_source_uses_lock_before_connect_and_exact_cdp(self):
        function = next(node for node in self.tree.body if isinstance(node, ast.FunctionDef) and node.name == "live_adapter")
        segment = ast.get_source_segment(self.source, function) or ""
        self.assertIn("ui_browser_lock(LOCK_LANE", segment)
        self.assertIn("connect_over_cdp(CDP_ENDPOINT", segment)
        self.assertLess(segment.index("ui_browser_lock(LOCK_LANE"), segment.index("connect_over_cdp(CDP_ENDPOINT"))
        self.assertIn("len(authenticated_contexts) != 1", segment)
        self.assertIn("authenticated_contexts[0].new_page()", segment)
        self.assertNotIn("PlaywrightAdapter(browser, authenticated[0]", segment)

    def test_three_fresh_anonymous_contexts_are_fixed(self):
        self.assertEqual({"desktop", "mobile", "cart"}, set(live.VIEWPORTS))
        init_segment = ast.get_source_segment(
            self.source,
            next(node for node in self.tree.body if isinstance(node, ast.ClassDef) and node.name == "PlaywrightAdapter"),
        ) or ""
        self.assertIn("browser.new_context(viewport=viewport, storage_state=None)", init_segment)
        self.assertIn("context.add_init_script(INIT_ANALYTICS_CAPTURE)", init_segment)


class RequestRecorderTests(unittest.TestCase):
    def test_binary_post_data_fails_closed_without_breaking_the_route(self):
        class BinaryRequest:
            method = "POST"
            url = live.QUOTE_URL

            @property
            def post_data(self):
                raise UnicodeDecodeError("utf-8", b"\x1f\x8b", 1, 2, "invalid start byte")

        recorder = live.RequestRecorder(submission_enabled=True)
        route = FakeRoute(BinaryRequest())
        recorder.submission_window = True
        recorder.submission_phase = "validation_failure"
        recorder.allow_or_abort(route)

        self.assertEqual("abort", route.action)
        self.assertEqual("blockedbyclient", route.reason)
        self.assertEqual(0, recorder.submission_count)
        self.assertEqual(1, len(recorder.non_read_requests))
        self.assertEqual("", recorder.non_read_requests[0]["post_data"])

    def test_read_only_methods_continue(self):
        recorder = live.RequestRecorder()
        for method in ("GET", "HEAD", "OPTIONS"):
            with self.subTest(method=method):
                route = FakeRoute(FakeRequest(method, live.QUOTE_URL))
                recorder.allow_or_abort(route)
                self.assertEqual("continue", route.action)
        self.assertEqual(0, recorder.submission_count)

    def test_non_read_requests_abort_by_default(self):
        recorder = live.RequestRecorder()
        route = FakeRoute(FakeRequest("POST", live.QUOTE_URL, "input_1=fixture"))
        recorder.allow_or_abort(route)
        self.assertEqual("abort", route.action)
        self.assertEqual("blockedbyclient", route.reason)
        self.assertEqual(1, len(recorder.non_read_requests))

    def test_two_attempt_sequence_requires_enable_window_phase_origin_path_and_marker(self):
        recorder = live.RequestRecorder(
            submission_enabled=True, submission_window=True, submission_phase="validation_failure"
        )
        unmarked = FakeRoute(FakeRequest("POST", live.QUOTE_URL, "input_1=fixture"))
        recorder.allow_or_abort(unmarked)
        self.assertEqual("abort", unmarked.action)
        self.assertEqual(0, recorder.submission_count)
        first = FakeRoute(FakeRequest("POST", live.QUOTE_URL, "input_13=" + live.TEST_MARKER))
        recorder.allow_or_abort(first)
        self.assertEqual("continue", first.action)
        self.assertEqual(1, recorder.submission_count)
        recorder.submission_phase = "success"
        second = FakeRoute(FakeRequest("POST", live.QUOTE_URL, "input_13=" + live.TEST_MARKER))
        recorder.allow_or_abort(second)
        self.assertEqual("continue", second.action)
        self.assertEqual(2, recorder.submission_count)
        third = FakeRoute(FakeRequest("POST", live.QUOTE_URL, "input_13=" + live.TEST_MARKER))
        recorder.allow_or_abort(third)
        self.assertEqual("abort", third.action)
        foreign = live.RequestRecorder(submission_enabled=True, submission_window=True)
        route = FakeRoute(FakeRequest("POST", "https://evil.invalid/request-a-quote/"))
        foreign.allow_or_abort(route)
        self.assertEqual("abort", route.action)

    def test_non_read_payment_request_is_recorded_and_never_permitted(self):
        recorder = live.RequestRecorder(submission_enabled=True, submission_window=True)
        route = FakeRoute(FakeRequest("POST", live.ORIGIN + "/wc/store/v1/checkout", "payment_method=fixture"))
        recorder.allow_or_abort(route)
        self.assertEqual("abort", route.action)
        self.assertEqual(1, len(recorder.payment_requests))
        self.assertEqual(0, recorder.submission_count)
        get_route = FakeRoute(FakeRequest("GET", live.ORIGIN + "/payment/fixture"))
        recorder.allow_or_abort(get_route)
        self.assertEqual("continue", get_route.action)
        self.assertEqual(1, len(recorder.payment_requests))

    def test_hidden_native_variation_select_is_changed_with_force(self):
        page = mock.Mock()
        form = mock.Mock()
        page.locator.return_value = form
        form.count.return_value = 1
        page.evaluate.return_value = {"attribute_size": "2"}

        selects = mock.Mock()
        form.locator.return_value = selects
        selects.count.return_value = 1
        select = mock.Mock()
        selects.nth.return_value = select
        select.get_attribute.return_value = "attribute_size"

        options = mock.Mock()
        select.locator.return_value = options
        options.count.return_value = 2
        options.nth.side_effect = [
            mock.Mock(get_attribute=mock.Mock(return_value="")),
            mock.Mock(get_attribute=mock.Mock(return_value="2")),
        ]

        live.PlaywrightAdapter._select_first_resolved_variation(page, True)

        select.select_option.assert_called_once_with("2", force=True)
        page.wait_for_function.assert_called_once()

    def test_analytics_transport_payload_is_recorded(self):
        recorder = live.RequestRecorder()
        request = FakeRequest("POST", "https://www.google-analytics.com/g/collect", "en=generate_lead")
        recorder.observe(request)
        self.assertEqual(1, len(recorder.analytics_transports))
        self.assertEqual("en=generate_lead", recorder.analytics_transports[0]["post_data"])


class EvaluationTests(unittest.TestCase):
    OFFLINE_PROOF = {
        "status": "PASS",
        "specification_sha256": live.SPECIFICATION_SHA256,
        "acceptance_test_count": 15,
        "acceptance_failures": 0,
    }

    def test_fake_harness_executes_all_15_and_passes(self):
        adapter = FakeAdapter(controlled=True)
        report = live.evaluate_adapter(adapter, enable_submission=True, offline_proof=self.OFFLINE_PROOF)
        self.assertEqual({"total": 15, "passed": 15, "failed": 0, "blocked": 0}, report["summary"])
        self.assertEqual(list(range(1, 16)), [test["id"] for test in report["tests"]])
        self.assertEqual(1, adapter.submit_calls)
        self.assertEqual(0, report["payment_request_count"])
        self.assertEqual(42, report["tests"][-1]["evidence"]["order_count_before"])
        self.assertEqual(42, report["tests"][-1]["evidence"]["order_count_after"])
        self.assertEqual(2, report["tests"][-1]["evidence"]["controlled_submission_count"])
        self.assertEqual(1, report["tests"][-1]["evidence"]["controlled_success_count"])

    def test_read_only_run_blocks_exactly_success_dependent_tests(self):
        adapter = FakeAdapter(controlled=False)
        report = live.evaluate_adapter(adapter, enable_submission=False, offline_proof=self.OFFLINE_PROOF)
        statuses = {row["id"]: row["status"] for row in report["tests"]}
        self.assertEqual("BLOCKED", statuses[10])
        self.assertEqual("BLOCKED", statuses[11])
        self.assertEqual("BLOCKED", statuses[12])
        self.assertEqual("BLOCKED", statuses[13])
        self.assertEqual("BLOCKED", statuses[14])
        self.assertEqual(0, adapter.submit_calls)
        self.assertEqual(5, report["summary"]["blocked"])

    def test_order_drift_fails_test_15(self):
        report = live.evaluate_adapter(FakeAdapter(order_after=43), enable_submission=True, offline_proof=self.OFFLINE_PROOF)
        row = report["tests"][14]
        self.assertEqual("FAIL", row["status"])
        self.assertEqual(42, row["evidence"]["order_count_before"])
        self.assertEqual(43, row["evidence"]["order_count_after"])

    def test_payment_request_fails_test_15(self):
        adapter = FakeAdapter()
        adapter.recorder.payment_requests.append({"method": "POST", "url": "fixture", "post_data": ""})
        report = live.evaluate_adapter(adapter, enable_submission=True, offline_proof=self.OFFLINE_PROOF)
        self.assertEqual("FAIL", report["tests"][14]["status"])

    def test_pii_or_extra_analytics_keys_fail_closed(self):
        adapter = FakeAdapter()
        original = adapter.controlled_submit

        def bad_submit():
            result = original()
            adapter.events[0]["email"] = live.CONTROLLED_VALUES["email"]
            return result

        adapter.controlled_submit = bad_submit
        report = live.evaluate_adapter(adapter, enable_submission=True, offline_proof=self.OFFLINE_PROOF)
        self.assertEqual("FAIL", report["tests"][10]["status"])
        self.assertEqual("FAIL", report["tests"][12]["status"])

    def test_missing_current_offline_proof_blocks_nonexistent_live_fixture_checks(self):
        report = live.evaluate_adapter(FakeAdapter(), enable_submission=True)
        statuses = {row["id"]: row["status"] for row in report["tests"]}
        for test_id in (5, 6, 7, 8, 9):
            self.assertEqual("BLOCKED", statuses[test_id])


if __name__ == "__main__":
    unittest.main(verbosity=2)
