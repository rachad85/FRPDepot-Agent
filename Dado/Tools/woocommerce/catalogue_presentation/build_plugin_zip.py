#!/usr/bin/env python
"""Build the reproducible FRP Depot Automatic Catalogue Presentation ZIP.

Local packaging only. This script makes no network/browser call and performs no
WordPress or external write. Fixed member order, timestamp and permissions make
the artifact byte-for-byte reproducible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

HERE = Path(__file__).resolve().parent
PLUGIN_DIR = HERE / "frpdepot-automatic-catalogue-presentation"
DEFAULT_OUTPUT = HERE / "frpdepot-automatic-catalogue-presentation.zip"
MEMBERS = (
    "frpdepot-automatic-catalogue-presentation.php",
    "readme.txt",
    "catalogue-sections/FRP_Depots_Stub_Flanges_2026.pdf",
    "catalogue-sections/FRP_Depots_Manways_and_Covers_2026.pdf",
    "catalogue-sections/FRP_Depots_90_Degree_Elbows_2026.pdf",
    "catalogue-sections/FRP_Depots_Filament_Wound_Pipe_2026.pdf",
    "catalogue-sections/FRP_Depots_FNPT_Couplings_2026.pdf",
)
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def build(output: Path) -> dict[str, object]:
    missing = [name for name in MEMBERS if not (PLUGIN_DIR / name).is_file()]
    if missing:
        raise SystemExit("ERROR: plugin source file(s) missing: " + ", ".join(missing))
    if output.resolve() == (PLUGIN_DIR / output.name).resolve():
        raise SystemExit("ERROR: output ZIP may not be written inside the plugin directory")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in MEMBERS:
            data = (PLUGIN_DIR / name).read_bytes()
            info = zipfile.ZipInfo(f"{PLUGIN_DIR.name}/{name}", date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, data)

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return {
        "zip": str(output.resolve()),
        "sha256": digest,
        "bytes": output.stat().st_size,
        "members": [f"{PLUGIN_DIR.name}/{name}" for name in MEMBERS],
        "member_sha256": {
            name: hashlib.sha256((PLUGIN_DIR / name).read_bytes()).hexdigest()
            for name in MEMBERS
        },
        "uploaded": False,
        "installed": False,
        "activated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the automatic catalogue presentation plugin ZIP")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    print(json.dumps(build(Path(args.output)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
