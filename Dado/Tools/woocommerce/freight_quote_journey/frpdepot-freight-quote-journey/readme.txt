=== FRP Depot Freight Quote Journey ===
Contributors: frpdepots
Tags: woocommerce, freight, gravity forms, quote
Requires at least: 6.4
Tested up to: 6.8
Requires PHP: 7.4
Stable tag: 1.0.0
License: GPLv2 or later

Adds the commissioned Choice A freight-quote journey without changing the active freight guard.

== Description ==

This companion plugin delegates every freight/direct-checkout decision to the active FRP Depot Freight Checkout Guard 1.0.1 and its allowlist. It adds the product, cart, Gravity Forms, Contact FAQ and success-only analytics presentation layers.

Specification SHA-256: 5348ef3f357676f5629cf72696fd3fe0be718a3847854974f20cf28cc7047400

== Installation ==

Use only the commissioned freight_quote_journey_deployment_tool.py. The existing Freight Checkout Guard 1.0.1, WooCommerce and Gravity Forms must already be active.

== Privacy ==

The generate_lead event has exactly five non-personal parameters: lead_type, form_id, product_id, variation_id and source_page. Form entries remain managed by Gravity Forms. Deactivation does not delete forms or entries.
