<?php
/* Offline WordPress activation harness for the fixed one-use cleanup plugin. */

define( 'ABSPATH', __DIR__ . '/fake-wordpress/' );
$GLOBALS['ffoc_events']       = array();
$GLOBALS['ffoc_activation']   = null;
$GLOBALS['ffoc_actions']      = array();
$GLOBALS['ffoc_filters']      = array();
$GLOBALS['ffoc_posts']        = array();
$GLOBALS['ffoc_path_records'] = array();
$GLOBALS['ffoc_hash_bad_at']  = 0;
$GLOBALS['ffoc_unlink_bad_at'] = 0;
$GLOBALS['ffoc_active']       = array();
$GLOBALS['ffoc_basedir']      = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'frpd-ffoc-offline-' . getmypid();

class FfocWpDie extends Exception {}
class FfocWpdb {
	public $posts = 'wp_posts';
	public $postmeta = 'wp_postmeta';
	public function prepare( $sql, ...$args ) {
		$GLOBALS['ffoc_events'][] = 'record_query:' . $args[2];
		return $args[2];
	}
	public function get_var( $relative ) {
		return isset( $GLOBALS['ffoc_path_records'][ $relative ] )
			? (string) $GLOBALS['ffoc_path_records'][ $relative ] : '0';
	}
}
$GLOBALS['wpdb'] = new FfocWpdb();

function ffoc_check( $condition, $message ) {
	static $count = 0;
	if ( ! $condition ) {
		fwrite( STDERR, "FAIL: {$message}\n" );
		exit( 1 );
	}
	++$count;
	$GLOBALS['ffoc_passed'] = $count;
}
function is_admin() { return true; }
function current_user_can( $capability ) { return 'activate_plugins' === $capability; }
function wp_upload_dir( $time = null, $create = true ) { return array( 'basedir' => $GLOBALS['ffoc_basedir'], 'error' => false ); }
function wp_normalize_path( $path ) { return str_replace( '\\', '/', $path ); }
function get_post( $id ) {
	$GLOBALS['ffoc_events'][] = 'fixed_record:' . $id;
	return $GLOBALS['ffoc_posts'][ $id ] ?? null;
}
function wp_die( $message, $title = '', $args = array() ) { throw new FfocWpDie( $message ); }
function wp_parse_url( $url ) { return parse_url( $url ); }
function add_query_arg( $args, $location ) {
	$separator = false === strpos( $location, '?' ) ? '?' : '&';
	return $location . $separator . http_build_query( $args );
}
function plugin_basename( $file ) { return 'frpdepot-fixed-four-origin-file-cleanup/frpdepot-fixed-four-origin-file-cleanup.php'; }
function register_activation_hook( $file, $callback ) { $GLOBALS['ffoc_activation'] = $callback; }
function add_action( $hook, $callback, $priority = 10, $args = 1 ) { $GLOBALS['ffoc_actions'][ $hook ] = $callback; }
function add_filter( $hook, $callback, $priority = 10, $args = 1 ) { $GLOBALS['ffoc_filters'][ $hook ] = $callback; }
function deactivate_plugins( $plugin, $silent = false ) {
	$GLOBALS['ffoc_events'][] = 'self_deactivate:' . $plugin;
	$GLOBALS['ffoc_active'] = array_values( array_diff( $GLOBALS['ffoc_active'], array( $plugin ) ) );
}

function frpd_ffoc_filesize( $path ) {
	$targets = frpd_ffoc_targets();
	foreach ( $targets as $target ) {
		if ( basename( $path ) === basename( $target['relative'] ) ) {
			$GLOBALS['ffoc_events'][] = 'size:' . $target['position'];
			return (int) $target['bytes'];
		}
	}
	return false;
}
function frpd_ffoc_hash_file( $path ) {
	foreach ( frpd_ffoc_targets() as $target ) {
		if ( basename( $path ) === basename( $target['relative'] ) ) {
			$GLOBALS['ffoc_events'][] = 'hash:' . $target['position'];
			if ( (int) $GLOBALS['ffoc_hash_bad_at'] === (int) $target['position'] ) {
				return str_repeat( '0', 64 );
			}
			return $target['sha256'];
		}
	}
	return false;
}
function frpd_ffoc_unlink( $path ) {
	$position = 0;
	foreach ( frpd_ffoc_targets() as $target ) {
		if ( basename( $path ) === basename( $target['relative'] ) ) {
			$position = (int) $target['position'];
		}
	}
	$GLOBALS['ffoc_events'][] = 'unlink:' . $position;
	if ( (int) $GLOBALS['ffoc_unlink_bad_at'] === $position ) {
		return false;
	}
	return unlink( $path );
}

require __DIR__ . '/frpdepot-fixed-four-origin-file-cleanup/frpdepot-fixed-four-origin-file-cleanup.php';

function ffoc_reset_files() {
	$GLOBALS['ffoc_events'] = array();
	$GLOBALS['ffoc_actions'] = array();
	$GLOBALS['ffoc_filters'] = array();
	$GLOBALS['ffoc_posts'] = array();
	$GLOBALS['ffoc_path_records'] = array();
	$GLOBALS['ffoc_hash_bad_at'] = 0;
	$GLOBALS['ffoc_unlink_bad_at'] = 0;
	@mkdir( $GLOBALS['ffoc_basedir'] . DIRECTORY_SEPARATOR . '2026' . DIRECTORY_SEPARATOR . '08', 0777, true );
	$GLOBALS['ffoc_basedir'] = realpath( $GLOBALS['ffoc_basedir'] );
	foreach ( frpd_ffoc_targets() as $target ) {
		$path = $GLOBALS['ffoc_basedir'] . DIRECTORY_SEPARATOR . str_replace( '/', DIRECTORY_SEPARATOR, $target['relative'] );
		file_put_contents( $path, 'offline-' . $target['position'] );
	}
}
function ffoc_remove_tree() {
	foreach ( frpd_ffoc_targets() as $target ) {
		$path = $GLOBALS['ffoc_basedir'] . DIRECTORY_SEPARATOR . str_replace( '/', DIRECTORY_SEPARATOR, $target['relative'] );
		@unlink( $path );
	}
	@rmdir( $GLOBALS['ffoc_basedir'] . DIRECTORY_SEPARATOR . '2026' . DIRECTORY_SEPARATOR . '08' );
	@rmdir( $GLOBALS['ffoc_basedir'] . DIRECTORY_SEPARATOR . '2026' );
	@rmdir( $GLOBALS['ffoc_basedir'] );
}

ffoc_check( is_callable( $GLOBALS['ffoc_activation'] ), 'one activation callback registered' );
ffoc_check( 4 === count( frpd_ffoc_targets() ), 'exactly four targets' );

/* Complete preflight precedes the first unlink; success schedules same-request deactivation. */
ffoc_reset_files();
call_user_func( $GLOBALS['ffoc_activation'] );
$first_unlink = array_search( 'unlink:1', $GLOBALS['ffoc_events'], true );
ffoc_check( false !== $first_unlink, 'first unlink occurred' );
foreach ( array( 'size:4', 'hash:4', 'fixed_record:5527', 'record_query:2026/08/04_manway_real_bore_flange_detail.png' ) as $event ) {
	ffoc_check( array_search( $event, $GLOBALS['ffoc_events'], true ) < $first_unlink, "all preflight evidence precedes first unlink: {$event}" );
}
ffoc_check( array( 'unlink:1', 'unlink:2', 'unlink:3', 'unlink:4' ) === array_values( array_filter( $GLOBALS['ffoc_events'], function ( $event ) { return 0 === strpos( $event, 'unlink:' ); } ) ), 'unlink order is exact' );
ffoc_check( isset( $GLOBALS['ffoc_actions']['shutdown'] ), 'shutdown self-deactivation scheduled' );
ffoc_check( isset( $GLOBALS['ffoc_filters']['wp_redirect'] ), 'bounded result redirect scheduled' );
$GLOBALS['ffoc_active'][] = plugin_basename( __FILE__ );
call_user_func( $GLOBALS['ffoc_actions']['shutdown'] );
ffoc_check( array() === $GLOBALS['ffoc_active'], 'plugin self-deactivated after modelled core activation' );
$redirect = call_user_func( $GLOBALS['ffoc_filters']['wp_redirect'], 'plugins.php?activate=true&plugin_status=all&paged=1&s=' );
ffoc_check( false !== strpos( $redirect, 'frpd_ffoc_result=deleted-4-self-deactivation-scheduled' ), 'bounded result marker exposed' );

/* Any preflight failure leaves all four files untouched. */
ffoc_reset_files();
$GLOBALS['ffoc_hash_bad_at'] = 4;
try {
	call_user_func( $GLOBALS['ffoc_activation'] );
	ffoc_check( false, 'bad fourth hash must refuse' );
} catch ( FfocWpDie $error ) {
	ffoc_check( false !== strpos( $error->getMessage(), 'FRPD_FFOC_HASH_P4;DELETED=0;TOTAL=4' ), 'hash refusal is bounded' );
}
ffoc_check( 0 === count( array_filter( $GLOBALS['ffoc_events'], function ( $event ) { return 0 === strpos( $event, 'unlink:' ); } ) ), 'preflight failure performed no unlink' );

/* Existing fixed ID or exact path attachment record refuses before any unlink. */
ffoc_reset_files();
$GLOBALS['ffoc_posts'][5523] = (object) array( 'ID' => 5523 );
try { call_user_func( $GLOBALS['ffoc_activation'] ); } catch ( FfocWpDie $error ) {
	ffoc_check( false !== strpos( $error->getMessage(), 'FRPD_FFOC_FIXED_RECORD_P2' ), 'fixed record refusal bounded' );
}
ffoc_check( 0 === count( array_filter( $GLOBALS['ffoc_events'], function ( $event ) { return 0 === strpos( $event, 'unlink:' ); } ) ), 'fixed record performed no unlink' );

ffoc_reset_files();
$GLOBALS['ffoc_path_records']['2026/08/03_manway_real_laminate_detail.png'] = 1;
try { call_user_func( $GLOBALS['ffoc_activation'] ); } catch ( FfocWpDie $error ) {
	ffoc_check( false !== strpos( $error->getMessage(), 'FRPD_FFOC_PATH_RECORD_P3' ), 'path record refusal bounded' );
}
ffoc_check( 0 === count( array_filter( $GLOBALS['ffoc_events'], function ( $event ) { return 0 === strpos( $event, 'unlink:' ); } ) ), 'path record performed no unlink' );

/* A later unlink failure preserves earlier deletion and stops later files. */
ffoc_reset_files();
$GLOBALS['ffoc_unlink_bad_at'] = 3;
try { call_user_func( $GLOBALS['ffoc_activation'] ); } catch ( FfocWpDie $error ) {
	ffoc_check( false !== strpos( $error->getMessage(), 'FRPD_FFOC_UNLINK_P3;DELETED=2;TOTAL=4' ), 'partial failure reports bounded count' );
}
$unlinks = array_values( array_filter( $GLOBALS['ffoc_events'], function ( $event ) { return 0 === strpos( $event, 'unlink:' ); } ) );
ffoc_check( array( 'unlink:1', 'unlink:2', 'unlink:3' ) === $unlinks, 'partial failure stops before fourth unlink' );
ffoc_check( ! file_exists( $GLOBALS['ffoc_basedir'] . '/2026/08/01_manway_real_hero.png' ), 'first earlier deletion remains' );
ffoc_check( file_exists( $GLOBALS['ffoc_basedir'] . '/2026/08/04_manway_real_bore_flange_detail.png' ), 'later file remains after failure' );

$source = file_get_contents( __DIR__ . '/frpdepot-fixed-four-origin-file-cleanup/frpdepot-fixed-four-origin-file-cleanup.php' );
foreach ( array( 'admin_post_', 'wp_ajax_', 'rest_api_init', 'register_rest_route', 'wp_remote_post', 'wp_mail(', 'update_post_meta(', 'wp_insert_post(', 'wp_delete_post(', 'delete_post_meta(', 'woocommerce_', '/wc/v3', 'eval(', 'exec(', 'shell_exec' ) as $forbidden ) {
	ffoc_check( false === stripos( $source, $forbidden ), "forbidden route/capability absent: {$forbidden}" );
}
ffoc_check( false !== strpos( $source, "add_action( 'shutdown'" ), 'only shutdown action is present' );
ffoc_check( false !== strpos( $source, 'return unlink( $path );' ), 'production wrapper uses native unlink' );

ffoc_remove_tree();
echo 'PASS ' . $GLOBALS['ffoc_passed'] . "\n";
