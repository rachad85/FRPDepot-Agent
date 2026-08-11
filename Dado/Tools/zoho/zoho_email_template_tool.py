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
selectable and its CC populates. The remaining three require his own direct
Android-test confirmation, recorded in the plan. Commissioning is not approval
and is not Android-test confirmation.

Everything else is permanently unreachable. There is no generic browser action,
no generic HTTP write helper, and no caller-supplied selector, URL, module,
name, address or source template. Template update, delete, rename, set-default,
clone-to-another-module, customer/vendor association, attachment change and PDF
templates are not implemented. No email, reminder, notification, SMTP, Graph or
Zoho transaction-email route exists anywhere in this module: the only network
verbs it can issue are same-origin browser GETs on two exact Books settings
paths and read-only OAuth GETs through ``zoho_tool.api_get``.

LIVE CREATE IS STILL NOT EXECUTABLE, AND THE REASON CHANGED ON 2026-08-10.

Zoho Books publishes no documented API for creating an email template, so the
only safe mechanism is Zoho's own native Save path, exactly as
``zoho_inventory_classification_tool`` does for the item custom field. That
contract has now BEEN CAPTURED under an abort-everything interceptor: Rachad
authorized opening the fixed ``New`` invoice-template form, filling only the
fixed name and the fixed Cc option, and clicking Save with every non-read
request blocked before the network. The captured request is one POST to
``/api/v3/settings/emailtemplates`` on the exact Books host, body pinned by
SHA-256. See ``NATIVE_SAVE_*`` and ``Dado\\20_Working\\
zoho_email_template_capture\\native_save_request.json``.

THE NEW BLOCKER IS NOT THE CONTRACT -- IT IS THE CONTENT. Decoding that captured
body proved the ``New`` form does NOT clone this organization's live ``Default``
template. It carries Zoho's stock factory invoice body, which differs from FRP
Depot's customized ``Default``: stock says BALANCE DUE / %Balance% / MAKE
PAYMENT / Regards %UserName% %CompanyName%, while the live ``Default`` says
INVOICE AMOUNT / %Total% / PAY NOW / Regards Accounting Departement. The subject
and From do match. Rachad's commission requires every target to preserve
``Default``'s current body, so creating through ``New`` would either break that
guarantee or -- worse -- succeed at the POST and then fail this tool's own
clone-fidelity read-back, leaving an orphan template behind a permanently locked
plan.

A native ``Clone`` control DOES exist on the ``Default`` row (verified read-only
on 2026-08-10; the row menu holds exactly ``Edit`` and ``Clone``), and that is
the mechanism the official Zoho documentation describes. Its Save contract has
not been captured. So ``commit`` still validates everything and then refuses,
before any lock and before any side effect, rather than creating a template that
would not be a clone. See ``CREATE_WORKFLOW_COMMISSIONED`` and ``CREATE_BLOCKER``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import zoho_tool

TOOL_NAME = "FRP Depot Zoho Books Invoice Email Template Tool"
TOOL_VERSION = "1.1.0"
# Bumped with the tool version so every plan staged under the old, now-obsolete
# create blocker fails closed instead of being commit-able against new rules.
SCHEMA_VERSION = 2
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
# The captured native Save contract. Every value here was read off the blocked
# capture artifact, never guessed. NATIVE_SAVE_METHOD is data describing what
# Zoho's own page emits -- this module issues no such request anywhere.
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
CREATE_ACCOUNTING_TEST = "create_accounting_test"
CREATE_REMAINING = "create_remaining_templates"
ACTION_TARGETS: dict[str, tuple[str, ...]] = {
    CREATE_ACCOUNTING_TEST: (ACCOUNTING_TEMPLATE,),
    CREATE_REMAINING: ("CC - Logistics", "CC - Operations", "CC - All"),
}
ACTIONS = tuple(ACTION_TARGETS)

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
    "language_content", "placeholder", "subject", "type",
)
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

# Zoho's native email-template Save contract HAS been captured, the way the item
# custom field's was: the fixed New form opened under an abort-everything route
# interceptor and its single Save request recorded without reaching the network.
NATIVE_SAVE_CONTRACT_CAPTURED = True

# *** THE ONE OPEN ITEM. ***
# False because the captured workflow is not a clone: the New form emits Zoho's
# stock factory body, not this organization's customized Default body. See
# CREATE_BLOCKER. Flipping this flag alone does nothing -- create_template_via_ui
# has no implementation to enable, and require_native_form_clones_source still
# checks the live Default against what the form actually emitted.
CREATE_WORKFLOW_COMMISSIONED = False

NON_ATOMIC_RISK = (
    "NOT ATOMIC: each template is saved by its own independent UI Save. A "
    "failure after one save leaves a partial set. The plan is locked on first "
    "failure and never retried."
)
CLONE_FIDELITY_RISK = (
    "BLOCKING: Zoho's New invoice-template form emits its own stock factory body, "
    "not this organization's customized Default body, so creating through it "
    "would not produce the clone this commission requires. The native Clone "
    "control on the Default row is the untested alternative. Nothing is created "
    "until Rachad decides."
)
RISKS = (
    NON_ATOMIC_RISK,
    CLONE_FIDELITY_RISK,
    "Zoho publishes no documented Books API for creating an email template, so "
    "the write surface is the authenticated UI only.",
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


def _execute_ui_get(url: str) -> dict[str, Any]:
    """Execute one same-origin GET inside the authenticated local Books page.

    Only status, success and response text cross the CDP boundary. Cookies,
    local storage, request headers and credentials are never read, printed or
    copied. The URL is re-validated here even though the caller built it.
    """
    url = ui_url_allowed(url)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise EmailTemplateError(
            "No authenticated live Zoho Books page is available. Run CONNECT_DADO_ZOHO_UI.bat."
        ) from exc
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=10_000)
            page = _authenticated_books_page(browser)
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
                url,
            )
    except EmailTemplateError:
        raise
    except Exception as exc:
        raise EmailTemplateError(
            "No authenticated live Zoho Books page is available. Run CONNECT_DADO_ZOHO_UI.bat."
        ) from exc


def ui_transport_allowed(
    method: str,
    path: str,
    organization_id: str,
    *,
    email_type: str | None = None,
) -> dict[str, Any]:
    """The complete UI transport. Every non-GET verb is refused here."""
    if method != "GET":
        raise EmailTemplateError(
            "REFUSED: this tool has no UI write transport. Only the exact "
            "email-template list and detail GETs are implemented."
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
    return _decode_ui_result(_execute_ui_get(ui_url_allowed(url)))


def ui_list_templates(organization_id: str) -> dict[str, Any]:
    return ui_transport_allowed("GET", TEMPLATE_LIST_PATH, organization_id)


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
    for key in SOURCE_CLONE_FIELDS:
        if key == "bcc_mail_ids":
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
    """The second action is refused unless the phone-tested template still matches."""
    matches = [
        row for row in rows
        if normalized_key(row["name"]) == normalized_key(ACCOUNTING_TEMPLATE)
    ]
    if len(matches) != 1 or matches[0]["name"] != ACCOUNTING_TEMPLATE:
        raise EmailTemplateError(
            f"Exactly one invoice template named {ACCOUNTING_TEMPLATE!r} must already "
            "exist before the remaining templates may be staged."
        )
    template_id = matches[0]["email_template_id"]
    detail = project_source_detail(
        ui_template_detail(organization_id, template_id), template_id
    )
    return verify_created_template(
        detail, ACCOUNTING_TEMPLATE, TARGET_TEMPLATES[ACCOUNTING_TEMPLATE], clone_fields
    )


# --------------------------------------------------------------------------
# The captured native Save contract, decoded and closed.
#
# These functions read and check a request body. They never issue one: this
# module still has exactly one network call site, the same-origin GET above.
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
# The create seam. The contract is captured; the workflow is still refused.
# --------------------------------------------------------------------------

CREATE_BLOCKER = (
    "NOT COMMISSIONED: the captured native create workflow would not produce a "
    "clone of this organization's Default invoice template, so this tool refuses "
    "to run it. The contract itself IS captured and pinned (one POST to "
    f"{UI_SCHEME}://{UI_HOST}{NATIVE_SAVE_PATH}, body SHA-256 "
    f"{NATIVE_SAVE_BODY_SHA256}). Decoding it proved that Zoho's New form loads "
    "its own stock factory invoice body, NOT the live Default body: stock reads "
    "BALANCE DUE / %Balance% / MAKE PAYMENT / Regards %UserName% %CompanyName%, "
    "while the live Default reads INVOICE AMOUNT / %Total% / PAY NOW / Regards "
    "Accounting Departement. Subject and From do match. Rachad's commission "
    "requires every target to preserve Default's current body, so creating "
    "through New would either break that guarantee or succeed at the POST and "
    "then fail this tool's own clone-fidelity read-back, leaving an orphan "
    "template behind a permanently locked plan. No workflow or selector was "
    "invented, nothing was created and no email was sent. Next step, if Rachad "
    "wants it: a native Clone control DOES exist on the Default row (its menu "
    "holds exactly Edit and Clone, verified read-only), so authorize a blocked "
    "capture of the Clone form's single Save request the same way the New form's "
    "was captured -- or tell Dado plainly that the stock body is acceptable, "
    "which is his call to make, not hers."
)


def require_native_form_clones_source(clone_fields: dict[str, Any]) -> None:
    """Refuse unless Zoho's New form would reproduce the live Default exactly.

    The pin is the content the New form actually emitted during the blocked
    capture. If the live Default ever matches it, creation through New becomes a
    faithful clone and this gate stops refusing; today it does not match.
    """
    subject_digest = hashlib.sha256(
        str(clone_fields["subject"]).encode("utf-8")
    ).hexdigest()
    body_digest = hashlib.sha256(str(clone_fields["body"]).encode("utf-8")).hexdigest()
    if not secrets.compare_digest(subject_digest, NATIVE_FORM_SUBJECT_SHA256):
        raise EmailTemplateError(
            "REFUSED: the live Default subject no longer matches the subject "
            "Zoho's New form emitted during the captured Save. " + CREATE_BLOCKER
        )
    if not secrets.compare_digest(body_digest, NATIVE_FORM_BODY_SHA256):
        raise EmailTemplateError(CREATE_BLOCKER)


def require_create_contract_commissioned(clone_fields: dict[str, Any]) -> None:
    """Fail closed BEFORE the replay lock and before any possible side effect."""
    if not CREATE_WORKFLOW_COMMISSIONED:
        raise EmailTemplateError(CREATE_BLOCKER)
    require_native_form_clones_source(clone_fields)


def create_template_via_ui(
    organization_id: str,
    name: str,
    cc_mail_ids: tuple[str, ...],
    clone_fields: dict[str, Any],
) -> str:
    """Create exactly one fixed template and return its new ID. ONE attempt.

    There is no implementation and no retry loop. The request contract is known,
    but the New form it belongs to does not clone Default, so calling this is an
    error rather than a guess.
    """
    require_create_contract_commissioned(clone_fields)
    raise EmailTemplateError(CREATE_BLOCKER)


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
                f"Zoho native UI Save, captured and pinned: {NATIVE_SAVE_METHOD} "
                f"{UI_SCHEME}://{UI_HOST}{NATIVE_SAVE_PATH}"
            ),
            "native_save_contract_captured": NATIVE_SAVE_CONTRACT_CAPTURED,
            "native_save_body_sha256": NATIVE_SAVE_BODY_SHA256,
            "create_workflow_commissioned": CREATE_WORKFLOW_COMMISSIONED,
            "create_blocker": None if CREATE_WORKFLOW_COMMISSIONED else CREATE_BLOCKER,
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
    if action == CREATE_ACCOUNTING_TEST:
        if confirmation is not None:
            raise EmailTemplateError(
                "The first Android-test plan must not carry an Android-test confirmation."
            )
    else:
        if not isinstance(confirmation, dict):
            raise EmailTemplateError(
                f"Action {CREATE_REMAINING} requires Rachad's own recorded "
                "Android-test confirmation."
            )
        if validate_android_confirmation(confirmation) != confirmation:
            raise EmailTemplateError("Plan Android-test confirmation is not canonical.")

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
    if action == CREATE_ACCOUNTING_TEST and live["accounting_template"] is not None:
        raise EmailTemplateError(
            f"The first plan must be staged while {ACCOUNTING_TEMPLATE} does not exist."
        )
    if action == CREATE_REMAINING and not isinstance(live["accounting_template"], dict):
        raise EmailTemplateError(
            f"Action {CREATE_REMAINING} requires verified live {ACCOUNTING_TEMPLATE} evidence."
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


def command_stage(args: argparse.Namespace) -> None:
    action = args.action
    if action not in ACTIONS:
        raise EmailTemplateError("Unsupported email-template action.")
    # Local input validation deliberately precedes vault, session and network.
    confirmation: dict[str, Any] | None = None
    if action == CREATE_REMAINING:
        if not args.android_test_confirmation:
            raise EmailTemplateError(
                f"Action {CREATE_REMAINING} requires --android-test-confirmation with "
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
    absent = require_targets_absent(rows, names)
    accounting = (
        require_accounting_template(rows, org_id, clone_fields)
        if action == CREATE_REMAINING
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
        "atomic": False,
        "risks": list(RISKS),
        "zoho_writes": 0,
        "emails_sent": 0,
        "commit_executable": CREATE_WORKFLOW_COMMISSIONED,
        "commit_blocker": None if CREATE_WORKFLOW_COMMISSIONED else CREATE_BLOCKER,
        "approval": APPROVAL_WORD,
    }, ensure_ascii=False, indent=2))


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
    if args.action == CREATE_REMAINING:
        require_accounting_template(rows, org_id, clone_fields)

    # Fails closed HERE: before the replay lock and before anything that could
    # cause a side effect, so an honest refusal never burns Rachad's plan.
    require_create_contract_commissioned(clone_fields)

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
            template_id = create_template_via_ui(org_id, name, cc, clone_fields)
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
        "source_default_unchanged": True,
        "atomic": False,
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
