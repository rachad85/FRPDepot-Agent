=== FRP Depot Freight Checkout Guard ===
Contributors: frpdepot
Requires at least: 6.0
Tested up to: 6.8
Requires PHP: 7.4
Stable tag: 2.0.3
License: GPLv2 or later

Fixed commissioned Choice-A freight quote journey pinned to specification SHA-256
5348ef3f357676f5629cf72696fd3fe0be718a3847854974f20cf28cc7047400.

== Description ==

Version 2.0.3 repairs the failed-closed version 2.0.2 under the same plugin slug. It preserves the
same default-deny exact product-ID, variation-ID, non-empty-SKU and empty-shipping-
class allowlist authority. A malformed, unreadable or expired allowlist and any
unverified or freight line keep the cart quote-only. No shipping rate is created.

The fixed Choice-A product action is rendered from a server decision. Product and
cart handoffs accept only the closed fqj_* query schema and reconstruct product
names, attributes, URLs, IDs and cart lines on the server. Quote carts have rates,
shipping calculator, checkout controls and gateways suppressed while classic,
order-creation, Store API and REST callback backstops remain in place.

The one commissioned transaction runs only from its authenticated fixed Apply control; plugin
install, overwrite, activation and reactivation perform no business-artifact write. The transaction
searches every Gravity Form first. It
reuses exactly one form only when its admin-only marker, title and complete field,
notification and confirmation projection match; it refuses partial or multiple
matches and otherwise creates exactly one form. It creates one marker-owned quote
page and the exact Contact page 469 sentence replacement, with separate immutable plugin/form/page/Contact/route backups
before the first business write and stores append-only hash-chained receipts.
Ordinary front-end/admin runtime performs no business-artifact write. Activation and reactivation
are read-only. Rollback checks exact post-write hashes
and ownership before each reverse-order restore/delete, refusing on drift.

The dedicated form has exactly 21 fields. Its one active notification uses only the
freshly verified server-side routing projection from Contact Form 1; recipient data
is not exposed in status or receipts. A form-specific successful confirmation with
a positive active entry emits an inert marker. The bundled listener pushes exactly
one dataLayer object with the six keys event, lead_type, form_id, product_id,
variation_id and source_page. It uses no PII, cookies or browser storage.

== Installation ==

This ZIP is only the pinned local artifact for the separately controlled fixed
replacement transaction. No live deployment or acceptance claim is made here.

== Changelog ==

= 2.0.3 =
* Removes the business transaction from the WordPress activation hook.
* Makes install, active overwrite, activation and reactivation content-write-free.
* Keeps the authenticated fixed Apply control as the only transaction trigger.

= 2.0.2 =
* Corrects all three title, public-marker and admin-marker collision comparisons.
* Searches and reuses exactly one form only on the complete ownership projection.
* Refuses partial and multiple matches before a business write and preserves a reused form during rollback.

= 2.0.1 =
* Accepts Gravity Forms' live fixed-email notification route representation, where unused conditional routing is null.
* Still refuses field/routing recipient modes and non-empty conditional routing; recipient values remain unprojected.

= 2.0.0 =
* Preserves default-deny allowlist and strict checkout behavior.
* Adds Choice A, server-reconstructed product/cart handoff and strict backstops.
* Adds the fixed 21-field form, quote page and Contact FAQ activation transaction.
* Adds independent immutable backups, hash-chained receipts and drift-safe rollback.
* Adds a form-specific inert success marker and exact six-key non-PII generate_lead listener.
