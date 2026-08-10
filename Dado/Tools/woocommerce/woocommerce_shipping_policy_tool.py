#!/usr/bin/env python
"""FRP Depot WooCommerce Approved Shipping-Policy Tool.

Commissioned by Rachad Homsi on 2026-08-09 (freight-policy capability).
Commissioning authorises building and testing this tool. It is NOT approval of
any store change: every write still needs Rachad's own one-word APPROVED against
one exact staged plan.

Exactly three write routes exist, all stage-then-commit:

  shipping_class_create   POST /products/shipping_classes
  shipping_class_assign   PUT  /products/{id} | /products/{pid}/variations/{vid}
  shipping_class_remove   PUT  /products/{id} | /products/{pid}/variations/{vid}

The only field ever written is ``shipping_class``. The only slug ever written is
the fixed freight slug or the empty string (removal). The class name and slug are
hard-coded constants, so no arbitrary shipping class, title or slug can be
created. There is no DELETE, no bulk route, no product/order/customer/payment/
refund/coupon/webhook/user/theme/plugin/setting/stock/price/catalog-copy write,
and no checkout-guard deployment route -- see command_deploy_checkout_guard,
which is permanently disabled because no authenticated, safety-tested site-side
deployment path exists.

A plan enumerates its targets explicitly and is replay-locked before the first
write, so it can be committed at most once; within that single commit each
enumerated target receives its own fresh read, its own one write attempt, and its
own read-back. Nothing resumes after a failure.

*** SCHEMA 2 (2026-08-09) -- PER-FIELD PROTECTED DIAGNOSTICS. ***
Schema 1 stored ONE aggregate fingerprint over every protected field. On
2026-08-09 the first approved assignment plan stopped at its first target with
"/products/1455/variations/1456 changed a protected product field" and the
aggregate hash could not say WHICH field moved, so the cause was unknowable from
the plan alone. Schema 2 stages, and re-checks, one SHA-256 per protected field
plus a per-entry projection of meta_data, so a mismatch can name the exact field
(and the exact metadata entry) without ever recording a raw product value.

This is a DIAGNOSTIC change, not a relaxation. A mismatch is still refused, the
plan is still locked indeterminate, and nothing is retried or rolled back.
Schema-1 plans are permanently unusable: they carry no per-field evidence, so
they are refused before any network access even if their hash is recomputed.

*** SCHEMA 3 (2026-08-09) -- BOUNDED GOOGLE-SYNC CONVERGENCE. ***
The schema-2 diagnostic did its job. An approved one-target assignment on
/products/1455/variations/2056 proved the exact side effect: the intended class
landed, changed_protected_fields was ["meta_data"] and nothing else, and the ONE
moved entry was id 63040, key `_wc_gla_sync_status` -- the Google Listings & Ads
sync flag. Same entry count, same identity, same order, no add and no remove. Its
value went from the staged value to a transient one at the immediate readback, and
two later fresh reads found it back at the staged value and stable.

Schema 3 waits for that ONE proven transition, and for nothing else:

  * The contract is CLOSED and fixed by SOURCE. No input can supply, widen or
    replace it, and a rehashed plan whose contract differs in any field, value or
    order is refused before the vault is opened.
  * A target is eligible only when its staged metadata carries exactly one sound
    `_wc_gla_sync_status` entry already at the staged value. Absent means not
    eligible; duplicated, malformed, or sitting at any other value REFUSES
    staging, because Rachad should never approve a plan built while Google sync
    is unsettled.
  * The wait is read-only and bounded: a fixed schedule totalling 90 seconds, one
    fresh GET per step, no second write of any kind.
  * The transient is NEVER success. The commit succeeds only when the COMPLETE
    protected state -- the aggregate hash, every per-field hash and the whole
    metadata projection -- returns exactly to the staged state. If the schedule
    expires while the entry is still transient, the plan locks indeterminate.
  * Any other movement -- another protected field, another metadata entry, an
    identity, count, order, value, class or shape change -- is an immediate,
    permanent indeterminate mismatch with the schema-2 bounded diagnostic.

Schema-1 and schema-2 plans are both permanently unusable and are refused before
any vault or network access even if their hash is recomputed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from typing import Any

import woocommerce_common as wc

TOOL_NAME = "FRP Depot WooCommerce Approved Shipping-Policy Tool"
SCHEMA_VERSION = 3
TOOL_VERSION = "3.0.0"
EXACT_ORIGIN = "https://frpdepots.com:443"
ROOT = Path(r"C:\FRPDepot")
PLAN_DIR = ROOT / "Dado" / "20_Working" / "woocommerce_shipping_plans"
PLAN_LIFETIME_HOURS = 24

# Rachad's approval is his own one-word reply to one exact staged plan. The plan
# digest is an internal integrity control and is never part of the approval text.
APPROVAL_WORD = "APPROVED"

# The single shipping class this tool may ever create or assign. Hard-coded on
# purpose: a caller cannot introduce another class, name or slug.
FREIGHT_CLASS_NAME = "Freight Quote Required"
FREIGHT_CLASS_SLUG = "freight-quote-required"
NO_CLASS_SLUG = ""

ACTIONS = {"shipping_class_create", "shipping_class_assign", "shipping_class_remove"}
ASSIGNMENT_ACTIONS = {"shipping_class_assign", "shipping_class_remove"}
SLUG_FOR_ACTION = {
    "shipping_class_assign": FREIGHT_CLASS_SLUG,
    "shipping_class_remove": NO_CLASS_SLUG,
}

# A plan enumerates its targets explicitly; it never selects them by query. The
# ceiling is a safety limit on an enumerated list, not a batch route.
MAX_TARGETS = 200

# Everything a shipping-class write must leave untouched. shipping_class and
# shipping_class_id are deliberately absent: they are the only fields we change.
PROTECTED_FIELDS = (
    "attributes", "backorders", "catalog_visibility", "categories", "cross_sell_ids",
    "date_on_sale_from_gmt", "date_on_sale_to_gmt", "description", "dimensions",
    "downloadable", "downloads", "external_url", "featured", "images", "manage_stock",
    "menu_order", "meta_data", "name", "parent_id", "price", "purchasable",
    "regular_price", "reviews_allowed", "sale_price", "short_description", "sku",
    "slug", "status", "stock_quantity", "stock_status", "tags", "tax_class",
    "tax_status", "type", "upsell_ids", "virtual", "weight",
)

# meta_data is the protected field most likely to be moved by a third-party save
# hook, and the one an aggregate hash is least able to explain. It gets a
# per-entry projection on top of its ordinary per-field fingerprint.
META_FIELD = "meta_data"

# Bounds. A plan never stores a metadata VALUE, only hashes of it, so the plan
# cannot be inflated by a large value -- but it can be inflated by a large
# NUMBER of entries, and memory can be inflated by one hostile entry. Both are
# capped, and an over-limit response is REFUSED rather than silently truncated.
MAX_META_ENTRIES = 250
MAX_META_KEY_CHARS = 190          # WordPress indexes meta_key to 191 characters.
MAX_META_ENTRY_CHARS = 262144     # canonical JSON of ONE entry, 256 KiB.
# Diagnostics are computed only from already-bounded projections, so this cap can
# never hide an unbounded list; anything past it is reported as an omitted count.
MAX_META_DIAGNOSTIC_ENTRIES = 25

META_PROJECTION_KEYS = frozenset(
    {"index", "id", "key", "key_sha256", "value_sha256", "entry_sha256", "shape"}
)
META_SHAPES = frozenset({"entry", "malformed"})

HEX64 = re.compile(r"[0-9a-f]{64}")

# --- The CLOSED convergence contract (schema 3) ----------------------------
# Every value below is fixed by SOURCE. A request cannot supply, widen or replace
# any of it, and the whole contract is hashed into the plan, so a rehashed edit is
# still refused semantically before the vault is opened.
#
# The two value digests are the canonical-JSON SHA-256s observed live in the
# 2026-08-09 diagnostic commit on /products/1455/variations/2056 -- staged, then
# transient at the immediate readback, then staged again and stable. The plaintext
# of neither is written anywhere at runtime; only these identifiers are.
CONVERGENCE_KIND = "gla_sync_pending_to_synced"
GLA_META_KEY = "_wc_gla_sync_status"
GLA_STAGED_VALUE_SHA256 = "bed425acaecb0b9f4dd17f2f763e28b52f027d43feb0265137f49c86bb875c8c"
GLA_TRANSIENT_VALUE_SHA256 = "12adac54ac6f7140109391b670b3cbcd51f083d8f5ee62ce26857c794ed67d36"

# A fixed, bounded, front-loaded schedule. Six reads, 90 seconds, no unbounded
# loop and no caller-supplied timing. max_seconds is the exact sum, so the two can
# never drift apart.
CONVERGENCE_SCHEDULE_SECONDS = (2, 4, 8, 16, 30, 30)
CONVERGENCE_MAX_SECONDS = sum(CONVERGENCE_SCHEDULE_SECONDS)
CONVERGENCE_CEILING_SECONDS = 90

# Success is the COMPLETE staged protected state. The transient is a reason to
# look again, never a reason to accept.
CONVERGENCE_FINAL_REQUIREMENT = "exact_staged_protected_state"
CONVERGENCE_TIMEOUT_FINAL_STATE = "pending_not_accepted"
CONVERGENCE_PHASE = "convergence"

# The only protected field the bounded wait may ever find moved.
CONVERGENCE_ALLOWED_CHANGED_FIELDS = (META_FIELD,)

# Per-target eligibility, decided at staging from the value-free projection.
GLA_ELIGIBILITY_FIELD = "gla_convergence_eligible"


class ShippingPolicyError(RuntimeError):
    pass


class ProtectedStateMismatch(ShippingPolicyError):
    """A protected field moved. Carries a bounded, value-free diagnostic.

    The diagnostic exists so a lock and a receipt can say WHICH field moved. It
    never carries a raw product value, page text, header, request body or
    exception dump -- only field names, metadata keys, indexes and SHA-256s.
    """

    def __init__(self, message: str, diagnostic: dict[str, Any]):
        super().__init__(message)
        self.diagnostic = diagnostic


class ConvergenceTimeout(ShippingPolicyError):
    """The bounded Google-sync wait expired with the entry still transient.

    This is a FAILURE. The transient state is never accepted as success, the plan
    is locked indeterminate, and nothing is retried, rolled back or written. The
    record it carries is fixed vocabulary, counts, seconds and hash identifiers.
    """

    def __init__(self, message: str, record: dict[str, Any]):
        super().__init__(message)
        self.record = record


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def monotonic() -> float:
    """Wall-clock-independent elapsed source, wrapped so a test can drive it.

    Injected rather than called inline for one reason: a bounded wait must be
    provable without a test suite actually waiting 90 seconds.
    """
    return time.monotonic()


def sleep(seconds: float) -> None:
    """The only sleep in this tool. It is only ever handed a fixed schedule value."""
    time.sleep(seconds)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_for(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha256_of(value: Any) -> str:
    """SHA-256 of one canonical JSON value. The value itself is never stored."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def clean_digest(value: Any, label: str) -> str:
    """A full 64-character lowercase SHA-256. Short, padded or upper forms fail."""
    text = value if isinstance(value, str) else ""
    if not HEX64.fullmatch(text):
        raise ShippingPolicyError(
            f"{label} must be a full 64-character lowercase SHA-256 digest."
        )
    return text


# Derived once from the fixed key above, by exactly the rule every projection row
# uses, so the two can never disagree about what the key hashes to.
GLA_META_KEY_SHA256 = sha256_of(GLA_META_KEY)


def convergence_contract() -> dict[str, Any]:
    """The one convergence contract this tool will ever honour.

    Built from module constants on every call. It is written into the plan core,
    so it is covered by the plan hash, and it is re-validated field by field at
    commit -- a rehashed plan carrying a different kind, key, digest, schedule
    order, ceiling or requirement is refused before any vault or network access.
    """
    return {
        "kind": CONVERGENCE_KIND,
        "meta_key": GLA_META_KEY,
        "staged_value_sha256": GLA_STAGED_VALUE_SHA256,
        "transient_value_sha256": GLA_TRANSIENT_VALUE_SHA256,
        "schedule_seconds": list(CONVERGENCE_SCHEDULE_SECONDS),
        "max_seconds": CONVERGENCE_MAX_SECONDS,
        "final_requirement": CONVERGENCE_FINAL_REQUIREMENT,
        "allowed_changed_protected_fields_during_wait": list(CONVERGENCE_ALLOWED_CHANGED_FIELDS),
    }


CONVERGENCE_CONTRACT_KEYS = frozenset(convergence_contract())


def _exact_int(value: Any) -> bool:
    """A real JSON integer. A bool is not an int here, and 2.0 is not 2.

    Load-bearing: ``[2.0, 4.0] == [2, 4]`` in Python, so plain equality alone
    would let a rehashed float schedule through.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def validate_convergence_contract(raw: Any) -> dict[str, Any]:
    """Exact semantic validation of a plan's contract. Closed, ordered, typed."""
    fixed = convergence_contract()
    if not isinstance(raw, dict):
        raise ShippingPolicyError("REFUSED: convergence_contract must be one object.")
    if set(raw) != CONVERGENCE_CONTRACT_KEYS:
        raise ShippingPolicyError(
            f"REFUSED: convergence_contract must carry exactly the {len(fixed)} fixed "
            "fields. An extra, missing or renamed field is refused."
        )
    schedule = raw["schedule_seconds"]
    if not isinstance(schedule, list) or len(schedule) != len(CONVERGENCE_SCHEDULE_SECONDS):
        raise ShippingPolicyError(
            "REFUSED: convergence_contract.schedule_seconds is not the fixed schedule."
        )
    if not all(_exact_int(step) and step > 0 for step in schedule):
        raise ShippingPolicyError(
            "REFUSED: convergence_contract.schedule_seconds must be positive integers."
        )
    if not _exact_int(raw["max_seconds"]):
        raise ShippingPolicyError(
            "REFUSED: convergence_contract.max_seconds must be an integer."
        )
    if sum(schedule) != raw["max_seconds"]:
        raise ShippingPolicyError(
            "REFUSED: convergence_contract.max_seconds is not the exact schedule total."
        )
    if raw["max_seconds"] > CONVERGENCE_CEILING_SECONDS:
        raise ShippingPolicyError(
            f"REFUSED: convergence_contract exceeds the fixed "
            f"{CONVERGENCE_CEILING_SECONDS}-second ceiling."
        )
    for field in ("staged_value_sha256", "transient_value_sha256"):
        clean_digest(raw[field], f"convergence_contract.{field}")
    for field, value in fixed.items():
        # Order matters for the schedule, so this is list equality, not a set.
        if raw[field] != value:
            raise ShippingPolicyError(
                f"REFUSED: convergence_contract.{field} is not the fixed value. "
                "The contract is fixed by source and cannot be widened or replaced."
            )
    return dict(raw)


def read_json(path: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShippingPolicyError(f"Input JSON is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ShippingPolicyError("Input JSON must contain one object.")
    return value


def clean_id(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ShippingPolicyError(f"{label} must be a positive integer.")
    text = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise ShippingPolicyError(f"{label} must be a positive integer.")
    return int(text)


def clean_source(value: Any, label: str) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise ShippingPolicyError(f"{label} is required.")
    if len(text) > 500:
        raise ShippingPolicyError(f"{label} exceeds the 500-character safety limit.")
    if any(ord(char) < 32 for char in text):
        raise ShippingPolicyError(f"{label} contains control characters.")
    return text


def clean_targets(raw: Any) -> list[dict[str, Any]]:
    """Accept only an explicit list of existing product/variation identities."""
    if not isinstance(raw, list) or not raw:
        raise ShippingPolicyError("targets must be a non-empty list of explicit resources.")
    if len(raw) > MAX_TARGETS:
        raise ShippingPolicyError(f"targets exceeds the {MAX_TARGETS}-resource safety limit.")
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ShippingPolicyError(f"targets[{index}] must be an object.")
        kind = str(row.get("kind") or "").strip().casefold()
        if kind == "product":
            if set(row) != {"kind", "product_id"}:
                raise ShippingPolicyError(
                    f"targets[{index}] product must contain exactly kind and product_id."
                )
            product_id = clean_id(row["product_id"], f"targets[{index}].product_id")
            cleaned = {"kind": "product", "product_id": product_id, "variation_id": 0}
        elif kind == "variation":
            if set(row) != {"kind", "product_id", "variation_id"}:
                raise ShippingPolicyError(
                    f"targets[{index}] variation must contain exactly kind, product_id "
                    "and variation_id."
                )
            product_id = clean_id(row["product_id"], f"targets[{index}].product_id")
            variation_id = clean_id(row["variation_id"], f"targets[{index}].variation_id")
            if variation_id == product_id:
                raise ShippingPolicyError(
                    f"targets[{index}] variation_id cannot equal product_id."
                )
            cleaned = {"kind": "variation", "product_id": product_id,
                       "variation_id": variation_id}
        else:
            raise ShippingPolicyError(f"targets[{index}].kind must be product or variation.")
        identity = (cleaned["kind"], cleaned["product_id"], cleaned["variation_id"])
        if identity in seen:
            raise ShippingPolicyError(f"targets[{index}] duplicates an earlier target.")
        seen.add(identity)
        output.append(cleaned)
    return output


def target_endpoint(target: dict[str, Any]) -> str:
    if target["kind"] == "product":
        return f"/products/{target['product_id']}"
    return f"/products/{target['product_id']}/variations/{target['variation_id']}"


def endpoint_for(action: str, target: dict[str, Any] | None) -> tuple[str, str]:
    if action == "shipping_class_create":
        return "POST", "/products/shipping_classes"
    if action in ASSIGNMENT_ACTIONS:
        if target is None:
            raise ShippingPolicyError("An assignment route requires one explicit target.")
        return "PUT", target_endpoint(target)
    raise ShippingPolicyError("Unsupported action.")


def validate_write_route(method: str, endpoint: str) -> None:
    allowed = (
        (method == "POST" and endpoint == "/products/shipping_classes")
        or (method == "PUT" and re.fullmatch(r"/products/[1-9][0-9]*", endpoint))
        or (method == "PUT" and re.fullmatch(
            r"/products/[1-9][0-9]*/variations/[1-9][0-9]*", endpoint
        ))
    )
    if not allowed:
        raise ShippingPolicyError("REFUSED: write method/path pair is not allowlisted.")


def validate_payload(action: str, payload: Any) -> None:
    """The payload allowlist is one field with one of two fixed values."""
    if not isinstance(payload, dict):
        raise ShippingPolicyError("Plan payload must be an object.")
    if action == "shipping_class_create":
        if payload != {"name": FREIGHT_CLASS_NAME, "slug": FREIGHT_CLASS_SLUG}:
            raise ShippingPolicyError(
                "REFUSED: the only creatable shipping class is the fixed freight class."
            )
        return
    if set(payload) != {"shipping_class"}:
        raise ShippingPolicyError("REFUSED: only the shipping_class field may be written.")
    if payload["shipping_class"] != SLUG_FOR_ACTION[action]:
        raise ShippingPolicyError("REFUSED: shipping_class value is not allowlisted for this action.")


def protected_state(record: dict[str, Any]) -> dict[str, Any]:
    return {field: record.get(field) for field in PROTECTED_FIELDS}


def protected_fingerprint(record: dict[str, Any]) -> str:
    """Fields that must be identical before and after a shipping-class write.

    date_modified_gmt is excluded on purpose: WooCommerce always moves it, so
    including it would make the after-check impossible to satisfy honestly.
    """
    return hashlib.sha256(canonical(protected_state(record)).encode("utf-8")).hexdigest()


def stale_fingerprint(record: dict[str, Any]) -> str:
    """Protected state plus the modification stamp and current class, for staleness."""
    safe = protected_state(record)
    safe["__id"] = record.get("id")
    safe["__date_modified_gmt"] = record.get("date_modified_gmt")
    safe["__shipping_class"] = record.get("shipping_class")
    return hashlib.sha256(canonical(safe).encode("utf-8")).hexdigest()


def protected_field_fingerprints(record: dict[str, Any]) -> dict[str, str]:
    """Exactly one SHA-256 per PROTECTED_FIELDS entry. Deterministic, value-free.

    The aggregate fingerprint above answers "did anything move"; this answers
    "what moved". Both are checked -- the aggregate is never dropped.
    """
    return {field: sha256_of(record.get(field)) for field in PROTECTED_FIELDS}


def validate_field_fingerprints(raw: Any, label: str) -> dict[str, str]:
    """The mapping is CLOSED: exactly the protected field names, nothing else."""
    if not isinstance(raw, dict):
        raise ShippingPolicyError(f"{label} must be an object of per-field fingerprints.")
    if set(raw) != set(PROTECTED_FIELDS):
        raise ShippingPolicyError(
            f"{label} must name exactly the {len(PROTECTED_FIELDS)} protected fields. "
            "A missing or extra field name is refused."
        )
    return {field: clean_digest(raw[field], f"{label}.{field}") for field in PROTECTED_FIELDS}


def metadata_projection(record: dict[str, Any]) -> list[dict[str, Any]]:
    """A closed, ordered, value-free projection of one record's meta_data list.

    Each entry keeps its ORIGINAL list index, its stable numeric id when it has
    one, its key, and three SHA-256s (key, value, whole entry). Duplicate
    id/key pairs stay separate rows and malformed entries stay representable as
    shape="malformed" -- nothing is collapsed, deduplicated or reordered, because
    a collapsed projection could hide the very change this exists to find.

    Key NAMES are stored in clear on purpose: they are WordPress field names such
    as `_wp_page_template`, not customer or credential data, and Rachad cannot act
    on "some metadata entry changed". Values are never stored, only hashed -- a
    value is where order, address or licence data would live. A key that is not a
    printable string is withheld and identified by key_sha256 alone.
    """
    raw = record.get(META_FIELD)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ShippingPolicyError(
            "REFUSED: meta_data is not a list, so no honest per-entry projection exists."
        )
    if len(raw) > MAX_META_ENTRIES:
        raise ShippingPolicyError(
            f"REFUSED: meta_data carries more than the {MAX_META_ENTRIES}-entry safety "
            "limit. The projection is not truncated; the resource is refused."
        )
    rows: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        text = canonical(entry)
        if len(text) > MAX_META_ENTRY_CHARS:
            raise ShippingPolicyError(
                f"REFUSED: meta_data entry {index} exceeds the "
                f"{MAX_META_ENTRY_CHARS}-character safety limit."
            )
        entry_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if not isinstance(entry, dict):
            rows.append({
                "index": index, "id": None, "key": None,
                "key_sha256": sha256_of(None), "value_sha256": sha256_of(None),
                "entry_sha256": entry_sha, "shape": "malformed",
            })
            continue
        raw_id = entry.get("id")
        identifier = raw_id if (isinstance(raw_id, int) and not isinstance(raw_id, bool)
                                and raw_id >= 0) else None
        id_is_sound = raw_id is None or identifier is not None
        raw_key = entry.get("key")
        key: str | None = None
        if isinstance(raw_key, str):
            if len(raw_key) > MAX_META_KEY_CHARS:
                raise ShippingPolicyError(
                    f"REFUSED: meta_data entry {index} has a key longer than the "
                    f"{MAX_META_KEY_CHARS}-character safety limit."
                )
            if raw_key.isprintable():
                key = raw_key
        rows.append({
            "index": index,
            "id": identifier,
            "key": key,
            "key_sha256": sha256_of(raw_key),
            "value_sha256": sha256_of(entry.get("value")),
            "entry_sha256": entry_sha,
            "shape": "entry" if (key is not None and id_is_sound) else "malformed",
        })
    return rows


def validate_meta_projection(raw: Any, label: str) -> list[dict[str, Any]]:
    """Re-validate a projection carried in a plan. Closed shape, exact order."""
    if not isinstance(raw, list):
        raise ShippingPolicyError(f"{label} must be a list.")
    if len(raw) > MAX_META_ENTRIES:
        raise ShippingPolicyError(
            f"{label} exceeds the {MAX_META_ENTRIES}-entry safety limit."
        )
    for position, row in enumerate(raw):
        where = f"{label}[{position}]"
        if not isinstance(row, dict) or set(row) != META_PROJECTION_KEYS:
            raise ShippingPolicyError(f"{where} has an unexpected shape.")
        if row["index"] != position or isinstance(row["index"], bool):
            # The stored index is NOT echoed back: a tampered plan could hold an
            # arbitrarily long value there, and an error message is bounded output.
            raise ShippingPolicyError(
                f"{where} does not carry its own position. A reordered, inserted or "
                "removed projection entry is refused."
            )
        identifier = row["id"]
        if identifier is not None and (isinstance(identifier, bool)
                                       or not isinstance(identifier, int)
                                       or identifier < 0):
            raise ShippingPolicyError(f"{where}.id must be a non-negative integer or null.")
        key = row["key"]
        if key is not None:
            if not isinstance(key, str) or not key.isprintable():
                raise ShippingPolicyError(f"{where}.key must be printable text or null.")
            if len(key) > MAX_META_KEY_CHARS:
                raise ShippingPolicyError(
                    f"{where}.key exceeds the {MAX_META_KEY_CHARS}-character safety limit."
                )
        if row["shape"] not in META_SHAPES:
            raise ShippingPolicyError(f"{where}.shape is not an allowlisted shape.")
        # One-way on purpose: shape="entry" always carries a key, while a
        # shape="malformed" row may still carry one (a sound key beside an
        # unsound id). Relabelling a sound row as malformed is not caught here --
        # it is caught by the pre-write projection comparison, which rebuilds the
        # projection from the live record and refuses before any PUT.
        if row["shape"] == "entry" and key is None:
            raise ShippingPolicyError(f"{where}.shape disagrees with its key.")
        for field in ("key_sha256", "value_sha256", "entry_sha256"):
            clean_digest(row[field], f"{where}.{field}")
    return list(raw)


def gla_convergence_eligible(projection: list[dict[str, Any]]) -> bool:
    """Is this resource's Google-sync flag settled enough to plan a write against?

    Decided from the value-free projection alone, so it can be recomputed at commit
    from the plan's own evidence rather than trusted as a stored boolean.

    True  -- exactly one sound `_wc_gla_sync_status` entry, already at the
             contract's staged value digest. Only such a target may ever use the
             bounded wait.
    False -- the key is absent. An ordinary resource Google does not track; the
             wait simply never applies to it.
    REFUSED -- the key appears more than once, is malformed, or holds any other
             value (including the transient one). Rachad must never be asked to
             approve a plan built while Google sync is unsettled: the staged
             "before" state would be a moving target, and the detector below
             could not tell a settling sync from a real third-party edit.

    Entries are matched by key DIGEST, not by the printable key, so an entry that
    carries this key beside an unsound id cannot slip past as "not found".
    """
    rows = [row for row in projection if row["key_sha256"] == GLA_META_KEY_SHA256]
    if not rows:
        return False
    if len(rows) > 1:
        raise ShippingPolicyError(
            f"REFUSED: {GLA_META_KEY} appears {len(rows)} times in this resource's "
            "metadata. One settled entry is required; stage nothing against it."
        )
    row = rows[0]
    # A stable numeric id is required as well as a sound shape: the detector below
    # tracks this entry across fresh reads by id, so an entry it cannot identify
    # could never be followed anyway.
    if row["shape"] != "entry" or row["key"] != GLA_META_KEY or \
            not _exact_int(row["id"]) or row["id"] < 0:
        raise ShippingPolicyError(
            f"REFUSED: the {GLA_META_KEY} metadata entry is malformed or carries no "
            "stable numeric id, so its state cannot be established. Stage nothing "
            "against this resource."
        )
    if row["value_sha256"] == GLA_STAGED_VALUE_SHA256:
        return True
    raise ShippingPolicyError(
        f"REFUSED: the {GLA_META_KEY} entry does not hold the contract's staged "
        f"value digest ({GLA_STAGED_VALUE_SHA256[:16]}...), so Google sync is "
        "unsettled on this resource right now. Wait for it to settle and stage "
        "again; do not ask Rachad to approve a moving before-state."
    )


def _meta_identity(row: dict[str, Any]) -> tuple[Any, str]:
    """Identity of one projection entry: stable numeric id (or null) plus key hash."""
    return (row["id"], row["key_sha256"])


def _meta_reference(row: dict[str, Any]) -> dict[str, Any]:
    return {"index": row["index"], "id": row["id"], "key": row["key"],
            "key_sha256": row["key_sha256"], "value_sha256": row["value_sha256"],
            "entry_sha256": row["entry_sha256"], "shape": row["shape"]}


def _bounded(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    return rows[:MAX_META_DIAGNOSTIC_ENTRIES], max(0, len(rows) - MAX_META_DIAGNOSTIC_ENTRIES)


def metadata_diagnostic(staged: list[dict[str, Any]],
                        readback: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify how two projections differ, in hashes and identities only."""
    staged_ids = [_meta_identity(row) for row in staged]
    live_ids = [_meta_identity(row) for row in readback]
    staged_counts = Counter(staged_ids)
    live_counts = Counter(live_ids)
    surplus_staged = staged_counts - live_counts
    surplus_live = live_counts - staged_counts

    def _surplus(rows: list[dict[str, Any]], surplus: Counter) -> list[dict[str, Any]]:
        # Take the surplus from the TAIL so the leading occurrences of a repeated
        # identity stay paired for the value comparison below.
        remaining = Counter(surplus)
        picked = []
        for row in reversed(rows):
            identity = _meta_identity(row)
            if remaining[identity] > 0:
                remaining[identity] -= 1
                picked.append(_meta_reference(row))
        picked.sort(key=lambda item: item["index"])
        return picked

    removed = _surplus(staged, surplus_staged)
    added = _surplus(readback, surplus_live)

    # Entries that survive by identity but whose value or full entry moved. Rows
    # sharing one identity are paired in list order so duplicates stay separate.
    by_identity: dict[tuple[Any, str], list[dict[str, Any]]] = {}
    for row in readback:
        by_identity.setdefault(_meta_identity(row), []).append(row)
    consumed: Counter = Counter()
    value_changed: list[dict[str, Any]] = []
    for row in staged:
        identity = _meta_identity(row)
        candidates = by_identity.get(identity, [])
        position = consumed[identity]
        if position >= len(candidates):
            continue
        consumed[identity] += 1
        other = candidates[position]
        if row["entry_sha256"] == other["entry_sha256"]:
            continue
        value_changed.append({
            "staged_index": row["index"], "readback_index": other["index"],
            "id": row["id"], "key": row["key"], "key_sha256": row["key_sha256"],
            "staged_value_sha256": row["value_sha256"],
            "readback_value_sha256": other["value_sha256"],
            "staged_entry_sha256": row["entry_sha256"],
            "readback_entry_sha256": other["entry_sha256"],
            "value_differs": row["value_sha256"] != other["value_sha256"],
        })

    identity_changed: list[dict[str, Any]] = []
    for position in range(min(len(staged), len(readback))):
        if staged_ids[position] == live_ids[position]:
            continue
        identity_changed.append({
            "index": position,
            "staged_id": staged[position]["id"], "staged_key": staged[position]["key"],
            "staged_key_sha256": staged[position]["key_sha256"],
            "readback_id": readback[position]["id"], "readback_key": readback[position]["key"],
            "readback_key_sha256": readback[position]["key_sha256"],
        })

    added_rows, added_omitted = _bounded(added)
    removed_rows, removed_omitted = _bounded(removed)
    changed_rows, changed_omitted = _bounded(value_changed)
    identity_rows, identity_omitted = _bounded(identity_changed)
    return {
        "staged_entry_count": len(staged),
        "readback_entry_count": len(readback),
        "added_entries": added_rows,
        "added_entry_count": len(added),
        "removed_entries": removed_rows,
        "removed_entry_count": len(removed),
        "value_changed_entries": changed_rows,
        "value_changed_entry_count": len(value_changed),
        "identity_changed_positions": identity_rows,
        "identity_changed_position_count": len(identity_changed),
        "order_changed": staged_ids != live_ids and staged_counts == live_counts,
        "diagnostic_entry_limit": MAX_META_DIAGNOSTIC_ENTRIES,
        "omitted_from_report": {
            "added": added_omitted, "removed": removed_omitted,
            "value_changed": changed_omitted, "identity_changed": identity_omitted,
        },
    }


def protected_diagnostic(endpoint: str, phase: str, target: dict[str, Any],
                         record: dict[str, Any]) -> dict[str, Any] | None:
    """None when the live record still matches the plan; a bounded report if not.

    ``readback_sha256`` is the freshly-observed digest in BOTH phases: at
    phase="pre_write" it is the read taken immediately before the write, at
    phase="post_write" it is the read taken immediately after it.
    """
    staged_fields = target["before_protected_field_fingerprints"]
    live_fields = protected_field_fingerprints(record)
    changed = sorted(field for field in PROTECTED_FIELDS
                     if staged_fields.get(field) != live_fields[field])
    staged_projection = target["before_meta_data_projection"]
    meta_diff: dict[str, Any] | None = None
    try:
        live_projection = metadata_projection(record)
        meta_status = "matched" if live_projection == staged_projection else "changed"
        if meta_status == "changed":
            meta_diff = metadata_diagnostic(staged_projection, live_projection)
    except ShippingPolicyError:
        # An unreadable or over-limit metadata list is itself a refusal, not a pass.
        meta_status = "refused"
    live_aggregate = protected_fingerprint(record)
    if not changed and meta_status == "matched" and \
            live_aggregate == target["before_protected_fingerprint"]:
        return None
    return {
        "phase": phase,
        "endpoint": endpoint,
        "changed_protected_fields": changed,
        "field_fingerprints": {
            field: {"staged_sha256": staged_fields.get(field),
                    "readback_sha256": live_fields[field]}
            for field in changed
        },
        "aggregate_protected_fingerprint": {
            "staged_sha256": target["before_protected_fingerprint"],
            "readback_sha256": live_aggregate,
            "matches": live_aggregate == target["before_protected_fingerprint"],
        },
        "meta_data_projection": {"status": meta_status, "diagnostic": meta_diff},
    }


def matched_diagnostic(endpoint: str, phase: str, target: dict[str, Any],
                       record: dict[str, Any]) -> dict[str, Any]:
    """The same bounded shape protected_diagnostic returns, for a clean record.

    protected_diagnostic answers None when the protected state is exact, but a
    shipping-class drift still has to be reported in the ordinary shape rather
    than as a bare message.
    """
    return {
        "phase": phase,
        "endpoint": endpoint,
        "changed_protected_fields": [],
        "field_fingerprints": {},
        "aggregate_protected_fingerprint": {
            "staged_sha256": target["before_protected_fingerprint"],
            "readback_sha256": protected_fingerprint(record),
            "matches": True,
        },
        "meta_data_projection": {"status": "matched", "diagnostic": None},
    }


def is_gla_sync_transient(target: dict[str, Any], diagnostic: dict[str, Any],
                          shipping_class_matches: bool) -> bool:
    """The ONE waitable mismatch, recognised exactly. Pure: no I/O, no clock.

    Every condition must hold. Anything else -- any other field, entry, identity,
    count, order, value, class or shape -- is an immediate permanent indeterminate
    mismatch, reported through the ordinary schema-2 bounded diagnostic.

    This is not a tolerance. It recognises a state that is still WRONG; the caller
    is only permitted to look again, within a fixed bound, for the complete staged
    state to come back.
    """
    # 1. The approved class is already exactly in place.
    if not shipping_class_matches:
        return False
    # 2. The target was staged with a settled, single, exact Google-sync entry.
    if target.get(GLA_ELIGIBILITY_FIELD) is not True:
        return False
    # 3. meta_data is the only protected field that moved, and it really moved.
    if diagnostic.get("changed_protected_fields") != list(CONVERGENCE_ALLOWED_CHANGED_FIELDS):
        return False
    fields = diagnostic.get("field_fingerprints")
    if not isinstance(fields, dict) or set(fields) != set(CONVERGENCE_ALLOWED_CHANGED_FIELDS):
        return False
    pair = fields[META_FIELD]
    # Every shape is checked before it is read: this predicate must answer False
    # for a malformed diagnostic, never raise on one.
    if not isinstance(pair, dict) or pair.get("staged_sha256") == pair.get("readback_sha256"):
        return False
    # 4. The aggregate mismatch shape agrees with exactly that one change.
    aggregate = diagnostic.get("aggregate_protected_fingerprint")
    if not isinstance(aggregate, dict) or aggregate.get("matches") is not False:
        return False
    meta = diagnostic.get("meta_data_projection")
    if not isinstance(meta, dict) or meta.get("status") != "changed":
        return False
    detail = meta.get("diagnostic")
    if not isinstance(detail, dict):
        return False
    # 5. Same entry count; nothing added, removed, re-identified or reordered.
    if detail.get("staged_entry_count") != detail.get("readback_entry_count"):
        return False
    if detail.get("added_entry_count") or detail.get("removed_entry_count"):
        return False
    if detail.get("identity_changed_position_count"):
        return False
    if detail.get("order_changed") is not False:
        return False
    # 6. Exactly one value-changed entry, and the bounded report omitted nothing.
    if detail.get("value_changed_entry_count") != 1:
        return False
    omitted = detail.get("omitted_from_report")
    if not isinstance(omitted, dict) or any(
            omitted.get(name) for name in
            ("added", "removed", "value_changed", "identity_changed")):
        return False
    rows = detail.get("value_changed_entries")
    if not isinstance(rows, list) or len(rows) != 1:
        return False
    row = rows[0]
    if not isinstance(row, dict):
        return False
    # 7. That entry is the fixed key, at the same index, with the same numeric id.
    if row.get("staged_index") != row.get("readback_index"):
        return False
    if not _exact_int(row.get("id")) or row["id"] < 0:
        return False
    if row.get("key") != GLA_META_KEY or row.get("key_sha256") != GLA_META_KEY_SHA256:
        return False
    # 8. Exactly the staged digest before, exactly the transient digest after.
    if row.get("staged_value_sha256") != GLA_STAGED_VALUE_SHA256:
        return False
    if row.get("readback_value_sha256") != GLA_TRANSIENT_VALUE_SHA256:
        return False
    if row.get("value_differs") is not True:
        return False
    return row.get("staged_entry_sha256") != row.get("readback_entry_sha256")


def mismatch_message(diagnostic: dict[str, Any]) -> str:
    names = list(diagnostic["changed_protected_fields"])
    if not names and diagnostic["meta_data_projection"]["status"] != "matched":
        names = [META_FIELD]
    label = ", ".join(names) or "unidentified protected state"
    endpoint = diagnostic["endpoint"]
    if diagnostic["phase"] == "pre_write":
        return (f"{endpoint} protected pre-write mismatch: {label}. "
                "Nothing was written to this resource; stage a new plan.")
    if diagnostic["phase"] == CONVERGENCE_PHASE:
        if diagnostic.get("shipping_class_matches_plan") is False:
            return (f"{endpoint} no longer carries the approved shipping class during "
                    "the bounded Google-sync wait. Reconcile this resource.")
        return (f"{endpoint} protected state moved during the bounded Google-sync "
                f"wait: {label}. Reconcile this resource.")
    return (f"{endpoint} protected readback mismatch: {label}. "
            "Reconcile this resource.")


def read_target(target: dict[str, Any], vault: dict[str, Any] | None = None) -> dict[str, Any]:
    endpoint = target_endpoint(target)
    record, _ = wc.api_get(endpoint, vault=vault)
    if not isinstance(record, dict):
        raise ShippingPolicyError(f"{endpoint} did not return one resource.")
    expected = target["variation_id"] if target["kind"] == "variation" else target["product_id"]
    if int(record.get("id") or 0) != int(expected):
        raise ShippingPolicyError(f"{endpoint} returned a different resource ID.")
    if target["kind"] == "variation" and int(record.get("parent_id") or 0) != int(target["product_id"]):
        raise ShippingPolicyError(f"{endpoint} is not a child of the enumerated parent product.")
    return record


def find_freight_class(vault: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows, _ = wc.api_get(
        "/products/shipping_classes",
        {"slug": FREIGHT_CLASS_SLUG, "per_page": 100, "_fields": "id,name,slug"},
        vault,
    )
    if not isinstance(rows, list):
        raise ShippingPolicyError("The shipping-class lookup returned an unexpected response.")
    for row in rows:
        if isinstance(row, dict) and str(row.get("slug") or "") == FREIGHT_CLASS_SLUG:
            return row
    return None


def require_rachad_approval(approval: Any) -> None:
    """Only the exact string APPROVED. No trimming, no case folding, no variants.

    The earlier check stripped whitespace and case-folded, so `approved`,
    ` APPROVED ` and `Approved` all passed. Rachad's standing rule is one plain
    uppercase word, and a looser check is a looser guardrail: it lets a quoted,
    echoed or auto-completed value stand in for a decision he made. This runs
    before any vault load and before any network access.
    """
    if not isinstance(approval, str) or approval != APPROVAL_WORD:
        raise ShippingPolicyError(
            f"Rachad must answer this staged plan with the exact one-word approval: "
            f"{APPROVAL_WORD}. It is compared exactly -- surrounding spaces, a different "
            "case, punctuation or any other wording is refused. It must come from his own "
            "message; workflow authorization is not change approval."
        )


def lock_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".commit-lock.json")


def write_lock(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise ShippingPolicyError(
            "This plan has already entered commit and cannot be replayed."
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def stage_plan(action: str, payload: dict[str, Any], targets: list[dict[str, Any]],
               sources: dict[str, str]) -> Path:
    created = utc_now()
    method, first_endpoint = endpoint_for(action, targets[0] if targets else None)
    validate_write_route(method, first_endpoint)
    for target in targets:
        validate_write_route("PUT", target_endpoint(target))
    validate_payload(action, payload)
    core = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "origin": EXACT_ORIGIN,
        "action": action,
        "method": method,
        "created_utc": created.isoformat(),
        "expires_utc": (created + timedelta(hours=PLAN_LIFETIME_HOURS)).isoformat(),
        "nonce": secrets.token_hex(16),
        "shipping_class_name": FREIGHT_CLASS_NAME,
        "shipping_class_slug": FREIGHT_CLASS_SLUG,
        "convergence_contract": convergence_contract(),
        "payload": payload,
        "targets": targets,
        "sources": sources,
    }
    digest = digest_for(core)
    plan = {**core, "sha256": digest}
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%dT%H%M%SZ")
    path = PLAN_DIR / f"{stamp}_{action}_{digest[:16]}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    wc.append_receipt("woocommerce_shipping_policy_plan_staged", str(path))
    return path


def command_stage(args: argparse.Namespace) -> None:
    raw = read_json(args.input)
    extra = sorted(set(raw) - {"action", "targets", "sources"})
    if extra:
        raise ShippingPolicyError("Unsupported top-level field(s): " + ", ".join(extra))
    action = str(raw.get("action") or "").strip().casefold()
    if action not in ACTIONS:
        raise ShippingPolicyError("action must be one of: " + ", ".join(sorted(ACTIONS)))
    sources = {"policy": clean_source((raw.get("sources") or {}).get("policy"), "sources.policy")}

    if action == "shipping_class_create":
        if raw.get("targets"):
            raise ShippingPolicyError("shipping_class_create takes no targets.")
        if find_freight_class() is not None:
            raise ShippingPolicyError(
                f"The {FREIGHT_CLASS_SLUG} shipping class already exists. No change is needed."
            )
        payload = {"name": FREIGHT_CLASS_NAME, "slug": FREIGHT_CLASS_SLUG}
        targets: list[dict[str, Any]] = []
        preview: list[dict[str, Any]] = []
    else:
        targets = clean_targets(raw.get("targets"))
        desired = SLUG_FOR_ACTION[action]
        payload = {"shipping_class": desired}
        if action == "shipping_class_assign" and find_freight_class() is None:
            raise ShippingPolicyError(
                f"The {FREIGHT_CLASS_SLUG} shipping class does not exist yet. "
                "Stage and commit shipping_class_create first."
            )
        preview = []
        for target in targets:
            record = read_target(target)
            current = str(record.get("shipping_class") or "")
            target["before_shipping_class"] = current
            target["before_stale_fingerprint"] = stale_fingerprint(record)
            target["before_protected_fingerprint"] = protected_fingerprint(record)
            target["before_date_modified_gmt"] = str(record.get("date_modified_gmt") or "")
            target["before_protected_field_fingerprints"] = protected_field_fingerprints(record)
            target["before_meta_data_projection"] = metadata_projection(record)
            # Refuses here, before Rachad ever sees a plan, if Google sync is
            # unsettled on this resource. Names eligibility only -- never a value.
            target[GLA_ELIGIBILITY_FIELD] = gla_convergence_eligible(
                target["before_meta_data_projection"])
            preview.append({
                "endpoint": target_endpoint(target),
                "sku": str(record.get("sku") or ""),
                "name": str(record.get("name") or ""),
                "before_shipping_class": current,
                "after_shipping_class": desired,
                "already_correct": current == desired,
                "protected_fields_fingerprinted": len(PROTECTED_FIELDS),
                "meta_data_entries_projected": len(target["before_meta_data_projection"]),
                GLA_ELIGIBILITY_FIELD: target[GLA_ELIGIBILITY_FIELD],
            })
        if all(row["already_correct"] for row in preview):
            raise ShippingPolicyError("No change was detected: every target already matches.")

    path = stage_plan(action, payload, targets, sources)
    plan = json.loads(path.read_text(encoding="utf-8"))
    diagnostic_scope = action in ASSIGNMENT_ACTIONS and len(targets) == 1
    print(json.dumps({
        "status": "STAGED_NOT_COMMITTED",
        "plan": str(path),
        "expires_utc": plan["expires_utc"],
        "action": action,
        "method": plan["method"],
        "payload": payload,
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "shipping_class_name": FREIGHT_CLASS_NAME,
        "shipping_class_slug": FREIGHT_CLASS_SLUG,
        "convergence_contract": plan["convergence_contract"],
        "target_count": len(targets),
        "diagnostic_scope": diagnostic_scope,
        "scope_note": (
            "One target only: a diagnostic scope. It is the ordinary approved "
            f"{action}, writing the same fixed payload " + canonical(payload)
            + " to this one resource. It still needs Rachad's own exact one-word "
            "approval, and it is not a test-only mutation. Its value is that a "
            "protected-field mismatch can be attributed to one resource before the "
            "rest of the catalog is touched."
            if diagnostic_scope else
            f"{len(targets)} enumerated target(s); ordinary scope."
        ),
        "targets": preview,
        "sources": sources,
        "approval": APPROVAL_WORD,
        "external_write_performed": False,
    }, indent=2, ensure_ascii=False))


def load_plan(path: str) -> dict[str, Any]:
    plan = read_json(path)
    saved = str(plan.pop("sha256", ""))
    if not saved or not secrets.compare_digest(saved, digest_for(plan)):
        raise ShippingPolicyError("Plan hash check failed. The plan changed after review.")
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise ShippingPolicyError(
            f"REFUSED: this plan is not schema {SCHEMA_VERSION}. Schema-1 plans carry only "
            "an aggregate protected fingerprint, so a mismatch could not name the field "
            "that moved. Schema-2 plans carry no convergence contract and no per-target "
            "Google-sync eligibility, so the bounded wait has no evidence to stand on. "
            "Both are permanently unusable and rehashing one does not revive it. Stage a "
            "new plan. Existing commit locks remain authoritative."
        )
    if plan.get("tool_version") != TOOL_VERSION:
        raise ShippingPolicyError(
            f"REFUSED: this plan was not staged by tool version {TOOL_VERSION}."
        )
    if plan.get("tool") != TOOL_NAME or plan.get("origin") != EXACT_ORIGIN:
        raise ShippingPolicyError("The plan schema, tool, or origin is invalid.")
    action = str(plan.get("action") or "")
    if action not in ACTIONS:
        raise ShippingPolicyError("The plan action is not allowlisted.")
    if plan.get("shipping_class_name") != FREIGHT_CLASS_NAME or \
            plan.get("shipping_class_slug") != FREIGHT_CLASS_SLUG:
        raise ShippingPolicyError("The plan shipping class name or slug is not the fixed value.")
    validate_convergence_contract(plan.get("convergence_contract"))
    try:
        expires = datetime.fromisoformat(str(plan["expires_utc"]))
    except (KeyError, ValueError) as exc:
        raise ShippingPolicyError("Plan expiry is invalid.") from exc
    if utc_now() >= expires:
        raise ShippingPolicyError("Plan expired. Stage a new plan for review.")
    validate_payload(action, plan.get("payload"))

    raw_targets = plan.get("targets")
    if action == "shipping_class_create":
        if raw_targets:
            raise ShippingPolicyError("A class-creation plan cannot carry targets.")
        method, endpoint = endpoint_for(action, None)
        if plan.get("method") != method:
            raise ShippingPolicyError("Plan method is not the internally constructed route.")
        validate_write_route(method, endpoint)
    else:
        if not isinstance(raw_targets, list) or not raw_targets:
            raise ShippingPolicyError("An assignment plan must enumerate its targets.")
        if len(raw_targets) > MAX_TARGETS:
            raise ShippingPolicyError(f"Plan exceeds the {MAX_TARGETS}-resource safety limit.")
        stripped = []
        for index, row in enumerate(raw_targets):
            if not isinstance(row, dict):
                raise ShippingPolicyError(f"targets[{index}] must be an object.")
            required = {"kind", "product_id", "variation_id", "before_shipping_class",
                        "before_stale_fingerprint", "before_protected_fingerprint",
                        "before_date_modified_gmt", "before_protected_field_fingerprints",
                        "before_meta_data_projection", GLA_ELIGIBILITY_FIELD}
            if set(row) != required:
                raise ShippingPolicyError(f"targets[{index}] has an unexpected shape.")
            # The aggregate fingerprint is never optional, and the per-field mapping
            # must be exactly closed over PROTECTED_FIELDS.
            clean_digest(row["before_protected_fingerprint"],
                         f"targets[{index}].before_protected_fingerprint")
            clean_digest(row["before_stale_fingerprint"],
                         f"targets[{index}].before_stale_fingerprint")
            validate_field_fingerprints(
                row["before_protected_field_fingerprints"],
                f"targets[{index}].before_protected_field_fingerprints",
            )
            validate_meta_projection(
                row["before_meta_data_projection"],
                f"targets[{index}].before_meta_data_projection",
            )
            # The eligibility flag is never trusted as stored. It is re-derived
            # from the plan's own projection, so a rehashed plan cannot flip a
            # resource into the bounded wait -- and an unsettled projection is
            # refused here exactly as it would have been at staging.
            eligible = row[GLA_ELIGIBILITY_FIELD]
            if not isinstance(eligible, bool):
                raise ShippingPolicyError(
                    f"targets[{index}].{GLA_ELIGIBILITY_FIELD} must be true or false."
                )
            if eligible != gla_convergence_eligible(row["before_meta_data_projection"]):
                raise ShippingPolicyError(
                    f"targets[{index}].{GLA_ELIGIBILITY_FIELD} disagrees with this "
                    "target's own metadata projection."
                )
            base = {"kind": row["kind"], "product_id": row["product_id"]}
            if row["kind"] == "variation":
                base["variation_id"] = row["variation_id"]
            elif row["variation_id"] != 0:
                raise ShippingPolicyError(f"targets[{index}] product must carry variation_id 0.")
            stripped.append(base)
        # Re-run the full closed target validation on the plan's own values.
        revalidated = clean_targets(stripped)
        if [(t["kind"], t["product_id"], t["variation_id"]) for t in revalidated] != \
                [(r["kind"], r["product_id"], r["variation_id"]) for r in raw_targets]:
            raise ShippingPolicyError("Plan targets failed the current allowlist validation.")
        if plan.get("method") != "PUT":
            raise ShippingPolicyError("Plan method is not the internally constructed route.")
        for target in raw_targets:
            validate_write_route("PUT", target_endpoint(target))
    plan["sha256"] = saved
    return plan


def _commit_class_create(plan: dict[str, Any], vault: dict[str, Any]) -> dict[str, Any]:
    if find_freight_class(vault) is not None:
        raise ShippingPolicyError("The freight shipping class already exists. Nothing was written.")
    method, endpoint = endpoint_for("shipping_class_create", None)
    validate_write_route(method, endpoint)
    result, _ = wc.api_request(method, endpoint, payload=plan["payload"], vault=vault)
    if not isinstance(result, dict) or not int(result.get("id") or 0):
        raise ShippingPolicyError("WooCommerce returned success without a shipping-class ID.")
    readback = find_freight_class(vault)
    if readback is None:
        raise ShippingPolicyError("The freight shipping class could not be read back.")
    if str(readback.get("name") or "") != FREIGHT_CLASS_NAME or \
            str(readback.get("slug") or "") != FREIGHT_CLASS_SLUG:
        raise ShippingPolicyError("The read-back shipping class name or slug did not match exactly.")
    if int(readback.get("id") or 0) != int(result["id"]):
        raise ShippingPolicyError("The read-back shipping class is not the created one.")
    return {"shipping_class_id": int(readback["id"]), "name": FREIGHT_CLASS_NAME,
            "slug": FREIGHT_CLASS_SLUG}


def _elapsed_since(started: float) -> float:
    return round(max(0.0, monotonic() - started), 3)


def _await_gla_convergence(target: dict[str, Any], endpoint: str, desired: str,
                           vault: dict[str, Any]) -> dict[str, Any]:
    """Bounded, READ-ONLY wait for the one proven Google-sync transition.

    One fresh GET per scheduled step, at most six steps, at most 90 seconds. There
    is no second write of any kind here -- no retry, no rollback, no metadata
    write, no compensating change -- only reading, and only for this one target.

    Success is the COMPLETE staged protected state coming back: the aggregate
    fingerprint, all per-field fingerprints and the whole metadata projection, plus
    the approved shipping class. The transient state is never success; if the
    schedule expires while it persists, this raises and the plan locks
    indeterminate.
    """
    started = monotonic()
    attempts = 0
    for wait_seconds in CONVERGENCE_SCHEDULE_SECONDS:
        sleep(wait_seconds)
        attempts += 1
        record = read_target(target, vault)
        class_matches = str(record.get("shipping_class") or "") == desired
        diagnostic = protected_diagnostic(endpoint, CONVERGENCE_PHASE, target, record)
        if diagnostic is None and class_matches:
            return {
                "convergence_used": True,
                "convergence_attempts": attempts,
                "convergence_elapsed_seconds": _elapsed_since(started),
                "convergence_meta_key": GLA_META_KEY,
                "final_requirement": CONVERGENCE_FINAL_REQUIREMENT,
                "final_state": CONVERGENCE_FINAL_REQUIREMENT,
            }
        detail = diagnostic if diagnostic is not None else matched_diagnostic(
            endpoint, CONVERGENCE_PHASE, target, record)
        detail = {**detail, "shipping_class_matches_plan": class_matches}
        if not is_gla_sync_transient(target, detail, class_matches):
            # Anything other than the exact transient ends this permanently.
            raise ProtectedStateMismatch(mismatch_message(detail), detail)
    raise ConvergenceTimeout(
        f"{endpoint} did not return to the exact staged protected state within the "
        f"fixed {CONVERGENCE_MAX_SECONDS}-second bounded wait ({attempts} read-only "
        f"observations). The transient {GLA_META_KEY} state is NOT accepted as "
        "success. The assignment itself landed; nothing was retried, rolled back or "
        "written again. Reconcile this resource before any new plan.",
        {
            "endpoint": endpoint,
            "kind": CONVERGENCE_KIND,
            "meta_key": GLA_META_KEY,
            "staged_value_sha256": GLA_STAGED_VALUE_SHA256,
            "transient_value_sha256": GLA_TRANSIENT_VALUE_SHA256,
            "schedule_seconds": list(CONVERGENCE_SCHEDULE_SECONDS),
            "max_seconds": CONVERGENCE_MAX_SECONDS,
            "convergence_attempts": attempts,
            "convergence_elapsed_seconds": _elapsed_since(started),
            "final_requirement": CONVERGENCE_FINAL_REQUIREMENT,
            "final_state": CONVERGENCE_TIMEOUT_FINAL_STATE,
        },
    )


def _commit_assignment(plan: dict[str, Any], vault: dict[str, Any],
                       lock: Path) -> list[dict[str, Any]]:
    desired = plan["payload"]["shipping_class"]
    if plan["action"] == "shipping_class_assign" and find_freight_class(vault) is None:
        raise ShippingPolicyError("The freight shipping class is missing. Nothing was written.")
    results: list[dict[str, Any]] = []
    attempted: list[str] = []
    for target in plan["targets"]:
        endpoint = target_endpoint(target)
        validate_write_route("PUT", endpoint)
        current = read_target(target, vault)
        if stale_fingerprint(current) != target["before_stale_fingerprint"] or \
                str(current.get("date_modified_gmt") or "") != target["before_date_modified_gmt"]:
            raise ShippingPolicyError(
                f"{endpoint} changed after review. Stage a new plan. "
                f"Completed targets so far: {attempted}"
            )
        before_protected = protected_fingerprint(current)
        if before_protected != target["before_protected_fingerprint"]:
            raise ShippingPolicyError(f"{endpoint} protected fields changed after review.")
        # Every per-field fingerprint and the whole metadata projection must match
        # the plan BEFORE anything is written. A mismatch here means the plan is
        # stale for this resource; it is refused, never normalised or accepted.
        pre_write = protected_diagnostic(endpoint, "pre_write", target, current)
        if pre_write is not None:
            raise ProtectedStateMismatch(mismatch_message(pre_write), pre_write)
        if str(current.get("shipping_class") or "") == desired:
            results.append({"endpoint": endpoint, "shipping_class": desired,
                            "written": False, "reason": "already correct",
                            "convergence_used": False})
            continue
        attempted.append(endpoint)
        write_lock(lock, {
            "plan_sha256": plan["sha256"], "status": "in_flight",
            "updated_utc": utc_now().isoformat(), "attempted_endpoints": attempted,
        })
        result, _ = wc.api_request("PUT", endpoint, payload=plan["payload"], vault=vault)
        if not isinstance(result, dict) or not int(result.get("id") or 0):
            raise ShippingPolicyError(f"{endpoint} returned success without a resource ID.")
        readback = read_target(target, vault)
        if str(readback.get("shipping_class") or "") != desired:
            raise ShippingPolicyError(f"{endpoint} read-back shipping class did not match.")
        # The aggregate fingerprint is still mandatory and is still the thing that
        # forbids the change; protected_diagnostic re-checks it alongside every
        # per-field hash and the metadata projection, and only EXPLAINS a failure.
        # Raising here stops the loop before any further target is touched.
        post_write = protected_diagnostic(endpoint, "post_write", target, readback)
        if post_write is None:
            results.append({"endpoint": endpoint, "shipping_class": desired,
                            "written": True, "convergence_used": False})
            continue
        if not is_gla_sync_transient(target, post_write, True):
            raise ProtectedStateMismatch(mismatch_message(post_write), post_write)
        # Exactly the proven Google-sync transient, and nothing else. Look again,
        # read-only and within a fixed bound, for the complete staged state. The
        # next target cannot start until this one is exact again -- a timeout or
        # any other movement raises out of this loop permanently.
        results.append({"endpoint": endpoint, "shipping_class": desired, "written": True,
                        **_await_gla_convergence(target, endpoint, desired, vault)})
    return results


def failure_record(exc: BaseException, vault: dict[str, Any] | None) -> dict[str, Any]:
    """What a lock is allowed to say about a failure.

    A ShippingPolicyError message is written by this tool, so it is safe to keep.
    A transport failure is NOT: WooError embeds up to 1000 characters of the
    server's response body, which can carry page text or product content. Only
    its class, HTTP status and REST code are kept, and any other exception is
    reduced to its class name -- never an exception dump.
    """
    if isinstance(exc, ProtectedStateMismatch):
        return {"reason_class": "ProtectedStateMismatch",
                "reason": wc.scrub(str(exc), vault),
                "diagnostic": exc.diagnostic}
    if isinstance(exc, ConvergenceTimeout):
        # Fixed vocabulary, counts, seconds and hash identifiers only. No page or
        # product value, no metadata value, no header, no body, no traceback.
        return {"reason_class": "ConvergenceTimeout",
                "reason": wc.scrub(str(exc), vault),
                "convergence": exc.record}
    if isinstance(exc, ShippingPolicyError):
        return {"reason_class": "ShippingPolicyError", "reason": wc.scrub(str(exc), vault)}
    if isinstance(exc, wc.WooError):
        return {"reason_class": "WooError", "http_status": exc.status, "rest_code": exc.code,
                "reason": "WooCommerce transport failure. The response body is deliberately "
                          "not recorded."}
    return {"reason_class": type(exc).__name__,
            "reason": "The failure detail is deliberately not recorded."}


def receipt_evidence(record: dict[str, Any]) -> str:
    """One bounded line: field names, counts and the phase. Never a value."""
    convergence = record.get("convergence")
    if isinstance(convergence, dict):
        return "; ".join([
            f"reason_class={record.get('reason_class')}",
            f"endpoint={convergence.get('endpoint')}",
            f"kind={convergence.get('kind')}",
            f"meta_key={convergence.get('meta_key')}",
            f"staged_value_sha256={convergence.get('staged_value_sha256')}",
            f"transient_value_sha256={convergence.get('transient_value_sha256')}",
            f"convergence_attempts={convergence.get('convergence_attempts')}",
            f"convergence_elapsed_seconds={convergence.get('convergence_elapsed_seconds')}",
            f"max_seconds={convergence.get('max_seconds')}",
            f"final_requirement={convergence.get('final_requirement')}",
            f"final_state={convergence.get('final_state')}",
        ])
    diagnostic = record.get("diagnostic")
    if not isinstance(diagnostic, dict):
        return f"reason_class={record.get('reason_class')}"
    meta = diagnostic.get("meta_data_projection") or {}
    detail = meta.get("diagnostic") or {}
    parts = [
        f"reason_class={record.get('reason_class')}",
        f"phase={diagnostic.get('phase')}",
        f"endpoint={diagnostic.get('endpoint')}",
        "changed_protected_fields=" + (
            ",".join(diagnostic.get("changed_protected_fields") or []) or "none"),
        f"meta_data_projection={meta.get('status')}",
    ]
    if detail:
        parts.append(
            "meta_added={added}; meta_removed={removed}; meta_value_changed={changed}; "
            "meta_identity_changed={identity}; meta_order_changed={order}".format(
                added=detail.get("added_entry_count"),
                removed=detail.get("removed_entry_count"),
                changed=detail.get("value_changed_entry_count"),
                identity=detail.get("identity_changed_position_count"),
                order=detail.get("order_changed"),
            )
        )
    return "; ".join(parts)


def command_commit(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve()
    if PLAN_DIR.resolve() not in plan_path.parents:
        raise ShippingPolicyError("Plan must be inside Dado's WooCommerce shipping-plan folder.")
    plan = load_plan(str(plan_path))
    require_rachad_approval(args.approval)
    lock = lock_path(plan_path)
    if lock.exists():
        raise ShippingPolicyError("This plan has already entered commit and cannot be replayed.")
    vault = wc.load_vault()
    if vault.get("declared_permissions") != "read_write":
        raise ShippingPolicyError("Saved WooCommerce key is not declared Read/Write.")
    try:
        saved_origin = wc.normalize_site_url(str(vault.get("site_url") or ""))
    except wc.WooError as exc:
        raise ShippingPolicyError(
            "Saved WooCommerce origin is not the exact FRP Depot origin."
        ) from exc
    if saved_origin != wc.ALLOWED_ORIGIN:
        raise ShippingPolicyError("Saved WooCommerce origin is not the exact FRP Depot origin.")
    write_lock(lock, {
        "plan_sha256": plan["sha256"], "status": "in_flight",
        "started_utc": utc_now().isoformat(), "attempted_endpoints": [],
    }, exclusive=True)
    action = str(plan["action"])
    try:
        if action == "shipping_class_create":
            outcome: Any = _commit_class_create(plan, vault)
        else:
            outcome = _commit_assignment(plan, vault, lock)
    except Exception as exc:
        record = failure_record(exc, vault)
        write_lock(lock, {
            "plan_sha256": plan["sha256"], "status": "indeterminate",
            "action": action, "updated_utc": utc_now().isoformat(), **record,
        })
        wc.append_receipt(
            "woocommerce_shipping_policy_indeterminate_no_retry",
            f"action={action}; plan={plan_path}; sha256={plan['sha256']}; "
            + receipt_evidence(record),
        )
        detail = str(exc) if isinstance(exc, ShippingPolicyError) else ""
        raise ShippingPolicyError(
            (detail + " " if detail else "")
            + "The write result is indeterminate or failed verification. This plan is locked "
            "and will not retry. Reconcile the affected resources in WooCommerce before any "
            "new plan."
        ) from exc
    write_lock(lock, {
        "plan_sha256": plan["sha256"], "status": "committed_verified",
        "updated_utc": utc_now().isoformat(), "outcome": outcome,
    })
    converged = [row for row in outcome
                 if isinstance(row, dict) and row.get("convergence_used")] \
        if isinstance(outcome, list) else []
    wc.append_receipt(
        "woocommerce_shipping_policy_committed",
        f"action={action}; plan={plan_path}; sha256={plan['sha256']}; "
        f"convergence_used_targets={len(converged)}; "
        f"convergence_meta_key={GLA_META_KEY}; "
        f"final_state={CONVERGENCE_FINAL_REQUIREMENT}",
    )
    print(json.dumps({
        "status": "COMMITTED_AND_VERIFIED", "action": action,
        "shipping_class_slug": FREIGHT_CLASS_SLUG, "outcome": outcome,
        "convergence": {
            "kind": CONVERGENCE_KIND,
            "meta_key": GLA_META_KEY,
            "targets_converged": len(converged),
            "max_seconds": CONVERGENCE_MAX_SECONDS,
            "final_requirement": CONVERGENCE_FINAL_REQUIREMENT,
        },
        "plan_sha256": plan["sha256"], "replay_locked": True,
    }, indent=2, ensure_ascii=False))


def command_deploy_checkout_guard(_: argparse.Namespace) -> None:
    """Permanently disabled. It exists so the refusal is explicit and testable."""
    raise ShippingPolicyError(
        "REFUSED: checkout-guard deployment is hard-disabled in this tool. The WooCommerce "
        "REST API exposes no product/shipping route that can install site-side checkout "
        "logic, and this connector has no plugin-upload, file-write, or PHP-execution "
        "capability by design. Install "
        "Dado/Tools/woocommerce/freight_checkout_guard/frpdepot-freight-checkout-guard.zip "
        "by hand in WordPress, or commission a separate, separately-tested deployment route."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--input", required=True)
    stage.set_defaults(func=command_stage)
    commit = commands.add_parser("commit")
    commit.add_argument("--plan", required=True)
    commit.add_argument("--approval", required=True)
    commit.set_defaults(func=command_commit)
    blocked = commands.add_parser("deploy-checkout-guard")
    blocked.set_defaults(func=command_deploy_checkout_guard)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (ShippingPolicyError, wc.WooError, OSError, ValueError) as exc:
        print("ERROR: " + wc.scrub(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
