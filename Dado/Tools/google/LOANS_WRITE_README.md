# Loans spreadsheet write connection (CCIVS and Stefe)

Commissioned by Rachad Homsi on 2026-07-26. The fixed `Stefe` table extension was commissioned by his direct instruction and live-tab confirmation on 2026-07-31. Built and tested with no live write before staging.

## Scope

- Target: the native Google Sheet `Loans`, spreadsheet ID
  `1WfstxvtOkfbX0zitJEirEUL94eYOo8Hm5mHOX_47TGQ`, MIME type
  `application/vnd.google-apps.spreadsheet`.
- Location is pinned by the complete immutable parent-ID chain, exactly as the
  investments tool does it: `My Drive / My Files / Rachad / Bussiness Folder`,
  ending at Business Folder ID `12C-CPb_1PWt-WHTQOd3PDLeJ_IV9zSdw`. Both the ID
  chain and the folder-name path must match, or the tool stops.
- Sheets identity is checked too: spreadsheet title `Loans`, locale `en_US`,
  time zone `America/Los_Angeles`, and the immutable tab IDs for either fixed
  target: `CCIVS` (`909361371`) or `Stefe` (`396384971`).
- CCIVS operation: `CCIVS!A4:B60`, with `B3` required to be exactly
  `=SUM(B4:B60)`; fill the next blank row with date and negative repayment.
- Stefe operation: `Stefe!C6:E43`, with `D4` required to be exactly
  `=SUM(D6:D212)`, headers `DATE / Amount / Description` fixed at C5:E5, and
  the note at C44 fixed; fill the next blank row with date, negative amount and
  a 1-100 character plain-text description. The write range ends at row 43 and
  can never overwrite the row 44 note.
- Both operations use the same single Sheets `values.append` write expression
  with `OVERWRITE`; it does not insert or shift rows. The selected range is
  derived only from the plan's closed, allowlisted action. Nothing else exists —
  no update, clear, batch, create, delete, rename, move, copy, permission, or
  revision path anywhere in the file.

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

1. `check` — read-only CCIVS check. Verifies Drive identity, the full parent
   chain, editability, Sheets identity, the `B3` total formula, the last used
   row, the next free row, and the current balance. Never writes.
2. `check-stefe` — the equivalent read-only check for the fixed `Stefe`
   layout, immutable tab ID, formula, headers, note, next row and balance. The
   read covers C1:E212 — everything the `D4 = SUM(D6:D212)` Balance formula
   counts — and any content below the row-44 note fails closed, because a value
   in D45:D212 would make the tool's reported balance diverge from the sheet's
   own Balance cell (2026-07-31 review FINDING 3).
3. `stage-ccivs-payment --date YYYY-MM-DD --amount <positive> --source "<why>"`
   — live-reads the CCIVS state and writes a plan file. Nothing remote changes.
4. `stage-stefe-adjustment --date YYYY-MM-DD --amount <positive>
   --description "<plain text>" --source "<why>"` — live-reads the Stefe state
   and writes a closed-schema plan for its next row. Nothing remote changes.
5. `commit --plan <plan.json> --approval APPROVED` — the only path that writes;
   the plan's action chooses one of the two fixed ranges.

The amount is given as a POSITIVE deduction (max two decimals, ≤ 100,000,000)
and is stored NEGATIVE. For example, `--amount 50` writes `-50`.

## What staging checks

- Exact Drive identity, path chain and editability; exact Sheets title, locale,
  time zone, selected tab title and tab ID.
- Selected layout guard: CCIVS requires `B3 = SUM(B4:B60)`; Stefe requires
  `D4 = SUM(D6:D212)`, exact C5:E5 headers, the unchanged C44 note, and
  rows 45:212 completely empty (fail closed — they are inside the summed range).
- Current table end and next row must stay inside the selected fixed range and
  every target cell must be blank.
- Duplicate guard: CCIVS refuses the same date and amount; Stefe refuses the
  same date, amount and description.
- Balance arithmetic with `Decimal` over the selected numeric amount column. A
  formula, non-numeric amount, partial Stefe row or layout drift stops the run.
- The plan is a closed-schema JSON document with a canonical full SHA-256 over
  every field, a 128-bit nonce, a 24-hour lifetime, and a SHA-256 of the exact
  pre-write FORMULA values for the selected fixed read range.
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
3. Writes a local JSON backup of the selected pre-write FORMULA grid to
   `%LOCALAPPDATA%\FRPDepot-Google-Investments-Write\loans_backups`.
4. Takes an exclusive, digest-keyed replay lock — created with `O_EXCL`, so a
   second commit of the same plan can never reach the write.
5. Repeats the full identity/content recheck immediately before the write, then
   performs the module's single remote write expression: `values.append` on
   exactly `'CCIVS'!A4:B60` or `'Stefe'!C6:E43` as sealed in the allowlisted
   action, `USER_ENTERED`, `OVERWRITE`, `num_retries=0`.
6. Requires `updatedRange` to equal the staged target row and exactly one row /
   two CCIVS cells or three Stefe cells.
7. Live readback of the selected FORMULA grid: the target cells must match, the
   total formula and pinned layout must remain unchanged, no other row may move,
   and the recomputed balance must equal the approved resulting balance. Drive
   and Sheets identity are verified again.
8. Only then does it print `COMMITTED_AND_VERIFIED` and write the receipt to
   `Dado\40_Logs\receipts.jsonl`.

There is no retry after the lock under any outcome. If the append fails or
cannot be verified, the lock is marked `indeterminate`, a receipt is written,
and the tool reports that reconciliation is required — compare the live tab with
the local backup and Sheets version history. If the run stops before the write
was ever issued, the lock records `aborted_before_write` and the tool says
plainly that nothing was written.

## Row behaviour

`OVERWRITE` fills the next existing blank row in the selected fixed table; it
does not insert or shift rows. Every staged target cell must still be blank
immediately before the write. A concurrent entry would move the returned range
away from the approved row; the tool then locks without retry and reports that
reconciliation is required.

## Testing

`test_google_loans_tool.py` fakes Drive and Sheets end to end. It asserts
statically that the only remote write expression in the module is one
`spreadsheets().values().append(` call with `num_retries=0`, and that no other
Sheets or Drive write surface exists. No test ever performs a live write.

Never paste OAuth files, tokens, authorization codes, or browser URLs into chat.
