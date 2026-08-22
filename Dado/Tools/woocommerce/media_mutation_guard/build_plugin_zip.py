#!/usr/bin/env python
"""Build the reproducible fixed FRP Depot Media Mutation Guard plugin ZIP.

Local packaging only. It validates the exact approved five-family source manifest,
including six Stub Flange and six Open Manway images and four images for every
other family, binds the one fixed pre-existing Stub Flange hero reuse and the one
literal Open Manway recovery contract, reduces them to the runtime
filename/size/SHA contract, and writes a deterministic ZIP. It
makes no browser, network, WordPress, WooCommerce, or email call.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(r"C:\FRPDepot")
HERE = Path(__file__).resolve().parent
PLUGIN_SLUG = "frpdepot-media-mutation-guard"
PLUGIN_VERSION = "1.0.7"
PLUGIN_DIR = HERE / PLUGIN_SLUG
SOURCE_MANIFEST = (
    ROOT / "Dado" / "20_Working" / "frp_manway" / "approved_gallery_20260820"
    / "approved_product_family_media_manifest_open_manway_update_20260820.json"
)
SOURCE_MANIFEST_SHA256 = "b0e020eaa9de9a43a64b2441e0b194b4972ace28cd84fe52cf92d8e6a72f05c7"
DEFAULT_OUTPUT = HERE / f"{PLUGIN_SLUG}-{PLUGIN_VERSION}.zip"
RUNTIME_MANIFEST = PLUGIN_DIR / "approved-media.json"
FAMILY_IDS = {
    "stub_flange": 1368,
    "open_manway": 1397,
    "manway_cover": 1411,
    "elbow_90": 1423,
    "pipe": 1455,
}
FAMILY_IMAGE_COUNTS = {
    "stub_flange": 6,
    "open_manway": 6,
    "manway_cover": 4,
    "elbow_90": 4,
    "pipe": 4,
}
REUSE_FAMILY = "stub_flange"
REUSE_PRODUCT_ID = 1368
REUSE_POSITION = 1
REUSE_ATTACHMENT_ID = 4849
REUSE_ACTUAL_FILENAME = "01_stub_flange_real_source_hero-1.png"
REUSE_BYTES = 895251
REUSE_SHA256 = "aa9c8da37cc4a1ee98b5f0b2c77dd5b369c327a583412938778210562936b3da"
REUSE_REQUIRED_CURRENT_RAW_META = {
    "_thumbnail_id": "4849",
    "_product_image_gallery": "4850,4851,4852",
}
REUSE_UPLOAD_POSITIONS = [2, 3, 4, 5, 6]
# The ONE literal Open Manway recovery contract finalized in 1.0.7. It is a closed
# record naming one family, one product, one prior operation and one verified
# attachment; there is no generic recovery contract in the plugin or here.
RECOVERY_CONTRACT = "open_manway_recovery"
RECOVERY_FAMILY = "open_manway"
RECOVERY_PRODUCT_ID = 1397
RECOVERY_PRIOR_OPERATION_SHA256 = (
    "e0127fcaa04c023cbdd19a36726d6e8f03c3fb01f12f0367550d17c87674dc85"
)
RECOVERY_PRIOR_PLAN_SHA256 = (
    "1c7865b0287b076fe83c179c2e44f33a3bcb2effb048e87374c32ad4781b19df"
)
RECOVERY_POSITION = 1
RECOVERY_ATTACHMENT_ID = 7609
RECOVERY_FILENAME = "01_manway_premium_hero.png"
RECOVERY_BYTES = 1750111
RECOVERY_SHA256 = "472b5e5b0aba9a7201444524c559e6797c266a0de008d7bc70b4f8ef1938d0cd"
RECOVERY_POSITIONS = [2, 3, 4, 5, 6]
MEMBERS = (
    "frpdepot-media-mutation-guard.php",
    "approved-media.json",
    "readme.txt",
)
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("ascii")


def build_runtime_manifest() -> dict[str, object]:
    source_bytes = SOURCE_MANIFEST.read_bytes()
    if sha256_bytes(source_bytes) != SOURCE_MANIFEST_SHA256:
        raise SystemExit("ERROR: approved source manifest SHA-256 changed")
    source = json.loads(source_bytes.decode("utf-8"))
    if source.get("status") != "APPROVED_WORKING_COLLECTION_NOT_PUBLISHED":
        raise SystemExit("ERROR: approved source manifest status changed")
    stub_instruction = source.get("stub_flange_update_instruction")
    if (not isinstance(stub_instruction, dict)
            or stub_instruction.get("exact_user_message") != "much better, proceed"
            or stub_instruction.get("scope") != "six reviewed Stub Flange images; staging only"
            or stub_instruction.get("review_sheet") != {
                "path": (
                    r"C:\FRPDepot\Dado\20_Working\stub_flange_chatgpt_views_v2_20260820"
                    r"\00_stub_flange_chatgpt_six_view_review.jpg"
                ),
                "sha256": "3d3fbbb409ff3a4eea810cbc9c726a79f623012acc23f7ee621cec4960831478",
            }
            or stub_instruction.get("not_authorized") != [
                "publication", "upload", "WordPress write", "WooCommerce write",
                "website write", "Drive write", "Zoho write", "email",
            ]):
        raise SystemExit("ERROR: Stub Flange staging-only instruction evidence changed")
    manway_instruction = source.get("open_manway_update_instruction")
    if (not isinstance(manway_instruction, dict)
            or manway_instruction.get("exact_user_message") != "Yes"
            or manway_instruction.get("scope") != "six reviewed FRP Manway images; staging only"
            or manway_instruction.get("review_evidence") != {
                "path": (
                    r"C:\FRPDepot\Dado\20_Working\frp_manway\approved_gallery_20260820"
                    r"\approved_gallery_manifest.json"
                ),
                "sha256": "abac2f99e4b3dd5f292097b06f36f883a2d866a3c8d57c289dfc2dcb76aa3d45",
            }
            or manway_instruction.get("order") != "image 1 hero; images 2-6 gallery in shown order"
            or manway_instruction.get("not_authorized") != [
                "publication", "upload", "WordPress write", "WooCommerce write",
                "website write", "Drive write", "Zoho write", "email",
            ]):
        raise SystemExit("ERROR: Open Manway staging-only instruction evidence changed")
    families = source.get("families")
    if not isinstance(families, dict) or set(families) != set(FAMILY_IDS):
        raise SystemExit("ERROR: source manifest must contain exactly the fixed five families")
    runtime_families: dict[str, object] = {}
    seen_names: set[str] = set()
    seen_hashes: set[str] = set()
    for key, product_id in FAMILY_IDS.items():
        family = families.get(key)
        if not isinstance(family, dict) or family.get("product_id") != product_id:
            raise SystemExit(f"ERROR: fixed family identity changed: {key}")
        images = family.get("accepted_images")
        expected_count = FAMILY_IMAGE_COUNTS[key]
        if not isinstance(images, list) or len(images) != expected_count:
            raise SystemExit(
                f"ERROR: fixed family must contain exactly {expected_count} images: {key}"
            )
        reduced = []
        for position, image in enumerate(images, 1):
            if (not isinstance(image, dict) or image.get("position") != position
                    or image.get("format") != "PNG"):
                raise SystemExit(f"ERROR: fixed image record changed: {key}/{position}")
            filename = image.get("filename")
            digest = image.get("sha256")
            size = image.get("bytes")
            width = image.get("width")
            height = image.get("height")
            mode = image.get("mode")
            if (not isinstance(filename, str) or Path(filename).name != filename
                    or not filename.lower().endswith(".png")
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(ch not in "0123456789abcdef" for ch in digest)
                    or type(size) is not int or size <= 0
                    or type(width) is not int or width <= 0
                    or type(height) is not int or height <= 0
                    or mode not in {"RGB", "RGBA"}
                    or filename in seen_names or digest in seen_hashes):
                raise SystemExit(f"ERROR: invalid or duplicate fixed image: {key}/{position}")
            seen_names.add(filename)
            seen_hashes.add(digest)
            reduced.append({
                "position": position,
                "filename": filename,
                "bytes": size,
                "width": width,
                "height": height,
                "mode": mode,
                "sha256": digest,
            })
        runtime_families[key] = {"product_id": product_id, "images": reduced}
    reused_image = runtime_families[REUSE_FAMILY]["images"][REUSE_POSITION - 1]
    if (runtime_families[REUSE_FAMILY]["product_id"] != REUSE_PRODUCT_ID
            or reused_image["position"] != REUSE_POSITION
            or reused_image["bytes"] != REUSE_BYTES
            or reused_image["sha256"] != REUSE_SHA256):
        raise SystemExit("ERROR: fixed Stub Flange reuse identity or approved bytes changed")
    recovery_image = runtime_families[RECOVERY_FAMILY]["images"][RECOVERY_POSITION - 1]
    if (runtime_families[RECOVERY_FAMILY]["product_id"] != RECOVERY_PRODUCT_ID
            or recovery_image["position"] != RECOVERY_POSITION
            or recovery_image["filename"] != RECOVERY_FILENAME
            or recovery_image["bytes"] != RECOVERY_BYTES
            or recovery_image["sha256"] != RECOVERY_SHA256
            or len(runtime_families[RECOVERY_FAMILY]["images"]) != 6):
        raise SystemExit("ERROR: fixed Open Manway recovery identity or approved bytes changed")
    return {
        "schema": 3,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "fixed_recovery": {
            "contract": RECOVERY_CONTRACT,
            "family": RECOVERY_FAMILY,
            "product_id": RECOVERY_PRODUCT_ID,
            "prior_operation_sha256": RECOVERY_PRIOR_OPERATION_SHA256,
            "prior_plan_sha256": RECOVERY_PRIOR_PLAN_SHA256,
            "position": RECOVERY_POSITION,
            "attachment_id": RECOVERY_ATTACHMENT_ID,
            "filename": RECOVERY_FILENAME,
            "bytes": RECOVERY_BYTES,
            "sha256": RECOVERY_SHA256,
            "recoverable_positions": list(RECOVERY_POSITIONS),
        },
        "fixed_reuse": {
            "family": REUSE_FAMILY,
            "product_id": REUSE_PRODUCT_ID,
            "position": REUSE_POSITION,
            "attachment_id": REUSE_ATTACHMENT_ID,
            "actual_filename": REUSE_ACTUAL_FILENAME,
            "approved_filename": reused_image["filename"],
            "bytes": reused_image["bytes"],
            "sha256": reused_image["sha256"],
            "must_be_current_product_hero": True,
            "required_current_raw_meta": dict(REUSE_REQUIRED_CURRENT_RAW_META),
            "upload_positions": list(REUSE_UPLOAD_POSITIONS),
        },
        "families": runtime_families,
    }


def validate_plugin_source(runtime_bytes: bytes) -> None:
    """Refuse a package whose PHP/readme contract disagrees with the manifest."""
    php = (PLUGIN_DIR / "frpdepot-media-mutation-guard.php").read_text(
        encoding="utf-8"
    )
    readme = (PLUGIN_DIR / "readme.txt").read_text(encoding="utf-8")
    runtime_digest = sha256_bytes(runtime_bytes)
    runtime = json.loads(runtime_bytes.decode("ascii"))
    reuse = runtime.get("fixed_reuse")
    if not isinstance(reuse, dict):
        raise SystemExit("ERROR: runtime fixed Stub Flange reuse contract is missing")
    if f" * Version: {PLUGIN_VERSION}\n" not in php:
        raise SystemExit("ERROR: plugin header version disagrees with package version")
    if f"define('FRPD_MG_VERSION', '{PLUGIN_VERSION}');" not in php:
        raise SystemExit("ERROR: FRPD_MG_VERSION disagrees with package version")
    if f"define('FRPD_MG_MANIFEST_SHA256', '{runtime_digest}');" not in php:
        raise SystemExit("ERROR: PHP runtime-manifest SHA-256 pin disagrees")
    reuse_pins = (
        f"define('FRPD_MG_REUSE_FAMILY', '{reuse['family']}');",
        f"define('FRPD_MG_REUSE_PRODUCT_ID', {reuse['product_id']});",
        f"define('FRPD_MG_REUSE_POSITION', {reuse['position']});",
        f"define('FRPD_MG_REUSE_ATTACHMENT_ID', {reuse['attachment_id']});",
        f"define('FRPD_MG_REUSE_ACTUAL_FILENAME', '{reuse['actual_filename']}');",
        f"define('FRPD_MG_REUSE_APPROVED_FILENAME', '{reuse['approved_filename']}');",
        f"define('FRPD_MG_REUSE_BYTES', {reuse['bytes']});",
        f"define('FRPD_MG_REUSE_SHA256', '{reuse['sha256']}');",
        "define('FRPD_MG_REUSE_CURRENT_THUMBNAIL_RAW', '4849');",
        "define('FRPD_MG_REUSE_CURRENT_GALLERY_RAW', '4850,4851,4852');",
    )
    if any(pin not in php for pin in reuse_pins):
        raise SystemExit("ERROR: PHP fixed Stub Flange reuse contract disagrees")
    recovery = runtime.get("fixed_recovery")
    if not isinstance(recovery, dict):
        raise SystemExit("ERROR: runtime fixed Open Manway recovery contract is missing")
    recovery_pins = (
        f"define('FRPD_MG_RECOVERY_CONTRACT', '{recovery['contract']}');",
        f"define('FRPD_MG_RECOVERY_FAMILY', '{recovery['family']}');",
        f"define('FRPD_MG_RECOVERY_PRODUCT_ID', {recovery['product_id']});",
        "define('FRPD_MG_RECOVERY_PRIOR_OPERATION_SHA256', "
        f"'{recovery['prior_operation_sha256']}');",
        f"define('FRPD_MG_RECOVERY_PRIOR_PLAN_SHA256', '{recovery['prior_plan_sha256']}');",
        f"define('FRPD_MG_RECOVERY_POSITION', {recovery['position']});",
        f"define('FRPD_MG_RECOVERY_ATTACHMENT_ID', {recovery['attachment_id']});",
        f"define('FRPD_MG_RECOVERY_FILENAME', '{recovery['filename']}');",
        f"define('FRPD_MG_RECOVERY_BYTES', {recovery['bytes']});",
        f"define('FRPD_MG_RECOVERY_SHA256', '{recovery['sha256']}');",
        "define('FRPD_MG_STATE_SCHEMA', 3);",
        "define('FRPD_MG_PROOF_SCHEMA', 3);",
    )
    if any(pin not in php for pin in recovery_pins):
        raise SystemExit("ERROR: PHP fixed Open Manway recovery contract disagrees")
    if recovery["recoverable_positions"] != [2, 3, 4, 5, 6]:
        raise SystemExit("ERROR: fixed Open Manway recoverable positions changed")
    if runtime.get("schema") != 3:
        raise SystemExit("ERROR: runtime manifest schema is not the 1.0.7 contract")
    count_block = re.search(
        r"\$counts\s*=\s*array\((.*?)\n\s*\);", php, flags=re.DOTALL
    )
    if not count_block:
        raise SystemExit("ERROR: PHP fixed family-count block is missing")
    for family, expected_count in FAMILY_IMAGE_COUNTS.items():
        count_matches = re.findall(
            rf"'{re.escape(family)}'\s*=>\s*([0-9]+)\s*,", count_block.group(1)
        )
        if count_matches != [str(expected_count)]:
            raise SystemExit(
                f"ERROR: PHP fixed image count disagrees for {family}"
            )
    if f"Stable tag: {PLUGIN_VERSION}\n" not in readme:
        raise SystemExit("ERROR: readme stable tag disagrees with package version")
    for phrase in (
            "one literal Open Manway recovery contract",
            "attachment 7609",
            "product 1397",
            "origin-only",
            "admin-post",
            "BUILT AND TESTED ONLY",
            "WITHDRAWN_NOT_DEPLOYED_NOT_STAGEABLE",
    ):
        if phrase not in readme:
            raise SystemExit(
                f"ERROR: readme does not state the exact 1.0.7 contract: {phrase!r}"
            )


def build(output: Path) -> dict[str, object]:
    runtime = build_runtime_manifest()
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    runtime_bytes = canonical_bytes(runtime)
    RUNTIME_MANIFEST.write_bytes(runtime_bytes)
    missing = [name for name in MEMBERS if not (PLUGIN_DIR / name).is_file()]
    if missing:
        raise SystemExit("ERROR: plugin source file(s) missing: " + ", ".join(missing))
    validate_plugin_source(runtime_bytes)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for name in MEMBERS:
            data = (PLUGIN_DIR / name).read_bytes()
            info = zipfile.ZipInfo(f"{PLUGIN_SLUG}/{name}", date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, data)
    with zipfile.ZipFile(output, "r") as archive:
        expected_members = [f"{PLUGIN_SLUG}/{name}" for name in MEMBERS]
        if archive.namelist() != expected_members:
            raise SystemExit("ERROR: built ZIP member set/order changed")
    return {
        "zip": str(output.resolve()),
        "sha256": sha256_bytes(output.read_bytes()),
        "bytes": output.stat().st_size,
        "members": [f"{PLUGIN_SLUG}/{name}" for name in MEMBERS],
        "member_sha256": {
            f"{PLUGIN_SLUG}/{name}": sha256_bytes((PLUGIN_DIR / name).read_bytes())
            for name in MEMBERS
        },
        "runtime_manifest_sha256": sha256_bytes(RUNTIME_MANIFEST.read_bytes()),
        "families": list(FAMILY_IDS),
        "family_image_counts": dict(FAMILY_IMAGE_COUNTS),
        "website_writes": 0,
    }


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) == 2 else DEFAULT_OUTPUT
    print(json.dumps(build(output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
