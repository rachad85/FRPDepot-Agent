<?php
/**
 * Plugin Name: FRP Depot Freight Quote Journey
 * Description: Adds the commissioned quote journey while leaving the active freight checkout guard and its UPS allowlist as the sole shipping authority.
 * Version: 1.0.0
 * Author: FRP Depot
 * License: GPL-2.0-or-later
 * Requires PHP: 7.4
 * Requires Plugins: woocommerce, gravityforms, frpdepot-freight-checkout-guard
 *
 * Specification SHA-256: 5348ef3f357676f5629cf72696fd3fe0be718a3847854974f20cf28cc7047400
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

const FRPDEPOT_FQ_VERSION = '1.0.0';
const FRPDEPOT_FQ_SPEC_SHA256 = '5348ef3f357676f5629cf72696fd3fe0be718a3847854974f20cf28cc7047400';
const FRPDEPOT_FQ_REQUIRED_GUARD_VERSION = '1.0.1';
const FRPDEPOT_FQ_FORM_TITLE = 'FRP Depot Product and Freight Quote';
const FRPDEPOT_FQ_FORM_SOURCE_ID = 1;
const FRPDEPOT_FQ_QUOTE_PATH = '/request-a-quote/';
const FRPDEPOT_FQ_QUERY_VAR = 'frpdepot_fq_page';
const FRPDEPOT_FQ_MAX_CART_LINES = 100;
const FRPDEPOT_FQ_MAX_OPTIONS = 20;
const FRPDEPOT_FQ_MAX_PAYLOAD_BYTES = 32768;
const FRPDEPOT_FQ_MAX_ENVELOPE_BYTES = 49152;
const FRPDEPOT_FQ_CONTEXT_TTL = 7200;
const FRPDEPOT_FQ_MAX_QUANTITY = 1000;

const FRPDEPOT_FQ_HEADING = 'Freight quote required';
const FRPDEPOT_FQ_PRODUCT_COPY = 'This selection is not available for direct online checkout. Choose the size, pressure rating, resin type and quantity, then request a product and freight quote. No payment will be taken.';
const FRPDEPOT_FQ_BUTTON = 'Request a Freight Quote';
const FRPDEPOT_FQ_CART_COPY = 'One or more items in this cart require a product and freight quote. Your selected products, options and quantities will be included automatically. No payment will be taken.';
const FRPDEPOT_FQ_PAGE_HEADING = 'Request a Product and Freight Quote';
const FRPDEPOT_FQ_INTRO = 'Send the selected product details and delivery destination below. FRP Depots will review product availability, packing and freight requirements before providing a complete quote. Submitting this form does not place an order or authorize payment.';
const FRPDEPOT_FQ_SUBMIT = 'Request Quote';
const FRPDEPOT_FQ_CONFIRMATION = 'Your quote request has been received. FRP Depots will review the product, packing and freight requirements before responding. No order has been placed and no payment has been taken.';
const FRPDEPOT_FQ_OLD_FAQ = 'If your item is listed in the Products section, you can add it to cart; otherwise use the contact form for custom or non-standard requests.';
const FRPDEPOT_FQ_NEW_FAQ_TEXT = 'Product selections approved for direct shipping can be purchased online. Selections requiring packing or freight review will show Request a Freight Quote. Submitting a quote request does not place an order or authorize payment.';
const FRPDEPOT_FQ_NEW_FAQ_HTML = 'Product selections approved for direct shipping can be purchased online. Selections requiring packing or freight review will show <strong>Request a Freight Quote</strong>. Submitting a quote request does not place an order or authorize payment.';

const FRPDEPOT_FQ_FIELD_FIRST_NAME = 1;
const FRPDEPOT_FQ_FIELD_LAST_NAME = 2;
const FRPDEPOT_FQ_FIELD_EMAIL = 3;
const FRPDEPOT_FQ_FIELD_COMPANY = 4;
const FRPDEPOT_FQ_FIELD_PRODUCT = 5;
const FRPDEPOT_FQ_FIELD_SIZE = 6;
const FRPDEPOT_FQ_FIELD_PRESSURE = 7;
const FRPDEPOT_FQ_FIELD_RESIN = 8;
const FRPDEPOT_FQ_FIELD_QUANTITY = 9;
const FRPDEPOT_FQ_FIELD_COUNTRY = 10;
const FRPDEPOT_FQ_FIELD_POSTAL = 11;
const FRPDEPOT_FQ_FIELD_NOTES = 12;
const FRPDEPOT_FQ_FIELD_PHONE = 13;
const FRPDEPOT_FQ_FIELD_DATE = 14;
const FRPDEPOT_FQ_FIELD_UPLOAD = 15;
const FRPDEPOT_FQ_FIELD_MARKETING = 16;
const FRPDEPOT_FQ_FIELD_PRODUCT_URL = 17;
const FRPDEPOT_FQ_FIELD_PRODUCT_ID = 18;
const FRPDEPOT_FQ_FIELD_VARIATION_ID = 19;
const FRPDEPOT_FQ_FIELD_SOURCE_PAGE = 20;
const FRPDEPOT_FQ_FIELD_PAYLOAD = 21;
const FRPDEPOT_FQ_FIELD_TEST_MARKER = 22;
const FRPDEPOT_FQ_TEST_MARKER = 'FRPDEPOT-FQJ-ACCEPTANCE-20260813';
const FRPDEPOT_FQ_FORM_MARKER = 'FRPDEPOT_FQJ_FIXED_FORM_V1_SPEC_5348EF3F';
const FRPDEPOT_FQ_PAGE_MARKER = '<!-- FRPDEPOT_FQJ_FIXED_PAGE_V1_SPEC_5348EF3F -->';
const FRPDEPOT_FQ_PAGE_TITLE = 'Request a Product and Freight Quote';
const FRPDEPOT_FQ_PAGE_SLUG = 'request-a-quote';
const FRPDEPOT_FQ_CONTACT_ID = 469;
const FRPDEPOT_FQ_FORM_ID_OPTION = 'frpdepot_fqj_form_id_v1';
const FRPDEPOT_FQ_PAGE_ID_OPTION = 'frpdepot_fqj_page_id_v1';
const FRPDEPOT_FQ_BACKUP_OPTION = 'frpdepot_fqj_reversible_backup_v1';
const FRPDEPOT_FQ_STATE_OPTION = 'frpdepot_fqj_state_v1';
const FRPDEPOT_FQ_RECEIPTS_OPTION = 'frpdepot_fqj_write_receipts_v1';
const FRPDEPOT_FQ_ACTIVATION_LOCK = 'frpdepot_fqj_activation_lock_v1';
const FRPDEPOT_FQ_ADMIN_SLUG = 'frpdepot-freight-quote-journey';

/** The exact dependency check. No local copy of guard rules or allowlist exists here. */
function frpdepot_fq_guard_ready() {
	return defined( 'FRPDEPOT_FCG_VERSION' )
		&& FRPDEPOT_FQ_REQUIRED_GUARD_VERSION === FRPDEPOT_FCG_VERSION
		&& function_exists( 'frpdepot_fcg_decide' )
		&& function_exists( 'frpdepot_fcg_load_allowlist_document' )
		&& function_exists( 'frpdepot_fcg_cart_requires_quote' );
}

/** Ask the active freight guard to decide one canonical product/variation. */
function frpdepot_fq_product_requires_quote( $product ) {
	if ( ! frpdepot_fq_guard_ready() || ! is_object( $product ) ) {
		return true;
	}
	foreach ( array( 'get_id', 'get_sku', 'get_shipping_class' ) as $method ) {
		if ( ! method_exists( $product, $method ) ) {
			return true;
		}
	}
	$is_variation = method_exists( $product, 'is_type' ) && $product->is_type( 'variation' );
	$variation_id = $is_variation ? (int) $product->get_id() : 0;
	$product_id   = $is_variation && method_exists( $product, 'get_parent_id' )
		? (int) $product->get_parent_id() : (int) $product->get_id();
	$decision = frpdepot_fcg_decide(
		array(
			array(
				'product_id'     => $product_id,
				'variation_id'   => $variation_id,
				'sku'            => (string) $product->get_sku(),
				'shipping_class' => (string) $product->get_shipping_class(),
			),
		),
		frpdepot_fcg_load_allowlist_document(),
		time()
	);
	return is_array( $decision ) && ! empty( $decision['freight_required'] );
}

function frpdepot_fq_cart_requires_quote() {
	return frpdepot_fq_guard_ready() && (bool) frpdepot_fcg_cart_requires_quote();
}

function frpdepot_fq_quote_url( $args = array() ) {
	$base = function_exists( 'home_url' ) ? home_url( FRPDEPOT_FQ_QUOTE_PATH ) : FRPDEPOT_FQ_QUOTE_PATH;
	return $args && function_exists( 'add_query_arg' ) ? add_query_arg( $args, $base ) : $base;
}

function frpdepot_fq_limit_text( $value, $limit = 200 ) {
	$text = function_exists( 'wp_strip_all_tags' ) ? wp_strip_all_tags( (string) $value ) : strip_tags( (string) $value );
	$text = trim( preg_replace( '/\s+/u', ' ', $text ) );
	if ( function_exists( 'mb_substr' ) ) {
		return mb_substr( $text, 0, $limit, 'UTF-8' );
	}
	return substr( $text, 0, $limit );
}

function frpdepot_fq_relative_source( $url, $fallback ) {
	$path = function_exists( 'wp_parse_url' ) ? wp_parse_url( (string) $url, PHP_URL_PATH ) : parse_url( (string) $url, PHP_URL_PATH );
	return is_string( $path ) && '/' === substr( $path, 0, 1 ) ? frpdepot_fq_limit_text( $path, 300 ) : $fallback;
}

/** Build canonical option labels without trusting a query string or browser label. */
function frpdepot_fq_product_options( $variation ) {
	$options = array();
	if ( ! is_object( $variation ) || ! method_exists( $variation, 'get_attributes' ) ) {
		return $options;
	}
	foreach ( (array) $variation->get_attributes() as $name => $value ) {
		if ( count( $options ) >= FRPDEPOT_FQ_MAX_OPTIONS ) {
			break;
		}
		$label = function_exists( 'wc_attribute_label' ) ? wc_attribute_label( (string) $name, $variation ) : (string) $name;
		$shown = (string) $value;
		if ( function_exists( 'taxonomy_exists' ) && taxonomy_exists( (string) $name ) && function_exists( 'get_term_by' ) ) {
			$term = get_term_by( 'slug', (string) $value, (string) $name );
			if ( is_object( $term ) && isset( $term->name ) ) {
				$shown = (string) $term->name;
			}
		}
		$options[ frpdepot_fq_limit_text( $label, 100 ) ] = frpdepot_fq_limit_text( $shown, 100 );
	}
	return $options;
}

function frpdepot_fq_quantity( $value ) {
	$quantity = (int) $value;
	return $quantity >= 1 && $quantity <= FRPDEPOT_FQ_MAX_QUANTITY ? $quantity : 0;
}

/** Canonical product handoff. Names, options and URL are resolved server-side. */
function frpdepot_fq_product_payload( $product_id, $variation_id, $quantity ) {
	$product_id   = (int) $product_id;
	$variation_id = (int) $variation_id;
	$quantity     = frpdepot_fq_quantity( $quantity );
	if ( $product_id <= 0 || $quantity <= 0 || ! function_exists( 'wc_get_product' ) ) {
		return new WP_Error( 'frpdepot_fq_invalid_product', 'The selected product could not be verified.' );
	}
	$parent    = wc_get_product( $product_id );
	$selection = $variation_id > 0 ? wc_get_product( $variation_id ) : $parent;
	if ( ! is_object( $parent ) || ! is_object( $selection ) ) {
		return new WP_Error( 'frpdepot_fq_missing_product', 'The selected product could not be verified.' );
	}
	if ( $variation_id > 0 ) {
		if ( ! method_exists( $selection, 'get_parent_id' ) || (int) $selection->get_parent_id() !== $product_id ) {
			return new WP_Error( 'frpdepot_fq_wrong_parent', 'The selected variation does not belong to this product.' );
		}
	}
	if ( ! frpdepot_fq_product_requires_quote( $selection ) ) {
		return new WP_Error( 'frpdepot_fq_direct_checkout', 'This selection is approved for direct checkout.' );
	}
	$name = method_exists( $parent, 'get_name' ) ? $parent->get_name() : '';
	$url  = function_exists( 'get_permalink' ) ? get_permalink( $product_id ) : '';
	$item = array(
		'product_name' => frpdepot_fq_limit_text( $name, 200 ),
		'product_id'   => $product_id,
		'variation_id' => $variation_id,
		'options'      => frpdepot_fq_product_options( $selection ),
		'quantity'     => $quantity,
		'product_url'  => frpdepot_fq_limit_text( $url, 500 ),
	);
	return array(
		'schema'      => 1,
		'source'      => 'product',
		'source_page' => frpdepot_fq_relative_source( $url, '/product/' ),
		'items'       => array( $item ),
	);
}

/** Full bounded cart handoff; deliberately excludes prices, stock, rates and customer data. */
function frpdepot_fq_cart_payload() {
	if ( ! frpdepot_fq_cart_requires_quote() || ! function_exists( 'WC' ) || ! WC() || ! isset( WC()->cart ) ) {
		return new WP_Error( 'frpdepot_fq_cart_not_blocked', 'This cart does not require a freight quote.' );
	}
	$cart = WC()->cart->get_cart();
	if ( ! is_array( $cart ) || 0 === count( $cart ) || count( $cart ) > FRPDEPOT_FQ_MAX_CART_LINES ) {
		return new WP_Error( 'frpdepot_fq_cart_bounds', 'The cart cannot be carried into the quote form.' );
	}
	$items = array();
	foreach ( $cart as $line ) {
		$product_id   = isset( $line['product_id'] ) ? (int) $line['product_id'] : 0;
		$variation_id = isset( $line['variation_id'] ) ? (int) $line['variation_id'] : 0;
		$quantity     = isset( $line['quantity'] ) ? frpdepot_fq_quantity( $line['quantity'] ) : 0;
		$selection    = isset( $line['data'] ) && is_object( $line['data'] ) ? $line['data'] : null;
		$parent       = function_exists( 'wc_get_product' ) ? wc_get_product( $product_id ) : null;
		if ( $product_id <= 0 || $quantity <= 0 || ! is_object( $selection ) || ! is_object( $parent ) ) {
			return new WP_Error( 'frpdepot_fq_cart_line', 'A cart line could not be verified.' );
		}
		if ( $variation_id > 0 && ( ! method_exists( $selection, 'get_parent_id' ) || (int) $selection->get_parent_id() !== $product_id ) ) {
			return new WP_Error( 'frpdepot_fq_cart_parent', 'A cart variation could not be verified.' );
		}
		$url = function_exists( 'get_permalink' ) ? get_permalink( $product_id ) : '';
		$items[] = array(
			'product_name' => frpdepot_fq_limit_text( method_exists( $parent, 'get_name' ) ? $parent->get_name() : '', 200 ),
			'product_id'   => $product_id,
			'variation_id' => $variation_id,
			'options'      => frpdepot_fq_product_options( $selection ),
			'quantity'     => $quantity,
			'product_url'  => frpdepot_fq_limit_text( $url, 500 ),
		);
	}
	$payload = array( 'schema' => 1, 'source' => 'cart', 'source_page' => '/cart/', 'items' => $items );
	$json    = wp_json_encode( $payload );
	if ( ! is_string( $json ) || strlen( $json ) > FRPDEPOT_FQ_MAX_PAYLOAD_BYTES ) {
		return new WP_Error( 'frpdepot_fq_cart_bytes', 'The cart payload is too large for the quote form.' );
	}
	return $payload;
}

function frpdepot_fq_direct_payload() {
	return array( 'schema' => 1, 'source' => 'direct', 'source_page' => FRPDEPOT_FQ_QUOTE_PATH, 'items' => array() );
}

function frpdepot_fq_base64url_encode( $value ) {
	return rtrim( strtr( base64_encode( (string) $value ), '+/', '-_' ), '=' );
}

function frpdepot_fq_base64url_decode( $value ) {
	if ( ! is_string( $value ) || ! preg_match( '/^[A-Za-z0-9_-]+$/', $value ) ) {
		return false;
	}
	$padding = strlen( $value ) % 4;
	if ( $padding ) {
		$value .= str_repeat( '=', 4 - $padding );
	}
	return base64_decode( strtr( $value, '-_', '+/' ), true );
}

function frpdepot_fq_sign( $encoded, $issued ) {
	$key = function_exists( 'wp_salt' ) ? wp_salt( 'auth' ) : 'offline-test-salt';
	return hash_hmac( 'sha256', (string) $issued . '|' . (string) $encoded, $key );
}

function frpdepot_fq_make_envelope( $payload, $issued = null ) {
	$json = wp_json_encode( $payload );
	if ( ! is_string( $json ) || strlen( $json ) > FRPDEPOT_FQ_MAX_PAYLOAD_BYTES ) {
		return new WP_Error( 'frpdepot_fq_payload_bytes', 'The quote context is too large.' );
	}
	$issued   = null === $issued ? time() : (int) $issued;
	$encoded  = frpdepot_fq_base64url_encode( $json );
	$envelope = wp_json_encode(
		array( 'version' => 1, 'issued' => $issued, 'payload' => $encoded, 'signature' => frpdepot_fq_sign( $encoded, $issued ) )
	);
	if ( ! is_string( $envelope ) || strlen( $envelope ) > FRPDEPOT_FQ_MAX_ENVELOPE_BYTES ) {
		return new WP_Error( 'frpdepot_fq_envelope_bytes', 'The quote context is too large.' );
	}
	return $envelope;
}

function frpdepot_fq_payload_schema_valid( $payload ) {
	if ( ! is_array( $payload ) || array_keys( $payload ) !== array( 'schema', 'source', 'source_page', 'items' ) ) {
		return false;
	}
	if ( 1 !== $payload['schema'] || ! in_array( $payload['source'], array( 'product', 'cart', 'direct' ), true ) || ! is_string( $payload['source_page'] ) || ! is_array( $payload['items'] ) ) {
		return false;
	}
	if ( count( $payload['items'] ) > FRPDEPOT_FQ_MAX_CART_LINES ) {
		return false;
	}
	if ( 'direct' === $payload['source'] && count( $payload['items'] ) !== 0 ) {
		return false;
	}
	if ( in_array( $payload['source'], array( 'product', 'cart' ), true ) && 0 === count( $payload['items'] ) ) {
		return false;
	}
	foreach ( $payload['items'] as $item ) {
		if ( ! is_array( $item ) || array_keys( $item ) !== array( 'product_name', 'product_id', 'variation_id', 'options', 'quantity', 'product_url' ) ) {
			return false;
		}
		if ( ! is_string( $item['product_name'] ) || ! is_int( $item['product_id'] ) || $item['product_id'] <= 0 || ! is_int( $item['variation_id'] ) || $item['variation_id'] < 0 || ! is_array( $item['options'] ) || count( $item['options'] ) > FRPDEPOT_FQ_MAX_OPTIONS || ! is_int( $item['quantity'] ) || frpdepot_fq_quantity( $item['quantity'] ) !== $item['quantity'] || ! is_string( $item['product_url'] ) ) {
			return false;
		}
		foreach ( $item['options'] as $key => $value ) {
			if ( ! is_string( $key ) || ! is_string( $value ) ) {
				return false;
			}
		}
	}
	return true;
}

function frpdepot_fq_verify_envelope( $raw, $now = null, $rehydrate = true ) {
	if ( ! is_string( $raw ) || '' === $raw || strlen( $raw ) > FRPDEPOT_FQ_MAX_ENVELOPE_BYTES ) {
		return new WP_Error( 'frpdepot_fq_bad_envelope', 'The quote context is invalid.' );
	}
	$envelope = json_decode( $raw, true );
	if ( ! is_array( $envelope ) || array_keys( $envelope ) !== array( 'version', 'issued', 'payload', 'signature' ) || 1 !== $envelope['version'] || ! is_int( $envelope['issued'] ) || ! is_string( $envelope['payload'] ) || ! is_string( $envelope['signature'] ) ) {
		return new WP_Error( 'frpdepot_fq_bad_envelope', 'The quote context is invalid.' );
	}
	$now = null === $now ? time() : (int) $now;
	if ( $envelope['issued'] > $now + 300 || $now - $envelope['issued'] > FRPDEPOT_FQ_CONTEXT_TTL ) {
		return new WP_Error( 'frpdepot_fq_expired', 'The quote context has expired. Please start again.' );
	}
	$expected = frpdepot_fq_sign( $envelope['payload'], $envelope['issued'] );
	if ( ! hash_equals( $expected, $envelope['signature'] ) ) {
		return new WP_Error( 'frpdepot_fq_signature', 'The quote context could not be verified.' );
	}
	$decoded = frpdepot_fq_base64url_decode( $envelope['payload'] );
	$payload = false === $decoded ? null : json_decode( $decoded, true );
	if ( ! frpdepot_fq_payload_schema_valid( $payload ) || strlen( (string) $decoded ) > FRPDEPOT_FQ_MAX_PAYLOAD_BYTES ) {
		return new WP_Error( 'frpdepot_fq_schema', 'The quote context is invalid.' );
	}
	if ( ! $rehydrate || 'direct' === $payload['source'] ) {
		return $payload;
	}
	if ( 'product' === $payload['source'] && 1 === count( $payload['items'] ) ) {
		$item  = $payload['items'][0];
		$fresh = frpdepot_fq_product_payload( $item['product_id'], $item['variation_id'], $item['quantity'] );
	} else {
		$fresh = frpdepot_fq_cart_payload();
	}
	if ( is_wp_error( $fresh ) || wp_json_encode( $fresh ) !== wp_json_encode( $payload ) ) {
		return new WP_Error( 'frpdepot_fq_changed', 'The selected products changed. Please start the quote request again.' );
	}
	return $payload;
}

function frpdepot_fq_context_from_request() {
	$source = isset( $_GET['frp_fq_source'] ) ? sanitize_key( wp_unslash( $_GET['frp_fq_source'] ) ) : 'direct';
	if ( 'cart' === $source ) {
		return frpdepot_fq_cart_payload();
	}
	if ( 'product' === $source ) {
		$product_id   = isset( $_GET['product_id'] ) ? absint( $_GET['product_id'] ) : 0;
		$variation_id = isset( $_GET['variation_id'] ) ? absint( $_GET['variation_id'] ) : 0;
		$quantity     = isset( $_GET['quantity'] ) ? absint( $_GET['quantity'] ) : 0;
		return frpdepot_fq_product_payload( $product_id, $variation_id, $quantity );
	}
	return frpdepot_fq_direct_payload();
}

function frpdepot_fq_option_value( $item, $needles ) {
	foreach ( $item['options'] as $label => $value ) {
		$haystack = strtolower( (string) $label );
		foreach ( $needles as $needle ) {
			if ( false !== strpos( $haystack, $needle ) ) {
				return $value;
			}
		}
	}
	return '';
}

function frpdepot_fq_lines( $payload, $callback ) {
	$lines = array();
	foreach ( $payload['items'] as $index => $item ) {
		$value   = call_user_func( $callback, $item );
		$lines[] = count( $payload['items'] ) > 1 ? ( $index + 1 ) . '. ' . $value : $value;
	}
	return implode( "\n", $lines );
}

function frpdepot_fq_form_values( $payload, $envelope ) {
	$first = count( $payload['items'] ) ? $payload['items'][0] : array( 'product_url' => '', 'product_id' => 0, 'variation_id' => 0 );
	return array(
		FRPDEPOT_FQ_FIELD_PRODUCT => frpdepot_fq_lines( $payload, function ( $item ) { return $item['product_name']; } ),
		FRPDEPOT_FQ_FIELD_SIZE => frpdepot_fq_lines( $payload, function ( $item ) { return frpdepot_fq_option_value( $item, array( 'size', 'diameter' ) ); } ),
		FRPDEPOT_FQ_FIELD_PRESSURE => frpdepot_fq_lines( $payload, function ( $item ) { return frpdepot_fq_option_value( $item, array( 'pressure', 'psi' ) ); } ),
		FRPDEPOT_FQ_FIELD_RESIN => frpdepot_fq_lines( $payload, function ( $item ) { return frpdepot_fq_option_value( $item, array( 'resin' ) ); } ),
		FRPDEPOT_FQ_FIELD_QUANTITY => frpdepot_fq_lines( $payload, function ( $item ) { return (string) $item['quantity']; } ),
		FRPDEPOT_FQ_FIELD_PRODUCT_URL => $first['product_url'],
		FRPDEPOT_FQ_FIELD_PRODUCT_ID => (string) ( 'cart' === $payload['source'] ? 0 : $first['product_id'] ),
		FRPDEPOT_FQ_FIELD_VARIATION_ID => (string) ( 'cart' === $payload['source'] ? 0 : $first['variation_id'] ),
		FRPDEPOT_FQ_FIELD_SOURCE_PAGE => $payload['source_page'],
		FRPDEPOT_FQ_FIELD_PAYLOAD => $envelope,
	);
}

/** Clone exactly one source notification's routing at activation; no address literal lives in this plugin. */
function frpdepot_fq_clone_notification_routing( $source ) {
	$allowed = array( 'to', 'toType', 'routing', 'from', 'fromName', 'replyTo', 'bcc', 'event', 'isActive' );
	$route   = array();
	foreach ( $allowed as $key ) {
		if ( array_key_exists( $key, $source ) ) {
			$route[ $key ] = $source[ $key ];
		}
	}
	$route['id']                = function_exists( 'wp_generate_uuid4' ) ? wp_generate_uuid4() : 'frpdepot-fq-admin';
	$route['name']              = 'Admin Notification';
	$route['subject']           = 'New product and freight quote request';
	$route['message']           = '{all_fields}';
	$route['disableAutoformat'] = false;
	$route['enableAttachments'] = false;
	return $route;
}

function frpdepot_fq_field( $id, $type, $label, $required, $extra = array() ) {
	return array_merge( array( 'id' => $id, 'type' => $type, 'label' => $label, 'isRequired' => (bool) $required ), $extra );
}

function frpdepot_fq_form_definition( $notification ) {
	return array(
		'title'       => FRPDEPOT_FQ_FORM_TITLE,
		'description' => FRPDEPOT_FQ_INTRO,
		'labelPlacement' => 'top_label',
		'button'      => array( 'type' => 'text', 'text' => FRPDEPOT_FQ_SUBMIT ),
		'fields'      => array(
			frpdepot_fq_field( 1, 'text', 'First name', true ),
			frpdepot_fq_field( 2, 'text', 'Last name', true ),
			frpdepot_fq_field( 3, 'email', 'Email', true ),
			frpdepot_fq_field( 4, 'text', 'Company', true ),
			frpdepot_fq_field( 5, 'textarea', 'Product', true ),
			frpdepot_fq_field( 6, 'textarea', 'Size', true ),
			frpdepot_fq_field( 7, 'textarea', 'Pressure rating', true ),
			frpdepot_fq_field( 8, 'textarea', 'Resin type', true ),
			frpdepot_fq_field( 9, 'text', 'Quantity', true ),
			frpdepot_fq_field( 10, 'select', 'Delivery country', true, array( 'choices' => array( array( 'text' => 'Canada', 'value' => 'Canada' ), array( 'text' => 'United States', 'value' => 'United States' ) ) ) ),
			frpdepot_fq_field( 11, 'text', 'Delivery postal/ZIP code', true ),
			frpdepot_fq_field( 12, 'textarea', 'Application or order notes', true ),
			frpdepot_fq_field( 13, 'phone', 'Phone', false ),
			frpdepot_fq_field( 14, 'date', 'Required delivery date', false, array( 'dateType' => 'datepicker', 'dateFormat' => 'mdy' ) ),
			frpdepot_fq_field( 15, 'fileupload', 'Drawing/specification upload', false, array( 'allowedExtensions' => 'pdf,jpg,jpeg,png', 'maxFileSize' => max( 1, min( 10, function_exists( 'wp_max_upload_size' ) ? (int) floor( wp_max_upload_size() / 1048576 ) : 10 ) ), 'multipleFiles' => false ) ),
			frpdepot_fq_field( 16, 'checkbox', 'Marketing consent', false, array( 'choices' => array( array( 'text' => 'Send me occasional FRP Depot product news and offers', 'value' => 'yes', 'isSelected' => false ) ) ) ),
			frpdepot_fq_field( 17, 'hidden', 'Product URL', false ),
			frpdepot_fq_field( 18, 'hidden', 'Product ID', false ),
			frpdepot_fq_field( 19, 'hidden', 'Variation ID', false ),
			frpdepot_fq_field( 20, 'hidden', 'Source page', false ),
			frpdepot_fq_field( 21, 'hidden', 'Cart line-item payload', false ),
			frpdepot_fq_field( 22, 'hidden', 'Controlled test marker', false ),
		),
		'confirmations' => array(
			'frpdepot-fq-confirmation' => array( 'id' => 'frpdepot-fq-confirmation', 'name' => 'Default Confirmation', 'type' => 'message', 'message' => FRPDEPOT_FQ_CONFIRMATION, 'isDefault' => true ),
		),
		'notifications' => array( $notification['id'] => $notification ),
		'frpdepotFqSpecSha256' => FRPDEPOT_FQ_SPEC_SHA256,
		'frpdepotFqMarker' => FRPDEPOT_FQ_FORM_MARKER,
	);
}

function frpdepot_fq_matching_forms() {
	if ( ! class_exists( 'GFAPI' ) ) {
		return array();
	}
	$matches = array();
	foreach ( (array) GFAPI::get_forms() as $form ) {
		if ( is_array( $form ) && isset( $form['title'] ) && FRPDEPOT_FQ_FORM_TITLE === $form['title'] ) {
			$matches[] = $form;
		}
	}
	return $matches;
}

function frpdepot_fq_form_id() {
	$forms = frpdepot_fq_matching_forms();
	return 1 === count( $forms ) && isset( $forms[0]['id'] ) ? (int) $forms[0]['id'] : 0;
}

function frpdepot_fq_form_is_exact( $form ) {
	if ( ! is_array( $form ) || ! isset( $form['frpdepotFqSpecSha256'], $form['frpdepotFqMarker'] ) || FRPDEPOT_FQ_SPEC_SHA256 !== $form['frpdepotFqSpecSha256'] || FRPDEPOT_FQ_FORM_MARKER !== $form['frpdepotFqMarker'] || ! isset( $form['fields'] ) || 22 !== count( $form['fields'] ) ) {
		return false;
	}
	$ids = array();
	foreach ( $form['fields'] as $field ) {
		$ids[] = (int) ( is_object( $field ) ? $field->id : $field['id'] );
	}
	return $ids === range( 1, 22 );
}

function frpdepot_fq_hash( $value ) {
	return hash( 'sha256', wp_json_encode( $value, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE ) );
}

function frpdepot_fq_receipt( $write, $before, $after ) {
	$allowed = array( 'form_upsert', 'page_upsert', 'contact_replace', 'form_restore', 'page_restore', 'contact_restore' );
	if ( ! in_array( $write, $allowed, true ) ) {
		throw new RuntimeException( 'Internal write receipt name is outside the fixed vocabulary.' );
	}
	$rows = get_option( FRPDEPOT_FQ_RECEIPTS_OPTION, array() );
	$rows = is_array( $rows ) ? $rows : array();
	$rows[] = array( 'write' => $write, 'before_sha256' => (string) $before,
		'after_sha256' => (string) $after, 'spec_sha256' => FRPDEPOT_FQ_SPEC_SHA256,
		'utc' => gmdate( 'c' ) );
	update_option( FRPDEPOT_FQ_RECEIPTS_OPTION, array_slice( $rows, -30 ), false );
}

/** Exactly Form 1 / Contact / one active Admin Notification. Recipient never leaves memory. */
function frpdepot_fq_source_notification() {
	$source = GFAPI::get_form( FRPDEPOT_FQ_FORM_SOURCE_ID );
	if ( ! is_array( $source ) || ! isset( $source['title'] ) || 'Contact' !== $source['title'] ) {
		throw new RuntimeException( 'Form 1 is not the verified Contact form.' );
	}
	$matches = array();
	foreach ( isset( $source['notifications'] ) && is_array( $source['notifications'] ) ? $source['notifications'] : array() as $notification ) {
		$name = is_array( $notification ) && isset( $notification['name'] ) ? (string) $notification['name'] : '';
		$active = is_array( $notification ) && ( ! isset( $notification['isActive'] ) || true === (bool) $notification['isActive'] );
		if ( $active && 'Admin Notification' === $name ) {
			$matches[] = $notification;
		}
	}
	if ( 1 !== count( $matches ) ) {
		throw new RuntimeException( 'Form 1 must expose exactly one active Admin Notification.' );
	}
	return $matches[0];
}

function frpdepot_fq_owned_page() {
	$page = get_page_by_path( FRPDEPOT_FQ_PAGE_SLUG, OBJECT, 'page' );
	if ( ! $page ) {
		return null;
	}
	if ( false === strpos( (string) $page->post_content, FRPDEPOT_FQ_PAGE_MARKER ) ) {
		throw new RuntimeException( 'The request-a-quote slug is occupied by an unowned page.' );
	}
	return $page;
}

function frpdepot_fq_page_content( $form_id ) {
	return FRPDEPOT_FQ_PAGE_MARKER . "\n<p>" . esc_html( FRPDEPOT_FQ_INTRO )
		. "</p>\n[gravityform id=\"" . (int) $form_id
		. "\" title=\"false\" description=\"false\" ajax=\"true\"]";
}

/** Immediate full reversible backup. Called before the first form/page/contact write. */
function frpdepot_fq_capture_backup( $notification ) {
	if ( get_option( FRPDEPOT_FQ_BACKUP_OPTION, false ) ) {
		throw new RuntimeException( 'A reversible freight-journey backup already exists.' );
	}
	$forms = frpdepot_fq_matching_forms();
	if ( count( $forms ) > 1 ) {
		throw new RuntimeException( 'More than one dedicated freight quote form exists.' );
	}
	$form = 1 === count( $forms ) ? GFAPI::get_form( (int) $forms[0]['id'] ) : null;
	if ( $form && ! frpdepot_fq_form_is_exact( $form ) ) {
		throw new RuntimeException( 'The existing dedicated freight quote form is not owned by this specification.' );
	}
	$page = frpdepot_fq_owned_page();
	$contact = get_post( FRPDEPOT_FQ_CONTACT_ID );
	if ( ! $contact || 'page' !== $contact->post_type || 1 !== substr_count( (string) $contact->post_content, FRPDEPOT_FQ_OLD_FAQ ) ) {
		throw new RuntimeException( 'Contact page 469 does not contain the exact old FAQ sentence once.' );
	}
	$backup = array(
		'schema' => 1, 'spec_sha256' => FRPDEPOT_FQ_SPEC_SHA256, 'created_utc' => gmdate( 'c' ),
		'source_notification_route_sha256' => frpdepot_fq_hash( frpdepot_fq_clone_notification_routing( $notification ) ),
		'form_existed' => (bool) $form, 'form' => $form,
		'page_existed' => (bool) $page,
		'page' => $page ? array( 'ID' => (int) $page->ID, 'post_title' => (string) $page->post_title,
			'post_name' => (string) $page->post_name, 'post_content' => (string) $page->post_content,
			'post_status' => (string) $page->post_status, 'post_type' => 'page' ) : null,
		'contact' => array( 'ID' => FRPDEPOT_FQ_CONTACT_ID, 'post_content' => (string) $contact->post_content ),
	);
	if ( ! add_option( FRPDEPOT_FQ_BACKUP_OPTION, $backup, '', false ) ) {
		throw new RuntimeException( 'The immediate reversible backup could not be saved.' );
	}
	return $backup;
}

function frpdepot_fq_upsert_form( $notification ) {
	$matches = frpdepot_fq_matching_forms();
	$existing = 1 === count( $matches ) ? GFAPI::get_form( (int) $matches[0]['id'] ) : null;
	$before = frpdepot_fq_hash( $existing );
	$definition = frpdepot_fq_form_definition( frpdepot_fq_clone_notification_routing( $notification ) );
	if ( $existing ) {
		$definition['id'] = (int) $existing['id'];
		$result = GFAPI::update_form( $definition );
		if ( is_wp_error( $result ) || false === $result ) {
			throw new RuntimeException( 'Gravity Forms refused the fixed form update.' );
		}
		$form_id = (int) $existing['id'];
	} else {
		$form_id = GFAPI::add_form( $definition );
		if ( is_wp_error( $form_id ) || (int) $form_id <= 0 ) {
			throw new RuntimeException( 'Gravity Forms refused the fixed form creation.' );
		}
		$form_id = (int) $form_id;
	}
	update_option( FRPDEPOT_FQ_FORM_ID_OPTION, $form_id, false );
	$after = GFAPI::get_form( $form_id );
	if ( ! frpdepot_fq_form_is_exact( $after ) ) {
		throw new RuntimeException( 'Fixed form read-at-write verification failed.' );
	}
	frpdepot_fq_receipt( 'form_upsert', $before, frpdepot_fq_hash( $after ) );
	return $form_id;
}

function frpdepot_fq_upsert_page( $form_id ) {
	$existing = frpdepot_fq_owned_page();
	$before = frpdepot_fq_hash( $existing ? array( $existing->post_title, $existing->post_name, $existing->post_content, $existing->post_status ) : null );
	$post = array( 'post_title' => FRPDEPOT_FQ_PAGE_TITLE, 'post_name' => FRPDEPOT_FQ_PAGE_SLUG,
		'post_content' => frpdepot_fq_page_content( $form_id ), 'post_status' => 'publish', 'post_type' => 'page' );
	if ( $existing ) {
		$post['ID'] = (int) $existing->ID;
		$page_id = wp_update_post( wp_slash( $post ), true );
	} else {
		$page_id = wp_insert_post( wp_slash( $post ), true );
	}
	if ( is_wp_error( $page_id ) || (int) $page_id <= 0 ) {
		throw new RuntimeException( 'WordPress refused the fixed request-a-quote page write.' );
	}
	$page_id = (int) $page_id;
	update_option( FRPDEPOT_FQ_PAGE_ID_OPTION, $page_id, false );
	$after = get_post( $page_id );
	if ( ! $after || FRPDEPOT_FQ_PAGE_TITLE !== $after->post_title || FRPDEPOT_FQ_PAGE_SLUG !== $after->post_name || false === strpos( $after->post_content, FRPDEPOT_FQ_PAGE_MARKER ) ) {
		throw new RuntimeException( 'Fixed quote-page read-at-write verification failed.' );
	}
	frpdepot_fq_receipt( 'page_upsert', $before, frpdepot_fq_hash( array( $after->post_title, $after->post_name, $after->post_content, $after->post_status ) ) );
	return $page_id;
}

function frpdepot_fq_replace_contact() {
	$contact = get_post( FRPDEPOT_FQ_CONTACT_ID );
	$before = (string) $contact->post_content;
	$content = str_replace( FRPDEPOT_FQ_OLD_FAQ, FRPDEPOT_FQ_NEW_FAQ_TEXT, $before, $count );
	if ( 1 !== $count ) {
		throw new RuntimeException( 'Exact Contact FAQ replacement count changed before write.' );
	}
	$result = wp_update_post( wp_slash( array( 'ID' => FRPDEPOT_FQ_CONTACT_ID, 'post_content' => $content ) ), true );
	if ( is_wp_error( $result ) || FRPDEPOT_FQ_CONTACT_ID !== (int) $result ) {
		throw new RuntimeException( 'WordPress refused the exact Contact FAQ replacement.' );
	}
	$after = get_post( FRPDEPOT_FQ_CONTACT_ID );
	if ( ! $after || 1 !== substr_count( (string) $after->post_content, FRPDEPOT_FQ_NEW_FAQ_TEXT ) || 0 !== substr_count( (string) $after->post_content, FRPDEPOT_FQ_OLD_FAQ ) ) {
		throw new RuntimeException( 'Contact FAQ read-at-write verification failed.' );
	}
	frpdepot_fq_receipt( 'contact_replace', hash( 'sha256', $before ), hash( 'sha256', (string) $after->post_content ) );
}

function frpdepot_fq_restore_backup() {
	$backup = get_option( FRPDEPOT_FQ_BACKUP_OPTION, false );
	if ( ! is_array( $backup ) || FRPDEPOT_FQ_SPEC_SHA256 !== $backup['spec_sha256'] ) {
		throw new RuntimeException( 'No matching reversible freight-journey backup exists.' );
	}
	$forms = frpdepot_fq_matching_forms();
	$current = 1 === count( $forms ) ? GFAPI::get_form( (int) $forms[0]['id'] ) : null;
	$before = frpdepot_fq_hash( $current );
	if ( $backup['form_existed'] ) {
		$result = GFAPI::update_form( $backup['form'] );
		if ( is_wp_error( $result ) || false === $result ) {
			throw new RuntimeException( 'Could not restore the previous fixed form.' );
		}
		update_option( FRPDEPOT_FQ_FORM_ID_OPTION, (int) $backup['form']['id'], false );
	} elseif ( $current ) {
		$result = GFAPI::delete_form( (int) $current['id'] );
		if ( is_wp_error( $result ) || false === $result ) {
			throw new RuntimeException( 'Could not remove the activation-created form.' );
		}
		delete_option( FRPDEPOT_FQ_FORM_ID_OPTION );
	}
	frpdepot_fq_receipt( 'form_restore', $before, frpdepot_fq_hash( $backup['form'] ) );

	$page = frpdepot_fq_owned_page();
	$before = frpdepot_fq_hash( $page ? $page->post_content : null );
	if ( $backup['page_existed'] ) {
		$result = wp_update_post( wp_slash( $backup['page'] ), true );
		if ( is_wp_error( $result ) ) {
			throw new RuntimeException( 'Could not restore the previous quote page.' );
		}
		update_option( FRPDEPOT_FQ_PAGE_ID_OPTION, (int) $backup['page']['ID'], false );
	} elseif ( $page ) {
		if ( ! wp_delete_post( (int) $page->ID, true ) ) {
			throw new RuntimeException( 'Could not remove the activation-created quote page.' );
		}
		delete_option( FRPDEPOT_FQ_PAGE_ID_OPTION );
	}
	frpdepot_fq_receipt( 'page_restore', $before, frpdepot_fq_hash( $backup['page'] ) );

	$contact = get_post( FRPDEPOT_FQ_CONTACT_ID );
	$before = $contact ? hash( 'sha256', (string) $contact->post_content ) : '';
	$result = wp_update_post( wp_slash( $backup['contact'] ), true );
	$after = get_post( FRPDEPOT_FQ_CONTACT_ID );
	if ( is_wp_error( $result ) || ! $after || (string) $after->post_content !== (string) $backup['contact']['post_content'] ) {
		throw new RuntimeException( 'Could not verify Contact page restore.' );
	}
	frpdepot_fq_receipt( 'contact_restore', $before, hash( 'sha256', (string) $after->post_content ) );
	update_option( FRPDEPOT_FQ_STATE_OPTION, array( 'status' => 'rolled_back', 'spec_sha256' => FRPDEPOT_FQ_SPEC_SHA256, 'utc' => gmdate( 'c' ) ), false );
	delete_option( FRPDEPOT_FQ_BACKUP_OPTION );
}

function frpdepot_fq_activate() {
	if ( ! frpdepot_fq_guard_ready() ) {
		wp_die( 'FRP Depot Freight Quote Journey requires the active Freight Checkout Guard version 1.0.1.' );
	}
	if ( ! class_exists( 'GFAPI' ) ) {
		wp_die( 'FRP Depot Freight Quote Journey requires Gravity Forms.' );
	}
	if ( ! add_option( FRPDEPOT_FQ_ACTIVATION_LOCK, array( 'utc' => gmdate( 'c' ) ), '', false ) ) {
		wp_die( 'Freight quote journey activation is already locked.' );
	}
	$backed_up = false;
	try {
		$notification = frpdepot_fq_source_notification();
		frpdepot_fq_capture_backup( $notification );
		$backed_up = true;
		$form_id = frpdepot_fq_upsert_form( $notification );
		$page_id = frpdepot_fq_upsert_page( $form_id );
		frpdepot_fq_replace_contact();
		update_option( FRPDEPOT_FQ_STATE_OPTION, array( 'status' => 'applied', 'spec_sha256' => FRPDEPOT_FQ_SPEC_SHA256,
			'form_id' => $form_id, 'page_id' => $page_id, 'contact_id' => FRPDEPOT_FQ_CONTACT_ID,
			'utc' => gmdate( 'c' ) ), false );
		frpdepot_fq_add_rewrite();
		if ( function_exists( 'flush_rewrite_rules' ) ) {
			flush_rewrite_rules( false );
		}
	} catch ( Throwable $error ) {
		if ( $backed_up ) {
			try {
				frpdepot_fq_restore_backup();
			} catch ( Throwable $rollback_error ) {
				update_option( FRPDEPOT_FQ_STATE_OPTION, array( 'status' => 'activation_failed_rollback_unverified', 'spec_sha256' => FRPDEPOT_FQ_SPEC_SHA256, 'utc' => gmdate( 'c' ) ), false );
			}
		}
		delete_option( FRPDEPOT_FQ_ACTIVATION_LOCK );
		wp_die( 'Freight quote journey activation failed closed.' );
	}
	delete_option( FRPDEPOT_FQ_ACTIVATION_LOCK );
}

function frpdepot_fq_deactivate() {
	if ( function_exists( 'flush_rewrite_rules' ) ) {
		flush_rewrite_rules( false );
	}
}

function frpdepot_fq_add_rewrite() {
	add_rewrite_rule( '^request-a-quote/?$', 'index.php?' . FRPDEPOT_FQ_QUERY_VAR . '=1', 'top' );
}

function frpdepot_fq_query_vars( $vars ) {
	$vars[] = FRPDEPOT_FQ_QUERY_VAR;
	return $vars;
}

function frpdepot_fq_available_variation( $data, $parent, $variation ) {
	unset( $parent );
	$data['frpdepot_fq_requires_quote'] = frpdepot_fq_product_requires_quote( $variation );
	return $data;
}

function frpdepot_fq_render_product_panel() {
	global $product;
	if ( ! is_object( $product ) || ! method_exists( $product, 'get_id' ) ) {
		return;
	}
	$is_variable = method_exists( $product, 'is_type' ) && $product->is_type( 'variable' );
	$requires    = $is_variable ? false : frpdepot_fq_product_requires_quote( $product );
	$classes     = 'frpdepot-fq-product-panel' . ( $requires ? '' : ' frpdepot-fq-hidden' );
	$url         = $requires ? frpdepot_fq_quote_url( array( 'frp_fq_source' => 'product', 'product_id' => (int) $product->get_id(), 'variation_id' => 0, 'quantity' => 1 ) ) : '';
	echo '<section class="' . esc_attr( $classes ) . '" aria-live="polite">';
	echo '<h3>' . esc_html( FRPDEPOT_FQ_HEADING ) . '</h3><p>' . esc_html( FRPDEPOT_FQ_PRODUCT_COPY ) . '</p>';
	echo '<label>' . esc_html( 'Quantity' ) . ' <input class="frpdepot-fq-quantity" type="number" min="1" max="' . esc_attr( FRPDEPOT_FQ_MAX_QUANTITY ) . '" step="1" value="1"></label>';
	echo '<a class="button frpdepot-fq-product-button" href="' . esc_url( $url ) . '"' . ( $requires ? '' : ' aria-disabled="true" tabindex="-1"' ) . '>' . esc_html( FRPDEPOT_FQ_BUTTON ) . '</a>';
	echo '</section>';
}

function frpdepot_fq_cart_panel_markup() {
	return '<section class="frpdepot-fq-cart-panel" role="status"><h2>' . esc_html( FRPDEPOT_FQ_HEADING ) . '</h2><p>' . esc_html( FRPDEPOT_FQ_CART_COPY ) . '</p><a class="button frpdepot-fq-cart-button" href="' . esc_url( frpdepot_fq_quote_url( array( 'frp_fq_source' => 'cart' ) ) ) . '">' . esc_html( FRPDEPOT_FQ_BUTTON ) . '</a></section>';
}

function frpdepot_fq_render_cart_panel() {
	if ( frpdepot_fq_cart_requires_quote() ) {
		echo frpdepot_fq_cart_panel_markup(); // phpcs:ignore WordPress.Security.EscapeOutput -- fixed escaped markup.
	}
}

function frpdepot_fq_render_block_cart( $block_content, $block ) {
	unset( $block );
	return frpdepot_fq_cart_requires_quote() ? frpdepot_fq_cart_panel_markup() . $block_content : $block_content;
}

function frpdepot_fq_checkout_url( $url ) {
	return frpdepot_fq_cart_requires_quote() ? frpdepot_fq_quote_url( array( 'frp_fq_source' => 'cart' ) ) : $url;
}

function frpdepot_fq_shipping_calculator( $enabled ) {
	return frpdepot_fq_cart_requires_quote() ? false : $enabled;
}

function frpdepot_fq_body_class( $classes ) {
	if ( frpdepot_fq_cart_requires_quote() ) {
		$classes[] = 'frpdepot-fq-cart-blocked';
	}
	return $classes;
}

function frpdepot_fq_filter_contact_faq( $content ) {
	if ( ! function_exists( 'is_page' ) || ( ! is_page( 469 ) && ! is_page( 'contact' ) ) ) {
		return $content;
	}
	return 1 === substr_count( $content, FRPDEPOT_FQ_OLD_FAQ )
		? str_replace( FRPDEPOT_FQ_OLD_FAQ, FRPDEPOT_FQ_NEW_FAQ_HTML, $content ) : $content;
}

function frpdepot_fq_cart_state() {
	check_ajax_referer( 'frpdepot_fq_cart_state', 'nonce' );
	wp_send_json_success( array( 'quote_required' => frpdepot_fq_cart_requires_quote() ) );
}

function frpdepot_fq_enqueue_assets() {
	if ( function_exists( 'is_admin' ) && is_admin() ) {
		return;
	}
	wp_enqueue_style( 'frpdepot-fq-journey', plugins_url( 'assets/journey.css', __FILE__ ), array(), FRPDEPOT_FQ_VERSION );
	wp_enqueue_script( 'frpdepot-fq-journey', plugins_url( 'assets/journey.js', __FILE__ ), array( 'jquery' ), FRPDEPOT_FQ_VERSION, true );
	wp_localize_script(
		'frpdepot-fq-journey',
		'frpdepotFqJourney',
		array(
			'quoteUrl'     => frpdepot_fq_quote_url(),
			'cartStateUrl' => function_exists( 'admin_url' ) ? admin_url( 'admin-ajax.php' ) : '',
			'cartNonce'    => wp_create_nonce( 'frpdepot_fq_cart_state' ),
			'cartBlocked'  => frpdepot_fq_cart_requires_quote(),
			'productId'    => function_exists( 'is_product' ) && is_product() && function_exists( 'get_the_ID' ) ? (int) get_the_ID() : 0,
		)
	);
	wp_enqueue_script( 'frpdepot-fq-analytics', plugins_url( 'assets/analytics.js', __FILE__ ), array(), FRPDEPOT_FQ_VERSION, true );
}

function frpdepot_fq_is_form( $form ) {
	return is_array( $form ) && isset( $form['id'] ) && (int) $form['id'] === frpdepot_fq_form_id();
}

function frpdepot_fq_populate_form( $form ) {
	if ( ! frpdepot_fq_is_form( $form ) ) {
		return $form;
	}
	$payload = frpdepot_fq_context_from_request();
	if ( is_wp_error( $payload ) ) {
		return $form;
	}
	$envelope = frpdepot_fq_make_envelope( $payload );
	if ( is_wp_error( $envelope ) ) {
		return $form;
	}
	$values  = frpdepot_fq_form_values( $payload, $envelope );
	$carried = 'direct' !== $payload['source'];
	foreach ( $form['fields'] as &$field ) {
		$id = (int) ( is_object( $field ) ? $field->id : $field['id'] );
		if ( array_key_exists( $id, $values ) ) {
			if ( is_object( $field ) ) {
				$field->defaultValue = $values[ $id ];
				if ( $carried && in_array( $id, array( 5, 6, 7, 8, 9 ), true ) ) {
					$field->cssClass = trim( (string) $field->cssClass . ' frpdepot-fq-readonly' );
				}
			} else {
				$field['defaultValue'] = $values[ $id ];
			}
		}
	}
	unset( $field );
	return $form;
}

function frpdepot_fq_posted_envelope() {
	$key = 'input_' . FRPDEPOT_FQ_FIELD_PAYLOAD;
	return isset( $_POST[ $key ] ) ? wp_unslash( $_POST[ $key ] ) : '';
}

function frpdepot_fq_validation( $validation ) {
	if ( ! isset( $validation['form'] ) || ! frpdepot_fq_is_form( $validation['form'] ) ) {
		return $validation;
	}
	$payload = frpdepot_fq_verify_envelope( frpdepot_fq_posted_envelope() );
	if ( ! is_wp_error( $payload ) ) {
		$GLOBALS['frpdepot_fq_verified_payload'] = $payload;
		return $validation;
	}
	$validation['is_valid'] = false;
	foreach ( $validation['form']['fields'] as $field ) {
		if ( (int) $field->id === FRPDEPOT_FQ_FIELD_PAYLOAD ) {
			$field->failed_validation = true;
			$field->validation_message = $payload->get_error_message();
		}
	}
	return $validation;
}

function frpdepot_fq_prepare_submission( $form ) {
	if ( ! frpdepot_fq_is_form( $form ) ) {
		return $form;
	}
	$payload = isset( $GLOBALS['frpdepot_fq_verified_payload'] ) ? $GLOBALS['frpdepot_fq_verified_payload'] : frpdepot_fq_verify_envelope( frpdepot_fq_posted_envelope() );
	if ( is_wp_error( $payload ) ) {
		return $form;
	}
	$envelope = frpdepot_fq_posted_envelope();
	$values   = frpdepot_fq_form_values( $payload, $envelope );
	foreach ( $values as $id => $value ) {
		if ( 'direct' !== $payload['source'] || in_array( $id, array( 17, 18, 19, 20, 21 ), true ) ) {
			$_POST[ 'input_' . $id ] = $value;
		}
	}
	return $form;
}

function frpdepot_fq_field_content( $content, $field, $value, $entry_id, $form_id ) {
	unset( $value, $entry_id );
	if ( (int) $form_id !== frpdepot_fq_form_id() || false === strpos( (string) $field->cssClass, 'frpdepot-fq-readonly' ) ) {
		return $content;
	}
	return preg_replace( '/<(input|textarea)\b/', '<$1 readonly aria-readonly="true"', $content, 1 );
}

function frpdepot_fq_confirmation( $confirmation, $form, $entry, $ajax ) {
	unset( $ajax );
	if ( ! frpdepot_fq_is_form( $form ) ) {
		return $confirmation;
	}
	$raw     = isset( $entry[ (string) FRPDEPOT_FQ_FIELD_PAYLOAD ] ) ? $entry[ (string) FRPDEPOT_FQ_FIELD_PAYLOAD ] : '';
	$payload = frpdepot_fq_verify_envelope( $raw, null, false );
	if ( is_wp_error( $payload ) ) {
		return '<p>' . esc_html( FRPDEPOT_FQ_CONFIRMATION ) . '</p>';
	}
	$first = count( $payload['items'] ) ? $payload['items'][0] : array( 'product_id' => 0, 'variation_id' => 0 );
	$product_id   = 'cart' === $payload['source'] ? 0 : (int) $first['product_id'];
	$variation_id = 'cart' === $payload['source'] ? 0 : (int) $first['variation_id'];
	$source_page  = in_array( $payload['source'], array( 'product', 'cart', 'direct' ), true ) ? $payload['source'] : 'direct';
	$entry_id     = isset( $entry['id'] ) ? (int) $entry['id'] : 0;
	$success_key  = hash_hmac( 'sha256', (string) $form['id'] . '|' . (string) $entry_id, wp_salt( 'nonce' ) );
	return '<p>' . esc_html( FRPDEPOT_FQ_CONFIRMATION ) . '</p><span class="frpdepot-fq-success" hidden aria-hidden="true" data-success-key="' . esc_attr( $success_key ) . '" data-form-id="' . esc_attr( (int) $form['id'] ) . '" data-product-id="' . esc_attr( $product_id ) . '" data-variation-id="' . esc_attr( $variation_id ) . '" data-source-page="' . esc_attr( $source_page ) . '"></span>';
}

function frpdepot_fq_template_redirect() {
	if ( frpdepot_fq_cart_requires_quote() && function_exists( 'is_checkout' ) && is_checkout() && ( ! isset( $_SERVER['REQUEST_METHOD'] ) || 'GET' === strtoupper( (string) $_SERVER['REQUEST_METHOD'] ) ) ) {
		wp_safe_redirect( frpdepot_fq_quote_url( array( 'frp_fq_source' => 'cart' ) ), 302 );
		exit;
	}
	if ( '1' !== (string) get_query_var( FRPDEPOT_FQ_QUERY_VAR ) ) {
		return;
	}
	status_header( 200 );
	nocache_headers();
	if ( function_exists( 'get_header' ) ) {
		get_header();
	}
	echo '<main id="primary" class="site-main frpdepot-fq-page"><article><h1>' . esc_html( FRPDEPOT_FQ_PAGE_HEADING ) . '</h1><p class="frpdepot-fq-intro">' . esc_html( FRPDEPOT_FQ_INTRO ) . '</p>';
	$form_id = frpdepot_fq_form_id();
	if ( $form_id > 0 && function_exists( 'gravity_form' ) ) {
		gravity_form( $form_id, false, false, false, null, true, 0, true );
	} else {
		echo '<p role="alert">' . esc_html( 'The quote form is temporarily unavailable.' ) . '</p>';
	}
	echo '</article></main>';
	if ( function_exists( 'get_footer' ) ) {
		get_footer();
	}
	exit;
}

function frpdepot_fq_boot() {
	if ( ! frpdepot_fq_guard_ready() ) {
		return;
	}
	add_filter( 'woocommerce_available_variation', 'frpdepot_fq_available_variation', 100, 3 );
	add_action( 'woocommerce_single_product_summary', 'frpdepot_fq_render_product_panel', 31 );
	add_action( 'woocommerce_before_cart', 'frpdepot_fq_render_cart_panel', 5 );
	add_filter( 'render_block_woocommerce/cart', 'frpdepot_fq_render_block_cart', 10, 2 );
	add_filter( 'woocommerce_get_checkout_url', 'frpdepot_fq_checkout_url', 100 );
	add_filter( 'woocommerce_shipping_calculator_enable', 'frpdepot_fq_shipping_calculator', 100 );
	add_filter( 'body_class', 'frpdepot_fq_body_class', 100 );
	add_filter( 'the_content', 'frpdepot_fq_filter_contact_faq', 100 );
	add_action( 'wp_enqueue_scripts', 'frpdepot_fq_enqueue_assets', 100 );
	add_action( 'wp_ajax_frpdepot_fq_cart_state', 'frpdepot_fq_cart_state' );
	add_action( 'wp_ajax_nopriv_frpdepot_fq_cart_state', 'frpdepot_fq_cart_state' );
	add_filter( 'gform_pre_render', 'frpdepot_fq_populate_form' );
	add_filter( 'gform_pre_validation', 'frpdepot_fq_populate_form' );
	add_filter( 'gform_validation', 'frpdepot_fq_validation' );
	add_filter( 'gform_pre_submission_filter', 'frpdepot_fq_prepare_submission' );
	add_filter( 'gform_field_content', 'frpdepot_fq_field_content', 10, 5 );
	add_filter( 'gform_confirmation', 'frpdepot_fq_confirmation', 10, 4 );
}

register_activation_hook( __FILE__, 'frpdepot_fq_activate' );
register_deactivation_hook( __FILE__, 'frpdepot_fq_deactivate' );
add_action( 'plugins_loaded', 'frpdepot_fq_boot', 20 );
add_action( 'init', 'frpdepot_fq_add_rewrite' );
add_filter( 'query_vars', 'frpdepot_fq_query_vars' );
add_action( 'template_redirect', 'frpdepot_fq_template_redirect', 1 );
