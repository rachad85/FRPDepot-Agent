<?php
/**
 * Child harness for the one owner-bound Open Manway recovery gallery commit.
 *
 * It runs in a separate process ONLY because frpd_mg_render_proof() ends the
 * request with exit, exactly as it does in WordPress. The parent suite drives
 * one scenario per process and asserts on this script's stdout. Nothing here
 * touches WordPress, the network or any website.
 *
 * 1.0.7 CORRECTION: the body is now presented the way the real admin form
 * presents it -- one urlencoded POST carrying EXACTLY `action`, `_wpnonce` and
 * `if_match`. `_wp_http_referer` is no longer sent, because the handler no
 * longer accepts it; the `referer_field` scenario proves that it is REFUSED.
 *
 * Usage: php test_media_mutation_guard_recovery_commit.php <scenario>
 */
require __DIR__ . '/guard_test_bootstrap.php';

$scenario = $argv[1] ?? 'ok';
$GLOBALS['product_ids'] = array(1368, 1397);
$secret = 'fixed-recovery-owner-secret';
$names = array_column(frpd_mg_expected_images('open_manway'), 'filename');
$ids = array(7609, 7700, 7803, 7704, 7805, 7806);

$record = array(
    'contract' => FRPD_MG_RECOVERY_CONTRACT,
    'initial_attachments' => array(
        $names[0] => 7609, $names[1] => 7700, $names[3] => 7704,
    ),
    'missing_positions' => array(3, 5, 6),
    'reserved' => array($names[2], $names[4], $names[5]),
);
$attachments = array(
    $names[0] => 7609, $names[1] => 7700, $names[3] => 7704,
    $names[2] => 7803, $names[4] => 7805, $names[5] => 7806,
);
if ('incomplete' === $scenario) {
    unset($attachments[$names[5]]);
    $record['reserved'] = array($names[2], $names[4]);
}
$state = array(
    'family' => 'open_manway',
    'owner_hash' => hash('sha256', $secret),
    'expires' => time() + 900,
    'snapshot_count' => 6,
    'record' => $record,
    'attachments' => $attachments,
);
if ('family_contract' === $scenario) {
    $state = array(
        'family' => 'stub_flange',
        'owner_hash' => hash('sha256', $secret),
        'expires' => time() + 900,
        'snapshot_count' => 6,
        'reserved' => array(),
        'attachments' => array('01_authentic_source_hero.png' => 4849),
    );
}
set_test_state($state);

$GLOBALS['attachment_ids'] = array_values($attachments);
$GLOBALS['thumbnail_ids'][1397] = 6991;
$GLOBALS['post_meta'][1397]['_thumbnail_id'] = '6991';
$GLOBALS['post_meta'][1397]['_product_image_gallery'] = '6992,6993,6994';
$before = array(6991, 6992, 6993, 6994);
$etag = frpd_mg_gallery_etag($before);

$_COOKIE[FRPD_MG_COOKIE] = 'no_cookie' === $scenario ? '' : $secret;
if ('no_cookie' === $scenario) {
    unset($_COOKIE[FRPD_MG_COOKIE]);
}
if ('foreign_cookie' === $scenario) {
    $_COOKIE[FRPD_MG_COOKIE] = 'a-different-browsers-secret';
}

// The exact three-field urlencoded body a real submission of the fixed form makes.
$_SERVER['REQUEST_METHOD'] = 'get_method' === $scenario ? 'GET' : 'POST';
$_SERVER['CONTENT_TYPE'] = 'wrong_content_type' === $scenario
    ? 'multipart/form-data; boundary=x' : 'application/x-www-form-urlencoded';
$_GET = array();
$_FILES = array();
$_POST = array(
    'action' => FRPD_MG_RECOVERY_ACTION,
    '_wpnonce' => 'test-nonce',
    'if_match' => 'wrong_if_match' === $scenario
        ? '"' . str_repeat('b', 64) . '"' : $etag,
);
if ('extra_field' === $scenario) {
    $_POST['product_id'] = 1397;
}
if ('referer_field' === $scenario) {
    $_POST['_wp_http_referer'] = '/wp-admin/tools.php?page=frpd-media-mutation-guard';
}
if ('caller_ids' === $scenario) {
    $_POST['images'] = array(array('id' => 1), array('id' => 2));
}
if ('caller_path' === $scenario) {
    $_POST['file'] = '2026/08/' . $names[2];
}
if ('missing_field' === $scenario) {
    unset($_POST['if_match']);
}
if ('malformed_if_match' === $scenario) {
    $_POST['if_match'] = 'not-a-hash';
}
if ('wrong_action' === $scenario) {
    $_POST['action'] = 'frpd_media_guard_complete';
}
if ('query_field' === $scenario) {
    $_GET['product_id'] = '1397';
}
if ('file_field' === $scenario) {
    $_FILES['upload'] = array('name' => $names[2], 'tmp_name' => '', 'error' => 0);
}
// A duplicate raw pair is invisible in $_POST -- PHP folds it -- so the raw body
// is supplied directly to prove the handler reads and rejects the wire form.
$raw_body = null;
if ('duplicate_field' === $scenario) {
    $raw_body = http_build_query($_POST, '', '&', PHP_QUERY_RFC3986)
        . '&' . rawurlencode('if_match') . '=' . rawurlencode($etag);
}
if ('post_claim_rest_failure' === $scenario) {
    $GLOBALS['force_rest_failure_after_claim'] = true;
}
if ('post_claim_exception' === $scenario) {
    $GLOBALS['force_rest_exception_after_claim'] = true;
}

// Registered BEFORE the handler runs: the success path ends the request with
// exit, exactly as WordPress does, so a shutdown function is the only place the
// evidence can be printed for both outcomes.
register_shutdown_function(static function () use ($ids) {
    echo "\nDISPATCHED: " . count($GLOBALS['rest_dispatched']) . "\n";
    foreach ($GLOBALS['rest_dispatched'] as $call) {
        echo 'CALL: ' . $call['method'] . ' ' . $call['route'] . ' '
            . json_encode($call['json']) . ' if-match=' . $call['if_match'] . "\n";
    }
    echo 'GALLERY: ' . implode(',', (array) frpd_mg_product_gallery_ids(1397)) . "\n";
    echo 'STATE: ' . (string) ($GLOBALS['wpdb']->state['state_status'] ?? 'none') . "\n";
    echo 'NONCES: ' . implode(',', $GLOBALS['checked_nonces']) . "\n";
    echo 'EXPECTED_IDS: ' . implode(',', $ids) . "\n";
});

reset_lock();
if (null !== $raw_body) {
    // Prove the raw-body reader itself, not just the folded superglobal.
    $fields = frpd_mg_exact_recovery_post_fields($raw_body);
    if (is_wp_error($fields)) {
        echo 'REFUSED: ' . $fields->get_error_message() . "\n";
        return;
    }
}
try {
    frpd_mg_handle_recovery_gallery();
    echo "NO_EXIT\n";
} catch (RuntimeException $exception) {
    echo 'REFUSED: ' . $exception->getMessage() . "\n";
}
