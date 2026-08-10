"""Safety tests for the FRP Depot WordPress plugin deployment tool.

Everything here is offline. There is no Playwright, no CDP, no browser and no
network: the WordPress admin screens and the public storefront are modelled by a
small fake DOM with a real (if tiny) CSS matcher, so the tool's own selectors,
scoping, ordering and refusal logic are genuinely exercised rather than mocked
away at the boundary. TestFakeEngineIsHonest pins the matcher down -- a
permissive fake would quietly invalidate every scoping test in this file.

The fakes record every navigation, click, file selection and option choice, which
is what lets these tests assert the properties that actually matter: that the
replay lock exists BEFORE the first side effect, that a failed activation
deactivates exactly once, that a successful one never does, and that nothing can
reach a Delete link, a second plugin, or a page off frpdepots.com.

2026-08-09 REPAIR COVERAGE. The storefront fake can inject a one-shot
Playwright-shaped TimeoutError at any named sub-step, so the fix for the bare
TimeoutError incident is tested at every step it could have happened at. The fake
also REFUSES a navigation, click, selection or text read that arrives without an
explicit bounded timeout, so "use explicit bounded timeouts" is enforced rather
than asserted in prose.

2026-08-09 SECOND REPAIR COVERAGE. The fake now models what a read-only live
inspection MEASURED the product page to be, not what the first repair assumed: one
hidden backing <select> per attribute row under visible
`<li role="radio" data-value="...">` options, with NO `input[type="radio"]`
anywhere. Two things follow, and both are what these tests are for.

  * The variation id and the Add to cart button are DERIVED from the selections
    rather than set by a flag, so "did the customer's clicks actually make this
    purchasable?" is a real question here. Forcing the hidden select changes values
    and still leaves the button disabled -- exactly the production shape.
  * A fresh anonymous context starts with nothing chosen, because a customer's
    selections live in the browser. That is what makes "three independent
    rehearsals" a claim the tests can check rather than a count of loop turns.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wordpress_plugin_deployment_tool as deploy  # noqa: E402

PLUGIN_FILE = deploy.PLUGIN_FILE
PLUGIN_NAME = deploy.PLUGIN_NAME
GOOD_VERSION = deploy.ARTIFACT_VERSION
OLD_VERSION = deploy.WITHDRAWN_VERSION
OLD_SHA256 = deploy.WITHDRAWN_SHA256

SIZE_STEP, PRESSURE_STEP, RESIN_STEP = deploy.SELECT_STEPS


class Playwright:
    """Stand-ins for the Playwright exception classes, kept out of the builtins.

    TimeoutError is nested rather than module-level on purpose: the tool records
    `type(exc).__name__`, so the class must genuinely be called TimeoutError,
    and shadowing the builtin in this module would be a trap for later readers.
    """

    class TimeoutError(Exception):
        pass


# ===========================================================================
# A very small CSS engine: tag, .class, #id, [attr="v"], :not(...), descendants.
# It supports exactly the selector vocabulary the tool uses, which is the point:
# if the tool ever reaches for a broader selector, these tests stop matching.
# ===========================================================================
_TOKEN = re.compile(
    r"^(?P<tag>[A-Za-z][\w-]*)?"
    r"(?P<rest>(?:\.[\w-]+|\#[\w-]+|\[[^\]]*\]|:not\([^)]*\))*)$"
)
_PART = re.compile(r"\.([\w-]+)|\#([\w-]+)|\[([^\]]*)\]|:not\(([^)]*)\)")
_ATTR = re.compile(r"^([\w-]+)(?:=(?:\"([^\"]*)\"|'([^']*)'))?$")


def _parse(token: str):
    matched = _TOKEN.match(token)
    if not matched:
        raise AssertionError(f"the fake CSS engine does not support selector {token!r}")
    tag = matched.group("tag")
    classes: set[str] = set()
    ident = None
    attrs: list[tuple[str, str | None]] = []
    nots: list[str] = []
    for part in _PART.finditer(matched.group("rest") or ""):
        if part.group(1):
            classes.add(part.group(1))
        elif part.group(2):
            ident = part.group(2)
        elif part.group(3) is not None:
            found = _ATTR.match(part.group(3))
            if not found:
                raise AssertionError(f"unsupported attribute selector {part.group(3)!r}")
            value = found.group(2) if found.group(2) is not None else found.group(3)
            attrs.append((found.group(1), value))
        else:
            nots.append(part.group(4))
    return tag, classes, ident, attrs, nots


def _matches(element: "FakeElement", token: str) -> bool:
    tag, classes, ident, attrs, nots = _parse(token)
    if tag and element.tag != tag:
        return False
    if ident and element.attrs.get("id") != ident:
        return False
    if not classes <= element.classes:
        return False
    for name, value in attrs:
        if name not in element.attrs:
            return False
        if value is not None and element.attrs[name] != value:
            return False
    return not any(_matches(element, sub) for sub in nots)


def _select(root: "FakeElement", selector: str) -> list["FakeElement"]:
    current = [root]
    for token in str(selector).split():
        found: list[FakeElement] = []
        for node in current:
            found.extend(child for child in node.descendants() if _matches(child, token))
        unique: list[FakeElement] = []
        for node in found:
            if all(node is not other for other in unique):
                unique.append(node)
        current = unique
    return current


class FakeElement:
    def __init__(self, tag, *, cls="", attrs=None, text="", children=(), visible=True,
                 on_click=None, on_set_files=None, on_select=None, on_input_value=None):
        self.tag = tag
        self.classes = set(str(cls).split())
        self.attrs = dict(attrs or {})
        if cls:
            self.attrs.setdefault("class", cls)
        self.text = text
        self.children = list(children)
        self.visible = bool(visible)
        self.on_click = on_click
        self.on_set_files = on_set_files
        self.on_select = on_select
        self.on_input_value = on_input_value

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()

    def get_attribute(self, name):
        return self.attrs.get(name)

    def inner_text(self):
        parts = [self.text] + [child.inner_text() for child in self.children]
        return " ".join(part for part in parts if part).strip()

    def query_selector_all(self, selector):
        return _select(self, selector)

    def query_selector(self, selector):
        found = self.query_selector_all(selector)
        return found[0] if found else None

    # -- state. Playwright exposes both without a timeout, so neither takes one.
    def is_visible(self):
        return self.visible

    def is_disabled(self):
        """Playwright's own rule: the `disabled` attribute or aria-disabled."""
        if "disabled" in self.attrs:
            return True
        return str(self.attrs.get("aria-disabled") or "").casefold() == "true"

    # -- actions. Each one insists on an explicit bounded timeout. ----------
    @staticmethod
    def _require_timeout(timeout, what):
        if timeout is None:
            raise AssertionError(f"{what} was attempted without an explicit bounded timeout")

    def click(self, timeout=None, force=None):
        self._require_timeout(timeout, "a click")
        # A hidden element fails Playwright's actionability check and eventually
        # times out. Modelling it keeps "check visibility, then click" honest.
        if not self.visible and not force:
            raise Playwright.TimeoutError("element is not visible")
        if self.on_click is None:
            raise AssertionError(f"clicked an element with no behaviour: <{self.tag}>")
        self.on_click()

    def set_input_files(self, path, timeout=None):
        self._require_timeout(timeout, "a file selection")
        if self.on_set_files is None:
            raise AssertionError("set_input_files on an element that is not a file input")
        self.on_set_files(path)

    def select_option(self, value, force=None, timeout=None):
        self._require_timeout(timeout, "an option selection")
        if self.on_select is None:
            raise AssertionError("select_option on an element that is not a select")
        # A hidden backing select is only reachable with force. Modelling that is
        # the whole point: without it the fake would hide the actionability
        # problem that is one candidate cause of the production timeout.
        if self.attrs.get("aria-hidden") == "true" and not force:
            raise Playwright.TimeoutError("element is not visible")
        self.on_select(value)

    def input_value(self, timeout=None):
        self._require_timeout(timeout, "a value read-back")
        if self.on_input_value is None:
            raise AssertionError("input_value on an element that is not a form control")
        return self.on_input_value()


class FakePage:
    """Renders lazily from the site model, so a click is visible immediately."""

    def __init__(self, site):
        self.site = site

    @property
    def url(self):
        return self.site.current_url

    def goto(self, url, wait_until=None, timeout=None):
        if timeout is None:
            raise AssertionError("navigation was attempted without an explicit bounded timeout")
        self.site.navigate(url)

    def wait_for_load_state(self, state=None, timeout=None):
        if timeout is None:
            raise AssertionError("a load wait was attempted without an explicit bounded timeout")
        return None

    def query_selector_all(self, selector):
        probe = getattr(self.site, "probe", None)
        if probe is not None:
            probe(selector)
        return _select(self.site.render(), selector)

    def query_selector(self, selector):
        found = self.query_selector_all(selector)
        return found[0] if found else None

    def inner_text(self, selector, timeout=None):
        if selector != "body":
            raise AssertionError(f"unexpected page-level text read: {selector!r}")
        if timeout is None:
            raise AssertionError("a text read was attempted without an explicit bounded timeout")
        return self.site.body_text()


def _path_of(url: str) -> str:
    return urlsplit(url).path or "/"


# ===========================================================================
# WordPress admin model
# ===========================================================================
class FakeWordPress:
    def __init__(self, *, version=OLD_VERSION, active=False, present=True,
                 update_marker=False):
        self.version = version
        self.active = active
        self.present = present
        self.update_marker = update_marker
        self.current_url = deploy.PLUGINS_URL

        # Injectable anomalies.
        self.duplicate_row = False
        self.state_class = None        # force the <tr> class tokens
        self.action_link = None        # "activate" | "deactivate" | "both" | "none"
        self.version_text = None       # force the version cell text
        self.comparison_name = PLUGIN_NAME
        self.comparison_version = GOOD_VERSION
        self.comparison_tables = 1
        self.overwrite_links = 1
        self.no_file_input = False
        self.redirect_after_upload = None
        self.upload_replaces = True

        # Recorded behaviour.
        self.navigations: list[str] = []
        self.clicks: list[str] = []
        self.uploads: list[str] = []
        self.observed_at_upload: list[bool] = []
        self.lock_probe = lambda: False

    def navigate(self, url):
        self.navigations.append(url)
        self.current_url = url

    # -- rendering ---------------------------------------------------------
    def render(self):
        path = _path_of(self.current_url)
        if path.endswith("/plugins.php"):
            return self._plugins_screen()
        if path.endswith("/plugin-install.php"):
            return self._upload_screen()
        if path.endswith("/update.php"):
            return self._update_screen()
        return FakeElement("body")

    def _row_actions(self):
        mode = self.action_link or ("deactivate" if self.active else "activate")
        spans = []
        if mode in ("activate", "both"):
            spans.append(FakeElement("span", cls="activate", children=[
                FakeElement("a", text="Activate", on_click=self._activate)]))
        if mode in ("deactivate", "both"):
            spans.append(FakeElement("span", cls="deactivate", children=[
                FakeElement("a", text="Deactivate", on_click=self._deactivate)]))
        # Real WordPress always offers Delete here. It is modelled on purpose:
        # the tool must have no path that can reach it.
        spans.append(FakeElement("span", cls="delete", children=[
            FakeElement("a", text="Delete", on_click=self._delete)]))
        return FakeElement("div", cls="row-actions visible", children=spans)

    def _plugin_row(self):
        tokens = self.state_class or ("active" if self.active else "inactive")
        if self.update_marker:
            tokens += " update"
        version_cell = FakeElement(
            "div", cls="plugin-version-author-uri",
            text=self.version_text if self.version_text is not None
            else f"Version {self.version} | By FRP Depot",
        )
        return FakeElement("tr", cls=tokens, attrs={"data-plugin": PLUGIN_FILE}, children=[
            FakeElement("td", cls="plugin-title column-primary", children=[
                FakeElement("strong", text=PLUGIN_NAME), self._row_actions()]),
            FakeElement("td", cls="column-description desc", children=[version_cell]),
        ])

    def _other_plugin_row(self):
        """An unrelated plugin. Nothing the tool does may ever touch it."""
        return FakeElement(
            "tr", cls="active", attrs={"data-plugin": "akismet/akismet.php"}, children=[
                FakeElement("td", cls="plugin-title column-primary", children=[
                    FakeElement("strong", text="Akismet Anti-spam"),
                    FakeElement("div", cls="row-actions visible", children=[
                        FakeElement("span", cls="deactivate", children=[
                            FakeElement("a", text="Deactivate", on_click=self._forbidden)]),
                        FakeElement("span", cls="delete", children=[
                            FakeElement("a", text="Delete", on_click=self._forbidden)]),
                    ])]),
                FakeElement("td", cls="column-description desc", children=[
                    FakeElement("div", cls="plugin-version-author-uri",
                                text="Version 5.3 | By Automattic")]),
            ])

    def _plugins_screen(self):
        rows = [self._other_plugin_row()]
        if self.present:
            rows.append(self._plugin_row())
            if self.duplicate_row:
                rows.append(self._plugin_row())
            if self.update_marker:
                # WordPress emits a SECOND row carrying the same data-plugin.
                rows.append(FakeElement(
                    "tr", cls="plugin-update-tr active",
                    attrs={"data-plugin": PLUGIN_FILE},
                    children=[FakeElement("td", text="There is a new version available.")]))
        return FakeElement("body", children=[FakeElement("table", children=rows)])

    def _upload_screen(self):
        children = []
        if not self.no_file_input:
            children.append(FakeElement(
                "input", attrs={"type": "file", "name": "pluginzip"},
                on_set_files=self._set_files))
        children.append(FakeElement(
            "input", attrs={"id": "install-plugin-submit", "type": "submit"},
            on_click=self._submit_upload))
        return FakeElement("body", children=[FakeElement("form", children=children)])

    @staticmethod
    def _comparison_row(label, current, uploaded):
        return FakeElement("tr", children=[
            FakeElement("td", cls="name-label", text=label),
            FakeElement("td", text=current),
            FakeElement("td", text=uploaded),
        ])

    def _update_screen(self):
        tables = [
            FakeElement("table", cls="update-from-upload-comparison", children=[
                FakeElement("tr", children=[
                    FakeElement("th", text=""), FakeElement("th", text="Current"),
                    FakeElement("th", text="Uploaded")]),
                self._comparison_row("Plugin name", PLUGIN_NAME, self.comparison_name),
                self._comparison_row("Version", self.version, self.comparison_version),
                self._comparison_row("Author", "FRP Depot", "FRP Depot"),
            ])
            for _ in range(self.comparison_tables)
        ]
        links = [
            FakeElement("a", cls="update-from-upload-overwrite",
                        text="Replace current with uploaded", on_click=self._overwrite)
            for _ in range(self.overwrite_links)
        ]
        return FakeElement("body", children=tables + links)

    def body_text(self):
        return "WordPress admin screen"

    # -- behaviours --------------------------------------------------------
    def _activate(self):
        self.clicks.append("activate")
        self.active = True

    def _deactivate(self):
        self.clicks.append("deactivate")
        self.active = False

    def _delete(self):
        self.clicks.append("delete")
        raise AssertionError("the tool reached a Delete link")

    def _forbidden(self):
        self.clicks.append("other-plugin")
        raise AssertionError("the tool touched an unrelated plugin row")

    def _set_files(self, path):
        self.uploads.append(str(path))
        self.observed_at_upload.append(bool(self.lock_probe()))

    def _submit_upload(self):
        self.clicks.append("install-plugin-submit")
        self.navigate(self.redirect_after_upload
                      or f"{deploy.EXACT_ORIGIN}/wp-admin/update.php?action=upload-plugin")

    def _overwrite(self):
        self.clicks.append("overwrite")
        if self.upload_replaces:
            self.version = self.comparison_version
            self.active = False
        self.navigate(f"{deploy.EXACT_ORIGIN}/wp-admin/update.php?action=upload-plugin")


# ===========================================================================
# Storefront model
#
# Shaped after what the live FRP FW Pipe page WAS MEASURED to be on 2026-08-09 by
# a read-only inspection: each attribute row carries one HIDDEN BACKING SELECT and
# one VISIBLE `<li role="radio" data-value="...">` per option, with classes
# `variable-item button-variable-item`. There is no `input[type="radio"]` anywhere
# -- assuming there was is what made the first repair select every attribute through
# the hidden select, which changes values but never makes WooCommerce resolve a
# variation. `role_radios = False` models the plain WooCommerce dropdown, which is
# the only case where the backing-select fallback is legitimate.
#
# The variation id and the Add to cart button are derived from the selections, so
# "did the customer's clicks actually make this purchasable?" is a real question
# here rather than a flag the test sets.
# ===========================================================================
TIMEOUT_MARKERS = {
    "home_load": "nav:/",
    "product_load": "nav:/product/frp-fw-pipe/",
    "variation_form": "query:form.variations_form",
    SIZE_STEP: "select:SIZE",
    PRESSURE_STEP: "select:PRESSURE RATING",
    RESIN_STEP: "select:RESIN TYPE",
    "variation_ready": "read:variation_id",
    "add_to_cart": "click:add_to_cart",
    "checkout_load": "nav:/checkout/",
    "checkout_assertions": "text:/checkout/",
}

STOREFRONT_BODY = "FRP Depot storefront page with plenty of ordinary visible content."


class FakeStorefront:
    def __init__(self):
        self.message_count = 1
        self.checkout_form = False
        self.payment_form = False
        self.checkout_fatal = False
        self.checkout_blank = False
        self.home_fatal = False
        self.home_blank = False
        self.product_fatal = False
        self.product_blank = False

        self.variation_form = True
        self.variation_forms = 1
        self.options_present = True
        self.duplicate_option = False
        self.wrong_values: dict[str, str] = {}
        self.duplicate_attribute_row = False
        self.backing_selects = 1

        # Visible customer controls.
        self.role_radios = True
        self.duplicate_role_radio = False
        self.role_radio_visible = True
        self.role_radio_disabled = False
        self.role_radio_values: dict[str, str] = {}   # force a wrong data-value
        self.radio_wires_backing = True

        # Readiness.
        self.variation_id_controls = 1
        self.variation_resolves = True
        self.variation_id_text: str | None = None
        self.add_to_cart_present = True
        self.add_to_cart_buttons = 1
        self.add_to_cart_visible = True
        self.add_to_cart_disabled_attr = False
        self.add_to_cart_stays_disabled = False

        self.timeout_at: str | None = None

        self.current_url = deploy.HOME_URL
        self.navigations: list[str] = []
        self.selected: list[tuple[str, str]] = []
        self.chosen: dict[str, str] = {}
        self.added = 0
        self.context_starts = 0
        self.carried_over: list[dict[str, str]] = []

    # -- recording ---------------------------------------------------------
    def reset_recording(self):
        self.navigations.clear()
        self.selected.clear()
        self.chosen.clear()
        self.added = 0
        self.context_starts = 0
        self.carried_over.clear()

    def start_context(self):
        """A brand-new anonymous browser: nothing chosen, back at the homepage.

        The selections a customer makes live in the BROWSER, so a genuinely fresh
        context must start with none of them. `carried_over` records what the
        previous context had, which is how the tests prove each rehearsal drove the
        page from nothing rather than inheriting a ready state.
        """
        self.context_starts += 1
        self.carried_over.append(dict(self.chosen))
        self.chosen.clear()
        self.current_url = deploy.HOME_URL

    def _maybe_timeout(self, marker):
        """One-shot, so an injected failure does not also break the rollback."""
        if self.timeout_at == marker:
            self.timeout_at = None
            raise Playwright.TimeoutError("timed out")

    def navigate(self, url):
        self._maybe_timeout("nav:" + _path_of(url))
        self.navigations.append(url)
        self.current_url = url

    def probe(self, selector):
        self._maybe_timeout("query:" + selector)

    # -- rendering ---------------------------------------------------------
    @staticmethod
    def option_value(display):
        return str(display).casefold().replace(" ", "-")

    def render(self):
        path = _path_of(self.current_url)
        if path == "/product/frp-fw-pipe/":
            return self._product()
        if path == "/checkout/":
            return self._checkout()
        return FakeElement("body")

    def _attribute_row(self, label, value):
        display = self.wrong_values.get(label, value)
        chosen = self.option_value(display)
        options = [FakeElement("option", attrs={"value": ""}, text="Choose an option")]
        if self.options_present:
            options.append(FakeElement("option", attrs={"value": chosen}, text=display))
            if self.duplicate_option:
                options.append(
                    FakeElement("option", attrs={"value": chosen + "-alt"}, text=display))
        controls = [
            FakeElement(
                "select",
                attrs={"name": f"attribute_pa_{label.casefold().replace(' ', '_')}",
                       "aria-hidden": "true"},
                children=options,
                on_select=lambda picked, label=label: self._choose(label, picked, "select"),
                on_input_value=lambda label=label: self.chosen.get(label, ""))
            for _ in range(self.backing_selects)
        ]
        if self.role_radios and self.options_present:
            # The live shape: a visible <li role="radio" data-value="DISPLAY"> whose
            # data-value is the DISPLAY term, not the select's slug. 150PSI vs
            # 150psi is exactly that difference, and it is why the tool matches the
            # role radio on the required value and the readback on the option value.
            data_value = self.role_radio_values.get(label, display)
            tokens = "variable-item button-variable-item"
            if self.role_radio_disabled:
                tokens += " disabled"
            for _ in range(2 if self.duplicate_role_radio else 1):
                controls.append(FakeElement(
                    "li", cls=tokens,
                    attrs={"role": "radio", "data-value": data_value},
                    visible=self.role_radio_visible,
                    on_click=lambda label=label, chosen=chosen:
                        self._choose(label, chosen, "radio")))
        return FakeElement("tr", children=[
            FakeElement("th", cls="label", text=label),
            FakeElement("td", cls="value", children=[
                FakeElement("ul", cls="variable-items-wrapper", children=controls)])])

    def _all_chosen(self):
        return all(self.chosen.get(label) for label, _ in deploy.REQUIRED_VARIATION)

    def _variation_id_value(self):
        if self.variation_id_text is not None:
            return self.variation_id_text
        return "9182" if (self._all_chosen() and self.variation_resolves) else "0"

    def _read_variation_id(self):
        self._maybe_timeout("read:variation_id")
        return self._variation_id_value()

    def _variation_resolved(self):
        raw = self._variation_id_value()
        return raw.isdigit() and int(raw) > 0

    def _variations_form(self):
        rows = []
        for label, value in deploy.REQUIRED_VARIATION:
            rows.append(self._attribute_row(label, value))
            if self.duplicate_attribute_row:
                rows.append(FakeElement("tr", children=[
                    FakeElement("th", cls="label", text=label),
                    FakeElement("td", cls="value")]))
        children = [FakeElement("table", children=rows)]
        for _ in range(self.variation_id_controls):
            children.append(FakeElement(
                "input", cls="variation_id",
                attrs={"type": "hidden", "name": "variation_id"},
                on_input_value=self._read_variation_id))
        if self.add_to_cart_present:
            # WooCommerce's real behaviour: the button carries
            # `disabled wc-variation-selection-needed` until a variation resolves.
            tokens = "single_add_to_cart_button button alt"
            if self.add_to_cart_stays_disabled or not self._variation_resolved():
                tokens += " disabled wc-variation-selection-needed"
            attrs = {"disabled": "disabled"} if self.add_to_cart_disabled_attr else {}
            for _ in range(self.add_to_cart_buttons):
                children.append(FakeElement(
                    "button", cls=tokens, attrs=attrs, text="Add to cart",
                    visible=self.add_to_cart_visible, on_click=self._add_to_cart))
        return FakeElement("form", cls="variations_form cart", children=children)

    def _product(self):
        if not self.variation_form:
            return FakeElement("body")
        return FakeElement("body", children=[
            self._variations_form() for _ in range(self.variation_forms)])

    def _checkout(self):
        children = []
        if self.checkout_form:
            children.append(FakeElement("form", cls="checkout woocommerce-checkout"))
        if self.payment_form:
            children.append(FakeElement("div", attrs={"id": "payment"}))
            children.append(FakeElement("button", attrs={
                "id": "place_order", "name": "woocommerce_checkout_place_order"}))
        return FakeElement("body", children=children)

    def body_text(self):
        path = _path_of(self.current_url)
        self._maybe_timeout("text:" + path)
        if path == "/checkout/":
            if self.checkout_blank:
                return "  "
            if self.checkout_fatal:
                return "There has been a critical error on this website."
            body = ["Checkout", "Your order"]
            body.extend([deploy.EXACT_MESSAGE] * self.message_count)
            return "\n".join(body) + "\nFRP Depot storefront footer content here."
        if path == "/":
            if self.home_blank:
                return "  "
            if self.home_fatal:
                return "There has been a critical error on this website."
        if path == "/product/frp-fw-pipe/":
            if self.product_blank:
                return "  "
            if self.product_fatal:
                return "There has been a critical error on this website."
        return STOREFRONT_BODY

    # -- behaviours --------------------------------------------------------
    def _choose(self, label, value, source):
        self._maybe_timeout(f"select:{label}")
        self.selected.append((label, value))
        if source == "radio" and not self.radio_wires_backing:
            return
        self.chosen[label] = value

    def _add_to_cart(self):
        self._maybe_timeout("click:add_to_cart")
        self.added += 1
        self.navigate(deploy.CART_URL)


# ===========================================================================
# Harness
# ===========================================================================
class Harness(unittest.TestCase):
    """Redirects plan/receipt writes into a temp dir and installs fake sessions."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wp-plugin-plans-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.plan_dir = self.tmp / "plans"
        self.plan_dir.mkdir()
        self.preflight_dir = self.tmp / "preflight"
        self.preflight_dir.mkdir()
        patches = (
            mock.patch.object(deploy, "PLAN_DIR", self.plan_dir),
            mock.patch.object(deploy, "PREFLIGHT_DIR", self.preflight_dir),
            mock.patch.object(deploy, "RECEIPTS", self.tmp / "receipts.jsonl"),
            # The fake DOM is synchronous, so a correct selection reads back on
            # the first poll and a wrong one is wrong immediately. A negative
            # ceiling puts the deadline strictly in the past, so a mismatch
            # raises on the first pass instead of spinning out the real 8s.
            mock.patch.object(deploy, "READBACK_TIMEOUT_MS", -1),
            mock.patch.object(deploy, "READBACK_POLL_SECONDS", 0),
            # Same reasoning for readiness: the fake resolves synchronously, so a
            # ready page is ready on the first pass and an unready one never
            # becomes ready. A deadline in the past turns the poll into one probe.
            mock.patch.object(deploy, "READINESS_TIMEOUT_MS", -1),
            mock.patch.object(deploy, "READINESS_POLL_SECONDS", 0),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.wp = FakeWordPress()
        self.shop = FakeStorefront()
        self.admin_opens = 0
        self.anon_opens = 0
        self.anon_allowlists: list[frozenset[str]] = []
        self.open_sessions = 0
        self.max_concurrent_sessions = 0

    def install_sessions(self):
        @contextlib.contextmanager
        def fake_admin():
            self.admin_opens += 1
            self.wp.current_url = deploy.PLUGINS_URL
            with self._tracked():
                yield deploy.AdminPage(FakePage(self.wp))

        @contextlib.contextmanager
        def fake_anon(allowed_paths=deploy.ALLOWED_PUBLIC_PATHS):
            self.anon_opens += 1
            self.anon_allowlists.append(allowed_paths)
            # A brand-new context carries no cookie and no storage, so it also
            # carries none of the previous context's variation selections.
            self.shop.start_context()
            with self._tracked():
                yield deploy.PublicPage(FakePage(self.shop), allowed_paths)

        for name, replacement in (("admin_session", fake_admin),
                                  ("anonymous_session", fake_anon)):
            patch = mock.patch.object(deploy, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)

    @contextlib.contextmanager
    def _tracked(self):
        self.open_sessions += 1
        self.max_concurrent_sessions = max(self.max_concurrent_sessions, self.open_sessions)
        try:
            yield
        finally:
            self.open_sessions -= 1

    def forbid_sessions(self):
        """Any browser access at all becomes a test failure."""
        def explode(*_args, **_kwargs):
            raise AssertionError("a browser session was opened before it was allowed")

        for name in ("admin_session", "anonymous_session"):
            patch = mock.patch.object(deploy, name, explode)
            patch.start()
            self.addCleanup(patch.stop)

    def preflight(self):
        before = set(self.preflight_dir.glob("*.json"))
        with contextlib.redirect_stdout(io.StringIO()):
            deploy.command_preflight_validation(argparse.Namespace())
        fresh = sorted(set(self.preflight_dir.glob("*.json")) - before)
        self.assertEqual(len(fresh), 1, "a preflight should produce exactly one evidence file")
        return fresh[0]

    def failed_preflight(self):
        """Run a preflight that is expected to fail; return its evidence path."""
        before = set(self.preflight_dir.glob("*.json"))
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(deploy.DeploymentError):
                deploy.command_preflight_validation(argparse.Namespace())
        fresh = sorted(set(self.preflight_dir.glob("*.json")) - before)
        self.assertEqual(len(fresh), 1)
        return fresh[0]

    def stage(self, action, preflight=None):
        handler = {
            "plugin_replace": deploy.command_stage_replace,
            "plugin_activate": deploy.command_stage_activate,
            "plugin_deactivate": deploy.command_stage_deactivate,
        }[action]
        if action == "plugin_activate" and preflight is None:
            preflight = self.preflight()
        before = set(self.plan_dir.glob("*.json"))
        with contextlib.redirect_stdout(io.StringIO()):
            handler(argparse.Namespace(preflight=str(preflight) if preflight else None))
        fresh = sorted(set(self.plan_dir.glob("*.json")) - before)
        self.assertEqual(len(fresh), 1, "staging should produce exactly one plan")
        return fresh[0]

    def stage_activation(self):
        """Stage an activation and forget everything the preflight recorded."""
        self.evidence_path = self.preflight()
        plan_path = self.stage("plugin_activate", preflight=self.evidence_path)
        self.shop.reset_recording()
        self.anon_opens = 0
        self.anon_allowlists.clear()
        return plan_path

    def evidence_sha256(self):
        return json.loads(self.evidence_path.read_text(encoding="utf-8"))["sha256"]

    def commit(self, action, plan_path, approval=deploy.APPROVAL_WORD):
        handler = {
            "plugin_replace": deploy.command_commit_replace,
            "plugin_activate": deploy.command_commit_activate,
            "plugin_deactivate": deploy.command_commit_deactivate,
        }[action]
        args = argparse.Namespace(plan=str(plan_path), approval=approval)
        with contextlib.redirect_stdout(io.StringIO()):
            handler(args)

    def rehash(self, plan_path, **changes):
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        plan.pop("sha256")
        plan.update(changes)
        plan["sha256"] = deploy.digest_for(plan)
        Path(plan_path).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        return plan_path

    def rehash_evidence(self, evidence_path, **changes):
        evidence = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
        evidence.pop("sha256")
        evidence.update(changes)
        evidence["sha256"] = deploy.digest_for(evidence)
        Path(evidence_path).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        return evidence_path

    def result_of(self, plan_path):
        return json.loads(deploy.result_path(Path(plan_path)).read_text(encoding="utf-8"))

    def lock_of(self, plan_path):
        return json.loads(deploy.lock_path(Path(plan_path)).read_text(encoding="utf-8"))


# ===========================================================================
# Fixed identity and closure
# ===========================================================================
class TestFixedIdentity(unittest.TestCase):
    def test_origin_plugin_and_slug_are_the_exact_fixed_values(self):
        self.assertEqual(deploy.EXACT_ORIGIN, "https://frpdepots.com")
        self.assertEqual(deploy.ALLOWED_HOST, "frpdepots.com")
        self.assertEqual(deploy.PLUGIN_NAME, "FRP Depot Freight Checkout Guard")
        self.assertEqual(deploy.PLUGIN_SLUG, "frpdepot-freight-checkout-guard")
        self.assertEqual(
            deploy.PLUGIN_FILE,
            "frpdepot-freight-checkout-guard/frpdepot-freight-checkout-guard.php")
        self.assertEqual(deploy.CDP_ENDPOINT, "http://127.0.0.1:9229")

    def test_approved_and_withdrawn_artifacts_are_distinct_and_fixed(self):
        self.assertEqual(deploy.ARTIFACT_VERSION, "1.0.1")
        self.assertEqual(
            deploy.ARTIFACT_SHA256,
            "fe6fa440ea3a08169bf568ae0fbb06f666ad71c1110e58f9b2b6bb0acc8be6cb")
        self.assertEqual(deploy.WITHDRAWN_VERSION, "1.0.0")
        self.assertEqual(
            deploy.WITHDRAWN_SHA256,
            "4d8396d95baf0907754730e578ad4c41b98908f77992718c41b293434e07fe25")
        self.assertNotEqual(deploy.ARTIFACT_SHA256, deploy.WITHDRAWN_SHA256)
        self.assertNotEqual(deploy.ARTIFACT_VERSION, deploy.WITHDRAWN_VERSION)
        self.assertEqual(Path(deploy.ARTIFACT_PATH), Path(
            r"C:\FRPDepot\Dado\Tools\woocommerce\freight_checkout_guard"
            r"\frpdepot-freight-checkout-guard.zip"))

    def test_actions_are_a_closed_set_of_three(self):
        self.assertEqual(set(deploy.ACTIONS),
                         {"plugin_replace", "plugin_activate", "plugin_deactivate"})

    def test_navigation_is_a_closed_allowlist(self):
        self.assertEqual(deploy.ALLOWED_ADMIN_PATHS, frozenset({
            "/wp-admin/plugins.php", "/wp-admin/plugin-install.php", "/wp-admin/update.php"}))
        self.assertEqual(deploy.ALLOWED_PUBLIC_PATHS, frozenset({
            "/", "/product/frp-fw-pipe/", "/cart/", "/checkout/"}))

    def test_the_preflight_allowlist_is_narrower_and_excludes_cart_and_checkout(self):
        self.assertEqual(deploy.PREFLIGHT_PUBLIC_PATHS,
                         frozenset({"/", "/product/frp-fw-pipe/"}))
        self.assertTrue(deploy.PREFLIGHT_PUBLIC_PATHS < deploy.ALLOWED_PUBLIC_PATHS)
        for url in (deploy.CART_URL, deploy.CHECKOUT_URL):
            with self.subTest(url=url), self.assertRaises(deploy.DeploymentError):
                deploy.assert_public_url(url, deploy.PREFLIGHT_PUBLIC_PATHS)

    def test_required_variation_and_message_are_exact(self):
        self.assertEqual(deploy.REQUIRED_VARIATION,
                         (("SIZE", "1/2\""), ("PRESSURE RATING", "150PSI"),
                          ("RESIN TYPE", "D411")))
        self.assertEqual(deploy.EXACT_MESSAGE, "Contact us for a freight quote.")
        self.assertEqual(deploy.PRODUCT_URL, "https://frpdepots.com/product/frp-fw-pipe/")

    def test_validation_steps_are_the_fixed_named_sequence(self):
        self.assertEqual(deploy.SELECT_STEPS,
                         ("select_SIZE", "select_PRESSURE_RATING", "select_RESIN_TYPE"))
        self.assertEqual(deploy.PREFLIGHT_STEPS, (
            "home_load", "product_load", "variation_form",
            "select_SIZE", "select_PRESSURE_RATING", "select_RESIN_TYPE",
            "variation_ready"))
        self.assertEqual(deploy.VALIDATION_STEPS, (
            "home_load", "product_load", "variation_form",
            "select_SIZE", "select_PRESSURE_RATING", "select_RESIN_TYPE",
            "variation_ready", "add_to_cart", "checkout_load", "checkout_assertions"))
        self.assertEqual(set(TIMEOUT_MARKERS), set(deploy.VALIDATION_STEPS))

    def test_selection_methods_are_a_closed_pair(self):
        self.assertEqual(deploy.SELECTION_METHODS,
                         frozenset({"visible_role_radio", "backing_select"}))

    def test_the_versions_moved_together_so_old_evidence_and_plans_are_dead(self):
        self.assertEqual(deploy.TOOL_VERSION, "1.2.0")
        self.assertEqual(deploy.SCHEMA_VERSION, 3)
        self.assertEqual(deploy.PREFLIGHT_SCHEMA_VERSION, 2)
        self.assertEqual(deploy.PREFLIGHT_RUNS, 3)

    def test_timeouts_are_explicit_and_bounded(self):
        for name in ("NAV_TIMEOUT_MS", "LOAD_STATE_TIMEOUT_MS", "ACTION_TIMEOUT_MS",
                     "READBACK_TIMEOUT_MS", "READINESS_TIMEOUT_MS"):
            with self.subTest(name=name):
                value = getattr(deploy, name)
                self.assertIsInstance(value, int)
                self.assertGreater(value, 0)
                self.assertLessEqual(value, 60_000)

    def test_off_origin_urls_are_refused(self):
        for url in ("http://frpdepots.com/wp-admin/plugins.php",
                    "https://frpdepots.com.evil.test/wp-admin/plugins.php",
                    "https://frpdepots.com:8443/wp-admin/plugins.php",
                    "https://user:pw@frpdepots.com/wp-admin/plugins.php",
                    "https://www.frpdepots.com/wp-admin/plugins.php",
                    "https://evil.test/wp-admin/plugins.php"):
            with self.subTest(url=url), self.assertRaises(deploy.DeploymentError):
                deploy.assert_origin(url)
        deploy.assert_origin(deploy.PLUGINS_URL)

    def test_admin_allowlist_refuses_login_settings_and_other_screens(self):
        for url in (f"{deploy.EXACT_ORIGIN}/wp-login.php",
                    f"{deploy.EXACT_ORIGIN}/wp-admin/options-general.php",
                    f"{deploy.EXACT_ORIGIN}/wp-admin/admin.php?page=wc-settings",
                    f"{deploy.EXACT_ORIGIN}/wp-admin/users.php",
                    f"{deploy.EXACT_ORIGIN}/wp-admin/theme-editor.php",
                    f"{deploy.EXACT_ORIGIN}/wp-admin/edit.php"):
            with self.subTest(url=url), self.assertRaises(deploy.DeploymentError):
                deploy.assert_admin_url(url)
        deploy.assert_admin_url(deploy.PLUGINS_URL)
        deploy.assert_admin_url(deploy.UPLOAD_URL)

    def test_public_allowlist_refuses_account_and_order_pages(self):
        for url in (f"{deploy.EXACT_ORIGIN}/my-account/",
                    f"{deploy.EXACT_ORIGIN}/checkout/order-received/91/"):
            with self.subTest(url=url), self.assertRaises(deploy.DeploymentError):
                deploy.assert_public_url(url)


# ===========================================================================
# Artifact verification
# ===========================================================================
class TestArtifact(unittest.TestCase):
    def test_real_artifact_on_disk_verifies_as_1_0_1(self):
        record = deploy.verify_artifact()
        self.assertEqual(record["sha256"], deploy.ARTIFACT_SHA256)
        self.assertEqual(record["version"], "1.0.1")
        self.assertEqual(tuple(record["members"]), tuple(sorted(deploy.ARTIFACT_MEMBERS)))
        self.assertIn(f"{deploy.PLUGIN_SLUG}/assets/frpdepot-freight-notice.js",
                      record["members"])
        self.assertEqual(len(record["members"]), 4)

    def test_only_the_hard_coded_artifact_path_is_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            stray = Path(folder) / "frpdepot-freight-checkout-guard.zip"
            stray.write_bytes(b"PK\x03\x04not-a-real-plugin")
            with self.assertRaises(deploy.DeploymentError) as caught:
                deploy.verify_artifact(stray)
        self.assertIn("hard-coded plugin artifact", str(caught.exception))

    def test_withdrawn_hash_is_refused_ahead_of_the_match_check(self):
        with mock.patch.object(deploy, "WITHDRAWN_SHA256", deploy.ARTIFACT_SHA256):
            with self.assertRaises(deploy.DeploymentError) as caught:
                deploy.verify_artifact()
        self.assertIn("withdrawn 1.0.0", str(caught.exception))

    def test_withdrawn_version_is_refused(self):
        with mock.patch.object(deploy, "WITHDRAWN_VERSION", "1.0.1"):
            with self.assertRaises(deploy.DeploymentError) as caught:
                deploy.verify_artifact()
        self.assertIn("withdrawn version", str(caught.exception))

    def test_hash_mismatch_is_refused(self):
        with mock.patch.object(deploy, "ARTIFACT_SHA256", "0" * 64):
            with self.assertRaises(deploy.DeploymentError) as caught:
                deploy.verify_artifact()
        self.assertIn("SHA-256 does not match", str(caught.exception))

    def test_member_mismatch_is_refused(self):
        trimmed = tuple(m for m in deploy.ARTIFACT_MEMBERS if not m.endswith(".js"))
        with mock.patch.object(deploy, "ARTIFACT_MEMBERS", trimmed):
            with self.assertRaises(deploy.DeploymentError) as caught:
                deploy.verify_artifact()
        self.assertIn("members are not the approved set", str(caught.exception))

    def test_declared_version_mismatch_is_refused(self):
        with mock.patch.object(deploy, "ARTIFACT_VERSION", "9.9.9"):
            with self.assertRaises(deploy.DeploymentError) as caught:
                deploy.verify_artifact()
        self.assertIn("declares version", str(caught.exception))

    def test_missing_artifact_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "frpdepot-freight-checkout-guard.zip"
            with mock.patch.object(deploy, "ARTIFACT_PATH", missing):
                with self.assertRaises(deploy.DeploymentError) as caught:
                    deploy.verify_artifact()
        self.assertIn("missing", str(caught.exception))


# ===========================================================================
# Row reading, scoping and ambiguity
# ===========================================================================
class TestRowReading(Harness):
    def read(self):
        return deploy.AdminPage(FakePage(self.wp)).read_row()

    def test_reads_the_fixed_row_only(self):
        self.assertEqual(self.read(), deploy.project_row(True, False, OLD_VERSION, False))

    def test_pending_update_row_does_not_make_the_target_ambiguous(self):
        self.wp.update_marker = True
        row = self.read()
        self.assertTrue(row["update_marker"])
        self.assertFalse(row["active"])
        self.assertEqual(row["version"], OLD_VERSION)

    def test_missing_row_is_refused(self):
        self.wp.present = False
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.read()
        self.assertIn("not present", str(caught.exception))

    def test_duplicate_rows_are_refused_as_ambiguous(self):
        self.wp.duplicate_row = True
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.read()
        self.assertIn("ambiguous", str(caught.exception))

    def test_state_class_and_action_link_must_agree(self):
        self.wp.state_class = "inactive"
        self.wp.action_link = "deactivate"
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.read()
        self.assertIn("disagree", str(caught.exception))

    def test_both_actions_present_is_ambiguous(self):
        self.wp.action_link = "both"
        with self.assertRaises(deploy.DeploymentError):
            self.read()

    def test_neither_action_present_is_ambiguous(self):
        self.wp.action_link = "none"
        with self.assertRaises(deploy.DeploymentError):
            self.read()

    def test_row_without_a_state_class_is_refused(self):
        self.wp.state_class = "plugin-row"
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.read()
        self.assertIn("exactly one of active/inactive", str(caught.exception))

    def test_unreadable_version_is_refused(self):
        self.wp.version_text = "By FRP Depot"
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.read()
        self.assertIn("readable version", str(caught.exception))

    def test_reading_off_origin_is_refused(self):
        self.wp.current_url = "https://example.test/wp-admin/plugins.php"
        with self.assertRaises(deploy.DeploymentError):
            self.read()

    def test_projection_carries_no_page_text(self):
        self.assertEqual(set(self.read()), {"present", "active", "version", "update_marker",
                                            "plugin_file", "fingerprint"})

    def test_fingerprint_moves_with_every_projected_field(self):
        base = deploy.project_row(True, False, GOOD_VERSION, False)
        for changed in (deploy.project_row(True, True, GOOD_VERSION, False),
                        deploy.project_row(True, False, OLD_VERSION, False),
                        deploy.project_row(True, False, GOOD_VERSION, True)):
            self.assertNotEqual(base["fingerprint"], changed["fingerprint"])


# ===========================================================================
# Variation selection: visible radio first, hidden backing select as fallback
# ===========================================================================
class TestVariationSelection(Harness):
    def setUp(self):
        super().setUp()
        self.shop.current_url = deploy.PRODUCT_URL
        self.public = deploy.PublicPage(FakePage(self.shop))

    def form(self):
        return self.public.variations_form()

    def select_all(self):
        form = self.form()
        return {label: self.public.select_attribute(form, label, value)
                for label, value in deploy.REQUIRED_VARIATION}

    def test_the_live_customer_control_is_a_visible_role_radio_with_a_data_value(self):
        """Pin the shape the 2026-08-09 live inspection actually measured.

        If the theme ever goes back to <input type="radio"> this test fails loudly
        rather than the tool silently taking the hidden-select branch again.
        """
        row = self.public._attribute_scope(self.form(), "SIZE")
        self.assertEqual(len(row.query_selector_all('input[type="radio"]')), 0)
        candidates = row.query_selector_all(deploy.ROLE_RADIO_SELECTOR)
        self.assertEqual(len(candidates), 1)
        control = candidates[0]
        self.assertEqual(control.tag, "li")
        self.assertEqual(control.get_attribute("role"), "radio")
        self.assertEqual(control.get_attribute("data-value"), '1/2"')
        self.assertTrue(control.is_visible())
        self.assertIn("variable-item", control.classes)
        self.assertEqual(len(row.query_selector_all("select")), 1)
        self.assertEqual(row.query_selector("select").get_attribute("aria-hidden"), "true")

    def test_the_visible_role_radio_is_preferred_over_the_hidden_select(self):
        methods = self.select_all()
        self.assertEqual(set(methods.values()), {"visible_role_radio"})
        self.assertEqual(self.shop.chosen, {
            "SIZE": '1/2"', "PRESSURE RATING": "150psi", "RESIN TYPE": "d411"})
        self.assertEqual([label for label, _ in self.shop.selected],
                         ["SIZE", "PRESSURE RATING", "RESIN TYPE"])

    def test_the_required_value_is_matched_in_python_not_built_into_a_selector(self):
        """The selector is value-free; the exact match happens in Python."""
        self.assertEqual(deploy.ROLE_RADIO_SELECTOR, '[role="radio"][data-value]')
        for banned in ('1/2"', "150PSI", "D411", "/"):
            self.assertNotIn(banned, deploy.ROLE_RADIO_SELECTOR)
        seen: list[str] = []
        row = self.public._attribute_scope(self.form(), "SIZE")
        original = row.query_selector_all

        def watched(selector):
            seen.append(selector)
            return original(selector)

        row.query_selector_all = watched
        self.public._role_radio(row, '1/2"')
        self.assertEqual(seen, [deploy.ROLE_RADIO_SELECTOR])

    def test_a_near_miss_data_value_is_not_accepted(self):
        """Exact string equality: no casefolding, no whitespace forgiveness."""
        row = self.public._attribute_scope(self.form(), "PRESSURE RATING")
        self.assertEqual(row.query_selector(deploy.ROLE_RADIO_SELECTOR)
                         .get_attribute("data-value"), "150PSI")
        for near in ("150psi", " 150PSI", "150PSI "):
            with self.subTest(near=near), self.assertRaises(deploy.ValidationRefusal) as caught:
                self.public._role_radio(row, near)
            self.assertEqual(caught.exception.code, "role_radio_value_missing")

    def test_hidden_backing_select_is_the_fallback_only_when_no_role_radio_exists(self):
        self.shop.role_radios = False
        methods = self.select_all()
        self.assertEqual(set(methods.values()), {"backing_select"})
        self.assertEqual(self.shop.chosen["SIZE"], '1/2"')

    def test_no_fallback_when_role_radios_exist_but_the_exact_value_is_absent(self):
        """The 1.1.0 bug, made impossible: never guess through the hidden select."""
        self.shop.role_radio_values = {"SIZE": '3/4"'}
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.select_all()
        self.assertEqual(caught.exception.code, "role_radio_value_missing")
        self.assertEqual(self.shop.selected, [], "nothing may be chosen by any route")
        self.assertEqual(self.shop.chosen, {})

    def test_the_hidden_select_needs_force_which_the_tool_supplies(self):
        """Without force the fake raises, exactly as a real hidden select would."""
        self.shop.role_radios = False
        form = self.form()
        row = self.public._attribute_scope(form, "SIZE")
        select = self.public._backing_select(row)
        with self.assertRaises(Playwright.TimeoutError):
            select.select_option('1/2"', timeout=deploy.ACTION_TIMEOUT_MS)
        select.select_option('1/2"', force=True, timeout=deploy.ACTION_TIMEOUT_MS)
        self.assertEqual(self.shop.chosen["SIZE"], '1/2"')

    def test_a_role_radio_that_does_not_wire_the_backing_select_is_refused(self):
        self.shop.radio_wires_backing = False
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.select_all()
        self.assertEqual(caught.exception.code, "selection_readback_mismatch")

    def test_the_backing_value_must_read_back_exactly_after_a_visible_click(self):
        methods = self.select_all()
        self.assertEqual(methods["PRESSURE RATING"], "visible_role_radio")
        # The click carried the SELECT's option value, not the visible label.
        self.assertEqual(self.shop.chosen["PRESSURE RATING"], "150psi")

    def test_two_role_radios_for_one_value_are_ambiguous(self):
        self.shop.duplicate_role_radio = True
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.select_all()
        self.assertEqual(caught.exception.code, "role_radio_ambiguous")
        self.assertEqual(self.shop.selected, [], "nothing may be chosen when ambiguous")

    def test_a_hidden_role_radio_is_refused_rather_than_clicked(self):
        self.shop.role_radio_visible = False
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.select_all()
        self.assertEqual(caught.exception.code, "role_radio_not_visible")
        self.assertEqual(self.shop.selected, [])

    def test_a_disabled_role_radio_is_refused_rather_than_clicked(self):
        self.shop.role_radio_disabled = True
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.select_all()
        self.assertEqual(caught.exception.code, "role_radio_disabled")
        self.assertEqual(self.shop.selected, [])

    def test_an_aria_disabled_role_radio_is_refused(self):
        row = self.public._attribute_scope(self.form(), "SIZE")
        row.query_selector(deploy.ROLE_RADIO_SELECTOR).attrs["aria-disabled"] = "true"
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.public._role_radio(row, '1/2"')
        self.assertEqual(caught.exception.code, "role_radio_disabled")

    def test_a_missing_option_is_refused(self):
        self.shop.options_present = False
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.select_all()
        self.assertEqual(caught.exception.code, "option_missing")

    def test_a_different_offered_value_is_refused_rather_than_approximated(self):
        self.shop.wrong_values = {"SIZE": '3/4"'}
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.select_all()
        self.assertEqual(caught.exception.code, "option_missing")
        self.assertEqual(self.shop.selected, [])

    def test_two_options_claiming_the_same_value_are_ambiguous(self):
        self.shop.duplicate_option = True
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.select_all()
        self.assertEqual(caught.exception.code, "option_ambiguous")

    def test_a_duplicated_attribute_row_is_ambiguous(self):
        self.shop.duplicate_attribute_row = True
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.select_all()
        self.assertEqual(caught.exception.code, "attribute_row_ambiguous")

    def test_a_missing_attribute_row_is_refused(self):
        form = self.form()
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.public.select_attribute(form, "COLOUR", "blue")
        self.assertEqual(caught.exception.code, "attribute_row_missing")

    def test_two_backing_selects_in_one_row_are_ambiguous(self):
        self.shop.backing_selects = 2
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.select_all()
        self.assertEqual(caught.exception.code, "backing_select_ambiguous")

    def test_a_row_with_no_backing_select_is_refused(self):
        self.shop.backing_selects = 0
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.select_all()
        self.assertEqual(caught.exception.code, "backing_select_missing")

    def test_a_missing_variations_form_is_refused(self):
        self.shop.variation_form = False
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.form()
        self.assertEqual(caught.exception.code, "variations_form_missing")

    def test_two_variations_forms_are_ambiguous(self):
        self.shop.variation_forms = 2
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.form()
        self.assertEqual(caught.exception.code, "variations_form_ambiguous")

    def test_every_refusal_code_is_from_the_closed_vocabulary(self):
        self.assertIn("selection_readback_mismatch", deploy.VALIDATION_CODES)
        with self.assertRaises(deploy.DeploymentError):
            deploy.ValidationRefusal("something_that_leaks_page_text")
        self.assertNotIn("radio_ambiguous", deploy.VALIDATION_CODES)


# ===========================================================================
# The variation_ready gate: does WOOCOMMERCE think this is purchasable?
# ===========================================================================
class TestVariationReadiness(Harness):
    def setUp(self):
        super().setUp()
        self.shop.current_url = deploy.PRODUCT_URL
        self.public = deploy.PublicPage(FakePage(self.shop))

    def select_all(self):
        form = self.public.variations_form()
        for label, value in deploy.REQUIRED_VARIATION:
            self.public.select_attribute(form, label, value)

    def expect(self, code):
        with self.assertRaises(deploy.ValidationRefusal) as caught:
            self.public.require_variation_ready()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_a_resolved_variation_with_an_enabled_button_is_ready(self):
        self.select_all()
        self.assertEqual(self.public.require_variation_ready(),
                         {"variation_resolved": True, "add_to_cart_enabled": True})

    def test_readiness_reports_only_booleans_and_never_the_variation_id(self):
        self.select_all()
        ready = self.public.require_variation_ready()
        self.assertEqual(set(ready), {"variation_resolved", "add_to_cart_enabled"})
        for value in ready.values():
            self.assertIsInstance(value, bool)
        self.assertNotIn("9182", json.dumps(ready))

    def test_selections_alone_are_not_readiness(self):
        """The 1.1.0 hole: three values set, WooCommerce still unconvinced."""
        self.shop.variation_resolves = False
        self.select_all()
        self.assertEqual(set(self.shop.chosen),
                         {"SIZE", "PRESSURE RATING", "RESIN TYPE"})
        self.expect("variation_unresolved")

    def test_an_unselected_form_is_never_ready(self):
        self.expect("variation_unresolved")

    def test_a_zero_negative_or_non_numeric_variation_id_is_unresolved(self):
        self.select_all()
        for raw in ("0", "", "-1", "abc", " "):
            with self.subTest(raw=raw):
                self.shop.variation_id_text = raw
                self.expect("variation_unresolved")

    def test_a_missing_variation_id_control_is_refused(self):
        self.select_all()
        self.shop.variation_id_controls = 0
        self.expect("variation_id_missing")

    def test_two_variation_id_controls_are_ambiguous(self):
        self.select_all()
        self.shop.variation_id_controls = 2
        self.expect("variation_id_ambiguous")

    def test_a_missing_add_to_cart_button_is_refused(self):
        self.select_all()
        self.shop.add_to_cart_present = False
        self.expect("add_to_cart_missing")

    def test_two_add_to_cart_buttons_are_ambiguous(self):
        self.select_all()
        self.shop.add_to_cart_buttons = 2
        self.expect("add_to_cart_ambiguous")

    def test_a_hidden_add_to_cart_button_is_refused(self):
        self.select_all()
        self.shop.add_to_cart_visible = False
        self.expect("add_to_cart_not_visible")

    def test_a_button_disabled_by_attribute_is_refused(self):
        self.select_all()
        self.shop.add_to_cart_disabled_attr = True
        self.expect("add_to_cart_disabled")

    def test_a_button_disabled_only_by_a_class_token_is_refused(self):
        """WooCommerce disables by class, not by the disabled attribute."""
        for token in ("disabled", "wc-variation-selection-needed"):
            with self.subTest(token=token):
                button = FakeElement(
                    "button", cls=f"single_add_to_cart_button button alt {token}",
                    text="Add to cart")
                self.assertFalse(button.is_disabled(),
                                 "a class token alone is invisible to is_disabled()")
                self.assertEqual(deploy.PublicPage._add_to_cart_blocked(button),
                                 "add_to_cart_disabled")
        clean = FakeElement("button", cls="single_add_to_cart_button button alt")
        self.assertIsNone(deploy.PublicPage._add_to_cart_blocked(clean))

    def test_the_disabled_class_vocabulary_is_the_fixed_pair(self):
        self.assertEqual(deploy.DISABLED_CLASS_TOKENS,
                         frozenset({"disabled", "wc-variation-selection-needed"}))

    def test_readiness_is_read_only(self):
        self.select_all()
        navigations = list(self.shop.navigations)
        self.public.require_variation_ready()
        self.assertEqual(self.shop.added, 0)
        self.assertEqual(self.shop.navigations, navigations)

    def test_a_missing_form_at_readiness_time_is_refused(self):
        self.select_all()
        self.shop.variation_form = False
        self.expect("variations_form_missing")

    def test_every_readiness_code_is_in_the_closed_vocabulary(self):
        for code in ("variation_id_missing", "variation_id_ambiguous", "variation_unresolved",
                     "add_to_cart_missing", "add_to_cart_ambiguous",
                     "add_to_cart_not_visible", "add_to_cart_disabled"):
            with self.subTest(code=code):
                self.assertIn(code, deploy.VALIDATION_CODES)


# ===========================================================================
# Preflight: the read-only rehearsal
# ===========================================================================
class TestPreflight(Harness):
    def setUp(self):
        super().setUp()
        self.install_sessions()

    def evidence_of(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def last_run(self, path):
        return self.evidence_of(path)["runs"][-1]

    def test_passing_preflight_records_the_closed_schema(self):
        evidence = self.evidence_of(self.preflight())
        self.assertEqual(set(evidence), deploy.PREFLIGHT_KEYS | {"sha256"})
        self.assertTrue(evidence["passed"])
        self.assertEqual(evidence["tool"], deploy.TOOL_NAME)
        self.assertEqual(evidence["tool_version"], deploy.TOOL_VERSION)
        self.assertEqual(evidence["kind"], "wordpress_plugin_preflight_validation")
        self.assertEqual(evidence["origin"], deploy.EXACT_ORIGIN)
        self.assertEqual(evidence["product_url"], deploy.PRODUCT_URL)
        self.assertEqual(evidence["variation"],
                         ['SIZE=1/2"', "PRESSURE RATING=150PSI", "RESIN TYPE=D411"])
        self.assertEqual(evidence["max_age_minutes"], 30)
        self.assertEqual(evidence["required_runs"], 3)
        self.assertTrue(evidence["anonymous"])
        for flag in ("persistent_profile", "admin_session_used", "add_to_cart_clicked",
                     "cart_visited", "checkout_visited", "business_write_performed"):
            self.assertFalse(evidence[flag], flag)
        self.assertRegex(evidence["sha256"], r"^[0-9a-f]{64}$")

    def test_evidence_records_exactly_three_ordered_passing_runs(self):
        evidence = self.evidence_of(self.preflight())
        self.assertEqual(len(evidence["runs"]), 3)
        self.assertEqual([run["run"] for run in evidence["runs"]], [1, 2, 3])
        for run in evidence["runs"]:
            self.assertEqual(set(run), deploy.PREFLIGHT_RUN_KEYS)
            self.assertTrue(run["passed"])
            self.assertTrue(run["variation_resolved"])
            self.assertTrue(run["add_to_cart_enabled"])
            for flag in deploy.PREFLIGHT_RUN_FALSE_FLAGS:
                self.assertFalse(run[flag], flag)

    def test_each_run_records_every_fixed_step_in_order_with_timings(self):
        for run in self.evidence_of(self.preflight())["runs"]:
            self.assertEqual([record["step"] for record in run["steps"]],
                             list(deploy.PREFLIGHT_STEPS))
            self.assertIn("variation_ready", [record["step"] for record in run["steps"]])
            for record in run["steps"]:
                self.assertEqual(set(record), {"step", "ok", "ms"})
                self.assertTrue(record["ok"])
                self.assertIsInstance(record["ms"], int)
                self.assertGreaterEqual(record["ms"], 0)

    def test_variation_ready_sits_between_the_last_selection_and_add_to_cart(self):
        self.assertEqual(deploy.PREFLIGHT_STEPS[-1], "variation_ready")
        self.assertEqual(deploy.PREFLIGHT_STEPS[-2], RESIN_STEP)
        self.assertEqual(
            deploy.VALIDATION_STEPS[deploy.VALIDATION_STEPS.index("variation_ready") + 1],
            "add_to_cart")

    def test_each_run_records_the_control_method_per_attribute(self):
        for run in self.evidence_of(self.preflight())["runs"]:
            self.assertEqual(run["selection_method"],
                             {"SIZE": "visible_role_radio",
                              "PRESSURE RATING": "visible_role_radio",
                              "RESIN TYPE": "visible_role_radio"})
        self.shop.role_radios = False
        for run in self.evidence_of(self.preflight())["runs"]:
            self.assertEqual(set(run["selection_method"].values()), {"backing_select"})

    def test_evidence_carries_only_booleans_labels_and_timings(self):
        raw = Path(self.preflight()).read_text(encoding="utf-8")
        for leak in (STOREFRONT_BODY, "Add to cart", "Choose an option", "<", "Checkout",
                     "9182", "variable-item"):
            self.assertNotIn(leak, raw)
        for run in json.loads(raw)["runs"]:
            self.assertIsInstance(run["variation_resolved"], bool)
            self.assertIsInstance(run["add_to_cart_enabled"], bool)
            for record in run["steps"]:
                self.assertIn(record["step"], deploy.PREFLIGHT_STEPS,
                              "a step name must come from the fixed vocabulary")
                self.assertIsInstance(record["ok"], bool)
                self.assertIsInstance(record["ms"], int)
            for method in run["selection_method"].values():
                self.assertIn(method, deploy.SELECTION_METHODS)

    def test_preflight_adds_nothing_to_a_cart_and_visits_no_cart_or_checkout(self):
        self.preflight()
        self.assertEqual(self.shop.added, 0)
        self.assertEqual(set(self.shop.navigations), {deploy.HOME_URL, deploy.PRODUCT_URL})
        self.assertNotIn(deploy.CART_URL, self.shop.navigations)
        self.assertNotIn(deploy.CHECKOUT_URL, self.shop.navigations)

    def test_preflight_opens_no_admin_session_and_clicks_nothing_in_wordpress(self):
        self.preflight()
        self.assertEqual(self.admin_opens, 0)
        self.assertEqual(self.wp.clicks, [])
        self.assertEqual(self.wp.uploads, [])
        self.assertEqual(self.wp.navigations, [])

    def test_preflight_runs_exactly_three_throwaway_contexts_on_the_narrow_allowlist(self):
        self.preflight()
        self.assertEqual(self.anon_opens, 3)
        self.assertEqual(self.anon_allowlists, [deploy.PREFLIGHT_PUBLIC_PATHS] * 3)
        self.assertEqual(self.max_concurrent_sessions, 1,
                         "the three rehearsals must be consecutive, never overlapping")
        self.assertEqual(self.shop.context_starts, 3)

    def test_each_fresh_context_starts_with_nothing_carried_over_and_reselects(self):
        """Three genuinely independent rehearsals, not one plus two free rides."""
        self.preflight()
        self.assertEqual(self.shop.carried_over[0], {},
                         "the first context must start clean")
        for carried in self.shop.carried_over[1:]:
            self.assertEqual(set(carried), {"SIZE", "PRESSURE RATING", "RESIN TYPE"},
                             "the previous context did complete its own selections")
        self.assertEqual(len(self.shop.selected), 9,
                         "each of the three runs makes all three selections itself")
        self.assertEqual(len(self.shop.navigations), 6,
                         "each run loads the homepage and the product page itself")

    def test_the_freshness_clock_starts_after_the_third_rehearsal(self):
        """created_utc is stamped when the LAST run finishes, not the first.

        A 30-minute window measured from the start of a slow three-run rehearsal
        would quietly buy extra minutes; measured from the end it cannot.
        """
        ends: list[datetime] = []
        original = deploy._preflight_single_run

        def watched(index):
            outcome = original(index)
            ends.append(deploy.utc_now())
            return outcome

        with mock.patch.object(deploy, "_preflight_single_run", watched):
            evidence = self.evidence_of(self.preflight())
        self.assertEqual(len(ends), 3)
        created = datetime.fromisoformat(evidence["created_utc"])
        self.assertGreaterEqual(created, ends[-1])
        self.assertEqual(datetime.fromisoformat(evidence["expires_utc"]) - created,
                         timedelta(minutes=30))

    def test_a_second_preflight_invocation_uses_three_more_fresh_contexts(self):
        self.preflight()
        self.preflight()
        self.assertEqual(self.anon_opens, 6)

    def test_a_fatal_homepage_fails_the_preflight_and_records_the_step(self):
        self.shop.home_fatal = True
        path = self.failed_preflight()
        self.assertFalse(self.evidence_of(path)["passed"])
        self.assertEqual(self.last_run(path)["steps"][-1]["step"], "home_load")
        self.assertFalse(self.last_run(path)["steps"][-1]["ok"])

    def test_a_blank_product_page_fails_closed_rather_than_passing_partially(self):
        self.shop.product_blank = True
        path = self.failed_preflight()
        self.assertFalse(self.evidence_of(path)["passed"])
        self.assertEqual(self.last_run(path)["steps"][-1]["step"], "product_load")
        self.assertFalse(self.last_run(path)["steps"][-1]["ok"])

    def test_a_wrong_variation_value_fails_the_preflight(self):
        self.shop.wrong_values = {"PRESSURE RATING": "300PSI"}
        path = self.failed_preflight()
        self.assertFalse(self.evidence_of(path)["passed"])
        self.assertEqual(self.last_run(path)["steps"][-1]["step"], PRESSURE_STEP)

    def test_a_variation_that_never_resolves_fails_at_variation_ready(self):
        """The exact production shape: values change, WooCommerce stays unconvinced."""
        self.shop.variation_resolves = False
        path = self.failed_preflight()
        run = self.last_run(path)
        self.assertFalse(self.evidence_of(path)["passed"])
        self.assertEqual(run["steps"][-1]["step"], "variation_ready")
        self.assertFalse(run["steps"][-1]["ok"])
        self.assertFalse(run["variation_resolved"])
        self.assertFalse(run["add_to_cart_enabled"])

    def test_a_disabled_add_to_cart_fails_at_variation_ready_not_at_the_click(self):
        self.shop.add_to_cart_stays_disabled = True
        path = self.failed_preflight()
        run = self.last_run(path)
        self.assertEqual(run["steps"][-1]["step"], "variation_ready")
        self.assertFalse(run["add_to_cart_enabled"])
        self.assertEqual(self.shop.added, 0)

    def test_the_first_failure_stops_the_remaining_rehearsals(self):
        self.shop.variation_resolves = False
        evidence = self.evidence_of(self.failed_preflight())
        self.assertEqual(len(evidence["runs"]), 1)
        self.assertEqual(self.anon_opens, 1,
                         "no further browser is opened once a rehearsal has failed")
        self.assertFalse(evidence["passed"])

    def test_a_timeout_at_any_preflight_step_names_that_step(self):
        for step in deploy.PREFLIGHT_STEPS:
            with self.subTest(step=step):
                self.setUp()  # a fresh temp tree, storefront and session per step
                self.shop.timeout_at = TIMEOUT_MARKERS[step]
                path = self.failed_preflight()
                self.assertFalse(self.evidence_of(path)["passed"])
                self.assertEqual(self.last_run(path)["steps"][-1]["step"], step)
                self.assertFalse(self.last_run(path)["steps"][-1]["ok"])

    def test_readiness_timing_is_attributed_to_the_variation_ready_step(self):
        self.shop.timeout_at = TIMEOUT_MARKERS["variation_ready"]
        run = self.last_run(self.failed_preflight())
        self.assertEqual([record["step"] for record in run["steps"]],
                         list(deploy.PREFLIGHT_STEPS))
        self.assertTrue(all(record["ok"] for record in run["steps"][:-1]))
        self.assertFalse(run["steps"][-1]["ok"])
        self.assertGreaterEqual(run["steps"][-1]["ms"], 0)

    def test_the_preflight_never_clicks_add_to_cart_even_when_it_is_enabled(self):
        """Proving the button is ready is the whole job; pressing it is not."""
        for run in self.evidence_of(self.preflight())["runs"]:
            self.assertTrue(run["add_to_cart_enabled"])
            self.assertFalse(run["add_to_cart_clicked"])
        self.assertEqual(self.shop.added, 0)
        self.assertNotIn(deploy.CART_URL, self.shop.navigations)
        self.assertNotIn(deploy.CHECKOUT_URL, self.shop.navigations)

    def test_a_failed_preflight_can_never_be_staged_on(self):
        self.shop.options_present = False
        evidence_path = self.failed_preflight()
        self.shop.options_present = True
        self.wp.version = GOOD_VERSION
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.stage("plugin_activate", preflight=evidence_path)
        self.assertIn("did not pass", str(caught.exception))


# ===========================================================================
# Preflight evidence integrity, identity and freshness
# ===========================================================================
class TestPreflightEvidence(Harness):
    def setUp(self):
        super().setUp()
        self.install_sessions()
        self.evidence_path = self.preflight()
        self.original = self.evidence_path.read_text(encoding="utf-8")

    def restore(self):
        self.evidence_path.write_text(self.original, encoding="utf-8")

    def runs(self):
        return json.loads(self.original)["runs"]

    def test_fresh_evidence_loads(self):
        evidence = deploy.load_preflight(self.evidence_path)
        self.assertTrue(evidence["passed"])
        self.assertEqual(len(evidence["runs"]), 3)
        self.assertRegex(evidence["sha256"], r"^[0-9a-f]{64}$")

    def test_tampering_without_rehashing_is_refused(self):
        evidence = json.loads(self.original)
        evidence["runs"][0]["selection_method"]["SIZE"] = "backing_select"
        self.evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_preflight(self.evidence_path)
        self.assertIn("hash check failed", str(caught.exception))

    def test_rehashed_identity_forgery_is_refused(self):
        for field, value in (("origin", "https://example.test"),
                             ("product_url", f"{deploy.EXACT_ORIGIN}/product/other/"),
                             ("tool", "Some Other Tool"),
                             ("tool_version", "0.0.1"),
                             ("tool_version", "1.1.0"),
                             ("kind", "something_else"),
                             ("schema_version", 1),
                             ("schema_version", 99),
                             ("max_age_minutes", 240),
                             ("required_runs", 1)):
            with self.subTest(field=field, value=value):
                self.restore()
                self.rehash_evidence(self.evidence_path, **{field: value})
                with self.assertRaises(deploy.DeploymentError):
                    deploy.load_preflight(self.evidence_path)

    def test_evidence_from_the_previous_build_is_refused_even_when_rehashed(self):
        """A 1.1.0 rehearsal recorded ONE run under the old flat schema.

        Reconstructing it and rehashing must not get it past the gate: identity and
        shape are both checked, so an old proof of a one-pass hidden-select
        rehearsal can never authorise an activation under this build.
        """
        legacy = json.loads(self.original)
        legacy.pop("sha256")
        run = legacy["runs"][0]
        legacy.pop("runs")
        legacy.pop("required_runs")
        legacy["steps"] = [record for record in run["steps"]
                           if record["step"] != "variation_ready"]
        legacy["selection_method"] = {label: "visible_radio"
                                      for label in run["selection_method"]}
        legacy["schema_version"] = 1
        legacy["tool_version"] = "1.1.0"
        legacy["sha256"] = deploy.digest_for(legacy)
        self.evidence_path.write_text(json.dumps(legacy, indent=2), encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_preflight(self.evidence_path)
        self.assertIn("closed set of fields", str(caught.exception))

    def test_fewer_or_more_than_three_runs_is_refused(self):
        runs = self.runs()
        extra = {**runs[-1], "run": 4}
        for forged in (runs[:1], runs[:2], runs + [extra], []):
            with self.subTest(count=len(forged)):
                self.restore()
                self.rehash_evidence(self.evidence_path, runs=forged)
                with self.assertRaises(deploy.DeploymentError) as caught:
                    deploy.load_preflight(self.evidence_path)
                self.assertIn("exactly 3", str(caught.exception))

    def test_reordered_runs_are_refused(self):
        runs = self.runs()
        self.rehash_evidence(self.evidence_path, runs=[runs[0], runs[2], runs[1]])
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_preflight(self.evidence_path)
        self.assertIn("ordered sequence", str(caught.exception))

    def test_any_single_failed_run_makes_the_whole_evidence_unusable(self):
        for index in range(3):
            with self.subTest(run=index + 1):
                self.restore()
                runs = self.runs()
                runs[index] = {**runs[index], "passed": False}
                self.rehash_evidence(self.evidence_path, runs=runs)
                with self.assertRaises(deploy.DeploymentError) as caught:
                    deploy.load_preflight(self.evidence_path)
                self.assertIn(f"run {index + 1} did not pass", str(caught.exception))

    def test_tampered_readiness_fields_are_refused(self):
        for field in ("variation_resolved", "add_to_cart_enabled"):
            with self.subTest(field=field):
                self.restore()
                runs = self.runs()
                runs[1] = {**runs[1], field: False}
                self.rehash_evidence(self.evidence_path, runs=runs)
                with self.assertRaises(deploy.DeploymentError):
                    deploy.load_preflight(self.evidence_path)

    def test_a_run_claiming_a_cart_checkout_or_admin_touch_is_refused(self):
        for flag in deploy.PREFLIGHT_RUN_FALSE_FLAGS:
            with self.subTest(flag=flag):
                self.restore()
                runs = self.runs()
                runs[2] = {**runs[2], flag: True}
                self.rehash_evidence(self.evidence_path, runs=runs)
                with self.assertRaises(deploy.DeploymentError) as caught:
                    deploy.load_preflight(self.evidence_path)
                self.assertIn(flag, str(caught.exception))

    def test_an_open_run_record_is_refused(self):
        runs = self.runs()
        runs[0] = {**runs[0], "notes": "looked fine to me"}
        self.rehash_evidence(self.evidence_path, runs=runs)
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_preflight(self.evidence_path)
        self.assertIn("closed projection", str(caught.exception))

    def test_a_run_missing_the_variation_ready_step_is_refused(self):
        """Three passes of the OLD sequence are still not the new proof."""
        runs = self.runs()
        runs[0] = {**runs[0], "steps": [record for record in runs[0]["steps"]
                                        if record["step"] != "variation_ready"]}
        self.rehash_evidence(self.evidence_path, runs=runs)
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_preflight(self.evidence_path)
        self.assertIn("fixed step list", str(caught.exception))

    def test_rehashed_variation_forgery_is_refused(self):
        self.rehash_evidence(self.evidence_path,
                             variation=['SIZE=3/4"', "PRESSURE RATING=150PSI",
                                        "RESIN TYPE=D411"])
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_preflight(self.evidence_path)
        self.assertIn("fixed required variation", str(caught.exception))

    def test_a_forged_pass_on_a_failed_step_is_refused(self):
        runs = self.runs()
        steps = list(runs[2]["steps"])
        steps[-1] = {**steps[-1], "ok": False}
        runs[2] = {**runs[2], "steps": steps}
        self.rehash_evidence(self.evidence_path, runs=runs)
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_preflight(self.evidence_path)
        self.assertIn("every fixed step passing", str(caught.exception))

    def test_a_shortened_or_reordered_step_list_is_refused(self):
        steps = self.runs()[0]["steps"]
        for forged in (steps[:-1], list(reversed(steps))):
            with self.subTest(count=len(forged)):
                self.restore()
                runs = self.runs()
                runs[0] = {**runs[0], "steps": forged}
                self.rehash_evidence(self.evidence_path, runs=runs)
                with self.assertRaises(deploy.DeploymentError):
                    deploy.load_preflight(self.evidence_path)

    def test_passed_false_is_refused(self):
        self.rehash_evidence(self.evidence_path, passed=False)
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_preflight(self.evidence_path)
        self.assertIn("did not pass", str(caught.exception))

    def test_a_claimed_cart_checkout_or_admin_touch_is_refused(self):
        for flag in ("add_to_cart_clicked", "cart_visited", "checkout_visited",
                     "admin_session_used", "persistent_profile",
                     "business_write_performed"):
            with self.subTest(flag=flag):
                self.restore()
                self.rehash_evidence(self.evidence_path, **{flag: True})
                with self.assertRaises(deploy.DeploymentError) as caught:
                    deploy.load_preflight(self.evidence_path)
                self.assertIn(flag, str(caught.exception))

    def test_an_unknown_selection_method_is_refused(self):
        """Including the retired 1.1.0 name, which no longer means anything."""
        for methods in (
            {"SIZE": "javascript", "PRESSURE RATING": "visible_role_radio",
             "RESIN TYPE": "visible_role_radio"},
            {"SIZE": "visible_radio", "PRESSURE RATING": "visible_role_radio",
             "RESIN TYPE": "visible_role_radio"},
        ):
            with self.subTest(method=methods["SIZE"]):
                self.restore()
                runs = self.runs()
                runs[0] = {**runs[0], "selection_method": methods}
                self.rehash_evidence(self.evidence_path, runs=runs)
                with self.assertRaises(deploy.DeploymentError) as caught:
                    deploy.load_preflight(self.evidence_path)
                self.assertIn("unknown selection method", str(caught.exception))

    def test_a_missing_attribute_method_is_refused(self):
        runs = self.runs()
        runs[0] = {**runs[0], "selection_method": {"SIZE": "visible_role_radio"}}
        self.rehash_evidence(self.evidence_path, runs=runs)
        with self.assertRaises(deploy.DeploymentError):
            deploy.load_preflight(self.evidence_path)

    def test_an_unknown_or_missing_field_is_refused(self):
        self.rehash_evidence(self.evidence_path, extra_capability="checkout")
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_preflight(self.evidence_path)
        self.assertIn("closed set of fields", str(caught.exception))
        self.restore()
        evidence = json.loads(self.original)
        evidence.pop("sha256")
        evidence.pop("passed")
        evidence["sha256"] = deploy.digest_for(evidence)
        self.evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError):
            deploy.load_preflight(self.evidence_path)

    def test_evidence_older_than_thirty_minutes_is_refused(self):
        stale = deploy.utc_now() - timedelta(minutes=31)
        self.rehash_evidence(
            self.evidence_path,
            created_utc=stale.isoformat(),
            expires_utc=(stale + timedelta(minutes=30)).isoformat())
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_preflight(self.evidence_path)
        self.assertIn("older than 30 minutes", str(caught.exception))

    def test_evidence_just_inside_thirty_minutes_still_loads(self):
        fresh = deploy.utc_now() - timedelta(minutes=29)
        self.rehash_evidence(
            self.evidence_path,
            created_utc=fresh.isoformat(),
            expires_utc=(fresh + timedelta(minutes=30)).isoformat())
        self.assertTrue(deploy.load_preflight(self.evidence_path)["passed"])

    def test_future_dated_evidence_is_refused(self):
        ahead = deploy.utc_now() + timedelta(minutes=10)
        self.rehash_evidence(
            self.evidence_path,
            created_utc=ahead.isoformat(),
            expires_utc=(ahead + timedelta(minutes=30)).isoformat())
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_preflight(self.evidence_path)
        self.assertIn("future", str(caught.exception))

    def test_an_expiry_that_does_not_match_its_own_creation_is_refused(self):
        created = json.loads(self.original)["created_utc"]
        self.rehash_evidence(
            self.evidence_path,
            expires_utc=(datetime.fromisoformat(created) + timedelta(hours=12)).isoformat())
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_preflight(self.evidence_path)
        self.assertIn("expiry does not match", str(caught.exception))

    def test_evidence_outside_the_preflight_folder_is_refused(self):
        stray = self.tmp / "stray.json"
        stray.write_text(self.original, encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_preflight(stray)
        self.assertIn("preflight folder", str(caught.exception))

    def test_traversal_out_of_the_preflight_folder_is_refused(self):
        with self.assertRaises(deploy.DeploymentError):
            deploy.resolve_preflight_path(str(self.preflight_dir / ".." / "stray.json"))


# ===========================================================================
# Staging performs no writes
# ===========================================================================
class TestStaging(Harness):
    def setUp(self):
        super().setUp()
        self.install_sessions()

    def assertNoSiteChange(self):
        self.assertEqual(self.wp.clicks, [])
        self.assertEqual(self.wp.uploads, [])
        self.assertEqual(self.shop.added, 0)
        self.assertNotIn(deploy.CART_URL, self.shop.navigations)
        self.assertNotIn(deploy.CHECKOUT_URL, self.shop.navigations)

    def test_stage_replace_writes_nothing_to_the_site(self):
        self.stage("plugin_replace")
        self.assertNoSiteChange()
        self.assertEqual(self.anon_opens, 0, "a replace never touches the storefront")
        self.assertEqual(self.wp.version, OLD_VERSION)
        self.assertFalse(self.wp.active)

    def test_stage_replace_plan_is_closed_and_hashed(self):
        plan = json.loads(self.stage("plugin_replace").read_text(encoding="utf-8"))
        self.assertEqual(set(plan), deploy.PLAN_KEYS | {"sha256"})
        self.assertEqual(plan["action"], "plugin_replace")
        self.assertEqual(plan["artifact"]["version"], GOOD_VERSION)
        self.assertEqual(plan["artifact"]["sha256"], deploy.ARTIFACT_SHA256)
        self.assertEqual(plan["after_expected"]["version"], GOOD_VERSION)
        self.assertFalse(plan["after_expected"]["active"])
        self.assertIsNone(plan["validation"])
        self.assertIsNone(plan["preflight"])
        self.assertEqual(datetime.fromisoformat(plan["expires_utc"])
                         - datetime.fromisoformat(plan["created_utc"]), timedelta(hours=24))

    def test_stage_replace_refuses_when_already_on_1_0_1(self):
        self.wp.version = GOOD_VERSION
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.stage("plugin_replace")
        self.assertIn("No change is needed", str(caught.exception))
        self.assertNoSiteChange()

    def test_stage_replace_refuses_an_active_1_0_0(self):
        self.wp.active = True
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.stage("plugin_replace")
        self.assertIn("active", str(caught.exception))
        self.assertNoSiteChange()

    def test_stage_replace_refuses_an_unexpected_installed_version(self):
        self.wp.version = "0.9.7"
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.stage("plugin_replace")
        self.assertIn("not the expected", str(caught.exception))

    def test_stage_replace_refuses_a_missing_row(self):
        self.wp.present = False
        with self.assertRaises(deploy.DeploymentError):
            self.stage("plugin_replace")

    def test_stage_replace_refuses_an_ambiguous_row(self):
        self.wp.duplicate_row = True
        with self.assertRaises(deploy.DeploymentError):
            self.stage("plugin_replace")

    def test_stage_activate_refuses_the_withdrawn_version(self):
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.stage("plugin_activate")
        self.assertIn("never be activated", str(caught.exception))
        self.assertNoSiteChange()

    def test_stage_activate_carries_the_validation_and_rollback_contract(self):
        self.wp.version = GOOD_VERSION
        plan = json.loads(self.stage("plugin_activate").read_text(encoding="utf-8"))
        self.assertEqual(plan["validation"], deploy.VALIDATION_CONTRACT)
        self.assertEqual(plan["validation"]["required_exact_message_count"], 1)
        self.assertEqual(plan["validation"]["exact_message"], deploy.EXACT_MESSAGE)
        self.assertTrue(plan["validation"]["require_checkout_blocked"])
        self.assertTrue(plan["validation"]["require_no_payment_form"])
        self.assertTrue(plan["validation"]["anonymous"])
        self.assertFalse(plan["validation"]["persistent_profile"])
        self.assertFalse(plan["validation"]["order_placed"])
        self.assertFalse(plan["validation"]["ups_setting_touched"])
        self.assertTrue(plan["validation"]["preflight_required"])
        self.assertEqual(plan["validation"]["preflight_max_age_minutes"], 30)
        self.assertEqual(plan["validation"]["preflight_runs_required"], 3)
        self.assertEqual(plan["validation"]["preflight_schema_version"],
                         deploy.PREFLIGHT_SCHEMA_VERSION)
        self.assertTrue(plan["validation"]["variation_ready_required"])
        self.assertIn("role-radio", plan["validation"]["customer_control"])
        self.assertTrue(plan["validation"]["timeout_is_failure"])
        self.assertEqual(plan["validation"]["steps"], list(deploy.VALIDATION_STEPS))
        self.assertIn("variation_ready", plan["validation"]["steps"])
        self.assertIn("emergency deactivate", plan["validation"]["on_any_failure"])
        self.assertNoSiteChange()

    def test_stage_activate_embeds_the_preflight_hash_timestamp_and_run_count(self):
        self.wp.version = GOOD_VERSION
        evidence_path = self.preflight()
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        plan = json.loads(
            self.stage("plugin_activate", preflight=evidence_path).read_text(encoding="utf-8"))
        self.assertEqual(set(plan["preflight"]), {"path", "sha256", "created_utc", "runs"})
        self.assertEqual(plan["preflight"]["sha256"], evidence["sha256"])
        self.assertEqual(plan["preflight"]["created_utc"], evidence["created_utc"])
        self.assertEqual(plan["preflight"]["runs"], 3)
        self.assertEqual(Path(plan["preflight"]["path"]), evidence_path.resolve())

    def test_stage_activate_refuses_stale_preflight_evidence(self):
        self.wp.version = GOOD_VERSION
        evidence_path = self.preflight()
        stale = deploy.utc_now() - timedelta(minutes=45)
        self.rehash_evidence(evidence_path, created_utc=stale.isoformat(),
                             expires_utc=(stale + timedelta(minutes=30)).isoformat())
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.stage("plugin_activate", preflight=evidence_path)
        self.assertIn("older than 30 minutes", str(caught.exception))
        self.assertEqual(list(self.plan_dir.glob("*.json")), [])

    def test_stage_activate_refuses_tampered_preflight_evidence(self):
        self.wp.version = GOOD_VERSION
        evidence_path = self.preflight()
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["passed"] = True
        evidence["product_url"] = f"{deploy.EXACT_ORIGIN}/product/other/"
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError):
            self.stage("plugin_activate", preflight=evidence_path)
        self.assertEqual(list(self.plan_dir.glob("*.json")), [])

    def test_stage_activate_refuses_evidence_from_outside_the_folder(self):
        self.wp.version = GOOD_VERSION
        evidence_path = self.preflight()
        stray = self.tmp / "stray.json"
        stray.write_text(evidence_path.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError):
            self.stage("plugin_activate", preflight=stray)

    def test_stage_activate_checks_the_preflight_before_opening_the_admin_window(self):
        self.wp.version = GOOD_VERSION
        evidence_path = self.preflight()
        opens = self.admin_opens
        stale = deploy.utc_now() - timedelta(hours=2)
        self.rehash_evidence(evidence_path, created_utc=stale.isoformat(),
                             expires_utc=(stale + timedelta(minutes=30)).isoformat())
        with self.assertRaises(deploy.DeploymentError):
            self.stage("plugin_activate", preflight=evidence_path)
        self.assertEqual(self.admin_opens, opens,
                         "a stale rehearsal must refuse without opening WordPress")

    def test_stage_activate_refuses_when_already_active(self):
        self.wp.version = GOOD_VERSION
        self.wp.active = True
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.stage("plugin_activate")
        self.assertIn("already active", str(caught.exception))

    def test_stage_deactivate_requires_an_active_1_0_1(self):
        self.wp.version = GOOD_VERSION
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.stage("plugin_deactivate")
        self.assertIn("already inactive", str(caught.exception))
        self.wp.active = True
        plan = json.loads(self.stage("plugin_deactivate").read_text(encoding="utf-8"))
        self.assertFalse(plan["after_expected"]["active"])
        self.assertTrue(plan["after_expected"]["present"])
        self.assertIsNone(plan["preflight"])
        self.assertNoSiteChange()

    def test_stage_deactivate_refuses_a_foreign_version(self):
        self.wp.version = OLD_VERSION
        self.wp.active = True
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.stage("plugin_deactivate")
        self.assertIn("only manages version", str(caught.exception))


# ===========================================================================
# Plan integrity
# ===========================================================================
class TestPlanIntegrity(Harness):
    def setUp(self):
        super().setUp()
        self.install_sessions()
        self.wp.version = GOOD_VERSION
        self.plan_path = self.stage_activation()
        self.original = self.plan_path.read_text(encoding="utf-8")

    def restore(self):
        self.plan_path.write_text(self.original, encoding="utf-8")

    def test_untouched_plan_loads(self):
        self.assertEqual(deploy.load_plan(str(self.plan_path))["action"], "plugin_activate")

    def test_full_64_character_hash_is_stored(self):
        plan = json.loads(self.original)
        self.assertRegex(plan["sha256"], r"^[0-9a-f]{64}$")

    def test_tampering_without_rehashing_is_refused(self):
        plan = json.loads(self.original)
        plan["before"]["version"] = "9.9.9"
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan_path))
        self.assertIn("hash check failed", str(caught.exception))

    def test_rehashed_tampering_is_still_refused(self):
        """A forger who recomputes the digest still cannot smuggle a change past."""
        self.rehash(self.plan_path, plugin_file="other-plugin/other-plugin.php")
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan_path))
        self.assertIn("fixed FRP Depot plugin", str(caught.exception))

    def test_rehashed_slug_or_name_forgery_is_refused(self):
        for field, value in (("plugin_slug", "other-plugin"), ("plugin_name", "Other")):
            with self.subTest(field=field):
                self.restore()
                self.rehash(self.plan_path, **{field: value})
                with self.assertRaises(deploy.DeploymentError):
                    deploy.load_plan(str(self.plan_path))

    def test_rehashed_fingerprint_forgery_is_refused(self):
        before = dict(json.loads(self.original)["before"])
        before["version"] = OLD_VERSION  # the stored fingerprint no longer covers this
        self.rehash(self.plan_path, before=before)
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan_path))
        self.assertIn("fingerprint", str(caught.exception))

    def test_rehashed_after_state_forgery_is_refused(self):
        self.rehash(self.plan_path,
                    after_expected=deploy.project_row(True, True, "2.0.0", False))
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan_path))
        self.assertIn("expected end state", str(caught.exception))

    def test_unknown_field_is_refused(self):
        self.rehash(self.plan_path, extra_capability="delete")
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan_path))
        self.assertIn("closed set of fields", str(caught.exception))

    def test_missing_field_is_refused(self):
        plan = json.loads(self.original)
        plan.pop("sha256")
        plan.pop("nonce")
        plan["sha256"] = deploy.digest_for(plan)
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError):
            deploy.load_plan(str(self.plan_path))

    def test_an_old_plan_without_the_preflight_contract_is_refused(self):
        """Plans staged before this repair can never be committed."""
        plan = json.loads(self.original)
        plan.pop("sha256")
        plan.pop("preflight")
        plan["sha256"] = deploy.digest_for(plan)
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan_path))
        self.assertIn("closed set of fields", str(caught.exception))

    def test_an_old_schema_version_is_refused(self):
        for version in (1, 2):
            with self.subTest(schema_version=version):
                self.restore()
                self.rehash(self.plan_path, schema_version=version)
                with self.assertRaises(deploy.DeploymentError):
                    deploy.load_plan(str(self.plan_path))

    def test_a_plan_carrying_the_previous_validation_contract_is_refused(self):
        """Every activation plan staged before this repair is semantically dead.

        The three closed activation plans from 2026-08-09 named a contract with no
        `variation_ready` step and no three-run requirement. Rehashing one cannot
        revive it: the contract is compared field for field.
        """
        legacy = dict(deploy.VALIDATION_CONTRACT)
        legacy.pop("preflight_runs_required")
        legacy.pop("variation_ready_required")
        legacy.pop("preflight_schema_version")
        legacy.pop("customer_control")
        legacy["steps"] = [step for step in deploy.VALIDATION_STEPS
                           if step != "variation_ready"]
        self.rehash(self.plan_path, validation=legacy)
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan_path))
        self.assertIn("current fixed contract", str(caught.exception))

    def test_a_plan_built_on_fewer_than_three_rehearsals_is_refused(self):
        for count in (1, 2, 4):
            with self.subTest(runs=count):
                self.restore()
                preflight = dict(json.loads(self.original)["preflight"])
                preflight["runs"] = count
                self.rehash(self.plan_path, preflight=preflight)
                with self.assertRaises(deploy.DeploymentError) as caught:
                    deploy.load_plan(str(self.plan_path))
                self.assertIn("3 passing rehearsals", str(caught.exception))

    def test_an_activation_plan_with_a_null_preflight_is_refused(self):
        self.rehash(self.plan_path, preflight=None)
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan_path))
        self.assertIn("closed preflight evidence record", str(caught.exception))

    def test_an_activation_plan_with_an_open_preflight_record_is_refused(self):
        preflight = dict(json.loads(self.original)["preflight"])
        preflight["skip_checks"] = True
        self.rehash(self.plan_path, preflight=preflight)
        with self.assertRaises(deploy.DeploymentError):
            deploy.load_plan(str(self.plan_path))

    def test_a_preflight_record_pointing_outside_the_folder_is_refused(self):
        preflight = dict(json.loads(self.original)["preflight"])
        preflight["path"] = str(self.tmp / "stray.json")
        self.rehash(self.plan_path, preflight=preflight)
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan_path))
        self.assertIn("preflight folder", str(caught.exception))

    def test_a_preflight_record_with_a_non_digest_hash_is_refused(self):
        preflight = dict(json.loads(self.original)["preflight"])
        preflight["sha256"] = "not-a-digest"
        self.rehash(self.plan_path, preflight=preflight)
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan_path))
        self.assertIn("SHA-256 digest", str(caught.exception))

    def test_a_non_activation_plan_may_not_carry_preflight_evidence(self):
        self.wp.version = OLD_VERSION
        self.wp.active = False
        replace_plan = self.stage("plugin_replace")
        preflight = json.loads(self.original)["preflight"]
        self.rehash(replace_plan, preflight=preflight)
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(replace_plan))
        self.assertIn("Only an activation plan", str(caught.exception))

    def test_expired_plan_is_refused(self):
        self.rehash(self.plan_path,
                    expires_utc=(deploy.utc_now() - timedelta(minutes=1)).isoformat())
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan_path))
        self.assertIn("expired", str(caught.exception))

    def test_unparseable_expiry_is_refused(self):
        self.rehash(self.plan_path, expires_utc="whenever")
        with self.assertRaises(deploy.DeploymentError):
            deploy.load_plan(str(self.plan_path))

    def test_foreign_origin_tool_or_schema_is_refused(self):
        for field, value in (("origin", "https://example.test"),
                             ("tool", "Some Other Tool"), ("schema_version", 99),
                             ("action", "plugin_delete")):
            with self.subTest(field=field):
                self.restore()
                self.rehash(self.plan_path, **{field: value})
                with self.assertRaises(deploy.DeploymentError):
                    deploy.load_plan(str(self.plan_path))

    def test_activation_plan_cannot_carry_an_artifact(self):
        self.rehash(self.plan_path, artifact=deploy.verify_artifact())
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan_path))
        self.assertIn("Only a replace plan", str(caught.exception))

    def test_replace_plan_naming_the_withdrawn_artifact_is_refused(self):
        self.wp.version = OLD_VERSION
        replace_plan = self.stage("plugin_replace")
        artifact = json.loads(replace_plan.read_text(encoding="utf-8"))["artifact"]
        for field, value in (("sha256", OLD_SHA256), ("version", OLD_VERSION)):
            with self.subTest(field=field):
                forged = dict(artifact)
                forged[field] = value
                self.rehash(replace_plan, artifact=forged)
                with self.assertRaises(deploy.DeploymentError) as caught:
                    deploy.load_plan(str(replace_plan))
                self.assertIn("withdrawn 1.0.0", str(caught.exception))

    def test_replace_plan_naming_a_foreign_zip_is_refused(self):
        self.wp.version = OLD_VERSION
        replace_plan = self.stage("plugin_replace")
        artifact = json.loads(replace_plan.read_text(encoding="utf-8"))["artifact"]
        artifact["path"] = str(self.tmp / "evil.zip")
        self.rehash(replace_plan, artifact=artifact)
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(replace_plan))
        self.assertIn("not the approved 1.0.1 artifact", str(caught.exception))

    def test_plan_expecting_the_withdrawn_version_is_refused(self):
        self.rehash(self.plan_path,
                    after_expected=deploy.project_row(True, True, OLD_VERSION, False))
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.load_plan(str(self.plan_path))
        self.assertIn("withdrawn 1.0.0", str(caught.exception))

    def test_weakened_validation_contract_is_refused(self):
        for field, value in (("required_exact_message_count", 0),
                             ("preflight_required", False),
                             ("preflight_max_age_minutes", 1440),
                             ("preflight_runs_required", 1),
                             ("variation_ready_required", False),
                             ("timeout_is_failure", False),
                             ("steps", ["home_load"])):
            with self.subTest(field=field):
                self.restore()
                weakened = dict(deploy.VALIDATION_CONTRACT)
                weakened[field] = value
                self.rehash(self.plan_path, validation=weakened)
                with self.assertRaises(deploy.DeploymentError) as caught:
                    deploy.load_plan(str(self.plan_path))
                self.assertIn("validation and rollback contract", str(caught.exception))

    def test_plan_outside_the_plan_folder_is_refused(self):
        stray = self.tmp / "stray.json"
        stray.write_text(self.original, encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError) as caught:
            deploy.resolve_plan_path(str(stray))
        self.assertIn("inside Dado's WordPress plugin-plan folder", str(caught.exception))

    def test_traversal_out_of_the_plan_folder_is_refused(self):
        with self.assertRaises(deploy.DeploymentError):
            deploy.resolve_plan_path(str(self.plan_dir / ".." / "stray.json"))


# ===========================================================================
# Approval gate -- before any browser access
# ===========================================================================
class TestApprovalGate(Harness):
    def setUp(self):
        super().setUp()
        self.install_sessions()
        self.plans = {"plugin_replace": self.stage("plugin_replace")}
        self.wp.version = GOOD_VERSION
        self.plans["plugin_activate"] = self.stage_activation()
        self.wp.active = True
        self.plans["plugin_deactivate"] = self.stage("plugin_deactivate")

    def test_word_is_exact(self):
        deploy.require_rachad_approval("APPROVED")
        for wrong in ("", "approved", " Approved ", " APPROVED ", "APPROVE", "APPROVED!",
                      "yes", "ok", "APPROVED APPROVED", "not APPROVED", "APPROVEDD",
                      "A P P R O V E D"):
            with self.subTest(wrong=wrong), self.assertRaises(deploy.DeploymentError):
                deploy.require_rachad_approval(wrong)

    def test_every_commit_refuses_before_opening_a_browser(self):
        self.forbid_sessions()
        for action, plan_path in self.plans.items():
            for wrong in ("", "yes please", "approve", "APPROVED."):
                with self.subTest(action=action, approval=wrong):
                    with self.assertRaises(deploy.DeploymentError) as caught:
                        self.commit(action, plan_path, approval=wrong)
                    self.assertIn("one-word approval", str(caught.exception))
                    self.assertFalse(deploy.lock_path(plan_path).exists(),
                                     "a refused approval must not lock the plan")
                    self.assertFalse(deploy.result_path(plan_path).exists())

    def test_a_plan_cannot_be_committed_by_the_wrong_action(self):
        self.forbid_sessions()
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.commit("plugin_deactivate", self.plans["plugin_activate"])
        self.assertIn("not plugin_deactivate", str(caught.exception))

    def test_an_expired_plan_refuses_before_the_browser_too(self):
        self.forbid_sessions()
        self.rehash(self.plans["plugin_activate"],
                    expires_utc=(deploy.utc_now() - timedelta(seconds=1)).isoformat())
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.commit("plugin_activate", self.plans["plugin_activate"])
        self.assertIn("expired", str(caught.exception))


# ===========================================================================
# commit-replace
# ===========================================================================
class TestCommitReplace(Harness):
    def setUp(self):
        super().setUp()
        self.install_sessions()
        self.plan_path = self.stage("plugin_replace")
        self.wp.lock_probe = lambda: deploy.lock_path(self.plan_path).exists()

    def test_happy_path_replaces_and_leaves_it_inactive(self):
        self.commit("plugin_replace", self.plan_path)
        self.assertEqual(self.wp.version, GOOD_VERSION)
        self.assertFalse(self.wp.active)
        self.assertIn("overwrite", self.wp.clicks)
        self.assertNotIn("activate", self.wp.clicks)
        self.assertNotIn("delete", self.wp.clicks)
        result = self.result_of(self.plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertFalse(result["activated"])
        self.assertEqual(result["after"]["version"], GOOD_VERSION)
        self.assertFalse(result["after"]["active"])
        self.assertEqual(self.lock_of(self.plan_path)["status"], "committed_verified")

    def test_lock_exists_before_the_file_is_ever_handed_over(self):
        self.commit("plugin_replace", self.plan_path)
        self.assertEqual(self.wp.observed_at_upload, [True],
                         "the replay lock must exist before the upload")

    def test_only_the_fixed_artifact_is_uploaded_exactly_once(self):
        self.commit("plugin_replace", self.plan_path)
        self.assertEqual(len(self.wp.uploads), 1)
        self.assertEqual(Path(self.wp.uploads[0]).resolve(),
                         Path(deploy.ARTIFACT_PATH).resolve())

    def test_the_anonymous_browser_is_never_opened_for_a_replace(self):
        self.commit("plugin_replace", self.plan_path)
        self.assertEqual(self.anon_opens, 0)

    def test_a_second_commit_is_refused_as_replay(self):
        self.commit("plugin_replace", self.plan_path)
        clicks = list(self.wp.clicks)
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.commit("plugin_replace", self.plan_path)
        self.assertIn("already entered commit", str(caught.exception))
        self.assertEqual(self.wp.clicks, clicks, "a replay must not click anything")

    def test_a_stale_row_refuses_without_burning_the_plan(self):
        self.wp.version = "0.9.9"
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.commit("plugin_replace", self.plan_path)
        self.assertIn("changed after Rachad reviewed", str(caught.exception))
        self.assertEqual(self.wp.uploads, [])
        self.assertFalse(deploy.lock_path(self.plan_path).exists(),
                         "a read-only refusal must leave the plan re-committable")

    def test_a_row_that_became_active_after_review_is_refused(self):
        self.wp.active = True
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_replace", self.plan_path)
        self.assertEqual(self.wp.uploads, [])
        self.assertEqual(self.wp.clicks, [])

    def test_a_missing_row_after_review_is_refused(self):
        self.wp.present = False
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_replace", self.plan_path)
        self.assertEqual(self.wp.uploads, [])

    def test_an_ambiguous_row_after_review_is_refused(self):
        self.wp.duplicate_row = True
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_replace", self.plan_path)
        self.assertEqual(self.wp.uploads, [])

    def test_a_comparison_naming_another_plugin_is_not_clicked(self):
        self.wp.comparison_name = "Some Other Plugin"
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_replace", self.plan_path)
        self.assertNotIn("overwrite", self.wp.clicks)
        self.assertEqual(self.wp.version, OLD_VERSION)

    def test_a_comparison_offering_the_withdrawn_version_is_not_clicked(self):
        self.wp.comparison_version = OLD_VERSION
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_replace", self.plan_path)
        self.assertNotIn("overwrite", self.wp.clicks)

    def test_a_comparison_offering_an_unexpected_version_is_not_clicked(self):
        self.wp.comparison_version = "1.0.2"
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_replace", self.plan_path)
        self.assertNotIn("overwrite", self.wp.clicks)

    def test_a_missing_comparison_is_not_clicked(self):
        self.wp.comparison_tables = 0
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_replace", self.plan_path)
        self.assertNotIn("overwrite", self.wp.clicks)

    def test_an_ambiguous_comparison_is_not_clicked(self):
        self.wp.comparison_tables = 2
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_replace", self.plan_path)
        self.assertNotIn("overwrite", self.wp.clicks)

    def test_an_ambiguous_replace_control_is_not_clicked(self):
        self.wp.overwrite_links = 2
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_replace", self.plan_path)
        self.assertNotIn("overwrite", self.wp.clicks)

    def test_a_missing_replace_control_is_not_clicked(self):
        self.wp.overwrite_links = 0
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_replace", self.plan_path)
        self.assertNotIn("overwrite", self.wp.clicks)

    def test_an_off_origin_redirect_after_upload_is_refused(self):
        self.wp.redirect_after_upload = "https://evil.test/wp-admin/update.php"
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_replace", self.plan_path)
        self.assertNotIn("overwrite", self.wp.clicks)

    def test_a_redirect_to_a_non_allowlisted_admin_page_is_refused(self):
        self.wp.redirect_after_upload = f"{deploy.EXACT_ORIGIN}/wp-admin/options-general.php"
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_replace", self.plan_path)
        self.assertNotIn("overwrite", self.wp.clicks)

    def test_a_missing_file_input_is_refused(self):
        self.wp.no_file_input = True
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_replace", self.plan_path)
        self.assertEqual(self.wp.uploads, [])

    def test_a_replacement_that_does_not_take_is_locked_indeterminate(self):
        self.wp.upload_replaces = False
        with self.assertRaises(deploy.IndeterminateError) as caught:
            self.commit("plugin_replace", self.plan_path)
        self.assertIn("will not retry", str(caught.exception))
        self.assertEqual(self.lock_of(self.plan_path)["status"], "indeterminate")
        self.assertFalse(self.result_of(self.plan_path)["retry"])

    def test_an_artifact_that_changed_on_disk_after_review_is_refused(self):
        with mock.patch.object(deploy, "ARTIFACT_SHA256", "1" * 64):
            with self.assertRaises(deploy.DeploymentError):
                self.commit("plugin_replace", self.plan_path)
        self.assertEqual(self.wp.uploads, [])
        self.assertFalse(deploy.lock_path(self.plan_path).exists())

    def test_upload_refuses_any_other_file(self):
        page = deploy.AdminPage(FakePage(self.wp))
        with self.assertRaises(deploy.DeploymentError) as caught:
            page.upload_replace(self.tmp / "evil.zip")
        self.assertIn("hard-coded plugin artifact", str(caught.exception))
        self.assertEqual(self.wp.uploads, [])

    def test_only_allowlisted_admin_pages_are_visited(self):
        self.commit("plugin_replace", self.plan_path)
        for url in self.wp.navigations:
            with self.subTest(url=url):
                deploy.assert_admin_url(url)


# ===========================================================================
# commit-activate
# ===========================================================================
class TestCommitActivate(Harness):
    def setUp(self):
        super().setUp()
        self.install_sessions()
        self.wp.version = GOOD_VERSION
        self.plan_path = self.stage_activation()

    def test_happy_path_activates_validates_and_never_deactivates(self):
        self.commit("plugin_activate", self.plan_path)
        self.assertTrue(self.wp.active)
        self.assertEqual(self.wp.clicks, ["activate"])
        self.assertNotIn("deactivate", self.wp.clicks)
        self.assertNotIn("delete", self.wp.clicks)
        self.assertEqual(self.shop.added, 1)
        self.assertEqual([label for label, _ in self.shop.selected],
                         ["SIZE", "PRESSURE RATING", "RESIN TYPE"])
        result = self.result_of(self.plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertFalse(result["emergency_deactivated"])
        self.assertEqual(result["validation"]["exact_message_count"], 1)
        self.assertTrue(result["validation"]["checkout_blocked"])
        self.assertTrue(result["validation"]["passed"])
        self.assertEqual(result["validation"]["reasons"], [])

    def test_a_successful_run_records_every_step_and_the_control_method(self):
        self.commit("plugin_activate", self.plan_path)
        result = self.result_of(self.plan_path)
        self.assertEqual([record["step"] for record in result["validation"]["steps"]],
                         list(deploy.VALIDATION_STEPS))
        self.assertTrue(all(record["ok"] for record in result["validation"]["steps"]))
        self.assertEqual(set(result["validation"]["selection_method"].values()),
                         {"visible_role_radio"})
        self.assertTrue(result["validation"]["variation_resolved"])
        self.assertTrue(result["validation"]["add_to_cart_enabled"])
        self.assertEqual(result["preflight_sha256"], self.evidence_sha256())
        self.assertEqual(result["preflight_runs"], 3)

    def test_activation_clicks_the_same_customer_control_the_preflight_rehearsed(self):
        self.commit("plugin_activate", self.plan_path)
        result = self.result_of(self.plan_path)
        self.assertEqual(result["validation"]["selection_method"],
                         {"SIZE": "visible_role_radio",
                          "PRESSURE RATING": "visible_role_radio",
                          "RESIN TYPE": "visible_role_radio"})
        self.assertEqual([label for label, _ in self.shop.selected],
                         ["SIZE", "PRESSURE RATING", "RESIN TYPE"])

    def test_activation_proves_readiness_with_the_same_helper_before_clicking(self):
        """Add to cart is never pressed until require_variation_ready has passed."""
        order: list[str] = []
        ready = deploy.PublicPage.require_variation_ready
        add = deploy.PublicPage.add_selected_to_cart

        def watched_ready(page):
            order.append("ready")
            return ready(page)

        def watched_add(page):
            order.append("add_to_cart")
            return add(page)

        with mock.patch.object(deploy.PublicPage, "require_variation_ready", watched_ready), \
                mock.patch.object(deploy.PublicPage, "add_selected_to_cart", watched_add):
            self.commit("plugin_activate", self.plan_path)
        self.assertEqual(order, ["ready", "add_to_cart"])

    def test_the_hidden_select_fallback_also_validates(self):
        self.shop.role_radios = False
        self.commit("plugin_activate", self.plan_path)
        result = self.result_of(self.plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(set(result["validation"]["selection_method"].values()),
                         {"backing_select"})

    def test_lock_exists_before_the_activate_click(self):
        seen: list[bool] = []
        original = self.wp._activate

        def watched():
            seen.append(deploy.lock_path(self.plan_path).exists())
            original()

        self.wp._activate = watched
        self.commit("plugin_activate", self.plan_path)
        self.assertEqual(seen, [True], "the replay lock must exist before the activate click")

    def test_browser_sessions_are_never_nested(self):
        self.commit("plugin_activate", self.plan_path)
        self.assertEqual(self.max_concurrent_sessions, 1,
                         "a Playwright session must never be opened inside another")

    def test_the_validation_browser_is_a_separate_anonymous_session(self):
        self.commit("plugin_activate", self.plan_path)
        self.assertGreaterEqual(self.anon_opens, 1)
        self.assertGreaterEqual(self.admin_opens, 1)
        self.assertEqual(set(self.anon_allowlists), {deploy.ALLOWED_PUBLIC_PATHS})

    def _expect_rollback(self, expected_reason=None, expect_recovered=True):
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.commit("plugin_activate", self.plan_path)
        self.assertEqual(self.wp.clicks.count("deactivate"), 1,
                         "emergency deactivation must happen exactly once")
        self.assertFalse(self.wp.active, "the plugin must be left inactive")
        self.assertNotIn("delete", self.wp.clicks)
        result = self.result_of(self.plan_path)
        self.assertEqual(result["status"], "FAILED_CLOSED")
        self.assertTrue(result["emergency_deactivated"])
        self.assertFalse(result["retry"])
        self.assertEqual(result["rollback"]["recovered"], expect_recovered)
        self.assertEqual(self.lock_of(self.plan_path)["status"], "failed_closed")
        self.assertEqual(self.max_concurrent_sessions, 1)
        if expected_reason:
            self.assertIn(expected_reason, json.dumps(result))
        return str(caught.exception)

    def test_missing_message_rolls_back(self):
        self.shop.message_count = 0
        self._expect_rollback("exact_message_count=0")
        self.assertEqual(self.result_of(self.plan_path)["step"], "checkout_assertions")

    def test_duplicate_message_rolls_back(self):
        self.shop.message_count = 2
        self._expect_rollback("exact_message_count=2")

    def test_generic_only_message_rolls_back(self):
        """The 1.0.0 production failure: blocked, but the exact sentence absent."""
        self.shop.message_count = 0
        self.assertIn("exact_message_count=0", self._expect_rollback())

    def test_available_checkout_form_rolls_back(self):
        self.shop.checkout_form = True
        self._expect_rollback("checkout_form_available")

    def test_available_payment_form_rolls_back(self):
        self.shop.payment_form = True
        self._expect_rollback("payment_form_available")

    def test_checkout_fatal_rolls_back(self):
        self.shop.checkout_fatal = True
        self._expect_rollback("checkout_fatal")

    def test_blank_checkout_rolls_back(self):
        self.shop.checkout_blank = True
        self._expect_rollback("checkout_blank")

    def test_unhealthy_homepage_rolls_back_and_reports_no_recovery(self):
        self.shop.home_fatal = True
        self._expect_rollback("storefront_home_unhealthy", expect_recovered=False)
        self.assertEqual(self.result_of(self.plan_path)["step"], "home_load")

    def test_missing_variation_form_rolls_back(self):
        self.shop.variation_form = False
        self._expect_rollback()
        result = self.result_of(self.plan_path)
        self.assertEqual(result["step"], "variation_form")
        self.assertEqual(result["code"], "variations_form_missing")

    def test_missing_variation_option_rolls_back(self):
        self.shop.options_present = False
        self._expect_rollback()
        result = self.result_of(self.plan_path)
        self.assertEqual(result["step"], SIZE_STEP)
        self.assertEqual(result["code"], "option_missing")

    def test_a_selection_that_does_not_read_back_rolls_back(self):
        self.shop.radio_wires_backing = False
        self._expect_rollback()
        result = self.result_of(self.plan_path)
        self.assertEqual(result["step"], SIZE_STEP)
        self.assertEqual(result["code"], "selection_readback_mismatch")

    def test_a_disabled_add_to_cart_now_rolls_back_at_variation_ready(self):
        """The 2026-08-09 production failure, caught one step earlier."""
        self.shop.add_to_cart_stays_disabled = True
        self._expect_rollback()
        result = self.result_of(self.plan_path)
        self.assertEqual(result["step"], "variation_ready")
        self.assertEqual(result["code"], "add_to_cart_disabled")
        self.assertEqual(self.shop.added, 0, "a disabled button must never be pressed")

    def test_an_unresolved_variation_rolls_back_at_variation_ready(self):
        self.shop.variation_resolves = False
        self._expect_rollback()
        result = self.result_of(self.plan_path)
        self.assertEqual(result["step"], "variation_ready")
        self.assertEqual(result["code"], "variation_unresolved")
        self.assertEqual(self.shop.added, 0)

    def test_a_button_disabled_after_readiness_rolls_back_at_add_to_cart(self):
        """The page is still live between the two checks, so both must check."""
        ready = deploy.PublicPage.require_variation_ready

        def then_disable(page):
            result = ready(page)
            self.shop.add_to_cart_stays_disabled = True
            return result

        with mock.patch.object(deploy.PublicPage, "require_variation_ready", then_disable):
            self._expect_rollback()
        result = self.result_of(self.plan_path)
        self.assertEqual(result["step"], "add_to_cart")
        self.assertEqual(result["code"], "add_to_cart_disabled")
        self.assertEqual(self.shop.added, 0)

    def test_a_missing_or_ambiguous_variation_id_rolls_back(self):
        for controls, code in ((0, "variation_id_missing"), (2, "variation_id_ambiguous")):
            with self.subTest(controls=controls):
                self.setUp()
                self.shop.variation_id_controls = controls
                self._expect_rollback()
                result = self.result_of(self.plan_path)
                self.assertEqual(result["step"], "variation_ready")
                self.assertEqual(result["code"], code)

    def test_a_missing_hidden_or_ambiguous_button_rolls_back_at_variation_ready(self):
        cases = (
            ({"add_to_cart_present": False}, "add_to_cart_missing"),
            ({"add_to_cart_buttons": 2}, "add_to_cart_ambiguous"),
            ({"add_to_cart_visible": False}, "add_to_cart_not_visible"),
            ({"add_to_cart_disabled_attr": True}, "add_to_cart_disabled"),
        )
        for overrides, code in cases:
            with self.subTest(code=code):
                self.setUp()
                for name, value in overrides.items():
                    setattr(self.shop, name, value)
                self._expect_rollback()
                result = self.result_of(self.plan_path)
                self.assertEqual(result["step"], "variation_ready")
                self.assertEqual(result["code"], code)
                self.assertEqual(self.shop.added, 0)

    def test_a_product_page_that_went_fatal_after_activation_rolls_back(self):
        self.shop.product_fatal = True
        self._expect_rollback()
        result = self.result_of(self.plan_path)
        self.assertEqual(result["step"], "product_load")
        self.assertEqual(result["code"], "page_fatal")

    def test_unverified_activation_closes_the_plan(self):
        """The click landed but the row never reported active."""
        self.wp._activate = lambda: self.wp.clicks.append("activate")
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_activate", self.plan_path)
        self.assertEqual(self.wp.clicks.count("deactivate"), 0,
                         "there was nothing to deactivate: the row never went active")
        result = self.result_of(self.plan_path)
        self.assertEqual(result["status"], "FAILED_CLOSED")
        self.assertEqual(result["stage"], "activate")
        self.assertEqual(result["exception_class"], "IndeterminateError")
        self.assertFalse(result["retry"])
        self.assertFalse(result["rollback"]["recovered"])

    def test_a_failed_plan_can_never_be_replayed(self):
        self.shop.message_count = 0
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_activate", self.plan_path)
        clicks = list(self.wp.clicks)
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.commit("plugin_activate", self.plan_path)
        self.assertIn("already entered commit", str(caught.exception))
        self.assertEqual(self.wp.clicks, clicks)

    def test_a_successful_plan_can_never_be_replayed(self):
        self.commit("plugin_activate", self.plan_path)
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_activate", self.plan_path)
        self.assertEqual(self.wp.clicks.count("activate"), 1)

    def test_a_stale_row_refuses_before_any_click(self):
        self.wp.active = True
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_activate", self.plan_path)
        self.assertEqual(self.wp.clicks, [])
        self.assertFalse(deploy.lock_path(self.plan_path).exists())

    def test_activation_refuses_a_version_that_is_not_1_0_1(self):
        self.wp.version = OLD_VERSION
        page = deploy.AdminPage(FakePage(self.wp))
        with self.assertRaises(deploy.DeploymentError) as caught:
            page.activate()
        self.assertIn(f"not {GOOD_VERSION}", str(caught.exception))
        self.assertEqual(self.wp.clicks, [])

    def test_no_order_is_placed_and_only_public_pages_are_visited(self):
        self.commit("plugin_activate", self.plan_path)
        allowed = {deploy.HOME_URL, deploy.PRODUCT_URL, deploy.CART_URL, deploy.CHECKOUT_URL}
        self.assertTrue(set(self.shop.navigations) <= allowed,
                        f"unexpected storefront navigation: {self.shop.navigations}")
        for url in self.shop.navigations:
            deploy.assert_public_url(url)


# ===========================================================================
# commit-activate: the preflight gate
# ===========================================================================
class TestCommitActivatePreflight(Harness):
    def setUp(self):
        super().setUp()
        self.install_sessions()
        self.wp.version = GOOD_VERSION
        self.evidence_path = self.preflight()
        self.plan_path = self.stage("plugin_activate", preflight=self.evidence_path)
        self.shop.reset_recording()

    def assertNothingHappened(self):
        self.assertEqual(self.wp.clicks, [])
        self.assertFalse(self.wp.active)
        self.assertFalse(deploy.lock_path(self.plan_path).exists(),
                         "a preflight refusal must leave the plan unburned")
        self.assertFalse(deploy.result_path(self.plan_path).exists())

    def test_a_deleted_evidence_file_refuses_before_any_write(self):
        self.evidence_path.write_text("", encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.commit("plugin_activate", self.plan_path)
        self.assertIn("unreadable", str(caught.exception))
        self.assertNothingHappened()

    def test_evidence_edited_after_approval_refuses_before_any_write(self):
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        evidence.pop("sha256")
        evidence["runs"][0]["selection_method"]["SIZE"] = "backing_select"
        evidence["sha256"] = deploy.digest_for(evidence)
        self.evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.commit("plugin_activate", self.plan_path)
        self.assertIn("changed after Rachad reviewed", str(caught.exception))
        self.assertNothingHappened()

    def test_evidence_that_went_stale_before_commit_refuses(self):
        stale = deploy.utc_now() - timedelta(minutes=31)
        self.rehash_evidence(self.evidence_path, created_utc=stale.isoformat(),
                             expires_utc=(stale + timedelta(minutes=30)).isoformat())
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.commit("plugin_activate", self.plan_path)
        self.assertIn("older than 30 minutes", str(caught.exception))
        self.assertNothingHappened()

    def test_a_substituted_but_valid_rehearsal_is_still_refused(self):
        """A different passing preflight is not the one Rachad approved."""
        other = self.preflight()
        self.assertNotEqual(other, self.evidence_path)
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        plan.pop("sha256")
        plan["preflight"] = {**plan["preflight"], "path": str(other.resolve())}
        plan["sha256"] = deploy.digest_for(plan)
        self.plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        with self.assertRaises(deploy.DeploymentError) as caught:
            self.commit("plugin_activate", self.plan_path)
        self.assertIn("changed after Rachad reviewed", str(caught.exception))
        self.assertNothingHappened()

    def test_the_preflight_is_checked_before_any_browser_is_opened(self):
        stale = deploy.utc_now() - timedelta(hours=3)
        self.rehash_evidence(self.evidence_path, created_utc=stale.isoformat(),
                             expires_utc=(stale + timedelta(minutes=30)).isoformat())
        self.forbid_sessions()
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_activate", self.plan_path)

    def test_a_valid_fresh_preflight_lets_the_commit_proceed(self):
        self.commit("plugin_activate", self.plan_path)
        self.assertEqual(self.result_of(self.plan_path)["status"], "COMMITTED_AND_VERIFIED")


# ===========================================================================
# Step attribution: the 2026-08-09 bare TimeoutError must never recur
# ===========================================================================
class TestValidationStepAttribution(Harness):
    def setUp(self):
        super().setUp()
        self.install_sessions()
        self.wp.version = GOOD_VERSION
        self.plan_path = self.stage_activation()

    def test_a_timeout_at_every_named_step_is_attributed_and_rolls_back(self):
        for step in deploy.VALIDATION_STEPS:
            with self.subTest(step=step):
                self.setUp()  # a fresh temp tree, plan, storefront and session per step
                self.shop.timeout_at = TIMEOUT_MARKERS[step]
                with self.assertRaises(deploy.IndeterminateError) as caught:
                    self.commit("plugin_activate", self.plan_path)
                result = self.result_of(self.plan_path)
                self.assertEqual(result["status"], "FAILED_CLOSED")
                self.assertEqual(result["stage"], "validation")
                self.assertEqual(result["step"], step)
                self.assertEqual(result["exception_class"], "TimeoutError")
                self.assertIsNone(result["code"])
                self.assertTrue(result["emergency_deactivated"])
                self.assertFalse(result["retry"])
                self.assertEqual(self.wp.clicks.count("deactivate"), 1,
                                 "rollback must still happen exactly once")
                self.assertFalse(self.wp.active)
                self.assertEqual(self.lock_of(self.plan_path)["step"], step)
                self.assertIn(step, str(caught.exception))
                self.assertIn("TimeoutError", str(caught.exception))

    def test_a_timeout_is_never_reported_as_a_pass(self):
        self.shop.timeout_at = TIMEOUT_MARKERS["checkout_assertions"]
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_activate", self.plan_path)
        result = self.result_of(self.plan_path)
        self.assertNotEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertNotIn("passed", json.dumps(result.get("validation", {})))

    def test_the_failure_record_never_carries_page_text(self):
        self.shop.timeout_at = TIMEOUT_MARKERS["product_load"]
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_activate", self.plan_path)
        blob = json.dumps(self.result_of(self.plan_path)) + json.dumps(
            self.lock_of(self.plan_path))
        for leak in (STOREFRONT_BODY, "timed out", "critical error", "<"):
            self.assertNotIn(leak, blob)

    def test_partial_steps_before_the_failure_are_recorded_as_completed(self):
        self.shop.timeout_at = TIMEOUT_MARKERS["checkout_load"]
        with self.assertRaises(deploy.IndeterminateError):
            self.commit("plugin_activate", self.plan_path)
        self.assertEqual(self.result_of(self.plan_path)["step"], "checkout_load")

    def test_an_unknown_step_name_can_never_be_recorded(self):
        with self.assertRaises(deploy.DeploymentError):
            deploy.ValidationStepError("some_other_step", "TimeoutError")
        recorder = deploy.StepRecorder()
        with self.assertRaises(deploy.DeploymentError):
            recorder.run("not_a_step", lambda: None)

    def test_a_foreign_exception_code_attribute_is_never_trusted(self):
        class Sneaky(Exception):
            code = "the entire page body would go here"

        recorder = deploy.StepRecorder()
        with self.assertRaises(deploy.ValidationStepError) as caught:
            recorder.run("home_load", lambda: (_ for _ in ()).throw(Sneaky()))
        self.assertIsNone(caught.exception.code)
        self.assertEqual(caught.exception.exception_class, "Sneaky")
        self.assertNotIn("page body", str(caught.exception))


# ===========================================================================
# commit-deactivate
# ===========================================================================
class TestCommitDeactivate(Harness):
    def setUp(self):
        super().setUp()
        self.install_sessions()
        self.wp.version = GOOD_VERSION
        self.wp.active = True
        self.plan_path = self.stage("plugin_deactivate")

    def test_deactivates_and_keeps_it_installed(self):
        self.commit("plugin_deactivate", self.plan_path)
        self.assertFalse(self.wp.active)
        self.assertTrue(self.wp.present)
        self.assertEqual(self.wp.clicks, ["deactivate"])
        self.assertNotIn("delete", self.wp.clicks)
        result = self.result_of(self.plan_path)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertFalse(result["deleted"])
        self.assertTrue(result["after"]["present"])

    def test_lock_exists_before_the_click(self):
        seen: list[bool] = []
        original = self.wp._deactivate

        def watched():
            seen.append(deploy.lock_path(self.plan_path).exists())
            original()

        self.wp._deactivate = watched
        self.commit("plugin_deactivate", self.plan_path)
        self.assertEqual(seen, [True])

    def test_replay_is_refused(self):
        self.commit("plugin_deactivate", self.plan_path)
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_deactivate", self.plan_path)
        self.assertEqual(self.wp.clicks.count("deactivate"), 1)

    def test_no_anonymous_browser_and_no_upload(self):
        self.commit("plugin_deactivate", self.plan_path)
        self.assertEqual(self.anon_opens, 0)
        self.assertEqual(self.wp.uploads, [])

    def test_a_stale_row_refuses_without_clicking(self):
        self.wp.active = False
        with self.assertRaises(deploy.DeploymentError):
            self.commit("plugin_deactivate", self.plan_path)
        self.assertEqual(self.wp.clicks, [])


# ===========================================================================
# Source scan
# ===========================================================================
def _strip_docs_and_comments(source: str) -> str:
    """Return executable source only: docstrings and comments removed.

    Prose legitimately mentions cookies, passwords and page dumps while promising
    NOT to touch them. Scanning raw text would either fire on the promise or force
    the promise to be deleted, so the scan runs on code alone.
    """
    drop: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                first = node.body[0]
                drop.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    lines = ["" if number in drop else line
             for number, line in enumerate(source.splitlines(), 1)]
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


class TestSourceBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(deploy.__file__).read_text(encoding="utf-8")
        cls.code = _strip_docs_and_comments(cls.source)

    def assertAbsent(self, banned):
        for needle in banned:
            with self.subTest(banned=needle):
                self.assertNotIn(needle, self.code)

    def test_no_shell_or_process_execution(self):
        self.assertAbsent(("import subprocess", "subprocess.", "os.system", "os.popen",
                           "os.spawn", "shell=True", "eval(", "exec(", "pty."))

    def test_no_network_or_rest_client(self):
        self.assertAbsent(("import requests", "urllib.request", "http.client", "socket.",
                           "woocommerce_common", "wp-json", "/wc/v3", "api_request",
                           "urlopen", "Request(", "admin-ajax", "xmlrpc"))

    def test_no_credential_cookie_or_storage_access(self):
        self.assertAbsent(("cookie", "storage_state", "localStorage", "sessionStorage",
                           "local_storage", "session_storage", "getpass",
                           "Authorization", "Bearer", "access_token", "credentials",
                           "add_init_script", "set_extra_http_headers"))
        # The tool's only "password" is urlsplit's userinfo component, used to
        # REFUSE a credential-bearing URL. Anything else would be reading one.
        self.assertNotIn("password", self.code.replace("parsed.password", ""))

    def test_no_page_dumps_or_screenshots(self):
        self.assertAbsent(("screenshot", ".content()", "inner_html", "innerHTML",
                           "outer_html", "page_source", "evaluate(", "console"))

    def test_no_typing_of_customer_or_payment_data(self):
        self.assertAbsent((".fill(", ".type(", "keyboard", ".press("))

    def test_no_delete_route_of_any_kind(self):
        self.assertAbsent(("delete-selected", "action=delete", ".delete", "unlink(",
                           "rmtree", "shutil", "os.remove"))

    def test_no_persistent_browser_profile_is_ever_launched(self):
        self.assertAbsent(("launch_persistent_context", "user_data_dir", "--user-data-dir",
                           "edge-profile"))
        self.assertIn("connect_over_cdp", self.code)

    def test_the_anonymous_session_is_a_throwaway_context(self):
        block = self.source.split("def anonymous_session")[1].split("\n# ---")[0]
        self.assertIn("headless=True", block)
        self.assertIn("new_context()", block)
        self.assertNotIn("storage_state", block)
        self.assertNotIn("launch_persistent_context", block)
        self.assertNotIn("user_data_dir", block)

    def test_the_preflight_never_reaches_an_admin_session(self):
        block = self.source.split("def _preflight_single_run")[1].split("\n# ---")[0]
        self.assertIn("anonymous_session(PREFLIGHT_PUBLIC_PATHS)", block)
        self.assertIn("require_variation_ready", block)
        for banned in ("admin_session", "CART_URL", "CHECKOUT_URL",
                       "add_selected_to_cart", "goto_checkout", "read_checkout"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, block)

    def test_the_preflight_opens_one_fresh_context_per_rehearsal(self):
        """Three runs must mean three `with anonymous_session(...)` entries."""
        block = self.source.split("def _preflight_single_run")[1].split("\n# ---")[0]
        self.assertEqual(block.count("with anonymous_session("), 1,
                         "one run opens exactly one context")
        self.assertIn("for index in range(1, PREFLIGHT_RUNS + 1)", block)
        self.assertIn("_preflight_single_run(index)", block)
        self.assertEqual(deploy.PREFLIGHT_RUNS, 3)

    def test_the_preflight_command_performs_no_write_and_no_admin_work(self):
        block = self.source.split("def command_preflight_validation")[1].split(
            "\ndef _live_row")[0]
        for banned_call in ("admin_session(", "write_lock(", "stage_plan(",
                            ".activate(", "upload_replace(", ".click(",
                            "add_selected_to_cart", "goto_checkout", "CART_URL",
                            "CHECKOUT_URL"):
            with self.subTest(banned_call=banned_call):
                self.assertNotIn(banned_call, block)

    def test_only_the_eight_subcommands_exist(self):
        literal = set(re.findall(r'add_parser\("([a-z-]+)"\)', self.source))
        looped = set(re.findall(r'^\s+\("([a-z-]+)", command_', self.source, re.M))
        self.assertEqual(literal | looped, {
            "inspect", "preflight-validation",
            "stage-replace", "stage-activate", "stage-deactivate",
            "commit-replace", "commit-activate", "commit-deactivate"})

    def test_every_navigation_and_action_carries_an_explicit_timeout(self):
        for call in re.findall(r"\.goto\([^)]*\)", self.code):
            with self.subTest(call=call):
                self.assertIn("timeout=", call)
        for call in re.findall(r"\.wait_for_load_state\([^)]*\)", self.code):
            with self.subTest(call=call):
                self.assertIn("timeout=", call)
        for call in re.findall(r"\.click\([^)]*\)", self.code):
            with self.subTest(call=call):
                self.assertIn("timeout=", call)
        for call in re.findall(r"\.select_option\([^)]*\)", self.code):
            with self.subTest(call=call):
                self.assertIn("timeout=", call)
        for call in re.findall(r"\.input_value\([^)]*\)", self.code):
            with self.subTest(call=call):
                self.assertIn("timeout=", call)

    def test_the_forced_selection_is_only_used_on_the_backing_select(self):
        forced = re.findall(r"^.*force=True.*$", self.code, re.M)
        self.assertEqual(len(forced), 1, "force must appear exactly once")
        self.assertIn("select.select_option", forced[0])

    def test_every_hard_coded_url_stays_on_the_fixed_origin(self):
        for url in re.findall(r"https?://[^\s\"')]+", self.source):
            if url == deploy.CDP_ENDPOINT:
                continue
            with self.subTest(url=url):
                self.assertTrue(url.startswith(deploy.EXACT_ORIGIN),
                                f"{url} is not on the fixed origin")

    def test_selectors_are_anchored_and_never_positional(self):
        self.assertAbsent((":first", ":nth", "first_of_type", ">> nth=", ":visible",
                           "a.activate", "a.deactivate", "input[type=\"radio\"][",
                           "has-text", "text=", ":checked"))
        self.assertEqual(
            deploy.ROW_SELECTOR,
            f'tr[data-plugin="{PLUGIN_FILE}"]:not(.plugin-update-tr)',
        )
        self.assertIn(".row-actions .activate a", self.code)
        self.assertIn(".row-actions .deactivate a", self.code)
        self.assertEqual(deploy.ROLE_RADIO_SELECTOR, '[role="radio"][data-value]')
        self.assertEqual(deploy.VARIATION_ID_SELECTOR, "input.variation_id")
        self.assertEqual(deploy.ADD_TO_CART_SELECTOR, "button.single_add_to_cart_button")
        self.assertEqual(deploy.VARIATION_FORM_SELECTOR, "form.variations_form")
        self.assertFalse(hasattr(deploy, "VISIBLE_RADIO_SELECTOR"),
                         "the retired input-radio selector must be gone, not kept beside")

    def test_no_attribute_value_is_ever_interpolated_into_a_selector(self):
        """Every selector literal is fixed; the exact match happens in Python.

        The required values contain a double quote and a slash. Building a selector
        around them would be fragile and would put page-derived data into a query,
        so the tool must never do it.
        """
        for call in re.findall(r"query_selector(?:_all)?\(([^)]*)\)", self.code):
            argument = call.strip()
            with self.subTest(selector=argument):
                self.assertFalse(argument.startswith(("f\"", "f'")),
                                 "a selector must never be an f-string")
                for banned in ("%", ".format(", "+ ", "{"):
                    self.assertNotIn(banned, argument)
        for name in ("ROLE_RADIO_SELECTOR", "VARIATION_ID_SELECTOR", "ADD_TO_CART_SELECTOR",
                     "VARIATION_FORM_SELECTOR", "BACKING_SELECT_SELECTOR"):
            with self.subTest(constant=name):
                for banned in ('1/2"', "150PSI", "D411", "{", "%"):
                    self.assertNotIn(banned, getattr(deploy, name))

    def test_the_withdrawn_artifact_is_named_and_refused_in_source(self):
        self.assertIn(OLD_SHA256, self.source)
        self.assertIn("can never be approved or installed", self.source)

    def test_the_tool_declares_no_other_plugin_or_slug(self):
        for other in ("akismet", "woocommerce/woocommerce.php", "hello-dolly",
                      "woocommerce-shipping", "wp-super-cache"):
            with self.subTest(other=other):
                self.assertNotIn(other, self.code.casefold())
        # Every plugin path/slug literal in the tool is the one fixed plugin.
        for literal in re.findall(r'"([a-z0-9-]+/[a-z0-9-]+\.php)"', self.code):
            self.assertEqual(literal, PLUGIN_FILE)


class TestFakeEngineIsHonest(unittest.TestCase):
    """A permissive fake would silently invalidate every scoping test above."""

    def setUp(self):
        self.root = FakeElement("body", children=[
            FakeElement("tr", cls="inactive", attrs={"data-plugin": PLUGIN_FILE},
                        children=[FakeElement("span", cls="activate", children=[
                            FakeElement("a", text="Activate")])]),
            FakeElement("tr", cls="plugin-update-tr", attrs={"data-plugin": PLUGIN_FILE}),
            FakeElement("tr", cls="active", attrs={"data-plugin": "other/other.php"}),
        ])

    def test_attribute_selector_discriminates(self):
        self.assertEqual(len(_select(self.root, f'tr[data-plugin="{PLUGIN_FILE}"]')), 2)
        self.assertEqual(len(_select(self.root, 'tr[data-plugin="other/other.php"]')), 1)
        self.assertEqual(len(_select(self.root, 'tr[data-plugin="missing"]')), 0)

    def test_not_pseudo_class_excludes_the_update_row(self):
        found = _select(self.root, deploy.ROW_SELECTOR)
        self.assertEqual(len(found), 1)
        self.assertIn("inactive", found[0].classes)

    def test_descendant_combinator_requires_the_whole_chain(self):
        self.assertEqual(len(_select(self.root, ".row-actions .activate a")), 0)
        self.assertEqual(len(_select(self.root, ".activate a")), 1)

    def test_class_selector_is_not_a_substring_match(self):
        self.assertEqual(len(_select(self.root, ".activ")), 0)
        self.assertEqual(len(_select(self.root, "tr.active")), 1)

    def test_unsupported_selectors_fail_loudly(self):
        for selector in ("tr:nth-child(2)", "tr > td", "input:checked", "tr + tr"):
            with self.subTest(selector=selector), self.assertRaises(AssertionError):
                _select(self.root, selector)

    def test_radio_type_selector_discriminates(self):
        root = FakeElement("td", children=[
            FakeElement("input", attrs={"type": "radio", "value": "a"}),
            FakeElement("input", attrs={"type": "checkbox", "value": "a"}),
            FakeElement("select", attrs={"aria-hidden": "true"}),
        ])
        self.assertEqual(len(_select(root, 'input[type="radio"]')), 1)
        self.assertEqual(len(_select(root, "select")), 1)

    def test_the_role_radio_selector_requires_both_role_and_a_data_value(self):
        root = FakeElement("ul", children=[
            FakeElement("li", attrs={"role": "radio", "data-value": '1/2"'}),
            FakeElement("li", attrs={"role": "radio"}),               # no data-value
            FakeElement("li", attrs={"data-value": '1/2"'}),          # no role
            FakeElement("li", attrs={"role": "option", "data-value": '1/2"'}),
        ])
        found = _select(root, deploy.ROLE_RADIO_SELECTOR)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].get_attribute("data-value"), '1/2"')

    def test_the_variation_id_selector_is_a_class_not_a_name_guess(self):
        root = FakeElement("form", children=[
            FakeElement("input", cls="variation_id", attrs={"type": "hidden"}),
            FakeElement("input", attrs={"type": "hidden", "name": "variation_id"}),
            FakeElement("select", cls="variation_id"),
        ])
        self.assertEqual(len(_select(root, deploy.VARIATION_ID_SELECTOR)), 1)

    def test_a_hidden_element_refuses_an_unforced_click(self):
        hidden = FakeElement("li", visible=False, on_click=lambda: None)
        with self.assertRaises(Playwright.TimeoutError):
            hidden.click(timeout=1000)
        self.assertFalse(hidden.is_visible())

    def test_is_disabled_follows_the_attribute_and_aria(self):
        self.assertFalse(FakeElement("button").is_disabled())
        self.assertTrue(FakeElement("button", attrs={"disabled": "disabled"}).is_disabled())
        self.assertTrue(FakeElement("button", attrs={"aria-disabled": "true"}).is_disabled())
        # A class token is NOT is_disabled()'s business; the tool checks it separately.
        self.assertFalse(FakeElement("button", cls="disabled").is_disabled())

    def test_actions_without_an_explicit_timeout_fail_loudly(self):
        element = FakeElement("button", on_click=lambda: None,
                              on_select=lambda _v: None, on_input_value=lambda: "",
                              on_set_files=lambda _p: None)
        for call in (element.click, element.input_value):
            with self.subTest(call=call.__name__), self.assertRaises(AssertionError):
                call()
        with self.assertRaises(AssertionError):
            element.select_option("x")
        with self.assertRaises(AssertionError):
            element.set_input_files("x")

    def test_a_hidden_select_refuses_an_unforced_selection(self):
        hidden = FakeElement("select", attrs={"aria-hidden": "true"},
                             on_select=lambda _v: None)
        with self.assertRaises(Playwright.TimeoutError):
            hidden.select_option("x", timeout=1000)
        hidden.select_option("x", force=True, timeout=1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
