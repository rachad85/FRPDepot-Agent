#!/usr/bin/env python
"""FRP Depot packing-experience collector (READ-ONLY WooCommerce).

Commissioned by Rachad Homsi on 2026-08-10: keep using the researched packing
estimates for now, and start collecting real packing data from future orders so
the estimates improve with experience.

WHAT THIS TOOL IS ALLOWED TO DO
-------------------------------
* GET WooCommerce orders through the existing shared transport, and nothing
  else. There is no POST, PUT, PATCH, DELETE, batch, stage, plan or approval
  route anywhere in this file, and no WordPress, Zoho, Drive, email, UPS or
  guard path. ``wc.api_get`` and ``wc.get_all`` are the only transport
  functions imported behaviour depends on.
* Append local queue entries describing WHICH future orders are worth measuring.
* Append physical measurements that RACHAD reports in his own words, through an
  explicit ``record`` / ``correct`` command. A monitor can never do this.
* Produce recommendations. It never rewrites the researched estimate, and every
  derived report carries REVIEW ONLY - NO WOO/UPS/GUARD CHANGE.

PRIVACY: WHY THE PROJECTION IS POSITIVE
---------------------------------------
Following the audit-tool precedent, orders are requested with a fixed positive
``_fields`` projection so customer names, emails, phones, addresses, notes,
payment details, IPs, order keys, metadata and every money field stay at the
store. That request-side projection is a request, not a guarantee: a plugin, a
cached response or a future WooCommerce release can return more than was asked
for. So every response is positively projected AGAIN in this process, before it
is read or stored, and only the projected copy is ever used. Nothing outside
ALLOWED_ORDER_FIELDS / ALLOWED_LINE_FIELDS can reach state, stdout, receipts or
the local data files.

Operational data lives OUTSIDE the Git repository, under
``%LOCALAPPDATA%\\FRPDepot-Packing-Observations\\``, so order history and packing
observations never enter the repo, the nightly conduct bundle or the GitHub push.

The first initialization is FUTURE-ONLY on purpose: it records the newest order
that exists right now as a baseline and queues nothing behind it. Past orders
cannot be measured any more, so queuing them would only produce a backlog of
work nobody can do.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable
import uuid

import woocommerce_common as wc

# --------------------------------------------------------------------------
# Fixed identities
# --------------------------------------------------------------------------

REPO_ROOT = Path(r"C:\FRPDepot")
CATALOG_PATH = (REPO_ROOT / "Dado" / "20_Working" / "catalog_shipping_policy"
                / "packing_measurement_estimates_researched.csv")

# The exact researched-estimate revision this tool was commissioned against.
# A different file is REFUSED rather than silently adopted: an opportunity
# captured under one estimate revision must not be compared against another one
# that nobody reviewed. Changing revision is a deliberate source edit.
ESTIMATE_CSV_SHA256 = "fa394c7e9513fcde8066752b7e89cae6149dfd7c88483ffe2aae47e492a19b72"
EXPECTED_GROUP_COUNT = 37
EXPECTED_VARIATION_COUNT = 78

SCHEMA_VERSION = 1
RECORDED_BY = "Rachad Homsi"
REVIEW_ONLY_BANNER = "REVIEW ONLY - NO WOO/UPS/GUARD CHANGE"

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))

# Module-level so the tests can redirect the whole operational root to a
# temporary directory. Every path below is derived from data_root() at call
# time, never captured at import time.
DATA_ROOT = LOCALAPPDATA / "FRPDepot-Packing-Observations"

# --------------------------------------------------------------------------
# Order projection
# --------------------------------------------------------------------------

ALLOWED_ORDER_FIELDS = ("id", "number", "date_created_gmt", "date_modified_gmt", "status")
ALLOWED_LINE_FIELDS = ("id", "product_id", "variation_id", "quantity", "sku")
ORDER_PROJECTION = ",".join(
    list(ALLOWED_ORDER_FIELDS) + ["line_items." + name for name in ALLOWED_LINE_FIELDS]
)

# Not used to filter -- the positive projection above already does that. These
# exist so `validate` and the tests can state the property in the other
# direction: if any of these ever appears in a stored record, something is
# badly wrong and the run must fail loudly.
FORBIDDEN_ORDER_KEYS = frozenset({
    "billing", "shipping", "customer_id", "customer_note", "customer_ip_address",
    "customer_user_agent", "payment_method", "payment_method_title", "transaction_id",
    "order_key", "cart_hash", "total", "subtotal", "total_tax", "shipping_total",
    "shipping_tax", "discount_total", "discount_tax", "cart_tax", "currency",
    "currency_symbol", "prices_include_tax", "coupon_lines", "fee_lines",
    "shipping_lines", "tax_lines", "refunds", "meta_data", "date_paid",
    "date_completed", "set_paid", "created_via", "customer", "email", "phone",
    "first_name", "last_name", "address_1", "address_2", "postcode", "price",
})
FORBIDDEN_LINE_KEYS = frozenset({
    "name", "price", "total", "subtotal", "total_tax", "subtotal_tax", "taxes",
    "meta_data", "tax_class", "parent_name", "image", "bundled_by",
})

FULFILLMENT_STATUSES = frozenset({"processing", "on-hold", "completed"})
TERMINAL_STATUSES = frozenset({"cancelled", "refunded", "failed", "trash"})

SCAN_MAX_PAGES = 10
SCAN_MAX_RECORDS = 1000
SCAN_PER_PAGE = 100

MAX_DIMENSION_CM = Decimal("1000")
MAX_WEIGHT_KG = Decimal("2000")

# Evidence references and notes are free text that Rachad types. They must never
# become a place a credential lands, so anything that looks like one is refused
# rather than scrubbed -- a scrub would silently keep a half-secret.
SECRET_PATTERNS = (
    re.compile(r"(?i)\bck_"),
    re.compile(r"(?i)\bcs_"),
    re.compile(r"(?i)bearer\s"),
    re.compile(r"(?i)password"),
    re.compile(r"(?i)token"),
    re.compile(r"(?i)secret"),
    re.compile(r"(?i)api[_\- ]?key"),
    # A long unbroken alphanumeric run is what a key looks like. Separators are
    # deliberately NOT part of the run: a real evidence reference such as
    # "packing-list-2026-08-10-order-1471" is long but not key-like, and
    # refusing it would push Rachad towards vaguer references.
    re.compile(r"[A-Za-z0-9]{32,}"),
)

CONFIDENCE_NO_DATA = "NO PHYSICAL DATA"
CONFIDENCE_LOW = "EMPIRICAL LOW"
CONFIDENCE_LOW_MEDIUM = "EMPIRICAL LOW-MEDIUM"
CONFIDENCE_MEDIUM = "EMPIRICAL MEDIUM"
CONFIDENCE_MEDIUM_HIGH = "EMPIRICAL MEDIUM-HIGH"

STATUS_COLLECT = "COLLECT MORE DATA"
STATUS_SUPPORTED = "ESTIMATE SUPPORTED - REVIEW ONLY"
STATUS_REVISE = "ESTIMATE REVISION RECOMMENDED - REVIEW ONLY"

REVIEW_THRESHOLD_ORDERS = 3


class PackingError(RuntimeError):
    """A packing-collector failure. Carries a stable short signature for dedupe."""

    def __init__(self, message: str, signature: str | None = None):
        super().__init__(message)
        self.signature = signature or message.split(":", 1)[0].strip()[:120]


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def data_root() -> Path:
    return Path(DATA_ROOT)


def state_path() -> Path:
    return data_root() / "monitor_state.json"


def opportunities_path() -> Path:
    return data_root() / "opportunities.jsonl"


def events_path() -> Path:
    return data_root() / "measurement_events.jsonl"


def observations_csv_path() -> Path:
    return data_root() / "current_observations.csv"


def group_report_csv_path() -> Path:
    return data_root() / "group_experience_report.csv"


def group_report_md_path() -> Path:
    return data_root() / "group_experience_report.md"


def error_flag_path() -> Path:
    return data_root() / "error_alerted.flag"


def weekly_state_path() -> Path:
    return data_root() / "weekly_alert_state.json"


def lock_path() -> Path:
    return data_root() / "packing_observations.lock"


def ensure_data_root() -> Path:
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


# --------------------------------------------------------------------------
# File lock (safe for an overlapping cron run and a manual run)
# --------------------------------------------------------------------------

_LOCK_DEPTH = 0


def _acquire_os_lock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover - the server is Windows
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_os_lock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover - the server is Windows
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def data_lock(timeout: float = 30.0):
    """Exclusive lock over the operational data root.

    Re-entrant within one process: ``record`` takes the lock and then rebuilds
    the derived views, which want it too.
    """
    global _LOCK_DEPTH
    if _LOCK_DEPTH:
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
        return
    ensure_data_root()
    handle = open(lock_path(), "a+b")
    deadline = time.monotonic() + timeout
    while True:
        try:
            _acquire_os_lock(handle)
            break
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise PackingError(
                    "Packing data lock is held by another run; nothing was changed.",
                    signature="data_lock_busy",
                )
            time.sleep(0.1)
    _LOCK_DEPTH = 1
    try:
        yield
    finally:
        _LOCK_DEPTH = 0
        try:
            _release_os_lock(handle)
        finally:
            handle.close()


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime | None = None) -> str:
    return (moment or utc_now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_gmt(value: Any) -> datetime | None:
    """Parse a WooCommerce GMT timestamp. Woo omits the zone; it is always UTC."""
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def woo_timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def dec_str(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.001"))
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def to_decimal(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise PackingError(f"{label} is not a number: {value!r}") from exc
    if not result.is_finite():
        raise PackingError(f"{label} is not a finite number.")
    return result


def positive_decimal(value: Any, label: str, maximum: Decimal) -> Decimal:
    result = to_decimal(value, label)
    if result <= 0:
        raise PackingError(f"{label} must be greater than zero (got {dec_str(result)}).")
    if result > maximum:
        raise PackingError(
            f"{label} must be at most {dec_str(maximum)} (got {dec_str(result)})."
        )
    return result


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temporary, path)


def append_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
            written += 1
    return written


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise PackingError(f"{path.name} line {number} is not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise PackingError(f"{path.name} line {number} is not a JSON object.")
        records.append(parsed)
    return records


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def contains_secret(text: str) -> str | None:
    candidate = str(text or "")
    for pattern in SECRET_PATTERNS:
        if pattern.search(candidate):
            return pattern.pattern
    return None


def reject_secrets(text: str, label: str) -> str:
    hit = contains_secret(text)
    if hit:
        raise PackingError(
            f"{label} looks like it contains a credential or key and was refused. "
            "Send a document or photo reference instead."
        )
    return text


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------

CATALOG_FIELDS = (
    "group_id", "family", "size_in", "pressure_psi", "skus", "variation_ids",
    "researched_packed_length_cm", "researched_packed_width_cm",
    "researched_packed_height_cm", "researched_gross_weight_kg",
    "researched_packing_material", "overall_confidence", "verification_status",
)


class Catalog:
    """The researched packing estimates, indexed for order matching.

    Only the short operational columns are loaded. The long dimension_basis /
    weight_basis narratives stay in the CSV: copying paragraphs of prose into
    every queued order line would bloat the append-only queue for no gain.
    """

    def __init__(self, path: Path, sha256: str, groups: dict[str, dict[str, Any]],
                 by_variation: dict[int, dict[str, Any]], by_sku: dict[str, dict[str, Any]]):
        self.path = path
        self.sha256 = sha256
        self.groups = groups
        self.by_variation = by_variation
        self.by_sku = by_sku

    @property
    def group_count(self) -> int:
        return len(self.groups)

    @property
    def variation_count(self) -> int:
        return len(self.by_variation)

    def target_for_variation(self, variation_id: int) -> dict[str, Any] | None:
        return self.by_variation.get(int(variation_id))

    def estimate_snapshot(self, group_id: str) -> dict[str, str]:
        group = self.groups[group_id]
        return {
            "packed_length_cm": group["packed_length_cm"],
            "packed_width_cm": group["packed_width_cm"],
            "packed_height_cm": group["packed_height_cm"],
            "gross_weight_kg": group["gross_weight_kg"],
            "packing_material": group["packing_material"],
            "overall_confidence": group["overall_confidence"],
            "verification_status": group["verification_status"],
        }


def load_catalog(path: Path | None = None, expected_sha256: str | None = None) -> Catalog:
    source = Path(path or CATALOG_PATH)
    if not source.exists():
        raise PackingError(f"Researched packing estimates are missing: {source}")
    digest = sha256_file(source)
    expected = expected_sha256 if expected_sha256 is not None else ESTIMATE_CSV_SHA256
    if digest != expected:
        raise PackingError(
            "Researched packing estimates do not match the commissioned revision "
            f"(expected {expected[:16]}..., found {digest[:16]}...). A new estimate "
            "revision is never adopted silently; update this tool deliberately.",
            signature="estimate_revision_mismatch",
        )
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise PackingError("Researched packing estimates are empty.")
    missing = [name for name in CATALOG_FIELDS if name not in rows[0]]
    if missing:
        raise PackingError("Researched packing estimates are missing columns: " + ", ".join(missing))

    groups: dict[str, dict[str, Any]] = {}
    by_variation: dict[int, dict[str, Any]] = {}
    by_sku: dict[str, dict[str, Any]] = {}
    for row in rows:
        group_id = str(row["group_id"] or "").strip()
        if not group_id:
            raise PackingError("Researched packing estimates contain a blank group_id.")
        if group_id in groups:
            raise PackingError(f"Researched packing estimates repeat group {group_id}.")
        skus = [part.strip() for part in str(row["skus"] or "").split("|")]
        variation_text = [part.strip() for part in str(row["variation_ids"] or "").split("|")]
        if len(skus) != len(variation_text):
            raise PackingError(
                f"Group {group_id} lists {len(variation_text)} variation ids for {len(skus)} SKUs."
            )
        estimate = {
            "packed_length_cm": dec_str(positive_decimal(
                row["researched_packed_length_cm"], f"{group_id} researched length", MAX_DIMENSION_CM)),
            "packed_width_cm": dec_str(positive_decimal(
                row["researched_packed_width_cm"], f"{group_id} researched width", MAX_DIMENSION_CM)),
            "packed_height_cm": dec_str(positive_decimal(
                row["researched_packed_height_cm"], f"{group_id} researched height", MAX_DIMENSION_CM)),
            "gross_weight_kg": dec_str(positive_decimal(
                row["researched_gross_weight_kg"], f"{group_id} researched gross weight", MAX_WEIGHT_KG)),
            "packing_material": str(row["researched_packing_material"] or "").strip(),
            "overall_confidence": str(row["overall_confidence"] or "").strip(),
            "verification_status": str(row["verification_status"] or "").strip(),
        }
        if not estimate["packing_material"]:
            raise PackingError(f"Group {group_id} has no researched packing material.")
        if not estimate["verification_status"]:
            raise PackingError(f"Group {group_id} has no verification status.")
        group = {
            "group_id": group_id,
            "family": str(row["family"] or "").strip(),
            "size_in": str(row["size_in"] or "").strip(),
            "pressure_psi": str(row["pressure_psi"] or "").strip(),
            "variation_ids": [],
            "skus": [],
            "estimate_csv_sha256": digest,
            **estimate,
        }
        groups[group_id] = group
        for raw_variation, raw_sku in zip(variation_text, skus):
            if not raw_sku:
                raise PackingError(f"Group {group_id} has a blank SKU.")
            try:
                variation_id = int(raw_variation)
            except ValueError as exc:
                raise PackingError(
                    f"Group {group_id} has a non-numeric variation id {raw_variation!r}."
                ) from exc
            if variation_id <= 0:
                raise PackingError(f"Group {group_id} has a non-positive variation id.")
            if variation_id in by_variation:
                raise PackingError(f"Variation {variation_id} appears in more than one group.")
            if raw_sku in by_sku:
                raise PackingError(f"SKU {raw_sku} appears in more than one group.")
            target = {
                "group_id": group_id,
                "family": group["family"],
                "size_in": group["size_in"],
                "pressure_psi": group["pressure_psi"],
                "variation_id": variation_id,
                "sku": raw_sku,
                "estimate_csv_sha256": digest,
                **estimate,
            }
            by_variation[variation_id] = target
            by_sku[raw_sku] = target
            group["variation_ids"].append(variation_id)
            group["skus"].append(raw_sku)

    if len(groups) != EXPECTED_GROUP_COUNT:
        raise PackingError(
            f"Researched packing estimates hold {len(groups)} groups; {EXPECTED_GROUP_COUNT} expected."
        )
    if len(by_variation) != EXPECTED_VARIATION_COUNT or len(by_sku) != EXPECTED_VARIATION_COUNT:
        raise PackingError(
            f"Researched packing estimates hold {len(by_variation)} variations and "
            f"{len(by_sku)} SKUs; {EXPECTED_VARIATION_COUNT} of each expected."
        )
    return Catalog(source, digest, groups, by_variation, by_sku)


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

def project_line_item(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    return {
        "id": _as_int(source.get("id")),
        "product_id": _as_int(source.get("product_id")),
        "variation_id": _as_int(source.get("variation_id")),
        "quantity": source.get("quantity"),
        "sku": _as_text(source.get("sku")),
    }


def project_order(raw: Any) -> dict[str, Any]:
    """Rebuild an order from scratch using only the allowed fields.

    Built positively -- a new dict with exactly the allowed keys -- rather than
    by deleting forbidden ones, because a deny-list can only remove the fields
    somebody thought of in advance.
    """
    source = raw if isinstance(raw, dict) else {}
    lines = source.get("line_items")
    return {
        "id": _as_int(source.get("id")),
        "number": _as_text(source.get("number")),
        "date_created_gmt": _as_text(source.get("date_created_gmt")),
        "date_modified_gmt": _as_text(source.get("date_modified_gmt")),
        "status": _as_text(source.get("status")).lower(),
        "line_items": [project_line_item(line) for line in lines] if isinstance(lines, list) else [],
    }


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def find_forbidden_keys(payload: Any, *, line_context: bool = False) -> list[str]:
    """Report any forbidden key found anywhere in a structure. Used by validate."""
    found: list[str] = []

    def walk(node: Any, in_line: bool) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                if lowered in FORBIDDEN_ORDER_KEYS or (in_line and lowered in FORBIDDEN_LINE_KEYS):
                    found.append(lowered)
                walk(value, in_line or lowered == "line_items")
        elif isinstance(node, list):
            for item in node:
                walk(item, in_line)

    walk(payload, line_context)
    return sorted(set(found))


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def load_state() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        raise PackingError(
            "Packing observation is not initialized. Run: packing_observation_tool.py initialize",
            signature="not_initialized",
        )
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackingError("monitor_state.json is unreadable.", signature="state_unreadable") from exc
    if not isinstance(state, dict) or int(state.get("schema") or 0) != SCHEMA_VERSION:
        raise PackingError("monitor_state.json has an unexpected schema.", signature="state_schema")
    return state


def save_state(state: dict[str, Any]) -> None:
    atomic_write_text(state_path(), json.dumps(state, ensure_ascii=True, indent=2, sort_keys=True) + "\n")


def opportunity_id_for(site: str, order_id: int, line_item_id: int, variation_id: int) -> str:
    seed = f"{site}|{int(order_id)}|{int(line_item_id)}|{int(variation_id)}"
    return "OPP-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


# --------------------------------------------------------------------------
# initialize
# --------------------------------------------------------------------------

def run_initialize(vault: dict[str, Any] | None = None,
                   catalog: Catalog | None = None) -> dict[str, Any]:
    if state_path().exists():
        raise PackingError(
            "Packing observation is already initialized; the baseline is never moved.",
            signature="already_initialized",
        )
    catalog = catalog or load_catalog()
    vault = vault or wc.load_vault()
    site = str(vault["site_url"])
    data, _headers = wc.api_get(
        "/orders",
        {"status": "any", "per_page": 1, "orderby": "id", "order": "desc",
         "_fields": ORDER_PROJECTION},
        vault,
    )
    orders = [project_order(item) for item in data] if isinstance(data, list) else []
    now = utc_now()
    if orders and orders[0]["id"]:
        latest = orders[0]
        baseline_id = int(latest["id"])
        created = parse_gmt(latest["date_created_gmt"]) or now
        baseline_created = woo_timestamp(created)
    else:
        baseline_id = 0
        baseline_created = woo_timestamp(now)

    state = {
        "schema": SCHEMA_VERSION,
        "site": site,
        "initialized_utc": iso_utc(now),
        "future_only_baseline": True,
        "baseline_order_id": baseline_id,
        "baseline_order_date_created_gmt": baseline_created,
        "cursor_date_created_gmt": baseline_created,
        "high_water_order_id": baseline_id,
        "pending_orders": {},
        "last_scan_utc": None,
        "scan_count": 0,
        "estimate_csv_sha256": catalog.sha256,
    }
    with data_lock():
        if state_path().exists():
            raise PackingError(
                "Packing observation is already initialized; the baseline is never moved.",
                signature="already_initialized",
            )
        save_state(state)
        rebuild_derived_views(catalog=catalog)
    wc.append_receipt(
        "packing_observation_initialized",
        f"baseline_order_id={baseline_id} groups={catalog.group_count} "
        f"variations={catalog.variation_count} root={data_root()}",
    )
    return {
        "status": "INITIALIZED",
        "site": site,
        "baseline_order_id": baseline_id,
        "baseline_order_date_created_gmt": baseline_created,
        "queued_opportunities": 0,
        "future_only": True,
        "groups": catalog.group_count,
        "variations": catalog.variation_count,
        "estimate_csv_sha256": catalog.sha256,
        "data_root": str(data_root()),
    }


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------

def _relevant_lines(order: dict[str, Any], catalog: Catalog) -> list[dict[str, Any]]:
    """Match order lines to packing targets, or fail loudly.

    Variation id is the identity. The SKU is then required to match exactly,
    because a target variation whose SKU has moved means the catalog and the
    store disagree about what that variation IS -- and an observation filed
    against the wrong product is worse than no observation. This runs in the
    read phase, before any local mutation.
    """
    matched: list[dict[str, Any]] = []
    for line in order["line_items"]:
        variation_id = line["variation_id"]
        sku = line["sku"]
        target = catalog.target_for_variation(variation_id) if variation_id else None
        if target is None:
            if sku and sku in catalog.by_sku:
                other = catalog.by_sku[sku]
                raise PackingError(
                    f"Order {order['id']} line {line['id']} carries packing SKU {sku} on "
                    f"variation {variation_id}, but the catalog binds that SKU to variation "
                    f"{other['variation_id']}. Nothing was recorded.",
                    signature="sku_identity_conflict",
                )
            continue
        if sku != target["sku"]:
            raise PackingError(
                f"Order {order['id']} line {line['id']} is packing variation "
                f"{variation_id} but its SKU is {sku or '(missing)'} where the catalog "
                f"says {target['sku']}. Nothing was recorded.",
                signature="sku_mismatch",
            )
        quantity = to_decimal(line["quantity"], f"Order {order['id']} line {line['id']} quantity")
        if quantity <= 0:
            continue
        matched.append({"line": line, "target": target, "quantity": quantity})
    return matched


def _build_opportunity(site: str, order: dict[str, Any], match: dict[str, Any],
                       captured: str) -> dict[str, Any]:
    line = match["line"]
    target = match["target"]
    return {
        "schema": SCHEMA_VERSION,
        "opportunity_id": opportunity_id_for(site, order["id"], line["id"], target["variation_id"]),
        "site": site,
        "order_id": int(order["id"]),
        "order_number": order["number"],
        "order_date_created_gmt": order["date_created_gmt"],
        "order_status_at_capture": order["status"],
        "line_item_id": int(line["id"]),
        "product_id": line["product_id"],
        "variation_id": int(target["variation_id"]),
        "sku": target["sku"],
        "ordered_quantity": dec_str(match["quantity"]),
        "group_id": target["group_id"],
        "family": target["family"],
        "size_in": target["size_in"],
        "pressure_psi": target["pressure_psi"],
        "estimate": {
            "packed_length_cm": target["packed_length_cm"],
            "packed_width_cm": target["packed_width_cm"],
            "packed_height_cm": target["packed_height_cm"],
            "gross_weight_kg": target["gross_weight_kg"],
            "packing_material": target["packing_material"],
            "overall_confidence": target["overall_confidence"],
            "verification_status": target["verification_status"],
        },
        "estimate_csv_sha256": target["estimate_csv_sha256"],
        "captured_utc": captured,
    }


def run_scan(vault: dict[str, Any] | None = None,
             catalog: Catalog | None = None) -> dict[str, Any]:
    """One read-only sweep for new packing opportunities.

    Every network read and every consistency check happens BEFORE the local
    mutation block, so a failure anywhere leaves the queue, the pending list and
    the cursor exactly as they were.
    """
    catalog = catalog or load_catalog()
    state = load_state()
    vault = vault or wc.load_vault()
    site = str(vault["site_url"])
    if site != str(state.get("site") or site):
        raise PackingError(
            "The connected WooCommerce site differs from the initialized baseline site.",
            signature="site_changed",
        )

    high_water = int(state.get("high_water_order_id") or 0)
    cursor = parse_gmt(state.get("cursor_date_created_gmt")) or utc_now()
    pending_before: dict[str, Any] = dict(state.get("pending_orders") or {})

    orders: dict[int, dict[str, Any]] = {}
    orders_read = 0
    pending_rechecked = 0
    pending_gone: list[int] = []

    # 1. Re-check every pending candidate by id.
    for key in sorted(pending_before, key=lambda item: int(item)):
        order_id = int(key)
        try:
            data, _headers = wc.api_get(
                f"/orders/{order_id}", {"_fields": ORDER_PROJECTION}, vault
            )
        except wc.WooError as exc:
            if exc.status == 404:
                pending_gone.append(order_id)
                continue
            raise
        projected = project_order(data)
        if projected["id"] != order_id:
            raise PackingError(
                f"WooCommerce returned order {projected['id']} for a request for {order_id}.",
                signature="order_identity_mismatch",
            )
        orders[order_id] = projected
        orders_read += 1
        pending_rechecked += 1

    # 2. Fetch everything created since the cursor. One second of overlap covers
    #    sub-second truncation at the store; the id high-water below removes the
    #    duplicates that overlap produces.
    after = woo_timestamp(cursor - timedelta(seconds=1))
    fetched = wc.get_all(
        "/orders",
        {"status": "any", "per_page": SCAN_PER_PAGE, "orderby": "id", "order": "asc",
         "after": after, "_fields": ORDER_PROJECTION},
        vault=vault, max_pages=SCAN_MAX_PAGES, max_items=SCAN_MAX_RECORDS,
    )
    new_high_water = high_water
    new_cursor = cursor
    for raw in fetched:
        projected = project_order(raw)
        orders_read += 1
        order_id = projected["id"]
        if not order_id or order_id <= high_water:
            continue
        created = parse_gmt(projected["date_created_gmt"])
        if created and created > new_cursor:
            new_cursor = created
        new_high_water = max(new_high_water, order_id)
        orders[order_id] = projected

    # 3. Classify. Any failure here happens before the mutation block.
    captured = iso_utc()
    candidate_opportunities: list[dict[str, Any]] = []
    pending_after: dict[str, Any] = {}
    terminal_orders: list[int] = []
    for order_id in sorted(orders):
        order = orders[order_id]
        matches = _relevant_lines(order, catalog)
        status = order["status"]
        if not matches:
            continue
        if status in FULFILLMENT_STATUSES:
            for match in matches:
                candidate_opportunities.append(_build_opportunity(site, order, match, captured))
            continue
        if status in TERMINAL_STATUSES:
            terminal_orders.append(order_id)
            continue
        previous = pending_before.get(str(order_id)) or {}
        pending_after[str(order_id)] = {
            "order_id": order_id,
            "status": status,
            "first_seen_utc": previous.get("first_seen_utc") or captured,
            "last_checked_utc": captured,
        }

    # 4. Local mutation, under the lock, all at once.
    with data_lock():
        existing_ids = {
            str(record.get("opportunity_id"))
            for record in read_jsonl(opportunities_path())
        }
        fresh = [
            record for record in candidate_opportunities
            if record["opportunity_id"] not in existing_ids
        ]
        # Guard against two identical lines inside one order producing the same id.
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for record in fresh:
            if record["opportunity_id"] in seen:
                continue
            seen.add(record["opportunity_id"])
            deduped.append(record)
        if deduped:
            append_jsonl(opportunities_path(), deduped)

        current = load_state()
        current["pending_orders"] = pending_after
        current["cursor_date_created_gmt"] = woo_timestamp(new_cursor)
        current["high_water_order_id"] = new_high_water
        current["last_scan_utc"] = captured
        current["scan_count"] = int(current.get("scan_count") or 0) + 1
        current["estimate_csv_sha256"] = catalog.sha256
        save_state(current)
        rebuild_derived_views(catalog=catalog)

    if deduped:
        wc.append_receipt(
            "packing_observation_opportunities_queued",
            f"new={len(deduped)} orders_read={orders_read} pending={len(pending_after)} "
            f"path={opportunities_path()}",
        )

    return {
        "status": "OK",
        "scanned_utc": captured,
        "orders_read": orders_read,
        "pending_rechecked": pending_rechecked,
        "pending_removed_gone": pending_gone,
        "terminal_orders": terminal_orders,
        "new_opportunity_count": len(deduped),
        "new_opportunities": [
            {
                "opportunity_id": record["opportunity_id"],
                "order_id": record["order_id"],
                "order_number": record["order_number"],
                "sku": record["sku"],
                "quantity": record["ordered_quantity"],
                "group_id": record["group_id"],
            }
            for record in deduped
        ],
        "pending_count": len(pending_after),
        "cursor_date_created_gmt": woo_timestamp(new_cursor),
        "high_water_order_id": new_high_water,
        "data_root": str(data_root()),
    }


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

def load_opportunities() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in read_jsonl(opportunities_path()):
        identifier = str(record.get("opportunity_id") or "")
        if not identifier:
            raise PackingError("opportunities.jsonl holds a record with no opportunity_id.")
        result[identifier] = record
    return result


def load_events() -> list[dict[str, Any]]:
    return read_jsonl(events_path())


def active_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Active = never superseded by a later event."""
    materialized = list(events)
    superseded = {
        str(record.get("supersedes_event_id"))
        for record in materialized
        if record.get("supersedes_event_id")
    }
    return [record for record in materialized if str(record.get("event_id")) not in superseded]


def events_for(opportunity_id: str, events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in events if str(record.get("opportunity_id")) == opportunity_id]


def _measurement_payload(args: argparse.Namespace, opportunity: dict[str, Any],
                         event_type: str, supersedes: str | None) -> dict[str, Any]:
    package_number = int(args.package_number)
    if package_number <= 0:
        raise PackingError("--package-number must be a positive whole number.")
    packed_quantity = positive_decimal(args.packed_quantity, "--packed-quantity", MAX_WEIGHT_KG)
    dimensions = sorted(
        (
            positive_decimal(args.length_cm, "--length-cm", MAX_DIMENSION_CM),
            positive_decimal(args.width_cm, "--width-cm", MAX_DIMENSION_CM),
            positive_decimal(args.height_cm, "--height-cm", MAX_DIMENSION_CM),
        ),
        reverse=True,
    )
    weight = positive_decimal(args.gross_weight_kg, "--gross-weight-kg", MAX_WEIGHT_KG)
    material = str(args.packing_material or "").strip()
    if not material:
        raise PackingError("--packing-material cannot be blank.")
    evidence = str(args.evidence_ref or "").strip()
    if not evidence:
        raise PackingError("--evidence-ref cannot be blank.")
    reject_secrets(material, "--packing-material")
    reject_secrets(evidence, "--evidence-ref")
    notes = str(getattr(args, "notes", "") or "").strip()
    if notes:
        reject_secrets(notes, "--notes")
    return {
        "schema": SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "recorded_utc": iso_utc(),
        "recorded_by": RECORDED_BY,
        "opportunity_id": str(opportunity["opportunity_id"]),
        "order_id": int(opportunity["order_id"]),
        "group_id": str(opportunity["group_id"]),
        "variation_id": int(opportunity["variation_id"]),
        "sku": str(opportunity["sku"]),
        "package_number": package_number,
        "packed_quantity": dec_str(packed_quantity),
        "length_cm": dec_str(dimensions[0]),
        "width_cm": dec_str(dimensions[1]),
        "height_cm": dec_str(dimensions[2]),
        "gross_weight_kg": dec_str(weight),
        "packing_material": material,
        "evidence_ref": evidence,
        "notes": notes,
        "supersedes_event_id": supersedes,
        "estimate_csv_sha256": str(opportunity.get("estimate_csv_sha256") or ""),
        "estimate_snapshot": dict(opportunity.get("estimate") or {}),
    }


def _check_quantity_and_package(event: dict[str, Any], opportunity: dict[str, Any],
                                existing: list[dict[str, Any]]) -> None:
    """Apply the new event's supersession, then re-check the whole opportunity."""
    projected = list(existing) + [event]
    live = active_events(projected)
    mine = events_for(event["opportunity_id"], live)
    numbers = [int(record["package_number"]) for record in mine]
    if len(numbers) != len(set(numbers)):
        raise PackingError(
            f"Package {event['package_number']} already has an active measurement for "
            f"{event['opportunity_id']}. Use `correct --supersedes-event-id` to replace it."
        )
    total = sum((Decimal(str(record["packed_quantity"])) for record in mine), Decimal("0"))
    ordered = Decimal(str(opportunity["ordered_quantity"]))
    if total > ordered:
        raise PackingError(
            f"Packed quantity {dec_str(total)} exceeds the {dec_str(ordered)} ordered on "
            f"{event['opportunity_id']}. Nothing was recorded."
        )


def run_record(args: argparse.Namespace) -> dict[str, Any]:
    catalog = load_catalog()
    with data_lock():
        opportunities = load_opportunities()
        identifier = str(args.opportunity_id or "").strip()
        opportunity = opportunities.get(identifier)
        if opportunity is None:
            raise PackingError(f"Unknown opportunity {identifier or '(blank)'}.")
        existing = load_events()
        event = _measurement_payload(args, opportunity, "measurement", None)
        _check_quantity_and_package(event, opportunity, existing)
        append_jsonl(events_path(), [event])
        rebuild_derived_views(catalog=catalog)
    wc.append_receipt(
        "packing_observation_measurement_recorded",
        f"opportunity={event['opportunity_id']} package={event['package_number']} "
        f"event={event['event_id']} path={events_path()}",
    )
    return {"status": "RECORDED", "event_id": event["event_id"],
            "opportunity_id": event["opportunity_id"],
            "package_number": event["package_number"],
            "length_cm": event["length_cm"], "width_cm": event["width_cm"],
            "height_cm": event["height_cm"], "gross_weight_kg": event["gross_weight_kg"]}


def run_correct(args: argparse.Namespace) -> dict[str, Any]:
    catalog = load_catalog()
    with data_lock():
        opportunities = load_opportunities()
        identifier = str(args.opportunity_id or "").strip()
        opportunity = opportunities.get(identifier)
        if opportunity is None:
            raise PackingError(f"Unknown opportunity {identifier or '(blank)'}.")
        existing = load_events()
        target_id = str(args.supersedes_event_id or "").strip()
        original = next((r for r in existing if str(r.get("event_id")) == target_id), None)
        if original is None:
            raise PackingError(f"Unknown event {target_id or '(blank)'}; nothing was corrected.")
        if str(original.get("opportunity_id")) != identifier:
            raise PackingError(
                f"Event {target_id} belongs to {original.get('opportunity_id')}, not {identifier}."
            )
        if original not in active_events(existing):
            raise PackingError(f"Event {target_id} has already been superseded.")
        if int(original.get("package_number") or 0) != int(args.package_number):
            raise PackingError(
                f"Event {target_id} is package {original.get('package_number')}, not "
                f"{args.package_number}. A correction stays on the same package."
            )
        event = _measurement_payload(args, opportunity, "correction", target_id)
        _check_quantity_and_package(event, opportunity, existing)
        append_jsonl(events_path(), [event])
        rebuild_derived_views(catalog=catalog)
    wc.append_receipt(
        "packing_observation_measurement_corrected",
        f"opportunity={event['opportunity_id']} package={event['package_number']} "
        f"event={event['event_id']} supersedes={target_id} path={events_path()}",
    )
    return {"status": "CORRECTED", "event_id": event["event_id"],
            "supersedes_event_id": target_id,
            "opportunity_id": event["opportunity_id"],
            "package_number": event["package_number"]}


# --------------------------------------------------------------------------
# Derived experience
# --------------------------------------------------------------------------

def round_up_to(value: Decimal, step: Decimal) -> Decimal:
    if value <= 0:
        return Decimal("0")
    multiples = (value / step).to_integral_value(rounding="ROUND_CEILING")
    return (multiples * step).quantize(Decimal("0.001"))


def confidence_for(distinct_single_orders: int) -> str:
    if distinct_single_orders <= 0:
        return CONFIDENCE_NO_DATA
    if distinct_single_orders == 1:
        return CONFIDENCE_LOW
    if distinct_single_orders == 2:
        return CONFIDENCE_LOW_MEDIUM
    if distinct_single_orders <= 4:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_MEDIUM_HIGH


def coverage(opportunity: dict[str, Any], events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    mine = events_for(str(opportunity["opportunity_id"]), events)
    packed = sum((Decimal(str(record["packed_quantity"])) for record in mine), Decimal("0"))
    ordered = Decimal(str(opportunity["ordered_quantity"]))
    return {
        "packages": len(mine),
        "packed_quantity": packed,
        "ordered_quantity": ordered,
        "remaining_quantity": ordered - packed,
        "complete": packed >= ordered,
    }


def build_experience(catalog: Catalog) -> dict[str, Any]:
    """Deterministic derived view over the append-only files. Reads only."""
    opportunities = load_opportunities()
    live = active_events(load_events())
    by_opportunity = {identifier: [] for identifier in opportunities}
    for record in live:
        identifier = str(record.get("opportunity_id"))
        by_opportunity.setdefault(identifier, []).append(record)

    observations: list[dict[str, Any]] = []
    for identifier in sorted(opportunities):
        opportunity = opportunities[identifier]
        for event in sorted(by_opportunity.get(identifier, []),
                            key=lambda item: int(item["package_number"])):
            packed = Decimal(str(event["packed_quantity"]))
            observations.append({
                "group_id": str(opportunity["group_id"]),
                "order_id": int(opportunity["order_id"]),
                "order_number": str(opportunity.get("order_number") or ""),
                "opportunity_id": identifier,
                "variation_id": int(opportunity["variation_id"]),
                "sku": str(opportunity["sku"]),
                "ordered_quantity": str(opportunity["ordered_quantity"]),
                "package_number": int(event["package_number"]),
                "packed_quantity": str(event["packed_quantity"]),
                "single_piece": "yes" if packed == 1 else "no",
                "actual_length_cm": str(event["length_cm"]),
                "actual_width_cm": str(event["width_cm"]),
                "actual_height_cm": str(event["height_cm"]),
                "actual_gross_weight_kg": str(event["gross_weight_kg"]),
                "actual_packing_material": str(event["packing_material"]),
                "estimate_length_cm": str(opportunity["estimate"]["packed_length_cm"]),
                "estimate_width_cm": str(opportunity["estimate"]["packed_width_cm"]),
                "estimate_height_cm": str(opportunity["estimate"]["packed_height_cm"]),
                "estimate_gross_weight_kg": str(opportunity["estimate"]["gross_weight_kg"]),
                "evidence_ref": str(event["evidence_ref"]),
                "recorded_utc": str(event["recorded_utc"]),
                "event_id": str(event["event_id"]),
                "event_type": str(event["event_type"]),
            })
    observations.sort(key=lambda row: (row["group_id"], row["order_id"], row["package_number"]))

    axes = (
        ("length_cm", "packed_length_cm"),
        ("width_cm", "packed_width_cm"),
        ("height_cm", "packed_height_cm"),
        ("gross_weight_kg", "gross_weight_kg"),
    )
    groups: list[dict[str, Any]] = []
    for group_id in sorted(catalog.groups):
        group = catalog.groups[group_id]
        group_opportunities = [o for o in opportunities.values() if str(o["group_id"]) == group_id]
        group_events = [
            event for identifier, records in by_opportunity.items() for event in records
            if identifier in opportunities and str(opportunities[identifier]["group_id"]) == group_id
        ]
        single = [e for e in group_events if Decimal(str(e["packed_quantity"])) == 1]
        single_orders = sorted({int(opportunities[str(e["opportunity_id"])]["order_id"]) for e in single})
        distinct_single_orders = len(single_orders)

        row: dict[str, Any] = {
            "group_id": group_id,
            "family": group["family"],
            "size_in": group["size_in"],
            "pressure_psi": group["pressure_psi"],
            "variation_count": len(group["variation_ids"]),
            "total_opportunities": len(group_opportunities),
            "measured_packages": len(group_events),
            "multi_piece_packages": len(group_events) - len(single),
            "single_piece_observations": len(single),
            "distinct_single_piece_orders": distinct_single_orders,
            "estimate_length_cm": group["packed_length_cm"],
            "estimate_width_cm": group["packed_width_cm"],
            "estimate_height_cm": group["packed_height_cm"],
            "estimate_gross_weight_kg": group["gross_weight_kg"],
            "estimate_verification_status": group["verification_status"],
            "data_confidence": confidence_for(distinct_single_orders),
            "review_note": REVIEW_ONLY_BANNER,
        }
        maxima: dict[str, Decimal | None] = {}
        variances: list[Decimal] = []
        exceeds = False
        for actual_key, estimate_key in axes:
            values = [Decimal(str(event[actual_key])) for event in single]
            best = max(values) if values else None
            maxima[actual_key] = best
            estimate = Decimal(str(group[estimate_key]))
            if best is None:
                row["actual_max_" + actual_key] = ""
                continue
            row["actual_max_" + actual_key] = dec_str(best)
            variances.append(((best - estimate) / estimate * Decimal("100")))
            if best > estimate:
                exceeds = True
        row["max_percent_variance"] = (
            dec_str(max(variances).quantize(Decimal("0.1"))) if variances else ""
        )

        if distinct_single_orders < REVIEW_THRESHOLD_ORDERS:
            row["recommendation_status"] = STATUS_COLLECT
        elif exceeds:
            row["recommendation_status"] = STATUS_REVISE
        else:
            row["recommendation_status"] = STATUS_SUPPORTED

        for actual_key, _estimate_key in axes:
            suggestion = ""
            if row["recommendation_status"] == STATUS_REVISE and maxima[actual_key] is not None:
                step = Decimal("0.5") if actual_key == "gross_weight_kg" else Decimal("5")
                suggestion = dec_str(round_up_to(maxima[actual_key], step))
            row["suggested_" + actual_key] = suggestion
        groups.append(row)

    pending: list[dict[str, Any]] = []
    for identifier in sorted(opportunities):
        opportunity = opportunities[identifier]
        cover = coverage(opportunity, live)
        if cover["complete"]:
            continue
        pending.append({
            "opportunity_id": identifier,
            "order_id": int(opportunity["order_id"]),
            "order_number": str(opportunity.get("order_number") or ""),
            "group_id": str(opportunity["group_id"]),
            "sku": str(opportunity["sku"]),
            "ordered_quantity": str(opportunity["ordered_quantity"]),
            "packed_quantity": dec_str(cover["packed_quantity"]),
            "remaining_quantity": dec_str(cover["remaining_quantity"]),
            "packages_recorded": cover["packages"],
            "captured_utc": str(opportunity.get("captured_utc") or ""),
        })
    pending.sort(key=lambda row: (row["order_id"], row["opportunity_id"]))

    return {
        "generated_utc": iso_utc(),
        "estimate_csv_sha256": catalog.sha256,
        "review_note": REVIEW_ONLY_BANNER,
        "total_opportunities": len(opportunities),
        "total_active_packages": len(live),
        "observations": observations,
        "groups": groups,
        "pending": pending,
        "groups_ready_for_review": [
            row["group_id"] for row in groups
            if row["distinct_single_piece_orders"] >= REVIEW_THRESHOLD_ORDERS
        ],
    }


OBSERVATION_COLUMNS = (
    "group_id", "order_id", "order_number", "opportunity_id", "variation_id", "sku",
    "ordered_quantity", "package_number", "packed_quantity", "single_piece",
    "actual_length_cm", "actual_width_cm", "actual_height_cm", "actual_gross_weight_kg",
    "actual_packing_material", "estimate_length_cm", "estimate_width_cm",
    "estimate_height_cm", "estimate_gross_weight_kg", "evidence_ref", "recorded_utc",
    "event_id", "event_type",
)
GROUP_COLUMNS = (
    "group_id", "family", "size_in", "pressure_psi", "variation_count",
    "total_opportunities", "measured_packages", "multi_piece_packages",
    "single_piece_observations", "distinct_single_piece_orders",
    "estimate_length_cm", "estimate_width_cm", "estimate_height_cm",
    "estimate_gross_weight_kg", "actual_max_length_cm", "actual_max_width_cm",
    "actual_max_height_cm", "actual_max_gross_weight_kg", "max_percent_variance",
    "data_confidence", "recommendation_status", "suggested_length_cm",
    "suggested_width_cm", "suggested_height_cm", "suggested_gross_weight_kg",
    "estimate_verification_status", "review_note",
)


def _csv_text(columns: Iterable[str], rows: Iterable[dict[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n",
                            extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({name: row.get(name, "") for name in columns})
    return buffer.getvalue()


def _report_markdown(experience: dict[str, Any]) -> str:
    lines = [
        "# FRP Depot packing experience",
        "",
        f"{REVIEW_ONLY_BANNER}.",
        "",
        "Physical packing observations collected from real FRP Depot orders. This",
        "report RECOMMENDS only. It never edits the researched estimate, WooCommerce,",
        "the checkout guard or anything UPS sees.",
        "",
        f"- Generated (UTC): {experience['generated_utc']}",
        f"- Researched estimate revision: {experience['estimate_csv_sha256']}",
        f"- Packing opportunities queued: {experience['total_opportunities']}",
        f"- Active measured packages: {experience['total_active_packages']}",
        f"- Groups at or past {REVIEW_THRESHOLD_ORDERS} distinct single-piece orders: "
        f"{len(experience['groups_ready_for_review'])}",
        "",
        "Estimates keep their standing status until they are changed separately:",
        "RESEARCH-BASED ESTIMATE - NOT PHYSICALLY VERIFIED - NOT UPS APPROVED.",
        "",
        "## Groups with data",
        "",
    ]
    measured = [row for row in experience["groups"] if row["measured_packages"] or row["total_opportunities"]]
    if not measured:
        lines.append("No packing opportunity has been captured yet.")
    else:
        lines.append("| Group | Family | Size in | Opportunities | Packages | 1-piece orders | "
                     "Confidence | Recommendation |")
        lines.append("| --- | --- | --- | ---: | ---: | ---: | --- | --- |")
        for row in measured:
            lines.append(
                f"| {row['group_id']} | {row['family']} | {row['size_in']} | "
                f"{row['total_opportunities']} | {row['measured_packages']} | "
                f"{row['distinct_single_piece_orders']} | {row['data_confidence']} | "
                f"{row['recommendation_status']} |"
            )
    lines += ["", "## Observed-maximum suggestions", ""]
    revise = [row for row in experience["groups"] if row["recommendation_status"] == STATUS_REVISE]
    if not revise:
        lines.append("No group has enough single-piece orders to suggest a revision.")
    else:
        lines.append("Suggested values are the OBSERVED MAXIMUM rounded up to the next 5 cm and")
        lines.append("next 0.5 kg. No margin is added; they are observed-max suggestions only.")
        lines.append("")
        lines.append("| Group | Est L x W x H cm | Est kg | Observed max L x W x H cm | "
                     "Observed max kg | Suggested L x W x H cm | Suggested kg |")
        lines.append("| --- | --- | ---: | --- | ---: | --- | ---: |")
        for row in revise:
            lines.append(
                f"| {row['group_id']} | {row['estimate_length_cm']} x {row['estimate_width_cm']} x "
                f"{row['estimate_height_cm']} | {row['estimate_gross_weight_kg']} | "
                f"{row['actual_max_length_cm']} x {row['actual_max_width_cm']} x "
                f"{row['actual_max_height_cm']} | {row['actual_max_gross_weight_kg']} | "
                f"{row['suggested_length_cm']} x {row['suggested_width_cm']} x "
                f"{row['suggested_height_cm']} | {row['suggested_gross_weight_kg']} |"
            )
    lines += ["", "## Packing opportunities still waiting for a measurement", ""]
    if not experience["pending"]:
        lines.append("None.")
    else:
        lines.append("| Order | SKU | Group | Ordered | Packed | Remaining |")
        lines.append("| --- | --- | --- | ---: | ---: | ---: |")
        for row in experience["pending"][:50]:
            lines.append(
                f"| {row['order_id']} | {row['sku']} | {row['group_id']} | "
                f"{row['ordered_quantity']} | {row['packed_quantity']} | "
                f"{row['remaining_quantity']} |"
            )
        if len(experience["pending"]) > 50:
            lines.append(f"| ... | {len(experience['pending']) - 50} more | | | | |")
    lines.append("")
    return "\n".join(lines)


def rebuild_derived_views(catalog: Catalog | None = None) -> dict[str, Any]:
    catalog = catalog or load_catalog()
    with data_lock():
        experience = build_experience(catalog)
        atomic_write_text(observations_csv_path(),
                          _csv_text(OBSERVATION_COLUMNS, experience["observations"]))
        atomic_write_text(group_report_csv_path(),
                          _csv_text(GROUP_COLUMNS, experience["groups"]))
        atomic_write_text(group_report_md_path(), _report_markdown(experience))
    return experience


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------

OPPORTUNITY_REQUIRED = (
    "schema", "opportunity_id", "site", "order_id", "order_number", "line_item_id",
    "variation_id", "sku", "ordered_quantity", "group_id", "estimate",
    "estimate_csv_sha256", "captured_utc",
)
EVENT_REQUIRED = (
    "schema", "event_id", "event_type", "recorded_utc", "recorded_by", "opportunity_id",
    "order_id", "group_id", "variation_id", "sku", "package_number", "packed_quantity",
    "length_cm", "width_cm", "height_cm", "gross_weight_kg", "packing_material",
    "evidence_ref",
)


def run_validate(catalog: Catalog | None = None) -> dict[str, Any]:
    catalog = catalog or load_catalog()
    problems: list[str] = []
    opportunities = load_opportunities()
    events = load_events()

    for identifier, record in sorted(opportunities.items()):
        missing = [name for name in OPPORTUNITY_REQUIRED if name not in record]
        if missing:
            problems.append(f"{identifier}: missing fields {', '.join(missing)}")
            continue
        expected = opportunity_id_for(
            str(record["site"]), int(record["order_id"]),
            int(record["line_item_id"]), int(record["variation_id"]),
        )
        if expected != identifier:
            problems.append(f"{identifier}: identifier does not match its order/line/variation.")
        target = catalog.target_for_variation(int(record["variation_id"]))
        if target is None:
            problems.append(f"{identifier}: variation {record['variation_id']} is not a packing target.")
        elif target["sku"] != str(record["sku"]):
            problems.append(f"{identifier}: SKU {record['sku']} disagrees with the catalog.")
        if str(record["estimate_csv_sha256"]) != catalog.sha256:
            problems.append(f"{identifier}: captured under a different estimate revision.")
        forbidden = find_forbidden_keys(record)
        if forbidden:
            problems.append(f"{identifier}: forbidden field(s) stored: {', '.join(forbidden)}")

    seen_events: set[str] = set()
    superseded_by: dict[str, list[str]] = {}
    for record in events:
        missing = [name for name in EVENT_REQUIRED if name not in record]
        if missing:
            problems.append(f"event {record.get('event_id')}: missing fields {', '.join(missing)}")
            continue
        event_id = str(record["event_id"])
        if event_id in seen_events:
            problems.append(f"event {event_id}: duplicated event id.")
        seen_events.add(event_id)
        if str(record["recorded_by"]) != RECORDED_BY:
            problems.append(f"event {event_id}: recorded_by is not {RECORDED_BY}.")
        if str(record["opportunity_id"]) not in opportunities:
            problems.append(f"event {event_id}: references unknown opportunity.")
        for field, maximum in (("length_cm", MAX_DIMENSION_CM), ("width_cm", MAX_DIMENSION_CM),
                               ("height_cm", MAX_DIMENSION_CM), ("gross_weight_kg", MAX_WEIGHT_KG),
                               ("packed_quantity", MAX_WEIGHT_KG)):
            try:
                positive_decimal(record[field], field, maximum)
            except PackingError as exc:
                problems.append(f"event {event_id}: {exc}")
        try:
            if not (Decimal(str(record["length_cm"])) >= Decimal(str(record["width_cm"]))
                    >= Decimal(str(record["height_cm"]))):
                problems.append(f"event {event_id}: dimensions are not stored as L >= W >= H.")
        except (InvalidOperation, ValueError):
            pass
        for field in ("evidence_ref", "notes", "packing_material"):
            value = str(record.get(field) or "")
            if value and contains_secret(value):
                problems.append(f"event {event_id}: {field} looks like a credential.")
        forbidden = find_forbidden_keys(record)
        if forbidden:
            problems.append(f"event {event_id}: forbidden field(s) stored: {', '.join(forbidden)}")
        parent = record.get("supersedes_event_id")
        if parent:
            superseded_by.setdefault(str(parent), []).append(event_id)

    known = {str(record.get("event_id")) for record in events}
    positions = {str(record.get("event_id")): index for index, record in enumerate(events)}
    by_id = {str(record.get("event_id")): record for record in events}
    for parent, children in sorted(superseded_by.items()):
        if parent not in known:
            problems.append(f"event(s) {', '.join(sorted(children))}: supersede unknown event {parent}.")
            continue
        if len(children) > 1:
            problems.append(f"event {parent}: superseded more than once by {', '.join(sorted(children))}.")
        for child in children:
            if positions.get(child, 0) <= positions.get(parent, 0):
                problems.append(f"event {child}: appears before the event it supersedes.")
            if str(by_id[child].get("opportunity_id")) != str(by_id[parent].get("opportunity_id")):
                problems.append(f"event {child}: supersedes an event on another opportunity.")
            if int(by_id[child].get("package_number") or 0) != int(by_id[parent].get("package_number") or -1):
                problems.append(f"event {child}: supersedes another package number.")

    live = active_events(events)
    for identifier, record in sorted(opportunities.items()):
        mine = events_for(identifier, live)
        numbers = [int(item["package_number"]) for item in mine]
        if len(numbers) != len(set(numbers)):
            problems.append(f"{identifier}: two active events share a package number.")
        try:
            total = sum((Decimal(str(item["packed_quantity"])) for item in mine), Decimal("0"))
            if total > Decimal(str(record["ordered_quantity"])):
                problems.append(f"{identifier}: active packed quantity exceeds the ordered quantity.")
        except (InvalidOperation, KeyError, ValueError):
            problems.append(f"{identifier}: packed quantities are unreadable.")

    return {
        "status": "VALID" if not problems else "INVALID",
        "opportunities": len(opportunities),
        "events": len(events),
        "active_events": len(live),
        "estimate_csv_sha256": catalog.sha256,
        "problems": problems[:100],
        "problem_count": len(problems),
    }


# --------------------------------------------------------------------------
# Error-alert flag (used by the monitor wrapper)
# --------------------------------------------------------------------------

def read_error_flag() -> dict[str, Any] | None:
    path = error_flag_path()
    if not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"signature": "", "first_utc": "", "count": 1}
    return parsed if isinstance(parsed, dict) else None


def raise_error_flag(signature: str) -> bool:
    """Record a failure. Returns True when this failure should be announced."""
    ensure_data_root()
    existing = read_error_flag()
    if existing and str(existing.get("signature") or "") == signature:
        existing["count"] = int(existing.get("count") or 1) + 1
        existing["last_utc"] = iso_utc()
        atomic_write_text(error_flag_path(), json.dumps(existing, ensure_ascii=True, indent=2) + "\n")
        return False
    payload = {"signature": signature, "first_utc": iso_utc(), "last_utc": iso_utc(), "count": 1}
    atomic_write_text(error_flag_path(), json.dumps(payload, ensure_ascii=True, indent=2) + "\n")
    return True


def clear_error_flag() -> bool:
    path = error_flag_path()
    if path.exists():
        path.unlink()
        return True
    return False


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))


def command_initialize(args: argparse.Namespace) -> None:
    result = run_initialize()
    if getattr(args, "json", False):
        _print_json(result)
        return
    print("Packing observation: INITIALIZED (future orders only)")
    print(f"Store: {result['site']}")
    print(f"Baseline order id: {result['baseline_order_id']} "
          f"({result['baseline_order_date_created_gmt']} GMT)")
    print(f"Packing targets: {result['groups']} groups / {result['variations']} variations")
    print("Opportunities queued from past orders: 0 (deliberate - they cannot be measured now)")
    print(f"Local data root: {result['data_root']}")
    print("No WooCommerce write was made or is possible from this tool.")


def command_scan(args: argparse.Namespace) -> None:
    result = run_scan()
    if getattr(args, "json", False):
        _print_json(result)
        return
    print(f"Packing scan: OK ({result['scanned_utc']})")
    print(f"Orders read: {result['orders_read']} | pending rechecked: {result['pending_rechecked']}")
    print(f"New packing opportunities: {result['new_opportunity_count']}")
    for row in result["new_opportunities"][:20]:
        print(f"  order {row['order_id']}: {row['quantity']} x {row['sku']} ({row['group_id']})")
    print(f"Pending candidate orders: {result['pending_count']}")


def command_pending(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    experience = build_experience(catalog)
    limit = max(1, int(args.limit))
    rows = experience["pending"][:limit]
    if getattr(args, "json", False):
        _print_json({"pending_count": len(experience["pending"]), "shown": len(rows),
                     "pending": rows})
        return
    print(f"Packing opportunities awaiting measurement: {len(experience['pending'])}")
    if not rows:
        print("None.")
        return
    print(f"{'Opportunity':<30}{'Order':>8}  {'SKU':<22}{'Group':<10}{'Ordered':>8}{'Packed':>8}")
    for row in rows:
        print(f"{row['opportunity_id']:<30}{row['order_id']:>8}  {row['sku']:<22}"
              f"{row['group_id']:<10}{row['ordered_quantity']:>8}{row['packed_quantity']:>8}")
    if len(experience["pending"]) > len(rows):
        print(f"... {len(experience['pending']) - len(rows)} more (raise --limit)")


def command_show(args: argparse.Namespace) -> None:
    identifier = str(args.opportunity_id or "").strip()
    opportunities = load_opportunities()
    opportunity = opportunities.get(identifier)
    if opportunity is None:
        raise PackingError(f"Unknown opportunity {identifier or '(blank)'}.")
    live = events_for(identifier, active_events(load_events()))
    payload = {
        "opportunity": {
            "opportunity_id": identifier,
            "order_id": opportunity["order_id"],
            "order_number": opportunity["order_number"],
            "order_status_at_capture": opportunity.get("order_status_at_capture"),
            "sku": opportunity["sku"],
            "variation_id": opportunity["variation_id"],
            "group_id": opportunity["group_id"],
            "ordered_quantity": opportunity["ordered_quantity"],
            "estimate": opportunity["estimate"],
            "captured_utc": opportunity.get("captured_utc"),
        },
        "active_packages": [
            {
                "package_number": event["package_number"],
                "packed_quantity": event["packed_quantity"],
                "length_cm": event["length_cm"],
                "width_cm": event["width_cm"],
                "height_cm": event["height_cm"],
                "gross_weight_kg": event["gross_weight_kg"],
                "packing_material": event["packing_material"],
                "evidence_ref": event["evidence_ref"],
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "recorded_utc": event["recorded_utc"],
            }
            for event in sorted(live, key=lambda item: int(item["package_number"]))
        ],
        "review_note": REVIEW_ONLY_BANNER,
    }
    if getattr(args, "json", False):
        _print_json(payload)
        return
    info = payload["opportunity"]
    print(f"Opportunity {identifier}")
    print(f"  Woo order {info['order_id']} ({info['order_number']}), status at capture "
          f"{info['order_status_at_capture']}")
    print(f"  {info['ordered_quantity']} x {info['sku']} -> group {info['group_id']}")
    estimate = info["estimate"]
    print(f"  Estimate: {estimate['packed_length_cm']} x {estimate['packed_width_cm']} x "
          f"{estimate['packed_height_cm']} cm, {estimate['gross_weight_kg']} kg "
          f"({estimate['verification_status']})")
    if not payload["active_packages"]:
        print("  No measurement recorded yet.")
    for package in payload["active_packages"]:
        print(f"  Package {package['package_number']}: {package['packed_quantity']} pc, "
              f"{package['length_cm']} x {package['width_cm']} x {package['height_cm']} cm, "
              f"{package['gross_weight_kg']} kg, {package['packing_material']}")
    print("  " + REVIEW_ONLY_BANNER)


def command_report(args: argparse.Namespace) -> None:
    experience = rebuild_derived_views()
    if getattr(args, "json", False):
        _print_json({key: value for key, value in experience.items() if key != "observations"})
        return
    print("FRP Depot packing experience")
    print(REVIEW_ONLY_BANNER)
    print(f"Opportunities queued: {experience['total_opportunities']} | "
          f"active measured packages: {experience['total_active_packages']}")
    ready = experience["groups_ready_for_review"]
    print(f"Groups with {REVIEW_THRESHOLD_ORDERS}+ distinct single-piece orders: {len(ready)}")
    rows = [row for row in experience["groups"] if row["measured_packages"]]
    for row in rows[:25]:
        print(f"  {row['group_id']} {row['family']:<16} {row['size_in']:>5} in | "
              f"pkgs {row['measured_packages']:>3} | 1-pc orders "
              f"{row['distinct_single_piece_orders']:>3} | {row['data_confidence']:<24} "
              f"{row['recommendation_status']}")
    if len(rows) > 25:
        print(f"  ... {len(rows) - 25} more in {group_report_csv_path()}")
    if not rows:
        print("  No physical measurement has been recorded yet.")
    print(f"Files: {group_report_csv_path()}")
    print(f"       {group_report_md_path()}")


def command_record(args: argparse.Namespace) -> None:
    result = run_record(args)
    if getattr(args, "json", False):
        _print_json(result)
        return
    print(f"Measurement RECORDED for {result['opportunity_id']} package "
          f"{result['package_number']}")
    print(f"  Stored L x W x H: {result['length_cm']} x {result['width_cm']} x "
          f"{result['height_cm']} cm, {result['gross_weight_kg']} kg")
    print(f"  Event id: {result['event_id']}")
    print("  " + REVIEW_ONLY_BANNER)


def command_correct(args: argparse.Namespace) -> None:
    result = run_correct(args)
    if getattr(args, "json", False):
        _print_json(result)
        return
    print(f"Correction RECORDED for {result['opportunity_id']} package "
          f"{result['package_number']}")
    print(f"  New event id: {result['event_id']} supersedes {result['supersedes_event_id']}")
    print("  The superseded event stays on file; history is never edited.")


def command_validate(args: argparse.Namespace) -> None:
    result = run_validate()
    if getattr(args, "json", False):
        _print_json(result)
    else:
        print(f"Packing observation data: {result['status']}")
        print(f"  Opportunities: {result['opportunities']} | events: {result['events']} "
              f"| active: {result['active_events']}")
        for problem in result["problems"][:20]:
            print("  PROBLEM: " + problem)
        if result["problem_count"] > 20:
            print(f"  ... {result['problem_count'] - 20} more problems")
    if result["status"] != "VALID":
        raise SystemExit(1)


def _add_measurement_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--opportunity-id", required=True)
    parser.add_argument("--package-number", required=True, type=int)
    parser.add_argument("--packed-quantity", required=True)
    parser.add_argument("--length-cm", required=True)
    parser.add_argument("--width-cm", required=True)
    parser.add_argument("--height-cm", required=True)
    parser.add_argument("--gross-weight-kg", required=True)
    parser.add_argument("--packing-material", required=True)
    parser.add_argument("--evidence-ref", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FRP Depot packing-experience collector (WooCommerce read-only)."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("initialize", help="Set the future-only baseline.")
    initialize.add_argument("--json", action="store_true")
    initialize.set_defaults(func=command_initialize)

    scan = commands.add_parser("scan", help="Read-only sweep for new packing opportunities.")
    scan.add_argument("--json", action="store_true")
    scan.set_defaults(func=command_scan)

    pending = commands.add_parser("pending", help="Opportunities still awaiting a measurement.")
    pending.add_argument("--limit", type=int, default=20)
    pending.add_argument("--json", action="store_true")
    pending.set_defaults(func=command_pending)

    show = commands.add_parser("show", help="One opportunity and its active packages.")
    show.add_argument("--opportunity-id", required=True)
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=command_show)

    report = commands.add_parser("report", help="Regenerate and print the group summary.")
    report.add_argument("--json", action="store_true")
    report.set_defaults(func=command_report)

    record = commands.add_parser("record", help="Record one physical package measurement.")
    _add_measurement_arguments(record)
    record.set_defaults(func=command_record)

    correct = commands.add_parser("correct", help="Supersede one measurement with a corrected one.")
    _add_measurement_arguments(correct)
    correct.add_argument("--supersedes-event-id", required=True)
    correct.set_defaults(func=command_correct)

    validate = commands.add_parser("validate", help="Check the append-only files end to end.")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=command_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except SystemExit:
        raise
    except (PackingError, wc.WooError, OSError, ValueError) as exc:
        print("ERROR: " + wc.scrub(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
