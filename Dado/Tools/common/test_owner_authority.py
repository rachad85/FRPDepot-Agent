"""Tests for the shared owner-authority module (autonomy programme, 2026-08-21).

Three things matter here:

1. The affirmative detector is the Aze copy, byte for byte (pinned hashes).
2. A clear go covers a reversible write; a money write additionally needs the
   time of his message, and that time must fall AFTER the plan was written
   (a go with no time is refused, a go before the plan is refused, a go after
   the plan with its time commits).
3. Nothing here ever says "permanent": a failed attempt is a re-stage.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import owner_authority as oa  # noqa: E402

PINNED = {
    "_normalize_reply": "b68990308e5d9b94f1e2600896069a8486f5ec1f2fe01fd652585df3b9b494e0",
    "is_clear_affirmative": "5378578fb083395d02ca345d077f93d1644a2e1baf082792c0552db9cb654507",
    "has_condition_or_negation": "af15e30d91de13287ba3511ca450153d3660c6afa29ccfd1ebcaa433c374866e",
}


class AffirmativeParityTests(unittest.TestCase):
    def test_the_three_copied_functions_are_byte_identical_to_the_aze_source(self) -> None:
        source = Path(oa.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        seen = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in PINNED:
                segment = ast.get_source_segment(source, node)
                seen[node.name] = hashlib.sha256(segment.encode("utf-8")).hexdigest()
        self.assertEqual(seen, PINNED, "the verbatim copy drifted from the Aze original; "
                         "change the Aze file first, then this copy, then the pins")

    def test_the_module_never_imports_across_the_company_wall(self) -> None:
        source = Path(oa.__file__).read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                self.assertNotIn("AgentTeam", stripped)
                self.assertNotIn("gate_receipt", stripped)
                self.assertNotIn("budget_owner", stripped)


class ClearAffirmativeTests(unittest.TestCase):
    def test_plain_yes_forms_count(self) -> None:
        for text in ("APPROVED", "approved", "Approved.", "yes", "Yes", "yes go ahead",
                     "go ahead", "do it", "Proceed", "ok", "okay", "sounds good",
                     "please go ahead", "yes please", "go", "green light", "confirmed",
                     "yes do the whole batch", "go ahead with all of them"):
            self.assertTrue(oa.is_clear_affirmative(text), text)

    def test_conditions_questions_and_negations_do_not(self) -> None:
        for text in ("yes but change the rate", "wait", "no", "not yet", "go ahead?",
                     "hold on", "yes if the price is right", "maybe", "x" * 81, "", "   ",
                     "check first", "yes, after lunch"):
            self.assertFalse(oa.is_clear_affirmative(text), text)
        self.assertFalse(oa.is_clear_affirmative(None))
        self.assertFalse(oa.is_clear_affirmative(12))

    def test_has_condition_or_negation_shares_the_vocabulary(self) -> None:
        self.assertTrue(oa.has_condition_or_negation("yes but"))
        self.assertFalse(oa.has_condition_or_negation("yes"))
        self.assertTrue(oa.has_condition_or_negation(None))


class ReversibleGoTests(unittest.TestCase):
    def test_exact_word_still_works_and_is_flagged(self) -> None:
        go = oa.require_owner_go("APPROVED")
        self.assertTrue(go.exact_word)
        self.assertEqual(go.lane, "telegram")
        self.assertEqual(go.kind, "reversible")
        self.assertIsNone(go.sent_utc)

    def test_any_clear_affirmative_covers_a_reversible_write(self) -> None:
        for text in ("yes", "go ahead", "do it", "proceed", "yes go ahead and rename them"):
            go = oa.require_owner_go(text, what="the rename")
            self.assertFalse(go.exact_word)
            self.assertEqual(go.text, text)

    def test_a_conditional_or_blank_word_is_refused(self) -> None:
        for text in ("yes but", "wait", "", None, "go ahead?"):
            with self.assertRaises(oa.OwnerAuthorityRefused):
                oa.require_owner_go(text)
        with self.assertRaises(oa.OwnerAuthorityRefused):
            oa.require_owner_go("yes\ngo")

    def test_lanes_are_his_own_lanes_and_aliases_map(self) -> None:
        self.assertEqual(oa.require_owner_go("yes", lane="discord").lane, "discord")
        self.assertEqual(oa.require_owner_go("yes", lane="api_server").lane, "dashboard")
        self.assertEqual(oa.require_owner_go("yes", lane="Dashboard").lane, "dashboard")
        self.assertEqual(oa.require_owner_go("yes", lane="").lane, "telegram")
        self.assertEqual(oa.require_owner_go("yes", lane=None).lane, "telegram")

    def test_a_relayed_or_unknown_lane_never_authorises(self) -> None:
        for lane in ("relay", "oar", "aze", "sary", "email", "cron", "subagent", "quoted"):
            with self.assertRaises(oa.OwnerAuthorityRefused):
                oa.require_owner_go("yes", lane=lane)
        with self.assertRaises(oa.OwnerAuthorityRefused):
            oa.require_owner_go("yes", lane="carrier-pigeon")

    def test_the_record_carries_lane_and_kind(self) -> None:
        record = oa.require_owner_go("go ahead", lane="discord").as_record()
        self.assertEqual(record["owner_go_lane"], "discord")
        self.assertEqual(record["owner_go_sent_utc"], "not stated")
        self.assertEqual(record["owner_go_kind"], "reversible")


class MoneyGoTests(unittest.TestCase):
    CREATED = "2026-08-21T10:00:00+00:00"
    EXPIRES = "2026-08-22T10:00:00+00:00"

    def test_yes_go_ahead_after_the_plan_is_accepted(self) -> None:
        go = oa.require_owner_go_after_plan(
            "yes go ahead", plan_created_utc=self.CREATED, plan_expires_utc=self.EXPIRES,
            sent_utc="2026-08-21T10:05:00+00:00", lane="telegram",
        )
        self.assertEqual(go.kind, "money")
        self.assertEqual(go.sent_utc, "2026-08-21T10:05:00+00:00")
        self.assertFalse(go.exact_word)

    def test_exact_approved_after_the_plan_is_accepted(self) -> None:
        go = oa.require_owner_go_after_plan(
            "APPROVED", plan_created_utc=self.CREATED, sent_utc="2026-08-21T10:00:01Z",
        )
        self.assertTrue(go.exact_word)

    def test_a_word_sent_before_the_plan_existed_is_refused(self) -> None:
        with self.assertRaises(oa.OwnerAuthorityRefused) as ctx:
            oa.require_owner_go_after_plan(
                "APPROVED", plan_created_utc=self.CREATED, sent_utc="2026-08-21T09:59:59+00:00",
            )
        self.assertIn("BEFORE this plan was created", str(ctx.exception))

    def test_a_word_sent_at_or_after_expiry_is_refused(self) -> None:
        with self.assertRaises(oa.OwnerAuthorityRefused):
            oa.require_owner_go_after_plan(
                "yes", plan_created_utc=self.CREATED, plan_expires_utc=self.EXPIRES,
                sent_utc=self.EXPIRES,
            )

    def test_a_go_with_no_time_is_refused_and_the_refusal_names_the_plan_time(self) -> None:
        """A3/A5: 'his go AFTER the plan' cannot be proven without the time of
        his message, so a money go with no time is refused -- never recorded
        as 'not stated'. The refusal names the flag and the plan's creation time."""
        for missing in (None, "", "   ", object()):
            with self.subTest(sent_utc=missing):
                with self.assertRaises(oa.OwnerAuthorityRefused) as ctx:
                    oa.require_owner_go_after_plan(
                        "yes go ahead", plan_created_utc=self.CREATED, sent_utc=missing,
                    )
                message = str(ctx.exception)
                self.assertIn("--approval-message-utc", message)
                self.assertIn("ISO-8601", message)
                self.assertIn("timezone", message)
                self.assertIn(self.CREATED, message)
                self.assertIn("AFTER the plan", message)

    def test_a_go_after_the_plan_with_its_time_commits(self) -> None:
        go = oa.require_owner_go_after_plan(
            "go ahead", plan_created_utc=self.CREATED, plan_expires_utc=self.EXPIRES,
            sent_utc="2026-08-21T12:00:00+00:00",
        )
        self.assertEqual(go.sent_utc, "2026-08-21T12:00:00+00:00")
        self.assertEqual(go.as_record()["owner_go_sent_utc"], "2026-08-21T12:00:00+00:00")

    def test_the_word_is_judged_before_the_time_so_a_condition_reads_as_a_condition(self) -> None:
        with self.assertRaises(oa.OwnerAuthorityRefused) as ctx:
            oa.require_owner_go_after_plan("yes but wait", plan_created_utc=self.CREATED)
        self.assertIn("unambiguous go", str(ctx.exception))
        self.assertNotIn("--approval-message-utc", str(ctx.exception))

    def test_a_conditional_word_is_refused_even_after_the_plan(self) -> None:
        with self.assertRaises(oa.OwnerAuthorityRefused):
            oa.require_owner_go_after_plan(
                "yes but lower the rate", plan_created_utc=self.CREATED,
                sent_utc="2026-08-21T11:00:00+00:00",
            )

    def test_naive_or_malformed_times_are_refused(self) -> None:
        with self.assertRaises(oa.OwnerAuthorityRefused):
            oa.require_owner_go_after_plan("yes", plan_created_utc="2026-08-21T10:00:00")
        with self.assertRaises(oa.OwnerAuthorityRefused):
            oa.require_owner_go_after_plan("yes", plan_created_utc=self.CREATED, sent_utc="yesterday")


class ParserHelperTests(unittest.TestCase):
    def test_reversible_parser_takes_approval_and_lane(self) -> None:
        parser = argparse.ArgumentParser()
        oa.add_owner_go_arguments(parser)
        args = parser.parse_args(["--approval", "yes", "--approval-lane", "discord"])
        self.assertEqual((args.approval, args.approval_lane), ("yes", "discord"))
        with self.assertRaises(SystemExit):
            parser.parse_args(["--approval", "yes", "--approval-message-utc", "x"])

    def test_money_parser_requires_the_message_time(self) -> None:
        parser = argparse.ArgumentParser()
        oa.add_owner_go_arguments(parser, money=True)
        args = parser.parse_args(["--approval", "APPROVED", "--approval-message-utc", "2026-08-21T10:00:00Z"])
        self.assertEqual(args.approval_message_utc, "2026-08-21T10:00:00Z")
        self.assertIsNone(args.approval_lane)
        with self.assertRaises(SystemExit):
            parser.parse_args(["--approval", "APPROVED"])


class AttemptRecordTests(unittest.TestCase):
    def test_records_never_claim_a_permanent_lock(self) -> None:
        go = oa.require_owner_go("yes")
        for status in (oa.STATUS_IN_FLIGHT, oa.STATUS_COMMITTED, oa.STATUS_NEEDS_RESTAGE,
                       oa.STATUS_INDETERMINATE):
            record = oa.attempt_record(status, plan_sha256="a" * 64, action="x", go=go, reason="r")
            self.assertFalse(record["permanent_lock"])
            self.assertNotIn("no_retry", record)
            self.assertIn("next_step", record)
            self.assertEqual(record["owner_go_lane"], "telegram")

    def test_explain_outcome_says_restage_and_never_permanent(self) -> None:
        text = oa.explain_outcome("Price tool", oa.STATUS_NEEDS_RESTAGE, "HTTP 500",
                                  completed=["1"], failed={"2": "HTTP 500"}, pending=["3"])
        self.assertIn("re-stage", text.lower())
        self.assertNotIn("permanent", text.lower())
        self.assertIn("Failed lines", text)
        money = oa.explain_outcome("Invoice tool", oa.STATUS_INDETERMINATE, "timeout", money=True)
        self.assertIn("his go to the NEW plan", money)
        self.assertNotIn("permanent", money.lower())

    def test_refuse_replay_distinguishes_spent_from_restage(self) -> None:
        class ToolError(RuntimeError):
            pass
        with self.assertRaises(ToolError) as spent:
            oa.refuse_replay(ToolError, {"status": oa.STATUS_COMMITTED})
        self.assertIn("spent", str(spent.exception))
        with self.assertRaises(ToolError) as restage:
            oa.refuse_replay(ToolError, {"status": oa.STATUS_NEEDS_RESTAGE})
        self.assertIn("Re-stage", str(restage.exception))
        self.assertNotIn("permanent", str(restage.exception).lower())
        with self.assertRaises(ToolError) as inflight:
            oa.refuse_replay(ToolError, {"status": oa.STATUS_IN_FLIGHT})
        self.assertIn("wait", str(inflight.exception))
        with self.assertRaises(ToolError):
            oa.refuse_replay(ToolError, None)

    def test_exclusive_then_replace(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.json"
            oa.write_json_exclusive_or_replace(path, {"a": 1}, exclusive=True)
            with self.assertRaises(FileExistsError):
                oa.write_json_exclusive_or_replace(path, {"a": 2}, exclusive=True)
            oa.write_json_exclusive_or_replace(path, {"a": 3}, exclusive=False)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 3})


class LiveStateAndReceiptTests(unittest.TestCase):
    def test_capture_load_roundtrip_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            plan = Path(folder) / "20260821T100000Z_item_rename_abc.json"
            plan.write_text("{}", encoding="utf-8")
            backup = oa.capture_live_state(plan, {"name": "old", "sku": "OLD-1"},
                                           what="item 1 name/sku", where="zoho inventory", plan_sha256="a" * 64)
            self.assertEqual(backup.name, "20260821T100000Z_item_rename_abc.live-before.json")
            record = oa.load_live_state(plan)
            self.assertEqual(record["state"], {"name": "old", "sku": "OLD-1"})
            self.assertEqual(record["where"], "zoho inventory")
            edited = json.loads(backup.read_text(encoding="utf-8"))
            edited["state"]["name"] = "tampered"
            backup.write_text(json.dumps(edited), encoding="utf-8")
            with self.assertRaises(oa.OwnerAuthorityRefused):
                oa.load_live_state(plan)
            with self.assertRaises(oa.OwnerAuthorityRefused):
                oa.load_live_state(Path(folder) / "never_staged.json")

    def test_restore_record_is_written_next_to_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            plan = Path(folder) / "plan.json"
            path = oa.record_restore(plan, {"status": "restored", "item_id": "1"})
            self.assertEqual(path.name, "plan.restore.json")
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(body["status"], "restored")
            self.assertIn("restored_utc", body)

    def test_receipt_keeps_the_existing_line_shape(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            receipts = Path(folder) / "receipts.jsonl"
            go = oa.require_owner_go("yes", lane="discord")
            oa.write_receipt(receipts, "zoho_item_renamed", what="item 1", where="zoho",
                             backup=Path(folder) / "b.json", plan="p.json", plan_sha256="f" * 64, go=go,
                             extra={"lines": 3})
            row = json.loads(receipts.read_text(encoding="utf-8").strip())
            self.assertEqual(set(row), {"ts", "action", "evidence"})
            self.assertIn("what=item 1", row["evidence"])
            self.assertIn("backup=", row["evidence"])
            self.assertIn("go_lane=discord", row["evidence"])
            self.assertIn("lines=3", row["evidence"])


class BatchProgressTests(unittest.TestCase):
    def test_a_failed_line_does_not_stop_the_others(self) -> None:
        progress = oa.BatchProgress(["a", "b", "c", "d"])
        progress.done("a")
        progress.fail("b", "HTTP 500")
        progress.done("c")
        self.assertEqual(progress.pending, ["d"])
        self.assertEqual(progress.status, oa.STATUS_NEEDS_RESTAGE)
        summary = progress.summary()
        self.assertEqual(summary["completed"], ["a", "c"])
        self.assertEqual(summary["failed"], {"b": "HTTP 500"})
        self.assertEqual(summary["restage_only"], ["b", "d"])

    def test_all_done_is_committed(self) -> None:
        progress = oa.BatchProgress(["a"])
        progress.done("a")
        self.assertEqual(progress.status, oa.STATUS_COMMITTED)
        self.assertEqual(progress.summary()["restage_only"], [])


if __name__ == "__main__":
    unittest.main()
