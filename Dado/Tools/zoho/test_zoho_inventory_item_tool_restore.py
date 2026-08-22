"""Item tool under the 2026-08-21 autonomy programme (spec A1/A2/A4).

Item names, SKUs and the one fixed group-option label are REVERSIBLE work:
* his clear go covers the write -- APPROVED still works, "yes" counts too;
* the live state is captured beside the plan before the PUT and a `restore`
  route puts it back;
* a failed write reads "needs re-stage"; nothing is permanently locked, but a
  committed plan is spent and cannot be replayed.
Creates have no restore because Zoho deletes are walled (Hard Rule 3).
"""
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
from test_zoho_inventory_item_tool import ZohoInventoryItemGroupOptionTests as GroupHarness


class GroupOptionRestoreTests(GroupHarness):
    def restore(self, plan: Path, states: list[dict], approval: str = "yes put it back"):
        with mock.patch.object(
            item_tool, "get_item_group", side_effect=[copy.deepcopy(value) for value in states],
        ) as get_group, mock.patch.object(
            item_tool, "api_write_allowed", return_value={"code": 0, "message": "updated"},
        ) as write, contextlib.redirect_stdout(io.StringIO()) as stdout:
            item_tool.command_restore_group_option_rename(
                argparse.Namespace(plan=str(plan), approval=approval)
            )
        return json.loads(stdout.getvalue()), get_group, write

    def expected(self, before):
        return item_tool.expected_group_after_option_rename(
            before, item_tool.GROUP_OPTION_TARGET["attribute_id"],
            item_tool.GROUP_OPTION_TARGET["option_id"], "30\"",
        )

    def test_a_plain_yes_commits_the_rename(self) -> None:
        plan, _ = self.stage()
        before = self.group()
        output, _, write = self.commit(plan, [before, self.expected(before)], approval="yes")
        write.assert_called_once()
        self.assertTrue(output["plan_spent"])
        lock = json.loads(item_tool.commit_lock_path(str(plan)).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")
        self.assertEqual(lock["owner_go"], "yes")
        self.assertFalse(lock["permanent_lock"])
        backup = item_tool.owner_authority.live_state_path(plan)
        self.assertTrue(backup.exists())
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["state"]["group"], before)

    def test_a_conditional_word_is_refused_before_network_or_lock(self) -> None:
        plan, _ = self.stage()
        self.load_vault.reset_mock()
        for bad in ("yes but check the 36 inch first", "wait", "go ahead?", ""):
            with self.assertRaises(item_tool.ItemToolError):
                item_tool.command_commit_group_option_rename(
                    argparse.Namespace(plan=str(plan), approval=bad)
                )
        self.load_vault.assert_not_called()
        self.assertFalse(item_tool.commit_lock_path(str(plan)).exists())

    def test_restore_puts_the_label_back_once(self) -> None:
        plan, _ = self.stage()
        before = self.group()
        after = self.expected(before)
        self.commit(plan, [before, after])
        output, get_group, write = self.restore(plan, [after, before])
        self.assertEqual(output["status"], "RESTORED")
        write.assert_called_once()
        self.assertEqual(write.call_args.args[5], item_tool.fixed_group_option_payload("30"))
        self.assertEqual(get_group.call_count, 2)
        record = json.loads(item_tool.owner_authority.restore_record_path(plan).read_text(encoding="utf-8"))
        self.assertEqual(record["owner_go"], "yes put it back")
        with self.assertRaises(item_tool.ItemToolError) as again:
            self.restore(plan, [after, before])
        self.assertIn("already restored", str(again.exception))

    def test_restore_leaves_a_group_that_moved_since_alone(self) -> None:
        plan, _ = self.stage()
        before = self.group()
        after = self.expected(before)
        self.commit(plan, [before, after])
        moved = copy.deepcopy(after)
        moved["items"][0]["stock_on_hand"] = 5.0
        output, _, write = self.restore(plan, [moved])
        self.assertEqual(output["status"], "NOT_RESTORED")
        self.assertIn("moved since", output["reason"])
        write.assert_not_called()

    def test_restore_refuses_without_his_go_or_without_a_capture(self) -> None:
        plan, _ = self.stage()
        with self.assertRaises(item_tool.ItemToolError) as no_capture:
            self.restore(plan, [self.group()])
        self.assertIn("nothing to restore", str(no_capture.exception))
        before = self.group()
        self.commit(plan, [before, self.expected(before)])
        with self.assertRaises(item_tool.ItemToolError):
            self.restore(plan, [self.expected(before), before], approval="wait")

    def test_a_failed_write_needs_restage_and_a_new_plan_still_works(self) -> None:
        plan, _ = self.stage()
        before = self.group()
        with mock.patch.object(item_tool, "get_item_group", return_value=before), mock.patch.object(
            item_tool, "api_write_allowed", side_effect=item_tool.ItemToolError("HTTP 500"),
        ):
            with self.assertRaises(item_tool.ItemToolError) as caught:
                item_tool.command_commit_group_option_rename(
                    argparse.Namespace(plan=str(plan), approval="APPROVED")
                )
        message = str(caught.exception)
        self.assertIn("re-stage", message.casefold())
        self.assertNotIn("permanent", message.casefold())
        lock = json.loads(item_tool.commit_lock_path(str(plan)).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertFalse(lock["permanent_lock"])
        with self.assertRaises(item_tool.ItemToolError) as replay:
            self.commit(plan, [before, self.expected(before)])
        self.assertIn("Re-stage", str(replay.exception))
        second, _ = self.stage()
        output, _, write = self.commit(second, [before, self.expected(before)])
        self.assertTrue(output["plan_spent"])

    def test_the_transport_allows_the_restore_payload_and_nothing_wider(self) -> None:
        requests = []

        def capture(request, timeout=0):
            requests.append(request)
            return type("R", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: False,
                                  "read": lambda s: b'{"code": 0}'})()

        with mock.patch.object(item_tool, "urlopen", side_effect=capture):
            item_tool.api_write_allowed(
                "token", "https://www.zohoapis.ca", "PUT",
                "/inventory/v1/itemgroups/96274000000034779", "99",
                item_tool.fixed_group_option_payload("30"),
            )
        self.assertEqual(len(requests), 1)
        with self.assertRaises(item_tool.ItemToolError):
            item_tool.api_write_allowed(
                "token", "https://www.zohoapis.ca", "PUT",
                "/inventory/v1/itemgroups/96274000000034779", "99",
                item_tool.fixed_group_option_payload("29"),
            )

    def test_parser_exposes_the_restore_commands(self) -> None:
        parser = item_tool.build_parser()
        args = parser.parse_args(["restore-group-option-rename", "--plan", "p.json", "--approval", "yes"])
        self.assertIs(args.func, item_tool.command_restore_group_option_rename)
        args = parser.parse_args(["restore-name-sku", "--plan", "p.json", "--approval", "go ahead",
                                  "--approval-lane", "discord"])
        self.assertIs(args.func, item_tool.command_restore_name_sku)
        self.assertEqual(args.approval_lane, "discord")


class NameSkuRestoreTests(unittest.TestCase):
    ITEM_ID = "96274000000523063"

    def setUp(self) -> None:
        here = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(dir=here)
        self.root = Path(self.temp.name)
        self.plan_dir = self.root / "plans"
        self.plan_dir.mkdir()
        self.vault = {"api_domain": "https://www.zohoapis.ca", "inventory_organization_id": "99",
                      "scopes": [item_tool.UPDATE_SCOPE]}
        self.item = {"item_id": self.ITEM_ID, "name": "Old Name", "sku": "OLD-1", "rate": 1.0}
        self.writes: list[dict] = []

        def get_item(access_token, vault, item_id):
            return copy.deepcopy(self.item)

        def write(access_token, api_domain, method, path, org, payload):
            self.writes.append({"method": method, "path": path, "payload": payload})
            self.item.update(payload)
            return {"code": 0, "item": copy.deepcopy(self.item)}

        self.patchers = [
            mock.patch.object(item_tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(item_tool.zoho_tool, "load_vault", return_value=self.vault),
            mock.patch.object(item_tool.zoho_tool, "refresh_access_token", return_value=("token", self.vault)),
            mock.patch.object(item_tool.zoho_tool, "save_vault"),
            mock.patch.object(item_tool.zoho_tool, "append_receipt"),
            mock.patch.object(item_tool, "get_item", side_effect=get_item),
            mock.patch.object(item_tool, "list_all_items", return_value=[]),
            mock.patch.object(item_tool, "api_write_allowed", side_effect=write),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def stage(self) -> Path:
        path = self.root / "input.json"
        path.write_text(json.dumps({"item_id": self.ITEM_ID, "new_name": "New Name", "new_sku": "NEW-1",
                                    "sources": {"name": "Rachad", "sku": "Rachad"}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            item_tool.command_stage_name_sku(argparse.Namespace(input=str(path)))
        return Path(json.loads(stdout.getvalue())["plan"])

    def commit(self, plan: Path, approval: str = "go ahead") -> dict:
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            item_tool.command_commit_name_sku(argparse.Namespace(plan=str(plan), approval=approval))
        return json.loads(stdout.getvalue())

    def restore(self, plan: Path, approval: str = "yes") -> dict:
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            item_tool.command_restore_name_sku(argparse.Namespace(plan=str(plan), approval=approval))
        return json.loads(stdout.getvalue())

    def test_commit_captures_then_restore_puts_name_and_sku_back(self) -> None:
        plan = self.stage()
        output = self.commit(plan)
        self.assertEqual((output["name"], output["sku"]), ("New Name", "NEW-1"))
        self.assertTrue(output["plan_spent"])
        backup = json.loads(Path(output["live_state_backup"]).read_text(encoding="utf-8"))
        self.assertEqual(backup["state"]["before"], {"item_id": self.ITEM_ID, "name": "Old Name", "sku": "OLD-1"})
        restored = self.restore(plan)
        self.assertEqual(restored["status"], "RESTORED")
        self.assertEqual(self.writes[-1]["payload"], {"name": "Old Name", "sku": "OLD-1"})
        self.assertEqual((self.item["name"], self.item["sku"]), ("Old Name", "OLD-1"))
        with self.assertRaises(item_tool.ItemToolError):
            self.restore(plan)
        self.assertEqual(len(self.writes), 2)

    def test_a_spent_plan_is_not_replayed_and_a_failed_one_needs_restage(self) -> None:
        plan = self.stage()
        self.commit(plan, approval="APPROVED")
        with self.assertRaises(item_tool.ItemToolError) as spent:
            self.commit(plan)
        self.assertIn("spent", str(spent.exception))
        self.item.update(name="Old Name", sku="OLD-1")
        second = self.stage()
        with mock.patch.object(item_tool, "api_write_allowed", side_effect=item_tool.ItemToolError("HTTP 502")):
            with self.assertRaises(item_tool.ItemToolError) as caught:
                self.commit(second)
        self.assertIn("re-stage", str(caught.exception).casefold())
        lock = json.loads(item_tool.commit_lock_path(str(second)).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertFalse(lock["permanent_lock"])

    def test_restore_leaves_an_item_that_moved_since_alone(self) -> None:
        plan = self.stage()
        self.commit(plan)
        self.item["name"] = "Someone Else Renamed It"
        restored = self.restore(plan)
        self.assertEqual(restored["status"], "NOT_RESTORED")
        self.assertIn("moved since", restored["reason"])
        self.assertEqual(len(self.writes), 1)


if __name__ == "__main__":
    unittest.main()
