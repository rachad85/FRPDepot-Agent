<?php
/**
 * Offline unit tests for the FRP Depot Freight Checkout Guard.
 *
 * Runs the REAL plugin file with minimal WordPress/WooCommerce stubs. No
 * WordPress, no WooCommerce, no database and no network are required.
 *
 *     php tests/test-freight-guard.php
 *
 * Exit code 0 means every scenario in ../freight_guard_scenarios.json passed and
 * every behavioural check below held.
 */

declare( strict_types = 1 );

define( 'ABSPATH', __DIR__ . '/' );

$GLOBALS['frpdepot_fcg_registered'] = array();
$GLOBALS['frpdepot_fcg_notices']    = array();
$GLOBALS['frpdepot_fcg_scripts']    = array();
$GLOBALS['frpdepot_fcg_wc']         = null;
$GLOBALS['frpdepot_fcg_is_cart']    = false;

function add_filter( $hook, $callback, $priority = 10, $args = 1 ) {
	$GLOBALS['frpdepot_fcg_registered'][] = array( 'type' => 'filter', 'hook' => $hook,
		'callback' => $callback, 'priority' => $priority, 'args' => $args );
	return true;
}

function add_action( $hook, $callback, $priority = 10, $args = 1 ) {
	$GLOBALS['frpdepot_fcg_registered'][] = array( 'type' => 'action', 'hook' => $hook,
		'callback' => $callback, 'priority' => $priority, 'args' => $args );
	return true;
}

function esc_html( $text ) {
	return htmlspecialchars( (string) $text, ENT_QUOTES, 'UTF-8' );
}

/* --- WooCommerce notice queue -------------------------------------------- */

function wc_add_notice( $message, $type = 'success' ) {
	$GLOBALS['frpdepot_fcg_notices'][] = array( 'message' => $message, 'type' => $type );
}

function wc_has_notice( $message, $type = 'success' ) {
	foreach ( $GLOBALS['frpdepot_fcg_notices'] as $notice ) {
		if ( $notice['message'] === $message && $notice['type'] === $type ) {
			return true;
		}
	}
	return false;
}

/** WooCommerce clears the notice queue after rendering checkout/cart-errors.php. */
function wc_clear_notices() {
	$GLOBALS['frpdepot_fcg_notices'] = array();
}

/* --- Script registration -------------------------------------------------- */

function plugins_url( $path = '', $plugin = '' ) {
	return 'https://example.invalid/wp-content/plugins/frpdepot-freight-checkout-guard/' . ltrim( (string) $path, '/' );
}

function wp_register_script( $handle, $src = '', $deps = array(), $version = false, $in_footer = false ) {
	$GLOBALS['frpdepot_fcg_scripts'][ $handle ] = array(
		'src' => $src, 'deps' => $deps, 'version' => $version,
		'in_footer' => $in_footer, 'enqueued' => false,
	);
	return true;
}

function wp_enqueue_script( $handle ) {
	if ( isset( $GLOBALS['frpdepot_fcg_scripts'][ $handle ] ) ) {
		$GLOBALS['frpdepot_fcg_scripts'][ $handle ]['enqueued'] = true;
	}
}

function is_admin() {
	return false;
}

function is_cart() {
	return (bool) $GLOBALS['frpdepot_fcg_is_cart'];
}

/* --- Cart stubs ----------------------------------------------------------- */

class FRPDEPOT_FCG_Stub_Product {
	private $sku;
	private $shipping_class;

	public function __construct( string $sku, string $shipping_class ) {
		$this->sku            = $sku;
		$this->shipping_class = $shipping_class;
	}
	public function get_sku() {
		return $this->sku;
	}
	public function get_shipping_class() {
		return $this->shipping_class;
	}
}

class FRPDEPOT_FCG_Stub_Cart {
	public $contents = array();
	public function get_cart() {
		return $this->contents;
	}
}

class FRPDEPOT_FCG_Stub_WC {
	public $cart;
	public function __construct() {
		$this->cart = new FRPDEPOT_FCG_Stub_Cart();
	}
}

function WC() {
	return $GLOBALS['frpdepot_fcg_wc'];
}

/** Errors collector shaped like WP_Error / the Store API error bag. */
class FRPDEPOT_FCG_Stub_Errors {
	public $added = array();
	public function add( $code, $message ) {
		$this->added[] = array( 'code' => $code, 'message' => $message );
	}
}

require __DIR__ . '/../frpdepot-freight-checkout-guard/frpdepot-freight-checkout-guard.php';

$failures = 0;
$passes   = 0;

function check( $condition, $label ) {
	global $failures, $passes;
	if ( $condition ) {
		$passes++;
		return;
	}
	$failures++;
	fwrite( STDERR, "FAIL: {$label}\n" );
}

/** Put a blocked (freight) cart in place and clear all per-request state. */
function frpdepot_fcg_test_reset_blocked_cart() {
	$GLOBALS['frpdepot_fcg_wc'] = new FRPDEPOT_FCG_Stub_WC();
	// The shipped allowlist is empty, so any real line is unverified => blocked.
	$GLOBALS['frpdepot_fcg_wc']->cart->contents = array(
		'line-1' => array(
			'product_id'   => 1423,
			'variation_id' => 1424,
			'data'         => new FRPDEPOT_FCG_Stub_Product( 'ELDN25150PSI411', 'freight-quote-required' ),
		),
	);
	$GLOBALS['frpdepot_fcg_message_emitted'] = false;
	$GLOBALS['frpdepot_fcg_notices']         = array();
	$GLOBALS['frpdepot_fcg_scripts']         = array();
	$GLOBALS['frpdepot_fcg_is_cart']         = true;
}

/** Put an empty (unblocked) cart in place. */
function frpdepot_fcg_test_reset_empty_cart() {
	frpdepot_fcg_test_reset_blocked_cart();
	$GLOBALS['frpdepot_fcg_wc']->cart->contents = array();
}

/** Capture whatever a callable echoes. */
function frpdepot_fcg_test_capture( callable $fn ): string {
	ob_start();
	$fn();
	return (string) ob_get_clean();
}

/** How many times the exact sentence appears in a blob of markup. */
function frpdepot_fcg_test_count_message( string $html ): int {
	return substr_count( $html, FRPDEPOT_FCG_MESSAGE );
}

$scenario_path = __DIR__ . '/../freight_guard_scenarios.json';
$scenarios     = json_decode( (string) file_get_contents( $scenario_path ), true );
check( is_array( $scenarios ), 'scenario file decodes' );

/* The exact customer message must survive verbatim. */
check(
	FRPDEPOT_FCG_MESSAGE === $scenarios['message'],
	'message constant is exactly "' . $scenarios['message'] . '"'
);
check(
	FRPDEPOT_FCG_MESSAGE === 'Contact us for a freight quote.',
	'message constant is the literal approved wording'
);
check(
	FRPDEPOT_FCG_FREIGHT_SLUG === $scenarios['freight_slug'],
	'freight slug constant matches the scenario file'
);

$now_ts = strtotime( $scenarios['now_utc'] );
check( false !== $now_ts, 'scenario now_utc parses' );

foreach ( $scenarios['cases'] as $case ) {
	$name      = $case['name'];
	$allowlist = $scenarios['allowlists'][ $case['allowlist'] ];
	$decision  = frpdepot_fcg_decide( $case['items'], $allowlist, $now_ts );

	check(
		$decision['freight_required'] === $case['expect_freight'],
		"{$name}: freight_required should be " . var_export( $case['expect_freight'], true )
	);
	check(
		$decision['reasons'] === $case['expect_reasons'],
		"{$name}: reasons should be " . json_encode( $case['expect_reasons'] )
			. ' but were ' . json_encode( $decision['reasons'] )
	);
	check(
		$decision['message'] === $scenarios['message'],
		"{$name}: message is preserved exactly"
	);
}

/* Every blocking surface must be registered, including the ones that stop a
   direct submission rather than merely hiding the rate list, and the ones added
   in 1.0.1 to make the message visible. */
$required_hooks = array(
	'woocommerce_package_rates',
	'woocommerce_no_shipping_available_html',
	'woocommerce_cart_no_shipping_available_html',
	'woocommerce_check_cart_items',
	'woocommerce_checkout_process',
	'woocommerce_after_checkout_validation',
	'woocommerce_store_api_cart_errors',
	'woocommerce_store_api_checkout_update_order_from_request',
	'woocommerce_before_template_part',
	'woocommerce_cart_has_errors',
	'wp_enqueue_scripts',
);
$registered_hooks = array_column( $GLOBALS['frpdepot_fcg_registered'], 'hook' );
foreach ( $required_hooks as $hook ) {
	check( in_array( $hook, $registered_hooks, true ), "hook registered: {$hook}" );
}

/* woocommerce_before_template_part must receive all four template arguments,
   otherwise the template-name narrowing cannot work. */
foreach ( $GLOBALS['frpdepot_fcg_registered'] as $registration ) {
	if ( 'woocommerce_before_template_part' === $registration['hook'] ) {
		check( 4 === $registration['args'],
			'woocommerce_before_template_part is registered for 4 arguments' );
	}
}

/* The declared version must match the plugin header, or WordPress will show one
   version while the code is another. */
$plugin_source = (string) file_get_contents(
	__DIR__ . '/../frpdepot-freight-checkout-guard/frpdepot-freight-checkout-guard.php'
);
preg_match( '/^\s*\*\s*Version:\s*(\S+)\s*$/m', $plugin_source, $header_version );
check( isset( $header_version[1] ), 'plugin header declares a version' );
check(
	isset( $header_version[1] ) && $header_version[1] === FRPDEPOT_FCG_VERSION,
	'header version matches FRPDEPOT_FCG_VERSION'
);
check( version_compare( FRPDEPOT_FCG_VERSION, '1.0.0', '>' ),
	'version is newer than the withdrawn 1.0.0' );

/* The bundled Blocks script must carry the identical sentence. */
$script_path = __DIR__ . '/../frpdepot-freight-checkout-guard/' . FRPDEPOT_FCG_SCRIPT_FILE;
check( is_readable( $script_path ), 'bundled Blocks script is present' );
$script_source = (string) file_get_contents( $script_path );
check(
	false !== strpos( $script_source, "var MESSAGE = '" . FRPDEPOT_FCG_MESSAGE . "';" ),
	'bundled script message is byte-identical to the PHP constant'
);

/* ---------------------------------------------------------------------------
 * Rate suppression.
 * ------------------------------------------------------------------------ */
frpdepot_fcg_test_reset_blocked_cart();
$freight_rates = frpdepot_fcg_filter_package_rates(
	array( 'ups:01' => 'UPS Standard', 'flat_rate:2' => 'Flat rate' ),
	array()
);
check( is_array( $freight_rates ), 'package rate filter returns an array' );
check( array() === $freight_rates, 'a blocked cart loses every shipping rate' );

frpdepot_fcg_test_reset_empty_cart();
$open_rates = frpdepot_fcg_filter_package_rates( array( 'ups:01' => 'UPS Standard' ), array() );
check( array( 'ups:01' => 'UPS Standard' ) === $open_rates,
	'an unblocked cart keeps its rates untouched' );

/* ---------------------------------------------------------------------------
 * REGRESSION, 2026-08-09: the generic cart-errors shell must not replace the
 * required message. In 1.0.0 the notice was added and then destroyed unprinted
 * by wc_clear_notices(), so the customer saw only WooCommerce's generic text.
 * ------------------------------------------------------------------------ */
frpdepot_fcg_test_reset_blocked_cart();

// 1. WooCommerce collects cart-item errors. This is what blocks the checkout.
frpdepot_fcg_check_cart_items();
check( wc_has_notice( FRPDEPOT_FCG_MESSAGE, 'error' ),
	'cart-items check adds the blocking error notice' );
check( 1 === count( $GLOBALS['frpdepot_fcg_notices'] ),
	'exactly one blocking notice is queued' );

// 2. Because an error exists, WooCommerce renders the generic shell instead of
//    the checkout form. Our message must be on the page BEFORE that shell.
$shell_output = frpdepot_fcg_test_capture( function () {
	frpdepot_fcg_before_template_part( 'checkout/cart-errors.php', '', '', array() );
} );
check( 1 === frpdepot_fcg_test_count_message( $shell_output ),
	'the cart-errors shell is preceded by exactly one required message' );
check( false !== strpos( $shell_output, FRPDEPOT_FCG_MESSAGE_CLASS ),
	'the emitted message carries its marker class' );

// 3. WooCommerce then discards the notice queue. The message must survive that.
wc_clear_notices();
check( ! wc_has_notice( FRPDEPOT_FCG_MESSAGE, 'error' ),
	'wc_clear_notices() empties the queue, as production did' );
check( 1 === frpdepot_fcg_test_count_message( $shell_output ),
	'the required message is still on the page after the queue is cleared' );

/* The narrowing must be exact: no other template may trigger an emission. */
frpdepot_fcg_test_reset_blocked_cart();
$other_template = frpdepot_fcg_test_capture( function () {
	foreach ( array( 'cart/cart.php', 'checkout/form-checkout.php', 'checkout/cart-errors.phpx',
		'global/quantity-input.php' ) as $template ) {
		frpdepot_fcg_before_template_part( $template, '', '', array() );
	}
} );
check( '' === $other_template, 'no unrelated template emits the message' );

/* An unblocked cart never emits, whatever renders. */
frpdepot_fcg_test_reset_empty_cart();
$unblocked = frpdepot_fcg_test_capture( function () {
	frpdepot_fcg_before_template_part( 'checkout/cart-errors.php', '', '', array() );
	frpdepot_fcg_cart_has_errors();
} );
check( '' === $unblocked, 'an unblocked cart emits no message' );
frpdepot_fcg_check_cart_items();
check( array() === $GLOBALS['frpdepot_fcg_notices'],
	'an unblocked cart queues no blocking notice' );

/* ---------------------------------------------------------------------------
 * Exactly one visible message, no matter how many surfaces fire.
 * ------------------------------------------------------------------------ */
frpdepot_fcg_test_reset_blocked_cart();
$everything = frpdepot_fcg_test_capture( function () {
	// Every emitting surface, in the worst possible order, twice over.
	for ( $pass = 0; $pass < 2; $pass++ ) {
		frpdepot_fcg_before_template_part( 'checkout/cart-errors.php', '', '', array() );
		frpdepot_fcg_cart_has_errors();
		echo frpdepot_fcg_no_shipping_html( '<p>No shipping options were found.</p>' );
		echo frpdepot_fcg_emit_message();
	}
} );
check( 1 === frpdepot_fcg_test_count_message( $everything ),
	'exactly one required message across every surface, called twice: got '
		. frpdepot_fcg_test_count_message( $everything ) );

/* Once the message is shown, the no-shipping slot keeps WooCommerce's own
   wording rather than blanking out or repeating ours. */
frpdepot_fcg_test_reset_blocked_cart();
$first_slot  = frpdepot_fcg_no_shipping_html( '<p>No shipping options were found.</p>' );
$second_slot = frpdepot_fcg_no_shipping_html( '<p>No shipping options were found.</p>' );
check( false !== strpos( $first_slot, FRPDEPOT_FCG_MESSAGE ),
	'the first no-shipping slot shows the required message' );
check( '<p>No shipping options were found.</p>' === $second_slot,
	'a later no-shipping slot falls back to WooCommerce wording, not a second copy' );

/* An unblocked cart must never have its no-shipping wording rewritten. */
frpdepot_fcg_test_reset_empty_cart();
check( '<p>original</p>' === frpdepot_fcg_no_shipping_html( '<p>original</p>' ),
	'an unblocked cart keeps WooCommerce no-shipping wording' );

/* The blocking notice is deduplicated, so a notice left over from an earlier
   request cannot become a second identical line. */
frpdepot_fcg_test_reset_blocked_cart();
frpdepot_fcg_check_cart_items();
frpdepot_fcg_check_cart_items();
frpdepot_fcg_checkout_process();
check( 1 === count( $GLOBALS['frpdepot_fcg_notices'] ),
	'repeated cart/checkout validation queues the notice only once' );

/* ---------------------------------------------------------------------------
 * Direct submission stays blocked on both paths.
 * ------------------------------------------------------------------------ */
frpdepot_fcg_test_reset_blocked_cart();
$classic_errors = new FRPDEPOT_FCG_Stub_Errors();
frpdepot_fcg_after_checkout_validation( array(), $classic_errors );
check( 1 === count( $classic_errors->added ),
	'classic checkout validation records one blocking error' );
check( FRPDEPOT_FCG_MESSAGE === $classic_errors->added[0]['message'],
	'classic checkout error carries the exact message' );

$store_errors = new FRPDEPOT_FCG_Stub_Errors();
$returned     = frpdepot_fcg_store_api_cart_errors( $store_errors );
check( $returned === $store_errors, 'the Store API cart error bag is returned' );
check( 1 === count( $store_errors->added ),
	'Store API cart validation records one blocking error' );
check( FRPDEPOT_FCG_MESSAGE === $store_errors->added[0]['message'],
	'Store API cart error carries the exact message' );

$threw = false;
try {
	frpdepot_fcg_store_api_checkout_guard();
} catch ( \Throwable $error ) {
	$threw = FRPDEPOT_FCG_MESSAGE === $error->getMessage();
}
check( $threw, 'Store API order placement throws with the exact message' );

/* An unblocked cart is left alone on every submission path. */
frpdepot_fcg_test_reset_empty_cart();
$open_classic = new FRPDEPOT_FCG_Stub_Errors();
frpdepot_fcg_after_checkout_validation( array(), $open_classic );
check( array() === $open_classic->added, 'an unblocked cart passes classic validation' );
$open_store = new FRPDEPOT_FCG_Stub_Errors();
frpdepot_fcg_store_api_cart_errors( $open_store );
check( array() === $open_store->added, 'an unblocked cart passes Store API validation' );
$open_threw = false;
try {
	frpdepot_fcg_store_api_checkout_guard();
} catch ( \Throwable $error ) {
	$open_threw = true;
}
check( ! $open_threw, 'an unblocked cart may place a Store API order' );

/* ---------------------------------------------------------------------------
 * Bundled Blocks script: registered only when blocking, from a local file.
 * ------------------------------------------------------------------------ */
frpdepot_fcg_test_reset_blocked_cart();
frpdepot_fcg_enqueue_blocks_notice();
$handle = FRPDEPOT_FCG_SCRIPT_HANDLE;
check( isset( $GLOBALS['frpdepot_fcg_scripts'][ $handle ] ),
	'the Blocks notice script is registered for a blocked cart' );
if ( isset( $GLOBALS['frpdepot_fcg_scripts'][ $handle ] ) ) {
	$script = $GLOBALS['frpdepot_fcg_scripts'][ $handle ];
	check( true === $script['enqueued'], 'the Blocks notice script is enqueued' );
	check( array() === $script['deps'], 'the Blocks notice script has no dependencies' );
	check( FRPDEPOT_FCG_VERSION === $script['version'],
		'the Blocks notice script is versioned with the plugin' );
	check( false !== strpos( $script['src'], FRPDEPOT_FCG_SCRIPT_FILE ),
		'the Blocks notice script is served from the bundled plugin file' );
}

frpdepot_fcg_test_reset_empty_cart();
frpdepot_fcg_enqueue_blocks_notice();
check( array() === $GLOBALS['frpdepot_fcg_scripts'],
	'no script is registered for an unblocked cart' );

frpdepot_fcg_test_reset_blocked_cart();
$GLOBALS['frpdepot_fcg_is_cart'] = false; // Not a cart/checkout page.
frpdepot_fcg_enqueue_blocks_notice();
check( array() === $GLOBALS['frpdepot_fcg_scripts'],
	'no script is registered away from cart/checkout' );

/* ---------------------------------------------------------------------------
 * Source closure: the plugin and its bundled script stay inert.
 * ------------------------------------------------------------------------ */
$forbidden_php = array(
	'eval(', 'exec(', 'shell_exec', 'system(', 'passthru', 'popen',
	'file_put_contents', 'fopen(', 'unlink(', 'wp_remote_get', 'wp_remote_post',
	'curl_init', 'update_option', 'update_post_meta', 'wp_insert_post',
	'$wpdb', 'create_function', 'assert(', 'base64_decode', 'wp_add_inline_script',
);
foreach ( $forbidden_php as $token ) {
	check( false === strpos( $plugin_source, $token ), "plugin source is free of {$token}" );
}

$forbidden_js = array(
	'eval(', 'new Function', 'Function(', 'fetch(', 'XMLHttpRequest', 'WebSocket',
	'importScripts', 'innerHTML', 'outerHTML', 'document.write', 'localStorage',
	'sessionStorage', 'document.cookie', 'http://', 'https://', 'insertAdjacentHTML',
	'setAttribute( \'src\'', 'atob(', 'ajax',
);
foreach ( $forbidden_js as $token ) {
	check( false === strpos( $script_source, $token ), "bundled script is free of {$token}" );
}

echo "passes={$passes} failures={$failures}\n";
exit( $failures > 0 ? 1 : 0 );
