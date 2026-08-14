(function () {
    'use strict';

    var allowedKeys = ['lead_type', 'form_id', 'product_id', 'variation_id', 'source_page'];

    function fire(marker) {
        if (!marker || marker.getAttribute('data-frpdepot-fq-fired') === '1') {
            return;
        }
        var formId = parseInt(marker.getAttribute('data-form-id') || '0', 10) || 0;
        var productId = parseInt(marker.getAttribute('data-product-id') || '0', 10) || 0;
        var variationId = parseInt(marker.getAttribute('data-variation-id') || '0', 10) || 0;
        var sourcePage = marker.getAttribute('data-source-page') || '';
        var successKey = marker.getAttribute('data-success-key') || '';
        if (!formId || !successKey || !sourcePage || typeof window.gtag !== 'function') {
            return;
        }
        var dedupeKey = 'frpdepot-fq-lead:' + successKey;
        try {
            if (window.sessionStorage.getItem(dedupeKey) === '1') {
                marker.setAttribute('data-frpdepot-fq-fired', '1');
                return;
            }
        } catch (error) {}

        var params = {
            lead_type: 'freight_quote',
            form_id: formId,
            product_id: productId,
            variation_id: variationId,
            source_page: sourcePage
        };
        if (Object.keys(params).sort().join('|') !== allowedKeys.slice().sort().join('|')) {
            return;
        }
        window.gtag('event', 'generate_lead', params);
        marker.setAttribute('data-frpdepot-fq-fired', '1');
        try {
            window.sessionStorage.setItem(dedupeKey, '1');
        } catch (error) {}
    }

    function scan(root) {
        if (root && root.matches && root.matches('.frpdepot-fq-success')) {
            fire(root);
        }
        if (root && root.querySelectorAll) {
            root.querySelectorAll('.frpdepot-fq-success').forEach(fire);
        }
    }

    function start() {
        scan(document);
        new MutationObserver(function (mutations) {
            mutations.forEach(function (mutation) {
                mutation.addedNodes.forEach(scan);
            });
        }).observe(document.documentElement, {childList: true, subtree: true});
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start, {once: true});
    } else {
        start();
    }
}());
