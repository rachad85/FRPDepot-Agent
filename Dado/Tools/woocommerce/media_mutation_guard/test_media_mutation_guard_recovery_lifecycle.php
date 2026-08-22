<?php
/**
 * Guard 1.0.7 Open Manway recovery LIFECYCLE harness.
 *
 * This is the file that closes correction finding A.3: every proof the Python
 * recovery tool consumes is produced HERE, by the real plugin functions, driven
 * through a real acquisition -> guarded progress -> reserved uploads -> owner-bound
 * gallery commit -> completion proof -> terminal `completed` transition. The exact
 * shapes are then written to
 *   media_mutation_guard/testdata/guard_107_proof_contract.json
 * and the Python contract test validates THAT file with the production validators,
 * so a producer/consumer schema drift is an executable failure rather than a claim.
 *
 * Every real handler ends its request with exit(), exactly as WordPress does, so
 * each step runs in its own child process of this same file and the durable world
 * (guard row, media library, product metadata, owner cookie) is carried between
 * steps in one JSON file. Nothing here touches WordPress, a network or a website.
 *
 *   php test_media_mutation_guard_recovery_lifecycle.php              # driver
 *   php test_media_mutation_guard_recovery_lifecycle.php <step> <world> [arg]
 */

// ======================================================================
// Child-step mode
// ======================================================================
if ($argc > 1) {
    require __DIR__ . '/guard_test_bootstrap.php';
    lifecycle_load_world((string) $argv[2]);
    register_shutdown_function(static function () {
        lifecycle_save_world($GLOBALS['lifecycle_world_path']);
    });
    lifecycle_run_step((string) $argv[1], (string) ($argv[3] ?? ''));
    exit(0);
}

require __DIR__ . '/guard_test_bootstrap.php';

// ======================================================================
// Driver mode: run the whole lifecycle and publish the proof contract.
// ======================================================================
$world_path = guard_test_temp_root() . DIRECTORY_SEPARATOR . 'frpd-mg-lifecycle-world.json';
$base = guard_test_temp_root() . DIRECTORY_SEPARATOR . 'frpd-mg-lifecycle-uploads';
lifecycle_reset_tree($base);

$names = lifecycle_manway_names();
// Position 1 is the ONE verified prior upload; 2-6 were never proven to land, so
// the immutable missing list this lifecycle exercises is exactly [2,3,4,5,6].
$hero_relative = '2026/08/' . $names[0];
$hero_path = $base . DIRECTORY_SEPARATOR . '2026' . DIRECTORY_SEPARATOR . '08'
    . DIRECTORY_SEPARATOR . $names[0];
copy(lifecycle_source(1), $hero_path);

// The plugin pins the REAL library size: 364 attachments at acquisition and 369
// after the five recovered uploads. A six-row toy library could never reach the
// terminal `completed` transition, so this world is built to the real totals:
// 362 unrelated originals + the fixed hero 7609 + the one private Hetron
// exception = 364, of which 363 are hashed and exactly one is the exception.
$attachment_ids = array(7609, FRPD_MG_PRIVATE_ATTACHMENT_ID);
$attachment_paths = array(7609 => $hero_path, FRPD_MG_PRIVATE_ATTACHMENT_ID => false);
$attached_files = array(7609 => $hero_relative);
$filler_dir = $base . DIRECTORY_SEPARATOR . '2026' . DIRECTORY_SEPARATOR . '01';
mkdir($filler_dir, 0700, true);
for ($index = 0; $index < 362; $index++) {
    $filler_id = 9000 + $index;
    $filler_path = $filler_dir . DIRECTORY_SEPARATOR . 'unrelated-' . $filler_id . '.png';
    file_put_contents($filler_path, "\x89PNG\r\n\x1a\n" . str_pad((string) $filler_id, 64, 'x'));
    $attachment_ids[] = $filler_id;
    $attachment_paths[$filler_id] = $filler_path;
    $attached_files[$filler_id] = '2026/01/unrelated-' . $filler_id . '.png';
}
sort($attachment_ids, SORT_NUMERIC);
check(count($attachment_ids) === FRPD_MG_RECOVERY_BASELINE_ATTACHMENT_TOTAL,
    'the lifecycle world starts at the pinned baseline attachment total');

file_put_contents($world_path, json_encode(array(
    'upload_basedir' => $base,
    'product_ids' => array(1368, 1397),
    'attachment_ids' => $attachment_ids,
    'attachment_paths' => $attachment_paths,
    'attached_files' => $attached_files,
    'post_meta' => array(
        1397 => array('_thumbnail_id' => '6991',
                      '_product_image_gallery' => '6992,6993,6994'),
        FRPD_MG_PRIVATE_ATTACHMENT_ID => array(
            '_wp_attached_file' => FRPD_MG_PRIVATE_ATTACHMENT_FILE),
    ),
    'thumbnail_ids' => array(1397 => 6991),
    'state' => null,
    'cookie' => '',
), JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));

$contract = array(
    'produced_by' => 'test_media_mutation_guard_recovery_lifecycle.php',
    'plugin_version' => FRPD_MG_VERSION,
    'state_schema' => FRPD_MG_STATE_SCHEMA,
    'proof_schema' => FRPD_MG_PROOF_SCHEMA,
);

$contract['capability'] = lifecycle_step('capability', $world_path);
check(is_array($contract['capability'])
    && $contract['capability']['plugin_version'] === FRPD_MG_VERSION,
    'the lifecycle capability projection is the running build');

$contract['origin_only_proof'] = lifecycle_step('origin_proof', $world_path);
check($contract['origin_only_proof']['complete'] === true
    && $contract['origin_only_proof']['origin_only'] === array(),
    'the lifecycle starts from a complete origin proof with no origin-only file');

$acquired = lifecycle_step('acquire', $world_path);
$contract['guard_acquired'] = $acquired;
check($acquired['mode'] === 'guard_acquired' && $acquired['guard_active'] === true,
    'the real acquire handler acquires the Open Manway recovery guard');
check($acquired['schema'] === FRPD_MG_PROOF_SCHEMA,
    'the real acquisition proof carries the running proof schema');
check($acquired['recovery']['missing_positions'] === array(2, 3, 4, 5, 6),
    'the acquisition records the exact immutable missing-position list');
check($acquired['recovery']['initial_attachments'] === array($names[0] => 7609),
    'the acquisition binds position 1 to exactly attachment 7609');
check($acquired['reserved_uploads'] === 0,
    'a newly acquired recovery guard reserves nothing');

$contract['guarded_snapshot_initial'] = lifecycle_step('guarded_snapshot', $world_path);
check($contract['guarded_snapshot_initial']['mode'] === 'guarded_snapshot'
    && $contract['guarded_snapshot_initial']['recovery']['bound_uploads'] === array(),
    'the first guarded snapshot proves no upload has bound yet');

// Five real reserved uploads, in ascending missing order.
$uploaded = array(2 => 7700, 3 => 7803, 4 => 7704, 5 => 7805, 6 => 7806);
$progress = array();
foreach ($uploaded as $position => $attachment_id) {
    $output = lifecycle_raw('upload', $world_path, $position . ':' . $attachment_id);
    check(str_contains($output, 'UPLOAD_BOUND: ' . $names[$position - 1]
        . ' -> ' . $attachment_id),
        'the real upload chain reserves and binds position ' . $position);
    $snapshot = lifecycle_step('guarded_snapshot', $world_path);
    $bound = $snapshot['recovery']['bound_uploads'];
    check(count($bound) === count($progress) + 1,
        'guarded progress grows by exactly one binding after upload ' . $position);
    check($snapshot['recovery']['missing_positions'] === array(2, 3, 4, 5, 6),
        'the immutable missing list never changes as uploads land');
    check($snapshot['recovery']['unbound_reservation'] === null,
        'no reservation is left unbound after upload ' . $position . ' binds');
    $progress[$position] = $attachment_id;
    $contract['guarded_snapshot_after_' . $position] = $snapshot;
}
check($contract['guarded_snapshot_after_6']['recovery']['remaining_positions'] === array(),
    'the final guarded snapshot has no remaining missing position');

$final_ids = array(7609, 7700, 7803, 7704, 7805, 7806);
$committed = lifecycle_step('gallery', $world_path);
$contract['recovery_gallery_committed'] = $committed;
check($committed['mode'] === 'recovery_gallery_committed',
    'the owner-bound admin-post route commits the recovery gallery');
check($committed['attachment_ids'] === $final_ids,
    'the server derives the six ordered IDs from its own durable state');
check($committed['state_status'] === 'gallery',
    'the commit leaves the guard in the claimed gallery state');

$completed = lifecycle_step('complete', $world_path);
$contract['guard_completed'] = $completed;
check($completed['mode'] === 'guard_completed' && $completed['attachment_ids'] === $final_ids,
    'the real completion proof names the exact six recovered attachments');
check($completed['schema'] === FRPD_MG_PROOF_SCHEMA,
    'the real completion proof carries the running proof schema');

$world = json_decode((string) file_get_contents($world_path), true);
check(($world['state']['state_status'] ?? '') === 'completed',
    'the guard row reaches the terminal completed state');
check((string) ($world['post_meta'][1397]['_thumbnail_id'] ?? '') === '7609'
    && (string) ($world['post_meta'][1397]['_product_image_gallery'] ?? '')
        === '7700,7803,7704,7805,7806',
    'product 1397 carries exactly the recovered gallery after completion');

// ======================================================================
// Publish the contract the Python consumer is tested against.
// ======================================================================
$testdata = __DIR__ . DIRECTORY_SEPARATOR . 'testdata';
if (!is_dir($testdata)) { mkdir($testdata, 0700, true); }
$serialized = json_encode($contract, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
check(!str_contains($serialized, $base),
    'the published proof contract discloses no absolute server path');
check(!str_contains($serialized, 'nonce') && !str_contains($serialized, 'cookie')
    && !str_contains($serialized, 'session'),
    'the published proof contract carries no nonce, cookie or session material');
file_put_contents($testdata . DIRECTORY_SEPARATOR . 'guard_107_proof_contract.json',
    $serialized . "\n");

echo "PASS {$passed}\n";

// ======================================================================
// Driver helpers
// ======================================================================
function lifecycle_raw($step, $world_path, $argument = '') {
    $command = escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ' '
        . escapeshellarg($step) . ' ' . escapeshellarg($world_path)
        . ('' === $argument ? '' : ' ' . escapeshellarg($argument)) . ' 2>&1';
    return (string) shell_exec($command);
}

function lifecycle_step($step, $world_path, $argument = '') {
    $output = lifecycle_raw($step, $world_path, $argument);
    if (1 !== preg_match(
            '#<script type="application/json" id="frpd-media-guard-proof">(.*?)</script>#s',
            $output, $match)) {
        throw new RuntimeException('lifecycle step ' . $step . ' produced no proof: ' . $output);
    }
    $decoded = json_decode(html_entity_decode($match[1], ENT_QUOTES), true);
    if (!is_array($decoded)) {
        throw new RuntimeException('lifecycle step ' . $step . ' produced unreadable proof.');
    }
    return $decoded;
}

function lifecycle_reset_tree($base) {
    if (is_dir($base)) {
        $iterator = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator($base, FilesystemIterator::SKIP_DOTS),
            RecursiveIteratorIterator::CHILD_FIRST
        );
        foreach ($iterator as $entry) {
            $entry->isDir() ? @rmdir($entry->getPathname()) : @unlink($entry->getPathname());
        }
        @rmdir($base);
    }
    mkdir($base . DIRECTORY_SEPARATOR . '2026' . DIRECTORY_SEPARATOR . '08', 0700, true);
}

// ======================================================================
// Shared world helpers
// ======================================================================
function lifecycle_manway_names() {
    return array_column(frpd_mg_expected_images('open_manway'), 'filename');
}

function lifecycle_source($position) {
    return 'C:/FRPDepot/Dado/20_Working/frp_manway/approved_gallery_20260820/'
        . lifecycle_manway_names()[$position - 1];
}

function lifecycle_load_world($path) {
    $GLOBALS['lifecycle_world_path'] = $path;
    $world = json_decode((string) file_get_contents($path), true);
    $GLOBALS['upload_basedir'] = (string) $world['upload_basedir'];
    $GLOBALS['product_ids'] = array_map('intval', $world['product_ids']);
    $GLOBALS['attachment_ids'] = array_map('intval', $world['attachment_ids']);
    $GLOBALS['attachment_paths'] = array();
    foreach ((array) $world['attachment_paths'] as $id => $value) {
        $GLOBALS['attachment_paths'][(int) $id] = (string) $value;
    }
    $GLOBALS['attached_files'] = array();
    $GLOBALS['attachment_posts'] = array();
    foreach ((array) $world['attached_files'] as $id => $value) {
        $GLOBALS['attached_files'][(int) $id] = (string) $value;
        set_attachment_post((int) $id);
    }
    $GLOBALS['post_meta'] = array();
    foreach ((array) $world['post_meta'] as $id => $rows) {
        $GLOBALS['post_meta'][(int) $id] = (array) $rows;
    }
    $GLOBALS['thumbnail_ids'] = array();
    foreach ((array) $world['thumbnail_ids'] as $id => $value) {
        $GLOBALS['thumbnail_ids'][(int) $id] = (int) $value;
    }
    // The one intentionally private Hetron attachment, exactly as production holds it.
    set_attachment_post(FRPD_MG_PRIVATE_ATTACHMENT_ID, array(
        'post_status' => 'private',
        'post_mime_type' => FRPD_MG_PRIVATE_ATTACHMENT_MIME,
        'post_name' => FRPD_MG_PRIVATE_ATTACHMENT_SLUG,
        'post_date_gmt' => FRPD_MG_PRIVATE_ATTACHMENT_DATE_GMT,
    ));
    $GLOBALS['wpdb']->state = is_array($world['state']) ? $world['state'] : null;
    if ('' !== (string) $world['cookie']) {
        $_COOKIE[FRPD_MG_COOKIE] = (string) $world['cookie'];
    }
    reset_lock();
}

function lifecycle_save_world($path) {
    file_put_contents($path, json_encode(array(
        'upload_basedir' => $GLOBALS['upload_basedir'],
        'product_ids' => array_values($GLOBALS['product_ids']),
        'attachment_ids' => array_values($GLOBALS['attachment_ids']),
        'attachment_paths' => $GLOBALS['attachment_paths'],
        'attached_files' => $GLOBALS['attached_files'],
        'post_meta' => $GLOBALS['post_meta'],
        'thumbnail_ids' => $GLOBALS['thumbnail_ids'],
        'state' => $GLOBALS['wpdb']->state,
        'cookie' => (string) ($_COOKIE[FRPD_MG_COOKIE] ?? ''),
    ), JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
}

/** Drive ONE real fixed upload through the plugin's whole upload chain. */
function lifecycle_upload($position, $attachment_id) {
    $names = lifecycle_manway_names();
    $filename = $names[$position - 1];
    $temporary = $GLOBALS['upload_basedir'] . DIRECTORY_SEPARATOR . 'incoming-' . $filename;
    copy(lifecycle_source($position), $temporary);
    $file = frpd_mg_upload_prefilter(upload_file($filename, $temporary));
    if (($file['error'] ?? UPLOAD_ERR_OK) !== UPLOAD_ERR_OK) {
        echo 'UPLOAD_REFUSED: ' . (string) $file['error'] . "\n";
        return;
    }
    $destination = $GLOBALS['upload_basedir'] . DIRECTORY_SEPARATOR . '2026'
        . DIRECTORY_SEPARATOR . '08' . DIRECTORY_SEPARATOR . $filename;
    $move = frpd_mg_pre_move_uploaded_file(true, $file, $destination, 'image/png');
    if (is_wp_error($move)) {
        echo 'MOVE_REFUSED: ' . $move->get_error_message() . "\n";
        return;
    }
    rename($temporary, $destination);
    $url = 'https://frpdepots.com/wp-content/uploads/2026/08/' . $filename;
    $moved = frpd_mg_post_move_upload(
        array('file' => $destination, 'url' => $url, 'type' => 'image/png'), 'upload');
    if (isset($moved['error'])) {
        echo 'POST_MOVE_REFUSED: ' . (string) $moved['error'] . "\n";
        return;
    }
    frpd_mg_insert_post_data(array(
        'post_type' => 'attachment', 'post_mime_type' => 'image/png', 'guid' => $url,
    ), array(), array(), false);
    $GLOBALS['attachment_ids'][] = (int) $attachment_id;
    sort($GLOBALS['attachment_ids'], SORT_NUMERIC);
    $GLOBALS['attachment_paths'][(int) $attachment_id] = $destination;
    $GLOBALS['attached_files'][(int) $attachment_id] = '2026/08/' . $filename;
    set_attachment_post((int) $attachment_id);
    frpd_mg_capture_allowed_attachment((int) $attachment_id);
    echo 'UPLOAD_BOUND: ' . $filename . ' -> ' . (int) $attachment_id . "\n";
}

/** Present exactly one urlencoded POST body, as the real admin form does. */
function lifecycle_post_form($fields) {
    $_SERVER['REQUEST_METHOD'] = 'POST';
    $_SERVER['CONTENT_TYPE'] = 'application/x-www-form-urlencoded';
    $_GET = array();
    $_FILES = array();
    $_POST = $fields;
}

function lifecycle_run_step($step, $argument) {
    switch ($step) {
        case 'acquire':
            $_POST = array('family' => 'open_manway', '_wpnonce' => 'lifecycle');
            frpd_mg_handle_acquire();
            return;
        case 'guarded_snapshot':
            $_POST = array('_wpnonce' => 'lifecycle');
            frpd_mg_handle_guarded_snapshot();
            return;
        case 'origin_proof':
            $_POST = array('_wpnonce' => 'lifecycle');
            frpd_mg_handle_origin_proof();
            return;
        case 'upload':
            $parts = array_map('intval', explode(':', $argument));
            lifecycle_upload($parts[0], $parts[1]);
            return;
        case 'gallery':
            $before = frpd_mg_product_gallery_ids(FRPD_MG_RECOVERY_PRODUCT_ID);
            lifecycle_post_form(array(
                'action' => FRPD_MG_RECOVERY_ACTION,
                '_wpnonce' => 'lifecycle',
                'if_match' => '' === $argument ? frpd_mg_gallery_etag($before) : $argument,
            ));
            frpd_mg_handle_recovery_gallery();
            return;
        case 'complete':
            $_POST = array('_wpnonce' => 'lifecycle');
            frpd_mg_handle_complete();
            return;
        case 'capability':
            echo '<script type="application/json" id="frpd-media-guard-proof">'
                . json_encode(frpd_mg_capability_projection(), JSON_UNESCAPED_SLASHES)
                . '</script>';
            return;
    }
    throw new RuntimeException('unknown lifecycle step ' . $step);
}
