#!/usr/bin/env python
"""Read-only exact and alternative Inventory matching for the current RFQs."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import zoho_tool
from zoho_inventory_item_tool import list_all_items

ROOT = Path(r"C:\FRPDepot")
OUT = ROOT / "Dado" / "20_Working" / "pricing_requests"


def slim(x):
    return {k: x.get(k) for k in ("item_id", "name", "sku", "unit", "rate", "stock_on_hand", "available_stock", "status")}


def matches(items, pattern):
    rx = re.compile(pattern, re.I)
    return [slim(x) for x in items if rx.search(" ".join(str(x.get(k) or "") for k in ("name", "sku", "description")))]


def exact_name(items, name):
    return [slim(x) for x in items if str(x.get("name") or "").casefold() == name.casefold()]


def main():
    vault = zoho_tool.load_vault()
    token, vault = zoho_tool.refresh_access_token(vault)
    items = list_all_items(token, vault)
    active = [x for x in items if str(x.get("status") or "active").casefold() == "active"]

    brian = {
        "exact_catalog_lines": {
            "6in_150psi_d411_pipe": exact_name(active, 'FRP FW PIPE-6"/150PSI/D411'),
            "6in_150psi_d411_stub_flange": exact_name(active, 'FRP STUB FLANGE-6"/150PSI/D411'),
            "2in_150psi_d411_stub_flange": exact_name(active, 'FRP STUB FLANGE-2"/150PSI/D411'),
        },
        "custom_searches": {
            "2in_d411_saddle_tee": matches(active, r"SADDLE.*2.*(D411|411)|(D411|411).*SADDLE.*2"),
            "joint_material": matches(active, r"JOINT|BOND|WRAP|ADHES|INSTALLATION KIT"),
            "black_exterior_or_gelcoat": matches(active, r"BLACK|GEL\s*COAT|COATING"),
        },
    }

    kenz_exact = {
        "b95_or_b9_resin": matches(active, r"\bB95\b|\bB9\b"),
        "collar_loose_flange": matches(active, r"COLLAR|LOOSE\s+FLANGE"),
        "flat_flange": matches(active, r"FLAT\s+FLANGE"),
    }
    kenz_alternatives = {}
    for inches in (6, 8, 10, 12):
        for resin in (411, 470):
            kenz_alternatives[f'{inches}in_150psi_d{resin}_pipe'] = exact_name(active, f'FRP FW PIPE-{inches}"/150PSI/D{resin}')
            kenz_alternatives[f'{inches}in_150psi_d{resin}_stub_flange'] = exact_name(active, f'FRP STUB FLANGE-{inches}"/150PSI/D{resin}')

    output = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "zoho_modified": False,
        "items_scanned": len(items),
        "brian": brian,
        "kenz": {
            "exact_requested_system_matches": kenz_exact,
            "standard_catalog_alternatives_not_exact_matches": kenz_alternatives,
            "mismatch_warning": "B95/B9 resin, specified wall thicknesses, collars with loose flanges, and fixed flat flanges do not have exact matching Inventory records.",
        },
    }
    zoho_tool.save_vault(vault)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT / f"{stamp}_zoho_inventory_rfq_match.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt("zoho_inventory_rfq_match", str(path))
    concise = {
        "output": str(path),
        "items_scanned": len(items),
        "brian_exact": brian["exact_catalog_lines"],
        "brian_custom_match_counts": {k: len(v) for k, v in brian["custom_searches"].items()},
        "kenz_exact_match_counts": {k: len(v) for k, v in kenz_exact.items()},
        "kenz_standard_alternatives_found": sum(1 for v in kenz_alternatives.values() if v),
    }
    print(json.dumps(concise, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
