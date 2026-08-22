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
    // `_wp_attached_file` has ONE harness source of truth: $GLOBALS['attached_files'].
    // Guard 1.0.7 reads it through get_post_meta() as well as through the BINARY owner
    // query, and the two must never disagree inside a test.
    if ('_wp_attached_file' === $key
        && !isset($GLOBALS['post_meta'][$id][$key])
        && !isset($GLOBALS['post_meta_rows'][$id][$key])
        && isset($GLOBALS['attached_files'][$id])) {
        $value = (string) $GLOBALS['attached_files'][$id];
        return $single ? $value : array($value);
    }
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
    public bool $concurrent_change_before_replacement = false;
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
    public bool $fail_attached_file_owner_lookup = false;
    /** Force the alias probe to report a collation-equal but byte-different row. */
    public array $collation_alias_owners = array();
    public function get_col($query) {
        // 1.0.7 asks two DIFFERENT questions about `_wp_attached_file` ownership:
        // a BINARY-exact owner list, and a collation-alias probe that must stay
        // empty. Matching only the old non-binary literal made the fake answer
        // both with the whole attachment table.
        $is_alias_probe = str_contains($query, 'BINARY meta_key <> BINARY');
        $is_owner_query = str_contains($query, '_wp_attached_file')
            || in_array('_wp_attached_file', $this->prepared_args, true);
        if ($is_owner_query) {
            if ($this->fail_attached_file_owner_lookup) {
                $this->last_error = 'simulated attached-file ownership failure';
                return array();
            }
            $wanted = '';
            foreach ($this->prepared_args as $argument) {
                if (is_string($argument) && '_wp_attached_file' !== $argument) {
                    $wanted = $argument;
                    break;
                }
            }
            if ($is_alias_probe) {
                return array_values(array_map('intval',
                    $this->collation_alias_owners[$wanted] ?? array()));
            }
            $owners = array();
            foreach (($GLOBALS['attached_files'] ?? array()) as $id => $relative) {
                if ((string) $relative === (string) $wanted) { $owners[] = (int) $id; }
            }
            sort($owners, SORT_NUMERIC);
            return $owners;
        }
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
            // The real statement is a conditional UPDATE keyed on the CURRENT product's
            // exact `_thumbnail_id` / `_product_image_gallery` row identities. The fake
            // used to hard-code Stub Flange's product 1368, so no other family could ever
            // reach a terminal `completed` row here.
            $product_id = (int) ($this->prepared_args[4] ?? 0);
            $wanted_thumbnail = (string) ($this->prepared_args[7] ?? '');
            $wanted_gallery = (string) ($this->prepared_args[10] ?? '');
            if (!is_array($this->state) || ($this->state['state_status'] ?? '') !== 'gallery'
                || (int) $this->state['expires_epoch'] <= time()
                || $this->connection_id !== 1 || $this->lock_owner_id !== 1
                || $product_id <= 0
                || !in_array($product_id, (array) $GLOBALS['product_ids'], true)
                || (string) ($GLOBALS['thumbnail_ids'][$product_id] ?? '') !== $wanted_thumbnail
                || (string) ($GLOBALS['post_meta'][$product_id]['_thumbnail_id'] ?? '') !== $wanted_thumbnail
                || (string) ($GLOBALS['post_meta'][$product_id]['_product_image_gallery'] ?? '') !== $wanted_gallery
                || (int) ($GLOBALS['product_meta_counts'][$product_id]['_thumbnail_id'] ?? 1) !== 1
                || (int) ($GLOBALS['product_meta_counts'][$product_id]['_product_image_gallery'] ?? 1) !== 1) {
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
        // The 1.0.7 replacement of a TERMINAL row is a compare-and-swap keyed on the
        // exact status, version and both durable JSON blobs it inspected. Without this
        // branch the fake returned 1 unconditionally and no concurrent-change test
        // could ever fail.
        if (str_contains($query, "UPDATE `wp_frpd_media_guard` SET schema_version=")) {
            $args = $this->prepared_args;
            // Model a concurrent writer that lands BETWEEN the row inspection and the
            // compare-and-swap -- the only window the CAS exists to close.
            if (!empty($this->concurrent_change_before_replacement) && is_array($this->state)) {
                $this->concurrent_change_before_replacement = false;
                $this->state['state_version'] = (int) $this->state['state_version'] + 1;
            }
            if (!is_array($this->state)
                || $this->connection_id !== 1 || $this->lock_owner_id !== 1
                || (string) ($this->state['state_status'] ?? '') !== (string) ($args[13] ?? '')
                || (int) ($this->state['state_version'] ?? 0) !== (int) ($args[14] ?? -1)
                || (string) ($this->state['reserved_json'] ?? '') !== (string) ($args[15] ?? '')
                || (string) ($this->state['attachments_json'] ?? '') !== (string) ($args[16] ?? '')) {
                return 0;
            }
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
$GLOBALS['attached_files'] = array();
$GLOBALS['post_meta_rows'] = array();
$GLOBALS['product_meta_counts'] = array();
$GLOBALS['product_ids'] = array(1368);
$GLOBALS['thumbnail_ids'] = array();
// Canonical, never the 8.3 short form: 1.0.7 proves the configured uploads root
// equals its own realpath, and a short temp path would read as an alias.
function guard_test_temp_root() {
    $real = realpath(sys_get_temp_dir());
    return rtrim(false === $real ? sys_get_temp_dir() : $real, DIRECTORY_SEPARATOR);
}
$GLOBALS['upload_basedir'] = guard_test_temp_root() . DIRECTORY_SEPARATOR . 'frpd-mg-uploads';
if (!is_dir($GLOBALS['upload_basedir'])) { mkdir($GLOBALS['upload_basedir'], 0700, true); }

function check_admin_referer($action, $query_arg = '_wpnonce') {
    $GLOBALS['checked_nonces'][] = (string) $action;
    if (!empty($GLOBALS['force_bad_nonce'])) {
        throw new RuntimeException('bad nonce');
    }
    return 1;
}

class WP_REST_Request {
    private string $method;
    private string $route;
    private array $headers = array();
    private string $body = '';
    public function __construct($method = '', $route = '') {
        $this->method = (string) $method;
        $this->route = (string) $route;
    }
    public function get_method() { return $this->method; }
    public function get_route() { return $this->route; }
    public function set_header($name, $value) { $this->headers[strtolower((string) $name)] = (string) $value; }
    public function get_header($name) { return $this->headers[strtolower((string) $name)] ?? ''; }
    public function set_body($body) { $this->body = (string) $body; }
    public function get_body() { return $this->body; }
    public function get_json_params() { return json_decode($this->body, true); }
    public function get_query_params() { return array(); }
    public function get_body_params() { return array(); }
    public function get_file_params() { return array(); }
}

class FakeRestResponse {
    private int $status;
    private $data;
    public function __construct($status, $data) { $this->status = (int) $status; $this->data = $data; }
    public function get_status() { return $this->status; }
    public function get_data() { return $this->data; }
    public function is_error() { return $this->status >= 400; }
}

/**
 * Model WooCommerce's product controller closely enough to exercise the REAL guard
 * filter: it runs woocommerce_rest_pre_insert_product_object exactly as WooCommerce
 * does, then writes the featured/gallery metadata through the guard's own filters.
 */
function rest_do_request($request) {
    $GLOBALS['rest_dispatched'][] = array(
        'method' => $request->get_method(),
        'route' => $request->get_route(),
        'json' => $request->get_json_params(),
        'if_match' => $request->get_header('if-match'),
    );
    preg_match('#/([0-9]+)\z#', (string) $request->get_route(), $route_match);
    $product_id = (int) ($route_match[1] ?? 0);
    $object = new FakeProduct($product_id);
    $filtered = frpd_mg_rest_pre_insert_product_object($object, $request, false);
    if (is_wp_error($filtered)) {
        return new FakeRestResponse(423, array('code' => 'refused'));
    }
    // The guard filter above has ALREADY claimed active -> gallery. A downstream
    // WooCommerce failure from here on therefore leaves the row in `gallery`, which
    // is exactly the post-claim case the failure renderer must report honestly.
    if (!empty($GLOBALS['force_rest_failure_after_claim'])) {
        return new FakeRestResponse(500, array('code' => 'woocommerce_rest_update_failed'));
    }
    if (!empty($GLOBALS['force_rest_exception_after_claim'])) {
        throw new RuntimeException('downstream WooCommerce exception after claim');
    }
    $json = $request->get_json_params();
    $ids = array();
    foreach (($json['images'] ?? array()) as $image) { $ids[] = (int) $image['id']; }
    $thumbnail = frpd_mg_product_gallery_metadata_filter(
        null, $product_id, '_thumbnail_id', (string) $ids[0]);
    $gallery_value = implode(',', array_slice($ids, 1));
    $gallery = frpd_mg_product_gallery_metadata_filter(
        null, $product_id, '_product_image_gallery', $gallery_value);
    if (false === $thumbnail || false === $gallery) {
        return new FakeRestResponse(423, array('code' => 'blocked'));
    }
    $GLOBALS['thumbnail_ids'][$product_id] = $ids[0];
    $GLOBALS['post_meta'][$product_id]['_thumbnail_id'] = (string) $ids[0];
    $GLOBALS['post_meta'][$product_id]['_product_image_gallery'] = $gallery_value;
    return new FakeRestResponse(200, array('id' => $product_id, 'images' => $json['images']));
}

$GLOBALS['checked_nonces'] = array();
$GLOBALS['rest_dispatched'] = array();

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
/** Build the exact schema-3 durable record a family-contract guard would hold. */
function test_record($family, $reserved) {
    $expected = frpd_mg_expected_images($family);
    $filenames = array_column($expected, 'filename');
    $reuse = frpd_mg_reuse_contract($family);
    if (is_array($reuse)) {
        $initial = array($reuse['approved_filename'] => $reuse['attachment_id']);
        $missing = $reuse['upload_positions'];
    } else {
        $initial = array();
        $missing = range(1, count($filenames));
    }
    $missing_names = array();
    foreach ($missing as $position) { $missing_names[] = $filenames[$position - 1]; }
    $uploads = array();
    foreach ($missing_names as $name) {
        if (in_array($name, (array) $reserved, true)) { $uploads[] = $name; }
    }
    return array(
        'contract' => FRPD_MG_FAMILY_CONTRACT,
        'initial_attachments' => $initial,
        'missing_positions' => $missing,
        'reserved' => $uploads,
    );
}

/** Build the exact schema-3 durable record for the one Open Manway recovery contract. */
function test_recovery_record($bound, $reserved = array()) {
    $expected = frpd_mg_expected_images(FRPD_MG_RECOVERY_FAMILY);
    $filenames = array_column($expected, 'filename');
    ksort($bound, SORT_NUMERIC);
    $initial = array();
    foreach ($bound as $position => $attachment_id) {
        $initial[$filenames[$position - 1]] = (int) $attachment_id;
    }
    $missing = array();
    foreach (range(1, count($filenames)) as $position) {
        if (!isset($bound[$position])) { $missing[] = $position; }
    }
    return array(
        'contract' => FRPD_MG_RECOVERY_CONTRACT,
        'initial_attachments' => $initial,
        'missing_positions' => $missing,
        'reserved' => array_values($reserved),
    );
}

function with_record($state) {
    if (!isset($state['record'])) {
        $state['record'] = test_record($state['family'], $state['reserved'] ?? array());
    }
    $state['reserved'] = $state['record']['reserved'];
    return $state;
}

function set_test_state($state) {
    $state = with_record($state);
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
        'reserved_json' => json_encode($state['record']),
        'attachments_json' => json_encode($state['attachments'] ?? array()),
    );
}
/**
 * Register the live post record an attachment must have for 1.0.7 identity proof.
 *
 * Guard 1.0.7 proves post type, post status and MIME server-side, so a test that
 * leaves `attachment_posts` empty can only ever exercise the absent-post branch.
 */
function set_attachment_post($attachment_id, $overrides = array()) {
    $GLOBALS['attachment_posts'][(int) $attachment_id] = (object) array_merge(array(
        'ID' => (int) $attachment_id,
        'post_type' => 'attachment',
        'post_status' => 'inherit',
        'post_mime_type' => 'image/png',
    ), $overrides);
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
