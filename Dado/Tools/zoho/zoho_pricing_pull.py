#!/usr/bin/env python
"""Pull only live Zoho records relevant to the two identified pricing requests."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import zoho_tool

ROOT = Path(r"C:\FRPDepot")
OUT_ROOT = ROOT / "Dado" / "20_Working" / "pricing_requests"
TARGETS = ("nasahtec", "nashtec", "brian baldeschwiler", "brianb@", "fibre mauricie", "yannick.bedard")


def paginate(token, domain, path, org_id, key):
    rows = []
    for page in range(1, 101):
        query = urlencode({"organization_id": org_id, "page": page, "per_page": 200})
        response = zoho_tool.api_get(token, domain, f"{path}?{query}")
        rows.extend(response.get(key) or [])
        if not (response.get("page_context") or {}).get("has_more_page"):
            return rows
    raise RuntimeError(f"Pagination guard stopped on {path}")


def item_text(item):
    return " ".join(str(item.get(field) or "") for field in ("name", "sku", "description", "purchase_description", "part_number")).casefold()


def contact_text(contact):
    people = contact.get("contact_persons") or []
    parts = [str(contact.get(field) or "") for field in ("contact_name", "company_name", "email")]
    for person in people:
        parts.extend(str(person.get(field) or "") for field in ("first_name", "last_name", "email"))
    return " ".join(parts).casefold()


def select_item(item):
    fields = (
        "item_id", "name", "sku", "unit", "item_type", "product_type", "description",
        "rate", "purchase_rate", "reorder_level", "tax_id", "tax_name", "is_taxable",
        "status", "stock_on_hand", "available_stock",
    )
    return {field: item.get(field) for field in fields}


def select_contact(contact):
    fields = (
        "contact_id", "contact_name", "company_name", "contact_type", "customer_sub_type",
        "email", "currency_id", "currency_code", "currency_symbol", "payment_terms",
        "payment_terms_label", "tax_id", "tax_name", "is_taxable", "status",
    )
    result = {field: contact.get(field) for field in fields}
    result["contact_persons"] = [
        {field: person.get(field) for field in ("first_name", "last_name", "email", "is_primary_contact")}
        for person in contact.get("contact_persons") or []
    ]
    return result


def main():
    vault = zoho_tool.load_vault()
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = str(vault["api_domain"])
    inventory_id = str(vault["inventory_organization_id"])
    books_id = str(vault["books_organization_id"])
    items = paginate(token, domain, "/inventory/v1/items", inventory_id, "items")
    item_matches = [item for item in items if "411" in item_text(item) or "470" in item_text(item) or any(word in item_text(item) for word in ("joint kit", "bonding kit", "saddle tee", "stub flange"))]
    contacts = paginate(token, domain, "/books/v3/contacts", books_id, "contacts")
    contact_matches = [contact for contact in contacts if any(target in contact_text(contact) for target in TARGETS)]
    estimates = paginate(token, domain, "/books/v3/estimates", books_id, "estimates")
    estimate_matches = [estimate for estimate in estimates if any(target in (str(estimate.get("customer_name") or "") + " " + str(estimate.get("reference_number") or "")).casefold() for target in TARGETS)]
    estimate_details = []
    for estimate in estimate_matches:
        estimate_id = str(estimate.get("estimate_id") or "")
        if estimate_id:
            query = urlencode({"organization_id": books_id})
            detail = zoho_tool.api_get(token, domain, f"/books/v3/estimates/{estimate_id}?{query}")
            record = detail.get("estimate") or {}
            estimate_details.append({
                "estimate_id": record.get("estimate_id"),
                "estimate_number": record.get("estimate_number"),
                "customer_id": record.get("customer_id"),
                "customer_name": record.get("customer_name"),
                "date": record.get("date"),
                "expiry_date": record.get("expiry_date"),
                "status": record.get("status"),
                "currency_code": record.get("currency_code"),
                "exchange_rate": record.get("exchange_rate"),
                "line_items": [
                    {field: line.get(field) for field in ("item_id", "name", "description", "quantity", "rate", "discount", "tax_name", "item_total")}
                    for line in record.get("line_items") or []
                ],
                "shipping_charge": record.get("shipping_charge"),
                "adjustment": record.get("adjustment"),
                "total": record.get("total"),
            })
    zoho_tool.save_vault(vault)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = OUT_ROOT / f"{stamp}_zoho_pricing_records.json"
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "organization": vault.get("books_organization_name"),
        "items_scanned": len(items),
        "item_matches": sorted((select_item(item) for item in item_matches), key=lambda row: str(row.get("name") or "")),
        "contact_matches": [select_contact(contact) for contact in contact_matches],
        "estimate_matches": estimate_details,
        "source": "Live Zoho Inventory and Zoho Books API records",
        "zoho_modified": False,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt("zoho_pricing_records_pulled", str(output))
    print(json.dumps({"output": str(output), "items_scanned": len(items), "item_match_count": len(item_matches), "contact_matches": payload["contact_matches"], "estimate_matches": [{k: row.get(k) for k in ("estimate_id", "estimate_number", "customer_name", "date", "status", "currency_code", "total")} for row in estimate_details], "item_matches": payload["item_matches"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
