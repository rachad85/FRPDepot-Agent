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
- [x] GitHub remote wired + pushing (2026-07-23): see Machine/runtime
      section above.

## How to work with Rachad
Baby steps, numbered, one action per step with a CHECK. Buttons
(.bat) over commands. One question at a time with a recommendation.
Terse, worst-news-first. Never relitigate his decisions.
