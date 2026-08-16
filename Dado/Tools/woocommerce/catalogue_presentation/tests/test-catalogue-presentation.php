<?php
/** Offline tests for the real Automatic Catalogue Presentation plugin. */
declare( strict_types = 1 );
define( 'ABSPATH', __DIR__ . '/' );

$GLOBALS['acp_hooks'] = array();
$GLOBALS['acp_admin'] = false;
$GLOBALS['acp_products'] = array();
$GLOBALS['acp_terms'] = array();
$GLOBALS['acp_term_assignments'] = array();
$GLOBALS['acp_last_query'] = null;

function add_filter( $hook, $callback, $priority = 10, $args = 1 ) {
	$GLOBALS['acp_hooks'][] = compact( 'hook', 'callback', 'priority', 'args' ) + array( 'type' => 'filter' );
	return true;
}
function add_action( $hook, $callback, $priority = 10, $args = 1 ) {
	$GLOBALS['acp_hooks'][] = compact( 'hook', 'callback', 'priority', 'args' ) + array( 'type' => 'action' );
	return true;
}
function is_admin() { return (bool) $GLOBALS['acp_admin']; }
function wp_doing_ajax() { return false; }
function is_feed() { return false; }
function is_preview() { return false; }
function is_product() { return true; }
function is_wp_error( $value ) { return $value instanceof WP_Error; }
function wp_parse_url( $url, $component = -1 ) { return parse_url( $url, $component ); }
class WP_Error {}

function get_term( $id, $taxonomy ) {
	if ( 'product_cat' !== $taxonomy || ! isset( $GLOBALS['acp_terms'][ (int) $id ] ) ) {
		return new WP_Error();
	}
	return clone $GLOBALS['acp_terms'][ (int) $id ];
}
function wp_get_post_terms( $product_id, $taxonomy ) {
	if ( 'product_cat' !== $taxonomy ) { return new WP_Error(); }
	$result = array();
	foreach ( $GLOBALS['acp_term_assignments'][ (int) $product_id ] ?? array() as $term_id ) {
		$result[] = get_term( $term_id, $taxonomy );
	}
	return $result;
}
function get_posts( $args ) {
	$GLOBALS['acp_last_query'] = $args;
	$ids = array();
	foreach ( $GLOBALS['acp_products'] as $id => $product ) {
		if ( 'publish' !== $product['status'] || $product['password'] || $product['exclude_catalog'] ) {
			continue;
		}
		$ids[] = (int) $id;
	}
	// Deliberate duplicate proves plugin deduplication is not delegated to the query.
	if ( $ids ) { $ids[] = $ids[0]; }
	return $ids;
}
function get_term_link( $term, $taxonomy ) {
	return 'https://frpdepots.com/product-category/' . $term->slug . '/';
}
function get_permalink( $id ) { return $GLOBALS['acp_products'][ (int) $id ]['url']; }
function get_the_title( $id ) { return $GLOBALS['acp_products'][ (int) $id ]['name']; }

require __DIR__ . '/../frpdepot-automatic-catalogue-presentation/frpdepot-automatic-catalogue-presentation.php';

$passes = 0;
$failures = 0;
function check( $condition, $label ) {
	global $passes, $failures;
	if ( $condition ) { $passes++; return; }
	$failures++;
	fwrite( STDERR, "FAIL: {$label}\n" );
}
function term( int $id, string $name, string $slug, int $parent ): stdClass {
	return (object) array( 'id' => $id, 'term_id' => $id, 'name' => $name,
		'slug' => $slug, 'parent' => $parent );
}
function nav( int $id, int $parent, string $title, string $url, int $order ): stdClass {
	return (object) array( 'ID' => $id, 'menu_item_parent' => $parent, 'title' => $title,
		'url' => $url, 'menu_order' => $order, 'classes' => array( 'menu-item' ) );
}
function generated_ids( array $items, string $prefix ): array {
	$result = array();
	foreach ( $items as $item ) {
		foreach ( $item->classes ?? array() as $class ) {
			$suffix = 0 === strpos( $class, $prefix ) ? substr( $class, strlen( $prefix ) ) : '';
			if ( '' !== $suffix && ctype_digit( $suffix ) ) { $result[] = (int) $suffix; }
		}
	}
	sort( $result );
	return $result;
}
function generated_projection( array $items ): array {
	$categories = array();
	$products = array();
	foreach ( $items as $item ) {
		$category_ids = generated_ids( array( $item ), 'frpdepot-acp-category-' );
		$product_ids = generated_ids( array( $item ), 'frpdepot-acp-product-' );
		if ( 1 === count( $category_ids ) ) {
			$categories[ (int) $item->ID ] = array( 'id' => $category_ids[0], 'products' => array() );
		}
		if ( 1 === count( $product_ids ) ) {
			$products[] = array( 'id' => $product_ids[0], 'parent' => (int) $item->menu_item_parent );
		}
	}
	foreach ( $products as $product ) {
		if ( isset( $categories[ $product['parent'] ] ) ) {
			$categories[ $product['parent'] ]['products'][] = $product['id'];
		}
	}
	$result = array_values( $categories );
	foreach ( $result as &$category ) { sort( $category['products'] ); }
	unset( $category );
	usort( $result, function ( $left, $right ) { return $left['id'] <=> $right['id']; } );
	return $result;
}
function generated_parent_classes_are_valid( array $items ): bool {
	$by_id = array();
	foreach ( $items as $item ) { $by_id[ (int) $item->ID ] = $item; }
	foreach ( $items as $item ) {
		$product_ids = generated_ids( array( $item ), 'frpdepot-acp-product-' );
		if ( empty( $product_ids ) ) { continue; }
		$parent_ids = generated_ids( array( $item ), 'frpdepot-acp-category-parent-' );
		$menu_parent = (int) $item->menu_item_parent;
		$category_ids = isset( $by_id[ $menu_parent ] )
			? generated_ids( array( $by_id[ $menu_parent ] ), 'frpdepot-acp-category-' ) : array();
		if ( 1 !== count( $product_ids ) || 1 !== count( $parent_ids )
			|| $parent_ids !== $category_ids ) { return false; }
	}
	return true;
}
function generated_nav_post_parents_are_valid( array $items ): bool {
	foreach ( $items as $item ) {
		$is_generated = false;
		foreach ( $item->classes ?? array() as $class ) {
			if ( 0 === strpos( $class, 'frpdepot-acp-' ) ) { $is_generated = true; break; }
		}
		if ( $is_generated && ( ! property_exists( $item, 'post_parent' )
			|| 0 !== (int) $item->post_parent ) ) { return false; }
	}
	return true;
}

$GLOBALS['acp_terms'] = array(
	17 => term( 17, 'Uncategorized', 'uncategorized', 0 ),
	58 => term( 58, 'Piping & Fluid Handling', 'piping-fluid-handling', 0 ),
	44 => term( 44, 'Pipe', 'pipe', 58 ),
	45 => term( 45, 'Elbows', 'elbows', 58 ),
	57 => term( 57, 'Flanges', 'flanges', 58 ),
	60 => term( 60, 'Manways', 'manways', 0 ),
	61 => term( 61, 'Couplings', 'couplings', 58 ),
	62 => term( 62, 'Backing Rings', 'backing-rings', 57 ),
	99 => term( 99, 'Speculative', 'speculative', 0 ),
);
function product_row( string $name, string $slug, string $status = 'publish', bool $hidden = false,
	bool $password = false ): array {
	return array( 'name' => $name, 'url' => 'https://frpdepots.com/product/' . $slug . '/',
		'status' => $status, 'exclude_catalog' => $hidden, 'password' => $password );
}
$GLOBALS['acp_products'] = array(
	1 => product_row( 'FRP Pipe', 'frp-fw-pipe' ),
	2 => product_row( 'FRP Elbow', 'frp-elbow-90' ),
	3 => product_row( 'FRP Stub Flange', 'frp-stub-flange' ),
	4 => product_row( 'FRP Manway', 'frp-manway' ),
	5 => product_row( 'FRP Manway Cover', 'frp-manway-cover' ),
	6 => product_row( 'FNPT Coupling', 'fnpt-coupling-threaded-on-both-ends' ),
	7 => product_row( 'Draft Product', 'draft-product', 'draft' ),
	8 => product_row( 'Private Product', 'private-product', 'private' ),
	9 => product_row( 'Hidden Product', 'hidden-product', 'publish', true ),
	10 => product_row( 'Uncategorized Product', 'uncategorized-product' ),
	11 => product_row( 'Outside Product', 'outside-product' ),
	12 => product_row( 'Backing Ring', 'backing-ring' ),
	13 => product_row( 'Password Product', 'password-product', 'publish', false, true ),
);
$GLOBALS['acp_term_assignments'] = array(
	1 => array( 44 ), 2 => array( 45 ), 3 => array( 58, 57, 17 ), 4 => array( 60 ),
	5 => array( 60 ), 6 => array( 58, 61 ), 7 => array( 44 ), 8 => array( 44 ),
	9 => array( 44 ), 10 => array( 17 ), 11 => array( 99 ), 12 => array( 58, 57, 62 ),
	13 => array( 44 ),
);

/* Hook and source-scope invariants. */
$hook_names = array_column( $GLOBALS['acp_hooks'], 'hook' );
foreach ( array( 'wp_get_nav_menu_items', 'template_redirect', 'transition_post_status',
	'save_post_product', 'set_object_terms', 'clean_object_term_cache',
	'woocommerce_product_set_visibility' ) as $hook ) {
	check( in_array( $hook, $hook_names, true ), "refresh/presentation hook registered: {$hook}" );
}
$source = (string) file_get_contents( __DIR__ . '/../frpdepot-automatic-catalogue-presentation/frpdepot-automatic-catalogue-presentation.php' );
foreach ( array( 'wp_update_post(', 'wp_insert_post(', 'update_post_meta(', 'update_option(',
	'add_option(', 'wp_set_object_terms(', 'wp_create_term(', 'wp_update_nav_menu_item(',
	'WC()->cart', 'woocommerce_checkout_', 'woocommerce_new_order' ) as $forbidden ) {
	check( false === strpos( $source, $forbidden ), "no persistent/business write route: {$forbidden}" );
}
check( false === strpos( $source, 'set_transient(' ), 'no persistent transient cache' );
check( false === strpos( $source, 'register_activation_hook' ), 'no activation-time writes' );

/* Query, grouping, exclusion, deepest category, future categories and dedupe. */
frpdepot_acp_invalidate_request_cache();
$groups = frpdepot_acp_catalogue_groups();
check( 'product' === $GLOBALS['acp_last_query']['post_type'], 'query is product-only' );
check( 'publish' === $GLOBALS['acp_last_query']['post_status'], 'query is publish-only' );
check( false === $GLOBALS['acp_last_query']['has_password'], 'query excludes password products' );
check( array( 'exclude-from-catalog' ) === $GLOBALS['acp_last_query']['tax_query'][0]['terms'],
	'query excludes WooCommerce catalog-hidden items' );
$category_ids = array_column( $groups, 'id' ); sort( $category_ids );
check( array( 44, 45, 57, 60, 61, 62 ) === $category_ids,
	'deepest approved categories include real future Couplings/Backing Rings and omit empty/root fallbacks' );
$all_product_ids = array();
foreach ( $groups as $group ) { $all_product_ids = array_merge( $all_product_ids, array_column( $group['products'], 'id' ) ); }
sort( $all_product_ids );
check( array( 1, 2, 3, 4, 5, 6, 12 ) === $all_product_ids,
	'only published catalog-visible approved products remain and duplicate query id is removed' );
check( count( $all_product_ids ) === count( array_unique( $all_product_ids ) ), 'products are deduplicated globally' );
$map = array(); foreach ( $groups as $group ) { foreach ( $group['products'] as $product ) { $map[ $product['id'] ] = $group['id']; } }
check( 57 === $map[3] && 61 === $map[6] && 62 === $map[12], 'deepest assigned approved category wins' );
check( 'https://frpdepots.com/product/frp-fw-pipe/' === $groups[ array_search( 44, array_column( $groups, 'id' ), true ) ]['products'][0]['url'],
	'existing product URL is reused unchanged' );

/* Automatic refresh after publish/unpublish/visibility changes: request cache only. */
$GLOBALS['acp_products'][1]['status'] = 'draft';
$stale = frpdepot_acp_catalogue_groups();
check( in_array( 1, array_merge( ...array_map( function ( $g ) { return array_column( $g['products'], 'id' ); }, $stale ) ), true ),
	'request memo is stable until a state hook invalidates it' );
frpdepot_acp_invalidate_request_cache();
$refreshed = frpdepot_acp_catalogue_groups();
$refreshed_ids = array(); foreach ( $refreshed as $group ) { $refreshed_ids = array_merge( $refreshed_ids, array_column( $group['products'], 'id' ) ); }
check( ! in_array( 1, $refreshed_ids, true ), 'unpublish is visible immediately after in-memory invalidation' );
$GLOBALS['acp_products'][1]['status'] = 'publish';
frpdepot_acp_invalidate_request_cache();

/* Main desktop/Divi mobile source/footer all derive from the same projection. */
$main = array(
	nav( 36, 0, 'Shop Online', 'https://frpdepots.com/products/', 1 ),
	nav( 1781, 36, 'Old Pipe', 'https://frpdepots.com/product/old/', 2 ),
	nav( 1782, 1781, 'Old Grandchild', 'https://frpdepots.com/product/old-child/', 3 ),
	nav( 34, 0, 'Resources', 'https://frpdepots.com/resources/', 4 ),
);
$desktop = frpdepot_acp_sync_menu_items( $main, (object) array( 'slug' => 'main' ), null );
$mobile_source = frpdepot_acp_sync_menu_items( $main, (object) array( 'slug' => 'main' ), null );
$footer = frpdepot_acp_sync_menu_items( array( nav( 999, 0, 'Stale', 'https://invalid/', 1 ) ),
	(object) array( 'slug' => 'product-categories' ), null );
foreach ( array( $desktop, $mobile_source, $footer ) as $surface ) {
	check( array( 44, 45, 57, 60, 61, 62 ) === generated_ids( $surface, 'frpdepot-acp-category-' ),
		'surface receives identical generated category ids' );
	check( array( 1, 2, 3, 4, 5, 6, 12 ) === generated_ids( $surface, 'frpdepot-acp-product-' ),
		'surface receives identical generated product ids' );
	check( generated_parent_classes_are_valid( $surface ),
		'surface product parent metadata matches its generated category group' );
	check( generated_nav_post_parents_are_valid( $surface ),
		'synthetic nav items expose the zero nav-post parent required by WordPress walkers' );
}
check( generated_projection( $desktop ) === generated_projection( $mobile_source )
	&& generated_projection( $desktop ) === generated_projection( $footer ),
	'all surfaces receive identical category/product grouping' );
$desktop_shop = array_values( array_filter( $desktop, function ( $item ) { return 36 === (int) $item->ID; } ) );
check( 1 === count( $desktop_shop ) && 'Shop All' === $desktop_shop[0]->title
	&& '/products/' === frpdepot_acp_menu_item_path( $desktop_shop[0] ),
	'legacy stored catalogue root is projected as exact Shop All at permanent /products/ path' );
check( 'Shop Online' === $main[0]->title,
	'read-time Shop All projection does not mutate the stored/source menu object' );
$desktop_ids = array_column( $desktop, 'ID' );
check( in_array( 36, $desktop_ids, true ) && in_array( 34, $desktop_ids, true ), 'main root and unrelated menu items are preserved' );
check( ! in_array( 1781, $desktop_ids, true ) && ! in_array( 1782, $desktop_ids, true ), 'all stale Shop descendants are projected away only at read time' );
check( ! in_array( 999, array_column( $footer, 'ID' ), true ), 'footer is synchronized rather than merged with stale catalogue items' );
$ambiguous = array( nav( 1, 0, 'Shop Online', 'https://frpdepots.com/products/', 1 ),
	nav( 2, 0, 'Shop All', 'https://frpdepots.com/products/', 2 ) );
check( $ambiguous === frpdepot_acp_sync_menu_items( $ambiguous, (object) array( 'slug' => 'main' ), null ),
	'ambiguous exact /products/ root fails closed unchanged regardless of title' );
$GLOBALS['acp_admin'] = true;
check( $main === frpdepot_acp_sync_menu_items( $main, (object) array( 'slug' => 'main' ), null ),
	'admin menu reads are untouched' );
$GLOBALS['acp_admin'] = false;

/* A genuinely empty public catalogue projects no stale descendants on any surface. */
$saved_products = $GLOBALS['acp_products'];
foreach ( $GLOBALS['acp_products'] as &$product ) { $product['status'] = 'draft'; }
unset( $product );
frpdepot_acp_invalidate_request_cache();
$empty_main = frpdepot_acp_sync_menu_items( $main, (object) array( 'slug' => 'main' ), null );
$empty_footer = frpdepot_acp_sync_menu_items( array( nav( 999, 0, 'Stale', 'https://invalid/', 1 ) ),
	(object) array( 'slug' => 'product-categories' ), null );
check( array( 36, 34 ) === array_column( $empty_main, 'ID' ),
	'empty public catalogue removes stale Shop descendants but preserves main roots' );
check( array() === $empty_footer, 'empty public catalogue removes stale footer catalogue items' );
check( ! in_array( 'menu-item-has-children', $empty_main[0]->classes, true ),
	'empty Shop root is not marked as having children' );
$GLOBALS['acp_products'] = $saved_products;
frpdepot_acp_invalidate_request_cache();

/* Exact, atomic Divi guide transformation and fail-closed ambiguity handling. */
$derakane = '<div class="et_pb_blurb_0_tb_body et_pb_blurb"><div><h4 class="et_pb_module_header">Derakane Resin</h4><div class="et_pb_blurb_description"><p>Chemical Resistance Guide</p></div></div></div>';
$hetron = '<div class="et_pb_blurb_1_tb_body et_pb_blurb"><div><h4 class="et_pb_module_header">Hetron Resin</h4><div class="et_pb_blurb_description"><p>Chemical Resistance Guide</p></div></div></div>';
$derakane_link = '{"class":"et-db #et-boc .et-l .et_pb_blurb_0_tb_body","url":"https://frpdepots.com/derakane-resin-selection-guide/","target":"_blank"}';
$hetron_link = '{"class":"et-db #et-boc .et-l .et_pb_blurb_1_tb_body","url":"https://frpdepots.com/hetron-cr-guide-2007_ineos/","target":"_blank"}';
$other_link = '{"class":"other","url":"/products","target":"_self"}';
$fixture = '<html><body>' . $derakane . $hetron . FRPDEPOT_ACP_INLINE_CTA
	. '<script>var diviElementLinkData=[' . $other_link . ',' . $derakane_link . '];</script></body></html>';
$changed = frpdepot_acp_transform_product_html( $fixture );
check( $changed !== $fixture, 'exact shared Divi fixture transforms' );
check( false === strpos( $changed, $hetron ) && false === strpos( $changed, FRPDEPOT_ACP_HETRON_URL ),
	'exact Hetron guide card and link are removed' );
check( 0 === substr_count( $changed, FRPDEPOT_ACP_DERAKANE_OLD_URL )
	&& 1 === substr_count( $changed, FRPDEPOT_ACP_DERAKANE_NEW_URL ),
	'exact Derakane card is repointed once' );
check( 1 === substr_count( $changed, FRPDEPOT_ACP_INLINE_CTA ), 'inline Derakane CTA is byte-preserved once' );
check( false !== strpos( $changed, $other_link ), 'unrelated Divi link data is preserved' );
$changed_derakane_blurb = str_replace(
	$derakane,
	str_replace( 'Chemical Resistance Guide', 'Changed Guide', $derakane ),
	$fixture
);
check( $changed_derakane_blurb === frpdepot_acp_transform_product_html( $changed_derakane_blurb ),
	'changed exact Derakane blurb marker fails closed atomically' );
$changed_hetron_blurb = str_replace(
	$hetron,
	str_replace( 'Chemical Resistance Guide', 'Changed Guide', $hetron ),
	$fixture
);
check( $changed_hetron_blurb === frpdepot_acp_transform_product_html( $changed_hetron_blurb ),
	'changed exact Hetron blurb marker fails closed atomically' );
$duplicate_semantic_marker = str_replace(
	$hetron,
	str_replace( '</h4>', '</h4><h4 class="et_pb_module_header">Hetron Resin</h4>', $hetron ),
	$fixture
);
check( $duplicate_semantic_marker === frpdepot_acp_transform_product_html( $duplicate_semantic_marker ),
	'duplicate exact semantic marker inside one card fails closed atomically' );
$preexisting_new_url = $fixture . FRPDEPOT_ACP_DERAKANE_NEW_URL;
check( $preexisting_new_url === frpdepot_acp_transform_product_html( $preexisting_new_url ),
	'pre-existing new Derakane URL fails closed atomically' );
check( $fixture . $derakane === frpdepot_acp_transform_product_html( $fixture . $derakane ),
	'duplicate Derakane module fails closed atomically' );
check( $fixture . $hetron === frpdepot_acp_transform_product_html( $fixture . $hetron ),
	'duplicate Hetron module fails closed atomically' );
$unexpected_hetron_link = str_replace( $derakane_link, $derakane_link . ',' . $hetron_link, $fixture );
check( $unexpected_hetron_link === frpdepot_acp_transform_product_html( $unexpected_hetron_link ),
	'unexpected retired Hetron link marker fails closed atomically' );
$duplicate_derakane_link = str_replace( $derakane_link, $derakane_link . ',' . $derakane_link, $fixture );
check( $duplicate_derakane_link === frpdepot_acp_transform_product_html( $duplicate_derakane_link ),
	'duplicate exact Derakane link marker fails closed atomically' );
$duplicate_cta = $fixture . FRPDEPOT_ACP_INLINE_CTA;
check( $duplicate_cta === frpdepot_acp_transform_product_html( $duplicate_cta ),
	'duplicate inline CTA fails closed atomically' );
$changed_cta = str_replace( 'Click here to find out', 'Find out here', $fixture );
check( $changed_cta === frpdepot_acp_transform_product_html( $changed_cta ),
	'changed inline CTA fails closed atomically' );
$nested_cards = '<div class="et_pb_blurb_0_tb_body et_pb_blurb"><h4 class="et_pb_module_header">Derakane Resin</h4>'
	. '<div class="et_pb_blurb_description"><p>Chemical Resistance Guide</p></div>' . $hetron . '</div>';
$overlapping_modules = str_replace( $derakane . $hetron, $nested_cards, $fixture );
check( $overlapping_modules === frpdepot_acp_transform_product_html( $overlapping_modules ),
	'overlapping exact module ranges fail closed atomically' );
check( str_replace( FRPDEPOT_ACP_INLINE_CTA, '', $fixture ) === frpdepot_acp_transform_product_html( str_replace( FRPDEPOT_ACP_INLINE_CTA, '', $fixture ) ),
	'missing inline CTA fails closed without partial transformation' );
check( '' === frpdepot_acp_transform_product_html( '' ), 'empty output stays empty' );

printf( "PASS: %d checks\n", $passes );
if ( $failures ) {
	fwrite( STDERR, "FAIL: {$failures} checks\n" );
	exit( 1 );
}
printf( "ALL CATALOGUE PRESENTATION PHP TESTS PASSED\n" );
