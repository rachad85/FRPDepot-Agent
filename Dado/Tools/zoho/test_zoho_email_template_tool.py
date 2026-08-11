"""Independent safety tests for the Zoho Books invoice email-template tool.

These tests never touch the live Zoho vault, the live UI session or the
network. Every transport is patched to fail loudly if it is reached, so a test
that "passes" because it silently talked to Zoho is impossible.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

import zoho_email_template_tool as emailtool


DEFAULT_ID = "96274000000000014"


def default_detail(**changes) -> dict:
    value = {
        "bcc_mail_ids": [],
        "body": "<div>Invoice #%InvoiceNumber%</div>",
        "bodyv2": "",
        "cc_mail_ids": [],
        "cc_me": False,
        "documents": [],
        "email_template_id": DEFAULT_ID,
        "from_address_id": "",
        "is_default": True,
        "language_content": [],
        "name": "Default",
        "placeholder": "mt_default",
        "subject": "Invoice - %InvoiceNumber% from %CompanyName%",
        "type": "invoice_notification",
    }
    value.update(changes)
    return value


def clone_of(detail: dict, template_id: str, name: str, cc: tuple[str, ...]) -> dict:
    value = dict(detail)
    value.update({
        "email_template_id": template_id,
        "name": name,
        "cc_mail_ids": list(cc),
        "is_default": False,
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
        assert query.get("organization_id") == ["110002157575"], query
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
            "books_organization_id": "110002157575",
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
        self.assertIs(plan["workflow"]["create_workflow_commissioned"], False)
        self.assertEqual(plan["workflow"]["native_save_body_sha256"],
                         emailtool.NATIVE_SAVE_BODY_SHA256)
        self.assertEqual(plan["module"]["email_type"], "invoice_notification")
        self.assertEqual(plan["organization"]["books_organization_id"], "110002157575")

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

    def test_stage_receipt_records_zero_writes(self) -> None:
        self.stage()
        action, evidence = self.append_receipt.call_args[0]
        self.assertIn("staged_not_committed", action)
        self.assertIn("zoho_writes=0", evidence)
        self.assertIn("emails_sent=0", evidence)

    def test_module_contains_no_browser_interaction_or_write_verb(self) -> None:
        source = inspect.getsource(emailtool)
        for token in ('.click(', '.fill(', 'get_by_role', 'page.goto', 'expect_response',
                      'page.route', 'set_input_files', 'press(', 'select_option',
                      '"PUT"', '"DELETE"', '"PATCH"', 'urlopen', 'requests.',
                      'cookie', 'localStorage', 'sessionStorage', 'storage_state'):
            with self.subTest(token=token):
                self.assertNotIn(token, source)
        self.assertEqual(source.count('method: "GET"'), 1)

    def test_the_captured_post_is_described_but_never_issued(self) -> None:
        """POST now appears as pinned capture DATA. It must never be a call site.

        This test was extended, not weakened: the module has to name the verb
        Zoho's own page emits in order to validate it, so the assertion moved
        from "the string POST is absent" to "no POST is ever issued".
        """
        source = inspect.getsource(emailtool)
        self.assertEqual(emailtool.NATIVE_SAVE_METHOD, "POST")
        for token in ('method: "POST"', "method='POST'", 'method="POST"',
                      'method: NATIVE_SAVE_METHOD', 'fetch(url, {method: NATIVE',
                      'route.continue_', 'route.abort', 'sync_playwright().start'):
            with self.subTest(token=token):
                self.assertNotIn(token, source)
        # Exactly one network call site remains, and it is the GET.
        self.assertEqual(source.count("await fetch("), 1)

    def test_module_has_no_send_or_settings_mutation_route(self) -> None:
        source = inspect.getsource(emailtool)
        for token in ('/send', 'sendmail', 'smtp', 'graph.microsoft', 'outlook',
                      '/reminder', '/associate', 'pdftemplate', 'dkim', 'relay',
                      'force_delete', 'markassent', 'markasdefault', '/contacts',
                      '/customers', '/vendors', '/estimates', '/salesorders',
                      '/purchaseorders', '/creditnotes'):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_only_two_actions_exist(self) -> None:
        self.assertEqual(
            emailtool.ACTIONS,
            (emailtool.CREATE_ACCOUNTING_TEST, emailtool.CREATE_REMAINING),
        )
        for banned in ("update", "delete", "rename", "set-default", "associate", "clone-module"):
            self.assertNotIn(banned, emailtool.ACTIONS)

    def test_parser_exposes_only_commissioned_commands(self) -> None:
        parser = emailtool.build_parser()
        actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        self.assertEqual(
            sorted(actions[0].choices), ["commit", "list-templates", "stage"])

    # -- transport allowlist ---------------------------------------------
    def test_transport_refuses_every_non_get_verb(self) -> None:
        for verb in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "get", ""):
            with self.subTest(verb=verb):
                with self.assertRaisesRegex(emailtool.EmailTemplateError, "REFUSED"):
                    emailtool.ui_transport_allowed(verb, emailtool.TEMPLATE_LIST_PATH,
                                                   "110002157575")

    def test_transport_refuses_foreign_paths(self) -> None:
        for path in ("/api/v3/contacts", "/api/v3/settings/emailtemplates/0",
                     "/api/v3/settings/emailtemplates/abc", "/api/v3/invoices",
                     "/api/v3/settings/emailtemplates/14/default", "/"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(emailtool.EmailTemplateError, "REFUSED"):
                    emailtool.ui_transport_allowed("GET", path, "110002157575")

    def test_transport_refuses_foreign_module_filter(self) -> None:
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "REFUSED"):
            emailtool.ui_transport_allowed(
                "GET", emailtool.TEMPLATE_LIST_PATH, "110002157575",
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

    def test_clone_fields_exclude_identity_and_cc(self) -> None:
        for excluded in ("email_template_id", "name", "cc_mail_ids", "is_default"):
            self.assertNotIn(excluded, emailtool.SOURCE_CLONE_FIELDS)
        self.assertEqual(
            set(emailtool.SOURCE_CLONE_FIELDS) | {"email_template_id", "name",
                                                  "cc_mail_ids", "is_default"},
            set(emailtool.SOURCE_DETAIL_FIELDS),
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
                          "organization_id": "110002157575"})

    def test_native_payload_schema_is_closed(self) -> None:
        emailtool.parse_native_save_body(self.native_body())
        extra = emailtool.captured_native_payload()
        extra["surprise"] = 1
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "closed schema"):
            emailtool.parse_native_save_body(self.native_body(**extra))
        missing = emailtool.captured_native_payload()
        missing.pop("cc_mail_ids")
        body = urlencode({"JSONString": json.dumps(missing),
                          "organization_id": "110002157575"})
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
            urlencode({"organization_id": "110002157575", "JSONString": payload}),
            urlencode({"JSONString": payload}),
            urlencode({"JSONString": payload, "organization_id": "110002157575",
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

    # -- create workflow is refused, not invented --------------------------
    def test_native_contract_is_captured_but_workflow_is_not_commissioned(self) -> None:
        self.assertIs(emailtool.NATIVE_SAVE_CONTRACT_CAPTURED, True)
        self.assertIs(emailtool.CREATE_WORKFLOW_COMMISSIONED, False)
        clone = emailtool.require_clean_source(self.source)
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "NOT COMMISSIONED"):
            emailtool.require_create_contract_commissioned(clone)
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "NOT COMMISSIONED"):
            emailtool.create_template_via_ui(
                "1", "CC - Accounting", ("accounting@frpdepots.com",), clone)

    def test_new_form_body_does_not_clone_the_live_default(self) -> None:
        """The blocker itself: proven from the capture, not assumed."""
        captured_body = emailtool.captured_native_payload()["language_content"][0]
        self.assertEqual(
            hashlib.sha256(captured_body["body"].encode("utf-8")).hexdigest(),
            emailtool.NATIVE_FORM_BODY_SHA256)
        self.assertEqual(
            hashlib.sha256(captured_body["subject"].encode("utf-8")).hexdigest(),
            emailtool.NATIVE_FORM_SUBJECT_SHA256)
        # A Default whose body differs from what New emits is refused outright.
        clone = emailtool.require_clean_source(self.source)
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "stock factory invoice body"):
            emailtool.require_native_form_clones_source(clone)

    def test_fidelity_gate_passes_only_when_the_form_would_clone_default(self) -> None:
        matching = emailtool.require_clean_source(default_detail(
            body=emailtool.captured_native_payload()["language_content"][0]["body"],
            subject=emailtool.captured_native_payload()["language_content"][0]["subject"],
        ))
        emailtool.require_native_form_clones_source(matching)
        drifted_subject = dict(matching)
        drifted_subject["subject"] = "Something else"
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "subject no longer"):
            emailtool.require_native_form_clones_source(drifted_subject)

    def test_commit_refuses_before_lock_and_leaves_plan_reusable(self) -> None:
        plan = self.stage()
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "NOT COMMISSIONED"):
            emailtool.command_commit(self.commit_args(plan))
        self.assertFalse(list((self.plan_dir / ".commit-locks").glob("*.json"))
                         if (self.plan_dir / ".commit-locks").exists() else [])
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "NOT COMMISSIONED"):
            emailtool.command_commit(self.commit_args(plan))

    def test_stage_reports_the_commit_blocker(self) -> None:
        plan = json.loads(self.stage().read_text(encoding="utf-8"))
        self.assertIs(plan["workflow"]["create_workflow_commissioned"], False)
        self.assertIn("NOT COMMISSIONED", emailtool.CREATE_BLOCKER)
        self.assertIn("No workflow or selector was invented", emailtool.CREATE_BLOCKER)
        self.assertIn("Clone", emailtool.CREATE_BLOCKER)
        self.assertIn(emailtool.CLONE_FIDELITY_RISK, plan["risks"])

    def test_old_schema_plans_are_incompatible(self) -> None:
        self.assertEqual(emailtool.SCHEMA_VERSION, 2)
        plan = self.stage()
        self.rewrite(plan, schema_version=1)
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "schema version"):
            emailtool.command_commit(self.commit_args(plan))

    # -- lock / no-retry machinery (exercised through the create seam) -----
    def enable_create(self, side_effect):
        # Both gates are lifted deliberately: these tests exercise the lock and
        # no-retry machinery, which must be proven independently of why the
        # create path is currently refused.
        return [
            mock.patch.object(emailtool, "CREATE_WORKFLOW_COMMISSIONED", True),
            mock.patch.object(emailtool, "require_native_form_clones_source"),
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

        def creator(org, name, cc, clone):
            seen["locked"] = bool(self.locks())
            new_id = "600"
            self.zoho.details[new_id] = clone_of(self.source, new_id, name, cc)
            return new_id

        self.run_with_create(creator, self.commit_args(plan))
        self.assertTrue(seen["locked"], "the replay lock must exist before creation")

    def test_successful_commit_locks_and_verifies(self) -> None:
        plan = self.stage()

        def creator(org, name, cc, clone):
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

        def creator(org, name, cc, clone):
            new_id = "600"
            self.zoho.details[new_id] = clone_of(self.source, new_id, name, cc)
            return new_id

        self.run_with_create(creator, self.commit_args(plan))
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "already exists"):
            self.run_with_create(creator, self.commit_args(plan))

    def test_no_retry_after_a_failed_attempt(self) -> None:
        plan = self.stage()
        calls = []

        def creator(org, name, cc, clone):
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

        def creator(org, name, cc, clone):
            raise emailtool.EmailTemplateError("Zoho Save failed")

        with self.assertRaises(emailtool.EmailTemplateError):
            self.run_with_create(creator, self.commit_args(plan))
        with self.assertRaisesRegex(emailtool.EmailTemplateError, "already entered commit"):
            self.run_with_create(creator, self.commit_args(plan))

    def test_partial_multi_template_failure_reports_exactly_what_happened(self) -> None:
        self.add_accounting()
        plan = self.stage(emailtool.CREATE_REMAINING, self.confirmation())
        created = []

        def creator(org, name, cc, clone):
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

        def creator(org, name, cc, clone):
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

        def creator(org, name, cc, clone):
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

        def creator(org, name, cc, clone):
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
