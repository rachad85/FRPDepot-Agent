#!/usr/bin/env python3
"""Pure offline/fake model for the 15 freight-quote acceptance checks.

This module has no browser, network, WordPress, WooCommerce, order, payment,
email, or filesystem-write path.  It models the commissioned UI and handoff
boundaries so the acceptance suite can be executed on any Python 3.11 host.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

SPECIFICATION_SHA256 = "5348ef3f357676f5629cf72696fd3fe0be718a3847854974f20cf28cc7047400"
ORIGIN = "https://frpdepots.com"
FORM_MARKER = "FRPDEPOT_FQJ_FIXED_FORM_V2_SPEC_5348EF3F"
TEST_MARKER = "FRPDEPOT-FQJ-ACCEPTANCE-20260813"

CHOICE_A_HEADING = "Freight quote required"
CHOICE_A_TEXT = (
    "This selection is not available for direct online checkout. Choose the size, "
    "pressure rating, resin type and quantity, then request a product and freight "
    "quote. No payment will be taken."
)
QUOTE_BUTTON = "Request a Freight Quote"
CART_TEXT = (
    "One or more items in this cart require a product and freight quote. Your "
    "selected products, options and quantities will be included automatically. "
    "No payment will be taken."
)

VIEWPORTS = {
    "desktop": {"width": 1440, "height": 1000},
    "mobile": {"width": 390, "height": 844},
}

TARGET_PRODUCTS: dict[int, dict[str, Any]] = {
    1455: {"slug": "frp-fw-pipe", "name": "FRP FW Pipe", "variation_id": 145501},
    1423: {"slug": "frp-elbow-90", "name": "FRP Elbow 90", "variation_id": 142301},
    1368: {"slug": "frp-stub-flange", "name": "FRP Stub Flange", "variation_id": 136801},
    1397: {"slug": "frp-manway", "name": "FRP Manway", "variation_id": 139701},
    1411: {"slug": "frp-manway-cover", "name": "FRP Manway Cover", "variation_id": 141101},
}

FUTURE_PRODUCT_ID = 900001
FUTURE_VARIATION_ID = 900002
FUTURE_SKU = "TEST-UPS-VERIFIED-1"

ANALYTICS_KEYS = frozenset({"event", "lead_type", "source_page"})
ANALYTICS_SOURCES = frozenset({"product", "cart", "contact", "direct"})
PII_TOKENS = (
    "first_name", "last_name", "name", "email", "phone", "company", "country",
    "postal", "zip", "notes", "message", "upload", "filename", "product_name",
    "product_url", "cart_payload",
)


class ModelRefusal(ValueError):
    """The fake authority rejected malformed or untrusted input."""


def _canonical_url(path: str) -> str:
    return ORIGIN + path


def _strict_positive_int(value: Any, *, maximum: int = 2_147_483_647) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ModelRefusal("expected a positive bounded decimal integer")
    return value


@dataclass(frozen=True)
class ProductLine:
    product_id: int
    variation_id: int
    sku: str
    shipping_class: str
    quantity: int = 1
    attributes: Mapping[str, str] = field(default_factory=dict)


class DecisionAuthority:
    """Default-deny product/variation/SKU/class allowlist decision core."""

    def __init__(self, allowlist: Mapping[str, Any], *, now: datetime | None = None):
        self.now = now or datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
        self.entries = self._prepare(allowlist)

    def _prepare(self, raw: Mapping[str, Any]) -> dict[tuple[int, int], str]:
        if not isinstance(raw, Mapping) or set(raw) != {"expires_utc", "items"}:
            raise ModelRefusal("allowlist schema is not exact")
        try:
            expiry = datetime.fromisoformat(str(raw["expires_utc"]).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ModelRefusal("allowlist expiry is unreadable") from exc
        if expiry.tzinfo is None or self.now >= expiry:
            raise ModelRefusal("allowlist is stale")
        items = raw["items"]
        if not isinstance(items, list):
            raise ModelRefusal("allowlist items are unreadable")
        result: dict[tuple[int, int], str] = {}
        for item in items:
            if not isinstance(item, Mapping) or set(item) != {"product_id", "variation_id", "sku"}:
                raise ModelRefusal("allowlist entry schema is not exact")
            product_id = _strict_positive_int(item["product_id"])
            variation_id = _strict_positive_int(item["variation_id"])
            sku = item["sku"]
            if not isinstance(sku, str) or not sku.strip():
                raise ModelRefusal("allowlist SKU is empty")
            key = (product_id, variation_id)
            if key in result:
                raise ModelRefusal("duplicate allowlist product/variation identity")
            result[key] = sku.strip()
        return result

    def reason(self, line: ProductLine | Any) -> str:
        if not isinstance(line, ProductLine):
            return "item_unreadable"
        if line.product_id <= 0 or line.variation_id <= 0:
            return "unresolvable_identity"
        if not isinstance(line.shipping_class, str):
            return "unresolvable_shipping_class"
        if line.shipping_class == "freight-quote-required":
            return "freight_shipping_class"
        if not isinstance(line.sku, str) or not line.sku.strip():
            return "missing_sku"
        expected = self.entries.get((line.product_id, line.variation_id))
        if expected is None:
            return "not_ups_verified"
        if expected != line.sku.strip():
            return "sku_mismatch"
        if line.shipping_class:
            return "unexpected_shipping_class"
        return ""

    def quote_required(self, lines: Iterable[ProductLine]) -> bool:
        return any(bool(self.reason(line)) for line in lines)


@dataclass(frozen=True)
class ProductUi:
    viewport: str
    resolved: bool
    quote_required: bool | None
    heading: str = ""
    text: str = ""
    quote_label: str = ""
    quote_visible: bool = False
    quote_enabled: bool = False
    quote_href: str = ""
    add_to_cart_visible: bool = False
    add_to_cart_enabled: bool = False


def render_product_ui(viewport: str, line: ProductLine | None, authority: DecisionAuthority) -> ProductUi:
    if viewport not in VIEWPORTS:
        raise ModelRefusal("viewport is not fixed")
    if line is None:
        return ProductUi(viewport, False, None)
    quote_required = bool(authority.reason(line))
    if quote_required:
        return ProductUi(
            viewport, True, True, CHOICE_A_HEADING, CHOICE_A_TEXT, QUOTE_BUTTON,
            True, True, "/request-a-quote/", False, False,
        )
    return ProductUi(
        viewport, True, False, add_to_cart_visible=True, add_to_cart_enabled=True,
    )


@dataclass(frozen=True)
class CartUi:
    quote_required: bool
    notice_count: int
    quote_cta_count: int
    shipping_calculator_visible: bool
    shipping_rates_visible: bool
    checkout_visible: bool
    payment_visible: bool
    rates: Any


def render_cart_ui(lines: list[ProductLine], rates: Any, authority: DecisionAuthority) -> CartUi:
    quote = authority.quote_required(lines)
    if quote:
        return CartUi(True, 1, 1, False, False, False, False, [])
    # Eligible state is deliberately the exact supplied object, not a rebuilt or
    # normalized list: the acceptance contract is that rates remain untouched.
    return CartUi(False, 0, 0, True, True, True, True, rates)


def target_line(product_id: int, *, quantity: int = 2) -> ProductLine:
    product = TARGET_PRODUCTS.get(product_id)
    if not product:
        raise ModelRefusal("product is outside the five fixed targets")
    return ProductLine(
        product_id=product_id,
        variation_id=product["variation_id"],
        sku=f"UNVERIFIED-{product_id}",
        shipping_class="freight-quote-required" if product_id == 1455 else "",
        quantity=quantity,
        attributes={"Size": "4 in", "Pressure rating": "150 psi", "Resin type": "Vinyl ester"},
    )


def future_allowlisted_line(*, quantity: int = 1) -> ProductLine:
    return ProductLine(
        FUTURE_PRODUCT_ID, FUTURE_VARIATION_ID, FUTURE_SKU, "", quantity,
        {"Size": "4 in", "Pressure rating": "150 psi", "Resin type": "Vinyl ester"},
    )


def fixed_allowlist() -> dict[str, Any]:
    return {
        "expires_utc": "2099-01-01T00:00:00Z",
        "items": [{
            "product_id": FUTURE_PRODUCT_ID,
            "variation_id": FUTURE_VARIATION_ID,
            "sku": FUTURE_SKU,
        }],
    }


def product_handoff(product_id: int, variation_id: int, quantity: int, untrusted: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Reconstruct product fields from fixed catalogue identity, never client text."""
    product_id = _strict_positive_int(product_id)
    variation_id = _strict_positive_int(variation_id)
    quantity = _strict_positive_int(quantity, maximum=9999)
    product = TARGET_PRODUCTS.get(product_id)
    if product is None or variation_id != product["variation_id"]:
        raise ModelRefusal("product/variation is not a fixed live parent pair")
    ignored = dict(untrusted or {})
    del ignored
    return {
        "source_page": "product",
        "product": product["name"],
        "product_id": str(product_id),
        "variation_id": str(variation_id),
        "size": "4 in",
        "pressure_rating": "150 psi",
        "resin_type": "Vinyl ester",
        "quantity": str(quantity),
        "product_url": _canonical_url(f"/product/{product['slug']}/"),
        "cart_projection": "",
    }


def cart_handoff(lines: list[ProductLine]) -> dict[str, Any]:
    if not isinstance(lines, list) or not 1 <= len(lines) <= 50:
        raise ModelRefusal("cart line count is outside 1..50")
    projection: list[dict[str, Any]] = []
    visible_names: list[str] = []
    for line in lines:
        if not isinstance(line, ProductLine):
            raise ModelRefusal("cart contains an unreadable line")
        _strict_positive_int(line.product_id)
        _strict_positive_int(line.variation_id)
        _strict_positive_int(line.quantity, maximum=9999)
        if line.product_id in TARGET_PRODUCTS:
            visible_names.append(TARGET_PRODUCTS[line.product_id]["name"])
        elif line.product_id == FUTURE_PRODUCT_ID:
            visible_names.append("Future UPS Fixture")
        else:
            raise ModelRefusal("cart product is not a fixed fixture")
        clean_attributes = {str(key): str(value) for key, value in line.attributes.items()}
        projection.append({
            "product_id": line.product_id,
            "variation_id": line.variation_id,
            "attributes": clean_attributes,
            "quantity": line.quantity,
        })
    encoded = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 12_000:
        raise ModelRefusal("cart projection is oversized")
    return {
        "source_page": "cart",
        "product": "; ".join(visible_names),
        "product_id": ",".join(str(line.product_id) for line in lines),
        "variation_id": ",".join(str(line.variation_id) for line in lines),
        "quantity": "; ".join(str(line.quantity) for line in lines),
        "product_url": "",
        "cart_projection": encoded,
    }


@dataclass
class CommerceCallbacks:
    order_create: int = 0
    order_save: int = 0
    payment_authorize: int = 0
    payment_capture: int = 0

    @property
    def total(self) -> int:
        return self.order_create + self.order_save + self.payment_authorize + self.payment_capture


@dataclass
class AnalyticsLatch:
    events: list[dict[str, str]] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)

    def success_confirmation(self, *, form_marker: str, entry_id: int, source_page: str) -> None:
        if form_marker != FORM_MARKER or isinstance(entry_id, bool) or entry_id <= 0:
            return
        if source_page not in ANALYTICS_SOURCES:
            return
        key = f"{form_marker}:{entry_id}"
        if key in self._seen:
            return
        self._seen.add(key)
        self.events.append({
            "event": "generate_lead",
            "lead_type": "freight_quote",
            "source_page": source_page,
        })


REQUIRED_FORM_FIELDS = frozenset({
    "first_name", "last_name", "email", "company", "product", "size",
    "pressure_rating", "resin_type", "quantity", "country", "postal", "notes",
    "source_page", "test_marker",
})


@dataclass
class QuoteForm:
    analytics: AnalyticsLatch
    commerce: CommerceCallbacks
    next_entry_id: int = 1000
    quote_entries: int = 0
    notification_callbacks: int = 0

    def validate(self, fields: Mapping[str, Any]) -> bool:
        if not isinstance(fields, Mapping) or not REQUIRED_FORM_FIELDS.issubset(fields):
            return False
        if any(not isinstance(fields[key], str) or not fields[key].strip() for key in REQUIRED_FORM_FIELDS):
            return False
        if fields["country"] not in {"CA", "US"}:
            return False
        if fields["source_page"] not in ANALYTICS_SOURCES:
            return False
        if fields["test_marker"] != TEST_MARKER:
            return False
        if "@" not in fields["email"]:
            return False
        try:
            quantity = int(fields["quantity"])
        except (TypeError, ValueError):
            return False
        return 1 <= quantity <= 9999

    def submit(self, fields: Mapping[str, Any], *, upload_ok: bool = True, confirmation_ok: bool = True) -> bool:
        if not self.validate(fields) or not upload_ok:
            return False
        self.next_entry_id += 1
        self.quote_entries += 1
        self.notification_callbacks += 1
        if confirmation_ok:
            self.analytics.success_confirmation(
                form_marker=FORM_MARKER,
                entry_id=self.next_entry_id,
                source_page=str(fields["source_page"]),
            )
        return confirmation_ok


def valid_form_fields(country: str = "CA", *, source_page: str = "product") -> dict[str, str]:
    return {
        "first_name": "Acceptance",
        "last_name": "Fixture",
        "email": "fqj-acceptance@example.invalid",
        "company": "FRP Depot Acceptance Fixture",
        "product": "FRP FW Pipe",
        "size": "4 in",
        "pressure_rating": "150 psi",
        "resin_type": "Vinyl ester",
        "quantity": "2",
        "country": country,
        "postal": "A1A 1A1" if country == "CA" else "12345",
        "notes": TEST_MARKER,
        "source_page": source_page,
        "test_marker": TEST_MARKER,
    }


def analytics_is_closed_and_non_pii(event: Mapping[str, Any], fixture: Mapping[str, Any]) -> bool:
    if set(event) != ANALYTICS_KEYS:
        return False
    lowered_keys = " ".join(str(key).casefold() for key in event)
    if any(token in lowered_keys for token in PII_TOKENS):
        return False
    encoded = json.dumps(event, ensure_ascii=False, sort_keys=True).casefold()
    sensitive_values = [
        str(fixture[key]).casefold()
        for key in ("first_name", "last_name", "email", "company", "postal", "notes")
        if key in fixture
    ]
    return not any(value and value in encoded for value in sensitive_values)


def assert_same_origin(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "frpdepots.com" or parsed.port is not None:
        raise ModelRefusal("URL is not the exact origin")


__all__ = [name for name in globals() if not name.startswith("_")]
