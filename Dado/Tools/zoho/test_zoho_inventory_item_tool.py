from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import zoho_inventory_item_tool as item_tool


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class ZohoInventoryItemGroupOptionTests(unittest.TestCase):
    def setUp(self) -> None:
        here = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(dir=here)
        self.root = Path(self.temp.name)
        self.plan_dir = self.root / "plans"
        self.plan_dir.mkdir()
        self.counter = 0
        self.vault = {
            "api_domain": "https://www.zohoapis.ca",
            "inventory_organization_id": "99",
            "scopes": [item_tool.UPDATE_SCOPE],
        }
        self.patchers = [
            mock.patch.object(item_tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(item_tool.zoho_tool, "load_vault", return_value=self.vault),
            mock.patch.object(
                item_tool.zoho_tool,
                "refresh_access_token",
                return_value=("token", self.vault),
            ),
            mock.patch.object(item_tool.zoho_tool, "save_vault"),
            mock.patch.object(item_tool.zoho_tool, "append_receipt"),
            mock.patch.object(
                item_tool.zoho_tool,
                "api_get",
                side_effect=AssertionError("live Zoho GET is forbidden in tests"),
            ),
            mock.patch.object(
                item_tool,
                "urlopen",
                side_effect=AssertionError("live Zoho write is forbidden in tests"),
            ),
        ]
        started = [patcher.start() for patcher in self.patchers]
        self.load_vault = started[1]
        self.refresh_access_token = started[2]
        self.save_vault = started[3]
        self.append_receipt = started[4]
        self.api_get = started[5]
        self.urlopen = started[6]

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def input_path(self, value: object) -> Path:
        self.counter += 1
        path = self.root / f"input_{self.counter}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    @staticmethod
    def group() -> dict:
        attributes = copy.deepcopy(item_tool.GROUP_OPTION_BASELINE_ATTRIBUTES)
        for order, attribute in enumerate(attributes, start=1):
            attribute["type"] = "text"
            attribute["order"] = order
            for option in attribute["options"]:
                option["data"] = ""
        common = {
            "status": "active",
            "rate": 1.0,
            "purchase_rate": 0.5,
            "stock_on_hand": 0.0,
            "available_stock": 0.0,
            "actual_available_stock": 0.0,
            "can_be_sold": True,
            "can_be_purchased": True,
            "track_inventory": True,
            "item_type": "inventory",
            "product_type": "goods",
            "attribute_option_id1": item_tool.GROUP_OPTION_TARGET["option_id"],
            "attribute_option_name1": item_tool.GROUP_OPTION_TARGET["before_name"],
            "attribute_option_id2": "96274000000023279",
            "attribute_option_name2": "150PSI",
        }
        linked_411 = {
            **common,
            "item_id": "96274000000034771",
            "name": "FRP FW PIPE-30\"/150PSI/D411",
            "sku": "PIDN750150PSI411",
            "rate": 29.75,
            "purchase_rate": 13.88,
            "attribute_option_id3": "96274000000019033",
            "attribute_option_name3": "D411",
        }
        linked_470 = {
            **common,
            "item_id": "96274000000034773",
            "name": "FRP FW PIPE-30\"/150PSI/D470",
            "sku": "PIDN750150PSI470",
            "rate": 51.31,
            "purchase_rate": 23.95,
            "attribute_option_id3": "96274000000019035",
            "attribute_option_name3": "D470",
        }
        unrelated = {
            **common,
            "item_id": "96274000000034775",
            "name": "FRP FW PIPE-36\"/150PSI/D411",
            "sku": "PIDN900150PSI411",
            "rate": 38.19,
            "purchase_rate": 17.82,
            "attribute_option_id1": "96274000000034783",
            "attribute_option_name1": "36\"",
            "attribute_option_id3": "96274000000019033",
            "attribute_option_name3": "D411",
        }
        return {
            "group_id": item_tool.GROUP_OPTION_TARGET["group_id"],
            "group_name": item_tool.GROUP_OPTION_TARGET["group_name"],
            "manufacturer_id": "96274000000020005",
            "manufacturer": "FRP JRAIN",
            "unit": "ft",
            "description": "",
            "is_taxable": True,
            "purchase_account_id": "439",
            "account_id": "346",
            "inventory_account_id": "442",
            "attribute_id1": "96274000000023957",
            "attribute_id2": "96274000000020153",
            "attribute_id3": "96274000000020151",
            "attribute_name1": "SIZE",
            "attribute_name2": "PRESSURE RATING",
            "attribute_name3": "RESIN TYPE",
            "status": "active",
            "show_in_storefront": True,
            "attributes": attributes,
            "custom_fields": [],
            "items": [linked_411, linked_470, unrelated],
        }

    def stage(self, group: dict | None = None, input_value: dict | None = None):
        group = copy.deepcopy(group or self.group())
        if input_value is None:
            input_value = {"source": "Rachad's 2026-08-08 commissioned correction"}
        with mock.patch.object(
            item_tool,
            "get_item_group",
            return_value=group,
        ), contextlib.redirect_stdout(io.StringIO()) as stdout:
            item_tool.command_stage_group_option_rename(
                argparse.Namespace(input=str(self.input_path(input_value)))
            )
        result = json.loads(stdout.getvalue())
        return Path(result["plan"]), result

    @staticmethod
    def rewrite_with_hash(path: Path, mutate) -> dict:
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan.pop("sha256", None)
        mutate(plan)
        plan["sha256"] = item_tool.digest_for(plan)
        path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        return plan

    def commit(self, plan: Path, states: list[dict], approval: str = "APPROVED"):
        with mock.patch.object(
            item_tool,
            "get_item_group",
            side_effect=[copy.deepcopy(value) for value in states],
        ) as get_group, mock.patch.object(
            item_tool,
            "api_write_allowed",
            return_value={"code": 0, "message": "updated"},
        ) as write, contextlib.redirect_stdout(io.StringIO()) as stdout:
            item_tool.command_commit_group_option_rename(
                argparse.Namespace(plan=str(plan), approval=approval)
            )
        return json.loads(stdout.getvalue()), get_group, write

    def test_fixed_target_and_payload_are_exact(self) -> None:
        target = item_tool.GROUP_OPTION_TARGET
        self.assertEqual(target["group_id"], "96274000000034779")
        self.assertEqual(target["attribute_id"], "96274000000023957")
        self.assertEqual(target["option_id"], "96274000000034781")
        self.assertEqual((target["before_name"], target["after_name"]), ("30", "30\""))
        before = item_tool.fixed_group_option_payload("30")
        after = item_tool.fixed_group_option_payload("30\"")
        differences = []
        for old_attribute, new_attribute in zip(before["attributes"], after["attributes"]):
            for old_option, new_option in zip(old_attribute["options"], new_attribute["options"]):
                if old_option != new_option:
                    differences.append((old_attribute["id"], old_option, new_option))
        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0][0], target["attribute_id"])
        self.assertEqual(differences[0][1], {"id": target["option_id"], "name": "30"})
        self.assertEqual(differences[0][2], {"id": target["option_id"], "name": "30\""})

    def test_stage_creates_hashed_plan_with_full_evidence(self) -> None:
        plan_path, output = self.stage()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        saved = plan.pop("sha256")
        self.assertEqual(saved, item_tool.digest_for(plan))
        self.assertEqual(plan["kind"], "group_option_rename")
        self.assertEqual(plan["payload"], item_tool.fixed_group_option_payload("30\""))
        self.assertEqual(plan["summary"]["before"], "30")
        self.assertEqual(plan["summary"]["after"], "30\"")
        self.assertEqual(len(plan["summary"]["linked_items"]), 2)
        self.assertEqual(
            plan["live_evidence"]["before_state_sha256"],
            item_tool.state_sha256(plan["live_evidence"]["before_state"]),
        )
        self.assertEqual(output["approval"], "APPROVED")
        self.assertFalse(item_tool.commit_lock_path(str(plan_path)).exists())

    def test_stage_input_is_source_only_and_nonblank(self) -> None:
        for value in ({}, {"source": ""}, {"source": "x", "group_id": "1"}):
            with self.subTest(value=value), self.assertRaises(item_tool.ItemToolError):
                self.stage(input_value=value)

    def test_stage_refuses_wrong_group_attribute_or_current_name(self) -> None:
        cases = []
        wrong_group = self.group()
        wrong_group["group_name"] = "Other"
        cases.append(wrong_group)
        wrong_attribute = self.group()
        wrong_attribute["attributes"][0]["name"] = "DIAMETER"
        cases.append(wrong_attribute)
        already_changed = self.group()
        already_changed["attributes"][0]["options"][15]["name"] = "30\""
        cases.append(already_changed)
        for group in cases:
            with self.subTest(group=group["group_name"]), self.assertRaises(item_tool.ItemToolError):
                self.stage(group=group)

    def test_stage_refuses_sibling_duplicate_and_any_baseline_drift(self) -> None:
        duplicate = self.group()
        duplicate["attributes"][0]["options"][0]["name"] = "30\""
        drift = self.group()
        drift["attributes"][2]["options"][1]["name"] = "Derakane 470-300"
        for group in (duplicate, drift):
            with self.assertRaises(item_tool.ItemToolError):
                self.stage(group=group)

    def test_stage_refuses_linked_item_drift(self) -> None:
        missing = self.group()
        missing["items"] = missing["items"][1:]
        extra = self.group()
        extra["items"].append({
            **copy.deepcopy(extra["items"][0]),
            "item_id": "999",
            "sku": "EXTRA",
        })
        wrong_sku = self.group()
        wrong_sku["items"][0]["sku"] = "WRONG"
        for group in (missing, extra, wrong_sku):
            with self.assertRaises(item_tool.ItemToolError):
                self.stage(group=group)

    def test_expected_state_changes_only_option_and_linked_item_labels(self) -> None:
        before = self.group()
        expected = item_tool.expected_group_after_option_rename(
            before,
            item_tool.GROUP_OPTION_TARGET["attribute_id"],
            item_tool.GROUP_OPTION_TARGET["option_id"],
            "30\"",
        )
        self.assertEqual(expected["attributes"][0]["options"][15]["name"], "30\"")
        self.assertEqual(expected["items"][0]["attribute_option_name1"], "30\"")
        self.assertEqual(expected["items"][1]["attribute_option_name1"], "30\"")
        self.assertEqual(expected["items"][2], before["items"][2])
        reverted = copy.deepcopy(expected)
        reverted["attributes"][0]["options"][15]["name"] = "30"
        reverted["items"][0]["attribute_option_name1"] = "30"
        reverted["items"][1]["attribute_option_name1"] = "30"
        self.assertEqual(reverted, before)

    def test_commit_requires_plain_approval_before_network_or_lock(self) -> None:
        plan, _ = self.stage()
        self.load_vault.reset_mock()
        with self.assertRaises(item_tool.ItemToolError):
            item_tool.command_commit_group_option_rename(
                argparse.Namespace(plan=str(plan), approval="YES")
            )
        self.load_vault.assert_not_called()
        self.assertFalse(item_tool.commit_lock_path(str(plan)).exists())

    def test_commit_refuses_rehashed_target_payload_and_evidence_forgery(self) -> None:
        mutators = [
            lambda plan: plan["summary"]["target"].update({"group_id": "1"}),
            lambda plan: plan["payload"]["attributes"][0]["options"][0].update({"name": "Other"}),
            lambda plan: plan["live_evidence"]["expected_state"].update({"group_name": "Other"}),
        ]
        for mutate in mutators:
            plan, _ = self.stage()
            self.rewrite_with_hash(plan, mutate)
            self.load_vault.reset_mock()
            with self.subTest(mutate=mutate), self.assertRaises(item_tool.ItemToolError):
                item_tool.command_commit_group_option_rename(
                    argparse.Namespace(plan=str(plan), approval="APPROVED")
                )
            self.load_vault.assert_not_called()
            self.assertFalse(item_tool.commit_lock_path(str(plan)).exists())

    def test_commit_refuses_stale_state_before_lock_or_write(self) -> None:
        plan, _ = self.stage()
        stale = self.group()
        stale["items"][0]["stock_on_hand"] = 1.0
        with mock.patch.object(item_tool, "get_item_group", return_value=stale), mock.patch.object(
            item_tool, "api_write_allowed"
        ) as write:
            with self.assertRaises(item_tool.ItemToolError):
                item_tool.command_commit_group_option_rename(
                    argparse.Namespace(plan=str(plan), approval="APPROVED")
                )
        write.assert_not_called()
        self.assertFalse(item_tool.commit_lock_path(str(plan)).exists())

    def test_commit_refuses_existing_replay_lock_before_network(self) -> None:
        plan, _ = self.stage()
        item_tool.commit_lock_path(str(plan)).write_text("{}", encoding="utf-8")
        self.load_vault.reset_mock()
        with self.assertRaises(item_tool.ItemToolError):
            item_tool.command_commit_group_option_rename(
                argparse.Namespace(plan=str(plan), approval="APPROVED")
            )
        self.load_vault.assert_not_called()

    def test_commit_success_writes_once_locks_and_verifies_full_state(self) -> None:
        plan, _ = self.stage()
        before = self.group()
        expected = item_tool.expected_group_after_option_rename(
            before,
            item_tool.GROUP_OPTION_TARGET["attribute_id"],
            item_tool.GROUP_OPTION_TARGET["option_id"],
            "30\"",
        )
        output, get_group, write = self.commit(plan, [before, expected])
        self.assertEqual(get_group.call_count, 2)
        write.assert_called_once()
        self.assertTrue(item_tool.commit_lock_path(str(plan)).exists())
        self.assertTrue(output["replay_locked"])
        self.assertEqual(output["after"], "30\"")
        self.append_receipt.assert_any_call(
            "zoho_inventory_group_option_renamed_by_named_tool",
            mock.ANY,
        )

    def test_write_failure_or_unexpected_result_remains_replay_locked(self) -> None:
        plan, _ = self.stage()
        before = self.group()
        with mock.patch.object(item_tool, "get_item_group", return_value=before), mock.patch.object(
            item_tool,
            "api_write_allowed",
            side_effect=item_tool.ItemToolError("indeterminate write failure"),
        ):
            with self.assertRaises(item_tool.ItemToolError):
                item_tool.command_commit_group_option_rename(
                    argparse.Namespace(plan=str(plan), approval="APPROVED")
                )
        self.assertTrue(item_tool.commit_lock_path(str(plan)).exists())

        second_plan, _ = self.stage()
        with mock.patch.object(
            item_tool,
            "get_item_group",
            side_effect=[before, before],
        ), mock.patch.object(
            item_tool,
            "api_write_allowed",
            return_value={"code": 0},
        ):
            with self.assertRaises(item_tool.ItemToolError):
                item_tool.command_commit_group_option_rename(
                    argparse.Namespace(plan=str(second_plan), approval="APPROVED")
                )
        self.assertTrue(item_tool.commit_lock_path(str(second_plan)).exists())
        self.append_receipt.assert_any_call(
            "zoho_inventory_group_option_unexpected_result",
            mock.ANY,
        )

    def test_missing_update_scope_refuses_before_get_or_lock(self) -> None:
        plan, _ = self.stage()
        self.vault["scopes"] = []
        with mock.patch.object(item_tool, "get_item_group") as get_group:
            with self.assertRaises(item_tool.ItemToolError):
                item_tool.command_commit_group_option_rename(
                    argparse.Namespace(plan=str(plan), approval="APPROVED")
                )
        get_group.assert_not_called()
        self.assertFalse(item_tool.commit_lock_path(str(plan)).exists())

    def test_api_guard_allows_only_fixed_group_endpoint_and_payload(self) -> None:
        payload = item_tool.fixed_group_option_payload("30\"")
        requests = []

        def capture(request, timeout=0):
            requests.append((request, timeout))
            return FakeResponse({"code": 0, "message": "updated"})

        with mock.patch.object(item_tool, "urlopen", side_effect=capture):
            result = item_tool.api_write_allowed(
                "token",
                "https://www.zohoapis.ca",
                "PUT",
                "/inventory/v1/itemgroups/96274000000034779",
                "99",
                payload,
            )
        self.assertEqual(result["code"], 0)
        self.assertEqual(len(requests), 1)
        request, timeout = requests[0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(timeout, 60)
        self.assertEqual(json.loads(request.data.decode("utf-8")), payload)

        wrong_cases = [
            ("/inventory/v1/itemgroups/1", payload),
            ("/inventory/v1/items/10", payload),
            ("/inventory/v1/itemgroups/96274000000034779", {**payload, "status": "inactive"}),
            (
                "/inventory/v1/itemgroups/96274000000034779",
                item_tool.fixed_group_option_payload("31\""),
            ),
        ]
        for path, wrong_payload in wrong_cases:
            with self.subTest(path=path), self.assertRaises(item_tool.ItemToolError):
                item_tool.api_write_allowed(
                    "token", "https://www.zohoapis.ca", "PUT", path, "99", wrong_payload
                )

    def test_existing_item_update_guard_still_refuses_group_fields(self) -> None:
        with self.assertRaises(item_tool.ItemToolError):
            item_tool.api_write_allowed(
                "token",
                "https://www.zohoapis.ca",
                "PUT",
                "/inventory/v1/items/10",
                "99",
                {"name": "Item", "attributes": []},
            )

    def test_withdrawn_item_create_plan_is_refused_after_hash_validation(self) -> None:
        core = {"tool": item_tool.TOOL_NAME, "kind": "item_create"}
        digest = item_tool.digest_for(core)
        path = self.root / "withdrawn_item_create.json"
        path.write_text(json.dumps({**core, "sha256": digest}), encoding="utf-8")
        with mock.patch.object(
            item_tool,
            "WITHDRAWN_ITEM_CREATE_PLAN_HASHES",
            {digest: "withdrawn in test"},
        ):
            with self.assertRaisesRegex(item_tool.ItemToolError, "permanently withdrawn"):
                item_tool.load_plan(str(path), "item_create")
        loaded = item_tool.load_plan(str(path), "item_create")
        self.assertEqual(loaded["sha256"], digest)

    def test_all_superseded_backing_ring_plans_are_pinned(self) -> None:
        expected = {
            "9d62d3962fa00f981f0ef6766448a176cc4d3a134b677c7982b1b8d7f012910a",
            "0a2d2944fd59a273000cd901b794727cd204f88eeeb3d40f47977a88b0030c1a",
            "0c192caad2b972e7c63a85f6e4944348c044aeef9f2d579bb356c04ffb50613d",
            "b44bc7cdb2e9c9bc023e630ffdfd550f63ff98c951974f6b90300e697844d38e",
            "dea4b48f0a55a2abccc5b76256a97ccab593f1c129a53c43c11035a6467728d0",
            "a3b3d81ca7046d01fc6218e823da9de24d9cbd5485efe022aee063d35150e5ab",
            "30291ce09dbd505cc64f40417fed77a02ba23cc087a63b6e1342d74d6be7b884",
            "0eabc41727bf143ce31e0a1801f7e544da74205555ca1db1ba30dbeaa2434621",
            "fa721418e4bdab8fbafe46b9db6bc253267f3e350a8dd3b7b51506c46339dc14",
            "d09f392e6444d923acbde8aad387cf23c628ebae38c30e5ba2c17c8b90caaf52",
            "8b02f2ef477604a2c71f4ad2743010782e820339517674bca89124d1a1239a17",
            "7efdea461d7974c61dd94f6bf05aae7fe97565b3707360567ffe98943596829d",
            "7856e86eb4f7d7b22799561d5c07d4004203eb593d3e1bb80285eb9ca1d51f5d",
            "e7a8688670ac0f43e6115e3b6d6cada10f2723387b55cac1167d21fd857c4318",
            "dd88cb32d545be3b8619fbdcf7ce8a68d61adc8511a5a4c9347cdbeb35fe798f",
            "d5340e598d5a2ed4d862c2e69c33eafdbef06b8db4b0e66eae917bcd9d584313",
        }
        self.assertEqual(set(item_tool.WITHDRAWN_ITEM_CREATE_PLAN_HASHES), expected)
        current = {
            "ceaf491e062574cc1c626604eb497029aac9e6b56858efc406412070bbe9b021",
            "0b12cb2b1b6075c2dce32fad09919e3bae7179f8238eac64b2eded9bc384bc5e",
            "bf55df0bec79e5d036c3662ce5e16c690cb07f99b7944afcb0fa8b5fe6fd00d5",
            "b422d0e000516d293fdf98b7c60c52ac446c18175810fed420691056d9301912",
            "db844527217a1c4908aeda06ccaf4aaace915533ee93581ab7e86f45b0432770",
            "8bd3658c913a79b88df6a3202ac8b00e7ab1b140ecd87474d1860bc912af19ad",
            "d09d398e74593b59fdcf067bf28a7bb93aad0bfffcde5ad79de52c170ab71dbd",
            "de6c5e0f6e804210d6d3331907fc0bf4af79e466294dd00181369baf4b74cb09",
        }
        self.assertTrue(current.isdisjoint(item_tool.WITHDRAWN_ITEM_CREATE_PLAN_HASHES))

    def test_parser_exposes_only_separate_stage_and_commit_commands(self) -> None:
        parser = item_tool.build_parser()
        stage = parser.parse_args(["stage-group-option-rename", "--input", "x.json"])
        commit = parser.parse_args([
            "commit-group-option-rename", "--plan", "p.json", "--approval", "APPROVED"
        ])
        self.assertIs(stage.func, item_tool.command_stage_group_option_rename)
        self.assertIs(commit.func, item_tool.command_commit_group_option_rename)


if __name__ == "__main__":
    unittest.main()
