'use strict';

const fs = require('fs');
const vm = require('vm');
const path = require('path');

let passes = 0;
let failures = 0;
function check(condition, label) {
  if (condition) {
    passes += 1;
  } else {
    failures += 1;
    process.stderr.write(`FAIL: ${label}\n`);
  }
}
function same(expected, actual, label) {
  check(JSON.stringify(actual) === JSON.stringify(expected), `${label} expected=${JSON.stringify(expected)} actual=${JSON.stringify(actual)}`);
}

class FakeClassList {
  constructor(owner) { this.owner = owner; }
  contains(name) { return String(this.owner.className || '').split(/\s+/).includes(name); }
}
class FakeElement {
  constructor(tag = 'div') {
    this.tagName = String(tag).toUpperCase();
    this.attributes = Object.create(null);
    this.children = [];
    this.listeners = Object.create(null);
    this.hidden = false;
    this.disabled = false;
    this.className = '';
    this.textContent = '';
    this.value = '';
    this.firstChild = null;
    this.removed = false;
    this.classList = new FakeClassList(this);
  }
  get href() { return this.attributes.href || ''; }
  set href(value) { this.attributes.href = String(value); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
  removeAttribute(name) { delete this.attributes[name]; }
  appendChild(child) { this.children.push(child); this.firstChild = this.children[0] || null; return child; }
  insertBefore(child) { this.children.unshift(child); this.firstChild = this.children[0] || null; return child; }
  remove() { this.removed = true; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  closest(selector) { return this.matches(selector) ? this : null; }
  matches(selector) { return selector === '.frpdepot-fq-success' && this.classList.contains('frpdepot-fq-success'); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
}

const jqueryHandlers = new Map();
function jquery(element) {
  return {
    on(names, callback) {
      String(names).split(/\s+/).forEach((name) => jqueryHandlers.set(`${element._id || 'document'}:${name}`, callback));
    }
  };
}

const documentListeners = Object.create(null);
const document = {
  readyState: 'loading',
  documentElement: new FakeElement('html'),
  body: new FakeElement('body'),
  createElement: (tag) => new FakeElement(tag),
  createTextNode(text) { const node = new FakeElement('#text'); node.textContent = String(text); return node; },
  addEventListener(name, callback) { documentListeners[name] = callback; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  matches() { return false; }
};
document._id = 'document';

const window = {
  __FRPDEPOT_FQJ_TEST__: true,
  FRPDepotFreightQuoteJourney: {
    quoteUrl: 'https://frpdepots.com/request-a-quote/?stale=1#stale',
    cartQuoteUrl: 'https://frpdepots.com/request-a-quote/?fqj_source=cart',
    cartQuoteRequired: false,
    button: 'Request a Freight Quote',
    cartHeading: 'Freight quote required',
    cartText: 'One or more items in this cart require a product and freight quote.',
    formId: 81,
    formMarker: 'FRPDEPOT_FQJ_FIXED_FORM_V2_SPEC_5348EF3F'
  },
  URL,
  location: { origin: 'https://frpdepots.com' },
  jQuery: jquery,
  dataLayer: [],
  setTimeout(callback) { callback(); },
  MutationObserver: function MutationObserver() { this.observe = () => {}; this.disconnect = () => {}; }
};

const context = vm.createContext({ window, document, URL, console });
const sourcePath = path.resolve(__dirname, '../frpdepot-freight-checkout-guard/assets/frpdepot-freight-quote-journey.js');
vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context, { filename: sourcePath });
const api = window.FRPDepotFreightQuoteJourneyTest;
check(api && typeof api.productQuoteUrl === 'function', 'test-only API is exposed only under explicit harness flag');

/* Strict scalar grammar. */
for (const value of ['1', '9999']) check(api.positiveQuantity(value), `valid quantity ${value}`);
for (const value of ['', '0', '+1', '-1', '1.2', '0001', '10000']) check(!api.positiveQuantity(value), `invalid quantity ${value}`);
for (const value of ['1', '9999999999']) check(api.decimalId(value, false), `valid positive ID ${value}`);
for (const value of ['', '0', '+1', '-1', '1.2', '0001', '10000000000']) check(!api.decimalId(value, false), `invalid positive ID ${value}`);
check(api.decimalId('0', true), 'zero accepted only for variation list grammar');
check(api.idList('1455,1423', false), 'canonical product ID list accepted');
check(api.idList('2455,0', true), 'canonical variation ID list accepts zero');
for (const value of ['01', '1,,2', '1,+2', '10000000000']) check(!api.idList(value, false), `invalid ID list ${value}`);

/* Complete closed product URL. */
function productForm(quantity, values) {
  const qty = new FakeElement('input'); qty.value = quantity;
  const selectors = values.map((value) => { const select = new FakeElement('select'); select.value = value; return select; });
  return {
    querySelector(selector) { return selector === 'input.qty' ? qty : null; },
    querySelectorAll(selector) { return selector === '.variations select[name^="attribute_"]' ? selectors : []; }
  };
}
const validUrl = new URL(api.productQuoteUrl(productForm('3', ['4-in', '150-psi', 'vinyl-ester']), {
  frpdepot_product_id: 1455,
  frpdepot_variation_id: 2455
}));
same('https://frpdepots.com', validUrl.origin, 'product URL remains same-origin');
same('', validUrl.hash, 'product URL drops stale fragment');
same(['fqj_product_id', 'fqj_quantity', 'fqj_source', 'fqj_variation_id'].sort(), Array.from(validUrl.searchParams.keys()).sort(), 'product URL has only closed fqj keys');
same('product', validUrl.searchParams.get('fqj_source'), 'product URL source exact');
same('1455', validUrl.searchParams.get('fqj_product_id'), 'product URL product ID exact');
same('2455', validUrl.searchParams.get('fqj_variation_id'), 'product URL variation ID exact');
same('3', validUrl.searchParams.get('fqj_quantity'), 'product URL quantity exact');
check(!api.productQuoteUrl(productForm('0', ['4-in', '150-psi', 'vinyl-ester']), { frpdepot_product_id: 1455, frpdepot_variation_id: 2455 }), 'invalid quantity disables product URL');
check(!api.productQuoteUrl(productForm('1', ['4-in', '', 'vinyl-ester']), { frpdepot_product_id: 1455, frpdepot_variation_id: 2455 }), 'missing selector disables product URL');
check(!api.productQuoteUrl(productForm('1', ['4-in', '150-psi', 'vinyl-ester']), { frpdepot_product_id: '0001455', frpdepot_variation_id: 2455 }), 'malformed server ID disables product URL');

/* Product Choice-A state machine. */
const panel = new FakeElement('section'); panel.className = 'frpdepot-fqj-product';
const quote = new FakeElement('a'); quote.className = 'frpdepot-fqj-product-button';
panel.querySelector = (selector) => selector === '.frpdepot-fqj-product-button' ? quote : null;
const native = new FakeElement('button'); native.className = 'single_add_to_cart_button'; native.disabled = true;
const selectors = ['4-in', '150-psi', 'vinyl-ester'].map((value) => { const item = new FakeElement('select'); item.value = value; return item; });
const qty = new FakeElement('input'); qty.value = '2';
const variationForm = new FakeElement('form'); variationForm._id = 'variation-form';
variationForm.querySelector = (selector) => selector === '.single_add_to_cart_button' ? native : (selector === 'input.qty' ? qty : null);
variationForm.querySelectorAll = (selector) => selector === '.variations select[name^="attribute_"]' ? selectors : [];
document.querySelector = (selector) => selector === '.frpdepot-fqj-product' ? panel : (selector === 'form.variations_form' ? variationForm : null);
api.bindProductChoiceA();
check(panel.hidden && quote.getAttribute('href') === null, 'unresolved product starts with hidden panel and no active quote URL');
check(native.disabled, 'unresolved product preserves native Woo disabled state');
const found = jqueryHandlers.get('variation-form:found_variation');
check(typeof found === 'function', 'found_variation handler registered');
found({}, { frpdepot_quote_required: true, frpdepot_product_id: 1455, frpdepot_variation_id: 2455 });
check(!panel.hidden && native.hidden && native.disabled, 'blocked variation shows quote panel and disables native purchase');
check(quote.href.includes('fqj_source=product') && quote.getAttribute('aria-disabled') === null, 'complete blocked variation enables quote CTA');
found({}, { frpdepot_quote_required: false, frpdepot_product_id: 1455, frpdepot_variation_id: 2455 });
check(panel.hidden && !native.hidden && native.disabled, 'allowlisted variation restores exact original native state and hides quote panel');
check(quote.getAttribute('href') === null, 'allowlisted transition clears stale quote href');
found({}, { frpdepot_quote_required: 'false', frpdepot_product_id: 1455, frpdepot_variation_id: 2455 });
check(!panel.hidden && native.hidden && native.disabled && quote.getAttribute('href') === null, 'malformed decision fails closed with disabled quote CTA');
const reset = jqueryHandlers.get('variation-form:reset_data');
reset();
check(panel.hidden && quote.getAttribute('href') === null && !native.hidden && native.disabled, 'reset clears stale quote state and restores native unresolved state');

/* Cart controls: exactly one notice/CTA, forbidden controls disabled, line control retained. */
const host = new FakeElement('main');
const actionHost = new FakeElement('section');
const shipping = new FakeElement('button'); shipping.className = 'shipping-calculator-button';
const checkout = new FakeElement('a'); checkout.className = 'checkout-button';
const lineRemove = new FakeElement('a'); lineRemove.className = 'remove';
const cartNotices = [];
const cartButtons = [];
document.querySelector = (selector) => {
  if (selector === '.wp-block-woocommerce-cart,.wc-block-cart,.woocommerce-cart') return host;
  if (selector === '.wc-block-cart__totals-title,.wc-block-components-totals-wrapper,.cart_totals') return actionHost;
  return null;
};
document.querySelectorAll = (selector) => {
  if (selector === '.frpdepot-fqj-cart-notice') return cartNotices;
  if (selector === '.frpdepot-fqj-cart-button') return cartButtons;
  if (selector.includes('.shipping-calculator-button')) return [shipping, checkout];
  return [];
};
const originalHostInsert = host.insertBefore.bind(host);
host.insertBefore = (child) => { cartNotices.push(child); return originalHostInsert(child); };
const originalActionAppend = actionHost.appendChild.bind(actionHost);
actionHost.appendChild = (child) => { cartButtons.push(child); return originalActionAppend(child); };
window.FRPDepotFreightQuoteJourney.cartQuoteRequired = true;
api.enforceCartQuoteState();
api.enforceCartQuoteState();
same(1, cartNotices.length, 'Blocks rerender leaves exactly one cart notice');
same(1, cartButtons.length, 'Blocks rerender leaves exactly one quote CTA');
check(shipping.hidden && shipping.disabled && checkout.hidden, 'shipping and checkout controls are unusable');
check(!lineRemove.hidden && !lineRemove.disabled, 'cart line removal control remains untouched');
same('https://frpdepots.com/request-a-quote/?fqj_source=cart', cartButtons[0].href, 'cart quote CTA has fixed same-origin closed URL');

/* Success marker: one exact six-key event, in-memory latch, malformed markers ignored. */
function marker(attributes) {
  const item = new FakeElement('span'); item.className = 'frpdepot-fq-success';
  Object.entries(attributes).forEach(([key, value]) => item.setAttribute(key, value));
  return item;
}
const success = marker({
  'data-form-id': '81',
  'data-entry-id': '900',
  'data-product-id': '1455,1423',
  'data-variation-id': '2455,0',
  'data-source-page': 'cart',
  'data-marker': 'FRPDEPOT_FQJ_FIXED_FORM_V2_SPEC_5348EF3F'
});
api.consumeSuccessMarker(success);
api.consumeSuccessMarker(success);
same(1, window.dataLayer.length, 'same success marker pushes once');
same(['event', 'lead_type', 'form_id', 'product_id', 'variation_id', 'source_page'], Object.keys(window.dataLayer[0]), 'analytics event has exact six-key order');
same({
  event: 'generate_lead',
  lead_type: 'freight_quote',
  form_id: '81',
  product_id: '1455,1423',
  variation_id: '2455,0',
  source_page: 'cart'
}, window.dataLayer[0], 'analytics payload values exact and non-PII');
same('1', success.getAttribute('data-consumed'), 'success marker latched in DOM');
const duplicateEntry = marker({
  'data-form-id': '81', 'data-entry-id': '900', 'data-product-id': '1455', 'data-variation-id': '2455',
  'data-source-page': 'product', 'data-marker': 'FRPDEPOT_FQJ_FIXED_FORM_V2_SPEC_5348EF3F'
});
api.consumeSuccessMarker(duplicateEntry);
same(1, window.dataLayer.length, 'AJAX rerender with same form/entry is deduplicated in memory');
for (const bad of [
  { 'data-form-id': '82', 'data-entry-id': '901', 'data-product-id': '1455', 'data-variation-id': '2455', 'data-source-page': 'product', 'data-marker': 'FRPDEPOT_FQJ_FIXED_FORM_V2_SPEC_5348EF3F' },
  { 'data-form-id': '81', 'data-entry-id': '0', 'data-product-id': '1455', 'data-variation-id': '2455', 'data-source-page': 'product', 'data-marker': 'FRPDEPOT_FQJ_FIXED_FORM_V2_SPEC_5348EF3F' },
  { 'data-form-id': '81', 'data-entry-id': '902', 'data-product-id': '1455', 'data-variation-id': '2455', 'data-source-page': 'evil', 'data-marker': 'FRPDEPOT_FQJ_FIXED_FORM_V2_SPEC_5348EF3F' },
  { 'data-form-id': '81', 'data-entry-id': '903', 'data-product-id': '0001455', 'data-variation-id': '2455', 'data-source-page': 'product', 'data-marker': 'FRPDEPOT_FQJ_FIXED_FORM_V2_SPEC_5348EF3F' },
  { 'data-form-id': '81', 'data-entry-id': '904', 'data-product-id': '1455', 'data-variation-id': '2455', 'data-source-page': 'product', 'data-marker': 'wrong-owner' }
]) api.consumeSuccessMarker(marker(bad));
same(1, window.dataLayer.length, 'wrong form/entry/source/grammar/owner produce zero events');

process.stdout.write(`passes=${passes} failures=${failures}\n`);
process.exit(failures ? 1 : 0);
