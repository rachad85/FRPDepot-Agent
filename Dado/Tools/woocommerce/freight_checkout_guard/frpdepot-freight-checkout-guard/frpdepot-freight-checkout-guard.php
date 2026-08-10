<?php
/**
 * Plugin Name: FRP Depot Freight Checkout Guard
 * Description: Blocks checkout and suppresses paid shipping rates whenever the cart
 *              contains any freight-classified or unverified item, and shows the exact
 *              message "Contact us for a freight quote."
 * Version:     1.0.1
 * Author:      FRP Depot
 * License:     GPL-2.0-or-later
 * Requires PHP: 7.4
 *
 * Commissioned by Rachad Homsi on 2026-08-09.
 *
 * DESIGN: default-deny. An item ships by UPS only when it is named in a current,
 * unexpired allowlist by exact product ID, exact variation ID and exact non-empty
 * SKU, and carries no shipping class. Everything else -- freight-classified, blank
 * class, unknown product, missing SKU, stale or unreadable allowlist -- requires a
 * freight quote. One such item makes the whole cart a freight cart.
 *
 * This plugin only reads the cart and blocks. It writes nothing, calls no remote
 * host, touches no order/customer/payment data, and registers no admin surface.
 *
 * ------------------------------------------------------------------------------
 * 1.0.1 -- FIX FOR THE 2026-08-09 PRODUCTION VALIDATION FAILURE.
 *
 * 1.0.0 blocked checkout correctly but the customer never saw the required
 * message; the page showed only WooCommerce's own generic cart-errors wording.
 *
 * CAUSE. Adding an error notice on woocommerce_check_cart_items is what blocks the
 * checkout, but it is also what destroys the message. When cart-item errors exist
 * at page-render time, WooCommerce's checkout renderer does not render the
 * checkout form at all: it renders the checkout/cart-errors.php template -- whose
 * entire visible content is that generic sentence -- and then calls
 * wc_clear_notices(). Our exact wording is discarded before any code prints it.
 * The Cart/Checkout Blocks path inherits the same behaviour: the block is not
 * mounted in that branch, so the browser never calls the Store API, so
 * woocommerce_store_api_cart_errors never runs either. Net effect: blocked (right),
 * generic shell (wrong), exact message absent (wrong).
 *
 * FIX. Emit the message into the render path that swallows it, rather than relying
 * on the notice queue surviving:
 *
 *   - woocommerce_before_template_part, narrowed to checkout/cart-errors.php, puts
 *     the message immediately above the generic shell. This anchors on wc_get_template()
 *     itself, so it does not depend on the contents of that template, and it fires
 *     for both the classic renderer and the block renderer's server-side fallback.
 *   - woocommerce_cart_has_errors is registered as a second anchor inside the same
 *     shell.
 *   - assets/frpdepot-freight-notice.js is a bundled static script, enqueued only on
 *     a cart/checkout page of an already-blocked cart, that surfaces the message in
 *     the Blocks UI if -- and only if -- it is not already on the page.
 *
 * Every emission point runs through frpdepot_fcg_emit_message(), which is latched:
 * at most ONE visible message per request, no matter how many surfaces fire. The
 * cart-item notice is likewise deduplicated before it is added.
 * ------------------------------------------------------------------------------
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/** The exact customer-facing message. Not translated, so it cannot drift. */
const FRPDEPOT_FCG_MESSAGE = 'Contact us for a freight quote.';

/** The exact shipping class slug that marks an item as freight. */
const FRPDEPOT_FCG_FREIGHT_SLUG = 'freight-quote-required';

/** Allowlist file shipped beside this plugin file. */
const FRPDEPOT_FCG_ALLOWLIST_FILE = 'ups-allowlist.json';

/** Must equal the Version: header above. */
const FRPDEPOT_FCG_VERSION = '1.0.1';

/** Bundled static script that carries the message into the Blocks UI. */
const FRPDEPOT_FCG_SCRIPT_FILE = 'assets/frpdepot-freight-notice.js';

/** Registered handle for that script. */
const FRPDEPOT_FCG_SCRIPT_HANDLE = 'frpdepot-freight-checkout-guard-notice';

/** CSS class marking our one emitted message element. */
const FRPDEPOT_FCG_MESSAGE_CLASS = 'frpdepot-freight-quote';

/** The generic WooCommerce template whose shell hid the message in 1.0.0. */
const FRPDEPOT_FCG_CART_ERRORS_TEMPLATE = 'checkout/cart-errors.php';

/* -------------------------------------------------------------------------
 * Pure decision core. No WordPress, no WooCommerce, no I/O: unit testable.
 * ---------------------------------------------------------------------- */

/**
 * Normalise one raw allowlist document into a lookup map.
 *
 * Any defect -- not an array, missing/invalid/expired expiry, missing items,
 * malformed entry -- yields an EMPTY map, which makes every cart item freight.
 *
 * @param mixed $raw     Decoded allowlist document, or null.
 * @param int   $now_ts  Current UNIX timestamp.
 * @return array{ok:bool,reason:string,map:array<string,array<string,string>>}
 */
function frpdepot_fcg_prepare_allowlist( $raw, $now_ts ) {
	$empty = array( 'ok' => false, 'reason' => '', 'map' => array() );

	if ( ! is_array( $raw ) ) {
		$empty['reason'] = 'allowlist_missing_or_unreadable';
		return $empty;
	}
	if ( ! isset( $raw['expires_utc'] ) || ! is_string( $raw['expires_utc'] ) ) {
		$empty['reason'] = 'allowlist_missing_expiry';
		return $empty;
	}
	$expires = strtotime( $raw['expires_utc'] );
	if ( false === $expires ) {
		$empty['reason'] = 'allowlist_invalid_expiry';
		return $empty;
	}
	if ( $now_ts >= $expires ) {
		$empty['reason'] = 'allowlist_stale';
		return $empty;
	}
	if ( ! isset( $raw['items'] ) || ! is_array( $raw['items'] ) ) {
		$empty['reason'] = 'allowlist_missing_items';
		return $empty;
	}

	$map = array();
	foreach ( $raw['items'] as $entry ) {
		if ( ! is_array( $entry ) ) {
			$empty['reason'] = 'allowlist_malformed_entry';
			return $empty;
		}
		if ( ! isset( $entry['product_id'], $entry['variation_id'], $entry['sku'] ) ) {
			$empty['reason'] = 'allowlist_malformed_entry';
			return $empty;
		}
		$product_id   = $entry['product_id'];
		$variation_id = $entry['variation_id'];
		$sku          = $entry['sku'];
		$product_ok   = is_int( $product_id ) && $product_id > 0;
		$variation_ok = is_int( $variation_id ) && $variation_id >= 0;
		$sku_ok       = is_string( $sku ) && '' !== trim( $sku );
		if ( ! $product_ok || ! $variation_ok || ! $sku_ok ) {
			$empty['reason'] = 'allowlist_malformed_entry';
			return $empty;
		}
		$key         = $product_id . ':' . $variation_id;
		$map[ $key ] = array( 'sku' => trim( $sku ) );
	}

	return array( 'ok' => true, 'reason' => '', 'map' => $map );
}

/**
 * Decide whether a normalised cart requires a freight quote.
 *
 * @param array $items    Normalised cart items.
 * @param mixed $raw      Decoded allowlist document, or null.
 * @param int   $now_ts   Current UNIX timestamp.
 * @return array{freight_required:bool,reasons:array<int,string>,message:string}
 */
function frpdepot_fcg_decide( $items, $raw, $now_ts ) {
	$result = array(
		'freight_required' => false,
		'reasons'          => array(),
		'message'          => FRPDEPOT_FCG_MESSAGE,
	);

	if ( ! is_array( $items ) || 0 === count( $items ) ) {
		return $result; // An empty cart is not blocked.
	}

	$allowlist = frpdepot_fcg_prepare_allowlist( $raw, $now_ts );

	foreach ( $items as $index => $item ) {
		$reason = frpdepot_fcg_item_reason( $item, $allowlist );
		if ( '' !== $reason ) {
			$result['freight_required'] = true;
			$result['reasons'][]        = $index . ':' . $reason;
		}
	}

	return $result;
}

/**
 * Why one item needs a freight quote. Empty string means UPS-eligible.
 *
 * @param mixed $item      One normalised cart item.
 * @param array $allowlist Prepared allowlist from frpdepot_fcg_prepare_allowlist().
 * @return string
 */
function frpdepot_fcg_item_reason( $item, $allowlist ) {
	if ( ! is_array( $item ) ) {
		return 'item_unreadable';
	}

	$product_id    = isset( $item['product_id'] ) ? $item['product_id'] : null;
	$variation_id  = isset( $item['variation_id'] ) ? $item['variation_id'] : 0;
	$sku           = isset( $item['sku'] ) ? $item['sku'] : '';
	$shipping_class = isset( $item['shipping_class'] ) ? $item['shipping_class'] : '';

	if ( ! is_int( $product_id ) || $product_id <= 0 ) {
		return 'unresolvable_product';
	}
	if ( ! is_int( $variation_id ) || $variation_id < 0 ) {
		return 'unresolvable_variation';
	}
	if ( ! is_string( $shipping_class ) ) {
		return 'unresolvable_shipping_class';
	}

	// An explicit freight class always blocks, allowlist or not.
	if ( FRPDEPOT_FCG_FREIGHT_SLUG === $shipping_class ) {
		return 'freight_shipping_class';
	}

	if ( ! $allowlist['ok'] ) {
		return '' !== $allowlist['reason'] ? $allowlist['reason'] : 'allowlist_unusable';
	}

	if ( ! is_string( $sku ) || '' === trim( $sku ) ) {
		return 'missing_sku';
	}

	$key = $product_id . ':' . $variation_id;
	if ( ! isset( $allowlist['map'][ $key ] ) ) {
		return 'not_ups_verified';
	}
	if ( $allowlist['map'][ $key ]['sku'] !== trim( $sku ) ) {
		return 'sku_mismatch';
	}

	// A verified parcel item must carry no shipping class at all.
	if ( '' !== $shipping_class ) {
		return 'unexpected_shipping_class';
	}

	return '';
}

/* -------------------------------------------------------------------------
 * WordPress / WooCommerce integration.
 * ---------------------------------------------------------------------- */

/** Read and decode the allowlist document. Returns null on any failure. */
function frpdepot_fcg_load_allowlist_document() {
	static $cached  = null;
	static $is_read = false;

	if ( $is_read ) {
		return $cached; // One read per request; the file cannot change mid-request.
	}
	$is_read = true;

	$path = __DIR__ . '/' . FRPDEPOT_FCG_ALLOWLIST_FILE;
	if ( ! is_readable( $path ) ) {
		return $cached;
	}
	$raw = file_get_contents( $path );
	if ( false === $raw || '' === $raw ) {
		return $cached;
	}
	$decoded = json_decode( $raw, true );
	$cached  = is_array( $decoded ) ? $decoded : null;
	return $cached;
}

/** Normalise the live WooCommerce cart into decision-core items. */
function frpdepot_fcg_normalise_cart( $cart_contents ) {
	$items = array();
	if ( ! is_array( $cart_contents ) ) {
		return $items;
	}
	foreach ( $cart_contents as $line ) {
		$product      = isset( $line['data'] ) ? $line['data'] : null;
		$product_id   = isset( $line['product_id'] ) ? (int) $line['product_id'] : 0;
		$variation_id = isset( $line['variation_id'] ) ? (int) $line['variation_id'] : 0;

		if ( ! is_object( $product ) || ! method_exists( $product, 'get_shipping_class' ) ) {
			// Fail closed: an unreadable line is treated as unverified.
			$items[] = array(
				'product_id'     => $product_id > 0 ? $product_id : 0,
				'variation_id'   => $variation_id,
				'sku'            => '',
				'shipping_class' => '',
			);
			continue;
		}

		// get_shipping_class() already resolves a variation's "inherit" state to the
		// parent's class, so a child cannot slip through with a blank own value.
		$items[] = array(
			'product_id'     => $product_id,
			'variation_id'   => $variation_id,
			'sku'            => method_exists( $product, 'get_sku' ) ? (string) $product->get_sku() : '',
			'shipping_class' => (string) $product->get_shipping_class(),
		);
	}
	return $items;
}

/** Whether the current cart requires a freight quote. */
function frpdepot_fcg_cart_requires_quote() {
	if ( ! function_exists( 'WC' ) || null === WC() || ! isset( WC()->cart ) || null === WC()->cart ) {
		return false;
	}
	$items    = frpdepot_fcg_normalise_cart( WC()->cart->get_cart() );
	$decision = frpdepot_fcg_decide( $items, frpdepot_fcg_load_allowlist_document(), time() );
	return (bool) $decision['freight_required'];
}

/* -------------------------------------------------------------------------
 * Message emission. Latched: one visible message per request, at most.
 * ---------------------------------------------------------------------- */

/**
 * The message markup, ONCE per request.
 *
 * Every surface that wants to show the customer the message asks here. The first
 * caller gets the markup; every later caller gets an empty string. That is what
 * keeps a cart page, a shipping slot, a cart-errors shell and the Blocks script
 * from stacking four copies of the same sentence on one page.
 *
 * The latch lives in a global rather than a function static purely so the test
 * harness can exercise more than one page's worth of surfaces in one process.
 * Both reset identically between real requests.
 *
 * @return string Markup on the first call, '' afterwards.
 */
function frpdepot_fcg_emit_message() {
	if ( ! empty( $GLOBALS['frpdepot_fcg_message_emitted'] ) ) {
		return '';
	}
	$GLOBALS['frpdepot_fcg_message_emitted'] = true;

	return '<p class="' . FRPDEPOT_FCG_MESSAGE_CLASS . '" role="alert">'
		. esc_html( FRPDEPOT_FCG_MESSAGE ) . '</p>';
}

/** Echo the one message if it has not been shown yet. */
function frpdepot_fcg_print_message() {
	$markup = frpdepot_fcg_emit_message();
	if ( '' !== $markup ) {
		echo $markup; // phpcs:ignore WordPress.Security.EscapeOutput -- built from esc_html() above.
	}
}

/* -------------------------------------------------------------------------
 * Blocking surfaces.
 * ---------------------------------------------------------------------- */

/** Suppress every purchasable shipping rate for a freight cart. */
function frpdepot_fcg_filter_package_rates( $rates, $package = array() ) {
	unset( $package );
	if ( frpdepot_fcg_cart_requires_quote() ) {
		return array();
	}
	return $rates;
}

/**
 * The exact message, wherever WooCommerce reports "no shipping available".
 *
 * If the message is already on the page this leaves WooCommerce's own wording in
 * place rather than printing a second copy.
 */
function frpdepot_fcg_no_shipping_html( $html ) {
	if ( ! frpdepot_fcg_cart_requires_quote() ) {
		return $html;
	}
	$markup = frpdepot_fcg_emit_message();
	return '' !== $markup ? $markup : $html;
}

/**
 * Block the cart page from proceeding to checkout.
 *
 * The notice is both the block and, on the cart page, the visible message: the
 * cart template prints notices. It is deduplicated so a notice left unprinted by
 * an earlier request cannot become a second identical line.
 */
function frpdepot_fcg_check_cart_items() {
	if ( ! frpdepot_fcg_cart_requires_quote() || ! function_exists( 'wc_add_notice' ) ) {
		return;
	}
	if ( function_exists( 'wc_has_notice' ) && wc_has_notice( FRPDEPOT_FCG_MESSAGE, 'error' ) ) {
		return;
	}
	wc_add_notice( FRPDEPOT_FCG_MESSAGE, 'error' );
}

/**
 * Put the message above WooCommerce's generic cart-errors shell.
 *
 * THIS IS THE 1.0.1 FIX. When cart-item errors exist at render time WooCommerce
 * renders checkout/cart-errors.php instead of the checkout form and then calls
 * wc_clear_notices(), so the notice added above is destroyed unprinted -- that is
 * exactly what production showed on 2026-08-09. Anchoring on wc_get_template()
 * puts the message on the page before that shell, in both the classic renderer
 * and the block renderer's server-side fallback, without depending on anything
 * inside the template.
 *
 * @param string $template_name Template being rendered, e.g. checkout/cart-errors.php.
 */
function frpdepot_fcg_before_template_part( $template_name, $template_path = '', $located = '', $args = array() ) {
	unset( $template_path, $located, $args );
	if ( FRPDEPOT_FCG_CART_ERRORS_TEMPLATE !== $template_name ) {
		return;
	}
	if ( ! frpdepot_fcg_cart_requires_quote() ) {
		return;
	}
	frpdepot_fcg_print_message();
}

/** Second anchor inside the same shell; the latch stops a double print. */
function frpdepot_fcg_cart_has_errors() {
	if ( frpdepot_fcg_cart_requires_quote() ) {
		frpdepot_fcg_print_message();
	}
}

/** Block the classic checkout POST, not merely the visible rate list. */
function frpdepot_fcg_after_checkout_validation( $data, $errors = null ) {
	unset( $data );
	if ( frpdepot_fcg_cart_requires_quote() && is_object( $errors ) && method_exists( $errors, 'add' ) ) {
		$errors->add( 'frpdepot_freight_quote_required', FRPDEPOT_FCG_MESSAGE );
	}
}

/** Block the Store API (block checkout) cart. */
function frpdepot_fcg_store_api_cart_errors( $errors ) {
	if ( frpdepot_fcg_cart_requires_quote() && is_object( $errors ) && method_exists( $errors, 'add' ) ) {
		$errors->add( 'frpdepot_freight_quote_required', FRPDEPOT_FCG_MESSAGE );
	}
	return $errors;
}

/** Block Store API order placement. */
function frpdepot_fcg_store_api_checkout_guard() {
	if ( ! frpdepot_fcg_cart_requires_quote() ) {
		return;
	}
	if ( class_exists( '\Automattic\WooCommerce\StoreApi\Exceptions\RouteException' ) ) {
		throw new \Automattic\WooCommerce\StoreApi\Exceptions\RouteException(
			'frpdepot_freight_quote_required',
			FRPDEPOT_FCG_MESSAGE,
			409
		);
	}
	throw new \Exception( FRPDEPOT_FCG_MESSAGE );
}

/** Last-resort block for any remaining classic checkout path. */
function frpdepot_fcg_checkout_process() {
	if ( ! frpdepot_fcg_cart_requires_quote() || ! function_exists( 'wc_add_notice' ) ) {
		return;
	}
	if ( function_exists( 'wc_has_notice' ) && wc_has_notice( FRPDEPOT_FCG_MESSAGE, 'error' ) ) {
		return;
	}
	wc_add_notice( FRPDEPOT_FCG_MESSAGE, 'error' );
}

/* -------------------------------------------------------------------------
 * Cart/Checkout Blocks. One bundled static file, enqueued only when blocking.
 * ---------------------------------------------------------------------- */

/** Whether this request is a cart or checkout page, classic or block. */
function frpdepot_fcg_is_cart_or_checkout() {
	if ( function_exists( 'is_cart' ) && is_cart() ) {
		return true;
	}
	if ( function_exists( 'is_checkout' ) && is_checkout() ) {
		return true;
	}
	if ( function_exists( 'has_block' ) ) {
		if ( has_block( 'woocommerce/cart' ) || has_block( 'woocommerce/checkout' ) ) {
			return true;
		}
	}
	return false;
}

/**
 * Register and enqueue the bundled Blocks notice script.
 *
 * Deliberately narrow: one fixed local file, no dependencies, no inline script, no
 * localised data, no settings, and it is not enqueued at all unless this cart is
 * already blocked and this is a cart/checkout page. The message is compiled into
 * the file as a literal, so nothing dynamic crosses the PHP/JS boundary.
 */
function frpdepot_fcg_enqueue_blocks_notice() {
	if ( function_exists( 'is_admin' ) && is_admin() ) {
		return;
	}
	if ( ! function_exists( 'wp_register_script' ) || ! function_exists( 'plugins_url' ) ) {
		return;
	}
	if ( ! frpdepot_fcg_is_cart_or_checkout() || ! frpdepot_fcg_cart_requires_quote() ) {
		return;
	}
	if ( ! is_readable( __DIR__ . '/' . FRPDEPOT_FCG_SCRIPT_FILE ) ) {
		return;
	}
	wp_register_script(
		FRPDEPOT_FCG_SCRIPT_HANDLE,
		plugins_url( FRPDEPOT_FCG_SCRIPT_FILE, __FILE__ ),
		array(),
		FRPDEPOT_FCG_VERSION,
		true
	);
	wp_enqueue_script( FRPDEPOT_FCG_SCRIPT_HANDLE );
}

add_filter( 'woocommerce_package_rates', 'frpdepot_fcg_filter_package_rates', 100, 2 );
add_filter( 'woocommerce_no_shipping_available_html', 'frpdepot_fcg_no_shipping_html', 100 );
add_filter( 'woocommerce_cart_no_shipping_available_html', 'frpdepot_fcg_no_shipping_html', 100 );
add_action( 'woocommerce_check_cart_items', 'frpdepot_fcg_check_cart_items', 100 );
add_action( 'woocommerce_checkout_process', 'frpdepot_fcg_checkout_process', 100 );
add_action( 'woocommerce_after_checkout_validation', 'frpdepot_fcg_after_checkout_validation', 100, 2 );
add_filter( 'woocommerce_store_api_cart_errors', 'frpdepot_fcg_store_api_cart_errors', 100 );
add_action( 'woocommerce_store_api_checkout_update_order_from_request', 'frpdepot_fcg_store_api_checkout_guard', 100 );
add_action( 'woocommerce_before_template_part', 'frpdepot_fcg_before_template_part', 1, 4 );
add_action( 'woocommerce_cart_has_errors', 'frpdepot_fcg_cart_has_errors', 1 );
add_action( 'wp_enqueue_scripts', 'frpdepot_fcg_enqueue_blocks_notice', 100 );
