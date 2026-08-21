<?php
// Local offline harness for FRP Depot Media Mutation Guard. No WordPress/network writes.
define('ABSPATH', __DIR__ . DIRECTORY_SEPARATOR);
define('ARRAY_A', 'ARRAY_A');
define('WP_PLUGIN_DIR', dirname(__DIR__) . DIRECTORY_SEPARATOR . 'hetron_private_history'
    . DIRECTORY_SEPARATOR . 'plugin');

class WP_Error {
    private string $code;
    private string $message;
    public function __construct($code = '', $message = '', $data = null) {
        $this->code = (string) $code;
        $this->message = (string) $message;
    }
    public function get_error_message() { return $this->message; }
}
function is_wp_error($value) { return $value instanceof WP_Error; }
function plugin_dir_path($file) { return dirname($file) . DIRECTORY_SEPARATOR; }
function add_action($hook, $callback, $priority = 10, $accepted_args = 1) {
    $GLOBALS['registered_actions'][(string) $hook][] = array($callback, $priority, $accepted_args);
}
function add_filter($hook, $callback, $priority = 10, $accepted_args = 1) {
    $GLOBALS['registered_filters'][(string) $hook][] = array($callback, $priority, $accepted_args);
}
function register_activation_hook($file, $callback) {}
function register_deactivation_hook($file, $callback) {}
function wp_get_session_token() { return 'offline-session-token'; }
function get_current_user_id() { return 1; }
function get_option($name, $default = false) { return $GLOBALS['options'][$name] ?? $default; }
function add_option($name, $value, $deprecated = '', $autoload = null) {
    if (array_key_exists($name, $GLOBALS['options'])) { return false; }
    $GLOBALS['options'][$name] = $value; return true;
}
function update_option($name, $value, $autoload = null) {
    $changed = !array_key_exists($name, $GLOBALS['options']) || $GLOBALS['options'][$name] !== $value;
    $GLOBALS['options'][$name] = $value; return $changed;
}
function delete_option($name) {
    if (!array_key_exists($name, $GLOBALS['options'])) { return false; }
    unset($GLOBALS['options'][$name]); return true;
}
function get_attached_file($id, $unfiltered = false) { return $GLOBALS['attachment_paths'][(int) $id] ?? false; }
function get_post_meta($id, $key, $single = false) {
    $id = (int) $id;
    $key = (string) $key;
    if (!$single && isset($GLOBALS['post_meta_rows'][$id])
        && array_key_exists($key, $GLOBALS['post_meta_rows'][$id])) {
        return $GLOBALS['post_meta_rows'][$id][$key];
    }
    if (!isset($GLOBALS['post_meta'][$id])
        || !array_key_exists($key, $GLOBALS['post_meta'][$id])) {
        return $single ? '' : array();
    }
    $value = $GLOBALS['post_meta'][$id][$key];
    return $single ? $value : array($value);
}
function get_metadata_by_mid($type, $meta_id) {
    return 'post' === $type ? ($GLOBALS['metadata_by_mid'][(int) $meta_id] ?? false) : false;
}
function get_post($id) { return $GLOBALS['attachment_posts'][(int) $id] ?? null; }
function get_post_status($post) { return is_object($post) ? ($post->post_status ?? '') : ''; }
function get_post_mime_type($post) { return is_object($post) ? ($post->post_mime_type ?? '') : ''; }
function is_plugin_active($file) { return in_array((string) $file, $GLOBALS['active_plugins'], true); }
function get_post_type($id) {
    if (in_array((int) $id, $GLOBALS['attachment_ids'], true)) { return 'attachment'; }
    return in_array((int) $id, $GLOBALS['product_ids'], true) ? 'product' : 'post';
}
function get_post_thumbnail_id($id) { return $GLOBALS['thumbnail_ids'][(int) $id] ?? 0; }
function wp_json_encode($value, $flags = 0) { return json_encode($value, $flags); }
function is_user_logged_in() { return true; }
function current_user_can($capability) { return true; }
function admin_url($path = '') { return 'https://frpdepots.com/wp-admin/' . ltrim($path, '/'); }
function esc_html($value) { return htmlspecialchars((string) $value, ENT_QUOTES); }
function esc_url($value) { return (string) $value; }
function esc_attr($value) { return htmlspecialchars((string) $value, ENT_QUOTES); }
function sanitize_key($value) { return preg_replace('/[^a-z0-9_\-]/', '', strtolower((string) $value)); }
function sanitize_file_name($value) { return (string) $value; }
function wp_normalize_path($path) { return str_replace('\\\\', '/', (string) $path); }
function wp_upload_dir($time = null, $create = true) { return array('basedir' => $GLOBALS['upload_basedir']); }
function wp_unslash($value) { return $value; }
function wp_nonce_field($action) {}
function status_header($status) {}
function nocache_headers() {}
function wp_die($message, $title = '', $args = array()) { throw new RuntimeException((string) $message); }

class FakeWpdb {
    public string $posts = 'wp_posts';
    public string $postmeta = 'wp_postmeta';
    public string $prefix = 'wp_';
    public string $last_error = '';
    public int $connection_id = 1;
    public int $lock_owner_id = 1;
    public ?array $state = null;
    public int $rows_affected = 0;
    public array $prepared_args = array();
    public bool $reconnect_on_state_read = false;
    public bool $fail_attachment_enumeration = false;
    public bool $fail_meta_collation = false;
    public function prepare($query, ...$args) { $this->prepared_args = $args; return $query; }
    public function get_var($query) {
        if (str_contains($query, 'SELECT CASE') && str_contains($query, "'_thumbnail_id'")) {
            $key = (string) ($this->prepared_args[0] ?? '');
            $folded = rtrim(strtolower(strtr($key, array('í' => 'i', 'Í' => 'i'))));
            if ('_thumbnail_id' === $folded) { return '_thumbnail_id'; }
            if ('_product_image_gallery' === $folded) { return '_product_image_gallery'; }
            return '';
        }
        if (str_contains($query, 'CONNECTION_ID')) { return $this->connection_id; }
        if (str_contains($query, 'IS_USED_LOCK')) { return $this->lock_owner_id; }
        return 1;
    }
    public function get_col($query) {
        if ($this->fail_attachment_enumeration) {
            $this->last_error = 'simulated attachment enumeration failure';
            return array();
        }
        return $GLOBALS['attachment_ids'];
    }
    public function get_results($query, $format = null) {
        if (str_contains($query, 'SELECT meta_key, meta_value FROM')) {
            $post_id = (int) ($this->prepared_args[0] ?? 0);
            $rows = array();
            foreach (($GLOBALS['post_meta_rows'][$post_id] ?? array()) as $key => $values) {
                foreach ($values as $value) {
                    $rows[] = array('meta_key' => (string) $key, 'meta_value' => (string) $value);
                }
            }
            return $rows;
        }
        return array();
    }
    public function get_row($query, $format = null) {
        if (str_contains($query, 'information_schema.COLUMNS')) {
            if ($this->fail_meta_collation) {
                $this->last_error = 'simulated metadata collation lookup failure';
                return null;
            }
            return array('charset_name' => 'utf8mb4', 'collation_name' => 'utf8mb4_unicode_ci');
        }
        if (str_contains($query, 'SHOW TABLE STATUS')) { return array('Engine' => 'InnoDB'); }
        if (str_contains($query, 'UTC_TIMESTAMP(6) AS issued_utc')) {
            return array(
                'issued_utc' => gmdate('Y-m-d H:i:s.000000'),
                'expires_utc' => gmdate('Y-m-d H:i:s.000000', time() + 1800),
                'expires_epoch' => time() + 1800,
                'db_connection_id' => $this->connection_id,
                'lock_owner_id' => $this->lock_owner_id,
            );
        }
        if (str_contains($query, 'UTC_TIMESTAMP(6) AS completed_utc')) {
            return array('completed_utc' => gmdate('Y-m-d H:i:s.000000'));
        }
        if (str_contains($query, 'wp_frpd_media_guard')) {
            if ($this->reconnect_on_state_read) {
                $this->connection_id = 2;
                $this->reconnect_on_state_read = false;
            }
            $row = is_array($this->state) ? $this->state : array('guard_id' => null);
            $row['is_active'] = (in_array(($row['state_status'] ?? 'active'), array('active', 'gallery'), true)
                && (int) ($row['expires_epoch'] ?? 0) > time()) ? '1' : '0';
            $row['db_connection_id'] = $this->connection_id;
            $row['lock_owner_id'] = $this->lock_owner_id;
            return $row;
        }
        return null;
    }
    public function query($query) {
        if (str_contains($query, 'INSERT INTO `wp_frpd_media_guard`')) {
            if ($this->connection_id !== 1 || $this->lock_owner_id !== 1) { return 0; }
            $args = $this->prepared_args;
            $this->state = array(
                'guard_id' => 1,
                'schema_version' => (int) ($args[0] ?? 0),
                'family' => (string) ($args[1] ?? ''),
                'product_id' => (int) ($args[2] ?? 0),
                'manifest_sha256' => (string) ($args[3] ?? ''),
                'snapshot_sha256' => (string) ($args[4] ?? ''),
                'snapshot_count' => (int) ($args[5] ?? 0),
                'owner_user_id' => (int) ($args[6] ?? 0),
                'owner_session_sha256' => (string) ($args[7] ?? ''),
                'owner_token_sha256' => (string) ($args[8] ?? ''),
                'issued_utc' => (string) ($args[9] ?? ''),
                'expires_utc' => (string) ($args[10] ?? ''),
                'expires_epoch' => time() + 1800,
                'state_status' => 'active',
                'completed_utc' => null,
                'state_version' => 1,
                'reserved_json' => (string) ($args[11] ?? '[]'),
                'attachments_json' => (string) ($args[12] ?? '{}'),
            );
            return 1;
        }
        if (str_contains($query, "SET state_status = 'completed'")) {
            if (!is_array($this->state) || ($this->state['state_status'] ?? '') !== 'gallery'
                || (int) $this->state['expires_epoch'] <= time()
                || $this->connection_id !== 1 || $this->lock_owner_id !== 1
                || (int) ($GLOBALS['thumbnail_ids'][1368] ?? 0) !== 4849
                || (string) ($GLOBALS['post_meta'][1368]['_product_image_gallery'] ?? '') !== '202,203,204,205,206'
                || ((int) ($GLOBALS['product_meta_counts'][1368]['_thumbnail_id'] ?? 1) !== 1
                    && str_contains($query, "meta_key = '_thumbnail_id')"))
                || ((int) ($GLOBALS['product_meta_counts'][1368]['_product_image_gallery'] ?? 1) !== 1
                    && str_contains($query, "meta_key = '_product_image_gallery')"))) {
                return 0;
            }
            $this->state['state_status'] = 'completed';
            $this->state['completed_utc'] = gmdate('Y-m-d H:i:s.000000');
            $this->state['state_version']++;
            return 1;
        }
        if (str_contains($query, "SET state_status = 'gallery'")) {
            if (!is_array($this->state) || ($this->state['state_status'] ?? '') !== 'active'
                || (int) $this->state['state_version'] !== (int) ($this->prepared_args[1] ?? 0)
                || (int) $this->state['expires_epoch'] <= time()
                || $this->connection_id !== 1 || $this->lock_owner_id !== 1) {
                return 0;
            }
            $this->state['state_status'] = 'gallery';
            $this->state['state_version'] = (int) ($this->prepared_args[0] ?? 0);
            return 1;
        }
        if (str_contains($query, 'SET reserved_json = %s')) {
            if (!is_array($this->state) || ($this->state['state_status'] ?? '') !== 'active'
                || (int) $this->state['expires_epoch'] <= time()
                || $this->connection_id !== 1 || $this->lock_owner_id !== 1) {
                return 0;
            }
            $this->state['reserved_json'] = (string) ($this->prepared_args[0] ?? '[]');
            $this->state['attachments_json'] = (string) ($this->prepared_args[1] ?? '{}');
            $this->state['state_version'] = (int) ($this->prepared_args[2] ?? $this->state['state_version']);
            return 1;
        }
        if (str_contains($query, "SET state_status = 'expired'")) {
            $allows_gallery = str_contains($query, "state_status IN ('active','gallery')");
            $eligible = is_array($this->state)
                && (($this->state['state_status'] ?? '') === 'active'
                    || ($allows_gallery && ($this->state['state_status'] ?? '') === 'gallery'))
                && (int) $this->state['expires_epoch'] <= time();
            if (!$eligible) { return 0; }
            $this->state['state_status'] = 'expired';
            $this->state['state_version']++;
            return 1;
        }
        return 1;
    }
    public function insert($table, $data) {
        if (is_array($this->state)) { return false; }
        $data['expires_epoch'] = time() + 1800;
        $this->state = $data;
        return 1;
    }
    public function update($table, $data, $where) {
        if (!is_array($this->state)
            || (isset($where['state_version'])
                && (int) $this->state['state_version'] !== (int) $where['state_version'])
            || (isset($where['state_status'])
                && (string) ($this->state['state_status'] ?? '') !== (string) $where['state_status'])) {
            return 0;
        }
        $this->state = array_merge($this->state, $data);
        return 1;
    }
}
$GLOBALS['wpdb'] = new FakeWpdb();
$GLOBALS['options'] = array();
$GLOBALS['attachment_ids'] = array();
$GLOBALS['attachment_paths'] = array();
$GLOBALS['attachment_posts'] = array();
$GLOBALS['active_plugins'] = array('frpdepot-hetron-private-history/frpdepot-hetron-private-history.php');
$GLOBALS['post_meta'] = array();
$GLOBALS['post_meta_rows'] = array();
$GLOBALS['product_meta_counts'] = array();
$GLOBALS['product_ids'] = array(1368);
$GLOBALS['thumbnail_ids'] = array();
$GLOBALS['upload_basedir'] = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'frpd-mg-uploads';
if (!is_dir($GLOBALS['upload_basedir'])) { mkdir($GLOBALS['upload_basedir'], 0700, true); }

require __DIR__ . '/frpdepot-media-mutation-guard/frpdepot-media-mutation-guard.php';

$passed = 0;
function check($condition, $message) {
    global $passed;
    if (!$condition) { throw new RuntimeException('FAIL: ' . $message); }
    $passed++;
}
function reset_lock() {
    $GLOBALS['frpd_mg_lock_held'] = false;
    $GLOBALS['frpd_mg_lock_connection_id'] = 0;
    $GLOBALS['wpdb']->connection_id = 1;
    $GLOBALS['wpdb']->lock_owner_id = 1;
    $GLOBALS['frpd_mg_allowed_upload_request'] = false;
    $GLOBALS['frpd_mg_allowed_upload_filename'] = '';
    $GLOBALS['frpd_mg_allowed_attachment_id'] = 0;
    $GLOBALS['frpd_mg_attachment_insert_consumed'] = false;
    $GLOBALS['frpd_mg_allowed_gallery_request'] = false;
    $GLOBALS['frpd_mg_allowed_gallery_product_id'] = 0;
    $GLOBALS['frpd_mg_allowed_gallery_ids'] = array();
    $GLOBALS['frpd_mg_allowed_tmp_path'] = '';
    $GLOBALS['frpd_mg_allowed_destination'] = '';
    $GLOBALS['frpd_mg_destination_verified'] = false;
}

class FakeProduct {
    private int $id;
    public function __construct($id) { $this->id = (int) $id; }
    public function get_id() { return $this->id; }
}
class FakeRestRequest {
    private string $method;
    private array $json;
    private string $if_match;
    private array $query;
    private array $body;
    private array $files;
    public function __construct($method, $json, $if_match, $query = array(), $body = array(), $files = array()) {
        $this->method = (string) $method;
        $this->json = $json;
        $this->if_match = (string) $if_match;
        $this->query = is_array($query) ? $query : array();
        $this->body = is_array($body) ? $body : array();
        $this->files = is_array($files) ? $files : array();
    }
    public function get_method() { return $this->method; }
    public function get_json_params() { return $this->json; }
    public function get_query_params() { return $this->query; }
    public function get_body_params() { return $this->body; }
    public function get_file_params() { return $this->files; }
    public function get_header($name) {
        return 'if-match' === strtolower((string) $name) ? $this->if_match : '';
    }
}
function set_test_state($state) {
    $manifest = frpd_mg_manifest();
    $GLOBALS['wpdb']->state = array(
        'guard_id' => 1,
        'schema_version' => FRPD_MG_STATE_SCHEMA,
        'family' => $state['family'],
        'product_id' => $manifest['families'][$state['family']]['product_id'],
        'manifest_sha256' => FRPD_MG_MANIFEST_SHA256,
        'snapshot_sha256' => str_repeat('a', 64),
        'snapshot_count' => $state['snapshot_count'] ?? 1,
        'owner_user_id' => 1,
        'owner_session_sha256' => hash('sha256', 'offline-session-token'),
        'owner_token_sha256' => $state['owner_hash'],
        'issued_utc' => gmdate('Y-m-d H:i:s.000000'),
        'expires_utc' => gmdate('Y-m-d H:i:s.000000', $state['expires']),
        'expires_epoch' => $state['expires'],
        'state_status' => $state['state_status'] ?? 'active',
        'completed_utc' => null,
        'state_version' => $state['state_version'] ?? 1,
        'reserved_json' => json_encode($state['reserved'] ?? array()),
        'attachments_json' => json_encode($state['attachments'] ?? array()),
    );
}
function upload_file($name, $tmp) {
    return array('name' => $name, 'tmp_name' => $tmp, 'error' => UPLOAD_ERR_OK, 'type' => 'image/png');
}
function fixed_source($family, $position) {
    $manifest = frpd_mg_manifest();
    $filename = $manifest['families'][$family]['images'][$position - 1]['filename'];
    if ('stub_flange' === $family) {
        return 'C:\\FRPDepot\\Dado\\20_Working\\stub_flange_chatgpt_views_v2_20260820\\'
            . $filename;
    }
    if ('open_manway' === $family) {
        return 'C:\\FRPDepot\\Dado\\20_Working\\frp_manway\\approved_gallery_20260820\\'
            . $filename;
    }
    $root = 'C:\\FRPDepot\\Dado\\20_Working\\product_image_overhaul_20260815\\generated_review_batches\\';
    $dirs = array(
        'manway_cover' => 'manway_cover_real_source_batch_20260815',
        'elbow_90' => 'elbow_90_family_batch_20260815',
        'pipe' => 'pipe_real_source_batch_20260815',
    );
    return $root . $dirs[$family] . '\\' . $filename;
}

$manifest = frpd_mg_manifest();
check(!is_wp_error($manifest), 'runtime manifest loads');
check(array_keys($manifest['families']) === array('elbow_90', 'manway_cover', 'open_manway', 'pipe', 'stub_flange'), 'manifest contains exactly five canonical keys');
$expected_family_counts = array(
    'stub_flange' => 6,
    'open_manway' => 6,
    'manway_cover' => 4,
    'elbow_90' => 4,
    'pipe' => 4,
);
foreach ($expected_family_counts as $family => $count) {
    check(count($manifest['families'][$family]['images']) === $count,
        $family . ' has its exact fixed image count');
}
check(FRPD_MG_VERSION === '1.0.5', 'guard version is pinned to v1.0.5');
check($manifest['fixed_reuse'] === array(
    'actual_filename' => '01_stub_flange_real_source_hero-1.png',
    'approved_filename' => '01_authentic_source_hero.png',
    'attachment_id' => 4849,
    'bytes' => 895251,
    'family' => 'stub_flange',
    'must_be_current_product_hero' => true,
    'position' => 1,
    'product_id' => 1368,
    'required_current_raw_meta' => array(
        '_product_image_gallery' => '4850,4851,4852',
        '_thumbnail_id' => '4849',
    ),
    'sha256' => 'aa9c8da37cc4a1ee98b5f0b2c77dd5b369c327a583412938778210562936b3da',
    'upload_positions' => array(2, 3, 4, 5, 6),
), 'runtime manifest pins the sole exact Stub Flange reuse and positions 2-6 uploads');
check(array_column($manifest['families']['open_manway']['images'], 'sha256') === array(
    '472b5e5b0aba9a7201444524c559e6797c266a0de008d7bc70b4f8ef1938d0cd',
    '0fd7e2c62fb88d425cdfaf949415520ac89a5d95d07cf75b8e9791d308ea8181',
    '40ac3a69f5903d53f6fd71f952ac63ed237abc1a37a17c800a345c06211c8e63',
    'd740be620cf0c083e7e399127c2205dd6f7b9e73fb08fdee31e1b79568d75950',
    'c54c9fd74fbdc55d0b9295b1bb7fb1dd0146cee645f455025d1b1895f21a543a',
    'bfde2b6ab1f1de5cc6ad24b9aa556ef1ed46bd9cd43b34a2a5b1c77bb612e0e7',
), 'v1.0.5 preserves the exact six-image Open Manway v1.0.4 contract');
check(!isset($manifest['families']['fnpt']), 'FNPT is unreachable');
check(is_wp_error(frpd_mg_expected_images('other_family')),
    'guard cannot target an unapproved family');
$admin_post_actions = array_values(array_filter(array_keys($GLOBALS['registered_actions']),
    fn($hook) => str_starts_with($hook, 'admin_post_frpd_media_guard_')));
sort($admin_post_actions, SORT_STRING);
check($admin_post_actions === array(
    'admin_post_frpd_media_guard_acquire',
    'admin_post_frpd_media_guard_complete',
    'admin_post_frpd_media_guard_guarded_snapshot',
    'admin_post_frpd_media_guard_snapshot',
), 'guard exposes only the four exact fixed admin actions');
check(!isset($GLOBALS['registered_actions']['admin_post_frpd_media_guard_delete']),
    'guard exposes no delete action');
check(isset($GLOBALS['registered_actions']['wp_ajax_image-editor'])
    && $GLOBALS['registered_actions']['wp_ajax_image-editor'][0][0] === 'frpd_mg_ajax_image_editor_gate'
    && $GLOBALS['registered_actions']['wp_ajax_image-editor'][0][1] === PHP_INT_MIN,
    'core image editor is intercepted before restore-original dispatch');
check(isset($GLOBALS['registered_filters']['woocommerce_rest_pre_insert_product_object'])
    && $GLOBALS['registered_filters']['woocommerce_rest_pre_insert_product_object'][0]
        === array('frpd_mg_rest_pre_insert_product_object', PHP_INT_MIN, 3),
    'Woo product gallery claim is intercepted before product persistence');
check(isset($GLOBALS['registered_filters']['update_post_metadata_by_mid'])
    && $GLOBALS['registered_filters']['update_post_metadata_by_mid'][0]
        === array('frpd_mg_update_post_metadata_by_mid', PHP_INT_MIN, 4),
    'metadata-by-ID updates are intercepted under the guard lock');
check(isset($GLOBALS['registered_filters']['delete_post_metadata_by_mid'])
    && $GLOBALS['registered_filters']['delete_post_metadata_by_mid'][0]
        === array('frpd_mg_delete_post_metadata_by_mid', PHP_INT_MIN, 2),
    'metadata-by-ID deletes are intercepted under the guard lock');
check(!isset($GLOBALS['registered_filters']['pre_wp_unique_filename_file_list']),
    'WordPress core alternate-image filename collision scanning remains enabled');

$benign = tempnam(sys_get_temp_dir(), 'frpd-mg-benign-');
file_put_contents($benign, 'benign attachment bytes');
$GLOBALS['attachment_ids'] = array(11);
$GLOBALS['attachment_paths'] = array(11 => $benign);
reset_lock();
check(frpd_mg_hold_request_lock() === true, 'advisory lock acquired');
$GLOBALS['wpdb']->connection_id = 2;
check(is_wp_error(frpd_mg_hold_request_lock()), 'database reconnect loses lock ownership and refuses');
$GLOBALS['wpdb']->connection_id = 1;
$GLOBALS['wpdb']->reconnect_on_state_read = true;
check(is_wp_error(frpd_mg_active_state()), 'same-query state read detects advisory-lock reconnect');
reset_lock();
check(frpd_mg_hold_request_lock() === true, 'advisory lock reacquired after reconnect refusal');
$snapshot = frpd_mg_snapshot('stub_flange', 'test', false);
check(!is_wp_error($snapshot) && $snapshot['complete'] === true,
    'benign snapshot complete' . (is_wp_error($snapshot) ? ': ' . $snapshot->get_error_message() : ''));
check($snapshot['attachment_total'] === 1 && $snapshot['hashed_total'] === 1, 'snapshot counts exact');
check($snapshot['name_conflicts'] === array() && $snapshot['hash_conflicts'] === array(), 'benign snapshot has no conflict');
check($snapshot['schema'] === 2 && $snapshot['private_exceptions'] === array(),
    'ordinary complete snapshot uses schema 2 with no private exception');
reset_lock();
$GLOBALS['attachment_ids'] = array(11, 1832);
$GLOBALS['attachment_paths'] = array(11 => $benign, 1832 => false);
$GLOBALS['attachment_posts'][1832] = (object) array(
    'ID' => 1832,
    'post_type' => 'attachment',
    'post_status' => 'private',
    'post_mime_type' => 'application/pdf',
    'post_name' => 'hetron-cr-guide-2007_ineos',
    'post_date_gmt' => '2026-03-17 15:20:38',
);
$GLOBALS['post_meta'][1832]['_wp_attached_file'] = '2026/03/HETRON-CR-Guide-2007_Ineos.pdf';
check(frpd_mg_hold_request_lock() === true, 'advisory lock acquired for fixed private attachment');
$private_snapshot = frpd_mg_snapshot('stub_flange', 'test', false);
check(!is_wp_error($private_snapshot) && $private_snapshot['complete'] === true,
    'exact protected private attachment is accepted without reading its file');
check($private_snapshot['attachment_total'] === 2 && $private_snapshot['hashed_total'] === 1
    && count($private_snapshot['private_exceptions']) === 1
    && $private_snapshot['private_exceptions'][0]['attachment_id'] === 1832,
    'private attachment is counted once and bound into proof');
$GLOBALS['attachment_paths'][1832] = $benign;
reset_lock();
check(frpd_mg_hold_request_lock() === true, 'advisory lock reacquired for readable private attachment');
$readable_private = frpd_mg_snapshot('stub_flange', 'test', false);
check(!is_wp_error($readable_private) && $readable_private['complete'] === true
    && $readable_private['hashed_total'] === 1
    && count($readable_private['private_exceptions']) === 1,
    'fixed private contract is enforced and classified identically even if its file becomes readable');
reset_lock();
$GLOBALS['attachment_posts'][1832]->post_status = 'publish';
check(frpd_mg_hold_request_lock() === true, 'advisory lock reacquired for private identity drift');
$private_drift = frpd_mg_snapshot('stub_flange', 'test', false);
check(!is_wp_error($private_drift) && $private_drift['complete'] === false
    && $private_drift['failures'][0]['reason'] === 'private_attachment_proof_failed',
    'private attachment identity drift fails closed');
$GLOBALS['attachment_posts'][1832]->post_status = 'private';
reset_lock();
$GLOBALS['active_plugins'] = array();
check(frpd_mg_hold_request_lock() === true, 'advisory lock reacquired for missing private protector');
$private_unprotected = frpd_mg_snapshot('stub_flange', 'test', false);
check(!is_wp_error($private_unprotected) && $private_unprotected['complete'] === false
    && $private_unprotected['failures'][0]['reason'] === 'private_attachment_proof_failed',
    'inactive private protector fails closed');
$GLOBALS['active_plugins'] = array('frpdepot-hetron-private-history/frpdepot-hetron-private-history.php');
$GLOBALS['attachment_ids'] = array(11);
$GLOBALS['attachment_paths'] = array(11 => $benign);
reset_lock();
check(frpd_mg_hold_request_lock() === true, 'advisory lock acquired for enumeration-failure test');
$GLOBALS['wpdb']->fail_attachment_enumeration = true;
check(is_wp_error(frpd_mg_snapshot('stub_flange', 'test', false)),
    'attachment SQL error is not accepted as a complete empty snapshot');
$GLOBALS['wpdb']->fail_attachment_enumeration = false;

$position_one_source = fixed_source('stub_flange', 1);
check(is_file($position_one_source), 'fixed approved Stub Flange position 1 source exists');
$reuse_original = $GLOBALS['upload_basedir'] . DIRECTORY_SEPARATOR
    . '01_stub_flange_real_source_hero-1.png';
@unlink($reuse_original);
copy($position_one_source, $reuse_original);
$GLOBALS['attachment_ids'] = array(11, 4849);
$GLOBALS['attachment_paths'] = array(11 => $benign, 4849 => $reuse_original);
$GLOBALS['thumbnail_ids'][1368] = 4849;
$GLOBALS['post_meta'][1368]['_thumbnail_id'] = '4849';
$GLOBALS['post_meta'][1368]['_product_image_gallery'] = '4850,4851,4852';
$GLOBALS['post_meta_rows'][1368]['_thumbnail_id'] = array('4849');
$GLOBALS['post_meta_rows'][1368]['_product_image_gallery'] = array('4850,4851,4852');
reset_lock();
frpd_mg_hold_request_lock();
$reuse_snapshot = frpd_mg_snapshot('stub_flange', 'test', false);
$reuse_match = array(array('attachment_id' => 4849, 'fixed_position' => 1));
check($reuse_snapshot['complete'] === true, 'fixed Stub Flange reuse snapshot is complete');
check($reuse_snapshot['name_conflicts'] === array(), 'fixed reuse creates no filename conflict');
check($reuse_snapshot['hash_conflicts'] === $reuse_match,
    'acquisition sees exactly the one permitted reused-hero hash conflict');
check($reuse_snapshot['fixed_matches'] === $reuse_match,
    'fresh original-file rehash binds attachment 4849 to fixed position 1');
$reuse_bindings = frpd_mg_acquisition_bindings('stub_flange', $reuse_snapshot);
check($reuse_bindings === array(
    'reserved' => array('01_authentic_source_hero.png'),
    'attachments' => array('01_authentic_source_hero.png' => 4849),
), 'acquisition produces the sole durable reuse binding in the existing JSON schema');

$secret = 'fixed-test-owner-secret';
$persisted = frpd_mg_insert_state('stub_flange', $secret, $reuse_snapshot);
check(is_array($persisted)
    && $persisted['reserved'] === $reuse_bindings['reserved']
    && $persisted['attachments'] === $reuse_bindings['attachments'],
    'guard insertion persists the reused attachment binding without a table-schema change');
$GLOBALS['attachment_ids'][] = 12;
$GLOBALS['attachment_paths'][12] = $position_one_source;
reset_lock();
frpd_mg_hold_request_lock();
$extra_conflict = frpd_mg_snapshot('stub_flange', 'test', false);
check(count($extra_conflict['hash_conflicts']) === 2
    && is_wp_error(frpd_mg_acquisition_bindings('stub_flange', $extra_conflict)),
    'any second matching hash conflict refuses acquisition');
array_pop($GLOBALS['attachment_ids']);
unset($GLOBALS['attachment_paths'][12]);
$GLOBALS['post_meta_rows'][1368]['_product_image_gallery'] = array('4850,4851,9999');
check(is_wp_error(frpd_mg_acquisition_bindings('stub_flange', $reuse_snapshot)),
    'acquisition refuses raw baseline gallery drift even with the prior complete rehash');
$GLOBALS['post_meta_rows'][1368]['_product_image_gallery'] = array('4850,4851,4852', '4850,4851,4852');
check(is_wp_error(frpd_mg_acquisition_bindings('stub_flange', $reuse_snapshot)),
    'acquisition refuses duplicate raw baseline metadata rows');
$GLOBALS['post_meta_rows'][1368]['_product_image_gallery'] = array('4850,4851,4852');

$state = array(
    'schema' => FRPD_MG_STATE_SCHEMA,
    'family' => 'stub_flange',
    'owner_hash' => hash('sha256', $secret),
    'created' => time(),
    'expires' => time() + 600,
    'snapshot_count' => 5,
    'reserved' => $reuse_bindings['reserved'],
    'attachments' => $reuse_bindings['attachments'],
);
$stub_names = array_column(frpd_mg_expected_images('stub_flange'), 'filename');
$stub_excess_state = $state;
$stub_excess_state['reserved'] = $stub_names;
$stub_excess_state['attachments'] = array_combine($stub_names, array(4849, 302, 303, 304, 305, 306));
set_test_state($stub_excess_state);
$_COOKIE[FRPD_MG_COOKIE] = $secret;
reset_lock();
$seven_stub_payload = array('images' => array_map(
    fn($id) => array('id' => $id), array(4849, 302, 303, 304, 305, 306, 307)
));
check(is_wp_error(frpd_mg_rest_pre_insert_product_object(
    new FakeProduct(1368), new FakeRestRequest(
        'PUT', $seven_stub_payload, frpd_mg_gallery_etag(array(4849, 4850, 4851, 4852))
    ), false
)), 'a seventh Stub Flange gallery image is refused');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active',
    'a seventh Stub Flange image cannot claim gallery mutation');
$pipe_names = array_column(frpd_mg_expected_images('pipe'), 'filename');
$pipe_excess_state = $state;
$pipe_excess_state['family'] = 'pipe';
$pipe_excess_state['reserved'] = $pipe_names;
$pipe_excess_state['attachments'] = array_combine($pipe_names, array(401, 402, 403, 404));
set_test_state($pipe_excess_state);
reset_lock();
$five_pipe_payload = array('images' => array_map(
    fn($id) => array('id' => $id), array(401, 402, 403, 404, 405)
));
check(is_wp_error(frpd_mg_rest_pre_insert_product_object(
    new FakeProduct(1455), new FakeRestRequest('PUT', $five_pipe_payload, frpd_mg_gallery_etag(array(11))), false
)), 'a fifth non-Stub gallery image is refused');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active',
    'a fifth non-Stub image cannot claim gallery mutation');
set_test_state($state);
reset_lock();
try {
    frpd_mg_ajax_image_editor_gate();
    check(false, 'active guard must block core restore-original before filesystem effects');
} catch (RuntimeException $exception) {
    check(str_contains($exception->getMessage(), 'image-editor mutation'),
        'active guard blocks core restore-original at the AJAX dispatch boundary');
}
reset_lock();
$position_one_upload = frpd_mg_upload_prefilter(
    upload_file(basename($position_one_source), $position_one_source)
);
check(isset($position_one_upload['error'])
    && json_decode($GLOBALS['wpdb']->state['reserved_json'], true) === array($stub_names[0]),
    'Stub Flange position 1 upload is impossible and cannot create a reservation');
$target = fixed_source('stub_flange', 2);
check(is_file($target), 'fixed approved Stub Flange position 2 upload exists');
$_COOKIE[FRPD_MG_COOKIE] = 'wrong-owner';
reset_lock();
$wrong_owner = frpd_mg_upload_prefilter(upload_file(basename($target), $target));
check(isset($wrong_owner['error']), 'wrong owner upload blocked');

$_COOKIE[FRPD_MG_COOKIE] = $secret;
reset_lock();
$bad = tempnam(sys_get_temp_dir(), 'frpd-mg-bad-');
file_put_contents($bad, 'wrong bytes');
$wrong_bytes = frpd_mg_upload_prefilter(upload_file(basename($target), $bad));
check(isset($wrong_bytes['error']), 'wrong bytes upload blocked');

reset_lock();
$upload = upload_file(basename($target), $target);
$allowed = frpd_mg_upload_prefilter($upload);
check(($allowed['error'] ?? UPLOAD_ERR_OK) === UPLOAD_ERR_OK,
    'exact owner upload allowed: ' . (string) ($allowed['error'] ?? 'no error'));
check($GLOBALS['frpd_mg_allowed_upload_request'] === true, 'allowed request marked');
check(json_decode($GLOBALS['wpdb']->state['reserved_json'], true)
    === array($stub_names[0], basename($target)), 'only exact position 2 is reserved after the reuse binding');
$destination = $GLOBALS['upload_basedir'] . DIRECTORY_SEPARATOR . basename($target);
@unlink($destination);
check(frpd_mg_pre_move_uploaded_file(null, $upload, $destination, 'image/png') === null, 'exact destination allowed before move');
copy($target, $destination);
$moved = frpd_mg_post_move_upload(array('file' => $destination, 'type' => 'image/png'), 'upload');
check(!isset($moved['error']) && $GLOBALS['frpd_mg_destination_verified'] === true, 'moved bytes verified before insert');

$insert = array(
    'post_type' => 'attachment',
    'post_mime_type' => 'image/png',
    'guid' => 'https://frpdepots.com/wp-content/uploads/2026/08/' . basename($target),
);
check(frpd_mg_insert_post_data($insert, array(), array(), false) === $insert, 'one exact attachment insertion allowed');
try {
    frpd_mg_insert_post_data($insert, array(), array(), false);
    check(false, 'second attachment insertion must throw');
} catch (RuntimeException $exception) {
    check(true, 'second same-request attachment insertion blocked');
}
$GLOBALS['attachment_ids'][] = 77;
$bound_file = frpd_mg_update_attached_file($destination, 77);
check($bound_file === $destination, 'first core attached-file write binds the exact attachment before add_attachment');
check($GLOBALS['frpd_mg_allowed_attachment_id'] === 77, 'new attachment ID captured during core attached-file order');
frpd_mg_capture_allowed_attachment(77);
check(json_decode($GLOBALS['wpdb']->state['attachments_json'], true) === array(
    $stub_names[0] => 4849,
    basename($target) => 77,
), 'later add_attachment notification preserves reuse plus one new binding idempotently');
check(frpd_mg_metadata_filter(null, 77, '_wp_attachment_metadata') === null, 'new attachment metadata allowed');
check(frpd_mg_metadata_filter(null, 4849, '_wp_attachment_metadata') === false,
    'reused attachment metadata remains immutable in the owner upload request');
reset_lock();
$GLOBALS['wpdb']->lock_owner_id = 999;
check(frpd_mg_metadata_filter(null, 1368, '_price') === null, 'unrelated product metadata bypasses media mutex');
check($GLOBALS['wpdb']->lock_owner_id === 999, 'unrelated product metadata never acquires media mutex');
$GLOBALS['wpdb']->lock_owner_id = 1;
reset_lock();
try {
    frpd_mg_refuse_active_deactivation();
    check(false, 'active guard must refuse plugin deactivation');
} catch (RuntimeException $exception) {
    check(str_contains($exception->getMessage(), 'deactivation is refused'), 'active guard refuses deactivation under lock');
}
check(frpd_mg_guard_blocks_attachment_mutation() === true, 'delete piggyback blocked in owner upload request');
try {
    frpd_mg_insert_post_data(array('post_type' => 'post'), array('ID' => 77), array(), true);
    check(false, 'attachment post-type conversion must throw');
} catch (RuntimeException $exception) {
    check(true, 'attachment post-type conversion blocked in owner request');
}
check(is_string(frpd_mg_upload_bits_guard(array('name' => 'other.png', 'bits' => 'bytes', 'time' => null))),
    'alternate upload route blocked under guard with the real one-argument filter shape');

reset_lock();
$repeated = frpd_mg_upload_prefilter(upload_file(basename($target), $target));
check(isset($repeated['error']), 'repeated fixed upload blocked');

$_COOKIE[FRPD_MG_COOKIE] = 'wrong-owner';
reset_lock();
check(frpd_mg_guard_blocks_attachment_mutation() === true, 'attachment mutation blocked while guard active');
$GLOBALS['wpdb']->state['expires_epoch'] = time() - 1;
reset_lock();
check(frpd_mg_guard_blocks_attachment_mutation() === false, 'expired guard does not block media forever');

$complete_state = $state;
$fixed_names = array_column(frpd_mg_expected_images('stub_flange'), 'filename');
$complete_state['reserved'] = $fixed_names;
$complete_state['attachments'] = array_combine(
    $fixed_names, array(4849, 202, 203, 204, 205, 206)
);
check(frpd_mg_final_attachment_ids($complete_state) === array(4849, 202, 203, 204, 205, 206),
    'final bindings require one reused hero plus five unique ordered new IDs');
$old_id_binding = $complete_state;
$old_id_binding['attachments'][$fixed_names[1]] = 4850;
check(is_wp_error(frpd_mg_final_attachment_ids($old_id_binding)),
    'a prior baseline gallery ID cannot masquerade as one of the five new uploads');
$duplicate_binding = $complete_state;
$duplicate_binding['attachments'][$fixed_names[5]] = 205;
check(is_wp_error(frpd_mg_final_attachment_ids($duplicate_binding)),
    'final Stub Flange attachment IDs must be unique');
set_test_state($complete_state);
$_COOKIE[FRPD_MG_COOKIE] = $secret;
$GLOBALS['attachment_ids'] = array(11, 202, 203, 204, 205, 206, 4849, 4850, 4851, 4852);
$GLOBALS['attachment_paths'] = array(
    11 => $benign,
    4849 => $reuse_original,
    4850 => $benign,
    4851 => $benign,
    4852 => $benign,
);
for ($position = 2; $position <= 6; $position++) {
    $GLOBALS['attachment_paths'][200 + $position] = fixed_source('stub_flange', $position);
}
$GLOBALS['thumbnail_ids'][1368] = 4849;
$GLOBALS['post_meta'][1368]['_thumbnail_id'] = '4849';
$GLOBALS['post_meta'][1368]['_product_image_gallery'] = '4850,4851,4852';
$GLOBALS['post_meta_rows'][1368]['_thumbnail_id'] = array('4849');
$GLOBALS['post_meta_rows'][1368]['_product_image_gallery'] = array('4850,4851,4852');
$target_payload = array('images' => array(
   array('id' => 4849), array('id' => 202), array('id' => 203), array('id' => 204),
   array('id' => 205), array('id' => 206),
));
$product = new FakeProduct(1368);
$etag = frpd_mg_gallery_etag(array(4849, 4850, 4851, 4852));
$GLOBALS['metadata_by_mid'] = array(
    501 => (object) array('post_id' => 1368, 'meta_key' => '_thumbnail_id'),
    502 => (object) array('post_id' => 1368, 'meta_key' => '_product_image_gallery'),
    503 => (object) array('post_id' => 11, 'meta_key' => '_wp_attached_file'),
    504 => (object) array('post_id' => 1368, 'meta_key' => 'unrelated_key'),
    505 => (object) array('post_id' => 1368, 'meta_key' => '_PRODUCT_IMAGE_GALLERY'),
    506 => (object) array('post_id' => 1368, 'meta_key' => '_thumbnail_id '),
    507 => (object) array('post_id' => 1368, 'meta_key' => '_thumbnaíl_id'),
);
reset_lock();
check(frpd_mg_update_post_metadata_by_mid(null, 501, 4849) === false,
    'metadata-by-ID cannot change a guarded product thumbnail before the exact claim');
check(frpd_mg_delete_post_metadata_by_mid(null, 502) === false,
    'metadata-by-ID cannot delete guarded gallery metadata');
check(frpd_mg_update_post_metadata_by_mid(null, 503, 'changed.pdf') === false,
    'metadata-by-ID cannot alter attachment metadata while the guard is active');
check(frpd_mg_delete_post_metadata_by_mid(null, 503) === false,
    'metadata-by-ID cannot delete attachment metadata while the guard is active');
check(frpd_mg_update_post_metadata_by_mid(null, 504, 'unchanged-scope') === null,
    'unrelated product metadata remains outside the gallery guard');
check(frpd_mg_product_gallery_metadata_filter(null, 1368, '_THUMBNAIL_ID', 999) === false,
    'case-insensitive thumbnail aliases cannot bypass ordinary metadata protection');
check(frpd_mg_delete_product_gallery_metadata(null, 1368, '_PRODUCT_IMAGE_GALLERY') === false,
    'case-insensitive gallery aliases cannot bypass ordinary metadata deletion protection');
check(frpd_mg_update_post_metadata_by_mid(null, 504, '999', '_THUMBNAIL_ID') === false,
    'metadata-by-ID cannot substitute an unrelated key for a case-insensitive protected alias');
check(frpd_mg_delete_post_metadata_by_mid(null, 505) === false,
    'metadata-by-ID deletion blocks a case-insensitive protected key already in the database');
check(frpd_mg_gallery_meta_key_identity('_thumbnail_id ') === '_thumbnail_id',
    'live MySQL PAD SPACE collation classifies a trailing-space thumbnail alias');
check(frpd_mg_gallery_meta_key_identity('_thumbnaíl_id') === '_thumbnail_id',
    'live MySQL accent-insensitive collation classifies an accented thumbnail alias');
check(frpd_mg_product_gallery_metadata_filter(null, 1368, '_thumbnail_id ', 999) === false,
    'ordinary metadata cannot bypass protection through a trailing-space alias');
check(frpd_mg_product_gallery_metadata_filter(null, 1368, '_thumbnaíl_id', 999) === false,
    'ordinary metadata cannot bypass protection through an accent-insensitive alias');
check(frpd_mg_delete_product_gallery_metadata(null, 1368, '_product_image_gallery ') === false,
    'ordinary deletion cannot bypass protection through a PAD SPACE alias');
check(frpd_mg_update_post_metadata_by_mid(null, 504, '999', '_thumbnail_id ') === false,
    'metadata-by-ID substitution cannot use a trailing-space protected alias');
check(frpd_mg_update_post_metadata_by_mid(null, 504, '999', '_thumbnaíl_id') === false,
    'metadata-by-ID substitution cannot use an accent-insensitive protected alias');
check(frpd_mg_delete_post_metadata_by_mid(null, 506) === false
    && frpd_mg_delete_post_metadata_by_mid(null, 507) === false,
    'metadata-by-ID deletion blocks stored PAD SPACE and accented aliases');
$GLOBALS['wpdb']->fail_meta_collation = true;
check(frpd_mg_product_gallery_metadata_filter(null, 1368, 'unrelated_key', 'x') === false,
    'collation lookup failure blocks target-product metadata rather than guessing');
$GLOBALS['wpdb']->fail_meta_collation = false;
reset_lock();
$query_mutation = frpd_mg_rest_pre_insert_product_object(
    $product, new FakeRestRequest('PUT', $target_payload, $etag, array('regular_price' => '0.01')), false
);
check(is_wp_error($query_mutation), 'images-only claim refuses every write query parameter');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active', 'query parameters cannot claim gallery mutation');
reset_lock();
$body_mutation = frpd_mg_rest_pre_insert_product_object(
    $product, new FakeRestRequest(
        'PUT', $target_payload, $etag, array(), array('regular_price' => '0.01')
    ), false
);
check(is_wp_error($body_mutation), 'images-only claim refuses form/body parameters beside JSON');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active', 'body parameters cannot claim gallery mutation');
reset_lock();
$file_mutation = frpd_mg_rest_pre_insert_product_object(
    $product, new FakeRestRequest(
        'PUT', $target_payload, $etag, array(), array(), array('unexpected' => array('name' => 'x'))
    ), false
);
check(is_wp_error($file_mutation), 'images-only claim refuses file parameters');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active', 'file parameters cannot claim gallery mutation');
$_COOKIE[FRPD_MG_COOKIE] = 'wrong-owner-token';
reset_lock();
check(is_wp_error(frpd_mg_rest_pre_insert_product_object(
    $product, new FakeRestRequest('PUT', $target_payload, $etag), false
)), 'gallery claim requires the exact owner token');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active', 'wrong token cannot claim gallery mutation');
$_COOKIE[FRPD_MG_COOKIE] = $secret;
$owner_session = $GLOBALS['wpdb']->state['owner_session_sha256'];
$GLOBALS['wpdb']->state['owner_session_sha256'] = hash('sha256', 'other-session');
reset_lock();
check(is_wp_error(frpd_mg_rest_pre_insert_product_object(
    $product, new FakeRestRequest('PUT', $target_payload, $etag), false
)), 'gallery claim requires the exact owner session');
$GLOBALS['wpdb']->state['owner_session_sha256'] = $owner_session;
$GLOBALS['wpdb']->state['owner_user_id'] = 2;
reset_lock();
check(is_wp_error(frpd_mg_rest_pre_insert_product_object(
    $product, new FakeRestRequest('PUT', $target_payload, $etag), false
)), 'gallery claim requires the exact owner user');
$GLOBALS['wpdb']->state['owner_user_id'] = 1;
reset_lock();
$GLOBALS['post_meta'][1368]['_product_image_gallery'] = '4850,4851,9999';
$GLOBALS['post_meta_rows'][1368]['_product_image_gallery'] = array('4850,4851,9999');
$changed_etag = frpd_mg_gallery_etag(array(4849, 4850, 4851, 9999));
check(is_wp_error(frpd_mg_rest_pre_insert_product_object(
    $product, new FakeRestRequest('PUT', $target_payload, $changed_etag), false
)), 'matching If-Match cannot bypass exact raw pre-gallery baseline revalidation');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active',
    'raw baseline drift cannot claim gallery mutation');
$GLOBALS['post_meta'][1368]['_product_image_gallery'] = '4850,4851,4852';
$GLOBALS['post_meta_rows'][1368]['_product_image_gallery'] = array('4850,4851,4852');
reset_lock();
$wrong_precondition = frpd_mg_rest_pre_insert_product_object(
   $product, new FakeRestRequest('PUT', $target_payload, '"' . str_repeat('0', 64) . '"'), false
);
check(is_wp_error($wrong_precondition), 'stale gallery If-Match is refused before a claim');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active', 'stale precondition cannot claim gallery mutation');
reset_lock();
$wrong_payload = array('images' => $target_payload['images']);
$wrong_payload['images'][5] = array('id' => 999);
check(is_wp_error(frpd_mg_rest_pre_insert_product_object(
   $product, new FakeRestRequest('PUT', $wrong_payload, $etag), false
)), 'non-fixed gallery ID sequence is refused');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active', 'wrong gallery payload cannot claim mutation');
reset_lock();
$claimed = frpd_mg_rest_pre_insert_product_object(
   $product, new FakeRestRequest('PUT', $target_payload, $etag), false
);
check($claimed === $product, 'exact conditional images-only gallery request is claimed');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'gallery', 'gallery claim transitions active state atomically');
check(frpd_mg_product_gallery_metadata_filter(null, 1368, '_thumbnail_id', 4849) === null,
   'claimed request may write the exact primary attachment');
check(frpd_mg_product_gallery_metadata_filter(null, 1368, '_product_image_gallery', '202,203,204,205,206') === null,
    'claimed request may write the exact gallery tail');
check(frpd_mg_update_post_metadata_by_mid(null, 501, 4849) === null,
    'claimed request may use the exact thumbnail metadata-by-ID value');
check(frpd_mg_update_post_metadata_by_mid(null, 502, '202,203,204,205,206') === null,
    'claimed request may use the exact gallery metadata-by-ID value');
check(frpd_mg_product_gallery_metadata_filter(null, 1368, '_product_image_gallery', '202,203,204,205,999') === false,
   'claimed request cannot substitute one gallery attachment');
check(frpd_mg_product_gallery_metadata_filter(null, 1368, '_PRODUCT_IMAGE_GALLERY', '202,203,204,205,999') === false,
   'claimed request cannot substitute through a case-insensitive gallery alias');
check(frpd_mg_delete_product_gallery_metadata(null, 1368, '_thumbnail_id') === false,
   'guarded product gallery metadata cannot be deleted');

$GLOBALS['thumbnail_ids'][1368] = 4849;
$GLOBALS['post_meta'][1368]['_thumbnail_id'] = '4849';
$GLOBALS['post_meta'][1368]['_product_image_gallery'] = '202,203,204,205,999';
reset_lock();
frpd_mg_hold_request_lock();
check(is_wp_error(frpd_mg_completion_proof(frpd_mg_active_state())), 'completion refuses gallery drift');
$GLOBALS['post_meta'][1368]['_product_image_gallery'] = '202,203,204,205,206';
$GLOBALS['product_meta_counts'][1368] = array('_thumbnail_id' => 2, '_product_image_gallery' => 1);
reset_lock();
frpd_mg_hold_request_lock();
$duplicate_meta_completion = frpd_mg_complete_state(frpd_mg_active_state());
check(is_wp_error($duplicate_meta_completion), 'completion refuses a conflicting duplicate thumbnail metadata row');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'gallery', 'ambiguous product metadata cannot complete the guard');
$GLOBALS['product_meta_counts'][1368] = array('_thumbnail_id' => 1, '_product_image_gallery' => 1);
$original_position_two_path = $GLOBALS['attachment_paths'][202];
$GLOBALS['attachment_paths'][202] = $bad;
reset_lock();
frpd_mg_hold_request_lock();
check(is_wp_error(frpd_mg_completion_proof(frpd_mg_active_state())),
    'completion freshly rehashes every original and rejects changed upload bytes');
$GLOBALS['attachment_paths'][202] = $original_position_two_path;
reset_lock();
frpd_mg_hold_request_lock();
$completion_proof = frpd_mg_completion_proof(frpd_mg_active_state());
check(!is_wp_error($completion_proof),
    'completion proof accepts reused attachment 4849, five uploads, and exact post metadata');
check($completion_proof['attachment_ids'] === array(4849, 202, 203, 204, 205, 206),
    'completion proof preserves exact unique six-ID gallery order');
$active_before_completion = frpd_mg_active_state();
$db_before_completion = $GLOBALS['wpdb']->state;
$GLOBALS['post_meta'][1368]['_product_image_gallery'] = '202,203,204,205,999';
$stale_product_completion = frpd_mg_complete_state($active_before_completion);
check(is_wp_error($stale_product_completion), 'atomic completion refuses product identity changed after cached proof');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'gallery', 'stale product proof cannot transition guard terminal');
$GLOBALS['post_meta'][1368]['_product_image_gallery'] = '202,203,204,205,206';
$GLOBALS['wpdb']->state['expires_epoch'] = time() - 1;
$expired_completion = frpd_mg_complete_state($active_before_completion);
check(is_wp_error($expired_completion), 'completion after database expiry is refused atomically');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'expired', 'expired completion retains expired audit row');
$GLOBALS['wpdb']->state = $db_before_completion;
$GLOBALS['wpdb']->state['expires_epoch'] = time() + 1800;
$active_before_completion = frpd_mg_active_state();
check(frpd_mg_complete_state($active_before_completion) === true, 'completion persists terminal guard state');
check($GLOBALS['wpdb']->state['state_status'] === 'completed', 'guard row retained as completed, not deleted');
check(frpd_mg_active_state() === null, 'completed guard is no longer active');

unlink($benign);
unlink($bad);
echo "PASS {$passed}\n";
