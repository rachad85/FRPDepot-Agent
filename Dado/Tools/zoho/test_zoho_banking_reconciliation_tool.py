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

import zoho_banking_reconciliation_tool as banking


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class ZohoBankingReconciliationToolTests(unittest.TestCase):
    def setUp(self) -> None:
        here = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(dir=here)
        self.root = Path(self.temp.name)
        self.plan_dir = self.root / "plans"
        self.plan_dir.mkdir()
        self.vault = {
            "api_domain": "https://www.zohoapis.ca",
            "books_organization_id": "99",
            "books_organization_name": "FRP Depot Inc.",
            "scopes": [banking.READ_SCOPE, banking.CREATE_SCOPE, banking.UPDATE_SCOPE],
        }
        self.organization = {"organization_id": "99", "name": "FRP Depot Inc."}
        self.counter = 0
        self.patchers = [
            mock.patch.object(banking, "PLAN_DIR", self.plan_dir),
            mock.patch.object(banking.zoho_tool, "load_vault", return_value=self.vault),
            mock.patch.object(
                banking.zoho_tool, "refresh_access_token", return_value=("token", self.vault)
            ),
            mock.patch.object(banking.zoho_tool, "save_vault"),
            mock.patch.object(banking.zoho_tool, "append_receipt"),
            mock.patch.object(banking, "get_frp_organization", return_value=self.organization),
            mock.patch.object(
                banking, "urlopen", side_effect=AssertionError("network is forbidden in unit tests")
            ),
        ]
        started = [patcher.start() for patcher in self.patchers]
        self.load_vault = started[1]
        self.refresh_access_token = started[2]
        self.save_vault = started[3]
        self.append_receipt = started[4]
        self.get_org = started[5]

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
    def source(transaction_id: str = "100", **changes) -> dict:
        value = {
            "transaction_id": transaction_id,
            "account_id": "10",
            "account_name": "Desjardins Operating",
            "date": "2026-08-07",
            "amount": 125.00,
            "currency_code": "CAD",
            "status": "uncategorized",
            "payee": "Example Payee",
            "reference_number": "BANK-100",
            "transaction_type": "deposit",
            "description": "Ordinary statement line",
        }
        value.update(changes)
        return value

    @staticmethod
    def transaction(transaction_id: str = "200", **changes) -> dict:
        value = {
            "transaction_id": transaction_id,
            "account_id": "20",
            "account_name": "Clearing",
            "date": "2026-08-07",
            "amount": -125.00,
            "currency_code": "CAD",
            "status": "posted",
            "payee": "Existing transaction",
            "reference_number": "EXISTING-200",
            "transaction_type": "owner_drawings",
            "description": "Existing outgoing transaction",
        }
        value.update(changes)
        return value

    @staticmethod
    def account(
        account_id: str, name: str | None = None, currency: str = "CAD",
        status: str = "active",
    ) -> dict:
        return {
            "account_id": account_id,
            "account_name": name or f"Account {account_id}",
            "currency_code": currency,
            "status": status,
        }

    @staticmethod
    def transfer(transaction_id: str = "300", **changes) -> dict:
        value = {
            "transaction_id": transaction_id,
            "account_id": "10",
            "account_name": "Old Transfer Source",
            "from_account_id": "10",
            "to_account_id": "20",
            "date": "2026-08-01",
            "amount": 125.00,
            "currency_code": "CAD",
            "currency_id": "CAD-CURRENCY-ID",
            "exchange_rate": 1.0,
            "status": "manually_added",
            "payee": "Internal transfer",
            "reference_number": "TRANSFER-300",
            "transaction_type": "transfer_fund",
            "description": "Preserve this transfer description",
        }
        value.update(changes)
        return value

    @staticmethod
    def sources(statement_line: str = "Ordinary bank statement line") -> dict:
        return {
            "instruction": "Rachad's reviewed reconciliation instruction",
            "statement_line": statement_line,
        }

    @staticmethod
    def evidence(outgoing_id: str = "") -> dict:
        value = {"basis": "Bank statement and live Zoho transaction review"}
        if outgoing_id:
            value["airwallex_outgoing_transaction_id"] = outgoing_id
        return value

    @staticmethod
    def update_sources(statement_line: str = "Reviewed existing transfer account links") -> dict:
        return {
            "instruction": "Rachad's 2026-07-24 live Sent Item in Outlook",
            "statement_line": statement_line,
        }

    @staticmethod
    def rewrite_with_hash(path: Path, mutate) -> dict:
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan.pop("sha256", None)
        mutate(plan)
        plan["sha256"] = banking.digest_for(plan)
        path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        return plan

    def stage(self, action: str, input_value: dict, transactions: dict[str, dict], accounts=None):
        accounts = accounts or {}

        def get_transaction(token, vault, transaction_id, mode):
            self.assertEqual(mode, "regular")
            return transactions[transaction_id]

        def get_account(token, vault, account_id):
            return accounts[account_id]

        with mock.patch.object(
            banking, "get_bank_transaction", side_effect=get_transaction
        ), mock.patch.object(
            banking, "get_account", side_effect=get_account
        ), mock.patch.object(
            banking, "api_post_allowed"
        ) as post, mock.patch.object(
            banking, "api_put_transfer_accounts_allowed"
        ) as put, contextlib.redirect_stdout(io.StringIO()) as stdout:
            banking.command_stage(
                argparse.Namespace(input=str(self.input_path(input_value))), action
            )
        post.assert_not_called()
        put.assert_not_called()
        result = json.loads(stdout.getvalue())
        return Path(result["plan"]), result

    def stage_match(self):
        source = self.source()
        target = self.transaction()
        return self.stage("match", {
            "source_transaction_id": "100",
            "transactions_to_be_matched": [
                {"transaction_id": "200", "transaction_type": "owner_drawings"}
            ],
            "sources": self.sources(),
            "evidence": self.evidence(),
        }, {"100": source, "200": target})

    def stage_categorize(self, source=None, payload=None, statement_line=None, outgoing=None):
        source = source or self.source()
        payload = payload or {
            "from_account_id": "10",
            "to_account_id": "30",
            "transaction_type": "owner_drawings",
            "description": "Reviewed bank expense",
        }
        input_value = {
            "source_transaction_id": "100",
            "payload": payload,
            "sources": self.sources(statement_line or "Ordinary bank statement line"),
            "evidence": self.evidence("200" if outgoing else ""),
        }
        transactions = {"100": source}
        if outgoing:
            transactions["200"] = outgoing
        accounts = {
            "10": self.account("10", "Desjardins Operating"),
            "30": self.account("30", "Reviewed Category"),
        }
        return self.stage("categorize", input_value, transactions, accounts)

    def stage_update(self, source=None, payload=None, accounts=None, statement_line=None):
        source = source or self.transfer()
        payload = payload or {
            "new_from_account_id": "30",
            "new_to_account_id": "20",
        }
        accounts = accounts or {
            "10": self.account("10", "Old Transfer Source"),
            "20": self.account("20", "Transfer Destination"),
            "30": self.account("30", "Corrected Transfer Source"),
        }
        return self.stage("update_transfer_accounts", {
            "source_transaction_id": source["transaction_id"],
            "payload": payload,
            "sources": self.update_sources(statement_line or "Reviewed existing transfer account links"),
            "evidence": {"basis": "Live transfer and account records reviewed"},
        }, {source["transaction_id"]: source}, accounts)

    def test_detail_missing_status_is_joined_to_exact_source_account_list_row(self) -> None:
        detail = self.transfer()
        detail.pop("status")
        other_side = {
            "transaction_id": "300", "account_id": "20", "status": "categorized",
        }
        source_side = {
            "transaction_id": "300", "account_id": "10", "status": "manually_added",
        }
        responses = [
            {"banktransaction": detail},
            {"banktransactions": [other_side, source_side], "page_context": {"has_more_page": False}},
        ]
        with mock.patch.object(banking.zoho_tool, "api_get", side_effect=responses) as api_get:
            result = banking.get_bank_transaction("token", self.vault, "300", "regular")
        self.assertEqual(result["status"], "manually_added")
        self.assertEqual(api_get.call_count, 2)
        self.assertIn("date_start=2026-08-01", api_get.call_args_list[1].args[2])
        self.assertIn("filter_by=Status.All", api_get.call_args_list[1].args[2])

    def test_bank_chart_detail_missing_currency_is_joined_to_exact_bank_account_row(self) -> None:
        responses = [
            {"chart_of_account": {
                "account_id": "30", "account_name": "AWX_FRPDepot Inc._CAD",
                "account_type": "bank", "status": "inactive",
            }},
            {"bankaccounts": [{
                "account_id": "30", "account_name": "AWX_FRPDepot Inc._CAD",
                "currency_code": "CAD", "is_active": False,
            }], "page_context": {"has_more_page": False}},
        ]
        with mock.patch.object(banking.zoho_tool, "api_get", side_effect=responses) as api_get:
            result = banking.get_account("token", self.vault, "30")
        self.assertEqual(result["currency_code"], "CAD")
        self.assertEqual(result["status"], "inactive")
        self.assertEqual(api_get.call_count, 2)
        self.assertIn("/books/v3/bankaccounts?", api_get.call_args_list[1].args[2])

    def test_stage_match_has_full_digest_expiry_org_lock_live_snapshots_and_human_summary(self) -> None:
        path, result = self.stage_match()
        plan = json.loads(path.read_text(encoding="utf-8"))
        saved = plan.pop("sha256")
        self.assertRegex(saved, r"^[0-9a-f]{64}$")
        self.assertEqual(saved, banking.digest_for(plan))
        created = banking.parse_time(plan["created_utc"], "created")
        expires = banking.parse_time(plan["expires_utc"], "expires")
        self.assertEqual(expires - created, timedelta(hours=24))
        self.assertRegex(plan["nonce"], r"^[0-9a-f]{32}$")
        self.assertEqual(plan["organization"], self.organization)
        self.assertEqual(plan["source_snapshot"]["before_state"]["transaction_id"], "100")
        self.assertEqual(plan["target_snapshots"][0]["before_state"]["transaction_id"], "200")
        self.assertEqual(plan["payload"], {
            "transactions_to_be_matched": [
                {"transaction_id": "200", "transaction_type": "owner_drawings"}
            ]
        })
        summary = plan["human_summary"]
        for field in (
            "account", "date", "payee", "reference", "amount", "currency", "status", "target"
        ):
            self.assertIn(field, summary)
        self.assertEqual(result["status"], "STAGED_NOT_COMMITTED")
        self.assertEqual(result["approval"], "APPROVED")
        self.assertEqual(result["plan_sha256"], saved)

    def test_stage_allows_only_narrow_match_categorize_unmatch_uncategorize_shapes(self) -> None:
        categorize_path, _ = self.stage_categorize()
        categorize = json.loads(categorize_path.read_text(encoding="utf-8"))
        self.assertEqual(set(categorize["payload"]), {
            "from_account_id", "to_account_id", "transaction_type", "amount", "date", "description"
        })
        self.assertEqual(categorize["payload"]["amount"], 125.0)
        self.assertEqual(categorize["payload"]["date"], "2026-08-07")
        self.assertEqual(
            [row["role"] for row in categorize["target_snapshots"]],
            ["from_account", "to_account"],
        )

        matched_source = self.source(
            status="matched", matched_transactions=[{"transaction_id": "200"}]
        )
        unmatch_path, _ = self.stage("unmatch", {
            "source_transaction_id": "100",
            "target_transaction_ids": ["200"],
            "sources": self.sources(),
            "evidence": self.evidence(),
        }, {"100": matched_source, "200": self.transaction()})
        unmatch = json.loads(unmatch_path.read_text(encoding="utf-8"))
        self.assertEqual(unmatch["payload"], {})
        self.assertEqual(unmatch["target_snapshots"][0]["role"], "matched_transaction")

        categorized_source = self.source(status="categorized", to_account_id="30")
        uncat_path, _ = self.stage("uncategorize", {
            "source_transaction_id": "100",
            "target_account_ids": ["30"],
            "sources": self.sources(),
            "evidence": self.evidence(),
        }, {"100": categorized_source}, {"30": self.account("30")})
        uncat = json.loads(uncat_path.read_text(encoding="utf-8"))
        self.assertEqual(uncat["payload"], {})
        self.assertEqual(uncat["target_snapshots"][0]["role"], "category_account")

    def test_closed_inputs_reject_delete_create_rule_account_change_and_extra_payload_before_token(self) -> None:
        cases = [
            ("categorize", {
                "source_transaction_id": "100", "payload": {
                    "from_account_id": "10", "to_account_id": "30", "transaction_type": "owner_drawings",
                    "delete": True,
                }, "sources": self.sources(), "evidence": self.evidence(),
            }),
            ("match", {
                "source_transaction_id": "100",
                "transactions_to_be_matched": [{
                    "transaction_id": "200", "transaction_type": "owner_drawings", "amount": 125,
                }],
                "sources": self.sources(), "evidence": self.evidence(),
            }),
            ("unmatch", {
                "source_transaction_id": "100", "target_transaction_ids": ["200"],
                "sources": self.sources(), "evidence": self.evidence(), "create_rule": True,
            }),
        ]
        for action, value in cases:
            with self.subTest(action=action):
                self.load_vault.reset_mock()
                with self.assertRaises(banking.BankingToolError):
                    banking.command_stage(
                        argparse.Namespace(input=str(self.input_path(value))), action
                    )
                self.load_vault.assert_not_called()

    def test_tamper_expiry_and_case_sensitive_approval_reject_before_token_or_lock(self) -> None:
        tampered, _ = self.stage_match()
        plan = json.loads(tampered.read_text(encoding="utf-8"))
        plan["human_summary"]["amount"] = "999"
        tampered.write_text(json.dumps(plan), encoding="utf-8")
        self.load_vault.reset_mock()
        with self.assertRaisesRegex(banking.BankingToolError, "hash check failed"):
            banking.command_commit(
                argparse.Namespace(plan=str(tampered), approval="APPROVED"), "match"
            )
        self.load_vault.assert_not_called()

        expired, _ = self.stage_match()
        expiry = banking.utc_now() - timedelta(seconds=1)
        self.rewrite_with_hash(expired, lambda value: value.update({
            "created_utc": (expiry - timedelta(hours=24)).isoformat(),
            "expires_utc": expiry.isoformat(),
        }))
        self.load_vault.reset_mock()
        with self.assertRaisesRegex(banking.BankingToolError, "expired"):
            banking.command_commit(
                argparse.Namespace(plan=str(expired), approval="APPROVED"), "match"
            )
        self.load_vault.assert_not_called()

        approval_path, _ = self.stage_match()
        saved = json.loads(approval_path.read_text(encoding="utf-8"))["sha256"]
        for approval in ("approved", " APPROVED", "APPROVED ", "APPROVED NOW", ""):
            with self.subTest(approval=approval):
                self.load_vault.reset_mock()
                with self.assertRaisesRegex(banking.BankingToolError, "case-sensitive"):
                    banking.command_commit(
                        argparse.Namespace(plan=str(approval_path), approval=approval), "match"
                    )
                self.load_vault.assert_not_called()
                self.assertFalse(banking.lock_path(saved).exists())

    def test_transport_exposes_exactly_four_posts_and_closed_payloads(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append({
                "method": request.get_method(),
                "url": request.full_url,
                "payload": json.loads(request.data),
            })
            return FakeResponse({"code": 0})

        categorize_payload = {
            "from_account_id": "10", "to_account_id": "30", "transaction_type": "transfer_fund",
            "amount": 125.0, "date": "2026-08-07", "reference_number": "R-1",
        }
        with mock.patch.object(banking, "urlopen", side_effect=fake_urlopen):
            banking.api_post_allowed("token", "https://www.zohoapis.ca", "match", "100", "99", {
                "transactions_to_be_matched": [
                    {"transaction_id": "200", "transaction_type": "owner_drawings"}
                ]
            })
            banking.api_post_allowed(
                "token", "https://www.zohoapis.ca", "categorize", "100", "99", categorize_payload
            )
            banking.api_post_allowed("token", "https://www.zohoapis.ca", "unmatch", "100", "99", {})
            banking.api_post_allowed("token", "https://www.zohoapis.ca", "uncategorize", "100", "99", {})
        self.assertEqual([row["method"] for row in captured], ["POST"] * 4)
        self.assertIn("/uncategorized/100/match?organization_id=99", captured[0]["url"])
        self.assertIn("/uncategorized/100/categorize?organization_id=99", captured[1]["url"])
        self.assertIn("/banktransactions/100/unmatch?organization_id=99", captured[2]["url"])
        self.assertIn("/banktransactions/100/uncategorize?organization_id=99", captured[3]["url"])
        all_urls = " ".join(row["url"] for row in captured)
        self.assertNotIn("/banktransactions?", all_urls)
        self.assertNotIn("/rules", all_urls)
        with mock.patch.object(banking, "urlopen") as transport:
            forbidden = [
                ("delete", {}),
                ("create", {}),
                ("exclude", {}),
                ("restore", {}),
                ("unmatch", {"delete": True}),
                ("categorize", {**categorize_payload, "account_name": "changed"}),
            ]
            for action, payload in forbidden:
                with self.subTest(action=action), self.assertRaises(banking.BankingToolError):
                    banking.api_post_allowed(
                        "token", "https://www.zohoapis.ca", action, "100", "99", payload
                    )
            transport.assert_not_called()

    def test_successful_commit_reserves_refetches_writes_once_verifies_and_receipts(self) -> None:
        original = self.source()
        path, _ = self.stage_categorize(source=original)
        readback = self.source(
            status="categorized", transaction_type="owner_drawings",
            from_account_id="10", to_account_id="30",
        )
        reads = [original, readback]
        accounts = {"10": self.account("10", "Desjardins Operating"), "30": self.account("30", "Reviewed Category")}
        with mock.patch.object(
            banking, "get_bank_transaction", side_effect=reads
        ) as get_transaction, mock.patch.object(
            banking, "get_account", side_effect=lambda token, vault, account_id: accounts[account_id]
        ) as get_account, mock.patch.object(
            banking, "api_post_allowed", return_value={"code": 0}
        ) as post, contextlib.redirect_stdout(io.StringIO()) as stdout:
            banking.command_commit(
                argparse.Namespace(plan=str(path), approval="APPROVED"), "categorize"
            )
        self.assertEqual(get_transaction.call_count, 2)
        self.assertEqual(get_account.call_count, 2)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[2], "categorize")
        self.assertEqual(post.call_args.args[6 - 1], json.loads(path.read_text(encoding="utf-8"))["payload"])
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertTrue(result["replay_locked"])
        lock = json.loads(banking.lock_path(result["plan_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")
        receipt_actions = [call.args[0] for call in self.append_receipt.call_args_list]
        self.assertIn("zoho_banking_reconciliation_committed_verified", receipt_actions)
        self.save_vault.assert_called_once_with(self.vault)

    def test_changed_source_or_target_blocks_post_after_single_use_reservation(self) -> None:
        original = self.source()
        path, _ = self.stage_categorize(source=original)
        changed = self.source(amount=126.00)
        with mock.patch.object(
            banking, "get_bank_transaction", return_value=changed
        ), mock.patch.object(banking, "api_post_allowed") as post:
            with self.assertRaisesRegex(banking.BankingToolError, "aborted_before_write"):
                banking.command_commit(
                    argparse.Namespace(plan=str(path), approval="APPROVED"), "categorize"
                )
            post.assert_not_called()
        digest = json.loads(path.read_text(encoding="utf-8"))["sha256"]
        lock = json.loads(banking.lock_path(digest).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "aborted_before_write")
        self.assertTrue(lock["no_retry"])
        self.load_vault.reset_mock()
        with mock.patch.object(banking, "api_post_allowed") as retry:
            with self.assertRaisesRegex(banking.BankingToolError, "cannot be replayed"):
                banking.command_commit(
                    argparse.Namespace(plan=str(path), approval="APPROVED"), "categorize"
                )
            retry.assert_not_called()
        self.load_vault.assert_not_called()

    def test_airwallex_amount_or_text_requires_transfer_and_verified_outgoing(self) -> None:
        guarded_source = self.source(
            date="2026-07-23", amount=78146.27, payee="Airwallex closure transfer"
        )
        bad_input = {
            "source_transaction_id": "100",
            "payload": {
                "from_account_id": "10", "to_account_id": "30", "transaction_type": "other_income"
            },
            "sources": self.sources("Desjardins deposit CAD 78,146.27"),
            "evidence": self.evidence(),
        }
        with mock.patch.object(banking, "get_bank_transaction", return_value=guarded_source), \
             mock.patch.object(banking, "get_account", side_effect=lambda token, vault, account_id: self.account(account_id)), \
             self.assertRaisesRegex(banking.BankingToolError, "transfer_fund"):
            banking.command_stage(
                argparse.Namespace(input=str(self.input_path(bad_input))), "categorize"
            )

        transfer_input = {
            **bad_input,
            "payload": {
                "from_account_id": "10", "to_account_id": "30", "transaction_type": "transfer_fund"
            },
        }
        with mock.patch.object(banking, "get_bank_transaction", return_value=guarded_source), \
             mock.patch.object(banking, "get_account", side_effect=lambda token, vault, account_id: self.account(account_id)), \
             self.assertRaisesRegex(banking.BankingToolError, "outgoing transaction ID"):
            banking.command_stage(
                argparse.Namespace(input=str(self.input_path(transfer_input))), "categorize"
            )

        text_source = self.source(amount=125.00)
        text_input = {
            "source_transaction_id": "100",
            "payload": {
                "from_account_id": "10", "to_account_id": "30", "transaction_type": "interest_income"
            },
            "sources": self.sources("AIRWALLEX settlement"),
            "evidence": self.evidence(),
        }
        with mock.patch.object(banking, "get_bank_transaction", return_value=text_source), \
             mock.patch.object(banking, "get_account", side_effect=lambda token, vault, account_id: self.account(account_id)), \
             self.assertRaisesRegex(banking.BankingToolError, "transfer_fund"):
            banking.command_stage(
                argparse.Namespace(input=str(self.input_path(text_input))), "categorize"
            )

    def test_airwallex_plan_contains_live_outgoing_compatibility_and_commit_rechecks_it(self) -> None:
        source = self.source(
            date="2026-07-23", amount=21642.71, currency_code="USD",
            payee="Airwallex closure transfer",
        )
        outgoing = self.transaction(
            transaction_id="200", date="2026-07-22", amount=-21642.71,
            currency_code="USD", transaction_type="transfer_fund",
            description="Airwallex outgoing closure transfer",
        )
        accounts = {
            "10": {**self.account("10"), "currency_code": "USD"},
            "30": {**self.account("30"), "currency_code": "USD"},
        }
        input_value = {
            "source_transaction_id": "100",
            "payload": {
                "from_account_id": "10", "to_account_id": "30",
                "transaction_type": "transfer_fund",
            },
            "sources": self.sources("2026-07-23 AIRWALLEX USD 21,642.71"),
            "evidence": self.evidence("200"),
        }
        path, result = self.stage(
            "categorize", input_value, {"100": source, "200": outgoing}, accounts
        )
        plan = json.loads(path.read_text(encoding="utf-8"))
        verification = plan["evidence"]["airwallex_verification"]
        self.assertTrue(verification["amount_compatible"])
        self.assertTrue(verification["currency_compatible"])
        self.assertTrue(verification["date_compatible"])
        self.assertTrue(verification["outgoing_direction_verified"])
        self.assertEqual(verification["date_difference_days"], 1)
        self.assertEqual(plan["evidence"]["airwallex_outgoing_transaction_id"], "200")
        self.assertIn("airwallex_outgoing", [row["role"] for row in plan["target_snapshots"]])
        self.assertTrue(result["evidence"]["airwallex_guarded"])

        changed_outgoing = {**outgoing, "date": "2026-07-01"}
        reads = {"100": source, "200": changed_outgoing}
        with mock.patch.object(
            banking, "get_bank_transaction", side_effect=lambda token, vault, transaction_id, mode: reads[transaction_id]
        ), mock.patch.object(
            banking, "get_account", side_effect=lambda token, vault, account_id: accounts[account_id]
        ), mock.patch.object(banking, "api_post_allowed") as post:
            with self.assertRaisesRegex(banking.BankingToolError, "aborted_before_write"):
                banking.command_commit(
                    argparse.Namespace(plan=str(path), approval="APPROVED"), "categorize"
                )
            post.assert_not_called()

    def test_airwallex_outgoing_incompatible_amount_currency_or_date_is_refused_at_stage(self) -> None:
        source = self.source(date="2026-07-23", amount=78146.27, currency_code="CAD")
        base_input = {
            "source_transaction_id": "100",
            "payload": {
                "from_account_id": "10", "to_account_id": "30",
                "transaction_type": "transfer_fund",
            },
            "sources": self.sources("Airwallex closure"),
            "evidence": self.evidence("200"),
        }
        bad_outgoing = [
            self.transaction(amount=-1, currency_code="CAD", transaction_type="transfer_fund"),
            self.transaction(amount=-78146.27, currency_code="USD", transaction_type="transfer_fund"),
            self.transaction(amount=-78146.27, currency_code="CAD", date="2026-06-01", transaction_type="transfer_fund"),
        ]
        for outgoing in bad_outgoing:
            with self.subTest(outgoing=outgoing):
                with mock.patch.object(
                    banking, "get_bank_transaction",
                    side_effect=lambda token, vault, transaction_id, mode, outgoing=outgoing: (
                        source if transaction_id == "100" else outgoing
                    ),
                ), mock.patch.object(
                    banking, "get_account", side_effect=lambda token, vault, account_id: self.account(account_id)
                ), self.assertRaisesRegex(banking.BankingToolError, "compatibility"):
                    banking.command_stage(
                        argparse.Namespace(input=str(self.input_path(base_input))), "categorize"
                    )

        same_id_input = {
            **base_input,
            "evidence": self.evidence("100"),
        }
        same_id_source = {
            **source,
            "transaction_type": "transfer_fund",
            "description": "outgoing transfer_fund",
        }
        with mock.patch.object(
            banking, "get_bank_transaction", return_value=same_id_source
        ), mock.patch.object(
            banking, "get_account", side_effect=lambda token, vault, account_id: self.account(account_id)
        ), self.assertRaisesRegex(banking.BankingToolError, "must differ"):
            banking.command_stage(
                argparse.Namespace(input=str(self.input_path(same_id_input))), "categorize"
            )

    def test_match_unmatch_and_uncategorize_commits_each_post_once_and_verify_readback(self) -> None:
        target = self.transaction()
        match_before = self.source()
        match_path, _ = self.stage_match()
        match_after = self.source(
            status="matched", matched_transactions=[{"transaction_id": "200"}]
        )

        unmatch_before = self.source(
            status="matched", matched_transactions=[{"transaction_id": "200"}]
        )
        unmatch_path, _ = self.stage("unmatch", {
            "source_transaction_id": "100",
            "target_transaction_ids": ["200"],
            "sources": self.sources(),
            "evidence": self.evidence(),
        }, {"100": unmatch_before, "200": target})
        unmatch_after = self.source(status="uncategorized")

        uncat_before = self.source(status="categorized", to_account_id="30")
        category_account = self.account("30")
        uncat_path, _ = self.stage("uncategorize", {
            "source_transaction_id": "100",
            "target_account_ids": ["30"],
            "sources": self.sources(),
            "evidence": self.evidence(),
        }, {"100": uncat_before}, {"30": category_account})
        uncat_after = self.source(status="uncategorized")

        cases = [
            ("match", match_path, [match_before, target, match_after], {}),
            ("unmatch", unmatch_path, [unmatch_before, target, unmatch_after], {}),
            ("uncategorize", uncat_path, [uncat_before, uncat_after], {"30": category_account}),
        ]
        for action, path, reads, accounts in cases:
            with self.subTest(action=action), mock.patch.object(
                banking, "get_bank_transaction", side_effect=reads
            ), mock.patch.object(
                banking, "get_account", side_effect=lambda token, vault, account_id, accounts=accounts: accounts[account_id]
            ), mock.patch.object(
                banking, "api_post_allowed", return_value={"code": 0}
            ) as post, contextlib.redirect_stdout(io.StringIO()) as stdout:
                banking.command_commit(
                    argparse.Namespace(plan=str(path), approval="APPROVED"), action
                )
                post.assert_called_once()
                self.assertEqual(post.call_args.args[2], action)
                self.assertEqual(json.loads(stdout.getvalue())["status"], "COMMITTED_AND_VERIFIED")

    def test_update_stage_reconstructs_put_payload_and_shows_full_before_after_summary(self) -> None:
        source = self.transfer()
        path, result = self.stage_update(source=source)
        plan = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(plan["action"], "update_transfer_accounts")
        self.assertEqual(plan["payload"], {
            "from_account_id": "30",
            "to_account_id": "20",
            "transaction_type": "transfer_fund",
            "amount": 125.0,
            "date": "2026-08-01",
            "exchange_rate": 1.0,
            "reference_number": "TRANSFER-300",
            "description": "Preserve this transfer description",
            "currency_id": "CAD-CURRENCY-ID",
        })
        self.assertEqual(
            [row["role"] for row in plan["target_snapshots"]],
            [
                "current_from_account", "current_to_account",
                "proposed_from_account", "proposed_to_account",
            ],
        )
        summary = plan["human_summary"]
        self.assertEqual(summary["transaction_id"], "300")
        self.assertEqual(summary["transaction_type"], "transfer_fund")
        self.assertEqual(summary["status"], "manually_added")
        self.assertEqual(summary["amount"], "125")
        self.assertEqual(summary["date"], "2026-08-01")
        self.assertEqual(summary["currency"], "CAD")
        self.assertEqual(summary["exchange_rate"], "1")
        self.assertEqual(summary["before"]["from_account"]["name"], "Old Transfer Source")
        self.assertEqual(summary["before"]["to_account"]["account_id"], "20")
        self.assertEqual(summary["after"]["from_account"]["name"], "Corrected Transfer Source")
        self.assertEqual(summary["after"]["to_account"]["account_id"], "20")
        self.assertEqual(plan["evidence"], {
            "basis": "Live transfer and account records reviewed",
            "airwallex_guarded": False,
            "existing_transfer_is_source_ledger_evidence": True,
            "airwallex_mapping": None,
        })
        self.assertEqual(result["status"], "STAGED_NOT_COMMITTED")
        self.assertIn(
            "zoho_banking_update_transfer_accounts_plan_staged_not_committed",
            [call.args[0] for call in self.append_receipt.call_args_list],
        )

    def test_update_input_is_closed_and_requires_basis_and_sent_item_citation_before_token(self) -> None:
        base = {
            "source_transaction_id": "300",
            "payload": {"new_from_account_id": "30", "new_to_account_id": "20"},
            "sources": self.update_sources(),
            "evidence": {"basis": "Live records reviewed"},
        }
        cases = [
            {**base, "payload": {**base["payload"], "amount": 999}},
            {**base, "payload": {**base["payload"], "date": "2026-01-01"}},
            {**base, "payload": {**base["payload"], "transaction_type": "deposit"}},
            {**base, "payload": {**base["payload"], "exchange_rate": 2}},
            {**base, "payload": {**base["payload"], "description": "replace"}},
            {**base, "payload": {**base["payload"], "reference_number": "replace"}},
            {**base, "evidence": {
                "basis": "Live records reviewed", "airwallex_outgoing_transaction_id": "999"
            }},
            {**base, "evidence": {"basis": ""}},
            {**base, "sources": {
                "instruction": "generic review", "statement_line": "no Outlook citation"
            }},
        ]
        for value in cases:
            with self.subTest(value=value):
                self.load_vault.reset_mock()
                with self.assertRaises(banking.BankingToolError):
                    banking.command_stage(
                        argparse.Namespace(input=str(self.input_path(value))),
                        "update_transfer_accounts",
                    )
                self.load_vault.assert_not_called()

    def test_update_requires_manual_transfer_real_change_and_currency_compatibility(self) -> None:
        with self.assertRaisesRegex(banking.BankingToolError, "transaction_type exactly transfer_fund"):
            self.stage_update(source=self.transfer(transaction_type="deposit"))
        with self.assertRaisesRegex(banking.BankingToolError, "manually_added"):
            self.stage_update(source=self.transfer(status="categorized"))
        with self.assertRaisesRegex(banking.BankingToolError, "actually change"):
            self.stage_update(payload={"new_from_account_id": "10", "new_to_account_id": "20"})
        with self.assertRaisesRegex(banking.BankingToolError, "must differ"):
            self.stage_update(payload={"new_from_account_id": "20", "new_to_account_id": "20"})

        source_without_rate = self.transfer()
        source_without_rate.pop("exchange_rate")
        accounts = {
            "10": self.account("10", "Old Transfer Source"),
            "20": self.account("20", "Transfer Destination", "USD"),
            "30": self.account("30", "Corrected Transfer Source", "CAD"),
        }
        with self.assertRaisesRegex(banking.BankingToolError, "currencies must match"):
            self.stage_update(source=source_without_rate, accounts=accounts)
        path, _ = self.stage_update(source=self.transfer(exchange_rate=1.25), accounts=accounts)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["payload"]["exchange_rate"], 1.25)

    def test_airwallex_update_hard_codes_both_verified_account_mappings_without_outgoing_id(self) -> None:
        cases = [
            {
                "transaction_id": "96274000001533058", "amount": 78146.27,
                "new_from": "96274000000149537", "new_name": "AWX_FRPDepot Inc._CAD",
                "to": "96274000001409019", "to_name": "Chequing account (C)", "currency": "CAD",
            },
            {
                "transaction_id": "96274000001535012", "amount": 21642.71,
                "new_from": "96274000000149257", "new_name": "AWX_FRPDepot Inc._USD",
                "to": "96274000001409012",
                "to_name": "USD Desjardins corporate build-up account", "currency": "USD",
            },
        ]
        for row in cases:
            with self.subTest(transaction_id=row["transaction_id"]):
                source = self.transfer(
                    transaction_id=row["transaction_id"],
                    account_id="96274000000097003",
                    account_name="FRPDepot Inc.",
                    from_account_id="96274000000097003",
                    to_account_id=row["to"],
                    amount=row["amount"],
                    date="2026-07-23",
                    currency_code="CAD",
                    reference_number="Closing Balance From Airwallex Account",
                    description="Airwallex closure transfer",
                )
                accounts = {
                    "96274000000097003": self.account(
                        "96274000000097003", "FRPDepot Inc.", "CAD", "active"
                    ),
                    row["new_from"]: self.account(
                        row["new_from"], row["new_name"], row["currency"], "inactive"
                    ),
                    row["to"]: self.account(
                        row["to"], row["to_name"], row["currency"], "active"
                    ),
                }
                path, _ = self.stage_update(
                    source=source,
                    payload={"new_from_account_id": row["new_from"], "new_to_account_id": row["to"]},
                    accounts=accounts,
                    statement_line=f"Airwallex closure transfer {row['amount']}",
                )
                plan = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(plan["evidence"]["airwallex_guarded"])
                self.assertTrue(plan["evidence"]["existing_transfer_is_source_ledger_evidence"])
                self.assertNotIn("airwallex_outgoing_transaction_id", plan["evidence"])
                self.assertEqual(plan["evidence"]["airwallex_mapping"]["transaction_id"], row["transaction_id"])
                self.assertEqual(plan["payload"]["from_account_id"], row["new_from"])
                self.assertEqual(plan["payload"]["to_account_id"], row["to"])
                self.assertFalse(any(
                    snapshot["record_type"] == "transaction"
                    for snapshot in plan["target_snapshots"]
                ))

    def test_airwallex_update_rejects_destination_source_amount_or_name_drift(self) -> None:
        source = self.transfer(
            transaction_id="96274000001533058",
            account_id="96274000000097003",
            account_name="FRPDepot Inc.",
            from_account_id="96274000000097003",
            to_account_id="96274000001409019",
            amount=78146.27,
            date="2026-07-23",
            reference_number="Closing Balance From Airwallex Account",
        )
        good_accounts = {
            "96274000000097003": self.account("96274000000097003", "FRPDepot Inc.", "CAD"),
            "96274000000149537": self.account(
                "96274000000149537", "AWX_FRPDepot Inc._CAD", "CAD", "inactive"
            ),
            "96274000001409019": self.account("96274000001409019", "Chequing account (C)", "CAD"),
            "999": self.account("999", "Wrong destination", "CAD"),
        }
        cases = [
            (
                source,
                {"new_from_account_id": "96274000000149537", "new_to_account_id": "999"},
                good_accounts,
            ),
            (
                {**source, "amount": 78146.28},
                {"new_from_account_id": "96274000000149537", "new_to_account_id": "96274000001409019"},
                good_accounts,
            ),
            (
                source,
                {"new_from_account_id": "96274000000149537", "new_to_account_id": "96274000001409019"},
                {**good_accounts, "96274000000149537": self.account(
                    "96274000000149537", "Wrong AWX name", "CAD", "inactive"
                )},
            ),
        ]
        for changed_source, payload, accounts in cases:
            with self.subTest(payload=payload, amount=changed_source["amount"]), self.assertRaises(
                banking.BankingToolError
            ):
                self.stage_update(
                    source=changed_source, payload=payload, accounts=accounts,
                    statement_line="Airwallex closure transfer CAD 78,146.27",
                )

    def test_update_transport_is_exactly_one_closed_put_endpoint(self) -> None:
        captured = []

        def fake_urlopen(request, timeout):
            captured.append({
                "method": request.get_method(), "url": request.full_url,
                "payload": json.loads(request.data),
            })
            return FakeResponse({"code": 0})

        payload = banking.construct_update_transfer_payload(self.transfer(), "300", "30", "20")
        with mock.patch.object(banking, "urlopen", side_effect=fake_urlopen):
            banking.api_put_transfer_accounts_allowed(
                "token", "https://www.zohoapis.ca", "300", "99", payload
            )
        self.assertEqual(captured, [{
            "method": "PUT",
            "url": "https://www.zohoapis.ca/books/v3/banktransactions/300?organization_id=99",
            "payload": payload,
        }])
        with mock.patch.object(banking, "urlopen") as transport:
            bad_payloads = [
                {**payload, "delete": True},
                {**payload, "transaction_type": "deposit"},
                {**payload, "from_account_id": "20"},
                {**payload, "create_rule": True},
                {**payload, "account_name": "rename"},
            ]
            for bad in bad_payloads:
                with self.subTest(payload=bad), self.assertRaises(banking.BankingToolError):
                    banking.api_put_transfer_accounts_allowed(
                        "token", "https://www.zohoapis.ca", "300", "99", bad
                    )
            with self.assertRaises(banking.BankingToolError):
                banking.api_post_allowed(
                    "token", "https://www.zohoapis.ca", "update_transfer_accounts",
                    "300", "99", payload,
                )
            transport.assert_not_called()

    def test_update_commit_reserves_refetches_puts_once_and_verifies_protected_fields(self) -> None:
        original = self.transfer()
        path, _ = self.stage_update(source=original)
        readback = self.transfer(
            account_id="30", account_name="Corrected Transfer Source",
            from_account_id="30", to_account_id="20",
        )
        accounts = {
            "10": self.account("10", "Old Transfer Source"),
            "20": self.account("20", "Transfer Destination"),
            "30": self.account("30", "Corrected Transfer Source"),
        }
        self.vault["scopes"] = [banking.READ_SCOPE, banking.UPDATE_SCOPE]
        with mock.patch.object(
            banking, "get_bank_transaction", side_effect=[original, readback]
        ) as get_transaction, mock.patch.object(
            banking, "get_account", side_effect=lambda token, vault, account_id: accounts[account_id]
        ) as get_account, mock.patch.object(
            banking, "api_put_transfer_accounts_allowed", return_value={"code": 0}
        ) as put, mock.patch.object(
            banking, "api_post_allowed"
        ) as post, contextlib.redirect_stdout(io.StringIO()) as stdout:
            banking.command_commit(
                argparse.Namespace(plan=str(path), approval="APPROVED"),
                "update_transfer_accounts",
            )
        post.assert_not_called()
        put.assert_called_once()
        self.assertEqual(put.call_args.args[2:5], (
            "300", "99", json.loads(path.read_text(encoding="utf-8"))["payload"]
        ))
        self.assertEqual(get_transaction.call_count, 2)
        self.assertEqual(get_account.call_count, 4)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["action"], "update_transfer_accounts")
        lock = json.loads(banking.lock_path(result["plan_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")
        self.assertIn(
            "zoho_banking_reconciliation_committed_verified",
            [call.args[0] for call in self.append_receipt.call_args_list],
        )

    def test_update_commit_failure_after_put_is_receipted_indeterminate_and_locked(self) -> None:
        original = self.transfer()
        path, _ = self.stage_update(source=original)
        bad_readback = self.transfer(
            account_id="30", from_account_id="30", to_account_id="20", amount=126.0
        )
        accounts = {
            "10": self.account("10", "Old Transfer Source"),
            "20": self.account("20", "Transfer Destination"),
            "30": self.account("30", "Corrected Transfer Source"),
        }
        with mock.patch.object(
            banking, "get_bank_transaction", side_effect=[original, bad_readback]
        ), mock.patch.object(
            banking, "get_account", side_effect=lambda token, vault, account_id: accounts[account_id]
        ), mock.patch.object(
            banking, "api_put_transfer_accounts_allowed", return_value={"code": 0}
        ) as put, mock.patch.object(banking, "api_post_allowed") as post:
            with self.assertRaisesRegex(banking.BankingToolError, "indeterminate"):
                banking.command_commit(
                    argparse.Namespace(plan=str(path), approval="APPROVED"),
                    "update_transfer_accounts",
                )
        put.assert_called_once()
        post.assert_not_called()
        digest = json.loads(path.read_text(encoding="utf-8"))["sha256"]
        lock = json.loads(banking.lock_path(digest).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate")
        self.assertTrue(lock["no_retry"])
        self.assertIn(
            "zoho_banking_commit_failed_permanently_locked",
            [call.args[0] for call in self.append_receipt.call_args_list],
        )

    def test_update_commit_requires_update_scope_and_cli_maps_hyphen_form_canonically(self) -> None:
        path, _ = self.stage_update()
        self.vault["scopes"] = [banking.READ_SCOPE, banking.CREATE_SCOPE]
        with mock.patch.object(banking, "api_put_transfer_accounts_allowed") as put:
            with self.assertRaisesRegex(banking.BankingToolError, "aborted_before_write"):
                banking.command_commit(
                    argparse.Namespace(plan=str(path), approval="APPROVED"),
                    "update_transfer_accounts",
                )
            put.assert_not_called()
        parser = banking.build_parser()
        stage_args = parser.parse_args(["stage-update-transfer-accounts", "--input", "x.json"])
        with mock.patch.object(banking, "command_stage") as stage:
            stage_args.func(stage_args)
        self.assertEqual(stage.call_args.args[1], "update_transfer_accounts")
        commit_args = parser.parse_args([
            "commit-update-transfer-accounts", "--plan", "x.json", "--approval", "APPROVED"
        ])
        with mock.patch.object(banking, "command_commit") as commit:
            commit_args.func(commit_args)
        self.assertEqual(commit.call_args.args[1], "update_transfer_accounts")

    def test_missing_scope_or_org_mismatch_is_locked_before_post(self) -> None:
        path, _ = self.stage_match()
        self.vault["scopes"] = [banking.READ_SCOPE]
        with mock.patch.object(banking, "api_post_allowed") as post:
            with self.assertRaisesRegex(banking.BankingToolError, "aborted_before_write"):
                banking.command_commit(
                    argparse.Namespace(plan=str(path), approval="APPROVED"), "match"
                )
            post.assert_not_called()
        digest = json.loads(path.read_text(encoding="utf-8"))["sha256"]
        self.assertEqual(
            json.loads(banking.lock_path(digest).read_text(encoding="utf-8"))["status"],
            "aborted_before_write",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
