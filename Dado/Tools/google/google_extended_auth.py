"""Separate read-only Google connection for Dado's reference services.

Services: Google Analytics, Calendar, Contacts/People, and Search Console.
This uses its own token so the existing Gmail/Drive connection remains intact.
No write, send, delete, sharing, or account-administration scopes are requested.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tdi_filter import is_tdi_flagged

EXPECTED_ACCOUNT = "rachad85@gmail.com"
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]
SCOPE_NAMES = {
    SCOPES[0]: "Google sign-in identity",
    SCOPES[1]: "Google account identity (email only)",
    SCOPES[2]: "Google Analytics read-only",
    SCOPES[3]: "Google Calendar read-only",
    SCOPES[4]: "Google Contacts read-only",
    SCOPES[5]: "Google Search Console read-only",
}
VAULT = Path(os.environ["LOCALAPPDATA"]) / "FRPDepot-Google"
TOKEN_FILE = VAULT / "extended_read_token.json"
AUDIT_FILE = VAULT / "reference" / "google_extended_service_access.json"
CLIENT_FILE = Path(r"C:\FRPDepot\Dado\Tools\google\google_client.json")
RECEIPTS = Path(r"C:\FRPDepot\Dado\40_Logs\receipts.jsonl")
PC = (os.environ.get("COMPUTERNAME") or socket.gethostname() or "?").upper()
CONNECT_BUTTON = r"C:\FRPDepot\CONNECT_DADO_GOOGLE_READ_SERVICES.bat"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    with RECEIPTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": now(), "action": action, "evidence": evidence}) + "\n")


def _check_client_file() -> None:
    if not CLIENT_FILE.exists():
        raise SystemExit(
            "Google OAuth client is missing. Expected the existing Dado client at: "
            + str(CLIENT_FILE)
        )
    data = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
    if "installed" not in data:
        raise SystemExit("The existing Google OAuth client is not a Desktop app client.")


def _save(creds) -> None:
    VAULT.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")


def get_creds(interactive: bool = False, force: bool = False):
    """force=True skips every reuse path and always re-consents in a browser.

    WHY THIS EXISTS (2026-07-24): Rachad moved the OAuth app from "Testing" to
    "In production" to kill Google's 7-day sign-in expiry. That only helps
    tokens MINTED AFTER the switch - a refresh token's lifetime is fixed when
    it is issued, and refreshing it does not reset the clock. He double-clicked
    the connect button, the branch below found the stored token still valid,
    returned early WITHOUT opening a browser, and printed "VERIFIED" - so the
    old 7-day token stayed in place while the output implied success. The only
    reliable cure was deleting the token file by hand. Use force instead:
        python google_extended_auth.py reconnect
    """
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if TOKEN_FILE.exists() and not force:
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
        except ValueError:
            creds = None
    scope_ok = bool(creds) and bool(creds.scopes) and creds.has_scopes(SCOPES)
    if creds and creds.valid and scope_ok and not force:
        return creds
    if creds and creds.expired and creds.refresh_token and scope_ok:
        try:
            creds.refresh(Request())
            _save(creds)
            return creds
        except RefreshError:
            if not interactive:
                raise SystemExit(
                    "Extended Google read sign-in expired. Double-click " + CONNECT_BUTTON
                )
    if not interactive:
        raise SystemExit(
            "Extended Google read permissions are not connected. Double-click " + CONNECT_BUTTON
        )

    from google_auth_oauthlib.flow import InstalledAppFlow
    _check_client_file()
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    creds = flow.run_local_server(
        port=0,
        authorization_prompt_message="A browser is opening for Rachad's read-only Google authorization.\n{url}\n",
        success_message="Read-only authorization saved. You may close this browser tab.",
        access_type="offline",
        prompt="consent",
    )
    _save(creds)
    receipt("google_extended_read_authorization_saved", str(TOKEN_FILE))
    return creds


def _reason(raw: str) -> str:
    try:
        err = json.loads(raw).get("error", {})
        for detail in err.get("details", []) or []:
            for item in detail.get("metadata", {}), detail:
                reason = item.get("reason") if isinstance(item, dict) else None
                if reason:
                    return str(reason)[:120]
        return str(err.get("status") or err.get("message") or "HTTP_ERROR")[:120]
    except Exception:
        low = raw.lower()
        if "insufficient" in low or "scope" in low:
            return "INSUFFICIENT_PERMISSIONS"
        return "HTTP_ERROR"


def _get(token: str, url: str) -> tuple[bool, int, dict, str]:
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return True, response.status, json.loads(response.read() or b"{}"), ""
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return False, exc.code, {}, _reason(raw)
    except Exception as exc:
        return False, 0, {}, type(exc).__name__


def _screen_count(records: list[dict], *fields: str) -> tuple[int, int]:
    safe = withheld = 0
    for record in records:
        values = [str(record.get(name) or "") for name in fields]
        if is_tdi_flagged(*values):
            withheld += 1
        else:
            safe += 1
    return safe, withheld


def self_check(interactive: bool, force: bool = False) -> bool:
    creds = get_creds(interactive=interactive, force=force)
    token = creds.token
    granted = set(creds.scopes or [])
    missing = [SCOPE_NAMES[s] for s in SCOPES if s not in granted]
    results: dict[str, dict] = {}

    ok, http, data, reason = _get(token, "https://www.googleapis.com/oauth2/v2/userinfo")
    email = (data.get("email") or "").lower() if ok else ""
    identity_ok = ok and email == EXPECTED_ACCOUNT
    results["Account identity"] = {
        "accessible": identity_ok,
        "http": http,
        "account_verified": identity_ok,
        "reason": "" if identity_ok else (reason or "WRONG_GOOGLE_ACCOUNT"),
    }

    endpoint = "https://analyticsadmin.googleapis.com/v1beta/accountSummaries?" + urlencode({"pageSize": 200})
    ok, http, data, reason = _get(token, endpoint)
    records = data.get("accountSummaries", []) if ok else []
    safe, withheld = _screen_count(records, "displayName", "account") if ok else (0, 0)
    results["Google Analytics"] = {"accessible": ok, "http": http, "safe_account_summaries": safe,
                                     "withheld_tdi_flagged": withheld, "reason": reason}

    endpoint = "https://www.googleapis.com/calendar/v3/users/me/calendarList?" + urlencode({"maxResults": 250})
    ok, http, data, reason = _get(token, endpoint)
    records = data.get("items", []) if ok else []
    safe, withheld = _screen_count(records, "summary", "description") if ok else (0, 0)
    results["Google Calendar"] = {"accessible": ok, "http": http, "safe_calendars": safe,
                                    "withheld_tdi_flagged": withheld, "reason": reason}

    endpoint = "https://people.googleapis.com/v1/people/me/connections?" + urlencode({
        "pageSize": 1, "personFields": "names,emailAddresses"
    })
    ok, http, data, reason = _get(token, endpoint)
    # Do not retain or print the sampled contact. Only the aggregate supplied by Google.
    total_people = int(data.get("totalPeople") or 0) if ok else 0
    results["Google Contacts"] = {"accessible": ok, "http": http, "reported_contact_count": total_people,
                                    "reason": reason}

    ok, http, data, reason = _get(token, "https://www.googleapis.com/webmasters/v3/sites")
    records = data.get("siteEntry", []) if ok else []
    safe, withheld = _screen_count(records, "siteUrl") if ok else (0, 0)
    results["Google Search Console"] = {"accessible": ok, "http": http, "safe_sites": safe,
                                          "withheld_tdi_flagged": withheld, "reason": reason}

    all_ok = identity_ok and not missing and all(
        results[name]["accessible"] for name in
        ["Google Analytics", "Google Calendar", "Google Contacts", "Google Search Console"]
    )
    payload = {
        "checked_at": now(),
        "expected_account": EXPECTED_ACCOUNT,
        "read_only_scopes": [SCOPE_NAMES[s] for s in SCOPES],
        "missing_grants": missing,
        "services": results,
        "all_services_ready": all_ok,
    }
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    receipt("google_extended_service_access_audited", str(AUDIT_FILE))

    print(f"ACCOUNT: {'VERIFIED ' + EXPECTED_ACCOUNT if identity_ok else 'NOT VERIFIED'}")
    for name in ["Google Analytics", "Google Calendar", "Google Contacts", "Google Search Console"]:
        item = results[name]
        if item["accessible"]:
            print(f"{name}: READ-ONLY ACCESS OK")
        else:
            print(f"{name}: BLOCKED - HTTP {item['http']} {item['reason']}")
    if missing:
        print("MISSING GRANTS: " + ", ".join(missing))
    if all_ok:
        print("ALL EXTENDED GOOGLE READ SERVICES VERIFIED. No write scopes were requested.")
    else:
        print("AUTHORIZATION MAY BE SAVED, but one or more Google APIs still require enabling or permission.")
    return all_ok


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "connect":
        return 0 if self_check(interactive=True) else 1
    if command == "check":
        return 0 if self_check(interactive=False) else 1
    if command == "reconnect":
        # Always re-consents in a browser, even when the stored token still
        # looks fine. See get_creds() for why "connect" alone is not enough.
        return 0 if self_check(interactive=True, force=True) else 1
    raise SystemExit("Use 'connect', 'check', or 'reconnect'.")


if __name__ == "__main__":
    raise SystemExit(main())
