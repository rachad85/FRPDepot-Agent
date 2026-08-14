"""Safety tests for the TDS percentage-discount correction.

Commissioned by Rachad on 2026-08-10 after Zoho read the numeric line discount
10 as a flat CAD 10.00 instead of 10%.

NO TEST IN THIS FILE PERFORMS A LIVE CALL. Every read is a fake api_get and
every write is a fake urlopen; the real transports are asserted never to run.
"""

from __future__ import annotations

import argparse
import copy
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import zoho_tool as tool
import zoho_customer_quote_tool as draft

QT29 = "96274000001559037"
QT30 = "96274000001558043"
ORG_ID = "96274000000000001"
FIRST_LINE_ID = 7000000


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def source_plan_payload(estimate_id: str) -> dict:
    target = draft.CORRECTION_TARGETS[estimate_id]
    plan = json.loads(Path(target["source_plan"]).read_text(encoding="utf-8"))
    return plan["payload"]


def live_estimate(estimate_id: str) -> dict:
    """A faithful stand-in for the live draft, built from the real artifacts."""
    target = draft.CORRECTION_TARGETS[estimate_id]
    payload = source_plan_payload(estimate_id)
    lines = []
    for index, source in enumerate(payload["line_items"]):
        gross = Decimal(str(source["quantity"])) * Decimal(str(source["rate"]))
        line = {
            "line_item_id": str(FIRST_LINE_ID + index),
            "item_id": source["item_id"],
            "name": source["name"],
            "description": source.get("description", ""),
            "quantity": source["quantity"],
            "rate": source["rate"],
            "unit": source["unit"],
            "tax_id": source["tax_id"],
            "tax_name": "Gst & Qst",
            "item_order": index,
            "line_item_taxes": [
                {
                    "tax_id": "96274000000035512",
                    "tax_name": "GST (5%)",
                    "tax_percentage": 5.0,
                    "tax_amount": 1.0,
                },
                {
                    "tax_id": "96274000001071131",
                    "tax_name": "QST (9.975%)",
                    "tax_percentage": 9.975,
                    "tax_amount": 2.0,
                },
            ],
            # The defect, exactly as the live diagnosis found it.
            "discount": 10.0,
            "discount_amount": 10.0,
            "item_total": float(gross - Decimal("10")),
        }
        lines.append(line)
    combined, gst, qst = draft.tax_on(target["current_sub_total"])
    assert combined == target["current_tax_total"]
    return {
        "estimate_id": estimate_id,
        "estimate_number": target["estimate_number"],
        "reference_number": target["reference_number"],
        "customer_id": target["customer_id"],
        "customer_name": "Troy Dualam Services Inc.",
        "status": "draft",
        "date": payload["date"],
        "expiry_date": "",
        "notes": payload["notes"],
        "terms": "",
        "currency_id": "96274000000000097",
        "currency_code": "CAD",
        "template_id": "96274000000000123",
        "salesperson_id": "",
        "discount_type": "item_level",
        "is_discount_before_tax": True,
        "discount_total": float(Decimal("10") * len(lines)),
        "discount_percent": 1.0,
        "sub_total": float(target["current_sub_total"]),
        "tax_total": float(target["current_tax_total"]),
        "total": float(target["current_total"]),
        "uninvoiced_amount": float(target["current_total"]),
        "taxes": [
            {"tax_name": "GST", "tax_amount": float(gst)},
            {"tax_name": "QST", "tax_amount": float(qst)},
        ],
        "estimate_url": "https://example.invalid/staged-secure-estimate-url",
        "line_items": lines,
        "last_modified_time": "2026-08-10T19:37:51-0400",
    }


def expected_for(estimate_id: str, before: dict) -> dict:
    target = draft.CORRECTION_TARGETS[estimate_id]
    sources = draft.collect_source_evidence(target, estimate_id)
    return draft.expected_correction(
        before["line_items"], sources["totals_artifact"]["record"], target
    )


def corrected_estimate(estimate_id: str, before: dict | None = None) -> dict:
    """What Zoho should return once the percentage lands."""
    before = before or live_estimate(estimate_id)
    target = draft.CORRECTION_TARGETS[estimate_id]
    expected = expected_for(estimate_id, before)
    after = copy.deepcopy(before)
    for line, want in zip(after["line_items"], expected["lines"]):
        line["discount"] = 10.0
        line["discount_amount"] = float(Decimal(want["discount_amount"]))
        line["item_total"] = float(Decimal(want["item_total"]))
        line["line_item_taxes"][0]["tax_amount"] = 3.0
        line["line_item_taxes"][1]["tax_amount"] = 4.0
    after["discount_total"] = float(Decimal(expected["discount_total"]))
    after["discount_percent"] = 10.0
    after["sub_total"] = float(Decimal(expected["sub_total"]))
    after["tax_total"] = float(Decimal(expected["tax_total"]))
    after["total"] = float(Decimal(expected["total"]))
    after["uninvoiced_amount"] = float(Decimal(expected["total"]))
    after["taxes"] = [
        {"tax_name": "GST", "tax_amount": float(Decimal(expected["tax_gst"]))},
        {"tax_name": "QST", "tax_amount": float(Decimal(expected["tax_qst"]))},
    ]
    after["estimate_url"] = "https://example.invalid/fresh-secure-estimate-url"
    after["last_modified_time"] = "2026-08-11T09:15:02-0400"
    assert Decimal(str(after["total"])) == target["corrected_total"]
    return after


def fake_vault(scopes=None) -> dict:
    return {
        "api_domain": tool.EXPECTED_API_DOMAIN,
        "books_organization_id": ORG_ID,
        "scopes": list(tool.SCOPES) if scopes is None else list(scopes),
    }


def rehash(plan: dict) -> dict:
    core = dict(plan)
    core.pop("sha256", None)
    plan["sha256"] = draft.digest_for(core)
    return plan


class CorrectionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.plan_dir = Path(self._temp.name).resolve() / "zoho_plans"
        self.plan_dir.mkdir(parents=True)
        self.addCleanup(self._temp.cleanup)

    def stage(self, estimate_id: str, before: dict | None = None) -> Path:
        before = before or live_estimate(estimate_id)
        vault = fake_vault()
        existing = set(self.plan_dir.glob(f"*_{draft.CORRECTION_KIND}_*.json"))
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool, "api_get", return_value={"code": 0, "estimate": before}
        ), patch.object(
            draft, "urlopen", side_effect=AssertionError("staging must never write")
        ):
            draft.command_stage_tds_discount_correction(
                argparse.Namespace(estimate_id=estimate_id)
            )
        created = set(self.plan_dir.glob(f"*_{draft.CORRECTION_KIND}_*.json")) - existing
        self.assertEqual(len(created), 1)
        return created.pop()

    def plan_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def write_plan(self, path: Path, plan: dict) -> None:
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def commit(
        self,
        plan_path: Path,
        *,
        approval: str = draft.APPROVAL_WORD,
        reads: list | None = None,
        put_result=None,
        put_error: Exception | None = None,
        scopes=None,
        estimate_id: str = QT29,
    ) -> dict:
        """Run a commit with every network boundary faked. Returns the calls."""
        before = live_estimate(estimate_id)
        after = corrected_estimate(estimate_id, before)
        if reads is None:
            reads = [
                {"code": 0, "estimate": before},
                {"code": 0, "estimate": after},
            ]
        vault = fake_vault(scopes)
        calls: dict = {"puts": [], "gets": 0}
        # Kept on the test case so a raising commit can still be inspected.
        self.last_calls = calls

        def fake_api_get(access_token, api_domain, path):
            calls["gets"] += 1
            if not reads:
                raise AssertionError("unexpected extra GET")
            return reads.pop(0)

        def fake_urlopen(request, timeout):
            calls["puts"].append({
                "method": request.get_method(),
                "url": request.full_url,
                "payload": json.loads(request.data.decode("utf-8")),
                "lock_exists": draft.correction_lock_path(
                    self.plan_json(plan_path)["sha256"]
                ).exists(),
            })
            if put_error is not None:
                raise put_error
            return FakeResponse(
                put_result if put_result is not None else {"code": 0, "estimate": after}
            )

        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool, "api_get", side_effect=fake_api_get
        ), patch.object(draft, "urlopen", side_effect=fake_urlopen):
            draft.command_commit_tds_discount_correction(
                argparse.Namespace(plan=str(plan_path), approval=approval)
            )
        return calls

    def commit_expecting_error(self, plan_path: Path, **kwargs):
        with self.assertRaises(draft.DraftToolError) as caught:
            self.commit(plan_path, **kwargs)
        return caught.exception

    def lock_record(self, plan_path: Path) -> dict:
        with patch.object(draft, "PLAN_DIR", self.plan_dir):
            lock = draft.correction_lock_path(self.plan_json(plan_path)["sha256"])
        return json.loads(lock.read_text(encoding="utf-8")) if lock.exists() else {}


class FixedTargetTests(CorrectionTestCase):
    def test_exactly_two_estimates_are_reachable(self) -> None:
        self.assertEqual(set(draft.CORRECTION_TARGETS), {QT29, QT30})
        for estimate_id, expected_number in ((QT29, "QT-000029"), (QT30, "QT-000030")):
            key, target = draft.require_correction_target(estimate_id)
            self.assertEqual(key, estimate_id)
            self.assertEqual(target["estimate_number"], expected_number)
            self.assertEqual(target["customer_id"], draft.TDS_CUSTOMER_ID)
            self.assertEqual(target["status"], "draft")

    def test_every_other_estimate_id_is_refused_before_any_network(self) -> None:
        for other in (
            "96274000001559038", "96274000001558044", "1", "", None, "  ",
            QT29 + "0", "abc", "96274000001559037 ; DROP",
        ):
            with self.assertRaisesRegex(draft.DraftToolError, "REFUSED"):
                draft.require_correction_target(other)

    def test_stage_refuses_a_foreign_id_before_vault_token_or_network(self) -> None:
        with patch.object(
            draft.zoho_tool, "load_vault", side_effect=AssertionError("vault must not open")
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", side_effect=AssertionError("no token")
        ), patch.object(
            draft.zoho_tool, "api_get", side_effect=AssertionError("no GET")
        ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
            with self.assertRaisesRegex(draft.DraftToolError, "REFUSED"):
                draft.command_stage_tds_discount_correction(
                    argparse.Namespace(estimate_id="96274000009999999")
                )

    def test_fixed_targets_match_the_approved_figures(self) -> None:
        qt29 = draft.CORRECTION_TARGETS[QT29]
        qt30 = draft.CORRECTION_TARGETS[QT30]
        self.assertEqual(qt29["current_total"], Decimal("15073.96"))
        self.assertEqual(qt29["corrected_total"], Decimal("13680.38"))
        self.assertEqual(qt29["line_count"], 11)
        self.assertEqual(qt30["current_total"], Decimal("6507.31"))
        self.assertEqual(qt30["corrected_total"], Decimal("5929.02"))
        self.assertEqual(qt30["line_count"], 7)


class StageTests(CorrectionTestCase):
    def test_stage_is_get_only_and_writes_one_plan(self) -> None:
        before = live_estimate(QT29)
        vault = fake_vault()
        receipts = []
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt", side_effect=lambda a, e: receipts.append((a, e))
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool, "api_get", return_value={"code": 0, "estimate": before}
        ) as fake_get, patch.object(
            draft, "urlopen", side_effect=AssertionError("staging must never write")
        ):
            draft.command_stage_tds_discount_correction(argparse.Namespace(estimate_id=QT29))
        self.assertEqual(fake_get.call_count, 1)
        self.assertIn("/books/v3/estimates/" + QT29, fake_get.call_args.args[2])
        self.assertEqual(len(list(self.plan_dir.glob("*.json"))), 1)
        self.assertEqual(len(receipts), 1)
        self.assertIn("writes=0", receipts[0][1])
        self.assertIn("GET_ONLY", receipts[0][1])

    def test_staged_plan_changes_only_the_discount(self) -> None:
        for estimate_id in (QT29, QT30):
            with self.subTest(estimate_id=estimate_id):
                before = live_estimate(estimate_id)
                plan = self.plan_json(self.stage(estimate_id, before))
                payload = plan["live_evidence"]["put_payload"]
                self.assertEqual(payload["customer_id"], draft.TDS_CUSTOMER_ID)
                self.assertEqual(payload["estimate_number"], before["estimate_number"])
                self.assertEqual(payload["reference_number"], before["reference_number"])
                self.assertEqual(payload["date"], before["date"])
                self.assertEqual(payload["notes"], before["notes"])
                self.assertEqual(payload["discount_type"], "item_level")
                self.assertIs(payload["is_discount_before_tax"], True)
                self.assertEqual(
                    len(payload["line_items"]),
                    draft.CORRECTION_TARGETS[estimate_id]["line_count"],
                )
                for index, (line, live) in enumerate(
                    zip(payload["line_items"], before["line_items"])
                ):
                    self.assertEqual(line["discount"], "10%")
                    self.assertEqual(line["line_item_id"], live["line_item_id"])
                    self.assertEqual(line["item_id"], live["item_id"])
                    self.assertEqual(line["name"], live["name"])
                    self.assertEqual(line["quantity"], live["quantity"])
                    self.assertEqual(line["rate"], live["rate"])
                    self.assertEqual(line["unit"], live["unit"])
                    self.assertEqual(line["tax_id"], live["tax_id"])
                    self.assertEqual(line["item_order"], live["item_order"])
                    self.assertEqual(
                        line.get("description", ""), live.get("description", "")
                    )
                    self.assertNotIn("discount_amount", line)
                    self.assertNotIn("item_total", line)
                    self.assertEqual(index, live["item_order"])

    def test_payload_carries_every_line_id_in_live_order(self) -> None:
        before = live_estimate(QT29)
        plan = self.plan_json(self.stage(QT29, before))
        self.assertEqual(
            [line["line_item_id"] for line in plan["live_evidence"]["put_payload"]["line_items"]],
            [line["line_item_id"] for line in before["line_items"]],
        )

    def test_no_forbidden_payload_key_is_reachable(self) -> None:
        plan = self.plan_json(self.stage(QT29))
        payload = plan["live_evidence"]["put_payload"]
        self.assertTrue(set(payload).issubset(draft.CORRECTION_ALLOWED_PUT_KEYS))
        for forbidden in (
            "status", "currency_id", "currency_code", "exchange_rate", "adjustment",
            "shipping_charge", "salesperson_id", "template_id", "custom_fields",
            "contact_persons", "billing_address_id", "shipping_address_id", "email",
            "send", "to_mail_ids", "estimate_id", "tax_id", "discount",
            "ignore_auto_number_generation",
        ):
            self.assertNotIn(forbidden, payload)
        for line in payload["line_items"]:
            self.assertTrue(set(line).issubset(set(draft.CORRECTION_LINE_PUT_KEYS)))

    def test_plan_records_endpoint_expiry_fingerprint_and_sources(self) -> None:
        path = self.stage(QT29)
        plan = self.plan_json(path)
        self.assertEqual(plan["tool"], draft.TOOL_NAME)
        self.assertEqual(plan["kind"], draft.CORRECTION_KIND)
        self.assertEqual(plan["schema_version"], draft.CORRECTION_SCHEMA_VERSION)
        self.assertEqual(plan["approval_required"], "APPROVED")
        self.assertEqual(plan["estimate_id"], QT29)
        self.assertEqual(
            plan["live_evidence"]["put_endpoint"], f"PUT /books/v3/estimates/{QT29}"
        )
        self.assertIs(plan["risk"]["single_put"], True)
        self.assertIs(plan["risk"]["email_sent"], False)
        self.assertIs(plan["live_evidence"]["email_sent"], False)
        created = draft.parse_plan_time(plan["created_utc"], "created")
        expires = draft.parse_plan_time(plan["expires_utc"], "expires")
        self.assertEqual((expires - created).total_seconds(), 24 * 3600)
        estimate = plan["live_evidence"]["estimate"]
        self.assertEqual(
            estimate["before_state_sha256"], draft.digest_for(estimate["before_state"])
        )
        self.assertEqual(
            estimate["protected_state_sha256"], draft.digest_for(estimate["protected_state"])
        )
        self.assertEqual(
            plan["source_evidence"]["source_plan"]["sha256"],
            draft.CORRECTION_TARGETS[QT29]["source_plan_sha256"],
        )
        self.assertIn("tds_draft_quote_totals.json", plan["source_evidence"]["totals_artifact"]["path"])
        self.assertIn(
            "tds_draft_estimates_discount_diagnosis_original_20260810.json",
            plan["source_evidence"]["diagnosis_artifact"]["path"],
        )
        self.assertEqual(plan["sha256"], draft.digest_for({k: v for k, v in plan.items() if k != "sha256"}))

    def test_stage_prints_the_approval_requirement_and_never_supplies_it(self) -> None:
        path = self.stage(QT29)
        plan = self.plan_json(path)
        printed = []
        with patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a))):
            draft.print_correction_summary(plan, path)
        text = "\n".join(printed)
        self.assertIn("NO WRITE HAS BEEN MADE", text)
        self.assertIn("APPROVED", text)
        self.assertIn("QT-000029", text)
        self.assertIn("13680.38", text)
        self.assertIn("15073.96", text)

    def test_exact_corrected_totals_for_both_estimates(self) -> None:
        cases = (
            (QT29, "11898.57", "1781.81", "13680.38", "1322.07"),
            (QT30, "5156.79", "772.23", "5929.02", "572.97"),
        )
        for estimate_id, sub_total, tax_total, total, discount_total in cases:
            with self.subTest(estimate_id=estimate_id):
                expected = self.plan_json(self.stage(estimate_id))["live_evidence"]["expected"]
                self.assertEqual(expected["sub_total"], sub_total)
                self.assertEqual(expected["tax_total"], tax_total)
                self.assertEqual(expected["total"], total)
                self.assertEqual(expected["discount_total"], discount_total)
                self.assertEqual(
                    Decimal(expected["tax_gst"]) + Decimal(expected["tax_qst"]),
                    Decimal(tax_total),
                )

    def test_expected_line_discount_amounts_match_the_approved_artifact(self) -> None:
        expected = self.plan_json(self.stage(QT29))["live_evidence"]["expected"]
        first = expected["lines"][0]
        self.assertEqual(first["line_gross"], "100.80")
        self.assertEqual(first["discount_amount"], "10.08")
        self.assertEqual(first["item_total"], "90.72")
        self.assertEqual(expected["lines"][-1]["discount_amount"], "203.69")
        self.assertEqual(expected["lines"][-1]["item_total"], "1833.19")
        total = sum(Decimal(line["item_total"]) for line in expected["lines"])
        self.assertEqual(total, Decimal(expected["sub_total"]))

    def test_ten_percent_is_never_the_flat_ten_dollars_again(self) -> None:
        expected = self.plan_json(self.stage(QT29))["live_evidence"]["expected"]
        for line in expected["lines"]:
            self.assertNotEqual(Decimal(line["discount_amount"]), Decimal("10.00"))
            self.assertEqual(
                Decimal(line["discount_amount"]),
                (Decimal(line["line_gross"]) / Decimal("10")).quantize(draft.CENT),
            )


class StageRefusalTests(CorrectionTestCase):
    def assert_stage_refused(self, estimate_id: str, mutate) -> None:
        before = live_estimate(estimate_id)
        mutate(before)
        with self.assertRaises(draft.DraftToolError):
            self.stage(estimate_id, before)

    def test_wrong_identity_or_state_is_refused(self) -> None:
        def drop_line(estimate):
            estimate["line_items"].pop()

        def add_line(estimate):
            estimate["line_items"].append(copy.deepcopy(estimate["line_items"][0]))

        def reorder(estimate):
            estimate["line_items"][0], estimate["line_items"][1] = (
                estimate["line_items"][1], estimate["line_items"][0],
            )

        mutations = {
            "number": lambda e: e.update({"estimate_number": "QT-000031"}),
            "reference": lambda e: e.update({"reference_number": "PO 999999"}),
            "customer": lambda e: e.update({"customer_id": "96274000000060020"}),
            "status_sent": lambda e: e.update({"status": "sent"}),
            "status_accepted": lambda e: e.update({"status": "accepted"}),
            "sub_total": lambda e: e.update({"sub_total": 13110.65}),
            "tax_total": lambda e: e.update({"tax_total": 1963.31}),
            "total": lambda e: e.update({"total": 15073.97}),
            "discount_type": lambda e: e.update({"discount_type": "entity_level"}),
            "before_tax_flag": lambda e: e.update({"is_discount_before_tax": False}),
            "missing_line": drop_line,
            "extra_line": add_line,
            "reordered_lines": reorder,
            "line_id": lambda e: e["line_items"][0].update({"line_item_id": ""}),
            "item_id": lambda e: e["line_items"][0].update({"item_id": "96274000000019584"}),
            "name": lambda e: e["line_items"][0].update({"name": "FRP STUB FLANGE-2\"/150PSI/D411"}),
            "description": lambda e: e["line_items"][-1].update({"description": "changed"}),
            "quantity": lambda e: e["line_items"][0].update({"quantity": 3.0}),
            "rate": lambda e: e["line_items"][0].update({"rate": 50.5}),
            "unit": lambda e: e["line_items"][0].update({"unit": "box"}),
            "tax": lambda e: e["line_items"][0].update({"tax_id": ""}),
            "already_percent": lambda e: e["line_items"][0].update(
                {"discount": "10%", "discount_amount": 10.08}
            ),
            "other_discount": lambda e: e["line_items"][0].update({"discount": 5.0}),
            "discount_amount": lambda e: e["line_items"][0].update({"discount_amount": 10.08}),
        }
        for label, mutate in mutations.items():
            with self.subTest(mutation=label):
                self.assert_stage_refused(QT29, mutate)

    def test_reordered_lines_are_refused_on_the_second_estimate_too(self) -> None:
        def reorder(estimate):
            estimate["line_items"].reverse()

        self.assert_stage_refused(QT30, reorder)

    def test_live_tax_that_does_not_add_up_is_refused(self) -> None:
        def break_taxes(estimate):
            estimate["taxes"] = [{"tax_name": "GST", "tax_amount": 1.0}]

        self.assert_stage_refused(QT29, break_taxes)

    def test_a_wrong_estimate_record_from_zoho_is_refused(self) -> None:
        before = live_estimate(QT30)
        vault = fake_vault()
        with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft.zoho_tool, "load_vault", return_value=dict(vault)
        ), patch.object(
            draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
        ), patch.object(draft.zoho_tool, "save_vault"), patch.object(
            draft.zoho_tool, "api_get", return_value={"code": 0, "estimate": before}
        ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
            with self.assertRaises(draft.DraftToolError):
                draft.command_stage_tds_discount_correction(
                    argparse.Namespace(estimate_id=QT29)
                )
        self.assertEqual(list(self.plan_dir.glob("*.json")), [])


class TransportTests(CorrectionTestCase):
    def test_only_put_and_only_the_two_fixed_endpoints(self) -> None:
        payload = self.plan_json(self.stage(QT29))["live_evidence"]["put_payload"]
        with patch.object(draft, "urlopen", side_effect=AssertionError("must not reach network")):
            for method in ("POST", "DELETE", "PATCH", "GET", "put"):
                with self.assertRaisesRegex(draft.DraftToolError, "REFUSED"):
                    draft.oauth_estimate_discount_write_allowed(
                        "token", tool.EXPECTED_API_DOMAIN, method,
                        f"/books/v3/estimates/{QT29}", ORG_ID, payload,
                    )
            for path in (
                "/books/v3/estimates",
                "/books/v3/estimates/96274000009999999",
                f"/books/v3/estimates/{QT29}/status/sent",
                f"/books/v3/estimates/{QT29}/email",
                f"/books/v3/estimates/{QT29}/approve",
                f"/books/v3/estimates/{QT29}/submit",
                f"/books/v3/estimates/{QT29}/converttosalesorder",
                f"/books/v3/estimates/{QT29}/attachment",
                "/books/v3/invoices/96274000001559012",
                f"/books/v3/estimates/{QT29}?send=true",
            ):
                with self.assertRaisesRegex(draft.DraftToolError, "REFUSED"):
                    draft.oauth_estimate_discount_write_allowed(
                        "token", tool.EXPECTED_API_DOMAIN, "PUT", path, ORG_ID, payload,
                    )

    def test_transport_refuses_a_damaged_payload_before_the_network(self) -> None:
        good = self.plan_json(self.stage(QT29))["live_evidence"]["put_payload"]

        def broken(mutate):
            payload = copy.deepcopy(good)
            mutate(payload)
            return payload

        cases = [
            lambda p: p["line_items"].pop(),
            lambda p: p["line_items"].append(copy.deepcopy(p["line_items"][0])),
            lambda p: p["line_items"][1].update({"line_item_id": p["line_items"][0]["line_item_id"]}),
            lambda p: p["line_items"][0].pop("line_item_id"),
            lambda p: p["line_items"][0].update({"discount": 10.0}),
            lambda p: p["line_items"][0].update({"discount": "10"}),
            lambda p: p["line_items"][0].update({"item_total": 90.72}),
            lambda p: p.update({"status": "sent"}),
            lambda p: p.update({"discount_type": "entity_level"}),
            lambda p: p.update({"is_discount_before_tax": False}),
            lambda p: p.update({"customer_id": "96274000000060020"}),
            lambda p: p.update({"estimate_number": "QT-000031"}),
            lambda p: p.pop("line_items"),
            lambda p: p.pop("customer_id"),
        ]
        with patch.object(draft, "urlopen", side_effect=AssertionError("must not reach network")):
            for index, mutate in enumerate(cases):
                with self.subTest(case=index):
                    with self.assertRaisesRegex(draft.DraftToolError, "REFUSED"):
                        draft.oauth_estimate_discount_write_allowed(
                            "token", tool.EXPECTED_API_DOMAIN, "PUT",
                            f"/books/v3/estimates/{QT29}", ORG_ID, broken(mutate),
                        )

    def test_the_put_request_is_exactly_one_call_to_the_fixed_url(self) -> None:
        payload = self.plan_json(self.stage(QT29))["live_evidence"]["put_payload"]
        captured = []

        def fake_urlopen(request, timeout):
            captured.append((request.get_method(), request.full_url, request.headers))
            return FakeResponse({"code": 0, "estimate": {}})

        with patch.object(draft, "urlopen", side_effect=fake_urlopen):
            draft.oauth_estimate_discount_write_allowed(
                "token", tool.EXPECTED_API_DOMAIN, "PUT",
                f"/books/v3/estimates/{QT29}", ORG_ID, payload,
            )
        self.assertEqual(len(captured), 1)
        method, url, headers = captured[0]
        self.assertEqual(method, "PUT")
        self.assertEqual(
            url, f"{tool.EXPECTED_API_DOMAIN}/books/v3/estimates/{QT29}?organization_id={ORG_ID}"
        )
        self.assertNotIn("send", url)
        self.assertNotIn("email", url)
        self.assertEqual(headers.get("Authorization"), "Zoho-oauthtoken token")

    def test_the_source_has_no_delete_patch_or_mail_route(self) -> None:
        source = Path(draft.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count('method="PUT"'), 1)
        # Two POSTs: the original customer/estimate creation path, and the fixed
        # SHM customer commissioned 2026-08-12.
        self.assertEqual(source.count('method="POST"'), 2)
        self.assertEqual(source.count("urlopen(request"), 3)
        for forbidden in (
            'method="DELETE"', 'method="PATCH"', 'method="GET"',
            "/books/v3/estimates/email", "/status/", "markassent", "markassent", "approve\"",
            "to_mail_ids", "send=true", "smtplib", "Mail.Send",
        ):
            self.assertNotIn(forbidden, source)


class CommitTests(CorrectionTestCase):
    def test_commit_puts_once_and_verifies_response_and_fresh_read(self) -> None:
        path = self.stage(QT29)
        calls = self.commit(path)
        self.assertEqual(len(calls["puts"]), 1)
        self.assertEqual(calls["puts"][0]["method"], "PUT")
        self.assertIn(f"/books/v3/estimates/{QT29}?organization_id={ORG_ID}", calls["puts"][0]["url"])
        # One GET before the PUT and one fresh GET after it.
        self.assertEqual(calls["gets"], 2)
        self.assertTrue(calls["puts"][0]["lock_exists"])
        self.assertEqual(self.lock_record(path)["status"], "committed_verified")

    def test_commit_works_for_the_second_estimate(self) -> None:
        path = self.stage(QT30)
        calls = self.commit(path, estimate_id=QT30)
        self.assertEqual(len(calls["puts"]), 1)
        self.assertEqual(
            calls["puts"][0]["payload"]["estimate_number"], "QT-000030"
        )
        self.assertEqual(self.lock_record(path)["status"], "committed_verified")

    def test_approval_must_be_exact_and_is_checked_before_anything_else(self) -> None:
        path = self.stage(QT29)
        digest = self.plan_json(path)["sha256"]
        for approval in ("approved", "Approved", " APPROVED", "APPROVED ", "APPROVED\n",
                         "", "YES", digest, f"APPROVED {digest}", None, True):
            with self.subTest(approval=approval):
                with patch.object(draft, "PLAN_DIR", self.plan_dir), patch.object(
                    draft.zoho_tool, "load_vault", side_effect=AssertionError("vault must not open")
                ), patch.object(
                    draft.zoho_tool, "api_get", side_effect=AssertionError("no GET")
                ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
                    with self.assertRaisesRegex(draft.DraftToolError, "APPROVED"):
                        draft.command_commit_tds_discount_correction(
                            argparse.Namespace(plan=str(path), approval=approval)
                        )
        # A refused approval never creates the single-use lock either.
        with patch.object(draft, "PLAN_DIR", self.plan_dir):
            self.assertFalse(draft.correction_lock_path(digest).exists())

    def test_missing_update_scope_is_refused_before_the_put(self) -> None:
        path = self.stage(QT29)
        scopes = [scope for scope in tool.SCOPES if scope != draft.ESTIMATE_UPDATE_SCOPE]
        error = self.commit_expecting_error(path, scopes=scopes)
        self.assertIn(draft.ESTIMATE_UPDATE_SCOPE, str(error))
        self.assertIn("REAUTHORIZE_DADO_ZOHO.bat", str(error))
        record = self.lock_record(path)
        self.assertEqual(record["status"], "aborted_before_write")
        self.assertFalse(record["write_attempted"])

    def test_plan_hash_mismatch_is_refused(self) -> None:
        path = self.stage(QT29)
        plan = self.plan_json(path)
        plan["live_evidence"]["expected"]["total"] = "1.00"
        self.write_plan(path, plan)
        error = self.commit_expecting_error(path)
        self.assertIn("hash check failed", str(error))

    def test_tampered_but_rehashed_plan_cannot_move_a_number(self) -> None:
        path = self.stage(QT29)
        original = self.plan_json(path)
        for mutate in (
            lambda plan: plan["live_evidence"]["expected"].update({"total": "1.00"}),
            lambda plan: plan["live_evidence"]["put_payload"]["line_items"][0].update(
                {"discount": 10.0}
            ),
            lambda plan: plan["live_evidence"].update(
                {"put_endpoint": f"PUT /books/v3/estimates/{QT30}"}
            ),
            lambda plan: plan["live_evidence"]["estimate"].update({"line_count": 10}),
            lambda plan: plan["live_evidence"]["estimate"].update(
                {"protected_state_sha256": "0" * 64}
            ),
        ):
            with self.subTest(mutate=mutate):
                plan = copy.deepcopy(original)
                mutate(plan)
                self.write_plan(path, rehash(plan))
                self.commit_expecting_error(path)
                self.write_plan(path, copy.deepcopy(original))

    def test_expired_plan_is_refused(self) -> None:
        path = self.stage(QT29)
        plan = self.plan_json(path)
        created = draft.parse_plan_time(plan["created_utc"], "created")
        plan["created_utc"] = (created - draft.timedelta(hours=48)).isoformat()
        plan["expires_utc"] = (created - draft.timedelta(hours=24)).isoformat()
        self.write_plan(path, rehash(plan))
        error = self.commit_expecting_error(path)
        self.assertIn("expired", str(error))

    def test_plan_from_another_tool_or_action_is_refused(self) -> None:
        path = self.stage(QT29)
        original = self.plan_json(path)
        for mutate in (
            lambda plan: plan.update({"tool": "Some Other Tool"}),
            lambda plan: plan.update({"kind": "quote"}),
            lambda plan: plan.update({"schema_version": 99}),
            lambda plan: plan.update({"approval_required": "OK"}),
            lambda plan: plan.update({"estimate_id": "96274000009999999"}),
            lambda plan: plan.update({"nonce": "short"}),
            lambda plan: plan["risk"].update({"single_put": False}),
        ):
            with self.subTest(mutate=mutate):
                plan = copy.deepcopy(original)
                mutate(plan)
                self.write_plan(path, rehash(plan))
                self.commit_expecting_error(path)
                self.write_plan(path, copy.deepcopy(original))

    def test_plan_outside_the_plan_folder_is_refused(self) -> None:
        outside = Path(self._temp.name).resolve() / "elsewhere.json"
        outside.write_text("{}", encoding="utf-8")
        with patch.object(draft, "PLAN_DIR", self.plan_dir):
            with self.assertRaises(draft.DraftToolError):
                draft.contained_correction_plan(str(outside))
            with self.assertRaises(draft.DraftToolError):
                draft.contained_correction_plan("relative.json")

    def test_pre_state_drift_refuses_before_the_put(self) -> None:
        path = self.stage(QT29)
        drifted = live_estimate(QT29)
        drifted["line_items"][0]["rate"] = 51.0
        error = self.commit_expecting_error(
            path, reads=[{"code": 0, "estimate": drifted}]
        )
        self.assertIn("changed after review", str(error))
        record = self.lock_record(path)
        self.assertEqual(record["status"], "aborted_before_write")
        self.assertFalse(record["write_attempted"])

    def test_only_estimate_url_may_regenerate_between_gets(self) -> None:
        self.assertEqual(draft.ESTIMATE_PREWRITE_VOLATILE_KEYS, frozenset({"estimate_url"}))
        path = self.stage(QT29)
        before = live_estimate(QT29)
        before["estimate_url"] = "https://example.invalid/regenerated-before-put"
        after = corrected_estimate(QT29, before)
        after["estimate_url"] = "https://example.invalid/regenerated-after-put"
        calls = self.commit(
            path,
            reads=[{"code": 0, "estimate": before}, {"code": 0, "estimate": after}],
        )
        self.assertEqual(len(calls["puts"]), 1)
        self.assertNotIn("estimate_url", draft.correction_protected_state(before))

    def test_status_drift_to_sent_refuses_before_the_put(self) -> None:
        path = self.stage(QT29)
        drifted = live_estimate(QT29)
        drifted["status"] = "sent"
        self.commit_expecting_error(path, reads=[{"code": 0, "estimate": drifted}])
        self.assertEqual(self.lock_record(path)["status"], "aborted_before_write")

    def test_replay_is_refused_after_a_committed_plan(self) -> None:
        path = self.stage(QT29)
        self.commit(path)
        error = self.commit_expecting_error(path)
        self.assertIn("already entered commit", str(error))

    def test_a_failed_put_leaves_the_plan_locked_with_no_retry(self) -> None:
        path = self.stage(QT29)
        error = self.commit_expecting_error(
            path, put_error=URLError("connection reset")
        )
        self.assertIn("indeterminate", str(error))
        self.assertIn("permanently locked", str(error))
        record = self.lock_record(path)
        self.assertEqual(record["status"], "indeterminate")
        self.assertTrue(record["write_attempted"])
        self.assertTrue(record["no_retry"])
        self.commit_expecting_error(path)

    def test_an_http_error_from_zoho_locks_the_plan(self) -> None:
        path = self.stage(QT29)
        failure = HTTPError(
            "https://www.zohoapis.ca", 400, "Bad Request", {},
            io.BytesIO(b'{"code":15,"message":"invalid"}'),
        )
        error = self.commit_expecting_error(path, put_error=failure)
        self.assertIn("permanently locked", str(error))
        self.assertEqual(self.lock_record(path)["status"], "indeterminate")

    def test_only_one_put_is_ever_attempted(self) -> None:
        path = self.stage(QT29)
        self.commit_expecting_error(path, put_error=URLError("boom"))
        # One attempt, then the lock. Nothing in this tool retries a write.
        self.assertEqual(len(self.last_calls["puts"]), 1)
        self.assertEqual(self.last_calls["gets"], 1)
        self.assertEqual(self.lock_record(path)["status"], "indeterminate")


class ReadBackTests(CorrectionTestCase):
    def wrong_after(self, mutate) -> dict:
        after = corrected_estimate(QT29)
        mutate(after)
        return after

    def test_a_wrong_fresh_read_back_stops_and_locks(self) -> None:
        before = live_estimate(QT29)
        mutations = {
            "flat_ten_again": lambda a: [
                line.update({"discount_amount": 10.0, "item_total": 90.8})
                for line in a["line_items"][:1]
            ],
            "wrong_total": lambda a: a.update({"total": 13680.39}),
            "wrong_sub_total": lambda a: a.update({"sub_total": 11898.58}),
            "wrong_tax": lambda a: a.update({"tax_total": 1781.80}),
            "status_moved": lambda a: a.update({"status": "sent"}),
            "number_moved": lambda a: a.update({"estimate_number": "QT-000031"}),
            "reference_moved": lambda a: a.update({"reference_number": "PO 0"}),
            "customer_moved": lambda a: a.update({"customer_id": "96274000000060020"}),
            "line_dropped": lambda a: a["line_items"].pop(),
            "line_id_moved": lambda a: a["line_items"][0].update({"line_item_id": "1"}),
            "rate_moved": lambda a: a["line_items"][0].update({"rate": 60.0}),
            "quantity_moved": lambda a: a["line_items"][0].update({"quantity": 5.0}),
            "name_moved": lambda a: a["line_items"][0].update({"name": "Something else"}),
            "tax_moved": lambda a: a["line_items"][0].update({"tax_id": "96274000001071140"}),
            "notes_moved": lambda a: a.update({"notes": "rewritten"}),
            "protected_moved": lambda a: a.update({"template_id": "96274000000000999"}),
            "discount_total_moved": lambda a: a.update({"discount_total": 1322.08}),
            "discount_percent_moved": lambda a: a.update({"discount_percent": 9.99}),
            "uninvoiced_amount_moved": lambda a: a.update({"uninvoiced_amount": 1.0}),
            "gst_amount_moved": lambda a: a["taxes"][0].update({"tax_amount": 594.92}),
            "qst_amount_moved": lambda a: a["taxes"][1].update({"tax_amount": 1186.87}),
            "tax_name_moved": lambda a: a["taxes"][0].update({"tax_name": "Other"}),
            "line_tax_id_moved": lambda a: a["line_items"][0]["line_item_taxes"][0].update(
                {"tax_id": "96274000000035513"}
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(mutation=label):
                fresh_path = self.stage(QT29)
                after = self.wrong_after(mutate)
                error = self.commit_expecting_error(
                    fresh_path,
                    reads=[{"code": 0, "estimate": before}, {"code": 0, "estimate": after}],
                    put_result={"code": 0, "estimate": corrected_estimate(QT29)},
                )
                self.assertIn("Fresh read-back", str(error))
                self.assertEqual(self.lock_record(fresh_path)["status"], "indeterminate")

    def test_a_wrong_put_response_stops_before_the_fresh_read(self) -> None:
        path = self.stage(QT29)
        wrong = self.wrong_after(lambda a: a.update({"total": 15073.96}))
        error = self.commit_expecting_error(
            path,
            reads=[{"code": 0, "estimate": live_estimate(QT29)}],
            put_result={"code": 0, "estimate": wrong},
        )
        self.assertIn("PUT response", str(error))
        self.assertEqual(self.lock_record(path)["status"], "indeterminate")

    def test_a_correct_read_back_passes_every_rule(self) -> None:
        path = self.stage(QT29)
        plan = self.plan_json(path)
        draft.verify_correction_result(
            corrected_estimate(QT29), plan["live_evidence"], "test"
        )

    def test_the_percentage_alone_is_not_accepted_without_the_amount(self) -> None:
        plan = self.plan_json(self.stage(QT29))
        after = corrected_estimate(QT29)
        # Zoho echoing "10%" while still charging a flat CAD 10.00 must fail.
        for line in after["line_items"]:
            line["discount"] = "10%"
            line["discount_amount"] = 10.0
        with self.assertRaises(draft.DraftToolError):
            draft.verify_correction_result(after, plan["live_evidence"], "test")

    def test_the_put_response_pass_is_lenient_and_the_fresh_read_pass_is_strict(self) -> None:
        plan = self.plan_json(self.stage(QT29))
        thin = corrected_estimate(QT29)
        # A response that carries only what Zoho always returns is accepted as a
        # response, but the authoritative fresh read must still be complete.
        thin.pop("template_id")
        for line in thin["line_items"]:
            line.pop("unit")
            line.pop("item_order")
        draft.verify_correction_result(thin, plan["live_evidence"], "PUT response", full=False)
        with self.assertRaises(draft.DraftToolError):
            draft.verify_correction_result(thin, plan["live_evidence"], "Fresh read-back")

    def test_a_wrong_amount_is_caught_even_in_the_response_pass(self) -> None:
        plan = self.plan_json(self.stage(QT29))
        after = corrected_estimate(QT29)
        after["line_items"][0]["discount_amount"] = 10.0
        with self.assertRaises(draft.DraftToolError):
            draft.verify_correction_result(
                after, plan["live_evidence"], "PUT response", full=False
            )

    def test_the_string_percent_form_is_accepted_when_the_amounts_are_right(self) -> None:
        plan = self.plan_json(self.stage(QT29))
        after = corrected_estimate(QT29)
        for line in after["line_items"]:
            line["discount"] = "10%"
        draft.verify_correction_result(after, plan["live_evidence"], "test")


class CreatePathRegressionTests(unittest.TestCase):
    @staticmethod
    def quote_evidence(payload: dict, summary: dict) -> dict:
        customer_id = str(payload["customer_id"])
        customer_name = (
            "Troy Dualam Services Inc." if customer_id == draft.TDS_CUSTOMER_ID
            else f"Customer {customer_id}"
        )
        items = {
            str(line["item_id"]): {
                "item_id": str(line["item_id"]),
                "name": str(line.get("name") or f"Item {line['item_id']}"),
                "sku": "TEST-SKU",
                "status": "active",
            }
            for line in payload["line_items"]
        }
        tax_ids = {
            str(line.get("tax_id") or "") for line in payload["line_items"]
            if line.get("tax_id")
        }
        taxes = {
            tax_id: {
                "tax_id": tax_id,
                "tax_name": "Gst & Qst" if tax_id == draft.TDS_GST_QST_TAX_ID else "Test tax",
                "tax_percentage": 14.975 if tax_id == draft.TDS_GST_QST_TAX_ID else 13,
                "tax_type": "tax",
                "status": "active",
            }
            for tax_id in tax_ids
        }
        currency_id = str(payload.get("currency_id") or "9988")
        customer = {
            "customer_id": customer_id,
            "customer_name": customer_name,
            "status": "active",
            "contact_type": "customer",
            "currency_code": "CAD",
            "currency_id": currency_id,
        }
        return {
            "customer": customer,
            "items": items,
            "taxes": taxes,
            "totals": draft.quote_totals(payload, summary, taxes),
        }

    def stage_quote(self, raw: dict, plan_dir: Path) -> dict:
        input_path = plan_dir.parent / "quote_input.json"
        input_path.write_text(json.dumps(raw), encoding="utf-8")
        with patch.object(draft, "PLAN_DIR", plan_dir), patch.object(
            draft.zoho_tool, "append_receipt"
        ), patch.object(
            draft, "stage_quote_live_evidence", side_effect=self.quote_evidence
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                draft.command_stage_quote(argparse.Namespace(input=str(input_path)))
            self.last_stage_output = output.getvalue()
        plans = sorted(plan_dir.glob("*_quote_*.json"))
        displayed_sha = json.loads(self.last_stage_output)["approval_card"]["plan_sha256"]
        matches = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in plans
            if json.loads(path.read_text(encoding="utf-8"))["sha256"] == displayed_sha
        ]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_new_quote_has_one_concise_hash_bound_24_hour_approval_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan_dir = Path(temp).resolve() / "plans"
            plan_dir.mkdir()
            plan = self.stage_quote(
                {
                    "customer_id": "1001",
                    "reference_number": "PO CARD",
                    "line_items": [{
                        "item_id": "2002", "name": "FRP PANEL", "quantity": 2, "rate": 125.5,
                        "quantity_source": "customer PO", "rate_source": "approved price list",
                    }],
                },
                plan_dir,
            )
        card = plan["approval_card"]
        self.assertEqual(json.loads(self.last_stage_output), {"approval_card": card})
        self.assertEqual(card["operation"], draft.QUOTE_CREATE_OPERATION)
        self.assertEqual(card["card_id"], f"QC-{plan['sha256'][:12].upper()}")
        self.assertEqual(card["scope"], {
            "method": "POST", "route": "/books/v3/estimates", "write_count": 1,
            "draft_only": True, "email_or_lifecycle_action": False,
        })
        self.assertEqual(card["customer"], {"id": "1001", "name": "Customer 1001"})
        self.assertEqual(card["document"]["reference_number"], "PO CARD")
        self.assertEqual(card["document"]["status"], "draft")
        self.assertEqual(card["currency"]["currency_id"], "9988")
        self.assertNotIn("currency_id", plan["payload"])
        self.assertNotIn("exchange_rate", plan["payload"])
        self.assertEqual(card["lines"][0]["item"], "FRP PANEL")
        self.assertEqual(card["totals"], {
            "subtotal": "251.00", "tax": "0.00", "total": "251.00",
            "tax_certainty": "deterministic_simple_tax",
        })
        self.assertTrue(card["risks"])
        self.assertEqual(card["expires_utc"], plan["expires_utc"])
        self.assertEqual(card["plan_sha256"], plan["sha256"])
        self.assertEqual(draft.approval_plan_digest(plan), plan["sha256"])
        self.assertIn("payload", plan)
        self.assertIn("sources", plan)
        self.assertIn("summary", plan)
        self.assertIn("live_evidence", plan)

    def test_resigned_payload_tamper_is_refused_before_vault_or_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan_dir = Path(temp).resolve() / "plans"
            plan_dir.mkdir()
            plan = self.stage_quote({
                "customer_id": "1001",
                "line_items": [{
                    "item_id": "2002", "quantity": 1, "rate": 10,
                    "quantity_source": "PO", "rate_source": "list",
                }],
            }, plan_dir)
            path = next(
                path for path in plan_dir.glob("*_quote_*.json")
                if json.loads(path.read_text(encoding="utf-8"))["sha256"] == plan["sha256"]
            )
            plan["payload"]["line_items"][0]["quantity"] = 500
            plan["sha256"] = draft.approval_plan_digest(plan)
            plan["approval_card"]["plan_sha256"] = plan["sha256"]
            plan["approval_card"]["card_id"] = draft.approval_card_id(
                draft.QUOTE_CREATE_OPERATION, plan["sha256"]
            )
            path.write_text(json.dumps(plan), encoding="utf-8")
            with patch.object(
                draft.zoho_tool, "load_vault", side_effect=AssertionError("vault must not open")
            ), patch.object(draft, "urlopen", side_effect=AssertionError("no network")):
                with self.assertRaisesRegex(draft.DraftToolError, "canonical projection"):
                    draft.command_commit(
                        argparse.Namespace(plan=str(path), approval="APPROVED"), "quote"
                    )

    def test_fresh_quote_drift_refuses_before_lock_and_post(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan_dir = Path(temp).resolve() / "plans"
            plan_dir.mkdir()
            plan = self.stage_quote({
                "customer_id": "1001",
                "line_items": [{
                    "item_id": "2002", "quantity": 1, "rate": 10,
                    "quantity_source": "PO", "rate_source": "list",
                }],
            }, plan_dir)
            path = next(
                path for path in plan_dir.glob("*_quote_*.json")
                if json.loads(path.read_text(encoding="utf-8"))["sha256"] == plan["sha256"]
            )
            drift = copy.deepcopy(plan["live_evidence"])
            drift["customer"]["currency_code"] = "USD"
            vault = {
                "api_domain": "https://www.zohoapis.com",
                "books_organization_id": "9009",
                "scopes": ["ZohoBooks.estimates.CREATE"],
            }
            with patch.object(draft, "PLAN_DIR", plan_dir), patch.object(
                draft.zoho_tool, "load_vault", return_value=dict(vault)
            ), patch.object(draft.zoho_tool, "validate_scopes"), patch.object(
                draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
            ), patch.object(
                draft, "quote_live_evidence", return_value=drift
            ), patch.object(
                draft, "api_post_allowed", side_effect=AssertionError("POST must not run")
            ), patch.object(draft, "urlopen", side_effect=AssertionError("no HTTP network")):
                with self.assertRaisesRegex(draft.DraftToolError, "changed after review"):
                    draft.command_commit(
                        argparse.Namespace(plan=str(path), approval="APPROVED"), "quote"
                    )
            lock = plan_dir / draft.QUOTE_COMMIT_LOCK_DIRNAME / f"{plan['sha256']}.json"
            self.assertFalse(lock.exists())

    def test_post_failure_locks_quote_indeterminate_and_no_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan_dir = Path(temp).resolve() / "plans"
            plan_dir.mkdir()
            plan = self.stage_quote({
                "customer_id": "1001",
                "line_items": [{
                    "item_id": "2002", "quantity": 1, "rate": 10,
                    "quantity_source": "PO", "rate_source": "list",
                }],
            }, plan_dir)
            path = next(
                path for path in plan_dir.glob("*_quote_*.json")
                if json.loads(path.read_text(encoding="utf-8"))["sha256"] == plan["sha256"]
            )
            calls = []

            def failed_post(*_args):
                lock = plan_dir / draft.QUOTE_COMMIT_LOCK_DIRNAME / f"{plan['sha256']}.json"
                calls.append(lock.exists())
                raise TimeoutError("mocked transport timeout")

            vault = {
                "api_domain": "https://www.zohoapis.com",
                "books_organization_id": "9009",
                "scopes": ["ZohoBooks.estimates.CREATE"],
            }
            with patch.object(draft, "PLAN_DIR", plan_dir), patch.object(
                draft.zoho_tool, "load_vault", return_value=dict(vault)
            ), patch.object(draft.zoho_tool, "validate_scopes"), patch.object(
                draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
            ), patch.object(
                draft, "quote_live_evidence", return_value=copy.deepcopy(plan["live_evidence"])
            ), patch.object(draft, "api_post_allowed", side_effect=failed_post), patch.object(
                draft, "urlopen", side_effect=AssertionError("no HTTP network")
            ):
                with self.assertRaisesRegex(TimeoutError, "mocked transport timeout"):
                    draft.command_commit(
                        argparse.Namespace(plan=str(path), approval="APPROVED"), "quote"
                    )
                with self.assertRaisesRegex(draft.DraftToolError, "already entered commit"):
                    draft.command_commit(
                        argparse.Namespace(plan=str(path), approval="APPROVED"), "quote"
                    )
            self.assertEqual(calls, [True])
            lock = plan_dir / draft.QUOTE_COMMIT_LOCK_DIRNAME / f"{plan['sha256']}.json"
            record = json.loads(lock.read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "indeterminate")
            self.assertTrue(record["no_retry"])
            self.assertTrue(record["write_attempted"])

    def test_quote_card_wrong_operation_hash_expiry_and_nonexact_approval_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            plan_dir = root / "plans"
            plan_dir.mkdir()
            plan = self.stage_quote({
                "customer_id": "1001",
                "line_items": [{
                    "item_id": "2002", "quantity": 1, "rate": 10,
                    "quantity_source": "PO", "rate_source": "list",
                }],
            }, plan_dir)
            plan_path = next(plan_dir.glob("*_quote_*.json"))
            for bad in ("approved", " APPROVED", "APPROVED ", "APPROVED\n", "APPROVED yes", ""):
                with self.subTest(approval=bad), patch.object(
                    draft.zoho_tool, "load_vault", side_effect=AssertionError("vault must not open")
                ), patch.object(draft, "urlopen", side_effect=AssertionError("no network")):
                    with self.assertRaisesRegex(draft.DraftToolError, "exact uppercase"):
                        draft.command_commit(
                            argparse.Namespace(plan=str(plan_path), approval=bad), "quote"
                        )
            tampered = copy.deepcopy(plan)
            tampered["approval_card"]["operation"] = draft.REVISION_OPERATION
            plan_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(draft.DraftToolError, "hash check failed"):
                draft.load_verified_plan(str(plan_path), "quote")
            expired = copy.deepcopy(plan)
            expired["created_utc"] = "2026-08-01T00:00:00+00:00"
            expired["expires_utc"] = "2026-08-02T00:00:00+00:00"
            expired["approval_card"] = draft.quote_approval_card(
                expired["payload"], expired["summary"], expired["live_evidence"],
                expired["expires_utc"]
            )
            expired["sha256"] = draft.approval_plan_digest(expired)
            expired["approval_card"]["plan_sha256"] = expired["sha256"]
            plan_path.write_text(json.dumps(expired), encoding="utf-8")
            with self.assertRaisesRegex(draft.DraftToolError, "expired"):
                draft.load_verified_plan(str(plan_path), "quote")

    def test_quote_card_cannot_be_relabelled_as_revision_even_with_a_new_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan_dir = Path(temp).resolve() / "plans"
            plan_dir.mkdir()
            plan = self.stage_quote({
                "customer_id": "1001",
                "line_items": [{
                    "item_id": "2002", "quantity": 1, "rate": 10,
                    "quantity_source": "PO", "rate_source": "list",
                }],
            }, plan_dir)
            path = next(plan_dir.glob("*_quote_*.json"))
            plan["approval_card"]["operation"] = draft.REVISION_OPERATION
            plan["approval_card"]["plan_sha256"] = ""
            plan["sha256"] = draft.approval_plan_digest(plan)
            plan["approval_card"]["plan_sha256"] = plan["sha256"]
            path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaisesRegex(draft.DraftToolError, "does not exactly match"):
                draft.load_verified_plan(str(path), "quote")

    def test_new_card_supersedes_old_card_and_bare_approved_is_not_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan_dir = Path(temp).resolve() / "plans"
            plan_dir.mkdir()
            first = self.stage_quote({
                "customer_id": "1001",
                "line_items": [{
                    "item_id": "2002", "quantity": 1, "rate": 10,
                    "quantity_source": "PO A", "rate_source": "list A",
                }],
            }, plan_dir)
            second = self.stage_quote({
                "customer_id": "1001",
                "line_items": [{
                    "item_id": "2002", "quantity": 2, "rate": 10,
                    "quantity_source": "PO B", "rate_source": "list B",
                }],
            }, plan_dir)
            paths = sorted(plan_dir.glob("*_quote_*.json"))
            by_digest = {
                json.loads(path.read_text(encoding="utf-8"))["sha256"]: path for path in paths
            }
            registry_path = plan_dir / draft.APPROVAL_REGISTRY_DIRNAME / "active.json"
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(len(registry["entries"]), 1)
            self.assertEqual(registry["entries"][0]["plan_sha256"], second["sha256"])
            self.assertNotEqual(first["sha256"], second["sha256"])
            with patch.object(draft, "PLAN_DIR", plan_dir), patch.object(
                draft.zoho_tool, "load_vault", side_effect=AssertionError("vault must not open")
            ), patch.object(draft, "urlopen", side_effect=AssertionError("no network")):
                with self.assertRaisesRegex(draft.DraftToolError, "not the sole active approval card"):
                    draft.command_commit(
                        argparse.Namespace(plan=str(by_digest[first["sha256"]]), approval="APPROVED"),
                        "quote",
                    )

    def test_quote_create_card_is_single_use_and_commit_remains_draft_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan_dir = Path(temp).resolve() / "plans"
            plan_dir.mkdir()
            plan = self.stage_quote({
                "customer_id": "1001",
                "line_items": [{
                    "item_id": "2002", "quantity": 1, "rate": 10,
                    "quantity_source": "PO", "rate_source": "list",
                }],
            }, plan_dir)
            plan_path = next(plan_dir.glob("*_quote_*.json"))
            calls = []

            def fake_post(_token, _domain, kind, _organization, payload):
                lock = plan_dir / draft.QUOTE_COMMIT_LOCK_DIRNAME / f"{plan['sha256']}.json"
                calls.append((kind, copy.deepcopy(payload), lock.exists()))
                return {"estimate": {
                    "estimate_id": "3003", "estimate_number": "QT-TEST", "status": "draft",
                }}

            vault = {
                "api_domain": "https://www.zohoapis.com",
                "books_organization_id": "9009",
                "scopes": ["ZohoBooks.estimates.CREATE"],
            }
            verified = {
                "estimate_id": "3003", "estimate_number": "QT-TEST", "status": "draft",
                "customer_id": "1001", "reference_number": "", "currency_code": "CAD",
                "line_items": [{
                    "item_id": "2002", "tax_id": "", "quantity": 1, "rate": 10,
                    "discount": 0,
                }],
                "sub_total": 10, "tax_total": 0, "total": 10,
            }
            with patch.object(draft, "PLAN_DIR", plan_dir), patch.object(
                draft.zoho_tool, "load_vault", return_value=dict(vault)
            ), patch.object(
                draft.zoho_tool, "validate_scopes"
            ), patch.object(
                draft.zoho_tool, "refresh_access_token", return_value=("token", dict(vault))
            ), patch.object(
                draft.zoho_tool, "save_vault"
            ), patch.object(
                draft.zoho_tool, "append_receipt"
            ), patch.object(
                draft, "api_post_allowed", side_effect=fake_post
            ), patch.object(
                draft, "quote_live_evidence", return_value=copy.deepcopy(plan["live_evidence"])
            ), patch.object(
                draft, "get_estimate", return_value=verified
            ), patch.object(draft, "urlopen", side_effect=AssertionError("no HTTP network")):
                with redirect_stdout(io.StringIO()):
                    draft.command_commit(
                        argparse.Namespace(plan=str(plan_path), approval="APPROVED"), "quote"
                    )
                with self.assertRaisesRegex(draft.DraftToolError, "consumed|replay"):
                    draft.command_commit(
                        argparse.Namespace(plan=str(plan_path), approval="APPROVED"), "quote"
                    )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "quote")
            self.assertEqual(calls[0][1]["status"], "draft")
            self.assertTrue(calls[0][2])
            self.assertFalse(any(key in calls[0][1] for key in (
                "send", "email", "accepted", "declined", "converted", "deleted"
            )))
            lock = plan_dir / draft.QUOTE_COMMIT_LOCK_DIRNAME / f"{plan['sha256']}.json"
            self.assertEqual(json.loads(lock.read_text(encoding="utf-8"))["status"],
                             "committed_verified")

    def test_approval_cards_do_not_expand_the_service_write_allowlist(self) -> None:
        self.assertEqual(draft.ALLOWED_POSTS, {
            "customer": "/books/v3/contacts",
            "quote": "/books/v3/estimates",
        })
        commands = draft.build_parser()._subparsers._group_actions[0].choices
        self.assertFalse(any(fragment in name for name in commands for fragment in (
            "send", "email", "status", "accept", "decline", "convert", "delete"
        )))

    def test_a_new_tds_quote_serializes_the_percentage_as_a_string(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan_dir = Path(temp).resolve() / "plans"
            plan_dir.mkdir()
            plan = self.stage_quote(
                {
                    "customer_id": draft.TDS_CUSTOMER_ID,
                    "reference_number": "PO TEST",
                    "line_items": [
                        {
                            "item_id": "2002",
                            "quantity": 2,
                            "rate": 100,
                            "quantity_source": "Rachad's words",
                            "rate_source": "price list",
                        }
                    ],
                },
                plan_dir,
            )
        line = plan["payload"]["line_items"][0]
        self.assertEqual(line["discount"], "10%")
        self.assertNotEqual(line["discount"], 10)
        self.assertNotEqual(line["discount"], 10.0)
        self.assertEqual(plan["summary"]["line_items"][0]["line_gross"], "200.00")
        self.assertEqual(plan["summary"]["line_items"][0]["line_discount_amount"], "20.00")
        self.assertEqual(plan["summary"]["line_items"][0]["line_net"], "180.00")
        self.assertEqual(plan["summary"]["net_subtotal_before_tax"], "180.00")

    def test_a_numeric_line_discount_is_refused_at_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan_dir = Path(temp).resolve() / "plans"
            plan_dir.mkdir()
            with self.assertRaisesRegex(draft.DraftToolError, "flat CAD amount"):
                self.stage_quote(
                    {
                        "customer_id": "1001",
                        "line_items": [
                            {
                                "item_id": "2002",
                                "quantity": 1,
                                "rate": 100,
                                "discount": 10,
                                "discount_source": "test",
                                "quantity_source": "test",
                                "rate_source": "test",
                            }
                        ],
                    },
                    plan_dir,
                )

    def test_a_percentage_string_is_accepted_for_any_customer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan_dir = Path(temp).resolve() / "plans"
            plan_dir.mkdir()
            plan = self.stage_quote(
                {
                    "customer_id": "1001",
                    "line_items": [
                        {
                            "item_id": "2002",
                            "quantity": 1,
                            "rate": 100,
                            "discount": "5%",
                            "discount_source": "Rachad's words",
                            "quantity_source": "test",
                            "rate_source": "test",
                        }
                    ],
                },
                plan_dir,
            )
        self.assertEqual(plan["payload"]["line_items"][0]["discount"], "5%")
        self.assertEqual(plan["summary"]["line_items"][0]["line_discount_amount"], "5.00")

    def test_a_legacy_numeric_plan_is_refused_before_the_vault(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            legacy_path = Path(temp) / "legacy_quote.json"
            core = {
                "tool": draft.TOOL_NAME,
                "kind": "quote",
                "created_utc": "2026-08-10T23:37:50.653749+00:00",
                "payload": {
                    "customer_id": draft.TDS_CUSTOMER_ID,
                    "status": "draft",
                    "discount_type": "item_level",
                    "is_discount_before_tax": True,
                    "line_items": [
                        {
                            "item_id": "96274000000019583",
                            "name": "FRP STUB FLANGE-1\"/150PSI/D411",
                            "quantity": 2.0,
                            "rate": 50.4,
                            "discount": 10.0,
                            "tax_id": draft.TDS_GST_QST_TAX_ID,
                        }
                    ],
                },
                "sources": {},
                "summary": {},
            }
            plan = dict(core)
            plan["sha256"] = draft.plan_hash(core)
            legacy_path.write_text(json.dumps(plan), encoding="utf-8")
            with patch.object(
                draft.zoho_tool, "load_vault", side_effect=AssertionError("vault must not open")
            ), patch.object(
                draft.zoho_tool, "refresh_access_token", side_effect=AssertionError("no token")
            ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
                with self.assertRaises(draft.DraftToolError) as caught:
                    draft.command_commit(
                        argparse.Namespace(plan=str(legacy_path), approval="APPROVED"), "quote"
                    )
        self.assertIn("10%", str(caught.exception))

    def test_the_two_consumed_create_plans_can_no_longer_be_replayed(self) -> None:
        for estimate_id in (QT29, QT30):
            source = Path(draft.CORRECTION_TARGETS[estimate_id]["source_plan"])
            with self.subTest(plan=source.name):
                self.assertTrue(source.exists())
                with patch.object(
                    draft.zoho_tool, "load_vault", side_effect=AssertionError("vault must not open")
                ), patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
                    with self.assertRaises(draft.DraftToolError):
                        draft.command_commit(
                            argparse.Namespace(plan=str(source), approval="APPROVED"), "quote"
                        )

    def test_percentage_discount_helper_rules(self) -> None:
        self.assertEqual(draft.percentage_discount(0, "d"), Decimal("0"))
        self.assertEqual(draft.percentage_discount(0.0, "d"), Decimal("0"))
        self.assertEqual(draft.percentage_discount(None, "d"), Decimal("0"))
        self.assertEqual(draft.percentage_discount("10%", "d"), Decimal("10"))
        self.assertEqual(draft.percentage_discount("7.5%", "d"), Decimal("7.5"))
        self.assertEqual(draft.percentage_discount("100%", "d"), Decimal("100"))
        for bad in (10, 10.0, 1, -5, "10", "10 %", "%10", "abc", "101%", True, [], {}):
            with self.subTest(value=bad):
                with self.assertRaises(draft.DraftToolError):
                    draft.percentage_discount(bad, "d")

    def test_tds_policy_now_demands_the_percentage_string(self) -> None:
        policy = draft.CUSTOMER_QUOTE_POLICIES[draft.TDS_CUSTOMER_ID]
        self.assertEqual(policy["line_discount"], "10%")
        payload = {
            "customer_id": draft.TDS_CUSTOMER_ID,
            "discount_type": "item_level",
            "is_discount_before_tax": True,
            "line_items": [{"discount": "10%", "tax_id": draft.TDS_GST_QST_TAX_ID}],
        }
        draft.validate_quote_customer_policy(payload)
        for bad in (10, 10.0, "10", 0, "5%"):
            with self.subTest(value=bad):
                payload["line_items"][0]["discount"] = bad
                with self.assertRaises(draft.DraftToolError):
                    draft.validate_quote_customer_policy(payload)


class BoundaryTests(unittest.TestCase):
    def test_create_boundaries_are_unchanged(self) -> None:
        self.assertEqual(
            draft.ALLOWED_POSTS,
            {"customer": "/books/v3/contacts", "quote": "/books/v3/estimates"},
        )
        with patch.object(draft, "urlopen", side_effect=AssertionError("no write")):
            with self.assertRaises(draft.DraftToolError):
                draft.api_post_allowed("token", tool.EXPECTED_API_DOMAIN, "email", "99", {})
            with self.assertRaises(draft.DraftToolError):
                draft.api_post_allowed("token", tool.EXPECTED_API_DOMAIN, "correction", "99", {})

    def test_customer_creation_still_refuses_a_vendor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "customer.json"
            path.write_text(
                json.dumps({"contact_name": "Example", "contact_type": "vendor"}), encoding="utf-8"
            )
            with self.assertRaisesRegex(draft.DraftToolError, "customers only"):
                draft.command_stage_customer(argparse.Namespace(input=str(path), source="test"))

    def test_the_update_scope_is_commissioned_and_nothing_wider(self) -> None:
        self.assertIn(draft.ESTIMATE_UPDATE_SCOPE, tool.ALLOWED_WRITE_SCOPES)
        self.assertIn(draft.ESTIMATE_UPDATE_SCOPE, tool.SCOPES)
        tool.validate_scopes(tool.SCOPES)
        for forbidden in (
            "ZohoBooks.estimates.DELETE",
            "ZohoBooks.estimates.ALL",
            "ZohoBooks.fullaccess.all",
        ):
            self.assertNotIn(forbidden, tool.SCOPES)
            with self.assertRaises(tool.ZohoError):
                tool.validate_scopes([forbidden])

    def test_no_mail_capability_exists_anywhere_in_the_tool(self) -> None:
        source = Path(draft.__file__).read_text(encoding="utf-8")
        for forbidden in ("smtplib", "sendmail", "send_email", "Mail.Send", "email_id"):
            self.assertNotIn(forbidden, source)
        self.assertIn("email_sent", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
