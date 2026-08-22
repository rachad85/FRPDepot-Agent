"""Offline safety tests for the fixed FRP MANWAY gallery recovery tool.

Every test is deterministic and mocked. Nothing here opens a browser, reaches
WordPress, WooCommerce, Zoho, Drive or any network, stages a live plan, or
touches the permanent prior evidence except to read it.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
from copy import deepcopy
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import wordpress_open_manway_gallery_recovery_tool as tool  # noqa: E402

SOURCE_PATH = Path(tool.__file__)
SOURCE_TEXT = SOURCE_PATH.read_text(encoding="utf-8")
SOURCE_TREE = ast.parse(SOURCE_TEXT)

LIVE_CAPABILITY = dict(tool.GUARD_PARTIAL_RECOVERY_CAPABILITY)
DEFAULT_DIRS = {name: str(getattr(tool, name)) for name in
                ("PLAN_DIR", "STAGE_REGISTRY_DIR", "ATTEMPT_LEDGER_DIR", "RESULT_DIR",
                 "JOURNAL_DIR", "RESERVATION_DIR")}
DEFAULT_REGISTRY_KEY_PATH = str(tool.REGISTRY_KEY_PATH)
HERO_ID = tool.PRIOR_VERIFIED_UPLOAD["attachment_id"]

# The EXACT permanent receipt line for the superseded operation, copied verbatim
# from the real append-only log at import time. v2.0.0's suite pointed RECEIPTS at
# an EMPTY temporary file, which silently disabled the very prior-evidence
# validation it claimed to be testing.
REAL_RECEIPTS_PATH = Path(tool.RECEIPTS)
PRIOR_RECEIPT_LINE = None
if REAL_RECEIPTS_PATH.is_file():
    with REAL_RECEIPTS_PATH.open("r", encoding="utf-8", newline="\n") as _handle:
        for _line in _handle:
            if tool.PRIOR_OPERATION_SHA256 in _line:
                PRIOR_RECEIPT_LINE = _line.rstrip("\n")
                break


def seed_receipts(path: Path) -> None:
    """Give the temporary receipt log the exact permanent prior line."""
    if PRIOR_RECEIPT_LINE is None:
        raise unittest.SkipTest(
            "the permanent prior receipt line is not present in this tree")
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(PRIOR_RECEIPT_LINE + "\n")
PRIVATE_ID = tool.family_base.GUARD_PRIVATE_EXCEPTION["attachment_id"]
PRIVATE_NAME = Path(tool.family_base.GUARD_PRIVATE_EXCEPTION["attached_file"]).name

_LANE_LOCK_TMP = None
_LANE_LOCK_ORIGINAL_DIR = None


def setUpModule():
    global _LANE_LOCK_TMP, _LANE_LOCK_ORIGINAL_DIR

    import ui_lane_lock

    _LANE_LOCK_TMP = tempfile.TemporaryDirectory()
    _LANE_LOCK_ORIGINAL_DIR = ui_lane_lock.LOCK_DIR
    ui_lane_lock.LOCK_DIR = Path(_LANE_LOCK_TMP.name)


def tearDownModule():
    import ui_lane_lock

    if _LANE_LOCK_ORIGINAL_DIR is not None:
        ui_lane_lock.LOCK_DIR = _LANE_LOCK_ORIGINAL_DIR
    if _LANE_LOCK_TMP is not None:
        _LANE_LOCK_TMP.cleanup()


FIXED_SOURCE_BYTES = {row["filename"]: Path(row["path"]).read_bytes()
                      for row in tool.FIXED_IMAGES}


def source_bytes(position: int) -> bytes:
    return Path(tool.FIXED_IMAGES[position - 1]["path"]).read_bytes()


def public_url(filename: str) -> str:
    return f"https://frpdepots.com/wp-content/uploads/2026/08/{filename}"


class FakeGuard:
    """The durable Guard 1.0.7 recovery record, with real progression.

    v2.0.0's fake froze the library and the reconciliation after acquisition, so
    the suite could not see that the production predicate compared evolving live
    state to the frozen staged baseline and refused every upload after the first.
    This one is a small state machine: reserving and binding an upload changes
    what every later read reports, exactly as the plugin's durable row does.
    """

    def __init__(self, initial: dict[int, int]):
        self.initial_attachments = {
            tool.FIXED_IMAGES[position - 1]["filename"]: attachment_id
            for position, attachment_id in sorted(initial.items())
        }
        self.missing_positions = [position for position in tool.POSITIONS
                                  if position not in initial]
        self.reservations: list[str] = []
        self.bound: dict[str, int] = {}

    def reserve(self, position: int) -> None:
        self.reservations.append(tool.FIXED_IMAGES[position - 1]["filename"])

    def bind(self, position: int, attachment_id: int) -> None:
        self.bound[tool.FIXED_IMAGES[position - 1]["filename"]] = int(attachment_id)

    @property
    def bound_positions(self) -> list[int]:
        return sorted(tool.POSITION_BY_FILENAME[name] for name in self.bound)

    def live_map(self, resolved: dict[int, int]) -> dict[int, int]:
        mapping = dict(resolved)
        for name, attachment_id in self.bound.items():
            mapping[tool.POSITION_BY_FILENAME[name]] = attachment_id
        return mapping

    def projection(self) -> dict[str, object]:
        bound_count = len(self.bound)
        unbound = None
        if len(self.reservations) == bound_count + 1:
            position = self.missing_positions[bound_count]
            unbound = {"position": position,
                       "filename": tool.FIXED_IMAGES[position - 1]["filename"]}
        return {
            "contract": tool.GUARD_RECOVERY_CONTRACT,
            "product_id": tool.PRODUCT_ID,
            "prior_operation_sha256": tool.PRIOR_OPERATION_SHA256,
            "initial_attachments": dict(self.initial_attachments),
            "missing_positions": list(self.missing_positions),
            "current_reservations": list(self.reservations),
            "bound_uploads": dict(self.bound),
            "remaining_positions": self.missing_positions[bound_count:],
            "unbound_reservation": unbound,
        }


def fixed_identity(position: int, attachment_id: int, **overrides) -> dict[str, object]:
    """The complete closed identity Guard 1.0.7 publishes for one fixed image."""
    fixed = tool.FIXED_IMAGES[position - 1]
    row = {
        "attachment_id": int(attachment_id),
        "position": position,
        "post_type": "attachment",
        "post_status": "inherit",
        "mime_type": "image/png",
        "relative_path": f"2026/08/{fixed['filename']}",
        "basename": fixed["filename"],
        "bytes": fixed["bytes"],
        "sha256": fixed["sha256"],
        "png_width": fixed["width"],
        "png_height": fixed["height"],
        "png_mode": fixed["mode"],
        "png_bit_depth": 8,
        "png_color_type": 2,
    }
    row.update(overrides)
    return row


def origin_proof_for(live: dict[int, int], **overrides) -> dict[str, object]:
    """The complete per-path origin-only proof for one live map."""
    files = []
    blockers = []
    for fixed in tool.FIXED_IMAGES:
        position = fixed["position"]
        attachment_id = live.get(position)
        if attachment_id is None:
            files.append({"position": position, "basename": fixed["filename"],
                          "discovered": 0, "paths": [], "owner_attachment_ids": [],
                          "bytes_and_hash_exact": False, "origin_only": False})
            continue
        path = {"relative_path": f"2026/08/{fixed['filename']}", "bytes": fixed["bytes"],
                "sha256": fixed["sha256"], "bytes_and_hash_exact": True,
                "owner_attachment_ids": [int(attachment_id)]}
        files.append({"position": position, "basename": fixed["filename"],
                      "discovered": 1, "paths": [path],
                      "owner_attachment_ids": [int(attachment_id)],
                      "bytes_and_hash_exact": True, "origin_only": False})
    proof = {
        "schema": tool.GUARD_PROOF_SCHEMA,
        "plugin_version": tool.GUARD_PLUGIN_VERSION,
        "mode": "origin_only_file_proof", "family": tool.FAMILY_KEY,
        "generated_utc": "2026-08-21T21:00:00+00:00",
        "directories_scanned": 3, "yearmonth_supported": True, "complete": True,
        "files": files, "origin_only": blockers,
    }
    proof.update(overrides)
    return proof


class FakeAdmin:
    """Models exactly the adapter surface the recovery tool is allowed to touch."""

    def __init__(self, resolved: dict[int, int], *, fail_upload_at: int | None = None,
                 upload_timeout_at: int | None = None, fail_guard_acquire: bool = False,
                 fail_guard_complete: bool = False, fail_public: bool = False,
                 fail_gallery_commit: Exception | None = None,
                 gallery_readback_wrong: bool = False,
                 guard_expires_minutes: int = 30):
        self.resolved = dict(resolved)
        self.fail_upload_at = fail_upload_at
        self.upload_timeout_at = upload_timeout_at
        self.fail_guard_acquire = fail_guard_acquire
        self.fail_guard_complete = fail_guard_complete
        self.fail_public = fail_public
        self.fail_gallery_commit = fail_gallery_commit
        self.gallery_readback_wrong = gallery_readback_wrong
        self.guard_expires_utc = (
            tool.utc_now() + timedelta(minutes=guard_expires_minutes)).isoformat()
        self.guard = FakeGuard(self.resolved)
        self.uploads: list[str] = []
        self.upload_positions_seen: list[int] = []
        self.guard_acquire_count = 0
        self.guard_complete_count = 0
        self.gallery_commit_count = 0
        self.gallery_committed_ids: list[int] | None = None
        self.next_upload_id = 9000
        self.events: list[str] = []
        self.id_to_name: dict[int, str] = {
            attachment_id: tool.FIXED_IMAGES[position - 1]["filename"]
            for position, attachment_id in self.resolved.items()
        }
        self.left = False

    # -- the live world ---------------------------------------------------
    def live_map(self) -> dict[int, int]:
        return self.guard.live_map(self.resolved)

    def library_rows(self) -> list[dict[str, object]]:
        rows = [{"id": PRIVATE_ID, "filename": PRIVATE_NAME, "stem": "private"}]
        for position, attachment_id in sorted(self.live_map().items()):
            name = tool.FIXED_IMAGES[position - 1]["filename"]
            rows.append({"id": attachment_id, "filename": name, "stem": Path(name).stem})
        return rows

    def enumerate_library(self):
        rows = self.library_rows()
        return {"rows": rows, "total": len(rows), "pages": 1, "complete": True,
                "unidentified": 0}

    def read_attachment(self, attachment_id, expected_basename=None, allowed_extensions=None):
        attachment_id = int(attachment_id)
        name = self.id_to_name.get(attachment_id, expected_basename or "")
        if expected_basename is not None and name != expected_basename:
            raise tool.media_base.MediaUploadError(
                "REFUSED: WordPress stored the upload under a different file name.")
        return {"attachment_id": attachment_id, "filename": name,
                "source_url": public_url(name)}

    def guard_health(self, expected_status="Guard inactive"):
        self.events.append(f"guard_health:{expected_status}")
        return {"plugin_version": tool.GUARD_PLUGIN_VERSION, "status": expected_status,
                "families": sorted(tool.family_base.FAMILY_KEYS),
                "capability": deepcopy(tool.GUARD_EXPECTED_CAPABILITY)}

    def origin_proof(self, expected_status="Guard inactive", **overrides):
        self.events.append("origin_proof")
        return tool.validate_origin_proof(
            origin_proof_for(self.live_map(), **(self.origin_overrides or {})))

    origin_overrides: dict[str, object] | None = None

    # -- guard -----------------------------------------------------------
    def snapshot(self, mode, active, *, total=None, matches=None, reserved=None,
                 overrides=None, recovery=None, identities=None):
        live = self.live_map()
        matched = matches if matches is not None else sorted(
            ({"attachment_id": attachment_id, "fixed_position": position}
             for position, attachment_id in live.items()),
            key=lambda row: row["fixed_position"])
        count = total if total is not None else len(self.library_rows())
        value = {
            "schema": tool.GUARD_PROOF_SCHEMA,
            "plugin_version": tool.GUARD_PLUGIN_VERSION,
            "mode": mode, "family": tool.FAMILY_KEY,
            "generated_utc": "2026-08-21T21:00:00+00:00",
            "attachment_total": count, "hashed_total": count - 1,
            "total_bytes": count * 10, "snapshot_sha256": "a" * 64,
            "complete": True, "failures": [],
            "private_exceptions": [deepcopy(tool.family_base.GUARD_PRIVATE_EXCEPTION)],
            "name_conflicts": deepcopy(matched), "hash_conflicts": deepcopy(matched),
            "fixed_matches": deepcopy(matched),
            "fixed_identities": (identities if identities is not None else [
                fixed_identity(row["fixed_position"], row["attachment_id"])
                for row in sorted(matched, key=lambda item: item["fixed_position"])]),
            "guard_active": active,
        }
        if active:
            value["guard_expires_utc"] = self.guard_expires_utc
            value["reserved_uploads"] = (len(self.guard.reservations)
                                         if reserved is None else reserved)
            value["recovery"] = (deepcopy(recovery) if recovery is not None
                                 else self.guard.projection())
        value.update(overrides or {})
        return value

    def atomic_snapshot(self, key):
        self.events.append("atomic_snapshot")
        return self.snapshot("atomic_snapshot", False)

    def prepare_guard_acquire(self, key):
        return object()

    def acquire_prepared_guard(self, key, button, on_submit_attempt=None):
        self.guard_acquire_count += 1
        self.events.append("acquire_guard")
        if on_submit_attempt is not None:
            on_submit_attempt()
        if self.fail_guard_acquire:
            raise RuntimeError("modelled guard acquisition failure")
        proof = self.snapshot("guard_acquired", True)
        proof["origin_only_proof"] = origin_proof_for(self.live_map())
        return proof

    def guarded_snapshot(self, key):
        self.events.append("guarded_snapshot")
        return self.snapshot("guarded_snapshot", True)

    def final_map(self) -> dict[int, int]:
        return self.live_map()

    def complete_guard(self, key, attachment_ids, on_submit_attempt=None):
        self.guard_complete_count += 1
        self.events.append("complete_guard")
        if on_submit_attempt is not None:
            on_submit_attempt()
        if self.fail_guard_complete:
            raise RuntimeError("modelled guard completion failure")
        return {
            "proof": {
                "schema": tool.GUARD_PROOF_SCHEMA,
                "plugin_version": tool.GUARD_PLUGIN_VERSION,
                "mode": "guard_completed", "family": key, "product_id": tool.PRODUCT_ID,
                "attachment_ids": list(attachment_ids),
                "attachment_identities": [
                    fixed_identity(position, attachment_id)
                    for position, attachment_id in enumerate(attachment_ids, 1)],
                "attachment_total": len(self.library_rows()),
                "snapshot_sha256": "a" * 64,
            },
            "post_completion_health": {
                "plugin_version": tool.GUARD_PLUGIN_VERSION, "status": "Guard inactive",
                "families": sorted(tool.family_base.FAMILY_KEYS)},
        }

    # -- the one write route ---------------------------------------------
    def upload_one(self, expected, known_ids, on_submit_attempt=None):
        position = int(expected["position"])
        self.upload_positions_seen.append(position)
        self.events.append(f"upload_{position}")
        self.guard.reserve(position)
        if on_submit_attempt is not None:
            on_submit_attempt()
        if self.upload_timeout_at == position:
            raise TimeoutError("modelled upload timeout")
        if self.fail_upload_at == position:
            raise RuntimeError("modelled upload failure")
        self.next_upload_id += 1
        self.id_to_name[self.next_upload_id] = expected["filename"]
        self.uploads.append(expected["filename"])
        self.guard.bind(position, self.next_upload_id)
        return self.next_upload_id

    def commit_recovery_gallery(self, if_match, on_submit_attempt=None):
        self.gallery_commit_count += 1
        self.events.append("commit_gallery")
        if on_submit_attempt is not None:
            on_submit_attempt()
        if self.fail_gallery_commit is not None:
            raise self.fail_gallery_commit
        live = self.live_map()
        ids = [live[position] for position in tool.POSITIONS]
        self.gallery_committed_ids = list(ids)
        after = '"' + hashlib.sha256(
            tool.canonical(ids).encode("ascii")).hexdigest() + '"'
        return {
            "schema": tool.GUARD_PROOF_SCHEMA,
            "plugin_version": tool.GUARD_PLUGIN_VERSION,
            "mode": "recovery_gallery_committed",
            "contract": tool.GUARD_RECOVERY_CONTRACT, "family": tool.FAMILY_KEY,
            "product_id": tool.PRODUCT_ID,
            "prior_operation_sha256": tool.PRIOR_OPERATION_SHA256,
            "attachment_ids": list(ids), "gallery_etag_before": if_match,
            "gallery_etag_after": after, "state_status": "gallery",
            "state_version": 2,
            "transport": "internal_authenticated_rest_do_request",
        }

    def verify_public_product(self, key, verified_source_urls):
        self.events.append("verify_public_product")
        if self.fail_public:
            raise RuntimeError("modelled public verification failure")
        if len(verified_source_urls) != tool.IMAGE_COUNT:
            raise RuntimeError("modelled public URL count mismatch")
        return {"url": tool.PRODUCT_PERMALINK, "http_status": 200,
                "product_id": tool.PRODUCT_ID, "fixed_images_in_order": tool.IMAGE_COUNT,
                "rendered_gallery_items": tool.IMAGE_COUNT, "broken_images": 0,
                "javascript_errors": 0}

    def leave_on_media_list(self):
        self.left = True


class RecoveryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.plan_dir = Path(self.tmp.name) / "plans"
        self.receipts = Path(self.tmp.name) / "receipts.jsonl"
        seed_receipts(self.receipts)
        self.patchers = [
            mock.patch.object(tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(tool, "STAGE_REGISTRY_DIR", self.plan_dir / "stage-registry"),
            mock.patch.object(tool, "ATTEMPT_LEDGER_DIR", self.plan_dir / "attempt-ledger"),
            mock.patch.object(tool, "RESERVATION_DIR", self.plan_dir / "result-reservations"),
            mock.patch.object(tool, "RESULT_DIR", self.plan_dir / "results"),
            mock.patch.object(tool, "JOURNAL_DIR", self.plan_dir / "event-journal"),
            mock.patch.object(tool, "REGISTRY_KEY_PATH", Path(self.tmp.name) / "registry.key"),
            mock.patch.object(tool, "RECEIPTS", self.receipts),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmp.cleanup()

    # -- fixtures --------------------------------------------------------
    @staticmethod
    def capable() -> dict[str, object]:
        """The pinned Guard 1.0.7 capability record, which CAN serve this recovery."""
        return deepcopy(tool.GUARD_PARTIAL_RECOVERY_CAPABILITY)

    @staticmethod
    def library_evidence(resolved: dict[int, int], *, extra_name_conflicts=None,
                         extra_hash_conflicts=None, overrides=None):
        image_rows = len(resolved)
        total = image_rows + 1
        hash_conflicts = [
            {"attachment_id": attachment_id,
             "matches_fixed_image": f"open_manway:{tool.FIXED_IMAGES[position - 1]['filename']}"}
            for position, attachment_id in sorted(resolved.items())
        ] + list(extra_hash_conflicts or [])
        name_conflicts = [
            {"attachment_id": attachment_id,
             "filename": tool.FIXED_IMAGES[position - 1]["filename"]}
            for position, attachment_id in sorted(resolved.items())
        ] + list(extra_name_conflicts or [])
        evidence = {
            "checked_utc": "2026-08-21T21:00:00+00:00",
            "library_total": total, "enumerated": total, "pages_read": 1,
            "enumeration_complete": True, "image_rows": image_rows,
            "image_hashes_completed": image_rows, "hash_failures": 0,
            "hash_bytes_read": 100, "hash_complete": True,
            "recheck_total": total, "recheck_enumerated": total, "recheck_pages": 1,
            "recheck_complete": True, "snapshot_stable": True,
            "recheck_image_hashes_completed": image_rows, "recheck_hash_failures": 0,
            "recheck_hash_bytes_read": 100, "recheck_hash_complete": True,
            "content_stable": True, "final_total": total, "final_enumerated": total,
            "final_pages": 1, "final_complete": True, "final_snapshot_stable": True,
            "private_exception": {"attachment_id": PRIVATE_ID, "filename": PRIVATE_NAME},
            "private_exception_proven": True, "name_conflicts": name_conflicts,
            "hash_conflicts": hash_conflicts, "reuse_candidates": [],
            "target_families": ["open_manway"], "complete": True,
        }
        evidence.update(overrides or {})
        return evidence

    @staticmethod
    def product_record(gallery=None):
        return {
            "id": tool.PRODUCT_ID, "name": tool.PRODUCT_LABEL, "slug": "frp-manway",
            "type": tool.PRODUCT_TYPE, "status": tool.PRODUCT_STATUS,
            "catalog_visibility": "visible", "sku": tool.PRODUCT_SKU,
            "permalink": tool.PRODUCT_PERMALINK, "price": "1200.00",
            "regular_price": "1200.00", "stock_status": "instock",
            "categories": [{"id": 9, "name": "Fittings", "slug": "fittings"}],
            "tags": [], "attributes": [], "variations": [], "related_ids": [1368, 1411],
            "meta_data": [{"id": 71, "key": "catalog_class", "value": "Website Catalog"}],
            "date_created": "2026-01-01T10:00:00",
            "date_created_gmt": "2026-01-01T15:00:00",
            "date_modified_gmt": "2026-08-21T18:00:00",
            "yoast_head": "derived", "yoast_head_json": {"og_image": []},
            "images": deepcopy(gallery if gallery is not None else [
                {"id": 6991, "alt": "", "src": "https://frpdepots.com/a.png"},
                {"id": 6992, "alt": "", "src": "https://frpdepots.com/b.png"},
                {"id": 6993, "alt": "", "src": "https://frpdepots.com/c.png"},
                {"id": 6994, "alt": "", "src": "https://frpdepots.com/d.png"},
            ]),
        }

    @classmethod
    def product_evidence_for(cls, gallery=None):
        record = cls.product_record(gallery)
        projection = tool.family_base.protected_product_projection(record)
        return {
            "product_id": tool.PRODUCT_ID,
            "identity": {field: record[field] for field in
                         ("id", "name", "sku", "type", "status", "permalink")},
            "date_modified_gmt": record["date_modified_gmt"],
            "before_gallery": tool.family_base.safe_gallery(record),
            "protected_fingerprint": hashlib.sha256(
                tool.canonical(projection).encode("utf-8")).hexdigest(),
            "protected_projection": projection,
        }

    @staticmethod
    def downloader():
        # Read the ~10 MB of fixed sources ONCE per process, not once per test.
        by_name = FIXED_SOURCE_BYTES

        def download(url, expected_basename=None, allowed_extensions=None):
            name = expected_basename or Path(str(url)).name
            return by_name[name]
        return download

    def live_patches(self, admin, resolved, *, gallery=None, library=None,
                     product_drift=None, readback_gallery_wrong=False,
                     protected_drift=False):
        """Every live read follows the FakeAdmin's evolving world.

        `library` may pin a fixed evidence object for the refusal tests; when it
        is None the Media Library evidence is rebuilt from the admin's CURRENT
        live map on every read, so an upload really does change what the next
        `read_guarded_live_state()` sees.
        """
        before = self.product_record(gallery)
        state = {"payload": None, "requests": [], "gets": 0, "if_match": None,
                 "write_calls": []}

        def api_get(endpoint, params=None, vault=None):
            state["gets"] += 1
            record = deepcopy(before)
            if product_drift is not None and state["gets"] == product_drift:
                record["price"] = "999.00"
            if admin.gallery_committed_ids is not None:
                record["images"] = ([{"id": 1}] if readback_gallery_wrong else [
                    {"id": value, "alt": "", "src": public_url(admin.id_to_name[value])}
                    for value in admin.gallery_committed_ids])
                if protected_drift:
                    record["price"] = "777.00"
            return record, {}

        def refuse_write(*args, **kwargs):
            state["write_calls"].append((args, kwargs))
            raise AssertionError(
                "the recovery tool must never make a WooCommerce write call")

        def duplicate_scan(*args, **kwargs):
            if library is not None:
                return deepcopy(library)
            return self.library_evidence(admin.live_map())

        patches = [
            mock.patch.object(tool.wc, "load_vault", return_value={
                "declared_permissions": "read_write", "site_url": tool.EXACT_ORIGIN,
                "consumer_key": "hidden", "consumer_secret": "hidden"}),
            mock.patch.object(tool.wc, "api_get", side_effect=api_get),
            mock.patch.object(tool.wc, "api_request", side_effect=refuse_write),
            mock.patch.object(tool, "admin_session",
                              side_effect=lambda allowed: contextlib.nullcontext(admin)),
            mock.patch.object(tool, "ui_browser_lock",
                              side_effect=lambda *a, **k: contextlib.nullcontext()),
            mock.patch.object(tool.family_base, "duplicate_scan",
                              side_effect=duplicate_scan),
            mock.patch.object(tool.media_base, "download_public_bytes",
                              side_effect=self.downloader()),
            mock.patch.object(tool, "emit"),
        ]
        return patches, state

    @contextlib.contextmanager
    def live(self, *args, **kwargs):
        patches, state = self.live_patches(*args, **kwargs)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            yield state

    def stage_plan(self, resolved=None, admin=None, **kwargs):
        resolved = {1: HERO_ID} if resolved is None else resolved
        admin = FakeAdmin(resolved) if admin is None else admin
        with self.live(admin, resolved, **kwargs):
            tool.command_stage(argparse.Namespace())
        plans = sorted(self.plan_dir.glob("*.json"))
        self.assertEqual(len(plans), 1)
        return plans[0], json.loads(plans[0].read_text(encoding="utf-8"))

    def commit(self, plan_path, admin, resolved, approval="APPROVED", **kwargs):
        with self.live(admin, resolved, **kwargs) as state:
            tool.command_commit(argparse.Namespace(plan=str(plan_path), approval=approval))
        return state


# ==========================================================================
# 1. Permanent prior evidence
# ==========================================================================
class PriorEvidenceTests(RecoveryTestCase):
    def test_live_prior_evidence_validates_exactly(self):
        evidence = tool.validate_prior_evidence()
        self.assertEqual(evidence["result"]["status"], "INDETERMINATE_NO_RETRY")
        self.assertEqual(evidence["failed_stage"], "upload_2")
        self.assertEqual(evidence["reason"], "MediaUploadError")
        self.assertEqual(evidence["verified_upload"]["attachment_id"], 7609)
        self.assertEqual(evidence["ambiguous_upload"]["filename"],
                         "02_manway_top_oblique.png")
        self.assertEqual(evidence["never_attempted_positions"], [3, 4, 5, 6])
        self.assertEqual(evidence["guard_expires_utc"], "2026-08-21T20:35:14+00:00")
        self.assertTrue(evidence["no_retry"])
        self.assertEqual(evidence["superseded_gallery_ids"], [6991, 6992, 6993, 6994])

    def test_pinned_prior_hashes_match_the_files_on_disk(self):
        for path, expected in (
                (tool.PRIOR_PLAN_PATH, tool.PRIOR_PLAN_FILE_SHA256),
                (tool.PRIOR_RESULT_PATH, tool.PRIOR_RESULT_FILE_SHA256),
                (tool.PRIOR_ATTEMPT_PATH, tool.PRIOR_ATTEMPT_FILE_SHA256)):
            with self.subTest(path=path.name):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)

    def test_one_byte_of_prior_evidence_drift_refuses(self):
        raw = tool.PRIOR_RESULT_PATH.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            drifted = Path(tmp) / "result.json"
            drifted.write_bytes(raw + b" ")
            with mock.patch.object(tool, "PRIOR_RESULT_PATH", drifted), \
                    self.assertRaises(tool.RecoveryError):
                tool.validate_prior_evidence()

    def test_missing_prior_evidence_refuses(self):
        with mock.patch.object(tool, "PRIOR_ATTEMPT_PATH",
                               tool.PRIOR_PLAN_DIR / "absent.json"), \
                self.assertRaises(tool.RecoveryError):
            tool.validate_prior_evidence()

    def test_less_restrictive_prior_result_refuses(self):
        parsed = json.loads(tool.PRIOR_RESULT_PATH.read_text(encoding="utf-8"))
        for field, value in (("no_retry", False), ("status", "COMMITTED_AND_VERIFIED"),
                             ("stage", "upload_5"), ("rollback_performed", True),
                             ("delete_performed", True), ("product_may_have_changed", True),
                             ("current_upload_may_have_landed", False),
                             ("observed_attachment_id", 7610)):
            with self.subTest(field=field):
                mutated = deepcopy(parsed)
                mutated[field] = value
                raw = json.dumps(mutated, ensure_ascii=False, indent=2).encode("utf-8")
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "r.json"
                    path.write_bytes(raw)
                    with mock.patch.object(tool, "PRIOR_RESULT_PATH", path), \
                            mock.patch.object(tool, "PRIOR_RESULT_FILE_BYTES", len(raw)), \
                            mock.patch.object(tool, "PRIOR_RESULT_FILE_SHA256",
                                              hashlib.sha256(raw).hexdigest()), \
                            self.assertRaises(tool.RecoveryError):
                        tool.validate_prior_evidence()

    @staticmethod
    def all_prior_paths() -> list[Path]:
        """EVERY permanent artifact, not just the three headline files."""
        paths = [tool.PRIOR_PLAN_PATH, tool.PRIOR_RESULT_PATH, tool.PRIOR_ATTEMPT_PATH]
        paths += [tool.PRIOR_JOURNAL_DIR / name for name in tool.PRIOR_JOURNAL_FILES]
        return paths

    def prior_snapshot(self) -> dict[Path, bytes]:
        snapshot = {path: path.read_bytes() for path in self.all_prior_paths()}
        # The receipt is ONE line inside a shared append-only log; its bytes are
        # what must not move, so the whole file is snapshotted too.
        snapshot[REAL_RECEIPTS_PATH] = REAL_RECEIPTS_PATH.read_bytes()
        return snapshot

    def assert_prior_unchanged(self, snapshot: dict[Path, bytes]) -> None:
        for path, raw in snapshot.items():
            self.assertEqual(path.read_bytes(), raw, f"{path} changed")
        self.assertEqual(len(snapshot), 3 + len(tool.PRIOR_JOURNAL_FILES) + 1)

    def test_prior_evidence_is_never_opened_for_writing(self):
        opened: list[tuple[str, str]] = []
        real_open = Path.open

        def watched(self, mode="r", *args, **kwargs):  # noqa: ANN001
            opened.append((str(self), mode))
            return real_open(self, mode, *args, **kwargs)

        with mock.patch.object(Path, "open", watched):
            tool.validate_prior_evidence()
        prior = {str(path) for path in self.all_prior_paths()}
        self.assertFalse([row for row in opened
                          if row[0] in prior and "r" not in row[1]])

    def test_every_prior_artifact_is_byte_pinned_and_validated(self):
        evidence = tool.validate_prior_evidence()
        self.assertEqual(set(evidence["event_journal"]["files"]),
                         set(tool.PRIOR_JOURNAL_FILES))
        self.assertEqual(evidence["event_journal"]["count"],
                         len(tool.PRIOR_JOURNAL_FILES))
        self.assertEqual(evidence["receipt"]["occurrences"], 1)
        self.assertEqual(evidence["receipt"]["bytes"], tool.PRIOR_RECEIPT_BYTES)
        for name, (digest, byte_count) in tool.PRIOR_JOURNAL_FILES.items():
            raw = (tool.PRIOR_JOURNAL_DIR / name).read_bytes()
            self.assertEqual(len(raw), byte_count)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)

    def test_prior_evidence_stays_byte_identical_across_a_full_stage(self):
        before = self.prior_snapshot()
        self.stage_plan()
        self.assert_prior_unchanged(before)

    def test_prior_evidence_stays_byte_identical_across_a_successful_commit(self):
        before = self.prior_snapshot()
        path, _plan = self.stage_plan()
        resolved = {1: HERO_ID}
        self.commit(path, FakeAdmin(resolved), resolved)
        self.assert_prior_unchanged(before)

    def test_prior_evidence_stays_byte_identical_across_every_failure_branch(self):
        branches = {
            "refused_approval": dict(approval="approved", expect=tool.RecoveryError),
            "guard_acquire": dict(admin_kwargs={"fail_guard_acquire": True},
                                  expect=tool.RecoveryIndeterminate),
            "upload": dict(admin_kwargs={"fail_upload_at": 4},
                           expect=tool.RecoveryIndeterminate),
            "upload_timeout": dict(admin_kwargs={"upload_timeout_at": 2},
                                   expect=tool.RecoveryIndeterminate),
            "gallery_commit": dict(
                admin_kwargs={"fail_gallery_commit": RuntimeError("modelled")},
                expect=tool.RecoveryIndeterminate),
            "readback": dict(commit_kwargs={"readback_gallery_wrong": True},
                             expect=tool.RecoveryIndeterminate),
            "protected_drift": dict(commit_kwargs={"protected_drift": True},
                                    expect=tool.RecoveryIndeterminate),
            "guard_complete": dict(admin_kwargs={"fail_guard_complete": True},
                                   expect=tool.RecoveryIndeterminate),
            "public_verification": dict(admin_kwargs={"fail_public": True},
                                        expect=tool.RecoveryIndeterminate),
        }
        for label, spec in branches.items():
            with self.subTest(branch=label):
                self.tearDown()
                self.setUp()
                before = self.prior_snapshot()
                path, _plan = self.stage_plan()
                resolved = {1: HERO_ID}
                admin = FakeAdmin(resolved, **spec.get("admin_kwargs", {}))
                with self.assertRaises(spec["expect"]):
                    self.commit(path, admin, resolved,
                                approval=spec.get("approval", "APPROVED"),
                                **spec.get("commit_kwargs", {}))
                self.assert_prior_unchanged(before)

    def test_the_temporary_receipt_log_really_carries_the_permanent_line(self):
        """The seam v2.0.0 broke: an EMPTY receipt file validated nothing."""
        self.assertIsNotNone(PRIOR_RECEIPT_LINE)
        receipt = tool.validate_prior_receipt()
        self.assertEqual(receipt["occurrences"], 1)
        self.assertEqual(receipt["line_sha256"], tool.PRIOR_RECEIPT_SHA256)
        self.receipts.write_text("", encoding="utf-8")
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.validate_prior_receipt()
        self.assertIn("0 receipt lines", str(caught.exception))

    def test_prior_guard_must_have_reached_its_authoritative_expiry(self):
        expiry = tool.datetime.fromisoformat(tool.PRIOR_GUARD_EXPIRES_UTC)
        self.assertEqual(
            tool.require_prior_guard_released(expiry)["prior_guard_expired"], True)
        with self.assertRaises(tool.RecoveryError):
            tool.require_prior_guard_released(expiry - timedelta(seconds=1))

    def test_a_journal_entry_for_a_never_attempted_upload_refuses(self):
        with mock.patch.object(tool, "PRIOR_NEVER_ATTEMPTED_POSITIONS", (1, 3, 4, 5, 6)), \
                self.assertRaises(tool.RecoveryError):
            tool.validate_prior_evidence()


# ==========================================================================
# 2. Fixed sources
# ==========================================================================
class FixedSourceTests(RecoveryTestCase):
    def test_six_fixed_sources_are_exact_and_ordered(self):
        self.assertEqual(len(tool.FIXED_IMAGES), 6)
        self.assertEqual([row["position"] for row in tool.FIXED_IMAGES], [1, 2, 3, 4, 5, 6])
        self.assertEqual(list(tool.FIXED_FILENAMES), [
            "01_manway_premium_hero.png", "02_manway_top_oblique.png",
            "03_manway_low_side_angle.png", "04_manway_flange_bore_detail.png",
            "05_manway_opposite_face.png", "06_manway_laminate_detail.png"])
        self.assertEqual([row["bytes"] for row in tool.FIXED_IMAGES],
                         [1750111, 1821849, 1805796, 2118498, 1751997, 2347237])
        self.assertEqual(len({row["sha256"] for row in tool.FIXED_IMAGES}), 6)
        self.assertTrue(all(row["format"] == "PNG" and row["mode"] == "RGB"
                            for row in tool.FIXED_IMAGES))

    def test_every_pinned_source_file_verifies_on_disk(self):
        self.assertEqual(len(tool.validate_local_images()), 6)

    def test_byte_size_drift_refuses(self):
        mutated = tuple({**row, "bytes": row["bytes"] + 1} if row["position"] == 3 else row
                        for row in tool.FIXED_IMAGES)
        with mock.patch.object(tool, "FIXED_IMAGES", mutated), \
                self.assertRaises(tool.RecoveryError):
            tool.validate_local_images()

    def test_hash_drift_refuses(self):
        mutated = tuple({**row, "sha256": "0" * 64} if row["position"] == 4 else row
                        for row in tool.FIXED_IMAGES)
        with mock.patch.object(tool, "FIXED_IMAGES", mutated), \
                self.assertRaises(tool.RecoveryError):
            tool.validate_local_images()

    def test_dimension_drift_refuses(self):
        mutated = tuple({**row, "width": 999} if row["position"] == 5 else row
                        for row in tool.FIXED_IMAGES)
        with mock.patch.object(tool, "FIXED_IMAGES", mutated), \
                self.assertRaises(tool.RecoveryError):
            tool.validate_local_images()

    def test_an_arbitrary_path_is_unreachable(self):
        outsider = {**deepcopy(tool.FIXED_IMAGES[0]), "path": r"C:\Windows\notepad.exe"}
        mutated = (outsider,) + tool.FIXED_IMAGES[1:]
        with mock.patch.object(tool, "FIXED_IMAGES", mutated), \
                self.assertRaises(tool.RecoveryError):
            tool.validate_local_images()

    def test_a_missing_or_reparse_source_refuses(self):
        with mock.patch.object(tool.media_base, "_is_reparse_point", return_value=True), \
                self.assertRaises(tool.RecoveryError):
            tool.validate_local_images()

    def test_sources_must_agree_with_the_approved_family_record(self):
        drifted = deepcopy(tool.family_base.FAMILY_SPECS)
        rows = list(drifted["open_manway"]["images"])
        rows[2] = {**rows[2], "sha256": "1" * 64}
        drifted["open_manway"]["images"] = tuple(rows)
        with mock.patch.object(tool.family_base, "FAMILY_SPECS", drifted), \
                self.assertRaises(tool.RecoveryError):
            tool.validate_local_images()

    def test_a_seventh_source_is_unreachable(self):
        extra = {**deepcopy(tool.FIXED_IMAGES[-1]), "position": 7}
        with mock.patch.object(tool, "FIXED_IMAGES", tool.FIXED_IMAGES + (extra,)), \
                self.assertRaises(tool.RecoveryError):
            tool.validate_local_images()


# ==========================================================================
# 3. Reconciliation
# ==========================================================================
class ReconciliationTests(RecoveryTestCase):
    def test_position_one_must_resolve_to_the_fixed_hero_attachment(self):
        resolved = tool.resolve_positions(self.library_evidence({1: HERO_ID}))
        self.assertEqual(resolved, {1: HERO_ID})

    def test_position_one_absent_refuses(self):
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.resolve_positions(self.library_evidence({2: 7610}))
        self.assertIn("upload 1 no longer resolves", str(caught.exception))

    def test_position_one_under_a_different_attachment_id_refuses(self):
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.resolve_positions(self.library_evidence({1: 8888}))
        self.assertIn("not the recorded fixed attachment", str(caught.exception))

    def test_position_two_zero_match_becomes_one_upload(self):
        reconciliation = self.reconcile({1: HERO_ID})
        self.assertEqual(tool.upload_positions(reconciliation), [2, 3, 4, 5, 6])
        self.assertEqual(reconciliation[1]["disposition"], "upload_once")
        self.assertIsNone(reconciliation[1]["attachment_id"])

    def test_position_two_single_exact_match_is_reused(self):
        reconciliation = self.reconcile({1: HERO_ID, 2: 7610})
        self.assertEqual(reconciliation[1]["disposition"], "reuse_existing")
        self.assertEqual(reconciliation[1]["attachment_id"], 7610)
        self.assertEqual(tool.upload_positions(reconciliation), [3, 4, 5, 6])

    def test_position_two_duplicate_matches_refuse(self):
        evidence = self.library_evidence({1: HERO_ID, 2: 7610}, extra_hash_conflicts=[
            {"attachment_id": 7611,
             "matches_fixed_image": "open_manway:02_manway_top_oblique.png"}])
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.resolve_positions(evidence)
        self.assertIn("ambiguous", str(caught.exception))

    def test_positions_three_to_six_zero_one_and_many(self):
        self.assertEqual(tool.upload_positions(self.reconcile({1: HERO_ID})), [2, 3, 4, 5, 6])
        mixed = self.reconcile({1: HERO_ID, 3: 7620, 5: 7622})
        self.assertEqual(tool.reuse_positions(mixed), [1, 3, 5])
        self.assertEqual(tool.upload_positions(mixed), [2, 4, 6])
        evidence = self.library_evidence({1: HERO_ID, 6: 7630}, extra_hash_conflicts=[
            {"attachment_id": 7631,
             "matches_fixed_image": "open_manway:06_manway_laminate_detail.png"}])
        with self.assertRaises(tool.RecoveryError):
            tool.resolve_positions(evidence)

    def test_a_fixed_filename_without_the_approved_bytes_refuses(self):
        evidence = self.library_evidence({1: HERO_ID}, extra_name_conflicts=[
            {"attachment_id": 7777, "filename": "04_manway_flange_bore_detail.png"}])
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.resolve_positions(evidence)
        self.assertIn("without the approved bytes", str(caught.exception))

    def test_a_hash_conflict_naming_a_foreign_file_refuses(self):
        evidence = self.library_evidence({1: HERO_ID}, extra_hash_conflicts=[
            {"attachment_id": 7801, "matches_fixed_image": "stub_flange:01_authentic_source_hero.png"}])
        with self.assertRaises(tool.RecoveryError):
            tool.resolve_positions(evidence)

    def test_one_attachment_cannot_serve_two_positions(self):
        evidence = self.library_evidence({1: HERO_ID}, extra_hash_conflicts=[
            {"attachment_id": HERO_ID,
             "matches_fixed_image": "open_manway:05_manway_opposite_face.png"}])
        with self.assertRaises(tool.RecoveryError):
            tool.resolve_positions(evidence)

    def test_identity_is_never_inferred_from_filename_alone(self):
        """Only the hash-conflict list creates a resolution; names only refuse."""
        source = ast.get_source_segment(SOURCE_TEXT, next(
            node for node in ast.walk(SOURCE_TREE)
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_positions"))
        self.assertIn("by_position[POSITION_BY_FILENAME[filename]].append", source)
        self.assertIn("without the approved bytes", source)

    def test_a_resolved_attachment_stored_under_another_name_refuses(self):
        admin = FakeAdmin({1: HERO_ID})
        admin.id_to_name[HERO_ID] = "01_manway_premium_hero-1.png"
        with mock.patch.object(tool.media_base, "download_public_bytes",
                               side_effect=self.downloader()), \
                self.assertRaises(tool.RecoveryError):
            tool.verify_resolved_attachment(admin, 1, HERO_ID)

    def test_a_resolved_attachment_serving_wrong_bytes_refuses(self):
        admin = FakeAdmin({1: HERO_ID})
        with mock.patch.object(tool.media_base, "download_public_bytes",
                               return_value=b"not the approved png"), \
                self.assertRaises(tool.RecoveryError):
            tool.verify_resolved_attachment(admin, 1, HERO_ID)

    def test_final_ids_are_unique_and_ordered_one_to_six(self):
        reconciliation = self.reconcile({1: HERO_ID, 3: 7620})
        ids = tool.final_attachment_ids_for(reconciliation, {2: 91, 4: 92, 5: 93, 6: 94})
        self.assertEqual(ids, [HERO_ID, 91, 7620, 92, 93, 94])
        with self.assertRaises(tool.RecoveryError):
            tool.final_attachment_ids_for(reconciliation, {2: 91, 4: 92, 5: 93})
        with self.assertRaises(tool.RecoveryError):
            tool.final_attachment_ids_for(reconciliation, {2: 91, 4: 91, 5: 93, 6: 94})

    # helper
    def reconcile(self, resolved):
        admin = FakeAdmin(resolved)
        mapping = tool.resolve_positions(self.library_evidence(resolved))
        with mock.patch.object(tool.media_base, "download_public_bytes",
                               side_effect=self.downloader()):
            verified = {position: tool.verify_resolved_attachment(admin, position, attachment_id)
                        for position, attachment_id in mapping.items()}
        return tool.build_reconciliation(mapping, verified)


# ==========================================================================
# 4. Complete scan
# ==========================================================================
class CompleteScanTests(RecoveryTestCase):
    def test_a_complete_scan_is_accepted(self):
        tool.require_complete_library_evidence(self.library_evidence({1: HERO_ID}))

    def test_every_incompleteness_flag_refuses(self):
        for field in ("enumeration_complete", "hash_complete", "recheck_complete",
                      "snapshot_stable", "recheck_hash_complete", "content_stable",
                      "final_complete", "final_snapshot_stable",
                      "private_exception_proven", "complete"):
            with self.subTest(field=field), self.assertRaises(tool.RecoveryError):
                tool.require_complete_library_evidence(
                    self.library_evidence({1: HERO_ID}, overrides={field: False}))

    def test_sampling_is_not_a_result(self):
        evidence = self.library_evidence({1: HERO_ID},
                                         overrides={"image_rows": 40,
                                                    "image_hashes_completed": 12})
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.require_complete_library_evidence(evidence)
        self.assertIn("sampling is not a result", str(caught.exception))

    def test_an_unreadable_attachment_refuses(self):
        with self.assertRaises(tool.RecoveryError):
            tool.require_complete_library_evidence(
                self.library_evidence({1: HERO_ID}, overrides={"hash_failures": 1}))
        with self.assertRaises(tool.RecoveryError):
            tool.require_complete_library_evidence(
                self.library_evidence({1: HERO_ID}, overrides={"recheck_hash_failures": 1}))

    def test_a_partial_page_walk_refuses(self):
        with self.assertRaises(tool.RecoveryError):
            tool.require_complete_library_evidence(
                self.library_evidence({1: HERO_ID}, overrides={"pages_read": 0}))
        with self.assertRaises(tool.RecoveryError):
            tool.require_complete_library_evidence(
                self.library_evidence({1: HERO_ID}, overrides={"enumerated": 1}))

    def test_a_missing_private_exception_refuses(self):
        with self.assertRaises(tool.RecoveryError):
            tool.require_complete_library_evidence(
                self.library_evidence({1: HERO_ID}, overrides={"private_exception": None}))

    def test_a_foreign_target_family_refuses(self):
        with self.assertRaises(tool.RecoveryError):
            tool.require_complete_library_evidence(
                self.library_evidence({1: HERO_ID},
                                      overrides={"target_families": ["stub_flange"]}))


# ==========================================================================
# 5. Origin-only file collision
# ==========================================================================
class OriginProofTests(RecoveryTestCase):
    """The server-side origin-only proof. There is no public-URL probe any more.

    v1 invented public 404 probes against one hard-coded month directory and
    called that proof about the server filesystem. Guard 1.0.7 enumerates the
    uploads tree itself, classifies EACH discovered path with its OWN owner list,
    and fails closed; these tests drive that proof shape.
    """

    def reconciliation(self, resolved):
        return ReconciliationTests.reconcile(self, resolved)

    def test_absent_origin_files_allow_the_uploads(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        tool.require_origin_proof_matches(origin_proof_for({1: HERO_ID}), reconciliation)

    def test_an_origin_only_collision_refuses_with_the_exact_diagnosis(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        proof = origin_proof_for({1: HERO_ID})
        fixed = tool.FIXED_IMAGES[1]
        orphan = {"relative_path": f"2026/08/{fixed['filename']}", "bytes": fixed["bytes"],
                  "sha256": fixed["sha256"], "bytes_and_hash_exact": True,
                  "owner_attachment_ids": []}
        proof["files"][1].update({"discovered": 1, "paths": [orphan],
                                  "owner_attachment_ids": [],
                                  "bytes_and_hash_exact": True, "origin_only": True})
        proof["origin_only"] = [{"position": 2, "relative_path": orphan["relative_path"],
                                 "bytes_and_hash_exact": True,
                                 "owner_attachment_ids": []}]
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.require_origin_proof_matches(proof, reconciliation)
        self.assertIn("ORIGIN-ONLY FIXED-FILE COLLISION", str(caught.exception))
        self.assertIn("Nothing was staged", str(caught.exception))

    def test_two_discovered_copies_of_one_fixed_basename_refuse(self):
        """The exact case 1.0.6 reported clean by aggregating owners across paths."""
        reconciliation = self.reconciliation({1: HERO_ID})
        proof = origin_proof_for({1: HERO_ID})
        fixed = tool.FIXED_IMAGES[0]
        owned = proof["files"][0]["paths"][0]
        orphan = {"relative_path": fixed["filename"], "bytes": fixed["bytes"],
                  "sha256": fixed["sha256"], "bytes_and_hash_exact": True,
                  "owner_attachment_ids": []}
        proof["files"][0].update({"discovered": 2, "paths": [orphan, owned],
                                  "origin_only": True})
        proof["origin_only"] = [
            {"position": 1, "relative_path": row["relative_path"],
             "bytes_and_hash_exact": row["bytes_and_hash_exact"],
             "owner_attachment_ids": row["owner_attachment_ids"]}
            for row in (orphan, owned)]
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.require_origin_proof_matches(proof, reconciliation)
        self.assertIn("ORIGIN-ONLY FIXED-FILE COLLISION", str(caught.exception))

    def test_a_path_with_two_owners_is_never_reported_clean(self):
        proof = origin_proof_for({1: HERO_ID})
        proof["files"][0]["paths"][0]["owner_attachment_ids"] = [HERO_ID, HERO_ID + 1]
        proof["files"][0]["owner_attachment_ids"] = [HERO_ID, HERO_ID + 1]
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.validate_origin_proof(proof)
        self.assertIn("misreports its own blocker state", str(caught.exception))

    def test_owner_summary_must_agree_with_its_own_per_path_lists(self):
        proof = origin_proof_for({1: HERO_ID})
        proof["files"][0]["owner_attachment_ids"] = [HERO_ID + 5]
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.validate_origin_proof(proof)
        self.assertIn("per-path owner lists", str(caught.exception))

    def test_a_reused_position_whose_origin_file_is_absent_refuses(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        proof = origin_proof_for({})
        with self.assertRaises(tool.RecoveryError):
            tool.require_origin_proof_matches(proof, reconciliation)

    def test_an_origin_file_with_other_bytes_refuses(self):
        proof = origin_proof_for({1: HERO_ID})
        proof["files"][0]["paths"][0]["sha256"] = "b" * 64
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.validate_origin_proof(proof)
        self.assertIn("claims exact approved bytes", str(caught.exception))

    def test_an_unsafe_or_foreign_relative_path_refuses(self):
        for unsafe in ("../outside.png", "/2026/08/01_manway_premium_hero.png",
                       "2026/08/../01_manway_premium_hero.png",
                       "C:/uploads/01_manway_premium_hero.png",
                       "2026/08/something_else.png"):
            with self.subTest(path=unsafe):
                proof = origin_proof_for({1: HERO_ID})
                proof["files"][0]["paths"][0]["relative_path"] = unsafe
                with self.assertRaises(tool.RecoveryError):
                    tool.validate_origin_proof(proof)

    def test_only_the_six_fixed_basenames_are_covered_in_order(self):
        proof = origin_proof_for({1: HERO_ID})
        self.assertEqual([row["basename"] for row in proof["files"]],
                         list(tool.FIXED_FILENAMES))
        proof["files"][3]["basename"] = "anything_else.png"
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.validate_origin_proof(proof)
        self.assertIn("exactly the six fixed basenames", str(caught.exception))

    def test_an_incomplete_enumeration_refuses_rather_than_reading_as_absent(self):
        for overrides in ({"complete": False}, {"yearmonth_supported": False},
                          {"directories_scanned": 0}):
            with self.subTest(**overrides):
                proof = origin_proof_for({1: HERO_ID}, **overrides)
                with self.assertRaises(tool.RecoveryError):
                    tool.validate_origin_proof(proof)

    def test_an_unproven_enumeration_refuses_before_any_plan_or_attempt(self):
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        with self.live(admin, resolved), \
                mock.patch.object(FakeAdmin, "origin_proof",
                                  side_effect=tool.RecoveryError(
                                      "REFUSED: origin-only fixed-file absence is UNPROVEN.")), \
                self.assertRaises(tool.RecoveryError):
            tool.command_stage(argparse.Namespace())
        self.assertFalse(list(self.plan_dir.glob("*.json"))
                         if self.plan_dir.exists() else [])
        self.assertFalse((self.plan_dir / "attempt-ledger").exists())

    def test_the_module_has_no_public_url_origin_probe_left(self):
        for removed in ("origin_probe_evidence", "probe_origin_file", "origin_probe_url",
                        "validate_origin_probe"):
            self.assertFalse(hasattr(tool, removed),
                             f"{removed} is a removed v1 public-URL probe")


# ==========================================================================
# 6. Guard
# ==========================================================================
class GuardTests(RecoveryTestCase):
    def reconciliation(self, resolved):
        return ReconciliationTests.reconcile(self, resolved)

    def test_this_build_pins_guard_107_and_withdraws_106(self):
        """The pinned capability record, and the rejected artifact it replaced."""
        live = tool.GUARD_PARTIAL_RECOVERY_CAPABILITY
        self.assertEqual(live["plugin_version"], "1.0.7")
        self.assertEqual(tool.GUARD_PLUGIN_VERSION, "1.0.7")
        self.assertTrue(live["supports_existing_fixed_attachment_acquisition"])
        self.assertTrue(live["supports_non_prefix_upload_reservation"])
        self.assertTrue(live["supports_origin_only_file_enumeration"])
        self.assertTrue(live["supports_owner_bound_gallery_commit"])
        self.assertEqual(
            tool.WITHDRAWN_GUARD_ARTIFACTS,
            {"6a753c570d167075b8fa0a66349ab0a812aa7e222a7aedb2f6d374b913a7010e":
             "WITHDRAWN_NOT_DEPLOYED_NOT_STAGEABLE"})
        self.assertEqual(tool.WITHDRAWN_GUARD_PLUGIN_VERSIONS, ("1.0.6",))
        self.assertNotIn(tool.GUARD_ZIP_SHA256, tool.WITHDRAWN_GUARD_ARTIFACTS)
        self.assertTrue(str(tool.GUARD_ZIP_PATH).endswith("-1.0.7.zip"))

    def test_the_withdrawn_106_artifact_is_still_on_disk_and_unchanged(self):
        withdrawn = tool.GUARD_DIR / "frpdepot-media-mutation-guard-1.0.6.zip"
        self.assertTrue(withdrawn.is_file(), "the rejected 1.0.6 evidence must be kept")
        digest = hashlib.sha256(withdrawn.read_bytes()).hexdigest()
        self.assertIn(digest, tool.WITHDRAWN_GUARD_ARTIFACTS)
        self.assertEqual(tool.WITHDRAWN_GUARD_ARTIFACTS[digest],
                         "WITHDRAWN_NOT_DEPLOYED_NOT_STAGEABLE")

    def test_stage_refuses_before_any_plan_when_the_guard_cannot_serve_it(self):
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        incapable = {**self.capable(),
                     "supports_existing_fixed_attachment_acquisition": False}
        with self.live(admin, resolved), \
                mock.patch.object(tool, "GUARD_PARTIAL_RECOVERY_CAPABILITY", incapable), \
                self.assertRaises(tool.RecoveryError) as caught:
            tool.command_stage(argparse.Namespace())
        message = str(caught.exception)
        self.assertIn("cannot acquire a guard", message)
        self.assertIn("NOTHING WAS STAGED", message)
        self.assertFalse(self.plan_dir.exists() and list(self.plan_dir.glob("*.json")))

    def test_non_prefix_upload_reservation_refusal(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        capability = {**self.capable(), "supports_non_prefix_upload_reservation": False}
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.require_guard_supports_recovery(reconciliation, capability)
        self.assertIn("in-order prefix", str(caught.exception))

    def test_a_changed_plugin_forces_capability_rederivation(self):
        drifted = {**self.capable(), "plugin_php_sha256": "f" * 64}
        with mock.patch.object(tool, "GUARD_PARTIAL_RECOVERY_CAPABILITY", drifted), \
                self.assertRaises(tool.RecoveryError) as caught:
            tool.guard_capability()
        self.assertIn("re-derived", str(caught.exception))

    def test_guard_snapshot_must_name_exactly_the_reconciled_attachments(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        admin = FakeAdmin({1: HERO_ID})
        tool.require_recovery_guard_snapshot(
            admin.atomic_snapshot(tool.FAMILY_KEY), "atomic_snapshot", reconciliation)
        drifted = admin.snapshot("atomic_snapshot", False,
                                 matches=[{"attachment_id": 9999, "fixed_position": 1}])
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.require_recovery_guard_snapshot(drifted, "atomic_snapshot", reconciliation)
        self.assertIn("disagree with the browser-side reconciliation", str(caught.exception))

    def test_an_active_guard_at_stage_time_refuses(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        admin = FakeAdmin({1: HERO_ID})
        active = admin.snapshot("atomic_snapshot", True)
        with self.assertRaises(tool.RecoveryError):
            tool.require_recovery_guard_snapshot(active, "atomic_snapshot", reconciliation)

    def test_an_incomplete_or_poisoned_guard_snapshot_refuses(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        admin = FakeAdmin({1: HERO_ID})
        for overrides in ({"complete": False},
                          {"failures": [{"attachment_id": 5, "reason": "unreadable_original"}],
                           "attachment_total": len(admin.library_rows()) + 1}):
            with self.subTest(overrides=sorted(overrides)):
                with self.assertRaises(tool.RecoveryError):
                    tool.require_recovery_guard_snapshot(
                        admin.snapshot("atomic_snapshot", False, overrides=overrides),
                        "atomic_snapshot", reconciliation)

    def test_a_wrong_plugin_version_or_schema_refuses(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        admin = FakeAdmin({1: HERO_ID})
        for overrides in ({"plugin_version": "1.0.4"}, {"schema": 1},
                          {"family": "stub_flange"}):
            with self.subTest(overrides=sorted(overrides)):
                with self.assertRaises(tool.RecoveryError):
                    tool.require_recovery_guard_snapshot(
                        admin.snapshot("atomic_snapshot", False, overrides=overrides),
                        "atomic_snapshot", reconciliation)

    def test_a_newly_acquired_guard_that_already_reserves_refuses(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        admin = FakeAdmin({1: HERO_ID})
        with self.assertRaises(tool.RecoveryError):
            tool.require_recovery_guard_snapshot(
                admin.snapshot("guard_acquired", True, reserved=2),
                "guard_acquired", reconciliation)

    def test_browser_and_server_totals_must_agree(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        admin = FakeAdmin({1: HERO_ID})
        library = self.library_evidence({1: HERO_ID})
        tool.require_recovery_guard_snapshot(
            admin.atomic_snapshot(tool.FAMILY_KEY), "atomic_snapshot",
            reconciliation, library)
        with self.assertRaises(tool.RecoveryError):
            tool.require_recovery_guard_snapshot(
                admin.snapshot("atomic_snapshot", False, total=99), "atomic_snapshot",
                reconciliation, library)

    def test_guard_completion_margin_refuses_a_nearly_expired_guard(self):
        proof = {"guard_expires_utc": (tool.utc_now() + timedelta(seconds=30)).isoformat()}
        with self.assertRaises(tool.RecoveryError):
            tool.require_guard_completion_margin(proof)


# ==========================================================================
# 7. Product identity, gallery drift, already-complete
# ==========================================================================
class ProductTests(RecoveryTestCase):
    def test_stage_records_the_full_current_gallery_and_protected_fingerprint(self):
        _path, plan = self.stage_plan()
        self.assertEqual([row["id"] for row in plan["product_before"]["before_gallery"]],
                         [6991, 6992, 6993, 6994])
        projection = plan["product_before"]["protected_projection"]
        self.assertEqual(plan["product_before"]["protected_fingerprint"],
                         hashlib.sha256(tool.canonical(projection).encode("utf-8")).hexdigest())
        for field in ("price", "sku", "status", "stock_status", "meta_data", "categories"):
            self.assertIn(field, projection)
        self.assertNotIn("images", projection)

    def test_a_wrong_product_identity_refuses(self):
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        patches, _ = self.live_patches(admin, resolved)
        bad = self.product_record()
        bad["name"] = "FRP MANWAY COVER"
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            stack.enter_context(mock.patch.object(tool.wc, "api_get",
                                                  return_value=(bad, {})))
            with self.assertRaises(tool.RecoveryError):
                tool.command_stage(argparse.Namespace())

    def test_unrelated_gallery_drift_is_accepted(self):
        _path, plan = self.stage_plan(gallery=[
            {"id": 5551, "alt": "", "src": "https://frpdepots.com/x.png"}])
        self.assertEqual([row["id"] for row in plan["product_before"]["before_gallery"]],
                         [5551])

    def test_a_partially_applied_recovery_gallery_refuses(self):
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        with self.live(admin, resolved, gallery=[
                {"id": HERO_ID, "alt": "", "src": public_url(tool.FIXED_FILENAMES[0])},
                {"id": 6992, "alt": "", "src": "https://frpdepots.com/b.png"}]), \
                self.assertRaises(tool.RecoveryError) as caught:
            tool.command_stage(argparse.Namespace())
        self.assertIn("partial or conflicting order", str(caught.exception))

    def test_the_live_state_is_read_again_while_the_guard_is_held(self):
        """`read_guarded_live_state` is a REAL fresh read of the guarded world."""
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        with self.live(admin, resolved):
            vault = tool.wc.load_vault()
            before = tool.read_guarded_live_state(admin, vault)
            self.assertEqual(tool.upload_positions(before["reconciliation"]),
                             [2, 3, 4, 5, 6])
            admin.upload_one(tool.FIXED_IMAGES[1], set())
            after = tool.read_guarded_live_state(admin, vault)
        self.assertEqual(tool.upload_positions(after["reconciliation"]), [3, 4, 5, 6])
        self.assertEqual(after["reconciliation"][1]["disposition"], "reuse_existing")
        self.assertNotEqual(before["library"]["library_total"],
                            after["library"]["library_total"])
        self.assertIn("guard_health:Guard active", admin.events)

    def test_already_complete_reports_no_plan_and_no_write(self):
        resolved = {position: 7600 + position for position in range(1, 7)}
        resolved[1] = HERO_ID
        gallery = [{"id": resolved[position], "alt": "",
                    "src": public_url(tool.FIXED_FILENAMES[position - 1])}
                   for position in range(1, 7)]
        admin = FakeAdmin(resolved)
        with self.live(admin, resolved, gallery=gallery) as state:
            with mock.patch.object(tool, "emit") as emitted:
                tool.command_stage(argparse.Namespace())
        payload = emitted.call_args[0][0]
        self.assertEqual(payload["status"], "VERIFIED_ALREADY_COMPLETE")
        self.assertIsNone(payload["plan"])
        self.assertEqual(payload["website_writes"], 0)
        self.assertEqual(state["write_calls"], [])
        self.assertFalse(self.plan_dir.exists() and list(self.plan_dir.glob("*.json")))

    def test_a_stale_plan_gallery_refuses_before_the_attempt_lock(self):
        path, _plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        with self.assertRaises(tool.RecoveryError):
            self.commit(path, admin, resolved, gallery=[
                {"id": 7001, "alt": "", "src": "https://frpdepots.com/z.png"}])
        self.assertFalse((self.plan_dir / "attempt-ledger").exists())


# ==========================================================================
# 8. Plan contract
# ==========================================================================
class PlanTests(RecoveryTestCase):
    def test_a_plan_from_the_withdrawn_200_build_can_never_commit(self):
        path, plan = self.stage_plan()
        core = {key: value for key, value in plan.items() if key != "sha256"}
        core["tool_version"] = "2.0.0"
        path.write_text(json.dumps({**core, "sha256": tool.digest_for(core)}, indent=2),
                        encoding="utf-8")
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.load_plan(path, authenticate_registry=False)
        self.assertIn("superseded build", str(caught.exception))

    def test_plan_identity_expiry_and_canonical_name(self):
        path, plan = self.stage_plan()
        self.assertEqual(plan["schema_version"], tool.SCHEMA_VERSION)
        self.assertEqual(plan["schema_version"], 3)
        self.assertEqual(plan["tool_version"], "2.1.0")
        self.assertEqual(plan["action"], "recover_fixed_open_manway_gallery")
        self.assertNotIn("method", plan)
        self.assertNotIn("endpoint", plan)
        self.assertEqual(plan["write_transport"]["method"], "POST")
        self.assertEqual(plan["read_transport"]["method"], "GET")
        created = tool.datetime.fromisoformat(plan["created_utc"])
        expires = tool.datetime.fromisoformat(plan["expires_utc"])
        self.assertEqual(expires - created, timedelta(hours=24))
        self.assertEqual(path.name,
                         tool.canonical_plan_filename(created, plan["sha256"]))
        self.assertEqual(path.parent, self.plan_dir)

    def test_plan_carries_the_superseded_hashes(self):
        _path, plan = self.stage_plan()
        self.assertEqual(plan["superseded"]["plan_sha256"], tool.PRIOR_PLAN_SHA256)
        self.assertEqual(plan["superseded"]["operation_sha256"], tool.PRIOR_OPERATION_SHA256)
        self.assertEqual(plan["superseded"]["result_file_sha256"],
                         tool.PRIOR_RESULT_FILE_SHA256)
        self.assertEqual(plan["superseded"]["attempt_file_sha256"],
                         tool.PRIOR_ATTEMPT_FILE_SHA256)
        self.assertFalse(plan["superseded"]["mutated_by_this_tool"])
        self.assertFalse(plan["superseded"]["retryable"])

    def test_any_plan_mutation_refuses(self):
        path, plan = self.stage_plan()
        original = path.read_bytes()
        for field, value in (("product_id", 1411), ("endpoint", "/products/1411"),
                             ("method", "POST"), ("tool_version", "9.9.9"),
                             ("schema_version", 2), ("origin", "https://example.com"),
                             ("origin_probe_directory", "/wp-content/uploads/2026/09/")):
            with self.subTest(field=field):
                mutated = deepcopy(plan)
                mutated[field] = value
                path.write_text(json.dumps(mutated, indent=2), encoding="utf-8")
                with self.assertRaises(tool.RecoveryError):
                    tool.load_plan(path)
        path.write_bytes(original)
        tool.load_plan(path)

    def test_an_extra_or_missing_plan_field_refuses(self):
        path, plan = self.stage_plan()
        extra = {**deepcopy(plan), "extra": 1}
        path.write_text(json.dumps(extra, indent=2), encoding="utf-8")
        with self.assertRaises(tool.RecoveryError):
            tool.load_plan(path)
        missing = deepcopy(plan)
        missing.pop("risk")
        path.write_text(json.dumps(missing, indent=2), encoding="utf-8")
        with self.assertRaises(tool.RecoveryError):
            tool.load_plan(path)

    def test_an_expired_plan_refuses(self):
        path, plan = self.stage_plan()
        old = tool.utc_now() - timedelta(hours=25)
        core = {key: value for key, value in plan.items() if key != "sha256"}
        core["created_utc"] = old.isoformat()
        core["expires_utc"] = (old + timedelta(hours=24)).isoformat()
        path.write_text(json.dumps({**core, "sha256": tool.digest_for(core)}, indent=2),
                        encoding="utf-8")
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.load_plan(path, authenticate_registry=False)
        self.assertIn("expired", str(caught.exception))

    def test_a_plan_outside_the_fixed_folder_refuses(self):
        path, _plan = self.stage_plan()
        outside = Path(self.tmp.name) / path.name
        outside.write_bytes(path.read_bytes())
        with self.assertRaises(tool.RecoveryError):
            tool.fixed_plan_path(str(outside))

    def test_a_missing_stage_registry_refuses(self):
        path, plan = self.stage_plan()
        tool.stage_registry_path(plan["sha256"]).unlink()
        with self.assertRaises(tool.RecoveryError):
            tool.load_plan(path)

    def test_a_forged_stage_registry_mac_refuses(self):
        path, plan = self.stage_plan()
        registry_path = tool.stage_registry_path(plan["sha256"])
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["hmac_sha256"] = "0" * 64
        registry_path.unlink()
        registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
        tool.load_plan(path, authenticate_registry=False)
        with self.assertRaises(tool.RecoveryError):
            tool.load_plan(path, authenticate_registry=True)

    def test_plan_states_its_write_surface_and_forbidden_routes(self):
        _path, plan = self.stage_plan()
        self.assertIn("NOT ATOMIC; NO ROLLBACK", plan["risk"])
        self.assertIn("ORIGIN-FILE PROOF SCOPE", plan["risk"])
        self.assertIn("read-only permanent evidence", plan["risk"])
        for banned in ("delete", "trash", "detach", "rename", "retry", "rollback",
                       "email", "second PUT", "guard clearing"):
            self.assertIn(banned, plan["forbidden"])
        self.assertTrue(any("owner-bound guard admin-post gallery commit" in row
                            for row in plan["writes_if_committed"]))

    def test_plan_names_its_read_and_write_transports_separately_and_truthfully(self):
        _path, plan = self.stage_plan()
        self.assertEqual(plan["read_transport"], tool.READ_TRANSPORT)
        self.assertEqual(plan["write_transport"], tool.WRITE_TRANSPORT)
        # The WRITE is the owner-bound admin-post form, never a Woo REST route.
        self.assertEqual(plan["write_transport"]["kind"], "guard_owned_admin_post_form")
        self.assertEqual(plan["write_transport"]["action"],
                         tool.GUARD_RECOVERY_GALLERY_ACTION)
        self.assertEqual(plan["write_transport"]["caller_supplied_fields"],
                         ["action", "_wpnonce", "if_match"])
        self.assertNotIn("/products/1397", json.dumps(plan["write_transport"]))
        # The READ is declared for what it is, not hidden behind a "no REST" claim.
        self.assertEqual(plan["read_transport"]["method"], "GET")
        self.assertIs(plan["read_transport"]["performs_writes"], False)
        self.assertEqual(plan["read_transport"]["routes"], ["/products/1397"])
        self.assertIn("read-only WooCommerce GETs", plan["risk"])
        proof = plan["guard_contract"]["static_transport_proof"]
        self.assertEqual(proof["woocommerce_write_primitives_reachable"], [])
        self.assertEqual(proof["woocommerce_read_calls"], ["wc.api_get"])

    def test_plan_records_the_exact_reuse_and_upload_split(self):
        _path, plan = self.stage_plan()
        self.assertEqual(plan["reuse_positions"], [1])
        self.assertEqual(plan["upload_positions"], [2, 3, 4, 5, 6])
        self.assertEqual([row["disposition"] for row in plan["reconciliation"]],
                         ["reuse_existing"] + ["upload_once"] * 5)
        self.assertEqual(plan["reconciliation"][0]["attachment_id"], HERO_ID)


# ==========================================================================
# 9. Approval, lane lock, attempt lock ordering
# ==========================================================================
class ApprovalAndLockOrderTests(RecoveryTestCase):
    def test_only_the_byte_exact_approval_word_is_accepted(self):
        tool.require_approval("APPROVED")
        for value in ("approved", "Approved", " APPROVED", "APPROVED ", "APPROVED\n",
                      "APPROVE", "", None, 1, "APPROVED APPROVED"):
            with self.subTest(value=repr(value)), self.assertRaises(tool.RecoveryError):
                tool.require_approval(value)

    def test_approval_is_checked_before_any_vault_browser_or_network(self):
        path, _plan = self.stage_plan()
        vault = mock.Mock(side_effect=AssertionError("vault must not be read"))
        lock = mock.Mock(side_effect=AssertionError("browser must not be locked"))
        session = mock.Mock(side_effect=AssertionError("browser must not be opened"))
        network = mock.Mock(side_effect=AssertionError("network must not be reached"))
        with mock.patch.object(tool.wc, "load_vault", vault), \
                mock.patch.object(tool, "ui_browser_lock", lock), \
                mock.patch.object(tool, "admin_session", session), \
                mock.patch.object(tool.media_base, "download_public_bytes", network), \
                self.assertRaises(tool.RecoveryError):
            tool.command_commit(argparse.Namespace(plan=str(path), approval="approved"))
        vault.assert_not_called()
        lock.assert_not_called()
        session.assert_not_called()
        network.assert_not_called()
        self.assertFalse((self.plan_dir / "attempt-ledger").exists())

    def test_a_busy_browser_is_a_free_refusal_with_no_attempt_lock(self):
        from ui_lane_lock import UiLaneBusy

        path, _plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        patches, _ = self.live_patches(admin, resolved)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            stack.enter_context(mock.patch.object(
                tool, "ui_browser_lock",
                side_effect=UiLaneBusy("the WordPress browser is busy")))
            with self.assertRaises(UiLaneBusy):
                tool.command_commit(
                    argparse.Namespace(plan=str(path), approval="APPROVED"))
        self.assertFalse((self.plan_dir / "attempt-ledger").exists())
        self.assertEqual(admin.guard_acquire_count, 0)

    def test_the_real_runtime_order_is_lock_then_attempt_then_every_side_effect(self):
        """RUNTIME order, not source order: v2.0.0 asserted on substring positions.

        A source-substring assertion cannot see a call that moved into a helper,
        and it happily passed against a symbol the module no longer had. This
        records the actual sequence of observed events during one whole commit.
        """
        path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        order: list[str] = []
        real_lock = tool.ui_browser_lock
        real_publish = tool.publish_attempt_lock

        def traced_lock(*args, **kwargs):
            order.append("browser_lock")
            return contextlib.nullcontext()

        def traced_publish(*args, **kwargs):
            order.append("attempt_lock")
            return real_publish(*args, **kwargs)

        patches, _state = self.live_patches(admin, resolved)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            stack.enter_context(mock.patch.object(tool, "ui_browser_lock", traced_lock))
            stack.enter_context(
                mock.patch.object(tool, "publish_attempt_lock", traced_publish))
            original_events = admin.events
            admin.events = order
            try:
                tool.command_commit(
                    argparse.Namespace(plan=str(path), approval="APPROVED"))
            finally:
                admin.events = original_events
        side_effects = [event for event in order if event in (
            "browser_lock", "attempt_lock", "acquire_guard", "upload_2", "upload_3",
            "upload_4", "upload_5", "upload_6", "commit_gallery", "complete_guard",
            "verify_public_product")]
        self.assertEqual(side_effects, [
            "browser_lock", "attempt_lock", "acquire_guard",
            "upload_2", "upload_3", "upload_4", "upload_5", "upload_6",
            "commit_gallery", "complete_guard", "verify_public_product"])
        # Nothing that could touch the website happens between the two locks.
        between = order[order.index("browser_lock") + 1:order.index("attempt_lock")]
        for banned in ("acquire_guard", "upload_2", "commit_gallery", "complete_guard"):
            self.assertNotIn(banned, between)
        self.assertTrue(tool.lock_path(plan["operation_sha256"]).is_file())

    def test_a_deterministic_preflight_failure_leaves_no_attempt_lock(self):
        path, _plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        library = self.library_evidence(resolved, overrides={"hash_complete": False})
        with self.assertRaises(tool.RecoveryError):
            self.commit(path, admin, resolved, library=library)
        self.assertFalse((self.plan_dir / "attempt-ledger").exists())
        self.assertEqual(admin.guard_acquire_count, 0)
        self.assertEqual(admin.upload_positions_seen, [])

    def test_replay_of_the_same_operation_refuses(self):
        path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        self.commit(path, FakeAdmin(resolved), resolved)
        with self.assertRaises(tool.RecoveryError) as caught:
            self.commit(path, FakeAdmin(resolved), resolved)
        self.assertIn("permanent no-retry", str(caught.exception))

    def test_the_recovery_owns_state_directories_the_old_operation_cannot_reach(self):
        self.assertNotEqual(DEFAULT_DIRS["PLAN_DIR"], str(tool.family_base.PLAN_DIR))
        self.assertNotEqual(DEFAULT_REGISTRY_KEY_PATH,
                            str(tool.family_base.REGISTRY_KEY_PATH))
        for name, value in DEFAULT_DIRS.items():
            with self.subTest(name=name):
                self.assertIn("open_manway_gallery_recovery", value.replace("\\", "/"))
        self.assertIn("open-manway-gallery-recovery",
                      DEFAULT_REGISTRY_KEY_PATH.replace("\\", "/"))


# ==========================================================================
# 10. Commit behaviour
# ==========================================================================
class CommitTests(RecoveryTestCase):
    def test_a_clean_commit_uploads_only_the_missing_files_in_order(self):
        path, _plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        state = self.commit(path, admin, resolved)
        self.assertEqual(admin.upload_positions_seen, [2, 3, 4, 5, 6])
        self.assertEqual(admin.uploads, list(tool.FIXED_FILENAMES[1:]))
        # The gallery write is the ONE owner-bound admin-post commit, and no
        # WooCommerce write call happens at all.
        self.assertEqual(admin.gallery_commit_count, 1)
        self.assertEqual(state["write_calls"], [])
        ids = admin.gallery_committed_ids
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(set(ids)), 6)
        self.assertEqual(ids[0], HERO_ID)

    def test_an_already_live_position_is_reused_and_never_re_uploaded(self):
        resolved = {1: HERO_ID, 2: 7610}
        path, plan = self.stage_plan(resolved)
        self.assertEqual(plan["upload_positions"], [3, 4, 5, 6])
        admin = FakeAdmin(resolved)
        state = self.commit(path, admin, resolved)
        self.assertEqual(admin.upload_positions_seen, [3, 4, 5, 6])
        self.assertNotIn("02_manway_top_oblique.png", admin.uploads)
        self.assertEqual(state["write_calls"], [])
        self.assertEqual(admin.gallery_committed_ids[:2], [HERO_ID, 7610])

    def test_the_caller_sends_nothing_but_the_if_match_hash(self):
        """No product, family, attachment ID, filename, path or URL leaves here."""
        path, _plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        sent: list[object] = []
        real = FakeAdmin.commit_recovery_gallery

        def traced(self_admin, if_match, on_submit_attempt=None):
            sent.append(if_match)
            return real(self_admin, if_match, on_submit_attempt)

        with mock.patch.object(FakeAdmin, "commit_recovery_gallery", traced):
            state = self.commit(path, admin, resolved)
        self.assertEqual(len(sent), 1)
        self.assertEqual(state["write_calls"], [])
        for banned in ("1397", "open_manway", ".png", "http", "/products"):
            self.assertNotIn(banned, str(sent[0]))

    def test_the_commit_is_conditional_on_the_exact_pre_write_gallery(self):
        path, _plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        sent: list[str] = []
        real = FakeAdmin.commit_recovery_gallery

        def traced(self_admin, if_match, on_submit_attempt=None):
            sent.append(if_match)
            return real(self_admin, if_match, on_submit_attempt)

        with mock.patch.object(FakeAdmin, "commit_recovery_gallery", traced):
            self.commit(path, admin, resolved)
        expected = '"' + hashlib.sha256(
            tool.canonical([6991, 6992, 6993, 6994]).encode("ascii")).hexdigest() + '"'
        self.assertEqual(sent, [expected])
        self.assertEqual(len(sent[0]), 66)

    def test_a_failed_upload_makes_no_product_put_and_locks_permanently(self):
        path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved, fail_upload_at=4)
        with self.assertRaises(tool.RecoveryIndeterminate):
            self.commit(path, admin, resolved)
        result = json.loads(
            tool.result_path(plan["operation_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "INDETERMINATE_NO_RETRY")
        self.assertEqual(result["stage"], "upload_4")
        self.assertTrue(result["no_retry"])
        self.assertTrue(result["current_upload_may_have_landed"])
        self.assertFalse(result["product_may_have_changed"])
        self.assertIsNone(result["gallery_payload"])
        self.assertFalse(result["rollback_performed"])
        self.assertFalse(result["delete_performed"])
        self.assertFalse(result["prior_evidence_mutated"])
        self.assertEqual(result["emails"], 0)
        self.assertEqual([row["position"] for row in result["uploaded_verified"]], [2, 3])
        self.assertEqual(admin.upload_positions_seen, [2, 3, 4])

    def test_an_upload_timeout_is_permanently_indeterminate_with_ordered_events(self):
        path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved, upload_timeout_at=2)
        with self.assertRaises(tool.RecoveryIndeterminate):
            self.commit(path, admin, resolved)
        journal = sorted(
            entry.name for entry in
            (self.plan_dir / "event-journal" / plan["operation_sha256"]).glob("*.json"))
        self.assertEqual(journal, ["050_guard_acquired.json",
                                   "060_guard_owner_verified.json",
                                   "990_indeterminate.json"])
        result = json.loads(
            tool.result_path(plan["operation_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(result["reason"], "TimeoutError")
        self.assertEqual(result["stage"], "upload_2")
        self.assertTrue(result["guard_may_be_active"])

    def test_a_failed_gallery_put_records_that_the_product_may_have_changed(self):
        path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        with self.assertRaises(tool.RecoveryIndeterminate):
            self.commit(path, FakeAdmin(
                resolved, fail_gallery_commit=RuntimeError("modelled commit failure")),
                resolved)
        result = json.loads(
            tool.result_path(plan["operation_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "gallery_commit_or_readback")
        self.assertTrue(result["product_may_have_changed"])
        self.assertTrue(result["no_retry"])

    def test_a_gallery_readback_mismatch_is_permanently_indeterminate(self):
        path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        with self.assertRaises(tool.RecoveryIndeterminate):
            self.commit(path, FakeAdmin(resolved), resolved, readback_gallery_wrong=True)
        result = json.loads(
            tool.result_path(plan["operation_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "gallery_commit_or_readback")
        self.assertTrue(result["product_may_have_changed"])

    def test_a_protected_field_change_during_the_put_refuses(self):
        path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        with self.assertRaises(tool.RecoveryIndeterminate):
            self.commit(path, FakeAdmin(resolved), resolved, protected_drift=True)
        result = json.loads(
            tool.result_path(plan["operation_sha256"]).read_text(encoding="utf-8"))
        self.assertIn("protected product field", result["message"])

    def test_a_guard_completion_failure_is_permanently_indeterminate(self):
        path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved, fail_guard_complete=True)
        with self.assertRaises(tool.RecoveryIndeterminate):
            self.commit(path, admin, resolved)
        result = json.loads(
            tool.result_path(plan["operation_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "guard_completion")
        self.assertTrue(result["guard_may_be_active"])
        self.assertTrue(result["product_may_have_changed"])

    def test_a_guard_acquisition_failure_is_permanently_indeterminate(self):
        path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved, fail_guard_acquire=True)
        with self.assertRaises(tool.RecoveryIndeterminate):
            self.commit(path, admin, resolved)
        result = json.loads(
            tool.result_path(plan["operation_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "guard_acquisition")
        self.assertEqual(result["uploaded_verified"], [])
        self.assertEqual(admin.upload_positions_seen, [])

    def test_a_verified_commit_records_the_terminal_result(self):
        path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        self.commit(path, FakeAdmin(resolved), resolved)
        result = json.loads(
            tool.result_path(plan["operation_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "COMMITTED_AND_VERIFIED")
        self.assertTrue(result["replay_locked"])
        self.assertTrue(result["no_retry"])
        self.assertFalse(result["rollback_performed"])
        self.assertFalse(result["delete_performed"])
        self.assertFalse(result["prior_evidence_mutated"])
        self.assertEqual(result["emails"], 0)
        self.assertEqual(result["superseded"]["operation_sha256"],
                         tool.PRIOR_OPERATION_SHA256)
        self.assertEqual([row["id"] for row in result["gallery"]],
                         [HERO_ID] + [9001, 9002, 9003, 9004, 9005])

    def test_result_status_vocabulary_is_closed(self):
        self.assertEqual(tool.RESULT_STATUSES,
                         ("COMMITTED_AND_VERIFIED", "FAILED_CLOSED", "INDETERMINATE_NO_RETRY"))
        statuses = {node.value for node in ast.walk(SOURCE_TREE)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.isupper() and node.value.endswith(("VERIFIED", "RETRY"))}
        self.assertTrue(statuses <= {"COMMITTED_AND_VERIFIED", "INDETERMINATE_NO_RETRY",
                                     "VERIFIED_ALREADY_COMPLETE"})


# ==========================================================================
# 11. The one eligibility predicate
# ==========================================================================
class EligibilityPredicateTests(RecoveryTestCase):
    def test_one_predicate_is_used_at_stage_preflight_and_every_side_effect(self):
        calls: list[str] = []
        real = tool.assert_recovery_eligibility

        def counted(**kwargs):
            calls.append("call")
            return real(**kwargs)

        path, _plan = self.stage_plan()
        stage_calls = len(calls)
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        with mock.patch.object(tool, "assert_recovery_eligibility", counted):
            self.commit(path, admin, resolved)
        self.assertEqual(stage_calls, 0)
        # preflight + guard acquire + 5 uploads + pre-PUT + the PUT itself
        self.assertGreaterEqual(len(calls), 9)

    def test_the_predicate_is_the_only_eligibility_gate_in_the_module(self):
        names = {node.func.id for node in ast.walk(SOURCE_TREE)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertIn("assert_recovery_eligibility", names)
        self.assertFalse({name for name in names
                          if name.startswith("assert_") and name.endswith("_eligibility")
                          and name != "assert_recovery_eligibility"})

    def test_every_adapter_side_effect_runs_the_predicate_first(self):
        """RUNTIME order again: every side effect is preceded by a predicate call."""
        path, _plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        order: list[str] = []
        real = tool.assert_recovery_eligibility

        def traced(**kwargs):
            order.append("predicate")
            return real(**kwargs)

        patches, _state = self.live_patches(admin, resolved)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            stack.enter_context(
                mock.patch.object(tool, "assert_recovery_eligibility", traced))
            admin.events = order
            tool.command_commit(argparse.Namespace(plan=str(path), approval="APPROVED"))
        for effect in ("acquire_guard", "upload_2", "upload_6", "commit_gallery",
                       "complete_guard"):
            index = order.index(effect)
            self.assertEqual(order[index - 1], "predicate",
                             f"{effect} must be immediately preceded by the predicate")

    def test_the_predicate_accepts_expected_progress_after_every_upload(self):
        """The defect that made v2.0.0 permanently stuck after upload 1.

        The staged reconciliation is the immutable ACQUISITION BASELINE. After N
        uploads the live world legitimately differs from it, and the predicate
        must accept exactly that difference and no other.
        """
        _path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        baseline = plan["reconciliation"]
        with self.live(admin, resolved):
            vault = tool.wc.load_vault()
            for step, position in enumerate([2, 3, 4, 5, 6]):
                live = tool.read_guarded_live_state(admin, vault)
                owner = admin.guarded_snapshot(tool.FAMILY_KEY)
                progress = tool.guard_progress(owner)
                self.assertEqual(progress["completed_positions"],
                                 [2, 3, 4, 5, 6][:step])
                self.assertEqual(progress["remaining_positions"],
                                 [2, 3, 4, 5, 6][step:])
                tool.assert_recovery_eligibility(
                    prior_evidence=tool.validate_prior_evidence(),
                    local=tool.validate_local_images(),
                    capability=tool.guard_capability(),
                    library=live["library"], product=live["product"],
                    reconciliation=live["reconciliation"],
                    origin_proof=live["origin_proof"],
                    expected_product=plan["product_before"],
                    expected_reconciliation=baseline,
                    guard_owner=owner,
                    completed_upload_positions=[2, 3, 4, 5, 6][:step],
                    next_upload_position=position)
                admin.upload_one(tool.FIXED_IMAGES[position - 1], set())

    def test_an_unexplained_attachment_at_a_future_position_is_external_drift(self):
        _path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        with self.live(admin, resolved):
            vault = tool.wc.load_vault()
            admin.upload_one(tool.FIXED_IMAGES[1], set())
            # Somebody ELSE puts the approved bytes for position 5 into the
            # library. The guard's durable record cannot explain it.
            admin.resolved[5] = 7777
            admin.id_to_name[7777] = tool.FIXED_IMAGES[4]["filename"]
            live = tool.read_guarded_live_state(admin, vault)
            owner = admin.guarded_snapshot(tool.FAMILY_KEY)
            with self.assertRaises(tool.RecoveryError) as caught:
                tool.assert_recovery_eligibility(
                    prior_evidence=tool.validate_prior_evidence(),
                    local=tool.validate_local_images(),
                    capability=tool.guard_capability(),
                    library=live["library"], product=live["product"],
                    reconciliation=live["reconciliation"],
                    origin_proof=live["origin_proof"],
                    expected_product=plan["product_before"],
                    expected_reconciliation=plan["reconciliation"],
                    guard_owner=owner, completed_upload_positions=[2],
                    next_upload_position=3)
        self.assertIn("external drift", str(caught.exception))

    def test_a_disappeared_binding_refuses_before_the_next_side_effect(self):
        _path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        with self.live(admin, resolved):
            vault = tool.wc.load_vault()
            admin.upload_one(tool.FIXED_IMAGES[1], set())
            owner = admin.guarded_snapshot(tool.FAMILY_KEY)
            # The guard still binds position 2; the library no longer holds it.
            admin.guard.bound.clear()
            live = tool.read_guarded_live_state(admin, vault)
            admin.guard.bind(2, 9001)
            with self.assertRaises(tool.RecoveryError):
                tool.assert_recovery_eligibility(
                    prior_evidence=tool.validate_prior_evidence(),
                    local=tool.validate_local_images(),
                    capability=tool.guard_capability(),
                    library=live["library"], product=live["product"],
                    reconciliation=live["reconciliation"],
                    origin_proof=live["origin_proof"],
                    expected_product=plan["product_before"],
                    expected_reconciliation=plan["reconciliation"],
                    guard_owner=owner, completed_upload_positions=[2],
                    next_upload_position=3)

    def test_one_unbound_reservation_stops_everything(self):
        """An in-flight upload with an ambiguous outcome may never be built on."""
        _path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        with self.live(admin, resolved):
            vault = tool.wc.load_vault()
            admin.guard.reserve(2)           # reserved, never bound
            live = tool.read_guarded_live_state(admin, vault)
            owner = admin.guarded_snapshot(tool.FAMILY_KEY)
            self.assertEqual(tool.guard_progress(owner)["unbound_reservation"],
                             {"position": 2,
                              "filename": tool.FIXED_IMAGES[1]["filename"]})
            with self.assertRaises(tool.RecoveryError) as caught:
                tool.assert_recovery_eligibility(
                    prior_evidence=tool.validate_prior_evidence(),
                    local=tool.validate_local_images(),
                    capability=tool.guard_capability(),
                    library=live["library"], product=live["product"],
                    reconciliation=live["reconciliation"],
                    origin_proof=live["origin_proof"],
                    expected_product=plan["product_before"],
                    expected_reconciliation=plan["reconciliation"],
                    guard_owner=owner, completed_upload_positions=[],
                    next_upload_position=2)
        self.assertIn("reserved-but-unbound", str(caught.exception))

    def test_the_journal_and_the_durable_record_must_agree(self):
        _path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        with self.live(admin, resolved):
            vault = tool.wc.load_vault()
            admin.upload_one(tool.FIXED_IMAGES[1], set())
            live = tool.read_guarded_live_state(admin, vault)
            owner = admin.guarded_snapshot(tool.FAMILY_KEY)
            with self.assertRaises(tool.RecoveryError) as caught:
                tool.assert_recovery_eligibility(
                    prior_evidence=tool.validate_prior_evidence(),
                    local=tool.validate_local_images(),
                    capability=tool.guard_capability(),
                    library=live["library"], product=live["product"],
                    reconciliation=live["reconciliation"],
                    origin_proof=live["origin_proof"],
                    expected_product=plan["product_before"],
                    expected_reconciliation=plan["reconciliation"],
                    guard_owner=owner, completed_upload_positions=[],
                    next_upload_position=3)
        self.assertIn("durable record binds", str(caught.exception))
        self.assertIn("[2]", str(caught.exception))

    def test_a_gallery_payload_requires_all_six_bindings(self):
        _path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        with self.live(admin, resolved):
            vault = tool.wc.load_vault()
            admin.upload_one(tool.FIXED_IMAGES[1], set())
            live = tool.read_guarded_live_state(admin, vault)
            owner = admin.guarded_snapshot(tool.FAMILY_KEY)
            with self.assertRaises(tool.RecoveryError) as caught:
                tool.assert_recovery_eligibility(
                    prior_evidence=tool.validate_prior_evidence(),
                    local=tool.validate_local_images(),
                    capability=tool.guard_capability(),
                    library=live["library"], product=live["product"],
                    reconciliation=live["reconciliation"],
                    origin_proof=live["origin_proof"],
                    expected_product=plan["product_before"],
                    expected_reconciliation=plan["reconciliation"],
                    guard_owner=owner, completed_upload_positions=[2],
                    final_attachment_ids=[HERO_ID, 9001, 1, 2, 3, 4])
        self.assertIn("all six bindings complete", str(caught.exception))
        self.assertIn("[3, 4, 5, 6]", str(caught.exception))

    def test_missing_subsets_from_zero_through_five_all_reconcile(self):
        """0..5 missing positions, including position 2 already live."""
        # Nothing missing at all is not a progression, it is "nothing to recover":
        # the durable record itself is refused, because position 1 must be bound
        # and at least one position must remain to upload.
        everything_live = {1: HERO_ID}
        everything_live.update({position: 7700 + position
                                for position in range(2, 7)})
        with self.assertRaises(tool.RecoveryError):
            tool.validate_guard_recovery_record(
                FakeGuard(everything_live).projection())

        for already in ([], [2], [2, 3], [3, 5], [2, 4, 6]):
            with self.subTest(already_live=already):
                resolved = {1: HERO_ID}
                for offset, position in enumerate(already):
                    resolved[position] = 7700 + offset
                guard = FakeGuard(resolved)
                expected_missing = [position for position in tool.POSITIONS
                                    if position not in resolved]
                self.assertEqual(guard.missing_positions, expected_missing)
                record = tool.validate_guard_recovery_record(guard.projection())
                self.assertEqual(record["missing_positions"], expected_missing)
                self.assertEqual(record["remaining_positions"], expected_missing)
                for step, position in enumerate(expected_missing):
                    guard.reserve(position)
                    guard.bind(position, 8800 + position)
                    progress = tool.guard_progress(
                        {"recovery": guard.projection(),
                         "guard_expires_utc": (
                             tool.utc_now() + timedelta(minutes=20)).isoformat()})
                    self.assertEqual(progress["completed_positions"],
                                     expected_missing[:step + 1])
                    self.assertEqual(progress["remaining_positions"],
                                     expected_missing[step + 1:])

    def test_a_reconciliation_change_after_staging_refuses(self):
        path, _plan = self.stage_plan()
        resolved = {1: HERO_ID, 3: 7620}
        admin = FakeAdmin(resolved)
        with self.assertRaises(tool.RecoveryError) as caught:
            self.commit(path, admin, resolved)
        self.assertIn("external drift", str(caught.exception))
        self.assertFalse((self.plan_dir / "attempt-ledger").exists())

    def test_the_predicate_refuses_a_reordered_gallery_payload(self):
        reconciliation = ReconciliationTests.reconcile(self, {1: HERO_ID, 2: 7610})
        kwargs = dict(prior_evidence=tool.validate_prior_evidence(),
                      local=tool.validate_local_images(),
                      capability=tool.guard_capability(),
                      library=self.library_evidence({1: HERO_ID, 2: 7610}),
                      product=self.product_evidence_for(),
                      reconciliation=reconciliation,
                      origin_proof=origin_proof_for({1: HERO_ID, 2: 7610}))
        tool.assert_recovery_eligibility(
            **kwargs, final_attachment_ids=[HERO_ID, 7610, 11, 12, 13, 14])
        for bad in ([7610, HERO_ID, 11, 12, 13, 14],
                    [HERO_ID, 99, 11, 12, 13, 14],
                    [HERO_ID, 7610, 11, 12, 13],
                    [HERO_ID, 7610, 11, 12, 13, 13]):
            with self.subTest(bad=bad), self.assertRaises(tool.RecoveryError):
                tool.assert_recovery_eligibility(**kwargs, final_attachment_ids=bad)


# ==========================================================================
# 11b. The PHP <-> Python proof contract
# ==========================================================================
class GuardProofContractTests(RecoveryTestCase):
    """The producer/consumer schema check 1.0.6 never had.

    `media_mutation_guard/test_media_mutation_guard_recovery_lifecycle.php` drives
    the real plugin through acquisition, guarded progress, five reserved uploads,
    the owner-bound gallery commit, the completion proof and the terminal
    `completed` transition, and publishes the EXACT proofs it produced. These
    tests validate that file with the production validators, so a producer that
    emits a shape this consumer rejects is an executable failure. 1.0.6's producer
    emitted schema 2 while this consumer pinned 3, and nothing caught it.
    """

    @classmethod
    def setUpClass(cls):
        if not tool.GUARD_PROOF_CONTRACT_PATH.is_file():
            raise unittest.SkipTest(
                "run test_media_mutation_guard_recovery_lifecycle.php to publish the "
                "guard proof contract")
        cls.contract = json.loads(
            tool.GUARD_PROOF_CONTRACT_PATH.read_text(encoding="utf-8"))
        cls.final_ids = [7609, 7700, 7803, 7704, 7805, 7806]

    def test_the_contract_was_produced_by_the_pinned_plugin_build(self):
        self.assertEqual(self.contract["plugin_version"], tool.GUARD_PLUGIN_VERSION)
        self.assertEqual(self.contract["state_schema"], tool.GUARD_STATE_SCHEMA)
        self.assertEqual(self.contract["proof_schema"], tool.GUARD_PROOF_SCHEMA)
        self.assertEqual(self.contract["produced_by"],
                         "test_media_mutation_guard_recovery_lifecycle.php")

    def test_the_real_capability_projection_is_the_pinned_one(self):
        self.assertEqual(tool.validate_live_capability(self.contract["capability"]),
                         tool.GUARD_EXPECTED_CAPABILITY)

    def test_the_real_origin_only_proof_validates(self):
        proof = tool.validate_origin_proof(self.contract["origin_only_proof"])
        self.assertEqual(proof["origin_only"], [])
        self.assertEqual([row["basename"] for row in proof["files"]],
                         list(tool.FIXED_FILENAMES))

    def test_the_real_acquisition_proof_validates(self):
        proof = tool.validate_guard_snapshot_proof(
            self.contract["guard_acquired"], "guard_acquired", True)
        record = tool.validate_guard_recovery_record(proof["recovery"])
        self.assertEqual(record["missing_positions"], [2, 3, 4, 5, 6])
        self.assertEqual(record["remaining_positions"], [2, 3, 4, 5, 6])
        self.assertEqual(proof["reserved_uploads"], 0)
        self.assertEqual(proof["attachment_total"], 364)
        tool.validate_origin_proof(proof["origin_only_proof"])

    def test_every_real_guarded_progress_snapshot_validates(self):
        names = ["guarded_snapshot_initial"] + [
            f"guarded_snapshot_after_{position}" for position in (2, 3, 4, 5, 6)]
        for step, name in enumerate(names):
            with self.subTest(snapshot=name):
                proof = tool.validate_guard_snapshot_proof(
                    self.contract[name], "guarded_snapshot", True)
                progress = tool.guard_progress(proof)
                self.assertEqual(progress["completed_positions"],
                                 [2, 3, 4, 5, 6][:step])
                self.assertEqual(progress["remaining_positions"],
                                 [2, 3, 4, 5, 6][step:])
                self.assertIsNone(progress["unbound_reservation"])
                self.assertEqual(proof["attachment_total"], 364 + step)

    def test_the_real_gallery_commit_proof_validates(self):
        proof = self.contract["recovery_gallery_committed"]
        checked = tool.validate_recovery_gallery_proof(
            proof, self.final_ids, proof["gallery_etag_before"])
        self.assertEqual(checked["transport"],
                         "internal_authenticated_rest_do_request")
        self.assertEqual(checked["state_status"], "gallery")

    def test_the_real_completion_proof_validates(self):
        checked = tool.validate_guard_completion_proof(
            self.contract["guard_completed"], self.final_ids)
        self.assertEqual(checked["attachment_ids"], self.final_ids)
        self.assertEqual([row["position"] for row in checked["attachment_identities"]],
                         list(tool.POSITIONS))
        self.assertEqual(checked["attachment_total"], 369)

    def test_the_real_proofs_carry_no_secret_or_absolute_path(self):
        serialized = json.dumps(self.contract)
        for banned in ("nonce", "cookie", "session", "C:\\", "/home/", "wp-content"):
            self.assertNotIn(banned, serialized)


# ==========================================================================
# 12. Static capability scan
# ==========================================================================
class StaticCapabilityTests(RecoveryTestCase):
    def test_no_woocommerce_write_primitive_is_reachable_at_all(self):
        """The write is the owner-bound admin-post form; reads are GET only."""
        proof = tool.assert_no_woocommerce_write_primitive()
        self.assertEqual(proof["woocommerce_write_primitives_reachable"], [])
        self.assertEqual(proof["woocommerce_read_calls"], ["wc.api_get"])
        callees = {node.func.attr for node in ast.walk(SOURCE_TREE)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        for banned in tool.FORBIDDEN_WRITE_PRIMITIVES:
            self.assertNotIn(banned, callees)
        gets = [node for node in ast.walk(SOURCE_TREE)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "api_get"]
        self.assertTrue(gets)
        for node in gets:
            self.assertIsInstance(node.args[0], ast.JoinedStr)

    def test_a_planted_write_primitive_makes_the_static_proof_fail(self):
        """The proof is a real parse, so it must fail on a real planted call."""
        planted = SOURCE_TEXT.replace(
            "readback, _ = wc.api_get(f\"/products/{PRODUCT_ID}\", vault=vault)",
            "readback, _ = wc.api_request(\"PUT\", f\"/products/{PRODUCT_ID}\")", 1)
        self.assertNotEqual(planted, SOURCE_TEXT)
        forged = Path(self.tmp.name) / "forged_tool.py"
        forged.write_text(planted, encoding="utf-8")
        with mock.patch.object(tool, "__file__", str(forged)), \
                mock.patch.dict(tool._TRANSPORT_PROOF_CACHE, {}, clear=True), \
                self.assertRaises(tool.RecoveryError) as caught:
            tool.assert_no_woocommerce_write_primitive()
        self.assertIn("api_request", str(caught.exception))

    def test_no_delete_patch_or_post_product_verb_exists(self):
        callees = {node.func.attr for node in ast.walk(SOURCE_TREE)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        for banned in ("delete", "post", "patch", "put", "api_request"):
            self.assertNotIn(banned, callees)

    def test_no_mail_transport_of_any_kind(self):
        upper = SOURCE_TEXT.upper()
        for banned in ("SMTPLIB", "MAIL.SEND", "SEND_MAIL", "SENDMAIL", "EMAIL.MIME",
                       "WP_MAIL", "MAILTO:"):
            self.assertNotIn(banned, upper)

    def test_no_delete_detach_rename_or_cleanup_route(self):
        forbidden_fragments = (
            "delete_attachment", "wp_delete", "force_delete", "delete_permanently",
            "detach_attachment", "rename_attachment", "cleanup_media",
            "action=delete", "action=trash", ".unlink(", "shutil.rmtree",
            "os.remove(", "delete permanently",
        )
        lowered = SOURCE_TEXT.lower()
        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, lowered, fragment)
        # Those words exist in this module only as refusals, never as a route.
        for word in ("delete", "trash", "detach", "rename"):
            self.assertIn(word, tool.FORBIDDEN)
        self.assertNotIn(chr(34) + "delete_performed" + chr(34) + ": true", lowered)

    def test_no_credential_cookie_or_storage_read(self):
        for banned in (".cookies(", "document.cookie", "storage_state", "_wpnonce =",
                       "localStorage", "sessionStorage"):
            self.assertNotIn(banned, SOURCE_TEXT)

    def test_no_generic_browser_or_adhoc_endpoint_route(self):
        for banned in ("page.evaluate(", "page.click(", "admin-ajax.php",
                       "wp-json/wp/v2", "def run_javascript", "def navigate("):
            self.assertNotIn(banned, SOURCE_TEXT)

    def test_the_module_never_writes_to_the_superseded_plan_folder(self):
        writers = [node for node in ast.walk(SOURCE_TREE)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "write_json"]
        self.assertTrue(writers)
        source = SOURCE_TEXT
        self.assertNotIn("family_base.PLAN_DIR", source)
        self.assertNotIn("family_base.RESULT_DIR", source)
        self.assertNotIn("family_base.ATTEMPT_LEDGER_DIR", source)
        self.assertNotIn("family_base.lock_path", source)
        self.assertNotIn("family_base.result_path", source)

    def test_only_the_fixed_six_paths_are_uploadable(self):
        self.assertEqual(len(tool.FIXED_PATHS), 6)
        self.assertTrue(all(path.parent == tool.SOURCE_DIR.resolve()
                            for path in tool.FIXED_PATHS))
        # RUNTIME proof: every upload the commit path makes comes from the six
        # fixed records, matched to the guard's own immutable missing positions.
        path, _plan = self.stage_plan()
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        seen: list[dict[str, object]] = []
        real = FakeAdmin.upload_one

        def traced(self_admin, expected, known_ids, on_submit_attempt=None):
            seen.append(expected)
            return real(self_admin, expected, known_ids, on_submit_attempt)

        with mock.patch.object(FakeAdmin, "upload_one", traced):
            self.commit(path, admin, resolved)
        self.assertEqual([row["position"] for row in seen], [2, 3, 4, 5, 6])
        for row in seen:
            self.assertIn(row, tool.FIXED_IMAGES)
            self.assertIn(Path(row["path"]).resolve(), tool.FIXED_PATHS)

    def test_only_stage_and_commit_exist_with_no_free_form_input(self):
        parser = tool.build_parser()
        choices = next(action for action in parser._actions
                       if isinstance(action, argparse._SubParsersAction)).choices
        self.assertEqual(set(choices), {"stage", "commit"})
        stage_options = {option for action in choices["stage"]._actions
                         for option in action.option_strings}
        commit_options = {option for action in choices["commit"]._actions
                          for option in action.option_strings}
        self.assertEqual(stage_options, {"-h", "--help"})
        self.assertEqual(commit_options, {"-h", "--help", "--plan", "--approval"})

    def test_the_product_id_and_family_are_constants(self):
        self.assertEqual(tool.PRODUCT_ID, 1397)
        self.assertEqual(tool.FAMILY_KEY, "open_manway")
        self.assertEqual(tool.PRODUCT_LABEL, "FRP MANWAY")
        endpoints = {node.value for node in ast.walk(SOURCE_TREE)
                     if isinstance(node, ast.Constant) and isinstance(node.value, str)
                     and node.value.startswith("/products")}
        self.assertEqual(endpoints, {"/products/"})

    def test_guard_completion_precedes_public_page_verification(self):
        self.assertLess(SOURCE_TEXT.index("guard_completion = adapter.complete_guard"),
                        SOURCE_TEXT.index("public_verification = admin.verify_public_product"))

    def test_main_scrubs_a_refusal_and_returns_one(self):
        with mock.patch.object(sys, "argv", [SOURCE_PATH.name, "stage"]), \
                mock.patch.object(tool, "validate_prior_evidence",
                                  side_effect=tool.RecoveryError("refused")), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(tool.main(), 1)
        self.assertIn("ERROR: refused", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
