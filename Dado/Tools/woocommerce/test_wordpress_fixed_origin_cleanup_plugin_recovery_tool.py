"""Focused offline safety contracts for the fixed cleanup-plugin recovery tool.

No test opens Playwright, CDP, WordPress or the network.  The exact WordPress 7.0.3
Delete-control/confirmation shape is pinned in a JSON fixture and exercised through
the production URL/form validators with an adversarial fake page.  Event-order
tests prove that the shared browser mutex and every deterministic refusal precede
the permanent attempt lock, while that lock precedes the Delete-link click and the
single confirmation submission.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urljoin, urlsplit

from Dado.Tools.woocommerce import wordpress_fixed_origin_cleanup_plugin_recovery_tool as tool


FIXTURE_PATH = (Path(tool.__file__).parent / "testdata"
                / "wordpress_7_0_3_plugin_delete_confirmation_contract.json")
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
FAKE_NONCE = "wpNonce_ABC12345"


def delete_href(nonce: str = FAKE_NONCE) -> str:
    return FIXTURE["delete_control"]["href_template"].replace("{nonce}", nonce)


def valid_state(phase: str = "removal", *, total: int = 111,
                rows_sha256: str = "a" * 64) -> dict:
    public = []
    records = []
    for target in tool.TARGETS:
        public.append({
            "position": target["position"], "upload_path": target["upload_path"],
            "url": target["url"], "bytes": target["bytes"],
            "sha256": target["sha256"], "status": 404, "redirected": False,
        })
        records.append({"attachment_id": target["attachment_id"],
                        "status": 404, "redirected": False})
    return tool.seal_state(
        tool.expected_plugin(phase), public, records,
        {"total": total, "enumerated": total, "pages": max(1, (total + 19) // 20),
         "rows_sha256": rows_sha256, "target_conflicts": []},
    )


def reseal(state: dict) -> dict:
    return tool.seal_state(state["plugin"], state["public_files"],
                           state["attachment_records"], state["media_catalog"])


class FakeControl:
    def __init__(self, attrs=None, *, on_click=None):
        self.attrs = dict(attrs or {})
        self.on_click = on_click
        self.clicks = 0

    def get_attribute(self, name):
        return self.attrs.get(name)

    def click(self, timeout=None):
        if timeout is None:
            raise AssertionError("click lacked an explicit timeout")
        self.clicks += 1
        if self.on_click is not None:
            self.on_click()


class FakeForm:
    def __init__(self, site: "FakeSite", *, nonce: str | None = None,
                 checked: str | None = None, action: str | None = None,
                 method: str = "post", referer: str | None = None,
                 extra_hidden: bool = False):
        nonce = nonce if nonce is not None else site.nonce
        request_uri = urlsplit(site.delete_url).path + "?" + urlsplit(site.delete_url).query
        self.attrs = {"method": method, "action": action if action is not None else request_uri}
        values = [
            ("checked[]", checked if checked is not None else tool.PLUGIN_FILE),
            ("verify-delete", "1"),
            ("action", "delete-selected"),
            ("_wpnonce", nonce),
            ("_wp_http_referer", referer if referer is not None else request_uri),
        ]
        if extra_hidden:
            values.append(("foreign", "1"))
        self.hidden = [FakeControl({"type": "hidden", "name": name, "value": value})
                       for name, value in values]
        self.submit = FakeControl({"id": "submit", "type": "submit"},
                                  on_click=site.submit_deletion)

    def get_attribute(self, name):
        return self.attrs.get(name)

    def query_selector_all(self, selector):
        if selector == 'input[type="hidden"]':
            return list(self.hidden)
        if selector == 'input#submit[type="submit"]':
            return [self.submit]
        return []


class FakeRow:
    def __init__(self, site: "FakeSite"):
        self.site = site

    def get_attribute(self, name):
        if name == "class":
            return "active" if self.site.active else "inactive"
        return None

    def inner_text(self):
        return f"{self.site.plugin_name} Version {self.site.version} | By FRP Depot"

    def query_selector_all(self, selector):
        if selector == tool.ACTIVATE_SELECTOR:
            return [] if self.site.active else [self.site.activation_decoy]
        if selector == tool.DEACTIVATE_SELECTOR:
            return [self.site.deactivation_decoy] if self.site.active else []
        if selector == tool.DELETE_SELECTOR:
            return [] if self.site.active else list(self.site.delete_links)
        return []


class FakeSite:
    def __init__(self, *, present=True, active=False, version=tool.PLUGIN_VERSION,
                 update_marker=False, row_count=1):
        self.present = present
        self.active = active
        self.version = version
        self.update_marker = update_marker
        self.row_count = row_count
        self.plugin_name = tool.PLUGIN_NAME
        self.nonce = FAKE_NONCE
        self.current_url = tool.PLUGINS_URL
        self.events: list[str] = []
        self.navigations: list[str] = []
        self.activation_decoy = FakeControl({"href": "never-inspected-activation"},
                                            on_click=lambda: self.forbidden("activation"))
        self.deactivation_decoy = FakeControl({"href": "never-inspected-deactivation"},
                                              on_click=lambda: self.forbidden("deactivation"))
        self.delete_url = urljoin(tool.PLUGINS_URL, delete_href(self.nonce))
        self.delete_links = [FakeControl({"href": delete_href(self.nonce)},
                                         on_click=self.open_confirmation)]
        self.forms: list[FakeForm] = []
        self.submissions = 0

    def forbidden(self, action):
        raise AssertionError(f"forbidden action was reached: {action}")

    def open_confirmation(self):
        self.events.append("delete_link_click")
        self.current_url = self.delete_url
        if not self.forms:
            self.forms = [FakeForm(self)]

    def submit_deletion(self):
        self.events.append("confirmation_submit")
        self.submissions += 1
        self.present = False
        self.row_count = 0
        self.current_url = FIXTURE["success_redirect"]["url"]

    def goto(self, url):
        self.navigations.append(url)
        self.current_url = url

    def query_selector_all(self, selector):
        if selector == tool.ROW_SELECTOR:
            return [FakeRow(self) for _ in range(self.row_count)] if self.present else []
        if selector == tool.UPDATE_ROW_SELECTOR:
            return [FakeControl()] if self.update_marker else []
        if selector == "form" and self.current_url == self.delete_url:
            return list(self.forms)
        return []


class FakePage:
    def __init__(self, site: FakeSite):
        self.site = site

    @property
    def url(self):
        return self.site.current_url

    def goto(self, url, wait_until=None, timeout=None):
        if wait_until is None or timeout is None:
            raise AssertionError("navigation was not explicitly bounded")
        self.site.goto(url)

    def wait_for_load_state(self, state, timeout=None):
        if state != "domcontentloaded" or timeout is None:
            raise AssertionError("load-state wait was not explicitly bounded")

    def query_selector_all(self, selector):
        return self.site.query_selector_all(selector)


class LocalStateMixin:
    @contextmanager
    def local_state(self):
        with tempfile.TemporaryDirectory(prefix="ffoc-recovery-test-") as folder:
            root = Path(folder)
            values = {
                "PLAN_DIR": root / "plans",
                "LOCAL_STATE": root / "state",
                "REGISTRY_KEY": root / "state" / "registry.key",
                "REGISTRY_DIR": root / "state" / "stages",
                "ATTEMPT_DIR": root / "state" / "attempts",
                "RESULT_DIR": root / "state" / "results",
                "EVENT_DIR": root / "state" / "events",
                "RECEIPTS": root / "receipts.jsonl",
            }
            with patch.multiple(tool, **values):
                yield values


class FixedIdentityAndEvidenceTests(LocalStateMixin, unittest.TestCase):
    def test_exact_identity_and_new_operation_are_separate(self):
        self.assertEqual("frpdepot-fixed-four-origin-file-cleanup/"
                         "frpdepot-fixed-four-origin-file-cleanup.php", tool.PLUGIN_FILE)
        self.assertEqual("1.0.0", tool.PLUGIN_VERSION)
        self.assertEqual((5521, 5523, 5525, 5527), tool.TARGET_IDS)
        self.assertNotEqual(tool.FAILED_OPERATION_SHA256, tool.operation_sha())
        self.assertRegex(tool.operation_sha(), r"^[0-9a-f]{64}$")
        self.assertNotEqual(tool.OLD_LOCAL_STATE, tool.LOCAL_STATE)
        for new_path in (tool.REGISTRY_DIR, tool.ATTEMPT_DIR, tool.RESULT_DIR, tool.EVENT_DIR):
            self.assertNotEqual(tool.OLD_LOCAL_STATE, new_path)
            self.assertNotIn(str(tool.OLD_LOCAL_STATE), str(new_path))

    def test_real_failed_plan_attempt_event_and_result_are_exact_read_only_evidence(self):
        paths = [tool.FAILED_PLAN_PATH, tool.FAILED_ATTEMPT_PATH,
                 tool.FAILED_EVENT_PATH, tool.FAILED_RESULT_PATH]
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        evidence = tool.validate_previous_failure_evidence()
        after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.assertEqual(before, after)
        self.assertEqual(tool.FAILED_PLAN_SHA256,
                         evidence["failed_plan"]["internal_sha256"])
        self.assertEqual(tool.FAILED_OPERATION_SHA256,
                         evidence["failed_operation_sha256"])
        self.assertEqual("attempt_started_no_retry", evidence["attempt"]["status"])
        self.assertEqual("attempted_no_retry", evidence["activation_event"]["status"])
        self.assertEqual("INDETERMINATE_NO_RETRY", evidence["result"]["status"])
        self.assertEqual(["plugin_installed_inactive_verified"],
                         evidence["result"]["completed_steps"])

    def test_staged_plan_preserves_old_paths_and_hashes_without_mutating_old_files(self):
        paths = [tool.FAILED_PLAN_PATH, tool.FAILED_ATTEMPT_PATH,
                 tool.FAILED_EVENT_PATH, tool.FAILED_RESULT_PATH]
        before_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        evidence = tool.validate_previous_failure_evidence()
        with self.local_state():
            path, plan = tool.stage_plan(valid_state(), evidence)
            self.assertTrue(path.is_file())
            self.assertEqual(evidence, plan["previous_failure_evidence"])
            self.assertEqual(str(tool.FAILED_ATTEMPT_PATH),
                             plan["previous_failure_evidence"]["attempt"]["path"])
            self.assertEqual(tool.FAILED_ATTEMPT_SHA256,
                             plan["previous_failure_evidence"]["attempt"]["sha256"])
            self.assertEqual(str(tool.FAILED_RESULT_PATH),
                             plan["previous_failure_evidence"]["result"]["path"])
            self.assertEqual(tool.FAILED_RESULT_SHA256,
                             plan["previous_failure_evidence"]["result"]["sha256"])
        after_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.assertEqual(before_hashes, after_hashes)


class EligibilityPredicateTests(unittest.TestCase):
    def test_one_predicate_accepts_exact_removal_and_final_states(self):
        for phase in ("removal", "final"):
            state = valid_state(phase)
            self.assertIs(state, tool.assert_recovery_eligible(state, phase))

    def test_complete_media_count_is_observed_not_hard_coded(self):
        for total in (0, 83, 111, 112, 400):
            tool.assert_recovery_eligible(valid_state(total=total), "removal")
            tool.assert_recovery_eligible(valid_state("final", total=total), "final")

    def test_active_version_update_delete_and_absent_mismatches_refuse(self):
        mutations = (
            lambda state: state.update(plugin=tool.project_plugin(True, True, "1.0.0", False, False)),
            lambda state: state.update(plugin=tool.project_plugin(True, False, "1.0.1", False, True)),
            lambda state: state.update(plugin=tool.project_plugin(True, False, "1.0.0", True, True)),
            lambda state: state.update(plugin=tool.project_plugin(True, False, "1.0.0", False, False)),
            lambda state: state.update(plugin=tool.expected_plugin("final")),
        )
        for mutate in mutations:
            state = valid_state()
            mutate(state)
            state = reseal(state)
            with self.assertRaises(tool.RecoveryError):
                tool.assert_recovery_eligible(state, "removal")

    def test_each_origin_and_media_id_must_independently_remain_exact_404(self):
        for index in range(4):
            state = valid_state()
            state["public_files"][index]["status"] = 410
            state = reseal(state)
            with self.assertRaises(tool.RecoveryError):
                tool.assert_recovery_eligible(state, "removal")
            state = valid_state()
            state["attachment_records"][index]["status"] = 200
            state = reseal(state)
            with self.assertRaises(tool.RecoveryError):
                tool.assert_recovery_eligible(state, "removal")

    def test_media_library_must_be_complete_and_conflict_free(self):
        mutations = (
            lambda catalog: catalog.update(enumerated=catalog["total"] - 1),
            lambda catalog: catalog.update(pages=0),
            lambda catalog: catalog.update(rows_sha256="0" * 63),
            lambda catalog: catalog.update(target_conflicts=[{"id": 5521, "filename": "x"}]),
        )
        for mutate in mutations:
            state = valid_state()
            mutate(state["media_catalog"])
            state = reseal(state)
            with self.assertRaises(tool.RecoveryError):
                tool.assert_recovery_eligible(state, "removal")

    def test_stale_plugin_or_state_fingerprint_refuses(self):
        state = valid_state()
        state["plugin"]["version"] = "1.0.1"
        with self.assertRaises(tool.RecoveryError):
            tool.assert_recovery_eligible(state, "removal")
        state = valid_state()
        state["fingerprint"] = "0" * 64
        with self.assertRaises(tool.RecoveryError):
            tool.assert_recovery_eligible(state, "removal")


class DeleteRouteFixtureTests(unittest.TestCase):
    def test_bounded_delete_route_diagnostic_retains_no_raw_url_or_nonce(self):
        shape = tool.sanitized_delete_url_shape(delete_href())
        serialized = json.dumps(shape, sort_keys=True)
        self.assertTrue(shape["same_origin"])
        self.assertTrue(shape["action_exact"])
        self.assertTrue(shape["checked_exact_once"])
        self.assertEqual("all", shape["plugin_status"])
        self.assertEqual(len(FAKE_NONCE), shape["nonce_length"])
        self.assertNotIn(FAKE_NONCE, serialized)
        self.assertNotIn(delete_href(), serialized)

    def test_bounded_delete_route_diagnostic_reports_shape_without_foreign_values(self):
        shape = tool.sanitized_delete_url_shape(
            "https://evil.example/wp-admin/plugins.php?action=activate&checked%5B%5D=akismet%2Fakismet.php"
        )
        serialized = json.dumps(shape, sort_keys=True)
        self.assertFalse(shape["same_origin"])
        self.assertFalse(shape["action_exact"])
        self.assertFalse(shape["checked_exact_once"])
        self.assertNotIn("evil.example", serialized)
        self.assertNotIn("akismet", serialized)

    def test_fixture_reproduces_exact_wordpress_703_delete_shape(self):
        self.assertEqual("7.0.3", FIXTURE["source"]["wordpress_version"])
        for path_key, hash_key in (("screen_file", "screen_sha256"),
                                   ("list_table_file", "list_table_sha256")):
            source = (tool.ROOT / FIXTURE["source"][path_key]).read_bytes()
            self.assertEqual(FIXTURE["source"][hash_key], hashlib.sha256(source).hexdigest())
        screen = (tool.ROOT / FIXTURE["source"]["screen_file"]).read_text(encoding="utf-8")
        list_table = (tool.ROOT / FIXTURE["source"]["list_table_file"]).read_text(
            encoding="utf-8")
        self.assertIn('form method="post" action="<?php echo esc_url( $_SERVER[\'REQUEST_URI\'] ); ?>"',
                      screen)
        self.assertIn('wp_redirect( self_admin_url( "plugins.php?deleted=$plugins_to_delete',
                      screen)
        self.assertIn("plugins.php?action=delete-selected", list_table)
        self.assertIn("'&amp;checked[]=' . urlencode( $plugin_file )", list_table)
        self.assertTrue(FIXTURE["delete_control"]["nonce_placeholder_only"])
        self.assertEqual(tool.PLUGIN_FILE, FIXTURE["plugin"]["file"])
        self.assertEqual(["action", "checked[0]", "plugin_status", "paged", "s", "_wpnonce"],
                         FIXTURE["delete_control"]["query_keys"])
        self.assertEqual(["checked[]", "verify-delete", "action", "_wpnonce",
                          "_wp_http_referer"],
                         FIXTURE["confirmation_form"]["hidden_names"])
        tool.assert_delete_action_url(delete_href())
        tool.assert_plugins_url(FIXTURE["success_redirect"]["url"], "deleted_result")

    def test_delete_url_rejects_wrong_plugin_action_keys_nonce_and_origin(self):
        good = delete_href()
        bad = (
            good.replace(tool.PLUGIN_FILE.replace("/", "%2F"), "akismet%2Fakismet.php"),
            good.replace("delete-selected", "activate"),
            good + "&checked%5B%5D=akismet%2Fakismet.php",
            good + "&foreign=1",
            good.replace(FAKE_NONCE, "short"),
            "https://evil.example/wp-admin/plugins.php?" + urlsplit(good).query,
        )
        for url in bad:
            with self.assertRaises(tool.RecoveryError, msg=url):
                tool.assert_delete_action_url(url)

    def test_real_adapters_click_exact_link_and_submit_exact_confirmation_once(self):
        site = FakeSite()
        admin = tool.AdminPage(FakePage(site))
        state = valid_state()
        link = admin.preflight_delete_control(state)
        submit = admin.prepare_delete(link, state)
        self.assertEqual(["delete_link_click"], site.events)
        absent = admin.execute_delete(submit, state)
        self.assertEqual(tool.expected_plugin("final"), absent)
        self.assertEqual(["delete_link_click", "confirmation_submit"], site.events)
        self.assertEqual(1, site.delete_links[0].clicks)
        self.assertEqual(1, submit.clicks)
        self.assertEqual(1, site.submissions)
        self.assertEqual(0, site.activation_decoy.clicks)
        self.assertEqual(0, site.deactivation_decoy.clicks)

    def test_confirmation_nonce_is_transient_and_never_returned(self):
        site = FakeSite()
        admin = tool.AdminPage(FakePage(site))
        state = valid_state()
        submit = admin.prepare_delete(admin.preflight_delete_control(state), state)
        serialized = json.dumps({"state": state, "submit_attributes": submit.attrs}, sort_keys=True)
        self.assertNotIn(FAKE_NONCE, serialized)
        self.assertNotIn("_wpnonce", serialized)

    def test_mismatched_form_nonce_or_target_refuses_without_submit(self):
        for form_factory in (
            lambda site: FakeForm(site, nonce="otherNonce_12345"),
            lambda site: FakeForm(site, checked="akismet/akismet.php"),
            lambda site: FakeForm(site, extra_hidden=True),
            lambda site: FakeForm(site, action="/wp-admin/plugins.php"),
            lambda site: FakeForm(site, method="get"),
        ):
            site = FakeSite()
            site.forms = [form_factory(site)]
            admin = tool.AdminPage(FakePage(site))
            state = valid_state()
            link = admin.preflight_delete_control(state)
            with self.assertRaises(tool.RecoveryError):
                admin.prepare_delete(link, state)
            self.assertEqual(["delete_link_click"], site.events)
            self.assertEqual(0, site.submissions)

    def test_ambiguous_two_exact_confirmation_forms_refuse(self):
        site = FakeSite()
        site.forms = [FakeForm(site), FakeForm(site)]
        admin = tool.AdminPage(FakePage(site))
        state = valid_state()
        with self.assertRaises(tool.RecoveryError):
            admin.prepare_delete(admin.preflight_delete_control(state), state)
        self.assertEqual(0, site.submissions)


class AdminProjectionTests(unittest.TestCase):
    def test_inactive_exact_row_projects_exact_removal_identity(self):
        admin = tool.AdminPage(FakePage(FakeSite()))
        admin.goto_plugins()
        self.assertEqual(tool.expected_plugin("removal"), admin.read_row())

    def test_absent_row_projects_exact_final_identity(self):
        admin = tool.AdminPage(FakePage(FakeSite(present=False, row_count=0)))
        admin.goto_plugins()
        self.assertEqual(tool.expected_plugin("final"), admin.read_row())

    def test_active_version_mismatch_update_and_ambiguity_are_not_eligible(self):
        active = tool.AdminPage(FakePage(FakeSite(active=True)))
        projected = active.read_row()
        state = valid_state()
        state["plugin"] = projected
        state = reseal(state)
        with self.assertRaises(tool.RecoveryError):
            tool.assert_recovery_eligible(state, "removal")

        for site in (FakeSite(version="1.0.1"), FakeSite(update_marker=True)):
            projected = tool.AdminPage(FakePage(site)).read_row()
            state = valid_state()
            state["plugin"] = projected
            state = reseal(state)
            with self.assertRaises(tool.RecoveryError):
                tool.assert_recovery_eligible(state, "removal")

        with self.assertRaises(tool.RecoveryError):
            tool.AdminPage(FakePage(FakeSite(row_count=2))).read_row()
        with self.assertRaises(tool.RecoveryError):
            tool.AdminPage(FakePage(FakeSite(present=False, row_count=0,
                                             update_marker=True))).read_row()

    def test_media_reader_requires_complete_catalog_and_finds_fixed_conflicts(self):
        admin = tool.AdminPage(FakePage(FakeSite()))
        admin._media_reader = Mock()
        rows = [{"id": 1, "filename": "ordinary.png", "stem": "ordinary"}]
        admin._media_reader.enumerate_library.return_value = {
            "complete": True, "total": 1, "pages": 1, "rows": rows, "unidentified": 0,
        }
        observed = admin.read_media_catalog()
        self.assertEqual(1, observed["total"])
        self.assertEqual([], observed["target_conflicts"])

        conflict_name = Path(tool.TARGETS[0]["upload_path"]).name
        conflicts = rows + [{"id": 5521, "filename": "other.png", "stem": "other"},
                            {"id": 2, "filename": conflict_name, "stem": "target"}]
        admin._media_reader.enumerate_library.return_value = {
            "complete": True, "total": 3, "pages": 1, "rows": conflicts,
            "unidentified": 0,
        }
        observed = admin.read_media_catalog()
        self.assertEqual([2, 5521], [item["id"] for item in observed["target_conflicts"]])
        state = valid_state()
        state["media_catalog"] = observed
        state = reseal(state)
        with self.assertRaises(tool.RecoveryError):
            tool.assert_recovery_eligible(state, "removal")

        admin._media_reader.enumerate_library.return_value = {
            "complete": False, "total": 2, "pages": 1, "rows": rows, "unidentified": 1,
        }
        with self.assertRaises(tool.RecoveryError):
            admin.read_media_catalog()


class PlanAndStageTests(LocalStateMixin, unittest.TestCase):
    def test_immutable_authenticated_24_hour_plan_round_trip(self):
        evidence = tool.validate_previous_failure_evidence()
        with self.local_state():
            path, plan = tool.stage_plan(valid_state(), evidence)
            self.assertEqual(timedelta(hours=24),
                tool.datetime.fromisoformat(plan["expires_utc"])
                - tool.datetime.fromisoformat(plan["created_utc"]))
            loaded_path, loaded = tool.load_plan(str(path))
            self.assertEqual(path, loaded_path)
            self.assertEqual(plan, loaded)
            self.assertIn("ONE ATTEMPT, NO RETRY, NO ROLLBACK", plan["risk"])
            self.assertEqual(valid_state()["fingerprint"], plan["before_fingerprint"])
            self.assertTrue(plan["after_expected"]["media_library"]
                            ["total_is_observed_not_fixed"])

    def test_plan_mutation_unregistered_copy_expiry_and_stale_version_fail_closed(self):
        evidence = tool.validate_previous_failure_evidence()
        with self.local_state() as values:
            path, plan = tool.stage_plan(valid_state(), evidence)
            raw = json.loads(path.read_text(encoding="ascii"))
            raw["risk"] = "atomic"
            path.write_text(json.dumps(raw), encoding="ascii")
            with self.assertRaises(tool.RecoveryError):
                tool.load_plan(str(path))

            path, plan = tool.stage_plan(valid_state(), evidence)
            outside = values["PLAN_DIR"].parent / "copy.json"
            outside.write_bytes(path.read_bytes())
            with self.assertRaises(tool.RecoveryError):
                tool.load_plan(str(outside))

            expired = tool.datetime.fromisoformat(plan["expires_utc"]) + timedelta(seconds=1)
            with patch.object(tool, "utc_now", return_value=expired):
                with self.assertRaises(tool.RecoveryError):
                    tool.load_plan(str(path))
            with patch.object(tool, "TOOL_VERSION", "9.9.9"):
                with self.assertRaises(tool.RecoveryError):
                    tool.load_plan(str(path))

    def test_approval_is_exact_unpadded_uppercase(self):
        tool.require_approval("APPROVED")
        for value in ("approved", " APPROVED", "APPROVED ", "APPROVED\n", "APPROVE", ""):
            with self.assertRaises(tool.RecoveryError):
                tool.require_approval(value)

    def test_already_absent_stage_reports_verified_and_creates_no_plan_or_lifecycle_state(self):
        with self.local_state() as values:
            @contextmanager
            def session(_purpose):
                yield object()

            output = io.StringIO()
            with patch.object(tool, "admin_session", session), \
                 patch.object(tool, "collect_state", return_value=valid_state("final")), \
                 patch("sys.stdout", new=output):
                tool.command_stage(SimpleNamespace())
            report = json.loads(output.getvalue())
            self.assertEqual("VERIFIED_ALREADY_ABSENT", report["status"])
            self.assertFalse(report["plan_created"])
            self.assertEqual(0, report["website_writes"])
            self.assertEqual(0, report["plugin_deletions"])
            for key in ("PLAN_DIR", "REGISTRY_DIR", "ATTEMPT_DIR", "RESULT_DIR", "EVENT_DIR"):
                self.assertFalse(values[key].exists(), key)

    def test_present_exact_stage_is_read_only_and_creates_one_plan_no_attempt(self):
        with self.local_state() as values:
            @contextmanager
            def session(_purpose):
                yield object()

            output = io.StringIO()
            with patch.object(tool, "admin_session", session), \
                 patch.object(tool, "collect_state", return_value=valid_state()), \
                 patch("sys.stdout", new=output):
                tool.command_stage(SimpleNamespace())
            report = json.loads(output.getvalue())
            self.assertEqual("STAGED_READ_ONLY", report["status"])
            self.assertEqual(0, report["website_writes"])
            self.assertEqual(1, len(list(values["PLAN_DIR"].glob("*.json"))))
            self.assertFalse(values["ATTEMPT_DIR"].exists())
            self.assertFalse(values["RESULT_DIR"].exists())
            self.assertFalse(values["EVENT_DIR"].exists())

    def test_stage_rejected_plugin_states_create_no_plan(self):
        for plugin in (
            tool.project_plugin(True, True, "1.0.0", False, False),
            tool.project_plugin(True, False, "1.0.1", False, True),
            tool.project_plugin(True, False, "1.0.0", True, True),
            tool.project_plugin(True, False, "1.0.0", False, False),
        ):
            with self.local_state() as values:
                state = valid_state()
                state["plugin"] = plugin
                state = reseal(state)

                @contextmanager
                def session(_purpose):
                    yield object()

                with patch.object(tool, "admin_session", session), \
                     patch.object(tool, "collect_state", return_value=state):
                    with self.assertRaises(tool.RecoveryError):
                        tool.command_stage(SimpleNamespace())
                self.assertFalse(values["PLAN_DIR"].exists())
                self.assertFalse(values["ATTEMPT_DIR"].exists())

    def test_cli_has_no_generic_inputs(self):
        parser = tool.parser()
        diagnose = parser.parse_args(["diagnose-delete-route"])
        self.assertEqual({"command", "func"}, set(vars(diagnose)))
        stage = parser.parse_args(["stage"])
        self.assertEqual({"command", "func"}, set(vars(stage)))
        commit = parser.parse_args(["commit", "--plan", "fixed.json",
                                    "--approval", "APPROVED"])
        self.assertEqual({"command", "plan", "approval", "func"}, set(vars(commit)))
        for args in (["stage", "--plugin", "akismet/akismet.php"],
                     ["diagnose-delete-route", "--url", "https://evil.example"],
                     ["stage", "--path", "other"],
                     ["commit", "--plan", "x", "--approval", "APPROVED",
                      "--url", "https://evil.example"]):
            with patch.object(sys, "stderr"):
                with self.assertRaises(SystemExit):
                    parser.parse_args(args)


class CommitOrderingTests(LocalStateMixin, unittest.TestCase):
    def plan(self, before=None):
        before = before or valid_state()
        return {
            "sha256": "b" * 64,
            "operation_sha256": tool.operation_sha(),
            "before": before,
            "before_fingerprint": before["fingerprint"],
            "previous_failure_evidence": tool.validate_previous_failure_evidence(),
            "expires_utc": (tool.utc_now() + timedelta(hours=1)).isoformat(),
        }

    def test_mutex_fresh_predicate_preflight_lock_click_single_submit_and_final_order(self):
        with self.local_state() as values:
            before, final = valid_state(), valid_state("final")
            plan = self.plan(before)
            events: list[str] = []
            mutex = {"held": False}
            self_test = self

            class FakeAdmin:
                def preflight_delete_control(self, state):
                    self_test.assertTrue(mutex["held"])
                    events.append("preflight_delete_control")
                    return object()

                def prepare_delete(self, link, state):
                    self_test.assertTrue((values["ATTEMPT_DIR"] /
                                          f'{plan["operation_sha256"]}.json').is_file())
                    events.append("delete_link_click")
                    return object()

                def execute_delete(self, submit, state):
                    self_test.assertTrue((values["EVENT_DIR"] / plan["operation_sha256"] /
                                          "02-delete-confirmation-submit-attempted.json").is_file())
                    events.append("confirmation_submit_once")
                    return tool.expected_plugin("final")

            @contextmanager
            def session(_purpose):
                mutex["held"] = True
                events.append("mutex_enter")
                try:
                    yield FakeAdmin()
                finally:
                    events.append("mutex_exit")
                    mutex["held"] = False

            states = iter([before, final])
            def collect(_admin):
                state = next(states)
                events.append("collect_removal" if state["plugin"]["present"] else "collect_final")
                return state

            real_exclusive = tool.exclusive_json
            def recording_exclusive(path, value):
                path = Path(path)
                if path.parent == values["ATTEMPT_DIR"]:
                    self.assertTrue(mutex["held"])
                    events.append("attempt_lock")
                elif path.parent == values["EVENT_DIR"] / plan["operation_sha256"]:
                    events.append(value["event"])
                return real_exclusive(path, value)

            args = SimpleNamespace(plan="fixed.json", approval="APPROVED")
            with patch.object(tool, "load_plan", return_value=(Path("fixed.json"), plan)), \
                 patch.object(tool, "admin_session", session), \
                 patch.object(tool, "collect_state", side_effect=collect), \
                 patch.object(tool, "exclusive_json", side_effect=recording_exclusive), \
                 patch("sys.stdout", new=io.StringIO()):
                tool.command_commit(args)

            self.assertEqual([
                "mutex_enter", "collect_removal", "preflight_delete_control",
                "attempt_lock", "delete_link_click", "01-delete-link-clicked",
                "02-delete-confirmation-submit-attempted", "confirmation_submit_once",
                "collect_final", "mutex_exit",
            ], events)
            result = json.loads((values["RESULT_DIR"] /
                                 f'{plan["operation_sha256"]}.json').read_text())
            self.assertEqual("COMMITTED_AND_VERIFIED", result["status"])
            self.assertEqual(1, result["delete_link_navigations"])
            self.assertEqual(1, result["delete_confirmation_submissions"])
            self.assertTrue(result["plugin_deleted"])

    def test_browser_busy_is_free_refusal_before_attempt_lock(self):
        with self.local_state() as values:
            plan = self.plan()

            @contextmanager
            def busy(_purpose):
                raise tool.UiLaneBusy("busy")
                yield  # pragma: no cover

            args = SimpleNamespace(plan="fixed.json", approval="APPROVED")
            with patch.object(tool, "load_plan", return_value=(Path("fixed.json"), plan)), \
                 patch.object(tool, "admin_session", busy):
                with self.assertRaises(tool.UiLaneBusy):
                    tool.command_commit(args)
            self.assertFalse(values["ATTEMPT_DIR"].exists())
            self.assertFalse(values["RESULT_DIR"].exists())

    def test_bad_approval_refuses_before_browser_and_attempt(self):
        with self.local_state() as values:
            plan = self.plan()
            args = SimpleNamespace(plan="fixed.json", approval="approved")
            with patch.object(tool, "load_plan", return_value=(Path("fixed.json"), plan)), \
                 patch.object(tool, "admin_session") as session:
                with self.assertRaises(tool.RecoveryError):
                    tool.command_commit(args)
            session.assert_not_called()
            self.assertFalse(values["ATTEMPT_DIR"].exists())

    def test_bad_approval_refuses_before_plan_registry_and_failed_evidence_reads(self):
        args = SimpleNamespace(plan="fixed.json", approval="approved")
        with patch.object(tool, "load_plan") as load, \
             patch.object(tool, "validate_previous_failure_evidence") as evidence, \
             patch.object(tool, "admin_session") as session:
            with self.assertRaises(tool.RecoveryError):
                tool.command_commit(args)
        load.assert_not_called()
        evidence.assert_not_called()
        session.assert_not_called()

    def test_active_version_update_and_ambiguous_projections_refuse_before_lock(self):
        rejected = (
            tool.project_plugin(True, True, "1.0.0", False, False),
            tool.project_plugin(True, False, "1.0.1", False, True),
            tool.project_plugin(True, False, "1.0.0", True, True),
            tool.project_plugin(True, False, "1.0.0", False, False),
        )
        for plugin in rejected:
            with self.local_state() as values:
                staged = valid_state()
                fresh = valid_state()
                fresh["plugin"] = plugin
                fresh = reseal(fresh)
                plan = self.plan(staged)

                @contextmanager
                def session(_purpose):
                    yield Mock()

                args = SimpleNamespace(plan="fixed.json", approval="APPROVED")
                with patch.object(tool, "load_plan", return_value=(Path("fixed.json"), plan)), \
                     patch.object(tool, "admin_session", session), \
                     patch.object(tool, "collect_state", return_value=fresh):
                    with self.assertRaises(tool.RecoveryError):
                        tool.command_commit(args)
                self.assertFalse(values["ATTEMPT_DIR"].exists())
                self.assertFalse(values["RESULT_DIR"].exists())

    def test_fresh_exact_fingerprint_drift_refuses_before_lock(self):
        with self.local_state() as values:
            staged = valid_state(rows_sha256="a" * 64)
            fresh = valid_state(rows_sha256="b" * 64)
            plan = self.plan(staged)

            @contextmanager
            def session(_purpose):
                yield Mock()

            args = SimpleNamespace(plan="fixed.json", approval="APPROVED")
            with patch.object(tool, "load_plan", return_value=(Path("fixed.json"), plan)), \
                 patch.object(tool, "admin_session", session), \
                 patch.object(tool, "collect_state", return_value=fresh):
                with self.assertRaises(tool.RecoveryError):
                    tool.command_commit(args)
            self.assertFalse(values["ATTEMPT_DIR"].exists())
            self.assertFalse(values["RESULT_DIR"].exists())

    def test_failure_after_delete_link_lock_is_indeterminate_and_never_submits(self):
        with self.local_state() as values:
            before = valid_state()
            plan = self.plan(before)
            events: list[str] = []

            class FakeAdmin:
                def preflight_delete_control(self, state):
                    events.append("preflight")
                    return object()

                def prepare_delete(self, link, state):
                    self_test.assertTrue((values["ATTEMPT_DIR"] /
                                          f'{plan["operation_sha256"]}.json').exists())
                    events.append("delete_link_click")
                    raise tool.RecoveryError("confirmation ambiguous")

                def execute_delete(self, submit, state):
                    events.append("forbidden_submit")

            self_test = self
            @contextmanager
            def session(_purpose):
                yield FakeAdmin()

            args = SimpleNamespace(plan="fixed.json", approval="APPROVED")
            with patch.object(tool, "load_plan", return_value=(Path("fixed.json"), plan)), \
                 patch.object(tool, "admin_session", session), \
                 patch.object(tool, "collect_state", return_value=before):
                with self.assertRaises(tool.RecoveryError):
                    tool.command_commit(args)
            self.assertEqual(["preflight", "delete_link_click"], events)
            result = json.loads((values["RESULT_DIR"] /
                                 f'{plan["operation_sha256"]}.json').read_text())
            self.assertEqual("INDETERMINATE_NO_RETRY", result["status"])
            self.assertTrue(result["no_retry"])
            self.assertFalse(result["rollback"])
            self.assertFalse(result["cleanup_after_failure"])
            self.assertTrue((values["ATTEMPT_DIR"] /
                             f'{plan["operation_sha256"]}.json').exists())


class SharedPredicateContractTests(LocalStateMixin, unittest.TestCase):
    def test_every_stage_accepted_state_reaches_prepare_and_execute_guards(self):
        evidence = tool.validate_previous_failure_evidence()
        state = valid_state()
        with self.local_state():
            path, _plan = tool.stage_plan(state, evidence)
            self.assertTrue(path.exists())
        site = FakeSite()
        admin = tool.AdminPage(FakePage(site))
        with patch.object(tool, "assert_recovery_eligible",
                          wraps=tool.assert_recovery_eligible) as predicate:
            link = admin.preflight_delete_control(state)
            submit = admin.prepare_delete(link, state)
            admin.execute_delete(submit, state)
        removal_calls = [call for call in predicate.call_args_list
                         if len(call.args) >= 2 and call.args[1] == "removal"]
        self.assertGreaterEqual(len(removal_calls), 3)

    def test_same_rejected_state_stops_stage_preflight_prepare_and_execute_before_click(self):
        bad = valid_state()
        bad["plugin"] = tool.project_plugin(True, True, "1.0.0", False, False)
        bad = reseal(bad)
        site = FakeSite()
        admin = tool.AdminPage(FakePage(site))
        control = FakeControl({"href": delete_href()}, on_click=site.open_confirmation)
        evidence = tool.validate_previous_failure_evidence()
        with self.local_state():
            with self.assertRaises(tool.RecoveryError):
                tool.stage_plan(bad, evidence)
        for call in (
            lambda: admin.preflight_delete_control(bad),
            lambda: admin.prepare_delete(control, bad),
            lambda: admin.execute_delete(control, bad),
        ):
            with self.assertRaises(tool.RecoveryError):
                call()
        self.assertEqual(0, control.clicks)
        self.assertEqual([], site.events)


class StaticCapabilityTests(unittest.TestCase):
    def test_only_two_exact_browser_click_sites_and_one_fixed_navigation_site_exist(self):
        source = Path(tool.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        click_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                       and isinstance(node.func, ast.Attribute) and node.func.attr == "click"]
        goto_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Attribute) and node.func.attr == "goto"]
        self.assertEqual(2, len(click_calls))
        self.assertEqual(1, len(goto_calls))
        self.assertEqual({"link", "submit"},
                         {node.func.value.id for node in click_calls
                          if isinstance(node.func.value, ast.Name)})
        goto = goto_calls[0]
        self.assertIsInstance(goto.args[0], ast.Name)
        self.assertEqual("PLUGINS_URL", goto.args[0].id)

    def test_banned_browser_network_business_and_shell_capabilities_are_absent(self):
        source = Path(tool.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {alias.name for node in ast.walk(tree)
                    if isinstance(node, (ast.Import, ast.ImportFrom))
                    for alias in node.names}
        for banned in ("requests", "urllib.request", "subprocess", "shutil", "socket",
                       "smtplib"):
            self.assertNotIn(banned, imported)
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for banned in ("set_input_files", "fill", "type", "press", "request", "post",
                       "delete", "launch", "launch_persistent_context"):
            self.assertNotIn(banned, attributes)
        lower = source.lower()
        for route in ("plugin-install.php", "update.php?action=upload-plugin",
                      "/wp-admin/admin-post.php", "admin_post_", "wp_ajax_",
                      "register_rest_route", "/wc/v3", "edit.php?post_type=product",
                      "wp-cli.phar"):
            self.assertNotIn(route, lower)
        self.assertEqual(1, source.count("method: 'GET'"))
        self.assertNotIn("method: 'POST'", source)
        self.assertNotIn("method: 'PUT'", source)
        self.assertNotIn("method: 'DELETE'", source)

    def test_failed_tool_adminpage_and_write_adapters_are_not_imported(self):
        source = Path(tool.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        failed_imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                          and node.module and node.module.endswith(
                              "wordpress_fixed_origin_file_cleanup_tool")]
        self.assertEqual(1, len(failed_imports))
        self.assertEqual(["StrictCleanupMediaReader"],
                         [alias.name for alias in failed_imports[0].names])
        self.assertNotIn("execute_install", source)
        self.assertNotIn("execute_activation", source)
        self.assertNotIn("prepare_install", source)
        self.assertNotIn("prepare_activation", source)

    def test_wordpress_nonce_never_enters_plan_event_result_or_output_contract(self):
        source = Path(tool.__file__).read_text(encoding="utf-8")
        for function_name in ("stage_plan", "_write_event", "command_stage", "command_commit"):
            function = next(node for node in ast.parse(source).body
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and node.name == function_name)
            segment = ast.get_source_segment(source, function) or ""
            self.assertNotIn('values["_wpnonce"]', segment)
            self.assertNotIn("query_nonce", segment)
            self.assertNotIn("form_query_nonce", segment)
        self.assertTrue(FIXTURE["safety"]
                        ["nonce_is_validated_transiently_but_never_stored_or_printed"])


if __name__ == "__main__":
    unittest.main()
