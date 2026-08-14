<?php
/** Focused offline tests for the fixed 2.0.3 replacement. No network/live services. */
declare( strict_types = 1 );

define( 'ABSPATH', __DIR__ . '/' );
define( 'OBJECT', 'OBJECT' );

$GLOBALS['fqj_passes'] = 0;
$GLOBALS['fqj_failures'] = 0;
$GLOBALS['fqj_hooks'] = array();
$GLOBALS['fqj_options'] = array();
$GLOBALS['fqj_posts'] = array();
$GLOBALS['fqj_products'] = array();
$GLOBALS['fqj_notices'] = array();
$GLOBALS['fqj_writes'] = array();
$GLOBALS['fqj_wc'] = null;
$GLOBALS['fqj_next_post_id'] = 700;
$GLOBALS['fqj_is_page'] = 0;
$GLOBALS['fqj_fail_update_option_once'] = '';
$GLOBALS['fqj_fail_update_option_match'] = 1;

class WP_Error {
	private $code;
	private $message;
	private $data;
	public function __construct( $code = '', $message = '', $data = null ) {
		$this->code = $code; $this->message = $message; $this->data = $data;
	}
	public function get_error_code() { return $this->code; }
	public function get_error_message() { return $this->message; }
	public function get_error_data() { return $this->data; }
}
function is_wp_error( $value ) { return $value instanceof WP_Error; }

function add_action( $hook, $callback, $priority = 10, $args = 1 ) {
	$GLOBALS['fqj_hooks'][] = array( 'type' => 'action', 'hook' => $hook, 'callback' => $callback, 'priority' => $priority, 'args' => $args );
	return true;
}
function add_filter( $hook, $callback, $priority = 10, $args = 1 ) {
	$GLOBALS['fqj_hooks'][] = array( 'type' => 'filter', 'hook' => $hook, 'callback' => $callback, 'priority' => $priority, 'args' => $args );
	return true;
}
function remove_action( $hook, $callback, $priority = 10 ) { $GLOBALS['fqj_removed'][] = array( $hook, $callback, $priority ); }
function register_activation_hook( $file, $callback ) { $GLOBALS['fqj_activation'] = $callback; }
function wp_json_encode( $value, $flags = 0 ) { return json_encode( $value, $flags ); }
function esc_html( $value ) { return htmlspecialchars( (string) $value, ENT_QUOTES, 'UTF-8' ); }
function esc_attr( $value ) { return esc_html( $value ); }
function esc_url( $value ) { return esc_attr( $value ); }
function esc_url_raw( $value ) { return (string) $value; }
function esc_html__( $value ) { return (string) $value; }
function sanitize_text_field( $value ) { return trim( strip_tags( (string) $value ) ); }
function wp_unslash( $value ) { return $value; }
function wp_slash( $value ) { return $value; }
function wp_parse_url( $url, $component = -1 ) { return parse_url( (string) $url, $component ); }
function home_url( $path = '/' ) { return 'https://frpdepots.com' . $path; }
function plugins_url( $path ) { return 'https://frpdepots.com/wp-content/plugins/frpdepot-freight-checkout-guard/' . ltrim( $path, '/' ); }
function add_query_arg( $args, $url ) { return $url . ( false === strpos( $url, '?' ) ? '?' : '&' ) . http_build_query( $args ); }
function wp_generate_uuid4() { static $n = 0; $n++; return sprintf( '11111111-2222-4333-8444-%012d', $n ); }
function wp_max_upload_size() { return 15 * 1024 * 1024; }
function is_admin() { return false; }
function is_product() { return false; }
function is_page( $id ) { return (int) $GLOBALS['fqj_is_page'] === (int) $id; }
function get_permalink( $id ) { return isset( $GLOBALS['fqj_posts'][ (int) $id ] ) && 'request-a-quote' === $GLOBALS['fqj_posts'][ (int) $id ]->post_name ? 'https://frpdepots.com/request-a-quote/' : 'https://frpdepots.com/product/item-' . (int) $id . '/'; }
function taxonomy_exists( $name ) { return false; }
function wc_attribute_label( $name ) { return ucwords( str_replace( array( 'attribute_', 'pa_', '-', '_' ), array( '', '', ' ', ' ' ), (string) $name ) ); }
function wp_enqueue_style() {}
function wp_enqueue_script() {}
function wp_localize_script() {}
function add_management_page() {}
function admin_url( $path ) { return 'https://frpdepots.com/wp-admin/' . ltrim( $path, '/' ); }
function wp_nonce_field() {}
function current_user_can() { return true; }
function check_admin_referer() {}
function wp_safe_redirect() {}
function wp_die( $message ) { throw new RuntimeException( (string) $message ); }

function get_option( $name, $default = false ) { return array_key_exists( $name, $GLOBALS['fqj_options'] ) ? $GLOBALS['fqj_options'][ $name ] : $default; }
function add_option( $name, $value ) {
	if ( array_key_exists( $name, $GLOBALS['fqj_options'] ) ) { return false; }
	$GLOBALS['fqj_options'][ $name ] = $value;
	$GLOBALS['fqj_writes'][] = 'option:add:' . $name;
	return true;
}
function update_option( $name, $value ) {
	if ( $GLOBALS['fqj_fail_update_option_once'] === $name ) {
		$GLOBALS['fqj_fail_update_option_match']--;
		if ( $GLOBALS['fqj_fail_update_option_match'] <= 0 ) {
			$GLOBALS['fqj_fail_update_option_once'] = '';
			$GLOBALS['fqj_fail_update_option_match'] = 1;
			return false;
		}
	}
	$changed = ! array_key_exists( $name, $GLOBALS['fqj_options'] ) || $GLOBALS['fqj_options'][ $name ] !== $value;
	$GLOBALS['fqj_options'][ $name ] = $value;
	if ( $changed ) { $GLOBALS['fqj_writes'][] = 'option:update:' . $name; }
	return $changed;
}
function delete_option( $name ) {
	if ( ! array_key_exists( $name, $GLOBALS['fqj_options'] ) ) { return false; }
	unset( $GLOBALS['fqj_options'][ $name ] );
	$GLOBALS['fqj_writes'][] = 'option:delete:' . $name;
	return true;
}

function fqj_post( $id, $type, $title, $slug, $content, $status = 'publish' ) {
	$post = new stdClass();
	$post->ID = (int) $id; $post->post_type = $type; $post->post_title = $title;
	$post->post_name = $slug; $post->post_content = $content; $post->post_status = $status;
	return $post;
}
function get_post( $id ) { return isset( $GLOBALS['fqj_posts'][ (int) $id ] ) ? clone $GLOBALS['fqj_posts'][ (int) $id ] : null; }
function get_page_by_path( $slug ) {
	foreach ( $GLOBALS['fqj_posts'] as $post ) { if ( 'page' === $post->post_type && $slug === $post->post_name ) { return clone $post; } }
	return null;
}
function get_pages() {
	return array_values( array_map( function ( $post ) { return clone $post; }, array_filter( $GLOBALS['fqj_posts'], function ( $post ) { return 'page' === $post->post_type; } ) ) );
}
function wp_insert_post( $data ) {
	$id = ++$GLOBALS['fqj_next_post_id'];
	$GLOBALS['fqj_posts'][ $id ] = fqj_post( $id, $data['post_type'], $data['post_title'], $data['post_name'], $data['post_content'], $data['post_status'] );
	$GLOBALS['fqj_writes'][] = 'business:page:create:' . $id;
	return $id;
}
function wp_update_post( $data ) {
	$id = (int) $data['ID'];
	if ( ! isset( $GLOBALS['fqj_posts'][ $id ] ) ) { return new WP_Error( 'missing', 'missing' ); }
	foreach ( array( 'post_type', 'post_title', 'post_name', 'post_content', 'post_status' ) as $key ) {
		if ( array_key_exists( $key, $data ) ) { $GLOBALS['fqj_posts'][ $id ]->{$key} = $data[ $key ]; }
	}
	$GLOBALS['fqj_writes'][] = 'business:post:update:' . $id;
	return $id;
}
function wp_delete_post( $id ) {
	$id = (int) $id;
	if ( ! isset( $GLOBALS['fqj_posts'][ $id ] ) ) { return false; }
	$post = $GLOBALS['fqj_posts'][ $id ]; unset( $GLOBALS['fqj_posts'][ $id ] );
	$GLOBALS['fqj_writes'][] = 'business:page:delete:' . $id;
	return $post;
}

class FQJ_Product {
	private $id; private $parent; private $name; private $sku; private $class; private $attributes; private $variable;
	public function __construct( $id, $parent, $name, $sku, $class, $attributes = array(), $variable = false ) {
		$this->id = $id; $this->parent = $parent; $this->name = $name; $this->sku = $sku;
		$this->class = $class; $this->attributes = $attributes; $this->variable = $variable;
	}
	public function get_id() { return $this->id; }
	public function get_parent_id() { return $this->parent; }
	public function get_name() { return $this->name; }
	public function get_sku() { return $this->sku; }
	public function get_shipping_class() { return $this->class; }
	public function get_attributes() { return $this->attributes; }
	public function is_type( $type ) { return 'variable' === $type && $this->variable; }
}
class FQJ_Cart { public $contents = array(); public function get_cart() { return $this->contents; } }
class FQJ_WC { public $cart; public function __construct() { $this->cart = new FQJ_Cart(); } }
function WC() { return $GLOBALS['fqj_wc']; }
function wc_get_product( $id ) { return isset( $GLOBALS['fqj_products'][ (int) $id ] ) ? $GLOBALS['fqj_products'][ (int) $id ] : null; }
function wc_add_notice( $message, $type = 'success' ) { $GLOBALS['fqj_notices'][] = array( $message, $type ); }
function wc_has_notice( $message, $type = 'success' ) { return in_array( array( $message, $type ), $GLOBALS['fqj_notices'], true ); }

class FQJ_Errors { public $items = array(); public function add( $code, $message ) { $this->items[] = array( $code, $message ); } }
class FQJ_Request {
	private $method; private $route;
	public function __construct( $method, $route ) { $this->method = $method; $this->route = $route; }
	public function get_method() { return $this->method; }
	public function get_route() { return $this->route; }
}

class GFAPI {
	public static $forms = array();
	public static $next = 80;
	public static function get_forms() {
		$result = array();
		foreach ( self::$forms as $form ) { $result[] = array( 'id' => $form['id'], 'title' => $form['title'] ); }
		return $result;
	}
	public static function get_form( $id ) { return isset( self::$forms[ (int) $id ] ) ? self::$forms[ (int) $id ] : null; }
	public static function add_form( $form ) {
		$id = ++self::$next; $form['id'] = $id; self::$forms[ $id ] = $form;
		$GLOBALS['fqj_writes'][] = 'business:form:create:' . $id; return $id;
	}
	public static function delete_form( $id ) {
		$id = (int) $id; if ( ! isset( self::$forms[ $id ] ) ) { return false; }
		unset( self::$forms[ $id ] ); $GLOBALS['fqj_writes'][] = 'business:form:delete:' . $id; return true;
	}
}

require __DIR__ . '/../frpdepot-freight-checkout-guard/frpdepot-freight-checkout-guard.php';
require dirname( __DIR__, 2 ) . '/freight_checkout_guard/frpdepot-freight-checkout-guard/frpdepot-freight-checkout-guard.php';

function fqj_check( $condition, $label ) {
	if ( $condition ) { $GLOBALS['fqj_passes']++; return; }
	$GLOBALS['fqj_failures']++; fwrite( STDERR, "FAIL: {$label}\n" );
}
function fqj_same( $expected, $actual, $label ) { fqj_check( $expected === $actual, $label . ' expected=' . var_export( $expected, true ) . ' actual=' . var_export( $actual, true ) ); }
function fqj_throws( $callable, $label ) { try { $callable(); fqj_check( false, $label ); } catch ( Throwable $error ) { fqj_check( true, $label ); } }
function fqj_reset_environment() {
	$GLOBALS['fqj_options'] = array(); $GLOBALS['fqj_posts'] = array(); $GLOBALS['fqj_writes'] = array(); $GLOBALS['fqj_notices'] = array();
	$GLOBALS['fqj_next_post_id'] = 700; $GLOBALS['fqj_wc'] = new FQJ_WC(); $GLOBALS['frpdepot_fqj_message_emitted'] = false;
	$GLOBALS['fqj_fail_update_option_once'] = '';
	$GLOBALS['fqj_fail_update_option_match'] = 1;
	GFAPI::$next = 80;
	$secret = 'recipient-canary-91a7@example.invalid';
	GFAPI::$forms = array( 1 => array(
		'id' => 1, 'title' => 'Contact', 'description' => '', 'fields' => array(),
		'notifications' => array( 'admin' => array(
			'id' => 'admin', 'name' => 'Admin Notification', 'isActive' => true,
			'to' => $secret, 'toType' => 'email', 'routing' => null, 'bcc' => '',
			'from' => '{admin_email}', 'fromName' => 'FRP Depot', 'replyTo' => '{Email:2}',
		) ),
	) );
	$GLOBALS['fqj_posts'][469] = fqj_post( 469, 'page', 'Contact', 'contact', '<p>Before</p><p>' . FRPDEPOT_FQJ_CONTACT_OLD . '</p><p>After</p>' );
}
function fqj_hook( $name ) {
	foreach ( $GLOBALS['fqj_hooks'] as $hook ) { if ( $hook['hook'] === $name ) { return $hook; } }
	return null;
}

/* Guard equivalence over every canonical baseline scenario. */
$scenarios = json_decode( file_get_contents( dirname( __DIR__, 2 ) . '/freight_checkout_guard/freight_guard_scenarios.json' ), true );
$now = strtotime( $scenarios['now_utc'] );
fqj_check( is_array( $scenarios ), 'baseline scenario file decoded' );
foreach ( $scenarios['cases'] as $case ) {
	$allowlist = $scenarios['allowlists'][ $case['allowlist'] ];
	$old = frpdepot_fcg_decide( $case['items'], $allowlist, $now );
	$new = frpdepot_fqj_decide( $case['items'], $allowlist, $now );
	fqj_same( $old['freight_required'], $new['freight_required'], 'guard equivalence decision: ' . $case['name'] );
	fqj_same( $old['reasons'], $new['reasons'], 'guard equivalence reasons: ' . $case['name'] );
}
$boundary = $scenarios['allowlists']['current']; $boundary['expires_utc'] = $scenarios['now_utc'];
fqj_same( '0:allowlist_stale', frpdepot_fqj_decide( $scenarios['cases'][1]['items'], $boundary, $now )['reasons'][0] ?? '', 'expiry boundary fails closed' );
$duplicate = $scenarios['allowlists']['current']; $duplicate['items'][] = $duplicate['items'][0];
fqj_check( ! frpdepot_fqj_decide( $scenarios['cases'][1]['items'], $duplicate, $now )['freight_required'], 'duplicate identical allowlist IDs preserve baseline last-write behavior' );
fqj_same( '0:item_unreadable', frpdepot_fqj_decide( array( null ), $scenarios['allowlists']['current'], $now )['reasons'][0], 'unreadable line fails closed' );

/* Required hook contract and strict backstops. */
$hook_contract = array(
	'woocommerce_package_rates' => array( 100, 2 ),
	'woocommerce_no_shipping_available_html' => array( 100, 1 ),
	'woocommerce_cart_no_shipping_available_html' => array( 100, 1 ),
	'woocommerce_add_to_cart_validation' => array( 100, 5 ),
	'woocommerce_checkout_create_order' => array( 1, 2 ),
	'woocommerce_store_api_checkout_update_order_from_request' => array( 1, 2 ),
	'rest_request_before_callbacks' => array( 100, 3 ),
);
foreach ( $hook_contract as $name => $expected ) {
	$hook = fqj_hook( $name ); fqj_check( is_array( $hook ), 'hook registered: ' . $name );
	if ( $hook ) { fqj_same( $expected, array( $hook['priority'], $hook['args'] ), 'hook priority/arity: ' . $name ); }
}
fqj_check( ! array_key_exists( 'fqj_activation', $GLOBALS ), 'plugin activation registers no business transaction' );
$apply_hook = fqj_hook( 'admin_post_frpdepot_fqj_fixed_apply' );
fqj_check( is_array( $apply_hook ) && 'frpdepot_fqj_admin_apply' === $apply_hook['callback'], 'fixed authenticated Apply hook is the only transaction trigger' );

fqj_reset_environment();
$blocked = new FQJ_Product( 2455, 1455, 'Pipe 4 in', 'PIPE-4', 'freight-quote-required', array( 'pa_size' => '4-in', 'pa_pressure-rating' => '150-psi', 'pa_resin-type' => 'vinyl-ester' ) );
$parent = new FQJ_Product( 1455, 0, 'FRP Pipe', 'PIPE', '', array(), true );
$other_parent = new FQJ_Product( 1423, 0, 'Elbow', 'ELBOW', '', array(), true );
$other_blocked = new FQJ_Product( 2423, 1423, 'Elbow 4 in', 'ELBOW-4', 'freight-quote-required', array( 'pa_size' => '4-in', 'pa_pressure-rating' => '150-psi', 'pa_resin-type' => 'vinyl-ester' ) );
$GLOBALS['fqj_products'] = array( 1455 => $parent, 2455 => $blocked, 1423 => $other_parent, 2423 => $other_blocked );
$GLOBALS['fqj_wc']->cart->contents = array( 'line' => array( 'product_id' => 1455, 'variation_id' => 2455, 'quantity' => 2, 'data' => $blocked, 'variation' => array( 'attribute_pa_size' => '4-in', 'attribute_pa_pressure-rating' => '150-psi', 'attribute_pa_resin-type' => 'vinyl-ester' ) ) );
$rates = array( 'ups:1' => (object) array( 'id' => 'ups:1' ) );
$gateways = array( 'card' => (object) array( 'id' => 'card' ) );
fqj_same( array(), frpdepot_fqj_filter_package_rates( $rates ), 'blocked cart suppresses every supplied rate without replacement' );
fqj_same( array(), frpdepot_fqj_filter_gateways( $gateways ), 'blocked cart suppresses gateways' );
fqj_same( false, frpdepot_fqj_shipping_calculator_enable( true ), 'blocked cart suppresses calculator' );
fqj_same( false, frpdepot_fqj_add_to_cart_validation( true, 1455, 1, 2455, array() ), 'crafted blocked variation add-to-cart is denied' );
fqj_same( false, frpdepot_fqj_add_to_cart_validation( false, 1455, 1, 2455, array() ), 'add-to-cart never upgrades a prior false' );
$classic = new FQJ_Errors(); frpdepot_fqj_after_checkout_validation( array(), $classic );
fqj_same( 1, count( $classic->items ), 'classic direct checkout receives a blocking error' );
$store = new FQJ_Errors(); frpdepot_fqj_store_api_cart_errors( $store );
fqj_same( 1, count( $store->items ), 'Store API cart receives a blocking error' );
$rest = frpdepot_fqj_rest_checkout_pre_callback( null, null, new FQJ_Request( 'POST', '/wc/store/v1/checkout' ) );
fqj_check( $rest instanceof WP_Error && 409 === $rest->get_error_data()['status'], 'REST checkout callback is stopped with 409' );
fqj_same( null, frpdepot_fqj_rest_checkout_pre_callback( null, null, new FQJ_Request( 'GET', '/wc/store/v1/checkout' ) ), 'non-POST Store API route passes untouched' );
fqj_same( 'prior', frpdepot_fqj_rest_checkout_pre_callback( 'prior', null, new FQJ_Request( 'POST', '/wc/store/v1/checkout' ) ), 'prior REST response passes untouched' );
fqj_throws( function () { frpdepot_fqj_checkout_create_order_guard( new stdClass(), array() ); }, 'classic pre-save order hook throws before mutation' );
fqj_throws( function () { frpdepot_fqj_store_api_checkout_guard(); }, 'Store API update-order hook throws' );
$GLOBALS['fqj_wc']->cart->contents = array();
fqj_same( $rates, frpdepot_fqj_filter_package_rates( $rates ), 'unblocked cart returns exact rate object array' );
fqj_same( $gateways, frpdepot_fqj_filter_gateways( $gateways ), 'unblocked cart returns exact gateway object array' );
fqj_same( true, frpdepot_fqj_shipping_calculator_enable( true ), 'unblocked cart preserves calculator flag' );

/* Product variation data exposes only the three immutable decision values. */
$data = frpdepot_fqj_available_variation( array( 'variation_id' => 2455 ), $parent, $blocked );
fqj_same( array( 'variation_id', 'frpdepot_quote_required', 'frpdepot_product_id', 'frpdepot_variation_id' ), array_keys( $data ), 'variation payload adds only decision and IDs' );
fqj_same( true, $data['frpdepot_quote_required'], 'variation server decision is quote-required' );

/* Closed route preflight refuses incomplete routing without any write. */
fqj_reset_environment();
unset( GFAPI::$forms[1]['notifications']['admin']['routing'] );
frpdepot_fqj_commissioned_activation_transaction();
fqj_same( null, frpdepot_fqj_state(), 'missing route key leaves no journey state for deploy-tool inspection' );
foreach ( frpdepot_fqj_backup_options() as $option ) {
	fqj_same( false, get_option( $option, false ), 'missing route key removes pre-write backup: ' . $option );
}
fqj_same( false, get_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION, false ), 'missing route key removes pre-write receipt head' );
fqj_same( 0, count( array_filter( $GLOBALS['fqj_writes'], function ( $write ) { return 0 === strpos( $write, 'business:' ); } ) ), 'route preflight refusal occurs before any business write' );

/* Restart cleanup sweeps both the committed chain and the sole possible head+1 orphan. */
fqj_reset_environment();
$restart_deployment = str_repeat( 'c', 32 );
$GLOBALS['fqj_options'][ FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION ] = array(
	'deployment_id' => $restart_deployment,
	'sequence' => 1,
	'receipt_sha256' => str_repeat( 'd', 64 ),
);
$restart_receipt = frpdepot_fqj_receipt_option( $restart_deployment, 1 );
$restart_orphan = frpdepot_fqj_receipt_option( $restart_deployment, 2 );
$GLOBALS['fqj_options'][ $restart_receipt ] = array( 'committed' => true );
$GLOBALS['fqj_options'][ $restart_orphan ] = array( 'orphan' => true );
frpdepot_fqj_cleanup_prewrite_options();
fqj_same( false, get_option( $restart_receipt, false ), 'restart cleanup removes committed pre-write receipt' );
fqj_same( false, get_option( $restart_orphan, false ), 'restart cleanup removes head+1 orphan receipt' );
fqj_same( false, get_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION, false ), 'restart cleanup removes receipt head after receipts' );

/* Live Gravity Forms fixed-email mode uses null routing; conditional routing remains refused. */
fqj_reset_environment();
fqj_same( null, frpdepot_fqj_source_route()['routing'], 'live fixed-email null routing is accepted unchanged' );
GFAPI::$forms[1]['notifications']['admin']['routing'] = array( array( 'fieldId' => 2, 'operator' => 'is', 'value' => 'x', 'email' => 'other@example.invalid' ) );
fqj_throws( function () { frpdepot_fqj_source_route(); }, 'non-empty conditional routing is refused' );
GFAPI::$forms[1]['notifications']['admin']['routing'] = null;
GFAPI::$forms[1]['notifications']['admin']['toType'] = 'routing';
fqj_throws( function () { frpdepot_fqj_source_route(); }, 'routing recipient mode is refused' );
GFAPI::$forms[1]['notifications']['admin']['toType'] = 'email';

/* Three-field collision search: only one complete projection is reusable. */
$route = frpdepot_fqj_source_route();
fqj_same(
	array( 'action' => 'create', 'form_id' => 0, 'collisions' => array( 'title' => 0, 'marker' => 0, 'admin_marker' => 0 ), 'exact' => 0, 'partial' => 0 ),
	( function ( $found ) { return array( 'action' => $found['action'], 'form_id' => $found['form_id'], 'collisions' => $found['collisions'], 'exact' => $found['exact_match_count'], 'partial' => $found['partial_match_count'] ); } )( frpdepot_fqj_resolve_owned_form( $route ) ),
	'empty search chooses create with all three collision counts zero'
);
$collision_fields = array( 'title', 'description', 'cssClass' );
foreach ( $collision_fields as $field ) {
	fqj_reset_environment();
	$candidate = frpdepot_fqj_form_definition( frpdepot_fqj_source_route() );
	$candidate['id'] = 81;
	$candidate['title'] = 'Other form';
	$candidate['description'] = 'OTHER_MARKER';
	$candidate['cssClass'] = 'OTHER_ADMIN_MARKER';
	if ( 'title' === $field ) { $candidate['title'] = FRPDEPOT_FQJ_FORM_TITLE; }
	if ( 'description' === $field ) { $candidate['description'] = FRPDEPOT_FQJ_FORM_MARKER; }
	if ( 'cssClass' === $field ) { $candidate['cssClass'] = FRPDEPOT_FQJ_FORM_ADMIN_MARKER; }
	GFAPI::$forms[81] = $candidate;
	$before = count( $GLOBALS['fqj_writes'] );
	fqj_throws( function () { frpdepot_fqj_resolve_owned_form( frpdepot_fqj_source_route() ); }, 'single-field partial ownership refuses: ' . $field );
	fqj_same( $before, count( $GLOBALS['fqj_writes'] ), 'partial ownership search is read-only: ' . $field );
}
fqj_reset_environment();
$signature_only = frpdepot_fqj_form_definition( frpdepot_fqj_source_route() );
$signature_only['id'] = 80;
$signature_only['title'] = 'Other form';
$signature_only['description'] = 'OTHER_MARKER';
$signature_only['cssClass'] = 'OTHER_ADMIN_MARKER';
GFAPI::$forms[80] = $signature_only;
fqj_throws( function () { frpdepot_fqj_resolve_owned_form( frpdepot_fqj_source_route() ); }, 'field-signature-only partial ownership refuses' );
fqj_reset_environment();
$owned = frpdepot_fqj_form_definition( frpdepot_fqj_source_route() );
$owned['id'] = 81;
GFAPI::$forms[81] = $owned;
$resolved = frpdepot_fqj_resolve_owned_form( frpdepot_fqj_source_route() );
fqj_same( 'reuse', $resolved['action'], 'one complete ownership proof chooses reuse' );
fqj_same( 81, $resolved['form_id'], 'one complete ownership proof returns its exact form ID' );
fqj_same( array( 'title' => 1, 'marker' => 1, 'admin_marker' => 1 ), $resolved['collisions'], 'reused form satisfies every ownership collision identity exactly once' );
$second = $owned; $second['id'] = 82; GFAPI::$forms[82] = $second;
fqj_throws( function () { frpdepot_fqj_resolve_owned_form( frpdepot_fqj_source_route() ); }, 'two complete owned forms refuse as multiple matches' );
$second['title'] = 'Partial'; GFAPI::$forms[82] = $second;
fqj_throws( function () { frpdepot_fqj_resolve_owned_form( frpdepot_fqj_source_route() ); }, 'one complete plus one partial form still refuses' );

/* Exact owned-form activation reuses and rollback preserves that form. */
fqj_reset_environment();
$owned = frpdepot_fqj_form_definition( frpdepot_fqj_source_route() );
$owned['id'] = 81; GFAPI::$forms[81] = $owned;
frpdepot_fqj_commissioned_activation_transaction();
$reuse_state = frpdepot_fqj_state();
fqj_same( 81, $reuse_state['form_id'], 'activation reuses the exact owned form ID' );
fqj_same( 0, count( array_filter( $GLOBALS['fqj_writes'], function ( $write ) { return 0 === strpos( $write, 'business:form:create:' ); } ) ), 'reuse activation creates no duplicate form' );
$reuse_backup = get_option( FRPDEPOT_FQJ_BACKUP_FORM_OPTION );
fqj_same( true, $reuse_backup['existed'], 'reuse backup records the form as pre-existing' );
fqj_same( 'reuse', $reuse_backup['payload']['ownership_action'], 'reuse backup records ownership action' );
frpdepot_fqj_rollback_transaction( false );
fqj_check( is_array( GFAPI::get_form( 81 ) ), 'rollback preserves the reused pre-existing form' );
fqj_same( 0, count( array_filter( $GLOBALS['fqj_writes'], function ( $write ) { return 'business:form:delete:81' === $write; } ) ), 'rollback never deletes the reused form' );

/* Fixed activation: all independent backups precede the first business write. */
fqj_reset_environment();
$GLOBALS['fqj_products'] = array( 1455 => $parent, 2455 => $blocked, 1423 => $other_parent, 2423 => $other_blocked );
frpdepot_fqj_commissioned_activation_transaction();
$state = frpdepot_fqj_state();
fqj_same( 'applied', $state['status'], 'fixed activation reaches applied' );
$business_index = null;
foreach ( $GLOBALS['fqj_writes'] as $index => $write ) { if ( 0 === strpos( $write, 'business:' ) ) { $business_index = $index; break; } }
$before_business = array_slice( $GLOBALS['fqj_writes'], 0, $business_index );
foreach ( frpdepot_fqj_backup_options() as $artifact => $option ) {
	fqj_check( in_array( 'option:add:' . $option, $before_business, true ), 'independent ' . $artifact . ' backup precedes first business write' );
	$backup = get_option( $option );
	fqj_same( array( 'schema_version', 'deployment_id', 'artifact', 'captured_utc', 'spec_sha256', 'existed', 'artifact_id', 'before_sha256', 'payload' ), array_keys( $backup ), 'closed backup schema: ' . $artifact );
}
fqj_check( frpdepot_fqj_verify_receipt_chain(), 'activation receipt chain validates' );
$receipt_head = get_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION );
$orphan_option = frpdepot_fqj_receipt_option( $receipt_head['deployment_id'], (int) $receipt_head['sequence'] + 1 );
$GLOBALS['fqj_options'][ $orphan_option ] = array( 'orphan' => true );
fqj_check( ! frpdepot_fqj_verify_receipt_chain(), 'receipt chain refuses an orphan record after the head' );
unset( $GLOBALS['fqj_options'][ $orphan_option ] );
fqj_check( frpdepot_fqj_verify_receipt_chain(), 'receipt chain validates again after the test-only orphan is removed' );

/* A failed head update removes its just-added immutable receipt before returning. */
$receipt_head = get_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION );
$failed_sequence = (int) $receipt_head['sequence'] + 1;
$failed_option = frpdepot_fqj_receipt_option( $receipt_head['deployment_id'], $failed_sequence );
$GLOBALS['fqj_fail_update_option_once'] = FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION;
fqj_throws( function () use ( $state ) {
	frpdepot_fqj_append_receipt( 'verify', 'journey', $state['deployment_id'], str_repeat( 'a', 64 ), str_repeat( 'b', 64 ), 'OK' );
}, 'failed receipt-head update refuses' );
fqj_same( false, get_option( $failed_option, false ), 'failed receipt-head update leaves no orphan receipt' );
fqj_same( $receipt_head, get_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION ), 'failed receipt-head update preserves the prior head' );
fqj_check( frpdepot_fqj_verify_receipt_chain(), 'receipt chain remains valid after failed head update cleanup' );

/* A form state-write failure removes the exact untracked form before rollback succeeds. */
fqj_reset_environment();
$GLOBALS['fqj_fail_update_option_once'] = FRPDEPOT_FQJ_STATE_OPTION;
frpdepot_fqj_commissioned_activation_transaction();
$failed_form_state = frpdepot_fqj_state();
fqj_same( 'rolled_back', $failed_form_state['status'], 'form state-write failure finishes rolled back' );
fqj_same( array( 1 ), array_keys( GFAPI::$forms ), 'form state-write failure leaves only the source form' );
fqj_same( null, get_page_by_path( FRPDEPOT_FQJ_PAGE_SLUG, OBJECT, 'page' ), 'form state-write failure leaves no quote page' );
fqj_check( in_array( 'business:form:create:81', $GLOBALS['fqj_writes'], true ), 'form failure fixture performed the business create' );
fqj_check( in_array( 'business:form:delete:81', $GLOBALS['fqj_writes'], true ), 'form failure compensates the exact created form' );

/* A page state-write failure removes that page, then rolls back the tracked form. */
fqj_reset_environment();
$GLOBALS['fqj_fail_update_option_once'] = FRPDEPOT_FQJ_STATE_OPTION;
$GLOBALS['fqj_fail_update_option_match'] = 2;
frpdepot_fqj_commissioned_activation_transaction();
$failed_page_state = frpdepot_fqj_state();
fqj_same( 'rolled_back', $failed_page_state['status'], 'page state-write failure finishes rolled back' );
fqj_same( array( 1 ), array_keys( GFAPI::$forms ), 'page state-write failure rolls back the tracked form' );
fqj_same( null, get_page_by_path( FRPDEPOT_FQJ_PAGE_SLUG, OBJECT, 'page' ), 'page state-write failure removes the untracked quote page' );
fqj_check( in_array( 'business:page:create:701', $GLOBALS['fqj_writes'], true ), 'page failure fixture performed the business create' );
fqj_check( in_array( 'business:page:delete:701', $GLOBALS['fqj_writes'], true ), 'page failure compensates the exact created page' );


/* Restore the ordinary applied fixture for the remaining projection checks. */
fqj_reset_environment();
frpdepot_fqj_commissioned_activation_transaction();
$state = frpdepot_fqj_state();
$form = GFAPI::get_form( $state['form_id'] );
$definition = frpdepot_fqj_form_projection( $form );
fqj_same( FRPDEPOT_FQJ_FORM_TITLE, $definition['title'], 'form title exact' );
fqj_same( FRPDEPOT_FQJ_FORM_MARKER, $definition['description'], 'form marker exact' );
fqj_same( 21, count( $definition['fields'] ), 'form has exactly 21 fields' );
fqj_same( range( 1, 21 ), array_column( $definition['fields'], 'id' ), 'form field IDs exact and ordered' );
fqj_same( array( 'text','text','email','text','phone','text','text','text','text','text','select','text','textarea','date','fileupload','checkbox','hidden','hidden','hidden','hidden','hidden' ), array_column( $definition['fields'], 'type' ), 'form field types exact' );
fqj_same( array( 'visible','visible','visible','visible','visible','visible','visible','visible','visible','visible','visible','visible','visible','visible','visible','visible','hidden','hidden','hidden','hidden','hidden' ), array_column( $definition['fields'], 'visibility' ), 'form visibility exact' );
fqj_same( array( 80,80,254,160,40,500,500,500,500,200,0,20,4000,0,0,0,0,0,0,0,0 ), array_column( $definition['fields'], 'maxLength' ), 'form max lengths exact' );
fqj_same( array( array( 'text' => 'Canada', 'value' => 'CA', 'isSelected' => false ), array( 'text' => 'United States', 'value' => 'US', 'isSelected' => false ) ), $definition['fields'][10]['choices'], 'country choices are exact CA/US values' );
fqj_same( 'pdf,jpg,jpeg,png', $definition['fields'][14]['allowedExtensions'], 'upload extensions exact' );
fqj_same( 10, $definition['fields'][14]['maxFileSize'], 'upload maximum is min(site limit, 10 MB)' );
fqj_same( false, $definition['fields'][14]['multipleFiles'], 'upload is single-file only' );
fqj_same( 'yes', $definition['fields'][15]['choices'][0]['value'], 'marketing exact value yes' );
fqj_same( false, $definition['fields'][15]['choices'][0]['isSelected'], 'marketing defaults unchecked' );
fqj_same( array( array( 'id' => '16.1', 'label' => 'I would like to receive occasional FRP Depot marketing updates.', 'name' => '' ) ), $definition['fields'][15]['inputs'], 'marketing checkbox has exact Gravity Forms input metadata' );
fqj_same( 'top_label', $definition['label_placement'], 'form label placement exact' );
fqj_same( 'text', $definition['button_type'], 'form submit button type exact' );
fqj_same( 'Request Quote', $definition['button_text'], 'form submit button wording exact' );
fqj_same( 1, $definition['notification_count'], 'exactly one notification' );
fqj_same( 'frpdepot_fqj_admin_notification', $definition['notification_id'], 'notification ID exact' );
fqj_same( 'Admin Notification', $definition['notification_name'], 'notification name exact' );
fqj_same( 'form_submission', $definition['notification_event'], 'notification event exact' );
fqj_same( true, $definition['notification_active'], 'notification active' );
fqj_same( '{Email:3}', array_values( $form['notifications'] )[0]['replyTo'], 'dedicated notification reply-to references dedicated email field' );
fqj_same( frpdepot_fqj_hash( frpdepot_fqj_route_projection( frpdepot_fqj_notification_definition( frpdepot_fqj_source_route() ) ) ), $definition['route_sha256'], 'dedicated notification route is fixed and compared only by hash' );
fqj_same( 1, $definition['confirmation_count'], 'exactly one confirmation' );
fqj_same( 'frpdepot_fqj_confirmation', $definition['confirmation_id'], 'confirmation ID exact' );
fqj_same( 'Default Confirmation', $definition['confirmation_name'], 'confirmation name exact' );
fqj_same( true, $definition['confirmation_default'], 'confirmation is default' );
fqj_same( false, $definition['confirmation_disable_autoformat'], 'confirmation autoformat exact' );
fqj_same( FRPDEPOT_FQJ_CONFIRMATION, $definition['confirmation_message'], 'confirmation text exact' );
$page = get_post( $state['page_id'] );
fqj_same( FRPDEPOT_FQJ_PAGE_TITLE, $page->post_title, 'quote page title exact' );
fqj_same( FRPDEPOT_FQJ_PAGE_SLUG, $page->post_name, 'quote page slug exact' );
fqj_same( 1, substr_count( $page->post_content, '[gravityform ' ), 'quote page has exactly one form shortcode' );
fqj_same( 1, substr_count( get_post(469)->post_content, FRPDEPOT_FQJ_CONTACT_NEW ), 'Contact replacement exact once' );
fqj_same( 0, substr_count( get_post(469)->post_content, FRPDEPOT_FQJ_CONTACT_OLD ), 'old Contact sentence absent after activation' );
$secret = 'recipient-canary-91a7@example.invalid';
$status_json = json_encode( frpdepot_fqj_status_projection() );
fqj_check( false === strpos( $status_json, $secret ), 'recipient secret absent from status projection' );
foreach ( range( 1, get_option( FRPDEPOT_FQJ_RECEIPT_HEAD_OPTION )['sequence'] ) as $sequence ) {
	$receipt = get_option( frpdepot_fqj_receipt_option( $state['deployment_id'], $sequence ) );
	fqj_same( array( 'schema_version','deployment_id','sequence','utc','spec_sha256','operation','artifact','artifact_id','before_sha256','after_sha256','status','previous_receipt_sha256','receipt_sha256' ), array_keys( $receipt ), 'closed receipt schema sequence ' . $sequence );
	fqj_check( false === strpos( json_encode( $receipt ), $secret ), 'recipient secret absent from receipt sequence ' . $sequence );
}
$writes_before_reactivation = count( $GLOBALS['fqj_writes'] );
frpdepot_fqj_commissioned_activation_transaction();
fqj_same( $writes_before_reactivation, count( $GLOBALS['fqj_writes'] ), 'ordinary reactivation performs no write after applied transaction' );

/* Closed server reconstruction for product and cart handoffs. */
$_GET = array( 'fqj_source' => 'product', 'fqj_product_id' => '1455', 'fqj_variation_id' => '2455', 'fqj_quantity' => '3', 'product' => 'INJECTED', 'product_url' => 'https://evil.invalid/' );
$product_handoff = frpdepot_fqj_handoff_from_request();
fqj_same( true, $product_handoff['valid'], 'valid product handoff accepted' );
fqj_same( 'product', $product_handoff['source'], 'product source enum' );
fqj_same( 'FRP Pipe — Pressure Rating: 150-psi; Resin Type: vinyl-ester; Size: 4-in', $product_handoff['values'][6], 'product name/options reconstructed server-side' );
fqj_same( 'https://frpdepots.com/product/item-1455/', $product_handoff['values'][17], 'product URL reconstructed same-origin' );
fqj_same( '1455', $product_handoff['values'][18], 'product ID canonical' );
fqj_same( '2455', $product_handoff['values'][19], 'variation ID canonical' );
$_GET['fqj_name'] = 'UNKNOWN';
$unknown = frpdepot_fqj_handoff_from_request();
fqj_same( false, $unknown['valid'], 'unknown fqj_* key rejected' );
fqj_same( '', $unknown['values'][6], 'malformed handoff yields blank editable product' );
$missing_resin = new FQJ_Product( 2456, 1455, 'Pipe 4 in', 'PIPE-4-NO-RESIN', 'freight-quote-required', array( 'pa_size' => '4-in', 'pa_pressure-rating' => '150-psi' ) );
$GLOBALS['fqj_products'][2456] = $missing_resin;
$_GET = array( 'fqj_source' => 'product', 'fqj_product_id' => '1455', 'fqj_variation_id' => '2456', 'fqj_quantity' => '1' );
fqj_same( false, frpdepot_fqj_handoff_from_request()['valid'], 'missing required resin handoff fails closed instead of requesting retyping' );
foreach ( array( '+1455', '-1', '1.2', '0001455', '99999999999' ) as $bad ) {
	$_GET = array( 'fqj_source' => 'product', 'fqj_product_id' => $bad, 'fqj_variation_id' => '2455', 'fqj_quantity' => '1' );
	fqj_same( false, frpdepot_fqj_handoff_from_request()['valid'], 'strict decimal product ID rejected: ' . $bad );
}
$_GET = array( 'fqj_source' => 'product', 'fqj_product_id' => '1423', 'fqj_variation_id' => '2455', 'fqj_quantity' => '1' );
fqj_same( false, frpdepot_fqj_handoff_from_request()['valid'], 'wrong variation parent rejected' );
$_GET = array( 'fqj_source' => 'product', 'fqj_product_id' => '1455', 'fqj_variation_id' => '2455', 'fqj_quantity' => '10000' );
fqj_same( false, frpdepot_fqj_handoff_from_request()['valid'], 'quantity overflow rejected' );
$GLOBALS['fqj_wc']->cart->contents = array(
	'a' => array( 'product_id' => 1455, 'variation_id' => 2455, 'quantity' => 2, 'data' => $blocked, 'variation' => array( 'attribute_pa_size' => '4-in', 'attribute_pa_pressure-rating' => '150-psi', 'attribute_pa_resin-type' => 'vinyl-ester' ) ),
	'b' => array( 'product_id' => 1423, 'variation_id' => 2423, 'quantity' => 1, 'data' => $other_blocked, 'variation' => array( 'attribute_pa_size' => '4-in', 'attribute_pa_pressure-rating' => '150-psi', 'attribute_pa_resin-type' => 'vinyl-ester' ) ),
);
$_GET = array( 'fqj_source' => 'cart' );
$cart_handoff = frpdepot_fqj_handoff_from_request();
fqj_same( true, $cart_handoff['valid'], 'mixed cart handoff accepted from live server cart' );
$projection = json_decode( $cart_handoff['values'][21], true );
fqj_same( 2, count( $projection ), 'cart handoff includes every line' );
fqj_same( array( 'product_id','variation_id','attributes','quantity' ), array_keys( $projection[0] ), 'cart projection keys exact' );
fqj_same( array( 'product_id','variation_id','attributes','quantity' ), array_keys( $projection[1] ), 'second cart projection keys exact' );
fqj_check( false === stripos( $cart_handoff['values'][21], 'price' ) && false === stripos( $cart_handoff['values'][21], 'sku' ), 'cart projection excludes price and SKU' );
$GLOBALS['fqj_wc']->cart->contents = array_fill( 0, 51, $GLOBALS['fqj_wc']->cart->contents['a'] );
fqj_same( false, frpdepot_fqj_handoff_from_request()['valid'], '51-line cart rejected' );

/* Form-specific successful marker and six-key analytics source closure. */
$_GET = array();
$form = GFAPI::get_form( $state['form_id'] );
$entry = array( 'id' => 900, 'status' => 'active', 'is_spam' => false, '18' => '1455', '19' => '2455', '20' => 'product' );
$marker = frpdepot_fqj_confirmation_marker( FRPDEPOT_FQJ_CONFIRMATION, $form, $entry, false );
fqj_same( 1, substr_count( $marker, 'class="frpdepot-fq-success"' ), 'valid dedicated success emits one inert marker' );
fqj_check( false === strpos( $marker, '<script' ), 'confirmation marker contains no inline script' );
$spam = $entry; $spam['is_spam'] = true;
fqj_same( FRPDEPOT_FQJ_CONFIRMATION, frpdepot_fqj_confirmation_marker( FRPDEPOT_FQJ_CONFIRMATION, $form, $spam, false ), 'spam entry emits no marker' );
$zero = $entry; $zero['id'] = 0;
fqj_same( FRPDEPOT_FQJ_CONFIRMATION, frpdepot_fqj_confirmation_marker( FRPDEPOT_FQJ_CONFIRMATION, $form, $zero, false ), 'zero entry ID emits no marker' );
$wrong_form = $form; $wrong_form['id'] = 999;
fqj_same( FRPDEPOT_FQJ_CONFIRMATION, frpdepot_fqj_confirmation_marker( FRPDEPOT_FQJ_CONFIRMATION, $wrong_form, $entry, false ), 'other form emits no marker' );
fqj_same( 'failure', frpdepot_fqj_confirmation_marker( 'failure', $form, $entry, false ), 'non-success confirmation emits no marker' );
$writes_before_runtime = count( $GLOBALS['fqj_writes'] );
frpdepot_fqj_register_form_hooks(); frpdepot_fqj_status_projection(); frpdepot_fqj_handoff_from_request();
fqj_same( $writes_before_runtime, count( $GLOBALS['fqj_writes'] ), 'ordinary runtime hooks/status/handoff perform no business or option writes' );

/* Drift-safe rollback stops before touching later artifacts. */
$GLOBALS['fqj_posts'][469]->post_content .= '<p>operator drift</p>';
$writes_before_drift_rollback = count( $GLOBALS['fqj_writes'] );
fqj_throws( function () { frpdepot_fqj_rollback_transaction( false ); }, 'Contact drift blocks rollback' );
fqj_same( 'rollback_blocked_drift', frpdepot_fqj_state()['status'], 'drift status recorded' );
fqj_check( isset( GFAPI::$forms[ $state['form_id'] ] ), 'drifted rollback leaves form untouched' );
fqj_check( isset( $GLOBALS['fqj_posts'][ $state['page_id'] ] ), 'drifted rollback leaves page untouched' );
$new_writes = array_slice( $GLOBALS['fqj_writes'], $writes_before_drift_rollback );
fqj_check( 0 === count( array_filter( $new_writes, function ( $write ) { return 0 === strpos( $write, 'business:' ); } ) ), 'drift refusal performs no business write' );

/* Clean rollback restores Contact and deletes owned page/form in reverse order. */
fqj_reset_environment();
$GLOBALS['fqj_products'] = array( 1455 => $parent, 2455 => $blocked, 1423 => $other_parent, 2423 => $other_blocked );
$contact_before = get_post(469)->post_content;
frpdepot_fqj_commissioned_activation_transaction();
$clean_state = frpdepot_fqj_state();
$rollback_start = count( $GLOBALS['fqj_writes'] );
frpdepot_fqj_rollback_transaction( false );
fqj_same( 'rolled_back', frpdepot_fqj_state()['status'], 'clean rollback reaches rolled_back' );
fqj_same( $contact_before, get_post(469)->post_content, 'clean rollback restores exact Contact content' );
fqj_same( null, get_post( $clean_state['page_id'] ), 'clean rollback deletes exact owned page' );
fqj_same( null, GFAPI::get_form( $clean_state['form_id'] ), 'clean rollback deletes exact owned form' );
$business_rollback = array_values( array_filter( array_slice( $GLOBALS['fqj_writes'], $rollback_start ), function ( $write ) { return 0 === strpos( $write, 'business:' ); } ) );
fqj_same( array( 'business:post:update:469', 'business:page:delete:' . $clean_state['page_id'], 'business:form:delete:' . $clean_state['form_id'] ), $business_rollback, 'clean rollback business writes are reverse ordered' );
fqj_check( frpdepot_fqj_verify_receipt_chain(), 'receipt chain remains valid after rollback' );
foreach ( frpdepot_fqj_backup_options() as $option ) { fqj_check( is_array( get_option( $option ) ), 'immutable backup retained after rollback: ' . $option ); }

/* Source/asset closure. */
$root = dirname( __DIR__ );
$php = file_get_contents( $root . '/frpdepot-freight-checkout-guard/frpdepot-freight-checkout-guard.php' );
$js = file_get_contents( $root . '/frpdepot-freight-checkout-guard/assets/frpdepot-freight-quote-journey.js' );
fqj_check( false === strpos( $php, 'Product and freight review required' ), 'Choice B heading absent' );
fqj_check( false === strpos( $php, 'Get a Product & Freight Quote' ), 'Choice B action absent' );
fqj_check( false === strpos( $php, 'register_activation_hook' ), 'activation hook cannot trigger the business transaction' );
foreach ( array( 'WC_Shipping_Rate', 'add_shipping_method', 'flat_rate', 'free_shipping', 'local_pickup', 'wp_remote_get', 'wp_remote_post', 'mail(', 'wp_mail(', 'wc_create_order', 'payment_complete', 'process_payment', 'update_post_meta' ) as $token ) {
	fqj_check( false === stripos( $php, $token ), 'forbidden PHP capability absent: ' . $token );
}
foreach ( array( 'fetch(', 'XMLHttpRequest', 'WebSocket', 'localStorage', 'document.cookie', 'innerHTML', 'insertAdjacentHTML', 'gtag(', '/wc/store/', 'wc-ajax', 'allowlist', 'shipping_class', 'sku' ) as $token ) {
	fqj_check( false === stripos( $js, $token ), 'forbidden JS authority/network/storage token absent: ' . $token );
}
fqj_same( 1, substr_count( $js, 'window.sessionStorage.getItem( storageKey )' ), 'analytics reads one scoped non-PII session dedup key' );
fqj_same( 1, substr_count( $js, "window.sessionStorage.setItem( storageKey, '1' )" ), 'analytics writes one scoped non-PII session dedup key after successful push' );
fqj_check( false === strpos( $js, 'sessionStorage.clear' ) && false === strpos( $js, 'sessionStorage.removeItem' ), 'analytics cannot enumerate or clear browser storage' );
fqj_same( 1, substr_count( $js, "event: 'generate_lead'" ), 'bundled analytics has one generate_lead construction' );
$analytics_match = preg_match( "/window\.dataLayer\.push\(\s*\{(.*?)\}\s*\);/s", $js, $match );
fqj_same( 1, $analytics_match, 'analytics dataLayer object found' );
if ( $analytics_match ) {
	preg_match_all( "/^\s*([a-z_]+):/m", $match[1], $keys );
	fqj_same( array( 'event','lead_type','form_id','product_id','variation_id','source_page' ), $keys[1], 'analytics key set/order is exact six-key schema' );
}
foreach ( array( 'email', 'phone', 'company', 'postal', 'notes', 'filename', 'cart_payload', 'product_name' ) as $pii ) {
	fqj_check( false === stripos( $match[1] ?? '', $pii ), 'analytics object excludes ' . $pii );
}

printf( "passes=%d failures=%d\n", $GLOBALS['fqj_passes'], $GLOBALS['fqj_failures'] );
exit( $GLOBALS['fqj_failures'] > 0 ? 1 : 0 );
