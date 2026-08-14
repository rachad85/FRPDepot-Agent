(function ($) {
    'use strict';

    var cfg = window.frpdepotFqJourney || {};
    var panel = document.querySelector('.frpdepot-fq-product-panel');
    var button = panel && panel.querySelector('.frpdepot-fq-product-button');
    var panelQty = panel && panel.querySelector('.frpdepot-fq-quantity');
    var cartForm = document.querySelector('form.cart');
    var addButton = cartForm && cartForm.querySelector('.single_add_to_cart_button');
    var selectedVariationId = 0;

    function sameOriginUrl(raw) {
        var url = new URL(raw, window.location.origin);
        if (url.origin !== window.location.origin) {
            throw new Error('FRP quote journey refused a cross-origin URL');
        }
        return url;
    }

    function requestedQuantity() {
        var nativeQty = cartForm && cartForm.querySelector('input.qty');
        var raw = panelQty ? panelQty.value : (nativeQty ? nativeQty.value : '1');
        var quantity = parseInt(raw, 10);
        if (!Number.isFinite(quantity) || quantity < 1) {
            quantity = 1;
        }
        return Math.min(quantity, 1000);
    }

    function refreshQuoteUrl() {
        if (!button || !cfg.quoteUrl || !cfg.productId) {
            return;
        }
        var url = sameOriginUrl(cfg.quoteUrl);
        url.searchParams.set('frp_fq_source', 'product');
        url.searchParams.set('product_id', String(cfg.productId));
        url.searchParams.set('variation_id', String(selectedVariationId || 0));
        url.searchParams.set('quantity', String(requestedQuantity()));
        button.href = url.href;
    }

    function setQuoteState(requiresQuote, variationId) {
        if (!panel || !button) {
            return;
        }
        selectedVariationId = variationId || 0;
        panel.classList.toggle('frpdepot-fq-hidden', !requiresQuote);
        button.setAttribute('aria-disabled', requiresQuote ? 'false' : 'true');
        button.tabIndex = requiresQuote ? 0 : -1;
        if (addButton) {
            addButton.classList.toggle('frpdepot-fq-hidden-by-journey', requiresQuote);
            addButton.setAttribute('aria-hidden', requiresQuote ? 'true' : 'false');
            if (requiresQuote) {
                addButton.disabled = true;
            }
        }
        refreshQuoteUrl();
    }

    if (panelQty) {
        panelQty.addEventListener('input', refreshQuoteUrl);
        panelQty.addEventListener('change', refreshQuoteUrl);
    }
    if (cartForm) {
        var nativeQty = cartForm.querySelector('input.qty');
        if (nativeQty) {
            nativeQty.addEventListener('input', function () {
                if (panelQty) {
                    panelQty.value = nativeQty.value;
                }
                refreshQuoteUrl();
            });
        }
        $(cartForm).on('found_variation', function (event, variation) {
            var blocked = !!(variation && variation.frpdepot_fq_requires_quote === true);
            setQuoteState(blocked, blocked ? parseInt(variation.variation_id, 10) || 0 : 0);
        });
        $(cartForm).on('reset_data hide_variation', function () {
            setQuoteState(false, 0);
        });
    }
    refreshQuoteUrl();

    function applyCartState(blocked) {
        document.body.classList.toggle('frpdepot-fq-cart-blocked', !!blocked);
    }

    function fetchCartState() {
        if (!cfg.cartStateUrl || !cfg.cartNonce) {
            return;
        }
        var url;
        try {
            url = sameOriginUrl(cfg.cartStateUrl);
        } catch (error) {
            return;
        }
        var body = new URLSearchParams();
        body.set('action', 'frpdepot_fq_cart_state');
        body.set('nonce', cfg.cartNonce);
        window.fetch(url.href, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'},
            body: body.toString()
        }).then(function (response) {
            return response.ok ? response.json() : null;
        }).then(function (json) {
            if (json && json.success && json.data && typeof json.data.quote_required === 'boolean') {
                applyCartState(json.data.quote_required);
            }
        }).catch(function () {});
    }

    applyCartState(!!cfg.cartBlocked);
    if (panel) {
        setQuoteState(!panel.classList.contains('frpdepot-fq-hidden'), 0);
    } else {
        refreshQuoteUrl();
    }
    $(document.body).on('updated_cart_totals updated_wc_div wc_fragments_refreshed', fetchCartState);
}(window.jQuery));
