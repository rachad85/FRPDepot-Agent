from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS = Path(r"C:\FRPDepot\Dado\Tools\woocommerce")
sys.path.insert(0, str(TOOLS))
import woocommerce_shipping_policy_tool as shipping  # noqa: E402

TARGET = {"kind": "variation", "product_id": 1455, "variation_id": 1457}
EXPECTED_SKU = "PIDN25150PSI470"
META_KEY = "_wc_gla_sync_status"
STATE_DIR = Path(r"C:\FRPDepot\Dado\20_Working\catalog_shipping_policy")
READY_FLAG = STATE_DIR / "gla_sync_variation_1457_ready.flag"
ERROR_FLAG = STATE_DIR / "gla_sync_variation_1457_monitor_error.flag"


def mark_once(path: Path, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": status,
                "recorded_utc": datetime.now(timezone.utc).isoformat(),
                "target": TARGET,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    if READY_FLAG.exists():
        return 0

    try:
        record = shipping.read_target(TARGET)
    except Exception as exc:
        if not ERROR_FLAG.exists():
            mark_once(ERROR_FLAG, type(exc).__name__)
            print(
                "Google-sync monitor could not read Pipe variation 1457 "
                f"({type(exc).__name__}). No WooCommerce write was performed."
            )
        return 0

    if ERROR_FLAG.exists():
        ERROR_FLAG.unlink()

    if str(record.get("sku") or "") != EXPECTED_SKU:
        print(
            "Google-sync monitor stopped: variation 1457 no longer has the expected SKU. "
            "No WooCommerce write was performed."
        )
        mark_once(READY_FLAG, "identity_changed")
        return 0

    if str(record.get("shipping_class") or "") != "":
        print(
            "Google-sync monitor stopped: variation 1457 no longer has a blank shipping class. "
            "No WooCommerce write was performed."
        )
        mark_once(READY_FLAG, "shipping_class_changed")
        return 0

    entries = [
        row
        for row in record.get("meta_data", [])
        if isinstance(row, dict) and row.get("key") == META_KEY
    ]
    if len(entries) != 1:
        return 0

    if entries[0].get("value") != "synced":
        return 0

    mark_once(READY_FLAG, "ready")
    shipping.wc.append_receipt(
        "gla_sync_ready_monitor_alerted",
        "variation 1457 / PIDN25150PSI470 is settled at the fixed Google sync staged state; read-only; no WooCommerce write",
    )
    print(
        "Google Listings sync is settled for Pipe variation 1457 "
        "(PIDN25150PSI470). It is ready for a fresh one-target shipping-class plan. "
        "No WooCommerce write was performed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
