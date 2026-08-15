#!/usr/bin/env python
"""FRP Depot WordPress Plugin Deployment Tool.

Commissioned by Rachad Homsi on 2026-08-09. Commissioning authorises building and
testing this tool. It is NOT approval of any site change: every write still needs
Rachad's own one-word APPROVED against one exact staged plan.

SCOPE -- ONE PLUGIN, FIVE WRITE ROUTES, NOTHING ELSE.

    plugin_replace      Upload Plugin -> replace the installed copy, leave INACTIVE
    plugin_activate     Plugins row -> Activate, then anonymous public validation
    plugin_deactivate   Plugins row -> Deactivate, stays installed
    plugin_fnpt_display_repair   Active 2.0.6 -> exact frozen 2.0.7 presentation repair
    plugin_freight_contact_preserve_repair   Active 2.0.7 -> exact frozen 2.0.8 repair

The plugin identity, site origin, and each commissioned route's exact artifact
path, version and SHA-256 are hard-coded constants. A caller supplies no URL, no
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
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
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
TOOL_VERSION = "1.9.0"
SCHEMA_VERSION = 10
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

# Fixed active-plugin FNPT presentation correction commissioned by Rachad on
# 2026-08-14. The artifact is an exact narrow transform of the active 2.0.6 ZIP:
# version/disclosure metadata, one plugin-owned native-button concealment class
# added in freight/fail-closed render and removed on restore, and one exact CSS
# rule for that class. The allowlist, server cart/checkout safeguards, quote form
# and transaction remain unchanged.
FNPT_REPAIR_ARTIFACT_PATH = (
    ROOT / "Dado" / "20_Working" / "fnpt_display_repair_2_0_7"
    / "frpdepot-freight-checkout-guard-2.0.7.zip"
)
FNPT_REPAIR_VERSION = "2.0.7"
FNPT_REPAIR_FROM_VERSION = "2.0.6"
FNPT_REPAIR_SHA256 = "8490974d64d23407384208785e56f4205fc4acac8657c7d677bca2ab7330613f"
FNPT_REPAIR_BYTES = 28586
FNPT_REPAIR_ALLOWLIST_SHA256 = "a8051de3e7c99a3d8285c3199f1f0a32bb525ff8ca3dac56acbf7132f8e154a8"
FNPT_REPAIR_BASELINE_PATH = (
    ROOT / "Dado" / "20_Working" / "fnpt_display_repair_2_0_6"
    / "frpdepot-freight-checkout-guard-2.0.6.zip"
)
FNPT_REPAIR_BASELINE_SHA256 = "2b21cf9e9a4455458fc1d35a0d5a23d8da3692bc44fe58314fafa06a9e8afef0"
FNPT_REPAIR_MEASUREMENT_STATUS = (
    "RESEARCH-BASED ESTIMATE - NOT PHYSICALLY VERIFIED - NOT UPS APPROVED"
)
FNPT_REPAIR_MEMBERS = (
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.css",
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.js",
    f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php",
    f"{PLUGIN_SLUG}/readme.txt",
    f"{PLUGIN_SLUG}/ups-allowlist.json",
)
FNPT_REPAIR_MEMBER_SHA256 = {
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.css":
        "93fd319ef5bb91e6fdffd1b62a33f3647684f307d3272f86f2a6875355790c15",
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.js":
        "43dddd42314f9ea4f4a53b33f78ab25f255f3548a9b956acbc07b98ae761f663",
    f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php":
        "5ad8574db1bfa58d4a38488fe1b9fe8d92f594808e94a9620a888126438a7acd",
    f"{PLUGIN_SLUG}/readme.txt":
        "89e44482639b6be768a17ea5d560bf38ad85676872f0265df18fb656e2341739",
    f"{PLUGIN_SLUG}/ups-allowlist.json":
        "a8051de3e7c99a3d8285c3199f1f0a32bb525ff8ca3dac56acbf7132f8e154a8",
}

# Fixed active-plugin Contact preservation repair. 2.0.8 changes only the exact
# PHP/readme contract needed to accept the already-correct strong-formatted
# Contact sentence without writing Contact page 469. The source ZIP is the exact
# currently active 2.0.7 artifact pinned above; aliases are intentional so the
# baseline cannot silently diverge from the FNPT constants that established it.
CONTACT_PRESERVE_ARTIFACT_PATH = (
    ROOT / "Dado" / "20_Working" / "freight_quote_contact_preserve_2_0_8"
    / "frpdepot-freight-checkout-guard-2.0.8.zip"
)
CONTACT_PRESERVE_VERSION = "2.0.8"
CONTACT_PRESERVE_FROM_VERSION = FNPT_REPAIR_VERSION
CONTACT_PRESERVE_SHA256 = "f2b74b5e935f8f953297b94eef8408173f74366092e7fe99657994b527bc15f5"
CONTACT_PRESERVE_BYTES = 29735
CONTACT_PRESERVE_MEMBERS = FNPT_REPAIR_MEMBERS
CONTACT_PRESERVE_MEMBER_SHA256 = {
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.css":
        "93fd319ef5bb91e6fdffd1b62a33f3647684f307d3272f86f2a6875355790c15",
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.js":
        "43dddd42314f9ea4f4a53b33f78ab25f255f3548a9b956acbc07b98ae761f663",
    f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php":
        "dbe9688810266c63dce71a6077b9489b8ee9cbf70918c97a8aff1d353344c1cd",
    f"{PLUGIN_SLUG}/readme.txt":
        "b156a215e95fcfea05f12194a4dc4a052ad2cc581a04ea0e7349443c50221311",
    f"{PLUGIN_SLUG}/ups-allowlist.json":
        "a8051de3e7c99a3d8285c3199f1f0a32bb525ff8ca3dac56acbf7132f8e154a8",
}
CONTACT_PRESERVE_BASELINE_PATH = FNPT_REPAIR_ARTIFACT_PATH
CONTACT_PRESERVE_BASELINE_SHA256 = FNPT_REPAIR_SHA256
CONTACT_PRESERVE_BASELINE_BYTES = FNPT_REPAIR_BYTES
CONTACT_PRESERVE_BASELINE_MEMBERS = FNPT_REPAIR_MEMBERS
CONTACT_PRESERVE_BASELINE_MEMBER_SHA256 = FNPT_REPAIR_MEMBER_SHA256

# Permanently refused. Withdrawn after the 2026-08-09 production failure.
WITHDRAWN_VERSION = "1.0.0"
WITHDRAWN_SHA256 = "4d8396d95baf0907754730e578ad4c41b98908f77992718c41b293434e07fe25"

# The only version the legacy replace/activate/deactivate routes may put on the
# site or activate. The later named active-to-active repair routes pin their own
# exact source and target versions independently above.
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
    "/wp-admin/plugin-editor.php",
    "/wp-admin/tools.php",
    "/wp-admin/admin.php",
})
# WordPress core's fixed replace-current destination. Query values are checked
# in memory only; nonce/package values are never logged, planned or returned.
OVERWRITE_QUERY_KEYS = frozenset({"action", "overwrite", "package", "_wpnonce"})
OVERWRITE_SUCCESS_MARKER = "Plugin updated successfully."
PLUGIN_EDITOR_TEXTAREA_SELECTOR = "textarea#newcontent"
FREIGHT_STATUS_URL = (
    f"{EXACT_ORIGIN}/wp-admin/tools.php?page=frpdepot-freight-quote-journey"
)
FREIGHT_STATUS_SELECTOR = "#frpdepot-fqj-status"
SOURCE_CONTACT_FORM_ID = 1
SOURCE_CONTACT_FORM_EDITOR_URL = (
    f"{EXACT_ORIGIN}/wp-admin/admin.php?page=gf_edit_forms&id={SOURCE_CONTACT_FORM_ID}"
    "&view=settings&subview=notification"
)
SOURCE_CONTACT_NOTIFICATION_NAME = "Admin Notification"
SOURCE_CONTACT_NOTIFICATION_LINK_SELECTOR = 'a[href*="subview=notification"]'
PLUGIN_EDITOR_URL_BY_MEMBER = {
    member: f"{EXACT_ORIGIN}/wp-admin/plugin-editor.php?" + urlencode({
        "file": member,
        "plugin": PLUGIN_FILE,
    })
    for member in CONTACT_PRESERVE_MEMBERS
}

HOME_URL = f"{EXACT_ORIGIN}/"
PRODUCT_URL = f"{EXACT_ORIGIN}/product/frp-fw-pipe/"
CART_URL = f"{EXACT_ORIGIN}/cart/"
CHECKOUT_URL = f"{EXACT_ORIGIN}/checkout/"
ALLOWED_PUBLIC_PATHS = frozenset({"/", "/product/frp-fw-pipe/", "/cart/", "/checkout/"})

CONTACT_URL = f"{EXACT_ORIGIN}/contact/"
REQUEST_QUOTE_URL = f"{EXACT_ORIGIN}/request-a-quote/"
CONTACT_READ_ONLY_URLS = frozenset({CONTACT_URL, REQUEST_QUOTE_URL})
CONTACT_READ_ONLY_PATHS = frozenset({"/contact/", "/request-a-quote/"})
CONTACT_TARGET_STRONG_TEXT = "Request a Freight Quote"
CONTACT_TARGET_SENTENCE = (
    "Product selections approved for direct shipping can be purchased online. "
    "Selections requiring packing or freight review will show Request a Freight Quote. "
    "Submitting a quote request does not place an order or authorize payment."
)
CONTACT_OLD_SENTENCE = (
    "If your item is listed in the Products section, you can add it to cart; otherwise "
    "use the contact form for custom or non-standard requests."
)
FREIGHT_SPEC_SHA256 = "5348ef3f357676f5629cf72696fd3fe0be718a3847854974f20cf28cc7047400"
FREIGHT_CONTACT_ID = 469
FREIGHT_PRIVACY_STATUS = {
    "recipient_values_projected": False,
    "customer_values_projected": False,
    "artifact_content_projected": False,
    "route_hash_only": True,
}
FREIGHT_BACKUP_STATUS_KEYS = (
    "form_backup_present", "quote_page_backup_present",
    "contact_backup_present", "route_backup_present",
)
FREIGHT_STATUS_KEYS = frozenset({
    "spec_sha256", "status", "deployment_id", "source_form_id",
    "source_notification_name_match", "route_sha256", "form_id", "form_owned",
    "form_sha256", "page_id", "page_owned", "page_sha256", "contact_id",
    "contact_new_count", "contact_old_count", "contact_sha256",
    *FREIGHT_BACKUP_STATUS_KEYS, "receipt_count", "receipt_schema_valid",
    "receipt_chain_valid", "receipt_append_only", "receipt_head_sha256",
    "apply_receipt_head_sha256", "rollback_drift_free", "rollback_blocked_artifact",
    "form_before_sha256", "quote_page_before_sha256", "contact_before_sha256",
    "privacy",
})
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# Fixed customer pages used by the active 2.0.6 -> 2.0.7 FNPT presentation correction. The
# approved command may open exactly two throwaway contexts. Context one starts
# from a unique fixed-shape cache-busting FNPT URL and context two starts from
# the canonical bare FNPT URL. Each context checks the exact FNPT, Stub and Pipe
# matrix. No cart,
# checkout, quote/contact page or admin path is in this allowlist.
FNPT_PRODUCT_URL = f"{EXACT_ORIGIN}/product/fnpt-coupling-threaded-on-both-ends/"
STUB_PRODUCT_URL = f"{EXACT_ORIGIN}/product/frp-stub-flange/"
PIPE_PRODUCT_URL = PRODUCT_URL
FNPT_CACHE_BUSTER_KEY = "frpdepot_fnpt_verify"
FNPT_PUBLIC_PATHS = frozenset({
    "/product/fnpt-coupling-threaded-on-both-ends/",
    "/product/frp-stub-flange/",
    "/product/frp-fw-pipe/",
})
FNPT_STATIC_PATH_PREFIXES = ("/wp-content/", "/wp-includes/")

# Exact live published FNPT IDs established by the read-only WooCommerce and
# public-page evidence. Four historical draft/private children are deliberately
# absent; broadening or shrinking this tuple makes staging and commit fail closed.
FNPT_PUBLISHED_VARIATION_IDS = (
    2062, 2063, 2064, 2065, 2066, 2067, 2068, 2069,
    2070, 2071, 2072, 2073, 2074, 2075, 2076, 2077,
    2078, 2079, 2080, 2081, 2082, 2083, 2084, 2085,
    2086, 2087, 2088, 2089, 2090, 2091, 2092, 2093,
    2155, 2156, 2157, 2158, 2159, 2160, 2161, 2162,
    2167, 2168, 2169, 2170, 2171, 2172, 2173, 2174,
    2175, 2176, 2177, 2178, 2179, 2180, 2181, 2182,
    2183, 2184, 2185, 2186,
)
FNPT_PUBLIC_CONTROL_IDS = (2028, 2044, 2057, 2088)
FNPT_JS_PATH = (
    f"/wp-content/plugins/{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.js"
)
FNPT_CSS_PATH = (
    f"/wp-content/plugins/{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.css"
)
FNPT_JS_SHA256 = FNPT_REPAIR_MEMBER_SHA256[
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.js"
]
FNPT_CSS_SHA256 = FNPT_REPAIR_MEMBER_SHA256[
    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.css"
]

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

ACTIONS = (
    "plugin_replace", "plugin_activate", "plugin_deactivate",
    "plugin_fnpt_display_repair", "plugin_freight_contact_preserve_repair",
)


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

# The FNPT replacement has its own closed post-write vocabulary. These are the
# only step/code values that may reach its permanent lock/result after an upload;
# no exception text, URL, HTML, selector value or customer-facing copy is kept.
FNPT_PUBLIC_VALIDATION_STEPS = (
    "cache_buster_fnpt_load",
    "cache_buster_fnpt_contract",
    "cache_buster_fnpt_variations",
    "cache_buster_fail_closed_cases",
    "cache_buster_stub_controls",
    "cache_buster_pipe_control",
    "cache_buster_page_errors",
    "canonical_fnpt_load",
    "canonical_fnpt_contract",
    "canonical_fnpt_variations",
    "canonical_page_errors",
    "anonymous_network_guard",
)
FNPT_PUBLIC_VALIDATION_CODES = frozenset({
    "unexpected_exception",
    "page_status",
    "page_url",
    "page_blank",
    "page_fatal",
    "page_error",
    "page_error_unclassified",
    "page_error_guard_attribution",
    "page_error_guard_repeated",
    "form_missing",
    "form_ambiguous",
    "parent_identity",
    "variation_dataset_missing",
    "variation_dataset_malformed",
    "variation_dataset_ids",
    "variation_dataset_identity",
    "variation_dataset_attributes",
    "panel_count",
    "asset_count",
    "asset_version",
    "asset_response",
    "asset_hash",
    "config_missing",
    "config_shape",
    "selection_control",
    "selection_timeout",
    "selection_identity",
    "freight_state",
    "direct_state",
    "quote_handoff",
    "stale_state",
    "context_count",
    "network_guard",
})

FNPT_PANEL_SELECTOR = ".frpdepot-fqj-product"
FNPT_QUOTE_BUTTON_SELECTOR = ".frpdepot-fqj-product-button"
FNPT_VARIATION_SELECT_SELECTOR = '.variations select[name^="attribute_"]'
FNPT_RESET_SELECTOR = ".reset_variations"
FNPT_QUANTITY_SELECTOR = "input.qty"
FNPT_SCRIPT_SELECTOR = "script[src]"
FNPT_STYLE_SELECTOR = 'link[rel="stylesheet"][href]'
FNPT_NATIVE_CONCEALMENT_CLASS = "frpdepot-fqj-native-button-concealed"
FNPT_NATIVE_BUTTON_SELECTOR = "button.single_add_to_cart_button"
FNPT_NATIVE_CONCEALMENT_SELECTOR = f".{FNPT_NATIVE_CONCEALMENT_CLASS}"

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


class FnptPublicRefusal(IndeterminateError):
    """One fixed public-validation refusal code; never page-derived text."""

    def __init__(self, code: str):
        if code not in FNPT_PUBLIC_VALIDATION_CODES:
            raise DeploymentError("Internal: an unknown FNPT public-validation code was used.")
        self.code = code
        super().__init__(code)


class FnptPublicValidationError(IndeterminateError):
    """Bounded FNPT public failure attribution safe for permanent records."""

    def __init__(self, step: str, exception_class: str, code: str):
        if step not in FNPT_PUBLIC_VALIDATION_STEPS:
            raise DeploymentError("Internal: an unknown FNPT public-validation step was used.")
        if code not in FNPT_PUBLIC_VALIDATION_CODES:
            raise DeploymentError("Internal: an unknown FNPT public-validation code was used.")
        self.step = step
        self.exception_class = str(exception_class)
        self.code = code
        super().__init__(
            f"FNPT public-validation step {step} failed with {self.exception_class} ({code})."
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
    parsed = urlsplit(str(url))
    path = parsed.path or "/"
    if path not in ALLOWED_ADMIN_PATHS:
        raise DeploymentError(
            "REFUSED: the WordPress admin page is not one of the fixed allowlisted "
            "plugin pages. Sign-in, settings and every other admin screen are out of scope."
        )
    values = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.fragment:
        raise DeploymentError("REFUSED: WordPress admin fragments are outside the fixed routes.")
    if path == "/wp-admin/plugin-editor.php":
        if values.get("plugin") != [PLUGIN_FILE] or len(values.get("file") or []) != 1 \
                or values["file"][0] not in CONTACT_PRESERVE_MEMBERS \
                or set(values) != {"file", "plugin"}:
            raise DeploymentError("REFUSED: only the five fixed read-only plugin-editor files are allowed.")
    elif path == "/wp-admin/tools.php":
        if values != {"page": ["frpdepot-freight-quote-journey"]}:
            raise DeploymentError("REFUSED: only the fixed freight status page is allowed.")
    elif path == "/wp-admin/admin.php":
        if values != {
            "page": ["gf_edit_forms"],
            "id": [str(SOURCE_CONTACT_FORM_ID)],
            "view": ["settings"],
            "subview": ["notification"],
        }:
            raise DeploymentError(
                "REFUSED: only the read-only Contact Form 1 notification list is allowed."
            )


def assert_source_contact_notification_detail_url(url: str) -> None:
    """Allow only the exact notification detail link discovered on Contact Form 1.

    Gravity Forms owns the opaque notification ID, so it is validated structurally
    and never planned, emitted, or retained in a result artifact.
    """
    assert_origin(url)
    parsed = urlsplit(str(url))
    values = parse_qs(parsed.query, keep_blank_values=True)
    nid = values.get("nid") or []
    if (parsed.path != "/wp-admin/admin.php" or parsed.fragment
            or set(values) != {"page", "id", "view", "subview", "nid"}
            or values.get("page") != ["gf_edit_forms"]
            or values.get("id") != [str(SOURCE_CONTACT_FORM_ID)]
            or values.get("view") != ["settings"]
            or values.get("subview") != ["notification"]
            or len(nid) != 1 or not isinstance(nid[0], str)
            or not nid[0].strip() or len(nid[0]) > 256):
        raise DeploymentError(
            "REFUSED: the Contact Form 1 Admin Notification detail route is not exact."
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


def verify_fnpt_display_repair_artifact(path: Path | None = None) -> dict[str, Any]:
    """Independently prove the fixed active 2.0.6 -> 2.0.7 presentation ZIP."""
    artifact = Path(path or FNPT_REPAIR_ARTIFACT_PATH)
    if artifact.resolve() != Path(FNPT_REPAIR_ARTIFACT_PATH).resolve():
        raise DeploymentError("REFUSED: only the fixed FNPT display repair artifact may be used.")
    if not artifact.is_file():
        raise DeploymentError(f"The fixed FNPT display repair artifact is missing: {artifact}")
    data = artifact.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != FNPT_REPAIR_SHA256:
        raise DeploymentError("REFUSED: the fixed FNPT display repair SHA-256 does not match.")
    if len(data) != FNPT_REPAIR_BYTES:
        raise DeploymentError("REFUSED: the fixed FNPT display repair byte size does not match.")

    baseline = Path(FNPT_REPAIR_BASELINE_PATH)
    if not baseline.is_file():
        raise DeploymentError("REFUSED: the frozen deployed 2.0.6 baseline is missing.")
    baseline_data = baseline.read_bytes()
    if hashlib.sha256(baseline_data).hexdigest() != FNPT_REPAIR_BASELINE_SHA256:
        raise DeploymentError("REFUSED: the frozen deployed 2.0.6 baseline SHA-256 changed.")

    try:
        with zipfile.ZipFile(artifact) as repair_zip, zipfile.ZipFile(baseline) as baseline_zip:
            members = tuple(sorted(repair_zip.namelist()))
            baseline_members = tuple(sorted(baseline_zip.namelist()))
            if members != tuple(sorted(FNPT_REPAIR_MEMBERS)) or baseline_members != members:
                raise DeploymentError("REFUSED: the FNPT repair ZIP members are not the fixed set.")
            repair = {member: repair_zip.read(member) for member in members}
            prior = {member: baseline_zip.read(member) for member in members}
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise DeploymentError("REFUSED: the fixed FNPT display artifact is unreadable.") from exc

    for member, expected in FNPT_REPAIR_MEMBER_SHA256.items():
        if hashlib.sha256(repair[member]).hexdigest() != expected:
            raise DeploymentError(f"REFUSED: fixed FNPT repair member changed: {member}")

    php_member = f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php"
    js_member = f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.js"
    css_member = f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.css"
    allowlist_member = f"{PLUGIN_SLUG}/ups-allowlist.json"
    readme_member = f"{PLUGIN_SLUG}/readme.txt"
    if repair[allowlist_member] != prior[allowlist_member]:
        raise DeploymentError("REFUSED: the FNPT display repair changed the UPS allowlist.")

    try:
        header = repair[php_member].decode("utf-8", errors="strict")
        prior_header = prior[php_member].decode("utf-8", errors="strict")
        javascript = repair[js_member].decode("utf-8", errors="strict")
        prior_javascript = prior[js_member].decode("utf-8", errors="strict")
        stylesheet = repair[css_member].decode("utf-8", errors="strict")
        prior_stylesheet = prior[css_member].decode("utf-8", errors="strict")
        readme = repair[readme_member].decode("utf-8", errors="strict")
        prior_readme = prior[readme_member].decode("utf-8", errors="strict")
        allowlist = json.loads(repair[allowlist_member])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError("REFUSED: the fixed FNPT display source is unreadable.") from exc

    found = re.search(r"(?im)^\s*\*\s*Version:\s*(\S+)\s*$", header)
    version = found.group(1) if found else ""
    if version != FNPT_REPAIR_VERSION or f"FRPDEPOT_FQJ_VERSION = '{FNPT_REPAIR_VERSION}'" not in header:
        raise DeploymentError("REFUSED: the FNPT repair does not declare fixed version 2.0.7.")
    if f"Plugin Name: {PLUGIN_NAME}" not in header:
        raise DeploymentError("REFUSED: the FNPT repair is not the fixed FRP Depot plugin.")
    if "register_activation_hook" in header or "admin_post_frpdepot_fqj_fixed_apply" not in header:
        raise DeploymentError("REFUSED: the fixed activation/apply trigger contract changed.")

    expected_php = prior_header
    php_changes = (
        (" * Version:     2.0.6", " * Version:     2.0.7"),
        ("const FRPDEPOT_FQJ_VERSION = '2.0.6';",
         "const FRPDEPOT_FQJ_VERSION = '2.0.7';"),
    )
    for old, new in php_changes:
        if expected_php.count(old) != 1:
            raise DeploymentError("REFUSED: frozen 2.0.6 PHP baseline relation changed.")
        expected_php = expected_php.replace(old, new, 1)
    if header != expected_php:
        raise DeploymentError("REFUSED: PHP changed beyond 2.0.7 version metadata.")

    expected_javascript = prior_javascript
    js_changes = (
        (
            "\tvar firedLeadKeys = Object.create( null );",
            "\tvar firedLeadKeys = Object.create( null );\n"
            "\tvar nativeConcealmentClass = 'frpdepot-fqj-native-button-concealed';",
        ),
        (
            "\tfunction restoreNativeButton( button ) {\n"
            "\t\tif ( ! button || ! button.frpdepotFqjOriginal ) {\n"
            "\t\t\treturn;\n"
            "\t\t}",
            "\tfunction restoreNativeButton( button ) {\n"
            "\t\tif ( ! button ) {\n"
            "\t\t\treturn;\n"
            "\t\t}\n"
            "\t\tbutton.classList.remove( nativeConcealmentClass );\n"
            "\t\tif ( ! button.frpdepotFqjOriginal ) {\n"
            "\t\t\treturn;\n"
            "\t\t}",
        ),
        (
            "\t\t\tif ( nativeButton ) {\n\t\t\t\tnativeButton.hidden = true;",
            "\t\t\tif ( nativeButton ) {\n"
            "\t\t\t\tnativeButton.classList.add( nativeConcealmentClass );\n"
            "\t\t\t\tnativeButton.hidden = true;",
        ),
    )
    for old, new in js_changes:
        if expected_javascript.count(old) != 1:
            raise DeploymentError("REFUSED: frozen 2.0.6 JavaScript baseline relation changed.")
        expected_javascript = expected_javascript.replace(old, new, 1)
    if javascript != expected_javascript:
        raise DeploymentError("REFUSED: JavaScript changed beyond the owned-class presentation correction.")
    if (javascript.count("frpdepot-fqj-native-button-concealed") != 1
            or javascript.count("classList.add( nativeConcealmentClass )") != 1
            or javascript.count("classList.remove( nativeConcealmentClass )") != 1
            or javascript.count("config.cartQuoteRequired !== true") != 2):
        raise DeploymentError("REFUSED: the fixed native-button or preserved cart consumer contract changed.")

    expected_css_suffix = (
        "\nform.variations_form button.single_add_to_cart_button."
        "frpdepot-fqj-native-button-concealed {\n"
        "\tdisplay: none !important;\n"
        "}\n"
    )
    if stylesheet != prior_stylesheet + expected_css_suffix:
        raise DeploymentError("REFUSED: CSS changed beyond one exact owned-class concealment rule.")

    expected_readme = prior_readme
    readme_changes = (
        ("Stable tag: 2.0.6", "Stable tag: 2.0.7"),
        ("Version 2.0.6 preserves", "Version 2.0.7 preserves"),
        (
            "== Changelog ==\n",
            "== Changelog ==\n\n"
            "= 2.0.7 =\n"
            "* Conceals the native Add to Cart button in freight and fail-closed product states with one plugin-owned class and exact display rule, even when the active theme overrides the hidden attribute.\n"
            "* Removes that owned class in direct and unresolved product states so reset, hide, quantity and blocked/direct transitions cannot leak stale presentation.\n"
            "* Changes no allowlist, eligibility, rate, cart, checkout, quote-form or business-transaction behavior.\n",
        ),
    )
    for old, new in readme_changes:
        if expected_readme.count(old) != 1:
            raise DeploymentError("REFUSED: frozen 2.0.6 readme baseline relation changed.")
        expected_readme = expected_readme.replace(old, new, 1)
    if readme != expected_readme:
        raise DeploymentError("REFUSED: readme changed beyond 2.0.7 disclosure.")

    if hashlib.sha256(repair[allowlist_member]).hexdigest() != FNPT_REPAIR_ALLOWLIST_SHA256:
        raise DeploymentError("REFUSED: the fixed allowlist SHA-256 changed.")
    required = {
        "schema_version": 2,
        "measurement_status": FNPT_REPAIR_MEASUREMENT_STATUS,
        "verified_packing_groups": 0,
        "researched_candidate_groups": 30,
        "oversized_groups_excluded": 7,
    }
    for key, expected in required.items():
        if allowlist.get(key) != expected:
            raise DeploymentError(f"REFUSED: fixed allowlist field {key!r} changed.")
    items = allowlist.get("items")
    if not isinstance(items, list) or len(items) != 64:
        raise DeploymentError("REFUSED: the fixed allowlist must contain exactly 64 variations.")
    identities: set[tuple[int, int, str]] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "product_id", "variation_id", "sku", "packing_group_id", "source_status",
        }:
            raise DeploymentError("REFUSED: a fixed allowlist row changed shape.")
        identity = (item["product_id"], item["variation_id"], item["sku"])
        if (item["product_id"] not in (1368, 1423) or not item["variation_id"]
                or not item["sku"] or item["source_status"] != FNPT_REPAIR_MEASUREMENT_STATUS):
            raise DeploymentError("REFUSED: a fixed allowlist identity or disclosure changed.")
        identities.add(identity)
    if len(identities) != 64:
        raise DeploymentError("REFUSED: fixed allowlist identities are duplicated.")
    protected_quote_ids = {2044, 2045, 2046, 2047, 2048, 2049, 2050, 2051, 2052, 2053,
                           2057, 2088}
    if any(identity[1] in protected_quote_ids for identity in identities):
        raise DeploymentError("REFUSED: an oversized, Pipe or FNPT variation entered the allowlist.")
    if not any(identity[1] == 2028 for identity in identities):
        raise DeploymentError("REFUSED: direct-checkout control variation 2028 left the allowlist.")
    try:
        expires = datetime.fromisoformat(str(allowlist["expires_utc"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise DeploymentError("REFUSED: the fixed allowlist expiry is invalid.") from exc
    if utc_now() >= expires:
        raise DeploymentError("REFUSED: the fixed allowlist has expired.")

    for marker in (
        "Stable tag: 2.0.7",
        "plugin-owned class and exact display rule",
        "blocked/direct transitions",
        "FNPT Coupling parent 2061",
        "selected variation_id",
        "Changes no allowlist, eligibility, rate, cart, checkout, quote-form",
        "64-variation UPS",
        "NOT physical packing measurements",
        "14 variations",
        "60 published FNPT",
    ):
        if marker not in readme:
            raise DeploymentError(f"REFUSED: FNPT repair disclosure is missing {marker!r}.")
    return {
        "path": str(artifact),
        "sha256": digest,
        "version": version,
        "members": list(members),
        "member_sha256": dict(FNPT_REPAIR_MEMBER_SHA256),
        "allowlist_sha256": FNPT_REPAIR_ALLOWLIST_SHA256,
        "baseline_path": str(baseline),
        "baseline_sha256": FNPT_REPAIR_BASELINE_SHA256,
        "bytes": FNPT_REPAIR_BYTES,
    }


def verify_contact_preserve_artifact(path: Path | None = None) -> dict[str, Any]:
    """Prove exact target and exact active 2.0.7 baseline, including member order."""
    artifact = Path(path or CONTACT_PRESERVE_ARTIFACT_PATH)
    if artifact.resolve() != Path(CONTACT_PRESERVE_ARTIFACT_PATH).resolve():
        raise DeploymentError("REFUSED: only the fixed 2.0.8 Contact preservation artifact is allowed.")
    baseline = Path(CONTACT_PRESERVE_BASELINE_PATH)
    for label, candidate, expected_bytes, expected_hash in (
        ("2.0.8 target", artifact, CONTACT_PRESERVE_BYTES, CONTACT_PRESERVE_SHA256),
        ("2.0.7 baseline", baseline, CONTACT_PRESERVE_BASELINE_BYTES,
         CONTACT_PRESERVE_BASELINE_SHA256),
    ):
        if not candidate.is_file():
            raise DeploymentError(f"REFUSED: the fixed {label} ZIP is missing.")
        raw = candidate.read_bytes()
        if len(raw) != expected_bytes \
                or not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), expected_hash):
            raise DeploymentError(f"REFUSED: the fixed {label} bytes or SHA-256 changed.")
    try:
        with zipfile.ZipFile(artifact) as target_zip, zipfile.ZipFile(baseline) as baseline_zip:
            target_names = tuple(target_zip.namelist())
            baseline_names = tuple(baseline_zip.namelist())
            target_data = {name: target_zip.read(name) for name in target_names}
            baseline_data = {name: baseline_zip.read(name) for name in baseline_names}
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise DeploymentError("REFUSED: the fixed Contact preservation ZIP pair is unreadable.") from exc
    if target_names != tuple(CONTACT_PRESERVE_MEMBERS):
        raise DeploymentError("REFUSED: the 2.0.8 ZIP member order or set changed.")
    if baseline_names != tuple(CONTACT_PRESERVE_BASELINE_MEMBERS):
        raise DeploymentError("REFUSED: the 2.0.7 baseline ZIP member order or set changed.")
    target_hashes = {name: hashlib.sha256(target_data[name]).hexdigest() for name in target_names}
    baseline_hashes = {
        name: hashlib.sha256(baseline_data[name]).hexdigest() for name in baseline_names
    }
    if target_hashes != CONTACT_PRESERVE_MEMBER_SHA256:
        raise DeploymentError("REFUSED: a fixed 2.0.8 member SHA-256 changed.")
    if baseline_hashes != CONTACT_PRESERVE_BASELINE_MEMBER_SHA256:
        raise DeploymentError("REFUSED: a fixed 2.0.7 baseline member SHA-256 changed.")

    php_name = f"{PLUGIN_SLUG}/frpdepot-freight-checkout-guard.php"
    readme_name = f"{PLUGIN_SLUG}/readme.txt"
    protected = set(target_names) - {php_name, readme_name}
    if any(target_data[name] != baseline_data[name] for name in protected):
        raise DeploymentError("REFUSED: 2.0.8 changed a protected CSS, JavaScript or allowlist member.")
    try:
        php = target_data[php_name].decode("utf-8", errors="strict")
        readme = target_data[readme_name].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise DeploymentError("REFUSED: fixed 2.0.8 source is not strict UTF-8.") from exc
    required_php = (
        " * Version:     2.0.8",
        "const FRPDEPOT_FQJ_VERSION = '2.0.8';",
        f"const FRPDEPOT_FQJ_SPEC_SHA256 = '{FREIGHT_SPEC_SHA256}';",
        "function frpdepot_fqj_contact_mode(",
        "'preserve' !== $after_mode",
        "'before_version' => '2.0.7'",
        f"'rollback_sha256' => '{CONTACT_PRESERVE_BASELINE_SHA256}'",
    )
    if any(php.count(marker) != 1 for marker in required_php):
        raise DeploymentError("REFUSED: fixed 2.0.8 PHP preservation contract changed.")
    if "register_activation_hook" in php:
        raise DeploymentError("REFUSED: fixed 2.0.8 gained an activation write trigger.")
    if (readme.count("Stable tag: 2.0.8") != 1
            or readme.count("already-correct strong-formatted sentence") != 1
            or readme.count("zero Contact post writes") != 1):
        raise DeploymentError("REFUSED: fixed 2.0.8 disclosure changed.")
    return {
        "path": str(artifact),
        "version": CONTACT_PRESERVE_VERSION,
        "sha256": CONTACT_PRESERVE_SHA256,
        "bytes": CONTACT_PRESERVE_BYTES,
        "members": list(target_names),
        "member_sha256": dict(target_hashes),
        "baseline_path": str(baseline),
        "baseline_version": CONTACT_PRESERVE_FROM_VERSION,
        "baseline_sha256": CONTACT_PRESERVE_BASELINE_SHA256,
        "baseline_bytes": CONTACT_PRESERVE_BASELINE_BYTES,
        "baseline_members": list(baseline_names),
        "baseline_member_sha256": dict(baseline_hashes),
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

    def read_installed_member_projection(self) -> dict[str, Any]:
        """GET the five fixed editor screens and retain hashes only, never source."""
        hashes: dict[str, str] = {}
        for member in CONTACT_PRESERVE_MEMBERS:
            self._goto(PLUGIN_EDITOR_URL_BY_MEMBER[member])
            editors = self._page.query_selector_all(PLUGIN_EDITOR_TEXTAREA_SELECTOR)
            if len(editors) != 1:
                raise DeploymentError("REFUSED: one fixed plugin-editor member is unavailable.")
            source = editors[0].input_value(timeout=ACTION_TIMEOUT_MS)
            if not isinstance(source, str):
                raise DeploymentError("REFUSED: one fixed installed plugin member is unreadable.")
            hashes[member] = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return {
            "members": list(CONTACT_PRESERVE_MEMBERS),
            "member_sha256": hashes,
            "source_projected": False,
            "read_only": True,
        }

    def read_freight_status(self) -> dict[str, Any]:
        """Read only the plugin's fixed privacy-preserving status projection."""
        self._goto(FREIGHT_STATUS_URL)
        rows = self._page.query_selector_all(FREIGHT_STATUS_SELECTOR)
        if len(rows) != 1:
            raise DeploymentError("REFUSED: fixed freight status projection is unavailable.")
        raw = str(rows[0].get_attribute("data-projection") or "")
        try:
            status = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeploymentError("REFUSED: fixed freight status projection is invalid JSON.") from exc
        if not isinstance(status, dict) or set(status) != FREIGHT_STATUS_KEYS:
            raise DeploymentError("REFUSED: fixed freight status projection schema changed.")
        return status

    def read_source_notification_route_projection(self) -> dict[str, Any]:
        """Validate Contact Form 1's route in-page and return no recipient values."""
        self._goto(SOURCE_CONTACT_FORM_EDITOR_URL)
        candidates = self._page.query_selector_all(SOURCE_CONTACT_NOTIFICATION_LINK_SELECTOR)
        matches = [
            item for item in candidates
            if str(item.inner_text() or "").strip() == SOURCE_CONTACT_NOTIFICATION_NAME
        ]
        if len(matches) != 1:
            raise DeploymentError(
                "REFUSED: Contact Form 1 does not expose exactly one Admin Notification link."
            )
        href = str(matches[0].get_attribute("href") or "")
        detail_url = urljoin(self.url, href)
        assert_source_contact_notification_detail_url(detail_url)
        self._page.goto(
            detail_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS
        )
        assert_source_contact_notification_detail_url(self.url)
        projection = self._page.evaluate(
            """async expected => {
              const source=(window.form && typeof window.form==='object' && !Array.isArray(window.form))
                ? window.form : null;
              if (!source) return null;
              const sourceId=Number(source.id || source.ID || 0);
              const values=source.notifications && typeof source.notifications==='object'
                ? Object.values(source.notifications) : [];
              const active=n => n && (n.isActive===undefined || n.isActive===true
                || n.isActive===1 || n.isActive==='1');
              const matches=values.filter(n => n && n.name===expected.notification && active(n));
              if (sourceId!==expected.formId || source.title!==expected.title || matches.length!==1)
                return null;
              const item=matches[0];
              const keys=['to','toType','routing','bcc','from','fromName','replyTo'];
              if (!keys.every(key => Object.prototype.hasOwnProperty.call(item,key))) return null;
              const route={}; keys.forEach(key => { route[key]=item[key]; });
              const emptyRouting=route.routing===null
                || (Array.isArray(route.routing) && route.routing.length===0)
                || (route.routing && typeof route.routing==='object'
                    && !Array.isArray(route.routing) && Object.keys(route.routing).length===0);
              if (typeof route.to!=='string' || route.to.trim()===''
                  || route.toType!=='email' || typeof route.bcc!=='string'
                  || typeof route.from!=='string' || typeof route.fromName!=='string'
                  || typeof route.replyTo!=='string' || !emptyRouting) return null;
              const canonical=value => {
                if (Array.isArray(value)) return value.map(canonical);
                if (value && typeof value==='object') {
                  const out={}; Object.keys(value).sort().forEach(key => { out[key]=canonical(value[key]); });
                  return out;
                }
                return value;
              };
              const bytes=new TextEncoder().encode(JSON.stringify(canonical(route)));
              const digest=await crypto.subtle.digest('SHA-256',bytes);
              const routeHash=Array.from(new Uint8Array(digest))
                .map(value => value.toString(16).padStart(2,'0')).join('');
              return {
                source_form_id:sourceId,
                source_form_title_match:true,
                source_notification_name_match:true,
                active_notification_match_count:matches.length,
                route_shape_valid:true,
                route_sha256:routeHash,
                privacy:{recipient_values_projected:false,customer_values_projected:false,
                  artifact_content_projected:false,route_hash_only:true}
              };
            }""",
            {"formId": SOURCE_CONTACT_FORM_ID, "title": "Contact",
             "notification": "Admin Notification"},
        )
        expected = {
            "source_form_id": SOURCE_CONTACT_FORM_ID,
            "source_form_title_match": True,
            "source_notification_name_match": True,
            "active_notification_match_count": 1,
            "route_shape_valid": True,
            "privacy": FREIGHT_PRIVACY_STATUS,
        }
        if (not isinstance(projection, dict)
                or set(projection) != {*expected, "route_sha256"}
                or any(projection.get(key) != value for key, value in expected.items())
                or not HEX_SHA256.fullmatch(str(projection.get("route_sha256") or ""))):
            raise DeploymentError("REFUSED: Contact Form 1 notification route is not exact and valid.")
        return projection

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

    def upload_fnpt_display_repair(self, artifact: Path) -> dict[str, Any]:
        """Upload only the exact frozen 2.0.7 FNPT presentation correction ZIP."""
        if Path(artifact).resolve() != Path(FNPT_REPAIR_ARTIFACT_PATH).resolve():
            raise DeploymentError("REFUSED: only the fixed FNPT display repair artifact may be uploaded.")
        return self._upload_fixed_replace(artifact, FNPT_REPAIR_VERSION)

    def upload_freight_contact_preserve_repair(
        self, artifact: Path, eligible_snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        """Final shared eligibility gate before the one exact active overwrite."""
        require_contact_preserve_eligibility(eligible_snapshot)
        if Path(artifact).resolve() != Path(CONTACT_PRESERVE_ARTIFACT_PATH).resolve():
            raise DeploymentError("REFUSED: only the fixed 2.0.8 Contact preservation ZIP may upload.")
        return self._upload_fixed_replace(artifact, CONTACT_PRESERVE_VERSION)

    def _require_upload_comparison_destination(self, url: str) -> None:
        absolute = urljoin(self.url, str(url or ""))
        assert_admin_url(absolute)
        parsed = urlsplit(absolute)
        values = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.fragment or parsed.path != "/wp-admin/update.php" \
                or values != {"action": ["upload-plugin"]}:
            raise IndeterminateError(
                "The upload submission did not reach the exact WordPress comparison route."
            )

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
        try:
            with self._page.expect_navigation(
                wait_until="domcontentloaded", timeout=LOAD_STATE_TIMEOUT_MS
            ) as pending_navigation:
                submit.click(timeout=ACTION_TIMEOUT_MS)
            response = pending_navigation.value
        except Exception as exc:
            raise IndeterminateError(
                "The upload submission did not produce one proven bounded comparison navigation."
            ) from exc
        if response is None or int(response.status) != 200:
            raise IndeterminateError(
                "The upload comparison navigation did not return one proven HTTP 200 response."
            )
        assert_admin_url(self.url)
        self._require_upload_comparison_destination(str(response.url))
        self._require_upload_comparison_destination(self.url)
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

    def _require_overwrite_destination(
        self, url: str, *, expected_identity: tuple[str, str] | None = None
    ) -> tuple[str, str]:
        """Require WordPress core's exact replace-current destination without exposing secrets."""
        absolute = urljoin(self.url, str(url or ""))
        assert_admin_url(absolute)
        parsed = urlsplit(absolute)
        values = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.fragment or parsed.path != "/wp-admin/update.php" \
                or set(values) != OVERWRITE_QUERY_KEYS:
            raise DeploymentError(
                "REFUSED: the replace-current control does not target the exact WordPress "
                "overwrite route. Nothing was replaced."
            )
        if values.get("action") != ["upload-plugin"] \
                or values.get("overwrite") != ["update-plugin"]:
            raise DeploymentError(
                "REFUSED: the replace-current action is not the fixed upload-plugin overwrite. "
                "Nothing was replaced."
            )
        package_values = values.get("package") or []
        nonce_values = values.get("_wpnonce") or []
        if len(package_values) != 1 or not package_values[0] or len(package_values[0]) > 512:
            raise DeploymentError("REFUSED: the fixed overwrite package identity is missing.")
        if len(nonce_values) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", nonce_values[0]):
            raise DeploymentError("REFUSED: the fixed overwrite authorization shape is invalid.")
        identity = (package_values[0], nonce_values[0])
        if expected_identity is not None and identity != expected_identity:
            raise IndeterminateError(
                "WordPress navigated with a different overwrite identity than the reviewed control."
            )
        return identity

    def _confirm_and_overwrite(self, expected_version: str) -> dict[str, Any]:
        """Verify the comparison, prove overwrite navigation, then verify WordPress success."""
        if expected_version not in (
            ARTIFACT_VERSION, FNPT_REPAIR_VERSION, CONTACT_PRESERVE_VERSION
        ):
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
        href = links[0].get_attribute("href")
        reviewed_identity = self._require_overwrite_destination(str(href or ""))
        assert_admin_url(self.url)
        try:
            with self._page.expect_navigation(
                wait_until="domcontentloaded", timeout=LOAD_STATE_TIMEOUT_MS
            ) as pending_navigation:
                links[0].click(timeout=ACTION_TIMEOUT_MS)
            response = pending_navigation.value
        except Exception as exc:  # Playwright class is imported only inside session creation.
            raise IndeterminateError(
                "The replace-current click did not produce one proven bounded navigation."
            ) from exc
        if response is None or int(response.status) != 200:
            raise IndeterminateError(
                "The replace-current navigation did not return one proven HTTP 200 response."
            )
        assert_admin_url(self.url)
        self._require_overwrite_destination(str(response.url), expected_identity=reviewed_identity)
        self._require_overwrite_destination(self.url, expected_identity=reviewed_identity)
        success_notices = [
            " ".join(str(node.inner_text() or "").split())
            for node in self._page.query_selector_all(".wrap p")
        ]
        if success_notices.count(OVERWRITE_SUCCESS_MARKER) != 1:
            raise IndeterminateError(
                "WordPress did not show exactly one fixed structured plugin-update success marker."
            )
        return {
            "comparison_name": uploaded_name,
            "comparison_uploaded_version": uploaded_version,
            "overwrite_navigation_proven": True,
            "overwrite_http_status": 200,
            "wordpress_success_marker_exact": True,
        }


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


class FnptNetworkGuard:
    """Abort/record every non-read request and every possible analytics read.

    Only GET/HEAD documents for the three fixed product paths and passive static
    resources on the exact origin may proceed. Off-origin reads and same-origin
    fetch/xhr/beacon-style reads are aborted, so no analytics endpoint can receive
    even a read-shaped submission. Records contain counts and fixed method buckets
    only -- never URLs, payloads, headers or customer/page text.
    """

    _METHOD_BUCKETS = ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE", "OTHER")
    _PASSIVE_RESOURCE_TYPES = frozenset({"script", "stylesheet", "image", "font", "media"})

    def __init__(self, allowed_documents: frozenset[str]):
        self.allowed_documents = allowed_documents
        self.non_read_method_counts = {method: 0 for method in self._METHOD_BUCKETS}
        self.off_origin_reads_aborted = 0
        self.disallowed_same_origin_reads_aborted = 0
        self.allowed_read_requests = 0
        self.aborted_requests = 0

    @staticmethod
    def _method_bucket(method: str) -> str:
        return method if method in FnptNetworkGuard._METHOD_BUCKETS[:-1] else "OTHER"

    def handle(self, route: Any) -> None:
        request = route.request
        method = str(request.method or "").upper()
        if method not in {"GET", "HEAD"}:
            self.non_read_method_counts[self._method_bucket(method)] += 1
            self.aborted_requests += 1
            route.abort("blockedbyclient")
            return

        parsed = urlsplit(str(request.url or ""))
        exact_origin = (
            parsed.scheme == "https"
            and (parsed.hostname or "").casefold() == ALLOWED_HOST
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
        )
        if not exact_origin:
            self.off_origin_reads_aborted += 1
            self.aborted_requests += 1
            route.abort("blockedbyclient")
            return

        path = parsed.path or "/"
        resource_type = str(getattr(request, "resource_type", "") or "")
        is_document = (
            resource_type == "document"
            and str(request.url or "") in self.allowed_documents
            and path in FNPT_PUBLIC_PATHS
        )
        is_passive_static = (
            resource_type in self._PASSIVE_RESOURCE_TYPES
            and any(path.startswith(prefix) for prefix in FNPT_STATIC_PATH_PREFIXES)
        )
        if is_document or is_passive_static:
            self.allowed_read_requests += 1
            route.continue_()
            return

        self.disallowed_same_origin_reads_aborted += 1
        self.aborted_requests += 1
        route.abort("blockedbyclient")

    def projection(self) -> dict[str, Any]:
        return {
            "allowed_methods": ["GET", "HEAD"],
            "allowed_read_requests": self.allowed_read_requests,
            "non_read_requests_aborted": sum(self.non_read_method_counts.values()),
            "non_read_method_counts": dict(self.non_read_method_counts),
            "off_origin_reads_aborted": self.off_origin_reads_aborted,
            "disallowed_same_origin_reads_aborted": self.disallowed_same_origin_reads_aborted,
            "total_requests_aborted": self.aborted_requests,
            "analytics_submission_performed": False,
            "business_write_performed": False,
        }


class FnptCustomerPage:
    """Narrow anonymous reader and local-DOM exerciser for fixed product pages."""

    _CONFIG_KEYS = (
        "button", "cartHeading", "cartQuoteRequired", "cartQuoteUrl", "cartText",
        "formId", "formMarker", "quoteUrl",
    )

    def __init__(self, page: Any, guard: FnptNetworkGuard, *,
                 release_version: str = FNPT_REPAIR_VERSION,
                 js_sha256: str = FNPT_JS_SHA256,
                 css_sha256: str = FNPT_CSS_SHA256):
        self._page = page
        self.guard = guard
        self.release_version = release_version
        self.js_sha256 = js_sha256
        self.css_sha256 = css_sha256
        self._asset_responses: dict[str, list[Any]] = {FNPT_JS_PATH: [], FNPT_CSS_PATH: []}
        self._page_error_count = 0
        self._guard_induced_stripe_page_error_count = 0
        self._page_error_category: str | None = None
        self._page_error_off_origin_start = 0
        self._page_error_pages: list[dict[str, Any]] = []
        self._unresolved_native_baseline: dict[str, Any] | None = None
        page.on("response", self._capture_asset_response)
        page.on("pageerror", self._capture_page_error)

    def _capture_page_error(self, error: Any) -> None:
        if str(error or "") == "Stripe is not defined":
            self._guard_induced_stripe_page_error_count += 1
            return
        self._page_error_count += 1

    @staticmethod
    def _fixed_page_category(path: str) -> str:
        categories = {
            urlsplit(FNPT_PRODUCT_URL).path: "fnpt_product",
            urlsplit(STUB_PRODUCT_URL).path: "stub_control",
            urlsplit(PIPE_PRODUCT_URL).path: "pipe_control",
        }
        category = categories.get(path)
        if category is None:
            raise FnptPublicRefusal("page_url")
        return category

    def _begin_page_error_scope(self, category: str) -> None:
        if (self._page_error_category is not None
                or category not in {"fnpt_product", "stub_control", "pipe_control"}):
            raise FnptPublicRefusal("page_error_guard_attribution")
        guard_aborts = getattr(self.guard, "off_origin_reads_aborted", None)
        if type(guard_aborts) is not int or guard_aborts < 0:
            raise FnptPublicRefusal("page_error_guard_attribution")
        self._page_error_category = category
        self._page_error_off_origin_start = guard_aborts
        self._page_error_count = 0
        self._guard_induced_stripe_page_error_count = 0

    def _finish_current_page_errors(self) -> None:
        if self._page_error_category is None:
            return
        guard_aborts = getattr(self.guard, "off_origin_reads_aborted", None)
        if type(guard_aborts) is not int or guard_aborts < self._page_error_off_origin_start:
            raise FnptPublicRefusal("page_error_guard_attribution")
        abort_delta = guard_aborts - self._page_error_off_origin_start
        if self._page_error_count != 0:
            raise FnptPublicRefusal("page_error_unclassified")
        if self._guard_induced_stripe_page_error_count > 1:
            raise FnptPublicRefusal("page_error_guard_repeated")
        if self._guard_induced_stripe_page_error_count == 1 and abort_delta < 1:
            raise FnptPublicRefusal("page_error_guard_attribution")
        self._page_error_pages.append({
            "page_category": self._page_error_category,
            "accepted_fixed_guard_error_count": self._guard_induced_stripe_page_error_count,
            "unclassified_error_count": 0,
            "off_origin_reads_aborted_delta": abort_delta,
            "status": "passed",
        })
        self._page_error_category = None

    def page_error_projection(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._page_error_pages]

    def _capture_asset_response(self, response: Any) -> None:
        path = urlsplit(str(response.url or "")).path
        if path in self._asset_responses:
            self._asset_responses[path].append(response)

    @property
    def url(self) -> str:
        return str(self._page.url)

    @staticmethod
    def _same_url(actual: str, expected: str) -> bool:
        left, right = urlsplit(actual), urlsplit(expected)
        return (
            left.scheme == right.scheme == "https"
            and (left.hostname or "").casefold() == (right.hostname or "").casefold() == ALLOWED_HOST
            and left.port in (None, 443)
            and right.port in (None, 443)
            and left.path == right.path
            and parse_qs(left.query, keep_blank_values=True) == parse_qs(right.query, keep_blank_values=True)
            and not left.fragment
        )

    def load(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.path not in FNPT_PUBLIC_PATHS:
            raise FnptPublicRefusal("page_url")
        self._finish_current_page_errors()
        self._begin_page_error_scope(self._fixed_page_category(parsed.path))
        self._unresolved_native_baseline = None
        self._asset_responses = {FNPT_JS_PATH: [], FNPT_CSS_PATH: []}
        response = self._page.goto(
            url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS
        )
        self._page.wait_for_load_state("load", timeout=LOAD_STATE_TIMEOUT_MS)
        if response is None or int(response.status) != 200:
            raise FnptPublicRefusal("page_status")
        if not self._same_url(self.url, url):
            raise FnptPublicRefusal("page_url")
        text = str(self._page.inner_text("body", timeout=ACTION_TIMEOUT_MS) or "")
        lowered = text.casefold()
        if len(text.strip()) < MIN_RENDERED_TEXT:
            raise FnptPublicRefusal("page_blank")
        if any(marker in lowered for marker in FATAL_MARKERS):
            raise FnptPublicRefusal("page_fatal")

    def form(self, parent_id: int) -> Any:
        forms = self._page.query_selector_all(VARIATION_FORM_SELECTOR)
        if not forms:
            raise FnptPublicRefusal("form_missing")
        if len(forms) != 1:
            raise FnptPublicRefusal("form_ambiguous")
        form = forms[0]
        if str(form.get_attribute("data-product_id") or "") != str(parent_id):
            raise FnptPublicRefusal("parent_identity")
        return form

    def variation_rows(self, parent_id: int, expected_ids: tuple[int, ...], *,
                       expected_freight: bool | None) -> dict[int, dict[str, Any]]:
        form = self.form(parent_id)
        raw = form.get_attribute("data-product_variations")
        if not isinstance(raw, str) or not raw or len(raw) > 2_000_000:
            raise FnptPublicRefusal("variation_dataset_missing")
        try:
            values = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise FnptPublicRefusal("variation_dataset_malformed") from exc
        if not isinstance(values, list):
            raise FnptPublicRefusal("variation_dataset_malformed")
        rows: dict[int, dict[str, Any]] = {}
        for value in values:
            if not isinstance(value, dict) or type(value.get("variation_id")) is not int:
                raise FnptPublicRefusal("variation_dataset_identity")
            variation_id = value["variation_id"]
            if variation_id in rows:
                raise FnptPublicRefusal("variation_dataset_ids")
            attributes = value.get("attributes")
            if (not isinstance(attributes, dict) or not attributes
                    or any(not isinstance(key, str) or not key.startswith("attribute_")
                           or not isinstance(option, str) or not option
                           for key, option in attributes.items())):
                raise FnptPublicRefusal("variation_dataset_attributes")
            if expected_freight is not None and (
                value.get("frpdepot_quote_required") is not expected_freight
                or type(value.get("frpdepot_product_id")) is not int
                or value.get("frpdepot_product_id") != parent_id
                or type(value.get("frpdepot_variation_id")) is not int
                or value.get("frpdepot_variation_id") != variation_id
            ):
                raise FnptPublicRefusal("variation_dataset_identity")
            rows[variation_id] = value
        if tuple(sorted(rows)) != tuple(expected_ids):
            raise FnptPublicRefusal("variation_dataset_ids")
        return rows

    def fixed_control_row(self, parent_id: int, variation_id: int,
                          expected_freight: bool | None) -> dict[str, Any]:
        """Read one exact comparator row without pretending its siblings are fixed here."""
        form = self.form(parent_id)
        raw = form.get_attribute("data-product_variations")
        if not isinstance(raw, str) or not raw or len(raw) > 2_000_000:
            raise FnptPublicRefusal("variation_dataset_missing")
        try:
            values = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise FnptPublicRefusal("variation_dataset_malformed") from exc
        if not isinstance(values, list):
            raise FnptPublicRefusal("variation_dataset_malformed")
        matches = [value for value in values if isinstance(value, dict)
                   and value.get("variation_id") == variation_id]
        if len(matches) != 1:
            raise FnptPublicRefusal("variation_dataset_ids")
        row = matches[0]
        attributes = row.get("attributes")
        if (type(row.get("variation_id")) is not int
                or not isinstance(attributes, dict) or not attributes
                or any(not isinstance(key, str) or not key.startswith("attribute_")
                       or not isinstance(option, str) or not option
                       for key, option in attributes.items())):
            raise FnptPublicRefusal("variation_dataset_attributes")
        if expected_freight is not None and (
            row.get("frpdepot_quote_required") is not expected_freight
            or type(row.get("frpdepot_product_id")) is not int
            or row.get("frpdepot_product_id") != parent_id
            or type(row.get("frpdepot_variation_id")) is not int
            or row.get("frpdepot_variation_id") != variation_id
        ):
            raise FnptPublicRefusal("variation_dataset_identity")
        return row

    def _fixed_asset_url(self, url: str, path: str) -> bool:
        parsed = urlsplit(str(url or ""))
        return (
            parsed.scheme == "https"
            and (parsed.hostname or "").casefold() == ALLOWED_HOST
            and parsed.port in (None, 443)
            and parsed.path == path
            and parse_qs(parsed.query, keep_blank_values=True) == {"ver": [self.release_version]}
            and not parsed.fragment
        )

    def _require_asset(self, selector: str, attribute: str, path: str, expected_hash: str) -> None:
        matching_path: list[str] = []
        exact: list[str] = []
        for element in self._page.query_selector_all(selector):
            url = str(element.get_attribute(attribute) or "")
            if urlsplit(url).path == path:
                matching_path.append(url)
                if self._fixed_asset_url(url, path):
                    exact.append(url)
        if len(matching_path) != 1:
            raise FnptPublicRefusal("asset_count")
        if len(exact) != 1:
            raise FnptPublicRefusal("asset_version")
        responses = [response for response in self._asset_responses[path]
                     if self._fixed_asset_url(str(response.url or ""), path)]
        if len(responses) != 1 or int(responses[0].status) != 200:
            raise FnptPublicRefusal("asset_response")
        try:
            body = responses[0].body()
        except Exception as exc:  # noqa: BLE001 - converted to a fixed code
            raise FnptPublicRefusal("asset_response") from exc
        if hashlib.sha256(body).hexdigest() != expected_hash:
            raise FnptPublicRefusal("asset_hash")

    def require_release_shell(self) -> None:
        if len(self._page.query_selector_all(FNPT_PANEL_SELECTOR)) != 1:
            raise FnptPublicRefusal("panel_count")
        self._require_asset(FNPT_SCRIPT_SELECTOR, "src", FNPT_JS_PATH, self.js_sha256)
        self._require_asset(FNPT_STYLE_SELECTOR, "href", FNPT_CSS_PATH, self.css_sha256)
        config = self._page.evaluate(
            """keys => {
              const value=window.FRPDepotFreightQuoteJourney;
              if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
              const types={};
              keys.forEach(key => { types[key]=typeof value[key]; });
              return {keys:Object.keys(value).sort(), types:types};
            }""",
            list(self._CONFIG_KEYS),
        )
        if not isinstance(config, dict):
            raise FnptPublicRefusal("config_missing")
        expected_types = {key: "string" for key in self._CONFIG_KEYS}
        if (tuple(config.get("keys") or ()) != tuple(sorted(self._CONFIG_KEYS))
                or config.get("types") != expected_types):
            raise FnptPublicRefusal("config_shape")

    def require_release_contract(self, parent_id: int, expected_ids: tuple[int, ...], *,
                                 expected_freight: bool | None) -> dict[int, dict[str, Any]]:
        self.require_release_shell()
        rows = self.variation_rows(
            parent_id, expected_ids, expected_freight=expected_freight
        )
        self.capture_unresolved_baseline(parent_id)
        return rows

    def require_selection_controls(self, parent_id: int, row: dict[str, Any]) -> None:
        form = self.form(parent_id)
        selects = form.query_selector_all(FNPT_VARIATION_SELECT_SELECTOR)
        for name, value in row["attributes"].items():
            matches = [select for select in selects
                       if str(select.get_attribute("name") or "") == name]
            if len(matches) != 1:
                raise FnptPublicRefusal("selection_control")
            options = [str(option.get_attribute("value") or "")
                       for option in matches[0].query_selector_all("option")]
            if options.count(value) != 1:
                raise FnptPublicRefusal("selection_control")

    def _reset(self, parent_id: int) -> None:
        form = self.form(parent_id)
        resets = form.query_selector_all(FNPT_RESET_SELECTOR)
        if len(resets) != 1:
            raise FnptPublicRefusal("selection_control")
        for select in form.query_selector_all(FNPT_VARIATION_SELECT_SELECTOR):
            select.select_option("", force=True, timeout=ACTION_TIMEOUT_MS)
        self._page.evaluate(
            """() => {
              const form=document.querySelector('form.variations_form');
              if (form && window.jQuery) window.jQuery(form).trigger('reset_data');
            }"""
        )
        self._require_unresolved_state(parent_id)

    def select_row(self, parent_id: int, row: dict[str, Any]) -> None:
        self._reset(parent_id)
        form = self.form(parent_id)
        selects = form.query_selector_all(FNPT_VARIATION_SELECT_SELECTOR)
        for name, value in row["attributes"].items():
            matches = [select for select in selects
                       if str(select.get_attribute("name") or "") == name]
            if len(matches) != 1:
                raise FnptPublicRefusal("selection_control")
            matches[0].select_option(value, force=True, timeout=ACTION_TIMEOUT_MS)
        expected = str(row["variation_id"])
        deadline = time.monotonic() + (READINESS_TIMEOUT_MS / 1000)
        while True:
            controls = form.query_selector_all(VARIATION_ID_SELECTOR)
            if len(controls) == 1:
                actual = str(controls[0].input_value(timeout=ACTION_TIMEOUT_MS) or "")
                if actual == expected:
                    return
            if time.monotonic() >= deadline:
                raise FnptPublicRefusal("selection_timeout")
            time.sleep(READINESS_POLL_SECONDS)

    def _state(self) -> dict[str, Any]:
        self._page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )
        value = self._page.evaluate(
            """() => {
              const forms=document.querySelectorAll('form.variations_form');
              const form=forms.length===1 ? forms[0] : null;
              const input=form && form.querySelector('input.variation_id');
              const nativeButtons=form ? form.querySelectorAll('button.single_add_to_cart_button') : [];
              const add=nativeButtons.length===1 ? nativeButtons[0] : null;
              const owned=document.querySelectorAll('.frpdepot-fqj-native-button-concealed');
              const panels=document.querySelectorAll('.frpdepot-fqj-product');
              const panel=panels.length===1 ? panels[0] : null;
              const quote=panel && panel.querySelector('.frpdepot-fqj-product-button');
              const visible=el => {
                if (!el) return false;
                const style=getComputedStyle(el);
                return style.display!=='none' && style.visibility!=='hidden' && style.visibility!=='collapse' && el.getClientRects().length>0;
              };
              const nativeStyle=add ? getComputedStyle(add) : null;
              return {
                formCount:forms.length,
                resolved:String(input && input.value || ''),
                panelCount:panels.length,
                panelHidden:panel ? !!panel.hidden : null,
                panelVisible:visible(panel),
                nativeHidden:add ? !!add.hidden : null,
                nativeVisible:visible(add),
                nativeDisplay:nativeStyle ? nativeStyle.display : null,
                nativeVisibility:nativeStyle ? nativeStyle.visibility : null,
                nativeOwnedClass:add ? add.classList.contains('frpdepot-fqj-native-button-concealed') : null,
                nativeButtonCount:nativeButtons.length,
                ownedClassCount:owned.length,
                nativeDisabled:add ? (!!add.disabled || add.getAttribute('disabled')!==null) : null,
                nativeAriaHidden:add ? add.getAttribute('aria-hidden') : null,
                nativeAriaDisabled:add ? add.getAttribute('aria-disabled') : null,
                nativeTabindex:add ? add.getAttribute('tabindex') : null,
                quoteHref:quote ? quote.getAttribute('href') : null,
                quoteAriaDisabled:quote ? quote.getAttribute('aria-disabled') : null,
                quoteTabindex:quote ? quote.getAttribute('tabindex') : null
              };
            }"""
        )
        if not isinstance(value, dict):
            raise FnptPublicRefusal("stale_state")
        return value

    @staticmethod
    def _native_restore_projection(state: dict[str, Any]) -> dict[str, Any]:
        return {
            key: state.get(key) for key in (
                "nativeHidden", "nativeVisible", "nativeDisplay", "nativeVisibility",
                "nativeOwnedClass", "nativeDisabled", "nativeAriaHidden",
                "nativeAriaDisabled", "nativeTabindex", "nativeButtonCount",
                "ownedClassCount",
            )
        }

    def capture_unresolved_baseline(self, parent_id: int) -> None:
        del parent_id
        state = self._state()
        if (state.get("formCount") != 1
                or state.get("resolved") not in {"", "0"}
                or state.get("panelCount") != 1
                or state.get("panelHidden") is not True
                or state.get("panelVisible") is not False
                or state.get("nativeButtonCount") != 1
                or state.get("ownedClassCount") != 0
                or state.get("nativeOwnedClass") is not False
                or state.get("nativeDisplay") in (None, "none")
                or state.get("nativeVisible") is not True
                or state.get("quoteHref") is not None
                or state.get("quoteAriaDisabled") != "true"
                or state.get("quoteTabindex") != "-1"):
            raise FnptPublicRefusal("stale_state")
        self._unresolved_native_baseline = self._native_restore_projection(state)

    def require_no_page_errors(self) -> list[dict[str, Any]]:
        self._finish_current_page_errors()
        return self.page_error_projection()

    @staticmethod
    def _quote_url_exact(url: str, parent_id: int, variation_id: int,
                         quantity: str = "1") -> bool:
        parsed = urlsplit(str(url or ""))
        return (
            parsed.scheme == "https"
            and (parsed.hostname or "").casefold() == ALLOWED_HOST
            and parsed.port in (None, 443)
            and parsed.path == "/"
            and parse_qs(parsed.query, keep_blank_values=True) == {
                "fqj_source": ["product"],
                "fqj_product_id": [str(parent_id)],
                "fqj_variation_id": [str(variation_id)],
                "fqj_quantity": [quantity],
            }
            and not parsed.fragment
        )

    def require_freight_state(self, parent_id: int, variation_id: int, *,
                              handoff: bool, quantity: str = "1") -> None:
        state = self._state()
        if state.get("formCount") != 1 or state.get("panelCount") != 1:
            raise FnptPublicRefusal("panel_count")
        if (state.get("panelHidden") is not False or state.get("panelVisible") is not True
                or state.get("nativeButtonCount") != 1
                or state.get("ownedClassCount") != 1
                or state.get("nativeHidden") is not True
                or state.get("nativeVisible") is not False
                or state.get("nativeDisplay") != "none"
                or state.get("nativeVisibility") != "visible"
                or state.get("nativeOwnedClass") is not True
                or state.get("nativeDisabled") is not True
                or state.get("nativeAriaHidden") != "true"
                or state.get("nativeAriaDisabled") != "true"
                or state.get("nativeTabindex") != "-1"):
            raise FnptPublicRefusal("freight_state")
        if handoff:
            if (state.get("resolved") != str(variation_id)
                    or state.get("quoteAriaDisabled") is not None
                    or not self._quote_url_exact(
                        str(state.get("quoteHref") or ""), parent_id, variation_id, quantity
                    )):
                raise FnptPublicRefusal("quote_handoff")
        elif (state.get("quoteHref") is not None
              or state.get("quoteAriaDisabled") != "true"
              or state.get("quoteTabindex") != "-1"):
            raise FnptPublicRefusal("quote_handoff")

    def require_direct_state(self, variation_id: int) -> None:
        state = self._state()
        if (state.get("resolved") != str(variation_id)
                or state.get("formCount") != 1
                or state.get("panelCount") != 1
                or state.get("panelHidden") is not True
                or state.get("panelVisible") is not False
                or state.get("nativeHidden") is not False
                or state.get("nativeVisible") is not True
                or state.get("nativeDisplay") in (None, "none")
                or state.get("nativeVisibility") != "visible"
                or state.get("nativeButtonCount") != 1
                or state.get("ownedClassCount") != 0
                or state.get("nativeOwnedClass") is not False
                or state.get("nativeDisabled") is not False
                or state.get("nativeAriaHidden") == "true"
                or state.get("nativeAriaDisabled") == "true"
                or state.get("nativeTabindex") == "-1"
                or state.get("quoteHref") is not None
                or state.get("quoteAriaDisabled") != "true"
                or state.get("quoteTabindex") != "-1"):
            raise FnptPublicRefusal("direct_state")

    def _require_restored_state(self, parent_id: int, *,
                                require_selection_clear: bool) -> None:
        del parent_id
        state = self._state()
        if (type(require_selection_clear) is not bool
                or self._unresolved_native_baseline is None
                or state.get("formCount") != 1
                or (require_selection_clear and state.get("resolved") not in {"", "0"})
                or state.get("panelCount") != 1
                or state.get("panelHidden") is not True
                or state.get("panelVisible") is not False
                or state.get("quoteHref") is not None
                or state.get("quoteAriaDisabled") != "true"
                or state.get("quoteTabindex") != "-1"
                or self._native_restore_projection(state) != self._unresolved_native_baseline):
            raise FnptPublicRefusal("stale_state")

    def _require_unresolved_state(self, parent_id: int) -> None:
        self._require_restored_state(parent_id, require_selection_clear=True)

    def _require_plugin_reset_state(self, parent_id: int) -> None:
        self._require_restored_state(parent_id, require_selection_clear=False)

    def dispatch_found_variation(self, payload: dict[str, Any]) -> None:
        self._page.evaluate(
            """payload => {
              const form=document.querySelector('form.variations_form');
              if (!form || !window.jQuery) throw new Error('fixed event unavailable');
              window.jQuery(form).trigger('found_variation', [payload]);
            }""",
            payload,
        )

    def dispatch_lifecycle(self, event_name: str) -> None:
        if event_name not in {"reset_data", "hide_variation"}:
            raise FnptPublicRefusal("stale_state")
        self._page.evaluate(
            """eventName => {
              const form=document.querySelector('form.variations_form');
              if (!form || !window.jQuery) throw new Error('fixed event unavailable');
              window.jQuery(form).trigger(eventName);
            }""",
            event_name,
        )

    def quantity_transition(self, value: str) -> None:
        if value != "2":
            raise FnptPublicRefusal("stale_state")
        changed = self._page.evaluate(
            """value => {
              const form=document.querySelector('form.variations_form');
              const qty=form && form.querySelector('input.qty');
              if (!qty) return false;
              qty.value=value;
              qty.dispatchEvent(new Event('input', {bubbles:true}));
              qty.dispatchEvent(new Event('change', {bubbles:true}));
              return true;
            }""",
            value,
        )
        if changed is not True:
            raise FnptPublicRefusal("selection_control")


@contextlib.contextmanager
def fnpt_anonymous_session(allowed_documents: frozenset[str], *,
                           release_version: str = FNPT_REPAIR_VERSION,
                           js_sha256: str = FNPT_JS_SHA256,
                           css_sha256: str = FNPT_CSS_SHA256) -> Iterator[FnptCustomerPage]:
    """One throwaway nonpersistent context with the strict FNPT network guard."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            context = browser.new_context()
            context.set_default_timeout(ACTION_TIMEOUT_MS)
            context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            guard = FnptNetworkGuard(allowed_documents)
            context.route("**/*", guard.handle)
            try:
                yield FnptCustomerPage(
                    context.new_page(), guard, release_version=release_version,
                    js_sha256=js_sha256, css_sha256=css_sha256,
                )
            finally:
                context.close()
        finally:
            browser.close()


class ContactReadOnlyNetworkGuard:
    """Allow only fixed GET/HEAD documents and passive same-origin static files."""

    _PASSIVE_TYPES = frozenset({"script", "stylesheet", "image", "font", "media"})

    def __init__(self) -> None:
        self.non_read_seen = 0
        self.aborted = 0

    def handle(self, route: Any) -> None:
        request = route.request
        method = str(request.method or "").upper()
        if method not in {"GET", "HEAD"}:
            self.non_read_seen += 1
            self.aborted += 1
            route.abort("blockedbyclient")
            return
        parsed = urlsplit(str(request.url or ""))
        exact_origin = (
            parsed.scheme == "https"
            and (parsed.hostname or "").casefold() == ALLOWED_HOST
            and parsed.port in (None, 443)
            and not parsed.username and not parsed.password
        )
        resource_type = str(getattr(request, "resource_type", "") or "")
        document = resource_type == "document" and str(request.url or "") in CONTACT_READ_ONLY_URLS
        static = resource_type in self._PASSIVE_TYPES and any(
            (parsed.path or "/").startswith(prefix) for prefix in FNPT_STATIC_PATH_PREFIXES
        )
        if exact_origin and (document or static):
            route.continue_()
            return
        self.aborted += 1
        route.abort("blockedbyclient")

    def projection(self) -> dict[str, Any]:
        return {
            "allowed_methods": ["GET", "HEAD"],
            "all_non_read_requests_aborted": True,
            "all_analytics_requests_aborted": True,
            "analytics_submission_performed": False,
            "business_write_performed": False,
            "form_submission_performed": False,
        }


class ContactPublicProbe:
    """Read the exact Contact render and quote-route 404 without exposing body text."""

    def __init__(self, page: Any, guard: ContactReadOnlyNetworkGuard):
        self._page = page
        self.guard = guard
        self.page_errors = 0
        page.on("pageerror", self._page_error)

    def _page_error(self, _error: Any) -> None:
        self.page_errors += 1

    @staticmethod
    def _same_bare_url(actual: str, expected: str) -> bool:
        left, right = urlsplit(str(actual or "")), urlsplit(expected)
        return (
            left.scheme == right.scheme == "https"
            and (left.hostname or "").casefold() == ALLOWED_HOST
            and (right.hostname or "").casefold() == ALLOWED_HOST
            and left.port in (None, 443) and right.port in (None, 443)
            and left.path == right.path and not left.query and not left.fragment
        )

    def snapshot(self) -> dict[str, Any]:
        contact_response = self._page.goto(
            CONTACT_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS
        )
        self._page.wait_for_load_state("load", timeout=LOAD_STATE_TIMEOUT_MS)
        if contact_response is None or int(contact_response.status) != 200 \
                or not self._same_bare_url(str(self._page.url), CONTACT_URL):
            raise DeploymentError("REFUSED: the fixed public Contact page is not exact HTTP 200.")
        body = str(self._page.inner_text("body", timeout=ACTION_TIMEOUT_MS) or "")
        normal = re.sub(r"[\s\u00a0]+", " ", body).strip()
        structure = self._page.evaluate(
            """expected => {
              const normal=value => String(value || '').replace(/[\\s\\u00a0]+/g,' ').trim();
              const strong=Array.from(document.querySelectorAll('strong'))
                .filter(node => normal(node.textContent)===expected.strong);
              let sentence=0;
              strong.forEach(node => {
                let parent=node.parentElement;
                while (parent && parent!==document.body) {
                  if (normal(parent.textContent)===expected.sentence) { sentence += 1; break; }
                  parent=parent.parentElement;
                }
              });
              return {strong_count:strong.length,strong_sentence_count:sentence};
            }""",
            {"strong": CONTACT_TARGET_STRONG_TEXT, "sentence": CONTACT_TARGET_SENTENCE},
        )
        contact = {
            "status": 200,
            "path": "/contact/",
            "target_sentence_count": normal.count(CONTACT_TARGET_SENTENCE),
            "old_sentence_count": normal.count(CONTACT_OLD_SENTENCE),
            "strong_count": structure.get("strong_count") if isinstance(structure, dict) else None,
            "strong_sentence_count": (
                structure.get("strong_sentence_count") if isinstance(structure, dict) else None
            ),
        }
        if contact != {
            "status": 200, "path": "/contact/", "target_sentence_count": 1,
            "old_sentence_count": 0, "strong_count": 1, "strong_sentence_count": 1,
        }:
            raise DeploymentError("REFUSED: public Contact sentence or strong structure changed.")

        quote_response = self._page.goto(
            REQUEST_QUOTE_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS
        )
        self._page.wait_for_load_state("load", timeout=LOAD_STATE_TIMEOUT_MS)
        quote = {
            "status": int(quote_response.status) if quote_response is not None else None,
            "path": "/request-a-quote/" if self._same_bare_url(
                str(self._page.url), REQUEST_QUOTE_URL
            ) else None,
        }
        if quote != {"status": 404, "path": "/request-a-quote/"}:
            raise DeploymentError("REFUSED: /request-a-quote/ is not the protected HTTP 404.")
        if self.page_errors != 0:
            raise DeploymentError("REFUSED: a fixed public read raised an uncaught page error.")
        return {
            "contact": contact,
            "request_quote": quote,
            "network": self.guard.projection(),
            "page_error_count": 0,
        }


@contextlib.contextmanager
def contact_read_only_session() -> Iterator[ContactPublicProbe]:
    """One throwaway context whose router makes every non-read request impossible."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            context = browser.new_context()
            context.set_default_timeout(ACTION_TIMEOUT_MS)
            context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            guard = ContactReadOnlyNetworkGuard()
            context.route("**/*", guard.handle)
            try:
                yield ContactPublicProbe(context.new_page(), guard)
            finally:
                context.close()
        finally:
            browser.close()


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


def _fnpt_cache_buster_url(nonce: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", str(nonce or "")):
        raise DeploymentError("Plan nonce is not one exact lowercase 128-bit hex value.")
    return f"{FNPT_PRODUCT_URL}?{FNPT_CACHE_BUSTER_KEY}={nonce}"


def _fnpt_step(records: list[dict[str, Any]], step: str,
               action: Callable[[], Any]) -> Any:
    if step not in FNPT_PUBLIC_VALIDATION_STEPS:
        raise DeploymentError("Internal: unknown FNPT public-validation step.")
    started = time.monotonic()
    try:
        value = action()
    except FnptPublicValidationError:
        raise
    except FnptPublicRefusal as exc:
        raise FnptPublicValidationError(step, type(exc).__name__, exc.code) from exc
    except Exception as exc:  # noqa: BLE001 - permanently reduced to bounded metadata
        raise FnptPublicValidationError(
            step, type(exc).__name__, "unexpected_exception"
        ) from exc
    records.append({
        "step": step,
        "status": "passed",
        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
    })
    return value


def _exercise_all_fnpt_rows(page: FnptCustomerPage,
                            rows: dict[int, dict[str, Any]]) -> None:
    for variation_id in FNPT_PUBLISHED_VARIATION_IDS:
        row = rows[variation_id]
        page.require_selection_controls(2061, row)
        page.select_row(2061, row)
        page.require_freight_state(2061, variation_id, handoff=True)

    # Pin quantity, reset and hide transitions after a real fixed selection so
    # stale quote/native state cannot be hidden by a fresh page load.
    page.select_row(2061, rows[2088])
    page.quantity_transition("2")
    page.require_freight_state(2061, 2088, handoff=True, quantity="2")
    page.dispatch_lifecycle("reset_data")
    page._require_plugin_reset_state(2061)
    page.select_row(2061, rows[2088])
    page.dispatch_lifecycle("hide_variation")
    page._require_plugin_reset_state(2061)


def _exercise_fnpt_fail_closed_cases(page: FnptCustomerPage) -> None:
    fixed = {
        "frpdepot_quote_required": False,
        "frpdepot_product_id": 2061,
        "frpdepot_variation_id": 2088,
    }
    cases = (
        dict(fixed),
        {**fixed, "variation_id": 0, "frpdepot_variation_id": 0},
        {**fixed, "variation_id": "2088x", "frpdepot_variation_id": "2088x"},
        {**fixed, "variation_id": 2088, "frpdepot_variation_id": 2089},
        {"variation_id": 2088, "frpdepot_product_id": 2061,
         "frpdepot_quote_required": False},
        {"variation_id": 2088, "frpdepot_product_id": 2061,
         "frpdepot_variation_id": 2088},
        {**fixed, "variation_id": 2088, "frpdepot_quote_required": "false"},
    )
    for payload in cases:
        page.dispatch_lifecycle("reset_data")
        page.dispatch_found_variation(payload)
        page.require_freight_state(2061, 2088, handoff=False)
    page.dispatch_lifecycle("hide_variation")
    page._require_plugin_reset_state(2061)


def _exercise_stub_controls(page: FnptCustomerPage) -> None:
    page.load(STUB_PRODUCT_URL)
    page.require_release_shell()
    direct = page.fixed_control_row(1368, 2028, False)
    blocked = page.fixed_control_row(1368, 2044, True)
    page.require_selection_controls(1368, direct)
    page.require_selection_controls(1368, blocked)
    page.capture_unresolved_baseline(1368)

    # blocked -> direct -> blocked proves neither panel nor native button leaks
    # stale state across comparator transitions.
    page.select_row(1368, blocked)
    page.require_freight_state(1368, 2044, handoff=True)
    page.select_row(1368, direct)
    page.require_direct_state(2028)
    page.select_row(1368, blocked)
    page.require_freight_state(1368, 2044, handoff=True)
    page.dispatch_lifecycle("reset_data")
    page._require_plugin_reset_state(1368)


def _exercise_pipe_control(page: FnptCustomerPage) -> None:
    page.load(PIPE_PRODUCT_URL)
    page.require_release_shell()
    blocked = page.fixed_control_row(1455, 2057, True)
    page.require_selection_controls(1455, blocked)
    page.capture_unresolved_baseline(1455)
    page.select_row(1455, blocked)
    page.require_freight_state(1455, 2057, handoff=True)
    page.dispatch_lifecycle("hide_variation")
    page._require_plugin_reset_state(1455)


def _run_fnpt_public_validation(
    plan: dict[str, Any], *, release_version: str = FNPT_REPAIR_VERSION,
    js_sha256: str = FNPT_JS_SHA256, css_sha256: str = FNPT_CSS_SHA256,
) -> dict[str, Any]:
    """Run the complete post-upload acceptance in exactly two cold contexts."""
    records: list[dict[str, Any]] = []
    network: list[dict[str, Any]] = []
    page_errors: list[dict[str, Any]] = []
    cache_url = _fnpt_cache_buster_url(str(plan.get("nonce") or ""))
    session_options = {} if release_version == FNPT_REPAIR_VERSION else {
        "release_version": release_version,
        "js_sha256": js_sha256,
        "css_sha256": css_sha256,
    }

    with fnpt_anonymous_session(
        frozenset({cache_url, STUB_PRODUCT_URL, PIPE_PRODUCT_URL}), **session_options
    ) as page:
        _fnpt_step(records, "cache_buster_fnpt_load", lambda: page.load(cache_url))
        rows = _fnpt_step(
            records,
            "cache_buster_fnpt_contract",
            lambda: page.require_release_contract(
                2061, FNPT_PUBLISHED_VARIATION_IDS, expected_freight=True
            ),
        )
        _fnpt_step(
            records, "cache_buster_fnpt_variations",
            lambda: _exercise_all_fnpt_rows(page, rows),
        )
        _fnpt_step(
            records, "cache_buster_fail_closed_cases",
            lambda: _exercise_fnpt_fail_closed_cases(page),
        )
        _fnpt_step(
            records, "cache_buster_stub_controls",
            lambda: _exercise_stub_controls(page),
        )
        _fnpt_step(
            records, "cache_buster_pipe_control",
            lambda: _exercise_pipe_control(page),
        )
        page_errors.append({
            "context": "cache_buster",
            "pages": _fnpt_step(
                records, "cache_buster_page_errors", page.require_no_page_errors
            ),
        })
        network.append(page.guard.projection())

    with fnpt_anonymous_session(
        frozenset({FNPT_PRODUCT_URL, STUB_PRODUCT_URL, PIPE_PRODUCT_URL}), **session_options
    ) as page:
        _fnpt_step(records, "canonical_fnpt_load", lambda: page.load(FNPT_PRODUCT_URL))
        rows = _fnpt_step(
            records,
            "canonical_fnpt_contract",
            lambda: page.require_release_contract(
                2061, FNPT_PUBLISHED_VARIATION_IDS, expected_freight=True
            ),
        )
        _fnpt_step(
            records, "canonical_fnpt_variations",
            lambda: (
                _exercise_all_fnpt_rows(page, rows),
                _exercise_fnpt_fail_closed_cases(page),
                _exercise_stub_controls(page),
                _exercise_pipe_control(page),
            ),
        )
        page_errors.append({
            "context": "canonical",
            "pages": _fnpt_step(
                records, "canonical_page_errors", page.require_no_page_errors
            ),
        })
        network.append(page.guard.projection())

    def require_network_guard() -> None:
        if len(network) != 2 or len(page_errors) != 2:
            raise FnptPublicRefusal("context_count")
        expected_page_categories = ["fnpt_product", "stub_control", "pipe_control"]
        for index, context_name in enumerate(("cache_buster", "canonical")):
            context = page_errors[index]
            pages = context.get("pages")
            if (set(context) != {"context", "pages"}
                    or context.get("context") != context_name
                    or not isinstance(pages, list)
                    or [row.get("page_category") for row in pages]
                       != expected_page_categories):
                raise FnptPublicRefusal("page_error_guard_attribution")
            for row in pages:
                fixed_count = row.get("accepted_fixed_guard_error_count")
                abort_delta = row.get("off_origin_reads_aborted_delta")
                if (set(row) != {
                        "page_category", "accepted_fixed_guard_error_count",
                        "unclassified_error_count", "off_origin_reads_aborted_delta", "status",
                    }
                        or fixed_count not in {0, 1}
                        or type(abort_delta) is not int or abort_delta < 0
                        or row.get("unclassified_error_count") != 0
                        or row.get("status") != "passed"
                        or (fixed_count == 1 and abort_delta < 1)):
                    raise FnptPublicRefusal("page_error_guard_attribution")
        for projection in network:
            if (projection.get("allowed_methods") != ["GET", "HEAD"]
                    or projection.get("analytics_submission_performed") is not False
                    or projection.get("business_write_performed") is not False
                    or set(projection.get("non_read_method_counts") or {})
                       != set(FnptNetworkGuard._METHOD_BUCKETS)):
                raise FnptPublicRefusal("network_guard")

    _fnpt_step(records, "anonymous_network_guard", require_network_guard)
    variation_ids_bytes = json.dumps(
        list(FNPT_PUBLISHED_VARIATION_IDS), ensure_ascii=True,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return {
        "status": "PASSED",
        "contexts_opened": 2,
        "contexts_persistent": False,
        "admin_session_used": False,
        "cache_buster": {
            "query_key": FNPT_CACHE_BUSTER_KEY,
            "value_shape": "32_lowercase_hex",
            "unique_to_plan_nonce": True,
            "corrected_behavior": True,
        },
        "canonical_bare_product_url": True,
        "canonical_corrected_behavior": True,
        "release_version": release_version,
        "js_sha256": js_sha256,
        "css_sha256": css_sha256,
        "fnpt_parent_id": 2061,
        "fnpt_variation_ids": list(FNPT_PUBLISHED_VARIATION_IDS),
        "fnpt_variation_ids_sha256": hashlib.sha256(variation_ids_bytes).hexdigest(),
        "fnpt_variations_checked_per_context": len(FNPT_PUBLISHED_VARIATION_IDS),
        "fnpt_variation_selections_total": 2 * len(FNPT_PUBLISHED_VARIATION_IDS),
        "fixed_controls": list(FNPT_PUBLIC_CONTROL_IDS),
        "complete_fail_closed_stub_pipe_matrix_each_context": True,
        "native_button_computed_display_checked": True,
        "native_button_computed_visibility_checked": True,
        "owned_native_button_class_checked": FNPT_NATIVE_CONCEALMENT_CLASS,
        "non_submission": {
            "add_to_cart_clicked": False,
            "quote_or_contact_form_visited": False,
            "quote_or_contact_form_submitted": False,
            "cart_or_checkout_visited": False,
            "order_or_payment_created": False,
            "email_sent": False,
            "analytics_submission_performed": False,
            "cache_purge_or_invalidation_performed": False,
            "wordpress_or_woocommerce_write_performed": False,
        },
        "network": network,
        "page_errors": page_errors,
        "steps": records,
    }


def _run_fnpt_design_preflight() -> dict[str, Any]:
    """Fail staging if exact current public controls cannot support acceptance."""
    with fnpt_anonymous_session(
        frozenset({FNPT_PRODUCT_URL, STUB_PRODUCT_URL, PIPE_PRODUCT_URL})
    ) as page:
        page.load(FNPT_PRODUCT_URL)
        rows = page.variation_rows(
            2061, FNPT_PUBLISHED_VARIATION_IDS, expected_freight=None
        )
        for row in rows.values():
            page.require_selection_controls(2061, row)
        page.load(STUB_PRODUCT_URL)
        direct = page.fixed_control_row(1368, 2028, None)
        blocked = page.fixed_control_row(1368, 2044, None)
        page.require_selection_controls(1368, direct)
        page.require_selection_controls(1368, blocked)
        page.load(PIPE_PRODUCT_URL)
        pipe = page.fixed_control_row(1455, 2057, None)
        page.require_selection_controls(1455, pipe)
        projection = page.guard.projection()
    variation_ids_bytes = json.dumps(
        list(FNPT_PUBLISHED_VARIATION_IDS), ensure_ascii=True,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return {
        "status": "STRUCTURE_COMPATIBLE",
        "anonymous_contexts_opened": 1,
        "persistent_context": False,
        "fnpt_parent_id": 2061,
        "fnpt_variation_count": len(rows),
        "fnpt_variation_ids_sha256": hashlib.sha256(variation_ids_bytes).hexdigest(),
        "fixed_controls": list(FNPT_PUBLIC_CONTROL_IDS),
        "selection_controls_exact": True,
        "allowed_methods": projection["allowed_methods"],
        "non_read_requests_aborted_and_recorded": True,
        "analytics_submission_performed": False,
        "business_write_performed": False,
    }


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

FNPT_REPAIR_VALIDATION_CONTRACT = {
    "artifact_relation": "exact 2.0.7 artifact, size/member hashes, allowlist hash and narrow transform from exact active 2.0.6",
    "presentation_scope_only": True,
    "fnpt_parent_product_id": 2061,
    "fnpt_published_variation_ids": list(FNPT_PUBLISHED_VARIATION_IDS),
    "fnpt_published_variation_count": 60,
    "fnpt_regression_variation_id": 2088,
    "direct_checkout_control_variation_id": 2028,
    "oversized_control_variation_id": 2044,
    "pipe_control_variation_id": 2057,
    "inline_dataset_requires_exact_parent_variation_identity_and_freight_true": True,
    "missing_malformed_or_inconsistent_variation_id_fails_to_freight": True,
    "missing_malformed_or_inconsistent_variation_id_disables_quote_handoff": True,
    "selection_coverage": "all exact 60 live published FNPT IDs in each cold context",
    "native_add_to_cart_for_all_fnpt": "owned class present; hidden/disabled/ARIA/tabindex preserved; computed display none and not visible",
    "freight_panel_for_all_fnpt": "exactly one and visible",
    "direct_stub_2028": "direct/Add to Cart enabled; freight hidden",
    "oversized_stub_2044": "freight",
    "pipe_2057": "freight",
    "reset_hide_quantity_and_blocked_direct_blocked_leave_no_stale_state": True,
    "native_button_exactly_one_and_owned_class_has_no_spill": True,
    "direct_and_unresolved_restore_original_accessibility_state": True,
    "two_animation_frame_stable_state_required": True,
    "allowlisted_variations": 64,
    "researched_candidate_groups": 30,
    "physically_verified_groups": 0,
    "measurement_status": FNPT_REPAIR_MEASUREMENT_STATUS,
    "freight_class_still_overrides_allowlist": True,
    "unknown_mixed_custom_customer_specific_still_quote_only": True,
    "server_cart_checkout_controls_changed": False,
    "cart_quote_required_strict_boolean_consumer_changed": False,
    "cart_quote_required_strict_boolean_consumer_note": "pre-existing and out of this presentation release",
    "quote_form_contract_changed": False,
    "creates_shipping_rate": False,
    "uses_existing_woocommerce_ups_method": True,
    "product_price_stock_shipping_class_weight_dimensions_touched": False,
    "live_stage_is_read_only": True,
    "required_later_approval": "APPROVED",
    "approval_must_be_exact_and_unpadded_first": True,
    "plan_lifetime_hours": 24,
    "shared_wordpress_browser_lane_before_permanent_attempt_lock": True,
    "fresh_exact_active_2_0_6_fingerprint_before_attempt_lock": True,
    "customer_page_design_preflight_hashed_into_plan": True,
    "customer_page_design_preflight_repeated_before_attempt_lock": True,
    "site_level_atomic": False,
    "automatic_rollback": False,
    "one_upload_attempt": True,
    "retry_allowed": False,
    "overwrite_control_requires_exact_reviewed_url_shape": True,
    "upload_comparison_navigation_must_start_and_finish_bounded": True,
    "upload_comparison_final_url_requires_only_action_upload_plugin": True,
    "upload_comparison_http_status_required": 200,
    "overwrite_navigation_must_start_and_finish_bounded": True,
    "overwrite_final_url_requires_action_upload_plugin_and_overwrite_update_plugin": True,
    "overwrite_final_url_must_match_reviewed_package_and_nonce_in_memory": True,
    "url_fragments_and_duplicate_query_values_refused": True,
    "overwrite_http_status_required": 200,
    "wordpress_success_marker_required_exactly_once": OVERWRITE_SUCCESS_MARKER,
    "nonce_and_package_values_never_logged_or_planned": True,
    "post_write_plugin_row_required": "version 2.0.7 and active",
    "public_validation_same_command_while_plan_permanently_locked": True,
    "anonymous_contexts_exact": 2,
    "each_context_runs_all_fnpt_fail_closed_stub_and_pipe_cases": True,
    "anonymous_contexts_throwaway_nonpersistent": True,
    "admin_cookies_or_storage_in_anonymous_contexts": False,
    "anonymous_allowed_methods": ["GET", "HEAD"],
    "every_non_get_head_aborted_and_recorded": True,
    "analytics_submission_allowed": False,
    "customer_business_submission_allowed": False,
    "cache_purge_or_invalidation_authorized": False,
    "cache_purge_or_invalidation_included": False,
    "cache_correctness_proof": {
        "context_1": "unique ?frpdepot_fnpt_verify=<32 lowercase hex plan nonce> FNPT product URL",
        "context_2": "canonical bare FNPT product URL",
        "both_require_corrected_behavior": True,
        "canonical_stale_content_is_indeterminate": True,
        "html_requires_one_2_0_7_panel_contract": True,
        "asset_urls_require_exact_ver": FNPT_REPAIR_VERSION,
        "javascript_sha256": FNPT_JS_SHA256,
        "css_sha256": FNPT_CSS_SHA256,
        "config_requires_exact_eight_keys_and_wp_localize_string_scalars": True,
        "all_eight_localized_value_types_checked": True,
    },
    "uncaught_page_errors_allowed": False,
    "public_validation_steps": list(FNPT_PUBLIC_VALIDATION_STEPS),
    "on_upload_row_or_public_failure": "permanent indeterminate lock/result with bounded step/code; no retry; no rollback",
    "success_status": "COMMITTED_AND_VERIFIED",
    "success_pending_status_allowed": False,
    "failure_record_contains_page_or_customer_text": False,
}

CONTACT_PRESERVE_VALIDATION_CONTRACT = {
    "transition": "exact active 2.0.7 to exact active 2.0.8",
    "active_to_active_wordpress_upload_overwrite": True,
    "deactivation_allowed": False,
    "artifact_and_installed_member_order_and_hashes_exact": True,
    "stage_is_read_only": True,
    "required_later_approval": "APPROVED",
    "approval_must_be_exact_and_unpadded_first": True,
    "plan_lifetime_hours": 24,
    "newer_plan_supersedes_older_plan": True,
    "single_use_permanent_attempt_lock": True,
    "shared_wordpress_mutex_before_attempt_lock": True,
    "browser_busy_is_free_refusal": True,
    "fresh_complete_snapshot_must_equal_staged_snapshot_before_attempt_lock": True,
    "source_contact_form_id": SOURCE_CONTACT_FORM_ID,
    "source_notification_name": "Admin Notification",
    "source_route_privacy_projected_hash_only": True,
    "freight_status_required_before": "not_applied; zero transaction state; contact counts 0/0",
    "freight_status_required_after": "same zero transaction state; contact counts 0/1",
    "freight_spec_sha256": FREIGHT_SPEC_SHA256,
    "contact_id": FREIGHT_CONTACT_ID,
    "contact_sha256_must_not_change": True,
    "public_contact_sentence_once_old_zero_and_strong_structure_once": True,
    "request_a_quote_required_http_status": 404,
    "wordpress_upload_attempts": 1,
    "automatic_rollback": False,
    "retry_after_upload_failure": False,
    "failure_after_upload": "permanently indeterminate",
    "post_write_installed_member_projection_required": True,
    "post_write_fnpt_cold_validation": "existing complete GET/HEAD-only acceptance at 2.0.8",
    "form_submit_email_order_cart_customer_product_zoho_write_allowed": False,
    "gravity_forms_form_page_contact_transaction_write_allowed": False,
    "success_status": "COMMITTED_AND_VERIFIED",
}

CONTACT_SNAPSHOT_KEYS = frozenset({
    "plugin_row", "installed_members", "freight_status", "source_notification_route",
    "public",
})
CONTACT_INSTALLED_KEYS = frozenset({"members", "member_sha256", "source_projected", "read_only"})
CONTACT_ROUTE_KEYS = frozenset({
    "source_form_id", "source_form_title_match", "source_notification_name_match",
    "active_notification_match_count", "route_shape_valid", "route_sha256", "privacy",
})
CONTACT_PUBLIC_KEYS = frozenset({"contact", "request_quote", "network", "page_error_count"})


def _require_freight_zero_status(status: Any, *, contact_new_count: int) -> None:
    """Require the exact no-transaction status; only the 2.0.8 display count may differ."""
    empty_hash = digest_for(None)
    if not isinstance(status, dict) or set(status) != FREIGHT_STATUS_KEYS:
        raise DeploymentError("REFUSED: freight zero-write status schema changed.")
    if status.get("spec_sha256") != FREIGHT_SPEC_SHA256 \
            or status.get("status") != "not_applied" \
            or status.get("deployment_id") != "0" * 32 \
            or status.get("source_form_id") != SOURCE_CONTACT_FORM_ID \
            or status.get("source_notification_name_match") is not False \
            or status.get("route_sha256") != empty_hash:
        raise DeploymentError("REFUSED: freight deployment state is not exact not_applied zero-write.")
    if (status.get("form_id") != 0 or status.get("page_id") != 0
            or status.get("form_owned") is not False or status.get("page_owned") is not False
            or status.get("form_sha256") != empty_hash or status.get("page_sha256") != empty_hash
            or status.get("contact_id") != FREIGHT_CONTACT_ID
            or status.get("contact_old_count") != 0
            or status.get("contact_new_count") != contact_new_count
            or not HEX_SHA256.fullmatch(str(status.get("contact_sha256") or ""))):
        raise DeploymentError("REFUSED: freight form/page/Contact zero-write projection changed.")
    if any(status.get(key) is not False for key in FREIGHT_BACKUP_STATUS_KEYS):
        raise DeploymentError("REFUSED: a freight backup exists; this repair cannot cross that state.")
    if (status.get("receipt_count") != 0
            or status.get("receipt_schema_valid") is not False
            or status.get("receipt_chain_valid") is not False
            or status.get("receipt_append_only") is not False
            or status.get("receipt_head_sha256") != empty_hash
            or status.get("apply_receipt_head_sha256") != empty_hash
            or status.get("rollback_drift_free") is not False
            # The fixed 2.0.7/2.0.8 PHP status projection serializes the
            # absent rollback artifact as the exact empty string.  This was
            # measured live on 2026-08-15 and is also the literal initialized
            # in both frozen plugin sources.  Require that one representation;
            # None or any non-empty artifact is not the exact live zero state.
            or status.get("rollback_blocked_artifact") != ""):
        raise DeploymentError("REFUSED: freight receipts or rollback state are not absent.")
    if any(status.get(key) != empty_hash for key in (
        "form_before_sha256", "quote_page_before_sha256", "contact_before_sha256"
    )) or status.get("privacy") != FREIGHT_PRIVACY_STATUS:
        raise DeploymentError("REFUSED: freight baseline or privacy projection changed.")


def require_contact_preserve_eligibility(snapshot: Any) -> None:
    """One normalized eligibility predicate for stage, commit and upload adapter."""
    if not isinstance(snapshot, dict) or set(snapshot) != CONTACT_SNAPSHOT_KEYS:
        raise DeploymentError("REFUSED: Contact preservation snapshot schema changed.")
    if snapshot.get("plugin_row") != project_row(
        True, True, CONTACT_PRESERVE_FROM_VERSION, False
    ):
        raise DeploymentError("REFUSED: plugin row is not exact active 2.0.7 with no update marker.")
    installed = snapshot.get("installed_members")
    if (not isinstance(installed, dict) or set(installed) != CONTACT_INSTALLED_KEYS
            or installed.get("members") != list(CONTACT_PRESERVE_BASELINE_MEMBERS)
            or installed.get("member_sha256") != CONTACT_PRESERVE_BASELINE_MEMBER_SHA256
            or installed.get("source_projected") is not False
            or installed.get("read_only") is not True):
        raise DeploymentError("REFUSED: installed plugin members are not the exact 2.0.7 baseline.")
    _require_freight_zero_status(snapshot.get("freight_status"), contact_new_count=0)
    route = snapshot.get("source_notification_route")
    if (not isinstance(route, dict) or set(route) != CONTACT_ROUTE_KEYS
            or route.get("source_form_id") != SOURCE_CONTACT_FORM_ID
            or route.get("source_form_title_match") is not True
            or route.get("source_notification_name_match") is not True
            or route.get("active_notification_match_count") != 1
            or route.get("route_shape_valid") is not True
            or not HEX_SHA256.fullmatch(str(route.get("route_sha256") or ""))
            or route.get("privacy") != FREIGHT_PRIVACY_STATUS):
        raise DeploymentError("REFUSED: Contact Form 1 notification route is not exact and private.")
    _require_contact_public_snapshot(snapshot.get("public"))


def _require_contact_public_snapshot(public: Any) -> None:
    expected_network = ContactReadOnlyNetworkGuard().projection()
    if (not isinstance(public, dict) or set(public) != CONTACT_PUBLIC_KEYS
            or public.get("contact") != {
                "status": 200, "path": "/contact/", "target_sentence_count": 1,
                "old_sentence_count": 0, "strong_count": 1, "strong_sentence_count": 1,
            }
            or public.get("request_quote") != {"status": 404, "path": "/request-a-quote/"}
            or public.get("network") != expected_network
            or public.get("page_error_count") != 0):
        raise DeploymentError("REFUSED: protected public Contact/quote projection changed.")


def require_contact_preserve_postcondition(before: Any, after: Any) -> None:
    """Prove the overwrite changed only version/member hashes and the fixed display count."""
    if not isinstance(after, dict) or set(after) != CONTACT_SNAPSHOT_KEYS:
        raise DeploymentError("INDETERMINATE: post-upload snapshot schema changed.")
    if after.get("plugin_row") != project_row(True, True, CONTACT_PRESERVE_VERSION, False):
        raise DeploymentError("INDETERMINATE: plugin is not exact active 2.0.8 without update marker.")
    installed = after.get("installed_members")
    if (not isinstance(installed, dict) or set(installed) != CONTACT_INSTALLED_KEYS
            or installed.get("members") != list(CONTACT_PRESERVE_MEMBERS)
            or installed.get("member_sha256") != CONTACT_PRESERVE_MEMBER_SHA256
            or installed.get("source_projected") is not False
            or installed.get("read_only") is not True):
        raise DeploymentError("INDETERMINATE: installed members are not exact 2.0.8.")
    _require_freight_zero_status(after.get("freight_status"), contact_new_count=1)
    expected_status = dict(before["freight_status"])
    expected_status["contact_new_count"] = 1
    if after.get("freight_status") != expected_status:
        raise DeploymentError("INDETERMINATE: protected freight state changed beyond display count.")
    if after.get("source_notification_route") != before.get("source_notification_route"):
        raise DeploymentError("INDETERMINATE: source Contact notification route changed.")
    _require_contact_public_snapshot(after.get("public"))
    if after.get("public") != before.get("public"):
        raise DeploymentError("INDETERMINATE: public Contact structure or quote 404 changed.")


PLAN_KEYS = frozenset({
    "schema_version", "tool", "tool_version", "origin", "action", "created_utc", "expires_utc", "nonce",
    "plugin_name", "plugin_slug", "plugin_file", "artifact", "before", "after_expected",
    "validation", "preflight",
})
PLAN_PREFLIGHT_KEYS = frozenset({"path", "sha256", "created_utc", "runs"})
FNPT_DESIGN_PREFLIGHT_KEYS = frozenset({
    "status", "anonymous_contexts_opened", "persistent_context", "fnpt_parent_id",
    "fnpt_variation_count", "fnpt_variation_ids_sha256", "fixed_controls",
    "selection_controls_exact", "allowed_methods",
    "non_read_requests_aborted_and_recorded", "analytics_submission_performed",
    "business_write_performed",
})


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
        "tool_version": TOOL_VERSION,
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
            else dict(FNPT_REPAIR_VALIDATION_CONTRACT) if action == "plugin_fnpt_display_repair"
            else dict(CONTACT_PRESERVE_VALIDATION_CONTRACT)
            if action == "plugin_freight_contact_preserve_repair"
            else None
        ),
        "preflight": dict(preflight) if action in {
            "plugin_activate", "plugin_fnpt_display_repair",
            "plugin_freight_contact_preserve_repair",
        } else None,
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{action}_{digest[:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_receipt("wordpress_plugin_plan_staged", str(path))
    return path


def _require_contact_plan_not_superseded(path: Path, plan: dict[str, Any]) -> None:
    """Any later intact plan for this one action permanently supersedes this one."""
    try:
        created = datetime.fromisoformat(str(plan["created_utc"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise DeploymentError("Contact preservation plan creation time is invalid.") from exc
    for candidate_path in PLAN_DIR.glob("*_plugin_freight_contact_preserve_repair_*.json"):
        if candidate_path.resolve() == path.resolve():
            continue
        try:
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            if not isinstance(candidate, dict) or set(candidate) != {*PLAN_KEYS, "sha256"}:
                continue
            saved = str(candidate.get("sha256") or "")
            core = {key: value for key, value in candidate.items() if key != "sha256"}
            candidate_created = datetime.fromisoformat(str(candidate.get("created_utc")))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if (not saved or not secrets.compare_digest(saved, digest_for(core))
                or candidate.get("schema_version") != SCHEMA_VERSION
                or candidate.get("tool") != TOOL_NAME
                or candidate.get("tool_version") != TOOL_VERSION
                or candidate.get("origin") != EXACT_ORIGIN
                or candidate.get("action") != "plugin_freight_contact_preserve_repair"
                or candidate.get("plugin_file") != PLUGIN_FILE):
            continue
        if candidate_created > created:
            raise DeploymentError(
                "This Contact preservation plan was superseded by a newer immutable plan."
            )


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
            or plan["tool_version"] != TOOL_VERSION or plan["origin"] != EXACT_ORIGIN):
        raise DeploymentError("The plan schema, tool version, tool, or origin is invalid.")
    if not re.fullmatch(r"[0-9a-f]{32}", str(plan["nonce"] or "")):
        raise DeploymentError("The plan nonce is not one exact lowercase 128-bit hex value.")
    if (plan["plugin_name"] != PLUGIN_NAME or plan["plugin_slug"] != PLUGIN_SLUG
            or plan["plugin_file"] != PLUGIN_FILE):
        raise DeploymentError("The plan does not name the fixed FRP Depot plugin.")
    action = str(plan["action"])
    if action not in ACTIONS:
        raise DeploymentError("The plan action is not allowlisted.")
    try:
        created = datetime.fromisoformat(str(plan["created_utc"]))
        expires = datetime.fromisoformat(str(plan["expires_utc"]))
    except (TypeError, ValueError) as exc:
        raise DeploymentError("Plan creation or expiry is invalid.") from exc
    if (created.tzinfo is None or expires.tzinfo is None
            or created.utcoffset() != timedelta(0) or expires.utcoffset() != timedelta(0)):
        raise DeploymentError("Plan creation and expiry must be explicit UTC timestamps.")
    if utc_now() >= expires:
        raise DeploymentError("Plan expired. Stage a new plan for review.")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise DeploymentError("Plan expiry does not match the immutable 24-hour lifetime.")
    if created > utc_now() + timedelta(seconds=5):
        raise DeploymentError("Plan creation time is in the future.")

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
    elif action == "plugin_fnpt_display_repair":
        if not isinstance(artifact, dict) or set(artifact) != {
            "path", "sha256", "version", "members", "member_sha256",
            "allowlist_sha256", "baseline_path", "baseline_sha256", "bytes"
        }:
            raise DeploymentError("A FNPT display repair plan must carry the closed artifact record.")
        if (artifact["sha256"] != FNPT_REPAIR_SHA256
                or artifact["version"] != FNPT_REPAIR_VERSION
                or Path(artifact["path"]).resolve() != Path(FNPT_REPAIR_ARTIFACT_PATH).resolve()
                or tuple(artifact["members"]) != tuple(sorted(FNPT_REPAIR_MEMBERS))
                or artifact["member_sha256"] != FNPT_REPAIR_MEMBER_SHA256
                or artifact["allowlist_sha256"] != FNPT_REPAIR_ALLOWLIST_SHA256
                or Path(artifact["baseline_path"]).resolve() != Path(FNPT_REPAIR_BASELINE_PATH).resolve()
                or artifact["baseline_sha256"] != FNPT_REPAIR_BASELINE_SHA256
                or artifact["bytes"] != FNPT_REPAIR_BYTES):
            raise DeploymentError("REFUSED: the plan artifact is not the fixed FNPT display repair.")
        fixed_before = project_row(True, True, FNPT_REPAIR_FROM_VERSION, False)
        fixed_after = project_row(True, True, FNPT_REPAIR_VERSION, False)
        if plan["before"] != fixed_before or plan["after_expected"] != fixed_after:
            raise DeploymentError(
                "REFUSED: FNPT display repair is only exact active 2.0.6 to exact active 2.0.7."
            )
        expected = _expected_after("plugin_fnpt_display_repair", plan["before"])
    elif action == "plugin_freight_contact_preserve_repair":
        artifact_keys = {
            "path", "sha256", "version", "bytes", "members", "member_sha256",
            "baseline_path", "baseline_version", "baseline_sha256", "baseline_bytes",
            "baseline_members", "baseline_member_sha256",
        }
        if not isinstance(artifact, dict) or set(artifact) != artifact_keys:
            raise DeploymentError("A Contact preservation plan must carry the closed ZIP pair record.")
        if (Path(artifact["path"]).resolve() != Path(CONTACT_PRESERVE_ARTIFACT_PATH).resolve()
                or artifact["sha256"] != CONTACT_PRESERVE_SHA256
                or artifact["version"] != CONTACT_PRESERVE_VERSION
                or artifact["bytes"] != CONTACT_PRESERVE_BYTES
                or artifact["members"] != list(CONTACT_PRESERVE_MEMBERS)
                or artifact["member_sha256"] != CONTACT_PRESERVE_MEMBER_SHA256
                or Path(artifact["baseline_path"]).resolve()
                   != Path(CONTACT_PRESERVE_BASELINE_PATH).resolve()
                or artifact["baseline_version"] != CONTACT_PRESERVE_FROM_VERSION
                or artifact["baseline_sha256"] != CONTACT_PRESERVE_BASELINE_SHA256
                or artifact["baseline_bytes"] != CONTACT_PRESERVE_BASELINE_BYTES
                or artifact["baseline_members"] != list(CONTACT_PRESERVE_BASELINE_MEMBERS)
                or artifact["baseline_member_sha256"]
                   != CONTACT_PRESERVE_BASELINE_MEMBER_SHA256):
            raise DeploymentError("REFUSED: plan does not pin the exact 2.0.7/2.0.8 ZIP pair.")
        if (plan["before"] != project_row(True, True, CONTACT_PRESERVE_FROM_VERSION, False)
                or plan["after_expected"]
                   != project_row(True, True, CONTACT_PRESERVE_VERSION, False)):
            raise DeploymentError("REFUSED: Contact repair is only exact active 2.0.7 to 2.0.8.")
        expected = _expected_after(action, plan["before"])
    elif artifact is not None:
        raise DeploymentError(
            "Only a replace or FNPT display repair plan, or the fixed Contact preservation "
            "repair plan, may carry an artifact."
        )
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
    elif action == "plugin_fnpt_display_repair":
        if plan["validation"] != FNPT_REPAIR_VALIDATION_CONTRACT:
            raise DeploymentError("The FNPT display repair plan disclosure/verification contract changed.")
        if not isinstance(preflight, dict) or set(preflight) != FNPT_DESIGN_PREFLIGHT_KEYS:
            raise DeploymentError(
                "A FNPT display repair plan must carry the closed customer-page design preflight."
            )
        variation_ids_bytes = json.dumps(
            list(FNPT_PUBLISHED_VARIATION_IDS), ensure_ascii=True,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        if (preflight.get("status") != "STRUCTURE_COMPATIBLE"
                or preflight.get("anonymous_contexts_opened") != 1
                or preflight.get("persistent_context") is not False
                or preflight.get("fnpt_parent_id") != 2061
                or preflight.get("fnpt_variation_count") != len(FNPT_PUBLISHED_VARIATION_IDS)
                or preflight.get("fnpt_variation_ids_sha256")
                   != hashlib.sha256(variation_ids_bytes).hexdigest()
                or preflight.get("fixed_controls") != list(FNPT_PUBLIC_CONTROL_IDS)
                or preflight.get("selection_controls_exact") is not True
                or preflight.get("allowed_methods") != ["GET", "HEAD"]
                or preflight.get("non_read_requests_aborted_and_recorded") is not True
                or preflight.get("analytics_submission_performed") is not False
                or preflight.get("business_write_performed") is not False):
            raise DeploymentError("The FNPT customer-page design preflight is not exact.")
    elif action == "plugin_freight_contact_preserve_repair":
        if plan["validation"] != CONTACT_PRESERVE_VALIDATION_CONTRACT:
            raise DeploymentError("The Contact preservation disclosure contract changed.")
        require_contact_preserve_eligibility(preflight)
        _require_contact_plan_not_superseded(Path(path), plan)
    else:
        if plan["validation"] is not None:
            raise DeploymentError("Only fixed guarded actions may carry validation contracts.")
        if preflight is not None:
            raise DeploymentError(
                "Only an activation plan, FNPT display repair plan, or Contact preservation "
                "repair plan may carry preflight evidence."
            )
    plan["sha256"] = saved
    return plan


def _expected_after(action: str, before: dict[str, Any]) -> dict[str, Any]:
    if action == "plugin_replace":
        return project_row(True, False, ARTIFACT_VERSION, False)
    if action == "plugin_fnpt_display_repair":
        return project_row(True, True, FNPT_REPAIR_VERSION, False)
    if action == "plugin_freight_contact_preserve_repair":
        return project_row(True, True, CONTACT_PRESERVE_VERSION, False)
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
@holds_wordpress_browser("WordPress: read fixed plugin row only")
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


def _contact_public_snapshot() -> dict[str, Any]:
    with contact_read_only_session() as public:
        return public.snapshot()


def _contact_admin_snapshot(admin: AdminPage) -> dict[str, Any]:
    admin.goto_plugins()
    return {
        "plugin_row": admin.read_row(),
        "installed_members": admin.read_installed_member_projection(),
        "freight_status": admin.read_freight_status(),
        "source_notification_route": admin.read_source_notification_route_projection(),
    }


def _join_contact_snapshot(admin_projection: dict[str, Any],
                           public_projection: dict[str, Any]) -> dict[str, Any]:
    return {**admin_projection, "public": public_projection}


def _stage_and_report(action: str, before: dict[str, Any],
                      artifact: dict[str, Any] | None,
                      preflight: dict[str, Any] | None = None,
                      design_preflight: dict[str, Any] | None = None) -> None:
    after_expected = _expected_after(action, before)
    closed_preflight = (
        design_preflight if action == "plugin_fnpt_display_repair" else preflight
    )
    path = stage_plan(action, before, after_expected, artifact, closed_preflight)
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
        "design_preflight": plan["preflight"] if action == "plugin_fnpt_display_repair" else None,
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


@holds_wordpress_browser("WordPress: read row and stage 2.0.7 FNPT plan")
def command_stage_fnpt_display_repair(_: argparse.Namespace) -> None:
    artifact = verify_fnpt_display_repair_artifact()
    before = _live_row()
    if before["version"] == FNPT_REPAIR_VERSION:
        raise DeploymentError(
            f"No change is needed: FNPT display repair version {FNPT_REPAIR_VERSION} is already installed. "
            "Nothing was staged."
        )
    if before["version"] != FNPT_REPAIR_FROM_VERSION:
        raise DeploymentError(
            f"REFUSED: the installed version is {before['version']!r}, not the exact frozen "
            f"baseline {FNPT_REPAIR_FROM_VERSION}. Nothing was staged."
        )
    if before["active"] is not True:
        raise DeploymentError(
            f"REFUSED: the fixed {FNPT_REPAIR_FROM_VERSION} plugin is not active. The FNPT display repair is an "
            "active-to-active replacement only; nothing was staged."
        )
    if before != project_row(True, True, FNPT_REPAIR_FROM_VERSION, False):
        raise DeploymentError(
            "REFUSED: the plugin row is not the exact active 2.0.6 baseline projection "
            "(including no pending update marker). Nothing was staged."
        )
    try:
        design_preflight = _run_fnpt_design_preflight()
    except FnptPublicRefusal as exc:
        raise DeploymentError(
            f"REFUSED: exact live customer-page structure is incompatible ({exc.code}); "
            "nothing was staged."
        ) from exc
    except Exception as exc:
        raise DeploymentError(
            "REFUSED: exact live customer-page structure could not be proven compatible; "
            "nothing was staged."
        ) from exc
    _stage_and_report(
        "plugin_fnpt_display_repair", before, artifact,
        design_preflight=design_preflight,
    )


def command_stage_freight_contact_preserve_repair(_: argparse.Namespace) -> None:
    """Read-only exact live snapshot; never uploads, saves a form, or edits a post."""
    artifact = verify_contact_preserve_artifact()
    with ui_browser_lock(
        "wordpress", purpose="WordPress: read-only stage freight Contact preservation repair"
    ):
        public_projection = _contact_public_snapshot()
        with admin_session() as admin:
            snapshot = _join_contact_snapshot(
                _contact_admin_snapshot(admin), public_projection
            )
        require_contact_preserve_eligibility(snapshot)
        _stage_and_report(
            "plugin_freight_contact_preserve_repair",
            snapshot["plugin_row"], artifact, preflight=snapshot,
        )


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
    # The approval gate is deliberately the first executable gate. Whitespace,
    # a near miss or any other value refuses before even reading a plan path.
    require_rachad_approval(args.approval)
    plan_path = resolve_plan_path(args.plan)
    plan = load_plan(str(plan_path))
    if plan["action"] != action:
        raise DeploymentError(f"This plan is a {plan['action']} plan, not {action}.")
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


def command_commit_fnpt_display_repair(args: argparse.Namespace) -> None:
    # Local approval/schema/tool/artifact gates all run before the shared browser
    # lane. In particular every schema-6/schema-7/schema-8 plan is a free local refusal: no browser,
    # admin page, network request, attempt lock or website write can occur.
    plan_path, plan = _open_commit(args, "plugin_fnpt_display_repair")
    artifact = verify_fnpt_display_repair_artifact(Path(plan["artifact"]["path"]))
    if artifact["sha256"] != plan["artifact"]["sha256"]:
        raise DeploymentError("REFUSED: the FNPT display repair artifact no longer matches the approved plan.")
    lock = lock_path(plan_path)

    # An approved schema-9 attempt takes the shared lane before the fresh exact
    # 2.0.6 fingerprint and before the permanent lock. The lane remains held
    # through the one upload, row readback and both cold anonymous contexts.
    with ui_browser_lock(
        "wordpress", purpose="WordPress: apply and fully verify active 2.0.7 FNPT display repair"
    ):
        try:
            fresh_design_preflight = _run_fnpt_design_preflight()
        except FnptPublicRefusal as exc:
            raise DeploymentError(
                f"REFUSED: the customer-page design preflight drifted ({exc.code}); "
                "nothing was written and the plan remains unused."
            ) from exc
        except Exception as exc:
            raise DeploymentError(
                "REFUSED: the fresh customer-page design preflight could not be proven; "
                "nothing was written and the plan remains unused."
            ) from exc
        if fresh_design_preflight != plan["preflight"]:
            raise DeploymentError(
                "REFUSED: the fresh customer-page design preflight differs from the immutable "
                "staged record. Nothing was written and the plan remains unused."
            )
        with admin_session() as admin:
            _verify_pre_state(admin, plan)
            write_lock(lock, {
                "plan_sha256": plan["sha256"], "status": "in_flight",
                "started_utc": utc_now().isoformat(), "stage": "fnpt_display_repair_upload",
            }, exclusive=True)
            try:
                comparison = admin.upload_fnpt_display_repair(Path(artifact["path"]))
                admin.goto_plugins()
                after = admin.read_row()
            except Exception as exc:
                _burn(lock, plan, plan_path, exc, "fnpt_display_repair_upload")
                raise IndeterminateError(
                    "The FNPT display repair upload or row read-back is unverified. The plan is permanently "
                    "locked with no retry and no rollback; inspect the fixed Plugins row."
                ) from exc

        if after != plan["after_expected"]:
            write_lock(lock, {
                "plan_sha256": plan["sha256"], "status": "indeterminate",
                "updated_utc": utc_now().isoformat(), "after": after,
                "stage": "fnpt_display_repair_row_readback", "retry": False,
                "rollback": False,
            })
            record_result(plan_path, plan, "INDETERMINATE", {
                "after": after, "retry": False, "rollback": False,
                "stage": "fnpt_display_repair_row_readback",
            })
            raise IndeterminateError(
                "The fixed plugin row does not exactly read back as active version 2.0.7. "
                "The plan is permanently locked with no retry and no rollback."
            )

        # Row verification is not success. Keep the already-permanent attempt lock
        # in flight while this same command proves the cache-buster and canonical
        # product pages independently in two fresh anonymous contexts.
        try:
            findings = _run_fnpt_public_validation(plan)
        except Exception as exc:  # noqa: BLE001 - permanently reduced to fixed metadata
            if isinstance(exc, FnptPublicValidationError):
                attribution = {
                    "step": exc.step,
                    "exception_class": exc.exception_class,
                    "code": exc.code,
                }
            else:
                attribution = {
                    "step": "cache_buster_fnpt_load",
                    "exception_class": type(exc).__name__,
                    "code": "unexpected_exception",
                }
            write_lock(lock, {
                "plan_sha256": plan["sha256"], "status": "indeterminate",
                "updated_utc": utc_now().isoformat(), "after": after,
                "stage": "fnpt_public_validation", "retry": False,
                "rollback": False, **attribution,
            })
            record_result(plan_path, plan, "INDETERMINATE", {
                "after": after, "stage": "fnpt_public_validation",
                "retry": False, "rollback": False, **attribution,
            })
            raise IndeterminateError(
                "Cold anonymous FNPT validation did not complete exactly. The upload attempt is "
                "permanently indeterminate with no retry and no rollback."
            ) from exc

        write_lock(lock, {
            "plan_sha256": plan["sha256"], "status": "committed_verified",
            "updated_utc": utc_now().isoformat(), "after": after,
            "public_validation": "passed",
        })
        record_result(plan_path, plan, "COMMITTED_AND_VERIFIED", {
            "after": after,
            "comparison": comparison,
            "active_before_and_after": True,
            "public_validation": findings,
            "automatic_rollback": False,
        })
        emit({
            "status": "COMMITTED_AND_VERIFIED",
            "action": "plugin_fnpt_display_repair",
            "plugin_file": PLUGIN_FILE,
            "installed_version": after["version"],
            "active": after["active"],
            "comparison": comparison,
            "public_validation": findings,
            "automatic_rollback": False,
            "plan_sha256": plan["sha256"],
            "replay_locked": True,
        })


def _burn_contact_attempt(lock: Path, plan: dict[str, Any], plan_path: Path,
                          exc: Exception, stage: str) -> None:
    detail = {
        "stage": stage,
        "reason": type(exc).__name__,
        "retry": False,
        "rollback": False,
    }
    write_lock(lock, {
        "plan_sha256": plan["sha256"], "status": "indeterminate",
        "updated_utc": utc_now().isoformat(), **detail,
    })
    record_result(plan_path, plan, "INDETERMINATE", detail)


def command_commit_freight_contact_preserve_repair(args: argparse.Namespace) -> None:
    """One active 2.0.7 -> 2.0.8 overwrite, then protected zero-write verification."""
    # Exact unpadded approval, immutable plan/supersession, and both local ZIPs
    # are proved before any browser or shared WordPress mutex is touched.
    plan_path, plan = _open_commit(args, "plugin_freight_contact_preserve_repair")
    artifact = verify_contact_preserve_artifact(Path(plan["artifact"]["path"]))
    if artifact != plan["artifact"]:
        raise DeploymentError("REFUSED: fixed 2.0.7/2.0.8 artifact record changed after review.")
    lock = lock_path(plan_path)

    with ui_browser_lock(
        "wordpress", purpose="WordPress: apply active 2.0.8 Contact preservation repair"
    ):
        # Public first, admin last. This permits two non-nested Playwright contexts
        # and keeps the same authenticated admin session from final preflight
        # through the attempt lock and the one upload.
        public_before = _contact_public_snapshot()
        with admin_session() as admin:
            admin_before = _contact_admin_snapshot(admin)
            fresh = _join_contact_snapshot(admin_before, public_before)
            require_contact_preserve_eligibility(fresh)
            if fresh != plan["preflight"]:
                raise DeploymentError(
                    "REFUSED: fresh complete preflight differs from the immutable staged snapshot. "
                    "Nothing was written and the plan remains unused."
                )
            write_lock(lock, {
                "plan_sha256": plan["sha256"], "status": "in_flight",
                "started_utc": utc_now().isoformat(),
                "stage": "freight_contact_preserve_upload",
            }, exclusive=True)
            try:
                comparison = admin.upload_freight_contact_preserve_repair(
                    Path(artifact["path"]), fresh
                )
                admin_after = _contact_admin_snapshot(admin)
            except Exception as exc:  # noqa: BLE001 - permanent bounded attribution
                _burn_contact_attempt(
                    lock, plan, plan_path, exc, "freight_contact_preserve_upload_or_admin_readback"
                )
                raise IndeterminateError(
                    "The one 2.0.8 upload or protected admin read-back is unverified. The plan is "
                    "permanently indeterminate with no retry, no deactivation and no rollback."
                ) from exc

        try:
            public_after = _contact_public_snapshot()
            after = _join_contact_snapshot(admin_after, public_after)
            require_contact_preserve_postcondition(fresh, after)
            fnpt_findings = _run_fnpt_public_validation(
                plan, release_version=CONTACT_PRESERVE_VERSION,
                js_sha256=CONTACT_PRESERVE_MEMBER_SHA256[
                    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.js"
                ],
                css_sha256=CONTACT_PRESERVE_MEMBER_SHA256[
                    f"{PLUGIN_SLUG}/assets/frpdepot-freight-quote-journey.css"
                ],
            )
        except Exception as exc:  # noqa: BLE001 - no values or exception text persisted
            _burn_contact_attempt(
                lock, plan, plan_path, exc, "freight_contact_preserve_protected_validation"
            )
            raise IndeterminateError(
                "Post-upload Contact, freight, member, quote-404 or cold FNPT validation did not "
                "complete exactly. The plan is permanently indeterminate with no retry, no "
                "deactivation and no rollback."
            ) from exc

        write_lock(lock, {
            "plan_sha256": plan["sha256"], "status": "committed_verified",
            "updated_utc": utc_now().isoformat(), "after": after,
            "fnpt_public_validation": "passed", "retry": False, "rollback": False,
        })
        record_result(plan_path, plan, "COMMITTED_AND_VERIFIED", {
            "before": fresh,
            "after": after,
            "comparison": comparison,
            "active_before_and_after": True,
            "upload_attempts": 1,
            "contact_sha256_unchanged": True,
            "fnpt_public_validation": fnpt_findings,
            "automatic_rollback": False,
            "deactivated": False,
        })
        emit({
            "status": "COMMITTED_AND_VERIFIED",
            "action": "plugin_freight_contact_preserve_repair",
            "plugin_file": PLUGIN_FILE,
            "installed_version": after["plugin_row"]["version"],
            "active": after["plugin_row"]["active"],
            "comparison": comparison,
            "upload_attempts": 1,
            "contact_sha256_unchanged": True,
            "request_a_quote_status": after["public"]["request_quote"]["status"],
            "fnpt_public_validation": fnpt_findings,
            "deactivated": False,
            "automatic_rollback": False,
            "plan_sha256": plan["sha256"],
            "replay_locked": True,
        })


def _verify_pre_state(admin: AdminPage, plan: dict[str, Any]) -> dict[str, Any]:
    admin.goto_plugins()
    live = admin.read_row()
    if live != plan["before"]:
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
    commands.add_parser("stage-fnpt-display-repair").set_defaults(func=command_stage_fnpt_display_repair)
    commands.add_parser("stage-freight-contact-preserve-repair").set_defaults(
        func=command_stage_freight_contact_preserve_repair
    )
    commands.add_parser("stage-deactivate").set_defaults(func=command_stage_deactivate)

    stage_activate = commands.add_parser("stage-activate")
    stage_activate.add_argument("--preflight", required=True)
    stage_activate.set_defaults(func=command_stage_activate)

    for name, handler in (
        ("commit-replace", command_commit_replace),
        ("commit-fnpt-display-repair", command_commit_fnpt_display_repair),
        ("commit-freight-contact-preserve-repair",
         command_commit_freight_contact_preserve_repair),
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
