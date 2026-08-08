#!/usr/bin/env python
"""Silent daily FRP Depot Zoho imported-feed review. GET-only; never stages or commits."""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

REPO_ZOHO_DIR = Path(r"C:\FRPDepot\Dado\Tools\zoho")
LOCAL_ZOHO_DIR = Path(__file__).resolve().parents[1] / "zoho"
ZOHO_DIR = REPO_ZOHO_DIR if REPO_ZOHO_DIR.is_dir() else LOCAL_ZOHO_DIR
if not ZOHO_DIR.is_dir():
    raise RuntimeError("FRP Depot Zoho tool directory is missing.")
if str(ZOHO_DIR) not in sys.path:
    sys.path.insert(0, str(ZOHO_DIR))

import zoho_banking_reconciliation_tool as banking  # noqa: E402
import zoho_tool  # noqa: E402

LOGICAL_ACCOUNTS = (
    {
        "label": "Desjardins CAD",
        "records": (
            ("96274000001409019", "Chequing account (C)", "CAD"),
            ("96274000001411002", "FRP Depots - Desjardins", "CAD"),
        ),
    },
    {
        "label": "Desjardins USD",
        "records": (("96274000001409012", "USD Desjardins corporate build-up account", "USD"),),
    },
    {
        "label": "Stripe",
        "records": (("96274000000035815", "Stripe Clearing", "CAD"),),
    },
    {
        "label": "PayPal",
        "records": (("96274000000035828", "PayPal Clearing", "CAD"),),
    },
)

PAYROLL_MARKERS = (
    "payroll", "salary", "salaries", "wage", "wages", "ceridian", "dayforce",
    "adp", "source deduction", "workers compensation", "workplace safety",
)
TRANSFER_MARKERS = (
    "transfer", "airwallex", "internal", "stripe payout", "paypal transfer",
)
EXPENSE_TARGET_TYPES = {
    "expense", "bill", "bill_payment", "vendor_payment", "vendorpayment",
    "credit_card_charge", "card_charge",
}
TRANSFER_TARGET_TYPES = {"transfer", "transfer_fund", "fund_transfer"}
PAYROLL_TARGET_TYPES = {"payroll", "payroll_payment", "payroll_liability"}


class DailyReviewError(RuntimeError):
    pass


def decimal_text(value: Any) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DailyReviewError(f"Invalid bank amount: {value!r}") from exc
    return f"{amount:,.2f}"


def account_rows(access_token: str, vault: dict[str, Any]) -> dict[str, dict[str, Any]]:
    query = urlencode({
        "organization_id": vault["books_organization_id"],
        "page": 1,
        "per_page": 200,
    })
    result = zoho_tool.api_get(
        access_token, str(vault["api_domain"]), f"/books/v3/bankaccounts?{query}"
    )
    rows = result.get("bankaccounts")
    if not isinstance(rows, list):
        raise DailyReviewError("Zoho did not return its bank-account list.")
    if (result.get("page_context") or {}).get("has_more_page"):
        raise DailyReviewError("Zoho bank-account list exceeded the 200-row safety limit.")
    return {
        str(row.get("account_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("account_id")
    }


def validate_accounts(live: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for group in LOGICAL_ACCOUNTS:
        for account_id, expected_name, expected_currency in group["records"]:
            row = live.get(account_id)
            if not row:
                raise DailyReviewError(
                    f"Configured {group['label']} account {account_id} is missing from live Zoho."
                )
            actual = (
                str(row.get("account_name") or "").strip(),
                str(row.get("currency_code") or "").strip().upper(),
                bool(row.get("is_active")),
            )
            expected = (expected_name, expected_currency, True)
            if actual != expected:
                raise DailyReviewError(
                    f"Configured {group['label']} account drift: expected {expected}, got {actual}."
                )
            checks.append({
                "logical_account": group["label"],
                "account_id": account_id,
                "account_name": expected_name,
                "currency": expected_currency,
                "feeds_last_refresh_date": str(row.get("feeds_last_refresh_date") or ""),
                "refresh_status": str(row.get("refresh_status") or ""),
            })
    return checks


def projected_line(row: dict[str, Any]) -> dict[str, Any]:
    transaction_id = banking.positive_id(row.get("transaction_id"), "imported transaction_id")
    account_id = banking.positive_id(row.get("account_id"), "imported account_id")
    return {
        "transaction_id": transaction_id,
        "account_id": account_id,
        "date": banking.date_text(row.get("date"), "imported transaction date"),
        "amount": decimal_text(row.get("amount")),
        "amount_raw": str(row.get("amount")),
        "currency": banking.clean_text(row.get("currency_code"), "imported currency", 16),
        "description": str(row.get("description") or "").strip(),
        "payee": str(row.get("payee") or "").strip(),
        "reference": str(row.get("reference_number") or "").strip(),
        "debit_or_credit": str(row.get("debit_or_credit") or "").strip().lower(),
        "status": banking.normalized_status(row.get("status")),
    }


def fetch_account_lines(vault: dict[str, Any], account_id: str) -> list[dict[str, Any]]:
    organization_id = banking.positive_id(vault["books_organization_id"], "organization_id")
    found: list[dict[str, Any]] = []
    for page_number in range(1, 51):
        query = urlencode({
            "account_id": account_id,
            "page": page_number,
            "per_page": 200,
            "response_option": 1,
            "organization_id": organization_id,
        })
        result = banking.books_ui_get(
            f"/api/v3/banktransactions/uncategorized?{query}", organization_id
        )
        rows = result.get("transactions")
        if not isinstance(rows, list):
            raise DailyReviewError(f"Zoho omitted imported-feed rows for account {account_id}.")
        for row in rows:
            if not isinstance(row, dict):
                raise DailyReviewError("Zoho returned an invalid imported-feed row.")
            line = projected_line(row)
            if line["account_id"] != account_id:
                raise DailyReviewError(
                    f"Zoho ignored account filter {account_id}; returned {line['account_id']}."
                )
            found.append(line)
        page_context = result.get("page_context") or {}
        if not isinstance(page_context, dict):
            raise DailyReviewError("Zoho returned invalid imported-feed page context.")
        if not page_context.get("has_more_page"):
            break
    else:
        raise DailyReviewError(f"Imported feed for {account_id} exceeded 50 pages.")
    ids = [row["transaction_id"] for row in found]
    if len(ids) != len(set(ids)):
        raise DailyReviewError(f"Imported feed for {account_id} returned duplicate transaction IDs.")
    return found


def candidates_for(vault: dict[str, Any], transaction_id: str) -> list[dict[str, Any]]:
    organization_id = banking.positive_id(vault["books_organization_id"], "organization_id")
    query = urlencode({
        "statement_ids": transaction_id,
        "organization_id": organization_id,
    })
    result = banking.books_ui_get(
        f"/api/v3/banktransactions/uncategorized/match?{query}", organization_id
    )
    rows = result.get("matching_transactions")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise DailyReviewError(f"Zoho returned invalid match candidates for {transaction_id}.")
    projected: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise DailyReviewError("Zoho returned a non-object match candidate.")
        projected.append({
            "transaction_id": str(row.get("transaction_id") or "").strip(),
            "transaction_type": str(row.get("transaction_type") or "").strip().lower(),
            "transaction_number": str(row.get("transaction_number") or "").strip(),
            "reference": str(row.get("reference_number") or "").strip(),
            "date": str(row.get("date") or "").strip(),
            "amount": decimal_text(row.get("amount")),
            "contact": str(row.get("contact_name") or "").strip(),
            "is_best_match": row.get("is_best_match") is True,
            "is_exact_match": row.get("is_exact_match") is True,
        })
    return projected


def classify(line: dict[str, Any], candidates: list[dict[str, Any]]) -> tuple[str, str]:
    best = [row for row in candidates if row["is_best_match"]]
    if len(best) > 1:
        return "unknown", "Zoho returned more than one best match; manual review required."
    if best:
        target_type = best[0]["transaction_type"]
        if target_type == "invoice":
            return "customer receipt", "Recommend staging an invoice match after live verification."
        if target_type in PAYROLL_TARGET_TYPES:
            return "payroll", "Recommend staging a payroll match after live verification."
        if target_type in EXPENSE_TARGET_TYPES:
            return "expense", "Recommend staging an expense/vendor-payment match after live verification."
        if target_type in TRANSFER_TARGET_TYPES:
            return "transfer", "Recommend staging an internal-transfer match after live verification."
    text = " ".join((line["description"], line["payee"], line["reference"])).lower()
    if any(marker in text for marker in PAYROLL_MARKERS):
        return "possible payroll", "No protected Zoho payroll target found; do not categorize automatically."
    if any(marker in text for marker in TRANSFER_MARKERS):
        return "possible transfer", "No protected transfer target found; verify both sides before staging."
    try:
        amount = Decimal(line["amount_raw"])
    except InvalidOperation:
        amount = Decimal("0")
    if amount < 0 or line["debit_or_credit"] == "debit":
        return "possible expense", "No protected expense target found; account and tax treatment need review."
    return "unknown receipt", "No protected target found; customer/source needs review."


def build_report(access_token: str, vault: dict[str, Any]) -> dict[str, Any]:
    checks = validate_accounts(account_rows(access_token, vault))
    check_by_id = {row["account_id"]: row for row in checks}
    lines: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in LOGICAL_ACCOUNTS:
        for account_id, _name, _currency in group["records"]:
            for line in fetch_account_lines(vault, account_id):
                if line["transaction_id"] in seen:
                    raise DailyReviewError(
                        f"Transaction {line['transaction_id']} appeared under multiple configured accounts."
                    )
                seen.add(line["transaction_id"])
                candidate_rows = candidates_for(vault, line["transaction_id"])
                category, recommendation = classify(line, candidate_rows)
                line.update({
                    "logical_account": group["label"],
                    "account_name": check_by_id[account_id]["account_name"],
                    "category": category,
                    "recommendation": recommendation,
                    "candidates": candidate_rows,
                })
                lines.append(line)
    lines.sort(key=lambda row: (row["date"], row["logical_account"], row["transaction_id"]))
    return {"accounts_checked": checks, "open_lines": lines, "open_count": len(lines)}


def render(report: dict[str, Any]) -> str:
    if not report["open_lines"]:
        return ""
    out = [
        "## Daily Zoho banking review",
        "",
        f"Open imported-feed lines: **{report['open_count']}**",
        "Zoho writes: **0**",
        "",
    ]
    for index, line in enumerate(report["open_lines"], 1):
        out.extend([
            f"### {index}. {line['logical_account']} — {line['currency']} {line['amount']}",
            f"- Date: {line['date']}",
            f"- Account: {line['account_name']}",
            f"- Description: {line['description'] or '(blank)'}",
            f"- Payee/reference: {line['payee'] or line['reference'] or '(blank)'}",
            f"- Classification: **{line['category']}**",
        ])
        best = [row for row in line["candidates"] if row["is_best_match"]]
        if len(best) == 1:
            row = best[0]
            label = row["transaction_number"] or row["reference"] or row["transaction_id"]
            out.append(
                f"- Zoho best match: {row['transaction_type']} {label} — {row['contact'] or '(no contact)'} — {line['currency']} {row['amount']}"
            )
        else:
            out.append("- Zoho best match: none")
        out.extend([f"- Recommendation: {line['recommendation']}", ""])
    out.append("Reply with the line number you want reviewed and staged. Nothing was committed.")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON even when no lines are open.")
    args = parser.parse_args()
    vault = zoho_tool.load_vault()
    access_token, vault = zoho_tool.refresh_access_token(vault)
    report = build_report(access_token, vault)
    zoho_tool.save_vault(vault)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    text = render(report)
    if text:
        zoho_tool.append_receipt(
            "daily_zoho_banking_review_issued",
            f"accounts=4; records_checked={len(report['accounts_checked'])}; open_lines={report['open_count']}; writes=0",
        )
        print(text)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DailyReviewError, banking.BankingToolError, zoho_tool.ZohoError) as exc:
        print(f"DAILY ZOHO BANKING REVIEW FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
