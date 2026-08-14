#!/usr/bin/env python3
"""Deterministically adapt the verified PDF rebuild to search dataset contract v2.

This module performs only local reads from the rebuild directory and local writes
to an explicit import directory. It verifies every rebuild-manifest pin before
constructing a lossless, manifest-pinned production import.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from import_contract import DATASET_NAME, MANIFEST_NAME, RESIN_IDS, verify_import

REQUIRED_REBUILD_ARTIFACTS = (
    "data/dataset_metadata.json",
    "data/records.jsonl",
    "data/cas_crosswalk.json",
    "data/footnotes.json",
    "data/semantics.json",
    "data/chemical_groups.json",
    "data/search_index.json",
    "validation/cas_adjudication/final_adjudication_report.json",
)
SOURCE = {
    "publisher": "INEOS Composites",
    "title": "Resin Selection Guide for Chemical Resistance",
    "product_family": "Derakane™ epoxy vinyl ester resins",
    "edition": "October 2019",
    "document_code": "INEOS-DERAKANE-CORROSION-GUIDE-OCT-2019",
    "publication_date": "2019-10",
    "document_sha256": "1cbd9522b49d5ac10d01f268b78e4d062b06e61c7e4011da69c989abba32c241",
    "page_count": 68,
    "source_url": None,
}
COLUMN_LABELS = {
    "411": "Derakane™ 411 series",
    "441": "Derakane™ 441 series",
    "451": "Derakane™ 451 series",
    "455": "Derakane™ 455 series",
    "470": "Derakane™ 470 series",
    "510A/B/C": "Derakane™ 510A/B/C series",
    "510N": "Derakane™ 510N series",
    "515": "Derakane™ 515 series",
    "8084": "Derakane™ 8084 series",
}


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def first_occurrence_unique(values: list[int]) -> list[int]:
    """Project source refs for display without changing the raw audit sequence."""
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def verify_rebuild(rebuild_dir: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest_path = rebuild_dir / "output_manifest.json"
    manifest = _read_json(manifest_path)
    pins = {item["path"]: item for item in manifest["artifacts"]}
    missing = [path for path in REQUIRED_REBUILD_ARTIFACTS if path not in pins]
    if missing:
        raise ValueError("rebuild output manifest does not pin: " + ", ".join(missing))
    failures: list[str] = []
    for relative, pin in pins.items():
        path = rebuild_dir / relative
        if not path.is_file():
            failures.append(f"missing {relative}")
        elif path.stat().st_size != pin["bytes"]:
            failures.append(f"byte mismatch {relative}")
        elif sha256_file(path) != pin["sha256"]:
            failures.append(f"SHA-256 mismatch {relative}")
    if failures:
        raise ValueError("rebuild verification failed: " + "; ".join(failures))
    if manifest["source_pdf_sha256"] != SOURCE["document_sha256"]:
        raise ValueError("unexpected source PDF SHA-256")
    return manifest, pins


def _rebuild_provenance(rebuild_dir: Path, manifest: dict[str, Any], pins: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "output_manifest_sha256": sha256_file(rebuild_dir / "output_manifest.json"),
        "output_manifest_bytes": (rebuild_dir / "output_manifest.json").stat().st_size,
        "source_pdf_sha256": manifest["source_pdf_sha256"],
        "artifacts": [
            {"path": relative, "bytes": pins[relative]["bytes"], "sha256": pins[relative]["sha256"]}
            for relative in REQUIRED_REBUILD_ARTIFACTS
        ],
    }


def build_dataset(rebuild_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest, pins = verify_rebuild(rebuild_dir)
    metadata = _read_json(rebuild_dir / "data/dataset_metadata.json")
    footnotes_source = _read_json(rebuild_dir / "data/footnotes.json")
    semantics = _read_json(rebuild_dir / "data/semantics.json")
    cas_source = _read_json(rebuild_dir / "data/cas_crosswalk.json")
    groups_source = _read_json(rebuild_dir / "data/chemical_groups.json")
    index_source = _read_json(rebuild_dir / "data/search_index.json")
    records = [json.loads(line) for line in (rebuild_dir / "data/records.jsonl").read_text(encoding="utf-8").splitlines()]

    if metadata["technical_rollout_gate"] != "READY_FOR_LOCAL_IMPORT_CONTRACT":
        raise ValueError("rebuild is not ready for the local import contract")
    if metadata["parser_uncertainty_count"] != 0 or metadata["cas_unadjudicated_target_count"] != 0:
        raise ValueError("rebuild contains unresolved parser/CAS work")

    cas_catalog = []
    cas_by_id: dict[str, dict[str, Any]] = {}
    for source in cas_source["entries"]:
        entry = {
            "cas_entry_id": source["cas_entry_id"],
            "source_sequence": source["source_sequence"],
            "cas_raw": source["cas_raw"],
            "raw_pdf_cas": source["raw_pdf_cas"],
            "normalized_cas": source["normalized_cas"],
            "public_searchable": source["public_searchable"],
            "checksum_valid": source["cas_checksum_valid"],
            "chemical_name_display": source["chemical_name_display"],
            "chemical_name_raw": source["chemical_name_raw"],
            "chemical_search_key": source["chemical_search_key"],
            "adjudication": source["adjudication"],
            "authority_provenance": source["authority_provenance"],
            "source": source["source"],
        }
        cas_catalog.append(entry)
        cas_by_id[entry["cas_entry_id"]] = entry

    entity_by_record: dict[str, dict[str, Any]] = {}
    search_entities = []
    for source in index_source["entries"]:
        cas_entry_ids = [entry["cas_entry_id"] for entry in source["cas"]]
        entity = {
            "entity_type": source["entity_type"],
            "entity_id": source["entity_id"],
            "source_order": source["source_order"],
            "display_name": source["display_name"],
            "name_key": source["name_key"],
            "aliases": source["aliases"],
            "cas_entry_ids": cas_entry_ids,
            "record_ids": source["record_ids"],
            "concentrations_in_source_order": source["concentrations_in_source_order"],
        }
        search_entities.append(entity)
        for record_id in entity["record_ids"]:
            if record_id in entity_by_record:
                raise ValueError(f"record belongs to multiple search entities: {record_id}")
            entity_by_record[record_id] = entity

    rows = []
    for record in records:
        entity = entity_by_record[record["record_id"]]
        linked_cas = [cas_by_id[entry_id] for entry_id in entity["cas_entry_ids"]]
        public_cas = []
        for entry in linked_cas:
            if entry["public_searchable"] and entry["normalized_cas"] not in public_cas:
                public_cas.append(entry["normalized_cas"])
        chemical_source = dict(record["chemical"])
        chemical_refs_raw = list(chemical_source["footnote_refs"])
        chemical_source["footnote_refs_raw"] = chemical_refs_raw
        chemical_source["footnote_refs"] = first_occurrence_unique(chemical_refs_raw)
        row_refs_raw = list(record["all_footnote_refs"])
        cells = []
        for source_rating in record["ratings"]:
            rating = dict(source_rating)
            rating_refs_raw = list(rating["footnote_refs"])
            rating["footnote_refs_raw"] = rating_refs_raw
            rating["footnote_refs"] = first_occurrence_unique(rating_refs_raw)
            cells.append({
                "resin_id": rating["resin"],
                "raw": rating["source_raw"],
                "footnote_ids_raw": list(rating_refs_raw),
                "footnote_ids": list(rating["footnote_refs"]),
                "ratings": [rating],
            })
        rows.append({
            "row_id": record["record_id"],
            "source_sequence": record["source_sequence"],
            "source_page": record["source"]["page"],
            "chemical_sequence": record["chemical_sequence"],
            "chemical_id": entity["entity_id"],
            "chemical_name": entity["display_name"],
            "chemical_source": chemical_source,
            "aliases": entity["aliases"],
            "cas_entry_ids": entity["cas_entry_ids"],
            "public_cas_numbers": public_cas,
            "concentration": record["concentration"],
            "row_footnote_ids_raw": row_refs_raw,
            "row_footnote_ids": first_occurrence_unique(row_refs_raw),
            "cells": cells,
            "source": record["source"],
            "qa_status": "VERIFIED",
        })

    footnotes = [
        {
            "id": item["number"],
            "text": item["definition"],
            "source_page": footnotes_source["source_page"],
            "source_parts": item["source_parts"],
        }
        for item in footnotes_source["definitions"]
    ]
    provenance = _rebuild_provenance(rebuild_dir, manifest, pins)
    dataset = {
        "contract": "frpdepot.derakane-search.dataset",
        "contract_version": 2,
        "source": SOURCE,
        "rebuild_provenance": provenance,
        "resin_columns": [
            {"id": resin_id, "label": COLUMN_LABELS[resin_id], "source_sequence": index}
            for index, resin_id in enumerate(RESIN_IDS, start=1)
        ],
        "footnotes": footnotes,
        "semantics": semantics,
        "cas_catalog": cas_catalog,
        "search_entities": search_entities,
        "rows": rows,
    }
    facts = {
        "rows": len(rows),
        "chemicals": len(groups_source["groups"]),
        "search_entities": len(search_entities),
        "footnotes": len(footnotes),
        "ratings": sum(len(row["cells"]) for row in rows),
        "source_special": sum(
            rating["state"] == "source_special"
            for row in rows for cell in row["cells"] for rating in cell["ratings"]
        ),
        "parser_uncertainty": sum(
            rating["state"] == "parser_uncertainty"
            for row in rows for cell in row["cells"] for rating in cell["ratings"]
        ),
        "cas_entries": len(cas_catalog),
        "cas_public_searchable": sum(entry["public_searchable"] for entry in cas_catalog),
        "cas_excluded": sum(not entry["public_searchable"] for entry in cas_catalog),
    }
    expected = {
        "rows": 1285, "chemicals": 1116, "search_entities": 1345, "footnotes": 25,
        "ratings": 11565, "source_special": 17, "parser_uncertainty": 0,
        "cas_entries": 716, "cas_public_searchable": 709, "cas_excluded": 7,
    }
    if facts != expected:
        raise ValueError(f"verified rebuild count mismatch: expected {expected}, got {facts}")
    return dataset, facts


def write_import(rebuild_dir: Path, data_dir: Path) -> dict[str, Any]:
    dataset, facts = build_dataset(rebuild_dir)
    dataset_payload = canonical_json(dataset)
    provenance = dataset["rebuild_provenance"]
    output_manifest = _read_json(rebuild_dir / "output_manifest.json")
    manifest = {
        "contract": "frpdepot.derakane-search.import-manifest",
        "contract_version": 2,
        "generated_utc": output_manifest["generated_utc"],
        "producer": {
            "pipeline": "frpdepot.derakane-guide-rebuild",
            "contract_version": 2,
            "run_id": f"output-manifest:{provenance['output_manifest_sha256']}",
        },
        "verification": {
            "status": "VERIFIED",
            "unresolved_count": 0,
            "reviewed_row_count": facts["rows"],
            "method": "PDF geometry rebuild, closed rating model, five-batch CAS adjudication, manifest verification, and deterministic contract-v2 adapter",
            "verified_utc": output_manifest["generated_utc"],
            "verifier": "FRP Depot local Derakane rebuild pipeline",
        },
        "source": SOURCE,
        "rebuild_provenance": provenance,
        "dataset": {
            "file": DATASET_NAME,
            "bytes": len(dataset_payload),
            "sha256": sha256_bytes(dataset_payload),
        },
        "summary": {
            "row_count": facts["rows"],
            "chemical_count": facts["chemicals"],
            "search_entity_count": facts["search_entities"],
            "footnote_count": facts["footnotes"],
            "resin_columns": list(RESIN_IDS),
            "rating_count": facts["ratings"],
            "source_special_count": facts["source_special"],
            "parser_uncertainty_count": facts["parser_uncertainty"],
            "cas_entry_count": facts["cas_entries"],
            "cas_public_searchable_count": facts["cas_public_searchable"],
            "cas_excluded_count": facts["cas_excluded"],
        },
    }
    manifest_payload = canonical_json(manifest)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / DATASET_NAME).write_bytes(dataset_payload)
    (data_dir / MANIFEST_NAME).write_bytes(manifest_payload)
    verified = verify_import(data_dir)
    return {
        "status": "IMPORTED",
        "dataset": str(verified.dataset_path),
        "dataset_bytes": verified.byte_count,
        "dataset_sha256": verified.sha256,
        "manifest": str(verified.manifest_path),
        "manifest_sha256": sha256_bytes(manifest_payload),
        **facts,
    }
