"""One-time Google sign-in for Dado's personal-Gmail + personal-Drive tools.

Run:  python google_tool.py connect     (or double-click CONNECT_DADO_GOOGLE.bat)
      python google_tool.py check       (silent verify, no browser - or CHECK_DADO_GOOGLE.bat)
A browser opens on connect; Rachad signs in with his personal Google account
and ticks the permission boxes. The token lands OUTSIDE C:\\FRPDepot, in
%LOCALAPPDATA%\\FRPDepot-Google\\token.json - never synced, never in chat.

Adapted 2026-07-24 from Aze's C:\\AgentTeam\\Aze\\Tools\\google\\google_auth.py
(sanctioned pattern reuse - the code shape only; no shared credentials, no
shared token). Rachad asked to copy Aze's live google_client.json/token.json
across to Dado; that was declined (crosses the hard company wall two ways -
see tdi_filter.py's docstring) in favor of Dado holding its OWN,
independently-registered OAuth client and minting its OWN token, fully
revocable without touching Aze's sign-in. See CLAUDE.md state section,
2026-07-24 entry.

SCOPES (Rachad approved 2026-07-24, "read+draft, TDI filtered" - narrower
than Aze's, matching Dado's drafts-only / read-only-until-commissioned house
rules):
  gmail.readonly  - read inbox/messages
  gmail.compose   - CREATE DRAFTS. Google offers no draft-only scope; this
                    one also technically permits sending. Drafts-only is
                    enforced in google_tool.py (no send function exists
                    anywhere in this tree), NOT at the token level - the one
                    place this token is technically send-capable. Golden
                    Rule 1 (DRAFTS ONLY) means that gap must never be closed
                    by adding a send path here, ever.
  drive.readonly  - read Drive files only. No edit/create/delete/share scope
                    (narrower than Aze's full "drive" scope) - Drive write
                    access would follow the same commissioning rule as Zoho
                    (Golden Rule 3): read-only until Rachad names a write
                    tool he actually wants built.
Every result returned through google_tool.py is screened by tdi_filter.py
before Dado ever sees it, logs it, or lets it into a receipt.

KNOWN GOOGLE LIMIT (their rule, not ours): personal-account apps run in
"Testing" mode, and Google expires the sign-in after 7 days. When the tools
say the sign-in expired, double-click CONNECT_DADO_GOOGLE.bat again - one
click, back for another week. This is normal, not a fault.
"""
import glob
import json
import os
from datetime import datetime
import shutil
import socket
import sys
import urllib.request

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/drive.readonly",
]
VAULT = os.path.join(os.environ["LOCALAPPDATA"], "FRPDepot-Google")
# App key lives next to the tools (same convention as Aze's); it is
# git-ignored so it never syncs. The TOKEN (the actual sign-in) stays in the
# hidden vault and is never written into the project tree.
CLIENT_FILE = r"C:\FRPDepot\Dado\Tools\google\google_client.json"
TOKEN_FILE = os.path.join(VAULT, "token.json")
PC = (os.environ.get("COMPUTERNAME") or socket.gethostname() or "?").upper()
GUIDE = r"C:\FRPDepot\Dado\Tools\google\SETUP_GUIDE_for_Rachad.md"
RECONNECT = ("Fix: double-click C:\\FRPDepot\\CONNECT_DADO_GOOGLE.bat and "
             "sign in (takes ~30 seconds; first time ever needs the one-time "
             "setup in Dado\\Tools\\google\\SETUP_GUIDE_for_Rachad.md).")

SCOPE_NAMES = {
    "https://www.googleapis.com/auth/gmail.readonly": "Gmail read",
    "https://www.googleapis.com/auth/gmail.compose": "Gmail drafts",
    "https://www.googleapis.com/auth/drive.readonly": "Drive read",
}


def _adopt_downloaded_client_file():
    """First run: pull the freshly downloaded client_secret*.json out of
    Downloads into the vault, so Rachad never has to move files by hand."""
    if os.path.exists(CLIENT_FILE):
        return
    downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    candidates = sorted(glob.glob(os.path.join(downloads, "client_secret*.json")),
                        key=os.path.getmtime, reverse=True)
    if not candidates:
        return
    os.makedirs(os.path.dirname(CLIENT_FILE), exist_ok=True)
    shutil.move(candidates[0], CLIENT_FILE)
    print(f"Found your downloaded key file and moved it into the vault:\n"
          f"  {candidates[0]}  ->  {CLIENT_FILE}")


def _check_client_file():
    if not os.path.exists(CLIENT_FILE):
        sys.exit(
            f"NO GOOGLE KEY FILE on {PC}.\n"
            f"Expected: {CLIENT_FILE}\n"
            f"This is the one-time setup - follow the numbered steps in:\n"
            f"  {GUIDE}\n"
            "(Download the JSON from Google, then just re-run this - it is "
            "picked up from Downloads automatically.)")
    data = json.load(open(CLIENT_FILE, "r", encoding="utf-8"))
    if "installed" not in data:
        sys.exit(
            "The key file is the WRONG TYPE (not a 'Desktop app' client).\n"
            "In Google Cloud -> Credentials, create an OAuth client ID with "
            "application type 'Desktop app', download its JSON, and re-run. "
            f"Guide: {GUIDE}")


PRODUCTION_SWITCH_DATE = "2026-07-24"  # when the OAuth app left "Testing"
# Written ONLY when a browser consent actually issues a new refresh token.
MINTED_FILE = TOKEN_FILE + ".minted"


def _record_mint() -> None:
    """Stamp when a refresh token was genuinely ISSUED.

    The token file's own mtime cannot answer this: _save() rewrites it on every
    silent refresh too, so an old grant looks freshly minted. A refresh token's
    lifetime is fixed when it is issued and refreshing does not reset it, so the
    issue date is the only thing that says whether the "In production" switch
    applies to this credential.
    """
    try:
        with open(MINTED_FILE, "w", encoding="utf-8") as fh:
            fh.write(datetime.now().strftime("%Y-%m-%d"))
    except OSError:
        pass


def _minted_date() -> str | None:
    """The recorded issue date, or None when we genuinely do not know."""
    try:
        with open(MINTED_FILE, encoding="utf-8") as fh:
            value = fh.read().strip()
    except OSError:
        return None
    return value if len(value) == 10 else None


def _token_age_note() -> str:
    """' (issued 2026-07-20)' - or '' when the issue date was never recorded."""
    minted = _minted_date()
    return f" (issued {minted})" if minted else ""


def get_creds(interactive=False, force=False):
    """Silent (load + refresh) first; browser sign-in only when interactive.

    force=True skips every reuse path and always re-consents in a browser.
    Needed because "connect" returns early when the stored token is still
    valid - which silently defeated the 2026-07-24 move to "In production".
    That switch only lengthens tokens MINTED AFTER it; refreshing an old one
    does not reset its 7-day clock. See google_extended_auth.get_creds for the
    full incident note. Use: python google_tool.py reconnect
    """
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if os.path.exists(TOKEN_FILE) and not force:
        try:
            # Load WITHOUT forcing SCOPES: we want creds.scopes to reflect what
            # was actually GRANTED, not what we now request, so the coverage
            # check below is truthful after a scope change.
            creds = Credentials.from_authorized_user_file(TOKEN_FILE)
        except ValueError:
            creds = None

    scope_ok = bool(creds) and bool(creds.scopes) and creds.has_scopes(SCOPES)

    if creds and creds.valid and scope_ok and not force:
        if interactive:
            # Say so. `connect` returning early looks identical to a successful
            # sign-in from the operator's side - no browser opens and the tool
            # prints VERIFIED - which is exactly how the stale pre-production
            # token survived a deliberate attempt to replace it on 2026-07-24.
            print("Reused the sign-in already stored on this PC"
                  + _token_age_note()
                  + ". No browser opened, so NOTHING was replaced.")
            print("To mint a genuinely NEW token, run RECONNECT_DADO_GOOGLE.bat.")
        return creds
    if creds and creds.expired and creds.refresh_token and scope_ok:
        try:
            creds.refresh(Request())
            _save(creds)
            return creds
        except RefreshError:
            if not interactive:
                sys.exit(f"Google sign-in EXPIRED on {PC} (Google limits "
                         f"personal-account apps to 7 days per sign-in - "
                         f"normal, not a fault). Nothing was changed. "
                         + RECONNECT)
    if creds and not scope_ok and not interactive:
        sys.exit(f"Google permissions on {PC} must be RE-GRANTED. Nothing "
                 f"was changed. " + RECONNECT)
    if not interactive:
        sys.exit(f"NO GOOGLE TOKEN on {PC}. Dado cannot use Gmail or Drive "
                 f"here until Rachad signs in once. " + RECONNECT)

    from google_auth_oauthlib.flow import InstalledAppFlow
    _adopt_downloaded_client_file()
    _check_client_file()
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, SCOPES)
    creds = flow.run_local_server(
        port=0,
        authorization_prompt_message="If no browser opened, paste this link "
                                     "into one:\n{url}\n",
        success_message="Signed in. You can close this browser tab and go "
                        "back to the black window.",
        # BOTH are load-bearing; google_auth_oauthlib defaults access_type to
        # "offline" but sets no prompt at all. Without prompt="consent" Google
        # AUTO-APPROVES a re-authorization of scopes Rachad already granted to
        # dado-frpd and returns refresh_token=null - which _save() would then
        # write over the working token. Sign-in "succeeds", and about an hour
        # later every non-interactive caller (google_indexer, google_backfill,
        # google_service_audit, every google_tool command) dies at once with no
        # way back except a hand-deleted token file. That would make `reconnect`
        # - the command built to CURE a stale token - the one most likely to
        # destroy access. google_extended_auth.py has always passed both.
        access_type="offline",
        prompt="consent")
    if not creds.refresh_token:
        # Belt and braces behind prompt="consent". Writing a credential with no
        # refresh token over a working one is the failure that kills every
        # non-interactive caller an hour later; refuse rather than save it.
        sys.exit("Google returned a sign-in with NO refresh token, so it would "
                 "stop working within the hour. The existing token was left "
                 "untouched. Try RECONNECT_DADO_GOOGLE.bat again and make sure "
                 "you approve the consent screen rather than being skipped past "
                 "it.")
    _save(creds)
    _record_mint()   # the ONE place a refresh token is genuinely issued
    granted = set(creds.scopes or [])
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        print("WARNING: you did not tick this box: "
              + ", ".join(SCOPE_NAMES[s] for s in missing)
              + ". Re-run CONNECT_DADO_GOOGLE.bat and tick every box.")
    return creds


def _save(creds):
    os.makedirs(VAULT, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())


def get_token(interactive=False):
    return get_creds(interactive=interactive).token


def _get(token, url):
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def self_check(interactive, force=False):
    creds = get_creds(interactive=interactive, force=force)
    token = creds.token
    granted = set(creds.scopes or [])
    ok = True
    try:
        prof = _get(token, "https://gmail.googleapis.com/gmail/v1/users/me/profile")
        can_draft = "https://www.googleapis.com/auth/gmail.compose" in granted
        print(f"GMAIL OK - signed in as {prof.get('emailAddress')} "
              f"({prof.get('messagesTotal'):,} messages). "
              f"Drafting: {'ON (drafts only - no send path in the tools)' if can_draft else 'OFF'}.")
    except Exception as e:
        ok = False
        print(f"Gmail check FAILED: {e} - did you tick the Gmail boxes?")
    try:
        about = _get(token, "https://www.googleapis.com/drive/v3/about?fields=user")
        u = about.get("user", {})
        print(f"DRIVE OK - signed in as {u.get('emailAddress')}. Read-only, by design.")
    except Exception as e:
        ok = False
        print(f"Drive check FAILED: {e} - did you tick the Drive box?")
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        ok = False
        print("WARNING: you did not grant: "
              + ", ".join(SCOPE_NAMES.get(s, s) for s in missing)
              + ". Re-run CONNECT_DADO_GOOGLE.bat and tick every box.")
    if ok:
        print("SIGNED IN OK. Token vault: " + TOKEN_FILE)
        # Only claim "long-lived" about a token we JUST MINTED. The claim is
        # about the OAuth app's publishing status, not about this credential, so
        # printing it on `check` or on a reuse path asserted a property of a
        # token the code had never seen issued - the same class of false
        # assurance the reconnect work existed to remove, and it had replaced the
        # previously correct 7-day reminder.
        minted = _minted_date()
        if minted is None:
            print("This sign-in's issue date was never recorded, so its lifetime "
                  "cannot be stated here. If any tool reports the sign-in expired, "
                  "run RECONNECT_DADO_GOOGLE.bat once.")
        elif minted < PRODUCTION_SWITCH_DATE:
            print(f"WARNING: this sign-in{_token_age_note()} predates the move to "
                  f'"In production" on {PRODUCTION_SWITCH_DATE}, so it STILL '
                  f"expires 7 days after it was issued. Run "
                  f"RECONNECT_DADO_GOOGLE.bat once to replace it.")
        else:
            print(f"Sign-in{_token_age_note()} should be long-lived - it was issued "
                  f"after the app moved to \"In production\".")
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "connect":
        result_ok = self_check(interactive=True)
    elif cmd == "check":
        result_ok = self_check(interactive=False)
    elif cmd == "reconnect":
        result_ok = self_check(interactive=True, force=True)
    else:
        sys.exit(f"Unknown command {cmd!r}. Use 'connect', 'check', or 'reconnect'.")
    sys.exit(0 if result_ok else 1)
