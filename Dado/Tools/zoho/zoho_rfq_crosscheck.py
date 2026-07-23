#!/usr/bin/env python
"""Read-only Zoho Books cross-check for named FRP Depot RFQ customers."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlencode

import zoho_tool

ROOT = Path(r"C:\FRPDepot")
OUT = ROOT / "Dado" / "20_Working" / "pricing_requests"
FORBIDDEN = ("troy dualam", "troydualam.com")
TARGETS = [
    {"key": "nasahtec", "searches": ["Nasahtec", "nashtecllc.com", "brianb@nashtecllc.com"]},
    {"key": "kenz_jordan", "searches": ["KENZ JORDAN", "kenzjordan.com", "kz.16sales@kenzjordan.com"]},
    {"key": "fibre_mauricie", "searches": ["Fibre Mauricie", "fibremauricie.com"]},
]


def forbidden(record) -> bool:
    text = json.dumps(record, ensure_ascii=False).casefold()
    return any(x in text for x in FORBIDDEN)


def get_pages(token, domain, path, org, extra=None):
    rows = []
    for page in range(1, 101):
        params = {"organization_id": org, "page": page, "per_page": 200}
        params.update(extra or {})
        result = zoho_tool.api_get(token, domain, f"{path}?{urlencode(params)}")
        key = {
            "/books/v3/contacts": "contacts",
            "/books/v3/estimates": "estimates",
            "/books/v3/invoices": "invoices",
            "/books/v3/salesorders": "salesorders",
            "/books/v3/customerpayments": "customerpayments",
        }[path]
        rows.extend(result.get(key) or [])
        if not (result.get("page_context") or {}).get("has_more_page"):
            return rows
    raise RuntimeError(f"Pagination guard stopped for {path}")


def slim_contact(c):
    return {k: c.get(k) for k in (
        "contact_id", "contact_name", "company_name", "contact_type", "email",
        "currency_id", "currency_code", "payment_terms", "payment_terms_label", "status"
    )}


def transaction_summary(path, row):
    common = {k: row.get(k) for k in ("customer_id", "customer_name", "date", "status", "currency_code", "total")}
    if path == "/books/v3/estimates":
        common.update({k: row.get(k) for k in ("estimate_id", "estimate_number")})
    elif path == "/books/v3/invoices":
        common.update({k: row.get(k) for k in ("invoice_id", "invoice_number", "balance", "payment_made")})
    elif path == "/books/v3/salesorders":
        common.update({k: row.get(k) for k in ("salesorder_id", "salesorder_number", "invoiced_status", "shipped_status")})
    else:
        common.update({k: row.get(k) for k in ("payment_id", "payment_number", "amount", "unused_amount", "payment_mode", "reference_number")})
    return common


def main():
    vault = zoho_tool.load_vault()
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = str(vault["api_domain"])
    org = str(vault["books_organization_id"])
    output = {"generated_utc": datetime.now(timezone.utc).isoformat(), "zoho_modified": False, "targets": []}
    endpoints = [
        "/books/v3/estimates",
        "/books/v3/invoices",
        "/books/v3/salesorders",
        "/books/v3/customerpayments",
    ]
    for target in TARGETS:
        contacts = {}
        for search in target["searches"]:
            for c in get_pages(token, domain, "/books/v3/contacts", org, {"search_text": search}):
                if forbidden(c):
                    continue
                contacts[str(c.get("contact_id"))] = c
        target_result = {"key": target["key"], "contacts": [slim_contact(c) for c in contacts.values()], "transactions": {}}
        for endpoint in endpoints:
            summaries = []
            for contact_id in contacts:
                for row in get_pages(token, domain, endpoint, org, {"customer_id": contact_id}):
                    if not forbidden(row):
                        summaries.append(transaction_summary(endpoint, row))
            target_result["transactions"][endpoint.rsplit("/", 1)[-1]] = summaries
        output["targets"].append(target_result)
    zoho_tool.save_vault(vault)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT / f"{stamp}_zoho_books_rfq_crosscheck.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt("zoho_books_rfq_crosscheck", str(path))
    print(json.dumps({"output": str(path), "targets": output["targets"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
