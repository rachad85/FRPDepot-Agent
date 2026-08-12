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

### B-04 FIXED (this commit) — Stale-heartbeat warning permanently mutes the real completion

Fixed 2026-07-25. `stall_reported` is now a separate flag from `reported`, so a job that went quiet, was flagged stuck, then finished cleanly is still announced as finished.

Original report follows.
`job_runner.py:307`. The stall notice sets `reported=True`, so line 278's
`continue` swallows the terminal done/failed announcement. A 45-minute OCR job
that goes quiet then finishes cleanly is reported as possibly-stuck and never
reported as finished.
FIX — separate `stall_reported` from `reported`.

### B-05 FIXED (this commit) — `cmd_watch` and the supervisor race; a finished job is announced "died"

Fixed 2026-07-25. `cmd_watch` re-reads the job file after `pid_alive()` returns and defers to a `done`/`failed` status written during that window; it never writes `status` from a stale dict. `save_flags()` does a read-modify-write of bookkeeping keys only, and `save()` uses a per-process temp name so the supervisor and the watch cron cannot collide on `<job_id>.json.tmp`.

Original report follows.
`job_runner.py:293`. `cmd_watch` loads the job, then shells to `tasklist`
(20s). If the supervisor writes `status=done, exit_code=0` in that window,
`cmd_watch` sees the pid gone and saves its **stale** dict as `status=died,
exit_code=None`. Both processes also write the same `<job_id>.json.tmp`, so a
concurrent open raises an uncaught `PermissionError` on Windows.
FIX — re-read immediately before mutating (compare-and-set), unique temp name
(`.{pid}.tmp`), or let only the supervisor own `status`.

### B-06 FIXED (this commit) — `cmd_watch` marks every job reported but prints only 5

Fixed 2026-07-25. Nothing is marked `reported` until it survives the print cap: the remainder is HELD and announced on the next tick, with a count line saying so. Broken-file notices moved to LAST so unreadable files cannot starve real completions.

Original report follows.
`job_runner.py:311`. Jobs 6+ in one tick are marked reported on disk and their
announcement is discarded permanently. `broken` messages are appended first and
not persisted, so 5 unparseable files re-fill the cap every tick and starve all
real completions.
FIX — only set `reported` for jobs actually printed; put broken notices last.

### B-07 FIXED (this commit) — `stall_tripwire` burns the alert budget on problems it never prints

Fixed 2026-07-25. The alert budget is spent only on problems actually printed. Unprinted stalls stay tracked at zero cost, so a 4th concurrent stalled session can no longer exhaust its own budget in silence.

Original report follows.
`stall_tripwire.py:179`. `record["alerts"] += 1` runs for every stalled session,
but only `problems[:3]` print. The 4th session climbs to `MAX_ALERTS_PER_TURN`
and goes permanently quiet **having never once reached Rachad**.
FIX — build the printed slice first, then increment only for included entries.

### B-08 FIXED 2026-08-12 — Job-watch and tripwire persisted "announced" before delivery succeeded
`job_runner.py`, `stall_tripwire.py`. Both used cron `deliver: telegram`, which
has no retry and no undelivered queue, and nothing reads `last_delivery_error`.
State said announced even when the message was dropped; never re-emitted.
FIXED on Rachad's own approval (he was asked first — see D-08 below; he chose
the bare-text option knowingly). Both scripts now OWN their delivery through
`send_clean(..., queue_on_failure=False)` and persist only after Telegram
confirms: job-watch leaves `reported` unset, and the tripwire spends NO alert
budget and does NOT start the 60-minute re-alert clock. Both alerts are
re-derived from durable state every 10/15 min, so the unspent state IS the
queue — deliberately NOT the shared `undelivered_alerts.txt`, which is an
unlocked read-all/rewrite-all that these two would collide on every 30 minutes,
and which would deliver a re-derived alert twice.
Both cron jobs moved to `deliver: local` so hermes does not send a second copy.
CONSEQUENCE, accepted: hermes no longer wraps these in "Cronjob Response: ..."
and no longer delivers their crash summary either — so each script now reports
its OWN crash through `dado_urgent_alert.py`, out of band.
TESTS: 11 in `test_watch_delivery.py`, plus a hard guard — both scripts refuse
to send when `PYTEST_CURRENT_TEST` is set. Wiring delivery into these paths made
a plain pytest run put 14 real messages on Rachad's phone before that guard
existed.

### GATEWAY-DEATH / ORPHANED-TURN CHECK — BUILT 2026-08-12 (proposed 2026-08-04)

The tripwire could not see a turn killed by a gateway restart. `open_turns()`
pops a session on TURN_END; a gateway death writes no such line, so the dead
turn looked OPEN and was described as "still active, ask her what she is doing"
— an instruction to interrogate an agent with no memory of the request. The
moment Rachad re-asked (what a user does when ignored) the newer turn overwrote
the dead one and the evidence was gone.

MEASURED ON THE LIVE LOG while building it — TWO real orphans, neither ever
reported:
  - 2026-08-11 12:19:56 Discord, "Please proceed the PO we just received from
    SCT. Great a new Sales order, attach..." — a business instruction, lost when
    the gateway restarted at 13:10:55.
  - 2026-08-11 23:35:04 Telegram, an attachment with no text, lost at 00:21:39.

WHY IT CANNOT CRY WOLF ON A DELIBERATE STOP: the flag alone cannot answer it —
START_DADO DELETES the flag, the cron does not tick while she is stopped, and
STOP_DADO ends in Stop-Process -Force so a deliberate stop produces the same
unclean exit as a crash. So STOP_DADO and START_DADO now append a stamp to
`40_Logs\gateway_stops.log` (gitignored), and an orphan is silenced iff a stamp
falls inside [received, died] — which can only happen if the stop occurred
during the very life that was handling the message.

THREE DECISIONS NOT TO "SIMPLIFY":
 - A reply clears only messages from its OWN gateway life. Lane-wide looks
   equivalent; it would let a reply to a RE-ASKED question erase the evidence of
   the lost one before the next tick.
 - The boundary prefers gateway_state.json start_time (psutil create_time in
   CENTISECONDS, /100) over the log line: it survives a rotation and is the
   EARLIER source, the direction that cannot manufacture a false alarm.
 - None is a legitimate boundary. No boundary established => the check is inert.
   It never guesses.

Announced ONCE (ORPHAN_ALERTS = 1); 12-hour lookback. A dead turn no longer
reports as a stall. Tests: 15 new, 46 in the watch suite.

### CRON / SCRIPT MIRROR + DRIFT DETECTION — BUILT 2026-08-12

Dado's entire schedule existed in ONE file on ONE disk
(`%LOCALAPPDATA%\hermes\profiles\dado\cron\jobs.json`), with no commit, no
diff and nothing noticing a deleted, disabled or re-scheduled job. The scripts
those jobs run live in a THIRD place — the profile `scripts\` dir, outside this
repo — which is the mechanism behind job_runner.py running three days behind its
repo fix with every health signal green.

`Dado\Tools\watch\dado_profile_mirror.py`, called from the keep-alive.

A DETECTOR, NOT AN IMPORTER, and the direction of truth is the OPPOSITE of
dado_soul_sync.py. SOUL.md is authored in the repo, so that one writes
mirror -> live. Cron is authored LIVE (`hermes -p dado cron create`) and hermes
rewrites jobs.json under its own lock on every run of every job, so LIVE IS THE
TRUTH and this only ever writes the repo. An external writer that does not hold
that lock can silently drop a claim or a completion, and copying a mirror over a
live schedule is exactly what the neighbouring tree had to build a refusal gate
against.

ALSO REJECTED: adding "dado" to TDI's EXPORT_HERMES_RUNTIME.ps1. That script
writes into `C:\AgentTeam\HermesProfiles\` — putting FRP Depot's schedule
there is a data-wall breach for a convenience. Nothing under C:\AgentTeam is
touched by this.

Writes `DadoProfile\cron\`: `jobs.mirror.json` (definitions only — runtime
bookkeeping stripped so it changes when the SCHEDULE changes, not 288x a day),
`scripts.mirror.json` (sha256 of what is deployed vs its repo original — content
deliberately not copied, since a second copy in the same repo is the drift this
exists to catch), and `RECREATE.md` (rebuild lines for a HUMAN).

TWO THINGS IT FOUND IMMEDIATELY:
 1. `config.yaml`'s committed mirror is FIVE parsed keys behind live, including
    the entire `platforms.discord` block — the mirror does not know Dado has a
    Discord lane. A TEXT diff cannot see this: the two files differ in key order
    and hand-written comments, so the noise hides the gap. The check is
    therefore semantic (yaml.safe_load), not textual. NOT auto-corrected — that
    file is being edited by another session; closing the gap is a separate,
    deliberate act.
 2. A false positive in the FIRST version of the checker, worth recording: two
    deployed GLA scripts are not stale copies but deliberate LAUNCHER SHIMS that
    put this repo on sys.path and delegate into it, so the repo file IS the
    executing code. Reporting them as drift every run is how a gate gets
    ignored. Launchers are now classified and exempt; 12 real copies are
    compared by hash.

### FIXED 2026-08-12 — live `fallback_providers` contradicted the recorded decision

CLAUDE.md: "NO fallback provider on purpose: primary down = honest failure,
never silent model drift (TDI learned this the hard way 2026-07-16)."

The LIVE dado profile carries:
    fallback_providers: [{provider: nous, model: deepseek/deepseek-v4-pro}]

Three reasons this matters rather than being cosmetic drift:
 1. It contradicts an explicit, dated decision of Rachad's.
 2. `nous` is recorded as UNFUNDED — "marking nous unhealthy for 600s
    (payment / credit error)" plus a hard 404 on that model. A Codex hiccup
    would route Dado at a provider that cannot answer.
 3. It matches a KNOWN BUG SHAPE. TDI's CLAUDE.md records Hermes falling back
    to deepseek-v4-pro and PERSISTING that fallback into config.yaml as the new
    default, with a standing "re-check after any Codex outage" warning. Nobody
    is recorded as having chosen this for Dado.

NOT mirrored into DadoProfile\config.yaml, deliberately: that file is the
INTENDED configuration, so leaving the key out means a runtime import removes
the unintended fallback rather than cementing it. The profile-mirror drift check
treats it as EXPECTED divergence pinned to this exact value — silent while it
stays, loud if live changes to anything else, silent again once it is gone.

RESOLVED the same day: Rachad said remove it. The key is gone from the live
profile, the gateway was restarted so it actually took effect, and the running
config now reads model gpt-5.6-sol / openai-codex with NO fallback - primary
down is once again an honest failure. Verified after the restart: both chat
lanes healthy, cron ticker beating, config drift clean.

*** THE EXPECTED-DIVERGENCE ENTRY WAS REMOVED WITH IT, ON PURPOSE. *** While the
key was live-only, the profile-mirror check treated it as expected divergence so
it would not cry wolf every five minutes. Now that mirror and live agree there is
nothing to expect - and leaving the entry would make the checker go SILENT if the
key came back. Hermes is recorded as PERSISTING a fallback into config.yaml after
a provider hiccup, so a reappearance is the bug recurring, not a new decision. It
is now flagged loudly. Do not re-add that entry.

### B-09 FIXED (this commit) — `scrub_noise` deletes legitimate business lines

Fixed 2026-07-25. `NOISE_LINE` is anchored to line start, and `job id` / `cleaned up` were removed from it entirely — both are ordinary operations English. The ticker branch now only peels allowlisted frame words, so "Pending... payment from SCT" keeps its first word.

Original report follows.
`dado_inbox_reasoner.py:192`. `NOISE_LINE` drops the WHOLE line on `\bjob[_ ]?id\b`
or `cleaned[_ ]?up`, so "Their job ID 88-A needs the FRP grating quote priced by
Friday" vanishes; if it was the only substantive line the alert is suppressed by
the `len(clean) < 8` guard. Separately `FRAME_TOKEN`'s ticker branch
(`[a-z]+ing\s*\.{2,}`) turns "Pending... payment from SCT" into "payment from
SCT", inverting the first word Rachad reads.
FIX — anchor the machinery patterns to line-start / whole-line; drop `job id`
and `cleaned up`.

### B-10 FIXED (this commit) — A turn older than the 30k-line tail is invisible to the tripwire

Fixed 2026-07-25. `read_tail` seeks backwards by bytes (`TAIL_BYTES`, 8 MB) instead of loading the whole file, and when no turn-start line is in view the rotated siblings are prepended — so a rotation mid-turn can no longer hide an open stall.

Original report follows.
`stall_tripwire.py:77`. `read_tail` keeps 30,000 lines; a turn whose start line
rolls out (or a log rotation mid-turn) has no entry, and line 174 deletes its
record. The 3-hour stall this was written for becomes undetectable exactly when
the log is busiest. Also loads the whole file into memory every 15 min.
FIX — seek from the end by bytes; size the window by wall-clock age; include the
rotated log when the newest turn start is older than the window.

---

## P1 — follow-up tracker correctness (beyond B-01)

### B-11 FIXED (this commit) — One unsent draft removes a live money thread from the tracker forever

Fixed 2026-07-25. `chase_draft_pending` now answers "did WE chase this thread",
read from `Dado/30_Memory/chase_log.jsonl`, which `outlook_tool.record_chase`
appends to whenever a reply-all draft is created. It expires after
`CHASE_QUIET_DAYS = 7`, matching the promise already in the digest prompt, so
an unsent chase resurfaces instead of hiding the thread forever. `chased_on` is
reported; `drafts_in_thread` is kept but is now informational only and decides
nothing. The digest prompt was updated to match.

Behavioural proof, same input through both versions — a 21-day-old Nashtec
thread with one unrelated draft and no chase ever made:
`BEFORE: overdue_count=0 already_chased=1` → invisible;
`AFTER: overdue_count=1 already_chased=0` → tracked.

9 tests in `Dado/Tools/outlook/test_followup_tracker.py`, including the
round-trip across the two modules (they must agree on the log format — that is
the seam), stale-chase expiry, and corrupt/naive timestamps not blinding the
tracker. NOTE those 9 only *error* against the pre-fix files (missing
attribute), so the behavioural comparison above is what actually demonstrates
the bug. `outlook_check.py` remains read-only over Graph — the chase log is a
local file read, no new HTTP verbs.

No migration needed: B-01 meant no chase draft had ever been successfully
created, so there is no history to backfill.

Original report follows.
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

### B-12 FIXED (this commit) — The 60-day window silently drops the longest-overdue threads

Fixed 2026-07-25. Threads are now carried forward in `30_Memory/followup_watch.json` until answered, so ageing past `days_back` cannot retire one unchased. QT-000023 no longer disappears on 2026-07-27. Carried threads are flagged `carried_forward` and counted; an answered thread drops off the watch by itself.

Original report follows.
`outlook_check.py:324`. Seeded only from Sent Items with `sentDateTime ge
now-60d`, so the longer a thread is ignored the closer it gets to falling out
entirely. **QT-000023** (last sent 2026-05-27, 42 working days) leaves the window
on **2026-07-27** and vanishes from every future digest with no record it was
ever tracked. Nothing distinguishes "resolved" from "aged out unchased".
FIX — filter on conversation activity, or keep a persistent tracker carried
forward until answered or explicitly closed; at minimum emit an `aged_out` list.

### B-13 FIXED (this commit) — Empty `my_addr` turns the tracker into a permanent all-clear

Fixed 2026-07-25. An unresolvable mailbox address now prints an error and exits non-zero instead of skipping every candidate and reporting a clean morning.

Original report follows.
`outlook_check.py:345`. `my_address()` swallows both failures and returns `""`;
the ownership test then skips every candidate, JSON stays valid,
`overdue_count` is 0, digest exits silent. `prefetch` only checks the return
code and the JSON parse.
FIX — exit non-zero on empty `you`; have `prefetch` reject a blank `you` field.

### B-14 FIXED (this commit) — Working-day clock mixes UTC instants with Eastern days

Fixed 2026-07-25. Both ends of the wait clock are converted to `America/Toronto` before the date is taken. The measured same-Monday pair (19:30Z and 01:30Z) now agrees at 4 days where it used to differ by a full working day.

Original report follows.
`outlook_check.py:210`. Measured: `business_days_since('2026-07-20T19:30:00Z')`
= 4 but `('2026-07-21T01:30:00Z')` = 3 — two mails sent the same Monday (15:30
and 21:30 ET) differ by a whole working day. Anything sent after ~20:00 ET goes
overdue a day late; the 19:00 sweep under EST is 00:00 UTC next day, adding a
phantom day and firing items early.
FIX — convert both ends to `ZoneInfo('America/Toronto')` before `.date()`.

### B-15 FIXED (this commit) — Sent Items capped at `$top=250`, truncation invisible

Fixed 2026-07-25. `_all_sent_since` follows `@odata.nextLink` up to `SENT_PAGE_LIMIT` (20 pages / 5,000 messages) and reports `sent_window_truncated` when it hits the cap, instead of silently discarding the oldest mail.

Original report follows.
`outlook_check.py:326`. No `@odata.nextLink` follow-up; ordering is
`sentDateTime desc`, so overflow discards the OLDEST mail — the most overdue
threads. Headroom is thin (99 sent in 60 days; 300 reach back to 2026-01-06), and
`days_back` is a free-form argument.
FIX — page until the window is exhausted, or emit `truncated: true`/`oldest_seen`.

### B-16 PARTIALLY FIXED — Category inferred from quoted history and forwarded bank notices

Fixed 2026-07-25, but only two of the three parts. DONE: a quote-numbered subject
now outranks payment words (`classify_thread('Quote QT-000099 for FRP pipe',
'Deposit invoice attached')` is `rfq_quote`, so it waits 5 working days not 7),
and `strip_quoted()` removes the quoted history so only Rachad's own added text
is classified.

**NOT FIXED — the forwarded-bank-notice case, which was the headline example.**
The exclusion I added tests whether the thread's ROOT message came from an
automated sender. That cannot work for a forward: forwarding starts a NEW
conversation whose root is RACHAD'S OWN message, so the bank's address is not in
this thread at all. Verified against the live mailbox 2026-07-25 — "Fw: You
received a deposit of 49,100.00 USD" is still `payment` + `urgent` + overdue at
27 working days (conversation has 2 messages, external party
`anh@troydualam.com`). The unit test I wrote passes because it models the bank as
the root message, which is not the shape the real data has. My test was wrong,
not the code.

DELIBERATELY LEFT VISIBLE rather than guessed at. A heuristic that suppresses
"forwarded notice" threads risks hiding real money, and the wrong direction to
err at 2am on data I cannot ask about. Mislabelled-but-visible beats hidden.

RACHAD'S CALL, and the real question is not technical: when you forward a
deposit notice to someone and they never reply, do you want that on the
follow-up list at all? If yes, it should probably not be `urgent`. If no, the
cleanest signal is the `Fw:`/`FW:` subject prefix combined with no inbound
external message in the thread — but that also describes a cold RFQ, which you
DO want chased, so it needs your judgement rather than mine.

Original report follows.
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

### B-18 FIXED (this commit) — The new 900s collection is a hard prerequisite for the whole sweep

Fixed 2026-07-25. The waiting-on-them collection is best-effort: on failure the sweep continues on its other three sources and writes an `unavailable` marker telling the model not to infer that nothing is overdue. A failure in a core source still raises.

Original report follows.
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

### B-20 FIXED (this commit) — Scanned PDFs stored as `indexed` with EMPTY content are skipped entirely

Fixed 2026-07-25. `candidates()` now selects on the real condition — `content IS NULL OR trim(content) = ''` — so an image-only PDF stored `indexed` with empty content is picked up instead of excluded forever. The `backfill_%` exclusion stays, so runs still converge.

Original report follows.
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

### B-21 FIXED (this commit) — `text_from_eml` promotes a failed body read to "read-and-clear"

Fixed 2026-07-25. Extractors return an `Extraction(text, complete)` NamedTuple. `text_from_eml` reports `complete=False` when the body extraction raises or `get_body()` returns None, so a failed body read is no longer indistinguishable from an empty one and the row is written `backfill_partial_eml` rather than read-and-clear.

Original report follows.
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

### B-22 FIXED (this commit) — Deliberately partial extractions are recorded as full reads

Fixed 2026-07-25. Partial extractions are recorded as `backfill_partial_<kind>`: the scanned-PDF page cap, the xls row/`MAX_TEXT` truncation, and zip (names-only, so never complete). `google_reference.py` already returns `content_status` verbatim in its Drive results, so a partial row reads as a pointer to verify without further change there.

Original report follows.
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

### B-24 FIXED (this commit) — DB writes outside the try, no `busy_timeout`

Fixed 2026-07-25. The connection sets `PRAGMA busy_timeout=30000`, and every write moved inside a `try` that counts `db_errors` and continues. A concurrent `google_indexer`/`google_rescreen` lock no longer kills the whole 45-minute run with no summary and no receipt.

Original report follows.
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

### B-26 FIXED (this commit) — `reconnect` is unreachable from the buttons Rachad actually uses

Fixed 2026-07-25. Buttons landed in a2ef5c7 (another session): RECONNECT_DADO_GOOGLE.bat and RECONNECT_DADO_GOOGLE_READ_SERVICES.bat. The second half is done here — the unforced `connect` reuse path now prints "Reused the sign-in already stored on this PC... No browser opened, so NOTHING was replaced" plus a pointer to the reconnect button, so a silent early return can no longer read as a successful re-sign-in.

Original report follows.
`google_extended_auth.py:251`. All three existing buttons still run the unforced
verbs, and no `RECONNECT_*.bat` exists at the repo root. CLAUDE.md says buttons
over commands, so the operator repeats the same double-click, gets the same
silent early return and the same false VERIFIED, and the fix never fires.
FIX — add `RECONNECT_DADO_GOOGLE.bat` and
`RECONNECT_DADO_GOOGLE_READ_SERVICES.bat`; have the unforced path print
"Reused the stored sign-in; no browser opened. To mint a NEW token run
RECONNECT_...bat".

### B-27 FIXED (this commit) — "Sign-in should now be long-lived" is printed for pre-switch tokens

Fixed 2026-07-25. The blanket "should now be long-lived" line is gone. Lifetime is now reported from a recorded ISSUE date (`token.json.minted`), written at the single site where a browser consent actually issues a refund token. NOTE the original suggestion — stamp the token file's mtime — would have LIED: `_save()` rewrites token.json on every silent refresh, so an old grant looks freshly minted. Where no issue date was recorded the tool says exactly that rather than guessing, which is what the live token reports today. Also added: a sign-in that comes back with no refresh token is now REFUSED instead of overwriting the working one.

Original report follows.
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

### B-28 FIXED (this commit) — `settings_group_warnings` never reaches the operator

Fixed 2026-07-25. Each skipped endpoint now becomes a real `finding` (severity medium) BEFORE the severity Counter is taken, so it flows into the summary counts, the markdown and stdout. `summary.endpoints_skipped` added. The warning is no longer visible only inside the audit JSON under %LOCALAPPDATA%.

Original report follows.
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

### B-29 FIXED (this commit) — The same list-then-fetch race is unguarded elsewhere

Fixed 2026-07-25. `WooError` carries `.status` and `.code` as attributes, parsed from the response, so callers stop deciding what a failure MEANT by searching its message text. New `wc.api_get_optional()` returns None on a genuine 404 and records it; auth, 5xx and network failures still raise, because those mean the numbers are wrong rather than merely incomplete. Applied to the settings groups and to BOTH per-zone shipping fetches.

Original report follows.
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

### B-30 FIXED (this commit) — `receipts.jsonl` has three timestamp formats plus one corrupt line

Fixed 2026-07-25. `job_runner` was the only naive writer in the tree; its receipts now use an aware UTC `receipt_stamp()`. Job records keep local wall-clock times in their own file, which is deliberate — a human reads those. `test_receipt_format.py` pins all nine writers.

**The malformed line 90 was deliberately NOT rewritten.** It is a 2026-07-23 audit record, and editing history to make a log parse is worse than the parse failure. `conduct_collect.tail_jsonl` matches raw substrings and never parses JSON, so the bundle is unaffected; only ad-hoc JSON queries need to tolerate it. If Rachad wants it quarantined rather than left in place, that is a one-line change and his call.

Original report follows.
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


---

## Dado's review, 2026-07-25 — her findings, not mine

Rachad asked for the night's work to be run past Dado before he woke. She read
the backlog and her own live overdue list. She was right about the two things
fixed below, and raised six more that are HIS call, not mine. Recorded verbatim
in substance because she has mailbox context the backend does not.

### D-01 FIXED (this commit) — record_chase fired on EVERY reply-all draft
Her finding, and a regression I introduced with B-11. `command_reply_all` logged
a chase after any successful draft, with no "this is a follow-up" distinction —
so an ordinary customer reply would suppress that thread from follow-up
monitoring for 7 days. That is the B-11 bug again, just narrower. Now gated on an
explicit `--chase` / `is_chase`, and the digest prompt tells Dado to pass it for
chases only.

### D-02 FIXED (this commit) — "already_chased" was untrue
Her point: the draft is UNSENT. Calling the thread "already chased" claims
something the evidence does not support, and if Rachad never sends it the thread
vanishes for 7 days. Renamed to `chase_draft_waiting` / `chase_drafted_on`, and
the digest prompt now says the honest phrasing is "a chase draft is waiting".

### D-03 OPEN — `support@info.airwallex.com` is not detected as automated
`_is_automated` matches local-part prefixes only (no-reply, notification,
bounce...). The live Airwallex deposit notices come from `support@`, which is a
perfectly ordinary human address at most companies. This is the concrete evidence
B-16 lacked. Fixing it needs either a known-automated-domain list or message
headers — and blanket-treating `support@` as automated WOULD hide real threads.
**Rachad's call.**

### D-04 OPEN — threads where his last message closed the matter
She read the actual previews. Three of the 15 "overdue" items are not waiting on
anyone: "Bien reçu ; merci" (Fibre Mauricie payment confirmation), "Thanks for
the Clarification Hunter" (LinkedIn), and "Je t'envoie le prix tt de suite"
(Fibre Mauricie commande) — that last one is Rachad promising HIS next action, so
it belongs under `--awaiting`, not here. Detecting acknowledgement and
self-promise intent is a heuristic that can hide money if it over-fires.

### D-05 OPEN — related conversations are not reconciled across conversation IDs
"Re: Payment of CAD4,101.30 is outstanding for INV-000040" is overdue, but a
NEWER separate conversation for the same invoice carries payment proof, and the
alert ledger already records Plooto reporting it completed. Conversation ID alone
is insufficient; invoice/quote/order numbers would need to be reconciled.

### D-06 OPEN — the CLOSED ledger is applied too late
The digest tells Dado to check `alert_ledger.md` per thread, AFTER the workload
is built. With B-17 open (`agent.max_turns: 60`), closed items consume turns that
money threads need. Pre-filtering CLOSED deterministically would fix it, but the
ledger is prose, so parsing it reliably is its own risk.

### D-07 OPEN — "Re: El Paso Projects" is addressed to Troy Dualam
She flags that an answer wanted FROM Troy Dualam should go through the sanctioned
Aze relay when Rachad instructs it, rather than into an automatic customer chase
lane. Note this brushes Hard Rule 4 in both directions — raise it, do not act.

### D-08 — her correction to my summary, and she is right
"Silence now means silence" is not true system-wide while **B-08** (job-watch and
tripwire deliver via cron with no retry or queue) and **B-17** (a 15-item digest
can exceed `max_turns: 60` and truncate with no indication) are open. Both are
the two items Rachad reserved for himself.
UPDATE 2026-08-12: **B-08 is FIXED**, and it was put to him first rather than
applied silently, precisely because of this line. He chose the option that drops
hermes' "Cronjob Response:" wrapper. **B-17 remains open and still reserved.**


---

## Found by the watch sweep, 2026-07-25 14:30

### W-01 FIXED (this commit) — Dado's gateway had no auto-start; she was unreachable ~13h

Rachad asked "confirm that you're watching dado". The sweep answered it: port
8647 had no listener. TDI's 8642 was fine, so it was Dado specifically.

TIMELINE. Last gateway activity 01:19:33 (an idle-cache eviction after his 00:16
exchange). Port already dead when checked at ~10:25. PC restarted 10:51-10:54
(event 1074, user-initiated). Still down at 14:35 when he asked.

CAUSE OF THE DEATH: not determined, and not claimed. No Windows crash event
(1000/1001/1026), nothing in errors.log, and gateway-exit-diag.log records only
`gateway.start` — verified to stay silent through a forced kill, so its missing
exit line is not evidence either way.

CAUSE OF STAYING DOWN: no auto-start existed. TDI/Aze has three (watchdog task,
Startup .vbs, restart task); FRP Depot had none. The reboot alone guaranteed
this outcome regardless of what killed the process.

WHY NOTHING CAUGHT IT: every other health signal looked fine. The crons kept
running and reporting `ok` — they do not depend on the gateway listener — so
conduct review, inbox watch, tripwire and job-watch all passed while the one
thing Rachad actually uses was dead. **A green cron list is not evidence the
gateway is up.** The session-start sweep checks port 8647 for exactly this
reason; it is the check that found this.

FIX: `dado_gateway_watchdog.ps1` + a 5-minute scheduled task + a logon .vbs, all
deferring to a disable flag so STOP_DADO.bat still means stop. Verified by
killing the live gateway (pid 16096) with no flag set and watching the watchdog
bring it back as pid 19244, with both events logged.

### W-02 FIXED (this commit) — nothing told Rachad when the gateway could not recover

He asked for it directly: "Alert me another way if the gateway can't recover."

The constraint is that every normal path to him runs through hermes — `send_clean`,
the cron `deliver: telegram`, Dado herself — and in this scenario hermes is
exactly what is suspect. An alerter sharing a failure mode with the thing it
reports on is not an alerter.

`Dado\Tools\watch\dado_urgent_alert.py` posts straight to the Telegram Bot API
over HTTPS using nothing but the standard library: no hermes, no gateway, no venv
packages, no Dado tooling. The bot token comes from the profile .env and is never
printed or logged — errors report `HTTP <code>` only, never the URL, which
carries the token.

Layers, in order: direct Telegram API → a `DADO_NEEDS_ATTENTION.txt` file on the
Desktop if even that fails (crude on purpose; a local file is the one thing that
still works when the network is the problem) → a line in `urgent_alert.log`.

One alert per reason per hour. The watchdog runs every 5 minutes, so without a
cooldown a weekend outage would be 200+ identical messages, which is its own way
of being ignored. Recovery clears the marker and the cooldown, scoped to the
reason so it cannot reset an unrelated future alert.

NOT a general-purpose sender, and deliberately so: there is no mail send path
anywhere in this tree (Golden Rule 1) and this does not create one. It posts
operational alerts about Dado's own health to Rachad's own chat.

REMAINING GAP, honestly: it shares Telegram-the-service and the network with the
normal path. If Telegram itself is unreachable, only the Desktop file remains. A
genuinely independent channel would mean a different service, or routing via
Aze's gateway (a separate process on 8642 that survived this outage) — the latter
crosses the company line and is Rachad's call, not mine.


---

## Found by the cron-message audit, 2026-08-02 (Rachad: "check all the cron messages I got from Dado on Telegram")

### A-01 FIXED (config, Rachad-approved) — hermes verify-on-stop replaced cron finals with verification babble
Root cause of the 08-01 monthly-reorder Telegram message being a "Verification
remains blocked..." stub (receipt falsely claimed issued) and of the 07-31 13:07
Q26-1549 PO-cutoff re-alert being scrubbed to nothing while the ledger said
"Re-alerted". Mechanism: hermes injects a verify-on-stop nudge when a turn edits
"code" (receipts.jsonl counts — .jsonl is not in its prose exemption) and the
model's ANSWER TO THE NUDGE becomes the final message; cron/`-z` headless turns
do not register as messaging surfaces, so "auto" armed it. Fixed with
`hermes -p dado config set agent.verify_on_stop false` (hermes's own off-switch;
their changelog calls the narrative "more noise than signal"). Config hot-reloads
on mtime. Ledger corrected; correction receipt appended. Commits a727b07,
e91daff.

### A-02 FIXED a727b07 — conduct runner: transient claude crash cost a whole night; mojibake on every ping
08-01 05:10 claude.exe exited 1 in ~3s with empty stderr → no 07-31 review, no
nightly commit, no push, and the failure text showed only stderr (claude errors
mostly go to stdout). Now: one retry on fast (<120s) failures, full
stdout/stderr preserved in `40_Logs\conduct\<day>-claude-failures.txt`, and
every Telegram-bound line ASCII-sanitized (all six 07-24..07-31 needs-you pings
reached Rachad with "â€\x9d"-mojibake — hermes decodes the script's UTF-8 stdout
as ANSI). Missed 07-31 review recovered by running the fixed script (8bc5647).

### A-03 FIXED a727b07 — profile scripts dir (what cron RUNS) was stale on three tools
The nightly reviewer auto-fixes REPO copies but is barred from writing outside
C:\FRPDepot, so its fixes never reach `%LOCALAPPDATA%\...\profiles\dado\scripts\`.
Found: conduct_review/conduct_collect stale (receipt-UTC-day, fit-profile
truncation, error-dedup bugs all still live in cron), zoho_reorder_analysis
missing its dateless-invoice crash guard; job_runner had a profile-side
improvement (CREATE_NO_WINDOW) missing from the repo — synced INTO the repo.
All seven cron/tool scripts hash-verified in sync. Session-start sweep now
includes this check (memory updated).

### A-04 FIXED b36f02a (quote/customer) + this commit (item tool) — Zoho approval is the plain word APPROVED on BOTH tools
Rachad's 2026-08-02 answers to the 07-30 review question: quote/customer AND
item tools now require `APPROVED` (digest validated internally from the plan
file), Hard Rule 3 rewritten — he ANSWERS THE PLAN in his own message, Dado
never supplies the word. Live SOUL synced.

### A-05 FIXED 68abd87 — chase-own: follow-ups for threads nobody ever answered
07-31 review FINDING 4 (5 genuine follow-ups, zero drafts possible — no external
source message exists in a thread Rachad started and nobody replied to).
Rachad-approved: `outlook_tool.py reply-all --chase-own` drafts under his OWN
latest sent message, original recipients; refused whenever any live external
message exists, on non-chase use, and when an external reply lands mid-draft.
Digest prompt, skill and fit profile updated. 23/23 + tracker suites green.

### A-07 OPEN (Rachad's call — money) — Nous auxiliary account out of credits since 2026-08-01 19:10
UPDATE 2026-08-03 (08-02 review FINDING 4): the failure signature changed on
08-02 to `Firecrawl client initialization failed: missing direct config and
tool-gateway auth` — consistent with the same root cause (no direct firecrawl
API key configured, and the Nous tool-gateway auth died with the subscription),
but after any top-up, VERIFY web_search actually works before closing this; if
init still fails, the fix is a direct firecrawl key or a different web backend.
Dado's auxiliary services all point at nous/deepseek-v4-pro (config.yaml:
vision, title_generation, compression, summary, web_extract, approval) and
web.backend firecrawl rides the same subscription. errors.log shows
SUBSCRIPTION_REQUIRED / "balance is too low" from 2026-08-01 19:10 onward:
web_search is BROKEN, title generation failing (cosmetic), and vision will fail
the next time Rachad sends Dado a photo on Telegram. Compression has NOT failed
yet (0 hits) but shares the account. The MAIN model (gpt-5.6-sol on
openai-codex) is unaffected — live PIPELINE-OK turn 2026-08-02 01:3x. Options:
top up at portal.nousresearch.com, or repoint auxiliaries at a free model —
either is a spend/model choice, so his call, not a backend auto-fix (no silent
model drift).

### A-08 OPEN — conduct bundle carries no outbound text (08-01 review FINDING 5)
`conduct_collect.py` collects only inbound messages; Dado's replies survive as a
char count. Two mandated checks (anything sent/promised, invented facts) cannot
be evidenced. Backend check needed: does gateway.log (or state.db) record reply
text, and can the collector include a bounded outbound section per turn?

### A-10 FIXED 2026-08-04 — rg pattern starting with `--` is consumed as a flag (08-03 review FINDING 5)
`search_files` died on pattern `--thread|add_argument("--thread|...` — rg parsed
it as a flag. Fix belongs in the existing LOCAL PATCH to shared hermes
`tools\file_operations.py` (the 2026-07-24 native-path patch): pass the pattern
after `-e` (or a `--` separator) at the three rg call sites. Shared pinned
install — do it deliberately, re-verify the path patch afterwards, note in
CLAUDE.md's patch section. Applied after the v0.20.0 update and covered by a
focused regression test (1 passed); native Windows path handling remains in
place. The running gateway must restart once to load the edited module.

### A-11 OPEN — 07:00 sweep and 08:00 digest double-touch the same thread (08-03 review FINDING 3)
Global Trade Links: sweep asked approval for a chase at 07:0x, digest prepared
the chase at 08:08 unasked. Dedup needed: digest drops threads the same
morning's sweep already raised, or rule 3b stays silent when the digest is <1h
away. Cross-file behavior change (reasoner + digest prompts/charter) — design
with Rachad's preference, not a quick edit.

### A-12 RESOLVED-NO-ACTION — duplicate Fibre Mauricie drafts (08-03 review FINDING 1 / NEEDS-RACHAD)
Graph readback 08-04: Drafts holds ZERO "Tuyauterie" messages; Sent Items holds
TWO — the pre-correction reply sent 18:10 local 08-03, the corrected one sent
18:53. Both receipt draft-ids died on send (normal Outlook behavior; explains
the missing supersede receipt). The customer received the correction 43 min
after the wrong version. Nothing to clean up; the 05:15 ping's warning was
already moot. Reviewer's other ask stands as a small improvement: reply-all
receipts should carry conversation id + subject, not just the opaque message id.

### A-09 FIX-LOADED 08-04; verify at 04:00 on 08-05 — daily reset never fired (08-03 review FINDING 2)
The 08-02 `session_reset.mode: daily` write is correct in the live config, but
the GATEWAY BUILDS ITS CONFIG OBJECT ONCE AT STARTUP (`load_gateway_config()`;
only .env is per-turn reloaded) and Dado's gateway (pid 28456) started 07-28 —
it still runs `mode: none`. Evidence: sid 20260802_115426 served all of 08-03
(~35h), no reset at the 04:00 boundary despite 5 contacts.
THE FIX NEEDS A GATEWAY RESTART, AND THE BACKEND CANNOT DO IT: this Claude Code
session is a CHILD of the gateway (powershell←claude.exe←bash←bash←pythonw
28456), hermes refuses `gateway restart` from inside its own tree, and killing
the gateway kills this session and its session-only watch crons. Rachad must
double-click STOP_DADO.bat then START_DADO.bat from Explorer (10 seconds;
STOP's process matcher fixed 08-04 to also catch the `--profile` form the
Startup .vbs uses). Until then, `/new` typed to Dado on Telegram is the manual
substitute. After the restart: reopen a backend session and re-arm the watch
per the dado-watch-thread memory.

UPDATE 2026-08-04 10:44: Dado's watchdog started a new gateway after the Hermes
v0.20.0 update. The running process therefore loaded `session_reset.mode: daily`
and `at_hour: 4`. Do not call this fully verified until the 08-05 04:00 boundary
creates a fresh session as configured.

### A-13 PATCHED-PENDING-RESTART — v0.20.0 Windows lifecycle guard broke full-path Python commands
Every command invoking the required full venv path failed before execution with
`ValueError: open: embedded null character in path`. Root cause: a local PE
binary returned `None` from `_read_referenced_script`, which wrongly activated
the remote-script fallback; decoded binary bytes were recursively tokenized as
paths. Shared-install patch now returns empty text for local binaries and catches
NUL-path `ValueError`. Direct regression class: 14 passed. FRP's 236-test suite
and every live system check pass when invoked with bare `python`. A Dado gateway
restart is still required to load the patched module into the live tool process;
after restart, verify the original full-path Python command and close this item.

### A-09-prior FIXED (config, Rachad-approved 08-02) — 43-hour agent session; compressor timing out (08-01 review FINDING 2)
Session 20260730_233504 served turns for ~43h; context summary failed twice with
524 origin timeouts; two of Rachad's messages sat ~16 min unanswered (FINDING 1
is largely downstream). Rachad approved ("Yes proceed", 08-02):
`session_reset.mode: daily` set via hermes config CLI — sessions now reset at
04:00 local (at_hour was already 4), before the 05:10 conduct run and the 07:00
sweeps; `notify: true` tells him in chat when it happens; a reset-guard spares
sessions with live background processes (<24h old). Mirror synced. Watch point:
first reset fires 2026-08-03 04:00 — Monday's 09:23 watch confirms it behaved.
Related, still open: Telegram progress-EDIT failures ("Message to edit not
found") live in shared hermes' telegram adapter — pinned install, not a repo
fix.

### A-06 FIXED (this commit) — Stefe loans balance could silently diverge from the sheet (07-31 review FINDING 3)
`D4 = SUM(D6:D212)` but the tool read only C1:E44, so content in rows 45:212
would falsify every current/resulting balance Rachad approves. Read widened to
C1:E212 with rows 45:212 required EMPTY (fail closed), same guard on post-write
readback; backups now capture the full range. 55/55 tests. Live read-only
`check-stefe` verified against the real sheet: rows 45:212 are empty, balance
-15 matches the 08-01 committed plan — the bug was latent, not an active
miscalculation.
