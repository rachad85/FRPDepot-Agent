"""Audit which Google read services the current Dado token can access.
Never prints the token or any record content. Writes only status codes/reasons.
"""
from __future__ import annotations
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import os

sys.path.insert(0, str(Path(__file__).resolve().parent))
import google_auth

OUT = Path(os.environ["LOCALAPPDATA"]) / "FRPDepot-Google" / "reference" / "google_service_access.json"
TESTS = {
    "Gmail": "https://gmail.googleapis.com/gmail/v1/users/me/profile",
    "Drive": "https://www.googleapis.com/drive/v3/about?fields=user",
    "Google Analytics Admin": "https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageSize=1",
    "Google Calendar": "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1",
    "Google Contacts/People": "https://people.googleapis.com/v1/people/me?personFields=names",
    "Google Search Console": "https://www.googleapis.com/webmasters/v3/sites",
}


def main() -> int:
    token = google_auth.get_token(interactive=False)
    results = {}
    for name, url in TESTS.items():
        req = urllib.request.Request(url)
        req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                response.read(1)
                results[name] = {"accessible": True, "http": response.status}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            reason = "HTTP_ERROR"
            try:
                payload = json.loads(raw)
                reason = payload.get("error", {}).get("status") or payload.get("error", {}).get("message") or reason
            except Exception:
                if "insufficient" in raw.lower() or "scope" in raw.lower():
                    reason = "INSUFFICIENT_PERMISSIONS"
            results[name] = {"accessible": False, "http": exc.code, "reason": str(reason)[:160]}
        except Exception as exc:
            results[name] = {"accessible": False, "reason": type(exc).__name__}
    payload = {"checked_at": datetime.now(timezone.utc).isoformat(), "account_expected": "rachad85@gmail.com", "services": results}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
