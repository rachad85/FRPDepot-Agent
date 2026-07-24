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
- Model: gpt-5.6-sol on openai-codex (global OAuth, shared plan with
  the TDI five — quota pressure is a known watch item). NO fallback
  provider on purpose: primary down = honest failure, never silent
  model drift (TDI learned this the hard way 2026-07-16).
- Hermes is PINNED — never `hermes update` casually (TDI rule, same
  install).
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
3. Zoho READ-ONLY until Rachad commissions a named write tool.
4. Company wall: FRP Depot data never mixes with TDI data.
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
      read-only + Drive read-only + Gmail drafts, TDI-filtered.
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
      commissioning rule as Zoho, Golden Rule 3). Every Gmail/Drive result
      is screened by Dado\Tools\google\tdi_filter.py before Dado sees it:
      flagged search hits are withheld and counted, a flagged direct --id
      read is refused outright. Code: Dado\Tools\google\{google_auth,
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
      TWO REAL FILTER BUGS FOUND AND FIXED on that first live run — do not
      regress either:
      (a) SCREEN WHAT YOU RETURN, not what you happen to check. The first
          cut screened Subject+From only, but a Gmail `q=` search matches
          BODY text, and gmail-read returned the full body after checking
          only the snippet. Live result: 10/10 "Troy Dualam" hits reached
          Dado, 0 withheld. Both Gmail paths now fetch format=full and screen
          every header value + snippet + full body (_message_is_tdi);
          drive-read screens fetched CONTENT, not just the filename.
          drive-search still screens filenames ONLY (a listing exposes no
          content and downloading every hit is unbounded) — that limitation
          is stated in its own output; never treat a Drive hit as cleared.
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
      *** THE CACHE IS GATED CLOSED (2026-07-24) — HARD RULE 4 BREACH. ***
      google_reference.py fails closed (exit 2) and must stay that way until
      Rachad decides the cache's fate. Nothing was deleted.
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
      NOT PATCHABLE IN PLACE: both index loops skip ids already stored, so
      widening TDI_TERMS can never re-screen existing rows. Any fix is
      delete-and-rebuild, and the rebuild needs a filter-version stamp.
      SCOPE IS ALSO STILL RACHAD'S CALL: the index holds ~29k personal Gmail
      messages spanning 2007-2026 plus Drive file CONTENT, ~305MB unencrypted
      on the shared server. That is far broader than the narrow read+draft
      access he scoped the same day, whose own note called "downloading every
      hit" deliberately out of bounds. Default recommendation: revert to the
      commissioned live per-query tool and keep no bulk cache.
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
- [ ] Zoho: Rachad creates an API client in the Zoho API console
      (one-time OAuth, like the Intuit flow); then build read-only
      Books/Inventory report tools.
- [x] GitHub remote wired + pushing (2026-07-23): see Machine/runtime
      section above.

## How to work with Rachad
Baby steps, numbered, one action per step with a CHECK. Buttons
(.bat) over commands. One question at a time with a recommendation.
Terse, worst-news-first. Never relitigate his decisions.
