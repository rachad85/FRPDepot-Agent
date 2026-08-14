<?php
/**
 * Plugin Name: FRP Depot Hetron Private History
 * Description: Provides fixed approval-gated preparation/protection and authenticated historical downloads for five retired Hetron assets.
 * Version: 1.1.0
 * Author: FRP Depot
 * Requires at least: 6.0
 * Requires PHP: 8.0
 */

defined( 'ABSPATH' ) || exit;

final class FRPDepot_Hetron_Private_History {
    private const VERSION = '1.1.0';
    private const ATTACHMENT_ID = 1832;
    private const ATTACHED_FILE = '2026/03/HETRON-CR-Guide-2007_Ineos.pdf';
    private const PRIVATE_DIRECTORY = '.frpdepot-private-history-hetron-1832';
    private const CANARY_NAME = 'access-probe.txt';
    private const CANARY_BYTES = "FRP Depot Hetron private-history access probe\n";
    private const NONCE_ACTION = 'frpdepot_hetron_private_history_transition';

    /** Five fixed records. No path, URL, hash, action or filename comes from a request. */
    private static function assets(): array {
        return array(
            'original_pdf' => array(
                'relative' => '2026/03/HETRON-CR-Guide-2007_Ineos.pdf',
                'private_name' => 'HETRON-CR-Guide-2007_Ineos.pdf',
                'download_name' => 'HETRON-CR-Guide-2007_Ineos.pdf',
                'mime' => 'application/pdf',
                'bytes' => 5740139,
                'sha256' => 'b9993ac63eeeb4994c17dd34a79d6db8e154d3ae65de1d6a98b188fb766986c5',
            ),
            'pdf_preview_full' => array(
                'relative' => '2026/03/HETRON-CR-Guide-2007_Ineos-pdf.jpg',
                'private_name' => 'HETRON-CR-Guide-2007_Ineos-pdf.jpg',
                'download_name' => 'HETRON-CR-Guide-2007_Ineos-pdf.jpg',
                'mime' => 'image/jpeg',
                'bytes' => 118876,
                'sha256' => 'c5a8cc7d2a188e6ecc6b85c52ca815c9c461234fe73602ba810f6bc817b949d2',
            ),
            'pdf_preview_medium' => array(
                'relative' => '2026/03/HETRON-CR-Guide-2007_Ineos-pdf-229x300.jpg',
                'private_name' => 'HETRON-CR-Guide-2007_Ineos-pdf-229x300.jpg',
                'download_name' => 'HETRON-CR-Guide-2007_Ineos-pdf-229x300.jpg',
                'mime' => 'image/jpeg',
                'bytes' => 10781,
                'sha256' => '5c15f53bc6559882c0718ee4c670a40d769b1fd37d5bdf40363d7ca6a072c973',
            ),
            'pdf_preview_large' => array(
                'relative' => '2026/03/HETRON-CR-Guide-2007_Ineos-pdf-783x1024.jpg',
                'private_name' => 'HETRON-CR-Guide-2007_Ineos-pdf-783x1024.jpg',
                'download_name' => 'HETRON-CR-Guide-2007_Ineos-pdf-783x1024.jpg',
                'mime' => 'image/jpeg',
                'bytes' => 67358,
                'sha256' => '5ba6213b9a7e81a9e8d000516c210af115f9f3b9b27178aeffc9989bfcb08e55',
            ),
            'pdf_preview_thumbnail' => array(
                'relative' => '2026/03/HETRON-CR-Guide-2007_Ineos-pdf-115x150.jpg',
                'private_name' => 'HETRON-CR-Guide-2007_Ineos-pdf-115x150.jpg',
                'download_name' => 'HETRON-CR-Guide-2007_Ineos-pdf-115x150.jpg',
                'mime' => 'image/jpeg',
                'bytes' => 5927,
                'sha256' => '7d64983d04041f9f3a069077ec5f8115110ca77305e1b000677b069a06f68ced',
            ),
        );
    }

    private static function normalize_path( string $path ): string {
        return rtrim( str_replace( '\\', '/', $path ), '/' );
    }

    private static function document_root(): string {
        $wordpress = realpath( ABSPATH );
        $server_root = isset( $_SERVER['DOCUMENT_ROOT'] ) ? realpath( (string) $_SERVER['DOCUMENT_ROOT'] ) : false;
        if ( false === $wordpress || false === $server_root || ! is_dir( $wordpress ) || ! is_dir( $server_root ) ) {
            throw new RuntimeException( 'document_root_unavailable' );
        }
        $wordpress = self::normalize_path( $wordpress );
        $server_root = self::normalize_path( $server_root );
        if ( $wordpress !== $server_root ) {
            throw new RuntimeException( 'wordpress_root_is_not_document_root' );
        }
        return $server_root;
    }

    private static function upload_root(): string {
        $upload = wp_upload_dir( null, false, false );
        if ( ! is_array( $upload ) || ! empty( $upload['error'] ) || empty( $upload['basedir'] ) ) {
            throw new RuntimeException( 'upload_root_unavailable' );
        }
        $root = realpath( (string) $upload['basedir'] );
        if ( false === $root || ! is_dir( $root ) ) {
            throw new RuntimeException( 'upload_root_unavailable' );
        }
        return self::normalize_path( $root );
    }

    private static function private_root( bool $must_exist ): string {
        $uploads = self::upload_root();
        $candidate = $uploads . '/' . self::PRIVATE_DIRECTORY;
        if ( $must_exist ) {
            $resolved = realpath( $candidate );
            if ( false === $resolved || self::normalize_path( $resolved ) !== $candidate || ! is_dir( $resolved ) ) {
                throw new RuntimeException( 'private_destination_unavailable' );
            }
        }
        return $candidate;
    }

    private static function exact_canary( string $path ): bool {
        clearstatcache( true, $path );
        return is_file( $path ) && ! is_link( $path )
            && strlen( self::CANARY_BYTES ) === (int) filesize( $path )
            && hash_equals( hash( 'sha256', self::CANARY_BYTES ), (string) hash_file( 'sha256', $path ) );
    }

    private static function require_private_attachment(): void {
        $post = get_post( self::ATTACHMENT_ID );
        if ( ! $post || 'attachment' !== $post->post_type || 'private' !== get_post_status( $post )
            || 'application/pdf' !== get_post_mime_type( $post )
            || self::ATTACHED_FILE !== get_post_meta( self::ATTACHMENT_ID, '_wp_attached_file', true ) ) {
            throw new RuntimeException( 'attachment_identity_or_private_status_mismatch' );
        }
    }

    private static function exact_file( string $path, array $asset ): bool {
        clearstatcache( true, $path );
        return is_file( $path )
            && ! is_link( $path )
            && (int) filesize( $path ) === (int) $asset['bytes']
            && hash_equals( (string) $asset['sha256'], (string) hash_file( 'sha256', $path ) );
    }

    /**
     * Preflight the complete five-file set before the first rename, then rename one
     * file at a time. Each rename is atomic on one filesystem; the five-file set is
     * deliberately documented and treated as non-atomic.
     */
    private static function transition_manifest( array $assets, string $from_root, string $to_root ): void {
        if ( ! is_dir( $to_root ) || is_link( $to_root )
            || ! self::exact_canary( $to_root . '/' . self::CANARY_NAME ) ) {
            throw new RuntimeException( 'prepared_private_directory_required' );
        }
        foreach ( $assets as $asset ) {
            $source = $from_root . '/' . $asset['relative'];
            $destination = $to_root . '/' . $asset['private_name'];
            if ( ! self::exact_file( $source, $asset ) || file_exists( $destination ) || is_link( $destination ) ) {
                throw new RuntimeException( 'transition_preflight_mismatch' );
            }
        }

        $source_stat = stat( $from_root );
        $destination_stat = stat( $to_root );
        if ( false === $source_stat || false === $destination_stat
            || (int) $source_stat['dev'] !== (int) $destination_stat['dev'] ) {
            throw new RuntimeException( 'transition_not_on_one_filesystem' );
        }

        foreach ( $assets as $asset ) {
            $source = $from_root . '/' . $asset['relative'];
            $destination = $to_root . '/' . $asset['private_name'];
            if ( ! rename( $source, $destination ) || ! self::exact_file( $destination, $asset ) || file_exists( $source ) ) {
                throw new RuntimeException( 'transition_indeterminate' );
            }
            @chmod( $destination, 0600 );
        }
    }

    private static function require_transition_user(): void {
        if ( ! is_user_logged_in() || ! current_user_can( 'manage_options' ) ) {
            wp_send_json_error( array( 'code' => 'not_authorized' ), 404 );
        }
        self::require_private_attachment();
    }

    private static function transition_state(): array {
        $assets = self::assets();
        $upload_root = self::upload_root();
        $private_root = self::private_root( false );
        $private_exists = file_exists( $private_root );
        $canary_path = $private_root . '/' . self::CANARY_NAME;
        $canary_exact = $private_exists && self::exact_canary( $canary_path );
        $public_exact = 0;
        $private_exact = 0;
        $records = array();
        foreach ( $assets as $role => $asset ) {
            $public_path = $upload_root . '/' . $asset['relative'];
            $private_path = $private_root . '/' . $asset['private_name'];
            $public = self::exact_file( $public_path, $asset );
            $private = self::exact_file( $private_path, $asset );
            if ( $public ) {
                ++$public_exact;
            }
            if ( $private ) {
                ++$private_exact;
            }
            $records[] = array(
                'role' => $role,
                'location' => $public ? 'public_upload' : ( $private ? 'private_history' : 'missing_or_mismatch' ),
                'bytes' => $asset['bytes'],
                'sha256' => $asset['sha256'],
            );
        }
        $state = 'mixed_or_invalid';
        if ( count( $assets ) === $public_exact && 0 === $private_exact && ! $private_exists ) {
            $state = 'public_uploads_exact_unprepared';
        } elseif ( count( $assets ) === $public_exact && 0 === $private_exact && $canary_exact ) {
            $state = 'public_uploads_exact_prepared';
        } elseif ( 0 === $public_exact && count( $assets ) === $private_exact && $canary_exact ) {
            $state = 'private_history_exact';
        }
        $document_root = self::document_root();
        $source_stat = stat( $upload_root );
        $destination_stat = stat( $private_exists ? $private_root : $upload_root );
        $same_filesystem = false !== $source_stat && false !== $destination_stat
            && (int) $source_stat['dev'] === (int) $destination_stat['dev'];
        $destination_safe = false;
        if ( ! is_link( $private_root ) ) {
            if ( ! $private_exists ) {
                $destination_safe = 'public_uploads_exact_unprepared' === $state;
            } elseif ( is_dir( $private_root ) ) {
                $entries = scandir( $private_root );
                if ( false !== $entries ) {
                    $entries = array_values( array_diff( $entries, array( '.', '..' ) ) );
                    sort( $entries );
                    $expected_entries = array_merge(
                        array( self::CANARY_NAME ),
                        array_values( array_map(
                            static fn( array $asset ): string => (string) $asset['private_name'],
                            $assets
                        ) )
                    );
                    sort( $expected_entries );
                    $destination_safe = ( 'public_uploads_exact_prepared' === $state
                            && array( self::CANARY_NAME ) === $entries )
                        || ( 'private_history_exact' === $state && $expected_entries === $entries );
                }
            }
        }
        $destination_writable = is_dir( $private_root )
            ? is_writable( $private_root )
            : is_writable( $upload_root );
        return array(
            'state' => $state,
            'attachment_id' => self::ATTACHMENT_ID,
            'attachment_status' => 'private',
            'private_root_hidden' => str_starts_with( basename( $private_root ), '.' )
                && str_starts_with( $private_root . '/', $upload_root . '/' )
                && str_starts_with( $private_root . '/', $document_root . '/' ),
            'same_filesystem' => $same_filesystem,
            'destination_safe' => $destination_safe,
            'destination_writable' => $destination_writable,
            'canary_exact' => $canary_exact,
            'assets' => $records,
        );
    }

    public static function probe(): void {
        try {
            self::require_transition_user();
            $state = self::transition_state();
            $state['nonce'] = wp_create_nonce( self::NONCE_ACTION );
            wp_send_json_success( $state, 200 );
        } catch ( Throwable $error ) {
            wp_send_json_error( array( 'code' => $error->getMessage() ), 409 );
        }
    }

    public static function prepare(): void {
        try {
            self::require_transition_user();
            check_admin_referer( self::NONCE_ACTION );
            $before = self::transition_state();
            if ( 'public_uploads_exact_unprepared' !== $before['state']
                || ! $before['private_root_hidden'] || ! $before['same_filesystem']
                || ! $before['destination_safe'] || ! $before['destination_writable'] ) {
                throw new RuntimeException( 'prepare_preflight_refused' );
            }
            $private_root = self::private_root( false );
            if ( file_exists( $private_root ) || is_link( $private_root )
                || ! mkdir( $private_root, 0700, false ) ) {
                throw new RuntimeException( 'private_directory_create_failed' );
            }
            @chmod( $private_root, 0700 );
            $canary = $private_root . '/' . self::CANARY_NAME;
            $handle = @fopen( $canary, 'xb' );
            if ( false === $handle ) {
                throw new RuntimeException( 'private_canary_create_failed' );
            }
            $written = fwrite( $handle, self::CANARY_BYTES );
            $flushed = fflush( $handle );
            if ( function_exists( 'fsync' ) ) {
                $flushed = fsync( $handle ) && $flushed;
            }
            fclose( $handle );
            @chmod( $canary, 0600 );
            if ( strlen( self::CANARY_BYTES ) !== $written || ! $flushed || ! self::exact_canary( $canary ) ) {
                throw new RuntimeException( 'private_canary_write_indeterminate' );
            }
            $after = self::transition_state();
            if ( 'public_uploads_exact_prepared' !== $after['state'] ) {
                throw new RuntimeException( 'prepare_readback_indeterminate' );
            }
            wp_send_json_success( $after, 200 );
        } catch ( Throwable $error ) {
            wp_send_json_error( array( 'code' => $error->getMessage() ), 409 );
        }
    }

    public static function protect(): void {
        try {
            self::require_transition_user();
            check_admin_referer( self::NONCE_ACTION );
            $before = self::transition_state();
            if ( 'public_uploads_exact_prepared' !== $before['state'] || ! $before['private_root_hidden']
                || ! $before['same_filesystem'] || ! $before['destination_safe'] || ! $before['destination_writable']
                || ! $before['canary_exact'] ) {
                throw new RuntimeException( 'protect_preflight_refused' );
            }
            $upload_root = self::upload_root();
            $private_root = self::private_root( false );
            if ( is_link( $private_root ) || ! is_dir( $private_root )
                || ! self::exact_canary( $private_root . '/' . self::CANARY_NAME )
                || ! is_writable( $private_root ) ) {
                throw new RuntimeException( 'private_destination_unsafe_or_unwritable' );
            }
            self::transition_manifest( self::assets(), $upload_root, $private_root );
            $after = self::transition_state();
            if ( 'private_history_exact' !== $after['state'] ) {
                throw new RuntimeException( 'protect_readback_indeterminate' );
            }
            wp_send_json_success( $after, 200 );
        } catch ( Throwable $error ) {
            wp_send_json_error( array( 'code' => $error->getMessage() ), 409 );
        }
    }

    private static function serve( string $role ): void {
        if ( ! is_user_logged_in() || ! current_user_can( 'upload_files' )
            || ! current_user_can( 'read_post', self::ATTACHMENT_ID )
            || 'private' !== get_post_status( self::ATTACHMENT_ID ) ) {
            status_header( 404 );
            exit;
        }
        $assets = self::assets();
        if ( ! isset( $assets[ $role ] ) ) {
            status_header( 404 );
            exit;
        }
        $asset = $assets[ $role ];
        try {
            $path = self::private_root( true ) . '/' . $asset['private_name'];
            if ( ! self::exact_file( $path, $asset ) ) {
                throw new RuntimeException( 'private_asset_mismatch' );
            }
        } catch ( Throwable $error ) {
            status_header( 404 );
            exit;
        }
        nocache_headers();
        header( 'Content-Type: ' . $asset['mime'] );
        header( 'Content-Length: ' . (string) $asset['bytes'] );
        header( 'Content-Disposition: attachment; filename="' . $asset['download_name'] . '"' );
        header( 'X-Content-Type-Options: nosniff' );
        readfile( $path );
        exit;
    }

    public static function serve_original_pdf(): void { self::serve( 'original_pdf' ); }
    public static function serve_preview_full(): void { self::serve( 'pdf_preview_full' ); }
    public static function serve_preview_medium(): void { self::serve( 'pdf_preview_medium' ); }
    public static function serve_preview_large(): void { self::serve( 'pdf_preview_large' ); }
    public static function serve_preview_thumbnail(): void { self::serve( 'pdf_preview_thumbnail' ); }
}

add_action( 'admin_post_frpdepot_hetron_private_history_probe', array( 'FRPDepot_Hetron_Private_History', 'probe' ) );
add_action( 'admin_post_frpdepot_hetron_private_history_prepare', array( 'FRPDepot_Hetron_Private_History', 'prepare' ) );
add_action( 'admin_post_frpdepot_hetron_private_history_protect', array( 'FRPDepot_Hetron_Private_History', 'protect' ) );
add_action( 'admin_post_frpdepot_hetron_history_original_pdf', array( 'FRPDepot_Hetron_Private_History', 'serve_original_pdf' ) );
add_action( 'admin_post_frpdepot_hetron_history_preview_full', array( 'FRPDepot_Hetron_Private_History', 'serve_preview_full' ) );
add_action( 'admin_post_frpdepot_hetron_history_preview_medium', array( 'FRPDepot_Hetron_Private_History', 'serve_preview_medium' ) );
add_action( 'admin_post_frpdepot_hetron_history_preview_large', array( 'FRPDepot_Hetron_Private_History', 'serve_preview_large' ) );
add_action( 'admin_post_frpdepot_hetron_history_preview_thumbnail', array( 'FRPDepot_Hetron_Private_History', 'serve_preview_thumbnail' ) );
