from __future__ import annotations

import argparse
import contextlib
from datetime import timedelta
import io
import inspect
import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlencode, urlsplit
import tempfile
import unittest
from unittest import mock

import zoho_inventory_classification_tool as classification


class FakeResponse:
    def __init__(self, payload=None, raw: bytes | None = None):
        self.raw = raw if raw is not None else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.raw


class ZohoInventoryClassificationToolTests(unittest.TestCase):
    def setUp(self) -> None:
        here = Path(__file__).resolve().parent
        self.real_execute_ui = classification._execute_ui_request
        self.temp = tempfile.TemporaryDirectory(dir=here)
        self.root = Path(self.temp.name)
        self.plan_dir = self.root / "plans"
        self.plan_dir.mkdir()
        self.vault = {
            "api_domain": "https://www.zohoapis.ca",
            "inventory_organization_id": "99",
            "scopes": [classification.UPDATE_SCOPE],
        }
        self.counter = 0
        self.target = self.field()
        self.patchers = [
            mock.patch.object(classification, "PLAN_DIR", self.plan_dir),
            mock.patch.object(classification.zoho_tool, "load_vault", return_value=self.vault),
            mock.patch.object(
                classification.zoho_tool,
                "refresh_access_token",
                return_value=("token", self.vault),
            ),
            mock.patch.object(classification.zoho_tool, "save_vault"),
            mock.patch.object(classification.zoho_tool, "append_receipt"),
            mock.patch.object(
                classification,
                "_execute_ui_request",
                side_effect=AssertionError("live UI/network is forbidden in tests"),
            ),
            mock.patch.object(
                classification,
                "_execute_native_field_create",
                side_effect=AssertionError("live native UI write is forbidden in tests"),
            ),
            mock.patch.object(
                classification.zoho_tool,
                "api_get",
                side_effect=AssertionError("live Zoho item GET is forbidden in tests"),
            ),
            mock.patch.object(
                classification,
                "urlopen",
                side_effect=AssertionError("live OAuth/network is forbidden in tests"),
            ),
        ]
        started = [patcher.start() for patcher in self.patchers]
        self.load_vault = started[1]
        self.refresh_access_token = started[2]
        self.save_vault = started[3]
        self.append_receipt = started[4]
        self.execute_ui = started[5]
        self.execute_native = started[6]
        self.api_get = started[7]
        self.urlopen = started[8]

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
    def field(**changes) -> dict:
        value = {
            "customfield_id": "900",
            "label": classification.FIELD_LABEL,
            "data_type": "dropdown",
            "is_active": True,
            "values": [
                {"name": name, "order": index, "is_active": True}
                for index, name in enumerate(classification.CLASSIFICATIONS, start=1)
            ],
        }
        value.update(changes)
        return value

    @staticmethod
    def fields_response(*rows: dict) -> dict:
        return {"code": 0, "fields": list(rows)}

    def item(self, item_id: str, *, target_value=mock.sentinel.absent, **changes) -> dict:
        custom_fields = [
            {
                "customfield_id": "800",
                "label": "Protected Other Field",
                "value": "preserve-me",
            }
        ]
        if target_value is not mock.sentinel.absent:
            custom_fields.append({
                "customfield_id": "900",
                "label": classification.FIELD_LABEL,
                "value": target_value,
            })
        value = {
            "item_id": item_id,
            "name": f"Item {item_id}",
            "sku": f"SKU-{item_id}",
            "group_id": "300",
            "group_name": "Protected Group",
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
            "custom_fields": custom_fields,
            "last_modified_time": "2026-08-07T00:00:00-0400",
        }
        value.update(changes)
        return value

    def assigned_item(self, original: dict, value: str, **changes) -> dict:
        rows = []
        found = False
        for row in original["custom_fields"]:
            row = dict(row)
            if row["customfield_id"] == self.target["customfield_id"]:
                row["value"] = value
                found = True
            rows.append(row)
        if not found:
            rows.append({
                "customfield_id": self.target["customfield_id"],
                "label": classification.FIELD_LABEL,
                "value": value,
            })
        result = {
            **original,
            "custom_fields": rows,
            # Live Zoho derives these keys from the intended target custom
            # field after a PUT. They are not an independent business field.
            "custom_field_hash": {
                **original.get("custom_field_hash", {}),
                "cf_catalog_classification": value,
                "cf_catalog_classification_unformatted": value,
            },
            "last_modified_time": "2026-08-07T01:00:00-0400",
        }
        result.update(changes)
        return result

    def stage_create(self, source: str = "Rachad's commissioned field specification"):
        with mock.patch.object(
            classification, "ui_list_fields", return_value=self.fields_response()
        ), contextlib.redirect_stdout(io.StringIO()) as stdout:
            classification.command_stage_create(argparse.Namespace(input=str(
                self.input_path({"source": source})
            )))
        result = json.loads(stdout.getvalue())
        return Path(result["plan"]), result

    def stage_assign(
        self,
        items: list[dict],
        value: str = "Website Catalog",
        field: dict | None = None,
    ):
        field = dict(field or self.target)
        by_id = {str(row["item_id"]): row for row in items}
        with mock.patch.object(
            classification,
            "ui_list_fields",
            return_value=self.fields_response(field),
        ), mock.patch.object(
            classification,
            "get_item",
            side_effect=lambda token, vault, item_id: by_id[item_id],
        ), contextlib.redirect_stdout(io.StringIO()) as stdout:
            classification.command_stage_assign(argparse.Namespace(input=str(
                self.input_path({
                    "classification": value,
                    "item_ids": list(by_id),
                    "sources": {"classification": "Rachad's reviewed item classification"},
                })
            )))
        result = json.loads(stdout.getvalue())
        return Path(result["plan"]), result

    @staticmethod
    def rewrite_with_hash(path: Path, mutate) -> dict:
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan.pop("sha256", None)
        mutate(plan)
        plan["sha256"] = classification.digest_for(plan)
        path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        return plan

    def test_fixed_definition_is_exact_and_production_has_no_write_shape_bypass(self) -> None:
        expected = {
            "is_mandatory": False,
            "is_basecurrency_amount": True,
            "data_type": "dropdown",
            "pii_type": "non_pii",
            "default_value": "",
            "entity": "item",
            "values": [
                {"name": "Website Catalog", "order": 1, "is_active": True},
                {"name": "Custom / Customer-Specific", "order": 2, "is_active": True},
                {"name": "Review / Unclassified", "order": 3, "is_active": True},
            ],
            "is_unique": False,
            "label": "Catalog Classification",
            "selected_txn_entities": [],
            "external_fields": [None],
            "field_preferences": {"is_color_code_supported": False},
            "show_on_pdf": False,
        }
        self.assertEqual(classification.FIXED_FIELD_DEFINITION, expected)
        self.assertFalse(hasattr(classification, "WRITE_SHAPES_VERIFIED"))
        self.assertEqual(
            list(inspect.signature(self.real_execute_ui).parameters),
            ["url"],
        )
        self.assertEqual(
            classification.PLAN_DIR,
            self.plan_dir,
        )

    def test_closed_input_schemas_reject_before_vault_session_or_token(self) -> None:
        bad_create = [
            {},
            {"source": "s", "extra": True},
            {"source": ""},
            {"source": " surrounding "},
        ]
        bad_assign = [
            {
                "classification": "Website Catalog",
                "item_ids": ["1"],
                "sources": {"classification": "s"},
                "sku": "forbidden",
            },
            {
                "classification": "website catalog",
                "item_ids": ["1"],
                "sources": {"classification": "s"},
            },
            {
                "classification": "Website Catalog",
                "item_ids": ["1"],
                "sources": {},
            },
            {
                "classification": "Website Catalog",
                "item_ids": ["1"],
                "sources": {"classification": "s", "other": "x"},
            },
        ]
        for kind, values, command in (
            ("create", bad_create, classification.command_stage_create),
            ("assign", bad_assign, classification.command_stage_assign),
        ):
            for index, value in enumerate(values):
                with self.subTest(kind=kind, index=index):
                    self.load_vault.reset_mock()
                    self.refresh_access_token.reset_mock()
                    with self.assertRaises(classification.ClassificationToolError):
                        command(argparse.Namespace(input=str(self.input_path(value))))
                    self.load_vault.assert_not_called()
                    self.refresh_access_token.assert_not_called()
                    self.execute_ui.assert_not_called()

    def test_assignment_requires_one_to_twenty_unique_positive_ids_before_access(self) -> None:
        cases = [
            [],
            [str(index) for index in range(1, 22)],
            ["1", "1"],
            ["0"],
            [-1],
            [True],
            ["not-an-id"],
            "1",
        ]
        for index, item_ids in enumerate(cases):
            with self.subTest(index=index):
                self.load_vault.reset_mock()
                path = self.input_path({
                    "classification": "Website Catalog",
                    "item_ids": item_ids,
                    "sources": {"classification": "source"},
                })
                with self.assertRaises(classification.ClassificationToolError):
                    classification.command_stage_assign(argparse.Namespace(input=str(path)))
                self.load_vault.assert_not_called()

    def test_stage_plans_have_full_hash_nonce_exact_24_hours_status_and_approval(self) -> None:
        create_path, create_result = self.stage_create()
        assign_path, assign_result = self.stage_assign([self.item("1")])
        for path, result in ((create_path, create_result), (assign_path, assign_result)):
            with self.subTest(path=path.name):
                plan = json.loads(path.read_text(encoding="utf-8"))
                saved = plan.pop("sha256")
                self.assertRegex(saved, r"^[0-9a-f]{64}$")
                self.assertEqual(saved, classification.digest_for(plan))
                self.assertRegex(plan["nonce"], r"^[0-9a-f]{32}$")
                created = classification.parse_time(plan["created_utc"], "created")
                expires = classification.parse_time(plan["expires_utc"], "expires")
                self.assertEqual(expires - created, timedelta(hours=24))
                self.assertEqual(result["status"], "STAGED_NOT_COMMITTED")
                self.assertEqual(result["approval"], "APPROVED")
        create_plan = json.loads(create_path.read_text(encoding="utf-8"))
        self.assertEqual(
            create_plan["payload"],
            {"field_definition": classification.FIXED_FIELD_DEFINITION},
        )

    def test_stage_assignment_preserves_all_existing_custom_field_values(self) -> None:
        original = self.item("4", target_value="Review / Unclassified")
        path, result = self.stage_assign(
            [original], value="Custom / Customer-Specific"
        )
        plan = json.loads(path.read_text(encoding="utf-8"))
        evidence = plan["live_evidence"]["items"][0]
        self.assertEqual(evidence["current_custom_fields"], [
            {"customfield_id": "800", "value": "preserve-me"},
            {"customfield_id": "900", "value": "Review / Unclassified"},
        ])
        self.assertEqual(evidence["assignment_custom_fields"], [
            {"customfield_id": "800", "value": "preserve-me"},
            {"customfield_id": "900", "value": "Custom / Customer-Specific"},
        ])
        self.assertEqual(evidence["name"], original["name"])
        self.assertFalse(result["atomic"])
        self.assertEqual(
            evidence["protected_state_sha256"],
            classification.digest_for(evidence["protected_state"]),
        )

    def test_stage_assignment_rejects_already_classified_same_value(self) -> None:
        with self.assertRaisesRegex(
            classification.ClassificationToolError, "already classified"
        ):
            self.stage_assign([
                self.item("5", target_value="Website Catalog")
            ])
        self.assertFalse(any(self.plan_dir.glob("*classification_assign*.json")))

    def test_path_containment_rejects_relative_outside_and_symlink(self) -> None:
        plan_path, _ = self.stage_create()
        outside_dir = self.root / "outside"
        outside_dir.mkdir()
        outside = outside_dir / "plan.json"
        outside.write_text(plan_path.read_text(encoding="utf-8"), encoding="utf-8")
        bad_paths = [plan_path.name, str(outside.resolve())]
        symlink = self.plan_dir / "linked-plan.json"
        try:
            os.symlink(plan_path, symlink)
        except (OSError, NotImplementedError):
            symlink = None
        if symlink is not None:
            bad_paths.append(str(symlink.absolute()))
        for bad_path in bad_paths:
            with self.subTest(path=bad_path), self.assertRaises(
                classification.ClassificationToolError
            ):
                classification.command_commit_create(argparse.Namespace(
                    plan=bad_path, approval="APPROVED"
                ))

    def test_hash_tamper_expiry_and_closed_plan_extras_reject_before_access(self) -> None:
        tampered, _ = self.stage_create("tamper source")
        plan = json.loads(tampered.read_text(encoding="utf-8"))
        plan["payload"]["field_definition"]["show_on_pdf"] = True
        tampered.write_text(json.dumps(plan), encoding="utf-8")
        self.load_vault.reset_mock()
        with self.assertRaisesRegex(classification.ClassificationToolError, "hash check failed"):
            classification.command_commit_create(argparse.Namespace(
                plan=str(tampered), approval="APPROVED"
            ))
        self.load_vault.assert_not_called()

        expired, _ = self.stage_create("expiry source")
        expiry = classification.utc_now() - timedelta(seconds=1)
        self.rewrite_with_hash(expired, lambda value: value.update({
            "created_utc": (expiry - timedelta(hours=24)).isoformat(),
            "expires_utc": expiry.isoformat(),
        }))
        self.load_vault.reset_mock()
        with self.assertRaisesRegex(classification.ClassificationToolError, "expired"):
            classification.command_commit_create(argparse.Namespace(
                plan=str(expired), approval="APPROVED"
            ))
        self.load_vault.assert_not_called()

        for index, mutation in enumerate((
            lambda value: value.update({"extra": True}),
            lambda value: value["payload"].update({"other_field": True}),
            lambda value: value["sources"].update({"other": "x"}),
        )):
            with self.subTest(closed=index):
                path, _ = self.stage_create(f"closed source {index}")
                self.rewrite_with_hash(path, mutation)
                self.load_vault.reset_mock()
                with self.assertRaises(classification.ClassificationToolError):
                    classification.command_commit_create(argparse.Namespace(
                        plan=str(path), approval="APPROVED"
                    ))
                self.load_vault.assert_not_called()

    def test_wrong_or_multiword_approval_refuses_before_lock_vault_token_or_network(self) -> None:
        path, _ = self.stage_assign([self.item("7")])
        sha = json.loads(path.read_text(encoding="utf-8"))["sha256"]
        for approval in ("YES", "APPROVED NOW", "APPROVE", "", "a" * 64, None):
            with self.subTest(approval=approval):
                self.load_vault.reset_mock()
                self.refresh_access_token.reset_mock()
                with mock.patch.object(classification, "ui_list_fields") as ui, \
                     mock.patch.object(classification, "oauth_item_write_allowed") as write:
                    with self.assertRaises(classification.ClassificationToolError):
                        classification.command_commit_assign(argparse.Namespace(
                            plan=str(path), approval=approval
                        ))
                    ui.assert_not_called()
                    write.assert_not_called()
                self.load_vault.assert_not_called()
                self.refresh_access_token.assert_not_called()
                self.assertFalse(classification.lock_path(sha).exists())

    def test_duplicate_and_wrong_field_shape_are_rejected(self) -> None:
        wrongs = [
            self.field(is_active=False),
            self.field(data_type="text"),
            self.field(label="catalog classification"),
            self.field(values=list(reversed(self.target["values"]))),
            self.field(values=[
                {**row, "is_active": False} if index == 1 else row
                for index, row in enumerate(self.target["values"])
            ]),
        ]
        for index, wrong in enumerate(wrongs):
            with self.subTest(index=index), mock.patch.object(
                classification,
                "ui_list_fields",
                return_value=self.fields_response(wrong),
            ), self.assertRaises(classification.ClassificationToolError):
                classification.command_stage_assign(argparse.Namespace(input=str(
                    self.input_path({
                        "classification": "Website Catalog",
                        "item_ids": ["1"],
                        "sources": {"classification": "source"},
                    })
                )))
        with mock.patch.object(
            classification,
            "ui_list_fields",
            return_value=self.fields_response(self.target, self.field(customfield_id="901")),
        ), self.assertRaisesRegex(classification.ClassificationToolError, "exactly one"):
            classification.command_stage_assign(argparse.Namespace(input=str(
                self.input_path({
                    "classification": "Website Catalog",
                    "item_ids": ["1"],
                    "sources": {"classification": "source"},
                })
            )))

    def test_create_stage_rejects_existing_exact_or_wrong_shape_field(self) -> None:
        for row in (self.target, self.field(is_active=False)):
            with self.subTest(row=row), mock.patch.object(
                classification,
                "ui_list_fields",
                return_value=self.fields_response(row),
            ), self.assertRaises(classification.ClassificationToolError):
                classification.command_stage_create(argparse.Namespace(input=str(
                    self.input_path({"source": "source"})
                )))

    def test_ui_transport_get_and_post_have_exact_shapes(self) -> None:
        captured_get = []
        captured_post = []

        def fake_execute(url):
            captured_get.append(url)
            return {
                "status": 200,
                "ok": True,
                "text": json.dumps(self.fields_response()),
            }

        def fake_native(org_id, body):
            captured_post.append((org_id, body))
            return {"status": 200, "ok": True, "text": json.dumps({"code": 0})}

        with mock.patch.object(
            classification, "_execute_ui_request", side_effect=fake_execute
        ), mock.patch.object(
            classification, "_execute_native_field_create", side_effect=fake_native
        ):
            self.assertEqual(
                classification.ui_list_fields("99"), self.fields_response()
            )
            self.assertEqual(classification.ui_create_fixed_field("99"), {"code": 0})
        get_url = captured_get[0]
        parsed_get = urlsplit(get_url)
        self.assertEqual(parsed_get.path, classification.FIELD_PATH)
        self.assertEqual(
            parse_qs(parsed_get.query), {"entity": ["item"], "organization_id": ["99"]}
        )
        post_org_id, post_body = captured_post[0]
        self.assertEqual(post_org_id, "99")
        outer = parse_qs(post_body, strict_parsing=True, keep_blank_values=True)
        self.assertEqual(set(outer), {"JSONString", "organization_id"})
        self.assertEqual(outer["organization_id"], ["99"])
        self.assertEqual(json.loads(outer["JSONString"][0]), classification.FIXED_FIELD_DEFINITION)

    def test_native_field_post_validator_accepts_only_exact_fixed_request(self) -> None:
        fixed_json = json.dumps(
            classification.FIXED_FIELD_DEFINITION,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        body = urlencode([
            ("JSONString", fixed_json),
            ("organization_id", "99"),
        ])
        classification._validate_native_field_post(
            "https://inventory.zohocloud.ca/api/v1/settings/fields",
            "POST",
            body,
            "99",
            body,
        )
        cases = [
            ("http://inventory.zohocloud.ca/api/v1/settings/fields", "POST", body),
            ("https://example.com/api/v1/settings/fields", "POST", body),
            ("https://inventory.zohocloud.ca/api/v1/settings/other", "POST", body),
            ("https://inventory.zohocloud.ca/api/v1/settings/fields?extra=1", "POST", body),
            ("https://inventory.zohocloud.ca/api/v1/settings/fields", "PUT", body),
            ("https://inventory.zohocloud.ca/api/v1/settings/fields", "POST", None),
            ("https://inventory.zohocloud.ca/api/v1/settings/fields", "POST", body + "&extra=x"),
            ("https://inventory.zohocloud.ca/api/v1/settings/fields", "POST", body.replace("organization_id=99", "organization_id=100")),
        ]
        for url, method, post_data in cases:
            with self.subTest(url=url, method=method), self.assertRaisesRegex(
                classification.ClassificationToolError, "REFUSED"
            ):
                classification._validate_native_field_post(
                    url, method, post_data, "99", body
                )

    def test_dropdown_slots_allow_only_three_fixed_values_plus_blank_extras(self) -> None:
        for count in (3, 4, 7):
            classification._validate_dropdown_slots([""] * count, filled=False)
            classification._validate_dropdown_slots(
                list(classification.CLASSIFICATIONS) + ([""] * (count - 3)),
                filled=True,
            )
        invalid = [
            (["", ""], False),
            (["", "", "prefilled", ""], False),
            (["Website Catalog", "Custom / Customer-Specific", ""], True),
            ([
                "Custom / Customer-Specific",
                "Website Catalog",
                "Review / Unclassified",
                "",
            ], True),
            (list(classification.CLASSIFICATIONS) + ["unexpected"], True),
            (["", "", "", None], False),
        ]
        for values, filled in invalid:
            with self.subTest(values=values, filled=filled), self.assertRaises(
                classification.ClassificationToolError
            ):
                classification._validate_dropdown_slots(values, filled=filled)

    def test_real_custom_field_metadata_shape_is_recognized_fail_closed(self) -> None:
        row = self.field(customfield_id="96274000001547006")
        field_id = row.pop("customfield_id")
        row.pop("label")
        row.update({
            "field_id": field_id,
            "field_name": "cf_catalog_classification",
            "api_name": "cf_catalog_classification",
            "field_name_formatted": "Catalog Classification",
            "is_custom_field": True,
        })
        expected = {
            **self.target,
            "customfield_id": field_id,
        }
        self.assertEqual(
            classification.require_exact_target_field(self.fields_response(row)),
            expected,
        )
        for key, value in (
            ("field_name", "cf_other"),
            ("api_name", "cf_other"),
            ("field_name_formatted", "Other"),
            ("is_custom_field", False),
        ):
            changed = dict(row)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(
                classification.ClassificationToolError
            ):
                classification.require_exact_target_field(
                    self.fields_response(changed)
                )

    def test_ui_write_allowlist_rejects_verbs_paths_content_outer_extras_and_org_mismatch(self) -> None:
        fixed_json = json.dumps(classification.FIXED_FIELD_DEFINITION)
        valid_outer = {"JSONString": fixed_json, "organization_id": "99"}
        cases = [
            ("DELETE", classification.FIELD_PATH, classification.UI_CONTENT_TYPE, valid_outer),
            ("PUT", classification.FIELD_PATH, classification.UI_CONTENT_TYPE, valid_outer),
            ("POST", "/api/v1/settings/other", classification.UI_CONTENT_TYPE, valid_outer),
            ("POST", classification.FIELD_PATH, "application/json", valid_outer),
            ("POST", classification.FIELD_PATH, classification.UI_CONTENT_TYPE,
             {**valid_outer, "extra": "x"}),
            ("POST", classification.FIELD_PATH, classification.UI_CONTENT_TYPE,
             {"JSONString": fixed_json}),
            ("POST", classification.FIELD_PATH, classification.UI_CONTENT_TYPE,
             {**valid_outer, "organization_id": "100"}),
            ("POST", classification.FIELD_PATH, classification.UI_CONTENT_TYPE,
             {**valid_outer, "JSONString": json.dumps({**classification.FIXED_FIELD_DEFINITION,
                                                        "show_on_pdf": True})}),
        ]
        for method, path, content_type, outer in cases:
            with self.subTest(method=method, path=path), self.assertRaisesRegex(
                classification.ClassificationToolError, "REFUSED"
            ):
                classification.ui_transport_allowed(
                    method, path, "99", content_type=content_type, outer_fields=outer
                )
        self.execute_ui.assert_not_called()

    def test_oauth_write_transport_uses_exact_put_path_and_payload(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append(request)
            return FakeResponse({"code": 0, "item": {"item_id": "10"}})

        payload = {
            "name": "Item 10",
            "custom_fields": [
                {"customfield_id": "800", "value": "preserve-me"},
                {"customfield_id": "900", "value": "Website Catalog"},
            ],
        }
        with mock.patch.object(classification, "urlopen", side_effect=fake_urlopen):
            result = classification.oauth_item_write_allowed(
                "token", "https://www.zohoapis.ca", "PUT",
                "/inventory/v1/items/10", "99", payload,
            )
        self.assertEqual(result["code"], 0)
        request = captured[0]
        self.assertEqual(request.get_method(), "PUT")
        parsed = urlsplit(request.full_url)
        self.assertEqual(parsed.path, "/inventory/v1/items/10")
        self.assertEqual(parse_qs(parsed.query), {"organization_id": ["99"]})
        self.assertEqual(json.loads(request.data), payload)
        self.assertEqual(request.headers["Content-type"], "application/json")

    def test_oauth_write_allowlist_rejects_forbidden_paths_payload_extras_and_malformed_rows(self) -> None:
        good = {
            "name": "Item 10",
            "custom_fields": [{"customfield_id": "900", "value": "Website Catalog"}],
        }
        bad = [
            ("DELETE", "/inventory/v1/items/10", good),
            ("PATCH", "/inventory/v1/items/10", good),
            ("PUT", "/inventory/v1/items", good),
            ("PUT", "/inventory/v1/items/0", good),
            ("PUT", "/inventory/v1/items/10", {**good, "rate": 99}),
            ("PUT", "/inventory/v1/items/10", {"custom_fields": good["custom_fields"]}),
            ("PUT", "/inventory/v1/items/10", {"name": "Item", "custom_fields": [
                {"customfield_id": "900", "value": "Website Catalog", "label": "extra"}
            ]}),
            ("PUT", "/inventory/v1/items/10", {"name": "Item", "custom_fields": [
                {"customfield_id": "0", "value": "Website Catalog"}
            ]}),
            ("PUT", "/inventory/v1/items/10", {"name": "Item", "custom_fields": [
                {"customfield_id": "900", "value": "Website Catalog"},
                {"customfield_id": "900", "value": "Review / Unclassified"},
            ]}),
        ]
        with mock.patch.object(classification, "urlopen") as transport:
            for method, path, payload in bad:
                with self.subTest(method=method, path=path), self.assertRaisesRegex(
                    classification.ClassificationToolError, "REFUSED"
                ):
                    classification.oauth_item_write_allowed(
                        "token", "https://www.zohoapis.ca", method, path, "99", payload
                    )
            transport.assert_not_called()

    def test_invalid_and_unknown_write_responses_are_rejected(self) -> None:
        for raw in (
            {"status": 200, "ok": True, "text": "not-json"},
            {"status": 200, "ok": True, "text": "{}"},
            {"status": 500, "ok": False, "text": "{}"},
            {"status": 200, "ok": True},
        ):
            with self.subTest(ui=raw), self.assertRaises(classification.ClassificationToolError):
                classification._decode_ui_result(raw, write=True)
        payload = {
            "name": "Item 10",
            "custom_fields": [{"customfield_id": "900", "value": "Website Catalog"}],
        }
        responses = [
            FakeResponse(raw=b"not-json"),
            FakeResponse({}),
            FakeResponse({"code": 1, "message": "failed"}),
            URLError("synthetic disconnect"),
        ]
        for response in responses:
            with self.subTest(oauth=response), mock.patch.object(
                classification, "urlopen", side_effect=response
                if isinstance(response, Exception) else lambda request, timeout, r=response: r
            ), self.assertRaises(classification.ClassificationToolError):
                classification.oauth_item_write_allowed(
                    "token", "https://www.zohoapis.ca", "PUT",
                    "/inventory/v1/items/10", "99", payload,
                )

    def test_create_commit_rechecks_absence_uses_fixed_write_and_live_readback(self) -> None:
        path, _ = self.stage_create()
        with mock.patch.object(
            classification,
            "ui_list_fields",
            side_effect=[self.fields_response(), self.fields_response(self.target)],
        ) as ui, mock.patch.object(
            classification, "ui_create_fixed_field", return_value={"code": 0}
        ) as write, contextlib.redirect_stdout(io.StringIO()) as stdout:
            classification.command_commit_create(argparse.Namespace(
                plan=str(path), approval="APPROVED"
            ))
        self.assertEqual(ui.call_count, 2)
        write.assert_called_once_with("99")
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["field"], self.target)
        lock = json.loads(classification.lock_path(result["plan_sha256"]).read_text())
        self.assertEqual(lock["status"], "committed_verified")
        self.assertTrue(lock["no_retry"])

    def test_create_commit_prewrite_collision_and_postwrite_bad_readback_lock_permanently(self) -> None:
        collision_path, _ = self.stage_create("collision source")
        with mock.patch.object(
            classification, "ui_list_fields", return_value=self.fields_response(self.target)
        ), mock.patch.object(classification, "ui_create_fixed_field") as write, \
             self.assertRaisesRegex(classification.ClassificationToolError, "aborted_before_write"):
            classification.command_commit_create(argparse.Namespace(
                plan=str(collision_path), approval="APPROVED"
            ))
        write.assert_not_called()
        collision_sha = json.loads(collision_path.read_text())["sha256"]
        collision_lock = json.loads(classification.lock_path(collision_sha).read_text())
        self.assertEqual(collision_lock["status"], "aborted_before_write")

        bad_path, _ = self.stage_create("bad readback source")
        with mock.patch.object(
            classification,
            "ui_list_fields",
            side_effect=[self.fields_response(), self.fields_response()],
        ), mock.patch.object(
            classification, "ui_create_fixed_field", return_value={"code": 0}
        ), self.assertRaisesRegex(classification.ClassificationToolError, "indeterminate"):
            classification.command_commit_create(argparse.Namespace(
                plan=str(bad_path), approval="APPROVED"
            ))
        bad_sha = json.loads(bad_path.read_text())["sha256"]
        bad_lock = json.loads(classification.lock_path(bad_sha).read_text())
        self.assertEqual(bad_lock["status"], "indeterminate")
        self.assertTrue(bad_lock["no_retry"])

    def test_assignment_stale_field_and_stale_item_abort_before_write_and_lock(self) -> None:
        field_path, _ = self.stage_assign([self.item("11")])
        changed_field = self.field(customfield_id="901")
        with mock.patch.object(
            classification,
            "ui_list_fields",
            return_value=self.fields_response(changed_field),
        ), mock.patch.object(classification, "get_item") as get, \
             mock.patch.object(classification, "oauth_item_write_allowed") as write, \
             self.assertRaisesRegex(classification.ClassificationToolError, "aborted_before_write"):
            classification.command_commit_assign(argparse.Namespace(
                plan=str(field_path), approval="APPROVED"
            ))
        get.assert_not_called()
        write.assert_not_called()
        lock = json.loads(classification.lock_path(
            json.loads(field_path.read_text())["sha256"]
        ).read_text())
        self.assertEqual(lock["status"], "aborted_before_write")

        original = self.item("12")
        item_path, _ = self.stage_assign([original])
        changed = self.item("12", stock_on_hand=8.0)
        with mock.patch.object(
            classification,
            "ui_list_fields",
            return_value=self.fields_response(self.target),
        ), mock.patch.object(
            classification, "get_item", return_value=changed
        ), mock.patch.object(
            classification, "oauth_item_write_allowed"
        ) as write, self.assertRaisesRegex(
            classification.ClassificationToolError, "aborted_before_write"
        ):
            classification.command_commit_assign(argparse.Namespace(
                plan=str(item_path), approval="APPROVED"
            ))
        write.assert_not_called()

    def commit_successful_assignment(self, count: int):
        originals = [self.item(str(index)) for index in range(1, count + 1)]
        path, _ = self.stage_assign(originals)
        by_id = {row["item_id"]: row for row in originals}
        read_count = {item_id: 0 for item_id in by_id}

        def get_item(token, vault, item_id):
            read_count[item_id] += 1
            if read_count[item_id] == 1:
                return by_id[item_id]
            if read_count[item_id] == 2:
                return self.assigned_item(by_id[item_id], "Website Catalog")
            self.fail(f"unexpected item read {read_count[item_id]} for {item_id}")

        def write_allowed(token, domain, method, endpoint, org_id, payload):
            item_id = endpoint.rsplit("/", 1)[-1]
            self.assertEqual(method, "PUT")
            self.assertEqual(payload, {
                "name": by_id[item_id]["name"],
                "custom_fields": [
                    {"customfield_id": "800", "value": "preserve-me"},
                    {"customfield_id": "900", "value": "Website Catalog"},
                ],
            })
            return {"code": 0}

        write = mock.Mock(side_effect=write_allowed)
        with mock.patch.object(
            classification,
            "ui_list_fields",
            return_value=self.fields_response(self.target),
        ) as ui, mock.patch.object(
            classification, "get_item", side_effect=get_item
        ), mock.patch.object(
            classification, "oauth_item_write_allowed", write
        ), contextlib.redirect_stdout(io.StringIO()) as stdout:
            classification.command_commit_assign(argparse.Namespace(
                plan=str(path), approval="APPROVED"
            ))
        return path, json.loads(stdout.getvalue()), write, ui

    def test_successful_one_and_twenty_item_assignments_recheck_each_and_read_back(self) -> None:
        for count in (1, 20):
            with self.subTest(count=count):
                path, result, write, ui = self.commit_successful_assignment(count)
                ids = [str(index) for index in range(1, count + 1)]
                self.assertEqual(write.call_count, count)
                self.assertEqual(ui.call_count, count)
                self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
                self.assertEqual(result["completed_item_ids"], ids)
                self.assertFalse(result["atomic"])
                self.assertTrue(result["replay_locked"])
                lock = json.loads(classification.lock_path(
                    json.loads(path.read_text())["sha256"]
                ).read_text())
                self.assertEqual(lock["status"], "committed_verified")
                self.assertEqual(lock["completed_item_ids"], ids)

    def test_readback_rejects_protected_top_level_or_other_custom_value_change(self) -> None:
        cases = [
            lambda original: self.assigned_item(
                original, "Website Catalog", rate=99.0
            ),
            lambda original: {
                **self.assigned_item(original, "Website Catalog"),
                "custom_fields": [
                    {"customfield_id": "800", "label": "Protected Other Field", "value": "changed"},
                    {"customfield_id": "900", "label": classification.FIELD_LABEL,
                     "value": "Website Catalog"},
                ],
            },
        ]
        for index, make_after in enumerate(cases):
            with self.subTest(index=index):
                original = self.item(str(30 + index))
                path, _ = self.stage_assign([original])
                with mock.patch.object(
                    classification,
                    "ui_list_fields",
                    return_value=self.fields_response(self.target),
                ), mock.patch.object(
                    classification,
                    "get_item",
                    side_effect=[original, make_after(original)],
                ), mock.patch.object(
                    classification,
                    "oauth_item_write_allowed",
                    return_value={"code": 0},
                ) as write, self.assertRaisesRegex(
                    classification.ClassificationToolError, "indeterminate"
                ):
                    classification.command_commit_assign(argparse.Namespace(
                        plan=str(path), approval="APPROVED"
                    ))
                write.assert_called_once()
                lock = json.loads(classification.lock_path(
                    json.loads(path.read_text())["sha256"]
                ).read_text())
                self.assertEqual(lock["status"], "indeterminate")
                self.assertEqual(lock["write_in_flight_item_id"], str(30 + index))

    def test_protected_state_ignores_derived_hash_but_keeps_other_custom_values(self) -> None:
        original = self.item("39", custom_field_hash={})
        assigned = self.assigned_item(original, "Website Catalog")
        before = classification.protected_item_state(original, "900")
        after = classification.protected_item_state(assigned, "900")
        self.assertEqual(before, after)

        changed_other = {
            **assigned,
            "custom_fields": [
                {"customfield_id": "800", "label": "Protected Other Field", "value": "changed"},
                {"customfield_id": "900", "label": classification.FIELD_LABEL,
                 "value": "Website Catalog"},
            ],
        }
        self.assertNotEqual(
            before,
            classification.protected_item_state(changed_other, "900"),
        )

    def test_partial_and_indeterminate_failures_are_permanently_locked_no_retry(self) -> None:
        originals = [self.item("41"), self.item("42")]
        path, _ = self.stage_assign(originals)
        reads = [
            originals[0],
            self.assigned_item(originals[0], "Website Catalog"),
            originals[1],
        ]
        with mock.patch.object(
            classification,
            "ui_list_fields",
            return_value=self.fields_response(self.target),
        ), mock.patch.object(
            classification, "get_item", side_effect=reads
        ), mock.patch.object(
            classification,
            "oauth_item_write_allowed",
            side_effect=[{"code": 0}, ClassificationToolSyntheticError("disconnect")],
        ) as write, self.assertRaisesRegex(
            classification.ClassificationToolError, "partial"
        ):
            classification.command_commit_assign(argparse.Namespace(
                plan=str(path), approval="APPROVED"
            ))
        self.assertEqual(write.call_count, 2)
        sha = json.loads(path.read_text())["sha256"]
        lock = json.loads(classification.lock_path(sha).read_text())
        self.assertEqual(lock["status"], "partial")
        self.assertEqual(lock["completed_item_ids"], ["41"])
        self.assertEqual(lock["write_in_flight_item_id"], "42")
        self.assertTrue(lock["no_retry"])
        actions = [call.args[0] for call in self.append_receipt.call_args_list]
        self.assertIn(
            "zoho_inventory_classification_assignment_partial_indeterminate_or_aborted_no_retry",
            actions,
        )
        self.load_vault.reset_mock()
        with mock.patch.object(classification, "oauth_item_write_allowed") as retry, \
             self.assertRaisesRegex(classification.ClassificationToolError, "cannot be replayed"):
            classification.command_commit_assign(argparse.Namespace(
                plan=str(path), approval="APPROVED"
            ))
        retry.assert_not_called()
        self.load_vault.assert_not_called()

        single = self.item("43")
        indeterminate_path, _ = self.stage_assign([single])
        with mock.patch.object(
            classification,
            "ui_list_fields",
            return_value=self.fields_response(self.target),
        ), mock.patch.object(
            classification, "get_item", return_value=single
        ), mock.patch.object(
            classification,
            "oauth_item_write_allowed",
            side_effect=ClassificationToolSyntheticError("disconnect"),
        ), self.assertRaisesRegex(classification.ClassificationToolError, "indeterminate"):
            classification.command_commit_assign(argparse.Namespace(
                plan=str(indeterminate_path), approval="APPROVED"
            ))
        lock = json.loads(classification.lock_path(
            json.loads(indeterminate_path.read_text())["sha256"]
        ).read_text())
        self.assertEqual(lock["status"], "indeterminate")
        self.assertEqual(lock["completed_item_ids"], [])
        self.assertEqual(lock["write_in_flight_item_id"], "43")

    def test_list_field_is_read_only_and_projects_only_metadata(self) -> None:
        raw = {
            **self.target,
            "secret": "must not be projected",
            "values": [
                {**row, "internal": "omit"} for row in self.target["values"]
            ],
        }
        with mock.patch.object(
            classification,
            "ui_list_fields",
            return_value=self.fields_response(raw),
        ), mock.patch.object(
            classification, "ui_create_fixed_field"
        ) as field_write, mock.patch.object(
            classification, "oauth_item_write_allowed"
        ) as item_write, contextlib.redirect_stdout(io.StringIO()) as stdout:
            classification.command_list_field(argparse.Namespace())
        field_write.assert_not_called()
        item_write.assert_not_called()
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "READ_ONLY")
        self.assertEqual(result["method"], "GET")
        self.assertEqual(result["endpoint"], classification.FIELD_PATH)
        self.assertEqual(result["target_fields"], [self.target])
        self.assertFalse(result["credentials_exposed"])
        self.assertNotIn("secret", json.dumps(result))

    def test_get_item_uses_only_exact_oauth_get_path(self) -> None:
        captured = []

        def fake_get(token, domain, path):
            captured.append((token, domain, path))
            return {"code": 0, "item": self.item("77")}

        with mock.patch.object(classification.zoho_tool, "api_get", side_effect=fake_get):
            item = classification.get_item("token", self.vault, "77")
        self.assertEqual(item["item_id"], "77")
        parsed = urlsplit(captured[0][2])
        self.assertEqual(parsed.path, "/inventory/v1/items/77")
        self.assertEqual(parse_qs(parsed.query), {"organization_id": ["99"]})

    def test_command_parser_exposes_only_commissioned_commands_and_required_flags(self) -> None:
        parser = classification.build_parser()
        cases = [
            (["list-field"], classification.command_list_field),
            (["stage-create", "--input", "x.json"], classification.command_stage_create),
            (["commit-create", "--plan", "x.json", "--approval", "APPROVED"],
             classification.command_commit_create),
            (["stage-assign", "--input", "x.json"], classification.command_stage_assign),
            (["commit-assign", "--plan", "x.json", "--approval", "APPROVED"],
             classification.command_commit_assign),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertIs(parser.parse_args(argv).func, expected)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["delete-field"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["commit-assign", "--plan", "x.json"])


class ClassificationToolSyntheticError(RuntimeError):
    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
