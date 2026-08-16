<?php
// Local offline harness for FRP Depot Media Mutation Guard. No WordPress/network writes.
define('ABSPATH', __DIR__ . DIRECTORY_SEPARATOR);
define('ARRAY_A', 'ARRAY_A');

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
function add_filter(...$args) {}
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
function get_post_meta($id, $key, $single = false) { return $GLOBALS['post_meta'][(int) $id][$key] ?? ($single ? '' : array()); }
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
    public function prepare($query, ...$args) { $this->prepared_args = $args; return $query; }
    public function get_var($query) {
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
            $row['is_active'] = (($row['state_status'] ?? 'active') === 'active'
                && (int) ($row['expires_epoch'] ?? 0) > time()) ? '1' : '0';
            $row['db_connection_id'] = $this->connection_id;
            $row['lock_owner_id'] = $this->lock_owner_id;
            return $row;
        }
        return null;
    }
    public function query($query) {
        if (str_contains($query, "SET state_status = 'completed'")) {
            if (!is_array($this->state) || ($this->state['state_status'] ?? '') !== 'active'
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
        if (str_contains($query, "SET state_status = 'expired'") && is_array($this->state)
            && (int) $this->state['expires_epoch'] <= time()) {
            $this->state['state_status'] = 'expired';
            $this->state['state_version']++;
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
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active', 'ambiguous product metadata cannot complete the guard');
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
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active', 'stale product proof cannot transition guard terminal');
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
