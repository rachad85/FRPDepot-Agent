"""Read-only FRP Depot Search Console performance report.

Uses the separately authorized Search Console read-only token. The Search
Analytics endpoint uses POST for a read query; it cannot change Search Console.
TDI-flagged rows are discarded before storage or output.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import google_extended_auth
from tdi_filter import is_tdi_flagged

SITE = "sc-domain:frpdepots.com"
OUT_DIR = Path.home() / "AppData" / "Local" / "FRPDepot-Google" / "reference"
RECEIPTS = Path(r"C:\FRPDepot\Dado\40_Logs\receipts.jsonl")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    with RECEIPTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": now(), "action": action, "evidence": evidence}) + "\n")


def api_query(token: str, start: date, end: date, dimensions: list[str] | None = None,
              row_limit: int = 1) -> list[dict]:
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "rowLimit": row_limit}
    if dimensions:
        body["dimensions"] = dimensions
    url = ("https://www.googleapis.com/webmasters/v3/sites/" + quote(SITE, safe="")
           + "/searchAnalytics/query")
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as response:
        rows = json.loads(response.read() or b"{}").get("rows", [])
    safe = []
    for row in rows:
        keys = [str(x) for x in row.get("keys", [])]
        if is_tdi_flagged(*keys):
            continue
        safe.append(row)
    return safe


def metric(row: dict | None) -> dict:
    row = row or {}
    return {
        "clicks": float(row.get("clicks") or 0),
        "impressions": float(row.get("impressions") or 0),
        "ctr": float(row.get("ctr") or 0),
        "position": float(row.get("position") or 0),
    }


def pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current - previous) / previous * 100


def keyed(rows: list[dict]) -> dict[str, dict]:
    return {str((row.get("keys") or [""])[0]): metric(row) for row in rows}


def compact_row(key: str, m: dict, previous: dict | None = None) -> dict:
    out = {
        "key": key,
        "clicks": round(m["clicks"], 0),
        "impressions": round(m["impressions"], 0),
        "ctr_pct": round(m["ctr"] * 100, 2),
        "position": round(m["position"], 2),
    }
    if previous is not None:
        out["click_change"] = round(m["clicks"] - previous.get("clicks", 0), 0)
        out["click_change_pct"] = (None if previous.get("clicks", 0) == 0 else
                                   round(pct_change(m["clicks"], previous["clicks"]), 1))
    return out


def main() -> int:
    creds = google_extended_auth.get_creds(False)
    token = creds.token

    # Search Console commonly lags 2-3 days; end three days before today.
    current_end = date.today() - timedelta(days=3)
    current_start = current_end - timedelta(days=89)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=89)

    current_total = metric((api_query(token, current_start, current_end) or [{}])[0])
    previous_total = metric((api_query(token, previous_start, previous_end) or [{}])[0])
    current_pages = keyed(api_query(token, current_start, current_end, ["page"], 25000))
    previous_pages = keyed(api_query(token, previous_start, previous_end, ["page"], 25000))
    current_queries = keyed(api_query(token, current_start, current_end, ["query"], 25000))
    previous_queries = keyed(api_query(token, previous_start, previous_end, ["query"], 25000))
    devices = keyed(api_query(token, current_start, current_end, ["device"], 20))

    top_pages = sorted(current_pages.items(), key=lambda x: x[1]["clicks"], reverse=True)[:15]
    page_losses = []
    for key, prev in previous_pages.items():
        if prev["clicks"] < 10:
            continue
        cur = current_pages.get(key, {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0})
        if cur["clicks"] < prev["clicks"]:
            page_losses.append((key, cur, prev))
    page_losses.sort(key=lambda x: x[1]["clicks"] - x[2]["clicks"])

    query_opportunities = []
    for key, cur in current_queries.items():
        if cur["impressions"] >= 100 and 3 <= cur["position"] <= 20 and cur["ctr"] < current_total["ctr"]:
            query_opportunities.append((key, cur, previous_queries.get(key)))
    query_opportunities.sort(key=lambda x: (-x[1]["impressions"], x[1]["ctr"]))

    changes = {
        name: (None if pct_change(current_total[name], previous_total[name]) is None else
               round(pct_change(current_total[name], previous_total[name]), 1))
        for name in ["clicks", "impressions", "ctr", "position"]
    }
    # Position is lower-is-better, so also expose the point movement directly.
    changes["position_points"] = round(current_total["position"] - previous_total["position"], 2)

    report = {
        "generated_at": now(),
        "source": "Google Search Console read-only API",
        "site": SITE,
        "current_period": {"start": current_start.isoformat(), "end": current_end.isoformat()},
        "previous_period": {"start": previous_start.isoformat(), "end": previous_end.isoformat()},
        "current": {
            "clicks": round(current_total["clicks"], 0),
            "impressions": round(current_total["impressions"], 0),
            "ctr_pct": round(current_total["ctr"] * 100, 2),
            "average_position": round(current_total["position"], 2),
        },
        "previous": {
            "clicks": round(previous_total["clicks"], 0),
            "impressions": round(previous_total["impressions"], 0),
            "ctr_pct": round(previous_total["ctr"] * 100, 2),
            "average_position": round(previous_total["position"], 2),
        },
        "changes_pct": changes,
        "devices": [compact_row(k, v) for k, v in sorted(devices.items(), key=lambda x: x[1]["clicks"], reverse=True)],
        "top_pages": [compact_row(k, v, previous_pages.get(k, {})) for k, v in top_pages],
        "largest_page_click_losses": [compact_row(k, cur, prev) for k, cur, prev in page_losses[:15]],
        "high_impression_low_ctr_queries": [compact_row(k, cur, prev) for k, cur, prev in query_opportunities[:25]],
        "limits": [
            "Search Console measures Google-search visibility and clicks, not website conversions or revenue.",
            "Conversion-rate conclusions require a non-TDI Google Analytics property for FRP Depot.",
            "TDI-flagged rows were discarded before this report was stored.",
        ],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / f"frp_search_console_{current_end.isoformat()}.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    receipt("frp_search_console_performance_report_written", str(json_path))

    # Print only the compact decision data; the full page/query lists remain local.
    compact = {
        "report": str(json_path),
        "current_period": report["current_period"],
        "previous_period": report["previous_period"],
        "current": report["current"],
        "previous": report["previous"],
        "changes_pct": report["changes_pct"],
        "devices": report["devices"],
        "top_pages": report["top_pages"][:10],
        "largest_page_click_losses": report["largest_page_click_losses"][:5],
        "high_impression_low_ctr_queries": report["high_impression_low_ctr_queries"][:10],
    }
    print(json.dumps(compact, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
