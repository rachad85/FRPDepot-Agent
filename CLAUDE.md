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
- Python: no `py` launcher on this server — use
  "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe".
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
- [ ] Zoho: Rachad creates an API client in the Zoho API console
      (one-time OAuth, like the Intuit flow); then build read-only
      Books/Inventory report tools.
- [x] GitHub remote wired + pushing (2026-07-23): see Machine/runtime
      section above.

## How to work with Rachad
Baby steps, numbered, one action per step with a CHECK. Buttons
(.bat) over commands. One question at a time with a recommendation.
Terse, worst-news-first. Never relitigate his decisions.
