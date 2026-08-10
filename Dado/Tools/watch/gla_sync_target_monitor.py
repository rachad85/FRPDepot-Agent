from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

WOOCOMMERCE_TOOLS = Path(r"C:\FRPDepot\Dado\Tools\woocommerce")
sys.path.insert(0, str(WOOCOMMERCE_TOOLS))
import woocommerce_shipping_policy_tool as shipping  # noqa: E402

STATE_DIR = Path(r"C:\FRPDepot\Dado\20_Working\catalog_shipping_policy")
META_KEY = "_wc_gla_sync_status"


def _mark_once(path: Path, status: str, target: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": status,
                "recorded_utc": datetime.now(timezone.utc).isoformat(),
                "target": target,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def run_monitor(*, family: str, target: dict, expected_sku: str, state_stem: str) -> int:
    ready_flag = STATE_DIR / f"{state_stem}_ready.flag"
    error_flag = STATE_DIR / f"{state_stem}_error.flag"
    if ready_flag.exists():
        return 0

    try:
        record = shipping.read_target(target)
    except Exception as exc:
        if not error_flag.exists():
            _mark_once(error_flag, type(exc).__name__, target)
            print(
                f"Google-sync monitor could not read {family} variation "
                f"{target['variation_id']} ({type(exc).__name__}). "
                "No WooCommerce write was performed."
            )
        return 0

    if error_flag.exists():
        error_flag.unlink()

    if str(record.get("sku") or "") != expected_sku:
        print(
            f"Google-sync monitor stopped: {family} variation {target['variation_id']} "
            "no longer has the expected SKU. No WooCommerce write was performed."
        )
        _mark_once(ready_flag, "identity_changed", target)
        return 0

    if str(record.get("shipping_class") or "") != "":
        print(
            f"Google-sync monitor stopped: {family} variation {target['variation_id']} "
            "no longer has a blank shipping class. No WooCommerce write was performed."
        )
        _mark_once(ready_flag, "shipping_class_changed", target)
        return 0

    entries = [
        row
        for row in record.get("meta_data", [])
        if isinstance(row, dict) and row.get("key") == META_KEY
    ]
    if len(entries) != 1 or entries[0].get("value") != "synced":
        return 0

    _mark_once(ready_flag, "ready", target)
    shipping.wc.append_receipt(
        "gla_sync_ready_monitor_alerted",
        f"{family} variation {target['variation_id']} / {expected_sku} settled at the fixed Google sync staged state; read-only; no WooCommerce write",
    )
    print(
        f"Google Listings sync is settled for {family} variation "
        f"{target['variation_id']} ({expected_sku}). It is ready for a fresh "
        "one-target shipping-class plan. No WooCommerce write was performed."
    )
    return 0
