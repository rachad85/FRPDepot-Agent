#!/usr/bin/env python
"""Build the exact reproducible FRP Depot Hetron private-history plugin ZIP."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
PLUGIN_SLUG = "frpdepot-hetron-private-history"
PLUGIN_DIR = ROOT / "plugin" / PLUGIN_SLUG
ARTIFACT = ROOT / f"{PLUGIN_SLUG}-1.1.0.zip"
MANIFEST = ROOT / f"{PLUGIN_SLUG}-1.1.0.manifest.json"
MEMBERS = ("frpdepot-hetron-private-history.php", "readme.txt")
FIXED_DATE_TIME = (2026, 8, 13, 0, 0, 0)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build() -> dict[str, object]:
    missing = [name for name in MEMBERS if not (PLUGIN_DIR / name).is_file()]
    if missing:
        raise SystemExit("ERROR: missing plugin source: " + ", ".join(missing))
    with tempfile.TemporaryDirectory(prefix="hetron-private-history-build-") as folder:
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
            "bytes": (PLUGIN_DIR / name).stat().st_size,
            "sha256": digest((PLUGIN_DIR / name).read_bytes()),
        }
        for name in MEMBERS
    }
    manifest = {
        "schema": 1,
        "plugin_name": "FRP Depot Hetron Private History",
        "plugin_slug": PLUGIN_SLUG,
        "plugin_file": f"{PLUGIN_SLUG}/frpdepot-hetron-private-history.php",
        "plugin_version": "1.1.0",
        "artifact_name": ARTIFACT.name,
        "artifact_bytes": len(built),
        "artifact_sha256": digest(built),
        "members": files,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, sort_keys=True))
