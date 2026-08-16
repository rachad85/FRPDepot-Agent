#!/usr/bin/env python3
"""Fixed, fail-closed live acceptance harness for the freight quote journey.

DO NOT run this module against production as part of local/offline verification.
The default ``plan`` command only prints the closed interface and does not import
Playwright or attach to a browser.  ``run`` is the sole live command.  It:

* attaches only to the existing loopback CDP endpoint on port 9229;
* holds the shared ``wordpress`` UI lane lock for the complete run;
* creates exactly three fresh anonymous browser contexts;
* permits only fixed GET/HEAD public/admin navigation;
* records dataLayer and analytics transport payloads;
* rejects every non-GET request except exactly two explicitly enabled,
  same-origin Gravity Forms attempts: one validation failure and one success;
* reads the WooCommerce order count before and after; and
* fails if any payment-like request is observed.

No URL, selector, marker, product, form, payload, or action is caller supplied.
The controlled sequence requires the exact fixed test marker as the CLI enable
value and in the visible notes field.  Only the second attempt may create a real
Gravity Forms entry and route one real notification, so it is disabled unless
explicitly enabled.
"""
from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterator, Mapping, Protocol, Sequence
from urllib.parse import parse_qsl, quote, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent / "common"))
try:
    from ui_lane_lock import ui_browser_lock
except ImportError:  # Import remains possible in isolated harness unit tests.
    ui_browser_lock = None  # type: ignore[assignment]

SPECIFICATION_SHA256 = "5348ef3f357676f5629cf72696fd3fe0be718a3847854974f20cf28cc7047400"
SCHEMA_VERSION = 1
ORIGIN = "https://frpdepots.com"
HOST = "frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"
LOCK_LANE = "wordpress"
FORM_MARKER = "FRPDEPOT_FQJ_FIXED_FORM_V2_SPEC_5348EF3F"
TEST_MARKER = "FRPDEPOT-FQJ-ACCEPTANCE-20260813"
STATUS_MARKER = "#frpdepot-fqj-status"
FORM_MARKER_STATUS_FIELD = "form_owned"

PRODUCT_URLS = {
    1455: ORIGIN + "/product/frp-fw-pipe/",
    1423: ORIGIN + "/product/frp-elbow-90/",
    1368: ORIGIN + "/product/frp-stub-flange/",
    1397: ORIGIN + "/product/frp-manway/",
    1411: ORIGIN + "/product/frp-manway-cover/",
}
HOME_URL = ORIGIN + "/"
CART_URL = ORIGIN + "/cart/"
CHECKOUT_URL = ORIGIN + "/checkout/"
QUOTE_URL = ORIGIN + "/request-a-quote/"
CONTACT_URL = ORIGIN + "/contact/"
STATUS_URL = ORIGIN + "/wp-admin/tools.php?page=frpdepot-freight-quote-journey"
ORDER_COUNT_URL = ORIGIN + "/wp-admin/admin.php?page=wc-orders&status=all"
ACCEPTANCE_ATTEMPT_PATH = HERE.parent.parent / "20_Working" / "freight_quote_journey_acceptance_attempt.json"
OFFLINE_PROOF_PATH = HERE.parent.parent / "20_Working" / "freight_quote_journey_offline_proof.json"
OFFLINE_PROOF_ARTIFACT = HERE / "freight_quote_journey" / "frpdepot-freight-checkout-guard-2.0.2.zip"
OFFLINE_PROOF_FILES = {
    "plugin_php": HERE / "freight_quote_journey" / "frpdepot-freight-checkout-guard" / "frpdepot-freight-checkout-guard.php",
    "plugin_php_tests": HERE / "freight_quote_journey" / "tests" / "test-freight-quote-journey.php",
    "plugin_javascript": HERE / "freight_quote_journey" / "frpdepot-freight-checkout-guard" / "assets" / "frpdepot-freight-quote-journey.js",
    "plugin_javascript_tests": HERE / "freight_quote_journey" / "tests" / "test-freight-quote-journey.js",
    "deployment_tool": HERE / "wordpress_freight_quote_journey_tool.py",
    "deployment_tool_tests": HERE / "test_wordpress_freight_quote_journey_tool.py",
    "live_acceptance": HERE / "freight_quote_journey_acceptance_live.py",
    "live_acceptance_tests": HERE / "test_freight_quote_journey_acceptance_live.py",
    "offline_model": HERE / "freight_quote_journey_acceptance_offline.py",
    "offline_acceptance_tests": HERE / "test_freight_quote_journey_acceptance_offline.py",
}
FIXED_URLS = frozenset({
    HOME_URL, CART_URL, CHECKOUT_URL, QUOTE_URL, CONTACT_URL, STATUS_URL,
    ORDER_COUNT_URL, *PRODUCT_URLS.values(),
})
FIXED_PUBLIC_URLS = frozenset({
    HOME_URL, CART_URL, CHECKOUT_URL, QUOTE_URL, CONTACT_URL, *PRODUCT_URLS.values(),
})
FIXED_ADMIN_URLS = frozenset({STATUS_URL, ORDER_COUNT_URL})

VIEWPORTS = {
    "desktop": {"width": 1440, "height": 1000},
    "mobile": {"width": 390, "height": 844},
    "cart": {"width": 1440, "height": 1000},
}

CHOICE_A_HEADING = "Freight quote required"
CHOICE_A_TEXT = (
    "This selection is not available for direct online checkout. Choose the size, "
    "pressure rating, resin type and quantity, then request a product and freight "
    "quote. No payment will be taken."
)
QUOTE_BUTTON = "Request a Freight Quote"
QUOTE_H1 = "Request a Product and Freight Quote"
QUOTE_INTRO = (
    "Send the selected product details and delivery destination below. FRP Depots will review "
    "product availability, packing and freight requirements before providing a complete quote. "
    "Submitting this form does not place an order or authorize payment."
)
SUBMIT_BUTTON = "Request Quote"
CONTACT_REPLACEMENT = (
    "Product selections approved for direct shipping can be purchased online. "
    "Selections requiring packing or freight review will show Request a Freight "
    "Quote. Submitting a quote request does not place an order or authorize payment."
)

ANALYTICS_KEYS = frozenset({
    "event", "lead_type", "form_id", "product_id", "variation_id", "source_page",
})
PII_KEY_FRAGMENTS = (
    "name", "email", "phone", "company", "postal", "zip", "notes", "message",
    "upload", "filename", "cart", "address", "country",
)
ANALYTICS_HOST_FRAGMENTS = (
    "google-analytics.com", "analytics.google.com", "googletagmanager.com",
    "/g/collect", "/collect",
)
PAYMENT_URL_FRAGMENTS = (
    "/checkout/order-pay/", "wc-ajax=checkout", "/wc/store/v1/checkout",
    "/payment", "/payments", "/authorize", "/capture", "stripe.com/v1/",
    "paypal.com/v2/checkout/orders",
)
SUBMISSION_PATHS = frozenset({"/request-a-quote/", "/wp-admin/admin-ajax.php"})
ALLOWED_SUBMISSION_METHODS = frozenset({"POST"})

ORDER_COUNT_SELECTORS = (
    ".subsubsub .all .count",
    "a[href*='status=all'] .count",
    ".wc-orders-list-table .displaying-num",
)

PRODUCT_PANEL = ".frpdepot-fqj-product"
PRODUCT_BUTTON = ".frpdepot-fqj-product-button"
ADD_TO_CART_BUTTON = "button.single_add_to_cart_button"
VARIATION_FORM = "form.variations_form"
CART_NOTICE = ".frpdepot-fqj-cart-notice"
CART_QUOTE_BUTTON = ".frpdepot-fqj-cart-button"
SHIPPING_SELECTORS = (
    ".shipping-calculator-button", ".woocommerce-shipping-calculator",
    ".wc-block-components-shipping-calculator", ".woocommerce-shipping-totals",
    ".wc-block-components-totals-shipping",
)
CHECKOUT_SELECTORS = (
    ".checkout-button:not(.frpdepot-fqj-cart-button)",
    ".wc-block-cart__submit-button", ".wc-block-cart__submit-container",
)
PAYMENT_SELECTORS = (
    ".woocommerce-checkout-payment", "#payment", ".wc-block-checkout__payment-method",
)

FORM_FIELD_IDS = {
    "first_name": 1,
    "last_name": 2,
    "email": 3,
    "company": 4,
    "phone": 5,
    "product": 6,
    "size": 7,
    "pressure_rating": 8,
    "resin_type": 9,
    "quantity": 10,
    "country": 11,
    "postal": 12,
    "notes": 13,
    "product_url": 17,
    "product_id": 18,
    "variation_id": 19,
    "source_page": 20,
    "cart_projection": 21,
}
CONTROLLED_VALUES = {
    "first_name": "Acceptance",
    "last_name": "Fixture",
    "email": "fqj-acceptance@example.invalid",
    "company": "FRP Depot Acceptance Fixture",
    "phone": "",
    "product": "FRP FW Pipe acceptance fixture",
    "size": "4 in",
    "pressure_rating": "150 psi",
    "resin_type": "Vinyl ester",
    "quantity": "1",
    "country": "CA",
    "postal": "A1A 1A1",
    "notes": TEST_MARKER,
}
CUSTOMER_FILL_FIELDS = frozenset({
    "first_name", "last_name", "email", "company", "phone", "country", "postal", "notes",
})
SERVER_HANDOFF_FIELDS = frozenset({
    "product", "size", "pressure_rating", "resin_type", "quantity", "product_url",
    "product_id", "variation_id", "source_page", "cart_projection",
})

INIT_ANALYTICS_CAPTURE = r"""
(() => {
  const captured = [];
  Object.defineProperty(window, '__frpFqjCapturedDataLayer', {
    value: captured, configurable: false, enumerable: false, writable: false
  });
  const layer = window.dataLayer = window.dataLayer || [];
  const original = layer.push.bind(layer);
  layer.push = function(...items) {
    for (const item of items) {
      try { captured.push(JSON.parse(JSON.stringify(item))); } catch (_) { captured.push(null); }
    }
    return original(...items);
  };
})();
"""


class HarnessRefusal(RuntimeError):
    """Fail-closed safety or acceptance refusal without page/customer content."""


@dataclass(frozen=True)
class TestResult:
    id: int
    name: str
    status: str
    evidence: Mapping[str, Any]


@dataclass
class RequestRecorder:
    analytics_transports: list[dict[str, str]] = field(default_factory=list)
    payment_requests: list[dict[str, str]] = field(default_factory=list)
    non_read_requests: list[dict[str, str]] = field(default_factory=list)
    submission_count: int = 0
    submission_enabled: bool = False
    submission_window: bool = False
    submission_phase: str = ""

    @staticmethod
    def _safe_post_data(request: Any) -> str:
        """Return text payloads without letting compressed/binary telemetry escape the guard.

        Playwright's sync ``request.post_data`` property decodes CDP's base64 bytes
        as UTF-8. Some browser telemetry payloads are compressed binary, so reading
        that property raises ``UnicodeDecodeError``. An unreadable payload must be
        treated as empty for marker matching; its non-read method is still recorded
        and aborted by the default-deny route below.
        """
        try:
            return str(request.post_data or "")
        except (UnicodeDecodeError, TypeError, ValueError):
            return ""

    @classmethod
    def _record(cls, request: Any) -> dict[str, str]:
        return {
            "method": str(request.method).upper(),
            "url": str(request.url),
            "post_data": cls._safe_post_data(request),
        }

    def observe(self, request: Any) -> dict[str, str]:
        record = self._record(request)
        method = record["method"]
        lowered_url = record["url"].casefold()
        lowered_body = record["post_data"].casefold()
        if any(fragment in lowered_url for fragment in ANALYTICS_HOST_FRAGMENTS):
            self.analytics_transports.append(record)
        # Read-only payment-related scripts, fonts and configuration endpoints
        # are ordinary storefront resources.  A payment callback/attempt is a
        # non-read request; those remain recorded and blocked below.
        if method not in {"GET", "HEAD", "OPTIONS"} and (
            any(fragment in lowered_url for fragment in PAYMENT_URL_FRAGMENTS)
            or "payment_method" in lowered_body
            or "card_number" in lowered_body
        ):
            self.payment_requests.append(record)
        if method not in {"GET", "HEAD", "OPTIONS"}:
            self.non_read_requests.append(record)
        return record

    def allow_or_abort(self, route: Any) -> None:
        request = route.request
        record = self.observe(request)
        method = str(request.method).upper()
        if method in {"GET", "HEAD", "OPTIONS"}:
            route.continue_()
            return
        parsed = urlsplit(str(request.url))
        permitted = (
            self.submission_enabled
            and self.submission_window
            and self.submission_count < 2
            and self.submission_phase in {"validation_failure", "success"}
            and (
                (self.submission_count == 0 and self.submission_phase == "validation_failure")
                or (self.submission_count == 1 and self.submission_phase == "success")
            )
            and parsed.scheme == "https"
            and parsed.hostname == HOST
            and parsed.port is None
            and parsed.path in SUBMISSION_PATHS
            and method in ALLOWED_SUBMISSION_METHODS
            and TEST_MARKER in record["post_data"]
            and not self.payment_requests
        )
        if not permitted:
            route.abort("blockedbyclient")
            return
        self.submission_count += 1
        route.continue_()


class Adapter(Protocol):
    recorder: RequestRecorder

    def read_status(self) -> Mapping[str, Any]: ...
    def read_order_count(self) -> int: ...
    def product_observation(self, product_id: int, viewport: str) -> Mapping[str, Any]: ...
    def future_allowlisted_observation(self) -> Mapping[str, Any]: ...
    def product_handoff(self) -> Mapping[str, Any]: ...
    def cart_observation(self) -> Mapping[str, Any]: ...
    def quote_form_observation(self) -> Mapping[str, Any]: ...
    def contact_observation(self) -> Mapping[str, Any]: ...
    def controlled_submit(self) -> Mapping[str, Any]: ...
    def analytics_events(self) -> Sequence[Mapping[str, Any]]: ...


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _visible(page: Any, selector: str) -> bool:
    locator = page.locator(selector)
    return locator.count() > 0 and any(locator.nth(i).is_visible() for i in range(locator.count()))


def _text(page: Any, selector: str) -> str:
    locator = page.locator(selector)
    return "" if locator.count() != 1 else str(locator.inner_text()).strip()


def _assert_fixed_url(actual: str, expected: str) -> None:
    if expected not in FIXED_URLS or actual.rstrip("/") != expected.rstrip("/"):
        raise HarnessRefusal("browser did not remain on the one expected fixed URL")


def _count_text(raw: str) -> int:
    match = re.search(r"([0-9][0-9,]*)", raw)
    if not match:
        raise HarnessRefusal("WooCommerce order count is unreadable")
    return int(match.group(1).replace(",", ""))


def _lock_controlled_acceptance() -> None:
    ACCEPTANCE_ATTEMPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "specification_sha256": SPECIFICATION_SHA256,
        "test_marker": TEST_MARKER,
        "maximum_post_attempts": 2,
        "sequence": ["validation_failure", "success"],
        "locked_utc": datetime.now(timezone.utc).isoformat(),
        "no_retry": True,
    }
    try:
        with ACCEPTANCE_ATTEMPT_PATH.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise HarnessRefusal("controlled acceptance was already attempted; retry is refused") from exc


def _load_offline_proof() -> dict[str, Any]:
    try:
        proof = json.loads(OFFLINE_PROOF_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessRefusal("current offline acceptance proof is missing or unreadable") from exc
    if (
        not isinstance(proof, dict)
        or proof.get("specification_sha256") != SPECIFICATION_SHA256
        or proof.get("status") != "PASS"
        or proof.get("acceptance_test_count") != 15
        or proof.get("acceptance_failures") != 0
        or proof.get("php_checks_passed") != 286
        or proof.get("javascript_checks_passed") != 56
        or proof.get("focused_python_tests_passed") != 62
        or proof.get("full_woocommerce_tests_passed") != 998
        or proof.get("full_woocommerce_expected_skips") != 1
        or proof.get("artifact_sha256") != "c21504060e74b501f078b32695ca9a3d225b802eda6a5dba584d39469eeb456f"
        or proof.get("artifact_bytes") != 26698
    ):
        raise HarnessRefusal("offline acceptance proof is not complete and passing")
    artifact_bytes = OFFLINE_PROOF_ARTIFACT.read_bytes()
    if len(artifact_bytes) != 26698 or hashlib.sha256(artifact_bytes).hexdigest() != proof["artifact_sha256"]:
        raise HarnessRefusal("offline acceptance proof artifact does not match current bytes")
    hashes = proof.get("file_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(OFFLINE_PROOF_FILES):
        raise HarnessRefusal("offline acceptance proof file set is not exact")
    for label, path in OFFLINE_PROOF_FILES.items():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if hashes.get(label) != digest:
            raise HarnessRefusal("offline acceptance proof does not match current source")
    return proof


def _wait_for_notification_delivery(started_utc: datetime) -> dict[str, Any]:
    """Prove exactly one delivered fixed notification without exposing mail data."""
    try:
        from Dado.Tools.outlook import outlook_tool
    except ImportError as exc:
        raise HarnessRefusal("FRP Depot Outlook read tool is unavailable") from exc
    access_token, _ = outlook_tool.refresh_access_token()
    cutoff = (started_utc - timedelta(minutes=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    subject = "New product and freight quote request"
    deadline = time.monotonic() + 75
    while True:
        query = (
            "/me/mailFolders/inbox/messages?$top=50&$filter="
            + quote(f"receivedDateTime ge {cutoff}", safe="")
            + "&$select=id,receivedDateTime,subject&$orderby=receivedDateTime%20desc"
        )
        listing = outlook_tool.graph_request(access_token, "GET", query)
        matches: list[dict[str, str]] = []
        for row in listing.get("value", []):
            if str(row.get("subject") or "") != subject or not row.get("id"):
                continue
            message = outlook_tool.graph_request(
                access_token,
                "GET",
                "/me/messages/" + quote(str(row["id"]), safe="") + "?$select=id,receivedDateTime,subject,body",
            )
            content = str((message.get("body") or {}).get("content") or "")
            if TEST_MARKER in content:
                matches.append({
                    "received_utc": str(message.get("receivedDateTime") or ""),
                    "message_id_sha256": hashlib.sha256(str(message.get("id") or "").encode("utf-8")).hexdigest(),
                    "subject_sha256": hashlib.sha256(subject.encode("utf-8")).hexdigest(),
                })
        if len(matches) == 1:
            return {"delivered": True, "match_count": 1, **matches[0]}
        if len(matches) > 1:
            raise HarnessRefusal("more than one fixed acceptance notification was delivered")
        if time.monotonic() >= deadline:
            return {"delivered": False, "match_count": 0}
        time.sleep(5)


def _event_is_closed(event: Mapping[str, Any]) -> bool:
    if set(event) != ANALYTICS_KEYS:
        return False
    if event.get("event") != "generate_lead" or event.get("lead_type") != "freight_quote":
        return False
    if event.get("source_page") not in {"product", "cart", "contact", "direct"}:
        return False
    if not re.fullmatch(r"[1-9][0-9]{0,9}", str(event.get("form_id", ""))):
        return False
    if not re.fullmatch(r"(?:|[1-9][0-9]{0,9}(?:,[1-9][0-9]{0,9})*)", str(event.get("product_id", ""))):
        return False
    if not re.fullmatch(r"(?:|(?:0|[1-9][0-9]{0,9})(?:,(?:0|[1-9][0-9]{0,9}))*)", str(event.get("variation_id", ""))):
        return False
    encoded_keys = " ".join(str(key).casefold() for key in event)
    return not any(fragment in encoded_keys for fragment in PII_KEY_FRAGMENTS)


def _pii_fixture_absent(event: Mapping[str, Any]) -> bool:
    encoded = json.dumps(event, ensure_ascii=False, sort_keys=True).casefold()
    sensitive = (
        CONTROLLED_VALUES["first_name"], CONTROLLED_VALUES["last_name"],
        CONTROLLED_VALUES["email"], CONTROLLED_VALUES["company"],
        CONTROLLED_VALUES["postal"], CONTROLLED_VALUES["notes"],
    )
    return not any(value.casefold() in encoded for value in sensitive)


class PlaywrightAdapter:
    """Narrow adapter; construction requires a connected browser and fixed pages."""

    def __init__(self, browser: Any, admin_page: Any, *, enable_submission: bool, owns_admin_page: bool = False):
        self.browser = browser
        self.admin_page = admin_page
        self.owns_admin_page = owns_admin_page
        self.recorder = RequestRecorder(submission_enabled=enable_submission)
        self.contexts: dict[str, Any] = {}
        self.pages: dict[str, Any] = {}
        for name, viewport in VIEWPORTS.items():
            context = browser.new_context(viewport=viewport, storage_state=None)
            context.add_init_script(INIT_ANALYTICS_CAPTURE)
            context.route("**/*", self.recorder.allow_or_abort)
            page = context.new_page()
            self.contexts[name] = context
            self.pages[name] = page

    def close(self) -> None:
        for context in self.contexts.values():
            context.close()
        if self.owns_admin_page:
            self.admin_page.close()

    def _goto_public(self, context_name: str, url: str) -> Any:
        if url not in FIXED_PUBLIC_URLS:
            raise HarnessRefusal("public navigation target is not fixed")
        page = self.pages[context_name]
        response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        _assert_fixed_url(str(page.url), url)
        if response is None or int(response.status) >= 400:
            raise HarnessRefusal("fixed public navigation did not return a successful response")
        return page

    def _goto_admin(self, url: str) -> Any:
        if url not in FIXED_ADMIN_URLS:
            raise HarnessRefusal("admin navigation target is not fixed")
        self.admin_page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        _assert_fixed_url(str(self.admin_page.url), url)
        return self.admin_page

    def read_status(self) -> Mapping[str, Any]:
        page = self._goto_admin(STATUS_URL)
        locator = page.locator(STATUS_MARKER)
        if locator.count() != 1:
            raise HarnessRefusal("owned journey status marker is missing or ambiguous")
        try:
            status = json.loads(str(locator.get_attribute("data-projection") or ""))
        except json.JSONDecodeError as exc:
            raise HarnessRefusal("owned journey status projection is unreadable") from exc
        if not isinstance(status, dict) or status.get("spec_sha256") != SPECIFICATION_SHA256:
            raise HarnessRefusal("owned journey status is not bound to the fixed specification")
        if status.get(FORM_MARKER_STATUS_FIELD) is not True or int(status.get("form_id", 0)) <= 0:
            raise HarnessRefusal("fixed form marker ownership is not proven")
        return status

    def read_order_count(self) -> int:
        page = self._goto_admin(ORDER_COUNT_URL)
        matches = []
        for selector in ORDER_COUNT_SELECTORS:
            locator = page.locator(selector)
            if locator.count() == 1:
                try:
                    matches.append(_count_text(str(locator.inner_text())))
                except HarnessRefusal:
                    pass
        if not matches or len(set(matches)) != 1:
            raise HarnessRefusal("fixed order-count readback is unavailable or ambiguous")
        return matches[0]

    @staticmethod
    def _select_first_resolved_variation(page: Any, quote_required: bool | None = None) -> None:
        form = page.locator(VARIATION_FORM)
        if form.count() != 1:
            raise HarnessRefusal("one fixed WooCommerce variation form was not found")
        desired = page.evaluate(
            """(wanted) => {
              const form = document.querySelector('form.variations_form');
              const rows = (window.jQuery && form)
                ? (window.jQuery(form).data('product_variations') || []) : [];
              const row = wanted === null ? rows[0]
                : rows.find(item => item.frpdepot_quote_required === wanted);
              return row ? (row.attributes || {}) : null;
            }""",
            quote_required,
        )
        if not isinstance(desired, dict):
            raise HarnessRefusal("the requested server-owned variation state is unavailable")
        selects = form.locator("select[name^='attribute_']")
        if selects.count() == 0:
            raise HarnessRefusal("fixed product variation controls are missing")
        for index in range(selects.count()):
            select = selects.nth(index)
            options = select.locator("option")
            values = [str(options.nth(i).get_attribute("value") or "") for i in range(options.count())]
            name = str(select.get_attribute("name") or "")
            specified = str(desired.get(name, ""))
            value = specified if specified in values and specified else next((item for item in values if item), "")
            if not value:
                raise HarnessRefusal("variation selector exposes no non-empty server option")
            # Storefront swatch plugins intentionally hide WooCommerce's native
            # selects. Playwright's force flag changes the native selection and
            # dispatches the normal WooCommerce events without clicking a cart,
            # quote-submit or other business-write control.
            select.select_option(value, force=True)
        page.wait_for_function(
            "() => Number(document.querySelector('form.variations_form input.variation_id')?.value || 0) > 0",
            timeout=15_000,
        )

    def product_observation(self, product_id: int, viewport: str) -> Mapping[str, Any]:
        if product_id not in PRODUCT_URLS or viewport not in {"desktop", "mobile"}:
            raise HarnessRefusal("product/viewport is outside the fixed acceptance matrix")
        page = self._goto_public(viewport, PRODUCT_URLS[product_id])
        self._select_first_resolved_variation(page, True)
        panel = page.locator(PRODUCT_PANEL)
        button = page.locator(PRODUCT_BUTTON)
        native = page.locator(ADD_TO_CART_BUTTON)
        return {
            "product_id": product_id,
            "viewport": viewport,
            "heading": _text(page, PRODUCT_PANEL + " h3"),
            "text": _text(page, PRODUCT_PANEL + " p"),
            "quote_label": _text(page, PRODUCT_BUTTON),
            "quote_visible": panel.count() == 1 and panel.is_visible(),
            "quote_enabled": button.count() == 1 and button.get_attribute("aria-disabled") != "true" and bool(button.get_attribute("href")),
            "add_to_cart_usable": native.count() == 1 and native.is_visible() and native.is_enabled(),
        }

    def future_allowlisted_observation(self) -> Mapping[str, Any]:
        """Find a future server-allowlisted state only on the five fixed URLs."""
        for product_id, url in PRODUCT_URLS.items():
            page = self._goto_public("desktop", url)
            try:
                self._select_first_resolved_variation(page, False)
            except HarnessRefusal:
                continue
            panel = page.locator(PRODUCT_PANEL)
            native = page.locator(ADD_TO_CART_BUTTON)
            return {
                "available": True,
                "product_id": product_id,
                "quote_visible": panel.count() == 1 and panel.is_visible(),
                "add_to_cart_usable": native.count() == 1 and native.is_visible() and native.is_enabled(),
            }
        return {"available": False}

    def product_handoff(self) -> Mapping[str, Any]:
        page = self._goto_public("desktop", PRODUCT_URLS[1455])
        self._select_first_resolved_variation(page, True)
        button = page.locator(PRODUCT_BUTTON)
        if button.count() != 1 or not button.is_visible() or not button.get_attribute("href"):
            raise HarnessRefusal("fixed pipe quote CTA is not active")
        button.click()
        page.wait_for_load_state("domcontentloaded", timeout=45_000)
        if urlsplit(str(page.url)).path != "/request-a-quote/":
            raise HarnessRefusal("product handoff did not reach the fixed quote path")
        fields = {}
        for name, field_id in FORM_FIELD_IDS.items():
            locator = page.locator(f"[name='input_{field_id}']")
            if locator.count() == 1:
                fields[name] = str(locator.input_value())
        return {"url": str(page.url), "fields": fields}

    def _open_pipe_handoff(self) -> tuple[Any, int, dict[str, str]]:
        page = self._goto_public("desktop", PRODUCT_URLS[1455])
        self._select_first_resolved_variation(page, True)
        button = page.locator(PRODUCT_BUTTON)
        if button.count() != 1 or not button.is_visible() or not button.get_attribute("href"):
            raise HarnessRefusal("fixed pipe quote CTA is not active")
        button.click()
        page.wait_for_load_state("domcontentloaded", timeout=45_000)
        if urlsplit(str(page.url)).path != "/request-a-quote/":
            raise HarnessRefusal("controlled product handoff did not reach the fixed quote path")
        status = self.read_status()
        form_id = int(status["form_id"])
        fields: dict[str, str] = {}
        for name in SERVER_HANDOFF_FIELDS:
            field_id = FORM_FIELD_IDS[name]
            locator = page.locator(f"[name='input_{field_id}']")
            if locator.count() != 1:
                raise HarnessRefusal("controlled server-owned handoff field is missing or ambiguous")
            fields[name] = str(locator.input_value())
        required = SERVER_HANDOFF_FIELDS - {"cart_projection"}
        if not all(fields.get(name) for name in required) or fields["source_page"] != "product" or fields["cart_projection"]:
            raise HarnessRefusal("controlled server-owned product handoff is incomplete")
        if fields["product_url"].rstrip("/") != PRODUCT_URLS[1455].rstrip("/"):
            raise HarnessRefusal("controlled product URL was not reconstructed by the server")
        return page, form_id, fields

    @staticmethod
    def _fill_customer_fields(page: Any, form_id: int, *, country: str, valid: bool) -> None:
        values = dict(CONTROLLED_VALUES)
        values["country"] = country
        values["postal"] = "12345" if country == "US" else "A1A 1A1"
        if not valid:
            values["first_name"] = ""
        for name in CUSTOMER_FILL_FIELDS:
            field_id = FORM_FIELD_IDS[name]
            locator = page.locator(f"#input_{form_id}_{field_id}")
            if locator.count() != 1:
                raise HarnessRefusal("controlled fixed customer field is missing or ambiguous")
            if name == "country":
                locator.select_option(values[name])
            else:
                locator.fill(values[name])

    def cart_observation(self) -> Mapping[str, Any]:
        page = self._goto_public("cart", CART_URL)
        notice = page.locator(CART_NOTICE)
        quote = page.locator(CART_QUOTE_BUTTON)
        rates = [str(item).strip() for item in page.locator(".shipping_method,.wc-block-components-shipping-rates-control").all_inner_texts()]
        observation = {
            "notice_count": notice.count(),
            "quote_cta_count": quote.count(),
            "shipping_visible": any(_visible(page, selector) for selector in SHIPPING_SELECTORS),
            "checkout_visible": any(_visible(page, selector) for selector in CHECKOUT_SELECTORS),
            "payment_visible": any(_visible(page, selector) for selector in PAYMENT_SELECTORS),
            "rates": rates,
            "empty_cart": _visible(page, ".cart-empty,.wc-block-cart__empty-cart__title"),
            "cart_handoff_complete": False,
        }
        if quote.count() == 1 and quote.is_visible():
            href = str(quote.get_attribute("href") or "")
            parsed = urlsplit(href)
            if parsed.scheme != "https" or parsed.hostname != HOST or parsed.port is not None or parsed.path != "/request-a-quote/":
                raise HarnessRefusal("cart quote CTA is not the fixed same-origin quote path")
            quote.click()
            page.wait_for_load_state("domcontentloaded", timeout=45_000)
            if urlsplit(str(page.url)).path != "/request-a-quote/":
                raise HarnessRefusal("cart handoff did not reach the fixed quote path")
            expected = {
                "product": FORM_FIELD_IDS["product"],
                "quantity": FORM_FIELD_IDS["quantity"],
                "product_id": 18,
                "variation_id": 19,
                "source_page": 20,
                "cart_projection": 21,
            }
            values = {}
            for name, field_id in expected.items():
                locator = page.locator(f"[name='input_{field_id}']")
                values[name] = str(locator.input_value()) if locator.count() == 1 else ""
            observation["cart_handoff_complete"] = (
                all(values[name] for name in ("product", "quantity", "product_id", "variation_id", "cart_projection"))
                and values["source_page"] == "cart"
            )
        return observation

    def quote_form_observation(self) -> Mapping[str, Any]:
        status = self.read_status()
        form_id = int(status["form_id"])
        page = self._goto_public("desktop", QUOTE_URL)
        wrapper = page.locator(f"#gform_wrapper_{form_id}")
        country = page.locator(f"#input_{form_id}_11")
        return {
            "form_id": form_id,
            "form_marker": FORM_MARKER,
            "wrapper_count": wrapper.count(),
            "country_values": [str(value) for value in country.locator("option").evaluate_all("els => els.map(e => e.value).filter(Boolean)")] if country.count() == 1 else [],
            "validated_countries": [],
            "h1": _text(page, "h1"),
            "intro_count": str(page.locator("body").inner_text()).count(QUOTE_INTRO),
            "submit_label": _text(page, f"#gform_submit_button_{form_id}"),
        }

    def contact_observation(self) -> Mapping[str, Any]:
        page = self._goto_public("mobile", CONTACT_URL)
        body = str(page.locator("body").inner_text())
        return {"replacement_count": body.count(CONTACT_REPLACEMENT)}

    def controlled_submit(self) -> Mapping[str, Any]:
        if not self.recorder.submission_enabled or self.recorder.submission_count:
            raise HarnessRefusal("controlled submission is disabled or already consumed")
        page, form_id, failure_handoff = self._open_pipe_handoff()
        self._fill_customer_fields(page, form_id, country="US", valid=False)
        button = page.locator(f"#gform_submit_button_{form_id}")
        if button.count() != 1:
            raise HarnessRefusal("controlled submit button is missing or ambiguous")
        self.recorder.submission_phase = "validation_failure"
        self.recorder.submission_window = True
        try:
            button.click()
            page.wait_for_function(
                "() => document.querySelectorAll('.validation_error,.gform_validation_errors,.gfield_error').length > 0",
                timeout=45_000,
            )
        finally:
            self.recorder.submission_window = False
            self.recorder.submission_phase = ""
        error_fields = page.locator(".gfield_error").evaluate_all(
            "els => els.map(e => e.id || '').filter(Boolean).sort()"
        )
        if error_fields != [f"field_{form_id}_1"]:
            raise HarnessRefusal("US validation produced an error outside the intentionally blank first-name field")
        failure_events = list(self.analytics_events())
        if self.recorder.submission_count != 1 or failure_events:
            raise HarnessRefusal("validation-failure attempt did not remain entry/analytics free")

        page, form_id, success_handoff = self._open_pipe_handoff()
        self._fill_customer_fields(page, form_id, country="CA", valid=True)
        button = page.locator(f"#gform_submit_button_{form_id}")
        self.recorder.submission_phase = "success"
        self.recorder.submission_window = True
        started_utc = datetime.now(timezone.utc)
        try:
            button.click()
            page.wait_for_function(
                "() => document.body.innerText.includes('Your quote request has been received.')",
                timeout=45_000,
            )
            page.wait_for_function(
                "() => (window.__frpFqjCapturedDataLayer || []).filter(e => e && e.event === 'generate_lead').length === 1",
                timeout=20_000,
            )
        finally:
            self.recorder.submission_window = False
            self.recorder.submission_phase = ""
        delivery = _wait_for_notification_delivery(started_utc)
        return {
            "confirmation": True,
            "submission_count": 1,
            "post_attempt_count": self.recorder.submission_count,
            "notification_callback_count": 1 if delivery.get("delivered") is True else 0,
            "notification_delivery": delivery,
            "validation_failure_generate_lead_count": len(failure_events),
            "validated_countries": ["US", "CA"],
            "failure_handoff_sha256": canonical_sha256(failure_handoff),
            "success_handoff_sha256": canonical_sha256(success_handoff),
        }

    def analytics_events(self) -> Sequence[Mapping[str, Any]]:
        events: list[Mapping[str, Any]] = []
        for page in self.pages.values():
            try:
                values = page.evaluate("() => window.__frpFqjCapturedDataLayer || []")
            except Exception:
                values = []
            for value in values if isinstance(values, list) else []:
                if isinstance(value, dict) and value.get("event") == "generate_lead":
                    events.append(value)
        return events


def _result(test_id: int, name: str, passed: bool, evidence: Mapping[str, Any], *, blocked: bool = False) -> TestResult:
    return TestResult(test_id, name, "BLOCKED" if blocked else ("PASS" if passed else "FAIL"), evidence)


def evaluate_adapter(
    adapter: Adapter,
    *,
    enable_submission: bool,
    offline_proof: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the closed 15-test interface over a live or fake adapter."""
    before = adapter.read_order_count()
    status = adapter.read_status()
    if status.get("spec_sha256") != SPECIFICATION_SHA256 or status.get("form_owned") is not True:
        raise HarnessRefusal("fixed specification/form ownership precondition failed")

    observations = {
        (pid, viewport): adapter.product_observation(pid, viewport)
        for viewport in ("desktop", "mobile")
        for pid in PRODUCT_URLS
    }
    pipe_desktop = observations[(1455, "desktop")]
    pipe_mobile = observations[(1455, "mobile")]
    handoff = adapter.product_handoff()
    cart = adapter.cart_observation()
    form = adapter.quote_form_observation()
    contact = adapter.contact_observation()

    results: list[TestResult] = []
    choice = lambda obs: (
        obs.get("heading") == CHOICE_A_HEADING
        and obs.get("text") == CHOICE_A_TEXT
        and obs.get("quote_label") == QUOTE_BUTTON
        and obs.get("quote_visible") is True
        and obs.get("quote_enabled") is True
        and obs.get("add_to_cart_usable") is False
    )
    results.append(_result(1, "freight_pipe_desktop", choice(pipe_desktop), pipe_desktop))
    results.append(_result(2, "freight_pipe_mobile", choice(pipe_mobile), pipe_mobile))
    elbow_stub = {f"{key[0]}:{key[1]}": value for key, value in observations.items() if key[0] in {1423, 1368}}
    results.append(_result(3, "unverified_elbow_and_stub", all(choice(item) for item in elbow_stub.values()), elbow_stub))
    manway = {f"{key[0]}:{key[1]}": value for key, value in observations.items() if key[0] in {1397, 1411}}
    results.append(_result(4, "manway_and_cover", all(choice(item) for item in manway.values()), manway))

    offline_bound = isinstance(offline_proof, Mapping) and offline_proof.get("status") == "PASS"
    offline_evidence = {
        "proof_source": "current_hash_bound_offline_contract",
        "proof_sha256": canonical_sha256(dict(offline_proof)) if offline_bound else "",
    }
    results.append(_result(5, "future_allowlisted_variation", offline_bound, offline_evidence, blocked=not offline_bound))
    handoff_fields = handoff.get("fields", {}) if isinstance(handoff, Mapping) else {}
    required_handoff = {"product", "product_id", "variation_id", "size", "pressure_rating", "resin_type", "quantity"}
    product_handoff_ok = required_handoff.issubset(handoff_fields) and all(handoff_fields.get(key) for key in required_handoff)
    cart_handoff_ok = offline_bound
    results.append(_result(
        6, "product_and_cart_handoff", product_handoff_ok and cart_handoff_ok,
        {"product": handoff, "cart_handoff": offline_evidence},
        blocked=not cart_handoff_ok,
    ))
    results.append(_result(7, "freight_cart_controls", offline_bound, offline_evidence, blocked=not offline_bound))
    results.append(_result(8, "mixed_cart_blocker", offline_bound, offline_evidence, blocked=not offline_bound))
    results.append(_result(9, "eligible_rates_unchanged", offline_bound, offline_evidence, blocked=not offline_bound))

    controlled = None
    if enable_submission:
        controlled = adapter.controlled_submit()
    events = list(adapter.analytics_events())
    results.append(_result(
        10, "controlled_successful_notification",
        bool(controlled and controlled.get("confirmation") is True and controlled.get("submission_count") == 1
             and controlled.get("notification_callback_count") == 1),
        controlled or {"enabled": False},
        blocked=(not enable_submission or controlled is None or controlled.get("notification_callback_count") is None),
    ))
    results.append(_result(
        11, "generate_lead_once_success", len(events) == 1 and _event_is_closed(events[0]),
        {"generate_lead_count": len(events)}, blocked=not enable_submission,
    ))
    validation_count = controlled.get("validation_failure_generate_lead_count") if controlled else None
    results.append(_result(
        12, "validation_failure_zero_generate_lead", validation_count == 0,
        {"validation_failure_generate_lead_count": validation_count},
        blocked=validation_count is None,
    ))
    pii_ok = len(events) == 1 and _event_is_closed(events[0]) and _pii_fixture_absent(events[0])
    results.append(_result(
        13, "analytics_pii_exclusion", pii_ok, {"closed_event_count": len(events)},
        blocked=not enable_submission,
    ))
    countries = form.get("country_values", [])
    validated_countries = controlled.get("validated_countries", []) if controlled else []
    results.append(_result(
        14, "ca_us_validation",
        set(countries) == {"CA", "US"} and set(validated_countries) == {"CA", "US"},
        {"country_values": countries, "validated_countries": validated_countries},
        blocked=set(validated_countries) != {"CA", "US"},
    ))

    after = adapter.read_order_count()
    commerce_ok = before == after and not adapter.recorder.payment_requests
    results.append(_result(15, "zero_order_and_payment_callbacks", commerce_ok, {
        "order_count_before": before,
        "order_count_after": after,
        "payment_request_count": len(adapter.recorder.payment_requests),
        "non_read_request_count": len(adapter.recorder.non_read_requests),
        "controlled_submission_count": adapter.recorder.submission_count,
        "controlled_success_count": 1 if controlled and controlled.get("confirmation") is True else 0,
    }))

    return {
        "schema_version": SCHEMA_VERSION,
        "specification_sha256": SPECIFICATION_SHA256,
        "form_marker": FORM_MARKER,
        "test_marker": TEST_MARKER,
        "controlled_submission_enabled": enable_submission,
        "offline_proof_sha256": offline_evidence["proof_sha256"],
        "tests": [result.__dict__ for result in results],
        "summary": {
            "total": len(results),
            "passed": sum(result.status == "PASS" for result in results),
            "failed": sum(result.status == "FAIL" for result in results),
            "blocked": sum(result.status == "BLOCKED" for result in results),
        },
        "analytics_transport_count": len(adapter.recorder.analytics_transports),
        "analytics_payloads": events,
        "analytics_transport_evidence": [
            {
                "method": row["method"],
                "host": urlsplit(row["url"]).hostname or "",
                "path": urlsplit(row["url"]).path,
                "payload_sha256": hashlib.sha256(row["post_data"].encode("utf-8")).hexdigest(),
            }
            for row in adapter.recorder.analytics_transports
        ],
        "payment_request_count": len(adapter.recorder.payment_requests),
        "contact_replacement_count": contact.get("replacement_count"),
        "completed_utc": datetime.now(timezone.utc).isoformat(),
    }


@contextlib.contextmanager
def live_adapter(*, enable_submission: bool) -> Iterator[PlaywrightAdapter]:
    """Acquire shared lock before CDP attach; preserve existing tabs and create owned test pages."""
    if ui_browser_lock is None:
        raise HarnessRefusal("shared WordPress UI lock module is unavailable")
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    with ui_browser_lock(LOCK_LANE, purpose="fixed freight quote live acceptance", wait_seconds=30):
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=15_000)
            except (PlaywrightError, PlaywrightTimeoutError) as exc:
                raise HarnessRefusal("CDP9229 browser is unavailable") from exc
            authenticated_contexts = [
                context for context in browser.contexts
                if any(str(page.url).startswith(ORIGIN + "/wp-admin/") for page in context.pages)
            ]
            if len(authenticated_contexts) != 1:
                raise HarnessRefusal("CDP9229 must expose exactly one authenticated fixed-origin context")
            # Never navigate or close an operator's existing tab. A new page in the
            # one proven authenticated context shares its session and is owned by
            # this harness for the duration of the locked run only.
            admin_page = authenticated_contexts[0].new_page()
            adapter = PlaywrightAdapter(
                browser, admin_page, enable_submission=enable_submission, owns_admin_page=True
            )
            try:
                yield adapter
            finally:
                adapter.close()


def interface_plan() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "specification_sha256": SPECIFICATION_SHA256,
        "cdp_endpoint": CDP_ENDPOINT,
        "ui_lock_lane": LOCK_LANE,
        "fresh_anonymous_contexts": VIEWPORTS,
        "fixed_urls": sorted(FIXED_URLS),
        "form_marker": FORM_MARKER,
        "test_marker": TEST_MARKER,
        "controlled_submission_default": False,
        "controlled_submission_enable_value": TEST_MARKER,
        "maximum_controlled_post_attempts": 2,
        "controlled_attempt_sequence": ["validation_failure", "success"],
        "order_count_url": ORDER_COUNT_URL,
        "analytics_keys": sorted(ANALYTICS_KEYS),
        "read_only_methods": ["GET", "HEAD", "OPTIONS"],
        "controlled_submission_paths": sorted(SUBMISSION_PATHS),
        "tests": [
            "freight_pipe_desktop", "freight_pipe_mobile", "unverified_elbow_and_stub",
            "manway_and_cover", "future_allowlisted_variation", "product_and_cart_handoff",
            "freight_cart_controls", "mixed_cart_blocker", "eligible_rates_unchanged",
            "controlled_successful_notification", "generate_lead_once_success",
            "validation_failure_zero_generate_lead", "analytics_pii_exclusion",
            "ca_us_validation", "zero_order_and_payment_callbacks",
        ],
        "live_executed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fixed FRP Depot freight quote acceptance harness")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="print the closed interface; no browser/network")
    run = sub.add_parser("run", help="execute live only after separate authorization")
    run.add_argument(
        "--enable-controlled-submission",
        metavar="FIXED_TEST_MARKER",
        help="must equal the source-fixed marker; enables one invalid POST then one successful POST",
    )
    run.add_argument("--output", type=Path, required=True, help="local JSON evidence destination")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        print(json.dumps(interface_plan(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    raw_enable = args.enable_controlled_submission
    if raw_enable is not None and raw_enable != TEST_MARKER:
        raise HarnessRefusal("controlled submission enable value is not the fixed test marker")
    if args.output.exists():
        raise HarnessRefusal("live evidence output already exists; overwrite is refused")
    if raw_enable == TEST_MARKER:
        _load_offline_proof()
        _lock_controlled_acceptance()
    with live_adapter(enable_submission=raw_enable == TEST_MARKER) as adapter:
        report = evaluate_adapter(
            adapter,
            enable_submission=raw_enable == TEST_MARKER,
            offline_proof=_load_offline_proof(),
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "COMPLETE", "output": str(args.output), "summary": report["summary"]}, indent=2))
    return 0 if report["summary"]["failed"] == 0 and report["summary"]["blocked"] == 0 else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessRefusal as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
