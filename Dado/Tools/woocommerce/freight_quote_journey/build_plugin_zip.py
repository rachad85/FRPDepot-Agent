#!/usr/bin/env python3
"""Build the commissioned 2.0.3 activation-trigger repair ZIP deterministically, offline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
PLUGIN_SLUG = "frpdepot-freight-checkout-guard"
PLUGIN_VERSION = "2.0.3"
PLUGIN_DIR = ROOT / PLUGIN_SLUG
ARTIFACT = ROOT / f"{PLUGIN_SLUG}-{PLUGIN_VERSION}.zip"
MANIFEST = ROOT / "artifact-manifest.json"
BASELINE_ALLOWLIST = ROOT.parent / "freight_checkout_guard" / PLUGIN_SLUG / "ups-allowlist.json"
SPEC_SHA256 = "5348ef3f357676f5629cf72696fd3fe0be718a3847854974f20cf28cc7047400"
MEMBERS = (
    "assets/frpdepot-freight-quote-journey.css",
    "assets/frpdepot-freight-quote-journey.js",
    "frpdepot-freight-checkout-guard.php",
    "readme.txt",
    "ups-allowlist.json",
)
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact_bytes() -> tuple[bytes, dict[str, dict[str, object]]]:
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / ARTIFACT.name
        files: dict[str, dict[str, object]] = {}
        with zipfile.ZipFile(candidate, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative in MEMBERS:
                source = PLUGIN_DIR / relative
                data = source.read_bytes()
                member = f"{PLUGIN_SLUG}/{relative}"
                info = zipfile.ZipInfo(member, ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.flag_bits = 0x800
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
                files[member] = {"sha256": sha256_bytes(data), "bytes": len(data)}
        return candidate.read_bytes(), files


def validate_sources() -> None:
    if not BASELINE_ALLOWLIST.is_file():
        raise RuntimeError("pinned baseline allowlist is missing")
    packaged_allowlist = (PLUGIN_DIR / "ups-allowlist.json").read_bytes()
    if packaged_allowlist != BASELINE_ALLOWLIST.read_bytes():
        raise RuntimeError("packaged allowlist is not byte-identical to the audited baseline")
    php = (PLUGIN_DIR / "frpdepot-freight-checkout-guard.php").read_text(encoding="utf-8")
    match = re.search(r"(?im)^\s*\*\s*Version:\s*(\S+)\s*$", php)
    if not match or match.group(1) != PLUGIN_VERSION or SPEC_SHA256 not in php:
        raise RuntimeError("plugin identity/version/specification mismatch")
    if "register_activation_hook" in php:
        raise RuntimeError("plugin activation must not trigger the business transaction")
    if "admin_post_frpdepot_fqj_fixed_apply" not in php:
        raise RuntimeError("fixed authenticated Apply trigger is missing")
    readme = (PLUGIN_DIR / "readme.txt").read_text(encoding="utf-8")
    if f"Stable tag: {PLUGIN_VERSION}" not in readme:
        raise RuntimeError("readme stable tag mismatch")


def build() -> dict[str, object]:
    validate_sources()
    first, files = artifact_bytes()
    second, second_files = artifact_bytes()
    if first != second or files != second_files:
        raise RuntimeError("two clean ZIP builds were not byte-identical")
    ARTIFACT.write_bytes(first)
    manifest: dict[str, object] = {
        "schema_version": 2,
        "specification_sha256": SPEC_SHA256,
        "plugin_slug": PLUGIN_SLUG,
        "plugin_version": PLUGIN_VERSION,
        "artifact": str(ARTIFACT.resolve()),
        "artifact_sha256": sha256_bytes(first),
        "artifact_bytes": len(first),
        "member_order": [f"{PLUGIN_SLUG}/{relative}" for relative in MEMBERS],
        "members": files,
        "zip_timestamp": "1980-01-01T00:00:00",
        "zip_permissions": "100644",
        "baseline_allowlist": str(BASELINE_ALLOWLIST.resolve()),
        "baseline_allowlist_sha256": sha256_bytes(BASELINE_ALLOWLIST.read_bytes()),
        "reproducible_two_clean_builds": True,
        "network_used": False,
        "live_write_performed": False,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, sort_keys=True, indent=2))
