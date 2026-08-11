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
from urllib.error import HTTPError

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
    def invoice_target(transaction_id: str = "96274000001115023", **changes) -> dict:
        value = {
            "transaction_id": transaction_id,
            "account_id": "96274000000186533",
            "account_name": "Structural Composites Technologies Ltd",
            "date": "2026-06-02",
            "amount": 4101.30,
            "currency_code": "CAD",
            "status": "overdue",
            "payee": "Structural Composites Technologies Ltd",
            "reference_number": "SO-00041",
            "transaction_type": "invoice",
            "description": "Invoice INV-000040",
            "transaction_number": "INV-000040",
            "match_source_transaction_id": "96274000001534055",
            "candidate_is_best_match": True,
            "candidate_is_exact_match": False,
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

    def test_bank_get_falls_back_to_exact_ui_feed_only_on_api_404(self) -> None:
        source = self.source("96274000001534055", amount=4101.30)

        def api_404(*_args):
            cause = HTTPError("https://www.zohoapis.ca", 404, "Not Found", None, None)
            raise banking.zoho_tool.ZohoError("Transaction does not exist") from cause

        with mock.patch.object(
            banking.zoho_tool, "api_get", side_effect=api_404
        ), mock.patch.object(
            banking, "get_uncategorized_ui_transaction", return_value=source
        ) as ui_get:
            result = banking.get_bank_transaction(
                "token", self.vault, "96274000001534055", "regular"
            )
        self.assertEqual(result, source)
        ui_get.assert_called_once_with(self.vault, "96274000001534055")

        def api_403(*_args):
            cause = HTTPError("https://www.zohoapis.ca", 403, "Forbidden", None, None)
            raise banking.zoho_tool.ZohoError("Forbidden") from cause

        with mock.patch.object(
            banking.zoho_tool, "api_get", side_effect=api_403
        ), mock.patch.object(banking, "get_uncategorized_ui_transaction") as ui_get:
            with self.assertRaises(banking.zoho_tool.ZohoError):
                banking.get_bank_transaction(
                    "token", self.vault, "96274000001534055", "regular"
                )
        ui_get.assert_not_called()

    def test_uncategorized_ui_reader_requires_one_exact_row_and_projects_fields(self) -> None:
        row = self.source("96274000001534055", amount=4101.30)
        row["unexpected_server_field"] = "must not enter the immutable plan"
        response = {
            "transactions": [row],
            "page_context": {"page": 1, "has_more_page": False},
        }
        with mock.patch.object(banking, "books_ui_get", return_value=response):
            result = banking.get_uncategorized_ui_transaction(
                self.vault, "96274000001534055"
            )
        self.assertNotIn("unexpected_server_field", result)
        self.assertEqual(result["transaction_id"], "96274000001534055")
        with mock.patch.object(banking, "books_ui_get", return_value={
            "transactions": [row, dict(row)],
            "page_context": {"page": 1, "has_more_page": False},
        }), self.assertRaisesRegex(banking.BankingToolError, "duplicate transaction IDs"):
            banking.get_uncategorized_ui_transaction(self.vault, "96274000001534055")

    def test_invoice_is_allowlisted_for_match_but_not_categorize(self) -> None:
        self.assertEqual(
            banking.validate_match_targets([
                {"transaction_id": "96274000001115023", "transaction_type": "invoice"}
            ])[0]["transaction_type"],
            "invoice",
        )
        with self.assertRaisesRegex(banking.BankingToolError, "generic banking"):
            banking.validate_categorize_input_payload({
                "from_account_id": "10",
                "to_account_id": "30",
                "transaction_type": "invoice",
            })

    def test_invoice_match_target_joins_best_candidate_to_open_live_invoice(self) -> None:
        candidate = {
            "transaction_id": "96274000001115023",
            "date": "2026-06-02",
            "transaction_type": "invoice",
            "reference_number": "SO-00041",
            "amount": 4101.30,
            "transaction_number": "INV-000040",
            "contact_name": "Structural Composites Technologies Ltd",
            "is_best_match": True,
            "is_exact_match": False,
        }
        invoice = {
            "invoice_id": "96274000001115023",
            "invoice_number": "INV-000040",
            "customer_id": "96274000000186533",
            "customer_name": "Structural Composites Technologies Ltd",
            "date": "2026-06-02",
            "currency_code": "CAD",
            "balance": 4101.30,
            "status": "overdue",
            "reference_number": "SO-00041",
        }
        with mock.patch.object(
            banking, "books_ui_get", return_value={"matching_transactions": [candidate]}
        ), mock.patch.object(
            banking.zoho_tool, "api_get", return_value={"invoice": invoice}
        ):
            result = banking.get_match_target(
                "token", self.vault, "96274000001534055",
                "96274000001115023", "invoice",
            )
        self.assertEqual(result, self.invoice_target())
        banking.transaction_before(result, "96274000001115023")

        candidate["is_best_match"] = False
        with mock.patch.object(
            banking, "books_ui_get", return_value={"matching_transactions": [candidate]}
        ), self.assertRaisesRegex(banking.BankingToolError, "not Zoho's current best match"):
            banking.get_match_target(
                "token", self.vault, "96274000001534055",
                "96274000001115023", "invoice",
            )

    def test_invoice_match_stages_and_refetches_protected_invoice_without_post(self) -> None:
        source_id = "96274000001534055"
        target_id = "96274000001115023"
        source = self.source(
            source_id,
            account_id="96274000001409019",
            account_name="Chequing account (C)",
            amount=4101.30,
            transaction_type="uncategorized",
        )
        target = self.invoice_target()
        input_value = {
            "source_transaction_id": source_id,
            "transactions_to_be_matched": [
                {"transaction_id": target_id, "transaction_type": "invoice"}
            ],
            "sources": self.sources("Live Desjardins CAD imported-feed line"),
            "evidence": self.evidence(),
        }
        with mock.patch.object(
            banking, "get_bank_transaction", return_value=source
        ), mock.patch.object(
            banking, "get_match_target", return_value=target
        ) as get_target, mock.patch.object(
            banking, "api_post_allowed"
        ) as post, mock.patch.object(
            banking, "api_put_transfer_accounts_allowed"
        ) as put, contextlib.redirect_stdout(io.StringIO()) as stdout:
            banking.command_stage(
                argparse.Namespace(input=str(self.input_path(input_value))), "match"
            )
        post.assert_not_called()
        put.assert_not_called()
        get_target.assert_called_once_with(
            "token", self.vault, source_id, target_id, "invoice"
        )
        plan_path = Path(json.loads(stdout.getvalue())["plan"])
        plan = banking.load_plan(plan_path, "match")
        self.assertEqual(plan["payload"]["transactions_to_be_matched"], [
            {"transaction_id": target_id, "transaction_type": "invoice"}
        ])
        snapshot = plan["target_snapshots"][0]
        with mock.patch.object(
            banking, "get_match_target", return_value=target
        ) as get_target, mock.patch.object(banking, "get_bank_transaction") as bank_get:
            refreshed = banking.refetch_snapshot("token", self.vault, snapshot)
        bank_get.assert_not_called()
        get_target.assert_called_once_with(
            "token", self.vault, source_id, target_id, "invoice"
        )
        self.assertEqual(refreshed["current_state"], target)

    def test_invoice_match_readback_requires_exact_payment_allocation_and_paid_invoice(self) -> None:
        source_id = "96274000001534055"
        target_id = "96274000001115023"
        source = self.source(
            source_id,
            account_id="96274000001409019",
            account_name="Chequing account (C)",
            amount=4101.30,
            date="2026-08-07",
            transaction_type="uncategorized",
            description="Direct deposit /Structural Composite Technolog",
        )
        target = self.invoice_target()
        plan = {
            "source_snapshot": banking.transaction_snapshot(
                source, source_id, "source", "regular"
            ),
            "target_snapshots": [banking.transaction_snapshot(
                target, target_id, "match_target", "regular"
            )],
            "payload": {"transactions_to_be_matched": [
                {"transaction_id": target_id, "transaction_type": "invoice"}
            ]},
        }
        payment_summary = {
            "payment_id": "96274000001542003",
            "date": "2026-08-07",
            "amount": 4101.30,
            "account_id": "96274000001409019",
        }
        payment = {
            **payment_summary,
            "unused_amount": 0.0,
            "description": "Direct deposit /Structural Composite Technolog",
            "invoices": [{
                "invoice_id": target_id,
                "amount_applied": 4101.30,
                "balance": 0.0,
            }],
        }
        invoice = {
            "invoice_id": target_id,
            "invoice_number": "INV-000040",
            "customer_id": "96274000000186533",
            "customer_name": "Structural Composites Technologies Ltd",
            "date": "2026-06-02",
            "currency_code": "CAD",
            "reference_number": "SO-00041",
            "status": "paid",
            "balance": 0.0,
        }

        def api_get(_token, _domain, path):
            if path.startswith("/books/v3/customerpayments?"):
                return {"customerpayments": [payment_summary], "page_context": {"has_more_page": False}}
            if path.startswith("/books/v3/customerpayments/96274000001542003?"):
                return {"payment": payment}
            if path.startswith(f"/books/v3/invoices/{target_id}?"):
                return {"invoice": invoice}
            self.fail(f"Unexpected GET: {path}")

        with mock.patch.object(
            banking, "list_uncategorized_ui_transactions", return_value=[]
        ), mock.patch.object(
            banking.zoho_tool, "api_get", side_effect=api_get
        ):
            result = banking.verify_invoice_match_readback(plan, "token", self.vault)
        self.assertEqual(result, {
            "status": "matched", "payment_id": "96274000001542003"
        })

        with mock.patch.object(
            banking, "list_uncategorized_ui_transactions", return_value=[source]
        ), mock.patch.object(banking.zoho_tool, "api_get") as get:
            with self.assertRaisesRegex(banking.BankingToolError, "still shows"):
                banking.verify_invoice_match_readback(plan, "token", self.vault)
        get.assert_not_called()

        payment["invoices"][0]["invoice_id"] = "96274000009999999"
        with mock.patch.object(
            banking, "list_uncategorized_ui_transactions", return_value=[]
        ), mock.patch.object(
            banking.zoho_tool, "api_get", side_effect=api_get
        ), self.assertRaisesRegex(banking.BankingToolError, "approved invoice allocations"):
            banking.verify_invoice_match_readback(plan, "token", self.vault)

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
        # A categorize CONSUMES the imported line and produces a DIFFERENT record,
        # so verification reads what was created, never the ID that was staged.
        produced = {
            "transaction_id": "101",
            "date": "2026-08-07",
            "transaction_type": "owner_drawings",
            "status": "categorized",
            "amount": 125.00,
            "from_account_id": "10",
            "to_account_id": "30",
            "description": "Reviewed bank expense",
            "currency_code": "CAD",
        }
        accounts = {"10": self.account("10", "Desjardins Operating"), "30": self.account("30", "Reviewed Category")}
        with mock.patch.object(
            banking, "get_bank_transaction", side_effect=[original]
        ) as get_transaction, mock.patch.object(
            banking, "get_categorized_result", return_value=produced
        ) as get_result, mock.patch.object(
            banking, "get_account", side_effect=lambda token, vault, account_id: accounts[account_id]
        ) as get_account, mock.patch.object(
            banking, "api_post_allowed",
            return_value={"code": 0, "banktransaction": produced},
        ) as post, contextlib.redirect_stdout(io.StringIO()) as stdout:
            banking.command_commit(
                argparse.Namespace(plan=str(path), approval="APPROVED"), "categorize"
            )
        self.assertEqual(get_transaction.call_count, 1)
        get_result.assert_called_once()
        self.assertEqual(get_result.call_args.args[2], "101")
        self.assertEqual(get_account.call_count, 2)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[2], "categorize")
        self.assertEqual(post.call_args.args[6 - 1], json.loads(path.read_text(encoding="utf-8"))["payload"])
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertTrue(result["replay_locked"])
        lock = json.loads(banking.lock_path(result["plan_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")
        # The lock names the record that now exists, which is what a later
        # reconcile has to look for.
        self.assertEqual(lock["resulting_transaction_id"], "101")
        receipt_actions = [call.args[0] for call in self.append_receipt.call_args_list]
        self.assertIn("zoho_banking_reconciliation_committed_verified", receipt_actions)
        self.save_vault.assert_called_once_with(self.vault)

    def test_categorize_refuses_an_inactive_account_and_stages_nothing(self) -> None:
        """Zoho answers a categorize onto an inactive account with 400 code 11015.

        The account GET already carries that answer and the plan lock is
        single-use, so the refusal belongs before anything is staged. 2026-08-11:
        from_account 96274000000149257 was staged with is_active False recorded in
        its own snapshot, and the burnt plan could never be retried.
        """
        for role, account_id in (("from_account", "10"), ("to_account", "30")):
            with self.subTest(role=role):
                accounts = {
                    "10": self.account("10", "Desjardins Operating"),
                    "30": self.account("30", "Reviewed Category"),
                }
                accounts[account_id] = self.account(
                    account_id, accounts[account_id]["account_name"], status="inactive"
                )
                staged_before = set(self.plan_dir.glob("*.json"))
                with self.assertRaisesRegex(
                    banking.BankingToolError, f"Inactive: {role} {account_id}"
                ):
                    self.stage("categorize", {
                        "source_transaction_id": "100",
                        "payload": {
                            "from_account_id": "10",
                            "to_account_id": "30",
                            "transaction_type": "owner_drawings",
                            "description": "Reviewed bank expense",
                        },
                        "sources": self.sources(),
                        "evidence": self.evidence(),
                    }, {"100": self.source()}, accounts)
                self.assertEqual(set(self.plan_dir.glob("*.json")), staged_before)

    def test_indeterminate_lock_records_that_zoho_accepted_the_write(self) -> None:
        """An indeterminate Zoho ACCEPTED needs a different reconcile from one it
        rejected, and the ID it created is what that reconcile has to look for."""
        original = self.source()
        path, _ = self.stage_categorize(source=original)
        accounts = {"10": self.account("10", "Desjardins Operating"), "30": self.account("30", "Reviewed Category")}
        with mock.patch.object(
            banking, "get_bank_transaction", side_effect=[original]
        ), mock.patch.object(
            banking, "get_categorized_result",
            return_value={"transaction_id": "101", "transaction_type": "deposit"},
        ), mock.patch.object(
            banking, "get_account", side_effect=lambda token, vault, account_id: accounts[account_id]
        ), mock.patch.object(
            banking, "api_post_allowed",
            return_value={"code": 0, "banktransaction": {"transaction_id": "101"}},
        ), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(banking.BankingToolError, "indeterminate"):
                banking.command_commit(
                    argparse.Namespace(plan=str(path), approval="APPROVED"), "categorize"
                )
        digest = json.loads(path.read_text(encoding="utf-8"))["sha256"]
        lock = json.loads(banking.lock_path(digest).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "indeterminate")
        self.assertTrue(lock["zoho_accepted_the_write"])
        self.assertEqual(lock["resulting_transaction_id"], "101")
        self.assertTrue(lock["no_retry"])

    def test_categorized_result_id_reads_one_ID_and_refuses_ambiguity(self) -> None:
        for label, response in (
            ("container", {"banktransaction": {"transaction_id": "101"}}),
            ("top level", {"transaction_id": "101"}),
            ("agreeing", {"transaction_id": "101", "banktransaction": {"transaction_id": "101"}}),
        ):
            with self.subTest(response=label):
                self.assertEqual(banking.categorized_result_id(response), "101")
        for label, response in (
            ("no ID at all", {"code": 0}),
            ("two different IDs", {
                "transaction_id": "101", "banktransaction": {"transaction_id": "102"},
            }),
            ("not an ID", {"banktransaction": {"transaction_id": "not-an-id"}}),
            ("zero", {"banktransaction": {"transaction_id": "0"}}),
            ("not an object", "not a dict"),
        ):
            with self.subTest(response=label):
                self.assertEqual(banking.categorized_result_id(response), "")

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
                if row["currency"] == "USD":
                    self.assertNotIn("currency_id", plan["payload"])
                    self.assertNotIn("exchange_rate", plan["payload"])
                    readback = {
                        **source,
                        "from_account_id": row["new_from"],
                        "to_account_id": row["to"],
                        "currency_code": "USD",
                        "currency_id": "USD-CURRENCY-ID",
                        "exchange_rate": 1.0,
                    }
                    verified = banking.verify_readback(plan, readback)
                    self.assertEqual(verified["currency"], "USD")
                    with self.assertRaisesRegex(
                        banking.BankingToolError, "did not derive currency USD"
                    ):
                        banking.verify_readback(plan, {**readback, "currency_code": "CAD"})
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

    # ------------------------------------------------------------------
    # The one hard-pinned Airwallex USD recovery of the statement line Rachad
    # uncategorized himself on 2026-08-10. Nothing here may relax any other case.
    # ------------------------------------------------------------------
    SPEC = banking.AIRWALLEX_USD_RECOVERY

    @classmethod
    def recovery_source(cls, **changes) -> dict:
        value = {
            "transaction_id": cls.SPEC["source_transaction_id"],
            "date": "2026-07-23",
            "transaction_type": "uncategorized",
            "status": "uncategorized",
            "amount": 21642.71,
            "bank_charges": 0,
            "gross_amount": 21642.71,
            "source": "bank_feeds",
            "account_id": "96274000001409012",
            "account_name": "USD Desjardins corporate build-up account",
            "account_type": "bank",
            "payee": "",
            "description": "Funds transfer received /FRPDepot Inc. /",
            "currency_id": "96274000000000081",
            "currency_code": "USD",
            "debit_or_credit": "debit",
            "reference_number": "",
        }
        value.update(changes)
        return value

    @classmethod
    def recovery_accounts(cls) -> dict:
        return {
            "96274000000149257": {
                "account_id": "96274000000149257",
                "account_name": "AWX_FRPDepot Inc._USD",
                "currency_code": "USD",
                "status": "active",
            },
            "96274000001409012": {
                "account_id": "96274000001409012",
                "account_name": "USD Desjardins corporate build-up account",
                "currency_code": "USD",
                "status": "active",
            },
        }

    @classmethod
    def recovery_payload(cls, **changes) -> dict:
        value = {
            "from_account_id": "96274000000149257",
            "to_account_id": "96274000001409012",
            "transaction_type": "transfer_fund",
            "reference_number": "Closing Balance From Airwallex Account",
            "description": "Funds transfer received /FRPDepot Inc. /",
            "currency_id": "96274000000000081",
        }
        value.update(changes)
        return value

    RESULT_ID = "96274000001558075"

    @classmethod
    def recovery_result(cls, **changes) -> dict:
        """The record a successful categorize PRODUCES.

        Live on 2026-08-11 the imported line 96274000001423074 was consumed and
        transfer 96274000001558075 appeared in its place, so the result carries
        its own ID and the staged source ID stops resolving.
        """
        value = {
            "transaction_id": cls.RESULT_ID,
            "date": "2026-07-23",
            "transaction_type": "transfer_fund",
            "status": "categorized",
            "amount": 21642.71,
            "from_account_id": "96274000000149257",
            "to_account_id": "96274000001409012",
            "reference_number": "Closing Balance From Airwallex Account",
            "description": "Funds transfer received /FRPDepot Inc. /",
            "currency_id": "96274000000000081",
            "currency_code": "USD",
        }
        value.update(changes)
        return value

    @classmethod
    def recovery_post_response(cls, **changes) -> dict:
        return {"code": 0, "banktransaction": cls.recovery_result(**changes)}

    def recovery_input(self, **changes) -> dict:
        value = {
            "mode": banking.AIRWALLEX_USD_RECOVERY_MODE,
            "historical_plan": str(self.plan_dir / self.SPEC["historical_plan_name"]),
            "historical_plan_sha256": self.SPEC["historical_plan_sha256"],
            "superseded_transfer_transaction_id": self.SPEC["superseded_transfer_transaction_id"],
        }
        value.update(changes)
        return value

    def install_recovery_evidence(
        self, *, plan_mutate=None, lock_mutate=None, install_plan=True, install_lock=True
    ) -> Path:
        """Copy the REAL immutable historical plan and its lock into the patched plan dir."""
        real = (
            banking.ROOT / "Dado" / "20_Working" / "zoho_banking_plans"
            / self.SPEC["historical_plan_name"]
        )
        self.assertTrue(real.is_file(), f"pinned immutable recovery evidence is missing: {real}")
        data = json.loads(real.read_text(encoding="utf-8"))
        self.assertEqual(data["sha256"], self.SPEC["historical_plan_sha256"])
        if plan_mutate is not None:
            plan_mutate(data)
        target = self.plan_dir / self.SPEC["historical_plan_name"]
        if install_plan:
            target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        if install_lock:
            lock = banking.lock_path(self.SPEC["historical_plan_sha256"])
            lock.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "plan_sha256": self.SPEC["historical_plan_sha256"],
                "action": "update_transfer_accounts",
                "status": "indeterminate",
                "updated_utc": "2026-08-08T03:15:28.399772+00:00",
                "reason": "HTTP 400 17004 same foreign currency",
                "no_retry": True,
            }
            if lock_mutate is not None:
                lock_mutate(state)
            lock.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        return target

    def recovery_reader(self, source: dict, superseded: dict | None = None):
        def get_transaction(token, vault, transaction_id, mode):
            self.assertEqual(mode, "regular")
            if transaction_id == source["transaction_id"]:
                return source
            if transaction_id == self.SPEC["superseded_transfer_transaction_id"]:
                if superseded is not None:
                    return superseded
                raise banking.BankingRecordAbsent(
                    "Zoho Books imported feed did not return exactly one transaction "
                    + transaction_id + "."
                )
            raise AssertionError(f"unexpected live read of {transaction_id}")

        return get_transaction

    def recovery_stage_call(
        self, *, source=None, payload=None, accounts=None, recovery=None,
        statement_line=None, superseded=None, drop_recovery=False, extra_evidence=None,
        action="categorize",
    ):
        source = self.recovery_source() if source is None else source
        accounts = self.recovery_accounts() if accounts is None else accounts
        payload = self.recovery_payload() if payload is None else payload
        evidence = self.evidence()
        if extra_evidence:
            evidence.update(extra_evidence)
        input_value = {
            "source_transaction_id": source["transaction_id"],
            "payload": payload,
            "sources": self.sources(
                statement_line
                or "2026-07-23 Airwallex USD 21,642.71 closure statement line, uncategorized by Rachad"
            ),
            "evidence": evidence,
        }
        if not drop_recovery:
            input_value["recovery"] = self.recovery_input() if recovery is None else recovery
        with mock.patch.object(
            banking, "get_bank_transaction", side_effect=self.recovery_reader(source, superseded)
        ), mock.patch.object(
            banking, "get_account",
            side_effect=lambda token, vault, account_id: accounts.get(
                account_id, self.account(account_id, currency="USD")
            ),
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

    def assert_recovery_refused(self, message: str | None = None, **kwargs) -> None:
        with mock.patch.object(banking, "api_post_allowed") as post, mock.patch.object(
            banking, "api_put_transfer_accounts_allowed"
        ) as put:
            if message:
                with self.assertRaisesRegex(banking.BankingToolError, message):
                    self.recovery_stage_call(**kwargs)
            else:
                with self.assertRaises(banking.BankingToolError):
                    self.recovery_stage_call(**kwargs)
            post.assert_not_called()
            put.assert_not_called()

    def stage_recovery(self, **kwargs):
        self.install_recovery_evidence()
        return self.recovery_stage_call(**kwargs)

    def test_recovery_stages_the_exact_uncategorized_usd_line_and_writes_nothing(self) -> None:
        path, result = self.stage_recovery()
        self.assertEqual(result["status"], "STAGED_NOT_COMMITTED")
        self.assertEqual(result["approval"], "APPROVED")
        plan = json.loads(path.read_text(encoding="utf-8"))
        saved = plan.pop("sha256")
        self.assertEqual(saved, banking.digest_for(plan))
        self.assertEqual(plan["action"], "categorize")
        self.assertEqual(plan["payload"], {
            "from_account_id": "96274000000149257",
            "to_account_id": "96274000001409012",
            "transaction_type": "transfer_fund",
            "reference_number": "Closing Balance From Airwallex Account",
            "description": "Funds transfer received /FRPDepot Inc. /",
            "currency_id": "96274000000000081",
            "amount": 21642.71,
            "date": "2026-07-23",
        })
        evidence = plan["evidence"]
        self.assertTrue(evidence["airwallex_guarded"])
        self.assertEqual(evidence["airwallex_outgoing_transaction_id"], "")
        self.assertIsNone(evidence["airwallex_verification"])
        recovery = evidence["airwallex_recovery"]
        self.assertEqual(recovery["mode"], banking.AIRWALLEX_USD_RECOVERY_MODE)
        self.assertEqual(recovery["source_transaction_id"], "96274000001423074")
        self.assertEqual(recovery["superseded_transfer_transaction_id"], "96274000001535012")
        self.assertTrue(recovery["superseded_transfer_verified_absent"])
        self.assertFalse(recovery["live_outgoing_counterpart_required"])
        self.assertEqual(recovery["historical_plan_sha256"], self.SPEC["historical_plan_sha256"])
        self.assertEqual(recovery["historical_plan_lock_status"], "indeterminate")

        summary = plan["human_summary"]["recovery"]
        self.assertIn("uncategorized", summary["statement"].casefold())
        self.assertIn("recovery", summary["statement"].casefold())
        self.assertEqual(summary["transaction_type"], "transfer_fund")
        self.assertEqual(summary["from_account"], {
            "account_id": "96274000000149257", "name": "AWX_FRPDepot Inc._USD",
            "currency": "USD", "status": "active",
        })
        self.assertEqual(summary["to_account"], {
            "account_id": "96274000001409012",
            "name": "USD Desjardins corporate build-up account",
            "currency": "USD", "status": "active",
        })
        self.assertEqual(summary["amount"], "21642.71")
        self.assertEqual(summary["currency"], "USD")
        self.assertEqual(summary["currency_id"], "96274000000000081")
        self.assertEqual(summary["date"], "2026-07-23")
        self.assertEqual(summary["reference_number"], "Closing Balance From Airwallex Account")
        self.assertEqual(summary["description"], "Funds transfer received /FRPDepot Inc. /")
        self.assertEqual(summary["emails_sent"], 0)
        self.assertFalse(summary["write_performed_yet"])
        self.assertIn("transfer_fund only", summary["revenue_or_income_classification"])
        self.assertIn("/uncategorized/96274000001423074/categorize", summary["write"])
        self.assertEqual(
            [call.args[0] for call in self.append_receipt.call_args_list],
            ["zoho_banking_categorize_plan_staged_not_committed"],
        )
        self.assertFalse(banking.lock_path(saved).exists())

    def test_recovery_refuses_one_changed_digit_in_any_pinned_fact(self) -> None:
        self.install_recovery_evidence()
        cases = {
            "source id": {"source": self.recovery_source(transaction_id="96274000001423075")},
            "amount": {"source": self.recovery_source(amount=21642.72)},
            "gross amount": {"source": self.recovery_source(gross_amount=21642.72)},
            "bank charges": {"source": self.recovery_source(bank_charges=0.01)},
            "date": {"source": self.recovery_source(date="2026-07-24")},
            "currency code": {"source": self.recovery_source(currency_code="CAD")},
            "currency id": {"source": self.recovery_source(currency_id="96274000000000082")},
            "description": {"source": self.recovery_source(
                description="Funds transfer received /FRPDepot Inc./"
            )},
            "status": {"source": self.recovery_source(status="categorized")},
            "statement account": {"source": self.recovery_source(account_id="96274000001409013")},
            "payload source account": {"payload": self.recovery_payload(
                from_account_id="96274000000149258"
            )},
            "payload destination account": {"payload": self.recovery_payload(
                to_account_id="96274000001409013"
            )},
            "payload currency id": {"payload": self.recovery_payload(
                currency_id="96274000000000082"
            )},
            "payload reference": {"payload": self.recovery_payload(
                reference_number="Closing Balance From Airwallex Accounts"
            )},
            "payload description": {"payload": self.recovery_payload(
                description="Funds transfer received /FRPDepot Inc /"
            )},
            "payload extra field": {"payload": self.recovery_payload(exchange_rate=1.0)},
            "historical digest": {"recovery": self.recovery_input(
                historical_plan_sha256=self.SPEC["historical_plan_sha256"][:-1] + "9"
            )},
            "superseded id": {"recovery": self.recovery_input(
                superseded_transfer_transaction_id="96274000001535013"
            )},
            "recovery mode": {"recovery": self.recovery_input(mode="airwallex_usd_closure")},
        }
        for label, override in cases.items():
            with self.subTest(changed=label):
                self.assert_recovery_refused(**override)

        renamed = self.recovery_accounts()
        renamed["96274000000149257"]["account_name"] = "AWX_FRPDepot Inc._CAD"
        self.assert_recovery_refused("new source account name", accounts=renamed)

        cad_accounts = self.recovery_accounts()
        cad_accounts["96274000001409012"]["currency_code"] = "CAD"
        self.assert_recovery_refused("destination account currency", accounts=cad_accounts)

        # Either account inactive is refused before anything is staged, because
        # Zoho answers a categorize onto one with HTTP 400 code 11015 and the
        # burnt plan lock is not recoverable - 2026-08-11.
        for role, account_id in (
            ("to_account", "96274000001409012"),
            ("from_account", "96274000000149257"),
        ):
            inactive = self.recovery_accounts()
            inactive[account_id]["status"] = "inactive"
            self.assert_recovery_refused(
                f"Inactive: {role} {account_id}", accounts=inactive
            )

    def test_recovery_refuses_missing_tampered_or_unlocked_historical_plan(self) -> None:
        self.install_recovery_evidence(install_plan=False)
        self.assert_recovery_refused("banking plan folder")

        # A real, readable, correctly-digested plan under any other name is still refused:
        # the evidence is pinned by NAME as well as by digest.
        installed = self.install_recovery_evidence()
        decoy = self.plan_dir / "20260808T031444Z_other_plan.json"
        decoy.write_text(installed.read_text(encoding="utf-8"), encoding="utf-8")
        self.assert_recovery_refused(
            "pinned historical plan", recovery=self.recovery_input(historical_plan=str(decoy))
        )

        # One changed byte anywhere inside the immutable evidence breaks its own digest.
        for label, mutate in (
            ("approved source account", lambda plan: plan["payload"].update(
                {"from_account_id": "96274000000149258"}
            )),
            ("imported line ID", lambda plan: plan["source_snapshot"]["current_state"][
                "imported_transactions"
            ][0].update({"imported_transaction_id": "96274000001423075"})),
            ("historical amount", lambda plan: plan["payload"].update({"amount": 21642.72})),
        ):
            with self.subTest(tampered=label):
                self.install_recovery_evidence(plan_mutate=mutate)
                self.assert_recovery_refused("digest check")

        self.install_recovery_evidence(install_lock=False)
        banking.lock_path(self.SPEC["historical_plan_sha256"]).unlink(missing_ok=True)
        self.assert_recovery_refused("no commit lock")

        for label, mutate in (
            ("retryable", lambda state: state.update({"no_retry": False})),
            ("committed", lambda state: state.update({"status": "committed_verified"})),
            ("foreign digest", lambda state: state.update({"plan_sha256": "0" * 64})),
        ):
            with self.subTest(lock=label):
                self.install_recovery_evidence(lock_mutate=mutate)
                self.assert_recovery_refused("permanently locked")

    def test_recovery_refuses_when_the_superseded_transfer_reappears(self) -> None:
        self.install_recovery_evidence()
        revived = self.transfer(
            transaction_id="96274000001535012",
            from_account_id="96274000000097003",
            to_account_id="96274000001409012",
            amount=21642.71, date="2026-07-23", currency_code="USD",
        )
        self.assert_recovery_refused("is live again", superseded=revived)

    def test_every_other_airwallex_case_still_requires_live_outgoing_evidence(self) -> None:
        self.install_recovery_evidence()
        # The other commissioned Airwallex amount cannot borrow this recovery.
        self.assert_recovery_refused(
            "reachable only for statement line",
            source=self.recovery_source(
                transaction_id="96274000001423099", amount=78146.27, gross_amount=78146.27,
                currency_code="CAD", currency_id="96274000000000087",
            ),
        )
        # Same line, recovery withheld: the ordinary guard is untouched.
        self.assert_recovery_refused(
            "outgoing transaction ID", drop_recovery=True
        )
        # A recovery request can never also carry an outgoing counterpart.
        self.assert_recovery_refused(
            "cannot also carry one",
            extra_evidence={"airwallex_outgoing_transaction_id": "96274000001535012"},
        )
        # A non-Airwallex line cannot reach the recovery path at all.
        self.assert_recovery_refused(
            "only for the guarded Airwallex line",
            source=self.recovery_source(
                transaction_id="96274000001423074", amount=125.0, gross_amount=125.0,
            ),
            statement_line="Ordinary Desjardins deposit",
        )
        # The optional key is accepted for categorize alone.
        for action in ("match", "unmatch", "uncategorize"):
            with self.subTest(action=action):
                self.assert_recovery_refused("unsupported: payload, recovery", action=action)

    def test_recovery_can_never_classify_income_or_revenue(self) -> None:
        self.install_recovery_evidence()
        for kind in ("other_income", "interest_income", "sales_without_invoices", "deposit"):
            with self.subTest(transaction_type=kind):
                self.assert_recovery_refused(
                    "transfer_fund", payload=self.recovery_payload(transaction_type=kind)
                )

    def test_recovery_commit_checks_exact_approval_before_any_network_or_lock(self) -> None:
        path, _ = self.stage_recovery()
        saved = json.loads(path.read_text(encoding="utf-8"))["sha256"]
        for approval in ("approved", "Approved", " APPROVED", "APPROVED ", "APPROVED NOW", ""):
            with self.subTest(approval=approval):
                self.load_vault.reset_mock()
                with mock.patch.object(banking, "api_post_allowed") as post:
                    with self.assertRaisesRegex(banking.BankingToolError, "case-sensitive"):
                        banking.command_commit(
                            argparse.Namespace(plan=str(path), approval=approval), "categorize"
                        )
                    post.assert_not_called()
                self.load_vault.assert_not_called()
                self.assertFalse(banking.lock_path(saved).exists())

    def commit_recovery(
        self, path: Path, *, reads, accounts=None, post=None, superseded=None, posted=None,
        result=None,
    ):
        accounts = self.recovery_accounts() if accounts is None else accounts
        posted = posted or mock.MagicMock(
            **(post or {"return_value": self.recovery_post_response()})
        )
        queue = list(reads)
        result_row = self.recovery_result() if result is None else result

        def get_transaction(token, vault, transaction_id, mode):
            if transaction_id == self.SPEC["superseded_transfer_transaction_id"]:
                if superseded is not None:
                    return superseded
                raise banking.BankingRecordAbsent("absent")
            return queue.pop(0)

        def get_result(token, vault, transaction_id):
            if isinstance(result_row, Exception):
                raise result_row
            return result_row

        with mock.patch.object(
            banking, "get_bank_transaction", side_effect=get_transaction
        ), mock.patch.object(
            banking, "get_categorized_result", side_effect=get_result
        ), mock.patch.object(
            banking, "get_account",
            side_effect=lambda token, vault, account_id: accounts[account_id],
        ), mock.patch.object(
            banking, "api_post_allowed", posted
        ), contextlib.redirect_stdout(io.StringIO()) as stdout:
            banking.command_commit(
                argparse.Namespace(plan=str(path), approval="APPROVED"), "categorize"
            )
        return posted, stdout.getvalue()

    def test_recovery_commit_issues_exactly_one_post_and_verifies_the_result(self) -> None:
        source = self.recovery_source()
        path, _ = self.stage_recovery(source=source)
        readback = self.recovery_source(
            status="categorized", transaction_type="transfer_fund",
            from_account_id="96274000000149257", to_account_id="96274000001409012",
        )
        posted, output = self.commit_recovery(path, reads=[source, readback])
        posted.assert_called_once()
        self.assertEqual(posted.call_args.args[2], "categorize")
        self.assertEqual(posted.call_args.args[3], "96274000001423074")
        self.assertEqual(
            posted.call_args.args[5], json.loads(path.read_text(encoding="utf-8"))["payload"]
        )
        result = json.loads(output)
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertEqual(result["source_transaction_id"], "96274000001423074")
        self.assertTrue(result["replay_locked"])
        lock = json.loads(banking.lock_path(result["plan_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "committed_verified")
        self.assertTrue(lock["no_retry"])
        self.assertIn(
            "zoho_banking_reconciliation_committed_verified",
            [call.args[0] for call in self.append_receipt.call_args_list],
        )
        # Replay is impossible even with a correct approval.
        with mock.patch.object(banking, "api_post_allowed") as retry:
            with self.assertRaisesRegex(banking.BankingToolError, "cannot be replayed"):
                banking.command_commit(
                    argparse.Namespace(plan=str(path), approval="APPROVED"), "categorize"
                )
            retry.assert_not_called()

    def test_recovery_commit_fails_closed_on_every_drift_error_and_readback_mismatch(self) -> None:
        source = self.recovery_source()
        readback = self.recovery_source(
            status="categorized", transaction_type="transfer_fund",
            from_account_id="96274000000149257", to_account_id="96274000001409012",
        )
        drifted_accounts = self.recovery_accounts()
        drifted_accounts["96274000000149257"]["status"] = "inactive"

        cases = [
            (
                "source line drift", "aborted_before_write",
                {"reads": [self.recovery_source(amount=21642.72), readback]},
            ),
            (
                "account drift", "aborted_before_write",
                {"reads": [source, readback], "accounts": drifted_accounts},
            ),
            (
                "superseded transfer returned", "aborted_before_write",
                {"reads": [source, readback], "superseded": self.transfer(
                    transaction_id="96274000001535012", amount=21642.71,
                    date="2026-07-23", currency_code="USD",
                )},
            ),
            (
                "api error", "indeterminate",
                {"reads": [source, readback], "post": {
                    "side_effect": banking.BankingToolError("Zoho banking categorize failed")
                }},
            ),
            (
                "result still uncategorized", "indeterminate",
                {"reads": [source, readback],
                 "result": self.recovery_result(transaction_type="uncategorized")},
            ),
            (
                "result wrong type", "indeterminate",
                {"reads": [source, readback],
                 "result": self.recovery_result(transaction_type="deposit")},
            ),
            (
                "result amount drifted", "indeterminate",
                {"reads": [source, readback],
                 "result": self.recovery_result(amount=21642.72)},
            ),
            (
                "result lost the approved account", "indeterminate",
                {"reads": [source, readback],
                 "result": self.recovery_result(from_account_id="96274000000000999")},
            ),
            (
                "result reference not preserved", "indeterminate",
                {"reads": [source, readback],
                 "result": self.recovery_result(reference_number="something else")},
            ),
            (
                "response carried no resulting ID", "indeterminate",
                {"reads": [source, readback], "post": {"return_value": {"code": 0}}},
            ),
            (
                "response carried two different IDs", "indeterminate",
                {"reads": [source, readback], "post": {"return_value": {
                    "code": 0,
                    "transaction_id": "96274000001558075",
                    "banktransaction": {"transaction_id": "96274000001558076"},
                }}},
            ),
            (
                "resulting record unreadable", "indeterminate",
                {"reads": [source, readback],
                 "result": banking.BankingToolError("Zoho did not return resulting bank transaction")},
            ),
        ]
        for label, expected_status, kwargs in cases:
            with self.subTest(case=label):
                path, _ = self.stage_recovery(source=source)
                digest = json.loads(path.read_text(encoding="utf-8"))["sha256"]
                posted = mock.MagicMock(
                    **(kwargs.pop("post", None) or {"return_value": self.recovery_post_response()})
                )
                with self.assertRaisesRegex(banking.BankingToolError, expected_status):
                    self.commit_recovery(path, posted=posted, **kwargs)
                if expected_status == "aborted_before_write":
                    posted.assert_not_called()
                else:
                    posted.assert_called_once()
                lock = json.loads(banking.lock_path(digest).read_text(encoding="utf-8"))
                self.assertEqual(lock["status"], expected_status)
                self.assertTrue(lock["no_retry"])
                self.assertIn(
                    "zoho_banking_commit_failed_permanently_locked",
                    [call.args[0] for call in self.append_receipt.call_args_list],
                )

    def test_recovery_plan_evidence_and_summary_cannot_be_edited_after_review(self) -> None:
        path, _ = self.stage_recovery()
        for index, (label, mutate) in enumerate((
            ("dropped recovery", lambda plan: plan["evidence"].update({"airwallex_recovery": None})),
            ("forged absence", lambda plan: plan["evidence"]["airwallex_recovery"].update(
                {"superseded_transfer_verified_absent": False}
            )),
            ("counterpart declared required", lambda plan: plan["evidence"]["airwallex_recovery"].update(
                {"live_outgoing_counterpart_required": True}
            )),
            ("swapped historical digest", lambda plan: plan["evidence"]["airwallex_recovery"].update(
                {"historical_plan_sha256": "0" * 64}
            )),
            ("softened statement", lambda plan: plan["human_summary"]["recovery"].update(
                {"statement": "routine deposit"}
            )),
            ("claimed already written", lambda plan: plan["human_summary"]["recovery"].update(
                {"write_performed_yet": True}
            )),
        )):
            with self.subTest(tampered=label):
                target = self.plan_dir / f"tampered_{index}.json"
                target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                self.rewrite_with_hash(target, mutate)
                self.load_vault.reset_mock()
                with mock.patch.object(banking, "api_post_allowed") as post:
                    with self.assertRaises(banking.BankingToolError):
                        banking.command_commit(
                            argparse.Namespace(plan=str(target), approval="APPROVED"), "categorize"
                        )
                    post.assert_not_called()
                self.load_vault.assert_not_called()

    def test_absent_record_class_is_narrower_than_a_failed_read(self) -> None:
        self.assertTrue(issubclass(banking.BankingRecordAbsent, banking.BankingToolError))
        response = {"transactions": [], "page_context": {"page": 1, "has_more_page": False}}
        with mock.patch.object(banking, "books_ui_get", return_value=response):
            with self.assertRaises(banking.BankingRecordAbsent):
                banking.get_uncategorized_ui_transaction(self.vault, "96274000001535012")
        # A read that FAILS is never an absence proof.
        with mock.patch.object(
            banking, "get_bank_transaction",
            side_effect=banking.BankingToolError("Playwright is unavailable"),
        ), self.assertRaisesRegex(banking.BankingToolError, "Playwright is unavailable"):
            banking.prove_superseded_transfer_absent("token", self.vault)


if __name__ == "__main__":
    unittest.main(verbosity=2)
