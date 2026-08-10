=== FRP Depot Freight Checkout Guard ===
Requires PHP: 7.4
Requires WooCommerce: 7.0
Stable tag: 1.0.1
License: GPL-2.0-or-later

== What it does ==

Blocks checkout and suppresses every purchasable shipping rate whenever the cart
contains any freight-classified or unverified item, and shows exactly:

    Contact us for a freight quote.

== Decision rule (default-deny) ==

An item is UPS-eligible ONLY when all of the following hold:

1. A current, unexpired `ups-allowlist.json` is present and well formed.
2. The item appears in it by exact product ID, exact variation ID and exact
   non-empty SKU.
3. The item carries no shipping class at all.

Everything else requires a freight quote, including:

* the `freight-quote-required` shipping class;
* a blank or unknown shipping class;
* a variation inheriting its parent's class (`get_shipping_class()` resolves
  inheritance before the check, so a blank child value cannot slip through);
* a missing or mismatched SKU;
* a product line the cart cannot resolve;
* a missing, unreadable, malformed or expired allowlist.

One freight item makes the whole cart a freight cart, so a mixed cart is blocked.

== Where it blocks ==

* `woocommerce_package_rates` - removes all rates.
* `woocommerce_no_shipping_available_html` and the cart variant - shows the message.
* `woocommerce_check_cart_items` - stops the cart proceeding to checkout.
* `woocommerce_checkout_process` and `woocommerce_after_checkout_validation` -
  stop a direct classic checkout POST.
* `woocommerce_store_api_cart_errors` and
  `woocommerce_store_api_checkout_update_order_from_request` - stop block-checkout
  and Store API order placement.

Cart and checkout validation iterate the full cart rather than the shipping
package, so marking an item virtual does not bypass the guard.

== Where the customer sees the message ==

* Cart page - the blocking error notice, which the cart template prints.
* Checkout page - `woocommerce_before_template_part` puts the message above
  WooCommerce's generic `checkout/cart-errors.php` shell, and
  `woocommerce_cart_has_errors` repeats the attempt from inside that shell.
* Cart/Checkout Blocks - `woocommerce_store_api_cart_errors` when the block is
  mounted, plus the bundled `assets/frpdepot-freight-notice.js` if the block
  renders without showing the sentence.

Every one of those surfaces goes through a single latch, so at most ONE copy of
the sentence is ever emitted per request.

== Changelog ==

= 1.0.1 =

Fixes the 2026-08-09 production validation failure. 1.0.0 blocked checkout
correctly, but the customer saw only WooCommerce's generic "There are some issues
with the items in your cart..." shell.

Cause: adding an error notice on `woocommerce_check_cart_items` is what blocks the
checkout, and is also what destroyed the message. When cart-item errors exist at
render time WooCommerce does not render the checkout form at all - it renders
`checkout/cart-errors.php`, whose whole visible content is that generic sentence,
and then calls `wc_clear_notices()`. The exact wording was discarded before any
code printed it. The Blocks path inherited the same failure: the block was never
mounted in that branch, so the browser never called the Store API and
`woocommerce_store_api_cart_errors` never ran either.

Fix: emit the sentence into the render path that swallows it, instead of relying
on the notice queue surviving. Added `woocommerce_before_template_part` (narrowed
to `checkout/cart-errors.php`), `woocommerce_cart_has_errors`, and a bundled
static Blocks script. Added a one-message latch and notice deduplication so the
sentence cannot appear twice.

= 1.0.0 =

First release. Withdrawn: blocked checkout but never displayed the message.

== What it never does ==

No writes, no remote calls, no order/customer/payment access, no admin screen,
no settings, no scheduled tasks, no file creation. It reads the cart and the
allowlist file, and blocks.

The bundled script is one fixed local file with no dependencies. It is enqueued
only on a cart/checkout page whose cart is already blocked. It performs no
network request, no eval, no dynamic code, no storage and no cookie access; it
sets text with `textContent`, never `innerHTML`; and it withdraws its own copy of
the sentence if WooCommerce renders that sentence itself.

== Install ==

Upload the ZIP in WordPress under Plugins > Add New > Upload Plugin, then
activate. Verify on a staging site first. Removing the plugin removes the block.
