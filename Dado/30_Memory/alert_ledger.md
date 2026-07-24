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
- 2026-07-23 Debit Return FRP DEPOTS - MID 426988 — Forte closed the ticket after no external reply; CAD 1,148.23 return and funding hold require immediate verification.
- 2026-07-23 Important: Account Closure Notification — Airwallex access ends 2026-08-15; remaining balance withdrawal and records download require verification.
- 2026-07-23 Interac Autodeposit registration replaced — info@frpdepots.com was moved from DCBank to Desjardins account ending 1140; authorization requires verification.

## CLOSED
- 2026-07-23 Inbox monitoring failure — root cause fixed by backend the same evening (sweep charter now invokes the venv python by full path); verified by rerun.
- 2026-07-23 Inbox monitoring failure (19:19 ET) — root cause fixed by backend: the wrapper now collects the triage data itself with no shell in the path, and reports collection failures deterministically without relying on the model; verified by rerun.
- 2026-07-23 commande de bride Derakane 470 — Rachad confirmed Fibre Mauricie had already received and paid; Zoho shows SO-00046 invoiced and INV-000048 paid with CAD 0.00 balance.
