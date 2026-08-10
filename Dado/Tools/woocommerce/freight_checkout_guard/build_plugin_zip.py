#!/usr/bin/env python
"""Build the reproducible FRP Depot Freight Checkout Guard plugin ZIP.

Local packaging only. This uploads nothing, contacts no host, and installs
nothing: the resulting ZIP is installed by hand in WordPress.

The archive is byte-for-byte reproducible -- fixed member order, fixed
timestamps, fixed permissions, no compression nondeterminism -- so the parent
can rebuild it and compare the SHA-256 against the build report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

HERE = Path(__file__).resolve().parent
PLUGIN_DIR = HERE / "frpdepot-freight-checkout-guard"
DEFAULT_OUTPUT = HERE / "frpdepot-freight-checkout-guard.zip"

# Members in fixed order, with a fixed DOS timestamp (1980-01-01 00:00:00).
# assets/frpdepot-freight-notice.js was added in plugin 1.0.1; it carries the
# required sentence into the Cart/Checkout Blocks UI.
MEMBERS = (
    "assets/frpdepot-freight-notice.js",
    "frpdepot-freight-checkout-guard.php",
    "readme.txt",
    "ups-allowlist.json",
)
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def build(output: Path) -> dict[str, object]:
    missing = [name for name in MEMBERS if not (PLUGIN_DIR / name).is_file()]
    if missing:
        raise SystemExit("ERROR: plugin source file(s) missing: " + ", ".join(missing))

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in MEMBERS:
            data = (PLUGIN_DIR / name).read_bytes()
            info = zipfile.ZipInfo(f"{PLUGIN_DIR.name}/{name}", date_time=FIXED_DATE_TIME)
            info.external_attr = 0o644 << 16
            info.create_system = 0  # Announce MS-DOS regardless of build host.
            archive.writestr(info, data)

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return {
        "zip": str(output),
        "sha256": digest,
        "bytes": output.stat().st_size,
        "members": [f"{PLUGIN_DIR.name}/{name}" for name in MEMBERS],
        "member_sha256": {
            name: hashlib.sha256((PLUGIN_DIR / name).read_bytes()).hexdigest()
            for name in MEMBERS
        },
        "uploaded": False,
        "installed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the freight checkout guard plugin ZIP")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    print(json.dumps(build(Path(args.output).resolve()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
