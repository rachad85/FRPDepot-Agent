#!/usr/bin/env python
"""FRP Depot Zoho Books invoice email-template tool.

Commissioned by Rachad Homsi on 2026-08-10 after live testing proved the Zoho
Books Android app exposes neither Android contacts nor SwiftKey clipboard clips
in its CC picker. Building, testing or staging with this module is not approval
of a business write.

The ONLY thing this named tool may ever create is one of exactly four fixed
organization-wide INVOICE email templates, each a clone of the single live
``Default`` invoice template with exactly one changed name and one fixed CC
list:

* ``CC - Accounting``  -> accounting@frpdepots.com
* ``CC - Logistics``   -> logistics@frpdepots.com
* ``CC - Operations``  -> operations@frpdepots.com
* ``CC - All``         -> logistics@, accounting@, operations@ (that order)

Creation is two-phase by design. The first plan may create ``CC - Accounting``
only, so Rachad can prove on his Android phone that a non-default template is
selectable and its CC populates. Anything after that requires his own direct
Android-test confirmation, recorded in the plan. Commissioning is not approval
and is not Android-test confirmation.

*** THE THIRD ACTION, ``create_all_only`` (2026-08-11). ***

Rachad confirmed on 2026-08-11 that the live ``CC - Accounting`` template was
selectable in the Zoho Books Android app and that its Cc field held only the
accountant. He then asked for ONE template carrying all three internal
recipients, because removing an unneeded recipient at send time is easier than
adding one. ``create_all_only`` therefore stages exactly ONE target, ``CC -
All``, and cannot reach ``CC - Logistics`` or ``CC - Operations`` at all -- they
are not in its target tuple, are never checked for absence, and are never
created. It carries the same two preconditions as ``create_remaining_templates``:
Rachad's own recorded Android-test confirmation, and exactly one live
``CC - Accounting`` template still verified as a faithful clone of ``Default``.

An existing ``CC - Accounting`` does NOT block this action -- it is a
precondition for it. An existing ``CC - All`` does block it.

Everything else is permanently unreachable. There is no generic browser action,
no generic HTTP write helper, and no caller-supplied selector, URL, module,
name, address or source template. Template update, rename, set-default,
clone-to-another-module, customer/vendor association, attachment change and PDF
templates are not implemented. DELETE is implemented for exactly ONE hard-coded
template -- the CC - Accounting an unapproved live write created on 2026-08-11 --
and for no other: see DELETE_TARGET_TEMPLATE_ID and require_delete_target. No email, reminder, notification, SMTP, Graph or
Zoho transaction-email route exists anywhere in this module: the only network
verbs it can issue are same-origin browser GETs on two exact Books settings
paths and read-only OAuth GETs through ``zoho_tool.api_get``.

THE CREATE PATH IS ZOHO'S OWN NATIVE ``Clone`` CONTROL, AND NOTHING ELSE.

Zoho Books publishes no documented API for creating an email template, so the
only safe mechanism is Zoho's own native Save path, exactly as
``zoho_inventory_classification_tool`` does for the item custom field.

TWO CONTRACTS WERE CAPTURED, EACH UNDER AN ABORT-EVERYTHING INTERCEPTOR, AND
ONLY ONE OF THEM IS USED.

1. THE ``New`` FORM (2026-08-10) IS PERMANENT NEGATIVE EVIDENCE. Decoding its
   captured Save body proved the ``New`` form does NOT clone this organization's
   live ``Default`` template -- it carries Zoho's stock factory invoice body
   (BALANCE DUE / %Balance% / MAKE PAYMENT / Regards %UserName% %CompanyName%)
   where the live ``Default`` reads INVOICE AMOUNT / %Total% / PAY NOW / Regards
   Accounting Departement. ``New`` is therefore never clicked by this tool. See
   ``NATIVE_SAVE_*`` and ``new_form_negative_evidence``.

2. THE ``Clone`` CONTROL ON THE ``Default`` ROW (2026-08-11) IS THE CREATE PATH.
   The row's exact ``Show dropdown menu`` disclosure was opened, its exact
   ``Clone`` item clicked, only the fixed Template Name and the fixed Cc
   dropdown option filled, and Save emitted exactly ONE request -- ``POST`` to
   ``https://books.zohocloud.ca/api/v3/settings/emailtemplates``, empty query,
   form body ``JSONString`` + ``organization_id``, body SHA-256
   ``f6e9d14c...`` -- aborted before the network. Its JSON payload is a FLAT
   eight-key object (``bcc_mail_ids``, ``body``, ``cc_mail_ids``,
   ``from_address_id``, ``is_default``, ``name``, ``subject``, ``type``); note
   that this is a DIFFERENT schema from the ``New`` form's nested
   ``language_content`` block, which is why the two are validated separately.

*** THE ONE NARROW EQUIVALENCE RACHAD ACCEPTED, AND ITS EXACT LIMITS. ***

The Clone body is not byte-identical to the live ``Default`` body. Both are
2,131 characters; canonical parsing yields exactly 106 events on each and every
event is equal. Zoho reorders ``href``/``style`` on the PAY NOW ``<a>`` and
``class``/``style`` on its two nested ``<span>`` elements. No element, nesting,
text, placeholder, link, attribute name, attribute value, style value, signature
or order changes. Asked whether to accept that harmless native rearrangement and
implement the Clone tool, Rachad answered ``YES`` on 2026-08-11.

That acceptance covers HTML ATTRIBUTE ORDER AND NOTHING ELSE.
``same_canonical_html`` preserves tags, nesting, attribute names, attribute
values, quoting, data and whitespace nodes, entities, comments and declarations
exactly, rejects duplicate attributes rather than letting a parser collapse
them, and refuses malformed or unclosed markup instead of guessing. The plan's
own source fingerprint still protects the live ``Default`` body BYTE-for-byte;
the canonical comparator applies only to the target's unavoidable native Clone
serialization, to the intercepted POST and to the read-back.

*** WHAT THIS COST TO LEARN, 2026-08-11. TWO DEFECTS, ONE UNAUTHORIZED WRITE. ***

While mutation-checking the new guards, the ``@holds_zoho_browser`` decorator was
temporarily deleted to prove it was load-bearing. It was -- but with it gone,
``test_busy_browser_refuses_for_free_and_leaves_the_plan_reusable`` (which calls
``command_commit`` directly and deliberately does NOT patch
``create_template_via_ui``) had nothing left between it and the live session.
The suite's fake vault carries the REAL organization id and the real ``Default``
template id, so the test drove the real browser and saved a real
``CC - Accounting`` invoice template into FRP Depot's Zoho Books
(``96274000001558092``). Rachad had not approved any plan. Nothing was emailed
and nothing else was touched.

  1. THE TEST HARNESS WAS THE HOLE, NOT THE TOOL. Patching the read transport
     was never enough, because the create path opens its OWN playwright session.
     ``test_zoho_email_template_tool`` now patches ``sync_playwright`` itself at
     module scope, so any test that reaches a real browser fails loudly instead
     of writing to a live business system.

  2. THE READ-BACK WOULD HAVE ORPHANED EVERY TEMPLATE IT CREATED. ``placeholder``
     was in ``SOURCE_CLONE_FIELDS``, i.e. required to be inherited byte-for-byte.
     Zoho DERIVES it from the template's own name -- measured on both live
     templates, ``Default`` -> ``mt_default`` and ``CC - Accounting`` ->
     ``mt_cc_accounting`` -- so a faithful clone can never carry the source's.
     Every successful create would therefore have failed its own read-back,
     reported indeterminate, and left an orphan template behind a permanently
     locked plan. It is now checked by ``derived_placeholder`` instead. The
     accidental write is what proved this; it would not have been caught by any
     test written against the old assumption.

The write is one intercepted, fully validated POST per target, allowed exactly
once, with no retry. Everything else stays permanently unreachable: no generic
browser action, no generic HTTP write helper, no caller-supplied selector, URL,
module, name, address or source template; no update, rename, set-default,
clone-to-another-module, customer/vendor association, attachment or
PDF-template route; no delete of anything but the ONE hard-coded template named
above; and no email, reminder, notification, SMTP, Graph or Zoho
transaction-email route anywhere in this module.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timedelta, timezone
import functools
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import zoho_tool

# One writer at a time on the shared authenticated Zoho window. This tool does
# not launch a browser -- it attaches to the single long-lived session on CDP
# 127.0.0.1:9228 and drives an EXISTING tab, so two concurrent chat lanes would
# otherwise receive the same page object and interleave clicks into each other's
# form. Appended, never inserted first, so it cannot shadow a stdlib name.
sys.path.append(str(Path(__file__).resolve().parent.parent / "common"))
from ui_lane_lock import UiLaneBusy, UiLaneLockError, ui_browser_lock  # noqa: E402,F401


def holds_zoho_browser(purpose: str):
    """Serialize a whole command against the shared Zoho browser.

    Wraps the ENTIRE command, so the browser is claimed before the plan's
    one-attempt replay lock is written. A busy browser therefore refuses for
    free -- the plan is never locked, never marked indeterminate, and can be
    committed unchanged once the other lane finishes.
    """
    def decorate(function):
        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any):
            with ui_browser_lock("zoho", purpose=purpose):
                return function(*args, **kwargs)
        return wrapper
    return decorate


TOOL_NAME = "FRP Depot Zoho Books Invoice Email Template Tool"
TOOL_VERSION = "2.2.0"
# Bumped with the tool version on every commission that changes what a plan may
# mean. v2.0.0/schema 3 closed the old New-form blocker; v2.1.0/schema 4 adds the
# one-target create_all_only action, so a plan staged under the previous build
# fails closed instead of being commit-able against the new action set.
SCHEMA_VERSION = 4
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_email_template_plans"
PLAN_LIFETIME_HOURS = 24

# Byte-exact, unpadded, uppercase. No .strip(), no case folding. Deliberately
# as strict as zoho_invoice_revision_tool and stricter than its older siblings.
APPROVAL_WORD = "APPROVED"

CDP_ENDPOINT = "http://127.0.0.1:9228"
UI_SCHEME = "https"
UI_HOST = "books.zohocloud.ca"
UI_APP_PATH = "/app"
TEMPLATE_LIST_PATH = "/api/v3/settings/emailtemplates"
TEMPLATE_DETAIL_RE = re.compile(r"^/api/v3/settings/emailtemplates/([1-9][0-9]*)$")
SETTINGS_URL = (
    f"{UI_SCHEME}://{UI_HOST}{UI_APP_PATH}"
    "#/settings/emails/templates?email_type=invoice_notification"
)

# --------------------------------------------------------------------------
# The captured NEW-FORM Save contract. HISTORICAL NEGATIVE EVIDENCE ONLY.
#
# Every value here was read off the blocked capture artifact, never guessed.
# Nothing in the create path uses it: the New form emits Zoho's stock factory
# body, not this organization's Default, and this tool never clicks New.
# --------------------------------------------------------------------------
NATIVE_SAVE_METHOD = "POST"
NATIVE_SAVE_PATH = TEMPLATE_LIST_PATH
NATIVE_SAVE_QUERY = ""
NATIVE_SAVE_FORM_KEYS = ("JSONString", "organization_id")
NATIVE_PAYLOAD_FIELDS = frozenset({
    "bcc_mail_ids", "cc_mail_ids", "from_address_id", "is_default",
    "language_content", "name", "type",
})
NATIVE_LANGUAGE_FIELDS = frozenset({"body", "is_default", "language_code", "subject"})
NATIVE_LANGUAGE_CODE = "en"
CAPTURE_DIR = ROOT / "Dado" / "20_Working" / "zoho_email_template_capture"
NATIVE_SAVE_ARTIFACT = CAPTURE_DIR / "native_save_request.json"
# SHA-256 of the exact captured form body, as recorded in the artifact.
NATIVE_SAVE_BODY_SHA256 = (
    "850b177880f00f693bca3ee367a8f548b06bf252e9db873c32a591233c29b7ad"
)
# SHA-256 of the subject and body the New form itself put in that captured
# payload. The subject matches the live Default; the body does NOT -- it is
# Zoho's stock factory body. Pinned here so the mismatch is proven, not assumed.
NATIVE_FORM_SUBJECT_SHA256 = (
    "187f668c35f774043cfa299dbebd44418a9615a1fc63915c7249a86dc9914d33"
)
NATIVE_FORM_BODY_SHA256 = (
    "97d978cae63e7538a99ced5cc62d3c7dbc9c1fbfc5d595358c3e6539f21036bd"
)

# --------------------------------------------------------------------------
# The captured NATIVE CLONE Save contract. THIS is the commissioned create
# path. Read off the blocked capture on 2026-08-11, never guessed.
#
# Note the schema differs from the New form's: Clone emits a FLAT eight-key
# payload with top-level body/subject, where New nested them in a
# language_content block. The two are validated by separate functions on
# purpose -- neither may stand in for the other.
# --------------------------------------------------------------------------
CLONE_SAVE_METHOD = "POST"
CLONE_SAVE_PATH = TEMPLATE_LIST_PATH
CLONE_SAVE_QUERY = ""
CLONE_SAVE_FORM_KEYS = ("JSONString", "organization_id")
CLONE_PAYLOAD_FIELDS = frozenset({
    "bcc_mail_ids", "body", "cc_mail_ids", "from_address_id", "is_default",
    "name", "subject", "type",
})
CLONE_SAVE_ARTIFACT = CAPTURE_DIR / "clone_native_save_request.json"
CLONE_SAVE_EVIDENCE_ARTIFACT = CAPTURE_DIR / "clone_native_save_evidence.json"
CLONE_FIDELITY_ARTIFACT = CAPTURE_DIR / "clone_native_fidelity_evidence.json"
# SHA-256 of the exact captured Clone form body, as recorded in the artifact.
CLONE_SAVE_BODY_SHA256 = (
    "f6e9d14c56e6560632f21245755674cee0a4013282a82ac5282b258eee5ff0ab"
)
# The live Default body at capture time, and the byte-different but
# canonical-HTML-identical body the Clone form emitted from it.
CLONE_SOURCE_BODY_SHA256 = (
    "a3e97795c9eb1e77f5e8c67382ac8edcf0fe2694f916073e8fe630d988529ec0"
)
CLONE_POSTED_BODY_SHA256 = (
    "3e9f0bd80a7689008f8c228a198c468386c549f458e8f44b1ad748947e9e9c93"
)
CLONE_CANONICAL_EVENT_COUNT = 106

# Version 1 targets exactly one module. Quotes, sales orders and every other
# Zoho module are out of scope and unreachable.
MODULE_NAME = "Invoices"
MODULE_EMAIL_TYPE = "invoice_notification"
MODULE_TYPE_FORMATTED = "Invoice Notification"
SOURCE_TEMPLATE_NAME = "Default"

OFFICIAL_DOC = "https://www.zoho.com/ca/books/help/settings/emails.html#email-templates"
INVESTIGATION_SOURCE = str(
    ROOT / "Dado" / "20_Working" / "zoho_android_internal_cc_investigation_20260810.md"
)

ACCOUNTING = "accounting@frpdepots.com"
LOGISTICS = "logistics@frpdepots.com"
OPERATIONS = "operations@frpdepots.com"
ALLOWED_ADDRESSES = frozenset({ACCOUNTING, LOGISTICS, OPERATIONS})

# The exact role=option values Zoho's Cc autocomplete offers, read off
# zoho_org_address_discovery\cc_dropdown_options.json on 2026-08-10. Live
# testing proved Cc entry works ONLY by opening that row's own .zf-ac-toggler
# and clicking one of these exact options: typing an address, pressing Enter and
# typing a comma all failed. Those failures are not fallbacks to try later.
CC_OPTION_TEXT: dict[str, str] = {
    ACCOUNTING: "FRP Depots Accounting<accounting@frpdepots.com>",
    LOGISTICS: "FRP Depots Logistics<logistics@frpdepots.com>",
    OPERATIONS: "Douhaa ABZ<operations@frpdepots.com>",
}
# Present in the same dropdown and deliberately unreachable: no fixed target
# CCs the org's own info@ or sales@ addresses.
CC_OPTIONS_NEVER_SELECTABLE = (
    "Rachad Homsi<info@frpdepots.com>",
    "FRP Depots<sales@frpdepots.com>",
)
CC_ROW_LABEL = "Cc"
CC_DROPDOWN_TOGGLER = ".zf-ac-toggler"

# The complete, fixed, closed target set. Order inside each tuple is the exact
# CC order Rachad approved and is asserted on read-back.
TARGET_TEMPLATES: dict[str, tuple[str, ...]] = {
    "CC - Accounting": (ACCOUNTING,),
    "CC - Logistics": (LOGISTICS,),
    "CC - Operations": (OPERATIONS,),
    "CC - All": (LOGISTICS, ACCOUNTING, OPERATIONS),
}
ACCOUNTING_TEMPLATE = "CC - Accounting"
ALL_TEMPLATE = "CC - All"
CREATE_ACCOUNTING_TEST = "create_accounting_test"
CREATE_REMAINING = "create_remaining_templates"
# Rachad's 2026-08-11 ask, after the Android test passed: one template holding
# all three internal recipients, because removing an unneeded recipient at send
# time is easier than adding one. Exactly one target, and Logistics/Operations
# are unreachable through it.
CREATE_ALL_ONLY = "create_all_only"
ACTION_TARGETS: dict[str, tuple[str, ...]] = {
    CREATE_ACCOUNTING_TEST: (ACCOUNTING_TEMPLATE,),
    CREATE_REMAINING: ("CC - Logistics", "CC - Operations", ALL_TEMPLATE),
    CREATE_ALL_ONLY: (ALL_TEMPLATE,),
}
ACTIONS = tuple(ACTION_TARGETS)
# Every action EXCEPT the first Android test itself. Each one requires Rachad's
# own recorded Android-test confirmation and a live CC - Accounting template
# still verified as a faithful clone of Default. Keyed off this tuple rather
# than off one action name, so adding an action cannot silently skip either gate.
ANDROID_CONFIRMED_ACTIONS = (CREATE_REMAINING, CREATE_ALL_ONLY)

# --------------------------------------------------------------------------
# The ONE deletion Rachad commissioned on 2026-08-11, and no second one.
#
# WHY IT EXISTS. On 2026-08-11 mutation-testing removed @holds_zoho_browser to
# prove it was load-bearing; with it gone, a test that calls command_commit
# directly and deliberately does NOT patch create_template_via_ui reached the
# real browser against a vault carrying the REAL organization, and created live
# invoice template CC - Accounting 96274000001558092 with NO approval. Rachad
# asked for it to be removed through a commissioned path rather than by hand,
# so the removal carries the same audit trail every other write here does.
#
# THE SCOPE IS ONE FIXED ROW. The template ID and its name are both constants
# and both must match the live row; every other template - above all the live
# Default this module clones from - is unreachable. There is no rename, no
# update, no set-default, no bulk route, and deletion of a DEFAULT template is
# refused outright even if someone edited these constants to point at one.
DELETE_ACCIDENTAL_ACCOUNTING = "delete_accidental_accounting_test"
DELETE_TARGET_TEMPLATE_ID = "96274000001558092"
DELETE_TARGET_NAME = ACCOUNTING_TEMPLATE
DELETE_ACTIONS = (DELETE_ACCIDENTAL_ACCOUNTING,)
# *** THE NATIVE DELETE REQUEST HAS NOT BEEN CAPTURED YET. ***
# Zoho publishes no documented Books API for deleting an email template, exactly
# as it publishes none for creating one, so the only safe mechanism is Zoho's own
# native control - and this tool refuses to release a request whose shape it has
# not first observed under an abort-everything interceptor. Guessing
# `DELETE /api/v3/settings/emailtemplates/{id}` because it looks RESTful is the
# precise failure this tree already has a rule against: measured, not guessed.
# *** CAPTURED 2026-08-11, authorized by Rachad, and pinned here. ***
# The row menu holds exactly ["Edit", "Delete"]; Delete only OPENS Zoho's
# confirmation modal, and the request fires on confirming with "Yes" inside it.
# Under an abort-everything interceptor the confirmed control emitted exactly
# this and nothing else, and it never reached the network:
#     DELETE https://books.zohocloud.ca/api/v3/settings/emailtemplates/<id>
#            ?organization_id=<org>      with NO body
# It is indeed the REST-shaped endpoint - but it is pinned because it was
# MEASURED, not because it looked obvious. Artifact:
# Dado\20_Working\zoho_email_template_capture\native_delete_request.json
DELETE_CONTRACT_CAPTURED = True
DELETE_METHOD = "DELETE"
DELETE_PATH = f"/api/v3/settings/emailtemplates/{DELETE_TARGET_TEMPLATE_ID}"
# sha256 of the empty string: the captured request carried no body at all, and
# anything else is drift.
DELETE_EMPTY_BODY_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)
DELETE_MENU_ITEM = "Delete"
DELETE_CONFIRM_LABEL = "Yes"
DELETE_EXPECTED_MENU_ITEMS = ("Edit", "Delete")
DELETE_NOT_CAPTURED = (
    "REFUSED: the native Delete request has never been captured, so this tool "
    "cannot prove what it would send, and it will not guess a REST-shaped delete "
    "endpoint. Nothing was deleted and this plan is still usable."
)

# Exactly the keys Zoho's live detail endpoint returned for the source template
# on 2026-08-10. Any added, renamed or removed key is UI/API drift and fails
# closed rather than being silently cloned or silently dropped.
SOURCE_DETAIL_FIELDS = frozenset({
    "bcc_mail_ids", "body", "bodyv2", "cc_mail_ids", "cc_me", "documents",
    "email_template_id", "from_address_id", "is_default", "language_content",
    "name", "placeholder", "subject", "type",
})
# Everything a target must inherit byte-for-byte from Default. The four keys
# outside this set are the only ones a target is allowed to differ on:
# email_template_id (Zoho assigns), name (fixed), cc_mail_ids (fixed) and
# is_default (must be False on every target).
SOURCE_CLONE_FIELDS = (
    "bcc_mail_ids", "body", "bodyv2", "cc_me", "documents", "from_address_id",
    "language_content", "subject", "type",
)
# *** placeholder IS NOT INHERITED, AND ASSUMING IT WAS WAS A REAL DEFECT. ***
# Zoho DERIVES this internal identifier from the template's own name, so a
# faithful clone can never carry the source's. Requiring byte-equality on it
# (as this tool did until 2026-08-11) meant every successful create would fail
# its own read-back afterwards -- orphaning a template behind a permanently
# locked plan, the exact outcome the create gate exists to prevent. Measured on
# two live templates: "Default" -> "mt_default" and "CC - Accounting" ->
# "mt_cc_accounting". It is now checked by its own explicit rule below.
DERIVED_FROM_NAME_FIELDS = ("placeholder",)
# The only fields compared through the canonical HTML comparator instead of
# byte-for-byte, because Zoho's own Clone editor re-serializes them. The
# comparator still refuses any change other than attribute ORDER, and a
# non-HTML value (bodyv2 is empty on the live Default) degrades to an exact
# single-data-node comparison.
CANONICAL_HTML_FIELDS = frozenset({"body", "bodyv2"})
LIST_ROW_FIELDS = frozenset({
    "cc_me", "documents", "email_template_id", "is_default", "is_from_plugin",
    "is_new_editor", "name", "placeholder", "subject", "type", "type_formatted",
})

ANDROID_CONFIRMATION_FIELDS = frozenset({
    "android_test_succeeded", "confirmed_by", "confirmed_utc", "statement", "source",
})
CONFIRMED_BY = "Rachad Homsi"
CONFIRMATION_MAX_AGE_DAYS = 30

PLAN_FIELDS = frozenset({
    "schema_version", "tool", "tool_version", "action", "created_utc",
    "expires_utc", "nonce", "approval_required", "origin", "organization",
    "module", "source", "targets", "sources", "android_test_confirmation",
    "workflow", "risks", "live_evidence", "email_sent", "sha256",
})

HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
EMAIL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._%+-]*[a-z0-9])?@[a-z0-9.-]+\.[a-z]{2,}$")

# Both native Save contracts were captured under an abort-everything route
# interceptor, the way the item custom field's was, without reaching the network.
NATIVE_SAVE_CONTRACT_CAPTURED = True
CLONE_SAVE_CONTRACT_CAPTURED = True

# True since 2026-08-11: Rachad answered YES to accepting Zoho's harmless
# attribute reordering, which was the only thing standing between the captured
# Clone contract and a faithful clone. The flag alone commissions nothing --
# require_create_contract_commissioned still re-proves the pinned contract and
# the canonical body equality against the live Default on every commit.
CREATE_WORKFLOW_COMMISSIONED = True

NON_ATOMIC_RISK = (
    "NOT ATOMIC: each template is saved by its own independent UI Save. A "
    "failure after one save leaves a partial set. The plan is locked on first "
    "failure and never retried."
)
CLONE_FIDELITY_RISK = (
    "ACCEPTED BY RACHAD (YES, 2026-08-11): Zoho's native Clone editor re-serializes "
    "the inherited body with its HTML attributes in a different order, so the "
    "created template's body is byte-different from Default while being "
    "canonically identical. Only attribute ORDER is accepted; every tag, nesting "
    "level, attribute name, attribute value, text node, placeholder, link, entity, "
    "comment and declaration must match exactly, and the plan's own source "
    "fingerprint still protects the live Default body byte-for-byte."
)
RISKS = (
    NON_ATOMIC_RISK,
    CLONE_FIDELITY_RISK,
    "Zoho publishes no documented Books API for creating an email template, so "
    "the write surface is the authenticated UI only.",
    "Only the single-CC Clone sequence was captured live. The three-option "
    "CC - All case repeats that same proven control once per address and "
    "verifies the exact resulting selection before Save; any surprise refuses "
    "before the write.",
    "The detail endpoint exposes exactly "
    f"{len(SOURCE_DETAIL_FIELDS)} template properties. Any Zoho-side property "
    "outside that set (for example a separate attach-PDF toggle) cannot be read "
    "and therefore cannot be verified; drift in the exposed set fails closed.",
    "Templates are created non-default and unassociated. Selecting one at send "
    "time stays a manual per-email choice by Rachad.",
)


class EmailTemplateError(RuntimeError):
    """A fail-closed validation, precondition, transport, or read-back error."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise EmailTemplateError(
            "Zoho returned evidence that is not JSON serializable."
        ) from exc


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EmailTemplateError(f"{label} JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise EmailTemplateError(f"{label} JSON must contain exactly one object.")
    return value


def closed_fields(value: Any, expected: frozenset[str] | set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise EmailTemplateError(f"{label} must be one object.")
    if set(value) == set(expected):
        return
    missing = sorted(set(expected) - set(value))
    extra = sorted(set(value) - set(expected))
    details: list[str] = []
    if missing:
        details.append("missing: " + ", ".join(missing))
    if extra:
        details.append("unsupported: " + ", ".join(extra))
    raise EmailTemplateError(
        f"{label} must use the exact closed schema ({'; '.join(details)})."
    )


def clean_text(value: Any, label: str, maximum: int = 2000) -> str:
    if not isinstance(value, str):
        raise EmailTemplateError(f"{label} must be text.")
    result = value.strip()
    if not result:
        raise EmailTemplateError(f"{label} cannot be blank.")
    if len(result) > maximum:
        raise EmailTemplateError(f"{label} exceeds the {maximum}-character safety limit.")
    if any(ord(character) < 32 for character in result):
        raise EmailTemplateError(f"{label} contains control characters.")
    return result


def positive_id(value: Any, label: str) -> str:
    if isinstance(value, bool):
        raise EmailTemplateError(f"{label} must be a positive Zoho ID.")
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise EmailTemplateError(f"{label} must be a positive Zoho ID.")
    return text


def parse_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise EmailTemplateError(f"{label} is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EmailTemplateError(f"{label} must include a timezone.")
    return parsed


def normalized_key(value: Any) -> str:
    """Collapse a template name to its comparison key.

    Case, spacing and punctuation are all removed, so ``CC-Accounting`` and
    ``cc accounting`` collide with ``CC - Accounting`` instead of quietly
    becoming a second near-duplicate template.
    """
    return "".join(character for character in str(value).casefold() if character.isalnum())


def derived_placeholder(name: Any) -> str:
    """Zoho's own internal identifier for a template, derived from its name.

    Verified live against both templates this organization has: ``Default`` ->
    ``mt_default`` and ``CC - Accounting`` -> ``mt_cc_accounting``.
    """
    parts = re.findall(r"[a-z0-9]+", str(name).casefold())
    if not parts:
        raise EmailTemplateError("A template name with no alphanumerics has no placeholder.")
    return "mt_" + "_".join(parts)


def require_fixed_address(value: Any, label: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise EmailTemplateError(f"{label} must be an exact unpadded address.")
    if not EMAIL_RE.fullmatch(value) or value not in ALLOWED_ADDRESSES:
        raise EmailTemplateError(
            f"{label} is not one of the three fixed FRP Depot internal addresses."
        )
    return value


def require_fixed_target(name: Any, cc: Any, label: str) -> tuple[str, tuple[str, ...]]:
    if not isinstance(name, str) or name not in TARGET_TEMPLATES:
        raise EmailTemplateError(
            f"{label} must be exactly one of the four fixed template names: "
            + ", ".join(TARGET_TEMPLATES)
        )
    if not isinstance(cc, list):
        raise EmailTemplateError(f"{label} CC list is invalid.")
    expected = TARGET_TEMPLATES[name]
    for index, address in enumerate(cc):
        require_fixed_address(address, f"{label} cc[{index}]")
    if tuple(cc) != expected:
        raise EmailTemplateError(
            f"{label} CC list must be exactly {list(expected)} in that order."
        )
    return name, expected


def require_rachad_approval(approval: Any) -> None:
    """Exact, unpadded, uppercase APPROVED. No strip(), no case folding."""
    if not isinstance(approval, str) or approval != APPROVAL_WORD:
        raise EmailTemplateError(
            "Rachad must answer this exact staged plan with the one-word approval: "
            f"{APPROVAL_WORD} (exact uppercase, no extra words or spaces). Building, "
            "testing or staging is not approval, and Dado cannot supply it."
        )


def origin_record() -> dict[str, str]:
    return {
        "tool_path": str(Path(__file__).resolve()),
        "repo_root": str(ROOT),
        "plan_dir": str(PLAN_DIR),
    }


def require_origin(origin: Any) -> None:
    if origin != origin_record():
        raise EmailTemplateError("Plan origin does not match this tool installation.")


def contained_plan(raw_path: Any) -> Path:
    candidate = Path(str(raw_path if raw_path is not None else ""))
    if not candidate.is_absolute():
        raise EmailTemplateError(
            "Plan must be an absolute path inside the exact email-template plan folder."
        )
    try:
        lexical_root = PLAN_DIR.absolute()
        lexical_candidate = candidate.absolute()
        lexical_candidate.relative_to(lexical_root)
    except (OSError, ValueError) as exc:
        raise EmailTemplateError(
            "Plan is outside the exact allowlisted email-template plan folder."
        ) from exc
    cursor = lexical_candidate
    while True:
        if cursor.is_symlink():
            raise EmailTemplateError("Plan paths and parents must not be symlinks.")
        if cursor == lexical_root:
            break
        parent = cursor.parent
        if parent == cursor:
            raise EmailTemplateError(
                "Plan is outside the exact allowlisted email-template plan folder."
            )
        cursor = parent
    try:
        root = PLAN_DIR.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise EmailTemplateError(
            "Plan does not resolve inside the exact email-template plan folder."
        ) from exc
    if (
        root not in resolved.parents
        or not resolved.is_file()
        or resolved.suffix.casefold() != ".json"
    ):
        raise EmailTemplateError(
            "Plan is outside the exact allowlisted email-template plan folder."
        )
    return resolved


# --------------------------------------------------------------------------
# The one narrow equivalence Rachad accepted on 2026-08-11: HTML ATTRIBUTE
# ORDER, and nothing else.
#
# Zoho's own Clone editor re-serializes the body it inherited, reordering
# href/style on the PAY NOW anchor and class/style on its two nested spans. The
# live Default and the Clone POST are both 2,131 characters, parse to exactly
# 106 events each, and every event is equal.
#
# This comparator is deliberately small, closed and dedicated to this tool. It
# ignores attribute ORDER. It preserves tag names (case included), nesting,
# attribute names, attribute values, quote characters, data and whitespace
# nodes, entities, comments, declarations and processing instructions exactly.
# Duplicate attributes are REFUSED rather than silently collapsed the way a
# permissive parser would, and malformed, mismatched or unclosed markup is
# REFUSED rather than repaired. When in doubt it fails closed; it never
# broadens the equivalence.
# --------------------------------------------------------------------------

# Elements HTML5 defines as having no end tag. A stray end tag for one of these
# is markup we do not understand, so it is refused rather than ignored.
VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})

_START_TAG_NAME_RE = re.compile(r"^<\s*([^\s/>]+)")
_ATTRIBUTE_RE = re.compile(
    r"""\s+([^\s=/>"'<]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?"""
)


class _CanonicalHtml(HTMLParser):
    """Turn one HTML string into an order-insensitive-attributes event list."""

    def __init__(self) -> None:
        # convert_charrefs=False keeps &amp; and &#34; as their own events
        # instead of quietly decoding them into indistinguishable text.
        super().__init__(convert_charrefs=False)
        self.events: list[tuple[Any, ...]] = []
        self.open_elements: list[str] = []

    def _canonical_start_tag(self) -> tuple[str, tuple[tuple[str, str | None, str], ...]]:
        raw = self.get_starttag_text() or ""
        name_match = _START_TAG_NAME_RE.match(raw)
        if name_match is None:
            raise EmailTemplateError(f"Unparseable HTML start tag: {raw[:120]!r}")
        raw_name = name_match.group(1)
        rest = (raw[name_match.end():]).rstrip()
        if rest.endswith("/>"):
            rest = rest[:-2]
        elif rest.endswith(">"):
            rest = rest[:-1]
        else:
            raise EmailTemplateError(f"Unterminated HTML start tag: {raw[:120]!r}")
        attributes: list[tuple[str, str | None, str]] = []
        cursor = 0
        while cursor < len(rest):
            match = _ATTRIBUTE_RE.match(rest, cursor)
            if match is None:
                if not rest[cursor:].strip():
                    break
                raise EmailTemplateError(
                    f"Unparseable HTML attribute region {rest[cursor:][:80]!r} "
                    f"in {raw[:120]!r}"
                )
            if match.group(2) is not None:
                value, quote = match.group(2), '"'
            elif match.group(3) is not None:
                value, quote = match.group(3), "'"
            elif match.group(4) is not None:
                value, quote = match.group(4), ""
            else:
                value, quote = None, ""
            attributes.append((match.group(1), value, quote))
            cursor = match.end()
        keys = [name.casefold() for name, _, _ in attributes]
        if len(set(keys)) != len(keys):
            raise EmailTemplateError(
                f"HTML carries a duplicate attribute, which is ambiguous: {raw[:120]!r}"
            )
        return raw_name, tuple(sorted(attributes, key=lambda item: (item[0], item[1] or "", item[2])))

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        raw_name, attributes = self._canonical_start_tag()
        self.events.append(("starttag", raw_name, attributes))
        if tag not in VOID_ELEMENTS:
            self.open_elements.append(tag)

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        raw_name, attributes = self._canonical_start_tag()
        self.events.append(("startendtag", raw_name, attributes))

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_ELEMENTS:
            raise EmailTemplateError(f"HTML carries an end tag for void element {tag!r}.")
        if not self.open_elements:
            raise EmailTemplateError(f"HTML carries an unmatched </{tag}>.")
        opened = self.open_elements.pop()
        if opened != tag:
            raise EmailTemplateError(
                f"HTML is malformed: </{tag}> closes an open <{opened}>."
            )
        self.events.append(("endtag", tag))

    def handle_data(self, data: str) -> None:
        self.events.append(("data", data))

    def handle_entityref(self, name: str) -> None:
        self.events.append(("entityref", name))

    def handle_charref(self, name: str) -> None:
        self.events.append(("charref", name))

    def handle_comment(self, data: str) -> None:
        self.events.append(("comment", data))

    def handle_decl(self, decl: str) -> None:
        self.events.append(("decl", decl))

    def handle_pi(self, data: str) -> None:
        self.events.append(("pi", data))

    def unknown_decl(self, data: str) -> None:
        self.events.append(("unknown_decl", data))


def canonical_html_events(value: Any, label: str) -> tuple[tuple[Any, ...], ...]:
    """Parse one HTML string, or fail closed. Never repairs, never guesses."""
    if not isinstance(value, str):
        raise EmailTemplateError(f"{label} must be text to be compared as HTML.")
    parser = _CanonicalHtml()
    try:
        parser.feed(value)
        parser.close()
    except EmailTemplateError as exc:
        raise EmailTemplateError(f"{label}: {exc}") from exc
    except Exception as exc:
        raise EmailTemplateError(f"{label} could not be parsed as HTML: {exc}") from exc
    if parser.open_elements:
        raise EmailTemplateError(
            f"{label} leaves {parser.open_elements!r} unclosed; refusing to guess."
        )
    return tuple(parser.events)


def same_canonical_html(left: Any, right: Any, label: str) -> bool:
    """True only when the two differ by nothing but HTML attribute ORDER."""
    if left == right:
        # Byte-identical still has to parse: unparseable markup is never
        # declared equivalent by this comparator.
        canonical_html_events(left, label)
        return True
    return canonical_html_events(left, label) == canonical_html_events(right, label)


def require_same_canonical_html(expected: Any, actual: Any, label: str) -> None:
    if not same_canonical_html(expected, actual, label):
        raise EmailTemplateError(
            f"{label} is not the source HTML. Only Zoho's own reordering of HTML "
            "attributes is accepted; every tag, nesting level, attribute name, "
            "attribute value, text node, placeholder, link, entity, comment and "
            "declaration must be identical."
        )


# --------------------------------------------------------------------------
# Authenticated UI transport. GET ONLY, on two exact paths. There is no write
# helper here and no caller-supplied URL reaches the browser.
# --------------------------------------------------------------------------

def ui_url_allowed(url: Any) -> str:
    """Fail closed unless this is one of the two exact read-only settings URLs."""
    parsed = urlsplit(str(url if url is not None else ""))
    if parsed.scheme != UI_SCHEME or parsed.hostname != UI_HOST:
        raise EmailTemplateError(
            "REFUSED: only the approved Zoho Books host is reachable."
        )
    if parsed.username or parsed.password or parsed.port or parsed.fragment:
        raise EmailTemplateError("REFUSED: the read URL carries unsupported parts.")
    if parsed.path != TEMPLATE_LIST_PATH and not TEMPLATE_DETAIL_RE.fullmatch(parsed.path):
        raise EmailTemplateError(
            "REFUSED: only the exact email-template list and detail paths are readable."
        )
    return str(url)


def _decode_ui_result(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"status", "ok", "text"}:
        raise EmailTemplateError("Zoho UI read returned an unknown response.")
    status = raw["status"]
    if isinstance(status, bool) or not isinstance(status, int):
        raise EmailTemplateError("Zoho UI read returned an invalid HTTP status.")
    if raw["ok"] is not True or not 200 <= status < 300:
        raise EmailTemplateError(f"Zoho UI email-template read failed with HTTP {status}.")
    if not isinstance(raw["text"], str):
        raise EmailTemplateError("Zoho UI read returned a non-text response.")
    try:
        payload = json.loads(raw["text"])
    except json.JSONDecodeError as exc:
        raise EmailTemplateError("Zoho UI email-template read returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise EmailTemplateError("Zoho UI email-template read was not one object.")
    if payload.get("code") not in (None, 0):
        raise EmailTemplateError(
            "Zoho UI email-template read failed: "
            + str(payload.get("message") or payload.get("code"))
        )
    return payload


def _authenticated_books_page(browser: Any) -> Any:
    """Return an already-signed-in Books app page, or fail closed.

    A page that has been redirected to a sign-in, consent or CAPTCHA host no
    longer matches the exact app host and path, so it is simply not selected
    and the caller is told to reconnect.
    """
    pages = [page for context in browser.contexts for page in context.pages]
    for candidate in pages:
        parsed = urlsplit(candidate.url)
        if (
            parsed.scheme == UI_SCHEME
            and parsed.hostname == UI_HOST
            and (parsed.path == UI_APP_PATH or parsed.path.startswith(UI_APP_PATH + "/"))
        ):
            return candidate
    raise EmailTemplateError(
        "No authenticated live Zoho Books page is available. Run CONNECT_DADO_ZOHO_UI.bat."
    )


NO_SESSION = (
    "No authenticated live Zoho Books page is available. Run CONNECT_DADO_ZOHO_UI.bat."
)


def _ui_get_on_page(page: Any, url: str) -> dict[str, Any]:
    """The ONLY network call site in this module, and it is a GET.

    Only status, success and response text cross the CDP boundary. Cookies,
    local storage, request headers and credentials are never read, printed or
    copied. The URL is re-validated here even though the caller built it.
    """
    return page.evaluate(
        """async (url) => {
            const response = await fetch(url, {
                method: "GET",
                headers: {"Accept": "application/json"},
                credentials: "include",
            });
            const text = await response.text();
            return {status: response.status, ok: response.ok, text};
        }""",
        ui_url_allowed(url),
    )


def _execute_ui_get(url: str) -> dict[str, Any]:
    """Open the shared authenticated session for one read and close it again."""
    url = ui_url_allowed(url)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise EmailTemplateError(NO_SESSION) from exc
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=10_000)
            page = _authenticated_books_page(browser)
            return _ui_get_on_page(page, url)
    except EmailTemplateError:
        raise
    except Exception as exc:
        raise EmailTemplateError(NO_SESSION) from exc


def ui_transport_allowed(
    method: str,
    path: str,
    organization_id: str,
    *,
    email_type: str | None = None,
    page: Any = None,
) -> dict[str, Any]:
    """The complete UI READ transport. Every non-GET verb is refused here.

    ``page`` lets the Clone create path reuse the session it already holds
    instead of nesting a second playwright context inside the live one. It
    changes nothing about what may be read.
    """
    if method != "GET":
        raise EmailTemplateError(
            "REFUSED: this tool has no UI read transport for that verb. Only the "
            "exact email-template list and detail GETs are implemented, and the "
            "one commissioned write is Zoho's own intercepted native Clone Save."
        )
    org_id = positive_id(organization_id, "books_organization_id")
    if path == TEMPLATE_LIST_PATH:
        query: list[tuple[str, str]] = []
        if email_type is not None:
            if email_type != MODULE_EMAIL_TYPE:
                raise EmailTemplateError(
                    "REFUSED: only the fixed invoice_notification module is readable."
                )
            query.append(("email_type", email_type))
        query.append(("organization_id", org_id))
    elif TEMPLATE_DETAIL_RE.fullmatch(path):
        if email_type is not None:
            raise EmailTemplateError("REFUSED: template detail takes no module filter.")
        query = [("organization_id", org_id)]
    else:
        raise EmailTemplateError(
            "REFUSED: only the exact email-template list and detail paths are readable."
        )
    url = f"{UI_SCHEME}://{UI_HOST}{path}?{urlencode(query)}"
    if page is not None:
        return _decode_ui_result(_ui_get_on_page(page, ui_url_allowed(url)))
    return _decode_ui_result(_execute_ui_get(ui_url_allowed(url)))


def ui_list_templates(organization_id: str, *, page: Any = None) -> dict[str, Any]:
    return ui_transport_allowed("GET", TEMPLATE_LIST_PATH, organization_id, page=page)


def ui_template_detail(organization_id: str, template_id: str) -> dict[str, Any]:
    template_id = positive_id(template_id, "email_template_id")
    return ui_transport_allowed(
        "GET", f"{TEMPLATE_LIST_PATH}/{template_id}", organization_id
    )


# --------------------------------------------------------------------------
# Projection and precondition checks over live state.
# --------------------------------------------------------------------------

def project_list_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise EmailTemplateError("Zoho returned a non-object email-template row.")
    closed_fields(row, LIST_ROW_FIELDS, "Email-template list row")
    projected = {key: json_copy(row[key]) for key in sorted(LIST_ROW_FIELDS)}
    projected["email_template_id"] = positive_id(
        projected["email_template_id"], "email_template_id"
    )
    if not isinstance(projected["name"], str) or not projected["name"].strip():
        raise EmailTemplateError("Zoho returned an email template with no name.")
    if projected["is_default"] not in (True, False):
        raise EmailTemplateError("Zoho returned an invalid is_default flag.")
    if projected["type"] != MODULE_EMAIL_TYPE:
        raise EmailTemplateError("Zoho returned a row outside the Invoices module.")
    if projected["type_formatted"] != MODULE_TYPE_FORMATTED:
        raise EmailTemplateError(
            f"Zoho labelled an {MODULE_EMAIL_TYPE} row "
            f"{projected['type_formatted']!r}, not {MODULE_TYPE_FORMATTED!r}."
        )
    return projected


def invoice_template_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("emailtemplates")
    if not isinstance(rows, list):
        raise EmailTemplateError("Zoho email-template list returned no templates array.")
    invoice_rows: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and row.get("type") == MODULE_EMAIL_TYPE:
            invoice_rows.append(project_list_row(row))
    if not invoice_rows:
        raise EmailTemplateError(
            f"Zoho returned no {MODULE_EMAIL_TYPE} templates; the module surface changed."
        )
    keys = [normalized_key(row["name"]) for row in invoice_rows]
    if len(set(keys)) != len(keys):
        raise EmailTemplateError(
            "Zoho already holds two invoice email templates with equivalent names."
        )
    return invoice_rows


def require_single_source_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [row for row in rows if row["name"] == SOURCE_TEMPLATE_NAME]
    equivalent = [
        row for row in rows
        if normalized_key(row["name"]) == normalized_key(SOURCE_TEMPLATE_NAME)
    ]
    if len(matches) != 1 or len(equivalent) != 1:
        raise EmailTemplateError(
            f"Zoho must expose exactly one invoice email template named "
            f"{SOURCE_TEMPLATE_NAME}; found {len(equivalent)}."
        )
    source = matches[0]
    if source["is_default"] is not True:
        raise EmailTemplateError(
            f"The source {SOURCE_TEMPLATE_NAME} invoice template is not Zoho's default."
        )
    return source


def project_source_detail(payload: dict[str, Any], template_id: str) -> dict[str, Any]:
    template = payload.get("email_template")
    if not isinstance(template, dict):
        raise EmailTemplateError("Zoho email-template detail returned no template object.")
    closed_fields(template, SOURCE_DETAIL_FIELDS, "Email-template detail")
    projected = {key: json_copy(template[key]) for key in sorted(SOURCE_DETAIL_FIELDS)}
    if positive_id(projected["email_template_id"], "email_template_id") != template_id:
        raise EmailTemplateError("Zoho returned a different email template than requested.")
    if projected["type"] != MODULE_EMAIL_TYPE:
        raise EmailTemplateError(
            f"Email template {template_id} is not an {MODULE_EMAIL_TYPE} template."
        )
    for key in ("cc_mail_ids", "bcc_mail_ids", "documents", "language_content"):
        if not isinstance(projected[key], list):
            raise EmailTemplateError(f"Email-template {key} must be a list.")
    for key in ("body", "bodyv2", "subject", "name", "placeholder", "from_address_id"):
        if not isinstance(projected[key], str):
            raise EmailTemplateError(f"Email-template {key} must be text.")
    for key in ("cc_me", "is_default"):
        if projected[key] not in (True, False):
            raise EmailTemplateError(f"Email-template {key} must be a boolean.")
    return projected


def require_clean_source(detail: dict[str, Any]) -> dict[str, Any]:
    """The source must be Default, the module default, with no preset CC/BCC.

    A non-empty BCC or an unexpected preset CC would be silently inherited by
    all four targets, so staging fails closed instead of cloning it.
    """
    if detail["name"] != SOURCE_TEMPLATE_NAME:
        raise EmailTemplateError(
            f"The source template must be named exactly {SOURCE_TEMPLATE_NAME}."
        )
    if detail["is_default"] is not True:
        raise EmailTemplateError("The source template must be Zoho's module default.")
    if detail["bcc_mail_ids"] != []:
        raise EmailTemplateError(
            "REFUSED: the source Default template has a preset BCC. Cloning it would "
            "silently copy that BCC onto every target template."
        )
    if detail["cc_mail_ids"] != []:
        raise EmailTemplateError(
            "REFUSED: the source Default template already has a preset CC. Cloning it "
            "would silently merge an unapproved recipient into every target template."
        )
    return {key: json_copy(detail[key]) for key in SOURCE_CLONE_FIELDS}


def require_targets_absent(
    rows: list[dict[str, Any]], names: tuple[str, ...]
) -> list[dict[str, Any]]:
    existing = {normalized_key(row["name"]) for row in rows}
    for name in names:
        if normalized_key(name) in existing:
            raise EmailTemplateError(
                f"An invoice email template equivalent to {name!r} already exists. "
                "No template was created."
            )
    return [{"name": name, "present": False} for name in names]


def verify_created_template(
    detail: dict[str, Any],
    name: str,
    cc_mail_ids: tuple[str, ...],
    clone_fields: dict[str, Any],
) -> dict[str, Any]:
    """Full read-back: exact name, module, clone fidelity, CC order, BCC, default."""
    if detail["name"] != name:
        raise EmailTemplateError(f"Created template name is {detail['name']!r}, not {name!r}.")
    if detail["type"] != MODULE_EMAIL_TYPE:
        raise EmailTemplateError("Created template is not in the Invoices module.")
    if detail["is_default"] is not False:
        raise EmailTemplateError(
            f"Created template {name!r} became a default template. It must not be."
        )
    if detail["cc_mail_ids"] != list(cc_mail_ids):
        raise EmailTemplateError(
            f"Created template {name!r} CC list is {detail['cc_mail_ids']!r}, "
            f"not the approved {list(cc_mail_ids)!r}."
        )
    if detail["bcc_mail_ids"] != []:
        raise EmailTemplateError(f"Created template {name!r} has a BCC. It must be empty.")
    expected_placeholder = derived_placeholder(name)
    if detail["placeholder"] != expected_placeholder:
        raise EmailTemplateError(
            f"Created template {name!r} carries placeholder "
            f"{detail['placeholder']!r}, not Zoho's own {expected_placeholder!r} "
            "for that name."
        )
    for key in SOURCE_CLONE_FIELDS:
        if key == "bcc_mail_ids":
            continue
        if key in CANONICAL_HTML_FIELDS:
            # The ONE accepted difference: Zoho's Clone editor re-serializes the
            # inherited HTML with its attributes in a different order. Every
            # other field below is byte-exact.
            require_same_canonical_html(
                clone_fields[key], detail[key],
                f"Created template {name!r} {key}",
            )
            continue
        if detail[key] != clone_fields[key]:
            raise EmailTemplateError(
                f"Created template {name!r} did not preserve the source {key}."
            )
    return json_copy(detail)


def verify_source_unchanged(detail: dict[str, Any], staged: dict[str, Any]) -> None:
    if digest_for(detail) != digest_for(staged):
        raise EmailTemplateError(
            "The source Default invoice template changed. A new plan is required."
        )


# --------------------------------------------------------------------------
# Android-test confirmation for the second action.
# --------------------------------------------------------------------------

def require_delete_target(
    rows: list[dict[str, Any]], source_template_id: str
) -> dict[str, Any]:
    """The one deletable row, identified by ID AND name, or a refusal.

    Both constants must match the same live row. An ID alone is not enough: if
    Zoho ever reissued that ID to a different template, deleting by ID would
    destroy the wrong record. `source_template_id` comes from the FRESH live
    read, never from a constant, so the source this module clones from is
    excluded by what Zoho says it is right now.
    """
    matches = [
        row for row in rows
        if str(row.get("email_template_id") or "") == DELETE_TARGET_TEMPLATE_ID
    ]
    if len(matches) != 1:
        raise EmailTemplateError(
            f"Expected exactly one live template {DELETE_TARGET_TEMPLATE_ID}; Zoho "
            f"returned {len(matches)}. Nothing is deletable."
        )
    row = matches[0]
    if normalized_key(row.get("name")) != normalized_key(DELETE_TARGET_NAME):
        raise EmailTemplateError(
            f"Template {DELETE_TARGET_TEMPLATE_ID} is now named {row.get('name')!r}, not "
            f"{DELETE_TARGET_NAME!r}. Refusing to delete a record that is not the one "
            "commissioned."
        )
    # The source this module clones from can never be the target, whatever the
    # constants say.
    if str(row.get("email_template_id") or "") == positive_id(
        source_template_id, "live source template id"
    ):
        raise EmailTemplateError(
            f"REFUSED: {SOURCE_TEMPLATE_NAME} is the source template and is never deletable."
        )
    if row.get("is_default") is not False:
        raise EmailTemplateError(
            "REFUSED: this template is the organization default. Deleting a default "
            "invoice template would change what every customer receives."
        )
    if row.get("documents"):
        raise EmailTemplateError(
            "REFUSED: this template carries attached documents, so deleting it would "
            "destroy more than the accidental template. Reconcile by hand."
        )
    return json_copy(row)


def require_delete_contract_commissioned() -> None:
    """Fail closed BEFORE the replay lock and before any possible side effect.

    An honest refusal must never burn Rachad's plan, so this runs while the only
    thing held is the browser lane.
    """
    if not DELETE_CONTRACT_CAPTURED:
        raise EmailTemplateError(DELETE_NOT_CAPTURED)


def verify_template_deleted(
    rows_after: list[dict[str, Any]], staged_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """The target is gone, and NOTHING ELSE moved.

    Proving absence alone is not enough: a delete that also removed another
    template, or promoted a different one to default, would satisfy it.
    """
    after_by_id = {
        str(row.get("email_template_id") or ""): row for row in rows_after
    }
    if DELETE_TARGET_TEMPLATE_ID in after_by_id:
        raise EmailTemplateError(
            f"Template {DELETE_TARGET_TEMPLATE_ID} is still live after the delete. "
            "Stop and reconcile."
        )
    expected_survivors = {
        str(row.get("email_template_id") or ""): row for row in staged_rows
        if str(row.get("email_template_id") or "") != DELETE_TARGET_TEMPLATE_ID
    }
    missing = sorted(set(expected_survivors) - set(after_by_id))
    if missing:
        raise EmailTemplateError(
            "The delete removed template(s) it must not have: " + ", ".join(missing)
        )
    appeared = sorted(set(after_by_id) - set(expected_survivors))
    if appeared:
        raise EmailTemplateError(
            "Template(s) appeared during the delete: " + ", ".join(appeared)
        )
    for template_id, before_row in expected_survivors.items():
        if after_by_id[template_id] != before_row:
            raise EmailTemplateError(
                f"Surviving template {template_id} changed during the delete. "
                "Stop and reconcile."
            )
    return {
        "deleted_template_id": DELETE_TARGET_TEMPLATE_ID,
        "deleted_template_name": DELETE_TARGET_NAME,
        "surviving_template_ids": sorted(after_by_id),
    }


def delete_interceptor(organization_id: str, state: dict[str, Any]):
    """Let exactly ONE request through: the captured DELETE, and nothing else.

    Everything that is not a read is aborted before the network. The one
    commissioned delete is released only after its method, host, path, query and
    EMPTY body have each been proven to equal the captured contract, and only
    once. Only method, URL and body are inspected; headers and cookies never are.
    """
    expected_query = urlencode({"organization_id": organization_id})

    def intercept(route: Any, request: Any) -> None:
        if request.method in {"GET", "HEAD"}:
            route.continue_()
            return
        parsed = urlsplit(request.url)
        body = request.post_data or ""
        matches = (
            request.method == DELETE_METHOD
            and parsed.scheme == UI_SCHEME
            and parsed.hostname == UI_HOST
            and parsed.path == DELETE_PATH
            and parsed.query == expected_query
            and hashlib.sha256(body.encode("utf-8")).hexdigest() == DELETE_EMPTY_BODY_SHA256
        )
        if not matches:
            state.setdefault("blocked", []).append({
                "method": request.method,
                "host": parsed.hostname or "",
                "path": parsed.path,
            })
            route.abort("blockedbyclient")
            return
        state["seen"] = int(state.get("seen") or 0) + 1
        if state.get("allowed") or state["seen"] > 1:
            state["failure"] = state.get("failure") or (
                "Zoho emitted more than one delete request. Only one validated "
                "delete is ever allowed, so the extra was aborted."
            )
            route.abort("blockedbyclient")
            return
        # Validation is complete BEFORE the request is released to the network.
        state["allowed"] = True
        state["request"] = {
            "method": request.method,
            "path": parsed.path,
            "query": parsed.query,
            "body_sha256": DELETE_EMPTY_BODY_SHA256,
        }
        route.continue_()

    return intercept


def name_boundary(name: str) -> "re.Pattern[str]":
    """`name` as a whole value, so `Default` cannot match `Default Copy`.

    An alphanumeric lookaround rather than \\b, because \\b treats punctuation
    as a boundary and these names carry spaces and hyphens.
    """
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])")


def template_row(page: Any, name: str, *, must_not_match: str = "") -> Any:
    """The one visible table row for a template, by its displayed name.

    *** EVERY row-level control must come from INSIDE the row this returns. ***
    The page carries one `Show dropdown menu` button PER TEMPLATE, so a
    page-wide lookup for it stops resolving the moment a second template exists
    - which is exactly what happened once CC - Accounting was created on
    2026-08-11. Scoping to the row is what makes the lookup correct; the
    caller's own downstream identity check is what makes it safe.
    """
    locator = page.locator("tr").filter(has_text=name_boundary(name))
    rows = [locator.nth(index) for index in range(locator.count())
            if locator.nth(index).is_visible()]
    if len(rows) != 1:
        raise EmailTemplateError(
            f"Expected exactly one visible row for template {name!r}; found "
            f"{len(rows)}. Refusing to guess which row Zoho means."
        )
    if must_not_match and name_boundary(must_not_match).search(rows[0].inner_text()):
        raise EmailTemplateError(
            f"The row located for {name!r} also mentions {must_not_match!r}. "
            "Refusing to open a menu that might belong to the wrong template."
        )
    return rows[0]


def delete_template_via_ui(organization_id: str) -> dict[str, Any]:
    """Drive Zoho's own Delete control and release the one validated request."""
    require_delete_contract_commissioned()
    state: dict[str, Any] = {"seen": 0, "allowed": False, "failure": "", "blocked": []}
    # Imported HERE, not at module scope: the suite patches
    # playwright.sync_api.sync_playwright, and that only intercepts a call-time
    # import. This is the exact gap that let an unapproved live write through on
    # 2026-08-11, so the create path's shape is followed to the letter.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise EmailTemplateError(NO_SESSION) from exc

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=10_000)
        except Exception as exc:
            raise EmailTemplateError(NO_SESSION) from exc
        page = _authenticated_books_page(browser)
        intercept = delete_interceptor(organization_id, state)
        # Armed BEFORE the first navigation: from here nothing unvalidated can
        # reach the network.
        page.route("**/*", intercept)
        try:
            page.goto(SETTINGS_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(6_000)
            settings = urlsplit(page.url)
            if settings.scheme != UI_SCHEME or settings.hostname != UI_HOST:
                raise EmailTemplateError(
                    "Zoho Books navigation left the approved host (sign-in, consent "
                    "or CAPTCHA). Nothing was deleted."
                )
            row = template_row(
                page, DELETE_TARGET_NAME, must_not_match=SOURCE_TEMPLATE_NAME
            )
            _one_visible(
                row.get_by_role("button", name=ROW_DISCLOSURE_LABEL, exact=True),
                f"{DELETE_TARGET_NAME}-row {ROW_DISCLOSURE_LABEL!r} control",
            ).click(timeout=10_000)
            page.wait_for_timeout(900)
            _one_visible(
                page.locator(MENU_ITEM_SELECTOR).filter(
                    has_text=re.compile(rf"^{re.escape(DELETE_MENU_ITEM)}$")
                ),
                f"exact {DELETE_MENU_ITEM!r} menu item",
            ).click(timeout=10_000)
            page.wait_for_timeout(2_500)
            # Delete only OPENS the confirmation modal; the request fires on
            # confirming, and the control is taken from INSIDE the modal so a
            # row's own Delete can never be hit instead.
            modal = page.locator(".modal.show")
            if modal.count() != 1:
                raise EmailTemplateError(
                    f"Expected exactly one open confirmation modal; found "
                    f"{modal.count()}. Nothing was confirmed."
                )
            _one_visible(
                modal.get_by_role("button", name=DELETE_CONFIRM_LABEL, exact=True),
                f"exact {DELETE_CONFIRM_LABEL!r} confirm control inside the modal",
            ).click(timeout=10_000)
            page.wait_for_timeout(4_000)
        finally:
            # A confirmation modal must never be left standing on the shared
            # page: a stray click there would be a second, unapproved deletion.
            # A hash-route goto does NOT reload an SPA - that is why the capture's
            # own goto left one standing - so a real reload is what clears it.
            # No synthetic key events here on purpose: this module forbids them,
            # because typing was a rejected CC fallback, and the reload alone is
            # sufficient to clear the modal.
            try:
                page.reload(wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(3_000)
            except Exception:
                pass
            page.unroute("**/*", intercept)
    if state.get("failure"):
        raise EmailTemplateError(str(state["failure"]))
    if not state.get("allowed"):
        raise EmailTemplateError(
            "Zoho never emitted the captured delete request, so nothing was sent. "
            "Blocked instead: " + json.dumps(state.get("blocked") or [])
        )
    return dict(state["request"])


def validate_android_confirmation(raw: dict[str, Any]) -> dict[str, Any]:
    closed_fields(raw, ANDROID_CONFIRMATION_FIELDS, "Android-test confirmation")
    if raw["android_test_succeeded"] is not True:
        raise EmailTemplateError(
            "Android-test confirmation requires android_test_succeeded to be exactly true."
        )
    confirmed_by = clean_text(raw["confirmed_by"], "confirmed_by", 200)
    if confirmed_by != CONFIRMED_BY:
        raise EmailTemplateError(
            f"Only {CONFIRMED_BY} can confirm the Android test; Dado cannot supply it."
        )
    statement = clean_text(raw["statement"], "statement", 2000)
    source = clean_text(raw["source"], "source", 500)
    for label, value in (("statement", statement), ("source", source)):
        if "commission" in value.casefold():
            raise EmailTemplateError(
                f"Android-test confirmation {label} reads as a commissioning instruction. "
                "Commissioning is not approval and is not Android-test confirmation. "
                f"Record {CONFIRMED_BY}'s own words after he tested the "
                f"{ACCOUNTING_TEMPLATE} template on his phone."
            )
    confirmed = parse_time(raw["confirmed_utc"], "confirmed_utc")
    now = utc_now()
    if confirmed > now + timedelta(minutes=5):
        raise EmailTemplateError("Android-test confirmation is dated in the future.")
    if now - confirmed > timedelta(days=CONFIRMATION_MAX_AGE_DAYS):
        raise EmailTemplateError(
            f"Android-test confirmation is older than {CONFIRMATION_MAX_AGE_DAYS} days. "
            "Ask Rachad to re-confirm."
        )
    return {
        "android_test_succeeded": True,
        "confirmed_by": confirmed_by,
        "confirmed_utc": confirmed.isoformat(),
        "statement": statement,
        "source": source,
    }


def require_accounting_template(
    rows: list[dict[str, Any]],
    organization_id: str,
    clone_fields: dict[str, Any],
) -> dict[str, Any]:
    """Every post-Android action is refused unless that template still matches."""
    matches = [
        row for row in rows
        if normalized_key(row["name"]) == normalized_key(ACCOUNTING_TEMPLATE)
    ]
    if len(matches) != 1 or matches[0]["name"] != ACCOUNTING_TEMPLATE:
        raise EmailTemplateError(
            f"Exactly one invoice template named {ACCOUNTING_TEMPLATE!r} must already "
            "exist, and still be a faithful clone, before any further template may "
            "be staged."
        )
    template_id = matches[0]["email_template_id"]
    detail = project_source_detail(
        ui_template_detail(organization_id, template_id), template_id
    )
    return verify_created_template(
        detail, ACCOUNTING_TEMPLATE, TARGET_TEMPLATES[ACCOUNTING_TEMPLATE], clone_fields
    )


# --------------------------------------------------------------------------
# The captured NEW-FORM Save contract, decoded and closed.
#
# HISTORICAL NEGATIVE EVIDENCE. These functions read and check a request body;
# they never issue one, and nothing in the create path calls them. They exist
# so the reason New is never clicked stays provable rather than remembered.
# --------------------------------------------------------------------------

def parse_native_save_body(post_data: Any) -> dict[str, Any]:
    """Decode the captured form body into its exact closed native payload.

    Fails closed on any extra/missing form field, any extra/missing JSON key,
    more than one language block, or a non-``en`` language code.
    """
    if not isinstance(post_data, str) or not post_data:
        raise EmailTemplateError("Native Save body is missing.")
    try:
        pairs = parse_qsl(post_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise EmailTemplateError("Native Save body is not a URL-encoded form.") from exc
    if tuple(key for key, _ in pairs) != NATIVE_SAVE_FORM_KEYS:
        raise EmailTemplateError(
            "Native Save body must carry exactly the form fields "
            f"{list(NATIVE_SAVE_FORM_KEYS)} in that order."
        )
    form = dict(pairs)
    positive_id(form["organization_id"], "Native Save organization_id")
    try:
        payload = json.loads(form["JSONString"])
    except json.JSONDecodeError as exc:
        raise EmailTemplateError("Native Save JSONString is not valid JSON.") from exc
    closed_fields(payload, NATIVE_PAYLOAD_FIELDS, "Native Save payload")

    if payload["type"] != MODULE_EMAIL_TYPE:
        raise EmailTemplateError(
            "REFUSED: the native Save payload is not an invoice_notification template."
        )
    if payload["is_default"] is not False:
        raise EmailTemplateError(
            "REFUSED: the native Save payload would create a default template."
        )
    if payload["bcc_mail_ids"] != []:
        raise EmailTemplateError("REFUSED: the native Save payload carries a BCC.")
    if not isinstance(payload["from_address_id"], str):
        raise EmailTemplateError("Native Save from_address_id must be text.")
    if not isinstance(payload["cc_mail_ids"], list) or not payload["cc_mail_ids"]:
        raise EmailTemplateError("Native Save payload has no CC list.")
    for index, address in enumerate(payload["cc_mail_ids"]):
        require_fixed_address(address, f"Native Save cc_mail_ids[{index}]")

    blocks = payload["language_content"]
    if not isinstance(blocks, list) or len(blocks) != 1:
        raise EmailTemplateError(
            "REFUSED: the native Save payload must carry exactly one language block."
        )
    block = blocks[0]
    closed_fields(block, NATIVE_LANGUAGE_FIELDS, "Native Save language block")
    if block["language_code"] != NATIVE_LANGUAGE_CODE:
        raise EmailTemplateError(
            f"REFUSED: only the {NATIVE_LANGUAGE_CODE} language block is commissioned."
        )
    if block["is_default"] is not True:
        raise EmailTemplateError("Native Save language block must be the default language.")
    for key in ("subject", "body"):
        if not isinstance(block[key], str) or not block[key]:
            raise EmailTemplateError(f"Native Save language block {key} is empty.")
    return payload


def captured_native_payload() -> dict[str, Any]:
    """Decode the pinned capture artifact, verifying its recorded SHA-256."""
    captured = read_json_object(NATIVE_SAVE_ARTIFACT, "Native Save capture")
    if (
        captured.get("method") != NATIVE_SAVE_METHOD
        or captured.get("scheme") != UI_SCHEME
        or captured.get("host") != UI_HOST
        or captured.get("path") != NATIVE_SAVE_PATH
        or captured.get("query") != NATIVE_SAVE_QUERY
    ):
        raise EmailTemplateError(
            "The captured native Save request is not the exact commissioned "
            f"{NATIVE_SAVE_METHOD} {UI_SCHEME}://{UI_HOST}{NATIVE_SAVE_PATH} contract."
        )
    body = captured.get("post_data")
    digest = hashlib.sha256(str(body or "").encode("utf-8")).hexdigest()
    if not secrets.compare_digest(digest, NATIVE_SAVE_BODY_SHA256):
        raise EmailTemplateError(
            "The captured native Save body does not match its pinned SHA-256."
        )
    if not secrets.compare_digest(str(captured.get("post_data_sha256") or ""), digest):
        raise EmailTemplateError(
            "The capture artifact's own recorded SHA-256 does not match its body."
        )
    return parse_native_save_body(body)


def expected_native_payload(
    name: str,
    cc_mail_ids: tuple[str, ...],
    clone_fields: dict[str, Any],
) -> dict[str, Any]:
    """The exact payload a faithful Default clone would carry for one target.

    Built from the LIVE source, never from the capture: the capture proves the
    request shape, the live Default decides the content.
    """
    require_fixed_target(name, list(cc_mail_ids), "Native payload target")
    return {
        "bcc_mail_ids": [],
        "cc_mail_ids": list(cc_mail_ids),
        "from_address_id": clone_fields["from_address_id"],
        "is_default": False,
        "language_content": [{
            "body": clone_fields["body"],
            "is_default": True,
            "language_code": NATIVE_LANGUAGE_CODE,
            "subject": clone_fields["subject"],
        }],
        "name": name,
        "type": MODULE_EMAIL_TYPE,
    }


# --------------------------------------------------------------------------
# The captured NATIVE CLONE Save contract, decoded and closed. THIS is the
# create path. Same discipline as the New-form decoder above, different schema.
# --------------------------------------------------------------------------

def parse_clone_save_body(post_data: Any, organization_id: str) -> dict[str, Any]:
    """Decode one native Clone Save form body into its exact closed payload.

    Fails closed on any extra/missing form field, a foreign organization, any
    extra/missing JSON key, a foreign module, a default template, any BCC, any
    address outside the three fixed internal ones, or an unfixed template name.
    """
    org_id = positive_id(organization_id, "books_organization_id")
    if not isinstance(post_data, str) or not post_data:
        raise EmailTemplateError("Clone Save body is missing.")
    try:
        pairs = parse_qsl(post_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise EmailTemplateError("Clone Save body is not a URL-encoded form.") from exc
    if tuple(key for key, _ in pairs) != CLONE_SAVE_FORM_KEYS:
        raise EmailTemplateError(
            "Clone Save body must carry exactly the form fields "
            f"{list(CLONE_SAVE_FORM_KEYS)} in that order."
        )
    form = dict(pairs)
    if form["organization_id"] != org_id:
        raise EmailTemplateError(
            "REFUSED: the Clone Save body names a different Zoho Books organization."
        )
    try:
        payload = json.loads(form["JSONString"])
    except json.JSONDecodeError as exc:
        raise EmailTemplateError("Clone Save JSONString is not valid JSON.") from exc
    closed_fields(payload, CLONE_PAYLOAD_FIELDS, "Clone Save payload")

    if payload["type"] != MODULE_EMAIL_TYPE:
        raise EmailTemplateError(
            "REFUSED: the Clone Save payload is not an invoice_notification template."
        )
    if payload["is_default"] is not False:
        raise EmailTemplateError(
            "REFUSED: the Clone Save payload would create a default template."
        )
    if payload["bcc_mail_ids"] != []:
        raise EmailTemplateError("REFUSED: the Clone Save payload carries a BCC.")
    if not isinstance(payload["from_address_id"], str):
        raise EmailTemplateError("Clone Save from_address_id must be text.")
    if not isinstance(payload["cc_mail_ids"], list):
        raise EmailTemplateError("Clone Save cc_mail_ids must be a list.")
    require_fixed_target(payload["name"], payload["cc_mail_ids"], "Clone Save payload")
    for key in ("subject", "body"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise EmailTemplateError(f"Clone Save payload {key} is empty.")
    return payload


def captured_clone_payload() -> dict[str, Any]:
    """Decode the pinned Clone capture artifact, verifying its recorded SHA-256."""
    captured = read_json_object(CLONE_SAVE_ARTIFACT, "Clone Save capture")
    if (
        captured.get("method") != CLONE_SAVE_METHOD
        or captured.get("scheme") != UI_SCHEME
        or captured.get("host") != UI_HOST
        or captured.get("path") != CLONE_SAVE_PATH
        or captured.get("query") != CLONE_SAVE_QUERY
    ):
        raise EmailTemplateError(
            "The captured native Clone Save request is not the exact commissioned "
            f"{CLONE_SAVE_METHOD} {UI_SCHEME}://{UI_HOST}{CLONE_SAVE_PATH} contract."
        )
    body = captured.get("post_data")
    digest = hashlib.sha256(str(body or "").encode("utf-8")).hexdigest()
    if not secrets.compare_digest(digest, CLONE_SAVE_BODY_SHA256):
        raise EmailTemplateError(
            "The captured native Clone Save body does not match its pinned SHA-256."
        )
    if not secrets.compare_digest(str(captured.get("post_data_sha256") or ""), digest):
        raise EmailTemplateError(
            "The Clone capture artifact's own recorded SHA-256 does not match its body."
        )
    form = dict(parse_qsl(str(body), keep_blank_values=True, strict_parsing=True))
    return parse_clone_save_body(body, form["organization_id"])


def expected_clone_payload(
    name: str,
    cc_mail_ids: tuple[str, ...],
    clone_fields: dict[str, Any],
) -> dict[str, Any]:
    """The exact payload a faithful Default clone must carry for one target.

    Built from the LIVE source, never from the capture: the capture proves the
    request shape, the live Default decides the content.
    """
    require_fixed_target(name, list(cc_mail_ids), "Clone payload target")
    return {
        "bcc_mail_ids": [],
        "body": clone_fields["body"],
        "cc_mail_ids": list(cc_mail_ids),
        "from_address_id": clone_fields["from_address_id"],
        "is_default": False,
        "name": name,
        "subject": clone_fields["subject"],
        "type": MODULE_EMAIL_TYPE,
    }


def require_clone_payload_matches(
    payload: dict[str, Any], expected: dict[str, Any], label: str
) -> None:
    """Byte-exact on every field except the body, which may only be reordered."""
    closed_fields(payload, CLONE_PAYLOAD_FIELDS, label)
    for key in sorted(CLONE_PAYLOAD_FIELDS):
        if key == "body":
            require_same_canonical_html(expected["body"], payload["body"], f"{label} body")
            continue
        if payload[key] != expected[key]:
            raise EmailTemplateError(
                f"REFUSED: {label} {key} is {payload[key]!r}, not the approved "
                f"{expected[key]!r}."
            )


# --------------------------------------------------------------------------
# The create seam. Zoho's own native Clone control, and nothing else.
# --------------------------------------------------------------------------

ACCEPTED_EQUIVALENCE = (
    "Rachad Homsi answered YES on 2026-08-11 to accepting ONE narrow difference: "
    "Zoho's native Clone editor re-serializes the inherited body with its HTML "
    "attributes in a different order (href/style on the PAY NOW link, class/style "
    "on its two nested spans). Both bodies are 2,131 characters, parse to exactly "
    f"{CLONE_CANONICAL_EVENT_COUNT} canonical events each, and every event is "
    "equal. No element, nesting, text, placeholder, link, attribute name, "
    "attribute value, style value, signature or order changes. Nothing beyond "
    "attribute ORDER is accepted anywhere."
)

CREATE_NOT_COMMISSIONED = (
    "NOT COMMISSIONED: the native Clone create workflow is switched off in this "
    "build. No plan may be committed and nothing is created."
)

NEW_FORM_NEGATIVE_EVIDENCE_TEXT = (
    "Zoho's New invoice-template form emits its own stock factory body (BALANCE "
    "DUE / %Balance% / MAKE PAYMENT / Regards %UserName% %CompanyName%), not this "
    "organization's Default body (INVOICE AMOUNT / %Total% / PAY NOW / Regards "
    "Accounting Departement). New is therefore never clicked; the create path is "
    "the Default row's own native Clone control."
)


def new_form_negative_evidence(clone_fields: dict[str, Any]) -> dict[str, Any]:
    """Prove, from the pinned New-form capture, why New is not the create path.

    This is recorded in every plan so the exclusion stays evidence rather than
    folklore. It is never a gate on creation and never selects a workflow.
    """
    subject_digest = hashlib.sha256(
        str(clone_fields["subject"]).encode("utf-8")
    ).hexdigest()
    body_digest = hashlib.sha256(str(clone_fields["body"]).encode("utf-8")).hexdigest()
    return {
        "artifact": str(NATIVE_SAVE_ARTIFACT),
        "new_form_body_sha256": NATIVE_FORM_BODY_SHA256,
        "live_default_body_sha256": body_digest,
        "new_form_subject_matches_live": secrets.compare_digest(
            subject_digest, NATIVE_FORM_SUBJECT_SHA256
        ),
        "new_form_body_matches_live": secrets.compare_digest(
            body_digest, NATIVE_FORM_BODY_SHA256
        ),
        "new_form_is_used_for_creation": False,
        "reason": NEW_FORM_NEGATIVE_EVIDENCE_TEXT,
    }


def require_create_contract_commissioned(clone_fields: dict[str, Any]) -> dict[str, Any]:
    """Fail closed BEFORE the replay lock and before any possible side effect.

    Proves the pinned Clone capture is still the exact contract AND that the
    body Zoho's Clone editor produced is still canonically identical to the live
    Default. If the live Default is edited in a way the Clone form would not
    reproduce, this refuses instead of creating a non-clone.
    """
    if not CREATE_WORKFLOW_COMMISSIONED:
        raise EmailTemplateError(CREATE_NOT_COMMISSIONED)
    captured = captured_clone_payload()
    if captured["name"] != ACCOUNTING_TEMPLATE:
        raise EmailTemplateError(
            "The pinned Clone capture is not the fixed "
            f"{ACCOUNTING_TEMPLATE} capture."
        )
    if captured["cc_mail_ids"] != list(TARGET_TEMPLATES[ACCOUNTING_TEMPLATE]):
        raise EmailTemplateError("The pinned Clone capture carries an unexpected CC list.")
    for key in ("subject", "from_address_id", "type"):
        if captured[key] != clone_fields[key]:
            raise EmailTemplateError(
                f"REFUSED: the live Default {key} no longer matches the {key} Zoho's "
                "Clone form emitted during the captured Save. " + ACCEPTED_EQUIVALENCE
            )
    require_same_canonical_html(
        clone_fields["body"], captured["body"], "Captured Clone Save body"
    )
    return {
        "clone_save_body_sha256": CLONE_SAVE_BODY_SHA256,
        "live_default_body_sha256": hashlib.sha256(
            str(clone_fields["body"]).encode("utf-8")
        ).hexdigest(),
        "clone_posted_body_sha256": CLONE_POSTED_BODY_SHA256,
        "body_byte_equal": clone_fields["body"] == captured["body"],
        "body_canonical_html_equal": True,
        "canonical_events": len(canonical_html_events(clone_fields["body"], "live Default body")),
        "accepted_equivalence": ACCEPTED_EQUIVALENCE,
    }


# The exact controls the blocked live capture proved. Every one is bounded by an
# exact visible label or an exact data attribute; none is caller-supplied.
ROW_DISCLOSURE_LABEL = "Show dropdown menu"
CLONE_MENU_ITEM = "Clone"
SAVE_BUTTON_LABEL = "Save"
MENU_ITEM_SELECTOR = (
    '[role="menuitem"]:visible, .dropdown-menu li:visible, .dropdown-item:visible'
)
NAME_INPUT_SELECTOR = 'input[data-auto-gen-binding-key="name"]:visible'
CLONE_FORM_PATH_MARKER = "/settings/emails/templates/edit"
CLONE_FORM_QUERY_KEY = "clone_email_template_id"
# Controls that must never be touched. Asserted by the tests against the source.
NEVER_CLICKED = ("New", "Edit", "Delete", "Mark as Default", "Associate")

# Read-only DOM snapshot of the Clone form, lifted verbatim from the blocked
# capture driver. It reads labels, list items and two fixed binding-key inputs.
# It never reads cookies, storage, tokens or headers.
FORM_SNAPSHOT_JS = """() => {
    const byLabel = wanted => {
        const label = Array.from(document.querySelectorAll('label')).find(
            el => (el.innerText || '').trim() === wanted);
        return label ? (label.closest('.row') || label.parentElement) : null;
    };
    const rowText = wanted => {
        const row = byLabel(wanted);
        return row ? (row.innerText || '').trim() : null;
    };
    const selected = wanted => {
        const row = byLabel(wanted);
        if (!row) return null;
        return Array.from(row.querySelectorAll('li'))
            .map(li => (li.innerText || '').trim()).filter(Boolean);
    };
    const inputValue = wanted => {
        const row = byLabel(wanted);
        const input = row ? row.querySelector('input[role="combobox"]') : null;
        return input ? input.value : null;
    };
    const name = document.querySelector('input[data-auto-gen-binding-key="name"]');
    const def = document.querySelector('input[data-auto-gen-binding-key="is_default"]');
    const subject = byLabel('Subject');
    return {
        url: location.href,
        name: name ? name.value : null,
        from_text: rowText('From'),
        cc_text: rowText('Cc'),
        cc_selected: selected('Cc'),
        cc_input_value: inputValue('Cc'),
        bcc_text: rowText('Bcc'),
        bcc_selected: selected('Bcc'),
        bcc_input_value: inputValue('Bcc'),
        subject_text: subject ? (subject.innerText || '').trim() : null,
        is_default: def ? def.checked : null,
    };
}"""


def clone_save_interceptor(
    organization_id: str, expected: dict[str, Any], state: dict[str, Any]
):
    """Build the route handler that lets exactly ONE validated POST through.

    Everything that is not a read is aborted before the network. The one
    commissioned write is allowed only after its complete decoded body has been
    proven to equal the approved payload, and only once -- a second attempt is
    aborted and recorded as a failure. Only method, URL and body are inspected;
    request headers and cookies are never read.
    """
    def intercept(route: Any, request: Any) -> None:
        if request.method in {"GET", "HEAD"}:
            route.continue_()
            return
        parsed = urlsplit(request.url)
        if not (
            request.method == CLONE_SAVE_METHOD
            and parsed.scheme == UI_SCHEME
            and parsed.hostname == UI_HOST
            and parsed.path == CLONE_SAVE_PATH
            and parsed.query == CLONE_SAVE_QUERY
        ):
            state.setdefault("blocked", []).append({
                "method": request.method,
                "scheme": parsed.scheme,
                "host": parsed.hostname or "",
                "path": parsed.path,
            })
            route.abort("blockedbyclient")
            return
        state["seen"] = int(state.get("seen") or 0) + 1
        if state.get("allowed") or state["seen"] > 1:
            state["failure"] = state.get("failure") or (
                "Zoho emitted more than one email-template Save request. Only one "
                "validated write is ever allowed, so the extra was aborted."
            )
            route.abort("blockedbyclient")
            return
        try:
            payload = parse_clone_save_body(request.post_data, organization_id)
            require_clone_payload_matches(payload, expected, "Clone Save")
        except EmailTemplateError as exc:
            state["failure"] = str(exc)
            route.abort("blockedbyclient")
            return
        # Validation is complete BEFORE the request is released to the network.
        state["allowed"] = True
        state["payload"] = payload
        route.continue_()

    return intercept


def _one_visible(locator: Any, label: str) -> Any:
    shown = [locator.nth(index) for index in range(locator.count())
             if locator.nth(index).is_visible()]
    if len(shown) != 1:
        raise EmailTemplateError(
            f"Expected exactly one visible {label}; found {len(shown)}. Refusing to "
            "guess which control Zoho means."
        )
    return shown[0]


def create_template_via_ui(
    organization_id: str,
    source_template_id: str,
    name: str,
    cc_mail_ids: tuple[str, ...],
    clone_fields: dict[str, Any],
) -> str:
    """Clone the fixed Default row into exactly one fixed template. ONE attempt.

    Opens only the Default row's exact disclosure menu and its exact Clone item,
    changes only the fixed Template Name and the fixed Cc options, and permits
    exactly one fully validated POST. New and Edit are never clicked. There is no
    retry: any timeout, drift, redirect, login, duplicate or unexpected response
    raises, and the caller permanently locks the plan.
    """
    org_id = positive_id(organization_id, "books_organization_id")
    source_id = positive_id(source_template_id, "source email_template_id")
    require_fixed_target(name, list(cc_mail_ids), "Clone target")
    expected = expected_clone_payload(name, cc_mail_ids, clone_fields)
    options = [CC_OPTION_TEXT[address] for address in cc_mail_ids]
    state: dict[str, Any] = {"seen": 0, "allowed": False, "failure": "", "blocked": []}

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise EmailTemplateError(NO_SESSION) from exc

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=10_000)
        except Exception as exc:
            raise EmailTemplateError(NO_SESSION) from exc
        page = _authenticated_books_page(browser)
        intercept = clone_save_interceptor(org_id, expected, state)
        # Armed BEFORE the first navigation, exactly as the blocked capture ran:
        # from here on, no non-read request can reach the network unvalidated.
        page.route("**/*", intercept)
        try:
            page.goto(SETTINGS_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(6_000)
            settings = urlsplit(page.url)
            if settings.scheme != UI_SCHEME or settings.hostname != UI_HOST:
                raise EmailTemplateError(
                    "Zoho Books navigation left the approved host (sign-in, consent "
                    "or CAPTCHA). Nothing was saved."
                )

            # Scoped to the Default ROW. A page-wide lookup worked only while
            # Default was the sole template; from the moment a second one exists
            # it resolves to several buttons and this refuses instead of cloning.
            # The clone_email_template_id check below is what proves the right
            # template was opened - this only makes the control findable.
            source_row = template_row(page, SOURCE_TEMPLATE_NAME)
            _one_visible(
                source_row.get_by_role("button", name=ROW_DISCLOSURE_LABEL, exact=True),
                f"{SOURCE_TEMPLATE_NAME}-row {ROW_DISCLOSURE_LABEL!r} control",
            ).click(timeout=10_000)
            page.wait_for_timeout(700)
            _one_visible(
                page.locator(MENU_ITEM_SELECTOR).filter(
                    has_text=re.compile(rf"^{re.escape(CLONE_MENU_ITEM)}$")
                ),
                f"exact {CLONE_MENU_ITEM!r} menu item",
            ).click(timeout=10_000)
            page.wait_for_timeout(3_000)

            if CLONE_FORM_PATH_MARKER not in page.url:
                raise EmailTemplateError(
                    f"The {SOURCE_TEMPLATE_NAME} Clone form did not open. Nothing was saved."
                )
            if f"{CLONE_FORM_QUERY_KEY}={source_id}" not in page.url:
                raise EmailTemplateError(
                    f"The Clone form is not cloning email template {source_id}. "
                    "Nothing was saved."
                )

            before = page.evaluate(FORM_SNAPSHOT_JS)
            if before.get("name") != SOURCE_TEMPLATE_NAME:
                raise EmailTemplateError(
                    f"The Clone form did not inherit the {SOURCE_TEMPLATE_NAME} name."
                )
            if before.get("cc_selected") or before.get("cc_input_value"):
                raise EmailTemplateError("The Clone form CC is not empty.")
            if before.get("bcc_selected") or before.get("bcc_input_value"):
                raise EmailTemplateError("The Clone form BCC is not empty.")
            if before.get("is_default") is not False:
                raise EmailTemplateError("The Clone form is marked as the default template.")
            for key in ("from_text", "subject_text"):
                if not before.get(key):
                    raise EmailTemplateError(
                        f"The Clone form did not inherit the source {key}."
                    )

            _one_visible(page.locator(NAME_INPUT_SELECTOR), "Template Name input").fill(name)

            cc_row = page.locator("div.form-group.row").filter(
                has=_one_visible(
                    page.locator("label").filter(
                        has_text=re.compile(rf"^{re.escape(CC_ROW_LABEL)}$")
                    ),
                    f"{CC_ROW_LABEL} label",
                )
            )
            for option_text in options:
                _one_visible(
                    cc_row.locator(CC_DROPDOWN_TOGGLER),
                    f"{CC_ROW_LABEL} dropdown toggler",
                ).click(timeout=10_000)
                page.wait_for_timeout(500)
                _one_visible(
                    page.get_by_role("option", name=option_text, exact=True),
                    f"exact Cc option {option_text!r}",
                ).click(timeout=10_000)
                page.wait_for_timeout(500)

            after = page.evaluate(FORM_SNAPSHOT_JS)
            if after.get("name") != name:
                raise EmailTemplateError("The Template Name did not retain the fixed value.")
            if list(after.get("cc_selected") or []) != options:
                raise EmailTemplateError(
                    f"The Cc row holds {after.get('cc_selected')!r}, not the approved "
                    f"{options!r} in that order."
                )
            if after.get("cc_input_value"):
                raise EmailTemplateError("The Cc row retained uncommitted text.")
            if after.get("bcc_selected") or after.get("bcc_input_value"):
                raise EmailTemplateError("The BCC changed while the Cc was filled.")
            if after.get("is_default") is not False:
                raise EmailTemplateError("The default checkbox changed while filling values.")
            for key in ("from_text", "subject_text"):
                if before.get(key) != after.get(key):
                    raise EmailTemplateError(f"The {key} changed while filling values.")

            save = _one_visible(
                page.get_by_role("button", name=SAVE_BUTTON_LABEL, exact=True),
                f"{SAVE_BUTTON_LABEL} button",
            )
            try:
                with page.expect_response(
                    lambda response: (
                        response.request.method == CLONE_SAVE_METHOD
                        and urlsplit(response.url).hostname == UI_HOST
                        and urlsplit(response.url).path == CLONE_SAVE_PATH
                    ),
                    timeout=60_000,
                ) as caught:
                    save.click(timeout=10_000)
                response = caught.value
            except PlaywrightTimeoutError as exc:
                raise EmailTemplateError(
                    "The Clone Save produced no answer from Zoho. "
                    + (state.get("failure") or
                       "Nothing validated was released, so no write was sent.")
                ) from exc
            if state.get("failure"):
                raise EmailTemplateError(state["failure"])
            if not state.get("allowed"):
                raise EmailTemplateError(
                    "The Clone Save request was never validated and released."
                )
            if not 200 <= int(response.status) < 300:
                raise EmailTemplateError(
                    f"Zoho rejected the Clone Save with HTTP {response.status}."
                )
            try:
                answer = json.loads(response.text())
            except Exception as exc:
                raise EmailTemplateError(
                    "Zoho's Clone Save answer was not JSON, so the result is "
                    "indeterminate."
                ) from exc
            if not isinstance(answer, dict) or answer.get("code") not in (None, 0):
                raise EmailTemplateError(
                    "Zoho refused the Clone Save: "
                    + str((answer or {}).get("message") or (answer or {}).get("code"))
                )

            # The ID comes from a fresh read of Zoho's own list, not from trusting
            # the write's answer. If the answer does carry one, it must agree.
            rows = invoice_template_rows(ui_list_templates(org_id, page=page))
            matches = [row for row in rows if row["name"] == name]
            if len(matches) != 1:
                raise EmailTemplateError(
                    f"After the Clone Save, Zoho lists {len(matches)} invoice templates "
                    f"named {name!r}. The result is indeterminate."
                )
            template_id = matches[0]["email_template_id"]
            answered = answer.get("email_template")
            if isinstance(answered, dict) and answered.get("email_template_id") is not None:
                if positive_id(answered["email_template_id"], "created id") != template_id:
                    raise EmailTemplateError(
                        "Zoho's Clone Save answer names a different template than the "
                        "one now listed. The result is indeterminate."
                    )
            return template_id
        finally:
            # Leave the shared browser on the harmless list route rather than an
            # edit form the other lane could stumble into.
            with contextlib.suppress(Exception):
                page.goto(SETTINGS_URL, wait_until="domcontentloaded", timeout=60_000)
            with contextlib.suppress(Exception):
                page.unroute("**/*", intercept)


# --------------------------------------------------------------------------
# Read-only exposure check. GET only -- there is no send route in this module.
# --------------------------------------------------------------------------

def verify_template_exposed(
    access_token: str,
    vault: dict[str, Any],
    invoice_id: str,
    template_id: str,
    cc_mail_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Read the invoice email-content endpoint and confirm the template resolves.

    This is verification only. It is a GET. Nothing is sent, and this tool has
    no route that could send anything.
    """
    invoice_id = positive_id(invoice_id, "invoice_id")
    template_id = positive_id(template_id, "email_template_id")
    query = urlencode({
        "organization_id": positive_id(
            vault.get("books_organization_id"), "books_organization_id"
        ),
        "email_template_id": template_id,
    })
    result = zoho_tool.api_get(
        access_token,
        str(vault["api_domain"]),
        f"/books/v3/invoices/{invoice_id}/email?{query}",
    )
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    templates = data.get("emailtemplates") if isinstance(data, dict) else None
    exposed = False
    if isinstance(templates, list):
        exposed = any(
            isinstance(row, dict)
            and str(row.get("email_template_id") or "") == template_id
            for row in templates
        )
    cc_list = data.get("cc_mails_list") if isinstance(data, dict) else None
    resolved = [
        str(row.get("email") or row) if isinstance(row, dict) else str(row)
        for row in (cc_list if isinstance(cc_list, list) else [])
    ]
    return {
        "template_exposed_to_composition": exposed,
        "resolved_cc": resolved,
        "resolved_cc_matches_plan": resolved == list(cc_mail_ids),
        "email_sent": False,
    }


# --------------------------------------------------------------------------
# Plan staging, validation, replay locking.
# --------------------------------------------------------------------------

def stage_plan(
    action: str,
    organization: dict[str, Any],
    source: dict[str, Any],
    targets: list[dict[str, Any]],
    confirmation: dict[str, Any] | None,
    live_evidence: dict[str, Any],
    new_form_evidence: dict[str, Any],
) -> Path:
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "action": action,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "approval_required": APPROVAL_WORD,
        "origin": origin_record(),
        "organization": organization,
        "module": {"module": MODULE_NAME, "email_type": MODULE_EMAIL_TYPE,
                   "type_formatted": MODULE_TYPE_FORMATTED},
        "source": source,
        "targets": targets,
        "sources": {
            "live_evidence": (
                f"Read-only GET {TEMPLATE_LIST_PATH} and "
                f"{TEMPLATE_LIST_PATH}/{{email_template_id}} in the dedicated "
                f"authenticated Books session on {created.date().isoformat()}."
            ),
            "official_doc": OFFICIAL_DOC,
            "investigation": INVESTIGATION_SOURCE,
        },
        "android_test_confirmation": confirmation,
        "workflow": {
            "host": UI_HOST,
            "settings_url": SETTINGS_URL,
            "list_path": TEMPLATE_LIST_PATH,
            "detail_path": TEMPLATE_LIST_PATH + "/{email_template_id}",
            "read_transport": "same-origin GET inside the authenticated Books page",
            "create_mechanism": (
                "Zoho's own native Clone control on the fixed Default row, captured "
                f"and pinned: one {CLONE_SAVE_METHOD} "
                f"{UI_SCHEME}://{UI_HOST}{CLONE_SAVE_PATH}, allowed exactly once "
                "after its complete decoded body matches the approved payload."
            ),
            "clicks": [
                f"{SOURCE_TEMPLATE_NAME} row {ROW_DISCLOSURE_LABEL!r} disclosure",
                f"exact {CLONE_MENU_ITEM!r} menu item",
                "Template Name (fixed value)",
                f"{CC_ROW_LABEL} {CC_DROPDOWN_TOGGLER} + exact role=option per fixed address",
                f"{SAVE_BUTTON_LABEL} (one validated POST)",
            ],
            "never_clicked": list(NEVER_CLICKED),
            "clone_save_contract_captured": CLONE_SAVE_CONTRACT_CAPTURED,
            "clone_save_body_sha256": CLONE_SAVE_BODY_SHA256,
            "clone_save_artifact": str(CLONE_SAVE_ARTIFACT),
            "clone_fidelity_artifact": str(CLONE_FIDELITY_ARTIFACT),
            "accepted_equivalence": ACCEPTED_EQUIVALENCE,
            "native_save_contract_captured": NATIVE_SAVE_CONTRACT_CAPTURED,
            "native_save_body_sha256": NATIVE_SAVE_BODY_SHA256,
            "new_form_negative_evidence": new_form_evidence,
            "create_workflow_commissioned": CREATE_WORKFLOW_COMMISSIONED,
            "create_blocker": None if CREATE_WORKFLOW_COMMISSIONED else CREATE_NOT_COMMISSIONED,
            "browser_lane_lock": "zoho (CDP 127.0.0.1:9228), acquired before the plan lock",
            "cdp_endpoint": CDP_ENDPOINT,
        },
        "risks": list(RISKS),
        "live_evidence": live_evidence,
        "email_sent": False,
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = (PLAN_DIR / f"{stamp}_{action}_{digest[:16]}.json").resolve()
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError as exc:
        raise EmailTemplateError("Refused to overwrite an existing email-template plan.") from exc
    zoho_tool.append_receipt(
        f"zoho_email_template_{action}_plan_staged_not_committed",
        f"plan={path}; sha256={digest}; targets={','.join(ACTION_TARGETS[action])}; "
        "zoho_writes=0; emails_sent=0",
    )
    return path


def validate_organization(organization: Any, vault: dict[str, Any]) -> None:
    closed_fields(organization, {"books_organization_id", "name", "fingerprint"},
                  "Plan organization")
    org_id = positive_id(organization["books_organization_id"], "books_organization_id")
    if org_id != positive_id(vault.get("books_organization_id"), "books_organization_id"):
        raise EmailTemplateError(
            "Plan was staged against a different Zoho Books organization."
        )
    expected = digest_for({
        "books_organization_id": org_id,
        "name": organization["name"],
    })
    if not secrets.compare_digest(str(organization["fingerprint"]), expected):
        raise EmailTemplateError("Plan organization fingerprint is invalid.")


def validate_plan(plan: dict[str, Any], action: str) -> None:
    closed_fields(plan, PLAN_FIELDS, "Plan")
    if (
        plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("tool") != TOOL_NAME
        or plan.get("tool_version") != TOOL_VERSION
        or plan.get("approval_required") != APPROVAL_WORD
    ):
        raise EmailTemplateError(
            "Plan schema version, tool, tool version, or approval requirement is invalid."
        )
    if plan.get("action") not in ACTIONS or plan.get("action") != action:
        raise EmailTemplateError("Plan action is not the requested commissioned action.")
    if plan.get("email_sent") is not False:
        raise EmailTemplateError("Plan email_sent must be exactly false.")
    require_origin(plan.get("origin"))
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise EmailTemplateError("Plan nonce is invalid.")
    created = parse_time(plan["created_utc"], "Plan creation time")
    expires = parse_time(plan["expires_utc"], "Plan expiry")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise EmailTemplateError("Plan must have exactly a 24-hour lifetime.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise EmailTemplateError("Plan creation time is in the future.")
    if now >= expires:
        raise EmailTemplateError("Plan expired. Stage a new plan for review.")

    module = plan.get("module")
    closed_fields(module, {"module", "email_type", "type_formatted"}, "Plan module")
    if (
        module["module"] != MODULE_NAME
        or module["email_type"] != MODULE_EMAIL_TYPE
        or module["type_formatted"] != MODULE_TYPE_FORMATTED
    ):
        raise EmailTemplateError("Plan module is not the fixed Invoices module.")

    source = plan.get("source")
    closed_fields(source, {"email_template_id", "name", "clone_fields",
                           "clone_fields_sha256"}, "Plan source")
    positive_id(source["email_template_id"], "source email_template_id")
    if source["name"] != SOURCE_TEMPLATE_NAME:
        raise EmailTemplateError(
            f"Plan source template must be named exactly {SOURCE_TEMPLATE_NAME}."
        )
    closed_fields(source["clone_fields"], set(SOURCE_CLONE_FIELDS), "Plan clone fields")
    if not secrets.compare_digest(
        str(source["clone_fields_sha256"]), digest_for(source["clone_fields"])
    ):
        raise EmailTemplateError("Plan clone-field fingerprint is invalid.")
    if source["clone_fields"]["bcc_mail_ids"] != []:
        raise EmailTemplateError("Plan clone fields carry a BCC. Staging should have refused.")

    targets = plan.get("targets")
    expected_names = ACTION_TARGETS[action]
    if not isinstance(targets, list) or len(targets) != len(expected_names):
        raise EmailTemplateError(
            f"Plan action {action} must carry exactly {len(expected_names)} targets."
        )
    for index, target in enumerate(targets):
        closed_fields(target, {"name", "cc_mail_ids", "is_default", "customer_associated"},
                      f"Plan target[{index}]")
        name, cc = require_fixed_target(
            target["name"], target["cc_mail_ids"], f"Plan target[{index}]"
        )
        if name != expected_names[index]:
            raise EmailTemplateError(
                f"Plan target[{index}] must be {expected_names[index]!r}."
            )
        if target["is_default"] is not False or target["customer_associated"] is not False:
            raise EmailTemplateError(
                f"Plan target {name!r} must be non-default and customer-unassociated."
            )
        if list(cc) != target["cc_mail_ids"]:
            raise EmailTemplateError(f"Plan target {name!r} CC list is not canonical.")

    confirmation = plan.get("android_test_confirmation")
    if action in ANDROID_CONFIRMED_ACTIONS:
        if not isinstance(confirmation, dict):
            raise EmailTemplateError(
                f"Action {action} requires Rachad's own recorded "
                "Android-test confirmation."
            )
        if validate_android_confirmation(confirmation) != confirmation:
            raise EmailTemplateError("Plan Android-test confirmation is not canonical.")
    elif confirmation is not None:
        raise EmailTemplateError(
            "The first Android-test plan must not carry an Android-test confirmation."
        )

    workflow = plan.get("workflow")
    if not isinstance(workflow, dict) or workflow.get("host") != UI_HOST:
        raise EmailTemplateError("Plan workflow host is not the approved Books host.")
    if workflow.get("list_path") != TEMPLATE_LIST_PATH:
        raise EmailTemplateError("Plan workflow read path is not the commissioned path.")
    if NON_ATOMIC_RISK not in (plan.get("risks") or []):
        raise EmailTemplateError("Plan does not state the non-atomicity risk.")

    live = plan.get("live_evidence")
    closed_fields(live, {"invoice_templates", "invoice_templates_sha256",
                         "source_detail", "source_detail_sha256", "targets_absent",
                         "accounting_template", "accounting_template_sha256"},
                  "Plan live evidence")
    if not secrets.compare_digest(
        str(live["invoice_templates_sha256"]), digest_for(live["invoice_templates"])
    ):
        raise EmailTemplateError("Plan template-list fingerprint is invalid.")
    if not secrets.compare_digest(
        str(live["source_detail_sha256"]), digest_for(live["source_detail"])
    ):
        raise EmailTemplateError("Plan source-detail fingerprint is invalid.")
    if not secrets.compare_digest(
        str(live["accounting_template_sha256"]), digest_for(live["accounting_template"])
    ):
        raise EmailTemplateError("Plan accounting-template fingerprint is invalid.")
    if action in ANDROID_CONFIRMED_ACTIONS:
        if not isinstance(live["accounting_template"], dict):
            raise EmailTemplateError(
                f"Action {action} requires verified live {ACCOUNTING_TEMPLATE} evidence."
            )
    elif live["accounting_template"] is not None:
        raise EmailTemplateError(
            f"The first plan must be staged while {ACCOUNTING_TEMPLATE} does not exist."
        )
    if [row["name"] for row in live["targets_absent"]] != list(expected_names):
        raise EmailTemplateError("Plan absence evidence does not match the target set.")


def load_plan(path: Path, action: str, vault: dict[str, Any]) -> dict[str, Any]:
    plan = read_json_object(path, "Plan")
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise EmailTemplateError("Plan hash check failed. The plan changed after review.")
    validate_plan(plan, action)
    validate_organization(plan["organization"], vault)
    return plan


def lock_path(plan_sha256: str) -> Path:
    if not HEX_64_RE.fullmatch(str(plan_sha256)):
        raise EmailTemplateError("Plan digest is invalid for replay locking.")
    return PLAN_DIR / ".commit-locks" / f"{plan_sha256}.json"


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise EmailTemplateError(
            "This plan has already entered commit and cannot be replayed."
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


# --------------------------------------------------------------------------
# Commands.
# --------------------------------------------------------------------------

def read_live_state(organization_id: str) -> tuple[
    list[dict[str, Any]], dict[str, Any], dict[str, Any]
]:
    rows = invoice_template_rows(ui_list_templates(organization_id))
    source_row = require_single_source_row(rows)
    detail = project_source_detail(
        ui_template_detail(organization_id, source_row["email_template_id"]),
        source_row["email_template_id"],
    )
    clone_fields = require_clean_source(detail)
    return rows, detail, clone_fields


@holds_zoho_browser("Zoho email templates: read-only template list and detail")
def command_list_templates(_: argparse.Namespace) -> None:
    vault = zoho_tool.load_vault()
    org_id = positive_id(vault.get("books_organization_id"), "books_organization_id")
    rows, detail, clone_fields = read_live_state(org_id)
    print(json.dumps({
        "status": "READ_ONLY",
        "method": "GET",
        "module": MODULE_NAME,
        "email_type": MODULE_EMAIL_TYPE,
        "invoice_templates": rows,
        "source_template_id": detail["email_template_id"],
        "source_subject": detail["subject"],
        "source_cc_mail_ids": detail["cc_mail_ids"],
        "source_bcc_mail_ids": detail["bcc_mail_ids"],
        "clone_fields_sha256": digest_for(clone_fields),
        "targets_present": sorted(
            name for name in TARGET_TEMPLATES
            if normalized_key(name) in {normalized_key(row["name"]) for row in rows}
        ),
        "zoho_writes": 0,
        "emails_sent": 0,
        "credentials_exposed": False,
    }, ensure_ascii=False, indent=2))


@holds_zoho_browser("Zoho email templates: read-only staging read of the live templates")
def command_stage(args: argparse.Namespace) -> None:
    action = args.action
    if action not in ACTIONS:
        raise EmailTemplateError("Unsupported email-template action.")
    # Local input validation deliberately precedes vault, session and network.
    confirmation: dict[str, Any] | None = None
    if action in ANDROID_CONFIRMED_ACTIONS:
        if not args.android_test_confirmation:
            raise EmailTemplateError(
                f"Action {action} requires --android-test-confirmation with "
                f"{CONFIRMED_BY}'s own confirmation that the {ACCOUNTING_TEMPLATE} "
                "template worked on his Android phone. Commissioning is not that "
                "confirmation."
            )
        confirmation = validate_android_confirmation(
            read_json_object(Path(args.android_test_confirmation), "Android-test confirmation")
        )
    elif args.android_test_confirmation:
        raise EmailTemplateError(
            f"Action {CREATE_ACCOUNTING_TEST} is the Android test itself and takes no "
            "Android-test confirmation."
        )

    names = ACTION_TARGETS[action]
    vault = zoho_tool.load_vault()
    org_id = positive_id(vault.get("books_organization_id"), "books_organization_id")
    org_name = str(vault.get("books_organization_name") or "")
    rows, detail, clone_fields = read_live_state(org_id)
    # ONLY this action's own targets are checked for absence. create_all_only
    # therefore neither reads nor requires anything about CC - Logistics or
    # CC - Operations, whether or not they exist.
    absent = require_targets_absent(rows, names)
    accounting = (
        require_accounting_template(rows, org_id, clone_fields)
        if action in ANDROID_CONFIRMED_ACTIONS
        else None
    )

    path = stage_plan(
        action,
        {
            "books_organization_id": org_id,
            "name": org_name,
            "fingerprint": digest_for({"books_organization_id": org_id, "name": org_name}),
        },
        {
            "email_template_id": detail["email_template_id"],
            "name": detail["name"],
            "clone_fields": clone_fields,
            "clone_fields_sha256": digest_for(clone_fields),
        },
        [
            {
                "name": name,
                "cc_mail_ids": list(TARGET_TEMPLATES[name]),
                "is_default": False,
                "customer_associated": False,
            }
            for name in names
        ],
        confirmation,
        {
            "invoice_templates": rows,
            "invoice_templates_sha256": digest_for(rows),
            "source_detail": detail,
            "source_detail_sha256": digest_for(detail),
            "targets_absent": absent,
            "accounting_template": accounting,
            "accounting_template_sha256": digest_for(accounting),
        },
        new_form_negative_evidence(clone_fields),
    )
    plan = read_json_object(path, "Staged plan")
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED",
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "action": action,
        "module": MODULE_NAME,
        "source_template": {
            "email_template_id": detail["email_template_id"],
            "name": detail["name"],
            "subject": detail["subject"],
            "cc_mail_ids": detail["cc_mail_ids"],
            "bcc_mail_ids": detail["bcc_mail_ids"],
            "is_default": detail["is_default"],
            "clone_fields_sha256": digest_for(clone_fields),
        },
        "targets": [
            {"name": name, "cc_mail_ids": list(TARGET_TEMPLATES[name]),
             "is_default": False, "customer_associated": False}
            for name in names
        ],
        "android_test_confirmation": confirmation,
        # Stated explicitly rather than left implicit in the target schema: every
        # target is created with an EMPTY Bcc, and each one costs exactly one
        # validated POST, which is why a multi-target plan is not atomic.
        "targets_bcc_mail_ids": [],
        "validated_posts": len(names),
        "atomic": False,
        "risks": list(RISKS),
        "zoho_writes": 0,
        "emails_sent": 0,
        "commit_executable": CREATE_WORKFLOW_COMMISSIONED,
        "commit_blocker": None if CREATE_WORKFLOW_COMMISSIONED else CREATE_NOT_COMMISSIONED,
        "create_mechanism": plan["workflow"]["create_mechanism"],
        "accepted_equivalence": ACCEPTED_EQUIVALENCE,
        "approval": APPROVAL_WORD,
    }, ensure_ascii=False, indent=2))


@holds_zoho_browser("Zoho email templates: clone Default into a fixed CC template")
def command_commit(args: argparse.Namespace) -> None:
    # Byte-exact approval is checked before any plan read, browser or network.
    require_rachad_approval(args.approval)
    plan_path = contained_plan(args.plan)
    vault = zoho_tool.load_vault()
    plan = load_plan(plan_path, args.action, vault)
    org_id = plan["organization"]["books_organization_id"]

    # Fresh live re-read and full precondition re-verification.
    rows, detail, clone_fields = read_live_state(org_id)
    verify_source_unchanged(detail, plan["live_evidence"]["source_detail"])
    if digest_for(clone_fields) != plan["source"]["clone_fields_sha256"]:
        raise EmailTemplateError(
            "The source Default clone fields changed after review. A new plan is required."
        )
    names = tuple(target["name"] for target in plan["targets"])
    require_targets_absent(rows, names)
    if args.action in ANDROID_CONFIRMED_ACTIONS:
        require_accounting_template(rows, org_id, clone_fields)

    # Fails closed HERE: before the replay lock and before anything that could
    # cause a side effect, so an honest refusal never burns Rachad's plan. The
    # browser lane is already held by the decorator, so a busy lane refused
    # earlier still and cost nothing.
    fidelity = require_create_contract_commissioned(clone_fields)
    # The row that will be cloned is identified by the FRESH live read, and the
    # plan has to agree with it. Nothing downstream trusts the plan's own ID.
    source_id = detail["email_template_id"]
    if source_id != plan["source"]["email_template_id"]:
        raise EmailTemplateError(
            f"The live {SOURCE_TEMPLATE_NAME} template is now {source_id}, not the "
            f"{plan['source']['email_template_id']} this plan was staged against."
        )

    lock = lock_path(plan["sha256"])
    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": args.action,
        "status": "in_flight",
        "created_template_ids": {},
        "started_utc": utc_now().isoformat(),
    }, exclusive=True)

    created: dict[str, str] = {}
    verified: dict[str, Any] = {}
    in_flight = ""
    try:
        for target in plan["targets"]:
            name, cc = require_fixed_target(
                target["name"], target["cc_mail_ids"], "Commit target"
            )
            # Absence is re-checked immediately before each individual creation.
            require_targets_absent(
                invoice_template_rows(ui_list_templates(org_id)), (name,)
            )
            in_flight = name
            template_id = create_template_via_ui(
                org_id, source_id, name, cc, clone_fields
            )
            created[name] = template_id
            verified[name] = verify_created_template(
                project_source_detail(
                    ui_template_detail(org_id, template_id), template_id
                ),
                name, cc, clone_fields,
            )
            in_flight = ""
        # Default itself must be untouched by all of this.
        _, final_detail, _ = read_live_state(org_id)
        verify_source_unchanged(final_detail, plan["live_evidence"]["source_detail"])
    except Exception as exc:
        status = "partial" if created else ("indeterminate" if in_flight else "aborted_before_write")
        write_lock(lock, {
            "plan_sha256": plan["sha256"],
            "action": args.action,
            "status": status,
            "created_template_ids": created,
            "write_in_flight_template": in_flight,
            "not_attempted": [
                name for name in names if name not in created and name != in_flight
            ],
            "updated_utc": utc_now().isoformat(),
            "reason": str(exc)[:2000],
            "no_retry": True,
            "email_sent": False,
        })
        zoho_tool.append_receipt(
            "zoho_email_template_create_partial_indeterminate_or_aborted_no_retry",
            f"status={status}; created={','.join(created) or 'none'}; "
            f"in_flight={in_flight or 'none'}; plan={plan_path}; "
            f"sha256={plan['sha256']}; emails_sent=0",
        )
        raise EmailTemplateError(
            f"Email-template creation is {status} and the whole plan is permanently "
            f"locked against retry. This was never atomic. Live-verified: "
            f"{sorted(verified) or 'none'}. In flight when it stopped: "
            f"{in_flight or 'none'}. Not attempted: "
            f"{[name for name in names if name not in created and name != in_flight]}. "
            f"Reconcile live Zoho state before staging anything new. Reason: {exc}"
        ) from exc

    exposure: dict[str, Any] = {"status": "not_requested"}
    if args.verification_invoice_id:
        try:
            access_token, vault = zoho_tool.refresh_access_token(vault)
            exposure = {
                name: verify_template_exposed(
                    access_token, vault, args.verification_invoice_id,
                    template_id, TARGET_TEMPLATES[name],
                )
                for name, template_id in created.items()
            }
        except Exception as exc:  # verification only; creation already succeeded
            exposure = {"status": "not_verified", "reason": str(exc)[:500]}

    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": args.action,
        "status": "committed_verified",
        "created_template_ids": created,
        "exposure": exposure,
        "clone_fidelity": fidelity,
        "updated_utc": utc_now().isoformat(),
        "no_retry": True,
        "email_sent": False,
    })
    zoho_tool.append_receipt(
        "zoho_email_template_created_committed_verified",
        f"action={args.action}; created={json.dumps(created)}; plan={plan_path}; "
        f"sha256={plan['sha256']}; emails_sent=0",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "action": args.action,
        "plan_sha256": plan["sha256"],
        "created": created,
        "verified": sorted(verified),
        "exposure": exposure,
        "clone_fidelity": fidelity,
        "source_default_unchanged": True,
        "atomic": False,
        "replay_locked": True,
        "emails_sent": 0,
    }, ensure_ascii=False, indent=2))


DELETE_PLAN_FIELDS = frozenset({
    "tool", "tool_version", "schema_version", "action", "created_utc", "expires_utc",
    "nonce", "origin", "organization", "target", "live_evidence", "risk",
    "approval_required", "sha256",
})


def validate_delete_plan(plan: dict[str, Any]) -> None:
    closed_fields(plan, DELETE_PLAN_FIELDS, "delete plan")
    if plan["tool"] != TOOL_NAME or plan["tool_version"] != TOOL_VERSION:
        raise EmailTemplateError(
            "This plan was staged by a different build of the tool and cannot be committed."
        )
    if plan["schema_version"] != SCHEMA_VERSION:
        raise EmailTemplateError("This plan uses a superseded schema and cannot be committed.")
    if plan["action"] != DELETE_ACCIDENTAL_ACCOUNTING:
        raise EmailTemplateError("This plan is not the commissioned delete action.")
    if plan["approval_required"] != APPROVAL_WORD:
        raise EmailTemplateError("This plan does not carry the required approval word.")
    require_origin(plan["origin"])
    target = plan["target"]
    closed_fields(target, frozenset({"email_template_id", "name"}), "delete plan target")
    if target["email_template_id"] != DELETE_TARGET_TEMPLATE_ID:
        raise EmailTemplateError("This plan targets a template this tool cannot delete.")
    if normalized_key(target["name"]) != normalized_key(DELETE_TARGET_NAME):
        raise EmailTemplateError("This plan's target name is not the commissioned one.")
    if parse_time(plan["expires_utc"], "expires_utc") <= utc_now():
        raise EmailTemplateError("This plan has expired. Stage a fresh one.")
    body = {key: value for key, value in plan.items() if key != "sha256"}
    if digest_for(body) != plan["sha256"]:
        raise EmailTemplateError("This plan's digest does not match its contents.")


def load_delete_plan(path: Path, vault: dict[str, Any]) -> dict[str, Any]:
    plan = read_json_object(path, "delete plan")
    validate_delete_plan(plan)
    validate_organization(plan["organization"], vault)
    return plan


@holds_zoho_browser("Zoho email templates: read-only staging read for the commissioned delete")
def command_stage_delete(_: argparse.Namespace) -> None:
    """GET-only. Nothing here can change anything in Zoho."""
    vault = zoho_tool.load_vault()
    org_id = positive_id(vault.get("books_organization_id"), "books_organization_id")
    org_name = str(vault.get("books_organization_name") or "")
    rows, detail, _clone_fields = read_live_state(org_id)
    target = require_delete_target(rows, detail["email_template_id"])
    created = utc_now()
    body = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "action": DELETE_ACCIDENTAL_ACCOUNTING,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "origin": origin_record(),
        "organization": {
            "books_organization_id": org_id,
            "name": org_name,
            "fingerprint": digest_for({"books_organization_id": org_id, "name": org_name}),
        },
        "target": {
            "email_template_id": target["email_template_id"],
            "name": target["name"],
        },
        "live_evidence": {
            "invoice_templates": json_copy(rows),
            "source_template_id": detail["email_template_id"],
            "target_row": target,
        },
        "risk": (
            "ONE permanent deletion of ONE fixed email template, attempted exactly "
            "once. It is irreversible: Zoho has no undo and this tool has no create "
            "route for it outside its own commissioned clone flow. Any failure, "
            "timeout or indeterminate result locks this plan permanently."
        ),
        "approval_required": APPROVAL_WORD,
    }
    plan = {**body, "sha256": digest_for(body)}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_delete_{plan['sha256'][:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED",
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "action": DELETE_ACCIDENTAL_ACCOUNTING,
        "will_delete": {"email_template_id": target["email_template_id"], "name": target["name"]},
        "will_survive": [
            row["email_template_id"] for row in rows
            if row["email_template_id"] != DELETE_TARGET_TEMPLATE_ID
        ],
        "irreversible": True,
        "contract_captured": DELETE_CONTRACT_CAPTURED,
        "blocked_until_captured": not DELETE_CONTRACT_CAPTURED,
        "risk": plan["risk"],
        "zoho_writes": 0,
        "emails_sent": 0,
    }, ensure_ascii=False, indent=2))


@holds_zoho_browser("Zoho email templates: commissioned delete of one fixed template")
def command_commit_delete(args: argparse.Namespace) -> None:
    # Byte-exact approval is checked before any plan read, browser or network.
    require_rachad_approval(args.approval)
    plan_path = contained_plan(args.plan)
    vault = zoho_tool.load_vault()
    plan = load_delete_plan(plan_path, vault)
    org_id = plan["organization"]["books_organization_id"]

    # Fresh live re-read: the plan's own record of the world is never trusted.
    rows, detail, _clone_fields = read_live_state(org_id)
    target = require_delete_target(rows, detail["email_template_id"])
    if target != plan["live_evidence"]["target_row"]:
        raise EmailTemplateError(
            "The target template changed after review. Stage a fresh plan."
        )
    if json_copy(rows) != plan["live_evidence"]["invoice_templates"]:
        raise EmailTemplateError(
            "The live invoice-template list changed after review. Stage a fresh plan."
        )
    # Fails closed HERE: before the replay lock and before anything that could
    # cause a side effect, so an honest refusal never burns Rachad's plan and the
    # SAME plan can be committed once the contract is pinned.
    require_delete_contract_commissioned()

    # The lock goes in LAST, immediately before the one irreversible action, so
    # every refusal above cost nothing and left the plan committable.
    lock = lock_path(plan["sha256"])
    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": DELETE_ACCIDENTAL_ACCOUNTING,
        "status": "in_flight",
        "target_template_id": DELETE_TARGET_TEMPLATE_ID,
        "started_utc": utc_now().isoformat(),
    }, exclusive=True)

    try:
        released = delete_template_via_ui(org_id)
        rows_after = invoice_template_rows(ui_list_templates(org_id))
        verified = verify_template_deleted(rows_after, plan["live_evidence"]["invoice_templates"])
        # Default itself must be untouched by all of this.
        _, final_detail, _ = read_live_state(org_id)
        if final_detail["email_template_id"] != plan["live_evidence"]["source_template_id"]:
            raise EmailTemplateError("The source Default template changed during the delete.")
    except Exception as exc:
        write_lock(lock, {
            "plan_sha256": plan["sha256"],
            "action": DELETE_ACCIDENTAL_ACCOUNTING,
            "status": "indeterminate",
            "target_template_id": DELETE_TARGET_TEMPLATE_ID,
            "reason": str(exc)[:2000],
            "updated_utc": utc_now().isoformat(),
            "no_retry": True,
        })
        zoho_tool.append_receipt(
            "zoho_email_template_delete_failed_permanently_locked",
            f"plan={plan_path}; sha256={plan['sha256']}; target={DELETE_TARGET_TEMPLATE_ID}",
        )
        raise EmailTemplateError(
            "Email-template delete is indeterminate and permanently locked against "
            "replay. Reconcile live Zoho state before staging another plan: " + str(exc)
        ) from exc

    write_lock(lock, {
        "plan_sha256": plan["sha256"],
        "action": DELETE_ACCIDENTAL_ACCOUNTING,
        "status": "committed_verified",
        "deleted_template_id": DELETE_TARGET_TEMPLATE_ID,
        "updated_utc": utc_now().isoformat(),
        "no_retry": True,
    })
    zoho_tool.append_receipt(
        "zoho_email_template_deleted_verified",
        f"template={DELETE_TARGET_TEMPLATE_ID} ({DELETE_TARGET_NAME}); plan={plan_path}",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED",
        "action": DELETE_ACCIDENTAL_ACCOUNTING,
        "plan_sha256": plan["sha256"],
        "released_request": released,
        "deleted": verified,
        "replay_locked": True,
        "emails_sent": 0,
    }, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list-templates")
    listing.set_defaults(func=command_list_templates)
    stage = commands.add_parser("stage")
    stage.add_argument("--action", required=True, choices=list(ACTIONS))
    stage.add_argument("--android-test-confirmation", default="")
    stage.set_defaults(func=command_stage)
    commit = commands.add_parser("commit")
    commit.add_argument("--action", required=True, choices=list(ACTIONS))
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.add_argument("--verification-invoice-id", default="")
    commit.set_defaults(func=command_commit)
    stage_delete = commands.add_parser("stage-delete")
    stage_delete.set_defaults(func=command_stage_delete)
    commit_delete = commands.add_parser("commit-delete")
    commit_delete.add_argument("--plan", required=True)
    commit_delete.add_argument("--approval", required=True)
    commit_delete.set_defaults(func=command_commit_delete)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (EmailTemplateError, zoho_tool.ZohoError, OSError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
