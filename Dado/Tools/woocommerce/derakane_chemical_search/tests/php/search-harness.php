<?php
// Offline fixture harness. No WordPress, browser, network, or deployment access.
if ( $argc < 4 ) {
	fwrite( STDERR, "usage: php search-harness.php <plugin-php> <fixture-dir> <action> [json-options]\n" );
	exit( 64 );
}
$plugin_php = $argv[1];
$fixture_dir = $argv[2];
$action = $argv[3];
$options = isset( $argv[4] ) ? json_decode( $argv[4], true ) : array();

define( 'ABSPATH', __DIR__ . '/' );
class WP_Error {
	public $code;
	public $message;
	public $data;
	public function __construct( $code, $message, $data = array() ) {
		$this->code = $code; $this->message = $message; $this->data = $data;
	}
}
class WP_REST_Request {}
class WP_REST_Server { const READABLE = 'GET'; }
function plugin_dir_path( $file ) { return dirname( $file ) . DIRECTORY_SEPARATOR; }
function plugin_dir_url( $file ) { return 'https://fixture.invalid/plugin/'; }
function remove_accents( $value ) { return $value; }
function add_action() {}
function add_shortcode() {}
function register_rest_route() {}
function rest_ensure_response( $value ) { return $value; }
function is_wp_error( $value ) { return $value instanceof WP_Error; }
function __return_true() { return true; }
function wp_register_style() {}
function wp_register_script() {}
function wp_enqueue_style() {}
function wp_enqueue_script() {}
function wp_add_inline_script() {}
function rest_url( $path ) { return 'https://fixture.invalid/wp-json/' . $path; }
function wp_json_encode( $value ) { return json_encode( $value ); }
function wp_unique_id( $prefix = '' ) { static $id = 0; return $prefix . ++$id; }
function esc_url_raw( $value ) { return $value; }
function esc_attr( $value ) { return htmlspecialchars( (string) $value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8' ); }
function esc_html( $value ) { return htmlspecialchars( (string) $value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8' ); }
function absint( $value ) { return abs( (int) $value ); }
function sanitize_text_field( $value ) { return trim( (string) $value ); }

require $plugin_php;

function fixture_v2_json( $path, $contract ) {
	$decoded = json_decode( file_get_contents( $path ), true, 512, JSON_THROW_ON_ERROR );
	if ( ! is_array( $decoded ) || $contract !== ( $decoded['contract'] ?? null ) || 2 !== ( $decoded['contract_version'] ?? null ) ) {
		throw new RuntimeException( 'fixture is not the required contract v2: ' . $path );
	}
	return $decoded;
}

if ( 'search' === $action ) {
	$dataset = fixture_v2_json( $fixture_dir . '/derakane-dataset.json', 'frpdepot.derakane-search.dataset' );
	$manifest = fixture_v2_json( $fixture_dir . '/import-manifest.json', 'frpdepot.derakane-search.import-manifest' );
	$result = frpdepot_derakane_search_dataset(
		$dataset,
		$manifest,
		$options['chemical'] ?? '',
		$options['concentration'] ?? '',
		$options['resin'] ?? '',
		$options['offset'] ?? 0
	);
	if ( $result instanceof WP_Error ) {
		echo json_encode( array( 'error' => $result->code, 'message' => $result->message ) );
		exit( 0 );
	}
	echo json_encode( $result, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE );
	exit( 0 );
}
if ( 'shortcode' === $action ) {
	echo frpdepot_derakane_shortcode();
	exit( 0 );
}
if ( 'runtime-verified' === $action ) {
	echo json_encode( (bool) frpdepot_derakane_verified_import() );
	exit( 0 );
}
fwrite( STDERR, "unknown action\n" );
exit( 64 );
