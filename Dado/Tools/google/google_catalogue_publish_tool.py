#!/usr/bin/env python3
"""FRP Depot fixed 2026 catalogue Drive publisher.

Commissioned directly by Rachad Homsi on 2026-08-15.

This tool can replace only the bytes of one existing Google Drive file:
FRP Depots Catalogue 2026.pdf / 1PqcjZf-SSCbBVp7quMri_ernaOPZDPz1.
It can upload only one pinned, reviewed local PDF digest. It cannot create,
delete, copy, rename, move, share, change permissions, publish another file,
mail anything, or use a browser. Stage is read-only against Google. Commit
requires a 24-hour immutable plan and Rachad's exact unpadded uppercase
APPROVED. The one media update is attempted once and then read back byte for
byte; any failure or uncertain verification permanently locks the plan.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any

import fitz
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

import google_investments_auth as auth

TOOL_NAME = "FRP Depot Fixed Catalogue Drive Publisher"
TOOL_VERSION = "1.2.0"
SCHEMA_VERSION = 3
ACTION = "replace_exact_catalogue_pdf"
APPROVAL_WORD = "APPROVED"
PLAN_LIFETIME_HOURS = 24
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "catalogue_publish_plans"
RESULT_DIR = ROOT / "Dado" / "20_Working" / "catalogue_publish_results"
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"
BACKUP_DIR = auth.VAULT / "catalogue_backups"

EXPECTED_FILE_ID = "1PqcjZf-SSCbBVp7quMri_ernaOPZDPz1"
EXPECTED_FILE_NAME = "FRP Depots Catalogue 2026.pdf"
EXPECTED_MIME_TYPE = "application/pdf"
EXPECTED_PARENT_IDS = (
    "0ACKbTL9Q6AISUk9PVA",
    "1y-WtVIKC0APKhFN_GjEC5wBRjsLODzhQ",
    "14JcHCth2XM1968eQn1hA4jGKquQqDxMx",
    "12C-CPb_1PWt-WHTQOd3PDLeJ_IV9zSdw",
    "1mxDSbwWCZVP68RKT8f4_oEvHARw0fnrP",
    "1UrmmLbRDu_5hSpMe234A5IZa1xYFLtyl",
)
EXPECTED_PARENT_PATH = (
    "My Drive",
    "My Files",
    "Rachad",
    "Bussiness Folder",
    "FRPDEPOT INC.",
    "Specs & Catalog",
)
ARTIFACT_PATH = ROOT / "Dado" / "20_Working" / "catalogue_image_revision_20260815" / "FRP_Depots_Catalogue_2026_image_revised.pdf"
EXPECTED_ARTIFACT_RESOLVED = str(ARTIFACT_PATH)
EXPECTED_ARTIFACT_SHA256 = "60bf4a5fcc19246f2d782608df145b06c83275fd30cec2ba7b3506b2c7382fb3"
EXPECTED_ARTIFACT_MD5 = "b48cff0b570cf68e4249802eebce57a0"
EXPECTED_ARTIFACT_BYTES = 15429789
EXPECTED_PAGES = 9
EXPECTED_PAGE_HEADINGS = (
    "FRP Pipe & Fittings",
    "Stocked in Canada. Shipped in Days.",
    "Resins, Laminate & Standards",
    "FRP Stub Flanges",
    "FRP Manways & Covers",
    "FRP 90° Elbows",
    "FRP Filament-Wound Pipe",
    "FRP FNPT Couplings",
    "How to order",
)
SOURCE = "Rachad Homsi Discord instruction on 2026-08-15 to publish the visually approved catalogue"
SUPERSEDED_PLAN_SHA256 = {
    "aaf4c8c2549a65a17576f1609da77780057a392b0e0f1bd1eb21ac2326d467b6",
}
WRITE_CONTRACT = {
    "remote_requests": 1,
    "remote_route": "Drive v2 files.update media-only (HTTP PUT) on the exact existing file",
    "atomicity": "one content-replacement request; verification follows separately",
    "attempts": 1,
    "retry": False,
    "rollback_route": False,
    "email_or_notification": False,
    "preserves": ["file_id", "name", "mime_type", "parent_path", "share_links"],
    "forbids": ["create", "delete", "copy", "rename", "move", "permissions", "sharing", "mail", "browser"],
}
SECRET_RE = re.compile(
    r"(?i)(?:ya29\.[A-Za-z0-9._-]+|access[_-]?token[=: ]+[^\s,}]+|refresh[_-]?token[=: ]+[^\s,}]+)"
)


class CataloguePublishError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()  # noqa: S324 - Drive metadata comparison only


def scrub(value: str) -> str:
    return SECRET_RE.sub("[REDACTED]", str(value))


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": utc_now().isoformat(), "action": action, "evidence": evidence}
    with RECEIPTS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def pdf_projection(data: bytes) -> dict[str, Any]:
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise CataloguePublishError("The catalogue artifact is not a readable PDF.") from exc
    try:
        if document.needs_pass:
            raise CataloguePublishError("The catalogue PDF is encrypted.")
        if len(document) != EXPECTED_PAGES:
            raise CataloguePublishError(f"The catalogue PDF must contain exactly {EXPECTED_PAGES} pages.")
        for index, heading in enumerate(EXPECTED_PAGE_HEADINGS):
            normalized = " ".join(document[index].get_text("text").split())
            if heading not in normalized:
                raise CataloguePublishError(
                    f"Catalogue page {index + 1} is missing the fixed heading {heading!r}."
                )
        return {"pages": len(document), "page_headings": list(EXPECTED_PAGE_HEADINGS)}
    finally:
        document.close()


def expected_artifact_projection() -> dict[str, Any]:
    return {
        "path": str(ARTIFACT_PATH),
        "mime_type": EXPECTED_MIME_TYPE,
        "bytes": EXPECTED_ARTIFACT_BYTES,
        "sha256": EXPECTED_ARTIFACT_SHA256,
        "md5": EXPECTED_ARTIFACT_MD5,
        "pages": EXPECTED_PAGES,
        "page_headings": list(EXPECTED_PAGE_HEADINGS),
    }


def artifact_projection() -> tuple[dict[str, Any], bytes]:
    if not ARTIFACT_PATH.is_file():
        raise CataloguePublishError(f"The pinned catalogue artifact is missing: {ARTIFACT_PATH}")
    if str(ARTIFACT_PATH.resolve()).casefold() != EXPECTED_ARTIFACT_RESOLVED.casefold():
        raise CataloguePublishError("The catalogue artifact path resolves outside the exact pinned location.")
    data = ARTIFACT_PATH.read_bytes()
    projection = {
        "path": str(ARTIFACT_PATH),
        "mime_type": EXPECTED_MIME_TYPE,
        "bytes": len(data),
        "sha256": sha256_bytes(data),
        "md5": md5_bytes(data),
        **pdf_projection(data),
    }
    if projection != expected_artifact_projection():
        raise CataloguePublishError(
            "The local catalogue artifact differs from the exact visually approved PDF. Rebuild and restage."
        )
    return projection, data


def _file_metadata(service) -> dict[str, Any]:
    return service.files().get(
        fileId=EXPECTED_FILE_ID,
        supportsAllDrives=True,
        fields=(
            "id,title,mimeType,parents(id),md5Checksum,modifiedDate,version,"
            "editable,labels(trashed),downloadUrl,etag,fileSize,alternateLink,webContentLink"
        ),
    ).execute(num_retries=3)


def _parent_identity(service, item: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    parents = list(item.get("parents") or [])
    if len(parents) != 1:
        raise CataloguePublishError("The catalogue must have exactly one parent folder.")
    names: list[str] = []
    identifiers: list[str] = []
    seen: set[str] = set()
    current = str((parents[0] or {}).get("id") or "")
    while current:
        if current in seen or len(names) > 12:
            raise CataloguePublishError("The catalogue parent path is cyclic or unexpectedly deep.")
        seen.add(current)
        parent = service.files().get(
            fileId=current, supportsAllDrives=True, fields="id,title,parents(id)"
        ).execute(num_retries=3)
        names.append(str(parent.get("title") or ""))
        identifiers.append(str(parent.get("id") or ""))
        next_parents = list(parent.get("parents") or [])
        if len(next_parents) > 1:
            raise CataloguePublishError("A catalogue parent folder has multiple parents.")
        current = str((next_parents[0] or {}).get("id") or "") if next_parents else ""
    return tuple(reversed(identifiers)), tuple(reversed(names))


def download_bytes(service) -> bytes:
    target = io.BytesIO()
    request = service.files().get_media(fileId=EXPECTED_FILE_ID, supportsAllDrives=True)
    downloader = MediaIoBaseDownload(target, request)
    done = False
    while not done:
        _, done = downloader.next_chunk(num_retries=3)
    return target.getvalue()


def live_projection(service) -> tuple[dict[str, Any], bytes]:
    item = _file_metadata(service)
    parent_ids, parent_path = _parent_identity(service, item)
    if (
        str(item.get("id") or "") != EXPECTED_FILE_ID
        or str(item.get("title") or "") != EXPECTED_FILE_NAME
        or str(item.get("mimeType") or "") != EXPECTED_MIME_TYPE
        or parent_ids != EXPECTED_PARENT_IDS
        or parent_path != EXPECTED_PARENT_PATH
    ):
        raise CataloguePublishError("The live Drive item is not the exact commissioned catalogue file and path.")
    if not item.get("editable"):
        raise CataloguePublishError("Google reports that the catalogue file is not editable.")
    if bool((item.get("labels") or {}).get("trashed")):
        raise CataloguePublishError("The catalogue file is in Drive trash.")
    for field, label in (("etag", "ETag"), ("downloadUrl", "download URL"),
                         ("alternateLink", "view link"), ("webContentLink", "download link")):
        if not str(item.get(field) or ""):
            raise CataloguePublishError(f"Google did not return the catalogue {label}.")
    data = download_bytes(service)
    if str(len(data)) != str(item.get("fileSize") or ""):
        raise CataloguePublishError("The live catalogue byte count differs from Drive metadata.")
    if md5_bytes(data) != str(item.get("md5Checksum") or ""):
        raise CataloguePublishError("The live catalogue bytes differ from Drive's MD5 metadata.")
    pdf = pdf_projection(data)
    projection = {
        "id": EXPECTED_FILE_ID,
        "name": EXPECTED_FILE_NAME,
        "mime_type": EXPECTED_MIME_TYPE,
        "parent_ids": list(parent_ids),
        "parent_path": list(parent_path),
        "editable": True,
        "trashed": False,
        "etag": str(item["etag"]),
        "md5": md5_bytes(data),
        "sha256": sha256_bytes(data),
        "bytes": len(data),
        "modified_time": str(item.get("modifiedDate") or ""),
        "version": str(item.get("version") or ""),
        "alternate_link": str(item["alternateLink"]),
        "web_content_link": str(item["webContentLink"]),
        **pdf,
    }
    if not projection["modified_time"] or not projection["version"]:
        raise CataloguePublishError("Drive did not return catalogue version metadata.")
    return projection, data


def eligibility(live: dict[str, Any], artifact: dict[str, Any],
                planned_before: dict[str, Any] | None = None) -> None:
    """One predicate used by stage, commit preflight and the final write adapter."""
    if artifact != expected_artifact_projection():
        raise CataloguePublishError("The proposed upload is not the pinned reviewed catalogue artifact.")
    fixed = {
        "id": EXPECTED_FILE_ID,
        "name": EXPECTED_FILE_NAME,
        "mime_type": EXPECTED_MIME_TYPE,
        "parent_ids": list(EXPECTED_PARENT_IDS),
        "parent_path": list(EXPECTED_PARENT_PATH),
        "editable": True,
        "trashed": False,
        "pages": EXPECTED_PAGES,
        "page_headings": list(EXPECTED_PAGE_HEADINGS),
    }
    for key, expected in fixed.items():
        if live.get(key) != expected:
            raise CataloguePublishError(f"The live catalogue failed the fixed {key} eligibility check.")
    for key in ("etag", "md5", "sha256", "bytes", "modified_time", "version",
                "alternate_link", "web_content_link"):
        if live.get(key) in (None, ""):
            raise CataloguePublishError(f"The live catalogue has no usable {key} fingerprint.")
    if live["sha256"] == artifact["sha256"]:
        raise CataloguePublishError("The approved catalogue is already live; no replacement is needed.")
    if planned_before is not None and live != planned_before:
        raise CataloguePublishError("The live catalogue changed after staging. Stage a new plan.")


def build_plan(live: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    eligibility(live, artifact)
    created = utc_now()
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "action": ACTION,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "target": {
            "id": EXPECTED_FILE_ID,
            "name": EXPECTED_FILE_NAME,
            "mime_type": EXPECTED_MIME_TYPE,
            "parent_ids": list(EXPECTED_PARENT_IDS),
            "parent_path": list(EXPECTED_PARENT_PATH),
        },
        "before": live,
        "artifact": artifact,
        "source": SOURCE,
        "write_contract": copy.deepcopy(WRITE_CONTRACT),
    }
    return {**core, "sha256": digest_for(core)}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CataloguePublishError("The publication plan is unreadable.") from exc
    if not isinstance(value, dict):
        raise CataloguePublishError("The publication plan must contain one JSON object.")
    return value


def load_plan(path: Path) -> dict[str, Any]:
    plan = read_json(path)
    saved = str(plan.pop("sha256", ""))
    if saved in SUPERSEDED_PLAN_SHA256:
        raise CataloguePublishError("This unapproved catalogue publication plan was superseded and cannot commit.")
    if not re.fullmatch(r"[0-9a-f]{64}", saved) or not secrets.compare_digest(saved, digest_for(plan)):
        raise CataloguePublishError("Plan hash check failed. The publication plan changed after review.")
    expected_keys = {
        "schema_version", "tool", "tool_version", "action", "created_utc", "expires_utc",
        "nonce", "target", "before", "artifact", "source", "write_contract",
    }
    if set(plan) != expected_keys:
        raise CataloguePublishError("Plan fields do not match the closed publication schema.")
    if (
        plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("tool") != TOOL_NAME
        or plan.get("tool_version") != TOOL_VERSION
        or plan.get("action") != ACTION
    ):
        raise CataloguePublishError("Plan schema, tool, version, or action is invalid.")
    try:
        created = datetime.fromisoformat(str(plan["created_utc"]))
        expires = datetime.fromisoformat(str(plan["expires_utc"]))
    except (ValueError, TypeError) as exc:
        raise CataloguePublishError("Plan creation or expiry time is invalid.") from exc
    if created.tzinfo is None or expires.tzinfo is None:
        raise CataloguePublishError("Plan times must include a timezone.")
    if expires - created != timedelta(hours=PLAN_LIFETIME_HOURS):
        raise CataloguePublishError("Plan lifetime must be exactly 24 hours.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise CataloguePublishError("Plan creation time is in the future.")
    if now >= expires:
        raise CataloguePublishError("Plan expired. Stage a new plan.")
    if not re.fullmatch(r"[0-9a-f]{32}", str(plan.get("nonce") or "")):
        raise CataloguePublishError("Plan nonce is invalid.")
    expected_target = {
        "id": EXPECTED_FILE_ID,
        "name": EXPECTED_FILE_NAME,
        "mime_type": EXPECTED_MIME_TYPE,
        "parent_ids": list(EXPECTED_PARENT_IDS),
        "parent_path": list(EXPECTED_PARENT_PATH),
    }
    if plan.get("target") != expected_target:
        raise CataloguePublishError("Plan does not target the exact commissioned catalogue file.")
    if plan.get("artifact") != expected_artifact_projection():
        raise CataloguePublishError("Plan does not contain the exact approved catalogue artifact.")
    if plan.get("source") != SOURCE or plan.get("write_contract") != WRITE_CONTRACT:
        raise CataloguePublishError("Plan publication source or write contract changed.")
    before = plan.get("before")
    if not isinstance(before, dict):
        raise CataloguePublishError("Plan live-state fingerprint is invalid.")
    required_before = {
        "id", "name", "mime_type", "parent_ids", "parent_path", "editable", "etag", "md5",
        "trashed", "sha256", "bytes", "modified_time", "version", "alternate_link", "web_content_link",
        "pages", "page_headings",
    }
    if set(before) != required_before:
        raise CataloguePublishError("Plan live-state fingerprint fields changed.")
    eligibility(before, plan["artifact"])
    plan["sha256"] = saved
    return plan


def lock_path(plan_digest: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", str(plan_digest)):
        raise CataloguePublishError("Plan digest is invalid for replay locking.")
    return PLAN_DIR / ".commit-locks" / f"{plan_digest}.json"


def write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CataloguePublishError("This publication plan has already entered commit and cannot be replayed.") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def overwrite_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def perform_one_update(service, live: dict[str, Any], artifact: dict[str, Any],
                       artifact_bytes: bytes, plan: dict[str, Any], lock: Path,
                       lock_record: dict[str, Any]) -> dict[str, Any]:
    """The only side-effect adapter. It reuses the shared eligibility predicate."""
    eligibility(live, artifact, plan["before"])
    media = MediaIoBaseUpload(io.BytesIO(artifact_bytes), mimetype=EXPECTED_MIME_TYPE, resumable=False)
    request = service.files().update(
        fileId=EXPECTED_FILE_ID,
        media_body=media,
        supportsAllDrives=True,
        fields="id,title,mimeType,parents(id),md5Checksum,modifiedDate,version,etag,alternateLink,webContentLink",
    )
    request.headers["If-Match"] = str(live["etag"])
    write_json_exclusive(lock, lock_record)
    return request.execute(num_retries=0)


def verify_after(before: dict[str, Any], after: dict[str, Any], artifact: dict[str, Any]) -> None:
    for key in ("id", "name", "mime_type", "parent_ids", "parent_path", "editable", "trashed",
                "alternate_link", "web_content_link"):
        if after.get(key) != before.get(key):
            raise CataloguePublishError(f"Protected catalogue field {key} changed during publication.")
    for key in ("sha256", "md5", "bytes", "pages", "page_headings"):
        artifact_key = "md5" if key == "md5" else key
        if after.get(key) != artifact.get(artifact_key):
            raise CataloguePublishError(f"Live catalogue readback failed the {key} verification.")
    for key in ("etag", "modified_time", "version"):
        if after.get(key) == before.get(key):
            raise CataloguePublishError(f"Drive did not advance catalogue {key} after the conditional media PUT.")


def command_stage(_args: argparse.Namespace) -> None:
    artifact, _ = artifact_projection()
    service = auth.drive_service()
    live, _ = live_projection(service)
    eligibility(live, artifact)
    plan = build_plan(live, artifact)
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{ACTION}_{plan['sha256'][:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_receipt("catalogue_drive_publication_plan_staged_read_only", str(path))
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED",
        "plan": str(path),
        "plan_sha256": plan["sha256"],
        "expires_utc": plan["expires_utc"],
        "target": plan["target"],
        "before": plan["before"],
        "artifact": plan["artifact"],
        "write_contract": plan["write_contract"],
        "approval": APPROVAL_WORD,
        "remote_write_performed": False,
    }, indent=2, ensure_ascii=False))


def command_commit(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve()
    if PLAN_DIR.resolve() not in plan_path.parents:
        raise CataloguePublishError("Plan must be inside Dado's catalogue publication plan folder.")
    plan = load_plan(plan_path)
    if (
        not isinstance(args.approval, str)
        or not args.approval.isascii()
        or not secrets.compare_digest(args.approval, APPROVAL_WORD)
    ):
        raise CataloguePublishError("Rachad must reply with exact unpadded uppercase APPROVED.")
    lock = lock_path(str(plan["sha256"]))
    if lock.exists():
        raise CataloguePublishError("This publication plan has already entered commit and cannot be replayed.")

    artifact, artifact_bytes = artifact_projection()
    if artifact != plan["artifact"]:
        raise CataloguePublishError("The local approved catalogue changed after staging.")
    service = auth.drive_service()
    live, current_bytes = live_projection(service)
    eligibility(live, artifact, plan["before"])

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / (
        utc_now().strftime("%Y%m%dT%H%M%SZ_") + str(live["sha256"]) + "_FRP_Depots_Catalogue_2026.pdf"
    )
    backup.write_bytes(current_bytes)
    lock_record = {
        "plan_sha256": plan["sha256"],
        "status": "in_flight",
        "started_utc": utc_now().isoformat(),
        "backup": str(backup),
        "attempts_allowed": 1,
        "rollback_route": False,
    }

    try:
        perform_one_update(
            service, live, artifact, artifact_bytes, plan, lock, lock_record
        )
        after, readback = live_projection(service)
        if not secrets.compare_digest(sha256_bytes(readback), artifact["sha256"]):
            raise CataloguePublishError("The downloaded live PDF is not byte-identical to the approved artifact.")
        verify_after(live, after, artifact)
    except Exception as exc:
        if not lock.exists():
            raise
        authoritative_no_write = int(
            getattr(getattr(exc, "resp", None), "status", 0) or 0
        ) == 412
        lock_status = (
            "failed_closed_no_write_authoritative"
            if authoritative_no_write else "indeterminate_no_retry"
        )
        overwrite_json(lock, {
            "plan_sha256": plan["sha256"],
            "status": lock_status,
            "updated_utc": utc_now().isoformat(),
            "backup": str(backup),
            "reason": scrub(str(exc)),
            "attempts_allowed": 1,
            "rollback_route": False,
        })
        append_receipt(
            (
                "catalogue_drive_publication_failed_closed_no_write"
                if authoritative_no_write else
                "catalogue_drive_publication_indeterminate_no_retry"
            ),
            f"plan={plan_path}; sha256={plan['sha256']}; backup={backup}",
        )
        if authoritative_no_write:
            raise CataloguePublishError(
                "Google authoritatively rejected the conditional media PUT because the live file changed. No Drive write occurred; the plan is permanently locked."
            ) from exc
        raise CataloguePublishError(
            "The Drive catalogue media PUT failed or could not be fully verified. The plan is permanently locked and will not retry."
        ) from exc

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    result_path = RESULT_DIR / f"{plan['sha256']}_committed_verified.json"
    result = {
        "status": "COMMITTED_AND_VERIFIED",
        "completed_utc": utc_now().isoformat(),
        "plan": str(plan_path),
        "plan_sha256": plan["sha256"],
        "target": plan["target"],
        "before": live,
        "after": after,
        "artifact": artifact,
        "downloaded_live_sha256": sha256_bytes(readback),
        "replay_locked": True,
        "backup": str(backup),
        "remote_write_requests": 1,
        "email_sent": False,
        "website_writes": 0,
        "zoho_writes": 0,
    }
    overwrite_json(result_path, result)
    overwrite_json(lock, {
        "plan_sha256": plan["sha256"],
        "status": "committed_verified",
        "updated_utc": utc_now().isoformat(),
        "backup": str(backup),
        "result": str(result_path),
        "new_sha256": after["sha256"],
        "new_version": after["version"],
        "attempts_allowed": 1,
        "rollback_route": False,
    })
    append_receipt("catalogue_drive_publication_committed_verified", str(result_path))
    print(json.dumps(result, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.set_defaults(func=command_stage)
    commit = commands.add_parser("commit")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (CataloguePublishError, auth.InvestmentsAuthError, OSError, ValueError) as exc:
        print("ERROR: " + scrub(str(exc)), file=sys.stderr)
        return 1
    except Exception as exc:
        print("ERROR: Catalogue publisher failed safely: " + scrub(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
