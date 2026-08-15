"""Focused offline policy tests for the fixed FNPT 2.0.6 -> 2.0.7 route.

The historical filename is retained so complete unittest discovery keeps the former
active-plugin repair suite in place. No test opens a browser or network connection.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timedelta
import hashlib
import io
import inspect
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import wordpress_plugin_deployment_tool as d


class Admin:
    """The only live-shaped methods the FNPT route is allowed to reach."""

    def __init__(self, before, after=None, *, fail_upload=False):
        self.rows = [before] + ([] if after is None else [after])
        self.fail_upload = fail_upload
        self.goto_count = 0
        self.uploads = 0
        self.upload_paths: list[str] = []
        self.lane_probe = lambda: False
        self.lock_probe = lambda: False
        self.lane_seen_at_upload: list[bool] = []
        self.lock_seen_at_upload: list[bool] = []

    def goto_plugins(self):
        self.goto_count += 1

    def read_row(self):
        if not self.rows:
            raise AssertionError("unexpected extra plugin-row read")
        return self.rows.pop(0)

    def upload_fnpt_display_repair(self, path):
        self.uploads += 1
        self.upload_paths.append(str(path))
        self.lane_seen_at_upload.append(bool(self.lane_probe()))
        self.lock_seen_at_upload.append(bool(self.lock_probe()))
        if Path(path).resolve() != Path(d.FNPT_REPAIR_ARTIFACT_PATH).resolve():
            raise AssertionError(f"unexpected upload path: {path}")
        if self.fail_upload:
            raise RuntimeError("blocked")
        return {
            "comparison_name": d.PLUGIN_NAME,
            "comparison_uploaded_version": d.FNPT_REPAIR_VERSION,
            "overwrite_navigation_proven": True,
            "overwrite_http_status": 200,
            "wordpress_success_marker_exact": True,
        }

    def activate(self):  # pragma: no cover - a call is itself the failure
        raise AssertionError("FNPT replacement route attempted activation")

    def deactivate(self):  # pragma: no cover - a call is itself the failure
        raise AssertionError("FNPT replacement route attempted deactivation")

    def delete(self):  # pragma: no cover - a call is itself the failure
        raise AssertionError("FNPT replacement route attempted deletion")


class FnptAdminPageNavigationTests(unittest.TestCase):
    """Exercise the exact FNPT entry point through the real shared AdminPage helper."""

    @staticmethod
    def page_and_site():
        from test_wordpress_plugin_deployment import FakePage, FakeWordPress

        site = FakeWordPress(version=d.FNPT_REPAIR_FROM_VERSION, active=True)
        site.comparison_version = d.FNPT_REPAIR_VERSION
        return d.AdminPage(FakePage(site)), site

    def test_exact_fnpt_upload_entry_proves_both_navigations_and_structured_success(self):
        page, site = self.page_and_site()
        result = page.upload_fnpt_display_repair(d.FNPT_REPAIR_ARTIFACT_PATH)
        self.assertEqual(site.uploads, [str(d.FNPT_REPAIR_ARTIFACT_PATH)])
        self.assertEqual(result["comparison_name"], d.PLUGIN_NAME)
        self.assertEqual(result["comparison_uploaded_version"], d.FNPT_REPAIR_VERSION)
        self.assertTrue(result["overwrite_navigation_proven"])
        self.assertEqual(result["overwrite_http_status"], 200)
        self.assertTrue(result["wordpress_success_marker_exact"])

    def test_exact_fnpt_upload_entry_refuses_unproven_navigation(self):
        page, site = self.page_and_site()
        site.overwrite_navigates = False
        with self.assertRaises(d.IndeterminateError):
            page.upload_fnpt_display_repair(d.FNPT_REPAIR_ARTIFACT_PATH)

    def test_exact_fnpt_upload_entry_refuses_unproven_initial_upload_navigation(self):
        page, site = self.page_and_site()
        site.upload_navigates = False
        with self.assertRaises(d.IndeterminateError):
            page.upload_fnpt_display_repair(d.FNPT_REPAIR_ARTIFACT_PATH)


class ProbeGuard:
    def projection(self):
        return {
            "allowed_methods": ["GET", "HEAD"],
            "allowed_read_requests": 10,
            "non_read_requests_aborted": 2,
            "non_read_method_counts": {
                method: (2 if method == "POST" else 0)
                for method in d.FnptNetworkGuard._METHOD_BUCKETS
            },
            "off_origin_reads_aborted": 4,
            "disallowed_same_origin_reads_aborted": 3,
            "total_requests_aborted": 9,
            "analytics_submission_performed": False,
            "business_write_performed": False,
        }


class PublicProbe:
    def __init__(self, name, *, canonical_stale=False):
        self.name = name
        self.guard = ProbeGuard()
        self.loads = []
        self.selections = []
        self.freight_states = []
        self.direct_states = []
        self.events = []
        self.payloads = []
        self.quantities = []
        self.plugin_reset_checks = []
        self.canonical_stale = canonical_stale
        self.current_url = None

    def load(self, url):
        self.current_url = url
        self.loads.append(url)

    def require_release_contract(self, parent_id, expected_ids, *, expected_freight):
        if self.canonical_stale and self.current_url == d.FNPT_PRODUCT_URL:
            raise d.FnptPublicRefusal("asset_hash")
        if parent_id != 2061 or tuple(expected_ids) != d.FNPT_PUBLISHED_VARIATION_IDS:
            raise AssertionError("FNPT contract broadened")
        if expected_freight is not True:
            raise AssertionError("FNPT freight payload was not exact")
        return {variation_id: {"variation_id": variation_id, "attributes": {"attribute_a": "x"}}
                for variation_id in expected_ids}

    def require_release_shell(self):
        return None

    def fixed_control_row(self, parent_id, variation_id, expected_freight):
        expected = {2028: (1368, False), 2044: (1368, True), 2057: (1455, True)}
        if expected[variation_id] != (parent_id, expected_freight):
            raise AssertionError("comparator identity/decision drift")
        return {"variation_id": variation_id, "attributes": {"attribute_a": "x"}}

    def require_selection_controls(self, parent_id, row):
        return None

    def capture_unresolved_baseline(self, parent_id):
        return None

    def select_row(self, parent_id, row):
        self.selections.append((parent_id, row["variation_id"]))

    def require_freight_state(self, parent_id, variation_id, **kwargs):
        self.freight_states.append((parent_id, variation_id, kwargs))

    def require_direct_state(self, variation_id):
        self.direct_states.append(variation_id)

    def dispatch_lifecycle(self, event):
        self.events.append(event)

    def dispatch_found_variation(self, payload):
        self.payloads.append(dict(payload))

    def _require_unresolved_state(self, parent_id):
        return None

    def _require_plugin_reset_state(self, parent_id):
        self.plugin_reset_checks.append(parent_id)

    def quantity_transition(self, value):
        self.quantities.append(value)

    def require_no_page_errors(self):
        return [
            {
                "page_category": page_category,
                "accepted_fixed_guard_error_count": 1,
                "unclassified_error_count": 0,
                "off_origin_reads_aborted_delta": 1,
                "status": "passed",
            }
            for page_category in ("fnpt_product", "stub_control", "pipe_control")
        ]


class FakeRequest:
    def __init__(self, method, url, resource_type):
        self.method = method
        self.url = url
        self.resource_type = resource_type


class FakeRoute:
    def __init__(self, request):
        self.request = request
        self.aborted = []
        self.continued = 0

    def abort(self, reason):
        self.aborted.append(reason)

    def continue_(self):
        self.continued += 1


class ShellPage:
    """Minimal offline page for the wp_localize_script scalar-type regression."""

    def __init__(self, config):
        self.config = config

    def on(self, *_args):
        return None

    def query_selector_all(self, selector):
        return [object()] if selector == d.FNPT_PANEL_SELECTOR else []

    def evaluate(self, _source, *_args):
        return self.config


class FnptDisplayRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fnpt-plugin-plans-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.old_plan_dir, self.old_receipts = d.PLAN_DIR, d.RECEIPTS
        d.PLAN_DIR = self.tmp / "plans"
        d.RECEIPTS = self.tmp / "receipts.jsonl"
        self.addCleanup(self._restore_paths)

        self.before = d.project_row(True, True, d.FNPT_REPAIR_FROM_VERSION, False)
        self.after = d.project_row(True, True, d.FNPT_REPAIR_VERSION, False)
        self.in_lane = False
        self.lane_entries: list[tuple[str, str]] = []
        self.lock_expected_absent_on_lane_entry: Path | None = None
        self.sessions_opened = 0
        self.real_public_validation = d._run_fnpt_public_validation
        self.public_findings = {
            "status": "PASSED",
            "contexts_opened": 2,
            "fnpt_variation_ids": list(d.FNPT_PUBLISHED_VARIATION_IDS),
            "non_submission": {
                "add_to_cart_clicked": False,
                "quote_or_contact_form_submitted": False,
                "order_or_payment_created": False,
                "email_sent": False,
                "analytics_submission_performed": False,
                "cache_purge_or_invalidation_performed": False,
                "wordpress_or_woocommerce_write_performed": False,
            },
        }
        self.design_findings = {
            "status": "STRUCTURE_COMPATIBLE",
            "anonymous_contexts_opened": 1,
            "persistent_context": False,
            "fnpt_parent_id": 2061,
            "fnpt_variation_count": 60,
            "fnpt_variation_ids_sha256": hashlib.sha256(json.dumps(
                list(d.FNPT_PUBLISHED_VARIATION_IDS), ensure_ascii=True,
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest(),
            "fixed_controls": [2028, 2044, 2057, 2088],
            "selection_controls_exact": True,
            "allowed_methods": ["GET", "HEAD"],
            "non_read_requests_aborted_and_recorded": True,
            "analytics_submission_performed": False,
            "business_write_performed": False,
        }
        self.public_patch = mock.patch.object(
            d, "_run_fnpt_public_validation", return_value=self.public_findings
        )
        self.design_patch = mock.patch.object(
            d, "_run_fnpt_design_preflight", return_value=self.design_findings
        )
        self.public_patch.start()
        self.design_patch.start()
        self.addCleanup(self.public_patch.stop)
        self.addCleanup(self.design_patch.stop)

        @contextlib.contextmanager
        def fake_lane(lane, *, purpose, **_kwargs):
            self.assertEqual(lane, "wordpress")
            if self.lock_expected_absent_on_lane_entry is not None:
                self.assertFalse(
                    self.lock_expected_absent_on_lane_entry.exists(),
                    "the shared browser lane must be acquired before the permanent attempt lock",
                )
            self.lane_entries.append((lane, purpose))
            self.in_lane = True
            try:
                yield
            finally:
                self.in_lane = False

        self.lane_patch = mock.patch.object(d, "ui_browser_lock", fake_lane)
        self.lane_patch.start()
        self.addCleanup(self.lane_patch.stop)

    def _restore_paths(self):
        d.PLAN_DIR, d.RECEIPTS = self.old_plan_dir, self.old_receipts

    @contextlib.contextmanager
    def session(self, admin):
        self.sessions_opened += 1
        yield admin

    def install_admin(self, admin):
        admin.lane_probe = lambda: self.in_lane
        patcher = mock.patch.object(d, "admin_session", lambda: self.session(admin))
        patcher.start()
        self.addCleanup(patcher.stop)
        return admin

    def artifact(self):
        return d.verify_fnpt_display_repair_artifact()

    def plan(self):
        return d.stage_plan(
            "plugin_fnpt_display_repair", self.before, self.after, self.artifact(),
            self.design_findings,
        )

    @staticmethod
    def args(path, approval="APPROVED"):
        return argparse.Namespace(plan=str(path), approval=approval)

    @staticmethod
    def read_json(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def rehash(self, path, mutate):
        payload = self.read_json(path)
        payload.pop("sha256")
        mutate(payload)
        payload["sha256"] = d.digest_for(payload)
        Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def commit(self, path, approval="APPROVED"):
        with contextlib.redirect_stdout(io.StringIO()):
            d.command_commit_fnpt_display_repair(self.args(path, approval))

    # ------------------------------------------------------------------
    # Artifact and fixed identity.
    # ------------------------------------------------------------------
    def test_artifact_is_hard_pinned_member_by_member(self):
        artifact = self.artifact()
        self.assertEqual(Path(artifact["path"]).resolve(), Path(d.FNPT_REPAIR_ARTIFACT_PATH).resolve())
        self.assertEqual(artifact["sha256"], d.FNPT_REPAIR_SHA256)
        self.assertEqual(artifact["version"], "2.0.7")
        self.assertEqual(artifact["bytes"], d.FNPT_REPAIR_BYTES)
        self.assertEqual(tuple(artifact["members"]), tuple(sorted(d.FNPT_REPAIR_MEMBERS)))
        self.assertEqual(artifact["member_sha256"], d.FNPT_REPAIR_MEMBER_SHA256)
        self.assertEqual(artifact["allowlist_sha256"], d.FNPT_REPAIR_ALLOWLIST_SHA256)
        self.assertEqual(Path(artifact["baseline_path"]).resolve(), Path(d.FNPT_REPAIR_BASELINE_PATH).resolve())
        self.assertEqual(artifact["baseline_sha256"], d.FNPT_REPAIR_BASELINE_SHA256)
        self.assertEqual(d.FNPT_REPAIR_ALLOWLIST_SHA256,
                         d.FNPT_REPAIR_MEMBER_SHA256[f"{d.PLUGIN_SLUG}/ups-allowlist.json"])

    def test_wrong_path_zip_hash_member_hash_and_allowlist_hash_are_refused(self):
        with self.assertRaises(d.DeploymentError):
            d.verify_fnpt_display_repair_artifact(Path(__file__))
        with mock.patch.object(d, "FNPT_REPAIR_SHA256", "0" * 64):
            with self.assertRaises(d.DeploymentError):
                d.verify_fnpt_display_repair_artifact()
        forged_members = dict(d.FNPT_REPAIR_MEMBER_SHA256)
        forged_members[next(iter(forged_members))] = "0" * 64
        with mock.patch.object(d, "FNPT_REPAIR_MEMBER_SHA256", forged_members):
            with self.assertRaises(d.DeploymentError):
                d.verify_fnpt_display_repair_artifact()
        with mock.patch.object(d, "FNPT_REPAIR_ALLOWLIST_SHA256", "0" * 64):
            with self.assertRaises(d.DeploymentError):
                d.verify_fnpt_display_repair_artifact()

    def test_validation_discloses_preserved_behavior_and_selected_id_fail_closed_rule(self):
        contract = d.FNPT_REPAIR_VALIDATION_CONTRACT
        self.assertTrue(contract["presentation_scope_only"])
        self.assertEqual(contract["fnpt_parent_product_id"], 2061)
        self.assertEqual(contract["fnpt_regression_variation_id"], 2088)
        self.assertEqual(contract["direct_checkout_control_variation_id"], 2028)
        self.assertEqual(contract["oversized_control_variation_id"], 2044)
        self.assertEqual(contract["pipe_control_variation_id"], 2057)
        self.assertTrue(contract["missing_malformed_or_inconsistent_variation_id_fails_to_freight"])
        self.assertEqual(contract["allowlisted_variations"], 64)
        self.assertFalse(contract["server_cart_checkout_controls_changed"])
        self.assertFalse(contract["quote_form_contract_changed"])
        self.assertFalse(contract["product_price_stock_shipping_class_weight_dimensions_touched"])
        self.assertTrue(contract["inline_dataset_requires_exact_parent_variation_identity_and_freight_true"])
        self.assertTrue(contract["missing_malformed_or_inconsistent_variation_id_disables_quote_handoff"])
        self.assertEqual(tuple(contract["fnpt_published_variation_ids"]), d.FNPT_PUBLISHED_VARIATION_IDS)
        self.assertEqual(contract["fnpt_published_variation_count"], 60)

    # ------------------------------------------------------------------
    # Staging and immutable plan policy.
    # ------------------------------------------------------------------
    def test_stage_accepts_only_exact_active_206_without_update_marker(self):
        with mock.patch.object(d, "_live_row", return_value=self.before), \
             mock.patch.object(d, "_stage_and_report") as staged:
            d.command_stage_fnpt_display_repair(argparse.Namespace())
        staged.assert_called_once_with(
            "plugin_fnpt_display_repair", self.before, self.artifact(),
            design_preflight=self.design_findings,
        )

        bad_rows = (
            d.project_row(True, False, "2.0.6", False),
            d.project_row(True, True, "2.0.5", False),
            d.project_row(True, True, "2.0.7", False),
            d.project_row(True, True, "2.0.6", True),
        )
        for bad in bad_rows:
            with self.subTest(bad=bad), mock.patch.object(d, "_live_row", return_value=bad):
                with self.assertRaises(d.DeploymentError):
                    d.command_stage_fnpt_display_repair(argparse.Namespace())

    def test_read_only_stage_creates_one_local_24_hour_plan_and_no_attempt_lock(self):
        before = set((d.PLAN_DIR).glob("*.json")) if d.PLAN_DIR.exists() else set()
        with mock.patch.object(d, "_live_row", return_value=self.before), \
             contextlib.redirect_stdout(io.StringIO()) as stdout:
            d.command_stage_fnpt_display_repair(argparse.Namespace())
        fresh = sorted(set(d.PLAN_DIR.glob("*.json")) - before)
        self.assertEqual(len(fresh), 1)
        plan_path = fresh[0]
        plan = self.read_json(plan_path)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["status"], "STAGED_NOT_COMMITTED")
        self.assertFalse(output["external_write_performed"])
        self.assertEqual(output["design_preflight"], self.design_findings)
        self.assertEqual(plan["schema_version"], 10)
        self.assertEqual(plan["tool_version"], d.TOOL_VERSION)
        self.assertEqual(plan["action"], "plugin_fnpt_display_repair")
        self.assertEqual(plan["before"], self.before)
        self.assertEqual(plan["after_expected"], self.after)
        self.assertEqual(plan["preflight"], self.design_findings)
        self.assertEqual(
            datetime.fromisoformat(plan["expires_utc"]) - datetime.fromisoformat(plan["created_utc"]),
            timedelta(hours=24),
        )
        self.assertFalse(d.lock_path(plan_path).exists())
        self.assertFalse(d.result_path(plan_path).exists())
        self.assertEqual(output["approval"], "APPROVED")

    def test_plan_load_semantically_pins_action_artifact_member_hashes_and_exact_states(self):
        path = self.plan()
        original = Path(path).read_text(encoding="utf-8")
        mutations = (
            lambda p: p.update(action="plugin_activate"),
            lambda p: p["artifact"].update(path=str(Path(__file__).resolve())),
            lambda p: p["artifact"].update(sha256="0" * 64),
            lambda p: p["artifact"].update(version="9.9.9"),
            lambda p: p["artifact"].update(member_sha256={}),
            lambda p: p["artifact"].update(allowlist_sha256="0" * 64),
            lambda p: p["artifact"].update(bytes=d.FNPT_REPAIR_BYTES + 1),
            lambda p: p.update(before=d.project_row(True, True, "2.0.5", False)),
            lambda p: p.update(after_expected=d.project_row(True, False, "2.0.7", False)),
            lambda p: p["validation"].update(allowlisted_variations=65),
            lambda p: p.update(preflight=None),
            lambda p: p["preflight"].update(fnpt_variation_count=59),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                Path(path).write_text(original, encoding="utf-8")
                self.rehash(path, mutate)
                with self.assertRaises(d.DeploymentError):
                    d.load_plan(str(path))

    def test_plan_hash_tampering_and_non_24_hour_expiry_are_refused(self):
        path = self.plan()
        payload = self.read_json(path)
        payload["nonce"] = "tampered"
        Path(path).write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(d.DeploymentError):
            d.load_plan(str(path))

        path = self.plan()
        created = datetime.fromisoformat(self.read_json(path)["created_utc"])
        self.rehash(path, lambda p: p.update(expires_utc=(created + timedelta(hours=25)).isoformat()))
        with self.assertRaises(d.DeploymentError) as caught:
            d.load_plan(str(path))
        self.assertIn("24-hour", str(caught.exception))

    def test_parser_has_fixed_fnpt_commands_and_no_generic_route_arguments(self):
        parser = d.build_parser()
        stage = parser.parse_args(["stage-fnpt-display-repair"])
        commit = parser.parse_args([
            "commit-fnpt-display-repair", "--plan", "x", "--approval", "APPROVED"
        ])
        self.assertIs(stage.func, d.command_stage_fnpt_display_repair)
        self.assertIs(commit.func, d.command_commit_fnpt_display_repair)
        self.assertFalse(hasattr(stage, "path"))
        self.assertFalse(hasattr(stage, "plugin"))
        self.assertFalse(hasattr(stage, "action"))
        for argv in (
            ["stage-fnpt-display-repair", "--path", "x"],
            ["stage-fnpt-display-repair", "--plugin", "akismet"],
            ["commit-fnpt-display-repair", "--plan", "x", "--approval", "APPROVED",
             "--action", "activate"],
            ["stage-ups-repair"],
            ["commit-ups-repair", "--plan", "x", "--approval", "APPROVED"],
        ):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args(argv)

    # ------------------------------------------------------------------
    # Commit policy. Every browser and write is fake/offline.
    # ------------------------------------------------------------------
    def test_exact_unpadded_later_approved_is_required_before_admin_or_attempt_lock(self):
        path = self.plan()
        for wrong in ("approved", " APPROVED", "APPROVED ", "APPROVED\n", "APPROVED NOW", ""):
            with self.subTest(wrong=wrong), mock.patch.object(
                d, "admin_session", side_effect=AssertionError("admin must not open")
            ):
                with self.assertRaises(d.DeploymentError):
                    self.commit(path, wrong)
                self.assertFalse(d.lock_path(path).exists())
                self.assertFalse(d.result_path(path).exists())

    def test_shared_lane_is_acquired_before_attempt_lock_and_upload_occurs_once(self):
        path = self.plan()
        lock = d.lock_path(path)
        self.lock_expected_absent_on_lane_entry = lock
        admin = self.install_admin(Admin(self.before, self.after))
        admin.lock_probe = lock.exists
        self.commit(path)

        self.assertEqual(admin.uploads, 1)
        self.assertEqual(admin.upload_paths, [str(d.FNPT_REPAIR_ARTIFACT_PATH)])
        self.assertEqual(admin.lane_seen_at_upload, [True])
        self.assertEqual(admin.lock_seen_at_upload, [True])
        self.assertEqual(self.read_json(lock)["status"], "committed_verified")
        result = self.read_json(d.result_path(path))
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["after"], self.after)
        self.assertTrue(result["comparison"]["overwrite_navigation_proven"])
        self.assertEqual(result["comparison"]["overwrite_http_status"], 200)
        self.assertTrue(result["comparison"]["wordpress_success_marker_exact"])
        self.assertFalse(result["automatic_rollback"])
        self.assertEqual(result["public_validation"], self.public_findings)

    def test_browser_busy_is_a_free_refusal_for_stage_and_commit(self):
        path = self.plan()
        planned_before = set(d.PLAN_DIR.glob("*.json"))

        @contextlib.contextmanager
        def busy(*_args, **_kwargs):
            raise d.UiLaneBusy("wordpress browser busy; nothing attempted")
            yield  # pragma: no cover

        with mock.patch.object(d, "ui_browser_lock", busy), \
             mock.patch.object(d, "admin_session", side_effect=AssertionError("no admin")):
            with self.assertRaises(d.UiLaneBusy):
                d.command_stage_fnpt_display_repair(argparse.Namespace())
            with self.assertRaises(d.UiLaneBusy):
                d.command_commit_fnpt_display_repair(self.args(path))
        self.assertEqual(set(d.PLAN_DIR.glob("*.json")), planned_before)
        self.assertFalse(d.lock_path(path).exists())
        self.assertFalse(d.result_path(path).exists())

    def test_fresh_prewrite_fingerprint_drift_refuses_without_burning_plan(self):
        path = self.plan()
        drift = d.project_row(True, True, "2.0.6", True)
        admin = self.install_admin(Admin(drift))
        with self.assertRaises(d.DeploymentError):
            self.commit(path)
        self.assertEqual(admin.uploads, 0)
        self.assertFalse(d.lock_path(path).exists())
        self.assertFalse(d.result_path(path).exists())

    def test_fresh_design_preflight_drift_refuses_before_admin_or_attempt_lock(self):
        path = self.plan()
        drift = {**self.design_findings, "selection_controls_exact": False}
        with mock.patch.object(d, "_run_fnpt_design_preflight", return_value=drift), \
             mock.patch.object(d, "admin_session", side_effect=AssertionError("no admin")):
            with self.assertRaises(d.DeploymentError) as caught:
                self.commit(path)
        self.assertIn("differs", str(caught.exception))
        self.assertFalse(d.lock_path(path).exists())
        self.assertFalse(d.result_path(path).exists())

    def test_upload_failure_is_indeterminate_one_attempt_no_retry_and_no_rollback(self):
        path = self.plan()
        admin = self.install_admin(Admin(self.before, fail_upload=True))
        admin.lock_probe = d.lock_path(path).exists
        with self.assertRaises(d.IndeterminateError):
            self.commit(path)
        self.assertEqual(admin.uploads, 1)
        lock = self.read_json(d.lock_path(path))
        result = self.read_json(d.result_path(path))
        self.assertEqual(lock["status"], "indeterminate")
        self.assertFalse(result["retry"])
        with self.assertRaises(d.DeploymentError):
            self.commit(path)
        self.assertEqual(admin.uploads, 1, "a permanently locked plan must never retry")

    def test_postwrite_row_must_be_exact_active_207_or_plan_is_indeterminate(self):
        for wrong_after in (
            d.project_row(True, False, "2.0.7", False),
            d.project_row(True, True, "2.0.6", False),
            d.project_row(True, True, "2.0.7", True),
        ):
            with self.subTest(wrong_after=wrong_after):
                path = self.plan()
                admin = self.install_admin(Admin(self.before, wrong_after))
                with self.assertRaises(d.IndeterminateError) as caught:
                    self.commit(path)
                self.assertIn("active version 2.0.7", str(caught.exception))
                result = self.read_json(d.result_path(path))
                self.assertFalse(result["retry"])
                self.assertFalse(result["rollback"])

    def test_route_has_no_activation_deactivation_delete_or_business_write_path(self):
        path = self.plan()
        admin = self.install_admin(Admin(self.before, self.after))
        self.commit(path)
        self.assertEqual(admin.uploads, 1)
        result = self.read_json(d.result_path(path))
        self.assertTrue(result["active_before_and_after"])
        for value in result["public_validation"]["non_submission"].values():
            self.assertFalse(value)
        self.assertFalse(result["automatic_rollback"])
        self.assertFalse(hasattr(d.AdminPage, "delete"))

    def test_non_atomic_no_rollback_risk_and_no_submission_postwrite_contract_are_explicit(self):
        contract = d.FNPT_REPAIR_VALIDATION_CONTRACT
        self.assertFalse(contract["site_level_atomic"])
        self.assertFalse(contract["automatic_rollback"])
        self.assertTrue(contract["one_upload_attempt"])
        self.assertFalse(contract["retry_allowed"])
        self.assertTrue(contract["overwrite_control_requires_exact_reviewed_url_shape"])
        self.assertTrue(contract["overwrite_navigation_must_start_and_finish_bounded"])
        self.assertEqual(contract["overwrite_http_status_required"], 200)
        self.assertEqual(
            contract["wordpress_success_marker_required_exactly_once"],
            "Plugin updated successfully.",
        )
        self.assertTrue(contract["nonce_and_package_values_never_logged_or_planned"])
        self.assertEqual(contract["post_write_plugin_row_required"], "version 2.0.7 and active")
        self.assertTrue(contract["each_context_runs_all_fnpt_fail_closed_stub_and_pipe_cases"])
        self.assertFalse(contract["cart_quote_required_strict_boolean_consumer_changed"])
        self.assertFalse(contract["cache_purge_or_invalidation_authorized"])
        self.assertFalse(contract["cache_purge_or_invalidation_included"])
        self.assertEqual(contract["anonymous_contexts_exact"], 2)
        self.assertEqual(contract["anonymous_allowed_methods"], ["GET", "HEAD"])
        self.assertTrue(contract["every_non_get_head_aborted_and_recorded"])
        self.assertFalse(contract["analytics_submission_allowed"])
        self.assertFalse(contract["customer_business_submission_allowed"])
        self.assertEqual(contract["success_status"], "COMMITTED_AND_VERIFIED")
        self.assertFalse(contract["success_pending_status_allowed"])
        self.assertTrue(contract["cache_correctness_proof"]["canonical_stale_content_is_indeterminate"])
    def test_every_schema6_schema7_and_schema8_plan_is_retired_locally_and_preserved(self):
        plan_dir = Path(r"C:\FRPDepot\Dado\20_Working\wordpress_plugin_plans")
        d.PLAN_DIR = plan_dir
        cases = (
            (
                plan_dir / "20260814T171727Z_plugin_fnpt_display_repair_4e475e6ec1ae4f98.json",
                {
                    "plan": "e9b216687ee433598f9107a01bec4305d36b20fe42ceb9eec74bd7b8aa8ed120",
                },
            ),
            (
                plan_dir / "20260814T175659Z_plugin_fnpt_display_repair_d02032a0717f6bdb.json",
                {
                    "plan": "aceb1a252fce769d04c0ae0ac18422640711531731b31236a1875c3f7a504b6c",
                    "commit-lock": "6498a5a16f90597af4ac6983d536f9d26d2b55386b4e950467fe1b644739b884",
                    "result": "93d25fd1db8d61003200d8c2fa09837c5e96899207822e938f8cc7330e712d73",
                },
            ),
            (
                plan_dir / "20260814T222445Z_plugin_fnpt_display_repair_8f6034de1fbc2d69.json",
                {
                    "plan": "b19b1097e2abb13e8b16e0955221d38f0c61334da458e26495eb2bbe2046fac8",
                    "commit-lock": "e49490ffdcf4862afd0c2c29f7b94a0ad01a6b4725fc620da978b4846c403537",
                    "result": "5241fcb9b72d5473798c6a54b624f0def5a48c02588f3b35051a94cfb5f36fcd",
                },
            ),
        )
        for old, expected in cases:
            with self.subTest(schema=self.read_json(old)["schema_version"]):
                related = {"plan": old}
                if "commit-lock" in expected:
                    related["commit-lock"] = d.lock_path(old)
                    related["result"] = d.result_path(old)
                before = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for name, path in related.items()}
                self.assertEqual(before, expected)
                with mock.patch.object(d, "ui_browser_lock", side_effect=AssertionError("no lane")), \
                     mock.patch.object(d, "admin_session", side_effect=AssertionError("no admin")), \
                     mock.patch.object(d, "fnpt_anonymous_session", side_effect=AssertionError("no network")):
                    with self.assertRaises(d.DeploymentError) as caught:
                        self.commit(old)
                self.assertIn("schema", str(caught.exception).casefold())
                after = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                         for name, path in related.items()}
                self.assertEqual(after, before)
        self.assertFalse(d.lock_path(cases[0][0]).exists())
        self.assertFalse(d.result_path(cases[0][0]).exists())

    def test_design_incompatibility_fails_closed_before_a_plan_is_created(self):
        before = set(d.PLAN_DIR.glob("*.json")) if d.PLAN_DIR.exists() else set()
        with mock.patch.object(d, "_live_row", return_value=self.before), \
             mock.patch.object(d, "_run_fnpt_design_preflight",
                               side_effect=d.FnptPublicRefusal("selection_control")):
            with self.assertRaises(d.DeploymentError):
                d.command_stage_fnpt_display_repair(argparse.Namespace())
        self.assertEqual(set(d.PLAN_DIR.glob("*.json")) if d.PLAN_DIR.exists() else set(), before)

    def test_synthetic_plugin_reset_does_not_require_woocommerce_to_clear_hidden_id(self):
        baseline = {
            "formCount": 1,
            "resolved": "0",
            "panelCount": 1,
            "panelHidden": True,
            "panelVisible": False,
            "nativeHidden": False,
            "nativeVisible": True,
            "nativeDisplay": "block",
            "nativeVisibility": "visible",
            "nativeOwnedClass": False,
            "nativeDisabled": False,
            "nativeAriaHidden": None,
            "nativeAriaDisabled": None,
            "nativeTabindex": None,
            "nativeButtonCount": 1,
            "ownedClassCount": 0,
            "quoteHref": None,
            "quoteAriaDisabled": "true",
            "quoteTabindex": "-1",
        }
        probe = object.__new__(d.FnptCustomerPage)
        probe._unresolved_native_baseline = probe._native_restore_projection(baseline)
        state = dict(baseline, resolved="2088")
        probe._state = lambda: state

        probe._require_plugin_reset_state(2061)
        with self.assertRaises(d.FnptPublicRefusal) as caught:
            probe._require_unresolved_state(2061)
        self.assertEqual(caught.exception.code, "stale_state")

        state["resolved"] = ""
        probe._require_unresolved_state(2061)

        state["resolved"] = "2088"
        state["quoteHref"] = "https://frpdepots.com/?forbidden=stale"
        with self.assertRaises(d.FnptPublicRefusal) as caught:
            probe._require_plugin_reset_state(2061)
        self.assertEqual(caught.exception.code, "stale_state")

    def test_guard_induced_page_errors_are_attributed_per_page_and_fail_closed(self):
        def customer():
            guard = ProbeGuard()
            guard.off_origin_reads_aborted = 0
            return d.FnptCustomerPage(ShellPage({}), guard), guard

        exact, exact_guard = customer()
        exact._begin_page_error_scope("fnpt_product")
        exact_guard.off_origin_reads_aborted = 1
        exact._capture_page_error(Exception("Stripe is not defined"))
        self.assertEqual(exact.require_no_page_errors(), [{
            "page_category": "fnpt_product",
            "accepted_fixed_guard_error_count": 1,
            "unclassified_error_count": 0,
            "off_origin_reads_aborted_delta": 1,
            "status": "passed",
        }])

        no_guard_cause, _ = customer()
        no_guard_cause._begin_page_error_scope("fnpt_product")
        no_guard_cause._capture_page_error(Exception("Stripe is not defined"))
        with self.assertRaises(d.FnptPublicRefusal) as caught:
            no_guard_cause.require_no_page_errors()
        self.assertEqual(caught.exception.code, "page_error_guard_attribution")

        repeated, repeated_guard = customer()
        repeated._begin_page_error_scope("fnpt_product")
        repeated_guard.off_origin_reads_aborted = 2
        repeated._capture_page_error(Exception("Stripe is not defined"))
        repeated._capture_page_error(Exception("Stripe is not defined"))
        with self.assertRaises(d.FnptPublicRefusal) as caught:
            repeated.require_no_page_errors()
        self.assertEqual(caught.exception.code, "page_error_guard_repeated")

        foreign, foreign_guard = customer()
        foreign._begin_page_error_scope("fnpt_product")
        foreign_guard.off_origin_reads_aborted = 1
        foreign._capture_page_error(Exception("ReferenceError: different failure"))
        with self.assertRaises(d.FnptPublicRefusal) as caught:
            foreign.require_no_page_errors()
        self.assertEqual(caught.exception.code, "page_error_unclassified")

        per_page, per_page_guard = customer()
        for page_category in ("fnpt_product", "stub_control", "pipe_control"):
            per_page._begin_page_error_scope(page_category)
            per_page_guard.off_origin_reads_aborted += 1
            per_page._capture_page_error(Exception("Stripe is not defined"))
            per_page._finish_current_page_errors()
        self.assertEqual(
            [row["page_category"] for row in per_page.page_error_projection()],
            ["fnpt_product", "stub_control", "pipe_control"],
        )

    def test_network_guard_aborts_and_records_non_get_and_all_analytics_shaped_reads(self):
        cache = d._fnpt_cache_buster_url("a" * 32)
        guard = d.FnptNetworkGuard(frozenset({cache}))
        routes = [
            FakeRoute(FakeRequest("POST", cache, "document")),
            FakeRoute(FakeRequest("GET", "https://analytics.invalid/collect", "fetch")),
            FakeRoute(FakeRequest("GET", "https://frpdepots.com/wp-json/analytics", "xhr")),
            FakeRoute(FakeRequest("GET", cache, "document")),
            FakeRoute(FakeRequest("HEAD", "https://frpdepots.com/wp-content/a.css", "stylesheet")),
        ]
        for route in routes:
            guard.handle(route)
        self.assertEqual(routes[0].aborted, ["blockedbyclient"])
        self.assertEqual(routes[1].aborted, ["blockedbyclient"])
        self.assertEqual(routes[2].aborted, ["blockedbyclient"])
        self.assertEqual(routes[3].continued, 1)
        self.assertEqual(routes[4].continued, 1)
        projection = guard.projection()
        self.assertEqual(projection["non_read_requests_aborted"], 1)
        self.assertEqual(projection["non_read_method_counts"]["POST"], 1)
        self.assertEqual(projection["off_origin_reads_aborted"], 1)
        self.assertEqual(projection["disallowed_same_origin_reads_aborted"], 1)
        self.assertFalse(projection["analytics_submission_performed"])
        self.assertFalse(projection["business_write_performed"])

    def test_release_shell_accepts_only_exact_eight_keys_and_wp_localize_string_scalars(self):
        exact_keys = list(sorted(d.FnptCustomerPage._CONFIG_KEYS))

        def shell(config):
            customer = d.FnptCustomerPage(ShellPage(config), ProbeGuard())
            with mock.patch.object(customer, "_require_asset", return_value=None):
                customer.require_release_shell()

        string_shape = {
            "keys": exact_keys,
            "types": {key: "string" for key in d.FnptCustomerPage._CONFIG_KEYS},
        }
        shell(string_shape)

        # This is the old read-only validator assumption. It must now fail because
        # WordPress wp_localize_script stringifies every one of these PHP scalars.
        old_boolean_number_shape = dict(
            string_shape,
            types={
                **string_shape["types"],
                "cartQuoteRequired": "boolean",
                "formId": "number",
            },
        )
        with self.assertRaises(d.FnptPublicRefusal) as caught:
            shell(old_boolean_number_shape)
        self.assertEqual(caught.exception.code, "config_shape")

        with self.assertRaises(d.FnptPublicRefusal):
            shell(dict(string_shape, keys=exact_keys + ["unexpected"]))
        for key in d.FnptCustomerPage._CONFIG_KEYS:
            with self.subTest(key=key), self.assertRaises(d.FnptPublicRefusal):
                shell({"keys": exact_keys, "types": {**string_shape["types"], key: "number"}})

    def test_public_validation_uses_two_cold_contexts_cache_buster_and_canonical_all_60(self):
        probes = []
        allowed_sets = []

        @contextlib.contextmanager
        def session(allowed):
            allowed_sets.append(frozenset(allowed))
            probe = PublicProbe(f"context-{len(probes) + 1}")
            probes.append(probe)
            yield probe

        nonce = "b" * 32
        with mock.patch.object(d, "fnpt_anonymous_session", session):
            findings = self.real_public_validation({"nonce": nonce})
        self.assertEqual(len(probes), 2)
        cache_url = d._fnpt_cache_buster_url(nonce)
        self.assertEqual(probes[0].loads[0], cache_url)
        self.assertEqual(
            probes[1].loads,
            [d.FNPT_PRODUCT_URL, d.STUB_PRODUCT_URL, d.PIPE_PRODUCT_URL],
        )
        self.assertIn(cache_url, allowed_sets[0])
        self.assertEqual(
            allowed_sets[1],
            frozenset({d.FNPT_PRODUCT_URL, d.STUB_PRODUCT_URL, d.PIPE_PRODUCT_URL}),
        )
        self.assertEqual(parse_qs(urlsplit(cache_url).query), {d.FNPT_CACHE_BUSTER_KEY: [nonce]})
        for probe in probes:
            first_sixty = [variation_id for parent, variation_id in probe.selections
                           if parent == 2061][:60]
            self.assertEqual(tuple(first_sixty), d.FNPT_PUBLISHED_VARIATION_IDS)
            self.assertIn((2061, 2088), probe.selections)
            self.assertEqual(probe.plugin_reset_checks, [2061, 2061, 2061, 1368, 1455])
        for probe in probes:
            self.assertEqual([variation for parent, variation in probe.selections if parent == 1368],
                             [2044, 2028, 2044])
            self.assertIn((1455, 2057), probe.selections)
            self.assertEqual(probe.direct_states, [2028])
            self.assertEqual(len(probe.payloads), 7)
        first_payloads = probes[0].payloads
        self.assertNotIn("variation_id", first_payloads[0])
        self.assertEqual(first_payloads[1]["variation_id"], 0)
        self.assertEqual(first_payloads[2]["variation_id"], "2088x")
        self.assertNotEqual(first_payloads[3]["variation_id"],
                            first_payloads[3]["frpdepot_variation_id"])
        self.assertNotIn("frpdepot_variation_id", first_payloads[4])
        self.assertNotIn("frpdepot_quote_required", first_payloads[5])
        self.assertEqual(first_payloads[6]["frpdepot_quote_required"], "false")
        self.assertEqual(findings["contexts_opened"], 2)
        self.assertEqual(findings["fnpt_variation_ids"], list(d.FNPT_PUBLISHED_VARIATION_IDS))
        self.assertEqual(findings["fnpt_variation_selections_total"], 120)
        self.assertTrue(findings["canonical_corrected_behavior"])
        self.assertFalse(any(findings["non_submission"].values()))
        self.assertEqual(
            [context["context"] for context in findings["page_errors"]],
            ["cache_buster", "canonical"],
        )
        for context in findings["page_errors"]:
            self.assertEqual(
                [row["page_category"] for row in context["pages"]],
                ["fnpt_product", "stub_control", "pipe_control"],
            )
        projected = json.dumps(findings["page_errors"], sort_keys=True)
        self.assertNotIn("Stripe is not defined", projected)
        self.assertNotIn("ReferenceError", projected)

    def test_canonical_stale_asset_is_bounded_indeterminate_not_a_pass(self):
        probes = []

        @contextlib.contextmanager
        def session(_allowed):
            probe = PublicProbe(
                f"context-{len(probes) + 1}", canonical_stale=(len(probes) == 1)
            )
            probes.append(probe)
            yield probe

        with mock.patch.object(d, "fnpt_anonymous_session", session):
            with self.assertRaises(d.FnptPublicValidationError) as caught:
                self.real_public_validation({"nonce": "c" * 32})
        self.assertEqual(caught.exception.step, "canonical_fnpt_contract")
        self.assertEqual(caught.exception.code, "asset_hash")
        self.assertEqual(caught.exception.exception_class, "FnptPublicRefusal")

    def test_public_failure_after_upload_is_permanent_bounded_and_never_rolls_back(self):
        path = self.plan()
        admin = self.install_admin(Admin(self.before, self.after))
        failure = d.FnptPublicValidationError(
            "canonical_fnpt_contract", "FnptPublicRefusal", "asset_hash"
        )
        with mock.patch.object(d, "_run_fnpt_public_validation", side_effect=failure):
            with self.assertRaises(d.IndeterminateError):
                self.commit(path)
        self.assertEqual(admin.uploads, 1)
        lock = self.read_json(d.lock_path(path))
        result = self.read_json(d.result_path(path))
        for record in (lock, result):
            self.assertEqual(record["status"], "indeterminate" if record is lock else "INDETERMINATE")
            self.assertEqual(record["stage"], "fnpt_public_validation")
            self.assertEqual(record["step"], "canonical_fnpt_contract")
            self.assertEqual(record["exception_class"], "FnptPublicRefusal")
            self.assertEqual(record["code"], "asset_hash")
            self.assertFalse(record["retry"])
            self.assertFalse(record["rollback"])
            serialized = json.dumps(record).casefold()
            self.assertNotIn("html", serialized)
            self.assertNotIn("customer", serialized)
        with self.assertRaises(d.DeploymentError):
            self.commit(path)
        self.assertEqual(admin.uploads, 1)

    def test_source_has_no_success_pending_or_customer_submission_action(self):
        source = "\n".join((
            inspect.getsource(d.FnptNetworkGuard),
            inspect.getsource(d.FnptCustomerPage),
            inspect.getsource(self.real_public_validation),
            inspect.getsource(d.command_commit_fnpt_display_repair),
        ))
        executable = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#")
        )
        self.assertNotIn("PLUGIN_ROW_VERIFIED_PUBLIC_CHECKS_PENDING", executable)
        self.assertNotIn("public_validation_pending", executable)
        for forbidden in (
            ".add_selected_to_cart", ".goto_checkout", ".submit()", ".fill(", ".type(",
            "cache_invalidation_write",
        ):
            self.assertNotIn(forbidden, executable)


if __name__ == "__main__":
    unittest.main(verbosity=2)
