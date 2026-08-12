"""Keep a committed record of Dado's LIVE profile, and notice when it moves.

WHY THIS EXISTS. Every scheduled duty Dado has - the daily banking review, the
inbox sweeps, the conduct review, the stall tripwire, the Zoho keep-alive -
exists in exactly ONE place on ONE disk:
`%LOCALAPPDATA%\\hermes\\profiles\\dado\\cron\\jobs.json`. Nothing commits it,
nothing diffs it, nothing notices when a job is deleted, disabled or
re-scheduled. The scripts those jobs run live in a THIRD place - the profile
`scripts\\` directory, outside this repo - and on 2026-08-08 job_runner.py was
fixed in the repo while cron kept executing the old deployed copy for three
days, with every health signal green.

DIRECTION OF TRUTH - THE OPPOSITE OF dado_soul_sync.py, AND THAT IS THE POINT.
SOUL.md is authored in the repo, so that sync writes mirror -> live. Cron is
authored in the LIVE profile (`hermes -p dado cron create`) and hermes rewrites
jobs.json on every run of every job, so here LIVE IS THE TRUTH and this tool
only ever writes the REPO. It never writes one byte into the profile, and it is
deliberately NOT an importer: copying a mirror over a live schedule is exactly
what the neighbouring tree had to build a refusal gate against (measured
2026-08-08 - an import at that moment would have deleted a live weekly job and
re-armed a spent one-shot against a real client case).

WHAT IT WRITES, all under DadoProfile\\cron\\ and all picked up by the nightly
conduct review's `git add -A`:
  jobs.mirror.json    the DEFINITION of every job: schedule, script, deliver,
                      repeat budget, enabled/state. Runtime bookkeeping is
                      STRIPPED and there is no generated-at stamp, so the file
                      changes only when the SCHEDULE changes - not 288x a day.
  scripts.mirror.json what is actually DEPLOYED in the profile scripts dir:
                      sha256 per file plus the repo file it should equal.
                      Content is NOT copied - it already lives in Dado\\Tools,
                      and a second copy in the same repo would be the very drift
                      this exists to catch.
  RECREATE.md         a human-readable rebuild line per job, so a wiped schedule
                      can be reconstructed BY A PERSON, deliberately not by a
                      script.

IT ALSO COMPARES config.yaml SEMANTICALLY, not textually. Measured 2026-08-11:
the committed mirror was 14 parsed keys behind live and did not know Dado had a
Discord lane at all - while a text diff screamed forever about permuted keys and
hand-written comments. Byte comparison is useless in both directions here.

SILENT WHEN CLEAN. It speaks only when a deployed script differs from its repo
original (the failure that actually bit) or when config drifts. It never
restarts anything and never alerts on its own - the caller decides.

Usage:
    python dado_profile_mirror.py            # refresh the record, report drift
    python dado_profile_mirror.py --check     # report only, write nothing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

PROFILE = "dado"
REPO = Path(r"C:\FRPDepot")
MIRROR_DIR = REPO / "DadoProfile" / "cron"
REPO_TOOLS = REPO / "Dado" / "Tools"
REPO_CONFIG = REPO / "DadoProfile" / "config.yaml"

# Rewritten by hermes on every run of every job. Keeping any of it would make
# this file churn hundreds of times a day and bury the one diff that matters.
RUNTIME_KEYS = {
    "next_run_at", "last_run_at", "last_status", "last_error",
    "last_delivery_error", "run_claim", "fire_claim", "paused_at",
    "updated_at", "created_at", "origin", "provider_snapshot", "model_snapshot",
}


def profile_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA is not set.")
    return Path(local) / "hermes" / "profiles" / PROFILE


def live_jobs() -> list[dict[str, Any]]:
    try:
        raw = json.loads((profile_dir() / "cron" / "jobs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    jobs = raw.get("jobs") if isinstance(raw, dict) else raw
    return jobs if isinstance(jobs, list) else []


def definition(job: dict[str, Any]) -> dict[str, Any]:
    """The job MINUS everything hermes rewrites as it runs."""
    out = {k: v for k, v in sorted(job.items()) if k not in RUNTIME_KEYS}
    repeat = out.get("repeat")
    if isinstance(repeat, dict):
        # `completed` is a counter, not a definition. `times` is the budget and
        # IS part of the definition - a one-shot becoming infinite matters.
        out["repeat"] = {"times": repeat.get("times")}
    return out


def sha256_of(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def repo_counterpart(name: str) -> Path | None:
    """Where a deployed script SHOULD have come from in this repo."""
    matches = sorted(REPO_TOOLS.rglob(name))
    matches = [m for m in matches if "__pycache__" not in m.parts]
    return matches[0] if len(matches) == 1 else (matches[0] if matches else None)


# Some deployed scripts are deliberately NOT copies: they are one-screen
# launchers that put this repo on sys.path and delegate straight into it, so the
# repo file IS the executing code and a hash comparison is meaningless. Measured
# 2026-08-12: gla_sync_ready_manway_monitor.py and its cover twin are exactly
# this, and the first version of this checker reported both as drift on every
# run. A gate that cries wolf gets ignored, which would have cost the one real
# drift it exists to catch.
LAUNCHER_MAX_BYTES = 1200


def is_launcher(path: Path) -> bool:
    """A shim that runs the repo copy rather than duplicating it."""
    try:
        if path.stat().st_size > LAUNCHER_MAX_BYTES:
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return ("sys.path.insert" in text or "sys.path.append" in text) and str(REPO) in text


def deployed_scripts() -> dict[str, dict[str, Any]]:
    """Every .py actually deployed in the profile, and how it relates to the repo."""
    scripts_dir = profile_dir() / "scripts"
    out: dict[str, dict[str, Any]] = {}
    if not scripts_dir.is_dir():
        return out
    for path in sorted(scripts_dir.glob("*.py")):
        live_hash = sha256_of(path)
        repo_path = repo_counterpart(path.name)
        repo_hash = sha256_of(repo_path) if repo_path else None
        launcher = is_launcher(path)
        out[path.name] = {
            "kind": "launcher" if launcher else "copy",
            "deployed_sha256": live_hash,
            "repo_path": str(repo_path.relative_to(REPO)) if repo_path else None,
            "repo_sha256": repo_hash,
            # A launcher delegates into the repo, so the executing code is the
            # repo's by construction and there is nothing to compare.
            "in_sync": True if launcher else bool(
                live_hash and repo_hash and live_hash == repo_hash),
        }
    return out


# Keys where mirror and live are KNOWN to differ on purpose, mapped to the live
# value that divergence is recorded against. Reported only if live moves to
# something else - a standing difference must not cry wolf every five minutes,
# but it must not hide a CHANGE either.
EXPECTED_CONFIG_DIVERGENCE = {
    # CLAUDE.md: "NO fallback provider on purpose: primary down = honest
    # failure, never silent model drift." Live carries one anyway, pointing at a
    # provider recorded as unfunded, and matching the known fallback-persistence
    # bug. The mirror deliberately omits it so an import REMOVES it rather than
    # cementing it. See the comment at the top of DadoProfile\config.yaml.
    "fallback_providers": [{"provider": "nous", "model": "deepseek/deepseek-v4-pro"}],
}


def config_drift() -> list[str]:
    """Parsed live-vs-mirror comparison. Text comparison cannot do this job.

    Returns dotted key paths that differ. Empty means the mirror is accurate.
    """
    try:
        import yaml
    except ImportError:
        return []
    def load(path: Path):
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return None
    live = load(profile_dir() / "config.yaml")
    mirror = load(REPO_CONFIG)
    if live is None or mirror is None:
        return []

    diffs: list[str] = []

    def walk(a, b, prefix=""):
        if prefix in EXPECTED_CONFIG_DIVERGENCE:
            # Silent while live still holds exactly the value this divergence
            # was recorded against; loud the moment it becomes something else.
            if a == EXPECTED_CONFIG_DIVERGENCE[prefix]:
                return
            diffs.append(f"{prefix} (EXPECTED to diverge, but live has CHANGED to {a!r})")
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                walk(a.get(key), b.get(key), f"{prefix}.{key}" if prefix else str(key))
        elif a != b:
            diffs.append(prefix)

    walk(live, mirror)
    return diffs


def recreate_lines(jobs: list[dict[str, Any]]) -> list[str]:
    lines = []
    for job in sorted(jobs, key=lambda j: str(j.get("name") or "")):
        schedule = job.get("schedule") or {}
        expr = schedule.get("expr") or schedule.get("display") or "?"
        parts = [f'hermes -p {PROFILE} cron create "{expr}"',
                 f'--name {job.get("name")}']
        if job.get("no_agent"):
            parts.append("--no-agent")
        if job.get("script"):
            parts.append(f'--script {job["script"]}')
        if job.get("deliver"):
            parts.append(f'--deliver {job["deliver"]}')
        state = "" if job.get("enabled", True) else "   # DISABLED in live"
        lines.append("    " + " ".join(parts) + state)
    return lines


def write_mirror(jobs: list[dict[str, Any]], scripts: dict[str, dict[str, Any]]) -> None:
    MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    definitions = [definition(job) for job in
                   sorted(jobs, key=lambda j: str(j.get("name") or ""))]
    (MIRROR_DIR / "jobs.mirror.json").write_text(
        json.dumps({"jobs": definitions}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (MIRROR_DIR / "scripts.mirror.json").write_text(
        json.dumps(scripts, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    body = [
        "# Rebuilding Dado's schedule by hand",
        "",
        "GENERATED - do not edit. Refreshed by dado_profile_mirror.py from the LIVE",
        "profile, which is the only authoritative copy.",
        "",
        "This file exists so a wiped schedule can be rebuilt BY A PERSON. There is",
        "deliberately no importer: hermes rewrites jobs.json on every run under its",
        "own lock, so an external writer can silently drop a claim or a completion,",
        "and copying a mirror over a live schedule is what the neighbouring tree had",
        "to build a refusal gate against.",
        "",
        f"{len(jobs)} job(s) live at the time of writing:",
        "",
    ]
    body.extend(recreate_lines(jobs))
    body.append("")
    (MIRROR_DIR / "RECREATE.md").write_text("\n".join(body), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="report drift, write nothing")
    args = parser.parse_args(argv)

    jobs = live_jobs()
    if not jobs:
        print("PROFILE MIRROR: could not read Dado's live cron store; nothing recorded.")
        return 1
    scripts = deployed_scripts()

    if not args.check:
        try:
            write_mirror(jobs, scripts)
        except OSError as exc:
            print(f"PROFILE MIRROR: could not write the record - {exc}")
            return 1

    problems: list[str] = []
    stale = [name for name, info in scripts.items()
             if info["repo_path"] and not info["in_sync"]]
    if stale:
        problems.append(
            "DEPLOYED SCRIPTS DIFFER FROM THIS REPO - cron is running code that is "
            "not what is committed. This is the failure that ran job_runner.py "
            "three days behind with every health signal green:")
        problems.extend(f"  - {name} (repo: {scripts[name]['repo_path']})" for name in stale)
    orphans = [name for name, info in scripts.items() if not info["repo_path"]]
    if orphans:
        problems.append("DEPLOYED BUT NOT IN THIS REPO (no counterpart found):")
        problems.extend(f"  - {name}" for name in orphans)
    drift = config_drift()
    if drift:
        problems.append(
            f"config.yaml mirror is {len(drift)} parsed key(s) behind live "
            "(a text diff cannot see this - key order and comments differ):")
        problems.extend(f"  - {key}" for key in drift[:12])
        if len(drift) > 12:
            problems.append(f"  - ...and {len(drift) - 12} more")

    if problems:
        print("\n".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
