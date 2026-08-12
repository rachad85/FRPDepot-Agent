# Remaining cron-audit plans - designed 2026-08-11, NOT yet implemented

> DATA WALL NOTE. These plans were designed by agents that could see the whole
> machine, including the neighbouring TDI profile on the same shared hermes
> install. Cross-company specifics (job names, TDI paths) have been scrubbed:
> this file lives in the FRP repo and feeds the nightly conduct bundle, and
> Hard Rule 4 keeps the two companies' material apart. Where a step belongs to
> the TDI side it is named as such without detail.

Source: the 11-agent design workflow of 2026-08-11, each plan adversarially
reviewed against the real code before being recorded here. Saved because the
workflow output and the scratchpad copy were both swept mid-session; the plans
were recovered from the workflow journal.

READ THE REVIEW NOTES. Every plan came back with corrections - several would
have shipped a regression as written. Where a corrected_plan exists it SUPERSEDES
the plan text above it.

These five are FRP-side. B-08 appears twice in the audit as two entries for one
defect. The Zoho one is the critical partial: the daily banking review has never
produced a single review, and the data-centre half is fixed while the
account-filter half is not.


## dado-b08

(plan not recovered)

## dado-b08 (duplicate entry)

(plan not recovered)


==============================================================================
## dado-cron-mirror
==============================================================================

**Finding:** Dado's 13 live cron jobs have no mirror, no export path and no drift detection — the SOUL-drift work of 2026-08-11 stopped one file short

**Verdict:** fix-needed  |  **Effort:** medium  |  **Needs hermes patch:** False  |  **Needs fingerprints note:** False

### Root cause

CONFIRMED, and one part is worse than reported.

1. No cron mirror at all. `C:\FRPDepot\DadoProfile\` holds exactly three entries — SOUL.md, config.yaml, skills\ (verified by directory listing). There is no `cron\`. The only copy of Dado's schedule is `C:\Users\TDI-service\AppData\Local\hermes\profiles\dado\cron\jobs.json` (19,799 bytes, 13 jobs, all script jobs: dado-conduct-review c0f3deffbd33, dado-inbox-watch 5c7453fc85b6, dado-stall-tripwire faaabfa052e9, dado-job-watch 7eb0333f5a25, dado-followup-digest c31dcefd7cd2, dado-monthly-reorder-review 96e29b6507b1, dado-daily-banking-review 984e2f4f60e4, dado-zoho-session-watch 65fe685baaa1, gla-sync-ready-1457 672965791e21, gla-sync-ready-manway-1409 29e7b92ac084, gla-sync-ready-manway-cover-1412 acddc3db84aa, packing-order-monitor 89d95c692495, packing-observation-weekly-reminder fd8eee0f604f).

2. No export path for Dado. `C:\AgentTeam\Sync\EXPORT_HERMES_RUNTIME.ps1:5` — `$profiles = @("aze","jas","paul","john","marie")`; the cron copy is gated `if ($profile -eq "aze")`. Dado is absent, and correctly so: that script writes into `C:\AgentTeam\HermesProfiles`, on the far side of the data wall.

3. No drift detection on anything except SOUL.md. `dado_soul_sync.py` (commit 0dcf031) hard-codes `REPO_MIRROR = Path(r"C:\FRPDepot\DadoProfile\SOUL.md")` and compares that one file. `dado_gateway_watchdog.ps1` invokes it via `$SoulSync` inside the `if (Test-GatewayUp)` branch. Nothing compares jobs.json, the profile `scripts\` dir, or config.yaml.

4. WORSE THAN REPORTED — the config.yaml mirror exists but is silently 14 semantic keys stale, and text comparison cannot see it. Parsed with yaml.safe_load, live vs `DadoProfile\config.yaml` differ on: `fallback_providers[0].provider=nous` / `.model=deepseek/deepseek-v4-pro` (live-only), the entire `platforms.discord` block — `enabled=True`, `home_channel.chat_id='1536573814560002089'`, `.user_id='804875531220418623'`, `.name`, `.platform` (all live-only), five `display.platforms.discord.*` keys (live-only), `agent.gateway_notify_interval=300` (live-only), and `display.platforms.telegram.tool_progress` False live vs 'off' in the mirror. The committed mirror does not know Dado has a Discord lane — the second chat lane the watchdog comment dates to 2026-08-10. A byte/line comparison in the soul_sync style would ALSO be useless here for the opposite reason: the two files differ in key order (`title_generation`/`web_extract`/`approval`/`summary` are permuted) and in hand-written comments, so a text diff screams forever while the real 14-key gap hides inside the noise. Any config check must compare parsed data.

5. The mechanism behind "job_runner.py ran 3 days behind": `hermes` resolves a job's `script` name against `profiles\dado\scripts\`, which is outside both repos, and nothing compares those 14 deployed files to `C:\FRPDepot\Dado\Tools\**`. job_runner.py was fixed in the repo on 2026-08-08 (commit 8d45af2, "A finite job now has a ceiling") while cron kept executing the old deployed copy. Today (after this session's resync) it matches — `cc102ca9a098`, both sides 2026-08-08 11:55 — so the exposure is closed but the blindness is not.

6. Two deployed scripts MUST differ from the repo, which is why a naive copier is the wrong answer. `profiles\dado\scripts\gla_sync_ready_manway_monitor.py` and `..._cover_monitor.py` each prepend `sys.path.insert(0, r"C:\FRPDepot\Dado\Tools\watch")` before importing; the repo copies do a bare `from gla_sync_target_monitor import run_monitor`, and `gla_sync_target_monitor.py` is not in the profile scripts dir. Copying repo→live would ImportError both jobs. Byte-equality is therefore not the right predicate.

### Plan

RECOMMENDATION FIRST: a DETECTOR that also maintains a generated record — NOT an importer. Four independent reasons, each measured:

  (a) The truth direction is the OPPOSITE of SOUL.md, and 0dcf031's reasoning inverts here. SOUL.md is authored in the repo and copied into the profile, so mirror→live is safe. Cron jobs are authored in the LIVE profile (`hermes -p dado cron create`) and `cron/jobs.py::_save_jobs_unlocked` rewrites the whole file on every run of every job. The repo can never legitimately be ahead. A mirror→live copy is a lie by construction.
  (b) Importing a cron mirror is precisely the thing the neighbouring repo had to build a refusal gate against. `C:\AgentTeam\Sync\import_cron_preflight.py` docstring: measured on 2026-08-08, an import at that moment would have DELETED a weekly job on the neighbouring profile and RE-ARMED a spent one-shot against a real client case. Same store format, same hazard.
  (c) It would race the scheduler. `cron/jobs.py::save_jobs` is read-modify-write of the entire array under `_jobs_lock()`, then `atomic_replace`. An external writer that does not hold that lock can silently drop a claim or a completion.
  (d) For scripts, a copier would break two live jobs today — see root cause (6).

  What a detector buys instead: the committed record the finding asks for, a diff in git history when a job is deleted or re-scheduled, a name for the "running stale code" failure that actually bit, and zero write authority over the live profile.

ALSO REJECTED: adding "dado" to `C:\AgentTeam\Sync\EXPORT_HERMES_RUNTIME.ps1` as the finding's suggested_fix proposes. That script writes into `C:\AgentTeam\HermesProfiles\`; putting FRP Depot's schedule there is a data-wall breach for a convenience. The equivalent must be built inside `C:\FRPDepot`, driven by Dado's own keep-alive. No file under C:\AgentTeam is touched by this plan.

=====================================================================
STEP 1 — NEW FILE: C:\FRPDepot\Dado\Tools\watch\dado_profile_mirror.py
=====================================================================
Sibling of dado_soul_sync.py, same conventions (module-level path constants that tests monkeypatch; core functions take explicit paths; main() never raises; silent when clean).

```python
"""Keep a committed record of Dado's LIVE cron schedule, and notice when it moves.

WHY THIS EXISTS. Every scheduled duty Dado has - the daily banking review, the
inbox sweeps, the conduct review, the stall tripwire, the Zoho keep-alive -
exists in exactly ONE place on ONE disk:
`%LOCALAPPDATA%\\hermes\\profiles\\dado\\cron\\jobs.json`. Nothing commits it,
nothing diffs it, nothing notices when a job is deleted, disabled or
re-scheduled. The scripts those jobs run live in a THIRD place - the profile
`scripts\\` directory, outside this repo - and on 2026-08-08 job_runner.py was
fixed in the repo while cron kept running the old deployed copy for three days
with every health signal green.

DIRECTION OF TRUTH - THE OPPOSITE OF dado_soul_sync.py, AND THAT IS THE POINT.
SOUL.md is authored in the repo, so that sync writes mirror -> live. Cron is
authored in the LIVE profile and hermes rewrites jobs.json on every run of every
job, so here LIVE IS THE TRUTH and this tool only ever writes the REPO. It never
writes one byte into the profile, and it is not an importer: copying a mirror
over a live schedule is exactly what C:\\AgentTeam\\Sync\\import_cron_preflight.py
had to be built to refuse (measured 2026-08-08: it would have deleted a live job
and re-armed a spent one-shot against a real client case).

WHAT IT WRITES, all under C:\\FRPDepot\\DadoProfile\\cron\\ and all picked up by
the nightly conduct review's `git add -A`:
  jobs.mirror.json    - the DEFINITION of every job: schedule, script, deliver,
                        repeat budget, enabled/state. Runtime bookkeeping is
                        stripped and there is NO generated-at stamp, so the file
                        changes only when the SCHEDULE changes - not 288x a day.
  scripts.mirror.json - what is actually DEPLOYED in the profile scripts dir:
                        sha256 per file plus the repo file it should equal.
                        Content is NOT copied - it already lives in Dado\\Tools,
                        and a second copy in the same repo would be the very
                        drift this exists to catch.
  RECREATE.md         - the `hermes -p dado cron create` line for each job, so a
                        wiped schedule can be rebuilt BY A HUMAN. Prose on
                        purpose. There is no automatic restore and must not be.

The mirror is named jobs.MIRROR.json, not jobs.json, so nobody can ever copy it
over the live file on the assumption that matching names mean interchangeable
files. Restoring it would erase every job's progress and re-arm spent one-shots.

WHAT IT SAYS. Silent when everything matches. One line + one receipt when it
recorded a change. A Telegram, out of band via dado_urgent_alert.py, ONLY for
loss: a job in the mirror is gone from live, a job was switched off before
finishing its budget, the live store cannot be read twice running, or a deployed
script has been stale against the repo for more than a day.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(r"C:\FRPDepot")
MIRROR_DIR = REPO / "DadoProfile" / "cron"
CONFIG_MIRROR = REPO / "DadoProfile" / "config.yaml"
TOOLS = REPO / "Dado" / "Tools"
ALERTER = TOOLS / "watch" / "dado_urgent_alert.py"
RECEIPTS = REPO / "Dado" / "40_Logs" / "receipts.jsonl"
STATE = REPO / "Dado" / "40_Logs" / "cron_mirror_state.json"
STALE_SCRIPT_ESCALATE_HOURS = 24

# Rewritten by hermes on EVERY run of EVERY job. Excluding them is the whole
# reason this mirror can live in git: the file moves when the SCHEDULE moves.
VOLATILE_JOB_KEYS = {
    "next_run_at", "last_run_at", "last_status", "last_error",
    "last_delivery_error", "fire_claim", "paused_at",
}
VOLATILE_TOP_KEYS = {"updated_at"}
# A DENYLIST, not an allowlist, deliberately: a field a future `hermes update`
# adds must land in the record by default. Being told once about a new noisy
# field is cheaper than silently failing to record a new job attribute.

# Deployed copies that MUST differ from the repo. hermes resolves `script`
# against the profile scripts dir only, and gla_sync_target_monitor.py is not in
# that dir, so the deployed shims put the repo path on sys.path first. Copying
# the repo file over the live one would ImportError both jobs.
# PINNED ON BOTH SIDES: change either file and the exemption lapses by itself.
EXPECTED_DIVERGENCE = {
    "gla_sync_ready_manway_monitor.py": {
        "live": "c713f16580277e7f22e80fa44901386590f698e50fb5b727e0a93ceb1da31d7f",
        "repo": "a53c43ad58aa96bfeb0eb75f32f6dce8d744d7321ef176984e1c4a1864b880b9",
        "reason": "deployed shim inserts C:\\FRPDepot\\Dado\\Tools\\watch on sys.path",
    },
    "gla_sync_ready_manway_cover_monitor.py": {
        "live": "d0adf7e990d54d8faced5b7931c69641032e093a593169611b9b2d2294ad208b",
        "repo": "3beafd2e6eaef42a524ec48ffc2b37023a671bc0fe1787e109023bf4d5611485",
        "reason": "deployed shim inserts C:\\FRPDepot\\Dado\\Tools\\watch on sys.path",
    },
}


def profile_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise RuntimeError("LOCALAPPDATA is not set; cannot locate the live profile.")
    return Path(base) / "hermes" / "profiles" / "dado"


def read_live_jobs(jobs_path: Path) -> list[dict]:
    """One read_bytes, no held handle, one retry.

    hermes replaces jobs.json with atomic_replace (cron/jobs.py
    _save_jobs_unlocked). On Windows a replace FAILS while another process holds
    the destination open, so a reader that lingers can make the SCHEDULER lose a
    job update. Read the whole file in a single call and let it close before
    parsing; never parse from an open handle.
    """
    last = None
    for attempt in range(2):
        try:
            raw = jobs_path.read_bytes()
            data = json.loads(raw.decode("utf-8-sig"))
            return data if isinstance(data, list) else list(data.get("jobs", []))
        except (OSError, ValueError) as exc:
            last = exc
            if attempt == 0:
                time.sleep(0.5)
    raise RuntimeError(f"{type(last).__name__}: {last}")


def definition(job: dict) -> dict:
    out = {k: v for k, v in job.items() if k not in VOLATILE_JOB_KEYS}
    repeat = out.get("repeat")
    if isinstance(repeat, dict):
        out["repeat"] = {"times": repeat.get("times")}   # budget, not progress
    return out


def projection(jobs: list[dict]) -> dict:
    return {
        "_generated_by": "Dado/Tools/watch/dado_profile_mirror.py",
        "_source": r"%LOCALAPPDATA%\hermes\profiles\dado\cron\jobs.json",
        "_warning": (
            "RECORD ONLY - NEVER COPY THIS OVER THE LIVE jobs.json. Runtime "
            "state is stripped on purpose, so restoring it would erase every "
            "job's progress and re-arm spent one-shots. Rebuild from "
            "RECREATE.md instead."
        ),
        "jobs": sorted((definition(j) for j in jobs),
                       key=lambda j: (j.get("name") or "", j.get("id") or "")),
    }


def render(obj: dict) -> str:
    # sort_keys so a hermes update that reorders keys cannot produce a phantom
    # diff; no timestamp anywhere, for the same reason.
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write_text_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".mirror.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
```

Job comparison — loss vs change (match on id; a name that reappears under a new id is a re-creation, not a loss):

```python
def compare_jobs(previous: list[dict], current: list[dict]) -> tuple[list[str], list[str]]:
    """Returns (changes, losses). Losses are the only Telegram-worthy class."""
    prev = {j.get("id"): j for j in previous}
    live = {j.get("id"): j for j in current}
    live_names = {j.get("name") for j in current}
    changes, losses = [], []

    for jid in sorted(set(live) - set(prev)):
        j = live[jid]
        changes.append(f"new job '{j.get('name')}' ({jid}, {j.get('script') or 'agent job'}, "
                       f"{(j.get('schedule') or {}).get('display')})")
    for jid in sorted(set(prev) - set(live)):
        j = prev[jid]
        if j.get("name") in live_names:
            changes.append(f"job '{j.get('name')}' was re-created under a new id (was {jid})")
        else:
            losses.append(f"job '{j.get('name')}' ({jid}, {j.get('script') or 'agent job'}, "
                          f"{(j.get('schedule') or {}).get('display')}) is in the committed "
                          f"record but NO LONGER IN THE LIVE SCHEDULE")
    for jid in sorted(set(prev) & set(live)):
        before, after = prev[jid], definition(live[jid])
        for key in sorted(set(before) | set(after)):
            if before.get(key) == after.get(key):
                continue
            if key in ("enabled", "state") and _finished_its_budget(live[jid]):
                # A budgeted monitor reaching times==completed is a normal end,
                # not an outage. Recorded, never paged. (The three GLA monitors
                # did exactly this on 2026-08-10.)
                changes.append(f"job '{after.get('name')}' finished its "
                               f"{(live[jid].get('repeat') or {}).get('times')}-run budget "
                               f"and switched itself off")
            elif key == "enabled" and before.get(key) and not after.get(key):
                losses.append(f"job '{after.get('name')}' ({jid}) was DISABLED without "
                              f"finishing its schedule")
            else:
                changes.append(f"job '{after.get('name')}' {key}: "
                               f"{json.dumps(before.get(key))} -> {json.dumps(after.get(key))}")
    return changes, _dedupe(losses)


def _finished_its_budget(live_job: dict) -> bool:
    repeat = live_job.get("repeat") or {}
    times, done = repeat.get("times"), repeat.get("completed")
    return (times is not None and times == done
            and str(live_job.get("state") or "").lower() == "completed")
```

Scripts manifest + drift:

```python
def repo_source(name: str) -> tuple[Path | None, str]:
    hits = [p for p in TOOLS.rglob(name) if "__pycache__" not in p.parts]
    if not hits:
        return None, "no file of that name under Dado\\Tools"
    if len(hits) > 1:
        return None, "ambiguous: " + ", ".join(str(p.relative_to(REPO)) for p in hits)
    return hits[0], ""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def survey_scripts(scripts_dir: Path, used_by: dict[str, list[str]]) -> tuple[dict, list[str]]:
    rows, notes = [], []
    for path in sorted(scripts_dir.glob("*.py")):
        live_sha = sha(path)
        src, why = repo_source(path.name)
        row = {"name": path.name, "sha256": live_sha, "bytes": path.stat().st_size,
               "used_by": used_by.get(path.name, [])}
        if src is None:
            row["repo_source"] = None
            row["matches_repo"] = False
            notes.append(f"deployed script {path.name} has NO source of truth in the repo ({why})")
        else:
            repo_sha = sha(src)
            row["repo_source"] = str(src.relative_to(REPO)).replace("\\", "/")
            row["repo_sha256"] = repo_sha
            pin = EXPECTED_DIVERGENCE.get(path.name)
            if live_sha == repo_sha:
                row["matches_repo"] = True
            elif pin and pin["live"] == live_sha and pin["repo"] == repo_sha:
                row["matches_repo"] = False
                row["divergence"] = "expected"
                row["reason"] = pin["reason"]
            else:
                row["matches_repo"] = False
                row["divergence"] = "UNEXPECTED"
                notes.append(
                    f"deployed script {path.name} does not match {row['repo_source']} "
                    f"- cron is running the deployed copy, not the committed one"
                    + (" (the pinned exemption has lapsed: one side changed)" if pin else ""))
        rows.append(row)
    return ({"_generated_by": "Dado/Tools/watch/dado_profile_mirror.py",
             "_note": ("The live profile scripts directory is outside this repo and is what "
                       "hermes actually executes. Content is not copied here - it lives in "
                       "Dado/Tools. This records what is DEPLOYED and whether it matches."),
             "scripts": rows}, notes)
```

config.yaml — parsed, never textual:

```python
def config_notes(live_cfg: Path, mirror_cfg: Path) -> list[str]:
    """Semantic, because textual is useless here.

    The two files differ in key ORDER and in hand-written comments while
    agreeing on the data, and they differ on real settings while looking
    similar. Measured 2026-08-11: 14 semantic differences, including the whole
    platforms.discord block, which the committed mirror does not know exists.
    Report only - never rewrite either side. Copying mirror -> live would delete
    Dado's Discord lane; copying live -> mirror would destroy the curated
    comments that explain why each key is set.
    """
    import yaml
    live = _flatten(yaml.safe_load(live_cfg.read_text(encoding="utf-8")))
    mirror = _flatten(yaml.safe_load(mirror_cfg.read_text(encoding="utf-8")))
    diffs = [k for k in sorted(set(live) | set(mirror)) if live.get(k, _ABSENT) != mirror.get(k, _ABSENT)]
    if not diffs:
        return []
    return [f"config.yaml: {len(diffs)} setting(s) differ between the live profile and the "
            f"committed mirror: " + ", ".join(diffs[:8]) + ("..." if len(diffs) > 8 else "")]
```
(`_flatten` walks dicts/lists into `/a/b/0/c` paths; `_ABSENT` is a module sentinel.)

RECREATE.md:

```python
def recreate_lines(jobs: list[dict]) -> str:
    out = ["# Rebuilding Dado's cron schedule by hand",
           "",
           "GENERATED - do not edit. There is no automatic restore on purpose: an",
           "importer would erase runtime state and re-arm spent one-shots. Run the",
           "line you need, then compare against jobs.mirror.json.",
           "",
           "NOTE: `cron create` has no --enabled flag. Every job below comes back",
           "ENABLED; pause the ones jobs.mirror.json records as disabled.",
           ""]
    for j in sorted(jobs, key=lambda j: j.get("name") or ""):
        sched = j.get("schedule") or {}
        spec = sched.get("expr") if sched.get("kind") == "cron" else sched.get("display")
        cmd = [f'hermes -p dado cron create "{spec}"', f'--name {j.get("name")}']
        if j.get("script"):   cmd.append(f'--script {j["script"]}')
        if j.get("no_agent"): cmd.append("--no-agent")
        if j.get("deliver"):  cmd.append(f'--deliver {j["deliver"]}')
        if (j.get("repeat") or {}).get("times"): cmd.append(f'--repeat {j["repeat"]["times"]}')
        if j.get("workdir"):  cmd.append(f'--workdir "{j["workdir"]}"')
        if j.get("model"):    cmd.append(f'--model {j["model"]}')
        if j.get("provider"): cmd.append(f'--provider {j["provider"]}')
        for s in (j.get("skills") or []): cmd.append(f'--skill {s}')
        out += [f'## {j.get("name")}  ({j.get("id")}, {"enabled" if j.get("enabled") else "DISABLED"})',
                "```", " ".join(cmd), "```", ""]
    return "\n".join(out) + "\n"
```
(Flags verified against `hermes_cli/subcommands/cron.py::build_cron_parser`: positional schedule, --name, --deliver, --repeat, --skill, --script, --no-agent, --workdir, --model, --provider. `schedule.display` for interval jobs is already CLI-shaped — "every 10m", "every 30m".)

Report / alert / state:

```python
def append_receipt(action: str, evidence: str) -> None:
    try:
        RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": datetime.now(timezone.utc).isoformat(), "action": action, "evidence": evidence}
        with RECEIPTS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass   # a receipt failure must never stop the check or the keep-alive


def raise_alert(reason: str, message: str, *, dry_run: bool, clear: bool = False) -> None:
    """Out of band, same path the watchdog already trusts.

    dado_urgent_alert.py talks straight to the Telegram Bot API with the
    standard library - no hermes, no gateway, no cron deliver - which is what
    B-08 says the cron telegram path cannot promise. Its own 60-minute per-reason
    cooldown means a standing loss re-announces hourly until acknowledged, and
    stops the moment --accept-removals clears it.
    """
    args = [sys.executable, str(ALERTER), "--reason", reason]
    args += ["--clear"] if clear else ["--message", message]
    if dry_run:
        args.append("--dry-run")
    try:
        subprocess.run(args, capture_output=True, text=True, timeout=60)
    except Exception as exc:
        print(f"cron-mirror: ALERT PATH FAILED ({type(exc).__name__}: {exc})")
```

main(): args `--dry-run`, `--accept-removals`, `--profile-dir`, `--mirror-dir` (the last two exist so the loss path can be rehearsed against a temp copy without touching the live schedule — see verification). Flow:
  1. read live jobs (on failure: bump `read_failures` in STATE; on the 2nd consecutive failure raise_alert("dado_cron_store_unreadable"); return 2). On success reset the counter.
  2. previous = jobs from the existing jobs.mirror.json (missing file → seed mode).
  3. changes, losses = compare_jobs(previous, live).
  4. scripts survey + config notes.
  5. Stale-script escalation: STATE records `stale_scripts: {name: first_seen_iso}`; any entry older than STALE_SCRIPT_ESCALATE_HOURS raises `dado_cron_script_stale`. Entries clear when the drift clears.
  6. If losses and not --accept-removals: DO NOT rewrite jobs.mirror.json (the last known-good record is the thing being protected). Print the loss lines, raise_alert("dado_cron_job_lost", ... ending with the exact literal ack command `python C:\FRPDepot\Dado\Tools\watch\dado_profile_mirror.py --accept-removals`), receipt once per changed signature, return 1.
  7. Otherwise write the three files if their rendered text differs from what is on disk (byte-compare first — never touch an unchanged file), print one summary line per change, receipt, and if --accept-removals also raise_alert(clear=True). Return 0.
  8. Signature dedupe: sha256 of the joined report lines stored in STATE; identical signature → print nothing and write no receipt (this is what keeps a standing, already-reported condition from filling the nightly bundle 288 times a day).
  9. --dry-run: everything computed and printed, nothing written, alerts passed through with --dry-run.
  10. main() wraps in `except Exception as exc: print(f"cron-mirror ERROR: {exc}"); return 1`.

=====================================================================
STEP 2 — WIRE IT INTO THE 5-MINUTE KEEP-ALIVE
=====================================================================
Edit `C:\FRPDepot\Dado\Tools\watch\dado_gateway_watchdog.ps1` (task "FRPDepot Dado Gateway Keep-Alive", verified: 5-minute repetition, `wscript.exe //B run_hidden.vbs powershell -File ...dado_gateway_watchdog.ps1`).

Add beside the existing `$SoulSync` declaration:
```powershell
$ProfileMirror = Join-Path $Root 'Dado\Tools\watch\dado_profile_mirror.py'
```
Insert as step 0c — after the 0b neighbour-heartbeat block and BEFORE the `if (Test-Path $DisableFlag)` exit:
```powershell
# 0c. The SCHEDULE itself needs a record. All 13 of Dado's cron jobs live in one
#     file on one disk with no committed copy, and the scripts they run live in a
#     third place outside this repo - which is how job_runner.py ran three days
#     behind its own fix with every signal green.
#
#     Deliberately ABOVE the disable-flag exit and outside the gateway-up branch:
#     `hermes cron remove` works with the gateway stopped, so a schedule can lose
#     a job precisely while nothing else is watching. This check needs no gateway
#     - it reads jobs.json and writes only inside C:\FRPDepot.
#
#     It is a DETECTOR, not an importer. It never writes into the live profile.
if ((Test-Path $ProfileMirror) -and (Test-Path $VenvPython)) {
    $mirrorArgs = @($ProfileMirror)
    if ($WhatIfOnly) { $mirrorArgs += '--dry-run' }
    try {
        & $VenvPython @mirrorArgs 2>&1 | Where-Object { $_ } | ForEach-Object {
            Write-Log "cron-mirror: $_"
        }
    } catch {
        # Same rule as the lane checker and the soul sync: never take down the
        # keep-alive.
        Write-Log "CRON MIRROR CHECK FAILED: $($_.Exception.Message)"
    }
}
```
Conservative alternative if a reviewer objects to running during a deliberate STOP: move the identical block into the `if (Test-GatewayUp)` branch immediately after the `$SoulSync` block. That costs the gateway-stopped coverage; everything else is the same.

=====================================================================
STEP 3 — SEED THE MIRROR (one manual run, reviewed before commit)
=====================================================================
Run once by hand so the first three files are inspected by a human before the nightly `git add -A` commits them. Creates:
  C:\FRPDepot\DadoProfile\cron\jobs.mirror.json
  C:\FRPDepot\DadoProfile\cron\scripts.mirror.json
  C:\FRPDepot\DadoProfile\cron\RECREATE.md
No .gitignore change needed — `C:\FRPDepot\.gitignore` excludes `Dado/40_Logs/`, `Jobs/`, `*.bak`, `*.tmp`, `__pycache__/`, `Dado/20_Working/`, vendor and one credential file. `DadoProfile/cron/` is unaffected. The new STATE file lives in the ignored 40_Logs, alongside urgent_alert_state.json.

=====================================================================
STEP 4 — REGISTER THE NEW RECEIPT WRITER
=====================================================================
`C:\FRPDepot\Dado\Tools\watch\test_receipt_format.py` enforces one timestamp format across "every module that appends to receipts.jsonl" via a WRITERS list (B-30). Add to that list:
```python
    "watch/dado_profile_mirror.py",
    "watch/dado_soul_sync.py",
```
dado_soul_sync.py has been writing receipts since 0dcf031 and was never added — a real pre-existing gap in that guard, fixed here in passing. Both modules build `ts` from `datetime.now(timezone.utc)`, so both pass immediately.

=====================================================================
OPTIONAL FOLLOW-UP (do NOT bundle)
=====================================================================
The two pinned divergences exist only because `gla_sync_target_monitor.py` was never deployed to the profile scripts dir. Copying it there and re-deploying the two shims byte-identical to the repo would delete both EXPECTED_DIVERGENCE entries and make the predicate a clean byte-equality. Both jobs are `enabled:false, state:"completed"` today, so this is safe but not urgent, and it belongs to whoever owns the GLA-monitor finding — coordinate rather than both touching those files.

### Tests

NEW FILE: C:\FRPDepot\Dado\Tools\watch\test_dado_profile_mirror.py — unittest, TemporaryDirectory, real files (no mocks for file-touching code), module constants monkeypatched in setUp and restored via addCleanup, exactly like test_dado_soul_sync.py. Core functions take explicit paths so the live profile is never involved. Run from inside the watch dir so `import dado_profile_mirror` resolves (there is no conftest.py or pytest.ini anywhere in C:\FRPDepot).

Fixtures: a `live_jobs(**overrides)` helper producing a realistic 13-job list copied in shape from the live store (both `{"kind":"cron","expr":...}` and `{"kind":"interval","minutes":10,"display":"every 10m"}`), and a fake alerter — inject via `mirror.raise_alert = recorder` so the test asserts on (reason, message, clear) without ever invoking dado_urgent_alert.py.

CHURN / SILENCE — the tests that decide whether this can live in git:
  test_volatile_fields_never_reach_the_mirror — assert last_run_at, next_run_at, last_status, last_error, last_delivery_error, fire_claim, paused_at and repeat.completed appear nowhere in the rendered text; assert repeat.times survives.
  test_a_run_that_only_advanced_the_clock_rewrites_nothing — seed the mirror, then mutate ONLY volatile fields plus top-level updated_at; assert stdout empty, exit 0, no receipt, and st_mtime_ns of jobs.mirror.json unchanged.
  test_reordered_keys_do_not_produce_a_diff — same jobs, different dict insertion order; assert the file is not rewritten (guards against a hermes update reordering keys).
  test_the_mirror_carries_no_generated_at_timestamp — assert no ISO-8601-looking value in the header keys.

CHANGE RECORDING:
  test_a_new_job_is_added_to_the_mirror_and_announced — one line naming the job, script and schedule; receipt written; job present in the file.
  test_a_changed_schedule_is_announced_with_before_and_after.
  test_a_repeated_run_of_a_standing_condition_reports_only_once — run twice with the same unresolved drift; second run silent (signature dedupe), so the nightly bundle cannot be flooded 288x/day.

LOSS — the class that pages:
  test_a_removed_job_is_not_erased_from_the_mirror_and_alerts — assert exit 1, the job is STILL in jobs.mirror.json, the file was not rewritten, and raise_alert got reason "dado_cron_job_lost".
  test_the_alert_names_the_acknowledgement_command — assert the literal "--accept-removals" is inside the message (an alert nobody can act on is the B-08 shape).
  test_accept_removals_writes_the_mirror_and_clears_the_alert — assert the job is gone from the file and raise_alert was called with clear=True.
  test_a_job_disabled_before_finishing_its_schedule_alerts — enabled true->false with repeat.times null.
  test_a_budget_exhausted_job_is_recorded_without_paging — repeat {"times":144,"completed":144}, enabled false, state "completed": assert a change line, a receipt, and ZERO alerts. This is the live GLA case and the house rule that a watcher speaking on healthy runs is a defect.
  test_a_job_recreated_under_a_new_id_is_a_change_not_a_loss — same name, different id: no alert.

SCRIPTS:
  test_script_drift_against_the_repo_is_reported — deployed sha != repo sha: assert the note names both the deployed name and the repo-relative source; assert scripts.mirror.json row has matches_repo false and divergence "UNEXPECTED".
  test_a_pinned_expected_divergence_is_silent — both hashes match EXPECTED_DIVERGENCE: no note, row carries divergence "expected" plus the reason.
  test_a_pinned_exemption_lapses_when_either_side_changes — two subtests (change the live byte; change the repo byte): each must report. Guards against the allowlist becoming permanent blindness.
  test_a_deployed_script_with_no_repo_source_is_reported_not_ignored.
  test_an_ambiguous_repo_filename_is_reported_not_guessed — same basename under two Tools subdirs.
  test_stale_script_escalates_to_an_alert_after_the_grace_window — pre-seed STATE with first_seen 25h ago: assert reason "dado_cron_script_stale"; and with first_seen 1h ago: assert no alert.

CONFIG:
  test_config_yaml_is_compared_semantically_not_textually — identical data, permuted key order and different comments: assert silent. This is the current live shape and the single reason a soul_sync-style line diff cannot be reused.
  test_a_setting_present_only_in_the_live_config_is_reported — insert `platforms.discord.enabled: true` live only; assert the path appears in the note. Reproduces the real 2026-08-11 gap.
  test_neither_config_file_is_ever_written — snapshot bytes+mtime of both, assert unchanged.

SAFETY:
  test_nothing_under_the_live_profile_is_ever_written — snapshot bytes and st_mtime_ns of every file under the temp profile dir before and after a run that changes jobs, scripts and config; assert byte-for-byte and mtime-for-mtime identical. The single most important test in the file.
  test_the_live_store_is_read_in_one_call_and_not_held_open — patch Path.read_bytes to count calls and assert the parse happens after it returns (guards the Windows atomic_replace hazard).
  test_an_unreadable_live_store_is_a_quiet_skip_the_first_time_and_alerts_the_second — assert exit 2 both times, no alert on run 1, reason "dado_cron_store_unreadable" on run 2, counter reset after a good read.
  test_a_broken_receipts_log_does_not_break_the_run — RECEIPTS pointed at Z:\nope\receipts.jsonl; assert exit 0 and the mirror still written (copied from test_dado_soul_sync.py's equivalent).
  test_dry_run_changes_nothing_and_sends_nothing — no files created, alerts recorded with dry_run True.
  test_no_temporary_file_is_left_behind — assert no *.mirror.tmp survives.
  test_a_missing_mirror_directory_seeds_cleanly — first-run path: three files created, no loss reported.

RECREATE:
  test_recreate_lists_every_job_with_a_runnable_command — 13 blocks; a cron job renders its expr, an interval job renders "every 10m"; --no-agent, --deliver, --repeat, --workdir, --model present where the job has them.
  test_recreate_warns_that_recreated_jobs_come_back_enabled — the CLI has no --enabled flag, so this must be stated or a restore silently re-arms a disabled job.

EDIT: test_receipt_format.py WRITERS gains "watch/dado_profile_mirror.py" and "watch/dado_soul_sync.py"; its existing test_every_writer_exists and test_no_writer_builds_a_receipt_timestamp_from_a_naive_now then cover both.

PROVING THEY FAIL WITHOUT THE FIX: every test above imports dado_profile_mirror, which does not exist today — collection fails outright, which is the honest baseline. The one test that is meaningful against the CURRENT system is the config semantic test: run `yaml.safe_load` over the two live config.yaml files today and they differ on 14 keys while `dado_soul_sync.lines_only_in_live` on the same pair reports dozens of textual differences that are almost all comments and key order — i.e. the existing tool's predicate, applied to config.yaml, would report drift forever and still not tell you the Discord lane is missing.

### Verification

Run in order. Steps 1-2 and 5-6 write nothing outside the repo; nothing here restarts a service or touches the live profile.

1. UNIT SUITE. `cd C:\FRPDepot\Dado\Tools\watch` then
   `& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" -m pytest test_dado_profile_mirror.py test_receipt_format.py test_dado_soul_sync.py -q`
   Expect the new file green and the two existing files still green (test_receipt_format now checks two extra writers).
   Then the whole watch suite: `... -m pytest . -q` — baseline is 121 tests per commit 0dcf031; expect 121 + the new ones, no regressions.

2. DRY RUN AGAINST LIVE, WRITING NOTHING.
   `& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" dado_profile_mirror.py --dry-run`
   Expect, from today's measured state:
     - a seed line for 13 jobs (no mirror exists yet), and NO loss lines;
     - no unexpected script drift — all 14 deployed scripts either match their repo source or are the two pinned GLA shims;
     - a config line naming ~14 differing settings including platforms/discord/enabled and fallback_providers.
   Then `cd C:\FRPDepot; git status --short DadoProfile` must be unchanged (only the pre-existing ` M DadoProfile/SOUL.md`). If the dry run created a file, stop — the dry-run contract is broken.

3. REAL SEED AND HUMAN REVIEW.
   Run without --dry-run. `git status --short DadoProfile` should now show three untracked files under DadoProfile/cron/. Inspect before letting the nightly commit take them:
     - `Select-String -Path DadoProfile\cron\jobs.mirror.json -Pattern 'last_run_at|next_run_at|last_status|fire_claim|"completed"'` must return NOTHING (proves the volatile strip; note "completed" as a repeat key must be absent — the string may still appear as a `state` value, so read the hits rather than trusting the count);
     - the file must contain 13 entries and all 13 job names;
     - `scripts.mirror.json` must show matches_repo true for 12 scripts and divergence "expected" for exactly the two GLA shims;
     - RECREATE.md must contain 13 `hermes -p dado cron create` blocks.

4. THE NO-CHURN PROOF — the one that decides whether this is safe to leave running.
   Record `(Get-FileHash DadoProfile\cron\jobs.mirror.json).Hash` and its LastWriteTime. Wait ~11 minutes so dado-job-watch (*/10) and dado-zoho-session-watch (*/10) both fire; confirm the live store moved by checking that `updated_at` in the live jobs.json changed. Re-run the tool. Expect: empty stdout, exit 0, hash and LastWriteTime IDENTICAL, and no new line in receipts.jsonl. If the hash moved, a volatile field escaped the denylist — find it and add it before wiring the watchdog, otherwise the nightly `git add -A` will commit a diff every single night.

5. LOSS PATH, REHEARSED WITHOUT TOUCHING THE LIVE SCHEDULE. Do not remove a real job.
   Copy the live cron dir to a temp dir, delete one job from the copy's jobs.json, and run
   `python dado_profile_mirror.py --profile-dir <temp profile> --dry-run`.
   Expect exit 1, a line naming the removed job by name and id, the ack command "--accept-removals" quoted in the alert text, and (dry-run) no Telegram sent. Repeat with the copy's `enabled` flipped to false on dado-stall-tripwire → same class. Repeat with one of the three GLA jobs (repeat 144/144, state completed) → change line, NO alert.

6. ALERT PATH READINESS WITHOUT SENDING.
   `python dado_urgent_alert.py --reason dado_cron_job_lost --self-test` — confirms the token and chat resolve and reports suppressed_now for that new cooldown key. It sends nothing.

7. WATCHDOG INTEGRATION, REHEARSAL FIRST.
   `powershell -NoProfile -ExecutionPolicy Bypass -File C:\FRPDepot\Dado\Tools\watch\dado_gateway_watchdog.ps1 -WhatIfOnly`
   Then tail `C:\FRPDepot\Dado\40_Logs\gateway_watchdog.log` for `cron-mirror:` lines, and confirm `git status` in C:\FRPDepot is unchanged (-WhatIfOnly must pass --dry-run through). Confirm the pre-existing `soul-sync:` and `lane-health:` behaviour is untouched and the script still exits 0 on a healthy gateway.

8. LIVE UNDER THE SCHEDULER. Wait for two 5-minute ticks of "FRPDepot Dado Gateway Keep-Alive". Expect: no new `cron-mirror:` lines at all once the mirror is seeded and committed (silence-when-clean), the gateway still up, and `git status` clean apart from the config note the tool reports but does not act on.

9. THE FINDING'S OWN REGRESSION, REPLAYED. Take a repo-only edit: touch a comment in `C:\FRPDepot\Dado\Tools\watch\stall_tripwire.py` in a scratch copy, point `--profile-dir` at a temp profile whose deployed stall_tripwire.py is the unmodified one, and confirm the tool says the deployed copy does not match the committed one. That is exactly the job_runner.py 2026-08-08→08-11 blindness, now named within five minutes instead of three days. Revert the scratch edit.

10. NIGHTLY LOOP. On the morning after, confirm `Dado\30_Memory\conduct_reviews\<date>.md` exists as usual and that the night's commit contains the three DadoProfile/cron files exactly once (from the seed), not a fresh diff.

### Risks

WHAT MUST NOT CHANGE
- Not one byte is written into `%LOCALAPPDATA%\hermes\profiles\dado\`. This tool is read-only on the live side, permanently. If a future version acquires a write path into the profile it becomes the importer that import_cron_preflight.py exists to refuse.
- No file under `C:\AgentTeam` is touched — not EXPORT_HERMES_RUNTIME.ps1, not Sync\, not HermesProfiles\. The finding's own suggested_fix proposes adding dado to the AgentTeam exporter; that is a data-wall breach and is rejected here. Consequently NO fingerprints note is needed (the guard at guard_round.ps1:105 covers C:\AgentTeam\{Masters,_Library,AzeChat,Sync,Aze\scripts} only) and NO hermes patch or $patches registration is needed (nothing under the shared install at C:\Users\TDI-service\AppData\Local\hermes\hermes-agent changes, so `hermes update` cannot revert any of this).
- dado_soul_sync.py's behaviour is untouched. The only edit near it is a new, separate PowerShell block and two new entries in a test's WRITERS list.

WHAT COULD BREAK
1. Windows atomic-replace contention — the real hazard. `cron/jobs.py::_save_jobs_unlocked` finishes with `atomic_replace`, which on Windows fails if another process holds the destination open. A reader that lingers over jobs.json can make the SCHEDULER's save fail and lose a claim or a completion. Mitigated by a single `read_bytes()` with the parse after it returns, one 0.5s retry, and a 5-minute duty cycle — microseconds of exposure per 300 seconds. The `test_the_live_store_is_read_in_one_call_and_not_held_open` test pins it. Do not "improve" this into a streaming or mmap read.
2. Mirror churn defeating the design. If any volatile field escapes VOLATILE_JOB_KEYS, the mirror is rewritten constantly, the nightly `conduct_review.py` `git add -A` commits a diff every night, and the record becomes noise nobody reads — the exact fate of Aze's raw `HermesProfiles\aze\cron\jobs.json`, which is a byte copy last exported 2026-08-09 and already stale. Verification step 4 is the gate; do not wire the watchdog until it passes.
3. New hermes fields after an update. The denylist design means a newly added volatile field would start churning. Cost is one noisy line and one commit, then add the key. This is the deliberate trade for not silently dropping new job attributes; state it in review.
4. Alert fatigue on a legitimate removal. If Rachad deletes a job on purpose, dado_urgent_alert's 60-minute cooldown re-announces hourly until someone runs `--accept-removals`. Mitigated by putting that exact command inside the alert text. If it still feels heavy, the cooldown key can be given a longer window — but do not make the alert one-shot: a one-shot alert that can be dropped is B-08, which is still OPEN.
5. Profile rebuild produces a false mass-loss. Re-creating all 13 jobs mints new ids, so every old id disappears. The name-matching branch reports those as "re-created under a new id" rather than lost, so no page. Genuinely new ids AND new names would page once, correctly. Document `--accept-removals` in RECREATE.md.
6. The nightly reviewer edits a generated file. conduct_review.py runs headless Claude with acceptEdits inside C:\FRPDepot; it could in principle edit jobs.mirror.json or EXPECTED_DIVERGENCE. A mirror edit is self-healing (next run rewrites it). An EXPECTED_DIVERGENCE edit would be a silent widening of an exemption — worth a line in the review prompt if the reviewer is ever seen touching Tools\watch, but the both-sides hash pinning limits the blast radius.
7. Running above the disable-flag exit (step 0c) means the check also runs during a deliberate STOP_DADO and during a profile rebuild, which can page mid-rebuild. That is the price of covering `hermes cron remove` while the gateway is stopped. The conservative alternative (inside the gateway-up branch, next to soul-sync) is a one-block move and loses only that coverage.
8. PowerShell wrapper risk: the new block must be inside its own try/catch, exactly like the lane checker and soul sync. A crash there would take down Dado's only auto-recovery. main() also swallows every exception and returns non-zero rather than raising.
9. Overlap with two other open findings. The "budget exhaustion should speak" half of the GLA finding and this tool's `finished_its_budget` branch both observe the same event — deliberately, mine only writes a receipt so the two cannot double-page. Coordinate before both land. Likewise, do not let this tool grow into the delivery-ledger or executions.db work; it reads jobs.json, the scripts dir and config.yaml, nothing else.
10. Secrets: verified the live config.yaml contains no token/secret/api_key/password values, and the tool never copies config.yaml anyway — it only reports differing key PATHS. Deliver targets (`telegram:891365639`) already appear throughout the committed repo, so jobs.mirror.json introduces no new exposure.


==============================================================================
## gla-monitors
==============================================================================

**Finding:** All three GLA sync-ready monitors burned their full 144-run budget without ever alerting, then self-disabled — no ready flag, no error flag, and nothing told Rachad the watch had ended (DADO)

**Verdict:** fix-needed  |  **Effort:** medium  |  **Needs hermes patch:** True  |  **Needs fingerprints note:** True

### Root cause

The finding is CORRECT in every measured particular, and the defect is WORSE than reported. There are two independent bugs, at two layers.

=== BUG 1 (Dado scripts): the monitors' primary terminal condition was UNREACHABLE BY CONSTRUCTION. ===

What they watched for. `C:\FRPDepot\Dado\Tools\watch\gla_sync_target_monitor.py::run_monitor` (and the byte-identical inline logic in `gla_sync_ready_monitor.py::main`) reads the variation read-only via `woocommerce_shipping_policy_tool.read_target` and returns 0 silently unless one of three things is true:
  a) `record["sku"] != expected_sku`      -> "identity_changed"  (terminal, prints)
  b) `record["shipping_class"] != ""`     -> "shipping_class_changed" (terminal, prints)
  c) exactly one `_wc_gla_sync_status` meta entry whose `value == "synced"` -> "ready" (terminal, prints)
Everything else is `return 0` with zero stdout. This is the primary condition (c): the whole point of the watch.

Why (c) could never fire. The value they compare against is confirmed to be the literal string. I recomputed the tool's own canonical-JSON digests:
    sha256(json.dumps("synced", sort_keys=True, separators=(',',':'), ensure_ascii=False)) = bed425acaecb0b9f4dd17f2f763e28b52f027d43feb0265137f49c86bb875c8c = GLA_STAGED_VALUE_SHA256
    sha256(... "pending" ...)                                                              = 12adac54ac6f7140109391b670b3cbcd51f083d8f5ee62ce26857c794ed67d36 = GLA_TRANSIENT_VALUE_SHA256
so the tool's `settled_baseline` == value "synced" and `pending_baseline` == value "pending". The comparison in the monitor is semantically right; the WAIT is what is impossible. `woocommerce_shipping_policy_tool.py`'s own module docstring (the "SCHEMA 4 (2026-08-10) -- THE PENDING BASELINE" section) records the deadlock, reproduced live on the exact variation these monitors watched:
    "...reproduced live on /products/1455/variations/1457: the entry sits at the PENDING digest and stays there -- monitors saw it long after the 90-second transient window... An untouched variation can rest at that value indefinitely, because nothing is going to move it until the product is updated."
A read-only monitor cannot update the product. So condition (c) required an event that the monitor's own read-only nature guaranteed would not happen. A budget of 144, 14400, or `repeat.times: null` makes no difference whatsoever.

Three independent live proofs that "synced" never arrived:
  1. `20_Working\catalog_shipping_policy\schema4_live_commit_1457_result.json` — the successful assignment on 1457 records `"baseline_mode": "pending_baseline"`, `"targets_converged": 0`, `"pending_settled_confirmed_targets": 0`, `"final_state": "exact_staged_pending_state"`. Still "pending" even AFTER the write.
  2. `freight_schema5_reconcile_and_stage_result_20260811T224404Z.json` (2026-08-11T22:51Z) — 1409 sits in a 13-target plan with `baseline_modes {absent:0, settled_baseline:0, pending_baseline:13}`; 1412 in an 11-target plan with `{absent:0, settled_baseline:0, pending_baseline:11}`. Still "pending" ~50h after the monitors were created.
  3. The tool docstring above, written 2026-08-10 — one day INTO the 24h run, while all three monitors were still ticking. The premise was known dead and nobody turned them off.

The premise was also formally VOIDED while they ran. The monitors existed to satisfy schema 3, which refused to stage a plan whose baseline was not settled. Schema 4 (2026-08-10) added `pending_baseline` and removed that precondition entirely; schema 5 (2026-08-11) superseded it again. From 2026-08-10 the answer to "is 1457 ready for a shipping-class plan?" was YES regardless of GLA status — and the monitors kept silently waiting for a different answer.

The one branch that WAS reachable fired ~20 minutes too late. Branch (b) became true for 1457: plan `20260811T032017Z_shipping_class_assign_2546a31f414cc049` is `COMMITTED_AND_VERIFIED` with `shipping_class: freight-quote-required`, staged 2026-08-11T03:20:17Z = 2026-08-10 23:20:17 EDT. Job 672965791e21's last tick was 2026-08-10T23:00:09 EDT with a ~11-minute observed period; its next two ticks (~23:11, ~23:22) would have caught it. The budget expired one tick short of the only alert these monitors were ever capable of delivering.

=== BUG 2 (hermes runtime, shared install): budget exhaustion is structurally silent. ===
`cron/jobs.py::mark_job_run`, the repeat-limit branch, is the only place a finite recurring job dies:
        if times is not None and times > 0 and completed >= times:
            job["enabled"] = False
            job["state"] = "completed"
            job["next_run_at"] = None
            save_jobs(jobs)
            return
`last_status` is left holding whatever the FINAL run said — for a silent watcher, "ok" — and the function emits no log line, no delivery, no marker. `_prune_completed_oneshots` (cron/jobs.py, docstring "Only one-shot (`schedule.kind == "once"`) records") never touches recurring records, so the corpse sits in jobs.json forever reading `last_status: "ok"`, `state: "completed"`. That is the green-corpse shape the finding names, and it is a runtime-layer defect: the runtime is the only component that knows a budget ran out.

Note the runtime already has the right instinct one function away: `cron/jobs.py::_write_wedged_oneshot_diagnostic` — "a silent removal leaves the user with no output, no error, and no job record" — but it only covers wedged one-shots.

=== NOT bugs (checked and cleared) ===
- The suggested fix's option "re-arm with `repeat.times: null`" would be actively WRONG here: unbounded polling of an unreachable condition is a job that is silent forever instead of silent for 24h.
- Profile/repo drift exists but is cosmetic and dissolves with retirement: the profile copies of the two manway wrappers are NOT byte-identical to their repo namesakes (profile 414/433 bytes vs repo 344/357). The profile copies do `sys.path.insert(0, r"C:\FRPDepot\Dado\Tools\watch")` then `from gla_sync_ready_manway_monitor import run_monitor` — a self-named import that resolves to the REPO file, which re-exports `run_monitor` from `gla_sync_target_monitor`. It works, but it means the scheduler's script sandbox (`cron/scheduler.py`, "Blocked: script path resolves outside the scripts directory") is satisfied by a 15-line shim while the real logic is pulled from outside the scripts dir at runtime. `gla_sync_target_monitor.py` is not in the profile scripts dir at all. `gla_sync_ready_monitor.py` IS byte-identical repo vs profile (verified by diff).
- Blast radius of the runtime fix, measured across both live profiles: the ONLY finite-budget recurring jobs on this box are the three dead GLA monitors (dado) and one paused `a job on the neighbouring profile-resume` (aze, cron, times=52, completed=20, enabled=false, state=paused, deliver=telegram:891365639). The patch therefore adds at most ONE future message on the whole machine.

### Plan

Two items. Item A is Dado-side and is not optional — the three jobs are already terminal, so the runtime patch cannot help them retroactively; it only prevents the next one. Item B is the structural half.

Do Item A first (it is pure cleanup and needs no restart). Item B lands on the SAME gateway restart as the two runtime fixes already staged this session — do not spend a second restart on it.

================================================================
ITEM A — RETIRE THE THREE MONITORS. DO NOT RE-ARM THEM.
================================================================

A1. Remove the three terminal job records (they are `enabled:false, state:"completed"`, so they cannot fire; but `_prune_completed_oneshots` will never remove them and they will read as three green successes in every `cron list` forever):

    "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe" -m hermes_cli -p dado cron remove 672965791e21
    "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe" -m hermes_cli -p dado cron remove 29e7b92ac084
    "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe" -m hermes_cli -p dado cron remove acddc3db84aa

    (or `hermes -p dado cron remove <id>` if the launcher is on PATH — `hermes_cli/cron.py` maps `remove|rm|delete` to `_job_action("remove", ...)`, which takes the same cross-process `_jobs_lock()` the live gateway uses, so this is safe with the gateway up.)

A2. Delete the now-orphaned scripts. The three job records above are their only callers (`grep -n "gla_sync" profiles\dado\cron\jobs.json` returns exactly lines 366/412/458, the three `"script":` fields):

    profile (outside both repos, no git, no fingerprint):
      C:\Users\TDI-service\AppData\Local\hermes\profiles\dado\scripts\gla_sync_ready_monitor.py
      C:\Users\TDI-service\AppData\Local\hermes\profiles\dado\scripts\gla_sync_ready_manway_monitor.py
      C:\Users\TDI-service\AppData\Local\hermes\profiles\dado\scripts\gla_sync_ready_manway_cover_monitor.py

    repo source of truth (git rm, recoverable):
      C:\FRPDepot\Dado\Tools\watch\gla_sync_ready_monitor.py
      C:\FRPDepot\Dado\Tools\watch\gla_sync_ready_manway_monitor.py
      C:\FRPDepot\Dado\Tools\watch\gla_sync_ready_manway_cover_monitor.py
      C:\FRPDepot\Dado\Tools\watch\gla_sync_target_monitor.py     <- shared body, no other importer

    Verify no stragglers before deleting: `grep -rn "gla_sync_target_monitor\|gla_sync_ready" C:\FRPDepot --include=*.py --include=*.ps1 --include=*.json` should return only these files plus the jobs.json entries removed in A1.

A3. Leave the record of WHY the watch ended — this is the finding's actual complaint, and a bare `git rm` does not answer it. New file, next to the rest of the GLA evidence:

    C:\FRPDepot\Dado\20_Working\catalog_shipping_policy\gla_sync_ready_monitors_retired_20260812.md
    ---
    # GLA sync-ready monitors retired — 2026-08-12

    Retired: cron jobs 672965791e21 / 29e7b92ac084 / acddc3db84aa and scripts
    gla_sync_ready_monitor.py, gla_sync_ready_manway_monitor.py,
    gla_sync_ready_manway_cover_monitor.py, gla_sync_target_monitor.py.

    ## Why, not just that
    They waited for `_wc_gla_sync_status == "synced"` on variations 1457 / 1409 / 1412.
    That condition was unreachable: a read-only observer cannot move it, and
    woocommerce_shipping_policy_tool.py's own SCHEMA 4 notes record the deadlock
    reproduced on 1457 — "the entry sits at the PENDING digest and stays there...
    An untouched variation can rest at that value indefinitely, because nothing is
    going to move it until the product is updated."

    They also became POINTLESS while they ran. Schema 3 refused to stage a plan on
    an unsettled baseline; schema 4 (2026-08-10) added `pending_baseline` and
    removed that precondition; schema 5 (2026-08-11) superseded it again. From
    2026-08-10 onward a pending GLA status blocked nothing.

    ## What actually happened to the three targets
    - 1457 / PIDN25150PSI470  — DONE. `freight-quote-required` COMMITTED_AND_VERIFIED,
      plan 20260811T032017Z_shipping_class_assign_2546a31f414cc049, GLA status still
      "pending" through the write (`final_state: exact_staged_pending_state`).
      This landed ~20 min after the monitor's last tick; had the budget not expired
      the monitor's `shipping_class_changed` branch would have caught it.
    - 1409 / MWDN50005PSI411  — still blank. In staged-not-committed plan
      20260811T224854Z_shipping_class_assign_ba13d0f234dfa1b4 (13 targets),
      expires 2026-08-12T22:48:54Z, awaiting Rachad's `APPROVED`.
    - 1412 / MWCDN50015PSI411 — still blank. In staged-not-committed plan
      20260811T225121Z_shipping_class_assign_7ee3f724073e488e (11 targets),
      expires 2026-08-12T22:51:21Z, awaiting Rachad's `APPROVED`.

    ## Not replaced
    No successor monitor. The GLA status is no longer a gate on anything. What is
    outstanding is a HUMAN decision (approve or let the two plans lapse), not an
    observable event, and a cron job cannot watch for a decision.
    ---

    Commit message for A2+A3:
      "Retire the three GLA sync-ready monitors — they watched an unreachable condition

       All three burned 144/144 ten-minute runs in silence and self-disabled with
       last_status 'ok'. Their primary terminal condition required
       _wc_gla_sync_status to reach "synced" on an untouched variation, which the
       shipping-policy tool's own schema-4 notes record as a permanent pending
       state. Schema 4 then removed the precondition they existed to satisfy.
       1457 is assigned; 1409/1412 sit in staged plans awaiting approval.
       Retirement rationale recorded in
       20_Working/catalog_shipping_policy/gla_sync_ready_monitors_retired_20260812.md."

A4. Pay the missing terminal message once, by hand. Do NOT build a script for it — this is the one-off debt, not a new watcher. Include this verbatim in the session report to Rachad:

    "The three GLA sync-ready monitors (1457 / 1409 / 1412) ended on 2026-08-10 at
     23:00-23:10 without ever speaking. They could not have: they were waiting for
     the Google sync flag to reach 'synced', which an untouched variation never
     does. Retired.
     Where the three targets actually stand:
       - Pipe 1457: freight class assigned and verified 2026-08-10 ~23:20. Done.
       - Manway 1409 and Manway Cover 1412: still blank. They are inside two staged,
         NOT-committed plans that EXPIRE 2026-08-12 ~18:48 and ~18:51 EDT. If you
         want them assigned, they need your APPROVED before then; otherwise they
         lapse and have to be re-staged."

A5. NOT recommended: no replacement monitor. Say so out loud rather than silently omitting it. The only thing outstanding is Rachad's approval on two plans with a hard expiry — a decision, not an observable event. If he later asks for an expiry nudge, that is a new, separately-scoped one-shot, and it must be created with the `once` kind (which does not hit the silent-exhaustion path at all).

================================================================
ITEM B — MAKE BUDGET EXHAUSTION SPEAK (shared hermes install)
================================================================
*** THIS TOUCHES THE SHARED INSTALL. It MUST be captured as a patch file under
C:\AgentTeam\Sync\patches\ AND registered in the $patches array of
C:\AgentTeam\Sync\APPLY_HERMES_SAFETY_PATCHES.ps1, or the next `hermes update`
silently reverts it while -VerifyOnly still reports all-clear. ***
It also edits under C:\AgentTeam\Sync, which is guard-fingerprinted -> dated note
required in the TDI-side fingerprints note.

B1. `cron/jobs.py` — report exhaustion instead of swallowing it.

  (a) signature, currently:
        def mark_job_run(job_id: str, success: bool, error: Optional[str] = None,
                         delivery_error: Optional[str] = None):
      becomes:
        def mark_job_run(job_id: str, success: bool, error: Optional[str] = None,
                         delivery_error: Optional[str] = None) -> bool:
      and add to the docstring:
        """...
        Returns True only when THIS run exhausted a finite ``repeat.times``
        budget on a RECURRING schedule — the caller announces it. False on
        every ordinary run, on unbounded jobs, and on one-shots.
        """

  (b) the repeat-limit branch (`kind` is already bound a few lines above as
      `kind = job.get("schedule", {}).get("kind")`). Replace the bare `return`:

        -                        job["enabled"] = False
        -                        job["state"] = "completed"
        -                        job["next_run_at"] = None
        -                        save_jobs(jobs)
        -                        return
        +                        job["enabled"] = False
        +                        job["state"] = "completed"
        +                        job["next_run_at"] = None
        +                        save_jobs(jobs)
        +                        # A RECURRING job that spends a finite budget dies
        +                        # here with last_status still holding whatever its
        +                        # FINAL run said — for a silent watcher, "ok" — plus
        +                        # enabled=false and state="completed". Every surface
        +                        # (jobs.json, `cron list`, the dashboards) then reads
        +                        # the corpse as a success, and _prune_completed_oneshots
        +                        # never removes it because it is not a one-shot.
        +                        # Measured 2026-08-10: three FRP Depot
        +                        # "gla-sync-ready-*" monitors each ran 144/144
        +                        # ten-minute ticks, never alerted, self-disabled, and
        +                        # nobody learned the watch had ended. Tell the caller
        +                        # so it can say so. One-shots are excluded on purpose:
        +                        # reaching times=1 IS their normal completion.
        +                        return kind in {"cron", "interval"}

  (c) make the other two exits explicit (both are currently falsy already, so this
      is clarity, not behaviour):
        - the `save_jobs(jobs); return` at the end of the matched-job block -> `return False`
        - the trailing `logger.warning("mark_job_run: job_id %s not found, skipping save", job_id)`
          -> add `return False` after it.

B2. `cron/scheduler.py` — import `get_job`:

        -from cron.jobs import get_due_jobs, mark_job_run, save_job_output, advance_next_runs, claim_dispatch, heartbeat_run_claim
        +from cron.jobs import get_due_jobs, mark_job_run, save_job_output, advance_next_runs, claim_dispatch, heartbeat_run_claim, get_job

B3. `cron/scheduler.py` — new helper, placed immediately after `_deliver_result`
    (symbol-anchored: insert before `def _run_job_script(`):

        def _announce_budget_exhausted(job: dict, *, adapters=None, loop=None) -> None:
            """Say out loud that a recurring watch has spent its run budget.

            Fires exactly ONCE, on the terminal tick, and never on a healthy run —
            silence-when-clean governs repeated healthy output, not the one state
            change that ends the job. A monitor allowed to end quietly is
            indistinguishable from one that succeeded.

            The notice is saved to the output directory BEFORE it is delivered, so
            a dropped send still leaves the record on disk. ``mark_job_run`` has
            already committed the job record, so a delivery failure here is logged
            and deliberately NOT written back to ``last_delivery_error`` — that
            field belongs to the run's own output, not to this notice.

            Best-effort: announcing must never fail the run it reports on.
            """
            try:
                record = get_job(job.get("id", "")) or job
                repeat = record.get("repeat") or {}
                name = record.get("name") or record.get("id") or "?"
                notice = (
                    f"Watch ended: cron job '{name}' has used its whole run budget "
                    f"({repeat.get('completed', '?')}/{repeat.get('times', '?')} runs, "
                    f"{record.get('schedule_display') or 'recurring'}) and is now "
                    "disabled.\n\n"
                    f"Last run: {record.get('last_run_at') or 'never'} "
                    f"(status: {record.get('last_status') or 'unknown'}).\n\n"
                    "It will NOT run again and nothing is watching for it any more. "
                    "If the thing it was watching still matters, re-create the job."
                )
                save_job_output(record.get("id", ""), notice)
                send_error = _deliver_result(record, notice, adapters=adapters, loop=loop)
                if send_error:
                    logger.error(
                        "Job '%s': budget-exhausted notice could not be delivered: %s",
                        name, send_error,
                    )
            except Exception as e:  # noqa: BLE001 — a notice must never break a run
                logger.error(
                    "Job '%s': failed to announce budget exhaustion: %s",
                    job.get("name", job.get("id", "?")), e,
                )

B4. `cron/scheduler.py::run_one_job` — capture and act on the signal. Two edits:

        -        if not _consume_interrupted_flag(job["id"]):
        -            mark_job_run(job["id"], success, error, delivery_error=delivery_error)
        +        budget_exhausted = False
        +        if not _consume_interrupted_flag(job["id"]):
        +            budget_exhausted = mark_job_run(
        +                job["id"], success, error, delivery_error=delivery_error
        +            )

    and, at the tail of the same try block:

                 finish_execution(
                     execution_id,
                     success=success,
                     error=error,
                     delivery_outcome=delivery_outcome,
                 )
        +        # After finish_execution on purpose: the ledger row for THIS run is
        +        # already closed, so nothing about the notice can alter it.
        +        if budget_exhausted:
        +            _announce_budget_exhausted(job, adapters=adapters, loop=loop)
                 return True

    Ordering note for the reviewer (the one non-obvious point): this runs after the
    deferred-agent teardown that #58720 introduced. It is safe here because
    (i) every finite-budget watcher on this box is `no_agent`, so `_deferred_agents`
    is empty and no teardown occurs at all; (ii) `_deliver_result` sends through
    `tools.send_message_tool._send_to_platform` / the gateway adapters and never
    touches the cron agent's client; (iii) `cleanup_stale_async_clients()` reaps only
    clients bound to already-closed loops, and this send acquires its own. The
    `save_job_output` call ordering is the belt-and-braces: the record exists on disk
    regardless of the send.

    Delivery semantics fall out of the job's own config, which is correct:
    `deliver: "local"` -> `_deliver_result` returns None and the notice lives only in
    the output dir; `deliver: "origin"`/`telegram:*` -> Rachad gets one message. It
    also composes with this session's already-applied `_platform_supports_push()`
    patch, so a non-push origin diverts to the home channel rather than vanishing.

B5. Capture and register the patch:
    - Produce `C:\AgentTeam\Sync\patches\hermes-cron-budget-exhaustion-speaks-20260812.patch`
      containing cron/jobs.py, cron/scheduler.py AND the new test file (matching the
      house pattern — the two 2026-08-11 cron patches both carry "7 regression tests
      ride along in the patch").
    - In `C:\AgentTeam\Sync\APPLY_HERMES_SAFETY_PATCHES.ps1`, add the variable next to
      `$cronOneshotLiveRunPatch` with a comment in the established style:

        # 2026-08-12: a recurring cron job with a finite repeat.times ended in total
        # silence. mark_job_run's repeat-limit branch set enabled=false /
        # state="completed" / next_run_at=null and returned — leaving last_status
        # holding whatever the FINAL run said, which for a silent watcher is "ok".
        # _prune_completed_oneshots never removes the record (not a one-shot), so the
        # corpse reads as a green success in cron list forever. Measured: three FRP
        # Depot gla-sync-ready-* monitors each ran 144/144 ten-minute ticks, never
        # alerted, self-disabled, and nobody learned the watch had ended. mark_job_run
        # now returns True on recurring-budget exhaustion and run_one_job announces it
        # once through the job's own deliver target, after saving it to the output
        # directory. One-shots and unbounded jobs are unaffected. 7 regression tests
        # ride along in the patch.
        $cronBudgetExhaustionPatch = Join-Path $PSScriptRoot "patches\hermes-cron-budget-exhaustion-speaks-20260812.patch"

      and append `$cronBudgetExhaustionPatch` as the last element of `$patches`
      (after `$cronOneshotLiveRunPatch`).

B6. Dated note in the TDI-side fingerprints note (required — Sync
    is fingerprinted at guard_round.ps1:105). One entry, dated 2026-08-12, naming:
    the two Sync paths touched (patches\hermes-cron-budget-exhaustion-speaks-20260812.patch,
    APPLY_HERMES_SAFETY_PATCHES.ps1), why (recurring-budget exhaustion was silent;
    three Dado monitors died green), and that the patch file is the durable copy so
    `hermes update` cannot revert it behind a passing -VerifyOnly.

### Tests

NEW FILE: C:\Users\TDI-service\AppData\Local\hermes\hermes-agent\tests\cron\test_repeat_budget_exhaustion_speaks.py
House style, matching tests/cron/test_oneshot_live_run_not_deleted.py exactly: real store against a temp HERMES_HOME via `monkeypatch.setenv("HERMES_HOME", str(tmp_path))`, no mocks for anything that touches a file. The only monkeypatch permitted is on `_deliver_result`, which is a network boundary, not file-touching.

    import pytest

    @pytest.fixture
    def temp_home(tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        yield tmp_path

1) test_recurring_budget_exhaustion_is_reported
   create_job(prompt="x", schedule="10m"...) -> use a RECURRING schedule (kind
   "interval"), repeat=2. Call mark_job_run(jid, True) twice.
   assert first is False; assert second is True.
   assert get_job(jid)["enabled"] is False and ["state"] == "completed".
   FAILS WITHOUT THE FIX: mark_job_run returns None both times, so `second is True`
   fails on the exact line that carries the whole finding.

2) test_the_gla_monitor_shape_is_what_triggers_it   <- the finding, executable
   Interval job, repeat=144, every run success=True with no error (a silent
   no_agent watcher). Loop mark_job_run 144 times collecting returns.
   assert returns[:143] == [False]*143 and returns[143] is True.
   assert get_job(jid)["last_status"] == "ok"           # the green corpse
   assert get_job(jid)["repeat"] == {"times": 144, "completed": 144}
   Pins that a job which looks perfectly healthy on every surface is precisely the
   case that must now speak.

3) test_a_run_inside_the_budget_is_not_reported
   repeat=5, one run -> False; get_job(...)["enabled"] is True, ["state"] ==
   "scheduled". Guards against the notice firing mid-life.

4) test_unbounded_recurring_job_never_reports
   repeat=None (the shape of every standing watcher: dado-inbox-watch,
   dado-stall-tripwire, packing-order-monitor, a 60-second job on the neighbouring profile). 200 calls,
   assert not any(...). This is the silence-when-clean guard: the fix must be
   incapable of making a permanent watcher chatty.

5) test_one_shot_completion_is_not_a_budget_exhaustion
   schedule="30m" (kind "once"), repeat=1, claim_dispatch then mark_job_run.
   assert result is False. A one-shot reaching times=1 is its normal terminal
   state and already visible; announcing it would be noise on every reminder.

6) test_notice_is_written_to_the_output_directory_and_names_the_job
   Build a real exhausted interval job with deliver="local", then call
   cron.scheduler._announce_budget_exhausted(job). Read the newest .md under
   HERMES_HOME/cron/output/<jid>/ off the real filesystem and assert it contains
   the job name, "2/2 runs", and "will NOT run again". No mocking of the write —
   this is the on-disk half that survives a dropped send.

7) test_notice_delivery_failure_neither_raises_nor_touches_last_delivery_error
   monkeypatch.setattr(scheduler, "_deliver_result", lambda *a, **k: "boom").
   Call _announce_budget_exhausted(job); assert it returns None without raising,
   and assert get_job(jid)["last_delivery_error"] is unchanged from what
   mark_job_run wrote. Pins that the notice cannot corrupt the run's own record.

8) test_announcing_cannot_break_the_run
   monkeypatch _deliver_result to raise RuntimeError; assert
   _announce_budget_exhausted returns None without propagating.

HOW TO PROVE THEY FAIL WITHOUT THE FIX
Stash only the two source files, keep the test file:
    cd %LOCALAPPDATA%\hermes\hermes-agent
    git stash push cron/jobs.py cron/scheduler.py
    venv\Scripts\python.exe -m pytest tests/cron/test_repeat_budget_exhaustion_speaks.py -q
  Expect: 1, 2, 6, 7, 8 fail (1 and 2 on the returned-None assertion; 6/7/8 on
  AttributeError: module 'cron.scheduler' has no attribute
  '_announce_budget_exhausted'). Tests 3, 4, 5 PASS unpatched — deliberately, they
  are the no-regression guards, not the regression.
    git stash pop
    venv\Scripts\python.exe -m pytest tests/cron/test_repeat_budget_exhaustion_speaks.py -q   # 8 passed

FULL SUITE (mark_job_run's return type changed, so run the neighbours):
    venv\Scripts\python.exe -m pytest tests/cron -q
Expect no new failures. tests/cron/test_jobs.py, test_scheduler.py,
test_run_one_job.py, test_execution_ledger.py, test_oneshot_live_run_not_deleted.py
and test_delivery_push_capability.py all call mark_job_run for its side effects and
ignore the return, so adding one is additive.

NO NEW DADO-SIDE TESTS. Item A only deletes code; C:\FRPDepot\Dado\Tools\watch\ has
no test_gla_* file to update (verified: the watch dir's tests are
test_dado_daily_banking_review / _followup_digest / _heartbeat_check / _lane_health /
_soul_sync / _job_runner / _receipt_format / _stall_tripwire / _watch_delivery /
_zoho_session_keepalive — none reference the GLA monitors).

### Verification

ITEM A — verify without touching anything live beyond the three dead records.
A-1. Before: `hermes -p dado cron list` shows 13 jobs including three
     `gla-sync-ready-*` rows reading `ok` / disabled. After the three removes: 10
     jobs, none named gla-sync-ready-*. Confirm on disk too:
       grep -n "gla_sync" "%LOCALAPPDATA%\hermes\profiles\dado\cron\jobs.json"   -> no output
     (before the change this returns lines 366, 412, 458).
A-2. Confirm nothing else referenced the deleted scripts:
       grep -rn "gla_sync_target_monitor\|gla_sync_ready" C:\FRPDepot --include=*.py --include=*.ps1
     -> only the retirement note. If dado_gateway_watchdog.ps1 or job_runner.py
     appear, STOP and re-scope.
A-3. Confirm no live behaviour changed: the remaining 10 dado jobs keep their ids
     and next_run_at values; `packing-order-monitor` (89d95c692495) is untouched.
A-4. Sanity-check the two target facts before telling Rachad anything (both are
     local file reads, zero WooCommerce calls):
       python -c "import json;d=json.load(open(r'C:\FRPDepot\Dado\20_Working\catalog_shipping_policy\schema4_live_commit_1457_result.json'));print(d['status'],d['outcome'][0]['shipping_class'])"
         -> COMMITTED_AND_VERIFIED freight-quote-required
       python -c "import json;d=json.load(open(r'C:\FRPDepot\Dado\20_Working\catalog_shipping_policy\freight_schema5_reconcile_and_stage_result_20260811T224404Z.json'));print(d['status'],[(p['family'],p['expires_utc']) for p in d['plans']])"
         -> STAGED_NOT_COMMITTED with the two expiry timestamps
     Do NOT run the retired monitors or any live WooCommerce read to "confirm" —
     the on-disk reconciliation of 2026-08-11T22:44Z is the authoritative record and
     a fresh read buys nothing.

ITEM B — verify the runtime fix.
B-1. Static, before any restart:
       cd %LOCALAPPDATA%\hermes\hermes-agent
       venv\Scripts\python.exe -m pytest tests/cron -q
       git diff --stat cron/jobs.py cron/scheduler.py     # exactly the edits above
B-2. Prove the patch registration actually protects the change (this is the step
     that was missed on the discord patch and is why the rule exists):
       powershell -File C:\AgentTeam\Sync\APPLY_HERMES_SAFETY_PATCHES.ps1 -VerifyOnly
     must name the new patch and report present. Then prove it is reversible/
     re-appliable without the live tree:
       git -C %LOCALAPPDATA%\hermes\hermes-agent apply --reverse --check --whitespace=nowarn C:\AgentTeam\Sync\patches\hermes-cron-budget-exhaustion-speaks-20260812.patch
     (exit 0 = the patch exactly describes what is in the tree). If this fails the
     patch file does not match the working tree and `hermes update` WILL silently
     revert the fix while -VerifyOnly still says all-clear.
B-3. Restart. The two runtime fixes already staged this session
     (_platform_supports_push, claim_dispatch liveness) are also not live yet —
     land all three on ONE restart. A restart kills in-flight turns, so do it
     deliberately, not opportunistically.
B-4. LIVE SMOKE TEST that cannot reach Rachad and cannot touch a real watcher:
     create a throwaway file
       %LOCALAPPDATA%\hermes\profiles\dado\scripts\_tmp_budget_probe.py
     containing exactly:
       import sys
       sys.exit(0)
     then:
       hermes -p dado cron create --name tmp-budget-probe --script _tmp_budget_probe.py --no-agent --schedule "1m" --repeat 2 --deliver local
     Wait ~2-3 minutes, then:
       hermes -p dado cron list          -> tmp-budget-probe shows enabled=false,
                                            state=completed, repeat 2/2
       dir %LOCALAPPDATA%\hermes\profiles\dado\cron\output\<jid>\
       -> THREE files: two "**Status:** silent (empty output)" run docs and one
          notice containing "Watch ended", "2/2 runs", "will NOT run again".
     That third file is the entire fix, demonstrated. Because deliver=local,
     `_deliver_result` returns None and NOTHING is sent to Telegram.
     Then clean up:
       hermes -p dado cron remove <jid>
       del %LOCALAPPDATA%\hermes\profiles\dado\scripts\_tmp_budget_probe.py
     (The temp script deliberately lives in the profile scripts dir only for the
     duration of the probe — it is not a repo artifact and must not be left behind,
     or it becomes new drift of exactly the kind this audit is cataloguing.)
B-5. Prove no chatter regression on the live tree: after the restart, watch one
     full hour of the gateway log. dado-inbox-watch, dado-stall-tripwire,
     dado-job-watch, dado-zoho-session-watch and packing-order-monitor all carry
     `repeat.times: null` and must produce ZERO "Watch ended" lines. Aze's
     `a 60-second job on the neighbouring profile` (60s, unbounded) likewise. If any healthy watcher emits a
     notice, the `times is not None` guard has been broken — revert immediately.

### Risks

WHAT COULD BREAK

1. mark_job_run's return type changes None -> bool. Additive: all three call sites
   (cron/scheduler.py lines ~390, ~4103, ~4135) currently discard the result, and
   the cron test suite calls it for side effects only. The real risk is a FUTURE
   caller writing `if not mark_job_run(...)` and misreading False as failure — hence
   the explicit docstring line. Mitigated by tests 3/4/5.

2. Over-firing. If the `kind in {"cron","interval"}` guard were dropped, every
   one-shot reminder on both profiles would announce its own completion — a large,
   immediate noise regression that would get the whole mechanism tuned out, which is
   exactly the failure mode the house rules call a defect. Test 5 pins it. If the
   `times is not None and times > 0` guard were loosened, unbounded watchers would
   announce; test 4 pins that.

3. Blast radius today is ONE message, measured not assumed. Across both live
   profiles the only finite-budget recurring jobs are the three dead GLA monitors
   (dado, already terminal — the patch cannot and will not announce them
   retroactively) and `a job on the neighbouring profile-resume` (f617b7d64efe, cron, times=52,
   completed=20, enabled=false, state=paused, deliver=telegram:891365639). If that
   job is ever resumed and reaches 52 it emits exactly one notice. That is correct
   behaviour, but flag it to whoever owns e10277 so the message is not a surprise.

4. Delivery-after-teardown ordering (the one subtle point). The announcement calls
   `_deliver_result` after the deferred-agent teardown that #58720 introduced. Safe
   because every finite-budget watcher here is `no_agent` (empty `_deferred_agents`,
   no teardown at all), `_deliver_result` goes through
   `tools.send_message_tool._send_to_platform` and never touches the agent's client,
   and `cleanup_stale_async_clients()` reaps only closed-loop clients. If a future
   change puts a finite budget on an LLM cron job, re-examine this. The
   `save_job_output`-before-send ordering means the record survives a dropped send
   regardless.

5. This notice inherits the delivery weakness catalogued in the "cron delivery
   failures are invisible" and B-08 findings: `_deliver_result` has no retry and no
   ledger. A dropped notice is logged and lands in the output dir but is not
   re-emitted. Deliberately NOT solved here — that is another finding's fix, and
   coupling them would make both harder to land. Say so rather than implying the
   notice is guaranteed.

6. Patch-registration is the load-bearing step, not the code. If B5/B6 are skipped,
   the next `hermes update` reverts jobs.py and scheduler.py while
   APPLY_HERMES_SAFETY_PATCHES.ps1 -VerifyOnly still prints all-clear — the precise
   trap the discord-prompt-timeout comment in that script documents.

WHAT MUST NOT CHANGE

- The four state writes inside the repeat-limit branch (`enabled=False`,
  `state="completed"`, `next_run_at=None`, `save_jobs(jobs)`) stay byte-for-byte.
  Only the trailing `return` changes. The comment above them records a prior fix
  (retain the record instead of popping it, so last_status/last_delivery_error
  survive) — do not disturb it.
- `_prune_completed_oneshots` stays one-shot-only. Do not "tidy up" recurring
  completed records by making them prunable: the retained record IS the evidence a
  watch existed, and deleting it would recreate the invisibility this fix removes.
- One-shot semantics (`claim_dispatch`, `_write_wedged_oneshot_diagnostic`, the
  at-most-times guarantee) are untouched, including this session's not-yet-live
  claim_dispatch liveness fix. Do not merge the two code paths.
- Dado's data wall. Item B edits C:\AgentTeam\Sync — that is the ORCHESTRATOR
  writing the shared patch registry, which is where the house keeps every shared-
  hermes patch regardless of which agent needed it (the discord adapter patch that
  Dado's config opts into is registered there today). It is NOT Dado reading
  C:\AgentTeam, and nothing in Item A or B adds a new cross-tree read. The only
  sanctioned crossing remains the one watchdog heartbeat call.
- Do not re-arm the three monitors, and specifically do not follow the finding's
  own suggestion of `repeat.times: null` for them. Unbounded polling of a condition
  that cannot occur is a job that is silent forever instead of for 24 hours — a
  strictly worse version of the bug being fixed.
- Do not run the retired monitor scripts to "check current state". They WRITE
  `*_ready.flag` / `*_error.flag` into
  C:\FRPDepot\Dado\20_Working\catalog_shipping_policy\ on any terminal outcome, and
  1457's shipping class is now non-blank — so a single run would create a
  `gla_sync_variation_1457_ready.flag` containing `"status": "shipping_class_changed"`
  and permanently muddy the evidence trail this analysis rests on. All the facts are
  already on disk.
- The two staged plans (ba13d0f234dfa1b4 for Manway/1409, 7ee3f724073e488e for
  Manway Cover/1412) must not be committed, approved, extended or re-staged as part
  of this work. They are Rachad's approval decision and they carry hard expiries of
  2026-08-12T22:48:54Z and 22:51:21Z. Report the deadline; do not act on it.


==============================================================================
## stall-tripwire-gateway-death
==============================================================================

**Finding:** dado-stall-tripwire still has no gateway-death / orphaned-turn check — the 2026-08-04 finding was proposed and never built, while the gateway restarted 4 times in the last 24 hours

**Verdict:** fix-needed  |  **Effort:** medium  |  **Needs hermes patch:** False  |  **Needs fingerprints note:** False

### Root cause

CONFIRMED, and it recurred TODAY — I found a second, undocumented instance while measuring it.

WHY THE TRIPWIRE CANNOT SEE A KILLED TURN (mechanism, in `C:\FRPDepot\Dado\Tools\watch\stall_tripwire.py`):

1. `open_turns()` only ever returns turns that are OPEN — it pops a session on `TURN_END` and otherwise keeps `turns[session]`. A gateway death writes no `Turn ended` line, so the dead turn *looks* open, and `main()` will describe it as "The turn is still active but has produced no reply. Ask her what she is doing" — an instruction to interrogate an agent that has no memory of the request.
2. That window then closes on its own: `open_turns()` does `turns[session] = {...}` on every `TURN_START`, keeping only the newest turn per session (pinned deliberately by the existing test `test_a_later_turn_in_the_same_session_resets_the_clock`). The moment Rachad re-asks — which is exactly what a user does when ignored — the dead turn is overwritten and vanishes. `main()`'s cleanup `for key in [k for k in state if k not in seen]: del state[key]` then deletes its state entry, so it can never be mentioned again.
3. `REALERT_MINUTES = 60` (line 38) covers the gap in between: if the turn was already announced as a stall before the death, the tick after the death is suppressed.
4. Nothing in the file reads `inbound message`, `response ready`, `Starting Hermes Gateway`, `gateway_state.json` or `gateway-starts.log`. Confirmed byte-identical repo/profile copies, sha256[:16] `2114c17d99653ec8`, both 9745 bytes, mtime 2026-07-25 01:10/01:12 — untouched since 11 days BEFORE the 08-04 finding.

THE LIVE RECURRENCE I MEASURED (not in any review file):
- `profiles\dado\logs\gateway.log:2858` — 2026-08-11 12:19:56 inbound discord "Please proceed the PO we just received from SCT. Great a new Sales order..."
- `agent.log:8313` — turn `20260811_093216_9796dde8` opened 12:19:56, and NO `Turn ended` for it ever.
- `gateway.log:2874` — 13:10:54 `gateway.lifecycle_ledger: Previous gateway life (pid=8728, ...) exited UNCLEANLY (no exit path ran — SIGKILL / OOM / VM death)`; new life at 13:10:55. No discord `response ready` between 12:16:54 and 13:24:07.
- `gateway.log:2911` — 13:23:14 Rachad re-sends the IDENTICAL message. That is the "Are you here?" shape, 51 minutes of silence.
- What the tripwire actually said: `cron\output\faaabfa052e9\2026-08-11_12-45-04.md` — "…on one discord reply for 25 min … The turn is still active…" (true at 12:45), then SILENCE at 13:00/13:15/13:30 (REALERT gate, then key deleted). `jobs.json` `last_status: "ok"` throughout.

SCALE, measured over the full retained `gateway.log` (2026-07-22 → now) with a probe I wrote in scratch: 22 gateway starts, 521 inbound vs 508 `response ready`, and exactly 5 events where a lane's last message died with its gateway — 2026-07-22 21:06 telegram, 2026-07-23 12:45 telegram, 2026-08-04 11:28:33 discord "Okay proceed" (the review's own incident), 2026-08-08 11:47:02 telegram (he asked "are you here?" at 11:55:18), 2026-08-11 12:19:56 discord. Rare, real, and every one of them is a message Rachad never got an answer to.

The finding's severity is right and its proposed fix is the right shape. One correction to it: an "inbound with no response within N minutes" rule as stated would fire on healthy long turns (a legitimate 6477s telegram turn ran on 2026-08-11 16:51), so the check must be scoped to the gateway-life boundary, not to a timeout.

### Plan

Everything below is in `C:\FRPDepot` only. No hermes install file is touched, so no Sync patch and no fingerprints note. Fully written and tested in scratch: copy-ready files are at
`C:\Users\TDI-service\AppData\Local\Temp\1\claude\C--\59eccd3b-b1f6-47a1-90c7-d2368efc8daa\scratchpad\proto\stall_tripwire.py` and `...\proto\test_stall_tripwire.py`, with unified diffs at `...\scratchpad\proposed_stall_tripwire.diff` and `...\scratchpad\proposed_test_stall_tripwire.diff`. 30 tests pass (13 pre-existing, unchanged, + 17 new).

APPLY IN THIS ORDER. Step 1 must land before step 2, or the first deliberate STOP_DADO that kills a turn pages Rachad about something he did on purpose.

────────────────────────────────────────────────────────
STEP 1 — make a deliberate stop leave a durable trace (2 one-line edits)

The constraint "must not fire on a DELIBERATE stop" cannot be met with the flag alone, and this is the load-bearing detail: `STOP_DADO.bat` writes `Dado\40_Logs\gateway_disabled.flag`, `START_DADO.bat` **deletes** it, and the cron that runs the tripwire does not tick at all while she is stopped. So by the first run that could notice a killed turn, the only evidence of *why* it was killed is already gone. Also note `STOP_DADO.bat` ends in `Stop-Process -Id $p.ProcessId -Force`, so a deliberate stop produces the same `exited UNCLEANLY` ledger line as a crash — clean-vs-unclean is NOT a usable discriminator. Two writers append to one gitignored ledger (`Dado/40_Logs/` is already covered by `.gitignore:1`, so zero commit churn and nothing enters the nightly bundle).

`C:\FRPDepot\STOP_DADO.bat` — inside the existing single `powershell -Command` string, immediately after the `Set-Content -Path $flag ...` statement and before `hermes -p dado gateway stop`:

    Add-Content -Path 'C:\FRPDepot\Dado\40_Logs\gateway_stops.log' -Value ((Get-Date).ToString('s') + ' stop requested via STOP_DADO.bat') -Encoding utf8;

`C:\FRPDepot\START_DADO.bat` — inside its `if (Test-Path $flag) { ... }` block, BEFORE the `Remove-Item $flag`:

    Add-Content -Path 'C:\FRPDepot\Dado\40_Logs\gateway_stops.log' -Value ((Get-Date).ToString('s') + ' disable flag cleared by START_DADO.bat') -Encoding utf8;

`(Get-Date).ToString('s')` yields `2026-08-11T22:12:13` — the first space-delimited token of the line and exactly what `dt.datetime.fromisoformat` parses. Both edits are needed: the STOP entry is the exact stop instant, the START entry also covers a flag someone created by hand. Either one falling inside the dead life suppresses the alert (see the interval rule below).

────────────────────────────────────────────────────────
STEP 2 — `C:\FRPDepot\Dado\Tools\watch\stall_tripwire.py` (the check itself)

New module constants (beside `STATE_PATH`):

    DISABLE_FLAG_PATH = Path(r"C:\FRPDepot\Dado\40_Logs\gateway_disabled.flag")
    STOP_LEDGER_PATH  = Path(r"C:\FRPDepot\Dado\40_Logs\gateway_stops.log")
    ORPHAN_ALERTS = 1           # a lost message is announced once; repeating adds nothing
    ORPHAN_LOOKBACK_HOURS = 12  # the tail window holds ~a week; older than this is archaeology

New regexes (beside `TURN_START`), all three verified verbatim against the live `agent.log`:

    INBOUND = re.compile(TS + r".*gateway\.run: inbound message: platform=(\S+) user=\S+ chat=(\S+) msg=(.*)")
    RESPONSE_READY = re.compile(TS + r".*gateway\.run: response ready: platform=(\S+) chat=(\S+) ")
    GATEWAY_START = re.compile(TS + r".*gateway\.run: Starting Hermes Gateway")

New symbols, inserted after `open_turns()`:

    def gateway_state_path() -> Path:
        """profiles\\dado\\gateway_state.json - derived from log_path so tests redirect
        both with one patch, and so this can never read another profile's file."""
        return log_path().parent.parent / "gateway_state.json"


    def current_gateway_start(lines: list[str]) -> dt.datetime | None:
        """When the RUNNING gateway life began, or None if it cannot be established."""
        try:
            raw = json.loads(gateway_state_path().read_text(encoding="utf-8", errors="replace"))
            value = raw.get("start_time")
            if isinstance(value, (int, float)) and value > 0:
                when = dt.datetime.fromtimestamp(float(value) / 100.0).replace(microsecond=0)
                # A start in the future means the field is not what we think it is
                # (wrong unit, clock skew). Believing it would silence the open-turn
                # check for every turn on the box, so refuse it rather than degrade.
                if when <= dt.datetime.now():
                    return when
        except (OSError, json.JSONDecodeError, AttributeError, ValueError, OverflowError):
            pass
        starts = gateway_starts(lines)
        return starts[-1] if starts else None


    def gateway_starts(lines: list[str]) -> list[dt.datetime]:
        """Every gateway birth visible in the window, oldest first."""
        found = []
        for line in lines:
            match = GATEWAY_START.search(line)
            if match:
                found.append(parse_ts(match.group(1)))
        return found


    def deliberate_stops() -> list[dt.datetime]:
        try:
            text = STOP_LEDGER_PATH.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        stamps = []
        for line in text.splitlines():
            head = line.strip().split(" ", 1)[0]
            try:
                stamps.append(dt.datetime.fromisoformat(head).replace(tzinfo=None))
            except ValueError:
                continue  # a comment or a hand-written line is not an error
        return stamps


    def orphaned_messages(lines: list[str], started: dt.datetime | None) -> list[dict]:
        if started is None:
            return []
        life = 0
        waiting: dict[tuple[str, str], list[tuple[dt.datetime, str, int]]] = {}
        for line in lines:
            if GATEWAY_START.search(line):
                life += 1
                continue
            inbound = INBOUND.search(line)
            if inbound:
                lane = (inbound.group(2), inbound.group(3))
                text = inbound.group(4).split(" reply_to_id=")[0].strip().strip("'\"")
                waiting.setdefault(lane, []).append((parse_ts(inbound.group(1)), text, life))
                continue
            ready = RESPONSE_READY.search(line)
            if ready:
                lane = (ready.group(2), ready.group(3))
                waiting[lane] = [item for item in waiting.get(lane, []) if item[2] != life]
        starts = gateway_starts(lines)

        orphans: list[dict] = []
        for (platform, chat), queue in waiting.items():
            lost = [item for item in queue if item[0] < started]
            if not lost:
                continue
            stamp, text, _ = lost[0]
            died = next((s for s in starts if s > stamp), started)
            orphans.append({"platform": platform, "chat": chat, "received": stamp,
                            "text": text, "count": len(lost), "died": died})
        orphans.sort(key=lambda item: item["received"])
        return orphans


    def orphan_key(orphan: dict) -> str:
        return f"orphan:{orphan['platform']}:{orphan['chat']}@{orphan['received'].isoformat()}"


    def orphan_message(orphan: dict) -> str:
        excerpt = orphan["text"][:70] + ("..." if len(orphan["text"]) > 70 else "")
        extra = ""
        if orphan["count"] > 1:
            extra = f" The {orphan['count'] - 1} message(s) you sent after it went the same way."
        return (
            f"Your {orphan['platform']} message from "
            f"{orphan['received'].strftime('%Y-%m-%d %H:%M')} - \"{excerpt}\" - never got a reply "
            f"and never will: her gateway restarted at {orphan['died'].strftime('%H:%M')} and the turn "
            f"working on it died with it.{extra} Nothing was sent to you and nothing was saved. "
            "Re-send it if you still need it."
        )

THREE DESIGN DECISIONS THAT MUST NOT BE "SIMPLIFIED" AWAY:

(a) A `response ready` clears ONLY messages from the SAME gateway life (`item[2] != life`). A lane-wide reset looks equivalent and is not: on 2026-08-11 Rachad re-asked at 13:23:14 and was answered at 13:24:07, 13 minutes after the death — with a lane-wide reset the evidence would evaporate before the next 15-minute tick and whether he was ever told would be luck. Within a life the reset IS lane-wide, which is what makes the pairing immune to drift: 6 messages in 3 weeks produce no `response ready` of their own (folded into a turn already running), and FIFO pairing accumulates that error until it names the wrong message. I measured both rules; the life-scoped one gives exactly 5 events with the correct message named each time.

(b) `current_gateway_start` prefers `gateway_state.json` `start_time` over the log line. `start_time` is psutil create_time in CENTISECONDS — the same field and the same `/100` that `dado_lane_health.start_epoch` reads (live value 178648399927 → 2026-08-11 17:33:19, log line 17:33:23). It survives a rotation that carried the start line out of the tail, and it is the EARLIER of the two, which is the direction that cannot manufacture a false alarm (a later boundary would reclassify in-flight messages as orphans).

(c) `None` is a legitimate outcome. No boundary established ⇒ the whole check is inert. It never guesses.

CHANGES INSIDE `main()`:

    def main() -> int:
        # A deliberate stop wins over everything, exactly as dado_lane_health does.
        if DISABLE_FLAG_PATH.exists():
            return 0
        path = log_path()
        ...

after `now = dt.datetime.now()`:

        started = current_gateway_start(lines)
        stops = deliberate_stops()
        problems: list[tuple[str, str, dict]] = []
        orphan_problems: list[tuple[str, str, dict]] = []
        seen: set[str] = set()

        # A message that died with its gateway outranks a slow one: the slow turn
        # may still answer him, the dead one cannot.
        for orphan in orphaned_messages(lines, started):
            if (now - orphan["died"]).total_seconds() > ORPHAN_LOOKBACK_HOURS * 3600:
                continue
            if any(orphan["received"] <= stop <= orphan["died"] for stop in stops):
                continue  # he stopped her himself - reporting it is crying wolf
            key = orphan_key(orphan)
            seen.add(key)
            record = state.get(key) or {"alerts": 0, "last_alert": None}
            if record["alerts"] >= ORPHAN_ALERTS:
                continue
            orphan_problems.append((orphan_message(orphan), key, record))

first statement inside the existing `for session, turn in open_turns(lines).items():` loop:

            if started is not None and turn["started"] < started:
                # Not a stall - a corpse. This turn belongs to a gateway life that
                # no longer exists, so "she is still working, ask her what she is
                # doing" would be false. The orphan check above owns what he
                # actually lost; a killed cron/local turn is not his to chase.
                continue

immediately before `shown = problems[:MAX_PROBLEMS_PER_TICK]`:

        problems = orphan_problems + problems

and the save at the end becomes:

        if "--dry-run" not in sys.argv[1:]:
            save_state(state)

`--dry-run` is checked off `sys.argv` rather than argparse on purpose: cron invokes this as exactly `[python, script]` (`cron/scheduler.py` `_run_job_script`: `argv = [python_exe, str(path)]`, cwd = the scripts dir), and an argument parser that could reject an unexpected argv is a way for the watcher to die of its own options.

THE SUPPRESSION INTERVAL, stated once because it is the whole safety argument: an orphan is silenced iff a ledger stamp falls in `[received, died]`. A stop can only land in that interval if it happened during the very life that was handling the message — a stop from any other episode is strictly outside it (a later stop is after `died`; an earlier one is before `received`). Both tested.

DELIVERY IS DELIBERATELY UNCHANGED. The alert is a single extra line on stdout, delivered by the job's existing `deliver: telegram:891365639`. Do NOT give this its own send path: B-08 (reserved for Rachad) is about routing this exact script through `send_clean`/`queue_undelivered`, and a competing path would have to be unpicked. When B-08 lands, orphan alerts ride it for free — they flow through the same `problems`/`shown`/`print` block. Note the two changes are adjacent but non-overlapping in `main()`: mine ends at `problems = orphan_problems + problems`, B-08's begins at the `for _, key, record in shown:` block below it.

────────────────────────────────────────────────────────
STEP 3 — copy to the profile (nothing runs from the repo)

`jobs.json` id `faaabfa052e9` runs `stall_tripwire.py` resolved against `HERMES_HOME\scripts`. Copy the file and prove it:

    copy C:\FRPDepot\Dado\Tools\watch\stall_tripwire.py "%LOCALAPPDATA%\hermes\profiles\dado\scripts\stall_tripwire.py"

then confirm both sha256 match (they are identical today at `2114c17d99653ec8`; they must be identical again after). No `jobs.json` edit, no cron re-create, no gateway restart — the next `*/15` tick picks up the new file because each run is a fresh subprocess.

────────────────────────────────────────────────────────
STEP 4 — record it, since the whole point of the finding is that it lived only in a nightly review

`C:\FRPDepot\Dado\30_Memory\backend_backlog.md` — append `### B-31 FIXED (this commit) — A turn killed by a gateway death was invisible to every watcher` (B-30 is the current maximum). Cite the 08-04 review, the 2026-08-11 12:19:56 recurrence, the 5-in-3-weeks measurement, and the STOP ledger contract so nobody later "simplifies" `START_DADO.bat` back.

`C:\FRPDepot\CLAUDE.md` — in the LONG-JOB DISCIPLINE bullet, extend the `dado-stall-tripwire` sentence to: "…catches a turn that is OPEN RIGHT NOW past 20 min, AND a turn that is GONE — an inbound message with no `response ready` that predates the running gateway's `start_time` is reported as killed-in-flight (silent for a deliberate stop, via the `40_Logs\gateway_stops.log` ledger that STOP_DADO/START_DADO append to)."

Commit ONLY: `Dado/Tools/watch/stall_tripwire.py`, `Dado/Tools/watch/test_stall_tripwire.py`, `START_DADO.bat`, `STOP_DADO.bat`, `Dado/30_Memory/backend_backlog.md`, `CLAUDE.md`. `git status` currently shows ~14 other modified files and 6 untracked Zoho files from other sessions — do not sweep them in.

────────────────────────────────────────────────────────
EXPLICITLY NOT DOING (and why):
- No new cron job. The bug is in the watcher, so the fix is in the watcher.
- No "inbound with no response after N minutes" rule. It would fire on healthy long turns (a legitimate 6477s telegram turn on 2026-08-11 16:51) — the boundary is the gateway life, not a timeout.
- No never-dispatched-message check (inbound that produced no `turn_context`). Zero occurrences in 3 weeks of log; building it would be speculative noise.
- No out-of-band `dado_urgent_alert.py` path. The gateway is by definition healthy at the moment this alert is emitted (that is how the cron ran), and a second path would collide with B-08.

### Tests

All in `C:\FRPDepot\Dado\Tools\watch\test_stall_tripwire.py`, in the file's existing unittest + tempfile + `patch.object` style (no mocks for file-touching code — every test writes a real log and a real state file into a `TemporaryDirectory`). Written and passing: 30 total, the 13 pre-existing ones unchanged and untouched. Ready-to-copy file: `...\scratchpad\proto\test_stall_tripwire.py`; diff at `...\scratchpad\proposed_test_stall_tripwire.diff`.

New module-level helpers: `inbound(stamp, platform, chat, msg)`, `response_ready(stamp, platform, chat)`, `gateway_start(stamp)` — each emits a line copied verbatim from the live log.

class OrphanedTurnTests (setUp builds `<tmp>/profiles/dado/logs/agent.log` so that patching `log_path` also redirects `gateway_state_path`; patches `STATE_PATH`, `STOP_LEDGER_PATH`, `DISABLE_FLAG_PATH`):
- test_a_message_killed_by_a_gateway_death_is_reported — the core case; also asserts the string "Ask her what she is doing" is ABSENT.
- test_an_answered_message_is_never_reported
- test_a_message_the_running_gateway_received_is_not_an_orphan — in-flight belongs to the open-turn check.
- test_a_deliberate_stop_stays_silent — ledger stamp inside `[received, died]` ⇒ empty output. THE house-rule test.
- test_a_stop_from_a_different_outage_does_not_mask_this_one — stamp 3 days earlier ⇒ still reported.
- test_a_live_disable_flag_silences_everything
- test_it_is_announced_once_and_then_never_again — three consecutive runs, output only on the first.
- test_a_reply_after_the_restart_does_not_erase_the_loss — the 2026-08-11 race (re-asked at 13:23, answered at 13:24); fails if the reply reset is lane-wide instead of life-scoped.
- test_an_old_loss_still_in_the_log_window_is_not_announced — 2-day-old orphan, silence.
- test_without_a_gateway_start_time_the_check_is_inert
- test_a_turn_that_predates_the_running_gateway_is_not_called_a_stall — an open `platform=local` turn started before the boundary produces nothing.
- test_the_2026_08_04_incident_would_have_been_caught — the review's own incident replayed.
- test_several_lost_messages_on_one_lane_are_one_alert_that_counts_them — 3 lost, ONE line, "2 message(s) you sent after it".

class GatewayStartResolutionTests:
- test_start_time_is_centiseconds_like_dado_lane_health_reads_it
- test_the_log_line_is_the_fallback_when_the_state_file_is_unusable
- test_no_evidence_at_all_means_none
- test_a_start_time_in_the_future_is_refused

class RealLogShapeTests (mirrors the existing `test_real_log_line_shape_parses` guard — the whole check is regex-on-a-log, so the live shapes are pinned):
- test_the_three_live_line_shapes_still_parse — asserts platform/chat/received/text/died against the verbatim 2026-08-11 12:19:56 inbound and 13:10:55 start lines.
- test_a_reply_inside_the_same_life_clears_the_lane

PROOF THEY FAIL WITHOUT THE FIX: check out the current `stall_tripwire.py` beside the new test file and run it — every test in `OrphanedTurnTests`, `GatewayStartResolutionTests` and `RealLogShapeTests` errors with `AttributeError: module 'stall_tripwire' has no attribute 'orphaned_messages' / 'current_gateway_start' / 'STOP_LEDGER_PATH'`. For a behavioural (not structural) proof, run `test_the_2026_08_04_incident_would_have_been_caught`'s fixture through the old `main()`: it produces empty output, because a killed turn writes no `Turn ended` and the fixture holds no open turn at all.

Run with the only interpreter on this box:
  "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe" -m pytest C:\FRPDepot\Dado\Tools\watch\test_stall_tripwire.py -q
(also passes under `-m unittest test_stall_tripwire` from that directory). Current result: 30 passed, 0 warnings.

### Verification

Nothing here restarts a gateway, edits live state, or sends a message.

1. Unit tests, as above: 30 passed.

2. READ-ONLY REHEARSAL AGAINST THE REAL LIVE LOG — the `--dry-run` flag exists for exactly this and writes no state:
     cd "%LOCALAPPDATA%\hermes\profiles\dado\scripts"
     "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe" stall_tripwire.py --dry-run
   I ran this against the live tree from my scratch copy at 22:15 today and it printed:
     'Your discord message from 2026-08-11 12:19 - "Please proceed the PO we just received from SCT. Great a new Sales ord..." - never got a reply and never will: her gateway restarted at 13:10 and the turn working on it died with it. Nothing was sent to you and nothing was saved. Re-send it if you still need it.'
   plus the existing (correct) stall line for the then-open 21:45 telegram turn. `C:\FRPDepot\Dado\40_Logs\stall_tripwire_state.json` was byte-unchanged afterwards — confirmed.

3. PROVE THE DELIBERATE-STOP SUPPRESSION ON LIVE DATA WITHOUT STOPPING HER. Copy the live `agent.log` + `gateway_state.json` into a scratch `profiles/dado/` mirror and drive `main()` with `STATE_PATH`/`STOP_LEDGER_PATH`/`DISABLE_FLAG_PATH`/`log_path` patched into scratch — harness already written at `...\scratchpad\live_dryrun.py`. Measured results, all three on the real log:
     - no ledger                                   → orphan reported, second run silent
     - ledger `2026-08-11T12:40:00 …`  (inside the dead life)  → SILENT
     - ledger `2026-08-09T02:00:00 …`  (unrelated window)      → still reported
   Do not point the rehearsal at the live `STATE_PATH`: it would spend the real alert budget.

4. AFTER DEPLOY, confirm it is live and silent when clean: `dir "%LOCALAPPDATA%\hermes\profiles\dado\cron\output\faaabfa052e9"` — the next few `*/15` files should read `**Status:** silent (empty output)` exactly as today, apart from the one-off catch-up in risk #1 below. Then `hermes -p dado cron list` should still show `dado-stall-tripwire last_status ok` (a script that crashed would show `error` — that is the regression signal, and it appears within 15 minutes).

5. END-TO-END REHEARSAL OF THE STOP PATH, whenever Rachad next stops her for his own reasons: run STOP_DADO.bat / START_DADO.bat and check that `Dado\40_Logs\gateway_stops.log` gained two lines and the following tripwire ticks stayed silent. Do not manufacture a stop just to test this — step 3 already proves the logic on real data.

### Risks

WHAT COULD BREAK

1. ONE-OFF CATCH-UP ON FIRST RUN. `ORPHAN_LOOKBACK_HOURS = 12` means the first tick after deploy will announce any real loss from the last 12 hours — right now that is the 2026-08-11 12:19 discord message. It is a true statement about a message Rachad genuinely lost this morning, so I would let it send; if the orchestrator would rather it not, pre-seed `Dado\40_Logs\stall_tripwire_state.json` with `{"orphan:discord:1536573814560002089@2026-08-11T12:19:56": {"alerts": 1, "last_alert": "<now iso>"}}` before the copy in step 3, or deploy after that message ages past 12h.

2. A RESTART RACHAD ASKED FOR IN CHAT IS NOT A STOP_DADO STOP. The 2026-08-11 17:33 restart was self-requested (`.restart_last_processed.json` `requested_at` 17:30:02) and exited cleanly, and it writes no disable flag. If such a restart kills an in-flight message the alert WILL fire. That is correct — the message really was destroyed — but it is the most likely source of an alert he considers unnecessary. Measured exposure: 22 starts in 3 weeks, 5 with a message in flight. If it ever becomes noise, the narrow follow-up is to also suppress when `.restart_last_processed.json` `requested_at` falls in `[received, died]`; do not widen the suppression to "clean exits", because `STOP_DADO.bat` force-kills and a crash can exit cleanly-looking.

3. B-08 STILL OWNS DELIVERY. This alert is printed once and the state is written whether or not the Telegram send succeeded — the same defect B-08 describes, on a message that says "you lost work". This change does not make B-08 worse (it adds ~1 message per gateway-death-with-traffic, roughly 5 in 3 weeks) and it does not fix it. Whoever lands B-08 must route this line too; it needs no separate work because orphan alerts flow through the same `problems`/`shown`/`print` block.

4. LOG-FORMAT DRIFT ON A HERMES UPDATE. If `gateway.run: inbound message:` / `response ready:` / `Starting Hermes Gateway` change wording, the check goes INERT (silent, never false) — the failure direction is safe but invisible. `RealLogShapeTests` is the tripwire for that and must be run after any `hermes update`.

5. The `turn["started"] < started` skip means that after a restart, genuinely long turns started before it are no longer reported as stalls. That is intended (they are dead), but it does remove coverage in one exotic case: if `gateway_state.json` were wrong. Guarded — a `start_time` in the future is refused and the check reverts to inert.

WHAT MUST NOT CHANGE

- `START_DADO.bat` must keep appending BEFORE `Remove-Item $flag`, and `STOP_DADO.bat` must keep writing the flag FIRST (the watchdog's 5-minute resurrection depends on that ordering, `dado_gateway_watchdog.ps1` step 1).
- `dado_gateway_watchdog.ps1` — untouched, including the heartbeat stamp that must stay first in the file and the single sanctioned `C:\AgentTeam\Sync\aze_heartbeat_check.py` call. Nothing in this plan reads C:\AgentTeam.
- `jobs.json` id `faaabfa052e9`: schedule `*/15 * * * *`, `--no-agent`, `deliver telegram:891365639` — all unchanged. No cron create/delete, no gateway restart, no hermes install file.
- Silence-when-clean: no healthy path prints anything. Verified — the existing output files stay `silent (empty output)`.
- The three existing behaviours the file's own tests pin (per-turn budget spent only on what is printed; the tail/rotation window; newest-turn-per-session) are untouched; all 13 original tests pass unmodified.
- Both copies of `stall_tripwire.py` (repo + `profiles\dado\scripts`) must end up byte-identical, as they are today.


==============================================================================
## zoho-account-filter
==============================================================================

**Finding:** zoho-account-filter: "Zoho ignoring the account_id filter is detected but never compensated, so the whole banking review aborts instead of degrading" (+ the DADO CRITICAL partial "dado-daily-banking-review has NEVER produced a single review")

**Verdict:** fix-needed  |  **Effort:** medium  |  **Needs hermes patch:** False  |  **Needs fingerprints note:** False

### Root cause

THE FINDING IS RIGHT AND UNDERSTATES ITSELF. The job is not "fragile"; as written it is structurally incapable of ever emitting a non-empty review.

MECHANISM (symbols, not line numbers):

1. `C:\FRPDepot\Dado\Tools\watch\dado_daily_banking_review.py` -> `fetch_account_lines(vault, account_id)` sends `account_id=<id>` to `banking.books_ui_get("/api/v3/banktransactions/uncategorized?...")` and then asserts `line["account_id"] != account_id -> raise DailyReviewError("Zoho ignored account filter ...")`.

2. `build_report` calls it once per record in `LOGICAL_ACCOUNTS` (5 records: 96274000001409019 "Chequing account (C)", 96274000001411002 "FRP Depots - Desjardins", 96274000001409012 "USD Desjardins corporate build-up account", 96274000000035815 "Stripe Clearing", 96274000000035828 "PayPal Clearing"), and attributes every returned row to the loop variable: `line.update({"logical_account": group["label"], "account_name": check_by_id[account_id]["account_name"]})`. Attribution comes from WHAT WAS ASKED FOR, never from the row's own `account_id`.

3. That guard is therefore the ONLY thing standing between this script and reporting USD build-up money under the header "Desjardins CAD - Chequing account (C)". Softening or deleting it without restructuring attribution would convert an availability bug into a money-attribution bug. This is the single most important constraint on the fix.

4. Zoho's server-side `account_id` filter on this UI route is (at minimum) not honoured, and the evidence points to it being a complete no-op:
   - `C:\FRPDepot\Dado\20_Working\airwallex_usd_uncategorized_recovery_build_brief.md`, measured GET-only 2026-08-10 ~22:02 via the UNFILTERED sibling `banking.list_uncategorized_ui_transactions`: "returned exactly one open imported feed line ... 96274000001423074 ... account 96274000001409012 ... no other open imported feed line exists".
   - Both failing runs asked for 96274000001409019 and were handed a row on 96274000001409012 (cron output `...\cron\output\984e2f4f60e4\2026-08-09_08-15-36.md` and `2026-08-10_08-15-35.md`; executions.db row a8c9895e...). A working filter cannot return a foreign account's row.
   - The one run that "passed" (2026-08-08_08-15-46.md, `Status: silent (empty output)`) is consistent with an EMPTY feed, where a no-op filter is indistinguishable from a working one.

5. CONSEQUENCE, and this is the part the finding misses: even with the guard removed, the run still dies. On the 2nd record the same whole-feed response comes back and `build_report` hits `if line["transaction_id"] in seen: raise DailyReviewError("Transaction ... appeared under multiple configured accounts.")`. So the only two reachable outcomes today are (a) empty feed -> silent, exit 0, `last_status=ok`, or (b) any non-empty feed -> exit 1. **There is no input for which this script produces a review.** That is why 5 runs produced 0 reviews.

6. The sibling read path in `C:\FRPDepot\Dado\Tools\zoho\zoho_banking_reconciliation_tool.py` -> `list_uncategorized_ui_transactions` never sends `account_id` and filters client-side; `get_uncategorized_ui_transaction` selects by transaction_id client-side. The commissioned tool is already correct. The bug is entirely in the review script. Fix belongs there, at the layer where it is.

ON THE SECOND QUESTION ("would it now succeed given the keepalive fix; is 08:15 still risky"):
- No. The keepalive fix (`zoho_session_keepalive.py` SESSION_COMMAND now naming books.zohocloud.ca / inventory.zohocloud.ca) removes only the 2026-08-11 cause. The 2026-08-12 08:15 run will exit 1 again the moment the feed is non-empty, and exit 0-silent if it is empty. Either way Rachad gets no review.
- "08:15 sits inside the window when the session is routinely signed out" is NOT supported. n=1. `C:\FRPDepot\Dado\40_Logs\zoho_session_keepalive.log` has 4 lines total across 492 keepalive runs since 2026-08-08 11:59, and the single signed-out window (08-10 23:30 -> 08-11 09:40) has an identified, already-fixed cause. There is no evidence of a recurring morning outage. Moving the cron to 10:30 would trade a slot Rachad chose for an unproven one and would still not cover a signed-out session.
- The real, time-independent precondition risk is `books_ui_get`'s "Exactly one authenticated Canadian Zoho Books app page must be open" — it fails on ZERO pages and equally on TWO OR MORE. Live right now (CDP 9228 /json/list): exactly one `https://books.zohocloud.ca/app#/settings/emails/templates...` page plus one inventory page and a newtab, i.e. currently satisfied. A second Books tab opened by any other Zoho UI tool kills the review at any hour.
- Also verified: hermes cron passes NO arguments to scripts (`cron/scheduler.py` `_run_job_script`: `argv = [python_exe, str(path)]`), so any "retry slot" must be decided by the script itself, not by a CLI flag. Script timeout is the 3600s default (`_DEFAULT_SCRIPT_TIMEOUT`; no `cron.script_timeout_seconds` in either config.yaml, no `HERMES_CRON_SCRIPT_TIMEOUT`), so runtime is a non-issue (the failing runs took 5.8s).

### Plan

All changes are inside C:\FRPDepot. NO hermes install file is touched, so NO patch under C:\AgentTeam\Sync\patches\ and NO registration in $patches of APPLY_HERMES_SAFETY_PATCHES.ps1 is needed. NO C:\AgentTeam path is read or written, so NO fingerprints note.

=====================================================================
PHASE 0 - PROVE THE MECHANISM ON THE LIVE SYSTEM (read-only, 30 s)
=====================================================================
Run this BEFORE editing anything. It uses only the commissioned allowlisted GET path, sends no writes, and does not navigate any page. Requires exactly one books.zohocloud.ca/app tab open (verify first with `curl -s http://127.0.0.1:9228/json/list`).

  "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'C:\FRPDepot\Dado\Tools\zoho'); import zoho_tool, zoho_banking_reconciliation_tool as b; from urllib.parse import urlencode; v=zoho_tool.load_vault(); org=str(v['books_organization_id']); base={'page':1,'per_page':200,'response_option':1,'organization_id':org}; f=lambda r:[(t.get('transaction_id'),t.get('account_id'),t.get('amount')) for t in (r.get('transactions') or [])]; un=f(b.books_ui_get('/api/v3/banktransactions/uncategorized?'+urlencode(base),org)); fl=f(b.books_ui_get('/api/v3/banktransactions/uncategorized?'+urlencode({**base,'account_id':'96274000001409019'}),org)); print('unfiltered',un); print('filtered  ',fl); print('FILTER IS A NO-OP' if un==fl else 'filter changed the result set')"

Record the output in the fit_profile note (Phase 4). The fix below is correct under BOTH outcomes - if the filter turns out to work sometimes, trusting it remains wrong because it demonstrably failed twice.

=====================================================================
PHASE 1 - THE FIX  (C:\FRPDepot\Dado\Tools\watch\dado_daily_banking_review.py)
=====================================================================
Principle: fetch the feed ONCE with no account filter, attribute every row from its OWN account_id against identities `validate_accounts` just verified, and disclose everything not attributed. Zoho's filter is never sent and never trusted.

(1a) imports - add to the existing block:
    from datetime import date, datetime
    import urllib.error
    import urllib.request

(1b) new constants, immediately after LOGICAL_ACCOUNTS (BEFORE PAYROLL_MARKERS). Derive the two session constants from the commissioned tool so they can never drift:

    FEED_PATH = "/api/v3/banktransactions/uncategorized"
    MATCH_PATH = "/api/v3/banktransactions/uncategorized/match"
    BOOKS_APP_PREFIX = banking.BOOKS_UI_ORIGIN + "/app"
    CDP_TARGETS_URL = banking.CDP_ENDPOINT + "/json/list"
    KEEPALIVE_STATE = Path(r"C:\FRPDepot\Dado\40_Logs\zoho_session_keepalive_state.json")
    RUN_STATE = Path(r"C:\FRPDepot\Dado\40_Logs\daily_banking_review_state.json")
    CATCH_UP_AFTER_HOUR = 10

(1c) DELETE `fetch_account_lines` entirely and replace with three functions:

def fetch_feed_rows(vault: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the WHOLE imported feed once. The account_id filter is never sent.

    MEASURED 2026-08-09 and 2026-08-10: a request carrying
    account_id=96274000001409019 came back with a row belonging to
    96274000001409012, and on 2026-08-10 that was the ONLY row in the whole
    feed. Zoho's server-side filter on this UI route is not trustworthy, so it
    is not used at all; every row is attributed client-side from its own
    account_id. banking.list_uncategorized_ui_transactions has always fetched
    unfiltered for the same reason.
    """
    organization_id = banking.positive_id(vault["books_organization_id"], "organization_id")
    rows_out: list[dict[str, Any]] = []
    for page_number in range(1, 51):
        query = urlencode({
            "page": page_number,
            "per_page": 200,
            "response_option": 1,
            "organization_id": organization_id,
        })
        result = banking.books_ui_get(f"{FEED_PATH}?{query}", organization_id)
        rows = result.get("transactions")
        if not isinstance(rows, list):
            raise DailyReviewError("Zoho omitted its imported-feed rows.")
        for row in rows:
            if not isinstance(row, dict):
                raise DailyReviewError("Zoho returned an invalid imported-feed row.")
            rows_out.append(row)
        page_context = result.get("page_context") or {}
        if not isinstance(page_context, dict):
            raise DailyReviewError("Zoho returned invalid imported-feed page context.")
        if not page_context.get("has_more_page"):
            break
    else:
        raise DailyReviewError("Imported feed exceeded 50 pages.")
    return rows_out


def partition_feed(
    rows: list[dict[str, Any]], check_by_id: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split rows on the row's OWN account_id - never on what was requested.

    A row is reviewed only when its account_id is a configured record AND the
    feed's own account_name agrees with the name validate_accounts just read
    from the Books bank-account record. Everything else goes to the
    not-reviewed list so it is disclosed, never dropped and never
    mis-attributed.
    """
    reviewed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        feed_name = str(row.get("account_name") or "").strip()
        try:
            line = projected_line(row)
        except (DailyReviewError, banking.BankingToolError) as exc:
            skipped.append({
                "reason": f"feed row could not be read ({exc})",
                "account_id": str(row.get("account_id") or "(none)"),
                "account_name": feed_name,
            })
            continue
        check = check_by_id.get(line["account_id"])
        if check is None:
            skipped.append({
                "reason": "not one of the configured FRP Depot accounts",
                "account_id": line["account_id"],
                "account_name": feed_name,
            })
            continue
        if feed_name and feed_name != check["account_name"]:
            skipped.append({
                "reason": (
                    f"feed calls this account {feed_name!r}, Zoho's bank-account "
                    f"record calls it {check['account_name']!r}"
                ),
                "account_id": line["account_id"],
                "account_name": feed_name,
            })
            continue
        reviewed.append(line)
    ids = [line["transaction_id"] for line in reviewed]
    if len(ids) != len(set(ids)):
        raise DailyReviewError("Imported feed returned duplicate transaction IDs.")
    return reviewed, skipped


def skipped_summary(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str, str], int] = {}
    for entry in entries:
        key = (entry["account_id"], entry["account_name"], entry["reason"])
        counts[key] = counts.get(key, 0) + 1
    return [
        {"account_id": account_id, "account_name": name, "reason": reason, "count": count}
        for (account_id, name, reason), count in sorted(counts.items())
    ]

(1d) replace `build_report` wholesale:

def build_report(access_token: str, vault: dict[str, Any]) -> dict[str, Any]:
    checks = validate_accounts(account_rows(access_token, vault))
    check_by_id = {row["account_id"]: row for row in checks}
    lines, skipped = partition_feed(fetch_feed_rows(vault), check_by_id)
    unread_candidates = 0
    for line in lines:
        check = check_by_id[line["account_id"]]   # attribution comes from the ROW
        line["logical_account"] = check["logical_account"]
        line["account_name"] = check["account_name"]
        try:
            candidate_rows = candidates_for(vault, line["transaction_id"])
        except (DailyReviewError, banking.BankingToolError) as exc:
            unread_candidates += 1
            line.update({
                "category": "not classified",
                "recommendation": (
                    f"Zoho match candidates could not be read ({exc}); "
                    "classify this line by hand."
                ),
                "candidates": [],
                "candidates_read": False,
            })
            continue
        category, recommendation = classify(line, candidate_rows)
        line.update({
            "category": category,
            "recommendation": recommendation,
            "candidates": candidate_rows,
            "candidates_read": True,
        })
    lines.sort(key=lambda row: (row["date"], row["logical_account"], row["transaction_id"]))
    return {
        "accounts_checked": checks,
        "open_lines": lines,
        "open_count": len(lines),
        "not_reviewed": skipped_summary(skipped),
        "not_reviewed_count": len(skipped),
        "unread_candidate_count": unread_candidates,
    }

Note the deliberate split: whole-read integrity failures (not a list, non-dict row, bad page context, >50 pages, duplicate ids, account drift in validate_accounts) still hard-fail, because a read we cannot trust must not become a report. Per-ROW problems degrade and are disclosed.

(1e) `render` - use .get() so the existing silence test keeps passing, and speak when anything was skipped:

def render(report: dict[str, Any]) -> str:
    not_reviewed = report.get("not_reviewed") or []
    if not report["open_lines"] and not not_reviewed:
        return ""
    out = [
        "## Daily Zoho banking review",
        "",
        f"Open imported-feed lines: **{report['open_count']}**",
        "Zoho writes: **0**",
    ]
    if not_reviewed:
        out.append(
            f"NOT REVIEWED: **{report.get('not_reviewed_count', 0)}** imported-feed "
            "line(s) were read but deliberately not attributed to any account below -"
        )
        for entry in not_reviewed:
            name = entry["account_name"] or "(unnamed)"
            out.append(f"- {name} ({entry['account_id']}) x{entry['count']} - {entry['reason']}")
    if report.get("unread_candidate_count"):
        out.append(
            "Zoho match candidates could not be read for "
            f"**{report['unread_candidate_count']}** of the lines below."
        )
    out.append("")
    for index, line in enumerate(report["open_lines"], 1):
        out.extend([
            f"### {index}. {line['logical_account']} - {line['currency']} {line['amount']}",
            f"- Date: {line['date']}",
            f"- Account: {line['account_name']} ({line['account_id']})",
            f"- Description: {line['description'] or '(blank)'}",
            f"- Payee/reference: {line['payee'] or line['reference'] or '(blank)'}",
            f"- Classification: **{line['category']}**",
        ])
        if not line.get("candidates_read", True):
            out.append("- Zoho best match: NOT READ - the match lookup failed for this line")
        else:
            best = [row for row in line["candidates"] if row["is_best_match"]]
            if len(best) == 1:
                row = best[0]
                label = row["transaction_number"] or row["reference"] or row["transaction_id"]
                out.append(
                    f"- Zoho best match: {row['transaction_type']} {label} - "
                    f"{row['contact'] or '(no contact)'} - {line['currency']} {row['amount']}"
                )
            else:
                out.append("- Zoho best match: none")
        out.extend([f"- Recommendation: {line['recommendation']}", ""])
    out.append("Reply with the line number you want reviewed and staged. Nothing was committed.")
    return "\n".join(out)

(`- Account:` now carries the id. Deliberate: the account number is the thing that went wrong; showing it makes a future mis-attribution visible to Rachad at a glance.)

(1f) session precheck - plain urllib, no playwright, no page interaction:

def keepalive_verdict() -> str:
    """Non-empty when dado-zoho-session-watch has already reported the session unusable."""
    try:
        state = json.loads(KEEPALIVE_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(state, dict):
        return ""
    if state.get("signed_out"):
        return "the Zoho UI session is signed out"
    if state.get("down"):
        return "the Zoho UI browser session is down"
    return ""


def books_app_page_count() -> int | None:
    """Books app pages on CDP 9228; None when the endpoint is not answering."""
    try:
        with urllib.request.urlopen(CDP_TARGETS_URL, timeout=6) as response:
            targets = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    if not isinstance(targets, list):
        return None
    return sum(
        1 for target in targets
        if isinstance(target, dict) and target.get("type") == "page"
        and str(target.get("url") or "").startswith(BOOKS_APP_PREFIX)
    )


def session_precondition() -> tuple[str, str]:
    """('', '') to proceed; ('skip'|'fail', reason) otherwise.

    ONE ALARM PER CAUSE, FROM ITS OWNER. dado-zoho-session-watch already pages
    Rachad for a signed-out or absent browser, so those are 'skip' (exit 0 with
    one explanatory line). Nothing else watches for the wrong NUMBER of Books
    tabs, so that is 'fail' (exit 1) - otherwise it would be silent.
    """
    blocked = keepalive_verdict()
    if blocked:
        return "skip", blocked
    pages = books_app_page_count()
    if pages is None:
        return "skip", "the Zoho UI browser is not answering on CDP 9228"
    if pages == 0:
        return "fail", (
            "no Canadian Zoho Books app page is open. Open exactly one tab on "
            f"{BOOKS_APP_PREFIX} in the dedicated Edge window (CDP 9228)."
        )
    if pages > 1:
        return "fail", (
            f"{pages} Canadian Zoho Books app pages are open and the commissioned "
            "read path requires exactly one. Close the extra Books tabs in the "
            "dedicated Edge window; nothing else needs doing."
        )
    return "", ""

(1g) same-day suppression for the catch-up slot (hermes cron cannot pass args, so the script decides by clock + state):

def completed_today() -> bool:
    try:
        state = json.loads(RUN_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(state, dict) and state.get("last_completed_date") == date.today().isoformat()


def mark_completed_today() -> None:
    try:
        RUN_STATE.parent.mkdir(parents=True, exist_ok=True)
        RUN_STATE.write_text(
            json.dumps({"last_completed_date": date.today().isoformat()}, indent=1),
            encoding="utf-8",
        )
    except OSError:
        pass

(1h) `main` - replace the body:

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON even when no lines are open.")
    parser.add_argument("--ignore-session-precheck", action="store_true",
                        help="Skip the local Zoho-session precheck (manual runs only).")
    args = parser.parse_args()
    if not args.json and datetime.now().hour >= CATCH_UP_AFTER_HOUR and completed_today():
        return 0                       # the morning slot already produced today's review
    if not args.ignore_session_precheck:
        severity, reason = session_precondition()
        if severity == "skip":
            print(
                f"Daily Zoho banking review did not run: {reason}. "
                "dado-zoho-session-watch owns that alarm. No account was read "
                "and nothing was written."
            )
            return 0
        if severity == "fail":
            raise DailyReviewError(reason)
    vault = zoho_tool.load_vault()
    access_token, vault = zoho_tool.refresh_access_token(vault)
    report = build_report(access_token, vault)
    zoho_tool.save_vault(vault)
    mark_completed_today()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    text = render(report)
    if text:
        zoho_tool.append_receipt(
            "daily_zoho_banking_review_issued",
            f"accounts=4; records_checked={len(report['accounts_checked'])}; "
            f"open_lines={report['open_count']}; not_reviewed={report['not_reviewed_count']}; "
            f"unread_candidates={report['unread_candidate_count']}; writes=0",
        )
        print(text)
    return 0

(1i) `__main__` - make the one remaining hard failure actionable:

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DailyReviewError, banking.BankingToolError, zoho_tool.ZohoError) as exc:
        print(f"DAILY ZOHO BANKING REVIEW FAILED: {exc}", file=sys.stderr)
        if "Zoho Books app page" in str(exc):
            print(
                "WHAT TO DO: in the dedicated Edge window (CDP 9228) leave exactly "
                f"one tab on {BOOKS_APP_PREFIX}, and sign in there if it asks.",
                file=sys.stderr,
            )
        raise SystemExit(1)

=====================================================================
PHASE 2 - SYNC THE PROFILE COPY (mandatory - this is what actually runs)
=====================================================================
Repo and profile copies are byte-identical today (sha256 cd5208ac78462689...). Cron resolves `dado_daily_banking_review.py` against HERMES_HOME\scripts, i.e. the profile copy, which is outside the repo. After editing the repo copy:

  copy /Y "C:\FRPDepot\Dado\Tools\watch\dado_daily_banking_review.py" "%LOCALAPPDATA%\hermes\profiles\dado\scripts\dado_daily_banking_review.py"

then prove they match:

  "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe" -c "import hashlib,pathlib; p=[r'C:\FRPDepot\Dado\Tools\watch\dado_daily_banking_review.py', r'C:\Users\TDI-service\AppData\Local\hermes\profiles\dado\scripts\dado_daily_banking_review.py']; print([hashlib.sha256(pathlib.Path(x).read_bytes()).hexdigest()[:16] for x in p])"

Also delete the stale bytecode so a partial copy cannot be masked:
  del "C:\FRPDepot\Dado\Tools\watch\__pycache__\dado_daily_banking_review.cpython-311.pyc"

=====================================================================
PHASE 3 - CATCH-UP SLOT (recommended; separable from Phase 1)
=====================================================================
Do NOT move the 08:15 job. Evidence does not support "routinely signed out" (n=1, cause fixed). Add a second slot instead, so a session that recovers mid-morning still yields the day's review - exactly the 2026-08-11 shape, where the session came back at 09:40:58:

  hermes -p dado cron create "15 11 * * *" --name dado-daily-banking-review-catchup ^
    --no-agent --script dado_daily_banking_review.py ^
    --deliver telegram:891365639 --workdir C:\FRPDepot

Because of (1g) this run is silent on every day the 08:15 run completed. Keep the existing job 984e2f4f60e4 untouched at "15 8 * * *".

=====================================================================
PHASE 4 - RECORD THE MEASURED ZOHO BEHAVIOUR
=====================================================================
Append to C:\FRPDepot\Dado\30_Memory\fit_profile.md, in the same style as the existing 2026-08-07 imported-feed entries:

  - 2026-08-12: The Zoho Books UI route `GET /api/v3/banktransactions/uncategorized`
    does NOT honour an `account_id` query parameter. Measured: on 2026-08-09 and
    2026-08-10 a request for `96274000001409019` returned a row belonging to
    `96274000001409012`, and the unfiltered read on 2026-08-10 22:02 proved that
    row was the only line in the entire feed. Never pass `account_id` to this
    route and never trust it to have filtered. Read the feed once unfiltered and
    partition client-side on each row's own `account_id`, exactly as
    `list_uncategorized_ui_transactions` already does. [Phase 0 probe output goes here.]

Then commit both files plus the test in C:\FRPDepot (the tree pushes to the private GitHub remote nightly). No AgentTeam file is touched, so no fingerprints note.

=====================================================================
DELIBERATELY NOT DOING
=====================================================================
- NOT relaxing `books_ui_get`'s "exactly one page" rule. It is inside the commissioned, write-capable `zoho_banking_reconciliation_tool.py`, guarded and heavily tested; loosening page selection there could point a future WRITE at the wrong tab. The review reports the condition instead.
- NOT moving 08:15 to 10:30. That is scheduling around a bug that is being fixed at its own layer, and it would not cover a signed-out session anyway.
- NOT adding an in-script sleep/retry. Cron script runs are sequential; a sleeping job causes head-of-line queueing for every other Dado job.
- NOT touching the `deliver` mechanism. This job inherits B-08's no-retry telegram weakness; that is a separate finding with a separate owner.

### Tests

FILE: C:\FRPDepot\Dado\Tools\watch\test_dado_daily_banking_review.py (extend; keep the existing 7 tests, all of which still pass unchanged - `render` uses .get() specifically so `test_clean_report_is_silent` and `test_nonempty_render_discloses_zero_writes_and_requires_reply` keep working with their minimal report/line dicts).

REPLACE `test_account_filter_must_be_honored` - it pins the behaviour being removed.

Shared helpers to add to the TestCase:

    def live_accounts(self):
        return {
            "96274000001409019": {"account_id": "96274000001409019", "account_name": "Chequing account (C)", "currency_code": "CAD", "is_active": True},
            "96274000001411002": {"account_id": "96274000001411002", "account_name": "FRP Depots - Desjardins", "currency_code": "CAD", "is_active": True},
            "96274000001409012": {"account_id": "96274000001409012", "account_name": "USD Desjardins corporate build-up account", "currency_code": "USD", "is_active": True},
            "96274000000035815": {"account_id": "96274000000035815", "account_name": "Stripe Clearing", "currency_code": "CAD", "is_active": True},
            "96274000000035828": {"account_id": "96274000000035828", "account_name": "PayPal Clearing", "currency_code": "CAD", "is_active": True},
        }

    def feed_row(self, **changes):
        value = {"transaction_id": "100", "account_id": "96274000001409019",
                 "account_name": "Chequing account (C)", "date": "2026-08-08",
                 "amount": 100, "currency_code": "CAD", "status": "uncategorized",
                 "debit_or_credit": "credit"}
        value.update(changes)
        return value

    def ui(self, feed_rows, match_rows=None, match_error=None, seen=None):
        def _call(path, organization_id):
            if seen is not None:
                seen.append(path)
            if path.startswith(review.MATCH_PATH):
                if match_error is not None:
                    raise match_error
                return {"matching_transactions": match_rows or []}
            return {"transactions": feed_rows, "page_context": {"has_more_page": False}}
        return _call

    def report_for(self, feed_rows, **kw):
        with mock.patch.object(review, "account_rows", return_value=self.live_accounts()), \
             mock.patch.object(review.banking, "books_ui_get", side_effect=self.ui(feed_rows, **kw)):
            return review.build_report("token", {"books_organization_id": "110002157575"})

NEW CASES (each names the defect it pins):

1. test_the_2026_08_09_stray_row_is_reviewed_not_aborted
   Feed = ONE row on 96274000001409012 (the exact shape that killed 08-09 and 08-10). Assert build_report returns open_count == 1, the line's logical_account == "Desjardins USD" and account_name == "USD Desjardins corporate build-up account", not_reviewed_count == 0, and no exception. THIS IS THE REGRESSION TEST FOR THE FINDING.
2. test_feed_is_fetched_without_an_account_filter
   Collect the paths via `seen`; assert exactly one non-match path was requested, that it starts with review.FEED_PATH, and that "account_id=" is NOT in it.
3. test_rows_are_attributed_from_their_own_account_id
   Feed = two rows, one on ...409019 and one on ...409012. Assert each line's logical_account/account_name matches its OWN account, and that no line carries the other account's name. (Directly pins "never report figures for the wrong account".)
4. test_row_for_an_unconfigured_account_is_disclosed_not_merged
   Feed = one row on 96274000009999999. Assert open_count == 0, not_reviewed_count == 1, and the single not_reviewed entry carries account_id "96274000009999999" and reason "not one of the configured FRP Depot accounts".
5. test_account_name_disagreement_is_disclosed_not_attributed
   Feed row on ...409019 with account_name "Chequing account (X)". Assert open_count == 0 and the not_reviewed reason mentions both names.
6. test_unreadable_row_is_counted_and_never_attributed
   Feed row with account_id "" (positive_id rejects it). Assert open_count == 0, not_reviewed_count == 1, reason starts "feed row could not be read".
7. test_match_candidate_failure_degrades_one_line_only
   match_error=review.banking.BankingToolError("session gone"). Assert open_count == 1, unread_candidate_count == 1, line["candidates_read"] is False, line["category"] == "not classified", and that render(report) contains "NOT READ".
8. test_duplicate_transaction_ids_still_hard_fail
   Two feed rows with the same transaction_id on the same configured account -> assertRaises(review.DailyReviewError, "duplicate transaction IDs").
9. test_render_names_unreviewed_accounts_and_counts
   Report with open_lines == [] and one not_reviewed entry -> render is NON-empty, contains "NOT REVIEWED", the account id and the count. (A skipped line must break silence.)
10. test_render_is_silent_only_when_nothing_open_and_nothing_skipped
    render({"open_lines": [], "open_count": 0, "not_reviewed": [], "not_reviewed_count": 0}) == "".
11. test_page_context_has_more_page_is_followed_once - two-page feed; assert both pages requested and both rows present.

PRECHECK CASES (real files, no I/O mocking - patch the module constants to real paths under tempfile.TemporaryDirectory(), matching the "no mocks for file-touching code" rule):
12. test_signed_out_keepalive_state_skips_without_failing - write {"down": false, "signed_out": true}; assert session_precondition() == ("skip", "the Zoho UI session is signed out").
13. test_healthy_keepalive_state_does_not_block - {"down": false,"signed_out": false} + books_app_page_count patched to 1 -> ("", "").
14. test_missing_keepalive_state_does_not_block - constant points at a nonexistent path -> keepalive_verdict() == "".
15. test_two_books_pages_fail_with_an_actionable_message - books_app_page_count -> 2; assert severity "fail" and the reason contains "Close the extra Books tabs".
16. test_zero_books_pages_fail - -> 0, severity "fail", reason names BOOKS_APP_PREFIX.
17. test_cdp_unreachable_skips - -> None, severity "skip".
18. test_books_app_prefix_tracks_the_commissioned_tool - assert review.BOOKS_APP_PREFIX == review.banking.BOOKS_UI_ORIGIN + "/app" and review.CDP_TARGETS_URL.startswith(review.banking.CDP_ENDPOINT). (Pins the anti-drift derivation; this is the class of bug that caused the 08-11 failure.)

CATCH-UP CASES (real temp RUN_STATE file):
19. test_catchup_is_silent_when_the_morning_run_completed - write {"last_completed_date": today}; assert completed_today() is True.
20. test_catchup_runs_when_yesterdays_date_is_stored - stored date = yesterday -> completed_today() is False.
21. test_mark_completed_today_roundtrips - mark_completed_today() then completed_today() is True.

PROVING THEY FAIL WITHOUT THE FIX: check out the current file and run cases 1-3. Case 1 raises DailyReviewError("Zoho ignored account filter 96274000001409019; returned 96274000001409012") - the verbatim production failure. Case 2 fails because every request carries account_id=. Case 3 raises "appeared under multiple configured accounts" once the guard is bypassed - the proof that the guard was load-bearing for correctness, not just for alarming.

RUN:
  cd C:\FRPDepot\Dado\Tools\watch
  "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe" -m pytest test_dado_daily_banking_review.py -q
Also re-run the commissioned banking suite untouched as a no-collateral check:
  cd C:\FRPDepot\Dado\Tools\zoho
  "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe" -m pytest test_zoho_banking_reconciliation_tool.py -q

### Verification

LIVE VERIFICATION, in order, none of it writing to Zoho.

0. Session sanity (read-only, no browser attach):
   curl -s http://127.0.0.1:9228/json/version
   curl -s http://127.0.0.1:9228/json/list
   Expect exactly ONE page whose url starts https://books.zohocloud.ca/app. (Confirmed present at the time of this analysis.) Also: type C:\FRPDepot\Dado\40_Logs\zoho_session_keepalive_state.json -> {"down": false, "signed_out": false}.

1. Phase 0 probe (command in the plan). Two outcomes, both fine:
   - "FILTER IS A NO-OP" -> mechanism confirmed; record it.
   - "filter changed the result set" -> the filter works TODAY; the fix stays, because it demonstrably did not work on 08-09/08-10. Record that too.
   It also tells you the current feed contents, i.e. whether tomorrow's unpatched run would have crashed or gone silent. (Note: the 2026-08-11 11:29 categorize attempt on 96274000001423074 locked `indeterminate` with reason "imported feed did not return exactly one transaction", so the feed may currently be empty - which is exactly why the probe, not an assumption, decides this.)

2. Unit tests green (both suites, commands above).

3. Manual end-to-end from the REPO copy, JSON mode, before touching the profile:
   "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe" C:\FRPDepot\Dado\Tools\watch\dado_daily_banking_review.py --json
   ACCEPTANCE:
   a. exit code 0;
   b. accounts_checked has 5 records with the expected names/currencies;
   c. for EVERY entry in open_lines, its account_id maps to its logical_account/account_name per LOGICAL_ACCOUNTS - check this by eye, it is the data-integrity property;
   d. not_reviewed lists anything the review did not attribute, with ids;
   e. compare open_lines + not_reviewed against the independent unfiltered read from step 1 - the transaction_id sets must be identical. Nothing may vanish between the two.

4. Human-text mode:
   ...dado_daily_banking_review.py
   Empty output when nothing is open and nothing skipped; otherwise a report whose "- Account:" lines carry the right id.

5. Precheck, proved rather than assumed - open a SECOND tab on https://books.zohocloud.ca/app in the dedicated Edge window, run the script, confirm it exits 1 with "2 Canadian Zoho Books app pages are open ... Close the extra Books tabs", then close the extra tab and confirm it runs again. (This is the only step that touches the live browser; it opens and closes a tab and performs no Zoho action.)

6. Sync the profile copy and prove sha256 equality (Phase 2 command).

7. Force ONE real cron run instead of waiting for 08:15:
   hermes -p dado cron run 984e2f4f60e4
   Then confirm:
   - a fresh file in %LOCALAPPDATA%\hermes\profiles\dado\cron\output\984e2f4f60e4\ that is NOT "script failed";
   - jobs.json job 984e2f4f60e4 shows last_status "ok" and last_error null;
   - a new row in profiles\dado\cron\executions.db with status 'completed' (read-only: sqlite3.connect('file:...?mode=ro', uri=True)) - this job has had ZERO completed rows to date, so the first one is the real proof;
   - Rachad's Telegram received either the review or nothing, but NOT a red "Script exited with code 1".

8. Next morning (2026-08-13, since 08-12 08:15 will likely still be the old code unless applied tonight): confirm the 08:15 output file is a review or a clean silence, and that any 11:15 catch-up run is silent.

9. Standing check that this stops being invisible: the job's own history is the signal. `hermes -p dado cron runs 984e2f4f60e4` plus the output dir. Do NOT accept last_status=ok as evidence - it read "ok" for the 2026-08-08 silent run too.

### Risks

WHAT COULD BREAK

1. NEW OUTPUT WHERE THERE WAS SILENCE. The unfiltered read now sees the whole organization's uncategorized feed, so open lines on bank accounts outside LOGICAL_ACCOUNTS become a "NOT REVIEWED" block that speaks every day until someone acts. That is intended (a bank line nobody reviews is a real finding) but it can look like noise. If it turns out to be a permanent, ignorable account, the correct response is to add it to LOGICAL_ACCOUNTS - NOT to suppress the disclosure.
2. THE account_name CROSS-CHECK COULD FALSE-POSITIVE if Zoho's feed renders a name differently from the bank-account record. Failure mode is safe (the line is disclosed, never mis-attributed) but it would look alarming. If it fires spuriously in step 3, downgrade that one branch to an advisory field on the line rather than removing it.
3. RUNTIME grows with open lines - one browser round trip per line for candidates_for. At ~1-2 s per call (measured: the whole failing run was 5.8 s) even 100 open lines is ~3 min, well inside the 3600 s default script timeout. No action needed, but do not add per-line retries without rechecking this.
4. BROWSER LANE CONTENTION. books_ui_get attaches to the shared CDP 9228 singleton and is NOT wrapped in Dado\Tools\common\ui_lane_lock.py. A concurrent lane navigating that tab mid-review makes an evaluate throw. Post-fix that degrades one line instead of killing the run for feed reads it will still abort - acceptable, and out of scope. Do not "fix" it by taking the lane lock here without thinking: a long read holding the browser lock would block a live write tool.
5. TWO CRON JOBS, ONE SCRIPT (Phase 3). If the 08:15 run is still executing at 11:15 the scheduler's already-running skip applies to each job independently, so both could in principle run; the RUN_STATE guard makes the second one silent only if the first has already finished. Harmless (both are GET-only) but worth knowing.
6. exit 0 ON A SKIPPED RUN sets last_status=ok for a day with no review. The printed line is the signal; last_status is not. This is the systemic weakness other findings cover - do not let this change be read as making last_status trustworthy.

WHAT MUST NOT CHANGE

- NEVER attribute a row by the requested account. Attribution must always be `check_by_id[line["account_id"]]`. If a future edit reintroduces the loop-variable form, the guard that used to catch it will be gone.
- Do not simply delete the old guard without the partition rewrite. On its own that turns an availability bug into USD money reported as CAD Chequing.
- Do not touch zoho_banking_reconciliation_tool.py. It is a commissioned write-capable tool with an approval/lock/readback contract; its read path is already correct and its strictness is deliberate. Anything that looks like it needs changing there should be raised with Rachad, not patched in passing.
- Keep it GET-only. No stage, no commit, no APPROVED, DRAFTS/READ ONLY.
- Keep the repo copy and the profile scripts copy byte-identical. The profile copy is what cron runs and it is outside the repo; a half-applied fix here is indistinguishable from no fix.
- Keep silence-when-clean: an empty feed with nothing skipped must still print nothing.
- No AgentTeam path is read or written anywhere in this plan; the data wall is untouched.
