#!/usr/bin/env python
"""FRP Depot fixed FRP MANWAY (product 1397) gallery recovery tool.

Commissioned by Rachad Homsi on 2026-08-21 after the approved six-image
open-manway media plan `1c7865b0...` permanently locked INDETERMINATE at
`upload_2`. He chose "Proceed with fixed recovery tool (recommended)". That
authorizes this build and the later read-only staging workflow. It is NOT
approval for any live write, and no plan is staged by building it.

The one fixed operation: finish the approved six-image gallery on the EXISTING
WooCommerce product 1397 (exact live identity `FRP MANWAY`) after reconciling
the known indeterminate prior operation. Upload 1 verifiably landed as
attachment 7609; upload 2 is ambiguous and may or may not have landed; uploads
3-6 were never attempted. Recovery therefore REUSES whatever is already live,
uploads only the missing subset, and makes one images-only gallery PUT.

Commands:
    stage
    commit --plan PATH --approval APPROVED

The prior plan, attempt, event journal, result and receipt are permanent
evidence. This tool hashes them and refuses on any drift; it never repairs,
normalizes, relabels, rewrites, retries or unlocks them, and it owns entirely
separate plan/registry/attempt/event/result state so the old lock can never be
bypassed.

*** VERSION 2.0.0 (2026-08-21) -- WHAT THE INDEPENDENT AUDIT FORCED. ***
Version 1.0.0 was fail-closed but its dormant commit path could not have worked.
Six defects were found and every one of them is now an executable test:

1. THE GALLERY PUT COULD NEVER SATISFY GUARD OWNERSHIP. v1 sent it through
   `wc.api_request()`, which carries Basic WooCommerce credentials and no
   WordPress user, session token or guard cookie -- and the guard cookie is
   deliberately scoped to `/wp-admin/`, so even a normal cookie jar would not
   send it to `/wp-json/`. It would have received `frpd_mg_gallery_owner`/403
   AFTER the uploads and the permanent no-retry lock. There is NO
   `wc.api_request` call in this module any more. The gallery mutation happens
   through Guard 1.0.7's one fixed authenticated `admin_post` form, submitted in
   the guard-owning browser, so the existing
   `woocommerce_rest_pre_insert_product_object` filter sees the real owner.
2. ORIGIN-ONLY COLLISION ABSENCE IS NOW PROVEN BY THE GUARD. v1 declared the
   capability unsupported, never checked that flag, and invented public URL
   probes against one hard-coded month directory instead. A public 404 is not
   proof about the server filesystem. The proof now comes from the plugin's own
   bounded, fail-closed, uploads-directory enumeration.
3. THE ELIGIBILITY PREDICATE IS FRESH AND COMPLETE. v1 re-ran it against frozen
   preflight objects, never re-read live state between uploads, and
   `complete_guard()` did not call it at all. It now takes the guard capability,
   guard-owner state, complete library evidence, origin proof and reserved-count
   arithmetic, and every side effect re-reads what it needs first.
4. A FAILED ATTEMPT-LOCK WRITE IS `FAILED_CLOSED`, NOT INDETERMINATE. v1 set
   `attempt_started` BEFORE the durable write, so a failure with no attempt
   marker and no website side effect was recorded as permanently indeterminate.
5. THE WHOLE PRIOR EVIDENCE SET IS PINNED. v1 pinned the plan, result and
   attempt but validated the event journal by filename and never loaded the
   receipt. Every event file and the exact receipt line are byte-pinned now.
6. THE TESTS PROVE IT. Runtime event-order assertions replaced source-substring
   ordering, the stage-time predicate use is actually observed, and the mocked
   transports no longer paper over an owner mismatch.

*** GUARD 1.0.7 IS REQUIRED AND IS NOT LIVE YET. ***
This tool pins Media Mutation Guard 1.0.7. Guard 1.0.6 is WITHDRAWN: an
independent review proved its producer emitted schema-2 proofs while its
consumer pinned schema 3, so a real snapshot could not be accepted at all, and
its acquisition could overwrite an expired-but-unresolved row. Its bytes are
kept unchanged as rejected evidence and are refused here by hash. The site still
runs 1.0.5, which
cannot serve this recovery at all: its `frpd_mg_acquisition_bindings()` refuses
any non-Stub-Flange family whose pre-guard proof reports a fixed match (7609 IS
one), and its `frpd_mg_state_bindings_are_valid()` requires reserved uploads to
be an in-order prefix from position 1. So `stage` refuses today, before creating
any plan, because the live plugin contract does not match the pinned one. This
tool will not deploy, replace, modify or work around a plugin.

This operation is NOT atomic and has NO rollback. There is no delete, trash,
detach, rename, media edit, retry, replay, restage, cleanup, guard-clearing,
product-revert or second-PUT route anywhere in this module, and no mail
transport of any kind.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any, Iterator

ROOT = Path(r"C:\FRPDepot")
THIS_DIR = Path(__file__).resolve().parent
sys.path.append(str(THIS_DIR))
sys.path.append(str(THIS_DIR.parent / "common"))
import woocommerce_common as wc  # noqa: E402
import wordpress_packing_ring_media_tool as media_base  # noqa: E402
import wordpress_product_family_media_tool as family_base  # noqa: E402
from ui_lane_lock import UiLaneBusy, UiLaneLockError, ui_browser_lock  # noqa: E402

TOOL_NAME = "FRP Depot Open Manway Gallery Recovery Tool"
TOOL_VERSION = "2.1.0"
SCHEMA_VERSION = 3
# Every v1 plan is permanently superseded. None was ever staged -- the v1 stage
# path refused before writing one, and the plan folder has never existed -- so
# this set is empty by fact, not by omission. `load_plan` additionally refuses
# any plan whose tool version or schema is not exactly this build's, which is
# what actually closes the door; the set is here so a future v1 artifact found on
# disk is refused by identity as well.
SUPERSEDED_PLAN_SHA256: frozenset[str] = frozenset()
SUPERSEDED_OPERATION_SHA256: frozenset[str] = frozenset()
SUPERSEDED_TOOL_VERSIONS = ("1.0.0", "2.0.0")
ACTION = "recover_fixed_open_manway_gallery"
APPROVAL_WORD = "APPROVED"
PLAN_LIFETIME_HOURS = 24
MIN_AUTHORIZATION_MARGIN = timedelta(minutes=2)
MIN_GUARD_COMPLETION_MARGIN = timedelta(minutes=2)

EXACT_ORIGIN = "https://frpdepots.com"
FAMILY_KEY = "open_manway"
PRODUCT_ID = 1397
PRODUCT_LABEL = "FRP MANWAY"
PRODUCT_SKU = "ZOHO-GROUP-3DCCB43DB14F"
PRODUCT_TYPE = "variable"
PRODUCT_STATUS = "publish"
PRODUCT_PERMALINK = "https://frpdepots.com/product/frp-manway/"
IMAGE_COUNT = 6

RESULT_STATUSES = ("COMMITTED_AND_VERIFIED", "FAILED_CLOSED", "INDETERMINATE_NO_RETRY")

# --------------------------------------------------------------------------
# Own local state. Nothing here is shared with the superseded family-media tool.
# --------------------------------------------------------------------------
PLAN_DIR = ROOT / "Dado" / "20_Working" / "wordpress_open_manway_gallery_recovery_plans"
STAGE_REGISTRY_DIR = PLAN_DIR / "stage-registry"
ATTEMPT_LEDGER_DIR = PLAN_DIR / "attempt-ledger"
RESERVATION_DIR = PLAN_DIR / "result-reservations"
RESULT_DIR = PLAN_DIR / "results"
JOURNAL_DIR = PLAN_DIR / "event-journal"
REGISTRY_KEY_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / (
    "FRPDepot-WordPress/open-manway-gallery-recovery-registry.key"
)
RECEIPTS = ROOT / "Dado" / "40_Logs" / "receipts.jsonl"

# --------------------------------------------------------------------------
# Permanent prior evidence. Read-only, hash-pinned, never rewritten.
# --------------------------------------------------------------------------
PRIOR_PLAN_DIR = ROOT / "Dado" / "20_Working" / "wordpress_product_family_media_plans"
PRIOR_PLAN_PATH = PRIOR_PLAN_DIR / "20260821T193640Z_open_manway_1c7865b0287b076f.json"
PRIOR_PLAN_SHA256 = "1c7865b0287b076fe83c179c2e44f33a3bcb2effb048e87374c32ad4781b19df"
PRIOR_PLAN_FILE_SHA256 = "9202e99a896238f64e14fc3492de038ff612b341e161ce8c9b23cbce486ee120"
PRIOR_PLAN_FILE_BYTES = 30946
PRIOR_OPERATION_SHA256 = "e0127fcaa04c023cbdd19a36726d6e8f03c3fb01f12f0367550d17c87674dc85"
PRIOR_RESULT_PATH = PRIOR_PLAN_DIR / "results" / f"{PRIOR_OPERATION_SHA256}.result.json"
PRIOR_RESULT_FILE_SHA256 = "e313645050730e29a2a1f2d1a13d4a2922bb4e7fc05d02a9a21efaeb87da0264"
PRIOR_RESULT_FILE_BYTES = 3721
PRIOR_ATTEMPT_PATH = PRIOR_PLAN_DIR / "attempt-ledger" / f"{PRIOR_OPERATION_SHA256}.attempt.json"
PRIOR_ATTEMPT_FILE_SHA256 = "be9af3a9ca2041305003d4644b7a94d96936ae4426e5091bf348990c0089a6f9"
PRIOR_ATTEMPT_FILE_BYTES = 606

# The COMPLETE permanent evidence set, byte-pinned. v1 pinned only the first
# three files and checked the journal by filename; a rewritten event or a
# rewritten receipt line would have passed unnoticed.
PRIOR_JOURNAL_DIR = PRIOR_PLAN_DIR / "event-journal" / PRIOR_OPERATION_SHA256
PRIOR_JOURNAL_FILES: dict[str, tuple[str, int]] = {
    "050_guard_acquired.json":
        ("db0273f9b8cbe564a04ceae2a27af30280eddc7ba2f85e27b34a980d45c966ab", 1442),
    "060_guard_owner_verified.json":
        ("161d394848bf7c1480be5aba12c0a04c70ba18f34627d07a9a9c0f2b3d9d740a", 1456),
    "110_upload_verified.json":
        ("2c8bf75d196662397a8a20548922d83b6e988cc1d47d7c4638f7cffd1c68aebe", 727),
    "990_indeterminate.json":
        ("d3559e4c41bd774f98760144128078ee439a271200bb49a5009842488b461656", 3774),
}
PRIOR_RECEIPT_ACTION = "product_family_media_indeterminate_no_retry"
PRIOR_RECEIPT_SHA256 = "868929b21dd45a3e887b3b745dac409051511b7c1a1496b9a88c8ae4f1fa4640"
PRIOR_RECEIPT_BYTES = 405

PRIOR_FAILED_STAGE = "upload_2"
PRIOR_FAILURE_REASON = "MediaUploadError"
PRIOR_GUARD_EXPIRES_UTC = "2026-08-21T20:35:14+00:00"
PRIOR_VERIFIED_UPLOAD = {
    "position": 1,
    "filename": "01_manway_premium_hero.png",
    "sha256": "472b5e5b0aba9a7201444524c559e6797c266a0de008d7bc70b4f8ef1938d0cd",
    "attachment_id": 7609,
    "source_url": "https://frpdepots.com/wp-content/uploads/2026/08/01_manway_premium_hero.png",
    "bytes": 1750111,
}
PRIOR_AMBIGUOUS_UPLOAD = {
    "position": 2,
    "filename": "02_manway_top_oblique.png",
    "sha256": "0fd7e2c62fb88d425cdfaf949415520ac89a5d95d07cf75b8e9791d308ea8181",
    "bytes": 1821849,
}
PRIOR_NEVER_ATTEMPTED_POSITIONS = (3, 4, 5, 6)
PRIOR_STAGED_GALLERY_IDS = (6991, 6992, 6993, 6994)

# --------------------------------------------------------------------------
# Six fixed approved sources. Every value is a constant; nothing is derived from
# a caller, an argument, a directory listing or a live page.
# --------------------------------------------------------------------------
SOURCE_DIR = ROOT / "Dado" / "20_Working" / "frp_manway" / "approved_gallery_20260820"


def _image(position: int, filename: str, byte_count: int, sha256: str,
           width: int, height: int) -> dict[str, Any]:
    return {
        "position": position,
        "path": str(SOURCE_DIR / filename),
        "filename": filename,
        "bytes": byte_count,
        "sha256": sha256,
        "width": width,
        "height": height,
        "format": "PNG",
        "mode": "RGB",
    }


FIXED_IMAGES: tuple[dict[str, Any], ...] = (
    _image(1, "01_manway_premium_hero.png", 1750111,
           "472b5e5b0aba9a7201444524c559e6797c266a0de008d7bc70b4f8ef1938d0cd", 1254, 1254),
    _image(2, "02_manway_top_oblique.png", 1821849,
           "0fd7e2c62fb88d425cdfaf949415520ac89a5d95d07cf75b8e9791d308ea8181", 1254, 1254),
    _image(3, "03_manway_low_side_angle.png", 1805796,
           "40ac3a69f5903d53f6fd71f952ac63ed237abc1a37a17c800a345c06211c8e63", 1402, 1122),
    _image(4, "04_manway_flange_bore_detail.png", 2118498,
           "d740be620cf0c083e7e399127c2205dd6f7b9e73fb08fdee31e1b79568d75950", 1402, 1122),
    _image(5, "05_manway_opposite_face.png", 1751997,
           "c54c9fd74fbdc55d0b9295b1bb7fb1dd0146cee645f455025d1b1895f21a543a", 1254, 1254),
    _image(6, "06_manway_laminate_detail.png", 2347237,
           "bfde2b6ab1f1de5cc6ad24b9aa556ef1ed46bd9cd43b34a2a5b1c77bb612e0e7", 1402, 1122),
)
FIXED_FILENAMES = tuple(row["filename"] for row in FIXED_IMAGES)
FIXED_PATHS = frozenset(Path(row["path"]).resolve() for row in FIXED_IMAGES)
FIXED_HASHES = {row["sha256"]: row["position"] for row in FIXED_IMAGES}
POSITION_BY_FILENAME = {row["filename"]: row["position"] for row in FIXED_IMAGES}
POSITIONS = tuple(range(1, IMAGE_COUNT + 1))


# --------------------------------------------------------------------------
# Installed guard capability, derived from the pinned plugin source and recorded
# so a future plugin change forces this to be re-derived rather than assumed.
# --------------------------------------------------------------------------
# *** THIS TOOL PINS GUARD 1.0.7, NOT THE 1.0.5 THE FAMILY TOOL DRIVES. ***
# `wordpress_product_family_media_tool` deliberately still pins the INSTALLED
# 1.0.5 release snapshot, so its constants cannot be reused here. These are read
# from the 1.0.7 build tree and are re-derived, never assumed. Every proof shape
# validated below is produced by the real plugin in
# `media_mutation_guard/test_media_mutation_guard_recovery_lifecycle.php` and
# published to `media_mutation_guard/testdata/guard_107_proof_contract.json`,
# which the contract test in this tool's suite validates with these very
# functions -- 1.0.6 shipped a consumer pinned to a schema its own producer never
# emitted, and that is the drift this fixture makes executable.
GUARD_PLUGIN_VERSION = "1.0.7"
GUARD_STATE_SCHEMA = 3
GUARD_PROOF_SCHEMA = 3
GUARD_TTL_SECONDS = family_base.GUARD_TTL_SECONDS
GUARD_DIR = THIS_DIR / "media_mutation_guard"
GUARD_ZIP_PATH = GUARD_DIR / "frpdepot-media-mutation-guard-1.0.7.zip"
GUARD_ZIP_SHA256 = "a1f6bf204e443dea9008699abcaf96e7da868a894a5f569215c572c9963ab2d1"
GUARD_ZIP_BYTES = 35656
# *** 1.0.6 IS WITHDRAWN. *** Its bytes are kept unchanged as rejected evidence and
# are refused by identity here, so a 1.0.6 artifact found on disk can never be
# validated, staged or deployed by this build.
WITHDRAWN_GUARD_ARTIFACTS: dict[str, str] = {
    "6a753c570d167075b8fa0a66349ab0a812aa7e222a7aedb2f6d374b913a7010e":
        "WITHDRAWN_NOT_DEPLOYED_NOT_STAGEABLE",
}
WITHDRAWN_GUARD_PLUGIN_VERSIONS = ("1.0.6",)
GUARD_PROOF_CONTRACT_PATH = GUARD_DIR / "testdata" / "guard_107_proof_contract.json"
GUARD_PLUGIN_PHP_PATH = GUARD_DIR / "frpdepot-media-mutation-guard" / "frpdepot-media-mutation-guard.php"
GUARD_PLUGIN_PHP_SHA256 = "87209d942828f2042c26225f48ebe18c91a336dbed1411a102290a3dbf1623bf"
GUARD_PLUGIN_PHP_BYTES = 146101
GUARD_RUNTIME_MANIFEST_PATH = GUARD_DIR / "frpdepot-media-mutation-guard" / "approved-media.json"
GUARD_RUNTIME_MANIFEST_SHA256 = "adb9b81f7a8e55205c7224af6005c0c386ec833eef36be7281dca96313e9d900"
GUARD_RUNTIME_MANIFEST_BYTES = 5910
GUARD_RECOVERY_CONTRACT = "open_manway_recovery"
GUARD_CAPABILITY_SELECTOR = "script#frpd-mg-capability[type='application/json']"
GUARD_ORIGIN_PROOF_SELECTOR = "#frpd-mg-origin-proof"
GUARD_ORIGIN_PROOF_ACTION = "frpd_media_guard_origin_proof"
GUARD_RECOVERY_GALLERY_SELECTOR = "#frpd-mg-recovery-gallery"
GUARD_RECOVERY_GALLERY_FORM_SELECTOR = "#frpd-mg-recovery-gallery-form"
GUARD_RECOVERY_GALLERY_ACTION = "frpd_media_guard_recovery_gallery"
GUARD_RECOVERY_IF_MATCH_SELECTOR = "#frpd-mg-recovery-if-match"

# --------------------------------------------------------------------------
# TWO TRANSPORTS, NAMED SEPARATELY AND HONESTLY.
#
# v2.0.0's plan pinned the authorization as `PUT /products/1397` and its
# forbidden list said "no Basic WooCommerce credentials / no generic REST",
# while the module in fact loaded the Woo vault and read the product with
# `wc.api_get()`. Both statements cannot be true. The commission forbids
# Basic/generic Woo for the GALLERY WRITE, not for the commissioned read-only
# verification, so the two are now declared apart and the claim matches the code:
# there is no write primitive of any kind reachable from this module, which
# `assert_no_woocommerce_write_primitive()` proves against its own source.
# --------------------------------------------------------------------------
READ_TRANSPORT: dict[str, Any] = {
    "kind": "woocommerce_rest_read_only",
    "client": "woocommerce_common.api_get",
    "method": "GET",
    "routes": [f"/products/{PRODUCT_ID}"],
    "purpose": "fresh exact product identity, gallery order and protected-field "
               "fingerprint verification only",
    "performs_writes": False,
    "credential": "the commissioned WooCommerce vault, used for GET only; no write "
                  "primitive is reachable from this module",
}
WRITE_TRANSPORT: dict[str, Any] = {
    "kind": "guard_owned_admin_post_form",
    "route": "/wp-admin/admin-post.php",
    "action": GUARD_RECOVERY_GALLERY_ACTION,
    "method": "POST",
    "submitted_in": "the already-authenticated guard-owning WordPress browser",
    "caller_supplied_fields": ["action", "_wpnonce", "if_match"],
    "server_derived_fields": ["product_id", "attachment_ids"],
    "server_side_effect": "one internal images-only WooCommerce product update inside "
                          "the same authenticated request",
    "forbidden": [
        "woocommerce_common.api_request", "basic_woocommerce_credentials_for_the_write",
        "consumer_key_write", "external_loopback_http", "generic_rest_write_route",
        "browser_supplied_image_ids", "product_edit_form",
    ],
}
# Any WooCommerce call that is not a read. None of these may appear in this module.
FORBIDDEN_WRITE_PRIMITIVES = (
    "api_request", "api_post", "api_put", "api_patch", "api_delete",
    "requests.post", "requests.put", "requests.patch", "requests.delete",
)


def _called_names(tree: ast.AST) -> list[str]:
    """Every callee this module actually invokes, as a dotted name."""
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        parts: list[str] = []
        while isinstance(target, ast.Attribute):
            parts.append(target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            parts.append(target.id)
        if parts:
            names.append(".".join(reversed(parts)))
    return names


_TRANSPORT_PROOF_CACHE: dict[str, dict[str, Any]] = {}


def assert_no_woocommerce_write_primitive() -> dict[str, Any]:
    """Prove from this module's own PARSED source that no Woo write primitive exists.

    This walks real call sites, not text: the module names `wc.api_request` in its
    docstring and in its forbidden list on purpose, and a substring scan would
    either fire on that prose or have to be weakened until it proved nothing.
    Read-only `api_get` calls are counted and reported rather than hidden, so the
    plan can state what the read transport actually is.
    """
    try:
        raw = Path(__file__).resolve().read_bytes()
    except OSError as exc:  # pragma: no cover - unreadable own source
        raise RecoveryError("REFUSED: this module's own source is unreadable.") from exc
    digest = hashlib.sha256(raw).hexdigest()
    cached = _TRANSPORT_PROOF_CACHE.get(digest)
    if cached is not None:
        return copy.deepcopy(cached)
    try:
        tree = ast.parse(raw.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError) as exc:  # pragma: no cover
        raise RecoveryError("REFUSED: this module's own source is unreadable.") from exc
    called = _called_names(tree)
    found = sorted({name for name in called
                    if name.split(".")[-1] in FORBIDDEN_WRITE_PRIMITIVES})
    if found:
        raise RecoveryError(
            "REFUSED: a WooCommerce write primitive is reachable from this module: "
            f"{found}. The gallery write must only ever be the owner-bound admin-post form."
        )
    proof = {
        "woocommerce_write_primitives_reachable": [],
        "woocommerce_read_calls": sorted(
            {name for name in called if name in ("wc.api_get", "api_get")}),
        "read_transport": copy.deepcopy(READ_TRANSPORT),
        "write_transport": copy.deepcopy(WRITE_TRANSPORT),
    }
    _TRANSPORT_PROOF_CACHE[digest] = copy.deepcopy(proof)
    return proof
GUARD_EXPECTED_CAPABILITY: dict[str, Any] = {
    "schema": GUARD_PROOF_SCHEMA,
    "plugin_version": GUARD_PLUGIN_VERSION,
    "state_schema": GUARD_STATE_SCHEMA,
    "proof_schema": GUARD_PROOF_SCHEMA,
    "manifest_sha256": GUARD_RUNTIME_MANIFEST_SHA256,
    "families": ["elbow_90", "manway_cover", "open_manway", "pipe", "stub_flange"],
    "fixed_reuse_family": "stub_flange",
    "fixed_recovery": {
        "contract": GUARD_RECOVERY_CONTRACT,
        "family": FAMILY_KEY,
        "product_id": PRODUCT_ID,
        "position": 1,
        "attachment_id": 7609,
        "filename": "01_manway_premium_hero.png",
        "prior_operation_sha256": PRIOR_OPERATION_SHA256,
        "recoverable_positions": [2, 3, 4, 5, 6],
    },
    "capabilities": {
        "existing_fixed_attachment_acquisition": True,
        "non_prefix_upload_reservation": True,
        "origin_only_file_enumeration": True,
        "owner_bound_gallery_commit": True,
    },
}
GUARD_PARTIAL_RECOVERY_CAPABILITY: dict[str, Any] = {
    "plugin_version": GUARD_PLUGIN_VERSION,
    "plugin_php_sha256": GUARD_PLUGIN_PHP_SHA256,
    "state_schema": GUARD_STATE_SCHEMA,
    "supports_existing_fixed_attachment_acquisition": True,
    "supports_non_prefix_upload_reservation": True,
    "supports_origin_only_file_enumeration": True,
    "supports_owner_bound_gallery_commit": True,
    "evidence": (
        "frpd_mg_recovery_acquisition_bindings() binds attachment 7609 at fixed "
        "position 1 and zero-or-one proven live attachment at each of positions 2-6, "
        "records the immutable missing-position list, and refuses on any ambiguity. "
        "frpd_mg_next_reserved_filename() derives each permitted upload from that "
        "durable record, so an ascending non-prefix order is supported and at most "
        "one reservation may be unbound. frpd_mg_origin_only_file_proof() enumerates "
        "the uploads directory itself and fails closed. "
        "frpd_mg_handle_recovery_gallery() commits the images-only product update "
        "inside the authenticated admin-post request, so the owner user, session "
        "token and /wp-admin/-scoped guard cookie are all present."
    ),
}


class RecoveryError(RuntimeError):
    """Closed refusal, or a permanently attributed one-attempt failure."""


class RecoveryIndeterminate(RecoveryError):
    """A side effect may have landed; the plan is permanently no-retry."""


@contextlib.contextmanager
def closed_refusal() -> Iterator[None]:
    """One refusal type for the whole tool.

    The reused readers, plan writer and guard validators are the proven ones from
    the superseded family tool, but their exception types are not this tool's
    contract. Every borrowed refusal is re-raised as a RecoveryError so a caller
    -- and every test -- can rely on exactly one closed-refusal type. The message
    is preserved verbatim and the original is kept as the cause.
    """
    try:
        yield
    except (family_base.FamilyMediaError, media_base.MediaUploadError) as exc:
        raise RecoveryError(str(exc)) from exc


# --------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def append_receipt(action: str, evidence: str) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    with RECEIPTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"ts": utc_now().isoformat(), "action": action,
                                 "evidence": evidence}, ensure_ascii=True) + "\n")


def require_approval(value: Any) -> None:
    """Byte-exact unpadded uppercase APPROVED, checked before any vault or network."""
    if not isinstance(value, str) or not secrets.compare_digest(value, APPROVAL_WORD):
        raise RecoveryError("REFUSED: approval must be exact unpadded uppercase APPROVED.")


def _hex64(value: Any, label: str) -> str:
    text = str(value)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise RecoveryError(f"REFUSED: {label} is not a lowercase SHA-256 digest.")
    return text


def _read_fixed_evidence_file(path: Path, expected_bytes: int,
                              expected_sha256: str, label: str) -> dict[str, Any]:
    """Read one pinned permanent evidence file. Never opened for writing."""
    if not path.is_file() or media_base._is_reparse_point(path):
        raise RecoveryError(f"REFUSED: prior {label} evidence is missing, non-file, or a reparse point.")
    raw = path.read_bytes()
    if len(raw) != expected_bytes:
        raise RecoveryError(f"REFUSED: prior {label} evidence byte size changed.")
    digest = hashlib.sha256(raw).hexdigest()
    if not secrets.compare_digest(digest, expected_sha256):
        raise RecoveryError(f"REFUSED: prior {label} evidence SHA-256 changed.")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"REFUSED: prior {label} evidence is unreadable.") from exc
    if not isinstance(parsed, dict):
        raise RecoveryError(f"REFUSED: prior {label} evidence is not one JSON object.")
    return {"path": str(path), "bytes": len(raw), "sha256": digest, "parsed": parsed}


def validate_prior_evidence() -> dict[str, Any]:
    """Prove the permanent prior evidence byte-for-byte AND semantically.

    Anything missing, drifted, or saying something LESS restrictive than the
    recorded permanent no-retry outcome is a closed refusal. Nothing here is
    repaired, normalized, relabelled or rewritten.
    """
    plan_file = _read_fixed_evidence_file(
        PRIOR_PLAN_PATH, PRIOR_PLAN_FILE_BYTES, PRIOR_PLAN_FILE_SHA256, "plan")
    result_file = _read_fixed_evidence_file(
        PRIOR_RESULT_PATH, PRIOR_RESULT_FILE_BYTES, PRIOR_RESULT_FILE_SHA256, "result")
    attempt_file = _read_fixed_evidence_file(
        PRIOR_ATTEMPT_PATH, PRIOR_ATTEMPT_FILE_BYTES, PRIOR_ATTEMPT_FILE_SHA256, "attempt")

    plan = plan_file["parsed"]
    result = result_file["parsed"]
    attempt = attempt_file["parsed"]

    if (plan.get("sha256") != PRIOR_PLAN_SHA256
            or plan.get("operation_sha256") != PRIOR_OPERATION_SHA256
            or plan.get("family") != FAMILY_KEY
            or plan.get("product_id") != PRODUCT_ID):
        raise RecoveryError("REFUSED: prior plan identity is not the exact superseded operation.")
    staged_gallery = plan.get("product_before", {}).get("before_gallery")
    if (not isinstance(staged_gallery, list)
            or [row.get("id") for row in staged_gallery] != list(PRIOR_STAGED_GALLERY_IDS)):
        raise RecoveryError("REFUSED: prior plan staged gallery is not the exact recorded baseline.")

    if (result.get("status") != "INDETERMINATE_NO_RETRY"
            or result.get("plan_sha256") != PRIOR_PLAN_SHA256
            or result.get("operation_sha256") != PRIOR_OPERATION_SHA256
            or result.get("stage") != PRIOR_FAILED_STAGE
            or result.get("reason") != PRIOR_FAILURE_REASON
            or result.get("no_retry") is not True
            or result.get("product_may_have_changed") is not False
            or result.get("gallery_payload") is not None
            or result.get("rollback_performed") is not False
            or result.get("delete_performed") is not False
            or result.get("emails") != 0):
        raise RecoveryError("REFUSED: prior result is missing, altered, or less restrictive than recorded.")

    verified = result.get("uploaded_verified")
    if (not isinstance(verified, list) or len(verified) != 1
            or {field: verified[0].get(field) for field in PRIOR_VERIFIED_UPLOAD}
                != PRIOR_VERIFIED_UPLOAD):
        raise RecoveryError("REFUSED: prior verified upload 1 evidence is not exact.")

    current = result.get("current_upload")
    if ({field: (current or {}).get(field) for field in PRIOR_AMBIGUOUS_UPLOAD}
            != PRIOR_AMBIGUOUS_UPLOAD
            or result.get("current_upload_may_have_landed") is not True
            or result.get("observed_attachment_id") is not None):
        raise RecoveryError("REFUSED: prior ambiguous upload 2 evidence is not exact.")

    acquisition = result.get("guard_acquisition")
    if (not isinstance(acquisition, dict)
            or acquisition.get("guard_expires_utc") != PRIOR_GUARD_EXPIRES_UTC):
        raise RecoveryError("REFUSED: prior guard acquisition expiry evidence is not exact.")

    # No evidence anywhere may claim an attempt beyond the ambiguous upload 2.
    for row in verified:
        if int(row.get("position") or 0) in PRIOR_NEVER_ATTEMPTED_POSITIONS:
            raise RecoveryError("REFUSED: prior evidence claims an upload that was never attempted.")
    journal = validate_prior_event_journal()
    for position in PRIOR_NEVER_ATTEMPTED_POSITIONS:
        if f"1{position}0_upload_verified.json" in journal["files"]:
            raise RecoveryError(
                f"REFUSED: prior event journal records an upload at position {position} "
                "that the result says was never attempted."
            )
    receipt = validate_prior_receipt()

    if (attempt.get("plan_sha256") != PRIOR_PLAN_SHA256
            or attempt.get("operation_sha256") != PRIOR_OPERATION_SHA256
            or attempt.get("no_retry") is not True):
        raise RecoveryError("REFUSED: prior attempt ledger is missing or less restrictive than recorded.")

    return {
        "plan": {"path": plan_file["path"], "bytes": plan_file["bytes"],
                 "file_sha256": plan_file["sha256"], "plan_sha256": PRIOR_PLAN_SHA256},
        "result": {"path": result_file["path"], "bytes": result_file["bytes"],
                   "file_sha256": result_file["sha256"], "status": "INDETERMINATE_NO_RETRY"},
        "attempt": {"path": attempt_file["path"], "bytes": attempt_file["bytes"],
                    "file_sha256": attempt_file["sha256"]},
        "operation_sha256": PRIOR_OPERATION_SHA256,
        "failed_stage": PRIOR_FAILED_STAGE,
        "reason": PRIOR_FAILURE_REASON,
        "verified_upload": copy.deepcopy(PRIOR_VERIFIED_UPLOAD),
        "ambiguous_upload": copy.deepcopy(PRIOR_AMBIGUOUS_UPLOAD),
        "never_attempted_positions": list(PRIOR_NEVER_ATTEMPTED_POSITIONS),
        "guard_expires_utc": PRIOR_GUARD_EXPIRES_UTC,
        "event_journal": journal,
        "receipt": receipt,
        "no_retry": True,
        "superseded_gallery_ids": list(PRIOR_STAGED_GALLERY_IDS),
    }


def validate_prior_event_journal() -> dict[str, Any]:
    """Byte-pin EVERY existing prior event-journal file, not just its name.

    v1 checked only that certain filenames were absent. A rewritten event body
    would have passed. Each file is now size- and SHA-256-pinned, the directory
    may hold nothing else, and nothing here opens any of them for writing.
    """
    if not PRIOR_JOURNAL_DIR.is_dir() or media_base._is_reparse_point(PRIOR_JOURNAL_DIR):
        raise RecoveryError("REFUSED: prior event journal is missing or is a reparse point.")
    present = sorted(entry.name for entry in PRIOR_JOURNAL_DIR.iterdir())
    if present != sorted(PRIOR_JOURNAL_FILES):
        raise RecoveryError(
            "REFUSED: the prior event journal gained, lost or renamed a file: "
            + json.dumps(present, ensure_ascii=True)
        )
    files: dict[str, Any] = {}
    for name, (expected_sha256, expected_bytes) in sorted(PRIOR_JOURNAL_FILES.items()):
        path = PRIOR_JOURNAL_DIR / name
        if not path.is_file() or media_base._is_reparse_point(path):
            raise RecoveryError(f"REFUSED: prior event {name} is missing or is a reparse point.")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != expected_bytes or not secrets.compare_digest(digest, expected_sha256):
            raise RecoveryError(f"REFUSED: prior event {name} changed byte-for-byte.")
        files[name] = {"bytes": len(raw), "file_sha256": digest}
    return {"path": str(PRIOR_JOURNAL_DIR), "files": files, "count": len(files),
            "mutated_by_this_tool": False}


def validate_prior_receipt() -> dict[str, Any]:
    """Prove the one permanent receipt line for the superseded operation.

    The receipt log is append-only and shared, so the line is located by CONTENT
    -- never by line number -- and must appear exactly once with the exact bytes.
    """
    if not RECEIPTS.is_file() or media_base._is_reparse_point(RECEIPTS):
        raise RecoveryError("REFUSED: the receipt log is missing or is a reparse point.")
    matches: list[str] = []
    with RECEIPTS.open("r", encoding="utf-8", newline="\n") as handle:
        for line in handle:
            stripped = line.rstrip("\n")
            if PRIOR_OPERATION_SHA256 in stripped:
                matches.append(stripped)
    if len(matches) != 1:
        raise RecoveryError(
            f"REFUSED: the superseded operation has {len(matches)} receipt lines, not exactly one."
        )
    raw = matches[0].encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) != PRIOR_RECEIPT_BYTES or not secrets.compare_digest(digest, PRIOR_RECEIPT_SHA256):
        raise RecoveryError("REFUSED: the permanent prior receipt line changed byte-for-byte.")
    try:
        parsed = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise RecoveryError("REFUSED: the permanent prior receipt line is unreadable.") from exc
    if (not isinstance(parsed, dict) or parsed.get("action") != PRIOR_RECEIPT_ACTION
            or PRIOR_PLAN_SHA256 not in str(parsed.get("evidence") or "")
            or f"stage={PRIOR_FAILED_STAGE}" not in str(parsed.get("evidence") or "")):
        raise RecoveryError("REFUSED: the permanent prior receipt line no longer says what it said.")
    return {"path": str(RECEIPTS), "action": PRIOR_RECEIPT_ACTION,
            "bytes": len(raw), "line_sha256": digest, "occurrences": 1,
            "mutated_by_this_tool": False}


def require_prior_guard_released(now: datetime | None = None) -> dict[str, Any]:
    """The old guard acquisition must be past its authoritative expiry."""
    moment = now or utc_now()
    expires = datetime.fromisoformat(PRIOR_GUARD_EXPIRES_UTC.replace("Z", "+00:00"))
    if moment < expires:
        raise RecoveryError(
            "REFUSED: the superseded operation's guard has not reached its authoritative "
            f"expiry ({PRIOR_GUARD_EXPIRES_UTC}). Recovery may not begin while it can still be active."
        )
    return {"prior_guard_expires_utc": PRIOR_GUARD_EXPIRES_UTC,
            "prior_guard_expired": True, "checked_utc": moment.isoformat()}


# --------------------------------------------------------------------------
# Fixed local sources
# --------------------------------------------------------------------------
def validate_local_images() -> list[dict[str, Any]]:
    """Path, byte size, SHA-256, PNG format/mode/dimensions, and regular-file identity."""
    if [row["position"] for row in FIXED_IMAGES] != list(POSITIONS):
        raise RecoveryError("REFUSED: fixed source positions are not exactly 1-6 in order.")
    family_rows = family_base.FAMILY_SPECS[FAMILY_KEY]["images"]
    fields = ("position", "path", "filename", "bytes", "sha256", "width", "height",
              "format", "mode")
    if ([{field: row[field] for field in fields} for row in FIXED_IMAGES]
            != [{field: row[field] for field in fields} for row in family_rows]):
        raise RecoveryError(
            "REFUSED: the fixed recovery sources disagree with the approved open-manway "
            "family record they were taken from."
        )
    evidence: list[dict[str, Any]] = []
    for expected in FIXED_IMAGES:
        path = Path(expected["path"])
        resolved = path.resolve()
        if resolved not in FIXED_PATHS or resolved.parent != SOURCE_DIR.resolve():
            raise RecoveryError("REFUSED: a source image escaped the fixed allowlist.")
        if not path.is_file() or media_base._is_reparse_point(path):
            raise RecoveryError(
                f"REFUSED: fixed source is missing, non-file, or a reparse point: {path}")
        if path.stat().st_size != expected["bytes"]:
            raise RecoveryError(f"REFUSED: byte size changed for {expected['filename']}.")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if not secrets.compare_digest(digest, expected["sha256"]):
            raise RecoveryError(f"REFUSED: SHA-256 changed for {expected['filename']}.")
        with closed_refusal():
            actual = media_base._verify_image_bytes(path)
        wanted = (expected["format"], expected["mode"], expected["width"], expected["height"])
        if actual != wanted:
            raise RecoveryError(
                f"REFUSED: format/mode/dimensions changed for {expected['filename']}.")
        evidence.append({**copy.deepcopy(expected), "regular_file": True,
                         "reparse_point": False})
    return evidence


# --------------------------------------------------------------------------
# Origin-only fixed-file proof -- SERVER-SIDE, from the guard plugin itself.
#
# v1 probed six public URLs under one hard-coded month directory and read a 404
# as "no file". That is not proof about the server filesystem: a public 404 can
# come from a rewrite rule, a CDN, a permissions change or simply the file being
# in a different month, and no public request can see a file whose attachment row
# never landed -- which is exactly the state the ambiguous upload 2 could have
# left behind. There is no HTTP client, URL builder or public probe left in this
# module. Guard 1.0.7 enumerates its own uploads directory, bounded and fail
# closed, and this validates the proof it returns.
# --------------------------------------------------------------------------
ORIGIN_PROOF_KEYS = {
    "schema", "plugin_version", "mode", "family", "generated_utc",
    "directories_scanned", "yearmonth_supported", "complete", "files", "origin_only",
}
ORIGIN_FILE_KEYS = {
    "position", "basename", "discovered", "paths", "owner_attachment_ids",
    "bytes_and_hash_exact", "origin_only",
}
# Every discovered path carries its OWN classification and its OWN owner list.
# 1.0.6 aggregated owners across paths before classifying, so "one unowned file
# plus one two-owner file" reported clean.
ORIGIN_PATH_KEYS = {"relative_path", "bytes", "sha256", "bytes_and_hash_exact",
                    "owner_attachment_ids"}
SAFE_RELATIVE_PATH = re.compile(r"\A(?:[0-9]{4}/[0-9]{2}/)?[^/\\:]+\Z")


def validate_origin_proof(proof: Any) -> dict[str, Any]:
    """Complete, bounded, path-safe, and exactly the six fixed basenames in order."""
    if not isinstance(proof, dict) or set(proof) != ORIGIN_PROOF_KEYS:
        raise RecoveryError("REFUSED: origin-only file proof has the wrong closed schema.")
    if (proof["schema"] != GUARD_PROOF_SCHEMA
            or proof["plugin_version"] != GUARD_PLUGIN_VERSION
            or proof["mode"] != "origin_only_file_proof"
            or proof["family"] != FAMILY_KEY
            or proof["yearmonth_supported"] is not True):
        raise RecoveryError("REFUSED: origin-only file proof identity is wrong.")
    with closed_refusal():
        family_base._guard_timestamp(proof["generated_utc"], "origin-proof generated")
    if proof["complete"] is not True:
        raise RecoveryError(
            "REFUSED: the guard could not prove complete origin-file enumeration, so "
            "origin-only collision absence is UNPROVEN. Nothing was staged."
        )
    if type(proof["directories_scanned"]) is not int or proof["directories_scanned"] < 1:
        raise RecoveryError("REFUSED: origin-only file proof scanned no directory.")
    files = proof["files"]
    if (not isinstance(files, list) or len(files) != IMAGE_COUNT
            or [row.get("basename") if isinstance(row, dict) else None
                for row in files] != list(FIXED_FILENAMES)):
        raise RecoveryError(
            "REFUSED: origin-only file proof does not cover exactly the six fixed basenames in order."
        )
    for row, fixed in zip(files, FIXED_IMAGES):
        if not isinstance(row, dict) or set(row) != ORIGIN_FILE_KEYS:
            raise RecoveryError("REFUSED: an origin-file record has the wrong closed schema.")
        if (row["position"] != fixed["position"]
                or type(row["discovered"]) is not int or row["discovered"] < 0
                or type(row["bytes_and_hash_exact"]) is not bool
                or type(row["origin_only"]) is not bool
                or not isinstance(row["paths"], list)
                or not isinstance(row["owner_attachment_ids"], list)
                or len(row["paths"]) != row["discovered"]):
            raise RecoveryError("REFUSED: an origin-file record is internally inconsistent.")
        for owner in row["owner_attachment_ids"]:
            if type(owner) is not int or owner <= 0:
                raise RecoveryError("REFUSED: an origin-file owner attachment ID is invalid.")
        if len(set(row["owner_attachment_ids"])) != len(row["owner_attachment_ids"]):
            raise RecoveryError("REFUSED: an origin-file record repeats an owner attachment ID.")
        seen_relative: set[str] = set()
        aggregated: list[int] = []
        for entry in row["paths"]:
            if not isinstance(entry, dict) or set(entry) != ORIGIN_PATH_KEYS:
                raise RecoveryError("REFUSED: an origin-file path record has the wrong schema.")
            relative = str(entry["relative_path"])
            if (not SAFE_RELATIVE_PATH.match(relative)
                    or Path(relative).name != fixed["filename"]
                    or ".." in relative or relative in seen_relative):
                raise RecoveryError(
                    "REFUSED: the guard reported an unsafe, absolute or repeated origin path."
                )
            seen_relative.add(relative)
            if (type(entry["bytes"]) is not int or entry["bytes"] < 0
                    or type(entry["bytes_and_hash_exact"]) is not bool
                    or not isinstance(entry["owner_attachment_ids"], list)):
                raise RecoveryError("REFUSED: an origin-file path record is invalid.")
            path_owners = entry["owner_attachment_ids"]
            for owner in path_owners:
                if type(owner) is not int or owner <= 0:
                    raise RecoveryError("REFUSED: an origin path owner attachment ID is invalid.")
            if len(set(path_owners)) != len(path_owners):
                raise RecoveryError("REFUSED: an origin path repeats an owner attachment ID.")
            aggregated.extend(path_owners)
            _hex64(entry["sha256"], "origin file digest")
            if (entry["bytes_and_hash_exact"]
                    and (entry["bytes"] != fixed["bytes"] or entry["sha256"] != fixed["sha256"])):
                raise RecoveryError(
                    "REFUSED: an origin path claims exact approved bytes it does not carry."
                )
        if sorted(set(aggregated)) != sorted(row["owner_attachment_ids"]):
            raise RecoveryError(
                "REFUSED: an origin-file record's owner summary disagrees with its own "
                "per-path owner lists."
            )
        # The blocker state is decided PER PATH, exactly as the plugin decides it:
        # one discovered copy, owned by exactly one attachment, with exact bytes.
        expected_origin_only = row["discovered"] > 0 and not (
            row["discovered"] == 1
            and len(row["paths"][0]["owner_attachment_ids"]) == 1
            and row["paths"][0]["bytes_and_hash_exact"] is True
        )
        if row["origin_only"] is not expected_origin_only:
            raise RecoveryError("REFUSED: an origin-file record misreports its own blocker state.")
        if (row["discovered"] == 1 and not expected_origin_only
                and row["bytes_and_hash_exact"] is not True):
            raise RecoveryError(
                "REFUSED: an origin-file record reports a clean fixed file without exact bytes."
            )
    declared = proof["origin_only"]
    if not isinstance(declared, list):
        raise RecoveryError("REFUSED: origin-only blocker evidence is not a list.")
    # Every PATH of an origin-only basename must be listed, not just the basename.
    expected_declared = sorted(
        (row["position"], entry["relative_path"], entry["bytes_and_hash_exact"],
         tuple(entry["owner_attachment_ids"]))
        for row in files if row["origin_only"] for entry in row["paths"]
    )
    declared_rows = []
    for row in declared:
        if (not isinstance(row, dict)
                or set(row) != {"position", "relative_path", "bytes_and_hash_exact",
                                "owner_attachment_ids"}
                or not isinstance(row["owner_attachment_ids"], list)):
            raise RecoveryError("REFUSED: an origin-only blocker record has the wrong schema.")
        declared_rows.append((row["position"], row["relative_path"],
                              row["bytes_and_hash_exact"],
                              tuple(row["owner_attachment_ids"])))
    if sorted(declared_rows) != expected_declared:
        raise RecoveryError("REFUSED: origin-only blocker evidence disagrees with its own records.")
    return copy.deepcopy(proof)


def require_origin_proof_matches(proof: dict[str, Any],
                                 reconciliation: list[dict[str, Any]]) -> None:
    """Each fixed position: exactly one owned exact file, or no file and no owner."""
    checked = validate_origin_proof(proof)
    if checked["origin_only"]:
        blocked = [row["position"] for row in checked["files"] if row["origin_only"]]
        raise RecoveryError(
            "REFUSED: ORIGIN-ONLY FIXED-FILE COLLISION. The uploads directory already holds "
            f"the fixed basename(s) for position(s) {blocked} with no owning attachment. A "
            "fresh upload would be stored under a -N name. This is exactly the state the "
            "superseded operation's ambiguous upload 2 could have left. Nothing was staged, "
            "and this tool has no route to delete, rename or adopt that file."
        )
    for row, resolved in zip(checked["files"], reconciliation):
        position = row["position"]
        if resolved["disposition"] == "reuse_existing":
            if (row["discovered"] != 1
                    or row["owner_attachment_ids"] != [int(resolved["attachment_id"])]
                    or row["bytes_and_hash_exact"] is not True):
                raise RecoveryError(
                    f"REFUSED: position {position} is recorded as live attachment "
                    f"{resolved['attachment_id']} but the server's own file proof does not "
                    "show exactly that attachment owning exactly one exact fixed file."
                )
            continue
        if row["discovered"] != 0 or row["owner_attachment_ids"]:
            raise RecoveryError(
                f"REFUSED: position {position} is marked for upload but the server already "
                "holds a file or an attachment for its fixed basename."
            )


# --------------------------------------------------------------------------
# Complete Media Library reconciliation
# --------------------------------------------------------------------------
def require_complete_library_evidence(evidence: Any) -> None:
    """Complete, bounded, fail-closed. No sampling path exists."""
    if not isinstance(evidence, dict):
        raise RecoveryError("REFUSED: Media Library evidence is not one object.")
    required_true = (
        "enumeration_complete", "hash_complete", "recheck_complete", "snapshot_stable",
        "recheck_hash_complete", "content_stable", "final_complete",
        "final_snapshot_stable", "private_exception_proven", "complete",
    )
    if any(evidence.get(field) is not True for field in required_true):
        raise RecoveryError(
            "REFUSED: complete Media Library evidence was not proven: "
            + json.dumps({field: evidence.get(field) for field in required_true},
                         sort_keys=True, separators=(",", ":"))
        )
    integer_fields = (
        "library_total", "enumerated", "pages_read", "image_rows",
        "image_hashes_completed", "hash_failures", "recheck_total", "recheck_enumerated",
        "recheck_pages", "recheck_image_hashes_completed", "recheck_hash_failures",
        "final_total", "final_enumerated", "final_pages",
    )
    if any(type(evidence.get(field)) is not int or evidence[field] < 0
           for field in integer_fields):
        raise RecoveryError("REFUSED: Media Library counters are invalid.")
    if (evidence["pages_read"] < 1 or evidence["recheck_pages"] < 1
            or evidence["final_pages"] < 1
            or evidence["enumerated"] != evidence["library_total"]
            or evidence["recheck_enumerated"] != evidence["recheck_total"]
            or evidence["final_enumerated"] != evidence["final_total"]
            or evidence["recheck_total"] != evidence["library_total"]
            or evidence["final_total"] != evidence["library_total"]):
        raise RecoveryError("REFUSED: Media Library evidence does not prove complete enumeration.")
    if (evidence["hash_failures"] != 0 or evidence["recheck_hash_failures"] != 0
            or evidence["image_hashes_completed"] != evidence["image_rows"]
            or evidence["recheck_image_hashes_completed"] != evidence["image_rows"]):
        raise RecoveryError(
            "REFUSED: Media Library download/hash proof is incomplete; sampling is not a result."
        )
    if evidence.get("private_exception") != {
            "attachment_id": family_base.GUARD_PRIVATE_EXCEPTION["attachment_id"],
            "filename": Path(family_base.GUARD_PRIVATE_EXCEPTION["attached_file"]).name}:
        raise RecoveryError("REFUSED: Media Library evidence lacks the exact private attachment.")
    if evidence.get("target_families") != [FAMILY_KEY]:
        raise RecoveryError("REFUSED: Media Library evidence covers the wrong families.")
    if evidence.get("reuse_candidates") != []:
        raise RecoveryError("REFUSED: Media Library evidence carries a foreign reuse candidate.")


def resolve_positions(evidence: dict[str, Any]) -> dict[int, int]:
    """Map fixed positions to live attachment IDs by COMPLETE HASH, never by name.

    A filename match without an exact hash match, or more than one attachment for
    one position, is ambiguity and refuses. Nothing is chosen among duplicates.
    """
    require_complete_library_evidence(evidence)
    hash_rows = evidence.get("hash_conflicts")
    name_rows = evidence.get("name_conflicts")
    if not isinstance(hash_rows, list) or not isinstance(name_rows, list):
        raise RecoveryError("REFUSED: Media Library conflict evidence is invalid.")
    by_position: dict[int, list[int]] = {position: [] for position in POSITIONS}
    for row in hash_rows:
        if (not isinstance(row, dict) or set(row) != {"attachment_id", "matches_fixed_image"}
                or type(row["attachment_id"]) is not int or row["attachment_id"] <= 0):
            raise RecoveryError("REFUSED: a Media Library hash-conflict row is invalid.")
        marker = str(row["matches_fixed_image"])
        family, _, filename = marker.partition(":")
        if family != FAMILY_KEY or filename not in POSITION_BY_FILENAME:
            raise RecoveryError(
                "REFUSED: a Media Library hash conflict names a file outside the fixed six."
            )
        by_position[POSITION_BY_FILENAME[filename]].append(row["attachment_id"])
    resolved: dict[int, int] = {}
    for position, ids in by_position.items():
        if len(ids) > 1:
            raise RecoveryError(
                f"REFUSED: position {position} matches {len(ids)} live attachments; "
                "the target is ambiguous and nothing is chosen among duplicates."
            )
        if ids:
            resolved[position] = ids[0]
    resolved_ids = set(resolved.values())
    if len(resolved_ids) != len(resolved):
        raise RecoveryError("REFUSED: one live attachment resolves to more than one fixed position.")
    for row in name_rows:
        if (not isinstance(row, dict) or set(row) != {"attachment_id", "filename"}
                or type(row["attachment_id"]) is not int or row["attachment_id"] <= 0):
            raise RecoveryError("REFUSED: a Media Library name-conflict row is invalid.")
        if row["attachment_id"] not in resolved_ids:
            raise RecoveryError(
                f"REFUSED: attachment {row['attachment_id']} carries a fixed open-manway "
                f"file name ({row['filename']!r}) without the approved bytes. Recovery "
                "cannot tell it apart from a genuine target."
            )
    if 1 not in resolved:
        raise RecoveryError(
            "REFUSED: the verified prior upload 1 no longer resolves to any live "
            "attachment with the approved bytes."
        )
    if resolved[1] != PRIOR_VERIFIED_UPLOAD["attachment_id"]:
        raise RecoveryError(
            "REFUSED: position 1 resolves to attachment "
            f"{resolved[1]}, not the recorded fixed attachment "
            f"{PRIOR_VERIFIED_UPLOAD['attachment_id']}."
        )
    return resolved


def verify_resolved_attachment(admin: Any, position: int, attachment_id: int) -> dict[str, Any]:
    """Complete identity + downloaded-hash proof for one already-live fixed asset."""
    fixed = FIXED_IMAGES[position - 1]
    with closed_refusal():
        detail = admin.read_attachment(attachment_id, expected_basename=fixed["filename"])
    if int(detail.get("attachment_id") or detail.get("id") or 0) != int(attachment_id):
        raise RecoveryError(
            f"REFUSED: the attachment screen for {attachment_id} identified a different attachment."
        )
    if str(detail.get("filename") or "") != fixed["filename"]:
        raise RecoveryError(
            f"REFUSED: attachment {attachment_id} is stored under a different file name "
            f"than the fixed {fixed['filename']}."
        )
    source_url = str(detail.get("source_url") or "")
    with closed_refusal():
        media_base.assert_public_upload_url(source_url, expected_basename=fixed["filename"])
        data = media_base.download_public_bytes(source_url, expected_basename=fixed["filename"])
    if len(data) != fixed["bytes"] or not secrets.compare_digest(
            hashlib.sha256(data).hexdigest(), fixed["sha256"]):
        raise RecoveryError(
            f"REFUSED: attachment {attachment_id} does not serve the approved bytes for "
            f"{fixed['filename']}."
        )
    return {"position": position, "attachment_id": int(attachment_id),
            "filename": fixed["filename"], "sha256": fixed["sha256"],
            "bytes": fixed["bytes"], "source_url": source_url}


def build_reconciliation(resolved: dict[int, int],
                         verified: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fixed in FIXED_IMAGES:
        position = fixed["position"]
        attachment_id = resolved.get(position)
        record = verified.get(position)
        if attachment_id is None:
            rows.append({"position": position, "filename": fixed["filename"],
                         "sha256": fixed["sha256"], "bytes": fixed["bytes"],
                         "disposition": "upload_once", "attachment_id": None,
                         "source_url": None})
            continue
        if record is None or record.get("attachment_id") != attachment_id:
            raise RecoveryError(
                f"REFUSED: position {position} has no complete verification record."
            )
        rows.append({"position": position, "filename": fixed["filename"],
                     "sha256": fixed["sha256"], "bytes": fixed["bytes"],
                     "disposition": "reuse_existing", "attachment_id": attachment_id,
                     "source_url": record["source_url"]})
    return rows


def validate_reconciliation(value: Any) -> list[dict[str, Any]]:
    expected_keys = {"position", "filename", "sha256", "bytes", "disposition",
                     "attachment_id", "source_url"}
    if not isinstance(value, list) or len(value) != IMAGE_COUNT:
        raise RecoveryError("REFUSED: reconciliation is not exactly six ordered records.")
    seen_ids: set[int] = set()
    for row, fixed in zip(value, FIXED_IMAGES):
        if not isinstance(row, dict) or set(row) != expected_keys:
            raise RecoveryError("REFUSED: a reconciliation record has the wrong closed schema.")
        if (row["position"] != fixed["position"] or row["filename"] != fixed["filename"]
                or row["sha256"] != fixed["sha256"] or row["bytes"] != fixed["bytes"]):
            raise RecoveryError("REFUSED: reconciliation does not describe the fixed six in order.")
        if row["disposition"] not in ("reuse_existing", "upload_once"):
            raise RecoveryError("REFUSED: a reconciliation disposition is not one of the two fixed values.")
        if row["disposition"] == "upload_once":
            if row["attachment_id"] is not None or row["source_url"] is not None:
                raise RecoveryError("REFUSED: an upload position carries live attachment evidence.")
            continue
        if type(row["attachment_id"]) is not int or row["attachment_id"] <= 0:
            raise RecoveryError("REFUSED: a reuse position has no positive attachment ID.")
        if row["attachment_id"] in seen_ids:
            raise RecoveryError("REFUSED: one attachment is reused at more than one position.")
        seen_ids.add(row["attachment_id"])
        with closed_refusal():
            media_base.assert_public_upload_url(str(row["source_url"] or ""),
                                                expected_basename=fixed["filename"])
    first = value[0]
    if (first["disposition"] != "reuse_existing"
            or first["attachment_id"] != PRIOR_VERIFIED_UPLOAD["attachment_id"]):
        raise RecoveryError(
            "REFUSED: position 1 must resolve to exactly the fixed live attachment "
            f"{PRIOR_VERIFIED_UPLOAD['attachment_id']}."
        )
    return copy.deepcopy(value)


def upload_positions(reconciliation: list[dict[str, Any]]) -> list[int]:
    return [row["position"] for row in reconciliation if row["disposition"] == "upload_once"]


def reuse_positions(reconciliation: list[dict[str, Any]]) -> list[int]:
    return [row["position"] for row in reconciliation if row["disposition"] == "reuse_existing"]


# --------------------------------------------------------------------------
# Guard 1.0.7
#
# The family tool's validators are pinned to the INSTALLED 1.0.5 plugin, so they
# cannot be reused to check a 1.0.7 proof. These are this tool's own, derived
# from the 1.0.7 source and package it pins by hash.
# --------------------------------------------------------------------------
def validate_guard_package() -> dict[str, Any]:
    """Pin the exact 1.0.7 ZIP, PHP source and runtime manifest by size and hash."""
    try:
        zip_raw = GUARD_ZIP_PATH.read_bytes()
        php_raw = GUARD_PLUGIN_PHP_PATH.read_bytes()
        manifest_raw = GUARD_RUNTIME_MANIFEST_PATH.read_bytes()
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryError("REFUSED: the pinned Guard 1.0.7 artifacts are unreadable.") from exc
    if (len(zip_raw) != GUARD_ZIP_BYTES or len(php_raw) != GUARD_PLUGIN_PHP_BYTES
            or len(manifest_raw) != GUARD_RUNTIME_MANIFEST_BYTES
            or not secrets.compare_digest(hashlib.sha256(zip_raw).hexdigest(), GUARD_ZIP_SHA256)
            or not secrets.compare_digest(hashlib.sha256(php_raw).hexdigest(),
                                          GUARD_PLUGIN_PHP_SHA256)
            or not secrets.compare_digest(hashlib.sha256(manifest_raw).hexdigest(),
                                          GUARD_RUNTIME_MANIFEST_SHA256)):
        raise RecoveryError(
            "REFUSED: the Guard 1.0.7 package or source changed; this tool's recovery "
            "capability must be re-derived from the new source before it may stage."
        )
    recovery = manifest.get("fixed_recovery")
    if (not isinstance(manifest, dict) or manifest.get("schema") != GUARD_STATE_SCHEMA
            or not isinstance(recovery, dict)
            or recovery.get("contract") != GUARD_RECOVERY_CONTRACT
            or recovery.get("family") != FAMILY_KEY
            or recovery.get("product_id") != PRODUCT_ID
            or recovery.get("attachment_id") != PRIOR_VERIFIED_UPLOAD["attachment_id"]
            or recovery.get("filename") != PRIOR_VERIFIED_UPLOAD["filename"]
            or recovery.get("bytes") != PRIOR_VERIFIED_UPLOAD["bytes"]
            or recovery.get("sha256") != PRIOR_VERIFIED_UPLOAD["sha256"]
            or recovery.get("prior_operation_sha256") != PRIOR_OPERATION_SHA256
            or recovery.get("prior_plan_sha256") != PRIOR_PLAN_SHA256
            or recovery.get("recoverable_positions") != [2, 3, 4, 5, 6]):
        raise RecoveryError(
            "REFUSED: the Guard 1.0.7 runtime manifest does not carry the exact fixed "
            "Open Manway recovery contract."
        )
    # The Stub Flange reuse contract must survive untouched alongside it.
    reuse = manifest.get("fixed_reuse")
    if (not isinstance(reuse, dict) or reuse.get("family") != "stub_flange"
            or reuse.get("attachment_id") != 4849
            or reuse.get("upload_positions") != [2, 3, 4, 5, 6]):
        raise RecoveryError(
            "REFUSED: Guard 1.0.7 does not preserve the separate fixed Stub Flange reuse contract."
        )
    families = manifest.get("families")
    if (not isinstance(families, dict)
            or sorted(families) != ["elbow_90", "manway_cover", "open_manway", "pipe",
                                    "stub_flange"]
            or families[FAMILY_KEY].get("product_id") != PRODUCT_ID
            or [row.get("filename") for row in families[FAMILY_KEY]["images"]]
                != list(FIXED_FILENAMES)):
        raise RecoveryError("REFUSED: Guard 1.0.7 family protections are not preserved exactly.")
    return {
        "plugin_version": GUARD_PLUGIN_VERSION,
        "state_schema": GUARD_STATE_SCHEMA,
        "proof_schema": GUARD_PROOF_SCHEMA,
        "zip_path": str(GUARD_ZIP_PATH),
        "zip_sha256": GUARD_ZIP_SHA256,
        "zip_bytes": GUARD_ZIP_BYTES,
        "plugin_php_sha256": GUARD_PLUGIN_PHP_SHA256,
        "plugin_php_bytes": GUARD_PLUGIN_PHP_BYTES,
        "runtime_manifest_sha256": GUARD_RUNTIME_MANIFEST_SHA256,
        "fixed_recovery": copy.deepcopy(recovery),
        "fixed_reuse_family": "stub_flange",
        "families": sorted(families),
    }


def guard_capability() -> dict[str, Any]:
    """Pin the plugin contract, then hand back the derived capability record."""
    package = validate_guard_package()
    if (GUARD_PARTIAL_RECOVERY_CAPABILITY["plugin_version"] != GUARD_PLUGIN_VERSION
            or GUARD_PARTIAL_RECOVERY_CAPABILITY["plugin_php_sha256"] != GUARD_PLUGIN_PHP_SHA256
            or GUARD_PARTIAL_RECOVERY_CAPABILITY["state_schema"] != GUARD_STATE_SCHEMA
            or package["plugin_php_sha256"] != GUARD_PLUGIN_PHP_SHA256):
        raise RecoveryError(
            "REFUSED: the pinned media guard changed; its partial-recovery capability "
            "must be re-derived from the new source before this tool may stage."
        )
    return copy.deepcopy(GUARD_PARTIAL_RECOVERY_CAPABILITY)


def validate_live_capability(value: Any) -> dict[str, Any]:
    """The LIVE guard page must publish exactly the pinned capability projection."""
    if value != GUARD_EXPECTED_CAPABILITY:
        raise RecoveryError(
            "REFUSED: the live media guard does not publish the exact Guard 1.0.7 recovery "
            "capability this plan pins. Guard 1.0.5 is still installed, or the installed "
            "build is not the pinned one. NOTHING WAS STAGED. This tool will not deploy, "
            "replace, modify or work around a plugin."
        )
    return copy.deepcopy(GUARD_EXPECTED_CAPABILITY)


def guard_contract() -> dict[str, Any]:
    return {
        "plugin_version": GUARD_PLUGIN_VERSION,
        "state_schema": GUARD_STATE_SCHEMA,
        "proof_schema": GUARD_PROOF_SCHEMA,
        "package": validate_guard_package(),
        "capability": guard_capability(),
        "expected_live_capability": copy.deepcopy(GUARD_EXPECTED_CAPABILITY),
        "ttl_seconds": GUARD_TTL_SECONDS,
        "minimum_completion_margin_seconds": int(MIN_GUARD_COMPLETION_MARGIN.total_seconds()),
        "family": FAMILY_KEY,
        "product_id": PRODUCT_ID,
        "recovery_contract": GUARD_RECOVERY_CONTRACT,
        "fixed_private_exception": copy.deepcopy(family_base.GUARD_PRIVATE_EXCEPTION),
        "image_count": IMAGE_COUNT,
        "deploys_or_modifies_a_plugin": False,
        "read_transport": copy.deepcopy(READ_TRANSPORT),
        "write_transport": copy.deepcopy(WRITE_TRANSPORT),
        "gallery_transport": "guard_owned_admin_post_form",
        "gallery_transport_forbidden": list(WRITE_TRANSPORT["forbidden"]),
        "static_transport_proof": assert_no_woocommerce_write_primitive(),
        "flow": ["origin_proof", "atomic_snapshot", "acquire", "missing_uploads_only",
                 "guarded_snapshot", "owner_bound_admin_post_gallery_commit", "complete"],
    }


def require_guard_supports_recovery(reconciliation: list[dict[str, Any]],
                                    capability: dict[str, Any]) -> None:
    """Refuse before any plan when the pinned guard cannot serve this recovery.

    v1 declared `supports_origin_only_file_enumeration` and then never read it.
    Every flag this recovery depends on is checked here, unconditionally, and the
    owner-bound commit flag is checked too because the gallery write cannot happen
    without it at all.
    """
    reused = reuse_positions(reconciliation)
    missing = upload_positions(reconciliation)
    if reused and not capability.get("supports_existing_fixed_attachment_acquisition"):
        raise RecoveryError(
            "REFUSED: the pinned FRP Depot Media Mutation Guard "
            f"{capability.get('plugin_version')} cannot acquire a guard for {FAMILY_KEY} "
            f"while approved attachments already exist live (positions {reused}, "
            f"attachment IDs {[row['attachment_id'] for row in reconciliation if row['attachment_id']]}). "
            "NOTHING WAS STAGED."
        )
    if (missing and missing != list(range(1, len(missing) + 1))
            and not capability.get("supports_non_prefix_upload_reservation")):
        raise RecoveryError(
            "REFUSED: the pinned FRP Depot Media Mutation Guard "
            f"{capability.get('plugin_version')} reserves fixed upload filenames only as an "
            f"in-order prefix from position 1, but recovery must upload positions {missing}. "
            "NOTHING WAS STAGED."
        )
    if not capability.get("supports_origin_only_file_enumeration"):
        raise RecoveryError(
            "REFUSED: the pinned FRP Depot Media Mutation Guard "
            f"{capability.get('plugin_version')} cannot enumerate the uploads directory, so "
            "origin-only fixed-file collision absence CANNOT BE PROVEN after the superseded "
            "operation's ambiguous upload 2. This tool will not substitute a public URL probe "
            "for that proof. NOTHING WAS STAGED."
        )
    if not capability.get("supports_owner_bound_gallery_commit"):
        raise RecoveryError(
            "REFUSED: the pinned FRP Depot Media Mutation Guard "
            f"{capability.get('plugin_version')} exposes no owner-bound gallery commit route. "
            "A WooCommerce REST PUT carrying Basic credentials cannot satisfy the guard's "
            "user/session/cookie owner check, and the guard cookie is scoped to /wp-admin/, "
            "so the gallery could never be written. NOTHING WAS STAGED."
        )


def validate_guard_snapshot_proof(value: Any, mode: str, guard_active: bool) -> dict[str, Any]:
    """This tool's own 1.0.7 snapshot validator, with the closed recovery keys."""
    base_keys = {
        "schema", "plugin_version", "mode", "family", "generated_utc",
        "attachment_total", "hashed_total", "total_bytes", "snapshot_sha256",
        "complete", "failures", "private_exceptions", "name_conflicts", "hash_conflicts",
        "fixed_matches", "fixed_identities", "guard_active",
    }
    if mode in {"guard_acquired", "guarded_snapshot"}:
        base_keys |= {"guard_expires_utc", "reserved_uploads"}
    optional = {"recovery", "origin_only_proof"} if mode in {
        "guard_acquired", "guarded_snapshot"} else set()
    if not isinstance(value, dict) or not base_keys <= set(value) <= base_keys | optional:
        raise RecoveryError("REFUSED: guard snapshot proof has the wrong closed schema.")
    if (value["schema"] != GUARD_PROOF_SCHEMA
            or value["plugin_version"] != GUARD_PLUGIN_VERSION
            or value["mode"] != mode or value["family"] != FAMILY_KEY
            or value["guard_active"] is not guard_active):
        raise RecoveryError("REFUSED: guard snapshot identity or state is wrong.")
    with closed_refusal():
        family_base._guard_timestamp(value["generated_utc"], "generated")
    for field in ("attachment_total", "hashed_total", "total_bytes"):
        if type(value[field]) is not int or value[field] < 0:
            raise RecoveryError("REFUSED: guard snapshot counters are invalid.")
    _hex64(value["snapshot_sha256"], "guard snapshot digest")
    if type(value["complete"]) is not bool or not isinstance(value["failures"], list):
        raise RecoveryError("REFUSED: guard snapshot completeness evidence is invalid.")
    allowed_failures = {"private_attachment_proof_failed", "unreadable_original", "hash_failed"}
    seen: set[int] = set()
    for failure in value["failures"]:
        if (not isinstance(failure, dict) or set(failure) != {"attachment_id", "reason"}
                or type(failure["attachment_id"]) is not int or failure["attachment_id"] <= 0
                or failure["attachment_id"] in seen
                or failure["reason"] not in allowed_failures):
            raise RecoveryError("REFUSED: guard snapshot failure evidence is invalid.")
        seen.add(failure["attachment_id"])
    with closed_refusal():
        private = family_base._guard_private_exception_rows(value["private_exceptions"])
        for field in ("name_conflicts", "hash_conflicts", "fixed_matches"):
            family_base._guard_match_rows(value[field], field.replace("_", "-"), FAMILY_KEY)
    identities = validate_fixed_identities(value["fixed_identities"])
    if ([{"attachment_id": row["attachment_id"], "fixed_position": row["position"]}
         for row in identities]
            != sorted(value["fixed_matches"], key=lambda row: row["fixed_position"])):
        raise RecoveryError(
            "REFUSED: the guard snapshot's complete fixed identities do not name exactly "
            "its own fixed matches."
        )
    if value["attachment_total"] != (
            value["hashed_total"] + len(private) + len(value["failures"])):
        raise RecoveryError("REFUSED: guard snapshot counters do not reconcile.")
    if mode in {"guard_acquired", "guarded_snapshot"}:
        with closed_refusal():
            family_base._guard_timestamp(value["guard_expires_utc"], "expiry")
        if (type(value["reserved_uploads"]) is not int
                or not 0 <= value["reserved_uploads"] <= IMAGE_COUNT):
            raise RecoveryError("REFUSED: guard reserved-upload count is invalid.")
        if "recovery" in value:
            validate_guard_recovery_record(value["recovery"])
        if "origin_only_proof" in value:
            validate_origin_proof(value["origin_only_proof"])
    return copy.deepcopy(value)


# The complete per-attachment identity Guard 1.0.7 proves server-side. 1.0.6
# proved only post type, non-trash status, basename, bytes and hash, so a JPEG,
# a drafted attachment or a second owner of the same relative path all passed.
FIXED_IDENTITY_KEYS = ("attachment_id", "position", "post_type", "post_status",
                       "mime_type", "relative_path", "basename", "bytes", "sha256",
                       "png_width", "png_height", "png_mode", "png_bit_depth",
                       "png_color_type")


def validate_fixed_identities(value: Any) -> list[dict[str, Any]]:
    """Every fixed identity the guard publishes, checked field by field."""
    if not isinstance(value, list) or len(value) > IMAGE_COUNT:
        raise RecoveryError("REFUSED: the guard fixed-identity evidence is not a bounded list.")
    seen_positions: set[int] = set()
    seen_ids: set[int] = set()
    seen_paths: set[str] = set()
    for row in value:
        if not isinstance(row, dict) or tuple(row) != FIXED_IDENTITY_KEYS:
            raise RecoveryError(
                "REFUSED: a guard fixed-identity record has the wrong closed shape or order."
            )
        position = row["position"]
        if (type(position) is not int or position not in POSITIONS
                or position in seen_positions):
            raise RecoveryError("REFUSED: a guard fixed identity has an invalid fixed position.")
        seen_positions.add(position)
        fixed = FIXED_IMAGES[position - 1]
        attachment_id = row["attachment_id"]
        if (type(attachment_id) is not int or attachment_id <= 0
                or attachment_id in seen_ids):
            raise RecoveryError(
                "REFUSED: a guard fixed identity has an invalid or duplicated attachment ID."
            )
        seen_ids.add(attachment_id)
        if (row["post_type"] != "attachment" or row["post_status"] != "inherit"
                or row["mime_type"] != "image/png"):
            raise RecoveryError(
                "REFUSED: a guard fixed identity is not an exact live PNG attachment "
                "(post type `attachment`, status `inherit`, MIME `image/png`)."
            )
        relative = str(row["relative_path"])
        if (not SAFE_RELATIVE_PATH.match(relative) or ".." in relative
                or Path(relative).name != fixed["filename"]
                or row["basename"] != fixed["filename"] or relative in seen_paths):
            raise RecoveryError(
                "REFUSED: a guard fixed identity does not own one safe, unique relative "
                "path ending in its own fixed basename."
            )
        seen_paths.add(relative)
        if row["bytes"] != fixed["bytes"] or row["sha256"] != fixed["sha256"]:
            raise RecoveryError(
                "REFUSED: a guard fixed identity does not carry the approved bytes and hash."
            )
        _hex64(row["sha256"], "guard fixed identity digest")
        if (row["png_width"] != fixed["width"] or row["png_height"] != fixed["height"]
                or row["png_mode"] != fixed["mode"] or row["png_bit_depth"] != 8
                or row["png_color_type"] != 2):
            raise RecoveryError(
                "REFUSED: a guard fixed identity does not carry the approved PNG "
                "dimensions, bit depth or colour mode."
            )
    return sorted(copy.deepcopy(value), key=lambda row: row["position"])


RECOVERY_RECORD_KEYS = {"contract", "product_id", "prior_operation_sha256",
                        "initial_attachments", "missing_positions",
                        "current_reservations", "bound_uploads",
                        "remaining_positions", "unbound_reservation"}


def validate_guard_recovery_record(value: Any) -> dict[str, Any]:
    """The durable recovery record the server reports back, checked exactly.

    1.0.6's consumer pinned five keys while the plugin's own
    `frpd_mg_recovery_projection()` published nine, so a real snapshot was
    rejected outright. These are the nine the producer emits, and the four
    progress fields are exactly what the one eligibility predicate needs to
    describe expected progress WITHOUT reading caller data.
    """
    if not isinstance(value, dict) or set(value) != RECOVERY_RECORD_KEYS:
        raise RecoveryError("REFUSED: the guard recovery record has the wrong closed schema.")
    if (value["contract"] != GUARD_RECOVERY_CONTRACT or value["product_id"] != PRODUCT_ID
            or value["prior_operation_sha256"] != PRIOR_OPERATION_SHA256):
        raise RecoveryError("REFUSED: the guard recovery record is not the fixed contract.")
    initial = value["initial_attachments"]
    missing = value["missing_positions"]
    if not isinstance(initial, dict) or not isinstance(missing, list):
        raise RecoveryError("REFUSED: the guard recovery record bindings are invalid.")
    bound: list[int] = []
    for filename, attachment_id in initial.items():
        if (filename not in POSITION_BY_FILENAME or type(attachment_id) is not int
                or attachment_id <= 0):
            raise RecoveryError("REFUSED: a guard recovery binding is not a fixed image.")
        bound.append(POSITION_BY_FILENAME[filename])
    if bound != sorted(bound) or len(set(bound)) != len(bound):
        raise RecoveryError("REFUSED: guard recovery bindings are not ascending and unique.")
    if (missing != sorted(set(missing)) or any(type(row) is not int for row in missing)
            or sorted(bound + missing) != list(POSITIONS) or not missing
            or 1 in missing):
        raise RecoveryError(
            "REFUSED: the guard recovery missing-position list is not an exact ascending "
            "complement of its bindings with position 1 always bound."
        )
    if initial.get(PRIOR_VERIFIED_UPLOAD["filename"]) != PRIOR_VERIFIED_UPLOAD["attachment_id"]:
        raise RecoveryError(
            "REFUSED: the guard recovery record does not bind position 1 to the fixed "
            f"verified attachment {PRIOR_VERIFIED_UPLOAD['attachment_id']}."
        )
    # --- durable PROGRESS, which is what the eligibility predicate reads ---
    reservations = value["current_reservations"]
    bound = value["bound_uploads"]
    remaining = value["remaining_positions"]
    unbound = value["unbound_reservation"]
    # PHP encodes an EMPTY map as `[]`, not `{}`, so a guard that has bound nothing
    # yet reports `bound_uploads: []`. That one boundary form is accepted; any other
    # list is a schema error.
    if bound == []:
        bound = {}
        value = dict(value, bound_uploads={})
    if (not isinstance(reservations, list) or not isinstance(bound, dict)
            or not isinstance(remaining, list)):
        raise RecoveryError("REFUSED: the guard recovery progress fields are invalid.")
    bound_positions = []
    for filename, attachment_id in bound.items():
        if (filename not in POSITION_BY_FILENAME or type(attachment_id) is not int
                or attachment_id <= 0):
            raise RecoveryError("REFUSED: a guard bound upload is not a fixed image.")
        bound_positions.append(POSITION_BY_FILENAME[filename])
    bound_positions.sort()
    if bound_positions != missing[:len(bound_positions)]:
        raise RecoveryError(
            "REFUSED: the guard's bound uploads are not the leading ascending prefix of "
            "its own immutable missing-position list."
        )
    bound_ids = list(bound.values())
    if (len(set(bound_ids)) != len(bound_ids)
            or set(bound_ids) & {int(row) for row in initial.values()}):
        raise RecoveryError(
            "REFUSED: a guard bound upload duplicates another binding's attachment ID."
        )
    if remaining != missing[len(bound_positions):]:
        raise RecoveryError(
            "REFUSED: the guard's remaining positions are not the unprocessed suffix of "
            "its immutable missing-position list."
        )
    expected_reservations = [FIXED_IMAGES[position - 1]["filename"]
                             for position in missing[:len(reservations)]]
    if (reservations != expected_reservations
            or not len(bound_positions) <= len(reservations) <= len(bound_positions) + 1):
        raise RecoveryError(
            "REFUSED: the guard reserves uploads out of missing order, or more than one "
            "reservation is unbound."
        )
    if len(reservations) == len(bound_positions):
        if unbound is not None:
            raise RecoveryError(
                "REFUSED: the guard reports an unbound reservation it does not hold."
            )
    else:
        next_position = missing[len(bound_positions)]
        if (not isinstance(unbound, dict) or set(unbound) != {"position", "filename"}
                or unbound["position"] != next_position
                or unbound["filename"] != FIXED_IMAGES[next_position - 1]["filename"]):
            raise RecoveryError(
                "REFUSED: the guard's one unbound reservation is not the next missing position."
            )
    return copy.deepcopy(value)


def require_recovery_guard_snapshot(proof: Any, mode: str,
                                    reconciliation: list[dict[str, Any]],
                                    library: dict[str, Any] | None = None) -> dict[str, Any]:
    """The server's own proof must name exactly the attachments we resolved."""
    guard_active = mode in {"guard_acquired", "guarded_snapshot"}
    checked = validate_guard_snapshot_proof(proof, mode, guard_active)
    if checked["complete"] is not True or checked["failures"]:
        raise RecoveryError(
            "REFUSED: the server-side guard snapshot is incomplete or has unreadable "
            "attachment evidence."
        )
    if checked["attachment_total"] != checked["hashed_total"] + 1:
        raise RecoveryError("REFUSED: the guard snapshot does not reconcile to one private exception.")
    expected = sorted(
        ({"attachment_id": row["attachment_id"], "fixed_position": row["position"]}
         for row in reconciliation if row["disposition"] == "reuse_existing"),
        key=lambda row: row["fixed_position"],
    )
    for field in ("fixed_matches", "hash_conflicts", "name_conflicts"):
        if sorted(checked[field], key=lambda row: row["fixed_position"]) != expected:
            raise RecoveryError(
                f"REFUSED: the guard snapshot's {field} disagree with the browser-side "
                "reconciliation, so the live state is ambiguous."
            )
    if library is not None and checked["attachment_total"] != library.get("library_total"):
        raise RecoveryError("REFUSED: browser and server attachment totals disagree.")
    if mode == "guard_acquired":
        if checked["reserved_uploads"] != 0:
            raise RecoveryError("REFUSED: a newly acquired guard already reserves an upload.")
        record = validate_guard_recovery_record(checked.get("recovery"))
        wanted_missing = upload_positions(reconciliation)
        wanted_initial = {row["filename"]: int(row["attachment_id"])
                          for row in reconciliation if row["disposition"] == "reuse_existing"}
        if record["missing_positions"] != wanted_missing or record["initial_attachments"] != wanted_initial:
            raise RecoveryError(
                "REFUSED: the acquired guard's immutable recovery record disagrees with the "
                "reconciliation this plan was staged from."
            )
        require_origin_proof_matches(checked.get("origin_only_proof"), reconciliation)
    return checked


def require_guarded_recovery_snapshot(proof: Any, baseline_total: int,
                                      final_attachment_ids: list[int],
                                      reserved_uploads: int) -> dict[str, Any]:
    checked = validate_guard_snapshot_proof(proof, "guarded_snapshot", True)
    count = len(final_attachment_ids)
    if count != IMAGE_COUNT or len(set(final_attachment_ids)) != IMAGE_COUNT:
        raise RecoveryError("REFUSED: the final attachment set is not six unique IDs.")
    wanted = [{"attachment_id": attachment_id, "fixed_position": position}
              for position, attachment_id in enumerate(final_attachment_ids, 1)]
    if (checked["complete"] is not True or checked["failures"]
            or checked["attachment_total"] != baseline_total
            or checked["hashed_total"] != baseline_total - 1
            or checked["reserved_uploads"] != reserved_uploads
            or sorted(checked["fixed_matches"], key=lambda row: row["fixed_position"]) != wanted
            or sorted(checked["hash_conflicts"], key=lambda row: row["fixed_position"]) != wanted
            or sorted(checked["name_conflicts"], key=lambda row: row["fixed_position"]) != wanted):
        raise RecoveryError(
            "The guarded snapshot does not prove the exact six recovered open-manway attachments."
        )
    return checked


def validate_guard_completion_proof(value: Any, attachment_ids: list[int]) -> dict[str, Any]:
    expected_keys = {"schema", "plugin_version", "mode", "family", "product_id",
                     "attachment_ids", "attachment_identities", "attachment_total",
                     "snapshot_sha256"}
    if (not isinstance(value, dict) or set(value) != expected_keys
            or value["schema"] != GUARD_PROOF_SCHEMA
            or value["plugin_version"] != GUARD_PLUGIN_VERSION
            or value["mode"] != "guard_completed" or value["family"] != FAMILY_KEY
            or value["product_id"] != PRODUCT_ID
            or value["attachment_ids"] != attachment_ids
            or len(attachment_ids) != IMAGE_COUNT
            or len(set(attachment_ids)) != IMAGE_COUNT
            or type(value["attachment_total"]) is not int
            or value["attachment_total"] < IMAGE_COUNT):
        raise RecoveryError("REFUSED: guard completion proof is not exact.")
    _hex64(value["snapshot_sha256"], "guard completion digest")
    identities = validate_fixed_identities(value["attachment_identities"])
    if (value["attachment_identities"] != identities
            or [row["position"] for row in identities] != list(POSITIONS)
            or [row["attachment_id"] for row in identities] != list(attachment_ids)):
        raise RecoveryError(
            "REFUSED: the completion proof does not carry one complete fixed identity for "
            "each of the six committed positions, in order."
        )
    return copy.deepcopy(value)


RECOVERY_GALLERY_PROOF_KEYS = {
    "schema", "plugin_version", "mode", "contract", "family", "product_id",
    "prior_operation_sha256", "attachment_ids", "gallery_etag_before",
    "gallery_etag_after", "state_status", "state_version", "transport",
}


def validate_recovery_gallery_proof(value: Any, final_attachment_ids: list[int],
                                    if_match: str) -> dict[str, Any]:
    """The owner-bound admin-post commit proof, checked exactly and secret-free."""
    if not isinstance(value, dict) or set(value) != RECOVERY_GALLERY_PROOF_KEYS:
        raise RecoveryError("REFUSED: the recovery gallery proof has the wrong closed schema.")
    if (value["schema"] != GUARD_PROOF_SCHEMA
            or value["plugin_version"] != GUARD_PLUGIN_VERSION
            or value["mode"] != "recovery_gallery_committed"
            or value["contract"] != GUARD_RECOVERY_CONTRACT
            or value["family"] != FAMILY_KEY or value["product_id"] != PRODUCT_ID
            or value["prior_operation_sha256"] != PRIOR_OPERATION_SHA256
            or value["attachment_ids"] != list(final_attachment_ids)
            or value["gallery_etag_before"] != if_match
            or value["state_status"] != "gallery"
            or type(value["state_version"]) is not int or value["state_version"] < 1
            or value["transport"] != "internal_authenticated_rest_do_request"):
        raise RecoveryError("REFUSED: the recovery gallery proof is not the exact fixed commit.")
    for field in ("gallery_etag_before", "gallery_etag_after"):
        text = str(value[field])
        if len(text) != 66 or text[0] != '"' or text[-1] != '"':
            raise RecoveryError("REFUSED: a recovery gallery ETag is not a quoted SHA-256.")
        _hex64(text[1:-1], "recovery gallery etag")
    if value["gallery_etag_after"] == value["gallery_etag_before"]:
        raise RecoveryError("REFUSED: the recovery gallery ETag did not change across the commit.")
    return copy.deepcopy(value)


def require_guard_completion_margin(proof: dict[str, Any]) -> None:
    expires = datetime.fromisoformat(str(proof["guard_expires_utc"]).replace("Z", "+00:00"))
    if expires - utc_now() < MIN_GUARD_COMPLETION_MARGIN:
        raise RecoveryError(
            "REFUSED: fewer than two minutes remain before the active guard expires."
        )


def require_authorization_margin(plan: dict[str, Any]) -> None:
    expires = datetime.fromisoformat(str(plan["expires_utc"]))
    if expires - utc_now() < MIN_AUTHORIZATION_MARGIN:
        raise RecoveryError(
            "REFUSED: fewer than two minutes remain before plan expiry; stage a fresh plan."
        )


# --------------------------------------------------------------------------
# Product
# --------------------------------------------------------------------------
def product_evidence(vault: dict[str, Any] | None = None) -> dict[str, Any]:
    with closed_refusal():
        evidence = family_base.product_evidence(FAMILY_KEY, vault)
    identity = evidence.get("identity") or {}
    if (evidence.get("product_id") != PRODUCT_ID or identity.get("id") != PRODUCT_ID
            or identity.get("name") != PRODUCT_LABEL or identity.get("sku") != PRODUCT_SKU
            or identity.get("type") != PRODUCT_TYPE or identity.get("status") != PRODUCT_STATUS
            or identity.get("permalink") != PRODUCT_PERMALINK):
        raise RecoveryError("REFUSED: fixed product 1397 identity is not the exact FRP MANWAY record.")
    return evidence


def current_gallery_ids(product: dict[str, Any]) -> list[int]:
    gallery = product.get("before_gallery")
    if (not isinstance(gallery, list)
            or any(not isinstance(row, dict) or set(row) != {"id", "alt"}
                   or type(row["id"]) is not int or row["id"] <= 0
                   or not isinstance(row["alt"], str) for row in gallery)):
        raise RecoveryError("REFUSED: the current product gallery is not an exact ID/alt list.")
    ids = [row["id"] for row in gallery]
    if len(set(ids)) != len(ids):
        raise RecoveryError("REFUSED: the current product gallery repeats an attachment ID.")
    return ids


def final_attachment_ids_for(reconciliation: list[dict[str, Any]],
                             uploaded: dict[int, int]) -> list[int]:
    ids: list[int] = []
    for row in reconciliation:
        if row["disposition"] == "reuse_existing":
            ids.append(int(row["attachment_id"]))
            continue
        landed = uploaded.get(row["position"])
        if type(landed) is not int or landed <= 0:
            raise RecoveryError(
                f"REFUSED: position {row['position']} has no verified uploaded attachment ID."
            )
        ids.append(landed)
    if len(ids) != IMAGE_COUNT or len(set(ids)) != IMAGE_COUNT:
        raise RecoveryError("REFUSED: the final gallery is not six unique ordered attachment IDs.")
    return ids


# --------------------------------------------------------------------------
# THE ONE PROGRESSION MODEL
#
# v2.0.0 compared the EVOLVING live reconciliation to the FROZEN staged one, so
# the first successful upload made every later check refuse -- after the
# permanent attempt lock, which turned an ordinary success into
# INDETERMINATE_NO_RETRY. The staged reconciliation is the immutable ACQUISITION
# BASELINE; what live state should look like after N uploads is computed from the
# guard's own durable record, never from anything the caller passes in.
# --------------------------------------------------------------------------
EMPTY_PROGRESS: dict[str, Any] = {
    "initial_attachments": None, "missing_positions": None, "bound_uploads": {},
    "completed_positions": [], "remaining_positions": None, "reserved_uploads": 0,
    "unbound_reservation": None, "phase": "pre_acquisition",
}


def guard_progress(guard_owner: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize durable guard state into one phase/progress record."""
    if guard_owner is None:
        return copy.deepcopy(EMPTY_PROGRESS)
    record = validate_guard_recovery_record(guard_owner.get("recovery"))
    bound = {POSITION_BY_FILENAME[filename]: int(attachment_id)
             for filename, attachment_id in record["bound_uploads"].items()}
    completed = sorted(bound)
    remaining = list(record["remaining_positions"])
    unbound = record["unbound_reservation"]
    if unbound is not None:
        phase = "upload_in_flight"
    elif remaining:
        phase = "uploading"
    else:
        phase = "gallery_ready"
    return {
        "initial_attachments": dict(record["initial_attachments"]),
        "missing_positions": list(record["missing_positions"]),
        "bound_uploads": bound,
        "completed_positions": completed,
        "remaining_positions": remaining,
        "reserved_uploads": len(record["current_reservations"]),
        "unbound_reservation": copy.deepcopy(unbound),
        "phase": phase,
    }


def reconciliation_projection(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """Identity without the live source URL, which only the server can know."""
    return [(row["position"], row["filename"], row["sha256"], row["bytes"],
             row["disposition"], row["attachment_id"]) for row in rows]


def expected_progressed_reconciliation(baseline: list[dict[str, Any]],
                                       progress: dict[str, Any]) -> list[dict[str, Any]]:
    """Exactly what live reconciliation must look like at this durable progress."""
    rows = copy.deepcopy(baseline)
    for row in rows:
        landed = progress["bound_uploads"].get(row["position"])
        if landed is None:
            continue
        if row["disposition"] != "upload_once":
            raise RecoveryError(
                f"REFUSED: the guard reports position {row['position']} as an upload it "
                "bound, but the acquisition baseline reused an existing attachment there."
            )
        row["disposition"] = "reuse_existing"
        row["attachment_id"] = landed
    return rows


def is_already_complete(product: dict[str, Any],
                        reconciliation: list[dict[str, Any]]) -> bool:
    if upload_positions(reconciliation):
        return False
    return current_gallery_ids(product) == [int(row["attachment_id"]) for row in reconciliation]


def gallery_precondition(product: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        canonical(current_gallery_ids(product)).encode("ascii")).hexdigest()
    return f'"{digest}"'


# --------------------------------------------------------------------------
# THE ONE ELIGIBILITY PREDICATE
#
# v1's version took only frozen preflight objects and never saw the guard
# capability, the live guard-owner state, the complete-library evidence or the
# reservation arithmetic, so drift could only be discovered by the server DURING
# a write -- after the uploads and the permanent no-retry lock had landed. This
# one takes every input a side effect depends on, and every caller passes FRESHLY
# READ values.
# --------------------------------------------------------------------------
def assert_recovery_eligibility(*, prior_evidence: dict[str, Any],
                                local: list[dict[str, Any]],
                                capability: dict[str, Any],
                                library: Any,
                                product: dict[str, Any],
                                reconciliation: Any,
                                origin_proof: Any,
                                expected_product: dict[str, Any] | None = None,
                                expected_reconciliation: Any = None,
                                guard_owner: dict[str, Any] | None = None,
                                completed_upload_positions: list[int] | None = None,
                                next_upload_position: int | None = None,
                                final_attachment_ids: Any = None) -> None:
    """Used unchanged at stage acceptance, commit preflight, and every side effect."""
    if prior_evidence != validate_prior_evidence():
        raise RecoveryError("REFUSED: the permanent prior evidence changed after it was recorded.")
    if local != validate_local_images():
        raise RecoveryError("REFUSED: the fixed source files changed after they were recorded.")
    if capability != guard_capability():
        raise RecoveryError(
            "REFUSED: the pinned media guard capability changed after it was recorded."
        )
    require_complete_library_evidence(library)
    # The product changes EXACTLY ONCE in this operation: at the one gallery
    # commit. Before it, the fresh read must equal the staged baseline outright.
    # After it, the gallery IS the recovered six and WordPress has moved
    # `date_modified_gmt`; every other field, including the whole protected
    # projection and its fingerprint, must still be byte-identical. Comparing the
    # post-commit product to the pre-commit baseline outright is what made
    # `complete_guard` -- a real state transition that runs after the write --
    # refuse and turn a completed recovery into INDETERMINATE_NO_RETRY.
    gallery_written = (final_attachment_ids is not None
                       and current_gallery_ids(product) == list(final_attachment_ids))
    with closed_refusal():
        family_base.assert_product_eligibility(
            FAMILY_KEY, product, None if gallery_written else expected_product)
    if gallery_written and expected_product is not None:
        moved = ("before_gallery", "date_modified_gmt")
        if ({field: value for field, value in product.items() if field not in moved}
                != {field: value for field, value in expected_product.items()
                    if field not in moved}):
            raise RecoveryError(
                "REFUSED: a product field other than the committed gallery and its "
                "modification timestamp changed during this recovery."
            )
    checked = validate_reconciliation(reconciliation)
    require_guard_supports_recovery(checked, capability)
    progress = guard_progress(guard_owner)
    baseline = (validate_reconciliation(expected_reconciliation)
                if expected_reconciliation is not None else None)
    if baseline is not None:
        expected_rows = expected_progressed_reconciliation(baseline, progress)
        if reconciliation_projection(checked) != reconciliation_projection(expected_rows):
            raise RecoveryError(
                "REFUSED: live open-manway state is not the acquisition baseline advanced "
                f"by exactly the {len(progress['bound_uploads'])} upload(s) the guard's own "
                "durable record accounts for. Expected "
                f"{reconciliation_projection(expected_rows)}, read "
                f"{reconciliation_projection(checked)}. An unexplained attachment at a "
                "future missing position, a disappeared binding or a substituted "
                "attachment is external drift and never a permitted progression."
            )
    require_origin_proof_matches(origin_proof, checked)

    live_ids = current_gallery_ids(product)
    resolved_ids = [int(row["attachment_id"]) for row in checked
                    if row["disposition"] == "reuse_existing"]
    # The IMMUTABLE missing list, not the shrinking live one: after N uploads the
    # live reconciliation legitimately has fewer upload positions left.
    immutable_missing = (upload_positions(baseline) if baseline is not None
                         else upload_positions(checked))
    if not immutable_missing:
        raise RecoveryError(
            "REFUSED: no approved asset is missing, so there is no recovery upload to make."
        )
    if (guard_owner is None and not upload_positions(checked)
            and live_ids == [int(row["attachment_id"]) for row in checked]):
        raise RecoveryError(
            "REFUSED: product 1397 already carries the exact six recovered assets in order; "
            "there is nothing to recover."
        )
    if guard_owner is None:
        # Before acquisition the gallery must not already hold any approved
        # recovery attachment. Once this guard owns the operation its own bound
        # uploads explain every one of them, and after the one gallery commit the
        # live gallery IS the recovered set.
        conflicting = sorted(set(live_ids) & set(resolved_ids))
        if conflicting:
            raise RecoveryError(
                "REFUSED: the current gallery already contains approved recovery attachments "
                f"{conflicting} in a partial or conflicting order. Unrelated gallery drift is "
                "acceptable; a partially applied recovery is not."
            )
    else:
        # The owner snapshot must belong to THIS recovery, still be inside its
        # completion margin, and its durable progress must account for every
        # upload this operation has completed.
        if baseline is not None:
            if progress["missing_positions"] != immutable_missing:
                raise RecoveryError(
                    "REFUSED: the live guard's immutable missing-position list disagrees "
                    "with the acquisition baseline this plan was staged from."
                )
            if progress["initial_attachments"] != {
                    row["filename"]: int(row["attachment_id"]) for row in baseline
                    if row["disposition"] == "reuse_existing"}:
                raise RecoveryError(
                    "REFUSED: the live guard's immutable acquisition bindings disagree with "
                    "the acquisition baseline this plan was staged from."
                )
        completed = list(completed_upload_positions or [])
        if completed != progress["completed_positions"]:
            raise RecoveryError(
                f"REFUSED: this operation recorded uploads {completed} but the guard's own "
                f"durable record binds {progress['completed_positions']}. The two must agree "
                "exactly before any further side effect."
            )
        if int(guard_owner.get("reserved_uploads", -1)) != progress["reserved_uploads"]:
            raise RecoveryError(
                "REFUSED: the live guard reserves "
                f"{guard_owner.get('reserved_uploads')} uploads but its durable record holds "
                f"{progress['reserved_uploads']}; a reservation is unaccounted for."
            )
        if progress["unbound_reservation"] is not None:
            raise RecoveryError(
                "REFUSED: the guard holds one reserved-but-unbound upload at position "
                f"{progress['unbound_reservation']['position']}. That upload's outcome is "
                "ambiguous; nothing further may be attempted."
            )
        require_guard_completion_margin(guard_owner)
        if next_upload_position is not None:
            if (not progress["remaining_positions"]
                    or next_upload_position != progress["remaining_positions"][0]):
                raise RecoveryError(
                    f"REFUSED: position {next_upload_position} is not the next permitted "
                    "upload derived from the immutable recovery state."
                )
        if final_attachment_ids is not None and progress["remaining_positions"]:
            raise RecoveryError(
                "REFUSED: a gallery payload requires all six bindings complete; positions "
                f"{progress['remaining_positions']} are still unprocessed."
            )
    if final_attachment_ids is not None:
        if (not isinstance(final_attachment_ids, list)
                or len(final_attachment_ids) != IMAGE_COUNT
                or any(type(value) is not int or value <= 0 for value in final_attachment_ids)
                or len(set(final_attachment_ids)) != IMAGE_COUNT):
            raise RecoveryError(
                "REFUSED: the gallery payload requires exactly six unique positive attachment IDs."
            )
        for row, value in zip(checked, final_attachment_ids):
            if row["disposition"] == "reuse_existing" and int(row["attachment_id"]) != value:
                raise RecoveryError(
                    "REFUSED: the gallery payload moved a reused attachment out of its fixed position."
                )
        if final_attachment_ids[0] != PRIOR_VERIFIED_UPLOAD["attachment_id"]:
            raise RecoveryError(
                "REFUSED: gallery position 1 must be the fixed live attachment "
                f"{PRIOR_VERIFIED_UPLOAD['attachment_id']}."
            )


# --------------------------------------------------------------------------
# Plan identity and immutable local evidence
# --------------------------------------------------------------------------
def risk_disclosure(reconciliation: list[dict[str, Any]]) -> str:
    missing = upload_positions(reconciliation)
    reused = reuse_positions(reconciliation)
    return (
        "NOT ATOMIC; NO ROLLBACK. This recovery reuses the already-live approved attachments at "
        f"positions {reused} byte-for-byte without rename, metadata edit, detach or delete, then "
        f"performs {len(missing)} independent WordPress uploads for the missing positions {missing} "
        "in order, then one independent images-only WooCommerce gallery PUT containing only the six "
        "returned media IDs, then one guard-completion write. One server-side guarded-commit "
        "acquisition happens first. If upload N fails, it may itself have landed, earlier verified "
        "uploads remain live but unattached, later uploads do not happen, the gallery is not "
        "written, and the plan is permanently no-retry. If the gallery PUT lands but the fresh "
        "API, media, public-page and protected-field checks do not all pass, the product may be "
        "changed and the plan is permanently indeterminate. A failure after guard acquisition can "
        "leave the guard active until its authoritative 30-minute database expiry, blocking other "
        "attachment mutations during that interval. There is no retry, replay, restage, delete, "
        "trash, detach, rename, media edit, cleanup, guard clearing, product revert or second "
        "gallery write anywhere in this tool. The superseded plan, attempt, every event-journal "
        "file and the receipt line are read-only permanent evidence, byte-pinned, and are never "
        "modified. GALLERY TRANSPORT: the gallery write is submitted through the guard's one fixed "
        "authenticated admin-post form in the guard-owning browser, carrying only the action, its "
        "nonce and this plan's non-secret If-Match gallery hash; product 1397 and the six ordered "
        "IDs are derived by the server from its own durable state. READ TRANSPORT: product "
        "identity, gallery order and the protected-field fingerprint are verified with read-only "
        "WooCommerce GETs through the commissioned vault. No WooCommerce write primitive of any "
        "kind -- api_request, POST, PUT, PATCH, DELETE, consumer-key write, external loopback "
        "HTTP call, generic REST write route or product edit form -- exists anywhere in this "
        "module, which is proven against its own source before any plan loads. ORIGIN-FILE PROOF SCOPE: origin-only collision absence is proven by the "
        "guard plugin's own bounded enumeration of the WordPress uploads directory and its "
        "supported year/month directories, correlated to exact attachment ownership. An "
        "unprovable or incomplete enumeration is a FREE REFUSAL before any attempt lock, never an "
        "assumption of absence."
    )


def writes_if_committed(reconciliation: list[dict[str, Any]]) -> list[str]:
    missing = upload_positions(reconciliation)
    return [
        "one fixed server-side guarded-commit acquisition",
        f"reuse existing attachments at positions {reuse_positions(reconciliation)} "
        "without any attachment mutation",
        f"{len(missing)} independent authenticated WordPress media uploads for positions {missing}",
        "one owner-bound guard admin-post gallery commit carrying only the fixed action, its "
        f"nonce and this plan's If-Match hash; the server writes product {PRODUCT_ID}'s six "
        "ordered image IDs from its own durable state",
        "one fixed guard-completion state transition after complete verification",
    ]


FORBIDDEN = [
    "delete", "trash", "detach", "rename", "media edit", "alt text", "cleanup",
    "rollback", "retry", "replay", "restage", "second PUT", "guard clearing",
    "product content", "excerpt", "price", "sale price", "stock", "SKU", "tax",
    "shipping", "dimensions", "downloadable files", "status", "category",
    "attribute", "variation", "review", "customer", "order", "payment", "plugin",
    "theme", "setting", "user", "email", "Zoho", "Drive",
    "wc.api_request gallery transport",
    "basic WooCommerce credentials for the gallery write",
    "consumer-key write", "external loopback HTTP", "generic REST write route",
    "browser-supplied image IDs", "product edit form", "public URL origin probe",
]


def _validated_plan_sha(plan_sha256: str) -> str:
    return _hex64(plan_sha256, "plan SHA-256")


def stage_registry_path(plan_sha256: str) -> Path:
    return STAGE_REGISTRY_DIR / f"{_validated_plan_sha(plan_sha256)}.stage.json"


def lock_path(operation_sha256: str) -> Path:
    return ATTEMPT_LEDGER_DIR / f"{_validated_plan_sha(operation_sha256)}.attempt.json"


def reservation_path(operation_sha256: str) -> Path:
    return RESERVATION_DIR / f"{_validated_plan_sha(operation_sha256)}.reserved.json"


def result_path(operation_sha256: str) -> Path:
    return RESULT_DIR / f"{_validated_plan_sha(operation_sha256)}.result.json"


def journal_path(operation_sha256: str, event: str) -> Path:
    if not event or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in event):
        raise RecoveryError("REFUSED: event journal key is invalid.")
    return JOURNAL_DIR / _validated_plan_sha(operation_sha256) / f"{event}.json"


def write_json(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    """Immutable-by-construction local evidence, reusing the proven writer."""
    with closed_refusal():
        family_base.write_json(path, value, exclusive=exclusive)


def _registry_key() -> bytes:
    path = REGISTRY_KEY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(str(path), flags, 0o600)
        except FileExistsError:
            descriptor = None
        if descriptor is not None:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(secrets.token_bytes(32))
                handle.flush()
                os.fsync(handle.fileno())
    if (not path.is_file() or media_base._is_reparse_point(path)
            or getattr(path.stat(), "st_nlink", 1) != 1):
        raise RecoveryError("REFUSED: local stage-registry integrity key is missing or aliased.")
    key = path.read_bytes()
    if len(key) != 32:
        raise RecoveryError("REFUSED: local stage-registry integrity key is invalid.")
    return key


def _registry_mac(registry_core: dict[str, Any]) -> str:
    return hmac.new(_registry_key(), canonical(registry_core).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def canonical_plan_filename(created: datetime, plan_sha256: str) -> str:
    stamp = created.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_open_manway_recovery_{plan_sha256[:16]}.json"


def fixed_plan_path(raw: str) -> Path:
    path = Path(raw).resolve()
    if (path.parent != PLAN_DIR.resolve() or path.suffix.casefold() != ".json"
            or not path.is_file() or media_base._is_reparse_point(path)):
        raise RecoveryError(
            "REFUSED: plan must be a regular non-reparse JSON file directly inside the "
            "fixed recovery plan folder."
        )
    if getattr(path.stat(), "st_nlink", 1) != 1:
        raise RecoveryError("REFUSED: hard-linked plan aliases are not accepted.")
    return path


def superseded_record() -> dict[str, Any]:
    return {
        "plan_path": str(PRIOR_PLAN_PATH),
        "plan_sha256": PRIOR_PLAN_SHA256,
        "plan_file_sha256": PRIOR_PLAN_FILE_SHA256,
        "operation_sha256": PRIOR_OPERATION_SHA256,
        "result_path": str(PRIOR_RESULT_PATH),
        "result_file_sha256": PRIOR_RESULT_FILE_SHA256,
        "attempt_path": str(PRIOR_ATTEMPT_PATH),
        "attempt_file_sha256": PRIOR_ATTEMPT_FILE_SHA256,
        "status": "INDETERMINATE_NO_RETRY",
        "failed_stage": PRIOR_FAILED_STAGE,
        "mutated_by_this_tool": False,
        "retryable": False,
    }


def operation_sha256(local: list[dict[str, Any]], product: dict[str, Any],
                     reconciliation: list[dict[str, Any]],
                     origin_proof: dict[str, Any],
                     prior_evidence: dict[str, Any]) -> str:
    semantic = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "action": ACTION,
        "family": FAMILY_KEY,
        "product_id": PRODUCT_ID,
        "files": [{field: row[field] for field in ("position", "filename", "bytes", "sha256")}
                  for row in local],
        "reconciliation": reconciliation,
        "origin_proof_files": [
            {"position": row["position"], "basename": row["basename"],
             "discovered": row["discovered"],
             "owner_attachment_ids": row["owner_attachment_ids"]}
            for row in origin_proof["files"]
        ],
        "prior_evidence": prior_evidence,
        "superseded": superseded_record(),
        "product_identity": product["identity"],
        "protected_fingerprint": product["protected_fingerprint"],
        "before_gallery": product["before_gallery"],
        "guard_contract": guard_contract(),
    }
    return digest_for(semantic)


def _verify_stage_registry(path: Path, plan: dict[str, Any], raw: bytes,
                           created: datetime, *, authenticate: bool) -> None:
    plan_sha256 = plan["sha256"]
    expected_name = canonical_plan_filename(created, plan_sha256)
    if path.name != expected_name:
        raise RecoveryError("REFUSED: plan filename is not its canonical staged hash-bearing name.")
    registry_path = stage_registry_path(plan_sha256)
    if (not registry_path.is_file() or media_base._is_reparse_point(registry_path)
            or getattr(registry_path.stat(), "st_nlink", 1) != 1):
        raise RecoveryError("REFUSED: immutable stage registry evidence is missing or aliased.")
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError("REFUSED: immutable stage registry evidence is unreadable.") from exc
    expected_core = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "plan_sha256": plan_sha256,
        "operation_sha256": plan["operation_sha256"],
        "plan_file_sha256": hashlib.sha256(raw).hexdigest(),
        "plan_path": str(path),
        "plan_filename": expected_name,
        "product_id": PRODUCT_ID,
        "nonce": plan["nonce"],
        "created_utc": plan["created_utc"],
        "expires_utc": plan["expires_utc"],
        "superseded_operation_sha256": PRIOR_OPERATION_SHA256,
    }
    if (not isinstance(registry, dict) or set(registry) != set(expected_core) | {"hmac_sha256"}
            or {key: registry.get(key) for key in expected_core} != expected_core
            or not isinstance(registry.get("hmac_sha256"), str)
            or len(registry["hmac_sha256"]) != 64):
        raise RecoveryError("REFUSED: plan does not match its immutable stage registry.")
    if authenticate and not hmac.compare_digest(registry["hmac_sha256"],
                                                _registry_mac(expected_core)):
        raise RecoveryError("REFUSED: stage registry authentication failed.")


def stage_one(local: list[dict[str, Any]], product: dict[str, Any],
              reconciliation: list[dict[str, Any]], origin_proof: dict[str, Any],
              prior_evidence: dict[str, Any], library: dict[str, Any],
              guard_stage_snapshot: dict[str, Any], live_capability: dict[str, Any],
              guard_release: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    created = utc_now()
    operation = operation_sha256(local, product, reconciliation, origin_proof, prior_evidence)
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "origin": EXACT_ORIGIN,
        "action": ACTION,
        "family": FAMILY_KEY,
        "family_label": PRODUCT_LABEL,
        "product_id": PRODUCT_ID,
        "read_transport": copy.deepcopy(READ_TRANSPORT),
        "write_transport": copy.deepcopy(WRITE_TRANSPORT),
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "operation_sha256": operation,
        "superseded": superseded_record(),
        "prior_evidence": prior_evidence,
        "prior_guard_release": guard_release,
        "files": local,
        "reconciliation": reconciliation,
        "reuse_positions": reuse_positions(reconciliation),
        "upload_positions": upload_positions(reconciliation),
        "origin_proof": origin_proof,
        "product_before": product,
        "library_check": library,
        "guard_contract": guard_contract(),
        "guard_stage_snapshot": guard_stage_snapshot,
        "guard_live_capability": live_capability,
        "gallery_payload_template": [
            {"position": row["position"], "filename": row["filename"],
             "sha256": row["sha256"], "disposition": row["disposition"],
             "attachment_id": row["attachment_id"]}
            for row in reconciliation
        ],
        "risk": risk_disclosure(reconciliation),
        "writes_if_committed": writes_if_committed(reconciliation),
        "forbidden": list(FORBIDDEN),
    }
    plan = {**core, "sha256": digest_for(core)}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    path = (PLAN_DIR / canonical_plan_filename(created, plan["sha256"])).resolve()
    write_json(path, plan, exclusive=True)
    if getattr(path.stat(), "st_nlink", 1) != 1 or media_base._is_reparse_point(path):
        raise RecoveryError("REFUSED: newly staged plan is aliased or a reparse point.")
    registry_core = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "plan_sha256": plan["sha256"],
        "operation_sha256": operation,
        "plan_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "plan_path": str(path),
        "plan_filename": path.name,
        "product_id": PRODUCT_ID,
        "nonce": plan["nonce"],
        "created_utc": plan["created_utc"],
        "expires_utc": plan["expires_utc"],
        "superseded_operation_sha256": PRIOR_OPERATION_SHA256,
    }
    write_json(stage_registry_path(plan["sha256"]),
               {**registry_core, "hmac_sha256": _registry_mac(registry_core)}, exclusive=True)
    append_receipt("open_manway_gallery_recovery_plan_staged", str(path))
    return path, plan


PLAN_KEYS = {
    "schema_version", "tool", "tool_version", "origin", "action", "family",
    "family_label", "product_id", "read_transport", "write_transport",
    "created_utc", "expires_utc",
    "nonce", "operation_sha256", "superseded", "prior_evidence", "prior_guard_release",
    "files", "reconciliation", "reuse_positions", "upload_positions", "origin_proof",
    "product_before", "library_check", "guard_contract", "guard_stage_snapshot",
    "guard_live_capability", "gallery_payload_template", "risk", "writes_if_committed",
    "forbidden", "sha256",
}


def load_plan(path: Path, *, authenticate_registry: bool = True) -> dict[str, Any]:
    path = Path(path).resolve()
    try:
        raw = path.read_bytes()
        loaded = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryError("REFUSED: plan is unreadable.") from exc
    if not isinstance(loaded, dict):
        raise RecoveryError("REFUSED: plan must contain exactly one object.")
    plan = dict(loaded)
    if set(plan) != PLAN_KEYS:
        raise RecoveryError("REFUSED: plan fields are not the exact closed schema.")
    saved = str(plan.pop("sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(plan)):
        raise RecoveryError("REFUSED: plan hash failed; the plan changed after staging.")
    if saved in SUPERSEDED_PLAN_SHA256 or plan.get("tool_version") in SUPERSEDED_TOOL_VERSIONS:
        raise RecoveryError(
            "REFUSED: this plan belongs to a superseded build of the recovery tool and can "
            "never commit."
        )
    if (plan.get("schema_version") != SCHEMA_VERSION or plan.get("tool") != TOOL_NAME
            or plan.get("tool_version") != TOOL_VERSION or plan.get("origin") != EXACT_ORIGIN
            or plan.get("action") != ACTION or plan.get("family") != FAMILY_KEY
            or plan.get("family_label") != PRODUCT_LABEL
            or plan.get("product_id") != PRODUCT_ID):
        raise RecoveryError("REFUSED: plan schema/tool/version/origin/action/identity is invalid.")
    if plan.get("read_transport") != READ_TRANSPORT:
        raise RecoveryError(
            "REFUSED: plan does not declare the exact read-only product verification transport."
        )
    if plan.get("write_transport") != WRITE_TRANSPORT:
        raise RecoveryError(
            "REFUSED: plan does not authorize exactly the owner-bound admin-post gallery "
            "commit as its ONLY write transport."
        )
    assert_no_woocommerce_write_primitive()
    nonce = plan.get("nonce")
    if (not isinstance(nonce, str) or len(nonce) != 32
            or any(char not in "0123456789abcdef" for char in nonce)):
        raise RecoveryError("REFUSED: plan nonce is invalid.")
    try:
        created = datetime.fromisoformat(str(plan["created_utc"]))
        expires = datetime.fromisoformat(str(plan["expires_utc"]))
    except (KeyError, ValueError) as exc:
        raise RecoveryError("REFUSED: plan timestamps are invalid.") from exc
    if (created.tzinfo is None or expires.tzinfo is None
            or expires - created != timedelta(hours=PLAN_LIFETIME_HOURS)):
        raise RecoveryError("REFUSED: plan lifetime is not exactly 24 hours.")
    now = utc_now()
    if created > now + timedelta(minutes=5):
        raise RecoveryError("REFUSED: plan creation time is in the future.")
    if now >= expires:
        raise RecoveryError("REFUSED: plan expired; stage a fresh read-only plan.")
    if plan.get("superseded") != superseded_record():
        raise RecoveryError("REFUSED: plan does not carry the exact superseded operation identity.")
    prior_evidence = validate_prior_evidence()
    if plan.get("prior_evidence") != prior_evidence:
        raise RecoveryError("REFUSED: plan does not carry the exact permanent prior evidence.")
    expected_local = validate_local_images()
    if plan.get("files") != expected_local:
        raise RecoveryError("REFUSED: plan files do not match the fixed approved sources.")
    reconciliation = validate_reconciliation(plan.get("reconciliation"))
    if (plan.get("reuse_positions") != reuse_positions(reconciliation)
            or plan.get("upload_positions") != upload_positions(reconciliation)):
        raise RecoveryError("REFUSED: plan reuse/upload positions disagree with its own reconciliation.")
    if plan.get("guard_contract") != guard_contract():
        raise RecoveryError("REFUSED: plan does not carry the exact fixed guard contract.")
    if plan.get("guard_live_capability") != GUARD_EXPECTED_CAPABILITY:
        raise RecoveryError(
            "REFUSED: plan does not carry the exact Guard 1.0.7 live capability projection."
        )
    if (plan.get("risk") != risk_disclosure(reconciliation)
            or plan.get("writes_if_committed") != writes_if_committed(reconciliation)
            or plan.get("forbidden") != list(FORBIDDEN)):
        raise RecoveryError("REFUSED: plan write surface or risk disclosure is invalid.")
    if plan.get("gallery_payload_template") != [
            {"position": row["position"], "filename": row["filename"],
             "sha256": row["sha256"], "disposition": row["disposition"],
             "attachment_id": row["attachment_id"]} for row in reconciliation]:
        raise RecoveryError("REFUSED: plan gallery template disagrees with its own reconciliation.")
    require_complete_library_evidence(plan.get("library_check"))
    require_recovery_guard_snapshot(plan.get("guard_stage_snapshot"), "atomic_snapshot",
                                    reconciliation, plan.get("library_check"))
    product = plan.get("product_before")
    with closed_refusal():
        family_base.assert_product_eligibility(FAMILY_KEY, product)
    assert_recovery_eligibility(
        prior_evidence=prior_evidence, local=expected_local, capability=guard_capability(),
        library=plan.get("library_check"), product=product,
        reconciliation=reconciliation, origin_proof=plan.get("origin_proof"),
    )
    wanted = operation_sha256(expected_local, product, reconciliation,
                              plan["origin_proof"], prior_evidence)
    if (not isinstance(plan.get("operation_sha256"), str)
            or not secrets.compare_digest(plan["operation_sha256"], wanted)):
        raise RecoveryError("REFUSED: stable operation identity is invalid.")
    plan["sha256"] = saved
    _verify_stage_registry(path, plan, raw, created, authenticate=authenticate_registry)
    return plan


def record_event(operation_id: str, plan_sha256: str, event: str,
                 detail: dict[str, Any]) -> Path:
    path = journal_path(operation_id, event)
    write_json(path, {
        "operation_sha256": operation_id,
        "plan_sha256": plan_sha256,
        "event": event,
        "recorded_utc": utc_now().isoformat(),
        **detail,
    }, exclusive=True)
    return path


def require_unattempted(operation_id: str) -> None:
    if (lock_path(operation_id).exists() or reservation_path(operation_id).exists()
            or result_path(operation_id).exists()
            or (JOURNAL_DIR / _validated_plan_sha(operation_id)).exists()):
        raise RecoveryError(
            "REFUSED: this stable recovery operation already entered commit; permanent no-retry."
        )


# --------------------------------------------------------------------------
# Read-only browser session and the guarded side-effect adapter
# --------------------------------------------------------------------------
class RecoveryAdmin(family_base.ProductFamilyAdmin):
    """The 1.0.5 reader, retargeted at Guard 1.0.7's version and its two new routes.

    The family tool deliberately still pins 1.0.5 because that is what is
    installed, so its version-sensitive methods are overridden here rather than
    parameterised there. Only reads, the existing guard actions, and the ONE
    fixed owner-bound gallery form are reachable; there is no generic navigation,
    click or form-fill route.
    """

    def _guard_status(self, expected: str) -> dict[str, Any]:
        self._goto(family_base.GUARD_ADMIN_URL)
        version_nodes = self._page.query_selector_all(family_base.GUARD_VERSION_SELECTOR)
        status_nodes = self._page.query_selector_all(family_base.GUARD_STATUS_SELECTOR)
        sections = self._page.query_selector_all("section[data-frpd-family]")
        families = [str(section.get_attribute("data-frpd-family") or "") for section in sections]
        if (len(version_nodes) != 1
                or str(version_nodes[0].inner_text() or "").strip()
                    != f"Version {GUARD_PLUGIN_VERSION}"
                or len(status_nodes) != 1
                or str(status_nodes[0].inner_text() or "").strip() != expected
                or len(families) != len(family_base.FAMILY_KEYS)
                or set(families) != set(family_base.FAMILY_KEYS)):
            raise RecoveryError(
                "REFUSED: live Media Mutation Guard identity or state is not exactly "
                f"{GUARD_PLUGIN_VERSION} / {expected!r}. Guard 1.0.5 is still installed, or "
                "the live build is not the pinned one. Nothing was written."
            )
        return {"plugin_version": GUARD_PLUGIN_VERSION, "status": expected,
                "families": sorted(families)}

    def read_live_capability(self) -> dict[str, Any]:
        """The non-secret capability projection, read from the live guard page."""
        nodes = self._page.query_selector_all(GUARD_CAPABILITY_SELECTOR)
        if len(nodes) != 1:
            raise RecoveryError(
                "REFUSED: the live media guard publishes no capability projection, so it is "
                "not Guard 1.0.7. Nothing was written."
            )
        try:
            value = json.loads(str(nodes[0].text_content() or ""))
        except json.JSONDecodeError as exc:
            raise RecoveryError("REFUSED: the live guard capability JSON is invalid.") from exc
        return validate_live_capability(value)

    def guard_health(self, expected_status: str = "Guard inactive") -> dict[str, Any]:
        health = self._guard_status(expected_status)
        return {**health, "capability": self.read_live_capability()}

    def _fixed_global_button(self, selector: str, action: str,
                             expected_status: str) -> Any:
        self._guard_status(expected_status)
        buttons = self._page.query_selector_all(selector)
        if len(buttons) != 1:
            raise RecoveryError("REFUSED: exact fixed guard action is missing or duplicated.")
        form = buttons[0].evaluate_handle("button => button.form").as_element()
        if form is None:
            raise RecoveryError("REFUSED: fixed guard action has no exact form.")
        with closed_refusal():
            return self._validate_guard_form(form, action=action, family=None), form

    # -- reads -----------------------------------------------------------
    def origin_proof(self, expected_status: str = "Guard inactive") -> dict[str, Any]:
        button, _form = self._fixed_global_button(
            GUARD_ORIGIN_PROOF_SELECTOR, GUARD_ORIGIN_PROOF_ACTION, expected_status)
        with closed_refusal():
            proof = self._submit_guard_button(button)
        return validate_origin_proof(proof)

    def atomic_snapshot(self, key: str) -> dict[str, Any]:
        button = self._family_guard_button(key, "snapshot", "Guard inactive")
        with closed_refusal():
            proof = self._submit_guard_button(button)
        return validate_guard_snapshot_proof(proof, "atomic_snapshot", False)

    def acquire_prepared_guard(self, key: str, button: Any,
                               on_submit_attempt: Any | None = None) -> dict[str, Any]:
        with closed_refusal():
            proof = self._submit_guard_button(button, on_submit_attempt)
        return validate_guard_snapshot_proof(proof, "guard_acquired", True)

    def guarded_snapshot(self, key: str) -> dict[str, Any]:
        button, _form = self._fixed_global_button(
            "#frpd-mg-guarded-snapshot", "frpd_media_guard_guarded_snapshot", "Guard active")
        with closed_refusal():
            proof = self._submit_guard_button(button)
        return validate_guard_snapshot_proof(proof, "guarded_snapshot", True)

    def complete_guard(self, key: str, attachment_ids: list[int],
                       on_submit_attempt: Any | None = None) -> dict[str, Any]:
        button, _form = self._fixed_global_button(
            "#frpd-mg-complete", "frpd_media_guard_complete", "Guard active")
        with closed_refusal():
            submitted = self._submit_guard_button(button, on_submit_attempt)
        proof = validate_guard_completion_proof(submitted, attachment_ids)
        return {"proof": proof, "post_completion_health": self.guard_health("Guard inactive")}

    # -- the ONE gallery write -------------------------------------------
    def commit_recovery_gallery(self, if_match: str,
                                on_submit_attempt: Any | None = None) -> dict[str, Any]:
        """Submit the one fixed owner-bound gallery form in the guard-owning browser.

        The ONLY value that leaves this process is the plan-pinned non-secret
        If-Match gallery hash. No product ID, family, attachment ID, filename,
        path or URL is sent: the server derives all of that from its own durable
        recovery record. There is no `wc.api_request` call, no consumer key and
        no REST route anywhere in this method.
        """
        if (not isinstance(if_match, str) or len(if_match) != 66
                or if_match[0] != '"' or if_match[-1] != '"'):
            raise RecoveryError("REFUSED: the gallery precondition is not a quoted SHA-256.")
        _hex64(if_match[1:-1], "gallery precondition")
        # *** THIS FORM IS THE ONLY ONE WITH NO `_wp_http_referer`. ***
        # The family tool's shared validator requires that field because every OTHER
        # guard form renders it. Guard 1.0.7 deliberately suppresses it here and its
        # handler REFUSES a body carrying it, so the shared validator would have
        # rejected the one correct form. This route gets its own exact validator.
        self._guard_status("Guard active")
        forms = self._page.query_selector_all(GUARD_RECOVERY_GALLERY_FORM_SELECTOR)
        buttons = self._page.query_selector_all(GUARD_RECOVERY_GALLERY_SELECTOR)
        if len(forms) != 1 or len(buttons) != 1:
            raise RecoveryError(
                "REFUSED: the fixed recovery gallery form or button is missing or duplicated."
            )
        form = forms[0]
        button = buttons[0]
        if (str(form.get_attribute("method") or "").casefold() != "post"
                or str(form.get_attribute("action") or "") != family_base.GUARD_POST_URL):
            raise RecoveryError(
                "REFUSED: the recovery gallery form method or destination is not exact."
            )
        fields = form.query_selector_all("input[name],select[name],textarea[name]")
        names = sorted(str(node.get_attribute("name") or "") for node in fields)
        action_nodes = form.query_selector_all("input[type='hidden'][name='action']")
        if (names != ["_wpnonce", "action", "if_match"] or len(action_nodes) != 1
                or str(action_nodes[0].get_attribute("value") or "")
                    != GUARD_RECOVERY_GALLERY_ACTION):
            raise RecoveryError(
                "REFUSED: the fixed recovery gallery form is missing, duplicated, or exposes "
                "a field beyond the action, its nonce and the If-Match hash."
            )
        inputs = self._page.query_selector_all(GUARD_RECOVERY_IF_MATCH_SELECTOR)
        if len(inputs) != 1:
            raise RecoveryError("REFUSED: the fixed If-Match field is missing or duplicated.")
        inputs[0].fill(if_match, timeout=media_base.ACTION_TIMEOUT_MS)
        with closed_refusal():
            return self._submit_guard_button(button, on_submit_attempt)


@contextlib.contextmanager
def admin_session(allowed_paths: frozenset[Path]) -> Iterator[Any]:
    """Attach to the ONE already-authenticated WordPress browser. Never launches one."""
    with family_base.admin_session(allowed_paths) as admin:
        yield RecoveryAdmin(admin._page, allowed_paths)


class RecoveryAdapter:
    """Every real side effect goes through here, behind the one shared predicate.

    v1 re-ran the predicate against the SAME frozen preflight objects each time,
    so nothing it checked could ever have changed between calls, and
    `complete_guard` skipped it entirely. Here every method takes a freshly read
    live state and `complete_guard` is no exception: it is a real state
    transition, so it runs the predicate immediately before submitting.
    """

    def __init__(self, admin: Any, plan: dict[str, Any], prior_evidence: dict[str, Any],
                 local: list[dict[str, Any]], capability: dict[str, Any]):
        self._admin = admin
        self._plan = plan
        self._prior = prior_evidence
        self._local = local
        self._capability = capability
        self.predicate_calls = 0

    def require_eligible(self, live: dict[str, Any], *,
                         guard_owner: dict[str, Any] | None = None,
                         completed_upload_positions: list[int] | None = None,
                         next_upload_position: int | None = None,
                         final_attachment_ids: list[int] | None = None) -> None:
        self.predicate_calls += 1
        assert_recovery_eligibility(
            prior_evidence=self._prior, local=self._local, capability=self._capability,
            library=live["library"], product=live["product"],
            reconciliation=live["reconciliation"], origin_proof=live["origin_proof"],
            expected_product=self._plan["product_before"],
            expected_reconciliation=self._plan["reconciliation"],
            guard_owner=guard_owner,
            completed_upload_positions=completed_upload_positions,
            next_upload_position=next_upload_position,
            final_attachment_ids=final_attachment_ids,
        )

    def acquire_guard(self, prepared: Any, live: dict[str, Any], on_attempt: Any) -> dict[str, Any]:
        self.require_eligible(live)
        proof = self._admin.acquire_prepared_guard(FAMILY_KEY, prepared, on_attempt)
        checked = require_recovery_guard_snapshot(
            proof, "guard_acquired", live["reconciliation"], live["library"])
        require_guard_completion_margin(checked)
        return checked

    def owner_snapshot(self, live: dict[str, Any]) -> dict[str, Any]:
        checked = require_recovery_guard_snapshot(
            self._admin.guarded_snapshot(FAMILY_KEY), "guarded_snapshot",
            live["reconciliation"], live["library"])
        require_guard_completion_margin(checked)
        return checked

    def upload_missing(self, expected: dict[str, Any], known_ids: set[int],
                       live: dict[str, Any], guard_owner: dict[str, Any],
                       completed_upload_positions: list[int], on_attempt: Any) -> dict[str, Any]:
        self.require_eligible(
            live, guard_owner=guard_owner,
            completed_upload_positions=completed_upload_positions,
            next_upload_position=expected["position"])
        attachment_id = self._admin.upload_one(expected, known_ids, on_attempt)
        with closed_refusal():
            detail = self._admin.read_attachment(attachment_id,
                                                 expected_basename=expected["filename"])
            source_url = str(detail.get("source_url") or "")
            data = media_base.download_public_bytes(source_url,
                                                    expected_basename=expected["filename"])
        if len(data) != expected["bytes"] or not secrets.compare_digest(
                hashlib.sha256(data).hexdigest(), expected["sha256"]):
            raise RecoveryError("Uploaded public file does not match the approved bytes/hash.")
        return {"position": expected["position"], "filename": expected["filename"],
                "sha256": expected["sha256"], "bytes": expected["bytes"],
                "attachment_id": int(attachment_id), "source_url": source_url,
                "verified_utc": utc_now().isoformat()}

    def commit_gallery(self, live: dict[str, Any], guard_owner: dict[str, Any],
                       completed_upload_positions: list[int],
                       final_attachment_ids: list[int], on_attempt: Any) -> dict[str, Any]:
        self.require_eligible(
            live, guard_owner=guard_owner,
            completed_upload_positions=completed_upload_positions,
            final_attachment_ids=final_attachment_ids)
        if_match = gallery_precondition(live["product"])
        submitted = self._admin.commit_recovery_gallery(if_match, on_attempt)
        return validate_recovery_gallery_proof(submitted, final_attachment_ids, if_match)

    def complete_guard(self, live: dict[str, Any], guard_owner: dict[str, Any],
                       completed_upload_positions: list[int],
                       final_attachment_ids: list[int], on_attempt: Any) -> dict[str, Any]:
        # Completion IS a real guard state transition, so it runs the same
        # predicate against freshly read state immediately before submitting.
        self.require_eligible(
            live, guard_owner=guard_owner,
            completed_upload_positions=completed_upload_positions,
            final_attachment_ids=final_attachment_ids)
        return self._admin.complete_guard(FAMILY_KEY, final_attachment_ids, on_attempt)


# --------------------------------------------------------------------------
# stage
# --------------------------------------------------------------------------
def read_live_state(admin: Any, vault: dict[str, Any], *,
                    guard_status: str = "Guard inactive") -> dict[str, Any]:
    """One complete read-only pass: guard health, origin proof, library, product."""
    health = admin.guard_health(guard_status)
    origin_proof = admin.origin_proof(guard_status)
    with closed_refusal():
        walked = admin.enumerate_library()
        library = family_base.duplicate_scan(
            admin, {FAMILY_KEY: family_base.family_spec(FAMILY_KEY)}, walked=walked)
    resolved = resolve_positions(library)
    verified = {position: verify_resolved_attachment(admin, position, attachment_id)
                for position, attachment_id in sorted(resolved.items())}
    reconciliation = build_reconciliation(resolved, verified)
    require_origin_proof_matches(origin_proof, reconciliation)
    product = product_evidence(vault)
    return {"walked": walked, "library": library, "reconciliation": reconciliation,
            "guard_health": health, "guard_live_capability": health["capability"],
            "origin_proof": origin_proof, "product": product,
            "known_ids": {int(row["id"]) for row in walked["rows"]}}


def read_guarded_live_state(admin: Any, vault: dict[str, Any]) -> dict[str, Any]:
    """The same complete read-only pass, taken while THIS guard is active."""
    return read_live_state(admin, vault, guard_status="Guard active")


def command_stage(_: argparse.Namespace) -> None:
    prior_evidence = validate_prior_evidence()
    guard_release = require_prior_guard_released()
    local = validate_local_images()
    capability = guard_capability()
    with ui_browser_lock("wordpress",
                         purpose="WordPress: read-only FRP MANWAY gallery recovery stage"):
        vault = wc.load_vault()
        with admin_session(FIXED_PATHS) as admin:
            state = read_live_state(admin, vault)
            guard_stage_snapshot = require_recovery_guard_snapshot(
                admin.atomic_snapshot(FAMILY_KEY), "atomic_snapshot",
                state["reconciliation"], state["library"])
    reconciliation = state["reconciliation"]
    if is_already_complete(state["product"], reconciliation):
        emit({"status": "VERIFIED_ALREADY_COMPLETE", "website_writes": 0, "uploads": 0,
              "product_changes": 0, "plan": None, "product_id": PRODUCT_ID,
              "product_name": PRODUCT_LABEL,
              "gallery": current_gallery_ids(state["product"]),
              "reconciliation": reconciliation,
              "message": ("Product 1397 already carries the exact six approved assets in "
                          "order. No plan was created and nothing was written.")})
        return
    assert_recovery_eligibility(
        prior_evidence=prior_evidence, local=local, capability=capability,
        library=state["library"], product=state["product"],
        reconciliation=reconciliation, origin_proof=state["origin_proof"],
    )
    path, plan = stage_one(local, state["product"], reconciliation, state["origin_proof"],
                           prior_evidence, state["library"], guard_stage_snapshot,
                           state["guard_live_capability"], guard_release)
    emit({"status": "STAGED_NOT_COMMITTED", "website_writes": 0, "uploads": 0,
          "product_changes": 0, "plan": str(path), "plan_sha256": plan["sha256"],
          "operation_sha256": plan["operation_sha256"], "expires_utc": plan["expires_utc"],
          "product_id": PRODUCT_ID, "product_name": PRODUCT_LABEL,
          "superseded": plan["superseded"],
          "before_gallery": plan["product_before"]["before_gallery"],
          "reuse_positions": plan["reuse_positions"],
          "upload_positions": plan["upload_positions"],
          "reconciliation": reconciliation, "origin_proof": plan["origin_proof"],
          "risk": plan["risk"], "approval": APPROVAL_WORD})


# --------------------------------------------------------------------------
# commit
# --------------------------------------------------------------------------
def _record_indeterminate(plan_path: Path, plan: dict[str, Any], stage: str,
                          uploaded: list[dict[str, Any]],
                          gallery_payload: list[dict[str, Any]] | None,
                          product_may_have_changed: bool, media_may_have_changed: bool,
                          guard_may_be_active: bool,
                          guard_acquisition: dict[str, Any] | None,
                          guard_owner_snapshot: dict[str, Any] | None,
                          guarded_snapshot: dict[str, Any] | None,
                          gallery_commit: dict[str, Any] | None,
                          guard_completion: dict[str, Any] | None,
                          current_upload: dict[str, Any] | None,
                          current_upload_may_have_landed: bool,
                          observed_attachment_id: int | None,
                          exc: Exception) -> dict[str, Any]:
    detail = {
        "status": "INDETERMINATE_NO_RETRY",
        "plan_sha256": plan["sha256"], "operation_sha256": plan["operation_sha256"],
        "superseded": superseded_record(),
        "updated_utc": utc_now().isoformat(), "stage": stage,
        "reason": type(exc).__name__, "message": str(exc),
        "reused_verified": [row for row in plan["reconciliation"]
                            if row["disposition"] == "reuse_existing"],
        "uploaded_verified": copy.deepcopy(uploaded),
        "current_upload": copy.deepcopy(current_upload),
        "current_upload_may_have_landed": current_upload_may_have_landed,
        "observed_attachment_id": observed_attachment_id,
        "media_may_have_changed": media_may_have_changed,
        "guard_may_be_active": guard_may_be_active,
        "guard_acquisition": copy.deepcopy(guard_acquisition),
        "guard_owner_snapshot": copy.deepcopy(guard_owner_snapshot),
        "guarded_snapshot": copy.deepcopy(guarded_snapshot),
        "gallery_commit": copy.deepcopy(gallery_commit),
        "guard_completion": copy.deepcopy(guard_completion),
        "guard_auto_expiry_seconds": GUARD_TTL_SECONDS,
        "gallery_payload": copy.deepcopy(gallery_payload),
        "product_may_have_changed": product_may_have_changed,
        "no_retry": True, "rollback_performed": False, "delete_performed": False,
        "prior_evidence_mutated": False, "emails": 0,
    }
    failures: list[str] = []
    try:
        record_event(plan["operation_sha256"], plan["sha256"], "990_indeterminate", detail)
    except Exception as evidence_exc:  # noqa: BLE001 - attempt record still proves no-retry
        failures.append("journal:" + type(evidence_exc).__name__)
    try:
        append_receipt(
            "open_manway_gallery_recovery_indeterminate_no_retry",
            f"plan={plan_path}; sha256={plan['sha256']}; "
            f"operation={plan['operation_sha256']}; stage={stage}")
    except Exception as evidence_exc:  # noqa: BLE001
        failures.append("receipt:" + type(evidence_exc).__name__)
    detail["evidence_write_failures"] = list(failures)
    try:
        write_json(result_path(plan["operation_sha256"]), detail, exclusive=True)
    except Exception as evidence_exc:  # noqa: BLE001
        failures.append("result:" + type(evidence_exc).__name__)
        detail["evidence_write_failures"] = list(failures)
    return detail


class AttemptLockNotPublished(RecoveryError):
    """The durable exclusive attempt lock could not be written.

    v1 set `attempt_started = True` BEFORE this write, so a failure here -- with
    no durable attempt marker anywhere and not one website byte touched -- was
    recorded as permanently INDETERMINATE. It is a FREE, FAIL-CLOSED refusal.
    """


def publish_attempt_lock(operation_id: str, plan_sha256: str, plan_path: Path) -> Path:
    """Write the durable exclusive attempt lock. Nothing may have happened yet."""
    path = lock_path(operation_id)
    try:
        write_json(path, {
            "status": "ATTEMPT_STARTED", "plan_sha256": plan_sha256,
            "operation_sha256": operation_id,
            "superseded_operation_sha256": PRIOR_OPERATION_SHA256,
            "plan_path": str(plan_path), "started_utc": utc_now().isoformat(),
            "result_path_reserved": str(result_path(operation_id)), "no_retry": True,
        }, exclusive=True)
    except Exception as exc:  # noqa: BLE001 - one boundary, one classification
        raise AttemptLockNotPublished(
            "FAILED_CLOSED: the durable attempt lock could not be published, so NO attempt "
            "was made and NOTHING was written to the website. Nothing is locked; the plan "
            f"may be committed again once the cause is fixed. Cause: {type(exc).__name__}."
        ) from exc
    if not path.is_file():
        raise AttemptLockNotPublished(
            "FAILED_CLOSED: the durable attempt lock is not on disk after writing, so NO "
            "attempt was made and NOTHING was written to the website."
        )
    return path


def command_commit(args: argparse.Namespace) -> None:
    plan_path = fixed_plan_path(args.plan)
    plan = load_plan(plan_path, authenticate_registry=False)
    # 2. Exact unpadded uppercase APPROVED, before any vault, browser or network.
    require_approval(args.approval)
    plan = load_plan(plan_path, authenticate_registry=True)
    plan_sha256 = plan["sha256"]
    operation_id = plan["operation_sha256"]
    if operation_id in SUPERSEDED_OPERATION_SHA256:
        raise RecoveryError(
            "REFUSED: this stable operation belongs to a superseded build and can never commit."
        )
    require_unattempted(operation_id)
    prior_evidence = validate_prior_evidence()
    require_prior_guard_released()
    local = validate_local_images()
    capability = guard_capability()
    plan_reconciliation = validate_reconciliation(plan["reconciliation"])
    require_guard_supports_recovery(plan_reconciliation, capability)

    vault = wc.load_vault()
    # This module only ever GETs through this vault, so a narrower read-only
    # credential is preferred and accepted the moment one exists. Today the tree
    # publishes exactly one commissioned vault, and it declares read_write; the
    # guarantee that matters is the static one above -- no write primitive is
    # reachable from this module at all.
    if vault.get("declared_permissions") not in ("read_only", "read_write"):
        raise RecoveryError(
            "REFUSED: the WooCommerce vault does not declare a permission this tool "
            "recognises for its read-only product verification."
        )
    if wc.normalize_site_url(str(vault.get("site_url") or "")) != EXACT_ORIGIN:
        raise RecoveryError("REFUSED: WooCommerce vault is not the exact FRP Depot origin.")

    uploaded: list[dict[str, Any]] = []
    completed_positions: list[int] = []
    gallery_payload: list[dict[str, Any]] | None = None
    public_verification: dict[str, Any] | None = None
    guard_acquisition: dict[str, Any] | None = None
    guard_owner_snapshot: dict[str, Any] | None = None
    guarded_snapshot: dict[str, Any] | None = None
    gallery_commit: dict[str, Any] | None = None
    guard_completion: dict[str, Any] | None = None
    stage = "pre_attempt"
    attempt_started = False
    product_may_have_changed = False
    media_may_have_changed = False
    guard_may_be_active = False
    current_upload: dict[str, Any] | None = None
    current_upload_may_have_landed = False
    observed_attachment_id: int | None = None

    try:
        # 3. The shared WordPress browser mutex, BEFORE any attempt lock: a busy
        #    browser must be a free refusal, never a permanently locked plan.
        with ui_browser_lock(
                "wordpress",
                purpose="WordPress: approved FRP MANWAY gallery recovery commit"):
            with admin_session(FIXED_PATHS) as admin:
                # 4. Fresh complete live state and the one shared predicate.
                live = read_live_state(admin, vault)
                if is_already_complete(live["product"], live["reconciliation"]):
                    raise RecoveryError(
                        "REFUSED: product 1397 already carries the exact six recovered assets "
                        "in order; nothing was written."
                    )
                adapter = RecoveryAdapter(admin, plan, prior_evidence, local, capability)
                adapter.require_eligible(live)
                missing = upload_positions(live["reconciliation"])
                stage_snapshot = require_recovery_guard_snapshot(
                    admin.atomic_snapshot(FAMILY_KEY), "atomic_snapshot",
                    live["reconciliation"], live["library"])
                if stage_snapshot["snapshot_sha256"] != plan["guard_stage_snapshot"][
                        "snapshot_sha256"]:
                    raise RecoveryError(
                        "REFUSED: the live guard snapshot changed after staging; stage a fresh plan."
                    )
                prepared_guard = admin.prepare_guard_acquire(FAMILY_KEY)
                known_ids = set(live["known_ids"])

                # 5/6. Every deterministic issue is behind us. Take the permanent
                #      lock, and only mark the attempt started once it is durable.
                require_unattempted(operation_id)
                require_authorization_margin(plan)
                stage = "attempt_marker"
                publish_attempt_lock(operation_id, plan_sha256, plan_path)
                attempt_started = True

                # 7. Acquire the fixed server-side guard exactly once.
                stage = "guard_acquisition"

                def mark_guard_acquire_attempt() -> None:
                    nonlocal guard_may_be_active
                    guard_may_be_active = True

                guard_acquisition = adapter.acquire_guard(
                    prepared_guard, live, mark_guard_acquire_attempt)
                record_event(operation_id, plan_sha256, "050_guard_acquired", {
                    "stage": stage, "guard_acquisition": copy.deepcopy(guard_acquisition),
                    "guard_may_be_active": True})

                stage = "guard_owner_snapshot"
                guard_owner_snapshot = adapter.owner_snapshot(live)
                if (guard_owner_snapshot["snapshot_sha256"] != guard_acquisition["snapshot_sha256"]
                        or guard_owner_snapshot["guard_expires_utc"]
                            != guard_acquisition["guard_expires_utc"]):
                    raise RecoveryError(
                        "Post-acquire guarded owner proof disagrees with the acquisition baseline."
                    )
                record_event(operation_id, plan_sha256, "060_guard_owner_verified", {
                    "stage": stage,
                    "guard_owner_snapshot": copy.deepcopy(guard_owner_snapshot),
                    "guard_may_be_active": True})

                # 8/9. Reuse every plan-proven attachment untouched; upload only the
                #      missing subset, once each, in ascending missing order. Every
                #      iteration re-reads live state first: an upload changes the
                #      Media Library and the origin filesystem, so a frozen preflight
                #      object is worthless from here on.
                landed: dict[int, int] = {}
                for position in missing:
                    expected = FIXED_IMAGES[position - 1]
                    stage = f"upload_{position}"
                    live = read_guarded_live_state(admin, vault)
                    guard_owner_snapshot = adapter.owner_snapshot(live)
                    current_upload = {"position": position, "filename": expected["filename"],
                                      "sha256": expected["sha256"], "bytes": expected["bytes"]}
                    current_upload_may_have_landed = False
                    observed_attachment_id = None

                    def mark_submit_attempt() -> None:
                        nonlocal current_upload_may_have_landed, media_may_have_changed
                        current_upload_may_have_landed = True
                        media_may_have_changed = True

                    record = adapter.upload_missing(
                        expected, known_ids, live, guard_owner_snapshot,
                        list(completed_positions), mark_submit_attempt)
                    observed_attachment_id = record["attachment_id"]
                    known_ids.add(record["attachment_id"])
                    landed[position] = record["attachment_id"]
                    uploaded.append(record)
                    completed_positions.append(position)
                    record_event(operation_id, plan_sha256,
                                 f"1{position}0_upload_verified", {
                                     "stage": stage,
                                     "uploaded_verified": copy.deepcopy(uploaded),
                                     "media_may_have_changed": True})
                    current_upload = None
                    current_upload_may_have_landed = False
                    observed_attachment_id = None

                final_ids = final_attachment_ids_for(live["reconciliation"], landed)
                stage = "guarded_snapshot"
                guarded_snapshot = require_guarded_recovery_snapshot(
                    admin.guarded_snapshot(FAMILY_KEY),
                    int(guard_acquisition["attachment_total"]) + len(missing),
                    final_ids, len(missing))
                if (guarded_snapshot["guard_expires_utc"]
                        != guard_acquisition["guard_expires_utc"]):
                    raise RecoveryError(
                        "The final guarded snapshot belongs to a different guard expiry.")
                record_event(operation_id, plan_sha256, "450_guarded_snapshot_verified", {
                    "stage": stage, "guarded_snapshot": copy.deepcopy(guarded_snapshot),
                    "uploaded_verified": copy.deepcopy(uploaded)})

                stage = "pre_gallery_revalidation"
                for row in live["reconciliation"]:
                    if row["disposition"] == "reuse_existing":
                        verify_resolved_attachment(admin, row["position"], row["attachment_id"])
                for row in uploaded:
                    detail = admin.read_attachment(row["attachment_id"],
                                                   expected_basename=row["filename"])
                    if str(detail.get("source_url") or "") != row["source_url"]:
                        raise RecoveryError(
                            "Pre-commit attachment URL changed after upload verification.")
                    data = media_base.download_public_bytes(
                        row["source_url"], expected_basename=row["filename"])
                    if len(data) != row["bytes"] or not secrets.compare_digest(
                            hashlib.sha256(data).hexdigest(), row["sha256"]):
                        raise RecoveryError(
                            "Pre-commit media revalidation did not match the approved bytes/hash.")
                live = read_guarded_live_state(admin, vault)
                guard_owner_snapshot = adapter.owner_snapshot(live)
                record_event(operation_id, plan_sha256, "500_pre_commit_verified", {
                    "stage": stage, "uploaded_verified": copy.deepcopy(uploaded),
                    "final_attachment_ids": list(final_ids),
                    "product_fingerprint": plan["product_before"]["protected_fingerprint"]})

                # 10. ONE owner-bound gallery commit, submitted in the guard-owning
                #     browser. No WooCommerce Basic-credential request exists here.
                gallery_payload = [{"id": value} for value in final_ids]
                stage = "guard_completion_margin"
                require_guard_completion_margin(guarded_snapshot)
                stage = "gallery_commit_or_readback"

                def mark_commit_attempt() -> None:
                    nonlocal product_may_have_changed
                    product_may_have_changed = True

                gallery_commit = adapter.commit_gallery(
                    live, guard_owner_snapshot, list(completed_positions),
                    final_ids, mark_commit_attempt)
                record_event(operation_id, plan_sha256, "600_gallery_committed", {
                    "stage": stage, "gallery_commit": copy.deepcopy(gallery_commit),
                    "product_may_have_changed": True})

                # 11. Fresh authoritative read-back.
                readback, _ = wc.api_get(f"/products/{PRODUCT_ID}", vault=vault)
                if family_base.gallery_ids(readback) != final_ids:
                    raise RecoveryError("Fresh product read-back did not match gallery IDs/order.")
                if (family_base.protected_product_projection(readback)
                        != plan["product_before"]["protected_projection"]):
                    raise RecoveryError(
                        "A protected product field changed during gallery assignment.")

                # 12. Completion immediately follows the verified read-back, and is
                #     itself a real side effect behind the same predicate.
                stage = "guard_completion"

                def mark_guard_complete_attempt() -> None:
                    nonlocal guard_may_be_active
                    guard_may_be_active = True

                live = read_guarded_live_state(admin, vault)
                guard_owner_snapshot = adapter.owner_snapshot(live)
                guard_completion = adapter.complete_guard(
                    live, guard_owner_snapshot, list(completed_positions), final_ids,
                    mark_guard_complete_attempt)
                if (guard_completion["proof"]["attachment_total"]
                        != guarded_snapshot["attachment_total"]
                        or guard_completion["proof"]["snapshot_sha256"]
                            != guarded_snapshot["snapshot_sha256"]):
                    raise RecoveryError(
                        "Guard completion proof disagrees with the final guarded snapshot.")
                guard_may_be_active = False
                record_event(operation_id, plan_sha256, "850_guard_completed_verified", {
                    "stage": stage, "guard_completion": copy.deepcopy(guard_completion),
                    "guard_may_be_active": False})

                stage = "post_completion_final_verification"
                post_completion, _ = wc.api_get(f"/products/{PRODUCT_ID}", vault=vault)
                if family_base.gallery_ids(post_completion) != final_ids:
                    raise RecoveryError(
                        "Post-completion product read-back did not preserve gallery IDs/order.")
                if (family_base.protected_product_projection(post_completion)
                        != plan["product_before"]["protected_projection"]):
                    raise RecoveryError(
                        "A protected product field changed after guard completion.")
                ordered_urls = [
                    (row["source_url"] if row["disposition"] == "reuse_existing"
                     else next(item["source_url"] for item in uploaded
                               if item["position"] == row["position"]))
                    for row in live["reconciliation"]
                ]
                public_verification = admin.verify_public_product(FAMILY_KEY, ordered_urls)

        final = {
            "status": "COMMITTED_AND_VERIFIED", "plan_sha256": plan_sha256,
            "operation_sha256": operation_id, "superseded": superseded_record(),
            "family": FAMILY_KEY, "product_id": PRODUCT_ID,
            "reused_verified": [row for row in live["reconciliation"]
                                if row["disposition"] == "reuse_existing"],
            "uploaded_verified": uploaded, "gallery": gallery_payload,
            "gallery_commit": gallery_commit,
            "public_verification": public_verification,
            "guard_acquisition": guard_acquisition,
            "guard_owner_snapshot": guard_owner_snapshot,
            "guarded_snapshot": guarded_snapshot, "guard_completion": guard_completion,
            "guard_active_after_verification": False,
            "post_completion_product_verified": True,
            "protected_product_fields_unchanged": True,
            "prior_evidence_mutated": False,
            "updated_utc": utc_now().isoformat(), "replay_locked": True, "no_retry": True,
            "rollback_performed": False, "delete_performed": False, "emails": 0,
        }
        evidence = record_event(operation_id, plan_sha256, "900_committed_verified", final)
        append_receipt("open_manway_gallery_recovery_committed_verified",
                       f"plan={plan_path}; sha256={plan_sha256}; "
                       f"operation={operation_id}; evidence={evidence}")
        write_json(result_path(operation_id), final, exclusive=True)
    except Exception as exc:  # noqa: BLE001 - one boundary owns every post-lock failure
        if not attempt_started:
            # No durable attempt marker and no website side effect: FAILED_CLOSED.
            raise
        detail = _record_indeterminate(
            plan_path, plan, stage, uploaded, gallery_payload, product_may_have_changed,
            media_may_have_changed, guard_may_be_active, guard_acquisition,
            guard_owner_snapshot, guarded_snapshot, gallery_commit, guard_completion,
            current_upload, current_upload_may_have_landed, observed_attachment_id, exc)
        failures = detail.get("evidence_write_failures") or []
        suffix = f" Evidence-write failures: {', '.join(failures)}." if failures else ""
        raise RecoveryIndeterminate(
            f"{stage} failed after the permanent attempt lock. Media may have changed: "
            f"{media_may_have_changed}; product may have changed: {product_may_have_changed}. "
            f"Guard may remain active until its 30-minute expiry: {guard_may_be_active}. "
            f"No retry, delete, cleanup, or rollback.{suffix}"
        ) from exc

    emit(final)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
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
    except (RecoveryError, family_base.FamilyMediaError, media_base.MediaUploadError,
            wc.WooError, UiLaneBusy, UiLaneLockError, OSError, ValueError) as exc:
        print("ERROR: " + wc.scrub(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
