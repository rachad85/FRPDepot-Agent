<?php
// Shared offline harness bootstrap; the assertion suites require this file.
require __DIR__ . '/guard_test_bootstrap.php';

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
check(FRPD_MG_VERSION === '1.0.7', 'guard version is pinned to v1.0.7');
check(FRPD_MG_STATE_SCHEMA === 3, 'durable guard state schema is bumped to 3');
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
    'admin_post_frpd_media_guard_origin_proof',
    'admin_post_frpd_media_guard_recovery_gallery',
    'admin_post_frpd_media_guard_snapshot',
), 'guard exposes only the six exact fixed admin actions in v1.0.7');
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
check($snapshot['schema'] === FRPD_MG_PROOF_SCHEMA && $snapshot['schema'] === 3
    && $snapshot['private_exceptions'] === array(),
    'ordinary complete snapshot uses the running proof schema 3 with no private exception');
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
    'record' => array(
        'contract' => FRPD_MG_FAMILY_CONTRACT,
        'initial_attachments' => array('01_authentic_source_hero.png' => 4849),
        'missing_positions' => array(2, 3, 4, 5, 6),
        'reserved' => array(),
    ),
    'attachments' => array('01_authentic_source_hero.png' => 4849),
), 'Stub Flange acquisition still produces its sole durable reuse binding, unchanged and not generalized');

$secret = 'fixed-test-owner-secret';
$persisted = frpd_mg_insert_state('stub_flange', $secret, $reuse_snapshot);
check(is_array($persisted)
    && $persisted['record'] === $reuse_bindings['record']
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
    'record' => $reuse_bindings['record'],
    'reserved' => $reuse_bindings['record']['reserved'],
    'attachments' => $reuse_bindings['attachments'],
);
$stub_names = array_column(frpd_mg_expected_images('stub_flange'), 'filename');
$stub_excess_state = $state;
$stub_excess_state['record'] = test_record('stub_flange', $stub_names);
$stub_excess_state['reserved'] = $stub_excess_state['record']['reserved'];
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
$pipe_excess_state['record'] = test_record('pipe', $pipe_names);
$pipe_excess_state['reserved'] = $pipe_excess_state['record']['reserved'];
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
    && json_decode($GLOBALS['wpdb']->state['reserved_json'], true)['reserved'] === array(),
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
check(json_decode($GLOBALS['wpdb']->state['reserved_json'], true)['reserved']
    === array(basename($target)), 'only exact position 2 is reserved after the reuse binding');
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
$complete_state['record'] = test_record('stub_flange', $fixed_names);
$complete_state['reserved'] = $complete_state['record']['reserved'];
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
// 1.0.7 REMOVED the automatic retire-to-expired write. An expired row keeps its own
// durable evidence untouched, and no acquisition may replace it while it is
// unresolved -- that is what closes the semantic-retry route, and it is why there
// is deliberately no reset, force-unlock or cleanup path anywhere in the plugin.
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'gallery',
    'a refused expired completion leaves the durable row byte-semantically untouched');
check(frpd_mg_active_state() === null, 'an expired row is never reported as an active guard');
check(is_array(frpd_mg_exact_state()) && frpd_mg_exact_state()['is_active'] === false,
    'the expired row is still readable as exact evidence, not collapsed to null');
check(frpd_mg_terminal_state_is_replaceable(frpd_mg_exact_state()) === false,
    'an expired unresolved row can never be replaced by a fresh acquisition');
$GLOBALS['wpdb']->state = $db_before_completion;
$GLOBALS['wpdb']->state['expires_epoch'] = time() + 1800;
$active_before_completion = frpd_mg_active_state();
check(frpd_mg_complete_state($active_before_completion) === true, 'completion persists terminal guard state');
check($GLOBALS['wpdb']->state['state_status'] === 'completed', 'guard row retained as completed, not deleted');
check(frpd_mg_active_state() === null, 'completed guard is no longer active');

unlink($benign);
unlink($bad);
echo "PASS {$passed}\n";
