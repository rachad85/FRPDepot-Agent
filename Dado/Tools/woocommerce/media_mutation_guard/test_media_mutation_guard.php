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
    return $GLOBALS['post_meta'][(int) $id][(string) $key] ?? ($single ? '' : array());
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
        if (str_contains($query, "SET state_status = 'completed'")) {
            if (!is_array($this->state) || ($this->state['state_status'] ?? '') !== 'gallery'
                || (int) $this->state['expires_epoch'] <= time()
                || $this->connection_id !== 1 || $this->lock_owner_id !== 1
                || (int) ($GLOBALS['thumbnail_ids'][1368] ?? 0) !== 201
                || (string) ($GLOBALS['post_meta'][1368]['_product_image_gallery'] ?? '') !== '202,203,204'
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
        'schema_version' => 1,
        'family' => $state['family'],
        'product_id' => $manifest['families'][$state['family']]['product_id'],
        'manifest_sha256' => FRPD_MG_MANIFEST_SHA256,
        'snapshot_sha256' => str_repeat('a', 64),
        'snapshot_count' => 1,
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
    $root = 'C:\\FRPDepot\\Dado\\20_Working\\product_image_overhaul_20260815\\generated_review_batches\\';
    $dirs = array(
        'stub_flange' => 'stub_flange_real_source_batch_20260815',
        'open_manway' => 'manway_real_source_batch_20260815',
        'manway_cover' => 'manway_cover_real_source_batch_20260815',
        'elbow_90' => 'elbow_90_family_batch_20260815',
        'pipe' => 'pipe_real_source_batch_20260815',
    );
    return $root . $dirs[$family] . '\\' . $filename;
}

$manifest = frpd_mg_manifest();
check(!is_wp_error($manifest), 'runtime manifest loads');
check(array_keys($manifest['families']) === array('elbow_90', 'manway_cover', 'open_manway', 'pipe', 'stub_flange'), 'manifest contains exactly five canonical keys');
foreach ($manifest['families'] as $family => $record) {
    check(count($record['images']) === 4, $family . ' has four fixed images');
}
check(!isset($manifest['families']['fnpt']), 'FNPT is unreachable');
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

$target = fixed_source('stub_flange', 1);
check(is_file($target), 'fixed approved upload exists');
$GLOBALS['attachment_ids'] = array(11, 12);
$GLOBALS['attachment_paths'][12] = $target;
reset_lock();
frpd_mg_hold_request_lock();
$conflict = frpd_mg_snapshot('stub_flange', 'test', false);
check($conflict['complete'] === true, 'target-conflict snapshot complete');
check(count($conflict['name_conflicts']) === 1, 'target filename conflict detected');
check(count($conflict['hash_conflicts']) === 1, 'target hash conflict detected');
check(count($conflict['fixed_matches']) === 1, 'exact filename and hash match bound to one attachment');

$secret = 'fixed-test-owner-secret';
$state = array(
    'schema' => 1,
    'family' => 'stub_flange',
    'owner_hash' => hash('sha256', $secret),
    'created' => time(),
    'expires' => time() + 600,
    'reserved' => array(),
);
set_test_state($state);
reset_lock();
try {
    frpd_mg_ajax_image_editor_gate();
    check(false, 'active guard must block core restore-original before filesystem effects');
} catch (RuntimeException $exception) {
    check(str_contains($exception->getMessage(), 'image-editor mutation'),
        'active guard blocks core restore-original at the AJAX dispatch boundary');
}
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
check(json_decode($GLOBALS['wpdb']->state['reserved_json'], true) === array(basename($target)), 'exact upload reserved durably');
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
check(count(json_decode($GLOBALS['wpdb']->state['attachments_json'], true)) === 1,
    'later add_attachment notification is idempotent for the same ID');
check(frpd_mg_metadata_filter(null, 77, '_wp_attachment_metadata') === null, 'new attachment metadata allowed');
check(frpd_mg_metadata_filter(null, 12, '_wp_attachment_metadata') === false, 'other attachment metadata blocked in owner request');
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
$complete_state['attachments'] = array_combine($fixed_names, array(201, 202, 203, 204));
set_test_state($complete_state);
$_COOKIE[FRPD_MG_COOKIE] = $secret;
$GLOBALS['attachment_ids'] = array(11, 201, 202, 203, 204);
$GLOBALS['attachment_paths'] = array(11 => $benign);
for ($position = 1; $position <= 4; $position++) {
    $GLOBALS['attachment_paths'][200 + $position] = fixed_source('stub_flange', $position);
}
$GLOBALS['thumbnail_ids'][1368] = 11;
$GLOBALS['post_meta'][1368]['_product_image_gallery'] = '';
$target_payload = array('images' => array(
   array('id' => 201), array('id' => 202), array('id' => 203), array('id' => 204),
));
$product = new FakeProduct(1368);
$etag = frpd_mg_gallery_etag(array(11));
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
check(frpd_mg_update_post_metadata_by_mid(null, 501, 201) === false,
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
reset_lock();
$wrong_precondition = frpd_mg_rest_pre_insert_product_object(
   $product, new FakeRestRequest('PUT', $target_payload, '"' . str_repeat('0', 64) . '"'), false
);
check(is_wp_error($wrong_precondition), 'stale gallery If-Match is refused before a claim');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active', 'stale precondition cannot claim gallery mutation');
reset_lock();
$wrong_payload = array('images' => $target_payload['images']);
$wrong_payload['images'][3] = array('id' => 999);
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
check(frpd_mg_product_gallery_metadata_filter(null, 1368, '_thumbnail_id', 201) === null,
   'claimed request may write the exact primary attachment');
check(frpd_mg_product_gallery_metadata_filter(null, 1368, '_product_image_gallery', '202,203,204') === null,
    'claimed request may write the exact gallery tail');
check(frpd_mg_update_post_metadata_by_mid(null, 501, 201) === null,
    'claimed request may use the exact thumbnail metadata-by-ID value');
check(frpd_mg_update_post_metadata_by_mid(null, 502, '202,203,204') === null,
    'claimed request may use the exact gallery metadata-by-ID value');
check(frpd_mg_product_gallery_metadata_filter(null, 1368, '_product_image_gallery', '202,203,999') === false,
   'claimed request cannot substitute one gallery attachment');
check(frpd_mg_product_gallery_metadata_filter(null, 1368, '_PRODUCT_IMAGE_GALLERY', '202,203,999') === false,
   'claimed request cannot substitute through a case-insensitive gallery alias');
check(frpd_mg_delete_product_gallery_metadata(null, 1368, '_thumbnail_id') === false,
   'guarded product gallery metadata cannot be deleted');

$GLOBALS['thumbnail_ids'][1368] = 201;
$GLOBALS['post_meta'][1368]['_product_image_gallery'] = '202,203,999';
reset_lock();
frpd_mg_hold_request_lock();
check(is_wp_error(frpd_mg_completion_proof(frpd_mg_active_state())), 'completion refuses gallery drift');
$GLOBALS['post_meta'][1368]['_product_image_gallery'] = '202,203,204';
$GLOBALS['product_meta_counts'][1368] = array('_thumbnail_id' => 2, '_product_image_gallery' => 1);
reset_lock();
frpd_mg_hold_request_lock();
$duplicate_meta_completion = frpd_mg_complete_state(frpd_mg_active_state());
check(is_wp_error($duplicate_meta_completion), 'completion refuses a conflicting duplicate thumbnail metadata row');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'gallery', 'ambiguous product metadata cannot complete the guard');
$GLOBALS['product_meta_counts'][1368] = array('_thumbnail_id' => 1, '_product_image_gallery' => 1);
reset_lock();
frpd_mg_hold_request_lock();
$completion_proof = frpd_mg_completion_proof(frpd_mg_active_state());
check(!is_wp_error($completion_proof), 'completion proof accepts four exact attachments and exact product gallery');
check($completion_proof['attachment_ids'] === array(201, 202, 203, 204), 'completion proof preserves exact gallery order');
$active_before_completion = frpd_mg_active_state();
$db_before_completion = $GLOBALS['wpdb']->state;
$GLOBALS['post_meta'][1368]['_product_image_gallery'] = '202,203,999';
$stale_product_completion = frpd_mg_complete_state($active_before_completion);
check(is_wp_error($stale_product_completion), 'atomic completion refuses product identity changed after cached proof');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'gallery', 'stale product proof cannot transition guard terminal');
$GLOBALS['post_meta'][1368]['_product_image_gallery'] = '202,203,204';
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
