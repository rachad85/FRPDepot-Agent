"""Dado's Google Analytics (GA4) READ tool - FRP Depot and, since 2026-07-24,
Troy Dualam.

Run:  python analytics_tool.py list
      python analytics_tool.py report  --property <id> [--days 28]
      python analytics_tool.py compare --property <id> [--days 28]

WHY THIS EXISTS / WHAT RACHAD DECIDED (2026-07-24):
Dado reported she could not produce FRP Depot conversion rates and blamed the
company wall. That diagnosis was WRONG and the evidence is worth keeping:
frpdepots.com has NO GA4 property at all. The only two GA4 properties in
Rachad's account are Troy Dualam's own:
    accounts/320963476 "Troy Dualam"          -> properties/449339383
    accounts/333650696 "Troy Dualam Services" -> properties/463861653
So no permission change could ever have produced an FRP conversion number -
the data is not being collected. FRP Depot DOES own Search Console
(sc-domain:frpdepots.com, siteOwner), so search metrics are available today;
only click->lead conversion is missing, and that needs a GA4 property created
and tagged on frpdepots.com first (GA4 has no retroactive data).

Shown that, Rachad chose separately and deliberately to grant Dado READ access
to Troy Dualam + Troy Dualam Services analytics so she can work TDI marketing
alongside Aze. That is a real amendment to Hard Rule 4, recorded in CLAUDE.md
and Dado's SOUL. Its LIMITS:
  - ANALYTICS/marketing metrics only. TDI mailbox remains separate. Drive and
    Zoho are unrestricted by Rachad's later instruction; the Gmail screen is
    deliberately not imported here.
  - READ-ONLY. Only Admin-API GETs and Data-API runReport (a read, despite
    being an HTTP POST). No property/stream/user administration, no writes.
  - CONTAINMENT. TDI figures must not enter FRP Depot's git history or the
    nightly conduct bundle. --save therefore writes ONLY to SAVE_DIR, which
    lives outside C:\\FRPDepot, and refuses to write anywhere else.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

ADMIN = "https://analyticsadmin.googleapis.com/v1beta"
DATA = "https://analyticsdata.googleapis.com/v1beta"
TOKEN_FILE = Path(os.environ["LOCALAPPDATA"]) / "FRPDepot-Google" / "extended_read_token.json"
# Outside the repo on purpose - see CONTAINMENT above.
SAVE_DIR = Path(os.environ["LOCALAPPDATA"]) / "FRPDepot-Google" / "analytics_reports"
REPO = Path(r"C:\FRPDepot")

DEFAULT_METRICS = ["sessions", "totalUsers", "screenPageViews", "engagementRate", "keyEvents"]
DEFAULT_DIMENSIONS = ["sessionDefaultChannelGroup"]


class AnalyticsError(Exception):
    pass


def get_token() -> str:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    if not TOKEN_FILE.exists():
        raise AnalyticsError(
            f"NO EXTENDED GOOGLE TOKEN at {TOKEN_FILE}. Nothing was read. "
            "Fix: double-click C:\\FRPDepot\\CONNECT_DADO_GOOGLE_READ_SERVICES.bat "
            "and sign in (Google expires it every 7 days).")
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
    if creds.expired and creds.refresh_token:
        from google.auth.exceptions import RefreshError
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise AnalyticsError(
                "Google sign-in EXPIRED (their 7-day limit on personal-account apps - "
                "normal, not a fault). Nothing was read. Fix: double-click "
                "C:\\FRPDepot\\CONNECT_DADO_GOOGLE_READ_SERVICES.bat") from exc
    if not creds.token:
        raise AnalyticsError("token file present but holds no usable access token")
    return creds.token


def _request(method: str, url: str, token: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise AnalyticsError(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {detail[:400]}") from e
    except urllib.error.URLError as e:
        raise AnalyticsError(f"{method} {url.split('?')[0]} -> {e.reason}") from e


def _window(days: int) -> tuple[str, str]:
    """GA4 counts today as incomplete; end yesterday for a clean window."""
    end = date.today() - timedelta(days=1)
    return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()


def _run_report(token: str, prop: str, body: dict) -> dict:
    """runReport, degrading honestly if a metric name is not available.

    GA4 renamed `conversions` to `keyEvents`; which one a property accepts
    depends on its API version history. Rather than fail the whole report,
    drop the offending metric and say so.
    """
    prop_id = prop.split("/")[-1]
    url = f"{DATA}/properties/{prop_id}:runReport"
    try:
        return _request("POST", url, token, body)
    except AnalyticsError as exc:
        msg = str(exc)
        dropped = [m["name"] for m in body.get("metrics", []) if m["name"] in msg]
        if not dropped:
            raise
        body = dict(body)
        body["metrics"] = [m for m in body["metrics"] if m["name"] not in dropped]
        if not body["metrics"]:
            raise
        out = _request("POST", url, token, body)
        out["_dropped_metrics"] = dropped
        return out


def _print_report(res: dict) -> None:
    heads = [h["name"] for h in res.get("dimensionHeaders", [])]
    heads += [h["name"] for h in res.get("metricHeaders", [])]
    rows = res.get("rows", [])
    if not rows:
        print("  (no rows - the property has no data in this window)")
        return
    print("  " + " | ".join(heads))
    for row in rows:
        vals = [d.get("value", "") for d in row.get("dimensionValues", [])]
        vals += [m.get("value", "") for m in row.get("metricValues", [])]
        print("  " + " | ".join(vals))
    if res.get("_dropped_metrics"):
        print(f"  NOTE: metric(s) not available on this property, omitted: "
              f"{', '.join(res['_dropped_metrics'])}")


def _save(name: str, payload: dict) -> Path:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    target = (SAVE_DIR / name).resolve()
    # Containment guard: never write TDI figures into the git-tracked tree.
    if REPO.resolve() in target.parents:
        raise AnalyticsError(f"refusing to save inside the repo: {target}")
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def command_list(args: argparse.Namespace) -> None:
    token = get_token()
    accounts = _request("GET", f"{ADMIN}/accounts", token).get("accounts", [])
    if not accounts:
        print("No GA4 accounts visible to this sign-in.")
        return
    for acct in accounts:
        print(f"{acct.get('name')}  |  {acct.get('displayName')}")
        props = _request("GET", f"{ADMIN}/properties"
                                f"?filter=parent:{acct.get('name')}&pageSize=200", token)
        for p in props.get("properties", []):
            print(f"    {p.get('name')}  |  {p.get('displayName')}")
        if not props.get("properties"):
            print("    (no properties)")


def command_report(args: argparse.Namespace) -> None:
    token = get_token()
    start, end = _window(args.days)
    metrics = args.metrics.split(",") if args.metrics else DEFAULT_METRICS
    dimensions = args.dimensions.split(",") if args.dimensions else DEFAULT_DIMENSIONS
    body = {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "metrics": [{"name": m} for m in metrics],
        "dimensions": [{"name": d} for d in dimensions],
        "limit": args.limit,
    }
    res = _run_report(token, args.property, body)
    print(f"property {args.property}  window {start}..{end} ({args.days}d)")
    _print_report(res)
    if args.save:
        print("saved: " + str(_save(f"report_{args.property.split('/')[-1]}_{end}.json", res)))


def command_compare(args: argparse.Namespace) -> None:
    token = get_token()
    start, end = _window(args.days)
    prior_end = date.fromisoformat(start) - timedelta(days=1)
    prior_start = prior_end - timedelta(days=args.days - 1)
    metrics = args.metrics.split(",") if args.metrics else DEFAULT_METRICS
    body = {
        "dateRanges": [{"startDate": start, "endDate": end, "name": "recent"},
                        {"startDate": prior_start.isoformat(), "endDate": prior_end.isoformat(),
                         "name": "prior"}],
        "metrics": [{"name": m} for m in metrics],
        "dimensions": [],
        "limit": 10,
    }
    res = _run_report(token, args.property, body)
    print(f"property {args.property}")
    print(f"  recent {start}..{end}   prior {prior_start}..{prior_end}")
    heads = [h["name"] for h in res.get("metricHeaders", [])]
    series = {}
    for row in res.get("rows", []):
        label = row.get("dimensionValues", [{}])[0].get("value", "?") if row.get("dimensionValues") else None
        label = label or ("recent" if "recent" not in series else "prior")
        series[label] = [m.get("value", "") for m in row.get("metricValues", [])]
    if len(series) < 2:
        _print_report(res)
        return
    rec, pri = series.get("recent"), series.get("prior")
    print(f"  {'metric':<22} {'recent':>14} {'prior':>14} {'change':>10}")
    for i, name in enumerate(heads):
        try:
            a, b = float(rec[i]), float(pri[i])
            chg = "n/a" if not b else f"{(a - b) / b * 100:+.1f}%"
            print(f"  {name:<22} {a:>14,.2f} {b:>14,.2f} {chg:>10}")
        except (ValueError, IndexError, TypeError):
            print(f"  {name:<22} {rec[i] if rec else '?':>14} {pri[i] if pri else '?':>14} {'':>10}")
    if res.get("_dropped_metrics"):
        print(f"  NOTE: metric(s) unavailable, omitted: {', '.join(res['_dropped_metrics'])}")
    if args.save:
        print("saved: " + str(_save(f"compare_{args.property.split('/')[-1]}_{end}.json", res)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    lst = commands.add_parser("list", help="List GA4 accounts and properties")
    lst.set_defaults(func=command_list)

    rep = commands.add_parser("report", help="Metrics broken down by dimension")
    rep.add_argument("--property", required=True, help="e.g. properties/449339383")
    rep.add_argument("--days", type=int, default=28)
    rep.add_argument("--metrics", help=f"comma list (default: {','.join(DEFAULT_METRICS)})")
    rep.add_argument("--dimensions", help=f"comma list (default: {','.join(DEFAULT_DIMENSIONS)})")
    rep.add_argument("--limit", type=int, default=25)
    rep.add_argument("--save", action="store_true", help="also write JSON outside the repo")
    rep.set_defaults(func=command_report)

    cmp_ = commands.add_parser("compare", help="This period vs the previous one")
    cmp_.add_argument("--property", required=True)
    cmp_.add_argument("--days", type=int, default=28)
    cmp_.add_argument("--metrics")
    cmp_.add_argument("--save", action="store_true")
    cmp_.set_defaults(func=command_compare)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (AnalyticsError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
