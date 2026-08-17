=== FRP Depot Automatic Catalogue Presentation ===
Contributors: frpdepot
Requires PHP: 7.4
Stable tag: 1.1.1
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
* Bundles five fixed reviewed section catalogues: Stub Flanges, Manways & Covers, 90 Degree Elbows, Filament-Wound Pipe, and FNPT Couplings.
* Repoints each of the six existing product pages' exact Product Catalog card to its one matching section PDF.
* Shows four distinct section links on the Piping & Fluid Handling parent and one matching link on each existing child category or Manways page.
* Neutralizes the retired full-catalogue product-card URL when exact section-card markers or a known product mapping are unavailable.
* Inserts category panels through the final rendered Divi archive output because the live Divi template omits WooCommerce's standard archive-description action.

== Persistence ==

The plugin creates no settings, Media Library records or admin UI and makes no persistent product, category, menu, post, option, theme, order, cart or customer write. Section PDFs are static members of the fixed plugin artifact, not attachments. Its catalogue projection is rebuilt from current public WooCommerce data once per request and memoized only in PHP process memory for that request.

== Fail-closed behavior ==

The shared Divi transformation is atomic and requires the exact post-protection state: one Hetron card with no retired Hetron URL/link data, one exact Derakane card with its old link data, and one exact inline CTA. Missing, duplicate or changed markers leave output unchanged.

The section-catalogue card transformation separately requires the exact visible Product Catalog card and exact old Divi link marker. A known exact product receives one matching bundled PDF. Drift or an unmapped product cannot retain the retired full-catalogue URL.

Category insertion requires exactly one live Divi shop-module anchor, no prior catalogue panel, no section PDF URL already present and no full-catalogue URL. A missing, duplicated or changed anchor leaves the category output unchanged rather than inserting at an uncertain location.

== Changelog ==

= 1.1.1 =
* Repair category catalogue-panel insertion for the live Divi archive template using one exact, fail-closed final-output anchor.

= 1.1.0 =
* Bundle five reviewed section catalogues and add exact product/category mappings.
