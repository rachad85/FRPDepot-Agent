<?php
/**
 * Plugin Name: Derakane™ Resin Chemical Resistance Guide Search
 * Description: Read-only search interface for a manifest-pinned, independently verified Derakane/Alta guide dataset.
 * Version: 2.0.0
 * Requires at least: 6.4
 * Requires PHP: 8.0
 * Author: FRP Depots
 * Text Domain: frpdepot-derakane-search
 */

defined( 'ABSPATH' ) || exit;

define( 'FRPDEPOT_DERAKANE_VERSION', '2.0.0' );
define( 'FRPDEPOT_DERAKANE_DIR', plugin_dir_path( __FILE__ ) );
define( 'FRPDEPOT_DERAKANE_URL', plugin_dir_url( __FILE__ ) );
define( 'FRPDEPOT_DERAKANE_MANIFEST', FRPDEPOT_DERAKANE_DIR . 'data/import-manifest.json' );

/**
 * Normalize guide search text without applying fuzzy matching.
 */
function frpdepot_derakane_normalize( $value ) {
	$value = remove_accents( (string) $value );
	$value = str_replace( array( '–', '—', '−', '‑' ), '-', $value );
	$value = function_exists( 'mb_strtolower' ) ? mb_strtolower( $value, 'UTF-8' ) : strtolower( $value );
	$value = preg_replace( '/[^a-z0-9]+/u', ' ', $value );
	return trim( preg_replace( '/\s+/', ' ', $value ) );
}

/**
 * Runtime defense in depth. Packaging performs the complete closed-contract audit.
 */
function frpdepot_derakane_verified_import() {
	static $loaded = false;
	static $result = null;

	if ( $loaded ) {
		return $result;
	}
	$loaded = true;

	if ( ! is_readable( FRPDEPOT_DERAKANE_MANIFEST ) || is_link( FRPDEPOT_DERAKANE_MANIFEST ) ) {
		return null;
	}
	$manifest_bytes = file_get_contents( FRPDEPOT_DERAKANE_MANIFEST );
	$manifest = json_decode( $manifest_bytes, true );
	if ( ! is_array( $manifest )
		|| 'frpdepot.derakane-search.import-manifest' !== ( $manifest['contract'] ?? null )
		|| 2 !== ( $manifest['contract_version'] ?? null )
		|| 'frpdepot.derakane-guide-rebuild' !== ( $manifest['producer']['pipeline'] ?? null )
		|| 2 !== ( $manifest['producer']['contract_version'] ?? null )
		|| 'VERIFIED' !== ( $manifest['verification']['status'] ?? null )
		|| 0 !== ( $manifest['verification']['unresolved_count'] ?? null )
		|| ! isset( $manifest['rebuild_provenance']['output_manifest_sha256'], $manifest['rebuild_provenance']['source_pdf_sha256'] )
		|| ! preg_match( '/^[a-f0-9]{64}$/', (string) $manifest['rebuild_provenance']['output_manifest_sha256'] )
		|| ( $manifest['source']['document_sha256'] ?? null ) !== $manifest['rebuild_provenance']['source_pdf_sha256']
		|| 'derakane-dataset.json' !== ( $manifest['dataset']['file'] ?? null )
		|| ! preg_match( '/^[a-f0-9]{64}$/', (string) ( $manifest['dataset']['sha256'] ?? '' ) )
	) {
		return null;
	}

	$dataset_path = FRPDEPOT_DERAKANE_DIR . 'data/derakane-dataset.json';
	if ( ! is_readable( $dataset_path ) || is_link( $dataset_path ) ) {
		return null;
	}
	$dataset_bytes = file_get_contents( $dataset_path );
	if ( strlen( $dataset_bytes ) !== ( $manifest['dataset']['bytes'] ?? null )
		|| hash( 'sha256', $dataset_bytes ) !== $manifest['dataset']['sha256']
	) {
		return null;
	}
	$dataset = json_decode( $dataset_bytes, true );
	if ( ! is_array( $dataset )
		|| 'frpdepot.derakane-search.dataset' !== ( $dataset['contract'] ?? null )
		|| 2 !== ( $dataset['contract_version'] ?? null )
		|| ( $dataset['source'] ?? null ) !== ( $manifest['source'] ?? null )
		|| ( $dataset['rebuild_provenance'] ?? null ) !== ( $manifest['rebuild_provenance'] ?? null )
		|| ! isset( $dataset['rows'], $dataset['footnotes'], $dataset['resin_columns'], $dataset['cas_catalog'], $dataset['search_entities'], $dataset['semantics'] )
		|| ! is_array( $dataset['rows'] )
		|| count( $dataset['rows'] ) !== ( $manifest['summary']['row_count'] ?? null )
		|| count( $dataset['search_entities'] ) !== ( $manifest['summary']['search_entity_count'] ?? null )
		|| count( $dataset['cas_catalog'] ) !== ( $manifest['summary']['cas_entry_count'] ?? null )
		|| count( $dataset['rows'] ) !== ( $manifest['verification']['reviewed_row_count'] ?? null )
		|| 25 !== count( $dataset['footnotes'] )
		|| 9 !== count( $dataset['resin_columns'] )
	) {
		return null;
	}
	foreach ( $dataset['rows'] as $row ) {
		if ( 'VERIFIED' !== ( $row['qa_status'] ?? null ) || 9 !== count( $row['cells'] ?? array() ) ) {
			return null;
		}
	}

	$result = array(
		'manifest' => $manifest,
		'dataset'  => $dataset,
	);
	return $result;
}

/** Rank one v2 search entity: exact name, exact public alias/CAS, starts-with, contains. */
function frpdepot_derakane_match_rank( $name_key, $alias_keys, $public_cas_numbers, $query ) {
	$name = frpdepot_derakane_normalize( $name_key );
	$aliases = array_map( 'frpdepot_derakane_normalize', $alias_keys );
	$cas_numbers = array_map( 'frpdepot_derakane_normalize', $public_cas_numbers );
	$secondary = array_merge( $aliases, $cas_numbers );
	$all = array_merge( array( $name ), $secondary );

	if ( $name === $query ) {
		return 0;
	}
	if ( in_array( $query, $secondary, true ) ) {
		return 1;
	}
	foreach ( $all as $candidate ) {
		if ( '' !== $candidate && str_starts_with( $candidate, $query ) ) {
			return 2;
		}
	}
	foreach ( $all as $candidate ) {
		if ( '' !== $candidate && false !== strpos( $candidate, $query ) ) {
			return 3;
		}
	}
	return null;
}

function frpdepot_derakane_referenced_footnotes( $rows, $visible_resin_ids, $definitions ) {
	$ids = array();
	foreach ( $rows as $row ) {
		foreach ( $row['row_footnote_ids'] as $id ) {
			$ids[ $id ] = true;
		}
		foreach ( $row['chemical_source']['footnote_refs'] as $id ) {
			$ids[ $id ] = true;
		}
		foreach ( $row['cells'] as $cell ) {
			if ( ! in_array( $cell['resin_id'], $visible_resin_ids, true ) ) {
				continue;
			}
			foreach ( $cell['footnote_ids'] as $id ) {
				$ids[ $id ] = true;
			}
			foreach ( $cell['ratings'] as $rating ) {
				foreach ( $rating['footnote_refs'] as $id ) {
					$ids[ $id ] = true;
				}
			}
		}
	}
	ksort( $ids, SORT_NUMERIC );
	$resolved = array();
	foreach ( array_keys( $ids ) as $id ) {
		if ( isset( $definitions[ $id ] ) ) {
			$resolved[] = $definitions[ $id ];
		}
	}
	return $resolved;
}

/**
 * Pure search operation over already verified rows. Source order is never replaced
 * with lexical/numeric concentration sorting.
 */
function frpdepot_derakane_search_dataset( $dataset, $manifest, $chemical, $concentration = '', $resin = '', $offset = 0 ) {
	$query = frpdepot_derakane_normalize( $chemical );
	if ( strlen( $query ) < 2 ) {
		return new WP_Error( 'derakane_short_query', 'Enter at least 2 characters.', array( 'status' => 400 ) );
	}

	$resin_ids = array_column( $dataset['resin_columns'], 'id' );
	if ( '' !== $resin && ! in_array( $resin, $resin_ids, true ) ) {
		return new WP_Error( 'derakane_invalid_resin', 'Unknown resin filter.', array( 'status' => 400 ) );
	}
	$offset = max( 0, (int) $offset );

	$rows_by_id = array();
	foreach ( $dataset['rows'] as $row ) {
		$rows_by_id[ $row['row_id'] ] = $row;
	}
	$cas_by_id = array();
	$excluded_cas_keys = array();
	foreach ( $dataset['cas_catalog'] as $entry ) {
		$cas_by_id[ $entry['cas_entry_id'] ] = $entry;
		if ( false === $entry['public_searchable'] ) {
			foreach ( array( $entry['cas_raw'], $entry['raw_pdf_cas'] ) as $raw_cas ) {
				$excluded_cas_keys[ frpdepot_derakane_normalize( $raw_cas ) ] = true;
			}
		}
	}
	$groups = array();
	foreach ( $dataset['search_entities'] as $entity ) {
		if ( isset( $excluded_cas_keys[ $query ] ) ) {
			continue;
		}
		$public_aliases = array_values(
			array_filter(
				$entity['aliases'],
				function ( $alias ) use ( $excluded_cas_keys ) {
					return ! isset( $excluded_cas_keys[ frpdepot_derakane_normalize( $alias['display'] ) ] )
						&& ! isset( $excluded_cas_keys[ frpdepot_derakane_normalize( $alias['search_key'] ) ] );
				}
			)
		);
		$alias_keys = array_map( function ( $alias ) { return $alias['search_key']; }, $public_aliases );
		$public_cas_numbers = array();
		foreach ( $entity['cas_entry_ids'] as $entry_id ) {
			$entry = $cas_by_id[ $entry_id ] ?? null;
			if ( $entry && true === $entry['public_searchable'] && null !== $entry['normalized_cas'] ) {
				$public_cas_numbers[] = $entry['normalized_cas'];
			}
		}
		$public_cas_numbers = array_values( array_unique( $public_cas_numbers ) );
		$rank = frpdepot_derakane_match_rank( $entity['name_key'], $alias_keys, $public_cas_numbers, $query );
		if ( null === $rank ) {
			continue;
		}
		$entity_rows = array();
		foreach ( $entity['record_ids'] as $record_id ) {
			if ( isset( $rows_by_id[ $record_id ] ) ) {
				$row = $rows_by_id[ $record_id ];
				$row['aliases'] = $public_aliases;
				$row['public_cas_numbers'] = array_values(
					array_intersect( $row['public_cas_numbers'], $public_cas_numbers )
				);
				$entity_rows[] = $row;
			}
		}
		$groups[] = array(
			'rank'            => $rank,
			'first_sequence'  => $entity['source_order'],
			'chemical_id'     => $entity['entity_id'],
			'chemical_name'   => $entity['display_name'],
			'aliases'         => $public_aliases,
			'public_cas_numbers' => $public_cas_numbers,
			'all_rows'        => $entity_rows,
			'entity_type'     => $entity['entity_type'],
		);
	}
	usort(
		$groups,
		function ( $left, $right ) {
			return array( $left['rank'], $left['first_sequence'] ) <=> array( $right['rank'], $right['first_sequence'] );
		}
	);

	$concentration_options = array();
	$filtered_groups = array();
	foreach ( $groups as $group ) {
		$rows = array();
		foreach ( $group['all_rows'] as $row ) {
			$display = $row['concentration']['display'];
			if ( ! isset( $concentration_options[ $display ] ) ) {
				$concentration_options[ $display ] = $display;
			}
			if ( '' === $concentration || $display === $concentration ) {
				if ( '' !== $resin ) {
					$row['cells'] = array_values(
						array_filter(
							$row['cells'],
							function ( $cell ) use ( $resin ) { return $cell['resin_id'] === $resin; }
						)
					);
				}
				$rows[] = $row;
			}
		}
		if ( $rows || ( 'cas_catalog_only' === $group['entity_type'] && '' === $concentration && '' === $resin ) ) {
			$group['rows'] = $rows;
			unset( $group['all_rows'], $group['rank'], $group['first_sequence'], $group['entity_type'] );
			$filtered_groups[] = $group;
		}
	}

	$definitions = array();
	foreach ( $dataset['footnotes'] as $footnote ) {
		$definitions[ $footnote['id'] ] = $footnote;
	}
	$visible_resins = '' === $resin ? $resin_ids : array( $resin );
	$total = count( $filtered_groups );
	$page = array_slice( $filtered_groups, $offset, 20 );
	foreach ( $page as &$group ) {
		$group['footnotes'] = frpdepot_derakane_referenced_footnotes( $group['rows'], $visible_resins, $definitions );
	}
	unset( $group );

	return array(
		'query'                 => $chemical,
		'total'                 => $total,
		'offset'                => $offset,
		'limit'                 => 20,
		'next_offset'           => $offset + count( $page ) < $total ? $offset + count( $page ) : null,
		'groups'                => $page,
		'concentration_options' => array_values( $concentration_options ),
		'resin_columns'         => array_values(
			array_filter(
				$dataset['resin_columns'],
				function ( $column ) use ( $resin ) { return '' === $resin || $column['id'] === $resin; }
			)
		),
		'source'                => array_merge(
			$dataset['source'],
			array( 'dataset_sha256' => $manifest['dataset']['sha256'] )
		),
	);
}

function frpdepot_derakane_rest_search( WP_REST_Request $request ) {
	$import = frpdepot_derakane_verified_import();
	if ( ! $import ) {
		return new WP_Error( 'derakane_unavailable', 'The verified guide dataset is unavailable.', array( 'status' => 503 ) );
	}
	$result = frpdepot_derakane_search_dataset(
		$import['dataset'],
		$import['manifest'],
		sanitize_text_field( $request->get_param( 'chemical' ) ?? '' ),
		sanitize_text_field( $request->get_param( 'concentration' ) ?? '' ),
		sanitize_text_field( $request->get_param( 'resin' ) ?? '' ),
		absint( $request->get_param( 'offset' ) ?? 0 )
	);
	return is_wp_error( $result ) ? $result : rest_ensure_response( $result );
}

function frpdepot_derakane_register_rest() {
	register_rest_route(
		'frpdepot-derakane/v1',
		'/search',
		array(
			'methods'             => WP_REST_Server::READABLE,
			'callback'            => 'frpdepot_derakane_rest_search',
			'permission_callback' => '__return_true',
		)
	);
}
add_action( 'rest_api_init', 'frpdepot_derakane_register_rest' );

function frpdepot_derakane_register_assets() {
	wp_register_style(
		'frpdepot-derakane-search',
		FRPDEPOT_DERAKANE_URL . 'assets/derakane-search.css',
		array(),
		FRPDEPOT_DERAKANE_VERSION
	);
	wp_register_script(
		'frpdepot-derakane-search',
		FRPDEPOT_DERAKANE_URL . 'assets/derakane-search.js',
		array(),
		FRPDEPOT_DERAKANE_VERSION,
		true
	);
}
add_action( 'wp_enqueue_scripts', 'frpdepot_derakane_register_assets' );

function frpdepot_derakane_source_line( $source, $dataset_sha256 ) {
	return sprintf(
		'<p class="derakane-search__source"><strong>Source:</strong> %1$s, <cite>%2$s</cite>, edition %3$s, document %4$s, publication %5$s, page count %6$d. <span class="derakane-search__hash">Source document SHA-256: <code>%7$s</code>. Verified dataset SHA-256: <code>%8$s</code>.</span></p>',
		esc_html( $source['publisher'] ),
		esc_html( $source['title'] ),
		esc_html( $source['edition'] ),
		esc_html( $source['document_code'] ),
		esc_html( $source['publication_date'] ),
		absint( $source['page_count'] ),
		esc_html( $source['document_sha256'] ),
		esc_html( $dataset_sha256 )
	);
}

function frpdepot_derakane_shortcode() {
	$import = frpdepot_derakane_verified_import();
	wp_enqueue_style( 'frpdepot-derakane-search' );

	$config = array(
		'restUrl'    => esc_url_raw( rest_url( 'frpdepot-derakane/v1/search' ) ),
		'minChars'   => 2,
		'debounceMs' => 250,
	);
	wp_enqueue_script( 'frpdepot-derakane-search' );
	wp_add_inline_script( 'frpdepot-derakane-search', 'window.FRPDepotDerakaneConfig = ' . wp_json_encode( $config ) . ';', 'before' );

	$instance = wp_unique_id( 'frpdepot-derakane-' );
	ob_start();
	?>
	<section class="derakane-search" data-derakane-search>
		<h1>Derakane™ Resin Chemical Resistance Guide Search</h1>
		<p class="derakane-search__deck">Search maximum service-temperature entries from the INEOS Resin Selection Guide for Chemical Resistance by chemical name, synonym, or CAS number.</p>
		<?php if ( $import ) : ?>
			<?php echo frpdepot_derakane_source_line( $import['dataset']['source'], $import['manifest']['dataset']['sha256'] ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped ?>
		<?php else : ?>
			<p class="derakane-search__unavailable" role="alert"><strong>Search unavailable:</strong> no manifest-pinned verified dataset is installed.</p>
		<?php endif; ?>

		<aside class="derakane-search__warning" aria-labelledby="<?php echo esc_attr( $instance ); ?>-warning-title">
			<h2 id="<?php echo esc_attr( $instance ); ?>-warning-title">Important technical limitations</h2>
			<p>This search is a quick-reference interface to selected data published in the INEOS <cite>Resin Selection Guide for Chemical Resistance</cite>. It is not a resin-selection approval, engineering recommendation, warranty, or guarantee of fitness for a particular purpose. Unless otherwise stated, values are the guide’s highest known service temperatures in <strong>°C/°F</strong> at which properly designed, fabricated, installed, and cured FRP equipment has generally provided good service or testing indicated good life expectancy; they are not necessarily maximum service temperatures.</p>
			<p><strong>Blank</strong> means no data was available when the guide ratings were assigned. <strong>NR</strong> means not recommended at any temperature. <strong>LS</strong> means limited service; consult INEOS Technical Service. Footnotes and construction requirements are essential parts of each result. Suitability can change with complete composition and trace constituents, mixtures, concentration, physical state, condensation, pressure or vacuum, normal and upset temperatures and durations, cycling, abrasion, corrosion-barrier construction, veil, cure/post-cure, fabrication, installation, and applicable codes. Do not infer mixture or alternating-environment suitability from individual chemical entries.</p>
			<p>Confirm the current INEOS guide and obtain a project-specific recommendation from INEOS Technical Service at <a href="mailto:derakane@ineos.com">derakane@ineos.com</a> or a qualified corrosion engineer before specification, purchase, or use. Derakane™, Derakane™ Momentum™, and Derakane™ Signia™ are trademarks of INEOS or its subsidiaries.</p>
		</aside>

		<div class="derakane-search__legend" aria-label="Guide rating legend">
			<h2>Guide rating legend</h2>
			<dl>
				<div><dt>Blank</dt><dd>No data was available when the guide ratings were assigned.</dd></div>
				<div><dt>NR</dt><dd>Not recommended at any temperature.</dd></div>
				<div><dt>LS</dt><dd>Limited service; consult INEOS Technical Service.</dd></div>
			</dl>
		</div>

		<?php if ( $import ) : ?>
			<form class="derakane-search__form" data-derakane-search-form>
				<div class="derakane-search__field derakane-search__field--query">
					<label for="<?php echo esc_attr( $instance ); ?>-chemical">Chemical name, synonym, or CAS number</label>
					<input type="search" id="<?php echo esc_attr( $instance ); ?>-chemical" name="chemical" data-derakane-search-input placeholder="e.g., hydrochloric acid, muriatic acid, or 7647-01-0" autocomplete="off" required minlength="2">
				</div>
				<button type="submit" class="derakane-search__button">Search guide</button>
				<div class="derakane-search__field">
					<label for="<?php echo esc_attr( $instance ); ?>-concentration">Concentration (exact guide entry)</label>
					<select id="<?php echo esc_attr( $instance ); ?>-concentration" data-derakane-concentration disabled><option value="">All listed concentrations</option></select>
				</div>
				<div class="derakane-search__field">
					<label for="<?php echo esc_attr( $instance ); ?>-resin">Resin series</label>
					<select id="<?php echo esc_attr( $instance ); ?>-resin" data-derakane-resin>
						<option value="">All resin series</option>
						<?php foreach ( $import['dataset']['resin_columns'] as $column ) : ?>
							<option value="<?php echo esc_attr( $column['id'] ); ?>"><?php echo esc_html( $column['label'] ); ?></option>
						<?php endforeach; ?>
					</select>
				</div>
			</form>
			<p class="derakane-search__filter-note">Filters display only exact published concentration rows and exact resin cells. No values are interpolated, inferred, or substituted.</p>
			<div class="derakane-search__status" data-derakane-search-status role="status" aria-live="polite" aria-atomic="true"></div>
			<div class="derakane-search__results" data-derakane-search-results></div>
			<div class="derakane-search__more"><button type="button" class="derakane-search__button" data-derakane-load-more hidden>Load more</button></div>
		<?php endif; ?>
	</section>
	<?php
	return ob_get_clean();
}
add_shortcode( 'frpdepot_derakane_search', 'frpdepot_derakane_shortcode' );
