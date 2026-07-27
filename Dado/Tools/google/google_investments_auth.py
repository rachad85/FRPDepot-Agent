"""Separate OAuth connection for the named Investments workbook write tool.

This token requests only full Google Drive access. It does not carry Gmail,
Calendar, Contacts, Analytics, or Search Console permission. The named caller
must still enforce its exact-file and exact-operation allowlist.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
EXPECTED_ACCOUNT = "rachad85@gmail.com"
ROOT = Path(r"C:\FRPDepot")
CLIENT_FILE = ROOT / "Dado" / "Tools" / "google" / "google_client.json"
VAULT = Path(os.environ["LOCALAPPDATA"]) / "FRPDepot-Google-Investments-Write"
TOKEN_FILE = VAULT / "token.json"
GRANT_FILE = VAULT / "grant.json"
PC = (os.environ.get("COMPUTERNAME") or socket.gethostname() or "?").upper()


class InvestmentsAuthError(RuntimeError):
    pass


def _scope_set(creds) -> set[str]:
    return {str(scope) for scope in (getattr(creds, "scopes", None) or [])}


def _exact_requested_scope(creds) -> bool:
    return _scope_set(creds) == {DRIVE_SCOPE}


def _granted_scope_set(creds) -> set[str] | None:
    granted = getattr(creds, "granted_scopes", None)
    if granted is None:
        return None
    return {str(scope) for scope in granted}


def _exact_new_grant(creds) -> bool:
    return _granted_scope_set(creds) == {DRIVE_SCOPE}


def _refresh_digest(creds) -> str:
    token = str(getattr(creds, "refresh_token", None) or "")
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _account_email(creds) -> str:
    """Validate a newly granted credential before it is allowed into the vault."""
    from googleapiclient.discovery import build

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return str(
        service.about().get(fields="user(emailAddress)").execute(num_retries=3)
        .get("user", {}).get("emailAddress", "")
    ).casefold()


def _client_check() -> None:
    if not CLIENT_FILE.exists():
        raise InvestmentsAuthError(
            f"Google desktop-app client is missing on {PC}: {CLIENT_FILE}"
        )
    try:
        raw = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvestmentsAuthError("Google desktop-app client file is unreadable.") from exc
    if "installed" not in raw:
        raise InvestmentsAuthError("Google OAuth client must be a Desktop app client.")


def _save(creds) -> None:
    VAULT.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix="token-", suffix=".tmp", dir=VAULT)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(creds.to_json())
        os.replace(temp_name, TOKEN_FILE)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _save_grant(creds, email: str) -> None:
    VAULT.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "account": email,
        "actual_granted_scopes": [DRIVE_SCOPE],
        "refresh_token_sha256": _refresh_digest(creds),
        "client_id": str(getattr(creds, "client_id", None) or ""),
    }
    descriptor, temp_name = tempfile.mkstemp(prefix="grant-", suffix=".tmp", dir=VAULT)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, indent=2) + "\n")
        os.replace(temp_name, GRANT_FILE)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _saved_grant_ok(creds) -> bool:
    try:
        record = json.loads(GRANT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(record, dict) or set(record) != {
        "schema_version", "account", "actual_granted_scopes",
        "refresh_token_sha256", "client_id",
    }:
        return False
    return bool(
        record.get("schema_version") == 1
        and record.get("account") == EXPECTED_ACCOUNT
        and record.get("actual_granted_scopes") == [DRIVE_SCOPE]
        and str(record.get("client_id") or "") == str(getattr(creds, "client_id", None) or "")
        and _refresh_digest(creds)
        and secrets.compare_digest(
            str(record.get("refresh_token_sha256") or ""), _refresh_digest(creds)
        )
    )


def get_creds(*, interactive: bool = False, force: bool = False):
    """Load/refresh silently, or perform explicit browser consent when requested."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if TOKEN_FILE.exists() and not force:
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
        except ValueError:
            creds = None
    scope_ok = bool(creds and _exact_requested_scope(creds) and _saved_grant_ok(creds))
    if creds and creds.valid and scope_ok and not force:
        return creds
    if creds and creds.expired and creds.refresh_token and scope_ok and not force:
        try:
            creds.refresh(Request())
            refreshed_grants = _granted_scope_set(creds)
            if refreshed_grants is not None and refreshed_grants != {DRIVE_SCOPE}:
                raise InvestmentsAuthError(
                    "The refreshed token has permissions outside the exact Drive grant. Nothing was saved."
                )
            email = _account_email(creds)
            if email != EXPECTED_ACCOUNT:
                raise InvestmentsAuthError(
                    f"Wrong Google account after refresh ({email or 'unknown'}). Nothing was saved."
                )
            _save(creds)
            _save_grant(creds, email)
            return creds
        except RefreshError:
            if not interactive:
                raise InvestmentsAuthError(
                    "Investments write sign-in expired. Double-click "
                    "CONNECT_DADO_INVESTMENTS_WRITE.bat."
                )
    if not interactive:
        raise InvestmentsAuthError(
            "Investments write access is not connected. Double-click "
            "CONNECT_DADO_INVESTMENTS_WRITE.bat."
        )

    _client_check()
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), [DRIVE_SCOPE])
    new_creds = flow.run_local_server(
        port=0,
        authorization_prompt_message=(
            "If no browser opened, paste this link into one:\n{url}\n"
        ),
        success_message="Investments write access connected. You may close this tab.",
        access_type="offline",
        prompt="consent",
    )
    if not new_creds.refresh_token:
        raise InvestmentsAuthError(
            "Google returned no refresh token. The existing token was left untouched. "
            "Run the connection button again and approve the consent screen."
        )
    if not _exact_requested_scope(new_creds) or not _exact_new_grant(new_creds):
        raise InvestmentsAuthError(
            "Google did not confirm an exact Drive-only grant. Nothing was saved."
        )
    email = _account_email(new_creds)
    if email != EXPECTED_ACCOUNT:
        raise InvestmentsAuthError(
            f"Wrong Google account ({email or 'unknown'}). Nothing was saved. "
            f"Required: {EXPECTED_ACCOUNT}."
        )
    _save(new_creds)
    _save_grant(new_creds, email)
    return new_creds


def drive_service(*, interactive: bool = False, force: bool = False):
    from googleapiclient.discovery import build

    creds = get_creds(interactive=interactive, force=force)
    # This commissioned writer deliberately uses Drive v2 because v2 exposes
    # file ETags and enforces If-Match on files.update. Drive v3 removed the
    # usable ETag, so it cannot provide race-free optimistic concurrency.
    service = build("drive", "v2", credentials=creds, cache_discovery=False)
    email = _account_email(creds)
    if email != EXPECTED_ACCOUNT:
        raise InvestmentsAuthError(
            f"Wrong Google account ({email or 'unknown'}). Required: {EXPECTED_ACCOUNT}."
        )
    return service
