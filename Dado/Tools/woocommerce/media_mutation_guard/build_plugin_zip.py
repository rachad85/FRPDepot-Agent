#!/usr/bin/env python
"""Build the reproducible fixed FRP Depot Media Mutation Guard plugin ZIP.

Local packaging only. It validates the exact approved five-family source manifest,
reduces it to the runtime filename/size/SHA contract, and writes a deterministic
ZIP. It makes no browser, network, WordPress, WooCommerce, or email call.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(r"C:\FRPDepot")
HERE = Path(__file__).resolve().parent
PLUGIN_SLUG = "frpdepot-media-mutation-guard"
PLUGIN_DIR = HERE / PLUGIN_SLUG
SOURCE_MANIFEST = (ROOT / "Dado" / "20_Working" / "product_image_overhaul_20260815"
                   / "final_family_review_20260815"
                   / "approved_product_family_media_manifest_20260815.json")
SOURCE_MANIFEST_SHA256 = "9020dfbbedec473430fa02a4e07578284ec3da33009444a1467c28aa75cc9748"
DEFAULT_OUTPUT = HERE / f"{PLUGIN_SLUG}.zip"
RUNTIME_MANIFEST = PLUGIN_DIR / "approved-media.json"
FAMILY_IDS = {
    "stub_flange": 1368,
    "open_manway": 1397,
    "manway_cover": 1411,
    "elbow_90": 1423,
    "pipe": 1455,
}
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
        if not isinstance(images, list) or len(images) != 4:
            raise SystemExit(f"ERROR: fixed family must contain four images: {key}")
        reduced = []
        for position, image in enumerate(images, 1):
            if (not isinstance(image, dict) or image.get("position") != position
                    or image.get("format") != "PNG"):
                raise SystemExit(f"ERROR: fixed image record changed: {key}/{position}")
            filename = image.get("filename")
            digest = image.get("sha256")
            size = image.get("bytes")
            if (not isinstance(filename, str) or Path(filename).name != filename
                    or not filename.lower().endswith(".png")
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(ch not in "0123456789abcdef" for ch in digest)
                    or type(size) is not int or size <= 0
                    or filename in seen_names or digest in seen_hashes):
                raise SystemExit(f"ERROR: invalid or duplicate fixed image: {key}/{position}")
            seen_names.add(filename)
            seen_hashes.add(digest)
            reduced.append({
                "position": position,
                "filename": filename,
                "bytes": size,
                "sha256": digest,
            })
        runtime_families[key] = {"product_id": product_id, "images": reduced}
    return {
        "schema": 1,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "families": runtime_families,
    }


def build(output: Path) -> dict[str, object]:
    runtime = build_runtime_manifest()
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_MANIFEST.write_bytes(canonical_bytes(runtime))
    missing = [name for name in MEMBERS if not (PLUGIN_DIR / name).is_file()]
    if missing:
        raise SystemExit("ERROR: plugin source file(s) missing: " + ", ".join(missing))
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
        "website_writes": 0,
    }


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) == 2 else DEFAULT_OUTPUT
    print(json.dumps(build(output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
