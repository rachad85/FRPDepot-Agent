<?php
/**
 * Plugin Name: FRP Depot Freight Checkout Guard
 * Description: Default-deny checkout guard and the fixed commissioned Choice-A freight quote journey.
 * Version:     2.0.4
 * Author:      FRP Depot
 * License:     GPL-2.0-or-later
 * Requires PHP: 7.4
 *
 * Specification SHA-256: 5348ef3f357676f5629cf72696fd3fe0be718a3847854974f20cf28cc7047400
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

const FRPDEPOT_FQJ_VERSION = '2.0.4';
const FRPDEPOT_FQJ_SPEC_SHA256 = '5348ef3f357676f5629cf72696fd3fe0be718a3847854974f20cf28cc7047400';
const FRPDEPOT_FQJ_FREIGHT_SLUG = 'freight-quote-required';
const FRPDEPOT_FQJ_ALLOWLIST_FILE = 'ups-allowlist.json';
const FRPDEPOT_FQJ_JS = 'assets/frpdepot-freight-quote-journey.js';
const FRPDEPOT_FQJ_CSS = 'assets/frpdepot-freight-quote-journey.css';

const FRPDEPOT_FQJ_PRODUCT_HEADING = 'Freight quote required';
const FRPDEPOT_FQJ_PRODUCT_TEXT = 'This selection is not available for direct online checkout. Choose the size, pressure rating, resin type and quantity, then request a product and freight quote. No payment will be taken.';
const FRPDEPOT_FQJ_BUTTON = 'Request a Freight Quote';
const FRPDEPOT_FQJ_CART_TEXT = 'One or more items in this cart require a product and freight quote. Your selected products, options and quantities will be included automatically. No payment will be taken.';
const FRPDEPOT_FQJ_PAGE_TITLE = 'Request a Product and Freight Quote';
const FRPDEPOT_FQJ_PAGE_SLUG = 'request-a-quote';
const FRPDEPOT_FQJ_PAGE_INTRO = 'Send the selected product details and delivery destination below. FRP Depots will review product availability, packing and freight requirements before providing a complete quote. Submitting this form does not place an order or authorize payment.';
const FRPDEPOT_FQJ_FORM_TITLE = 'Product and Freight Quote Request';
const FRPDEPOT_FQJ_FORM_MARKER = 'FRPDEPOT_FQJ_FIXED_FORM_V2_SPEC_5348EF3F';
const FRPDEPOT_FQJ_FORM_ADMIN_MARKER = 'FRPDEPOT_FQJ_ADMIN_OWNERSHIP_V1_SPEC_5348EF3F';
const FRPDEPOT_FQJ_PAGE_MARKER = '<!-- FRPDEPOT_FQJ_FIXED_PAGE_V2_SPEC_5348EF3F -->';
const FRPDEPOT_FQJ_CONFIRMATION = 'Your quote request has been received. FRP Depots will review the product, packing and freight requirements before responding. No order has been placed and no payment has been taken.';
const FRPDEPOT_FQJ_HANDOFF_ERROR = 'The selected product details could not be verified. Please start the quote request again.';
const FRPDEPOT_FQJ_CONTACT_OLD = 'If your item is listed in the Products section, you can add it to cart; otherwise use the contact form for custom or non-standard requests.';
const FRPDEPOT_FQJ_CONTACT_NEW = 'Product selections approved for direct shipping can be purchased online. Selections requiring packing or freight review will show Request a Freight Quote. Submitting a quote request does not place an order or authorize payment.';

const FRPDEPOT_FQJ_CONTACT_ID = 469;
const FRPDEPOT_FQJ_SOURCE_FORM_ID = 1;
const FRPDEPOT_FQJ_MAX_CART_LINES = 50;
const FRPDEPOT_FQJ_MAX_CART_JSON_BYTES = 12000;
const FRPDEPOT_FQJ_MAX_QUANTITY = 9999;
const FRPDEPOT_FQJ_CART_ERRORS_TEMPLATE = 'checkout/cart-errors.php';
const FRPDEPOT_FQJ_ADMIN_SLUG = 'frpdepot-freight-quote-journey';

const FRPDEPOT_FQJ_STATE_OPTION = 'frpdepot_fqj_state_v2';
const FRPDEPOT_FQJ_LOCK_OPTION = 'frpdepot_fqj_activation_lock_v2';
const FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION = 'frpdepot_fqj_receipt_head_v2';
const FRPDEPOT_FQJ_BACKUP_PLUGIN_OPTION = 'frpdepot_fqj_backup_plugin_v2';
const FRPDEPOT_FQJ_BACKUP_FORM_OPTION = 'frpdepot_fqj_backup_form_v2';
const FRPDEPOT_FQJ_BACKUP_PAGE_OPTION = 'frpdepot_fqj_backup_quote_page_v2';
const FRPDEPOT_FQJ_BACKUP_CONTACT_OPTION = 'frpdepot_fqj_backup_contact_v2';
const FRPDEPOT_FQJ_BACKUP_ROUTE_OPTION = 'frpdepot_fqj_backup_route_v2';

/** Fixed assessed product IDs only. */
function frpdepot_fqj_product_targets() {
	return array( 1455, 1423, 1368, 1397, 1411 );
}

/* -------------------------------------------------------------------------
 * Default-deny authority, retained behaviorally from version 1.0.1.
 * ---------------------------------------------------------------------- */

function frpdepot_fqj_prepare_allowlist( $raw, $now_ts ) {
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
		if ( ! is_array( $entry ) || ! isset( $entry['product_id'], $entry['variation_id'], $entry['sku'] ) ) {
			$empty['reason'] = 'allowlist_malformed_entry';
			return $empty;
		}
		$product_ok = is_int( $entry['product_id'] ) && $entry['product_id'] > 0;
		$variation_ok = is_int( $entry['variation_id'] ) && $entry['variation_id'] >= 0;
		$sku_ok = is_string( $entry['sku'] ) && '' !== trim( $entry['sku'] );
		if ( ! $product_ok || ! $variation_ok || ! $sku_ok ) {
			$empty['reason'] = 'allowlist_malformed_entry';
			return $empty;
		}
		$key = $entry['product_id'] . ':' . $entry['variation_id'];
		$map[ $key ] = array( 'sku' => trim( $entry['sku'] ) );
	}
	return array( 'ok' => true, 'reason' => '', 'map' => $map );
}

function frpdepot_fqj_item_reason( $item, $allowlist ) {
	if ( ! is_array( $item ) ) {
		return 'item_unreadable';
	}
	$product_id = isset( $item['product_id'] ) ? $item['product_id'] : null;
	$variation_id = isset( $item['variation_id'] ) ? $item['variation_id'] : 0;
	$sku = isset( $item['sku'] ) ? $item['sku'] : '';
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
	if ( FRPDEPOT_FQJ_FREIGHT_SLUG === $shipping_class ) {
		return 'freight_shipping_class';
	}
	if ( empty( $allowlist['ok'] ) ) {
		return ! empty( $allowlist['reason'] ) ? $allowlist['reason'] : 'allowlist_unusable';
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
	if ( '' !== $shipping_class ) {
		return 'unexpected_shipping_class';
	}
	return '';
}

function frpdepot_fqj_decide( $items, $raw, $now_ts ) {
	$result = array(
		'freight_required' => false,
		'reasons' => array(),
		'message' => FRPDEPOT_FQJ_CART_TEXT,
	);
	if ( ! is_array( $items ) || 0 === count( $items ) ) {
		return $result;
	}
	$allowlist = frpdepot_fqj_prepare_allowlist( $raw, $now_ts );
	foreach ( $items as $index => $item ) {
		$reason = frpdepot_fqj_item_reason( $item, $allowlist );
		if ( '' !== $reason ) {
			$result['freight_required'] = true;
			$result['reasons'][] = $index . ':' . $reason;
		}
	}
	return $result;
}

function frpdepot_fqj_load_allowlist_document() {
	static $cached = null;
	static $read = false;
	if ( $read ) {
		return $cached;
	}
	$read = true;
	$path = __DIR__ . '/' . FRPDEPOT_FQJ_ALLOWLIST_FILE;
	if ( ! is_readable( $path ) ) {
		return null;
	}
	$raw = file_get_contents( $path );
	if ( false === $raw || '' === $raw ) {
		return null;
	}
	$decoded = json_decode( $raw, true );
	$cached = is_array( $decoded ) ? $decoded : null;
	return $cached;
}

function frpdepot_fqj_product_item( $product_id, $variation_id, $product ) {
	if ( ! is_object( $product ) || ! method_exists( $product, 'get_shipping_class' ) ) {
		return array(
			'product_id' => (int) $product_id,
			'variation_id' => (int) $variation_id,
			'sku' => '',
			'shipping_class' => '',
		);
	}
	return array(
		'product_id' => (int) $product_id,
		'variation_id' => (int) $variation_id,
		'sku' => method_exists( $product, 'get_sku' ) ? (string) $product->get_sku() : '',
		'shipping_class' => (string) $product->get_shipping_class(),
	);
}

function frpdepot_fqj_item_requires_quote( $product_id, $variation_id, $product ) {
	$prepared = frpdepot_fqj_prepare_allowlist( frpdepot_fqj_load_allowlist_document(), time() );
	return '' !== frpdepot_fqj_item_reason(
		frpdepot_fqj_product_item( (int) $product_id, (int) $variation_id, $product ),
		$prepared
	);
}

function frpdepot_fqj_normalise_cart( $cart_contents ) {
	$items = array();
	if ( ! is_array( $cart_contents ) ) {
		return $items;
	}
	foreach ( $cart_contents as $line ) {
		$product = is_array( $line ) && isset( $line['data'] ) ? $line['data'] : null;
		$product_id = is_array( $line ) && isset( $line['product_id'] ) ? (int) $line['product_id'] : 0;
		$variation_id = is_array( $line ) && isset( $line['variation_id'] ) ? (int) $line['variation_id'] : 0;
		if ( ! is_object( $product ) || ! method_exists( $product, 'get_shipping_class' ) ) {
			// Preserve the baseline unreadable-line projection exactly, without
			// relying on PHP's warning-producing array-offset coercion.
			$items[] = array(
				'product_id' => $product_id > 0 ? $product_id : 0,
				'variation_id' => $variation_id,
				'sku' => '',
				'shipping_class' => '',
			);
			continue;
		}
		$items[] = array(
			'product_id' => $product_id,
			'variation_id' => $variation_id,
			'sku' => method_exists( $product, 'get_sku' ) ? (string) $product->get_sku() : '',
			'shipping_class' => (string) $product->get_shipping_class(),
		);
	}
	return $items;
}

function frpdepot_fqj_cart_requires_quote() {
	if ( ! function_exists( 'WC' ) || null === WC() || ! isset( WC()->cart ) || null === WC()->cart ) {
		return false;
	}
	$decision = frpdepot_fqj_decide(
		frpdepot_fqj_normalise_cart( WC()->cart->get_cart() ),
		frpdepot_fqj_load_allowlist_document(),
		time()
	);
	return (bool) $decision['freight_required'];
}

/** Direct checkout endpoints fail closed when the cart cannot be resolved. */
function frpdepot_fqj_direct_checkout_requires_quote() {
	if ( ! function_exists( 'WC' ) || null === WC() || ! isset( WC()->cart ) || null === WC()->cart ) {
		return true;
	}
	$cart = WC()->cart->get_cart();
	if ( ! is_array( $cart ) ) {
		return true;
	}
	return frpdepot_fqj_cart_requires_quote();
}

/* -------------------------------------------------------------------------
 * Product Choice A and strict add-to-cart backstop.
 * ---------------------------------------------------------------------- */

function frpdepot_fqj_available_variation( $data, $product, $variation ) {
	if ( ! is_array( $data ) || ! is_object( $product ) || ! is_object( $variation )
		|| ! method_exists( $product, 'get_id' ) || ! method_exists( $variation, 'get_id' ) ) {
		return $data;
	}
	$product_id = (int) $product->get_id();
	if ( ! in_array( $product_id, frpdepot_fqj_product_targets(), true ) ) {
		return $data;
	}
	$variation_id = (int) $variation->get_id();
	$data['frpdepot_quote_required'] = frpdepot_fqj_item_requires_quote( $product_id, $variation_id, $variation );
	$data['frpdepot_product_id'] = $product_id;
	$data['frpdepot_variation_id'] = $variation_id;
	return $data;
}

function frpdepot_fqj_product_quote_panel() {
	global $product;
	if ( ! is_object( $product ) || ! method_exists( $product, 'get_id' ) || ! method_exists( $product, 'is_type' )
		|| ! $product->is_type( 'variable' ) || ! in_array( (int) $product->get_id(), frpdepot_fqj_product_targets(), true ) ) {
		return;
	}
	echo '<section class="frpdepot-fqj-product" hidden>';
	echo '<h3>' . esc_html( FRPDEPOT_FQJ_PRODUCT_HEADING ) . '</h3>';
	echo '<p>' . esc_html( FRPDEPOT_FQJ_PRODUCT_TEXT ) . '</p>';
	echo '<a class="button alt frpdepot-fqj-product-button" aria-disabled="true" tabindex="-1">'
		. esc_html( FRPDEPOT_FQJ_BUTTON ) . '</a></section>';
}

function frpdepot_fqj_add_to_cart_validation( $passed, $product_id, $quantity, $variation_id = 0, $variations = array() ) {
	unset( $quantity, $variations );
	if ( ! $passed ) {
		return false;
	}
	$product_id = (int) $product_id;
	$variation_id = (int) $variation_id;
	$selection_id = $variation_id > 0 ? $variation_id : $product_id;
	$product = function_exists( 'wc_get_product' ) && $selection_id > 0 ? wc_get_product( $selection_id ) : null;
	if ( $variation_id > 0 && ( ! is_object( $product ) || ! method_exists( $product, 'get_parent_id' )
		|| (int) $product->get_parent_id() !== $product_id ) ) {
		frpdepot_fqj_add_blocking_notice();
		return false;
	}
	if ( frpdepot_fqj_item_requires_quote( $product_id, $variation_id, $product ) ) {
		frpdepot_fqj_add_blocking_notice();
		return false;
	}
	return $passed;
}

/* -------------------------------------------------------------------------
 * Cart presentation, rate suppression, and server checkout backstops.
 * ---------------------------------------------------------------------- */

function frpdepot_fqj_block_message() {
	return FRPDEPOT_FQJ_PRODUCT_HEADING . '. ' . FRPDEPOT_FQJ_CART_TEXT;
}

function frpdepot_fqj_filter_package_rates( $rates, $package = array() ) {
	unset( $package );
	return frpdepot_fqj_cart_requires_quote() ? array() : $rates;
}

function frpdepot_fqj_filter_gateways( $gateways ) {
	return frpdepot_fqj_cart_requires_quote() ? array() : $gateways;
}

function frpdepot_fqj_shipping_calculator_enable( $enabled ) {
	return frpdepot_fqj_cart_requires_quote() ? false : $enabled;
}

function frpdepot_fqj_emit_cart_message() {
	if ( ! empty( $GLOBALS['frpdepot_fqj_message_emitted'] ) ) {
		return '';
	}
	$GLOBALS['frpdepot_fqj_message_emitted'] = true;
	return '<div class="woocommerce-info frpdepot-fqj-cart-notice" role="status"><strong>'
		. esc_html( FRPDEPOT_FQJ_PRODUCT_HEADING ) . '</strong><br>' . esc_html( FRPDEPOT_FQJ_CART_TEXT ) . '</div>';
}

function frpdepot_fqj_print_cart_message() {
	if ( frpdepot_fqj_cart_requires_quote() ) {
		$markup = frpdepot_fqj_emit_cart_message();
		if ( '' !== $markup ) {
			echo $markup; // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped
		}
	}
}

function frpdepot_fqj_no_shipping_html( $html ) {
	if ( ! frpdepot_fqj_cart_requires_quote() ) {
		return $html;
	}
	$markup = frpdepot_fqj_emit_cart_message();
	return '' !== $markup ? $markup : $html;
}

function frpdepot_fqj_add_blocking_notice() {
	if ( ! function_exists( 'wc_add_notice' ) ) {
		return;
	}
	$message = frpdepot_fqj_block_message();
	if ( ! function_exists( 'wc_has_notice' ) || ! wc_has_notice( $message, 'error' ) ) {
		wc_add_notice( $message, 'error' );
	}
}

function frpdepot_fqj_check_cart_items() {
	if ( frpdepot_fqj_cart_requires_quote() ) {
		frpdepot_fqj_add_blocking_notice();
	}
}

function frpdepot_fqj_checkout_process() {
	if ( frpdepot_fqj_direct_checkout_requires_quote() ) {
		frpdepot_fqj_add_blocking_notice();
	}
}

function frpdepot_fqj_after_checkout_validation( $data, $errors = null ) {
	unset( $data );
	if ( frpdepot_fqj_direct_checkout_requires_quote() && is_object( $errors ) && method_exists( $errors, 'add' ) ) {
		$errors->add( 'frpdepot_freight_quote_required', frpdepot_fqj_block_message() );
	}
}

function frpdepot_fqj_checkout_create_order_guard( $order, $data ) {
	unset( $order, $data );
	if ( ! frpdepot_fqj_direct_checkout_requires_quote() ) {
		return;
	}
	if ( class_exists( 'WC_Data_Exception' ) ) {
		throw new WC_Data_Exception( 'frpdepot_freight_quote_required', frpdepot_fqj_block_message() );
	}
	throw new RuntimeException( frpdepot_fqj_block_message() );
}

function frpdepot_fqj_store_api_cart_errors( $errors ) {
	if ( frpdepot_fqj_direct_checkout_requires_quote() && is_object( $errors ) && method_exists( $errors, 'add' ) ) {
		$errors->add( 'frpdepot_freight_quote_required', frpdepot_fqj_block_message() );
	}
	return $errors;
}

function frpdepot_fqj_store_api_checkout_guard( $order = null, $request = null ) {
	unset( $order, $request );
	if ( ! frpdepot_fqj_direct_checkout_requires_quote() ) {
		return;
	}
	if ( class_exists( '\\Automattic\\WooCommerce\\StoreApi\\Exceptions\\RouteException' ) ) {
		throw new \Automattic\WooCommerce\StoreApi\Exceptions\RouteException(
			'frpdepot_freight_quote_required',
			frpdepot_fqj_block_message(),
			409
		);
	}
	throw new RuntimeException( frpdepot_fqj_block_message() );
}

function frpdepot_fqj_rest_checkout_pre_callback( $response, $handler, $request ) {
	unset( $handler );
	if ( null !== $response || ! is_object( $request ) || ! method_exists( $request, 'get_method' )
		|| ! method_exists( $request, 'get_route' ) ) {
		return $response;
	}
	if ( 'POST' !== strtoupper( (string) $request->get_method() )
		|| '/wc/store/v1/checkout' !== rtrim( (string) $request->get_route(), '/' ) ) {
		return $response;
	}
	if ( frpdepot_fqj_direct_checkout_requires_quote() ) {
		return new WP_Error(
			'frpdepot_freight_quote_required',
			frpdepot_fqj_block_message(),
			array( 'status' => 409 )
		);
	}
	return $response;
}

function frpdepot_fqj_before_template_part( $template_name, $template_path = '', $located = '', $args = array() ) {
	unset( $template_path, $located, $args );
	if ( FRPDEPOT_FQJ_CART_ERRORS_TEMPLATE === $template_name && frpdepot_fqj_cart_requires_quote() ) {
		frpdepot_fqj_print_cart_message();
	}
}

function frpdepot_fqj_cart_has_errors() {
	frpdepot_fqj_print_cart_message();
}

function frpdepot_fqj_remove_classic_checkout_control() {
	if ( frpdepot_fqj_cart_requires_quote() ) {
		remove_action( 'woocommerce_proceed_to_checkout', 'woocommerce_button_proceed_to_checkout', 20 );
	}
}

function frpdepot_fqj_cart_quote_url() {
	$url = frpdepot_fqj_quote_page_url();
	return '' !== $url && function_exists( 'add_query_arg' ) ? add_query_arg( array( 'fqj_source' => 'cart' ), $url ) : '';
}

function frpdepot_fqj_cart_quote_button() {
	$url = frpdepot_fqj_cart_requires_quote() ? frpdepot_fqj_cart_quote_url() : '';
	if ( '' !== $url ) {
		echo '<a class="button alt frpdepot-fqj-cart-button" href="' . esc_url( $url ) . '">'
			. esc_html( FRPDEPOT_FQJ_BUTTON ) . '</a>';
	}
}

/* -------------------------------------------------------------------------
 * Canonical hashes, immutable backups, chained receipts, and fixed transaction.
 * ---------------------------------------------------------------------- */

function frpdepot_fqj_canonicalize( $value ) {
	if ( is_object( $value ) ) {
		$value = get_object_vars( $value );
	}
	if ( ! is_array( $value ) ) {
		return $value;
	}
	$is_list = array_keys( $value ) === range( 0, count( $value ) - 1 );
	if ( ! $is_list ) {
		ksort( $value, SORT_STRING );
	}
	foreach ( $value as $key => $item ) {
		$value[ $key ] = frpdepot_fqj_canonicalize( $item );
	}
	return $value;
}

function frpdepot_fqj_hash( $value ) {
	return hash( 'sha256', wp_json_encode( frpdepot_fqj_canonicalize( $value ), JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE ) );
}

function frpdepot_fqj_backup_options() {
	return array(
		'plugin' => FRPDEPOT_FQJ_BACKUP_PLUGIN_OPTION,
		'form' => FRPDEPOT_FQJ_BACKUP_FORM_OPTION,
		'quote_page' => FRPDEPOT_FQJ_BACKUP_PAGE_OPTION,
		'contact_faq' => FRPDEPOT_FQJ_BACKUP_CONTACT_OPTION,
		'route' => FRPDEPOT_FQJ_BACKUP_ROUTE_OPTION,
	);
}

function frpdepot_fqj_receipt_option( $deployment_id, $sequence ) {
	$clean_id = preg_replace( '/[^a-z0-9]/', '', strtolower( (string) $deployment_id ) );
	return 'frpdepot_fqj_receipt_v2_' . $clean_id . '_' . str_pad( (string) $sequence, 6, '0', STR_PAD_LEFT );
}

function frpdepot_fqj_append_receipt( $operation, $artifact, $artifact_id, $before_sha256, $after_sha256, $status ) {
	$operations = array( 'backup', 'create', 'replace', 'verify', 'restore', 'delete', 'finalize' );
	$artifacts = array( 'plugin', 'form', 'quote_page', 'contact_faq', 'route', 'journey' );
	$statuses = array( 'OK', 'FAILED_CLOSED', 'INDETERMINATE', 'ROLLBACK_BLOCKED_DRIFT', 'ROLLED_BACK' );
	if ( ! in_array( $operation, $operations, true ) || ! in_array( $artifact, $artifacts, true )
		|| ! in_array( $status, $statuses, true ) ) {
		throw new RuntimeException( 'Fixed receipt vocabulary refused.' );
	}
	$head = get_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION, false );
	if ( ! is_array( $head ) || ! isset( $head['deployment_id'], $head['sequence'], $head['receipt_sha256'] ) ) {
		throw new RuntimeException( 'Fixed receipt head is unavailable.' );
	}
	$sequence = (int) $head['sequence'] + 1;
	$record = array(
		'schema_version' => 1,
		'deployment_id' => (string) $head['deployment_id'],
		'sequence' => $sequence,
		'utc' => gmdate( 'c' ),
		'spec_sha256' => FRPDEPOT_FQJ_SPEC_SHA256,
		'operation' => $operation,
		'artifact' => $artifact,
		'artifact_id' => $artifact_id,
		'before_sha256' => (string) $before_sha256,
		'after_sha256' => (string) $after_sha256,
		'status' => $status,
		'previous_receipt_sha256' => (string) $head['receipt_sha256'],
		'receipt_sha256' => '',
	);
	$record['receipt_sha256'] = frpdepot_fqj_hash( $record );
	$option = frpdepot_fqj_receipt_option( $head['deployment_id'], $sequence );
	if ( ! add_option( $option, $record, '', false ) ) {
		throw new RuntimeException( 'Immutable receipt append refused.' );
	}
	$new_head = array(
		'deployment_id' => (string) $head['deployment_id'],
		'sequence' => $sequence,
		'receipt_sha256' => $record['receipt_sha256'],
	);
	if ( ! update_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION, $new_head, false ) ) {
		if ( ! delete_option( $option ) || false !== get_option( $option, false ) ) {
			throw new RuntimeException( 'Receipt head update failed and orphan cleanup was not verified.' );
		}
		throw new RuntimeException( 'Receipt head update was not verified.' );
	}
	return $record;
}

function frpdepot_fqj_verify_receipt_chain() {
	$head = get_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION, false );
	if ( ! is_array( $head ) || array_keys( $head ) !== array( 'deployment_id', 'sequence', 'receipt_sha256' )
		|| (int) $head['sequence'] < 0 || ! preg_match( '/^[0-9a-f]{32}$/', (string) $head['deployment_id'] )
		|| ! preg_match( '/^[0-9a-f]{64}$/', (string) $head['receipt_sha256'] ) ) {
		return false;
	}
	$record_keys = array( 'schema_version', 'deployment_id', 'sequence', 'utc', 'spec_sha256', 'operation',
		'artifact', 'artifact_id', 'before_sha256', 'after_sha256', 'status', 'previous_receipt_sha256', 'receipt_sha256' );
	$operations = array( 'backup', 'create', 'replace', 'verify', 'restore', 'delete', 'finalize' );
	$artifacts = array( 'plugin', 'form', 'quote_page', 'contact_faq', 'route', 'journey' );
	$statuses = array( 'OK', 'FAILED_CLOSED', 'INDETERMINATE', 'ROLLBACK_BLOCKED_DRIFT', 'ROLLED_BACK' );
	$previous = str_repeat( '0', 64 );
	for ( $sequence = 1; $sequence <= (int) $head['sequence']; $sequence++ ) {
		$record = get_option( frpdepot_fqj_receipt_option( $head['deployment_id'], $sequence ), false );
		if ( ! is_array( $record ) || array_keys( $record ) !== $record_keys
			|| 1 !== (int) $record['schema_version'] || (string) $record['deployment_id'] !== (string) $head['deployment_id']
			|| (int) $record['sequence'] !== $sequence || '' === (string) $record['utc']
			|| FRPDEPOT_FQJ_SPEC_SHA256 !== (string) $record['spec_sha256']
			|| ! in_array( (string) $record['operation'], $operations, true )
			|| ! in_array( (string) $record['artifact'], $artifacts, true )
			|| ! in_array( (string) $record['status'], $statuses, true )
			|| ! preg_match( '/^[0-9a-f]{64}$/', (string) $record['before_sha256'] )
			|| ! preg_match( '/^[0-9a-f]{64}$/', (string) $record['after_sha256'] )
			|| ! preg_match( '/^[0-9a-f]{64}$/', (string) $record['receipt_sha256'] )
			|| (string) $record['previous_receipt_sha256'] !== $previous ) {
			return false;
		}
		$claimed = (string) $record['receipt_sha256'];
		$record['receipt_sha256'] = '';
		if ( ! hash_equals( $claimed, frpdepot_fqj_hash( $record ) ) ) {
			return false;
		}
		$previous = $claimed;
	}
	if ( false !== get_option( frpdepot_fqj_receipt_option( $head['deployment_id'], (int) $head['sequence'] + 1 ), false ) ) {
		return false;
	}
	return hash_equals( (string) $head['receipt_sha256'], $previous );
}

function frpdepot_fqj_route_projection( $notification ) {
	if ( ! is_array( $notification ) ) {
		throw new RuntimeException( 'Fixed notification route verification refused.' );
	}
	$keys = array( 'to', 'toType', 'routing', 'bcc', 'from', 'fromName', 'replyTo' );
	foreach ( $keys as $key ) {
		if ( ! array_key_exists( $key, $notification ) ) {
			throw new RuntimeException( 'Fixed notification route verification refused.' );
		}
	}
	$route = array_intersect_key( $notification, array_fill_keys( $keys, true ) );
	$route = array_replace( array_fill_keys( $keys, null ), $route );
	if ( ! is_string( $route['to'] ) || ! is_string( $route['toType'] )
		|| ! is_string( $route['bcc'] ) ) {
		throw new RuntimeException( 'Fixed notification route verification refused.' );
	}
	// Gravity Forms stores the unused routing property as null for its normal
	// fixed-email mode.  It may also normalize that same empty state to an empty
	// array.  Non-empty conditional routing remains outside this commission.
	if ( null !== $route['routing']
		&& ( ! is_array( $route['routing'] ) || count( $route['routing'] ) > 0 ) ) {
		throw new RuntimeException( 'Fixed notification route verification refused.' );
	}
	$has_to = isset( $route['to'] ) && is_string( $route['to'] ) && '' !== trim( $route['to'] );
	if ( 'email' !== $route['toType'] || ! $has_to ) {
		throw new RuntimeException( 'Fixed notification route verification refused.' );
	}
	foreach ( array( 'from', 'fromName', 'replyTo' ) as $required ) {
		if ( ! isset( $route[ $required ] ) || ! is_string( $route[ $required ] ) ) {
			throw new RuntimeException( 'Fixed notification route verification refused.' );
		}
	}
	return $route;
}

function frpdepot_fqj_source_route() {
	$source = GFAPI::get_form( FRPDEPOT_FQJ_SOURCE_FORM_ID );
	if ( ! is_array( $source ) || ! isset( $source['title'] ) || 'Contact' !== (string) $source['title'] ) {
		throw new RuntimeException( 'Fixed source form verification refused.' );
	}
	$matches = array();
	$notifications = isset( $source['notifications'] ) && is_array( $source['notifications'] ) ? $source['notifications'] : array();
	foreach ( $notifications as $notification ) {
		if ( is_array( $notification ) && isset( $notification['name'] )
			&& 'Admin Notification' === (string) $notification['name']
			&& ( ! isset( $notification['isActive'] ) || (bool) $notification['isActive'] ) ) {
			$matches[] = $notification;
		}
	}
	if ( 1 !== count( $matches ) ) {
		throw new RuntimeException( 'Fixed source notification verification refused.' );
	}
	return frpdepot_fqj_route_projection( $matches[0] );
}

function frpdepot_fqj_field( $id, $type, $label, $required, $max_length = 0 ) {
	$field = array(
		'id' => (int) $id,
		'type' => $type,
		'label' => $label,
		'isRequired' => (bool) $required,
		'visibility' => 'visible',
	);
	if ( $max_length > 0 ) {
		$field['maxLength'] = (int) $max_length;
	}
	return $field;
}

function frpdepot_fqj_upload_limit_mb() {
	$bytes = function_exists( 'wp_max_upload_size' ) ? (int) wp_max_upload_size() : 0;
	if ( $bytes < 1048576 ) {
		throw new RuntimeException( 'Site upload limit could not be verified.' );
	}
	return min( 10, (int) floor( $bytes / 1048576 ) );
}

function frpdepot_fqj_notification_definition( $route ) {
	$notification = $route;
	$notification['id'] = 'frpdepot_fqj_admin_notification';
	$notification['name'] = 'Admin Notification';
	$notification['event'] = 'form_submission';
	$notification['isActive'] = true;
	$notification['subject'] = 'New product and freight quote request';
	$notification['message'] = "First name: {First name:1}\nLast name: {Last name:2}\nEmail: {Email:3}\nCompany: {Company:4}\nPhone: {Phone:5}\nProduct: {Product:6}\nSize: {Size:7}\nPressure rating: {Pressure rating:8}\nResin type: {Resin type:9}\nQuantity: {Quantity:10}\nDelivery country: {Delivery country:11}\nDelivery postal/ZIP code: {Delivery postal/ZIP code:12}\nApplication or order notes: {Application or order notes:13}\nRequired delivery date: {Required delivery date:14}\nDrawing/specification upload: {Drawing/specification upload:15}\nMarketing subscription: {Marketing subscription:16}\nProduct URL: {Product URL:17}\nProduct ID(s): {Product ID(s):18}\nVariation ID(s): {Variation ID(s):19}\nSource page: {Source page:20}\nCart line projection: {Cart line projection:21}";
	// The source form's reply merge tag belongs to its field IDs. The dedicated
	// form always uses its own fixed email field instead of copying that reference.
	$notification['replyTo'] = '{Email:3}';
	$notification['disableAutoformat'] = false;
	$notification['enableAttachments'] = true;
	return $notification;
}

function frpdepot_fqj_form_definition( $route ) {
	$fields = array(
		frpdepot_fqj_field( 1, 'text', 'First name', true, 80 ),
		frpdepot_fqj_field( 2, 'text', 'Last name', true, 80 ),
		frpdepot_fqj_field( 3, 'email', 'Email', true, 254 ),
		frpdepot_fqj_field( 4, 'text', 'Company', true, 160 ),
		frpdepot_fqj_field( 5, 'phone', 'Phone', false, 40 ),
		frpdepot_fqj_field( 6, 'text', 'Product', true, 500 ),
		frpdepot_fqj_field( 7, 'text', 'Size', true, 500 ),
		frpdepot_fqj_field( 8, 'text', 'Pressure rating', true, 500 ),
		frpdepot_fqj_field( 9, 'text', 'Resin type', true, 500 ),
		frpdepot_fqj_field( 10, 'text', 'Quantity', true, 200 ),
		frpdepot_fqj_field( 11, 'select', 'Delivery country', true ),
		frpdepot_fqj_field( 12, 'text', 'Delivery postal/ZIP code', true, 20 ),
		frpdepot_fqj_field( 13, 'textarea', 'Application or order notes', true, 4000 ),
		frpdepot_fqj_field( 14, 'date', 'Required delivery date', false ),
		frpdepot_fqj_field( 15, 'fileupload', 'Drawing/specification upload', false ),
		frpdepot_fqj_field( 16, 'checkbox', 'Marketing subscription', false ),
		frpdepot_fqj_field( 17, 'hidden', 'Product URL', false ),
		frpdepot_fqj_field( 18, 'hidden', 'Product ID(s)', false ),
		frpdepot_fqj_field( 19, 'hidden', 'Variation ID(s)', false ),
		frpdepot_fqj_field( 20, 'hidden', 'Source page', false ),
		frpdepot_fqj_field( 21, 'hidden', 'Cart line projection', false ),
	);
	foreach ( $fields as &$field ) {
		if ( (int) $field['id'] >= 17 ) {
			$field['visibility'] = 'hidden';
		}
	}
	unset( $field );
	$fields[10]['choices'] = array(
		array( 'text' => 'Canada', 'value' => 'CA' ),
		array( 'text' => 'United States', 'value' => 'US' ),
	);
	$fields[14]['allowedExtensions'] = 'pdf,jpg,jpeg,png';
	$fields[14]['maxFileSize'] = frpdepot_fqj_upload_limit_mb();
	$fields[14]['multipleFiles'] = false;
	$fields[15]['choices'] = array(
		array( 'text' => 'I would like to receive occasional FRP Depot marketing updates.', 'value' => 'yes', 'isSelected' => false ),
	);
	$fields[15]['inputs'] = array(
		array( 'id' => '16.1', 'label' => 'I would like to receive occasional FRP Depot marketing updates.', 'name' => '' ),
	);
	$notification = frpdepot_fqj_notification_definition( $route );
	return array(
		'title' => FRPDEPOT_FQJ_FORM_TITLE,
		'description' => FRPDEPOT_FQJ_FORM_MARKER,
		'cssClass' => FRPDEPOT_FQJ_FORM_ADMIN_MARKER,
		'labelPlacement' => 'top_label',
		'button' => array( 'type' => 'text', 'text' => 'Request Quote' ),
		'fields' => $fields,
		'notifications' => array( 'frpdepot_fqj_admin_notification' => $notification ),
		'confirmations' => array(
			'frpdepot_fqj_confirmation' => array(
				'id' => 'frpdepot_fqj_confirmation',
				'name' => 'Default Confirmation',
				'isDefault' => true,
				'type' => 'message',
				'message' => FRPDEPOT_FQJ_CONFIRMATION,
				'disableAutoformat' => false,
			),
		),
	);
}

function frpdepot_fqj_value( $item, $key, $default = null ) {
	if ( is_object( $item ) ) {
		return isset( $item->{$key} ) ? $item->{$key} : $default;
	}
	return is_array( $item ) && array_key_exists( $key, $item ) ? $item[ $key ] : $default;
}

function frpdepot_fqj_form_projection( $form ) {
	if ( ! is_array( $form ) ) {
		return null;
	}
	$fields = array();
	foreach ( isset( $form['fields'] ) && is_array( $form['fields'] ) ? $form['fields'] : array() as $field ) {
		$choices = array();
		foreach ( (array) frpdepot_fqj_value( $field, 'choices', array() ) as $choice ) {
			$choices[] = array(
				'text' => (string) frpdepot_fqj_value( $choice, 'text', '' ),
				'value' => (string) frpdepot_fqj_value( $choice, 'value', '' ),
				'isSelected' => (bool) frpdepot_fqj_value( $choice, 'isSelected', false ),
			);
		}
		$inputs = array();
		foreach ( (array) frpdepot_fqj_value( $field, 'inputs', array() ) as $input ) {
			$inputs[] = array(
				'id' => (string) frpdepot_fqj_value( $input, 'id', '' ),
				'label' => (string) frpdepot_fqj_value( $input, 'label', '' ),
				'name' => (string) frpdepot_fqj_value( $input, 'name', '' ),
			);
		}
		$fields[] = array(
			'id' => (int) frpdepot_fqj_value( $field, 'id', 0 ),
			'type' => (string) frpdepot_fqj_value( $field, 'type', '' ),
			'label' => (string) frpdepot_fqj_value( $field, 'label', '' ),
			'isRequired' => (bool) frpdepot_fqj_value( $field, 'isRequired', false ),
			'visibility' => (string) frpdepot_fqj_value( $field, 'visibility', '' ),
			'maxLength' => (int) frpdepot_fqj_value( $field, 'maxLength', 0 ),
			'choices' => $choices,
			'inputs' => $inputs,
			'allowedExtensions' => (string) frpdepot_fqj_value( $field, 'allowedExtensions', '' ),
			'maxFileSize' => (int) frpdepot_fqj_value( $field, 'maxFileSize', 0 ),
			'multipleFiles' => (bool) frpdepot_fqj_value( $field, 'multipleFiles', false ),
		);
	}
	$notifications = isset( $form['notifications'] ) && is_array( $form['notifications'] ) ? array_values( $form['notifications'] ) : array();
	$notification = 1 === count( $notifications ) ? $notifications[0] : null;
	$route = null;
	if ( is_array( $notification ) ) {
		try {
			$route = frpdepot_fqj_route_projection( $notification );
		} catch ( Throwable $error ) {
			$route = null; // Drifted notification definitions fail closed without exposing route values.
		}
	}
	$confirmations = isset( $form['confirmations'] ) && is_array( $form['confirmations'] ) ? array_values( $form['confirmations'] ) : array();
	$confirmation = 1 === count( $confirmations ) ? $confirmations[0] : array();
	return array(
		'title' => isset( $form['title'] ) ? (string) $form['title'] : '',
		'description' => isset( $form['description'] ) ? (string) $form['description'] : '',
		'admin_ownership_marker' => isset( $form['cssClass'] ) ? (string) $form['cssClass'] : '',
		'label_placement' => isset( $form['labelPlacement'] ) ? (string) $form['labelPlacement'] : '',
		'button_type' => isset( $form['button']['type'] ) ? (string) $form['button']['type'] : '',
		'button_text' => isset( $form['button']['text'] ) ? (string) $form['button']['text'] : '',
		'fields' => $fields,
		'notification_count' => count( $notifications ),
		'notification_id' => is_array( $notification ) && isset( $notification['id'] ) ? (string) $notification['id'] : '',
		'notification_name' => is_array( $notification ) && isset( $notification['name'] ) ? (string) $notification['name'] : '',
		'notification_event' => is_array( $notification ) && isset( $notification['event'] ) ? (string) $notification['event'] : '',
		'notification_active' => is_array( $notification ) && ( ! isset( $notification['isActive'] ) || (bool) $notification['isActive'] ),
		'notification_subject' => is_array( $notification ) && isset( $notification['subject'] ) ? (string) $notification['subject'] : '',
		'notification_message' => is_array( $notification ) && isset( $notification['message'] ) ? (string) $notification['message'] : '',
		'route_sha256' => null === $route ? '' : frpdepot_fqj_hash( $route ),
		'confirmation_count' => count( $confirmations ),
		'confirmation_id' => isset( $confirmation['id'] ) ? (string) $confirmation['id'] : '',
		'confirmation_name' => isset( $confirmation['name'] ) ? (string) $confirmation['name'] : '',
		'confirmation_default' => ! empty( $confirmation['isDefault'] ),
		'confirmation_type' => isset( $confirmation['type'] ) ? (string) $confirmation['type'] : '',
		'confirmation_message' => isset( $confirmation['message'] ) ? (string) $confirmation['message'] : '',
		'confirmation_disable_autoformat' => ! empty( $confirmation['disableAutoformat'] ),
	);
}

function frpdepot_fqj_page_content( $form_id ) {
	return FRPDEPOT_FQJ_PAGE_MARKER . "\n<p>" . esc_html( FRPDEPOT_FQJ_PAGE_INTRO ) . "</p>\n[gravityform id=\""
		. (int) $form_id . "\" title=\"false\" description=\"false\" ajax=\"true\"]";
}

function frpdepot_fqj_page_projection( $page ) {
	return $page ? array(
		'ID' => (int) $page->ID,
		'post_type' => (string) $page->post_type,
		'post_title' => (string) $page->post_title,
		'post_name' => (string) $page->post_name,
		'post_content' => (string) $page->post_content,
		'post_status' => (string) $page->post_status,
	) : null;
}

function frpdepot_fqj_form_has_admin_ownership( $form ) {
	if ( ! is_array( $form )
		|| FRPDEPOT_FQJ_FORM_TITLE !== (string) frpdepot_fqj_value( $form, 'title', '' )
		|| FRPDEPOT_FQJ_FORM_MARKER !== (string) frpdepot_fqj_value( $form, 'description', '' )
		|| FRPDEPOT_FQJ_FORM_ADMIN_MARKER !== (string) frpdepot_fqj_value( $form, 'cssClass', '' ) ) {
		return false;
	}
	$projection = frpdepot_fqj_form_projection( $form );
	if ( ! is_array( $projection ) || 21 !== count( $projection['fields'] ) ) {
		return false;
	}
	return range( 1, 21 ) === array_column( $projection['fields'], 'id' )
		&& array( 'text','text','email','text','phone','text','text','text','text','text','select','text','textarea','date','fileupload','checkbox','hidden','hidden','hidden','hidden','hidden' )
			=== array_column( $projection['fields'], 'type' )
		&& 1 === (int) $projection['notification_count']
		&& 'frpdepot_fqj_admin_notification' === (string) $projection['notification_id']
		&& 'Admin Notification' === (string) $projection['notification_name']
		&& true === (bool) $projection['notification_active']
		&& 1 === (int) $projection['confirmation_count']
		&& 'frpdepot_fqj_confirmation' === (string) $projection['confirmation_id'];
}

function frpdepot_fqj_form_field_signature_sha256( $form ) {
	$projection = frpdepot_fqj_form_projection( $form );
	return is_array( $projection ) ? frpdepot_fqj_hash( $projection['fields'] ) : '';
}

function frpdepot_fqj_form_collisions() {
	$title = 0;
	$marker = 0;
	$admin_marker = 0;
	foreach ( (array) GFAPI::get_forms( null, null, 'title', 'ASC' ) as $summary ) {
		$id = (int) frpdepot_fqj_value( $summary, 'id', 0 );
		$form = $id > 0 ? GFAPI::get_form( $id ) : null;
		if ( is_array( $form ) && isset( $form['title'] ) && FRPDEPOT_FQJ_FORM_TITLE === (string) $form['title'] ) {
			$title++;
		}
		if ( is_array( $form ) && isset( $form['description'] ) && FRPDEPOT_FQJ_FORM_MARKER === (string) $form['description'] ) {
			$marker++;
		}
		if ( is_array( $form ) && FRPDEPOT_FQJ_FORM_ADMIN_MARKER === (string) frpdepot_fqj_value( $form, 'cssClass', '' ) ) {
			$admin_marker++;
		}
	}
	return array( 'title' => $title, 'marker' => $marker, 'admin_marker' => $admin_marker );
}

/**
 * Search every form and resolve exactly one safe action.
 *
 * An existing form is reusable only when all three identity values and the
 * complete route-aware form projection match. Any partial identity match, one
 * exact match plus a partial match, or more than one exact match is ambiguous
 * and refuses before a business write.
 */
function frpdepot_fqj_resolve_owned_form( $route ) {
	$expected_projection = frpdepot_fqj_form_projection( frpdepot_fqj_form_definition( $route ) );
	$expected_sha256 = frpdepot_fqj_hash( $expected_projection );
	$expected_field_signature_sha256 = frpdepot_fqj_hash( $expected_projection['fields'] );
	$counts = array( 'title' => 0, 'marker' => 0, 'admin_marker' => 0 );
	$exact = array();
	$partial = array();
	foreach ( (array) GFAPI::get_forms( null, null, 'title', 'ASC' ) as $summary ) {
		$id = (int) frpdepot_fqj_value( $summary, 'id', 0 );
		$form = $id > 0 ? GFAPI::get_form( $id ) : null;
		if ( ! is_array( $form ) ) {
			continue;
		}
		$title_match = FRPDEPOT_FQJ_FORM_TITLE === (string) frpdepot_fqj_value( $form, 'title', '' );
		$marker_match = FRPDEPOT_FQJ_FORM_MARKER === (string) frpdepot_fqj_value( $form, 'description', '' );
		$admin_match = FRPDEPOT_FQJ_FORM_ADMIN_MARKER === (string) frpdepot_fqj_value( $form, 'cssClass', '' );
		$field_signature_match = hash_equals( $expected_field_signature_sha256, frpdepot_fqj_form_field_signature_sha256( $form ) );
		$counts['title'] += $title_match ? 1 : 0;
		$counts['marker'] += $marker_match ? 1 : 0;
		$counts['admin_marker'] += $admin_match ? 1 : 0;
		if ( ! $title_match && ! $marker_match && ! $admin_match && ! $field_signature_match ) {
			continue;
		}
		$projection = frpdepot_fqj_form_projection( $form );
		$complete = $title_match && $marker_match && $admin_match && $field_signature_match
			&& frpdepot_fqj_form_has_admin_ownership( $form ) && is_array( $projection )
			&& hash_equals( $expected_sha256, frpdepot_fqj_hash( $projection ) );
		if ( $complete ) {
			$exact[] = $id;
		} else {
			$partial[] = $id;
		}
	}
	if ( count( $exact ) > 1 || count( $partial ) > 0 ) {
		throw new RuntimeException( 'Fixed form ownership search found a partial or multiple match.' );
	}
	return array(
		'action' => 1 === count( $exact ) ? 'reuse' : 'create',
		'form_id' => 1 === count( $exact ) ? (int) $exact[0] : 0,
		'form_sha256' => 1 === count( $exact ) ? $expected_sha256 : frpdepot_fqj_hash( null ),
		'expected_sha256' => $expected_sha256,
		'expected_field_signature_sha256' => $expected_field_signature_sha256,
		'collisions' => $counts,
		'exact_match_count' => count( $exact ),
		'partial_match_count' => count( $partial ),
	);
}

function frpdepot_fqj_page_collisions() {
	$slug = get_page_by_path( FRPDEPOT_FQJ_PAGE_SLUG, OBJECT, 'page' ) ? 1 : 0;
	$marker = 0;
	foreach ( (array) get_pages( array( 'post_type' => 'page', 'post_status' => array( 'publish', 'draft', 'private', 'pending', 'future', 'trash' ) ) ) as $page ) {
		if ( false !== strpos( (string) $page->post_content, FRPDEPOT_FQJ_PAGE_MARKER ) ) {
			$marker++;
		}
	}
	return array( 'slug' => $slug, 'marker' => $marker );
}

function frpdepot_fqj_backup_record( $deployment_id, $artifact, $existed, $artifact_id, $before_sha256, $payload ) {
	return array(
		'schema_version' => 1,
		'deployment_id' => $deployment_id,
		'artifact' => $artifact,
		'captured_utc' => gmdate( 'c' ),
		'spec_sha256' => FRPDEPOT_FQJ_SPEC_SHA256,
		'existed' => (bool) $existed,
		'artifact_id' => $artifact_id,
		'before_sha256' => $before_sha256,
		'payload' => $payload,
	);
}

function frpdepot_fqj_capture_all_backups() {
	foreach ( frpdepot_fqj_backup_options() as $option ) {
		if ( false !== get_option( $option, false ) ) {
			throw new RuntimeException( 'Immutable backup already exists.' );
		}
	}
	if ( false !== get_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION, false ) ) {
		throw new RuntimeException( 'Receipt chain already exists.' );
	}
	$route = frpdepot_fqj_source_route();
	$route_sha256 = frpdepot_fqj_hash( $route );
	$form_resolution = frpdepot_fqj_resolve_owned_form( $route );
	$page_counts = frpdepot_fqj_page_collisions();
	$contact = get_post( FRPDEPOT_FQJ_CONTACT_ID );
	$form_create_ready = 'create' === $form_resolution['action']
		&& array( 'title' => 0, 'marker' => 0, 'admin_marker' => 0 ) === $form_resolution['collisions']
		&& 0 === (int) $form_resolution['exact_match_count'] && 0 === (int) $form_resolution['partial_match_count'];
	$form_reuse_ready = 'reuse' === $form_resolution['action'] && (int) $form_resolution['form_id'] > 0
		&& array( 'title' => 1, 'marker' => 1, 'admin_marker' => 1 ) === $form_resolution['collisions']
		&& 1 === (int) $form_resolution['exact_match_count'] && 0 === (int) $form_resolution['partial_match_count'];
	if ( ( ! $form_create_ready && ! $form_reuse_ready )
		|| $page_counts !== array( 'slug' => 0, 'marker' => 0 )
		|| ! $contact || 'page' !== (string) $contact->post_type
		|| 1 !== substr_count( (string) $contact->post_content, FRPDEPOT_FQJ_CONTACT_OLD ) ) {
		throw new RuntimeException( 'Fixed business-artifact precheck refused.' );
	}
	$deployment_id = function_exists( 'wp_generate_uuid4' )
		? str_replace( '-', '', strtolower( wp_generate_uuid4() ) )
		: substr( hash( 'sha256', gmdate( 'c' ) . mt_rand() ), 0, 32 );
	$null_hash = frpdepot_fqj_hash( null );
	$plugin_payload = array(
		'plugin_file' => 'frpdepot-freight-checkout-guard/frpdepot-freight-checkout-guard.php',
		'before_version' => '1.0.1',
		'rollback_artifact' => 'frpdepot-freight-checkout-guard.zip',
		'rollback_sha256' => 'fe6fa440ea3a08169bf568ae0fbb06f666ad71c1110e58f9b2b6bb0acc8be6cb',
		'local_plan_fingerprint_required' => true,
	);
	$records = array(
		'plugin' => frpdepot_fqj_backup_record( $deployment_id, 'plugin', true,
			'frpdepot-freight-checkout-guard/frpdepot-freight-checkout-guard.php', frpdepot_fqj_hash( $plugin_payload ), $plugin_payload ),
		'form' => frpdepot_fqj_backup_record( $deployment_id, 'form', $form_reuse_ready,
			(int) $form_resolution['form_id'], (string) $form_resolution['form_sha256'], array(
				'ownership_action' => (string) $form_resolution['action'],
				'title_lookup_count' => (int) $form_resolution['collisions']['title'],
				'marker_lookup_count' => (int) $form_resolution['collisions']['marker'],
				'admin_marker_lookup_count' => (int) $form_resolution['collisions']['admin_marker'],
				'complete_match_count' => (int) $form_resolution['exact_match_count'],
				'partial_match_count' => (int) $form_resolution['partial_match_count'],
				'expected_form_sha256' => (string) $form_resolution['expected_sha256'],
				'expected_field_signature_sha256' => (string) $form_resolution['expected_field_signature_sha256'],
				'canonical_null_sha256' => $null_hash,
			) ),
		'quote_page' => frpdepot_fqj_backup_record( $deployment_id, 'quote_page', false, 0, $null_hash,
			array( 'slug_lookup_count' => 0, 'marker_lookup_count' => 0, 'canonical_null_sha256' => $null_hash ) ),
		'contact_faq' => frpdepot_fqj_backup_record( $deployment_id, 'contact_faq', true, FRPDEPOT_FQJ_CONTACT_ID,
			hash( 'sha256', (string) $contact->post_content ), array(
				'ID' => FRPDEPOT_FQJ_CONTACT_ID,
				'post_type' => 'page',
				'post_content' => (string) $contact->post_content,
				'old_sentence_count' => 1,
			) ),
		'route' => frpdepot_fqj_backup_record( $deployment_id, 'route', true, FRPDEPOT_FQJ_SOURCE_FORM_ID,
			$route_sha256, array(
				'source_form_id' => FRPDEPOT_FQJ_SOURCE_FORM_ID,
				'source_form_title_match' => true,
				'source_notification_name_match' => true,
				'route_sha256' => $route_sha256,
			) ),
	);
	if ( ! add_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION, array(
		'deployment_id' => $deployment_id,
		'sequence' => 0,
		'receipt_sha256' => str_repeat( '0', 64 ),
	), '', false ) ) {
		throw new RuntimeException( 'Receipt chain initialization refused.' );
	}
	$options = frpdepot_fqj_backup_options();
	foreach ( array( 'plugin', 'form', 'quote_page', 'contact_faq', 'route' ) as $artifact ) {
		if ( ! add_option( $options[ $artifact ], $records[ $artifact ], '', false )
			|| get_option( $options[ $artifact ], false ) !== $records[ $artifact ] ) {
			throw new RuntimeException( 'Immutable backup capture refused.' );
		}
		frpdepot_fqj_append_receipt( 'backup', $artifact, $records[ $artifact ]['artifact_id'],
			$records[ $artifact ]['before_sha256'], frpdepot_fqj_hash( $records[ $artifact ] ), 'OK' );
	}
	return array(
		'deployment_id' => $deployment_id,
		'route' => $route,
		'route_sha256' => $route_sha256,
		'form_resolution' => $form_resolution,
	);
}

function frpdepot_fqj_cleanup_prewrite_options() {
	$state = get_option( FRPDEPOT_FQJ_STATE_OPTION, false );
	if ( is_array( $state ) && ( 0 !== (int) $state['form_id'] || 0 !== (int) $state['page_id']
		|| '' !== (string) $state['contact_after_sha256'] ) ) {
		throw new RuntimeException( 'Pre-write cleanup refused because business-artifact state exists.' );
	}
	$head = get_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION, false );
	if ( is_array( $head ) ) {
		if ( ! isset( $head['deployment_id'], $head['sequence'] ) || (int) $head['sequence'] < 0
			|| (int) $head['sequence'] > 100 || ! preg_match( '/^[0-9a-f]{32}$/', (string) $head['deployment_id'] ) ) {
			throw new RuntimeException( 'Pre-write receipt cleanup refused.' );
		}
		$orphan = frpdepot_fqj_receipt_option( $head['deployment_id'], (int) $head['sequence'] + 1 );
		if ( false !== get_option( $orphan, false )
			&& ( ! delete_option( $orphan ) || false !== get_option( $orphan, false ) ) ) {
			throw new RuntimeException( 'Pre-write orphan receipt cleanup was not verified.' );
		}
		for ( $sequence = 1; $sequence <= (int) $head['sequence']; $sequence++ ) {
			$option = frpdepot_fqj_receipt_option( $head['deployment_id'], $sequence );
			if ( false !== get_option( $option, false ) && ! delete_option( $option ) ) {
				throw new RuntimeException( 'Pre-write receipt cleanup was not verified.' );
			}
		}
	}
	foreach ( frpdepot_fqj_backup_options() as $option ) {
		if ( false !== get_option( $option, false ) && ! delete_option( $option ) ) {
			throw new RuntimeException( 'Pre-write backup cleanup was not verified.' );
		}
	}
	foreach ( array( FRPDEPOT_FQJ_STATE_OPTION, FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION ) as $option ) {
		if ( false !== get_option( $option, false ) && ! delete_option( $option ) ) {
			throw new RuntimeException( 'Pre-write state cleanup was not verified.' );
		}
	}
	foreach ( array_merge( array_values( frpdepot_fqj_backup_options() ),
		array( FRPDEPOT_FQJ_STATE_OPTION, FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION ) ) as $option ) {
		if ( false !== get_option( $option, false ) ) {
			throw new RuntimeException( 'Pre-write cleanup readback refused.' );
		}
	}
}

function frpdepot_fqj_state() {
	$state = get_option( FRPDEPOT_FQJ_STATE_OPTION, false );
	return is_array( $state ) ? $state : null;
}

function frpdepot_fqj_write_state( $state, $create = false ) {
	$result = $create
		? add_option( FRPDEPOT_FQJ_STATE_OPTION, $state, '', false )
		: update_option( FRPDEPOT_FQJ_STATE_OPTION, $state, false );
	if ( ! $result || get_option( FRPDEPOT_FQJ_STATE_OPTION, false ) !== $state ) {
		throw new RuntimeException( 'Fixed state write was not verified.' );
	}
}

function frpdepot_fqj_commissioned_activation_transaction() {
	if ( ! class_exists( 'GFAPI' ) ) {
		throw new RuntimeException( 'Gravity Forms is required for the fixed transaction.' );
	}
	$existing_state = frpdepot_fqj_state();
	if ( is_array( $existing_state ) && 'applied' === (string) $existing_state['status'] ) {
		return; // Ordinary reactivation performs no business mutation.
	}
	if ( null !== $existing_state ) {
		throw new RuntimeException( 'The fixed transaction is already closed.' );
	}
	if ( ! add_option( FRPDEPOT_FQJ_LOCK_OPTION, array( 'utc' => gmdate( 'c' ), 'spec_sha256' => FRPDEPOT_FQJ_SPEC_SHA256 ), '', false ) ) {
		throw new RuntimeException( 'The fixed activation transaction is locked.' );
	}
	$business_write_attempted = false;
	$pending_created_form_id = 0;
	$pending_created_page_id = 0;
	try {
		$captured = frpdepot_fqj_capture_all_backups();
		$state = array(
			'schema_version' => 1,
			'deployment_id' => $captured['deployment_id'],
			'spec_sha256' => FRPDEPOT_FQJ_SPEC_SHA256,
			'status' => 'applying',
			'form_id' => 0,
			'form_after_sha256' => '',
			'page_id' => 0,
			'page_after_sha256' => '',
			'contact_id' => FRPDEPOT_FQJ_CONTACT_ID,
			'contact_after_sha256' => '',
			'route_sha256' => $captured['route_sha256'],
			'apply_receipt_head_sha256' => str_repeat( '0', 64 ),
			'rollback_blocked_artifact' => '',
		);
		frpdepot_fqj_write_state( $state, true );

		// Re-read every mutable prerequisite after backups and immediately before
		// the first business write. The option lock serializes this transaction,
		// while these hashes/collision counts reject external drift.
		$route_now = frpdepot_fqj_source_route();
		$contact_now = get_post( FRPDEPOT_FQJ_CONTACT_ID );
		if ( frpdepot_fqj_resolve_owned_form( $route_now ) !== $captured['form_resolution']
			|| frpdepot_fqj_page_collisions() !== array( 'slug' => 0, 'marker' => 0 )
			|| ! hash_equals( $captured['route_sha256'], frpdepot_fqj_hash( $route_now ) )
			|| ! $contact_now || 'page' !== (string) $contact_now->post_type
			|| ! hash_equals(
				(string) get_option( FRPDEPOT_FQJ_BACKUP_CONTACT_OPTION )['before_sha256'],
				hash( 'sha256', (string) $contact_now->post_content )
			) ) {
			throw new RuntimeException( 'Fixed business-artifact pre-write drift refused.' );
		}

		$business_write_attempted = true;
		$expected_form_projection = frpdepot_fqj_form_projection( frpdepot_fqj_form_definition( $captured['route'] ) );
		$expected_form_sha256 = frpdepot_fqj_hash( $expected_form_projection );
		if ( ! hash_equals( $expected_form_sha256, (string) $captured['form_resolution']['expected_sha256'] ) ) {
			throw new RuntimeException( 'Fixed form definition drift refused.' );
		}
		if ( 'reuse' === (string) $captured['form_resolution']['action'] ) {
			$form_id = (int) $captured['form_resolution']['form_id'];
		} else {
			$form_result = GFAPI::add_form( frpdepot_fqj_form_definition( $captured['route'] ) );
			if ( is_wp_error( $form_result ) || ! is_numeric( $form_result ) || (int) $form_result <= 0 ) {
				throw new RuntimeException( 'Fixed form creation refused.' );
			}
			$form_id = (int) $form_result;
			$pending_created_form_id = $form_id;
		}
		$state['form_id'] = $form_id;
		$state['form_after_sha256'] = $expected_form_sha256;
		frpdepot_fqj_write_state( $state );
		if ( $pending_created_form_id === $form_id ) {
			$pending_created_form_id = 0;
			frpdepot_fqj_append_receipt( 'create', 'form', $form_id, frpdepot_fqj_hash( null ), $expected_form_sha256, 'INDETERMINATE' );
		}
		$form = GFAPI::get_form( $form_id );
		$form_projection = frpdepot_fqj_form_projection( $form );
		if ( ! frpdepot_fqj_form_has_admin_ownership( $form ) || null === $form_projection
			|| ! hash_equals( $expected_form_sha256, frpdepot_fqj_hash( $form_projection ) ) ) {
			throw new RuntimeException( 'Fixed form readback refused.' );
		}
		frpdepot_fqj_append_receipt( 'verify', 'form', $form_id, $expected_form_sha256, $expected_form_sha256, 'OK' );

		$expected_page_projection = array(
			'ID' => 0,
			'post_type' => 'page',
			'post_title' => FRPDEPOT_FQJ_PAGE_TITLE,
			'post_name' => FRPDEPOT_FQJ_PAGE_SLUG,
			'post_content' => frpdepot_fqj_page_content( $form_id ),
			'post_status' => 'publish',
		);
		$page_id = wp_insert_post( wp_slash( array(
			'post_type' => 'page',
			'post_status' => 'publish',
			'post_title' => FRPDEPOT_FQJ_PAGE_TITLE,
			'post_name' => FRPDEPOT_FQJ_PAGE_SLUG,
			'post_content' => frpdepot_fqj_page_content( $form_id ),
		) ), true );
		if ( is_wp_error( $page_id ) || (int) $page_id <= 0 ) {
			throw new RuntimeException( 'Fixed quote-page creation refused.' );
		}
		$page_id = (int) $page_id;
		$pending_created_page_id = $page_id;
		$expected_page_projection['ID'] = $page_id;
		$expected_page_sha256 = frpdepot_fqj_hash( $expected_page_projection );
		$state['page_id'] = $page_id;
		$state['page_after_sha256'] = $expected_page_sha256;
		frpdepot_fqj_write_state( $state );
		$pending_created_page_id = 0;
		frpdepot_fqj_append_receipt( 'create', 'quote_page', $page_id, frpdepot_fqj_hash( null ), $expected_page_sha256, 'INDETERMINATE' );
		$page = get_post( $page_id );
		$page_projection = frpdepot_fqj_page_projection( $page );
		if ( null === $page_projection || ! hash_equals( $expected_page_sha256, frpdepot_fqj_hash( $page_projection ) ) ) {
			throw new RuntimeException( 'Fixed quote-page readback refused.' );
		}
		frpdepot_fqj_append_receipt( 'verify', 'quote_page', $page_id, $expected_page_sha256, $expected_page_sha256, 'OK' );

		$contact_backup = get_option( FRPDEPOT_FQJ_BACKUP_CONTACT_OPTION, false );
		$contact = get_post( FRPDEPOT_FQJ_CONTACT_ID );
		$current_content = $contact ? (string) $contact->post_content : '';
		if ( ! is_array( $contact_backup ) || 'page' !== (string) $contact->post_type
			|| ! hash_equals( (string) $contact_backup['before_sha256'], hash( 'sha256', $current_content ) )
			|| 1 !== substr_count( $current_content, FRPDEPOT_FQJ_CONTACT_OLD ) ) {
			throw new RuntimeException( 'Contact pre-write drift refused.' );
		}
		$new_content = str_replace( FRPDEPOT_FQJ_CONTACT_OLD, FRPDEPOT_FQJ_CONTACT_NEW, $current_content, $replace_count );
		if ( 1 !== $replace_count ) {
			throw new RuntimeException( 'Contact replacement refused.' );
		}
		$expected_contact_sha256 = hash( 'sha256', $new_content );
		$state['contact_after_sha256'] = $expected_contact_sha256;
		frpdepot_fqj_write_state( $state );
		frpdepot_fqj_append_receipt( 'replace', 'contact_faq', FRPDEPOT_FQJ_CONTACT_ID,
			$contact_backup['before_sha256'], $expected_contact_sha256, 'INDETERMINATE' );
		$result = wp_update_post( wp_slash( array( 'ID' => FRPDEPOT_FQJ_CONTACT_ID, 'post_content' => $new_content ) ), true );
		if ( is_wp_error( $result ) || FRPDEPOT_FQJ_CONTACT_ID !== (int) $result ) {
			throw new RuntimeException( 'Contact replacement write refused.' );
		}
		$contact_after = get_post( FRPDEPOT_FQJ_CONTACT_ID );
		if ( ! $contact_after || 'page' !== (string) $contact_after->post_type
			|| (string) $contact_after->post_content !== $new_content
			|| 0 !== substr_count( $new_content, FRPDEPOT_FQJ_CONTACT_OLD )
			|| 1 !== substr_count( $new_content, FRPDEPOT_FQJ_CONTACT_NEW ) ) {
			throw new RuntimeException( 'Contact replacement readback refused.' );
		}
		frpdepot_fqj_append_receipt( 'verify', 'contact_faq', FRPDEPOT_FQJ_CONTACT_ID,
			$expected_contact_sha256, $expected_contact_sha256, 'OK' );

		$state['status'] = 'applied';
		frpdepot_fqj_write_state( $state );
		frpdepot_fqj_append_receipt( 'finalize', 'journey', $captured['deployment_id'],
			frpdepot_fqj_hash( null ), frpdepot_fqj_hash( $state ), 'OK' );
		$final_head = get_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION, false );
		if ( ! is_array( $final_head ) || ! isset( $final_head['receipt_sha256'] ) ) {
			throw new RuntimeException( 'Final receipt head verification refused.' );
		}
		$state['apply_receipt_head_sha256'] = (string) $final_head['receipt_sha256'];
		frpdepot_fqj_write_state( $state );
	} catch ( Throwable $error ) {
		$failed_closed = false;
		$untracked_cleanup_verified = true;
		$persisted_state = frpdepot_fqj_state();
		if ( $pending_created_form_id > 0 && is_array( $persisted_state )
			&& (int) $persisted_state['form_id'] === $pending_created_form_id ) {
			$pending_created_form_id = 0;
		}
		if ( $pending_created_page_id > 0 && is_array( $persisted_state )
			&& (int) $persisted_state['page_id'] === $pending_created_page_id ) {
			$pending_created_page_id = 0;
		}
		if ( $pending_created_page_id > 0 ) {
			$pending_page_id = $pending_created_page_id;
			$deleted_page = wp_delete_post( $pending_page_id, true );
			if ( ! $deleted_page || get_post( $pending_page_id )
				|| get_page_by_path( FRPDEPOT_FQJ_PAGE_SLUG, OBJECT, 'page' ) ) {
				$untracked_cleanup_verified = false;
			} else {
				$pending_created_page_id = 0;
			}
		}
		if ( $pending_created_form_id > 0 ) {
			$pending_form_id = $pending_created_form_id;
			$deleted_form = GFAPI::delete_form( $pending_form_id );
			if ( is_wp_error( $deleted_form ) || false === $deleted_form || GFAPI::get_form( $pending_form_id ) ) {
				$untracked_cleanup_verified = false;
			} else {
				$pending_created_form_id = 0;
			}
		}
		if ( $business_write_attempted && null !== frpdepot_fqj_state() ) {
			try {
				$rolled_back = frpdepot_fqj_rollback_transaction( false );
				$failed_closed = $untracked_cleanup_verified && is_array( $rolled_back )
					&& 'rolled_back' === (string) $rolled_back['status'];
			} catch ( Throwable $rollback_error ) {
				$state = frpdepot_fqj_state();
				if ( is_array( $state ) && 'rollback_blocked_drift' !== (string) $state['status'] ) {
					$state['status'] = 'indeterminate';
					update_option( FRPDEPOT_FQJ_STATE_OPTION, $state, false );
				}
			}
		} elseif ( ! $business_write_attempted ) {
			try {
				frpdepot_fqj_cleanup_prewrite_options();
				$failed_closed = $untracked_cleanup_verified;
			} catch ( Throwable $cleanup_error ) {
				$failed_closed = false;
			}
		}
		if ( ! $untracked_cleanup_verified ) {
			$failed_closed = false;
			$state = frpdepot_fqj_state();
			if ( is_array( $state ) && 'rollback_blocked_drift' !== (string) $state['status'] ) {
				$state['status'] = 'indeterminate';
				update_option( FRPDEPOT_FQJ_STATE_OPTION, $state, false );
			}
		}
		delete_option( FRPDEPOT_FQJ_LOCK_OPTION );
		if ( $failed_closed ) {
			return; // Keep v2 inspectable so the named deploy tool can restore the original binary.
		}
		throw new RuntimeException( 'The fixed activation transaction failed closed.' );
	}
	delete_option( FRPDEPOT_FQJ_LOCK_OPTION );
}

function frpdepot_fqj_rollback_drift( &$state, $operation, $artifact, $artifact_id, $current_hash ) {
	frpdepot_fqj_append_receipt( $operation, $artifact, $artifact_id, $current_hash, $current_hash, 'ROLLBACK_BLOCKED_DRIFT' );
	$state['status'] = 'rollback_blocked_drift';
	$state['rollback_blocked_artifact'] = (string) $artifact;
	update_option( FRPDEPOT_FQJ_STATE_OPTION, $state, false );
	throw new RuntimeException( 'Rollback refused because an owned artifact drifted.' );
}

function frpdepot_fqj_rollback_transaction( $require_admin = true ) {
	if ( $require_admin ) {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( esc_html__( 'Access denied.', 'frpdepot-fqj' ) );
		}
		check_admin_referer( 'frpdepot_fqj_fixed_rollback', 'frpdepot_fqj_nonce' );
	}
	$state = frpdepot_fqj_state();
	if ( ! is_array( $state ) || ! in_array( (string) $state['status'], array( 'applied', 'applying', 'indeterminate' ), true )
		|| FRPDEPOT_FQJ_SPEC_SHA256 !== (string) $state['spec_sha256'] || ! frpdepot_fqj_verify_receipt_chain() ) {
		throw new RuntimeException( 'Fixed rollback preconditions refused.' );
	}
	foreach ( frpdepot_fqj_backup_options() as $artifact => $option ) {
		$backup = get_option( $option, false );
		if ( ! is_array( $backup ) || (string) $backup['deployment_id'] !== (string) $state['deployment_id']
			|| (string) $backup['artifact'] !== $artifact ) {
			throw new RuntimeException( 'Fixed rollback backup verification refused.' );
		}
	}

	if ( '' !== (string) $state['contact_after_sha256'] ) {
		$contact = get_post( FRPDEPOT_FQJ_CONTACT_ID );
		$current_hash = $contact ? hash( 'sha256', (string) $contact->post_content ) : frpdepot_fqj_hash( null );
		$backup = get_option( FRPDEPOT_FQJ_BACKUP_CONTACT_OPTION, false );
		$already_before = is_array( $backup ) && $contact && 'page' === (string) $contact->post_type
			&& hash_equals( (string) $backup['before_sha256'], $current_hash )
			&& 1 === substr_count( (string) $contact->post_content, FRPDEPOT_FQJ_CONTACT_OLD );
		if ( $already_before ) {
			$state['contact_after_sha256'] = (string) $backup['before_sha256'];
			frpdepot_fqj_write_state( $state );
		} else {
			if ( ! $contact || 'page' !== (string) $contact->post_type
				|| ! hash_equals( (string) $state['contact_after_sha256'], $current_hash )
				|| 1 !== substr_count( (string) $contact->post_content, FRPDEPOT_FQJ_CONTACT_NEW ) ) {
				frpdepot_fqj_rollback_drift( $state, 'restore', 'contact_faq', FRPDEPOT_FQJ_CONTACT_ID, $current_hash );
			}
			$result = wp_update_post( wp_slash( array( 'ID' => FRPDEPOT_FQJ_CONTACT_ID, 'post_content' => $backup['payload']['post_content'] ) ), true );
			$restored = get_post( FRPDEPOT_FQJ_CONTACT_ID );
			if ( is_wp_error( $result ) || FRPDEPOT_FQJ_CONTACT_ID !== (int) $result || ! $restored
				|| ! hash_equals( (string) $backup['before_sha256'], hash( 'sha256', (string) $restored->post_content ) ) ) {
				throw new RuntimeException( 'Contact rollback verification refused.' );
			}
			frpdepot_fqj_append_receipt( 'restore', 'contact_faq', FRPDEPOT_FQJ_CONTACT_ID,
				$current_hash, $backup['before_sha256'], 'ROLLED_BACK' );
			frpdepot_fqj_append_receipt( 'verify', 'contact_faq', FRPDEPOT_FQJ_CONTACT_ID,
				$backup['before_sha256'], $backup['before_sha256'], 'ROLLED_BACK' );
			$state['contact_after_sha256'] = (string) $backup['before_sha256'];
			frpdepot_fqj_write_state( $state );
		}
	}

	if ( (int) $state['page_id'] > 0 ) {
		$page = get_post( (int) $state['page_id'] );
		$current_hash = frpdepot_fqj_hash( frpdepot_fqj_page_projection( $page ) );
		if ( ! $page || 'page' !== (string) $page->post_type
			|| false === strpos( (string) $page->post_content, FRPDEPOT_FQJ_PAGE_MARKER )
			|| ! hash_equals( (string) $state['page_after_sha256'], $current_hash ) ) {
			frpdepot_fqj_rollback_drift( $state, 'delete', 'quote_page', (int) $state['page_id'], $current_hash );
		}
		$page_id = (int) $state['page_id'];
		if ( ! wp_delete_post( $page_id, true ) || get_post( $page_id ) || get_page_by_path( FRPDEPOT_FQJ_PAGE_SLUG, OBJECT, 'page' ) ) {
			throw new RuntimeException( 'Quote-page rollback verification refused.' );
		}
		$null_hash = frpdepot_fqj_hash( null );
		frpdepot_fqj_append_receipt( 'delete', 'quote_page', $page_id, $current_hash, $null_hash, 'ROLLED_BACK' );
		frpdepot_fqj_append_receipt( 'verify', 'quote_page', $page_id, $null_hash, $null_hash, 'ROLLED_BACK' );
		$state['page_after_sha256'] = frpdepot_fqj_hash( null );
		frpdepot_fqj_write_state( $state );
	}

	if ( (int) $state['form_id'] > 0 ) {
		$form = GFAPI::get_form( (int) $state['form_id'] );
		$current_hash = frpdepot_fqj_hash( frpdepot_fqj_form_projection( $form ) );
		$form_backup = get_option( FRPDEPOT_FQJ_BACKUP_FORM_OPTION, false );
		if ( ! frpdepot_fqj_form_has_admin_ownership( $form )
			|| ! hash_equals( (string) $state['form_after_sha256'], $current_hash ) ) {
			frpdepot_fqj_rollback_drift( $state, 'delete', 'form', (int) $state['form_id'], $current_hash );
		}
		$form_id = (int) $state['form_id'];
		if ( is_array( $form_backup ) && ! empty( $form_backup['existed'] ) ) {
			if ( (int) $form_backup['artifact_id'] !== $form_id
				|| ! hash_equals( (string) $form_backup['before_sha256'], $current_hash ) ) {
				frpdepot_fqj_rollback_drift( $state, 'restore', 'form', $form_id, $current_hash );
			}
			frpdepot_fqj_append_receipt( 'verify', 'form', $form_id, $current_hash, $current_hash, 'ROLLED_BACK' );
			$state['form_after_sha256'] = (string) $form_backup['before_sha256'];
		} else {
			$result = GFAPI::delete_form( $form_id );
			if ( is_wp_error( $result ) || false === $result || GFAPI::get_form( $form_id ) ) {
				throw new RuntimeException( 'Form rollback verification refused.' );
			}
			$null_hash = frpdepot_fqj_hash( null );
			frpdepot_fqj_append_receipt( 'delete', 'form', $form_id, $current_hash, $null_hash, 'ROLLED_BACK' );
			frpdepot_fqj_append_receipt( 'verify', 'form', $form_id, $null_hash, $null_hash, 'ROLLED_BACK' );
			$state['form_after_sha256'] = $null_hash;
			frpdepot_fqj_write_state( $state );
		}
	}
	$state['status'] = 'rolled_back';
	$state['rollback_blocked_artifact'] = '';
	frpdepot_fqj_write_state( $state );
	frpdepot_fqj_append_receipt( 'finalize', 'journey', $state['deployment_id'],
		frpdepot_fqj_hash( null ), frpdepot_fqj_hash( $state ), 'ROLLED_BACK' );
	return $state;
}

/* -------------------------------------------------------------------------
 * Closed server-reconstructed handoff and form-specific runtime hooks.
 * ---------------------------------------------------------------------- */

function frpdepot_fqj_decimal( $value, $positive = false ) {
	if ( ! is_string( $value ) && ! is_int( $value ) ) {
		return null;
	}
	$text = (string) $value;
	if ( ! preg_match( '/^(0|[1-9][0-9]*)$/', $text ) || strlen( $text ) > 10 ) {
		return null;
	}
	$number = (int) $text;
	return ( $positive && $number <= 0 ) ? null : $number;
}

function frpdepot_fqj_request_query() {
	$query = array();
	foreach ( $_GET as $key => $value ) {
		if ( 0 === strpos( (string) $key, 'fqj_' ) ) {
			$query[ (string) $key ] = is_string( $value ) ? wp_unslash( $value ) : null;
		}
	}
	ksort( $query, SORT_STRING );
	return $query;
}

function frpdepot_fqj_same_origin_url( $url ) {
	if ( ! is_string( $url ) || '' === $url || ! function_exists( 'home_url' ) ) {
		return '';
	}
	$home = wp_parse_url( home_url( '/' ) );
	$parsed = wp_parse_url( $url );
	if ( ! is_array( $home ) || ! is_array( $parsed ) || ! isset( $home['scheme'], $home['host'], $parsed['scheme'], $parsed['host'] )
		|| strtolower( $home['scheme'] ) !== strtolower( $parsed['scheme'] )
		|| strtolower( $home['host'] ) !== strtolower( $parsed['host'] )
		|| isset( $parsed['user'] ) || isset( $parsed['pass'] ) ) {
		return '';
	}
	return esc_url_raw( $url );
}

function frpdepot_fqj_attribute_value( $name, $value, $product ) {
	$shown = (string) $value;
	$taxonomy = preg_replace( '/^attribute_/', '', (string) $name );
	if ( function_exists( 'taxonomy_exists' ) && taxonomy_exists( $taxonomy ) && function_exists( 'get_term_by' ) ) {
		$term = get_term_by( 'slug', (string) $value, $taxonomy );
		if ( is_object( $term ) && isset( $term->name ) ) {
			$shown = (string) $term->name;
		}
	}
	$label = function_exists( 'wc_attribute_label' ) ? wc_attribute_label( $taxonomy, $product ) : $taxonomy;
	return array( sanitize_text_field( $label ), sanitize_text_field( $shown ) );
}

function frpdepot_fqj_product_attributes( $product ) {
	$attributes = array();
	if ( ! is_object( $product ) || ! method_exists( $product, 'get_attributes' ) ) {
		return null;
	}
	foreach ( (array) $product->get_attributes() as $name => $value ) {
		list( $label, $shown ) = frpdepot_fqj_attribute_value( $name, $value, $product );
		if ( '' === $label || '' === $shown ) {
			return null;
		}
		$attributes[ $label ] = $shown;
	}
	ksort( $attributes, SORT_STRING );
	return $attributes;
}

function frpdepot_fqj_line_text( $name, $attributes ) {
	$parts = array();
	foreach ( $attributes as $label => $value ) {
		$parts[] = $label . ': ' . $value;
	}
	return $name . ( $parts ? ' — ' . implode( '; ', $parts ) : '' );
}

function frpdepot_fqj_option_lines( $lines, $needles ) {
	$values = array();
	foreach ( $lines as $index => $line ) {
		$found = '';
		foreach ( $line['attributes'] as $label => $value ) {
			$normal = strtolower( $label );
			foreach ( $needles as $needle ) {
				if ( false !== strpos( $normal, $needle ) ) {
					$found = $value;
					break 2;
				}
			}
		}
		$values[] = count( $lines ) > 1 ? ( $index + 1 ) . '. ' . $found : $found;
	}
	return implode( "\n", $values );
}

function frpdepot_fqj_line_has_option( $line, $needles ) {
	foreach ( $line['attributes'] as $label => $value ) {
		$normal = strtolower( (string) $label );
		foreach ( $needles as $needle ) {
			if ( false !== strpos( $normal, $needle ) && '' !== trim( (string) $value ) ) {
				return true;
			}
		}
	}
	return false;
}

function frpdepot_fqj_handoff_result( $valid, $source, $lines = array(), $projection = '' ) {
	$products = array();
	$quantities = array();
	$product_ids = array();
	$variation_ids = array();
	foreach ( $lines as $index => $line ) {
		$prefix = count( $lines ) > 1 ? ( $index + 1 ) . '. ' : '';
		$products[] = $prefix . frpdepot_fqj_line_text( $line['product_name'], $line['attributes'] );
		$quantities[] = $prefix . (string) $line['quantity'];
		$product_ids[] = (string) $line['product_id'];
		$variation_ids[] = (string) $line['variation_id'];
		if ( ! frpdepot_fqj_line_has_option( $line, array( 'size', 'diameter' ) )
			|| ! frpdepot_fqj_line_has_option( $line, array( 'pressure', 'psi' ) )
			|| ! frpdepot_fqj_line_has_option( $line, array( 'resin' ) ) ) {
			$valid = false;
		}
	}
	$first_url = 1 === count( $lines ) ? $lines[0]['product_url'] : '';
	$values = array(
		6 => implode( "\n", $products ),
		7 => frpdepot_fqj_option_lines( $lines, array( 'size', 'diameter' ) ),
		8 => frpdepot_fqj_option_lines( $lines, array( 'pressure', 'psi' ) ),
		9 => frpdepot_fqj_option_lines( $lines, array( 'resin' ) ),
		10 => implode( "\n", $quantities ),
		17 => $first_url,
		18 => implode( ',', $product_ids ),
		19 => implode( ',', $variation_ids ),
		20 => $source,
		21 => $projection,
	);
	foreach ( array( 6, 7, 8, 9 ) as $field_id ) {
		if ( strlen( $values[ $field_id ] ) > 500 ) {
			$valid = false;
		}
	}
	if ( strlen( $values[10] ) > 200 ) {
		$valid = false;
	}
	return array( 'valid' => (bool) $valid, 'source' => $source, 'values' => $valid ? $values : array(
		6 => '', 7 => '', 8 => '', 9 => '', 10 => '', 17 => '', 18 => '', 19 => '', 20 => 'direct', 21 => '',
	) );
}

function frpdepot_fqj_product_handoff( $query ) {
	if ( array_keys( $query ) !== array( 'fqj_product_id', 'fqj_quantity', 'fqj_source', 'fqj_variation_id' )
		|| 'product' !== $query['fqj_source'] ) {
		return frpdepot_fqj_handoff_result( false, 'direct' );
	}
	$product_id = frpdepot_fqj_decimal( $query['fqj_product_id'], true );
	$variation_id = frpdepot_fqj_decimal( $query['fqj_variation_id'], true );
	$quantity = frpdepot_fqj_decimal( $query['fqj_quantity'], true );
	if ( null === $product_id || null === $variation_id || null === $quantity || $quantity > FRPDEPOT_FQJ_MAX_QUANTITY
		|| ! in_array( $product_id, frpdepot_fqj_product_targets(), true ) || ! function_exists( 'wc_get_product' ) ) {
		return frpdepot_fqj_handoff_result( false, 'direct' );
	}
	$parent = wc_get_product( $product_id );
	$variation = wc_get_product( $variation_id );
	if ( ! is_object( $parent ) || ! is_object( $variation ) || ! method_exists( $variation, 'get_parent_id' )
		|| (int) $variation->get_parent_id() !== $product_id
		|| ! frpdepot_fqj_item_requires_quote( $product_id, $variation_id, $variation ) ) {
		return frpdepot_fqj_handoff_result( false, 'direct' );
	}
	$attributes = frpdepot_fqj_product_attributes( $variation );
	$name = method_exists( $parent, 'get_name' ) ? sanitize_text_field( (string) $parent->get_name() ) : '';
	$url = function_exists( 'get_permalink' ) ? frpdepot_fqj_same_origin_url( get_permalink( $product_id ) ) : '';
	if ( ! is_array( $attributes ) || '' === $name || '' === $url ) {
		return frpdepot_fqj_handoff_result( false, 'direct' );
	}
	return frpdepot_fqj_handoff_result( true, 'product', array( array(
		'product_name' => $name,
		'product_id' => $product_id,
		'variation_id' => $variation_id,
		'attributes' => $attributes,
		'quantity' => $quantity,
		'product_url' => $url,
	) ) );
}

function frpdepot_fqj_cart_handoff( $query ) {
	if ( array_keys( $query ) !== array( 'fqj_source' ) || 'cart' !== $query['fqj_source']
		|| ! function_exists( 'WC' ) || null === WC() || ! isset( WC()->cart ) || null === WC()->cart
		|| ! frpdepot_fqj_cart_requires_quote() ) {
		return frpdepot_fqj_handoff_result( false, 'direct' );
	}
	$cart = WC()->cart->get_cart();
	if ( ! is_array( $cart ) || 0 === count( $cart ) || count( $cart ) > FRPDEPOT_FQJ_MAX_CART_LINES ) {
		return frpdepot_fqj_handoff_result( false, 'direct' );
	}
	$lines = array();
	$projection = array();
	foreach ( $cart as $line ) {
		if ( ! is_array( $line ) || ! isset( $line['data'] ) || ! is_object( $line['data'] ) ) {
			return frpdepot_fqj_handoff_result( false, 'direct' );
		}
		$product_id = isset( $line['product_id'] ) ? (int) $line['product_id'] : 0;
		$variation_id = isset( $line['variation_id'] ) ? (int) $line['variation_id'] : 0;
		$quantity = isset( $line['quantity'] ) ? frpdepot_fqj_decimal( $line['quantity'], true ) : null;
		$parent = function_exists( 'wc_get_product' ) ? wc_get_product( $product_id ) : null;
		$selection_id = $variation_id > 0 ? $variation_id : $product_id;
		if ( $product_id <= 0 || $variation_id < 0 || null === $quantity || $quantity > FRPDEPOT_FQJ_MAX_QUANTITY
			|| ! is_object( $parent ) || ! method_exists( $line['data'], 'get_id' )
			|| (int) $line['data']->get_id() !== $selection_id
			|| ( $variation_id > 0 && ( ! method_exists( $line['data'], 'get_parent_id' )
				|| (int) $line['data']->get_parent_id() !== $product_id ) ) ) {
			return frpdepot_fqj_handoff_result( false, 'direct' );
		}
		$attributes = array();
		$raw_attributes = isset( $line['variation'] ) && is_array( $line['variation'] ) ? $line['variation'] : array();
		foreach ( $raw_attributes as $name => $value ) {
			list( $label, $shown ) = frpdepot_fqj_attribute_value( $name, $value, $line['data'] );
			if ( '' === $label || '' === $shown ) {
				return frpdepot_fqj_handoff_result( false, 'direct' );
			}
			$attributes[ $label ] = $shown;
		}
		ksort( $attributes, SORT_STRING );
		$name = method_exists( $parent, 'get_name' ) ? sanitize_text_field( (string) $parent->get_name() ) : '';
		if ( '' === $name ) {
			return frpdepot_fqj_handoff_result( false, 'direct' );
		}
		$url = function_exists( 'get_permalink' ) ? frpdepot_fqj_same_origin_url( get_permalink( $product_id ) ) : '';
		if ( '' === $url ) {
			return frpdepot_fqj_handoff_result( false, 'direct' );
		}
		$lines[] = array(
			'product_name' => $name,
			'product_id' => $product_id,
			'variation_id' => $variation_id,
			'attributes' => $attributes,
			'quantity' => $quantity,
			'product_url' => $url,
		);
		$projection[] = array(
			'product_id' => $product_id,
			'variation_id' => $variation_id,
			'attributes' => $attributes,
			'quantity' => $quantity,
		);
	}
	$json = wp_json_encode( $projection, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE );
	if ( ! is_string( $json ) || strlen( $json ) > FRPDEPOT_FQJ_MAX_CART_JSON_BYTES ) {
		return frpdepot_fqj_handoff_result( false, 'direct' );
	}
	return frpdepot_fqj_handoff_result( true, 'cart', $lines, $json );
}

function frpdepot_fqj_handoff_from_request() {
	$query = frpdepot_fqj_request_query();
	if ( array() === $query ) {
		return frpdepot_fqj_handoff_result( true, 'direct' );
	}
	if ( isset( $query['fqj_source'] ) && 'product' === $query['fqj_source'] ) {
		return frpdepot_fqj_product_handoff( $query );
	}
	if ( isset( $query['fqj_source'] ) && 'cart' === $query['fqj_source'] ) {
		return frpdepot_fqj_cart_handoff( $query );
	}
	if ( array_keys( $query ) === array( 'fqj_source' ) && 'contact' === $query['fqj_source'] ) {
		return frpdepot_fqj_handoff_result( true, 'contact' );
	}
	return frpdepot_fqj_handoff_result( false, 'direct' );
}

function frpdepot_fqj_set_field_default( &$form, $field_id, $value, $handoff_error = false ) {
	if ( ! isset( $form['fields'] ) || ! is_array( $form['fields'] ) ) {
		return;
	}
	foreach ( $form['fields'] as &$field ) {
		if ( (int) frpdepot_fqj_value( $field, 'id', 0 ) !== (int) $field_id ) {
			continue;
		}
		if ( is_object( $field ) ) {
			$field->defaultValue = $value;
			if ( $handoff_error ) {
				$field->description = FRPDEPOT_FQJ_HANDOFF_ERROR;
			}
		} else {
			$field['defaultValue'] = $value;
			if ( $handoff_error ) {
				$field['description'] = FRPDEPOT_FQJ_HANDOFF_ERROR;
			}
		}
		break;
	}
	unset( $field );
}

function frpdepot_fqj_apply_handoff( $form, $submission = false ) {
	$handoff = frpdepot_fqj_handoff_from_request();
	foreach ( $handoff['values'] as $field_id => $value ) {
		frpdepot_fqj_set_field_default( $form, $field_id, $value, ! $handoff['valid'] && 6 === (int) $field_id );
		if ( $submission && ( in_array( (int) $field_id, array( 17, 18, 19, 20, 21 ), true )
			|| ( $handoff['valid'] && in_array( $handoff['source'], array( 'product', 'cart' ), true ) ) ) ) {
			$_POST[ 'input_' . (int) $field_id ] = $value;
		}
	}
	$GLOBALS['frpdepot_fqj_current_handoff_valid'] = (bool) $handoff['valid'];
	return $form;
}

function frpdepot_fqj_form_pre_render( $form ) {
	return frpdepot_fqj_apply_handoff( $form, false );
}

function frpdepot_fqj_form_pre_validation( $form ) {
	return frpdepot_fqj_apply_handoff( $form, true );
}

function frpdepot_fqj_form_pre_submission( $form ) {
	return frpdepot_fqj_apply_handoff( $form, true );
}

function frpdepot_fqj_form_validation( $result ) {
	if ( ! is_array( $result ) || ! isset( $result['form'] ) ) {
		return $result;
	}
	$handoff = frpdepot_fqj_handoff_from_request();
	if ( $handoff['valid'] ) {
		return $result;
	}
	$result['is_valid'] = false;
	foreach ( $result['form']['fields'] as &$field ) {
		if ( 6 === (int) frpdepot_fqj_value( $field, 'id', 0 ) ) {
			if ( is_object( $field ) ) {
				$field->failed_validation = true;
				$field->validation_message = FRPDEPOT_FQJ_HANDOFF_ERROR;
			} else {
				$field['failed_validation'] = true;
				$field['validation_message'] = FRPDEPOT_FQJ_HANDOFF_ERROR;
			}
			break;
		}
	}
	unset( $field );
	return $result;
}

function frpdepot_fqj_ids_grammar( $value, $allow_zero = false ) {
	if ( '' === $value ) {
		return true;
	}
	$part = $allow_zero ? '(?:0|[1-9][0-9]*)' : '[1-9][0-9]*';
	return 1 === preg_match( '/^' . $part . '(?:,' . $part . ')*$/', $value );
}

function frpdepot_fqj_confirmation_marker( $confirmation, $form, $entry, $ajax ) {
	unset( $ajax );
	$state = frpdepot_fqj_state();
	if ( ! is_array( $state ) || 'applied' !== (string) $state['status'] || ! frpdepot_fqj_form_has_admin_ownership( $form )
		|| (int) frpdepot_fqj_value( $form, 'id', 0 ) !== (int) $state['form_id']
		|| ! hash_equals( (string) $state['form_after_sha256'], frpdepot_fqj_hash( frpdepot_fqj_form_projection( $form ) ) )
		|| ! is_array( $entry ) ) {
		return $confirmation;
	}
	$message = is_array( $confirmation ) && isset( $confirmation['message'] ) ? (string) $confirmation['message'] : (string) $confirmation;
	$entry_id = isset( $entry['id'] ) ? (int) $entry['id'] : 0;
	$status = isset( $entry['status'] ) ? (string) $entry['status'] : '';
	$is_spam = ! empty( $entry['is_spam'] );
	$product_ids = isset( $entry['18'] ) ? (string) $entry['18'] : '';
	$variation_ids = isset( $entry['19'] ) ? (string) $entry['19'] : '';
	$source = isset( $entry['20'] ) ? (string) $entry['20'] : '';
	if ( FRPDEPOT_FQJ_CONFIRMATION !== $message || $entry_id <= 0 || 'active' !== $status || $is_spam
		|| ! frpdepot_fqj_ids_grammar( $product_ids, false ) || ! frpdepot_fqj_ids_grammar( $variation_ids, true )
		|| ! in_array( $source, array( 'product', 'cart', 'contact', 'direct' ), true ) ) {
		return $confirmation;
	}
	$marker = '<span class="frpdepot-fq-success" hidden data-form-id="' . esc_attr( (string) $state['form_id'] )
		. '" data-entry-id="' . esc_attr( (string) $entry_id )
		. '" data-product-id="' . esc_attr( $product_ids )
		. '" data-variation-id="' . esc_attr( $variation_ids )
		. '" data-source-page="' . esc_attr( $source )
		. '" data-marker="' . esc_attr( FRPDEPOT_FQJ_FORM_MARKER ) . '"></span>';
	if ( is_array( $confirmation ) && isset( $confirmation['message'] ) ) {
		$confirmation['message'] .= $marker;
		return $confirmation;
	}
	return $message . $marker;
}

function frpdepot_fqj_register_form_hooks() {
	$state = frpdepot_fqj_state();
	if ( ! is_array( $state ) || 'applied' !== (string) $state['status'] || (int) $state['form_id'] <= 0
		|| ! class_exists( 'GFAPI' ) ) {
		return;
	}
	$form = GFAPI::get_form( (int) $state['form_id'] );
	if ( ! frpdepot_fqj_form_has_admin_ownership( $form )
		|| ! hash_equals( (string) $state['form_after_sha256'], frpdepot_fqj_hash( frpdepot_fqj_form_projection( $form ) ) ) ) {
		return;
	}
	$id = (int) $state['form_id'];
	add_filter( 'gform_pre_render_' . $id, 'frpdepot_fqj_form_pre_render', 10, 1 );
	add_filter( 'gform_pre_validation_' . $id, 'frpdepot_fqj_form_pre_validation', 10, 1 );
	add_filter( 'gform_pre_submission_filter_' . $id, 'frpdepot_fqj_form_pre_submission', 10, 1 );
	add_filter( 'gform_validation_' . $id, 'frpdepot_fqj_form_validation', 10, 1 );
	add_filter( 'gform_confirmation_' . $id, 'frpdepot_fqj_confirmation_marker', 100, 4 );
}

function frpdepot_fqj_quote_page_url() {
	$state = frpdepot_fqj_state();
	if ( ! is_array( $state ) || 'applied' !== (string) $state['status'] || (int) $state['page_id'] <= 0 ) {
		return '';
	}
	$page = get_post( (int) $state['page_id'] );
	if ( ! $page || false === strpos( (string) $page->post_content, FRPDEPOT_FQJ_PAGE_MARKER )
		|| ! hash_equals( (string) $state['page_after_sha256'], frpdepot_fqj_hash( frpdepot_fqj_page_projection( $page ) ) ) ) {
		return '';
	}
	$url = get_permalink( (int) $state['page_id'] );
	return is_string( $url ) ? frpdepot_fqj_same_origin_url( $url ) : '';
}

function frpdepot_fqj_enqueue_assets() {
	if ( function_exists( 'is_admin' ) && is_admin() ) {
		return;
	}
	$state = frpdepot_fqj_state();
	$form_id = is_array( $state ) && 'applied' === (string) $state['status'] ? (int) $state['form_id'] : 0;
	$page_id = is_array( $state ) ? (int) $state['page_id'] : 0;
	$is_quote_page = $page_id > 0 && function_exists( 'is_page' ) && is_page( $page_id );
	$is_product = false;
	if ( function_exists( 'is_product' ) && is_product() ) {
		global $product;
		$product_id = is_object( $product ) && method_exists( $product, 'get_id' )
			? (int) $product->get_id()
			: ( function_exists( 'get_queried_object_id' ) ? (int) get_queried_object_id() : 0 );
		$is_product = in_array( $product_id, frpdepot_fqj_product_targets(), true );
	}
	$cart_quote = frpdepot_fqj_cart_requires_quote();
	if ( ! $is_product && ! $cart_quote && ! $is_quote_page ) {
		return;
	}
	wp_enqueue_style( 'frpdepot-fqj', plugins_url( FRPDEPOT_FQJ_CSS, __FILE__ ), array(), FRPDEPOT_FQJ_VERSION );
	wp_enqueue_script( 'frpdepot-fqj', plugins_url( FRPDEPOT_FQJ_JS, __FILE__ ), array( 'jquery' ), FRPDEPOT_FQJ_VERSION, true );
	wp_localize_script( 'frpdepot-fqj', 'FRPDepotFreightQuoteJourney', array(
		'quoteUrl' => frpdepot_fqj_quote_page_url(),
		'cartQuoteUrl' => $cart_quote ? frpdepot_fqj_cart_quote_url() : '',
		'cartQuoteRequired' => (bool) $cart_quote,
		'button' => FRPDEPOT_FQJ_BUTTON,
		'cartHeading' => FRPDEPOT_FQJ_PRODUCT_HEADING,
		'cartText' => FRPDEPOT_FQJ_CART_TEXT,
		'formId' => $form_id,
		'formMarker' => FRPDEPOT_FQJ_FORM_MARKER,
	) );
}

/* -------------------------------------------------------------------------
 * Privacy-preserving fixed status and rollback control.
 * ---------------------------------------------------------------------- */

function frpdepot_fqj_status_projection() {
	$state = frpdepot_fqj_state();
	$head = get_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION, false );
	$empty_sha256 = frpdepot_fqj_hash( null );
	$deployment_id = is_array( $state ) ? (string) $state['deployment_id'] : str_repeat( '0', 32 );
	$status = is_array( $state ) ? (string) $state['status'] : 'not_applied';
	$form_id = is_array( $state ) ? (int) $state['form_id'] : 0;
	$page_id = is_array( $state ) ? (int) $state['page_id'] : 0;
	$form = $form_id > 0 ? GFAPI::get_form( $form_id ) : null;
	$form_projection = frpdepot_fqj_form_projection( $form );
	$form_sha256 = null === $form_projection ? $empty_sha256 : frpdepot_fqj_hash( $form_projection );
	$form_owned = 'rolled_back' !== $status && frpdepot_fqj_form_has_admin_ownership( $form )
		&& is_array( $state ) && hash_equals( (string) $state['form_after_sha256'], $form_sha256 );
	$page = $page_id > 0 ? get_post( $page_id ) : null;
	$page_projection = frpdepot_fqj_page_projection( $page );
	$page_sha256 = null === $page_projection ? $empty_sha256 : frpdepot_fqj_hash( $page_projection );
	$page_owned = $page && false !== strpos( (string) $page->post_content, FRPDEPOT_FQJ_PAGE_MARKER )
		&& is_array( $state ) && hash_equals( (string) $state['page_after_sha256'], $page_sha256 );
	$contact = get_post( FRPDEPOT_FQJ_CONTACT_ID );
	$contact_content = $contact ? (string) $contact->post_content : '';
	$contact_sha256 = $contact ? hash( 'sha256', $contact_content ) : $empty_sha256;
	$form_backup = get_option( FRPDEPOT_FQJ_BACKUP_FORM_OPTION, false );
	$page_backup = get_option( FRPDEPOT_FQJ_BACKUP_PAGE_OPTION, false );
	$contact_backup = get_option( FRPDEPOT_FQJ_BACKUP_CONTACT_OPTION, false );
	$route_backup = get_option( FRPDEPOT_FQJ_BACKUP_ROUTE_OPTION, false );
	$chain_valid = is_array( $head ) ? frpdepot_fqj_verify_receipt_chain() : false;
	$route_matches = false;
	if ( is_array( $state ) && is_array( $route_backup ) ) {
		try {
			$route_matches = hash_equals( (string) $state['route_sha256'], frpdepot_fqj_hash( frpdepot_fqj_source_route() ) );
		} catch ( Throwable $route_error ) {
			$route_matches = false;
		}
	}
	$form_before = is_array( $form_backup ) ? (string) $form_backup['before_sha256'] : $empty_sha256;
	$page_before = is_array( $page_backup ) ? (string) $page_backup['before_sha256'] : $empty_sha256;
	$contact_before = is_array( $contact_backup ) ? (string) $contact_backup['before_sha256'] : $empty_sha256;
	$rollback_drift_free = false;
	if ( 'applied' === $status ) {
		$rollback_drift_free = $form_owned && $page_owned && $contact
			&& 1 === substr_count( $contact_content, FRPDEPOT_FQJ_CONTACT_NEW )
			&& is_array( $state ) && hash_equals( (string) $state['contact_after_sha256'], $contact_sha256 );
	} elseif ( 'rolled_back' === $status ) {
		$form_restored = is_array( $form_backup ) && ! empty( $form_backup['existed'] )
			? is_array( $form ) && (int) frpdepot_fqj_value( $form, 'id', 0 ) === (int) $form_backup['artifact_id']
				&& hash_equals( $form_before, $form_sha256 )
			: ! is_array( $form );
		$rollback_drift_free = $form_restored && ! $page && $contact
			&& hash_equals( $contact_before, $contact_sha256 );
	}
	return array(
		'spec_sha256' => FRPDEPOT_FQJ_SPEC_SHA256,
		'status' => $status,
		'deployment_id' => $deployment_id,
		'source_form_id' => FRPDEPOT_FQJ_SOURCE_FORM_ID,
		'source_notification_name_match' => $route_matches,
		'route_sha256' => is_array( $state ) ? (string) $state['route_sha256'] : $empty_sha256,
		'form_id' => $form_id,
		'form_owned' => $form_owned,
		'form_sha256' => $form_sha256,
		'page_id' => $page_id,
		'page_owned' => $page_owned,
		'page_sha256' => $page_sha256,
		'contact_id' => FRPDEPOT_FQJ_CONTACT_ID,
		'contact_new_count' => substr_count( $contact_content, FRPDEPOT_FQJ_CONTACT_NEW ),
		'contact_old_count' => substr_count( $contact_content, FRPDEPOT_FQJ_CONTACT_OLD ),
		'contact_sha256' => $contact_sha256,
		'form_backup_present' => is_array( $form_backup ),
		'quote_page_backup_present' => is_array( $page_backup ),
		'contact_backup_present' => is_array( $contact_backup ),
		'route_backup_present' => is_array( $route_backup ),
		'receipt_count' => is_array( $head ) ? (int) $head['sequence'] : 0,
		'receipt_schema_valid' => $chain_valid,
		'receipt_chain_valid' => $chain_valid,
		'receipt_append_only' => $chain_valid,
		'receipt_head_sha256' => is_array( $head ) ? (string) $head['receipt_sha256'] : $empty_sha256,
		'apply_receipt_head_sha256' => is_array( $state ) ? (string) $state['apply_receipt_head_sha256'] : $empty_sha256,
		'rollback_drift_free' => $rollback_drift_free,
		'rollback_blocked_artifact' => is_array( $state ) ? (string) $state['rollback_blocked_artifact'] : '',
		'form_before_sha256' => $form_before,
		'quote_page_before_sha256' => $page_before,
		'contact_before_sha256' => $contact_before,
		'privacy' => array(
			'recipient_values_projected' => false,
			'customer_values_projected' => false,
			'artifact_content_projected' => false,
			'route_hash_only' => true,
		),
	);
}

function frpdepot_fqj_admin_menu() {
	add_management_page( 'FRP Depot Freight Quote Journey', 'Freight Quote Journey', 'manage_options',
		FRPDEPOT_FQJ_ADMIN_SLUG, 'frpdepot_fqj_admin_page' );
}

function frpdepot_fqj_admin_page() {
	if ( ! current_user_can( 'manage_options' ) ) {
		wp_die( esc_html__( 'Access denied.', 'frpdepot-fqj' ) );
	}
	$status = frpdepot_fqj_status_projection();
	echo '<div class="wrap"><h1>FRP Depot Freight Quote Journey</h1><table id="frpdepot-fqj-status" data-projection="'
		. esc_attr( wp_json_encode( $status ) ) . '"><tbody>';
	foreach ( $status as $key => $value ) {
		if ( is_array( $value ) ) {
			$value = wp_json_encode( $value );
		} elseif ( is_bool( $value ) ) {
			$value = $value ? 'true' : 'false';
		}
		echo '<tr data-key="' . esc_attr( $key ) . '"><th>' . esc_html( $key ) . '</th><td>' . esc_html( (string) $value ) . '</td></tr>';
	}
	echo '</tbody></table>';
	if ( 'not_applied' === $status['status'] && ! $status['form_backup_present']
		&& ! $status['quote_page_backup_present'] && ! $status['contact_backup_present']
		&& ! $status['route_backup_present'] ) {
		echo '<form id="frpdepot-fqj-apply-form" method="post" action="' . esc_url( admin_url( 'admin-post.php' ) ) . '">';
		echo '<input type="hidden" name="action" value="frpdepot_fqj_fixed_apply">';
		wp_nonce_field( 'frpdepot_fqj_fixed_apply', 'frpdepot_fqj_nonce' );
		echo '<button id="frpdepot-fqj-apply" type="submit" class="button button-primary">Apply fixed freight quote journey</button></form>';
	}
	$rollback_backups_ready = $status['form_backup_present'] && $status['quote_page_backup_present']
		&& $status['contact_backup_present'] && $status['route_backup_present'];
	if ( in_array( $status['status'], array( 'applied', 'applying', 'indeterminate' ), true ) && $rollback_backups_ready ) {
		echo '<form id="frpdepot-fqj-rollback-form" method="post" action="' . esc_url( admin_url( 'admin-post.php' ) ) . '">';
		echo '<input type="hidden" name="action" value="frpdepot_fqj_fixed_rollback">';
		wp_nonce_field( 'frpdepot_fqj_fixed_rollback', 'frpdepot_fqj_nonce' );
		echo '<button id="frpdepot-fqj-rollback" type="submit" class="button button-primary">Restore fixed freight quote journey backup</button></form>';
	}
	echo '</div>';
}

function frpdepot_fqj_admin_apply() {
	if ( ! current_user_can( 'manage_options' ) ) {
		wp_die( esc_html__( 'Access denied.', 'frpdepot-fqj' ) );
	}
	check_admin_referer( 'frpdepot_fqj_fixed_apply', 'frpdepot_fqj_nonce' );
	frpdepot_fqj_commissioned_activation_transaction();
	wp_safe_redirect( admin_url( 'tools.php?page=' . FRPDEPOT_FQJ_ADMIN_SLUG . '&applied=1' ) );
	exit;
}

function frpdepot_fqj_admin_rollback() {
	frpdepot_fqj_rollback_transaction( true );
	wp_safe_redirect( admin_url( 'tools.php?page=' . FRPDEPOT_FQJ_ADMIN_SLUG . '&rolled_back=1' ) );
	exit;
}

add_filter( 'woocommerce_available_variation', 'frpdepot_fqj_available_variation', 100, 3 );
add_action( 'woocommerce_after_add_to_cart_form', 'frpdepot_fqj_product_quote_panel', 20 );
add_filter( 'woocommerce_add_to_cart_validation', 'frpdepot_fqj_add_to_cart_validation', 100, 5 );
add_filter( 'woocommerce_package_rates', 'frpdepot_fqj_filter_package_rates', 100, 2 );
add_filter( 'woocommerce_no_shipping_available_html', 'frpdepot_fqj_no_shipping_html', 100, 1 );
add_filter( 'woocommerce_cart_no_shipping_available_html', 'frpdepot_fqj_no_shipping_html', 100, 1 );
add_filter( 'woocommerce_shipping_calculator_enable', 'frpdepot_fqj_shipping_calculator_enable', 100, 1 );
add_filter( 'woocommerce_available_payment_gateways', 'frpdepot_fqj_filter_gateways', 100, 1 );
add_action( 'woocommerce_check_cart_items', 'frpdepot_fqj_check_cart_items', 100 );
add_action( 'woocommerce_checkout_process', 'frpdepot_fqj_checkout_process', 100 );
add_action( 'woocommerce_after_checkout_validation', 'frpdepot_fqj_after_checkout_validation', 100, 2 );
add_action( 'woocommerce_checkout_create_order', 'frpdepot_fqj_checkout_create_order_guard', 1, 2 );
add_filter( 'woocommerce_store_api_cart_errors', 'frpdepot_fqj_store_api_cart_errors', 100, 1 );
add_action( 'woocommerce_store_api_checkout_update_order_from_request', 'frpdepot_fqj_store_api_checkout_guard', 1, 2 );
add_filter( 'rest_request_before_callbacks', 'frpdepot_fqj_rest_checkout_pre_callback', 100, 3 );
add_action( 'woocommerce_before_template_part', 'frpdepot_fqj_before_template_part', 1, 4 );
add_action( 'woocommerce_cart_has_errors', 'frpdepot_fqj_cart_has_errors', 1 );
add_action( 'wp', 'frpdepot_fqj_remove_classic_checkout_control', 100 );
add_action( 'woocommerce_before_cart', 'frpdepot_fqj_print_cart_message', 5 );
add_action( 'woocommerce_proceed_to_checkout', 'frpdepot_fqj_cart_quote_button', 30 );
add_action( 'wp_enqueue_scripts', 'frpdepot_fqj_enqueue_assets', 100 );
add_action( 'init', 'frpdepot_fqj_register_form_hooks', 20 );
add_action( 'admin_menu', 'frpdepot_fqj_admin_menu' );
add_action( 'admin_post_frpdepot_fqj_fixed_apply', 'frpdepot_fqj_admin_apply' );
add_action( 'admin_post_frpdepot_fqj_fixed_rollback', 'frpdepot_fqj_admin_rollback' );
