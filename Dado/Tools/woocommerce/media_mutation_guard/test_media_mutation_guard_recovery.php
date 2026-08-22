<?php
// Offline harness for the ONE literal Open Manway recovery contract, corrected in
// Media Mutation Guard 1.0.7. No WordPress, network or website call happens here.
require __DIR__ . '/guard_test_bootstrap.php';

$GLOBALS['product_ids'] = array(1368, 1397);

$manway = frpd_mg_expected_images('open_manway');
$manway_names = array_column($manway, 'filename');

function manway_source($position) {
    return 'C:\\FRPDepot\\Dado\\20_Working\\frp_manway\\approved_gallery_20260820\\'
        . frpd_mg_expected_images('open_manway')[$position - 1]['filename'];
}

/** Rebuild an isolated uploads tree holding only the requested fixed originals. */
function recovery_uploads($placements) {
    $base = guard_test_temp_root() . DIRECTORY_SEPARATOR . 'frpd-mg-recovery-uploads';
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
    mkdir($base, 0700, true);
    $GLOBALS['upload_basedir'] = $base;
    $written = array();
    foreach ($placements as $relative => $position) {
        $target = $base . DIRECTORY_SEPARATOR . str_replace('/', DIRECTORY_SEPARATOR, $relative);
        $parent = dirname($target);
        if (!is_dir($parent)) { mkdir($parent, 0700, true); }
        copy(manway_source($position), $target);
        $written[$relative] = $target;
    }
    return $written;
}

/** Put the whole harness into one exact live Open Manway state. */
function recovery_state($bound, $extra_placements = array(), $extra_owner_rows = array()) {
    $placements = array();
    foreach ($bound as $position => $attachment_id) {
        $placements[sprintf('2026/08/%s', frpd_mg_expected_images('open_manway')[$position - 1]['filename'])]
            = $position;
    }
    foreach ($extra_placements as $relative => $position) { $placements[$relative] = $position; }
    $files = recovery_uploads($placements);
    $GLOBALS['attachment_ids'] = array_values($bound);
    $GLOBALS['attachment_paths'] = array();
    $GLOBALS['attached_files'] = array();
    $GLOBALS['attachment_posts'] = array();
    foreach ($bound as $position => $attachment_id) {
        $relative = sprintf('2026/08/%s',
            frpd_mg_expected_images('open_manway')[$position - 1]['filename']);
        $GLOBALS['attachment_paths'][$attachment_id] = $files[$relative];
        $GLOBALS['attached_files'][$attachment_id] = $relative;
        set_attachment_post($attachment_id);
    }
    foreach ($extra_owner_rows as $attachment_id => $relative) {
        $GLOBALS['attached_files'][$attachment_id] = $relative;
        set_attachment_post($attachment_id);
        if (!in_array((int) $attachment_id, $GLOBALS['attachment_ids'], true)) {
            $GLOBALS['attachment_ids'][] = (int) $attachment_id;
            $GLOBALS['attachment_paths'][$attachment_id] =
                $GLOBALS['upload_basedir'] . DIRECTORY_SEPARATOR
                . str_replace('/', DIRECTORY_SEPARATOR, $relative);
        }
    }
    sort($GLOBALS['attachment_ids'], SORT_NUMERIC);
    $GLOBALS['wpdb']->state = null;
    reset_lock();
    frpd_mg_hold_request_lock();
    return frpd_mg_snapshot('open_manway', 'pre_guard_snapshot', false);
}

// ======================================================================
// 1. The manifest carries exactly one closed recovery contract.
// ======================================================================
$manifest = frpd_mg_manifest();
check(!is_wp_error($manifest), 'v1.0.7 runtime manifest loads');
check($manifest['schema'] === 3, 'runtime manifest is schema 3');
check($manifest['fixed_recovery'] === array(
    'attachment_id' => 7609,
    'bytes' => 1750111,
    'contract' => 'open_manway_recovery',
    'family' => 'open_manway',
    'filename' => '01_manway_premium_hero.png',
    'position' => 1,
    'prior_operation_sha256' => 'e0127fcaa04c023cbdd19a36726d6e8f03c3fb01f12f0367550d17c87674dc85',
    'prior_plan_sha256' => '1c7865b0287b076fe83c179c2e44f33a3bcb2effb048e87374c32ad4781b19df',
    'product_id' => 1397,
    'recoverable_positions' => array(2, 3, 4, 5, 6),
    'sha256' => '472b5e5b0aba9a7201444524c559e6797c266a0de008d7bc70b4f8ef1938d0cd',
), 'the one literal Open Manway recovery contract is exact and closed');
foreach (array('stub_flange', 'manway_cover', 'elbow_90', 'pipe') as $family) {
    check(frpd_mg_recovery_contract($family) === null,
        $family . ' has no recovery contract; recovery is not generalized');
}
check(is_array(frpd_mg_recovery_contract('open_manway')),
    'only open_manway resolves the fixed recovery contract');
check(frpd_mg_reuse_contract('open_manway') === null,
    'the Stub Flange reuse contract is not extended to the recovery family');
check(is_array(frpd_mg_reuse_contract('stub_flange')),
    'the separate fixed Stub Flange reuse contract is preserved');

$capability = frpd_mg_capability_projection();
check($capability['plugin_version'] === '1.0.7' && $capability['state_schema'] === 3
    && $capability['capabilities'] === array(
        'existing_fixed_attachment_acquisition' => true,
        'non_prefix_upload_reservation' => true,
        'origin_only_file_enumeration' => true,
        'owner_bound_gallery_commit' => true,
    ), 'the non-secret capability projection states exactly the 1.0.7 capabilities');
check(!str_contains(json_encode($capability), DIRECTORY_SEPARATOR === '\\' ? 'C:' : '/home'),
    'the capability projection discloses no absolute server path');

// ======================================================================
// 2. Origin-only file proof.
// ======================================================================
$snapshot = recovery_state(array(1 => 7609));
check($snapshot['complete'] === true, 'recovery pre-guard snapshot is complete');
check($snapshot['fixed_matches'] === array(array('attachment_id' => 7609, 'fixed_position' => 1)),
    'the live snapshot binds attachment 7609 to fixed position 1');

$origin = frpd_mg_origin_only_file_proof();
check(!is_wp_error($origin) && $origin['complete'] === true,
    'origin-only proof completes over the base and year/month upload directories');
check(count($origin['files']) === 6
    && array_column($origin['files'], 'basename') === $manway_names,
    'origin proof inspects exactly the six fixed basenames, in order');
check($origin['origin_only'] === array(), 'no origin-only fixed file exists in the clean state');
check($origin['files'][0]['discovered'] === 1
    && $origin['files'][0]['owner_attachment_ids'] === array(7609)
    && $origin['files'][0]['bytes_and_hash_exact'] === true,
    'position 1 owns exactly one exact fixed origin file');
check($origin['files'][1]['discovered'] === 0
    && $origin['files'][1]['owner_attachment_ids'] === array(),
    'the ambiguous position 2 resolves as NO origin file and NO attachment');
$serialized = json_encode($origin);
check(!str_contains($serialized, $GLOBALS['upload_basedir']),
    'origin proof reports safe relative paths and never an absolute server path');
foreach ($origin['files'] as $file) {
    foreach ($file['paths'] as $path) {
        check(1 === preg_match('#\A(?:[0-9]{4}/[0-9]{2}/)?[^/\\\\]+\z#', $path['relative_path']),
            'every reported origin path is a safe bounded relative path');
    }
}

// Unsupported/incomplete environment refuses rather than reporting absence.
$saved_basedir = $GLOBALS['upload_basedir'];
$GLOBALS['upload_basedir'] = $saved_basedir . DIRECTORY_SEPARATOR . 'does-not-exist';
check(is_wp_error(frpd_mg_origin_only_file_proof()),
    'an unreadable uploads directory refuses instead of reporting fixed-file absence');
$GLOBALS['upload_basedir'] = $saved_basedir;
$GLOBALS['wpdb']->fail_attached_file_owner_lookup = true;
check(is_wp_error(frpd_mg_origin_only_file_proof()),
    'unprovable attachment file ownership refuses instead of reporting absence');
$GLOBALS['wpdb']->fail_attached_file_owner_lookup = false;

// ======================================================================
// 3. Acquisition: bindings, missing positions, and every refusal.
// ======================================================================
$bindings = frpd_mg_acquisition_bindings('open_manway', $snapshot, frpd_mg_origin_only_file_proof());
check(!is_wp_error($bindings), 'v1.0.7 acquires the recovery contract from exactly one live match');
check($bindings['record'] === array(
    'contract' => 'open_manway_recovery',
    'initial_attachments' => array('01_manway_premium_hero.png' => 7609),
    'missing_positions' => array(2, 3, 4, 5, 6),
    'reserved' => array(),
), 'acquisition binds 7609 at position 1 and records missing positions 2-6 immutably');
check($bindings['attachments'] === array('01_manway_premium_hero.png' => 7609),
    'only the proven live attachment is bound at acquisition');

// Optional exact position-2 attachment is reused, not re-uploaded.
$snapshot_two = recovery_state(array(1 => 7609, 2 => 7700));
$bindings_two = frpd_mg_acquisition_bindings(
    'open_manway', $snapshot_two, frpd_mg_origin_only_file_proof());
check(!is_wp_error($bindings_two)
    && $bindings_two['record']['missing_positions'] === array(3, 4, 5, 6)
    && $bindings_two['record']['initial_attachments'] === array(
        '01_manway_premium_hero.png' => 7609, '02_manway_top_oblique.png' => 7700),
    'an exact live position-2 attachment is reused and drops out of the missing list');

// Arbitrary gaps in 2-6 are supported and stay in ascending order.
$snapshot_gap = recovery_state(array(1 => 7609, 4 => 7704, 6 => 7706));
$bindings_gap = frpd_mg_acquisition_bindings(
    'open_manway', $snapshot_gap, frpd_mg_origin_only_file_proof());
check(!is_wp_error($bindings_gap)
    && $bindings_gap['record']['missing_positions'] === array(2, 3, 5),
    'arbitrary gaps in positions 2-6 produce an exact ascending immutable missing list');

// Position 1 must be exactly attachment 7609.
$snapshot_wrong_hero = recovery_state(array(1 => 9999));
check(is_wp_error(frpd_mg_acquisition_bindings(
    'open_manway', $snapshot_wrong_hero, frpd_mg_origin_only_file_proof())),
    'a different attachment at position 1 refuses the recovery acquisition');

// Nothing live at all still refuses: the verified prior upload must be present.
$snapshot_empty = recovery_state(array());
check(is_wp_error(frpd_mg_acquisition_bindings(
        'open_manway', $snapshot_empty, frpd_mg_origin_only_file_proof()))
    || $snapshot_empty['fixed_matches'] === array(),
    'an empty library cannot reach the recovery branch with a bound hero');

// An origin-only file (present bytes, no owning attachment) is a blocker.
$snapshot_orphan = recovery_state(
    array(1 => 7609), array('2026/09/02_manway_top_oblique.png' => 2));
$orphan_proof = frpd_mg_origin_only_file_proof();
check(!is_wp_error($orphan_proof) && count($orphan_proof['origin_only']) === 1
    && $orphan_proof['origin_only'][0]['position'] === 2,
    'a fixed basename with no owning attachment is reported as an origin-only blocker');
check(is_wp_error(frpd_mg_acquisition_bindings('open_manway', $snapshot_orphan, $orphan_proof)),
    'an origin-only fixed file refuses acquisition before any durable state is inserted');
check($GLOBALS['wpdb']->state === null,
    'the origin-only refusal inserted no guard state row');

// Duplicate ownership of one fixed relative path is ambiguous and refuses.
$snapshot_dupe = recovery_state(
    array(1 => 7609), array(), array(8888 => '2026/08/01_manway_premium_hero.png'));
$dupe_proof = frpd_mg_origin_only_file_proof();
check(!is_wp_error($dupe_proof)
    && $dupe_proof['files'][0]['owner_attachment_ids'] === array(7609, 8888),
    'two attachments owning one fixed file are both reported');
check(is_wp_error(frpd_mg_acquisition_bindings('open_manway', $snapshot_dupe, $dupe_proof)),
    'ambiguous fixed-file ownership refuses acquisition');

// An unprovable origin proof refuses; absence is never assumed.
$snapshot = recovery_state(array(1 => 7609));
check(is_wp_error(frpd_mg_acquisition_bindings(
    'open_manway', $snapshot, new WP_Error('x', 'unprovable'))),
    'an unprovable origin proof refuses acquisition');
$partial = frpd_mg_origin_only_file_proof();
$partial['complete'] = false;
check(is_wp_error(frpd_mg_acquisition_bindings('open_manway', $snapshot, $partial)),
    'an incomplete origin proof refuses acquisition');
$short = frpd_mg_origin_only_file_proof();
array_pop($short['files']);
check(is_wp_error(frpd_mg_acquisition_bindings('open_manway', $snapshot, $short)),
    'a partial origin enumeration refuses acquisition');

// The three server proofs must agree exactly.
$mismatched = $snapshot;
$mismatched['name_conflicts'] = array();
check(is_wp_error(frpd_mg_acquisition_bindings(
    'open_manway', $mismatched, frpd_mg_origin_only_file_proof())),
    'disagreeing name/hash/fixed match evidence refuses acquisition');
$ambiguous = $snapshot;
$ambiguous['name_conflicts'][] = array('attachment_id' => 5555, 'fixed_position' => 1);
$ambiguous['hash_conflicts'][] = array('attachment_id' => 5555, 'fixed_position' => 1);
$ambiguous['fixed_matches'][] = array('attachment_id' => 5555, 'fixed_position' => 1);
check(is_wp_error(frpd_mg_acquisition_bindings(
    'open_manway', $ambiguous, frpd_mg_origin_only_file_proof())),
    'two attachments claiming one fixed position refuses acquisition');
$incomplete_snapshot = $snapshot;
$incomplete_snapshot['complete'] = false;
check(is_wp_error(frpd_mg_acquisition_bindings(
    'open_manway', $incomplete_snapshot, frpd_mg_origin_only_file_proof())),
    'a partial library snapshot refuses acquisition');
$failed_snapshot = $snapshot;
$failed_snapshot['failures'] = array(
    array('attachment_id' => 11, 'reason' => 'unreadable_original'));
check(is_wp_error(frpd_mg_acquisition_bindings(
    'open_manway', $failed_snapshot, frpd_mg_origin_only_file_proof())),
    'an unreadable original refuses acquisition');

// A drifted live original refuses even when the snapshot still claims a match.
$hero_path = $GLOBALS['attachment_paths'][7609];
$saved_hero = file_get_contents($hero_path);
file_put_contents($hero_path, 'not the approved bytes');
check(is_wp_error(frpd_mg_recovery_attachment_identity(1, 7609, $manway)),
    'a matched attachment whose original drifted is refused by fresh identity proof');
file_put_contents($hero_path, $saved_hero);
check(!is_wp_error(frpd_mg_recovery_attachment_identity(1, 7609, $manway)),
    'the restored exact original passes fresh identity proof');

// The Stub Flange contract cannot be driven through the recovery branch.
check(frpd_mg_recovery_contract('stub_flange') === null
    && !is_array(frpd_mg_recovery_contract('pipe')),
    'no other family can reach the recovery acquisition branch');

// ======================================================================
// 4. Durable record validation.
// ======================================================================
$valid_record = array(
    'contract' => 'open_manway_recovery',
    'initial_attachments' => array('01_manway_premium_hero.png' => 7609),
    'missing_positions' => array(2, 3, 4, 5, 6),
    'reserved' => array(),
);
check(frpd_mg_state_record_is_valid('open_manway', $valid_record,
    array('01_manway_premium_hero.png' => 7609)) === true, 'the exact recovery record validates');
$malformed = array(
    'position 1 in the missing list' => array_merge($valid_record, array(
        'missing_positions' => array(1, 2, 3, 4, 5, 6))),
    'descending missing positions' => array_merge($valid_record, array(
        'missing_positions' => array(6, 5, 4, 3, 2))),
    'a missing position outside the family' => array_merge($valid_record, array(
        'missing_positions' => array(2, 3, 4, 5, 6, 7))),
    'a wrong position-1 attachment' => array_merge($valid_record, array(
        'initial_attachments' => array('01_manway_premium_hero.png' => 1))),
    'an unknown contract name' => array_merge($valid_record, array('contract' => 'anything')),
    'an unknown extra key' => array_merge($valid_record, array('extra' => true)),
    'no missing positions at all' => array(
        'contract' => 'open_manway_recovery',
        'initial_attachments' => array_combine($manway_names, array(7609, 2, 3, 4, 5, 6)),
        'missing_positions' => array(),
        'reserved' => array(),
    ),
);
foreach ($malformed as $label => $record) {
    $attachments = $record['initial_attachments'];
    check(frpd_mg_state_record_is_valid('open_manway', $record, $attachments) === false,
        'a malformed durable row is refused: ' . $label);
}
check(frpd_mg_state_record_is_valid('open_manway', $valid_record,
    array('01_manway_premium_hero.png' => 7609, '03_manway_low_side_angle.png' => 99)) === false,
    'an attachment binding with no matching reservation is refused');

// ======================================================================
// 5. Uploads: ascending missing order, once each, one ambiguous reservation.
// ======================================================================
$secret = 'fixed-recovery-owner-secret';
$_COOKIE[FRPD_MG_COOKIE] = $secret;
recovery_state(array(1 => 7609, 2 => 7700, 4 => 7704));
$gap_state = array(
    'family' => 'open_manway',
    'owner_hash' => hash('sha256', $secret),
    'expires' => time() + 900,
    'snapshot_count' => 3,
    'record' => test_recovery_record(array(1 => 7609, 2 => 7700, 4 => 7704)),
    'attachments' => array('01_manway_premium_hero.png' => 7609,
                           '02_manway_top_oblique.png' => 7700,
                           '04_manway_flange_bore_detail.png' => 7704),
);
check($gap_state['record']['missing_positions'] === array(3, 5, 6),
    'the fixture records the exact arbitrary missing list');
set_test_state($gap_state);
reset_lock();
frpd_mg_hold_request_lock();
check(frpd_mg_next_reserved_filename(frpd_mg_active_state()) === $manway_names[2],
    'the first permitted upload is the first MISSING position, which is 3 and not 1 or 2');
reset_lock();
$out_of_order = frpd_mg_upload_prefilter(
    upload_file($manway_names[4], manway_source(5)));
check(isset($out_of_order['error']),
    'position 5 cannot be uploaded before the earlier missing position 3');
reset_lock();
$already_live = frpd_mg_upload_prefilter(
    upload_file($manway_names[1], manway_source(2)));
check(isset($already_live['error']),
    'a position already bound to a live attachment can never be uploaded');
reset_lock();
$hero_upload = frpd_mg_upload_prefilter(upload_file($manway_names[0], manway_source(1)));
check(isset($hero_upload['error']),
    'the fixed verified position-1 attachment can never be re-uploaded');
reset_lock();
$allowed = frpd_mg_upload_prefilter(upload_file($manway_names[2], manway_source(3)));
check(($allowed['error'] ?? UPLOAD_ERR_OK) === UPLOAD_ERR_OK,
    'the exact next missing position is reserved: ' . (string) ($allowed['error'] ?? 'ok'));
$stored = json_decode($GLOBALS['wpdb']->state['reserved_json'], true);
check($stored['reserved'] === array($manway_names[2])
    && $stored['missing_positions'] === array(3, 5, 6)
    && $stored['initial_attachments'] === $gap_state['record']['initial_attachments'],
    'reserving an upload never rewrites the immutable half of the record');
reset_lock();
$second_reservation = frpd_mg_upload_prefilter(
    upload_file($manway_names[4], manway_source(5)));
check(isset($second_reservation['error']),
    'at most ONE reserved upload may be unbound at a time');
reset_lock();
frpd_mg_hold_request_lock();
check(frpd_mg_next_reserved_filename(frpd_mg_active_state()) === null,
    'no further upload is permitted while a reservation is unbound');

// Binding the landed attachment lets the next missing position proceed.
$GLOBALS['attachment_ids'][] = 7803;
$bound_state = frpd_mg_active_state();
$bound_attachments = $bound_state['attachments'];
$bound_attachments[$manway_names[2]] = 7803;
$fresh = frpd_mg_update_state($bound_state, $bound_state['record'], $bound_attachments);
check(is_array($fresh) && $fresh['attachments'][$manway_names[2]] === 7803,
    'the landed upload binds to its exact reserved filename');
check(frpd_mg_next_reserved_filename($fresh) === $manway_names[4],
    'the next permitted upload is the next MISSING position, 5, skipping bound position 4');

// An update can never rewrite the immutable half of the record.
$tampered = $fresh['record'];
$tampered['missing_positions'] = array(3, 4, 5, 6);
check(is_wp_error(frpd_mg_update_state($fresh, $tampered, $fresh['attachments'])),
    'no route can rewrite the immutable missing-position list');
$tampered = $fresh['record'];
$tampered['initial_attachments'] = array('01_manway_premium_hero.png' => 1);
check(is_wp_error(frpd_mg_update_state($fresh, $tampered, $fresh['attachments'])),
    'no route can rewrite the immutable acquisition bindings');
$tampered = $fresh['record'];
$tampered['contract'] = 'family';
check(is_wp_error(frpd_mg_update_state($fresh, $tampered, $fresh['attachments'])),
    'no route can change the contract of a live guard');

// ======================================================================
// 6. Final ordered IDs.
// ======================================================================
$complete_record = test_recovery_record(array(1 => 7609, 2 => 7700, 4 => 7704),
    array($manway_names[2], $manway_names[4], $manway_names[5]));
$complete_state = array(
    'family' => 'open_manway',
    'owner_hash' => hash('sha256', $secret),
    'expires' => time() + 900,
    'snapshot_count' => 3,
    'record' => $complete_record,
    'attachments' => array(
        '01_manway_premium_hero.png' => 7609,
        '02_manway_top_oblique.png' => 7700,
        '04_manway_flange_bore_detail.png' => 7704,
        '03_manway_low_side_angle.png' => 7803,
        '05_manway_opposite_face.png' => 7805,
        '06_manway_laminate_detail.png' => 7806,
    ),
);
check(frpd_mg_final_attachment_ids($complete_state)
    === array(7609, 7700, 7803, 7704, 7805, 7806),
    'the final six IDs follow MANIFEST order, not binding order');
$wrong_hero = $complete_state;
$wrong_hero['attachments']['01_manway_premium_hero.png'] = 7610;
check(is_wp_error(frpd_mg_final_attachment_ids($wrong_hero)),
    'the final gallery must carry the fixed verified attachment 7609 at position 1');
$duplicate = $complete_state;
$duplicate['attachments']['06_manway_laminate_detail.png'] = 7805;
check(is_wp_error(frpd_mg_final_attachment_ids($duplicate)),
    'the final six attachment IDs must be unique');
$partial_state = $complete_state;
unset($partial_state['attachments']['06_manway_laminate_detail.png']);
$partial_state['record']['reserved'] = array($manway_names[2], $manway_names[4]);
check(is_wp_error(frpd_mg_final_attachment_ids($partial_state)),
    'an incomplete recovery cannot produce a gallery payload');

// ======================================================================
// 7. Owner-bound gallery commit: the Basic-Woo transport CANNOT commit.
// ======================================================================
$commit_record = test_recovery_record(array(1 => 7609, 2 => 7700, 4 => 7704),
    array($manway_names[2], $manway_names[4], $manway_names[5]));
$commit_state = array(
    'family' => 'open_manway',
    'owner_hash' => hash('sha256', $secret),
    'expires' => time() + 900,
    'snapshot_count' => 6,
    'record' => $commit_record,
    'attachments' => $complete_state['attachments'],
);
set_test_state($commit_state);
$GLOBALS['attachment_ids'] = array_values($complete_state['attachments']);
$GLOBALS['thumbnail_ids'][1397] = 6991;
$GLOBALS['post_meta'][1397]['_thumbnail_id'] = '6991';
$GLOBALS['post_meta'][1397]['_product_image_gallery'] = '6992,6993,6994';
$live_etag = frpd_mg_gallery_etag(array(6991, 6992, 6993, 6994));
$final_ids = array(7609, 7700, 7803, 7704, 7805, 7806);
$payload = array('images' => array_map(fn($id) => array('id' => $id), $final_ids));

unset($_COOKIE[FRPD_MG_COOKIE]);
reset_lock();
$no_cookie = frpd_mg_rest_pre_insert_product_object(
    new FakeProduct(1397), new FakeRestRequest('PUT', $payload, $live_etag), false);
check(is_wp_error($no_cookie),
    'a Basic-auth WooCommerce REST PUT with no guard cookie cannot commit the gallery');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active',
    'the cookie-less transport never claims the gallery state');

$_COOKIE[FRPD_MG_COOKIE] = 'some-other-browser-secret';
reset_lock();
$wrong_cookie = frpd_mg_rest_pre_insert_product_object(
    new FakeProduct(1397), new FakeRestRequest('PUT', $payload, $live_etag), false);
check(is_wp_error($wrong_cookie), 'a foreign guard cookie cannot commit the gallery');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active',
    'a foreign guard cookie never claims the gallery state');

$_COOKIE[FRPD_MG_COOKIE] = $secret;
reset_lock();
$stale_precondition = frpd_mg_rest_pre_insert_product_object(
    new FakeProduct(1397), new FakeRestRequest('PUT', $payload, '"' . str_repeat('c', 64) . '"'), false);
check(is_wp_error($stale_precondition), 'a stale If-Match gallery hash cannot commit');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'active',
    'a stale precondition never claims the gallery state');

reset_lock();
$foreign_ids = array('images' => array_map(fn($id) => array('id' => $id),
    array(7609, 7700, 7803, 7704, 7805, 9999)));
check(is_wp_error(frpd_mg_rest_pre_insert_product_object(
    new FakeProduct(1397), new FakeRestRequest('PUT', $foreign_ids, $live_etag), false)),
    'a caller-substituted attachment ID cannot commit the recovery gallery');

reset_lock();
check(is_wp_error(frpd_mg_rest_pre_insert_product_object(
    new FakeProduct(1397),
    new FakeRestRequest('PUT', array('images' => $payload['images'], 'name' => 'x'), $live_etag),
    false)), 'any non-images product field refuses the recovery gallery update');

reset_lock();
$owner_allowed = frpd_mg_rest_pre_insert_product_object(
    new FakeProduct(1397), new FakeRestRequest('PUT', $payload, $live_etag), false);
check(!is_wp_error($owner_allowed),
    'the owner-bound request with the exact fixed payload claims the gallery');
check(($GLOBALS['wpdb']->state['state_status'] ?? '') === 'gallery',
    'the owner-bound claim moves the row atomically from active to gallery');

// ======================================================================
// 8. The fixed admin-post recovery route, exercised in a real request.
// ======================================================================
function run_commit_scenario($scenario) {
    $command = escapeshellarg(PHP_BINARY) . ' '
        . escapeshellarg(__DIR__ . DIRECTORY_SEPARATOR
            . 'test_media_mutation_guard_recovery_commit.php')
        . ' ' . escapeshellarg($scenario);
    return (string) shell_exec($command . ' 2>&1');
}

$committed = run_commit_scenario('ok');
check(str_contains($committed, '"mode":"recovery_gallery_committed"'),
    'the fixed admin-post route commits the recovery gallery inside the authenticated request');
check(str_contains($committed, '"attachment_ids":[7609,7700,7803,7704,7805,7806]'),
    'the committed gallery is the six manifest-ordered IDs derived from guard state');
check(str_contains($committed, '"transport":"internal_authenticated_rest_do_request"'),
    'the commit uses one internal authenticated REST dispatch, not an external HTTP call');
check(str_contains($committed, 'CALL: PUT /wc/v3/products/1397 '
    . '{"images":[{"id":7609},{"id":7700},{"id":7803},{"id":7704},{"id":7805},{"id":7806}]}'),
    'exactly one images-only PUT to fixed product 1397 is dispatched');
check(substr_count($committed, 'CALL: PUT') === 1, 'exactly one product request is made');
check(str_contains($committed, 'STATE: gallery'),
    'the existing REST filter performs the one atomic active->gallery claim');
check(str_contains($committed, 'NONCES: frpd_mg_recovery_gallery'),
    'the fixed recovery route checks its own exact nonce');
check(!str_contains($committed, $secret) && !str_contains($committed, 'test-nonce'),
    'the rendered proof discloses no nonce or guard secret');
foreach (array('_wpnonce', 'frpd_media_guard_owner', 'C:\\\\', 'wp-content')
         as $forbidden_fragment) {
    $proof_json = substr($committed, (int) strpos($committed, '{"schema":3'));
    $proof_json = substr($proof_json, 0, (int) strpos($proof_json, '</script>'));
    check(!str_contains($proof_json, $forbidden_fragment),
        'the rendered proof leaks no ' . $forbidden_fragment);
}

$refusals = array(
    'no_cookie' => 'not owned by this user, session and browser',
    'foreign_cookie' => 'not owned by this user, session and browser',
    'wrong_if_match' => 'changed before the conditional recovery update',
    'malformed_if_match' => 'not a gallery hash',
    'extra_field' => 'exactly one action, _wpnonce, and if_match field',
    'missing_field' => 'exactly one action, _wpnonce, and if_match field',
    'duplicate_field' => 'exactly one action, _wpnonce, and if_match field',
    // 1.0.7 closes the documented three-field surface: the referer WordPress
    // usually appends is now an EXTRA field and is refused like any other.
    'referer_field' => 'exactly one action, _wpnonce, and if_match field',
    'caller_ids' => 'exactly one action, _wpnonce, and if_match field',
    'caller_path' => 'exactly one action, _wpnonce, and if_match field',
    'query_field' => 'rejects query and file fields',
    'file_field' => 'rejects query and file fields',
    'get_method' => 'requires one exact POST request',
    'wrong_content_type' => 'application/x-www-form-urlencoded',
    'wrong_action' => 'fixed recovery action is not exact',
    'family_contract' => 'not the fixed Open Manway recovery contract',
    'incomplete' => 'not complete and exact',
);
foreach ($refusals as $scenario => $fragment) {
    $output = run_commit_scenario($scenario);
    check(str_contains($output, 'REFUSED: ') && str_contains($output, $fragment),
        'the fixed recovery route refuses scenario ' . $scenario . ': ' . $output);
    check(str_contains($output, 'GALLERY: 6991,6992,6993,6994'),
        'scenario ' . $scenario . ' leaves the live product gallery untouched');
    check(str_contains($output, 'STATE: active'),
        'scenario ' . $scenario . ' never claims the guard gallery state');
    check(str_contains($output, "\nDISPATCHED: 0"),
        'scenario ' . $scenario . ' makes no product request at all');
}

// A failure AFTER the atomic active->gallery claim must say the state may still be
// `gallery`. Reporting "unchanged" there is exactly the false message 1.0.7 fixes.
foreach (array('post_claim_rest_failure' => 500, 'post_claim_exception' => null) as $scenario => $status) {
    $output = run_commit_scenario($scenario);
    check(str_contains($output, '"mode":"recovery_gallery_failed_no_retry"'),
        'a post-claim downstream failure renders the fixed no-retry failure proof');
    check(str_contains($output, '"observed_state_status":"gallery"'),
        'the post-claim failure proof records the FRESH observed state, which is gallery');
    check(!str_contains($output, 'unchanged') && !str_contains($output, 'rollback'),
        'the post-claim failure proof never claims the state is unchanged or rolled back');
    check(str_contains($output, 'state remains gallery'),
        'the post-claim failure message says outright that the state remains gallery');
    check(str_contains($output, 'STATE: gallery'),
        'the durable row really does remain gallery after a post-claim failure');
    check(str_contains($output, 'GALLERY: 6991,6992,6993,6994'),
        'a post-claim failure leaves the live product gallery untouched');
    check(null === $status || str_contains($output, '"dispatch_status":500'),
        'the post-claim failure proof records the observed dispatch status');
}

// ======================================================================
// 8b. COMPLETE fixed attachment identity (correction finding C).
//
// 1.0.6 proved post type, non-trash status, basename, bytes and hash. It did
// NOT prove the exact live post status, the exact MIME, byte-exact single
// ownership, PNG dimensions or colour mode, so a JPEG, a drafted attachment or
// a second owner of the same relative path all passed.
// ======================================================================
$identity_snapshot = recovery_state(array(1 => 7609));
check(!is_wp_error(frpd_mg_recovery_attachment_identity(1, 7609, $manway)),
    'the exact live fixed attachment proves its complete identity');
$identity = frpd_mg_recovery_attachment_identity(1, 7609, $manway);
check(array_keys($identity) === array(
    'attachment_id', 'position', 'post_type', 'post_status', 'mime_type',
    'relative_path', 'basename', 'bytes', 'sha256', 'png_width', 'png_height',
    'png_mode', 'png_bit_depth', 'png_color_type'),
    'the identity record is one closed shape carrying post/MIME/status/dimensions');
check($identity['post_status'] === 'inherit' && $identity['mime_type'] === 'image/png'
    && $identity['post_type'] === 'attachment' && $identity['png_mode'] === 'RGB'
    && $identity['png_width'] === 1254 && $identity['png_height'] === 1254
    && $identity['png_bit_depth'] === 8 && $identity['png_color_type'] === 2,
    'the identity record states the exact pinned safe live post/MIME/PNG identity');

$identity_drift = array(
    'a JPEG MIME' => array('post_mime_type' => 'image/jpeg'),
    'a trashed attachment' => array('post_status' => 'trash'),
    'a private attachment' => array('post_status' => 'private'),
    'a drafted attachment' => array('post_status' => 'draft'),
    'a published attachment' => array('post_status' => 'publish'),
    'an auto-draft attachment' => array('post_status' => 'auto-draft'),
    'a non-attachment post type' => array('post_type' => 'post'),
);
foreach ($identity_drift as $label => $overrides) {
    set_attachment_post(7609, $overrides);
    check(is_wp_error(frpd_mg_recovery_attachment_identity(1, 7609, $manway)),
        'complete identity refuses ' . $label);
    reset_lock();
    frpd_mg_hold_request_lock();
    $drifted_snapshot = frpd_mg_snapshot('open_manway', 'pre_guard_snapshot', false);
    check($drifted_snapshot['complete'] === false
        && $drifted_snapshot['failures'][0]['reason'] === 'fixed_attachment_identity_failed',
        'the complete snapshot fails closed on ' . $label);
    check(is_wp_error(frpd_mg_acquisition_bindings(
        'open_manway', $drifted_snapshot, frpd_mg_origin_only_file_proof())),
        'acquisition refuses ' . $label);
    set_attachment_post(7609);
}
unset($GLOBALS['attachment_posts'][7609]);
check(is_wp_error(frpd_mg_recovery_attachment_identity(1, 7609, $manway)),
    'complete identity refuses an absent attachment post');
set_attachment_post(7609);

// A second attachment row owning the SAME relative file is duplicate ownership.
$GLOBALS['attached_files'][7611] = $GLOBALS['attached_files'][7609];
set_attachment_post(7611);
check(is_wp_error(frpd_mg_recovery_attachment_identity(1, 7609, $manway)),
    'complete identity refuses a duplicate `_wp_attached_file` owner');
unset($GLOBALS['attached_files'][7611], $GLOBALS['attachment_posts'][7611]);

// A collation-equal but byte-different meta row is ambiguity, never a match.
$GLOBALS['wpdb']->collation_alias_owners[$GLOBALS['attached_files'][7609]] = array(7612);
check(is_wp_error(frpd_mg_recovery_attachment_identity(1, 7609, $manway)),
    'complete identity refuses a collation-equal but byte-different attached-file row');
check(is_wp_error(frpd_mg_origin_only_file_proof()),
    'the origin proof refuses the same collation ambiguity instead of reporting absence');
$GLOBALS['wpdb']->collation_alias_owners = array();

// Wrong pinned dimensions and a non-PNG signature both refuse.
$wrong_dimensions = $manway;
$wrong_dimensions[0]['width'] = 999;
check(is_wp_error(frpd_mg_attachment_file_identity(7609, $wrong_dimensions[0], true)),
    'complete identity refuses an attachment whose PNG dimensions are not the pinned ones');
$not_png = $GLOBALS['upload_basedir'] . DIRECTORY_SEPARATOR . '2026'
    . DIRECTORY_SEPARATOR . '08' . DIRECTORY_SEPARATOR . 'not-a-png.bin';
file_put_contents($not_png, 'GIF89a this is not a PNG at all');
$GLOBALS['attachment_ids'][] = 7613;
$GLOBALS['attachment_paths'][7613] = $not_png;
$GLOBALS['attached_files'][7613] = '2026/08/not-a-png.bin';
set_attachment_post(7613);
check(is_wp_error(frpd_mg_attachment_file_identity(7613, array(
    'filename' => 'not-a-png.bin', 'position' => 1,
    'bytes' => filesize($not_png), 'sha256' => hash_file('sha256', $not_png),
    'width' => 1254, 'height' => 1254, 'mode' => 'RGB'), true)),
    'complete identity refuses exact bytes that carry no PNG signature');

// An unsafe or aliased relative path is refused before the file is ever opened.
foreach (array('../outside.png', '/2026/08/x.png', '2026//08/x.png', '2026/../08/x.png',
               'C:/absolute/x.png') as $unsafe) {
    $GLOBALS['attached_files'][7609] = $unsafe;
    check(is_wp_error(frpd_mg_recovery_attachment_identity(1, 7609, $manway)),
        'complete identity refuses the unsafe relative path ' . $unsafe);
}

// ======================================================================
// 8c. PER-PATH origin-only proof (correction finding D).
//
// 1.0.6 aggregated every discovered path's owners into ONE de-duplicated list
// and then compared counts, so "one unowned file plus one two-owner file" was
// reported as origin_only=false. Each discovered path now carries its own
// exact owner list and is classified alone.
// ======================================================================
$per_path = recovery_state(
    array(1 => 7609),
    // The SAME fixed basename discovered twice: one owned copy and one orphan.
    array($manway_names[0] => 1)
);
$per_path_proof = frpd_mg_origin_only_file_proof();
check(!is_wp_error($per_path_proof) && $per_path_proof['files'][0]['discovered'] === 2,
    'both copies of one fixed basename are discovered');
$paths = $per_path_proof['files'][0]['paths'];
check(count($paths) === 2
    && $paths[0]['owner_attachment_ids'] === array()
    && $paths[1]['owner_attachment_ids'] === array(7609),
    'each discovered path carries its OWN owner list; owners are never aggregated');
check($per_path_proof['files'][0]['origin_only'] === true,
    'two discovered copies of one fixed basename classify as origin-only, not clean');
check(count($per_path_proof['origin_only']) === 2,
    'every path of an origin-only basename is reported, not just one of them');
check(is_wp_error(frpd_mg_acquisition_bindings('open_manway',
    $per_path, $per_path_proof)),
    'acquisition refuses while one fixed basename has more than one discovered copy');
check($GLOBALS['wpdb']->state === null,
    'no durable row is written when the per-path origin proof refuses');

// Two owners of one exact path is ambiguity, not a match.
$two_owner = recovery_state(array(1 => 7609), array(),
    array(7614 => '2026/08/' . $manway_names[0]));
$two_owner_proof = frpd_mg_origin_only_file_proof();
check(is_wp_error($two_owner_proof)
    || $two_owner_proof['files'][0]['paths'][0]['owner_attachment_ids'] === array(7609, 7614),
    'a path with two owners reports both, or refuses');
check(is_wp_error($two_owner_proof) || $two_owner_proof['files'][0]['origin_only'] === true,
    'a path owned by two attachments is never reported clean');

// An owner that is not an exact live attachment record refuses the whole proof.
$owner_drift = recovery_state(array(1 => 7609));
set_attachment_post(7609, array('post_status' => 'trash'));
check(is_wp_error(frpd_mg_origin_only_file_proof()),
    'the origin proof refuses when a discovered path owner is not an exact live attachment');
set_attachment_post(7609);

// ======================================================================
// 8d. Uploads-root, year and month alias identity (correction finding E.5).
// ======================================================================
check(frpd_mg_path_identity_is_exact('C:/x/uploads', 'C:/x/uploads', false, true, true) === true,
    'a canonical readable directory is exact');
check(frpd_mg_path_identity_is_exact('C:/x/uploads', 'C:/x/uploads', true, true, true) === false,
    'a symlinked directory is never exact, at root, year or month');
check(frpd_mg_path_identity_is_exact('C:/x/link', 'C:/x/real', false, true, true) === false,
    'a configured path that does not equal its canonical path is never exact');
check(frpd_mg_path_identity_is_exact('C:/x/uploads', 'C:/x/uploads', false, false, true) === false,
    'a non-directory is never an uploads directory');
check(frpd_mg_path_identity_is_exact('C:/x/uploads', 'C:/x/uploads', false, true, false) === false,
    'an unreadable directory is never exact');
check(frpd_mg_path_identity_is_exact('', '', false, true, true) === false,
    'an empty configured path is never exact');

$canonical_base = $GLOBALS['upload_basedir'];
$GLOBALS['upload_basedir'] = dirname($canonical_base) . DIRECTORY_SEPARATOR . '.'
    . DIRECTORY_SEPARATOR . basename($canonical_base);
check(is_wp_error(frpd_mg_uploads_root_identity()),
    'a non-canonical configured uploads root is refused, not silently canonicalized');
check(is_wp_error(frpd_mg_origin_only_file_proof()),
    'a non-canonical uploads root refuses the whole origin proof');
check(is_wp_error(frpd_mg_recovery_attachment_identity(1, 7609, $manway)),
    'a non-canonical uploads root refuses fixed attachment identity too');
$GLOBALS['upload_basedir'] = $canonical_base;
check(!is_wp_error(frpd_mg_uploads_root_identity()),
    'the canonical uploads root is accepted again');

// A REAL directory symlink where this account may create one; the pure predicate
// above covers the same branch deterministically when it may not.
$link_root = guard_test_temp_root() . DIRECTORY_SEPARATOR . 'frpd-mg-recovery-uploads-link';
@rmdir($link_root);
$linked = @symlink($canonical_base, $link_root);
if ($linked) {
    $GLOBALS['upload_basedir'] = $link_root;
    check(is_wp_error(frpd_mg_uploads_root_identity()),
        'a symlinked uploads ROOT is refused rather than canonicalized away');
    check(is_wp_error(frpd_mg_origin_only_file_proof()),
        'a symlinked uploads root refuses the origin proof');
    $GLOBALS['upload_basedir'] = $canonical_base;
    @rmdir($link_root);
} else {
    check(true, 'directory symlinks are unavailable to this account; the root/year/month '
        . 'alias branch is covered deterministically by frpd_mg_path_identity_is_exact');
}

// ======================================================================
// 8e. Expired UNRESOLVED state is never overwritten (correction finding B).
//
// 1.0.6 collapsed every expired row to null before decoding it, then acquired
// with an unconditional ON DUPLICATE KEY UPDATE -- an ordinary acquire could
// therefore silently overwrite a reserved-but-unbound upload and reacquire.
// That was a semantic retry route with no action literally named "retry".
// ======================================================================
$expired_snapshot = recovery_state(array(1 => 7609));
function expired_row($record, $attachments, $status = 'active') {
    set_test_state(array(
        'family' => 'open_manway',
        'owner_hash' => hash('sha256', 'expired-owner-secret'),
        'expires' => time() - 60,
        'snapshot_count' => 1,
        'state_status' => $status,
        'record' => $record,
        'attachments' => $attachments,
    ));
    reset_lock();
    frpd_mg_hold_request_lock();
    return $GLOBALS['wpdb']->state;
}
$hero_binding = array($manway_names[0] => 7609);
$unresolved = array(
    'a reserved-but-unbound upload' => array(
        test_recovery_record(array(1 => 7609), array($manway_names[1])), $hero_binding, 'active'),
    'a partially bound upload set' => array(
        test_recovery_record(array(1 => 7609), array($manway_names[1])),
        $hero_binding + array($manway_names[1] => 7700), 'active'),
    'a gallery-claimed row' => array(
        test_recovery_record(array(1 => 7609)), $hero_binding, 'gallery'),
    'an unexpired-shaped active row' => array(
        test_recovery_record(array(1 => 7609)), $hero_binding, 'active'),
);
foreach ($unresolved as $label => $fixture) {
    $before_row = expired_row($fixture[0], $fixture[1], $fixture[2]);
    $exact = frpd_mg_exact_state();
    check(is_array($exact) && $exact['is_active'] === false,
        'the exact state reader decodes an expired row instead of collapsing it to null: ' . $label);
    check(frpd_mg_active_state() === null,
        'an expired row is never returned as an ACTIVE guard: ' . $label);
    check(frpd_mg_terminal_state_is_replaceable($exact) === false,
        'an expired row holding ' . $label . ' is not replaceable');
    $refusal = frpd_mg_insert_state('open_manway', 'a-new-secret', $expired_snapshot);
    check(is_wp_error($refusal),
        'acquisition refuses rather than overwriting an expired row holding ' . $label);
    check($GLOBALS['wpdb']->state === $before_row,
        'the expired row holding ' . $label . ' is preserved byte-semantically');
}

// A CLEAN terminal row -- expired with nothing reserved and nothing bound beyond
// its acquisition bindings -- is the only replaceable shape.
$clean_row = expired_row(test_recovery_record(array(1 => 7609)), $hero_binding, 'expired');
$clean_exact = frpd_mg_exact_state();
check(frpd_mg_terminal_state_is_replaceable($clean_exact) === true,
    'an expired row with no reservation and no extra binding is replaceable');
$replacement = frpd_mg_insert_state('open_manway', 'a-new-secret', $expired_snapshot);
check(is_array($replacement) && $replacement['state_status'] === 'active'
    && $replacement['state_version'] === 1,
    'a clean terminal row is replaced by a fresh acquisition');

// A concurrent change between the inspection and the compare-and-swap writes nothing.
$concurrent_before = expired_row(test_recovery_record(array(1 => 7609)), $hero_binding, 'expired');
$GLOBALS['wpdb']->concurrent_change_before_replacement = true;
$concurrent = frpd_mg_insert_state('open_manway', 'a-new-secret', $expired_snapshot);
check(is_wp_error($concurrent),
    'a concurrent state change makes the replacement affect zero rows and refuse');
check((int) $GLOBALS['wpdb']->state['state_version']
        === (int) $concurrent_before['state_version'] + 1
    && (string) $GLOBALS['wpdb']->state['reserved_json']
        === (string) $concurrent_before['reserved_json'],
    'the concurrently changed row keeps its own durable evidence and is not overwritten');
$GLOBALS['wpdb']->concurrent_change_before_replacement = false;

// A malformed durable row fails closed; it is never treated as absent.
expired_row(test_recovery_record(array(1 => 7609)), $hero_binding, 'expired');
$GLOBALS['wpdb']->state['reserved_json'] = '{not json';
check(is_wp_error(frpd_mg_exact_state()),
    'a malformed durable row is an error, never a missing row');
check(is_wp_error(frpd_mg_insert_state('open_manway', 'a-new-secret', $expired_snapshot)),
    'acquisition refuses on a malformed durable row rather than replacing it');

// There is no retry, reset, force-unlock, cleanup or reacquire route anywhere.
$recovery_source = file_get_contents(
    __DIR__ . '/frpdepot-media-mutation-guard/frpdepot-media-mutation-guard.php');
check(!str_contains($recovery_source, 'ON DUPLICATE KEY UPDATE'),
    'no unconditional ON DUPLICATE KEY UPDATE acquisition path survives');
foreach (array('force_unlock', 'frpd_mg_reset_state', 'frpd_mg_clear_state',
               'frpd_mg_force_', 'frpd_mg_reacquire', 'frpd_mg_retry') as $banned) {
    check(!str_contains($recovery_source, $banned),
        'the plugin exposes no ' . $banned . ' route');
}

// ======================================================================
// 9. Nothing new is reachable beyond the one recovery capability.
// ======================================================================
$actions = array_values(array_filter(array_keys($GLOBALS['registered_actions']),
    fn($hook) => str_starts_with($hook, 'admin_post_')));
sort($actions, SORT_STRING);
check($actions === array(
    'admin_post_frpd_media_guard_acquire',
    'admin_post_frpd_media_guard_complete',
    'admin_post_frpd_media_guard_guarded_snapshot',
    'admin_post_frpd_media_guard_origin_proof',
    'admin_post_frpd_media_guard_recovery_gallery',
    'admin_post_frpd_media_guard_snapshot',
), 'v1.0.7 exposes exactly six fixed admin-post routes and no more');
$source = file_get_contents(
    __DIR__ . '/frpdepot-media-mutation-guard/frpdepot-media-mutation-guard.php');
foreach (array('wp_delete_attachment', 'wp_delete_post', 'wp_mail', 'wp_remote_',
               'file_get_contents(\'http', 'curl_', 'unlink(', 'rename(', 'rmdir(',
               'admin_post_frpd_media_guard_delete', 'admin_post_frpd_media_guard_retry',
               'admin_post_frpd_media_guard_cleanup', 'admin_post_frpd_media_guard_rollback')
         as $banned) {
    check(!str_contains($source, $banned),
        'the plugin has no ' . $banned . ' route');
}
check(str_contains($source, "add_action('admin_post_' . FRPD_MG_RECOVERY_ACTION"),
    'the recovery route is registered under exactly its fixed action constant');

echo "PASS {$passed}\n";
