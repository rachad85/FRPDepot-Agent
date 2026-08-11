"""The ONE commissioned deletion, and everything it must refuse.

Deleting is the most dangerous verb this module could carry, and it exists here
only because an unapproved live write on 2026-08-11 created invoice template
`CC - Accounting` 96274000001558092 and Rachad asked for it to be removed
through a commissioned path rather than by hand.

So the weight of these tests is on the refusals: any other template, a renamed
row, a default template, a template with attachments, a delete that also removed
something else. The happy path is deliberately NOT reachable yet - the native
Delete request has never been captured, and the tool refuses BEFORE its replay
lock so an honest refusal never burns a plan.

Kept in its own module: the main email-template suite was being edited
concurrently when this was written.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import zoho_email_template_tool as tool


SOURCE_ID = "96274000000000014"
TARGET_ID = tool.DELETE_TARGET_TEMPLATE_ID


def row(template_id: str, name: str, *, is_default: bool = False, documents=None) -> dict:
    return {
        "email_template_id": template_id,
        "name": name,
        "is_default": is_default,
        "documents": documents if documents is not None else [],
        "type": "invoice_notification",
    }


def live_rows() -> list[dict]:
    return [
        row(TARGET_ID, "CC - Accounting"),
        row(SOURCE_ID, "Default", is_default=True),
    ]


class DeleteTargetSelectionTests(unittest.TestCase):
    def test_the_one_commissioned_row_is_selected(self) -> None:
        found = tool.require_delete_target(live_rows(), SOURCE_ID)
        self.assertEqual(found["email_template_id"], TARGET_ID)
        self.assertEqual(found["name"], "CC - Accounting")

    def test_only_that_one_ID_is_reachable(self) -> None:
        rows = [row("96274000009999999", "CC - Accounting"), row(SOURCE_ID, "Default", is_default=True)]
        with self.assertRaisesRegex(tool.EmailTemplateError, "Expected exactly one live template"):
            tool.require_delete_target(rows, SOURCE_ID)

    def test_a_renamed_row_is_refused_even_with_the_right_ID(self) -> None:
        """An ID alone is not identity: Zoho could reissue it."""
        rows = [row(TARGET_ID, "Customer Statement"), row(SOURCE_ID, "Default", is_default=True)]
        with self.assertRaisesRegex(tool.EmailTemplateError, "not 'CC - Accounting'"):
            tool.require_delete_target(rows, SOURCE_ID)

    def test_a_default_template_is_never_deletable(self) -> None:
        rows = [row(TARGET_ID, "CC - Accounting", is_default=True)]
        with self.assertRaisesRegex(tool.EmailTemplateError, "organization default"):
            tool.require_delete_target(rows, SOURCE_ID)

    def test_the_source_template_is_never_deletable_even_if_constants_point_at_it(self) -> None:
        # The source ID comes from the FRESH live read, so pointing the constant
        # at Default cannot get past this.
        rows = [row(TARGET_ID, "CC - Accounting")]
        with self.assertRaisesRegex(tool.EmailTemplateError, "never deletable"):
            tool.require_delete_target(rows, TARGET_ID)

    def test_a_template_with_attachments_is_refused(self) -> None:
        rows = [row(TARGET_ID, "CC - Accounting", documents=[{"document_id": "1"}])]
        with self.assertRaisesRegex(tool.EmailTemplateError, "attached documents"):
            tool.require_delete_target(rows, SOURCE_ID)


class DeleteReadBackTests(unittest.TestCase):
    """Proving absence is not enough - nothing else may have moved."""

    def test_a_clean_delete_verifies(self) -> None:
        result = tool.verify_template_deleted([row(SOURCE_ID, "Default", is_default=True)], live_rows())
        self.assertEqual(result["deleted_template_id"], TARGET_ID)
        self.assertEqual(result["surviving_template_ids"], [SOURCE_ID])

    def test_a_target_still_present_is_refused(self) -> None:
        with self.assertRaisesRegex(tool.EmailTemplateError, "still live after the delete"):
            tool.verify_template_deleted(live_rows(), live_rows())

    def test_taking_another_template_with_it_is_refused(self) -> None:
        with self.assertRaisesRegex(tool.EmailTemplateError, "must not have"):
            tool.verify_template_deleted([], live_rows())

    def test_a_survivor_that_changed_is_refused(self) -> None:
        promoted = [row(SOURCE_ID, "Default", is_default=True)]
        promoted[0]["name"] = "Default renamed"
        with self.assertRaisesRegex(tool.EmailTemplateError, "changed during the delete"):
            tool.verify_template_deleted(promoted, live_rows())

    def test_a_template_appearing_during_the_delete_is_refused(self) -> None:
        after = [row(SOURCE_ID, "Default", is_default=True), row("96274000001999999", "New")]
        with self.assertRaisesRegex(tool.EmailTemplateError, "appeared during the delete"):
            tool.verify_template_deleted(after, live_rows())


class DeleteContractGateTests(unittest.TestCase):
    def test_commit_refuses_while_the_native_request_is_uncaptured(self) -> None:
        self.assertFalse(tool.DELETE_CONTRACT_CAPTURED)
        with self.assertRaisesRegex(tool.EmailTemplateError, "never been captured"):
            tool.require_delete_contract_commissioned()

    def test_the_gate_runs_before_any_replay_lock_is_written(self) -> None:
        """An honest refusal must cost nothing - the plan stays committable."""
        import inspect
        source = inspect.getsource(tool.command_commit_delete)
        gate = source.index("require_delete_contract_commissioned()")
        self.assertNotIn("write_lock(", source[:gate])
        # And no lock is written after it either, while no transport exists.
        self.assertNotIn("write_lock(", source)

    def test_the_module_still_carries_no_delete_transport(self) -> None:
        text = Path(tool.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"DELETE"', text)
        self.assertNotIn("urlopen", text)
        self.assertEqual(text.count('method="PUT"'), 0)


class DeletePlanTests(unittest.TestCase):
    @staticmethod
    def plan(**changes) -> dict:
        org = {"books_organization_id": "110002157575", "name": "FRP DEPOTS"}
        org["fingerprint"] = tool.digest_for(org)
        body = {
            "tool": tool.TOOL_NAME,
            "tool_version": tool.TOOL_VERSION,
            "schema_version": tool.SCHEMA_VERSION,
            "action": tool.DELETE_ACCIDENTAL_ACCOUNTING,
            "created_utc": tool.utc_now().isoformat(),
            "expires_utc": (tool.utc_now() + tool.timedelta(hours=1)).isoformat(),
            "nonce": "0" * 32,
            "origin": tool.origin_record(),
            "organization": org,
            "target": {"email_template_id": TARGET_ID, "name": "CC - Accounting"},
            "live_evidence": {
                "invoice_templates": live_rows(),
                "source_template_id": SOURCE_ID,
                "target_row": row(TARGET_ID, "CC - Accounting"),
            },
            "risk": "one deletion",
            "approval_required": tool.APPROVAL_WORD,
        }
        body.update(changes)
        return {**body, "sha256": tool.digest_for(body)}

    def test_a_well_formed_plan_validates(self) -> None:
        tool.validate_delete_plan(self.plan())

    def test_a_plan_targeting_another_template_is_refused(self) -> None:
        bad = self.plan(target={"email_template_id": SOURCE_ID, "name": "Default"})
        with self.assertRaisesRegex(tool.EmailTemplateError, "cannot delete"):
            tool.validate_delete_plan(bad)

    def test_a_tampered_plan_is_refused(self) -> None:
        bad = self.plan()
        bad["target"]["name"] = "CC - Logistics"
        with self.assertRaises(tool.EmailTemplateError):
            tool.validate_delete_plan(bad)

    def test_an_expired_plan_is_refused(self) -> None:
        stale = self.plan(expires_utc=(tool.utc_now() - tool.timedelta(minutes=1)).isoformat())
        with self.assertRaisesRegex(tool.EmailTemplateError, "expired"):
            tool.validate_delete_plan(stale)

    def test_a_create_action_cannot_ride_the_delete_validator(self) -> None:
        wrong = self.plan(action=tool.CREATE_ACCOUNTING_TEST)
        with self.assertRaisesRegex(tool.EmailTemplateError, "not the commissioned delete"):
            tool.validate_delete_plan(wrong)


class DeleteApprovalTests(unittest.TestCase):
    def test_only_the_byte_exact_word_is_accepted(self) -> None:
        tool.require_rachad_approval(tool.APPROVAL_WORD)
        for wrong in ("approved", " APPROVED", "APPROVED ", "APPROVED!", "yes", ""):
            with self.subTest(approval=wrong):
                with self.assertRaises(tool.EmailTemplateError):
                    tool.require_rachad_approval(wrong)

    def test_approval_is_checked_before_the_plan_is_even_read(self) -> None:
        import inspect
        source = inspect.getsource(tool.command_commit_delete)
        self.assertLess(
            source.index("require_rachad_approval"), source.index("contained_plan")
        )


if __name__ == "__main__":
    unittest.main()
