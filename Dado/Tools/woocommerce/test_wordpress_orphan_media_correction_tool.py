from __future__ import annotations

import argparse
import ast
import contextlib
from datetime import timedelta
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).with_name("wordpress_orphan_media_correction_tool.py")
DOM_CONTRACT_FIXTURE = (
    Path(__file__).with_name("testdata")
    / "wordpress_7_0_3_attachment_dom_contract.json"
)
SPEC = importlib.util.spec_from_file_location("wordpress_orphan_media_correction_tool_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class FixedContractTests(unittest.TestCase):
    def test_fixed_ids_and_names_are_exact(self):
        self.assertEqual(m.TARGET_IDS, (5521, 5523, 5525, 5527))
        self.assertEqual([row["filename"] for row in m.TARGETS], [
            "01_manway_real_hero.png",
            "02_manway_real_alternate.png",
            "03_manway_real_laminate_detail.png",
            "04_manway_real_bore_flange_detail.png",
        ])
        self.assertEqual(
            [row["attachment_id"] for row in m.PROTECTED_SURVIVOR_GALLERY],
            [5823, 5824, 5825, 5826],
        )
        self.assertEqual(
            [row["fixed_position"] for row in m.PROTECTED_SURVIVOR_GALLERY],
            [1, 2, 3, 4],
        )
        self.assertTrue(all(row["source_url"].endswith("-1.png")
                            for row in m.PROTECTED_SURVIVOR_GALLERY))

    def test_successor_guard_contract_has_no_old_survivor_fixed_matches(self):
        self.assertEqual(m.TOOL_VERSION, "1.0.1")
        self.assertEqual(m.SCHEMA_VERSION, 2)
        manifest = json.loads(
            m.family_media.GUARD_RUNTIME_MANIFEST_PATH.read_text(encoding="utf-8")
        )
        open_manway = manifest["families"]["open_manway"]["images"]
        self.assertEqual([row["filename"] for row in open_manway], [
            "01_manway_premium_hero.png",
            "02_manway_top_oblique.png",
            "03_manway_low_side_angle.png",
            "04_manway_flange_bore_detail.png",
            "05_manway_opposite_face.png",
            "06_manway_laminate_detail.png",
        ])
        self.assertTrue(
            {row["sha256"] for row in m.TARGETS}.isdisjoint(
                {row["sha256"] for row in open_manway}
            )
        )

    def test_source_operation_is_fixed(self):
        self.assertEqual(
            m.SOURCE_OPERATION_SHA256,
            "877ff133b0e4fbf560b3be5877b755c72e5c33dc217c7e4affb23c1a314e2a26",
        )
        evidence = m.validate_fixed_contract()
        self.assertEqual(evidence["source_result_sha256"], m.SOURCE_RESULT_SHA256)
        self.assertEqual(evidence["source_status"], "INDETERMINATE_NO_RETRY")
        self.assertFalse(evidence["source_product_may_have_changed"])
        self.assertIsNone(evidence["source_gallery_payload"])
        self.assertFalse(evidence["source_delete_performed"])
        self.assertEqual(evidence["predecessor_operation_sha256"],
                         m.PREDECESSOR_OPERATION_SHA256)
        self.assertEqual(evidence["predecessor_event_sha256"],
                         m.PREDECESSOR_EVENT_SHA256)

    def test_attachment_dom_contract_has_pinned_wordpress_core_provenance(self):
        fixture = json.loads(DOM_CONTRACT_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(fixture["wordpress_version"], m.WORDPRESS_CORE_VERSION)
        self.assertEqual(
            fixture["sources"]["media_list"]["sha256"],
            m.WORDPRESS_MEDIA_LIST_SOURCE_SHA256,
        )
        self.assertEqual(
            fixture["sources"]["edit_form"]["sha256"],
            m.WORDPRESS_EDIT_FORM_SOURCE_SHA256,
        )
        self.assertEqual(
            fixture["sources"]["attachment_metadata"]["sha256"],
            m.WORDPRESS_MEDIA_SOURCE_SHA256,
        )
        self.assertFalse(
            fixture["attachment_edit"]["post_parent_hidden_field_exists"]
        )
        self.assertEqual(
            fixture["attachment_edit"]["uploaded_to_box_count_when_unattached"], 0
        )

    def test_complete_library_parent_adapter_accepts_only_core_unattached_shape(self):
        fixture = json.loads(DOM_CONTRACT_FIXTURE.read_text(encoding="utf-8"))
        parent_fixture = fixture["unattached_parent_cell"]
        admin = object.__new__(m.CleanupAdmin)
        admin._sanitized_row_link_diagnostic = mock.Mock(return_value={"safe": True})
        snapshot = {
            "rows": [
                {"id": row["attachment_id"], "filename": row["filename"]}
                for row in m.TARGETS
            ],
            "total": 4,
            "pages": 1,
            "complete": True,
            "unidentified": 0,
        }
        admin._reader = mock.Mock()
        admin._reader.enumerate_library.return_value = snapshot
        rows = {}
        for attachment_id in m.TARGET_IDS:
            control = mock.Mock()
            control.inner_text.return_value = parent_fixture["attach_text"]
            control.get_attribute.side_effect = lambda name, value=attachment_id: (
                parent_fixture["onclick_template"].format(attachment_id=value)
                if name == "onclick" else None
            )
            cell = mock.Mock()
            cell.inner_text.return_value = parent_fixture["inner_text"]
            cell.query_selector_all.side_effect = lambda selector, item=control: (
                [item] if selector in {
                    "a[href]", parent_fixture["attach_selector"],
                } else []
            )
            row = mock.Mock()
            row.query_selector_all.side_effect = lambda selector, item=cell: (
                [item] if selector == parent_fixture["selector"] else []
            )
            rows[attachment_id] = row
        admin._page = mock.Mock()
        admin._page.query_selector_all.side_effect = lambda selector: [
            row for attachment_id, row in rows.items()
            if selector == f"{m.media_base.LIST_ROW_SELECTOR}#post-{attachment_id}"
        ]
        result = admin.enumerate_library()
        self.assertEqual(result["target_parent_states"], exact_parent_states())
        admin._reader._goto.assert_called_once_with(m.media_base.library_page_url(1))

        rows[5521].query_selector_all("td.parent.column-parent")[0].inner_text.return_value = (
            "Attached product"
        )
        with self.assertRaises(m.CleanupError):
            admin.enumerate_library()

    def test_predecessor_state_must_be_exactly_one_permanent_event(self):
        raw = m.PREDECESSOR_EVENT.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "old-state"
            event = (state / "events" / m.PREDECESSOR_OPERATION_SHA256
                     / "01-5521-attempted.json")
            event.parent.mkdir(parents=True)
            event.write_bytes(raw)
            with mock.patch.object(m, "PREDECESSOR_STATE", state), \
                    mock.patch.object(m, "PREDECESSOR_EVENT", event):
                m.validate_fixed_contract()
                extra = state / "attempts" / f"{m.PREDECESSOR_OPERATION_SHA256}.attempt.json"
                extra.parent.mkdir(parents=True)
                extra.write_text("{}", encoding="utf-8")
                with self.assertRaises(m.CleanupError):
                    m.validate_fixed_contract()

    def test_guard_identity_is_pinned_independently_of_imported_dependency(self):
        with mock.patch.object(m.family_media, "GUARD_PLUGIN_VERSION", "1.0.6"), \
                self.assertRaises(m.CleanupError):
            m.validate_fixed_contract()

    def test_target_spec_rejects_every_other_id_and_non_int(self):
        for value in (0, 5522, 5528, "5521", True, None):
            with self.subTest(value=value), self.assertRaises(m.CleanupError):
                m.target_spec(value)

    def test_approval_is_exact(self):
        m.require_approval("APPROVED")
        for value in ("approved", " APPROVED", "APPROVED ", "APPROVED\n", "", None):
            with self.subTest(value=value), self.assertRaises(m.CleanupError):
                m.require_approval(value)

    def test_cli_has_only_stage_and_commit(self):
        parser = m.parser()
        self.assertEqual(parser.parse_args(["stage"]).command, "stage")
        parsed = parser.parse_args(["commit", "--plan", "x", "--approval", "APPROVED"])
        self.assertEqual(parsed.command, "commit")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["delete", "--id", "5521"])

    def test_source_has_no_generic_network_or_process_write_client(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names}
        self.assertNotIn("requests", imported)
        self.assertNotIn("subprocess", imported)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        attrs = {node.func.attr for node in calls if isinstance(node.func, ast.Attribute)}
        self.assertNotIn("api_request", attrs)
        self.assertNotIn("api_put", attrs)
        self.assertNotIn("api_post", attrs)
        self.assertNotIn("api_delete", attrs)
        self.assertNotIn("fetch", attrs)
        self.assertNotIn("upload_one", attrs)

    def test_docstring_discloses_non_atomic_no_retry(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("NOT atomic", text)
        self.assertIn("NO rollback", text)
        self.assertIn("INDETERMINATE_NO_RETRY", text)

    def test_cleanup_actor_does_not_inherit_broad_media_actor(self):
        self.assertNotIn(m.family_media.ProductFamilyAdmin, m.CleanupAdmin.__mro__)
        self.assertFalse(hasattr(m.CleanupAdmin, "upload_one"))
        self.assertFalse(hasattr(m.CleanupAdmin, "complete_guard"))
        self.assertFalse(hasattr(m.CleanupAdmin, "acquire_prepared_guard"))

    def test_correction_metadata_route_uses_only_attachment_edit_dom(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        route = source.split("def _edit_dom_projection", 1)[1].split(
            "    def delete_one", 1
        )[0]
        self.assertNotIn("wp.media", route)
        self.assertNotIn("apiFetch", route)
        self.assertNotIn("evaluate(", route)

        admin = object.__new__(m.CleanupAdmin)
        admin.read_attachment = mock.Mock(return_value={
            "attachment_id": 5521,
            "filename": m.TARGETS[0]["filename"],
            "source_url": m.TARGETS[0]["source_url"],
            "extension": ".png",
            "filetype_matches_name": True,
        })
        admin._edit_identity = mock.Mock(return_value={
            "post_id": "5521",
            "post_type": "attachment",
            "original_post_status": "inherit",
            "uploaded_to_box": "absent",
        })
        control = mock.Mock()
        control.inner_text.return_value = "Delete Permanently"
        admin._page = mock.Mock()
        admin._page.url = "https://frpdepots.com/wp-admin/post.php?post=5521&action=edit"
        admin._page.query_selector_all.return_value = [control]
        admin._delete_control_exact = mock.Mock(return_value=True)
        parent = {"id": 5521, "state": "unattached", "attach_control_exact": True}
        value = admin._edit_dom_projection(5521, library_parent_state=parent)
        self.assertEqual(
            value["identity_route"],
            "canonical_attachment_edit_dom_plus_complete_library_parent",
        )
        self.assertEqual(value["library_parent_state"], parent)
        self.assertEqual(value["registered_urls"], [m.TARGETS[0]["source_url"]])
        admin.read_attachment.assert_called_once_with(
            5521, expected_basename=m.TARGETS[0]["filename"]
        )

    def test_edit_dom_projection_rejects_noncanonical_or_ambiguous_urls(self):
        urls = [
            "https://evil.example/wp-admin/post.php?post=5521&action=edit",
            "https://frpdepots.com/wp-admin/post.php?post=5523&action=edit",
            "https://frpdepots.com/wp-admin/post.php?post=5521&action=edit&extra=1",
            "https://frpdepots.com/wp-admin/post.php?post=5521&post=5521&action=edit",
            "https://frpdepots.com/wp-admin/post.php?post=5521&action=edit#fragment",
            "https://frpdepots.com/wp-admin/edit.php?post=5521&action=edit",
        ]
        for url in urls:
            admin = object.__new__(m.CleanupAdmin)
            admin.read_attachment = mock.Mock(return_value={
                "attachment_id": 5521,
                "filename": m.TARGETS[0]["filename"],
                "source_url": m.TARGETS[0]["source_url"],
                "extension": ".png",
                "filetype_matches_name": True,
            })
            admin._page = mock.Mock()
            admin._page.url = url
            with self.subTest(url=url), self.assertRaises(m.CleanupError):
                admin._edit_dom_projection(
                    5521,
                    library_parent_state={
                        "id": 5521, "state": "unattached",
                        "attach_control_exact": True,
                    },
                )

    def test_edit_identity_requires_exact_form_and_no_uploaded_to_box(self):
        selectors = {
            "form#post": [mock.Mock()],
            'input#post_ID[name="post_ID"][type="hidden"]': [mock.Mock()],
            'input#post_type[name="post_type"][type="hidden"]': [mock.Mock()],
            'input#original_post_status[name="original_post_status"][type="hidden"]': [mock.Mock()],
            ".misc-pub-uploadedto": [],
        }
        values = ["5521", "attachment", "inherit"]
        for elements, value in zip(list(selectors.values())[1:], values):
            elements[0].input_value.return_value = value
        admin = object.__new__(m.CleanupAdmin)
        admin._page = mock.Mock()
        admin._page.query_selector_all.side_effect = lambda selector: selectors.get(selector, [])
        self.assertEqual(admin._edit_identity(5521)["uploaded_to_box"], "absent")
        selectors[".misc-pub-uploadedto"] = [mock.Mock()]
        with self.assertRaises(m.CleanupError):
            admin._edit_identity(5521)
        selectors[".misc-pub-uploadedto"] = []
        selectors["form#post"] = [mock.Mock(), mock.Mock()]
        with self.assertRaises(m.CleanupError):
            admin._edit_identity(5521)

    def test_edit_identity_failure_discloses_only_sanitized_field_shapes(self):
        secret = "SENSITIVE_NONCE_VALUE_123"
        post_id = mock.Mock()
        post_id.input_value.return_value = "5521"
        nonce = mock.Mock()
        nonce.input_value.return_value = secret
        nonce.get_attribute.side_effect = lambda name: {
            "id": "_wpnonce", "name": "_wpnonce", "type": "hidden",
        }.get(name)
        selectors = {
            "form#post": [mock.Mock()],
            'input#post_ID[name="post_ID"][type="hidden"]': [post_id],
            'input#post_type[name="post_type"][type="hidden"]': [],
            'input#original_post_status[name="original_post_status"][type="hidden"]': [],
            "form#post input": [nonce],
            ".misc-pub-uploadedto": [],
        }
        admin = object.__new__(m.CleanupAdmin)
        admin._page = mock.Mock()
        admin._page.query_selector_all.side_effect = lambda selector: selectors.get(selector, [])
        with self.assertRaises(m.CleanupError) as caught:
            admin._edit_identity(5521)
        rendered = str(caught.exception)
        self.assertIn('\"post_type\":0', rendered)
        self.assertIn('\"name\":\"_wpnonce\"', rendered)
        self.assertIn('\"values_retained\":false', rendered)
        self.assertNotIn(secret, rendered)
        nonce.input_value.assert_not_called()

    def test_delete_adapter_records_event_before_click_and_cleans_listener(self):
        admin = object.__new__(m.CleanupAdmin)
        state = valid_before_state()
        order = []
        admin._navigate_edit = mock.Mock()
        admin._assert_edit_route = mock.Mock()
        admin.read_target = mock.Mock(return_value=state["attachments"][0])
        admin._delete_control_exact = mock.Mock(return_value=True)
        control = mock.Mock()
        control.inner_text.return_value = "Delete Permanently"
        control.click.side_effect = lambda **_kwargs: order.append("click")
        admin._page = mock.Mock()
        admin._page.query_selector_all.return_value = [control]
        admin._page.url = "https://frpdepots.com/wp-admin/upload.php?deleted=1"
        admin._page.is_visible.return_value = False

        @contextlib.contextmanager
        def navigation():
            order.append("navigation_armed")
            yield
            order.append("navigation_complete")

        admin._page.expect_navigation.side_effect = lambda **_kwargs: navigation()
        result = admin.delete_one(
            5521, lambda: order.append("durable_event"), eligibility_state=state,
            expected_present_ids=m.TARGET_IDS,
        )
        self.assertLess(order.index("durable_event"), order.index("navigation_armed"))
        self.assertLess(order.index("navigation_armed"), order.index("click"))
        self.assertEqual(result["wordpress_deleted_marker"], 1)
        admin._page.remove_listener.assert_called_once()

    def test_delete_adapter_removes_dialog_listener_when_event_write_fails(self):
        admin = object.__new__(m.CleanupAdmin)
        state = valid_before_state()
        admin._navigate_edit = mock.Mock()
        admin._assert_edit_route = mock.Mock()
        admin.read_target = mock.Mock(return_value=state["attachments"][0])
        admin._delete_control_exact = mock.Mock(return_value=True)
        control = mock.Mock()
        control.inner_text.return_value = "Delete Permanently"
        admin._page = mock.Mock()
        admin._page.query_selector_all.return_value = [control]

        def fail_event():
            raise OSError("disk full")

        with self.assertRaises(OSError):
            admin.delete_one(
                5521, fail_event, eligibility_state=state,
                expected_present_ids=m.TARGET_IDS,
            )
        control.click.assert_not_called()
        admin._page.remove_listener.assert_called_once()

    def test_sanitized_link_shape_identifies_absolute_and_root_relative_edit_links(self):
        absolute = (
            "https://frpdepots.com/wp-admin/post.php?post=5521&action=edit"
        )
        relative = "/wp-admin/post.php?post=5521&action=edit"
        for value, raw_form in ((absolute, "absolute_https"), (relative, "root_relative")):
            with self.subTest(value=value):
                shape = m.sanitized_row_link_shape(value)
                self.assertEqual(shape["raw_form"], raw_form)
                self.assertTrue(shape["same_origin"])
                self.assertEqual(shape["path"], "/wp-admin/post.php")
                self.assertEqual(shape["query_keys"], ["action", "post"])
                self.assertEqual(shape["actions"], ["edit"])
                self.assertTrue(shape["resolved_exact_attachment_edit"])

    def test_sanitized_link_shape_never_retains_query_values_or_credentials(self):
        secret = "SENSITIVE_NONCE_123"
        shape = m.sanitized_row_link_shape(
            "https://user:password@frpdepots.com/wp-admin/post.php"
            f"?post=5521&action=delete&_wpnonce={secret}"
        )
        rendered = m.canonical(shape)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("5521", rendered)
        self.assertNotIn("user", rendered)
        self.assertNotIn("password", rendered)
        self.assertTrue(shape["credentials"])
        self.assertFalse(shape["same_origin"])
        self.assertEqual(shape["query_keys"], ["_wpnonce", "action", "post"])
        self.assertEqual(shape["actions"], ["delete"])
        self.assertFalse(shape["resolved_exact_attachment_edit"])

    def test_read_only_snapshot_uses_explicit_navigation_expectation(self):
        events = []
        proof = {
            "schema": m.family_media.GUARD_PROOF_SCHEMA,
            "plugin_version": m.family_media.GUARD_PLUGIN_VERSION,
            "mode": "atomic_snapshot",
            "family": "open_manway",
            "generated_utc": "2026-08-20T12:00:00+00:00",
            "attachment_total": 5,
            "hashed_total": 0,
            "total_bytes": 0,
            "snapshot_sha256": "a" * 64,
            "complete": False,
            "failures": [
                {"attachment_id": value, "reason": "unreadable_original"}
                for value in m.TARGET_IDS
            ],
            "private_exceptions": [dict(m.family_media.GUARD_PRIVATE_EXCEPTION)],
            "name_conflicts": [],
            "hash_conflicts": [],
            "fixed_matches": [],
            "guard_active": False,
        }

        class Navigation:
            def __enter__(self):
                events.append("navigation_enter")
                return self

            def __exit__(self, exc_type, exc, traceback):
                events.append("navigation_exit")
                return False

        class Page:
            def expect_navigation(self, **kwargs):
                events.append(("expect_navigation", kwargs))
                return Navigation()

        class Button:
            def click(self, **kwargs):
                events.append(("click", kwargs))

        class Reader:
            def _family_guard_button(self, family, action, status):
                events.append(("button", family, action, status))
                return Button()

            def _assert_landed(self):
                events.append("assert_landed")

            def _read_guard_proof(self):
                events.append("read_proof")
                return proof

        admin = object.__new__(m.CleanupAdmin)
        admin._page = Page()
        admin._reader = Reader()
        result = admin.atomic_snapshot("open_manway")
        self.assertEqual(result, proof)
        self.assertEqual(events, [
            ("button", "open_manway", "snapshot", "Guard inactive"),
            ("expect_navigation", {
                "wait_until": "domcontentloaded",
                "timeout": m.media_base.UPLOAD_TIMEOUT_MS,
            }),
            "navigation_enter",
            ("click", {
                "timeout": m.media_base.ACTION_TIMEOUT_MS,
                "no_wait_after": True,
            }),
            "navigation_exit",
            "assert_landed",
            "read_proof",
        ])


def product_fixture(product_id, images, *, variations=None, **overrides):
    value = {
        "id": product_id,
        "status": "publish",
        "images": images,
        "variations": list(variations or []),
        "description": "",
        "short_description": "",
        "downloads": [],
        "meta_data": [],
    }
    value.update(overrides)
    return value


class ProjectionTests(unittest.TestCase):
    @staticmethod
    def guard(*, failures=True):
        rows = ([{"attachment_id": value, "reason": "unreadable_original"}
                 for value in m.TARGET_IDS] if failures else [])
        return {
            "schema": m.family_media.GUARD_PROOF_SCHEMA,
            "plugin_version": m.family_media.GUARD_PLUGIN_VERSION,
            "mode": "atomic_snapshot",
            "family": "open_manway",
            "generated_utc": m.utc_now().isoformat(),
            "guard_active": False,
            "complete": not failures,
            "attachment_total": 75 if failures else 71,
            "hashed_total": 70,
            "total_bytes": 123456,
            "failures": rows,
            "private_exceptions": [
                json.loads(json.dumps(m.family_media.GUARD_PRIVATE_EXCEPTION))
            ],
            "name_conflicts": [],
            "hash_conflicts": [],
            "fixed_matches": [],
            "snapshot_sha256": "a" * 64,
        }

    def test_guard_before_accepts_exact_four_failures(self):
        value = m.guard_projection(self.guard(), expected_failure_ids=m.TARGET_IDS)
        self.assertFalse(value["complete"])
        self.assertEqual([row["attachment_id"] for row in value["failures"]], list(m.TARGET_IDS))
        self.assertEqual(value["guard_fixed_conflicts"], [])

    def test_guard_after_accepts_complete_snapshot(self):
        value = m.guard_projection(self.guard(failures=False), expected_failure_ids=())
        self.assertTrue(value["complete"])
        self.assertEqual(value["failures"], [])

    def test_guard_rejects_extra_or_wrong_failure(self):
        for mutate in (
            lambda value: value["failures"].append({"attachment_id": 9999, "reason": "unreadable_original"}),
            lambda value: value["failures"].__setitem__(0, {"attachment_id": 5521, "reason": "hash_failed"}),
            lambda value: value["name_conflicts"].append(
                {"attachment_id": 9999, "fixed_position": 1}),
            lambda value: value.__setitem__("attachment_total", 999),
            lambda value: value.__setitem__("guard_active", True),
            lambda value: value["private_exceptions"][0].__setitem__("post_status", "public"),
        ):
            proof = self.guard()
            mutate(proof)
            with self.assertRaises(m.CleanupError):
                m.guard_projection(proof, expected_failure_ids=m.TARGET_IDS)

    def test_guard_conflict_refusal_projects_identity_and_match_strength(self):
        proof = self.guard()
        row = {"attachment_id": 5823, "fixed_position": 1}
        proof["name_conflicts"] = [dict(row)]
        proof["hash_conflicts"] = [dict(row)]
        proof["fixed_matches"] = [dict(row)]
        with self.assertRaises(m.CleanupError) as caught:
            m.guard_projection(proof, expected_failure_ids=m.TARGET_IDS)
        message = str(caught.exception)
        self.assertIn('"attachment_id":5823', message)
        self.assertIn('"fixed_position":1', message)
        self.assertIn('"name_match":true', message)
        self.assertIn('"hash_match":true', message)
        self.assertIn('"exact_fixed_match":true', message)
        self.assertNotIn("manway_real", message)

    def test_guard_rejects_manifest_positions_five_and_six_without_stale_target_lookup(self):
        for position in (5, 6):
            proof = self.guard()
            proof["hash_conflicts"] = [
                {"attachment_id": 6000 + position, "fixed_position": position}
            ]
            with self.subTest(position=position), self.assertRaises(m.CleanupError) as caught:
                m.guard_projection(proof, expected_failure_ids=m.TARGET_IDS)
            self.assertIn(f'"fixed_position":{position}', str(caught.exception))

    def test_library_projection_accepts_exact_targets_and_tracks_survivors(self):
        rows = [
            {"id": 100, "filename": "other.png", "stem": "other"},
            *({"id": row["attachment_id"], "filename": row["filename"], "stem": "x"}
              for row in m.TARGETS),
        ]
        value = m.library_projection({
            "rows": rows, "total": len(rows), "complete": True,
            "pages": 1, "unidentified": 0,
            "target_parent_states": exact_parent_states(),
        })
        self.assertEqual(value["total"], 5)
        self.assertEqual(value["target_rows"], [
            {"id": row["attachment_id"], "filename": row["filename"]}
            for row in m.TARGETS
        ])
        self.assertNotEqual(value["rows_sha256"], value["survivor_rows_sha256"])

    def test_library_projection_failure_reports_closed_summary(self):
        snapshot = {
            "rows": [], "total": 1, "complete": False,
            "pages": 3, "unidentified": 1,
        }
        with self.assertRaises(m.CleanupError) as caught:
            m.library_projection(snapshot)
        message = str(caught.exception)
        self.assertIn('"complete":false', message)
        self.assertIn('"rows_count":0', message)
        self.assertIn('"total":1', message)
        self.assertIn('"pages":3', message)
        self.assertIn('"unidentified":1', message)

    def test_library_projection_rejects_missing_renamed_or_duplicate_target(self):
        base = [{"id": row["attachment_id"], "filename": row["filename"]} for row in m.TARGETS]
        variants = [
            base[:-1],
            [{**row, "filename": "wrong.png"} if row["id"] == 5521 else row for row in base],
            base + [dict(base[0])],
        ]
        for rows in variants:
            with self.subTest(rows=rows), self.assertRaises(m.CleanupError):
                m.library_projection({"rows": rows, "total": len(rows), "complete": True})

    def test_product_projection_accepts_no_references(self):
        products = [
            product_fixture(1455, [{"id": 100}, {"id": 101}]),
            product_fixture(1397, [
                {"id": row["attachment_id"], "src": row["source_url"]}
                for row in m.PROTECTED_SURVIVOR_GALLERY
            ]),
        ]
        with mock.patch.object(m, "strict_get_all", return_value=products):
            value = m.product_projection({"fake": True})
        self.assertEqual(value["products_checked"], 2)
        self.assertEqual(value["target_references"], [])
        self.assertEqual(value["protected_survivor_gallery"],
                         list(m.PROTECTED_SURVIVOR_GALLERY))

    def test_product_projection_rejects_survivor_gallery_drift(self):
        products = [product_fixture(1397, [
            {"id": row["attachment_id"], "src": row["source_url"]}
            for row in m.PROTECTED_SURVIVOR_GALLERY[:-1]
        ])]
        with mock.patch.object(m, "strict_get_all", return_value=products), \
                self.assertRaises(m.CleanupError):
            m.product_projection({"fake": True})

    def test_product_projection_rejects_any_target_reference(self):
        products = [product_fixture(1397, [{"id": 5521}])]
        with mock.patch.object(m, "strict_get_all", return_value=products), self.assertRaises(m.CleanupError):
            m.product_projection({"fake": True})

    def test_product_projection_rejects_omitted_fields_and_numeric_meta_references(self):
        gallery = [
            {"id": row["attachment_id"], "src": row["source_url"]}
            for row in m.PROTECTED_SURVIVOR_GALLERY
        ]
        missing = product_fixture(1397, gallery)
        missing.pop("meta_data")
        numeric = product_fixture(1397, gallery, meta_data=[{"key": "old", "value": "5521"}])
        for products in ([missing], [numeric]):
            with self.subTest(products=products), \
                    mock.patch.object(m, "strict_get_all", return_value=products), \
                    self.assertRaises(m.CleanupError):
                m.product_projection({"fake": True})

    def test_strict_paginator_refuses_every_unallowlisted_route_or_parameter(self):
        fields = "id,status,images,variations,description,short_description,downloads,meta_data"
        cases = [
            ("/orders", {"_fields": fields}, m.media_base.MAX_LIBRARY_ROWS),
            ("/products/0/variations", {"_fields": "id,image,description,downloads,meta_data"},
             m.media_base.MAX_LIBRARY_ROWS),
            ("/products", {"_fields": "id"}, m.media_base.MAX_LIBRARY_ROWS),
            ("/products", {"_fields": fields}, 10),
        ]
        for endpoint, params, maximum in cases:
            with self.subTest(endpoint=endpoint, params=params), \
                    mock.patch.object(m.wc, "api_get") as network, \
                    self.assertRaises(m.CleanupError):
                m.strict_get_all(endpoint, params, {"fake": True}, max_items=maximum)
            network.assert_not_called()

    def test_strict_paginator_reconciles_headers_and_rows(self):
        pages = [
            ([{"id": 1}, {"id": 2}], {"x-wp-total": "3", "x-wp-totalpages": "2"}),
            ([{"id": 3}], {"x-wp-total": "3", "x-wp-totalpages": "2"}),
        ]
        with mock.patch.object(m.wc, "api_get", side_effect=pages):
            rows = m.strict_get_all(
                "/products",
                {"_fields": "id,status,images,variations,description,short_description,downloads,meta_data"},
                {"fake": True}, max_items=m.media_base.MAX_LIBRARY_ROWS,
            )
        self.assertEqual([row["id"] for row in rows], [1, 2, 3])

    def test_strict_paginator_rejects_malformed_or_drifting_totals(self):
        cases = [
            [([{"id": 1}, "bad"], {"x-wp-total": "2", "x-wp-totalpages": "1"})],
            [([{"id": 1}], {"x-wp-total": "2", "x-wp-totalpages": "1"})],
            [([{"id": 1}], {})],
        ]
        for responses in cases:
            with self.subTest(responses=responses), \
                    mock.patch.object(m.wc, "api_get", side_effect=responses), \
                    self.assertRaises(m.CleanupError):
                m.strict_get_all(
                    "/products",
                    {"_fields": "id,status,images,variations,description,short_description,downloads,meta_data"},
                    {"fake": True}, max_items=m.media_base.MAX_LIBRARY_ROWS,
                )

def exact_parent_states(ids=m.TARGET_IDS):
    return [
        {"id": attachment_id, "state": "unattached", "attach_control_exact": True}
        for attachment_id in ids
    ]


def valid_before_state():
    survivor = [{"id": 100, "filename": "survivor.png", "stem": "survivor"}]
    library_rows = survivor + [
        {"id": row["attachment_id"], "filename": row["filename"], "stem": "fixed"}
        for row in m.TARGETS
    ]
    library = m.library_projection({
        "rows": library_rows,
        "total": len(library_rows),
        "complete": True,
        "pages": 1,
        "unidentified": 0,
        "target_parent_states": exact_parent_states(),
    })
    attachments = []
    for row in m.TARGETS:
        attachment_id = row["attachment_id"]
        attachments.append({
            "attachment_id": attachment_id,
            "filename": row["filename"],
            "source_url": row["source_url"],
            "edit_identity": {
                "post_id": str(attachment_id),
                "post_type": "attachment",
                "original_post_status": "inherit",
                "uploaded_to_box": "absent",
            },
            "library_parent_state": {
                "id": attachment_id,
                "state": "unattached",
                "attach_control_exact": True,
            },
            "identity_route": "canonical_attachment_edit_dom_plus_complete_library_parent",
            "source_provenance": "immutable_locked_upload_result",
            "registered_urls": [row["source_url"]],
            "delete_control_exact": True,
        })
    products = {
        "products_checked": 10,
        "variations_checked": 2,
        "product_and_variation_galleries_sha256": "c" * 64,
        "target_references": [],
        "protected_survivor_gallery": list(m.PROTECTED_SURVIVOR_GALLERY),
        "strict_totals_proven": True,
    }
    public_files = [
        {"url": url, "state": "not_found", "http_status": 404}
        for url in sorted(row["source_url"] for row in m.TARGETS)
    ]
    state = {
        "guard": m.guard_projection(
            ProjectionTests.guard(), expected_failure_ids=m.TARGET_IDS
        ),
        "library": library,
        "attachments": attachments,
        "products": products,
        "public_files": public_files,
    }
    return m.assert_correction_eligible(state, m.TARGET_IDS)


class EligibilityTests(unittest.TestCase):
    def test_exact_fixed_state_passes(self):
        state = valid_before_state()
        self.assertIs(m.assert_correction_eligible(state, m.TARGET_IDS), state)

    def test_every_material_boundary_fails_closed(self):
        mutations = [
            lambda state: state["guard"].__setitem__("complete", True),
            lambda state: state["guard"]["failures"].__setitem__(
                0, {"attachment_id": 5521, "reason": "wrong"}
            ),
            lambda state: state["library"]["target_rows"].pop(),
            lambda state: state["attachments"][0].__setitem__(
                "identity_route", "wp.media"
            ),
            lambda state: state["attachments"][0]["edit_identity"].__setitem__(
                "post_id", "9999"
            ),
            lambda state: state["products"]["target_references"].append({"id": 5521}),
            lambda state: state["products"].__setitem__(
                "protected_survivor_gallery", []
            ),
            lambda state: state["public_files"][0].__setitem__("state", "present"),
        ]
        for mutate in mutations:
            state = valid_before_state()
            mutate(state)
            with self.subTest(mutate=mutate), self.assertRaises(m.CleanupError):
                m.assert_correction_eligible(state, m.TARGET_IDS)

    def test_non_suffix_target_state_refuses(self):
        state = valid_before_state()
        with self.assertRaises(m.CleanupError):
            m.assert_correction_eligible(state, (5521, 5525))

    def test_stage_plan_cannot_bypass_eligibility(self):
        source = m.validate_fixed_contract()
        state = valid_before_state()
        state["products"]["strict_totals_proven"] = False
        with self.assertRaises(m.CleanupError):
            m.stage_plan(source, state)

    def test_predecessor_event_hash_is_mandatory(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "attempt.json"
            bad.write_text("{}", encoding="ascii")
            with mock.patch.object(m, "PREDECESSOR_EVENT", bad), \
                    self.assertRaises(m.CleanupError):
                m.validate_fixed_contract()


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.patches = [
            mock.patch.object(m, "PLAN_DIR", root / "plans"),
            mock.patch.object(m, "REGISTRY_DIR", root / "registry"),
            mock.patch.object(m, "ATTEMPT_DIR", root / "attempts"),
            mock.patch.object(m, "RESULT_DIR", root / "results"),
            mock.patch.object(m, "EVENT_DIR", root / "events"),
            mock.patch.object(m, "REGISTRY_KEY", root / "registry.key"),
            mock.patch.object(m, "RECEIPTS", root / "receipts.jsonl"),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.source = m.validate_fixed_contract()
        self.before = valid_before_state()

    def test_stage_and_load_round_trip(self):
        path, plan = m.stage_plan(self.source, self.before)
        loaded_path, loaded = m.load_plan(str(path))
        self.assertEqual(loaded_path, path)
        self.assertEqual(loaded, plan)
        self.assertEqual(plan["after_expected"]["library_total"], 1)
        self.assertTrue((m.REGISTRY_DIR / f"{plan['sha256']}.json").is_file())

    def test_plan_is_24_hours_and_separately_approved(self):
        _path, plan = m.stage_plan(self.source, self.before)
        created = m.datetime.fromisoformat(plan["created_utc"])
        expires = m.datetime.fromisoformat(plan["expires_utc"])
        self.assertEqual(expires - created, timedelta(hours=24))
        self.assertNotIn("approval", plan)

    def test_tampered_plan_refuses(self):
        path, _plan = m.stage_plan(self.source, self.before)
        data = json.loads(path.read_text(encoding="ascii"))
        data["targets"][0]["attachment_id"] = 9999
        path.write_text(json.dumps(data), encoding="ascii")
        with self.assertRaises(m.CleanupError):
            m.load_plan(str(path))

    def test_authenticated_stage_registry_tamper_refuses(self):
        path, plan = m.stage_plan(self.source, self.before)
        registry_path = m.stage_registry_path(plan["sha256"])
        registry = json.loads(registry_path.read_text(encoding="ascii"))
        registry["nonce"] = "0" * 32
        registry_path.write_text(json.dumps(registry), encoding="ascii")
        with self.assertRaises(m.CleanupError):
            m.load_plan(str(path))

    def test_plan_outside_fixed_directory_refuses(self):
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("{}", encoding="ascii")
        with self.assertRaises(m.CleanupError):
            m.load_plan(str(outside))

    def test_same_before_has_stable_operation_but_unique_plan_nonce(self):
        _path1, plan1 = m.stage_plan(self.source, self.before)
        _path2, plan2 = m.stage_plan(self.source, self.before)
        self.assertEqual(plan1["operation_sha256"], plan2["operation_sha256"])
        self.assertNotEqual(plan1["sha256"], plan2["sha256"])

    def test_operation_identity_ignores_mutable_before_state(self):
        changed = json.loads(json.dumps(self.before))
        changed["library"]["total"] = 999
        self.assertEqual(
            m.operation_sha(self.source, self.before),
            m.operation_sha(self.source, changed),
        )

    def test_plan_schema_change_keeps_successor_operation_identity(self):
        self.assertEqual(m.SCHEMA_VERSION, 2)
        self.assertEqual(m.OPERATION_SCHEMA_VERSION, 1)
        operation = m.operation_sha(self.source, self.before)
        self.assertNotEqual(operation, m.PREDECESSOR_OPERATION_SHA256)
        with mock.patch.object(m, "SCHEMA_VERSION", 99):
            self.assertEqual(m.operation_sha(self.source, self.before), operation)

    def test_any_per_attachment_event_permanently_blocks_operation(self):
        operation = m.operation_sha(self.source, self.before)
        marker = m.EVENT_DIR / operation / "01-5521-attempted.json"
        marker.parent.mkdir(parents=True)
        marker.write_text("{}", encoding="ascii")
        with self.assertRaises(m.CleanupError) as caught:
            m.assert_operation_not_attempted(operation)
        self.assertIn("permanent per-attachment attempt evidence", str(caught.exception))
        self.assertFalse(m.attempt_path(operation).exists())
        self.assertFalse(m.result_path(operation).exists())

    def test_stage_refuses_event_before_opening_browser(self):
        operation = m.operation_sha(self.source, self.before)
        marker = m.EVENT_DIR / operation / "01-5521-attempted.json"
        marker.parent.mkdir(parents=True)
        marker.write_text("{}", encoding="ascii")
        with mock.patch.object(m, "validate_fixed_contract", return_value=self.source), \
                mock.patch.object(m, "admin_session") as browser, \
                self.assertRaises(m.CleanupError):
            m.command_stage(argparse.Namespace())
        browser.assert_not_called()

    def test_stage_refuses_two_complete_reads_that_differ(self):
        drift = json.loads(json.dumps(self.before))
        drift["products"]["product_and_variation_galleries_sha256"] = "d" * 64

        @contextlib.contextmanager
        def session(_purpose):
            yield object()

        with mock.patch.object(m, "validate_fixed_contract", return_value=self.source), \
                mock.patch.object(m, "admin_session", session), \
                mock.patch.object(m.wc, "load_vault", return_value={}), \
                mock.patch.object(m, "collect_before", side_effect=[self.before, drift]), \
                self.assertRaises(m.CleanupError):
            m.command_stage(argparse.Namespace())
        self.assertFalse(m.PLAN_DIR.exists())


class CommitFlowTests(PlanTests):
    class FakeAdmin:
        def __init__(self, owner, before, *, drift=False, fail_after=None):
            self.owner = owner
            self.before = before
            self.drift = drift
            self.fail_after = fail_after
            self.deleted = []

        def delete_one(self, attachment_id, on_write_attempt, *,
                       eligibility_state, expected_present_ids):
            m.assert_correction_eligible(eligibility_state, expected_present_ids)
            self.owner.assertEqual(attachment_id, expected_present_ids[0])
            self.owner.assertTrue(m.attempt_path(self.owner.plan["operation_sha256"]).exists())
            on_write_attempt()
            if self.fail_after is not None and len(self.deleted) == self.fail_after:
                raise RuntimeError("simulated post-lock failure")
            self.deleted.append(attachment_id)
            spec = m.target_spec(attachment_id)
            return {
                "attachment_id": attachment_id, "filename": spec["filename"],
                "source_url": spec["source_url"], "delete_control_exact": True,
                "wordpress_deleted_marker": 1, "dialog": "confirm",
            }

        def enumerate_library(self):
            rows = [{"id": 100, "filename": "survivor.png"}]
            return {
                "rows": rows, "total": 1, "complete": True,
                "pages": 1, "unidentified": 0, "target_parent_states": [],
            }

        def atomic_snapshot(self, key):
            return ProjectionTests.guard(failures=False)

    def _prepare_commit(self):
        self.path, self.plan = m.stage_plan(self.source, self.before)

    def _run(self, admin, fresh):
        @contextlib.contextmanager
        def session(_purpose):
            yield admin

        def verify(_admin, _vault, _plan, deleted_ids):
            remaining = m.TARGET_IDS[len(deleted_ids):]
            rows = [{"id": 100, "filename": "survivor.png"}] + [
                {"id": row["attachment_id"], "filename": row["filename"]}
                for row in m.TARGETS if row["attachment_id"] in remaining
            ]
            rows.sort(key=lambda row: row["id"])
            guard = ProjectionTests.guard(failures=bool(remaining))
            guard["attachment_total"] = 71 + len(remaining)
            guard["failures"] = [
                {"attachment_id": value, "reason": "unreadable_original"}
                for value in remaining
            ]
            library = m.library_projection({
                "rows": rows,
                "total": len(rows),
                "complete": True,
                "pages": 1,
                "unidentified": 0,
                "target_parent_states": exact_parent_states(remaining),
            }, expected_present_ids=remaining)
            guard_projection = m.guard_projection(
                guard, expected_failure_ids=remaining
            )
            attachments = [
                row for row in self.before["attachments"]
                if row["attachment_id"] in remaining
            ]
            eligibility_state = {
                "guard": guard_projection,
                "library": library,
                "attachments": attachments,
                "products": self.before["products"],
                "public_files": self.before["public_files"],
            }
            m.assert_correction_eligible(eligibility_state, remaining)
            return {
                "deleted_ids": list(deleted_ids), "remaining_ids": list(remaining),
                "library": library,
                "guard": guard_projection,
                "products": self.before["products"],
                "record_removal_proof": [
                    {
                        "attachment_id": value,
                        "media_library_absent": True,
                        "guard_failure_absent": True,
                    }
                    for value in deleted_ids
                ],
                "public_absent": self.before["public_files"],
                "eligibility_state": eligibility_state,
            }

        args = argparse.Namespace(plan=str(self.path), approval="APPROVED")
        with mock.patch.object(m, "admin_session", session), \
                mock.patch.object(m.wc, "load_vault", return_value={"fake": True}), \
                mock.patch.object(m, "collect_before", return_value=fresh), \
                mock.patch.object(m, "verify_after_step", side_effect=verify), \
                mock.patch.object(m, "append_receipt"):
            with contextlib.redirect_stdout(io.StringIO()):
                m.command_commit(args)

    def test_lock_precedes_first_delete_and_success_is_replay_locked(self):
        self._prepare_commit()
        admin = self.FakeAdmin(self, self.before)
        self._run(admin, self.before)
        result = json.loads(m.result_path(self.plan["operation_sha256"]).read_text())
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["deleted_attachment_ids"], list(m.TARGET_IDS))
        self.assertEqual(result["writes_attempted"], 4)
        self.assertTrue(result["replay_locked"])

    def test_drift_refuses_before_attempt_lock(self):
        self._prepare_commit()
        fresh = json.loads(json.dumps(self.before))
        fresh["products"]["product_and_variation_galleries_sha256"] = "d" * 64
        admin = self.FakeAdmin(self, self.before)
        with self.assertRaises(m.CleanupError):
            self._run(admin, fresh)
        self.assertFalse(m.attempt_path(self.plan["operation_sha256"]).exists())
        self.assertEqual(admin.deleted, [])

    def test_failure_after_lock_is_indeterminate_and_no_retry(self):
        self._prepare_commit()
        admin = self.FakeAdmin(self, self.before, fail_after=1)
        with self.assertRaises(RuntimeError):
            self._run(admin, self.before)
        result = json.loads(m.result_path(self.plan["operation_sha256"]).read_text())
        self.assertEqual(result["status"], "INDETERMINATE_NO_RETRY")
        self.assertEqual(result["write_attempts"], 2)
        self.assertEqual(len(result["deleted_verified_before_failure"]), 1)
        self.assertTrue(result["no_retry"])
        self.assertFalse(result["rollback"])

    def test_wrong_approval_refuses_before_plan_or_network(self):
        self._prepare_commit()
        args = argparse.Namespace(plan=str(self.path), approval="approved")
        with mock.patch.object(m, "admin_session") as session, self.assertRaises(m.CleanupError):
            m.command_commit(args)
        session.assert_not_called()
        self.assertFalse(m.attempt_path(self.plan["operation_sha256"]).exists())

    def test_browser_busy_refuses_before_attempt_lock(self):
        self._prepare_commit()
        args = argparse.Namespace(plan=str(self.path), approval="APPROVED")
        with mock.patch.object(
                m, "admin_session", side_effect=m.UiLaneBusy("browser busy")
        ) as session, self.assertRaises(m.UiLaneBusy):
            m.command_commit(args)
        session.assert_called_once()
        self.assertFalse(m.attempt_path(self.plan["operation_sha256"]).exists())


if __name__ == "__main__":
    unittest.main()
