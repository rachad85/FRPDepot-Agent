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


def source_bytes(position: int) -> bytes:
    return Path(tool.FIXED_IMAGES[position - 1]["path"]).read_bytes()


def public_url(filename: str) -> str:
    return f"https://frpdepots.com/wp-content/uploads/2026/08/{filename}"


class FakeAdmin:
    """Models exactly the adapter surface the recovery tool is allowed to touch."""

    def __init__(self, resolved: dict[int, int], *, fail_upload_at: int | None = None,
                 upload_timeout_at: int | None = None, fail_guard_acquire: bool = False,
                 fail_guard_complete: bool = False, fail_public: bool = False,
                 guard_expires_minutes: int = 30):
        self.resolved = dict(resolved)
        self.fail_upload_at = fail_upload_at
        self.upload_timeout_at = upload_timeout_at
        self.fail_guard_acquire = fail_guard_acquire
        self.fail_guard_complete = fail_guard_complete
        self.fail_public = fail_public
        self.guard_expires_utc = (
            tool.utc_now() + timedelta(minutes=guard_expires_minutes)).isoformat()
        self.uploads: list[str] = []
        self.upload_positions_seen: list[int] = []
        self.guard_acquire_count = 0
        self.guard_complete_count = 0
        self.next_upload_id = 9000
        self.id_to_name: dict[int, str] = {
            attachment_id: tool.FIXED_IMAGES[position - 1]["filename"]
            for position, attachment_id in self.resolved.items()
        }
        self.left = False

    # -- reads -----------------------------------------------------------
    def library_rows(self) -> list[dict[str, object]]:
        rows = [{"id": PRIVATE_ID, "filename": PRIVATE_NAME, "stem": "private"}]
        for position, attachment_id in sorted(self.resolved.items()):
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

    # -- guard -----------------------------------------------------------
    def snapshot(self, mode, active, *, total=None, matches=None, reserved=0,
                 overrides=None):
        matched = matches if matches is not None else sorted(
            ({"attachment_id": attachment_id, "fixed_position": position}
             for position, attachment_id in self.resolved.items()),
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
            "fixed_matches": deepcopy(matched), "guard_active": active,
        }
        if active:
            value["guard_expires_utc"] = self.guard_expires_utc
            value["reserved_uploads"] = reserved
        value.update(overrides or {})
        return value

    def atomic_snapshot(self, key):
        return self.snapshot("atomic_snapshot", False)

    def prepare_guard_acquire(self, key):
        return object()

    def acquire_prepared_guard(self, key, button, on_submit_attempt=None):
        self.guard_acquire_count += 1
        if on_submit_attempt is not None:
            on_submit_attempt()
        if self.fail_guard_acquire:
            raise RuntimeError("modelled guard acquisition failure")
        return self.snapshot("guard_acquired", True, reserved=0)

    def guarded_snapshot(self, key):
        if not self.uploads:
            return self.snapshot("guarded_snapshot", True, reserved=0)
        matched = sorted(
            ({"attachment_id": attachment_id, "fixed_position": position}
             for position, attachment_id in self.final_map().items()),
            key=lambda row: row["fixed_position"])
        return self.snapshot("guarded_snapshot", True,
                             total=len(self.library_rows()) + len(self.uploads),
                             matches=matched, reserved=len(self.uploads))

    def final_map(self) -> dict[int, int]:
        mapping = dict(self.resolved)
        for attachment_id, name in self.id_to_name.items():
            mapping[tool.POSITION_BY_FILENAME[name]] = attachment_id
        return mapping

    def complete_guard(self, key, attachment_ids, on_submit_attempt=None):
        self.guard_complete_count += 1
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
                "attachment_total": len(self.library_rows()) + len(self.uploads),
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
        if on_submit_attempt is not None:
            on_submit_attempt()
        if self.upload_timeout_at == position:
            raise TimeoutError("modelled upload timeout")
        if self.fail_upload_at == position:
            raise RuntimeError("modelled upload failure")
        self.next_upload_id += 1
        self.id_to_name[self.next_upload_id] = expected["filename"]
        self.uploads.append(expected["filename"])
        return self.next_upload_id

    def verify_public_product(self, key, verified_source_urls):
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
        self.patchers = [
            mock.patch.object(tool, "PLAN_DIR", self.plan_dir),
            mock.patch.object(tool, "STAGE_REGISTRY_DIR", self.plan_dir / "stage-registry"),
            mock.patch.object(tool, "ATTEMPT_LEDGER_DIR", self.plan_dir / "attempt-ledger"),
            mock.patch.object(tool, "RESERVATION_DIR", self.plan_dir / "result-reservations"),
            mock.patch.object(tool, "RESULT_DIR", self.plan_dir / "results"),
            mock.patch.object(tool, "JOURNAL_DIR", self.plan_dir / "event-journal"),
            mock.patch.object(tool, "REGISTRY_KEY_PATH", Path(self.tmp.name) / "registry.key"),
            mock.patch.object(tool, "RECEIPTS", self.receipts),
            mock.patch.object(tool, "GUARD_PARTIAL_RECOVERY_CAPABILITY", self.capable()),
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
        """A guard that COULD serve this recovery. The installed one cannot."""
        return {**deepcopy(LIVE_CAPABILITY),
                "supports_existing_fixed_attachment_acquisition": True,
                "supports_non_prefix_upload_reservation": True}

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
    def probes_for(resolved: dict[int, int]):
        rows = []
        for fixed in tool.FIXED_IMAGES:
            if fixed["position"] in resolved:
                rows.append({"filename": fixed["filename"],
                             "url": public_url(fixed["filename"]), "state": "present",
                             "http_status": 200, "bytes": fixed["bytes"],
                             "sha256": fixed["sha256"]})
            else:
                rows.append({"filename": fixed["filename"],
                             "url": public_url(fixed["filename"]), "state": "absent",
                             "http_status": 404, "bytes": None, "sha256": None})
        return rows

    @staticmethod
    def downloader():
        by_name = {row["filename"]: Path(row["path"]).read_bytes()
                   for row in tool.FIXED_IMAGES}

        def download(url, expected_basename=None, allowed_extensions=None):
            name = expected_basename or Path(str(url)).name
            return by_name[name]
        return download

    def live_patches(self, admin, resolved, *, gallery=None, library=None,
                     probes=None, product_drift=None, put_error=None,
                     readback_gallery_wrong=False, protected_drift=False):
        before = self.product_record(gallery)
        state = {"put": False, "payload": None, "requests": [], "gets": 0,
                 "if_match": None}

        def api_get(endpoint, params=None, vault=None):
            state["gets"] += 1
            record = deepcopy(before)
            if product_drift is not None and state["gets"] == product_drift:
                record["price"] = "999.00"
            if state["put"]:
                record["images"] = ([{"id": 1}] if readback_gallery_wrong
                                    else deepcopy(state["payload"]["images"]))
                if protected_drift:
                    record["price"] = "777.00"
            return record, {}

        def api_request(method, endpoint, *, params=None, payload=None, vault=None,
                        timeout=60, if_match=None):
            state["requests"].append((method, endpoint, deepcopy(payload)))
            state["if_match"] = if_match
            if put_error is not None:
                raise put_error
            state["put"] = True
            state["payload"] = deepcopy(payload)
            response = deepcopy(before)
            response["images"] = deepcopy(payload["images"])
            return response, {}

        patches = [
            mock.patch.object(tool.wc, "load_vault", return_value={
                "declared_permissions": "read_write", "site_url": tool.EXACT_ORIGIN,
                "consumer_key": "hidden", "consumer_secret": "hidden"}),
            mock.patch.object(tool.wc, "api_get", side_effect=api_get),
            mock.patch.object(tool.wc, "api_request", side_effect=api_request),
            mock.patch.object(tool, "admin_session",
                              side_effect=lambda allowed: contextlib.nullcontext(admin)),
            mock.patch.object(tool, "ui_browser_lock",
                              side_effect=lambda *a, **k: contextlib.nullcontext()),
            mock.patch.object(tool.family_base, "duplicate_scan",
                              return_value=(library if library is not None
                                            else self.library_evidence(resolved))),
            mock.patch.object(tool.media_base, "download_public_bytes",
                              side_effect=self.downloader()),
            mock.patch.object(tool, "origin_probe_evidence",
                              return_value=(probes if probes is not None
                                            else self.probes_for(resolved))),
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

    def stage_plan(self, resolved=None, **kwargs):
        resolved = {1: HERO_ID} if resolved is None else resolved
        admin = FakeAdmin(resolved)
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

    def test_prior_evidence_is_never_opened_for_writing(self):
        opened: list[tuple[str, str]] = []
        real_open = Path.open

        def watched(self, mode="r", *args, **kwargs):  # noqa: ANN001
            opened.append((str(self), mode))
            return real_open(self, mode, *args, **kwargs)

        with mock.patch.object(Path, "open", watched):
            tool.validate_prior_evidence()
        prior = {str(tool.PRIOR_PLAN_PATH), str(tool.PRIOR_RESULT_PATH),
                 str(tool.PRIOR_ATTEMPT_PATH)}
        self.assertFalse([row for row in opened
                          if row[0] in prior and "r" not in row[1]])

    def test_prior_evidence_stays_byte_identical_across_a_full_stage(self):
        before = {path: path.read_bytes() for path in
                  (tool.PRIOR_PLAN_PATH, tool.PRIOR_RESULT_PATH, tool.PRIOR_ATTEMPT_PATH)}
        self.stage_plan()
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)

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
class OriginProbeTests(RecoveryTestCase):
    def reconciliation(self, resolved):
        return ReconciliationTests.reconcile(self, resolved)

    def test_absent_origin_files_allow_the_uploads(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        tool.validate_origin_probe(self.probes_for({1: HERO_ID}), reconciliation)

    def test_an_origin_only_collision_refuses_with_the_exact_diagnosis(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        probes = self.probes_for({1: HERO_ID})
        fixed = tool.FIXED_IMAGES[1]
        probes[1] = {"filename": fixed["filename"], "url": public_url(fixed["filename"]),
                     "state": "present", "http_status": 200, "bytes": fixed["bytes"],
                     "sha256": fixed["sha256"]}
        with self.assertRaises(tool.RecoveryError) as caught:
            tool.validate_origin_probe(probes, reconciliation)
        self.assertIn("ORIGIN-ONLY FIXED-FILE COLLISION", str(caught.exception))
        self.assertIn("Nothing was staged", str(caught.exception))

    def test_a_reused_position_whose_origin_file_is_absent_refuses(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        probes = self.probes_for({})
        probes[0] = {"filename": tool.FIXED_FILENAMES[0],
                     "url": public_url(tool.FIXED_FILENAMES[0]), "state": "absent",
                     "http_status": 404, "bytes": None, "sha256": None}
        with self.assertRaises(tool.RecoveryError):
            tool.validate_origin_probe(probes, reconciliation)

    def test_an_origin_file_with_other_bytes_refuses(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        probes = self.probes_for({1: HERO_ID})
        probes[0]["sha256"] = "b" * 64
        with self.assertRaises(tool.RecoveryError):
            tool.validate_origin_probe(probes, reconciliation)

    def test_a_probe_of_a_foreign_url_refuses(self):
        reconciliation = self.reconciliation({1: HERO_ID})
        probes = self.probes_for({1: HERO_ID})
        probes[0]["url"] = "https://frpdepots.com/wp-content/uploads/2025/01/x.png"
        with self.assertRaises(tool.RecoveryError):
            tool.validate_origin_probe(probes, reconciliation)

    def test_only_the_six_fixed_filenames_are_probeable(self):
        with self.assertRaises(tool.RecoveryError):
            tool.origin_probe_url("anything_else.png")

    def test_a_clean_404_proves_absence(self):
        error = HTTPError(public_url(tool.FIXED_FILENAMES[1]), 404, "Not Found", {}, None)
        with mock.patch.object(tool, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            probe = tool.probe_origin_file(tool.FIXED_FILENAMES[1])
        self.assertEqual(probe["state"], "absent")
        self.assertEqual(probe["http_status"], 404)

    def test_an_unprovable_probe_refuses_rather_than_reading_as_absent(self):
        for failure in (HTTPError(public_url(tool.FIXED_FILENAMES[1]), 403, "no", {}, None),
                        HTTPError(public_url(tool.FIXED_FILENAMES[1]), 500, "no", {}, None),
                        URLError("no route")):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(tool, "build_opener") as opener:
                    opener.return_value.open.side_effect = failure
                    with self.assertRaises(tool.RecoveryError) as caught:
                        tool.probe_origin_file(tool.FIXED_FILENAMES[1])
                self.assertIn("unproven", str(caught.exception))

    def test_an_unprovable_probe_refuses_before_any_plan_or_attempt(self):
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        with self.live(admin, resolved) as _state, \
                mock.patch.object(tool, "origin_probe_evidence",
                                  side_effect=tool.RecoveryError("REFUSED: unproven")), \
                self.assertRaises(tool.RecoveryError):
            tool.command_stage(argparse.Namespace())
        self.assertFalse(list(self.plan_dir.glob("*.json"))
                         if self.plan_dir.exists() else [])
        self.assertFalse((self.plan_dir / "attempt-ledger").exists())


# ==========================================================================
# 6. Guard
# ==========================================================================
class GuardTests(RecoveryTestCase):
    def reconciliation(self, resolved):
        return ReconciliationTests.reconcile(self, resolved)

    def test_installed_guard_cannot_serve_this_recovery(self):
        """The live capability record, not the test seam."""
        live = LIVE_CAPABILITY
        self.assertFalse(live["supports_existing_fixed_attachment_acquisition"])
        self.assertFalse(live["supports_non_prefix_upload_reservation"])
        self.assertFalse(live["supports_origin_only_file_enumeration"])
        self.assertEqual(live["plugin_version"], "1.0.5")

    def test_stage_refuses_before_any_plan_under_the_installed_guard(self):
        resolved = {1: HERO_ID}
        admin = FakeAdmin(resolved)
        with self.live(admin, resolved), \
                mock.patch.object(tool, "GUARD_PARTIAL_RECOVERY_CAPABILITY",
                                  deepcopy(LIVE_CAPABILITY)), \
                self.assertRaises(tool.RecoveryError) as caught:
            tool.command_stage(argparse.Namespace())
        message = str(caught.exception)
        self.assertIn("cannot acquire a guard", message)
        self.assertIn("NOTHING WAS STAGED", message)
        self.assertIn("frpd_mg_acquisition_bindings", message)
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

    def test_already_complete_reports_no_plan_and_no_write(self):
        resolved = {position: 7600 + position for position in range(1, 7)}
        resolved[1] = HERO_ID
        gallery = [{"id": resolved[position], "alt": "",
                    "src": public_url(tool.FIXED_FILENAMES[position - 1])}
                   for position in range(1, 7)]
        admin = FakeAdmin(resolved)
        with self.live(admin, resolved, gallery=gallery,
                       probes=self.probes_for(resolved)) as state:
            with mock.patch.object(tool, "emit") as emitted:
                tool.command_stage(argparse.Namespace())
        payload = emitted.call_args[0][0]
        self.assertEqual(payload["status"], "VERIFIED_ALREADY_COMPLETE")
        self.assertIsNone(payload["plan"])
        self.assertEqual(payload["website_writes"], 0)
        self.assertEqual(state["requests"], [])
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
    def test_plan_identity_expiry_and_canonical_name(self):
        path, plan = self.stage_plan()
        self.assertEqual(plan["schema_version"], 1)
        self.assertEqual(plan["tool_version"], "1.0.0")
        self.assertEqual(plan["action"], "recover_fixed_open_manway_gallery")
        self.assertEqual(plan["method"], "PUT")
        self.assertEqual(plan["endpoint"], "/products/1397")
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
        self.assertTrue(any("images-only PUT /products/1397" in row
                            for row in plan["writes_if_committed"]))

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
        probe = mock.Mock(side_effect=AssertionError("network must not be reached"))
        with mock.patch.object(tool.wc, "load_vault", vault), \
                mock.patch.object(tool, "ui_browser_lock", lock), \
                mock.patch.object(tool, "admin_session", session), \
                mock.patch.object(tool, "origin_probe_evidence", probe), \
                self.assertRaises(tool.RecoveryError):
            tool.command_commit(argparse.Namespace(plan=str(path), approval="approved"))
        vault.assert_not_called()
        lock.assert_not_called()
        session.assert_not_called()
        probe.assert_not_called()
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

    def test_the_browser_lock_is_taken_before_the_attempt_lock(self):
        source = ast.get_source_segment(SOURCE_TEXT, next(
            node for node in ast.walk(SOURCE_TREE)
            if isinstance(node, ast.FunctionDef) and node.name == "command_commit"))
        self.assertLess(source.index('ui_browser_lock('), source.index("ATTEMPT_STARTED"))

    def test_the_attempt_lock_is_written_immediately_before_guard_acquisition(self):
        source = ast.get_source_segment(SOURCE_TEXT, next(
            node for node in ast.walk(SOURCE_TREE)
            if isinstance(node, ast.FunctionDef) and node.name == "command_commit"))
        lock_at = source.index("ATTEMPT_STARTED")
        acquire_at = source.index("adapter.acquire_guard(")
        upload_at = source.index("adapter.upload_missing(")
        put_at = source.index("adapter.assign_gallery(")
        self.assertLess(lock_at, acquire_at)
        self.assertLess(acquire_at, upload_at)
        self.assertLess(upload_at, put_at)
        between = source[lock_at:acquire_at]
        self.assertNotIn("upload_one", between)
        self.assertNotIn("api_request", between)

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
        self.assertEqual(len(state["requests"]), 1)
        method, endpoint, payload = state["requests"][0]
        self.assertEqual((method, endpoint), ("PUT", "/products/1397"))
        self.assertEqual(list(payload), ["images"])
        ids = [row["id"] for row in payload["images"]]
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(set(ids)), 6)
        self.assertEqual(ids[0], HERO_ID)
        self.assertTrue(all(set(row) == {"id"} for row in payload["images"]))

    def test_an_already_live_position_is_reused_and_never_re_uploaded(self):
        resolved = {1: HERO_ID, 2: 7610}
        path, plan = self.stage_plan(resolved)
        self.assertEqual(plan["upload_positions"], [3, 4, 5, 6])
        admin = FakeAdmin(resolved)
        state = self.commit(path, admin, resolved)
        self.assertEqual(admin.upload_positions_seen, [3, 4, 5, 6])
        self.assertNotIn("02_manway_top_oblique.png", admin.uploads)
        ids = [row["id"] for row in state["requests"][0][2]["images"]]
        self.assertEqual(ids[:2], [HERO_ID, 7610])

    def test_the_gallery_put_carries_only_images(self):
        path, _plan = self.stage_plan()
        resolved = {1: HERO_ID}
        state = self.commit(path, FakeAdmin(resolved), resolved)
        payload = state["requests"][0][2]
        for banned in ("name", "price", "regular_price", "sale_price", "status", "sku",
                       "stock_quantity", "description", "categories", "attributes",
                       "variations", "tax_class", "shipping_class", "meta_data"):
            self.assertNotIn(banned, payload)

    def test_the_put_is_conditional_on_the_exact_pre_write_gallery(self):
        path, _plan = self.stage_plan()
        resolved = {1: HERO_ID}
        state = self.commit(path, FakeAdmin(resolved), resolved)
        self.assertTrue(str(state["if_match"]).startswith('"'))
        self.assertEqual(len(str(state["if_match"])), 66)

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
            self.commit(path, FakeAdmin(resolved), resolved,
                        put_error=RuntimeError("modelled PUT failure"))
        result = json.loads(
            tool.result_path(plan["operation_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "gallery_put_or_readback")
        self.assertTrue(result["product_may_have_changed"])
        self.assertTrue(result["no_retry"])

    def test_a_gallery_readback_mismatch_is_permanently_indeterminate(self):
        path, plan = self.stage_plan()
        resolved = {1: HERO_ID}
        with self.assertRaises(tool.RecoveryIndeterminate):
            self.commit(path, FakeAdmin(resolved), resolved, readback_gallery_wrong=True)
        result = json.loads(
            tool.result_path(plan["operation_sha256"]).read_text(encoding="utf-8"))
        self.assertEqual(result["stage"], "gallery_put_or_readback")
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
        adapter_source = ast.get_source_segment(SOURCE_TEXT, next(
            node for node in ast.walk(SOURCE_TREE)
            if isinstance(node, ast.ClassDef) and node.name == "RecoveryAdapter"))
        for method, effect in (("acquire_guard", "acquire_prepared_guard"),
                               ("upload_missing", "upload_one"),
                               ("assign_gallery", "api_request")):
            body = adapter_source[adapter_source.index(f"def {method}("):]
            body = body[:body.index("\n    def ", 1)] if "\n    def " in body[1:] else body
            self.assertLess(body.index("require_eligible("), body.index(effect),
                            f"{method} must run the predicate before {effect}")

    def test_a_reconciliation_change_after_staging_refuses(self):
        path, _plan = self.stage_plan()
        resolved = {1: HERO_ID, 3: 7620}
        admin = FakeAdmin(resolved)
        with self.assertRaises(tool.RecoveryError) as caught:
            self.commit(path, admin, resolved)
        self.assertIn("changed after staging", str(caught.exception))
        self.assertFalse((self.plan_dir / "attempt-ledger").exists())

    def test_the_predicate_refuses_a_reordered_gallery_payload(self):
        reconciliation = ReconciliationTests.reconcile(self, {1: HERO_ID, 2: 7610})
        kwargs = dict(prior_evidence=tool.validate_prior_evidence(),
                      local=tool.validate_local_images(),
                      product=self.product_evidence_for(),
                      reconciliation=reconciliation,
                      origin_probe=self.probes_for({1: HERO_ID, 2: 7610}))
        tool.assert_recovery_eligibility(
            **kwargs, final_attachment_ids=[HERO_ID, 7610, 11, 12, 13, 14])
        for bad in ([7610, HERO_ID, 11, 12, 13, 14],
                    [HERO_ID, 99, 11, 12, 13, 14],
                    [HERO_ID, 7610, 11, 12, 13],
                    [HERO_ID, 7610, 11, 12, 13, 13]):
            with self.subTest(bad=bad), self.assertRaises(tool.RecoveryError):
                tool.assert_recovery_eligibility(**kwargs, final_attachment_ids=bad)


# ==========================================================================
# 12. Static capability scan
# ==========================================================================
class StaticCapabilityTests(RecoveryTestCase):
    def test_exactly_one_woocommerce_write_call_and_it_is_a_put(self):
        calls = [node for node in ast.walk(SOURCE_TREE)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "api_request"]
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0].args[0], ast.Constant)
        self.assertEqual(calls[0].args[0].value, "PUT")
        self.assertIsInstance(calls[0].args[1], ast.JoinedStr)

    def test_no_delete_patch_or_post_product_verb_exists(self):
        upper = SOURCE_TEXT.upper()
        for banned in ('API_REQUEST("DELETE"', 'API_REQUEST("POST"',
                       'API_REQUEST("PATCH"', "REQUESTS.DELETE", "REQUESTS.POST"):
            self.assertNotIn(banned, upper)

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
        adapter_source = ast.get_source_segment(SOURCE_TEXT, next(
            node for node in ast.walk(SOURCE_TREE)
            if isinstance(node, ast.ClassDef) and node.name == "RecoveryAdapter"))
        self.assertIn("upload_positions(reconciliation)", adapter_source)

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
