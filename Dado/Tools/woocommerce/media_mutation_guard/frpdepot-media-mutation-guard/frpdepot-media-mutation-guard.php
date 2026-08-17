<?php
/**
 * Plugin Name: FRP Depot Media Mutation Guard
 * Description: Fixed atomic snapshot and five-family media mutation guard for FRP Depot.
 * Version: 1.0.1
 * Author: FRP Depot
 */

if (!defined('ABSPATH')) {
    exit;
}

define('FRPD_MG_VERSION', '1.0.1');
define('FRPD_MG_MANIFEST_SHA256', '2e8fdde2ba90aedb07de5bddb64a4dc4d02b82a2db88deba4605bdbfa6f18d8b');
define('FRPD_MG_OPTION', 'frpd_media_mutation_guard_v1');
define('FRPD_MG_COOKIE', 'frpd_media_guard_owner');
define('FRPD_MG_LOCK_NAME', 'frpd_media_mutation_guard_v1');
define('FRPD_MG_TTL', 1800);
define('FRPD_MG_MAX_ATTACHMENTS', 1000);
define('FRPD_MG_MAX_TOTAL_BYTES', 2147483648);
define('FRPD_MG_PRIVATE_ATTACHMENT_ID', 1832);
define('FRPD_MG_PRIVATE_ATTACHMENT_FILE', '2026/03/HETRON-CR-Guide-2007_Ineos.pdf');
define('FRPD_MG_PRIVATE_ATTACHMENT_SLUG', 'hetron-cr-guide-2007_ineos');
define('FRPD_MG_PRIVATE_ATTACHMENT_DATE_GMT', '2026-03-17 15:20:38');
define('FRPD_MG_PRIVATE_ATTACHMENT_MIME', 'application/pdf');
define('FRPD_MG_PRIVATE_PROTECTOR_FILE', 'frpdepot-hetron-private-history/frpdepot-hetron-private-history.php');
define('FRPD_MG_PRIVATE_PROTECTOR_SHA256', '8c06b73a3a76ac2da7e7e9bba25c3f8a31ecdad917b30d436e41b32784b17116');

/** Return the exact closed runtime manifest or fail closed. */
function frpd_mg_manifest() {
    static $manifest = null;
    if (is_array($manifest)) {
        return $manifest;
    }
    $path = plugin_dir_path(__FILE__) . 'approved-media.json';
    $raw = is_readable($path) ? file_get_contents($path) : false;
    if (!is_string($raw) || !hash_equals(FRPD_MG_MANIFEST_SHA256, hash('sha256', $raw))) {
        return new WP_Error('frpd_mg_manifest', 'The fixed media manifest digest is invalid.');
    }
    $value = is_string($raw) ? json_decode($raw, true) : null;
    $ids = array(
        'stub_flange' => 1368,
        'open_manway' => 1397,
        'manway_cover' => 1411,
        'elbow_90' => 1423,
        'pipe' => 1455,
    );
    $actual_family_keys = is_array($value['families'] ?? null)
        ? array_keys($value['families']) : array();
    $expected_family_keys = array_keys($ids);
    sort($actual_family_keys, SORT_STRING);
    sort($expected_family_keys, SORT_STRING);
    if (!is_array($value) || 1 !== ($value['schema'] ?? null)
        || !isset($value['families']) || !is_array($value['families'])
        || $actual_family_keys !== $expected_family_keys) {
        return new WP_Error('frpd_mg_manifest', 'The fixed media manifest is invalid.');
    }
    foreach ($ids as $family => $product_id) {
        $record = $value['families'][$family] ?? null;
        if (!is_array($record) || $product_id !== ($record['product_id'] ?? null)
            || !isset($record['images']) || !is_array($record['images'])
            || 4 !== count($record['images'])) {
            return new WP_Error('frpd_mg_manifest', 'A fixed family manifest record is invalid.');
        }
        foreach ($record['images'] as $index => $image) {
            if (!is_array($image) || ($index + 1) !== ($image['position'] ?? null)
                || !is_string($image['filename'] ?? null)
                || basename($image['filename']) !== $image['filename']
                || '.png' !== strtolower(substr($image['filename'], -4))
                || !is_int($image['bytes'] ?? null) || $image['bytes'] <= 0
                || !is_string($image['sha256'] ?? null)
                || 1 !== preg_match('/\A[a-f0-9]{64}\z/', $image['sha256'])) {
                return new WP_Error('frpd_mg_manifest', 'A fixed image manifest record is invalid.');
            }
        }
    }
    $manifest = $value;
    return $manifest;
}

/** Hold the one MySQL advisory lock until PHP request shutdown. */
function frpd_mg_hold_request_lock() {
    if (!empty($GLOBALS['frpd_mg_lock_held'])) {
        return frpd_mg_assert_lock_owned();
    }
    global $wpdb;
    $result = $wpdb->get_var($wpdb->prepare('SELECT GET_LOCK(%s, %d)', FRPD_MG_LOCK_NAME, 10));
    if ('1' !== (string) $result) {
        return new WP_Error('frpd_mg_lock_busy', 'The media mutation lock is busy.');
    }
    $connection_id = (int) $wpdb->get_var('SELECT CONNECTION_ID()');
    $owner_id = (int) $wpdb->get_var($wpdb->prepare('SELECT IS_USED_LOCK(%s)', FRPD_MG_LOCK_NAME));
    if ($connection_id <= 0 || $owner_id !== $connection_id) {
        return new WP_Error('frpd_mg_lock_owner', 'The media mutation lock owner is indeterminate.');
    }
    $GLOBALS['frpd_mg_lock_held'] = true;
    $GLOBALS['frpd_mg_lock_connection_id'] = $connection_id;
    add_action('shutdown', 'frpd_mg_release_request_lock', PHP_INT_MAX);
    return true;
}

function frpd_mg_assert_lock_owned() {
    if (empty($GLOBALS['frpd_mg_lock_held'])
        || empty($GLOBALS['frpd_mg_lock_connection_id'])) {
        return new WP_Error('frpd_mg_lock_owner', 'The media mutation lock is not held.');
    }
    global $wpdb;
    $connection_id = (int) $wpdb->get_var('SELECT CONNECTION_ID()');
    $owner_id = (int) $wpdb->get_var($wpdb->prepare('SELECT IS_USED_LOCK(%s)', FRPD_MG_LOCK_NAME));
    if ($connection_id !== (int) $GLOBALS['frpd_mg_lock_connection_id']
        || $owner_id !== $connection_id) {
        return new WP_Error('frpd_mg_lock_owner', 'The database connection no longer owns the media mutation lock.');
    }
    return true;
}

function frpd_mg_release_request_lock() {
    if (empty($GLOBALS['frpd_mg_lock_held'])) {
        return;
    }
    global $wpdb;
    if (!is_wp_error(frpd_mg_assert_lock_owned())) {
        $wpdb->get_var($wpdb->prepare('SELECT RELEASE_LOCK(%s)', FRPD_MG_LOCK_NAME));
    }
    $GLOBALS['frpd_mg_lock_held'] = false;
    $GLOBALS['frpd_mg_lock_connection_id'] = 0;
}

/** The live guard is one uncached InnoDB row; no option or object cache is consulted. */
function frpd_mg_table_name() {
    global $wpdb;
    return $wpdb->prefix . 'frpd_media_guard';
}

function frpd_mg_install_table() {
    $locked = frpd_mg_hold_request_lock();
    if (is_wp_error($locked)) {
        wp_die($locked->get_error_message(), 'Media guard activation refused');
    }
    global $wpdb;
    $table = frpd_mg_table_name();
    $sql = "CREATE TABLE IF NOT EXISTS `{$table}` ("
        . "guard_id TINYINT UNSIGNED NOT NULL PRIMARY KEY,"
        . "schema_version INT UNSIGNED NOT NULL,"
        . "family VARCHAR(64) NOT NULL,"
        . "product_id BIGINT UNSIGNED NOT NULL,"
        . "manifest_sha256 CHAR(64) NOT NULL,"
        . "snapshot_sha256 CHAR(64) NOT NULL,"
        . "snapshot_count BIGINT UNSIGNED NOT NULL,"
        . "owner_user_id BIGINT UNSIGNED NOT NULL,"
        . "owner_session_sha256 CHAR(64) NOT NULL,"
        . "owner_token_sha256 CHAR(64) NOT NULL,"
        . "issued_utc DATETIME(6) NOT NULL,"
        . "expires_utc DATETIME(6) NOT NULL,"
        . "state_status VARCHAR(16) NOT NULL,"
        . "completed_utc DATETIME(6) NULL,"
        . "state_version BIGINT UNSIGNED NOT NULL,"
        . "reserved_json LONGTEXT NOT NULL,"
        . "attachments_json LONGTEXT NOT NULL"
        . ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4";
    if (false === $wpdb->query($sql)) {
        wp_die('The fixed media guard InnoDB table could not be created.', 'Media guard activation refused');
    }
    if (is_wp_error(frpd_mg_assert_lock_owned())) {
        wp_die('Advisory-lock ownership changed while creating the guard table.', 'Media guard activation refused');
    }
    $status = $wpdb->get_row($wpdb->prepare('SHOW TABLE STATUS LIKE %s', $table), ARRAY_A);
    if (is_wp_error(frpd_mg_assert_lock_owned()) || !is_array($status)
        || 'innodb' !== strtolower((string) ($status['Engine'] ?? ''))) {
        wp_die('The fixed media guard table is not authoritative InnoDB.', 'Media guard activation refused');
    }
}
register_activation_hook(__FILE__, 'frpd_mg_install_table');

function frpd_mg_refuse_active_deactivation() {
    $locked = frpd_mg_hold_request_lock();
    if (is_wp_error($locked)) {
        wp_die('FRP Depot media guard deactivation cannot prove the advisory lock.', 'Deactivation refused');
    }
    $state = frpd_mg_active_state();
    if (is_wp_error($state) || is_array($state)) {
        wp_die('FRP Depot media guard deactivation is refused while guard state is active or indeterminate.', 'Deactivation refused');
    }
}
register_deactivation_hook(__FILE__, 'frpd_mg_refuse_active_deactivation');

/** Return an active, directly-read guard row, null when absent/expired, or WP_Error. */
function frpd_mg_active_state() {
    $owned = frpd_mg_assert_lock_owned();
    if (is_wp_error($owned)) {
        return $owned;
    }
    global $wpdb;
    $wpdb->last_error = '';
    $table = frpd_mg_table_name();
    $row = $wpdb->get_row(
        $wpdb->prepare(
            "SELECT guard_state.*, (guard_state.state_status IN ('active','gallery') AND guard_state.expires_utc > UTC_TIMESTAMP(6)) AS is_active, "
            . "UNIX_TIMESTAMP(guard_state.expires_utc) AS expires_epoch, lock_state.db_connection_id, lock_state.lock_owner_id "
            . "FROM (SELECT CONNECTION_ID() AS db_connection_id, IS_USED_LOCK(%s) AS lock_owner_id) AS lock_state "
            . "LEFT JOIN `{$table}` AS guard_state ON guard_state.guard_id = 1",
            FRPD_MG_LOCK_NAME
        ),
        ARRAY_A
    );
    if (!is_array($row)) {
        if ('' !== (string) $wpdb->last_error) {
            return new WP_Error('frpd_mg_state_read', 'The uncached media guard state could not be read.');
        }
        return null;
    }
    if ((int) ($row['db_connection_id'] ?? 0) !== (int) $GLOBALS['frpd_mg_lock_connection_id']
        || (int) ($row['lock_owner_id'] ?? 0) !== (int) $GLOBALS['frpd_mg_lock_connection_id']) {
        return new WP_Error('frpd_mg_lock_owner', 'The state read did not retain advisory-lock ownership.');
    }
    if (empty($row['guard_id']) || '1' !== (string) ($row['is_active'] ?? '0')) {
        return null;
    }
    $reserved = json_decode((string) ($row['reserved_json'] ?? ''), true);
    $attachments = json_decode((string) ($row['attachments_json'] ?? ''), true);
    $state = array(
        'schema' => (int) ($row['schema_version'] ?? 0),
        'family' => (string) ($row['family'] ?? ''),
        'product_id' => (int) ($row['product_id'] ?? 0),
        'manifest_sha256' => (string) ($row['manifest_sha256'] ?? ''),
        'snapshot_sha256' => (string) ($row['snapshot_sha256'] ?? ''),
        'snapshot_count' => (int) ($row['snapshot_count'] ?? -1),
        'owner_user_id' => (int) ($row['owner_user_id'] ?? 0),
        'owner_session_sha256' => (string) ($row['owner_session_sha256'] ?? ''),
        'owner_hash' => (string) ($row['owner_token_sha256'] ?? ''),
        'expires' => (int) ($row['expires_epoch'] ?? 0),
        'state_status' => (string) ($row['state_status'] ?? ''),
        'state_version' => (int) ($row['state_version'] ?? 0),
        'reserved' => $reserved,
        'attachments' => $attachments,
    );
    $manifest = frpd_mg_manifest();
    if (1 !== $state['schema'] || !is_array($reserved) || !is_array($attachments)
        || $state['state_version'] <= 0 || $state['expires'] <= 0
        || !hash_equals(FRPD_MG_MANIFEST_SHA256, $state['manifest_sha256'])
        || is_wp_error($manifest) || !isset($manifest['families'][$state['family']])
        || $state['product_id'] !== (int) $manifest['families'][$state['family']]['product_id']) {
        return new WP_Error('frpd_mg_state_invalid', 'The uncached media guard state is invalid.');
    }
    return $state;
}

function frpd_mg_owner_matches($state) {
    $secret = isset($_COOKIE[FRPD_MG_COOKIE]) ? (string) $_COOKIE[FRPD_MG_COOKIE] : '';
    $session = function_exists('wp_get_session_token') ? (string) wp_get_session_token() : '';
    return '' !== $secret && '' !== $session
        && (int) get_current_user_id() === (int) $state['owner_user_id']
        && hash_equals($state['owner_session_sha256'], hash('sha256', $session))
        && hash_equals($state['owner_hash'], hash('sha256', $secret));
}

function frpd_mg_expected_images($family) {
    $manifest = frpd_mg_manifest();
    if (is_wp_error($manifest) || !is_string($family)
        || !isset($manifest['families'][$family])) {
        return new WP_Error('frpd_mg_family', 'The requested family is not fixed.');
    }
    return $manifest['families'][$family]['images'];
}

function frpd_mg_fixed_image_lookup() {
    $manifest = frpd_mg_manifest();
    if (is_wp_error($manifest)) {
        return $manifest;
    }
    $names = array();
    $hashes = array();
    foreach ($manifest['families'] as $family => $record) {
        foreach ($record['images'] as $image) {
            $identity = array('family' => $family, 'position' => $image['position']);
            $names[strtolower($image['filename'])] = $identity;
            $stem = strtolower(pathinfo($image['filename'], PATHINFO_FILENAME));
            $names[$stem] = $identity;
            $hashes[$image['sha256']] = $identity;
        }
    }
    return array('names' => $names, 'hashes' => $hashes);
}

/** Prove the one intentionally private attachment and its fixed protector plugin. */
function frpd_mg_private_attachment_projection($attachment_id) {
    if ((int) $attachment_id !== FRPD_MG_PRIVATE_ATTACHMENT_ID) {
        return new WP_Error('frpd_mg_private_attachment', 'The private attachment exception ID is invalid.');
    }
    $post = get_post(FRPD_MG_PRIVATE_ATTACHMENT_ID);
    if (!$post || 'attachment' !== ($post->post_type ?? null)
        || 'private' !== get_post_status($post)
        || FRPD_MG_PRIVATE_ATTACHMENT_MIME !== get_post_mime_type($post)
        || FRPD_MG_PRIVATE_ATTACHMENT_SLUG !== ($post->post_name ?? null)
        || FRPD_MG_PRIVATE_ATTACHMENT_DATE_GMT !== ($post->post_date_gmt ?? null)
        || FRPD_MG_PRIVATE_ATTACHMENT_FILE
            !== get_post_meta(FRPD_MG_PRIVATE_ATTACHMENT_ID, '_wp_attached_file', true)) {
        return new WP_Error(
            'frpd_mg_private_attachment',
            'The fixed private attachment identity or status changed.'
        );
    }
    if (!defined('WP_PLUGIN_DIR') || !function_exists('is_plugin_active')
        || !is_plugin_active(FRPD_MG_PRIVATE_PROTECTOR_FILE)) {
        return new WP_Error(
            'frpd_mg_private_attachment',
            'The fixed private-attachment protector is not active.'
        );
    }
    $protector_path = WP_PLUGIN_DIR . DIRECTORY_SEPARATOR
        . str_replace('/', DIRECTORY_SEPARATOR, FRPD_MG_PRIVATE_PROTECTOR_FILE);
    $protector_digest = is_readable($protector_path)
        ? hash_file('sha256', $protector_path) : false;
    if (!is_string($protector_digest)
        || !hash_equals(FRPD_MG_PRIVATE_PROTECTOR_SHA256, $protector_digest)) {
        return new WP_Error(
            'frpd_mg_private_attachment',
            'The fixed private-attachment protector digest changed.'
        );
    }
    return array(
        'attachment_id' => FRPD_MG_PRIVATE_ATTACHMENT_ID,
        'attached_file' => FRPD_MG_PRIVATE_ATTACHMENT_FILE,
        'post_name' => FRPD_MG_PRIVATE_ATTACHMENT_SLUG,
        'post_status' => 'private',
        'mime_type' => FRPD_MG_PRIVATE_ATTACHMENT_MIME,
        'post_date_gmt' => FRPD_MG_PRIVATE_ATTACHMENT_DATE_GMT,
        'protector_plugin_file' => FRPD_MG_PRIVATE_PROTECTOR_FILE,
        'protector_plugin_sha256' => FRPD_MG_PRIVATE_PROTECTOR_SHA256,
    );
}

/** Build a complete, bounded original-file snapshot while the advisory lock is held. */
function frpd_mg_snapshot($family, $mode, $guard_active = false) {
    if (empty($GLOBALS['frpd_mg_lock_held'])) {
        return new WP_Error('frpd_mg_unlocked', 'Snapshot attempted without the media lock.');
    }
    $expected = frpd_mg_expected_images($family);
    $lookup = frpd_mg_fixed_image_lookup();
    if (is_wp_error($expected) || is_wp_error($lookup)) {
        return is_wp_error($expected) ? $expected : $lookup;
    }
    global $wpdb;
    $wpdb->last_error = '';
    $ids = $wpdb->get_col(
        "SELECT ID FROM {$wpdb->posts} WHERE post_type = 'attachment' " .
        "AND post_status <> 'trash' ORDER BY ID ASC"
    );
    if ('' !== trim((string) $wpdb->last_error)) {
        return new WP_Error('frpd_mg_snapshot_query', 'Attachment enumeration could not be proven complete.');
    }
    if (!is_array($ids) || count($ids) > FRPD_MG_MAX_ATTACHMENTS) {
        return new WP_Error('frpd_mg_snapshot_bound', 'Attachment snapshot exceeded row bounds.');
    }
    $rows = array();
    $failures = array();
    $name_conflicts = array();
    $hash_conflicts = array();
    $fixed_matches = array();
    $private_exceptions = array();
    $total_bytes = 0;
    foreach ($ids as $raw_id) {
        $owner = frpd_mg_assert_lock_owned();
        if (is_wp_error($owner)) {
            return $owner;
        }
        $attachment_id = (int) $raw_id;
        if (FRPD_MG_PRIVATE_ATTACHMENT_ID === $attachment_id) {
            $private = frpd_mg_private_attachment_projection($attachment_id);
            if (!is_wp_error($private)) {
                $private_exceptions[] = $private;
            } else {
                $failures[] = array(
                    'attachment_id' => $attachment_id,
                    'reason' => 'private_attachment_proof_failed',
                );
            }
            continue;
        }
        $path = get_attached_file($attachment_id, true);
        if ($attachment_id <= 0 || !is_string($path) || '' === $path
            || !is_file($path) || !is_readable($path)) {
            $failures[] = array('attachment_id' => $attachment_id, 'reason' => 'unreadable_original');
            continue;
        }
        $size = filesize($path);
        $digest = hash_file('sha256', $path);
        $owner = frpd_mg_assert_lock_owned();
        if (is_wp_error($owner)) {
            return $owner;
        }
        if (false === $size || !is_string($digest) || 64 !== strlen($digest)) {
            $failures[] = array('attachment_id' => $attachment_id, 'reason' => 'hash_failed');
            continue;
        }
        $total_bytes += (int) $size;
        if ($total_bytes > FRPD_MG_MAX_TOTAL_BYTES) {
            return new WP_Error('frpd_mg_snapshot_bound', 'Attachment snapshot exceeded byte bounds.');
        }
        $filename = basename($path);
        $stem = strtolower(pathinfo($filename, PATHINFO_FILENAME));
        $normalized_stem = preg_replace('/-\d+\z/', '', $stem);
        $row = array(
            'attachment_id' => $attachment_id,
            'filename' => $filename,
            'bytes' => (int) $size,
            'sha256' => $digest,
        );
        $rows[] = $row;
        $name_identity = $lookup['names'][strtolower($filename)]
            ?? $lookup['names'][$normalized_stem] ?? null;
        if (is_array($name_identity) && $name_identity['family'] === $family) {
            $name_conflicts[] = array(
                'attachment_id' => $attachment_id,
                'fixed_position' => $name_identity['position'],
            );
        }
        $hash_identity = $lookup['hashes'][$digest] ?? null;
        if (is_array($hash_identity) && $hash_identity['family'] === $family) {
            $hash_conflicts[] = array(
                'attachment_id' => $attachment_id,
                'fixed_position' => $hash_identity['position'],
            );
        }
        if (is_array($name_identity) && is_array($hash_identity)
            && $name_identity === $hash_identity
            && $name_identity['family'] === $family) {
            foreach ($expected as $fixed_image) {
                if ($fixed_image['position'] === $name_identity['position']
                    && hash_equals($fixed_image['filename'], $filename)) {
                    $fixed_matches[] = array(
                        'attachment_id' => $attachment_id,
                        'fixed_position' => $name_identity['position'],
                    );
                    break;
                }
            }
        }
    }
    $complete = count($failures) === 0
        && count($rows) + count($private_exceptions) === count($ids);
    $canonical = wp_json_encode(
        array('readable_attachments' => $rows, 'private_exceptions' => $private_exceptions),
        JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE
    );
    return array(
        'schema' => 2,
        'plugin_version' => FRPD_MG_VERSION,
        'mode' => $mode,
        'family' => $family,
        'generated_utc' => gmdate('c'),
        'attachment_total' => count($ids),
        'hashed_total' => count($rows),
        'total_bytes' => $total_bytes,
        'snapshot_sha256' => hash('sha256', (string) $canonical),
        'complete' => $complete,
        'failures' => $failures,
        'private_exceptions' => $private_exceptions,
        'name_conflicts' => $name_conflicts,
        'hash_conflicts' => $hash_conflicts,
        'fixed_matches' => $fixed_matches,
        'guard_active' => (bool) $guard_active,
    );
}

function frpd_mg_require_admin() {
    if (!is_user_logged_in() || !current_user_can('manage_options')
        || !current_user_can('upload_files')) {
        wp_die('FRP Depot media guard access refused.', 'Access refused', array('response' => 403));
    }
}

function frpd_mg_render_proof($title, $proof, $response = 200) {
    status_header($response);
    nocache_headers();
    $json = wp_json_encode(
        $proof,
        JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE |
        JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT
    );
    echo '<!doctype html><html><head><meta charset="utf-8"><title>' . esc_html($title) . '</title></head><body>';
    echo '<h1>' . esc_html($title) . '</h1>';
    echo '<script type="application/json" id="frpd-media-guard-proof">' . $json . '</script>';
    echo '<p><a href="' . esc_url(admin_url('tools.php?page=frpd-media-mutation-guard')) . '">Return to guard</a></p>';
    echo '</body></html>';
    exit;
}

function frpd_mg_fail($message, $response = 409) {
    wp_die(esc_html($message), 'FRP Depot Media Guard Refusal', array('response' => $response));
}

function frpd_mg_post_family() {
    $family = isset($_POST['family']) ? sanitize_key(wp_unslash($_POST['family'])) : '';
    $expected = frpd_mg_expected_images($family);
    if (is_wp_error($expected)) {
        frpd_mg_fail('The family is not one of the five fixed families.');
    }
    return $family;
}

function frpd_mg_handle_snapshot() {
    frpd_mg_require_admin();
    check_admin_referer('frpd_mg_snapshot');
    $family = frpd_mg_post_family();
    $locked = frpd_mg_hold_request_lock();
    if (is_wp_error($locked)) {
        frpd_mg_fail($locked->get_error_message(), 423);
    }
    $proof = frpd_mg_snapshot($family, 'atomic_snapshot', false);
    if (is_wp_error($proof)) {
        frpd_mg_fail($proof->get_error_message());
    }
    frpd_mg_render_proof('FRP Depot Atomic Media Snapshot', $proof);
}
add_action('admin_post_frpd_media_guard_snapshot', 'frpd_mg_handle_snapshot');

function frpd_mg_clear_expired_state_under_lock() {
    $owned = frpd_mg_assert_lock_owned();
    if (is_wp_error($owned)) {
        return $owned;
    }
    global $wpdb;
    $table = frpd_mg_table_name();
    $retired = $wpdb->query($wpdb->prepare(
        "UPDATE `{$table}` SET state_status = 'expired', completed_utc = UTC_TIMESTAMP(6), "
        . "state_version = state_version + 1 WHERE guard_id = 1 "
        . "AND state_status IN ('active','gallery') "
        . "AND expires_utc <= UTC_TIMESTAMP(6) AND CONNECTION_ID() = %d "
        . "AND IS_USED_LOCK(%s) = CONNECTION_ID()",
        (int) $GLOBALS['frpd_mg_lock_connection_id'],
        FRPD_MG_LOCK_NAME
    ));
    return false === $retired
        ? new WP_Error('frpd_mg_state_retire', 'Expired guard state could not be retired.')
        : true;
}

function frpd_mg_insert_state($family, $secret, $proof) {
    $owned = frpd_mg_assert_lock_owned();
    if (is_wp_error($owned)) {
        return $owned;
    }
    global $wpdb;
    $manifest = frpd_mg_manifest();
    $session = function_exists('wp_get_session_token') ? (string) wp_get_session_token() : '';
    $user_id = (int) get_current_user_id();
    if (is_wp_error($manifest) || '' === $session || $user_id <= 0) {
        return new WP_Error('frpd_mg_owner', 'The guard owner session is unavailable.');
    }
    $times = $wpdb->get_row(
        $wpdb->prepare(
            'SELECT UTC_TIMESTAMP(6) AS issued_utc, '
            . 'TIMESTAMPADD(SECOND, %d, UTC_TIMESTAMP(6)) AS expires_utc, '
            . 'UNIX_TIMESTAMP(TIMESTAMPADD(SECOND, %d, UTC_TIMESTAMP(6))) AS expires_epoch, '
            . 'CONNECTION_ID() AS db_connection_id, IS_USED_LOCK(%s) AS lock_owner_id',
            FRPD_MG_TTL,
            FRPD_MG_TTL,
            FRPD_MG_LOCK_NAME
        ),
        ARRAY_A
    );
    if (!is_array($times) || empty($times['issued_utc']) || empty($times['expires_utc'])
        || (int) ($times['expires_epoch'] ?? 0) <= 0
        || (int) ($times['db_connection_id'] ?? 0) !== (int) $GLOBALS['frpd_mg_lock_connection_id']
        || (int) ($times['lock_owner_id'] ?? 0) !== (int) $GLOBALS['frpd_mg_lock_connection_id']) {
        return new WP_Error('frpd_mg_db_time', 'Database UTC guard time is unavailable.');
    }
    $table = frpd_mg_table_name();
    $columns = 'guard_id,schema_version,family,product_id,manifest_sha256,snapshot_sha256,snapshot_count,'
        . 'owner_user_id,owner_session_sha256,owner_token_sha256,issued_utc,expires_utc,state_status,'
        . 'completed_utc,state_version,reserved_json,attachments_json';
    $written = $wpdb->query($wpdb->prepare(
        "INSERT INTO `{$table}` ({$columns}) SELECT 1,1,%s,%d,%s,%s,%d,%d,%s,%s,%s,%s,'active',NULL,1,'[]','{}' "
        . "FROM DUAL WHERE CONNECTION_ID() = %d AND IS_USED_LOCK(%s) = CONNECTION_ID() "
        . "ON DUPLICATE KEY UPDATE schema_version=VALUES(schema_version),family=VALUES(family),"
        . "product_id=VALUES(product_id),manifest_sha256=VALUES(manifest_sha256),snapshot_sha256=VALUES(snapshot_sha256),"
        . "snapshot_count=VALUES(snapshot_count),owner_user_id=VALUES(owner_user_id),"
        . "owner_session_sha256=VALUES(owner_session_sha256),owner_token_sha256=VALUES(owner_token_sha256),"
        . "issued_utc=VALUES(issued_utc),expires_utc=VALUES(expires_utc),state_status=VALUES(state_status),"
        . "completed_utc=NULL,state_version=1,reserved_json='[]',attachments_json='{}'",
        $family,
        (int) $manifest['families'][$family]['product_id'],
        FRPD_MG_MANIFEST_SHA256,
        (string) $proof['snapshot_sha256'],
        (int) $proof['attachment_total'],
        $user_id,
        hash('sha256', $session),
        hash('sha256', $secret),
        (string) $times['issued_utc'],
        (string) $times['expires_utc'],
        (int) $GLOBALS['frpd_mg_lock_connection_id'],
        FRPD_MG_LOCK_NAME
    ));
    if ((int) $written <= 0) {
        return new WP_Error('frpd_mg_state_insert', 'The media guard state could not be created.');
    }
    $state = frpd_mg_active_state();
    if (!is_array($state) || $state['family'] !== $family
        || !hash_equals((string) $proof['snapshot_sha256'], $state['snapshot_sha256'])) {
        return new WP_Error('frpd_mg_state_verify', 'The media guard state could not be verified.');
    }
    return $state;
}

function frpd_mg_handle_acquire() {
    frpd_mg_require_admin();
    check_admin_referer('frpd_mg_acquire');
    $family = frpd_mg_post_family();
    $locked = frpd_mg_hold_request_lock();
    if (is_wp_error($locked)) {
        frpd_mg_fail($locked->get_error_message(), 423);
    }
    $cleared = frpd_mg_clear_expired_state_under_lock();
    if (is_wp_error($cleared)) {
        frpd_mg_fail($cleared->get_error_message(), 500);
    }
    $existing = frpd_mg_active_state();
    if (is_wp_error($existing)) {
        frpd_mg_fail($existing->get_error_message(), 500);
    }
    if (is_array($existing)) {
        frpd_mg_fail('A media guard is already active.', 423);
    }
    $thumbnail_identity = frpd_mg_gallery_meta_key_identity('_thumbnail_id');
    $gallery_identity = frpd_mg_gallery_meta_key_identity('_product_image_gallery');
    if (is_wp_error($thumbnail_identity) || is_wp_error($gallery_identity)
        || '_thumbnail_id' !== $thumbnail_identity
        || '_product_image_gallery' !== $gallery_identity) {
        frpd_mg_fail('The live post metadata collation could not be proven safe.', 500);
    }
    $proof = frpd_mg_snapshot($family, 'pre_guard_snapshot', false);
    if (is_wp_error($proof)) {
        frpd_mg_fail($proof->get_error_message());
    }
    if (!$proof['complete'] || $proof['name_conflicts'] || $proof['hash_conflicts']) {
        $proof['mode'] = 'guard_refused';
        frpd_mg_render_proof('FRP Depot Media Guard Refused', $proof, 409);
    }
    try {
        $secret = bin2hex(random_bytes(32));
    } catch (Exception $exception) {
        frpd_mg_fail('Secure guard ownership could not be created.', 500);
    }
    $state = frpd_mg_insert_state($family, $secret, $proof);
    if (is_wp_error($state)) {
        frpd_mg_fail($state->get_error_message(), 500);
    }
    setcookie(FRPD_MG_COOKIE, $secret, array(
        'expires' => $state['expires'],
        'path' => '/wp-admin/',
        'secure' => true,
        'httponly' => true,
        'samesite' => 'Strict',
    ));
    $_COOKIE[FRPD_MG_COOKIE] = $secret;
    $proof['mode'] = 'guard_acquired';
    $proof['guard_active'] = true;
    $proof['guard_expires_utc'] = gmdate('c', $state['expires']);
    $proof['reserved_uploads'] = 0;
    frpd_mg_render_proof('FRP Depot Media Guard Acquired', $proof);
}
add_action('admin_post_frpd_media_guard_acquire', 'frpd_mg_handle_acquire');

function frpd_mg_handle_guarded_snapshot() {
    frpd_mg_require_admin();
    check_admin_referer('frpd_mg_guarded_snapshot');
    $locked = frpd_mg_hold_request_lock();
    if (is_wp_error($locked)) {
        frpd_mg_fail($locked->get_error_message(), 423);
    }
    $state = frpd_mg_active_state();
    if (is_wp_error($state)) {
        frpd_mg_fail($state->get_error_message(), 500);
    }
    if (!is_array($state) || !frpd_mg_owner_matches($state)) {
        frpd_mg_fail('The active media guard is not owned by this browser.', 403);
    }
    $proof = frpd_mg_snapshot($state['family'], 'guarded_snapshot', true);
    if (is_wp_error($proof)) {
        frpd_mg_fail($proof->get_error_message());
    }
    $proof['guard_expires_utc'] = gmdate('c', $state['expires']);
    $proof['reserved_uploads'] = count($state['reserved']);
    frpd_mg_render_proof('FRP Depot Guarded Media Snapshot', $proof);
}
add_action('admin_post_frpd_media_guard_guarded_snapshot', 'frpd_mg_handle_guarded_snapshot');

/** Return one strict primary-plus-gallery ID list, or WP_Error on malformed live metadata. */
function frpd_mg_product_gallery_ids($product_id) {
    $product_id = (int) $product_id;
    if ($product_id <= 0 || 'product' !== get_post_type($product_id)) {
        return new WP_Error('frpd_mg_gallery_product', 'The fixed gallery product identity is unavailable.');
    }
    $primary = (int) get_post_thumbnail_id($product_id);
    $raw = (string) get_post_meta($product_id, '_product_image_gallery', true);
    $ids = array();
    if ($primary > 0) {
        $ids[] = $primary;
    }
    if ('' !== $raw) {
        foreach (explode(',', $raw) as $part) {
            if (!preg_match('/^[1-9][0-9]*$/D', $part)) {
                return new WP_Error('frpd_mg_gallery_metadata', 'The live gallery metadata is malformed.');
            }
            $ids[] = (int) $part;
        }
    }
    if (count(array_unique($ids)) !== count($ids)) {
        return new WP_Error('frpd_mg_gallery_duplicate', 'The live gallery contains a duplicate attachment ID.');
    }
    return $ids;
}

function frpd_mg_gallery_etag($ids) {
    return is_array($ids)
        ? '"' . hash('sha256', wp_json_encode(array_values($ids), JSON_UNESCAPED_SLASHES)) . '"'
        : '';
}

/**
 * Atomically claim the one exact images-only WooCommerce product update.
 *
 * The If-Match value is a non-secret SHA-256 of the complete pre-write gallery ID
 * list. The advisory lock and active->gallery state transition serialize every
 * guarded product-gallery metadata route against this claim.
 */
function frpd_mg_rest_pre_insert_product_object($object, $request, $creating) {
    $locked = frpd_mg_hold_request_lock();
    $state = is_wp_error($locked) ? $locked : frpd_mg_active_state();
    if (is_wp_error($state)) {
        return new WP_Error('frpd_mg_gallery_lock', 'FRP Depot media guard could not prove the product-gallery lock.', array('status' => 423));
    }
    if (!is_array($state)) {
        return $object;
    }
    $object_id = is_object($object) && method_exists($object, 'get_id') ? (int) $object->get_id() : 0;
    if ($object_id !== (int) $state['product_id']) {
        return $object;
    }
    $method = is_object($request) && method_exists($request, 'get_method')
        ? strtoupper((string) $request->get_method()) : '';
    $json = is_object($request) && method_exists($request, 'get_json_params')
        ? $request->get_json_params() : null;
    $query = is_object($request) && method_exists($request, 'get_query_params')
        ? $request->get_query_params() : null;
    $body = is_object($request) && method_exists($request, 'get_body_params')
        ? $request->get_body_params() : null;
    $files = is_object($request) && method_exists($request, 'get_file_params')
        ? $request->get_file_params() : null;
    $if_match = is_object($request) && method_exists($request, 'get_header')
        ? (string) $request->get_header('if-match') : '';
    $expected = frpd_mg_expected_images($state['family']);
    if ($creating || 'PUT' !== $method || 'active' !== ($state['state_status'] ?? '')
        || !is_array($json) || array_keys($json) !== array('images')
        || !is_array($query) || array() !== $query
        || !is_array($body) || array() !== $body
        || !is_array($files) || array() !== $files
        || is_wp_error($expected) || 4 !== count($expected)) {
        return new WP_Error('frpd_mg_gallery_request', 'FRP Depot media guard refused a non-fixed product mutation.', array('status' => 423));
    }
    $filenames = array_column($expected, 'filename');
    if ($state['reserved'] !== $filenames || array_keys($state['attachments']) !== $filenames
        || count(array_unique(array_values($state['attachments']))) !== 4
        || !is_array($json['images']) || 4 !== count($json['images'])) {
        return new WP_Error('frpd_mg_gallery_set', 'The four fixed upload identities are not exact.', array('status' => 423));
    }
    $ids = array();
    foreach ($json['images'] as $index => $image) {
        $filename = $filenames[$index];
        $id = (int) ($state['attachments'][$filename] ?? 0);
        if (!is_array($image) || array_keys($image) !== array('id')
            || !is_int($image['id']) || $image['id'] !== $id || $id <= 0) {
            return new WP_Error('frpd_mg_gallery_payload', 'The product gallery payload is not the exact fixed ID sequence.', array('status' => 423));
        }
        $ids[] = $id;
    }
    $current = frpd_mg_product_gallery_ids($object_id);
    if (is_wp_error($current) || !hash_equals(frpd_mg_gallery_etag($current), $if_match)) {
        return new WP_Error('frpd_mg_gallery_precondition', 'The product gallery changed before the conditional update.', array('status' => 412));
    }
    global $wpdb;
    $next = (int) $state['state_version'] + 1;
    $updated = $wpdb->query($wpdb->prepare(
        "UPDATE `" . frpd_mg_table_name() . "` SET state_status = 'gallery', state_version = %d "
        . "WHERE guard_id = 1 AND state_status = 'active' AND state_version = %d "
        . "AND expires_utc > UTC_TIMESTAMP(6) AND CONNECTION_ID() = %d "
        . "AND IS_USED_LOCK(%s) = CONNECTION_ID()",
        $next,
        (int) $state['state_version'],
        (int) $GLOBALS['frpd_mg_lock_connection_id'],
        FRPD_MG_LOCK_NAME
    ));
    $fresh = 1 === (int) $updated ? frpd_mg_active_state()
        : new WP_Error('frpd_mg_gallery_claim', 'The conditional gallery claim did not land atomically.');
    if (!is_array($fresh) || 'gallery' !== ($fresh['state_status'] ?? '')
        || $next !== (int) ($fresh['state_version'] ?? 0)) {
        return new WP_Error('frpd_mg_gallery_claim', 'The conditional gallery claim could not be verified.', array('status' => 423));
    }
    $GLOBALS['frpd_mg_allowed_gallery_request'] = true;
    $GLOBALS['frpd_mg_allowed_gallery_product_id'] = $object_id;
    $GLOBALS['frpd_mg_allowed_gallery_ids'] = $ids;
    return $object;
}
add_filter('woocommerce_rest_pre_insert_product_object', 'frpd_mg_rest_pre_insert_product_object', PHP_INT_MIN, 3);

/** Resolve protected postmeta keys with the live meta_key column's exact MySQL collation. */
function frpd_mg_gallery_meta_key_identity($meta_key) {
    global $wpdb;
    $wpdb->last_error = '';
    $schema = $wpdb->get_row($wpdb->prepare(
        "SELECT CHARACTER_SET_NAME AS charset_name, COLLATION_NAME AS collation_name "
        . "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        . "AND TABLE_NAME = %s AND COLUMN_NAME = 'meta_key'",
        (string) $wpdb->postmeta
    ), ARRAY_A);
    $charset = is_array($schema) ? strtolower((string) ($schema['charset_name'] ?? '')) : '';
    $collation = is_array($schema) ? strtolower((string) ($schema['collation_name'] ?? '')) : '';
    if ('' !== trim((string) $wpdb->last_error)
        || 1 !== preg_match('/\A[a-z0-9_]+\z/', $charset)
        || 1 !== preg_match('/\A[a-z0-9_]+\z/', $collation)) {
        return new WP_Error('frpd_mg_meta_collation', 'The post metadata collation is unavailable.');
    }
    $sql = "SELECT CASE "
        . "WHEN CONVERT(%s USING {$charset}) COLLATE {$collation} "
        . "= CONVERT('_thumbnail_id' USING {$charset}) COLLATE {$collation} THEN '_thumbnail_id' "
        . "WHEN CONVERT(%s USING {$charset}) COLLATE {$collation} "
        . "= CONVERT('_product_image_gallery' USING {$charset}) COLLATE {$collation} "
        . "THEN '_product_image_gallery' ELSE '' END";
    $wpdb->last_error = '';
    $identity = $wpdb->get_var($wpdb->prepare($sql, (string) $meta_key, (string) $meta_key));
    if ('' !== trim((string) $wpdb->last_error)
        || !in_array((string) $identity, array('', '_thumbnail_id', '_product_image_gallery'), true)) {
        return new WP_Error('frpd_mg_meta_collation', 'The post metadata key could not be classified safely.');
    }
    return '' === (string) $identity ? null : (string) $identity;
}

function frpd_mg_product_gallery_metadata_filter($check, $object_id, $meta_key, $meta_value = null, $prev_value = null) {
    if ('product' !== get_post_type((int) $object_id)) {
        return $check;
    }
    $locked = frpd_mg_hold_request_lock();
    $state = is_wp_error($locked) ? $locked : frpd_mg_active_state();
    if (is_wp_error($state)) {
        return false;
    }
    if (!is_array($state) || (int) $object_id !== (int) $state['product_id']) {
        return $check;
    }
    $identity = frpd_mg_gallery_meta_key_identity($meta_key);
    if (is_wp_error($identity)) {
        return false;
    }
    if (!is_string($identity)) {
        return $check;
    }
    $ids = $GLOBALS['frpd_mg_allowed_gallery_ids'] ?? array();
    $allowed = 'gallery' === ($state['state_status'] ?? '')
        && !empty($GLOBALS['frpd_mg_allowed_gallery_request']) && is_array($ids)
        && 4 === count($ids)
        && (int) $object_id === (int) ($GLOBALS['frpd_mg_allowed_gallery_product_id'] ?? 0)
        && (('_thumbnail_id' === $identity && (string) $meta_value === (string) $ids[0])
            || ('_product_image_gallery' === $identity
                && (string) $meta_value === implode(',', array_slice($ids, 1))));
    return $allowed ? $check : false;
}
add_filter('add_post_metadata', 'frpd_mg_product_gallery_metadata_filter', PHP_INT_MIN, 5);
add_filter('update_post_metadata', 'frpd_mg_product_gallery_metadata_filter', PHP_INT_MIN, 5);

function frpd_mg_delete_product_gallery_metadata($delete, $object_id, $meta_key, $meta_value = null, $delete_all = false) {
    if ('product' !== get_post_type((int) $object_id)) {
        return $delete;
    }
    $locked = frpd_mg_hold_request_lock();
    $state = is_wp_error($locked) ? $locked : frpd_mg_active_state();
    if (is_wp_error($state)) {
        return false;
    }
    if (!is_array($state) || (int) $object_id !== (int) $state['product_id']) {
        return $delete;
    }
    $identity = frpd_mg_gallery_meta_key_identity($meta_key);
    return is_wp_error($identity) || is_string($identity) ? false : $delete;
}
add_filter('delete_post_metadata', 'frpd_mg_delete_product_gallery_metadata', PHP_INT_MIN, 5);

function frpd_mg_post_metadata_by_mid_record($meta_id) {
    $meta_id = (int) $meta_id;
    if ($meta_id <= 0) {
        return new WP_Error('frpd_mg_meta_mid', 'A post metadata ID is invalid.');
    }
    $record = get_metadata_by_mid('post', $meta_id);
    if (!is_object($record) || !isset($record->post_id, $record->meta_key)) {
        return new WP_Error('frpd_mg_meta_mid', 'The post metadata identity is unavailable.');
    }
    return array(
        'object_id' => (int) $record->post_id,
        'meta_key' => (string) $record->meta_key,
    );
}

/** Close WordPress's separate update_metadata_by_mid() filter path. */
function frpd_mg_update_post_metadata_by_mid($check, $meta_id, $meta_value, $meta_key = false) {
    $locked = frpd_mg_hold_request_lock();
    $state = is_wp_error($locked) ? $locked : frpd_mg_active_state();
    if (is_wp_error($state)) {
        return false;
    }
    if (!is_array($state)) {
        return $check;
    }
    $record = frpd_mg_post_metadata_by_mid_record($meta_id);
    if (is_wp_error($record)) {
        return false;
    }
    $object_id = (int) $record['object_id'];
    $existing_key = (string) $record['meta_key'];
    $proposed_key = is_string($meta_key) && '' !== $meta_key ? $meta_key : $existing_key;
    if ('attachment' === get_post_type($object_id)) {
        return false;
    }
    if ((int) $state['product_id'] === $object_id) {
        $existing_identity = frpd_mg_gallery_meta_key_identity($existing_key);
        $proposed_identity = frpd_mg_gallery_meta_key_identity($proposed_key);
        if (is_wp_error($existing_identity) || is_wp_error($proposed_identity)) {
            return false;
        }
        if (is_string($existing_identity) || is_string($proposed_identity)) {
            if ($existing_key !== $proposed_key || !is_string($existing_identity)) {
                return false;
            }
            return frpd_mg_product_gallery_metadata_filter(
                $check, $object_id, $existing_identity, $meta_value, null
            );
        }
    }
    return $check;
}
add_filter('update_post_metadata_by_mid', 'frpd_mg_update_post_metadata_by_mid', PHP_INT_MIN, 4);

/** Close WordPress's separate delete_metadata_by_mid() filter path. */
function frpd_mg_delete_post_metadata_by_mid($delete, $meta_id) {
    $locked = frpd_mg_hold_request_lock();
    $state = is_wp_error($locked) ? $locked : frpd_mg_active_state();
    if (is_wp_error($state)) {
        return false;
    }
    if (!is_array($state)) {
        return $delete;
    }
    $record = frpd_mg_post_metadata_by_mid_record($meta_id);
    if (is_wp_error($record)) {
        return false;
    }
    $object_id = (int) $record['object_id'];
    if ('attachment' === get_post_type($object_id)) {
        return false;
    }
    if ((int) $state['product_id'] === $object_id) {
        $identity = frpd_mg_gallery_meta_key_identity((string) $record['meta_key']);
        if (is_wp_error($identity) || is_string($identity)) {
            return false;
        }
    }
    return $delete;
}
add_filter('delete_post_metadata_by_mid', 'frpd_mg_delete_post_metadata_by_mid', PHP_INT_MIN, 2);

function frpd_mg_completion_proof($state) {
    $owned = frpd_mg_assert_lock_owned();
    $expected = is_array($state) ? frpd_mg_expected_images($state['family'])
        : new WP_Error('frpd_mg_state', 'The completion state is unavailable.');
    if (is_wp_error($owned) || is_wp_error($expected)) {
        return is_wp_error($owned) ? $owned : $expected;
    }
    if (!frpd_mg_owner_matches($state) || 'gallery' !== ($state['state_status'] ?? '')) {
        return new WP_Error('frpd_mg_owner', 'The completion browser does not own the active guard.');
    }
    $filenames = array_column($expected, 'filename');
    if ($state['reserved'] !== $filenames || array_keys($state['attachments']) !== $filenames
        || count(array_unique(array_values($state['attachments']))) !== 4) {
        return new WP_Error('frpd_mg_completion_set', 'The four fixed upload identities are not exact.');
    }
    $snapshot = frpd_mg_snapshot($state['family'], 'completion_snapshot', true);
    if (is_wp_error($snapshot) || !$snapshot['complete']
        || (int) $snapshot['attachment_total'] !== (int) $state['snapshot_count'] + 4
        || count($snapshot['name_conflicts']) !== 4 || count($snapshot['hash_conflicts']) !== 4
        || count($snapshot['fixed_matches']) !== 4) {
        return is_wp_error($snapshot) ? $snapshot
            : new WP_Error('frpd_mg_completion_snapshot', 'The complete live attachment proof is not exact.');
    }
    $by_position = array();
    foreach ($snapshot['fixed_matches'] as $match) {
        $position = (int) $match['fixed_position'];
        if (isset($by_position[$position])) {
            return new WP_Error('frpd_mg_completion_duplicate', 'A fixed attachment position is duplicated.');
        }
        $by_position[$position] = (int) $match['attachment_id'];
    }
    $ids = array();
    foreach ($expected as $image) {
        $position = (int) $image['position'];
        $filename = (string) $image['filename'];
        $id = (int) ($state['attachments'][$filename] ?? 0);
        if ($id <= 0 || ($by_position[$position] ?? 0) !== $id) {
            return new WP_Error('frpd_mg_completion_identity', 'A fixed attachment does not match its live bytes.');
        }
        $ids[] = $id;
    }
    $product_id = (int) $state['product_id'];
    $gallery = (string) get_post_meta($product_id, '_product_image_gallery', true);
    if ('product' !== get_post_type($product_id)
        || (int) get_post_thumbnail_id($product_id) !== $ids[0]
        || !hash_equals(implode(',', array_slice($ids, 1)), $gallery)) {
        return new WP_Error('frpd_mg_completion_gallery', 'The fixed product gallery does not match the four verified attachments.');
    }
    return array(
        'schema' => 2,
        'plugin_version' => FRPD_MG_VERSION,
        'mode' => 'guard_completed',
        'family' => $state['family'],
        'product_id' => $product_id,
        'attachment_ids' => $ids,
        'attachment_total' => $snapshot['attachment_total'],
        'snapshot_sha256' => $snapshot['snapshot_sha256'],
    );
}

function frpd_mg_complete_state($state) {
    $owned = frpd_mg_assert_lock_owned();
    if (is_wp_error($owned)) { return $owned; }
    global $wpdb;
    $next = (int) $state['state_version'] + 1;
    $table = frpd_mg_table_name();
    $expected = frpd_mg_expected_images($state['family']);
    if (is_wp_error($expected) || count($expected) !== 4) {
        return new WP_Error('frpd_mg_completion_identity', 'The fixed completion identity is unavailable.');
    }
    $ids = array();
    foreach ($expected as $image) {
        $id = (int) ($state['attachments'][$image['filename']] ?? 0);
        if ($id <= 0) {
            return new WP_Error('frpd_mg_completion_identity', 'A fixed completion attachment is missing.');
        }
        $ids[] = $id;
    }
    $posts = $wpdb->posts;
    $postmeta = $wpdb->postmeta;
    $gallery = implode(',', array_slice($ids, 1));
    $updated = $wpdb->query($wpdb->prepare(
        "UPDATE `{$table}` SET state_status = 'completed', completed_utc = UTC_TIMESTAMP(6), "
        . "state_version = %d WHERE guard_id = 1 AND state_status = 'gallery' "
        . "AND state_version = %d AND expires_utc > UTC_TIMESTAMP(6) "
        . "AND CONNECTION_ID() = %d AND IS_USED_LOCK(%s) = CONNECTION_ID() "
        . "AND EXISTS (SELECT 1 FROM `{$posts}` WHERE ID = %d AND post_type = 'product') "
        . "AND 1 = (SELECT COUNT(*) FROM `{$postmeta}` WHERE post_id = %d AND meta_key = '_thumbnail_id') "
        . "AND 1 = (SELECT COUNT(*) FROM `{$postmeta}` WHERE post_id = %d AND meta_key = '_thumbnail_id' AND meta_value = %s) "
        . "AND 1 = (SELECT COUNT(*) FROM `{$postmeta}` WHERE post_id = %d AND meta_key = '_product_image_gallery') "
        . "AND 1 = (SELECT COUNT(*) FROM `{$postmeta}` WHERE post_id = %d AND meta_key = '_product_image_gallery' AND meta_value = %s)",
        $next,
        (int) $state['state_version'],
        (int) $GLOBALS['frpd_mg_lock_connection_id'],
        FRPD_MG_LOCK_NAME,
        (int) $state['product_id'],
        (int) $state['product_id'],
        (int) $state['product_id'],
        (string) $ids[0],
        (int) $state['product_id'],
        (int) $state['product_id'],
        $gallery
    ));
    if (1 !== (int) $updated) {
        frpd_mg_clear_expired_state_under_lock();
        return new WP_Error('frpd_mg_completion_drift', 'The guard changed before completion could be recorded.');
    }
    $row = $wpdb->get_row('SELECT state_status, state_version FROM `' . frpd_mg_table_name() . '` WHERE guard_id = 1', ARRAY_A);
    if (!is_array($row) || 'completed' !== ($row['state_status'] ?? '')
        || $next !== (int) ($row['state_version'] ?? 0)) {
        return new WP_Error('frpd_mg_completion_verify', 'The completed guard row could not be verified.');
    }
    return true;
}

function frpd_mg_handle_complete() {
    frpd_mg_require_admin();
    check_admin_referer('frpd_mg_complete');
    $locked = frpd_mg_hold_request_lock();
    if (is_wp_error($locked)) { frpd_mg_fail($locked->get_error_message(), 423); }
    $state = frpd_mg_active_state();
    if (!is_array($state)) {
        frpd_mg_fail(is_wp_error($state) ? $state->get_error_message() : 'No active guard exists.', 409);
    }
    $proof = frpd_mg_completion_proof($state);
    if (is_wp_error($proof)) { frpd_mg_fail($proof->get_error_message(), 409); }
    $completed = frpd_mg_complete_state($state);
    if (is_wp_error($completed)) { frpd_mg_fail($completed->get_error_message(), 500); }
    setcookie(FRPD_MG_COOKIE, '', array(
        'expires' => 1, 'path' => '/wp-admin/', 'secure' => true, 'httponly' => true, 'samesite' => 'Strict',
    ));
    unset($_COOKIE[FRPD_MG_COOKIE]);
    frpd_mg_render_proof('FRP Depot Media Guard Completed', $proof);
}
add_action('admin_post_frpd_media_guard_complete', 'frpd_mg_handle_complete');

function frpd_mg_update_state($state, $reserved, $attachments) {
    $owned = frpd_mg_assert_lock_owned();
    if (is_wp_error($owned)) {
        return $owned;
    }
    global $wpdb;
    $next_version = (int) $state['state_version'] + 1;
    $table = frpd_mg_table_name();
    $updated = $wpdb->query($wpdb->prepare(
        "UPDATE `{$table}` SET reserved_json = %s, attachments_json = %s, state_version = %d "
        . "WHERE guard_id = 1 AND state_status = 'active' AND state_version = %d "
        . "AND expires_utc > UTC_TIMESTAMP(6) AND CONNECTION_ID() = %d "
        . "AND IS_USED_LOCK(%s) = CONNECTION_ID()",
        wp_json_encode(array_values($reserved), JSON_UNESCAPED_SLASHES),
        wp_json_encode($attachments, JSON_UNESCAPED_SLASHES),
        $next_version,
        (int) $state['state_version'],
        (int) $GLOBALS['frpd_mg_lock_connection_id'],
        FRPD_MG_LOCK_NAME
    ));
    if (1 !== (int) $updated) {
        return new WP_Error('frpd_mg_state_drift', 'The media guard state changed or could not be updated.');
    }
    $fresh = frpd_mg_active_state();
    if (!is_array($fresh) || $fresh['state_version'] !== $next_version
        || $fresh['reserved'] !== array_values($reserved) || $fresh['attachments'] !== $attachments) {
        return new WP_Error('frpd_mg_state_verify', 'The media guard state update could not be verified.');
    }
    return $fresh;
}

/** Reserve and allow one exact fixed upload in the guard-owning request. */
function frpd_mg_upload_prefilter($file) {
    $locked = frpd_mg_hold_request_lock();
    if (is_wp_error($locked)) {
        $file['error'] = $locked->get_error_message();
        return $file;
    }
    $state = frpd_mg_active_state();
    if (is_wp_error($state)) {
        $file['error'] = $state->get_error_message();
        return $file;
    }
    if (!is_array($state)) {
        return $file;
    }
    if (!frpd_mg_owner_matches($state)) {
        $file['error'] = 'FRP Depot media guard blocks this upload.';
        return $file;
    }
    $expected = frpd_mg_expected_images($state['family']);
    $raw_filename = isset($file['name']) ? (string) $file['name'] : '';
    $filename = basename($raw_filename);
    $match = null;
    foreach ($expected as $image) {
        if (hash_equals($image['filename'], $filename)) {
            $match = $image;
            break;
        }
    }
    $tmp = isset($file['tmp_name']) ? (string) $file['tmp_name'] : '';
    $size = is_file($tmp) ? filesize($tmp) : false;
    $digest = is_file($tmp) ? hash_file('sha256', $tmp) : false;
    $signature = is_file($tmp) ? file_get_contents($tmp, false, null, 0, 8) : false;
    $image_info = is_file($tmp) ? @getimagesize($tmp) : false;
    $mime = is_array($image_info) ? (string) ($image_info['mime'] ?? '') : '';
    $image_type = is_array($image_info) ? (int) ($image_info[2] ?? 0) : 0;
    if (($file['error'] ?? null) !== UPLOAD_ERR_OK
        || $raw_filename !== $filename || sanitize_file_name($raw_filename) !== $raw_filename
        || !is_array($match) || false === $size || (int) $size !== $match['bytes']
        || !is_string($digest) || !hash_equals($match['sha256'], $digest)
        || "\x89PNG\r\n\x1a\n" !== $signature || IMAGETYPE_PNG !== $image_type
        || 'image/png' !== $mime
        || in_array($filename, $state['reserved'], true)) {
        $file['error'] = 'FRP Depot media guard refused a non-fixed or repeated upload.';
        return $file;
    }
    $reserved = $state['reserved'];
    $reserved[] = $filename;
    $fresh = frpd_mg_update_state($state, $reserved, $state['attachments']);
    if (is_wp_error($fresh)) {
        $file['error'] = 'FRP Depot media guard could not reserve the fixed upload.';
        return $file;
    }
    $GLOBALS['frpd_mg_allowed_upload_request'] = true;
    $GLOBALS['frpd_mg_allowed_upload_filename'] = $filename;
    $GLOBALS['frpd_mg_allowed_attachment_id'] = 0;
    $GLOBALS['frpd_mg_attachment_insert_consumed'] = false;
    $GLOBALS['frpd_mg_allowed_tmp_path'] = $tmp;
    $GLOBALS['frpd_mg_allowed_destination'] = '';
    $GLOBALS['frpd_mg_destination_verified'] = false;
    return $file;
}
add_filter('wp_handle_upload_prefilter', 'frpd_mg_upload_prefilter', PHP_INT_MIN);
add_filter('wp_handle_sideload_prefilter', 'frpd_mg_upload_prefilter', PHP_INT_MIN);

function frpd_mg_pre_move_uploaded_file($move_new_file, $file, $new_file, $type) {
    $locked = frpd_mg_hold_request_lock();
    $state = is_wp_error($locked) ? $locked : frpd_mg_active_state();
    if (is_wp_error($state)) {
        return $state;
    }
    if (!is_array($state)) {
        return $move_new_file;
    }
    $expected = (string) ($GLOBALS['frpd_mg_allowed_upload_filename'] ?? '');
    $tmp = (string) ($GLOBALS['frpd_mg_allowed_tmp_path'] ?? '');
    $uploads = wp_upload_dir(null, false);
    $base = is_array($uploads) ? realpath((string) ($uploads['basedir'] ?? '')) : false;
    $parent = realpath(dirname((string) $new_file));
    if (empty($GLOBALS['frpd_mg_allowed_upload_request']) || '' === $expected
        || !hash_equals($expected, basename((string) $new_file))
        || !hash_equals($tmp, (string) ($file['tmp_name'] ?? ''))
        || 'image/png' !== (string) $type || false === $base || false === $parent
        || ($parent !== $base && !str_starts_with($parent, $base . DIRECTORY_SEPARATOR))) {
        return new WP_Error('frpd_mg_move', 'FRP Depot media guard refused the upload destination.');
    }
    $GLOBALS['frpd_mg_allowed_destination'] = (string) $new_file;
    return null;
}
add_filter('pre_move_uploaded_file', 'frpd_mg_pre_move_uploaded_file', PHP_INT_MIN, 4);

function frpd_mg_post_move_upload($upload, $context) {
    if (empty($GLOBALS['frpd_mg_allowed_upload_request'])) {
        return $upload;
    }
    $path = is_array($upload) ? (string) ($upload['file'] ?? '') : '';
    $expected = (string) ($GLOBALS['frpd_mg_allowed_upload_filename'] ?? '');
    $destination = (string) ($GLOBALS['frpd_mg_allowed_destination'] ?? '');
    $state = frpd_mg_active_state();
    $fixed = is_array($state) ? frpd_mg_expected_images($state['family'])
        : new WP_Error('frpd_mg_state', 'The media guard state is unavailable after upload.');
    $match = null;
    if (is_array($fixed)) {
        foreach ($fixed as $image) {
            if (hash_equals($image['filename'], $expected)) { $match = $image; break; }
        }
    }
    $size = is_file($path) ? filesize($path) : false;
    $digest = is_file($path) ? hash_file('sha256', $path) : false;
    if (!is_array($match) || '' === $path || !hash_equals($destination, $path)
        || !hash_equals($expected, basename($path)) || false === $size
        || (int) $size !== $match['bytes'] || !is_string($digest)
        || !hash_equals($match['sha256'], $digest)) {
        return array('error' => 'FRP Depot media guard could not verify the moved upload.');
    }
    $GLOBALS['frpd_mg_destination_verified'] = true;
    return $upload;
}
add_filter('wp_handle_upload', 'frpd_mg_post_move_upload', PHP_INT_MAX, 2);

function frpd_mg_guard_blocks_attachment_mutation() {
    $locked = frpd_mg_hold_request_lock();
    if (is_wp_error($locked)) {
        return true;
    }
    $state = frpd_mg_active_state();
    return is_wp_error($state) || is_array($state);
}

function frpd_mg_insert_post_data($data, $postarr, $unsanitized, $update) {
    $existing_id = (int) ($postarr['ID'] ?? 0);
    $existing_attachment = $existing_id > 0 && 'attachment' === get_post_type($existing_id);
    if ('attachment' !== ($data['post_type'] ?? '') && !$existing_attachment) {
        return $data;
    }
    $locked = frpd_mg_hold_request_lock();
    if (is_wp_error($locked)) {
        wp_die('FRP Depot media guard could not verify the attachment lock.', 'Media guard', array('response' => 423));
    }
    $state = frpd_mg_active_state();
    if (is_wp_error($state)) {
        wp_die('FRP Depot media guard state is indeterminate.', 'Media guard', array('response' => 423));
    }
    if (!is_array($state)) {
        return $data;
    }
    if ($update && $existing_attachment && 'attachment' !== ($data['post_type'] ?? '')) {
        wp_die('FRP Depot media guard blocks attachment post-type conversion.', 'Media guard', array('response' => 423));
    }
    $allowed = !empty($GLOBALS['frpd_mg_allowed_upload_request']);
    if (!$allowed) {
        wp_die('FRP Depot media guard blocks attachment creation or editing.', 'Media guard', array('response' => 423));
    }
    if (!$update) {
        $guid_path = parse_url((string) ($data['guid'] ?? ''), PHP_URL_PATH);
        $guid_filename = is_string($guid_path) ? basename($guid_path) : '';
        if (empty($GLOBALS['frpd_mg_destination_verified'])
            || !empty($GLOBALS['frpd_mg_attachment_insert_consumed'])
            || 'image/png' !== ($data['post_mime_type'] ?? '')
            || !hash_equals((string) ($GLOBALS['frpd_mg_allowed_upload_filename'] ?? ''), $guid_filename)) {
            wp_die('FRP Depot media guard blocks a second or non-fixed attachment insertion.', 'Media guard', array('response' => 423));
        }
        $GLOBALS['frpd_mg_attachment_insert_consumed'] = true;
        return $data;
    }
    $post_id = (int) ($postarr['ID'] ?? 0);
    if ($post_id <= 0 || $post_id !== (int) ($GLOBALS['frpd_mg_allowed_attachment_id'] ?? 0)) {
        wp_die('FRP Depot media guard blocks attachment substitution.', 'Media guard', array('response' => 423));
    }
    return $data;
}
add_filter('wp_insert_post_data', 'frpd_mg_insert_post_data', PHP_INT_MIN, 4);

function frpd_mg_capture_allowed_attachment($attachment_id) {
    if (!empty($GLOBALS['frpd_mg_allowed_upload_request'])
        && !empty($GLOBALS['frpd_mg_attachment_insert_consumed'])
        && empty($GLOBALS['frpd_mg_allowed_attachment_id'])) {
        $attachment_id = (int) $attachment_id;
        $filename = (string) ($GLOBALS['frpd_mg_allowed_upload_filename'] ?? '');
        $state = frpd_mg_active_state();
        if ($attachment_id <= 0 || '' === $filename || !is_array($state)
            || !frpd_mg_owner_matches($state) || !in_array($filename, $state['reserved'], true)
            || isset($state['attachments'][$filename])) {
            wp_die('FRP Depot media guard could not bind the fixed attachment identity.', 'Media guard', array('response' => 423));
        }
        $attachments = $state['attachments'];
        $attachments[$filename] = $attachment_id;
        $fresh = frpd_mg_update_state($state, $state['reserved'], $attachments);
        if (is_wp_error($fresh)) {
            wp_die('FRP Depot media guard could not persist the fixed attachment identity.', 'Media guard', array('response' => 423));
        }
        $GLOBALS['frpd_mg_allowed_attachment_id'] = $attachment_id;
    }
}
add_action('add_attachment', 'frpd_mg_capture_allowed_attachment', PHP_INT_MIN, 1);

function frpd_mg_pre_delete_attachment($delete, $post, $force_delete) {
    if (frpd_mg_guard_blocks_attachment_mutation()) {
        return false;
    }
    return $delete;
}
add_filter('pre_delete_attachment', 'frpd_mg_pre_delete_attachment', PHP_INT_MIN, 3);

function frpd_mg_rest_pre_insert_attachment($prepared_post, $request) {
    $locked = frpd_mg_hold_request_lock();
    $state = is_wp_error($locked) ? $locked : frpd_mg_active_state();
    if (is_wp_error($state) || is_array($state)) {
        return new WP_Error('frpd_mg_guarded', 'FRP Depot media guard blocks attachment mutation.', array('status' => 423));
    }
    return $prepared_post;
}
add_filter('rest_pre_insert_attachment', 'frpd_mg_rest_pre_insert_attachment', PHP_INT_MIN, 2);

function frpd_mg_update_attachment_metadata($data, $attachment_id) {
    $locked = frpd_mg_hold_request_lock();
    if (is_wp_error($locked)) {
        $existing = get_post_meta((int) $attachment_id, '_wp_attachment_metadata', true);
        return is_array($existing) ? $existing : array();
    }
    $state = frpd_mg_active_state();
    if (is_wp_error($state)) {
        $existing = get_post_meta((int) $attachment_id, '_wp_attachment_metadata', true);
        return is_array($existing) ? $existing : array();
    }
    if (!is_array($state)) {
        return $data;
    }
    if (!empty($GLOBALS['frpd_mg_allowed_upload_request'])
        && (int) $attachment_id === (int) ($GLOBALS['frpd_mg_allowed_attachment_id'] ?? 0)) {
        return $data;
    }
    $existing = get_post_meta((int) $attachment_id, '_wp_attachment_metadata', true);
    return is_array($existing) ? $existing : array();
}
add_filter('wp_update_attachment_metadata', 'frpd_mg_update_attachment_metadata', PHP_INT_MIN, 2);

function frpd_mg_save_image_file($override, $filename, $image, $mime_type, $post_id) {
    $locked = frpd_mg_hold_request_lock();
    if (is_wp_error($locked)) {
        return false;
    }
    $state = frpd_mg_active_state();
    if (is_wp_error($state) || (is_array($state)
        && (empty($GLOBALS['frpd_mg_allowed_upload_request'])
            || (int) $post_id !== (int) ($GLOBALS['frpd_mg_allowed_attachment_id'] ?? 0)))) {
        return false;
    }
    return $override;
}
add_filter('wp_save_image_file', 'frpd_mg_save_image_file', PHP_INT_MIN, 5);

/** Refuse the core image editor before restore-original can delete or replace files. */
function frpd_mg_ajax_image_editor_gate() {
    $locked = frpd_mg_hold_request_lock();
    $state = is_wp_error($locked) ? $locked : frpd_mg_active_state();
    if (is_wp_error($state) || is_array($state)) {
        wp_die('FRP Depot media guard blocks image-editor mutation.', 'Media mutation blocked', array('response' => 423));
    }
}
add_action('wp_ajax_image-editor', 'frpd_mg_ajax_image_editor_gate', PHP_INT_MIN);

function frpd_mg_update_attached_file($file, $attachment_id) {
    $locked = frpd_mg_hold_request_lock();
    if (is_wp_error($locked)) {
        $existing = get_post_meta((int) $attachment_id, '_wp_attached_file', true);
        return is_string($existing) ? $existing : '';
    }
    $state = frpd_mg_active_state();
    if (is_wp_error($state)) {
        $existing = get_post_meta((int) $attachment_id, '_wp_attached_file', true);
        return is_string($existing) ? $existing : '';
    }
    if (is_array($state) && !empty($GLOBALS['frpd_mg_allowed_upload_request'])
        && !empty($GLOBALS['frpd_mg_attachment_insert_consumed'])
        && empty($GLOBALS['frpd_mg_allowed_attachment_id'])) {
        $expected = (string) ($GLOBALS['frpd_mg_allowed_destination'] ?? '');
        $actual_real = realpath((string) $file);
        $expected_real = realpath($expected);
        if (false === $actual_real || false === $expected_real
            || !hash_equals(wp_normalize_path($expected_real), wp_normalize_path($actual_real))) {
            return '';
        }
        frpd_mg_capture_allowed_attachment((int) $attachment_id);
        $state = frpd_mg_active_state();
    }
    if (!is_array($state)
        || (!empty($GLOBALS['frpd_mg_allowed_upload_request'])
            && (int) $attachment_id === (int) ($GLOBALS['frpd_mg_allowed_attachment_id'] ?? 0))) {
        return $file;
    }
    $existing = get_post_meta((int) $attachment_id, '_wp_attached_file', true);
    return is_string($existing) ? $existing : '';
}
add_filter('update_attached_file', 'frpd_mg_update_attached_file', PHP_INT_MIN, 2);

function frpd_mg_metadata_filter($check, $object_id, $meta_key, $meta_value = null, $prev_value = null) {
    if ('attachment' !== get_post_type((int) $object_id)) {
        return $check;
    }
    $locked = frpd_mg_hold_request_lock();
    if (is_wp_error($locked)) {
        return false;
    }
    $state = frpd_mg_active_state();
    if (is_wp_error($state)
        || (is_array($state)
        && (empty($GLOBALS['frpd_mg_allowed_upload_request'])
            || (int) $object_id !== (int) ($GLOBALS['frpd_mg_allowed_attachment_id'] ?? 0)))) {
        return false;
    }
    return $check;
}
add_filter('add_post_metadata', 'frpd_mg_metadata_filter', PHP_INT_MIN, 5);
add_filter('update_post_metadata', 'frpd_mg_metadata_filter', PHP_INT_MIN, 5);
add_filter('delete_post_metadata', 'frpd_mg_metadata_filter', PHP_INT_MIN, 5);

function frpd_mg_upload_bits_guard($upload) {
    if (!is_array($upload)) {
        return 'FRP Depot media guard received an invalid alternate-upload payload.';
    }
    $locked = frpd_mg_hold_request_lock();
    $state = is_wp_error($locked) ? $locked : frpd_mg_active_state();
    if (is_wp_error($state) || is_array($state)) {
        return 'FRP Depot media guard blocks alternate attachment uploads.';
    }
    return $upload;
}
add_filter('wp_upload_bits', 'frpd_mg_upload_bits_guard', PHP_INT_MIN, 1);

function frpd_mg_admin_menu() {
    add_management_page(
        'FRP Depot Media Guard',
        'FRP Depot Media Guard',
        'upload_files',
        'frpd-media-mutation-guard',
        'frpd_mg_admin_page'
    );
}
add_action('admin_menu', 'frpd_mg_admin_menu');

function frpd_mg_admin_page() {
    frpd_mg_require_admin();
    $manifest = frpd_mg_manifest();
    if (is_wp_error($manifest)) {
        echo '<div class="notice notice-error"><p>' . esc_html($manifest->get_error_message()) . '</p></div>';
        return;
    }
    $locked = frpd_mg_hold_request_lock();
    $state = is_wp_error($locked) ? $locked : frpd_mg_active_state();
    echo '<div class="wrap"><h1>FRP Depot Media Mutation Guard</h1>';
    echo '<p id="frpd-mg-version">Version ' . esc_html(FRPD_MG_VERSION) . '</p>';
    echo '<p id="frpd-mg-status">' . (is_wp_error($state) ? 'Guard unavailable'
        : (is_array($state) ? 'Guard active' : 'Guard inactive')) . '</p>';
    foreach ($manifest['families'] as $family => $record) {
        echo '<section data-frpd-family="' . esc_attr($family) . '"><h2>' . esc_html($family) . '</h2>';
        echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
        wp_nonce_field('frpd_mg_snapshot');
        echo '<input type="hidden" name="action" value="frpd_media_guard_snapshot">';
        echo '<input type="hidden" name="family" value="' . esc_attr($family) . '">';
        echo '<button type="submit" data-frpd-action="snapshot" data-frpd-family="' . esc_attr($family) . '">Atomic snapshot</button></form>';
        echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
        wp_nonce_field('frpd_mg_acquire');
        echo '<input type="hidden" name="action" value="frpd_media_guard_acquire">';
        echo '<input type="hidden" name="family" value="' . esc_attr($family) . '">';
        echo '<button type="submit" data-frpd-action="acquire" data-frpd-family="' . esc_attr($family) . '">Acquire guarded commit</button></form></section>';
    }
    if (is_array($state)) {
        echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
        wp_nonce_field('frpd_mg_guarded_snapshot');
        echo '<input type="hidden" name="action" value="frpd_media_guard_guarded_snapshot">';
        echo '<button type="submit" id="frpd-mg-guarded-snapshot">Guarded snapshot</button></form>';
        echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
        wp_nonce_field('frpd_mg_complete');
        echo '<input type="hidden" name="action" value="frpd_media_guard_complete">';
        echo '<button type="submit" id="frpd-mg-complete">Verify four attachments and complete guard</button></form>';
    }
    echo '</div>';
}
