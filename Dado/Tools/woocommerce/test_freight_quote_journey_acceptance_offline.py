#!/usr/bin/env python3
"""Executable offline/fake acceptance suite: exactly the 15 commissioned tests."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import freight_quote_journey_acceptance_offline as model


class FreightQuoteJourneyOfflineAcceptance(unittest.TestCase):
    def setUp(self) -> None:
        self.authority = model.DecisionAuthority(model.fixed_allowlist())
        self.commerce = model.CommerceCallbacks()
        self.analytics = model.AnalyticsLatch()
        self.form = model.QuoteForm(self.analytics, self.commerce)

    def assert_choice_a(self, ui: model.ProductUi, viewport: str) -> None:
        self.assertEqual(viewport, ui.viewport)
        self.assertTrue(ui.resolved)
        self.assertTrue(ui.quote_required)
        self.assertEqual(model.CHOICE_A_HEADING, ui.heading)
        self.assertEqual(model.CHOICE_A_TEXT, ui.text)
        self.assertEqual(model.QUOTE_BUTTON, ui.quote_label)
        self.assertTrue(ui.quote_visible)
        self.assertTrue(ui.quote_enabled)
        self.assertEqual("/request-a-quote/", ui.quote_href)
        self.assertFalse(ui.add_to_cart_visible)
        self.assertFalse(ui.add_to_cart_enabled)

    def test_01_freight_pipe_desktop(self) -> None:
        unresolved = model.render_product_ui("desktop", None, self.authority)
        self.assertFalse(unresolved.quote_enabled)
        self.assertFalse(unresolved.add_to_cart_enabled)
        self.assert_choice_a(model.render_product_ui("desktop", model.target_line(1455), self.authority), "desktop")

    def test_02_freight_pipe_mobile(self) -> None:
        ui = model.render_product_ui("mobile", model.target_line(1455), self.authority)
        self.assert_choice_a(ui, "mobile")
        self.assertEqual({"width": 390, "height": 844}, model.VIEWPORTS["mobile"])

    def test_03_unverified_elbow_and_stub_desktop_mobile(self) -> None:
        for product_id in (1423, 1368):
            for viewport in model.VIEWPORTS:
                with self.subTest(product_id=product_id, viewport=viewport):
                    self.assert_choice_a(model.render_product_ui(viewport, model.target_line(product_id), self.authority), viewport)

    def test_04_manway_and_cover_desktop_mobile(self) -> None:
        for product_id in (1397, 1411):
            for viewport in model.VIEWPORTS:
                with self.subTest(product_id=product_id, viewport=viewport):
                    self.assert_choice_a(model.render_product_ui(viewport, model.target_line(product_id), self.authority), viewport)

    def test_05_future_allowlisted_variation_retains_add_to_cart(self) -> None:
        for viewport in model.VIEWPORTS:
            with self.subTest(viewport=viewport):
                ui = model.render_product_ui(viewport, model.future_allowlisted_line(), self.authority)
                self.assertFalse(ui.quote_required)
                self.assertFalse(ui.quote_visible)
                self.assertFalse(ui.quote_enabled)
                self.assertTrue(ui.add_to_cart_visible)
                self.assertTrue(ui.add_to_cart_enabled)
        duplicate = model.fixed_allowlist()
        duplicate["items"].append(dict(duplicate["items"][0]))
        with self.assertRaises(model.ModelRefusal):
            model.DecisionAuthority(duplicate)

    def test_06_product_and_cart_handoff_is_complete_and_authoritative(self) -> None:
        injected = {
            "product": "ATTACKER PRODUCT",
            "size": "ATTACKER SIZE",
            "product_url": "https://evil.invalid/",
        }
        handoff = model.product_handoff(1455, model.TARGET_PRODUCTS[1455]["variation_id"], 3, injected)
        self.assertEqual("FRP FW Pipe", handoff["product"])
        self.assertEqual("4 in", handoff["size"])
        self.assertEqual("https://frpdepots.com/product/frp-fw-pipe/", handoff["product_url"])
        self.assertNotIn("ATTACKER", json.dumps(handoff))
        lines = [model.future_allowlisted_line(), model.target_line(1423)]
        cart = model.cart_handoff(lines)
        projection = json.loads(cart["cart_projection"])
        self.assertEqual(2, len(projection))
        self.assertEqual([model.FUTURE_PRODUCT_ID, 1423], [line["product_id"] for line in projection])
        self.assertEqual(
            {"attributes", "product_id", "quantity", "variation_id"},
            set(projection[0]),
        )
        self.assertNotIn("sku", cart["cart_projection"].casefold())
        self.assertNotIn("price", cart["cart_projection"].casefold())
        with self.assertRaises(model.ModelRefusal):
            model.cart_handoff(lines * 26)

    def test_07_freight_cart_hides_shipping_checkout_and_payment(self) -> None:
        cart = model.render_cart_ui([model.target_line(1455)], ["UPS Ground"], self.authority)
        self.assertTrue(cart.quote_required)
        self.assertEqual(1, cart.notice_count)
        self.assertEqual(1, cart.quote_cta_count)
        self.assertFalse(cart.shipping_calculator_visible)
        self.assertFalse(cart.shipping_rates_visible)
        self.assertFalse(cart.checkout_visible)
        self.assertFalse(cart.payment_visible)
        self.assertEqual([], cart.rates)
        self.assertEqual(0, self.commerce.total)

    def test_08_mixed_cart_blocks_until_freight_line_removed(self) -> None:
        eligible = model.future_allowlisted_line()
        freight = model.target_line(1397)
        mixed = model.render_cart_ui([eligible, freight], ["UPS Ground"], self.authority)
        remainder = model.render_cart_ui([eligible], ["UPS Ground"], self.authority)
        self.assertTrue(mixed.quote_required)
        self.assertFalse(mixed.checkout_visible)
        self.assertFalse(remainder.quote_required)
        self.assertTrue(remainder.checkout_visible)
        self.assertTrue(remainder.payment_visible)

    def test_09_no_invented_rate_and_eligible_rates_are_same_object(self) -> None:
        supplied = [{"id": "ups:ground", "label": "UPS Ground", "cost": "fixture"}]
        eligible = model.render_cart_ui([model.future_allowlisted_line()], supplied, self.authority)
        blocked = model.render_cart_ui([model.target_line(1368)], supplied, self.authority)
        self.assertIs(supplied, eligible.rates)
        self.assertEqual(supplied, eligible.rates)
        self.assertEqual([], blocked.rates)
        self.assertNotIn("free", json.dumps(blocked.__dict__).casefold())
        self.assertNotIn("pickup", json.dumps(blocked.__dict__).casefold())

    def test_10_controlled_success_creates_one_quote_entry_and_one_notification_only(self) -> None:
        fields = model.valid_form_fields("CA")
        self.assertTrue(self.form.submit(fields))
        self.assertEqual(1, self.form.quote_entries)
        self.assertEqual(1, self.form.notification_callbacks)
        self.assertEqual(0, self.commerce.total)

    def test_11_success_emits_exactly_one_closed_generate_lead(self) -> None:
        fields = model.valid_form_fields("US")
        self.assertTrue(self.form.submit(fields))
        self.assertEqual(1, len(self.analytics.events))
        event = self.analytics.events[0]
        self.assertEqual(model.ANALYTICS_KEYS, frozenset(event))
        self.assertEqual({
            "event": "generate_lead",
            "lead_type": "freight_quote",
            "source_page": "product",
        }, event)
        self.analytics.success_confirmation(
            form_marker=model.FORM_MARKER,
            entry_id=self.form.next_entry_id,
            source_page="product",
        )
        self.assertEqual(1, len(self.analytics.events))

    def test_12_validation_upload_and_confirmation_failures_emit_zero(self) -> None:
        invalid = model.valid_form_fields("CA")
        invalid["email"] = "not-an-email"
        self.assertFalse(self.form.submit(invalid))
        self.assertFalse(self.form.submit(model.valid_form_fields("CA"), upload_ok=False))
        self.assertFalse(self.form.submit(model.valid_form_fields("CA"), confirmation_ok=False))
        self.analytics.success_confirmation(form_marker="WRONG", entry_id=10, source_page="product")
        self.analytics.success_confirmation(form_marker=model.FORM_MARKER, entry_id=0, source_page="product")
        self.assertEqual([], self.analytics.events)

    def test_13_analytics_payload_excludes_pii(self) -> None:
        fields = model.valid_form_fields("CA")
        self.form.submit(fields)
        self.assertTrue(model.analytics_is_closed_and_non_pii(self.analytics.events[0], fields))
        encoded = json.dumps(self.analytics.events[0]).casefold()
        for key in ("first_name", "last_name", "email", "company", "postal", "notes"):
            self.assertNotIn(fields[key].casefold(), encoded)

    def test_14_ca_and_us_validate_while_us_direct_checkout_stays_default_deny(self) -> None:
        for country in ("CA", "US"):
            with self.subTest(country=country):
                self.assertTrue(self.form.validate(model.valid_form_fields(country)))
        invalid = model.valid_form_fields("GB")
        self.assertFalse(self.form.validate(invalid))
        us_unverified = model.target_line(1423)
        self.assertTrue(self.authority.quote_required([us_unverified]))

    def test_15_all_acceptance_actions_have_zero_order_and_payment_callbacks(self) -> None:
        before = self.commerce.__dict__.copy()
        for product_id in model.TARGET_PRODUCTS:
            model.render_product_ui("desktop", model.target_line(product_id), self.authority)
            model.render_product_ui("mobile", model.target_line(product_id), self.authority)
        model.product_handoff(1455, model.TARGET_PRODUCTS[1455]["variation_id"], 1)
        model.cart_handoff([model.future_allowlisted_line(), model.target_line(1455)])
        model.render_cart_ui([model.target_line(1455)], ["UPS Ground"], self.authority)
        self.form.submit(model.valid_form_fields("CA"))
        self.assertEqual(before, self.commerce.__dict__)
        self.assertEqual(0, self.commerce.total)


if __name__ == "__main__":
    unittest.main(verbosity=2)
