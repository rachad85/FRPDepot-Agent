#!/usr/bin/env python
"""Read-only search of Zoho estimate history for selected item IDs."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlencode

import zoho_tool

ROOT = Path(r"C:\FRPDepot")
OUT_ROOT = ROOT / "Dado" / "20_Working" / "pricing_requests"
TARGET_IDS = {
    "96274000000034735",  # 6in D411 pipe
    "96274000000029183",  # 6in D411 flange
    "96274000000019627",  # 2in D411 flange
    "96274000000034729",  # 3in D470 pipe
    "96274000000034733",  # 4in D470 pipe
    "96274000000034745",  # 10in D470 pipe
    "96274000000019660",  # 3in D470 flange
    "96274000000019682",  # 4in D470 flange
    "96274000000029185",  # 6in D470 flange
    "96274000000029201",  # 10in D470 flange
}
FORBIDDEN_CUSTOMER_NEEDLES = ("troy dualam", "troydualam")


def list_estimates(token, domain, organization_id):
    rows = []
    for page in range(1, 101):
        query = urlencode({"organization_id": organization_id, "page": page, "per_page": 200})
        result = zoho_tool.api_get(token, domain, f"/books/v3/estimates?{query}")
        rows.extend(result.get("estimates") or [])
        if not (result.get("page_context") or {}).get("has_more_page"):
            return rows
    raise RuntimeError("Estimate pagination guard stopped.")


def main():
    vault = zoho_tool.load_vault()
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = str(vault["api_domain"])
    org = str(vault["books_organization_id"])
    estimates = list_estimates(token, domain, org)
    matches = []
    for estimate in estimates:
        estimate_id = str(estimate.get("estimate_id") or "")
        query = urlencode({"organization_id": org})
        detail = zoho_tool.api_get(token, domain, f"/books/v3/estimates/{estimate_id}?{query}")
        record = detail.get("estimate") or {}
        customer_name = str(record.get("customer_name") or "")
        if any(needle in customer_name.casefold() for needle in FORBIDDEN_CUSTOMER_NEEDLES):
            continue
        for line in record.get("line_items") or []:
            item_id = str(line.get("item_id") or "")
            if item_id in TARGET_IDS:
                matches.append({
                    "estimate_id": estimate_id,
                    "estimate_number": record.get("estimate_number"),
                    "date": record.get("date"),
                    "status": record.get("status"),
                    "customer_name": customer_name,
                    "currency_code": record.get("currency_code"),
                    "exchange_rate": record.get("exchange_rate"),
                    "item_id": item_id,
                    "name": line.get("name"),
                    "quantity": line.get("quantity"),
                    "rate": line.get("rate"),
                    "discount": line.get("discount"),
                    "tax_name": line.get("tax_name"),
                })
    zoho_tool.save_vault(vault)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = OUT_ROOT / f"{stamp}_zoho_item_price_history.json"
    output.write_text(json.dumps({"generated_utc": datetime.now(timezone.utc).isoformat(), "estimates_scanned": len(estimates), "matches": matches, "zoho_modified": False}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    zoho_tool.append_receipt("zoho_item_price_history_pulled", str(output))
    print(json.dumps({"output": str(output), "estimates_scanned": len(estimates), "matches": matches}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
