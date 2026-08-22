from __future__ import annotations

import argparse
import contextlib
from datetime import timedelta
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import zoho_woo_sku_pair_tool as pair


class PairedSkuToolTests(unittest.TestCase):
    def setUp(self) -> None:
        # Keep every test artifact inside the authorized C:\FRPDepot workspace.
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self.tmp.name)
        self.pair_dir = self.root / "pair-plans"
        self.zoho_dir = self.root / "zoho-plans"
        self.woo_dir = self.root / "woo-plans"
        self.ids = {
            "zoho_item_id": "96274000000000001",
            "woo_parent_id": 1001,
            "woo_variation_id": 1002,
        }
        self.old_sku = "OLD-SKU"
        self.new_sku = "NEW-SKU"
        self.source = "Rachad's written correction instruction"
        for folder in (self.pair_dir, self.zoho_dir, self.woo_dir):
            folder.mkdir(parents=True)
        self.patches = [
            mock.patch.object(pair, "PLAN_DIR", self.pair_dir),
            mock.patch.object(pair, "ZOHO_PLAN_DIR", self.zoho_dir),
            mock.patch.object(pair, "WOO_PLAN_DIR", self.woo_dir),
            mock.patch.object(pair.zoho_tool, "append_receipt"),
        ]
        for item in self.patches:
            item.start()
        self.zoho_child = self._child_plan(self.zoho_dir / "zoho.json", "zoho")
        self.woo_child = self._child_plan(self.woo_dir / "woo.json", "woocommerce")

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    @staticmethod
    def _completed(value: dict | None = None, *, returncode: int = 0, stderr: str = ""):
        return subprocess.CompletedProcess(
            args=[], returncode=returncode,
            stdout=json.dumps(value) if value is not None else "",
            stderr=stderr,
        )

    def _child_plan(self, path: Path, label: str) -> Path:
        if label == "zoho":
            core = {
                "tool": "FRP Depot Zoho Inventory Item Catalog Tool",
                "kind": "item_name_sku",
                "created_utc": pair.utc_now().isoformat(),
                "payload": {"name": "Test item", "sku": self.new_sku},
                "sources": {"sku": self.source},
                "summary": {
                    "before": {
                        "item_id": self.ids["zoho_item_id"],
                        "name": "Test item",
                        "sku": self.old_sku,
                    },
                    "after": {
                        "item_id": self.ids["zoho_item_id"],
                        "name": "Test item",
                        "sku": self.new_sku,
                    },
                    "changed": {"sku": self.new_sku},
                },
            }
        else:
            core = {
                "schema_version": 2,
                "tool": "FRP Depot WooCommerce Audit & Approved Catalog Change Tool",
                "origin": "https://frpdepots.com:443",
                "method": "PUT",
                "endpoint": (
                    f"/products/{self.ids['woo_parent_id']}/variations/"
                    f"{self.ids['woo_variation_id']}"
                ),
                "action": "variation_update",
                "created_utc": pair.utc_now().isoformat(),
                "expires_utc": (pair.utc_now() + timedelta(hours=24)).isoformat(),
                "nonce": "fedcba9876543210fedcba9876543210",
                "resource_id": self.ids["woo_variation_id"],
                "parent_id": self.ids["woo_parent_id"],
                "before": {"sku": self.old_sku},
                "before_fingerprint": "0" * 64,
                "before_date_modified_gmt": "2026-08-06T00:00:00",
                "payload": {"sku": self.new_sku},
                "sources": {"sku": self.source},
            }
        path.write_text(
            json.dumps({**core, "sha256": pair.digest_for(core)}, indent=2) + "\n",
            encoding="utf-8",
        )
        return path.resolve()

    def _input_path(self, value: dict) -> Path:
        path = self.root / "input.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def _valid_input(self) -> dict:
        return {
            **self.ids,
            "new_sku": self.new_sku,
            "sources": {"sku": self.source},
        }

    def _zoho_stage_output(self) -> dict:
        return {
            "plan": str(self.zoho_child),
            "summary": {
                "before": {
                    "item_id": self.ids["zoho_item_id"],
                    "name": "Test item",
                    "sku": self.old_sku,
                },
                "after": {
                    "item_id": self.ids["zoho_item_id"],
                    "name": "Test item",
                    "sku": self.new_sku,
                },
                "changed": {"sku": self.new_sku},
            },
            "approval": "APPROVED",
        }

    def _woo_stage_output(self) -> dict:
        return {
            "status": "STAGED_NOT_COMMITTED",
            "plan": str(self.woo_child),
            "action": "variation_update",
            "resource_id": self.ids["woo_variation_id"],
            "parent_id": self.ids["woo_parent_id"],
            "before": {"sku": self.old_sku},
            "payload": {"sku": self.new_sku},
            "sources": {"sku": self.source},
            "approval": "APPROVED",
        }

    def _make_pair_plan(self, *, folder: Path | None = None) -> Path:
        created = pair.utc_now()
        core = {
            "schema_version": pair.SCHEMA_VERSION,
            "tool": pair.TOOL_NAME,
            "created_utc": created.isoformat(),
            "expires_utc": (created + timedelta(hours=24)).isoformat(),
            "nonce": "0123456789abcdef0123456789abcdef",
            "approval_required": "APPROVED",
            "identifiers": dict(self.ids),
            "before": {
                "zoho": {
                    "item_id": self.ids["zoho_item_id"],
                    "name": "Test item",
                    "sku": self.old_sku,
                },
                "woocommerce": {
                    "parent_id": self.ids["woo_parent_id"],
                    "variation_id": self.ids["woo_variation_id"],
                    "sku": self.old_sku,
                },
            },
            "after": {
                "zoho": {
                    "item_id": self.ids["zoho_item_id"],
                    "name": "Test item",
                    "sku": self.new_sku,
                },
                "woocommerce": {
                    "parent_id": self.ids["woo_parent_id"],
                    "variation_id": self.ids["woo_variation_id"],
                    "sku": self.new_sku,
                },
            },
            "sources": {"sku": self.source},
            "children": {
                "zoho": {
                    "plan": str(self.zoho_child),
                    "sha256": self._saved_sha(self.zoho_child),
                },
                "woocommerce": {
                    "plan": str(self.woo_child),
                    "sha256": self._saved_sha(self.woo_child),
                },
            },
        }
        target = folder or self.pair_dir
        target.mkdir(parents=True, exist_ok=True)
        path = (target / "paired.json").resolve()
        path.write_text(
            json.dumps({**core, "sha256": pair.digest_for(core)}, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _saved_sha(path: Path) -> str:
        return json.loads(path.read_text(encoding="utf-8"))["sha256"]

    def _zoho_commit_output(self, *, sku: str | None = None) -> dict:
        return {
            "updated": "item_name_sku",
            "item_id": self.ids["zoho_item_id"],
            "name": "Test item",
            "sku": sku if sku is not None else self.new_sku,
        }

    def _woo_commit_output(self, *, sku: str | None = None) -> dict:
        return {
            "status": "COMMITTED_AND_VERIFIED",
            "action": "variation_update",
            "resource_id": self.ids["woo_variation_id"],
            "approved_payload": {"sku": sku if sku is not None else self.new_sku},
            "plan_sha256": self._saved_sha(self.woo_child),
            "replay_locked": True,
        }

    def test_closed_input_schema_and_sources_rejected_before_subprocess(self) -> None:
        cases = [
            {**self._valid_input(), "stock_quantity": 5},
            {key: value for key, value in self._valid_input().items() if key != "new_sku"},
            {**self._valid_input(), "sources": {"sku": self.source, "name": "not allowed"}},
            {**self._valid_input(), "sources": {}},
        ]
        for index, value in enumerate(cases):
            with self.subTest(index=index), mock.patch.object(pair.subprocess, "run") as run:
                with self.assertRaises(pair.PairToolError):
                    pair.command_stage(argparse.Namespace(input=str(self._input_path(value))))
                run.assert_not_called()

    def test_stage_invokes_only_named_child_stage_clis_and_builds_full_hash_plan(self) -> None:
        seen_inputs = []

        def fake_run(command, **kwargs):
            self.assertEqual(command[0], pair.sys.executable)
            input_path = Path(command[-1])
            seen_inputs.append(json.loads(input_path.read_text(encoding="utf-8")))
            if command[1:] == [
                str(pair.ZOHO_TOOL), "stage-name-sku", "--input", str(input_path)
            ]:
                return self._completed(self._zoho_stage_output())
            if command[1:] == [str(pair.WOO_TOOL), "stage", "--input", str(input_path)]:
                return self._completed(self._woo_stage_output())
            self.fail(f"Unexpected subprocess command: {command}")

        output = io.StringIO()
        with mock.patch.object(pair.subprocess, "run", side_effect=fake_run) as run, \
             contextlib.redirect_stdout(output):
            pair.command_stage(argparse.Namespace(input=str(self._input_path(self._valid_input()))))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(seen_inputs, [
            {
                "item_id": self.ids["zoho_item_id"],
                "new_sku": self.new_sku,
                "sources": {"sku": self.source},
            },
            {
                "action": "variation_update",
                "resource_id": self.ids["woo_variation_id"],
                "parent_id": self.ids["woo_parent_id"],
                "changes": {"sku": self.new_sku},
                "sources": {"sku": self.source},
            },
        ])
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "PAIRED_STAGED_NOT_COMMITTED")
        self.assertEqual(result["approval"], "APPROVED")
        plan_path = Path(result["plan"])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        saved = plan.pop("sha256")
        self.assertEqual(len(saved), 64)
        self.assertEqual(saved, pair.digest_for(plan))
        self.assertEqual(plan["after"]["zoho"]["sku"], self.new_sku)
        self.assertEqual(plan["after"]["woocommerce"]["sku"], self.new_sku)
        self.assertTrue(Path(plan["children"]["zoho"]["plan"]).is_absolute())
        self.assertTrue(Path(plan["children"]["woocommerce"]["plan"]).is_absolute())

    def test_child_staging_failure_is_explicit_and_never_calls_commit(self) -> None:
        failed = self._completed(returncode=1, stderr="synthetic child stage failure")
        with mock.patch.object(pair.subprocess, "run", return_value=failed) as run:
            with self.assertRaisesRegex(pair.PairToolError, "no commit command was invoked"):
                pair.command_stage(argparse.Namespace(input=str(self._input_path(self._valid_input()))))
        self.assertEqual(run.call_count, 1)
        invoked = run.call_args.args[0]
        self.assertEqual(invoked[2], "stage-name-sku")
        self.assertNotIn("commit", invoked)
        self.assertEqual(list(self.pair_dir.glob("*paired_sku*.json")), [])

    def test_pair_hash_tampering_rejected_before_subprocess(self) -> None:
        plan_path = self._make_pair_plan()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["after"]["zoho"]["sku"] = "TAMPERED"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with mock.patch.object(pair.subprocess, "run") as run:
            with self.assertRaisesRegex(pair.PairToolError, "hash check failed"):
                pair.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"
                ))
            run.assert_not_called()

    def test_expired_full_hash_plan_rejected_before_subprocess(self) -> None:
        plan_path = self._make_pair_plan()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan.pop("sha256")
        # Preserve the exactly-24-hour relationship with one shared timestamp.
        expires = pair.utc_now() - timedelta(seconds=1)
        plan["expires_utc"] = expires.isoformat()
        plan["created_utc"] = (expires - timedelta(hours=24)).isoformat()
        plan["sha256"] = pair.digest_for(plan)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with mock.patch.object(pair.subprocess, "run") as run:
            with self.assertRaisesRegex(pair.PairToolError, "expired"):
                pair.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"
                ))
            run.assert_not_called()

    def test_pair_and_child_path_containment(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        outside_pair = self._make_pair_plan(folder=outside)
        with mock.patch.object(pair.subprocess, "run") as run:
            with self.assertRaisesRegex(pair.PairToolError, "outside"):
                pair.command_commit(argparse.Namespace(
                    plan=str(outside_pair), approval="APPROVED"
                ))
            run.assert_not_called()

        plan_path = self._make_pair_plan()
        outside_child = self._child_plan(outside / "child.json", "outside")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan.pop("sha256")
        plan["children"]["zoho"] = {
            "plan": str(outside_child),
            "sha256": self._saved_sha(outside_child),
        }
        plan["sha256"] = pair.digest_for(plan)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with mock.patch.object(pair.subprocess, "run") as run:
            with self.assertRaisesRegex(pair.PairToolError, "outside"):
                pair.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"
                ))
            run.assert_not_called()

    def test_child_hash_tampering_rejected_before_subprocess(self) -> None:
        plan_path = self._make_pair_plan()
        child = json.loads(self.zoho_child.read_text(encoding="utf-8"))
        child["injected"] = True
        self.zoho_child.write_text(json.dumps(child), encoding="utf-8")
        with mock.patch.object(pair.subprocess, "run") as run:
            with self.assertRaisesRegex(pair.PairToolError, "Child plan hash check failed"):
                pair.command_commit(argparse.Namespace(
                    plan=str(plan_path), approval="APPROVED"
                ))
            run.assert_not_called()

    def test_wrong_approval_rejected_before_subprocess_and_before_lock(self) -> None:
        plan_path = self._make_pair_plan()
        for wrong in ("approved but wait", "hold on", "APPROVED?", ""):
            with self.subTest(wrong=wrong), mock.patch.object(pair.subprocess, "run") as run:
                with self.assertRaises(pair.PairToolError):
                    pair.command_commit(argparse.Namespace(plan=str(plan_path), approval=wrong))
                run.assert_not_called()
                self.assertFalse(pair.pair_lock_path(plan_path).exists())

    def test_one_word_approved_invokes_each_named_commit_cli_exactly_once(self) -> None:
        plan_path = self._make_pair_plan()
        outputs = [
            self._completed(self._zoho_commit_output()),
            self._completed(self._woo_commit_output()),
        ]
        stdout = io.StringIO()
        with mock.patch.object(pair.subprocess, "run", side_effect=outputs) as run, \
             contextlib.redirect_stdout(stdout):
            pair.command_commit(argparse.Namespace(plan=str(plan_path), approval="APPROVED"))
        self.assertEqual(run.call_count, 2)
        zoho_command = run.call_args_list[0].args[0]
        woo_command = run.call_args_list[1].args[0]
        self.assertEqual(zoho_command, [
            pair.sys.executable, str(pair.ZOHO_TOOL), "commit-name-sku", "--plan",
            str(self.zoho_child), "--approval", "APPROVED",
        ])
        self.assertEqual(woo_command, [
            pair.sys.executable, str(pair.WOO_TOOL), "commit", "--plan",
            str(self.woo_child), "--approval", "APPROVED",
        ])
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED_BOTH")
        self.assertEqual(result["verified"]["zoho"]["sku"], self.new_sku)
        self.assertEqual(result["verified"]["woocommerce"]["sku"], self.new_sku)
        lock = json.loads(pair.pair_lock_path(plan_path).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")

    def test_the_lane_his_word_arrived_on_is_forwarded_to_both_children(self) -> None:
        """A5: each writer's own lock and receipt must record the lane, so the
        coordinator forwards --approval-lane to both named CLIs when it was
        given. --approval-message-utc is not forwarded: both children are
        reversible writers whose parsers do not take it."""
        plan_path = self._make_pair_plan()
        outputs = [
            self._completed(self._zoho_commit_output()),
            self._completed(self._woo_commit_output()),
        ]
        with mock.patch.object(pair.subprocess, "run", side_effect=outputs) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            pair.command_commit(argparse.Namespace(
                plan=str(plan_path), approval="yes go ahead", approval_lane="discord",
            ))
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            command = call.args[0]
            self.assertEqual(command[-4:], ["--approval", "yes go ahead", "--approval-lane", "discord"])
            self.assertNotIn("--approval-message-utc", command)
        lock = json.loads(pair.pair_lock_path(plan_path).read_text(encoding="utf-8"))
        self.assertEqual(lock["owner_go_lane"], "discord")

    def test_exact_sku_output_verification_locks_indeterminate_and_stops(self) -> None:
        plan_path = self._make_pair_plan()
        with mock.patch.object(
            pair.subprocess, "run",
            return_value=self._completed(self._zoho_commit_output(sku="WRONG-SKU")),
        ) as run:
            with self.assertRaisesRegex(pair.PairToolError, "indeterminate"):
                pair.command_commit(argparse.Namespace(plan=str(plan_path), approval="APPROVED"))
        self.assertEqual(run.call_count, 1)
        lock = json.loads(pair.pair_lock_path(plan_path).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertFalse(lock["permanent_lock"])

    def test_replay_rejected_without_another_subprocess(self) -> None:
        plan_path = self._make_pair_plan()
        with mock.patch.object(pair.subprocess, "run", side_effect=[
            self._completed(self._zoho_commit_output()),
            self._completed(self._woo_commit_output()),
        ]):
            with contextlib.redirect_stdout(io.StringIO()):
                pair.command_commit(argparse.Namespace(plan=str(plan_path), approval="APPROVED"))
        with mock.patch.object(pair.subprocess, "run") as run:
            with self.assertRaisesRegex(pair.PairToolError, "cannot be replayed"):
                pair.command_commit(argparse.Namespace(plan=str(plan_path), approval="APPROVED"))
            run.assert_not_called()

    def test_partial_failure_lock_has_no_retry(self) -> None:
        plan_path = self._make_pair_plan()
        outputs = [
            self._completed(self._zoho_commit_output()),
            self._completed(returncode=1, stderr="synthetic Woo failure"),
        ]
        with mock.patch.object(pair.subprocess, "run", side_effect=outputs) as run:
            with self.assertRaisesRegex(pair.PairToolError, "partial"):
                pair.command_commit(argparse.Namespace(plan=str(plan_path), approval="APPROVED"))
        self.assertEqual(run.call_count, 2)
        lock_path = pair.pair_lock_path(plan_path)
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertEqual(lock["completed_children"], ["zoho"])
        self.assertFalse(lock["permanent_lock"])
        pair.zoho_tool.append_receipt.assert_called()

        with mock.patch.object(pair.subprocess, "run") as retry:
            with self.assertRaisesRegex(pair.PairToolError, "Re-stage"):
                pair.command_commit(argparse.Namespace(plan=str(plan_path), approval="APPROVED"))
            retry.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
