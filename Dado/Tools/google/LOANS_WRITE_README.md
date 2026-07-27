# Loans spreadsheet write connection (CCIVS repayments)

Commissioned by Rachad Homsi on 2026-07-26. Built and tested with no live write.

## Scope

- Target: the native Google Sheet `Loans`, spreadsheet ID
  `1WfstxvtOkfbX0zitJEirEUL94eYOo8Hm5mHOX_47TGQ`, MIME type
  `application/vnd.google-apps.spreadsheet`.
- Location is pinned by the complete immutable parent-ID chain, exactly as the
  investments tool does it: `My Drive / My Files / Rachad / Bussiness Folder`,
  ending at Business Folder ID `12C-CPb_1PWt-WHTQOd3PDLeJ_IV9zSdw`. Both the ID
  chain and the folder-name path must match, or the tool stops.
- Sheets identity is checked too: spreadsheet title `Loans`, locale `en_US`,
  time zone `America/Los_Angeles`, tab `CCIVS` with immutable sheetId
  `909361371`.
- Table: `CCIVS!A4:B60`, with `B3` required to be exactly `=SUM(B4:B60)`.
- The ONE commissioned operation: fill the next existing blank repayment row
  (date, negative amount) in that table. Sheets `values.append` is used only as
  a safe table-end locator with `OVERWRITE`; it does not insert or shift rows.
  Nothing else exists — no update, clear, batch, create,
  delete, rename, move, copy, permission, or revision path anywhere in the file.

## Authorization

Reuses the separately validated Drive-only credential from
`google_investments_auth.py` (`%LOCALAPPDATA%\FRPDepot-Google-Investments-Write`).
Full Drive already authorizes the Sheets API for this file, so no new consent,
scope, token, or grant file is created or touched. Wrong account, drifted scope,
or a grant file that does not match the live refresh token still fails closed in
that module.

## Commands

Run with the hermes venv interpreter
`%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe`.

1. `check` — read-only. Verifies Drive identity, the full parent chain,
   editability, Sheets identity, the `B3` total formula, the last used row, the
   next free row, and the current balance. Never writes.
2. `stage-ccivs-payment --date YYYY-MM-DD --amount <positive> --source "<why>"`
   — live-reads everything above plus the FORMULA values of `A1:B60`, then
   writes a plan file to `Dado\20_Working\loans_plans`. Nothing remote changes.
3. `commit --plan <plan.json> --approval APPROVED` — the only path that writes.

The amount is given as a POSITIVE deduction (max two decimals, ≤ 100,000,000)
and is stored NEGATIVE in column B. `--date 2026-07-26 --amount 1000` writes
`7/26/2026` and `-1000`.

## What staging checks

- Exact Drive identity, path chain, and editability; exact Sheets title, locale,
  time zone, tab title and tab ID.
- `B3` is exactly `=SUM(B4:B60)`.
- Current table end and the next row: it must fall inside rows 4–60 and both
  A and B must be blank.
- Duplicate guard: refuses if the same date AND the same negative amount already
  exist anywhere in rows 4–60 (serial-number and text date cells both parsed).
- Balance arithmetic with `Decimal` over the numeric rows of `B4:B60`. A formula
  or non-numeric value inside the used part of column B stops the run rather
  than producing a balance that cannot be trusted.
- The plan is a closed-schema JSON document with a canonical full SHA-256 over
  every field, a 128-bit nonce, a 24-hour lifetime, and a SHA-256 of the exact
  pre-write `A1:B60` FORMULA values.
- Rachad sees a short plan (row, date, value written, current and resulting
  balance) and one approval word: `APPROVED`. The digest is never printed.

## What commit does

1. Loads the plan and revalidates hash, closed schema, tool/action, exact 24-hour
   lifetime, expiry, nonce, file identity, entry allowlist, and balance
   arithmetic. Approval must be `APPROVED`, trimmed, case-insensitive, nothing
   else.
2. Rechecks all Drive/Sheets identity and re-reads the FORMULA values; they must
   match the staged content SHA-256 exactly, the target row must still be blank,
   and the balance must still match. A Drive version-counter-only change is
   ignored when modification time and exact cell content remain unchanged,
   because opening a native Google Sheet can advance that counter.
3. Writes a local JSON backup of the pre-write `A1:B60` values to
   `%LOCALAPPDATA%\FRPDepot-Google-Investments-Write\loans_backups`.
4. Takes an exclusive, digest-keyed replay lock — created with `O_EXCL`, so a
   second commit of the same plan can never reach the write.
5. Repeats the full identity/content recheck immediately before the write, then
   performs the single remote write:
   `spreadsheets.values.append`, range `'CCIVS'!A4:B60`,
   `valueInputOption=USER_ENTERED`, `insertDataOption=OVERWRITE`,
   body `[[M/D/YYYY, negative amount]]`, `num_retries=0`.
6. Requires the API response `updatedRange` to be exactly the planned
   `CCIVS!A{row}:B{row}` and exactly 1 row / 2 columns / 2 cells.
7. Live readback of the FORMULA values: the appended row must hold that exact
   date and negative amount, `B3` must be unchanged, no other row may have
   moved, and the recomputed balance must equal the approved resulting balance.
   Drive and Sheets identity are verified again.
8. Only then does it print `COMMITTED_AND_VERIFIED` and write the receipt to
   `Dado\40_Logs\receipts.jsonl`.

There is no retry after the lock under any outcome. If the append fails or
cannot be verified, the lock is marked `indeterminate`, a receipt is written,
and the tool reports that reconciliation is required — compare the live tab with
the local backup and Sheets version history. If the run stops before the write
was ever issued, the lock records `aborted_before_write` and the tool says
plainly that nothing was written.

## Row behaviour

`OVERWRITE` fills the next existing blank table row; it does not insert or shift
rows. The staged target must still be blank immediately before the write. A
concurrent entry would move the API's returned range away from the approved row;
the tool then locks without retry and reports reconciliation required.

## Testing

`test_google_loans_tool.py` fakes Drive and Sheets end to end. It asserts
statically that the only remote write expression in the module is one
`spreadsheets().values().append(` call with `num_retries=0`, and that no other
Sheets or Drive write surface exists. No test ever performs a live write.

Never paste OAuth files, tokens, authorization codes, or browser URLs into chat.
