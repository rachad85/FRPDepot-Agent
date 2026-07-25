# Extended Google read-only setup

This connection is separate from Dado's working Gmail/Drive token.

## Permissions requested

1. Google account identity — email address only, to verify `rachad85@gmail.com`.
2. Google Analytics — read-only.
3. Google Calendar — read-only.
4. Google Contacts — read-only.
5. Google Search Console — read-only.

No write, send, delete, share, billing, user-administration, or account-management scope is requested.

## Required Google APIs

The OAuth project may require these APIs to be enabled once:

1. Google Analytics Admin API
2. Google Analytics Data API
3. Google Calendar API
4. People API
5. Google Search Console API

## Connect

Double-click:

`C:\FRPDepot\CONNECT_DADO_GOOGLE_READ_SERVICES.bat`

Sign in as `rachad85@gmail.com`, approve the five read-only boxes, and wait for the black window to show the live checks.

The token is stored outside the repository at:

`%LOCALAPPDATA%\FRPDepot-Google\extended_read_token.json`

Never paste or send that file or any value from it.
