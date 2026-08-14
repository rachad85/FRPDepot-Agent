#!/usr/bin/env python
"""FRP Depot WordPress Plugin Deployment Tool.

Commissioned by Rachad Homsi on 2026-08-09. Commissioning authorises building and
testing this tool. It is NOT approval of any site change: every write still needs
Rachad's own one-word APPROVED against one exact staged plan.

SCOPE -- ONE PLUGIN, FOUR WRITE ROUTES, NOTHING ELSE.

    plugin_replace      Upload Plugin -> replace the installed copy, leave INACTIVE
    plugin_activate     Plugins row -> Activate, then anonymous public validation
    plugin_deactivate   Plugins row -> Deactivate, stays installed
    plugin_ups_repair   Active 2.0.4 -> exact frozen 2.0.5 hidden-panel CSS repair

The plugin identity, the site origin, the artifact path, the artifact version and
the artifact SHA-256 are all hard-coded constants. A caller supplies no URL, no
path, no slug, no ZIP, no PHP, no selector and no free-form action, so no other
plugin, file or page can be reached through this tool.

WHAT THIS TOOL CANNOT DO, BY CONSTRUCTION: delete a plugin, install an arbitrary
plugin, write a WordPress or WooCommerce setting, touch themes/users/posts/media/
comments/orders/customers/payments/refunds/options, issue any REST call, run a
shell, read or persist a password/cookie/token/storage entry, dump a page, place
an order, or type customer/address/payment data. There is no fill/type call in
this module and no subprocess import.

BROWSER ACCESS. Admin work attaches to the ALREADY AUTHENTICATED loopback CDP
session at 127.0.0.1:9229 that Rachad opened; this tool never launches a browser
profile and never signs in. The anonymous storefront work is deliberately the
opposite: a throwaway headless Edge with NO persistent profile, NO stored state
and therefore no admin cookies, so it sees exactly what a customer sees.

REPLAY. A plan is hashed, nonce-bearing, 24-hour, closed-schema and one-use. An
exclusive lock file is created before the first side effect, so a plan can enter
the acting phase at most once. A failed, committed, expired, tampered or locked
plan is never replayed and never retried.

WITHDRAWN ARTIFACT. Version 1.0.0 / SHA-256 4d8396d9... failed production
validation on 2026-08-09 (it blocked checkout but never showed the message). It
is refused everywhere in this tool and can never be staged, approved or installed.

*** 2026-08-09 REPAIR -- WHY THIS FILE GREW A PREFLIGHT AND STEP NAMES. ***
Activation plan 20260809T180243Z_plugin_activate_4841d651c0e89698 activated 1.0.1
once, and the anonymous validation then raised a BARE TimeoutError. The rollback
worked and the plan is permanently failed-closed, but the record could not say
WHICH sub-step timed out, so nothing could be learned from a production write.
Three consequences, all implemented below.

  1. Every anonymous sub-step now has a fixed name (VALIDATION_STEPS) and every
     failure that reaches rollback records that name plus the exception CLASS.
     Exception TEXT is never recorded: only a code from the closed
     VALIDATION_CODES vocabulary, so no page content can leak into a receipt.
  2. `preflight-validation` rehearses the read-only half of that validation on a
     throwaway anonymous context BEFORE anything is activated, and `stage-activate`
     refuses without fresh, hash-matching evidence that it passed. The mechanism
     that timed out is now exercised while the site is untouched.
  3. Selection prefers the exact VISIBLE customer control, falls back to the exact
     backing select only when no visible control exists at all, and in BOTH cases
     requires the backing value to read back exactly.
Timeouts are bounded and explicit, and a timed-out required step fails closed: a
partially rendered page is never treated as a pass.

*** 2026-08-09 SECOND REPAIR -- THE VISIBLE CONTROL WAS THE WRONG ELEMENT. ***
Activation plan 20260809T185220Z_plugin_activate_83f9fa35eec3cb88 got further and
then failed closed at step `add_to_cart` with `add_to_cart_disabled`; rollback
succeeded and the storefront recovered. The step attribution added above is what
made the next diagnosis possible, and a read-only live inspection of the exact FRP
Pipe form then produced the missing fact.

  THE MISTAKE: the first repair guessed the visible control was
  `input[type="radio"]`. It is not. The live theme renders each option as a VISIBLE
  `<li role="radio" data-value="...">` (classes `variable-item button-variable-item
  ...`) sitting on top of ONE hidden backing `<select>` per attribute row. No
  `input[type="radio"]` exists anywhere in that form, so every attribute silently
  took the `backing_select` branch. Forcing a hidden <select> does change its value
  -- the readback passed, honestly -- but it does NOT run the theme's own click
  handlers, so WooCommerce never resolved a variation and never enabled Add to
  cart. Values changed; the product never became purchasable.

  WHY THE READBACK DID NOT CATCH IT: a backing-select readback proves the SELECT
  agrees with us. It proves nothing about whether WooCommerce agrees the variation
  is purchasable. That is a different fact and it now has its own gate.

Three consequences, all implemented below.

  A. Selection queries only `[role="radio"][data-value]` inside the one exact
     attribute row and filters candidates in PYTHON by an exact `data-value` match.
     Nothing is interpolated into a selector -- the fixed values contain a double
     quote and a slash. Exactly one visible, non-disabled match is required. The
     backing-select fallback survives ONLY for a row with no role-radio controls at
     all; if role-radio controls exist and the exact required value is absent, the
     tool refuses rather than quietly forcing the hidden select again. The method
     vocabulary is named for what it does: `visible_role_radio | backing_select`.
  B. A read-only `variation_ready` step runs after the three selections and before
     Add to cart, in the preflight AND in the activation validation. It polls, with
     a bounded timeout, until WooCommerce itself says the variation resolved (one
     `input.variation_id` inside the one form, holding a positive id -- only the
     boolean is ever recorded, never the id) and the one
     `button.single_add_to_cart_button` is present, visible and carries no disabled
     property, attribute or class token. This is the check whose absence let the
     production attempt reach a disabled button.
  C. `preflight-validation` now runs THREE consecutive rehearsals, each in its own
     brand-new anonymous context with no shared cookies or storage, and all three
     must pass. Rachad asked for three fresh-browser proofs before any activation is
     staged, because one pass on a page this dynamic is a sample, not a habit. The
     run stops at the first failure and records it; staging refuses the evidence.
Bumping TOOL_VERSION, PREFLIGHT_SCHEMA_VERSION and SCHEMA_VERSION together means
every preflight recorded by the old build and every activation plan staged against
it is refused on identity, not merely on hash -- rehashing them cannot help.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timedelta, timezone
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit
import zipfile

# One writer at a time on the shared authenticated WordPress window. Added
# 2026-08-10 with Dado's Discord lane: her two chat lanes now run as genuinely
# concurrent turns, and admin_session() below does not launch a browser -- it
# attaches to the single long-lived Edge session on CDP 127.0.0.1:9229 and
# drives contexts[0].pages[0]. Two concurrent turns would drive the same page.
# Appended, never inserted first, so it cannot shadow a stdlib name.
sys.path.append(str(Path(__file__).resolve().parent.parent / "common"))
from ui_lane_lock import UiLaneBusy, UiLaneLockError, ui_browser_lock  # noqa: E402


def holds_wordpress_browser(purpose: str):
    """Serialize a whole command against the shared WordPress admin browser.

    Applied to the COMMAND rather than only to admin_session() so the hold spans
    the emergency rollback too. Activation deliberately closes its session before
    rolling back (nesting Playwright contexts fails only in production), which
    would otherwise leave a gap for the other lane to take the browser at exactly
    the moment a half-activated plugin needed deactivating. The lock is
    re-entrant per thread, so the nested admin_session() calls inside are no-ops.
    """
    def decorate(function):
        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any):
            with ui_browser_lock("wordpress", purpose=purpose):
                return function(*args, **kwargs)
        return wrapper
    return decorate

TOOL_NAME = "FRP Depot WordPress Plugin Deployment Tool"
TOOL_VERSION = "1.4.0"
SCHEMA_VERSION = 5
PREFLIGHT_SCHEMA_VERSION = 2

ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "wordpress_plugin_plans"
PREFLIGHT_DIR = ROOT / "Dado" / "20_Working" / "wordpress_plugin_preflight"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
PLAN_LIFETIME_HOURS = 24

# Rachad's approval is his own one-word reply to one exact staged plan.
APPROVAL_WORD = "APPROVED"

# ---------------------------------------------------------------------------
# Fixed identity. None of these is ever taken from a caller.
# ---------------------------------------------------------------------------
EXACT_ORIGIN = "https://frpdepots.com"
ALLOWED_HOST = "frpdepots.com"
CDP_ENDPOINT = "http://127.0.0.1:9229"

PLUGIN_NAME = "FRP Depot Freight Checkout Guard"
PLUGIN_SLUG = "frpdepot-freight-checkout-guard"
PLUGIN_FILE = "frpdepot-freight-checkout-guard/frpdepot-freight-checkout-guard.php"

ARTIFACT_PATH = (
    ROOT / "Dado" / "Tools" / "woocommerce" / "freight_checkout_guard"
    / "frpdepot-freight-checkout-guard.zip"
)
ARTIFACT_VERSION = "1.0.1"
ARTIFACT_SHA256 = "fe6fa440ea3a08169bf568ae0fbb06f666ad71c1110e58f9b2b6bb0acc8be6cb"
ARTIFACT_MEMBERS = (
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-notice.js",
    f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php",
    f"{PLUGIN_SLUG}/readme.txt",
    f"{PLUGIN_SLUG}/ups-allowlist.json",
)

# Fixed active-plugin UPS repair commissioned by Rachad's "Fix UPS" / "RESUME UPS"
# instructions on 2026-08-13/14. The artifact is frozen from the exact deployed
# 2.0.4 ZIP and changes only two version tokens, the disclosed readme and the
# exact one-rule CSS fix. The 64-variation allowlist remains byte-identical.
# It is deliberately a separate action:
# the older inactive 1.0.0 -> 1.0.1 deployment routes remain scoped as before.
UPS_REPAIR_ARTIFACT_PATH = (
    ROOT / "Dado" / "20_Working" / "ups_repair_2_0_5"
    / "frpdepot-freight-checkout-guard-2.0.5.zip"
)
UPS_REPAIR_VERSION = "2.0.5"
UPS_REPAIR_FROM_VERSION = "2.0.4"
UPS_REPAIR_SHA256 = "0955d21163c5cc96f5f9eea7e71935807f8433450904ac2f815baa8d6cbe8d10"
UPS_REPAIR_ALLOWLIST_SHA256 = "a8051de3e7c99a3d8285c3199f1f0a32bb525ff8ca3dac56acbf7132f8e154a8"
UPS_REPAIR_BASELINE_PATH = (
    ROOT / "Dado" / "20_Working" / "ups_repair_2_0_5"
    / "frpdepot-freight-checkout-guard-2.0.4.zip"
)
UPS_REPAIR_BASELINE_SHA256 = "9f4d1917b99a1a75de8a2549375e8e262cdbb3bd2353bc09560636821f1e4f75"
UPS_REPAIR_MEASUREMENT_STATUS = (
    "RESEARCH-BASED ESTIMATE - NOT PHYSICALLY VERIFIED - NOT UPS APPROVED"
)
UPS_REPAIR_MEMBERS = (
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.css",
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.js",
    f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php",
    f"{PLUGIN_SLUG}/readme.txt",
    f"{PLUGIN_SLUG}/ups-allowlist.json",
)
UPS_REPAIR_CSS_SUFFIX = (
    "\n/* The active theme gives section elements display:block even when hidden. */\n"
    ".frpdepot-fqj-product[hidden] {\n"
    "\tdisplay: none !important;\n"
    "}\n"
).encode("utf-8")

# Permanently refused. Withdrawn after the 2026-08-09 production failure.
WITHDRAWN_VERSION = "1.0.0"
WITHDRAWN_SHA256 = "4d8396d95baf0907754730e578ad4c41b98908f77992718c41b293434e07fe25"

# The only version this tool may ever put on the site, and the only version it
# may ever activate.
REPLACE_FROM_VERSION = WITHDRAWN_VERSION
REPLACE_TO_VERSION = ARTIFACT_VERSION

# ---------------------------------------------------------------------------
# Fixed navigation. Any other path, host, scheme or port is refused.
# ---------------------------------------------------------------------------
PLUGINS_URL = f"{EXACT_ORIGIN}/wp-admin/plugins.php"
UPLOAD_URL = f"{EXACT_ORIGIN}/wp-admin/plugin-install.php?tab=upload"
ALLOWED_ADMIN_PATHS = frozenset({
    "/wp-admin/plugins.php",
    "/wp-admin/plugin-install.php",
    "/wp-admin/update.php",
})

HOME_URL = f"{EXACT_ORIGIN}/"
PRODUCT_URL = f"{EXACT_ORIGIN}/product/frp-fw-pipe/"
CART_URL = f"{EXACT_ORIGIN}/cart/"
CHECKOUT_URL = f"{EXACT_ORIGIN}/checkout/"
ALLOWED_PUBLIC_PATHS = frozenset({"/", "/product/frp-fw-pipe/", "/cart/", "/checkout/"})

# The preflight rehearsal is read-only and must never reach a cart or a checkout,
# so it gets a STRICTLY NARROWER allowlist than the post-activation validation.
PREFLIGHT_PUBLIC_PATHS = frozenset({"/", "/product/frp-fw-pipe/"})

# ---------------------------------------------------------------------------
# Bounded, explicit timings. The public site is sometimes slow, so navigation is
# given real headroom -- but nothing here is unbounded, and NOTHING converts a
# timeout into a pass. A required step that times out fails closed.
# ---------------------------------------------------------------------------
NAV_TIMEOUT_MS = 45_000
LOAD_STATE_TIMEOUT_MS = 45_000
ACTION_TIMEOUT_MS = 15_000
READBACK_TIMEOUT_MS = 8_000
READBACK_POLL_SECONDS = 0.1

# WooCommerce resolves a variation in the browser after the last option is chosen,
# so readiness is POLLED rather than read once. Bounded, and a deadline reached
# without readiness is a refusal, never a shrug.
READINESS_TIMEOUT_MS = 20_000
READINESS_POLL_SECONDS = 0.25

# ---------------------------------------------------------------------------
# Fixed post-activation validation contract. Staged into the activation plan so
# Rachad approves the rollback criteria at the same time as the activation.
# ---------------------------------------------------------------------------
EXACT_MESSAGE = "Contact us for a freight quote."
REQUIRED_VARIATION = (
    ("SIZE", "1/2\""),
    ("PRESSURE RATING", "150PSI"),
    ("RESIN TYPE", "D411"),
)
FATAL_MARKERS = (
    "there has been a critical error on this website",
    "fatal error",
    "parse error:",
    "call to undefined function",
)
MIN_RENDERED_TEXT = 40  # Below this a storefront page is treated as blank.

ACTIONS = ("plugin_replace", "plugin_activate", "plugin_deactivate", "plugin_ups_repair")


def _select_step(label: str) -> str:
    return "select_" + re.sub(r"[^0-9A-Za-z]+", "_", str(label)).strip("_")


# Fixed step names. Nothing outside this tuple can ever be recorded as a step, so
# a receipt can name a failure without naming a page.
#
# `variation_ready` sits deliberately between the last selection and Add to cart:
# it is the step that asks WooCommerce whether the variation resolved, which is the
# question the 2026-08-09 add_to_cart_disabled failure proved nobody was asking.
SELECT_STEPS = tuple(_select_step(label) for label, _ in REQUIRED_VARIATION)
PREFLIGHT_STEPS = ("home_load", "product_load", "variation_form", *SELECT_STEPS,
                   "variation_ready")
VALIDATION_STEPS = (*PREFLIGHT_STEPS, "add_to_cart", "checkout_load", "checkout_assertions")

# The ONLY refusal vocabulary a step may carry into a receipt. Every entry is a
# fixed token chosen here; none is derived from page text, an attribute value or
# a foreign exception's message.
VALIDATION_CODES = frozenset({
    "page_blank",
    "page_fatal",
    "variations_form_missing",
    "variations_form_ambiguous",
    "attribute_row_missing",
    "attribute_row_ambiguous",
    "backing_select_missing",
    "backing_select_ambiguous",
    "option_missing",
    "option_ambiguous",
    "role_radio_value_missing",
    "role_radio_ambiguous",
    "role_radio_not_visible",
    "role_radio_disabled",
    "selection_readback_mismatch",
    "variation_id_missing",
    "variation_id_ambiguous",
    "variation_unresolved",
    "add_to_cart_missing",
    "add_to_cart_ambiguous",
    "add_to_cart_not_visible",
    "add_to_cart_disabled",
    "add_to_cart_page_unhealthy",
})

# Named for what the tool actually did, so a receipt cannot flatter itself. The
# 1.1.0 vocabulary said "visible_radio" while every live attribute in fact took the
# hidden-select branch; that is precisely the confusion this rename removes.
SELECTION_METHODS = frozenset({"visible_role_radio", "backing_select"})

# Which named step owns each contract failure, so a clean (non-exception)
# validation failure is attributed just as precisely as a crash.
STEP_BY_FAILURE_REASON = {
    "storefront_home_unhealthy": "home_load",
    "checkout_fatal": "checkout_assertions",
    "checkout_blank": "checkout_assertions",
    "checkout_form_available": "checkout_assertions",
    "payment_form_available": "checkout_assertions",
}

# Row-action selectors are scoped to the fixed plugin row. `.delete` is
# deliberately absent: this tool has no path that can reach a Delete link.
#
# :not(.plugin-update-tr) is load-bearing. When an update is pending WordPress
# emits a SECOND row -- the update notice -- carrying the same data-plugin value.
# Without the exclusion the fixed row would look ambiguous and every action would
# refuse, exactly when the site most needs one.
ROW_SELECTOR = f'tr[data-plugin="{PLUGIN_FILE}"]:not(.plugin-update-tr)'
UPDATE_ROW_SELECTOR = f'tr.plugin-update-tr[data-plugin="{PLUGIN_FILE}"]'
ACTIVATE_SELECTOR = ".row-actions .activate a"
DEACTIVATE_SELECTOR = ".row-actions .deactivate a"
VERSION_SELECTOR = ".plugin-version-author-uri"
VERSION_PATTERN = re.compile(r"(?i)\bversion\s+([0-9][0-9A-Za-z.\-+_]*)")

# Variation controls. Every one of these is anchored to the fixed attribute row;
# none is positional and none reaches outside the single variations form.
#
# ROLE_RADIO_SELECTOR is the CUSTOMER-FACING control as the live FRP Pipe page
# actually renders it: a visible <li role="radio" data-value="..."> per option. It
# carries NO value: the required value is compared in Python against each
# candidate's own data-value, because the fixed values contain a double quote and a
# slash and have no business inside a selector string.
VARIATION_FORM_SELECTOR = "form.variations_form"
ATTRIBUTE_ROW_SELECTOR = "tr"
ATTRIBUTE_LABEL_SELECTOR = "th"
BACKING_SELECT_SELECTOR = "select"
ROLE_RADIO_SELECTOR = '[role="radio"][data-value]'
ROLE_RADIO_VALUE_ATTRIBUTE = "data-value"
VARIATION_ID_SELECTOR = "input.variation_id"
ADD_TO_CART_SELECTOR = "button.single_add_to_cart_button"

# WooCommerce's own "you have not chosen a purchasable variation yet" markers, plus
# the plain Bootstrap-ish `disabled`. Any of them means the button is not ready.
DISABLED_CLASS_TOKENS = frozenset({"disabled", "wc-variation-selection-needed"})


def _class_tokens(element: Any) -> set[str]:
    return set(str(element.get_attribute("class") or "").split())


def _marked_disabled(element: Any) -> bool:
    """Disabled by attribute or by ARIA. Neither reads page text."""
    if element.get_attribute("disabled") is not None:
        return True
    return str(element.get_attribute("aria-disabled") or "").casefold() == "true"


class DeploymentError(RuntimeError):
    """A refusal or verified failure. Messages never carry page or secret text."""


class IndeterminateError(DeploymentError):
    """The live state could not be established. Never retried automatically."""


class ValidationRefusal(IndeterminateError):
    """A named step refused. Its message is ONE fixed code, never page text."""

    def __init__(self, code: str):
        if code not in VALIDATION_CODES:
            raise DeploymentError("Internal: a validation refusal used an unknown code.")
        self.code = code
        super().__init__(code)


class ValidationStepError(IndeterminateError):
    """A named step failed. Carries the fixed step name and the exception CLASS.

    This is the fix for the 2026-08-09 bare TimeoutError: a rollback record can
    now always say WHERE it failed and WHAT class of failure it was, without ever
    carrying the exception's text.
    """

    def __init__(self, step: str, exception_class: str, code: str | None = None):
        if step not in VALIDATION_STEPS:
            raise DeploymentError("Internal: a validation failure used an unknown step name.")
        self.step = step
        self.exception_class = str(exception_class)
        self.code = code if code in VALIDATION_CODES else None
        super().__init__(
            f"Validation step {step} failed with {self.exception_class}"
            + (f" ({self.code})." if self.code else ".")
        )


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": utc_now().isoformat(), "action": action, "evidence": evidence}
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def require_rachad_approval(approval: str) -> None:
    """Exact uppercase one-word approval, before any write."""
    if not isinstance(approval, str) or approval != APPROVAL_WORD:
        raise DeploymentError(
            f"Rachad must answer this staged plan with the exact one-word approval: {APPROVAL_WORD}. "
            "It must come from his own message; commissioning is not change approval."
        )


def assert_origin(url: str) -> None:
    parsed = urlsplit(str(url or ""))
    try:
        port = parsed.port
    except ValueError as exc:
        raise DeploymentError("REFUSED: the page URL carries an invalid port.") from exc
    if (parsed.scheme != "https" or (parsed.hostname or "").casefold() != ALLOWED_HOST
            or port not in (None, 443) or parsed.username or parsed.password):
        raise DeploymentError(
            f"REFUSED: the browser is not on the exact FRP Depot origin {EXACT_ORIGIN}."
        )


def assert_admin_url(url: str) -> None:
    assert_origin(url)
    path = urlsplit(str(url)).path or "/"
    if path not in ALLOWED_ADMIN_PATHS:
        raise DeploymentError(
            "REFUSED: the WordPress admin page is not one of the three allowlisted "
            "plugin pages. Sign-in, settings and every other admin screen are out of scope."
        )


def assert_public_url(url: str, allowed: frozenset[str] = ALLOWED_PUBLIC_PATHS) -> None:
    assert_origin(url)
    path = urlsplit(str(url)).path or "/"
    if path not in allowed:
        raise DeploymentError("REFUSED: the storefront page is not an allowlisted public page.")


# ---------------------------------------------------------------------------
# Artifact verification. Local only -- reads the ZIP, uploads nothing.
# ---------------------------------------------------------------------------
def verify_artifact(path: Path | None = None) -> dict[str, Any]:
    artifact = Path(path or ARTIFACT_PATH)
    if artifact.resolve() != Path(ARTIFACT_PATH).resolve():
        raise DeploymentError("REFUSED: only the one hard-coded plugin artifact may be used.")
    if not artifact.is_file():
        raise DeploymentError(f"The plugin artifact is missing: {artifact}")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if digest == WITHDRAWN_SHA256:
        raise DeploymentError(
            "REFUSED: this is the withdrawn 1.0.0 artifact that failed production validation "
            "on 2026-08-09. It can never be approved or installed. Rebuild 1.0.1."
        )
    if digest != ARTIFACT_SHA256:
        raise DeploymentError(
            "REFUSED: the plugin artifact SHA-256 does not match the approved 1.0.1 hash."
        )
    try:
        with zipfile.ZipFile(artifact) as archive:
            members = tuple(sorted(archive.namelist()))
            php = archive.read(f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php")
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise DeploymentError("REFUSED: the plugin artifact is unreadable or incomplete.") from exc
    if members != tuple(sorted(ARTIFACT_MEMBERS)):
        raise DeploymentError("REFUSED: the plugin artifact members are not the approved set.")
    header = php.decode("utf-8", errors="replace")
    found = re.search(r"(?im)^\s*\*\s*Version:\s*(\S+)\s*$", header)
    version = found.group(1) if found else ""
    if version == WITHDRAWN_VERSION:
        raise DeploymentError("REFUSED: the artifact declares the withdrawn version 1.0.0.")
    if version != ARTIFACT_VERSION:
        raise DeploymentError(
            f"REFUSED: the artifact declares version {version!r}, not {ARTIFACT_VERSION}."
        )
    if f"Plugin Name: {PLUGIN_NAME}" not in header:
        raise DeploymentError("REFUSED: the artifact is not the fixed FRP Depot plugin.")
    return {
        "path": str(artifact),
        "sha256": digest,
        "version": version,
        "members": list(members),
        "bytes": artifact.stat().st_size,
    }


def verify_ups_repair_artifact(path: Path | None = None) -> dict[str, Any]:
    """Independently prove the one frozen active 2.0.4 -> 2.0.5 CSS repair ZIP."""
    artifact = Path(path or UPS_REPAIR_ARTIFACT_PATH)
    if artifact.resolve() != Path(UPS_REPAIR_ARTIFACT_PATH).resolve():
        raise DeploymentError("REFUSED: only the fixed UPS repair artifact may be used.")
    if not artifact.is_file():
        raise DeploymentError(f"The fixed UPS repair artifact is missing: {artifact}")
    data = artifact.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != UPS_REPAIR_SHA256:
        raise DeploymentError("REFUSED: the fixed UPS repair artifact SHA-256 does not match.")

    baseline = Path(UPS_REPAIR_BASELINE_PATH)
    if not baseline.is_file():
        raise DeploymentError("REFUSED: the frozen deployed 2.0.4 baseline is missing.")
    baseline_data = baseline.read_bytes()
    if hashlib.sha256(baseline_data).hexdigest() != UPS_REPAIR_BASELINE_SHA256:
        raise DeploymentError("REFUSED: the frozen deployed 2.0.4 baseline SHA-256 changed.")

    try:
        with zipfile.ZipFile(artifact) as repair_zip, zipfile.ZipFile(baseline) as baseline_zip:
            members = tuple(sorted(repair_zip.namelist()))
            baseline_members = tuple(sorted(baseline_zip.namelist()))
            php_member = f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php"
            allowlist_member = f"{PLUGIN_SLUG}/ups-allowlist.json"
            readme_member = f"{PLUGIN_SLUG}/readme.txt"
            repair_php = repair_zip.read(php_member)
            baseline_php = baseline_zip.read(php_member)
            allowlist_bytes = repair_zip.read(allowlist_member)
            readme = repair_zip.read(readme_member).decode("utf-8")
            js_member = f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.js"
            if repair_zip.read(js_member) != baseline_zip.read(js_member):
                raise DeploymentError(
                    "REFUSED: the CSS-only UPS repair changed protected JavaScript."
                )
            css_member = f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.css"
            if repair_zip.read(css_member) != baseline_zip.read(css_member) + UPS_REPAIR_CSS_SUFFIX:
                raise DeploymentError(
                    "REFUSED: the UPS repair is not the exact fixed hidden-panel CSS rule."
                )
    except (KeyError, OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise DeploymentError("REFUSED: the fixed UPS repair artifact is unreadable.") from exc

    if members != tuple(sorted(UPS_REPAIR_MEMBERS)) or baseline_members != members:
        raise DeploymentError("REFUSED: the UPS repair ZIP members are not the fixed set.")
    header = repair_php.decode("utf-8", errors="strict")
    found = re.search(r"(?im)^\s*\*\s*Version:\s*(\S+)\s*$", header)
    version = found.group(1) if found else ""
    if version != UPS_REPAIR_VERSION or f"FRPDEPOT_FQJ_VERSION = '{UPS_REPAIR_VERSION}'" not in header:
        raise DeploymentError("REFUSED: the UPS repair does not declare fixed version 2.0.5.")
    if f"Plugin Name: {PLUGIN_NAME}" not in header:
        raise DeploymentError("REFUSED: the UPS repair is not the fixed FRP Depot plugin.")
    if "register_activation_hook" in header or "admin_post_frpdepot_fqj_fixed_apply" not in header:
        raise DeploymentError("REFUSED: the UPS repair changed the fixed activation/apply trigger contract.")

    normalized_php = header.replace(
        " * Version:     2.0.5", " * Version:     2.0.4", 1
    ).replace(
        "const FRPDEPOT_FQJ_VERSION = '2.0.5';",
        "const FRPDEPOT_FQJ_VERSION = '2.0.4';",
        1,
    ).encode("utf-8")
    if normalized_php != baseline_php:
        raise DeploymentError("REFUSED: the UPS repair PHP changed beyond the two version tokens.")

    if hashlib.sha256(allowlist_bytes).hexdigest() != UPS_REPAIR_ALLOWLIST_SHA256:
        raise DeploymentError("REFUSED: the UPS repair allowlist SHA-256 changed.")
    try:
        allowlist = json.loads(allowlist_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError("REFUSED: the UPS repair allowlist is unreadable.") from exc
    required = {
        "schema_version": 2,
        "measurement_status": UPS_REPAIR_MEASUREMENT_STATUS,
        "verified_packing_groups": 0,
        "researched_candidate_groups": 30,
        "oversized_groups_excluded": 7,
    }
    for key, expected in required.items():
        if allowlist.get(key) != expected:
            raise DeploymentError(f"REFUSED: the UPS repair allowlist field {key!r} changed.")
    items = allowlist.get("items")
    if not isinstance(items, list) or len(items) != 64:
        raise DeploymentError("REFUSED: the UPS repair must allowlist exactly 64 variations.")
    identities: set[tuple[int, int, str]] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "product_id", "variation_id", "sku", "packing_group_id", "source_status",
        }:
            raise DeploymentError("REFUSED: a UPS repair allowlist entry is not the fixed projection.")
        identity = (item["product_id"], item["variation_id"], item["sku"])
        if (item["product_id"] not in (1368, 1423) or not item["variation_id"]
                or not item["sku"] or item["source_status"] != UPS_REPAIR_MEASUREMENT_STATUS):
            raise DeploymentError("REFUSED: a UPS repair allowlist identity or disclosure changed.")
        identities.add(identity)
    if len(identities) != 64:
        raise DeploymentError("REFUSED: UPS repair allowlist identities are duplicated.")
    oversized = {1444, 1445, 1446, 1447, 2044, 2045, 2046, 2047,
                 2048, 2049, 2050, 2051, 2052, 2053}
    if any(identity[1] in oversized for identity in identities):
        raise DeploymentError("REFUSED: the UPS repair includes an oversized variation.")
    try:
        expires = datetime.fromisoformat(str(allowlist["expires_utc"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise DeploymentError("REFUSED: the UPS repair allowlist expiry is invalid.") from exc
    if utc_now() >= expires:
        raise DeploymentError("REFUSED: the UPS repair allowlist has expired.")

    for marker in (
        "Stable tag: 2.0.5",
        "honor its hidden state",
        "64-variation UPS",
        "NOT physical packing measurements",
        "14 variations",
        "60 published FNPT",
    ):
        if marker not in readme:
            raise DeploymentError(f"REFUSED: the UPS repair disclosure is missing {marker!r}.")
    return {
        "path": str(artifact),
        "sha256": digest,
        "version": version,
        "members": list(members),
        "bytes": artifact.stat().st_size,
    }


# ---------------------------------------------------------------------------
# Privacy-projected view of the one plugin row.
#
# Only these fields ever leave the page: presence, state, version, update marker
# and the canonical plugin file. No HTML, no page text, no other plugin's row.
# ---------------------------------------------------------------------------
def row_fingerprint(row: dict[str, Any]) -> str:
    safe = {key: row[key] for key in ("present", "active", "version", "update_marker",
                                      "plugin_file")}
    return hashlib.sha256(canonical(safe).encode("utf-8")).hexdigest()


def project_row(present: bool, active: bool | None, version: str,
                update_marker: bool) -> dict[str, Any]:
    row = {
        "present": bool(present),
        "active": active,
        "version": str(version or ""),
        "update_marker": bool(update_marker),
        "plugin_file": PLUGIN_FILE,
    }
    row["fingerprint"] = row_fingerprint(row)
    return row


class AdminPage:
    """Narrow reader/actor over the WordPress Plugins screen.

    Every lookup is anchored to the fixed plugin row. There is no generic click,
    no generic navigation and no text extraction outside the row and the upload
    comparison table.
    """

    def __init__(self, page: Any):
        self._page = page

    # -- navigation ---------------------------------------------------------
    @property
    def url(self) -> str:
        return str(self._page.url)

    def _goto(self, url: str) -> None:
        assert_admin_url(url)
        self._page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_admin_url(self.url)

    def goto_plugins(self) -> None:
        self._goto(PLUGINS_URL)

    def goto_upload(self) -> None:
        self._goto(UPLOAD_URL)

    # -- reading ------------------------------------------------------------
    def _row(self) -> Any:
        assert_admin_url(self.url)
        matches = self._page.query_selector_all(ROW_SELECTOR)
        if len(matches) == 0:
            raise DeploymentError(
                f"REFUSED: the fixed plugin row for {PLUGIN_FILE} is not present on the "
                "Plugins screen. Nothing was clicked."
            )
        if len(matches) > 1:
            raise DeploymentError(
                f"REFUSED: {len(matches)} rows claim the fixed plugin file. The target is "
                "ambiguous, so nothing was clicked."
            )
        return matches[0]

    def read_row(self) -> dict[str, Any]:
        """Return the privacy projection, or refuse if the row is not unambiguous."""
        row = self._row()
        tokens = set(str(row.get_attribute("class") or "").split())
        by_class_active = "active" in tokens
        by_class_inactive = "inactive" in tokens
        if by_class_active == by_class_inactive:
            raise DeploymentError(
                "REFUSED: the fixed plugin row does not state exactly one of active/inactive."
            )

        has_activate = row.query_selector(ACTIVATE_SELECTOR) is not None
        has_deactivate = row.query_selector(DEACTIVATE_SELECTOR) is not None
        if has_activate == has_deactivate:
            raise DeploymentError(
                "REFUSED: the fixed plugin row does not offer exactly one of "
                "Activate/Deactivate. The target state is ambiguous."
            )
        # Two independent signals must agree, otherwise the screen is stale or
        # not the screen we think it is.
        if by_class_active != has_deactivate:
            raise DeploymentError(
                "REFUSED: the fixed plugin row's state class and its available action "
                "disagree. The target state is ambiguous."
            )

        version_cell = row.query_selector(VERSION_SELECTOR)
        if version_cell is None:
            raise DeploymentError("REFUSED: the fixed plugin row exposes no version cell.")
        found = VERSION_PATTERN.search(str(version_cell.inner_text() or ""))
        if not found:
            raise DeploymentError("REFUSED: the fixed plugin row exposes no readable version.")

        update_marker = (
            "update" in tokens
            or len(self._page.query_selector_all(UPDATE_ROW_SELECTOR)) > 0
        )
        return project_row(True, by_class_active, found.group(1), update_marker)

    # -- acting -------------------------------------------------------------
    def _click_row_action(self, selector: str, expect_active_before: bool) -> None:
        before = self.read_row()
        if before["active"] is not expect_active_before:
            raise DeploymentError(
                "REFUSED: the fixed plugin is not in the state this action requires. "
                "Nothing was clicked."
            )
        if before["version"] != ARTIFACT_VERSION:
            raise DeploymentError(
                f"REFUSED: the installed version is not {ARTIFACT_VERSION}. Nothing was clicked."
            )
        link = self._row().query_selector(selector)
        if link is None:
            raise DeploymentError("REFUSED: the scoped row action is not available.")
        assert_admin_url(self.url)
        link.click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_STATE_TIMEOUT_MS)
        assert_admin_url(self.url)

    def activate(self) -> dict[str, Any]:
        self._click_row_action(ACTIVATE_SELECTOR, expect_active_before=False)
        after = self.read_row()
        if after["active"] is not True or after["version"] != ARTIFACT_VERSION:
            raise IndeterminateError(
                "The plugin row does not read back as active on the approved version."
            )
        return after

    def deactivate(self) -> dict[str, Any]:
        self._click_row_action(DEACTIVATE_SELECTOR, expect_active_before=True)
        after = self.read_row()
        if after["active"] is not False:
            raise IndeterminateError("The plugin row does not read back as inactive.")
        return after

    # -- replace ------------------------------------------------------------
    def upload_replace(self, artifact: Path) -> dict[str, Any]:
        """Upload the fixed legacy 1.0.1 ZIP and take WordPress's replace branch."""
        if Path(artifact).resolve() != Path(ARTIFACT_PATH).resolve():
            raise DeploymentError("REFUSED: only the one hard-coded plugin artifact may be uploaded.")
        return self._upload_fixed_replace(artifact, ARTIFACT_VERSION)

    def upload_ups_repair(self, artifact: Path) -> dict[str, Any]:
        """Upload only the exact frozen 2.0.5 UPS repair ZIP."""
        if Path(artifact).resolve() != Path(UPS_REPAIR_ARTIFACT_PATH).resolve():
            raise DeploymentError("REFUSED: only the fixed UPS repair artifact may be uploaded.")
        return self._upload_fixed_replace(artifact, UPS_REPAIR_VERSION)

    def _upload_fixed_replace(self, artifact: Path, expected_version: str) -> dict[str, Any]:
        self.goto_upload()
        chooser = self._page.query_selector('input[type="file"][name="pluginzip"]')
        if chooser is None:
            raise DeploymentError("REFUSED: the Upload Plugin file input was not found.")
        chooser.set_input_files(str(artifact), timeout=ACTION_TIMEOUT_MS)
        submit = self._page.query_selector("#install-plugin-submit")
        if submit is None:
            raise DeploymentError("REFUSED: the Install Now control was not found.")
        assert_admin_url(self.url)
        submit.click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_STATE_TIMEOUT_MS)
        assert_admin_url(self.url)
        return self._confirm_and_overwrite(expected_version)

    def _comparison_cells(self, table: Any, label_pattern: str) -> str:
        """Return the 'Uploaded' cell for one labelled comparison row."""
        wanted = re.compile(label_pattern, re.IGNORECASE)
        for row in table.query_selector_all("tr"):
            cells = row.query_selector_all("td")
            if len(cells) < 2:
                continue
            if wanted.fullmatch(str(cells[0].inner_text() or "").strip()):
                return str(cells[-1].inner_text() or "").strip()
        return ""

    def _confirm_and_overwrite(self, expected_version: str) -> dict[str, Any]:
        """Verify the comparison screen is the fixed plugin at the fixed version."""
        if expected_version not in (ARTIFACT_VERSION, UPS_REPAIR_VERSION):
            raise DeploymentError("Internal: a non-fixed replacement version was requested.")
        tables = self._page.query_selector_all("table.update-from-upload-comparison")
        if len(tables) != 1:
            raise DeploymentError(
                "REFUSED: WordPress did not present exactly one replace-current comparison "
                "table. Nothing was replaced."
            )
        table = tables[0]
        uploaded_name = self._comparison_cells(table, r"(plugin\s+)?name")
        uploaded_version = self._comparison_cells(table, r"version")
        if uploaded_name != PLUGIN_NAME:
            raise DeploymentError(
                "REFUSED: the comparison screen does not identify the fixed FRP Depot plugin. "
                "Nothing was replaced."
            )
        if uploaded_version == WITHDRAWN_VERSION:
            raise DeploymentError(
                "REFUSED: the comparison screen offers the withdrawn 1.0.0. Nothing was replaced."
            )
        if uploaded_version != expected_version:
            raise DeploymentError(
                f"REFUSED: the comparison screen offers version {uploaded_version!r}, not "
                f"{expected_version}. Nothing was replaced."
            )
        links = self._page.query_selector_all("a.update-from-upload-overwrite")
        if len(links) != 1:
            raise DeploymentError(
                "REFUSED: the replace-current control is missing or ambiguous. "
                "Nothing was replaced."
            )
        assert_admin_url(self.url)
        links[0].click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_STATE_TIMEOUT_MS)
        assert_admin_url(self.url)
        return {"comparison_name": uploaded_name, "comparison_uploaded_version": uploaded_version}


class StepRecorder:
    """Runs each anonymous sub-step under its fixed name and times it.

    Every escaping exception becomes a ValidationStepError carrying the step name
    and the exception CLASS. A refusal code survives only if it is one of the
    fixed VALIDATION_CODES, so a foreign exception can never smuggle page text,
    an attribute value or a URL into a receipt.
    """

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def run(self, step: str, action: Callable[[], Any]) -> Any:
        if step not in VALIDATION_STEPS:
            raise DeploymentError("Internal: an unknown validation step name was requested.")
        started = time.monotonic()
        try:
            value = action()
        except ValidationStepError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised, attributed, never swallowed
            self._record(step, False, started)
            code = getattr(exc, "code", None)
            raise ValidationStepError(
                step,
                type(exc).__name__,
                code if isinstance(code, str) and code in VALIDATION_CODES else None,
            ) from exc
        self._record(step, True, started)
        return value

    def _record(self, step: str, ok: bool, started: float) -> None:
        self.steps.append({
            "step": step,
            "ok": bool(ok),
            "ms": int((time.monotonic() - started) * 1000),
        })


class PublicPage:
    """Anonymous storefront reader. Selects variations and reads booleans only.

    `allowed_paths` narrows navigation further than the module allowlist; the
    preflight rehearsal uses it so a cart or a checkout is unreachable even by a
    coding mistake, not merely unvisited.
    """

    def __init__(self, page: Any, allowed_paths: frozenset[str] = ALLOWED_PUBLIC_PATHS):
        self._page = page
        self._allowed = allowed_paths

    @property
    def url(self) -> str:
        return str(self._page.url)

    def _goto(self, url: str) -> None:
        assert_public_url(url, self._allowed)
        self._page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        assert_public_url(self.url, self._allowed)

    def _body_text(self) -> str:
        """Rendered visible text. Held in memory for counting only; never logged."""
        return str(self._page.inner_text("body", timeout=ACTION_TIMEOUT_MS) or "")

    def _page_health(self) -> dict[str, Any]:
        text = self._body_text()
        lowered = text.casefold()
        return {
            "blank": len(text.strip()) < MIN_RENDERED_TEXT,
            "fatal": any(marker in lowered for marker in FATAL_MARKERS),
            "exact_message_count": text.count(EXACT_MESSAGE),
        }

    def check_page_renders(self, url: str) -> dict[str, Any]:
        self._goto(url)
        health = self._page_health()
        return {"url": url, "blank": health["blank"], "fatal": health["fatal"]}

    def load_healthy_page(self, url: str) -> dict[str, Any]:
        """Load a page and REFUSE it if it is fatal or blank.

        A partially rendered page is not a pass. This is the difference between
        `check_page_renders` (report the booleans, let the caller judge) and the
        steps that must fail closed.
        """
        found = self.check_page_renders(url)
        if found["fatal"]:
            raise ValidationRefusal("page_fatal")
        if found["blank"]:
            raise ValidationRefusal("page_blank")
        return found

    # -- variations ---------------------------------------------------------
    def variations_form(self) -> Any:
        forms = self._page.query_selector_all(VARIATION_FORM_SELECTOR)
        if len(forms) == 0:
            raise ValidationRefusal("variations_form_missing")
        if len(forms) > 1:
            raise ValidationRefusal("variations_form_ambiguous")
        return forms[0]

    @staticmethod
    def _normalise(text: str) -> str:
        return re.sub(r"[\s\u00a0]+", " ", str(text or "")).strip().casefold()

    def _attribute_scope(self, form: Any, label: str) -> Any:
        """The one row whose leading label is EXACTLY the fixed attribute name."""
        wanted = self._normalise(label)
        found = []
        for row in form.query_selector_all(ATTRIBUTE_ROW_SELECTOR):
            heads = row.query_selector_all(ATTRIBUTE_LABEL_SELECTOR)
            if not heads:
                continue
            if self._normalise(heads[0].inner_text()) == wanted:
                found.append(row)
        if not found:
            raise ValidationRefusal("attribute_row_missing")
        if len(found) > 1:
            raise ValidationRefusal("attribute_row_ambiguous")
        return found[0]

    def _backing_select(self, scope: Any) -> Any:
        selects = scope.query_selector_all(BACKING_SELECT_SELECTOR)
        if not selects:
            raise ValidationRefusal("backing_select_missing")
        if len(selects) > 1:
            raise ValidationRefusal("backing_select_ambiguous")
        return selects[0]

    def _option_value(self, select: Any, value: str) -> str:
        """Resolve the fixed display value to the ONE option value that means it."""
        wanted = self._normalise(value)
        matches: set[str] = set()
        for option in select.query_selector_all("option"):
            attribute = str(option.get_attribute("value") or "")
            if not attribute:
                continue
            if (self._normalise(option.inner_text()) == wanted
                    or self._normalise(attribute) == wanted):
                matches.add(attribute)
        if not matches:
            raise ValidationRefusal("option_missing")
        if len(matches) > 1:
            raise ValidationRefusal("option_ambiguous")
        return matches.pop()

    def _role_radio(self, scope: Any, value: str) -> Any:
        """The one visible customer control inside the fixed attribute row.

        This is the element a person actually clicks on the live page: a visible
        `<li role="radio" data-value="...">`. Candidates are gathered with a fixed,
        value-free selector and then filtered in PYTHON by an EXACT data-value
        match -- the required values contain a double quote (1/2") and a slash, and
        interpolating them into a selector would be both fragile and a way to smuggle
        data into a query.

        Returns None ONLY when the row exposes no role-radio control at all, which
        is the single case where the backing-select fallback is honest. If the row
        DOES offer role-radio controls but not this exact value, that is a refusal:
        the previous build's quiet fallback is exactly how three attributes ended up
        selected by a route WooCommerce ignores.
        """
        candidates = scope.query_selector_all(ROLE_RADIO_SELECTOR)
        if not candidates:
            return None
        matches = [item for item in candidates
                   if str(item.get_attribute(ROLE_RADIO_VALUE_ATTRIBUTE) or "") == str(value)]
        if not matches:
            raise ValidationRefusal("role_radio_value_missing")
        if len(matches) > 1:
            raise ValidationRefusal("role_radio_ambiguous")
        control = matches[0]
        if not control.is_visible():
            raise ValidationRefusal("role_radio_not_visible")
        if _marked_disabled(control) or "disabled" in _class_tokens(control):
            raise ValidationRefusal("role_radio_disabled")
        return control

    def _require_readback(self, select: Any, option_value: str) -> None:
        """The backing value must read back EXACTLY, whichever control was used.

        A radio click reaches the backing select through the theme's own script,
        so polling is the honest way to observe it. The poll is bounded and a
        mismatch at the deadline is a failure, never a shrug.
        """
        deadline = time.monotonic() + (READBACK_TIMEOUT_MS / 1000)
        while True:
            if str(select.input_value(timeout=ACTION_TIMEOUT_MS) or "") == option_value:
                return
            if time.monotonic() >= deadline:
                raise ValidationRefusal("selection_readback_mismatch")
            time.sleep(READBACK_POLL_SECONDS)

    def select_attribute(self, form: Any, label: str, value: str) -> str:
        """Choose one fixed attribute value. Returns the control method used.

        The order is deliberate and it is the point of this repair. The live page
        renders a VISIBLE `<li role="radio">` per option over ONE HIDDEN BACKING
        SELECT per row, and only clicking the visible control runs the theme's own
        handlers -- which is what makes WooCommerce resolve a variation. So the
        visible control is used whenever the row has one, and the forced backing
        select survives only for a row that offers no visible control at all.

        Either way the backing value must read back exactly, so forcing never means
        guessing -- but note honestly that a readback proves only that the SELECT
        agrees. Whether WooCommerce agrees the variation is purchasable is a
        different question, asked by `require_variation_ready`.
        """
        scope = self._attribute_scope(form, label)
        select = self._backing_select(scope)
        option_value = self._option_value(select, value)
        control = self._role_radio(scope, value)
        if control is not None:
            control.click(timeout=ACTION_TIMEOUT_MS)
            method = "visible_role_radio"
        else:
            select.select_option(option_value, force=True, timeout=ACTION_TIMEOUT_MS)
            method = "backing_select"
        self._require_readback(select, option_value)
        return method

    def select_required_variation(self, form: Any, recorder: StepRecorder) -> dict[str, str]:
        """Select all three fixed attributes, one named step each."""
        methods: dict[str, str] = {}
        for (label, value), step in zip(REQUIRED_VARIATION, SELECT_STEPS):
            methods[label] = recorder.run(
                step,
                lambda label=label, value=value: self.select_attribute(form, label, value),
            )
        return methods

    # -- readiness ----------------------------------------------------------
    def _add_to_cart_button(self, form: Any) -> Any:
        """The one Add to cart button. Refuses with a fixed code, never page text."""
        buttons = form.query_selector_all(ADD_TO_CART_SELECTOR)
        if not buttons:
            raise ValidationRefusal("add_to_cart_missing")
        if len(buttons) > 1:
            raise ValidationRefusal("add_to_cart_ambiguous")
        return buttons[0]

    @staticmethod
    def _add_to_cart_blocked(button: Any) -> str | None:
        if not button.is_visible():
            return "add_to_cart_not_visible"
        if button.is_disabled() or _marked_disabled(button):
            return "add_to_cart_disabled"
        if DISABLED_CLASS_TOKENS & _class_tokens(button):
            return "add_to_cart_disabled"
        return None

    def _readiness_gap(self) -> str | None:
        """One read-only pass. Returns the first fixed code that is not yet met.

        Everything is re-resolved from the page each pass, because readiness is
        precisely the thing that changes under us after the last click.
        """
        form = self.variations_form()
        controls = form.query_selector_all(VARIATION_ID_SELECTOR)
        if not controls:
            return "variation_id_missing"
        if len(controls) > 1:
            return "variation_id_ambiguous"
        # The id itself is WooCommerce's own product data. Only whether it resolved
        # is ever recorded, never the value.
        raw = str(controls[0].input_value(timeout=ACTION_TIMEOUT_MS) or "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            return "variation_unresolved"
        # Counted here rather than through _add_to_cart_button so that EVERY
        # readiness condition is polled the same way: a button that is late to
        # render gets the same bounded patience as one that is late to enable.
        buttons = form.query_selector_all(ADD_TO_CART_SELECTOR)
        if not buttons:
            return "add_to_cart_missing"
        if len(buttons) > 1:
            return "add_to_cart_ambiguous"
        return self._add_to_cart_blocked(buttons[0])

    def require_variation_ready(self) -> dict[str, bool]:
        """Poll until WOOCOMMERCE says the chosen variation is purchasable.

        This is the gate whose absence let the 2026-08-09 activation reach a
        disabled button. It is entirely read-only: it clicks nothing, adds nothing
        to a cart and navigates nowhere. A deadline reached without readiness is a
        refusal carrying one fixed code -- never a pass, never page text.
        """
        deadline = time.monotonic() + (READINESS_TIMEOUT_MS / 1000)
        while True:
            gap = self._readiness_gap()
            if gap is None:
                return {"variation_resolved": True, "add_to_cart_enabled": True}
            if time.monotonic() >= deadline:
                raise ValidationRefusal(gap)
            time.sleep(READINESS_POLL_SECONDS)

    # -- cart / checkout ----------------------------------------------------
    def add_selected_to_cart(self) -> dict[str, Any]:
        """Click Add to cart. Only ever reached after `require_variation_ready`.

        The button is re-resolved and re-checked here rather than trusted from the
        readiness pass: between the two, the page is still live.
        """
        form = self.variations_form()
        button = self._add_to_cart_button(form)
        blocked = self._add_to_cart_blocked(button)
        if blocked is not None:
            raise ValidationRefusal(blocked)
        button.click(timeout=ACTION_TIMEOUT_MS)
        self._page.wait_for_load_state("domcontentloaded", timeout=LOAD_STATE_TIMEOUT_MS)
        assert_public_url(self.url, self._allowed)
        health = self._page_health()
        if health["fatal"] or health["blank"]:
            raise ValidationRefusal("add_to_cart_page_unhealthy")
        return {"added": True}

    def goto_checkout(self) -> dict[str, Any]:
        self._goto(CHECKOUT_URL)
        return {"url": CHECKOUT_URL}

    def read_checkout(self) -> dict[str, Any]:
        health = self._page_health()
        checkout_form = self._count_any((
            "form.checkout", "form.woocommerce-checkout", "form[name='checkout']",
            ".wc-block-checkout__form",
        ))
        payment_form = self._count_any((
            "#payment", ".wc-block-checkout__payment-method", "#place_order",
            "button[name='woocommerce_checkout_place_order']",
        ))
        return {
            "exact_message_count": health["exact_message_count"],
            "checkout_form_present": checkout_form > 0,
            "payment_form_present": payment_form > 0,
            "blank": health["blank"],
            "fatal": health["fatal"],
        }

    def _count_any(self, selectors: tuple[str, ...]) -> int:
        return sum(len(self._page.query_selector_all(selector)) for selector in selectors)


# ---------------------------------------------------------------------------
# Sessions. Patched wholesale in tests; no network in the test suite.
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def admin_session() -> Iterator[AdminPage]:
    """Attach to Rachad's already-authenticated loopback CDP session.

    This never launches a browser profile and never signs in. If the window is
    closed or has left the admin origin, it refuses rather than recovering.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    # Belt and braces: the commands are decorated, but any future caller that
    # reaches for the admin browser directly is serialized too. Re-entrant, so
    # nesting inside a decorated command costs nothing.
    with ui_browser_lock("wordpress", purpose="WordPress admin session"), sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=15_000)
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            raise DeploymentError(
                "The authenticated FRP Depot WordPress window is not reachable on the loopback "
                "session. Ask Rachad to run CONNECT_DADO_WORDPRESS_UI.bat and keep it open."
            ) from exc
        contexts = browser.contexts
        if not contexts or not contexts[0].pages:
            raise DeploymentError("The authenticated WordPress window has no open page.")
        yield AdminPage(contexts[0].pages[0])


@contextlib.contextmanager
def anonymous_session(
    allowed_paths: frozenset[str] = ALLOWED_PUBLIC_PATHS,
) -> Iterator[PublicPage]:
    """A throwaway headless Edge with no persistent profile and no stored state.

    Deliberately NOT the admin session and deliberately NOT a persistent context:
    it must carry no admin cookie so it sees what a customer sees. A fresh context
    is created per call, so a preflight and a later validation never share state.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            context = browser.new_context()
            context.set_default_timeout(ACTION_TIMEOUT_MS)
            context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            try:
                yield PublicPage(context.new_page(), allowed_paths)
            finally:
                context.close()
        finally:
            browser.close()


# ---------------------------------------------------------------------------
# Preflight evidence
#
# A read-only rehearsal of the anonymous half of the activation validation, run
# BEFORE anything is activated. `stage-activate` refuses without one that passed,
# names this exact site/product/variation/tool version, and is recent.
#
# Since 1.2.0 one invocation is THREE consecutive rehearsals, each in its own
# brand-new anonymous context. All three must pass. The freshness clock starts when
# the THIRD pass finishes, which is when `created_utc` is stamped -- so a 30-minute
# window always measures from the end of the evidence, never from its start.
# ---------------------------------------------------------------------------
PREFLIGHT_KIND = "wordpress_plugin_preflight_validation"
PREFLIGHT_MAX_AGE_MINUTES = 30
PREFLIGHT_CLOCK_SKEW_MINUTES = 2
PREFLIGHT_RUNS = 3

PREFLIGHT_KEYS = frozenset({
    "schema_version", "tool", "tool_version", "kind", "origin", "product_url",
    "created_utc", "expires_utc", "max_age_minutes", "required_runs", "anonymous",
    "persistent_profile", "admin_session_used", "variation", "runs", "passed",
    "add_to_cart_clicked", "cart_visited", "checkout_visited", "business_write_performed",
})
PREFLIGHT_RUN_KEYS = frozenset({
    "run", "passed", "steps", "selection_method", "variation_resolved",
    "add_to_cart_enabled", "add_to_cart_clicked", "cart_visited", "checkout_visited",
    "admin_session_used", "persistent_profile", "business_write_performed",
})
PREFLIGHT_RUN_FALSE_FLAGS = ("add_to_cart_clicked", "cart_visited", "checkout_visited",
                             "admin_session_used", "persistent_profile",
                             "business_write_performed")
PREFLIGHT_STEP_KEYS = frozenset({"step", "ok", "ms"})

REQUIRED_VARIATION_LABELS = tuple(label for label, _ in REQUIRED_VARIATION)
REQUIRED_VARIATION_TEXT = [f"{label}={value}" for label, value in REQUIRED_VARIATION]


def preflight_path_of(digest: str, created: datetime) -> Path:
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    return PREFLIGHT_DIR / f"{stamp}_preflight_{digest[:16]}.json"


def project_run(index: int, steps: list[dict[str, Any]], methods: dict[str, str],
                ready: dict[str, bool] | None) -> dict[str, Any]:
    """One rehearsal, as the closed projection that may be persisted.

    `ready` is the readiness step's own return value, or None if the run never got
    that far. Nothing else from the page survives into this record: no variation id,
    no page text, no HTML, no URL beyond the fixed ones already in the envelope.
    """
    passed = bool(ready) and all(record["ok"] for record in steps)
    return {
        "run": int(index),
        "passed": passed,
        "steps": list(steps),
        "selection_method": dict(methods),
        "variation_resolved": bool(ready and ready.get("variation_resolved")),
        "add_to_cart_enabled": bool(ready and ready.get("add_to_cart_enabled")),
        "add_to_cart_clicked": False,
        "cart_visited": False,
        "checkout_visited": False,
        "admin_session_used": False,
        "persistent_profile": False,
        "business_write_performed": False,
    }


def write_preflight(runs: list[dict[str, Any]], passed: bool) -> tuple[Path, dict[str, Any]]:
    """Persist the rehearsal result. Booleans, labels, timings and method only."""
    created = utc_now()
    core = {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "kind": PREFLIGHT_KIND,
        "origin": EXACT_ORIGIN,
        "product_url": PRODUCT_URL,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(minutes=PREFLIGHT_MAX_AGE_MINUTES)).isoformat(),
        "max_age_minutes": PREFLIGHT_MAX_AGE_MINUTES,
        "required_runs": PREFLIGHT_RUNS,
        "anonymous": True,
        "persistent_profile": False,
        "admin_session_used": False,
        "variation": list(REQUIRED_VARIATION_TEXT),
        "runs": list(runs),
        "passed": bool(passed),
        "add_to_cart_clicked": False,
        "cart_visited": False,
        "checkout_visited": False,
        "business_write_performed": False,
    }
    digest = digest_for(core)
    evidence = {**core, "sha256": digest}
    PREFLIGHT_DIR.mkdir(parents=True, exist_ok=True)
    path = preflight_path_of(digest, created)
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_receipt("wordpress_plugin_preflight_recorded",
                   f"passed={bool(passed)}; runs={len(runs)}/{PREFLIGHT_RUNS}; {path}")
    return path, evidence


def resolve_preflight_path(raw: str) -> Path:
    evidence_path = Path(raw).resolve()
    if PREFLIGHT_DIR.resolve() not in evidence_path.parents:
        raise DeploymentError(
            "The preflight evidence must be inside Dado's WordPress preflight folder."
        )
    return evidence_path


def _check_preflight_run(run: Any, index: int) -> None:
    """One rehearsal record must be the closed projection, in place, and passing.

    Ordering is checked against the record's OWN declared run number, so three
    genuine runs cannot be shuffled to hide which one failed.
    """
    if not isinstance(run, dict) or set(run) != PREFLIGHT_RUN_KEYS:
        raise DeploymentError("A preflight run record is not the closed projection.")
    if run["run"] != index:
        raise DeploymentError(
            f"Preflight run records are not the fixed ordered sequence 1..{PREFLIGHT_RUNS}."
        )
    if run["passed"] is not True:
        raise DeploymentError(
            f"Preflight run {index} did not pass. All {PREFLIGHT_RUNS} rehearsals must "
            "pass; one failure makes the whole evidence unusable."
        )
    if run["variation_resolved"] is not True:
        raise DeploymentError(
            f"Preflight run {index} does not record WooCommerce resolving the variation."
        )
    if run["add_to_cart_enabled"] is not True:
        raise DeploymentError(
            f"Preflight run {index} does not record Add to cart becoming enabled."
        )
    for flag in PREFLIGHT_RUN_FALSE_FLAGS:
        if run[flag] is not False:
            raise DeploymentError(f"Preflight run {index} records {flag}; it must be false.")

    steps = run["steps"]
    if not isinstance(steps, list) or len(steps) != len(PREFLIGHT_STEPS):
        raise DeploymentError(f"Preflight run {index} does not record the fixed step list.")
    for record, expected in zip(steps, PREFLIGHT_STEPS):
        if not isinstance(record, dict) or set(record) != PREFLIGHT_STEP_KEYS:
            raise DeploymentError("A preflight step record is not the closed projection.")
        if record["step"] != expected or record["ok"] is not True:
            raise DeploymentError(
                f"Preflight run {index} does not show every fixed step passing in order."
            )
        if not isinstance(record["ms"], int) or record["ms"] < 0:
            raise DeploymentError("A preflight step record carries an invalid timing.")

    methods = run["selection_method"]
    if not isinstance(methods, dict) or tuple(methods) != REQUIRED_VARIATION_LABELS:
        raise DeploymentError(
            f"Preflight run {index} does not record a control method for each fixed attribute."
        )
    if any(method not in SELECTION_METHODS for method in methods.values()):
        raise DeploymentError(f"Preflight run {index} records an unknown selection method.")


def load_preflight(path: Path | str, *,
                   max_age_minutes: int = PREFLIGHT_MAX_AGE_MINUTES) -> dict[str, Any]:
    """Load, hash-check, identity-check and freshness-check one evidence file."""
    evidence_path = resolve_preflight_path(str(path))
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError(f"Preflight evidence is unreadable: {evidence_path}") from exc
    if not isinstance(evidence, dict):
        raise DeploymentError("Preflight evidence must contain one object.")
    saved = str(evidence.pop("sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(evidence)):
        raise DeploymentError(
            "Preflight evidence hash check failed. The evidence changed after it was recorded."
        )
    if set(evidence) != PREFLIGHT_KEYS:
        raise DeploymentError("Preflight evidence schema is not the exact closed set of fields.")
    if (evidence["schema_version"] != PREFLIGHT_SCHEMA_VERSION
            or evidence["tool"] != TOOL_NAME
            or evidence["tool_version"] != TOOL_VERSION
            or evidence["kind"] != PREFLIGHT_KIND):
        raise DeploymentError(
            "Preflight evidence was not produced by this exact tool version. Run "
            "preflight-validation again."
        )
    if evidence["origin"] != EXACT_ORIGIN or evidence["product_url"] != PRODUCT_URL:
        raise DeploymentError("Preflight evidence is not for the fixed site and product page.")
    if evidence["variation"] != REQUIRED_VARIATION_TEXT:
        raise DeploymentError("Preflight evidence is not for the fixed required variation.")
    if evidence["max_age_minutes"] != PREFLIGHT_MAX_AGE_MINUTES:
        raise DeploymentError("Preflight evidence declares a different maximum age.")
    if evidence["required_runs"] != PREFLIGHT_RUNS:
        raise DeploymentError(
            f"Preflight evidence does not require the fixed {PREFLIGHT_RUNS} rehearsals."
        )
    if evidence["anonymous"] is not True:
        raise DeploymentError("Preflight evidence does not record an anonymous session.")
    for flag in ("persistent_profile", "admin_session_used", "add_to_cart_clicked",
                 "cart_visited", "checkout_visited", "business_write_performed"):
        if evidence[flag] is not False:
            raise DeploymentError(f"Preflight evidence records {flag}; it must be false.")
    if evidence["passed"] is not True:
        raise DeploymentError(
            "The preflight did not pass. Nothing may be staged on a failed rehearsal."
        )

    runs = evidence["runs"]
    if not isinstance(runs, list) or len(runs) != PREFLIGHT_RUNS:
        raise DeploymentError(
            f"Preflight evidence does not record exactly {PREFLIGHT_RUNS} fresh-browser "
            "rehearsals. One short is not evidence of a habit; one extra is not the "
            "approved shape."
        )
    for index, run in enumerate(runs, start=1):
        _check_preflight_run(run, index)

    try:
        created = datetime.fromisoformat(str(evidence["created_utc"]))
        expires = datetime.fromisoformat(str(evidence["expires_utc"]))
    except (TypeError, ValueError) as exc:
        raise DeploymentError("Preflight evidence carries an invalid timestamp.") from exc
    if expires != created + timedelta(minutes=PREFLIGHT_MAX_AGE_MINUTES):
        raise DeploymentError("Preflight evidence expiry does not match its own creation time.")
    now = utc_now()
    if created > now + timedelta(minutes=PREFLIGHT_CLOCK_SKEW_MINUTES):
        raise DeploymentError("Preflight evidence is timestamped in the future.")
    if now - created > timedelta(minutes=max_age_minutes):
        raise DeploymentError(
            f"Preflight evidence is older than {max_age_minutes} minutes. Run "
            "preflight-validation again so the judgement is made on the site as it is now."
        )
    evidence["sha256"] = saved
    return evidence


def _preflight_single_run(index: int) -> tuple[dict[str, Any], ValidationStepError | None]:
    """ONE read-only rehearsal, in its own brand-new anonymous context.

    Deliberately stops the moment readiness is proven: no add-to-cart click, no
    cart, no checkout, no admin session and no write of any kind. The narrowed
    PREFLIGHT_PUBLIC_PATHS makes the cart and checkout unreachable rather than
    merely unvisited, and `anonymous_session` builds a fresh context per call, so
    two runs share no cookie and no storage.
    """
    recorder = StepRecorder()
    methods: dict[str, str] = {}
    ready: dict[str, bool] | None = None
    failure: ValidationStepError | None = None
    try:
        with anonymous_session(PREFLIGHT_PUBLIC_PATHS) as public:
            recorder.run("home_load", lambda: public.load_healthy_page(HOME_URL))
            recorder.run("product_load", lambda: public.load_healthy_page(PRODUCT_URL))
            form = recorder.run("variation_form", public.variations_form)
            methods = public.select_required_variation(form, recorder)
            ready = recorder.run("variation_ready", public.require_variation_ready)
    except ValidationStepError as exc:
        failure = exc
    return project_run(index, recorder.steps, methods, ready), failure


def _preflight_probe() -> tuple[list[dict[str, Any]], int | None, ValidationStepError | None]:
    """Exactly THREE consecutive rehearsals, each on a fresh anonymous context.

    Stops at the FIRST failure and records it as the last run. Continuing would
    only produce a longer file that still cannot be staged on, and it would leave
    the honest question -- "did the site behave three times running?" -- answered
    by a mixture instead of a yes.
    """
    runs: list[dict[str, Any]] = []
    for index in range(1, PREFLIGHT_RUNS + 1):
        run, failure = _preflight_single_run(index)
        runs.append(run)
        if failure is not None or not run["passed"]:
            return runs, index, failure
    return runs, None, None


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------
VALIDATION_CONTRACT = {
    "anonymous": True,
    "persistent_profile": False,
    "product_url": PRODUCT_URL,
    "variation": list(REQUIRED_VARIATION_TEXT),
    "exact_message": EXACT_MESSAGE,
    "required_exact_message_count": 1,
    "require_checkout_blocked": True,
    "require_no_payment_form": True,
    "require_no_fatal_or_blank": True,
    "order_placed": False,
    "customer_data_entered": False,
    "ups_setting_touched": False,
    "steps": list(VALIDATION_STEPS),
    "preflight_required": True,
    "preflight_max_age_minutes": PREFLIGHT_MAX_AGE_MINUTES,
    "preflight_runs_required": PREFLIGHT_RUNS,
    "preflight_schema_version": PREFLIGHT_SCHEMA_VERSION,
    "variation_ready_required": True,
    "customer_control": "visible role-radio option, exact data-value match",
    "timeout_is_failure": True,
    "failure_attribution": "fixed step name plus exception class, never exception text",
    "on_any_failure": "emergency deactivate the fixed plugin once, verify inactive, "
                      "verify homepage and cart recovery, close the plan permanently",
}

UPS_REPAIR_VALIDATION_CONTRACT = {
    "artifact_relation": "exact deployed 2.0.4 baseline plus two version tokens, "
                         "disclosed readme and one fixed hidden-panel CSS rule only; "
                         "allowlist and JavaScript byte-identical",
    "allowlisted_variations": 64,
    "researched_candidate_groups": 30,
    "physically_verified_groups": 0,
    "measurement_status": UPS_REPAIR_MEASUREMENT_STATUS,
    "oversized_variations_still_quote_only": 14,
    "fnpt_variations_still_quote_only": 60,
    "freight_class_still_overrides_allowlist": True,
    "unknown_mixed_custom_customer_specific_still_quote_only": True,
    "creates_shipping_rate": False,
    "uses_existing_woocommerce_ups_method": True,
    "product_price_stock_shipping_class_weight_dimensions_touched": False,
    "quote_form_transaction_triggered": False,
    "email_order_payment_created": False,
    "automatic_rollback": False,
    "one_upload_attempt": True,
    "post_write_plugin_row_required": "version 2.0.5 and active",
    "post_commit_public_checks": [
        "one allowlisted small elbow/stub selection keeps direct checkout",
        "one oversized selection remains freight quote",
        "one incomplete FNPT selection remains freight quote",
        "pipe remains freight quote",
        "one mixed cart remains freight quote",
        "ordinary eligible cart reaches existing UPS rate path",
    ],
    "on_upload_or_row_failure": "lock plan indeterminate with no retry and no rollback",
}

PLAN_KEYS = frozenset({
    "schema_version", "tool", "origin", "action", "created_utc", "expires_utc", "nonce",
    "plugin_name", "plugin_slug", "plugin_file", "artifact", "before", "after_expected",
    "validation", "preflight",
})
PLAN_PREFLIGHT_KEYS = frozenset({"path", "sha256", "created_utc", "runs"})


def lock_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".commit-lock.json")


def result_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".result.json")


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise DeploymentError(
            "This plan has already entered commit and cannot be replayed."
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def stage_plan(action: str, before: dict[str, Any], after_expected: dict[str, Any],
               artifact: dict[str, Any] | None,
               preflight: dict[str, Any] | None) -> Path:
    if action not in ACTIONS:
        raise DeploymentError("Unsupported action.")
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "origin": EXACT_ORIGIN,
        "action": action,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "plugin_name": PLUGIN_NAME,
        "plugin_slug": PLUGIN_SLUG,
        "plugin_file": PLUGIN_FILE,
        "artifact": artifact,
        "before": before,
        "after_expected": after_expected,
        "validation": (
            dict(VALIDATION_CONTRACT) if action == "plugin_activate"
            else dict(UPS_REPAIR_VALIDATION_CONTRACT) if action == "plugin_ups_repair"
            else None
        ),
        "preflight": dict(preflight) if action == "plugin_activate" else None,
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{action}_{digest[:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_receipt("wordpress_plugin_plan_staged", str(path))
    return path


def load_plan(path: str) -> dict[str, Any]:
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError(f"Plan JSON is unreadable: {path}") from exc
    if not isinstance(plan, dict):
        raise DeploymentError("A plan must contain one object.")
    saved = str(plan.pop("sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(plan)):
        raise DeploymentError("Plan hash check failed. The plan changed after review.")
    if set(plan) != PLAN_KEYS:
        raise DeploymentError(
            "Plan schema is not the exact closed set of fields. A plan staged before the "
            "preflight contract existed can never be committed; stage a new one."
        )
    if (plan["schema_version"] != SCHEMA_VERSION or plan["tool"] != TOOL_NAME
            or plan["origin"] != EXACT_ORIGIN):
        raise DeploymentError("The plan schema, tool, or origin is invalid.")
    if (plan["plugin_name"] != PLUGIN_NAME or plan["plugin_slug"] != PLUGIN_SLUG
            or plan["plugin_file"] != PLUGIN_FILE):
        raise DeploymentError("The plan does not name the fixed FRP Depot plugin.")
    action = str(plan["action"])
    if action not in ACTIONS:
        raise DeploymentError("The plan action is not allowlisted.")
    try:
        expires = datetime.fromisoformat(str(plan["expires_utc"]))
    except (TypeError, ValueError) as exc:
        raise DeploymentError("Plan expiry is invalid.") from exc
    if utc_now() >= expires:
        raise DeploymentError("Plan expired. Stage a new plan for review.")

    for label in ("before", "after_expected"):
        state = plan[label]
        if not isinstance(state, dict) or set(state) != {
            "present", "active", "version", "update_marker", "plugin_file", "fingerprint"
        }:
            raise DeploymentError(f"Plan {label} state is not the closed projection.")
        if state["plugin_file"] != PLUGIN_FILE:
            raise DeploymentError(f"Plan {label} state names a different plugin file.")
        if row_fingerprint(state) != state["fingerprint"]:
            raise DeploymentError(f"Plan {label} fingerprint does not match its own values.")
        if state["version"] == WITHDRAWN_VERSION and label == "after_expected":
            raise DeploymentError("REFUSED: a plan may never expect the withdrawn 1.0.0.")

    artifact = plan["artifact"]
    if action == "plugin_replace":
        if not isinstance(artifact, dict) or set(artifact) != {
            "path", "sha256", "version", "members", "bytes"
        }:
            raise DeploymentError("A replace plan must carry the closed artifact record.")
        if artifact["sha256"] == WITHDRAWN_SHA256 or artifact["version"] == WITHDRAWN_VERSION:
            raise DeploymentError(
                "REFUSED: this plan names the withdrawn 1.0.0 artifact. It can never be installed."
            )
        if (artifact["sha256"] != ARTIFACT_SHA256 or artifact["version"] != ARTIFACT_VERSION
                or Path(artifact["path"]).resolve() != Path(ARTIFACT_PATH).resolve()
                or tuple(artifact["members"]) != tuple(sorted(ARTIFACT_MEMBERS))):
            raise DeploymentError("REFUSED: the plan artifact is not the approved 1.0.1 artifact.")
        expected = _expected_after("plugin_replace", plan["before"])
    elif action == "plugin_ups_repair":
        if not isinstance(artifact, dict) or set(artifact) != {
            "path", "sha256", "version", "members", "bytes"
        }:
            raise DeploymentError("A UPS repair plan must carry the closed artifact record.")
        if (artifact["sha256"] != UPS_REPAIR_SHA256
                or artifact["version"] != UPS_REPAIR_VERSION
                or Path(artifact["path"]).resolve() != Path(UPS_REPAIR_ARTIFACT_PATH).resolve()
                or tuple(artifact["members"]) != tuple(sorted(UPS_REPAIR_MEMBERS))):
            raise DeploymentError("REFUSED: the plan artifact is not the fixed UPS repair.")
        expected = _expected_after("plugin_ups_repair", plan["before"])
    elif artifact is not None:
        raise DeploymentError("Only a replace or UPS repair plan may carry an artifact.")
    else:
        expected = _expected_after(action, plan["before"])

    if plan["after_expected"] != expected:
        raise DeploymentError("The plan's expected end state is not the one this tool produces.")

    preflight = plan["preflight"]
    if action == "plugin_activate":
        if plan["validation"] != VALIDATION_CONTRACT:
            raise DeploymentError(
                "The plan's validation and rollback contract is not the current fixed contract."
            )
        if not isinstance(preflight, dict) or set(preflight) != PLAN_PREFLIGHT_KEYS:
            raise DeploymentError(
                "An activation plan must carry the closed preflight evidence record."
            )
        if (not isinstance(preflight["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", preflight["sha256"])):
            raise DeploymentError("The plan's preflight evidence hash is not a SHA-256 digest.")
        if preflight["runs"] != PREFLIGHT_RUNS:
            raise DeploymentError(
                f"An activation plan must be built on {PREFLIGHT_RUNS} passing rehearsals."
            )
        resolve_preflight_path(str(preflight["path"]))
    elif action == "plugin_ups_repair":
        if plan["validation"] != UPS_REPAIR_VALIDATION_CONTRACT:
            raise DeploymentError("The UPS repair plan disclosure/verification contract changed.")
        if preflight is not None:
            raise DeploymentError("A UPS repair plan cannot carry activation preflight evidence.")
    else:
        if plan["validation"] is not None:
            raise DeploymentError("Only activation and UPS repair plans may carry validation contracts.")
        if preflight is not None:
            raise DeploymentError("Only an activation plan may carry preflight evidence.")
    plan["sha256"] = saved
    return plan


def _expected_after(action: str, before: dict[str, Any]) -> dict[str, Any]:
    if action == "plugin_replace":
        return project_row(True, False, ARTIFACT_VERSION, False)
    if action == "plugin_ups_repair":
        return project_row(True, True, UPS_REPAIR_VERSION, False)
    if action == "plugin_activate":
        return project_row(True, True, ARTIFACT_VERSION, before["update_marker"])
    return project_row(True, False, ARTIFACT_VERSION, before["update_marker"])


def resolve_plan_path(raw: str) -> Path:
    plan_path = Path(raw).resolve()
    if PLAN_DIR.resolve() not in plan_path.parents:
        raise DeploymentError("Plan must be inside Dado's WordPress plugin-plan folder.")
    return plan_path


def record_result(plan_path: Path, plan: dict[str, Any], status: str,
                  detail: dict[str, Any]) -> None:
    payload = {
        "status": status,
        "action": plan["action"],
        "plan": str(plan_path),
        "plan_sha256": plan["sha256"],
        "plugin_file": PLUGIN_FILE,
        "recorded_utc": utc_now().isoformat(),
        **detail,
    }
    result_path(plan_path).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    append_receipt(f"wordpress_plugin_{status.casefold()}",
                   f"action={plan['action']}; plan={plan_path}; sha256={plan['sha256']}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def command_inspect(_: argparse.Namespace) -> None:
    with admin_session() as admin:
        admin.goto_plugins()
        row = admin.read_row()
    emit({
        "status": "INSPECTED",
        "site": EXACT_ORIGIN,
        "connected": True,
        "plugin_name": PLUGIN_NAME,
        **row,
        "withdrawn_version_installed": row["version"] == WITHDRAWN_VERSION,
        "approved_version": ARTIFACT_VERSION,
        "business_write_performed": False,
    })


def command_preflight_validation(_: argparse.Namespace) -> None:
    """Rehearse the read-only half of the activation validation THREE times.

    This is the answer to two production failures. The sub-steps that can time out
    are exercised, timed and named while the site is untouched; and the question
    that actually sank the last attempt -- does WooCommerce declare this variation
    purchasable? -- is now asked and answered before anything is staged, three times
    over, each in a fresh anonymous browser context.

    It clicks no Add to cart, creates no cart, visits no checkout, opens no admin
    session and changes no business data.
    """
    runs, failed_run, failure = _preflight_probe()
    passed = failed_run is None and len(runs) == PREFLIGHT_RUNS
    path, evidence = write_preflight(runs, passed=passed)
    emit({
        "status": "PREFLIGHT_PASSED" if passed else "PREFLIGHT_FAILED",
        "evidence": str(path),
        "evidence_sha256": evidence["sha256"],
        "created_utc": evidence["created_utc"],
        "expires_utc": evidence["expires_utc"],
        "max_age_minutes": PREFLIGHT_MAX_AGE_MINUTES,
        "freshness_starts": "after the final rehearsal completed",
        "required_runs": PREFLIGHT_RUNS,
        "runs_completed": len(runs),
        "product_url": PRODUCT_URL,
        "variation": list(REQUIRED_VARIATION_TEXT),
        "runs": runs,
        "failed_run": failed_run,
        "failed_step": failure.step if failure else None,
        "failed_exception_class": failure.exception_class if failure else None,
        "failed_code": failure.code if failure else None,
        "add_to_cart_clicked": False,
        "cart_visited": False,
        "checkout_visited": False,
        "admin_session_used": False,
        "business_write_performed": False,
    })
    if not passed:
        detail = (f" at step {failure.step} ({failure.exception_class}"
                  + (f"/{failure.code}" if failure.code else "") + ")") if failure else ""
        raise DeploymentError(
            f"Preflight run {failed_run} of {PREFLIGHT_RUNS} failed{detail}. All "
            f"{PREFLIGHT_RUNS} rehearsals must pass; nothing may be staged on this evidence."
        )


def _live_row() -> dict[str, Any]:
    with admin_session() as admin:
        admin.goto_plugins()
        return admin.read_row()


def _stage_and_report(action: str, before: dict[str, Any],
                      artifact: dict[str, Any] | None,
                      preflight: dict[str, Any] | None = None) -> None:
    after_expected = _expected_after(action, before)
    path = stage_plan(action, before, after_expected, artifact, preflight)
    plan = json.loads(path.read_text(encoding="utf-8"))
    emit({
        "status": "STAGED_NOT_COMMITTED",
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "action": action,
        "plugin_name": PLUGIN_NAME,
        "plugin_file": PLUGIN_FILE,
        "artifact": artifact,
        "before": before,
        "after_expected": after_expected,
        "validation": plan["validation"],
        "preflight": plan["preflight"],
        "approval": APPROVAL_WORD,
        "external_write_performed": False,
    })


def command_stage_replace(_: argparse.Namespace) -> None:
    artifact = verify_artifact()
    before = _live_row()
    if before["version"] == ARTIFACT_VERSION:
        raise DeploymentError(
            f"No change is needed: version {ARTIFACT_VERSION} is already installed. "
            "Nothing was staged."
        )
    if before["version"] != REPLACE_FROM_VERSION:
        raise DeploymentError(
            f"REFUSED: the installed version is {before['version']!r}, not the expected "
            f"{REPLACE_FROM_VERSION}. Nothing was staged."
        )
    if before["active"] is not False:
        raise DeploymentError(
            "REFUSED: the installed plugin is active. Replacing a live plugin is out of scope; "
            "it must be inactive first. Nothing was staged."
        )
    _stage_and_report("plugin_replace", before, artifact)


def command_stage_ups_repair(_: argparse.Namespace) -> None:
    artifact = verify_ups_repair_artifact()
    before = _live_row()
    if before["version"] == UPS_REPAIR_VERSION:
        raise DeploymentError(
            f"No change is needed: UPS repair version {UPS_REPAIR_VERSION} is already installed. "
            "Nothing was staged."
        )
    if before["version"] != UPS_REPAIR_FROM_VERSION:
        raise DeploymentError(
            f"REFUSED: the installed version is {before['version']!r}, not the exact frozen "
            f"baseline {UPS_REPAIR_FROM_VERSION}. Nothing was staged."
        )
    if before["active"] is not True:
        raise DeploymentError(
            f"REFUSED: the fixed {UPS_REPAIR_FROM_VERSION} plugin is not active. The UPS repair is an "
            "active-to-active replacement only; nothing was staged."
        )
    _stage_and_report("plugin_ups_repair", before, artifact)


def command_stage_activate(args: argparse.Namespace) -> None:
    # The preflight is checked FIRST: it is a local file read, so a missing,
    # stale, foreign or failed rehearsal refuses without even opening the admin
    # window.
    evidence_path = resolve_preflight_path(str(args.preflight))
    evidence = load_preflight(evidence_path)
    before = _live_row()
    if before["version"] == WITHDRAWN_VERSION:
        raise DeploymentError(
            "REFUSED: version 1.0.0 failed production validation and can never be activated. "
            "Replace it with 1.0.1 first."
        )
    if before["version"] != ARTIFACT_VERSION:
        raise DeploymentError(
            f"REFUSED: only version {ARTIFACT_VERSION} may be activated. Nothing was staged."
        )
    if before["active"] is not False:
        raise DeploymentError("No change is needed: the plugin is already active.")
    _stage_and_report("plugin_activate", before, None, {
        "path": str(evidence_path),
        "sha256": evidence["sha256"],
        "created_utc": evidence["created_utc"],
        "runs": len(evidence["runs"]),
    })


def command_stage_deactivate(_: argparse.Namespace) -> None:
    before = _live_row()
    if before["version"] != ARTIFACT_VERSION:
        raise DeploymentError(
            f"REFUSED: this tool only manages version {ARTIFACT_VERSION}. Nothing was staged."
        )
    if before["active"] is not True:
        raise DeploymentError("No change is needed: the plugin is already inactive.")
    _stage_and_report("plugin_deactivate", before, None)


def _open_commit(args: argparse.Namespace, action: str) -> tuple[Path, dict[str, Any]]:
    """Everything that must pass before a browser is touched."""
    plan_path = resolve_plan_path(args.plan)
    plan = load_plan(str(plan_path))
    if plan["action"] != action:
        raise DeploymentError(f"This plan is a {plan['action']} plan, not {action}.")
    require_rachad_approval(args.approval)
    if lock_path(plan_path).exists():
        raise DeploymentError("This plan has already entered commit and cannot be replayed.")
    return plan_path, plan


def _verify_plan_preflight(plan: dict[str, Any]) -> dict[str, Any]:
    """Re-check the approved evidence: same file, same hash, still fresh.

    Deliberately stricter than plan expiry. A plan lives 24 hours, but a
    rehearsal only speaks for the site as it was 30 minutes ago, and activation
    is judged against the site as it is NOW.
    """
    recorded = plan["preflight"]
    evidence = load_preflight(str(recorded["path"]))
    if not secrets.compare_digest(str(evidence["sha256"]), str(recorded["sha256"])):
        raise DeploymentError(
            "REFUSED: the preflight evidence changed after Rachad reviewed this plan. "
            "Nothing was written."
        )
    if str(evidence["created_utc"]) != str(recorded["created_utc"]):
        raise DeploymentError(
            "REFUSED: the preflight evidence is not the one recorded in this plan. "
            "Nothing was written."
        )
    return evidence


@holds_wordpress_browser("WordPress: replace the freight checkout-guard plugin")
def command_commit_replace(args: argparse.Namespace) -> None:
    plan_path, plan = _open_commit(args, "plugin_replace")
    artifact = verify_artifact(Path(plan["artifact"]["path"]))
    if artifact["sha256"] != plan["artifact"]["sha256"]:
        raise DeploymentError("REFUSED: the artifact on disk no longer matches the approved plan.")
    lock = lock_path(plan_path)

    # Read-only pre-state first: it can create no side effect, so a closed window
    # or a moved row refuses WITHOUT burning the plan. The exclusive lock is taken
    # immediately afterwards and strictly before the upload.
    with admin_session() as admin:
        _verify_pre_state(admin, plan)
        write_lock(lock, {
            "plan_sha256": plan["sha256"], "status": "in_flight",
            "started_utc": utc_now().isoformat(), "stage": "upload",
        }, exclusive=True)
        try:
            comparison = admin.upload_replace(Path(artifact["path"]))
            admin.goto_plugins()
            after = admin.read_row()
        except Exception as exc:
            _burn(lock, plan, plan_path, exc, "replace")
            raise IndeterminateError(
                "The plugin replacement is unverified. This plan is locked and will not retry. "
                "Check the Plugins screen in WordPress before staging anything new."
            ) from exc

    if after["version"] != ARTIFACT_VERSION or after["active"] is not False:
        write_lock(lock, {"plan_sha256": plan["sha256"], "status": "indeterminate",
                          "updated_utc": utc_now().isoformat(), "after": after})
        record_result(plan_path, plan, "INDETERMINATE", {"after": after, "retry": False})
        raise IndeterminateError(
            f"The plugin row does not read back as {ARTIFACT_VERSION} and inactive. "
            "This plan is locked and will not retry."
        )
    write_lock(lock, {"plan_sha256": plan["sha256"], "status": "committed_verified",
                      "updated_utc": utc_now().isoformat(), "after": after})
    record_result(plan_path, plan, "COMMITTED_AND_VERIFIED",
                  {"after": after, "comparison": comparison, "activated": False})
    emit({
        "status": "COMMITTED_AND_VERIFIED",
        "action": "plugin_replace",
        "plugin_file": PLUGIN_FILE,
        "installed_version": after["version"],
        "active": after["active"],
        "comparison": comparison,
        "activated": False,
        "plan_sha256": plan["sha256"],
        "replay_locked": True,
    })


@holds_wordpress_browser("WordPress: apply the active 2.0.5 UPS repair")
def command_commit_ups_repair(args: argparse.Namespace) -> None:
    plan_path, plan = _open_commit(args, "plugin_ups_repair")
    artifact = verify_ups_repair_artifact(Path(plan["artifact"]["path"]))
    if artifact["sha256"] != plan["artifact"]["sha256"]:
        raise DeploymentError("REFUSED: the UPS repair artifact no longer matches the approved plan.")
    lock = lock_path(plan_path)

    # The shared WordPress browser lock is acquired by the decorator before this
    # function. The live row is checked before the permanent plan lock, so drift or
    # a busy/missing browser is a free refusal. The lock is written immediately
    # before the one overwrite click.
    with admin_session() as admin:
        _verify_pre_state(admin, plan)
        write_lock(lock, {
            "plan_sha256": plan["sha256"], "status": "in_flight",
            "started_utc": utc_now().isoformat(), "stage": "ups_repair_upload",
        }, exclusive=True)
        try:
            comparison = admin.upload_ups_repair(Path(artifact["path"]))
            admin.goto_plugins()
            after = admin.read_row()
        except Exception as exc:
            _burn(lock, plan, plan_path, exc, "ups_repair_upload")
            raise IndeterminateError(
                "The UPS repair upload or row read-back is unverified. The plan is permanently "
                "locked with no retry and no rollback; inspect the fixed Plugins row."
            ) from exc

    if after != plan["after_expected"]:
        write_lock(lock, {
            "plan_sha256": plan["sha256"], "status": "indeterminate",
            "updated_utc": utc_now().isoformat(), "after": after,
            "public_validation_pending": True,
        })
        record_result(plan_path, plan, "INDETERMINATE", {
            "after": after, "retry": False, "rollback": False,
            "public_validation_pending": True,
        })
        raise IndeterminateError(
            "The fixed plugin row does not exactly read back as active version 2.0.5. "
            "The plan is permanently locked with no retry and no rollback."
        )

    write_lock(lock, {
        "plan_sha256": plan["sha256"], "status": "plugin_row_verified",
        "updated_utc": utc_now().isoformat(), "after": after,
        "public_validation_pending": True,
    })
    record_result(plan_path, plan, "PLUGIN_ROW_VERIFIED_PUBLIC_CHECKS_PENDING", {
        "after": after,
        "comparison": comparison,
        "active_before_and_after": True,
        "quote_form_transaction_triggered": False,
        "product_or_setting_write": False,
        "email_order_payment_created": False,
        "automatic_rollback": False,
        "public_validation_pending": True,
    })
    emit({
        "status": "PLUGIN_ROW_VERIFIED_PUBLIC_CHECKS_PENDING",
        "action": "plugin_ups_repair",
        "plugin_file": PLUGIN_FILE,
        "installed_version": after["version"],
        "active": after["active"],
        "comparison": comparison,
        "quote_form_transaction_triggered": False,
        "product_or_setting_write": False,
        "email_order_payment_created": False,
        "automatic_rollback": False,
        "public_validation_pending": True,
        "plan_sha256": plan["sha256"],
        "replay_locked": True,
    })


def _verify_pre_state(admin: AdminPage, plan: dict[str, Any]) -> dict[str, Any]:
    admin.goto_plugins()
    live = admin.read_row()
    if live["fingerprint"] != plan["before"]["fingerprint"]:
        raise DeploymentError(
            "REFUSED: the plugin row changed after Rachad reviewed this plan. Nothing was "
            "written. Stage a new plan."
        )
    return live


def _failure_attribution(exc: Exception | None,
                         findings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Name the failure: fixed step, exception class, fixed code. Never text."""
    if isinstance(exc, ValidationStepError):
        return {"step": exc.step, "exception_class": exc.exception_class, "code": exc.code}
    if exc is not None:
        return {"step": None, "exception_class": type(exc).__name__, "code": None}
    reasons = list((findings or {}).get("reasons") or [])
    step = "checkout_assertions"
    for reason in reasons:
        named = STEP_BY_FAILURE_REASON.get(reason)
        if named is None and reason.startswith("exact_message_count="):
            named = "checkout_assertions"
        if named is not None:
            step = named
            break
    return {"step": step, "exception_class": None, "code": "validation_failed"}


def _burn(lock: Path, plan: dict[str, Any], plan_path: Path, exc: Exception,
          stage: str) -> None:
    attribution = _failure_attribution(exc)
    write_lock(lock, {
        "plan_sha256": plan["sha256"], "status": "indeterminate",
        "updated_utc": utc_now().isoformat(), "stage": stage,
        "reason": type(exc).__name__, **attribution,
    })
    record_result(plan_path, plan, "INDETERMINATE",
                  {"stage": stage, "reason": type(exc).__name__, "retry": False, **attribution})


def _validate_anonymously() -> dict[str, Any]:
    """The fixed public contract, one named step at a time.

    Every sub-step runs under a fixed name so a failure can always be attributed.
    Nothing here treats a timeout as a pass: a step that times out raises, and the
    caller rolls back.
    """
    recorder = StepRecorder()
    with anonymous_session() as public:
        home = recorder.run("home_load", lambda: public.check_page_renders(HOME_URL))
        recorder.run("product_load", lambda: public.load_healthy_page(PRODUCT_URL))
        form = recorder.run("variation_form", public.variations_form)
        methods = public.select_required_variation(form, recorder)
        # Exactly the helper the preflight rehearsed. Add to cart is never clicked
        # until WooCommerce itself says the variation is purchasable.
        ready = recorder.run("variation_ready", public.require_variation_ready)
        recorder.run("add_to_cart", public.add_selected_to_cart)
        recorder.run("checkout_load", public.goto_checkout)
        checkout = recorder.run("checkout_assertions", public.read_checkout)
    findings = {
        "home": home,
        "cart_add": {"variation": list(REQUIRED_VARIATION_TEXT), "added": True,
                     "selection_method": methods},
        "checkout": checkout,
        "steps": recorder.steps,
        "selection_method": methods,
        "variation_resolved": bool(ready["variation_resolved"]),
        "add_to_cart_enabled": bool(ready["add_to_cart_enabled"]),
        "exact_message_count": checkout["exact_message_count"],
        "checkout_blocked": not checkout["checkout_form_present"]
                            and not checkout["payment_form_present"],
    }
    reasons: list[str] = []
    if home["blank"] or home["fatal"]:
        reasons.append("storefront_home_unhealthy")
    if checkout["fatal"]:
        reasons.append("checkout_fatal")
    if checkout["blank"]:
        reasons.append("checkout_blank")
    if checkout["exact_message_count"] != 1:
        reasons.append(f"exact_message_count={checkout['exact_message_count']}")
    if checkout["checkout_form_present"]:
        reasons.append("checkout_form_available")
    if checkout["payment_form_present"]:
        reasons.append("payment_form_available")
    findings["passed"] = not reasons
    findings["reasons"] = reasons
    return findings


def _emergency_deactivate() -> dict[str, Any]:
    """Deactivate the fixed plugin and confirm the storefront recovered."""
    with admin_session() as admin:
        admin.goto_plugins()
        after = admin.deactivate()
    with anonymous_session() as public:
        home = public.check_page_renders(HOME_URL)
        cart = public.check_page_renders(CART_URL)
    recovered = (after["active"] is False and not home["blank"] and not home["fatal"]
                 and not cart["blank"] and not cart["fatal"])
    return {"after": after, "home": home, "cart": cart, "recovered": recovered,
            "plugin_file": PLUGIN_FILE}


@holds_wordpress_browser("WordPress: activate the freight checkout-guard plugin")
def command_commit_activate(args: argparse.Namespace) -> None:
    plan_path, plan = _open_commit(args, "plugin_activate")
    # Local file check, still before any browser: stale or tampered evidence
    # refuses without burning the plan.
    evidence = _verify_plan_preflight(plan)
    lock = lock_path(plan_path)
    activation_attempted = False

    # The rollback deliberately happens AFTER this block closes. Emergency
    # deactivation opens its own session, and nesting one Playwright context
    # inside another is exactly the kind of thing that fails only in production.
    activation_error: Exception | None = None
    after: dict[str, Any] = {}
    with admin_session() as admin:
        _verify_pre_state(admin, plan)
        write_lock(lock, {
            "plan_sha256": plan["sha256"], "status": "in_flight",
            "started_utc": utc_now().isoformat(), "stage": "activate",
        }, exclusive=True)
        try:
            activation_attempted = True
            after = admin.activate()
        except Exception as exc:  # noqa: BLE001 - re-raised below, outside the session
            activation_error = exc

    if activation_error is not None:
        rollback = _rollback_once(plan, plan_path, lock, activation_error, "activate",
                                  attempted=activation_attempted)
        raise IndeterminateError(
            "Activation could not be verified. The fixed plugin was deactivated again "
            f"(recovered={rollback['recovered']}). This plan is permanently closed."
        ) from activation_error

    try:
        findings = _validate_anonymously()
    except Exception as exc:
        rollback = _rollback_once(plan, plan_path, lock, exc, "validation",
                                  attempted=activation_attempted)
        attribution = _failure_attribution(exc)
        raise IndeterminateError(
            "The anonymous public validation could not be completed, so it is treated as a "
            f"failure at step {attribution['step']} ({attribution['exception_class']}). "
            f"The fixed plugin was deactivated again (recovered={rollback['recovered']}). "
            "This plan is permanently closed."
        ) from exc

    if not findings["passed"]:
        rollback = _rollback_once(plan, plan_path, lock, None, "validation",
                                  attempted=activation_attempted, findings=findings)
        emit({
            "status": "VALIDATION_FAILED_ROLLED_BACK",
            "action": "plugin_activate",
            "plugin_file": PLUGIN_FILE,
            "reasons": findings["reasons"],
            "failed_step": _failure_attribution(None, findings)["step"],
            "steps": findings["steps"],
            "exact_message_count": findings["exact_message_count"],
            "checkout_blocked": findings["checkout_blocked"],
            "emergency_deactivated": True,
            "recovered": rollback["recovered"],
            "plan_sha256": plan["sha256"],
            "plan_closed_permanently": True,
        })
        raise DeploymentError(
            "Public validation failed: " + ", ".join(findings["reasons"])
            + ". The fixed plugin was deactivated again and this plan is permanently closed."
        )

    write_lock(lock, {"plan_sha256": plan["sha256"], "status": "committed_verified",
                      "updated_utc": utc_now().isoformat(), "after": after})
    record_result(plan_path, plan, "COMMITTED_AND_VERIFIED",
                  {"after": after, "validation": findings, "emergency_deactivated": False,
                   "preflight_sha256": evidence["sha256"],
                   "preflight_runs": len(evidence["runs"]),
                   "preflight_created_utc": evidence["created_utc"]})
    emit({
        "status": "COMMITTED_AND_VERIFIED",
        "action": "plugin_activate",
        "plugin_file": PLUGIN_FILE,
        "installed_version": after["version"],
        "active": after["active"],
        "exact_message_count": findings["exact_message_count"],
        "checkout_blocked": findings["checkout_blocked"],
        "selection_method": findings["selection_method"],
        "variation_resolved": findings["variation_resolved"],
        "add_to_cart_enabled": findings["add_to_cart_enabled"],
        "steps": findings["steps"],
        "preflight_sha256": evidence["sha256"],
        "preflight_runs": len(evidence["runs"]),
        "emergency_deactivated": False,
        "order_placed": False,
        "ups_setting_touched": False,
        "plan_sha256": plan["sha256"],
        "replay_locked": True,
    })


def _rollback_once(plan: dict[str, Any], plan_path: Path, lock: Path,
                   exc: Exception | None, stage: str, *, attempted: bool,
                   findings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Emergency-deactivate exactly once, then close the plan permanently."""
    rollback: dict[str, Any] = {"performed": False, "recovered": False,
                                "reason": "activation was never attempted"}
    if attempted:
        try:
            rollback = {"performed": True, **_emergency_deactivate()}
        except Exception as rollback_exc:  # noqa: BLE001 - reported, never swallowed
            rollback = {"performed": True, "recovered": False,
                        "reason": type(rollback_exc).__name__}
    attribution = _failure_attribution(exc, findings)
    write_lock(lock, {
        "plan_sha256": plan["sha256"], "status": "failed_closed",
        "updated_utc": utc_now().isoformat(), "stage": stage,
        "reason": type(exc).__name__ if exc is not None else "validation_failed",
        "emergency_deactivated": rollback["performed"],
        "recovered": rollback["recovered"],
        **attribution,
    })
    detail: dict[str, Any] = {
        "stage": stage,
        "reason": type(exc).__name__ if exc is not None else "validation_failed",
        "emergency_deactivated": rollback["performed"],
        "rollback": rollback,
        "retry": False,
        **attribution,
    }
    if findings is not None:
        detail["validation"] = findings
    record_result(plan_path, plan, "FAILED_CLOSED", detail)
    return rollback


@holds_wordpress_browser("WordPress: deactivate the freight checkout-guard plugin")
def command_commit_deactivate(args: argparse.Namespace) -> None:
    plan_path, plan = _open_commit(args, "plugin_deactivate")
    lock = lock_path(plan_path)
    with admin_session() as admin:
        _verify_pre_state(admin, plan)
        write_lock(lock, {
            "plan_sha256": plan["sha256"], "status": "in_flight",
            "started_utc": utc_now().isoformat(), "stage": "deactivate",
        }, exclusive=True)
        try:
            after = admin.deactivate()
        except Exception as exc:
            _burn(lock, plan, plan_path, exc, "deactivate")
            raise IndeterminateError(
                "The deactivation is unverified. This plan is locked and will not retry."
            ) from exc
    if after["active"] is not False or after["present"] is not True:
        write_lock(lock, {"plan_sha256": plan["sha256"], "status": "indeterminate",
                          "updated_utc": utc_now().isoformat(), "after": after})
        record_result(plan_path, plan, "INDETERMINATE", {"after": after, "retry": False})
        raise IndeterminateError("The plugin row does not read back as installed and inactive.")
    write_lock(lock, {"plan_sha256": plan["sha256"], "status": "committed_verified",
                      "updated_utc": utc_now().isoformat(), "after": after})
    record_result(plan_path, plan, "COMMITTED_AND_VERIFIED", {"after": after, "deleted": False})
    emit({
        "status": "COMMITTED_AND_VERIFIED",
        "action": "plugin_deactivate",
        "plugin_file": PLUGIN_FILE,
        "installed_version": after["version"],
        "active": after["active"],
        "still_installed": after["present"],
        "deleted": False,
        "plan_sha256": plan["sha256"],
        "replay_locked": True,
    })


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("inspect").set_defaults(func=command_inspect)
    commands.add_parser("preflight-validation").set_defaults(func=command_preflight_validation)
    commands.add_parser("stage-replace").set_defaults(func=command_stage_replace)
    commands.add_parser("stage-ups-repair").set_defaults(func=command_stage_ups_repair)
    commands.add_parser("stage-deactivate").set_defaults(func=command_stage_deactivate)

    stage_activate = commands.add_parser("stage-activate")
    stage_activate.add_argument("--preflight", required=True)
    stage_activate.set_defaults(func=command_stage_activate)

    for name, handler in (
        ("commit-replace", command_commit_replace),
        ("commit-ups-repair", command_commit_ups_repair),
        ("commit-activate", command_commit_activate),
        ("commit-deactivate", command_commit_deactivate),
    ):
        commit = commands.add_parser(name)
        commit.add_argument("--plan", required=True)
        commit.add_argument("--approval", required=True)
        commit.set_defaults(func=handler)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (DeploymentError, OSError, ValueError, UiLaneBusy, UiLaneLockError) as exc:
        # UiLaneBusy is a clean refusal, not a crash: the other lane holds the
        # shared WordPress window, no plan was locked and nothing was uploaded
        # or activated. It must print one ERROR line, not a traceback.
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
