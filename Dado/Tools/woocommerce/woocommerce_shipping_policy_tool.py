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

*** SCHEMA 4 (2026-08-10) -- THE PENDING BASELINE. ***
Schema 3 assumed the settled value is the only lawful before-state, and refused to
stage anything else. That assumption produced a DEADLOCK, reproduced live on
/products/1455/variations/1457: the entry sits at the PENDING digest and stays
there -- monitors saw it long after the 90-second transient window, and the six
next blank FRP Pipe candidates were in the same state. An untouched variation can
rest at that value indefinitely, because nothing is going to move it until the
product is updated; schema 3 demanded a move before it would allow the update.

Schema 4 repairs the verifier by adding a SECOND closed baseline, and nothing else:

  * BASELINE MODES are a closed enum decided by SOURCE from the value-free
    projection: "absent" (no Google-sync entry -- no wait ever applies),
    "settled_baseline" (exactly the schema-3 path, unchanged) and
    "pending_baseline" (exactly the one already-proven transient digest). Any
    third value, a duplicate entry, a malformed entry or an entry with no stable
    numeric id still REFUSES staging.
  * NO INPUT CAN CHOOSE. The mode is never read from a request, and it is never
    trusted as stored: load_plan RE-DERIVES it from the plan's own hashed
    projection, so a rehashed edit cannot flip a target into another mode.
  * A PENDING BASELINE MUST BE PROVEN STILL. Staging takes the first read, then
    two more fresh GETs of the same exact resource on a fixed 2s/4s schedule
    (6-second ceiling, owned by source). All three observations must agree on the
    shipping class, date_modified_gmt, the aggregate fingerprint, every per-field
    fingerprint, the complete metadata projection, and the one sound
    `_wc_gla_sync_status` entry at the exact pending digest with a stable numeric
    id. Any disagreement refuses staging and writes no plan at all.
  * COMMIT RE-PROVES IT. Before the one PUT, the fresh pre-write read must still
    carry the whole staged baseline AND the same mode, entry index, entry id and
    value digest.
  * AFTER THE WRITE, a pending baseline has exactly two successful shapes:
      - the complete protected state is still exactly the staged pending state.
        That is success IMMEDIATELY: the write moved nothing protected, and
        waiting for settlement to call an unchanged state successful would be
        inventing a requirement.
      - the ONLY movement is that same entry, same id, same index, same count and
        order, from the fixed pending digest to the fixed settled digest -- the
        reverse half of the convergence schema 3 already proved. That is not
        accepted on sight: a fixed 2s/4s confirmation (6-second ceiling) must show
        the complete settled state, unchanged, on every observation.
    Anything else -- any other value, any other protected field, any metadata
    add/remove/reorder/identity change, a shipping-class drift, a GET error, a
    timeout or any instability -- locks the plan indeterminate. No retry, no
    rollback, no second PUT.
  * The settled-baseline path is untouched: same detector, same fixed 90-second
    schedule, same "the transient is never success" rule.

Schema-1, schema-2 and schema-3 plans are all permanently unusable and are refused
before any vault or network access even if their hash is recomputed. Existing
commit locks remain authoritative.

*** SCHEMA 5 (2026-08-11) -- THE MEASURED THREE-ENTRY SETTLEMENT. ***
Schema 4's pending path was right about the transition and wrong about its SHAPE.
It recognised a settlement only when EXACTLY ONE metadata entry moved. Live
evidence disproved that on the 31-target FRP Pipe plan
20260811T204921Z_shipping_class_assign_3e02e445093c9afb: 21 targets verified, then
at target 22 -- /products/1455/variations/1476, SKU PIDN450150PSI411 -- the single
approved PUT set the freight class and the immediate readback showed Google's save
hook had settled the resource in ONE step, moving THREE existing entries' values
together with no add, no remove, no id change, no key change and no reorder:

    `_wc_gla_sync_status`  id 45152, index 1   pending digest -> settled digest
    `_wc_gla_synced_at`    id 45151, index 0   new save stamp
    `_wc_gla_sync_hash`    id 74838, index 5   new content hash

Schema 4 could only read that as an unknown third-party edit. It raised
ProtectedStateMismatch, locked the plan indeterminate and stopped -- correctly, by
its own rules, but on a resource that was in fact exactly right. Later read-only
GETs confirmed variation 1476 carries the freight class and a settled Google
status; the 9 remaining Pipe targets and the 24 Manway/Manway Cover targets were
never attempted.

Schema 5 adds ONE more exactly-known movement, and nothing else:

  * SETTLEMENT SHAPES are a closed enum fixed by SOURCE: "gla_status_only" (the
    schema-4 shape, unchanged) and "gla_status_with_stamp_and_hash" (the measured
    one). There is no third shape and no way to add one from outside this module.
  * THE STATUS TRANSITION IS THE GATE. In BOTH shapes `_wc_gla_sync_status` must
    move from the one pinned pending digest to the one pinned settled digest --
    never to a third value, never in the other direction.
  * ONLY TWO NAMED COMPANIONS, and only their VALUES. `_wc_gla_synced_at` and
    `_wc_gla_sync_hash` may differ; their ids, keys and list indexes may not, they
    are matched by key digest as well as by name, and exactly three entries in
    total may differ. A fourth changed value, a duplicate of any of the three, or
    any add/remove/reorder/identity drift is refused exactly as before. This is
    NOT an exemption for Google metadata, for all metadata, or for these three
    keys unconditionally: outside the one status transition, on any other
    baseline, or in any other structural shape, all three are protected as ever.
  * CONFIRMATION IS UNCHANGED AND STILL EXACT. The accepted settled state must
    reproduce COMPLETELY -- class, date_modified_gmt, aggregate hash, every
    per-field hash and the whole projection -- on the fixed 2s/4s schedule. A
    stamp or hash that moves AGAIN during confirmation fails closed; ongoing churn
    is never accepted.
  * The unchanged pending state remains immediate success, and the settled
    baseline's 90-second contract is untouched in every respect.

Schema-1 through schema-4 plans are all permanently unusable and are refused before
any vault or network access even if their hash is recomputed -- the version bump
and the four new contract fields make that true twice over. Existing commit locks,
including 3e02e445093c9afb's, remain authoritative and are never revisited.
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
SCHEMA_VERSION = 5
TOOL_VERSION = "5.0.0"
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

# --- The CLOSED baseline modes (schema 4) ----------------------------------
# Which of the two proven Google-sync states a target rests in. A closed enum,
# decided by SOURCE from the value-free projection, never supplied by a request
# and never trusted as stored -- load_plan re-derives it from the plan's own
# hashed projection, so a rehashed edit cannot flip a target into another mode.
#
# The two live values behind these modes are the strings "synced" and "pending".
# The mode names are DELIBERATELY not either of those words on their own: a leak
# check has to be able to tell "the tool named its own mode" from "the tool echoed
# a value it read", and a bare "pending" in a plan would be indistinguishable from
# the real metadata value. Compound tokens stay strippable; bare ones would not.
BASELINE_ABSENT = "absent"                 # No entry at all. No wait ever applies.
BASELINE_SETTLED = "settled_baseline"      # Exactly the schema-3 path, unchanged.
BASELINE_PENDING = "pending_baseline"      # The one already-proven pending digest.
BASELINE_MODES = (BASELINE_ABSENT, BASELINE_SETTLED, BASELINE_PENDING)
GLA_BASELINE_MODE_FIELD = "gla_baseline_mode"

# The one digest each waitable mode is allowed to rest at. There is no third
# entry here and no way to add one from outside this module.
BASELINE_VALUE_SHA256 = {
    BASELINE_SETTLED: GLA_STAGED_VALUE_SHA256,
    BASELINE_PENDING: GLA_TRANSIENT_VALUE_SHA256,
}

# A pending before-state is only honest if it is STILL. Staging takes the first
# read and then two more fresh GETs of the same exact resource on this fixed
# front-loaded schedule. Short on purpose: this runs inside Rachad's staging
# command, and the point is to catch a resource mid-flight, not to outwait one.
PENDING_STABILITY_SCHEDULE_SECONDS = (2, 4)
PENDING_STABILITY_MAX_SECONDS = sum(PENDING_STABILITY_SCHEDULE_SECONDS)
PENDING_STABILITY_CEILING_SECONDS = 6
PENDING_STABILITY_OBSERVATIONS = 1 + len(PENDING_STABILITY_SCHEDULE_SECONDS)
# The ceiling above bounds the SCHEDULE, which is the only thing this tool paces.
# Measured elapsed time also contains the store's own answer time for the extra
# reads, which this tool does not control; it is bounded by the transport timeout
# per read rather than by the schedule, and it is recorded, never relied upon.
WOO_READ_TIMEOUT_SECONDS = 60
PENDING_STABILITY_ELAPSED_LIMIT_SECONDS = (
    PENDING_STABILITY_MAX_SECONDS
    + WOO_READ_TIMEOUT_SECONDS * len(PENDING_STABILITY_SCHEDULE_SECONDS)
)

# After a write against a pending baseline, the settled digest coming back is the
# reverse half of the proven convergence -- but it is not accepted on sight. This
# fixed schedule must show the complete settled state, unchanged, every time.
PENDING_SETTLE_CONFIRM_SCHEDULE_SECONDS = (2, 4)
PENDING_SETTLE_CONFIRM_MAX_SECONDS = sum(PENDING_SETTLE_CONFIRM_SCHEDULE_SECONDS)
PENDING_SETTLE_CONFIRM_CEILING_SECONDS = 6
PENDING_SETTLE_CONFIRM_OBSERVATIONS = len(PENDING_SETTLE_CONFIRM_SCHEDULE_SECONDS)

# What a pending baseline is allowed to end as, in fixed vocabulary.
PENDING_FINAL_REQUIREMENT = "exact_staged_pending_state_or_confirmed_settled_state"
PENDING_UNCHANGED_FINAL_STATE = "exact_staged_pending_state"
PENDING_SETTLED_FINAL_STATE = "confirmed_stable_settled_state"
PENDING_CONFIRM_PHASE = "pending_settle_confirmation"

# The closed shape of the per-target stability evidence a plan may carry. Hashes,
# indexes, counts and seconds only -- never a metadata value.
PENDING_STABILITY_KEYS = frozenset({
    "mode", "observations", "schedule_seconds", "max_seconds", "elapsed_seconds",
    "value_sha256", "meta_entry_index", "meta_entry_id", "stable",
})
GLA_STABILITY_FIELD = "gla_pending_stability"


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

# --- The CLOSED settlement shapes (schema 5) -------------------------------
# Schema 4 recognised a settlement as ONE entry moving: `_wc_gla_sync_status`,
# pending digest -> settled digest, nothing else at all. Live evidence on
# 2026-08-11 disproved that as the only shape. On
# /products/1455/variations/1476 the single approved PUT set the freight class,
# and the immediate readback showed Google's own save hook had settled the
# resource in ONE step: three existing entries changed VALUE together, with no
# entry added, removed, re-identified or reordered --
#
#   `_wc_gla_sync_status`  pending digest -> settled digest   (id 45152, index 1)
#   `_wc_gla_synced_at`    new stamp                          (id 45151, index 0)
#   `_wc_gla_sync_hash`    new content hash                   (id 74838, index 5)
#
# Schema 4 could only read that as an unknown third-party edit, so it locked the
# plan indeterminate at target 22 of 31 after the assignment had already landed.
#
# The repair is NOT a tolerance for Google metadata. It is one more exactly-known
# movement, and it is as closed as the first: the same one status transition
# between the same two pinned digests, plus exactly these two named companions,
# each of which may move its VALUE and nothing else -- not its id, not its key,
# not its index. A stamp and a content hash are unpredictable BY CONSTRUCTION, so
# they are the only two entries whose new value cannot be pinned; that is why the
# rule pins their identity instead, and why the settled state still has to hold
# still across the bounded confirmation before anything is called committed.
GLA_SYNCED_AT_META_KEY = "_wc_gla_synced_at"
GLA_SYNC_HASH_META_KEY = "_wc_gla_sync_hash"
GLA_SETTLEMENT_COMPANION_KEYS = (GLA_SYNCED_AT_META_KEY, GLA_SYNC_HASH_META_KEY)
GLA_SETTLEMENT_TRIPLET_KEYS = (GLA_META_KEY,) + GLA_SETTLEMENT_COMPANION_KEYS
GLA_SETTLEMENT_TRIPLET_ENTRY_COUNT = len(GLA_SETTLEMENT_TRIPLET_KEYS)
GLA_SETTLEMENT_TRIPLET_KEY_SHA256 = {key: sha256_of(key)
                                     for key in GLA_SETTLEMENT_TRIPLET_KEYS}

# What a pending baseline's write is allowed to look like, as a closed enum. Both
# are settlements of the SAME status entry between the SAME two pinned digests;
# they differ only in whether Google's two companion entries moved with it.
SETTLEMENT_STATUS_ONLY = "gla_status_only"
SETTLEMENT_STATUS_WITH_STAMP_AND_HASH = "gla_status_with_stamp_and_hash"
SETTLEMENT_SHAPES = (SETTLEMENT_STATUS_ONLY, SETTLEMENT_STATUS_WITH_STAMP_AND_HASH)
SETTLEMENT_SHAPE_FIELD = "settlement_shape"


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
        # Schema 4. The pending baseline reuses the SAME two digests -- it adds no
        # third state, only a second lawful before-state and the bounded proofs
        # that make it honest.
        "baseline_modes": list(BASELINE_MODES),
        "settled_baseline_value_sha256": GLA_STAGED_VALUE_SHA256,
        "pending_baseline_value_sha256": GLA_TRANSIENT_VALUE_SHA256,
        "pending_stability_schedule_seconds": list(PENDING_STABILITY_SCHEDULE_SECONDS),
        "pending_stability_max_seconds": PENDING_STABILITY_MAX_SECONDS,
        "pending_settle_confirm_schedule_seconds":
            list(PENDING_SETTLE_CONFIRM_SCHEDULE_SECONDS),
        "pending_settle_confirm_max_seconds": PENDING_SETTLE_CONFIRM_MAX_SECONDS,
        "pending_final_requirement": PENDING_FINAL_REQUIREMENT,
        # Schema 5. The two closed settlement shapes, and the exact three metadata
        # keys the wider one may ever find moved. No third shape and no fourth key
        # can be introduced from outside this module, and adding these four fields
        # is what makes every schema-4 contract semantically invalid: load_plan
        # requires the contract's key set to be exactly this one.
        "settlement_shapes": list(SETTLEMENT_SHAPES),
        "settlement_status_meta_key": GLA_META_KEY,
        "settlement_companion_meta_keys": list(GLA_SETTLEMENT_COMPANION_KEYS),
        "settlement_max_changed_meta_entries": GLA_SETTLEMENT_TRIPLET_ENTRY_COUNT,
    }


CONVERGENCE_CONTRACT_KEYS = frozenset(convergence_contract())


def _exact_int(value: Any) -> bool:
    """A real JSON integer. A bool is not an int here, and 2.0 is not 2.

    Load-bearing: ``[2.0, 4.0] == [2, 4]`` in Python, so plain equality alone
    would let a rehashed float schedule through.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_fixed_schedule(raw: dict[str, Any], schedule_key: str, max_key: str,
                             fixed: tuple[int, ...], ceiling: int) -> None:
    """One bounded, ordered, integer schedule that totals its own declared max.

    Applied identically to all three fixed schedules, so none of them can be
    widened, re-ordered, floated or decoupled from its own ceiling.
    """
    schedule = raw[schedule_key]
    if not isinstance(schedule, list) or len(schedule) != len(fixed):
        raise ShippingPolicyError(
            f"REFUSED: convergence_contract.{schedule_key} is not the fixed schedule."
        )
    if not all(_exact_int(step) and step > 0 for step in schedule):
        raise ShippingPolicyError(
            f"REFUSED: convergence_contract.{schedule_key} must be positive integers."
        )
    if not _exact_int(raw[max_key]):
        raise ShippingPolicyError(
            f"REFUSED: convergence_contract.{max_key} must be an integer."
        )
    if sum(schedule) != raw[max_key]:
        raise ShippingPolicyError(
            f"REFUSED: convergence_contract.{max_key} is not the exact schedule total."
        )
    if raw[max_key] > ceiling:
        raise ShippingPolicyError(
            f"REFUSED: convergence_contract.{max_key} exceeds the fixed "
            f"{ceiling}-second ceiling."
        )


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
    _validate_fixed_schedule(raw, "schedule_seconds", "max_seconds",
                             CONVERGENCE_SCHEDULE_SECONDS, CONVERGENCE_CEILING_SECONDS)
    _validate_fixed_schedule(raw, "pending_stability_schedule_seconds",
                             "pending_stability_max_seconds",
                             PENDING_STABILITY_SCHEDULE_SECONDS,
                             PENDING_STABILITY_CEILING_SECONDS)
    _validate_fixed_schedule(raw, "pending_settle_confirm_schedule_seconds",
                             "pending_settle_confirm_max_seconds",
                             PENDING_SETTLE_CONFIRM_SCHEDULE_SECONDS,
                             PENDING_SETTLE_CONFIRM_CEILING_SECONDS)
    for field in ("staged_value_sha256", "transient_value_sha256",
                  "settled_baseline_value_sha256", "pending_baseline_value_sha256"):
        clean_digest(raw[field], f"convergence_contract.{field}")
    # `3.0 == 3` in Python, so the generic equality below would let a rehashed
    # float through -- the same trap _exact_int exists for on the schedules.
    if not _exact_int(raw["settlement_max_changed_meta_entries"]):
        raise ShippingPolicyError(
            "REFUSED: convergence_contract.settlement_max_changed_meta_entries must "
            "be an integer."
        )
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


def gla_baseline_row(projection: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The one sound `_wc_gla_sync_status` projection row, or None if absent.

    Entries are matched by key DIGEST, not by the printable key, so an entry that
    carries this key beside an unsound id cannot slip past as "not found".
    """
    rows = [row for row in projection if row["key_sha256"] == GLA_META_KEY_SHA256]
    if not rows:
        return None
    if len(rows) > 1:
        raise ShippingPolicyError(
            f"REFUSED: {GLA_META_KEY} appears {len(rows)} times in this resource's "
            "metadata. Exactly one entry is required; stage nothing against it."
        )
    row = rows[0]
    # A stable numeric id is required as well as a sound shape: the detectors below
    # track this entry across fresh reads by id, so an entry that cannot be
    # identified could never be followed anyway.
    if row["shape"] != "entry" or row["key"] != GLA_META_KEY or \
            not _exact_int(row["id"]) or row["id"] < 0:
        raise ShippingPolicyError(
            f"REFUSED: the {GLA_META_KEY} metadata entry is malformed or carries no "
            "stable numeric id, so its state cannot be established. Stage nothing "
            "against this resource."
        )
    return row


def gla_baseline_mode(projection: list[dict[str, Any]]) -> str:
    """Which CLOSED baseline this resource's Google-sync flag rests in.

    Decided from the value-free projection alone, so it can be recomputed at commit
    from the plan's own evidence rather than trusted as a stored string.

    "absent"           -- no `_wc_gla_sync_status` entry. An ordinary resource
                          Google does not track; neither wait applies to it.
    "settled_baseline" -- exactly one sound entry at the contract's settled digest.
                          This is the schema-3 path, unchanged in every respect.
    "pending_baseline" -- exactly one sound entry at the contract's pending digest.
                          Schema 3 refused this outright, which deadlocked every
                          resource resting there; schema 4 accepts it only after a
                          bounded stability proof.
    REFUSED            -- the key appears more than once, is malformed, carries no
                          stable numeric id, or holds ANY value other than those
                          two proven ones. A third value is a state nobody has
                          diagnosed: the detectors could not tell it from a real
                          third-party edit, so nothing is staged.
    """
    row = gla_baseline_row(projection)
    if row is None:
        return BASELINE_ABSENT
    for mode, digest in BASELINE_VALUE_SHA256.items():
        if row["value_sha256"] == digest:
            return mode
    # The two baselines are named by their MODE tokens, never by the live words
    # they stand for: an error message is output, and output must stay value-free.
    raise ShippingPolicyError(
        f"REFUSED: the {GLA_META_KEY} entry matches neither the {BASELINE_SETTLED} "
        f"digest ({GLA_STAGED_VALUE_SHA256[:16]}...) nor the {BASELINE_PENDING} digest "
        f"({GLA_TRANSIENT_VALUE_SHA256[:16]}...). That is a Google-sync state this "
        "tool has never diagnosed, so it cannot tell it from a third-party edit. "
        "Stage nothing against this resource."
    )


def gla_convergence_eligible(projection: list[dict[str, Any]]) -> bool:
    """True only for the SETTLED baseline -- the schema-3 wait's own precondition.

    Kept as its own name because the settled path reads exactly this and nothing
    else, so schema 4 cannot widen that path by accident.
    """
    return gla_baseline_mode(projection) == BASELINE_SETTLED


def baseline_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    """Everything an observation of one resource must hold still, value-free.

    date_modified_gmt is INCLUDED here, unlike protected_fingerprint: a stability
    proof is asking "is anything happening to this resource right now", and the
    modification stamp is the store's own answer to that.
    """
    return {
        "shipping_class": str(record.get("shipping_class") or ""),
        "date_modified_gmt": str(record.get("date_modified_gmt") or ""),
        "protected_fingerprint": protected_fingerprint(record),
        "protected_field_fingerprints": protected_field_fingerprints(record),
        "meta_data_projection": metadata_projection(record),
    }


def validate_pending_stability(raw: Any, mode: str, projection: list[dict[str, Any]],
                               label: str) -> None:
    """Re-validate the stability evidence a plan carries. Closed, and re-derived.

    Only a pending baseline may carry evidence, and every field of it except the
    measured elapsed time is recomputed from source constants and from the plan's
    own hashed projection -- so a rehashed edit can neither invent a proof nor
    change what the proof claims.
    """
    if mode != BASELINE_PENDING:
        if raw is not None:
            raise ShippingPolicyError(
                f"{label} must be null: only a {BASELINE_PENDING} target carries a "
                "stability proof."
            )
        return
    if not isinstance(raw, dict) or set(raw) != PENDING_STABILITY_KEYS:
        raise ShippingPolicyError(f"{label} has an unexpected shape.")
    row = gla_baseline_row(projection)
    if row is None:
        raise ShippingPolicyError(f"{label} has no Google-sync entry to stand on.")
    expected = {
        "mode": BASELINE_PENDING,
        "observations": PENDING_STABILITY_OBSERVATIONS,
        "schedule_seconds": list(PENDING_STABILITY_SCHEDULE_SECONDS),
        "max_seconds": PENDING_STABILITY_MAX_SECONDS,
        "value_sha256": GLA_TRANSIENT_VALUE_SHA256,
        "meta_entry_index": row["index"],
        "meta_entry_id": row["id"],
        "stable": True,
    }
    for field, value in expected.items():
        if raw[field] != value or type(raw[field]) is not type(value):
            raise ShippingPolicyError(
                f"REFUSED: {label}.{field} is not the value this plan's own evidence "
                "and this tool's fixed schedule produce."
            )
    # `[2.0, 4.0] == [2, 4]` in Python, so list equality alone would let a rehashed
    # float schedule through -- the same trap _exact_int exists for.
    if not all(_exact_int(step) for step in raw["schedule_seconds"]):
        raise ShippingPolicyError(
            f"REFUSED: {label}.schedule_seconds must be the fixed integer schedule."
        )
    elapsed = raw["elapsed_seconds"]
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or \
            elapsed < PENDING_STABILITY_MAX_SECONDS or \
            elapsed > PENDING_STABILITY_ELAPSED_LIMIT_SECONDS:
        raise ShippingPolicyError(
            f"REFUSED: {label}.elapsed_seconds must be at least the fixed "
            f"{PENDING_STABILITY_MAX_SECONDS}-second schedule and no more than "
            f"{PENDING_STABILITY_ELAPSED_LIMIT_SECONDS} seconds. A proof that took "
            "less time than its own schedule did not run it."
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


def _gla_meta_only_movement(diagnostic: Any, shipping_class_matches: bool,
                            changed_entries: int) -> list[dict[str, Any]] | None:
    """The structural half every accepted Google-sync movement must satisfy.

    Returns the bounded value-changed rows when meta_data is the ONLY protected
    field that moved, exactly ``changed_entries`` entries changed value, and
    NOTHING else moved -- no entry added, removed, re-identified or reordered, no
    count change, and no row omitted from the bounded report. Returns None
    otherwise. Pure: no I/O, no clock.

    Fail-closed by construction: every shape is checked before it is read, so a
    malformed or hand-built diagnostic answers None rather than raising, and the
    caller still records the ordinary protected-state mismatch.
    """
    # A malformed or absent bounded diagnostic can never prove a known movement.
    if not isinstance(diagnostic, dict):
        return None
    # 1. The approved class is already exactly in place.
    if not shipping_class_matches:
        return None
    # 2. meta_data is the only protected field that moved, and it really moved.
    if diagnostic.get("changed_protected_fields") != list(CONVERGENCE_ALLOWED_CHANGED_FIELDS):
        return None
    fields = diagnostic.get("field_fingerprints")
    if not isinstance(fields, dict) or set(fields) != set(CONVERGENCE_ALLOWED_CHANGED_FIELDS):
        return None
    pair = fields[META_FIELD]
    if not isinstance(pair, dict) or pair.get("staged_sha256") == pair.get("readback_sha256"):
        return None
    # 3. The aggregate mismatch shape agrees with exactly that one field change.
    aggregate = diagnostic.get("aggregate_protected_fingerprint")
    if not isinstance(aggregate, dict) or aggregate.get("matches") is not False:
        return None
    meta = diagnostic.get("meta_data_projection")
    if not isinstance(meta, dict) or meta.get("status") != "changed":
        return None
    detail = meta.get("diagnostic")
    if not isinstance(detail, dict):
        return None
    # 4. Same entry count; nothing added, removed, re-identified or reordered.
    if detail.get("staged_entry_count") != detail.get("readback_entry_count"):
        return None
    if detail.get("added_entry_count") or detail.get("removed_entry_count"):
        return None
    if detail.get("identity_changed_position_count"):
        return None
    if detail.get("order_changed") is not False:
        return None
    # 5. Exactly the expected number of value-changed entries, and the bounded
    #    report omitted nothing -- an omitted row could hide a further change.
    if detail.get("value_changed_entry_count") != changed_entries:
        return None
    omitted = detail.get("omitted_from_report")
    if not isinstance(omitted, dict) or any(
            omitted.get(name) for name in
            ("added", "removed", "value_changed", "identity_changed")):
        return None
    rows = detail.get("value_changed_entries")
    if not isinstance(rows, list) or len(rows) != changed_entries:
        return None
    if not all(isinstance(row, dict) for row in rows):
        return None
    return rows


def _is_value_only_move(row: dict[str, Any]) -> bool:
    """One value-changed row whose VALUE moved and whose identity did not.

    Same list index before and after, same stable non-negative numeric id, and a
    real difference. Position and identity are what this pins; the value itself is
    pinned (or deliberately not) by the caller.
    """
    if row.get("staged_index") != row.get("readback_index"):
        return False
    if not _exact_int(row.get("id")) or row["id"] < 0:
        return False
    if row.get("value_differs") is not True:
        return False
    return row.get("staged_entry_sha256") != row.get("readback_entry_sha256")


def _is_single_gla_value_move(diagnostic: dict[str, Any], shipping_class_matches: bool,
                              before_sha256: str, after_sha256: str) -> bool:
    """One `_wc_gla_sync_status` value move, between two FIXED digests, and nothing
    else at all. Pure: no I/O, no clock. Unchanged in behaviour since schema 3.

    Every condition must hold. Anything else -- any other field, entry, identity,
    count, order, value, class or shape -- is an immediate permanent indeterminate
    mismatch, reported through the ordinary schema-2 bounded diagnostic.

    This is not a tolerance. It recognises one exactly-known movement; what the
    caller may then do with it is decided by the caller's baseline, not here.
    """
    rows = _gla_meta_only_movement(diagnostic, shipping_class_matches, 1)
    if rows is None:
        return False
    row = rows[0]
    # The fixed key, at the same index, with the same numeric id.
    if not _is_value_only_move(row):
        return False
    if row.get("key") != GLA_META_KEY or row.get("key_sha256") != GLA_META_KEY_SHA256:
        return False
    # Exactly the caller's fixed digest before, exactly its fixed digest after.
    if row.get("staged_value_sha256") != before_sha256:
        return False
    return row.get("readback_value_sha256") == after_sha256


def _triplet_entries_are_singular(projection: Any) -> bool:
    """Each of the three named keys appears EXACTLY once in the staged projection.

    Matched by key DIGEST, exactly as gla_baseline_row matches, so an entry
    carrying one of these keys beside an unsound id or an unprintable key cannot
    slip past as "not there". A resource holding two `_wc_gla_sync_hash` entries is
    a state nobody has diagnosed, and this tool must not guess which of them
    Google meant to move.

    The readback side needs no separate count: the structural half above already
    proved the two projections carry one identity list, in one order, with nothing
    added, removed or re-identified -- so if the staged side is singular the
    readback side is too.
    """
    if not isinstance(projection, list):
        return False
    counts = Counter(row.get("key_sha256") for row in projection
                     if isinstance(row, dict))
    return all(counts.get(digest) == 1
               for digest in GLA_SETTLEMENT_TRIPLET_KEY_SHA256.values())


def _is_gla_settlement_triplet_move(target: dict[str, Any], diagnostic: dict[str, Any],
                                    shipping_class_matches: bool) -> bool:
    """The EXACT three-entry Google settlement observed live on 2026-08-11.

    The same single status transition the predicate above recognises -- the one
    pinned pending digest to the one pinned settled digest, never to a third value
    -- carried out together with Google's two named companion entries, and nothing
    else whatsoever. Pure: no I/O, no clock.

    Closed on every axis that can be closed:
      * meta_data is the only protected field that moved (shared structural half);
      * the entry count, order, ids and keys are identical, and nothing is added,
        removed, re-identified or omitted from the bounded report;
      * EXACTLY three entries changed value -- no fewer, no more;
      * their keys are exactly `_wc_gla_sync_status`, `_wc_gla_synced_at` and
        `_wc_gla_sync_hash`, one occurrence each, matched by key digest as well as
        by name, so a relabelled entry cannot pass;
      * each of those three keys occurs EXACTLY once in the staged projection
        itself, so a resource carrying a duplicate of any of them is refused
        rather than guessed at;
      * each of the three kept its own index and its own stable numeric id;
      * the status entry moved pending digest -> settled digest and nothing else.

    The two companions are a save stamp and a content hash: their new values are
    unpredictable by construction, so they are the only part of this movement that
    cannot be pinned to a digest. Their IDENTITY and POSITION are pinned instead,
    the count is exact, and the resulting settled state still has to hold still
    across the bounded read-only confirmation before anything is committed. This
    is not "Google metadata is exempt": a fourth key, a second occurrence of one
    of these three, a status move to any other value, or a companion entry that
    changed anything but its value all answer False.
    """
    if not isinstance(target, dict):
        return False
    if not _triplet_entries_are_singular(target.get("before_meta_data_projection")):
        return False
    rows = _gla_meta_only_movement(diagnostic, shipping_class_matches,
                                   GLA_SETTLEMENT_TRIPLET_ENTRY_COUNT)
    if rows is None:
        return False
    if not all(_is_value_only_move(row) for row in rows):
        return False
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get("key")
        # `key` is checked to be one of three fixed strings BEFORE it is used as a
        # mapping key: a hand-built diagnostic could carry an unhashable value
        # there, and this predicate must answer False rather than raise.
        if not isinstance(key, str) or key not in GLA_SETTLEMENT_TRIPLET_KEY_SHA256:
            return False
        if row.get("key_sha256") != GLA_SETTLEMENT_TRIPLET_KEY_SHA256[key]:
            return False
        if key in by_key:
            # The same key twice is not the triplet, whatever the third row says.
            return False
        by_key[key] = row
    if set(by_key) != set(GLA_SETTLEMENT_TRIPLET_KEYS):
        return False
    status = by_key[GLA_META_KEY]
    if status.get("staged_value_sha256") != GLA_TRANSIENT_VALUE_SHA256:
        return False
    return status.get("readback_value_sha256") == GLA_STAGED_VALUE_SHA256


def is_gla_sync_transient(target: dict[str, Any], diagnostic: dict[str, Any],
                          shipping_class_matches: bool) -> bool:
    """The SETTLED baseline's one waitable mismatch: settled -> pending.

    Unchanged from schema 3, down to its precondition: only a target staged with a
    settled, single, exact Google-sync entry can ever reach the 90-second wait.
    """
    if target.get(GLA_ELIGIBILITY_FIELD) is not True:
        return False
    return _is_single_gla_value_move(diagnostic, shipping_class_matches,
                                     GLA_STAGED_VALUE_SHA256, GLA_TRANSIENT_VALUE_SHA256)


def gla_settlement_shape(target: dict[str, Any], diagnostic: dict[str, Any],
                         shipping_class_matches: bool) -> str | None:
    """Which CLOSED settlement shape this post-write movement is, or None.

    The PENDING baseline's acceptable movement is always the same status
    transition -- pending digest -> settled digest -- in one of exactly two
    observed shapes:

    "gla_status_only"
        The status entry alone. Schema 3 proved this transition live in the
        forward direction on /products/1455/variations/2056, and schema 4 accepted
        its reverse half.
    "gla_status_with_stamp_and_hash"
        The same transition carried out together with `_wc_gla_synced_at` and
        `_wc_gla_sync_hash`. Measured live on /products/1455/variations/1476 on
        2026-08-11, where schema 4 could only read it as an unknown third-party
        edit and locked the plan indeterminate mid-run.

    Neither is success on its own -- the caller must confirm the settled state
    holds across a fixed bounded schedule before anything is called committed.

    Only a target whose plan re-derived the pending baseline can reach either
    shape, so a settled-baseline target can never be settled-confirmed by mistake,
    and an absent-baseline target can reach neither path.
    """
    if target.get(GLA_BASELINE_MODE_FIELD) != BASELINE_PENDING:
        return None
    if _is_single_gla_value_move(diagnostic, shipping_class_matches,
                                 GLA_TRANSIENT_VALUE_SHA256, GLA_STAGED_VALUE_SHA256):
        return SETTLEMENT_STATUS_ONLY
    if _is_gla_settlement_triplet_move(target, diagnostic, shipping_class_matches):
        return SETTLEMENT_STATUS_WITH_STAMP_AND_HASH
    return None


def is_gla_sync_settling(target: dict[str, Any], diagnostic: dict[str, Any],
                         shipping_class_matches: bool) -> bool:
    """True for either closed settlement shape. One decision point, not two."""
    return gla_settlement_shape(target, diagnostic, shipping_class_matches) is not None


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
    if diagnostic["phase"] == PENDING_CONFIRM_PHASE:
        # Deliberately not built from `label`: when the resource falls BACK to the
        # staged pending state nothing differs from the plan, so the field list is
        # empty and would read as "unidentified" rather than as the real answer.
        if diagnostic.get("shipping_class_matches_plan") is False:
            return (f"{endpoint} no longer carries the approved shipping class during "
                    "the bounded settled-state confirmation. Reconcile this resource.")
        return (f"{endpoint} did not hold one exact settled {GLA_META_KEY} state across "
                f"the fixed {PENDING_SETTLE_CONFIRM_MAX_SECONDS}-second confirmation "
                f"({PENDING_SETTLE_CONFIRM_OBSERVATIONS} read-only observations). The "
                "assignment itself landed; nothing was retried, rolled back or written "
                "again. Reconcile this resource.")
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


def prove_pending_baseline_stable(target: dict[str, Any], first_record: dict[str, Any],
                                  vault: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bounded, READ-ONLY proof that a pending before-state is actually STILL.

    Schema 3 refused a pending baseline outright because a state that is mid-flight
    is a moving before-state, and Rachad must never approve one of those. That
    reasoning is right; the mistake was concluding that every pending state is
    mid-flight. Live evidence says otherwise: an untouched variation rests at the
    pending digest indefinitely, long past the 90-second transient window.

    So the refusal is replaced by a measurement, not by an assumption. The first
    read is the one staging already took; two more fresh GETs of the SAME exact
    resource follow on the fixed 2s/4s schedule, and all three observations must
    agree on every one of: the shipping class, date_modified_gmt, the aggregate
    protected fingerprint, every per-field fingerprint, the complete metadata
    projection, and the single sound `_wc_gla_sync_status` entry -- same stable
    numeric id, same index, exactly the pending digest.

    Any disagreement REFUSES staging and no plan is written. Nothing is written to
    the store on any path through this function.
    """
    endpoint = target_endpoint(target)
    baseline = baseline_snapshot(first_record)
    if gla_baseline_mode(baseline["meta_data_projection"]) != BASELINE_PENDING:
        raise ShippingPolicyError(
            f"REFUSED: {endpoint} is not at the {BASELINE_PENDING}; no stability proof "
            "applies to it."
        )
    started = monotonic()
    observations = 1
    for wait_seconds in PENDING_STABILITY_SCHEDULE_SECONDS:
        sleep(wait_seconds)
        record = read_target(target, vault)
        observations += 1
        again = baseline_snapshot(record)
        if again != baseline or \
                gla_baseline_mode(again["meta_data_projection"]) != BASELINE_PENDING:
            raise ShippingPolicyError(
                f"REFUSED: {endpoint} did not hold one exact state across "
                f"{PENDING_STABILITY_OBSERVATIONS} fresh read-only observations over "
                f"{PENDING_STABILITY_MAX_SECONDS} seconds, so its {GLA_META_KEY} state "
                "is moving right now and its before-state cannot be staged honestly. "
                "Nothing was written and no plan was created; try again once the "
                "resource is quiet."
            )
    row = gla_baseline_row(baseline["meta_data_projection"])
    return {
        "mode": BASELINE_PENDING,
        "observations": observations,
        "schedule_seconds": list(PENDING_STABILITY_SCHEDULE_SECONDS),
        "max_seconds": PENDING_STABILITY_MAX_SECONDS,
        "elapsed_seconds": _elapsed_since(started),
        "value_sha256": GLA_TRANSIENT_VALUE_SHA256,
        "meta_entry_index": row["index"],
        "meta_entry_id": row["id"],
        "stable": True,
    }


def prove_staged_baseline_live(target: dict[str, Any], record: dict[str, Any],
                               endpoint: str) -> str:
    """Re-prove the plan's whole baseline against ONE fresh read, before the PUT.

    The pre-write projection comparison already implies most of this, but the
    baseline is the thing the post-write branch dispatches on, so it is proven
    explicitly and freshly rather than inferred. Returns the proven mode.
    """
    mode = target[GLA_BASELINE_MODE_FIELD]
    live_projection = metadata_projection(record)
    live_mode = gla_baseline_mode(live_projection)
    if live_mode != mode:
        raise ShippingPolicyError(
            f"{endpoint} is no longer at the {mode} {GLA_META_KEY} baseline this plan "
            "was staged against. Nothing was written to this resource; stage a new plan."
        )
    if mode == BASELINE_ABSENT:
        return mode
    live_row = gla_baseline_row(live_projection)
    staged_row = gla_baseline_row(target["before_meta_data_projection"])
    if staged_row is None or live_row is None or \
            live_row["index"] != staged_row["index"] or \
            live_row["id"] != staged_row["id"] or \
            live_row["value_sha256"] != BASELINE_VALUE_SHA256[mode]:
        raise ShippingPolicyError(
            f"{endpoint} {GLA_META_KEY} entry moved its index, its id or its value "
            "since this plan was staged. Nothing was written to this resource; stage "
            "a new plan."
        )
    return mode


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
            # Refuses here, before Rachad ever sees a plan, if the Google-sync entry
            # is duplicated, malformed, unidentifiable or at a state nobody has
            # diagnosed. Names the closed mode only -- never a value.
            mode = gla_baseline_mode(target["before_meta_data_projection"])
            target[GLA_BASELINE_MODE_FIELD] = mode
            target[GLA_ELIGIBILITY_FIELD] = mode == BASELINE_SETTLED
            # A pending before-state is only staged once it has been MEASURED still.
            # Read-only, bounded, and a refusal writes no plan at all.
            target[GLA_STABILITY_FIELD] = (
                prove_pending_baseline_stable(target, record)
                if mode == BASELINE_PENDING else None
            )
            preview.append({
                "endpoint": target_endpoint(target),
                "sku": str(record.get("sku") or ""),
                "name": str(record.get("name") or ""),
                "before_shipping_class": current,
                "after_shipping_class": desired,
                "already_correct": current == desired,
                "protected_fields_fingerprinted": len(PROTECTED_FIELDS),
                "meta_data_entries_projected": len(target["before_meta_data_projection"]),
                GLA_BASELINE_MODE_FIELD: mode,
                GLA_ELIGIBILITY_FIELD: target[GLA_ELIGIBILITY_FIELD],
                GLA_STABILITY_FIELD: target[GLA_STABILITY_FIELD],
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
        "baseline_modes": {mode: sum(1 for row in preview
                                     if row.get(GLA_BASELINE_MODE_FIELD) == mode)
                           for mode in BASELINE_MODES},
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
            f"Schema-3 plans carry no baseline mode and no {GLA_STABILITY_FIELD} proof, "
            f"so a {BASELINE_PENDING} before-state in one could never have been measured "
            "still. Schema-4 plans carry a verifier that recognised only the narrower "
            "settlement shape, so the measured three-entry Google settlement locked them "
            "indeterminate mid-run. All four are permanently unusable and rehashing one "
            "does not revive it. Stage a new plan. Existing commit locks remain "
            "authoritative."
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
                        "before_meta_data_projection", GLA_BASELINE_MODE_FIELD,
                        GLA_ELIGIBILITY_FIELD, GLA_STABILITY_FIELD}
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
            # Neither the baseline mode nor the eligibility flag is trusted as
            # stored. Both are RE-DERIVED from the plan's own hashed projection, so
            # a rehashed plan cannot flip a resource into another mode or into the
            # bounded wait -- and a duplicated, malformed or undiagnosed projection
            # is refused here exactly as it would have been at staging.
            mode = row[GLA_BASELINE_MODE_FIELD]
            if mode not in BASELINE_MODES:
                raise ShippingPolicyError(
                    f"targets[{index}].{GLA_BASELINE_MODE_FIELD} is not one of the "
                    f"closed baseline modes: {', '.join(BASELINE_MODES)}."
                )
            if mode != gla_baseline_mode(row["before_meta_data_projection"]):
                raise ShippingPolicyError(
                    f"targets[{index}].{GLA_BASELINE_MODE_FIELD} disagrees with this "
                    "target's own metadata projection."
                )
            eligible = row[GLA_ELIGIBILITY_FIELD]
            if not isinstance(eligible, bool):
                raise ShippingPolicyError(
                    f"targets[{index}].{GLA_ELIGIBILITY_FIELD} must be true or false."
                )
            if eligible != (mode == BASELINE_SETTLED):
                raise ShippingPolicyError(
                    f"targets[{index}].{GLA_ELIGIBILITY_FIELD} disagrees with this "
                    "target's own baseline mode."
                )
            validate_pending_stability(
                row[GLA_STABILITY_FIELD], mode, row["before_meta_data_projection"],
                f"targets[{index}].{GLA_STABILITY_FIELD}",
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


def _confirm_pending_settled(target: dict[str, Any], endpoint: str, desired: str,
                             settled_record: dict[str, Any],
                             vault: dict[str, Any]) -> dict[str, Any]:
    """Bounded, READ-ONLY confirmation that a newly SETTLED state is holding.

    Reached only when a pending-baseline write produced one of the two CLOSED
    settlement shapes: the fixed `_wc_gla_sync_status` entry moving from the
    pending digest to the settled digest, same id, same index, same count, same
    order -- alone, or together with `_wc_gla_synced_at` and `_wc_gla_sync_hash`
    changing value only. Either way it is a state change, so it is not accepted on
    sight.

    The post-write read becomes the expected state, and every observation on the
    fixed 2s/4s schedule must reproduce it EXACTLY: the shipping class,
    date_modified_gmt, the aggregate fingerprint, every per-field fingerprint, the
    complete projection, and the settled baseline itself. A resource that flips
    back to pending, moves on to anything else, or errors, fails here -- the plan
    locks indeterminate and nothing is retried, rolled back or written again.

    That exactness is what keeps the wider settlement shape honest. The stamp and
    the content hash are accepted ONCE, as part of one recognised transition; if
    either moves AGAIN during this confirmation the complete-projection comparison
    below fails and the plan locks. Ongoing churn is never tolerated, because the
    expected state is the whole snapshot and not a subset of it.
    """
    expected = baseline_snapshot(settled_record)
    started = monotonic()
    observations = 0
    for wait_seconds in PENDING_SETTLE_CONFIRM_SCHEDULE_SECONDS:
        sleep(wait_seconds)
        record = read_target(target, vault)
        observations += 1
        class_matches = str(record.get("shipping_class") or "") == desired
        try:
            observed = baseline_snapshot(record)
            holding = class_matches and observed == expected and \
                gla_baseline_mode(observed["meta_data_projection"]) == BASELINE_SETTLED
        except ShippingPolicyError:
            # An unreadable or undiagnosed metadata state is a failure, not a pass.
            holding = False
        if holding:
            continue
        detail = protected_diagnostic(endpoint, PENDING_CONFIRM_PHASE, target, record) \
            or matched_diagnostic(endpoint, PENDING_CONFIRM_PHASE, target, record)
        detail = {**detail, "shipping_class_matches_plan": class_matches}
        raise ProtectedStateMismatch(mismatch_message(detail), detail)
    return {
        "convergence_used": True,
        "convergence_attempts": observations,
        "convergence_elapsed_seconds": _elapsed_since(started),
        "convergence_meta_key": GLA_META_KEY,
        "final_requirement": PENDING_FINAL_REQUIREMENT,
        "final_state": PENDING_SETTLED_FINAL_STATE,
    }


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
        # The whole staged baseline, re-proven on this fresh read, BEFORE the PUT.
        # The post-write branch dispatches on it, so it is never inferred.
        mode = prove_staged_baseline_live(target, current, endpoint)
        if str(current.get("shipping_class") or "") == desired:
            results.append({"endpoint": endpoint, "shipping_class": desired,
                            "written": False, "reason": "already correct",
                            "baseline_mode": mode, "convergence_used": False})
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
        row = {"endpoint": endpoint, "shipping_class": desired, "written": True,
               "baseline_mode": mode}
        if post_write is None:
            # The complete staged protected state, unchanged. That is success on
            # ANY baseline, immediately. A pending baseline in particular must
            # never wait for settlement merely to call an unchanged state
            # successful -- the write moved nothing protected, and inventing a
            # settlement requirement would fail correct work.
            unchanged = {**row, "convergence_used": False}
            if mode == BASELINE_PENDING:
                unchanged["final_requirement"] = PENDING_FINAL_REQUIREMENT
                unchanged["final_state"] = PENDING_UNCHANGED_FINAL_STATE
            results.append(unchanged)
            continue
        if mode == BASELINE_PENDING:
            # A pending baseline has exactly two other acceptable shapes, both of
            # them the SAME status entry moving on to the settled digest -- alone,
            # or together with Google's two named companion entries. Either is
            # confirmed, not assumed, and the confirmation is identical for both.
            shape = gla_settlement_shape(target, post_write, True)
            if shape is None:
                raise ProtectedStateMismatch(mismatch_message(post_write), post_write)
            results.append({**row, SETTLEMENT_SHAPE_FIELD: shape,
                            **_confirm_pending_settled(
                                target, endpoint, desired, readback, vault)})
            continue
        if not is_gla_sync_transient(target, post_write, True):
            raise ProtectedStateMismatch(mismatch_message(post_write), post_write)
        # Exactly the proven Google-sync transient, and nothing else. Look again,
        # read-only and within a fixed bound, for the complete staged state. The
        # next target cannot start until this one is exact again -- a timeout or
        # any other movement raises out of this loop permanently.
        results.append({**row, **_await_gla_convergence(target, endpoint, desired, vault)})
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
    rows = outcome if isinstance(outcome, list) else []
    converged = [row for row in rows if isinstance(row, dict) and row.get("convergence_used")]
    modes = {mode: sum(1 for row in rows
                       if isinstance(row, dict) and row.get("baseline_mode") == mode)
             for mode in BASELINE_MODES}
    settled_confirmed = sum(1 for row in converged
                            if row.get("final_state") == PENDING_SETTLED_FINAL_STATE)
    # Which of the two closed settlement shapes was actually accepted, per target.
    # A count, never a value: this is how a reader tells "Google settled the status
    # alone" from "Google settled it with its stamp and content hash".
    shapes = {shape: sum(1 for row in rows if isinstance(row, dict)
                         and row.get(SETTLEMENT_SHAPE_FIELD) == shape)
              for shape in SETTLEMENT_SHAPES}
    wc.append_receipt(
        "woocommerce_shipping_policy_committed",
        f"action={action}; plan={plan_path}; sha256={plan['sha256']}; "
        f"convergence_used_targets={len(converged)}; "
        f"convergence_meta_key={GLA_META_KEY}; "
        f"final_state={CONVERGENCE_FINAL_REQUIREMENT}; "
        + "; ".join(f"baseline_{mode}_targets={count}" for mode, count in modes.items())
        + f"; pending_settled_confirmed_targets={settled_confirmed}; "
        + "; ".join(f"settlement_{shape}_targets={count}"
                    for shape, count in shapes.items()),
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
            "baseline_modes": modes,
            "pending_final_requirement": PENDING_FINAL_REQUIREMENT,
            "pending_settle_confirm_max_seconds": PENDING_SETTLE_CONFIRM_MAX_SECONDS,
            "pending_settled_confirmed_targets": settled_confirmed,
            "settlement_shapes": shapes,
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
