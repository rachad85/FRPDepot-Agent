#!/usr/bin/env python
"""Weekly nudge about packing measurements that were never sent back.

Local files only -- this never contacts WooCommerce, WordPress, Zoho or UPS,
and it never records a measurement. It answers two questions:

  * which queued packing opportunities are still missing their real numbers, and
  * which groups now have enough single-piece orders to be worth reviewing.

Silent when both answers are "none", and silent again when the answer has not
changed since the last time it spoke -- an unread weekly repeat is worse than
no reminder.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

WOO_TOOLS = Path(r"C:\FRPDepot\Dado\Tools\woocommerce")
if str(WOO_TOOLS) not in sys.path:
    sys.path.insert(0, str(WOO_TOOLS))

import packing_observation_tool as pot  # noqa: E402

MAX_LINES = 15


def load_weekly_state() -> dict:
    path = pot.weekly_state_path()
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def save_weekly_state(state: dict) -> None:
    pot.atomic_write_text(
        pot.weekly_state_path(), json.dumps(state, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    )


def build_summary(experience: dict, new_ready: list[str]) -> list[str]:
    pending = experience["pending"]
    lines = [
        f"Packing measurements outstanding: {len(pending)} opportunity(ies). "
        + pot.REVIEW_ONLY_BANNER,
    ]
    room = MAX_LINES - 2 - (1 if new_ready else 0)
    shown = pending[:max(0, room)]
    for row in shown:
        lines.append(
            f"  Woo order {row['order_id']}: {row['remaining_quantity']} of "
            f"{row['ordered_quantity']} x {row['sku']} ({row['group_id']}) not measured "
            f"[{row['opportunity_id']}]"
        )
    if len(pending) > len(shown):
        lines.append(f"  +{len(pending) - len(shown)} more. Run: packing_observation_tool.py pending")
    if new_ready:
        lines.append(
            f"Ready for estimate review ({pot.REVIEW_THRESHOLD_ORDERS}+ distinct single-piece "
            f"orders): {', '.join(new_ready)}"
        )
    lines.append("Send order number, SKU, units in the package, L x W x H cm, scale weight kg, "
                 "packing material and a photo reference.")
    return lines[:MAX_LINES]


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FRP Depot weekly packing-measurement reminder.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        experience = pot.build_experience(pot.load_catalog())
    except Exception as exc:  # noqa: BLE001 - a weekly nudge must not crash the cron
        print("Packing reminder could not read its local data: " + str(exc)[:200])
        return 0

    state = load_weekly_state()
    announced = set(state.get("announced_ready_groups") or [])
    ready = list(experience["groups_ready_for_review"])
    new_ready = [group for group in ready if group not in announced]

    if not experience["pending"] and not new_ready:
        return 0

    lines = build_summary(experience, new_ready)
    digest = pot.sha256_text("\n".join(lines))
    if digest == str(state.get("last_content_sha256") or ""):
        return 0

    if args.json:
        print(json.dumps({"lines": lines, "new_ready_groups": new_ready}, ensure_ascii=True,
                         indent=2, sort_keys=True))
    else:
        for line in lines:
            print(line)
    save_weekly_state({
        "last_content_sha256": digest,
        "last_alert_utc": pot.iso_utc(),
        "announced_ready_groups": sorted(announced | set(ready)),
        "pending_count": len(experience["pending"]),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
