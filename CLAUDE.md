# CLAUDE.md — FRP Depot agent tree (Dado)

Created 2026-07-22. This is FRP DEPOT's tree — a DIFFERENT COMPANY
from Troy Dualam (TDI, C:\AgentTeam). Hard wall both ways: Dado never
reads C:\AgentTeam; TDI agents never read C:\FRPDepot.

## What this is
One Hermes profile: **dado** — Rachad's operations assistant for FRP
Depot. Email drafting (Outlook, DRAFTS ONLY), reporting and quotes
from Zoho Books/Invoice + Zoho Inventory (READ-ONLY until a write
tool is commissioned). No engineering engine — quotes are price-list
based. Rachad is the only operator; Claude Code is the backend
engineer.

## Machine / runtime
- Server: BKV-TD-SERVER01 (also hosts the TDI team — see
  C:\AgentTeam\CLAUDE.local.md; do not disturb Aze's gateway 8642).
- Hermes profile: %LOCALAPPDATA%\hermes\profiles\dado\
  (SOUL.md + config.yaml + .env). Mirror in this repo: DadoProfile\.
- Gateway port: 8647 (127.0.0.1). Start/stop: START_DADO.bat /
  STOP_DADO.bat at the repo root.
- *** GATEWAY KEEP-ALIVE (added 2026-07-25) — TWO PIECES, ONE OUTSIDE THE REPO. ***
  Dado had NO auto-start of any kind while TDI/Aze had three (a watchdog task, a
  Startup entry, a restart task). Her gateway stopped some time between 01:19 and
  10:25 on 2026-07-25 — no exit record, no Windows crash event, nothing in
  errors.log — and the 10:51 PC restart then left it down. Rachad could not reach
  her on Telegram for ~13h, and NOTHING flagged it: the crons kept running, so
  every other health signal looked fine. Two failure modes to survive, not one.
  1. `Dado\Tools\watch\dado_gateway_watchdog.ps1` (in the repo) — starts the
     gateway iff 8647 has no listener; silent when healthy; logs to
     40_Logs\gateway_watchdog.log.
  2. Scheduled task "FRPDepot Dado Gateway Keep-Alive", every 5 min. Registered
     with `schtasks`, NOT the PowerShell cmdlet — Register-ScheduledTask returns
     "Access is denied" for this user.
  3. `%APPDATA%\...\Start Menu\Programs\Startup\FRPDepot_Dado_AutoStart.vbs`
     (NOT in the repo — recreate by hand on a rebuild) so logon recovery takes
     seconds rather than up to 5 minutes.
  DELIBERATE STOPS ARE RESPECTED: STOP_DADO.bat writes
  40_Logs\gateway_disabled.flag and START_DADO.bat clears it; both mechanisms
  check it, so neither can resurrect a gateway stopped on purpose. Do not remove
  that flag handling without replacing it, or STOP_DADO becomes a 5-minute pause.
  LIMIT: the task runs only while TDI-service is logged on (running otherwise
  needs a stored password, which is not acceptable). TDI's own auto-start has the
  same constraint.
  NOTE gateway-exit-diag.log records ONLY `gateway.start`, never an exit — it was
  verified to stay silent through a forced kill, so a missing exit line there is
  NOT evidence about how a gateway died.
- Model: gpt-5.6-sol on openai-codex (global OAuth, shared plan with
  the TDI five — quota pressure is a known watch item). NO fallback
  provider on purpose: primary down = honest failure, never silent
  model drift (TDI learned this the hard way 2026-07-16).
- Hermes is PINNED — never `hermes update` casually (TDI rule, same
  install).
- *** LOCAL PATCH TO SHARED HERMES (2026-07-24) — RE-APPLY AFTER ANY UPDATE. ***
  File: %LOCALAPPDATA%\hermes\hermes-agent\tools\file_operations.py
  Adds ShellFileOperations._escape_native_path_arg and uses it for the PATH
  argument at the three `rg` call sites (_search_content once, _search_files_rg
  twice). Rachad approved patching the shared install 2026-07-24.
  THE BUG: _escape_shell_arg rewrites C:\x to the MSYS /c/x form because bash
  builtins need it, then single-quotes it. `rg` here is rg.exe, a NATIVE
  Windows binary — and single-quoting SUPPRESSES MSYS's usual argument
  conversion, so rg received the literal "/c/FRPDepot/..." and died with
  "IO error ... (os error 3)". The tell: `test -e '/c/FRPDepot/...'` SUCCEEDS
  (bash builtin, resolves POSIX fine) and only the following rg call fails on
  the identical path. Dado then retried the same doomed search — that is the
  mechanism behind 2026-07-23 conduct FINDING 3 (15+ failures) and 7 more on
  2026-07-24, and a large part of why her turns ran long.
  NOT CHANGED, deliberately: _search_with_grep. grep here comes from Git Bash
  and IS an MSYS binary, so it wants the POSIX form. Only native binaries get
  the Windows form.
  A first attempt patched file_tools.search_tool instead and was reverted — it
  normalised the path earlier, but _escape_shell_arg converted it straight back,
  so it was a no-op. Fix the ARGUMENT HANDED TO THE NATIVE BINARY, not the
  caller. Verified after: MSYS, Windows, /c/Intercompany, target=content and
  target=files all return matches; /home/x, relative paths and "." unchanged.
  *** v0.20.0 additions (2026-08-04) — also re-apply after updates. ***
  1. Every native-rg content pattern is passed after `-e`, including the three
     zero-match probes. Otherwise a valid pattern beginning with `--` is parsed
     as an rg option and dies. Those probe paths also use
     `_escape_native_path_arg`. Regression test:
     `TestSearchPathValidation::test_search_rg_pattern_starting_with_hyphens_uses_expression_flag`.
  2. File `%LOCALAPPDATA%\hermes\hermes-agent\cron\lifecycle_guard.py`:
     `_read_referenced_script` returns empty text (not `None`) for a local binary
     and catches `ValueError` from NUL-bearing paths. v0.20.0 otherwise fed the
     local Windows `python.exe` through the remote-script fallback, recursively
     tokenized PE bytes, and crashed every full-path Python terminal command with
     `open: embedded null character in path`; that broke the 11:00 inbox sweep.
     Regression test:
     `TestLifecycleGuardModule::test_local_binary_does_not_fall_back_to_remote_reader`.
  *** PROMPTS THAT NEVER EXPIRE (2026-08-11) — re-apply after updates. ***
  File: `%LOCALAPPDATA%\hermes\hermes-agent\plugins\platforms\discord\adapter.py`,
  function `_read_discord_prompt_timeout`. Rachad: choice prompts died far faster
  on Discord than on Telegram while he was mid-task on another lane.
  THE ASYMMETRY, measured not assumed — ONE question, TWO independent clocks:
  (1) the GATEWAY clarify entry (`agent.clarify_timeout`, default 3600s) and
  (2) on Discord ONLY, a `discord.ui.View` timeout (`approvals.discord_prompt_timeout`,
  default 300s). Telegram's `send_clarify` builds a bare `InlineKeyboardMarkup`
  with NO client-side expiry at all, so its buttons live the full hour. Discord's
  fired `on_timeout` at 5 min, greyed the embed to "⏱ Prompt expired — no action
  taken" and disabled every button — while the gateway entry sat waiting another
  55 minutes. 12x shorter, same question. That gap is what he was hitting.
  THE PATCH: `<= 0` AND the word sentinels `never`/`unlimited`/`infinite`/
  `none`/`off` now return `None` (unlimited), and `<= 0` is checked BEFORE the
  min clamp. Without that ordering a configured `0` fell through
  `if seconds < MIN` and became a **30-second** prompt — worse than the default
  and the exact opposite of the ask. The numeric form mirrors
  `clarify_timeout`'s own "<= 0 = unlimited" convention so the two clocks cannot
  disagree. Positive values still clamp to [30, 900];
  `ModelPickerView`/`ChoicePickerView` keep their 120s (self-initiated pickers,
  deliberately untouched).
  *** WRITE THE WORD `never` IN CONFIG, NEVER A BARE 0. *** This function is
  re-read per view, but a RUNNING gateway still holds the previously-imported
  code, so a config edit lands before a patched function does. Under the
  pre-patch body `0` reads as 30 seconds — editing config to ask for "never"
  would have made prompts expire 10x FASTER until someone restarted. A word is
  unparseable to the old body, so it takes the malformed-value branch and keeps
  the unchanged 300s default: no regression window, restart whenever suits.
  Verified live at the time of the change: both running gateways resolved 300s
  while the patched code resolved unlimited.
  Regression tests (13 new, 19 in file):
  `tests/gateway/test_discord_prompt_timeout_config.py` —
  `::test_zero_or_negative_means_unlimited`,
  `::test_unlimited_is_checked_before_the_min_clamp`,
  `::test_word_sentinels_mean_unlimited`,
  `::test_yaml_bool_false_is_unlimited_but_true_is_not` (YAML 1.1 folds a bare
  `off` to False before we see it), and
  `::test_word_sentinel_is_safe_against_a_pre_patch_gateway`, which replays the
  old body inline so the no-regression-window property is executable, not a
  comment.
  THE 15-MINUTE COMMENT IN THE OLD CODE WAS WRONG and is now corrected in place:
  ~15 min is the lifetime of one interaction TOKEN, not a cap on how long a button
  on a normal bot message stays clickable — each click opens a NEW interaction with
  a fresh token. Verified against the vendored discord.py: `View._start_listening_
  from_store` only schedules the expiry task `if self.timeout`, and `ViewStore.
  add_view` holds the view in `_views[message_id]` with no TTL. So a `None` view is
  clickable for the life of the gateway process.
  CONFIG (both dado and aze profiles, 2026-08-11): `agent.clarify_timeout: 0`,
  `approvals.discord_prompt_timeout: never`, `approvals.timeout: 86400`.
  Each profile's own config.yaml is read (`get_config_path()` is HERMES_HOME-
  scoped), so the patch changes NOTHING for a profile that does not opt in.
  *** approvals.timeout MUST NOT BE 0. *** Unlike clarify, `approval.py` computes
  `_deadline = _now + max(timeout, 0)`, so 0 is an INSTANT fail-closed deny of
  every command, not an unlimited wait. 24h is the practical "never" there.
  ESCAPE HATCHES, because an unanswered prompt now pins its agent thread forever:
  `/new` (clear_session), `/stop` (approvals only — the wait loop polls
  `is_interrupted()`), a gateway restart, and for dado the 4am daily
  `session_reset`. Aze's `session_reset.mode` is `none`, so that profile has no
  automatic release. The gateway dispatches turns on a
  `ThreadPoolExecutor(max_workers=10)`, so ~10 forgotten prompts across all lanes
  would wedge it — this is the real cost of unlimited, and it is Rachad's call.
- Python: STILL no `py` launcher, and it cannot be installed — a Software
  Restriction Policy (HKLM\SOFTWARE\Policies\Microsoft\Windows\Safer) blocks
  running downloaded installers, so winget/python.org .exe fails with 1625.
  Do not retry that route; it is an org security setting, not a fault to fix.
  uv is the ONLY install path on this box (it extracts standalone builds
  instead of running an installer): `uv python install <ver>`, uv lives at
  %LOCALAPPDATA%\hermes\bin\uv.exe.
  Interpreters present (2026-07-24):
  - 3.11.15 hermes venv — THE interpreter for ALL Dado tooling; it is the
    only one holding the deps (google-auth, etc.). Always call it by FULL
    PATH "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe" in
    .bat files and cron (bare `python` does not resolve in cron env —
    learned 2026-07-23, see alert_ledger).
  - 3.13.14 general-purpose (added 2026-07-24 on Rachad's "install python"
    ask), reachable as `python3.13`; own pip, fully isolated from the venv.
  PATH RULE: the venv Scripts dir must stay FIRST in the USER PATH so bare
  `python` keeps meaning 3.11-with-deps. The uv shim dir
  C:\Users\TDI-service\.local\bin was deliberately APPENDED last, and holds
  only version-suffixed shims (python3.11/python3.13, no bare python.exe),
  so it cannot shadow the venv. Do not reorder.
- GitHub remote: https://github.com/rachad85/FRPDepot-Agent (private,
  wired 2026-07-23; nightly review pushes after its commit).
  Dado\Tools\vendor\ is NOT tracked (~150MB reinstallable binaries she
  bundled — cv2/onnxruntime/numpy/PIL for attachment reading; early
  history still carries one copy). On a fresh clone, rebuild with
  pip install --target Dado\Tools\vendor <packages>.

## Golden rules (mirror of Dado's SOUL — enforced in tools)
1. DRAFTS ONLY — no send capability anywhere, ever.
2. No keys/tokens/passwords in chat. Vaults + profile .env only.
3. Zoho: no company-wall filtering (Troy Dualam is a normal customer —
   Rachad removed those filters 2026-07-24). Writes still go only through
   the commissioned tools' stage-then-commit flow with Rachad's own
   approval phrase — that is HIS guardrail, not a wall to strip; leave it
   unless he says otherwise. See SOUL rule 3 for the exact flow.
4. NEVER ADD WALLS RACHAD DID NOT ASK FOR (his standing instruction,
   2026-07-24: "do not add any walls unless I specifically ask for it").
   He owns both companies and decides what is separate. OPEN as of
   2026-07-24: Drive (unfiltered), Zoho (no company-wall restriction),
   and TDI marketing analytics (read-only). STILL WALLED only because he
   has not opened them: C:\AgentTeam itself, and TDI mail (the Gmail TDI
   screen stays on). Raise a guardrail concern ONCE, then build what he
   asked for.
5. Honest errors: what failed, on what, the fix.

## State (2026-07-22, day 1)
- [x] Profile created (--no-skills, marker present), SOUL + config in.
- [x] Tree + repo initialized.
- [x] Telegram LIVE (verified: RH message answered in 17s). Token in
      local vault + profile .env; allowlist 891365639.
- [x] CONDUCT MONITORING armed (Rachad 2026-07-22: "quieter and easier
      than Aze"): ONE cron dado-conduct-review "10 5 * * *" no-agent →
      Dado\Tools\conduct\conduct_review.py (profile scripts copy runs).
      Collector folds the tripwire checks into the nightly bundle
      (gateway hard-stop guardrails cover live runaways). Headless
      Claude reviews AND may auto-fix small causes (never HARD RULES /
      .env / new capabilities; <~30 lines; every night git-committed).
      Deterministic guard reverts any HARD RULES edit. Telegram ping
      ONLY when Rachad is needed / guard trips / run fails — clean and
      auto-fixed-only nights are silent. First E2E run verified: clean,
      silent, auto-committed. BACKEND SESSION-START DUTY: read the
      newest file in Dado\30_Memory\conduct_reviews\.
- [x] INTER-COMPANY LINE to TDI/Aze LIVE (Rachad 2026-07-23): Dado may talk to
      Troy Dualam's Aze BOTH WAYS via the ONE sanctioned relay
      `python C:\Intercompany\intercompany_relay.py --to aze --message "..."`
      (returns Aze's reply on stdout; audit log C:\Intercompany\intercompany_log.jsonl).
      Hard Rule 4 amended + committed (ad3d6f0). DATA WALL UNCHANGED: message
      pass-through only, no TDI file/mailbox/Zoho reads, no FRP Depot financials/
      margins disclosed — arm's-length sibling company. See the intercompany-relay
      memory + Aze fingerprints_notes 2026-07-23. Relay config/keys are neutral
      (%LOCALAPPDATA%\Intercompany-Relay\config.json); re-run build_config.py after
      any gateway key rotation.
- [x] Outlook LIVE (verified 2026-07-22): device-code sign-in to the FRP
      DEPOT mailbox info@frpdepots.com; token cache
      %LOCALAPPDATA%\FRPDepot-Outlook\; scopes User.Read, Mail.ReadWrite,
      Calendars.Read — NEVER Mail.Send. Tools: Dado\Tools\outlook\
      outlook_tool.py (connect/check/unread/draft/reply-all, verified
      drafts) + outlook_check.py (READ-ONLY triage: who-spoke-last tags,
      [draft pending], --awaiting JSON, --thread dump, --sent, calendar).
- [x] INBOX WATCH cron LIVE (Rachad 2026-07-23, "same rules as Aze",
      chose 2h cadence): dado-inbox-watch "0 7,9,11,13,15,17,19 * * *"
      no-agent deliver-local → Dado\Tools\watch\dado_inbox_reasoner.py
      (profile scripts copy runs — keep both in sync). Wrapper owns
      delivery: [SILENT] contract, noise scrub, 3× Telegram retry +
      undelivered queue. Charter: deep-read before alerting, alert once,
      money-landing outranks quiet rules, uncertain = silent, DRAFTS ONLY
      unchanged. Alert ledger Dado\30_Memory\alert_ledger.md is BINDING
      (check before, append after, CLOSED = permanent silence). Cadence
      offset from Aze's :30 sweeps to spread the shared openai-codex quota.
- [x] Google LIVE (connected + verified 2026-07-24): personal Gmail
      read-only + drafts with its mailbox screen; Drive read-only and
      unrestricted with no company filtering.
      Rachad asked (2026-07-24) to copy Aze's live Google
      credentials over to Dado so "whoever is free" can handle his personal
      Google needs — declined. Two reasons: (1) Aze's own tool notes
      (HermesProfiles\aze\skills\tdi-operations\references\
      personal-google-business-account-prep.md) warn this personal account
      already mixes TDI banking/KYC threads with unrelated FRP material, so
      inheriting Aze's broad access would pull TDI content into FRP-side
      logs/nightly bundle/GitHub push — the mixing Hard Rule 4 exists to
      prevent; (2) a copied token stays bound to Aze's OAuth client
      regardless of which profile holds the file. Built instead: Dado's OWN
      independent OAuth client + OWN token vault
      (%LOCALAPPDATA%\FRPDepot-Google\token.json, never in the repo),
      scopes gmail.readonly + gmail.compose (drafts, no send path anywhere
      in the tree — Golden Rule 1) + drive.readonly (no edit — same
      commissioning rule as Zoho, Golden Rule 3). Gmail results are screened
      by Dado\Tools\google\tdi_filter.py: flagged search hits are withheld and
      counted, and a flagged direct --id read is refused. Drive has no such
      screen. Code: Dado\Tools\google\{google_auth,
      google_tool,tdi_filter}.py, CONNECT_DADO_GOOGLE.bat/
      CHECK_DADO_GOOGLE.bat at repo root. OAuth project dado-frpd, app
      Dado_FRPD, publishing status stays TESTING (never PUBLISH — that
      submits a private one-user app for Google review); Rachad must stay on
      the Test users list or sign-in dies with 403 access_denied (hit
      2026-07-24, troubleshooting section is in the setup guide). Google
      expires the sign-in every 7 days: re-run CONNECT_DADO_GOOGLE.bat.
      Verified live 2026-07-24: granted scopes are exactly gmail.readonly +
      gmail.compose + drive.readonly (NO send scope, NO Drive write), token
      sits outside the repo, draft create + read-back works and sends nothing.
      TWO REAL GMAIL FILTER BUGS FOUND AND FIXED on that first live run — do not
      regress either:
      (a) SCREEN WHAT YOU RETURN, not what you happen to check. The first
          cut screened Subject+From only, but a Gmail `q=` search matches
          BODY text, and gmail-read returned the full body after checking
          only the snippet. Live result: 10/10 "Troy Dualam" hits reached
          Dado, 0 withheld. Both Gmail paths now fetch format=full and screen
          every header value + snippet + full body (_message_is_tdi). Drive is
          deliberately unrestricted and does not use this screen.
      (b) The term was the two-word phrase "troy dualam", which missed the
          no-space company DOMAIN troydualam.com — mail from TDI's own
          domain was passing. TDI_TERMS is now ["dualam","tdi"]; "dualam"
          alone is load-bearing, do NOT narrow it back. Bare "troy" is
          deliberately excluded (common name/city, would over-block).
      After the fix: "TDI" 10/10 withheld; "Troy Dualam" 5 withheld with the
      5 survivors carrying no TDI marker; ordinary mail unaffected
      ("invoice": 10 results, 0 withheld).
      BULK REFERENCE INDEX (google_indexer.py, built 2026-07-24): caches Gmail
      + Drive into %LOCALAPPDATA%\FRPDepot-Google\reference\ (outside the repo;
      google_client.json is gitignored). Its first unattended run was the
      3-hour stall — four defects fixed, all measured: (1) every insert ran
      "DELETE FROM <fts> WHERE id=?" on an UNINDEXED FTS5 column = full scan of
      the whole index, and since only new ids are processed it deleted nothing;
      benchmark at 12k rows showed 226x slowdown, growing — that was the
      255->73 items/min collapse; now skipped for known-new ids. (2) folders
      were 73% of the corpus (72,943 of 114,000+ items) and 87% of stored rows;
      the listing now excludes them. (3) no ceiling — now --max-minutes
      (default 45) and resumable, since already-indexed ids are skipped.
      (4) a receipt per 20 items wrote 3,768 lines in a day, burying real
      business receipts; now one per run.
      *** DRIVE CACHE UNRESTRICTED (2026-07-25). *** Rachad explicitly ordered
      no Drive restrictions. google_reference.py no longer filters Drive;
      google_indexer.py and google_backfill.py do not screen or quarantine
      Drive content; google_rescreen.py --apply now affects Gmail only.
      --release-drive cleared 428 stored Drive flags, deleted 3,677 legacy
      Drive withheld hashes and requeued OCR files whose prior text had been
      discarded. Do not reintroduce a Drive company wall without Rachad asking.
      HISTORICAL BACKGROUND ONLY — this does not authorize Drive screening:
      WHAT HAPPENED: the screen only knows "dualam" and "tdi". Bare "troy" is
      deliberately excluded (common name/city), so TDI material that spells out
      neither term passed through. Verified in the live index: Aze's own
      aze_active_task.json stored WITH CONTENT, 187 Drive rows + 240 Gmail
      bodies mentioning "aze", 1,168 Gmail bodies mentioning "troy", 4 rows
      naming TDI's troy_history DB, 120 Drive filenames carrying TDI's Q26-
      quote numbering. Separately 2,610 Drive rows were stored after only their
      FILENAME was screened: unreadable types, oversize files and extraction
      errors return empty content, so the `if content:` screen in upsert_drive
      is skipped and the row is written anyway — the exact inverse of this
      tree's own rule, "SCREEN WHAT YOU RETURN, not what you happen to check."
      METHOD WARNING — do not repeat this mistake: an initial check searched
      stored rows for "dualam"/"tdi", found zero, and was reported as clean.
      That is CIRCULAR — those are the terms the filter already removes, so the
      query can only ever return zero. It proves the filter ran, NOT that TDI
      content is absent. Probe for markers the filter does NOT screen.
      MARKER DESIGN — measured, not guessed (see tdi_filter.DEEP_MARKERS):
      dualam / dumalac / tdi / troy_history / AgentTeam / aze_*.json /
      Q26-####. Bare "troy" is DELIBERATELY EXCLUDED: 368 of its hits are
      parcel deliveries to a person named Troy and only 5 are the company, so
      it would wall off Rachad's own mail. Boundaries use an alphanumeric
      lookaround, NOT \b — underscore is a word character, so \bq26 missed the
      real file "RE_ RFQ ... ______Q26-1526.msg". "tdi" is boundary-anchored
      because as a raw substring it fired on Turkish mail ("yurtdisi").
      NOT PATCHABLE BY RE-RUNNING THE INDEXER: both loops skip ids already
      stored, so a widened list can never revisit them — that is exactly why
      google_rescreen.py walks the stored rows directly.
      Drive rows whose formats cannot be read remain metadata-only. That is an
      extraction limit, not a company restriction; inspect the original file
      when content is required.
- [x] FOLLOW-UP TRACKER LIVE (2026-07-24, Rachad's choices): Dado's watch used
      to be ONE-DIRECTIONAL — [awaits YOU] only, with a thread he spoke last in
      treated as "handled, never surface", so a quote or RFQ HE sent could go
      silent forever. Measured at build time: 15 overdue, incl. "Quote
      QT-000023 awaiting your approval" at 42 working days and CAD 4,101.30
      outstanding at 9. Now: outlook_check.py --waiting-on-them (READ-ONLY;
      classifies rfq_quote/payment/general, counts WORKING days, thresholds
      5/7/3, and never re-offers a thread that already has a chase draft) feeds
      dado-followup-digest "0 8 * * 1-5" (morning digest, prepares a reply-all
      chase DRAFT per item — DRAFTS ONLY unchanged) plus inbox-watch charter
      rule 3b, which raises ONLY overdue+urgent (money/RFQ) items the same
      sweep. Everything else waits for the digest so it never becomes a stream.
- [x] DRIVE BACKFILL / OCR (2026-07-24): metadata-only rows are read — images
      via rapidocr_onnxruntime (bundled in
      vendor; ONNX, no installer, which matters under the SRP), .msg via
      extract-msg, .eml via stdlib, scanned PDFs via PyMuPDF render + OCR,
      legacy .xls via xlrd, zips by member name. Tool:
      Dado\Tools\google\google_backfill.py, run through job_runner. Drive text
      is indexed without company filtering. NOTE vendor is
      APPENDED to sys.path, never inserted first — it carries its own
      cryptography copy and the venv pins 46.0.7 for hermes.
- [x] LONG-JOB DISCIPLINE (2026-07-24, after the 3-hour stall): Dado must
      NEVER wait on a job inside her turn. Anything over ~2 min goes through
      `Dado\Tools\watch\job_runner.py start --name X -- <cmd>` (returns in
      0.2s, runs fully detached, supervisor records the exit code); she then
      tells Rachad it started and ENDS HER TURN. Two no-agent crons carry it:
      dado-job-watch "*/10 * * * *" announces done/failed/died/stalled ONCE
      per job, and dado-stall-tripwire "*/15 * * * *" catches a turn that is
      OPEN RIGHT NOW past 20 min with no word to Rachad (plus the blocking-poll
      signature). SOUL "## LONG JOBS" rewritten to match.
      WHY THE OLD DEFENCES MISSED IT — do not rely on them alone: the SOUL
      "ping every 10 min" rule needs the model to choose to speak and a
      blocking 600s call gives it no chance (it had already failed the same
      way on 2026-07-22); config tool_loop_guardrails count FAILING calls, so
      a poll that succeeds and says "still running" never moves the counter;
      and Aze's behavior_tripwires reads gateway.log "response ready", which is
      written only AFTER a turn ends, so an in-flight stall is invisible to it.
      Incident shape for reference: 17 consecutive 600s polls, 0 messages in 3h,
      turn died at max_iterations_reached(60/60).
- [x] TDI ANALYTICS OPENED TO DADO, READ-ONLY (Rachad 2026-07-24) — a
      deliberate, separately-taken decision, NOT a fix for the conversion
      question. THE FACT THAT PROMPTED IT, keep it on record: Dado reported
      she could not give FRP Depot conversion rates and blamed the company
      wall. Wrong diagnosis. At that 2026-07-24 check, frpdepots.com had no
      accessible GA4 property; the only two visible properties were Troy Dualam's
      (accounts/320963476 "Troy Dualam" -> properties/449339383;
      accounts/333650696 "Troy Dualam Services" -> properties/463861653).
      So no permission change could produce an FRP conversion number at that
      time. FRP DID own Search Console (sc-domain:frpdepots.com, siteOwner),
      while click->lead conversion was unavailable. Shown that, Rachad chose
      to grant the TDI analytics read anyway so Dado can work TDI marketing alongside Aze.
      SCOPE AND LIMITS: marketing METRICS only, read-only, via
      Dado\Tools\google\analytics_tool.py (list/report/compare; Admin-API
      GETs + Data-API runReport only, no administration, no writes). TDI
      mailbox remains separate; Drive and Zoho are unrestricted. Gmail keeps
      its mailbox screen. analytics_tool.py deliberately does NOT import it.
      CONTAINMENT: --save writes only to
      %LOCALAPPDATA%\FRPDepot-Google\analytics_reports\ and the tool REFUSES
      to write inside C:\FRPDepot, so TDI figures cannot enter FRP's git
      history or the nightly conduct bundle. Hard Rule 4 amended in
      DadoProfile\SOUL.md and synced to the live profile.
      NOTE FOR WHOEVER COMMITS: this HARD RULES edit should be committed —
      while uncommitted, a tripped conduct guard runs
      `git checkout -- DadoProfile/SOUL.md` and would silently wipe it.
- [x] FRP Depot GA4 LIVE (verified 2026-07-27): account 2499934 "Northnet
      Media" exposes property 529941333 "FRP Depots" to Dado's read-only
      Analytics token. The Data API returns historical site and ecommerce
      activity. This supersedes the 2026-07-24 no-property finding above.
      Search Console's prior audit showed the real bottleneck was RANKING:
      28d to 2026-07-21 gave
      7,036 impressions / 56 clicks / 0.80% CTR at average position 22.4
      (page 3), impressions +46.5% and clicks +43.6% vs the prior 28d — so
      CTR is flat, not collapsing. 99.2% of impressions come from
      zero-click queries sitting at positions 10-47 ("frp pipe" 365 imp at
      pos 12.8, "custom frp solutions", "frp pipe elbow").
- [x] Zoho Books + Inventory LIVE: read access verified. Named writes remain
      restricted to the commissioned stage-then-commit tools in SOUL rule 3.
      2026-08-07: `zoho_inventory_classification_tool.py` commissioned for the
      one fixed `Catalog Classification` item dropdown and assignment of only
      its three fixed values. Field creation uses the exact live UI request shape;
      assignments use the verified item custom-field serializer. The independent
      safety suite passes 25/25, including refusal before network without Rachad's
      one-word `APPROVED`. The first creation plan was staged locally; no Zoho
      write occurred.
      2026-08-08: `zoho_inventory_item_tool.py` received one fixed additional
      operation for FRP FW PIPE group `96274000000034779`: rename SIZE option
      `96274000000034781` from `30` to `30\"`, preserving every ID and every
      other group/item field. The implementation is hard-coded to the two linked
      30-inch items, full-state fingerprinted, replay-locked before its one PUT,
      and independently tested with all 120 Zoho tests passing. Rachad approved
      plan `20260808T164957Z_group_option_rename_9d6ba1d7.json` on 2026-08-08.
      Zoho rejected the single PUT with HTTP 400, code 15: `Please ensure that
      the "attributes" has less than 100 characters.` A separate live GET then
      proved the complete group state unchanged and the option still `30`. The
      plan remains permanently replay-locked; no retry and no dependent
      WooCommerce Pipe plan were made.
      2026-08-10: `zoho_inventory_price_tool.py` commissioned for EXISTING-item
      sales rates on the FNPT D411/D470 couplings only. It writes exactly one
      field, `rate`, on items whose live SKU begins with
      `FNPTCOUPLING-DERAKANE411-` or `FNPTCOUPLING-DERAKANE470-`, at exactly
      supplier USD cost x 3.6 (Decimal ROUND_HALF_UP, two decimals, CAD). Cost/
      purchase rate, stock, name, SKU, status, creation, deletion and every batch
      route are unreachable. Its approval word is byte-exact `APPROVED` — no
      `.strip()`, no case fold, deliberately stricter than its siblings. The PUT
      payload is exactly `{name (preserved unchanged), rate}`; `name` is the one
      preserved identifying field Zoho requires on an item PUT, proven by the
      classification tool's 206 verified live item PUTs, and the read-back proves
      it did not move. Protected fingerprint = every returned item field except
      the rate family (rate/sales_rate/pricebook_rate/default_price_brackets/
      sales_margin, which Zoho recomputes from rate and which are each verified by
      explicit rule) and `last_modified_time`. BATCHES ARE NOT ATOMIC: one PUT per
      line, no retry, and any failure or indeterminate result locks the whole plan.
      Tests: 55 new + 175 across the whole Zoho suite, all passing; WooCommerce
      suite 561 passed / 1 skipped. A 26-item plan
      (`20260810T051851Z_fnpt_sales_rate_update_2fe8ea90ff05f294.json`, 20 increases,
      6 decreases) was STAGED ONLY on 2026-08-10 — zero Zoho writes, zero Woo
      writes, no commit lock — and awaits Rachad's own `APPROVED`. Six existing
      8-inch D411/D470 variations are excluded because their supplier cells are
      blank; no cost was inferred.
      2026-08-10: `zoho_invoice_revision_tool.py` commissioned to revise ONE
      EXISTING Books invoice with ONE atomic `PUT /books/v3/invoices/{id}`. Only
      `customer_id` (to an ALREADY EXISTING customer — creation stays solely in
      `zoho_customer_quote_tool.py`), `reference_number`, `date`, `due_date`,
      `billing_address_id`/`shipping_address_id` owned by that live customer,
      `notes`, `terms`, and per EXISTING line `quantity`, `rate`, `discount`,
      `description`, `tax_id` may change. NO OMISSION-BASED DELETION: every live
      line is always resent once, in order, with its own line_item_id and item_id;
      adding, removing or substituting a line is refused by the schema, the plan
      validator, the write allowlist AND the read-back. Invoice number, status,
      currency, exchange rate, balance/payments/write-offs, adjustments, shipping
      charges and custom fields are unreachable, as are create, delete, void,
      mark-draft, mark-sent, submit, approve, reject, mail, reminder, payment,
      credit-note, attachment, template and every bulk route. IT HAS NO MAIL
      TRANSPORT AT ALL — a test asserts the source contains no mail/status/
      lifecycle route and exactly one `urlopen` call site. It refuses any invoice
      that is not exactly `draft` or `sent`, or that carries a payment, credit,
      write-off, package, shipment or recurring profile, and it refuses line-value
      changes on a sales-order-linked invoice (they would desync fulfilment).
      Totals are predicted only where Zoho's result is deterministic and the plan
      says so; the byte-exact protected fingerprint exempts a key ONLY when the
      plan genuinely moves it, so a PO-only revision keeps every line, total and
      the customer inside the fingerprint. Approval word is byte-exact `APPROVED`.
      ONE attempt: any failure, timeout or indeterminate result permanently locks
      the plan. Scope `ZohoBooks.invoices.UPDATE` was added to the PREPARED list
      only — NOT YET LIVE: Rachad must run PREPARE_DADO_ZOHO_ACCESS.bat, create
      the grant, then REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat. There is
      deliberately no invoice CREATE/DELETE/ALL/fullaccess scope. Tests: 76 new,
      251 across the whole Zoho suite, all passing; WooCommerce 561 passed /
      1 skipped. Six deliberate mutations of the tool (weakened approval, dropped
      line, unpreserved invoice number, non-exclusive lock, skipped fingerprint
      check, unrestricted status) were each caught by the suite. BUILD AND TESTS
      ONLY — the live vault was not touched, no plan was staged, and no invoice
      was changed. Motivating case, NOT approval: INV-000051 (96274000001559012,
      sent) where Ralmax asked for SHM PO 0000031 and billing to SHM Marine
      Constructors JV — SHM does not exist as a contact yet, and the tax treatment
      is unresolved, so nothing was staged.
      2026-08-10 (follow-on; Rachad answered "Yes" to adding creation): the SAME
      named tool gained a SECOND and final plan action, `create_draft_invoice` —
      one new invoice per plan, one `POST /books/v3/invoices`, then a fresh live
      read that must show status exactly `draft`. Stage with `stage-create`;
      `commit` dispatches on the plan's action. ZOHO'S OWN AUTO-NUMBERING assigns
      the number: `invoice_number` is absent from the POST allowlist and the
      string `ignore_auto_number_generation` does not appear anywhere in the
      module (a test asserts it). It requires an EXISTING ACTIVE customer whose
      live name matches the stated one, addresses owned by that customer, and
      EXISTING ACTIVE Zoho items on every line — no free-text or unlinked lines,
      and no customer/item/tax/settings write of any kind. Every stated value
      (quantity, rate, discount, description, tax ID) needs its own `source`
      string; both dates must be stated so nothing is inferred. The customer's
      currency is preserved and neither currency nor exchange rate is in the
      payload allowlist. A repeated item line is refused unless each such line
      carries its own distinct description. An independent Decimal half-up
      calculation of line totals, discount, tax and grand total is shown before
      approval and asserted on read-back WHERE DETERMINISTIC — a tax group or
      compound tax is labelled ESTIMATE and deliberately not asserted, because
      Zoho rounds each component separately (this matters: Troy Dualam Services
      bills on the GST+QST group). Price precision comes from the live
      organization record; when absent it defaults to 2 and totals are claimed
      exact only if no line needed rounding at all. Read-back also verifies
      `is_emailed` is false and that no shipping charge or adjustment appeared.
      If the POST succeeds but the read-back is missing or not draft, the tool
      reports indeterminate WITH the invoice ID when known and NEVER cleans up,
      deletes, voids, changes status or retries. Both transports now funnel
      through ONE `urlopen` call site (`_perform`), so the source still holds
      exactly one network call, one `method="PUT"` and one `method="POST"`.
      Scope `ZohoBooks.invoices.CREATE` was added to the PREPARED list beside
      `.UPDATE` — NOT YET LIVE, same reauthorization steps. There is still no
      invoice DELETE/ALL/fullaccess scope. Tests: 67 new
      (`test_zoho_invoice_draft_creation.py`), 318 across the whole Zoho suite,
      all passing; WooCommerce 561 passed / 1 skipped. Two pre-existing tests were
      EXTENDED, not weakened, because this commission superseded them: the
      "no invoices.CREATE scope" assertions (that scope is now commissioned), and
      "exactly one write verb" (now one PUT plus one POST, still one urlopen).
      BUILD AND TESTS ONLY — the live vault was not touched, no plan was staged,
      no invoice was created and no email was sent.
      2026-08-10: `zoho_customer_quote_tool.py` gained the ONE narrow ESTIMATE
      UPDATE Rachad commissioned after two approved create-only draft estimates
      landed high. THE DEFECT, and it is a Zoho contract fact worth keeping: Zoho
      Books reads a NUMERIC line discount as a FLAT CAD AMOUNT and only a STRING
      containing `%` as a percentage. Staging `10` for "10%" therefore took CAD
      10.00 off each line: QT-000029 (96274000001559037) landed at CAD 15,073.96
      instead of 13,680.38 and QT-000030 (96274000001558043) at 6,507.31 instead
      of 5,929.02. Two fixes, one commission.
      (a) CREATE PATH: percentages are now the exact string (`TDS_LINE_DISCOUNT`
      = "10%"). A nonzero NUMERIC line or entity discount is refused at staging,
      and `refuse_numeric_percentage_discounts` refuses a legacy plan at commit
      BEFORE the vault, the token refresh and the network — so neither consumed
      create plan can be replayed. Flat-amount line discounts are consequently
      unreachable through this tool; write the percentage instead.
      (b) CORRECTION PATH: `stage-tds-discount-correction --estimate-id` and
      `commit-tds-discount-correction --plan --approval`. Exactly two estimate IDs
      are reachable (`CORRECTION_TARGETS`); every other ID is refused before any
      network call. Stage is GET-only: it re-reads the live estimate, requires the
      exact number, reference, customer, `draft` status, exact line count/IDs/
      order, `discount == 10` and `discount_amount == 10` on every line and the
      exact diagnosed wrong totals, then cross-checks each line against the
      IMMUTABLE original create plan (hash-verified against a constant) and the
      live diagnosis artifact, and RECOMPUTES every corrected figure from the live
      quantities and rates before requiring it to equal the approved totals
      artifact AND the fixed approved totals. Nothing is copied on trust.
      TAX BASIS, measured not assumed: Zoho computes the Quebec GST+QST on the NET
      SUBTOTAL, not per line. On the live records 13,110.64 -> 1,963.32 and
      5,659.76 -> 847.55 reproduce exactly on the subtotal basis (both as the
      combined 14.975% and as 5% + 9.975%), while the per-line sum gives 1,963.33
      — one cent out. That is why the corrected tax can be predicted at all, and
      the tool refuses to stage if that reproduction ever fails.
      The PUT resends the COMPLETE live line list with every `line_item_id` and
      `item_id` (Zoho deletes lines a PUT omits) plus the preserved customer,
      number, reference, date, notes and terms; only the discount changes. The
      payload allowlist has no status, currency, tax, adjustment, shipping,
      template, custom-field or mail key, and `oauth_estimate_discount_write_allowed`
      accepts only PUT to the two fixed IDs — the module holds exactly one
      `method="PUT"`, one `method="POST"` (the existing create) and two `urlopen`
      call sites, with no DELETE/PATCH/send/status/approve route anywhere.
      Commit: byte-exact approval `APPROVED`, plan hash + 24h expiry + full
      re-derivation of the plan from its own staged live state, `estimates.UPDATE`
      required in the saved connection, fresh GET fingerprint comparison, durable
      single-use lock BEFORE the PUT, one attempt, then verification of BOTH the
      PUT response and a fresh GET (identity, draft status, every line ID/order,
      each line's expected discount_amount and item_total, the exact totals, and a
      byte-exact protected fingerprint of every non-derived field). Any mismatch,
      failure, timeout or indeterminate result leaves the plan permanently locked;
      there is no retry and no rollback.
      Scope `ZohoBooks.estimates.UPDATE` was added to the PREPARED list ONLY —
      NOT YET LIVE: Rachad must run PREPARE_DADO_ZOHO_ACCESS.bat, create the
      grant, then REAUTHORIZE_DADO_ZOHO.bat and CHECK_DADO_ZOHO.bat. No estimate
      DELETE/ALL/fullaccess scope exists.
      Tests: `test_zoho_quote_discount_correction.py` (new) plus the updated
      `test_zoho_tool.py`. THEY WERE NOT EXECUTED IN THE BUILD SESSION — that
      session's permission layer refused every `python` invocation, so the counts
      are unverified; run
      `python -m unittest discover -s C:\FRPDepot\Dado\Tools\zoho -p "test_zoho*.py"`
      before trusting them. BUILD ONLY — no plan was staged live, ZERO Zoho
      writes, ZERO estimates changed, ZERO emails.
      2026-08-10: `zoho_email_template_tool.py` commissioned after live testing
      proved Zoho Books ANDROID exposes neither Android contacts nor SwiftKey
      clipboard clips in its CC picker. The ONLY thing it may ever create is one
      of exactly FOUR fixed organization-wide INVOICE email templates, each a
      clone of the single live `Default` invoice template (ID
      `96274000000000014`) with exactly one changed name and one fixed CC list:
      `CC - Accounting`/`CC - Logistics`/`CC - Operations` (one address each) and
      `CC - All` (logistics@, accounting@, operations@ IN THAT ORDER). Creation
      is TWO-PHASE — the first plan may create `CC - Accounting` ALONE so Rachad
      can prove on his phone that a non-default template is selectable and its CC
      populates; the other three need his own recorded Android-test confirmation,
      and the tool refuses a confirmation whose statement/source reads as
      commissioning language. No other module, name, address, subset or source is
      reachable; there is no update/delete/rename/set-default/associate/
      attachment/PDF/sender/DKIM/relay/workflow route, no mail transport, and no
      write verb at all — a test asserts the source contains no `"POST"`/`"PUT"`/
      `"DELETE"`, no `urlopen`, no cookie/storage access and exactly one
      `method: "GET"` call site.
      Read-only discovery established the real surface: the settings route is
      `#/settings/emails/templates?email_type=invoice_notification` and the two
      readable endpoints are `GET /api/v3/settings/emailtemplates` and
      `GET /api/v3/settings/emailtemplates/{id}` (both verified live). Zoho
      publishes NO documented Books API for CREATING an email template, so the
      only safe mechanism is Zoho's own native Save path, exactly as
      `zoho_inventory_classification_tool.py` creates the item custom field.
      *** THAT CONTRACT IS NOW CAPTURED (2026-08-10, Rachad authorized it). ***
      The fixed `New` form was opened under an abort-everything interceptor,
      only the fixed name and the fixed Cc dropdown option were filled, and Save
      emitted exactly ONE request — `POST` to
      `https://books.zohocloud.ca/api/v3/settings/emailtemplates`, empty query,
      form body `JSONString=` + `organization_id`, body SHA-256
      `850b177880f00f693bca3ee367a8f548b06bf252e9db873c32a591233c29b7ad` —
      aborted before the network. Artifact:
      `Dado\20_Working\zoho_email_template_capture\native_save_request.json`.
      The payload schema is closed and now enforced in code: 7 top-level keys,
      exactly one `language_content` block (`subject`/`body`/`language_code=en`/
      `is_default=true`). NO header, cookie, storage value, CSRF token or
      password was read. Cc entry works ONLY by opening that row's own
      `.zf-ac-toggler` and clicking an exact `role=option` value; typing an
      address, Enter and comma all failed live and are NOT fallbacks to retry.
      *** COMMIT IS STILL NOT EXECUTABLE — THE BLOCKER MOVED FROM CONTRACT TO
      CONTENT. *** Decoding that captured body proved Zoho's `New` form does NOT
      clone this organization's `Default`: it loads Zoho's stock factory invoice
      body. Stock reads BALANCE DUE / %Balance% / MAKE PAYMENT / Regards
      %UserName% %CompanyName%; the live `Default` reads INVOICE AMOUNT /
      %Total% / PAY NOW / Regards Accounting Departement. Subject and From DO
      match; the bodies differ (2118 vs 2131 chars). Since the commission
      requires every target to preserve `Default`'s body, creating through `New`
      would either break that guarantee or succeed at the POST and then fail this
      tool's own clone-fidelity read-back — leaving an ORPHAN template behind a
      permanently locked plan. `require_native_form_clones_source` therefore
      refuses BEFORE the replay lock, comparing the live Default's body against
      what the form actually emitted. Nothing was invented and nothing created.
      NEXT STEP IS RACHAD'S CALL, two options: (1) a native `Clone` control DOES
      exist on the `Default` row — its menu holds exactly `Edit` and `Clone`,
      verified read-only with one click on the disclosure toggle and zero write
      requests — so authorize a blocked capture of the Clone form's single Save
      request the way the New form's was captured; or (2) he says plainly that
      the stock body is acceptable, which changes what customers receive and is
      his decision, not Dado's.
      Tool is now v1.1.0 / schema 2, so plans staged under the old blocker fail
      closed. Tests: 103 in the email-template suite, 421 across the whole Zoho
      suite, all passing. NO NEW SCOPE was needed — the tool reads through
      the existing authenticated UI session and existing read-only OAuth GETs.
      One read-only plan
      (`20260810T231950Z_create_accounting_test_2fa2a591d4936ffe.json`) is STAGED;
      approving it refuses before any write until the blocker above is resolved. The encrypted vault and profile .env
      were not touched, ZERO Zoho writes were made, ZERO templates were created
      and ZERO emails were sent.
      2026-08-11: two defects fixed in `zoho_banking_reconciliation_tool.py`, both
      found while checking an unrelated tripwire alert. NOT a new commission — no
      capability added, no guard relaxed, transport surface unchanged (still exactly
      2 `urlopen` call sites, one POST, one PUT, no DELETE/PATCH; the new read goes
      through the existing `zoho_tool.api_get`).
      *** THE ZOHO CONTRACT FACT, measured live and worth keeping: A CATEGORIZE
      CONSUMES THE IMPORTED FEED LINE AND CREATES A DIFFERENT RECORD. *** On
      2026-08-11 line `96274000001423074` became transfer `96274000001558075` and
      the old ID stopped resolving entirely.
      (a) VERIFICATION COULD NEVER CONFIRM SUCCESS. `AFTER_SOURCE_MODES["categorize"]`
      read back the PRE-WRITE source ID; `get_bank_transaction` 404s on it, falls back
      to the uncategorized feed, finds zero rows, throws — and since `write_attempted`
      is already true the commit recorded `indeterminate` and permanently locked a
      write that HAD LANDED. A successful categorize could not reach
      `committed_verified`. Now `verify_categorize_result` takes the resulting ID from
      the POST response (`categorized_result_id`, which returns "" rather than guess
      when the response carries no single ID) and verifies THAT record via
      `get_categorized_result` — a direct GET, deliberately NOT `get_bank_transaction`,
      whose feed fallback would both re-read the state a success removes and drag in
      the authenticated UI session. Checked: type equals the approved
      `transaction_type`; amount/date/currency equal the source before-state; both
      approved account IDs present; `reference_number`/`description` preserved where
      staged. The lock now carries `resulting_transaction_id`, and a FAILED commit
      that Zoho nonetheless accepted records `zoho_accepted_the_write` plus that ID —
      an indeterminate Zoho ACCEPTED needs a different reconcile from one it rejected.
      (b) STAGING NEVER CHECKED THE ACCOUNTS WERE ACTIVE, though the account GET
      already answers it: the 09:50 plan stored `is_active: False` IN ITS OWN SNAPSHOT,
      staged anyway, and Zoho rejected it 400/11015 "Inactive or deleted accounts".
      Nothing reached the books, but it burned a single-use plan and lock.
      `require_categorize_accounts_active` now refuses both accounts at staging, and
      the Airwallex recovery fixed-fact list gained `new source account status` — it
      had always asserted the DESTINATION was active and never the source.
      WHY IT SHIPPED: `recovery_accounts()` pinned the source account `"status":
      "inactive"` and every recovery test passed with it — the live broken state
      captured as a fixture — and there was no categorize commit-success test at all.
      Both corrected; the fixture is `active`, matching live after Rachad reactivated
      the account between the two attempts.
      Tests: 43 in the banking suite (3 new), 587 across the whole Zoho suite, all
      passing; WooCommerce 618 passed / 1 skipped. Both fixes were mutation-checked —
      removing either makes the suite fail (3 and 8 failures respectively).
      NO live Zoho call was made from this session: BUILD AND TESTS ONLY, zero writes,
      zero plans staged, zero emails. The two locked plans from 2026-08-11 stay locked
      and MUST NOT be retried — the transfer already exists.
- [x] WOOCOMMERCE IMAGE ALT SUPPORT (2026-08-08): Rachad commissioned a narrow
      existing-product image-alt-only extension to `woocommerce_change_tool.py`.
      Every plan must carry the complete gallery with unchanged IDs and order;
      image entries contain only `id` and nonblank plain-text `alt`. Creation,
      removal, replacement, reordering, URLs/files/metadata, product creation and
      variation images are refused. Pre-write full-product fingerprinting,
      one-attempt commit locking and complete-gallery readback remain mandatory.
      Full WooCommerce discovery suite passes 48/48.
- [x] WOOCOMMERCE FREIGHT POLICY + WORDPRESS DEPLOYMENT (2026-08-09): Rachad
      commissioned `woocommerce_shipping_policy_tool.py` for the one fixed class
      `Freight Quote Required` / `freight-quote-required` and explicit existing
      product/variation assignment/removal only. Checkout-guard 1.0.0 was
      manually activated on production after approval; it blocked checkout but
      showed only WooCommerce's generic error, so Rachad deactivated it. Fresh
      readback proved the storefront recovered. That plan and ZIP hash
      `4d8396d95baf0907754730e578ad4c41b98908f77992718c41b293434e07fe25`
      are permanently closed. Corrected version 1.0.1 has reproducible ZIP hash
      `fe6fa440ea3a08169bf568ae0fbb06f666ad71c1110e58f9b2b6bb0acc8be6cb`.
      Rachad then commissioned `wordpress_plugin_deployment_tool.py` so Dado can
      replace, activate or deactivate only this fixed plugin through the
      dedicated authenticated WordPress UI session. No deletion, foreign plugin,
      setting/content/user change or generic browser action is reachable. Every
      write uses a 24-hour immutable plan, exact uppercase `APPROVED`, pre-write
      fingerprint, lock-before-side-effect and post-write readback. Activation
      performs an anonymous checkout validation and automatically deactivates on
      any failure. Full WooCommerce suite: 248 run, 247 passed, one PHP-only skip, zero
 failures. The approved replacement plan positively identified the fixed
 WordPress plugin and uploaded version 1.0.1; fresh readback confirmed 1.0.1
 installed inactive and the plan replay-locked. A first unapproved activation
 plan was abandoned before any write after live evidence proved its `1/2 inch`
 selector label was wrong. The corrected `1/2"` plan was staged only after a
 fresh 248-test pass; activation still awaits its own exact `APPROVED`.
- [x] DISCORD LANE — SECOND CHAT SURFACE, SAME DADO (2026-08-10, Rachad asked for
      "a new lane for Dado on Discord, completely independent so I can run 2 tasks
      in parallel without interference"; he chose same-profile over a second agent).
      IT IS GENUINELY PARALLEL, verified in hermes code, not assumed: every
      mutual-exclusion primitive in the dispatch path is keyed by SESSION KEY or
      resolved session_id, never by profile, and the platform value is a mandatory
      slot in the key (gateway/session.py:1096-1115). So a Telegram DM and a Discord
      DM are two keys -> two asyncio tasks (platforms/base.py:5393) -> two threads of
      a ThreadPoolExecutor(max_workers=10) (run.py:21153). `max_concurrent_sessions`
      is null = unlimited for her. Two turns is nowhere near the ceiling.
      ZERO INSTALL: discord.py 2.7.1 + PyNaCl are already in the hermes venv, the
      bundled plugin adapter auto-registers, and Discord binds NO port (it dials out
      over WebSocket) — 8647 is API_SERVER_PORT, unrelated. Enablement is env-var
      presence only (gateway/config.py:1867-1871); no config.yaml block is required.
      *** SHE ONCE RAN AS TDI'S BOT — THIS IS WHY THE SETUP REFUSES SHARED TOKENS. ***
      On 2026-08-04 her own gateway (profile dado, api_server 8647) connected to
      Discord as **Aze#1753**, lost a token race to Aze's gateway ("Discord bot token
      already in use (PID 17152)"), won it on retry 3, and served Rachad's DMs into
      session agent:main:discord:dm:1530608805388353686. Cause: her .env had NO
      DISCORD_BOT_TOKEN, and hermes DELIBERATELY does not scrub credential keys from
      os.environ (hermes_cli/env_loader.py). The fix is that her .env now sets it
      EXPLICITLY — profile .env loads with override=True (env_loader.py:488), so hers
      beats anything inherited. SET_DADO_DISCORD_TOKEN.bat additionally REFUSES a
      token whose sha256[:16] matches a gateway lock owned by another profile
      (gateway/status.py:279-284, lock dir ~\.local\state\hermes\gateway-locks) —
      a hash match is a positive identification of a shared bot, not a guess.
      Files: SET_DADO_DISCORD_TOKEN.bat + Dado\Tools\setup\SET_DADO_DISCORD_TOKEN.ps1,
      CHECK_DADO_DISCORD.bat + Dado\Tools\discord\dado_discord_check.py (asks Discord
      GET /users/@me which bot she actually is), OPEN_DADO_DISCORD_SETUP.bat +
      Dado\Tools\discord\DISCORD_SETUP.txt (9 numbered steps with CHECKs).
      IDENTITY IS PINNED, NOT NARRATED. The lock check alone is not enough — it only
      fires while the OTHER gateway is running, so with Aze's gateway stopped her
      token would have sailed through. So the setter now asks Discord /users/@me
      itself, refuses a bot named like Aze, requires Rachad's own typed YES, and
      records DISCORD_EXPECTED_BOT_ID; the check tool FAILS on a mismatch instead of
      printing ALL CLEAR. CHECK_DADO_DISCORD.bat also distinguishes exit 2
      ("CHECK INCOMPLETE" — something unproven, usually she is not running) from a
      pass, because reporting unproven as PASSED is the same false comfort as the
      stale status line.
      TWO SETUP-GUIDE FACTS CORRECTED AGAINST THE ADAPTER, not the vendor docs:
      (a) MESSAGE CONTENT INTENT missing means she NEVER COMES ONLINE — the adapter
      sets `intents.message_content = True` unconditionally, so Discord refuses the
      connection; she does not appear online-but-silent. (b) SERVER MEMBERS INTENT is
      NOT required here: the adapter requests it only when the allowlist holds a
      non-numeric entry or a role, and Rachad's is a numeric user id. (c) The server
      invite is MANDATORY even for DM-only use — Discord will not open a DM with a
      bot you share no guild with — and the invite needs Send Messages, not just the
      OAuth scopes.
      DELIBERATE: no DISCORD_HOME_CHANNEL. Proactive output (inbox watch, follow-up
      digest, job watch, conduct review, urgent alerts) stays on TELEGRAM so nothing
      is duplicated; Discord is a lane he asks on. SOUL gained a "## YOUR TWO LANES"
      section (synced to the live profile).
      *** gateway_state.json LIES BY OMISSION — do not trust it raw. *** Per-platform
      entries are NEVER cleared. Hers still claimed discord "connected" from
      2026-08-04 on a gateway started 2026-08-10, and whatsapp "fatal" from a config
      she no longer has. Both the check tool and the lane-health watcher compare every
      entry against the gateway's own start_time (which is psutil create_time in
      CENTISECONDS — divide by 100) and treat anything older as ABSENT, never as truth.
- [x] BROWSER LANE LOCK (2026-08-10, shipped with the Discord lane — the one
      DATA-CORRUPTING risk a second lane creates). The authenticated UI sessions are
      singletons: Zoho on CDP 9228, WordPress on 9229. The write tools do not launch a
      browser — they attach with connect_over_cdp and drive an EXISTING tab
      (wordpress_plugin_deployment_tool took contexts[0].pages[0] unfiltered;
      zoho_inventory_classification_tool takes the first /app page and navigates it).
      Two concurrent turns get THE SAME PAGE OBJECT, so a click landing after the
      other lane navigated is a live business write on the wrong screen. The tools'
      own replay locks are per PLAN and never contend across different plans.
      Dado\Tools\common\ui_lane_lock.py: O_EXCL file lock per browser, PID-liveness
      reclamation (not age alone — a browser write legitimately takes minutes),
      release-only-what-we-own by pid+nonce, and RE-ENTRANT PER THREAD (not per
      process — the gateway runs the lanes as threads, so a process-wide counter
      would let the second lane walk straight through; a test pins this).
      Wired via decorators so the commissioned bodies stay byte-identical:
      classification commit_create/commit_assign, and WordPress commit_replace/
      commit_activate/commit_deactivate plus admin_session() itself. The command-level
      hold is deliberate — activation closes its session before its emergency
      rollback re-opens one, and the hold must span that gap.
      ACQUIRED BEFORE THE PLAN'S REPLAY LOCK, always: a busy browser must be a FREE
      refusal, never a mid-write abort that permanently locks Rachad's plan.
      NOT a wall — it removes no capability. TOOL_VERSION deliberately NOT bumped:
      the recorded PREFLIGHT EVIDENCE carries `tool_version` and the commit path
      refuses evidence whose version does not match the running build, so bumping
      it would invalidate the fresh three-rehearsal preflight a staged activation
      depends on. (Prefer symbol names over line numbers when citing this file —
      any edit above a cited line silently makes the citation wrong.)
      *** THE EXCLUSION IS A WINDOWS NAMED MUTEX, NOT A LOCK FILE. *** The first
      cut used an O_EXCL file with PID-liveness reclaim; an adversarial review
      REPRODUCED two defects in it with real subprocesses: (1) reclaim was
      check-then-act, so a stalled process could unlink the NEW holder's lock and
      both ran at once (measured: 9 seconds of overlap); (2) a hard-killed holder
      whose PID was later reused wedged the lane permanently, and the documented
      HARD_STALE backstop was never consulted on the acquire path. The kernel
      object has neither failure mode — acquisition is atomic, and a dead holder
      either releases (WAIT_ABANDONED) or takes the object with it. The JSON file
      beside it is now PURELY INFORMATIONAL (who/why, for the refusal message);
      it never grants or denies. The mutex name includes a hash of LOCK_DIR so a
      test run cannot contend with the live browser — and BOTH commissioned
      suites now redirect LOCK_DIR in setUpModule, because before that the Zoho
      suite took the real lock 25x per run and WooCommerce 98x.
      Tests: 19 (test_ui_lane_lock.py) covering cross-thread AND cross-process
      exclusion plus the killed-holder case; Zoho 476 pass, WooCommerce 561 pass /
      1 PHP-only skip.
- [x] CHAT-LANE HEALTH WATCHER (2026-08-10). THE WATCHDOG WAS BLIND TO CHAT: it
      treats a listener on 8647 as liveness, but that is API_SERVER_PORT, and hermes
      keeps the gateway UP when a chat adapter fails — her own log shows Discord down
      ~90s on 2026-08-04 with everything reporting healthy. That is the 2026-07-25
      shape, and two lanes make it worse (the surviving lane hides the dead one).
      Dado\Tools\watch\dado_lane_health.py: for every chat lane whose token is
      actually configured, is it connected on THIS gateway run? Silent when healthy,
      out-of-band Telegram alert via dado_urgent_alert.py when not, respects
      gateway_disabled.flag, and NEVER restarts anything (a restart kills in-flight
      turns). Called from dado_gateway_watchdog.ps1 on the port-is-up path, wrapped so
      a broken checker cannot take down the keep-alive; -WhatIfOnly passes --dry-run
      through so the rehearsal switch stays a real rehearsal.
      THREE PAGING RULES, each one a defect an adversarial review caught before it
      ever fired: (1) TWO consecutive bad samples before paging — hermes reconnects
      on its own (Discord retries at +30s) and a single sample would page Rachad for
      something already fixing itself. (2) A credential whose .env mtime is NEWER
      than the gateway's start_time means "configured, awaiting restart", NOT down —
      without this the documented setup order guaranteed a false "DISCORD LANE IS
      DOWN" page in the window between step 5 (write token) and step 7 (restart).
      A lane the gateway DID report as broken is still reported in that window.
      (3) --clear is called ONLY after we actually alerted: dado_urgent_alert --clear
      also deletes the SHARED last-resort Desktop marker, so calling it on every
      healthy 5-minute run would quietly erase the marker left by a real
      gateway-down alert. Alert text also branches — a `fatal` lane (e.g. token held
      by another gateway) is told NOT to just restart, because a restart cannot fix
      it and would end whatever is running on the surviving lane.
      Tests: 33, all passing.
- [x] NEIGHBOUR HEARTBEAT — DADO WATCHES AZE'S WATCHDOG (2026-08-11, Rachad
      asked for it after the incident below). THE ONLY PLACE THIS TREE REACHES
      INTO C:\AgentTeam, and it reaches for exactly one path.
      WHY: Aze's 5-minute watchdog was found REFUSING TO START ANYTHING for ~24h
      (Aug 10 13:38 -> Aug 11 13:33). `hermes gateway install` recreated
      `Hermes_Gateway_aze.vbs` in the Startup folder; TDI's
      `Assert-TdiManagedStartSafety` throws on any active `^Hermes_Gateway.*\.vbs$`
      and exited 42 every run. Nothing showed it: `aze_watchdog_launch.cmd` ended
      in an unconditional `exit /b 0`, so Task Scheduler read 0x0, and the log was
      `>` truncated every 5 minutes, destroying the evidence. She survived only
      because her process happened to stay alive; when stopped, nothing recovered
      her. That is the 2026-07-25 shape again. Third recurrence on record (Jul 29,
      Aug 7, Aug 10) — it WILL come back on the next `hermes update`.
      TDI-SIDE (all logic, state, logging and alerting live there, not here):
      `Sync\aze_urgent_alert.py` (stdlib-only Telegram, marker
      `AZE_NEEDS_ATTENTION.txt` — deliberately NOT Dado's, since `--clear`
      deletes it and a shared name would erase her unread alert),
      `Sync\aze_health_check.py` (watchdog_blocked / gateway_down /
      chat_lane_down), `Sync\aze_heartbeat_check.py`, plus 35 tests. The launcher
      now propagates its real exit code and appends to
      `always_on_watchdog.history.log`.
      THIS SIDE: `dado_gateway_watchdog.ps1` gained ONE guarded call to
      `C:\AgentTeam\Sync\aze_heartbeat_check.py`. Three deliberate choices, do not
      undo them: (a) it runs BEFORE the disable-flag check — that flag means
      "Rachad stopped DADO on purpose" and is no reason to stop watching Aze;
      (b) it is DETACHED fire-and-forget, so Dado's own recovery can never wait on
      a neighbour's monitor; (c) its stdout is DISCARDED, so no TDI detail can
      reach an FRP log, git history or the nightly conduct bundle. A missing file
      means TDI is not installed here and is silently skipped.
      WHY A HEARTBEAT AT ALL: `aze_health_check.py` runs from inside Aze's
      watchdog, so it can report a blocked run but never its own absence. Only a
      SEPARATE scheduler can notice hers stopped — hence Dado's task, which is a
      different task on a different cycle. Verified live end-to-end: blocked run
      paged on the 2nd sample, stale heartbeat paged on the 2nd sample, and both
      cleared on recovery.
      *** RECIPROCAL — AZE WATCHES DADO'S WATCHDOG TOO (2026-08-11, same ask). ***
      Adding the above made `dado_gateway_watchdog.ps1` the single point the whole
      chain hangs off, and nothing watched IT: if the "FRPDepot Dado Gateway
      Keep-Alive" task stopped, Dado would lose auto-recovery AND Aze would lose
      her monitor, silently. So it is now mutual.
      `dado_gateway_watchdog.ps1` stamps `40_Logs\dado_watchdog_heartbeat.txt`
      (gitignored, so no 5-minute commit churn and nothing enters the nightly
      bundle). `Dado\Tools\watch\dado_heartbeat_check.py` reads its AGE and pages
      through Dado's own alerter — a Dado problem should arrive from Dado's bot.
      `aze_watchdog_launch.cmd` invokes it guarded + detached with output
      discarded, the exact mirror of the containment in the other direction.
      *** THE STAMP IS FIRST IN THE FILE, BEFORE THE DISABLE-FLAG EXIT. ***
      STOP_DADO stops the GATEWAY, not the task. If the stamp sat after that exit,
      every deliberate stop would read as a dead watchdog and page Rachad for
      something he did on purpose. A test pins it, and it was verified live with
      the flag set. Both alert texts also say outright that the flag does NOT
      explain a stale heartbeat, so the obvious guess cannot wave away a real
      failure.
      NEITHER SIDE CAN REPORT BOTH SCHEDULERS BEING DEAD — inherent to mutual
      monitoring, stated rather than hidden. Both checkers go silent when the
      OTHER tree is absent (`not_installed`), so a single-company PC never pages.
      Tests: 15 (dado) + 35 (aze), all passing. Verified live in both directions:
      stale heartbeat paged on the 2nd sample and cleared on recovery.
- [x] GitHub remote wired + pushing (2026-07-23): see Machine/runtime
      section above.

## How to work with Rachad
Baby steps, numbered, one action per step with a CHECK. Buttons
(.bat) over commands. One question at a time with a recommendation.
Terse, worst-news-first. Never relitigate his decisions.
