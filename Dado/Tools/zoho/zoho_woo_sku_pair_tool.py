#!/usr/bin/env python
"""Approval-gated paired Zoho + WooCommerce SKU correction coordinator.

Rachad Homsi authorized this workflow design on 2026-08-06: one combined
staged plan, his own one-word APPROVED reply, and verified read-back in both
systems.  Workflow authorization is not approval of any SKU correction.

This coordinator has no service transport.  The only possible business writes
are subprocess calls to the two pre-existing named writer CLIs:
- zoho_inventory_item_tool.py commit-name-sku
- woocommerce_change_tool.py commit
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
from typing import Any

import zoho_tool

# Shared owner authority (autonomy programme 2026-08-21, spec A1/A2/A4/A6). A
# SKU correction is REVERSIBLE work: his clear go in his own message covers the
# pair (exact APPROVED still works; "yes" / "go ahead" count) and is passed
# through to both named writer CLIs; a failed child leaves the pair "needs
# re-stage" with the landed half recorded -- nothing is permanently locked; a
# committed pair is spent. Each writer keeps its own captured live state and
# its own restore route (restore-name-sku / restore), so this coordinator
# restores by pointing at them. Appended so the common folder never shadows
# the stdlib.
sys.path.append(str(Path(__file__).resolve().parent.parent / "common"))
import owner_authority  # noqa: E402

TOOL_NAME = "FRP Depot Paired Zoho + WooCommerce SKU Correction Coordinator"
SCHEMA_VERSION = 1
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_woo_sku_pair_plans"
ZOHO_PLAN_DIR = ROOT / "Dado" / "20_Working" / "zoho_item_plans"
WOO_PLAN_DIR = ROOT / "Dado" / "20_Working" / "woocommerce_plans"
ZOHO_TOOL = ROOT / "Dado" / "Tools" / "zoho" / "zoho_inventory_item_tool.py"
WOO_TOOL = ROOT / "Dado" / "Tools" / "woocommerce" / "woocommerce_change_tool.py"
PLAN_LIFETIME_HOURS = 24
# Still the word the plan names; since 2026-08-21 any clear go of his counts.
APPROVAL_WORD = owner_authority.EXACT_WORD
INPUT_FIELDS = {"zoho_item_id", "woo_parent_id", "woo_variation_id", "new_sku", "sources"}
PLAN_FIELDS = {
    "schema_version", "tool", "created_utc", "expires_utc", "nonce",
    "approval_required", "identifiers", "before", "after", "sources",
    "children", "sha256",
}
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")


class PairToolError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PairToolError(f"{label} JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise PairToolError(f"{label} JSON must contain one object.")
    return value


def clean_positive_id(value: Any, label: str, *, as_text: bool = False) -> int | str:
    if isinstance(value, bool):
        raise PairToolError(f"{label} must be a positive integer.")
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise PairToolError(f"{label} must be a positive integer.")
    return text if as_text else int(text)


def clean_nonempty_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise PairToolError(f"{label} must be text.")
    result = value.strip()
    if not result:
        raise PairToolError(f"{label} cannot be blank.")
    if len(result) > maximum:
        raise PairToolError(f"{label} exceeds the {maximum}-character safety limit.")
    if any(ord(char) < 32 for char in result):
        raise PairToolError(f"{label} contains control characters.")
    return result


def require_rachad_approval(approval: Any, lane: Any = None) -> owner_authority.OwnerGo:
    """His clear go in his own message (spec A1); workflow authorization is not
    SKU-change approval and Dado never supplies it."""
    try:
        return owner_authority.require_owner_go(approval, lane=lane, what="this paired SKU plan")
    except owner_authority.OwnerAuthorityRefused as exc:
        raise PairToolError(str(exc)) from exc


def contained_file(raw_path: Any, folder: Path, label: str) -> Path:
    text = str(raw_path if raw_path is not None else "")
    candidate = Path(text)
    if not candidate.is_absolute():
        raise PairToolError(f"{label} must be an absolute path.")
    try:
        resolved = candidate.resolve(strict=True)
        root = folder.resolve(strict=True)
    except OSError as exc:
        raise PairToolError(f"{label} does not resolve to an existing file inside its plan folder.") from exc
    if root not in resolved.parents or not resolved.is_file() or resolved.suffix.casefold() != ".json":
        raise PairToolError(f"{label} is outside its exact allowlisted plan folder.")
    return resolved


def output_json(completed: subprocess.CompletedProcess[str], label: str) -> dict[str, Any]:
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "no diagnostic output").strip()
        raise PairToolError(f"{label} failed (exit {completed.returncode}): {detail[:2000]}")
    try:
        value = json.loads((completed.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise PairToolError(f"{label} returned success without one valid JSON object.") from exc
    if not isinstance(value, dict):
        raise PairToolError(f"{label} returned success without one JSON object.")
    return value


def run_named_cli(command: list[str], label: str) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return output_json(completed, label)


def child_digest(path: Path, expected: str | None = None) -> str:
    child = read_json_object(path, "Child plan")
    saved = str(child.pop("sha256", ""))
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(child)):
        raise PairToolError(f"Child plan hash check failed: {path}")
    if expected is not None and not secrets.compare_digest(saved, expected):
        raise PairToolError(f"Child plan no longer matches the hash stored in the paired plan: {path}")
    return saved


def validate_child_plan_semantics(
    zoho_path: Path, woo_path: Path, paired: dict[str, Any]
) -> None:
    """Bind the reviewed pair evidence to the exact two child write plans."""
    zoho = read_json_object(zoho_path, "Zoho child plan")
    woo = read_json_object(woo_path, "WooCommerce child plan")
    before = paired["before"]
    after = paired["after"]
    identifiers = paired["identifiers"]
    new_sku = after["zoho"]["sku"]
    if (zoho.get("kind") != "item_name_sku"
            or zoho.get("payload") != {"name": after["zoho"]["name"], "sku": new_sku}
            or zoho.get("sources") != paired["sources"]
            or zoho.get("summary") != {
                "before": before["zoho"],
                "after": after["zoho"],
                "changed": {"sku": new_sku},
            }):
        raise PairToolError("Zoho child plan does not exactly match the paired SKU-only evidence.")
    if (woo.get("action") != "variation_update"
            or woo.get("method") != "PUT"
            or woo.get("endpoint") != (
                f"/products/{identifiers['woo_parent_id']}/variations/"
                f"{identifiers['woo_variation_id']}"
            )
            or woo.get("resource_id") != identifiers["woo_variation_id"]
            or woo.get("parent_id") != identifiers["woo_parent_id"]
            or woo.get("before") != {"sku": before["woocommerce"]["sku"]}
            or woo.get("payload") != {"sku": new_sku}
            or woo.get("sources") != paired["sources"]):
        raise PairToolError("WooCommerce child plan does not exactly match the paired SKU-only evidence.")


def write_child_input(value: dict[str, Any], prefix: str) -> Path:
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", suffix=".json",
        prefix=prefix, dir=PLAN_DIR, delete=False,
    )
    path = Path(handle.name)
    try:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    finally:
        handle.close()
    return path


def validate_stage_input(raw: dict[str, Any]) -> dict[str, Any]:
    if set(raw) != INPUT_FIELDS:
        missing = sorted(INPUT_FIELDS - set(raw))
        extra = sorted(set(raw) - INPUT_FIELDS)
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if extra:
            details.append("unsupported: " + ", ".join(extra))
        raise PairToolError("Input must contain exactly the allowlisted fields (" + "; ".join(details) + ").")
    sources = raw.get("sources")
    if not isinstance(sources, dict) or set(sources) != {"sku"}:
        raise PairToolError("sources must contain exactly sku.")
    return {
        "zoho_item_id": clean_positive_id(raw["zoho_item_id"], "zoho_item_id", as_text=True),
        "woo_parent_id": clean_positive_id(raw["woo_parent_id"], "woo_parent_id"),
        "woo_variation_id": clean_positive_id(raw["woo_variation_id"], "woo_variation_id"),
        "new_sku": clean_nonempty_text(raw["new_sku"], "new_sku", 100),
        "sources": {"sku": clean_nonempty_text(sources["sku"], "sources.sku", 2000)},
    }


def validate_zoho_stage_output(result: dict[str, Any], expected: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    if result.get("approval") != APPROVAL_WORD:
        raise PairToolError("Zoho child stage did not advertise the required one-word approval.")
    summary = result.get("summary")
    if not isinstance(summary, dict) or set(summary) != {"before", "after", "changed"}:
        raise PairToolError("Zoho child stage returned invalid before/after evidence.")
    before = summary.get("before")
    after = summary.get("after")
    changed = summary.get("changed")
    if (not isinstance(before, dict) or set(before) != {"item_id", "name", "sku"}
            or not isinstance(after, dict) or set(after) != {"item_id", "name", "sku"}):
        raise PairToolError("Zoho child stage returned invalid item evidence.")
    item_id = expected["zoho_item_id"]
    new_sku = expected["new_sku"]
    if (str(before.get("item_id")) != item_id or str(after.get("item_id")) != item_id
            or after.get("sku") != new_sku or before.get("sku") == new_sku
            or before.get("name") != after.get("name")
            or changed != {"sku": new_sku}):
        raise PairToolError("Zoho child stage evidence does not exactly match the requested SKU-only correction.")
    return contained_file(result.get("plan"), ZOHO_PLAN_DIR, "Zoho child plan"), summary


def validate_woo_stage_output(result: dict[str, Any], expected: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    parent_id = expected["woo_parent_id"]
    variation_id = expected["woo_variation_id"]
    new_sku = expected["new_sku"]
    if (result.get("status") != "STAGED_NOT_COMMITTED"
            or result.get("approval") != APPROVAL_WORD
            or result.get("action") != "variation_update"
            or result.get("parent_id") != parent_id
            or result.get("resource_id") != variation_id
            or result.get("payload") != {"sku": new_sku}
            or result.get("sources") != expected["sources"]
            or not isinstance(result.get("before"), dict)
            or set(result["before"]) != {"sku"}
            or result["before"].get("sku") == new_sku):
        raise PairToolError("WooCommerce child stage evidence does not exactly match the requested SKU-only correction.")
    return contained_file(result.get("plan"), WOO_PLAN_DIR, "WooCommerce child plan"), result


def stage_pair_plan(expected: dict[str, Any], zoho_result: dict[str, Any], woo_result: dict[str, Any]) -> Path:
    zoho_path, zoho_summary = validate_zoho_stage_output(zoho_result, expected)
    woo_path, woo_evidence = validate_woo_stage_output(woo_result, expected)
    zoho_sha = child_digest(zoho_path)
    woo_sha = child_digest(woo_path)
    created = utc_now()
    item_id = expected["zoho_item_id"]
    parent_id = expected["woo_parent_id"]
    variation_id = expected["woo_variation_id"]
    new_sku = expected["new_sku"]
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "approval_required": APPROVAL_WORD,
        "identifiers": {
            "zoho_item_id": item_id,
            "woo_parent_id": parent_id,
            "woo_variation_id": variation_id,
        },
        "before": {
            "zoho": dict(zoho_summary["before"]),
            "woocommerce": {
                "parent_id": parent_id,
                "variation_id": variation_id,
                "sku": woo_evidence["before"]["sku"],
            },
        },
        "after": {
            "zoho": dict(zoho_summary["after"]),
            "woocommerce": {
                "parent_id": parent_id,
                "variation_id": variation_id,
                "sku": new_sku,
            },
        },
        "sources": dict(expected["sources"]),
        "children": {
            "zoho": {"plan": str(zoho_path), "sha256": zoho_sha},
            "woocommerce": {"plan": str(woo_path), "sha256": woo_sha},
        },
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = (PLAN_DIR / f"{stamp}_paired_sku_{digest[:16]}.json").resolve()
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    zoho_tool.append_receipt("zoho_woo_sku_pair_plan_staged", f"plan={path}; sha256={digest}")
    return path


def command_stage(args: argparse.Namespace) -> None:
    raw = read_json_object(Path(args.input), "Input")
    expected = validate_stage_input(raw)
    zoho_input = write_child_input({
        "item_id": expected["zoho_item_id"],
        "new_sku": expected["new_sku"],
        "sources": expected["sources"],
    }, "zoho-sku-")
    woo_input = write_child_input({
        "action": "variation_update",
        "resource_id": expected["woo_variation_id"],
        "parent_id": expected["woo_parent_id"],
        "changes": {"sku": expected["new_sku"]},
        "sources": expected["sources"],
    }, "woo-sku-")
    try:
        zoho_result = run_named_cli([
            sys.executable, str(ZOHO_TOOL), "stage-name-sku", "--input", str(zoho_input)
        ], "Zoho child staging")
        woo_result = run_named_cli([
            sys.executable, str(WOO_TOOL), "stage", "--input", str(woo_input)
        ], "WooCommerce child staging")
        path = stage_pair_plan(expected, zoho_result, woo_result)
    except Exception as exc:
        raise PairToolError(
            "Paired staging failed explicitly; no commit command was invoked. " + str(exc)
        ) from exc
    finally:
        for child_input in (zoho_input, woo_input):
            try:
                child_input.unlink()
            except FileNotFoundError:
                pass
    plan = read_json_object(path, "Paired plan")
    print(json.dumps({
        "status": "PAIRED_STAGED_NOT_COMMITTED",
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "identifiers": plan["identifiers"],
        "before": plan["before"],
        "after": plan["after"],
        "sources": plan["sources"],
        "children": plan["children"],
        "approval": APPROVAL_WORD,
    }, ensure_ascii=False, indent=2))


def parse_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise PairToolError(f"Paired plan {label} is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PairToolError(f"Paired plan {label} must be timezone-aware.")
    return parsed


def validate_pair_schema(plan: dict[str, Any]) -> None:
    if set(plan) != PLAN_FIELDS:
        raise PairToolError("Paired plan schema is not closed or is missing required fields.")
    if (plan.get("schema_version") != SCHEMA_VERSION or plan.get("tool") != TOOL_NAME
            or plan.get("approval_required") != APPROVAL_WORD):
        raise PairToolError("Paired plan schema, tool, or approval requirement is invalid.")
    if not NONCE_RE.fullmatch(str(plan.get("nonce") or "")):
        raise PairToolError("Paired plan nonce is invalid.")
    created = parse_time(plan.get("created_utc"), "creation time")
    expires = parse_time(plan.get("expires_utc"), "expiry")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise PairToolError("Paired plan must have exactly a 24-hour lifetime.")
    if utc_now() >= expires:
        raise PairToolError("Paired plan expired. Stage a new paired plan for review.")

    identifiers = plan.get("identifiers")
    if not isinstance(identifiers, dict) or set(identifiers) != {
        "zoho_item_id", "woo_parent_id", "woo_variation_id"
    }:
        raise PairToolError("Paired plan identifiers are invalid.")
    item_id = clean_positive_id(identifiers["zoho_item_id"], "zoho_item_id", as_text=True)
    parent_id = clean_positive_id(identifiers["woo_parent_id"], "woo_parent_id")
    variation_id = clean_positive_id(identifiers["woo_variation_id"], "woo_variation_id")
    if identifiers != {
        "zoho_item_id": item_id,
        "woo_parent_id": parent_id,
        "woo_variation_id": variation_id,
    }:
        raise PairToolError("Paired plan identifiers are not canonical.")

    sources = plan.get("sources")
    if not isinstance(sources, dict) or set(sources) != {"sku"}:
        raise PairToolError("Paired plan sources must contain exactly sku.")
    clean_nonempty_text(sources["sku"], "sources.sku", 2000)

    before = plan.get("before")
    after = plan.get("after")
    if (not isinstance(before, dict) or set(before) != {"zoho", "woocommerce"}
            or not isinstance(after, dict) or set(after) != {"zoho", "woocommerce"}):
        raise PairToolError("Paired plan before/after evidence is invalid.")
    zoho_before = before.get("zoho")
    zoho_after = after.get("zoho")
    woo_before = before.get("woocommerce")
    woo_after = after.get("woocommerce")
    if (not isinstance(zoho_before, dict) or set(zoho_before) != {"item_id", "name", "sku"}
            or not isinstance(zoho_after, dict) or set(zoho_after) != {"item_id", "name", "sku"}
            or not isinstance(woo_before, dict) or set(woo_before) != {"parent_id", "variation_id", "sku"}
            or not isinstance(woo_after, dict) or set(woo_after) != {"parent_id", "variation_id", "sku"}):
        raise PairToolError("Paired plan exact before/after fields are invalid.")
    new_sku = clean_nonempty_text(zoho_after.get("sku"), "after SKU", 100)
    if (zoho_before.get("item_id") != item_id or zoho_after.get("item_id") != item_id
            or not isinstance(zoho_before.get("name"), str)
            or not zoho_before.get("name")
            or not isinstance(zoho_after.get("name"), str)
            or not isinstance(zoho_before.get("sku"), str)
            or not isinstance(woo_before.get("sku"), str)
            or zoho_before.get("name") != zoho_after.get("name")
            or zoho_before.get("sku") == new_sku
            or woo_before.get("parent_id") != parent_id
            or woo_after.get("parent_id") != parent_id
            or woo_before.get("variation_id") != variation_id
            or woo_after.get("variation_id") != variation_id
            or woo_before.get("sku") == new_sku
            or woo_after.get("sku") != new_sku):
        raise PairToolError("Paired plan before/after evidence does not match one SKU-only correction.")

    children = plan.get("children")
    if not isinstance(children, dict) or set(children) != {"zoho", "woocommerce"}:
        raise PairToolError("Paired plan children are invalid.")
    for label in ("zoho", "woocommerce"):
        child = children.get(label)
        if (not isinstance(child, dict) or set(child) != {"plan", "sha256"}
                or not HEX_64_RE.fullmatch(str(child.get("sha256") or ""))):
            raise PairToolError(f"Paired plan {label} child reference is invalid.")
        if not Path(str(child.get("plan") or "")).is_absolute():
            raise PairToolError(f"Paired plan {label} child path must be absolute.")


def load_pair_plan(path: Path) -> dict[str, Any]:
    plan = read_json_object(path, "Paired plan")
    saved = str(plan.get("sha256") or "")
    core = dict(plan)
    core.pop("sha256", None)
    if not HEX_64_RE.fullmatch(saved) or not secrets.compare_digest(saved, digest_for(core)):
        raise PairToolError("Paired plan hash check failed. The plan changed after review.")
    validate_pair_schema(plan)
    return plan


def pair_lock_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".pair-commit-lock.json")


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError:
        # A4: what the existing record says decides -- spent, or needs re-stage.
        owner_authority.refuse_replay(PairToolError, owner_authority.read_json_if_exists(path),
                                      what="paired plan")
        raise  # unreachable
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def validate_zoho_commit_output(result: dict[str, Any], plan: dict[str, Any]) -> None:
    expected = plan["after"]["zoho"]
    if (result.get("updated") != "item_name_sku"
            or str(result.get("item_id")) != expected["item_id"]
            or result.get("name") != expected["name"]
            or result.get("sku") != expected["sku"]):
        raise PairToolError("Zoho child commit output did not verify the exact approved item name and new SKU.")


def validate_woo_commit_output(result: dict[str, Any], plan: dict[str, Any]) -> None:
    expected = plan["after"]["woocommerce"]
    child_sha = plan["children"]["woocommerce"]["sha256"]
    if (result.get("status") != "COMMITTED_AND_VERIFIED"
            or result.get("action") != "variation_update"
            or result.get("resource_id") != expected["variation_id"]
            or result.get("approved_payload") != {"sku": expected["sku"]}
            or result.get("plan_sha256") != child_sha
            or result.get("replay_locked") is not True):
        raise PairToolError("WooCommerce child commit output did not verify the exact approved new SKU.")


def command_commit(args: argparse.Namespace) -> None:
    plan_path = contained_file(args.plan, PLAN_DIR, "Paired plan")
    plan = load_pair_plan(plan_path)
    go = require_rachad_approval(args.approval, getattr(args, "approval_lane", None))
    # The lane his word arrived on is forwarded to both children so each
    # writer's own lock and receipt record it (A5). --approval-message-utc is
    # NOT forwarded: both children are REVERSIBLE writers whose parsers do not
    # take it (they would refuse the whole command line), and this coordinator's
    # own parser does not define it either.
    lane_args = ["--approval-lane", go.lane] if getattr(args, "approval_lane", None) else []

    zoho_child = contained_file(plan["children"]["zoho"]["plan"], ZOHO_PLAN_DIR, "Zoho child plan")
    woo_child = contained_file(
        plan["children"]["woocommerce"]["plan"], WOO_PLAN_DIR, "WooCommerce child plan"
    )
    child_digest(zoho_child, plan["children"]["zoho"]["sha256"])
    child_digest(woo_child, plan["children"]["woocommerce"]["sha256"])
    validate_child_plan_semantics(zoho_child, woo_child, plan)

    lock = pair_lock_path(plan_path)
    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_IN_FLIGHT, plan_sha256=plan["sha256"], action="sku_pair", go=go,
        started_utc=utc_now().isoformat(),
    ), exclusive=True)
    completed_children: list[str] = []
    try:
        zoho_result = run_named_cli([
            sys.executable, str(ZOHO_TOOL), "commit-name-sku", "--plan", str(zoho_child),
            "--approval", str(args.approval), *lane_args,
        ], "Zoho child commit")
        validate_zoho_commit_output(zoho_result, plan)
        completed_children.append("zoho")

        woo_result = run_named_cli([
            sys.executable, str(WOO_TOOL), "commit", "--plan", str(woo_child),
            "--approval", str(args.approval), *lane_args,
        ], "WooCommerce child commit")
        validate_woo_commit_output(woo_result, plan)
        completed_children.append("woocommerce")
    except Exception as exc:
        # A child that did not verify may still have landed: the outcome is
        # indeterminate; the re-stage reads both systems and shows what did.
        status = owner_authority.STATUS_INDETERMINATE
        half = ("partial: the " + " and ".join(completed_children) + " child landed; the other did not"
                if completed_children else "neither child verified")
        write_lock(lock, owner_authority.attempt_record(
            status, plan_sha256=plan["sha256"], action="sku_pair", go=go, reason=str(exc),
            completed_children=completed_children, completed=completed_children,
        ))
        zoho_tool.append_receipt(
            "zoho_woo_sku_pair_indeterminate_needs_restage",
            f"status={status}; completed={','.join(completed_children) or 'none'}; "
            f"plan={plan_path}; sha256={plan['sha256']}",
        )
        raise PairToolError(
            owner_authority.explain_outcome(
                "Paired commit", status,
                f"{half}. Reconcile both systems, then re-stage the pair; a landed half keeps its "
                f"writer's own restore route. Reason: {exc}",
            )
        ) from exc

    new_sku = plan["after"]["zoho"]["sku"]
    write_lock(lock, owner_authority.attempt_record(
        owner_authority.STATUS_COMMITTED, plan_sha256=plan["sha256"], action="sku_pair", go=go,
        completed_children=completed_children, completed=completed_children, new_sku=new_sku,
        restore="each writer's own route: zoho_inventory_item_tool restore-name-sku and woocommerce_change_tool restore",
    ))
    zoho_tool.append_receipt(
        "zoho_woo_sku_pair_committed_verified",
        f"zoho_item_id={plan['identifiers']['zoho_item_id']}; "
        f"woo_parent_id={plan['identifiers']['woo_parent_id']}; "
        f"woo_variation_id={plan['identifiers']['woo_variation_id']}; "
        f"sku={new_sku}; plan={plan_path}; sha256={plan['sha256']}",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED_BOTH",
        "plan_sha256": plan["sha256"],
        "identifiers": plan["identifiers"],
        "verified": {
            "zoho": {"sku": new_sku},
            "woocommerce": {"sku": new_sku},
        },
        "replay_locked": True,
        "plan_spent": True,
        "restore": ("each writer's own route: zoho_inventory_item_tool restore-name-sku and "
                    "woocommerce_change_tool restore, on his go"),
    }, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--input", required=True)
    stage.set_defaults(func=command_stage)
    commit = commands.add_parser("commit")
    commit.add_argument("--plan", required=True)
    owner_authority.add_owner_go_arguments(commit)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (PairToolError, OSError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
