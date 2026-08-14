#!/usr/bin/env python3
"""Verify, build, or package the fixed Derakane search plugin locally.

All commands fail closed until the plugin data directory contains the rebuild
pipeline's verified, zero-unresolved, manifest-hash-matching import. No network,
browser, website, staging, email, or deployment operation exists in this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

from import_contract import DATASET_NAME, MANIFEST_NAME, ImportContractError, verify_import

HERE = Path(__file__).resolve().parent
PLUGIN_SLUG = "frpdepot-derakane-chemical-search"
PLUGIN_DIR = HERE / "plugin" / PLUGIN_SLUG
DEFAULT_DATA_DIR = PLUGIN_DIR / "data"
DEFAULT_BUILD_DIR = HERE / "build" / PLUGIN_SLUG
DEFAULT_ZIP = HERE / "build" / f"{PLUGIN_SLUG}.zip"
SOURCE_MEMBERS = (
    "frpdepot-derakane-chemical-search.php",
    "assets/derakane-search.js",
    "assets/derakane-search.css",
    "readme.txt",
)
DATA_MEMBERS = (MANIFEST_NAME, DATASET_NAME)
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def _verified_members(data_dir: Path):
    verified = verify_import(data_dir)
    missing = [name for name in SOURCE_MEMBERS if not (PLUGIN_DIR / name).is_file()]
    if missing:
        raise ImportContractError("plugin source file(s) missing: " + ", ".join(missing))
    return verified


def verification_report(data_dir: Path) -> dict[str, object]:
    verified = _verified_members(data_dir)
    return {
        "status": "VERIFIED",
        "source_edition": verified.dataset["source"]["edition"],
        "source_document_sha256": verified.dataset["source"]["document_sha256"],
        "dataset_sha256": verified.sha256,
        "dataset_bytes": verified.byte_count,
        "row_count": len(verified.dataset["rows"]),
        "permission_gate_applied": False,
        "external_write_performed": False,
    }


def build(output_dir: Path, data_dir: Path) -> dict[str, object]:
    verified = _verified_members(data_dir)  # Gate before creating output.
    destination = output_dir.resolve()
    if destination == PLUGIN_DIR.resolve() or PLUGIN_DIR.resolve() in destination.parents:
        raise ImportContractError("build output may not overwrite or sit inside plugin source")
    if destination.exists():
        shutil.rmtree(destination)
    for name in SOURCE_MEMBERS:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PLUGIN_DIR / name, target)
    for name in DATA_MEMBERS:
        target = destination / "data" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(data_dir / name, target)
    return {
        "status": "BUILT",
        "directory": str(destination),
        "dataset_sha256": verified.sha256,
        "members": [*SOURCE_MEMBERS, *(f"data/{name}" for name in DATA_MEMBERS)],
        "external_write_performed": False,
    }


def package(output: Path, data_dir: Path) -> dict[str, object]:
    verified = _verified_members(data_dir)  # Gate before creating ZIP.
    destination = output.resolve()
    if destination.exists():
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    members = tuple((PLUGIN_DIR / name, name) for name in SOURCE_MEMBERS) + tuple(
        (data_dir / name, f"data/{name}") for name in DATA_MEMBERS
    )
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, relative in members:
            info = zipfile.ZipInfo(f"{PLUGIN_SLUG}/{relative}", date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, source.read_bytes())
    return {
        "status": "PACKAGED",
        "zip": str(destination),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "dataset_sha256": verified.sha256,
        "external_write_performed": False,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    for command in ("verify", "build", "package"):
        child = subparsers.add_parser(command)
        child.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
        if command == "build":
            child.add_argument("--output-dir", type=Path, default=DEFAULT_BUILD_DIR)
        if command == "package":
            child.add_argument("--output", type=Path, default=DEFAULT_ZIP)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "verify":
            report = verification_report(args.data_dir)
        elif args.command == "build":
            report = build(args.output_dir, args.data_dir)
        else:
            report = package(args.output, args.data_dir)
    except ImportContractError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
