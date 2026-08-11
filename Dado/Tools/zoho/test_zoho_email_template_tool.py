"""Independent safety tests for the Zoho Books invoice email-template tool.

These tests never touch the live Zoho vault, the live UI session or the
network. Every transport is patched to fail loudly if it is reached, so a test
that "passes" because it silently talked to Zoho is impossible.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import timedelta
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

import zoho_email_template_tool as emailtool

# ---------------------------------------------------------------------------
# The commit command now takes the SHARED Zoho browser mutex. Without this
# redirect a test run would contend with the real live browser lane: the suite
# would take the live lock dozens of times, could block a genuine write, and a
# run killed mid-test could leave a descriptor behind. Redirect once per module
# so the suite stays hermetic.
# ---------------------------------------------------------------------------
_LANE_LOCK_TMP = None
_LANE_LOCK_ORIGINAL_DIR = None
_NO_REAL_BROWSER = None


def _forbidden_playwright(*args, **kwargs):
    """*** THIS GUARD EXISTS BECAUSE ITS ABSENCE COST A REAL ZOHO WRITE. ***

    On 2026-08-11 a mutation check deleted the ``@holds_zoho_browser`` decorator
    to prove it was load-bearing. It was -- but with it gone,
    ``test_busy_browser_refuses_for_free_and_leaves_the_plan_reusable`` (which
    calls ``command_commit`` directly, deliberately WITHOUT patching
    ``create_template_via_ui``) had nothing left between it and the live
    session. The suite's fake vault carries the REAL organization id and the
    real Default template id, so it drove the real browser and saved a real
    ``CC - Accounting`` template into FRP Depot's Zoho Books.

    Patching the transport (``_execute_ui_get``) was never enough, because the
    create path opens its OWN playwright session. The browser itself is now
    what is patched, module-wide: any test that reaches a real session fails
    loudly instead of writing to a live business system. Tests that need a
    browser install their own recording fake over this.
    """
    raise AssertionError(
        "A test tried to open a REAL browser session. Tests must never reach the "
        "live Zoho UI: patch create_template_via_ui, or install the recording "
        "FakePlaywright."
    )


def setUpModule():
    global _LANE_LOCK_TMP, _LANE_LOCK_ORIGINAL_DIR, _NO_REAL_BROWSER
    import playwright.sync_api as pwapi
    import ui_lane_lock

    _LANE_LOCK_TMP = tempfile.TemporaryDirectory()
    _LANE_LOCK_ORIGINAL_DIR = ui_lane_lock.LOCK_DIR
    ui_lane_lock.LOCK_DIR = Path(_LANE_LOCK_TMP.name)
    _NO_REAL_BROWSER = mock.patch.object(
        pwapi, "sync_playwright", _forbidden_playwright)
    _NO_REAL_BROWSER.start()


def tearDownModule():
    import ui_lane_lock

    if _NO_REAL_BROWSER is not None:
        _NO_REAL_BROWSER.stop()
    if _LANE_LOCK_ORIGINAL_DIR is not None:
        ui_lane_lock.LOCK_DIR = _LANE_LOCK_ORIGINAL_DIR
    if _LANE_LOCK_TMP is not None:
        _LANE_LOCK_TMP.cleanup()


DEFAULT_ID = "96274000000000014"

# *** THE FAKE VAULT MUST NOT NAME THE LIVE ORGANIZATION. ***
# The 2026-08-11 accidental write happened partly because this suite's fake
# vault carried the REAL Zoho Books organization id, so a test that escaped to
# the live browser addressed real FRP Depot data and succeeded. It now carries
# an id that exists nowhere in Zoho: an escape would be refused by Zoho itself,
# on top of the module-scope browser patch that should stop it first.
# LIVE_CAPTURE_ORG is the real id, and it appears ONLY where the pinned capture
# artifact's own recorded contents are being asserted -- never as a vault value,
# never as a value this suite sends anywhere.
FAKE_ORG = "770000000001"
LIVE_CAPTURE_ORG = "110002157575"

# The live Default's real content, taken from the pinned blocked Clone capture
# rather than invented, so the canonical comparator and the clone-fidelity gate
# are exercised against the actual FRP Depot invoice HTML.
_CAPTURED = emailtool.captured_clone_payload()
REAL_BODY = _CAPTURED["body"]
REAL_SUBJECT = _CAPTURED["subject"]
REAL_FROM = _CAPTURED["from_address_id"]


def default_detail(**changes) -> dict:
    value = {
        "bcc_mail_ids": [],
        "body": REAL_BODY,
        "bodyv2": "",
        "cc_mail_ids": [],
        "cc_me": False,
        "documents": [],
        "email_template_id": DEFAULT_ID,
        "from_address_id": REAL_FROM,
        "is_default": True,
        "language_content": [],
        "name": "Default",
        "placeholder": "mt_default",
        "subject": REAL_SUBJECT,
        "type": "invoice_notification",
    }
    value.update(changes)
    return value


class FakeRequest:
    """The only three request attributes the interceptor may ever read."""

    def __init__(self, method: str, url: str, post_data=None):
        self.method = method
        self.url = url
        self.post_data = post_data

    def __getattr__(self, item):  # pragma: no cover - defensive
        raise AssertionError(f"the interceptor must not read request.{item}")


class FakeRoute:
    def __init__(self, state: dict | None = None):
        self.actions: list = []
        self.state = state if state is not None else {}
        self.validated_when_continued: list = []

    def continue_(self):
        self.actions.append("continue")
        self.validated_when_continued.append(
            (bool(self.state.get("allowed")), "payload" in self.state)
        )

    def abort(self, reason=None):
        self.actions.append(("abort", reason))


# ---------------------------------------------------------------------------
# A fake authenticated Zoho page, faithful enough to prove the exact click
# sequence, the one-attempt rule and the read-back -- without a browser, a
# session or a single live request. It records every interaction, so a test can
# assert that New and Edit are never touched.
# ---------------------------------------------------------------------------
CLONE_FORM_URL = (
    "https://books.zohocloud.ca/app#/settings/emails/templates/edit"
    f"?clone_email_template_id={DEFAULT_ID}"
)


class FakeLocator:
    def __init__(self, page, label: str, present: bool = True):
        self.page = page
        self.label = label
        self.present = present

    def filter(self, has_text=None, has=None):
        suffix = ""
        if has_text is not None:
            suffix += f"|text:{getattr(has_text, 'pattern', has_text)}"
        if has is not None:
            suffix += f"|has:{has.label}"
        return FakeLocator(self.page, self.label + suffix, self.present)

    def locator(self, selector: str):
        return FakeLocator(self.page, f"{self.label}>{selector}", self.present)

    def get_by_role(self, role, name=None, exact=None):
        # Row-scoped lookups: the real page carries one disclosure button PER
        # TEMPLATE, so every row control is resolved inside its own row.
        return FakeLocator(self.page, f"{self.label}>role:{role}:{name}", self.present)

    def inner_text(self):
        marker = "|text:"
        if marker in self.label:
            return self.label.split(marker, 1)[1].replace("\\", "")
        return self.label

    def count(self):
        return 1 if self.page.exists(self.label) else 0

    def nth(self, index):
        return self

    def is_visible(self):
        return True

    def click(self, timeout=None):
        self.page.record_click(self.label)

    def fill(self, value):
        self.page.fills.append((self.label, value))
        self.page.form["name"] = value


class FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self.url = f"https://{emailtool.UI_HOST}{emailtool.CLONE_SAVE_PATH}"
        self._payload = payload
        self.request = FakeRequest("POST", self.url)

    def text(self):
        return self._payload if isinstance(self._payload, str) else json.dumps(self._payload)


class FakeExpect:
    def __init__(self, page):
        self.page = page
        self.value = None

    def __enter__(self):
        self.page.last_response = None
        return self

    def __exit__(self, *exc):
        if exc[0] is not None:
            return False
        if self.page.last_response is None:
            raise emailtool_playwright_timeout("no matching response")
        self.value = self.page.last_response
        return False


def emailtool_playwright_timeout(message: str):
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    return PlaywrightTimeoutError(message)


class FakePage:
    """Drives the commissioned flow and records every interaction."""

    def __init__(self, test, name, cc_option_texts, *, save_payload=None,
                 save_status=200, save_answer=None, emit_extra_save=False):
        self.test = test
        self.url = "https://books.zohocloud.ca/app#/settings/emails/templates"
        self.clicks: list[str] = []
        self.fills: list[tuple] = []
        self.evaluations: list[str] = []
        self.routes: list = []
        self.unrouted = False
        self.navigations: list[str] = []
        self.last_response = None
        self.save_attempts = 0
        self.target_name = name
        self.cc_option_texts = list(cc_option_texts)
        self.save_payload = save_payload
        self.save_status = save_status
        self.save_answer = save_answer if save_answer is not None else {"code": 0}
        self.emit_extra_save = emit_extra_save
        self.form = {
            "url": self.url, "name": "Default",
            "from_text": "From\nThis email address will be used...",
            "cc_text": "Cc", "cc_selected": [], "cc_input_value": "",
            "bcc_text": "Bcc", "bcc_selected": [], "bcc_input_value": "",
            "subject_text": "Subject\nInvoice - InvoiceNumber from Name",
            "is_default": False,
        }

    # -- locator plumbing ------------------------------------------------
    def exists(self, label: str) -> bool:
        if label.startswith("role:option:"):
            return label.split(":", 2)[2] in self.cc_option_texts
        return True

    def get_by_role(self, role, name=None, exact=None):
        return FakeLocator(self, f"role:{role}:{name}")

    def locator(self, selector):
        return FakeLocator(self, f"sel:{selector}")

    def record_click(self, label: str) -> None:
        self.clicks.append(label)
        if label.endswith(f"role:button:{emailtool.ROW_DISCLOSURE_LABEL}"):
            return
        if label.endswith(f"|text:^{emailtool.CLONE_MENU_ITEM}$"):
            self.url = CLONE_FORM_URL
            self.form["url"] = self.url
            return
        if label.startswith("role:option:"):
            self.form["cc_selected"] = list(self.form["cc_selected"]) + [
                label.split(":", 2)[2]]
            return
        if label == f"role:button:{emailtool.SAVE_BUTTON_LABEL}":
            self._save()
            return

    def _save(self) -> None:
        self.save_attempts += 1
        body = self.save_payload if self.save_payload is not None else urlencode({
            "JSONString": json.dumps(emailtool.expected_clone_payload(
                self.target_name, emailtool.TARGET_TEMPLATES[self.target_name],
                emailtool.require_clean_source(self.test.source))),
            "organization_id": FAKE_ORG,
        })
        attempts = 2 if self.emit_extra_save else 1
        for _ in range(attempts):
            route = FakeRoute(self.routes[-1][1] if self.routes else None)
            request = FakeRequest(
                "POST", f"https://{emailtool.UI_HOST}{emailtool.CLONE_SAVE_PATH}", body)
            self.routes[-1][0](route, request)
            if route.actions == ["continue"]:
                self.test.zoho.details["600"] = clone_of(
                    self.test.source, "600", self.target_name,
                    emailtool.TARGET_TEMPLATES[self.target_name])
                self.last_response = FakeResponse(self.save_status, self.save_answer)

    # -- page API --------------------------------------------------------
    def route(self, pattern, handler):
        self.routes.append((handler, getattr(handler, "__closure__", None)))
        # Keep the handler's own state dict reachable so FakeRoute can inspect it.
        state = next((cell.cell_contents for cell in (handler.__closure__ or ())
                      if isinstance(cell.cell_contents, dict)
                      and "allowed" in cell.cell_contents), None)
        self.routes[-1] = (handler, state)

    def unroute(self, pattern, handler=None):
        self.unrouted = True

    def goto(self, url, wait_until=None, timeout=None):
        self.navigations.append(url)
        self.url = url

    def wait_for_timeout(self, ms):
        return None

    def evaluate(self, script, *args):
        self.evaluations.append(script)
        if script == emailtool.FORM_SNAPSHOT_JS:
            return dict(self.form, url=self.url)
        return self.test.zoho(args[0])

    def expect_response(self, predicate, timeout=None):
        return FakeExpect(self)


class FakeContext:
    def __init__(self, pages):
        self.pages = pages


class FakeBrowser:
    def __init__(self, pages):
        self.contexts = [FakeContext(pages)]


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser

    def connect_over_cdp(self, endpoint, timeout=None):
        assert endpoint == emailtool.CDP_ENDPOINT, endpoint
        return self.browser


class FakePlaywright:
    def __init__(self, page):
        self.chromium = FakeChromium(FakeBrowser([page]))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def clone_of(detail: dict, template_id: str, name: str, cc: tuple[str, ...]) -> dict:
    value = dict(detail)
    value.update({
        "email_template_id": template_id,
        "name": name,
        "cc_mail_ids": list(cc),
        "is_default": False,
        # Zoho derives this from the new name; it is never inherited. Measured
        # live on the real clone: "CC - Accounting" -> "mt_cc_accounting".
        "placeholder": emailtool.derived_placeholder(name),
    })
    return value


def list_row(detail: dict, **changes) -> dict:
    value = {
        "cc_me": detail["cc_me"],
        "documents": detail["documents"],
        "email_template_id": detail["email_template_id"],
        "is_default": detail["is_default"],
        "is_from_plugin": False,
        "is_new_editor": True,
        "name": detail["name"],
        "placeholder": detail["placeholder"],
        "subject": detail["subject"],
        "type": detail["type"],
        "type_formatted": (
            "Invoice Notification" if detail["type"] == "invoice_notification"
            else "Quote Notification"
        ),
    }
    value.update(changes)
    return value


class FakeZoho:
    """A minimal in-memory stand-in for the two read-only Books endpoints."""

    def __init__(self, details: list[dict]):
        self.details = {row["email_template_id"]: row for row in details}
        self.gets: list[str] = []
        self.status = 200
        self.ok = True

    def rows(self) -> list[dict]:
        # One foreign-module row is always present so module filtering is real.
        foreign = list_row(default_detail(
            email_template_id="777", name="Default", type="estimate_notification",
        ))
        return [list_row(row) for row in self.details.values()] + [foreign]

    def __call__(self, url: str) -> dict:
        self.gets.append(url)
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if parsed.path == emailtool.TEMPLATE_LIST_PATH:
            payload = {"code": 0, "message": "success", "emailtemplates": self.rows()}
        else:
            match = emailtool.TEMPLATE_DETAIL_RE.fullmatch(parsed.path)
            if match is None:
                raise AssertionError(f"unexpected read path {parsed.path}")
            found = self.details.get(match.group(1))
            if found is None:
                return {"status": 404, "ok": False, "text": json.dumps(
                    {"code": 1000, "message": "not found"})}
            payload = {"code": 0, "message": "success", "email_template": found,
                       "organization_emails": []}
        assert query.get("organization_id") == [FAKE_ORG], query
        return {"status": self.status, "ok": self.ok, "text": json.dumps(payload)}


class EmailTemplateToolTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        here = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(dir=here)
        self.root = Path(self.temp.name)
        self.plan_dir = self.root / "plans"
        self.plan_dir.mkdir()
        self.counter = 0
        self.vault = {
            "api_domain": "https://www.zohoapis.ca",
            "books_organization_id": FAKE_ORG,
            "books_organization_name": "FRP Depots",
        }
        self.source = default_detail()
        self.zoho = FakeZoho([self.source])
        self.patchers = [
            mock.patch.object(emailtool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(emailtool.zoho_tool, "load_vault", return_value=self.vault),
            mock.patch.object(emailtool.zoho_tool, "append_receipt"),
            mock.patch.object(
                emailtool.zoho_tool, "refresh_access_token",
                side_effect=AssertionError("token refresh is forbidden unless asserted"),
            ),
            mock.patch.object(
                emailtool.zoho_tool, "api_get",
                side_effect=AssertionError("live OAuth GET is forbidden in tests"),
            ),
            mock.patch.object(emailtool, "_execute_ui_get", side_effect=self.zoho),
        ]
        started = [patcher.start() for patcher in self.patchers]
        self.load_vault = started[1]
        self.append_receipt = started[2]
        self.refresh = started[3]
        self.api_get = started[4]
        self.execute_get = started[5]

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    # -- helpers ---------------------------------------------------------
    def input_path(self, value: object) -> Path:
        self.counter += 1
        path = self.root / f"input_{self.counter}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def confirmation(self, **changes) -> dict:
        value = {
            "android_test_succeeded": True,
            "confirmed_by": "Rachad Homsi",
            "confirmed_utc": (emailtool.utc_now() - timedelta(hours=2)).isoformat(),
            "statement": "I tested it on my phone. CC - Accounting shows up and the CC fills in.",
            "source": "Telegram message from Rachad, 2026-08-11.",
        }
        value.update(changes)
        return value

    def stage(self, action=emailtool.CREATE_ACCOUNTING_TEST, confirmation=None) -> Path:
        args = argparse.Namespace(
            action=action,
            android_test_confirmation=str(self.input_path(confirmation)) if confirmation else "",
        )
        with mock.patch("sys.stdout"):
            emailtool.command_stage(args)
        plans = sorted(self.plan_dir.glob("*.json"))
        return plans[-1]

    def commit_args(self, plan: Path, **changes) -> argparse.Namespace:
        value = {
            "action": emailtool.CREATE_ACCOUNTING_TEST,
            "plan": str(plan),
            "approval": "APPROVED",
            "verification_invoice_id": "",
        }
        value.update(changes)
        return argparse.Namespace(**value)

    def rewrite(self, plan: Path, **changes) -> Path:
        data = json.loads(plan.read_text(encoding="utf-8"))
        data.update(changes)
        core = {k: v for k, v in data.items() if k != "sha256"}
        data["sha256"] = emailtool.digest_for(core)
        plan.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return plan

    def add_accounting(self, **changes) -> dict:
        detail = clone_of(self.source, "500", "CC - Accounting",
                          (emailtool.ACCOUNTING,))
        detail.update(changes)
        self.zoho.details["500"] = detail
        return detail

    # -- approval --------------------------------------------------------
    def test_approval_must_be_byte_exact(self) -> None:
        for bad in ["approved", "Approved", " APPROVED", "APPROVED ", "APPROVED\n",
                    "APPROVED PLEASE", "", "APPROVE", None, True, 1]:
            with self.subTest(bad=bad):
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.require_rachad_approval(bad)
        emailtool.require_rachad_approval("APPROVED")

    def test_wrong_approval_refuses_before_any_plan_read_or_network(self) -> None:
        plan = self.stage()
        self.execute_get.reset_mock()
        self.load_vault.reset_mock()
        with self.assertRaises(emailtool.EmailTemplateError):
            emailtool.command_commit(self.commit_args(plan, approval="approved"))
        self.execute_get.assert_not_called()
        self.load_vault.assert_not_called()

    def test_tool_cannot_supply_its_own_approval(self) -> None:
        source = inspect.getsource(emailtool)
        self.assertNotIn('approval = APPROVAL_WORD', source)
        self.assertNotIn('approval="APPROVED"', source)

    # -- plan integrity --------------------------------------------------
    def test_tampered_plan_hash_is_refused(self) -> None:
        plan = self.stage()
        data = json.loads(plan.read_text(encoding="utf-8"))
        data["targets"][0]["cc_mail_ids"] = ["attacker@example.com"]
        plan.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "hash check failed"):
            emailtool.command_commit(self.commit_args(plan))

    def test_rehashed_plan_with_foreign_recipient_is_still_refused(self) -> None:
        plan = self.stage()
        data = json.loads(plan.read_text(encoding="utf-8"))
        data["targets"][0]["cc_mail_ids"] = ["attacker@example.com"]
        self.rewrite(plan, targets=data["targets"])
        with self.assertRaises(emailtool.EmailTemplateError):
            emailtool.command_commit(self.commit_args(plan))

    def test_expired_plan_is_refused(self) -> None:
        plan = self.stage()
        created = emailtool.utc_now() - timedelta(hours=30)
        self.rewrite(plan, created_utc=created.isoformat(),
                     expires_utc=(created + timedelta(hours=24)).isoformat())
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "expired"):
            emailtool.command_commit(self.commit_args(plan))

    def test_stretched_lifetime_is_refused(self) -> None:
        plan = self.stage()
        created = emailtool.utc_now()
        self.rewrite(plan, created_utc=created.isoformat(),
                     expires_utc=(created + timedelta(hours=72)).isoformat())
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "24-hour lifetime"):
            emailtool.command_commit(self.commit_args(plan))

    def test_wrong_organization_plan_is_refused(self) -> None:
        plan = self.stage()
        data = json.loads(plan.read_text(encoding="utf-8"))
        org = dict(data["organization"])
        org["books_organization_id"] = "999999"
        org["fingerprint"] = emailtool.digest_for(
            {"books_organization_id": "999999", "name": org["name"]})
        self.rewrite(plan, organization=org)
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "different Zoho Books"):
            emailtool.command_commit(self.commit_args(plan))

    def test_forged_organization_fingerprint_is_refused(self) -> None:
        plan = self.stage()
        data = json.loads(plan.read_text(encoding="utf-8"))
        org = dict(data["organization"])
        org["fingerprint"] = "0" * 64
        self.rewrite(plan, organization=org)
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "fingerprint is invalid"):
            emailtool.command_commit(self.commit_args(plan))

    def test_wrong_action_plan_is_refused(self) -> None:
        plan = self.stage()
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "not the requested"):
            emailtool.command_commit(
                self.commit_args(plan, action=emailtool.CREATE_REMAINING))

    def test_wrong_tool_version_is_refused(self) -> None:
        plan = self.stage()
        self.rewrite(plan, tool_version="9.9.9")
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "tool version"):
            emailtool.command_commit(self.commit_args(plan))

    def test_foreign_tool_plan_is_refused(self) -> None:
        plan = self.stage()
        self.rewrite(plan, tool="Some Other Tool")
        with self.assertRaises(emailtool.EmailTemplateError):
            emailtool.command_commit(self.commit_args(plan))

    def test_plan_origin_must_match_this_installation(self) -> None:
        plan = self.stage()
        self.rewrite(plan, origin={"tool_path": "C:/evil.py", "repo_root": "C:/evil",
                                   "plan_dir": "C:/evil"})
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "origin"):
            emailtool.command_commit(self.commit_args(plan))

    def test_plan_email_sent_must_be_false(self) -> None:
        plan = self.stage()
        self.rewrite(plan, email_sent=True)
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "email_sent"):
            emailtool.command_commit(self.commit_args(plan))

    def test_plan_outside_plan_folder_is_refused(self) -> None:
        stray = self.root / "stray.json"
        stray.write_text("{}", encoding="utf-8")
        with self.assertRaises(emailtool.EmailTemplateError):
            emailtool.contained_plan(str(stray))

    def test_relative_plan_path_is_refused(self) -> None:
        with self.assertRaises(emailtool.EmailTemplateError):
            emailtool.contained_plan("plan.json")

    def test_non_json_plan_suffix_is_refused(self) -> None:
        other = self.plan_dir / "plan.txt"
        other.write_text("{}", encoding="utf-8")
        with self.assertRaises(emailtool.EmailTemplateError):
            emailtool.contained_plan(str(other))

    def test_plan_states_non_atomicity(self) -> None:
        plan = json.loads(self.stage().read_text(encoding="utf-8"))
        self.assertIn(emailtool.NON_ATOMIC_RISK, plan["risks"])
        self.assertIs(plan["email_sent"], False)

    def test_plan_missing_non_atomicity_risk_is_refused(self) -> None:
        plan = self.stage()
        self.rewrite(plan, risks=["something else"])
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "non-atomicity"):
            emailtool.command_commit(self.commit_args(plan))

    def test_plan_records_required_provenance(self) -> None:
        plan = json.loads(self.stage().read_text(encoding="utf-8"))
        self.assertEqual(plan["sources"]["official_doc"], emailtool.OFFICIAL_DOC)
        self.assertIn("GET", plan["sources"]["live_evidence"])
        self.assertEqual(plan["workflow"]["host"], emailtool.UI_HOST)
        self.assertEqual(plan["workflow"]["settings_url"], emailtool.SETTINGS_URL)
        self.assertIs(plan["workflow"]["native_save_contract_captured"], True)
        self.assertIs(plan["workflow"]["create_workflow_commissioned"], True)
        self.assertEqual(plan["workflow"]["native_save_body_sha256"],
                         emailtool.NATIVE_SAVE_BODY_SHA256)
        self.assertEqual(plan["workflow"]["clone_save_body_sha256"],
                         emailtool.CLONE_SAVE_BODY_SHA256)
        self.assertEqual(plan["module"]["email_type"], "invoice_notification")
        self.assertEqual(plan["organization"]["books_organization_id"], FAKE_ORG)

    # -- staging is read-only -------------------------------------------
    def test_stage_performs_zero_writes(self) -> None:
        self.stage()
        for url in self.zoho.gets:
            parsed = urlsplit(url)
            self.assertEqual(parsed.hostname, emailtool.UI_HOST)
            self.assertTrue(
                parsed.path == emailtool.TEMPLATE_LIST_PATH
                or emailtool.TEMPLATE_DETAIL_RE.fullmatch(parsed.path),
                url,
            )
        self.api_get.assert_not_called()
        self.refresh.assert_not_called()

    def test_stage_never_enters_the_write_path(self) -> None:
        """REVISED, not weakened, when the read paths joined the browser mutex.

        This test used to assert that staging took NO browser lane. That was
        wrong once it was looked at properly: staging and listing both NAVIGATE
        the shared authenticated Zoho page, so while they hold no plan lock and
        write nothing, they can still collide with the other lane's in-flight
        form. They now take the same mutex. What must still never happen is any
        step of the WRITE path -- no create, no interceptor, no plan lock.
        """
        forbidden = [
            mock.patch.object(emailtool, "create_template_via_ui",
                              side_effect=AssertionError("stage must never create")),
            mock.patch.object(emailtool, "clone_save_interceptor",
                              side_effect=AssertionError("stage must never intercept")),
            mock.patch.object(emailtool, "write_lock",
                              side_effect=AssertionError("stage must never lock")),
        ]
        for patcher in forbidden:
            patcher.start()
        try:
            self.stage()
            with mock.patch("sys.stdout"):
                emailtool.command_list_templates(argparse.Namespace())
        finally:
            for patcher in reversed(forbidden):
                patcher.stop()
        self.assertEqual(self.locks(), [])

    def test_commit_pins_the_clone_source_to_the_fresh_live_default(self) -> None:
        plan = self.stage()
        data = json.loads(plan.read_text(encoding="utf-8"))
        source = dict(data["source"])
        source["email_template_id"] = "424242"
        self.rewrite(plan, source=source)
        seen: list = []

        def creator(org, source_id, name, cc, clone):  # pragma: no cover
            seen.append(source_id)
            return "600"

        with self.assertRaisesRegex(emailtool.EmailTemplateError, "not the 424242"):
            self.run_with_create(creator, self.commit_args(plan))
        self.assertEqual(seen, [])
        self.assertEqual(self.locks(), [])

    def test_commit_hands_the_live_default_id_to_the_clone_path(self) -> None:
        plan = self.stage()
        seen: list = []

        def creator(org, source_id, name, cc, clone):
            seen.append(source_id)
            self.zoho.details["600"] = clone_of(self.source, "600", name, cc)
            return "600"

        self.run_with_create(creator, self.commit_args(plan))
        self.assertEqual(seen, [DEFAULT_ID])

    def test_stage_receipt_records_zero_writes(self) -> None:
        self.stage()
        action, evidence = self.append_receipt.call_args[0]
        self.assertIn("staged_not_committed", action)
        self.assertIn("zoho_writes=0", evidence)
        self.assertIn("emails_sent=0", evidence)

    def test_module_has_no_write_transport_of_its_own(self) -> None:
        """REVISED, not weakened, when the Clone path was commissioned.

        The tool now drives Zoho's own UI, so browser verbs are expected. What
        must stay absent is a transport this tool controls: no HTTP client, no
        second fetch verb, no file upload, no keyboard entry, no credential or
        storage access.
        """
        source = inspect.getsource(emailtool)
        for token in ('urlopen', 'requests.', 'http.client', 'set_input_files',
                      '.press(', 'select_option', 'keyboard', 'insert_text',
                      'document.cookie', '.cookies', 'localStorage',
                      'sessionStorage', 'storage_state', 'request.headers',
                      'all_headers', 'add_init_script',
                      '"PUT"', '"PATCH"'):
            with self.subTest(token=token):
                self.assertNotIn(token, source)
        # DELETE was commissioned on 2026-08-11 to remove the template an
        # unapproved live write created. It is BOUNDED, not merely allowed:
        # exactly one occurrence, and only as the pinned method constant against
        # one hard-coded template path.
        self.assertEqual(source.count('"DELETE"'), 1)
        self.assertEqual(emailtool.DELETE_METHOD, "DELETE")
        self.assertTrue(
            emailtool.DELETE_PATH.endswith(emailtool.DELETE_TARGET_TEMPLATE_ID)
        )
        # Exactly one fetch call site, and it is the read.
        self.assertEqual(source.count("await fetch("), 1)
        self.assertEqual(source.count('method: "GET"'), 1)

    def test_exactly_one_post_can_ever_be_released(self) -> None:
        """The single write is Zoho's own request, gated by the interceptor."""
        source = inspect.getsource(emailtool)
        self.assertEqual(emailtool.CLONE_SAVE_METHOD, "POST")
        # One handler, one abort call site, and continue_ only twice: reads, and
        # the one validated Save.
        self.assertEqual(source.count("route.continue_()"), 4)
        self.assertEqual(source.count('route.abort("blockedbyclient")'), 5)
        self.assertEqual(source.count("def clone_save_interceptor"), 1)
        self.assertEqual(source.count("page.route("), 2)
        for token in ('method: "POST"', "method='POST'", 'method="POST"',
                      'fetch(url, {method: CLONE', 'sync_playwright().start'):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_only_the_commissioned_controls_are_ever_clicked(self) -> None:
        source = inspect.getsource(emailtool)
        self.assertEqual(emailtool.ROW_DISCLOSURE_LABEL, "Show dropdown menu")
        self.assertEqual(emailtool.CLONE_MENU_ITEM, "Clone")
        self.assertEqual(emailtool.SAVE_BUTTON_LABEL, "Save")
        for banned in emailtool.NEVER_CLICKED:
            with self.subTest(banned=banned):
                self.assertNotIn(f'name="{banned}"', source)
                self.assertNotIn(f"^{banned}$", source)
        # The create path opens the Default row's own Clone form, never New.
        self.assertIn("clone_email_template_id", source)
        self.assertNotIn("templates/new", source)

    def test_module_has_no_send_or_settings_mutation_route(self) -> None:
        source = inspect.getsource(emailtool)
        for token in ('/send', 'sendmail', 'smtp', 'graph.microsoft', 'outlook',
                      '/reminder', '/associate', 'pdftemplate', 'dkim', 'relay',
                      'force_delete', 'markassent', 'markasdefault', '/contacts',
                      '/customers', '/vendors', '/estimates', '/salesorders',
                      '/purchaseorders', '/creditnotes'):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_only_the_three_commissioned_actions_exist(self) -> None:
        """EXTENDED, not weakened: create_all_only is the third and last action.

        Every one of them CREATES a fixed template. No action mutates, deletes,
        renames, re-defaults or associates anything, and the parser can only
        offer what is in ACTIONS.
        """
        self.assertEqual(
            emailtool.ACTIONS,
            (emailtool.CREATE_ACCOUNTING_TEST, emailtool.CREATE_REMAINING,
             emailtool.CREATE_ALL_ONLY),
        )
        self.assertEqual(emailtool.CREATE_ALL_ONLY, "create_all_only")
        for action in emailtool.ACTIONS:
            self.assertTrue(action.startswith("create_"), action)
        for banned in ("update", "delete", "rename", "set-default", "associate",
                       "clone-module", "send", "email", "default"):
            for action in emailtool.ACTIONS:
                with self.subTest(banned=banned, action=action):
                    self.assertNotIn(banned, action)

    def test_parser_exposes_only_commissioned_commands(self) -> None:
        parser = emailtool.build_parser()
        actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        # stage-delete / commit-delete were commissioned on 2026-08-11 to remove
        # the CC - Accounting template an unapproved live write created. This list
        # is the whole reachable surface and must stay closed.
        self.assertEqual(
            sorted(actions[0].choices),
            ["commit", "commit-delete", "list-templates", "stage", "stage-delete"],
        )

    # -- transport allowlist ---------------------------------------------
    def test_transport_refuses_every_non_get_verb(self) -> None:
        for verb in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "get", ""):
            with self.subTest(verb=verb):
                with self.assertRaisesRegex(emailtool.EmailTemplateError, "REFUSED"):
                    emailtool.ui_transport_allowed(verb, emailtool.TEMPLATE_LIST_PATH,
                                                   FAKE_ORG)

    def test_transport_refuses_foreign_paths(self) -> None:
        for path in ("/api/v3/contacts", "/api/v3/settings/emailtemplates/0",
                     "/api/v3/settings/emailtemplates/abc", "/api/v3/invoices",
                     "/api/v3/settings/emailtemplates/14/default", "/"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(emailtool.EmailTemplateError, "REFUSED"):
                    emailtool.ui_transport_allowed("GET", path, FAKE_ORG)

    def test_transport_refuses_foreign_module_filter(self) -> None:
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "REFUSED"):
            emailtool.ui_transport_allowed(
                "GET", emailtool.TEMPLATE_LIST_PATH, FAKE_ORG,
                email_type="estimate_notification")

    def test_url_allowlist_rejects_foreign_hosts_and_schemes(self) -> None:
        for url in ("https://evil.example.com/api/v3/settings/emailtemplates?x=1",
                    "http://books.zohocloud.ca/api/v3/settings/emailtemplates",
                    "https://books.zoho.com/api/v3/settings/emailtemplates",
                    "https://books.zohocloud.ca:8443/api/v3/settings/emailtemplates",
                    "https://user:pw@books.zohocloud.ca/api/v3/settings/emailtemplates",
                    "https://books.zohocloud.ca/api/v3/settings/emailtemplates#frag",
                    "file:///c:/secret", "javascript:alert(1)"):
            with self.subTest(url=url):
                with self.assertRaisesRegex(emailtool.EmailTemplateError, "REFUSED"):
                    emailtool.ui_url_allowed(url)

    def test_url_allowlist_accepts_only_the_two_read_paths(self) -> None:
        base = f"https://{emailtool.UI_HOST}"
        emailtool.ui_url_allowed(f"{base}{emailtool.TEMPLATE_LIST_PATH}?organization_id=1")
        emailtool.ui_url_allowed(f"{base}{emailtool.TEMPLATE_LIST_PATH}/14?organization_id=1")

    def test_login_or_captcha_redirect_leaves_no_usable_page(self) -> None:
        class FakePage:
            def __init__(self, url): self.url = url

        class FakeContext:
            def __init__(self, pages): self.pages = pages

        class FakeBrowser:
            def __init__(self, pages): self.contexts = [FakeContext(pages)]

        for url in ("https://accounts.zohocloud.ca/signin",
                    "https://books.zohocloud.ca/login",
                    "https://books.zohocloud.ca/captcha",
                    "https://evil.example.com/app",
                    "http://books.zohocloud.ca/app"):
            with self.subTest(url=url):
                with self.assertRaisesRegex(emailtool.EmailTemplateError,
                                            "No authenticated live Zoho Books page"):
                    emailtool._authenticated_books_page(FakeBrowser([FakePage(url)]))
        page = emailtool._authenticated_books_page(
            FakeBrowser([FakePage("https://books.zohocloud.ca/app#/settings")]))
        self.assertTrue(page.url.endswith("#/settings"))

    def test_http_error_and_zoho_error_code_fail_closed(self) -> None:
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "HTTP 403"):
            emailtool._decode_ui_result({"status": 403, "ok": False, "text": "{}"})
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "read failed"):
            emailtool._decode_ui_result(
                {"status": 200, "ok": True,
                 "text": json.dumps({"code": 1000, "message": "no permission"})})
        with self.assertRaises(emailtool.EmailTemplateError):
            emailtool._decode_ui_result({"status": 200, "ok": True, "text": "<html>login</html>"})
        with self.assertRaises(emailtool.EmailTemplateError):
            emailtool._decode_ui_result({"unexpected": True})

    # -- source template constraints -------------------------------------
    def test_source_must_be_exactly_one_default_invoice_template(self) -> None:
        # A second literal "Default" trips the duplicate-name guard first; both
        # paths are the same fail-closed refusal to guess which one is the source.
        self.zoho.details["501"] = clone_of(self.source, "501", "Default", ())
        with self.assertRaisesRegex(
            emailtool.EmailTemplateError, "exactly one|equivalent names"
        ):
            self.stage()

    def test_similarly_named_second_default_is_refused(self) -> None:
        self.zoho.details["501"] = clone_of(self.source, "501", "default", ())
        with self.assertRaises(emailtool.EmailTemplateError):
            self.stage()

    def test_missing_default_is_refused(self) -> None:
        self.zoho.details.clear()
        self.zoho.details["501"] = clone_of(self.source, "501", "CC - Accounting",
                                            (emailtool.ACCOUNTING,))
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "exactly one"):
            self.stage()

    def test_nonempty_default_bcc_is_refused(self) -> None:
        self.source["bcc_mail_ids"] = ["hidden@example.com"]
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "preset BCC"):
            self.stage()

    def test_unexpected_default_cc_is_refused(self) -> None:
        self.source["cc_mail_ids"] = ["someone@example.com"]
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "preset CC"):
            self.stage()

    def test_source_that_is_not_module_default_is_refused(self) -> None:
        self.source["is_default"] = False
        with self.assertRaises(emailtool.EmailTemplateError):
            self.stage()

    def test_detail_schema_drift_fails_closed(self) -> None:
        self.source["surprise_new_field"] = "x"
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "closed schema"):
            self.stage()

    def test_detail_missing_field_fails_closed(self) -> None:
        self.source.pop("bodyv2")
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "closed schema"):
            self.stage()

    def test_list_row_drift_fails_closed(self) -> None:
        original = self.zoho.rows

        def drifted():
            rows = original()
            rows[0]["brand_new_column"] = 1
            return rows

        self.zoho.rows = drifted
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "closed schema"):
            self.stage()

    def test_mislabelled_module_row_fails_closed(self) -> None:
        original = self.zoho.rows

        def drifted():
            rows = original()
            rows[0]["type_formatted"] = "Quote Notification"
            return rows

        self.zoho.rows = drifted
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "not 'Invoice Notification'"):
            self.stage()

    def test_source_drift_between_stage_and_commit_is_refused(self) -> None:
        plan = self.stage()
        self.source["subject"] = "Changed by someone else"
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "changed"):
            emailtool.command_commit(self.commit_args(plan))

    def test_clone_fields_exclude_identity_cc_and_derived_values(self) -> None:
        for excluded in ("email_template_id", "name", "cc_mail_ids", "is_default",
                         "placeholder"):
            self.assertNotIn(excluded, emailtool.SOURCE_CLONE_FIELDS)
        self.assertEqual(
            set(emailtool.SOURCE_CLONE_FIELDS)
            | {"email_template_id", "name", "cc_mail_ids", "is_default"}
            | set(emailtool.DERIVED_FROM_NAME_FIELDS),
            set(emailtool.SOURCE_DETAIL_FIELDS),
        )
        # The source is still fingerprinted in full, placeholder included, so
        # dropping it from the inherited set weakens nothing about Default.
        plan = json.loads(self.stage().read_text(encoding="utf-8"))
        self.assertEqual(plan["live_evidence"]["source_detail"]["placeholder"],
                         "mt_default")

    def test_placeholder_is_derived_from_the_name_not_inherited(self) -> None:
        """The defect that would have orphaned every template it created.

        Zoho derives this internal id from the template's own name, so a clone
        can never carry the source's. Requiring byte-equality on it meant a
        successful create always failed its own read-back afterwards.
        """
        self.assertEqual(emailtool.derived_placeholder("Default"), "mt_default")
        self.assertEqual(emailtool.derived_placeholder("CC - Accounting"),
                         "mt_cc_accounting")
        self.assertEqual(emailtool.derived_placeholder("CC - All"), "mt_cc_all")
        clone = emailtool.require_clean_source(self.source)
        cc = emailtool.TARGET_TEMPLATES["CC - Accounting"]
        good = clone_of(self.source, "600", "CC - Accounting", cc)
        self.assertEqual(good["placeholder"], "mt_cc_accounting")
        emailtool.verify_created_template(good, "CC - Accounting", cc, clone)
        # Inheriting the source's placeholder is now itself the refusal.
        stale = dict(good, placeholder="mt_default")
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "placeholder"):
            emailtool.verify_created_template(stale, "CC - Accounting", cc, clone)

    def test_no_test_can_open_a_real_browser_session(self) -> None:
        import playwright.sync_api as pwapi

        with self.assertRaisesRegex(AssertionError, "REAL browser"):
            pwapi.sync_playwright()
        with self.assertRaises(AssertionError):
            emailtool.create_template_via_ui(
                FAKE_ORG, DEFAULT_ID, "CC - Accounting",
                emailtool.TARGET_TEMPLATES["CC - Accounting"],
                emailtool.require_clean_source(self.source),
            )

    # -- fixed target set -------------------------------------------------
    def test_exactly_four_fixed_templates_and_recipients(self) -> None:
        self.assertEqual(sorted(emailtool.TARGET_TEMPLATES), [
            "CC - Accounting", "CC - All", "CC - Logistics", "CC - Operations"])
        self.assertEqual(emailtool.TARGET_TEMPLATES["CC - Accounting"],
                         ("accounting@frpdepots.com",))
        self.assertEqual(emailtool.TARGET_TEMPLATES["CC - Logistics"],
                         ("logistics@frpdepots.com",))
        self.assertEqual(emailtool.TARGET_TEMPLATES["CC - Operations"],
                         ("operations@frpdepots.com",))
        self.assertEqual(emailtool.TARGET_TEMPLATES["CC - All"], (
            "logistics@frpdepots.com", "accounting@frpdepots.com",
            "operations@frpdepots.com"))

    def test_arbitrary_names_and_addresses_are_refused(self) -> None:
        for name in ("CC - Sales", "cc - accounting", "Default", "", "CC - All ",
                     None, 42):
            with self.subTest(name=name):
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.require_fixed_target(name, ["accounting@frpdepots.com"], "t")
        for cc in (["rachad@gmail.com"], ["ACCOUNTING@frpdepots.com"], [], None,
                   ["accounting@frpdepots.com", "extra@frpdepots.com"],
                   [" accounting@frpdepots.com"]):
            with self.subTest(cc=cc):
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.require_fixed_target("CC - Accounting", cc, "t")

    def test_cc_all_order_is_enforced(self) -> None:
        emailtool.require_fixed_target("CC - All", [
            "logistics@frpdepots.com", "accounting@frpdepots.com",
            "operations@frpdepots.com"], "t")
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "in that order"):
            emailtool.require_fixed_target("CC - All", [
                "accounting@frpdepots.com", "logistics@frpdepots.com",
                "operations@frpdepots.com"], "t")

    def test_only_the_three_fixed_addresses_are_accepted(self) -> None:
        for address in ("rachad@gmail.com", "info@frpdepots.com", "sales@frpdepots.com",
                        "accounting@frpdepot.com", "", None):
            with self.subTest(address=address):
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.require_fixed_address(address, "cc")

    def test_targets_are_never_default_or_associated(self) -> None:
        plan = json.loads(self.stage().read_text(encoding="utf-8"))
        for target in plan["targets"]:
            self.assertIs(target["is_default"], False)
            self.assertIs(target["customer_associated"], False)

    def test_plan_target_marked_default_is_refused(self) -> None:
        plan = self.stage()
        data = json.loads(plan.read_text(encoding="utf-8"))
        data["targets"][0]["is_default"] = True
        self.rewrite(plan, targets=data["targets"])
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "non-default"):
            emailtool.command_commit(self.commit_args(plan))

    def test_plan_target_marked_associated_is_refused(self) -> None:
        plan = self.stage()
        data = json.loads(plan.read_text(encoding="utf-8"))
        data["targets"][0]["customer_associated"] = True
        self.rewrite(plan, targets=data["targets"])
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "unassociated"):
            emailtool.command_commit(self.commit_args(plan))

    # -- two-phase contract -----------------------------------------------
    def test_first_action_creates_accounting_only(self) -> None:
        plan = json.loads(self.stage().read_text(encoding="utf-8"))
        self.assertEqual([t["name"] for t in plan["targets"]], ["CC - Accounting"])
        self.assertIsNone(plan["android_test_confirmation"])

    def test_second_action_creates_exactly_the_remaining_three(self) -> None:
        self.add_accounting()
        plan = json.loads(
            self.stage(emailtool.CREATE_REMAINING, self.confirmation()).read_text("utf-8"))
        self.assertEqual([t["name"] for t in plan["targets"]],
                         ["CC - Logistics", "CC - Operations", "CC - All"])
        self.assertEqual(plan["android_test_confirmation"]["confirmed_by"], "Rachad Homsi")

    def test_second_action_requires_android_confirmation_file(self) -> None:
        self.add_accounting()
        with self.assertRaisesRegex(emailtool.EmailTemplateError,
                                    "requires --android-test-confirmation"):
            self.stage(emailtool.CREATE_REMAINING)

    def test_first_action_rejects_an_android_confirmation(self) -> None:
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "takes no"):
            self.stage(emailtool.CREATE_ACCOUNTING_TEST, self.confirmation())

    def test_second_action_refused_when_accounting_missing(self) -> None:
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "must already"):
            self.stage(emailtool.CREATE_REMAINING, self.confirmation())

    def test_second_action_refused_when_accounting_cc_is_wrong(self) -> None:
        self.add_accounting(cc_mail_ids=["logistics@frpdepots.com"])
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "CC list"):
            self.stage(emailtool.CREATE_REMAINING, self.confirmation())

    def test_second_action_refused_when_accounting_drifted_from_default(self) -> None:
        self.add_accounting(subject="Someone edited this")
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "preserve the source"):
            self.stage(emailtool.CREATE_REMAINING, self.confirmation())

    def test_second_action_refused_when_accounting_became_default(self) -> None:
        self.add_accounting(is_default=True)
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "default"):
            self.stage(emailtool.CREATE_REMAINING, self.confirmation())

    def test_second_action_refused_when_accounting_has_bcc(self) -> None:
        self.add_accounting(bcc_mail_ids=["x@frpdepots.com"])
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "BCC"):
            self.stage(emailtool.CREATE_REMAINING, self.confirmation())

    def test_commissioning_text_is_not_android_confirmation(self) -> None:
        for field in ("statement", "source"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(emailtool.EmailTemplateError, "Commissioning"):
                    emailtool.validate_android_confirmation(
                        self.confirmation(**{field: "Rachad commissioned this tool."}))

    def test_android_confirmation_must_be_rachad_and_true_and_fresh(self) -> None:
        for change in (
            {"android_test_succeeded": False},
            {"android_test_succeeded": "true"},
            {"confirmed_by": "Dado"},
            {"confirmed_by": "rachad homsi"},
            {"statement": "   "},
            {"source": ""},
            {"confirmed_utc": (emailtool.utc_now() + timedelta(days=1)).isoformat()},
            {"confirmed_utc": (emailtool.utc_now() - timedelta(days=60)).isoformat()},
            {"confirmed_utc": "not-a-date"},
            {"confirmed_utc": "2026-08-10T00:00:00"},
        ):
            with self.subTest(change=change):
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.validate_android_confirmation(self.confirmation(**change))

    def test_android_confirmation_schema_is_closed(self) -> None:
        extra = self.confirmation()
        extra["approved"] = True
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "closed schema"):
            emailtool.validate_android_confirmation(extra)
        missing = self.confirmation()
        missing.pop("source")
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "closed schema"):
            emailtool.validate_android_confirmation(missing)

    def test_remaining_plan_without_confirmation_is_refused_at_commit(self) -> None:
        self.add_accounting()
        plan = self.stage(emailtool.CREATE_REMAINING, self.confirmation())
        self.rewrite(plan, android_test_confirmation=None)
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "Android-test confirmation"):
            emailtool.command_commit(
                self.commit_args(plan, action=emailtool.CREATE_REMAINING))

    def test_first_plan_carrying_confirmation_is_refused_at_commit(self) -> None:
        plan = self.stage()
        self.rewrite(plan, android_test_confirmation=self.confirmation())
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "must not carry"):
            emailtool.command_commit(self.commit_args(plan))

    # -- the third action: create_all_only ---------------------------------
    #
    # Rachad confirmed the Android test on 2026-08-11 and then asked for ONE
    # template holding all three internal recipients. This action stages exactly
    # that one target. CC - Logistics and CC - Operations are unreachable
    # through it -- not staged, not created, and not even checked for.
    # ----------------------------------------------------------------------
    def add_all(self, **changes) -> dict:
        detail = clone_of(self.source, "550", "CC - All",
                          emailtool.TARGET_TEMPLATES["CC - All"])
        detail.update(changes)
        self.zoho.details["550"] = detail
        return detail

    def stage_all_only(self, confirmation=None) -> Path:
        return self.stage(emailtool.CREATE_ALL_ONLY,
                          confirmation if confirmation is not None else self.confirmation())

    def test_create_all_only_has_exactly_one_target(self) -> None:
        self.assertEqual(emailtool.ACTION_TARGETS[emailtool.CREATE_ALL_ONLY],
                         ("CC - All",))
        self.assertEqual(emailtool.ALL_TEMPLATE, "CC - All")
        self.add_accounting()
        plan = json.loads(self.stage_all_only().read_text(encoding="utf-8"))
        self.assertEqual([t["name"] for t in plan["targets"]], ["CC - All"])
        self.assertEqual(len(plan["targets"]), 1)

    def test_create_all_only_plan_carries_the_exact_cc_order(self) -> None:
        self.add_accounting()
        plan = json.loads(self.stage_all_only().read_text(encoding="utf-8"))
        target = plan["targets"][0]
        self.assertEqual(target["cc_mail_ids"], [
            "logistics@frpdepots.com", "accounting@frpdepots.com",
            "operations@frpdepots.com"])
        self.assertIs(target["is_default"], False)
        self.assertIs(target["customer_associated"], False)

    def test_create_all_only_requires_the_android_confirmation(self) -> None:
        self.add_accounting()
        with self.assertRaisesRegex(emailtool.EmailTemplateError,
                                    "requires --android-test-confirmation"):
            self.stage(emailtool.CREATE_ALL_ONLY)
        self.assertEqual(sorted(self.plan_dir.glob("*.json")), [])

    def test_create_all_only_plan_stripped_of_its_confirmation_is_refused(self) -> None:
        self.add_accounting()
        plan = self.stage_all_only()
        self.rewrite(plan, android_test_confirmation=None)
        with self.assertRaisesRegex(emailtool.EmailTemplateError,
                                    "Android-test confirmation"):
            emailtool.command_commit(
                self.commit_args(plan, action=emailtool.CREATE_ALL_ONLY))
        self.assertEqual(self.locks(), [])

    def test_create_all_only_rejects_commissioning_text_as_confirmation(self) -> None:
        self.add_accounting()
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "Commissioning"):
            self.stage_all_only(
                self.confirmation(statement="Rachad commissioned CC - All."))

    def test_create_all_only_requires_a_live_verified_accounting_template(self) -> None:
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "must already"):
            self.stage_all_only()
        for change, pattern in (
            ({"cc_mail_ids": ["logistics@frpdepots.com"]}, "CC list"),
            ({"is_default": True}, "default"),
            ({"bcc_mail_ids": ["x@frpdepots.com"]}, "BCC"),
            ({"subject": "Someone edited this"}, "preserve the source"),
        ):
            with self.subTest(change=change):
                self.zoho.details.pop("500", None)
                self.add_accounting(**change)
                with self.assertRaisesRegex(emailtool.EmailTemplateError, pattern):
                    self.stage_all_only()
        self.assertEqual(sorted(self.plan_dir.glob("*.json")), [])

    def test_create_all_only_plan_without_accounting_evidence_is_refused(self) -> None:
        self.add_accounting()
        plan = self.stage_all_only()
        data = json.loads(plan.read_text(encoding="utf-8"))
        live = dict(data["live_evidence"])
        live["accounting_template"] = None
        live["accounting_template_sha256"] = emailtool.digest_for(None)
        self.rewrite(plan, live_evidence=live)
        with self.assertRaisesRegex(emailtool.EmailTemplateError,
                                    "requires verified live"):
            emailtool.command_commit(
                self.commit_args(plan, action=emailtool.CREATE_ALL_ONLY))
        self.assertEqual(self.locks(), [])

    def test_existing_accounting_does_not_block_create_all_only(self) -> None:
        """The opposite of a blocker: CC - Accounting is its precondition."""
        self.add_accounting()
        plan = json.loads(self.stage_all_only().read_text(encoding="utf-8"))
        self.assertEqual([row["name"] for row in plan["live_evidence"]["targets_absent"]],
                         ["CC - All"])
        self.assertEqual(plan["live_evidence"]["accounting_template"]["name"],
                         "CC - Accounting")

    def test_existing_all_blocks_create_all_only(self) -> None:
        self.add_accounting()
        for name in ("CC - All", "cc - all", "CC-All", "ccall", "CC  -  All"):
            with self.subTest(name=name):
                self.zoho.details["550"] = clone_of(
                    self.source, "550", name, emailtool.TARGET_TEMPLATES["CC - All"])
                with self.assertRaisesRegex(emailtool.EmailTemplateError,
                                            "already exists"):
                    self.stage_all_only()
                del self.zoho.details["550"]

    def test_all_created_between_stage_and_commit_is_caught(self) -> None:
        self.add_accounting()
        plan = self.stage_all_only()
        self.add_all()
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "already exists"):
            emailtool.command_commit(
                self.commit_args(plan, action=emailtool.CREATE_ALL_ONLY))
        self.assertEqual(self.locks(), [], "a duplicate must refuse before the lock")

    def test_create_all_only_ignores_logistics_and_operations_entirely(self) -> None:
        """Neither their presence nor their absence may affect this action."""
        self.add_accounting()
        first = json.loads(self.stage_all_only().read_text(encoding="utf-8"))
        self.assertEqual([t["name"] for t in first["targets"]], ["CC - All"])

        # Both already exist -> still stages, still exactly one target.
        self.zoho.details["560"] = clone_of(
            self.source, "560", "CC - Logistics", (emailtool.LOGISTICS,))
        self.zoho.details["570"] = clone_of(
            self.source, "570", "CC - Operations", (emailtool.OPERATIONS,))
        second = json.loads(self.stage_all_only().read_text(encoding="utf-8"))
        self.assertEqual([t["name"] for t in second["targets"]], ["CC - All"])
        self.assertEqual(
            [row["name"] for row in second["live_evidence"]["targets_absent"]],
            ["CC - All"])

    def test_create_all_only_can_never_carry_a_logistics_or_operations_target(self) -> None:
        self.add_accounting()
        for name in ("CC - Logistics", "CC - Operations", "CC - Accounting"):
            with self.subTest(name=name):
                plan = self.stage_all_only()
                self.rewrite(plan, targets=[{
                    "name": name,
                    "cc_mail_ids": list(emailtool.TARGET_TEMPLATES[name]),
                    "is_default": False, "customer_associated": False,
                }])
                with self.assertRaisesRegex(emailtool.EmailTemplateError,
                                            "must be 'CC - All'"):
                    emailtool.command_commit(
                        self.commit_args(plan, action=emailtool.CREATE_ALL_ONLY))
                self.assertEqual(self.locks(), [])

    def test_create_all_only_refuses_extra_or_reordered_targets(self) -> None:
        self.add_accounting()
        all_target = {
            "name": "CC - All",
            "cc_mail_ids": list(emailtool.TARGET_TEMPLATES["CC - All"]),
            "is_default": False, "customer_associated": False,
        }
        logistics = {
            "name": "CC - Logistics", "cc_mail_ids": [emailtool.LOGISTICS],
            "is_default": False, "customer_associated": False,
        }
        for targets in ([all_target, logistics], [logistics, all_target], [],
                        [all_target, all_target]):
            with self.subTest(count=len(targets)):
                plan = self.stage_all_only()
                self.rewrite(plan, targets=targets)
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.command_commit(
                        self.commit_args(plan, action=emailtool.CREATE_ALL_ONLY))
                self.assertEqual(self.locks(), [])

    def test_create_all_only_refuses_arbitrary_recipients(self) -> None:
        self.add_accounting()
        for cc in (["rachad@gmail.com"],
                   ["info@frpdepots.com"],
                   ["logistics@frpdepots.com"],
                   ["accounting@frpdepots.com", "logistics@frpdepots.com",
                    "operations@frpdepots.com"],
                   ["logistics@frpdepots.com", "accounting@frpdepots.com",
                    "operations@frpdepots.com", "info@frpdepots.com"],
                   []):
            with self.subTest(cc=cc):
                plan = self.stage_all_only()
                self.rewrite(plan, targets=[{
                    "name": "CC - All", "cc_mail_ids": cc,
                    "is_default": False, "customer_associated": False,
                }])
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.command_commit(
                        self.commit_args(plan, action=emailtool.CREATE_ALL_ONLY))
                self.assertEqual(self.locks(), [])

    def test_create_all_only_plan_pins_schema_and_tool_version(self) -> None:
        self.add_accounting()
        plan = json.loads(self.stage_all_only().read_text(encoding="utf-8"))
        self.assertEqual(plan["schema_version"], 4)
        self.assertEqual(plan["tool_version"], "2.2.0")
        self.assertEqual(plan["action"], "create_all_only")
        self.assertEqual(plan["approval_required"], "APPROVED")
        self.assertIs(plan["email_sent"], False)
        self.assertIn(emailtool.NON_ATOMIC_RISK, plan["risks"])

    def test_create_all_only_plan_cannot_be_committed_as_another_action(self) -> None:
        self.add_accounting()
        plan = self.stage_all_only()
        for action in (emailtool.CREATE_ACCOUNTING_TEST, emailtool.CREATE_REMAINING):
            with self.subTest(action=action):
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.command_commit(self.commit_args(plan, action=action))
        self.assertEqual(self.locks(), [])

    def test_create_all_only_locks_before_its_single_creation(self) -> None:
        self.add_accounting()
        plan = self.stage_all_only()
        seen: list = []

        def creator(org, source_id, name, cc, clone):
            seen.append((name, tuple(cc), bool(self.locks())))
            self.zoho.details["600"] = clone_of(self.source, "600", name, cc)
            return "600"

        self.run_with_create(
            creator, self.commit_args(plan, action=emailtool.CREATE_ALL_ONLY))
        self.assertEqual(seen, [(
            "CC - All",
            ("logistics@frpdepots.com", "accounting@frpdepots.com",
             "operations@frpdepots.com"),
            True,
        )])
        lock = self.locks()[0]
        self.assertEqual(lock["status"], "committed_verified")
        self.assertEqual(lock["created_template_ids"], {"CC - All": "600"})
        self.assertIs(lock["no_retry"], True)
        self.assertIs(lock["email_sent"], False)

    def test_create_all_only_commits_through_exactly_one_validated_post(self) -> None:
        """End to end against the recording fake page: one POST, then read-back."""
        import playwright.sync_api as pwapi

        self.add_accounting()
        plan = self.stage_all_only()
        page = self.fake_page("CC - All")
        with mock.patch.object(pwapi, "sync_playwright", lambda: FakePlaywright(page)):
            with mock.patch("sys.stdout"):
                emailtool.command_commit(
                    self.commit_args(plan, action=emailtool.CREATE_ALL_ONLY))
        self.assertEqual(page.save_attempts, 1)
        state = page.routes[-1][1]
        self.assertEqual(state["seen"], 1)
        self.assertIs(state["allowed"], True)
        self.assertEqual(state["payload"]["name"], "CC - All")
        self.assertEqual(state["payload"]["cc_mail_ids"], [
            "logistics@frpdepots.com", "accounting@frpdepots.com",
            "operations@frpdepots.com"])
        self.assertEqual(state["payload"]["bcc_mail_ids"], [])
        self.assertIs(state["payload"]["is_default"], False)
        self.assertEqual(self.locks()[0]["created_template_ids"], {"CC - All": "600"})
        # One template created, and Default is untouched.
        self.assertEqual(self.zoho.details[DEFAULT_ID], self.source)
        self.assertNotIn("CC - Logistics",
                         [row["name"] for row in self.zoho.details.values()])

    def test_create_all_only_never_retries_after_a_failure(self) -> None:
        self.add_accounting()
        plan = self.stage_all_only()
        calls: list = []

        def creator(org, source_id, name, cc, clone):
            calls.append(name)
            raise emailtool.EmailTemplateError("Zoho Save failed")

        with self.assertRaisesRegex(emailtool.EmailTemplateError, "indeterminate"):
            self.run_with_create(
                creator, self.commit_args(plan, action=emailtool.CREATE_ALL_ONLY))
        self.assertEqual(calls, ["CC - All"])
        lock = self.locks()[0]
        self.assertEqual(lock["status"], "indeterminate")
        self.assertEqual(lock["write_in_flight_template"], "CC - All")
        self.assertEqual(lock["not_attempted"], [])
        self.assertIs(lock["no_retry"], True)
        # A second attempt is refused by the lock, and never reaches the creator.
        calls.clear()
        with self.assertRaisesRegex(emailtool.EmailTemplateError,
                                    "already entered commit"):
            self.run_with_create(
                creator, self.commit_args(plan, action=emailtool.CREATE_ALL_ONLY))
        self.assertEqual(calls, [])

    def test_create_all_only_readback_rejects_every_drift(self) -> None:
        for change in ({"cc_mail_ids": ["accounting@frpdepots.com",
                                        "logistics@frpdepots.com",
                                        "operations@frpdepots.com"]},
                       {"cc_mail_ids": ["logistics@frpdepots.com"]},
                       {"bcc_mail_ids": ["hidden@frpdepots.com"]},
                       {"is_default": True},
                       {"name": "CC - All 2"},
                       {"subject": "different"},
                       {"body": REAL_BODY.replace("PAY NOW", "PAY LATER")},
                       {"placeholder": "mt_default"},
                       {"type": "estimate_notification"},
                       {"from_address_id": "9"}):
            with self.subTest(change=change):
                self.zoho.details.pop("500", None)
                self.add_accounting()
                plan = self.stage_all_only()

                def creator(org, source_id, name, cc, clone, change=change):
                    wrong = clone_of(self.source, "600", name, cc)
                    wrong.update(change)
                    self.zoho.details["600"] = wrong
                    return "600"

                with self.assertRaises(emailtool.EmailTemplateError):
                    self.run_with_create(
                        creator,
                        self.commit_args(plan, action=emailtool.CREATE_ALL_ONLY))
                self.assertIs(self.locks()[-1]["no_retry"], True)
                self.zoho.details.pop("600", None)

    def test_create_all_only_readback_accepts_only_attribute_reordering(self) -> None:
        clone = emailtool.require_clean_source(self.source)
        cc = emailtool.TARGET_TEMPLATES["CC - All"]
        detail = clone_of(self.source, "600", "CC - All", cc)
        detail["body"] = REAL_BODY.replace(
            '<a href="%PaymentLink%" style="text-decoration: none;">',
            '<a style="text-decoration: none;" href="%PaymentLink%">')
        self.assertNotEqual(detail["body"], REAL_BODY)
        verified = emailtool.verify_created_template(detail, "CC - All", cc, clone)
        self.assertEqual(verified["cc_mail_ids"], list(cc))
        self.assertEqual(verified["bcc_mail_ids"], [])
        self.assertIs(verified["is_default"], False)
        self.assertEqual(verified["placeholder"], "mt_cc_all")

    def test_the_real_android_confirmation_file_validates_normally(self) -> None:
        """Rachad's own durable confirmation, validated by the ordinary rules.

        The clock is pinned two hours after his own timestamp so this proves the
        file passes the real validator forever, instead of quietly turning into
        a failure once the 30-day freshness window closes.
        """
        path = (emailtool.CAPTURE_DIR /
                "android_test_confirmation_20260811.json")
        self.assertTrue(path.is_file(), path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(raw), set(emailtool.ANDROID_CONFIRMATION_FIELDS))
        self.assertIs(raw["android_test_succeeded"], True)
        self.assertEqual(raw["confirmed_by"], "Rachad Homsi")
        confirmed = emailtool.parse_time(raw["confirmed_utc"], "confirmed_utc")
        with mock.patch.object(emailtool, "utc_now",
                               lambda: confirmed + timedelta(hours=2)):
            canonical = emailtool.validate_android_confirmation(raw)
            self.assertEqual(canonical["confirmed_by"], "Rachad Homsi")
            self.assertIn("CC - Accounting", canonical["statement"])
            # And a plan may be staged from that exact file.
            self.add_accounting()
            args = argparse.Namespace(
                action=emailtool.CREATE_ALL_ONLY,
                android_test_confirmation=str(path))
            with mock.patch("sys.stdout"):
                emailtool.command_stage(args)
        plan = json.loads(
            sorted(self.plan_dir.glob("*.json"))[-1].read_text(encoding="utf-8"))
        self.assertEqual(plan["android_test_confirmation"], canonical)
        self.assertEqual([t["name"] for t in plan["targets"]], ["CC - All"])
        # Stale is still stale, even for the real file.
        with mock.patch.object(emailtool, "utc_now",
                               lambda: confirmed + timedelta(days=45)):
            with self.assertRaisesRegex(emailtool.EmailTemplateError, "older than"):
                emailtool.validate_android_confirmation(raw)

    def test_the_third_action_adds_no_email_or_mutation_route(self) -> None:
        source = inspect.getsource(emailtool)
        for token in ('/send', 'sendmail', 'smtp', 'graph.microsoft', 'outlook',
                      '/reminder', '/associate', 'markasdefault', 'force_delete',
                      '"PUT"', '"PATCH"', 'urlopen'):
            with self.subTest(token=token):
                self.assertNotIn(token, source)
        self.assertEqual(source.count("await fetch("), 1)
        self.assertEqual(source.count('method: "GET"'), 1)
        # Two interceptors now: the Clone Save POST and the commissioned DELETE.
        # Each passes reads through and releases at most one validated write.
        self.assertEqual(source.count("route.continue_()"), 4)
        self.assertEqual(source.count("page.route("), 2)
        self.assertEqual(source.count("def delete_interceptor"), 1)
        self.assertEqual(emailtool.CLONE_SAVE_METHOD, "POST")

    # -- duplicate protection ---------------------------------------------
    def test_existing_target_blocks_staging(self) -> None:
        self.add_accounting()
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "already exists"):
            self.stage()

    def test_similarly_normalized_target_blocks_staging(self) -> None:
        for name in ("cc - accounting", "CC-Accounting", "ccaccounting", "CC  -  Accounting"):
            with self.subTest(name=name):
                self.zoho.details["502"] = clone_of(
                    self.source, "502", name, (emailtool.ACCOUNTING,))
                with self.assertRaisesRegex(emailtool.EmailTemplateError, "already exists"):
                    self.stage()
                del self.zoho.details["502"]

    def test_target_created_between_stage_and_commit_is_caught(self) -> None:
        plan = self.stage()
        self.add_accounting()
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "already exists"):
            emailtool.command_commit(self.commit_args(plan))

    def test_normalized_key_collapses_case_space_and_punctuation(self) -> None:
        self.assertEqual(emailtool.normalized_key("CC - Accounting"), "ccaccounting")
        self.assertEqual(emailtool.normalized_key("cc_accounting"), "ccaccounting")
        self.assertNotEqual(emailtool.normalized_key("CC - All"),
                            emailtool.normalized_key("CC - Accounting"))

    # -- the captured native Save contract ---------------------------------
    def test_capture_artifact_parses_to_the_exact_closed_native_payload(self) -> None:
        payload = emailtool.captured_native_payload()
        self.assertEqual(set(payload), set(emailtool.NATIVE_PAYLOAD_FIELDS))
        self.assertEqual(payload["name"], "CC - Accounting")
        self.assertEqual(payload["type"], "invoice_notification")
        self.assertEqual(payload["cc_mail_ids"], ["accounting@frpdepots.com"])
        self.assertEqual(payload["bcc_mail_ids"], [])
        self.assertIs(payload["is_default"], False)
        self.assertEqual(payload["from_address_id"], "")
        self.assertEqual(len(payload["language_content"]), 1)
        block = payload["language_content"][0]
        self.assertEqual(set(block), set(emailtool.NATIVE_LANGUAGE_FIELDS))
        self.assertEqual(block["language_code"], "en")
        self.assertIs(block["is_default"], True)

    def test_capture_artifact_is_the_exact_pinned_request(self) -> None:
        captured = json.loads(
            emailtool.NATIVE_SAVE_ARTIFACT.read_text(encoding="utf-8"))
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["scheme"], "https")
        self.assertEqual(captured["host"], "books.zohocloud.ca")
        self.assertEqual(captured["path"], "/api/v3/settings/emailtemplates")
        self.assertEqual(captured["query"], "")
        self.assertEqual(captured["post_data_sha256"],
                         emailtool.NATIVE_SAVE_BODY_SHA256)

    def test_capture_with_a_drifted_body_or_route_is_refused(self) -> None:
        good = json.loads(emailtool.NATIVE_SAVE_ARTIFACT.read_text(encoding="utf-8"))
        for change in ({"method": "PUT"}, {"method": "GET"}, {"scheme": "http"},
                       {"host": "evil.example.com"}, {"host": "books.zoho.com"},
                       {"path": "/api/v3/contacts"},
                       {"path": "/api/v3/settings/emailtemplates/14"},
                       {"query": "organization_id=1"},
                       {"post_data": good["post_data"] + "&extra=1"}):
            with self.subTest(change=change):
                drifted = dict(good)
                drifted.update(change)
                path = self.root / "drifted_capture.json"
                path.write_text(json.dumps(drifted), encoding="utf-8")
                with mock.patch.object(emailtool, "NATIVE_SAVE_ARTIFACT", path):
                    with self.assertRaises(emailtool.EmailTemplateError):
                        emailtool.captured_native_payload()

    def native_body(self, **payload_changes) -> str:
        payload = emailtool.captured_native_payload()
        payload.update(payload_changes)
        return urlencode({"JSONString": json.dumps(payload),
                          "organization_id": FAKE_ORG})

    def test_native_payload_schema_is_closed(self) -> None:
        emailtool.parse_native_save_body(self.native_body())
        extra = emailtool.captured_native_payload()
        extra["surprise"] = 1
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "closed schema"):
            emailtool.parse_native_save_body(self.native_body(**extra))
        missing = emailtool.captured_native_payload()
        missing.pop("cc_mail_ids")
        body = urlencode({"JSONString": json.dumps(missing),
                          "organization_id": FAKE_ORG})
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "closed schema"):
            emailtool.parse_native_save_body(body)

    def test_native_payload_rejects_every_dangerous_drift(self) -> None:
        block = emailtool.captured_native_payload()["language_content"][0]
        for change in (
            {"type": "estimate_notification"},
            {"is_default": True},
            {"bcc_mail_ids": ["hidden@frpdepots.com"]},
            {"cc_mail_ids": []},
            {"cc_mail_ids": ["attacker@example.com"]},
            {"cc_mail_ids": ["info@frpdepots.com"]},
            {"cc_mail_ids": ["sales@frpdepots.com"]},
            {"cc_mail_ids": ["accounting@frpdepots.com", "attacker@example.com"]},
            {"language_content": []},
            {"language_content": [block, block]},
            {"language_content": [{**block, "language_code": "fr"}]},
            {"language_content": [{**block, "is_default": False}]},
            {"language_content": [{**block, "body": ""}]},
            {"language_content": [{**block, "extra": 1}]},
        ):
            with self.subTest(change=change):
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.parse_native_save_body(self.native_body(**change))

    def test_native_form_body_must_be_exactly_two_fields(self) -> None:
        payload = json.dumps(emailtool.captured_native_payload())
        for body in (
            urlencode({"organization_id": FAKE_ORG, "JSONString": payload}),
            urlencode({"JSONString": payload}),
            urlencode({"JSONString": payload, "organization_id": FAKE_ORG,
                       "extra": "1"}),
            urlencode({"JSONString": payload, "organization_id": "0"}),
            "", None, 42,
        ):
            with self.subTest(body=str(body)[:40]):
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.parse_native_save_body(body)

    def test_expected_payload_is_built_from_the_live_source(self) -> None:
        clone = emailtool.require_clean_source(self.source)
        expected = emailtool.expected_native_payload(
            "CC - All", emailtool.TARGET_TEMPLATES["CC - All"], clone)
        self.assertEqual(set(expected), set(emailtool.NATIVE_PAYLOAD_FIELDS))
        self.assertEqual(expected["name"], "CC - All")
        self.assertEqual(expected["cc_mail_ids"], [
            "logistics@frpdepots.com", "accounting@frpdepots.com",
            "operations@frpdepots.com"])
        self.assertEqual(expected["bcc_mail_ids"], [])
        self.assertIs(expected["is_default"], False)
        # Content comes from the live Default, never from the capture.
        self.assertEqual(expected["language_content"][0]["body"], self.source["body"])
        self.assertEqual(expected["language_content"][0]["subject"],
                         self.source["subject"])
        self.assertEqual(expected["from_address_id"], self.source["from_address_id"])

    def test_expected_payload_refuses_arbitrary_targets(self) -> None:
        clone = emailtool.require_clean_source(self.source)
        for name, cc in (("CC - Sales", ("accounting@frpdepots.com",)),
                         ("Default", ("accounting@frpdepots.com",)),
                         ("CC - Accounting", ("info@frpdepots.com",)),
                         ("CC - All", ("accounting@frpdepots.com",))):
            with self.subTest(name=name):
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.expected_native_payload(name, cc, clone)

    # -- only the exact dropdown options are reachable ----------------------
    def test_cc_options_are_exactly_the_three_fixed_ones(self) -> None:
        self.assertEqual(emailtool.CC_OPTION_TEXT, {
            "accounting@frpdepots.com": "FRP Depots Accounting<accounting@frpdepots.com>",
            "logistics@frpdepots.com": "FRP Depots Logistics<logistics@frpdepots.com>",
            "operations@frpdepots.com": "Douhaa ABZ<operations@frpdepots.com>",
        })
        self.assertEqual(set(emailtool.CC_OPTION_TEXT), set(emailtool.ALLOWED_ADDRESSES))
        for never in emailtool.CC_OPTIONS_NEVER_SELECTABLE:
            self.assertNotIn(never, emailtool.CC_OPTION_TEXT.values())

    def test_no_typed_email_enter_or_comma_cc_fallback_exists(self) -> None:
        source = inspect.getsource(emailtool)
        for token in ('press("Enter")', "press('Enter')", '.type(', 'keyboard.',
                      'insert_text', "','.join(cc", '", ".join(cc'):
            with self.subTest(token=token):
                self.assertNotIn(token, source)
        # The only recorded way in is the row's own dropdown toggler.
        self.assertIn(emailtool.CC_DROPDOWN_TOGGLER, source)
        self.assertEqual(emailtool.CC_DROPDOWN_TOGGLER, ".zf-ac-toggler")

    # -- the captured native CLONE Save contract ---------------------------
    def test_clone_capture_artifact_is_the_exact_pinned_request(self) -> None:
        captured = json.loads(
            emailtool.CLONE_SAVE_ARTIFACT.read_text(encoding="utf-8"))
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["scheme"], "https")
        self.assertEqual(captured["host"], "books.zohocloud.ca")
        self.assertEqual(captured["path"], "/api/v3/settings/emailtemplates")
        self.assertEqual(captured["query"], "")
        self.assertEqual(captured["post_data_sha256"],
                         emailtool.CLONE_SAVE_BODY_SHA256)
        form = dict(parse_qsl(captured["post_data"], keep_blank_values=True,
                              strict_parsing=True))
        self.assertEqual(list(form), ["JSONString", "organization_id"])
        self.assertEqual(form["organization_id"], LIVE_CAPTURE_ORG)

    def test_clone_capture_parses_to_the_exact_flat_eight_key_payload(self) -> None:
        payload = emailtool.captured_clone_payload()
        self.assertEqual(sorted(payload), [
            "bcc_mail_ids", "body", "cc_mail_ids", "from_address_id",
            "is_default", "name", "subject", "type"])
        self.assertEqual(set(payload), set(emailtool.CLONE_PAYLOAD_FIELDS))
        # The Clone schema is FLAT: New's nested block must not appear.
        self.assertNotIn("language_content", payload)
        self.assertEqual(payload["name"], "CC - Accounting")
        self.assertEqual(payload["type"], "invoice_notification")
        self.assertEqual(payload["cc_mail_ids"], ["accounting@frpdepots.com"])
        self.assertEqual(payload["bcc_mail_ids"], [])
        self.assertIs(payload["is_default"], False)
        self.assertEqual(
            hashlib.sha256(payload["body"].encode("utf-8")).hexdigest(),
            emailtool.CLONE_POSTED_BODY_SHA256)

    def test_clone_capture_evidence_records_the_blocked_capture(self) -> None:
        evidence = json.loads(
            emailtool.CLONE_SAVE_EVIDENCE_ARTIFACT.read_text(encoding="utf-8"))
        self.assertIs(evidence["request_aborted_before_network"], True)
        self.assertIs(evidence["business_write_performed"], False)
        self.assertIs(evidence["email_sent"], False)
        self.assertIs(evidence["credentials_headers_cookies_or_storage_read"], False)
        self.assertEqual(evidence["captured_template_request_count"], 1)
        self.assertEqual(evidence["clicks"], [
            "Default row disclosure toggle", "Clone",
            "Save (request intercepted and aborted)"])
        fidelity = json.loads(
            emailtool.CLONE_FIDELITY_ARTIFACT.read_text(encoding="utf-8"))
        self.assertIs(fidelity["body_byte_equal"], False)
        self.assertIs(fidelity["body_canonical_html_equal"], True)
        self.assertEqual(fidelity["canonical_source_events"],
                         emailtool.CLONE_CANONICAL_EVENT_COUNT)
        self.assertEqual(fidelity["canonical_posted_events"],
                         emailtool.CLONE_CANONICAL_EVENT_COUNT)
        self.assertEqual(fidelity["live_template_names_after_capture"], ["Default"])

    def test_clone_capture_with_a_drifted_body_or_route_is_refused(self) -> None:
        good = json.loads(emailtool.CLONE_SAVE_ARTIFACT.read_text(encoding="utf-8"))
        for change in ({"method": "PUT"}, {"method": "GET"}, {"scheme": "http"},
                       {"host": "evil.example.com"}, {"host": "books.zoho.com"},
                       {"path": "/api/v3/contacts"},
                       {"path": "/api/v3/settings/emailtemplates/14"},
                       {"query": "organization_id=1"},
                       {"post_data": good["post_data"] + "&extra=1"}):
            with self.subTest(change=change):
                drifted = dict(good)
                drifted.update(change)
                path = self.root / "drifted_clone_capture.json"
                path.write_text(json.dumps(drifted), encoding="utf-8")
                with mock.patch.object(emailtool, "CLONE_SAVE_ARTIFACT", path):
                    with self.assertRaises(emailtool.EmailTemplateError):
                        emailtool.captured_clone_payload()

    def clone_body(self, org=FAKE_ORG, **payload_changes) -> str:
        payload = emailtool.captured_clone_payload()
        payload.update(payload_changes)
        return urlencode({"JSONString": json.dumps(payload), "organization_id": org})

    def test_clone_payload_schema_is_closed(self) -> None:
        emailtool.parse_clone_save_body(self.clone_body(), FAKE_ORG)
        extra = emailtool.captured_clone_payload()
        extra["surprise"] = 1
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "closed schema"):
            emailtool.parse_clone_save_body(self.clone_body(**extra), FAKE_ORG)
        missing = emailtool.captured_clone_payload()
        missing.pop("cc_mail_ids")
        body = urlencode({"JSONString": json.dumps(missing),
                          "organization_id": FAKE_ORG})
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "closed schema"):
            emailtool.parse_clone_save_body(body, FAKE_ORG)
        # The New form's nested schema must never be accepted here.
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "closed schema"):
            emailtool.parse_clone_save_body(
                urlencode({"JSONString": json.dumps(emailtool.captured_native_payload()),
                           "organization_id": FAKE_ORG}),
                FAKE_ORG)

    def test_clone_payload_rejects_every_dangerous_drift(self) -> None:
        for change in (
            {"type": "estimate_notification"},
            {"is_default": True},
            {"bcc_mail_ids": ["hidden@frpdepots.com"]},
            {"cc_mail_ids": []},
            {"cc_mail_ids": ["attacker@example.com"]},
            {"cc_mail_ids": ["info@frpdepots.com"]},
            {"cc_mail_ids": ["sales@frpdepots.com"]},
            {"cc_mail_ids": ["accounting@frpdepots.com", "attacker@example.com"]},
            {"name": "CC - Sales"},
            {"name": "Default"},
            {"body": ""},
            {"subject": ""},
        ):
            with self.subTest(change=change):
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.parse_clone_save_body(
                        self.clone_body(**change), FAKE_ORG)

    def test_clone_form_body_must_be_exactly_two_fields_and_the_right_org(self) -> None:
        payload = json.dumps(emailtool.captured_clone_payload())
        for body in (
            urlencode({"organization_id": FAKE_ORG, "JSONString": payload}),
            urlencode({"JSONString": payload}),
            urlencode({"JSONString": payload, "organization_id": FAKE_ORG,
                       "extra": "1"}),
            "", None, 42,
        ):
            with self.subTest(body=str(body)[:40]):
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.parse_clone_save_body(body, FAKE_ORG)
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "different Zoho Books"):
            emailtool.parse_clone_save_body(self.clone_body(org="999999"), FAKE_ORG)

    def test_expected_clone_payload_is_built_from_the_live_source(self) -> None:
        clone = emailtool.require_clean_source(self.source)
        expected = emailtool.expected_clone_payload(
            "CC - All", emailtool.TARGET_TEMPLATES["CC - All"], clone)
        self.assertEqual(set(expected), set(emailtool.CLONE_PAYLOAD_FIELDS))
        self.assertEqual(expected["name"], "CC - All")
        self.assertEqual(expected["cc_mail_ids"], [
            "logistics@frpdepots.com", "accounting@frpdepots.com",
            "operations@frpdepots.com"])
        self.assertEqual(expected["bcc_mail_ids"], [])
        self.assertIs(expected["is_default"], False)
        # Content comes from the live Default, never from the capture.
        self.assertEqual(expected["body"], self.source["body"])
        self.assertEqual(expected["subject"], self.source["subject"])
        self.assertEqual(expected["from_address_id"], self.source["from_address_id"])

    def test_expected_clone_payload_refuses_arbitrary_targets(self) -> None:
        clone = emailtool.require_clean_source(self.source)
        for name, cc in (("CC - Sales", ("accounting@frpdepots.com",)),
                         ("Default", ("accounting@frpdepots.com",)),
                         ("CC - Accounting", ("info@frpdepots.com",)),
                         ("CC - All", ("accounting@frpdepots.com",))):
            with self.subTest(name=name):
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.expected_clone_payload(name, cc, clone)

    # -- the accepted narrow equivalence, and its limits --------------------
    def test_the_real_pair_differs_by_attribute_order_alone(self) -> None:
        posted = emailtool.captured_clone_payload()["body"]
        source = self.source["body"]
        self.assertEqual(source, posted)  # the fixture IS the captured body
        events = emailtool.canonical_html_events(posted, "posted")
        self.assertEqual(len(events), emailtool.CLONE_CANONICAL_EVENT_COUNT)
        # Reordering the attributes of the PAY NOW anchor is accepted...
        reordered = posted.replace(
            '<a href="%PaymentLink%" style="text-decoration: none;">',
            '<a style="text-decoration: none;" href="%PaymentLink%">')
        self.assertNotEqual(reordered, posted)
        self.assertTrue(emailtool.same_canonical_html(posted, reordered, "reordered"))
        self.assertEqual(
            len(emailtool.canonical_html_events(reordered, "reordered")),
            emailtool.CLONE_CANONICAL_EVENT_COUNT)

    def test_every_meaningful_html_change_is_rejected(self) -> None:
        posted = emailtool.captured_clone_payload()["body"]
        for label, changed in (
            ("changed link", posted.replace("%PaymentLink%", "https://evil.example.com")),
            ("changed text", posted.replace("PAY NOW", "PAY LATER")),
            ("changed placeholder", posted.replace("%Total%", "%Balance%")),
            ("changed signature", posted.replace("Accounting Departement", "Someone Else")),
            ("changed attribute name", posted.replace(
                '<a href="%PaymentLink%"', '<a data-href="%PaymentLink%"')),
            ("changed class attribute", posted.replace(
                'class="highlight"', 'class="highlighted"')),
            ("changed attribute value", posted.replace(
                "background-color:rgb(77, 207, 89)", "background-color:rgb(0, 0, 0)")),
            ("changed tag", posted.replace("<h4 ", "<h5 ", 1).replace("</h4>", "</h5>", 1)),
            ("dropped element", posted.replace("<br>", "", 1)),
            ("added whitespace data node", posted.replace("</div></div>", "</div> </div>", 1)),
            ("added comment", posted.replace("<div>", "<!-- x --><div>", 1)),
            ("added declaration", "<!DOCTYPE html>" + posted),
            ("entity instead of text", posted.replace("PAY NOW", "PAY&nbsp;NOW")),
        ):
            with self.subTest(label=label):
                self.assertNotEqual(changed, posted, label)
                self.assertFalse(
                    emailtool.same_canonical_html(posted, changed, label), label)
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.require_same_canonical_html(posted, changed, label)

    def test_nesting_changes_are_rejected(self) -> None:
        outer = "<div><span>a</span><b>c</b></div>"
        inner = "<div><span>a<b>c</b></span></div>"
        self.assertFalse(emailtool.same_canonical_html(outer, inner, "nesting"))

    def test_entities_comments_and_declarations_are_compared_exactly(self) -> None:
        for left, right in (
            ("<p>&amp;</p>", "<p>&#38;</p>"),
            ("<p>&amp;</p>", "<p>&</p>"),
            ("<p><!-- one --></p>", "<p><!-- two --></p>"),
            ("<!DOCTYPE html><p>x</p>", "<!DOCTYPE HTML><p>x</p>"),
            ("<p>a b</p>", "<p>a  b</p>"),
            ("<p>a</p>", "<p>a </p>"),
        ):
            with self.subTest(left=left, right=right):
                self.assertFalse(emailtool.same_canonical_html(left, right, "exactness"))

    def test_attribute_quoting_and_case_are_preserved(self) -> None:
        self.assertFalse(emailtool.same_canonical_html(
            '<div class="a"></div>', "<div class='a'></div>", "quoting"))
        self.assertFalse(emailtool.same_canonical_html(
            "<DIV></DIV>", "<div></div>", "tag case"))
        self.assertFalse(emailtool.same_canonical_html(
            '<div CLASS="a"></div>', '<div class="a"></div>', "attribute case"))

    def test_duplicate_attributes_are_refused_not_collapsed(self) -> None:
        for markup in ('<div class="a" class="b"></div>',
                       '<div class="a" CLASS="b"></div>'):
            with self.subTest(markup=markup):
                with self.assertRaisesRegex(emailtool.EmailTemplateError, "duplicate"):
                    emailtool.canonical_html_events(markup, "duplicate")
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.same_canonical_html(markup, markup, "duplicate")

    def test_malformed_or_unclosed_html_is_refused(self) -> None:
        for markup in ("<div><span></div></span>", "<div><span>text</div>",
                       "<div>", "</div>", "<br></br>", "<div><p>x</div>"):
            with self.subTest(markup=markup):
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.canonical_html_events(markup, "malformed")
        with self.assertRaises(emailtool.EmailTemplateError):
            emailtool.canonical_html_events(None, "not text")

    def test_void_elements_and_self_closing_forms_are_distinguished(self) -> None:
        emailtool.canonical_html_events("<div><br><img src='x'></div>", "void")
        self.assertFalse(emailtool.same_canonical_html(
            "<div><br></div>", "<div><br/></div>", "self-closing"))

    # -- the create workflow is commissioned, and re-proven every commit ----
    def test_clone_workflow_is_commissioned_and_new_is_negative_evidence(self) -> None:
        self.assertIs(emailtool.CLONE_SAVE_CONTRACT_CAPTURED, True)
        self.assertIs(emailtool.NATIVE_SAVE_CONTRACT_CAPTURED, True)
        self.assertIs(emailtool.CREATE_WORKFLOW_COMMISSIONED, True)
        clone = emailtool.require_clean_source(self.source)
        fidelity = emailtool.require_create_contract_commissioned(clone)
        self.assertIs(fidelity["body_canonical_html_equal"], True)
        self.assertEqual(fidelity["canonical_events"],
                         emailtool.CLONE_CANONICAL_EVENT_COUNT)
        self.assertIn("YES on 2026-08-11", fidelity["accepted_equivalence"])
        evidence = emailtool.new_form_negative_evidence(clone)
        self.assertIs(evidence["new_form_body_matches_live"], False)
        self.assertIs(evidence["new_form_is_used_for_creation"], False)

    def test_commissioning_flag_off_refuses_before_any_side_effect(self) -> None:
        clone = emailtool.require_clean_source(self.source)
        with mock.patch.object(emailtool, "CREATE_WORKFLOW_COMMISSIONED", False):
            with self.assertRaisesRegex(emailtool.EmailTemplateError, "NOT COMMISSIONED"):
                emailtool.require_create_contract_commissioned(clone)
            plan = self.stage()
            with self.assertRaisesRegex(emailtool.EmailTemplateError, "NOT COMMISSIONED"):
                emailtool.command_commit(self.commit_args(plan))
            self.assertEqual(self.locks(), [])

    def test_drifted_live_default_refuses_before_the_lock(self) -> None:
        """A Default the captured Clone form would not reproduce is refused."""
        plan = self.stage()
        for change in ({"body": "<div>rewritten</div>"},
                       {"subject": "Rewritten subject"},
                       {"from_address_id": "12345"}):
            with self.subTest(change=change):
                clone = emailtool.require_clean_source(default_detail(**change))
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.require_create_contract_commissioned(clone)
        self.assertEqual(self.locks(), [], "no lock is written by the gate")
        self.assertTrue(plan.exists())

    def test_stage_records_the_clone_mechanism_and_accepted_equivalence(self) -> None:
        plan = json.loads(self.stage().read_text(encoding="utf-8"))
        workflow = plan["workflow"]
        self.assertIs(workflow["create_workflow_commissioned"], True)
        self.assertIsNone(workflow["create_blocker"])
        self.assertIs(workflow["clone_save_contract_captured"], True)
        self.assertEqual(workflow["clone_save_body_sha256"],
                         emailtool.CLONE_SAVE_BODY_SHA256)
        self.assertIn("native Clone control", workflow["create_mechanism"])
        self.assertIn("YES on 2026-08-11", workflow["accepted_equivalence"])
        self.assertIn("Show dropdown menu", " ".join(workflow["clicks"]))
        self.assertIn("Clone", " ".join(workflow["clicks"]))
        self.assertEqual(workflow["never_clicked"], list(emailtool.NEVER_CLICKED))
        self.assertIs(
            workflow["new_form_negative_evidence"]["new_form_is_used_for_creation"], False)
        self.assertIn(emailtool.CLONE_FIDELITY_RISK, plan["risks"])
        self.assertIn("ACCEPTED BY RACHAD", emailtool.CLONE_FIDELITY_RISK)

    def test_old_schema_plans_are_incompatible(self) -> None:
        """EXTENDED, not weakened, when create_all_only was commissioned.

        Schema 3 / v2.0.0 knew only two actions. A plan staged under that build
        must not be commit-able against the three-action set, so both numbers
        move together and every earlier value is refused.
        """
        self.assertEqual(emailtool.SCHEMA_VERSION, 4)
        # 2.2.0 commissioned the DELETE path, so any plan staged while the
        # tool could only refuse fails closed instead of silently becoming
        # executable.
        self.assertEqual(emailtool.TOOL_VERSION, "2.2.0")
        plan = self.stage()
        for change in ({"schema_version": 3}, {"schema_version": 2},
                       {"schema_version": 1}, {"tool_version": "2.0.0"},
                       {"tool_version": "1.1.0"}):
            with self.subTest(change=change):
                self.rewrite(plan, **change)
                with self.assertRaisesRegex(
                    emailtool.EmailTemplateError, "schema version|tool version"
                ):
                    emailtool.command_commit(self.commit_args(plan))

    # -- the Save interceptor ----------------------------------------------
    def interceptor(self, name="CC - Accounting"):
        clone = emailtool.require_clean_source(self.source)
        expected = emailtool.expected_clone_payload(
            name, emailtool.TARGET_TEMPLATES[name], clone)
        state: dict = {"seen": 0, "allowed": False, "failure": "", "blocked": []}
        return emailtool.clone_save_interceptor(FAKE_ORG, expected, state), state

    def save_request(self, **payload_changes):
        clone = emailtool.require_clean_source(self.source)
        payload = emailtool.expected_clone_payload(
            "CC - Accounting", emailtool.TARGET_TEMPLATES["CC - Accounting"], clone)
        payload.update(payload_changes)
        return FakeRequest(
            "POST",
            f"https://{emailtool.UI_HOST}{emailtool.CLONE_SAVE_PATH}",
            urlencode({"JSONString": json.dumps(payload),
                       "organization_id": FAKE_ORG}),
        )

    def test_interceptor_releases_exactly_one_validated_post(self) -> None:
        intercept, state = self.interceptor()
        route = FakeRoute(state)
        intercept(route, self.save_request())
        self.assertEqual(route.actions, ["continue"])
        self.assertIs(state["allowed"], True)
        # Validation completed BEFORE the request was released.
        self.assertEqual(route.validated_when_continued, [(True, True)])
        # A second Save is aborted and recorded as a failure.
        second = FakeRoute(state)
        intercept(second, self.save_request())
        self.assertEqual(second.actions, [("abort", "blockedbyclient")])
        self.assertIn("more than one", state["failure"])

    def test_interceptor_lets_reads_through_and_aborts_everything_else(self) -> None:
        intercept, state = self.interceptor()
        for method in ("GET", "HEAD"):
            route = FakeRoute(state)
            intercept(route, FakeRequest(method, "https://books.zohocloud.ca/anything"))
            self.assertEqual(route.actions, ["continue"])
        for method, url in (
            ("POST", "https://books.zohocloud.ca/_chat/useractive.do"),
            ("POST", "https://logsapi.zohocloud.ca/csplog"),
            ("POST", "https://books.zohocloud.ca/api/v3/contacts"),
            ("POST", "https://books.zohocloud.ca/api/v3/settings/emailtemplates/14"),
            ("POST", "https://books.zohocloud.ca/api/v3/settings/emailtemplates?x=1"),
            ("POST", "http://books.zohocloud.ca/api/v3/settings/emailtemplates"),
            ("POST", "https://evil.example.com/api/v3/settings/emailtemplates"),
            ("PUT", "https://books.zohocloud.ca/api/v3/settings/emailtemplates"),
            ("DELETE", "https://books.zohocloud.ca/api/v3/settings/emailtemplates"),
            ("PATCH", "https://books.zohocloud.ca/api/v3/settings/emailtemplates"),
        ):
            with self.subTest(method=method, url=url):
                route = FakeRoute(state)
                intercept(route, FakeRequest(method, url, "JSONString=%7B%7D"))
                self.assertEqual(route.actions, [("abort", "blockedbyclient")])
        self.assertIs(state["allowed"], False)
        self.assertEqual(state["seen"], 0)

    def test_interceptor_aborts_every_payload_drift_before_the_network(self) -> None:
        for change in (
            {"name": "CC - Sales"},
            {"name": "Default"},
            {"type": "estimate_notification"},
            {"is_default": True},
            {"bcc_mail_ids": ["hidden@frpdepots.com"]},
            {"cc_mail_ids": ["logistics@frpdepots.com"]},
            {"cc_mail_ids": ["accounting@frpdepots.com", "info@frpdepots.com"]},
            {"from_address_id": "9999"},
            {"subject": "Rewritten subject"},
            {"body": REAL_BODY.replace("PAY NOW", "PAY LATER")},
            {"body": REAL_BODY.replace("%PaymentLink%", "https://evil.example.com")},
        ):
            with self.subTest(change=change):
                intercept, state = self.interceptor()
                route = FakeRoute(state)
                intercept(route, self.save_request(**change))
                self.assertEqual(route.actions, [("abort", "blockedbyclient")])
                self.assertIs(state["allowed"], False)
                self.assertTrue(state["failure"])

    def test_interceptor_accepts_only_attribute_reordering_in_the_body(self) -> None:
        reordered = REAL_BODY.replace(
            '<a href="%PaymentLink%" style="text-decoration: none;">',
            '<a style="text-decoration: none;" href="%PaymentLink%">')
        self.assertNotEqual(reordered, REAL_BODY)
        intercept, state = self.interceptor()
        route = FakeRoute(state)
        intercept(route, self.save_request(body=reordered))
        self.assertEqual(route.actions, ["continue"])
        self.assertIs(state["allowed"], True)

    def test_interceptor_enforces_cc_all_order(self) -> None:
        clone = emailtool.require_clean_source(self.source)
        expected = emailtool.expected_clone_payload(
            "CC - All", emailtool.TARGET_TEMPLATES["CC - All"], clone)
        state: dict = {"seen": 0, "allowed": False, "failure": "", "blocked": []}
        intercept = emailtool.clone_save_interceptor(FAKE_ORG, expected, state)
        wrong = dict(expected)
        wrong["cc_mail_ids"] = ["accounting@frpdepots.com", "logistics@frpdepots.com",
                                "operations@frpdepots.com"]
        route = FakeRoute(state)
        intercept(route, FakeRequest(
            "POST", f"https://{emailtool.UI_HOST}{emailtool.CLONE_SAVE_PATH}",
            urlencode({"JSONString": json.dumps(wrong),
                       "organization_id": FAKE_ORG})))
        self.assertEqual(route.actions, [("abort", "blockedbyclient")])
        self.assertIs(state["allowed"], False)

        # A fresh attempt (a new commit, a new plan) accepts the exact order.
        fresh: dict = {"seen": 0, "allowed": False, "failure": "", "blocked": []}
        good = FakeRoute(fresh)
        emailtool.clone_save_interceptor(FAKE_ORG, expected, fresh)(
            good, FakeRequest(
                "POST", f"https://{emailtool.UI_HOST}{emailtool.CLONE_SAVE_PATH}",
                urlencode({"JSONString": json.dumps(expected),
                           "organization_id": FAKE_ORG})))
        self.assertEqual(good.actions, ["continue"])
        self.assertEqual(fresh["payload"]["cc_mail_ids"], [
            "logistics@frpdepots.com", "accounting@frpdepots.com",
            "operations@frpdepots.com"])

    def test_interceptor_never_reads_request_headers(self) -> None:
        intercept, state = self.interceptor()
        request = self.save_request()
        with self.assertRaises(AssertionError):
            request.headers  # the fake proves any such read would be caught
        intercept(FakeRoute(state), request)
        self.assertIs(state["allowed"], True)

    # -- the live Clone workflow, driven against a recording fake page ------
    def fake_page(self, name="CC - Accounting", **changes) -> FakePage:
        return FakePage(
            self, name,
            [emailtool.CC_OPTION_TEXT[a] for a in emailtool.TARGET_TEMPLATES[name]],
            **changes,
        )

    def run_clone(self, page: FakePage, name="CC - Accounting") -> str:
        import playwright.sync_api as pwapi

        with mock.patch.object(pwapi, "sync_playwright", lambda: FakePlaywright(page)):
            return emailtool.create_template_via_ui(
                FAKE_ORG, DEFAULT_ID, name,
                emailtool.TARGET_TEMPLATES[name],
                emailtool.require_clean_source(self.source),
            )

    def test_clone_path_clicks_disclosure_then_clone_and_never_new_or_edit(self) -> None:
        page = self.fake_page()
        self.assertEqual(self.run_clone(page), "600")
        # The disclosure is taken from INSIDE the Default row. A page-wide
        # lookup resolved to several buttons the moment a second template
        # existed, which broke cloning outright on 2026-08-11.
        source_row = (
            f"sel:tr|text:"
            f"{emailtool.name_boundary(emailtool.SOURCE_TEMPLATE_NAME).pattern}"
        )
        self.assertEqual(page.clicks, [
            f"{source_row}>role:button:{emailtool.ROW_DISCLOSURE_LABEL}",
            f"sel:{emailtool.MENU_ITEM_SELECTOR}|text:^{emailtool.CLONE_MENU_ITEM}$",
            f"sel:div.form-group.row|has:sel:label|text:^{emailtool.CC_ROW_LABEL}$"
            f">{emailtool.CC_DROPDOWN_TOGGLER}",
            f"role:option:{emailtool.CC_OPTION_TEXT[emailtool.ACCOUNTING]}",
            f"role:button:{emailtool.SAVE_BUTTON_LABEL}",
        ])
        joined = " ".join(page.clicks)
        for banned in emailtool.NEVER_CLICKED:
            with self.subTest(banned=banned):
                self.assertNotIn(f":{banned}", joined)
                self.assertNotIn(f"^{banned}$", joined)
        self.assertEqual(page.fills, [
            (f"sel:{emailtool.NAME_INPUT_SELECTOR}", "CC - Accounting")])
        # The shared page is handed back on the harmless list route.
        self.assertEqual(page.navigations[0], emailtool.SETTINGS_URL)
        self.assertEqual(page.navigations[-1], emailtool.SETTINGS_URL)
        self.assertTrue(page.unrouted)

    def test_accounting_clone_attempts_exactly_one_validated_post(self) -> None:
        page = self.fake_page()
        self.run_clone(page)
        self.assertEqual(page.save_attempts, 1)
        state = page.routes[-1][1]
        self.assertEqual(state["seen"], 1)
        self.assertIs(state["allowed"], True)
        self.assertEqual(state["payload"]["name"], "CC - Accounting")
        self.assertEqual(state["payload"]["cc_mail_ids"], ["accounting@frpdepots.com"])

    def test_cc_all_selects_exactly_the_three_fixed_options_in_order(self) -> None:
        page = self.fake_page("CC - All")
        self.assertEqual(self.run_clone(page, "CC - All"), "600")
        options = [click.split(":", 2)[2] for click in page.clicks
                   if click.startswith("role:option:")]
        self.assertEqual(options, [
            "FRP Depots Logistics<logistics@frpdepots.com>",
            "FRP Depots Accounting<accounting@frpdepots.com>",
            "Douhaa ABZ<operations@frpdepots.com>",
        ])
        # One toggler open per address, and no other way in.
        togglers = [c for c in page.clicks if c.endswith(emailtool.CC_DROPDOWN_TOGGLER)]
        self.assertEqual(len(togglers), 3)
        self.assertEqual(page.routes[-1][1]["payload"]["cc_mail_ids"], [
            "logistics@frpdepots.com", "accounting@frpdepots.com",
            "operations@frpdepots.com"])

    def test_a_drifted_save_payload_is_aborted_and_the_clone_fails(self) -> None:
        clone = emailtool.require_clean_source(self.source)
        drifted = emailtool.expected_clone_payload(
            "CC - Accounting", ("accounting@frpdepots.com",), clone)
        drifted["cc_mail_ids"] = ["info@frpdepots.com"]
        page = self.fake_page(save_payload=urlencode({
            "JSONString": json.dumps(drifted), "organization_id": FAKE_ORG}))
        with self.assertRaises(emailtool.EmailTemplateError):
            self.run_clone(page)
        self.assertIs(page.routes[-1][1]["allowed"], False)
        self.assertIsNone(page.last_response)
        self.assertNotIn("600", self.zoho.details)

    def test_a_second_save_request_is_aborted(self) -> None:
        page = self.fake_page(emit_extra_save=True)
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "more than one"):
            self.run_clone(page)
        self.assertEqual(page.routes[-1][1]["seen"], 2)

    def test_non_2xx_or_error_coded_save_is_a_failure(self) -> None:
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "HTTP 400"):
            self.run_clone(self.fake_page(save_status=400))
        self.zoho.details.pop("600", None)
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "refused the Clone Save"):
            self.run_clone(self.fake_page(
                save_answer={"code": 1001, "message": "duplicate name"}))

    def test_clone_refuses_a_form_that_is_not_the_default_clone(self) -> None:
        page = self.fake_page()
        page.form["name"] = "Something Else"
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "did not inherit"):
            self.run_clone(page)
        self.assertEqual(page.save_attempts, 0)

    def test_clone_refuses_a_form_carrying_cc_bcc_or_default(self) -> None:
        for change, pattern in (
            ({"cc_selected": ["x"]}, "CC is not empty"),
            ({"bcc_selected": ["x"]}, "BCC is not empty"),
            ({"is_default": True}, "marked as the default"),
            ({"from_text": ""}, "did not inherit the source from_text"),
            ({"subject_text": ""}, "did not inherit the source subject_text"),
        ):
            with self.subTest(change=change):
                page = self.fake_page()
                page.form.update(change)
                with self.assertRaisesRegex(emailtool.EmailTemplateError, pattern):
                    self.run_clone(page)
                self.assertEqual(page.save_attempts, 0)

    def test_clone_refuses_when_the_cc_row_does_not_hold_the_approved_list(self) -> None:
        page = self.fake_page()
        original = page.record_click

        def swallow_option(label):
            if label.startswith("role:option:"):
                page.clicks.append(label)
                return
            original(label)

        page.record_click = swallow_option
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "not the approved"):
            self.run_clone(page)
        self.assertEqual(page.save_attempts, 0)

    def test_clone_refuses_a_redirect_off_the_approved_host(self) -> None:
        page = self.fake_page()
        original = page.goto

        def hijack(url, wait_until=None, timeout=None):
            original(url, wait_until, timeout)
            page.url = "https://accounts.zohocloud.ca/signin"

        page.goto = hijack
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "left the approved host"):
            self.run_clone(page)
        self.assertEqual(page.clicks, [])

    def test_clone_refuses_a_form_cloning_a_different_template(self) -> None:
        page = self.fake_page()
        original = page.record_click

        def wrong_form(label):
            original(label)
            if label.endswith(f"|text:^{emailtool.CLONE_MENU_ITEM}$"):
                page.url = (
                    "https://books.zohocloud.ca/app#/settings/emails/templates/edit"
                    "?clone_email_template_id=999999")

        page.record_click = wrong_form
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "not cloning email template"):
            self.run_clone(page)
        self.assertEqual(page.save_attempts, 0)

    def test_clone_reads_back_the_id_from_zoho_not_from_the_answer(self) -> None:
        page = self.fake_page(save_answer={"code": 0, "email_template":
                                           {"email_template_id": "999999"}})
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "different template"):
            self.run_clone(page)

    def test_clone_only_ever_evaluates_the_snapshot_and_the_read(self) -> None:
        page = self.fake_page()
        self.run_clone(page)
        for script in page.evaluations:
            self.assertTrue(
                script == emailtool.FORM_SNAPSHOT_JS or "await fetch(" in script, script)
            for secret in ("cookie", "localStorage", "sessionStorage", "token"):
                self.assertNotIn(secret, script)

    # -- the shared Zoho browser mutex --------------------------------------
    def test_commit_holds_the_zoho_browser_lane(self) -> None:
        self.assertTrue(hasattr(emailtool.command_commit, "__wrapped__"))
        plan = self.stage()
        entered: list = []

        @contextlib.contextmanager
        def fake_lock(lane, *, purpose, **kwargs):
            entered.append((lane, purpose, bool(self.locks())))
            yield

        def creator(org, source_id, name, cc, clone):
            new_id = "600"
            self.zoho.details[new_id] = clone_of(self.source, new_id, name, cc)
            return new_id

        with mock.patch.object(emailtool, "ui_browser_lock", fake_lock):
            self.run_with_create(creator, self.commit_args(plan))
        self.assertEqual(len(entered), 1)
        self.assertEqual(entered[0][0], "zoho")
        # The lane was claimed BEFORE the plan's replay lock existed.
        self.assertIs(entered[0][2], False)

    def test_busy_browser_refuses_for_free_and_leaves_the_plan_reusable(self) -> None:
        plan = self.stage()

        @contextlib.contextmanager
        def busy(lane, *, purpose, **kwargs):
            raise emailtool.UiLaneBusy("the other lane is driving this browser")
            yield  # pragma: no cover

        with mock.patch.object(emailtool, "ui_browser_lock", busy):
            with self.assertRaises(emailtool.UiLaneBusy):
                emailtool.command_commit(self.commit_args(plan))
        self.assertEqual(self.locks(), [], "a busy browser must never lock the plan")

        # And the untouched plan still commits once the lane frees up.
        def creator(org, source_id, name, cc, clone):
            new_id = "600"
            self.zoho.details[new_id] = clone_of(self.source, new_id, name, cc)
            return new_id

        self.run_with_create(creator, self.commit_args(plan))
        self.assertEqual(self.locks()[0]["status"], "committed_verified")

    def test_every_browser_path_takes_the_same_zoho_lane(self) -> None:
        """The READ paths navigate the shared page too, so they hold it as well.

        list-templates and stage do not write, but they drive the same single
        authenticated tab the create path drives. Before this, a stage running on
        one chat lane could navigate the page out from under an in-flight Clone
        form on the other. All three commands now take the SAME named lane.
        """
        for command in (emailtool.command_list_templates, emailtool.command_stage,
                        emailtool.command_commit):
            with self.subTest(command=command.__name__):
                self.assertTrue(hasattr(command, "__wrapped__"), command.__name__)
        lanes: list = []

        @contextlib.contextmanager
        def record(lane, *, purpose, **kwargs):
            lanes.append((lane, purpose))
            yield

        with mock.patch.object(emailtool, "ui_browser_lock", record):
            with mock.patch("sys.stdout"):
                emailtool.command_list_templates(argparse.Namespace())
            self.stage()
        self.assertEqual([lane for lane, _ in lanes], ["zoho", "zoho"])
        self.assertTrue(all(purpose for _, purpose in lanes))

    def test_busy_browser_refuses_reads_before_any_navigation_or_plan(self) -> None:
        @contextlib.contextmanager
        def busy(lane, *, purpose, **kwargs):
            raise emailtool.UiLaneBusy("the other lane is driving this browser")
            yield  # pragma: no cover

        self.execute_get.reset_mock()
        with mock.patch.object(emailtool, "ui_browser_lock", busy):
            with self.assertRaises(emailtool.UiLaneBusy):
                self.stage()
            with self.assertRaises(emailtool.UiLaneBusy):
                with mock.patch("sys.stdout"):
                    emailtool.command_list_templates(argparse.Namespace())
        # No page was navigated and no plan file was written.
        self.execute_get.assert_not_called()
        self.assertEqual(sorted(self.plan_dir.glob("*.json")), [])
        self.assertEqual(self.locks(), [])

    def test_module_scope_patch_stops_a_lane_lock_free_mutation_copy(self) -> None:
        """*** THE GUARD THAT THE 2026-08-11 UNAUTHORIZED WRITE PROVED NECESSARY. ***

        Mutation testing must never edit the live source in place, and it must
        never be the only thing standing between a test and the real browser.
        This copies the module to an isolated temp directory, strips every
        ``@holds_zoho_browser`` decorator there, and proves the copy still cannot
        reach a live session -- because ``sync_playwright`` itself is patched at
        module scope. The real file on disk is not touched.
        """
        import importlib.util
        import re as regex
        import sys

        original_path = Path(emailtool.__file__).resolve()
        original_text = original_path.read_text(encoding="utf-8")
        mutated_text = regex.sub(r"@holds_zoho_browser\(.*\)\n", "", original_text)
        self.assertNotIn("@holds_zoho_browser(", mutated_text)
        self.assertNotEqual(mutated_text, original_text)

        saved_path = list(sys.path)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / "mutated_email_template_tool.py"
                copy.write_text(mutated_text, encoding="utf-8")
                spec = importlib.util.spec_from_file_location(
                    "mutated_email_template_tool", copy)
                mutated = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mutated)
        finally:
            sys.path[:] = saved_path
            sys.modules.pop("mutated_email_template_tool", None)

        # The decorator really is gone from the copy...
        for name in ("command_commit", "command_stage", "command_list_templates"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(getattr(mutated, name), "__wrapped__"), name)
        # ...and the copy still cannot open a real browser.
        with self.assertRaisesRegex(AssertionError, "REAL browser"):
            mutated.create_template_via_ui(
                FAKE_ORG, DEFAULT_ID, "CC - All",
                mutated.TARGET_TEMPLATES["CC - All"],
                mutated.require_clean_source(default_detail()),
            )
        # The read transport converts any session failure into its own honest
        # refusal, so the guard shows up as the cause rather than as the type.
        with self.assertRaises(mutated.EmailTemplateError) as caught:
            mutated._execute_ui_get(
                f"https://{mutated.UI_HOST}{mutated.TEMPLATE_LIST_PATH}?organization_id=1")
        self.assertIsInstance(caught.exception.__cause__, AssertionError)
        self.assertIn("REAL browser", str(caught.exception.__cause__))
        # The live source on disk was not modified by any of this.
        self.assertEqual(original_path.read_text(encoding="utf-8"), original_text)
        self.assertIn("@holds_zoho_browser(", original_text)

    def test_real_lane_lock_excludes_a_second_holder(self) -> None:
        import ui_lane_lock

        self.assertNotEqual(str(ui_lane_lock.LOCK_DIR),
                            str(Path("~").expanduser()), "lock dir must be redirected")
        held = threading.Event()
        release = threading.Event()

        def holder():
            with ui_lane_lock.ui_browser_lock("zoho", purpose="test holder"):
                held.set()
                release.wait(10)

        thread = threading.Thread(target=holder)
        thread.start()
        try:
            self.assertTrue(held.wait(10))
            with self.assertRaises(ui_lane_lock.UiLaneBusy):
                with ui_lane_lock.ui_browser_lock(
                    "zoho", purpose="second lane", wait_seconds=0
                ):
                    pass  # pragma: no cover
        finally:
            release.set()
            thread.join(10)

    # -- lock / no-retry machinery (exercised through the create seam) -----
    def enable_create(self, side_effect):
        # ONLY the browser interaction is replaced. Every gate -- approval, plan
        # validation, the pinned Clone contract and the canonical body check --
        # runs for real in these tests, against the real captured HTML.
        return [
            mock.patch.object(emailtool, "create_template_via_ui", side_effect=side_effect),
        ]

    def run_with_create(self, side_effect, args):
        patchers = self.enable_create(side_effect)
        for patcher in patchers:
            patcher.start()
        try:
            with mock.patch("sys.stdout"):
                emailtool.command_commit(args)
        finally:
            for patcher in reversed(patchers):
                patcher.stop()

    def locks(self) -> list[dict]:
        folder = self.plan_dir / ".commit-locks"
        return [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(folder.glob("*.json"))] if folder.exists() else []

    def test_lock_exists_before_the_first_side_effect(self) -> None:
        plan = self.stage()
        seen = {}

        def creator(org, source_id, name, cc, clone):
            seen["locked"] = bool(self.locks())
            new_id = "600"
            self.zoho.details[new_id] = clone_of(self.source, new_id, name, cc)
            return new_id

        self.run_with_create(creator, self.commit_args(plan))
        self.assertTrue(seen["locked"], "the replay lock must exist before creation")

    def test_successful_commit_locks_and_verifies(self) -> None:
        plan = self.stage()

        def creator(org, source_id, name, cc, clone):
            new_id = "600"
            self.zoho.details[new_id] = clone_of(self.source, new_id, name, cc)
            return new_id

        self.run_with_create(creator, self.commit_args(plan))
        lock = self.locks()[0]
        self.assertEqual(lock["status"], "committed_verified")
        self.assertEqual(lock["created_template_ids"], {"CC - Accounting": "600"})
        self.assertIs(lock["email_sent"], False)
        self.assertIs(lock["no_retry"], True)

    def test_committed_plan_cannot_be_replayed(self) -> None:
        plan = self.stage()

        def creator(org, source_id, name, cc, clone):
            new_id = "600"
            self.zoho.details[new_id] = clone_of(self.source, new_id, name, cc)
            return new_id

        self.run_with_create(creator, self.commit_args(plan))
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "already exists"):
            self.run_with_create(creator, self.commit_args(plan))

    def test_no_retry_after_a_failed_attempt(self) -> None:
        plan = self.stage()
        calls = []

        def creator(org, source_id, name, cc, clone):
            calls.append(name)
            raise emailtool.EmailTemplateError("Zoho Save failed")

        with self.assertRaisesRegex(emailtool.EmailTemplateError, "indeterminate"):
            self.run_with_create(creator, self.commit_args(plan))
        self.assertEqual(calls, ["CC - Accounting"])
        lock = self.locks()[0]
        self.assertEqual(lock["status"], "indeterminate")
        self.assertIs(lock["no_retry"], True)

    def test_locked_plan_cannot_be_committed_again(self) -> None:
        plan = self.stage()

        def creator(org, source_id, name, cc, clone):
            raise emailtool.EmailTemplateError("Zoho Save failed")

        with self.assertRaises(emailtool.EmailTemplateError):
            self.run_with_create(creator, self.commit_args(plan))
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "already entered commit"):
            self.run_with_create(creator, self.commit_args(plan))

    def test_partial_multi_template_failure_reports_exactly_what_happened(self) -> None:
        self.add_accounting()
        plan = self.stage(emailtool.CREATE_REMAINING, self.confirmation())
        created = []

        def creator(org, source_id, name, cc, clone):
            if name == "CC - Operations":
                raise emailtool.EmailTemplateError("Zoho Save failed")
            created.append(name)
            new_id = str(700 + len(created))
            self.zoho.details[new_id] = clone_of(self.source, new_id, name, cc)
            return new_id

        with self.assertRaises(emailtool.EmailTemplateError) as caught:
            self.run_with_create(
                creator, self.commit_args(plan, action=emailtool.CREATE_REMAINING))
        message = str(caught.exception)
        self.assertIn("partial", message)
        self.assertIn("never atomic", message)
        self.assertIn("CC - Logistics", message)
        self.assertIn("CC - All", message)
        lock = self.locks()[0]
        self.assertEqual(lock["status"], "partial")
        self.assertEqual(list(lock["created_template_ids"]), ["CC - Logistics"])
        self.assertEqual(lock["write_in_flight_template"], "CC - Operations")
        self.assertEqual(lock["not_attempted"], ["CC - All"])
        self.assertIs(lock["no_retry"], True)

    def test_readback_failure_locks_the_plan(self) -> None:
        plan = self.stage()

        def creator(org, source_id, name, cc, clone):
            new_id = "600"
            wrong = clone_of(self.source, new_id, name, cc)
            wrong["cc_mail_ids"] = ["logistics@frpdepots.com"]
            self.zoho.details[new_id] = wrong
            return new_id

        with self.assertRaisesRegex(emailtool.EmailTemplateError, "partial|indeterminate"):
            self.run_with_create(creator, self.commit_args(plan))
        self.assertIs(self.locks()[0]["no_retry"], True)

    def test_created_template_that_became_default_is_rejected(self) -> None:
        plan = self.stage()

        def creator(org, source_id, name, cc, clone):
            new_id = "600"
            wrong = clone_of(self.source, new_id, name, cc)
            wrong["is_default"] = True
            self.zoho.details[new_id] = wrong
            return new_id

        with self.assertRaises(emailtool.EmailTemplateError):
            self.run_with_create(creator, self.commit_args(plan))

    # -- read-back logic ---------------------------------------------------
    def test_verify_created_template_accepts_an_exact_clone(self) -> None:
        clone = emailtool.require_clean_source(self.source)
        detail = clone_of(self.source, "600", "CC - All", emailtool.TARGET_TEMPLATES["CC - All"])
        emailtool.verify_created_template(
            detail, "CC - All", emailtool.TARGET_TEMPLATES["CC - All"], clone)

    def test_verify_created_template_rejects_every_drift(self) -> None:
        clone = emailtool.require_clean_source(self.source)
        cc = emailtool.TARGET_TEMPLATES["CC - Accounting"]
        for change in ({"name": "CC - Accounting 2"}, {"type": "estimate_notification"},
                       {"is_default": True}, {"cc_mail_ids": []},
                       {"cc_mail_ids": ["accounting@frpdepots.com", "x@frpdepots.com"]},
                       {"bcc_mail_ids": ["x@frpdepots.com"]}, {"subject": "different"},
                       {"body": "different"}, {"from_address_id": "9"},
                       {"cc_me": True}, {"documents": [{"id": "1"}]},
                       {"placeholder": "other"}, {"bodyv2": "x"},
                       {"language_content": [{"lang": "fr"}]}):
            with self.subTest(change=change):
                detail = clone_of(self.source, "600", "CC - Accounting", cc)
                detail.update(change)
                with self.assertRaises(emailtool.EmailTemplateError):
                    emailtool.verify_created_template(detail, "CC - Accounting", cc, clone)

    def test_source_unchanged_check_detects_any_edit(self) -> None:
        staged = dict(self.source)
        emailtool.verify_source_unchanged(dict(self.source), staged)
        changed = dict(self.source)
        changed["body"] = "edited"
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "changed"):
            emailtool.verify_source_unchanged(changed, staged)

    # -- exposure verification is a read ------------------------------------
    def test_exposure_verification_is_a_get_and_never_sends(self) -> None:
        payload = {
            "emailtemplates": [{"email_template_id": "600", "name": "CC - Accounting"}],
            "cc_mails_list": ["accounting@frpdepots.com"],
        }
        self.api_get.side_effect = None
        self.api_get.return_value = payload
        result = emailtool.verify_template_exposed(
            "token", self.vault, "96274000001559012", "600",
            ("accounting@frpdepots.com",))
        self.assertTrue(result["template_exposed_to_composition"])
        self.assertTrue(result["resolved_cc_matches_plan"])
        self.assertIs(result["email_sent"], False)
        path = self.api_get.call_args[0][2]
        self.assertTrue(path.startswith("/books/v3/invoices/96274000001559012/email?"))
        self.assertIn("email_template_id=600", path)

    def test_exposure_verification_reports_a_mismatch_honestly(self) -> None:
        self.api_get.side_effect = None
        self.api_get.return_value = {"emailtemplates": [], "cc_mails_list": []}
        result = emailtool.verify_template_exposed(
            "token", self.vault, "1", "600", ("accounting@frpdepots.com",))
        self.assertFalse(result["template_exposed_to_composition"])
        self.assertFalse(result["resolved_cc_matches_plan"])

    def test_exposure_verification_only_runs_when_an_invoice_is_named(self) -> None:
        plan = self.stage()

        def creator(org, source_id, name, cc, clone):
            new_id = "600"
            self.zoho.details[new_id] = clone_of(self.source, new_id, name, cc)
            return new_id

        self.run_with_create(creator, self.commit_args(plan))
        self.refresh.assert_not_called()
        self.api_get.assert_not_called()
        self.assertEqual(self.locks()[0]["exposure"], {"status": "not_requested"})

    # -- list command -------------------------------------------------------
    def test_list_templates_is_read_only(self) -> None:
        with mock.patch("sys.stdout"):
            emailtool.command_list_templates(argparse.Namespace())
        self.api_get.assert_not_called()
        self.refresh.assert_not_called()
        self.append_receipt.assert_not_called()

    def test_no_secret_material_is_ever_emitted(self) -> None:
        plan_text = self.stage().read_text(encoding="utf-8")
        for secret in ("cookie", "token", "password", "csrf", "localStorage",
                       "client_secret", "refresh_token"):
            self.assertNotIn(secret.casefold(), plan_text.casefold())


if __name__ == "__main__":
    unittest.main()
