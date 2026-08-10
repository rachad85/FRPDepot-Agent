#!/usr/bin/env python
"""Silent watch for FRP Depot orders worth measuring while they are packed.

One read-only WooCommerce scan per run. When a future order contains a product
that has only a RESEARCHED packing estimate, this prints one short line asking
for the real measurements. When nothing new is relevant it prints nothing at
all, because a monitor that speaks every run stops being read.

It can queue an opportunity. It can NEVER record a measurement: physical
numbers enter only through `packing_observation_tool.py record`, from Rachad's
own words.

No customer, address, amount, tax, payment or contact detail can appear in this
output -- the scan never holds any, by projection.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
import json
from pathlib import Path
import sys

WOO_TOOLS = Path(r"C:\FRPDepot\Dado\Tools\woocommerce")
if str(WOO_TOOLS) not in sys.path:
    sys.path.insert(0, str(WOO_TOOLS))

import packing_observation_tool as pot  # noqa: E402
import woocommerce_common as wc  # noqa: E402

MAX_LINES = 12
FOLLOW_UP = ("When packed, send actual package quantity, L x W x H cm, scale weight kg, "
             "packing material, and a photo/document reference.")


def build_messages(result: dict) -> list[str]:
    """One line per order, newest last, bounded to MAX_LINES total."""
    grouped: "OrderedDict[int, list[dict]]" = OrderedDict()
    for row in result.get("new_opportunities") or []:
        grouped.setdefault(int(row["order_id"]), []).append(row)
    if not grouped:
        return []

    lines: list[str] = []
    order_ids = sorted(grouped)
    room = MAX_LINES if len(order_ids) <= MAX_LINES else MAX_LINES - 1
    for order_id in order_ids[:room]:
        rows = grouped[order_id]
        items = ", ".join(f"{row['quantity']} x {row['sku']}" for row in rows)
        groups = ", ".join(sorted({str(row["group_id"]) for row in rows}))
        lines.append(
            f"Packing data opportunity: Woo order {order_id} has {items} ({groups}). {FOLLOW_UP}"
        )
    if len(order_ids) > room:
        remaining = len(order_ids) - room
        lines.append(
            f"+{remaining} more order(s) with packing opportunities. "
            "Run: packing_observation_tool.py pending"
        )
    return lines[:MAX_LINES]


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FRP Depot packing-opportunity monitor.")
    parser.add_argument("--json", action="store_true", help="Print the raw scan result.")
    args = parser.parse_args(argv)

    try:
        result = pot.run_scan()
    except Exception as exc:  # noqa: BLE001 - a watch job must not crash the cron
        signature = getattr(exc, "signature", None) or type(exc).__name__
        detail = wc.scrub(str(exc))[:300]
        try:
            announce = pot.raise_error_flag(str(signature))
        except OSError:
            announce = True
        if announce:
            print("Packing monitor could not read WooCommerce orders: " + detail)
            print("No packing data was lost; the next successful scan picks up where this "
                  "one stopped. Nothing was written to WooCommerce.")
        return 0

    pot.clear_error_flag()
    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        return 0
    for line in build_messages(result):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
