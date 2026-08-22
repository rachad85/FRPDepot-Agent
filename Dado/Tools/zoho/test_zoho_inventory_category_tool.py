from __future__ import annotations

import argparse
import contextlib
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import zoho_inventory_category_tool as category


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class ZohoInventoryCategoryToolTests(unittest.TestCase):
    def setUp(self) -> None:
        here = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(dir=here)
        self.root = Path(self.temp.name)
        self.plan_dir = self.root / "plans"
        self.plan_dir.mkdir()
        self.vault = {
            "api_domain": "https://www.zohoapis.ca",
            "inventory_organization_id": "99",
            "scopes": [category.CREATE_SCOPE, category.UPDATE_SCOPE],
        }
        self.target = {"category_id": "200", "name": "Target Category"}
        self.counter = 0
        self.patchers = [
            mock.patch.object(category, "PLAN_DIR", self.plan_dir),
            mock.patch.object(category, "WRITE_SHAPES_VERIFIED", True),
            mock.patch.object(category.zoho_tool, "load_vault", return_value=self.vault),
            mock.patch.object(
                category.zoho_tool, "refresh_access_token", return_value=("token", self.vault)
            ),
            mock.patch.object(category.zoho_tool, "save_vault"),
            mock.patch.object(category.zoho_tool, "append_receipt"),
            mock.patch.object(
                category, "urlopen", side_effect=AssertionError("network is forbidden in tests")
            ),
        ]
        started = [patcher.start() for patcher in self.patchers]
        self.load_vault = started[2]
        self.refresh_access_token = started[3]
        self.save_vault = started[4]
        self.append_receipt = started[5]

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def input_path(self, value: object) -> Path:
        self.counter += 1
        path = self.root / f"input_{self.counter}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def item(self, item_id: str, **changes) -> dict:
        value = {
            "item_id": item_id,
            "name": f"Item {item_id}",
            "sku": f"SKU-{item_id}",
            "group_id": "300",
            "group_name": "Protected Group",
            "category_id": "",
            "category_name": "",
            "rate": 12.5,
            "purchase_rate": 6.25,
            "stock_on_hand": 7.0,
            "available_stock": 7.0,
            "account_id": "400",
            "purchase_account_id": "401",
            "inventory_account_id": "402",
            "tax_id": "500",
            "unit": "pcs",
            "unit_id": "600",
            "description": "Protected description",
            "purchase_description": "Protected purchase description",
            "image_name": "protected.jpg",
            "documents": [{"document_id": "700"}],
            "custom_fields": [],
            "last_modified_time": "2026-08-07T00:00:00-0400",
        }
        value.update(changes)
        return value

    def stage_create(self, name: str = "New Category", categories=None):
        source = {"category_name": "Rachad's written category instruction"}
        with mock.patch.object(
            category, "list_all_categories", return_value=list(categories or [])
        ), contextlib.redirect_stdout(io.StringIO()) as stdout:
            category.command_stage_create(argparse.Namespace(input=str(self.input_path({
                "category_name": name,
                "sources": source,
            }))))
        result = json.loads(stdout.getvalue())
        return Path(result["plan"]), result

    def stage_assign(self, items: list[dict], target=None):
        target = dict(target or self.target)
        by_id = {str(row["item_id"]): row for row in items}
        with mock.patch.object(
            category, "list_all_categories", return_value=[target]
        ), mock.patch.object(
            category, "get_item", side_effect=lambda token, vault, item_id: by_id[item_id]
        ), contextlib.redirect_stdout(io.StringIO()) as stdout:
            category.command_stage_assign(argparse.Namespace(input=str(self.input_path({
                "category_id": target["category_id"],
                "item_ids": list(by_id),
                "sources": {"category_assignment": "Rachad's reviewed classification source"},
            }))))
        result = json.loads(stdout.getvalue())
        return Path(result["plan"]), result

    @staticmethod
    def rewrite_with_hash(path: Path, mutate) -> dict:
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan.pop("sha256", None)
        mutate(plan)
        plan["sha256"] = category.digest_for(plan)
        path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        return plan

    def test_closed_input_schemas_reject_before_token_or_service_access(self) -> None:
        bad_create = [
            {"category_name": "X", "sources": {"category_name": "source"}, "rename": True},
            {"category_name": "X", "sources": {}},
            {"category_name": "X", "sources": {"category_name": "source", "extra": "x"}},
            {"sources": {"category_name": "source"}},
        ]
        bad_assign = [
            {"category_id": "1", "item_ids": ["2"], "sources": {"category_assignment": "s"}, "rate": 5},
            {"category_id": "1", "item_ids": ["2"], "sources": {}},
            {"category_id": "1", "item_ids": ["2"], "sources": {"category_assignment": "s", "sku": "x"}},
            {"item_ids": ["2"], "sources": {"category_assignment": "s"}},
        ]
        for index, value in enumerate(bad_create):
            with self.subTest(kind="create", index=index):
                self.load_vault.reset_mock()
                with self.assertRaises(category.CategoryToolError):
                    category.command_stage_create(
                        argparse.Namespace(input=str(self.input_path(value)))
                    )
                self.load_vault.assert_not_called()
        for index, value in enumerate(bad_assign):
            with self.subTest(kind="assign", index=index):
                self.load_vault.reset_mock()
                with self.assertRaises(category.CategoryToolError):
                    category.command_stage_assign(
                        argparse.Namespace(input=str(self.input_path(value)))
                    )
                self.load_vault.assert_not_called()

    def test_assignment_requires_unique_one_to_twenty_positive_ids_before_token_access(self) -> None:
        cases = [
            [],
            [str(index) for index in range(1, 22)],
            ["1", "1"],
            ["0"],
            [-1],
            [True],
            ["not-an-id"],
        ]
        for index, item_ids in enumerate(cases):
            with self.subTest(index=index, item_ids=item_ids):
                self.load_vault.reset_mock()
                path = self.input_path({
                    "category_id": "200",
                    "item_ids": item_ids,
                    "sources": {"category_assignment": "source"},
                })
                with self.assertRaises(category.CategoryToolError):
                    category.command_stage_assign(argparse.Namespace(input=str(path)))
                self.load_vault.assert_not_called()

    def test_stage_outputs_full_hash_24_hour_nonce_status_and_approval(self) -> None:
        create_path, create_result = self.stage_create()
        assign_path, assign_result = self.stage_assign([self.item("1")])
        for path, result in ((create_path, create_result), (assign_path, assign_result)):
            with self.subTest(path=path.name):
                plan = json.loads(path.read_text(encoding="utf-8"))
                saved = plan.pop("sha256")
                self.assertRegex(saved, r"^[0-9a-f]{64}$")
                self.assertEqual(saved, category.digest_for(plan))
                created = category.parse_time(plan["created_utc"], "created")
                expires = category.parse_time(plan["expires_utc"], "expires")
                self.assertEqual(expires - created, timedelta(hours=24))
                self.assertRegex(plan["nonce"], r"^[0-9a-f]{32}$")
                self.assertEqual(result["status"], "STAGED_NOT_COMMITTED")
                self.assertEqual(result["approval"], "APPROVED")
        assign_plan = json.loads(assign_path.read_text(encoding="utf-8"))
        evidence = assign_plan["live_evidence"]["items"][0]
        for field in (
            "item_id", "name", "sku", "group_id", "group_name",
            "category_id", "category_name", "proposed_category",
        ):
            self.assertIn(field, evidence)
        self.assertEqual(evidence["protected_state"]["rate"], 12.5)
        self.assertEqual(
            evidence["protected_state_sha256"], category.digest_for(evidence["protected_state"])
        )

    def test_plan_path_containment_rejects_outside_and_relative_paths(self) -> None:
        plan_path, _ = self.stage_create()
        outside_dir = self.root / "outside"
        outside_dir.mkdir()
        outside = outside_dir / "plan.json"
        outside.write_text(plan_path.read_text(encoding="utf-8"), encoding="utf-8")
        self.load_vault.reset_mock()
        for bad_path in (str(outside.resolve()), plan_path.name):
            with self.subTest(path=bad_path), self.assertRaisesRegex(
                category.CategoryToolError, "absolute|outside"
            ):
                category.command_commit_create(
                    argparse.Namespace(plan=bad_path, approval="APPROVED")
                )
        self.load_vault.assert_not_called()

    def test_full_hash_tamper_and_expiry_rejected_before_token_access(self) -> None:
        tampered, _ = self.stage_create("Tamper Test")
        plan = json.loads(tampered.read_text(encoding="utf-8"))
        plan["payload"]["name"] = "Changed after review"
        tampered.write_text(json.dumps(plan), encoding="utf-8")
        self.load_vault.reset_mock()
        with self.assertRaisesRegex(category.CategoryToolError, "hash check failed"):
            category.command_commit_create(
                argparse.Namespace(plan=str(tampered), approval="APPROVED")
            )
        self.load_vault.assert_not_called()

        expired, _ = self.stage_create("Expiry Test")
        expiry = category.utc_now() - timedelta(seconds=1)
        self.rewrite_with_hash(expired, lambda value: value.update({
            "created_utc": (expiry - timedelta(hours=24)).isoformat(),
            "expires_utc": expiry.isoformat(),
        }))
        self.load_vault.reset_mock()
        with self.assertRaisesRegex(category.CategoryToolError, "expired"):
            category.command_commit_create(
                argparse.Namespace(plan=str(expired), approval="APPROVED")
            )
        self.load_vault.assert_not_called()

    def test_closed_plan_schemas_reject_top_level_nested_and_source_extras(self) -> None:
        mutations = [
            lambda plan: plan.update({"unexpected": True}),
            lambda plan: plan["payload"].update({"rename": "forbidden"}),
            lambda plan: plan["sources"].update({"other": "forbidden"}),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                path, _ = self.stage_create(f"Closed {index}")
                self.rewrite_with_hash(path, mutation)
                self.load_vault.reset_mock()
                with self.assertRaises(category.CategoryToolError):
                    category.command_commit_create(
                        argparse.Namespace(plan=str(path), approval="APPROVED")
                    )
                self.load_vault.assert_not_called()

        assign_path, _ = self.stage_assign([self.item("9")])
        self.rewrite_with_hash(
            assign_path,
            lambda plan: plan["live_evidence"]["items"][0].update({"rate": 99}),
        )
        self.load_vault.reset_mock()
        with self.assertRaises(category.CategoryToolError):
            category.command_commit_assign(
                argparse.Namespace(plan=str(assign_path), approval="APPROVED")
            )
        self.load_vault.assert_not_called()

    def test_wrong_or_multiword_approval_rejected_before_token_service_and_lock(self) -> None:
        path, _ = self.stage_create("Approval Test")
        saved = json.loads(path.read_text(encoding="utf-8"))["sha256"]
        # 2026-08-21 (A1): "YES" / "APPROVE" now count; a conditional, a
        # question, a blank or a non-word still refuses before anything runs.
        for approval in ("YES but rename it first", "wait", "APPROVED?", "", "a" * 64):
            with self.subTest(approval=approval):
                self.load_vault.reset_mock()
                with mock.patch.object(category, "api_write_allowed") as write:
                    with self.assertRaises(category.CategoryToolError):
                        category.command_commit_create(
                            argparse.Namespace(plan=str(path), approval=approval)
                        )
                    write.assert_not_called()
                self.load_vault.assert_not_called()
                self.assertFalse(category.lock_path(saved).exists())

    def test_production_write_shape_gate_blocks_both_commits_before_service_or_lock(self) -> None:
        create_path, _ = self.stage_create("Write Gate")
        assign_path, _ = self.stage_assign([self.item("88")])
        self.load_vault.reset_mock()
        with mock.patch.object(category, "WRITE_SHAPES_VERIFIED", False), \
             mock.patch.object(category, "api_write_allowed") as write:
            for command, path in (
                (category.command_commit_create, create_path),
                (category.command_commit_assign, assign_path),
            ):
                with self.subTest(command=command.__name__), self.assertRaisesRegex(
                    category.CategoryToolError, "commit commands are disabled"
                ):
                    command(argparse.Namespace(plan=str(path), approval="APPROVED"))
                self.assertFalse(category.lock_path(
                    json.loads(path.read_text(encoding="utf-8"))["sha256"]
                ).exists())
            write.assert_not_called()
        self.load_vault.assert_not_called()

    def test_duplicate_category_name_detected_case_insensitively_at_stage_and_commit(self) -> None:
        duplicate = {"category_id": "201", "name": "Website Catalog"}
        self.load_vault.reset_mock()
        with mock.patch.object(
            category, "list_all_categories", return_value=[duplicate]
        ), self.assertRaisesRegex(category.CategoryToolError, "case-insensitively"):
            category.command_stage_create(argparse.Namespace(input=str(self.input_path({
                "category_name": "  website catalog  ",
                "sources": {"category_name": "source"},
            }))))
        self.assertFalse(any(self.plan_dir.glob("*category_create*.json")))

        path, _ = self.stage_create("Appears Later", categories=[])
        with mock.patch.object(
            category, "list_all_categories",
            return_value=[{"category_id": "202", "name": "appears later"}],
        ), mock.patch.object(category, "api_write_allowed") as write:
            with self.assertRaisesRegex(category.CategoryToolError, "re-stage"):
                category.command_commit_create(
                    argparse.Namespace(plan=str(path), approval="APPROVED")
                )
            write.assert_not_called()
        lock = json.loads(category.lock_path(
            json.loads(path.read_text(encoding="utf-8"))["sha256"]
        ).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "needs_restage")
        self.assertFalse(lock["write_attempted"])
        self.assertFalse(lock["permanent_lock"])

    def test_explicit_write_allowlist_uses_only_exact_verbs_paths_and_payload_keys(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append({
                "method": request.get_method(),
                "url": request.full_url,
                "payload": json.loads(request.data),
            })
            return FakeResponse({"code": 0})

        with mock.patch.object(category, "urlopen", side_effect=fake_urlopen):
            category.api_write_allowed(
                "token", "https://www.zohoapis.ca", "POST",
                "/inventory/v1/categories", "99", {"name": "Category"},
            )
            category.api_write_allowed(
                "token", "https://www.zohoapis.ca", "PUT",
                "/inventory/v1/items/10", "99", {"name": "Item 10", "category_id": "200"},
            )
        self.assertEqual([row["method"] for row in captured], ["POST", "PUT"])
        self.assertEqual(captured[0]["payload"], {"name": "Category"})
        self.assertEqual(captured[1]["payload"], {"name": "Item 10", "category_id": "200"})
        self.assertTrue(captured[0]["url"].endswith(
            "/inventory/v1/categories?organization_id=99"
        ))
        self.assertTrue(captured[1]["url"].endswith(
            "/inventory/v1/items/10?organization_id=99"
        ))

    def test_no_delete_rename_deactivate_stock_price_group_or_generic_write_path(self) -> None:
        forbidden = [
            ("DELETE", "/inventory/v1/categories/200", {}),
            ("PUT", "/inventory/v1/categories/200", {"name": "Rename"}),
            ("POST", "/inventory/v1/categories/200/inactive", {}),
            ("POST", "/inventory/v1/inventoryadjustments", {"quantity": 5}),
            ("PUT", "/inventory/v1/itemgroups/300", {"name": "Group"}),
            ("PUT", "/inventory/v1/items/10", {"name": "Item", "category_id": "200", "rate": 5}),
            ("PUT", "/inventory/v1/items/10", {"name": "Item", "category_id": "200", "stock_on_hand": 5}),
            ("PUT", "/inventory/v1/items/10", {"name": "Item", "category_id": "200", "group_id": "300"}),
            ("PUT", "/inventory/v1/items/10", {"category_id": "200"}),
            ("PATCH", "/inventory/v1/items/10", {"category_id": "200"}),
        ]
        with mock.patch.object(category, "urlopen") as transport:
            for method, path, payload in forbidden:
                with self.subTest(method=method, path=path, payload=payload):
                    with self.assertRaisesRegex(category.CategoryToolError, "REFUSED"):
                        category.api_write_allowed(
                            "token", "https://www.zohoapis.ca", method, path, "99", payload
                        )
            transport.assert_not_called()

    def test_create_commit_uses_duplicate_recheck_and_live_readback(self) -> None:
        path, _ = self.stage_create("Verified Category", categories=[])
        write_result = {
            "code": 0,
            "category": {"category_id": "222", "name": "Verified Category"},
        }
        with mock.patch.object(
            category, "list_all_categories",
            side_effect=[[], [{"category_id": "222", "name": "Verified Category"}]],
        ), mock.patch.object(
            category, "api_write_allowed", return_value=write_result
        ) as write, contextlib.redirect_stdout(io.StringIO()) as stdout:
            category.command_commit_create(
                argparse.Namespace(plan=str(path), approval="APPROVED")
            )
        write.assert_called_once_with(
            "token", self.vault["api_domain"], "POST", category.CATEGORY_PATH,
            self.vault["inventory_organization_id"], {"name": "Verified Category"},
        )
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["category"], {
            "category_id": "222", "name": "Verified Category"
        })
        lock = json.loads(category.lock_path(result["plan_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")
        self.assertTrue(result["replay_locked"])

    def test_assignment_exact_live_precondition_blocks_write_and_needs_restage(self) -> None:
        original = self.item("1")
        path, _ = self.stage_assign([original])
        changed = self.item("1", stock_on_hand=8.0)
        with mock.patch.object(
            category, "list_all_categories", return_value=[self.target]
        ), mock.patch.object(
            category, "get_item", return_value=changed
        ), mock.patch.object(category, "api_write_allowed") as write:
            with self.assertRaisesRegex(category.CategoryToolError, "re-stage"):
                category.command_commit_assign(
                    argparse.Namespace(plan=str(path), approval="APPROVED")
                )
            write.assert_not_called()
        sha = json.loads(path.read_text(encoding="utf-8"))["sha256"]
        lock = json.loads(category.lock_path(sha).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "needs_restage")
        self.assertEqual(lock["completed_item_ids"], [])
        self.assertIn("1", lock["failed"])
        self.assertFalse(lock["permanent_lock"])

    def test_assignment_readback_protects_every_noncategory_business_field(self) -> None:
        original = self.item("1")
        path, _ = self.stage_assign([original])
        changed_after = self.item(
            "1", category_id="200", category_name="Target Category", rate=99.0,
            last_modified_time="2026-08-07T01:00:00-0400",
        )
        with mock.patch.object(
            category, "list_all_categories", return_value=[self.target]
        ), mock.patch.object(
            category, "get_item", side_effect=[original, changed_after]
        ), mock.patch.object(
            category, "api_write_allowed", return_value={"code": 0}
        ) as write:
            with self.assertRaisesRegex(category.CategoryToolError, "indeterminate"):
                category.command_commit_assign(
                    argparse.Namespace(plan=str(path), approval="APPROVED")
                )
        self.assertEqual(write.call_count, 1)
        sha = json.loads(path.read_text(encoding="utf-8"))["sha256"]
        lock = json.loads(category.lock_path(sha).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertEqual(lock["indeterminate"], ["1"])
        self.assertFalse(lock["permanent_lock"])

    def commit_successful_assignment(self, count: int) -> tuple[Path, dict, mock.Mock]:
        originals = [self.item(str(index)) for index in range(1, count + 1)]
        path, _ = self.stage_assign(originals)
        originals_by_id = {row["item_id"]: row for row in originals}
        read_count = {item_id: 0 for item_id in originals_by_id}

        def get_item(token, vault, item_id):
            read_count[item_id] += 1
            if read_count[item_id] == 1:
                return originals_by_id[item_id]
            if read_count[item_id] == 2:
                return {
                    **originals_by_id[item_id],
                    "category_id": self.target["category_id"],
                    "category_name": self.target["name"],
                    "last_modified_time": "2026-08-07T01:00:00-0400",
                }
            self.fail(f"Unexpected third read for item {item_id}")

        def write_allowed(token, domain, method, endpoint, org_id, payload):
            item_id = endpoint.rsplit("/", 1)[-1]
            self.assertEqual(method, "PUT")
            self.assertEqual(endpoint, f"/inventory/v1/items/{item_id}")
            self.assertEqual(payload, {
                "name": originals_by_id[item_id]["name"],
                "category_id": self.target["category_id"],
            })
            return {"code": 0, "item": {"item_id": item_id}}

        write = mock.Mock(side_effect=write_allowed)
        with mock.patch.object(
            category, "list_all_categories", return_value=[self.target]
        ), mock.patch.object(
            category, "get_item", side_effect=get_item
        ), mock.patch.object(
            category, "api_write_allowed", write
        ), contextlib.redirect_stdout(io.StringIO()) as stdout:
            category.command_commit_assign(
                argparse.Namespace(plan=str(path), approval="APPROVED")
            )
        return path, json.loads(stdout.getvalue()), write

    def test_successful_one_and_twenty_item_assignment(self) -> None:
        for count in (1, 20):
            with self.subTest(count=count):
                path, result, write = self.commit_successful_assignment(count)
                expected_ids = [str(index) for index in range(1, count + 1)]
                self.assertEqual(write.call_count, count)
                self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
                self.assertEqual(result["completed_item_ids"], expected_ids)
                self.assertFalse(result["atomic"])
                self.assertTrue(result["replay_locked"])
                lock = json.loads(category.lock_path(
                    json.loads(path.read_text(encoding="utf-8"))["sha256"]
                ).read_text(encoding="utf-8"))
                self.assertEqual(lock["status"], "committed_verified")
                self.assertEqual(lock["completed_item_ids"], expected_ids)

    def test_replay_rejected_before_token_or_another_write(self) -> None:
        path, result, _ = self.commit_successful_assignment(1)
        self.load_vault.reset_mock()
        with mock.patch.object(category, "api_write_allowed") as write:
            with self.assertRaisesRegex(category.CategoryToolError, "cannot be replayed"):
                category.command_commit_assign(
                    argparse.Namespace(plan=str(path), approval="APPROVED")
                )
            write.assert_not_called()
        self.load_vault.assert_not_called()
        self.assertTrue(result["replay_locked"])

    def test_a_failed_line_is_recorded_the_batch_goes_on_and_the_plan_needs_restage(self) -> None:
        """A4/A6 (2026-08-21): one failed line does not stop the others and
        nothing is permanently locked; the same plan is bound to stale state
        and is refused, the re-stage carries only the failed lines."""
        originals = [self.item(str(index)) for index in range(1, 4)]
        path, _ = self.stage_assign(originals)
        by_id = {row["item_id"]: row for row in originals}
        assigned = lambda row: {**row, "category_id": "200", "category_name": "Target Category",  # noqa: E731
                                "last_modified_time": "2026-08-07T01:00:00-0400"}
        reads = {item_id: 0 for item_id in by_id}

        def get_item(token, vault, item_id):
            reads[item_id] += 1
            return by_id[item_id] if reads[item_id] == 1 else assigned(by_id[item_id])

        def write_allowed(token, domain, method, endpoint, org_id, payload):
            if endpoint.endswith("/2"):
                raise category.CategoryToolError("synthetic indeterminate transport failure")
            return {"code": 0}

        with mock.patch.object(
            category, "list_all_categories", return_value=[self.target]
        ), mock.patch.object(
            category, "get_item", side_effect=get_item
        ), mock.patch.object(
            category, "api_write_allowed", side_effect=write_allowed
        ) as write:
            with self.assertRaisesRegex(category.CategoryToolError, "re-stage") as caught:
                category.command_commit_assign(
                    argparse.Namespace(plan=str(path), approval="go ahead with all three")
                )
        self.assertNotIn("permanent", str(caught.exception).casefold())
        self.assertEqual(write.call_count, 3, "the third line must still run")
        sha = json.loads(path.read_text(encoding="utf-8"))["sha256"]
        lock = json.loads(category.lock_path(sha).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate_needs_restage")
        self.assertEqual(lock["completed_item_ids"], ["1", "3"])
        self.assertEqual(lock["indeterminate"], ["2"])
        self.assertEqual(list(lock["failed"]), ["2"])
        self.assertEqual(lock["restage_only"], ["2"])
        self.assertFalse(lock["permanent_lock"])
        self.assertEqual(lock["owner_go"], "go ahead with all three")
        backup = json.loads(Path(lock["backup"]).read_text(encoding="utf-8"))["state"]
        self.assertEqual(set(backup), {"1", "2", "3"})
        receipt_actions = [call.args[0] for call in self.append_receipt.call_args_list]
        self.assertIn("zoho_inventory_category_assignment_indeterminate_needs_restage", receipt_actions)

        self.load_vault.reset_mock()
        with mock.patch.object(category, "api_write_allowed") as retry:
            with self.assertRaisesRegex(category.CategoryToolError, "Re-stage"):
                category.command_commit_assign(
                    argparse.Namespace(plan=str(path), approval="APPROVED")
                )
            retry.assert_not_called()
        self.load_vault.assert_not_called()

    def test_restore_assign_puts_the_previous_categories_back(self) -> None:
        path, result, _ = self.commit_successful_assignment(2)
        live = {
            "1": self.item("1", category_id="200", category_name="Target Category"),
            "2": self.item("2", category_id="200", category_name="Target Category"),
        }
        backup = json.loads(Path(result["live_state_backup"]).read_text(encoding="utf-8"))["state"]
        self.assertEqual(backup["1"]["before_category_id"], "")
        # Give item 2 a previous category so one line is restorable and one is not.
        backup_path = category.owner_authority.live_state_path(path)
        record = json.loads(backup_path.read_text(encoding="utf-8"))
        record["state"]["2"]["before_category_id"] = "150"
        record["state_sha256"] = category.owner_authority.sha256_of(record["state"])
        backup_path.write_text(json.dumps(record), encoding="utf-8")
        writes = []

        def write_allowed(token, domain, method, endpoint, org_id, payload):
            writes.append((endpoint, payload))
            item_id = endpoint.rsplit("/", 1)[-1]
            live[item_id]["category_id"] = payload["category_id"]
            return {"code": 0}

        with mock.patch.object(
            category, "get_item", side_effect=lambda token, vault, item_id: dict(live[item_id])
        ), mock.patch.object(
            category, "api_write_allowed", side_effect=write_allowed
        ), contextlib.redirect_stdout(io.StringIO()) as stdout:
            category.command_restore_assign(argparse.Namespace(plan=str(path), approval="yes put them back"))
        restored = json.loads(stdout.getvalue())
        self.assertEqual(restored["status"], "RESTORED")
        self.assertEqual([row["item_id"] for row in restored["restored"]], ["2"])
        self.assertEqual(writes, [("/inventory/v1/items/2", {"name": "Item 2", "category_id": "150"})])
        self.assertIn("had no category before", restored["skipped"]["1"])
        with self.assertRaisesRegex(category.CategoryToolError, "already restored"):
            with contextlib.redirect_stdout(io.StringIO()):
                category.command_restore_assign(argparse.Namespace(plan=str(path), approval="yes"))
        with self.assertRaises(category.CategoryToolError):
            category.command_restore_assign(argparse.Namespace(plan=str(path), approval="wait"))

    def test_parser_exposes_restore_assign(self) -> None:
        parser = category.build_parser()
        args = parser.parse_args(["restore-assign", "--plan", "p.json", "--approval", "yes",
                                  "--approval-lane", "discord"])
        self.assertIs(args.func, category.command_restore_assign)
        self.assertEqual(args.approval_lane, "discord")

    def test_list_categories_is_read_only_and_uses_confirmed_representation(self) -> None:
        with mock.patch.object(
            category, "list_all_categories", return_value=[self.target]
        ), mock.patch.object(category, "api_write_allowed") as write, \
             contextlib.redirect_stdout(io.StringIO()) as stdout:
            category.command_list_categories(argparse.Namespace())
        write.assert_not_called()
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "READ_ONLY")
        self.assertEqual(result["method"], "GET")
        self.assertEqual(result["endpoint"], "/inventory/v1/categories")
        self.assertEqual(result["categories"], [self.target])

    def test_list_helper_uses_only_get_categories_endpoint(self) -> None:
        captured = []

        def fake_get(token, domain, path):
            captured.append(path)
            return {
                "code": 0,
                "categories": [
                    {
                        "category_id": "-1", "name": "ROOT",
                        "parent_category_id": "-1", "visibility": True,
                    },
                    {"category_id": "200", "name": "Target Category"},
                ],
                "page_context": {"has_more_page": False},
            }

        with mock.patch.object(category.zoho_tool, "api_get", side_effect=fake_get):
            raw_rows = category.list_all_categories("token", self.vault)
        rows = category.simplified_categories(raw_rows)
        self.assertEqual(rows, [{"category_id": "200", "name": "Target Category"}])
        self.assertTrue(captured[0].startswith("/inventory/v1/categories?"))
        self.assertIn("organization_id=99", captured[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
