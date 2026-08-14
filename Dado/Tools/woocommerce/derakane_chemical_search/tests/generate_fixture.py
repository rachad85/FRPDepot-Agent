#!/usr/bin/env python3
"""Generate the conspicuously synthetic closed-contract-v2 regression fixture."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATASET = ROOT / "tests" / "fixtures" / "source" / "legacy-v1-dataset.json"
OUTPUT_DIR = ROOT / "tests" / "fixtures" / "verified"
RESINS = ("411", "441", "451", "455", "470", "510A/B/C", "510N", "515", "8084")
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
    "publisher": "SYNTHETIC FIXTURE PUBLISHER",
    "title": "Synthetic Chemical Resistance Guide Fixture",
    "product_family": "Synthetic Derakane/Alta fixture data only",
    "edition": "SYNTHETIC TEST EDITION — NOT FOR PUBLICATION",
    "document_code": "FIXTURE-ONLY-002",
    "publication_date": "2099-01-01",
    "document_sha256": "a" * 64,
    "page_count": 99,
    "source_url": None,
}
PROVENANCE = {
    "output_manifest_sha256": hashlib.sha256(b"synthetic-fixture-output-manifest-v2").hexdigest(),
    "output_manifest_bytes": 1234,
    "source_pdf_sha256": SOURCE["document_sha256"],
    "artifacts": [
        {
            "path": path,
            "bytes": 100 + index,
            "sha256": hashlib.sha256(("synthetic-fixture:" + path).encode()).hexdigest(),
        }
        for index, path in enumerate(REQUIRED_REBUILD_ARTIFACTS, start=1)
    ],
}
CAS_RE = re.compile(r"^[0-9]{2,7}-[0-9]{2}-[0-9]$")


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def key(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def first_occurrence_unique(values: list[int]) -> list[int]:
    return list(dict.fromkeys(values))


def component(unit: str, value: int, limited: bool = False, prefix: str | None = None) -> dict[str, Any]:
    return {"unit": unit, "value": value, "limited_service": limited, "limited_service_prefix_raw": prefix}


def pair(raw: str, celsius: int, fahrenheit: int, *, limited_c: bool = False, limited_f: bool = False) -> dict[str, Any]:
    return {
        "kind": "temperature_pair",
        "source_raw": raw,
        "components": [
            component("C", celsius, limited_c, "LS" if limited_c else None),
            component("F", fahrenheit, limited_f, "LS" if limited_f else None),
        ],
        "temperature_pair_order": "C/F",
    }


def rating_base(resin: str, sequence: int, raw: str, footnotes: list[int]) -> dict[str, Any]:
    return {
        "alternative_separators_raw": [],
        "alternatives": [],
        "footnote_refs_raw": footnotes,
        "footnote_refs": first_occurrence_unique(footnotes),
        "rating_model_version": 2,
        "resin": resin,
        "resin_sequence": sequence,
        "resolution_status": "RESOLVED",
        "source_geometry": {"bbox": [float(sequence), 1.0, float(sequence) + 0.5, 2.0]},
        "source_raw": raw,
        "state": "blank",
        "syntax": "blank",
        "temperature_c": None,
        "temperature_f": None,
        "temperature_pair_order": None,
        "value_text": raw,
    }


def ordinary_rating(old: dict[str, Any], resin: str, sequence: int, raw: str, cell_footnotes: list[int]) -> dict[str, Any]:
    footnotes = [*cell_footnotes, *old["footnote_ids"]]
    source_raw = raw + (f" <{','.join(map(str, footnotes))}>" if footnotes else "")
    result = rating_base(resin, sequence, source_raw, footnotes)
    state = old["state"]
    result["state"] = state
    result["syntax"] = {"blank": "blank", "nr": "not_recommended", "ls": "limited_service", "value": "temperature_pair"}[state]
    if state == "value":
        celsius = old["temperature_c"]
        fahrenheit = old["temperature_f"]
        result.update({
            "temperature_c": celsius,
            "temperature_f": fahrenheit,
            "temperature_pair_order": "C/F",
            "alternatives": [pair(raw, celsius, fahrenheit)],
        })
    return result


def source_special(form: str, resin: str, sequence: int) -> dict[str, Any]:
    if form == "limited_service_temperature_pair":
        result = rating_base(resin, sequence, "LS80/180", [])
        result.update({
            "state": "source_special", "resolution_status": "SOURCE_SPECIAL", "syntax": "temperature_pair",
            "source_special_form": form, "temperature_c": 80, "temperature_f": 180,
            "temperature_pair_order": "C/F", "alternatives": [pair("LS80/180", 80, 180, limited_c=True)],
            "value_text": "LS80/180",
        })
        return result
    if form == "semicolon_separated_temperature_alternatives":
        result = rating_base(resin, sequence, "40/100;\nLS 50/120", [])
        result.update({
            "state": "source_special", "resolution_status": "SOURCE_SPECIAL", "syntax": "multiple_alternatives",
            "source_special_form": form, "alternative_separators_raw": [";\n"],
            "alternatives": [pair("40/100", 40, 100), pair("LS 50/120", 50, 120, limited_c=True)],
            "value_text": "40/100;\nLS 50/120",
        })
        return result
    if form == "printed_single_temperature_without_unit":
        result = rating_base(resin, sequence, "75", [])
        result.update({
            "state": "source_special", "resolution_status": "SOURCE_SPECIAL", "syntax": "single_temperature",
            "source_special_form": form,
            "alternatives": [{"kind": "single_temperature", "source_raw": "75", "value": 75, "unit": None, "unit_status": "NOT_PRINTED"}],
            "value_text": "75",
        })
        return result
    if form == "printed_dash":
        result = rating_base(resin, sequence, "-", [])
        result.update({
            "state": "source_special", "resolution_status": "SOURCE_SPECIAL", "syntax": "printed_dash",
            "source_special_form": form, "alternatives": [{"kind": "printed_dash", "source_raw": "-"}], "value_text": "-",
        })
        return result
    result = rating_base(resin, sequence, "510A/B:\n65/150 510C:\nNR <3,4,3>", [3, 4, 3])
    first = pair("65/150", 65, 150)
    nr = rating_base(resin, sequence, "NR", [4])
    nr.update({"state": "nr", "syntax": "not_recommended", "value_text": "NR"})
    result.update({
        "state": "source_special", "resolution_status": "SOURCE_SPECIAL", "syntax": "resin_variant_values",
        "source_special_form": "510_variant_specific_values", "value_text": result["source_raw"],
        "resin_variants": {
            "A": first, "B": first, "C": {"state": "not_printed", "source_raw": None},
            "assignments": [
                {"label_raw": "510A/B:", "rating": first, "variants": ["A", "B"]},
                {"label_raw": "510C:", "rating": {"kind": "printed_dash", "source_raw": "-"}, "variants": ["C"]},
            ],
            "source_notation": "510A/B and 510C have distinct printed values",
            "source_notation_raw": result["source_raw"],
            "unassigned_variants": [],
        },
    })
    return result


def build() -> tuple[dict[str, Any], dict[str, Any]]:
    legacy = json.loads(SOURCE_DATASET.read_text(encoding="utf-8"))
    legacy["footnotes"] = legacy["footnotes"][:25]

    cas_catalog: list[dict[str, Any]] = []
    cas_id_by_number: dict[str, str] = {}
    for row in legacy["rows"]:
        for number in row["cas_numbers"]:
            if number in cas_id_by_number:
                continue
            entry_id = f"fixture-cas-{len(cas_catalog) + 1:04d}"
            cas_id_by_number[number] = entry_id
            cas_catalog.append({
                "cas_entry_id": entry_id,
                "source_sequence": len(cas_catalog) + 1,
                "cas_raw": number,
                "raw_pdf_cas": number,
                "normalized_cas": number,
                "public_searchable": True,
                "checksum_valid": True,
                "chemical_name_display": row["chemical_name"],
                "chemical_name_raw": row["chemical_name"],
                "chemical_search_key": key(row["chemical_name"]),
                "adjudication": {
                    "category": "a", "category_label": "synthetic exact fixture",
                    "mechanism": "synthetic_fixture", "rationale": "Synthetic fixture public CAS.", "status": "PROVEN",
                },
                "authority_provenance": [],
                "source": {
                    "column": 1, "line_bboxes": [[1.0, 1.0, 2.0, 2.0]], "page": row["source_page"],
                    "pdf_sha256": SOURCE["document_sha256"], "raw_lines": [f"{number} {row['chemical_name']}"],
                },
            })
    excluded_id = "fixture-cas-excluded"
    excluded_raw = "1330-96-4"
    cas_catalog.append({
        "cas_entry_id": excluded_id, "source_sequence": len(cas_catalog) + 1,
        "cas_raw": excluded_raw, "raw_pdf_cas": excluded_raw, "normalized_cas": None,
        "public_searchable": False, "checksum_valid": False,
        "chemical_name_display": "Malic Acid", "chemical_name_raw": "Malic Acid", "chemical_search_key": "malic acid",
        "adjudication": {
            "category": "c", "category_label": "ambiguous/unproven and excluded from normalized CAS search",
            "mechanism": "ambiguous_or_unproven_excluded",
            "rationale": "Synthetic broad-form fixture exclusion; raw token is internal provenance only.", "status": "EXCLUDED",
        },
        "authority_provenance": [],
        "source": {
            "column": 1, "line_bboxes": [[1.0, 1.0, 2.0, 2.0]], "page": 43,
            "pdf_sha256": SOURCE["document_sha256"], "raw_lines": [f"{excluded_raw} Malic Acid"],
        },
    })

    entities: list[dict[str, Any]] = []
    rows_by_chemical: dict[str, list[dict[str, Any]]] = {}
    for row in legacy["rows"]:
        rows_by_chemical.setdefault(row["chemical_id"], []).append(row)
    for source_order, group in enumerate(rows_by_chemical.values(), start=1):
        first = group[0]
        aliases = [{"display": value, "search_key": key(value), "basis": "synthetic_fixture_alias"} for value in first["aliases"]]
        cas_ids = [cas_id_by_number[value] for value in first["cas_numbers"]]
        if first["chemical_id"] == "malic":
            cas_ids.append(excluded_id)
        entities.append({
            "entity_type": "table_chemical", "entity_id": first["chemical_id"], "source_order": source_order,
            "display_name": first["chemical_name"], "name_key": key(first["chemical_name"]), "aliases": aliases,
            "cas_entry_ids": cas_ids, "record_ids": [row["row_id"] for row in group],
            "concentrations_in_source_order": [
                {
                    "display": row["concentration"]["display"], "record_id": row["row_id"],
                    "source_page": row["source_page"], "source_raw": row["concentration"]["display"],
                    "source_sequence": row["source_sequence"],
                }
                for row in group
            ],
        })

    entity_by_id = {entity["entity_id"]: entity for entity in entities}
    special_slots = {
        ("acetic", "510A/B/C"): "limited_service_temperature_pair",
        ("acetone", "470"): "semicolon_separated_temperature_alternatives",
        ("malic", "411"): "printed_single_temperature_without_unit",
        ("mixture", "515"): "printed_dash",
        ("hypochlorite", "510A/B/C"): "510_variant_specific_values",
    }
    used_slots: set[tuple[str, str]] = set()
    rows = []
    for old in legacy["rows"]:
        entity = entity_by_id[old["chemical_id"]]
        cells = []
        for sequence, old_cell in enumerate(old["cells"], start=1):
            resin = old_cell["resin_id"]
            slot = (old["chemical_id"], resin)
            if slot in special_slots and slot not in used_slots:
                rating = source_special(special_slots[slot], resin, sequence)
                used_slots.add(slot)
            else:
                rating = ordinary_rating(old_cell["ratings"][0], resin, sequence, old_cell["raw"], old_cell["footnote_ids"])
            cells.append({
                "resin_id": resin, "raw": rating["source_raw"],
                "footnote_ids_raw": list(rating["footnote_refs_raw"]),
                "footnote_ids": list(rating["footnote_refs"]), "ratings": [rating],
            })
        aliases = entity["aliases"]
        public_cas = [number for number in old["cas_numbers"]]
        chemical_refs_raw = [x for x in old["row_footnote_ids"] if x <= 25]
        if old["row_id"] == "fixture-p14-s10":
            chemical_refs_raw.append(chemical_refs_raw[0])
        source_suffix = f" <{','.join(map(str, chemical_refs_raw))}>" if chemical_refs_raw else ""
        chemical_source = {
            "display": old["chemical_name"], "footnote_refs_raw": chemical_refs_raw,
            "footnote_refs": first_occurrence_unique(chemical_refs_raw), "search_key": key(old["chemical_name"]),
            "source_raw": old["chemical_name"] + source_suffix,
        }
        concentration = {
            "column_unit_raw": "%", "display": old["concentration"]["display"],
            "source_raw": old["concentration"]["display"], "unit_semantics": "weight-% unless otherwise stated",
        }
        raw_cells = [chemical_source["source_raw"], concentration["source_raw"], *[cell["raw"] for cell in cells]]
        row_refs_raw = sorted(
            chemical_refs_raw + [ref for cell in cells for ref in cell["footnote_ids_raw"]]
        )
        rows.append({
            "row_id": old["row_id"], "source_sequence": old["source_sequence"], "source_page": old["source_page"],
            "chemical_sequence": entity["source_order"], "chemical_id": old["chemical_id"],
            "chemical_name": old["chemical_name"], "chemical_source": chemical_source,
            "aliases": aliases, "cas_entry_ids": entity["cas_entry_ids"], "public_cas_numbers": public_cas,
            "concentration": concentration, "row_footnote_ids_raw": row_refs_raw,
            "row_footnote_ids": first_occurrence_unique(row_refs_raw),
            "cells": cells,
            "source": {
                "cell_bboxes": [[float(i), 1.0, float(i) + 0.5, 2.0] for i in range(11)],
                "page": old["source_page"], "page_row_index": old["source_sequence"],
                "pdf_sha256": SOURCE["document_sha256"], "raw_cells": raw_cells,
                "row_bbox": [0.0, 1.0, 11.0, 2.0], "table_index": 1,
            },
            "qa_status": "VERIFIED",
        })

    footnotes = [
        {
            "id": item["id"], "text": item["text"], "source_page": item["source_page"],
            "source_parts": [{"bbox": [1.0, float(item["id"]), 2.0, float(item["id"]) + 0.5], "raw": item["text"]}],
        }
        for item in legacy["footnotes"]
    ]
    semantics = {
        "blank": {"source_quote": "Synthetic fixture blank definition.", "state": "blank"},
        "concentration": {"column_header_raw": "%", "legend": "weight-% unless otherwise stated"},
        "ls": {"source_quote": "Synthetic fixture limited-service definition.", "state": "ls"},
        "model_states": ["blank", "nr", "ls", "value", "source_special", "parser_uncertainty"],
        "nr": {"source_quote": "Synthetic fixture not-recommended definition.", "state": "nr"},
        "parser_uncertainty": {
            "meaning": "Synthetic fixture parser-uncertainty definition.",
            "resolution_status": "PARSER_UNCERTAINTY", "state": "parser_uncertainty",
        },
        "rating_model_version": 2,
        "resolution_statuses": ["RESOLVED", "SOURCE_SPECIAL", "PARSER_UNCERTAINTY"],
        "source_page": 6,
        "source_special": {
            "forms": [
                "limited_service_temperature_pair", "semicolon_separated_temperature_alternatives",
                "printed_single_temperature_without_unit", "printed_dash", "510_variant_specific_values",
            ],
            "state": "source_special", "resolution_status": "SOURCE_SPECIAL",
            "meaning": "Synthetic fixture closed source-special forms.",
        },
        "temperature_pair": {
            "column_header_raw": "°C/°F", "meaning": "Celsius first and Fahrenheit second.",
            "not_necessarily_maximum_service_temperature": True,
            "page_text_sha256": hashlib.sha256(b"synthetic fixture semantics").hexdigest(),
            "source_quote_parts": ["Synthetic fixture source quote."],
        },
    }
    dataset = {
        "contract": "frpdepot.derakane-search.dataset", "contract_version": 2,
        "source": SOURCE, "rebuild_provenance": PROVENANCE,
        "resin_columns": legacy["resin_columns"], "footnotes": footnotes, "semantics": semantics,
        "cas_catalog": cas_catalog, "search_entities": entities, "rows": rows,
    }
    dataset_payload = canonical_json(dataset)
    summary = {
        "row_count": len(rows), "chemical_count": len(entities), "search_entity_count": len(entities),
        "footnote_count": len(footnotes), "resin_columns": list(RESINS),
        "rating_count": sum(len(cell["ratings"]) for row in rows for cell in row["cells"]),
        "source_special_count": sum(rating["state"] == "source_special" for row in rows for cell in row["cells"] for rating in cell["ratings"]),
        "parser_uncertainty_count": 0, "cas_entry_count": len(cas_catalog),
        "cas_public_searchable_count": sum(entry["public_searchable"] for entry in cas_catalog),
        "cas_excluded_count": sum(not entry["public_searchable"] for entry in cas_catalog),
    }
    manifest = {
        "contract": "frpdepot.derakane-search.import-manifest", "contract_version": 2,
        "generated_utc": "2099-01-01T00:00:00Z",
        "producer": {"pipeline": "frpdepot.derakane-guide-rebuild", "contract_version": 2, "run_id": "SYNTHETIC-FIXTURE-RUN-V2"},
        "verification": {
            "status": "VERIFIED", "unresolved_count": 0, "reviewed_row_count": len(rows),
            "method": "Synthetic fixture contract-v2 exercise only", "verified_utc": "2099-01-01T00:01:00Z", "verifier": "FIXTURE",
        },
        "source": SOURCE, "rebuild_provenance": PROVENANCE,
        "dataset": {"file": "derakane-dataset.json", "bytes": len(dataset_payload), "sha256": hashlib.sha256(dataset_payload).hexdigest()},
        "summary": summary,
    }
    return dataset, manifest


def main() -> int:
    dataset, manifest = build()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "derakane-dataset.json").write_bytes(canonical_json(dataset))
    (OUTPUT_DIR / "import-manifest.json").write_bytes(canonical_json(manifest))
    print(json.dumps({
        "status": "GENERATED", "dataset_sha256": manifest["dataset"]["sha256"],
        "dataset_bytes": manifest["dataset"]["bytes"], **manifest["summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
