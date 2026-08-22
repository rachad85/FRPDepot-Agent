from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).with_name("wordpress_media_guard_deployment_tool.py")
spec = importlib.util.spec_from_file_location("media_guard_deploy", MODULE_PATH)
tool = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(tool)


class FakeElement:
    def __init__(self, *, text="", classes="", children=None, attrs=None,
                 evaluate_result=True, on_click=None):
        self.text = text
        self.classes = classes
        self.children = children or {}
        self.attrs = attrs or {}
        self.clicked = 0
        self.files = []
        self.evaluate_result = evaluate_result
        self.on_click = on_click

    def get_attribute(self, name):
        return self.classes if name == "class" else self.attrs.get(name)

    def query_selector(self, selector):
        value = self.children.get(selector)
        return value[0] if isinstance(value, list) and value else value

    def query_selector_all(self, selector):
        value = self.children.get(selector)
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def inner_text(self):
        return self.text

    def text_content(self):
        return self.text

    def click(self, **kwargs):
        self.clicked += 1
        if self.on_click is not None:
            self.on_click()

    def set_input_files(self, path, **kwargs):
        self.files.append(path)

    def evaluate(self, _script):
        return self.evaluate_result


class FakeLocator:
    def __init__(self, text):
        self.text = text

    def inner_text(self, **kwargs):
        return self.text


class FakePage:
    def __init__(self, row=None, update_rows=None):
        self.url = tool.PLUGINS_URL
        self.row = row
        self.update_rows = list(update_rows or [])
        self.handlers = {}
        self.guard_version = f"Version {tool.PLUGIN_VERSION}"
        self.guard_status = "Guard inactive"
        # "auto" mirrors production: only a live 1.0.7 page publishes a capability.
        self.capability = "auto"
        self.families = ["elbow_90", "manway_cover", "open_manway", "pipe", "stub_flange"]
        # A live guard row would render these; an inactive, unresolved-state-free
        # page renders none of them.
        self.guarded_snapshot_controls = 0
        self.completion_controls = 0
        self.recovery_gallery_forms = 0
        self.waits = []
        self.closed = False

    def goto(self, url, **kwargs):
        self.url = url

    def query_selector_all(self, selector):
        if selector == tool.ROW_SELECTOR:
            return [] if self.row is None else [self.row]
        if selector.startswith("tr.plugin-update-tr"):
            return self.update_rows
        if selector == tool.GUARD_CAPABILITY_SELECTOR:
            value = self.capability
            if value == "auto":
                if self.guard_version != f"Version {tool.PLUGIN_VERSION}":
                    return []
                value = json.dumps(tool.EXPECTED_GUARD_CAPABILITY)
            return [] if value is None else [FakeElement(text=value)]
        if selector == "section[data-frpd-family]":
            return [FakeElement(attrs={"data-frpd-family": name})
                    for name in self.families]
        if selector == "#frpd-mg-guarded-snapshot":
            return [FakeElement()] * self.guarded_snapshot_controls
        if selector == "#frpd-mg-complete":
            return [FakeElement()] * self.completion_controls
        if selector == "#frpd-mg-recovery-gallery-form":
            return [FakeElement()] * self.recovery_gallery_forms
        if selector == "#frpd-mg-origin-proof":
            # Only the pinned build exposes the origin-only proof control.
            return ([FakeElement()]
                    if self.guard_version == f"Version {tool.PLUGIN_VERSION}" else [])
        return []

    def query_selector(self, selector):
        return None

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, value):
        self.waits.append(value)

    def close(self):
        self.closed = True

    def on(self, event, callback):
        self.handlers[event] = callback

    def locator(self, selector):
        if selector == "#frpd-mg-version":
            return FakeLocator(self.guard_version)
        if selector == "#frpd-mg-status":
            return FakeLocator(self.guard_status)
        raise AssertionError(selector)


def fixed_row(active: bool, version: str = tool.PLUGIN_VERSION):
    def action_link(action):
        return FakeElement(attrs={"href": (
            f"{tool.PLUGINS_URL}?action={action}&plugin=frpdepot-media-mutation-guard%2F"
            "frpdepot-media-mutation-guard.php&plugin_status=all&paged=1&s=&_wpnonce=abcDEF1234"
        )})
    activate = action_link("activate")
    deactivate = action_link("deactivate")
    children = {
        tool.DEACTIVATE_SELECTOR if active else tool.ACTIVATE_SELECTOR:
            deactivate if active else activate,
    }
    return FakeElement(
        text=f"{tool.PLUGIN_NAME} Version {version}",
        classes="active" if active else "inactive",
        children=children,
    )


class ArtifactTests(unittest.TestCase):
    def test_release_contract_is_exact_105_to_107(self):
        self.assertEqual(tool.CURRENT_PLUGIN_VERSION, "1.0.5")
        self.assertEqual(tool.WITHDRAWN_PLUGIN_VERSION, "1.0.5")
        self.assertEqual(tool.PLUGIN_VERSION, "1.0.7")
        self.assertEqual(tool.TOOL_VERSION, "1.7.0")
        self.assertEqual(tool.SCHEMA_VERSION, 11)
        self.assertEqual(tool.ARTIFACT_PATH.name, "frpdepot-media-mutation-guard-1.0.7.zip")
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("v1.0.3-to-v1.0.4 update", source)
        self.assertNotIn("v1.0.5-to-v1.0.6 update", source)
        self.assertIn("v1.0.5-to-v1.0.7 update", source)

    def test_the_withdrawn_106_artifact_can_never_be_staged_or_deployed(self):
        self.assertEqual(tool.WITHDRAWN_ARTIFACT_VERSIONS, ("1.0.6",))
        self.assertEqual(
            tool.WITHDRAWN_ARTIFACTS,
            {"6a753c570d167075b8fa0a66349ab0a812aa7e222a7aedb2f6d374b913a7010e":
             "WITHDRAWN_NOT_DEPLOYED_NOT_STAGEABLE"})
        self.assertNotIn(tool.ARTIFACT_SHA256, tool.WITHDRAWN_ARTIFACTS)
        withdrawn = (tool.ARTIFACT_PATH.parent
                     / "frpdepot-media-mutation-guard-1.0.6.zip")
        self.assertTrue(withdrawn.is_file(),
                        "the rejected 1.0.6 evidence must be kept unchanged")
        digest = hashlib.sha256(withdrawn.read_bytes()).hexdigest()
        self.assertIn(digest, tool.WITHDRAWN_ARTIFACTS)
        with mock.patch.object(tool, "ARTIFACT_PATH", withdrawn), \
                self.assertRaises(tool.DeploymentError) as caught:
            tool.validate_artifact_payload()
        self.assertIn("REFUSED", str(caught.exception))

    def test_only_the_one_new_transition_is_permitted(self):
        """Exactly installed-active-healthy 1.0.5 may become exactly 1.0.7."""
        self.assertEqual(tool.ACTIONS,
                         frozenset({"install_inactive", "replace_active", "activate", "deactivate"}))
        tool.validate_before("replace_active",
                             tool.project_row(True, True, "1.0.5", False))
        for row in (tool.project_row(True, True, "1.0.6", False),
                    tool.project_row(True, True, "1.0.7", False),
                    tool.project_row(True, True, "1.0.4", False),
                    tool.project_row(True, False, "1.0.5", False),
                    tool.project_row(True, True, "1.0.5", True),
                    tool.project_row(False, None, "", False)):
            with self.subTest(row=row["version"] + str(row["active"]) + str(row["update_marker"])):
                with self.assertRaises(tool.DeploymentError):
                    tool.validate_before("replace_active", row)
        self.assertEqual(tool.expected_after("replace_active"),
                         tool.project_row(True, True, "1.0.7", False))

    def test_every_earlier_plan_and_operation_is_permanently_superseded(self):
        plan_dir = tool.PLAN_DIR
        staged = sorted(plan_dir.glob("*.json")) if plan_dir.is_dir() else []
        self.assertTrue(staged, "the earlier deployment plans must remain on disk")
        for path in staged:
            record = json.loads(path.read_text(encoding="ascii"))
            with self.subTest(plan=path.name):
                self.assertIn(record["sha256"], tool.SUPERSEDED_PLAN_SHA256)
                self.assertIn(record["operation_sha256"], tool.SUPERSEDED_OPERATION_SHA256)

    def test_expected_capability_names_the_one_recovery_contract(self):
        capability = tool.EXPECTED_GUARD_CAPABILITY
        self.assertEqual(capability["plugin_version"], "1.0.7")
        self.assertEqual(capability["state_schema"], 3)
        self.assertEqual(capability["proof_schema"], 3)
        self.assertEqual(capability["manifest_sha256"],
                         tool.EXPECTED_GUARD_MANIFEST_SHA256)
        self.assertEqual(capability["fixed_recovery"]["product_id"], 1397)
        self.assertEqual(capability["fixed_recovery"]["attachment_id"], 7609)
        self.assertEqual(capability["fixed_recovery"]["recoverable_positions"], [2, 3, 4, 5, 6])
        self.assertEqual(capability["fixed_reuse_family"], "stub_flange")
        self.assertEqual(capability["families"],
                         ["elbow_90", "manway_cover", "open_manway", "pipe", "stub_flange"])
        self.assertTrue(all(capability["capabilities"].values()))

    def test_pinned_artifact_is_exact_and_reproducible(self):
        value = tool.validate_artifact()
        self.assertEqual(value["sha256"], tool.ARTIFACT_SHA256)
        self.assertEqual(value["bytes"], tool.ARTIFACT_BYTES)
        self.assertEqual(tuple(value["members"]), tuple(sorted(tool.ARTIFACT_MEMBERS)))
        self.assertEqual(value["member_sha256"], tool.ARTIFACT_MEMBER_SHA256)
        self.assertEqual(value["member_bytes"], tool.ARTIFACT_MEMBER_BYTES)
        self.assertEqual(value["version"], tool.PLUGIN_VERSION)

    def test_the_artifact_members_carry_the_exact_version_and_readme_contract(self):
        """The ZIP is the deliverable, so its own bytes must state the contract."""
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(tool.ARTIFACT_PATH.read_bytes())) as archive:
            php = archive.read(
                f"{tool.PLUGIN_SLUG}/frpdepot-media-mutation-guard.php").decode("utf-8")
            readme = archive.read(f"{tool.PLUGIN_SLUG}/readme.txt").decode("utf-8")
            manifest = json.loads(
                archive.read(f"{tool.PLUGIN_SLUG}/approved-media.json").decode("ascii"))
        self.assertIn(f" * Version: {tool.PLUGIN_VERSION}\n", php)
        self.assertIn(f"define('FRPD_MG_VERSION', '{tool.PLUGIN_VERSION}');", php)
        self.assertIn("define('FRPD_MG_STATE_SCHEMA', 3);", php)
        self.assertIn(f"Stable tag: {tool.PLUGIN_VERSION}\n", readme)
        self.assertIn("one literal Open Manway recovery contract", readme)
        self.assertIn("attachment 7609", readme)
        self.assertIn("origin-only", readme)
        self.assertIn("admin-post", readme)
        self.assertEqual(manifest["schema"], 3)
        self.assertEqual(manifest["fixed_recovery"]["attachment_id"], 7609)
        self.assertEqual(manifest["fixed_recovery"]["product_id"], 1397)
        # The Stub Flange reuse contract and all five families survive unchanged.
        self.assertEqual(manifest["fixed_reuse"]["family"], "stub_flange")
        self.assertEqual(manifest["fixed_reuse"]["attachment_id"], 4849)
        self.assertEqual(sorted(manifest["families"]),
                         ["elbow_90", "manway_cover", "open_manway", "pipe", "stub_flange"])

    def test_only_four_fixed_actions_exist(self):
        self.assertEqual(tool.ACTIONS, frozenset({
            "install_inactive", "replace_active", "activate", "deactivate",
        }))
        with self.assertRaises(SystemExit):
            tool.parser().parse_args(["stage", "--action", "replace"])

    def test_other_plugin_version_and_action_targets_are_refused(self):
        valid = (
            f"{tool.PLUGINS_URL}?action=activate&plugin="
            "frpdepot-media-mutation-guard%2Ffrpdepot-media-mutation-guard.php"
            "&plugin_status=all&paged=1&s=&_wpnonce=abcDEF1234"
        )
        tool.assert_state_action_url(valid, "activate")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_state_action_url(
                valid.replace(
                    "frpdepot-media-mutation-guard%2Ffrpdepot-media-mutation-guard.php",
                    "akismet%2Fakismet.php",
                ),
                "activate",
            )
        for version in ("1.0.1", "1.0.2.1", "1.0.3", "1.0.4", "1.0.6", "1.0.7"):
            with self.subTest(version=version), self.assertRaises(tool.DeploymentError):
                tool.validate_before(
                    "replace_active", tool.project_row(True, True, version, False)
                )
        with self.assertRaises(tool.DeploymentError):
            tool.validate_before(
                "delete", tool.project_row(True, True, tool.PLUGIN_VERSION, False)
            )
        with self.assertRaises(tool.DeploymentError):
            tool.assert_state_action_url(valid, "delete")

    def test_wordpress_action_ids_pin_the_live_name_slug_not_the_directory_slug(self):
        self.assertEqual(tool.ACTION_ID_SLUG, "frp-depot-media-mutation-guard")
        self.assertEqual(tool.ACTIVATE_SELECTOR, "#activate-frp-depot-media-mutation-guard")
        self.assertEqual(tool.DEACTIVATE_SELECTOR, "#deactivate-frp-depot-media-mutation-guard")
        self.assertNotEqual(tool.ACTION_ID_SLUG, tool.PLUGIN_SLUG)

    def test_exact_approval_only(self):
        tool.require_approval("APPROVED")
        for value in ("approved", " APPROVED", "APPROVED ", "YES", ""):
            with self.assertRaises(tool.DeploymentError):
                tool.require_approval(value)

    def test_terminal_evidence_is_published_only_after_full_flush(self):
        with tempfile.TemporaryDirectory() as directory:
            final = Path(directory) / "result.json"
            with mock.patch.object(tool.os, "fsync", side_effect=OSError("modelled fsync failure")):
                with self.assertRaises(OSError):
                    tool.exclusive_json(final, {"status": "verified"})
            self.assertFalse(final.exists())
            self.assertEqual(list(Path(directory).glob("*.pending")), [])
            tool.exclusive_json(final, {"status": "indeterminate_no_retry"})
            self.assertEqual(
                json.loads(final.read_text(encoding="ascii"))["status"],
                "indeterminate_no_retry",
            )
            with self.assertRaises(tool.DeploymentError):
                tool.exclusive_json(final, {"status": "second_write_forbidden"})


class ProjectionTests(unittest.TestCase):
    def test_absent_projection(self):
        admin = tool.AdminPage(FakePage())
        self.assertEqual(admin.read_row(allow_absent=True), tool.project_row(False, None, "", False))

    def test_active_and_inactive_rows(self):
        self.assertEqual(
            tool.AdminPage(FakePage(fixed_row(True))).read_row(),
            tool.project_row(True, True, tool.PLUGIN_VERSION, False),
        )
        self.assertEqual(
            tool.AdminPage(FakePage(fixed_row(False))).read_row(),
            tool.project_row(True, False, tool.PLUGIN_VERSION, False),
        )

    def test_ambiguous_state_refused(self):
        row = fixed_row(False)
        row.classes = "active inactive"
        with self.assertRaises(tool.DeploymentError):
            tool.AdminPage(FakePage(row)).read_row()

    def test_update_marker_refused_by_staging_contract(self):
        before = tool.project_row(True, False, tool.PLUGIN_VERSION, True)
        with self.assertRaises(tool.DeploymentError):
            tool.validate_before("activate", before)

    def test_action_state_contracts_are_exact(self):
        tool.validate_before("install_inactive", tool.project_row(False, None, "", False))
        tool.validate_before("activate", tool.project_row(True, False, tool.PLUGIN_VERSION, False))
        tool.validate_before("deactivate", tool.project_row(True, True, tool.PLUGIN_VERSION, False))
        tool.validate_before("replace_active", tool.project_row(
            True, True, tool.WITHDRAWN_PLUGIN_VERSION, False))
        with self.assertRaises(tool.DeploymentError):
            tool.validate_before("install_inactive", tool.project_row(True, False, tool.PLUGIN_VERSION, False))
        with self.assertRaises(tool.DeploymentError):
            tool.validate_before("activate", tool.project_row(True, True, tool.PLUGIN_VERSION, False))
        with self.assertRaises(tool.DeploymentError):
            tool.validate_before("deactivate", tool.project_row(True, False, tool.PLUGIN_VERSION, False))
        with self.assertRaises(tool.DeploymentError):
            tool.validate_before("replace_active", tool.project_row(
                True, False, tool.WITHDRAWN_PLUGIN_VERSION, False))
        with self.assertRaises(tool.DeploymentError):
            tool.validate_before("replace_active", tool.project_row(
                True, True, tool.WITHDRAWN_PLUGIN_VERSION, True))
        with self.assertRaises(tool.DeploymentError):
            tool.validate_before("replace_active", tool.project_row(False, None, "", False))

    def test_guard_health_is_exact_and_js_clean(self):
        page = FakePage(fixed_row(True))
        proof = tool.AdminPage(page).verify_guard_health()
        self.assertEqual(proof["guard_active"], False)
        self.assertEqual(proof["javascript_errors"], 0)
        page.guard_status = "Guard active"
        with self.assertRaises(tool.IndeterminateError):
            tool.AdminPage(page).verify_guard_health()

    def test_guard_health_rejects_console_error(self):
        page = FakePage(fixed_row(True))
        original_goto = page.goto
        def goto_and_error(url, **kwargs):
            original_goto(url, **kwargs)
            if "console" in page.handlers:
                message = type("Message", (), {"type": "error"})()
                page.handlers["console"](message)
        page.goto = goto_and_error
        with self.assertRaises(tool.IndeterminateError):
            tool.AdminPage(page).verify_guard_health()

    def test_replacement_source_health_is_exact_105_and_inactive_state(self):
        page = FakePage(fixed_row(True, tool.WITHDRAWN_PLUGIN_VERSION))
        page.guard_version = f"Version {tool.WITHDRAWN_PLUGIN_VERSION}"
        page.guard_status = "Guard inactive"
        proof = tool.AdminPage(page).verify_guard_health(tool.CURRENT_PLUGIN_VERSION)
        self.assertEqual(proof, {
            "url": "/wp-admin/tools.php?page=frpd-media-mutation-guard",
            "version": tool.CURRENT_PLUGIN_VERSION,
            "guard_active": False,
            "guard_state_absent": True,
            "families": ["elbow_90", "manway_cover", "open_manway", "pipe", "stub_flange"],
            "state_schema": None,
            "proof_schema": None,
            "manifest_sha256": None,
            "recovery_capability_exact": False,
            "javascript_errors": 0,
        })
        # The live 1.0.5 page must present its exact fixed shape: five families,
        # no capability projection, no origin-proof control, and NO unresolved
        # guard/recovery state controls.
        for field, bad in (("families", ["elbow_90", "open_manway"]),
                           ("guarded_snapshot_controls", 1),
                           ("completion_controls", 1),
                           ("recovery_gallery_forms", 1)):
            with self.subTest(field=field):
                original = getattr(page, field)
                setattr(page, field, bad)
                with self.assertRaises(tool.IndeterminateError):
                    tool.AdminPage(page).verify_guard_health(tool.CURRENT_PLUGIN_VERSION)
                setattr(page, field, original)
        # "Guard active" is exactly the live signal for a media guard or recovery
        # state that has not been resolved; it refuses the replacement.
        page.guard_status = "Guard active"
        with self.assertRaises(tool.IndeterminateError):
            tool.AdminPage(page).verify_guard_health(tool.WITHDRAWN_PLUGIN_VERSION)
        page.guard_status = "Guard unavailable"
        with self.assertRaises(tool.IndeterminateError):
            tool.AdminPage(page).verify_guard_health(tool.WITHDRAWN_PLUGIN_VERSION)
        page.guard_status = "Guard inactive"
        # A 1.0.5 page must NOT already publish the 1.0.6 capability projection.
        page.capability = json.dumps(tool.EXPECTED_GUARD_CAPABILITY)
        with self.assertRaises(tool.IndeterminateError):
            tool.AdminPage(page).verify_guard_health(tool.WITHDRAWN_PLUGIN_VERSION)

    def test_replacement_result_health_requires_the_exact_recovery_capability(self):
        page = FakePage(fixed_row(True))
        proof = tool.AdminPage(page).verify_guard_health()
        self.assertTrue(proof["recovery_capability_exact"])
        self.assertTrue(proof["guard_state_absent"])
        rounds = tool.AdminPage(page).verify_guard_health_rounds()
        self.assertEqual(len(rounds), tool.POST_WRITE_READ_ROUNDS)
        # An absent, drifted or partially-preserved capability all refuse.
        for broken in (None, json.dumps({"plugin_version": "1.0.6"}), "not json"):
            with self.subTest(capability=str(broken)[:24]):
                page.capability = broken
                with self.assertRaises(tool.IndeterminateError):
                    tool.AdminPage(page).verify_guard_health()
        drifted = json.loads(json.dumps(tool.EXPECTED_GUARD_CAPABILITY))
        drifted["families"] = ["open_manway"]
        page.capability = json.dumps(drifted)
        with self.assertRaises(tool.IndeterminateError):
            tool.AdminPage(page).verify_guard_health()
        drifted = json.loads(json.dumps(tool.EXPECTED_GUARD_CAPABILITY))
        drifted["fixed_reuse_family"] = "open_manway"
        page.capability = json.dumps(drifted)
        with self.assertRaises(tool.IndeterminateError):
            tool.AdminPage(page).verify_guard_health()
        drifted = json.loads(json.dumps(tool.EXPECTED_GUARD_CAPABILITY))
        drifted["fixed_recovery"]["attachment_id"] = 7610
        page.capability = json.dumps(drifted)
        with self.assertRaises(tool.IndeterminateError):
            tool.AdminPage(page).verify_guard_health()

    def test_network_admin_and_other_admin_routes_are_refused(self):
        tool.assert_admin_url(tool.PLUGINS_URL, mode="plugins")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_admin_url("https://frpdepots.com/wp-admin/network/plugins.php", mode="state_result")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_admin_url("https://frpdepots.com/wp-admin/users.php", mode="state_result")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_admin_url(tool.PLUGINS_URL + "?action=activate", mode="plugins")


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patchers = [
            mock.patch.object(tool, "PLAN_DIR", root / "plans"),
            mock.patch.object(tool, "REGISTRY_KEY", root / "state" / "registry.key"),
            mock.patch.object(tool, "REGISTRY_DIR", root / "state" / "stages"),
            mock.patch.object(tool, "ATTEMPT_DIR", root / "state" / "attempts"),
            mock.patch.object(tool, "RESULT_DIR", root / "state" / "results"),
            mock.patch.object(tool, "RECEIPTS", root / "receipts.jsonl"),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.artifact = tool.validate_artifact()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_authenticated_immutable_plan_round_trip(self):
        before = tool.project_row(False, None, "", False)
        path, staged = tool.stage("install_inactive", before, self.artifact)
        loaded_path, loaded = tool.load_plan(str(path))
        self.assertEqual(loaded_path, path)
        self.assertEqual(loaded, staged)
        self.assertEqual(loaded["writes_if_committed"], ["one fixed plugin ZIP upload; plugin remains inactive"])
        self.assertTrue(tool.REGISTRY_KEY.is_file())
        self.assertEqual(len(tool.REGISTRY_KEY.read_bytes()), 32)

    def test_commit_requires_two_minutes_of_authorization_after_browser_preparation(self):
        now = tool.utc_now()
        with mock.patch.object(tool, "utc_now", return_value=now):
            with self.assertRaises(tool.DeploymentError):
                tool.assert_commit_execution_window({
                    "expires_utc": (now + tool.COMMIT_EXPIRY_MARGIN).isoformat()
                })
            tool.assert_commit_execution_window({
                "expires_utc": (now + tool.COMMIT_EXPIRY_MARGIN + tool.timedelta(seconds=1)).isoformat()
            })
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertLess(source.index("assert_commit_execution_window(plan)"),
                        source.index("exclusive_json(attempt_path(operation)"))

    def test_plan_tamper_refused(self):
        path, _ = tool.stage("install_inactive", tool.project_row(False, None, "", False), self.artifact)
        value = json.loads(path.read_text(encoding="ascii"))
        value["risk"] = "changed"
        path.write_text(json.dumps(value), encoding="ascii")
        with self.assertRaises(tool.DeploymentError):
            tool.load_plan(str(path))

    def test_registry_tamper_refused(self):
        path, plan = tool.stage("install_inactive", tool.project_row(False, None, "", False), self.artifact)
        registry = tool.stage_registry_path(plan["sha256"])
        value = json.loads(registry.read_text(encoding="ascii"))
        value["hmac_sha256"] = "0" * 64
        registry.write_text(json.dumps(value), encoding="ascii")
        with self.assertRaises(tool.DeploymentError):
            tool.load_plan(str(path))

    def test_stable_operation_ignores_plan_nonce(self):
        before = tool.project_row(False, None, "", False)
        first_path, first = tool.stage("install_inactive", before, self.artifact)
        second_path, second = tool.stage("install_inactive", before, self.artifact)
        self.assertNotEqual(first_path, second_path)
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["operation_sha256"], second["operation_sha256"])

    def test_literal_replacement_release_is_exact_v105_to_v107(self):
        self.assertEqual(tool.CURRENT_PLUGIN_VERSION, "1.0.5")
        self.assertEqual(tool.PLUGIN_VERSION, "1.0.7")
        self.assertEqual(tool.TOOL_VERSION, "1.7.0")
        self.assertEqual(tool.SCHEMA_VERSION, 11)
        self.assertEqual(tool.ARTIFACT_SHA256, "a1f6bf204e443dea9008699abcaf96e7da868a894a5f569215c572c9963ab2d1")
        self.assertEqual(tool.ARTIFACT_BYTES, 35656)

    def test_superseded_plan_hash_refuses_before_artifact_registry_or_browser(self):
        path, staged = tool.stage(
            "replace_active",
            tool.project_row(True, True, tool.WITHDRAWN_PLUGIN_VERSION, False),
            self.artifact,
        )
        old_hash = next(iter(tool.SUPERSEDED_PLAN_SHA256))
        forged = dict(staged)
        forged["sha256"] = old_hash
        created = tool.datetime.fromisoformat(forged["created_utc"])
        old_path = tool.plan_path(created, forged["action"], old_hash)
        old_path.write_text(json.dumps(forged), encoding="ascii")
        with mock.patch.object(tool, "digest_for", return_value=old_hash), \
                mock.patch.object(tool, "validate_artifact") as artifact, \
                mock.patch.object(tool, "admin_session") as browser:
            with self.assertRaisesRegex(tool.DeploymentError, "superseded guard artifact"):
                tool.load_plan(str(old_path))
        artifact.assert_not_called()
        browser.assert_not_called()

    def test_each_action_discloses_its_exact_write_sequence(self):
        cases = {
            "install_inactive": tool.project_row(False, None, "", False),
            "activate": tool.project_row(True, False, tool.PLUGIN_VERSION, False),
            "deactivate": tool.project_row(True, True, tool.PLUGIN_VERSION, False),
            "replace_active": tool.project_row(
                True, True, tool.WITHDRAWN_PLUGIN_VERSION, False),
        }
        for action, before in cases.items():
            with self.subTest(action=action):
                _, plan = tool.stage(action, before, self.artifact)
                self.assertEqual(len(plan["writes_if_committed"]), 1)
                if action == "replace_active":
                    self.assertIn("then one exact replace-current click",
                                  plan["writes_if_committed"][0])
                    self.assertIn("not atomic", plan["risk"])

    def test_bad_approval_refuses_before_plan_key_or_browser(self):
        args = argparse.Namespace(approval="approved", plan="missing")
        with mock.patch.object(tool, "load_plan") as load, mock.patch.object(tool, "admin_session") as session:
            with self.assertRaises(tool.DeploymentError):
                tool.command_commit(args)
        load.assert_not_called()
        session.assert_not_called()

    def test_pre_attempt_drift_refuses_without_burning_operation(self):
        before = tool.project_row(False, None, "", False)
        path, plan = tool.stage("install_inactive", before, self.artifact)

        class DriftAdmin:
            def goto_plugins(self): pass
            def read_row(self, allow_absent=True):
                return tool.project_row(True, False, tool.PLUGIN_VERSION, False)

        @contextlib.contextmanager
        def session(_purpose):
            yield DriftAdmin()

        with mock.patch.object(tool, "admin_session", session):
            with self.assertRaises(tool.DeploymentError):
                tool.command_commit(argparse.Namespace(approval="APPROVED", plan=str(path)))
        self.assertFalse(tool.attempt_path(plan["operation_sha256"]).exists())
        self.assertFalse(tool.result_path(plan["operation_sha256"]).exists())

    def test_verified_install_locks_before_one_write_and_replay_refuses(self):
        before = tool.project_row(False, None, "", False)
        path, plan = tool.stage("install_inactive", before, self.artifact)
        events = []

        class InstallAdmin:
            def goto_plugins(self): events.append("goto")
            def read_row(self, allow_absent=True): return before
            def prepare_install(self): events.append("prepare"); return object(), object()
            def execute_install(self, chooser, submit, artifact_raw):
                self_outer.assertEqual(hashlib.sha256(artifact_raw).hexdigest(), tool.ARTIFACT_SHA256)
                self_outer.assertTrue(tool.attempt_path(plan["operation_sha256"]).exists())
                events.append("write")
                return tool.expected_after("install_inactive")

        self_outer = self
        @contextlib.contextmanager
        def session(_purpose):
            yield InstallAdmin()

        with mock.patch.object(tool, "admin_session", session), mock.patch.object(tool, "print_json"):
            tool.command_commit(argparse.Namespace(approval="APPROVED", plan=str(path)))
        self.assertEqual(events, ["goto", "prepare", "write"])
        result = json.loads(tool.result_path(plan["operation_sha256"]).read_text(encoding="ascii"))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["writes"], 1)
        with mock.patch.object(tool, "admin_session", session), mock.patch.object(tool, "print_json"):
            with self.assertRaises(tool.DeploymentError):
                tool.command_commit(argparse.Namespace(approval="APPROVED", plan=str(path)))
        self.assertEqual(events, ["goto", "prepare", "write"])

    def test_exception_after_attempt_is_indeterminate_and_no_retry(self):
        before = tool.project_row(False, None, "", False)
        path, plan = tool.stage("install_inactive", before, self.artifact)

        class FailingAdmin:
            def goto_plugins(self): pass
            def read_row(self, allow_absent=True): return before
            def prepare_install(self): return object(), object()
            def execute_install(self, chooser, submit, artifact_raw): raise RuntimeError("submission uncertain")

        @contextlib.contextmanager
        def session(_purpose):
            yield FailingAdmin()

        with mock.patch.object(tool, "admin_session", session):
            with self.assertRaises(RuntimeError):
                tool.command_commit(argparse.Namespace(approval="APPROVED", plan=str(path)))
        result = json.loads(tool.result_path(plan["operation_sha256"]).read_text(encoding="ascii"))
        self.assertEqual(result["status"], "indeterminate_no_retry")
        self.assertTrue(tool.attempt_path(plan["operation_sha256"]).exists())

    def test_verified_replacement_locks_before_upload_and_preserves_active_state(self):
        before = tool.project_row(True, True, tool.WITHDRAWN_PLUGIN_VERSION, False)
        path, plan = tool.stage("replace_active", before, self.artifact)
        events = []

        def complete_health(expected_version):
            """The COMPLETE proof the production reader returns; v1.6.0's stub
            returned a two-field stand-in, which is exactly why its test accepted
            an incomplete health check."""
            is_target = expected_version == tool.PLUGIN_VERSION
            return {
                "url": "/wp-admin/tools.php?page=frpd-media-mutation-guard",
                "version": expected_version, "guard_active": False,
                "guard_state_absent": True,
                "families": ["elbow_90", "manway_cover", "open_manway", "pipe",
                             "stub_flange"],
                "state_schema": tool.EXPECTED_GUARD_STATE_SCHEMA if is_target else None,
                "proof_schema": tool.EXPECTED_GUARD_PROOF_SCHEMA if is_target else None,
                "manifest_sha256": (tool.EXPECTED_GUARD_MANIFEST_SHA256 if is_target
                                    else None),
                "recovery_capability_exact": is_target,
                "javascript_errors": 0,
            }

        class ReplaceAdmin:
            def goto_plugins(self): events.append("goto")
            def read_row(self, allow_absent=True): return before
            def verify_guard_health(self, expected_version=tool.PLUGIN_VERSION):
                events.append(f"health:{expected_version}")
                return complete_health(expected_version)
            def verify_deployment_round(self, expected_version=tool.PLUGIN_VERSION):
                health = self.verify_guard_health(expected_version)
                events.append("round_row")
                return {"row": tool.project_row(True, True, expected_version, False),
                        "health": health}
            def verify_guard_health_rounds(self, expected_version=tool.PLUGIN_VERSION,
                                           rounds=tool.POST_WRITE_READ_ROUNDS):
                return [self.verify_deployment_round(expected_version)
                        for _ in range(rounds)]
            def read_bounded(self, wanted):
                events.append("read_bounded")
                return wanted
            def prepare_install(self): events.append("prepare"); return object(), object()
            def execute_replace(self, chooser, submit, artifact_raw, expected_before,
                                artifact=None, plan=None):
                self_outer.assertEqual(expected_before, before)
                self_outer.assertEqual(artifact, self_outer.artifact)
                self_outer.assertIsNotNone(plan)
                self_outer.assertTrue(tool.attempt_path(
                    plan["operation_sha256"]).exists())
                self_outer.assertEqual(hashlib.sha256(artifact_raw).hexdigest(),
                                       tool.ARTIFACT_SHA256)
                events.append("upload_then_replace")
                return {"comparison_name": tool.PLUGIN_NAME,
                        "comparison_current_version": tool.CURRENT_PLUGIN_VERSION,
                        "comparison_uploaded_version": tool.PLUGIN_VERSION,
                        "wordpress_success_marker_exact": True,
                        "active_withdrawn_version_reverified_immediately_before_overwrite": True,
                        "after": tool.expected_after("replace_active")}

        self_outer = self
        @contextlib.contextmanager
        def session(_purpose):
            yield ReplaceAdmin()

        with mock.patch.object(tool, "admin_session", session), mock.patch.object(tool, "print_json"):
            tool.command_commit(argparse.Namespace(approval="APPROVED", plan=str(path)))
        self.assertEqual(events, [
            "goto", f"health:{tool.CURRENT_PLUGIN_VERSION}", "goto", "prepare",
            "upload_then_replace",
            f"health:{tool.PLUGIN_VERSION}", "round_row",
            f"health:{tool.PLUGIN_VERSION}", "round_row",
            f"health:{tool.PLUGIN_VERSION}", "round_row",
            "goto", "read_bounded",
        ])
        result = json.loads(tool.result_path(plan["operation_sha256"]).read_text(encoding="ascii"))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["after"], tool.project_row(True, True, tool.PLUGIN_VERSION, False))
        self.assertEqual(result["writes"], 2)
        # Three independent fresh COMPLETE reads must each prove the exact plugin
        # row AND the exact guard health together.
        rounds = result["replacement"]["health_rounds"]
        self.assertEqual(len(rounds), tool.POST_WRITE_READ_ROUNDS)
        for round_proof in rounds:
            self.assertEqual(round_proof["row"],
                             tool.project_row(True, True, tool.PLUGIN_VERSION, False))
            self.assertEqual(round_proof["health"]["version"], tool.PLUGIN_VERSION)
            self.assertTrue(round_proof["health"]["recovery_capability_exact"])
            self.assertTrue(round_proof["health"]["guard_state_absent"])
            self.assertEqual(round_proof["health"]["javascript_errors"], 0)
            self.assertEqual(round_proof["health"]["state_schema"],
                             tool.EXPECTED_GUARD_STATE_SCHEMA)
            self.assertEqual(round_proof["health"]["manifest_sha256"],
                             tool.EXPECTED_GUARD_MANIFEST_SHA256)

    def test_a_post_round_row_or_health_mismatch_is_indeterminate(self):
        """Each of the three rounds must prove BOTH the row and the health."""
        for broken in ("row", "version", "capability", "state", "javascript"):
            with self.subTest(broken=broken):
                page = FakePage(fixed_row(True, tool.PLUGIN_VERSION))
                if broken == "row":
                    page.row = fixed_row(False, tool.PLUGIN_VERSION)
                elif broken == "version":
                    page.guard_version = "Version 1.0.6"
                elif broken == "capability":
                    page.capability = None
                elif broken == "state":
                    page.recovery_gallery_forms = 1
                elif broken == "javascript":
                    page.guard_status = "Guard unavailable"
                with self.assertRaises(tool.IndeterminateError):
                    tool.AdminPage(page).verify_deployment_round()

    def test_a_complete_post_round_proves_row_and_health_together(self):
        page = FakePage(fixed_row(True, tool.PLUGIN_VERSION))
        proof = tool.AdminPage(page).verify_deployment_round()
        self.assertEqual(proof["row"],
                         tool.project_row(True, True, tool.PLUGIN_VERSION, False))
        self.assertEqual(proof["health"]["version"], tool.PLUGIN_VERSION)
        self.assertTrue(proof["health"]["recovery_capability_exact"])


class SharedPredicateTests(unittest.TestCase):
    """ONE normalized predicate, at stage, at commit preflight, and immediately
    before the first upload form submission.

    v1.6.0 had `validate_before()` but used it only while staging: commit
    re-implemented a subset inline, and `AdminPage.execute_replace()` selected
    and submitted the artifact with nothing fresher than the preflight.
    """

    def setUp(self):
        self.artifact = tool.validate_artifact()
        self.before = tool.project_row(True, True, tool.CURRENT_PLUGIN_VERSION, False)
        self.plan = {"action": "replace_active", "before": self.before,
                     "after_expected": tool.expected_after("replace_active"),
                     "artifact": self.artifact}
        self.health = {"url": "/wp-admin/tools.php?page=frpd-media-mutation-guard",
                       "version": tool.CURRENT_PLUGIN_VERSION, "guard_active": False,
                       "guard_state_absent": True, "javascript_errors": 0}

    def test_the_exact_accepted_evidence_passes(self):
        tool.assert_deployment_eligibility(
            "replace_active", self.before, artifact=self.artifact, plan=self.plan,
            health=self.health)

    def test_stage_and_the_predicate_are_the_same_gate(self):
        tool.validate_before("replace_active", self.before)
        source = MODULE_PATH.read_text(encoding="utf-8")
        # stage -> validate_before -> assert_deployment_eligibility, plus the
        # commit preflight and the pre-submit call inside execute_replace.
        self.assertEqual(source.count("assert_deployment_eligibility("), 5)

    def test_every_stage_accepted_field_mutated_refuses_before_any_write(self):
        """Mutate each accepted field and prove a FREE refusal."""
        row_mutations = {
            "absent": tool.project_row(False, None, "", False),
            "inactive": tool.project_row(True, False, tool.CURRENT_PLUGIN_VERSION, False),
            "already_the_target": tool.project_row(True, True, tool.PLUGIN_VERSION, False),
            "the_withdrawn_build": tool.project_row(True, True, "1.0.6", False),
            "another_version": tool.project_row(True, True, "1.0.4", False),
            "update_marker": tool.project_row(True, True, tool.CURRENT_PLUGIN_VERSION, True),
        }
        for label, row in row_mutations.items():
            with self.subTest(field=f"row:{label}"), \
                    self.assertRaises(tool.DeploymentError):
                tool.assert_deployment_eligibility(
                    "replace_active", row, artifact=self.artifact, plan=self.plan,
                    health=self.health)

        artifact_mutations = {
            "sha256": {**self.artifact, "sha256": "f" * 64},
            "bytes": {**self.artifact, "bytes": self.artifact["bytes"] + 1},
            "version": {**self.artifact, "version": "1.0.6"},
            "members": {**self.artifact, "members": ["x"]},
            "member_sha256": {**self.artifact, "member_sha256": {}},
            "member_bytes": {**self.artifact, "member_bytes": {}},
            "withdrawn_hash": {
                **self.artifact,
                "sha256": next(iter(tool.WITHDRAWN_ARTIFACTS))},
        }
        for label, artifact in artifact_mutations.items():
            with self.subTest(field=f"artifact:{label}"), \
                    self.assertRaises(tool.DeploymentError):
                tool.assert_deployment_eligibility(
                    "replace_active", self.before, artifact=artifact, plan=self.plan,
                    health=self.health)

        plan_mutations = {
            "action": {**self.plan, "action": "activate"},
            "before": {**self.plan, "before": tool.project_row(True, False, "1.0.5", False)},
            "after_expected": {**self.plan,
                               "after_expected": tool.project_row(True, False,
                                                                  tool.PLUGIN_VERSION, False)},
            "artifact": {**self.plan, "artifact": {**self.artifact, "sha256": "0" * 64}},
        }
        for label, plan in plan_mutations.items():
            with self.subTest(field=f"plan:{label}"), \
                    self.assertRaises(tool.DeploymentError):
                tool.assert_deployment_eligibility(
                    "replace_active", self.before, artifact=self.artifact, plan=plan,
                    health=self.health)

        health_mutations = {
            "version": {**self.health, "version": tool.PLUGIN_VERSION},
            "guard_active": {**self.health, "guard_active": True},
            "guard_state": {**self.health, "guard_state_absent": False},
            "javascript": {**self.health, "javascript_errors": 1},
        }
        for label, health in health_mutations.items():
            with self.subTest(field=f"health:{label}"), \
                    self.assertRaises(tool.DeploymentError):
                tool.assert_deployment_eligibility(
                    "replace_active", self.before, artifact=self.artifact,
                    plan=self.plan, health=health)

    def test_a_refusal_is_free_and_never_indeterminate(self):
        with self.assertRaises(tool.DeploymentError) as caught:
            tool.assert_deployment_eligibility(
                "replace_active", tool.project_row(True, False, "1.0.5", False))
        self.assertNotIsInstance(caught.exception, tool.IndeterminateError)

    def test_the_pre_submit_predicate_runs_before_any_file_is_chosen(self):
        """A row that drifts between preflight and submit uploads NOTHING."""
        chooser = FakeElement()
        submit = FakeElement()
        page = FakePage(fixed_row(True, tool.CURRENT_PLUGIN_VERSION))
        page.url = f"{tool.ORIGIN}/wp-admin/update.php?action=upload-plugin"
        drifted = FakePage(fixed_row(True, tool.PLUGIN_VERSION))
        drifted.guard_version = f"Version {tool.CURRENT_PLUGIN_VERSION}"
        page.context = type("Ctx", (), {"new_page": lambda self: drifted})()
        with self.assertRaises(tool.DeploymentError) as caught:
            tool.AdminPage(page).execute_replace(
                chooser, submit, tool.ARTIFACT_PATH.read_bytes(),
                tool.project_row(True, True, tool.CURRENT_PLUGIN_VERSION, False))
        self.assertNotIsInstance(caught.exception, tool.IndeterminateError)
        self.assertEqual(chooser.files, [])
        self.assertEqual(submit.clicked, 0)
        self.assertTrue(drifted.closed)


class SourceSurfaceTests(unittest.TestCase):
    def test_no_arbitrary_replace_delete_or_mail_write_call(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("delete_plugin", source)
        self.assertNotIn("wp-json", source)
        self.assertNotIn("/products/", source)
        self.assertNotIn("send_mail", source)
        self.assertNotIn("requests.post", source)
        self.assertNotIn("requests.put", source)
        self.assertNotIn("requests.delete", source)

    def test_exact_replace_comparison_and_route_are_required(self):
        def cells(label, current, uploaded):
            return FakeElement(children={"td": [
                FakeElement(text=label), FakeElement(text=current), FakeElement(text=uploaded),
            ]})
        table = FakeElement(children={"tr": [
            cells("Plugin name", tool.PLUGIN_NAME, tool.PLUGIN_NAME),
            cells("Version", tool.WITHDRAWN_PLUGIN_VERSION, tool.PLUGIN_VERSION),
        ]})
        chooser = FakeElement()
        page = FakePage(fixed_row(True, tool.WITHDRAWN_PLUGIN_VERSION))
        page.url = f"{tool.ORIGIN}/wp-admin/update.php?action=upload-plugin"

        def overwrite():
            page.row = fixed_row(True, tool.PLUGIN_VERSION)
            page.url = (f"{tool.ORIGIN}/wp-admin/update.php?action=upload-plugin"
                        "&overwrite=update-plugin&package=fixed.zip&_wpnonce=abcDEF1234")
        link = FakeElement(on_click=overwrite)
        submit = FakeElement()
        original_query = page.query_selector_all
        def query(selector):
            if selector == "table.update-from-upload-comparison": return [table]
            if selector == tool.OVERWRITE_SELECTOR: return [link]
            if selector == ".wrap p": return [FakeElement(text=tool.OVERWRITE_SUCCESS_MARKER)]
            return original_query(selector)
        page.query_selector_all = query
        page.evaluate = lambda _script: True
        audit_page = FakePage(fixed_row(True, tool.WITHDRAWN_PLUGIN_VERSION))
        audit_page.guard_version = f"Version {tool.WITHDRAWN_PLUGIN_VERSION}"
        page.context = type("FakeContext", (), {"new_page": lambda self: audit_page})()
        result = tool.AdminPage(page).execute_replace(
            chooser, submit, tool.ARTIFACT_PATH.read_bytes(),
            tool.project_row(True, True, tool.WITHDRAWN_PLUGIN_VERSION, False),
        )
        self.assertEqual(result["after"], tool.expected_after("replace_active"))
        self.assertEqual(link.clicked, 1)
        self.assertEqual(len(chooser.files), 1)
        self.assertIsInstance(chooser.files[0], dict)
        self.assertEqual(hashlib.sha256(chooser.files[0]["buffer"]).hexdigest(),
                         tool.ARTIFACT_SHA256)
        self.assertTrue(audit_page.closed)
        self.assertTrue(result["active_withdrawn_version_reverified_immediately_before_overwrite"])

        bad_link = FakeElement(evaluate_result=False)
        page.row = fixed_row(True, tool.WITHDRAWN_PLUGIN_VERSION)
        page.url = f"{tool.ORIGIN}/wp-admin/update.php?action=upload-plugin"
        page.query_selector_all = lambda selector: (
            [table] if selector == "table.update-from-upload-comparison"
            else [bad_link] if selector == tool.OVERWRITE_SELECTOR else []
        )
        with self.assertRaises(tool.IndeterminateError):
            tool.AdminPage(page).execute_replace(
                FakeElement(), FakeElement(), tool.ARTIFACT_PATH.read_bytes(),
                tool.project_row(True, True, tool.WITHDRAWN_PLUGIN_VERSION, False),
            )
        self.assertEqual(bad_link.clicked, 0)

        drift_link = FakeElement()
        drift_audit = FakePage(fixed_row(False, tool.WITHDRAWN_PLUGIN_VERSION))
        drift_audit.guard_version = f"Version {tool.WITHDRAWN_PLUGIN_VERSION}"
        page.context = type("DriftContext", (), {"new_page": lambda self: drift_audit})()
        page.url = f"{tool.ORIGIN}/wp-admin/update.php?action=upload-plugin"
        page.query_selector_all = lambda selector: (
            [table] if selector == "table.update-from-upload-comparison"
            else [drift_link] if selector == tool.OVERWRITE_SELECTOR else []
        )
        drift_chooser = FakeElement()
        with self.assertRaises(tool.DeploymentError) as caught:
            tool.AdminPage(page).execute_replace(
                drift_chooser, FakeElement(), tool.ARTIFACT_PATH.read_bytes(),
                tool.project_row(True, True, tool.CURRENT_PLUGIN_VERSION, False),
            )
        # 1.7.0 catches this at the FRESH pre-submit predicate, so it is a free
        # refusal with no upload at all rather than an indeterminate attempt.
        self.assertNotIsInstance(caught.exception, tool.IndeterminateError)
        self.assertEqual(drift_chooser.files, [])
        self.assertEqual(drift_link.clicked, 0)
        self.assertTrue(drift_audit.closed)

    def test_install_preparation_matches_live_form_without_id_and_has_no_caller_selected_path(self):
        class Chooser:
            def __init__(self): self.value = None
            def set_input_files(self, value, **kwargs): self.value = value
        class Submit:
            def click(self, **kwargs): pass
        chooser = Chooser(); submit = Submit()
        nonce = FakeElement(attrs={"value": "abcDEF1234"})
        form = FakeElement(
            classes="wp-upload-form",
            attrs={"action": "update.php?action=upload-plugin"},
            children={
                'input[type="file"][name="pluginzip"]': [chooser],
                "#install-plugin-submit": [submit],
                'input[type="hidden"][name="_wpnonce"]': [nonce],
            },
        )
        fake = FakePage()
        fake.url = tool.UPLOAD_URL
        fake.query_selector_all = lambda selector: [form] if selector == tool.UPLOAD_FORM_SELECTOR else []
        admin = tool.AdminPage(fake)
        got_chooser, got_submit = admin.prepare_install()
        self.assertIs(got_chooser, chooser)
        self.assertIs(got_submit, submit)
        self.assertIsNone(form.get_attribute("id"))
        self.assertEqual(tool.UPLOAD_FORM_SELECTOR, "form.wp-upload-form")

    def test_install_preparation_refuses_missing_or_duplicate_live_forms(self):
        fake = FakePage()
        fake.url = tool.UPLOAD_URL
        admin = tool.AdminPage(fake)
        with self.assertRaises(tool.DeploymentError):
            admin.prepare_install()

        form = FakeElement(classes="wp-upload-form")
        fake.query_selector_all = lambda selector: [form, form] if selector == tool.UPLOAD_FORM_SELECTOR else []
        with self.assertRaises(tool.DeploymentError):
            admin.prepare_install()

    def test_action_href_and_result_routes_are_closed(self):
        valid = (f"{tool.PLUGINS_URL}?action=deactivate&plugin=frpdepot-media-mutation-guard%2F"
                 "frpdepot-media-mutation-guard.php&plugin_status=all&paged=1&s=&_wpnonce=abcDEF1234")
        tool.assert_state_action_url(valid, "deactivate")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_state_action_url(valid.replace("action=deactivate", "action=delete-selected"), "deactivate")
        tool.assert_admin_url(f"{tool.PLUGINS_URL}?plugin_status=all&paged=1&s=", mode="state_result")
        tool.assert_admin_url(f"{tool.PLUGINS_URL}?deactivate=true&plugin_status=all&paged=1&s=", mode="state_result")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_admin_url(f"{tool.PLUGINS_URL}?plugin_status=all&paged=2&s=", mode="state_result")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_admin_url(f"{tool.PLUGINS_URL}?action=delete-selected&checked[]=akismet.php", mode="state_result")
        with self.assertRaises(tool.DeploymentError):
            tool.assert_admin_url(f"{tool.ORIGIN}/wp-admin/update.php?action=upload-plugin&unrelated=1", mode="install_result")

    def test_artifact_payload_is_exact_bytes_and_source_never_uploads_a_path(self):
        artifact, raw = tool.validate_artifact_payload()
        self.assertEqual(artifact, tool.validate_artifact())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), tool.ARTIFACT_SHA256)
        self.assertEqual(len(raw), tool.ARTIFACT_BYTES)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("set_input_files(str(", source)
        self.assertIn('"buffer": artifact_raw', source)

    @staticmethod
    def temp_path():
        return tempfile.gettempdir()


if __name__ == "__main__":
    unittest.main(verbosity=2)
