# Dado alert ledger — BINDING for every inbox sweep

Rules (mirrored in the sweep charter; Aze's proven pattern, adopted 2026-07-23):
- BEFORE composing any alert, read this file. An item under CLOSED is silent,
  permanently, unless genuinely NEW inbound mail arrived after the closing
  date. An item under ALERTED is already on Rachad's phone; no repeat without
  new inbound mail or a deadline now within 24h.
- AFTER deciding to alert, append ONE dated line per item to ALERTED in the
  same run, and record a receipt (SOUL rule).
- When Rachad closes an item in any channel, append it to CLOSED immediately.
- Line format:
  `- YYYY-MM-DD <thread subject or short id> — <one-line what was alerted / why closed>`

## ALERTED
- 2026-07-23 Inbox monitoring failure — Outlook sweep could not start because the check script path was not resolved by the Python runner; needs attention.
- 2026-07-23 Inbox monitoring failure (19:19 ET) — Outlook sweep failed before mail could be checked because MSYS rewrote the script path to C:\c\FRPDepot; monitoring needs attention.

## CLOSED
- 2026-07-23 Inbox monitoring failure — root cause fixed by backend the same evening (sweep charter now invokes the venv python by full path); verified by rerun.
- 2026-07-23 Inbox monitoring failure (19:19 ET) — root cause fixed by backend: the wrapper now collects the triage data itself with no shell in the path, and reports collection failures deterministically without relying on the model; verified by rerun.
