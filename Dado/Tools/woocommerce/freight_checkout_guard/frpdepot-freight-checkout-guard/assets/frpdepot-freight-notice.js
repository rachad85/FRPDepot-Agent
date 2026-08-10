/**
 * FRP Depot Freight Checkout Guard -- Cart/Checkout Blocks notice.
 *
 * PURPOSE. Version 1.0.0 blocked checkout but the customer only ever saw
 * WooCommerce's own generic cart-errors wording. The PHP fix puts the required
 * sentence above that shell. This file covers the other Blocks case: when the
 * Cart or Checkout block DOES mount and renders its own notice area, so nothing
 * server-rendered is on the page.
 *
 * SCOPE. Deliberately tiny and fixed. It is enqueued by the plugin only on a
 * cart/checkout page whose cart the server has ALREADY decided is blocked. It:
 *
 *   - reads nothing and sends nothing anywhere: no request of any kind, no
 *     dynamic code, no cookie or storage access, no globals published;
 *   - writes exactly one <p>, whose text is set as text, never as markup;
 *   - inserts that <p> ONLY when the sentence is not already visible, and removes
 *     its own copy if WooCommerce later renders the same sentence itself, so the
 *     customer can never see it twice;
 *   - gives up after a fixed deadline and disconnects its observer.
 *
 * The source-closure tests in test-freight-guard.php and the Python suite assert
 * each of those properties by scanning this file, so keep it free of the
 * corresponding APIs -- including in comments.
 *
 * MESSAGE must stay byte-identical to FRPDEPOT_FCG_MESSAGE in the plugin PHP.
 * Both harnesses assert that too.
 */
( function () {
	'use strict';

	var MESSAGE = 'Contact us for a freight quote.';
	var MARKER = 'frpdepot-freight-quote';

	/* Where the message goes, most specific container first. */
	var HOSTS = [
		'.wp-block-woocommerce-checkout',
		'.wc-block-checkout',
		'.wp-block-woocommerce-cart',
		'.wc-block-cart',
		'.woocommerce-checkout',
		'.woocommerce-cart-form'
	];

	/* Places WooCommerce itself renders a blocking message. */
	var NATIVE_NOTICES = [
		'.wc-block-components-notice-banner',
		'.wc-block-store-notice',
		'.woocommerce-error',
		'.woocommerce-NoticeGroup',
		'.is-error'
	];

	/* Wait this long after the block appears before deciding WooCommerce is not
	   going to show the sentence itself. The Store API cart request resolves well
	   inside this window. */
	var SETTLE_MS = 1500;

	/* Hard stop, so a theme that never mounts the block cannot leave an observer
	   running for the life of the page. */
	var DEADLINE_MS = 20000;

	var observer = null;
	var settleTimer = null;
	var expiresAt = 0;
	var ours = null;

	function first( selectors ) {
		for ( var i = 0; i < selectors.length; i++ ) {
			var found = document.querySelector( selectors[ i ] );
			if ( found ) {
				return found;
			}
		}
		return null;
	}

	/** Is the sentence already on the page in something other than our own node? */
	function nativeMessageVisible() {
		for ( var i = 0; i < NATIVE_NOTICES.length; i++ ) {
			var nodes = document.querySelectorAll( NATIVE_NOTICES[ i ] );
			for ( var j = 0; j < nodes.length; j++ ) {
				var node = nodes[ j ];
				if ( ours && ( node === ours || node.contains( ours ) ) ) {
					continue;
				}
				if ( node.textContent && node.textContent.indexOf( MESSAGE ) !== -1 ) {
					return true;
				}
			}
		}
		return false;
	}

	/** Did the server already print our marker element? */
	function serverMessageVisible() {
		var existing = document.querySelectorAll( '.' + MARKER );
		for ( var i = 0; i < existing.length; i++ ) {
			if ( existing[ i ] !== ours ) {
				return true;
			}
		}
		return false;
	}

	function stop() {
		if ( observer ) {
			observer.disconnect();
			observer = null;
		}
		if ( settleTimer ) {
			window.clearTimeout( settleTimer );
			settleTimer = null;
		}
	}

	function removeOurs() {
		if ( ours && ours.parentNode ) {
			ours.parentNode.removeChild( ours );
		}
		ours = null;
	}

	function insert( host ) {
		var note = document.createElement( 'p' );
		note.className = MARKER;
		note.setAttribute( 'role', 'alert' );
		note.textContent = MESSAGE;
		host.insertBefore( note, host.firstChild );
		ours = note;
	}

	/**
	 * One evaluation pass.
	 *
	 * Before our copy exists: insert only if the block has mounted and neither
	 * WooCommerce nor the server has shown the sentence.
	 * After our copy exists: withdraw it the moment WooCommerce shows its own.
	 */
	function evaluate() {
		if ( ours ) {
			if ( nativeMessageVisible() || serverMessageVisible() ) {
				removeOurs();
				stop();
			}
			return;
		}

		if ( nativeMessageVisible() || serverMessageVisible() ) {
			stop();
			return;
		}

		var host = first( HOSTS );
		if ( ! host ) {
			return;
		}

		insert( host );
	}

	function onMutation() {
		if ( Date.now() > expiresAt ) {
			stop();
			return;
		}
		evaluate();
	}

	function begin() {
		expiresAt = Date.now() + DEADLINE_MS;

		if ( nativeMessageVisible() || serverMessageVisible() ) {
			return; // The message is already in front of the customer.
		}

		if ( typeof window.MutationObserver === 'function' && document.body ) {
			observer = new window.MutationObserver( onMutation );
			observer.observe( document.body, { childList: true, subtree: true } );
		}

		/* Let the block render and its own notices settle before adding anything. */
		settleTimer = window.setTimeout( function () {
			settleTimer = null;
			evaluate();
		}, SETTLE_MS );

		window.setTimeout( stop, DEADLINE_MS );
	}

	if ( document.readyState === 'loading' ) {
		document.addEventListener( 'DOMContentLoaded', begin );
	} else {
		begin();
	}
}() );
