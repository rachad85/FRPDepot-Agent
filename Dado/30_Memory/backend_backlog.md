# Backend engineering backlog — Dado

**This file is for the BACKEND (Claude Code), not for Dado.** It is NOT the
alert ledger. `alert_ledger.md` is binding on Dado's alerting; this file is a
work queue for code fixes and carries no authority over her behaviour.

Opened 2026-07-24 by the standing watch thread. Every item below was found by
reading the code, then independently re-verified by an adversarial pass that
tried to refute it; 14 further candidates were refuted and dropped rather than
listed here. Line numbers are as of 2026-07-24 21:00–22:00.

Status key: `OPEN` — not started. `FIXED <commit>` — landed and verified.
`WONTFIX <reason>` — Rachad's call, do not re-raise.

---

## P0 — a shipped feature that cannot work at all

### B-01 FIXED 6265910 (code) + this commit (tests) — Follow-up chase drafts were structurally impossible

Fixed 2026-07-24. `latest_live_external_non_draft()` now resolves the reply
source to the newest **live external** non-draft message, so our own Sent copy
no longer blocks the chase; automated senders (bounce/no-reply) are skipped and
refused as chase targets. The same guard is applied at BOTH sites — the
pre-flight check and the post-creation re-check at `:1014`, which would
otherwise have failed *after* creating the draft and left an orphan. Five
regression tests added, verified to fail against the pre-fix code and pass
against the fix. NOTE the code landed inside commit 6265910 ("Fix the MSYS path
bug…") because a concurrent session ran a catch-all `git commit`; the message
does not describe this change.

Original report follows.

`Dado/Tools/outlook/outlook_tool.py` — `command_reply_all`

Two guards are jointly unsatisfiable for exactly the threads the follow-up
tracker selects:

- **:891** the source must be EXTERNAL (`not ...endswith("@frpdepots.com")`)
- **:916** the source must be the newest non-draft message in the thread, where
  `latest_non_draft` (**:609**) filters on `isDraft` only — **no direction
  filter**, so Rachad's own Sent copy counts.

`show_waiting_on_them` (`outlook_check.py:314`) only emits a thread **when
Rachad spoke last**. So the newest non-draft is always his own outbound message:
:891 forbids using it, :916 forbids anything older. No source satisfies both.

Evidence: first live run 2026-07-24 21:51 — 15 overdue collected, 0 drafts
created, 4 attempts logged to `alert_ledger.md` as "follow-up draft blocked
because Outlook rejected the older inbound source after Rachad's newer sent
message". Behind it: QT-000023 at 42 working days, CAD 4,101.30 at 9 days.

Why tests missed it: `test_outlook_tool.py:248` models a conversation
containing only the external message, so the guard never sees an outbound one.

FIX — scope guard :916 to the latest **external** non-draft message. The real
safety property (*don't reply to a stale message if THEY have since replied*)
is preserved; only the comparison against your own sent mail goes away. Add a
regression test with an outbound-last thread.

CAUTION — this loosens a guard on the DRAFTS-ONLY path. Get Rachad's go-ahead.
Note also the one accidental escape: if an **automated** external message
(bounce/noreply) lands after his send, the selector still lists the thread but
the guards pass, so the chase would be addressed to the noreply address. Fix
that in the same change by requiring the source to be a non-automated external.

---

## P1 — monitoring that fails silently (Rachad reads quiet as "nothing needs me")

### B-02 FIXED (this commit) — A failed brain run is indistinguishable from a clean sweep

Fixed 2026-07-25. `run_dado` now returns **None** for "the run never produced
text", distinct from `""`, and catches `TimeoutExpired`. Both callers check
`msg is None` **before** `is_silent`, which returns True for None — that
ordering is the whole fix, and a test pins it. The sweep alerts with "This is
NOT an all-clear"; the digest additionally names the overdue count, and because
`prefetch` has already proven there is work, empty output and output that
scrubs to nothing now alert too instead of going dark. Genuine `[SILENT]` is
untouched. 14 tests in `Dado/Tools/watch/test_watch_delivery.py`, 10 of which
fail against the pre-fix modules (the other 4 are controls asserting that quiet
mornings stay quiet). Both **profile script copies re-synced** — the cron runs
those, not the repo.

Original report follows.
`dado_inbox_reasoner.py:409` and the same hole at `dado_followup_digest.py:180`.
`run_dado` returns `""` on any non-zero exit — quota exhaustion on the shared
openai-codex plan, gateway down, provider outage (**no fallback by design**).
`is_silent("")` is True, so it logs "silent" and sends nothing. The prefetch
failure path DOES alert, which makes the gap invisible. A `timeout=2700`
`TimeoutExpired` is uncaught and kills the process with a traceback, no alert.
FIX — return a sentinel distinct from a genuine empty reply; alert on it; catch
`TimeoutExpired`. Never treat empty output as silent when prefetch already
proved there is work.

### B-03 FIXED (this commit) — `send_clean` loses the alert when a send hangs

Fixed 2026-07-25. `_try_send` wraps its `subprocess.run` and reports any
exception as `(-1, "<Type>: <msg>")`, so a hung Telegram call
(`TimeoutExpired`) or a missing binary (`FileNotFoundError`) becomes a failed
attempt that still retries and still queues. `send_clean`'s retry loop is
additionally wrapped so nothing between the first attempt and
`queue_undelivered` can throw the alert away. Covered by
`test_watch_delivery.py`; both cases error out against the pre-fix module.

Original report follows.
`dado_inbox_reasoner.py:331`. `_try_send` runs `hermes send` with `timeout=60`;
`TimeoutExpired` (the exact transient outage the 3-retry policy exists for) is
caught nowhere, so it escapes `send_clean` and `queue_undelivered` is never
reached. A missing `hermes` binary does the same via `FileNotFoundError`.
FIX — `try/except Exception` in `_try_send` returning `(-1, repr(exc))`, and
wrap the `send_clean` body so anything unexpected still falls through to the
undelivered queue.

### B-04 OPEN — Stale-heartbeat warning permanently mutes the real completion
`job_runner.py:307`. The stall notice sets `reported=True`, so line 278's
`continue` swallows the terminal done/failed announcement. A 45-minute OCR job
that goes quiet then finishes cleanly is reported as possibly-stuck and never
reported as finished.
FIX — separate `stall_reported` from `reported`.

### B-05 OPEN — `cmd_watch` and the supervisor race; a finished job is announced "died"
`job_runner.py:293`. `cmd_watch` loads the job, then shells to `tasklist`
(20s). If the supervisor writes `status=done, exit_code=0` in that window,
`cmd_watch` sees the pid gone and saves its **stale** dict as `status=died,
exit_code=None`. Both processes also write the same `<job_id>.json.tmp`, so a
concurrent open raises an uncaught `PermissionError` on Windows.
FIX — re-read immediately before mutating (compare-and-set), unique temp name
(`.{pid}.tmp`), or let only the supervisor own `status`.

### B-06 OPEN — `cmd_watch` marks every job reported but prints only 5
`job_runner.py:311`. Jobs 6+ in one tick are marked reported on disk and their
announcement is discarded permanently. `broken` messages are appended first and
not persisted, so 5 unparseable files re-fill the cap every tick and starve all
real completions.
FIX — only set `reported` for jobs actually printed; put broken notices last.

### B-07 OPEN — `stall_tripwire` burns the alert budget on problems it never prints
`stall_tripwire.py:179`. `record["alerts"] += 1` runs for every stalled session,
but only `problems[:3]` print. The 4th session climbs to `MAX_ALERTS_PER_TURN`
and goes permanently quiet **having never once reached Rachad**.
FIX — build the printed slice first, then increment only for included entries.

### B-08 OPEN — Job-watch and tripwire persist "announced" before delivery succeeds
`job_runner.py:286`, `stall_tripwire.py:176`. Both use cron `deliver: telegram`,
which has no retry and no undelivered queue. State says announced even when the
message was dropped; never re-emitted.
FIX — route through `send_clean`/`queue_undelivered`; persist only after a
confirmed send.

### B-09 OPEN — `scrub_noise` deletes legitimate business lines
`dado_inbox_reasoner.py:192`. `NOISE_LINE` drops the WHOLE line on `\bjob[_ ]?id\b`
or `cleaned[_ ]?up`, so "Their job ID 88-A needs the FRP grating quote priced by
Friday" vanishes; if it was the only substantive line the alert is suppressed by
the `len(clean) < 8` guard. Separately `FRAME_TOKEN`'s ticker branch
(`[a-z]+ing\s*\.{2,}`) turns "Pending... payment from SCT" into "payment from
SCT", inverting the first word Rachad reads.
FIX — anchor the machinery patterns to line-start / whole-line; drop `job id`
and `cleaned up`.

### B-10 OPEN — A turn older than the 30k-line tail is invisible to the tripwire
`stall_tripwire.py:77`. `read_tail` keeps 30,000 lines; a turn whose start line
rolls out (or a log rotation mid-turn) has no entry, and line 174 deletes its
record. The 3-hour stall this was written for becomes undetectable exactly when
the log is busiest. Also loads the whole file into memory every 15 min.
FIX — seek from the end by bytes; size the window by wall-clock age; include the
rotated log when the newest turn start is older than the window.

---

## P1 — follow-up tracker correctness (beyond B-01)

### B-11 OPEN — One unsent draft removes a live money thread from the tracker forever
`outlook_check.py:350`. `chase_draft_pending` matches ANY draft in the
conversation — an ordinary reply draft, a half-typed reply, a rejected chase
(deleted drafts still return from `/me/messages`). Such threads go to
`already_chased`, are excluded from `overdue` (:372) and from `overdue_count`,
and the prompt tells Dado to ignore them. **Live proof in today's
`waiting_on_them.json`**: the CAD 9,936 budgetary quote to brianb@nashtecllc.com
is already in `already_chased` after 1 day — no chase can have happened. If every
overdue thread carries a stale draft, `overdue_count` is 0 and the digest never
starts the model.
FIX — track chases we actually created (state file or match the draft we wrote),
expire after the 7 days the prompt already promises, and keep the thread in
`overdue` with a `chased_on` field instead of deleting it from the list.

### B-12 OPEN — The 60-day window silently drops the longest-overdue threads
`outlook_check.py:324`. Seeded only from Sent Items with `sentDateTime ge
now-60d`, so the longer a thread is ignored the closer it gets to falling out
entirely. **QT-000023** (last sent 2026-05-27, 42 working days) leaves the window
on **2026-07-27** and vanishes from every future digest with no record it was
ever tracked. Nothing distinguishes "resolved" from "aged out unchased".
FIX — filter on conversation activity, or keep a persistent tracker carried
forward until answered or explicitly closed; at minimum emit an `aged_out` list.

### B-13 OPEN — Empty `my_addr` turns the tracker into a permanent all-clear
`outlook_check.py:345`. `my_address()` swallows both failures and returns `""`;
the ownership test then skips every candidate, JSON stays valid,
`overdue_count` is 0, digest exits silent. `prefetch` only checks the return
code and the JSON parse.
FIX — exit non-zero on empty `you`; have `prefetch` reject a blank `you` field.

### B-14 OPEN — Working-day clock mixes UTC instants with Eastern days
`outlook_check.py:210`. Measured: `business_days_since('2026-07-20T19:30:00Z')`
= 4 but `('2026-07-21T01:30:00Z')` = 3 — two mails sent the same Monday (15:30
and 21:30 ET) differ by a whole working day. Anything sent after ~20:00 ET goes
overdue a day late; the 19:00 sweep under EST is 00:00 UTC next day, adding a
phantom day and firing items early.
FIX — convert both ends to `ZoneInfo('America/Toronto')` before `.date()`.

### B-15 OPEN — Sent Items capped at `$top=250`, truncation invisible
`outlook_check.py:326`. No `@odata.nextLink` follow-up; ordering is
`sentDateTime desc`, so overflow discards the OLDEST mail — the most overdue
threads. Headroom is thin (99 sent in 60 days; 300 reach back to 2026-01-06), and
`days_back` is a free-form argument.
FIX — page until the window is exhausted, or emit `truncated: true`/`oldest_seen`.

### B-16 OPEN — Category inferred from quoted history and forwarded bank notices
`outlook_check.py:197`. `classify_thread` reads subject + `bodyPreview`, and
`_PAY_WORDS` wins first. On a reply the preview leads with quoted history, so the
tier is often set by words that are not Rachad's — a quote misfiled as `payment`
waits 7 days instead of 5. Worse, today's overdue list contains "Fw: You received
a deposit of 49,100.00 USD" and "FW: Your conversion has been created" — bank
notices he forwarded — classified `payment`+`urgent`, so rule 3b gates them to
his phone with a chase draft prepared **against a bank notification**.
`_is_automated` only inspects the sender, and here the sender is Rachad.
FIX — classify on subject + his own added text (strip the quoted block), check
RFQ words before payment words on a quote-numbered subject, and exclude threads
whose ROOT message came from an automated sender even when he forwarded it.

### B-17 OPEN — 15 items × 4 tool calls exceeds `agent.max_turns: 60`
`dado_followup_digest.py:64`. The prompt demands a thread read, a ledger read, a
draft, a ledger append and a receipt per item; 15 overdue needs 60+ iterations
against the exact cap that ended the 2026-07-24 three-hour incident. The turn
ends mid-list and Rachad reads "3 threads have gone quiet" as the whole story.
The wrapper never compares the digest's item count against `overdue_count`.
FIX — cap work per run (worst/urgent first) and have the WRAPPER append a
deterministic "15 overdue in total" line so a truncated answer cannot understate.

### B-18 OPEN — The new 900s collection is a hard prerequisite for the whole sweep
`dado_inbox_reasoner.py:279`. `collect("waiting_on_them", ..., timeout=900)` was
added to `prefetch_triage`, which raises on any failure — so the slowest, newest
call is now a single point of failure for the pre-existing `[awaits YOU]`
monitoring. A Graph slowdown discards the inbox/awaiting/sent data already
collected and no mail is reasoned over for two hours.
FIX — make it best-effort: catch, write a marker, proceed with the other three
sources and tell the prompt the data is unavailable this run.

---

## P1 — Drive OCR backfill (`google_backfill.py`)

### B-19 FIXED bb1f697 (another session) — Never converges: successful rows are re-downloaded and re-OCR'd every run

Closed by one line in `candidates()`: `AND content_status NOT LIKE 'backfill_%'`,
which excludes rows a previous run already read. Runs converge now. Verified by
reading the predicate, not by re-running the job.

SIDE EFFECT worth knowing: line ~301 writes `backfill_no_text` for rows that
produced nothing, and the new predicate excludes those too — so a file whose OCR
came back empty is never retried. Bounded, which is what was wanted, but it means
that population joins B-20's "indexed but empty" rows as permanently unread.
**B-20 is still open and is the more important of the two.**

Original report follows.
**:308** writes `content_status = f"backfill_{kind}"`, but `candidates()` admits
any row whose status does not start with `indexed`. So every row a run succeeds
on is a candidate again next run, in the same `ORDER BY size ASC`. From the live
log (1,484 candidates, ~19 items/min, 45-min cap ≈ item 860): run 2 starts over
at item 1 and spends ~78% of its budget redoing work, barely advancing. Because
ordering is size ASC, the rows never reached are the LARGEST — the multi-page
scans most likely to matter. The docstring's "bounded + resumable" does not hold.
FIX — name the success status inside the convention the query understands
(`indexed_backfill_{kind}`), or switch to the content-presence predicate in B-20
plus an attempt counter so a permanently-bad file cannot re-occupy the head.

### B-20 OPEN — Scanned PDFs stored as `indexed` with EMPTY content are skipped entirely
**:229**. `candidates()` filters `content_status NOT LIKE 'indexed%'`, but the
defect being fixed is "row has no content" — and they diverge for the headline
case. In `google_indexer.extract_drive_content` an image-only scan has no text
layer, `extract_text()` returns `""` for every page, no exception is raised, and
the row is stored `content=""`, status `indexed`. `NOT LIKE 'indexed%'` excludes
it forever. The live plan proves it: **pdf: 7 of 1,484 candidates**, while the
module advertises scanned-PDF OCR as a headline capability. Same for
docx/xlsx/pptx/text that extract to empty. Those rows look read-and-clear to
`google_reference.py`.
FIX — select on the real condition: `WHERE (content IS NULL OR trim(content) =
'') AND content_status != 'folder_metadata' AND tdi_quarantined = 0`. Add a
`no_text_but_indexed` counter to the plan output so the hidden population is
visible before the run.

### B-21 OPEN — `text_from_eml` promotes a failed body read to "read-and-clear"
**:122**. `except Exception: body = ""` makes a failed extraction
indistinguishable from an empty body, but the function still returns a non-empty
header block. So `text` is truthy, `deep_tdi_marker` sees only headers, nothing
fires, and the row is written `content=<headers>`, `backfill_eml`, `indexed_at=now`
— **moved out of the unscreened pool while its body was never read**. Attachment
CONTENT is never read either (names only), so an `.eml` whose TDI material is
inside an attached PDF is stored as clear. This is the exact inversion of this
tree's own rule: SCREEN WHAT YOU RETURN.
FIX — do not swallow. Signal partiality (`return (text, complete)` or raise) and
write a status that `candidates()`/`google_reference.py` treat as still
unscreened.

### B-22 OPEN — Deliberately partial extractions are recorded as full reads
**:156**. `text_from_scanned_pdf` OCRs only the first 12 pages; `text_from_xls`
caps at `MAX_TEXT`/4,000 rows per sheet; `text_from_zip` stores member NAMES
only. All return normally, so a 30-page scanned drawing set whose first 12 pages
carry no marker is stored as clear and permanently leaves the unscreened pool —
pages 13-30 are never looked at by any pass. `google_reference.py`'s honest
caveat only covers rows holding NO content, so it no longer applies to these.
FIX — have each extractor report completeness; store `backfill_partial_<kind>`;
keep partial rows in the pool, or surface `content_status` verbatim so a partial
row reads as a pointer to verify.

### B-23 FIXED (this commit) — `text_from_msg` leaks an open handle, leaving raw Drive bytes in %TEMP%

Fixed 2026-07-25. `contextlib.closing` guarantees `close()` runs before the
unlink, the temp file gets a unique `tempfile.mkstemp` name per item instead of
one per-PID path shared by every `.msg` in a run, and a failed unlink now prints
a `{"phase": "warn", "temp_unlink_failed": ...}` line instead of being swallowed
by `except OSError: pass`. `Dado/Tools/google/test_google_backfill.py` pins it —
against the pre-fix file the parse-error case leaves `_dado_backfill_<pid>.msg`
behind and the test fails on exactly that.

NOTE the "one bad message poisons the next" test passes against the pre-fix file
too: whether the stale handle blocks the following `write_bytes` depends on GC
timing. It is a real invariant worth holding, but the leftover-file assertion is
the one that actually demonstrated the bug.

Original report follows.
**:142**. `m.close()` is only on the success path. Any parse error propagates
with the handle open, so `tmp.unlink()` fails on Windows and `except OSError:
pass` absorbs it. The full raw bytes sit at `%TEMP%\_dado_backfill_<pid>.msg`
indefinitely — outside the DB, outside quarantine, outside anything
`google_rescreen.py` can walk. If that file would have been quarantined as TDI
material, the DB is clean but an unscreened plaintext copy is on disk. 122 `.msg`
files are queued in the current run.
FIX — `contextlib.closing` so `close()` always runs before unlink; unique temp
name per item; escalate a failed unlink to a visible warning.

### B-24 OPEN — DB writes outside the try, no `busy_timeout`
**:285**. Default 5s lock timeout, and every write is outside the `try` that
guards download/extraction. A concurrent `google_indexer.py`/`google_rescreen.py`
writer raises `database is locked`, which propagates past `main()` and kills the
run mid-flight with no summary and no receipt. Worse, if the indexer wins a race
on a row this pass backfilled, its `INSERT OR REPLACE` rewrites it to
`content=""` / `metadata_only_unreadable_type`, silently undoing the read.
FIX — `PRAGMA busy_timeout=30000`; move per-row writes into the try with a
`db_errors` counter; take a lockfile so indexer and backfill cannot overlap.

---

## P1 — Google reconnect (UNCOMMITTED working-tree change, 2026-07-24)

### B-25 FIXED (this commit) — ⚠ `reconnect` could have DESTROYED Google access

Fixed 2026-07-24: `google_auth.py.get_creds` now passes `access_type="offline",
prompt="consent"`, matching `google_extended_auth.py`. New
`Dado/Tools/google/test_google_auth.py` asserts both modules carry both kwargs
(AST assertions — a mocked flow object would accept a missing kwarg silently, so
only reading the call as written catches this), verified to fail against the
pre-fix file and to pass for the sibling. B-26 below is still OPEN: the fix is
correct but Rachad has no BUTTON that reaches it.

Original report follows.
`google_auth.py:158`. `flow.run_local_server()` passes only `port` and `message`.
`google_auth_oauthlib` defaults `access_type="offline"` but does **not** default
`prompt`. Google returns a refresh token only on first authorization or when
`prompt=consent` forces re-approval. Rachad has already granted these scopes to
the `dado-frpd` client, so `google_tool.py reconnect` re-runs an already-granted
request: Google auto-approves, returns `refresh_token=null`, and `_save()`
**writes that over the working token**. `self_check` then succeeds on the live
access token and prints "SIGNED IN OK" + "Sign-in should now be long-lived".
About an hour later the access token expires and every non-interactive consumer
dies at once — `google_indexer.py:571`, `google_backfill.py:270`,
`google_service_audit.py:28`, and all six `google_tool.py` commands.

The command sold as the cure for the stale-token problem is the one most likely
to cause total loss of access. `google_extended_auth.py:123` already passes
`prompt="consent"` correctly — `google_auth.py` is the outlier.

FIX — add `access_type="offline", prompt="consent"` to the `run_local_server`
call in `google_auth.py.get_creds`, matching the sibling file. **Do this before
anyone double-clicks reconnect.**

### B-26 OPEN — `reconnect` is unreachable from the buttons Rachad actually uses
`google_extended_auth.py:251`. All three existing buttons still run the unforced
verbs, and no `RECONNECT_*.bat` exists at the repo root. CLAUDE.md says buttons
over commands, so the operator repeats the same double-click, gets the same
silent early return and the same false VERIFIED, and the fix never fires.
FIX — add `RECONNECT_DADO_GOOGLE.bat` and
`RECONNECT_DADO_GOOGLE_READ_SERVICES.bat`; have the unforced path print
"Reused the stored sign-in; no browser opened. To mint a NEW token run
RECONNECT_...bat".

### B-27 OPEN — "Sign-in should now be long-lived" is printed for pre-switch tokens
`google_auth.py:228`. Printed whenever `ok` is True, on all three verbs,
including `check` and the `connect` reuse path. The claim is about the OAuth
app's publishing status, not about this credential, so it asserts a property of a
token it did not mint — the same class of false assurance the change was written
to remove, and it replaced the previously correct 7-day reminder.
FIX — print the long-lived claim only on the force path; on reuse paths stamp the
token mtime: "Reusing the sign-in stored on <date>. Tokens minted before
2026-07-24 still expire in 7 days — run reconnect once to replace it."

---

## P2 — WooCommerce audit (UNCOMMITTED working-tree change, 2026-07-24)

### B-28 OPEN — `settings_group_warnings` never reaches the operator
`woocommerce_audit_tool.py:663`. `public_report` is only
`generated_utc/summary/findings`, so the markdown never renders it; `summary` has
no warning count; stdout omits it; the receipt records only the markdown path, so
the conduct bundle sees nothing. The only copy sits in the audit JSON under
`%LOCALAPPDATA%`, opened by nobody. The operator sees `STORE_AUDIT_COMPLETE` and
a `configuration.settings` block that looks authoritative but is missing every
setting from the failed group. The `except` is functionally a bare swallow.
FIX — append a real `finding` per skipped group before the severity `Counter`, so
it flows into summary counts, markdown and stdout; add
`settings_groups_skipped` to `report["summary"]`.

### B-29 OPEN — The same list-then-fetch race is unguarded elsewhere
`woocommerce_audit_tool.py:514`. `/shipping/zones` is listed, then
`/locations` and `/methods` are fetched per zone; a zone deleted in between (or
one a plugin advertises but cannot serve) 404s and kills the whole audit after
all catalog, order and customer pages were already fetched.
`/products/attributes/{aid}/terms` at :477 is the same shape.
FIX — once `WooError` carries structured status/code, factor
`api_get_optional(endpoint, params, vault, skipped_list)` and use it at every
enumerate-then-fetch site rather than one inline `try`.

---

## P2 — log integrity

### B-30 OPEN — `receipts.jsonl` has three timestamp formats plus one corrupt line
4,829 lines: 4,807 UTC, 8 local-offset, 13 naive (no timezone at all), 1
unparseable. All 13 naive stamps come from `job_runner.py` (shipped 19:24) —
`background_job_started`/`_finished`. Comparing a naive to an aware datetime
raises `TypeError` in Python, so any receipt-freshness check can crash or
silently skip; this broke a freshness query during the 2026-07-24 sweep. The new
follow-up digest writes local-offset while everything else writes UTC. Line 90 is
still the malformed-JSON receipt raised as FINDING 4 in the 2026-07-23 conduct
review and never fixed.
FIX — one writer, one format (UTC, `timezone.utc`), used everywhere; repair or
quarantine line 90.
