( function ( window, document, $ ) {
	'use strict';

	var config = window.FRPDepotFreightQuoteJourney || {};
	var firedLeadKeys = Object.create( null );

	function positiveQuantity( value ) {
		var text = String( value === undefined || value === null ? '' : value );
		return /^[1-9][0-9]*$/.test( text ) && Number( text ) <= 9999;
	}

	function decimalId( value, allowZero ) {
		var text = String( value === undefined || value === null ? '' : value );
		var expression = allowZero ? /^(?:0|[1-9][0-9]*)$/ : /^[1-9][0-9]*$/;
		return text.length <= 10 && expression.test( text );
	}

	function idList( value, allowZero ) {
		var text = String( value || '' );
		if ( text === '' ) {
			return true;
		}
		return text.split( ',' ).every( function ( item ) {
			return decimalId( item, allowZero );
		} );
	}

	function sameOriginUrl( raw ) {
		try {
			var url = new window.URL( String( raw || '' ), window.location.origin );
			return url.origin === window.location.origin ? url : null;
		} catch ( error ) {
			return null;
		}
	}

	function allVariationSelectorsResolved( form ) {
		var selectors = form.querySelectorAll( '.variations select[name^="attribute_"]' );
		if ( ! selectors.length ) {
			return false;
		}
		for ( var index = 0; index < selectors.length; index++ ) {
			if ( String( selectors[ index ].value || '' ) === '' ) {
				return false;
			}
		}
		return true;
	}

	function quantity( form ) {
		var field = form.querySelector( 'input.qty' );
		var value = field ? String( field.value || '' ) : '1';
		return positiveQuantity( value ) ? value : '';
	}

	function rememberNativeButton( button ) {
		if ( ! button || button.frpdepotFqjOriginal ) {
			return;
		}
		button.frpdepotFqjOriginal = {
			hidden: button.hidden,
			disabled: button.disabled,
			ariaHidden: button.getAttribute( 'aria-hidden' ),
			ariaDisabled: button.getAttribute( 'aria-disabled' ),
			tabindex: button.getAttribute( 'tabindex' )
		};
	}

	function restoreNativeButton( button ) {
		if ( ! button || ! button.frpdepotFqjOriginal ) {
			return;
		}
		var original = button.frpdepotFqjOriginal;
		button.hidden = original.hidden;
		button.disabled = original.disabled;
		[ [ 'aria-hidden', original.ariaHidden ], [ 'aria-disabled', original.ariaDisabled ], [ 'tabindex', original.tabindex ] ].forEach( function ( pair ) {
			if ( pair[ 1 ] === null ) {
				button.removeAttribute( pair[ 0 ] );
			} else {
				button.setAttribute( pair[ 0 ], pair[ 1 ] );
			}
		} );
	}

	function disableQuoteButton( button ) {
		if ( ! button ) {
			return;
		}
		button.removeAttribute( 'href' );
		button.setAttribute( 'aria-disabled', 'true' );
		button.setAttribute( 'tabindex', '-1' );
	}

	function productQuoteUrl( form, variation ) {
		var base = sameOriginUrl( config.quoteUrl );
		var requestedQuantity = quantity( form );
		if ( ! base || ! requestedQuantity || ! allVariationSelectorsResolved( form )
			|| ! decimalId( variation.frpdepot_product_id, false )
			|| ! decimalId( variation.frpdepot_variation_id, false ) ) {
			return '';
		}
		base.search = '';
		base.hash = '';
		base.searchParams.set( 'fqj_source', 'product' );
		base.searchParams.set( 'fqj_product_id', String( variation.frpdepot_product_id ) );
		base.searchParams.set( 'fqj_variation_id', String( variation.frpdepot_variation_id ) );
		base.searchParams.set( 'fqj_quantity', requestedQuantity );
		return base.toString();
	}

	function bindProductChoiceA() {
		var panel = document.querySelector( '.frpdepot-fqj-product' );
		var form = document.querySelector( 'form.variations_form' );
		if ( ! panel || ! form || ! $ ) {
			return;
		}
		var quoteButton = panel.querySelector( '.frpdepot-fqj-product-button' );
		var nativeButton = form.querySelector( '.single_add_to_cart_button' );
		var currentVariation = null;
		rememberNativeButton( nativeButton );

		function unresolved() {
			currentVariation = null;
			panel.hidden = true;
			disableQuoteButton( quoteButton );
			restoreNativeButton( nativeButton );
		}

		function render( variation ) {
			currentVariation = variation || null;
			var hasDecision = variation && typeof variation.frpdepot_quote_required === 'boolean';
			var hasIds = variation && decimalId( variation.frpdepot_product_id, false )
				&& decimalId( variation.frpdepot_variation_id, false );
			if ( hasDecision && hasIds && variation.frpdepot_quote_required === false ) {
				panel.hidden = true;
				disableQuoteButton( quoteButton );
				restoreNativeButton( nativeButton );
				return;
			}

			panel.hidden = false;
			if ( nativeButton ) {
				nativeButton.hidden = true;
				nativeButton.disabled = true;
				nativeButton.setAttribute( 'aria-hidden', 'true' );
				nativeButton.setAttribute( 'aria-disabled', 'true' );
				nativeButton.setAttribute( 'tabindex', '-1' );
			}
			disableQuoteButton( quoteButton );
			if ( hasDecision && hasIds && variation.frpdepot_quote_required === true ) {
				var target = productQuoteUrl( form, variation );
				if ( target ) {
					quoteButton.href = target;
					quoteButton.removeAttribute( 'aria-disabled' );
					quoteButton.removeAttribute( 'tabindex' );
				}
			}
		}

		$( form ).on( 'found_variation', function ( event, variation ) {
			render( variation );
		} );
		$( form ).on( 'reset_data hide_variation', unresolved );
		form.addEventListener( 'input', function () {
			if ( currentVariation ) {
				render( currentVariation );
			}
		} );
		form.addEventListener( 'change', function () {
			if ( currentVariation ) {
				render( currentVariation );
			}
		} );
		panel.addEventListener( 'click', function ( event ) {
			var target = event.target.closest( '.frpdepot-fqj-product-button' );
			if ( target && ( target.getAttribute( 'aria-disabled' ) === 'true' || ! target.getAttribute( 'href' ) ) ) {
				event.preventDefault();
			}
		} );
		unresolved();
	}

	function makeCartNotice() {
		var notice = document.createElement( 'div' );
		notice.className = 'woocommerce-info frpdepot-fqj-cart-notice';
		notice.setAttribute( 'role', 'status' );
		var heading = document.createElement( 'strong' );
		heading.textContent = String( config.cartHeading || '' );
		var breakNode = document.createElement( 'br' );
		var copy = document.createTextNode( String( config.cartText || '' ) );
		notice.appendChild( heading );
		notice.appendChild( breakNode );
		notice.appendChild( copy );
		return notice;
	}

	function makeCartButton() {
		var url = sameOriginUrl( config.cartQuoteUrl );
		if ( ! url || url.searchParams.toString() !== 'fqj_source=cart' ) {
			return null;
		}
		var button = document.createElement( 'a' );
		button.className = 'button alt frpdepot-fqj-cart-button';
		button.href = url.toString();
		button.textContent = String( config.button || '' );
		return button;
	}

	function enforceCartQuoteState() {
		if ( config.cartQuoteRequired !== true ) {
			return;
		}
		var host = document.querySelector( '.wp-block-woocommerce-cart,.wc-block-cart,.woocommerce-cart' ) || document.body;
		var notices = document.querySelectorAll( '.frpdepot-fqj-cart-notice' );
		for ( var noticeIndex = 1; noticeIndex < notices.length; noticeIndex++ ) {
			notices[ noticeIndex ].remove();
		}
		if ( notices.length === 0 && host ) {
			host.insertBefore( makeCartNotice(), host.firstChild );
		}
		var buttons = document.querySelectorAll( '.frpdepot-fqj-cart-button' );
		for ( var buttonIndex = 1; buttonIndex < buttons.length; buttonIndex++ ) {
			buttons[ buttonIndex ].remove();
		}
		if ( buttons.length === 0 ) {
			var actionHost = document.querySelector( '.wc-block-cart__totals-title,.wc-block-components-totals-wrapper,.cart_totals' ) || host;
			var quoteButton = makeCartButton();
			if ( actionHost && quoteButton ) {
				actionHost.appendChild( quoteButton );
			}
		}
		var forbidden = [
			'.shipping-calculator-button',
			'.woocommerce-shipping-calculator',
			'.woocommerce-shipping-methods',
			'.wc-block-components-shipping-calculator',
			'.wc-block-components-shipping-rates-control',
			'.wc-block-cart__submit-container',
			'.wc-block-cart__submit-button',
			'a.checkout-button',
			'button.checkout-button',
			'.wc-block-checkout__actions_row',
			'.wc-block-components-payment-methods-icons',
			'.wc-block-checkout__payment-method'
		].join( ',' );
		document.querySelectorAll( forbidden ).forEach( function ( element ) {
			if ( element.classList.contains( 'frpdepot-fqj-cart-button' ) ) {
				return;
			}
			element.hidden = true;
			element.setAttribute( 'aria-hidden', 'true' );
			element.setAttribute( 'tabindex', '-1' );
			if ( 'disabled' in element ) {
				element.disabled = true;
			}
		} );
	}

	function bindCartChoiceA() {
		if ( config.cartQuoteRequired !== true || typeof window.MutationObserver !== 'function' ) {
			return;
		}
		enforceCartQuoteState();
		var observer = new window.MutationObserver( enforceCartQuoteState );
		observer.observe( document.documentElement, { childList: true, subtree: true } );
		window.setTimeout( function () {
			observer.disconnect();
		}, 20000 );
	}

	function consumeSuccessMarker( marker ) {
		if ( ! marker || marker.getAttribute( 'data-consumed' ) === '1' || ! Array.isArray( window.dataLayer ) ) {
			return;
		}
		var formId = String( marker.getAttribute( 'data-form-id' ) || '' );
		var entryId = String( marker.getAttribute( 'data-entry-id' ) || '' );
		var productId = String( marker.getAttribute( 'data-product-id' ) || '' );
		var variationId = String( marker.getAttribute( 'data-variation-id' ) || '' );
		var sourcePage = String( marker.getAttribute( 'data-source-page' ) || '' );
		var markerOwner = String( marker.getAttribute( 'data-marker' ) || '' );
		if ( ! decimalId( formId, false ) || formId !== String( config.formId || '' )
			|| ! decimalId( entryId, false ) || markerOwner !== String( config.formMarker || '' )
			|| ! idList( productId, false ) || ! idList( variationId, true )
			|| [ 'product', 'cart', 'contact', 'direct' ].indexOf( sourcePage ) === -1 ) {
			return;
		}
		var key = formId + ':' + entryId;
		var storageKey = 'frpdepot-fqj-generate-lead:' + key;
		var stored = false;
		try {
			stored = window.sessionStorage.getItem( storageKey ) === '1';
		} catch ( error ) {}
		if ( firedLeadKeys[ key ] || stored ) {
			marker.setAttribute( 'data-consumed', '1' );
			return;
		}
		try {
			window.dataLayer.push( {
				event: 'generate_lead',
				lead_type: 'freight_quote',
				form_id: formId,
				product_id: productId,
				variation_id: variationId,
				source_page: sourcePage
			} );
		} catch ( error ) {
			return; // A later bounded scan retries without marking this lead consumed.
		}
		firedLeadKeys[ key ] = true;
		try {
			window.sessionStorage.setItem( storageKey, '1' );
		} catch ( error ) {}
		marker.setAttribute( 'data-consumed', '1' );
	}

	function scanSuccessMarkers( root ) {
		if ( root && root.matches && root.matches( '.frpdepot-fq-success' ) ) {
			consumeSuccessMarker( root );
		}
		if ( root && root.querySelectorAll ) {
			root.querySelectorAll( '.frpdepot-fq-success' ).forEach( consumeSuccessMarker );
		}
	}

	function bindSuccessAnalytics() {
		scanSuccessMarkers( document );
		var retries = 0;
		var retryTimer = window.setInterval( function () {
			retries++;
			scanSuccessMarkers( document );
			if ( retries >= 40 || ! document.querySelector( '.frpdepot-fq-success:not([data-consumed="1"])' ) ) {
				window.clearInterval( retryTimer );
			}
		}, 250 );
		if ( $ ) {
			$( document ).on( 'gform_confirmation_loaded', function ( event, formId ) {
				if ( String( formId ) === String( config.formId || '' ) ) {
					scanSuccessMarkers( document );
				}
			} );
		}
		if ( typeof window.MutationObserver === 'function' ) {
			var observer = new window.MutationObserver( function ( mutations ) {
				mutations.forEach( function ( mutation ) {
					mutation.addedNodes.forEach( scanSuccessMarkers );
				} );
			} );
			observer.observe( document.documentElement, { childList: true, subtree: true } );
		}
	}

	function start() {
		bindProductChoiceA();
		bindCartChoiceA();
		bindSuccessAnalytics();
	}

	if ( window.__FRPDEPOT_FQJ_TEST__ === true ) {
		window.FRPDepotFreightQuoteJourneyTest = {
			positiveQuantity: positiveQuantity,
			decimalId: decimalId,
			idList: idList,
			productQuoteUrl: productQuoteUrl,
			bindProductChoiceA: bindProductChoiceA,
			consumeSuccessMarker: consumeSuccessMarker,
			enforceCartQuoteState: enforceCartQuoteState
		};
	}

	if ( document.readyState === 'loading' ) {
		document.addEventListener( 'DOMContentLoaded', start, { once: true } );
	} else {
		start();
	}
}( window, document, window.jQuery ) );
