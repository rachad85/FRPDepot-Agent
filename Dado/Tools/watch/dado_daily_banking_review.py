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


def fetch_feed_rows(vault: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the WHOLE imported feed once. The account_id filter is never sent.

    MEASURED 2026-08-09 and 2026-08-10: a request carrying
    account_id=96274000001409019 came back with a row belonging to
    96274000001409012, and the unfiltered read on 08-10 proved that row was the
    ONLY line in the whole feed. Zoho's server-side filter on this UI route is
    not trustworthy, so it is not used at all - every row is attributed
    client-side from its own account_id. banking.list_uncategorized_ui_transactions
    has always fetched unfiltered for the same reason.

    THIS IS WHY THE JOB NEVER PRODUCED A REVIEW. The old code asked per account
    and raised the moment Zoho answered with a different one, so an empty feed
    was silent and ANY row was a hard failure - there was no input that produced
    a report. Five runs since 2026-08-07: one creation test, one silent, three
    errors, zero reviews.
    """
    organization_id = banking.positive_id(vault["books_organization_id"], "organization_id")
    rows_out: list[dict[str, Any]] = []
    for page_number in range(1, 51):
        query = urlencode({
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
            raise DailyReviewError("Zoho omitted its imported-feed rows.")
        for row in rows:
            if not isinstance(row, dict):
                raise DailyReviewError("Zoho returned an invalid imported-feed row.")
            rows_out.append(row)
        page_context = result.get("page_context") or {}
        if not isinstance(page_context, dict):
            raise DailyReviewError("Zoho returned invalid imported-feed page context.")
        if not page_context.get("has_more_page"):
            break
    else:
        raise DailyReviewError("Imported feed exceeded 50 pages.")
    return rows_out


def partition_feed(
    rows: list[dict[str, Any]], check_by_id: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split rows on the row's OWN account_id - never on what was requested.

    A row is reviewed only when its account_id is a configured record AND the
    feed's own account_name agrees with the name validate_accounts just read
    from the Books bank-account record. Everything else is DISCLOSED, never
    dropped and never mis-attributed.

    This is the guard that matters. The old code attributed every returned row
    to the account it had ASKED for, so softening its filter check without
    restructuring attribution would have turned an availability bug into a
    money-attribution bug - USD build-up money reported under "Desjardins CAD".
    """
    reviewed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        feed_name = str(row.get("account_name") or "").strip()
        try:
            line = projected_line(row)
        except (DailyReviewError, banking.BankingToolError) as exc:
            skipped.append({
                "reason": f"feed row could not be read ({exc})",
                "account_id": str(row.get("account_id") or "(none)"),
                "account_name": feed_name,
            })
            continue
        check = check_by_id.get(line["account_id"])
        if check is None:
            skipped.append({
                "reason": "not one of the configured FRP Depot accounts",
                "account_id": line["account_id"],
                "account_name": feed_name,
            })
            continue
        if feed_name and feed_name != check["account_name"]:
            skipped.append({
                "reason": (
                    f"feed calls this account {feed_name!r}, Zoho's bank-account "
                    f"record calls it {check['account_name']!r}"
                ),
                "account_id": line["account_id"],
                "account_name": feed_name,
            })
            continue
        reviewed.append(line)
    ids = [line["transaction_id"] for line in reviewed]
    if len(ids) != len(set(ids)):
        raise DailyReviewError("Imported feed returned duplicate transaction IDs.")
    return reviewed, skipped


def skipped_summary(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str, str], int] = {}
    for entry in entries:
        key = (entry["account_id"], entry["account_name"], entry["reason"])
        counts[key] = counts.get(key, 0) + 1
    return [
        {"account_id": account_id, "account_name": name, "reason": reason, "count": count}
        for (account_id, name, reason), count in sorted(counts.items())
    ]


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
    """One unfiltered read, attributed per row.

    THE DELIBERATE SPLIT: whole-read integrity failures still HARD-FAIL - a
    non-list, a non-dict row, bad page context, >50 pages, duplicate ids,
    account drift in validate_accounts - because a read we cannot trust must not
    become a report. Per-ROW problems degrade and are disclosed instead, because
    one unreadable line must not cost the other nine their review.
    """
    checks = validate_accounts(account_rows(access_token, vault))
    check_by_id = {row["account_id"]: row for row in checks}
    lines, skipped = partition_feed(fetch_feed_rows(vault), check_by_id)
    unread_candidates = 0
    for line in lines:
        check = check_by_id[line["account_id"]]   # attribution comes from the ROW
        line["logical_account"] = check["logical_account"]
        line["account_name"] = check["account_name"]
        try:
            candidate_rows = candidates_for(vault, line["transaction_id"])
        except (DailyReviewError, banking.BankingToolError) as exc:
            unread_candidates += 1
            line.update({
                "category": "not classified",
                "recommendation": (
                    f"Zoho match candidates could not be read ({exc}); "
                    "classify this line by hand."
                ),
                "candidates": [],
                "candidates_read": False,
            })
            continue
        category, recommendation = classify(line, candidate_rows)
        line.update({
            "category": category,
            "recommendation": recommendation,
            "candidates": candidate_rows,
            "candidates_read": True,
        })
    # If NOTHING could be classified, the read is not trustworthy enough to be a
    # report at all - the same principle as the whole-read failures above,
    # applied consistently.
    if lines and unread_candidates == len(lines):
        raise DailyReviewError(
            "Zoho match candidates could not be read for ANY open line; "
            "the read is not trustworthy enough to report."
        )
    lines.sort(key=lambda row: (row["date"], row["logical_account"], row["transaction_id"]))
    return {
        "accounts_checked": checks,
        "open_lines": lines,
        "open_count": len(lines),
        "not_reviewed": skipped_summary(skipped),
        "not_reviewed_count": len(skipped),
        "unread_candidate_count": unread_candidates,
    }


def render(report: dict[str, Any]) -> str:
    # .get() throughout so a report built by older code still renders.
    not_reviewed = report.get("not_reviewed") or []
    if not report["open_lines"] and not not_reviewed:
        return ""
    out = [
        "## Daily Zoho banking review",
        "",
        f"Open imported-feed lines: **{report['open_count']}**",
        "Zoho writes: **0**",
    ]
    if not_reviewed:
        # Disclosed, never dropped: a line this tool could not attribute is
        # exactly the thing a silent review would hide.
        out.append(
            f"NOT REVIEWED: **{report.get('not_reviewed_count', 0)}** imported-feed "
            "line(s) could not be attributed to a configured account -"
        )
        for entry in not_reviewed:
            name = entry["account_name"] or "(unnamed)"
            out.append(
                f"  - {entry['count']} x account {entry['account_id']} "
                f"({name}): {entry['reason']}"
            )
    if report.get("unread_candidate_count"):
        out.append(
            f"Match candidates unreadable on **{report['unread_candidate_count']}** "
            "line(s); those are flagged below for manual classification."
        )
    out.append("")
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
    # Do not invite a reply when there is nothing to reply about: a report that
    # exists only to disclose skipped lines has no line numbers to pick from.
    if report["open_lines"]:
        out.append("Reply with the line number you want reviewed and staged. Nothing was committed.")
    else:
        out.append("No line was attributable to a configured account. Nothing was committed.")
    return "\n".join(out)


ZOHO_SESSION_DISABLED = Path(r"C:\FRPDepot\Dado\40_Logs\zoho_session_disabled.flag")
def session_is_stopped_on_purpose() -> bool:
    """A deliberate stop is not a fault and must not speak.

    Same rule the gateway watchdog and the lane-health checker already follow.

    NOTE what is deliberately NOT here: a "is the keepalive happy?" precheck.
    When the Zoho UI session is genuinely down, the existing hard failure
    ("Exactly one authenticated Canadian Zoho Books app page must be open") is
    honest, delivered, and names the real problem - swapping it for a quiet exit
    would recreate the silent-failure class this whole audit exists to remove.
    The session going down is the keepalive's problem to fix and report; the
    root cause of the 2026-08-11 outage was that it restarted the browser on the
    wrong data centre, which is fixed there, not worked around here.
    """
    return ZOHO_SESSION_DISABLED.exists()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON even when no lines are open.")
    args = parser.parse_args()
    if session_is_stopped_on_purpose():
        # Silent, and NOT an error: he stopped the Zoho session himself.
        return 0
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
