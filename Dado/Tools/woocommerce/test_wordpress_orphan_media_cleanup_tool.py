from __future__ import annotations

import argparse
import ast
import contextlib
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).with_name("wordpress_orphan_media_cleanup_tool.py")
SPEC = importlib.util.spec_from_file_location("wordpress_orphan_media_cleanup_tool_under_test", MODULE_PATH)
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

    def test_source_operation_is_fixed(self):
        self.assertEqual(
            m.SOURCE_OPERATION_SHA256,
            "877ff133b0e4fbf560b3be5877b755c72e5c33dc217c7e4affb23c1a314e2a26",
        )
        evidence = m.validate_fixed_contract()
        self.assertEqual(evidence["result_file_sha256"], m.SOURCE_RESULT_SHA256)
        self.assertEqual(evidence["status"], "INDETERMINATE_NO_RETRY")
        self.assertFalse(evidence["product_may_have_changed"])
        self.assertIsNone(evidence["gallery_payload"])
        self.assertFalse(evidence["delete_performed"])

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
        with self.assertRaises(SystemExit):
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
            "guard_active": False,
            "complete": not failures,
            "attachment_total": 75 if failures else 71,
            "hashed_total": 70,
            "failures": rows,
            "private_exceptions": [{"attachment_id": m.PRIVATE_EXCEPTION_ID}],
            "name_conflicts": [],
            "hash_conflicts": [],
            "fixed_matches": [],
            "snapshot_sha256": "a" * 64,
        }

    def test_guard_before_accepts_exact_four_failures(self):
        value = m.guard_projection(self.guard(), expected_failure_ids=m.TARGET_IDS)
        self.assertFalse(value["complete"])
        self.assertEqual([row["attachment_id"] for row in value["failures"]], list(m.TARGET_IDS))

    def test_guard_after_accepts_complete_snapshot(self):
        value = m.guard_projection(self.guard(failures=False), expected_failure_ids=())
        self.assertTrue(value["complete"])
        self.assertEqual(value["failures"], [])

    def test_guard_rejects_extra_or_wrong_failure(self):
        for mutate in (
            lambda value: value["failures"].append({"attachment_id": 9999, "reason": "unreadable_original"}),
            lambda value: value["failures"].__setitem__(0, {"attachment_id": 5521, "reason": "hash_failed"}),
            lambda value: value.__setitem__("name_conflicts", [{"id": 1}]),
            lambda value: value.__setitem__("attachment_total", 999),
            lambda value: value.__setitem__("guard_active", True),
        ):
            proof = self.guard()
            mutate(proof)
            with self.assertRaises(m.CleanupError):
                m.guard_projection(proof, expected_failure_ids=m.TARGET_IDS)

    def test_library_projection_accepts_exact_targets_and_tracks_survivors(self):
        rows = [
            {"id": 100, "filename": "other.png", "stem": "other"},
            *({"id": row["attachment_id"], "filename": row["filename"], "stem": "x"}
              for row in m.TARGETS),
        ]
        value = m.library_projection({
            "rows": rows, "total": len(rows), "complete": True,
            "pages": 1, "unidentified": 0,
        })
        self.assertEqual(value["total"], 5)
        self.assertEqual(value["target_rows"], [
            {"id": row["attachment_id"], "filename": row["filename"]}
            for row in m.TARGETS
        ])
        self.assertNotEqual(value["rows_sha256"], value["survivor_rows_sha256"])

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
            {"id": 1455, "status": "publish", "images": [{"id": 100}, {"id": 101}]},
            {"id": 1397, "status": "publish", "images": []},
        ]
        with mock.patch.object(m, "strict_get_all", return_value=products):
            value = m.product_projection({"fake": True})
        self.assertEqual(value["products_checked"], 2)
        self.assertEqual(value["target_references"], [])

    def test_product_projection_rejects_any_target_reference(self):
        products = [{"id": 1397, "status": "publish", "images": [{"id": 5521}]}]
        with mock.patch.object(m, "strict_get_all", return_value=products), self.assertRaises(m.CleanupError):
            m.product_projection({"fake": True})

    def test_strict_paginator_reconciles_headers_and_rows(self):
        pages = [
            ([{"id": 1}, {"id": 2}], {"x-wp-total": "3", "x-wp-totalpages": "2"}),
            ([{"id": 3}], {"x-wp-total": "3", "x-wp-totalpages": "2"}),
        ]
        with mock.patch.object(m.wc, "api_get", side_effect=pages):
            rows = m.strict_get_all("/products", {}, {"fake": True})
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
                m.strict_get_all("/products", {}, {"fake": True})


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
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
        self.source = m.validate_fixed_contract()
        self.before = {
            "guard": {"complete": False, "failures": list(m.TARGET_IDS)},
            "library": {
                "total": 75, "rows_sha256": "a" * 64,
                "survivor_rows_sha256": "b" * 64,
                "target_rows": [{"id": row["attachment_id"], "filename": row["filename"]}
                                for row in m.TARGETS],
            },
            "attachments": [
                {"attachment_id": row["attachment_id"], "filename": row["filename"],
                 "source_url": row["source_url"], "delete_control_exact": True}
                for row in m.TARGETS
            ],
            "products": {"products_checked": 10, "product_galleries_sha256": "c" * 64,
                         "target_references": []},
        }

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def test_stage_and_load_round_trip(self):
        path, plan = m.stage_plan(self.source, self.before)
        loaded_path, loaded = m.load_plan(str(path))
        self.assertEqual(loaded_path, path)
        self.assertEqual(loaded, plan)
        self.assertEqual(plan["after_expected"]["library_total"], 71)
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


class CommitFlowTests(PlanTests):
    class FakeAdmin:
        def __init__(self, owner, before, *, drift=False, fail_after=None):
            self.owner = owner
            self.before = before
            self.drift = drift
            self.fail_after = fail_after
            self.deleted = []

        def delete_one(self, attachment_id, on_write_attempt):
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
            return {"rows": rows, "total": 1, "complete": True}

        def atomic_snapshot(self, key):
            return ProjectionTests.guard(failures=False)

    def _prepare_commit(self):
        survivor = [{"id": 100, "filename": "survivor.png"}]
        self.before["library"] = {
            "total": 5,
            "rows_sha256": m.digest_for(survivor + [
                {"id": row["attachment_id"], "filename": row["filename"]}
                for row in m.TARGETS
            ]),
            "survivor_rows_sha256": m.digest_for(survivor),
            "target_rows": [{"id": row["attachment_id"], "filename": row["filename"]}
                            for row in m.TARGETS],
        }
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
            return {
                "deleted_ids": list(deleted_ids), "remaining_ids": list(remaining),
                "library": {
                    "total": len(rows), "rows": rows,
                    "rows_sha256": m.digest_for(rows),
                    "survivor_rows_sha256": m.digest_for([{"id": 100, "filename": "survivor.png"}]),
                },
                "guard": m.guard_projection(guard, expected_failure_ids=remaining),
                "products": self.before["products"],
                "authenticated_missing": [
                    {"missing": True, "code": "rest_post_invalid_id", "status": 404}
                    for _value in deleted_ids
                ],
                "public_absent": [],
            }

        args = argparse.Namespace(plan=str(self.path), approval="APPROVED")
        with mock.patch.object(m, "admin_session", session), \
                mock.patch.object(m.wc, "load_vault", return_value={"fake": True}), \
                mock.patch.object(m, "collect_before", return_value=fresh), \
                mock.patch.object(m, "verify_after_step", side_effect=verify), \
                mock.patch.object(m, "append_receipt"):
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
        fresh["products"]["product_galleries_sha256"] = "d" * 64
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


if __name__ == "__main__":
    unittest.main()
