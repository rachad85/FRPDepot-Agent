# Investments workbook write connection

Commissioned by Rachad Homsi on 2026-07-26.

## Scope

- Separate OAuth token in `%LOCALAPPDATA%\FRPDepot-Google-Investments-Write\token.json`; its verified actual grant is bound in `grant.json`.
- Google permission: exactly full Drive and no other granted scope, required to update an existing file not opened through Google Picker.
- Named tool enforcement: immutable workbook ID plus the complete immutable parent-ID chain for `My Drive / My Files / Rachad / Bussiness Folder / Investements.xlsx`.
- Current allowed edit: add a cash-profit row in the `Pistavo Labs` revenue section.
- No create, delete, rename, move, sharing, permission, or revision-management API path exists.
- Every edit uses a 24-hour full-SHA-256 plan, Rachad's one-word `APPROVED` confirmation, Drive v2 ETag `If-Match`, a digest-keyed single-use lock, local backup, and live readback. The digest stays internal; Rachad never has to copy checksum characters.
- The XLSX is patched directly: unrelated ZIP parts, formulas, formatting, custom content, and the archive comment are preserved; the real calculation settings are validated after readback.

## Buttons

1. `CONNECT_DADO_INVESTMENTS_WRITE.bat` — fresh browser consent and live workbook verification.
2. `CHECK_DADO_INVESTMENTS_WRITE.bat` — read-only connection check; never changes the workbook.

Never paste OAuth files, tokens, authorization codes, or browser URLs into chat.
