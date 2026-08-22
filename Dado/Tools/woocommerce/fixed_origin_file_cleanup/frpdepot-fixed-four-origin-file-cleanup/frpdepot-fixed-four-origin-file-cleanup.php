<?php
/**
 * Plugin Name: FRP Depot Fixed Four Origin File Cleanup
 * Description: One-use activation cleanup for four exact unregistered August 2026 origin files.
 * Version: 1.0.0
 * Author: FRP Depot
 * Requires at least: 6.0
 * Requires PHP: 7.4
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'FRPD_FFOC_VERSION', '1.0.0' );
define( 'FRPD_FFOC_RESULT_QUERY', 'frpd_ffoc_result' );

/** Return the complete, immutable four-file contract. */
function frpd_ffoc_targets() {
	return array(
		array(
			'position'      => 1,
			'attachment_id' => 5521,
			'relative'      => '2026/08/01_manway_real_hero.png',
			'bytes'         => 261492,
			'sha256'        => 'db886ee83d211d755ffc5e095b3546351f9b01478be73d1a71c5b299a1643be6',
		),
		array(
			'position'      => 2,
			'attachment_id' => 5523,
			'relative'      => '2026/08/02_manway_real_alternate.png',
			'bytes'         => 366491,
			'sha256'        => '07d1678e976152a5fdc8ccdc0396a43a92e0055125fffc587508b354c747484b',
		),
		array(
			'position'      => 3,
			'attachment_id' => 5525,
			'relative'      => '2026/08/03_manway_real_laminate_detail.png',
			'bytes'         => 301011,
			'sha256'        => '572741ffd433acbc8b2bd36dbd9cb2afe02dbd8b6346978c38a7c0d4f8a352d9',
		),
		array(
			'position'      => 4,
			'attachment_id' => 5527,
			'relative'      => '2026/08/04_manway_real_bore_flange_detail.png',
			'bytes'         => 416461,
			'sha256'        => 'c5742b9ee84370d2ed6034d891955ff1a7774e89c1f1ad1ffd5b2b5d14bfd753',
		),
	);
}

/* Narrow wrappers permit a no-network, no-site PHP harness. Production defaults
 * call the native read/delete primitives directly. */
if ( ! function_exists( 'frpd_ffoc_filesize' ) ) {
	function frpd_ffoc_filesize( $path ) {
		return filesize( $path );
	}
}
if ( ! function_exists( 'frpd_ffoc_hash_file' ) ) {
	function frpd_ffoc_hash_file( $path ) {
		return hash_file( 'sha256', $path );
	}
}
if ( ! function_exists( 'frpd_ffoc_unlink' ) ) {
	function frpd_ffoc_unlink( $path ) {
		return unlink( $path );
	}
}

/** Compare canonical paths without weakening case checks on case-sensitive hosts. */
function frpd_ffoc_same_path( $left, $right ) {
	$left  = wp_normalize_path( $left );
	$right = wp_normalize_path( $right );
	if ( defined( 'PHP_OS_FAMILY' ) && 'Windows' === PHP_OS_FAMILY ) {
		return strtolower( $left ) === strtolower( $right );
	}
	return $left === $right;
}

/** Stop activation with a finite, path-free result. */
function frpd_ffoc_fail( $reason, $position = 0, $deleted = 0 ) {
	$reasons = array(
		'capability', 'uploads', 'base_path', 'missing', 'alias', 'path', 'size',
		'hash', 'fixed_record', 'path_record', 'record_read', 'unlink', 'still_present',
	);
	if ( ! in_array( $reason, $reasons, true ) ) {
		$reason = 'record_read';
	}
	$position = is_int( $position ) && $position >= 0 && $position <= 4 ? $position : 0;
	$deleted  = is_int( $deleted ) && $deleted >= 0 && $deleted <= 4 ? $deleted : 0;
	$code     = 'FRPD_FFOC_' . strtoupper( $reason ) . '_P' . $position;
	wp_die(
		'FRPD_FFOC_RESULT=' . $code . ';DELETED=' . $deleted . ';TOTAL=4',
		'Fixed origin cleanup refused',
		array( 'response' => 409 )
	);
}

/**
 * Preflight every target and every attachment-record absence before any unlink.
 * Returns only exact absolute paths generated from wp_upload_dir().
 */
function frpd_ffoc_preflight() {
	global $wpdb;

	if ( ! is_admin() || ! current_user_can( 'activate_plugins' ) ) {
		frpd_ffoc_fail( 'capability' );
	}
	$uploads = wp_upload_dir( null, false );
	if ( ! is_array( $uploads ) || ! empty( $uploads['error'] ) || empty( $uploads['basedir'] ) ) {
		frpd_ffoc_fail( 'uploads' );
	}
	$base      = rtrim( (string) $uploads['basedir'], '/\\' );
	$base_real = realpath( $base );
	if ( false === $base_real || ! frpd_ffoc_same_path( $base_real, $base ) ) {
		frpd_ffoc_fail( 'base_path' );
	}
	if ( ! is_object( $wpdb ) || empty( $wpdb->posts ) || empty( $wpdb->postmeta ) ) {
		frpd_ffoc_fail( 'record_read' );
	}

	$prepared = array();
	foreach ( frpd_ffoc_targets() as $target ) {
		$position = (int) $target['position'];
		$path     = $base . DIRECTORY_SEPARATOR
			. str_replace( '/', DIRECTORY_SEPARATOR, $target['relative'] );
		if ( ! is_file( $path ) ) {
			frpd_ffoc_fail( 'missing', $position );
		}
		if ( is_link( $path ) ) {
			frpd_ffoc_fail( 'alias', $position );
		}
		$real = realpath( $path );
		if ( false === $real || ! frpd_ffoc_same_path( $real, $path ) ) {
			frpd_ffoc_fail( 'path', $position );
		}
		clearstatcache( true, $path );
		$size = frpd_ffoc_filesize( $path );
		if ( ! is_int( $size ) || $size !== (int) $target['bytes'] ) {
			frpd_ffoc_fail( 'size', $position );
		}
		$hash = frpd_ffoc_hash_file( $path );
		if ( ! is_string( $hash ) || ! hash_equals( $target['sha256'], strtolower( $hash ) ) ) {
			frpd_ffoc_fail( 'hash', $position );
		}
		if ( null !== get_post( (int) $target['attachment_id'] ) ) {
			frpd_ffoc_fail( 'fixed_record', $position );
		}
		$sql = $wpdb->prepare(
			"SELECT COUNT(*) FROM {$wpdb->posts} p INNER JOIN {$wpdb->postmeta} pm ON pm.post_id = p.ID WHERE p.post_type = %s AND pm.meta_key = %s AND pm.meta_value = %s",
			'attachment',
			'_wp_attached_file',
			$target['relative']
		);
		$count = $wpdb->get_var( $sql );
		if ( null === $count || ! preg_match( '/^[0-9]+$/', (string) $count ) ) {
			frpd_ffoc_fail( 'record_read', $position );
		}
		if ( 0 !== (int) $count ) {
			frpd_ffoc_fail( 'path_record', $position );
		}
		$prepared[] = array(
			'position' => $position,
			'path'     => $path,
		);
	}
	if ( 4 !== count( $prepared ) ) {
		frpd_ffoc_fail( 'record_read' );
	}
	return $prepared;
}

/** Add a finite success marker only to WordPress's activation-result redirect. */
function frpd_ffoc_result_redirect( $location ) {
	$parts = wp_parse_url( $location );
	if ( ! is_array( $parts ) || empty( $parts['path'] ) || 'plugins.php' !== basename( $parts['path'] ) ) {
		return $location;
	}
	$query = array();
	if ( ! empty( $parts['query'] ) ) {
		parse_str( $parts['query'], $query );
	}
	if ( ! isset( $query['activate'] ) || 'true' !== (string) $query['activate'] ) {
		return $location;
	}
	return add_query_arg(
		array(
			FRPD_FFOC_RESULT_QUERY => 'deleted-4-self-deactivation-scheduled',
			'frpd_ffoc_count'      => '4',
		),
		$location
	);
}

/** Self-deactivate after core has added the plugin to active_plugins. */
function frpd_ffoc_shutdown_deactivate() {
	if ( ! function_exists( 'deactivate_plugins' ) ) {
		require_once ABSPATH . 'wp-admin/includes/plugin.php';
	}
	deactivate_plugins( plugin_basename( __FILE__ ), true );
}

/** Exact one-use activation transaction. */
function frpd_ffoc_activate() {
	$prepared = frpd_ffoc_preflight();
	$deleted  = 0;
	foreach ( $prepared as $item ) {
		if ( ! frpd_ffoc_unlink( $item['path'] ) ) {
			frpd_ffoc_fail( 'unlink', (int) $item['position'], $deleted );
		}
		clearstatcache( true, $item['path'] );
		if ( file_exists( $item['path'] ) || is_link( $item['path'] ) ) {
			frpd_ffoc_fail( 'still_present', (int) $item['position'], $deleted );
		}
		++$deleted;
	}
	if ( 4 !== $deleted ) {
		frpd_ffoc_fail( 'still_present', 0, $deleted );
	}
	$GLOBALS['frpd_ffoc_bounded_result'] = 'deleted-4-self-deactivation-scheduled';
	add_filter( 'wp_redirect', 'frpd_ffoc_result_redirect', PHP_INT_MAX, 1 );
	add_action( 'shutdown', 'frpd_ffoc_shutdown_deactivate', PHP_INT_MAX, 0 );
}

register_activation_hook( __FILE__, 'frpd_ffoc_activate' );
