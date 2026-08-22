#!/usr/bin/env python
"""Build the reproducible fixed four-origin-file cleanup plugin ZIP offline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
PLUGIN_SLUG = "frpdepot-fixed-four-origin-file-cleanup"
PLUGIN_DIR = HERE / PLUGIN_SLUG
ARTIFACT = HERE / f"{PLUGIN_SLUG}-1.0.0.zip"
MANIFEST = HERE / f"{PLUGIN_SLUG}-1.0.0.manifest.json"
MEMBERS = (
    "frpdepot-fixed-four-origin-file-cleanup.php",
    "readme.txt",
)
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build() -> dict[str, object]:
    missing = [name for name in MEMBERS if not (PLUGIN_DIR / name).is_file()]
    if missing:
        raise SystemExit("ERROR: missing fixed plugin source: " + ", ".join(missing))
    with tempfile.TemporaryDirectory(prefix="fixed-origin-cleanup-build-") as folder:
        candidate = Path(folder) / ARTIFACT.name
        with zipfile.ZipFile(candidate, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=9) as archive:
            for name in MEMBERS:
                data = (PLUGIN_DIR / name).read_bytes()
                info = zipfile.ZipInfo(f"{PLUGIN_SLUG}/{name}", date_time=FIXED_DATE_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                info.create_system = 3
                archive.writestr(info, data)
        built = candidate.read_bytes()
    ARTIFACT.write_bytes(built)
    files = {
        f"{PLUGIN_SLUG}/{name}": {
            "bytes": len((PLUGIN_DIR / name).read_bytes()),
            "sha256": digest((PLUGIN_DIR / name).read_bytes()),
        }
        for name in MEMBERS
    }
    manifest = {
        "schema": 1,
        "plugin_name": "FRP Depot Fixed Four Origin File Cleanup",
        "plugin_slug": PLUGIN_SLUG,
        "plugin_file": f"{PLUGIN_SLUG}/frpdepot-fixed-four-origin-file-cleanup.php",
        "plugin_version": "1.0.0",
        "artifact_name": ARTIFACT.name,
        "artifact_bytes": len(built),
        "artifact_sha256": digest(built),
        "members": files,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="ascii")
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, sort_keys=True))
