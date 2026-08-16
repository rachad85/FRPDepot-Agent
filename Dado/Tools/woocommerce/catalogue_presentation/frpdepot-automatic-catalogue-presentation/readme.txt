=== FRP Depot Automatic Catalogue Presentation ===
Contributors: frpdepot
Requires PHP: 7.4
Stable tag: 1.0.4
License: GPLv2 or later

Read-time public catalogue presentation for FRP Depot.

== Scope ==

* Removes only the exact Hetron Chemical Resistance Guide card/link from the shared Divi WooCommerce product output.
* Repoints only the exact Derakane Chemical Resistance Guide card from the PDF page to /derakane-resin-resistance-search/.
* Preserves the existing inline Derakane search call to action.
* Projects the one permanent /products/ catalogue root with the exact public title Shop All, without changing the stored menu item.
* Projects published, password-free, catalog-visible WooCommerce products into synchronized main/mobile/footer catalogue navigation.
* Groups each product once under its deepest assigned category beneath the existing approved Piping & Fluid Handling or Manways roots.
* Excludes Uncategorized and categories outside those approved roots.
* Reads and reuses existing product/category permalinks.

== Persistence ==

The plugin creates no settings or admin UI and makes no persistent product, category, menu, post, option, theme, order, cart or customer write. Its catalogue projection is rebuilt from current public WooCommerce data once per request and memoized only in PHP process memory for that request.

== Fail-closed behavior ==

The shared Divi transformation is atomic and requires the exact post-protection state: one Hetron card with no retired Hetron URL/link data, one exact Derakane card with its old link data, and one exact inline CTA. Missing, duplicate or changed markers leave output unchanged.
