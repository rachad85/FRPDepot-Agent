<?php
/**
 * Plugin Name: FRP Depot Automatic Catalogue Presentation
 * Description: Keeps the public FRP Depot catalogue navigation and exact shared Divi product-guide presentation synchronized with the live, public WooCommerce catalogue.
 * Version:     1.0.4
 * Author:      FRP Depot
 * License:     GPL-2.0-or-later
 * Requires PHP: 7.4
 *
 * Read-time presentation only. This plugin creates no admin page and persists no
 * product, category, menu, post, option, theme, order, cart or customer changes.
 * Product/category permalinks are read and reused; they are never rewritten.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

const FRPDEPOT_ACP_VERSION = '1.0.4';
const FRPDEPOT_ACP_MAIN_MENU_SLUG = 'main';
const FRPDEPOT_ACP_FOOTER_MENU_SLUG = 'product-categories';
const FRPDEPOT_ACP_SHOP_TITLE = 'Shop All';
const FRPDEPOT_ACP_SHOP_PATH = '/products/';
const FRPDEPOT_ACP_UNCATEGORIZED_ID = 17;
const FRPDEPOT_ACP_UNCATEGORIZED_SLUG = 'uncategorized';
const FRPDEPOT_ACP_HETRON_MODULE_CLASS = 'et_pb_blurb_1_tb_body';
const FRPDEPOT_ACP_DERAKANE_MODULE_CLASS = 'et_pb_blurb_0_tb_body';
const FRPDEPOT_ACP_HETRON_URL = 'https://frpdepots.com/hetron-cr-guide-2007_ineos/';
const FRPDEPOT_ACP_DERAKANE_OLD_URL = 'https://frpdepots.com/derakane-resin-selection-guide/';
const FRPDEPOT_ACP_DERAKANE_NEW_URL = 'https://frpdepots.com/derakane-resin-resistance-search/';
const FRPDEPOT_ACP_INLINE_CTA = '<h3>Not sure what Derakane Resin to select? <a href="/derakane-resin-resistance-search/" target="_blank" rel="noopener">Click here to find out</a>.</h3>';

/**
 * Existing category roots approved for public catalogue presentation.
 *
 * Descendants are approved by ancestry, so a real future Couplings or Backing
 * Rings category beneath Piping & Fluid Handling appears automatically once it
 * contains a published, catalog-visible product. Empty/speculative categories
 * never appear because products, not terms, drive the result.
 */
const FRPDEPOT_ACP_APPROVED_CATEGORY_ROOTS = array(
	58 => 'piping-fluid-handling',
	60 => 'manways',
);

/** Public read-time work is disabled for every administrative/non-page context. */
function frpdepot_acp_is_public_request() {
	if ( function_exists( 'is_admin' ) && is_admin() ) {
		return false;
	}
	if ( defined( 'REST_REQUEST' ) && REST_REQUEST ) {
		return false;
	}
	if ( function_exists( 'wp_doing_ajax' ) && wp_doing_ajax() ) {
		return false;
	}
	if ( function_exists( 'is_feed' ) && is_feed() ) {
		return false;
	}
	if ( function_exists( 'is_preview' ) && is_preview() ) {
		return false;
	}
	return true;
}

/** Clear the request-local catalogue projection after a same-request state event. */
function frpdepot_acp_invalidate_request_cache() {
	unset( $GLOBALS['frpdepot_acp_catalogue_groups'] );
}

/** Fetch one product category term safely. */
function frpdepot_acp_get_category( $term_id ) {
	$term = get_term( (int) $term_id, 'product_cat' );
	if ( ! is_object( $term ) || ( function_exists( 'is_wp_error' ) && is_wp_error( $term ) ) ) {
		return null;
	}
	if ( ! isset( $term->term_id, $term->slug, $term->parent ) ) {
		return null;
	}
	return $term;
}

/**
 * Return the assigned term's depth beneath an approved root, or false.
 *
 * The root's ID and slug must both match. Cycles, missing parents, Uncategorized
 * and paths outside the two approved roots fail closed.
 */
function frpdepot_acp_approved_depth( $term ) {
	$seen  = array();
	$depth = 0;
	while ( is_object( $term ) && isset( $term->term_id, $term->slug, $term->parent ) ) {
		$term_id = (int) $term->term_id;
		$slug    = (string) $term->slug;
		if ( $term_id <= 0 || isset( $seen[ $term_id ] ) || count( $seen ) > 32 ) {
			return false;
		}
		$seen[ $term_id ] = true;
		if ( FRPDEPOT_ACP_UNCATEGORIZED_ID === $term_id || FRPDEPOT_ACP_UNCATEGORIZED_SLUG === $slug ) {
			return false;
		}
		if ( isset( FRPDEPOT_ACP_APPROVED_CATEGORY_ROOTS[ $term_id ] ) ) {
			return FRPDEPOT_ACP_APPROVED_CATEGORY_ROOTS[ $term_id ] === $slug ? $depth : false;
		}
		$parent_id = (int) $term->parent;
		if ( $parent_id <= 0 ) {
			return false;
		}
		$term = frpdepot_acp_get_category( $parent_id );
		$depth++;
	}
	return false;
}

/** Choose one deterministic deepest approved assigned category for a product. */
function frpdepot_acp_deepest_category( $product_id ) {
	$terms = wp_get_post_terms( (int) $product_id, 'product_cat' );
	if ( ! is_array( $terms ) || ( function_exists( 'is_wp_error' ) && is_wp_error( $terms ) ) ) {
		return null;
	}
	$candidates = array();
	foreach ( $terms as $term ) {
		$depth = frpdepot_acp_approved_depth( $term );
		if ( false === $depth ) {
			continue;
		}
		$candidates[] = array( 'depth' => (int) $depth, 'term' => $term );
	}
	if ( empty( $candidates ) ) {
		return null;
	}
	usort(
		$candidates,
		function ( $left, $right ) {
			if ( $left['depth'] !== $right['depth'] ) {
				return $right['depth'] <=> $left['depth'];
			}
			$by_name = strnatcasecmp( (string) $left['term']->name, (string) $right['term']->name );
			return 0 !== $by_name ? $by_name : ( (int) $left['term']->term_id <=> (int) $right['term']->term_id );
		}
	);
	return $candidates[0]['term'];
}

/**
 * Build the catalogue projection directly from current public WooCommerce data.
 *
 * There is no persistent cache: each public request starts from a fresh query.
 * A request-local memo makes the header, its mobile clone and footer use the same
 * projection. Published status, no password, and absence of WooCommerce's
 * exclude-from-catalog visibility term are enforced in the query itself.
 */
function frpdepot_acp_catalogue_groups() {
	if ( isset( $GLOBALS['frpdepot_acp_catalogue_groups'] ) ) {
		return $GLOBALS['frpdepot_acp_catalogue_groups'];
	}

	$product_ids = get_posts(
		array(
			'post_type'      => 'product',
			'post_status'    => 'publish',
			'has_password'   => false,
			'numberposts'    => -1,
			'fields'         => 'ids',
			'orderby'        => array( 'menu_order' => 'ASC', 'title' => 'ASC', 'ID' => 'ASC' ),
			'order'          => 'ASC',
			'tax_query'      => array(
				array(
					'taxonomy' => 'product_visibility',
					'field'    => 'name',
					'terms'    => array( 'exclude-from-catalog' ),
					'operator' => 'NOT IN',
				),
			),
		)
	);

	$groups   = array();
	$products = array();
	foreach ( is_array( $product_ids ) ? $product_ids : array() as $product_id ) {
		$product_id = (int) $product_id;
		if ( $product_id <= 0 || isset( $products[ $product_id ] ) ) {
			continue;
		}
		$category = frpdepot_acp_deepest_category( $product_id );
		if ( null === $category ) {
			continue;
		}
		$category_id = (int) $category->term_id;
		$category_url = get_term_link( $category, 'product_cat' );
		$product_url  = get_permalink( $product_id );
		if ( ( function_exists( 'is_wp_error' ) && is_wp_error( $category_url ) )
			|| ! is_string( $category_url ) || '' === $category_url
			|| ! is_string( $product_url ) || '' === $product_url ) {
			continue;
		}
		if ( ! isset( $groups[ $category_id ] ) ) {
			$groups[ $category_id ] = array(
				'id'       => $category_id,
				'name'     => (string) $category->name,
				'url'      => $category_url,
				'products' => array(),
			);
		}
		$groups[ $category_id ]['products'][] = array(
			'id'    => $product_id,
			'name'  => (string) get_the_title( $product_id ),
			'url'   => $product_url,
		);
		$products[ $product_id ] = true;
	}

	foreach ( $groups as &$group ) {
		usort(
			$group['products'],
			function ( $left, $right ) {
				$by_name = strnatcasecmp( $left['name'], $right['name'] );
				return 0 !== $by_name ? $by_name : ( $left['id'] <=> $right['id'] );
			}
		);
	}
	unset( $group );
	$groups = array_values( $groups );
	usort(
		$groups,
		function ( $left, $right ) {
			$by_name = strnatcasecmp( $left['name'], $right['name'] );
			return 0 !== $by_name ? $by_name : ( $left['id'] <=> $right['id'] );
		}
	);

	$GLOBALS['frpdepot_acp_catalogue_groups'] = $groups;
	return $groups;
}

/** Resolve the guaranteed menu-object slug supplied by wp_get_nav_menu_items. */
function frpdepot_acp_menu_slug( $menu ) {
	return is_object( $menu ) && isset( $menu->slug ) ? (string) $menu->slug : '';
}

/** Exact permanent path match used only to identify the existing catalogue root. */
function frpdepot_acp_menu_item_path( $item ) {
	if ( ! is_object( $item ) || ! isset( $item->url ) ) {
		return '';
	}
	$path = function_exists( 'wp_parse_url' ) ? wp_parse_url( (string) $item->url, PHP_URL_PATH ) : parse_url( (string) $item->url, PHP_URL_PATH );
	return is_string( $path ) ? $path : '';
}

/** Construct one request-only nav item; it is never inserted into WordPress. */
function frpdepot_acp_nav_item( $id, $parent, $order, $object, $object_id, $title, $url, $classes ) {
	$item                    = new stdClass();
	$item->ID                = (int) $id;
	$item->db_id             = (int) $id;
	// WordPress nav walkers read the underlying nav-post parent separately from
	// menu_item_parent. Real wp_get_nav_menu_items() objects always expose it.
	$item->post_parent       = 0;
	$item->menu_item_parent  = (int) $parent;
	$item->object_id         = (int) $object_id;
	$item->object            = (string) $object;
	$item->type              = 'product_cat' === $object ? 'taxonomy' : 'post_type';
	$item->type_label        = 'product_cat' === $object ? 'Product category' : 'Product';
	$item->title             = (string) $title;
	$item->url               = (string) $url;
	$item->target            = '';
	$item->attr_title        = '';
	$item->description       = '';
	$item->xfn               = '';
	$item->status            = '';
	$item->menu_order        = (int) $order;
	$item->classes           = array_values( $classes );
	return $item;
}

/** Create category/product nav items from one shared catalogue projection. */
function frpdepot_acp_generated_items( $groups, $root_parent, $order_start ) {
	$items = array();
	$order = (int) $order_start;
	$seed  = -700000;
	foreach ( $groups as $group ) {
		$category_item_id = $seed--;
		$items[] = frpdepot_acp_nav_item(
			$category_item_id,
			$root_parent,
			$order++,
			'product_cat',
			$group['id'],
			$group['name'],
			$group['url'],
			array( 'menu-item', 'menu-item-type-taxonomy', 'menu-item-object-product_cat',
				'menu-item-has-children', 'frpdepot-acp-catalogue-item', 'frpdepot-acp-category-item',
				'frpdepot-acp-category-' . (int) $group['id'] )
		);
		foreach ( $group['products'] as $product ) {
			$items[] = frpdepot_acp_nav_item(
				$seed--,
				$category_item_id,
				$order++,
				'product',
				$product['id'],
				$product['name'],
				$product['url'],
				array( 'menu-item', 'menu-item-type-post_type', 'menu-item-object-product',
					'frpdepot-acp-catalogue-item', 'frpdepot-acp-product-item',
					'frpdepot-acp-category-parent-' . (int) $group['id'],
					'frpdepot-acp-product-' . (int) $product['id'] )
			);
		}
	}
	return $items;
}

/** Return all descendant IDs below one existing menu item. */
function frpdepot_acp_descendant_ids( $items, $root_id ) {
	$descendants = array();
	$changed     = true;
	while ( $changed ) {
		$changed = false;
		foreach ( $items as $item ) {
			$parent = isset( $item->menu_item_parent ) ? (int) $item->menu_item_parent : 0;
			$id     = isset( $item->ID ) ? (int) $item->ID : 0;
			if ( $id && ( $parent === (int) $root_id || isset( $descendants[ $parent ] ) )
				&& ! isset( $descendants[ $id ] ) ) {
				$descendants[ $id ] = true;
				$changed             = true;
			}
		}
	}
	return $descendants;
}

/**
 * Synchronize the server-rendered main, Divi mobile source, and footer menus.
 *
 * The filter receives the exact menu object. Main-menu replacement requires one
 * unambiguous top-level item at the permanent /products/ path. Its request-only
 * projection is titled exactly Shop All, even if the stored legacy label is Shop
 * Online; WordPress is never updated. The footer menu is wholly catalogue-owned.
 * A genuinely empty public catalogue removes stale projected descendants/footer
 * items at read time.
 */
function frpdepot_acp_sync_menu_items( $items, $menu, $args ) {
	unset( $args );
	if ( ! frpdepot_acp_is_public_request() || ! is_array( $items ) ) {
		return $items;
	}
	$slug = frpdepot_acp_menu_slug( $menu );
	if ( FRPDEPOT_ACP_MAIN_MENU_SLUG !== $slug && FRPDEPOT_ACP_FOOTER_MENU_SLUG !== $slug ) {
		return $items;
	}
	$groups = frpdepot_acp_catalogue_groups();

	if ( FRPDEPOT_ACP_FOOTER_MENU_SLUG === $slug ) {
		return frpdepot_acp_generated_items( $groups, 0, 1 );
	}

	$shop_items = array();
	foreach ( $items as $item ) {
		$parent = isset( $item->menu_item_parent ) ? (int) $item->menu_item_parent : 0;
		if ( 0 === $parent && FRPDEPOT_ACP_SHOP_PATH === frpdepot_acp_menu_item_path( $item ) ) {
			$shop_items[] = $item;
		}
	}
	if ( 1 !== count( $shop_items ) ) {
		return $items;
	}
	$shop        = $shop_items[0];
	$descendants = frpdepot_acp_descendant_ids( $items, (int) $shop->ID );
	$kept        = array();
	$max_order   = 0;
	foreach ( $items as $item ) {
		if ( isset( $descendants[ (int) $item->ID ] ) ) {
			continue;
		}
		$projected = $item;
		if ( (int) $item->ID === (int) $shop->ID ) {
			// Clone before changing presentation so even the request's source item is untouched.
			$projected = clone $item;
			$classes = isset( $item->classes ) && is_array( $item->classes ) ? $item->classes : array();
			if ( ! empty( $groups ) && ! in_array( 'menu-item-has-children', $classes, true ) ) {
				$classes[] = 'menu-item-has-children';
			}
			if ( empty( $groups ) ) {
				$classes = array_values( array_diff( $classes, array( 'menu-item-has-children' ) ) );
			}
			$projected->title   = FRPDEPOT_ACP_SHOP_TITLE;
			$projected->classes = $classes;
		}
		$max_order = max( $max_order, isset( $item->menu_order ) ? (int) $item->menu_order : 0 );
		$kept[]    = $projected;
	}
	return array_merge(
		$kept,
		frpdepot_acp_generated_items( $groups, (int) $shop->ID, $max_order + 1 )
	);
}

/** Find exactly one balanced outer DIV carrying an exact class token. */
function frpdepot_acp_find_single_div( $html, $class_token ) {
	$pattern = '/<div\b(?=[^>]*\bclass=(?:"[^"]*\b' . preg_quote( $class_token, '/' ) . '\b[^"]*"|\'[^\']*\b' . preg_quote( $class_token, '/' ) . '\b[^\']*\'))[^>]*>/i';
	$found   = preg_match_all( $pattern, $html, $matches, PREG_OFFSET_CAPTURE );
	if ( 1 !== $found ) {
		return null;
	}
	$start = (int) $matches[0][0][1];
	$tail  = substr( $html, $start );
	if ( ! preg_match_all( '/<\/?div\b[^>]*>/i', $tail, $tags, PREG_OFFSET_CAPTURE ) ) {
		return null;
	}
	$depth = 0;
	foreach ( $tags[0] as $tag ) {
		if ( 0 === stripos( $tag[0], '</div' ) ) {
			$depth--;
		} else {
			$depth++;
		}
		if ( 0 === $depth ) {
			$length = (int) $tag[1] + strlen( $tag[0] );
			return array( 'start' => $start, 'length' => $length, 'html' => substr( $html, $start, $length ) );
		}
	}
	return null;
}

/** Exact card semantics: one fixed title and one fixed guide description. */
function frpdepot_acp_exact_guide_module( $module, $resin ) {
	if ( ! is_array( $module ) || ! isset( $module['html'] ) ) {
		return false;
	}
	$source = $module['html'];
	$title  = '/<h4\s+class="et_pb_module_header">\s*' . preg_quote( $resin . ' Resin', '/' ) . '\s*<\/h4>/';
	$body   = '/<div\s+class="et_pb_blurb_description">\s*<p>\s*Chemical Resistance Guide\s*<\/p>\s*<\/div>/';
	return 1 === preg_match_all( $title, $source ) && 1 === preg_match_all( $body, $source );
}

/** Exact guide cards are siblings: their balanced outer DIV ranges cannot overlap. */
function frpdepot_acp_modules_are_disjoint( $left, $right ) {
	if ( ! is_array( $left ) || ! is_array( $right )
		|| ! isset( $left['start'], $left['length'], $right['start'], $right['length'] ) ) {
		return false;
	}
	$left_end  = (int) $left['start'] + (int) $left['length'];
	$right_end = (int) $right['start'] + (int) $right['length'];
	return $left_end <= (int) $right['start'] || $right_end <= (int) $left['start'];
}

/**
 * Atomically transform the exact shared Divi product output.
 *
 * No marker, duplicate marker, changed card copy or changed inline CTA means no
 * transformation at all. Validation precedes every replacement so Hetron removal,
 * Derakane repointing and CTA preservation cannot land partially.
 */
function frpdepot_acp_transform_product_html( $html ) {
	if ( ! is_string( $html ) || '' === $html ) {
		return $html;
	}
	$hetron_module  = frpdepot_acp_find_single_div( $html, FRPDEPOT_ACP_HETRON_MODULE_CLASS );
	$derakane_module = frpdepot_acp_find_single_div( $html, FRPDEPOT_ACP_DERAKANE_MODULE_CLASS );
	$derakane_old_link = '{"class":"et-db #et-boc .et-l .et_pb_blurb_0_tb_body","url":"'
		. FRPDEPOT_ACP_DERAKANE_OLD_URL . '","target":"_blank"}';
	$derakane_new_link = '{"class":"et-db #et-boc .et-l .et_pb_blurb_0_tb_body","url":"'
		. FRPDEPOT_ACP_DERAKANE_NEW_URL . '","target":"_blank"}';

	$markers_exact = frpdepot_acp_exact_guide_module( $hetron_module, 'Hetron' )
		&& frpdepot_acp_exact_guide_module( $derakane_module, 'Derakane' )
		&& frpdepot_acp_modules_are_disjoint( $hetron_module, $derakane_module )
		&& 0 === substr_count( $html, FRPDEPOT_ACP_HETRON_URL )
		&& 1 === substr_count( $html, $derakane_old_link )
		&& 1 === substr_count( $html, FRPDEPOT_ACP_DERAKANE_OLD_URL )
		&& 0 === substr_count( $html, FRPDEPOT_ACP_DERAKANE_NEW_URL )
		&& 1 === substr_count( $html, FRPDEPOT_ACP_INLINE_CTA );
	if ( ! $markers_exact ) {
		return $html;
	}

	$transformed = substr_replace(
		$html,
		'',
		$hetron_module['start'],
		$hetron_module['length']
	);
	$transformed = str_replace( $derakane_old_link, $derakane_new_link, $transformed );

	$postconditions = 0 === substr_count( $transformed, FRPDEPOT_ACP_HETRON_URL )
		&& 0 === substr_count( $transformed, FRPDEPOT_ACP_DERAKANE_OLD_URL )
		&& 1 === substr_count( $transformed, FRPDEPOT_ACP_DERAKANE_NEW_URL )
		&& 1 === substr_count( $transformed, $derakane_new_link )
		&& 1 === substr_count( $transformed, FRPDEPOT_ACP_INLINE_CTA )
		&& null === frpdepot_acp_find_single_div( $transformed, FRPDEPOT_ACP_HETRON_MODULE_CLASS )
		&& frpdepot_acp_exact_guide_module(
			frpdepot_acp_find_single_div( $transformed, FRPDEPOT_ACP_DERAKANE_MODULE_CLASS ),
			'Derakane'
		);
	return $postconditions ? $transformed : $html;
}

/** Start the final-output filter only for anonymous/public Woo product pages. */
function frpdepot_acp_start_product_output_filter() {
	if ( ! frpdepot_acp_is_public_request() || ! function_exists( 'is_product' ) || ! is_product() ) {
		return;
	}
	ob_start( 'frpdepot_acp_transform_product_html' );
}

add_filter( 'wp_get_nav_menu_items', 'frpdepot_acp_sync_menu_items', 90, 3 );
add_action( 'template_redirect', 'frpdepot_acp_start_product_output_filter', 0 );

// Request-local refresh hooks. They clear only an in-memory PHP array.
add_action( 'transition_post_status', 'frpdepot_acp_invalidate_request_cache', 10, 3 );
add_action( 'save_post_product', 'frpdepot_acp_invalidate_request_cache', 10, 3 );
add_action( 'set_object_terms', 'frpdepot_acp_invalidate_request_cache', 10, 6 );
add_action( 'clean_object_term_cache', 'frpdepot_acp_invalidate_request_cache', 10, 2 );
add_action( 'woocommerce_product_set_visibility', 'frpdepot_acp_invalidate_request_cache', 10, 1 );
