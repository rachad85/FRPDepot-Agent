#!/usr/bin/env python
"""READ-ONLY monthly reorder analysis for FRP Depot.

Uses Zoho physical stock, invoice demand, open sales commitments, and open
purchase-order quantities. It never writes to Zoho. It writes dated JSON/CSV
reports under Dado/20_Working/reports and appends one local receipt.
"""
from __future__ import annotations

import calendar
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(r"C:\FRPDepot")
ZOHO_TOOLS = ROOT / "Dado" / "Tools" / "zoho"
sys.path.insert(0, str(ZOHO_TOOLS))
import zoho_tool  # noqa: E402


def shift_months(day: date, months: int) -> date:
    month_index = day.year * 12 + day.month - 1 + months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def dec(value: object) -> Decimal:
    return Decimal(str(value or 0))


def main() -> int:
    as_of = date.today()
    history_start = shift_months(as_of, -12)
    recent_start = shift_months(as_of, -4)

    vault = zoho_tool.load_vault()
    token, vault = zoho_tool.refresh_access_token(vault)
    domain = str(vault["api_domain"])
    organization_id = str(vault["inventory_organization_id"])

    def get(path: str, params: dict | None = None) -> dict:
        query = urlencode({**(params or {}), "organization_id": organization_id})
        return zoho_tool.api_get(token, domain, path + "?" + query)

    def collect(path: str, key: str) -> list[dict]:
        rows: list[dict] = []
        for page in range(1, 20):
            payload = get(path, {"page": page, "per_page": 200})
            rows.extend(payload.get(key, []))
            if not (payload.get("page_context") or {}).get("has_more_page"):
                break
        return rows

    items = collect("/inventory/v1/items", "items")
    item_map = {str(item.get("item_id")): item for item in items}
    invoice_list = collect("/inventory/v1/invoices", "invoices")
    salesorder_list = collect("/inventory/v1/salesorders", "salesorders")
    purchaseorder_list = collect("/inventory/v1/purchaseorders", "purchaseorders")

    usage_12m: dict[str, Decimal] = defaultdict(Decimal)
    usage_4m: dict[str, Decimal] = defaultdict(Decimal)
    invoice_evidence: dict[str, list[dict]] = defaultdict(list)
    invoices_used = 0

    for meta in invoice_list:
        status = str(meta.get("status", "")).casefold()
        if status in {"draft", "void", "cancelled"}:
            continue
        raw_date = str(meta.get("date") or "")
        if not raw_date:
            continue
        invoice_date = date.fromisoformat(raw_date)
        if invoice_date < history_start or invoice_date > as_of:
            continue
        invoice = get("/inventory/v1/invoices/" + str(meta["invoice_id"])).get("invoice") or {}
        invoices_used += 1
        for line in invoice.get("line_items", []):
            item_id = str(line.get("item_id") or "")
            quantity = dec(line.get("quantity"))
            if not item_id or quantity <= 0:
                continue
            usage_12m[item_id] += quantity
            if invoice_date >= recent_start:
                usage_4m[item_id] += quantity
            invoice_evidence[item_id].append(
                {
                    "invoice": invoice.get("invoice_number"),
                    "date": str(invoice_date),
                    "customer": invoice.get("customer_name"),
                    "quantity": float(quantity),
                    "unit": line.get("unit"),
                }
            )

    open_commitment: dict[str, Decimal] = defaultdict(Decimal)
    open_salesorders: dict[str, list[dict]] = defaultdict(list)
    for meta in salesorder_list:
        order = get("/inventory/v1/salesorders/" + str(meta["salesorder_id"])).get("salesorder") or {}
        if str(order.get("status", "")).casefold() in {"draft", "void", "cancelled"}:
            continue
        for line in order.get("line_items", []):
            item_id = str(line.get("item_id") or "")
            if not item_id:
                continue
            remaining = max(
                Decimal("0"),
                dec(line.get("quantity"))
                - dec(line.get("quantity_shipped"))
                - dec(line.get("quantity_cancelled"))
                - dec(line.get("quantity_manuallyfulfilled"))
                - dec(line.get("quantity_dropshipped")),
            )
            if remaining <= 0:
                continue
            open_commitment[item_id] += remaining
            open_salesorders[item_id].append(
                {
                    "salesorder": order.get("salesorder_number"),
                    "date": order.get("date"),
                    "customer": order.get("customer_name"),
                    "remaining": float(remaining),
                    "status": order.get("status"),
                }
            )

    incoming: dict[str, Decimal] = defaultdict(Decimal)
    incoming_purchaseorders: dict[str, list[dict]] = defaultdict(list)
    for meta in purchaseorder_list:
        order = get("/inventory/v1/purchaseorders/" + str(meta["purchaseorder_id"])).get("purchaseorder") or {}
        if str(order.get("status", "")).casefold() in {"draft", "void", "cancelled"}:
            continue
        for line in order.get("line_items", []):
            item_id = str(line.get("item_id") or "")
            if not item_id:
                continue
            pending = max(
                Decimal("0"),
                dec(line.get("quantity"))
                - dec(line.get("quantity_received"))
                - dec(line.get("quantity_cancelled")),
            )
            if pending <= 0:
                continue
            incoming[item_id] += pending
            incoming_purchaseorders[item_id].append(
                {
                    "purchaseorder": order.get("purchaseorder_number"),
                    "date": order.get("date"),
                    "vendor": order.get("vendor_name"),
                    "pending": float(pending),
                    "received_status": order.get("received_status"),
                    "expected_delivery_date": order.get("expected_delivery_date")
                    or order.get("delivery_date")
                    or "",
                }
            )

    rows: list[dict] = []
    for item_id in set(usage_12m) | set(open_commitment) | set(incoming):
        item = item_map.get(item_id, {})
        physical = dec(item.get("actual_available_stock"))
        sales_12m = usage_12m[item_id]
        sales_4m = usage_4m[item_id]
        projected_4m = max(sales_4m, sales_12m / Decimal("3"))
        committed = open_commitment[item_id]
        incoming_qty = incoming[item_id]
        requirement = projected_4m + committed
        shortage_before_incoming = max(Decimal("0"), requirement - physical)
        shortage_after_incoming = max(Decimal("0"), requirement - physical - incoming_qty)
        monthly_rate = sales_12m / Decimal("12")
        months_cover = physical / monthly_rate if monthly_rate > 0 else None
        invoices = invoice_evidence[item_id]
        customers = sorted({str(e.get("customer")) for e in invoices if e.get("customer")})
        rows.append(
            {
                "item_id": item_id,
                "sku": item.get("sku") or "",
                "item": item.get("name") or item.get("item_name") or "",
                "unit": item.get("unit") or "",
                "physical_stock": float(physical),
                "open_commitment": float(committed),
                "incoming_open_po": float(incoming_qty),
                "sales_12m": float(sales_12m),
                "sales_last_4m": float(sales_4m),
                "projected_4m_demand": float(projected_4m),
                "requirement_4m_plus_commitments": float(requirement),
                "shortage_before_incoming": float(shortage_before_incoming),
                "shortage_after_incoming": float(shortage_after_incoming),
                "months_cover_at_12m_rate": None if months_cover is None else float(months_cover),
                "invoice_count": len(invoices),
                "customers": customers,
                "invoice_evidence": invoices,
                "open_sales_orders": open_salesorders[item_id],
                "incoming_purchase_orders": incoming_purchaseorders[item_id],
            }
        )

    rows.sort(
        key=lambda row: (
            -row["shortage_after_incoming"],
            -row["shortage_before_incoming"],
            -row["sales_last_4m"],
            -row["sales_12m"],
            row["sku"],
        )
    )
    order_candidates = [row for row in rows if row["shortage_after_incoming"] > 0]
    covered_by_incoming = [
        row
        for row in rows
        if row["shortage_before_incoming"] > 0 and row["shortage_after_incoming"] == 0
    ]
    watch = [
        row
        for row in rows
        if row["sales_12m"] > 0
        and row["shortage_before_incoming"] == 0
        and row["months_cover_at_12m_rate"] is not None
        and row["months_cover_at_12m_rate"] <= 8
    ]

    output_dir = ROOT / "Dado" / "20_Working" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "reorder_analysis_" + as_of.isoformat()
    json_path = output_dir / (stem + ".json")
    csv_path = output_dir / (stem + ".csv")
    method = (
        "Projected four-month demand is the greater of actual last-four-month invoiced "
        "quantity and one-third of twelve-month invoiced quantity. Open sales-order "
        "commitments are added. Physical stock is Zoho actual_available_stock. Open PO "
        "quantities remain incoming, not physical. No safety stock is added."
    )
    report = {
        "as_of": as_of.isoformat(),
        "lead_time_months": 4,
        "history_start": history_start.isoformat(),
        "recent_start": recent_start.isoformat(),
        "method": method,
        "counts": {
            "catalog_items": len(items),
            "invoices_total": len(invoice_list),
            "invoices_used": invoices_used,
            "salesorders_total": len(salesorder_list),
            "purchaseorders_total": len(purchaseorder_list),
            "items_with_activity_or_incoming": len(rows),
            "order_candidates": len(order_candidates),
            "covered_by_incoming": len(covered_by_incoming),
            "watch": len(watch),
        },
        "order_candidates": order_candidates,
        "covered_by_incoming": covered_by_incoming,
        "watch": watch,
        "all_rows": rows,
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    columns = [
        "sku",
        "item",
        "unit",
        "physical_stock",
        "open_commitment",
        "incoming_open_po",
        "sales_12m",
        "sales_last_4m",
        "projected_4m_demand",
        "requirement_4m_plus_commitments",
        "shortage_before_incoming",
        "shortage_after_incoming",
        "months_cover_at_12m_rate",
        "invoice_count",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows([{column: row.get(column) for column in columns} for row in rows])

    receipts = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
    with receipts.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "action": "generated monthly four-month lead-time reorder analysis",
                    "evidence": [str(json_path), str(csv_path)],
                }
            )
            + "\n"
        )

    # Keep cron stdout concise. Full evidence remains in the dated JSON and CSV.
    top_activity = sorted(
        [row for row in rows if row["sales_12m"] > 0],
        key=lambda row: (-row["sales_last_4m"], -row["sales_12m"]),
    )[:15]

    summary_fields = (
        "item_id",
        "sku",
        "item",
        "unit",
        "physical_stock",
        "open_commitment",
        "incoming_open_po",
        "sales_12m",
        "sales_last_4m",
        "projected_4m_demand",
        "requirement_4m_plus_commitments",
        "shortage_before_incoming",
        "shortage_after_incoming",
        "months_cover_at_12m_rate",
        "invoice_count",
        "customers",
    )

    def slim(row: dict, include_evidence: bool = False) -> dict:
        result = {field: row.get(field) for field in summary_fields}
        if include_evidence:
            result["invoice_evidence"] = row.get("invoice_evidence", [])
            result["open_sales_orders"] = row.get("open_sales_orders", [])
            result["incoming_purchase_orders"] = row.get("incoming_purchase_orders", [])
        return result

    print(
        json.dumps(
            {
                "report_paths": [str(json_path), str(csv_path)],
                "as_of": as_of.isoformat(),
                "method": method,
                "counts": report["counts"],
                "order_candidates": [slim(row, True) for row in order_candidates],
                "covered_by_incoming": [slim(row, True) for row in covered_by_incoming],
                "watch": [slim(row) for row in watch],
                "top_activity": [slim(row) for row in top_activity],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    zoho_tool.save_vault(vault)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
